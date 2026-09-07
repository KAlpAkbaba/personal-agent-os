using System.Diagnostics;
using System.Runtime.Versioning;
using System.Text.RegularExpressions;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>What a search was asked: roots already RESOLVED and CONFINED by the caller, a pattern, optional filters.</summary>
public sealed record SearchQuery(
    IReadOnlyList<string> Roots,
    string Pattern,
    IReadOnlyList<string>? Extensions,
    int Max,
    DateTimeOffset? ModifiedAfter);

/// <summary>What a search found, and why it stopped if it stopped early.</summary>
public sealed record SearchOutcome(
    IReadOnlyList<FileRecord> Files,
    bool Truncated,
    string? StopReason,
    int EntriesWalked,
    IReadOnlyList<string> SearchedRoots);

/// <summary>
/// §2 <c>file.search</c>: a bounded recursive walk under the given roots. Three bounds, each
/// reported as <c>truncated: true</c> with the reason rather than a silent cut — at most
/// <see cref="DocumentCapabilityNames.MaxSearchResults"/> records, at most
/// <see cref="DocumentCapabilityNames.MaxSearchEntries"/> directory entries looked at, at most
/// <see cref="DocumentCapabilityNames.SearchTimeout"/> of wall time. Two rules that never
/// bend: a reparse point (junction, symbolic link, mount point) is never followed — not as a
/// directory to descend into, not as a file to list — so a link planted inside a root cannot
/// make the search read outside it; and a secret-bearing name is never listed. The pattern
/// is a glob on the file name when it carries <c>*</c> or <c>?</c>, otherwise a fragment; both
/// match after <see cref="TextFold.Fold"/> on both sides, so <c>sözleşme</c> finds
/// <c>sozlesme.docx</c> and the other way round. A search never opens a file: records carry
/// no content hash.
/// </summary>
[SupportedOSPlatform("windows")]
public static class FileSearch
{
    public const string StopResults = "max_results";
    public const string StopEntries = "max_entries";
    public const string StopTimeout = "timeout";

    private static readonly EnumerationOptions ListOnly = new()
    {
        RecurseSubdirectories = false,
        IgnoreInaccessible = true,
        ReturnSpecialDirectories = false,
        AttributesToSkip = FileAttributes.System,
    };

    public static SearchOutcome Run(SearchQuery query, TimeSpan timeout, CancellationToken cancellationToken)
    {
        var matcher = Matcher(query.Pattern);
        var extensions = query.Extensions is null || query.Extensions.Count == 0
            ? null
            : new HashSet<string>(query.Extensions.Select(e => e.ToLowerInvariant()), StringComparer.Ordinal);
        var files = new List<FileRecord>();
        var searched = new List<string>();
        var entries = 0;
        var truncated = false;
        string? stop = null;
        var stopwatch = Stopwatch.StartNew();

        var pending = new Queue<string>();
        foreach (var root in query.Roots)
        {
            if (Directory.Exists(root))
            {
                pending.Enqueue(root);
                searched.Add(root);
            }
        }

        while (pending.Count > 0 && stop is null)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var directory = pending.Dequeue();
            IEnumerable<FileSystemInfo> children;
            try
            {
                children = new DirectoryInfo(directory).EnumerateFileSystemInfos("*", ListOnly);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                continue;
            }

            foreach (var child in children)
            {
                cancellationToken.ThrowIfCancellationRequested();
                entries++;
                if (entries > DocumentCapabilityNames.MaxSearchEntries)
                {
                    truncated = true;
                    stop = StopEntries;
                    break;
                }

                if (stopwatch.Elapsed > timeout)
                {
                    truncated = true;
                    stop = StopTimeout;
                    break;
                }

                FileAttributes attributes;
                try
                {
                    attributes = child.Attributes;
                }
                catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
                {
                    continue;
                }

                if ((attributes & FileAttributes.ReparsePoint) != 0)
                {
                    // Never followed, never listed: the one way a search could leave the roots.
                    continue;
                }

                if ((attributes & FileAttributes.Directory) != 0)
                {
                    pending.Enqueue(child.FullName);
                    continue;
                }

                if (child is not FileInfo file)
                {
                    continue;
                }

                if (SecretNames.IsSecretBearing(file.Name))
                {
                    continue;
                }

                if (extensions is not null && !extensions.Contains(file.Extension.ToLowerInvariant()))
                {
                    continue;
                }

                if (!matcher(file.Name))
                {
                    continue;
                }

                if (query.ModifiedAfter is { } after && new DateTimeOffset(file.LastWriteTimeUtc, TimeSpan.Zero) <= after)
                {
                    continue;
                }

                if (files.Count >= query.Max)
                {
                    truncated = true;
                    stop = StopResults;
                    break;
                }

                files.Add(FileRecord.From(file, hashContent: false));
            }
        }

        return new SearchOutcome(files, truncated, stop, entries, searched);
    }

    /// <summary>The name predicate: a folded glob, or a folded fragment.</summary>
    public static Func<string, bool> Matcher(string pattern)
    {
        if (TextFold.IsGlob(pattern))
        {
            var regex = new Regex(TextFold.GlobToRegex(TextFold.Fold(pattern)), RegexOptions.CultureInvariant);
            return name => regex.IsMatch(TextFold.Fold(name));
        }

        var fragment = TextFold.Fold(pattern);
        return name => TextFold.Fold(name).Contains(fragment, StringComparison.Ordinal);
    }
}
