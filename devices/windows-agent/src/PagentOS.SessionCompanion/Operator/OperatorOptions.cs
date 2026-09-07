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
/// and list; empty means <see cref="DefaultRoots"/>: the owner's document folders, not the
/// profile.</item>
/// </list>
/// </summary>
public sealed record OperatorOptions(
    bool Enabled,
    IReadOnlyList<string> TerminalAllowlist,
    IReadOnlyList<string> AuthorisedRoots)
{
    /// <summary>The fixture folder the operator lab uses (M19_DIGITAL_OPERATOR_SPEC.md §5); a default root so the lab runs against the default configuration.</summary>
    public static string FixtureRoot => Path.Combine(Path.GetTempPath(), "pagentos-operator-fixture");

    private static readonly Guid DownloadsFolderId = new("374DE290-123F-4565-9164-39C4925E467B");

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

    /// <summary>
    /// The owner's document folders — Documents, Desktop, Downloads, Pictures, Videos, Music —
    /// and the operator fixture root. NOT the profile directory (ADR-0082 addendum 2, finding
    /// 1): the profile carries AppData (browser cookie stores, credentials, the agent's own
    /// state) and <c>.ssh</c>, none of which "open a file for the owner" needs. A folder the
    /// owner does not have is left out; one that does not exist yet (the fixture) is kept and
    /// admits nothing until it does.
    /// </summary>
    public static IReadOnlyList<string> DefaultRoots()
    {
        var roots = new List<string>();
        Add(roots, Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments));
        Add(roots, Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory));
        Add(roots, DownloadsFolder());
        Add(roots, Environment.GetFolderPath(Environment.SpecialFolder.MyPictures));
        Add(roots, Environment.GetFolderPath(Environment.SpecialFolder.MyVideos));
        Add(roots, Environment.GetFolderPath(Environment.SpecialFolder.MyMusic));
        Add(roots, FixtureRoot);
        return roots;
    }

    private static string? DownloadsFolder()
    {
        var known = OperatingSystem.IsWindows() ? OperatorNative.KnownFolderPath(DownloadsFolderId) : null;
        if (!string.IsNullOrWhiteSpace(known))
        {
            return known;
        }

        var profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        return string.IsNullOrWhiteSpace(profile) ? null : Path.Combine(profile, "Downloads");
    }

    private static void Add(List<string> roots, string? root)
    {
        if (!string.IsNullOrWhiteSpace(root) && !roots.Contains(root, StringComparer.OrdinalIgnoreCase))
        {
            roots.Add(root);
        }
    }

    private static IReadOnlyList<string> SplitList(string? raw)
        => string.IsNullOrWhiteSpace(raw)
            ? []
            : raw.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
}
