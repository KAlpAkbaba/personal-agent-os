using System.IO.Compression;
using System.Text;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Documents;
using UglyToad.PdfPig.Filters;
using UglyToad.PdfPig.Tokens;
using Xunit;
using Xunit.Abstractions;

namespace PagentOS.Agent.Tests.Documents;

/// <summary>
/// ADR-0083 addendum 3 (the M20 review's High): a decompression bomb inside a 50 MiB
/// container must be a typed refusal before the parser is entered, never an
/// <c>OutOfMemoryException</c> in the Session Companion. Real files under the fixture root,
/// the real dispatcher; the SDK's absence is proven twice — by an extractor that counts its
/// calls, and by the process's memory across the calls on the REAL extractors.
/// </summary>
[Collection(DocumentLabCollection.Name)]
public sealed class DecompressionBoundTests(ITestOutputHelper output) : IDisposable
{
    private const long BombBytes = 200L * 1024 * 1024;
    private const double GrowthBoundMiB = 64;

    private readonly DocumentLab _lab = new();

    public void Dispose() => _lab.Dispose();

    [Theory]
    [InlineData("docx", "sozlesmeler/2026/sozlesme.docx")]
    [InlineData("xlsx", "butce-2026.xlsx")]
    [InlineData("pptx", "sunum-q3.pptx")]
    public void An_OOXML_bomb_is_refused_by_inspect_extract_and_compare_without_entering_the_SDK(string kind, string honestFixture)
    {
        var bomb = Path.Combine(_lab.Root, "bomba." + kind);
        BombFixtures.WriteOoxml(bomb, kind, BombBytes);
        var onDisk = new FileInfo(bomb).Length;
        Assert.True(onDisk < 2 * 1024 * 1024, $"the bomb is {onDisk} bytes on disk: it must sit far inside the 50 MiB container bound");

        // The real extractors: three calls, and the process must not grow by the bomb.
        CapabilityException inspect = null!, extract = null!, compare = null!;
        var (workingSetMiB, allocatedMiB) = BombFixtures.Measure(() =>
        {
            inspect = _lab.ExpectFailure(DocumentCapabilityNames.FileInspect, new JsonObject { ["path"] = bomb });
            extract = _lab.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = bomb });
            compare = _lab.ExpectFailure(DocumentCapabilityNames.FileCompare, new JsonObject
            {
                ["a"] = new JsonObject { ["path"] = bomb },
                ["b"] = new JsonObject { ["path"] = _lab.PathOf(honestFixture) },
            });
        });
        output.WriteLine($"{kind} bomb: {onDisk} bytes on disk, {BombBytes} inflated; working set +{workingSetMiB:F1} MiB, allocated +{allocatedMiB:F1} MiB across inspect+extract+compare");

        foreach (var (name, ex) in new[] { ("inspect", inspect), ("extract", extract), ("compare", compare) })
        {
            Assert.True(ErrorClasses.UnsupportedFormat == ex.ErrorClass, $"{name}: {ex.ErrorClass} {ex.Message}");
            Assert.Equal(DocumentErrors.DecompressionBound, ex.Detail[DocumentErrors.DetailKey]);
            Assert.False(ex.Retryable);
            Assert.Contains("inflate", ex.Message, StringComparison.Ordinal);
            Assert.DoesNotContain("document.xml", ex.Message, StringComparison.OrdinalIgnoreCase);
            Assert.DoesNotContain("sheet1", ex.Message, StringComparison.OrdinalIgnoreCase);
            Assert.DoesNotContain("slide1", ex.Message, StringComparison.OrdinalIgnoreCase);
        }

        Assert.True(workingSetMiB < GrowthBoundMiB, $"working set grew {workingSetMiB:F1} MiB");
        Assert.True(allocatedMiB < GrowthBoundMiB, $"allocated {allocatedMiB:F1} MiB");

        // A second lab whose only extractor counts: the guard sits before the extractor, so it
        // is never called — for a compare in either order, since both containers are bounded
        // before either side is extracted.
        var counting = new CountingExtractor();
        using var counted = new DocumentLab(extractors: [counting]);
        var bombCopy = Path.Combine(counted.Root, "bomba." + kind);
        File.Copy(bomb, bombCopy);
        Assert.Equal(DocumentErrors.DecompressionBound, counted.ExpectFailure(DocumentCapabilityNames.FileInspect, new JsonObject { ["path"] = bombCopy }).Detail[DocumentErrors.DetailKey]);
        Assert.Equal(DocumentErrors.DecompressionBound, counted.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = bombCopy }).Detail[DocumentErrors.DetailKey]);
        Assert.Equal(DocumentErrors.DecompressionBound, counted.ExpectFailure(DocumentCapabilityNames.FileCompare, new JsonObject
        {
            ["a"] = new JsonObject { ["path"] = counted.PathOf(honestFixture) },
            ["b"] = new JsonObject { ["path"] = bombCopy },
        }).Detail[DocumentErrors.DetailKey]);
        Assert.Equal(DocumentErrors.DecompressionBound, counted.ExpectFailure(DocumentCapabilityNames.FileCompare, new JsonObject
        {
            ["a"] = new JsonObject { ["path"] = bombCopy },
            ["b"] = new JsonObject { ["path"] = counted.PathOf(honestFixture) },
        }).Detail[DocumentErrors.DetailKey]);
        Assert.Equal(0, counting.InspectCalls);
        Assert.Equal(0, counting.ExtractCalls);

        // The honest fixture of the same kind still opens: the guard refuses bombs, not documents.
        var honest = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = _lab.PathOf(honestFixture) });
        Assert.NotEmpty(honest["blocks"]!.AsArray());
    }

    [Fact]
    public void The_sum_and_count_rules_refuse_what_the_ratio_rule_alone_would_pass()
    {
        // 80 parts of 900 KiB: each under the 1 MiB ratio threshold, together 70 MiB > 64 MiB.
        var sum = Path.Combine(_lab.Root, "toplam.docx");
        BombFixtures.WriteDocxWithParts(sum, parts: 80, bytesEach: 900 * 1024);
        var bySum = _lab.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = sum });
        Assert.Equal(DocumentErrors.DecompressionBound, bySum.Detail[DocumentErrors.DetailKey]);
        Assert.Contains("64 MiB", bySum.Message, StringComparison.Ordinal);

        // 10 001 empty parts: nothing inflates, the count alone refuses.
        var many = Path.Combine(_lab.Root, "cok.docx");
        BombFixtures.WriteDocxWithParts(many, parts: DocumentBounds.MaxPackageEntries, bytesEach: 0);
        var byCount = _lab.ExpectFailure(DocumentCapabilityNames.FileInspect, new JsonObject { ["path"] = many });
        Assert.Equal(DocumentErrors.DecompressionBound, byCount.Detail[DocumentErrors.DetailKey]);
        Assert.Contains("parts", byCount.Message, StringComparison.Ordinal);

        // 40 parts of 900 KiB (35 MiB): inside every rule, so the SDK opens it and reads the run.
        var fine = Path.Combine(_lab.Root, "uygun.docx");
        BombFixtures.WriteDocxWithParts(fine, parts: 40, bytesEach: 900 * 1024);
        var extracted = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = fine });
        Assert.Equal("aaaaaaaa", extracted["blocks"]![0]!["text"]!.GetValue<string>());

        // Not a zip at all: the SDK's own verdict, as before.
        var corrupt = Path.Combine(_lab.Root, "bozuk.pptx");
        File.WriteAllText(corrupt, "PK is not enough");
        Assert.Equal(DocumentErrors.ParseFailed, _lab.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = corrupt }).Detail[DocumentErrors.DetailKey]);
    }

    [Fact]
    public void A_300_page_PDF_is_refused_before_any_page_is_read_and_read_by_page_range()
    {
        var honest = Path.Combine(_lab.Root, "uzun.pdf");
        BombFixtures.WritePdf(honest, pages: 300, inflatedSpaces: 0);

        var refused = _lab.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = honest });
        Assert.Equal(ErrorClasses.UnsupportedFormat, refused.ErrorClass);
        Assert.Equal(DocumentErrors.PageBound, refused.Detail[DocumentErrors.DetailKey]);
        Assert.Contains("300 pages", refused.Message, StringComparison.Ordinal);
        Assert.Contains("page_range", refused.Message, StringComparison.Ordinal);

        // Inspect is headers: the true count, no refusal.
        var inspected = _lab.Exec(DocumentCapabilityNames.FileInspect, new JsonObject { ["path"] = honest });
        Assert.Equal(300, inspected["pages"]!.GetValue<int>());

        // A window is read as asked; the structure still says 300.
        var window = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = honest, ["page_range"] = new JsonArray(299, 300) });
        Assert.Equal(["p299", "p300"], window["blocks"]!.AsArray().Select(b => b!["ref"]!.GetValue<string>()));
        Assert.Equal("Sayfa metni burada", window["blocks"]![0]!["text"]!.GetValue<string>());
        Assert.Equal(300, window["structure"]!["pages"]!.GetValue<int>());
        Assert.False(window["truncated"]!.GetValue<bool>());

        // A window wider than the page bound is a validation error, before the file is opened.
        var wide = _lab.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = honest, ["page_range"] = new JsonArray(1, 201) });
        Assert.Equal(ErrorClasses.ValidationError, wide.ErrorClass);

        // The 5-page fixture is inside the bound: whole, as the oracle proves elsewhere.
        var fixture = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = _lab.PathOf("rapor.pdf") });
        Assert.Equal(5, fixture["blocks"]!.AsArray().Count);
    }

    [Fact]
    public void A_PDF_stream_bomb_is_refused_per_page_and_never_inflated_when_the_page_count_refuses_first()
    {
        var bomb = Path.Combine(_lab.Root, "bomba.pdf");
        BombFixtures.WritePdf(bomb, pages: 300, inflatedSpaces: BombBytes);
        var onDisk = new FileInfo(bomb).Length;
        Assert.True(onDisk < 2 * 1024 * 1024, $"the PDF bomb is {onDisk} bytes on disk");

        JsonObject inspected = null!;
        CapabilityException unranged = null!, ranged = null!;
        var (workingSetMiB, allocatedMiB) = BombFixtures.Measure(() =>
        {
            inspected = _lab.Exec(DocumentCapabilityNames.FileInspect, new JsonObject { ["path"] = bomb });
            unranged = _lab.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = bomb });
            ranged = _lab.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = bomb, ["page_range"] = new JsonArray(1, 1) });
        });
        output.WriteLine($"pdf bomb: {onDisk} bytes on disk, {BombBytes} inflated per page; working set +{workingSetMiB:F1} MiB, allocated +{allocatedMiB:F1} MiB across inspect+extract+extract[1,1]");

        Assert.Equal(300, inspected["pages"]!.GetValue<int>());
        Assert.Equal(DocumentErrors.PageBound, unranged.Detail[DocumentErrors.DetailKey]);
        Assert.Equal(ErrorClasses.UnsupportedFormat, ranged.ErrorClass);
        Assert.Equal(DocumentErrors.DecompressionBound, ranged.Detail[DocumentErrors.DetailKey]);
        Assert.Contains("32 MiB", ranged.Message, StringComparison.Ordinal);

        // The per-stream counter stops at 32 MiB of a 200 MiB stream through a 64 KiB scratch
        // buffer: PdfPig never materialised it (that would be 200 MiB plus its parse).
        Assert.True(workingSetMiB < GrowthBoundMiB, $"working set grew {workingSetMiB:F1} MiB");
        Assert.True(allocatedMiB < GrowthBoundMiB, $"allocated {allocatedMiB:F1} MiB");

        // A compare against the honest fixture: the fixture is read, the bomb is refused by its page count.
        var compare = _lab.ExpectFailure(DocumentCapabilityNames.FileCompare, new JsonObject
        {
            ["a"] = new JsonObject { ["path"] = _lab.PathOf("rapor.pdf") },
            ["b"] = new JsonObject { ["path"] = bomb },
        });
        Assert.Equal(DocumentErrors.PageBound, compare.Detail[DocumentErrors.DetailKey]);

        // Four pages sharing the bomb, so the page count admits it: the bound trips on page 1 itself.
        var small = Path.Combine(_lab.Root, "kucuk-bomba.pdf");
        BombFixtures.WritePdf(small, pages: 4, inflatedSpaces: BombBytes);
        var perPage = _lab.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = small });
        Assert.Equal(DocumentErrors.DecompressionBound, perPage.Detail[DocumentErrors.DetailKey]);
    }

    // ================================================================== the counters

    [Fact]
    public void The_Flate_counter_matches_the_inflated_length_and_stops_at_the_cap()
    {
        var plain = new byte[3 * 1024 * 1024];
        for (var i = 0; i < plain.Length; i++)
        {
            plain[i] = (byte)('a' + i % 7);
        }

        using var buffer = new MemoryStream();
        using (var z = new ZLibStream(buffer, CompressionLevel.Optimal, leaveOpen: true))
        {
            z.Write(plain);
        }

        var compressed = buffer.ToArray();
        Assert.Equal(plain.Length, BoundedFilterProvider.CountFlate(compressed, long.MaxValue));
        Assert.Equal(1024 + 1, BoundedFilterProvider.CountFlate(compressed, 1024));

        // PdfPig's own filter inflates the same bytes to the same length.
        var decoded = new FlateFilter().Decode(compressed, new DictionaryToken(new Dictionary<NameToken, IToken>()), DefaultFilterProvider.Instance, 0);
        Assert.Equal(plain.Length, decoded.Length);
    }

    [Fact]
    public void The_LZW_counter_matches_the_specification_vector_and_PdfPigs_decoder()
    {
        // ISO 32000-1 7.4.4.2, the worked example: ten bytes.
        byte[] vector = [0x80, 0x0B, 0x60, 0x50, 0x22, 0x0C, 0x0C, 0x85, 0x01];
        Assert.Equal(10, BoundedFilterProvider.CountLzw(vector, earlyChange: 1, long.MaxValue));
        var expected = new byte[] { 45, 45, 45, 45, 45, 65, 45, 45, 45, 66 };
        var decoded = new LzwFilter().Decode(vector, new DictionaryToken(new Dictionary<NameToken, IToken>()), DefaultFilterProvider.Instance, 0);
        Assert.Equal(expected, decoded.ToArray());

        // A long stream through the code-width growth (9 → 12 bits) and a table reset, encoded here,
        // decoded by PdfPig: the counter must agree with what PdfPig actually produced.
        var text = new StringBuilder();
        var seed = 12345u;
        while (text.Length < 200_000)
        {
            seed = seed * 1103515245u + 12345u;
            text.Append("kelime" + (seed >> 16) % 97 + ' ');
        }

        var plain = Encoding.ASCII.GetBytes(text.ToString());
        var encoded = LzwEncode(plain, earlyChange: 1);
        var byPdfPig = new LzwFilter().Decode(encoded, new DictionaryToken(new Dictionary<NameToken, IToken>()), DefaultFilterProvider.Instance, 0);
        Assert.Equal(plain, byPdfPig.ToArray());
        Assert.Equal(plain.Length, BoundedFilterProvider.CountLzw(encoded, earlyChange: 1, long.MaxValue));
        Assert.Equal(4096 + 1, BoundedFilterProvider.CountLzw(encoded, earlyChange: 1, 4096));
    }

    [Fact]
    public void The_RunLength_counter_matches_PdfPigs_decoder()
    {
        // 3 literal bytes, then 'z' × 100, then 2 literal bytes, EOD.
        byte[] stream = [2, (byte)'a', (byte)'b', (byte)'c', 157, (byte)'z', 1, (byte)'d', (byte)'e', 128];
        var decoded = new RunLengthFilter().Decode(stream, new DictionaryToken(new Dictionary<NameToken, IToken>()), DefaultFilterProvider.Instance, 0);
        Assert.Equal(105, decoded.Length);
        Assert.Equal(105, BoundedFilterProvider.CountRunLength(stream, long.MaxValue));
        Assert.Equal(50 + 1, BoundedFilterProvider.CountRunLength(stream, 50));
    }

    [Fact]
    public void The_provider_trips_per_stream_and_per_document_and_leaves_honest_streams_alone()
    {
        var perStream = new BoundedFilterProvider(perStream: 1024, perDocument: long.MaxValue);
        var filters = perStream.GetNamedFilters([NameToken.Create("FlateDecode")]);
        var flate = Assert.Single(filters);
        var small = Zlib(new byte[512]);
        var big = Zlib(new byte[4096]);
        var dictionary = new DictionaryToken(new Dictionary<NameToken, IToken>());
        Assert.Equal(512, flate.Decode(small, dictionary, perStream, 0).Length);
        Assert.False(perStream.Tripped);
        var ex = Assert.Throws<DecompressionBoundException>(() => flate.Decode(big, dictionary, perStream, 0));
        Assert.True(perStream.Tripped);
        Assert.Contains("per-stream", ex.Message, StringComparison.Ordinal);
        Assert.Equal(512, perStream.Inflated);

        var perDocument = new BoundedFilterProvider(perStream: long.MaxValue, perDocument: 1000);
        var docFlate = Assert.Single(perDocument.GetNamedFilters([NameToken.Create("FlateDecode")]));
        docFlate.Decode(small, dictionary, perDocument, 0);
        var second = Assert.Throws<DecompressionBoundException>(() => docFlate.Decode(small, dictionary, perDocument, 0));
        Assert.Contains("per-document", second.Message, StringComparison.Ordinal);
        Assert.Equal(2, perDocument.Decodes);

        // A filter that cannot amplify is handed back untouched.
        var hex = Assert.Single(new BoundedFilterProvider().GetNamedFilters([NameToken.Create("ASCIIHexDecode")]));
        Assert.IsType<AsciiHexDecodeFilter>(hex);
    }

    private static byte[] Zlib(byte[] plain)
    {
        using var buffer = new MemoryStream();
        using (var z = new ZLibStream(buffer, CompressionLevel.Optimal, leaveOpen: true))
        {
            z.Write(plain);
        }

        return buffer.ToArray();
    }

    /// <summary>
    /// A PDF LZW encoder (7.4.4.2): 9–12-bit codes MSB-first, 256 clear first, 257 end. The
    /// decoder defines each entry one code LATER than the encoder (it needs the next string's
    /// first byte), so the encoder's width grows when its table is one entry AHEAD of the
    /// decoder's switch point; after the final code, which adds no encoder entry, the
    /// decoder still adds one, so the encoder accounts for it before choosing EOD's width.
    /// PdfPig's own decoder is the oracle for this encoder in the test that uses it.
    /// </summary>
    private static byte[] LzwEncode(byte[] input, int earlyChange)
    {
        var table = new Dictionary<(int Prefix, byte Next), int>();
        var next = 258;
        var width = 9;
        var output = new List<byte>();
        long bitBuffer = 0;
        var bitCount = 0;

        void Emit(int code)
        {
            bitBuffer = (bitBuffer << width) | (uint)code;
            bitCount += width;
            while (bitCount >= 8)
            {
                output.Add((byte)(bitBuffer >> (bitCount - 8)));
                bitCount -= 8;
            }
        }

        void Grow()
        {
            if (next + earlyChange - 1 >= (1 << width) && width < 12)
            {
                width++;
            }
        }

        Emit(256);
        var prefix = -1;
        foreach (var b in input)
        {
            if (prefix == -1)
            {
                prefix = b;
                continue;
            }

            if (table.TryGetValue((prefix, b), out var code))
            {
                prefix = code;
                continue;
            }

            Emit(prefix);
            if (next < 4096)
            {
                table[(prefix, b)] = next++;
            }

            Grow();
            if (next >= 4096)
            {
                Emit(256);
                table.Clear();
                next = 258;
                width = 9;
                prefix = -1;
            }

            prefix = b;
        }

        if (prefix != -1)
        {
            Emit(prefix);
            if (next < 4096)
            {
                next++;
            }

            Grow();
        }

        Emit(257);
        if (bitCount > 0)
        {
            output.Add((byte)(bitBuffer << (8 - bitCount)));
        }

        return [.. output];
    }

    private sealed class CountingExtractor : IDocumentExtractor
    {
        public int ExtractCalls { get; private set; }

        public int InspectCalls { get; private set; }

        public bool Supports(string kind) => true;

        public JsonObject Inspect(string path, string kind, CancellationToken cancellationToken)
        {
            InspectCalls++;
            return new JsonObject();
        }

        public ExtractResult Extract(string path, string kind, ExtractRequest request, CancellationToken cancellationToken)
        {
            ExtractCalls++;
            return new ExtractResult(null, [], new JsonObject(), false);
        }
    }
}
