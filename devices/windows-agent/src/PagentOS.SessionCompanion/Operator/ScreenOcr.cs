using System.Runtime.InteropServices.WindowsRuntime;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using Windows.Globalization;
using Windows.Graphics.Imaging;
using Windows.Media.Ocr;

namespace PagentOS.SessionCompanion.Operator;

/// <summary>One recognised word: its text and its box, in pixels of the captured picture.</summary>
public sealed record OcrWord(string Text, int X, int Y, int Width, int Height);

/// <summary>One recognised line: its text, the union of its words' boxes, and the words.</summary>
public sealed record OcrLine(string Text, int X, int Y, int Width, int Height, IReadOnlyList<OcrWord> Words)
{
    /// <summary>A line from its words: the box is the union of theirs. A line with no words has no box and is not a line.</summary>
    public static OcrLine? FromWords(string text, IReadOnlyList<OcrWord> words)
    {
        if (words.Count == 0)
        {
            return null;
        }

        var left = words.Min(w => w.X);
        var top = words.Min(w => w.Y);
        var right = words.Max(w => w.X + w.Width);
        var bottom = words.Max(w => w.Y + w.Height);
        return new OcrLine(text, left, top, right - left, bottom - top, words);
    }
}

/// <summary>The recogniser behind <c>screen.ocr</c>; the seam a test replaces.</summary>
public interface IScreenOcrEngine
{
    /// <summary>Whether this machine can recognise the language. A malformed tag is simply not installed.</summary>
    bool IsInstalled(string languageTag);

    /// <summary>Every line the language's recogniser reads in the picture, boxes in the picture's pixels.</summary>
    IReadOnlyList<OcrLine> Recognize(RgbImage image, string languageTag);
}

/// <summary>
/// <c>screen.ocr</c> (ADR-0176) - everything about it that is not the recogniser itself, so
/// all of it runs, and is mutation-tested, without WinRT: which languages are tried, how two
/// languages' readings become one, the bounds, and the shaping of a result that ALWAYS fits
/// one broker frame (a page with a lot of text loses its words' boxes from the bottom up,
/// then its last lines, and says <c>truncated</c> - it never fails for being long). The
/// picture is never in the result and never on disk.
/// </summary>
public static class ScreenOcr
{
    /// <summary>The most languages one request may name.</summary>
    public const int MaxLanguages = 8;

    /// <summary><c>payload.languages</c>: absent = the default order; otherwise 1…8 short strings.</summary>
    public static IReadOnlyList<string> ParseLanguages(JsonObject payload)
    {
        var node = payload["languages"];
        if (node is null)
        {
            return OperatorCapabilityNames.DefaultOcrLanguages;
        }

        if (node is not JsonArray array || array.Count == 0 || array.Count > MaxLanguages)
        {
            throw new CapabilityException(ErrorClasses.ValidationError, $"payload.languages must be an array of 1 to {MaxLanguages} language tags", retryable: false);
        }

        var languages = new List<string>();
        foreach (var item in array)
        {
            if (item is not JsonValue value || !value.TryGetValue<string>(out var tag) || tag.Length is 0 or > 35)
            {
                throw new CapabilityException(ErrorClasses.ValidationError, "payload.languages holds something that is not a language tag", retryable: false);
            }

            if (!languages.Contains(tag, StringComparer.OrdinalIgnoreCase))
            {
                languages.Add(tag);
            }
        }

        return languages;
    }

    /// <summary>The whole capability once the picture is in hand.</summary>
    public static JsonObject Run(RgbImage image, IReadOnlyList<string> requested, IScreenOcrEngine engine, string? windowId, JsonNode? observedWindow)
    {
        var languages = requested.Where(engine.IsInstalled).ToList();
        if (languages.Count == 0)
        {
            throw new CapabilityException(
                ErrorClasses.DependencyUnavailable,
                $"no OCR language installed (asked for {string.Join(", ", requested)})",
                retryable: false);
        }

        var readings = new List<IReadOnlyList<OcrLine>>();
        foreach (var language in languages)
        {
            readings.Add(engine.Recognize(image, language));
        }

        return BuildResult(image.Width, image.Height, languages, Merge(readings), windowId, observedWindow);
    }

    /// <summary>
    /// Every line of the first reading; a line of a later reading only when no line already
    /// kept covers more than half of ITS box (the same text read twice lands on the same
    /// place). The outcome is in reading order: top to bottom, then left to right.
    /// </summary>
    public static IReadOnlyList<OcrLine> Merge(IReadOnlyList<IReadOnlyList<OcrLine>> readings)
    {
        var kept = new List<OcrLine>();
        foreach (var reading in readings)
        {
            foreach (var line in reading)
            {
                if (!kept.Any(existing => CoveredFraction(line, existing) > 0.5))
                {
                    kept.Add(line);
                }
            }
        }

        return [.. kept.OrderBy(l => l.Y).ThenBy(l => l.X)];
    }

    /// <summary>How much of <paramref name="line"/>'s box lies inside <paramref name="other"/>'s, 0…1.</summary>
    public static double CoveredFraction(OcrLine line, OcrLine other)
    {
        long area = (long)line.Width * line.Height;
        if (area <= 0)
        {
            return 0;
        }

        long width = Math.Min(line.X + line.Width, other.X + other.Width) - Math.Max(line.X, other.X);
        long height = Math.Min(line.Y + line.Height, other.Y + other.Height) - Math.Max(line.Y, other.Y);
        return width <= 0 || height <= 0 ? 0 : (double)(width * height) / area;
    }

    /// <summary>
    /// The result, bounded (lines, characters, words per line) and then fitted to the frame
    /// by MEASURING each line as it will be serialized: the words go first, from the bottom
    /// line up; then the trailing lines.
    /// </summary>
    public static JsonObject BuildResult(int width, int height, IReadOnlyList<string> languages, IReadOnlyList<OcrLine> lines, string? windowId, JsonNode? observedWindow)
    {
        var truncated = lines.Count > OperatorCapabilityNames.MaxOcrLines;
        var shaped = new List<(JsonObject Full, int FullBytes, int BareBytes)>();
        foreach (var line in lines.Take(OperatorCapabilityNames.MaxOcrLines))
        {
            truncated |= line.Text.Length > OperatorCapabilityNames.MaxOcrTextChars
                         || line.Words.Count > OperatorCapabilityNames.MaxOcrWordsPerLine
                         || line.Words.Any(w => w.Text.Length > OperatorCapabilityNames.MaxOcrTextChars);
            var full = LineJson(line, withWords: true);
            shaped.Add((full, CaptureFit.SerializedBytes(full), CaptureFit.SerializedBytes(LineJson(line, withWords: false))));
        }

        // What the result costs with no lines at all, at its widest: the largest line_count, and "false".
        var envelope = CaptureFit.SerializedBytes(Assemble(width, height, languages, [], OperatorCapabilityNames.MaxOcrLines, false, windowId, observedWindow));
        var bare = new bool[shaped.Count];
        var count = shaped.Count;
        long total = envelope + shaped.Sum(s => (long)s.FullBytes + 1);

        for (var i = shaped.Count - 1; i >= 0 && total > CaptureFit.MaxResultBytes; i--)
        {
            if (shaped[i].FullBytes > shaped[i].BareBytes)
            {
                bare[i] = true;
                total -= shaped[i].FullBytes - shaped[i].BareBytes;
                truncated = true;
            }
        }

        while (count > 0 && total > CaptureFit.MaxResultBytes)
        {
            count--;
            total -= (bare[count] ? shaped[count].BareBytes : shaped[count].FullBytes) + 1;
            truncated = true;
        }

        while (true)
        {
            var array = new JsonArray();
            for (var i = 0; i < count; i++)
            {
                var node = (JsonObject)shaped[i].Full.DeepClone();
                if (bare[i])
                {
                    node["words"] = new JsonArray();
                }

                array.Add(node);
            }

            var result = Assemble(width, height, languages, array, count, truncated, windowId, observedWindow);

            // The arithmetic above is exact; this is the measurement that makes it a fact.
            if (count == 0 || CaptureFit.SerializedBytes(result) <= CaptureFit.MaxResultBytes)
            {
                return result;
            }

            count--;
            truncated = true;
        }
    }

    private static JsonObject Assemble(int width, int height, IReadOnlyList<string> languages, JsonArray lines, int lineCount, bool truncated, string? windowId, JsonNode? observedWindow)
        => new()
        {
            ["width"] = width,
            ["height"] = height,
            ["scale"] = 1,
            ["languages"] = new JsonArray([.. languages.Select(l => (JsonNode)l)]),
            ["lines"] = lines,
            ["line_count"] = lineCount,
            ["truncated"] = truncated,
            ["window_id"] = windowId,
            ["observed"] = new JsonObject { ["window"] = observedWindow?.DeepClone() },
        };

    private static JsonObject LineJson(OcrLine line, bool withWords)
    {
        var words = new JsonArray();
        if (withWords)
        {
            foreach (var word in line.Words.Take(OperatorCapabilityNames.MaxOcrWordsPerLine))
            {
                words.Add(new JsonObject
                {
                    ["text"] = Bounded(word.Text),
                    ["x"] = word.X,
                    ["y"] = word.Y,
                    ["width"] = word.Width,
                    ["height"] = word.Height,
                });
            }
        }

        return new JsonObject
        {
            ["text"] = Bounded(line.Text),
            ["x"] = line.X,
            ["y"] = line.Y,
            ["width"] = line.Width,
            ["height"] = line.Height,
            ["words"] = words,
        };
    }

    private static string Bounded(string text)
        => text.Length <= OperatorCapabilityNames.MaxOcrTextChars ? text : text[..OperatorCapabilityNames.MaxOcrTextChars];
}

/// <summary>
/// The recogniser that ships with Windows (<see cref="OcrEngine"/>): on-device, no account, no
/// network. The picture goes from the capture's pixels into a <see cref="SoftwareBitmap"/> in
/// memory and nowhere else. Threading as in <see cref="JpegEncoder"/>: no UI thread is needed;
/// the asynchronous call runs on the thread pool and the synchronous dispatch blocks on it.
/// </summary>
public sealed class WindowsOcrEngine : IScreenOcrEngine
{
    public bool IsInstalled(string languageTag)
    {
        try
        {
            return OcrEngine.IsLanguageSupported(new Language(languageTag));
        }
        catch (Exception ex) when (ex is ArgumentException or System.Runtime.InteropServices.COMException)
        {
            return false; // not a well-formed tag: nothing this machine has installed
        }
    }

    public IReadOnlyList<OcrLine> Recognize(RgbImage image, string languageTag)
    {
        if (image.Width > OcrEngine.MaxImageDimension || image.Height > OcrEngine.MaxImageDimension)
        {
            throw new CapabilityException(
                ErrorClasses.ValidationError,
                $"a {image.Width}x{image.Height} picture is over the recogniser's {OcrEngine.MaxImageDimension} px bound",
                retryable: false);
        }

        var engine = OcrEngine.TryCreateFromLanguage(new Language(languageTag))
                     ?? throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"the OCR engine for '{languageTag}' could not be created", retryable: true);

        var bgra = new byte[image.Width * image.Height * 4];
        var rgb = image.Rgb;
        for (int src = 0, dst = 0; dst < bgra.Length; src += 3, dst += 4)
        {
            bgra[dst] = rgb[src + 2];
            bgra[dst + 1] = rgb[src + 1];
            bgra[dst + 2] = rgb[src];
            bgra[dst + 3] = 255;
        }

        OcrResult recognised;
        try
        {
            using var bitmap = SoftwareBitmap.CreateCopyFromBuffer(bgra.AsBuffer(), BitmapPixelFormat.Bgra8, image.Width, image.Height, BitmapAlphaMode.Premultiplied);
            recognised = Task.Run(() => engine.RecognizeAsync(bitmap).AsTask()).GetAwaiter().GetResult();
        }
        catch (Exception ex) when (ex is not CapabilityException and not OperationCanceledException)
        {
            // Never an empty success: a recogniser that failed read nothing, which is not
            // the same as a screen with nothing written on it.
            throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"the OCR engine for '{languageTag}' failed: {ex.GetType().Name} 0x{ex.HResult:X8}", retryable: true);
        }

        var lines = new List<OcrLine>();
        foreach (var line in recognised.Lines)
        {
            var words = line.Words
                .Select(w => new OcrWord(
                    w.Text,
                    (int)Math.Floor(w.BoundingRect.X),
                    (int)Math.Floor(w.BoundingRect.Y),
                    (int)Math.Ceiling(w.BoundingRect.Width),
                    (int)Math.Ceiling(w.BoundingRect.Height)))
                .ToList();
            if (OcrLine.FromWords(line.Text, words) is { } shaped)
            {
                lines.Add(shaped);
            }
        }

        return lines;
    }
}
