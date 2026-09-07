using System.Text;
using System.Text.RegularExpressions;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>What a byte sequence decoded to, under which encoding, and whether it looks like text at all.</summary>
public sealed record DecodedText(string Text, string Encoding, bool IsText);

/// <summary>
/// §2 <c>file.read</c>: the bytes of a text-like file as characters. Encoding is detected —
/// a UTF-8 / UTF-16 LE / UTF-16 BE byte-order mark first, then strict UTF-8 (invalid
/// sequences reject it), then a UTF-16 LE heuristic (ASCII text with a NUL after every
/// byte), then Latin-1 as the fallback that always decodes. Whether the result IS text is a
/// separate verdict: a NUL, or more than 1 % control characters outside tab/newline, says
/// no — and a "no" is <c>unsupported_format</c>, never a page of mojibake.
/// </summary>
public static class TextFileReader
{
    public const string Utf8 = "utf-8";
    public const string Utf16Le = "utf-16le";
    public const string Utf16Be = "utf-16be";
    public const string Latin1 = "iso-8859-1";

    private static readonly UTF8Encoding StrictUtf8 = new(encoderShouldEmitUTF8Identifier: false, throwOnInvalidBytes: true);

    public static DecodedText Decode(ReadOnlySpan<byte> bytes)
    {
        if (bytes.Length >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF)
        {
            var text = Encoding.UTF8.GetString(bytes[3..]);
            return new DecodedText(text, Utf8, LooksLikeText(text));
        }

        if (bytes.Length >= 2 && bytes[0] == 0xFF && bytes[1] == 0xFE)
        {
            var text = Encoding.Unicode.GetString(bytes[2..]);
            return new DecodedText(text, Utf16Le, LooksLikeText(text));
        }

        if (bytes.Length >= 2 && bytes[0] == 0xFE && bytes[1] == 0xFF)
        {
            var text = Encoding.BigEndianUnicode.GetString(bytes[2..]);
            return new DecodedText(text, Utf16Be, LooksLikeText(text));
        }

        try
        {
            var text = StrictUtf8.GetString(bytes);
            return new DecodedText(text, Utf8, LooksLikeText(text));
        }
        catch (DecoderFallbackException)
        {
            // Not UTF-8; fall through.
        }

        if (LooksLikeUtf16Le(bytes))
        {
            var text = Encoding.Unicode.GetString(bytes);
            return new DecodedText(text, Utf16Le, LooksLikeText(text));
        }

        var latin = Encoding.Latin1.GetString(bytes);
        return new DecodedText(latin, Latin1, LooksLikeText(latin));
    }

    /// <summary>Read and decode a whole file (the caller has already applied the size bound).</summary>
    public static DecodedText Read(string path) => Decode(File.ReadAllBytes(path));

    /// <summary>The spec's whitespace normalisation (<c>\s+</c> → one space, trimmed): how a block's text is compared and how a PDF page's text is reported.</summary>
    public static string Normalise(string text) => WhitespaceRun.Replace(text, " ").Trim();

    private static readonly Regex WhitespaceRun = new(@"\s+", RegexOptions.Compiled | RegexOptions.CultureInvariant);

    /// <summary>
    /// Python's <c>str.splitlines()</c> for the three common line ends: no trailing empty
    /// element for a text that ends with a newline, so a file of eighteen lines has eighteen.
    /// </summary>
    public static IReadOnlyList<string> SplitLines(string text)
    {
        var lines = new List<string>();
        var start = 0;
        for (var i = 0; i < text.Length; i++)
        {
            var ch = text[i];
            if (ch == '\n' || ch == '\r')
            {
                lines.Add(text[start..i]);
                if (ch == '\r' && i + 1 < text.Length && text[i + 1] == '\n')
                {
                    i++;
                }

                start = i + 1;
            }
        }

        if (start < text.Length)
        {
            lines.Add(text[start..]);
        }

        return lines;
    }

    private static bool LooksLikeUtf16Le(ReadOnlySpan<byte> bytes)
    {
        if (bytes.Length < 4 || bytes.Length % 2 != 0)
        {
            return false;
        }

        var sample = Math.Min(bytes.Length, 4096);
        var zerosAtOdd = 0;
        var zerosAtEven = 0;
        for (var i = 0; i + 1 < sample; i += 2)
        {
            if (bytes[i + 1] == 0)
            {
                zerosAtOdd++;
            }

            if (bytes[i] == 0)
            {
                zerosAtEven++;
            }
        }

        var pairs = sample / 2;
        return zerosAtOdd > pairs * 3 / 4 && zerosAtEven < pairs / 8;
    }

    private static bool LooksLikeText(string text)
    {
        if (text.Length == 0)
        {
            return true;
        }

        var controls = 0;
        foreach (var ch in text)
        {
            if (ch == '\0')
            {
                return false;
            }

            if (ch < 0x20 && ch is not ('\t' or '\n' or '\r' or '\f' or '\v'))
            {
                controls++;
            }
            else if (ch == '�')
            {
                controls++;
            }
        }

        return controls * 100 <= text.Length;
    }
}
