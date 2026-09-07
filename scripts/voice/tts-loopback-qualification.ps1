<#
.SYNOPSIS
  The TTS -> STT loopback proxy qualification (VOICE_SPEC §7a, owner directive 2026-09-07):
  the assistant's spoken responses from the Owner Utterance Corpus, synthesised, transcribed
  on an isolated byte path and compared with the text that was meant to be spoken.

.DESCRIPTION
  1. Takes a corpus report (the nightly voice-routing-qualification.ps1 output, which now
     carries each case's full speech), or runs the corpus suite once to produce it.
  2. Samples a deterministic, category-balanced set of speakable cases (-MaxCases, -Seed).
  3. Runs `python -m app.voice.loopback_cli` with the REAL OpenAI TTS (tts-1) and Whisper
     when PAGENTOS_VOICE_OPENAI_API_KEY is in the DPAPI secret store, else with the
     deterministic fakes (and the report says so in its marks).
  4. Writes docs/evidence/tts-loopback-<stamp>.json and prints the counts and the marks.
  5. -Post records ONE voice.tts_loopback row on the Cloud Core through POST /v1/ledger/events
     with the DPAPI owner credential (counts, marks, providers; never a transcript).

  Isolation: nothing here touches the owner's microphone or speakers, no window opens, no
  audio is played. The "loopback capture" is the byte path inside the Python process.

  The API key is handed to the child process through an environment variable that this
  script clears afterwards; it is never printed, logged or written to disk.

  Budget: real runs cost money (characters + audio seconds). Default -MaxCases 40; more
  than 120 is refused without -Force.

.PARAMETER CorpusReport
  A corpus report JSON (with `results[].speech`). Default: the newest
  state\reports\voice-routing-*.json from today, else the corpus suite is run to write one.

.PARAMETER MaxCases
  The budget: at most this many cases are synthesised. Default 40.

.PARAMETER PerCategory
  At most this many cases from any one category (0 = balanced by the budget alone).

.PARAMETER Provider
  auto (real OpenAI when the key is stored, else fake), openai (require the key), fake.

.PARAMETER Seed
  The sampler's seed; the same seed on the same corpus picks the same cases.

.PARAMETER RefreshCorpus
  Run the corpus suite even when a report from today exists.

.PARAMETER Force
  Allow -MaxCases above 120.

.PARAMETER Post
  Record the run on the Cloud Core (voice.tts_loopback ledger row) with the owner credential.

.PARAMETER CoreUrl
  The Cloud Core base URL for -Post. Default: the production tailnet address.

.EXAMPLE
  .\scripts\voice\tts-loopback-qualification.ps1
  .\scripts\voice\tts-loopback-qualification.ps1 -MaxCases 40 -Post
#>
[CmdletBinding()]
param(
    [string]$CorpusReport = "",
    [int]$MaxCases = 40,
    [int]$PerCategory = 0,
    [ValidateSet("auto", "openai", "fake")][string]$Provider = "auto",
    [int]$Seed = 20260907,
    [string]$EvidenceDir = "",
    [switch]$RefreshCorpus,
    [switch]$Force,
    [switch]$Post,
    [string]$CoreUrl = "http://100.90.158.26:8001"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$api = Join-Path $root "services\api"
$reportsDir = Join-Path $root "state\reports"
if (-not $EvidenceDir) { $EvidenceDir = Join-Path $root "docs\evidence" }
if (-not (Test-Path $EvidenceDir)) { New-Item -ItemType Directory -Force $EvidenceDir | Out-Null }
if (-not (Test-Path $reportsDir)) { New-Item -ItemType Directory -Force $reportsDir | Out-Null }

$maxWithoutForce = 120
if ($MaxCases -lt 1) { throw "-MaxCases must be at least 1" }
if ($MaxCases -gt $maxWithoutForce -and -not $Force) {
    throw "-MaxCases $MaxCases exceeds the budget guard ($maxWithoutForce); real runs cost money. Pass -Force if you mean it."
}

$uv = "uv"
$wingetUv = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe"
if (Test-Path $wingetUv) { $uv = $wingetUv }

$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd-HHmmss")
$evidencePath = Join-Path $EvidenceDir "tts-loopback-$stamp.json"
$evidenceRelative = "docs/evidence/tts-loopback-$stamp.json"

Write-Host "== TTS -> STT loopback proxy qualification"
Write-Host "   evidence: $evidencePath"

# ---------------------------------------------------------------- 1. corpus report
if (-not $CorpusReport) {
    $today = (Get-Date).ToString("yyyy-MM-dd")
    $candidate = Join-Path $reportsDir "voice-routing-$today.json"
    if ((Test-Path $candidate) -and -not $RefreshCorpus) {
        $CorpusReport = $candidate
        Write-Host "   corpus report: $CorpusReport (today's)"
    }
    else {
        Write-Host "   corpus report: running the Owner Utterance Suite to write one"
        $nightly = Join-Path $root "scripts\core\voice-routing-qualification.ps1"
        & $nightly -ReportPath $candidate
        if (-not (Test-Path $candidate)) { throw "the corpus suite wrote no report at $candidate" }
        $CorpusReport = $candidate
    }
}
if (-not (Test-Path $CorpusReport)) { throw "corpus report not found: $CorpusReport" }
$corpus = Get-Content $CorpusReport -Raw -Encoding UTF8 | ConvertFrom-Json
$speakable = @($corpus.results | Where-Object {
    $_.verdict -eq "correct" -and ($_.expected_response -in @("ok", "refused")) -and $_.speech
})
if ($speakable.Count -eq 0) {
    throw "the corpus report carries no speech per case (results[].speech); it predates the loopback harness - rerun with -RefreshCorpus"
}
Write-Host ("   corpus v{0}: {1} cases, {2} speakable (ok/refused with speech)" -f $corpus.corpus_version, $corpus.total_cases, $speakable.Count)

# --------------------------------------------------------------- 2. the provider
$secretStore = Join-Path $root "scripts\lib\SecretStore.ps1"
. $secretStore
$keyStored = $false
try {
    $keyStored = Test-Path -LiteralPath (Get-SecretStorePath -Name "PAGENTOS_VOICE_OPENAI_API_KEY")
} catch { $keyStored = $false }

$chosen = $Provider
if ($chosen -eq "auto") { $chosen = if ($keyStored) { "openai" } else { "fake" } }
if ($chosen -eq "openai" -and -not $keyStored) {
    throw "PAGENTOS_VOICE_OPENAI_API_KEY is not in the secret store; store it (scripts\secret-store.ps1 -Set PAGENTOS_VOICE_OPENAI_API_KEY) or run with -Provider fake"
}
Write-Host "   provider: $chosen$(if ($chosen -eq 'fake') { ' (deterministic fakes: the loop is exercised, no real voice)' })"

# ----------------------------------------------------------------- 3. the loop
$cliArgs = @(
    "run", "python", "-m", "app.voice.loopback_cli",
    "--cases", $CorpusReport,
    "--provider", $chosen,
    "--out", $evidencePath,
    "--max-cases", "$MaxCases",
    "--seed", "$Seed"
)
if ($PerCategory -gt 0) { $cliArgs += @("--per-category", "$PerCategory") }

$env:PYTHONIOENCODING = "utf-8"
$exit = 1
Push-Location $api
try {
    if ($chosen -eq "openai") {
        # the key exists only in this process's environment while the child runs;
        # it is never assigned to a variable that could be echoed
        $env:PAGENTOS_VOICE_OPENAI_API_KEY = Get-StoredSecretValue -Name "PAGENTOS_VOICE_OPENAI_API_KEY"
    }
    & $uv @cliArgs
    $exit = $LASTEXITCODE
} finally {
    if (Test-Path Env:PAGENTOS_VOICE_OPENAI_API_KEY) { Remove-Item Env:PAGENTOS_VOICE_OPENAI_API_KEY }
    Pop-Location
}

if ($exit -ne 0) {
    Write-Host "!! the loopback run exited with code $exit (2 bad input, 3 no key, 4 the run failed)"
    exit $exit
}
if (-not (Test-Path $evidencePath)) {
    Write-Host "!! no evidence file was written"
    exit 4
}

# ---------------------------------------------------------------- 4. the summary
$report = Get-Content $evidencePath -Raw -Encoding UTF8 | ConvertFrom-Json
$t = $report.totals
$a = $report.audio
$m = $report.marks
Write-Host ""
Write-Host ("   {0} cases: {1} matched, {2} degraded, {3} mismatched, {4} error; mean WER {5}" -f `
    $t.cases, $t.matched, $t.degraded, $t.mismatched, $t.error, $report.mean_wer)
Write-Host ("   audio: {0} files, {1} ms total, mean {2} ms (min {3}, max {4}); TTS {5} -> STT {6}" -f `
    $a.cases_with_audio, $a.total_ms, $a.mean_ms, $a.min_ms, $a.max_ms, $report.providers.tts, $report.providers.stt)
foreach ($name in @("audio_generation", "loopback_semantics", "physical_hearing")) {
    $mark = $m.$name
    Write-Host ("   {0,-20} {1,-17} {2}" -f $name, $mark.mark, $mark.reason)
}
foreach ($case in @($report.cases | Where-Object { $_.verdict -in @("mismatched", "error") })) {
    $why = if ($case.error) { $case.error } else { "WER $($case.wer) missing: $($case.missing_content -join ', ')" }
    Write-Host ("   x {0} [{1}] {2}" -f $case.case_id, $case.category, $why)
}
Write-Host ("   summary: {0}" -f $report.summary)

# ----------------------------------------------------------------- 5. the record
if ($Post) {
    $credential = $null
    try { $credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL" } catch { $credential = $null }
    if (-not $credential) { throw "PAGENTOS_OWNER_CREDENTIAL is not stored (DPAPI); nothing posted" }
    $loginBody = @{ owner_credential = $credential; client_kind = "cli"; label = "tts-loopback-qualification" } | ConvertTo-Json -Compress
    $login = Invoke-RestMethod -Method Post -Uri "$CoreUrl/v1/identity/sessions" -ContentType "application/json; charset=utf-8" `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($loginBody))
    $loginBody = $null
    $credential = $null
    $headers = @{ Authorization = "Bearer $($login.token)" }
    $event = @{
        event_type      = $report.ledger.event_type
        subsystem       = "voice"
        action          = "tts_loopback_qualified"
        factual_summary = $report.ledger.factual_summary
        source_ref      = "tts_loopback:$stamp"
        status          = "completed"
        severity        = $(if ($report.summary -eq "HEALTHY") { "info" } else { "warning" })
        result          = $report.summary
        evidence_refs   = @(@{ kind = "file"; ref = $evidenceRelative })
        detail_json     = $report.ledger.detail
    } | ConvertTo-Json -Depth 8
    try {
        $posted = Invoke-RestMethod -Method Post -Uri "$CoreUrl/v1/ledger/events" -Headers $headers `
            -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($event))
        Write-Host ("   posted: event_id={0} recorded_at={1}" -f $posted.event_id, $posted.recorded_at)
    }
    catch {
        $status = $null
        try { $status = [int]$_.Exception.Response.StatusCode } catch { $status = $null }
        if ($status -eq 422) {
            Write-Host "!! the Cloud Core refused the row (422): it does not know voice.tts_loopback yet - release the Cloud Core with this vocabulary first, then -Post again"
        }
        else {
            Write-Host "!! posting failed: $($_.Exception.Message)"
        }
        exit 5
    }
}

exit 0
