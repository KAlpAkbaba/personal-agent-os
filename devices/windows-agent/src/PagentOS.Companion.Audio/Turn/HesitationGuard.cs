namespace PagentOS.Companion.Audio.Turn;

public sealed record HesitationGuardOptions
{
    /// <summary>Silence after speech that ends a turn when nothing suggests the owner is mid-thought.</summary>
    public int BaseTrailingSilenceMs { get; init; } = 500;

    /// <summary>Extra silence tolerated after a filler ("şey", "yani", "hani", "ııı").</summary>
    public int FillerExtensionMs { get; init; } = 900;

    /// <summary>Extra silence tolerated after a connective that promises more ("ve", "ama", "çünkü").</summary>
    public int ConnectiveExtensionMs { get; init; } = 600;

    /// <summary>Extra silence tolerated after an acoustically drawn-out final sound ("şeeey…").</summary>
    public int ProlongationExtensionMs { get; init; } = 500;

    /// <summary>A final voiced run at least this long, with steady energy, counts as prolongation.</summary>
    public int ProlongationMinMs { get; init; } = 350;

    public double ProlongationMaxEnergyStdDb { get; init; } = 3.0;

    /// <summary>Whatever the evidence, a turn ends after this much silence.</summary>
    public int MaxTrailingSilenceMs { get; init; } = 2500;

    public IReadOnlySet<string> Fillers { get; init; } = new HashSet<string>(StringComparer.Ordinal)
    {
        "şey", "yani", "hani", "ııı", "ıı", "eee", "ee", "hmm", "hm", "mmm", "şöyle", "işte", "böyle", "yok",
    };

    public IReadOnlySet<string> Connectives { get; init; } = new HashSet<string>(StringComparer.Ordinal)
    {
        "ve", "ama", "fakat", "çünkü", "yoksa", "veya", "ya", "da", "de", "ile", "sonra", "ki", "ancak", "hem", "için",
    };
}

/// <summary>Evidence available at the moment the silence clock is consulted.</summary>
public readonly record struct HesitationContext(
    string? TranscriptTail,
    double LastVoicedRunMs,
    double LastVoicedRunEnergyStdDb);

public sealed record HesitationVerdict(int RequiredTrailingSilenceMs, int ExtensionMs, string Reason);

/// <summary>
/// The Turkish hesitation guard (M12 spec §5). Given what the owner last said and how the
/// last sound ended, it says how much trailing silence must pass before the client treats
/// the turn as over. Normal Turkish hesitation — "şey…", "yani…", a drawn vowel, a sentence
/// left hanging on "ve" — buys extra time; a plain end does not. Pure function of its
/// inputs: no clock, no audio, so every rule is tested in isolation.
/// </summary>
public sealed class HesitationGuard(HesitationGuardOptions? options = null)
{
    public HesitationGuardOptions Options { get; } = options ?? new HesitationGuardOptions();

    public HesitationVerdict Evaluate(in HesitationContext context)
    {
        var extension = 0;
        var reasons = new List<string>(3);

        var tokens = TurkishText.Tokens(context.TranscriptTail);
        var last = tokens.Count > 0 ? tokens[^1] : null;
        if (last is not null)
        {
            var bigram = tokens.Count > 1 ? tokens[^2] + " " + last : null;
            if (Options.Fillers.Contains(last) || (bigram is not null && Options.Fillers.Contains(bigram)) || TurkishText.IsElongated(last))
            {
                extension += Options.FillerExtensionMs;
                reasons.Add("filler:" + last);
            }
            else if (Options.Connectives.Contains(last) || (bigram is not null && Options.Connectives.Contains(bigram)))
            {
                extension += Options.ConnectiveExtensionMs;
                reasons.Add("connective:" + last);
            }
        }

        if (context.LastVoicedRunMs >= Options.ProlongationMinMs
            && context.LastVoicedRunEnergyStdDb <= Options.ProlongationMaxEnergyStdDb)
        {
            extension += Options.ProlongationExtensionMs;
            reasons.Add("prolongation");
        }

        var required = Math.Min(Options.BaseTrailingSilenceMs + extension, Options.MaxTrailingSilenceMs);
        return new HesitationVerdict(required, required - Options.BaseTrailingSilenceMs, reasons.Count == 0 ? "none" : string.Join("+", reasons));
    }
}
