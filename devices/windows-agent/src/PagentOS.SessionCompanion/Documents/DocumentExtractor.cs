using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// One referenced block of a document (§2, ADR-0083 decision 2): <c>ref</c> is the string an
/// answer will cite (<c>p3</c>, <c>s4</c>, <c>sheet:Ozet!A5:B5</c>, <c>h2:Kararlar</c>,
/// <c>r7</c>, <c>$.ses</c>, <c>L1-40</c>), <c>kind</c> what the block is, <c>text</c> what it
/// says; <see cref="Extra"/> carries the per-kind fields (<c>level</c>, <c>title</c>,
/// <c>rows</c>, <c>formulas</c>, <c>sheet</c>).
/// </summary>
public sealed class DocumentBlock
{
    public DocumentBlock(string reference, string kind, string text, JsonObject? extra = null)
    {
        Ref = reference;
        Kind = kind;
        Text = text;
        Extra = extra;
    }

    public string Ref { get; }

    public string Kind { get; }

    public string Text { get; private set; }

    public JsonObject? Extra { get; private set; }

    /// <summary>Cut the text to <paramref name="chars"/> characters and say so on the block.</summary>
    public void Truncate(int chars)
    {
        Text = Text[..Math.Max(0, Math.Min(chars, Text.Length))];
        Extra ??= new JsonObject();
        Extra["truncated"] = true;
    }

    public JsonObject ToJson()
    {
        var json = new JsonObject { ["ref"] = Ref, ["kind"] = Kind, ["text"] = Text };
        if (Extra is not null)
        {
            foreach (var (key, value) in Extra)
            {
                json[key] = value?.DeepClone();
            }
        }

        return json;
    }
}

/// <summary>What a caller asked <c>document.extract</c> for (§2 payload), already validated.</summary>
public sealed record ExtractRequest(
    bool Text = true,
    bool Structure = true,
    bool Tables = true,
    (int From, int To)? PageRange = null,
    string? Sheet = null,
    (int From, int To)? SlideRange = null,
    int MaxChars = DocumentCapabilityNames.MaxExtractChars,
    string? Language = null);

/// <summary>
/// The blocks an extractor produced, under the character budget: a block that fits is
/// added whole; the block that crosses the budget is cut and flagged; everything after it is
/// dropped and <see cref="Truncated"/> says so. A provider that hit one of ITS bounds (200
/// PDF pages, 2 000 sheet rows) marks <see cref="Truncated"/> too.
/// </summary>
public sealed class BlockCollector(int maxChars)
{
    private readonly List<DocumentBlock> _blocks = new();

    public IReadOnlyList<DocumentBlock> Blocks => _blocks;

    public int Chars { get; private set; }

    public bool Truncated { get; private set; }

    /// <summary>True once nothing more will be accepted — an extractor stops walking the document here.</summary>
    public bool Full { get; private set; }

    public bool Add(DocumentBlock block)
    {
        if (Full)
        {
            Truncated = true;
            return false;
        }

        var remaining = maxChars - Chars;
        if (block.Text.Length <= remaining)
        {
            _blocks.Add(block);
            Chars += block.Text.Length;
            return true;
        }

        if (remaining > 0)
        {
            block.Truncate(remaining);
            _blocks.Add(block);
            Chars += block.Text.Length;
        }

        Full = true;
        Truncated = true;
        return false;
    }

    /// <summary>A provider bound (pages, rows) was hit: the result is truncated even if the characters fit.</summary>
    public void MarkTruncated() => Truncated = true;
}

/// <summary>What an extractor answers: the document's title, its blocks and its <c>structure</c>.</summary>
public sealed record ExtractResult(string? Title, IReadOnlyList<DocumentBlock> Blocks, JsonObject Structure, bool Truncated);

/// <summary>
/// A per-format provider (§2 "extraction runs on the device, behind per-format provider
/// interfaces"). <see cref="Inspect"/> reads headers only — sheet names, slide and page
/// counts, the title — and never returns text; <see cref="Extract"/> produces the referenced
/// blocks. Both receive a path the dispatcher has already resolved, confined, size-checked
/// and cleared of secret-bearing names; a provider does no policy of its own.
/// </summary>
public interface IDocumentExtractor
{
    bool Supports(string kind);

    JsonObject Inspect(string path, string kind, CancellationToken cancellationToken);

    ExtractResult Extract(string path, string kind, ExtractRequest request, CancellationToken cancellationToken);
}

/// <summary>The typed failures the family answers with, and the detail each carries for an in-process caller.</summary>
public static class DocumentErrors
{
    public const string DetailKey = "detail";

    public const string TooLarge = "too_large";
    public const string NotText = "not_text";
    public const string UnknownKind = "unknown_kind";
    public const string ParseFailed = "parse_failed";

    /// <summary>The container or a stream would inflate past <see cref="DocumentBounds"/>; the parser was not entered (ADR-0083 addendum 3).</summary>
    public const string DecompressionBound = "decompression_bound";

    /// <summary>A PDF with more pages than <see cref="PagentOS.Agent.Core.Protocol.DocumentCapabilityNames.MaxPdfPages"/> asked for without a <c>page_range</c>; no page was read.</summary>
    public const string PageBound = "page_bound";

    public static CapabilityException Unsupported(string message, string detail)
        => new(ErrorClasses.UnsupportedFormat, message + $" [{detail}]", retryable: false, new Dictionary<string, object?> { [DetailKey] = detail });

    public static CapabilityException NotFound(string message)
        => new(ErrorClasses.NotFound, message, retryable: false);

    public static CapabilityException Denied(string message, string? detail = null)
        => detail is null
            ? new CapabilityException(ErrorClasses.PermissionDenied, message, retryable: false)
            : new CapabilityException(ErrorClasses.PermissionDenied, message + $" [{detail}]", retryable: false, new Dictionary<string, object?> { [DetailKey] = detail });

    public static CapabilityException Invalid(string message)
        => new(ErrorClasses.ValidationError, message, retryable: false);
}
