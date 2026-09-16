using System.IO.Compression;
using System.Text;
using PagentOS.Agent.Core.Commands;
using PagentOS.SessionCompanion.Documents;
using Xunit;

namespace PagentOS.Agent.Tests.Documents;

/// <summary>
/// B52 (req 143-146): what the oracle fixtures cannot show - the refusals and the bounds. The
/// real files from a real producer are held to account by <see cref="ExtractionOracleTests"/>;
/// these are the hostile and broken ones, each answered with the family's typed failure, never
/// an exception from inside a parser and never an empty success.
/// </summary>
public sealed class LegacyFormatBoundsTests : IDisposable
{
    private readonly string _root = Directory.CreateTempSubdirectory("pagentos-b52-").FullName;

    public void Dispose()
    {
        try
        {
            Directory.Delete(_root, recursive: true);
        }
        catch (IOException)
        {
        }
    }

    private string Write(string name, byte[] bytes)
    {
        var path = Path.Combine(_root, name);
        File.WriteAllBytes(path, bytes);
        return path;
    }

    private static string DetailOf(CapabilityException ex)
        => ex.Detail.TryGetValue(DocumentErrors.DetailKey, out var value) ? value?.ToString() ?? string.Empty : string.Empty;

    private static CapabilityException Refused(Action action) => Assert.Throws<CapabilityException>(action);

    [Fact]
    public void A_file_that_is_not_a_compound_document_is_parse_failed_for_every_legacy_kind()
    {
        foreach (var kind in new[] { FileKinds.Doc, FileKinds.Xls, FileKinds.Ppt })
        {
            var path = Write($"fake.{kind}", Encoding.ASCII.GetBytes("this is plain text, not an OLE file at all"));
            var ex = Refused(() => new LegacyOfficeExtractor().Extract(path, kind, new ExtractRequest(), CancellationToken.None));
            Assert.Equal(DocumentErrors.ParseFailed, DetailOf(ex));
        }
    }

    [Fact]
    public void A_truncated_compound_header_is_parse_failed_not_an_index_exception()
    {
        var bytes = new byte[600];
        CompoundFile.Signature.CopyTo(bytes, 0);
        BitConverter.GetBytes((ushort)9).CopyTo(bytes, 30);
        BitConverter.GetBytes((ushort)6).CopyTo(bytes, 32);
        BitConverter.GetBytes(uint.MaxValue).CopyTo(bytes, 44);
        var path = Write("broken.doc", bytes);
        var ex = Refused(() => new LegacyOfficeExtractor().Extract(path, FileKinds.Doc, new ExtractRequest(), CancellationToken.None));
        Assert.Equal(DocumentErrors.ParseFailed, DetailOf(ex));
    }

    [Fact]
    public void A_file_that_does_not_start_with_rtf_is_parse_failed()
    {
        var path = Write("not.rtf", Encoding.ASCII.GetBytes("{\\notrtf hello}"));
        var ex = Refused(() => new RtfExtractor().Extract(path, FileKinds.Rtf, new ExtractRequest(), CancellationToken.None));
        Assert.Equal(DocumentErrors.ParseFailed, DetailOf(ex));
    }

    [Fact]
    public void An_rtf_nesting_bomb_is_refused_before_it_can_exhaust_the_stack()
    {
        var bomb = "{\\rtf1 " + new string('{', RtfExtractor.MaxGroupDepth + 10) + "x" + new string('}', RtfExtractor.MaxGroupDepth + 10) + "}";
        var path = Write("bomb.rtf", Encoding.ASCII.GetBytes(bomb));
        var ex = Refused(() => new RtfExtractor().Extract(path, FileKinds.Rtf, new ExtractRequest(), CancellationToken.None));
        Assert.Equal(DocumentErrors.ParseFailed, DetailOf(ex));
    }

    [Fact]
    public void Rtf_metadata_and_font_tables_never_become_the_documents_text()
    {
        var rtf = "{\\rtf1\\ansi\\ansicpg1254{\\fonttbl{\\f0 Arial;}}{\\info{\\title Ba\\u351\\'3fl\\u305\\'3fk}{\\author Gizli Yazar}{\\doccomm yorum}}\\pard Metin\\par}";
        var path = Write("meta.rtf", Encoding.ASCII.GetBytes(rtf));
        var result = new RtfExtractor().Extract(path, FileKinds.Rtf, new ExtractRequest(), CancellationToken.None);
        Assert.Equal("Başlık", result.Title);
        var block = Assert.Single(result.Blocks);
        Assert.Equal("Metin", block.Text);
    }

    [Fact]
    public void A_password_protected_odt_is_refused_by_name()
    {
        using var buffer = new MemoryStream();
        using (var zip = new ZipArchive(buffer, ZipArchiveMode.Create, leaveOpen: true))
        {
            WriteEntry(zip, "mimetype", "application/vnd.oasis.opendocument.text");
            WriteEntry(zip, "META-INF/manifest.xml",
                "<manifest:manifest xmlns:manifest=\"urn:oasis:names:tc:opendocument:xmlns:manifest:1.0\">" +
                "<manifest:file-entry manifest:full-path=\"content.xml\"><manifest:encryption-data manifest:checksum=\"x\"/></manifest:file-entry>" +
                "</manifest:manifest>");
            WriteEntry(zip, "content.xml", "not readable ciphertext");
        }

        var path = Write("secret.odt", buffer.ToArray());
        var ex = Refused(() => new OpenPackageExtractor().Extract(path, FileKinds.Odt, new ExtractRequest(), CancellationToken.None));
        Assert.Equal(DocumentErrors.Encrypted, DetailOf(ex));
    }

    [Fact]
    public void An_xhtml_chapter_cannot_pull_in_an_external_entity()
    {
        var secret = Write("secret.txt", Encoding.UTF8.GetBytes("TOP SECRET"));
        using var buffer = new MemoryStream();
        using (var zip = new ZipArchive(buffer, ZipArchiveMode.Create, leaveOpen: true))
        {
            WriteEntry(zip, "mimetype", "application/epub+zip");
            WriteEntry(zip, "META-INF/container.xml",
                "<container xmlns=\"urn:oasis:names:tc:opendocument:xmlns:container\"><rootfiles><rootfile full-path=\"OEBPS/content.opf\"/></rootfiles></container>");
            WriteEntry(zip, "OEBPS/content.opf",
                "<package xmlns=\"http://www.idpf.org/2007/opf\"><metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\"><dc:title>X</dc:title></metadata>" +
                "<manifest><item id=\"c\" href=\"c.xhtml\" media-type=\"application/xhtml+xml\"/></manifest><spine><itemref idref=\"c\"/></spine></package>");
            WriteEntry(zip, "OEBPS/c.xhtml",
                $"<?xml version=\"1.0\"?><!DOCTYPE html [<!ENTITY xxe SYSTEM \"file:///{secret.Replace('\\', '/')}\">]><html xmlns=\"http://www.w3.org/1999/xhtml\"><body><p>&xxe;</p></body></html>");
        }

        var path = Write("xxe.epub", buffer.ToArray());
        try
        {
            var result = new OpenPackageExtractor().Extract(path, FileKinds.Epub, new ExtractRequest(), CancellationToken.None);
            Assert.DoesNotContain(result.Blocks, b => b.Text.Contains("TOP SECRET", StringComparison.Ordinal));
        }
        catch (CapabilityException ex)
        {
            Assert.Equal(DocumentErrors.ParseFailed, DetailOf(ex));
        }
    }

    [Fact]
    public void A_spine_href_that_climbs_out_of_the_archive_is_ignored()
    {
        Assert.Null(OpenPackageExtractor.ResolveHref("OEBPS/", "../../../windows/win.ini"));
        Assert.Null(OpenPackageExtractor.ResolveHref("", "/etc/passwd"));
        Assert.Null(OpenPackageExtractor.ResolveHref("", "file:///c:/x"));
        Assert.Equal("OEBPS/text/c1.xhtml", OpenPackageExtractor.ResolveHref("OEBPS/", "text/c1.xhtml#top"));
        Assert.Equal("text/c1.xhtml", OpenPackageExtractor.ResolveHref("OEBPS/", "../text/c1.xhtml"));
    }

    [Fact]
    public void A_part_that_inflates_past_its_bound_is_a_decompression_bound()
    {
        using var inner = new MemoryStream(new byte[64]);
        using var bounded = new BoundedPartStream(inner, 16, "content.xml");
        var ex = Refused(() => bounded.CopyTo(Stream.Null));
        Assert.Equal(DocumentErrors.DecompressionBound, DetailOf(ex));
    }

    [Fact]
    public void A_biff_formula_decompiles_to_the_text_the_xlsx_extractor_shows()
    {
        // B5*0.2 : ptgRefV(row 4, col 1, both relative) ptgNum(0.2) ptgMul
        var rgce = new List<byte> { 0x44 };
        rgce.AddRange(BitConverter.GetBytes((ushort)4));
        rgce.AddRange(BitConverter.GetBytes((ushort)(1 | 0x4000 | 0x8000)));
        rgce.Add(0x1F);
        rgce.AddRange(BitConverter.GetBytes(0.2));
        rgce.Add(0x05);
        Assert.Equal("B5*0.2", LegacyOfficeExtractor.FormulaText([.. rgce], 0, rgce.Count));

        // An unknown token is null - the caller shows the cached result, never a guess.
        Assert.Null(LegacyOfficeExtractor.FormulaText([0x3F, 0x00], 0, 2));
    }

    private static void WriteEntry(ZipArchive zip, string name, string text)
    {
        var entry = zip.CreateEntry(name);
        using var stream = entry.Open();
        var bytes = Encoding.UTF8.GetBytes(text);
        stream.Write(bytes, 0, bytes.Length);
    }
}
