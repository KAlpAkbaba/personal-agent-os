using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion;

/// <summary>
/// Executes <c>desktop.open_artifact</c> (M3): opens a downloaded artifact file with its OS-associated
/// application, but only after passing the same allowlist rigor as <see cref="AppLauncher"/> for
/// <c>desktop.open_application</c>. Runs inside the interactive owner session (companion), so the shell
/// handler opens in that session.
///
/// <para>Security gates, in order, each with a typed error from the protocol taxonomy:</para>
/// <list type="number">
/// <item>payload.path missing/blank, not fully-qualified, or a UNC path → <c>validation_error</c>.</item>
/// <item>extension is executable (.exe/.bat/.cmd/.ps1/… denylist) → <c>security_scope_error</c>
///       (hard security rejection; never execute a program via this document capability).</item>
/// <item>extension not in the document allowlist (default .pdf/.docx/.html/.htm/.txt/.md)
///       → <c>capability_missing</c> (mirrors AppLauncher's "not in allowlist").</item>
/// <item>canonical full path (after collapsing <c>..</c>) is not under any configured artifact root
///       → <c>security_scope_error</c> (covers traversal and plainly-outside paths).</item>
/// <item>file does not exist → <c>dependency_unavailable</c> (mirrors AppLauncher's missing-executable).</item>
/// <item>a symlink/junction whose real target escapes every root → <c>security_scope_error</c>.</item>
/// </list>
/// </summary>
public sealed class ArtifactOpener
{
    /// <summary>Document extensions this capability may open. Lower-case, dot-prefixed.</summary>
    public static readonly IReadOnlySet<string> DefaultAllowedExtensions = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
    {
        ".pdf", ".docx", ".html", ".htm", ".txt", ".md",
    };

    /// <summary>
    /// Executable/script extensions that are always rejected, even if inside an artifact root and even
    /// if a misconfiguration added them to the allowed set. Defense in depth: this denylist wins.
    /// </summary>
    public static readonly IReadOnlySet<string> ExecutableExtensions = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
    {
        ".exe", ".bat", ".cmd", ".com", ".ps1", ".psm1", ".psd1", ".js", ".jse", ".vbs", ".vbe",
        ".wsf", ".wsh", ".msi", ".msp", ".scr", ".pif", ".cpl", ".hta", ".jar", ".reg", ".lnk",
        ".sh", ".py", ".dll",
    };

    private readonly IReadOnlyList<string> _roots;
    private readonly IReadOnlySet<string> _allowedExtensions;
    private readonly IFileOpener _opener;
    private readonly AuditLog? _audit;

    public ArtifactOpener(
        IEnumerable<string> artifactRoots,
        IFileOpener opener,
        IReadOnlySet<string>? allowedExtensions = null,
        AuditLog? audit = null)
    {
        // Canonicalize + de-dup roots up front so containment checks are cheap and stable.
        var normalized = new List<string>();
        foreach (var root in artifactRoots)
        {
            if (string.IsNullOrWhiteSpace(root))
            {
                continue;
            }

            var full = Path.TrimEndingDirectorySeparator(Path.GetFullPath(Environment.ExpandEnvironmentVariables(root)));
            if (!normalized.Contains(full, StringComparer.OrdinalIgnoreCase))
            {
                normalized.Add(full);
            }
        }

        _roots = normalized;
        _opener = opener;
        _allowedExtensions = allowedExtensions ?? DefaultAllowedExtensions;
        _audit = audit;
    }

    /// <summary>The configured artifact roots (canonicalized), for logging/diagnostics.</summary>
    public IReadOnlyList<string> Roots => _roots;

    /// <summary>Default roots: an <c>artifacts</c> dir under the agent data dir, plus any semicolon-
    /// separated roots from <c>PAGENTOS_AGENT_ArtifactRoots</c> (config key <c>ArtifactRoots</c>).</summary>
    public static IReadOnlyList<string> DefaultRoots(string? extraRoots = null)
    {
        var roots = new List<string>
        {
            Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "PagentOS",
                "agent",
                "artifacts"),
        };

        if (!string.IsNullOrWhiteSpace(extraRoots))
        {
            foreach (var part in extraRoots.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
            {
                roots.Add(part);
            }
        }

        return roots;
    }

    /// <summary>
    /// Payload: <c>{"path": "&lt;absolute local file path&gt;", "artifact_id": "&lt;uuid&gt;"?}</c>.
    /// Result on success: <c>{"opened": true, "path": "&lt;resolved path&gt;", "handler": "shell-associated"}</c>.
    /// </summary>
    public JsonObject Open(JsonObject payload)
    {
        var artifactId = payload["artifact_id"]?.GetValue<string>();
        var rawPath = payload["path"]?.GetValue<string>();

        try
        {
            var resolved = Validate(rawPath);
            _opener.Open(resolved);

            var result = new JsonObject
            {
                ["opened"] = true,
                ["path"] = resolved,
                ["handler"] = "shell-associated",
            };
            _audit?.Write(
                "artifact_open",
                capability: AgentCapabilities.DesktopOpenArtifact,
                status: AckStatus.Succeeded,
                detail: $"path={resolved}; artifact_id={artifactId ?? "-"}; handler=shell-associated");
            return result;
        }
        catch (CapabilityException ex)
        {
            _audit?.Write(
                "artifact_open",
                capability: AgentCapabilities.DesktopOpenArtifact,
                status: AckStatus.Failed,
                detail: $"path={rawPath ?? "-"}; artifact_id={artifactId ?? "-"}; rejected={ex.ErrorClass}: {ex.Message}");
            throw;
        }
    }

    /// <summary>Runs every security gate and returns the canonical resolved full path to open.</summary>
    private string Validate(string? rawPath)
    {
        if (string.IsNullOrWhiteSpace(rawPath))
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "payload.path is required", retryable: false);
        }

        // Reject UNC before any filesystem access; \\server\share and //server/share both count.
        var trimmed = rawPath.Trim();
        if (trimmed.StartsWith(@"\\", StringComparison.Ordinal) || trimmed.StartsWith("//", StringComparison.Ordinal))
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "UNC paths are not allowed", retryable: false);
        }

        // Must be an absolute, drive-rooted local path (rejects relative and bare "..\..").
        if (!Path.IsPathFullyQualified(rawPath))
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "path must be an absolute local file path", retryable: false);
        }

        string canonical;
        try
        {
            canonical = Path.GetFullPath(rawPath); // collapses . and .. segments
        }
        catch (Exception ex) when (ex is ArgumentException or NotSupportedException or PathTooLongException)
        {
            throw new CapabilityException(ErrorClasses.ValidationError, $"invalid path: {ex.Message}", retryable: false);
        }

        // A resolved UNC (e.g. via a mapped-through form) is still rejected.
        if (canonical.StartsWith(@"\\", StringComparison.Ordinal))
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "UNC paths are not allowed", retryable: false);
        }

        var extension = Path.GetExtension(canonical);
        if (ExecutableExtensions.Contains(extension))
        {
            throw new CapabilityException(
                ErrorClasses.SecurityScopeError,
                $"executable file type '{extension}' cannot be opened via desktop.open_artifact",
                retryable: false);
        }

        if (!_allowedExtensions.Contains(extension))
        {
            throw new CapabilityException(
                ErrorClasses.CapabilityMissing,
                $"file type '{(string.IsNullOrEmpty(extension) ? "<none>" : extension)}' is not in the artifact extension allowlist",
                retryable: false);
        }

        // Containment on the canonical path catches traversal (..) escapes and plainly-outside paths,
        // and works whether or not the file exists yet.
        if (!IsUnderAnyRoot(canonical))
        {
            throw new CapabilityException(
                ErrorClasses.SecurityScopeError,
                "path resolves outside the configured artifact root(s)",
                retryable: false);
        }

        if (!File.Exists(canonical))
        {
            throw new CapabilityException(
                ErrorClasses.DependencyUnavailable,
                $"artifact file not found: {canonical}",
                retryable: false);
        }

        // Defense against a symlink/junction inside a root that points outside it: re-check the
        // fully-resolved real target.
        var realPath = ResolveFinalTarget(canonical);
        if (!string.Equals(realPath, canonical, StringComparison.OrdinalIgnoreCase) && !IsUnderAnyRoot(realPath))
        {
            throw new CapabilityException(
                ErrorClasses.SecurityScopeError,
                "path resolves (via a link) outside the configured artifact root(s)",
                retryable: false);
        }

        return canonical;
    }

    private bool IsUnderAnyRoot(string canonicalPath)
    {
        foreach (var root in _roots)
        {
            if (canonicalPath.Equals(root, StringComparison.OrdinalIgnoreCase)
                || canonicalPath.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
        }

        return false;
    }

    private static string ResolveFinalTarget(string canonicalPath)
    {
        try
        {
            var info = new FileInfo(canonicalPath);
            var target = info.ResolveLinkTarget(returnFinalTarget: true);
            return target is null ? canonicalPath : Path.GetFullPath(target.FullName);
        }
        catch (Exception)
        {
            // If link resolution is unavailable, fall back to the canonical path; the earlier
            // containment check already constrained it to a root.
            return canonicalPath;
        }
    }
}
