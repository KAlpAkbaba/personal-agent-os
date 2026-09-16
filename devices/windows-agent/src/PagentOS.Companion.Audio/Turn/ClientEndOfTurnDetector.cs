using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Audio.Processing;
using PagentOS.Companion.Audio.Audio.Vad;

namespace PagentOS.Companion.Audio.Turn;

public enum EndOfTurnMode
{
    /// <summary>The provider's server VAD ends turns; the client's detector only times events and feeds the guard's metrics.</summary>
    Server,

    /// <summary>The client ends turns (provider turn detection off) and commits the buffer itself; the hesitation guard is decisive.</summary>
    Client,
}

public enum TurnEventKind
{
    SpeechStarted,
    SpeechEnded,
}

public sealed record TurnEvent(
    TurnEventKind Kind,
    long Timestamp,
    double TrailingSilenceMs,
    int RequiredSilenceMs,
    int HesitationExtensionMs,
    string Reason);

/// <summary>
/// Frame-driven speech start/end with the hesitation guard in the loop. All durations are
/// accumulated from frame lengths, not wall-clock, so a test (or the offline bench) can feed
/// a minute of audio in a few milliseconds and get identical decisions.
/// </summary>
public sealed class ClientEndOfTurnDetector(EnergyVad vad, HesitationGuard guard, TimeProvider time)
{
    private const int TranscriptTailLength = 96;

    private readonly List<double> _runEnergies = new();
    private string? _transcriptTail;
    private double _silenceMs;
    private double _voicedRunMs;
    private double _lastVoicedRunMs;
    private double _lastVoicedRunStdDb;
    private bool _voicedNow;

    public bool InSpeech { get; private set; }

    public EnergyVad Vad => vad;

    public HesitationGuard Guard => guard;

    /// <summary>The VAD's verdict on the most recent frame (B47: the device gate keeps per-frame voicing).</summary>
    public VadDecision LastDecision { get; private set; }

    /// <summary>Provider transcription of the current turn so far (delta or final), newest last.</summary>
    public void ObserveTranscript(string text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }

        var combined = (_transcriptTail ?? string.Empty) + " " + text.Trim();
        _transcriptTail = combined.Length <= TranscriptTailLength ? combined : combined[^TranscriptTailLength..];
    }

    public HesitationVerdict CurrentVerdict()
        => guard.Evaluate(new HesitationContext(_transcriptTail, _lastVoicedRunMs, _lastVoicedRunStdDb));

    public TurnEvent? Process(AudioFrame frame, in AudioProcessingContext context)
    {
        var decision = vad.Process(frame, in context);
        LastDecision = decision;

        if (!InSpeech)
        {
            if (!decision.Onset)
            {
                return null;
            }

            InSpeech = true;
            _silenceMs = 0;
            _voicedRunMs = frame.DurationMs;
            _voicedNow = true;
            _runEnergies.Clear();
            _runEnergies.Add(decision.EnergyDbfs);
            _lastVoicedRunMs = 0;
            _lastVoicedRunStdDb = 0;
            return new TurnEvent(TurnEventKind.SpeechStarted, time.GetTimestamp(), 0, 0, 0, "onset");
        }

        if (decision.Voiced)
        {
            if (!_voicedNow)
            {
                _voicedNow = true;
                _voicedRunMs = 0;
                _runEnergies.Clear();
            }

            _voicedRunMs += frame.DurationMs;
            _runEnergies.Add(decision.EnergyDbfs);
            _silenceMs = 0;
            return null;
        }

        if (_voicedNow)
        {
            _voicedNow = false;
            _lastVoicedRunMs = _voicedRunMs;
            _lastVoicedRunStdDb = StandardDeviation(_runEnergies);
        }

        _silenceMs += frame.DurationMs;
        var verdict = CurrentVerdict();
        if (_silenceMs < verdict.RequiredTrailingSilenceMs)
        {
            return null;
        }

        var ended = new TurnEvent(
            TurnEventKind.SpeechEnded,
            time.GetTimestamp(),
            _silenceMs,
            verdict.RequiredTrailingSilenceMs,
            verdict.ExtensionMs,
            verdict.Reason);
        Reset();
        return ended;
    }

    public void Reset()
    {
        InSpeech = false;
        _silenceMs = 0;
        _voicedRunMs = 0;
        _voicedNow = false;
        _runEnergies.Clear();
        _transcriptTail = null;
        vad.Reset();
    }

    private static double StandardDeviation(IReadOnlyList<double> values)
    {
        if (values.Count < 2)
        {
            return 0;
        }

        var mean = values.Average();
        var variance = values.Sum(v => (v - mean) * (v - mean)) / values.Count;
        return Math.Sqrt(variance);
    }
}
