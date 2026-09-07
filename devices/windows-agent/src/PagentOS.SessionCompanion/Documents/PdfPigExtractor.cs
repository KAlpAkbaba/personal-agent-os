using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using UglyToad.PdfPig;
using UglyToad.PdfPig.DocumentLayoutAnalysis.TextExtractor;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// PDF through PdfPig (Apache-2.0; ADR-0083 decision 1): <c>p&lt;n&gt;</c> per page with the
/// page's text in content order, whitespace-normalised (layout engines differ in whitespace,
/// which is why the oracle's PDF match is <c>contains</c>); <c>structure.pages</c>; the title
/// from the document information dictionary, else the file name. At most
/// <see cref="DocumentCapabilityNames.MaxPdfPages"/> pages are extracted (beyond: truncated,
/// the page count still true). An encrypted or unparsable file is <c>unsupported_format</c>.
/// </summary>
public sealed class PdfPigExtractor : IDocumentExtractor
{
    public bool Supports(string kind) => kind == FileKinds.Pdf;

    public JsonObject Inspect(string path, string kind, CancellationToken cancellationToken)
    {
        using var document = Open(path);
        return new JsonObject
        {
            ["title"] = Title(document, path),
            ["pages"] = document.NumberOfPages,
        };
    }

    public ExtractResult Extract(string path, string kind, ExtractRequest request, CancellationToken cancellationToken)
    {
        using var document = Open(path);
        var collector = new BlockCollector(request.MaxChars);
        var pages = document.NumberOfPages;
        var from = 1;
        var to = pages;
        if (request.PageRange is { } range)
        {
            from = Math.Max(1, range.From);
            to = Math.Min(pages, range.To);
        }

        if (to - from + 1 > DocumentCapabilityNames.MaxPdfPages)
        {
            to = from + DocumentCapabilityNames.MaxPdfPages - 1;
            collector.MarkTruncated();
        }

        for (var number = from; number <= to; number++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (collector.Full)
            {
                break;
            }

            string text;
            try
            {
                var page = document.GetPage(number);
                text = TextFileReader.Normalise(ContentOrderTextExtractor.GetText(page, false));
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                throw DocumentErrors.Unsupported($"page {number} of '{Path.GetFileName(path)}' could not be read: {ex.Message}", DocumentErrors.ParseFailed);
            }

            collector.Add(new DocumentBlock($"p{number}", "page", text));
        }

        var structure = new JsonObject { ["pages"] = pages };
        return new ExtractResult(Title(document, path), collector.Blocks, structure, collector.Truncated);
    }

    private static PdfDocument Open(string path)
    {
        try
        {
            return PdfDocument.Open(path);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' could not be opened as a PDF: {ex.Message}", DocumentErrors.ParseFailed);
        }
    }

    private static string Title(PdfDocument document, string path)
    {
        string? title = null;
        try
        {
            title = document.Information?.Title;
        }
        catch (Exception)
        {
            // A malformed information dictionary is not a reason to refuse the text.
        }

        return string.IsNullOrWhiteSpace(title) ? Path.GetFileNameWithoutExtension(path) : title.Trim();
    }
}
