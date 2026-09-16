using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Companion.Audio.Audio;

namespace PagentOS.SessionCompanion;

/// <summary>
/// Executes <c>desktop.alarm_start</c> and <c>desktop.alarm_stop</c> (DEVICE_PROTOCOL.md §6c,
/// M18). An interactive-session capability by construction: it opens a render endpoint in the
/// owner's session, which is something the Session-0 Device Service cannot do and must never
/// try (CLAUDE.md Windows Agent rule).
///
/// <para>What it does NOT do is as load-bearing as what it does:</para>
/// <list type="bullet">
/// <item><b>It never touches the system volume.</b> The ramp scales the samples this process
/// generates (<see cref="WakeTone"/>) on its own shared-mode render stream. The machine's
/// mixer is exactly where the owner left it, during the alarm and after it.</item>
/// <item><b>It cannot produce a sudden full volume.</b> The first sample is generated at
/// <see cref="WakeRamp.Start"/>, which <see cref="WakeRamp.Create"/> refuses to accept at or
/// above <see cref="WakeRamp.MaxStartVolume"/>, and no later sample exceeds
/// <see cref="WakeRamp.MaxVolume"/> — the generator clamps to that ceiling independently of
/// the ramp, so the two would have to fail together.</item>
/// <item><b>It always stops.</b> Three ways: the owner's <c>desktop.alarm_stop</c>, the
/// alarm's own <c>max_duration_s</c> (checked on every pump, so an alarm nobody stops is
/// bounded), and companion shutdown. There is no path where a ringing alarm outlives this
/// process.</item>
/// </list>
///
/// <para>One alarm rings at a time. Starting a second stops the first and says so in the
/// result (<c>replaced_alarm_id</c>) rather than layering two ramps on one endpoint.</para>
///
/// <para><b>Testing.</b> The audio boundary is <see cref="IAudioDeviceFactory"/>, so a test
/// drives a real ramp through <c>FakePlayback</c> and asserts the bytes; no test plays a
/// sound. With <c>autoPump: false</c> the caller drives <see cref="Pump"/> against a
/// <c>ManualTimeProvider</c>, which makes the ramp's shape an exact assertion rather than a
/// wall-clock race.</para>
/// </summary>
public sealed class AlarmController : IDisposable
{
    /// <summary>How long an unattended alarm rings before stopping itself, when the caller says nothing.</summary>
    public const int DefaultMaxDurationSeconds = 300;

    /// <summary>
    /// What the tone drops to while something else speaks (requirement 269). The same 0.15
    /// the cloud already uses to duck the owner's music, so a greeting over the tone and a
    /// greeting over a song sound like the same decision rather than two.
    /// </summary>
    public const double DuckLevel = 0.15;

    public const int MinMaxDurationSeconds = 10;

    /// <summary>Half an hour. Past this, "the alarm is still ringing" is a fault, not a wake-up.</summary>
    public const int MaxMaxDurationSeconds = 1800;

    private readonly IAudioDeviceFactory _devices;
    private readonly Func<string?> _resolveRenderDeviceId;
    private readonly ILogger _logger;
    private readonly TimeProvider _time;
    private readonly AuditLog? _audit;
    private readonly bool _autoPump;
    private readonly int _chunkMs;
    private readonly object _sync = new();

    /// <summary>How many things are currently speaking over the tone (req 269).</summary>
    private int _duckDepth;

    private Ringing? _current;
    private CancellationTokenSource? _pumpCts;
    private Task? _pumpTask;
    private bool _disposed;

    public AlarmController(
        IAudioDeviceFactory devices,
        Func<string?> resolveRenderDeviceId,
        ILogger logger,
        TimeProvider? time = null,
        AuditLog? audit = null,
        bool autoPump = true,
        int chunkMs = 200)
    {
        _devices = devices;
        _resolveRenderDeviceId = resolveRenderDeviceId;
        _logger = logger;
        _time = time ?? TimeProvider.System;
        _audit = audit;
        _autoPump = autoPump;
        _chunkMs = Math.Max(20, chunkMs);
    }

    /// <summary>The alarm currently ringing, or null. Read by tests and by the shutdown path.</summary>
    public string? RingingAlarmId
    {
        get
        {
            lock (_sync)
            {
                return _current?.AlarmId;
            }
        }
    }

    public bool IsRinging => RingingAlarmId is not null;

    /// <summary>
    /// B47: what the ringing alarm was started WITH (id, label, ramp, duration, test flag and the
    /// snooze terms the cloud sent), detached. The offline "ertele" needs it to re-arm the same
    /// alarm locally without the cloud; null when nothing rings.
    /// </summary>
    public JsonObject? RingingPayload
    {
        get
        {
            lock (_sync)
            {
                return _current is null ? null : (JsonObject)_current.Payload.DeepClone();
            }
        }
    }

    /// <summary>
    /// B13 requirement 269: the alarm tone steps back while something else speaks.
    /// </summary>
    /// <remarks>
    /// <para>The cloud has ducked the owner's MUSIC since M18.3 — one `browser.media_volume`
    /// at 0.15 while the greeting plays, and back afterwards. It could never duck this,
    /// because this tone is generated inside the companion and has no per-stream volume the
    /// cloud can address. So on the tone-fallback path (which is every alarm without a wake
    /// song, and every alarm whose media failed) the greeting simply played OVER a ringing
    /// alarm at full level, and the sentence the owner was supposed to hear was the one
    /// thing in the room they could not.</para>
    /// <para>Ducked HERE rather than by a new capability, because the two sounds are made by
    /// one process: the level is applied per chunk in <c>Pump</c>, so it takes effect on the
    /// next chunk and needs no restart, no second stream and no round trip.</para>
    /// </remarks>
    public bool Ducked
    {
        get
        {
            lock (_sync)
            {
                return _duckDepth > 0;
            }
        }
    }

    /// <summary>
    /// Lowers the tone until the returned handle is disposed. Nested and reference-counted:
    /// two overlapping greetings must not have the first one's end restore full volume under
    /// the second.
    /// </summary>
    public IDisposable Duck(string reason)
    {
        lock (_sync)
        {
            _duckDepth++;
            if (_duckDepth == 1)
            {
                _logger.LogInformation("alarm tone ducked to {Level} ({Reason})", DuckLevel, reason);
            }
        }

        return new DuckHandle(this);
    }

    private void Unduck()
    {
        lock (_sync)
        {
            if (_duckDepth > 0)
            {
                _duckDepth--;
            }
        }
    }

    private sealed class DuckHandle(AlarmController owner) : IDisposable
    {
        private int _disposed;

        public void Dispose()
        {
            // Once, whatever the caller does: a double dispose that decremented twice would
            // leave a still-speaking greeting fighting a tone back at full volume.
            if (Interlocked.Exchange(ref _disposed, 1) == 0)
            {
                owner.Unduck();
            }
        }
    }

    /// <summary>
    /// Payload: <c>{"alarm_id": "&lt;id&gt;"?, "label": "&lt;text&gt;"?, "wake_volume": {"start": f,
    /// "end": f, "ramp_seconds": i}?, "max_duration_s": i?}</c>.
    /// Result: <c>{"started": true, "alarm_id": "...", "start_volume": f, "end_volume": f,
    /// "ramp_seconds": i, "max_duration_s": i, "end_volume_clamped": bool,
    /// "ramp_seconds_clamped": bool, "replaced_alarm_id": "..."|null}</c>.
    /// </summary>
    public JsonObject Start(JsonObject payload)
    {
        ArgumentNullException.ThrowIfNull(payload);

        var alarmId = payload["alarm_id"]?.GetValue<string>();
        if (string.IsNullOrWhiteSpace(alarmId))
        {
            alarmId = Guid.NewGuid().ToString();
        }

        var label = payload["label"]?.GetValue<string>();
        var ramp = ParseRamp(payload["wake_volume"]);
        var maxDuration = ParseMaxDuration(payload["max_duration_s"]);

        var deviceId = _resolveRenderDeviceId();
        if (string.IsNullOrWhiteSpace(deviceId))
        {
            // Retryable: a headset that is not plugged in yet is a real, transient reason a
            // wake-up could not sound, and the caller should be told that rather than
            // "this device cannot do alarms".
            throw new CapabilityException(
                ErrorClasses.DependencyUnavailable,
                "no audio render device is available in the owner's session, so no alarm can sound",
                retryable: true);
        }

        string? replaced;
        lock (_sync)
        {
            ObjectDisposedException.ThrowIf(_disposed, this);
            replaced = StopCurrentLocked("replaced");

            IAudioPlayback playback;
            try
            {
                playback = _devices.OpenPlayback(deviceId, WakeTone.Format);
                playback.Start();
            }
            catch (Exception ex)
            {
                throw new CapabilityException(
                    ErrorClasses.DependencyUnavailable,
                    $"could not open the render endpoint for the alarm: {ex.Message}",
                    retryable: true);
            }

            var snapshot = (JsonObject)payload.DeepClone();
            snapshot["alarm_id"] = alarmId;
            _current = new Ringing(alarmId, label, ramp, maxDuration, playback, _time.GetTimestamp(), snapshot);

            // Prime a couple of chunks so the first sound is immediate rather than one pump
            // interval late, then let the loop keep it fed.
            PumpLocked();
            PumpLocked();

            if (_autoPump)
            {
                StartPumpLocked();
            }
        }

        _logger.LogInformation(
            "alarm {AlarmId} started: ramp {Start:0.###}->{End:0.###} over {Ramp}s, max {MaxDuration}s, device={Device}",
            alarmId,
            ramp.Start,
            ramp.End,
            ramp.RampSeconds,
            maxDuration,
            deviceId);
        _audit?.Write(
            "alarm_start",
            capability: AgentCapabilities.DesktopAlarmStart,
            status: AckStatus.Succeeded,
            detail: $"alarm_id={alarmId}; start={ramp.Start:0.###}; end={ramp.End:0.###}; "
                + $"ramp_s={ramp.RampSeconds}; max_duration_s={maxDuration}; replaced={replaced ?? "-"}");

        return new JsonObject
        {
            ["started"] = true,
            ["alarm_id"] = alarmId,
            ["start_volume"] = ramp.Start,
            ["end_volume"] = ramp.End,
            ["ramp_seconds"] = ramp.RampSeconds,
            ["max_duration_s"] = maxDuration,
            ["end_volume_clamped"] = ramp.EndClamped,
            ["ramp_seconds_clamped"] = ramp.RampClamped,
            ["replaced_alarm_id"] = replaced,
        };
    }

    /// <summary>
    /// Payload: <c>{"alarm_id": "&lt;id&gt;"?}</c> — omit it to stop whatever is ringing.
    /// Result: <c>{"stopped": bool, "alarm_id": "..."|null, "was_ringing": bool}</c>.
    ///
    /// <para>Idempotent by design. Stopping an alarm that already stopped is a success with
    /// <c>was_ringing: false</c>, not an error: the owner said "stop", and it is stopped. A
    /// stop naming a DIFFERENT alarm than the one ringing does nothing and reports
    /// <c>stopped: false</c> — a stale retry must not silence the alarm that replaced it.</para>
    /// </summary>
    public JsonObject Stop(JsonObject payload)
    {
        ArgumentNullException.ThrowIfNull(payload);
        var requested = payload["alarm_id"]?.GetValue<string>();

        lock (_sync)
        {
            var current = _current;
            if (current is null)
            {
                return new JsonObject
                {
                    ["stopped"] = true,
                    ["alarm_id"] = requested,
                    ["was_ringing"] = false,
                };
            }

            if (!string.IsNullOrWhiteSpace(requested)
                && !string.Equals(requested, current.AlarmId, StringComparison.Ordinal))
            {
                _logger.LogInformation(
                    "alarm_stop named {Requested} but {Ringing} is ringing; leaving it alone",
                    requested,
                    current.AlarmId);
                return new JsonObject
                {
                    ["stopped"] = false,
                    ["alarm_id"] = requested,
                    ["was_ringing"] = false,
                };
            }

            var stopped = StopCurrentLocked("owner_stop");
            return new JsonObject
            {
                ["stopped"] = true,
                ["alarm_id"] = stopped,
                ["was_ringing"] = true,
            };
        }
    }

    /// <summary>
    /// Feeds one chunk of the chime at the ramp's current level, or stops the alarm when its
    /// <c>max_duration_s</c> has elapsed. Returns true while the alarm is still ringing.
    /// Public so a test can advance a manual clock and pump deterministically.
    /// </summary>
    public bool Pump()
    {
        lock (_sync)
        {
            return PumpLocked();
        }
    }

    /// <summary>Stops any ringing alarm. Called on companion shutdown; safe to call repeatedly.</summary>
    public void StopAll(string reason = "shutdown")
    {
        lock (_sync)
        {
            StopCurrentLocked(reason);
        }
    }

    public void Dispose()
    {
        Task? pump;
        lock (_sync)
        {
            if (_disposed)
            {
                return;
            }

            _disposed = true;
            StopCurrentLocked("disposed");
            pump = _pumpTask;
        }

        try
        {
            pump?.Wait(TimeSpan.FromSeconds(2));
        }
        catch (Exception)
        {
            // Teardown only: the pump logs its own outcome and the endpoint is already closed.
        }
    }

    // ---------------------------------------------------------------- internals

    private bool PumpLocked()
    {
        var current = _current;
        if (current is null)
        {
            return false;
        }

        var elapsed = _time.GetElapsedTime(current.StartedAt);
        if (elapsed.TotalSeconds >= current.MaxDurationSeconds)
        {
            _logger.LogInformation(
                "alarm {AlarmId} reached its {MaxDuration}s limit and stopped itself",
                current.AlarmId,
                current.MaxDurationSeconds);
            StopCurrentLocked("max_duration");
            return false;
        }

        var level = current.Ramp.LevelAt(elapsed);
        if (_duckDepth > 0)
        {
            // req 269. Applied per chunk, so ducking takes effect on the next one - no
            // restart, no second stream, and the ramp underneath keeps its own shape so the
            // tone comes back exactly where it would have been.
            level = Math.Min(level, DuckLevel);
        }

        var pcm = WakeTone.Render(current.Playback.Format, current.OffsetMs, _chunkMs, level);
        current.OffsetMs += _chunkMs;
        try
        {
            current.Playback.Enqueue(pcm);
        }
        catch (Exception ex)
        {
            // A render endpoint that vanished mid-alarm (the owner unplugged the headset) must
            // not leave a half-dead alarm object behind claiming to ring.
            _logger.LogWarning("alarm {AlarmId} lost its render endpoint: {Reason}", current.AlarmId, ex.Message);
            StopCurrentLocked("endpoint_lost");
            return false;
        }

        return true;
    }

    private string? StopCurrentLocked(string reason)
    {
        var current = _current;
        if (current is null)
        {
            return null;
        }

        _current = null;
        try
        {
            current.Playback.StopImmediately();
        }
        catch (Exception ex)
        {
            _logger.LogWarning("alarm {AlarmId} stop: {Reason}", current.AlarmId, ex.Message);
        }

        try
        {
            current.Playback.Dispose();
        }
        catch (Exception ex)
        {
            _logger.LogWarning("alarm {AlarmId} endpoint dispose: {Reason}", current.AlarmId, ex.Message);
        }

        _pumpCts?.Cancel();
        _pumpCts?.Dispose();
        _pumpCts = null;

        _logger.LogInformation("alarm {AlarmId} stopped ({Reason})", current.AlarmId, reason);
        _audit?.Write(
            "alarm_stop",
            capability: AgentCapabilities.DesktopAlarmStop,
            status: AckStatus.Succeeded,
            detail: $"alarm_id={current.AlarmId}; reason={reason}");
        return current.AlarmId;
    }

    private void StartPumpLocked()
    {
        var cts = new CancellationTokenSource();
        _pumpCts = cts;
        var token = cts.Token;
        _pumpTask = Task.Run(
            async () =>
            {
                try
                {
                    while (!token.IsCancellationRequested)
                    {
                        await Task.Delay(_chunkMs, token).ConfigureAwait(false);
                        if (!Pump())
                        {
                            return;
                        }
                    }
                }
                catch (OperationCanceledException)
                {
                    // Stopped, replaced or disposed: the stop path already closed the endpoint.
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "alarm pump stopped unexpectedly: {Reason}", ex.Message);
                    StopAll("pump_failed");
                }
            },
            CancellationToken.None);
    }

    private static WakeRamp ParseRamp(JsonNode? node)
    {
        if (node is null)
        {
            return WakeRamp.Create(null, null, null);
        }

        if (node is not JsonObject wake)
        {
            throw new CapabilityException(
                ErrorClasses.ValidationError,
                "payload.wake_volume must be an object with start/end/ramp_seconds",
                retryable: false);
        }

        return WakeRamp.Create(
            Number(wake["start"], "wake_volume.start"),
            Number(wake["end"], "wake_volume.end"),
            Integer(wake["ramp_seconds"], "wake_volume.ramp_seconds"));
    }

    private static int ParseMaxDuration(JsonNode? node)
    {
        var requested = Integer(node, "max_duration_s") ?? DefaultMaxDurationSeconds;
        return Math.Clamp(requested, MinMaxDurationSeconds, MaxMaxDurationSeconds);
    }

    private static double? Number(JsonNode? node, string field)
    {
        if (node is null)
        {
            return null;
        }

        if (node.GetValueKind() != JsonValueKind.Number
            || !double.TryParse(node.ToJsonString(), NumberStyles.Float, CultureInfo.InvariantCulture, out var value))
        {
            throw new CapabilityException(
                ErrorClasses.ValidationError,
                $"payload.{field} must be a number",
                retryable: false);
        }

        return value;
    }

    private static int? Integer(JsonNode? node, string field)
    {
        var value = Number(node, field);
        if (value is null)
        {
            return null;
        }

        if (value.Value != Math.Floor(value.Value) || value.Value < int.MinValue || value.Value > int.MaxValue)
        {
            throw new CapabilityException(
                ErrorClasses.ValidationError,
                $"payload.{field} must be a whole number of seconds",
                retryable: false);
        }

        return (int)value.Value;
    }

    private sealed class Ringing(
        string alarmId,
        string? label,
        WakeRamp ramp,
        int maxDurationSeconds,
        IAudioPlayback playback,
        long startedAt,
        JsonObject payload)
    {
        public JsonObject Payload { get; } = payload;

        public string AlarmId { get; } = alarmId;

        public string? Label { get; } = label;

        public WakeRamp Ramp { get; } = ramp;

        public int MaxDurationSeconds { get; } = maxDurationSeconds;

        public IAudioPlayback Playback { get; } = playback;

        public long StartedAt { get; } = startedAt;

        /// <summary>Milliseconds of chime already generated — keeps the waveform continuous across chunks.</summary>
        public long OffsetMs { get; set; }
    }
}
