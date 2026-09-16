using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// B52 (req 146): the binary Office formats, read honestly and only as far as their text.
/// <list type="bullet">
/// <item><c>.doc</c> (Word 97-2003): the FIB, then the piece table in the table stream; field
/// instructions dropped, field results kept; <c>p&lt;n&gt;</c> per non-empty paragraph, cells
/// tab-joined. Heading levels live in the style sheet and paragraph property pages this reader
/// does not parse, so every block is a <c>paragraph</c>.</item>
/// <item><c>.xls</c> (BIFF8): the shared strings, the sheets and their LABELSST / LABEL / NUMBER /
/// RK / MULRK / FORMULA (cached result) / BOOLERR cells; rows referenced exactly as the XLSX
/// extractor does (<c>sheet:&lt;name&gt;!A&lt;r&gt;:&lt;col&gt;&lt;r&gt;</c>, cells tab-joined).</item>
/// <item><c>.ppt</c> (PowerPoint 97-2003): the text atoms of each slide (from the slide list's
/// outline text, or the slide containers' own text boxes when there is no outline);
/// <c>s&lt;n&gt;</c> per slide, title first, as the PPTX extractor does.</item>
/// </list>
/// An encrypted file is refused by name (<c>encrypted</c>); anything unrecognised is
/// <c>parse_failed</c> - never an empty success.
/// </summary>
public sealed class LegacyOfficeExtractor : IDocumentExtractor
{
    static LegacyOfficeExtractor()
    {
        Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
    }

    public bool Supports(string kind) => kind is FileKinds.Doc or FileKinds.Xls or FileKinds.Ppt;

    public JsonObject Inspect(string path, string kind, CancellationToken cancellationToken)
    {
        var result = Extract(path, kind, new ExtractRequest(), cancellationToken);
        var headers = new JsonObject { ["title"] = result.Title };
        foreach (var (key, value) in result.Structure)
        {
            if (key is "sheets" or "slide_count" or "paragraphs")
            {
                headers[key] = value?.DeepClone();
            }
        }

        return headers;
    }

    public ExtractResult Extract(string path, string kind, ExtractRequest request, CancellationToken cancellationToken)
    {
        CompoundFile file;
        try
        {
            file = CompoundFile.Open(path);
        }
        catch (Exception ex) when (ex is IOException or ArgumentException or IndexOutOfRangeException)
        {
            throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' could not be read as {kind}: {ex.Message}", DocumentErrors.ParseFailed);
        }

        try
        {
            return kind switch
            {
                FileKinds.Doc => Word(file, path, request, cancellationToken),
                FileKinds.Xls => Excel(file, path, request, cancellationToken),
                _ => PowerPoint(file, path, request, cancellationToken),
            };
        }
        catch (Exception ex) when (ex is ArgumentException or IndexOutOfRangeException or InvalidDataException or DecoderFallbackException)
        {
            throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' could not be read as {kind}: {ex.Message}", DocumentErrors.ParseFailed);
        }
    }

    // ================================================================== Word

    private static ExtractResult Word(CompoundFile file, string path, ExtractRequest request, CancellationToken cancellationToken)
    {
        var document = file.TryRead("WordDocument") ?? throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' has no WordDocument stream", DocumentErrors.ParseFailed);
        if (document.Length < 0x200 || BitConverter.ToUInt16(document, 0) != 0xA5EC)
        {
            throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' is not a Word 97-2003 document (FIB)", DocumentErrors.ParseFailed);
        }

        var flags = BitConverter.ToUInt16(document, 0x0A);
        if ((flags & 0x0100) != 0)
        {
            throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' is password-protected; its text cannot be read", DocumentErrors.Encrypted);
        }

        var tableName = (flags & 0x0200) != 0 ? "1Table" : "0Table";
        var table = file.TryRead(tableName) ?? throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' has no {tableName} stream", DocumentErrors.ParseFailed);

        // FibBase (32) | csw | FibRgW (csw*2) | cslw | FibRgLw (cslw*4) | cbRgFcLcb | FibRgFcLcb...
        var offset = 32;
        var csw = BitConverter.ToUInt16(document, offset);
        offset += 2 + csw * 2;
        var cslw = BitConverter.ToUInt16(document, offset);
        offset += 2 + cslw * 4;
        offset += 2;
        var fcClx = BitConverter.ToUInt32(document, offset + 33 * 8);
        var lcbClx = BitConverter.ToUInt32(document, offset + 33 * 8 + 4);
        if (lcbClx == 0 || fcClx + lcbClx > table.Length)
        {
            throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' has no readable piece table", DocumentErrors.ParseFailed);
        }

        var text = PieceText(document, table, (int)fcClx, (int)lcbClx, cancellationToken);
        var collector = new BlockCollector(request.MaxChars);
        var paragraphs = 0;
        foreach (var raw in text.Split('\r'))
        {
            var paragraph = TextFileReader.Normalise(raw.Replace('\u0007', '\t').Trim('\t'));
            if (paragraph.Length == 0)
            {
                continue;
            }

            paragraphs++;
            if (!collector.Add(new DocumentBlock($"p{paragraphs}", "paragraph", paragraph)))
            {
                break;
            }
        }

        var title = file.SummaryTitle() ?? Path.GetFileNameWithoutExtension(path);
        return new ExtractResult(title, collector.Blocks, new JsonObject { ["paragraphs"] = paragraphs }, collector.Truncated);
    }

    /// <summary>The document text from the Clx's piece table, fields reduced to their results.</summary>
    private static string PieceText(byte[] document, byte[] table, int fcClx, int lcbClx, CancellationToken cancellationToken)
    {
        var position = fcClx;
        var end = fcClx + lcbClx;
        while (position < end && table[position] == 0x01)
        {
            var cbGrpprl = BitConverter.ToInt16(table, position + 1);
            position += 3 + Math.Max(0, (int)cbGrpprl);
        }

        if (position >= end || table[position] != 0x02)
        {
            throw new InvalidDataException("the Clx carries no piece table (Pcdt)");
        }

        var lcb = BitConverter.ToInt32(table, position + 1);
        var plc = position + 5;
        var pieces = (lcb - 4) / 12;
        if (pieces <= 0 || plc + lcb > table.Length)
        {
            throw new InvalidDataException("the piece table is empty or truncated");
        }

        var builder = new StringBuilder();
        var cp1252 = Encoding.GetEncoding(1252);
        for (var i = 0; i < pieces; i++)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var cpStart = BitConverter.ToInt32(table, plc + i * 4);
            var cpEnd = BitConverter.ToInt32(table, plc + (i + 1) * 4);
            var pcd = plc + (pieces + 1) * 4 + i * 8;
            var fc = BitConverter.ToUInt32(table, pcd + 2);
            var count = cpEnd - cpStart;
            if (count <= 0)
            {
                continue;
            }

            if ((fc & 0x40000000) != 0)
            {
                var start = (int)((fc & ~0x40000000u) / 2);
                if (start + count > document.Length)
                {
                    throw new InvalidDataException("a compressed piece lies outside the WordDocument stream");
                }

                builder.Append(cp1252.GetString(document, start, count));
            }
            else
            {
                if (fc + (long)count * 2 > document.Length)
                {
                    throw new InvalidDataException("a Unicode piece lies outside the WordDocument stream");
                }

                builder.Append(Encoding.Unicode.GetString(document, (int)fc, count * 2));
            }

            if (builder.Length > DocumentBounds.MaxTextPrefixBytes)
            {
                break;
            }
        }

        return StripFields(builder.ToString());
    }

    /// <summary>0x13 instructions 0x14 result 0x15 -> result; other control characters dropped or mapped.</summary>
    private static string StripFields(string text)
    {
        var builder = new StringBuilder(text.Length);
        var depth = 0;
        var inResult = new Stack<bool>();
        foreach (var ch in text)
        {
            switch (ch)
            {
                case '\u0013':
                    depth++;
                    inResult.Push(false);
                    continue;
                case '\u0014':
                    if (inResult.Count > 0)
                    {
                        inResult.Pop();
                        inResult.Push(true);
                    }

                    continue;
                case '\u0015':
                    if (inResult.Count > 0)
                    {
                        inResult.Pop();
                        depth--;
                    }

                    continue;
            }

            if (depth > 0 && inResult.Count > 0 && !inResult.Peek())
            {
                continue;
            }

            switch (ch)
            {
                case '\u000B':
                    builder.Append('\n');
                    break;
                case '\u000C':
                    builder.Append('\r');
                    break;
                case '\r' or '\u0007' or '\t':
                    builder.Append(ch);
                    break;
                case '\u001E':
                    builder.Append('-');
                    break;
                case '\u001F':
                    break;
                default:
                    if (!char.IsControl(ch))
                    {
                        builder.Append(ch);
                    }

                    break;
            }
        }

        return builder.ToString();
    }

    // ================================================================== Excel

    private sealed record Record(ushort Type, byte[] Data, int Offset);

    private static ExtractResult Excel(CompoundFile file, string path, ExtractRequest request, CancellationToken cancellationToken)
    {
        var stream = file.TryRead("Workbook") ?? file.TryRead("Book") ?? throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' has no Workbook stream", DocumentErrors.ParseFailed);
        var records = ReadRecords(stream, cancellationToken);
        if (records.Count == 0 || records[0].Type != 0x0809)
        {
            throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' is not a BIFF8 workbook", DocumentErrors.ParseFailed);
        }

        if (records.Any(r => r.Type == 0x002F))
        {
            throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' is password-protected; its cells cannot be read", DocumentErrors.Encrypted);
        }

        var shared = new List<string>();
        var sheets = new List<(string Name, int Offset)>();
        for (var i = 0; i < records.Count; i++)
        {
            var record = records[i];
            if (record.Type == 0x0085 && record.Data.Length >= 8)
            {
                var position = BitConverter.ToInt32(record.Data, 0);
                var nameOffset = 6;
                sheets.Add((ShortUnicode(record.Data, ref nameOffset), position));
            }
            else if (record.Type == 0x00FC)
            {
                var continues = new List<byte[]>();
                for (var j = i + 1; j < records.Count && records[j].Type == 0x003C; j++)
                {
                    continues.Add(records[j].Data);
                }

                shared = ReadSst(record.Data, continues);
            }
        }

        var collector = new BlockCollector(request.MaxChars);
        var sheetsStructure = new JsonArray();
        foreach (var (name, sheetOffset) in sheets)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (request.Sheet is not null && !string.Equals(request.Sheet, name, StringComparison.Ordinal))
            {
                continue;
            }

            var cells = new SortedDictionary<int, SortedDictionary<int, string>>();
            var start = records.FindIndex(r => r.Offset == sheetOffset);
            if (start < 0)
            {
                continue;
            }

            string? pendingFormulaCell = null;
            for (var i = start + 1; i < records.Count && records[i].Type != 0x000A; i++)
            {
                var r = records[i];
                var d = r.Data;
                switch (r.Type)
                {
                    case 0x00FD when d.Length >= 10:
                        Put(cells, BitConverter.ToUInt16(d, 0), BitConverter.ToUInt16(d, 2), (int)BitConverter.ToUInt32(d, 6) is var isst && isst < shared.Count ? shared[isst] : string.Empty);
                        break;
                    case 0x0204 when d.Length >= 8:
                        {
                            var o = 6;
                            Put(cells, BitConverter.ToUInt16(d, 0), BitConverter.ToUInt16(d, 2), LongUnicode(d, ref o));
                            break;
                        }

                    case 0x0203 when d.Length >= 14:
                        Put(cells, BitConverter.ToUInt16(d, 0), BitConverter.ToUInt16(d, 2), Number(BitConverter.ToDouble(d, 6)));
                        break;
                    case 0x027E when d.Length >= 10:
                        Put(cells, BitConverter.ToUInt16(d, 0), BitConverter.ToUInt16(d, 2), Number(Rk(BitConverter.ToUInt32(d, 6))));
                        break;
                    case 0x00BD when d.Length >= 6:
                        {
                            var row = BitConverter.ToUInt16(d, 0);
                            var first = BitConverter.ToUInt16(d, 2);
                            var count = (d.Length - 6) / 6;
                            for (var k = 0; k < count; k++)
                            {
                                Put(cells, row, first + k, Number(Rk(BitConverter.ToUInt32(d, 4 + k * 6 + 2))));
                            }

                            break;
                        }

                    case 0x0006 when d.Length >= 20:
                        {
                            var row = BitConverter.ToUInt16(d, 0);
                            var column = BitConverter.ToUInt16(d, 2);
                            // The XLSX extractor shows a formula cell as its formula; BIFF carries it as
                            // parsed tokens, decompiled here. Only a token this reader does not know
                            // falls back to the cached result below.
                            if (d.Length >= 22 && FormulaText(d, 22, BitConverter.ToUInt16(d, 20)) is { } formula)
                            {
                                Put(cells, row, column, "=" + formula);
                                break;
                            }

                            if (BitConverter.ToUInt16(d, 12) == 0xFFFF)
                            {
                                switch (d[6])
                                {
                                    case 0:
                                        pendingFormulaCell = $"{row}:{column}";
                                        break;
                                    case 1:
                                        Put(cells, row, column, d[8] != 0 ? "TRUE" : "FALSE");
                                        break;
                                }
                            }
                            else
                            {
                                Put(cells, row, column, Number(BitConverter.ToDouble(d, 6)));
                            }

                            break;
                        }

                    case 0x0207 when pendingFormulaCell is not null:
                        {
                            var parts = pendingFormulaCell.Split(':');
                            var o = 0;
                            Put(cells, int.Parse(parts[0], CultureInfo.InvariantCulture), int.Parse(parts[1], CultureInfo.InvariantCulture), LongUnicode(d, ref o));
                            pendingFormulaCell = null;
                            break;
                        }

                    case 0x0205 when d.Length >= 8 && d[7] == 0:
                        Put(cells, BitConverter.ToUInt16(d, 0), BitConverter.ToUInt16(d, 2), d[6] != 0 ? "TRUE" : "FALSE");
                        break;
                }
            }

            var maxRow = 0;
            var maxColumn = 0;
            var rowsSeen = 0;
            foreach (var (rowIndex, row) in cells)
            {
                var nonEmpty = row.Where(kv => kv.Value.Length > 0).ToList();
                if (nonEmpty.Count == 0)
                {
                    continue;
                }

                rowsSeen++;
                var excelRow = rowIndex + 1;
                var lastColumn = nonEmpty.Max(kv => kv.Key) + 1;
                maxRow = Math.Max(maxRow, excelRow);
                maxColumn = Math.Max(maxColumn, lastColumn);
                if (rowsSeen > PagentOS.Agent.Core.Protocol.DocumentCapabilityNames.MaxSheetRows)
                {
                    collector.MarkTruncated();
                    break;
                }

                if (collector.Full)
                {
                    continue;
                }

                var texts = new List<string>();
                for (var column = 1; column <= lastColumn; column++)
                {
                    texts.Add(row.TryGetValue(column - 1, out var value) ? value : string.Empty);
                }

                collector.Add(new DocumentBlock($"sheet:{name}!A{excelRow}:{ColumnLetters(lastColumn)}{excelRow}", "row", string.Join("\t", texts), new JsonObject { ["sheet"] = name }));
            }

            sheetsStructure.Add(new JsonObject { ["name"] = name, ["rows"] = maxRow, ["columns"] = maxColumn });
        }

        if (request.Sheet is not null && sheetsStructure.Count == 0)
        {
            throw DocumentErrors.NotFound($"the workbook has no sheet named '{request.Sheet}'");
        }

        var title = file.SummaryTitle() ?? Path.GetFileNameWithoutExtension(path);
        return new ExtractResult(title, collector.Blocks, new JsonObject { ["sheets"] = sheetsStructure }, collector.Truncated);
    }

    /// <summary>BIFF8 function indexes this decompiler names (the common ones; others fall back).</summary>
    private static readonly Dictionary<ushort, (string Name, int Args)> Functions = new()
    {
        [0] = ("COUNT", -1), [1] = ("IF", -1), [4] = ("SUM", -1), [5] = ("AVERAGE", -1), [6] = ("MIN", -1),
        [7] = ("MAX", -1), [8] = ("ROW", -1), [9] = ("COLUMN", -1), [15] = ("SIN", 1), [16] = ("COS", 1),
        [19] = ("PI", 0), [20] = ("SQRT", 1), [24] = ("ABS", 1), [25] = ("INT", 1), [26] = ("SIGN", 1),
        [27] = ("ROUND", 2), [30] = ("REPT", 2), [31] = ("MID", 3), [32] = ("LEN", 1), [36] = ("AND", -1),
        [37] = ("OR", -1), [38] = ("NOT", 1), [39] = ("MOD", 2), [65] = ("DATE", 3), [66] = ("TIME", 3),
        [67] = ("DAY", 1), [68] = ("MONTH", 1), [69] = ("YEAR", 1), [74] = ("NOW", 0), [100] = ("CHOOSE", -1),
        [101] = ("HLOOKUP", -1), [102] = ("VLOOKUP", -1), [111] = ("CHAR", 1), [112] = ("LOWER", 1),
        [113] = ("UPPER", 1), [115] = ("LEFT", -1), [116] = ("RIGHT", -1), [118] = ("TRIM", 1),
        [169] = ("COUNTA", -1), [212] = ("ROUNDUP", 2), [213] = ("ROUNDDOWN", 2), [221] = ("TODAY", 0),
        [336] = ("CONCATENATE", -1), [337] = ("POWER", 2), [345] = ("SUMIF", -1), [346] = ("COUNTIF", -1),
    };

    /// <summary>
    /// The formula text of a BIFF8 <c>rgce</c> (reverse Polish tokens), or null when a token is one
    /// this reader does not decode - the caller then shows the cached result instead of a guess.
    /// </summary>
    public static string? FormulaText(byte[] data, int start, int length)
    {
        var end = start + length;
        if (length <= 0 || end > data.Length)
        {
            return null;
        }

        var stack = new Stack<string>();
        var position = start;

        string Pop() => stack.Count > 0 ? stack.Pop() : throw new InvalidDataException("formula stack underflow");

        string Binary(string op)
        {
            var right = Pop();
            var left = Pop();
            return left + op + right;
        }

        try
        {
            while (position < end)
            {
                var ptg = data[position++];
                var token = ptg >= 0x20 ? (byte)((ptg & 0x1F) | 0x20) : ptg;
                switch (token)
                {
                    case 0x03: stack.Push(Binary("+")); break;
                    case 0x04: stack.Push(Binary("-")); break;
                    case 0x05: stack.Push(Binary("*")); break;
                    case 0x06: stack.Push(Binary("/")); break;
                    case 0x07: stack.Push(Binary("^")); break;
                    case 0x08: stack.Push(Binary("&")); break;
                    case 0x09: stack.Push(Binary("<")); break;
                    case 0x0A: stack.Push(Binary("<=")); break;
                    case 0x0B: stack.Push(Binary("=")); break;
                    case 0x0C: stack.Push(Binary(">=")); break;
                    case 0x0D: stack.Push(Binary(">")); break;
                    case 0x0E: stack.Push(Binary("<>")); break;
                    case 0x12: stack.Push("+" + Pop()); break;
                    case 0x13: stack.Push("-" + Pop()); break;
                    case 0x14: stack.Push(Pop() + "%"); break;
                    case 0x15: stack.Push("(" + Pop() + ")"); break;
                    case 0x16: stack.Push(string.Empty); break;
                    case 0x17:
                        {
                            var cch = data[position];
                            var high = (data[position + 1] & 0x01) != 0;
                            var text = high ? Encoding.Unicode.GetString(data, position + 2, cch * 2) : Encoding.GetEncoding(1252).GetString(data, position + 2, cch);
                            position += 2 + (high ? cch * 2 : cch);
                            stack.Push("\"" + text.Replace("\"", "\"\"") + "\"");
                            break;
                        }

                    case 0x19:
                        {
                            // ptgAttr: an optimisation hint; SUM over one argument carries its own name.
                            var flags = data[position];
                            var argument = BitConverter.ToUInt16(data, position + 1);
                            position += 3;
                            if ((flags & 0x04) != 0)
                            {
                                position += (argument + 1) * 2;
                            }
                            else if ((flags & 0x10) != 0)
                            {
                                stack.Push("SUM(" + Pop() + ")");
                            }

                            break;
                        }

                    case 0x1C:
                        return null;
                    case 0x1D:
                        stack.Push(data[position++] != 0 ? "TRUE" : "FALSE");
                        break;
                    case 0x1E:
                        stack.Push(BitConverter.ToUInt16(data, position).ToString(CultureInfo.InvariantCulture));
                        position += 2;
                        break;
                    case 0x1F:
                        stack.Push(Number(BitConverter.ToDouble(data, position)));
                        position += 8;
                        break;
                    case 0x21:
                        {
                            var index = BitConverter.ToUInt16(data, position);
                            position += 2;
                            if (!Functions.TryGetValue(index, out var function) || function.Args < 0)
                            {
                                return null;
                            }

                            var args = new string[function.Args];
                            for (var a = function.Args - 1; a >= 0; a--)
                            {
                                args[a] = Pop();
                            }

                            stack.Push(function.Name + "(" + string.Join(",", args) + ")");
                            break;
                        }

                    case 0x22:
                        {
                            var count = data[position] & 0x7F;
                            var index = (ushort)(BitConverter.ToUInt16(data, position + 1) & 0x7FFF);
                            position += 3;
                            if (!Functions.TryGetValue(index, out var function))
                            {
                                return null;
                            }

                            var args = new string[count];
                            for (var a = count - 1; a >= 0; a--)
                            {
                                args[a] = Pop();
                            }

                            stack.Push(function.Name + "(" + string.Join(",", args) + ")");
                            break;
                        }

                    case 0x24:
                        stack.Push(CellReference(BitConverter.ToUInt16(data, position), BitConverter.ToUInt16(data, position + 2)));
                        position += 4;
                        break;
                    case 0x25:
                        {
                            var firstRow = BitConverter.ToUInt16(data, position);
                            var lastRow = BitConverter.ToUInt16(data, position + 2);
                            var firstColumn = BitConverter.ToUInt16(data, position + 4);
                            var lastColumn = BitConverter.ToUInt16(data, position + 6);
                            position += 8;
                            stack.Push(CellReference(firstRow, firstColumn) + ":" + CellReference(lastRow, lastColumn));
                            break;
                        }

                    default:
                        return null;
                }
            }
        }
        catch (Exception ex) when (ex is InvalidDataException or ArgumentException or IndexOutOfRangeException)
        {
            return null;
        }

        return stack.Count == 1 ? stack.Pop() : null;
    }

    /// <summary>A BIFF8 row/column pair as A1 text; a cleared relative bit is written with <c>$</c>.</summary>
    private static string CellReference(ushort row, ushort column)
    {
        var columnRelative = (column & 0x4000) != 0;
        var rowRelative = (column & 0x8000) != 0;
        var letters = ColumnLetters((column & 0x3FFF) + 1);
        return (columnRelative ? string.Empty : "$") + letters + (rowRelative ? string.Empty : "$") + (row + 1).ToString(CultureInfo.InvariantCulture);
    }

    private static void Put(SortedDictionary<int, SortedDictionary<int, string>> cells, int row, int column, string text)
    {
        if (!cells.TryGetValue(row, out var line))
        {
            line = new SortedDictionary<int, string>();
            cells[row] = line;
        }

        line[column] = text;
    }

    private static List<Record> ReadRecords(byte[] stream, CancellationToken cancellationToken)
    {
        var records = new List<Record>();
        var position = 0;
        while (position + 4 <= stream.Length)
        {
            if ((records.Count & 0x3FF) == 0)
            {
                cancellationToken.ThrowIfCancellationRequested();
            }

            var type = BitConverter.ToUInt16(stream, position);
            var size = BitConverter.ToUInt16(stream, position + 2);
            if (position + 4 + size > stream.Length)
            {
                break;
            }

            records.Add(new Record(type, stream[(position + 4)..(position + 4 + size)], position));
            position += 4 + size;
            if (records.Count > 2_000_000)
            {
                throw new InvalidDataException("the workbook has more records than the bound");
            }
        }

        return records;
    }

    /// <summary>The SST's strings, reading across CONTINUE records (each continuation restarts with an option byte).</summary>
    private static List<string> ReadSst(byte[] first, List<byte[]> continues)
    {
        var result = new List<string>();
        if (first.Length < 8)
        {
            return result;
        }

        var unique = BitConverter.ToUInt32(first, 4);
        var chunks = new List<byte[]> { first };
        chunks.AddRange(continues);
        var chunk = 0;
        var offset = 8;

        byte ReadByte()
        {
            while (offset >= chunks[chunk].Length)
            {
                chunk++;
                offset = 0;
                if (chunk >= chunks.Count)
                {
                    throw new InvalidDataException("the shared string table ends early");
                }
            }

            return chunks[chunk][offset++];
        }

        ushort ReadUInt16() => (ushort)(ReadByte() | (ReadByte() << 8));
        uint ReadUInt32() => (uint)(ReadUInt16() | (ReadUInt16() << 16));

        for (var i = 0; i < unique && i < 1_000_000; i++)
        {
            var cch = ReadUInt16();
            var options = ReadByte();
            var highByte = (options & 0x01) != 0;
            var rich = (options & 0x08) != 0;
            var ext = (options & 0x04) != 0;
            var runs = rich ? ReadUInt16() : 0;
            var extSize = ext ? ReadUInt32() : 0;
            var builder = new StringBuilder(cch);
            for (var c = 0; c < cch; c++)
            {
                if (offset >= chunks[chunk].Length)
                {
                    // A string split across CONTINUE: the continuation's first byte is a fresh option byte.
                    chunk++;
                    offset = 0;
                    if (chunk >= chunks.Count)
                    {
                        throw new InvalidDataException("a shared string ends early");
                    }

                    highByte = (chunks[chunk][offset++] & 0x01) != 0;
                }

                builder.Append(highByte ? (char)ReadUInt16() : (char)ReadByte());
            }

            for (var r = 0; r < runs * 4; r++)
            {
                ReadByte();
            }

            for (var e = 0; e < extSize; e++)
            {
                ReadByte();
            }

            result.Add(builder.ToString());
        }

        return result;
    }

    private static string ShortUnicode(byte[] data, ref int offset)
    {
        var cch = data[offset];
        var high = (data[offset + 1] & 0x01) != 0;
        offset += 2;
        var text = high ? Encoding.Unicode.GetString(data, offset, cch * 2) : Encoding.GetEncoding(1252).GetString(data, offset, cch);
        offset += high ? cch * 2 : cch;
        return text;
    }

    private static string LongUnicode(byte[] data, ref int offset)
    {
        var cch = BitConverter.ToUInt16(data, offset);
        var high = (data[offset + 2] & 0x01) != 0;
        offset += 3;
        var length = Math.Min(high ? cch * 2 : cch, data.Length - offset);
        var text = high ? Encoding.Unicode.GetString(data, offset, length) : Encoding.GetEncoding(1252).GetString(data, offset, length);
        offset += length;
        return text;
    }

    private static double Rk(uint rk)
    {
        var divide = (rk & 0x01) != 0;
        double value = (rk & 0x02) != 0 ? (int)rk >> 2 : BitConverter.Int64BitsToDouble((long)((ulong)(rk & 0xFFFFFFFC) << 32));
        return divide ? value / 100 : value;
    }

    /// <summary>The number as the OOXML cell text carries it: shortest round-trip, invariant culture.</summary>
    private static string Number(double value) => value.ToString("R", CultureInfo.InvariantCulture);

    private static string ColumnLetters(int column)
    {
        var letters = new StringBuilder();
        while (column > 0)
        {
            var remainder = (column - 1) % 26;
            letters.Insert(0, (char)('A' + remainder));
            column = (column - 1) / 26;
        }

        return letters.ToString();
    }

    // ================================================================== PowerPoint

    private sealed record Atom(ushort Type, ushort Instance, bool Container, int Start, int Length);

    private static ExtractResult PowerPoint(CompoundFile file, string path, ExtractRequest request, CancellationToken cancellationToken)
    {
        if (file.Has("EncryptedSummary"))
        {
            throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' is password-protected; its slides cannot be read", DocumentErrors.Encrypted);
        }

        var stream = file.TryRead("PowerPoint Document") ?? throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' has no PowerPoint Document stream", DocumentErrors.ParseFailed);
        var slides = OutlineSlides(stream, cancellationToken);
        if (slides.All(s => s.Count == 0))
        {
            slides = ContainerSlides(stream, cancellationToken);
        }

        var collector = new BlockCollector(request.MaxChars);
        var structure = new JsonArray();
        string? firstTitle = null;
        for (var index = 1; index <= slides.Count; index++)
        {
            var texts = slides[index - 1];
            var title = texts.FirstOrDefault(t => t.IsTitle)?.Text;
            firstTitle ??= title;
            structure.Add(new JsonObject { ["index"] = index, ["title"] = title });
            if (request.SlideRange is { } range && (index < range.From || index > range.To))
            {
                continue;
            }

            if (collector.Full)
            {
                continue;
            }

            var lines = new List<string>();
            if (title is not null)
            {
                lines.Add(title);
            }

            foreach (var text in texts.Where(t => !t.IsTitle || t.Text != title))
            {
                lines.AddRange(text.Text.Split('\n').Select(TextFileReader.Normalise).Where(l => l.Length > 0));
            }

            collector.Add(new DocumentBlock($"s{index}", "slide", string.Join("\n", lines), new JsonObject { ["title"] = title }));
        }

        var documentTitle = file.SummaryTitle() ?? firstTitle ?? Path.GetFileNameWithoutExtension(path);
        return new ExtractResult(documentTitle, collector.Blocks, new JsonObject { ["slides"] = structure, ["slide_count"] = slides.Count }, collector.Truncated);
    }

    private sealed record SlideText(string Text, bool IsTitle);

    /// <summary>Slides from the SlideListWithText (instance 0) outline: a SlidePersistAtom starts a slide.</summary>
    private static List<List<SlideText>> OutlineSlides(byte[] stream, CancellationToken cancellationToken)
    {
        var slides = new List<List<SlideText>>();
        foreach (var list in Walk(stream, 0, stream.Length, 0, cancellationToken).Where(a => a.Type == 0x0FF0 && a.Instance == 0))
        {
            List<SlideText>? current = null;
            uint headerType = 4;
            foreach (var atom in Children(stream, list, cancellationToken))
            {
                switch (atom.Type)
                {
                    case 0x03F3:
                        current = new List<SlideText>();
                        slides.Add(current);
                        headerType = 4;
                        break;
                    case 0x0F9F when atom.Length >= 4:
                        headerType = BitConverter.ToUInt32(stream, atom.Start);
                        break;
                    case 0x0FA0 or 0x0FA8 when current is not null:
                        AddText(current, stream, atom, headerType);
                        break;
                }
            }
        }

        return slides;
    }

    /// <summary>Slides from the SlideContainers in stream order, collecting every text atom inside each.</summary>
    private static List<List<SlideText>> ContainerSlides(byte[] stream, CancellationToken cancellationToken)
    {
        var slides = new List<List<SlideText>>();
        foreach (var slide in Walk(stream, 0, stream.Length, 0, cancellationToken).Where(a => a.Type == 0x03EE))
        {
            var texts = new List<SlideText>();
            uint headerType = 4;
            foreach (var atom in Walk(stream, slide.Start, slide.Start + slide.Length, 1, cancellationToken))
            {
                switch (atom.Type)
                {
                    case 0x0F9F when atom.Length >= 4:
                        headerType = BitConverter.ToUInt32(stream, atom.Start);
                        break;
                    case 0x0FA0 or 0x0FA8:
                        AddText(texts, stream, atom, headerType);
                        headerType = 4;
                        break;
                }
            }

            slides.Add(texts);
        }

        return slides;
    }

    private static void AddText(List<SlideText> into, byte[] stream, Atom atom, uint headerType)
    {
        var raw = atom.Type == 0x0FA0
            ? Encoding.Unicode.GetString(stream, atom.Start, atom.Length - atom.Length % 2)
            : Encoding.GetEncoding(1252).GetString(stream, atom.Start, atom.Length);
        var text = raw.Replace('\r', '\n').Replace('\u000B', '\n');
        var isTitle = headerType is 0 or 6;
        if (isTitle)
        {
            text = TextFileReader.Normalise(text.Replace('\n', ' '));
        }

        if (text.Trim().Length > 0)
        {
            into.Add(new SlideText(text, isTitle));
        }
    }

    private static IEnumerable<Atom> Children(byte[] stream, Atom container, CancellationToken cancellationToken)
    {
        var position = container.Start;
        var end = container.Start + container.Length;
        while (position + 8 <= end)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var header = ReadHeader(stream, position, end);
            if (header is null)
            {
                yield break;
            }

            yield return header;
            position = header.Start + header.Length;
        }
    }

    /// <summary>Every record in [start, end), depth-first, bounded in depth and count.</summary>
    private static IEnumerable<Atom> Walk(byte[] stream, int start, int end, int depth, CancellationToken cancellationToken)
    {
        if (depth > 32)
        {
            throw new InvalidDataException("records nest deeper than 32");
        }

        var position = start;
        var count = 0;
        while (position + 8 <= end)
        {
            if ((++count & 0xFFF) == 0)
            {
                cancellationToken.ThrowIfCancellationRequested();
            }

            var header = ReadHeader(stream, position, end);
            if (header is null)
            {
                yield break;
            }

            yield return header;
            if (header.Container)
            {
                foreach (var child in Walk(stream, header.Start, header.Start + header.Length, depth + 1, cancellationToken))
                {
                    yield return child;
                }
            }

            position = header.Start + header.Length;
        }
    }

    private static Atom? ReadHeader(byte[] stream, int position, int end)
    {
        var verInstance = BitConverter.ToUInt16(stream, position);
        var type = BitConverter.ToUInt16(stream, position + 2);
        var length = BitConverter.ToUInt32(stream, position + 4);
        if (position + 8 + (long)length > end)
        {
            return null;
        }

        return new Atom(type, (ushort)(verInstance >> 4), (verInstance & 0x0F) == 0x0F, position + 8, (int)length);
    }
}
