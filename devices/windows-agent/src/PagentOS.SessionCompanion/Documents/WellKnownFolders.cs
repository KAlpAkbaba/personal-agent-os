using System.Runtime.Versioning;
using PagentOS.SessionCompanion.Operator;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// The bucket names <c>file.search</c> accepts in <c>payload.roots</c>, and where each one is
/// on THIS machine (packages/protocol/file-search-roots.json).
/// </summary>
/// <remarks>
/// <para>
/// The Cloud Core asks for "the PDFs in my Documents". It cannot name that folder - only this
/// machine knows where the owner's Documents really is, and the owner may have moved it. So
/// the Cloud Core sends a bucket NAME and the device resolves it here.
/// </para>
/// <para>
/// A bucket is never more authority than a path. The resolved folder goes through
/// <see cref="AuthorisedRoots.Confine"/> exactly like an absolute path does, so an owner who
/// narrowed the root list still sees a bucket outside it refused; and a bucket cannot name
/// anywhere the well-known folders do not.
/// </para>
/// <para>
/// Until 2026-09-12 this was not a contract but an assumption on both sides. The Cloud Core
/// put "documents" straight into <c>payload.roots</c>; this device answered
/// <c>payload.roots must be absolute paths</c>; the refusal was filed by the defect sink on
/// 2026-09-09 (ADR-0102) and folder search stayed broken for three days with green suites on
/// both halves. <c>FileSearchRootsContractTests</c> now holds this table equal to the shared
/// file the Cloud Core reads.
/// </para>
/// </remarks>
[SupportedOSPlatform("windows")]
public static class WellKnownFolders
{
    /// <summary>The bucket names, in the shared contract's order. Comparison is case-insensitive.</summary>
    public static readonly IReadOnlyList<string> Names =
        ["documents", "desktop", "downloads", "pictures", "videos", "music"];

    /// <summary>
    /// The folder a bucket names on this machine, or null when the name is not a bucket or
    /// the machine has no such folder. Never throws.
    /// </summary>
    public static string? Resolve(string bucket)
    {
        if (string.IsNullOrWhiteSpace(bucket))
        {
            return null;
        }

        var path = bucket.Trim().ToLowerInvariant() switch
        {
            "documents" => Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
            "desktop" => Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
            // The owner may have moved Downloads; OperatorOptions asks the known-folder API
            // first and falls back to %USERPROFILE%\Downloads, which is the same answer the
            // default root list uses.
            "downloads" => OperatorOptions.DownloadsFolder(),
            "pictures" => Environment.GetFolderPath(Environment.SpecialFolder.MyPictures),
            "videos" => Environment.GetFolderPath(Environment.SpecialFolder.MyVideos),
            "music" => Environment.GetFolderPath(Environment.SpecialFolder.MyMusic),
            _ => null,
        };
        return string.IsNullOrWhiteSpace(path) ? null : path;
    }

    /// <summary>
    /// A <c>roots</c> entry that is not an absolute path, as a real directory on this machine:
    /// a bucket name, or a bucket name followed by relative segments
    /// (<c>documents/Faturalar</c>). Null when the first segment is not a bucket, when the
    /// machine has no such folder, or when any segment is <c>..</c> - the caller then refuses.
    /// </summary>
    /// <remarks>
    /// The result is a candidate path, NOT an authorised one: the caller must still confine it.
    /// Rejecting <c>..</c> here is belt-and-braces; the confinement is what actually holds,
    /// and it resolves every junction before it compares.
    /// </remarks>
    public static string? ResolveEntry(string entry)
    {
        if (string.IsNullOrWhiteSpace(entry))
        {
            return null;
        }

        var segments = entry.Trim().Split(['/', '\\'], StringSplitOptions.RemoveEmptyEntries);
        if (segments.Length == 0)
        {
            return null;
        }

        var root = Resolve(segments[0]);
        if (root is null)
        {
            return null;
        }

        var path = root;
        foreach (var segment in segments.Skip(1))
        {
            if (segment == "..")
            {
                return null;
            }

            path = Path.Combine(path, segment);
        }

        return path;
    }

    /// <summary>The bucket names, for a refusal message that teaches instead of only refusing.</summary>
    public static string NamesForMessage => string.Join(", ", Names);
}
