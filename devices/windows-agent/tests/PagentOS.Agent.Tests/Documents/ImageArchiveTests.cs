using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Documents;
using Xunit;

namespace PagentOS.Agent.Tests.Documents;

/// <summary>
/// B32 requirements 139-142, 150: an image's headers and its OCR text, an archive's central
/// directory, and one file sent to the Recycle Bin — each against the committed fixture
/// (<c>metin.png</c>, <c>arsiv.zip</c>) or a file the lab writes itself, every answer read
/// back from the machine.
/// </summary>
[Collection(DocumentLabCollection.Name)]
public sealed class ImageArchiveTests : IDisposable
{
    private readonly DocumentLab _lab = new();

    public void Dispose() => _lab.Dispose();

    [Fact]
    public void The_kinds_are_told_apart_by_extension_and_only_the_image_is_extractable()
    {
        Assert.Equal(FileKinds.Image, FileKinds.Of(".png"));
        Assert.Equal(FileKinds.Image, FileKinds.Of(".JPG"));
        Assert.Equal(FileKinds.Archive, FileKinds.Of(".zip"));
        Assert.True(FileKinds.IsExtractable(FileKinds.Image));
        Assert.False(FileKinds.IsExtractable(FileKinds.Archive));
        Assert.False(FileKinds.IsTextLike(FileKinds.Image));
        Assert.Equal(14, DocumentCapabilityNames.All.Count);
        Assert.Equal("file.trash", DocumentCapabilityNames.All[7]);
        Assert.Equal("file.restore", DocumentCapabilityNames.All[^1]);
    }

    [Fact]
    public void An_image_inspects_to_its_own_headers_without_decoding_pixels()
    {
        var truth = DocumentLab.TruthFor("metin.png");
        var inspected = _lab.Exec(DocumentCapabilityNames.FileInspect, new JsonObject { ["path"] = _lab.PathOf("metin.png") });
        Assert.Equal("image", inspected["kind"]!.GetValue<string>());
        Assert.False(inspected["is_text"]!.GetValue<bool>());
        var image = (JsonObject)inspected["image"]!;
        Assert.Equal(truth["image"]!["width"]!.GetValue<int>(), image["width"]!.GetValue<int>());
        Assert.Equal(truth["image"]!["height"]!.GetValue<int>(), image["height"]!.GetValue<int>());
        Assert.Contains("PNG", image["format"]!.GetValue<string>(), StringComparison.OrdinalIgnoreCase);
        Assert.NotNull(image["metadata"]);
        Assert.Equal(truth["sha256"]!.GetValue<string>(), inspected["file"]!["sha256"]!.GetValue<string>());
    }

    [Fact]
    public void An_image_extracts_to_ocr_lines_or_says_which_language_pack_is_missing()
    {
        var truth = DocumentLab.TruthFor("metin.png");
        var languages = new OcrHost().AvailableLanguages(CancellationToken.None);
        if (languages.Count == 0)
        {
            // No OCR language on this machine: the honest answer is a typed refusal, never an
            // empty extract. (The oracle run on a machine with a pack asserts the text.)
            var refused = _lab.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = _lab.PathOf("metin.png") });
            Assert.Equal(ErrorClasses.DependencyUnavailable, refused.ErrorClass);
            return;
        }

        var extracted = _lab.Exec(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = _lab.PathOf("metin.png"), ["language"] = "tr" });
        Assert.Equal("image", extracted["kind"]!.GetValue<string>());
        Assert.Equal("doc:" + truth["sha256"]!.GetValue<string>(), extracted["doc_id"]!.GetValue<string>());
        var blocks = extracted["blocks"]!.AsArray();
        Assert.NotEmpty(blocks);
        Assert.Equal("o1", blocks[0]!["ref"]!.GetValue<string>());
        Assert.Equal(ImageExtractor.BlockKind, blocks[0]!["kind"]!.GetValue<string>());
        var text = string.Join(" ", blocks.Select(b => b!["text"]!.GetValue<string>()));
        // The committed reference: exact with the Turkish pack, at least the digits without it.
        var reference = truth["ocr_text"]!.GetValue<string>();
        var structure = (JsonObject)extracted["structure"]!;
        Assert.Equal(OcrHost.EngineName, structure["engine"]!.GetValue<string>());
        Assert.Equal(600, structure["width"]!.GetValue<int>());
        if (string.Equals(structure["language"]!.GetValue<string>(), "tr", StringComparison.OrdinalIgnoreCase))
        {
            Assert.Equal(reference, DocumentLab.Normalise(text));
        }
        else
        {
            Assert.Contains("1234", text, StringComparison.Ordinal);
        }
    }

    [Fact]
    public void An_archive_inspects_to_its_central_directory_and_is_never_extracted()
    {
        var truth = DocumentLab.TruthFor("arsiv.zip");
        var inspected = _lab.Exec(DocumentCapabilityNames.FileInspect, new JsonObject { ["path"] = _lab.PathOf("arsiv.zip") });
        Assert.Equal("archive", inspected["kind"]!.GetValue<string>());
        var archive = (JsonObject)inspected["archive"]!;
        Assert.Equal(truth["archive"]!["entry_count"]!.GetValue<int>(), archive["entry_count"]!.GetValue<int>());
        var names = archive["entries"]!.AsArray().Select(e => e!["name"]!.GetValue<string>()).ToArray();
        var expected = truth["archive"]!["entries"]!.AsArray().Select(e => e!["name"]!.GetValue<string>()).ToArray();
        Assert.Equal(expected, names);
        Assert.Equal(truth["archive"]!["total_uncompressed"]!.GetValue<long>(), archive["total_uncompressed"]!.GetValue<long>());
        Assert.False(archive["truncated"]!.GetValue<bool>());
        Assert.Equal("md", archive["entries"]![0]!["kind"]!.GetValue<string>());

        var refused = _lab.ExpectFailure(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["path"] = _lab.PathOf("arsiv.zip") });
        Assert.Equal(ErrorClasses.UnsupportedFormat, refused.ErrorClass);
    }

    [Fact]
    public void Trash_moves_one_lab_file_to_the_recycle_bin_and_refuses_what_it_must()
    {
        var path = Path.Combine(_lab.Root, "silinecek.txt");
        File.WriteAllText(path, "gereksiz kopya");
        var trashed = _lab.Exec(DocumentCapabilityNames.FileTrash, new JsonObject { ["path"] = path });
        Assert.True(trashed["trashed"]!.GetValue<bool>(), trashed.ToJsonString());
        Assert.Equal("recycle_bin", trashed["method"]!.GetValue<string>());
        Assert.False(trashed["observed"]!["exists"]!.GetValue<bool>());
        Assert.False(File.Exists(path), "the file must be gone from its folder");

        // Gone is gone: a second trash is not_found, never a silent success.
        var again = _lab.ExpectFailure(DocumentCapabilityNames.FileTrash, new JsonObject { ["path"] = path });
        Assert.Equal(ErrorClasses.NotFound, again.ErrorClass);

        // A directory is refused; a secret-bearing name is refused before any I/O.
        var dir = _lab.ExpectFailure(DocumentCapabilityNames.FileTrash, new JsonObject { ["path"] = _lab.Root });
        Assert.Equal(ErrorClasses.ValidationError, dir.ErrorClass);
        var secret = _lab.ExpectFailure(DocumentCapabilityNames.FileTrash, new JsonObject { ["path"] = Path.Combine(_lab.Root, ".env") });
        Assert.Equal(ErrorClasses.PermissionDenied, secret.ErrorClass);
    }
}
