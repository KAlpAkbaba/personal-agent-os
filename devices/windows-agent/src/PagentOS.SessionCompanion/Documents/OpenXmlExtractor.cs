using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using DocumentFormat.OpenXml;
using DocumentFormat.OpenXml.Packaging;
using PagentOS.Agent.Core.Protocol;
using A = DocumentFormat.OpenXml.Drawing;
using P = DocumentFormat.OpenXml.Presentation;
using S = DocumentFormat.OpenXml.Spreadsheet;
using W = DocumentFormat.OpenXml.Wordprocessing;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// DOCX / XLSX / PPTX through the Open XML SDK (<c>DocumentFormat.OpenXml</c>, MIT; ADR-0083
/// decision 1). Every part is reached through the SDK's package model — the zip's entries
/// are never inflated by hand; only its central directory is read, by
/// <see cref="ContainerGuard"/> in the dispatcher, before the SDK is entered, because the
/// SDK materialises a part's whole DOM in one synchronous getter — and the references follow
/// <c>truth.json.reference_scheme</c>:
/// <list type="bullet">
/// <item>DOCX: <c>p&lt;n&gt;</c> per non-empty paragraph in document order, <c>kind = heading|paragraph</c>
/// with <c>level</c> from the paragraph's <c>Heading N</c> style; <c>t&lt;n&gt;</c> per table with
/// <c>rows</c> and tab/newline text; <c>structure.headings/tables/paragraphs</c>.</item>
/// <item>XLSX: <c>sheet:&lt;name&gt;!A&lt;r&gt;:&lt;lastcol&gt;&lt;r&gt;</c> per non-empty row, cells tab-joined;
/// a formula cell's text is <c>"=" + formula</c> and the block carries <c>formulas: {"B6": "B5*0.2"}</c>;
/// <c>structure.sheets[{name, rows, columns}]</c>, <c>structure.formulas[{ref, formula}]</c>;
/// at most 2 000 rows per sheet (beyond: truncated).</item>
/// <item>PPTX: <c>s&lt;n&gt;</c> per slide with <c>title</c> (the title placeholder) and text = title
/// then the body paragraphs; <c>structure.slides[{index, title}]</c>, <c>structure.slide_count</c>.</item>
/// </list>
/// A package the SDK cannot open is <c>unsupported_format</c> with the SDK's reason.
/// </summary>
public sealed class OpenXmlExtractor : IDocumentExtractor
{
    private static readonly Regex HeadingStyle = new(@"^heading\s*([1-9])$", RegexOptions.Compiled | RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
    private static readonly Regex CellReference = new(@"^([A-Z]+)(\d+)$", RegexOptions.Compiled | RegexOptions.CultureInvariant);

    public bool Supports(string kind) => kind is FileKinds.Docx or FileKinds.Xlsx or FileKinds.Pptx;

    public JsonObject Inspect(string path, string kind, CancellationToken cancellationToken)
        => Guarded(path, kind, () => kind switch
        {
            FileKinds.Docx => InspectDocx(path),
            FileKinds.Xlsx => InspectXlsx(path),
            _ => InspectPptx(path),
        });

    public ExtractResult Extract(string path, string kind, ExtractRequest request, CancellationToken cancellationToken)
        => Guarded(path, kind, () => kind switch
        {
            FileKinds.Docx => ExtractDocx(path, request, cancellationToken),
            FileKinds.Xlsx => ExtractXlsx(path, request, cancellationToken),
            _ => ExtractPptx(path, request, cancellationToken),
        });

    private static T Guarded<T>(string path, string kind, Func<T> read)
    {
        try
        {
            return read();
        }
        catch (Exception ex) when (ex is OpenXmlPackageException or FileFormatException or InvalidDataException or IOException or InvalidOperationException or ArgumentException)
        {
            throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' could not be read as {kind}: {ex.Message}", DocumentErrors.ParseFailed);
        }
    }

    private static string? PackageTitle(OpenXmlPackage package)
    {
        var title = package.PackageProperties.Title;
        return string.IsNullOrWhiteSpace(title) ? null : title.Trim();
    }

    // ================================================================== DOCX

    private static JsonObject InspectDocx(string path)
    {
        using var document = WordprocessingDocument.Open(path, false);
        var body = document.MainDocumentPart?.Document?.Body;
        var paragraphs = 0;
        var tables = 0;
        string? firstHeading = null;
        if (body is not null)
        {
            var styles = HeadingLevels(document);
            foreach (var element in body.ChildElements)
            {
                switch (element)
                {
                    case W.Paragraph paragraph:
                        {
                            var text = ParagraphText(paragraph);
                            if (text.Length == 0)
                            {
                                break;
                            }

                            paragraphs++;
                            firstHeading ??= HeadingLevel(paragraph, styles) is not null ? text : null;
                            break;
                        }

                    case W.Table:
                        tables++;
                        break;
                }
            }
        }

        return new JsonObject
        {
            ["title"] = PackageTitle(document) ?? firstHeading ?? Path.GetFileNameWithoutExtension(path),
            ["paragraphs"] = paragraphs,
            ["tables"] = tables,
        };
    }

    private static ExtractResult ExtractDocx(string path, ExtractRequest request, CancellationToken cancellationToken)
    {
        using var document = WordprocessingDocument.Open(path, false);
        var body = document.MainDocumentPart?.Document?.Body
                   ?? throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' has no document body", DocumentErrors.ParseFailed);
        var styles = HeadingLevels(document);
        var collector = new BlockCollector(request.MaxChars);
        var headings = new JsonArray();
        string? firstHeading = null;
        var paragraphs = 0;
        var tables = 0;

        foreach (var element in body.ChildElements)
        {
            cancellationToken.ThrowIfCancellationRequested();
            switch (element)
            {
                case W.Paragraph paragraph:
                    {
                        var text = ParagraphText(paragraph);
                        if (text.Length == 0)
                        {
                            break;
                        }

                        paragraphs++;
                        var level = HeadingLevel(paragraph, styles);
                        if (level is not null)
                        {
                            headings.Add(text);
                            firstHeading ??= text;
                        }

                        if (!collector.Full && request.Text)
                        {
                            collector.Add(level is null
                                ? new DocumentBlock($"p{paragraphs}", "paragraph", text)
                                : new DocumentBlock($"p{paragraphs}", "heading", text, new JsonObject { ["level"] = level.Value }));
                        }

                        break;
                    }

                case W.Table table:
                    {
                        tables++;
                        if (collector.Full || !request.Tables)
                        {
                            break;
                        }

                        var rows = new JsonArray();
                        var lines = new List<string>();
                        foreach (var row in table.Elements<W.TableRow>())
                        {
                            var cells = new JsonArray();
                            var cellTexts = new List<string>();
                            foreach (var cell in row.Elements<W.TableCell>())
                            {
                                var cellText = string.Join("\n", cell.Elements<W.Paragraph>().Select(ParagraphText).Where(t => t.Length > 0));
                                cells.Add(cellText);
                                cellTexts.Add(cellText);
                            }

                            rows.Add(cells);
                            lines.Add(string.Join("\t", cellTexts));
                        }

                        collector.Add(new DocumentBlock($"t{tables}", "table", string.Join("\n", lines), new JsonObject { ["rows"] = rows }));
                        break;
                    }
            }
        }

        var structure = new JsonObject
        {
            ["headings"] = headings,
            ["tables"] = tables,
            ["paragraphs"] = paragraphs,
        };
        return new ExtractResult(PackageTitle(document) ?? firstHeading ?? Path.GetFileNameWithoutExtension(path), collector.Blocks, structure, collector.Truncated);
    }

    /// <summary>Style id → heading level, from the styles part: a style whose NAME is <c>heading N</c> (Word's built-ins) or whose id is <c>HeadingN</c>.</summary>
    private static Dictionary<string, int> HeadingLevels(WordprocessingDocument document)
    {
        var levels = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        var styles = document.MainDocumentPart?.StyleDefinitionsPart?.Styles;
        if (styles is null)
        {
            return levels;
        }

        foreach (var style in styles.Elements<W.Style>())
        {
            var id = style.StyleId?.Value;
            if (string.IsNullOrEmpty(id))
            {
                continue;
            }

            var name = style.StyleName?.Val?.Value ?? string.Empty;
            var match = HeadingStyle.Match(name);
            if (!match.Success)
            {
                match = HeadingStyle.Match(id);
            }

            if (match.Success)
            {
                levels[id] = int.Parse(match.Groups[1].Value, CultureInfo.InvariantCulture);
            }
        }

        return levels;
    }

    private static int? HeadingLevel(W.Paragraph paragraph, Dictionary<string, int> styles)
    {
        var styleId = paragraph.ParagraphProperties?.ParagraphStyleId?.Val?.Value;
        if (styleId is not null && styles.TryGetValue(styleId, out var level))
        {
            return level;
        }

        if (styleId is not null)
        {
            var match = HeadingStyle.Match(styleId);
            if (match.Success)
            {
                return int.Parse(match.Groups[1].Value, CultureInfo.InvariantCulture);
            }
        }

        return null;
    }

    /// <summary>The visible text of a paragraph: <c>w:t</c> runs, tabs and breaks — never field codes or deleted text.</summary>
    private static string ParagraphText(W.Paragraph paragraph)
    {
        var builder = new StringBuilder();
        foreach (var element in paragraph.Descendants())
        {
            switch (element)
            {
                case W.Text text:
                    builder.Append(text.Text);
                    break;
                case W.TabChar:
                    builder.Append('\t');
                    break;
                case W.Break or W.CarriageReturn:
                    builder.Append('\n');
                    break;
                case W.NoBreakHyphen:
                    builder.Append('-');
                    break;
            }
        }

        return builder.ToString().Trim();
    }

    // ================================================================== XLSX

    private static JsonObject InspectXlsx(string path)
    {
        using var document = SpreadsheetDocument.Open(path, false);
        var sheets = new JsonArray();
        var workbook = document.WorkbookPart?.Workbook;
        if (workbook?.Sheets is not null)
        {
            foreach (var sheet in workbook.Sheets.Elements<S.Sheet>())
            {
                sheets.Add(sheet.Name?.Value ?? string.Empty);
            }
        }

        return new JsonObject
        {
            ["title"] = PackageTitle(document) ?? Path.GetFileNameWithoutExtension(path),
            ["sheets"] = sheets,
        };
    }

    private static ExtractResult ExtractXlsx(string path, ExtractRequest request, CancellationToken cancellationToken)
    {
        using var document = SpreadsheetDocument.Open(path, false);
        var workbookPart = document.WorkbookPart
                           ?? throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' has no workbook", DocumentErrors.ParseFailed);
        var shared = SharedStrings(workbookPart);
        var collector = new BlockCollector(request.MaxChars);
        var sheetsStructure = new JsonArray();
        var formulasStructure = new JsonArray();
        var sheetFound = request.Sheet is null;

        foreach (var sheet in workbookPart.Workbook?.Sheets?.Elements<S.Sheet>() ?? [])
        {
            cancellationToken.ThrowIfCancellationRequested();
            var name = sheet.Name?.Value ?? string.Empty;
            if (request.Sheet is not null && !string.Equals(name, request.Sheet, StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            sheetFound = true;
            if (sheet.Id?.Value is not { } relationshipId || workbookPart.GetPartById(relationshipId) is not WorksheetPart worksheetPart)
            {
                continue;
            }

            var sheetData = worksheetPart.Worksheet?.GetFirstChild<S.SheetData>();
            var maxRow = 0;
            var maxColumn = 0;
            var rowsSeen = 0;
            if (sheetData is not null)
            {
                foreach (var row in sheetData.Elements<S.Row>())
                {
                    cancellationToken.ThrowIfCancellationRequested();
                    var rowIndex = (int)(row.RowIndex?.Value ?? (uint)(maxRow + 1));
                    var cells = new SortedDictionary<int, string>();
                    JsonObject? formulas = null;
                    foreach (var cell in row.Elements<S.Cell>())
                    {
                        var cellReference = cell.CellReference?.Value;
                        var match = cellReference is null ? null : CellReference.Match(cellReference);
                        var column = match is { Success: true } ? ColumnIndex(match.Groups[1].Value) : cells.Count + 1;
                        var formula = cell.CellFormula?.Text;
                        string text;
                        if (!string.IsNullOrEmpty(formula))
                        {
                            text = "=" + formula;
                            formulas ??= new JsonObject();
                            var cellRef = ColumnLetters(column) + rowIndex.ToString(CultureInfo.InvariantCulture);
                            formulas[cellRef] = formula;
                            formulasStructure.Add(new JsonObject { ["ref"] = $"sheet:{name}!{cellRef}", ["formula"] = formula });
                        }
                        else
                        {
                            text = CellText(cell, shared);
                        }

                        if (text.Length > 0)
                        {
                            cells[column] = text;
                        }
                    }

                    if (cells.Count == 0)
                    {
                        continue;
                    }

                    rowsSeen++;
                    var lastColumn = cells.Keys.Max();
                    maxRow = Math.Max(maxRow, rowIndex);
                    maxColumn = Math.Max(maxColumn, lastColumn);
                    if (rowsSeen > DocumentCapabilityNames.MaxSheetRows)
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
                        texts.Add(cells.TryGetValue(column, out var value) ? value : string.Empty);
                    }

                    var extra = new JsonObject { ["sheet"] = name };
                    if (formulas is not null)
                    {
                        extra["formulas"] = formulas;
                    }

                    var reference = $"sheet:{name}!A{rowIndex}:{ColumnLetters(lastColumn)}{rowIndex}";
                    collector.Add(new DocumentBlock(reference, "row", string.Join("\t", texts), extra));
                }
            }

            sheetsStructure.Add(new JsonObject { ["name"] = name, ["rows"] = maxRow, ["columns"] = maxColumn });
        }

        if (!sheetFound)
        {
            throw DocumentErrors.NotFound($"the workbook has no sheet named '{request.Sheet}'");
        }

        var structure = new JsonObject { ["sheets"] = sheetsStructure, ["formulas"] = formulasStructure };
        return new ExtractResult(PackageTitle(document) ?? Path.GetFileNameWithoutExtension(path), collector.Blocks, structure, collector.Truncated);
    }

    private static List<string> SharedStrings(WorkbookPart workbookPart)
    {
        var strings = new List<string>();
        var table = workbookPart.SharedStringTablePart?.SharedStringTable;
        if (table is null)
        {
            return strings;
        }

        foreach (var item in table.Elements<S.SharedStringItem>())
        {
            strings.Add(item.InnerText);
        }

        return strings;
    }

    private static string CellText(S.Cell cell, List<string> shared)
    {
        var raw = cell.CellValue?.Text ?? string.Empty;
        var type = cell.DataType?.Value;
        if (type == S.CellValues.SharedString)
        {
            return int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out var index) && index >= 0 && index < shared.Count ? shared[index] : string.Empty;
        }

        if (type == S.CellValues.InlineString)
        {
            return cell.InlineString?.InnerText ?? cell.InnerText;
        }

        if (type == S.CellValues.Boolean)
        {
            return raw == "1" ? "TRUE" : "FALSE";
        }

        return raw.Trim();
    }

    private static int ColumnIndex(string letters)
    {
        var index = 0;
        foreach (var ch in letters)
        {
            index = index * 26 + (char.ToUpperInvariant(ch) - 'A' + 1);
        }

        return index;
    }

    private static string ColumnLetters(int index)
    {
        var builder = new StringBuilder();
        while (index > 0)
        {
            index--;
            builder.Insert(0, (char)('A' + index % 26));
            index /= 26;
        }

        return builder.ToString();
    }

    // ================================================================== PPTX

    private static JsonObject InspectPptx(string path)
    {
        using var document = PresentationDocument.Open(path, false);
        var count = document.PresentationPart?.Presentation?.SlideIdList?.Elements<P.SlideId>().Count() ?? 0;
        return new JsonObject
        {
            ["title"] = PackageTitle(document) ?? Path.GetFileNameWithoutExtension(path),
            ["slides"] = count,
        };
    }

    private static ExtractResult ExtractPptx(string path, ExtractRequest request, CancellationToken cancellationToken)
    {
        using var document = PresentationDocument.Open(path, false);
        var presentationPart = document.PresentationPart
                               ?? throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' has no presentation", DocumentErrors.ParseFailed);
        var collector = new BlockCollector(request.MaxChars);
        var slides = new JsonArray();
        var index = 0;
        string? firstTitle = null;
        foreach (var slideId in presentationPart.Presentation?.SlideIdList?.Elements<P.SlideId>() ?? [])
        {
            cancellationToken.ThrowIfCancellationRequested();
            index++;
            if (slideId.RelationshipId?.Value is not { } relationshipId || presentationPart.GetPartById(relationshipId) is not SlidePart slidePart)
            {
                slides.Add(new JsonObject { ["index"] = index, ["title"] = null });
                continue;
            }

            string? title = null;
            var body = new List<string>();
            var tree = slidePart.Slide?.CommonSlideData?.ShapeTree;
            if (tree is not null)
            {
                foreach (var shape in tree.ChildElements)
                {
                    if (shape is P.Shape s && IsTitlePlaceholder(s) && title is null)
                    {
                        title = ShapeText(s).FirstOrDefault(t => t.Length > 0);
                        continue;
                    }

                    foreach (var paragraph in shape.Descendants<A.Paragraph>())
                    {
                        var text = ParagraphText(paragraph);
                        if (text.Length > 0)
                        {
                            body.Add(text);
                        }
                    }
                }
            }

            firstTitle ??= title;
            slides.Add(new JsonObject { ["index"] = index, ["title"] = title });
            if (request.SlideRange is { } range && (index < range.From || index > range.To))
            {
                continue;
            }

            if (collector.Full)
            {
                continue;
            }

            var lines = title is null ? body : [title, .. body];
            collector.Add(new DocumentBlock($"s{index}", "slide", string.Join("\n", lines), new JsonObject { ["title"] = title }));
        }

        var structure = new JsonObject { ["slides"] = slides, ["slide_count"] = index };
        return new ExtractResult(PackageTitle(document) ?? firstTitle ?? Path.GetFileNameWithoutExtension(path), collector.Blocks, structure, collector.Truncated);
    }

    private static bool IsTitlePlaceholder(P.Shape shape)
    {
        var placeholder = shape.NonVisualShapeProperties?.ApplicationNonVisualDrawingProperties?.PlaceholderShape;
        var type = placeholder?.Type?.Value;
        return type == P.PlaceholderValues.Title || type == P.PlaceholderValues.CenteredTitle;
    }

    private static IEnumerable<string> ShapeText(P.Shape shape)
    {
        var body = shape.TextBody;
        if (body is null)
        {
            yield break;
        }

        foreach (var paragraph in body.Elements<A.Paragraph>())
        {
            yield return ParagraphText(paragraph);
        }
    }

    private static string ParagraphText(A.Paragraph paragraph)
    {
        var builder = new StringBuilder();
        foreach (var element in paragraph.Descendants())
        {
            switch (element)
            {
                case A.Text text:
                    builder.Append(text.Text);
                    break;
                case A.Break:
                    builder.Append('\n');
                    break;
            }
        }

        return builder.ToString().Trim();
    }
}
