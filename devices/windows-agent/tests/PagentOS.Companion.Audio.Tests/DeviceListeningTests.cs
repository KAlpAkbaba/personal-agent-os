using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Fakes;
using PagentOS.Companion.Audio.Listening;
using PagentOS.Companion.Audio.Listening.Spotting;
using PagentOS.Companion.Audio.Tests.Support;
using PagentOS.Companion.Audio.Timing;
using PagentOS.Companion.Audio.Turn;
using Xunit;

namespace PagentOS.Companion.Audio.Tests;

/// <summary>
/// B47: the device listener against fake audio. Nothing here opens a microphone; every frame is
/// synthetic and every decision is observed through the tap the realtime session would use.
/// </summary>
public sealed class DeviceListeningTests : IAsyncDisposable
{
    private readonly ManualTimeProvider _time = new();
    private readonly FakeDeviceCatalog _catalog = new();
    private readonly FakeDeviceFactory _devices;
    private readonly InMemoryListeningSettingsStore _store;
    private readonly FakeMicMuteMonitor _mute = new();
    private readonly FakePushToTalkKey _ptt = new("test-key");
    private readonly RecordingPrivacyIndicator _indicator = new();
    private readonly FakeCommandSink _commands = new();
    private readonly DeviceVoiceHealth _health;
    private readonly List<object> _received = [];
    private DeviceListeningService? _service;
    private GatedCaptureTap? _tap;

    public DeviceListeningTests()
    {
        _devices = new FakeDeviceFactory(_time);
        _catalog.Set(FakeDeviceCatalog.LaptopMic(), FakeDeviceCatalog.LaptopSpeakers());
        _store = new InMemoryListeningSettingsStore();
        _health = new DeviceVoiceHealth(_time);
    }

    private FakeCapture? Mic => _devices.CurrentCapture;

    private IReadOnlyList<AudioFrame> Delivered
    {
        get
        {
            lock (_received)
            {
                return _received.OfType<AudioFrame>().ToList();
            }
        }
    }

    private IReadOnlyList<UtteranceBoundary> Boundaries
    {
        get
        {
            lock (_received)
            {
                return _received.OfType<UtteranceBoundary>().ToList();
            }
        }
    }

    public async ValueTask DisposeAsync()
    {
        if (_service is not null)
        {
            await _service.DisposeAsync();
        }
    }

    private async Task<DeviceListeningService> StartAsync(
        ListeningSettings? settings = null,
        IKeywordSpotter? spotter = null,
        bool attach = true,
        bool cloud = true,
        bool withCommands = true)
    {
        if (settings is not null)
        {
            _store.Save(settings);
        }

        _service = new DeviceListeningService(
            new DeviceListeningOptions { AutoTick = false },
            _catalog,
            _devices,
            _store,
            _mute,
            _ptt,
            spotter ?? new NoKeywordSpotter("test: nothing enrolled", Phrases.Format.SampleRate),
            _indicator,
            withCommands ? _commands : null,
            _health,
            _time);
        await _service.StartAsync(CancellationToken.None);
        if (attach)
        {
            _tap = (GatedCaptureTap)new GatedCaptureFactory(_service, _devices).OpenCapture("cap-laptop", Phrases.Format);
            _tap.FrameCaptured += frame =>
            {
                lock (_received)
                {
                    _received.Add(frame);
                }
            };
            _tap.Boundary += boundary =>
            {
                lock (_received)
                {
                    _received.Add(boundary);
                }
            };
            _tap.Start();
        }

        _service.SetCloudConnected(cloud);
        await _service.DrainAsync();
        return _service;
    }

    private async Task FeedAsync(IEnumerable<AudioFrame> frames)
    {
        foreach (var frame in frames)
        {
            Mic!.Feed(frame);
        }

        await _service!.DrainAsync();
    }

    private async Task TickAfterAsync(double ms)
    {
        _time.AdvanceMs(ms);
        await _service!.TickAsync();
    }

    // ------------------------------------------------------------------ row 244 + 249

    [Fact]
    public async Task Silence_never_leaves_the_device_and_is_zeroed_as_it_falls_out_of_the_preroll()
    {
        var service = await StartAsync();
        var quiet = Phrases.Quiet(2000);

        await FeedAsync(quiet);

        Assert.Empty(Delivered);
        Assert.Empty(Boundaries);
        Assert.InRange(service.PreRoll.HeldMs, 0, DeviceVoiceContract.DefaultPreRollMs);
        var evicted = quiet.Take(quiet.Count - (int)(service.PreRoll.HeldMs / 20)).ToList();
        Assert.All(evicted, frame => Assert.All(frame.Pcm16, b => Assert.Equal(0, b)));
        Assert.Equal(evicted.Count, service.PreRoll.ZeroedFrames);
        Assert.Equal(DeviceVoiceContract.IndicatorListening, _indicator.Current);
    }

    [Fact]
    public async Task Speech_is_sent_as_one_utterance_that_starts_with_the_preroll_and_ends_at_the_local_end_of_turn()
    {
        await StartAsync();
        var lead = Phrases.Quiet(600);
        var speech = TestSupport.ModulatedSpeech(new SyntheticAudio(Phrases.Format), 400, () => 0).ToList();
        var tail = Phrases.Quiet(2000);

        await FeedAsync(lead);
        await FeedAsync(speech);
        Assert.Equal(DeviceVoiceContract.IndicatorSending, _indicator.Current);
        await FeedAsync(tail);

        var boundaries = Boundaries;
        Assert.Equal(2, boundaries.Count);
        var started = Assert.IsType<UtteranceStarted>(boundaries[0]);
        Assert.Equal("vad_onset", started.Reason);
        var ended = Assert.IsType<UtteranceEnded>(boundaries[1]);
        Assert.Equal(TurnEventKind.SpeechEnded, ended.Turn.Kind);

        var delivered = Delivered;
        // The pre-roll comes first, so the first syllable is not clipped: the lead-in's last
        // frames and the voiced frame that preceded the onset decision.
        Assert.Contains(delivered[0], lead);
        Assert.InRange(delivered.Count(f => lead.Contains(f)) * 20, 200, DeviceVoiceContract.DefaultPreRollMs);
        Assert.Contains(speech[0], delivered);
        Assert.Contains(speech[^1], delivered);
        // After the end of turn, nothing: at most the trailing silence the detector needed.
        var trailing = delivered.Count(f => tail.Contains(f));
        Assert.InRange(trailing * 20, 500, 2500);
        Assert.DoesNotContain(tail[^1], delivered);
        Assert.Equal(DeviceVoiceContract.IndicatorListening, _indicator.Current);
    }

    [Fact]
    public async Task With_no_session_attached_admitted_speech_goes_nowhere_and_is_zeroed()
    {
        var service = await StartAsync(attach: false, cloud: false);
        var speech = TestSupport.ModulatedSpeech(new SyntheticAudio(Phrases.Format), 400, () => 0).ToList();

        await FeedAsync([.. Phrases.Quiet(300), .. speech, .. Phrases.Quiet(1500)]);

        Assert.True(service.AdmittedButUndelivered > 0);
        Assert.All(speech, frame => Assert.All(frame.Pcm16, b => Assert.Equal(0, b)));
        // Offline, the indicator says listening (locally), never "sending".
        Assert.DoesNotContain(DeviceVoiceContract.IndicatorSending, _indicator.States);
    }

    [Fact]
    public async Task A_session_that_attaches_mid_utterance_is_told_the_utterance_started()
    {
        var service = await StartAsync(attach: false);
        await FeedAsync([.. Phrases.Quiet(300), .. TestSupport.ModulatedSpeech(new SyntheticAudio(Phrases.Format), 200, () => 0)]);
        Assert.True(service.UtteranceOpen);

        var tap = (GatedCaptureTap)new GatedCaptureFactory(service, _devices).OpenCapture("x", Phrases.Format);
        var seen = new List<UtteranceBoundary>();
        tap.Boundary += seen.Add;
        tap.Start();
        await service.DrainAsync();

        Assert.Equal("attached_mid_utterance", Assert.IsType<UtteranceStarted>(Assert.Single(seen)).Reason);
    }

    // ------------------------------------------------------------------ rows 242 + 254

    [Fact]
    public async Task Turning_listening_off_closes_the_microphone_itself_and_the_choice_is_persisted()
    {
        var service = await StartAsync();
        var mic = Mic!;
        Assert.True(mic.Started);

        Assert.True(await service.SetEnabledAsync(false, DeviceListeningService.SourceOwnerDevice));

        Assert.False(mic.Started);
        Assert.True(mic.Disposed);
        Assert.Null(service.Capture);
        Assert.False(_store.Load().Enabled);
        Assert.Equal(DeviceVoiceContract.IndicatorOff, _indicator.Current);
        Assert.False(_health.Heartbeat()["listening"]!.GetValue<bool>());

        // Ticks do not reopen it.
        await TickAfterAsync(1000);
        Assert.Single(_devices.Captures);
    }

    [Fact]
    public async Task Only_the_owner_at_the_device_can_turn_listening_back_on()
    {
        var service = await StartAsync(new ListeningSettings(false, ListeningMode.Continuous));
        Assert.Empty(_devices.Captures);
        Assert.Equal(DeviceVoiceContract.IndicatorOff, _indicator.Current);

        Assert.False(await service.SetEnabledAsync(true, DeviceListeningService.SourceRemote));
        Assert.Empty(_devices.Captures);

        Assert.True(await service.SetEnabledAsync(true, DeviceListeningService.SourceOwnerDevice));
        Assert.True(Mic!.Started);
        Assert.True(_store.Load().Enabled);
        Assert.Equal(DeviceVoiceContract.IndicatorListening, _indicator.Current);
    }

    [Fact]
    public void An_unreadable_settings_file_loads_as_off_and_a_written_one_round_trips()
    {
        var directory = Path.Combine(Path.GetTempPath(), "pagentos-b47-" + Guid.NewGuid().ToString("N"));
        try
        {
            var path = FileListeningSettingsStore.DefaultPath(directory);
            var store = new FileListeningSettingsStore(path);
            Assert.Equal(ListeningSettings.Default, store.Load());
            Assert.True(ListeningSettings.Default.Enabled);
            Assert.Equal(ListeningMode.Continuous, ListeningSettings.Default.Mode);

            store.Save(new ListeningSettings(false, ListeningMode.PushToTalk));
            Assert.Equal(new ListeningSettings(false, ListeningMode.PushToTalk), store.Load());
            Assert.False(File.Exists(path + ".tmp"));

            File.WriteAllText(path, "{\"enabled\": tru");
            Assert.False(store.Load().Enabled);
            File.WriteAllText(path, "{\"enabled\": true, \"mode\": \"always_streaming\"}");
            Assert.False(store.Load().Enabled);
        }
        finally
        {
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }

    [Fact]
    public async Task A_cancelled_caller_still_gets_the_microphone_closed_and_the_indicator_off()
    {
        // Regression (found by B47's host test): the loop used to share the caller's token, so
        // cancelling it ended the loop before the shutdown ran - capture left running, the
        // indicator left saying "listening".
        using var cts = new CancellationTokenSource();
        _service = new DeviceListeningService(
            new DeviceListeningOptions { AutoTick = false },
            _catalog,
            _devices,
            _store,
            _mute,
            _ptt,
            new NoKeywordSpotter("test", Phrases.Format.SampleRate),
            _indicator,
            null,
            _health,
            _time);
        await _service.StartAsync(cts.Token);
        var mic = Mic!;
        Assert.True(mic.Started);

        cts.Cancel();
        await _service.DisposeAsync();

        Assert.False(mic.Started);
        Assert.True(mic.Disposed);
        Assert.Equal(DeviceVoiceContract.IndicatorOff, _indicator.Current);
        Assert.Equal(DeviceVoiceContract.IndicatorOff, _health.Indicator);
    }

    // ------------------------------------------------------------------ row 255

    [Fact]
    public async Task A_detected_hardware_mute_stops_capture_and_unmuting_starts_it_again()
    {
        var service = await StartAsync();
        var first = Mic!;

        _mute.Muted = true;
        await TickAfterAsync(600);

        Assert.False(first.Started);
        Assert.Null(service.Capture);
        Assert.Equal(DeviceVoiceContract.IndicatorMuted, _indicator.Current);
        Assert.True(_health.Heartbeat()["mic_muted"]!.GetValue<bool>());

        // Frames the old stream still delivers are dropped and zeroed.
        var late = Phrases.Say(Phrases.Request);
        foreach (var frame in late)
        {
            // Feed ignores a stopped capture; deliver through the handler the way a racing
            // WASAPI callback would, by starting the old fake again without the service knowing.
            first.Start();
            first.Feed(frame);
            first.Stop();
        }

        await service.DrainAsync();
        Assert.Empty(Delivered);
        Assert.All(late, frame => Assert.All(frame.Pcm16, b => Assert.Equal(0, b)));

        _mute.Muted = false;
        await TickAfterAsync(600);
        Assert.NotSame(first, Mic);
        Assert.True(Mic!.Started);
        Assert.Equal(2, service.CaptureOpens);
        Assert.Equal(DeviceVoiceContract.IndicatorListening, _indicator.Current);
    }

    [Fact]
    public async Task A_mute_state_that_cannot_be_read_is_reported_as_unknown_and_listening_continues()
    {
        var service = await StartAsync();
        _mute.Muted = null;
        await TickAfterAsync(600);

        Assert.NotNull(service.Capture);
        Assert.Null(_health.Heartbeat()["mic_muted"]);
        Assert.Equal(DeviceVoiceContract.IndicatorListening, _indicator.Current);
    }

    [Fact]
    public async Task The_mute_state_is_polled_on_its_own_period_not_on_every_tick()
    {
        await StartAsync();
        var before = _mute.Queries;
        for (var i = 0; i < 9; i++)
        {
            await TickAfterAsync(50);
        }

        Assert.Equal(before, _mute.Queries);
        await TickAfterAsync(60);
        Assert.Equal(before + 1, _mute.Queries);
    }

    [Fact]
    public async Task Digital_silence_from_an_open_microphone_is_reported()
    {
        await StartAsync();
        var zeros = Enumerable.Range(0, 160).Select(_ => AudioFrame.Silence(Phrases.Format, 20, 0)).ToList();

        await FeedAsync(zeros);

        Assert.True(_health.Report()["microphone"]!["digital_silence"]!.GetValue<bool>());
    }

    // ------------------------------------------------------------------ row 243

    [Fact]
    public async Task Push_to_talk_captures_nothing_until_the_key_is_held_and_ends_the_turn_on_release()
    {
        var service = await StartAsync(new ListeningSettings(true, ListeningMode.PushToTalk));
        Assert.Empty(_devices.Captures);
        Assert.Equal(DeviceVoiceContract.IndicatorPushToTalk, _indicator.Current);

        _ptt.IsDown = true;
        await TickAfterAsync(50);
        Assert.True(Mic!.Started);
        Assert.False(service.UtteranceOpen);

        await TickAfterAsync(200);
        Assert.True(service.UtteranceOpen);
        Assert.Equal("push_to_talk", Assert.IsType<UtteranceStarted>(Assert.Single(Boundaries)).Reason);

        // A pause while the key is held does not end the turn.
        await FeedAsync([.. Phrases.Say(Phrases.Request), .. Phrases.Quiet(3000), .. Phrases.Say(Phrases.Request, seed: 3)]);
        Assert.True(service.UtteranceOpen);
        Assert.Single(Boundaries);

        _ptt.IsDown = false;
        await TickAfterAsync(50);
        var ended = Assert.IsType<UtteranceEnded>(Boundaries[^1]);
        Assert.Equal("push_to_talk_release", ended.Turn.Reason);
        Assert.Null(service.Capture);
        Assert.True(_devices.Captures[0].Disposed);
    }

    [Fact]
    public async Task A_key_tap_shorter_than_the_minimum_hold_sends_nothing()
    {
        var service = await StartAsync(new ListeningSettings(true, ListeningMode.PushToTalk));

        _ptt.IsDown = true;
        await TickAfterAsync(50);
        await FeedAsync(Phrases.Say([(700, 80)]));
        _ptt.IsDown = false;
        await TickAfterAsync(50);

        Assert.Empty(Boundaries);
        Assert.Empty(Delivered);
        Assert.Null(service.Capture);
    }

    // ------------------------------------------------------------------ rows 241 + 242

    [Fact]
    public async Task Wake_word_mode_is_refused_without_an_enrolled_wake_word_and_a_stored_choice_runs_as_push_to_talk()
    {
        var service = await StartAsync();
        var refused = await service.SetModeAsync(ListeningMode.WakeWord);
        Assert.NotNull(refused);
        Assert.StartsWith("wake_word_unavailable", refused, StringComparison.Ordinal);
        Assert.Equal(ListeningMode.Continuous, service.EffectiveMode);
        Assert.False(_health.Report()["wake_word"]!["available"]!.GetValue<bool>());

        await service.DisposeAsync();
        _service = null;
        var again = await StartAsync(new ListeningSettings(true, ListeningMode.WakeWord));
        Assert.Equal(ListeningMode.WakeWord, again.Settings.Mode);
        Assert.Equal(ListeningMode.PushToTalk, again.EffectiveMode);
        Assert.Equal("wake_word_unavailable", _health.LastError);
    }

    [Fact]
    public async Task In_wake_word_mode_speech_that_does_not_start_with_the_wake_word_never_leaves_and_is_zeroed()
    {
        var service = await StartAsync(new ListeningSettings(true, ListeningMode.WakeWord), Spotter());
        Assert.Equal(DeviceVoiceContract.IndicatorWakeWord, _indicator.Current);
        var other = Phrases.Say(Phrases.Request, seed: 70);

        await FeedAsync([.. Phrases.Quiet(400), .. other, .. Phrases.Quiet(2000)]);

        Assert.Empty(Delivered);
        Assert.Empty(Boundaries);
        Assert.True(service.WatchZeroedFrames > 0);
        Assert.All(other, frame => Assert.All(frame.Pcm16, b => Assert.Equal(0, b)));
    }

    [Fact]
    public async Task Wake_word_and_request_in_one_breath_send_the_request_and_never_the_wake_word()
    {
        var service = await StartAsync(new ListeningSettings(true, ListeningMode.WakeWord), Spotter());
        var wake = Phrases.Say(Phrases.Wake, seed: 71);
        var request = Phrases.Say(Phrases.Request, seed: 72);

        await FeedAsync([.. Phrases.Quiet(400), .. wake, .. request, .. Phrases.Quiet(2000)]);

        var started = Assert.IsType<UtteranceStarted>(Boundaries[0]);
        Assert.Equal("wake_word", started.Reason);
        Assert.IsType<UtteranceEnded>(Boundaries[^1]);
        var delivered = Delivered;
        Assert.Contains(request[^1], delivered);
        // At most the wake word's last frames straddle the boundary; its body never leaves.
        Assert.True(wake.Take(wake.Count - 3).All(f => !delivered.Contains(f)));
        Assert.All(wake.Take(wake.Count - 3), frame => Assert.All(frame.Pcm16, b => Assert.Equal(0, b)));
        Assert.True(service.WatchZeroedFrames > 0);
    }

    [Fact]
    public async Task A_wake_word_on_its_own_lets_the_next_breath_through_inside_the_follow_up_window_only()
    {
        await StartAsync(new ListeningSettings(true, ListeningMode.WakeWord), Spotter());

        await FeedAsync([.. Phrases.Quiet(400), .. Phrases.Say(Phrases.Wake, seed: 73), .. Phrases.Quiet(1500)]);
        Assert.Empty(Boundaries);

        await FeedAsync([.. Phrases.Say(Phrases.Request, seed: 74), .. Phrases.Quiet(2000)]);
        Assert.Equal("follow_up", Assert.IsType<UtteranceStarted>(Boundaries[0]).Reason);

        // Well after the window, the same request is not addressed to the device.
        _time.AdvanceMs(DeviceVoiceContract.FollowUpWindowMs + 1000);
        var count = Boundaries.Count;
        await FeedAsync([.. Phrases.Say(Phrases.Request, seed: 75), .. Phrases.Quiet(2000)]);
        Assert.Equal(count, Boundaries.Count);
    }

    [Fact]
    public async Task Switching_modes_is_persisted_and_push_to_talk_releases_the_microphone()
    {
        var service = await StartAsync(spotter: Spotter());
        Assert.NotNull(service.Capture);

        Assert.Null(await service.SetModeAsync(ListeningMode.PushToTalk));
        Assert.Null(service.Capture);
        Assert.Equal(ListeningMode.PushToTalk, _store.Load().Mode);

        Assert.Null(await service.SetModeAsync(ListeningMode.WakeWord));
        Assert.NotNull(service.Capture);
        Assert.Equal(DeviceVoiceContract.IndicatorWakeWord, _indicator.Current);
        Assert.Equal(DeviceVoiceContract.ModeWakeWord, _health.Heartbeat()["mode"]!.GetValue<string>());
    }

    // ------------------------------------------------------------------ row 253 + 259

    [Fact]
    public async Task An_offline_snooze_acts_only_while_an_alarm_rings_and_the_cloud_is_unreachable()
    {
        var service = await StartAsync(spotter: Spotter());
        _commands.Ringing = true;

        // Cloud reachable: the words went to the Cloud Core; the device does not act too.
        await SayAsync(Phrases.Snooze, 80);
        Assert.Empty(_commands.Executed);

        service.SetCloudConnected(false);
        await SayAsync(Phrases.Snooze, 81);
        Assert.Equal([DeviceVoiceContract.CommandAlarmSnooze], _commands.Executed);
        var last = _health.Report()["offline_commands"]!["last"]!;
        Assert.Equal(DeviceVoiceContract.CommandAlarmSnooze, last["id"]!.GetValue<string>());
        Assert.True(last["executed"]!.GetValue<bool>());

        // Nothing ringing: the command has nothing to act on and is not even attempted.
        _commands.Ringing = false;
        await SayAsync(Phrases.Snooze, 82);
        Assert.Single(_commands.Executed);
    }

    [Fact]
    public async Task A_request_that_is_not_an_offline_command_does_nothing_locally()
    {
        var service = await StartAsync(spotter: Spotter(), cloud: false);
        _commands.Ringing = true;

        await SayAsync(Phrases.Request, 83);
        await SayAsync(Phrases.Stop, 84);

        Assert.Empty(_commands.Executed);
        Assert.True(service.Settings.Enabled);
    }

    [Fact]
    public async Task Listening_off_by_voice_acts_even_with_the_cloud_up_and_closes_the_microphone()
    {
        var service = await StartAsync(spotter: Spotter(), cloud: true);

        await SayAsync(Phrases.ListeningOff, 85);

        Assert.False(service.Settings.Enabled);
        Assert.Null(service.Capture);
        Assert.Equal(DeviceVoiceContract.IndicatorOff, _indicator.Current);
        Assert.Empty(_commands.Executed);
        Assert.Equal(DeviceVoiceContract.CommandListeningOff, _health.Report()["offline_commands"]!["last"]!["id"]!.GetValue<string>());
    }

    [Fact]
    public async Task A_long_request_is_never_classified_as_a_command()
    {
        await StartAsync(spotter: Spotter(), cloud: false);
        _commands.Ringing = true;
        var longRequest = Enumerable.Repeat(Phrases.Snooze, 6).SelectMany(p => p).ToArray();

        await SayAsync(longRequest, 86);

        Assert.Empty(_commands.Executed);
    }

    // ------------------------------------------------------------------ devices + health

    [Fact]
    public async Task Removing_the_active_microphone_moves_listening_to_the_next_one()
    {
        var service = await StartAsync();
        var laptop = Mic!;
        _catalog.Set(FakeDeviceCatalog.LaptopMic(isCommunications: false), FakeDeviceCatalog.HeadsetMic(isDefault: false, isCommunications: true), FakeDeviceCatalog.LaptopSpeakers());
        await service.DrainAsync();
        Assert.Equal("cap-headset", service.Capture!.DeviceId);
        Assert.True(laptop.Disposed);

        _catalog.Set(FakeDeviceCatalog.LaptopSpeakers());
        await service.DrainAsync();
        Assert.Null(service.Capture);
        Assert.False(_health.Report()["microphone"]!["capturing"]!.GetValue<bool>());
    }

    [Fact]
    public async Task The_heartbeat_carries_exactly_the_contract_keys_and_only_contract_states()
    {
        await StartAsync();
        var heartbeat = _health.Heartbeat();

        Assert.Equal(DeviceVoiceContract.HeartbeatKeys.Order(StringComparer.Ordinal), heartbeat.Select(p => p.Key).Order(StringComparer.Ordinal));
        Assert.Contains(heartbeat["indicator"]!.GetValue<string>(), DeviceVoiceContract.IndicatorStates);
        Assert.All(_indicator.States, s => Assert.Contains(s, DeviceVoiceContract.IndicatorStates));
        var report = _health.Report();
        Assert.False(report["privacy"]!["raw_audio_persisted"]!.GetValue<bool>());
        Assert.Equal("session_companion", report["microphone"]!["capture_process"]!.GetValue<string>());
        Assert.Throws<ArgumentException>(() => _health.SetState("streaming"));
        Assert.Throws<ArgumentException>(() => _health.SetListening(true, "continuous", "recording"));
    }

    [Fact]
    public void The_preroll_ring_never_holds_more_than_the_contract_allows()
    {
        var buffer = new PreRollBuffer(60_000);
        Assert.Equal(DeviceVoiceContract.MaxPreRollMs, buffer.CapacityMs);
        var frames = Phrases.Say(Phrases.Request).Concat(Phrases.Say(Phrases.Request)).Concat(Phrases.Say(Phrases.Request)).ToList();
        foreach (var frame in frames)
        {
            buffer.Push(frame);
        }

        Assert.InRange(buffer.HeldMs, 0, DeviceVoiceContract.MaxPreRollMs);
        Assert.All(frames.Take(frames.Count - buffer.Count), f => Assert.All(f.Pcm16, b => Assert.Equal(0, b)));
        var taken = buffer.TakeAll();
        Assert.Contains(taken, f => f.Pcm16.Any(b => b != 0));
        Assert.Equal(0, buffer.Count);

        var none = new PreRollBuffer(0);
        var frame0 = Phrases.Say(Phrases.Request)[0];
        none.Push(frame0);
        Assert.Equal(0, none.Count);
        Assert.All(frame0.Pcm16, b => Assert.Equal(0, b));
    }

    // ------------------------------------------------------------------ helpers

    private static TemplateKeywordSpotter Spotter() => new(Phrases.Enrolled(
        (DeviceVoiceContract.WakeWordPhraseId, Phrases.Wake),
        (DeviceVoiceContract.CommandAlarmSnooze, Phrases.Snooze),
        (DeviceVoiceContract.CommandListeningOff, Phrases.ListeningOff)));

    private Task SayAsync((double Hz, int Ms)[] phrase, int seed)
        => FeedAsync([.. Phrases.Quiet(400), .. Phrases.Say(phrase, seed: seed), .. Phrases.Quiet(2600)]);

    private sealed class FakeCommandSink : IOfflineCommandSink
    {
        public bool Ringing { get; set; }

        public List<string> Executed { get; } = [];

        public bool AlarmRinging => Ringing;

        public OfflineCommandOutcome Execute(string commandId)
        {
            Executed.Add(commandId);
            return new OfflineCommandOutcome(true, "done");
        }
    }
}
