<#
.SYNOPSIS
    One command: install the browser worker that can attach, then authorize it to attach
    to the owner's own Chrome (contract v1.4, ADR-0113).

.DESCRIPTION
    Two steps that always go together, so they are one command.

    1. install-device-service.ps1 - puts the worker that understands profile 'owner' on
       this machine, preserving the switches this install already uses. Needs elevation,
       which is why this script checks for it up front instead of failing halfway. Its
       staged-update path is qualified separately by scripts/qualify-staged-update.ps1
       (85 checks) and rolls back on its own if anything fails.
    2. enroll-owner-chrome.ps1 - closes Chrome, relaunches it with a loopback debugging
       port, proves the port answers, and records the authorization.

    Step 2 CLOSES CHROME. Tabs come back if "Continue where you left off" is on. From
    then on, open Chrome from the shortcut this script prints, or the port is gone and
    the enrollment has to be redone.

    Skip step 1 with -EnrollOnly when the worker is already current (a second enrollment
    after a Chrome restart is the common case, and needs no install at all).

.EXAMPLE
    .\scripts\browser\use-my-chrome.ps1

.EXAMPLE
    .\scripts\browser\use-my-chrome.ps1 -EnrollOnly
#>
[CmdletBinding()]
param(
    [switch]$EnrollOnly,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

if (-not $EnrollOnly) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Host "This needs an elevated PowerShell (the worker lives under Program Files)." -ForegroundColor Yellow
        Write-Host "Right-click PowerShell -> Run as administrator, then run this again."
        Write-Host ""
        Write-Host "Already installed and only re-enrolling after a Chrome restart?"
        Write-Host "  .\scripts\browser\use-my-chrome.ps1 -EnrollOnly"
        exit 1
    }

    Write-Host "== 1/2  installing the browser worker that can attach" -ForegroundColor Cyan
    # The switches this machine's companion is already configured with
    # (DisplayPowerEnabled / OperatorEnabled in its appsettings.json). Re-running with
    # the same set is what makes the installer an update rather than a reconfiguration.
    $installer = Join-Path $repoRoot "scripts\install-device-service.ps1"
    & $installer -DisplayPower -Operator -BrowserChannel chrome
    if ($LASTEXITCODE -ne 0) {
        throw "install-device-service.ps1 exited $LASTEXITCODE; nothing was enrolled."
    }
    Write-Host ""
}

Write-Host "== 2/2  authorizing attach to your Chrome" -ForegroundColor Cyan
$enroll = Join-Path $repoRoot "scripts\browser\enroll-owner-chrome.ps1"
if ($Force) { & $enroll -Force } else { & $enroll }
if ($LASTEXITCODE -ne 0) {
    throw "enroll-owner-chrome.ps1 exited $LASTEXITCODE."
}

Write-Host ""
Write-Host 'Done. Say: "YouTube''dan <sarki adi> ac."' -ForegroundColor Green
Write-Host "It will play in the Chrome window that is open now."
Write-Host ""
Write-Host "Undo:  .\scripts\browser\enroll-owner-chrome.ps1 -Revoke"
