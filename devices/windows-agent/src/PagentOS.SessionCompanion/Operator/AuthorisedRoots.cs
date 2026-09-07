using System.Runtime.Versioning;

namespace PagentOS.SessionCompanion.Operator;

/// <summary>
/// The owner's authorised roots (M19_DIGITAL_OPERATOR_SPEC.md §2, ADR-0082 addendum 2 finding
/// 1), enforced by RESOLVING before CONTAINING. A path is opened and the file system asked
/// what it really opened (<see cref="OperatorNative.FinalPath"/>: every junction, mount point
/// and symbolic link in every component followed); only that final path is compared with the
/// roots' own final paths. The lexical form of a path is never compared with anything — a
/// junction created inside a root (no privilege needed) pointing outside it used to pass a
/// <c>GetFullPath().StartsWith(root)</c> check and read the outside content; here it resolves
/// to the outside and is refused. A path that cannot be resolved (missing, unreadable, on a
/// volume without a drive letter) is refused too: there is no fallback to the lexical check.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class AuthorisedRoots
{
    private const string LongPathPrefix = @"\\?\";
    private const string LongUncPrefix = @"\\?\UNC\";

    private readonly string[] _configured;
    private readonly string?[] _resolved;
    private readonly object _lock = new();

    public AuthorisedRoots(IEnumerable<string> roots)
    {
        var configured = new List<string>();
        foreach (var root in roots)
        {
            if (string.IsNullOrWhiteSpace(root))
            {
                continue;
            }

            string normalised;
            try
            {
                normalised = Normalise(Path.GetFullPath(root));
            }
            catch (Exception)
            {
                continue;
            }

            if (!configured.Contains(normalised, StringComparer.OrdinalIgnoreCase))
            {
                configured.Add(normalised);
            }
        }

        _configured = [.. configured];
        _resolved = new string?[_configured.Length];
        for (var i = 0; i < _configured.Length; i++)
        {
            // Resolved at load; a root that does not exist yet (the fixture folder before a
            // lab creates it) is retried at the first check that needs it.
            _resolved[i] = ResolveFinal(_configured[i]);
        }
    }

    /// <summary>The roots as configured, normalised (full path, no trailing separator), for messages and the log.</summary>
    public IReadOnlyList<string> Configured => _configured;

    /// <summary>The roots' final paths — what the file system says each configured root really is — for those that resolve.</summary>
    public IReadOnlyList<string> Resolved
    {
        get
        {
            lock (_lock)
            {
                return [.. ResolvedRootsLocked()];
            }
        }
    }

    /// <summary>
    /// The final path <paramref name="path"/> opens, or null: not absolute, cannot be opened,
    /// or its final form is not what the caller may treat as a path (a prefix-stripped, fully
    /// resolved path is the only thing this returns).
    /// </summary>
    public static string? ResolveFinal(string path)
    {
        if (string.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path))
        {
            return null;
        }

        string full;
        try
        {
            full = Path.GetFullPath(path);
        }
        catch (Exception)
        {
            return null;
        }

        var final = OperatorNative.FinalPath(full);
        if (final is null)
        {
            return null;
        }

        try
        {
            return Normalise(Path.GetFullPath(StripPrefix(final)));
        }
        catch (Exception)
        {
            return null;
        }
    }

    /// <summary>
    /// The resolved path when what <paramref name="path"/> really opens lies inside a root's
    /// resolved form; null otherwise (outside, or unresolvable). The caller acts on the RETURNED
    /// path, never on the one it was given.
    /// </summary>
    public string? Confine(string path)
    {
        var resolved = ResolveFinal(path);
        if (resolved is null)
        {
            return null;
        }

        lock (_lock)
        {
            foreach (var root in ResolvedRootsLocked())
            {
                if (IsWithin(resolved, root))
                {
                    return resolved;
                }
            }
        }

        return null;
    }

    public bool Contains(string path) => Confine(path) is not null;

    /// <summary>Equal to the root, or below it — on separator boundaries, so <c>C:\Owner2</c> is not under <c>C:\Owner</c>.</summary>
    public static bool IsWithin(string resolvedPath, string resolvedRoot)
    {
        if (resolvedPath.Equals(resolvedRoot, StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }

        var prefix = resolvedRoot.EndsWith(Path.DirectorySeparatorChar) ? resolvedRoot : resolvedRoot + Path.DirectorySeparatorChar;
        return resolvedPath.StartsWith(prefix, StringComparison.OrdinalIgnoreCase);
    }

    /// <summary><c>\\?\C:\x</c> → <c>C:\x</c>, <c>\\?\UNC\server\share</c> → <c>\\server\share</c>; anything else unchanged.</summary>
    public static string StripPrefix(string path)
    {
        if (path.StartsWith(LongUncPrefix, StringComparison.OrdinalIgnoreCase))
        {
            return @"\\" + path[LongUncPrefix.Length..];
        }

        if (path.StartsWith(LongPathPrefix, StringComparison.Ordinal))
        {
            return path[LongPathPrefix.Length..];
        }

        return path;
    }

    private IEnumerable<string> ResolvedRootsLocked()
    {
        for (var i = 0; i < _configured.Length; i++)
        {
            _resolved[i] ??= ResolveFinal(_configured[i]);
            if (_resolved[i] is { } resolved)
            {
                yield return resolved;
            }
        }
    }

    /// <summary>No trailing separator, except a drive root (<c>C:\</c>) which needs it to stay a path.</summary>
    private static string Normalise(string fullPath)
    {
        var trimmed = fullPath.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        return trimmed.Length < fullPath.Length && trimmed.EndsWith(':') ? trimmed + Path.DirectorySeparatorChar : trimmed;
    }
}
