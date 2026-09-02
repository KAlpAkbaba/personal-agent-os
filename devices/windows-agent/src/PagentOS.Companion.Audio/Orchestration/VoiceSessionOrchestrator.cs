using System.Text.Json.Nodes;
using System.Threading.Channels;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Audio.Processing;
using PagentOS.Companion.Audio.Audio.Vad;
using PagentOS.Companion.Audio.Media;
using PagentOS.Companion.Audio.Session;
using PagentOS.Companion.Audio.Sideband;
using PagentOS.Companion.Audio.Timing;
using PagentOS.Companion.Audio.Turn;

namespace PagentOS.Companion.Audio.Orchestration;

/// <summary>Capture backends that can say whether the driver's voice-processing APOs were engaged.</summary>
public interface IReportsDriverProcessing
{
    bool DriverApoActive { get; }
}

public sealed record DeviceSwitchRecord(AudioDirection Direction, string? From, string To, string Reason, bool Immediate);

/// <summary>
/// Wires the whole client together and runs it as ONE event loop: microphone frames,
/// provider events, Cloud Core pushes, device changes and playback drain all enter through a
/// single channel and are handled in order on one task. That is what makes the barge-in
/// path a straight line — frame arrives, VAD says onset, playback stops — with no lock in
/// it, and what lets the tests and the offline bench drive it deterministically with fakes.
///
/// Track C scope: this is the desktop audio client of the M12 session contract. It does not
/// resolve Turkish intents (track E, in Cloud Core) or select providers (track A). Since
/// ADR-0039 it speaks the server's contract exactly: batched events with the server's kinds,
/// pushes from the device-protocol <c>voice_sideband</c> frame or the HTTP backlog, and the
/// server's verdicts — 409 (another client holds the leg: stop, re-attach when the owner
/// speaks to this device again), 410 (session gone: end, the host opens a new one), 422
/// (a client bug: logged loudly, never retried).
/// </summary>
public sealed class VoiceSessionOrchestrator : IAsyncDisposable
{
    private readonly VoiceClientOptions _options;
    private readonly IAudioDeviceCatalog _catalog;
    private readonly IAudioDeviceFactory _devices;
    private readonly Func<RealtimeSessionGrant, IMediaLeg> _legFactory;
    private readonly ISidebandClient _sideband;
    private readonly ISidebandPushSource _pushes;
    private readonly TimeProvider _time;
    private readonly ILogger? _logger;
    private readonly AuditLog? _audit;
    private readonly IAudioProcessor _processor;
    private readonly IVoiceEventReporter? _reporterOverride;
    private readonly Channel<Input> _inputs = Channel.CreateUnbounded<Input>(new UnboundedChannelOptions { SingleReader = true });
    private readonly AudioDeviceSelector _captureSelector;
    private readonly AudioDeviceSelector _renderSelector;
    private readonly List<DeviceSwitchRecord> _deviceSwitches = new();
    private readonly List<string> _defects = new();
    private readonly List<Task> _background = new();

    private IMediaLeg? _leg;
    private CancellationTokenSource? _legPumpCts;
    private IAudioCapture? _capture;
    private IAudioPlayback? _playback;
    private AudioDeviceInfo? _renderInfo;
    private DeviceSwitch? _pendingCaptureSwitch;
    private DeviceSwitch? _pendingRenderSwitch;
    private ClientEndOfTurnDetector? _eot;
    private BargeInController? _bargeIn;
    private ToolCallRelay? _relay;
    private VoiceEventReporter? _reporter;
    private IVoiceEventReporter? _events;
    private Task? _loop;
    private CancellationTokenSource? _runCts;
    private long _sessionStartedAt;
    private bool _uplinkPending;
    private bool _turnOpen;
    private bool _responseDone;
    private string? _currentResponseId;
    private bool _firstAudioOfResponsePending;
    private bool _reconnecting;
    private bool _legSuperseded;
    private bool _sessionGone;
    private long _disconnectedAt;
    private long _toolStartedAt;
    private long _lastAssistantAudioAt;
    private bool _toolSilenceReported;

    public VoiceSessionOrchestrator(
        VoiceClientOptions options,
        IAudioDeviceCatalog catalog,
        IAudioDeviceFactory devices,
        Func<RealtimeSessionGrant, IMediaLeg> legFactory,
        ISidebandClient sideband,
        ISidebandPushSource pushes,
        TimeProvider time,
        ILogger? logger = null,
        AuditLog? audit = null,
        IAudioProcessor? processor = null,
        IVoiceEventReporter? reporterOverride = null)
    {
        _options = options;
        _catalog = catalog;
        _devices = devices;
        _legFactory = legFactory;
        _sideband = sideband;
        _pushes = pushes;
        _time = time;
        _logger = logger;
        _audit = audit;
        _processor = processor ?? new ProcessorChain(new IAudioProcessor[] { new DcBlockerProcessor(), new NoiseGateProcessor() });
        _reporterOverride = reporterOverride;
        _captureSelector = new AudioDeviceSelector(AudioDirection.Capture, options.PreferredCaptureDeviceId);
        _renderSelector = new AudioDeviceSelector(AudioDirection.Render, options.PreferredRenderDeviceId);
        Fsm = new VoiceClientStateMachine(time);
        Latency = new LatencyRecorder(time);
    }

    public VoiceClientStateMachine Fsm { get; }

    public LatencyRecorder Latency { get; }

    public RealtimeSessionGrant? Grant { get; private set; }

    public IMediaLeg? Leg => _leg;

    public IAudioCapture? Capture => _capture;

    public IAudioPlayback? Playback => _playback;

    public VoiceEventReporter? Reporter => _reporter;

    public ToolCallRelay? Relay => _relay;

    public AudioProcessingReport? ProcessingReport { get; private set; }

    public int ReconnectCount { get; private set; }

    /// <summary>Times the media leg was closed because another client took the session over (409 or a leg_closed push).</summary>
    public int LegSupersededCount { get; private set; }

    /// <summary>Times this client re-attached after being superseded, on the owner's next speech.</summary>
    public int ReattachCount { get; private set; }

    /// <summary>True while another client holds the leg: no uplink, no playback, until the owner speaks here again.</summary>
    public bool LegSuperseded => _legSuperseded;

    /// <summary>Why the loop ended on its own (<c>session_gone</c>), or null while running / stopped by the caller.</summary>
    public string? StopReason { get; private set; }

    /// <summary>Sideband pushes handled, by kind (asserted by tests).</summary>
    public IReadOnlyList<string> PushesHandled
    {
        get
        {
            lock (_pushesHandled)
            {
                return _pushesHandled.ToList();
            }
        }
    }

    private readonly List<string> _pushesHandled = new();

    public IReadOnlyList<DeviceSwitchRecord> DeviceSwitches
    {
        get
        {
            lock (_deviceSwitches)
            {
                return _deviceSwitches.ToList();
            }
        }
    }

    /// <summary>Things the harness must report as defects (spec §6), e.g. tool silence over the bound.</summary>
    public IReadOnlyList<string> Defects
    {
        get
        {
            lock (_defects)
            {
                return _defects.ToList();
            }
        }
    }

    /// <summary>Creates the Cloud Core session, opens the media leg and the devices, and starts the loop.</summary>
    public async Task<RealtimeSessionGrant> StartAsync(CancellationToken cancellationToken)
    {
        if (_loop is not null)
        {
            throw new InvalidOperationException("orchestrator already started");
        }

        _runCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        var ct = _runCts.Token;
        _sessionStartedAt = _time.GetTimestamp();

        Grant = await CreateSessionAsync(ct).ConfigureAwait(false);

        _events = _reporterOverride;
        if (_events is null)
        {
            _reporter = new VoiceEventReporter(
                _sideband, Grant.SessionId, _time, _sessionStartedAt, _logger,
                turnProvider: () => Latency.Current?.Index ?? 0,
                onAck: ack => EnqueuePushes(ack.PendingSideband),
                onFault: fault => _inputs.Writer.TryWrite(new SidebandFaultInput(fault)));
            _events = _reporter;
        }

        _eot = new ClientEndOfTurnDetector(new EnergyVad(_options.Vad), new HesitationGuard(_options.Hesitation), _time);
        _bargeIn = new BargeInController(() => _playback!, () => _leg!, _events, Fsm, _time, _logger);
        _relay = new ToolCallRelay(_sideband, () => _leg!, _events, Fsm, _time, Grant.SessionId, _logger);

        OpenDevicesInitial();
        await OpenLegAsync(Grant, ct).ConfigureAwait(false);

        _catalog.DevicesChanged += OnDevicesChanged;
        _background.Add(Task.Run(() => PumpPushesAsync(ct), CancellationToken.None));
        _loop = Task.Run(() => LoopAsync(ct), CancellationToken.None);

        var driverApo = _capture is IReportsDriverProcessing reports && reports.DriverApoActive;
        ProcessingReport = AudioProcessingReport.Describe(driverApo, _renderInfo?.IsHeadsetLike ?? false, _processor);
        _audit?.Write("voice_session_started", status: "ok", detail: new JsonObject
        {
            ["grant"] = Grant.ToAuditJson(),
            ["capture_device"] = _capture?.DeviceId,
            ["render_device"] = _playback?.DeviceId,
            ["transport"] = _leg?.Transport,
            ["processing"] = new JsonObject
            {
                ["echo_cancellation"] = ProcessingReport.EchoCancellation,
                ["noise_suppression"] = ProcessingReport.NoiseSuppression,
                ["client_processors"] = new JsonArray(ProcessingReport.ClientProcessors.Select(p => (JsonNode)p).ToArray()),
            },
        }.ToJsonString());
        _logger?.LogInformation(
            "voice session {SessionId} up: provider={Provider} transport={Transport} capture={Capture} render={Render} aec={Aec} ns={Ns}",
            Grant.SessionId, Grant.Provider, _leg?.Transport, _capture?.DeviceId, _playback?.DeviceId,
            ProcessingReport.EchoCancellation, ProcessingReport.NoiseSuppression);

        _capture?.Start();
        _playback?.Start();
        return Grant;
    }

    /// <summary>
    /// <c>POST /sessions</c> with this client's first transport preference. The server answers
    /// 422 naming the provider's transports when that one is not offered; the next preference
    /// the provider offers is tried once, and a provider that offers none of ours is an error
    /// said out loud rather than a WebRTC grant this build cannot open.
    /// </summary>
    private async Task<RealtimeSessionGrant> CreateSessionAsync(CancellationToken ct)
    {
        var preferences = _options.TransportPreference.Count == 0 ? new[] { (string?)null } : _options.TransportPreference.Cast<string?>().ToArray();
        try
        {
            return await _sideband.CreateSessionAsync(new CreateSessionRequest(_options.ClientKind, preferences[0]), ct).ConfigureAwait(false);
        }
        catch (SidebandException ex) when (ex.IsPayloadRefused && OfferedTransports(ex.Body) is { Count: > 0 } offered)
        {
            var fallback = _options.TransportPreference.FirstOrDefault(offered.Contains)
                ?? throw new NotSupportedException(
                    $"Cloud Core's provider offers transports [{string.Join(", ", offered)}]; this client supports [{string.Join(", ", _options.TransportPreference)}]");
            _logger?.LogInformation("transport {Requested} not offered; using {Fallback}", preferences[0], fallback);
            return await _sideband.CreateSessionAsync(new CreateSessionRequest(_options.ClientKind, fallback), ct).ConfigureAwait(false);
        }
    }

    private static List<string>? OfferedTransports(string body)
    {
        try
        {
            return (JsonNode.Parse(body)?["detail"]?["transports"] as JsonArray)?
                .Select(t => t?.GetValue<string>())
                .Where(t => t is not null)
                .Select(t => t!)
                .ToList();
        }
        catch (Exception)
        {
            return null;
        }
    }

    public async Task RunAsync(CancellationToken cancellationToken)
    {
        await StartAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            await _loop!.ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            // Normal shutdown.
        }
    }

    public async Task StopAsync()
    {
        if (_runCts is null)
        {
            return;
        }

        _runCts.Cancel();
        _catalog.DevicesChanged -= OnDevicesChanged;
        _capture?.Stop();
        _playback?.StopImmediately();
        _inputs.Writer.TryComplete();
        if (_loop is not null)
        {
            try
            {
                await _loop.ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
            }
        }

        if (_leg is not null)
        {
            await _leg.CloseAsync(CancellationToken.None).ConfigureAwait(false);
        }

        if (Fsm.State != VoiceClientState.Closed)
        {
            Fsm.Close();
        }

        _audit?.Write("voice_session_closed", status: "ok", detail: new JsonObject
        {
            ["session_id"] = Grant?.SessionId,
            ["barge_ins"] = Fsm.BargeInCount,
            ["reconnects"] = ReconnectCount,
            ["leg_superseded"] = LegSupersededCount,
            ["reattached"] = ReattachCount,
            ["stop_reason"] = StopReason ?? "stopped",
            ["defects"] = _defects.Count,
        }.ToJsonString());
    }

    public async ValueTask DisposeAsync()
    {
        await StopAsync().ConfigureAwait(false);
        _capture?.Dispose();
        _playback?.Dispose();
        if (_leg is not null)
        {
            await _leg.DisposeAsync().ConfigureAwait(false);
        }

        _legPumpCts?.Dispose();
        _runCts?.Dispose();
    }

    // ------------------------------------------------------------------ devices

    private void OpenDevicesInitial()
    {
        var captureChoice = _captureSelector.Choose(_catalog.List(AudioDirection.Capture))
            ?? throw new InvalidOperationException("no capture device available");
        var renderChoice = _renderSelector.Choose(_catalog.List(AudioDirection.Render))
            ?? throw new InvalidOperationException("no render device available");
        SwitchCapture(new DeviceSwitch(null, captureChoice.DeviceId, captureChoice.Reason, Immediate: true), start: false);
        SwitchRender(new DeviceSwitch(null, renderChoice.DeviceId, renderChoice.Reason, Immediate: true), start: false);
    }

    private void SwitchCapture(DeviceSwitch change, bool start)
    {
        var old = _capture;
        if (old is not null)
        {
            old.FrameCaptured -= OnFrameCaptured;
            old.Stop();
            old.Dispose();
        }

        var capture = _devices.OpenCapture(change.ToDeviceId, _options.Format);
        capture.FrameCaptured += OnFrameCaptured;
        _capture = capture;
        _captureSelector.MarkActive(change.ToDeviceId);
        _eot?.Reset();
        if (start)
        {
            capture.Start();
        }

        RecordSwitch(AudioDirection.Capture, change);
    }

    private void SwitchRender(DeviceSwitch change, bool start)
    {
        var old = _playback;
        if (old is not null)
        {
            old.Drained -= OnPlaybackDrained;
            old.StopImmediately();
            old.Dispose();
        }

        var playback = _devices.OpenPlayback(change.ToDeviceId, _options.Format);
        playback.Drained += OnPlaybackDrained;
        _playback = playback;
        _renderInfo = _catalog.List(AudioDirection.Render).FirstOrDefault(d => d.Id == change.ToDeviceId);
        _renderSelector.MarkActive(change.ToDeviceId);
        if (start)
        {
            playback.Start();
        }

        RecordSwitch(AudioDirection.Render, change);
    }

    private void RecordSwitch(AudioDirection direction, DeviceSwitch change)
    {
        var record = new DeviceSwitchRecord(direction, change.FromDeviceId, change.ToDeviceId, change.Reason, change.Immediate);
        lock (_deviceSwitches)
        {
            _deviceSwitches.Add(record);
        }

        if (change.FromDeviceId is not null)
        {
            _logger?.LogInformation("{Direction} device switched {From} -> {To} ({Reason})", direction, change.FromDeviceId, change.ToDeviceId, change.Reason);
            _audit?.Write("voice_device_switched", status: "ok", detail: new JsonObject
            {
                ["direction"] = direction.ToString().ToLowerInvariant(),
                ["from"] = change.FromDeviceId,
                ["to"] = change.ToDeviceId,
                ["reason"] = change.Reason,
                ["immediate"] = change.Immediate,
            }.ToJsonString());
        }
    }

    private void ApplyPendingSwitchesIfIdle()
    {
        if (_turnOpen || (_playback?.IsPlaying ?? false) || Fsm.State is VoiceClientState.AssistantSpeaking or VoiceClientState.ToolRunning)
        {
            return;
        }

        if (_pendingCaptureSwitch is { } capture)
        {
            _pendingCaptureSwitch = null;
            SwitchCapture(capture, start: true);
        }

        if (_pendingRenderSwitch is { } render)
        {
            _pendingRenderSwitch = null;
            SwitchRender(render, start: true);
        }
    }

    private void OnDevicesChanged() => _inputs.Writer.TryWrite(new DevicesChangedInput());

    private void OnFrameCaptured(AudioFrame frame) => _inputs.Writer.TryWrite(new FrameInput(frame));

    private void OnPlaybackDrained() => _inputs.Writer.TryWrite(new PlaybackDrainedInput());

    // ---------------------------------------------------------------- media leg

    private async Task OpenLegAsync(RealtimeSessionGrant grant, CancellationToken ct)
    {
        var leg = _legFactory(grant);
        await leg.OpenAsync(grant, new MediaLegOptions(_options.Format, _options.EndOfTurn), ct).ConfigureAwait(false);

        _legPumpCts?.Cancel();
        _legPumpCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        var pumpToken = _legPumpCts.Token;
        var old = _leg;
        _leg = leg;
        _background.Add(Task.Run(() => PumpProviderEventsAsync(leg, pumpToken), CancellationToken.None));
        if (old is not null)
        {
            await old.DisposeAsync().ConfigureAwait(false);
        }
    }

    private async Task PumpProviderEventsAsync(IMediaLeg leg, CancellationToken ct)
    {
        try
        {
            await foreach (var providerEvent in leg.Events.ReadAllAsync(ct).ConfigureAwait(false))
            {
                _inputs.Writer.TryWrite(new ProviderInput(providerEvent, leg));
            }
        }
        catch (OperationCanceledException)
        {
        }
    }

    private async Task PumpPushesAsync(CancellationToken ct)
    {
        try
        {
            await foreach (var push in _pushes.Pushes.ReadAllAsync(ct).ConfigureAwait(false))
            {
                _inputs.Writer.TryWrite(new PushInput(push));
            }
        }
        catch (OperationCanceledException)
        {
        }
    }

    /// <summary>The sideband backlog Cloud Core returned over HTTP (events ack, attach): the same pushes, the other door.</summary>
    private void EnqueuePushes(IReadOnlyList<SidebandPush> pushes)
    {
        foreach (var push in pushes)
        {
            _inputs.Writer.TryWrite(new PushInput(push));
        }
    }

    // --------------------------------------------------------------------- loop

    private async Task LoopAsync(CancellationToken ct)
    {
        await foreach (var input in _inputs.Reader.ReadAllAsync(ct).ConfigureAwait(false))
        {
            try
            {
                switch (input)
                {
                    case FrameInput frame:
                        await HandleFrameAsync(frame.Frame, ct).ConfigureAwait(false);
                        break;
                    case ProviderInput provider when ReferenceEquals(provider.Leg, _leg) || provider.Event is DisconnectedEvent:
                        await HandleProviderEventAsync(provider.Event, provider.Leg, ct).ConfigureAwait(false);
                        break;
                    case PushInput push:
                        await HandlePushAsync(push.Push, ct).ConfigureAwait(false);
                        break;
                    case DevicesChangedInput:
                        HandleDevicesChanged();
                        break;
                    case PlaybackDrainedInput:
                        HandlePlaybackDrained();
                        break;
                    case ReconnectedInput reconnected:
                        await HandleReconnectedAsync(reconnected, ct).ConfigureAwait(false);
                        break;
                    case ToolFinishedInput finished:
                        await HandleToolFinishedAsync(finished.CallId, finished.Status, ct).ConfigureAwait(false);
                        break;
                    case SidebandFaultInput fault:
                        await HandleSidebandFaultAsync(fault.Fault, ct).ConfigureAwait(false);
                        break;
                }
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                throw;
            }
            catch (Exception ex)
            {
                // One bad input must not take the session down; it is logged and counted.
                _logger?.LogError(ex, "voice loop input {Input} failed", input.GetType().Name);
                AddDefect("loop_exception:" + input.GetType().Name + ":" + ex.GetType().Name);
            }
        }
    }

    private async Task HandleFrameAsync(AudioFrame frame, CancellationToken ct)
    {
        var playback = _playback;
        var context = new AudioProcessingContext(
            PlaybackActive: playback?.IsPlaying ?? false,
            RenderIsLoudspeaker: !(_renderInfo?.IsHeadsetLike ?? false));
        _processor.Process(frame.Samples, frame.Format, in context);

        var turnEvent = _eot!.Process(frame, in context);
        if (turnEvent is { Kind: TurnEventKind.SpeechStarted })
        {
            await OnOwnerSpeechStartedAsync(frame.CapturedAt, "vad_onset", ct).ConfigureAwait(false);
        }

        var leg = _leg;
        if (leg is { IsOpen: true } && !_legSuperseded && (_turnOpen || _options.StreamWhileIdle))
        {
            try
            {
                await leg.SendAudioAsync(frame, ct).ConfigureAwait(false);
                if (_uplinkPending)
                {
                    _uplinkPending = false;
                    var now = _time.GetTimestamp();
                    var turn = Latency.Current;
                    if (turn is not null)
                    {
                        turn.FirstUplinkAt = now;
                    }

                    await _events!.ReportAsync(VoiceClientEvents.UplinkFirstPacket, new JsonObject
                    {
                        ["mic_to_uplink_ms"] = Math.Round(_time.ElapsedMs(frame.CapturedAt, now), 3),
                        ["frame_ms"] = frame.DurationMs,
                    }, ct).ConfigureAwait(false);
                }
            }
            catch (Exception ex) when (ex is IOException or InvalidOperationException or System.Net.WebSockets.WebSocketException)
            {
                _logger?.LogWarning("uplink send failed: {Reason}", ex.Message);
            }
        }

        if (turnEvent is { Kind: TurnEventKind.SpeechEnded } ended)
        {
            await OnOwnerSpeechEndedAsync(ended, ct).ConfigureAwait(false);
        }

        CheckToolSilence();
    }

    private async Task OnOwnerSpeechStartedAsync(long startedAt, string reason, CancellationToken ct)
    {
        if (_turnOpen)
        {
            return;
        }

        if (_legSuperseded)
        {
            // The owner is talking to THIS device again: that is the moment to take the leg
            // back (spec §7), not the moment the 409 arrived — re-attaching on the refusal
            // itself would have two clients stealing the leg from each other forever.
            if (!await TryReclaimLegAsync("owner_speech", ct).ConfigureAwait(false))
            {
                return;
            }
        }

        _turnOpen = true;
        _uplinkPending = true;
        var turn = Latency.BeginTurn(startedAt);

        var decision = Fsm.OwnerSpeechStarted();
        var playing = _playback?.IsPlaying ?? false;
        if (decision.ShouldBargeIn || playing)
        {
            // Audible playback without the FSM in AssistantSpeaking (a tool preamble) is a
            // barge-in too; the controller records the cut either way.
            turn.BargeInAt = _time.GetTimestamp();
            var outcome = await _bargeIn!.ExecuteAsync(decision.StopWord, reason, ct).ConfigureAwait(false);
            turn.PlaybackStoppedAt = turn.BargeInAt + (long)(outcome.PlaybackStoppedMs * _time.TimestampFrequency / 1000.0);
            _responseDone = true;
        }

        await _events!.ReportAsync(VoiceClientEvents.MicSpeechStart, new JsonObject
        {
            ["reason"] = reason,
            ["barge_in"] = decision.ShouldBargeIn || playing,
        }, ct).ConfigureAwait(false);
    }

    private async Task OnOwnerSpeechEndedAsync(TurnEvent ended, CancellationToken ct)
    {
        if (!_turnOpen)
        {
            return;
        }

        _turnOpen = false;
        Fsm.OwnerSpeechEnded(ended.Reason);
        var turn = Latency.Current;
        if (turn is not null)
        {
            turn.SpeechEndedAt = ended.Timestamp;
            turn.HesitationExtensionMs = ended.HesitationExtensionMs;
            turn.HesitationReason = ended.Reason;
        }

        await _events!.ReportAsync(VoiceClientEvents.EndOfTurn, new JsonObject
        {
            ["trailing_silence_ms"] = Math.Round(ended.TrailingSilenceMs, 1),
            ["required_silence_ms"] = ended.RequiredSilenceMs,
            ["hesitation_extension_ms"] = ended.HesitationExtensionMs,
            ["hesitation_reason"] = ended.Reason,
            ["end_of_turn"] = _options.EndOfTurn.ToString().ToLowerInvariant(),
        }, ct).ConfigureAwait(false);

        if (_options.EndOfTurn == EndOfTurnMode.Client && _leg is { IsOpen: true } leg && !_legSuperseded)
        {
            await leg.CommitTurnAsync(ct).ConfigureAwait(false);
        }

        ApplyPendingSwitchesIfIdle();
    }

    private async Task HandleProviderEventAsync(ProviderEvent providerEvent, IMediaLeg source, CancellationToken ct)
    {
        switch (providerEvent)
        {
            case SessionReadyEvent:
                break;

            case InputSpeechStartedEvent:
                if (!_eot!.InSpeech && !_turnOpen)
                {
                    await OnOwnerSpeechStartedAsync(_time.GetTimestamp(), "server_vad", ct).ConfigureAwait(false);
                }

                break;

            case InputSpeechStoppedEvent:
                // In Server mode the provider decides; the client's own detector still reports
                // end_of_turn with the guard's verdict when its silence clock runs out.
                break;

            case ResponseStartedEvent started:
                _currentResponseId = started.ResponseId;
                _responseDone = false;
                _firstAudioOfResponsePending = true;
                break;

            case AudioDeltaEvent delta:
                await HandleAudioDeltaAsync(delta, ct).ConfigureAwait(false);
                break;

            case ResponseDoneEvent done:
                if (done.ResponseId == _currentResponseId || string.IsNullOrEmpty(done.ResponseId))
                {
                    _responseDone = true;
                    if (!(_playback?.IsPlaying ?? false) && Fsm.State == VoiceClientState.AssistantSpeaking)
                    {
                        Fsm.AssistantStopSpeaking(done.Status);
                        ApplyPendingSwitchesIfIdle();
                    }
                }

                await _events!.ReportAsync(VoiceClientEvents.ResponseDone, new JsonObject
                {
                    ["response_id"] = done.ResponseId,
                    ["status"] = done.Status,
                }, ct).ConfigureAwait(false);
                break;

            case ToolCallEvent call:
                await HandleToolCallAsync(call, ct).ConfigureAwait(false);
                break;

            case TranscriptDeltaEvent transcript:
                _eot!.ObserveTranscript(transcript.Text);
                if (transcript.Final && VoiceClientStateMachine.IsStopWord(transcript.Text)
                    && ((_playback?.IsPlaying ?? false) || Fsm.State is VoiceClientState.AssistantSpeaking or VoiceClientState.ToolRunning))
                {
                    await StopWordBargeInAsync(transcript.Text, ct).ConfigureAwait(false);
                }

                if (transcript.Final && !string.IsNullOrWhiteSpace(transcript.Text))
                {
                    // Cloud Core resolves intents from the utterance (track E); the client only
                    // transcribes. The text travels in the event's `text` field, never in payload.
                    await _events!.ReportAsync(VoiceClientEvents.Utterance, null, transcript.Text.Trim(), ct).ConfigureAwait(false);
                }

                break;

            case ProviderErrorEvent error:
                _logger?.LogWarning("provider error {Code}: {Message}", error.Code, error.Message);
                AddDefect("provider_error:" + error.Code);
                await _events!.ReportAsync(VoiceClientEvents.Error, new JsonObject
                {
                    ["error_class"] = "voice_provider_error",
                    ["code"] = error.Code,
                }, ct).ConfigureAwait(false);
                break;

            case DisconnectedEvent disconnected when ReferenceEquals(source, _leg):
                await HandleDisconnectedAsync(disconnected, ct).ConfigureAwait(false);
                break;
        }
    }

    private async Task HandleAudioDeltaAsync(AudioDeltaEvent delta, CancellationToken ct)
    {
        if (!string.Equals(delta.ResponseId, _currentResponseId, StringComparison.Ordinal))
        {
            // Audio for a response whose response.created we never saw (or a provider that
            // does not send one): treat the first delta as the start.
            _currentResponseId = delta.ResponseId;
            _responseDone = false;
            _firstAudioOfResponsePending = true;
        }

        if (_responseDone && !_firstAudioOfResponsePending)
        {
            // Audio for a response we already cancelled: drop it rather than un-barge.
            return;
        }

        if (_firstAudioOfResponsePending)
        {
            _firstAudioOfResponsePending = false;
            var now = delta.ReceivedAt;
            var turn = Latency.Current;

            // Timestamps first, FSM second: the metric describes the moment the audio
            // arrived, and an observer that sees the state change must already see the stamp.
            if (Fsm.State == VoiceClientState.ToolRunning)
            {
                var firstPreamble = turn is not null && turn.ToolPreambleAudioAt is null;
                if (firstPreamble)
                {
                    turn!.ToolPreambleAudioAt = now;
                }

                Fsm.AssistantProgress("preamble");
                if (firstPreamble)
                {
                    await _events!.ReportAsync(VoiceClientEvents.PreambleAudioStart, new JsonObject
                    {
                        ["response_id"] = delta.ResponseId,
                        ["tool_preamble_ms"] = Math.Round(_time.ElapsedMs(turn!.ToolCallAt ?? now, now), 3),
                    }, ct).ConfigureAwait(false);
                }
            }
            else
            {
                string? kind = null;
                JsonObject? report = null;
                if (turn is not null)
                {
                    if (turn.ToolDoneAt is not null && turn.ResumedSpeechAt is null)
                    {
                        turn.ResumedSpeechAt = now;
                        kind = VoiceClientEvents.SpeechResumed;
                        report = new JsonObject
                        {
                            ["response_id"] = delta.ResponseId,
                            ["tool_done_to_speech_ms"] = Math.Round(_time.ElapsedMs(turn.ToolDoneAt.Value, now), 3),
                        };
                    }
                    else if (turn.FirstAudioAt is null)
                    {
                        turn.FirstAudioAt = now;
                        kind = VoiceClientEvents.FirstAudio;
                        // "eot_to_first_ms", not "..._audio_ms": the server refuses any payload key containing "audio".
                        report = new JsonObject { ["response_id"] = delta.ResponseId };
                        if (turn.SpeechEndedAt is not null)
                        {
                            report["eot_to_first_ms"] = Math.Round(_time.ElapsedMs(turn.SpeechEndedAt.Value, now), 3);
                        }
                    }
                }

                Fsm.AssistantStartSpeaking(delta.ResponseId);
                if (kind is not null)
                {
                    await _events!.ReportAsync(kind, report, ct).ConfigureAwait(false);
                }
            }
        }

        _lastAssistantAudioAt = delta.ReceivedAt;
        _toolSilenceReported = false;
        var playback = _playback;
        if (playback is not null && delta.Pcm16.Length > 0)
        {
            playback.Start();
            playback.Enqueue(delta.Pcm16);
        }
    }

    private async Task HandleToolCallAsync(ToolCallEvent call, CancellationToken ct)
    {
        var turn = Latency.Current ?? Latency.BeginTurn(_time.GetTimestamp());
        turn.ToolCallAt ??= call.ReceivedAt;
        _toolStartedAt = call.ReceivedAt;
        _toolSilenceReported = false;
        var relayTask = _relay!.HandleAsync(call, ct);
        _background.Add(relayTask.ContinueWith(
            t =>
            {
                if (t.IsCompletedSuccessfully)
                {
                    if (!t.Result.IsRunning)
                    {
                        // A synchronous tool is done the moment its result is submitted.
                        _inputs.Writer.TryWrite(new ToolFinishedInput(call.CallId, t.Result.Status));
                    }
                }
                else if (t.IsFaulted)
                {
                    _logger?.LogWarning("tool call {CallId} relay failed: {Reason}", call.CallId, t.Exception?.GetBaseException().Message);
                }
            },
            CancellationToken.None,
            TaskContinuationOptions.None,
            TaskScheduler.Default));
        await Task.CompletedTask.ConfigureAwait(false);
    }

    private async Task HandleToolFinishedAsync(string callId, string status, CancellationToken ct)
    {
        var turn = Latency.Current;
        if (turn is not null && turn.ToolDoneAt is null)
        {
            turn.ToolDoneAt = _time.GetTimestamp();
        }

        await _events!.ReportAsync(VoiceClientEvents.ToolDone, new JsonObject
        {
            ["call_id"] = callId,
            ["status"] = status,
        }, ct).ConfigureAwait(false);
    }

    private async Task StopWordBargeInAsync(string text, CancellationToken ct)
    {
        var turn = Latency.Current ?? Latency.BeginTurn(_time.GetTimestamp());
        turn.BargeInAt = _time.GetTimestamp();
        if (!_turnOpen)
        {
            _turnOpen = true;
            Fsm.OwnerSpeechStarted(text);
        }
        else
        {
            // The onset already went through OwnerSpeechStarted without a stop word; re-arm.
            Fsm.OwnerSpeechStarted(text);
        }

        var outcome = await _bargeIn!.ExecuteAsync(stopWord: true, text, ct).ConfigureAwait(false);
        turn.PlaybackStoppedAt = turn.BargeInAt + (long)(outcome.PlaybackStoppedMs * _time.TimestampFrequency / 1000.0);
        _responseDone = true;
    }

    private async Task HandlePushAsync(SidebandPush push, CancellationToken ct)
    {
        if (push.SessionId is not null && Grant is not null && !string.Equals(push.SessionId, Grant.SessionId, StringComparison.Ordinal))
        {
            _logger?.LogInformation("sideband push {Kind} for another session ({SessionId}) ignored", push.Kind, push.SessionId);
            return;
        }

        lock (_pushesHandled)
        {
            _pushesHandled.Add(push.Kind);
        }

        switch (push.Kind)
        {
            case SidebandPushKinds.Say:
                if (push.Payload["text"]?.GetValue<string>() is { Length: > 0 } text && _leg is { IsOpen: true } leg && !_legSuperseded)
                {
                    await leg.SayAsync(text, ct).ConfigureAwait(false);
                }

                break;

            case SidebandPushKinds.ToolCompleted:
                var callId = push.Payload["call_id"]?.GetValue<string>();
                if (callId is null)
                {
                    break;
                }

                var turn = Latency.Current;
                if (turn is not null)
                {
                    turn.ToolDoneAt = _time.GetTimestamp();
                }

                var error = push.Payload["error"] as JsonObject;
                var completed = await _relay!.CompleteAsync(
                    callId,
                    push.Payload["result"] is { } result ? JsonNode.Parse(result.ToJsonString()) : null,
                    error,
                    ct).ConfigureAwait(false);
                if (completed)
                {
                    await _events!.ReportAsync(VoiceClientEvents.ToolDone, new JsonObject
                    {
                        ["call_id"] = callId,
                        ["status"] = push.Payload["status"]?.GetValue<string>() ?? (error is null ? ToolCallRelayResult.StatusSucceeded : ToolCallRelayResult.StatusFailed),
                    }, ct).ConfigureAwait(false);
                }

                break;

            case SidebandPushKinds.ToolProgress:
                if (Fsm.State == VoiceClientState.ToolRunning)
                {
                    Fsm.AssistantProgress(push.Payload["text"]?.GetValue<string>() ?? "progress");
                }

                break;

            case SidebandPushKinds.LegClosed:
                await HandleLegSupersededAsync("leg_closed:" + (push.Payload["reason"]?.GetValue<string>() ?? "unknown"), ct).ConfigureAwait(false);
                break;

            default:
                // plan_changed / narration_cursor are Cloud Core state the client only logs in track C.
                _logger?.LogInformation("sideband push {Kind} noted", push.Kind);
                break;
        }
    }

    private void HandleDevicesChanged()
    {
        var captureSwitch = _captureSelector.Reconcile(_catalog.List(AudioDirection.Capture));
        if (captureSwitch is not null)
        {
            if (captureSwitch.Immediate)
            {
                _pendingCaptureSwitch = null;
                SwitchCapture(captureSwitch, start: true);
            }
            else
            {
                _pendingCaptureSwitch = captureSwitch;
            }
        }

        var renderSwitch = _renderSelector.Reconcile(_catalog.List(AudioDirection.Render));
        if (renderSwitch is not null)
        {
            if (renderSwitch.Immediate)
            {
                _pendingRenderSwitch = null;
                SwitchRender(renderSwitch, start: true);
            }
            else
            {
                _pendingRenderSwitch = renderSwitch;
            }
        }

        ApplyPendingSwitchesIfIdle();
    }

    private void HandlePlaybackDrained()
    {
        if (_responseDone && Fsm.State == VoiceClientState.AssistantSpeaking)
        {
            Fsm.AssistantStopSpeaking("drained");
        }

        ApplyPendingSwitchesIfIdle();
    }

    private void CheckToolSilence()
    {
        if (Fsm.State != VoiceClientState.ToolRunning || _toolSilenceReported)
        {
            return;
        }

        var reference = Math.Max(_toolStartedAt, _lastAssistantAudioAt);
        if (_time.ElapsedMs(reference) > _options.ToolSilenceBoundMs && !(_playback?.IsPlaying ?? false))
        {
            _toolSilenceReported = true;
            AddDefect($"tool_silence_exceeded:{_options.ToolSilenceBoundMs}ms");
            _audit?.Write("voice_tool_silence", status: "defect", detail: new JsonObject
            {
                ["bound_ms"] = _options.ToolSilenceBoundMs,
                ["running_calls"] = new JsonArray(_relay!.RunningCalls.Select(c => (JsonNode)c).ToArray()),
            }.ToJsonString());
        }
    }

    // ------------------------------------------------------- server verdicts

    private async Task HandleSidebandFaultAsync(SidebandFault fault, CancellationToken ct)
    {
        switch (fault)
        {
            case SidebandFault.StaleLeg:
                await HandleLegSupersededAsync("stale_leg_409", ct).ConfigureAwait(false);
                break;
            case SidebandFault.SessionGone:
                HandleSessionGone("session_gone");
                break;
        }
    }

    /// <summary>
    /// Another client holds the media leg (409, or a leg_closed push): stop everything the
    /// owner could hear or that could reach the provider from here, keep the session, and wait
    /// for the owner to speak to this device again before reclaiming the leg.
    /// </summary>
    private async Task HandleLegSupersededAsync(string reason, CancellationToken ct)
    {
        if (_legSuperseded || _sessionGone)
        {
            return;
        }

        // An open owner turn is left to end naturally (the detector still reports end_of_turn,
        // held by the reporter until the leg is reclaimed); only the media path is cut here.
        _legSuperseded = true;
        LegSupersededCount++;
        _playback?.StopImmediately();
        if (Fsm.State == VoiceClientState.AssistantSpeaking)
        {
            Fsm.AssistantStopSpeaking("leg_superseded");
        }

        _responseDone = true;
        _reporter?.SetOnline(false);
        var leg = _leg;
        if (leg is not null)
        {
            try
            {
                await leg.CloseAsync(ct).ConfigureAwait(false);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                _logger?.LogWarning("closing the superseded leg failed: {Reason}", ex.Message);
            }
        }

        _logger?.LogInformation("media leg superseded ({Reason}); this device is silent until the owner speaks to it again", reason);
        _audit?.Write("voice_leg_superseded", status: "ok", detail: new JsonObject
        {
            ["session_id"] = Grant?.SessionId,
            ["reason"] = reason,
        }.ToJsonString());
    }

    private async Task<bool> TryReclaimLegAsync(string reason, CancellationToken ct)
    {
        try
        {
            var attached = await _sideband.AttachAsync(Grant!.SessionId, _options.ClientKind, _leg?.Transport is { } t && RealtimeContract.Transports.Contains(t) ? t : Grant.Transport, ct).ConfigureAwait(false);
            if (attached is null)
            {
                HandleSessionGone("session_gone_on_reattach");
                return false;
            }

            Grant = attached.Grant;
            await OpenLegAsync(attached.Grant, ct).ConfigureAwait(false);
            _legSuperseded = false;
            ReattachCount++;
            _responseDone = true;
            _reporter?.SetOnline(true);
            _audit?.Write("voice_leg_reclaimed", status: "ok", detail: new JsonObject
            {
                ["session_id"] = Grant.SessionId,
                ["reason"] = reason,
                ["previous_leg"] = attached.PreviousLeg?["client_kind"]?.GetValue<string>(),
                ["pending_pushes"] = attached.PendingSideband.Count,
            }.ToJsonString());
            _logger?.LogInformation("media leg reclaimed ({Reason}); {Pending} queued pushes", reason, attached.PendingSideband.Count);
            EnqueuePushes(attached.PendingSideband);
            if (_reporter is not null)
            {
                await _reporter.FlushAsync(ct).ConfigureAwait(false);
            }

            return true;
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception ex)
        {
            _logger?.LogWarning("reclaiming the media leg failed: {Reason}", ex.Message);
            AddDefect("reattach_failed:" + ex.GetType().Name);
            return false;
        }
    }

    /// <summary>410/404: nothing more can be posted to this session. The loop ends; the host opens a new session.</summary>
    private void HandleSessionGone(string reason)
    {
        if (_sessionGone)
        {
            return;
        }

        _sessionGone = true;
        StopReason = "session_gone";
        _playback?.StopImmediately();
        _logger?.LogWarning("voice session {SessionId} is gone ({Reason}); ending this session", Grant?.SessionId, reason);
        _audit?.Write("voice_session_gone", status: "ok", detail: new JsonObject
        {
            ["session_id"] = Grant?.SessionId,
            ["reason"] = reason,
        }.ToJsonString());
        _inputs.Writer.TryComplete();
    }

    // ------------------------------------------------------------- reconnection

    private async Task HandleDisconnectedAsync(DisconnectedEvent disconnected, CancellationToken ct)
    {
        if (_reconnecting || _legSuperseded || _sessionGone)
        {
            return;
        }

        _reconnecting = true;
        _disconnectedAt = disconnected.ReceivedAt;
        Fsm.NetworkLost(disconnected.Reason);
        _playback?.StopImmediately();
        _reporter?.SetOnline(false);
        _audit?.Write("voice_network_lost", status: "degraded", detail: new JsonObject { ["reason"] = disconnected.Reason }.ToJsonString());
        await _events!.ReportAsync(VoiceClientEvents.NetworkLost, new JsonObject { ["reason"] = disconnected.Reason }, ct).ConfigureAwait(false);
        _background.Add(Task.Run(() => ReconnectAsync(ct), CancellationToken.None));
    }

    private async Task ReconnectAsync(CancellationToken ct)
    {
        for (var attempt = 0; attempt < _options.MaxReconnectAttempts && !ct.IsCancellationRequested; attempt++)
        {
            try
            {
                await Task.Delay(_options.ReconnectBackoff.NextDelay(attempt), _time, ct).ConfigureAwait(false);
                var attached = await _sideband.AttachAsync(Grant!.SessionId, _options.ClientKind, Grant.Transport, ct).ConfigureAwait(false);
                if (attached is null)
                {
                    _logger?.LogWarning("Cloud Core no longer knows session {SessionId}; giving up reconnection", Grant!.SessionId);
                    AddDefect("session_gone_on_reattach");
                    _inputs.Writer.TryWrite(new SidebandFaultInput(SidebandFault.SessionGone));
                    return;
                }

                Grant = attached.Grant;
                await OpenLegAsync(attached.Grant, ct).ConfigureAwait(false);
                EnqueuePushes(attached.PendingSideband);
                _inputs.Writer.TryWrite(new ReconnectedInput(attempt + 1));
                return;
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                return;
            }
            catch (Exception ex)
            {
                _logger?.LogWarning("reconnect attempt {Attempt} failed: {Reason}", attempt + 1, ex.Message);
            }
        }

        AddDefect("reconnect_exhausted");
    }

    private async Task HandleReconnectedAsync(ReconnectedInput reconnected, CancellationToken ct)
    {
        _reconnecting = false;
        ReconnectCount++;
        var outageMs = _time.ElapsedMs(_disconnectedAt);
        Fsm.NetworkRestored($"attempts={reconnected.Attempts}");
        _audit?.Write("voice_network_restored", status: "ok", detail: new JsonObject
        {
            ["outage_ms"] = Math.Round(outageMs, 1),
            ["attempts"] = reconnected.Attempts,
        }.ToJsonString());
        _reporter?.SetOnline(true);
        await _events!.ReportAsync(VoiceClientEvents.NetworkRestored, new JsonObject
        {
            ["outage_ms"] = Math.Round(outageMs, 1),
            ["attempts"] = reconnected.Attempts,
        }, ct).ConfigureAwait(false);
        if (_reporter is not null)
        {
            await _reporter.FlushAsync(ct).ConfigureAwait(false);
        }
    }

    private void AddDefect(string defect)
    {
        lock (_defects)
        {
            _defects.Add(defect);
        }
    }

    private abstract record Input;

    private sealed record FrameInput(AudioFrame Frame) : Input;

    private sealed record ProviderInput(ProviderEvent Event, IMediaLeg Leg) : Input;

    private sealed record PushInput(SidebandPush Push) : Input;

    private sealed record DevicesChangedInput : Input;

    private sealed record PlaybackDrainedInput : Input;

    private sealed record ReconnectedInput(int Attempts) : Input;

    private sealed record ToolFinishedInput(string CallId, string Status) : Input;

    private sealed record SidebandFaultInput(SidebandFault Fault) : Input;
}
