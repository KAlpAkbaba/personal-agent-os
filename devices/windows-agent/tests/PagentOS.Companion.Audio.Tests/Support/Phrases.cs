using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Listening.Spotting;

namespace PagentOS.Companion.Audio.Tests.Support;

/// <summary>
/// Deterministic stand-ins for spoken phrases: a phrase is a sequence of tone segments, said
/// with a speed and pitch variation and a little seeded noise. That is enough to exercise
/// everything the template engine decides (MFCC shape over time, DTW warping, thresholds,
/// margins) without a microphone. It is never a claim about real Turkish speech - that is the
/// owner's physical run (scripts/core/qualify-device-voice.ps1).
/// </summary>
public static class Phrases
{
    public static readonly (double Hz, int Ms)[] Wake = [(320, 160), (1250, 140), (640, 180)];
    public static readonly (double Hz, int Ms)[] Snooze = [(900, 140), (260, 180), (1500, 160)];
    public static readonly (double Hz, int Ms)[] Stop = [(2100, 150), (450, 150), (2100, 150)];
    public static readonly (double Hz, int Ms)[] ListeningOff = [(1800, 160), (700, 160), (380, 160)];
    public static readonly (double Hz, int Ms)[] Request = [(540, 200), (1650, 200), (820, 200), (1100, 200)];

    public static readonly AudioFormat Format = AudioFormat.Pcm16Mono24k;

    /// <summary>One continuous take of a phrase (or several joined without a pause), as 20 ms frames.</summary>
    public static IReadOnlyList<AudioFrame> Say(IEnumerable<(double Hz, int Ms)> phrase, double stretch = 1.0, double pitch = 1.0, int seed = 1, double amplitude = 0.3, Func<long>? clock = null)
    {
        var random = new Random(seed);
        var samples = new List<short>();
        double phase = 0;
        foreach (var (hz, ms) in phrase)
        {
            var count = Format.SamplesForMs((int)Math.Round(ms * stretch));
            var step = 2 * Math.PI * hz * pitch / Format.SampleRate;
            for (var i = 0; i < count; i++)
            {
                var value = Math.Sin(phase) * amplitude + (random.NextDouble() * 2 - 1) * 0.004;
                samples.Add((short)Math.Round(Math.Clamp(value, -1, 1) * short.MaxValue));
                phase += step;
            }
        }

        return ToFrames(samples, clock);
    }

    public static IReadOnlyList<AudioFrame> Quiet(int ms, Func<long>? clock = null)
        => Enumerable.Range(0, ms / 20).Select(_ => SyntheticAudio.Noise(Format, 20, clock?.Invoke() ?? 0, amplitude: 0.002)).ToList();

    /// <summary>A template set holding <paramref name="phrases"/>, each enrolled from three varied takes.</summary>
    public static KeywordTemplateSet Enrolled(params (string Id, (double Hz, int Ms)[] Phrase)[] phrases)
    {
        var set = new KeywordTemplateSet(Format.SampleRate);
        foreach (var (id, phrase) in phrases)
        {
            var takes = new[] { (0.9, 0.98, 11), (1.0, 1.02, 12), (1.1, 1.0, 13) };
            foreach (var (stretch, pitch, seed) in takes)
            {
                var result = KeywordEnrollment.FromRecording(
                    [.. Quiet(200), .. Say(phrase, stretch, pitch, seed), .. Quiet(200)],
                    Format);
                if (!result.Accepted)
                {
                    throw new InvalidOperationException($"synthetic enrollment of {id} was refused: {result.Detail}");
                }

                set.Add(id, result.Template!);
            }
        }

        return set;
    }

    public static float[][] Features(IReadOnlyList<AudioFrame> frames)
    {
        var extractor = new MfccExtractor(Format.SampleRate);
        var result = new List<float[]>();
        foreach (var frame in frames)
        {
            result.AddRange(extractor.Append(frame.Samples));
        }

        return [.. result];
    }

    private static IReadOnlyList<AudioFrame> ToFrames(List<short> samples, Func<long>? clock)
    {
        var perFrame = Format.SamplesForMs(20);
        var frames = new List<AudioFrame>();
        for (var offset = 0; offset < samples.Count; offset += perFrame)
        {
            var frame = AudioFrame.Silence(Format, 20, clock?.Invoke() ?? 0);
            var span = frame.Samples;
            for (var i = 0; i < perFrame && offset + i < samples.Count; i++)
            {
                span[i] = samples[offset + i];
            }

            frames.Add(frame);
        }

        return frames;
    }
}
