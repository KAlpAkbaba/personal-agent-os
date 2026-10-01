<#
.SYNOPSIS
  STT engine comparison on the owner's own recordings (stt-engines-measure): MEASUREMENT
  ONLY. Nothing is adopted, no default changes, no account is opened.

.DESCRIPTION
  1. Points at a folder of recordings. When the folder has no manifest.json, a
     manifest.template.json holding the twenty sentences to record is written there and the
     script stops: record them (WAV, PCM 16-bit, mono, 16 kHz), rename the template to
     manifest.json, run again.
  2. Loads the engine keys that EXIST in the DPAPI secret store (OpenAI, Soniox, Azure) into
     the child's environment and clears them afterwards. A key is never printed, logged or
     written to disk. An engine without a key is a NOT_RUN row in the table, not an error.
  3. Runs `python -m app.voice.stt_compare`, which writes
     docs/evidence/stt-compare-<date>.json: per engine WER, CER, the number of sentences
     whose intent would change, latency p50/p95, and the ten worst sentences side by side.
  4. Prints the Turkish summary and writes it beside the report (stt-compare-<date>.md).

  Where the audio goes: to the engines that ran, and nowhere else - the summary names them.
  The report holds the transcripts of the recorded sentences and lives in the repository:
  point this at scripted sentences, never at free speech.

  A Soniox account and its key are a SEPARATE owner approval. Without the key the Soniox row
  says so and nothing is sent to Soniox.

.PARAMETER Folder
  The folder holding the recordings and their manifest.json.

.PARAMETER Engines
  Comma-separated engine labels to run (default: every configured engine). The others stay
  in the table as "not selected".

.PARAMETER SonioxEu
  Use Soniox's EU endpoint (the EU region is enabled by request and has its own key).

.PARAMETER EvidenceDir
  Where the report is written. Default: docs\evidence.

.PARAMETER Python
  A python.exe to use instead of `uv run python` (a worktree without its own environment).

.EXAMPLE
  .\scripts\voice\stt-compare.ps1 -Folder D:\kayitlar
  .\scripts\voice\stt-compare.ps1 -Folder D:\kayitlar -Engines openai:gpt-4o-transcribe
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Folder,
    [string]$Engines = "",
    [switch]$SonioxEu,
    [string]$EvidenceDir = "",
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$api = Join-Path $root "services\api"
if (-not (Test-Path -LiteralPath $Folder -PathType Container)) { throw "recordings folder not found: $Folder" }
$Folder = (Resolve-Path -LiteralPath $Folder).Path
if (-not $EvidenceDir) { $EvidenceDir = Join-Path $root "docs\evidence" }

$uv = "uv"
$wingetUv = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe"
if (Test-Path $wingetUv) { $uv = $wingetUv }
if ($Python) {
    $exe = $Python
    $prefix = @("-m", "app.voice.stt_compare")
}
else {
    $exe = $uv
    $prefix = @("run", "python", "-m", "app.voice.stt_compare")
}

Write-Host "== STT engine comparison (measurement only)"
Write-Host "   recordings: $Folder"

$env:PYTHONIOENCODING = "utf-8"
$previousEncoding = $null
try {
    # the summary is Turkish and the child writes UTF-8
    $previousEncoding = [Console]::OutputEncoding
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch { $previousEncoding = $null }

$keyNames = @("PAGENTOS_VOICE_OPENAI_API_KEY", "PAGENTOS_VOICE_SONIOX_API_KEY", "PAGENTOS_VOICE_AZURE_SPEECH_KEY")
$loaded = @()
$exit = 1
$evidencePath = ""
Push-Location $api
try {
    # ------------------------------------------------------------ 1. the manifest
    if (-not (Test-Path -LiteralPath (Join-Path $Folder "manifest.json"))) {
        & $exe @prefix --folder $Folder --write-template
        $exit = $LASTEXITCODE
        if ($exit -eq 0) { Write-Host "   no manifest.json yet: nothing was measured and no audio was sent anywhere" }
    }
    else {
        # ---------------------------------------------------------------- 2. the keys
        . (Join-Path $root "scripts\lib\SecretStore.ps1")
        foreach ($name in $keyNames) {
            if (Test-Path "Env:$name") { continue }   # the caller's own environment wins and is left alone
            $stored = $false
            try { $stored = Test-Path -LiteralPath (Get-SecretStorePath -Name $name) } catch { $stored = $false }
            if ($stored) {
                # the key exists only in this process's environment while the child runs;
                # it is never assigned to a variable that could be echoed
                Set-Item -Path "Env:$name" -Value (Get-StoredSecretValue -Name $name)
                $loaded += $name
            }
        }
        Write-Host ("   keys in the secret store: {0}" -f $(if ($loaded.Count) { $loaded -join ", " } else { "none" }))

        # ----------------------------------------------------------------- 3. the run
        if (-not (Test-Path $EvidenceDir)) { New-Item -ItemType Directory -Force $EvidenceDir | Out-Null }
        # An earlier run is evidence too: never overwritten. The day's name first, then the
        # second's, then a counter - two runs in one second chose the same timestamped name
        # and the later one replaced the earlier. The .md beside it is checked as well. The
        # comparison itself takes the name exclusively before any engine is called, so two
        # runs racing to one free name end with one report and one refusal (exit 2).
        $now = Get-Date
        $name = "stt-compare-" + $now.ToString("yyyy-MM-dd")
        $attempt = 0
        while ((Test-Path -LiteralPath (Join-Path $EvidenceDir "$name.json")) -or (Test-Path -LiteralPath (Join-Path $EvidenceDir "$name.md"))) {
            $attempt++
            $name = "stt-compare-" + $now.ToString("yyyy-MM-dd-HHmmss")
            if ($attempt -gt 1) { $name = "$name-$attempt" }
        }
        $evidencePath = Join-Path $EvidenceDir "$name.json"
        $cliArgs = $prefix + @("--folder", $Folder, "--out", $evidencePath)
        if ($Engines) { $cliArgs += @("--engines", $Engines) }
        if ($SonioxEu) { $cliArgs += @("--soniox-url", "wss://stt-rt.eu.soniox.com/transcribe-websocket") }
        Write-Host ""
        & $exe @cliArgs
        $exit = $LASTEXITCODE
    }
}
finally {
    foreach ($name in $loaded) {
        if (Test-Path "Env:$name") { Remove-Item "Env:$name" }
    }
    Pop-Location
    if ($null -ne $previousEncoding) { try { [Console]::OutputEncoding = $previousEncoding } catch { } }
}

if ($exit -ne 0) {
    Write-Host "!! the comparison exited with code $exit (2 = bad input: the folder, its manifest, or a report name that is taken)"
    exit $exit
}
if (-not $evidencePath) { exit 0 }
if (-not (Test-Path -LiteralPath $evidencePath)) {
    Write-Host "!! no report was written"
    exit 4
}

# ---------------------------------------------------------------- 4. the summary
$report = Get-Content -LiteralPath $evidencePath -Raw -Encoding UTF8 | ConvertFrom-Json
$summaryPath = [System.IO.Path]::ChangeExtension($evidencePath, ".md")
$summary = @($report.summary_tr)
$lines = @("# $($summary[0])", "") + @($summary | Select-Object -Skip 1 | ForEach-Object { "- $_" })
[System.IO.File]::WriteAllLines($summaryPath, [string[]]$lines, (New-Object System.Text.UTF8Encoding($false)))
Write-Host ""
Write-Host "   report:  $evidencePath"
Write-Host "   summary: $summaryPath"
exit 0
