using System.Diagnostics;
using System.Text;
using System.Text.Json.Nodes;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// B32 requirements 140/496: the LOCAL OCR engine — <c>Windows.Media.Ocr</c>, the one every
/// Windows 10/11 carries with the owner's installed language packs — hosted in a headless
/// PowerShell child rather than called in-process.
/// </summary>
/// <remarks>
/// <para>
/// Why a child: the companion targets <c>net10.0-windows</c> without a Windows SDK version,
/// so the WinRT projections (<c>Windows.Media.Ocr</c>, <c>Windows.Graphics.Imaging</c>) are
/// not compiled into it. Bumping the target framework touches every project, the installer
/// and the staged-update qualification; the engine itself is reachable from PowerShell 5.1
/// on the same machine in ~15 ms per image (measured 2026-09-14, Turkish, exact text back).
/// The script below is the whole contract with the engine; it is embedded here (never a file
/// on disk the owner could edit) and sent as <c>-EncodedCommand</c>. The image path and the
/// language go through the environment, never through the command line.
/// </para>
/// <para>
/// Honesty: no engine, or no language pack, is a typed refusal
/// (<c>dependency_unavailable</c> with the languages that ARE installed), never an empty
/// success. The text the engine returns is the text; nothing is corrected here.
/// </para>
/// </remarks>
public sealed class OcrHost
{
    public const string EngineName = "windows.media.ocr";

    /// <summary>The language asked for when the payload names none: the owner's Turkish, then whatever is installed.</summary>
    public const string PreferredLanguage = "tr";

    public static readonly TimeSpan Timeout = TimeSpan.FromSeconds(25);

    private const string Script = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
  [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
  [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
  [Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime] | Out-Null
  $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
  function Await($op, $type) { $t = $asTaskGeneric.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
  $languages = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag })
  $image = $env:PAGENTOS_OCR_IMAGE
  if ($env:PAGENTOS_OCR_PROBE -eq '1') {
    @{ engine_available = ($languages.Count -gt 0); languages = $languages } | ConvertTo-Json -Compress -Depth 4
    exit 0
  }
  $wanted = $env:PAGENTOS_OCR_LANGUAGE
  $engine = $null
  $chosen = $null
  foreach ($candidate in @($wanted, 'tr') + $languages) {
    if (-not $candidate) { continue }
    $lang = New-Object Windows.Globalization.Language $candidate
    if ([Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($lang)) {
      $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
      if ($engine) { $chosen = $engine.RecognizerLanguage.LanguageTag; break }
    }
  }
  if (-not $engine) {
    @{ engine_available = $false; languages = $languages } | ConvertTo-Json -Compress -Depth 4
    exit 0
  }
  $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($image)) ([Windows.Storage.StorageFile])
  $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
  $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
  $lines = @()
  foreach ($line in $result.Lines) {
    $words = @()
    foreach ($w in $line.Words) { $words += @{ text = $w.Text; x = [int]$w.BoundingRect.X; y = [int]$w.BoundingRect.Y; w = [int]$w.BoundingRect.Width; h = [int]$w.BoundingRect.Height } }
    $lines += @{ text = $line.Text; words = $words }
  }
  $angle = $null
  if ($result.TextAngle -ne $null) { $angle = [double]$result.TextAngle }
  @{ engine_available = $true; language = $chosen; languages = $languages; width = [int]$bitmap.PixelWidth; height = [int]$bitmap.PixelHeight; text = $result.Text; lines = $lines; text_angle = $angle } | ConvertTo-Json -Compress -Depth 6
} catch {
  @{ error = $_.Exception.Message } | ConvertTo-Json -Compress
  exit 0
}
""";

    private readonly string _powershellPath;

    public OcrHost(string? powershellPath = null)
    {
        _powershellPath = powershellPath ?? Operator.TerminalRunner.DefaultPowerShellPath();
    }

    /// <summary>The OCR languages installed on this machine (empty: no engine here).</summary>
    public IReadOnlyList<string> AvailableLanguages(CancellationToken cancellationToken)
    {
        var probe = Run(null, null, probe: true, cancellationToken);
        return [.. (probe["languages"] as JsonArray ?? []).Select(n => n!.GetValue<string>())];
    }

    /// <summary>
    /// Recognise the text in one image. Throws <c>dependency_unavailable</c> when no OCR
    /// language is installed and <c>unsupported_format</c> (<c>parse_failed</c>) when the
    /// engine could not decode the image.
    /// </summary>
    public JsonObject Recognize(string imagePath, string? language, CancellationToken cancellationToken)
    {
        var result = Run(imagePath, language, probe: false, cancellationToken);
        if (result["error"] is not null)
        {
            throw DocumentErrors.Unsupported($"'{Path.GetFileName(imagePath)}' could not be decoded for OCR: {result["error"]!.GetValue<string>()}", DocumentErrors.ParseFailed);
        }

        if (result["engine_available"]?.GetValue<bool>() != true)
        {
            var installed = string.Join(", ", (result["languages"] as JsonArray ?? []).Select(n => n!.GetValue<string>()));
            throw new PagentOS.Agent.Core.Commands.CapabilityException(
                PagentOS.Agent.Core.Protocol.ErrorClasses.DependencyUnavailable,
                $"no OCR language is installed for '{language ?? PreferredLanguage}' (installed: [{installed}]); the Windows OCR language pack is the owner's to add",
                retryable: false,
                new Dictionary<string, object?> { [DocumentErrors.DetailKey] = "ocr_language_missing" });
        }

        return result;
    }

    private JsonObject Run(string? imagePath, string? language, bool probe, CancellationToken cancellationToken)
    {
        var startInfo = new ProcessStartInfo(_powershellPath)
        {
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
        };
        startInfo.ArgumentList.Add("-NoProfile");
        startInfo.ArgumentList.Add("-NonInteractive");
        startInfo.ArgumentList.Add("-ExecutionPolicy");
        startInfo.ArgumentList.Add("Bypass");
        startInfo.ArgumentList.Add("-EncodedCommand");
        startInfo.ArgumentList.Add(Convert.ToBase64String(Encoding.Unicode.GetBytes(Script)));
        startInfo.Environment["PAGENTOS_OCR_IMAGE"] = imagePath ?? string.Empty;
        startInfo.Environment["PAGENTOS_OCR_LANGUAGE"] = language ?? PreferredLanguage;
        startInfo.Environment["PAGENTOS_OCR_PROBE"] = probe ? "1" : "0";

        using var process = Process.Start(startInfo)
            ?? throw new PagentOS.Agent.Core.Commands.CapabilityException(
                PagentOS.Agent.Core.Protocol.ErrorClasses.DependencyUnavailable, "powershell.exe did not start for OCR", retryable: true);
        var stdoutTask = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var stderrTask = process.StandardError.ReadToEndAsync(cancellationToken);
        if (!process.WaitForExit((int)Timeout.TotalMilliseconds))
        {
            try
            {
                process.Kill(entireProcessTree: true);
            }
            catch (Exception)
            {
                // Already gone.
            }

            throw new PagentOS.Agent.Core.Commands.CapabilityException(
                PagentOS.Agent.Core.Protocol.ErrorClasses.Timeout, $"OCR did not answer within {Timeout.TotalSeconds:0} s", retryable: true);
        }

        var stdout = stdoutTask.GetAwaiter().GetResult().Trim();
        var stderr = stderrTask.GetAwaiter().GetResult();
        JsonObject? parsed = null;
        if (stdout.Length > 0)
        {
            try
            {
                parsed = JsonNode.Parse(stdout) as JsonObject;
            }
            catch (System.Text.Json.JsonException)
            {
                parsed = null;
            }
        }

        return parsed ?? new JsonObject { ["error"] = $"the OCR host answered no JSON (stderr: {stderr.Trim()})" };
    }
}
