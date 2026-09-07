using System.Text;
using System.Text.RegularExpressions;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// What a byte sequence decoded to, under which encoding, and whether it looks like text at
/// all. <see cref="Truncated"/> says the file went on past the prefix that was read;
/// <see cref="TotalBytes"/> is the file's length on the handle the bytes came from.
/// </summary>
public sealed record DecodedText(string Text, string Encoding, bool IsText, bool Truncated = false, long TotalBytes = 0);

/// <summary>
/// §2 <c>file.read</c>: the bytes of a text-like file as characters. Encoding is detected —
/// a UTF-8 / UTF-16 LE / UTF-16 BE byte-order mark first, then strict UTF-8 (invalid
/// sequences reject it), then a UTF-16 LE heuristic (ASCII text with a NUL after every
/// byte), then Latin-1 as the fallback that always decodes. Whether the result IS text is a
/// separate verdict: a NUL, or more than 1 % control characters outside tab/newline, says
/// no — and a "no" is <c>unsupported_format</c>, never a page of mojibake.
/// The file is read through ONE handle: its length is checked on that handle (not on the
/// directory entry the record was built from), at most a bounded prefix is read
/// (<see cref="DocumentBounds.MaxTextPrefixBytes"/> unless the caller says otherwise), and a
/// prefix that cut a multi-byte sequence is trimmed to the last whole character before it
/// is decoded, so a cut never turns UTF-8 into Latin-1 (ADR-0083 addendum 3).
/// </summary>
public static class TextFileReader
{
    public const string Utf8 = "utf-8";
    public const string Utf16Le = "utf-16le";
    public const string Utf16Be = "utf-16be";
    public const string Latin1 = "iso-8859-1";

    private static readonly UTF8Encoding StrictUtf8 = new(encoderShouldEmitUTF8Identifier: false, throwOnInvalidBytes: true);

    public static DecodedText Decode(ReadOnlySpan<byte> bytes) => Decode(bytes, truncated: false, totalBytes: bytes.Length);

    /// <summary>Decode a prefix; when <paramref name="truncated"/>, the cut is moved back to a character boundary first.</summary>
    public static DecodedText Decode(ReadOnlySpan<byte> bytes, bool truncated, long totalBytes)
    {
        if (bytes.Length >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF)
        {
            var body = truncated ? TrimIncompleteUtf8(bytes[3..]) : bytes[3..];
            var text = Encoding.UTF8.GetString(body);
            return new DecodedText(text, Utf8, LooksLikeText(text), truncated, totalBytes);
        }

        if (bytes.Length >= 2 && bytes[0] == 0xFF && bytes[1] == 0xFE)
        {
            var body = truncated ? TrimIncompleteUtf16(bytes[2..], bigEndian: false) : bytes[2..];
            var text = Encoding.Unicode.GetString(body);
            return new DecodedText(text, Utf16Le, LooksLikeText(text), truncated, totalBytes);
        }

        if (bytes.Length >= 2 && bytes[0] == 0xFE && bytes[1] == 0xFF)
        {
            var body = truncated ? TrimIncompleteUtf16(bytes[2..], bigEndian: true) : bytes[2..];
            var text = Encoding.BigEndianUnicode.GetString(body);
            return new DecodedText(text, Utf16Be, LooksLikeText(text), truncated, totalBytes);
        }

        if (truncated && LooksLikeUtf16Le(bytes))
        {
            var text = Encoding.Unicode.GetString(TrimIncompleteUtf16(bytes, bigEndian: false));
            return new DecodedText(text, Utf16Le, LooksLikeText(text), truncated, totalBytes);
        }

        var candidate = truncated ? TrimIncompleteUtf8(bytes) : bytes;
        try
        {
            var text = StrictUtf8.GetString(candidate);
            return new DecodedText(text, Utf8, LooksLikeText(text), truncated, totalBytes);
        }
        catch (DecoderFallbackException)
        {
            // Not UTF-8; fall through.
        }

        if (LooksLikeUtf16Le(bytes))
        {
            var text = Encoding.Unicode.GetString(bytes);
            return new DecodedText(text, Utf16Le, LooksLikeText(text), truncated, totalBytes);
        }

        var latin = Encoding.Latin1.GetString(bytes);
        return new DecodedText(latin, Latin1, LooksLikeText(latin), truncated, totalBytes);
    }

    /// <summary>
    /// Read and decode at most <paramref name="maxBytes"/> of a file through one handle. The
    /// handle's length is the size that counts: over
    /// <see cref="DocumentCapabilityNames.MaxFileBytes"/> is <c>unsupported_format</c> /
    /// <c>too_large</c> even if the directory entry said less a moment ago.
    /// </summary>
    public static DecodedText Read(string path, int maxBytes = DocumentBounds.MaxTextPrefixBytes)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 1 << 16, FileOptions.SequentialScan);
        var length = stream.Length;
        if (length > DocumentCapabilityNames.MaxFileBytes)
        {
            throw DocumentErrors.Unsupported(
                $"'{Path.GetFileName(path)}' is {length} bytes on open, over the {DocumentBounds.Mebibytes(DocumentCapabilityNames.MaxFileBytes)} bound; it was not read",
                DocumentErrors.TooLarge);
        }

        var want = (int)Math.Min(length, maxBytes);
        var buffer = new byte[want];
        var read = stream.ReadAtLeast(buffer, want, throwOnEndOfStream: false);
        var total = Math.Max(stream.Length, read);
        return Decode(buffer.AsSpan(0, read), truncated: total > read, totalBytes: total);
    }

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

    /// <summary>Drop a trailing UTF-8 sequence the cut left incomplete (at most three bytes).</summary>
    public static ReadOnlySpan<byte> TrimIncompleteUtf8(ReadOnlySpan<byte> bytes)
    {
        var limit = Math.Max(0, bytes.Length - 4);
        for (var i = bytes.Length - 1; i >= limit; i--)
        {
            var b = bytes[i];
            if ((b & 0xC0) == 0x80)
            {
                continue; // a continuation byte: keep looking for its lead
            }

            var expected = b < 0x80 ? 1 : (b & 0xE0) == 0xC0 ? 2 : (b & 0xF0) == 0xE0 ? 3 : (b & 0xF8) == 0xF0 ? 4 : 1;
            return bytes.Length - i < expected ? bytes[..i] : bytes;
        }

        return bytes;
    }

    /// <summary>Make the cut fall on a code unit, and drop a trailing high surrogate whose pair is beyond it.</summary>
    public static ReadOnlySpan<byte> TrimIncompleteUtf16(ReadOnlySpan<byte> bytes, bool bigEndian)
    {
        if (bytes.Length % 2 != 0)
        {
            bytes = bytes[..^1];
        }

        if (bytes.Length >= 2)
        {
            var high = bigEndian ? bytes[^2] : bytes[^1];
            if (high is >= 0xD8 and <= 0xDB)
            {
                bytes = bytes[..^2];
            }
        }

        return bytes;
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
