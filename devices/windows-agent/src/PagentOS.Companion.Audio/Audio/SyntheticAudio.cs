namespace PagentOS.Companion.Audio.Audio;

/// <summary>
/// Deterministic test/bench signal source. Speech is stood in for by a tone; that is enough
/// for everything the client decides on energy (VAD, gate, prolongation) and keeps the offline
/// numbers reproducible. It is never a claim about real speech — that is the owner's run.
/// </summary>
public sealed class SyntheticAudio(AudioFormat format)
{
    private double _phase;

    public AudioFormat Format { get; } = format;

    /// <summary>A steady tone at <paramref name="amplitude"/> (0..1 of full scale).</summary>
    public AudioFrame Tone(int milliseconds, long capturedAt, double frequencyHz = 220, double amplitude = 0.3)
    {
        var frame = AudioFrame.Silence(Format, milliseconds, capturedAt);
        var samples = frame.Samples;
        var step = 2 * Math.PI * frequencyHz / Format.SampleRate;
        for (var i = 0; i < samples.Length; i++)
        {
            samples[i] = (short)Math.Round(Math.Sin(_phase) * amplitude * short.MaxValue);
            _phase += step;
        }

        return frame;
    }

    /// <summary>Low-level pseudo-random noise (room tone). Seeded, so identical across runs.</summary>
    public static AudioFrame Noise(AudioFormat format, int milliseconds, long capturedAt, double amplitude = 0.005, int seed = 7)
    {
        var frame = AudioFrame.Silence(format, milliseconds, capturedAt);
        var random = new Random(seed);
        var samples = frame.Samples;
        for (var i = 0; i < samples.Length; i++)
        {
            samples[i] = (short)Math.Round((random.NextDouble() * 2 - 1) * amplitude * short.MaxValue);
        }

        return frame;
    }
}
