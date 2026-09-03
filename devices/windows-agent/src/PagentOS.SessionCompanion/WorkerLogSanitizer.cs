using System.Text.RegularExpressions;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion;

/// <summary>
/// What the companion is willing to copy from the worker's stderr (and its <c>log</c>
/// messages) into its own log. The worker's log is free text the companion did not write,
/// and the companion log is read by the owner and shipped in diagnostics, so three rules
/// apply before a line is repeated:
/// <list type="number">
/// <item>URLs lose their query string and fragment — <c>scheme://host/path</c> survives,
/// <c>?q=…&amp;token=…</c> does not (BROWSER_CAPABILITIES.md §5: never a URL query string
/// with credentials).</item>
/// <item>A line in which a credential-like word (the §6 forbidden-key rule, same
/// normalisation as the result scan) is immediately followed by <c>:</c> or <c>=</c> is
/// replaced by a one-line marker: the value after that separator is exactly what must not
/// be logged, and the marker keeps the fact that something was said.</item>
/// <item>The line is capped at <see cref="MaxLineChars"/> (the error-message limit), so a
/// worker cannot flood the log with one write.</item>
/// </list>
/// </summary>
public static class WorkerLogSanitizer
{
    /// <summary>Same ceiling as <c>error.message</c>; the suffix that marks a cut is added on top.</summary>
    public const int MaxLineChars = ProtocolConstants.MaxErrorMessageLength;

    /// <summary>What replaces a line that named a credential-like key with a value.</summary>
    public const string RedactedMarker = "<redacted: worker log line named a credential-like key with a value>";

    // scheme://authority/path up to the first '?' or '#', then everything that follows
    // until whitespace. The authority is kept whole (userinfo is not a §5 concern here).
    private static readonly Regex UrlWithQueryOrFragment = new(
        @"\b([a-z][a-z0-9+.\-]*://[^\s?#]*)[?#]\S*",
        RegexOptions.Compiled | RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    // A word (letters, digits, '_' and '-', so api_key / x-api-key / Set-Cookie are one
    // word) directly followed by ':' or '=' (optional whitespace between). The word itself
    // is judged by the shared forbidden-key rule, not by a second list.
    private static readonly Regex KeyBeforeSeparator = new(
        @"([A-Za-z0-9_\-]+)\s*[:=]",
        RegexOptions.Compiled | RegexOptions.CultureInvariant);

    public static string Sanitize(string line)
    {
        if (string.IsNullOrEmpty(line))
        {
            return line;
        }

        var stripped = UrlWithQueryOrFragment.Replace(line, "$1");

        foreach (Match match in KeyBeforeSeparator.Matches(stripped))
        {
            if (BrowserCapabilities.IsForbiddenKey(match.Groups[1].Value))
            {
                return RedactedMarker;
            }
        }

        if (stripped.Length > MaxLineChars)
        {
            return string.Concat(stripped.AsSpan(0, MaxLineChars), " …[+", (stripped.Length - MaxLineChars).ToString(System.Globalization.CultureInfo.InvariantCulture), " chars cut]");
        }

        return stripped;
    }
}
