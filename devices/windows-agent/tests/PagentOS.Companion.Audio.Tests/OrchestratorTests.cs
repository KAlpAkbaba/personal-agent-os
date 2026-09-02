using System.Text.Json.Nodes;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Audio.Processing;
using PagentOS.Companion.Audio.Fakes;
using PagentOS.Companion.Audio.Media;
using PagentOS.Companion.Audio.Orchestration;
using PagentOS.Companion.Audio.Session;
using PagentOS.Companion.Audio.Sideband;
using PagentOS.Companion.Audio.Testing;
using PagentOS.Companion.Audio.Tests.Support;
using PagentOS.Companion.Audio.Turn;
using Xunit;

namespace PagentOS.Companion.Audio.Tests;

/// <summary>The whole client against fakes: real loop, VAD, guard, barge-in, relay, reporter; no audio hardware, no network.</summary>
public sealed class OrchestratorTests : IAsyncDisposable
{
    private const string Token = "pagentos_sess_orch";
    private readonly TimeProvider _time = TimeProvider.System;
    private readonly List<string> _trace = new();
    private readonly FakeDeviceCatalog _catalog = new();
    private readonly FakeDeviceFactory _devices;
    private readonly InProcessFakeCloudCore _cloud = new(Token);
    private readonly FakeSidebandPushSource _pushes = new();
    private readonly List<FakeMediaLeg> _legs = new();
    private readonly SyntheticAudio _synth = new(TestSupport.Format);
    private VoiceSessionOrchestrator? _orchestrator;

    public OrchestratorTests()
    {
        _devices = new FakeDeviceFactory(_time, _trace);
        _catalog.Set(FakeDeviceCatalog.LaptopMic(), FakeDeviceCatalog.LaptopSpeakers());
    }

    private FakeMediaLeg Leg => _legs[^1];

    private FakeCapture Capture => _devices.CurrentCapture!;

    private FakePlayback Playback => _devices.CurrentPlayback!;

    private async Task<VoiceSessionOrchestrator> StartAsync(VoiceClientOptions? options = null, IVoiceEventReporter? reporter = null)
    {
        _orchestrator = new VoiceSessionOrchestrator(
            options ?? new VoiceClientOptions { EndOfTurn = EndOfTurnMode.Client },
            _catalog,
            _devices,
            _ =>
            {
                var leg = new FakeMediaLeg(_time)
                {
                    OnCommand = (command, _) =>
                    {
                        if (command is CancelResponseCommand)
                        {
                            _trace.Add("leg:cancel");
                        }

                        return Task.CompletedTask;
                    },
                };
                _legs.Add(leg);
                return leg;
            },
            _cloud.CreateSidebandClient(),
            _pushes,
            _time,
            reporterOverride: reporter);
        await _orchestrator.StartAsync(CancellationToken.None);
        return _orchestrator;
    }

    private void Speak(int ms)
    {
        foreach (var frame in TestSupport.ModulatedSpeech(_synth, ms, _time.GetTimestamp))
        {
            Capture.Feed(frame);
        }
    }

    private async Task SilenceUntilAsync(Func<bool> condition, int maxMs = 3000)
    {
        for (var fed = 0; fed < maxMs && !condition(); fed += 20)
        {
            Capture.Feed(TestSupport.Quiet(_time.GetTimestamp()));
            await Task.Delay(1);
        }

        Assert.True(await TestSupport.WaitForAsync(condition, 1000), "condition never held");
    }

    private void ProviderSpeaks(string responseId, int chunks = 5)
    {
        Leg.Emit(new ResponseStartedEvent(_time.GetTimestamp(), responseId));
        for (var i = 0; i < chunks; i++)
        {
            Leg.Emit(new AudioDeltaEvent(_time.GetTimestamp(), responseId, _synth.Tone(40, 0, 440, 0.2).Pcm16));
        }
    }

    [Fact]
    public async Task Start_creates_the_session_opens_the_leg_and_records_an_honest_processing_report()
    {
        var o = await StartAsync();

        Assert.Single(_cloud.Sessions);
        Assert.Equal(o.Grant!.SessionId, Leg.Grant!.SessionId);
        Assert.Equal(EndOfTurnMode.Client, Leg.Options!.EndOfTurn);
        Assert.True(Capture.Started);
        Assert.Equal("cap-laptop", Capture.DeviceId);
        Assert.Equal("ren-laptop", Playback.DeviceId);
        Assert.Equal(AudioProcessingReport.EchoAwareGateOnly, o.ProcessingReport!.EchoCancellation);
        Assert.Equal(AudioProcessingReport.NoiseGate, o.ProcessingReport.NoiseSuppression);
        Assert.Equal(new[] { "dc_blocker", "noise_gate" }, o.ProcessingReport.ClientProcessors);
        Assert.NotEmpty(o.ProcessingReport.Deferred);
        Assert.Equal(VoiceClientState.Idle, o.Fsm.State);
    }

    [Fact]
    public async Task A_headset_render_path_reports_that_echo_cancellation_is_not_needed()
    {
        _catalog.Set(FakeDeviceCatalog.HeadsetMic(isDefault: true, isCommunications: true), FakeDeviceCatalog.HeadsetEarpiece(isDefault: true, isCommunications: true));
        var o = await StartAsync();
        Assert.Equal(AudioProcessingReport.HeadsetNoEchoPath, o.ProcessingReport!.EchoCancellation);
    }

    [Fact]
    public async Task A_turn_flows_from_microphone_to_uplink_to_end_of_turn_to_first_audio_with_the_metrics_reported()
    {
        var o = await StartAsync();

        Speak(300);
        Assert.True(await TestSupport.WaitForAsync(() => o.Fsm.State == VoiceClientState.Listening));
        await SilenceUntilAsync(() => o.Fsm.EventKinds().Contains("owner_speech_ended"));

        Assert.True(await TestSupport.WaitForAsync(() => Leg.Commands.OfType<CommitTurnCommand>().Any()));
        Assert.True(Leg.AudioFramesSent >= 15);

        ProviderSpeaks("resp-1");
        Assert.True(await TestSupport.WaitForAsync(() => o.Fsm.State == VoiceClientState.AssistantSpeaking));
        Assert.True(await TestSupport.WaitForAsync(() => Playback.EnqueuedBytes > 0));
        Leg.Emit(new ResponseDoneEvent(_time.GetTimestamp(), "resp-1", "completed"));
        await Task.Delay(20);
        Playback.Drain();
        Assert.True(await TestSupport.WaitForAsync(() => o.Fsm.State == VoiceClientState.Idle));

        var names = _cloud.EventKindsFor(o.Grant!.SessionId);
        Assert.Equal(new[] { "mic_speech_start", "uplink_first_packet", "end_of_turn", "first_audio", "response_done" }, names);
        var events = _cloud.EventsFor(o.Grant.SessionId);
        Assert.True(events[1]["payload"]!["mic_to_uplink_ms"]!.GetValue<double>() >= 0);
        Assert.Equal("none", events[2]["payload"]!["hesitation_reason"]!.GetValue<string>());
        Assert.True(events[3]["payload"]!["eot_to_first_ms"]!.GetValue<double>() >= 0);

        var metrics = Assert.Single(o.Latency.Metrics());
        Assert.NotNull(metrics.MicToUplinkMs);
        Assert.NotNull(metrics.EotToFirstAudioMs);
        Assert.Null(metrics.BargeInToStopMs);
    }

    [Fact]
    public async Task Speaking_over_the_assistant_stops_playback_first_then_cancels_then_reports()
    {
        var reporter = new RecordingEventReporter(_time, _trace);
        var o = await StartAsync(reporter: reporter);

        ProviderSpeaks("resp-1", chunks: 20);
        Assert.True(await TestSupport.WaitForAsync(() => o.Fsm.State == VoiceClientState.AssistantSpeaking && Playback.IsPlaying));
        _trace.Clear();

        Speak(200);
        Assert.True(await TestSupport.WaitForAsync(() => o.Fsm.BargeInCount == 1));

        Assert.Equal(new[] { "playback:stop", "leg:cancel", "report:barge_in_start", "report:playback_stopped", "report:mic_speech_start" }, _trace.Take(5));
        Assert.False(Playback.IsPlaying);
        Assert.Equal(VoiceClientState.Listening, o.Fsm.State);
        var kinds = o.Fsm.EventKinds().ToList();
        Assert.True(kinds.IndexOf("assistant_speech_cut") < kinds.IndexOf("barge_in"));
        var bargeIn = reporter.Reports.Single(r => r.Event == VoiceClientEvents.BargeInStart).Data;
        Assert.True(bargeIn["playback_stopped_ms"]!.GetValue<double>() >= 0);
        Assert.True(bargeIn["discarded_ms"]!.GetValue<int>() > 0);
        Assert.True(reporter.Reports.Single(r => r.Event == VoiceClientEvents.MicSpeechStart).Data["barge_in"]!.GetValue<bool>());

        // Late audio for the cancelled response must not restart playback.
        Leg.Emit(new AudioDeltaEvent(_time.GetTimestamp(), "resp-1", _synth.Tone(40, 0).Pcm16));
        Leg.Emit(new ResponseDoneEvent(_time.GetTimestamp(), "resp-1", "cancelled"));
        await Task.Delay(30);
        Assert.False(Playback.IsPlaying);
        Assert.NotNull(o.Latency.Metrics().Single().BargeInToStopMs);
    }

    [Fact]
    public async Task The_stop_word_in_the_transcript_interrupts_even_when_the_vad_missed_the_onset()
    {
        var o = await StartAsync();
        ProviderSpeaks("resp-1", chunks: 20);
        Assert.True(await TestSupport.WaitForAsync(() => Playback.IsPlaying));

        Leg.Emit(new TranscriptDeltaEvent(_time.GetTimestamp(), "Dur.", Final: true));

        Assert.True(await TestSupport.WaitForAsync(() => o.Fsm.BargeInCount == 1));
        Assert.False(Playback.IsPlaying);
        Assert.Contains(Leg.Commands, c => c is CancelResponseCommand);
        Assert.Equal("dur", o.Fsm.Events.Single(e => e.Kind == "barge_in").Detail);
        Assert.True(_cloud.EventsFor(o.Grant!.SessionId).Single(e => e["kind"]!.GetValue<string>() == "barge_in_start")["payload"]!["stop_word"]!.GetValue<bool>());
    }

    [Fact]
    public async Task A_long_running_tool_is_relayed_its_preamble_spoken_and_its_completion_submitted_as_a_follow_up()
    {
        var o = await StartAsync(new VoiceClientOptions { EndOfTurn = EndOfTurnMode.Server, ToolSilenceBoundMs = 60 });

        Leg.Emit(new ToolCallEvent(_time.GetTimestamp(), "call-1", "research", """{"topic":"ai"}"""));
        Assert.True(await TestSupport.WaitForAsync(() => o.Relay!.RunningCalls.Contains("call-1")));
        Assert.Equal(1, _cloud.ToolExecutions);
        var provisional = Leg.Commands.OfType<ToolResultCommand>().Single();
        Assert.False(provisional.Final);
        Assert.Contains("Bakıyorum", provisional.OutputJson);
        Assert.Equal(VoiceClientState.ToolRunning, o.Fsm.State);

        ProviderSpeaks("resp-preamble", chunks: 2);
        Assert.True(await TestSupport.WaitForAsync(() => o.Fsm.EventKinds().Contains("assistant_progress")));
        Assert.Equal(VoiceClientState.ToolRunning, o.Fsm.State);
        Leg.Emit(new ResponseDoneEvent(_time.GetTimestamp(), "resp-preamble", "completed"));
        Playback.Drain();

        // Spec §6: silence during a tool beyond the bound is a defect the harness reports.
        await Task.Delay(120);
        await SilenceUntilAsync(() => o.Defects.Any(d => d.StartsWith("tool_silence_exceeded")), 500);

        _pushes.Push(SidebandPushKinds.ToolCompleted, new JsonObject { ["call_id"] = "call-1", ["result"] = new JsonObject { ["summary"] = "tamam" } });
        Assert.True(await TestSupport.WaitForAsync(() => o.Relay!.RunningCalls.Count == 0));
        var final = Leg.Commands.OfType<ToolResultCommand>().Last();
        Assert.True(final.Final);
        Assert.True(final.FollowUp);
        Assert.Equal(VoiceClientState.Idle, o.Fsm.State);

        ProviderSpeaks("resp-final", chunks: 2);
        Assert.True(await TestSupport.WaitForAsync(() => o.Fsm.State == VoiceClientState.AssistantSpeaking));
        var metrics = o.Latency.Metrics().Single();
        Assert.NotNull(metrics.ToolPreambleMs);
        Assert.NotNull(metrics.ToolDoneToSpeechMs);
        Assert.Equal(new[] { "tool_call", "preamble_audio_start", "response_done", "tool_done", "speech_resumed" }, _cloud.EventKindsFor(o.Grant!.SessionId));
    }

    [Fact]
    public async Task A_say_push_is_spoken_through_the_leg()
    {
        var o = await StartAsync();
        _pushes.Push(SidebandPushKinds.Say, new JsonObject { ["text"] = "Bir dakika." });
        Assert.True(await TestSupport.WaitForAsync(() => Leg.Commands.OfType<SayCommand>().Any()));
        Assert.Equal("Bir dakika.", Leg.Commands.OfType<SayCommand>().Single().Text);
        Assert.Equal(VoiceClientState.Idle, o.Fsm.State);
    }

    [Fact]
    public async Task Losing_the_media_leg_keeps_the_session_reattaches_and_reports_both_events_in_order()
    {
        var o = await StartAsync(new VoiceClientOptions
        {
            EndOfTurn = EndOfTurnMode.Client,
            ReconnectBackoff = new PagentOS.Agent.Core.Connection.BackoffPolicy(0.01, 0.05),
        });
        var firstLeg = Leg;
        var firstCredential = o.Grant!.CredentialSecret();

        firstLeg.EmitDisconnect("wifi dropped");

        Assert.True(await TestSupport.WaitForAsync(() => o.ReconnectCount == 1, 5000));
        Assert.Equal(2, _legs.Count);
        Assert.True(Leg.IsOpen);
        Assert.NotSame(firstLeg, Leg);
        Assert.Equal(1, _cloud.AttachRequests);
        Assert.NotEqual(firstCredential, o.Grant.CredentialSecret());
        Assert.True(await TestSupport.WaitForAsync(() => _cloud.EventKindsFor(o.Grant.SessionId).Contains("network_restored")));
        Assert.Equal(new[] { "network_lost", "network_restored" }, _cloud.EventKindsFor(o.Grant.SessionId));
        Assert.Contains("network_lost", o.Fsm.EventKinds());
        Assert.Contains("network_restored", o.Fsm.EventKinds());
        Assert.True(o.Reporter!.Online);

        // Audio keeps flowing on the new leg.
        Speak(100);
        Assert.True(await TestSupport.WaitForAsync(() => Leg.AudioFramesSent >= 5));
        Assert.Equal(VoiceClientState.Listening, o.Fsm.State);
    }

    [Fact]
    public async Task Unplugging_the_active_microphone_switches_immediately_and_a_headset_plugged_in_switches_at_the_turn_boundary()
    {
        var o = await StartAsync();
        var laptopCapture = Capture;

        _catalog.Set(FakeDeviceCatalog.HeadsetMic(isDefault: true, isCommunications: true), FakeDeviceCatalog.LaptopSpeakers());

        Assert.True(await TestSupport.WaitForAsync(() => Capture.DeviceId == "cap-headset"));
        Assert.True(laptopCapture.Disposed);
        Assert.True(Capture.Started);
        var removed = Assert.Single(o.DeviceSwitches, s => s.Direction == AudioDirection.Capture && s.From is not null);
        Assert.Equal("active_device_removed", removed.Reason);
        Assert.True(removed.Immediate);

        // Mid-turn, a better device appearing must not cut the sentence: deferred to the boundary.
        Speak(300);
        Assert.True(await TestSupport.WaitForAsync(() => o.Fsm.State == VoiceClientState.Listening));
        _catalog.Set(FakeDeviceCatalog.HeadsetMic(), FakeDeviceCatalog.LaptopMic(isDefault: true, isCommunications: true), FakeDeviceCatalog.LaptopSpeakers());
        await Task.Delay(30);
        Assert.Equal("cap-headset", Capture.DeviceId);

        await SilenceUntilAsync(() => o.Fsm.EventKinds().Contains("owner_speech_ended"));
        Assert.True(await TestSupport.WaitForAsync(() => Capture.DeviceId == "cap-laptop"));
        var deferred = o.DeviceSwitches.Last();
        Assert.Equal("default_communications", deferred.Reason);
        Assert.False(deferred.Immediate);
    }

    [Fact]
    public async Task The_owners_preferred_microphone_is_honoured_when_present()
    {
        _catalog.Set(FakeDeviceCatalog.LaptopMic(), FakeDeviceCatalog.HeadsetMic(), FakeDeviceCatalog.LaptopSpeakers());
        await StartAsync(new VoiceClientOptions { PreferredCaptureDeviceId = "cap-headset" });
        Assert.Equal("cap-headset", Capture.DeviceId);
    }

    [Fact]
    public async Task Stopping_closes_the_fsm_and_the_leg()
    {
        var o = await StartAsync();
        await o.StopAsync();
        Assert.Equal(VoiceClientState.Closed, o.Fsm.State);
        Assert.False(Leg.IsOpen);
    }

    // ---------------------------------------------------- the server's verdicts (ADR-0039)

    [Fact]
    public async Task A_leg_closed_push_silences_this_device_and_the_owners_next_words_reclaim_the_leg_with_its_backlog()
    {
        var o = await StartAsync();
        var sid = o.Grant!.SessionId;
        var firstLeg = Leg;

        // The phone attached (spec §7): Cloud Core pushes leg_closed to this device.
        var legClosed = _cloud.SupersedeLeg(sid, "mobile");
        _pushes.Push(SidebandPush.TryParseFrame(legClosed)!);

        Assert.True(await TestSupport.WaitForAsync(() => o.LegSuperseded));
        Assert.False(firstLeg.IsOpen);
        Assert.Equal(1, o.LegSupersededCount);
        Assert.Contains("leg_closed", o.PushesHandled);
        // Nothing is uplinked while another client holds the leg (idle frames would normally stream).
        var framesBefore = firstLeg.AudioFramesSent;
        for (var i = 0; i < 10; i++)
        {
            Capture.Feed(TestSupport.Quiet(_time.GetTimestamp()));
        }

        await Task.Delay(30);
        Assert.Equal(framesBefore, firstLeg.AudioFramesSent);
        Assert.Equal(0, o.ReattachCount);

        // Meanwhile Cloud Core queued a push for this session; the owner speaks to the desktop again.
        _cloud.QueueSideband(sid, SidebandPushKinds.Say, new JsonObject { ["text"] = "Masaüstüne döndük." });
        Speak(300);

        Assert.True(await TestSupport.WaitForAsync(() => o.ReattachCount == 1, 5000));
        Assert.False(o.LegSuperseded);
        Assert.Equal(2, _legs.Count);
        Assert.True(Leg.IsOpen);
        Assert.Equal(1, _cloud.AttachRequests);
        Assert.Equal("windows_desktop", _cloud.Sessions[sid].ClientKind);
        // the backlog returned by attach was applied on the NEW leg, and reporting resumed
        Assert.True(await TestSupport.WaitForAsync(() => Leg.Commands.OfType<SayCommand>().Any()));
        Assert.Equal("Masaüstüne döndük.", Leg.Commands.OfType<SayCommand>().Single().Text);
        Assert.True(await TestSupport.WaitForAsync(() => _cloud.EventKindsFor(sid).Contains("mic_speech_start")));
        Assert.True(o.Reporter!.Online);
        Assert.Equal(0, _cloud.Refusals);
    }

    [Fact]
    public async Task A_409_from_cloud_core_is_a_superseded_leg_too_and_the_held_events_arrive_after_the_reattach()
    {
        var o = await StartAsync();
        var sid = o.Grant!.SessionId;

        // The leg moved but the push never reached us (offline at the time): the next post says 409.
        _cloud.SupersedeLeg(sid, "mobile");
        Speak(300);

        Assert.True(await TestSupport.WaitForAsync(() => o.LegSuperseded, 5000));
        Assert.Equal(1, o.Reporter!.StaleLegRefusals);
        Assert.False(o.Reporter.Online);
        Assert.True(o.Reporter.PendingCount >= 1); // held, not dropped
        await SilenceUntilAsync(() => o.Fsm.EventKinds().Contains("owner_speech_ended"));

        Speak(300);
        Assert.True(await TestSupport.WaitForAsync(() => o.ReattachCount == 1, 5000));
        Assert.True(await TestSupport.WaitForAsync(() => o.Reporter.PendingCount == 0 && o.Reporter.Online, 5000));
        var kinds = _cloud.EventKindsFor(sid);
        Assert.Equal("mic_speech_start", kinds[0]);
        Assert.Contains("end_of_turn", kinds);
        Assert.Equal(2, kinds.Count(k => k == "mic_speech_start"));
        Assert.Equal(0, o.Reporter.RefusedBatches);
    }

    [Fact]
    public async Task A_gone_session_ends_the_orchestrator_with_a_reason_the_host_acts_on()
    {
        var o = await StartAsync();
        _cloud.CloseSession(o.Grant!.SessionId);

        Speak(300);

        Assert.True(await TestSupport.WaitForAsync(() => o.StopReason == "session_gone", 5000));
        Assert.False(o.Reporter!.Online);
        Assert.Equal(0, o.Reporter.RefusedBatches);
        Assert.Equal(0, o.ReattachCount);
    }

    [Fact]
    public async Task The_sideband_backlog_in_an_events_ack_is_applied_like_a_push()
    {
        var o = await StartAsync();
        var sid = o.Grant!.SessionId;
        _cloud.QueueSideband(sid, SidebandPushKinds.Say, new JsonObject { ["text"] = "Kuyruktan geldi." });

        Speak(300); // the first report's ack carries the backlog

        Assert.True(await TestSupport.WaitForAsync(() => Leg.Commands.OfType<SayCommand>().Any(), 5000));
        Assert.Equal("Kuyruktan geldi.", Leg.Commands.OfType<SayCommand>().Single().Text);
        Assert.Contains("say", o.PushesHandled);
    }

    [Fact]
    public async Task A_push_for_another_session_is_ignored_and_a_provider_error_is_reported_as_an_error_event()
    {
        var o = await StartAsync();
        _pushes.Push(SidebandPushKinds.Say, new JsonObject { ["text"] = "Yanlış oturum." }, sessionId: Guid.NewGuid().ToString());
        Leg.Emit(new ProviderErrorEvent(_time.GetTimestamp(), "rate_limit", "slow down"));

        Assert.True(await TestSupport.WaitForAsync(() => _cloud.EventKindsFor(o.Grant!.SessionId).Contains("error")));
        await Task.Delay(30);
        Assert.Empty(Leg.Commands.OfType<SayCommand>());
        Assert.Empty(o.PushesHandled);
        var error = _cloud.EventsFor(o.Grant!.SessionId).Single(e => e["kind"]!.GetValue<string>() == "error");
        Assert.Equal("voice_provider_error", error["payload"]!["error_class"]!.GetValue<string>());
        Assert.Contains("provider_error:rate_limit", o.Defects);
    }

    [Fact]
    public async Task A_final_transcript_is_reported_as_an_utterance_for_cloud_core_to_resolve()
    {
        var o = await StartAsync();
        Leg.Emit(new TranscriptDeltaEvent(_time.GetTimestamp(), "ikinci maddeyi tekrar oku", Final: true));

        Assert.True(await TestSupport.WaitForAsync(() => _cloud.EventKindsFor(o.Grant!.SessionId).Contains("utterance")));
        var utterance = _cloud.EventsFor(o.Grant!.SessionId).Single(e => e["kind"]!.GetValue<string>() == "utterance");
        Assert.Equal("ikinci maddeyi tekrar oku", utterance["text"]!.GetValue<string>());
        Assert.Empty(utterance["payload"]!.AsObject()); // the text travels in `text`, never in payload
        Assert.Equal(0, _cloud.Refusals);
    }

    public async ValueTask DisposeAsync()
    {
        if (_orchestrator is not null)
        {
            await _orchestrator.DisposeAsync();
        }

        _cloud.Dispose();
    }
}
