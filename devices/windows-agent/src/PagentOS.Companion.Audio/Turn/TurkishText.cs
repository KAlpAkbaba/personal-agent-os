using System.Text;

namespace PagentOS.Companion.Audio.Turn;

/// <summary>
/// Case folding that is right for Turkish regardless of the process culture or ICU mode:
/// dotted <c>İ</c> lowers to <c>i</c>, dotless <c>I</c> lowers to <c>ı</c>. The invariant
/// culture gets both wrong, and depending on <c>tr-TR</c> being loaded would make a VAD
/// decision hinge on globalization settings.
/// </summary>
public static class TurkishText
{
    public static string Fold(string text)
    {
        ArgumentNullException.ThrowIfNull(text);
        var builder = new StringBuilder(text.Length);
        foreach (var ch in text)
        {
            builder.Append(ch switch
            {
                'I' => 'ı',
                'İ' => 'i',
                _ => char.ToLowerInvariant(ch),
            });
        }

        return builder.ToString();
    }

    /// <summary>Whitespace/punctuation tokens after folding; empty for blank input.</summary>
    public static IReadOnlyList<string> Tokens(string? text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return Array.Empty<string>();
        }

        var tokens = new List<string>();
        var current = new StringBuilder();
        foreach (var ch in Fold(text))
        {
            if (char.IsLetterOrDigit(ch) || ch == '\'')
            {
                current.Append(ch);
            }
            else if (current.Length > 0)
            {
                tokens.Add(current.ToString());
                current.Clear();
            }
        }

        if (current.Length > 0)
        {
            tokens.Add(current.ToString());
        }

        return tokens;
    }

    /// <summary>"şeyyy", "eee", "ıııı": any letter run of three or more.</summary>
    public static bool IsElongated(string token)
    {
        var run = 1;
        for (var i = 1; i < token.Length; i++)
        {
            run = token[i] == token[i - 1] ? run + 1 : 1;
            if (run >= 3)
            {
                return true;
            }
        }

        return false;
    }
}
