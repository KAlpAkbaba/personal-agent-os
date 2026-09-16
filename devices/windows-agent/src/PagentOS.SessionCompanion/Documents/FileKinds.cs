using System.Text;
using System.Text.RegularExpressions;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// §2: extension → kind. <c>docx | xlsx | pptx | pdf | md | csv | json | source | txt | unknown</c>;
/// <c>source</c> is the spec's list. A kind decides which extractor answers and whether
/// <c>file.read</c> may return the bytes as text; <c>unknown</c> is readable only when the
/// content itself sniffs as text (<see cref="TextFileReader"/>) and never extractable.
/// </summary>
public static class FileKinds
{
    public const string Docx = "docx";
    public const string Xlsx = "xlsx";
    public const string Pptx = "pptx";
    public const string Pdf = "pdf";
    public const string Md = "md";
    public const string Csv = "csv";
    public const string Json = "json";
    public const string Source = "source";
    public const string Txt = "txt";
    /// <summary>B32 req 139-141: a picture — headers on inspect, OCR lines on extract.</summary>
    public const string Image = "image";
    /// <summary>B32 req 142: a zip archive — its central directory on inspect, never extracted.</summary>
    public const string Archive = "archive";
    public const string Unknown = "unknown";

    /// <summary>The extensions of kind <c>image</c>: what WPF's decoders and Windows OCR both read.</summary>
    public static readonly IReadOnlyList<string> ImageExtensions =
    [
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp",
    ];

    /// <summary>§2: the extensions of kind <c>source</c>, verbatim from the spec.</summary>
    public static readonly IReadOnlyList<string> SourceExtensions =
    [
        ".py", ".ps1", ".cs", ".ts", ".tsx", ".js", ".sh", ".sql", ".yaml", ".yml", ".toml", ".ini", ".xml", ".html", ".css",
    ];

    public static string Of(string extension)
    {
        var e = extension.ToLowerInvariant();
        if (SourceExtensions.Contains(e, StringComparer.Ordinal))
        {
            return Source;
        }

        if (ImageExtensions.Contains(e, StringComparer.Ordinal))
        {
            return Image;
        }

        return e switch
        {
            ".docx" => Docx,
            ".xlsx" => Xlsx,
            ".pptx" => Pptx,
            ".pdf" => Pdf,
            ".md" or ".markdown" => Md,
            ".csv" or ".tsv" => Csv,
            ".json" => Json,
            ".txt" or ".log" or ".text" => Txt,
            ".zip" => Archive,
            _ => Unknown,
        };
    }

    /// <summary>The kinds whose bytes ARE the text: readable by <c>file.read</c>, compared line by line.</summary>
    public static bool IsTextLike(string kind) => kind is Md or Csv or Json or Source or Txt;

    /// <summary>The kinds an extractor turns into referenced blocks (everything but <c>unknown</c> and an <c>archive</c>, which is only ever listed).</summary>
    public static bool IsExtractable(string kind) => kind is not (Unknown or Archive);

    /// <summary>The kinds that need a parser (a package or a PDF), never returned raw.</summary>
    public static bool IsBinaryDocument(string kind) => kind is Docx or Xlsx or Pptx or Pdf;

    /// <summary>The OOXML kinds — zip containers whose central directory <see cref="ContainerGuard"/> bounds before the SDK opens them.</summary>
    public static bool IsPackage(string kind) => kind is Docx or Xlsx or Pptx;

    /// <summary><c>structure.language</c> for a source file, by extension.</summary>
    public static string Language(string extension) => extension.ToLowerInvariant() switch
    {
        ".py" => "python",
        ".ps1" => "powershell",
        ".cs" => "csharp",
        ".ts" => "typescript",
        ".tsx" => "tsx",
        ".js" => "javascript",
        ".sh" => "shell",
        ".sql" => "sql",
        ".yaml" or ".yml" => "yaml",
        ".toml" => "toml",
        ".ini" => "ini",
        ".xml" => "xml",
        ".html" => "html",
        ".css" => "css",
        _ => "text",
    };
}

/// <summary>
/// §2 / ADR-0083 decision 5: the names that carry secrets and are NEVER opened — not read,
/// not hashed, not listed by a search. Matched on the file name alone, case-insensitively,
/// before any I/O; a match is <c>permission_denied</c> with detail <c>secret_bearing_name</c>.
/// </summary>
public static class SecretNames
{
    /// <summary>The spec's list, verbatim (glob on the file name).</summary>
    public static readonly IReadOnlyList<string> Patterns =
    [
        ".env*", "*.pem", "*.key", "*.pfx", "*.p12", "id_rsa*", "id_ed25519*", "*.kdbx", "secrets.json",
    ];

    public const string Detail = "secret_bearing_name";

    private static readonly Regex[] Compiled = [.. Patterns.Select(p => new Regex(TextFold.GlobToRegex(p), RegexOptions.IgnoreCase | RegexOptions.CultureInvariant))];

    public static bool IsSecretBearing(string fileName)
    {
        foreach (var regex in Compiled)
        {
            if (regex.IsMatch(fileName))
            {
                return true;
            }
        }

        return false;
    }
}

/// <summary>
/// The matching fold a search uses (§2: "casefold + diacritics-insensitive"): the Turkish
/// letters the spec names (<c>ş→s ı→i İ→i ğ→g ü→u ö→o ç→c</c>) are mapped explicitly, every
/// other letter is lower-cased in the invariant culture and stripped of its combining marks,
/// so <c>sözleşme</c>, <c>SÖZLEŞME</c> and <c>sozlesme</c> are one word.
/// </summary>
public static class TextFold
{
    public static string Fold(string text)
    {
        var builder = new StringBuilder(text.Length);
        foreach (var ch in text)
        {
            switch (ch)
            {
                case 'ş' or 'Ş':
                    builder.Append('s');
                    break;
                case 'ı' or 'I' or 'İ' or 'i':
                    builder.Append('i');
                    break;
                case 'ğ' or 'Ğ':
                    builder.Append('g');
                    break;
                case 'ü' or 'Ü':
                    builder.Append('u');
                    break;
                case 'ö' or 'Ö':
                    builder.Append('o');
                    break;
                case 'ç' or 'Ç':
                    builder.Append('c');
                    break;
                default:
                    builder.Append(char.ToLowerInvariant(ch));
                    break;
            }
        }

        var decomposed = builder.ToString().Normalize(NormalizationForm.FormD);
        var stripped = new StringBuilder(decomposed.Length);
        foreach (var ch in decomposed)
        {
            if (System.Globalization.CharUnicodeInfo.GetUnicodeCategory(ch) != System.Globalization.UnicodeCategory.NonSpacingMark)
            {
                stripped.Append(ch);
            }
        }

        return stripped.ToString().Normalize(NormalizationForm.FormC);
    }

    /// <summary>A file-name glob (<c>*</c>, <c>?</c>) as an anchored regex; everything else is literal.</summary>
    public static string GlobToRegex(string glob)
    {
        var builder = new StringBuilder("^");
        foreach (var ch in glob)
        {
            builder.Append(ch switch
            {
                '*' => ".*",
                '?' => ".",
                _ => Regex.Escape(ch.ToString()),
            });
        }

        return builder.Append('$').ToString();
    }

    public static bool IsGlob(string pattern) => pattern.Contains('*') || pattern.Contains('?');
}
