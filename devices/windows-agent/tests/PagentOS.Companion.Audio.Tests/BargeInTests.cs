using PagentOS.Companion.Audio.Fakes;
using PagentOS.Companion.Audio.Media;
using PagentOS.Companion.Audio.Session;
using PagentOS.Companion.Audio.Sideband;
using PagentOS.Companion.Audio.Timing;
using Xunit;

namespace PagentOS.Companion.Audio.Tests;

public sealed class BargeInTests
{
    private static (BargeInController Controller, FakePlayback Playback, FakeMediaLeg Leg, RecordingEventReporter Reporter, VoiceClientStateMachine Fsm, List<string> Trace, ManualTimeProvider Time) Build(double stopMs = 0)
    {
        var time = new ManualTimeProvider();
        var trace = new List<string>();
        var playback = new FakePlayback("ren", Support.TestSupport.Format, time, trace) { SimulatedStopMs = stopMs };
        var leg = new FakeMediaLeg(time)
        {
            OnCommand = (command, _) =>
            {
                if (command is CancelResponseCommand)
                {
                    trace.Add("leg:cancel");
                }

                return Task.CompletedTask;
            },
        };
        var reporter = new RecordingEventReporter(time, trace);
        var fsm = new VoiceClientStateMachine(time);
        var controller = new BargeInController(() => playback, () => leg, reporter, fsm, time);
        return (controller, playback, leg, reporter, fsm, trace, time);
    }

    [Fact]
    public async Task Playback_stops_first_then_provider_is_cancelled_then_the_report_goes_out()
    {
        var (controller, playback, leg, reporter, fsm, trace, _) = Build();
        await leg.OpenAsync(Grant(), new MediaLegOptions(Support.TestSupport.Format, Turn.EndOfTurnMode.Server), CancellationToken.None);
        playback.Start();
        playback.Enqueue(new byte[Support.TestSupport.Format.BytesForMs(400)]);
        fsm.AssistantStartSpeaking();
        fsm.OwnerSpeechStarted();

        var outcome = await controller.ExecuteAsync(stopWord: false, "overlap", CancellationToken.None);

        Assert.Equal(new[] { "playback:stop", "leg:cancel", "report:barge_in", "report:playback_stopped" }, trace);
        Assert.False(playback.IsPlaying);
        Assert.Equal(400, outcome.Stop.DiscardedMs);
        Assert.Equal(VoiceClientState.Listening, fsm.State);
        Assert.Contains("assistant_speech_cut", fsm.EventKinds());
        var kinds = fsm.EventKinds().ToList();
        Assert.True(kinds.IndexOf("assistant_speech_cut") < kinds.IndexOf("barge_in"));
        Assert.Equal(new[] { VoiceClientEvents.BargeIn, VoiceClientEvents.PlaybackStopped }, reporter.EventNames);
    }

    [Fact]
    public async Task The_reported_playback_stopped_latency_is_the_measured_stop_cost()
    {
        var (controller, playback, leg, reporter, fsm, _, _) = Build(stopMs: 3.25);
        await leg.OpenAsync(Grant(), new MediaLegOptions(Support.TestSupport.Format, Turn.EndOfTurnMode.Server), CancellationToken.None);
        playback.Start();
        playback.Enqueue(new byte[Support.TestSupport.Format.BytesForMs(100)]);
        fsm.AssistantStartSpeaking();
        fsm.OwnerSpeechStarted();

        var outcome = await controller.ExecuteAsync(stopWord: false, "overlap", CancellationToken.None);

        Assert.Equal(3.25, outcome.PlaybackStoppedMs, precision: 3);
        var report = reporter.Reports.Single(r => r.Event == VoiceClientEvents.BargeIn).Data;
        Assert.Equal(3.25, report["playback_stopped_ms"]!.GetValue<double>(), precision: 3);
        Assert.Equal(100, report["discarded_ms"]!.GetValue<int>());
        Assert.Equal(playback.SimulatedResidualLatencyMs, report["residual_latency_ms"]!.GetValue<int>());
        Assert.False(report["stop_word"]!.GetValue<bool>());
    }

    [Fact]
    public async Task A_failed_provider_cancel_does_not_undo_the_stop_or_skip_the_report()
    {
        var (controller, playback, leg, reporter, fsm, trace, _) = Build();
        await leg.OpenAsync(Grant(), new MediaLegOptions(Support.TestSupport.Format, Turn.EndOfTurnMode.Server), CancellationToken.None);
        playback.Start();
        playback.Enqueue(new byte[Support.TestSupport.Format.BytesForMs(50)]);
        fsm.AssistantStartSpeaking();
        fsm.OwnerSpeechStarted("dur");
        leg.SendsFail = true;

        var outcome = await controller.ExecuteAsync(stopWord: true, "dur", CancellationToken.None);

        Assert.Equal("playback:stop", trace[0]);
        Assert.False(playback.IsPlaying);
        Assert.True(outcome.StopWord);
        Assert.Equal(1, fsm.BargeInCount);
        Assert.Contains(VoiceClientEvents.BargeIn, reporter.EventNames);
        Assert.Equal("stop_word", reporter.Reports.Single(r => r.Event == VoiceClientEvents.PlaybackStopped).Data["reason"]!.GetValue<string>());
    }

    private static RealtimeSessionGrant Grant() => new(
        "rts-1", "fake", "fake", new System.Text.Json.Nodes.JsonObject { ["value"] = "ek" }, null, null, null, null);
}
