using System.IO.Compression;
using System.Text.Json.Nodes;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// B32 requirement 142: what a zip archive HOLDS, from its central directory alone — names,
/// sizes, compressed sizes, timestamps — never an entry inflated. The same bounds
/// <see cref="ContainerGuard"/> applies to an OOXML package apply here: too many entries or
/// too much declared content is a typed refusal before anything is read past the
/// directory, and a listing longer than <see cref="MaxListedEntries"/> is cut and says so.
/// </summary>
public static class ArchiveInspector
{
    public const int MaxListedEntries = 200;

    public static JsonObject Inspect(string path)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        ZipArchive archive;
        try
        {
            archive = new ZipArchive(stream, ZipArchiveMode.Read, leaveOpen: true);
        }
        catch (InvalidDataException ex)
        {
            throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' is not a zip archive this device can read: {ex.Message}", DocumentErrors.ParseFailed);
        }

        using (archive)
        {
            IReadOnlyCollection<ZipArchiveEntry> entries;
            try
            {
                entries = archive.Entries;
            }
            catch (InvalidDataException ex)
            {
                throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' has an unreadable central directory: {ex.Message}", DocumentErrors.ParseFailed);
            }

            if (entries.Count > DocumentBounds.MaxPackageEntries)
            {
                throw DocumentErrors.Unsupported(
                    $"'{Path.GetFileName(path)}' declares {entries.Count} entries, over the {DocumentBounds.MaxPackageEntries} bound; it was not listed",
                    DocumentErrors.DecompressionBound);
            }

            long total = 0;
            long compressed = 0;
            var listed = new JsonArray();
            var directories = 0;
            foreach (var entry in entries)
            {
                total += entry.Length;
                compressed += entry.CompressedLength;
                if (entry.FullName.EndsWith('/'))
                {
                    directories++;
                    continue;
                }

                if (listed.Count < MaxListedEntries)
                {
                    listed.Add(new JsonObject
                    {
                        ["name"] = entry.FullName,
                        ["size"] = entry.Length,
                        ["compressed_size"] = entry.CompressedLength,
                        ["modified"] = entry.LastWriteTime.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", System.Globalization.CultureInfo.InvariantCulture),
                        ["kind"] = FileKinds.Of(Path.GetExtension(entry.FullName)),
                    });
                }
            }

            return new JsonObject
            {
                ["entry_count"] = entries.Count - directories,
                ["directory_count"] = directories,
                ["total_uncompressed"] = total,
                ["total_compressed"] = compressed,
                ["entries"] = listed,
                ["truncated"] = entries.Count - directories > MaxListedEntries,
                ["over_inflate_bound"] = total > DocumentBounds.MaxPackageInflatedBytes,
            };
        }
    }
}
