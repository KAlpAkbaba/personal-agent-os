using System.IO.Compression;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// The decompression bound for an OOXML package (ADR-0083 addendum 3). A zip's central
/// directory records every entry's compressed AND uncompressed length, so the whole
/// inflated size of a package is known without inflating a byte: this reads that directory
/// through <see cref="ZipArchive"/> (read mode, entries never opened) and refuses, with the
/// numbers named, a package whose parts would sum past
/// <see cref="DocumentBounds.MaxPackageInflatedBytes"/>, that has more than
/// <see cref="DocumentBounds.MaxPackageEntries"/> parts, or one of whose parts above
/// <see cref="DocumentBounds.PackageRatioEntryBytes"/> inflates more than
/// <see cref="DocumentBounds.MaxPackageEntryRatio"/>-fold. The dispatcher runs it before the
/// Open XML SDK is entered, which materialises a part's whole DOM in one synchronous getter.
/// A file that is not a zip at all is left to the SDK, which names the reason in its own
/// words (<c>parse_failed</c>) — the guard never turns a corrupt file into a bomb verdict.
/// Part names are never echoed: they are attacker-chosen strings.
/// </summary>
public static class ContainerGuard
{
    /// <summary>Refuse a package the SDK must not open; return normally when it may.</summary>
    public static void RequireBoundedPackage(string path, string kind)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 1 << 16, FileOptions.SequentialScan);
        if (stream.Length > DocumentCapabilityNames.MaxFileBytes)
        {
            // The directory entry said it fit; the open handle says otherwise.
            throw DocumentErrors.Unsupported(
                $"'{Path.GetFileName(path)}' is {stream.Length} bytes on open, over the {DocumentBounds.Mebibytes(DocumentCapabilityNames.MaxFileBytes)} bound; it was not parsed",
                DocumentErrors.TooLarge);
        }

        ZipArchive archive;
        try
        {
            archive = new ZipArchive(stream, ZipArchiveMode.Read, leaveOpen: true);
        }
        catch (InvalidDataException)
        {
            return;
        }

        using (archive)
        {
            Inspect(archive, Path.GetFileName(path), kind);
        }
    }

    /// <summary>The rules on an already open archive (the entry list is the central directory; no entry is opened).</summary>
    public static void Inspect(ZipArchive archive, string name, string kind)
    {
        IReadOnlyCollection<ZipArchiveEntry> entries;
        try
        {
            entries = archive.Entries;
        }
        catch (InvalidDataException)
        {
            return;
        }

        if (entries.Count > DocumentBounds.MaxPackageEntries)
        {
            throw Refuse(name, kind, $"has {entries.Count} parts, over the {DocumentBounds.MaxPackageEntries} bound");
        }

        long inflated = 0;
        var index = 0;
        foreach (var entry in entries)
        {
            index++;
            var length = entry.Length;
            var compressed = Math.Max(entry.CompressedLength, 1);
            inflated += length;
            if (inflated > DocumentBounds.MaxPackageInflatedBytes)
            {
                throw Refuse(name, kind, $"would inflate to at least {inflated} bytes across its first {index} of {entries.Count} parts, over the {DocumentBounds.Mebibytes(DocumentBounds.MaxPackageInflatedBytes)} bound");
            }

            if (length > DocumentBounds.PackageRatioEntryBytes && length / compressed > DocumentBounds.MaxPackageEntryRatio)
            {
                throw Refuse(name, kind, $"part {index} of {entries.Count} inflates {compressed} → {length} bytes (ratio {length / compressed}), over the {DocumentBounds.MaxPackageEntryRatio}:1 bound for parts above {DocumentBounds.Mebibytes(DocumentBounds.PackageRatioEntryBytes)}");
            }
        }
    }

    private static Exception Refuse(string name, string kind, string reason)
        => DocumentErrors.Unsupported($"'{name}' was not opened as {kind}: the package {reason}", DocumentErrors.DecompressionBound);
}
