using System.Globalization;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion.Camera;

/// <summary>
/// B48 (rows 300, 326, 327, 671): the device-local presence provider behind
/// <c>desktop.camera_mode</c> and the heartbeat's <c>camera</c> / <c>presence</c> fields.
///
/// <para><b>Consent is an act.</b> The mode is <see cref="CameraModes.Off"/> after every start
/// and only a <c>desktop.camera_mode</c> command (Cloud Core relaying the owner's choice)
/// changes it. Windows' own camera permission is read before every open; a denied camera is
/// reported as <see cref="CameraStates.Blocked"/> and never worked around. The owner can also
/// close the camera from the tray indicator on this device, which outranks the cloud's mode
/// until they re-allow it there.</para>
///
/// <para><b>In memory, then gone.</b> A sample is a handful of frames, each analysed (a face
/// box, a small luma plane) and zeroed before the observation is built. Nothing is logged,
/// written or sent but the seven observation fields and this monitor's state.</para>
///
/// <para><b>The indicator is honest.</b> It shows <see cref="CameraIndicatorState.Open"/> before
/// the device is opened and leaves that state only after the device is closed.</para>
/// </summary>
public sealed class CameraPresenceMonitor : IDisposable
{
    private readonly ICameraFrameSource _source;
    private readonly ICameraIndicator _indicator;
    private readonly ICameraConsent _consent;
    private readonly IInputActivitySource _input;
    private readonly IMediaActivityProbe _media;
    private readonly TimeProvider _time;
    private readonly CameraOptions _options;
    private readonly ILogger _logger;
    private readonly AuditLog? _audit;
    private readonly PresenceClassifier _classifier;
    private readonly SemaphoreSlim _checkGate = new(1, 1);
    private readonly object _sync = new();

    private string _mode = CameraModes.Off;
    private TimeSpan _interval;
    private string _state = CameraStates.Off;
    private string? _error;
    private bool _vetoed;
    private DateTimeOffset? _lastCheckAt;
    private CameraObservation? _latest;
    private ICameraSession? _session;
    private CancellationTokenSource _wake = new();
    private bool _disposed;

    public CameraPresenceMonitor(
        ICameraFrameSource source,
        ICameraIndicator indicator,
        ILogger logger,
        CameraOptions? options = null,
        ICameraConsent? consent = null,
        IInputActivitySource? input = null,
        IMediaActivityProbe? media = null,
        TimeProvider? time = null,
        AuditLog? audit = null)
    {
        _source = source;
        _indicator = indicator;
        _logger = logger;
        _options = options ?? new CameraOptions();
        _consent = consent ?? AllowAllCameraConsent.Instance;
        _input = input ?? UnknownInputActivitySource.Instance;
        _media = media ?? UnknownMediaActivityProbe.Instance;
        _time = time ?? TimeProvider.System;
        _audit = audit;
        _classifier = new PresenceClassifier(_options);
        _interval = _options.PeriodicInterval;
        _indicator.OwnerVeto += OnOwnerVeto;
        if (!_options.EnabledOnDevice)
        {
            _state = CameraStates.Blocked;
            _error = "disabled_on_device";
        }
    }

    public ICameraIndicator Indicator => _indicator;

    public string Mode
    {
        get
        {
            lock (_sync)
            {
                return _mode;
            }
        }
    }

    public string State
    {
        get
        {
            lock (_sync)
            {
                return _state;
            }
        }
    }

    /// <summary>Whether a camera session is open right now.</summary>
    public bool IsOpen
    {
        get
        {
            lock (_sync)
            {
                return _session is not null;
            }
        }
    }

    /// <summary>
    /// <c>desktop.camera_mode</c>. Payload <c>{"mode"?: "off"|"periodic"|"continuous",
    /// "interval_s"?: int, "reason"?: str}</c>; no mode reads. Never blocks on the camera: the
    /// change is applied by the monitor loop, woken here.
    /// </summary>
    public JsonObject Configure(JsonObject payload)
    {
        ArgumentNullException.ThrowIfNull(payload);
        string? requested = null;
        if (payload.TryGetPropertyValue("mode", out var modeNode) && modeNode is not null)
        {
            requested = modeNode is JsonValue value && value.TryGetValue<string>(out var text) ? text : null;
            if (!CameraModes.IsKnown(requested))
            {
                throw new CapabilityException(
                    ErrorClasses.ValidationError,
                    $"mode must be one of {string.Join(", ", CameraModes.All)}",
                    retryable: false);
            }
        }

        TimeSpan? interval = null;
        if (payload.TryGetPropertyValue("interval_s", out var intervalNode) && intervalNode is not null)
        {
            if (intervalNode is not JsonValue number || !number.TryGetValue<int>(out var seconds))
            {
                throw new CapabilityException(ErrorClasses.ValidationError, "interval_s must be an integer", retryable: false);
            }

            interval = TimeSpan.FromSeconds(seconds);
        }

        var changed = false;
        string previous;
        lock (_sync)
        {
            previous = _mode;
            if (requested is not null)
            {
                changed = requested != _mode;
                _mode = requested;
                var wanted = interval ?? _options.IntervalFor(requested);
                var clamped = CameraOptions.Clamp(requested, wanted);
                changed |= clamped != _interval && requested != CameraModes.Off;
                _interval = clamped;
                if (requested == CameraModes.Off)
                {
                    // Nothing derived from a camera the owner just closed stays on this device.
                    _latest = null;
                    _classifier.Reset();
                }
            }
        }

        if (changed)
        {
            var reason = payload["reason"] is JsonValue r && r.TryGetValue<string>(out var why) ? why : "-";
            _audit?.Write(
                "camera_mode",
                capability: AgentCapabilities.DesktopCameraMode,
                status: AckStatus.Succeeded,
                detail: $"mode={requested}; previous={previous}; reason={Truncate(reason, 48)}");
            _logger.LogInformation("camera mode {Previous} -> {Mode}", previous, requested);
            Wake();
        }

        var result = StatusObject();
        result["changed"] = changed;
        return result;
    }

    /// <summary>The heartbeat's <c>camera</c> object (DEVICE_PROTOCOL.md §6o).</summary>
    public JsonObject StatusObject()
    {
        lock (_sync)
        {
            return new JsonObject
            {
                ["mode"] = _mode,
                ["state"] = _state,
                ["interval_s"] = (int)_interval.TotalSeconds,
                ["indicator"] = _indicator.State switch
                {
                    CameraIndicatorState.Open => "open",
                    CameraIndicatorState.Armed => "armed",
                    _ => "hidden",
                },
                ["last_check_at"] = _lastCheckAt is null
                    ? null
                    : JsonValue.Create(_lastCheckAt.Value.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'", CultureInfo.InvariantCulture)),
                ["error"] = _error,
            };
        }
    }

    /// <summary>The heartbeat's <c>presence</c> object: the latest observation, or null (mode off, or none yet).</summary>
    public JsonObject? LatestObservation()
    {
        lock (_sync)
        {
            return _mode == CameraModes.Off || _vetoed ? null : _latest?.ToJson();
        }
    }

    /// <summary>The loop: one check, then sleep for the interval (or until woken by a mode change).</summary>
    public async Task RunAsync(CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested)
        {
            CancellationToken wake;
            TimeSpan interval;
            lock (_sync)
            {
                wake = _wake.Token;
                interval = _interval;
            }

            try
            {
                await CheckOnceAsync(cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                _logger.LogWarning("camera check failed: {Reason}", ex.GetType().Name);
            }

            using var linked = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, wake);
            try
            {
                if (Mode == CameraModes.Off)
                {
                    await Task.Delay(Timeout.InfiniteTimeSpan, _time, linked.Token).ConfigureAwait(false);
                }
                else
                {
                    await Task.Delay(interval, _time, linked.Token).ConfigureAwait(false);
                }
            }
            catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
            {
                // Woken by a mode change: check again now.
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }

        await CloseAsync().ConfigureAwait(false);
    }

    /// <summary>
    /// One pass. With the mode off (or vetoed, or the camera denied) it CLOSES whatever is open
    /// and hides the indicator; otherwise it takes one sample. Returns whether an observation
    /// was produced.
    /// </summary>
    public async Task<bool> CheckOnceAsync(CancellationToken cancellationToken)
    {
        await _checkGate.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            string mode;
            bool vetoed;
            lock (_sync)
            {
                mode = _mode;
                vetoed = _vetoed;
            }

            if (!_options.EnabledOnDevice)
            {
                await CloseAsync().ConfigureAwait(false);
                SetState(CameraStates.Blocked, "disabled_on_device");
                return false;
            }

            if (mode == CameraModes.Off)
            {
                await CloseAsync().ConfigureAwait(false);
                _indicator.Set(CameraIndicatorState.Hidden, mode);
                SetState(CameraStates.Off, null);
                return false;
            }

            if (vetoed)
            {
                await CloseAsync().ConfigureAwait(false);
                _indicator.Set(CameraIndicatorState.Armed, mode);
                SetState(CameraStates.Vetoed, "owner_closed_on_device");
                return false;
            }

            var denied = SafeDeniedBy();
            if (denied is not null)
            {
                await CloseAsync().ConfigureAwait(false);
                _indicator.Set(CameraIndicatorState.Armed, mode);
                SetBlockedOnce(denied);
                return false;
            }

            ICameraSession session;
            try
            {
                session = await OpenAsync(mode, cancellationToken).ConfigureAwait(false);
            }
            catch (CameraAccessException ex)
            {
                _indicator.Set(CameraIndicatorState.Armed, mode);
                var state = ex.Failure switch
                {
                    CameraFailure.Blocked => CameraStates.Blocked,
                    CameraFailure.Unavailable => CameraStates.Unavailable,
                    CameraFailure.Busy => CameraStates.Busy,
                    _ => CameraStates.Error,
                };
                if (state == CameraStates.Blocked)
                {
                    SetBlockedOnce(ex.Token);
                }
                else
                {
                    SetState(state, ex.Token);
                }

                return false;
            }

            var frames = new List<CameraFrame>(_options.FramesPerSample);
            SampleSummary summary;
            try
            {
                for (var i = 0; i < _options.FramesPerSample; i++)
                {
                    if (i > 0)
                    {
                        await Task.Delay(_options.FrameSpacing, _time, cancellationToken).ConfigureAwait(false);
                    }

                    var frame = await session.NextFrameAsync(_options.FrameTimeout, cancellationToken).ConfigureAwait(false);
                    if (frame is null)
                    {
                        break;
                    }

                    frames.Add(frame);
                }

                summary = SampleSummary.Of(frames);
            }
            finally
            {
                foreach (var frame in frames)
                {
                    frame.Dispose();
                }

                frames.Clear();
                if (mode != CameraModes.Continuous || Mode != CameraModes.Continuous)
                {
                    await CloseAsync().ConfigureAwait(false);
                    _indicator.Set(Mode == CameraModes.Off ? CameraIndicatorState.Hidden : CameraIndicatorState.Armed, mode);
                }
            }

            var now = _time.GetUtcNow();
            var observation = _classifier.Classify(summary, now, SafeIdle(), SafeMediaPlaying());
            lock (_sync)
            {
                _lastCheckAt = now;
                if (observation is null)
                {
                    _state = CameraStates.Error;
                    _error = "no_frames";
                    return false;
                }

                if (_mode == CameraModes.Off)
                {
                    // Closed while the sample ran: the owner's "off" wins over the frames.
                    return false;
                }

                _latest = observation;
                _state = _session is not null ? CameraStates.Capturing : CameraStates.Idle;
                _error = null;
            }

            return true;
        }
        finally
        {
            _checkGate.Release();
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        _indicator.OwnerVeto -= OnOwnerVeto;
        CloseAsync().AsTask().GetAwaiter().GetResult();
        _indicator.Set(CameraIndicatorState.Hidden, CameraModes.Off);
        _wake.Dispose();
        _checkGate.Dispose();
    }

    private async Task<ICameraSession> OpenAsync(string mode, CancellationToken cancellationToken)
    {
        lock (_sync)
        {
            if (_session is not null)
            {
                return _session;
            }
        }

        // The indicator goes up BEFORE the device opens: there is no instant at which the
        // camera is open and the owner has not been told.
        _indicator.Set(CameraIndicatorState.Open, mode);
        SetState(CameraStates.Capturing, null);
        var session = await _source.OpenAsync(cancellationToken).ConfigureAwait(false);
        lock (_sync)
        {
            _session = session;
        }

        return session;
    }

    private async ValueTask CloseAsync()
    {
        ICameraSession? session;
        lock (_sync)
        {
            session = _session;
            _session = null;
        }

        if (session is null)
        {
            return;
        }

        try
        {
            await session.DisposeAsync().ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            _logger.LogWarning("closing the camera failed: {Reason}", ex.GetType().Name);
        }
    }

    private void OnOwnerVeto(bool vetoed)
    {
        lock (_sync)
        {
            if (_vetoed == vetoed)
            {
                return;
            }

            _vetoed = vetoed;
            if (vetoed)
            {
                _latest = null;
                _classifier.Reset();
            }
        }

        _audit?.Write(
            vetoed ? "camera_owner_closed" : "camera_owner_allowed",
            capability: AgentCapabilities.DesktopCameraMode,
            status: AckStatus.Succeeded,
            detail: "source=tray");
        Wake();
    }

    private void Wake()
    {
        CancellationTokenSource previous;
        lock (_sync)
        {
            previous = _wake;
            _wake = new CancellationTokenSource();
        }

        previous.Cancel();
        previous.Dispose();
    }

    private void SetState(string state, string? error)
    {
        lock (_sync)
        {
            _state = state;
            _error = error;
        }
    }

    private void SetBlockedOnce(string token)
    {
        bool first;
        lock (_sync)
        {
            first = _state != CameraStates.Blocked || _error != token;
            _state = CameraStates.Blocked;
            _error = token;
            _latest = null;
        }

        if (first)
        {
            _audit?.Write("camera_blocked", capability: AgentCapabilities.DesktopCameraMode, status: AckStatus.Failed, detail: $"by={token}");
            _logger.LogWarning("camera blocked by {Token}; nothing was opened", token);
        }
    }

    private string? SafeDeniedBy()
    {
        try
        {
            return _consent.DeniedBy();
        }
        catch (Exception)
        {
            return null;
        }
    }

    private TimeSpan? SafeIdle()
    {
        try
        {
            return _input.IdleTime;
        }
        catch (Exception)
        {
            return null;
        }
    }

    private bool? SafeMediaPlaying()
    {
        try
        {
            return _media.IsPlaying();
        }
        catch (Exception)
        {
            return null;
        }
    }

    private static string Truncate(string text, int max) => text.Length <= max ? text : text[..max];
}
