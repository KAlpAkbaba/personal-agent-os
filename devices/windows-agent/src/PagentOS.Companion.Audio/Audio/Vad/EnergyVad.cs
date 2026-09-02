using PagentOS.Companion.Audio.Audio.Processing;

namespace PagentOS.Companion.Audio.Audio.Vad;

public sealed record EnergyVadOptions(
    double SpeechOnsetDbfs = -38,
    double SpeechHoldDbfs = -46,
    int OnsetMs = 40,
    double PlaybackEchoMarginDb = 12);

public readonly record struct VadDecision(bool Voiced, double EnergyDbfs, bool Onset, bool Offset, double ThresholdDbfs);

/// <summary>
/// Client-side voice activity on frame energy with hysteresis. Its job is not end-of-turn —
/// the provider's server VAD owns that in the primary mode — but the two things only the
/// client can do fast: notice the owner has started talking so playback can be cut within a
/// frame, and feed the hesitation guard. While the assistant is audible through
/// loudspeakers, the onset threshold rises by <see cref="EnergyVadOptions.PlaybackEchoMarginDb"/>
/// so the speakers do not barge in on themselves; a headset path gets the plain threshold.
/// </summary>
public sealed class EnergyVad(EnergyVadOptions? options = null)
{
    private readonly EnergyVadOptions _options = options ?? new EnergyVadOptions();
    private double _aboveOnsetMs;

    public bool InSpeech { get; private set; }

    public EnergyVadOptions Options => _options;

    public VadDecision Process(AudioFrame frame, in AudioProcessingContext context)
    {
        var energy = AudioEnergy.Dbfs(frame.Samples);
        var onsetThreshold = _options.SpeechOnsetDbfs
            + (context.PlaybackActive && context.RenderIsLoudspeaker ? _options.PlaybackEchoMarginDb : 0);

        if (!InSpeech)
        {
            if (energy >= onsetThreshold)
            {
                _aboveOnsetMs += frame.DurationMs;
                if (_aboveOnsetMs >= _options.OnsetMs)
                {
                    InSpeech = true;
                    _aboveOnsetMs = 0;
                    return new VadDecision(true, energy, Onset: true, Offset: false, onsetThreshold);
                }
            }
            else
            {
                _aboveOnsetMs = 0;
            }

            return new VadDecision(false, energy, Onset: false, Offset: false, onsetThreshold);
        }

        if (energy < _options.SpeechHoldDbfs)
        {
            InSpeech = false;
            return new VadDecision(false, energy, Onset: false, Offset: true, _options.SpeechHoldDbfs);
        }

        return new VadDecision(true, energy, Onset: false, Offset: false, _options.SpeechHoldDbfs);
    }

    public void Reset()
    {
        InSpeech = false;
        _aboveOnsetMs = 0;
    }
}
