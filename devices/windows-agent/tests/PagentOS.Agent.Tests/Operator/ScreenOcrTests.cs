using System.Globalization;
using System.Text;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// ADR-0176, owner decision 2026-09-19: named text on the screen is found by the OCR that
/// ships with Windows - free, fast, and the picture never leaves the PC. Everything but the
/// recogniser is pure and tested with a fake one; the recogniser is tested once, on a
/// picture this file draws itself.
/// </summary>
public sealed class ScreenOcrTests
{
    private sealed class FakeEngine(IReadOnlyDictionary<string, IReadOnlyList<OcrLine>> readings) : IScreenOcrEngine
    {
        public List<string> Recognised { get; } = [];

        public bool IsInstalled(string languageTag) => readings.ContainsKey(languageTag);

        public IReadOnlyList<OcrLine> Recognize(RgbImage image, string languageTag)
        {
            Recognised.Add(languageTag);
            return readings[languageTag];
        }
    }

    private static readonly RgbImage Blank = new(1000, 2000, new byte[1000 * 2000 * 3]);

    private static OcrLine Line(string text, int x, int y, int width = 200, int height = 20)
        => OcrLine.FromWords(text, [new OcrWord(text, x, y, width, height)])!;

    /// <summary>A line of <paramref name="words"/> four-letter Turkish words (60 of them are 299 characters) - every letter of which the protocol's encoder writes as six bytes.</summary>
    private static OcrLine Crowded(int y, int words)
    {
        var list = Enumerable.Range(0, words).Select(i => new OcrWord("çığö", i * 15, y, 14, 12)).ToList();
        return OcrLine.FromWords(string.Join(' ', list.Select(w => w.Text)), list)!;
    }

    private static int FrameBytes(JsonObject result) => Encoding.UTF8.GetByteCount(ProtocolJson.Serialize(new CommandAckMessage
    {
        CommandId = "11111111-2222-3333-4444-555555555555",
        Status = AckStatus.Succeeded,
        Result = result,
    }));

    // ------------------------------------------------------------------ the capability's name

    [Fact]
    public void Screen_ocr_is_an_operator_name_appended_last_and_not_an_input()
    {
        Assert.Equal("screen.ocr", OperatorCapabilityNames.ScreenOcr);
        Assert.Equal(OperatorCapabilityNames.ScreenOcr, OperatorCapabilityNames.All[^1]);
        Assert.Single(OperatorCapabilityNames.All, n => n == OperatorCapabilityNames.ScreenOcr);
        Assert.True(OperatorCapabilityNames.IsMember("screen.ocr"));
        Assert.True(AgentCapabilities.IsInteractive("screen.ocr"));
        Assert.False(OperatorCapabilityNames.IsGuarded("screen.ocr"));
        Assert.Equal(OperatorCapabilityNames.IsGuarded("screen.capture"), OperatorCapabilityNames.IsGuarded("screen.ocr"));
        Assert.Contains("screen.ocr", AgentCapabilities.Compose(browserEnabled: false, displayPowerEnabled: false, operatorEnabled: true));
        Assert.DoesNotContain("screen.ocr", AgentCapabilities.Compose(browserEnabled: true, displayPowerEnabled: true, operatorEnabled: false));
    }

    // ------------------------------------------------------------------ languages

    [Fact]
    public void Without_languages_turkish_then_english_are_tried()
    {
        Assert.Equal(["tr", "en-US"], ScreenOcr.ParseLanguages(new JsonObject()));
        Assert.Equal(["en-US", "de"], ScreenOcr.ParseLanguages(new JsonObject { ["languages"] = new JsonArray("en-US", "de", "EN-us") }));
    }

    [Fact]
    public void Languages_that_are_not_a_list_of_tags_are_refused()
    {
        foreach (var bad in new JsonNode[] { "tr", new JsonArray(), new JsonArray(1), new JsonArray(""), new JsonArray(new string('x', 36)), new JsonArray([.. Enumerable.Range(0, 9).Select(i => (JsonNode)$"l{i}")]) })
        {
            var error = Assert.Throws<CapabilityException>(() => ScreenOcr.ParseLanguages(new JsonObject { ["languages"] = bad }));
            Assert.Equal(ErrorClasses.ValidationError, error.ErrorClass);
        }
    }

    [Fact]
    public void A_language_that_is_not_installed_is_skipped_and_the_result_names_the_ones_used()
    {
        var engine = new FakeEngine(new Dictionary<string, IReadOnlyList<OcrLine>> { ["en-US"] = [Line("hello", 10, 10)] });

        var result = ScreenOcr.Run(Blank, ["tr", "en-US"], engine, "w-1", new JsonObject { ["state"] = "normal" });

        Assert.Equal(["en-US"], engine.Recognised);
        Assert.Equal(["en-US"], result["languages"]!.AsArray().Select(l => l!.GetValue<string>()).ToArray());
        Assert.Equal("hello", result["lines"]![0]!["text"]!.GetValue<string>());
    }

    [Fact]
    public void No_installed_language_is_dependency_unavailable_and_nothing_is_recognised()
    {
        var engine = new FakeEngine(new Dictionary<string, IReadOnlyList<OcrLine>>());

        var error = Assert.Throws<CapabilityException>(() => ScreenOcr.Run(Blank, ["tr", "en-US"], engine, null, null));

        Assert.Equal(ErrorClasses.DependencyUnavailable, error.ErrorClass);
        Assert.Contains("no OCR language installed", error.Message, StringComparison.Ordinal);
        Assert.Empty(engine.Recognised);
    }

    // ------------------------------------------------------------------ merge

    [Fact]
    public void A_later_language_adds_only_the_lines_no_kept_line_already_covers()
    {
        var turkish = new[] { Line("Üç Kağıtçı", 0, 0), Line("Abone ol", 0, 100) };
        var english = new[]
        {
            Line("Uc Kagitci", 2, 1),          // the same place: covered almost entirely -> dropped
            Line("Subscribe", 0, 50),          // nowhere near a kept line -> added
            Line("most", 80, 100, 200, 20),    // 60 % inside "Abone ol" -> dropped (and first, so only "Abone ol" can be what drops it)
            Line("half", 100, 100, 200, 20),   // exactly half inside "Abone ol": not MORE than half -> added
        };

        var merged = ScreenOcr.Merge([turkish, english]);

        Assert.Equal(["Üç Kağıtçı", "Subscribe", "Abone ol", "half"], merged.Select(l => l.Text).ToArray());
    }

    [Fact]
    public void Both_readings_reach_the_result_through_run_in_reading_order()
    {
        var engine = new FakeEngine(new Dictionary<string, IReadOnlyList<OcrLine>>
        {
            ["tr"] = [Line("alt", 0, 500), Line("üst", 0, 10)],
            ["en-US"] = [Line("middle", 0, 250), Line("ust", 1, 11)],
        });

        var result = ScreenOcr.Run(Blank, ["tr", "en-US"], engine, null, null);

        Assert.Equal(["tr", "en-US"], engine.Recognised);
        Assert.Equal(["üst", "middle", "alt"], result["lines"]!.AsArray().Select(l => l!["text"]!.GetValue<string>()).ToArray());
        Assert.Equal(3, result["line_count"]!.GetValue<int>());
    }

    [Fact]
    public void A_lines_box_is_the_union_of_its_words()
    {
        var line = OcrLine.FromWords("iki kelime", [new OcrWord("iki", 40, 12, 30, 18), new OcrWord("kelime", 80, 10, 70, 22)])!;

        Assert.Equal((40, 10, 110, 22), (line.X, line.Y, line.Width, line.Height));
        Assert.Null(OcrLine.FromWords("", []));
    }

    // ------------------------------------------------------------------ shape, bounds, fit

    [Fact]
    public void The_result_has_exactly_the_contracts_shape_and_no_picture()
    {
        var line = OcrLine.FromWords("Üç Kağıtçı", [new OcrWord("Üç", 10, 20, 30, 40), new OcrWord("Kağıtçı", 50, 22, 90, 38)])!;

        var result = ScreenOcr.BuildResult(2576, 1416, ["tr", "en-US"], [line], "w-7", new JsonObject { ["state"] = "maximized" });

        Assert.Equal(
            ["width", "height", "scale", "languages", "lines", "line_count", "truncated", "window_id", "observed"],
            result.Select(p => p.Key).ToArray());
        Assert.Equal(2576, result["width"]!.GetValue<int>());
        Assert.Equal(1416, result["height"]!.GetValue<int>());
        Assert.Equal(1, result["scale"]!.GetValue<int>());
        Assert.False(result["truncated"]!.GetValue<bool>());
        Assert.Equal("w-7", result["window_id"]!.GetValue<string>());
        Assert.Equal("maximized", result["observed"]!["window"]!["state"]!.GetValue<string>());

        var shaped = result["lines"]![0]!.AsObject();
        Assert.Equal(["text", "x", "y", "width", "height", "words"], shaped.Select(p => p.Key).ToArray());
        Assert.Equal("Üç Kağıtçı", shaped["text"]!.GetValue<string>());
        Assert.Equal((10, 20, 130, 40), (shaped["x"]!.GetValue<int>(), shaped["y"]!.GetValue<int>(), shaped["width"]!.GetValue<int>(), shaped["height"]!.GetValue<int>()));
        var word = shaped["words"]![1]!.AsObject();
        Assert.Equal(["text", "x", "y", "width", "height"], word.Select(p => p.Key).ToArray());
        Assert.Equal("Kağıtçı", word["text"]!.GetValue<string>());
        Assert.Equal(50, word["x"]!.GetValue<int>());
    }

    [Fact]
    public void Lines_characters_and_words_are_bounded_and_the_result_says_truncated()
    {
        var many = Enumerable.Range(0, OperatorCapabilityNames.MaxOcrLines + 100).Select(i => Line($"l{i}", 0, i * 3, 50, 2)).ToList();
        var result = ScreenOcr.BuildResult(100, 100, ["tr"], many, null, null);
        Assert.Equal(OperatorCapabilityNames.MaxOcrLines, result["line_count"]!.GetValue<int>());
        Assert.Equal(OperatorCapabilityNames.MaxOcrLines, result["lines"]!.AsArray().Count);
        Assert.Equal("l0", result["lines"]![0]!["text"]!.GetValue<string>());
        Assert.True(result["truncated"]!.GetValue<bool>());

        var longText = ScreenOcr.BuildResult(100, 100, ["tr"], [Line(new string('a', OperatorCapabilityNames.MaxOcrTextChars + 1), 0, 0)], null, null);
        Assert.Equal(OperatorCapabilityNames.MaxOcrTextChars, longText["lines"]![0]!["text"]!.GetValue<string>().Length);
        Assert.Equal(OperatorCapabilityNames.MaxOcrTextChars, longText["lines"]![0]!["words"]![0]!["text"]!.GetValue<string>().Length);
        Assert.True(longText["truncated"]!.GetValue<bool>());

        var wordy = ScreenOcr.BuildResult(100, 100, ["tr"], [Crowded(0, OperatorCapabilityNames.MaxOcrWordsPerLine + 1)], null, null);
        Assert.Equal(OperatorCapabilityNames.MaxOcrWordsPerLine, wordy["lines"]![0]!["words"]!.AsArray().Count);
        Assert.True(wordy["truncated"]!.GetValue<bool>());

        var atTheBounds = ScreenOcr.BuildResult(100, 100, ["tr"], [Crowded(0, OperatorCapabilityNames.MaxOcrWordsPerLine), Line(new string('a', OperatorCapabilityNames.MaxOcrTextChars), 0, 40)], null, null);
        Assert.False(atTheBounds["truncated"]!.GetValue<bool>());
    }

    [Fact]
    public void A_page_with_too_much_text_loses_words_from_the_bottom_up_and_still_fits_the_frame()
    {
        // 250 crowded lines: too much WITH every word's box, comfortable without the lower ones'.
        var lines = Enumerable.Range(0, 250).Select(i => Crowded(i * 14, 60)).ToList();
        var untruncatedBytes = lines.Count * CaptureFit.SerializedBytes(ScreenOcr.BuildResult(1, 1, ["tr"], [lines[0]], null, null));
        Assert.True(untruncatedBytes > CaptureFit.MaxResultBytes, $"the input is only ~{untruncatedBytes} bytes; make it larger");

        var result = ScreenOcr.BuildResult(2576, 3500, ["tr"], lines, "w-1", null);

        Assert.True(CaptureFit.SerializedBytes(result) <= CaptureFit.MaxResultBytes);
        Assert.True(FrameBytes(result) <= ProtocolConstants.MaxFrameBytes);
        Assert.True(result["truncated"]!.GetValue<bool>());
        var shaped = result["lines"]!.AsArray();
        Assert.Equal(250, shaped.Count);                    // no line was dropped: the words were enough
        Assert.Equal(250, result["line_count"]!.GetValue<int>());
        var wordCounts = shaped.Select(l => l!["words"]!.AsArray().Count).ToArray();
        var firstBare = Array.IndexOf(wordCounts, 0);
        Assert.InRange(firstBare, 1, 249);                   // the top keeps its words ...
        Assert.All(wordCounts.Take(firstBare), c => Assert.Equal(60, c));
        Assert.All(wordCounts.Skip(firstBare), c => Assert.Equal(0, c)); // ... the bottom gave them up
        Assert.Equal(lines[249].Text, shaped[249]!["text"]!.GetValue<string>());

        // Not one word more was given up than had to be: the first bare line's words would not have fit.
        var restored = CaptureFit.SerializedBytes(result)
                       + CaptureFit.SerializedBytes(ScreenOcr.BuildResult(1, 1, ["tr"], [lines[firstBare]], null, null))
                       - CaptureFit.SerializedBytes(ScreenOcr.BuildResult(1, 1, ["tr"], [lines[firstBare] with { Words = [] }], null, null));
        Assert.True(restored > CaptureFit.MaxResultBytes - 16, $"line {firstBare} lost its words with {CaptureFit.MaxResultBytes - restored} bytes to spare");
    }

    [Fact]
    public void When_the_words_are_not_enough_the_trailing_lines_go_and_it_still_fits()
    {
        // 600 lines of 300 Turkish characters: the TEXT alone is over a frame.
        var text = string.Concat(Enumerable.Repeat("çığöşü", 50));
        var lines = Enumerable.Range(0, OperatorCapabilityNames.MaxOcrLines).Select(i => Line(text, 0, i * 5, 900, 4)).ToList();

        var result = ScreenOcr.BuildResult(1000, 3000, ["tr"], lines, null, null);

        Assert.True(FrameBytes(result) <= ProtocolConstants.MaxFrameBytes);
        Assert.True(result["truncated"]!.GetValue<bool>());
        var shaped = result["lines"]!.AsArray();
        Assert.InRange(shaped.Count, 100, OperatorCapabilityNames.MaxOcrLines - 1);
        Assert.Equal(shaped.Count, result["line_count"]!.GetValue<int>());
        Assert.All(shaped, l => Assert.Empty(l!["words"]!.AsArray())); // words first, everywhere, before any line went
        Assert.Equal(0, shaped[0]!["y"]!.GetValue<int>());             // the TOP of the page is what is kept
        Assert.Equal((shaped.Count - 1) * 5, shaped[^1]!["y"]!.GetValue<int>());

        // And not one line more than had to go.
        var oneMore = CaptureFit.SerializedBytes(result) + CaptureFit.SerializedBytes(ScreenOcr.BuildResult(1, 1, ["tr"], [lines[0] with { Words = [] }], null, null)) - CaptureFit.SerializedBytes(ScreenOcr.BuildResult(1, 1, ["tr"], [], null, null));
        Assert.True(oneMore > CaptureFit.MaxResultBytes - 16, "another line would have fit");
    }

    // ------------------------------------------------------------------ the real recogniser

    [Fact]
    public void A_picture_larger_than_the_recogniser_accepts_is_a_typed_refusal()
    {
        var wide = new RgbImage(10_001, 1, new byte[10_001 * 3]);
        var error = Assert.Throws<CapabilityException>(() => new WindowsOcrEngine().Recognize(wide, "tr"));
        Assert.Equal(ErrorClasses.ValidationError, error.ErrorClass);
    }

    [Fact]
    public void A_malformed_language_tag_is_simply_not_installed()
    {
        Assert.False(new WindowsOcrEngine().IsInstalled("not a language tag !!"));
        Assert.False(new WindowsOcrEngine().IsInstalled("tlh")); // well-formed, and nobody ships Klingon OCR
    }

    [TurkishOcrFact]
    public void Drawn_turkish_text_is_read_where_it_was_drawn()
    {
        const int width = 1400;
        const int height = 500;
        var (image, drawn) = Draw(width, height, "Üç Kağıtçı", new Point(180, 140), 72, ("Subscribe to the channel", new Point(180, 330), 40));

        var result = ScreenOcr.Run(image, ["tr"], new WindowsOcrEngine(), null, null);

        Assert.Equal(["tr"], result["languages"]!.AsArray().Select(l => l!.GetValue<string>()).ToArray());
        Assert.Equal(width, result["width"]!.GetValue<int>());
        Assert.Equal(height, result["height"]!.GetValue<int>());
        var lines = result["lines"]!.AsArray();
        var read = string.Join(" | ", lines.Select(l => l!["text"]!.GetValue<string>()));
        var line = lines.FirstOrDefault(l => l!["text"]!.GetValue<string>().Contains("Üç Kağıtçı", StringComparison.Ordinal));
        Assert.True(line is not null, $"the recogniser read: {read}");

        var (x, y, w, h) = (line!["x"]!.GetValue<int>(), line["y"]!.GetValue<int>(), line["width"]!.GetValue<int>(), line["height"]!.GetValue<int>());
        const int slack = 12;
        Assert.True(x >= drawn.Left - slack && y >= drawn.Top - slack && x + w <= drawn.Right + slack && y + h <= drawn.Bottom + slack, $"the box {x},{y} {w}x{h} is not inside the drawn area {drawn}");
        Assert.True(w > drawn.Width / 2 && h > drawn.Height / 4, $"the box {w}x{h} is not the text's ({drawn.Width}x{drawn.Height})");

        var words = line["words"]!.AsArray();
        Assert.Equal(["Üç", "Kağıtçı"], words.Select(v => v!["text"]!.GetValue<string>()).ToArray());
        Assert.True(words[0]!["x"]!.GetValue<int>() < words[1]!["x"]!.GetValue<int>());
    }

    /// <summary>Black text on white through WPF's managed text path, on an STA thread; pixels only, nothing on disk.</summary>
    private static (RgbImage Image, Rect Drawn) Draw(int width, int height, string text, Point at, double size, (string Text, Point At, double Size) second)
    {
        RgbImage? image = null;
        var drawn = Rect.Empty;
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            try
            {
                var typeface = new Typeface("Segoe UI");
                var visual = new DrawingVisual();
                using (var context = visual.RenderOpen())
                {
                    context.DrawRectangle(Brushes.White, null, new Rect(0, 0, width, height));
                    var formatted = new FormattedText(text, CultureInfo.GetCultureInfo("tr-TR"), FlowDirection.LeftToRight, typeface, size, Brushes.Black, 1.0);
                    context.DrawText(formatted, at);
                    drawn = new Rect(at.X, at.Y, formatted.Width, formatted.Height);
                    context.DrawText(new FormattedText(second.Text, CultureInfo.GetCultureInfo("en-US"), FlowDirection.LeftToRight, typeface, second.Size, Brushes.Black, 1.0), second.At);
                }

                var target = new RenderTargetBitmap(width, height, 96, 96, PixelFormats.Pbgra32);
                target.Render(visual);
                var bgra = new byte[width * height * 4];
                target.CopyPixels(bgra, width * 4, 0);
                var rgb = new byte[width * height * 3];
                for (int src = 0, dst = 0; src < bgra.Length; src += 4, dst += 3)
                {
                    rgb[dst] = bgra[src + 2];
                    rgb[dst + 1] = bgra[src + 1];
                    rgb[dst + 2] = bgra[src];
                }

                image = new RgbImage(width, height, rgb);
            }
            catch (Exception ex)
            {
                failure = ex;
            }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        thread.Join();
        if (failure is not null)
        {
            throw new InvalidOperationException("the test picture could not be drawn", failure);
        }

        return (image!, drawn);
    }
}

/// <summary>Runs only where Windows has the Turkish recogniser installed; says why when it does not.</summary>
public sealed class TurkishOcrFactAttribute : FactAttribute
{
    public TurkishOcrFactAttribute()
    {
        if (!OperatingSystem.IsWindowsVersionAtLeast(10, 0, 19041))
        {
            Skip = "Windows.Media.Ocr needs Windows 10";
        }
        else if (!new WindowsOcrEngine().IsInstalled("tr"))
        {
            Skip = "no Turkish OCR language is installed on this runner (Settings > Language > Türkçe > optical character recognition)";
        }
    }
}
