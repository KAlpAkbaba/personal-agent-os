using PagentOS.Companion.Audio.Session;
using PagentOS.Companion.Audio.Timing;
using Xunit;

namespace PagentOS.Companion.Audio.Tests;

public sealed class StateMachineTests
{
    private static VoiceClientStateMachine NewMachine() => new(new ManualTimeProvider());

    [Fact]
    public void Barge_in_records_cut_before_latch_and_returns_to_listening()
    {
        var fsm = NewMachine();
        fsm.AssistantStartSpeaking("resp-1");

        var decision = fsm.OwnerSpeechStarted();
        Assert.True(decision.ShouldBargeIn);
        Assert.False(decision.StopWord);

        fsm.AssistantSpeechCut("overlap");
        fsm.BargeInLatched(stopWord: false, "overlap");

        Assert.Equal(
            new[] { "assistant_speech_started", "owner_speech_started", "assistant_speech_cut", "barge_in", "listening" },
            fsm.EventKinds());
        Assert.Equal(VoiceClientState.Listening, fsm.State);
        Assert.Equal(1, fsm.BargeInCount);
        Assert.Equal(1, fsm.BargeInLatencyEvents());
        Assert.Equal(VoiceClientState.Interrupted, fsm.Events.Single(e => e.Kind == "barge_in").State);
    }

    [Fact]
    public void A_barge_in_cannot_be_latched_before_playback_was_cut()
    {
        var fsm = NewMachine();
        fsm.AssistantStartSpeaking();
        fsm.OwnerSpeechStarted();

        var ex = Assert.Throws<InvalidOperationException>(() => fsm.BargeInLatched(stopWord: false));
        Assert.Contains("stop playback first", ex.Message);
        Assert.Equal(0, fsm.BargeInCount);
    }

    [Theory]
    [InlineData("dur")]
    [InlineData("DUR")]
    [InlineData("Dur.")]
    [InlineData("tamam dur")]
    [InlineData("yeter")]
    [InlineData("kes")]
    [InlineData("sus")]
    public void Stop_words_interrupt_from_tool_running_state(string text)
    {
        var fsm = NewMachine();
        fsm.StartToolCall("research");
        fsm.AssistantProgress("bakıyorum");

        var decision = fsm.OwnerSpeechStarted(text);

        Assert.True(decision.ShouldBargeIn);
        Assert.True(decision.StopWord);
        fsm.AssistantSpeechCut("stop_word");
        var latched = fsm.BargeInLatched(stopWord: true, text);
        Assert.Equal("dur", latched.Detail);
    }

    [Theory]
    [InlineData("devam")]
    [InlineData("yarın toplantı var")]
    [InlineData("")]
    [InlineData("durum nedir")]
    public void Ordinary_speech_while_idle_goes_to_listening(string text)
    {
        var fsm = NewMachine();

        var decision = fsm.OwnerSpeechStarted(text);

        Assert.False(decision.ShouldBargeIn);
        Assert.Equal(VoiceClientState.Listening, fsm.State);
        fsm.OwnerSpeechEnded();
        Assert.Equal(VoiceClientState.Idle, fsm.State);
    }

    [Fact]
    public void Progress_is_only_valid_while_a_tool_runs()
    {
        var fsm = NewMachine();
        Assert.Throws<InvalidOperationException>(() => fsm.AssistantProgress("x"));
        fsm.StartToolCall();
        fsm.AssistantProgress("bakıyorum");
        Assert.Equal(VoiceClientState.ToolRunning, fsm.State);
        fsm.FinishToolCall();
        Assert.Equal(VoiceClientState.Idle, fsm.State);
    }

    [Fact]
    public void Network_loss_keeps_state_and_a_closed_session_refuses_everything()
    {
        var fsm = NewMachine();
        fsm.AssistantStartSpeaking();
        fsm.NetworkLost("ws");
        Assert.Equal(VoiceClientState.AssistantSpeaking, fsm.State);
        fsm.NetworkRestored();
        fsm.Close();
        Assert.Equal(VoiceClientState.Closed, fsm.State);
        Assert.Throws<InvalidOperationException>(() => fsm.OwnerSpeechStarted());
    }

    [Fact]
    public void Events_carry_monotonic_timestamps_from_the_time_provider()
    {
        var time = new ManualTimeProvider();
        var fsm = new VoiceClientStateMachine(time);
        fsm.AssistantStartSpeaking();
        time.AdvanceMs(12.5);
        fsm.AssistantStopSpeaking();

        var events = fsm.Events;
        Assert.Equal(TimeSpan.FromMilliseconds(12.5), time.GetElapsedTime(events[0].Timestamp, events[1].Timestamp));
        Assert.Equal(new[] { 1, 2 }, events.Select(e => e.Seq));
    }
}
