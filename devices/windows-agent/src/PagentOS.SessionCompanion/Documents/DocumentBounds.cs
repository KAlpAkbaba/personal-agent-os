namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// The device-side bounds that keep a hostile file from taking the Session Companion down
/// (ADR-0083 addendum 3, the M20 review's High finding). The spec's bounds
/// (<see cref="PagentOS.Agent.Core.Protocol.DocumentCapabilityNames"/>) say how much a caller
/// may GET; these say how much the companion will DO before it answers. Every one is
/// checked before the library that would do the work is entered, or inside the one seam
/// that library offers, so hitting a bound is a typed refusal with the numbers named —
/// <c>unsupported_format</c> with detail <c>decompression_bound</c>, <c>page_bound</c> or
/// <c>too_large</c> — never an <c>OutOfMemoryException</c> in the process that also hosts
/// the Digital Operator. A 50 MiB container can inflate to fifty gigabytes; the 30 s budget
/// cannot interrupt a synchronous SDK getter, so the check has to come first.
/// </summary>
public static class DocumentBounds
{
    // ---- OOXML packages (docx / xlsx / pptx): ContainerGuard reads the zip's central
    //      directory, which records every part's uncompressed size; nothing is inflated.

    /// <summary>The sum of every part's uncompressed length may not exceed this.</summary>
    public const long MaxPackageInflatedBytes = 64L * 1024 * 1024;

    /// <summary>A package with more parts than this is refused (each part is an allocation in the SDK).</summary>
    public const int MaxPackageEntries = 10_000;

    /// <summary>The ratio rule applies to a part whose uncompressed length is above this (small parts compress absurdly well and honestly).</summary>
    public const long PackageRatioEntryBytes = 1L * 1024 * 1024;

    /// <summary>A part above <see cref="PackageRatioEntryBytes"/> whose uncompressed/compressed ratio exceeds this is a bomb, whatever its sum.</summary>
    public const int MaxPackageEntryRatio = 100;

    // ---- PDF: PdfPig inflates lazily, per stream, and lets a caller supply the filters
    //      (ParsingOptions.FilterProvider) — BoundedFilterProvider counts every stream's
    //      inflated size in a streaming pass before PdfPig materialises it.

    /// <summary>One PDF stream (a page's content, a font program, an object stream) may not inflate past this.</summary>
    public const long MaxPdfStreamBytes = 32L * 1024 * 1024;

    /// <summary>All the streams one document inflates across one request may not sum past this.</summary>
    public const long MaxPdfInflatedBytes = 256L * 1024 * 1024;

    /// <summary>The characters kept of one page's text before whitespace normalisation (the block says <c>truncated: true</c> beyond).</summary>
    public const int MaxPdfPageChars = 256 * 1024;

    // ---- Text-like kinds: TextFileReader reads a bounded prefix through one handle whose
    //      length is re-checked on that handle (no stat-then-read window).

    /// <summary>How much of a text-like file <c>file.read</c>, <c>file.inspect</c>, <c>file.compare</c> and the CSV / MD / source / TXT extractors read.</summary>
    public const int MaxTextPrefixBytes = 4 * 1024 * 1024;

    /// <summary>JSON needs the whole document for its top-level keys, so it is read whole up to this and refused beyond (<c>too_large</c>).</summary>
    public const int MaxJsonBytes = 8 * 1024 * 1024;

    public static string Mebibytes(long bytes) => (bytes / (1024.0 * 1024.0)).ToString("0.#", System.Globalization.CultureInfo.InvariantCulture) + " MiB";
}
