namespace PagentOS.Companion.Audio.Listening.Spotting;

/// <summary>A phrase the spotter recognised, with the numbers it decided on.</summary>
public sealed record KeywordHit(string PhraseId, double Distance, double Threshold, int EndFrame);

/// <summary>
/// The offline phrase engine behind the wake word (row 241) and the offline command set
/// (row 253). A provider interface like every other voice component: the shipped engine is
/// <see cref="TemplateKeywordSpotter"/>; a better offline engine (a Turkish recogniser, if one
/// ever becomes installable without an account) replaces it here and nowhere else.
/// </summary>
public interface IKeywordSpotter
{
    string Engine { get; }

    /// <summary>The sample rate the features must be computed at.</summary>
    int SampleRate { get; }

    IReadOnlyCollection<string> AvailablePhrases { get; }

    /// <summary>Why a phrase is not available, or null when every enrolled phrase is.</summary>
    string? UnavailableReason { get; }

    bool IsAvailable(string phraseId);

    /// <summary>Does the utterance BEGIN with <paramref name="phraseId"/>? Features are raw (not normalised).</summary>
    KeywordHit? SpotPrefix(IReadOnlyList<float[]> features, string phraseId);

    /// <summary>Which one of <paramref name="phraseIds"/> is the whole utterance, if any.</summary>
    KeywordHit? Classify(IReadOnlyList<float[]> features, IReadOnlyCollection<string> phraseIds);
}

/// <summary>The engine for a device with nothing enrolled: honest about it, matches nothing.</summary>
public sealed class NoKeywordSpotter(string reason, int sampleRate) : IKeywordSpotter
{
    public string Engine => DeviceVoiceContract.OfflineEngine;

    public int SampleRate { get; } = sampleRate;

    public IReadOnlyCollection<string> AvailablePhrases => [];

    public string? UnavailableReason { get; } = reason;

    public bool IsAvailable(string phraseId) => false;

    public KeywordHit? SpotPrefix(IReadOnlyList<float[]> features, string phraseId) => null;

    public KeywordHit? Classify(IReadOnlyList<float[]> features, IReadOnlyCollection<string> phraseIds) => null;
}

/// <summary>
/// Speaker-dependent template matching: MFCC frames compared by DTW against the owner's own
/// enrolled recordings. Each phrase's acceptance threshold is MEASURED from its templates
/// (the largest distance between two of them, scaled by <see cref="ThresholdScale"/>), so a
/// phrase the owner says consistently gets a tight threshold and nobody tunes a number by hand.
/// </summary>
/// <remarks>
/// It matches words. It does not identify anyone and must never be read as doing so: a
/// recording of the owner, or a similar voice, matches just as well. That is why the command
/// set it can trigger is limited to things that only stop, defer or report.
/// </remarks>
public sealed class TemplateKeywordSpotter : IKeywordSpotter
{
    public const double DefaultThresholdScale = 1.25;

    /// <summary>A command is accepted only when the runner-up phrase is at least this much further away.</summary>
    public const double DefaultMargin = 1.15;

    private readonly KeywordTemplateSet _set;
    private readonly Dictionary<string, double> _thresholds = new(StringComparer.Ordinal);
    private readonly Dictionary<string, float[][][]> _normalised = new(StringComparer.Ordinal);

    public TemplateKeywordSpotter(KeywordTemplateSet set, double thresholdScale = DefaultThresholdScale, double margin = DefaultMargin)
    {
        _set = set;
        ThresholdScale = thresholdScale;
        Margin = margin;
        var missing = new List<string>();
        foreach (var phrase in set.PhraseIds)
        {
            var templates = set.For(phrase).Select(t => MfccExtractor.Normalise(t.Frames)).ToArray();
            if (templates.Length < KeywordTemplateSet.MinTemplatesPerPhrase)
            {
                missing.Add(phrase);
                continue;
            }

            var worst = 0.0;
            for (var i = 0; i < templates.Length; i++)
            {
                for (var j = i + 1; j < templates.Length; j++)
                {
                    var distance = Dtw.Distance(templates[i], templates[j]);
                    if (double.IsFinite(distance))
                    {
                        worst = Math.Max(worst, distance);
                    }
                }
            }

            if (worst <= 0)
            {
                missing.Add(phrase);
                continue;
            }

            _normalised[phrase] = templates;
            _thresholds[phrase] = worst * thresholdScale;
        }

        UnavailableReason = missing.Count == 0
            ? null
            : "fewer than " + KeywordTemplateSet.MinTemplatesPerPhrase + " consistent recordings: " + string.Join(",", missing.OrderBy(m => m, StringComparer.Ordinal));
    }

    public string Engine => DeviceVoiceContract.OfflineEngine;

    public int SampleRate => _set.SampleRate;

    public double ThresholdScale { get; }

    public double Margin { get; }

    public IReadOnlyCollection<string> AvailablePhrases => _thresholds.Keys;

    public string? UnavailableReason { get; }

    public bool IsAvailable(string phraseId) => _thresholds.ContainsKey(phraseId);

    public double? ThresholdFor(string phraseId) => _thresholds.TryGetValue(phraseId, out var t) ? t : null;

    public KeywordHit? SpotPrefix(IReadOnlyList<float[]> features, string phraseId)
    {
        if (!_normalised.TryGetValue(phraseId, out var templates) || features.Count == 0)
        {
            return null;
        }

        var longest = templates.Max(t => t.Length);
        var window = features.Take(Math.Min(features.Count, (int)Math.Ceiling(longest * 1.6))).ToArray();

        // The mean is taken over the stretch the phrase can occupy, not the whole window: the
        // words AFTER the wake word must not shift the wake word's own colouring.
        var input = NormaliseOver(window, Math.Min(window.Length, longest));
        KeywordHit? best = null;
        foreach (var template in templates)
        {
            var (distance, end) = Dtw.PrefixDistance(template, input);
            if (double.IsFinite(distance) && (best is null || distance < best.Distance))
            {
                best = new KeywordHit(phraseId, distance, _thresholds[phraseId], end);
            }
        }

        return best is not null && best.Distance <= best.Threshold ? best : null;
    }

    public KeywordHit? Classify(IReadOnlyList<float[]> features, IReadOnlyCollection<string> phraseIds)
    {
        if (features.Count == 0)
        {
            return null;
        }

        var input = MfccExtractor.Normalise(features);
        var scores = new List<(string Phrase, double Distance)>();
        foreach (var phrase in phraseIds)
        {
            if (!_normalised.TryGetValue(phrase, out var templates))
            {
                continue;
            }

            var bestForPhrase = templates.Min(t => Dtw.Distance(t, input));
            if (double.IsFinite(bestForPhrase))
            {
                scores.Add((phrase, bestForPhrase));
            }
        }

        if (scores.Count == 0)
        {
            return null;
        }

        scores.Sort((a, b) => a.Distance.CompareTo(b.Distance));
        var (winner, distance) = scores[0];
        var threshold = _thresholds[winner];
        if (distance > threshold)
        {
            return null;
        }

        if (scores.Count > 1 && scores[1].Distance <= distance * Margin)
        {
            // Two phrases fit almost equally well: acting on either would be a guess.
            return null;
        }

        return new KeywordHit(winner, distance, threshold, features.Count - 1);
    }

    private static float[][] NormaliseOver(IReadOnlyList<float[]> frames, int meanFrames)
    {
        var dims = frames[0].Length;
        var mean = new double[dims];
        for (var i = 0; i < meanFrames; i++)
        {
            for (var d = 0; d < dims; d++)
            {
                mean[d] += frames[i][d];
            }
        }

        for (var d = 0; d < dims; d++)
        {
            mean[d] /= Math.Max(1, meanFrames);
        }

        return frames.Select(f =>
        {
            var normalised = new float[dims];
            for (var d = 0; d < dims; d++)
            {
                normalised[d] = (float)(f[d] - mean[d]);
            }

            return normalised;
        }).ToArray();
    }
}
