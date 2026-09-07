using System.Diagnostics;
using System.Globalization;
using System.IO.Compression;
using System.Text;

namespace PagentOS.Agent.Tests.Documents;

/// <summary>
/// Hostile-but-well-formed files for the decompression-bound lab (ADR-0083 addendum 3),
/// written through streams in 1 MiB chunks so the TEST's own memory stays flat whatever
/// the file would inflate to. Every OOXML package here is a minimal, valid package of its
/// kind (<c>[Content_Types].xml</c>, <c>_rels/.rels</c>, the main part with one text run)
/// so that "the SDK was never entered" is a claim about the guard, not about a corrupt file.
/// </summary>
public static class BombFixtures
{
    private const string ContentTypesNs = "http://schemas.openxmlformats.org/package/2006/content-types";
    private const string RelationshipsNs = "http://schemas.openxmlformats.org/package/2006/relationships";
    private const string OfficeDocumentRel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument";

    /// <summary>A .docx / .xlsx / .pptx whose main part holds one text run of <paramref name="inflatedBytes"/> of one character.</summary>
    public static void WriteOoxml(string path, string kind, long inflatedBytes)
    {
        using var archive = new ZipArchive(new FileStream(path, FileMode.Create, FileAccess.Write), ZipArchiveMode.Create);
        switch (kind)
        {
            case "docx":
                Put(archive, "[Content_Types].xml", ContentTypes(("/word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml")));
                Put(archive, "_rels/.rels", Relationships(("rId1", OfficeDocumentRel, "word/document.xml")));
                PutRun(
                    archive,
                    "word/document.xml",
                    "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body><w:p><w:r><w:t>",
                    "</w:t></w:r></w:p></w:body></w:document>",
                    inflatedBytes);
                break;
            case "xlsx":
                Put(archive, "[Content_Types].xml", ContentTypes(
                    ("/xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"),
                    ("/xl/worksheets/sheet1.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml")));
                Put(archive, "_rels/.rels", Relationships(("rId1", OfficeDocumentRel, "xl/workbook.xml")));
                Put(archive, "xl/_rels/workbook.xml.rels", Relationships(("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet", "worksheets/sheet1.xml")));
                Put(archive, "xl/workbook.xml", "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"><sheets><sheet name=\"Sayfa1\" sheetId=\"1\" r:id=\"rId1\"/></sheets></workbook>");
                PutRun(
                    archive,
                    "xl/worksheets/sheet1.xml",
                    "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData><row r=\"1\"><c r=\"A1\" t=\"inlineStr\"><is><t>",
                    "</t></is></c></row></sheetData></worksheet>",
                    inflatedBytes);
                break;
            case "pptx":
                Put(archive, "[Content_Types].xml", ContentTypes(
                    ("/ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"),
                    ("/ppt/slides/slide1.xml", "application/vnd.openxmlformats-officedocument.presentationml.slide+xml")));
                Put(archive, "_rels/.rels", Relationships(("rId1", OfficeDocumentRel, "ppt/presentation.xml")));
                Put(archive, "ppt/_rels/presentation.xml.rels", Relationships(("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide", "slides/slide1.xml")));
                Put(archive, "ppt/presentation.xml", "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><p:presentation xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\"><p:sldIdLst><p:sldId id=\"256\" r:id=\"rId1\"/></p:sldIdLst></p:presentation>");
                PutRun(
                    archive,
                    "ppt/slides/slide1.xml",
                    "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><p:sld xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\" xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>",
                    "</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>",
                    inflatedBytes);
                break;
            default:
                throw new ArgumentOutOfRangeException(nameof(kind), kind, "docx, xlsx or pptx");
        }
    }

    /// <summary>A .docx with <paramref name="parts"/> extra parts of <paramref name="bytesEach"/> (the sum rule, below the per-part ratio threshold) or just many empty parts (the count rule).</summary>
    public static void WriteDocxWithParts(string path, int parts, long bytesEach)
    {
        using var archive = new ZipArchive(new FileStream(path, FileMode.Create, FileAccess.Write), ZipArchiveMode.Create);
        Put(archive, "[Content_Types].xml", ContentTypes(("/word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml")));
        Put(archive, "_rels/.rels", Relationships(("rId1", OfficeDocumentRel, "word/document.xml")));
        PutRun(
            archive,
            "word/document.xml",
            "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:body><w:p><w:r><w:t>",
            "</w:t></w:r></w:p></w:body></w:document>",
            8);
        for (var i = 0; i < parts; i++)
        {
            PutRun(archive, $"word/media/part{i.ToString(CultureInfo.InvariantCulture)}.xml", "<x>", "</x>", bytesEach);
        }
    }

    private static string ContentTypes(params (string PartName, string ContentType)[] overrides)
    {
        var builder = new StringBuilder($"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><Types xmlns=\"{ContentTypesNs}\"><Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/><Default Extension=\"xml\" ContentType=\"application/xml\"/>");
        foreach (var (partName, contentType) in overrides)
        {
            builder.Append($"<Override PartName=\"{partName}\" ContentType=\"{contentType}\"/>");
        }

        return builder.Append("</Types>").ToString();
    }

    private static string Relationships(params (string Id, string Type, string Target)[] relationships)
    {
        var builder = new StringBuilder($"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><Relationships xmlns=\"{RelationshipsNs}\">");
        foreach (var (id, type, target) in relationships)
        {
            builder.Append($"<Relationship Id=\"{id}\" Type=\"{type}\" Target=\"{target}\"/>");
        }

        return builder.Append("</Relationships>").ToString();
    }

    private static void Put(ZipArchive archive, string name, string content)
    {
        var entry = archive.CreateEntry(name, CompressionLevel.Optimal);
        using var stream = entry.Open();
        stream.Write(Encoding.UTF8.GetBytes(content));
    }

    private static void PutRun(ZipArchive archive, string name, string prefix, string suffix, long runBytes)
    {
        var entry = archive.CreateEntry(name, CompressionLevel.SmallestSize);
        using var stream = entry.Open();
        stream.Write(Encoding.UTF8.GetBytes(prefix));
        WriteRun(stream, (byte)'a', runBytes);
        stream.Write(Encoding.UTF8.GetBytes(suffix));
    }

    /// <summary>Write <paramref name="count"/> copies of one byte through a stream, 1 MiB at a time.</summary>
    public static void WriteRun(Stream stream, byte value, long count)
    {
        var chunk = new byte[Math.Min(1 << 20, Math.Max(1, count))];
        Array.Fill(chunk, value);
        var remaining = count;
        while (remaining > 0)
        {
            var n = (int)Math.Min(chunk.Length, remaining);
            stream.Write(chunk, 0, n);
            remaining -= n;
        }
    }

    /// <summary>
    /// A PDF of <paramref name="pages"/> pages that all share ONE content stream: a text
    /// operator ("Sayfa metni burada") followed by <paramref name="inflatedSpaces"/> spaces,
    /// Flate-compressed (zlib). With zero spaces it is an honest small document; with a
    /// couple of hundred MiB it is a per-page stream bomb a few hundred KB on disk.
    /// </summary>
    public static void WritePdf(string path, int pages, long inflatedSpaces)
    {
        using var file = new FileStream(path, FileMode.Create, FileAccess.Write);
        var offsets = new List<long>();
        void Write(string s) => file.Write(Encoding.ASCII.GetBytes(s));

        Write("%PDF-1.4\n");
        offsets.Add(file.Position);
        Write("1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n");
        offsets.Add(file.Position);
        var kids = new StringBuilder();
        for (var i = 0; i < pages; i++)
        {
            kids.Append((5 + i).ToString(CultureInfo.InvariantCulture)).Append(" 0 R ");
        }

        Write($"2 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {pages.ToString(CultureInfo.InvariantCulture)} >>\nendobj\n");

        byte[] compressed;
        using (var buffer = new MemoryStream())
        {
            using (var z = new ZLibStream(buffer, CompressionLevel.SmallestSize, leaveOpen: true))
            {
                z.Write(Encoding.ASCII.GetBytes("BT /F1 12 Tf 72 720 Td (Sayfa metni burada) Tj ET\n"));
                WriteRun(z, (byte)' ', inflatedSpaces);
            }

            compressed = buffer.ToArray();
        }

        offsets.Add(file.Position);
        Write($"3 0 obj\n<< /Length {compressed.Length.ToString(CultureInfo.InvariantCulture)} /Filter /FlateDecode >>\nstream\n");
        file.Write(compressed);
        Write("\nendstream\nendobj\n");
        offsets.Add(file.Position);
        Write("4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n");
        for (var i = 0; i < pages; i++)
        {
            offsets.Add(file.Position);
            Write($"{(5 + i).ToString(CultureInfo.InvariantCulture)} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 3 0 R /Resources << /Font << /F1 4 0 R >> >> >>\nendobj\n");
        }

        var xref = file.Position;
        Write($"xref\n0 {(offsets.Count + 1).ToString(CultureInfo.InvariantCulture)}\n0000000000 65535 f \n");
        foreach (var offset in offsets)
        {
            Write(offset.ToString("D10", CultureInfo.InvariantCulture) + " 00000 n \n");
        }

        Write($"trailer\n<< /Size {(offsets.Count + 1).ToString(CultureInfo.InvariantCulture)} /Root 1 0 R >>\nstartxref\n{xref.ToString(CultureInfo.InvariantCulture)}\n%%EOF\n");
    }

    /// <summary>
    /// The two gauges a bomb cannot hide from: the working set sampled with NO collection
    /// between the samples (a materialised DOM is still live or still uncollected at the
    /// second sample), and the runtime's precise count of bytes allocated, which a
    /// collection never lowers. Both are reported in MiB.
    /// </summary>
    public readonly record struct MemoryGauge(long WorkingSet, long Allocated)
    {
        public static MemoryGauge Now()
        {
            using var process = Process.GetCurrentProcess();
            return new MemoryGauge(process.WorkingSet64, GC.GetTotalAllocatedBytes(precise: true));
        }

        public (double WorkingSetMiB, double AllocatedMiB) Since(MemoryGauge before)
            => ((WorkingSet - before.WorkingSet) / (1024.0 * 1024.0), (Allocated - before.Allocated) / (1024.0 * 1024.0));
    }

    /// <summary>
    /// Run <paramref name="work"/> up to <paramref name="attempts"/> times and return the
    /// attempt that allocated least. The test process runs other collections in parallel:
    /// they can only ADD to the allocated-bytes counter, so the smallest delta is an upper
    /// bound on this work's own allocation — a bomb that was materialised shows in every
    /// attempt. The working set of that attempt is reported beside it; it moves both ways
    /// with the rest of the process and is the number the review asked to see, not the
    /// number the assertion rests on.
    /// </summary>
    public static (double WorkingSetMiB, double AllocatedMiB) Measure(Action work, int attempts = 3)
    {
        var workingSet = 0.0;
        var allocated = double.MaxValue;
        for (var attempt = 0; attempt < attempts; attempt++)
        {
            var before = MemoryGauge.Now();
            work();
            var (ws, alloc) = MemoryGauge.Now().Since(before);
            if (alloc < allocated)
            {
                allocated = alloc;
                workingSet = ws;
            }
        }

        return (workingSet, allocated);
    }
}
