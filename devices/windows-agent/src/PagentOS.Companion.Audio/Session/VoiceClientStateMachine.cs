using PagentOS.Companion.Audio.Turn;

namespace PagentOS.Companion.Audio.Session;

/// <summary>Mirrors M4's <c>RealtimeState</c> (services/api/app/voice/realtime.py) name for name.</summary>
public enum VoiceClientState
{
    Idle,
    Listening,
    AssistantSpeaking,
    ToolRunning,
    Interrupted,
    Closed,
}

public sealed record VoiceClientEvent(int Seq, string Kind, VoiceClientState State, long Timestamp, string Detail = "");

public readonly record struct BargeInDecision(bool ShouldBargeIn, bool StopWord);

/// <summary>
/// The client-side control FSM, kept faithful to the M4 coordination model the spec says
/// to preserve verbatim: "cut speech first, then latch". The machine records an ordered,
/// timestamped event log and carries no audio. What is new here versus M4 is only that the
/// cut is a real playback stop performed by the caller between
/// <see cref="OwnerSpeechStarted"/> and <see cref="BargeInLatched"/> — and the machine
/// refuses to latch a barge-in that was not preceded by a recorded cut.
/// </summary>
public sealed class VoiceClientStateMachine(TimeProvider time)
{
    public static readonly IReadOnlySet<string> StopWords = new HashSet<string>(StringComparer.Ordinal)
    {
        "dur", "kes", "sus", "yeter", "tamam dur",
    };

    private readonly object _sync = new();
    private readonly List<VoiceClientEvent> _events = new();
    private int _seq;
    private bool _cutRecordedThisOnset;

    public VoiceClientState State { get; private set; } = VoiceClientState.Idle;

    public int BargeInCount { get; private set; }

    public IReadOnlyList<VoiceClientEvent> Events
    {
        get
        {
            lock (_sync)
            {
                return _events.ToList();
            }
        }
    }

    public static bool IsStopWord(string? text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return false;
        }

        var folded = string.Join(' ', TurkishText.Tokens(text));
        return StopWords.Contains(folded);
    }

    public VoiceClientEvent AssistantStartSpeaking(string detail = "")
    {
        lock (_sync)
        {
            RequireOpen();
            State = VoiceClientState.AssistantSpeaking;
            return Emit("assistant_speech_started", detail);
        }
    }

    public VoiceClientEvent AssistantStopSpeaking(string detail = "")
    {
        lock (_sync)
        {
            RequireOpen();
            if (State == VoiceClientState.AssistantSpeaking)
            {
                State = VoiceClientState.Idle;
            }

            return Emit("assistant_speech_stopped", detail);
        }
    }

    public VoiceClientEvent StartToolCall(string detail = "")
    {
        lock (_sync)
        {
            RequireOpen();
            State = VoiceClientState.ToolRunning;
            return Emit("tool_call_started", detail);
        }
    }

    /// <summary>Short spoken progress while a tool runs; does not leave ToolRunning and stays interruptible.</summary>
    public VoiceClientEvent AssistantProgress(string detail = "")
    {
        lock (_sync)
        {
            RequireOpen();
            if (State != VoiceClientState.ToolRunning)
            {
                throw new InvalidOperationException("progress reports are only valid during a tool call");
            }

            return Emit("assistant_progress", detail);
        }
    }

    public VoiceClientEvent FinishToolCall(string detail = "")
    {
        lock (_sync)
        {
            RequireOpen();
            if (State == VoiceClientState.ToolRunning)
            {
                State = VoiceClientState.Idle;
            }

            return Emit("tool_call_finished", detail);
        }
    }

    /// <summary>
    /// Owner starts talking. Returns whether this is a barge-in — assistant audible, or a stop
    /// word from any active state — in which case the caller must stop playback, record the
    /// cut, cancel the provider, and only then latch. Otherwise the machine goes to Listening.
    /// </summary>
    public BargeInDecision OwnerSpeechStarted(string text = "")
    {
        lock (_sync)
        {
            RequireOpen();
            var stopWord = IsStopWord(text);
            _cutRecordedThisOnset = false;
            Emit("owner_speech_started", text);
            if (State == VoiceClientState.AssistantSpeaking || stopWord)
            {
                return new BargeInDecision(true, stopWord);
            }

            State = VoiceClientState.Listening;
            Emit("listening", text);
            return new BargeInDecision(false, false);
        }
    }

    /// <summary>Records that assistant audio has actually been cut — the latency-critical action, always first.</summary>
    public VoiceClientEvent AssistantSpeechCut(string reason)
    {
        lock (_sync)
        {
            RequireOpen();
            _cutRecordedThisOnset = true;
            return Emit("assistant_speech_cut", reason);
        }
    }

    public VoiceClientEvent BargeInLatched(bool stopWord, string detail = "")
    {
        lock (_sync)
        {
            RequireOpen();
            if (!_cutRecordedThisOnset)
            {
                throw new InvalidOperationException(
                    "barge_in cannot be latched before assistant speech has been cut (stop playback first)");
            }

            BargeInCount++;
            State = VoiceClientState.Interrupted;
            var bargeIn = Emit("barge_in", stopWord ? "dur" : detail);
            State = VoiceClientState.Listening;
            Emit("listening", detail);
            return bargeIn;
        }
    }

    public VoiceClientEvent OwnerSpeechEnded(string detail = "")
    {
        lock (_sync)
        {
            RequireOpen();
            if (State == VoiceClientState.Listening)
            {
                State = VoiceClientState.Idle;
            }

            return Emit("owner_speech_ended", detail);
        }
    }

    public VoiceClientEvent NetworkLost(string detail = "")
    {
        lock (_sync)
        {
            RequireOpen();
            return Emit("network_lost", detail);
        }
    }

    public VoiceClientEvent NetworkRestored(string detail = "")
    {
        lock (_sync)
        {
            RequireOpen();
            return Emit("network_restored", detail);
        }
    }

    public VoiceClientEvent Close()
    {
        lock (_sync)
        {
            State = VoiceClientState.Closed;
            return Emit("closed");
        }
    }

    public IReadOnlyList<string> EventKinds() => Events.Select(e => e.Kind).ToList();

    /// <summary>Events between the last owner-speech-start and the cut, as in M4; null if no barge-in happened.</summary>
    public int? BargeInLatencyEvents()
    {
        int? start = null;
        int? cut = null;
        foreach (var ev in Events)
        {
            if (ev.Kind == "owner_speech_started")
            {
                start = ev.Seq;
            }

            if (ev.Kind == "assistant_speech_cut" && start is not null)
            {
                cut = ev.Seq;
            }
        }

        return start is null || cut is null ? null : cut - start;
    }

    private VoiceClientEvent Emit(string kind, string detail = "")
    {
        var ev = new VoiceClientEvent(++_seq, kind, State, time.GetTimestamp(), detail);
        _events.Add(ev);
        return ev;
    }

    private void RequireOpen()
    {
        if (State == VoiceClientState.Closed)
        {
            throw new InvalidOperationException("session is closed");
        }
    }
}
