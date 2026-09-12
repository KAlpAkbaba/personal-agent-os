using System.Text;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Operator;
using PagentOS.SessionCompanion.Documents;
using Xunit;

namespace PagentOS.Agent.Tests.Documents;

/// <summary>
/// <c>file.search</c>, <c>file.inspect</c> and <c>file.read</c> on the copied fixtures
/// (ADR-0083 gates: search/inspect/read as listed): diacritics-insensitive search, the
/// extension filter, the result bound reported as <c>truncated</c>, a reparse point never
/// followed; inspect's headers only; read's exact bytes, its window and its refusals.
/// </summary>
[Collection(DocumentLabCollection.Name)]
public sealed class SearchInspectReadTests : IDisposable
{
    private readonly DocumentLab _lab = new();

    public void Dispose() => _lab.Dispose();

    private JsonArray Roots() => new(_lab.Root);

    [Theory]
    [InlineData("sözleşme")]
    [InlineData("sozlesme")]
    [InlineData("SÖZLEŞME")]
    [InlineData("*.docx")]
    public void Search_finds_both_contracts_whatever_the_diacritics(string pattern)
    {
        var result = _lab.Exec(DocumentCapabilityNames.FileSearch, new JsonObject { ["roots"] = Roots(), ["pattern"] = pattern });
        var files = result["files"]!.AsArray().Select(f => (JsonObject)f!).ToList();
        Assert.Equal(2, files.Count);
        Assert.All(files, f => Assert.Equal("sozlesme.docx", f["name"]!.GetValue<string>()));
        Assert.Contains(files, f => f["path"]!.GetValue<string>().Contains(@"\2025\", StringComparison.Ordinal));
        Assert.Contains(files, f => f["path"]!.GetValue<string>().Contains(@"\2026\", StringComparison.Ordinal));
        Assert.False(result["truncated"]!.GetValue<bool>());
        Assert.Equal([_lab.Root], result["searched_roots"]!.AsArray().Select(r => r!.GetValue<string>()));

        // Two same-named files, two ids (a location identity); no content hash from a search.
        Assert.NotEqual(files[0]["file_id"]!.GetValue<string>(), files[1]["file_id"]!.GetValue<string>());
        Assert.All(files, f => Assert.Null(f["sha256"]));
        Assert.All(files, f => Assert.EndsWith("Z", f["mtime"]!.GetValue<string>(), StringComparison.Ordinal));
    }

    [Fact]
    public void Search_filters_by_extension_and_truncates_at_max()
    {
        var pdf = _lab.Exec(DocumentCapabilityNames.FileSearch, new JsonObject { ["roots"] = Roots(), ["pattern"] = "*", ["extensions"] = new JsonArray(".pdf") });
        var only = Assert.Single(pdf["files"]!.AsArray());
        Assert.Equal("rapor.pdf", only!["name"]!.GetValue<string>());

        var bare = _lab.Exec(DocumentCapabilityNames.FileSearch, new JsonObject { ["roots"] = Roots(), ["pattern"] = "rapor", ["extensions"] = new JsonArray("PDF") });
        Assert.Single(bare["files"]!.AsArray());

        var two = _lab.Exec(DocumentCapabilityNames.FileSearch, new JsonObject { ["roots"] = Roots(), ["pattern"] = "*", ["max"] = 2 });
        Assert.Equal(2, two["files"]!.AsArray().Count);
        Assert.True(two["truncated"]!.GetValue<bool>());
        Assert.Equal(FileSearch.StopResults, two["stop_reason"]!.GetValue<string>());

        var all = _lab.Exec(DocumentCapabilityNames.FileSearch, new JsonObject { ["roots"] = Roots(), ["pattern"] = "*" });
        Assert.Equal(DocumentLab.FixturePaths().Count, all["files"]!.AsArray().Count);
        Assert.False(all["truncated"]!.GetValue<bool>());

        var future = _lab.Exec(DocumentCapabilityNames.FileSearch, new JsonObject { ["roots"] = Roots(), ["pattern"] = "*", ["modified_after"] = DateTimeOffset.UtcNow.AddDays(1).ToString("o") });
        Assert.Empty(future["files"]!.AsArray());

        var tooMany = _lab.ExpectFailure(DocumentCapabilityNames.FileSearch, new JsonObject { ["roots"] = Roots(), ["pattern"] = "*", ["max"] = 201 });
        Assert.Equal(ErrorClasses.ValidationError, tooMany.ErrorClass);
        var noPattern = _lab.ExpectFailure(DocumentCapabilityNames.FileSearch, new JsonObject { ["roots"] = Roots() });
        Assert.Equal(ErrorClasses.ValidationError, noPattern.ErrorClass);
    }

    [Fact]
    public void Search_never_follows_a_reparse_point_and_never_lists_a_secret_bearing_name()
    {
        var outside = Path.Combine(Path.GetTempPath(), "pagentos-doc-outside-" + _lab.RunId);
        Directory.CreateDirectory(outside);
        File.WriteAllText(Path.Combine(outside, "gizli-sozlesme.docx"), "outside");
        var junction = Path.Combine(_lab.Root, "atlama");
        JunctionFixture.CreateJunction(junction, outside);
        File.WriteAllText(Path.Combine(_lab.Root, ".env"), "TOKEN=x");
        File.WriteAllText(Path.Combine(_lab.Root, "sunucu.key"), "-----BEGIN");
        try
        {
            var result = _lab.Exec(DocumentCapabilityNames.FileSearch, new JsonObject { ["roots"] = Roots(), ["pattern"] = "*" });
            var names = result["files"]!.AsArray().Select(f => f!["name"]!.GetValue<string>()).ToList();
            Assert.DoesNotContain("gizli-sozlesme.docx", names);
            Assert.DoesNotContain(".env", names);
            Assert.DoesNotContain("sunucu.key", names);
            Assert.All(result["files"]!.AsArray(), f => Assert.DoesNotContain(outside, f!["path"]!.GetValue<string>(), StringComparison.OrdinalIgnoreCase));

            // The junction itself is not a search root either: it resolves outside.
            var viaJunction = _lab.ExpectFailure(DocumentCapabilityNames.FileSearch, new JsonObject { ["roots"] = new JsonArray(junction), ["pattern"] = "*" });
            Assert.Equal(ErrorClasses.PermissionDenied, viaJunction.ErrorClass);
            Assert.DoesNotContain(outside, viaJunction.Message, StringComparison.OrdinalIgnoreCase);
            Assert.DoesNotContain("atlama", viaJunction.Message, StringComparison.Ordinal);
        }
        finally
        {
            Directory.Delete(junction);
            Directory.Delete(outside, recursive: true);
        }
    }

    [Fact]
    public void Search_roots_outside_the_authorised_roots_or_relative_are_refused_and_never_echoed()
    {
        var outside = _lab.ExpectFailure(DocumentCapabilityNames.FileSearch, new JsonObject { ["roots"] = new JsonArray(@"C:\Windows\System32"), ["pattern"] = "*.dll" });
        Assert.Equal(ErrorClasses.PermissionDenied, outside.ErrorClass);
        Assert.False(outside.Retryable);
        Assert.DoesNotContain("System32", outside.Message, StringComparison.OrdinalIgnoreCase);

        // "documents" is no longer a validation error: since 2026-09-12 it is a BUCKET NAME
        // the device resolves (packages/protocol/file-search-roots.json), because only this
        // machine knows where the owner's Documents folder is. It is still refused HERE - the
        // lab's only authorised root is its own run directory - but as permission_denied,
        // through the same resolve-then-confine path an absolute path takes. A bucket is never
        // a way around the root list; FileSearchRootsContractTests holds that from the other
        // side. What stays a validation error is a relative folder that names no bucket.
        var bucketOutsideRoots = _lab.ExpectFailure(DocumentCapabilityNames.FileSearch, new JsonObject { ["roots"] = new JsonArray("documents"), ["pattern"] = "*" });
        Assert.Equal(ErrorClasses.PermissionDenied, bucketOutsideRoots.ErrorClass);

        var relative = _lab.ExpectFailure(DocumentCapabilityNames.FileSearch, new JsonObject { ["roots"] = new JsonArray("Faturalar"), ["pattern"] = "*" });
        Assert.Equal(ErrorClasses.ValidationError, relative.ErrorClass);
        Assert.Contains("absolute path", relative.Message, StringComparison.Ordinal);

        var file = _lab.ExpectFailure(DocumentCapabilityNames.FileSearch, new JsonObject { ["roots"] = new JsonArray(_lab.PathOf("notlar.md")), ["pattern"] = "*" });
        Assert.Equal(ErrorClasses.ValidationError, file.ErrorClass);
    }

    [Fact]
    public void Search_without_roots_walks_the_authorised_roots_only()
    {
        // The lab's only authorised root is its run directory; omitting `roots` searches it.
        var result = _lab.Exec(DocumentCapabilityNames.FileSearch, new JsonObject { ["pattern"] = "kod" });
        var only = Assert.Single(result["files"]!.AsArray());
        Assert.Equal("kod.py", only!["name"]!.GetValue<string>());
        Assert.Equal([_lab.Root], result["searched_roots"]!.AsArray().Select(r => r!.GetValue<string>()));
    }

    [Fact]
    public void Inspect_reports_headers_only()
    {
        var xlsx = _lab.Exec(DocumentCapabilityNames.FileInspect, new JsonObject { ["path"] = _lab.PathOf("butce-2026.xlsx") });
        Assert.Equal("xlsx", xlsx["kind"]!.GetValue<string>());
        Assert.False(xlsx["is_text"]!.GetValue<bool>());
        Assert.Equal(["Ozet", "Detay"], xlsx["sheets"]!.AsArray().Select(s => s!.GetValue<string>()));
        Assert.Null(xlsx["text"]);
        Assert.Null(xlsx["blocks"]);

        var pptx = _lab.Exec(DocumentCapabilityNames.FileInspect, new JsonObject { ["path"] = _lab.PathOf("sunum-q3.pptx") });
        Assert.Equal(7, pptx["slides"]!.GetValue<int>());
        Assert.Equal("Q3 Sunumu", pptx["title"]!.GetValue<string>());

        var pdf = _lab.Exec(DocumentCapabilityNames.FileInspect, new JsonObject { ["path"] = _lab.PathOf("rapor.pdf") });
        Assert.Equal(5, pdf["pages"]!.GetValue<int>());
        Assert.Equal("Yıllık Rapor 2026", pdf["title"]!.GetValue<string>());

        var docx = _lab.Exec(DocumentCapabilityNames.FileInspect, new JsonObject { ["path"] = _lab.PathOf("sozlesmeler/2026/sozlesme.docx") });
        Assert.Equal("Hizmet Sözleşmesi", docx["title"]!.GetValue<string>());
        Assert.Equal(1, docx["tables"]!.GetValue<int>());

        var md = _lab.Exec(DocumentCapabilityNames.FileInspect, new JsonObject { ["path"] = _lab.PathOf("notlar.md") });
        Assert.True(md["is_text"]!.GetValue<bool>());
        Assert.Equal("utf-8", md["encoding"]!.GetValue<string>());
        Assert.Equal(17, md["lines"]!.GetValue<int>());
        Assert.Equal("Toplantı Notları", md["title"]!.GetValue<string>());

        var binary = Path.Combine(_lab.Root, "veri.bin");
        File.WriteAllBytes(binary, [0, 1, 2, 0, 0, 200, 201]);
        var unknown = _lab.Exec(DocumentCapabilityNames.FileInspect, new JsonObject { ["path"] = binary });
        Assert.Equal("unknown", unknown["kind"]!.GetValue<string>());
        Assert.False(unknown["is_text"]!.GetValue<bool>());
    }

    [Fact]
    public void Read_returns_the_exact_text_honours_the_window_and_refuses_a_docx()
    {
        var path = _lab.PathOf("notlar.md");
        var whole = File.ReadAllText(path, Encoding.UTF8);
        var read = _lab.Exec(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = path });
        Assert.Equal(whole, read["text"]!.GetValue<string>());
        Assert.Equal("utf-8", read["encoding"]!.GetValue<string>());
        Assert.False(read["truncated"]!.GetValue<bool>());
        Assert.Equal(whole.Length, read["total_chars"]!.GetValue<int>());
        Assert.Equal(DocumentLab.TruthFor("notlar.md")["sha256"]!.GetValue<string>(), read["file"]!["sha256"]!.GetValue<string>());

        var window = _lab.Exec(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = path, ["offset"] = 2, ["length"] = 16 });
        Assert.Equal(whole.Substring(2, 16), window["text"]!.GetValue<string>());
        Assert.Equal("Toplantı Notları", window["text"]!.GetValue<string>());
        Assert.True(window["truncated"]!.GetValue<bool>());
        Assert.Equal(whole.Length, window["total_chars"]!.GetValue<int>());

        var past = _lab.Exec(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = path, ["offset"] = whole.Length + 10 });
        Assert.Equal(string.Empty, past["text"]!.GetValue<string>());
        Assert.False(past["truncated"]!.GetValue<bool>());

        var docx = _lab.ExpectFailure(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = _lab.PathOf("sozlesmeler/2025/sozlesme.docx") });
        Assert.Equal(ErrorClasses.UnsupportedFormat, docx.ErrorClass);
        Assert.False(docx.Retryable);
        Assert.Equal(DocumentErrors.NotText, docx.Detail[DocumentErrors.DetailKey]);

        var tooLong = _lab.ExpectFailure(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = path, ["length"] = DocumentCapabilityNames.MaxReadChars + 1 });
        Assert.Equal(ErrorClasses.ValidationError, tooLong.ErrorClass);
    }

    [Fact]
    public void Read_detects_utf16_and_latin1_and_refuses_binary()
    {
        var utf16 = Path.Combine(_lab.Root, "utf16.txt");
        File.WriteAllText(utf16, "Merhaba Dünya\n", new UnicodeEncoding(bigEndian: false, byteOrderMark: true));
        var read16 = _lab.Exec(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = utf16 });
        Assert.Equal("Merhaba Dünya\n", read16["text"]!.GetValue<string>());
        Assert.Equal("utf-16le", read16["encoding"]!.GetValue<string>());

        var latin = Path.Combine(_lab.Root, "latin1.txt");
        File.WriteAllBytes(latin, Encoding.Latin1.GetBytes("caf\u00e9 na\u00efve\n"));
        var readLatin = _lab.Exec(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = latin });
        Assert.Equal("café naïve\n", readLatin["text"]!.GetValue<string>());
        Assert.Equal("iso-8859-1", readLatin["encoding"]!.GetValue<string>());

        var binary = Path.Combine(_lab.Root, "ikili.txt");
        File.WriteAllBytes(binary, [0x4D, 0x5A, 0, 0, 1, 2, 3, 0, 0, 0]);
        var refused = _lab.ExpectFailure(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = binary });
        Assert.Equal(ErrorClasses.UnsupportedFormat, refused.ErrorClass);
        Assert.Equal(DocumentErrors.NotText, refused.Detail[DocumentErrors.DetailKey]);

        // An unknown extension whose content IS text reads; the kind stays unknown.
        var cfg = Path.Combine(_lab.Root, "ayar.cfg");
        File.WriteAllText(cfg, "anahtar=değer\n");
        var readCfg = _lab.Exec(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = cfg });
        Assert.Equal("anahtar=değer\n", readCfg["text"]!.GetValue<string>());
    }
}
