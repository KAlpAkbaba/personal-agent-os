using System.Collections.Concurrent;
using System.Runtime.Versioning;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Documents;
using PagentOS.SessionCompanion.Operator;

namespace PagentOS.SessionCompanion.Projects;

/// <summary>
/// The Projects root (M23_APP_FACTORY_SPEC.md §1, ADR-0086 decision 2): the ONE directory the
/// companion writes source into — <c>Documents\PagentOS Projects\&lt;slug&gt;</c>, or what
/// <c>ProjectsRoot</c> configures — and the marker that says a folder there is the assistant's
/// own. Every path here is RESOLVED then CONTAINED (<see cref="AuthorisedRoots"/>): the root
/// must itself resolve inside the authorised roots, a project folder must resolve directly
/// under the root, and a folder that lacks <see cref="MarkerFileName"/> — or carries another
/// project's id — is never written into, because it is the owner's.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class ProjectRoots
{
    /// <summary>The marker a scaffold writes last: <c>{project_id, slug, scaffolded_at, manifest}</c>. Its presence, with the right id, is what makes a folder writable.</summary>
    public const string MarkerFileName = ".pagentos-project.json";

    /// <summary>The companion's own state folder inside a project (the run and test logs); never part of a file list.</summary>
    public const string StateFolderName = ".pagentos";

    public const int MaxProjectIdChars = 128;

    /// <summary>How many entries of the Projects root a lookup by id will read markers from.</summary>
    public const int MaxScanEntries = 2000;

    /// <summary>M25: how many missing directories above a root <see cref="RequireRoot"/> will create on the way to it (the 3D root needs one — its parent, the Projects root).</summary>
    public const int MaxRootAncestors = 4;

    private const int MaxMarkerBytes = 64 * 1024;

    private static readonly Regex SlugPattern = new("^[a-z0-9][a-z0-9-]{0,63}$", RegexOptions.Compiled | RegexOptions.CultureInvariant);
    private static readonly Regex ProjectIdPattern = new("^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", RegexOptions.Compiled | RegexOptions.CultureInvariant);

    private readonly OperatorOptions _options;
    private readonly ConcurrentDictionary<string, string> _memo = new(StringComparer.Ordinal);

    public ProjectRoots(OperatorOptions options)
    {
        _options = options;
        Roots = new AuthorisedRoots(options.AuthorisedRoots);
    }

    /// <summary>The authorised roots the Projects root must lie inside.</summary>
    public AuthorisedRoots Roots { get; }

    /// <summary>The Projects root as configured (not yet resolved; it may not exist).</summary>
    public string? ConfiguredRoot => _options.EffectiveProjectsRoot;

    /// <summary>§1: a slug is a plain name — lower-case letters, digits and hyphens, at most 64 — never a path, never a reserved device name.</summary>
    public static void RequireSlug(string slug)
    {
        if (string.IsNullOrEmpty(slug) || slug.Length > ProjectCapabilityNames.MaxSlugChars || !SlugPattern.IsMatch(slug))
        {
            throw DocumentErrors.Invalid($"payload.slug must be a plain name of 1-{ProjectCapabilityNames.MaxSlugChars} characters from [a-z0-9-], starting with a letter or digit");
        }

        if (ProjectScaffold.IsReservedName(slug))
        {
            throw DocumentErrors.Invalid("payload.slug is a reserved device name");
        }
    }

    public static void RequireProjectId(string projectId)
    {
        if (string.IsNullOrEmpty(projectId) || projectId.Length > MaxProjectIdChars || !ProjectIdPattern.IsMatch(projectId))
        {
            throw DocumentErrors.Invalid($"payload.project_id must be 1-{MaxProjectIdChars} characters from [A-Za-z0-9._:-]");
        }
    }

    /// <summary>
    /// The Projects root, resolved — created when it does not exist yet, but only under a
    /// PARENT that resolves inside the authorised roots, and only when nothing else (a file, a
    /// link) squats the name. <c>permission_denied</c> otherwise: the family has no other
    /// place to write.
    /// </summary>
    public string RequireRoot()
    {
        var configured = ConfiguredRoot;
        if (string.IsNullOrWhiteSpace(configured))
        {
            throw DocumentErrors.Denied("this companion has no Projects root (no Documents folder and no ProjectsRoot configured); nothing was written");
        }

        string full;
        try
        {
            full = Path.GetFullPath(configured);
        }
        catch (Exception)
        {
            throw DocumentErrors.Denied("the configured Projects root is not a usable path; nothing was written");
        }

        if (!Directory.Exists(full))
        {
            if (File.Exists(full) || IsReparsePoint(full))
            {
                throw DocumentErrors.Denied("the Projects root's name is taken by something that is not a directory; nothing was written");
            }

            // The nearest ancestor that EXISTS must resolve inside the authorised roots; the
            // missing directories between it and the root are then created, outermost first,
            // each under a parent that was just resolved. M25 needs the walk: the 3D root's
            // parent is the Projects root, which may not exist either the first time a 3D
            // project is scaffolded, and a `<root>\3d` that could never be created would make
            // every 3D run impossible on a fresh machine.
            var missing = new List<string>();
            var ancestor = Path.GetDirectoryName(full);
            while (ancestor is not null && !Directory.Exists(ancestor) && missing.Count < MaxRootAncestors)
            {
                if (File.Exists(ancestor) || IsReparsePoint(ancestor))
                {
                    throw DocumentErrors.Denied("a directory above the Projects root's name is taken by something that is not a directory; nothing was written");
                }

                missing.Add(ancestor);
                ancestor = Path.GetDirectoryName(ancestor);
            }

            var existing = ancestor is null ? null : Roots.Confine(ancestor);
            if (existing is null || !Directory.Exists(existing))
            {
                throw DocumentErrors.Denied(Refusal("the Projects root's parent"));
            }

            var current = existing;
            for (var i = missing.Count - 1; i >= 0; i--)
            {
                current = Path.Combine(current, Path.GetFileName(missing[i]));
                Directory.CreateDirectory(current);
                current = Roots.Confine(current) ?? throw DocumentErrors.Denied(Refusal("the Projects root's parent"));
            }

            Directory.CreateDirectory(Path.Combine(current, Path.GetFileName(full)));
        }

        var resolved = Roots.Confine(full) ?? throw DocumentErrors.Denied(Refusal("the Projects root"));
        if (!Directory.Exists(resolved))
        {
            throw DocumentErrors.Denied(Refusal("the Projects root"));
        }

        return resolved;
    }

    /// <summary>The Projects root resolved and confined, or null — never created, never a refusal (for lookups and the terminal's token).</summary>
    public string? ResolveRootQuietly()
    {
        var configured = ConfiguredRoot;
        if (string.IsNullOrWhiteSpace(configured))
        {
            return null;
        }

        try
        {
            var resolved = Roots.Confine(Path.GetFullPath(configured));
            return resolved is not null && Directory.Exists(resolved) ? resolved : null;
        }
        catch (Exception)
        {
            return null;
        }
    }

    /// <summary>The resolved, confined project folder for an id — from this process's memo, else by reading the markers directly under the root; null when no such project exists.</summary>
    public string? Find(string projectId)
    {
        RequireProjectId(projectId);
        var root = ResolveRootQuietly();
        if (root is null)
        {
            return null;
        }

        if (_memo.TryGetValue(projectId, out var remembered))
        {
            var stillThere = ConfineProjectFolder(remembered, root);
            if (stillThere is not null && ReadMarker(stillThere)?.ProjectId == projectId)
            {
                return stillThere;
            }

            _memo.TryRemove(projectId, out _);
        }

        var scanned = 0;
        foreach (var entry in Directory.EnumerateDirectories(root))
        {
            if (++scanned > MaxScanEntries)
            {
                break;
            }

            if (IsReparsePoint(entry))
            {
                // A link planted in the root is never followed: it is not a project folder.
                continue;
            }

            var marker = ReadMarker(entry);
            if (marker is null || !string.Equals(marker.ProjectId, projectId, StringComparison.Ordinal))
            {
                continue;
            }

            var confined = ConfineProjectFolder(entry, root);
            if (confined is not null)
            {
                _memo[projectId] = confined;
                return confined;
            }
        }

        return null;
    }

    public void Remember(string projectId, string folder) => _memo[projectId] = folder;

    /// <summary>The folder resolved, and lying DIRECTLY under the resolved root (never deeper, never beside it); null otherwise.</summary>
    public static string? ConfineProjectFolder(string folder, string resolvedRoot)
    {
        var resolved = AuthorisedRoots.ResolveFinal(folder);
        if (resolved is null || !Directory.Exists(resolved) || !AuthorisedRoots.IsWithin(resolved, resolvedRoot))
        {
            return null;
        }

        var parent = Path.GetDirectoryName(resolved);
        return string.Equals(parent, resolvedRoot, StringComparison.OrdinalIgnoreCase) ? resolved : null;
    }

    /// <summary>
    /// M23 §4, the terminal's <c>&lt;project-entry&gt;</c> token: <paramref name="path"/> is
    /// a file that resolves under the Projects root, inside a folder that carries the
    /// marker, and is the file that folder's manifest names as <c>entry</c>. Everything else
    /// — the owner's own scripts, a project's other files, a link — is not.
    /// </summary>
    public bool IsEntry(string path)
    {
        try
        {
            var root = ResolveRootQuietly();
            if (root is null || string.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path))
            {
                return false;
            }

            var resolved = AuthorisedRoots.ResolveFinal(path);
            if (resolved is null || !File.Exists(resolved) || !AuthorisedRoots.IsWithin(resolved, root) || resolved.Length <= root.Length + 1)
            {
                return false;
            }

            var relative = resolved[(root.Length + 1)..];
            var separator = relative.IndexOf(Path.DirectorySeparatorChar);
            if (separator <= 0)
            {
                // A file directly in the root is nobody's entry.
                return false;
            }

            var folder = ConfineProjectFolder(Path.Combine(root, relative[..separator]), root);
            if (folder is null)
            {
                return false;
            }

            var marker = ReadMarker(folder);
            if (marker is null)
            {
                return false;
            }

            var manifest = ProjectManifest.Parse(marker.Manifest, filePaths: null);
            var entry = AuthorisedRoots.ResolveFinal(Path.Combine(folder, manifest.Entry.Replace('/', Path.DirectorySeparatorChar)));
            return entry is not null && string.Equals(entry, resolved, StringComparison.OrdinalIgnoreCase);
        }
        catch (Exception)
        {
            return false;
        }
    }

    /// <summary>The marker in <paramref name="folder"/>, or null when absent, unreadable, over 64 KiB or malformed (an unreadable marker is no marker: the folder is treated as the owner's).</summary>
    public static ProjectMarker? ReadMarker(string folder)
    {
        var path = Path.Combine(folder, MarkerFileName);
        try
        {
            var info = new FileInfo(path);
            if (!info.Exists || info.Length > MaxMarkerBytes || (info.Attributes & FileAttributes.ReparsePoint) != 0)
            {
                return null;
            }

            var node = JsonNode.Parse(File.ReadAllText(path, Encoding.UTF8)) as JsonObject;
            if (node is null)
            {
                return null;
            }

            var projectId = node["project_id"]?.GetValueKind() == JsonValueKind.String ? node["project_id"]!.GetValue<string>() : null;
            var slug = node["slug"]?.GetValueKind() == JsonValueKind.String ? node["slug"]!.GetValue<string>() : null;
            var scaffoldedAt = node["scaffolded_at"]?.GetValueKind() == JsonValueKind.String ? node["scaffolded_at"]!.GetValue<string>() : null;
            if (projectId is null || slug is null || node["manifest"] is not JsonObject manifest)
            {
                return null;
            }

            return new ProjectMarker(projectId, slug, scaffoldedAt ?? string.Empty, (JsonObject)manifest.DeepClone());
        }
        catch (Exception)
        {
            return null;
        }
    }

    public static void WriteMarker(string folder, ProjectMarker marker)
    {
        var json = new JsonObject
        {
            ["project_id"] = marker.ProjectId,
            ["slug"] = marker.Slug,
            ["scaffolded_at"] = marker.ScaffoldedAt,
            ["manifest"] = marker.Manifest.DeepClone(),
        };
        File.WriteAllText(Path.Combine(folder, MarkerFileName), json.ToJsonString(new JsonSerializerOptions { WriteIndented = true }) + "\n", new UTF8Encoding(false));
    }

    public static bool IsReparsePoint(string path)
    {
        try
        {
            return (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0;
        }
        catch (Exception)
        {
            return false;
        }
    }

    private string Refusal(string what)
        => $"{what} does not resolve to a directory inside the owner's authorised roots ({string.Join(";", _options.AuthorisedRoots)}); every junction and link is followed before the comparison; nothing was written";
}

/// <summary>What <see cref="ProjectRoots.MarkerFileName"/> records: the project this folder belongs to and the manifest the scaffold validated (the run commands come from HERE, never from a file the owner may have edited).</summary>
public sealed record ProjectMarker(string ProjectId, string Slug, string ScaffoldedAt, JsonObject Manifest);
