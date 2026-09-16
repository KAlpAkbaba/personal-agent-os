using System.Text;
using System.Text.Json.Nodes;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// B52 (req 144): RTF, read by a bounded control-word parser — no package. The reference
/// scheme is DOCX's: <c>p&lt;n&gt;</c> per non-empty paragraph (a paragraph whose style is
/// named "heading N" in the file's own stylesheet is a heading with <c>level</c>), and
/// <c>t&lt;n&gt;</c> per table (cells tab-joined, rows on lines). The title is
/// <c>{\info{\title …}}</c>. Destinations that are not the document's text — fonts, colours,
/// the stylesheet itself, pictures, embedded objects, headers/footers and every
/// <c>{\*\…}</c> group — are skipped, never printed.
/// <para>Bounds: the file is read as a <see cref="DocumentBounds.MaxTextPrefixBytes"/> prefix
/// (the result says <c>truncated</c>); groups nest at most <see cref="MaxGroupDepth"/> deep;
/// <c>\'hh</c> bytes decode in the file's own <c>\ansicpgN</c> code page (1254 for Turkish),
/// <c>\uN</c> as UTF-16 with its <c>\ucN</c> fallback characters skipped.</para>
/// </summary>
public sealed class RtfExtractor : IDocumentExtractor
{
    public const int MaxGroupDepth = 256;

    private static readonly HashSet<string> SkippedDestinations = new(StringComparer.Ordinal)
    {
        "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header", "footer",
        "headerl", "headerr", "headerf", "footerl", "footerr", "footerf", "listtable",
        "listoverridetable", "rsidtbl", "generator", "xmlnstbl", "themedata",
        "colorschememapping", "datastore", "latentstyles", "pgdsctbl", "filetbl", "revtbl",
        "footnote", "fldinst", "shp", "shpinst", "nonshppict", "background", "field-result-none",
    };

    static RtfExtractor()
    {
        Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
    }

    public bool Supports(string kind) => kind == FileKinds.Rtf;

    public JsonObject Inspect(string path, string kind, CancellationToken cancellationToken)
    {
        var parsed = Parse(path, int.MaxValue, cancellationToken);
        return new JsonObject
        {
            ["title"] = parsed.Title ?? Path.GetFileNameWithoutExtension(path),
            ["paragraphs"] = parsed.ParagraphCount,
        };
    }

    public ExtractResult Extract(string path, string kind, ExtractRequest request, CancellationToken cancellationToken)
    {
        var parsed = Parse(path, request.MaxChars, cancellationToken);
        return new ExtractResult(parsed.Title ?? parsed.FirstHeading ?? Path.GetFileNameWithoutExtension(path), parsed.Collector.Blocks, parsed.Structure, parsed.Collector.Truncated);
    }

    private sealed class Parsed(BlockCollector collector)
    {
        public BlockCollector Collector { get; } = collector;
        public string? Title { get; set; }
        public string? FirstHeading { get; set; }
        public int ParagraphCount { get; set; }
        public JsonObject Structure { get; set; } = new();
    }

    private sealed class Group
    {
        public bool Skip { get; set; }
        public int UnicodeSkip { get; set; } = 1;
        public string Destination { get; set; } = string.Empty;
    }

    private static Parsed Parse(string path, int maxChars, CancellationToken cancellationToken)
    {
        byte[] bytes;
        bool truncated;
        using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
        {
            var length = (int)Math.Min(stream.Length, DocumentBounds.MaxTextPrefixBytes);
            bytes = new byte[length];
            var read = 0;
            while (read < length)
            {
                var n = stream.Read(bytes, read, length - read);
                if (n == 0)
                {
                    break;
                }

                read += n;
            }

            truncated = stream.Length > DocumentBounds.MaxTextPrefixBytes;
        }

        if (bytes.Length < 5 || Encoding.ASCII.GetString(bytes, 0, 5) != "{\\rtf")
        {
            throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' does not start with {{\\rtf; it is not an RTF file", DocumentErrors.ParseFailed);
        }

        var parsed = new Parsed(new BlockCollector(maxChars));
        if (truncated)
        {
            parsed.Collector.MarkTruncated();
        }

        var state = new ParserState(parsed, Encoding.GetEncoding(1252));
        state.Run(bytes, cancellationToken);
        return parsed;
    }

    /// <summary>The whole parser: one pass over the bytes, a group stack, and the paragraph/table assembly.</summary>
    private sealed class ParserState(Parsed parsed, Encoding initialEncoding)
    {
        private readonly Stack<Group> _groups = new();
        private readonly StringBuilder _paragraph = new();
        private readonly List<byte> _pendingBytes = new();
        private readonly Dictionary<int, string> _styleNames = new();
        private readonly StringBuilder _styleName = new();
        private readonly StringBuilder _title = new();
        private readonly List<string> _cells = new();
        private readonly List<List<string>> _rows = new();
        private readonly JsonArray _headings = new();
        private Encoding _encoding = initialEncoding;
        private int _paragraphs;
        private int _tables;
        private int _style;
        private int _stylesheetIndex;
        private bool _inTable;
        private int _unicodePending;

        public void Run(byte[] bytes, CancellationToken cancellationToken)
        {
            _groups.Push(new Group());
            var i = 0;
            while (i < bytes.Length)
            {
                if ((i & 0xFFF) == 0)
                {
                    cancellationToken.ThrowIfCancellationRequested();
                }

                if (parsed.Collector.Full)
                {
                    break;
                }

                var b = bytes[i];
                switch (b)
                {
                    case (byte)'{':
                        FlushBytes();
                        if (_groups.Count >= MaxGroupDepth)
                        {
                            throw DocumentErrors.Unsupported($"RTF groups nest deeper than {MaxGroupDepth}; the file was not parsed", DocumentErrors.ParseFailed);
                        }

                        var parent = _groups.Peek();
                        _groups.Push(new Group { Skip = parent.Skip, UnicodeSkip = parent.UnicodeSkip, Destination = parent.Destination });
                        i++;
                        break;
                    case (byte)'}':
                        FlushBytes();
                        EndGroup();
                        i++;
                        break;
                    case (byte)'\\':
                        i = ControlWord(bytes, i + 1);
                        break;
                    case (byte)'\r' or (byte)'\n':
                        i++;
                        break;
                    default:
                        if (_unicodePending > 0)
                        {
                            _unicodePending--;
                        }
                        else
                        {
                            AppendByte(b);
                        }

                        i++;
                        break;
                }
            }

            FlushBytes();
            EndParagraph();
            EndTable();
            parsed.ParagraphCount = _paragraphs;
            parsed.Structure = new JsonObject { ["headings"] = _headings, ["tables"] = _tables };
        }

        private void EndGroup()
        {
            if (_groups.Count <= 1)
            {
                return;
            }

            var closing = _groups.Pop();
            if (closing.Destination == "stylesheet-entry" && _groups.Peek().Destination == "stylesheet")
            {
                var name = _styleName.ToString().Trim().TrimEnd(';').Trim();
                if (name.Length > 0)
                {
                    _styleNames[_stylesheetIndex] = name;
                }

                _styleName.Clear();
                _stylesheetIndex = 0;
            }

            if (closing.Destination is "title" or "title-ud" && _groups.Peek().Destination is not ("title" or "title-ud"))
            {
                var title = TextFileReader.Normalise(_title.ToString());
                _title.Clear();
                if (title.Length > 0)
                {
                    // The Unicode alternative wins over the ANSI title it accompanies.
                    parsed.Title = closing.Destination == "title-ud" ? title : parsed.Title ?? title;
                }
            }
        }

        private int ControlWord(byte[] bytes, int i)
        {
            if (i >= bytes.Length)
            {
                return i;
            }

            var c = bytes[i];
            var group = _groups.Peek();
            if (!IsLetter(c))
            {
                // Control symbols.
                switch (c)
                {
                    case (byte)'\'':
                        if (i + 2 < bytes.Length && TryHex(bytes[i + 1], bytes[i + 2], out var value))
                        {
                            if (_unicodePending > 0)
                            {
                                _unicodePending--;
                            }
                            else
                            {
                                _pendingBytes.Add(value);
                            }
                        }

                        return i + 3;
                    case (byte)'*':
                        group.Skip = true;
                        return i + 1;
                    case (byte)'~':
                        AppendText("\u00A0");
                        return i + 1;
                    case (byte)'_':
                        AppendText("\u2011");
                        return i + 1;
                    case (byte)'-':
                        return i + 1;
                    case (byte)'\\' or (byte)'{' or (byte)'}':
                        AppendByte(c);
                        return i + 1;
                    case (byte)'\r' or (byte)'\n':
                        FlushBytes();
                        EndParagraph();
                        return i + 1;
                    default:
                        return i + 1;
                }
            }

            var start = i;
            while (i < bytes.Length && IsLetter(bytes[i]))
            {
                i++;
            }

            var word = Encoding.ASCII.GetString(bytes, start, i - start);
            int? parameter = null;
            var negative = false;
            if (i < bytes.Length && bytes[i] == (byte)'-')
            {
                negative = true;
                i++;
            }

            var digitsStart = i;
            while (i < bytes.Length && bytes[i] >= (byte)'0' && bytes[i] <= (byte)'9' && i - digitsStart < 10)
            {
                i++;
            }

            if (i > digitsStart && int.TryParse(Encoding.ASCII.GetString(bytes, digitsStart, i - digitsStart), out var number))
            {
                parameter = negative ? -number : number;
            }

            if (i < bytes.Length && bytes[i] == (byte)' ')
            {
                i++;
            }

            Apply(word, parameter, group);
            return i;
        }

        private void Apply(string word, int? parameter, Group group)
        {
            if (SkippedDestinations.Contains(word))
            {
                FlushBytes();
                group.Destination = word;
                if (word is not ("stylesheet" or "info"))
                {
                    group.Skip = true;
                }

                return;
            }

            switch (word)
            {
                case "ansicpg" when parameter is { } cp:
                    try
                    {
                        _encoding = Encoding.GetEncoding(cp);
                    }
                    catch (ArgumentException)
                    {
                        // An unknown code page keeps the previous one; its bytes still decode.
                    }

                    return;
                case "uc" when parameter is { } uc:
                    group.UnicodeSkip = Math.Clamp(uc, 0, 8);
                    return;
                case "u" when parameter is { } code:
                    FlushBytes();
                    AppendText(((char)(code < 0 ? code + 65536 : code)).ToString());
                    _unicodePending = group.UnicodeSkip;
                    return;
                case "ud":
                    // {\upr{ANSI}{\*\ud{UNICODE}}}: the Unicode alternative of the group before it.
                    // Lifted out of the \* skip so its title can replace a lossy ANSI one.
                    group.Destination = "ud";
                    group.Skip = false;
                    return;
                case "title":
                    group.Destination = _groups.Any(g => g.Destination == "ud") ? "title-ud" : "title";
                    group.Skip = false;
                    return;
                case "s" when parameter is { } styleIndex:
                    if (group.Destination == "stylesheet" || _groups.Skip(1).Any(g => g.Destination == "stylesheet"))
                    {
                        group.Destination = "stylesheet-entry";
                        _stylesheetIndex = styleIndex;
                        _styleName.Clear();
                    }
                    else
                    {
                        _style = styleIndex;
                    }

                    return;
                case "pard":
                    _style = 0;
                    _inTable = false;
                    return;
                case "intbl":
                    _inTable = true;
                    return;
                case "par" or "sect" or "page":
                    FlushBytes();
                    if (!_inTable)
                    {
                        EndTable();
                    }

                    EndParagraph();
                    return;
                case "line":
                    AppendText("\n");
                    return;
                case "tab":
                    AppendText("\t");
                    return;
                case "cell":
                    FlushBytes();
                    _cells.Add(TextFileReader.Normalise(_paragraph.ToString()));
                    _paragraph.Clear();
                    return;
                case "row":
                    FlushBytes();
                    if (_cells.Any(c => c.Length > 0))
                    {
                        _rows.Add([.. _cells]);
                    }

                    _cells.Clear();
                    return;
                case "emdash":
                    AppendText("\u2014");
                    return;
                case "endash":
                    AppendText("\u2013");
                    return;
                case "lquote":
                    AppendText("\u2018");
                    return;
                case "rquote":
                    AppendText("\u2019");
                    return;
                case "ldblquote":
                    AppendText("\u201C");
                    return;
                case "rdblquote":
                    AppendText("\u201D");
                    return;
                case "bullet":
                    AppendText("\u2022");
                    return;
            }
        }

        private void AppendByte(byte b)
        {
            _pendingBytes.Add(b);
        }

        private void FlushBytes()
        {
            if (_pendingBytes.Count == 0)
            {
                return;
            }

            var text = _encoding.GetString(_pendingBytes.ToArray());
            _pendingBytes.Clear();
            AppendText(text);
        }

        private void AppendText(string text)
        {
            var group = _groups.Peek();
            if (group.Destination == "stylesheet-entry")
            {
                _styleName.Append(text);
                return;
            }

            if (group.Destination is "title" or "title-ud")
            {
                _title.Append(text);
                return;
            }

            // Inside \info only the title is kept (author, comment, company and the rest are
            // metadata, not the document's text); inside \stylesheet only an entry's name is.
            if (group.Destination is "info" or "stylesheet" || _groups.Any(g => g.Destination is "info" or "stylesheet"))
            {
                return;
            }

            if (group.Skip)
            {
                return;
            }

            _paragraph.Append(text);
        }

        private void EndParagraph()
        {
            var text = TextFileReader.Normalise(_paragraph.ToString());
            _paragraph.Clear();
            if (_inTable || text.Length == 0)
            {
                return;
            }

            EndTable();
            _paragraphs++;
            var level = HeadingLevel(_style);
            if (level is { } l)
            {
                _headings.Add(text);
                parsed.FirstHeading ??= text;
                parsed.Collector.Add(new DocumentBlock($"p{_paragraphs}", "heading", text, new JsonObject { ["level"] = l }));
            }
            else
            {
                parsed.Collector.Add(new DocumentBlock($"p{_paragraphs}", "paragraph", text));
            }
        }

        private void EndTable()
        {
            if (_rows.Count == 0)
            {
                return;
            }

            _tables++;
            // `rows` is the cell grid, exactly as the DOCX extractor carries it.
            var grid = new JsonArray();
            foreach (var row in _rows)
            {
                var cells = new JsonArray();
                foreach (var cell in row)
                {
                    cells.Add(cell);
                }

                grid.Add(cells);
            }

            var tableText = string.Join("\n", _rows.Select(r => string.Join("\t", r)));
            parsed.Collector.Add(new DocumentBlock($"t{_tables}", "table", tableText, new JsonObject { ["rows"] = grid }));
            _rows.Clear();
        }

        /// <summary>"heading 2" / "Heading 2" / "Başlık 2" in the file's stylesheet -> 2.</summary>
        private int? HeadingLevel(int style)
        {
            if (!_styleNames.TryGetValue(style, out var name))
            {
                return null;
            }

            var lowered = name.ToLowerInvariant();
            foreach (var prefix in new[] { "heading ", "başlık ", "baslik " })
            {
                if (lowered.StartsWith(prefix, StringComparison.Ordinal) && int.TryParse(lowered[prefix.Length..].Trim(), out var level) && level is >= 1 and <= 9)
                {
                    return level;
                }
            }

            return null;
        }

        private static bool IsLetter(byte b) => b is >= (byte)'a' and <= (byte)'z' or >= (byte)'A' and <= (byte)'Z';

        private static bool TryHex(byte hi, byte lo, out byte value)
        {
            value = 0;
            var h = HexValue(hi);
            var l = HexValue(lo);
            if (h < 0 || l < 0)
            {
                return false;
            }

            value = (byte)((h << 4) | l);
            return true;
        }

        private static int HexValue(byte b) => b switch
        {
            >= (byte)'0' and <= (byte)'9' => b - '0',
            >= (byte)'a' and <= (byte)'f' => b - 'a' + 10,
            >= (byte)'A' and <= (byte)'F' => b - 'A' + 10,
            _ => -1,
        };
    }
}
