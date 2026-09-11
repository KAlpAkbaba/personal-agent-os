<#
.SYNOPSIS
    Run one self-development attempt (ADR-0124) on this repository with the real
    engineering model.

.DESCRIPTION
    The engine makes a worktree on a selfdev/<run> branch from the exact base SHA, asks the
    model for an analysis, a plan and a patch, judges the candidate by running things
    (regression test red on the base and green with the patch, targeted tests, lint, scope),
    commits it on its own branch, and STOPS at the policy boundary - it never merges, pushes
    or releases. Budgets (attempts, time, tokens) are hard; the first one crossed quarantines
    the run.

    The Anthropic key is read from the owner's DPAPI store (PAGENTOS_ANTHROPIC_API_KEY) and
    handed to the one child process through its environment - never on a command line, never
    printed - and removed from this process's environment when the run ends.

    Run: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\selfdev\run-selfdev.ps1 `
             -Defect defect.json -Test services/api/tests/unit/test_x.py
    Exit 0 when the candidate stopped at the policy boundary, 1 when it was quarantined or
    refused (the record says why), 2 when it could not be started.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Defect,
    [string]$Base = "",
    [string[]]$Test = @(),
    [string]$Model = "claude-opus-5",
    [int]$MaxAttempts = 3,
    [int]$MaxSeconds = 1800,
    [int]$MaxTokens = 300000
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")

$python = Join-Path $repoRoot "services\api\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { Write-Host "no api virtualenv at $python"; exit 2 }
$defectPath = (Resolve-Path -LiteralPath $Defect).Path
if (-not $Base) {
    $Base = [string](& git -C $repoRoot rev-parse origin/main)
    $Base = $Base.Trim()
}
if ($Base -notmatch '^[0-9a-f]{40}$') { Write-Host "the base must be an exact 40-hex commit"; exit 2 }

$arguments = @("-m", "app.selfdev", "--defect", $defectPath, "--base", $Base, "--model", $Model,
    "--max-attempts", $MaxAttempts, "--max-seconds", $MaxSeconds, "--max-tokens", $MaxTokens,
    "--repo", $repoRoot)
foreach ($path in $Test) { $arguments += @("--test", $path) }

$key = Get-StoredSecretValue -Name "PAGENTOS_ANTHROPIC_API_KEY"
$env:ANTHROPIC_API_KEY = $key
$key = $null
try {
    Push-Location (Join-Path $repoRoot "services\api")
    try {
        & $python @arguments
        $code = $LASTEXITCODE
    }
    finally { Pop-Location }
}
finally {
    Remove-Item Env:\ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
}
exit $code
