using System.Globalization;
using System.Threading.Channels;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Audio.Processing;
using PagentOS.Companion.Audio.Audio.Vad;
using PagentOS.Companion.Audio.Listening.Spotting;
using PagentOS.Companion.Audio.Timing;
using PagentOS.Companion.Audio.Turn;

namespace PagentOS.Companion.Audio.Listening;

public sealed record DeviceListeningOptions
{
    public AudioFormat Format { get; init; } = AudioFormat.Pcm16Mono24k;

    /// <summary>Clamped to <see cref="DeviceVoiceContract.MaxPreRollMs"/> by the buffer itself.</summary>
    public int PreRollMs { get; init; } = DeviceVoiceContract.DefaultPreRollMs;

    public EnergyVadOptions Vad { get; init; } = new();

    public HesitationGuardOptions Hesitation { get; init; } = new();

    public string? PreferredCaptureDeviceId { get; init; }

    /// <summary>How often mute, the push-to-talk key and the capture's health are looked at.</summary>
    public int TickMs { get; init; } = 50;

    public int MutePollMs { get; init; } = 500;

    /// <summary>After a failed open, how long before the microphone is tried again.</summary>
    public int CaptureRetryMs { get; init; } = 5000;

    /// <summary>Exact-zero audio for this long is reported (a closed privacy shutter looks like this).</summary>
    public int DigitalSilenceMs { get; init; } = 3000;

    /// <summary>Off in tests, which drive <see cref="DeviceListeningService.TickAsync"/> by hand.</summary>
    public bool AutoTick { get; init; } = true;

    /// <summary>Wake-word spotting is attempted every this many feature frames (10 ms each).</summary>
    public int WakeSpotEveryFrames { get; init; } = 5;

    /// <summary>Voiced audio after the wake word needed to treat the rest of the breath as the request.</summary>
    public int MinPostWakeVoicedMs { get; init; } = 150;

    /// <summary>
    /// Lead-in kept before the onset when a wake-word watch starts: the first voiced frame (the
    /// detector needs two to declare an onset). More would put silence at the head of a match
    /// whose start is not free.
    /// </summary>
    public int WakeLeadInMs { get; init; } = 20;

    /// <summary>A wake-word match is accepted only once this much audio exists beyond its end.</summary>
    public int WakeSettleFrames { get; init; } = 10;
}

/// <summary>
/// B47: the device's continuous listener. It owns the real microphone (in the Session
/// Companion, never the Session-0 service) and decides, frame by frame, what may leave the
/// device.
/// <list type="bullet">
/// <item><b>Row 244 - local VAD.</b> Nothing leaves unless the local detector opened an
/// utterance (continuous mode), the owner said the wake word (wake-word mode) or holds the
/// push-to-talk key. Silence between utterances is never forwarded: it sits in the bounded
/// <see cref="PreRollBuffer"/> and is zeroed as it falls out.</item>
/// <item><b>Row 249 - raw audio is never kept.</b> No file, no log line, no audit field
/// carries audio or a transcript. The only buffers are the pre-roll ring and one utterance's
/// segment, both bounded by the contract and zeroed when dropped.</item>
/// <item><b>Rows 242/254/255.</b> The owner's switch and the endpoint's mute state stop the
/// capture stream itself - not a flag in front of it - and the indicator always says which
/// state the microphone is in.</item>
/// <item><b>Rows 241/243/253.</b> The wake word and the offline commands come from an
/// offline <see cref="IKeywordSpotter"/>; push-to-talk from a polled key.</item>
/// </list>
/// Admitted audio goes to at most one <see cref="GatedCaptureTap"/> (the realtime session's
/// view of the microphone). With no session attached - the Cloud Core unreachable - admitted
/// audio is zeroed and dropped; only the offline commands act.
///
/// One loop, like the orchestrator: frames, ticks, switches and device changes are handled in
/// order on one task, which is what makes "muted means nothing is captured" a sequence rather
/// than a race.
/// </summary>
public sealed class DeviceListeningService : IAsyncDisposable
{
    public const string SourceOwnerDevice = "owner_device";
    public const string SourceVoiceCommand = "voice_command";
    public const string SourceRemote = "remote";
    public const string SourceStartup = "startup";

    private readonly DeviceListeningOptions _options;
    private readonly IAudioDeviceCatalog _catalog;
    private readonly IAudioDeviceFactory _devices;
    private readonly IListeningSettingsStore _store;
    private readonly IMicMuteMonitor _mute;
    private readonly IPushToTalkKey? _ptt;
    private readonly IPrivacyIndicator _indicator;
    private readonly IOfflineCommandSink? _commands;
    private readonly DeviceVoiceHealth _health;
    private readonly TimeProvider _time;
    private readonly ILogger? _logger;
    private readonly AuditLog? _audit;
    private readonly IAudioProcessor _processor;
    private readonly Channel<Input> _inputs = Channel.CreateUnbounded<Input>(new UnboundedChannelOptions { SingleReader = true });
    private readonly AudioDeviceSelector _selector;
    private readonly ClientEndOfTurnDetector _detector;
    private readonly PreRollBuffer _preRoll;
    private readonly MfccExtractor _utteranceMfcc;
    private readonly List<float[]> _utteranceFeatures = [];
    private readonly List<double> _utteranceFrameDb = [];
    private readonly MfccExtractor _watchMfcc;
    private readonly List<float[]> _watchFeatures = [];
    private readonly List<SegmentFrame> _watchFrames = [];
    private readonly List<AudioFrame> _postWake = [];

    private IKeywordSpotter _spotter;
    private ListeningSettings _settings;
    private ListeningMode _effectiveMode;
    private IAudioCapture? _capture;
    private string? _selectedDeviceId;
    private DeviceSwitch? _pendingSwitch;
    private int _captureGeneration;
    private long _captureRetryAt;
    private long _lastMutePollAt = long.MinValue;
    private bool? _muted;
    private GatedCaptureTap? _tap;
    private bool _cloudConnected;
    private bool _open;
    private bool _stopping;
    private double _utteranceMs;
    private bool _utteranceOverflow;
    private bool _watching;
    private long _watchSamples;
    private int _featuresAtLastSpot;
    private KeywordHit? _wakeHit;
    private double _postWakeVoicedMs;
    private long _followUpUntil = long.MinValue;
    private long? _pttDownSince;
    private double _zeroRunMs;
    private bool _digitalSilence;
    private string? _indicatorShown;
    private Task? _loop;
    private Task? _ticker;
    private CancellationTokenSource? _cts;
    private Func<AudioProcessingContext> _playbackContext = () => default;
    private long _admitted;
    private long _dropped;

    public DeviceListeningService(
        DeviceListeningOptions options,
        IAudioDeviceCatalog catalog,
        IAudioDeviceFactory devices,
        IListeningSettingsStore settings,
        IMicMuteMonitor mute,
        IPushToTalkKey? pushToTalk,
        IKeywordSpotter spotter,
        IPrivacyIndicator indicator,
        IOfflineCommandSink? commands,
        DeviceVoiceHealth health,
        TimeProvider time,
        ILogger? logger = null,
        AuditLog? audit = null,
        IAudioProcessor? processor = null)
    {
        _options = options;
        _catalog = catalog;
        _devices = devices;
        _store = settings;
        _mute = mute;
        _ptt = pushToTalk;
        _spotter = spotter;
        _indicator = indicator;
        _commands = commands;
        _health = health;
        _time = time;
        _logger = logger;
        _audit = audit;
        _processor = processor ?? new ProcessorChain([new DcBlockerProcessor(), new NoiseGateProcessor()]);
        _selector = new AudioDeviceSelector(AudioDirection.Capture, options.PreferredCaptureDeviceId);
        _detector = new ClientEndOfTurnDetector(new EnergyVad(options.Vad), new HesitationGuard(options.Hesitation), time);
        _preRoll = new PreRollBuffer(options.PreRollMs);
        _utteranceMfcc = new MfccExtractor(options.Format.SampleRate);
        _watchMfcc = new MfccExtractor(options.Format.SampleRate);
        _settings = ListeningSettings.Default;
        _effectiveMode = _settings.Mode;
    }

    /// <summary>The owner's stored choice.</summary>
    public ListeningSettings Settings => _settings;

    /// <summary>The mode actually in force (a wake-word choice with no enrolled wake word runs as push-to-talk).</summary>
    public ListeningMode EffectiveMode => _effectiveMode;

    public bool UtteranceOpen => _open;

    public IAudioCapture? Capture => _capture;

    public int CaptureOpens { get; private set; }

    public PreRollBuffer PreRoll => _preRoll;

    /// <summary>Frames the wake-word watch dropped (and zeroed). Asserted by tests.</summary>
    public long WatchZeroedFrames { get; private set; }

    /// <summary>Admitted frames that had no session to go to (and were zeroed).</summary>
    public long AdmittedButUndelivered { get; private set; }

    public string? IndicatorState => _indicatorShown;

    public IReadOnlyList<string> ProcessorNames
        => _processor is ProcessorChain chain ? chain.Processors.Select(p => p.Name).ToList() : [_processor.Name];

    /// <summary>Whether the assistant is audible right now; the gate raises its onset threshold while it is.</summary>
    public Func<AudioProcessingContext> PlaybackContext
    {
        set => _playbackContext = value ?? (() => default);
    }

    public Task StartAsync(CancellationToken cancellationToken)
    {
        if (_loop is not null)
        {
            throw new InvalidOperationException("listening service already started");
        }

        _cts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        var ct = _cts.Token;
        _settings = _store.Load();
        _effectiveMode = ResolveEffectiveMode(_settings.Mode);
        PublishEngine();
        _catalog.DevicesChanged += OnDevicesChanged;
        // The loop is NOT tied to the caller's token: it ends only by handling its own shutdown
        // input, so a cancelled companion still closes the microphone and turns the indicator
        // off (found by B47's host test: cancellation used to end the loop first, leaving the
        // capture stream running and the indicator saying "listening").
        _loop = Task.Run(() => LoopAsync(CancellationToken.None), CancellationToken.None);
        _inputs.Writer.TryWrite(new TickInput());
        if (_options.AutoTick)
        {
            _ticker = Task.Run(() => TickerAsync(ct), CancellationToken.None);
        }

        _logger?.LogInformation(
            "voice: device listening started; listening={Enabled} mode={Mode} (effective {Effective}); wake word {Wake}; offline commands [{Commands}]",
            _settings.Enabled,
            _settings.Mode.ToWire(),
            _effectiveMode.ToWire(),
            _spotter.IsAvailable(DeviceVoiceContract.WakeWordPhraseId) ? "available" : "not enrolled",
            string.Join(",", _spotter.AvailablePhrases.Where(p => p != DeviceVoiceContract.WakeWordPhraseId).OrderBy(p => p, StringComparer.Ordinal)));
        return DrainAsync();
    }

    /// <summary>Completes once every input enqueued before the call has been handled.</summary>
    public async Task DrainAsync()
    {
        var loop = _loop ?? throw new InvalidOperationException("listening service not started");
        var marker = new DrainInput(new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously));
        if (_inputs.Writer.TryWrite(marker))
        {
            await Task.WhenAny(marker.Completion.Task, loop).ConfigureAwait(false);
        }
    }

    /// <summary>One tick, handled; for tests that run with <see cref="DeviceListeningOptions.AutoTick"/> off.</summary>
    public Task TickAsync()
    {
        _inputs.Writer.TryWrite(new TickInput());
        return DrainAsync();
    }

    /// <summary>
    /// The owner's switch (row 242/254). Turning listening ON is honoured only from the device
    /// itself; <see cref="SourceRemote"/> may only turn it off. Returns whether it applied.
    /// </summary>
    public async Task<bool> SetEnabledAsync(bool enabled, string source)
    {
        var done = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        _inputs.Writer.TryWrite(new EnabledInput(enabled, source, done));
        return await done.Task.ConfigureAwait(false);
    }

    /// <summary>Returns null when applied, or the reason it was refused.</summary>
    public async Task<string?> SetModeAsync(ListeningMode mode)
    {
        var done = new TaskCompletionSource<string?>(TaskCreationOptions.RunContinuationsAsynchronously);
        _inputs.Writer.TryWrite(new ModeInput(mode, done));
        return await done.Task.ConfigureAwait(false);
    }

    public void SetCloudConnected(bool connected) => _inputs.Writer.TryWrite(new CloudInput(connected));

    /// <summary>After an enrollment: the new engine takes over at the next input.</summary>
    public void ReplaceSpotter(IKeywordSpotter spotter) => _inputs.Writer.TryWrite(new SpotterInput(spotter));

    public async ValueTask DisposeAsync()
    {
        if (_cts is null)
        {
            return;
        }

        _catalog.DevicesChanged -= OnDevicesChanged;
        _cts.Cancel();
        var stopped = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        _inputs.Writer.TryWrite(new ShutdownInput(stopped));
        _inputs.Writer.TryComplete();
        if (_loop is not null)
        {
            await Task.WhenAny(stopped.Task, _loop).ConfigureAwait(false);
        }

        foreach (var task in new[] { _loop, _ticker })
        {
            if (task is null)
            {
                continue;
            }

            try
            {
                await task.ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
            }
        }

        // Frames that arrived after the shutdown was queued never reach a handler; they are
        // zeroed here rather than left for the collector.
        while (_inputs.Reader.TryRead(out var leftover))
        {
            if (leftover is FrameInput frame)
            {
                Array.Clear(frame.Frame.Pcm16);
            }
        }

        _cts.Dispose();
        _cts = null;
    }

    // ------------------------------------------------------------------ tap plumbing

    internal GatedCaptureTap OpenTap(string deviceId, AudioFormat format)
    {
        if (format != _options.Format)
        {
            throw new NotSupportedException($"the device listener captures {_options.Format}; {format} was asked for");
        }

        return new GatedCaptureTap(this, deviceId, format);
    }

    internal void Attach(GatedCaptureTap tap) => _inputs.Writer.TryWrite(new AttachInput(tap));

    internal void Detach(GatedCaptureTap tap) => _inputs.Writer.TryWrite(new DetachInput(tap));

    internal void ObserveTranscript(string text) => _inputs.Writer.TryWrite(new TranscriptInput(text));

    // ------------------------------------------------------------------ the loop

    private async Task TickerAsync(CancellationToken ct)
    {
        try
        {
            while (!ct.IsCancellationRequested)
            {
                await Task.Delay(TimeSpan.FromMilliseconds(_options.TickMs), _time, ct).ConfigureAwait(false);
                _inputs.Writer.TryWrite(new TickInput());
            }
        }
        catch (OperationCanceledException)
        {
        }
    }

    private async Task LoopAsync(CancellationToken ct)
    {
        await foreach (var input in _inputs.Reader.ReadAllAsync(ct).ConfigureAwait(false))
        {
            try
            {
                switch (input)
                {
                    case FrameInput frame:
                        HandleFrame(frame);
                        break;
                    case TickInput:
                        HandleTick();
                        break;
                    case EnabledInput enabled:
                        enabled.Done.TrySetResult(ApplyEnabled(enabled.Enabled, enabled.Source));
                        break;
                    case ModeInput mode:
                        mode.Done.TrySetResult(ApplyMode(mode.Mode));
                        break;
                    case CloudInput cloud:
                        _cloudConnected = cloud.Connected;
                        UpdateIndicator();
                        break;
                    case SpotterInput spotter:
                        _spotter = spotter.Spotter;
                        _effectiveMode = ResolveEffectiveMode(_settings.Mode);
                        PublishEngine();
                        HandleTick();
                        break;
                    case AttachInput attach:
                        _tap = attach.Tap;
                        if (_open)
                        {
                            attach.Tap.Deliver(new UtteranceStarted(_time.GetTimestamp(), "attached_mid_utterance"));
                        }

                        UpdateIndicator();
                        break;
                    case DetachInput detach:
                        if (ReferenceEquals(_tap, detach.Tap))
                        {
                            _tap = null;
                            UpdateIndicator();
                        }

                        break;
                    case TranscriptInput transcript:
                        _detector.ObserveTranscript(transcript.Text);
                        break;
                    case DevicesChangedInput:
                        HandleDevicesChanged();
                        break;
                    case ShutdownInput shutdown:
                        StopCapture("shutdown");
                        ShowIndicator(DeviceVoiceContract.IndicatorOff, "stopped");
                        _health.SetListening(false, _settings.Mode.ToWire(), DeviceVoiceContract.IndicatorOff);
                        shutdown.Done.TrySetResult();
                        return;
                    case DrainInput drain:
                        drain.Completion.TrySetResult();
                        break;
                }
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                throw;
            }
            catch (Exception ex)
            {
                // One bad input must not end listening; the class of the fault is reported.
                _logger?.LogError(ex, "voice: listening input {Input} failed", input.GetType().Name);
                _health.CountRestart("listening_input:" + ex.GetType().Name);
            }
        }
    }

    // ------------------------------------------------------------------ frames

    private void OnFrame(AudioFrame frame, int generation) => _inputs.Writer.TryWrite(new FrameInput(frame, generation));

    private void HandleFrame(FrameInput input)
    {
        var frame = input.Frame;
        if (input.Generation != _captureGeneration || _capture is null || !CaptureWanted())
        {
            // A frame from a stream that has since been stopped (mute, off, device switch):
            // it was captured before the stop landed and it goes nowhere.
            Zero(frame);
            return;
        }

        TrackDigitalSilence(frame);
        var context = _playbackContext();
        _processor.Process(frame.Samples, frame.Format, in context);
        var turn = _detector.Process(frame, in context);
        var voiced = _detector.LastDecision.Voiced;

        switch (_effectiveMode)
        {
            case ListeningMode.Continuous:
                if (turn is { Kind: TurnEventKind.SpeechStarted } && !_open)
                {
                    Open("vad_onset", _preRoll.TakeAll());
                }

                if (_open)
                {
                    Forward(frame);
                    if (turn is { Kind: TurnEventKind.SpeechEnded })
                    {
                        Close(turn);
                    }
                }
                else
                {
                    _preRoll.Push(frame);
                }

                break;

            case ListeningMode.WakeWord:
                HandleWakeWordFrame(frame, turn, voiced, context.PlaybackActive);
                break;

            case ListeningMode.PushToTalk:
                // The key, not the detector, bounds the turn: a pause mid-sentence while the
                // owner still holds the key is not the end of what they meant to say.
                if (_open)
                {
                    Forward(frame);
                }
                else
                {
                    _preRoll.Push(frame);
                }

                break;
        }
    }

    private void HandleWakeWordFrame(AudioFrame frame, TurnEvent? turn, bool voiced, bool assistantAudible)
    {
        if (_open)
        {
            Forward(frame);
            if (turn is { Kind: TurnEventKind.SpeechEnded })
            {
                Close(turn);
            }

            return;
        }

        if (_watching)
        {
            WatchFrame(frame, turn, voiced);
            return;
        }

        if (turn is { Kind: TurnEventKind.SpeechStarted })
        {
            if (_time.GetTimestamp() < _followUpUntil || assistantAudible)
            {
                // The owner is still in the conversation the wake word opened - or is
                // talking over the answer to it, which is a barge-in, not a new request.
                Open("follow_up", _preRoll.TakeAll());
                Forward(frame);
                return;
            }

            BeginWatch(frame);
            return;
        }

        _preRoll.Push(frame);
    }

    private void BeginWatch(AudioFrame onsetFrame)
    {
        _watching = true;
        _wakeHit = null;
        _postWake.Clear();
        _postWakeVoicedMs = 0;
        _watchSamples = 0;
        _featuresAtLastSpot = 0;
        _watchMfcc.Reset();
        _watchFeatures.Clear();

        // Only the lead-in just before the onset joins the segment; the rest of the ring is
        // older room tone and is dropped here.
        var lead = _preRoll.TakeAll();
        var keepFrom = lead.Count;
        double kept = 0;
        while (keepFrom > 0 && kept < _options.WakeLeadInMs)
        {
            keepFrom--;
            kept += lead[keepFrom].DurationMs;
        }

        for (var i = 0; i < lead.Count; i++)
        {
            if (i < keepFrom)
            {
                ZeroWatch(lead[i]);
            }
            else
            {
                AddWatchFrame(lead[i], voiced: false);
            }
        }

        AddWatchFrame(onsetFrame, voiced: true);
        TrySpotWake(force: false);
    }

    private void WatchFrame(AudioFrame frame, TurnEvent? turn, bool voiced)
    {
        if (_wakeHit is null)
        {
            AddWatchFrame(frame, voiced);
            TrySpotWake(force: turn is { Kind: TurnEventKind.SpeechEnded });
            if (_wakeHit is not null)
            {
                MaybeOpenAfterWake(turn);
                return;
            }

            if (turn is { Kind: TurnEventKind.SpeechEnded } || SegmentMs() > DeviceVoiceContract.MaxSegmentMs)
            {
                // Not addressed to the device: nothing of it leaves, nothing of it stays.
                EndWatch();
            }

            return;
        }

        _postWake.Add(frame);
        if (voiced)
        {
            _postWakeVoicedMs += frame.DurationMs;
        }

        MaybeOpenAfterWake(turn);
    }

    private void MaybeOpenAfterWake(TurnEvent? turn)
    {
        if (_postWakeVoicedMs >= _options.MinPostWakeVoicedMs)
        {
            var request = _postWake.ToList();
            _postWake.Clear();
            ResetWatch();
            Open("wake_word", request);
            if (turn is { Kind: TurnEventKind.SpeechEnded })
            {
                Close(turn);
            }

            return;
        }

        if (turn is { Kind: TurnEventKind.SpeechEnded })
        {
            // The wake word on its own: the request comes in the next breath, inside the
            // follow-up window that the hit already opened.
            EndWatch();
        }
    }

    private void TrySpotWake(bool force)
    {
        if (_wakeHit is not null || _watchFeatures.Count == 0)
        {
            return;
        }

        if (!force && _watchFeatures.Count - _featuresAtLastSpot < _options.WakeSpotEveryFrames)
        {
            return;
        }

        _featuresAtLastSpot = _watchFeatures.Count;
        var hit = _spotter.SpotPrefix(_watchFeatures, DeviceVoiceContract.WakeWordPhraseId);
        if (hit is null)
        {
            return;
        }

        if (!force && hit.EndFrame > _watchFeatures.Count - 1 - _options.WakeSettleFrames)
        {
            // The best end is at the edge of what has been heard: the phrase may not be over,
            // and accepting now would send its tail as the request. Wait for more audio.
            return;
        }

        _wakeHit = hit;
        _followUpUntil = _time.GetTimestamp() + MsToTicks(DeviceVoiceContract.FollowUpWindowMs);
        var hop = _options.Format.SampleRate * MfccExtractor.HopMs / 1000;
        var frameLength = _options.Format.SampleRate * MfccExtractor.FrameMs / 1000;
        var wakeEndsAtSample = (long)hit.EndFrame * hop + frameLength;
        foreach (var segment in _watchFrames)
        {
            if (segment.EndSample > wakeEndsAtSample)
            {
                // What the owner said after the wake word is the request; keep it.
                _postWake.Add(segment.Frame);
                if (segment.Voiced)
                {
                    _postWakeVoicedMs += segment.Frame.DurationMs;
                }
            }
            else
            {
                // The wake word itself never leaves the device.
                ZeroWatch(segment.Frame);
            }
        }

        _watchFrames.Clear();
        _logger?.LogInformation("voice: wake word heard (distance {Distance:F2} <= {Threshold:F2})", hit.Distance, hit.Threshold);
        _audit?.Write("voice_wake_word", status: "ok", detail: string.Create(
            CultureInfo.InvariantCulture,
            $"distance={hit.Distance:F3}; threshold={hit.Threshold:F3}"));
    }

    private void AddWatchFrame(AudioFrame frame, bool voiced)
    {
        _watchSamples += frame.SampleCount;
        _watchFrames.Add(new SegmentFrame(frame, voiced, _watchSamples));
        _watchFeatures.AddRange(_watchMfcc.Append(frame.Samples));
    }

    private double SegmentMs() => _options.Format.MsForSamples((int)Math.Min(int.MaxValue, _watchSamples));

    private void EndWatch()
    {
        foreach (var segment in _watchFrames)
        {
            ZeroWatch(segment.Frame);
        }

        foreach (var frame in _postWake)
        {
            ZeroWatch(frame);
        }

        _postWake.Clear();
        ResetWatch();
    }

    private void ResetWatch()
    {
        _watchFrames.Clear();
        _watchFeatures.Clear();
        _watchMfcc.Reset();
        _watching = false;
        _wakeHit = null;
        _postWakeVoicedMs = 0;
        _watchSamples = 0;
        _featuresAtLastSpot = 0;
    }

    private void ZeroWatch(AudioFrame frame)
    {
        Array.Clear(frame.Pcm16);
        WatchZeroedFrames++;
    }

    // ------------------------------------------------------------------ utterances

    private void Open(string reason, IReadOnlyList<AudioFrame> lead)
    {
        _open = true;
        _utteranceMs = 0;
        _utteranceOverflow = false;
        _utteranceMfcc.Reset();
        _utteranceFeatures.Clear();
        _utteranceFrameDb.Clear();
        _health.CountUtterance();
        _tap?.Deliver(new UtteranceStarted(_time.GetTimestamp(), reason));
        foreach (var frame in lead)
        {
            Forward(frame);
        }

        UpdateIndicator();
    }

    private void Forward(AudioFrame frame)
    {
        if (!_utteranceOverflow)
        {
            _utteranceFeatures.AddRange(_utteranceMfcc.Append(frame.Samples));
            _utteranceFrameDb.Add(AudioEnergy.Dbfs(frame.Samples));
            _utteranceMs += frame.DurationMs;
            if (_utteranceMs > DeviceVoiceContract.MaxSegmentMs)
            {
                // Longer than any offline command: it is a request for the Cloud Core, and the
                // spotter lets go of it.
                _utteranceOverflow = true;
                _utteranceFeatures.Clear();
                _utteranceFrameDb.Clear();
            }
        }

        var tap = _tap;
        if (tap is { Started: true } && _cloudConnected)
        {
            tap.Deliver(frame);
            _admitted++;
        }
        else
        {
            Zero(frame);
            AdmittedButUndelivered++;
        }

        FlushCounters();
    }

    private void Close(TurnEvent turn)
    {
        if (!_open)
        {
            return;
        }

        _open = false;
        _tap?.Deliver(new UtteranceEnded(_time.GetTimestamp(), turn));
        FlushCounters(force: true);
        if (_effectiveMode == ListeningMode.WakeWord)
        {
            _followUpUntil = _time.GetTimestamp() + MsToTicks(DeviceVoiceContract.FollowUpWindowMs);
        }

        if (!_utteranceOverflow && _utteranceFeatures.Count > 0)
        {
            var spoken = SpokenFeatures();
            if (spoken.Count > 0)
            {
                ClassifyOfflineCommand(spoken);
            }
        }

        _utteranceFeatures.Clear();
        _utteranceFrameDb.Clear();
        _utteranceMfcc.Reset();
        if (!_stopping)
        {
            ApplyPendingSwitch();
        }

        UpdateIndicator();
    }

    /// <summary>
    /// The utterance's features without the pre-roll and the trailing silence, trimmed by the
    /// same energy rule enrollment uses - a template is the phrase alone, so the utterance it
    /// is compared with must be too.
    /// </summary>
    private List<float[]> SpokenFeatures()
    {
        if (_utteranceFrameDb.Count == 0)
        {
            return [];
        }

        var peak = _utteranceFrameDb.Max();
        var floor = Math.Max(KeywordEnrollment.AbsoluteFloorDbfs, peak - KeywordEnrollment.TrimBelowPeakDb);
        var first = _utteranceFrameDb.FindIndex(db => db >= floor);
        var last = _utteranceFrameDb.FindLastIndex(db => db >= floor);
        if (first < 0 || peak < KeywordEnrollment.AbsoluteFloorDbfs)
        {
            return [];
        }

        var frameSamples = _options.Format.SamplesForMs(20);
        var hop = _options.Format.SampleRate * MfccExtractor.HopMs / 1000;
        var length = _options.Format.SampleRate * MfccExtractor.FrameMs / 1000;
        long from = (long)first * frameSamples;
        long to = (long)(last + 1) * frameSamples;
        var result = new List<float[]>();
        for (var k = 0; k < _utteranceFeatures.Count; k++)
        {
            long start = (long)k * hop;
            if (start >= from && start + length <= to + hop)
            {
                result.Add(_utteranceFeatures[k]);
            }
        }

        return result;
    }

    private TurnEvent Synthetic(string reason) => new(TurnEventKind.SpeechEnded, _time.GetTimestamp(), 0, 0, 0, reason);

    // ------------------------------------------------------------------ offline commands

    private void ClassifyOfflineCommand(IReadOnlyList<float[]> features)
    {
        var allowed = new List<string>();
        foreach (var rule in DeviceVoiceContract.OfflineCommands)
        {
            if (!_spotter.IsAvailable(rule.Id))
            {
                continue;
            }

            if (rule.Acts == DeviceVoiceContract.ActsOfflineOnly && _cloudConnected)
            {
                // The same words went to the Cloud Core; it acts, the device does not.
                continue;
            }

            if (rule.Id != DeviceVoiceContract.CommandListeningOff && _commands is null)
            {
                continue;
            }

            if (rule.Requires == DeviceVoiceContract.RequiresAlarmRinging && _commands?.AlarmRinging != true)
            {
                continue;
            }

            allowed.Add(rule.Id);
        }

        if (allowed.Count == 0)
        {
            return;
        }

        var hit = _spotter.Classify(features, allowed);
        if (hit is null)
        {
            return;
        }

        OfflineCommandOutcome outcome;
        if (hit.PhraseId == DeviceVoiceContract.CommandListeningOff)
        {
            var applied = ApplyEnabled(false, SourceVoiceCommand);
            outcome = new OfflineCommandOutcome(applied, applied ? "listening_off" : "already_off");
        }
        else
        {
            try
            {
                outcome = _commands!.Execute(hit.PhraseId);
            }
            catch (Exception ex)
            {
                outcome = new OfflineCommandOutcome(false, "failed:" + ex.GetType().Name);
            }
        }

        _health.RecordOfflineCommand(hit.PhraseId, outcome.Executed, outcome.Detail);
        _logger?.LogInformation(
            "voice: offline command {Command} {Outcome} ({Detail}); cloud_connected={Cloud}",
            hit.PhraseId,
            outcome.Executed ? "executed" : "refused",
            outcome.Detail,
            _cloudConnected);
        _audit?.Write(
            "voice_offline_command",
            status: outcome.Executed ? "succeeded" : "failed",
            detail: string.Create(
                CultureInfo.InvariantCulture,
                $"command={hit.PhraseId}; detail={outcome.Detail}; distance={hit.Distance:F3}; threshold={hit.Threshold:F3}; cloud_connected={_cloudConnected}"));
    }

    // ------------------------------------------------------------------ switches

    private bool ApplyEnabled(bool enabled, string source)
    {
        if (enabled && source == SourceRemote && !DeviceVoiceContract.RemoteEnableAllowed)
        {
            _logger?.LogWarning("voice: a remote request to turn listening ON was refused; only the owner at the device can");
            _audit?.Write("voice_listening_toggle", status: "failed", detail: "enabled=true; source=remote; refused=remote_enable_not_allowed");
            return false;
        }

        if (_settings.Enabled == enabled)
        {
            return false;
        }

        _settings = _settings with { Enabled = enabled };
        _store.Save(_settings);
        _audit?.Write("voice_listening_toggle", status: "succeeded", detail: $"enabled={enabled}; source={source}");
        _logger?.LogInformation("voice: listening turned {State} ({Source})", enabled ? "ON" : "OFF", source);
        if (!enabled)
        {
            StopCapture("listening_off");
            _followUpUntil = long.MinValue;
        }

        HandleTick();
        return true;
    }

    private string? ApplyMode(ListeningMode mode)
    {
        if (mode == ListeningMode.WakeWord && !_spotter.IsAvailable(DeviceVoiceContract.WakeWordPhraseId))
        {
            return "wake_word_unavailable: " + (_spotter.UnavailableReason ?? "no wake word is enrolled");
        }

        if (_open)
        {
            Close(Synthetic("mode_changed"));
        }

        EndWatch();
        _settings = _settings with { Mode = mode };
        _store.Save(_settings);
        _effectiveMode = ResolveEffectiveMode(mode);
        _followUpUntil = long.MinValue;
        _pttDownSince = null;
        if (_effectiveMode == ListeningMode.PushToTalk)
        {
            // Push-to-talk captures nothing between presses.
            StopCapture("push_to_talk_idle");
        }

        _audit?.Write("voice_listening_mode", status: "succeeded", detail: $"mode={mode.ToWire()}; effective={_effectiveMode.ToWire()}");
        PublishEngine();
        HandleTick();
        return null;
    }

    private ListeningMode ResolveEffectiveMode(ListeningMode stored)
        => stored == ListeningMode.WakeWord && !_spotter.IsAvailable(DeviceVoiceContract.WakeWordPhraseId)
            ? ListeningMode.PushToTalk
            : stored;

    private void PublishEngine()
    {
        var wake = _spotter.IsAvailable(DeviceVoiceContract.WakeWordPhraseId);
        _health.SetEngine(
            wake,
            wake ? null : (_spotter.UnavailableReason ?? "no wake word is enrolled"),
            _spotter.AvailablePhrases.Where(p => p != DeviceVoiceContract.WakeWordPhraseId).OrderBy(p => p, StringComparer.Ordinal).ToList(),
            _spotter.AvailablePhrases.Any(p => p != DeviceVoiceContract.WakeWordPhraseId) ? null : (_spotter.UnavailableReason ?? "no offline command is enrolled"),
            _ptt?.Name);
        if (_settings.Mode == ListeningMode.WakeWord && !wake)
        {
            _health.SetState(_health.State, "wake_word_unavailable");
        }
    }

    // ------------------------------------------------------------------ ticks

    private void HandleTick()
    {
        var now = _time.GetTimestamp();
        if (!_settings.Enabled)
        {
            StopCapture("listening_off");
            PublishMicrophone();
            UpdateIndicator();
            return;
        }

        PollMute(now);
        if (_muted == true)
        {
            StopCapture("mic_muted");
            PublishMicrophone();
            UpdateIndicator();
            return;
        }

        if (_effectiveMode == ListeningMode.PushToTalk)
        {
            HandlePushToTalk(now);
        }
        else
        {
            EnsureCapture(now);
        }

        PublishMicrophone();
        UpdateIndicator();
    }

    private void HandlePushToTalk(long now)
    {
        var down = _ptt?.IsDown == true;
        if (down)
        {
            _pttDownSince ??= now;
            EnsureCapture(now);
            if (!_open && _capture is not null && _time.ElapsedMs(_pttDownSince.Value, now) >= DeviceVoiceContract.PushToTalkMinHoldMs)
            {
                Open("push_to_talk", _preRoll.TakeAll());
            }

            return;
        }

        if (_pttDownSince is null)
        {
            if (_capture is not null)
            {
                StopCapture("push_to_talk_idle");
            }

            return;
        }

        _pttDownSince = null;
        if (_open)
        {
            Close(Synthetic("push_to_talk_release"));
        }

        StopCapture("push_to_talk_idle");
    }

    private void PollMute(long now)
    {
        if (_lastMutePollAt != long.MinValue && _time.ElapsedMs(_lastMutePollAt, now) < _options.MutePollMs)
        {
            return;
        }

        _lastMutePollAt = now;
        var deviceId = _capture?.DeviceId ?? _selectedDeviceId ?? _selector.Choose(_catalog.List(AudioDirection.Capture))?.DeviceId;
        bool? muted;
        try
        {
            muted = deviceId is null ? null : _mute.IsMuted(deviceId);
        }
        catch (Exception ex)
        {
            _logger?.LogDebug("voice: mute state unreadable: {Reason}", ex.Message);
            muted = null;
        }

        if (muted != _muted)
        {
            _logger?.LogInformation("voice: microphone mute is now {State}", muted switch { true => "ON", false => "off", _ => "unknown" });
            _audit?.Write("voice_mic_mute", status: "ok", detail: $"muted={(muted is null ? "unknown" : muted.Value ? "true" : "false")}");
        }

        _muted = muted;
    }

    private bool CaptureWanted()
        => _settings.Enabled
           && _muted != true
           && (_effectiveMode != ListeningMode.PushToTalk || _pttDownSince is not null);

    private void EnsureCapture(long now)
    {
        if (_capture is not null || now < _captureRetryAt)
        {
            return;
        }

        var choice = _selector.Choose(_catalog.List(AudioDirection.Capture));
        if (choice is null)
        {
            _captureRetryAt = now + MsToTicks(_options.CaptureRetryMs);
            _health.SetState(_health.State, "no_capture_device");
            return;
        }

        OpenCapture(choice.DeviceId);
        if (_capture is null)
        {
            _captureRetryAt = now + MsToTicks(_options.CaptureRetryMs);
        }
    }

    private void OpenCapture(string deviceId)
    {
        IAudioCapture? capture = null;
        try
        {
            capture = _devices.OpenCapture(deviceId, _options.Format);
            var generation = ++_captureGeneration;
            capture.FrameCaptured += frame => OnFrame(frame, generation);
            capture.Start();
            _capture = capture;
            _selectedDeviceId = deviceId;
            _selector.MarkActive(deviceId);
            CaptureOpens++;
            _detector.Reset();
            _zeroRunMs = 0;
            _digitalSilence = false;
            _logger?.LogInformation("voice: microphone open (capture #{Count})", CaptureOpens);
            _audit?.Write("voice_capture_open", status: "ok", detail: $"mode={_effectiveMode.ToWire()}");
        }
        catch (Exception ex)
        {
            capture?.Dispose();
            _logger?.LogWarning("voice: the microphone could not be opened: {Reason}", ex.Message);
            _health.SetState(_health.State, "capture_open_failed:" + ex.GetType().Name);
        }
    }

    private void StopCapture(string reason)
    {
        _stopping = true;
        try
        {
            if (_open)
            {
                Close(Synthetic(reason));
            }
        }
        finally
        {
            _stopping = false;
        }

        EndWatch();
        _preRoll.Discard();
        if (_pendingSwitch is { } pending)
        {
            // The stream is going away anyway; the next open simply uses the better device.
            _pendingSwitch = null;
            _selectedDeviceId = pending.ToDeviceId;
        }

        if (_capture is null)
        {
            return;
        }

        var capture = _capture;
        _capture = null;
        _captureGeneration++;
        try
        {
            capture.Stop();
        }
        finally
        {
            capture.Dispose();
        }

        _detector.Reset();
        _logger?.LogInformation("voice: microphone closed ({Reason})", reason);
        _audit?.Write("voice_capture_closed", status: "ok", detail: $"reason={reason}");
    }

    private void HandleDevicesChanged()
    {
        var change = _selector.Reconcile(_catalog.List(AudioDirection.Capture));
        if (change is null)
        {
            if (_capture is not null && !_catalog.List(AudioDirection.Capture).Any(d => d.Id == _capture.DeviceId))
            {
                StopCapture("device_removed");
                _selector.MarkActive(string.Empty);
                _health.SetState(_health.State, "no_capture_device");
            }

            PublishMicrophone();
            UpdateIndicator();
            return;
        }

        if (_capture is null)
        {
            _selectedDeviceId = change.ToDeviceId;
            return;
        }

        if (change.Immediate || !_open)
        {
            SwitchTo(change);
        }
        else
        {
            _pendingSwitch = change;
        }

        PublishMicrophone();
        UpdateIndicator();
    }

    private void ApplyPendingSwitch()
    {
        if (_pendingSwitch is { } change && !_open)
        {
            _pendingSwitch = null;
            SwitchTo(change);
        }
    }

    private void SwitchTo(DeviceSwitch change)
    {
        StopCapture(change.Immediate ? "device_removed" : "device_switch");
        _selectedDeviceId = change.ToDeviceId;
        if (CaptureWanted())
        {
            OpenCapture(change.ToDeviceId);
        }

        _audit?.Write("voice_capture_switched", status: "ok", detail: $"reason={change.Reason}; immediate={change.Immediate}");
    }

    // ------------------------------------------------------------------ indicator + health

    private void UpdateIndicator()
    {
        string state;
        string detail;
        if (!_settings.Enabled)
        {
            (state, detail) = (DeviceVoiceContract.IndicatorOff, "Dinleme kapalı");
        }
        else if (_muted == true)
        {
            (state, detail) = (DeviceVoiceContract.IndicatorMuted, "Mikrofon donanımda kapalı");
        }
        else if (_open && _cloudConnected && _tap is { Started: true })
        {
            (state, detail) = (DeviceVoiceContract.IndicatorSending, "Konuşma Cloud Core'a gidiyor");
        }
        else
        {
            (state, detail) = _effectiveMode switch
            {
                ListeningMode.WakeWord => (DeviceVoiceContract.IndicatorWakeWord, "Uyandırma sözcüğü bekleniyor (ses cihazda kalır)"),
                ListeningMode.PushToTalk => (DeviceVoiceContract.IndicatorPushToTalk, _capture is null ? "Bas-konuş: mikrofon kapalı" : "Bas-konuş: tuş basılı"),
                _ => (DeviceVoiceContract.IndicatorListening, "Dinliyor (sessizlik cihazda kalır)"),
            };
        }

        if (!_cloudConnected && state != DeviceVoiceContract.IndicatorOff && state != DeviceVoiceContract.IndicatorMuted)
        {
            detail += " · Cloud Core'a bağlı değil: yalnızca çevrimdışı komutlar";
        }

        _health.SetListening(_settings.Enabled, _settings.Mode.ToWire(), state);
        ShowIndicator(state, detail);
    }

    private void ShowIndicator(string state, string detail)
    {
        var key = state + "|" + detail;
        if (key == _indicatorShown + "|" + _indicatorDetail)
        {
            return;
        }

        _indicatorShown = state;
        _indicatorDetail = detail;
        try
        {
            _indicator.Show(state, detail);
        }
        catch (Exception ex)
        {
            _logger?.LogWarning("voice: the privacy indicator could not be updated: {Reason}", ex.Message);
        }
    }

    private string? _indicatorDetail;

    private void PublishMicrophone()
    {
        var present = _capture is not null || _catalog.List(AudioDirection.Capture).Count > 0;
        _health.SetMicrophone(present, _capture is not null, _muted, _digitalSilence);
    }

    private void TrackDigitalSilence(AudioFrame frame)
    {
        var allZero = true;
        foreach (var sample in frame.Samples)
        {
            if (sample != 0)
            {
                allZero = false;
                break;
            }
        }

        if (!allZero)
        {
            _zeroRunMs = 0;
            if (_digitalSilence)
            {
                _digitalSilence = false;
                PublishMicrophone();
            }

            return;
        }

        _zeroRunMs += frame.DurationMs;
        if (!_digitalSilence && _zeroRunMs >= _options.DigitalSilenceMs)
        {
            _digitalSilence = true;
            _logger?.LogWarning("voice: the microphone has delivered exact silence for {Ms:F0} ms (a privacy shutter or a muted driver looks like this)", _zeroRunMs);
            PublishMicrophone();
        }
    }

    private void Zero(AudioFrame frame)
    {
        Array.Clear(frame.Pcm16);
        _dropped++;
        FlushCounters();
    }

    private void FlushCounters(bool force = false)
    {
        if ((force && _admitted + _dropped > 0) || _admitted + _dropped >= 50)
        {
            _health.CountFrames(_admitted, _dropped);
            _admitted = 0;
            _dropped = 0;
        }
    }

    private void OnDevicesChanged() => _inputs.Writer.TryWrite(new DevicesChangedInput());

    private long MsToTicks(double ms) => (long)(ms * _time.TimestampFrequency / 1000.0);

    private readonly record struct SegmentFrame(AudioFrame Frame, bool Voiced, long EndSample);

    private abstract record Input;

    private sealed record FrameInput(AudioFrame Frame, int Generation) : Input;

    private sealed record TickInput : Input;

    private sealed record EnabledInput(bool Enabled, string Source, TaskCompletionSource<bool> Done) : Input;

    private sealed record ModeInput(ListeningMode Mode, TaskCompletionSource<string?> Done) : Input;

    private sealed record CloudInput(bool Connected) : Input;

    private sealed record SpotterInput(IKeywordSpotter Spotter) : Input;

    private sealed record AttachInput(GatedCaptureTap Tap) : Input;

    private sealed record DetachInput(GatedCaptureTap Tap) : Input;

    private sealed record TranscriptInput(string Text) : Input;

    private sealed record DevicesChangedInput : Input;

    private sealed record ShutdownInput(TaskCompletionSource Done) : Input;

    private sealed record DrainInput(TaskCompletionSource Completion) : Input;
}
