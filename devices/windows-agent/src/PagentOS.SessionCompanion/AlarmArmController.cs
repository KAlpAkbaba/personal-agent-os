using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion;

/// <summary>
/// Executes <c>desktop.alarm_arm</c> and <c>desktop.alarm_disarm</c> (DEVICE_PROTOCOL.md §6f,
/// M18.3), and rings the local fallback when the cloud does not.
///
/// <para><b>The failure this exists for.</b> A wake alarm that only rings when the cloud can
/// reach the device is not a wake alarm; it is a wake alarm plus an availability requirement
/// the owner never agreed to. So when Cloud Core schedules a wake-up it also ARMS the device:
/// the device is told the moment and the ramp, writes them to disk, and rings on its own if
/// nothing has consumed the arm by <c>fire_at + grace_s</c>.</para>
///
/// <para><b>One ring per alarm id, always.</b> Three things consume an arm and each of them
/// removes it from the store before anything else happens: a cloud <c>desktop.alarm_start</c>
/// naming that id (the cloud got there first — the correct, ordinary case), a
/// <c>desktop.alarm_stop</c> naming it (the owner has already dealt with it), and
/// <c>desktop.alarm_disarm</c>. Firing locally removes it too, and the removal is persisted
/// before the alarm sounds, so a companion that crashes mid-ring does not ring again on
/// restart. There is no path that rings one id twice.</para>
///
/// <para><b>Reload is deliberately conservative.</b> On start, an arm whose local fire time has
/// already passed rings ONCE if it passed less than <see cref="StaleAfter"/> ago, and is
/// expired with an audit row otherwise. Waking someone twenty minutes late is a late alarm;
/// waking them at 14:00 for a 06:30 alarm is a machine behaving badly, and the owner would
/// rather read about it in the audit trail.</para>
///
/// <para><b>Testing.</b> Like <see cref="AlarmController"/>, the clock is a
/// <see cref="TimeProvider"/> and the timer is optional: with <c>autoTick: false</c> a test
/// advances a manual clock and calls <see cref="Tick"/>, so "it rings at fire_at + grace_s and
/// not before" is exact arithmetic rather than a wall-clock race.</para>
/// </summary>
public sealed class AlarmArmController : IDisposable
{
    /// <summary>Default wait after <c>fire_at</c> before this device rings on its own.</summary>
    public const int DefaultGraceSeconds = 60;

    public const int MinGraceSeconds = 0;

    public const int MaxGraceSeconds = 3600;

    /// <summary>Past this much lateness, an overdue arm is expired instead of rung.</summary>
    /// <remarks>
    /// <para>Five minutes, not the two hours this used to be. Measured on the owner's machine
    /// on 2026-09-10: a 07:30 alarm was armed the night before, the PC slept through the alarm
    /// time, and the companion started at 08:10 - whereupon it reloaded the overdue arm and
    /// rang it, 39 minutes and 39 seconds late. The log line it writes for an over-stale arm
    /// calls it "too late to be a wake-up", which was exactly right and exactly what a
    /// two-hour horizon failed to catch.</para>
    /// <para>An alarm is a request to be woken AT a time. A couple of minutes late still serves
    /// that - a slow resume, a busy boot, the cloud losing a race - so the horizon is not zero.
    /// Forty minutes late serves nothing: the owner is already awake, or was never going to be
    /// woken by this, and the only thing the noise does is startle them.</para>
    /// </remarks>
    public static readonly TimeSpan StaleAfter = TimeSpan.FromMinutes(5);

    private readonly ArmedAlarmStore _store;
    private readonly AlarmController? _alarm;
    private readonly ILogger _logger;
    private readonly TimeProvider _time;
    private readonly AuditLog? _audit;
    private readonly int _tickMs;
    private readonly object _sync = new();
    private readonly HashSet<string> _reportedRingFailures = new(StringComparer.Ordinal);

    private CancellationTokenSource? _tickCts;
    private Task? _tickTask;
    private bool _disposed;

    public AlarmArmController(
        ArmedAlarmStore store,
        AlarmController? alarm,
        ILogger logger,
        TimeProvider? time = null,
        AuditLog? audit = null,
        bool autoTick = true,
        int tickMs = 1000)
    {
        _store = store;
        _alarm = alarm;
        _logger = logger;
        _time = time ?? TimeProvider.System;
        _audit = audit;
        _tickMs = Math.Max(50, tickMs);
        AutoTick = autoTick;
    }

    public bool AutoTick { get; }

    /// <summary>How many arms are held right now.</summary>
    public int ArmedCount
    {
        get
        {
            lock (_sync)
            {
                return _store.Count;
            }
        }
    }

    /// <summary>When this device would next ring on its own, or null.</summary>
    public DateTimeOffset? NextFireLocalAt
    {
        get
        {
            lock (_sync)
            {
                return _store.NextFireLocalAt;
            }
        }
    }

    /// <summary>Local fallback rings since this process started. Telemetry; asserted by tests.</summary>
    public int LocalRings { get; private set; }

    /// <summary>Arms expired for being too far overdue to be a wake-up. Telemetry; asserted by tests.</summary>
    public int Expired { get; private set; }

    /// <summary>
    /// Payload: <c>{"alarm_id": "&lt;id&gt;", "fire_at": "&lt;iso-8601&gt;", "grace_s": &lt;int&gt;?,
    /// "label": "&lt;text&gt;"?, "wake_volume": {…}?, "max_duration_s": &lt;int&gt;?}</c>.
    /// Result: <c>{"armed": true, "alarm_id": "…", "fire_at": "…", "grace_s": &lt;i&gt;,
    /// "fire_local_at": "…", "replaced": &lt;bool&gt;, "armed_count": &lt;i&gt;}</c>.
    ///
    /// <para>Idempotent by <c>alarm_id</c>: arming the same id again REPLACES the arm (and says
    /// so) rather than adding a second one. A cloud that retries a schedule must not end up
    /// with two fallbacks for one wake-up.</para>
    /// </summary>
    public JsonObject Arm(JsonObject payload)
    {
        ArgumentNullException.ThrowIfNull(payload);

        var alarmId = payload["alarm_id"]?.GetValue<string>();
        if (string.IsNullOrWhiteSpace(alarmId))
        {
            // Unlike alarm_start, an arm without an id is meaningless: the id is what a later
            // alarm_start, disarm or stop uses to consume it, and an anonymous arm could never
            // be consumed by anything except its own firing.
            throw new CapabilityException(
                ErrorClasses.ValidationError,
                "payload.alarm_id is required for desktop.alarm_arm: it is what consumes the arm later",
                retryable: false);
        }

        var fireAt = ParseInstant(payload["fire_at"], "fire_at");
        var grace = ParseGrace(payload["grace_s"]);

        var arm = new ArmedAlarm
        {
            AlarmId = alarmId,
            FireAt = fireAt,
            GraceSeconds = grace,
            Label = payload["label"]?.GetValue<string>(),
            WakeVolume = payload["wake_volume"] is JsonObject wake ? (JsonObject)wake.DeepClone() : null,
            MaxDurationSeconds = ParseOptionalInt(payload["max_duration_s"], "max_duration_s"),
            ArmedAt = _time.GetUtcNow(),
        };

        bool replaced;
        int count;
        lock (_sync)
        {
            ObjectDisposedException.ThrowIf(_disposed, this);
            replaced = _store.Upsert(arm);
            _reportedRingFailures.Remove(alarmId);
            count = _store.Count;
            if (AutoTick)
            {
                StartTickLocked();
            }
        }

        _logger.LogInformation(
            "alarm {AlarmId} armed locally for {FireLocalAt:O} (fire_at {FireAt:O} + {Grace}s grace); {Count} armed",
            alarmId,
            arm.FireLocalAt,
            fireAt,
            grace,
            count);
        _audit?.Write(
            "alarm_arm",
            capability: AgentCapabilities.DesktopAlarmArm,
            status: AckStatus.Succeeded,
            detail: $"alarm_id={alarmId}; fire_at={fireAt:O}; grace_s={grace}; "
                + $"fire_local_at={arm.FireLocalAt:O}; replaced={replaced}");

        return new JsonObject
        {
            ["armed"] = true,
            ["alarm_id"] = alarmId,
            ["fire_at"] = fireAt.ToString("O", CultureInfo.InvariantCulture),
            ["grace_s"] = grace,
            ["fire_local_at"] = arm.FireLocalAt.ToString("O", CultureInfo.InvariantCulture),
            ["replaced"] = replaced,
            ["armed_count"] = count,
        };
    }

    /// <summary>
    /// Payload: <c>{"alarm_id": "&lt;id&gt;"?}</c> — omit it to forget every arm.
    /// Result: <c>{"disarmed": true, "alarm_id": "…"|null, "was_armed": &lt;bool&gt;,
    /// "armed_count": &lt;i&gt;}</c>.
    ///
    /// <para>Idempotent, for the same reason <c>alarm_stop</c> is: the caller said "do not ring
    /// this", and after this call it will not ring. Disarming something that was never armed is
    /// a success with <c>was_armed: false</c>, not an error to retry.</para>
    /// </summary>
    public JsonObject Disarm(JsonObject payload)
    {
        ArgumentNullException.ThrowIfNull(payload);
        var alarmId = payload["alarm_id"]?.GetValue<string>();

        bool wasArmed;
        int count;
        lock (_sync)
        {
            wasArmed = string.IsNullOrWhiteSpace(alarmId)
                ? _store.RemoveAll() > 0
                : _store.Remove(alarmId);
            count = _store.Count;
        }

        _logger.LogInformation(
            "alarm arm disarmed: alarm_id={AlarmId} was_armed={WasArmed}; {Count} still armed",
            alarmId ?? "(all)",
            wasArmed,
            count);
        _audit?.Write(
            "alarm_disarm",
            capability: AgentCapabilities.DesktopAlarmDisarm,
            status: AckStatus.Succeeded,
            detail: $"alarm_id={alarmId ?? "(all)"}; was_armed={wasArmed}");

        return new JsonObject
        {
            ["disarmed"] = true,
            ["alarm_id"] = alarmId,
            ["was_armed"] = wasArmed,
            ["armed_count"] = count,
        };
    }

    /// <summary>
    /// The cloud got there first (or the owner did): forget the arm for this id, if any.
    /// Called from the <c>desktop.alarm_start</c> and <c>desktop.alarm_stop</c> paths, which is
    /// what makes "the fallback never doubles the cloud's alarm" true rather than likely.
    /// </summary>
    public bool Consume(string? alarmId, string reason)
    {
        if (string.IsNullOrWhiteSpace(alarmId))
        {
            return false;
        }

        bool removed;
        lock (_sync)
        {
            removed = _store.Remove(alarmId);
        }

        if (removed)
        {
            _logger.LogInformation("local fallback for alarm {AlarmId} consumed ({Reason})", alarmId, reason);
            _audit?.Write(
                "alarm_arm_consumed",
                capability: AgentCapabilities.DesktopAlarmArm,
                status: AckStatus.Succeeded,
                detail: $"alarm_id={alarmId}; reason={reason}");
        }

        return removed;
    }

    /// <summary>
    /// Evaluates the arms persisted by an earlier run. An overdue arm rings once when it is less
    /// than <see cref="StaleAfter"/> late and is expired with an audit row otherwise. Returns
    /// how many rang. Call once at startup, before the ticker starts.
    /// </summary>
    public int ReloadOnStart()
    {
        var armed = ArmedCount;
        if (armed > 0)
        {
            _logger.LogInformation(
                "{Count} armed alarm(s) reloaded from {Path}; next local ring {Next}",
                armed,
                _store.FilePath,
                NextFireLocalAt?.ToString("O", CultureInfo.InvariantCulture) ?? "-");
        }

        var rang = Tick();
        lock (_sync)
        {
            if (AutoTick && _store.Count > 0)
            {
                StartTickLocked();
            }
        }

        return rang;
    }

    /// <summary>
    /// Fires everything due. Returns how many alarms this call rang. Public so a test can
    /// advance a manual clock and drive it deterministically.
    /// </summary>
    public int Tick()
    {
        var now = _time.GetUtcNow();
        List<ArmedAlarm> due;
        lock (_sync)
        {
            due = [.. _store.All.Where(a => a.FireLocalAt <= now)];
        }

        var rang = 0;
        foreach (var arm in due)
        {
            var lateness = now - arm.FireLocalAt;
            if (lateness >= StaleAfter)
            {
                lock (_sync)
                {
                    _store.Remove(arm.AlarmId);
                }

                Expired++;
                _logger.LogWarning(
                    "armed alarm {AlarmId} was due {Late} ago and is too late to be a wake-up; expired without ringing",
                    arm.AlarmId,
                    lateness);
                _audit?.Write(
                    "alarm_arm_expired",
                    capability: AgentCapabilities.DesktopAlarmArm,
                    status: AckStatus.Failed,
                    detail: $"alarm_id={arm.AlarmId}; fire_local_at={arm.FireLocalAt:O}; "
                        + $"late_s={(int)lateness.TotalSeconds}");
                continue;
            }

            if (RingLocally(arm, lateness))
            {
                rang++;
            }
        }

        return rang;
    }

    public void Dispose()
    {
        Task? ticker;
        lock (_sync)
        {
            if (_disposed)
            {
                return;
            }

            _disposed = true;
            _tickCts?.Cancel();
            ticker = _tickTask;
        }

        try
        {
            ticker?.Wait(TimeSpan.FromSeconds(2));
        }
        catch (Exception)
        {
            // Teardown only; the ticker logs its own outcome.
        }

        lock (_sync)
        {
            _tickCts?.Dispose();
            _tickCts = null;
        }
    }

    // ---------------------------------------------------------------- internals

    private bool RingLocally(ArmedAlarm arm, TimeSpan lateness)
    {
        if (_alarm is null)
        {
            ReportRingFailure(arm, "no alarm output is configured on this companion");
            return false;
        }

        // Removed and PERSISTED before a single sample is generated. If this process dies
        // between here and the first sound, the owner gets a missed alarm — which they will
        // notice — rather than a second one on the next start, which they might not.
        lock (_sync)
        {
            _store.Remove(arm.AlarmId);
        }

        var payload = new JsonObject { ["alarm_id"] = arm.AlarmId };
        if (arm.Label is not null)
        {
            payload["label"] = arm.Label;
        }

        if (arm.WakeVolume is not null)
        {
            payload["wake_volume"] = arm.WakeVolume.DeepClone();
        }

        if (arm.MaxDurationSeconds is not null)
        {
            payload["max_duration_s"] = arm.MaxDurationSeconds.Value;
        }

        try
        {
            _alarm.Start(payload);
        }
        catch (Exception ex)
        {
            ReportRingFailure(arm, ex.Message);
            return false;
        }

        LocalRings++;
        _logger.LogWarning(
            "alarm {AlarmId} rang from the LOCAL fallback: no cloud alarm_start arrived within {Grace}s of {FireAt:O} (late {Late})",
            arm.AlarmId,
            arm.GraceSeconds,
            arm.FireAt,
            lateness);
        _audit?.Write(
            "alarm_arm_fired",
            capability: AgentCapabilities.DesktopAlarmArm,
            status: AckStatus.Succeeded,
            detail: $"alarm_id={arm.AlarmId}; fire_at={arm.FireAt:O}; grace_s={arm.GraceSeconds}; "
                + $"late_s={(int)lateness.TotalSeconds}; source=local_fallback");
        return true;
    }

    private void ReportRingFailure(ArmedAlarm arm, string reason)
    {
        // Once per arm: a render endpoint that is not there will still not be there on the next
        // tick, and one audit row per second would bury the record it is supposed to keep.
        bool first;
        lock (_sync)
        {
            first = _reportedRingFailures.Add(arm.AlarmId);
        }

        if (!first)
        {
            _logger.LogDebug("local fallback for alarm {AlarmId} still cannot ring: {Reason}", arm.AlarmId, reason);
            return;
        }

        _logger.LogError("local fallback for alarm {AlarmId} could not ring: {Reason}", arm.AlarmId, reason);
        _audit?.Write(
            "alarm_arm_ring_failed",
            capability: AgentCapabilities.DesktopAlarmArm,
            status: AckStatus.Failed,
            detail: $"alarm_id={arm.AlarmId}; reason={reason}");
    }

    private void StartTickLocked()
    {
        if (_tickTask is not null && !_tickTask.IsCompleted)
        {
            return;
        }

        var cts = new CancellationTokenSource();
        _tickCts?.Dispose();
        _tickCts = cts;
        var token = cts.Token;
        _tickTask = Task.Run(
            async () =>
            {
                try
                {
                    while (!token.IsCancellationRequested)
                    {
                        await Task.Delay(_tickMs, token).ConfigureAwait(false);
                        Tick();
                    }
                }
                catch (OperationCanceledException)
                {
                    // Stopped or disposed.
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "armed alarm ticker stopped unexpectedly: {Reason}", ex.Message);
                }
            },
            CancellationToken.None);
    }

    private static DateTimeOffset ParseInstant(JsonNode? node, string field)
    {
        var raw = node?.GetValueKind() == JsonValueKind.String ? node.GetValue<string>() : null;
        if (raw is null
            || !DateTimeOffset.TryParse(raw, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var value))
        {
            throw new CapabilityException(
                ErrorClasses.ValidationError,
                $"payload.{field} must be an ISO-8601 instant",
                retryable: false);
        }

        return value.ToUniversalTime();
    }

    private static int ParseGrace(JsonNode? node)
    {
        var requested = ParseOptionalInt(node, "grace_s") ?? DefaultGraceSeconds;
        return Math.Clamp(requested, MinGraceSeconds, MaxGraceSeconds);
    }

    private static int? ParseOptionalInt(JsonNode? node, string field)
    {
        if (node is null)
        {
            return null;
        }

        if (node.GetValueKind() != JsonValueKind.Number
            || !double.TryParse(node.ToJsonString(), NumberStyles.Float, CultureInfo.InvariantCulture, out var value)
            || value != Math.Floor(value)
            || value is < int.MinValue or > int.MaxValue)
        {
            throw new CapabilityException(
                ErrorClasses.ValidationError,
                $"payload.{field} must be a whole number of seconds",
                retryable: false);
        }

        return (int)value;
    }
}
