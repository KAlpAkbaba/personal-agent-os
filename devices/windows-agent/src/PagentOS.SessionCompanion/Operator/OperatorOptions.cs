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
/// <item><c>DownloadsRoot</c> (M22, DEVICE_PROTOCOL.md §6k) — the ONE directory
/// <c>file.fetch</c> writes into; empty means the owner's Downloads known folder. It must
/// itself resolve inside <c>OperatorRoots</c> at fetch time or every fetch is refused.</item>
/// <item><c>ProjectsRoot</c> (M23, DEVICE_PROTOCOL.md §6l) — the ONE directory the projects
/// family writes source into and runs from; empty means <see cref="DefaultProjectsRoot"/>
/// (<c>Documents\PagentOS Projects</c>). It is always one of the authorised roots
/// (<see cref="FromConfiguration"/> appends it), so <c>file.*</c> and the terminal's
/// project-scoped entry reach it, and it must itself resolve inside the roots at scaffold
/// time.</item>
/// <item><c>ProjectsRoot3d</c> (M25, DEVICE_PROTOCOL.md §6m) — the ONE directory the two 3D
/// runtimes may run under; empty means <c>&lt;ProjectsRoot&gt;\3d</c>. Like the Projects root
/// it is always one of the authorised roots and is resolved-then-contained on every use. The
/// owner's own project folders are never under it: an M23 project lives at
/// <c>&lt;ProjectsRoot&gt;\&lt;slug&gt;</c> and the owner's real work lives wherever the owner
/// put it — <c>blender</c> and <c>unity</c> are refused anywhere but here.</item>
/// </list>
/// </summary>
public sealed record OperatorOptions(
    bool Enabled,
    IReadOnlyList<string> TerminalAllowlist,
    IReadOnlyList<string> AuthorisedRoots,
    string? DownloadsRoot = null,
    string? ProjectsRoot = null,
    string? ProjectsRoot3d = null)
{
    /// <summary>The fixture folder the operator lab uses (M19_DIGITAL_OPERATOR_SPEC.md §5); a default root so the lab runs against the default configuration.</summary>
    public static string FixtureRoot => Path.Combine(Path.GetTempPath(), "pagentos-operator-fixture");

    /// <summary>M23: the folder name of the Projects root under the owner's Documents.</summary>
    public const string ProjectsFolderName = "PagentOS Projects";

    private static readonly Guid DownloadsFolderId = new("374DE290-123F-4565-9164-39C4925E467B");

    public static OperatorOptions FromConfiguration(IConfiguration configuration)
    {
        var enabled = Program.ParseFlag(configuration["OperatorEnabled"]);
        var allowlist = SplitList(configuration["TerminalAllowlist"]);
        var roots = SplitList(configuration["OperatorRoots"]);
        var downloads = configuration["DownloadsRoot"];
        var projects = configuration["ProjectsRoot"];
        var projects3d = configuration["ProjectsRoot3d"];
        var projectsRoot = string.IsNullOrWhiteSpace(projects) ? DefaultProjectsRoot() : projects.Trim();
        var projectsRoot3d = string.IsNullOrWhiteSpace(projects3d) ? Default3dRoot(projectsRoot) : projects3d.Trim();

        // M23: the Projects root is an authorised root whatever the owner configured — the
        // family writes there and nowhere else, and the operator must be able to open and
        // reveal what it wrote. An owner-configured root list gains exactly this one entry.
        // M25 adds the 3D root for the same reason: it is where the two 3D runtimes run, and
        // it is ALWAYS authorised even when it was configured somewhere the Projects root does
        // not contain.
        var effectiveRoots = new List<string>(roots.Count == 0 ? DefaultRoots() : roots);
        Add(effectiveRoots, projectsRoot);
        Add(effectiveRoots, projectsRoot3d);
        return new OperatorOptions(
            enabled,
            allowlist.Count == 0 ? TerminalRunner.DefaultAllowlist : allowlist,
            effectiveRoots,
            string.IsNullOrWhiteSpace(downloads) ? null : downloads.Trim(),
            projectsRoot,
            projectsRoot3d);
    }

    /// <summary>The directory <c>file.fetch</c> writes into: the configured one, else the owner's Downloads folder; null when the machine has neither.</summary>
    public string? EffectiveDownloadsRoot => string.IsNullOrWhiteSpace(DownloadsRoot) ? DownloadsFolder() : DownloadsRoot;

    /// <summary>The directory the projects family writes into: the configured one, else <c>Documents\PagentOS Projects</c>; null when the machine has no Documents folder.</summary>
    public string? EffectiveProjectsRoot => string.IsNullOrWhiteSpace(ProjectsRoot) ? DefaultProjectsRoot() : ProjectsRoot;

    /// <summary>M25: the directory the two 3D runtimes may run under — the configured one, else <c>&lt;Projects root&gt;\3d</c>; null when there is no Projects root at all.</summary>
    public string? EffectiveProjectsRoot3d => string.IsNullOrWhiteSpace(ProjectsRoot3d) ? Default3dRoot(EffectiveProjectsRoot) : ProjectsRoot3d;

    /// <summary><c>%USERPROFILE%\Documents\PagentOS Projects\3d</c> (M25_CREATIVE_3D_SPEC.md §1), or null when there is no Projects root.</summary>
    public static string? Default3dRoot(string? projectsRoot)
        => string.IsNullOrWhiteSpace(projectsRoot) ? null : Path.Combine(projectsRoot, Agent.Core.Protocol.SceneCapabilityNames.Root3dFolderName);

    /// <summary><c>%USERPROFILE%\Documents\PagentOS Projects</c> (M23_APP_FACTORY_SPEC.md §1), or null when the owner has no Documents folder.</summary>
    public static string? DefaultProjectsRoot()
    {
        var documents = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
        return string.IsNullOrWhiteSpace(documents) ? null : Path.Combine(documents, ProjectsFolderName);
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
        // M23: the Projects root, stated on its own even though Documents contains it — the
        // list is what the log and the refusal messages name. M25 adds the 3D root beneath it
        // for the same reason.
        Add(roots, DefaultProjectsRoot());
        Add(roots, Default3dRoot(DefaultProjectsRoot()));
        return roots;
    }

    /// <summary>The owner's Downloads known folder (the owner may have moved it), else <c>%USERPROFILE%\Downloads</c>, else null.</summary>
    public static string? DownloadsFolder()
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

    /// <summary>
    /// M25: these options seen from the 3D root — the same gate, allowlist and authorised
    /// roots, with <c>ProjectsRoot</c> pointing at <see cref="EffectiveProjectsRoot3d"/>. The
    /// scenes half builds its <c>ProjectRoots</c> from this, so a 3D project gets the M23
    /// resolve-then-contain, marker and id rules unchanged, one directory lower.
    /// </summary>
    public OperatorOptions Scene3dOptions()
    {
        var root3d = EffectiveProjectsRoot3d;
        var roots = new List<string>(AuthorisedRoots);
        Add(roots, root3d);
        return this with { AuthorisedRoots = roots, ProjectsRoot = root3d, ProjectsRoot3d = root3d };
    }

    /// <summary>These options with the projects root replaced (a lab points it inside its run directory); the 3D root follows it unless it was configured on its own.</summary>
    public OperatorOptions WithProjectsRoot(string projectsRoot)
    {
        var roots = new List<string>(AuthorisedRoots);
        Add(roots, projectsRoot);
        var root3d = string.IsNullOrWhiteSpace(ProjectsRoot3d) ? Default3dRoot(projectsRoot) : ProjectsRoot3d;
        Add(roots, root3d);
        return this with { AuthorisedRoots = roots, ProjectsRoot = projectsRoot, ProjectsRoot3d = root3d };
    }

    private static IReadOnlyList<string> SplitList(string? raw)
        => string.IsNullOrWhiteSpace(raw)
            ? []
            : raw.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
}
