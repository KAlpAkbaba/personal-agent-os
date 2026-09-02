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

        var names = _cloud.EventNamesFor(o.Grant!.SessionId);
        Assert.Equal(new[] { "speech_started", "mic_uplink", "speech_ended", "first_audio" }, names);
        var events = _cloud.EventsFor(o.Grant.SessionId);
        Assert.True(events[1]["data"]!["mic_to_uplink_ms"]!.GetValue<double>() >= 0);
        Assert.Equal("none", events[2]["data"]!["hesitation_reason"]!.GetValue<string>());
        Assert.True(events[3]["data"]!["eot_to_first_audio_ms"]!.GetValue<double>() >= 0);

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

        Assert.Equal(new[] { "playback:stop", "leg:cancel", "report:barge_in", "report:playback_stopped", "report:speech_started" }, _trace.Take(5));
        Assert.False(Playback.IsPlaying);
        Assert.Equal(VoiceClientState.Listening, o.Fsm.State);
        var kinds = o.Fsm.EventKinds().ToList();
        Assert.True(kinds.IndexOf("assistant_speech_cut") < kinds.IndexOf("barge_in"));
        var bargeIn = reporter.Reports.Single(r => r.Event == VoiceClientEvents.BargeIn).Data;
        Assert.True(bargeIn["playback_stopped_ms"]!.GetValue<double>() >= 0);
        Assert.True(bargeIn["discarded_ms"]!.GetValue<int>() > 0);
        Assert.True(reporter.Reports.Single(r => r.Event == VoiceClientEvents.SpeechStarted).Data["barge_in"]!.GetValue<bool>());

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
        Assert.True(_cloud.EventsFor(o.Grant!.SessionId).Single(e => e["event"]!.GetValue<string>() == "barge_in")["data"]!["stop_word"]!.GetValue<bool>());
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
        Assert.Equal(new[] { "tool_call_relayed", "tool_result_submitted", "tool_result_submitted" }, _cloud.EventNamesFor(o.Grant!.SessionId));
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
        var firstCredential = o.Grant!.CredentialValue();

        firstLeg.EmitDisconnect("wifi dropped");

        Assert.True(await TestSupport.WaitForAsync(() => o.ReconnectCount == 1, 5000));
        Assert.Equal(2, _legs.Count);
        Assert.True(Leg.IsOpen);
        Assert.NotSame(firstLeg, Leg);
        Assert.Equal(1, _cloud.AttachRequests);
        Assert.NotEqual(firstCredential, o.Grant.CredentialValue());
        Assert.True(await TestSupport.WaitForAsync(() => _cloud.EventNamesFor(o.Grant.SessionId).Contains("network_restored")));
        Assert.Equal(new[] { "network_lost", "network_restored" }, _cloud.EventNamesFor(o.Grant.SessionId));
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

    public async ValueTask DisposeAsync()
    {
        if (_orchestrator is not null)
        {
            await _orchestrator.DisposeAsync();
        }

        _cloud.Dispose();
    }
}
