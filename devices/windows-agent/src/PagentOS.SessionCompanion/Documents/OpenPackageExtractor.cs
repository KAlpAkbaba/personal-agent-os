using System.IO.Compression;
using System.Text;
using System.Text.Json.Nodes;
using System.Xml;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// B52 (req 143 EPUB, req 145 ODT): the two zip-and-XML formats, read with the framework's
/// own <see cref="ZipArchive"/> and <see cref="XmlReader"/> — no new package. The reference
/// scheme is the DOCX extractor's, so an answer cites a converted document the same way:
/// <c>p&lt;n&gt;</c> for every non-empty paragraph or heading in reading order (a heading
/// carries <c>level</c>), <c>t&lt;n&gt;</c> for a table (cells tab-joined, rows on lines);
/// <c>structure.headings</c> and <c>structure.tables</c> as for DOCX, plus
/// <c>structure.chapters</c> for an EPUB.
/// <para>Safety, beyond <see cref="ContainerGuard"/> (which bounds the central directory
/// before this runs): every part is read through <see cref="BoundedPartStream"/>, which counts
/// the bytes actually inflated — a central directory may lie — and no XML reader ever resolves
/// an external entity (<see cref="XmlResolver"/> is null; ODF parts prohibit a DTD outright,
/// XHTML chapters may carry a DOCTYPE, which is ignored, never followed).</para>
/// </summary>
public sealed class OpenPackageExtractor : IDocumentExtractor
{
    /// <summary>The most one part may inflate to.</summary>
    public const long MaxPartBytes = 16L * 1024 * 1024;

    /// <summary>The most chapters an EPUB spine is walked for.</summary>
    public const int MaxChapters = 2_000;

    private const string OfficeNs = "urn:oasis:names:tc:opendocument:xmlns:office:1.0";
    private const string TextNs = "urn:oasis:names:tc:opendocument:xmlns:text:1.0";
    private const string TableNs = "urn:oasis:names:tc:opendocument:xmlns:table:1.0";
    private const string DcNs = "http://purl.org/dc/elements/1.1/";
    private const string ManifestNs = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0";
    private const string ContainerNs = "urn:oasis:names:tc:opendocument:xmlns:container";
    private const string OpfNs = "http://www.idpf.org/2007/opf";

    public bool Supports(string kind) => kind is FileKinds.Epub or FileKinds.Odt;

    public JsonObject Inspect(string path, string kind, CancellationToken cancellationToken)
        => Guarded(path, kind, () =>
        {
            using var archive = Open(path);
            if (kind == FileKinds.Odt)
            {
                RefuseEncryptedOdf(archive, path);
                return new JsonObject { ["title"] = OdfTitle(archive) ?? Path.GetFileNameWithoutExtension(path) };
            }

            var book = ReadBook(archive, path);
            return new JsonObject
            {
                ["title"] = book.Title ?? Path.GetFileNameWithoutExtension(path),
                ["chapters"] = book.Chapters.Count,
            };
        });

    public ExtractResult Extract(string path, string kind, ExtractRequest request, CancellationToken cancellationToken)
        => Guarded(path, kind, () =>
        {
            using var archive = Open(path);
            var collector = new BlockCollector(request.MaxChars);
            var walker = new BlockWalker(collector);
            if (kind == FileKinds.Odt)
            {
                RefuseEncryptedOdf(archive, path);
                using (var content = ReadPart(archive, "content.xml", path))
                {
                    WalkOdf(content, walker, cancellationToken);
                }

                var odtTitle = OdfTitle(archive) ?? walker.FirstHeading ?? Path.GetFileNameWithoutExtension(path);
                return new ExtractResult(odtTitle, collector.Blocks, walker.Structure(), collector.Truncated);
            }

            var book = ReadBook(archive, path);
            foreach (var chapter in book.Chapters)
            {
                cancellationToken.ThrowIfCancellationRequested();
                if (collector.Full)
                {
                    break;
                }

                using var part = ReadPart(archive, chapter, path);
                WalkXhtml(part, walker, cancellationToken);
            }

            var structure = walker.Structure();
            structure["chapters"] = book.Chapters.Count;
            return new ExtractResult(book.Title ?? walker.FirstHeading ?? Path.GetFileNameWithoutExtension(path), collector.Blocks, structure, collector.Truncated);
        });

    // ================================================================== ODT

    private static void WalkOdf(Stream content, BlockWalker walker, CancellationToken cancellationToken)
    {
        using var reader = XmlReader.Create(content, OdfSettings());
        while (reader.Read())
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (walker.Collector.Full)
            {
                return;
            }

            if (reader.NodeType != XmlNodeType.Element)
            {
                continue;
            }

            if (reader.NamespaceURI == TableNs && reader.LocalName == "table")
            {
                walker.Table(ReadOdfTable(reader));
                continue;
            }

            if (reader.NamespaceURI == TextNs && reader.LocalName is "h" or "p")
            {
                var isHeading = reader.LocalName == "h";
                var level = isHeading && int.TryParse(reader.GetAttribute("outline-level", TextNs), out var l) ? l : (isHeading ? 1 : 0);
                var text = ReadOdfInline(reader);
                walker.Paragraph(text, isHeading ? level : null);
            }
        }
    }

    /// <summary>The text of an ODF paragraph/heading element and everything inside it; the reader ends on its end tag.</summary>
    private static string ReadOdfInline(XmlReader reader)
    {
        if (reader.IsEmptyElement)
        {
            return string.Empty;
        }

        var builder = new StringBuilder();
        var depth = reader.Depth;
        while (reader.Read() && reader.Depth > depth)
        {
            switch (reader.NodeType)
            {
                case XmlNodeType.Text or XmlNodeType.SignificantWhitespace or XmlNodeType.Whitespace:
                    builder.Append(reader.Value);
                    break;
                case XmlNodeType.Element when reader.NamespaceURI == TextNs:
                    switch (reader.LocalName)
                    {
                        case "s":
                            builder.Append(' ', int.TryParse(reader.GetAttribute("c", TextNs), out var count) ? Math.Clamp(count, 1, 1000) : 1);
                            break;
                        case "tab":
                            builder.Append('\t');
                            break;
                        case "line-break":
                            builder.Append('\n');
                            break;
                        case "note":
                            // A footnote's body is not the sentence it hangs off.
                            reader.Skip();
                            break;
                    }

                    break;
            }
        }

        return TextFileReader.Normalise(builder.ToString());
    }

    private static List<List<string>> ReadOdfTable(XmlReader reader)
    {
        var rows = new List<List<string>>();
        if (reader.IsEmptyElement)
        {
            return rows;
        }

        var depth = reader.Depth;
        List<string>? row = null;
        while (reader.Read() && reader.Depth > depth)
        {
            if (reader.NodeType != XmlNodeType.Element)
            {
                continue;
            }

            if (reader.NamespaceURI == TableNs && reader.LocalName == "table-row")
            {
                row = new List<string>();
                rows.Add(row);
            }
            else if (reader.NamespaceURI == TableNs && reader.LocalName == "table-cell" && row is not null)
            {
                row.Add(ReadCellText(reader, TextNs));
            }
        }

        return rows;
    }

    private static string ReadCellText(XmlReader reader, string paragraphNs)
    {
        if (reader.IsEmptyElement)
        {
            return string.Empty;
        }

        var parts = new List<string>();
        var depth = reader.Depth;
        while (reader.Read() && reader.Depth > depth)
        {
            if (reader.NodeType == XmlNodeType.Element && reader.NamespaceURI == paragraphNs && reader.LocalName is "p" or "h")
            {
                var text = ReadOdfInline(reader);
                if (text.Length > 0)
                {
                    parts.Add(text);
                }
            }
        }

        return string.Join(" ", parts);
    }

    private static string? OdfTitle(ZipArchive archive)
    {
        var entry = archive.GetEntry("meta.xml");
        if (entry is null)
        {
            return null;
        }

        using var stream = new BoundedPartStream(entry.Open(), MaxPartBytes, "meta.xml");
        using var reader = XmlReader.Create(stream, OdfSettings());
        while (reader.Read())
        {
            if (reader.NodeType == XmlNodeType.Element && reader.NamespaceURI == DcNs && reader.LocalName == "title")
            {
                var title = TextFileReader.Normalise(reader.ReadElementContentAsString());
                return title.Length > 0 ? title : null;
            }
        }

        return null;
    }

    private static void RefuseEncryptedOdf(ZipArchive archive, string path)
    {
        var manifest = archive.GetEntry("META-INF/manifest.xml");
        if (manifest is null)
        {
            return;
        }

        using var stream = new BoundedPartStream(manifest.Open(), MaxPartBytes, "META-INF/manifest.xml");
        using var reader = XmlReader.Create(stream, OdfSettings());
        while (reader.Read())
        {
            if (reader.NodeType == XmlNodeType.Element && reader.NamespaceURI == ManifestNs && reader.LocalName == "encryption-data")
            {
                throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' is password-protected; its text cannot be read", DocumentErrors.Encrypted);
            }
        }
    }

    // ================================================================== EPUB

    private sealed record Book(string? Title, IReadOnlyList<string> Chapters);

    private static Book ReadBook(ZipArchive archive, string path)
    {
        string rootFile;
        using (var container = ReadPart(archive, "META-INF/container.xml", path))
        using (var reader = XmlReader.Create(container, OdfSettings()))
        {
            rootFile = string.Empty;
            while (reader.Read())
            {
                if (reader.NodeType == XmlNodeType.Element && reader.NamespaceURI == ContainerNs && reader.LocalName == "rootfile")
                {
                    rootFile = reader.GetAttribute("full-path") ?? string.Empty;
                    break;
                }
            }
        }

        if (rootFile.Length == 0)
        {
            throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' names no package document in META-INF/container.xml", DocumentErrors.ParseFailed);
        }

        var baseDir = rootFile.Contains('/') ? rootFile[..(rootFile.LastIndexOf('/') + 1)] : string.Empty;
        string? title = null;
        var manifest = new Dictionary<string, (string Href, string MediaType)>(StringComparer.Ordinal);
        var spine = new List<string>();
        using (var opf = ReadPart(archive, rootFile, path))
        using (var reader = XmlReader.Create(opf, XhtmlSettings()))
        {
            while (reader.Read())
            {
                if (reader.NodeType != XmlNodeType.Element)
                {
                    continue;
                }

                if (title is null && reader.NamespaceURI == DcNs && reader.LocalName == "title")
                {
                    var value = TextFileReader.Normalise(reader.ReadElementContentAsString());
                    title = value.Length > 0 ? value : null;
                }
                else if (reader.NamespaceURI == OpfNs && reader.LocalName == "item")
                {
                    var id = reader.GetAttribute("id");
                    var href = reader.GetAttribute("href");
                    if (id is not null && href is not null)
                    {
                        manifest[id] = (href, reader.GetAttribute("media-type") ?? string.Empty);
                    }
                }
                else if (reader.NamespaceURI == OpfNs && reader.LocalName == "itemref")
                {
                    var idref = reader.GetAttribute("idref");
                    if (idref is not null && spine.Count < MaxChapters)
                    {
                        spine.Add(idref);
                    }
                }
            }
        }

        var chapters = new List<string>();
        foreach (var idref in spine)
        {
            if (!manifest.TryGetValue(idref, out var item) || !item.MediaType.Contains("html", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            var resolved = ResolveHref(baseDir, item.Href);
            if (resolved is not null && archive.GetEntry(resolved) is not null)
            {
                chapters.Add(resolved);
            }
        }

        return new Book(title, chapters);
    }

    /// <summary>A manifest href resolved inside the archive, or null when it would leave it.</summary>
    public static string? ResolveHref(string baseDir, string href)
    {
        var withoutFragment = href.Split('#')[0];
        var decoded = Uri.UnescapeDataString(withoutFragment);
        if (decoded.Length == 0 || decoded.StartsWith('/') || decoded.Contains(':'))
        {
            return null;
        }

        var parts = new List<string>();
        foreach (var segment in (baseDir + decoded).Split('/'))
        {
            if (segment is "" or ".")
            {
                continue;
            }

            if (segment == "..")
            {
                if (parts.Count == 0)
                {
                    return null;
                }

                parts.RemoveAt(parts.Count - 1);
                continue;
            }

            parts.Add(segment);
        }

        return parts.Count == 0 ? null : string.Join('/', parts);
    }

    private static void WalkXhtml(Stream part, BlockWalker walker, CancellationToken cancellationToken)
    {
        using var reader = XmlReader.Create(part, XhtmlSettings());
        while (reader.Read())
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (walker.Collector.Full)
            {
                return;
            }

            if (reader.NodeType != XmlNodeType.Element)
            {
                continue;
            }

            switch (reader.LocalName.ToLowerInvariant())
            {
                case "h1" or "h2" or "h3" or "h4" or "h5" or "h6":
                    {
                        var level = reader.LocalName[1] - '0';
                        walker.Paragraph(ReadXhtmlInline(reader), level);
                        break;
                    }

                case "p" or "li" or "blockquote" or "pre" or "dt" or "dd":
                    walker.Paragraph(ReadXhtmlInline(reader), null);
                    break;
                case "table":
                    walker.Table(ReadXhtmlTable(reader));
                    break;
                case "script" or "style" or "head":
                    reader.Skip();
                    break;
            }
        }
    }

    private static string ReadXhtmlInline(XmlReader reader)
    {
        if (reader.IsEmptyElement)
        {
            return string.Empty;
        }

        var builder = new StringBuilder();
        var depth = reader.Depth;
        while (reader.Read() && reader.Depth > depth)
        {
            if (reader.NodeType is XmlNodeType.Text or XmlNodeType.CDATA or XmlNodeType.SignificantWhitespace or XmlNodeType.Whitespace)
            {
                builder.Append(reader.Value);
            }
            else if (reader.NodeType == XmlNodeType.Element && reader.LocalName.Equals("br", StringComparison.OrdinalIgnoreCase))
            {
                builder.Append('\n');
            }
        }

        return TextFileReader.Normalise(builder.ToString());
    }

    private static List<List<string>> ReadXhtmlTable(XmlReader reader)
    {
        var rows = new List<List<string>>();
        if (reader.IsEmptyElement)
        {
            return rows;
        }

        var depth = reader.Depth;
        List<string>? row = null;
        while (reader.Read() && reader.Depth > depth)
        {
            if (reader.NodeType != XmlNodeType.Element)
            {
                continue;
            }

            var name = reader.LocalName.ToLowerInvariant();
            if (name == "tr")
            {
                row = new List<string>();
                rows.Add(row);
            }
            else if (name is "td" or "th" && row is not null)
            {
                row.Add(ReadXhtmlInline(reader));
            }
        }

        return rows;
    }

    // ================================================================== shared

    /// <summary>The DOCX reference scheme, shared by both formats.</summary>
    private sealed class BlockWalker(BlockCollector collector)
    {
        private int _paragraphs;
        private int _tables;
        private readonly JsonArray _headings = new();

        public BlockCollector Collector { get; } = collector;

        public string? FirstHeading { get; private set; }

        public void Paragraph(string text, int? level)
        {
            if (text.Length == 0)
            {
                return;
            }

            _paragraphs++;
            if (level is { } l)
            {
                _headings.Add(text);
                FirstHeading ??= text;
                Collector.Add(new DocumentBlock($"p{_paragraphs}", "heading", text, new JsonObject { ["level"] = Math.Clamp(l, 1, 9) }));
            }
            else
            {
                Collector.Add(new DocumentBlock($"p{_paragraphs}", "paragraph", text));
            }
        }

        public void Table(List<List<string>> rows)
        {
            var kept = rows.Where(r => r.Any(c => c.Length > 0)).ToList();
            if (kept.Count == 0)
            {
                return;
            }

            _tables++;
            // `rows` is the cell grid, exactly as the DOCX extractor carries it.
            var grid = new JsonArray();
            foreach (var row in kept)
            {
                var cells = new JsonArray();
                foreach (var cell in row)
                {
                    cells.Add(cell);
                }

                grid.Add(cells);
            }

            var text = string.Join("\n", kept.Select(r => string.Join("\t", r)));
            Collector.Add(new DocumentBlock($"t{_tables}", "table", text, new JsonObject { ["rows"] = grid }));
        }

        public JsonObject Structure() => new() { ["headings"] = _headings.DeepClone(), ["tables"] = _tables };
    }

    private static ZipArchive Open(string path)
    {
        var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 1 << 16, FileOptions.SequentialScan);
        try
        {
            return new ZipArchive(stream, ZipArchiveMode.Read, leaveOpen: false);
        }
        catch
        {
            stream.Dispose();
            throw;
        }
    }

    private static Stream ReadPart(ZipArchive archive, string name, string path)
    {
        var entry = archive.GetEntry(name)
                    ?? throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' has no '{name}' part", DocumentErrors.ParseFailed);
        return new BoundedPartStream(entry.Open(), MaxPartBytes, name);
    }

    private static XmlReaderSettings OdfSettings() => new()
    {
        DtdProcessing = DtdProcessing.Prohibit,
        XmlResolver = null,
        IgnoreComments = true,
        IgnoreProcessingInstructions = true,
        MaxCharactersFromEntities = 0,
        CloseInput = false,
    };

    private static XmlReaderSettings XhtmlSettings() => new()
    {
        DtdProcessing = DtdProcessing.Ignore,
        XmlResolver = null,
        IgnoreComments = true,
        IgnoreProcessingInstructions = true,
        MaxCharactersFromEntities = 0,
        CloseInput = false,
    };

    private static T Guarded<T>(string path, string kind, Func<T> action)
    {
        try
        {
            return action();
        }
        catch (PagentOS.Agent.Core.Commands.CapabilityException)
        {
            throw;
        }
        catch (Exception ex) when (ex is InvalidDataException or XmlException or IOException or FormatException or ArgumentException)
        {
            throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' could not be read as {kind}: {ex.Message}", DocumentErrors.ParseFailed);
        }
    }
}

/// <summary>
/// A part's inflated bytes, counted as they are read: past the limit it throws the family's
/// <c>decompression_bound</c>, whatever the central directory claimed.
/// </summary>
public sealed class BoundedPartStream(Stream inner, long limit, string name) : Stream
{
    private long _read;

    public override bool CanRead => true;
    public override bool CanSeek => false;
    public override bool CanWrite => false;
    public override long Length => throw new NotSupportedException();
    public override long Position { get => _read; set => throw new NotSupportedException(); }

    public override int Read(byte[] buffer, int offset, int count)
    {
        var n = inner.Read(buffer, offset, count);
        _read += n;
        if (_read > limit)
        {
            throw DocumentErrors.Unsupported($"part '{name}' inflates past the {DocumentBounds.Mebibytes(limit)} bound; it was not parsed", DocumentErrors.DecompressionBound);
        }

        return n;
    }

    public override void Flush() { }
    public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
    public override void SetLength(long value) => throw new NotSupportedException();
    public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            inner.Dispose();
        }

        base.Dispose(disposing);
    }
}
