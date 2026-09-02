<#
.SYNOPSIS
    Move the already-enrolled Windows agent from the loopback broker to the Cloud Core on
    the tailnet — without reinstalling, re-enrolling, or minting a new device identity.

.DESCRIPTION
    The device's identity (its id and P-256 private key) is valid against ANY broker: the
    broker authenticates the device by its public key, and nothing in the handshake is
    bound to a hostname. So the migration is only two facts moving:

      1. the cloud database learns the device that already exists — rebuilt from the
         device's own NON-SECRET identity document (id, name, PUBLIC key), exactly the
         restore path already proven locally. The private key never leaves this machine;
      2. the installed service's appsettings.json points at the tailnet endpoint instead of
         127.0.0.1, applied atomically with a health probe first and a one-command
         rollback.

    Elevation is required for one reason only: the device key and state are service-owned
    machine material (SYSTEM + Administrators), and reading the identity document means
    reading them.

.EXAMPLE
    # From an ELEVATED PowerShell at the repository root:
    .\scripts\cloud\migrate-agent-to-cloud.ps1 -BrokerHost 100.90.158.26
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BrokerHost,
    [int]$Port = 8001,
    [string]$CloudUser = "root",
    [string]$DataDir = (Join-Path $env:ProgramData "PagentOS\agent"),
    [string]$InstallRoot = (Join-Path $env:ProgramFiles "PagentOS\agent"),
    [string]$ServiceName = "PagentOSDeviceAgent",
    [switch]$SkipSwitch,

    # Phase resumability. "SwitchBroker" skips the identity read and the cloud registration
    # (already done on the real machine) and resumes at the broker switch; "All" runs
    # everything. Idempotent either way: registration re-runs are harmless, but there is no
    # reason to re-read machine material for a step that already completed.
    [ValidateSet("All", "SwitchBroker")]
    [string]$StartPhase = "All"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "..\lib\NativeProcess.ps1")

$shouldSwitch = -not [bool]$SkipSwitch
$resumeAtSwitch = ($StartPhase -eq "SwitchBroker")
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ssh = Join-Path $env:SystemRoot "System32\OpenSSH\ssh.exe"
$scp = Join-Path $env:SystemRoot "System32\OpenSSH\scp.exe"

function Assert-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this from an ELEVATED PowerShell: the device key and state are SYSTEM+Administrators material."
    }
}

Assert-Elevated
if (-not (Test-Path -LiteralPath $ssh)) { throw "OpenSSH client not found at $ssh" }

# ------------------------------------------------------------------ 1. the cloud answers
Write-Host "=== 1. the Cloud Core answers over the tailnet ===" -ForegroundColor Cyan
$baseUrl = "http://${BrokerHost}:$Port"
$health = Invoke-RestMethod -Uri "$baseUrl/v1/system/health" -TimeoutSec 20
Write-Host "  $baseUrl -> status=$($health.status) version=$($health.version)"

if ($resumeAtSwitch) {
    Write-Host ""
    Write-Host "=== 2-3. skipped (-StartPhase SwitchBroker): identity already read and device already registered in the cloud ===" -ForegroundColor Cyan
}
else {
# --------------------------------------------------- 2. this device's identity, non-secret
Write-Host ""
Write-Host "=== 2. reading the device's own identity (public half only) ===" -ForegroundColor Cyan
# The REPO build, not the installed exe: the installed binary may predate the `identity`
# verb, and reading state is never a reason to touch a qualified runtime.
$dotnet = Join-Path $env:LOCALAPPDATA "Microsoft\dotnet\dotnet.exe"
if (-not (Test-Path $dotnet)) { $dotnet = (Get-Command dotnet -ErrorAction Stop).Source }
$helperProject = Join-Path $repoRoot "devices\windows-agent\src\PagentOS.DeviceService\PagentOS.DeviceService.csproj"
$helperExe = Join-Path $repoRoot "devices\windows-agent\src\PagentOS.DeviceService\bin\Release\net10.0-windows\PagentOS.DeviceService.exe"
if (-not (Test-Path -LiteralPath $helperExe)) {
    Write-Host "  building the identity helper..."
    $build = Invoke-NativeProcess -FilePath $dotnet -Arguments @("build", $helperProject, "-c", "Release", "--nologo", "-v", "q") -TimeoutSeconds 600
    Assert-NativeSuccess -Result $build -Activity "build identity helper"
}

$env:PAGENTOS_AGENT_DataDir = $DataDir
try {
    $identity = Invoke-MachineReadableProcess -FilePath $helperExe -Arguments @("identity") `
        -TimeoutSeconds 60 -Activity "device identity" -SensitiveOutput $false
}
finally {
    Remove-Item Env:\PAGENTOS_AGENT_DataDir -ErrorAction SilentlyContinue
}
Write-Host "  device_id=$($identity.device_id) name=$($identity.name) enrolled_at=$($identity.enrolled_at)"
Write-Host "  (the private key is not read out; only the SPKI public half travels)"

# ------------------------------------------------- 3. rebuild the row in the cloud database
Write-Host ""
Write-Host "=== 3. registering the EXISTING device in the cloud database ===" -ForegroundColor Cyan
$documentPath = Join-Path ([System.IO.Path]::GetTempPath()) "pagentos-identity-$([guid]::NewGuid().ToString('N')).json"
$document = @{
    device_id           = $identity.device_id
    name                = $identity.name
    public_key_spki_b64 = $identity.public_key_spki_b64
} | ConvertTo-Json
[System.IO.File]::WriteAllText($documentPath, $document, (New-Object System.Text.UTF8Encoding($false)))

try {
    $copy = Invoke-NativeProcess -FilePath $scp -Arguments @(
        "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20",
        $documentPath, "${CloudUser}@${BrokerHost}:/tmp/pagentos-identity.json") -TimeoutSeconds 180
    Assert-NativeSuccess -Result $copy -Activity "copy the identity document to the host"

    $restore = Invoke-NativeProcess -FilePath $ssh -Arguments @(
        "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20",
        "${CloudUser}@${BrokerHost}",
        "cd /opt/pagentos/app && ./scripts/cloud/restore-device-row.sh /tmp/pagentos-identity.json; rc=`$?; rm -f /tmp/pagentos-identity.json; exit `$rc"
    ) -TimeoutSeconds 600
    Write-Host ($restore.StdOut.Trim())
    if ($restore.ExitCode -ne 0) {
        Write-Host $restore.StdErr
        throw "registering the device in the cloud database failed with exit code $($restore.ExitCode)"
    }
}
finally {
    Remove-Item -LiteralPath $documentPath -Force -ErrorAction SilentlyContinue
}
}   # end of the identity + registration phases

# ------------------------------------------------------------- 4. point the agent at it
if (-not $shouldSwitch) {
    Write-Host ""
    Write-Host "-SkipSwitch: the device is registered in the cloud, but the agent still dials its current broker."
    return
}

Write-Host ""
Write-Host "=== 4. switching the installed agent to the tailnet endpoint ===" -ForegroundColor Cyan
& (Join-Path $repoRoot "scripts\switch-agent-broker.ps1") -BrokerHost $BrokerHost -Port $Port `
    -ServiceName $ServiceName -InstallRoot $InstallRoot

Write-Host ""
Write-Host "The agent now dials the Cloud Core over the tailnet, with the same device identity." -ForegroundColor Green
Write-Host "Rollback, if ever needed:  .\scripts\switch-agent-broker.ps1 -Rollback"

# ------------------------------------------------- 5. owner identity on the cloud deployment
# This deployment has its own identity root (ADR-0032 §4): two instances, two roots, neither
# able to impersonate the other. Bootstrap is one-time and loopback-only, so it runs ON the
# host — through Tailscale SSH, from YOUR console, so the credential is displayed to you and
# to nobody else. It is never returned to this script's caller, written to a file, or logged.
Write-Host ""
Write-Host "=== 5. owner credential for the cloud deployment ===" -ForegroundColor Cyan

$identityCheck = Invoke-NativeProcess -FilePath $ssh -Arguments @(
    "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20", "${CloudUser}@${BrokerHost}",
    "test -s /mnt/pagentos-data/identity/owner_credential.json && echo EXISTS || echo ABSENT"
) -TimeoutSeconds 120
$alreadyBootstrapped = ($identityCheck.StdOut -match "EXISTS")

if ($alreadyBootstrapped) {
    Write-Host "  an owner credential already exists on the host; bootstrap is one-time and is being left alone."
    Write-Host "  If you do not have it, rotate on the HOST (never through the API):"
    Write-Host "    ssh $CloudUser@$BrokerHost `"docker exec pagentos-prod-api uv run python -m app.identity.recover --rotate`""
}
else {
    Write-Host "  no owner credential on this deployment yet. Minting it now, ONCE, in THIS console." -ForegroundColor Yellow
    Write-Host "  Copy it into your password manager before continuing - it is not stored anywhere else." -ForegroundColor Yellow
    Write-Host ""
    # Deliberately NOT captured: the output goes straight to the console so the value never
    # enters a variable, a transcript, or this script's output stream.
    & $ssh -o StrictHostKeyChecking=accept-new "${CloudUser}@${BrokerHost}" `
        "curl -fsS -X POST http://127.0.0.1:8001/v1/identity/bootstrap"
    Write-Host ""
    Write-Host ""
    Write-Host "  ^ that credential is shown exactly once." -ForegroundColor Yellow
}

# The E2E and the resilience runs need a session, not the credential. Minting one here and
# storing it DPAPI-encrypted to this Windows account means the credential itself is typed
# once, by you, and never again.
Write-Host ""
$storeRoot = Join-Path $env:LOCALAPPDATA "PagentOS\secrets"
$storedSession = Join-Path $storeRoot "PAGENTOS_CLOUD_SESSION_TOKEN.dpapi"
$secureCredential = Read-Host -Prompt "Paste the owner credential once to mint an automation session (input hidden)" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureCredential)
try { $plainCredential = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }

try {
    $session = Invoke-RestMethod -Uri "$baseUrl/v1/identity/sessions" -Method Post -TimeoutSec 30 `
        -ContentType "application/json" `
        -Body (@{ owner_credential = $plainCredential; client_kind = "cli"; label = "cloud-automation" } | ConvertTo-Json)
}
finally {
    $plainCredential = $null
}

New-Item -ItemType Directory -Force -Path $storeRoot | Out-Null
(ConvertTo-SecureString -String $session.token -AsPlainText -Force) | ConvertFrom-SecureString |
    Set-Content -Path $storedSession -Encoding ASCII
$session = $null

Write-Host ""
Write-Host "Automation session stored, encrypted to this Windows account:" -ForegroundColor Green
Write-Host "  $storedSession"
Write-Host "Nothing else needs the credential; put it in your password manager and close this window."
