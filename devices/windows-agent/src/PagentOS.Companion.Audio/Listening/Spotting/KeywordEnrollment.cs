using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Audio.Processing;

namespace PagentOS.Companion.Audio.Listening.Spotting;

/// <summary>What one enrollment recording produced.</summary>
public sealed record EnrollmentResult(bool Accepted, string Detail, KeywordTemplate? Template, double SpokenMs);

/// <summary>
/// Turns one recording of a phrase into a template: the silence around it is trimmed by
/// energy, the phrase must be plausibly a phrase (not a cough, not a paragraph), and only its
/// normalised MFCC frames survive. The recording's samples are zeroed before this returns -
/// enrollment keeps no audio either.
/// </summary>
public static class KeywordEnrollment
{
    public const int MinSpokenMs = 200;

    /// <summary>A block this far under the loudest block is not part of the phrase.</summary>
    public const double TrimBelowPeakDb = 30;

    public const double AbsoluteFloorDbfs = -50;

    private const int BlockMs = 10;

    public static EnrollmentResult FromRecording(IReadOnlyList<AudioFrame> recording, AudioFormat format)
    {
        try
        {
            var samples = new List<short>();
            foreach (var frame in recording)
            {
                foreach (var sample in frame.Samples)
                {
                    samples.Add(sample);
                }
            }

            var all = samples.ToArray();
            try
            {
                return Extract(all, format);
            }
            finally
            {
                Array.Clear(all);
            }
        }
        finally
        {
            foreach (var frame in recording)
            {
                Array.Clear(frame.Pcm16);
            }
        }
    }

    private static EnrollmentResult Extract(short[] samples, AudioFormat format)
    {
        var block = format.SamplesForMs(BlockMs) * format.Channels;
        if (block == 0 || samples.Length < block)
        {
            return new EnrollmentResult(false, "no_audio", null, 0);
        }

        var blocks = samples.Length / block;
        var energies = new double[blocks];
        for (var b = 0; b < blocks; b++)
        {
            energies[b] = AudioEnergy.Dbfs(samples.AsSpan(b * block, block));
        }

        var peak = energies.Max();
        if (peak < AbsoluteFloorDbfs)
        {
            return new EnrollmentResult(false, "too_quiet", null, 0);
        }

        var floor = Math.Max(AbsoluteFloorDbfs, peak - TrimBelowPeakDb);
        var first = Array.FindIndex(energies, e => e >= floor);
        var last = Array.FindLastIndex(energies, e => e >= floor);
        var spokenMs = (last - first + 1) * BlockMs;
        if (spokenMs < MinSpokenMs)
        {
            return new EnrollmentResult(false, "too_short", null, spokenMs);
        }

        if (spokenMs > DeviceVoiceContract.MaxSegmentMs)
        {
            return new EnrollmentResult(false, "too_long", null, spokenMs);
        }

        var phrase = samples.AsSpan(first * block, (last - first + 1) * block);
        var frames = MfccExtractor.Extract(phrase, format.SampleRate);
        if (frames.Length < 5)
        {
            return new EnrollmentResult(false, "too_short", null, spokenMs);
        }

        return new EnrollmentResult(true, "ok", new KeywordTemplate(MfccExtractor.Normalise(frames)), spokenMs);
    }

    /// <summary>How consistent a phrase's recordings are, as the spotter will judge them.</summary>
    public static IReadOnlyDictionary<string, double?> Thresholds(KeywordTemplateSet set)
    {
        var spotter = new TemplateKeywordSpotter(set);
        return set.PhraseIds.ToDictionary(p => p, p => spotter.ThresholdFor(p), StringComparer.Ordinal);
    }
}
