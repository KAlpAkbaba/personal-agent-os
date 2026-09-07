using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// The text-like kinds, natively (§2, <c>truth.json.reference_scheme</c>):
/// <list type="bullet">
/// <item><c>md</c>: <c>h&lt;level&gt;:&lt;title&gt;</c> per heading section, text = the section body;
/// text before the first heading becomes blank-line paragraphs <c>p&lt;n&gt;</c>.</item>
/// <item><c>csv</c>: <c>r&lt;n&gt;</c> per line including the header; <c>structure.columns/rows/delimiter</c>.</item>
/// <item><c>json</c>: <c>$.&lt;key&gt;</c> per top-level key with the value as compact JSON (numbers
/// as written, non-ASCII unescaped — the generator's <c>json.dumps(..., ensure_ascii=False,
/// separators=(",", ":"))</c>); <c>structure.keys</c>.</item>
/// <item><c>source</c>: <c>L&lt;a&gt;-&lt;b&gt;</c> per 40-line chunk; <c>structure.symbols[{name, line}]</c>
/// from <c>def|class|function|public …(</c> lines; <c>structure.lines/language</c>.</item>
/// <item><c>txt</c>: <c>p&lt;n&gt;</c> per blank-line paragraph.</item>
/// </list>
/// A file whose bytes do not sniff as text is <c>unsupported_format</c>.
/// </summary>
public sealed class TextLikeExtractor : IDocumentExtractor
{
    public const int SourceChunkLines = 40;

    private static readonly Regex MarkdownHeading = new(@"^(#{1,6})\s+(.*)$", RegexOptions.Compiled | RegexOptions.CultureInvariant);

    /// <summary>
    /// <c>def name</c> / <c>class name</c> (Python, C#, TypeScript classes), <c>function name</c>
    /// (JavaScript/TypeScript, optionally exported/async/generator), and a <c>public …
    /// name(</c> member (C#/Java: the identifier immediately before the parenthesis).
    /// </summary>
    private static readonly Regex[] SymbolPatterns =
    [
        new(@"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", RegexOptions.Compiled | RegexOptions.CultureInvariant),
        new(@"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][A-Za-z0-9_$]*)", RegexOptions.Compiled | RegexOptions.CultureInvariant),
        new(@"^\s*public\s+(?:[A-Za-z_][A-Za-z0-9_<>\[\],.?]*\s+)*?([A-Za-z_][A-Za-z0-9_]*)\s*\(", RegexOptions.Compiled | RegexOptions.CultureInvariant),
    ];

    public bool Supports(string kind) => FileKinds.IsTextLike(kind);

    public JsonObject Inspect(string path, string kind, CancellationToken cancellationToken)
    {
        var decoded = TextFileReader.Read(path);
        var headers = new JsonObject
        {
            ["is_text"] = decoded.IsText,
            ["encoding"] = decoded.Encoding,
        };
        if (decoded.IsText)
        {
            var lines = TextFileReader.SplitLines(decoded.Text);
            headers["lines"] = lines.Count;
            if (kind == FileKinds.Md)
            {
                headers["title"] = MarkdownTitle(lines);
            }
        }

        return headers;
    }

    public ExtractResult Extract(string path, string kind, ExtractRequest request, CancellationToken cancellationToken)
    {
        var decoded = TextFileReader.Read(path);
        if (!decoded.IsText)
        {
            throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' does not decode as text ({decoded.Encoding}); it is not a {kind} file", DocumentErrors.NotText);
        }

        var collector = new BlockCollector(request.MaxChars);
        cancellationToken.ThrowIfCancellationRequested();
        return kind switch
        {
            FileKinds.Md => Markdown(decoded.Text, collector),
            FileKinds.Csv => Csv(decoded.Text, Path.GetExtension(path), collector),
            FileKinds.Json => Json(decoded.Text, collector),
            FileKinds.Source => Source(decoded.Text, Path.GetExtension(path), collector),
            _ => PlainText(decoded.Text, collector),
        };
    }

    // ------------------------------------------------------------------ md

    private static ExtractResult Markdown(string text, BlockCollector collector)
    {
        var lines = TextFileReader.SplitLines(text);
        var headings = new JsonArray();
        var refs = new HashSet<string>(StringComparer.Ordinal);
        string? currentRef = null;
        JsonObject? currentExtra = null;
        var body = new List<string>();
        var preamble = new List<string>();
        var sawHeading = false;

        void Flush()
        {
            if (currentRef is not null)
            {
                collector.Add(new DocumentBlock(currentRef, "section", TextFileReader.Normalise(string.Join("\n", body)), currentExtra));
            }
        }

        foreach (var line in lines)
        {
            var match = MarkdownHeading.Match(line);
            if (match.Success)
            {
                if (!sawHeading)
                {
                    EmitParagraphs(preamble, collector);
                    sawHeading = true;
                }
                else
                {
                    Flush();
                }

                if (collector.Full)
                {
                    break;
                }

                var level = match.Groups[1].Value.Length;
                var title = match.Groups[2].Value.Trim();
                headings.Add(title);
                currentRef = UniqueRef($"h{level}:{title}", refs);
                currentExtra = new JsonObject { ["level"] = level, ["title"] = title };
                body.Clear();
            }
            else if (sawHeading)
            {
                body.Add(line);
            }
            else
            {
                preamble.Add(line);
            }
        }

        if (!sawHeading)
        {
            EmitParagraphs(preamble, collector);
        }
        else
        {
            Flush();
        }

        var documentTitle = MarkdownTitle(lines);
        return new ExtractResult(documentTitle, collector.Blocks, new JsonObject { ["headings"] = headings }, collector.Truncated);
    }

    private static string? MarkdownTitle(IReadOnlyList<string> lines)
    {
        string? first = null;
        foreach (var line in lines)
        {
            var match = MarkdownHeading.Match(line);
            if (!match.Success)
            {
                continue;
            }

            if (match.Groups[1].Value.Length == 1)
            {
                return match.Groups[2].Value.Trim();
            }

            first ??= match.Groups[2].Value.Trim();
        }

        return first;
    }

    private static string UniqueRef(string reference, HashSet<string> seen)
    {
        if (seen.Add(reference))
        {
            return reference;
        }

        for (var n = 2; ; n++)
        {
            var candidate = $"{reference}#{n}";
            if (seen.Add(candidate))
            {
                return candidate;
            }
        }
    }

    // ------------------------------------------------------------------ csv

    private static ExtractResult Csv(string text, string extension, BlockCollector collector)
    {
        var lines = TextFileReader.SplitLines(text);
        var delimiter = extension.Equals(".tsv", StringComparison.OrdinalIgnoreCase) ? '\t' : DetectDelimiter(lines.Count > 0 ? lines[0] : string.Empty);
        for (var i = 0; i < lines.Count; i++)
        {
            if (!collector.Add(new DocumentBlock($"r{i + 1}", "row", lines[i])))
            {
                break;
            }
        }

        var columns = new JsonArray();
        if (lines.Count > 0)
        {
            foreach (var column in SplitDelimited(lines[0], delimiter))
            {
                columns.Add(column);
            }
        }

        var structure = new JsonObject
        {
            ["columns"] = columns,
            ["rows"] = Math.Max(0, lines.Count - 1),
            ["delimiter"] = delimiter.ToString(),
        };
        return new ExtractResult(null, collector.Blocks, structure, collector.Truncated);
    }

    private static char DetectDelimiter(string header)
    {
        var best = ',';
        var bestCount = header.Count(c => c == ',');
        foreach (var candidate in new[] { ';', '\t', '|' })
        {
            var count = header.Count(c => c == candidate);
            if (count > bestCount)
            {
                best = candidate;
                bestCount = count;
            }
        }

        return best;
    }

    /// <summary>RFC 4180-style split: a quoted field may contain the delimiter and a doubled quote.</summary>
    public static IReadOnlyList<string> SplitDelimited(string line, char delimiter)
    {
        var fields = new List<string>();
        var field = new StringBuilder();
        var quoted = false;
        for (var i = 0; i < line.Length; i++)
        {
            var ch = line[i];
            if (quoted)
            {
                if (ch == '"')
                {
                    if (i + 1 < line.Length && line[i + 1] == '"')
                    {
                        field.Append('"');
                        i++;
                    }
                    else
                    {
                        quoted = false;
                    }
                }
                else
                {
                    field.Append(ch);
                }
            }
            else if (ch == '"' && field.Length == 0)
            {
                quoted = true;
            }
            else if (ch == delimiter)
            {
                fields.Add(field.ToString());
                field.Clear();
            }
            else
            {
                field.Append(ch);
            }
        }

        fields.Add(field.ToString());
        return fields;
    }

    // ------------------------------------------------------------------ json

    private static readonly JsonDocumentOptions JsonOptions = new() { AllowTrailingCommas = true, CommentHandling = JsonCommentHandling.Skip, MaxDepth = 128 };

    private static ExtractResult Json(string text, BlockCollector collector)
    {
        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(text, JsonOptions);
        }
        catch (JsonException ex)
        {
            throw DocumentErrors.Unsupported($"the file is not valid JSON: {ex.Message}", DocumentErrors.ParseFailed);
        }

        using (document)
        {
            var root = document.RootElement;
            var structure = new JsonObject();
            switch (root.ValueKind)
            {
                case JsonValueKind.Object:
                    {
                        var keys = new JsonArray();
                        foreach (var property in root.EnumerateObject())
                        {
                            keys.Add(property.Name);
                            if (!collector.Add(new DocumentBlock($"$.{property.Name}", "key", Compact(property.Value))))
                            {
                                break;
                            }
                        }

                        structure["keys"] = keys;
                        break;
                    }

                case JsonValueKind.Array:
                    {
                        var index = 0;
                        foreach (var item in root.EnumerateArray())
                        {
                            if (!collector.Add(new DocumentBlock($"$[{index}]", "item", Compact(item))))
                            {
                                break;
                            }

                            index++;
                        }

                        structure["length"] = root.GetArrayLength();
                        break;
                    }

                default:
                    collector.Add(new DocumentBlock("$", "value", Compact(root)));
                    break;
            }

            return new ExtractResult(null, collector.Blocks, structure, collector.Truncated);
        }
    }

    /// <summary>
    /// Compact JSON the way the oracle was written: no whitespace, numbers exactly as they
    /// appear in the file (<c>1.0</c> stays <c>1.0</c>), non-ASCII characters unescaped.
    /// </summary>
    public static string Compact(JsonElement element)
    {
        using var buffer = new MemoryStream();
        using (var writer = new Utf8JsonWriter(buffer, new JsonWriterOptions { Indented = false, Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping, SkipValidation = true }))
        {
            WriteCompact(writer, element);
        }

        return Encoding.UTF8.GetString(buffer.ToArray());
    }

    private static void WriteCompact(Utf8JsonWriter writer, JsonElement element)
    {
        switch (element.ValueKind)
        {
            case JsonValueKind.Object:
                writer.WriteStartObject();
                foreach (var property in element.EnumerateObject())
                {
                    writer.WritePropertyName(property.Name);
                    WriteCompact(writer, property.Value);
                }

                writer.WriteEndObject();
                break;
            case JsonValueKind.Array:
                writer.WriteStartArray();
                foreach (var item in element.EnumerateArray())
                {
                    WriteCompact(writer, item);
                }

                writer.WriteEndArray();
                break;
            case JsonValueKind.Number:
                writer.WriteRawValue(element.GetRawText());
                break;
            case JsonValueKind.String:
                writer.WriteStringValue(element.GetString());
                break;
            case JsonValueKind.True:
                writer.WriteBooleanValue(true);
                break;
            case JsonValueKind.False:
                writer.WriteBooleanValue(false);
                break;
            default:
                writer.WriteNullValue();
                break;
        }
    }

    // ------------------------------------------------------------------ source

    private static ExtractResult Source(string text, string extension, BlockCollector collector)
    {
        var lines = TextFileReader.SplitLines(text);
        for (var start = 0; start < lines.Count; start += SourceChunkLines)
        {
            var count = Math.Min(SourceChunkLines, lines.Count - start);
            var chunk = string.Join("\n", lines.Skip(start).Take(count));
            if (!collector.Add(new DocumentBlock($"L{start + 1}-{start + count}", "lines", chunk)))
            {
                break;
            }
        }

        var symbols = new JsonArray();
        for (var i = 0; i < lines.Count; i++)
        {
            var name = SymbolOf(lines[i]);
            if (name is not null)
            {
                symbols.Add(new JsonObject { ["name"] = name, ["line"] = i + 1 });
            }
        }

        var structure = new JsonObject
        {
            ["symbols"] = symbols,
            ["lines"] = lines.Count,
            ["language"] = FileKinds.Language(extension),
        };
        return new ExtractResult(null, collector.Blocks, structure, collector.Truncated);
    }

    public static string? SymbolOf(string line)
    {
        foreach (var pattern in SymbolPatterns)
        {
            var match = pattern.Match(line);
            if (match.Success)
            {
                return match.Groups[1].Value;
            }
        }

        return null;
    }

    // ------------------------------------------------------------------ txt

    private static ExtractResult PlainText(string text, BlockCollector collector)
    {
        var lines = TextFileReader.SplitLines(text);
        var paragraphs = EmitParagraphs(lines, collector);
        return new ExtractResult(null, collector.Blocks, new JsonObject { ["paragraphs"] = paragraphs, ["lines"] = lines.Count }, collector.Truncated);
    }

    /// <summary>Blank-line paragraphs as <c>p&lt;n&gt;</c> blocks; returns how many there were in the text (not how many fit).</summary>
    private static int EmitParagraphs(IReadOnlyList<string> lines, BlockCollector collector)
    {
        var count = 0;
        var current = new List<string>();
        void Flush()
        {
            if (current.Count > 0)
            {
                count++;
                collector.Add(new DocumentBlock($"p{count}", "paragraph", string.Join("\n", current)));
                current.Clear();
            }
        }

        foreach (var line in lines)
        {
            if (string.IsNullOrWhiteSpace(line))
            {
                Flush();
            }
            else
            {
                current.Add(line);
            }
        }

        Flush();
        return count;
    }
}
