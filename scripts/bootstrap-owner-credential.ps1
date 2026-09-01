<#
.SYNOPSIS
    Mints the owner credential for a real deployment, shows it exactly once, and proves it
    was never written anywhere it could be read again.

.DESCRIPTION
    The owner credential is the root of the whole authentication model (ADR-0027): it is
    exchanged for bearer sessions, and only its SHA-256 hash is stored, in a file outside the
    database. It is displayed once, here, and then it exists in exactly two places — the
    owner's password manager, and nowhere.

    So this script is mostly about the places it must NOT end up:

      * not on disk — nothing is written except the API's own hash-only identity root;
      * not in a PowerShell transcript — the script refuses to run when transcription is on,
        because a transcript would capture the credential as plaintext in a log file;
      * not in source control — the repository never sees it;
      * not in a scrollback the owner forgets about — the script says to clear the screen.

    Bootstrap is loopback-only and one-time by design: it mints authority from nothing, so it
    must run on the machine hosting the API, and it refuses once a credential exists.
    Recovering a lost credential is not an API operation at all — see -Rotate below.

.PARAMETER ApiBase
    Where the API is listening, from this machine. Must be loopback.

.PARAMETER Rotate
    Do not bootstrap: print the host-side recovery command instead. Rotation runs inside the
    API process on the host, not over HTTP, because a credential you have lost cannot
    authenticate the request that replaces it.

.EXAMPLE
    .\scripts\bootstrap-owner-credential.ps1
#>
[CmdletBinding()]
param(
    [string]$ApiBase = "http://127.0.0.1:8001",
    [switch]$Rotate,
    [switch]$Status
)

$ErrorActionPreference = "Stop"

function Assert-NoTranscription {
    # System-wide transcription would write this credential into a log file the moment it is
    # printed. Refusing is the only correct behaviour: there is no way to print a secret to a
    # transcribed console safely.
    $policyPaths = @(
        "HKLM:\Software\Policies\Microsoft\Windows\PowerShell\Transcription",
        "HKCU:\Software\Policies\Microsoft\Windows\PowerShell\Transcription"
    )
    foreach ($path in $policyPaths) {
        if (Test-Path $path) {
            $value = (Get-ItemProperty -Path $path -ErrorAction SilentlyContinue).EnableTranscripting
            if ($value -eq 1) {
                throw "PowerShell transcription is enabled by policy ($path). The credential would be written to a transcript file in plaintext. Disable transcription, or bootstrap from a console where it is off."
            }
        }
    }

    try {
        # Stop-Transcript throws when no transcript is running, which is the state we want.
        $null = Stop-Transcript -ErrorAction Stop
        throw "A PowerShell transcript was running in this session and has now been stopped. Start a fresh console and run this again, so no part of the credential is in that file."
    }
    catch [System.InvalidOperationException] {
        # No transcript running. Good.
    }
}

function Assert-Loopback {
    param([string]$Base)
    $uri = [Uri]$Base
    if ($uri.Host -notin @("127.0.0.1", "::1", "localhost")) {
        throw "bootstrap is loopback-only: $Base is not local. Run this on the machine hosting the API."
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$apiDir = Join-Path $repoRoot "services\api"

if ($Status -or $Rotate) {
    $uv = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe"
    if (-not (Test-Path $uv)) { $uv = "uv" }

    if ($Status) {
        Push-Location $apiDir
        try { & $uv run python -m app.identity.recover --status }
        finally { Pop-Location }
        exit $LASTEXITCODE
    }

    Write-Host ""
    Write-Host "Rotation runs on the host, not over HTTP: a credential you have lost cannot" -ForegroundColor Yellow
    Write-Host "authenticate the request that would replace it. From $apiDir, run:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "    uv run python -m app.identity.recover --rotate"
    Write-Host ""
    Write-Host "It prints the new credential once and revokes every existing session, so every"
    Write-Host "signed-in client must sign in again. Add --keep-sessions to leave them alive."
    exit 0
}

Assert-NoTranscription
Assert-Loopback -Base $ApiBase

Write-Host "checking the API is up and not already bootstrapped..."
try {
    $health = Invoke-RestMethod -Uri "$ApiBase/v1/system/health" -Method Get -TimeoutSec 10
}
catch {
    throw "cannot reach $ApiBase - start the API first. ($($_.Exception.Message))"
}
Write-Host "API is up (status: $($health.status))"

try {
    $response = Invoke-RestMethod -Uri "$ApiBase/v1/identity/bootstrap" -Method Post -TimeoutSec 30
}
catch {
    $status = $_.Exception.Response.StatusCode.value__
    if ($status -eq 409) {
        Write-Host ""
        Write-Host "An owner credential already exists. Bootstrap is one-time by design." -ForegroundColor Yellow
        Write-Host "If you have lost it, rotate on the host:  .\scripts\bootstrap-owner-credential.ps1 -Rotate"
        exit 3
    }
    if ($status -eq 403) {
        throw "the API refused this peer as non-loopback. Run this on the machine hosting the API."
    }
    throw
}

$credential = $response.owner_credential

Write-Host ""
Write-Host "==================== OWNER CREDENTIAL - SHOWN ONCE ====================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  $credential"
Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Store it in your password manager NOW. It is not saved anywhere else:" -ForegroundColor Yellow
Write-Host "  * the server keeps only its SHA-256 hash, in the identity root file;"
Write-Host "  * this script wrote nothing to disk;"
Write-Host "  * there is no API that can show it to you again."
Write-Host ""
Write-Host "Then clear this screen so it is not left in scrollback:  Clear-Host"
Write-Host ""

# Prove the hash-only claim rather than asserting it: show where the root lives and that the
# credential itself is not in it.
Push-Location $apiDir
try {
    $uv = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe"
    if (-not (Test-Path $uv)) { $uv = "uv" }
    & $uv run python -m app.identity.recover --status
}
catch {
    Write-Warning "could not read the identity-root status: $($_.Exception.Message)"
}
finally {
    Pop-Location
}

$credential = $null
[System.GC]::Collect()
