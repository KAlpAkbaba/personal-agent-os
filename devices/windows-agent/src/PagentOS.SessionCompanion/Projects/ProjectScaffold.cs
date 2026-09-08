using System.Runtime.Versioning;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Documents;
using PagentOS.SessionCompanion.Operator;

namespace PagentOS.SessionCompanion.Projects;

/// <summary>One file of a generated set: a normalised relative path (<c>/</c> separators) and its text.</summary>
public sealed record ProjectFile(string Path, string Text);

/// <summary>A validated <c>project.scaffold</c> payload.</summary>
public sealed record ScaffoldRequest(string ProjectId, string Slug, IReadOnlyList<ProjectFile> Files, ProjectManifest Manifest);

/// <summary>What a scaffold wrote: the resolved project folder and the SHA-256 of every file's bytes, by path.</summary>
public sealed record ScaffoldOutcome(string Folder, IReadOnlyList<(string Path, string Sha256)> Files);

/// <summary>
/// <c>project.scaffold</c> (M23_APP_FACTORY_SPEC.md §2/§3, ADR-0086 decisions 1 and 2): the
/// file list is validated whole before a byte is written — ≤ 200 files, ≤ 2 MiB of UTF-8,
/// text only (no NUL, no control characters but tab, newline and return; well-formed
/// UTF-16), every path relative with no <c>..</c>, no absolute or UNC form, no reserved
/// device name, no trailing dot or space, no launcher extension (a project may be
/// JavaScript, so <c>.js</c> is allowed; <c>.bat</c>, <c>.cmd</c>, <c>.ps1</c>, <c>.lnk</c>,
/// the macro-enabled Office family and the rest of <see cref="OperatorCapabilities.ExecutableExtensions"/>
/// are not), never the marker, never under <c>.pagentos/</c>, no two paths that differ only
/// in case, and no path the family's forbidden-key scan would refuse as a result key. Then
/// the folder <c>Projects\&lt;slug&gt;</c> is created or reused — reused ONLY when it carries
/// the marker with this project's id; an owner's folder (no marker, or another id) is
/// <c>permission_denied</c> and untouched — every directory the files land in is RESOLVED
/// and must lie inside the project folder (a link planted inside a project cannot redirect
/// a write outside it), the files are written and hashed, and the marker is written LAST so
/// a folder is a project only once its files are there.
/// </summary>
[SupportedOSPlatform("windows")]
public static class ProjectScaffold
{
    public const int MaxPathChars = 200;
    public const int MaxPathDepth = 8;
    public const int MaxSegmentChars = 100;

    private static readonly HashSet<string> ReservedNames = new(StringComparer.OrdinalIgnoreCase)
    {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    };

    private static readonly char[] InvalidSegmentCharacters = ['<', '>', ':', '"', '|', '?', '*', '\\', '/'];

    private static readonly UTF8Encoding StrictUtf8 = new(encoderShouldEmitUTF8Identifier: false, throwOnInvalidBytes: true);

    /// <summary>The extensions a file list may not carry: everything <c>file.open</c> refuses except <c>.js</c>, which a web or CLI project IS.</summary>
    public static readonly IReadOnlySet<string> RefusedExtensions =
        OperatorCapabilities.ExecutableExtensions.Where(e => !string.Equals(e, ".js", StringComparison.OrdinalIgnoreCase)).ToHashSet(StringComparer.OrdinalIgnoreCase);

    /// <summary>A Windows reserved device name, with or without an extension (<c>CON</c>, <c>nul.txt</c>, <c>com1</c>).</summary>
    public static bool IsReservedName(string segment)
    {
        var stem = segment;
        var dot = segment.IndexOf('.');
        if (dot >= 0)
        {
            stem = segment[..dot];
        }

        return ReservedNames.Contains(stem);
    }

    /// <summary>
    /// A relative path normalised to <c>/</c> separators, or <c>validation_error</c> naming
    /// <paramref name="field"/> and the reason: empty, absolute (a drive, a leading separator,
    /// a UNC form), a <c>.</c> or <c>..</c> segment, an empty segment, an invalid or control
    /// character, a trailing dot or space, a reserved device name, too long, too deep.
    /// </summary>
    public static string NormalisePath(string raw, string field)
    {
        if (string.IsNullOrWhiteSpace(raw))
        {
            throw DocumentErrors.Invalid($"{field} must be a non-empty relative path");
        }

        if (raw.Length > MaxPathChars)
        {
            throw DocumentErrors.Invalid($"{field} exceeds {MaxPathChars} characters");
        }

        var unified = raw.Replace('\\', '/');
        if (unified.StartsWith('/') || unified.Contains(':') || Path.IsPathRooted(raw))
        {
            throw DocumentErrors.Invalid($"{field} must be relative (no drive, no leading separator, no UNC form)");
        }

        var segments = unified.Split('/');
        if (segments.Length > MaxPathDepth)
        {
            throw DocumentErrors.Invalid($"{field} is deeper than {MaxPathDepth} segments");
        }

        foreach (var segment in segments)
        {
            if (segment.Length == 0)
            {
                throw DocumentErrors.Invalid($"{field} carries an empty segment");
            }

            if (segment is "." or "..")
            {
                throw DocumentErrors.Invalid($"{field} must not carry '.' or '..' segments");
            }

            if (segment.Length > MaxSegmentChars)
            {
                throw DocumentErrors.Invalid($"{field} carries a segment over {MaxSegmentChars} characters");
            }

            if (segment.IndexOfAny(InvalidSegmentCharacters) >= 0 || segment.Any(c => char.IsControl(c) || char.GetUnicodeCategory(c) == System.Globalization.UnicodeCategory.Format))
            {
                throw DocumentErrors.Invalid($"{field} carries a character a file name may not have");
            }

            if (segment.EndsWith('.') || segment.EndsWith(' ') || segment.StartsWith(' '))
            {
                throw DocumentErrors.Invalid($"{field} carries a segment ending in a dot or space");
            }

            if (IsReservedName(segment))
            {
                throw DocumentErrors.Invalid($"{field} carries a reserved device name");
            }
        }

        return string.Join('/', segments);
    }

    /// <summary>Validates the whole payload — ids, slug, files, manifest — before a byte is written.</summary>
    public static ScaffoldRequest Parse(JsonObject payload)
    {
        var projectId = RequireString(payload, "project_id", ProjectRoots.MaxProjectIdChars);
        ProjectRoots.RequireProjectId(projectId);
        var slug = RequireString(payload, "slug", ProjectCapabilityNames.MaxSlugChars);
        ProjectRoots.RequireSlug(slug);

        if (payload["files"] is not JsonArray array || array.Count == 0)
        {
            throw DocumentErrors.Invalid("payload.files is required and must be a non-empty array of {path, text}");
        }

        if (array.Count > ProjectCapabilityNames.MaxFiles)
        {
            throw DocumentErrors.Invalid($"payload.files carries {array.Count} files, over the {ProjectCapabilityNames.MaxFiles} bound; nothing was written");
        }

        var files = new List<ProjectFile>(array.Count);
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        long totalBytes = 0;
        for (var i = 0; i < array.Count; i++)
        {
            if (array[i] is not JsonObject item)
            {
                throw DocumentErrors.Invalid($"payload.files[{i}] must be an object {{path, text}}");
            }

            var path = NormalisePath(RequireString(item, "path", MaxPathChars), $"payload.files[{i}].path");
            RequireWritableName(path, $"payload.files[{i}].path");
            if (!seen.Add(path))
            {
                throw DocumentErrors.Invalid($"payload.files[{i}].path repeats an earlier path (case-insensitively)");
            }

            var textNode = item["text"];
            if (textNode is null || textNode.GetValueKind() != JsonValueKind.String)
            {
                throw DocumentErrors.Invalid($"payload.files[{i}].text is required and must be a string");
            }

            var text = textNode.GetValue<string>();
            totalBytes += RequireText(text, $"payload.files[{i}].text");
            if (totalBytes > ProjectCapabilityNames.MaxTotalBytes)
            {
                throw DocumentErrors.Invalid($"payload.files sum past the {ProjectCapabilityNames.MaxTotalBytes / (1024 * 1024)} MiB bound; nothing was written");
            }

            files.Add(new ProjectFile(path, text));
        }

        if (payload["manifest"] is not JsonObject manifestJson)
        {
            throw DocumentErrors.Invalid("payload.manifest is required and must be an object");
        }

        var manifest = ProjectManifest.Parse(manifestJson, seen);
        return new ScaffoldRequest(projectId, slug, files, manifest);
    }

    /// <summary>Writes the validated set under <c>Projects\&lt;slug&gt;</c> and returns the folder and the hashes.</summary>
    public static ScaffoldOutcome Write(ProjectRoots roots, ScaffoldRequest request, CancellationToken cancellationToken)
    {
        var root = roots.RequireRoot();
        var folder = Path.Combine(root, request.Slug);

        if (Directory.Exists(folder))
        {
            if (ProjectRoots.IsReparsePoint(folder))
            {
                throw DocumentErrors.Denied("the project folder is a link, not a folder; nothing was written");
            }

            var marker = ProjectRoots.ReadMarker(folder);
            if (marker is null)
            {
                if (Directory.EnumerateFileSystemEntries(folder).Any())
                {
                    throw DocumentErrors.Denied($"'{request.Slug}' exists without the project marker ({ProjectRoots.MarkerFileName}) — an owner's folder is never written into; nothing was written", "owner_folder");
                }
            }
            else if (!string.Equals(marker.ProjectId, request.ProjectId, StringComparison.Ordinal))
            {
                throw DocumentErrors.Denied($"'{request.Slug}' belongs to another project; nothing was written", "other_project");
            }
        }
        else if (File.Exists(folder) || ProjectRoots.IsReparsePoint(folder))
        {
            throw DocumentErrors.Denied($"'{request.Slug}' is taken by something that is not a folder; nothing was written");
        }
        else
        {
            Directory.CreateDirectory(folder);
        }

        var resolvedFolder = ProjectRoots.ConfineProjectFolder(folder, root)
            ?? throw DocumentErrors.Denied("the project folder does not resolve directly under the Projects root; nothing was written");

        var written = new List<(string Path, string Sha256)>(request.Files.Count);
        foreach (var file in request.Files)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var target = Path.GetFullPath(Path.Combine(resolvedFolder, file.Path.Replace('/', Path.DirectorySeparatorChar)));
            if (!AuthorisedRoots.IsWithin(target, resolvedFolder) || string.Equals(target, resolvedFolder, StringComparison.OrdinalIgnoreCase))
            {
                throw DocumentErrors.Denied($"'{file.Path}' would land outside the project folder; the scaffold stopped");
            }

            var directory = Path.GetDirectoryName(target)!;
            Directory.CreateDirectory(directory);
            var resolvedDirectory = AuthorisedRoots.ResolveFinal(directory);
            if (resolvedDirectory is null || !AuthorisedRoots.IsWithin(resolvedDirectory, resolvedFolder))
            {
                throw DocumentErrors.Denied($"the directory of '{file.Path}' resolves outside the project folder (a link inside the project); the scaffold stopped");
            }

            if (ProjectRoots.IsReparsePoint(target))
            {
                throw DocumentErrors.Denied($"'{file.Path}' is a link; it is never written through; the scaffold stopped");
            }

            var bytes = StrictUtf8.GetBytes(file.Text);
            File.WriteAllBytes(target, bytes);
            written.Add((file.Path, Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant()));
        }

        Directory.CreateDirectory(Path.Combine(resolvedFolder, ProjectRoots.StateFolderName));
        ProjectRoots.WriteMarker(resolvedFolder, new ProjectMarker(
            request.ProjectId,
            request.Slug,
            DateTimeOffset.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", System.Globalization.CultureInfo.InvariantCulture),
            request.Manifest.Json));
        roots.Remember(request.ProjectId, resolvedFolder);
        return new ScaffoldOutcome(resolvedFolder, written);
    }

    /// <summary>The names a file list may not write: the marker, the state folder, a launcher extension, a path the result scan would refuse as a key.</summary>
    private static void RequireWritableName(string path, string field)
    {
        var name = path[(path.LastIndexOf('/') + 1)..];
        if (string.Equals(name, ProjectRoots.MarkerFileName, StringComparison.OrdinalIgnoreCase))
        {
            throw DocumentErrors.Invalid($"{field} must not be the project marker; the companion writes it");
        }

        var first = path.Contains('/') ? path[..path.IndexOf('/')] : path;
        if (string.Equals(first, ProjectRoots.StateFolderName, StringComparison.OrdinalIgnoreCase))
        {
            throw DocumentErrors.Invalid($"{field} must not be under {ProjectRoots.StateFolderName}/; that folder is the companion's");
        }

        var extension = Path.GetExtension(name);
        if (extension.Length > 0 && RefusedExtensions.Contains(extension))
        {
            throw DocumentErrors.Invalid($"{field} carries the launcher extension '{extension.ToLowerInvariant()}', which a project never ships");
        }

        if (BrowserCapabilities.IsForbiddenKey(path))
        {
            // The result maps sha256 by path, and every result passes the family's forbidden-key
            // scan (a substring rule over normalised keys). A path that would trip it is refused
            // here, before anything is written, with the rule named — never a scaffold that
            // succeeded on disk and was then refused on the way out.
            throw DocumentErrors.Invalid($"{field} contains a fragment the result scan refuses in a key ({string.Join(", ", BrowserCapabilities.ForbiddenResultKeyFragments)}); rename the file");
        }
    }

    /// <summary>Text only; returns the UTF-8 byte count.</summary>
    private static long RequireText(string text, string field)
    {
        foreach (var ch in text)
        {
            if (ch == '\0' || (char.IsControl(ch) && ch is not ('\t' or '\n' or '\r')))
            {
                throw DocumentErrors.Invalid($"{field} is not text (a NUL or control character); a project ships text only");
            }
        }

        try
        {
            return StrictUtf8.GetByteCount(text);
        }
        catch (EncoderFallbackException)
        {
            throw DocumentErrors.Invalid($"{field} is not well-formed UTF-16");
        }
    }

    private static string RequireString(JsonObject payload, string key, int maxChars)
    {
        var node = payload[key];
        if (node is null || node.GetValueKind() != JsonValueKind.String)
        {
            throw DocumentErrors.Invalid($"payload.{key} is required and must be a string");
        }

        var value = node.GetValue<string>();
        if (string.IsNullOrWhiteSpace(value))
        {
            throw DocumentErrors.Invalid($"payload.{key} must not be empty");
        }

        if (value.Length > maxChars)
        {
            throw DocumentErrors.Invalid($"payload.{key} exceeds {maxChars} characters ({value.Length})");
        }

        return value;
    }
}
