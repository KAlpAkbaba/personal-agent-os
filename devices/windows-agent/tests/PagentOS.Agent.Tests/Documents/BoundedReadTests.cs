using System.Text;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Documents;
using Xunit;
using Xunit.Abstractions;

namespace PagentOS.Agent.Tests.Documents;

/// <summary>
/// ADR-0083 addendum 3, the two Low findings: a text-like file is read as a bounded prefix
/// through one handle (never materialised whole before the 64 KB cut), a JSON document is
/// read whole only up to 8 MiB, and a secret-bearing name answers the same whether or not
/// the file exists.
/// </summary>
[Collection(DocumentLabCollection.Name)]
public sealed class BoundedReadTests(ITestOutputHelper output) : IDisposable
{
    private readonly DocumentLab _lab = new();

    public void Dispose() => _lab.Dispose();

    [Fact]
    public void A_6_MiB_CSV_is_read_as_a_4_MiB_prefix_and_says_truncated()
    {
        var csv = Path.Combine(_lab.Root, "buyuk.csv");
        WriteCsv(csv, 6L * 1024 * 1024);
        Assert.True(new FileInfo(csv).Length > 6L * 1024 * 1024);

        JsonObject read = null!;
        var (workingSetMiB, allocatedMiB) = BombFixtures.Measure(() => read = _lab.Exec(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = csv }));
        output.WriteLine($"6 MiB csv file.read: working set +{workingSetMiB:F1} MiB, allocated +{allocatedMiB:F1} MiB");

        Assert.Equal(DocumentCapabilityNames.MaxReadChars, read["text"]!.GetValue<string>().Length);
        Assert.True(read["truncated"]!.GetValue<bool>());
        // ASCII, so the decoded prefix is exactly the byte bound: the decode stopped at 4 MiB, not at the file's end.
        Assert.Equal(DocumentBounds.MaxTextPrefixBytes, read["total_chars"]!.GetValue<int>());
        Assert.StartsWith("ad;tutar;tarih\n", read["text"]!.GetValue<string>(), StringComparison.Ordinal);
        // The prefix costs 4 MiB of bytes and 8 MiB of UTF-16 = 12 MiB inherent; materialising
        // the whole file — the failure this bound exists to catch — costs 6 + 12 on top of that,
        // so about 30. The bound sits at 24: still decisive against materialisation, with room
        // for the process-wide counter to catch a neighbour's allocation (2026-09-08: at 16 it
        // failed for its neighbours' work rather than its own, on the runner and locally).
        Assert.True(allocatedMiB < 24, $"allocated {allocatedMiB:F1} MiB (working set +{workingSetMiB:F1} MiB)");

        // A window that reaches the prefix's end is still truncated: the file went on.
        var tail = _lab.Exec(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = csv, ["offset"] = DocumentBounds.MaxTextPrefixBytes - 10 });
        Assert.Equal(10, tail["text"]!.GetValue<string>().Length);
        Assert.True(tail["truncated"]!.GetValue<bool>());

        // Extract and inspect say so too; the row count is the prefix's.
        var extracted = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = csv });
        Assert.True(extracted["truncated"]!.GetValue<bool>());
        Assert.Equal("r1", extracted["blocks"]![0]!["ref"]!.GetValue<string>());
        Assert.Equal(["ad", "tutar", "tarih"], extracted["structure"]!["columns"]!.AsArray().Select(c => c!.GetValue<string>()));
        var inspected = _lab.Exec(DocumentCapabilityNames.FileInspect, new JsonObject { ["path"] = csv });
        Assert.True(inspected["truncated"]!.GetValue<bool>());
        Assert.True(inspected["lines"]!.GetValue<int>() > 100_000);

        // Compared with itself the lines agree, and the result is honest about the cut.
        var compared = _lab.Exec(DocumentCapabilityNames.FileCompare, new JsonObject
        {
            ["a"] = new JsonObject { ["path"] = csv },
            ["b"] = new JsonObject { ["path"] = csv },
        });
        Assert.True(compared["same_content"]!.GetValue<bool>());
        Assert.True(compared["truncated"]!.GetValue<bool>());
        Assert.Empty(compared["changed_refs"]!.AsArray());
    }

    [Fact]
    public void A_40_MiB_text_read_grows_the_process_by_the_prefix_not_the_file()
    {
        var big = Path.Combine(_lab.Root, "cok-buyuk.txt");
        using (var stream = new FileStream(big, FileMode.Create, FileAccess.Write))
        {
            var line = Encoding.ASCII.GetBytes("satır satır metin 0123456789\n");
            var remaining = 40L * 1024 * 1024;
            while (remaining > 0)
            {
                stream.Write(line);
                remaining -= line.Length;
            }
        }

        JsonObject read = null!;
        var (workingSetMiB, allocatedMiB) = BombFixtures.Measure(() => read = _lab.Exec(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = big, ["length"] = 100 }));
        output.WriteLine($"40 MiB txt file.read: working set +{workingSetMiB:F1} MiB, allocated +{allocatedMiB:F1} MiB");

        Assert.Equal(100, read["text"]!.GetValue<string>().Length);
        Assert.True(read["truncated"]!.GetValue<bool>());
        Assert.Null(read["file"]!["sha256"]);
        // Reading the whole file would allocate 40 MiB of bytes and 80 MiB of UTF-16.
        Assert.True(allocatedMiB < 24, $"allocated {allocatedMiB:F1} MiB (working set +{workingSetMiB:F1} MiB)");
    }

    [Fact]
    public void A_9_MiB_JSON_is_refused_whole_and_a_5_MiB_one_is_read_whole()
    {
        var big = Path.Combine(_lab.Root, "buyuk.json");
        WriteJson(big, 9L * 1024 * 1024);
        var refused = _lab.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = big });
        Assert.Equal(ErrorClasses.UnsupportedFormat, refused.ErrorClass);
        Assert.Equal(DocumentErrors.TooLarge, refused.Detail[DocumentErrors.DetailKey]);
        Assert.Contains("8 MiB", refused.Message, StringComparison.Ordinal);

        // file.read of the same file is a prefix, as for any text-like kind.
        var read = _lab.Exec(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = big, ["length"] = 20 });
        Assert.StartsWith("{\"ad\":\"", read["text"]!.GetValue<string>(), StringComparison.Ordinal);
        Assert.True(read["truncated"]!.GetValue<bool>());

        var medium = Path.Combine(_lab.Root, "orta.json");
        WriteJson(medium, 5L * 1024 * 1024);
        var extracted = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = medium });
        Assert.Equal(["ad", "veri", "son"], extracted["structure"]!["keys"]!.AsArray().Select(k => k!.GetValue<string>()));
        Assert.Equal("$.ad", extracted["blocks"]![0]!["ref"]!.GetValue<string>());
        Assert.True(extracted["truncated"]!.GetValue<bool>(), "the 5 MiB value crosses the 64 KB block budget");
    }

    [Fact]
    public void A_prefix_cut_inside_a_multibyte_character_keeps_the_encoding_and_the_last_whole_character()
    {
        // 'a' then 'ş' (2 bytes) repeated: every 'ş' starts at an odd offset, so the 4 MiB
        // (even) cut lands in the middle of one.
        var path = Path.Combine(_lab.Root, "kesik.txt");
        using (var stream = new FileStream(path, FileMode.Create, FileAccess.Write))
        {
            stream.WriteByte((byte)'a');
            var pair = Encoding.UTF8.GetBytes("ş");
            var chunk = new byte[1 << 20];
            for (var i = 0; i < chunk.Length; i += 2)
            {
                chunk[i] = pair[0];
                chunk[i + 1] = pair[1];
            }

            for (var i = 0; i < 5; i++)
            {
                stream.Write(chunk);
            }
        }

        var read = _lab.Exec(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = path, ["offset"] = 2_097_100 });
        Assert.Equal("utf-8", read["encoding"]!.GetValue<string>());
        Assert.True(read["truncated"]!.GetValue<bool>());
        var expectedChars = 1 + (DocumentBounds.MaxTextPrefixBytes - 1 - 1) / 2;
        Assert.Equal(expectedChars, read["total_chars"]!.GetValue<int>());
        Assert.EndsWith("ş", read["text"]!.GetValue<string>(), StringComparison.Ordinal);
        Assert.DoesNotContain("�", read["text"]!.GetValue<string>(), StringComparison.Ordinal);

        // The trimmers on their own.
        Assert.Equal(3, TextFileReader.TrimIncompleteUtf8([0x61, 0xC5, 0x9F, 0xC5]).Length);
        Assert.Equal(4, TextFileReader.TrimIncompleteUtf8([0x61, 0xC5, 0x9F, 0x62]).Length);
        Assert.Equal(1, TextFileReader.TrimIncompleteUtf8([0x61, 0xE2, 0x82]).Length);
        Assert.Equal(2, TextFileReader.TrimIncompleteUtf16([0x41, 0x00, 0x3D, 0xD8], bigEndian: false).Length);
        Assert.Equal(2, TextFileReader.TrimIncompleteUtf16([0x00, 0x41, 0x00], bigEndian: true).Length);
    }

    [Fact]
    public void A_secret_bearing_name_answers_the_same_whether_or_not_it_exists()
    {
        var present = Path.Combine(_lab.Root, ".env");
        File.WriteAllText(present, "TOKEN=x\n");
        var absent = Path.Combine(_lab.Root, "gizli", ".env.production");
        Directory.CreateDirectory(Path.GetDirectoryName(absent)!);
        Assert.False(File.Exists(absent));
        var outsideAbsent = Path.Combine(Path.GetTempPath(), "pagentos-yok-" + _lab.RunId, "id_rsa");
        Assert.False(File.Exists(outsideAbsent));

        foreach (var capability in new[] { DocumentCapabilityNames.FileLocate, DocumentCapabilityNames.FileInspect, DocumentCapabilityNames.FileRead, DocumentCapabilityNames.DocumentExtract })
        {
            foreach (var path in new[] { present, absent, outsideAbsent })
            {
                var ex = _lab.ExpectFailure(capability, new JsonObject { ["path"] = path });
                Assert.True(ErrorClasses.PermissionDenied == ex.ErrorClass, $"{capability} {Path.GetFileName(path)}: {ex.ErrorClass}");
                Assert.Equal(SecretNames.Detail, ex.Detail[DocumentErrors.DetailKey]);
                Assert.DoesNotContain("no file named", ex.Message, StringComparison.Ordinal);
                Assert.DoesNotContain("authorised roots", ex.Message, StringComparison.Ordinal);
            }
        }

        var compare = _lab.ExpectFailure(DocumentCapabilityNames.FileCompare, new JsonObject
        {
            ["a"] = new JsonObject { ["path"] = _lab.PathOf("notlar.md") },
            ["b"] = new JsonObject { ["path"] = absent },
        });
        Assert.Equal(ErrorClasses.PermissionDenied, compare.ErrorClass);
        Assert.Equal(SecretNames.Detail, compare.Detail[DocumentErrors.DetailKey]);

        // A missing NON-secret name is still not_found, so the planner searches again.
        var missing = _lab.ExpectFailure(DocumentCapabilityNames.FileLocate, new JsonObject { ["path"] = Path.Combine(_lab.Root, "gizli", "notlar.md") });
        Assert.Equal(ErrorClasses.NotFound, missing.ErrorClass);
    }

    private static void WriteCsv(string path, long atLeastBytes)
    {
        using var stream = new FileStream(path, FileMode.Create, FileAccess.Write);
        stream.Write(Encoding.UTF8.GetBytes("ad;tutar;tarih\n"));
        var row = Encoding.UTF8.GetBytes("kira;12500;2026-09-08\n");
        var written = 0L;
        while (written < atLeastBytes)
        {
            stream.Write(row);
            written += row.Length;
        }
    }

    private static void WriteJson(string path, long atLeastBytes)
    {
        using var stream = new FileStream(path, FileMode.Create, FileAccess.Write);
        stream.Write(Encoding.UTF8.GetBytes("{\"ad\":\"Müşteri\",\"veri\":\""));
        BombFixtures.WriteRun(stream, (byte)'x', atLeastBytes);
        stream.Write(Encoding.UTF8.GetBytes("\",\"son\":1.0}\n"));
    }
}
