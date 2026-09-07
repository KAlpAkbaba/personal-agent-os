<#
.SYNOPSIS
  The nightly Owner Utterance Suite: every corpus case through the real canonical voice
  path (utterance -> router -> tool -> speech record), with a JSON report.

.DESCRIPTION
  Runs tests/unit/test_owner_utterance_corpus.py under the API's own environment and asks
  it to write the report (PAGENTOS_VOICE_CORPUS_REPORT). The report is what the Living
  Core's "Ses yönlendirme kalitesi" state is built from when it is posted to the Cloud
  Core (-Post): HEALTHY / REGRESSION_FOUND from the counts, never from a claim.

  No audio is involved and nothing here talks to a device: every tool runs against the
  fake device the corpus harness wires (docs/DECISIONS.md ADR-0080).

.PARAMETER ReportPath
  Where to write the JSON report. Default: state/reports/voice-routing-<date>.json.

.PARAMETER Post
  After the run, post the report to the Cloud Core (POST /v1/voice/qualification) using
  the DPAPI-stored owner credential, so the Core's qualification state is the real one.

.PARAMETER CoreUrl
  The Cloud Core base URL for -Post. Default: the production tailnet address.
#>
[CmdletBinding()]
param(
    [string]$ReportPath = "",
    [switch]$Post,
    [string]$CoreUrl = "http://100.90.158.26:8001"
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$api = Join-Path $root "services\api"
if (-not $ReportPath) {
    $stamp = (Get-Date).ToString("yyyy-MM-dd")
    $ReportPath = Join-Path $root "state\reports\voice-routing-$stamp.json"
}
$reportDir = Split-Path -Parent $ReportPath
if (-not (Test-Path $reportDir)) { New-Item -ItemType Directory -Force $reportDir | Out-Null }

$uv = "uv"
$wingetUv = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe"
if (Test-Path $wingetUv) { $uv = $wingetUv }

Write-Host "== Owner Utterance Suite (synthetic voice routing qualification)"
Write-Host "   report: $ReportPath"
$env:PAGENTOS_VOICE_CORPUS_REPORT = $ReportPath
$env:PYTHONIOENCODING = "utf-8"
Push-Location $api
try {
    & $uv run pytest -q --no-header -p no:cacheprovider tests/unit/test_owner_utterance_corpus.py
    $exit = $LASTEXITCODE
} finally {
    Pop-Location
}

if (-not (Test-Path $ReportPath)) {
    Write-Host "!! no report was written (the suite did not reach its report step)"
    exit 2
}
$report = Get-Content $ReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Host ""
Write-Host ("   corpus v{0}: {1} cases, {2} passed, {3} clarification, {4} wrong route, {5} forbidden side effects -> {6}" -f `
    $report.corpus_version, $report.total_cases, $report.passed, $report.clarification, `
    $report.failed_routing, $report.forbidden_side_effects, $report.summary)
foreach ($row in $report.confusion) {
    Write-Host ("   x {0}  {1}  expected={2} resolved={3}" -f $row.case_id, $row.utterance, $row.expected, $row.resolved)
}

if ($Post) {
    $secretStore = Join-Path $root "scripts\lib\SecretStore.ps1"
    if (-not (Test-Path $secretStore)) { throw "scripts\lib\SecretStore.ps1 not found; cannot post without the owner credential" }
    . $secretStore
    $credential = $null
    try { $credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL" } catch { $credential = $null }
    if (-not $credential) { throw "PAGENTOS_OWNER_CREDENTIAL is not stored (DPAPI); nothing posted" }
    $summary = @{
        suite                  = $report.suite
        corpus_version         = $report.corpus_version
        generated_at           = $report.generated_at
        total_cases            = $report.total_cases
        passed                 = $report.passed
        clarification          = $report.clarification
        failed_routing         = $report.failed_routing
        forbidden_side_effects = $report.forbidden_side_effects
        summary                = $report.summary
        confusion              = @($report.confusion | Select-Object -First 20)
    } | ConvertTo-Json -Depth 6
    $loginBody = @{ owner_credential = $credential; client_kind = "cli"; label = "voice-routing-qualification" } | ConvertTo-Json -Compress
    $login = Invoke-RestMethod -Method Post -Uri "$CoreUrl/v1/identity/sessions" -ContentType "application/json; charset=utf-8" `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($loginBody))
    $loginBody = $null
    $credential = $null
    $headers = @{ Authorization = "Bearer $($login.token)" }
    $posted = Invoke-RestMethod -Method Post -Uri "$CoreUrl/v1/voice/qualification" -Headers $headers `
        -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($summary))
    Write-Host ("   posted: state={0} recorded_at={1}" -f $posted.state, $posted.recorded_at)
}

exit $exit
