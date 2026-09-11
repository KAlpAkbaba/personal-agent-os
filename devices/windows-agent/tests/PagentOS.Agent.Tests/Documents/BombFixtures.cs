using System.Diagnostics;
using System.Globalization;
using System.IO.Compression;
using System.Text;
using PagentOS.Agent.Tests.Support;
using Xunit.Abstractions;

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
    /// Run <paramref name="work"/> <paramref name="attempts"/> times and return the attempt that
    /// allocated least, in a window the caller can trust against <paramref name="allocatedBoundMiB"/>.
    ///
    /// <para>
    /// Both gauges are PROCESS-wide, and xunit runs collections in parallel, so a neighbour's
    /// allocation lands on this reading. That noise is one-sided for the allocated-bytes counter
    /// — it can only ADD — so a reading already under the bound is conclusive however busy the
    /// process was, and is returned as it stands. It is a reading OVER the bound that proves
    /// nothing: it may be this work materialising what it was supposed to bound, or it may be
    /// the neighbours. So that case, and only that case, is measured again in a quiet window
    /// (<see cref="TestActivity"/>): other tests wait at the door, the ones already running are
    /// allowed to finish, and the register is checked afterwards to confirm the window really
    /// was undisturbed. What the caller then asserts on is this work's own cost.
    /// </para>
    ///
    /// <para>
    /// 2026-09-08: before that second measurement existed, this took the smallest of three (then
    /// six) attempts beside the rest of the suite and hoped one would land in a quiet window. It
    /// did not once this assembly reached 758 tests, and a bounded read failed for its
    /// neighbours' allocations rather than its own about one run in four. Hoping for a quiet
    /// window is now asking for one, and the bounds are back where the guarantee put them.
    /// </para>
    ///
    /// <para>
    /// The working set is sampled with NO collection between the samples (a materialised DOM is
    /// still live or still uncollected at the second sample). It moves BOTH ways with the rest of
    /// the process, and that asymmetry is worth stating plainly: an over-bound working set earns
    /// the same second measurement when a caller passes <paramref name="workingSetBoundMiB"/>, but
    /// an UNDER-bound one is not the proof its allocated-bytes counterpart is — a neighbour's
    /// collection can push the delta down as easily as up. It is corroboration, not the guarantee.
    /// The guarantee rests on the allocated-bytes bound, which the same materialisation would
    /// break first: every inflation these bounds guard is managed, so it is counted there.
    /// </para>
    /// </summary>
    /// <param name="output">Where the gauge narrates what it measured and whether the window was verified quiet.</param>
    public static (double WorkingSetMiB, double AllocatedMiB) Measure(
        Action work,
        double allocatedBoundMiB,
        ITestOutputHelper? output = null,
        double? workingSetBoundMiB = null,
        int attempts = 3)
    {
        var best = Attempt(work, attempts);
        if (Fits(best, allocatedBoundMiB, workingSetBoundMiB))
        {
            output?.WriteLine($"gauge: {Describe(best)}, best of {attempts} beside the rest of the suite — under the bound, and the allocated-bytes counter only ever adds, so it needs no quieter window");
            return best;
        }

        output?.WriteLine($"gauge: {Describe(best)}, best of {attempts} beside the rest of the suite, is over the {allocatedBoundMiB:F0} MiB bound and proves nothing; asking for a quiet window");
        for (var round = 1; round <= QuietRounds; round++)
        {
            // The first round only shuts the door: no new test starts, which is where the noise
            // comes from, and it costs the rest of the suite nothing but the measurement itself.
            // A round that still cannot get under the bound pays for the full drain.
            var (window, reading) = TestActivity.Measure(
                () => Attempt(work, attempts),
                round == 1 ? TimeSpan.Zero : TestActivity.DefaultDrainTimeout);
            best = reading.AllocatedMiB < best.AllocatedMiB ? reading : best;
            output?.WriteLine($"gauge: round {round} in {window}: {Describe(reading)}");

            // Under the bound is conclusive however the window went — the counter only adds.
            // Over it is conclusive only if the window was verified: that is this work's own cost,
            // and the caller is right to fail on it.
            if (Fits(best, allocatedBoundMiB, workingSetBoundMiB) || window.Verified)
            {
                return best;
            }
        }

        output?.WriteLine($"gauge: {QuietRounds} rounds and never a verified quiet window; reporting {Describe(best)}, which the suite's own noise may have inflated");
        return best;
    }

    /// <summary>
    /// How many times a reading over the bound is taken again in a quiet window before the gauge
    /// gives up and reports a number it could not verify. Each round shuts the door for its drain,
    /// so this is also the most the rest of the suite can be made to wait on one gauge.
    /// </summary>
    private const int QuietRounds = 2;

    private static (double WorkingSetMiB, double AllocatedMiB) Attempt(Action work, int attempts)
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

    private static bool Fits((double WorkingSetMiB, double AllocatedMiB) reading, double allocatedBoundMiB, double? workingSetBoundMiB)
        => reading.AllocatedMiB < allocatedBoundMiB && (workingSetBoundMiB is null || reading.WorkingSetMiB < workingSetBoundMiB);

    private static string Describe((double WorkingSetMiB, double AllocatedMiB) reading)
        => $"allocated {SignedMiB(reading.AllocatedMiB)} MiB, working set {SignedMiB(reading.WorkingSetMiB)} MiB";

    /// <summary>
    /// A signed MiB delta. The working set falls as well as rises, and adding zero first is what
    /// keeps a delta of NEGATIVE zero — which the runtime prints with a sign of its own — from
    /// coming out as "-+0,0".
    /// </summary>
    public static string SignedMiB(double mib) => (mib + 0.0).ToString("+0.0;-0.0", CultureInfo.CurrentCulture);
}
