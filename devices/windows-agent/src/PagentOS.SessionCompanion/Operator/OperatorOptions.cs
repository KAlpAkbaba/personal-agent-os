using Microsoft.Extensions.Configuration;

namespace PagentOS.SessionCompanion.Operator;

/// <summary>
/// The companion's configuration for the Digital Operator (M19_DIGITAL_OPERATOR_SPEC.md §2,
/// §3), all <c>PAGENTOS_AGENT_</c>-prefixed and nothing machine-specific:
/// <list type="bullet">
/// <item><c>OperatorEnabled</c> — the family gate (default false). The Device Service has the
/// same key; both must be on for a command to reach a window.</item>
/// <item><c>TerminalAllowlist</c> — <c>;</c>-separated patterns for <c>terminal.execute</c>;
/// empty means <see cref="TerminalRunner.DefaultAllowlist"/>.</item>
/// <item><c>OperatorRoots</c> — <c>;</c>-separated directories the operator may open, reveal
/// and list; empty means the owner's profile directory.</item>
/// </list>
/// </summary>
public sealed record OperatorOptions(
    bool Enabled,
    IReadOnlyList<string> TerminalAllowlist,
    IReadOnlyList<string> AuthorisedRoots)
{
    public static OperatorOptions FromConfiguration(IConfiguration configuration)
    {
        var enabled = Program.ParseFlag(configuration["OperatorEnabled"]);
        var allowlist = SplitList(configuration["TerminalAllowlist"]);
        var roots = SplitList(configuration["OperatorRoots"]);
        return new OperatorOptions(
            enabled,
            allowlist.Count == 0 ? TerminalRunner.DefaultAllowlist : allowlist,
            roots.Count == 0 ? DefaultRoots() : roots);
    }

    /// <summary>The owner's profile directory: their documents, desktop, downloads and temp all live under it.</summary>
    public static IReadOnlyList<string> DefaultRoots()
        => [Environment.GetFolderPath(Environment.SpecialFolder.UserProfile)];

    private static IReadOnlyList<string> SplitList(string? raw)
        => string.IsNullOrWhiteSpace(raw)
            ? []
            : raw.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
}
