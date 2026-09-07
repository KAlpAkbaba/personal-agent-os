using System.Text.Json;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Documents;
using Xunit;

namespace PagentOS.Agent.Tests.Documents;

/// <summary>
/// ADR-0083 decision 6: the expected extracts are the single seam between the device and
/// Cloud Core, so for EVERY fixture <c>document.extract</c> through the real dispatcher must
/// produce them block for block — ref by ref, in order, with whitespace-collapsed text
/// equality and every listed field (<c>equals_normalised</c>), or with every <c>contains</c>
/// string present in the block (the PDF, whose layout engine differs from the generator's);
/// every <c>structure</c> key the expected file lists must match; the title must match; and
/// the <c>doc_id</c> must be the SHA-256 <c>truth.json</c> records, which also proves the copy
/// under the fixture root is byte-identical to the committed file.
/// </summary>
[Collection(DocumentLabCollection.Name)]
public sealed class ExtractionOracleTests : IDisposable
{
    private static readonly string[] ListedBlockFields = ["kind", "level", "title", "sheet", "formulas", "rows"];

    private readonly DocumentLab _lab = new();

    public void Dispose() => _lab.Dispose();

    public static IEnumerable<object[]> Fixtures() => DocumentLab.FixturePaths().Select(p => new object[] { p });

    [Theory]
    [MemberData(nameof(Fixtures))]
    public void Every_fixture_extracts_to_its_expected_extract(string relative)
    {
        var expected = DocumentLab.Expected(relative);
        var truth = DocumentLab.TruthFor(relative);
        var result = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = _lab.PathOf(relative) });

        Assert.Equal(expected["kind"]!.GetValue<string>(), result["kind"]!.GetValue<string>());
        Assert.False(result["truncated"]!.GetValue<bool>(), "no fixture is anywhere near the bounds");
        Assert.Equal("doc:" + truth["sha256"]!.GetValue<string>(), result["doc_id"]!.GetValue<string>());
        Assert.Equal(truth["size"]!.GetValue<long>(), result["file"]!["size"]!.GetValue<long>());
        Assert.Equal(truth["sha256"]!.GetValue<string>(), result["file"]!["sha256"]!.GetValue<string>());
        Assert.Equal(Path.GetFileName(relative), result["file"]!["name"]!.GetValue<string>());
        Assert.StartsWith("file:", result["file"]!["file_id"]!.GetValue<string>(), StringComparison.Ordinal);

        var expectedTitle = expected["title"]?.GetValue<string>();
        var actualTitle = result["title"]?.GetValue<string>();
        Assert.True(expectedTitle == actualTitle, $"{relative}: title expected {Show(expectedTitle)} got {Show(actualTitle)}");

        var expectedStructure = (JsonObject)expected["structure"]!;
        var actualStructure = (JsonObject)result["structure"]!;
        foreach (var (key, value) in expectedStructure)
        {
            Assert.True(
                JsonNode.DeepEquals(value, actualStructure[key]),
                $"{relative}: structure.{key} expected {value?.ToJsonString()} got {actualStructure[key]?.ToJsonString()}");
        }

        var match = expected["match"]!.GetValue<string>();
        var expectedBlocks = expected["blocks"]!.AsArray().Select(b => (JsonObject)b!).ToList();
        var actualBlocks = result["blocks"]!.AsArray().Select(b => (JsonObject)b!).ToList();
        Assert.True(
            expectedBlocks.Count == actualBlocks.Count,
            $"{relative}: {expectedBlocks.Count} blocks expected [{string.Join(", ", expectedBlocks.Select(b => b["ref"]))}], got {actualBlocks.Count} [{string.Join(", ", actualBlocks.Select(b => b["ref"]))}]");
        Assert.Equal(truth["blocks"]!.GetValue<int>(), actualBlocks.Count);

        for (var i = 0; i < expectedBlocks.Count; i++)
        {
            var want = expectedBlocks[i];
            var got = actualBlocks[i];
            var reference = want["ref"]!.GetValue<string>();
            Assert.True(reference == got["ref"]!.GetValue<string>(), $"{relative}: block {i + 1} ref expected {reference} got {got["ref"]}");

            var wantText = want["text"]!.GetValue<string>();
            var gotText = got["text"]!.GetValue<string>();
            var blockMatch = want["match"]?.GetValue<string>() ?? match;
            if (blockMatch == "equals_normalised")
            {
                Assert.True(
                    DocumentLab.Normalise(wantText) == DocumentLab.Normalise(gotText),
                    $"{relative} {reference}: text expected {Show(DocumentLab.Normalise(wantText))} got {Show(DocumentLab.Normalise(gotText))}");
            }
            else
            {
                Assert.Equal("contains", blockMatch);
                var haystack = DocumentLab.Normalise(gotText);
                foreach (var needle in want["contains"]!.AsArray().Select(n => n!.GetValue<string>()))
                {
                    Assert.True(
                        haystack.Contains(DocumentLab.Normalise(needle), StringComparison.Ordinal),
                        $"{relative} {reference}: expected to contain {Show(needle)}; page text is {Show(haystack)}");
                }
            }

            foreach (var field in ListedBlockFields)
            {
                if (want[field] is null)
                {
                    continue;
                }

                Assert.True(
                    JsonNode.DeepEquals(want[field], got[field]),
                    $"{relative} {reference}: {field} expected {want[field]!.ToJsonString()} got {got[field]?.ToJsonString() ?? "null"}");
            }
        }
    }

    [Fact]
    public void Extract_by_file_id_after_a_search_gives_the_same_extract_as_by_path()
    {
        var found = _lab.Exec(DocumentCapabilityNames.FileSearch, new JsonObject { ["roots"] = new JsonArray(_lab.Root), ["pattern"] = "notlar.md" });
        var fileId = found["files"]![0]!["file_id"]!.GetValue<string>();

        var byId = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["file_id"] = fileId });
        var byPath = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = _lab.PathOf("notlar.md") });
        Assert.True(JsonNode.DeepEquals(byPath["blocks"], byId["blocks"]));
        Assert.Equal(byPath["doc_id"]!.GetValue<string>(), byId["doc_id"]!.GetValue<string>());
        Assert.Equal(fileId, byId["file"]!["file_id"]!.GetValue<string>());
    }

    [Fact]
    public void Parts_page_range_sheet_and_slide_range_narrow_the_extract_without_changing_the_refs()
    {
        var structureOnly = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = _lab.PathOf("sozlesmeler/2025/sozlesme.docx"), ["parts"] = new JsonArray("structure") });
        Assert.Empty(structureOnly["blocks"]!.AsArray());
        Assert.Equal(6, structureOnly["structure"]!["headings"]!.AsArray().Count);

        var tablesOnly = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = _lab.PathOf("sozlesmeler/2025/sozlesme.docx"), ["parts"] = new JsonArray("tables") });
        var table = Assert.Single(tablesOnly["blocks"]!.AsArray());
        Assert.Equal("t1", table!["ref"]!.GetValue<string>());
        Assert.Equal(3, table["rows"]!.AsArray().Count);

        var pages = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = _lab.PathOf("rapor.pdf"), ["page_range"] = new JsonArray(3, 4) });
        Assert.Equal(["p3", "p4"], pages["blocks"]!.AsArray().Select(b => b!["ref"]!.GetValue<string>()));
        Assert.Equal(5, pages["structure"]!["pages"]!.GetValue<int>());

        var sheet = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = _lab.PathOf("butce-2026.xlsx"), ["sheet"] = "Detay" });
        Assert.All(sheet["blocks"]!.AsArray(), b => Assert.StartsWith("sheet:Detay!", b!["ref"]!.GetValue<string>(), StringComparison.Ordinal));
        Assert.Equal(13, sheet["blocks"]!.AsArray().Count);
        var missingSheet = _lab.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = _lab.PathOf("butce-2026.xlsx"), ["sheet"] = "Yok" });
        Assert.Equal(ErrorClasses.NotFound, missingSheet.ErrorClass);

        var slides = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = _lab.PathOf("sunum-q3.pptx"), ["slide_range"] = new JsonArray(4, 4) });
        var slide = Assert.Single(slides["blocks"]!.AsArray());
        Assert.Equal("s4", slide!["ref"]!.GetValue<string>());
        Assert.Equal("Müşteri Kazanımı", slide["title"]!.GetValue<string>());
        Assert.Equal(7, slides["structure"]!["slide_count"]!.GetValue<int>());

        var badParts = _lab.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = _lab.PathOf("rapor.pdf"), ["parts"] = new JsonArray("everything") });
        Assert.Equal(ErrorClasses.ValidationError, badParts.ErrorClass);
    }

    [Fact]
    public void Max_chars_truncates_the_block_list_and_says_so()
    {
        var result = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = _lab.PathOf("veri.csv"), ["max_chars"] = 60 });
        Assert.True(result["truncated"]!.GetValue<bool>());
        var blocks = result["blocks"]!.AsArray();
        Assert.True(blocks.Count < 11 && blocks.Count >= 2, $"{blocks.Count} blocks");
        var last = (JsonObject)blocks[^1]!;
        Assert.True(last["truncated"]?.GetValue<bool>() == true, "the block that crossed the budget is cut and flagged");
        Assert.True(blocks.Sum(b => b!["text"]!.GetValue<string>().Length) <= 60);
        // The structure is still the whole file's.
        Assert.Equal(10, result["structure"]!["rows"]!.GetValue<int>());
    }

    [Fact]
    public void A_corrupt_package_and_an_unknown_extension_are_unsupported_format_never_an_empty_success()
    {
        var corrupt = Path.Combine(_lab.Root, "bozuk.docx");
        File.WriteAllText(corrupt, "this is not a zip");
        var ex = _lab.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = corrupt });
        Assert.Equal(ErrorClasses.UnsupportedFormat, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Equal(DocumentErrors.ParseFailed, ex.Detail[DocumentErrors.DetailKey]);

        var unknown = Path.Combine(_lab.Root, "resim.bin");
        File.WriteAllBytes(unknown, [0, 1, 2, 3, 255, 254]);
        var noKind = _lab.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = unknown });
        Assert.Equal(ErrorClasses.UnsupportedFormat, noKind.ErrorClass);
        Assert.Equal(DocumentErrors.UnknownKind, noKind.Detail[DocumentErrors.DetailKey]);

        var badJson = Path.Combine(_lab.Root, "kirik.json");
        File.WriteAllText(badJson, "{ \"a\": ");
        var invalid = _lab.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = badJson });
        Assert.Equal(ErrorClasses.UnsupportedFormat, invalid.ErrorClass);
        Assert.Contains("not valid JSON", invalid.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void Text_like_extraction_matches_the_generator_on_the_edge_cases()
    {
        // Markdown without a heading: blank-line paragraphs, no headings, no title.
        var plainMd = Path.Combine(_lab.Root, "basliksiz.md");
        File.WriteAllText(plainMd, "ilk paragraf\nikinci satır\n\nikinci paragraf\n");
        var md = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = plainMd });
        Assert.Equal(["p1", "p2"], md["blocks"]!.AsArray().Select(b => b!["ref"]!.GetValue<string>()));
        Assert.Null(md["title"]);

        // A repeated heading gets a distinct ref, so an answer never cites two places with one string.
        var repeated = Path.Combine(_lab.Root, "tekrar.md");
        File.WriteAllText(repeated, "# A\nbir\n## Notlar\niki\n## Notlar\nüç\n");
        var refs = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = repeated })["blocks"]!.AsArray().Select(b => b!["ref"]!.GetValue<string>()).ToList();
        Assert.Equal(["h1:A", "h2:Notlar", "h2:Notlar#2"], refs);

        // A plain text file: blank-line paragraphs.
        var txt = Path.Combine(_lab.Root, "duz.txt");
        File.WriteAllText(txt, "a\nb\n\n\nc\n");
        var paragraphs = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = txt });
        Assert.Equal(["p1", "p2"], paragraphs["blocks"]!.AsArray().Select(b => b!["ref"]!.GetValue<string>()));
        Assert.Equal("txt", paragraphs["kind"]!.GetValue<string>());

        // Source symbols: C# and JavaScript shapes too, one per line.
        Assert.Equal("Foo", TextLikeExtractor.SymbolOf("    public static int Foo(int x)"));
        Assert.Equal("Bar", TextLikeExtractor.SymbolOf("export async function Bar() {"));
        Assert.Equal("Baz", TextLikeExtractor.SymbolOf("class Baz:"));
        Assert.Null(TextLikeExtractor.SymbolOf("    return kira + maas"));

        // Compact JSON keeps the number spelling and the Turkish letters.
        using var doc = JsonDocument.Parse("{\"hiz\": 1.0, \"ad\": \"Müşteri\", \"n\": [1, 2.50]}");
        Assert.Equal("{\"hiz\":1.0,\"ad\":\"Müşteri\",\"n\":[1,2.50]}", TextLikeExtractor.Compact(doc.RootElement));
    }

    private static string Show(string? text) => text is null ? "null" : "\"" + text + "\"";
}
