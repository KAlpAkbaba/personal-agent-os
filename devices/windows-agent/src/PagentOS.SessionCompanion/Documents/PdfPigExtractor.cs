using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using UglyToad.PdfPig;
using UglyToad.PdfPig.DocumentLayoutAnalysis.TextExtractor;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// PDF through PdfPig (Apache-2.0; ADR-0083 decision 1): <c>p&lt;n&gt;</c> per page with the
/// page's text in content order, whitespace-normalised (layout engines differ in whitespace,
/// which is why the oracle's PDF match is <c>contains</c>); <c>structure.pages</c>; the title
/// from the document information dictionary, else the file name. PdfPig is lazy per page,
/// and every stream it inflates — at open, per page, per font — goes through
/// <see cref="BoundedFilterProvider"/>, so a stream bomb is a typed refusal
/// (<c>decompression_bound</c>) before the bytes exist (ADR-0083 addendum 3). A document
/// with more than <see cref="DocumentCapabilityNames.MaxPdfPages"/> pages is refused
/// (<c>page_bound</c>) before any page is read unless the caller names a <c>page_range</c>
/// (at most that many pages), which is then read as asked; <c>file.inspect</c> still reports
/// the true page count. One page's text is kept up to <see cref="DocumentBounds.MaxPdfPageChars"/>
/// characters (the block says <c>truncated: true</c> beyond). An encrypted or unparsable
/// file is <c>unsupported_format</c>.
/// </summary>
public sealed class PdfPigExtractor : IDocumentExtractor
{
    public bool Supports(string kind) => kind == FileKinds.Pdf;

    public JsonObject Inspect(string path, string kind, CancellationToken cancellationToken)
    {
        var bounds = new BoundedFilterProvider();
        return Bounded(bounds, path, () =>
        {
            using var document = Open(path, bounds);
            return new JsonObject
            {
                ["title"] = Title(document, path),
                ["pages"] = document.NumberOfPages,
            };
        });
    }

    public ExtractResult Extract(string path, string kind, ExtractRequest request, CancellationToken cancellationToken)
    {
        var bounds = new BoundedFilterProvider();
        return Bounded(bounds, path, () =>
        {
            using var document = Open(path, bounds);
            var collector = new BlockCollector(request.MaxChars);
            var pages = document.NumberOfPages;
            var from = 1;
            var to = pages;
            if (request.PageRange is { } range)
            {
                from = Math.Max(1, range.From);
                to = Math.Min(pages, range.To);
            }
            else if (pages > DocumentCapabilityNames.MaxPdfPages)
            {
                throw DocumentErrors.Unsupported(
                    $"'{Path.GetFileName(path)}' has {pages} pages, over the {DocumentCapabilityNames.MaxPdfPages}-page bound; no page was read — ask again with a page_range of at most {DocumentCapabilityNames.MaxPdfPages} pages",
                    DocumentErrors.PageBound);
            }

            if (to - from + 1 > DocumentCapabilityNames.MaxPdfPages)
            {
                // The payload validation already bounds a page_range's span; this keeps the loop honest regardless.
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
                    text = ContentOrderTextExtractor.GetText(page, false);
                }
                catch (Exception ex) when (ex is not OperationCanceledException and not DecompressionBoundException)
                {
                    if (bounds.Tripped)
                    {
                        throw new DecompressionBoundException(bounds.Refusal!);
                    }

                    throw DocumentErrors.Unsupported($"page {number} of '{Path.GetFileName(path)}' could not be read: {ex.Message}", DocumentErrors.ParseFailed);
                }

                JsonObject? extra = null;
                if (text.Length > DocumentBounds.MaxPdfPageChars)
                {
                    text = text[..DocumentBounds.MaxPdfPageChars];
                    extra = new JsonObject { ["truncated"] = true };
                    collector.MarkTruncated();
                }

                collector.Add(new DocumentBlock($"p{number}", "page", TextFileReader.Normalise(text), extra));
            }

            var structure = new JsonObject { ["pages"] = pages };
            return new ExtractResult(Title(document, path), collector.Blocks, structure, collector.Truncated);
        });
    }

    /// <summary>Run a read under the provider: a tripped bound, thrown or swallowed by lenient parsing, is <c>decompression_bound</c>.</summary>
    private static T Bounded<T>(BoundedFilterProvider bounds, string path, Func<T> read)
    {
        T result;
        try
        {
            result = read();
        }
        catch (DecompressionBoundException ex)
        {
            throw Refuse(path, ex.Message);
        }

        if (bounds.Tripped)
        {
            throw Refuse(path, bounds.Refusal!);
        }

        return result;
    }

    private static Exception Refuse(string path, string reason)
        => DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' was not read as pdf: {reason}", DocumentErrors.DecompressionBound);

    private static PdfDocument Open(string path, BoundedFilterProvider bounds)
    {
        try
        {
            return PdfDocument.Open(path, new ParsingOptions { FilterProvider = bounds });
        }
        catch (Exception ex) when (ex is not OperationCanceledException and not DecompressionBoundException)
        {
            if (bounds.Tripped)
            {
                throw new DecompressionBoundException(bounds.Refusal!);
            }

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
        catch (Exception ex) when (ex is not DecompressionBoundException)
        {
            // A malformed information dictionary is not a reason to refuse the text.
        }

        return string.IsNullOrWhiteSpace(title) ? Path.GetFileNameWithoutExtension(path) : title.Trim();
    }
}
