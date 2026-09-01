<#
.SYNOPSIS
    One elevated run that finishes the real-machine qualification:
    update the agent binaries in place -> restore the device's broker registration ->
    Notepad E2E (service-restart proof) -> Cloud Core restart + reconnect + second command ->
    full verification -> final owner-credential rotation.

.DESCRIPTION
    Everything here follows from three measured facts:

      * the pipe DACL can only be observed non-invasively from the pipe HANDLE at creation,
        so proving criterion 1.1 requires the service binary that audits the effective SDDL
        (`ipc_pipe_created`) - the currently installed binary predates that capture. The
        update is a staged, atomic binary swap through the existing installer; enrollment,
        device key, configuration and ACL posture are all preserved, and the installer's own
        idempotency decides "AlreadyCorrect" for the service registration;

      * the device's broker ROW was destroyed by the integration suite (shared database,
        schema round-trip test), while the device identity on this machine is intact. It is
        restored from the device's own key - same device_id, same public key, no
        re-enrollment, no new identity - into a DEDICATED database (`pagentos_prod`) that no
        test suite touches;

      * rotation revokes sessions, so it runs LAST: the E2E and both restart proofs use the
        pre-rotation automation session, and the credential displayed at the end is minted
        after everything already works.

.EXAMPLE
    # From an ELEVATED PowerShell, at the repository root:
    .\scripts\finalize-qualification.ps1
#>
[CmdletBinding()]
param(
    [string]$ServiceName = "PagentOSDeviceAgent",
    [string]$InstallRoot = (Join-Path $env:ProgramFiles "PagentOS\agent"),
    [string]$DataDir = (Join-Path $env:ProgramData "PagentOS\agent"),
    [switch]$SkipRotation
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "lib\NativeProcess.ps1")

$repoRoot = Split-Path -Parent $PSScriptRoot
$apiRoot = Join-Path $repoRoot "services\api"

function Assert-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this from an elevated PowerShell."
    }
}

function Get-Uv {
    $candidate = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe"
    if (Test-Path -LiteralPath $candidate) { return $candidate }
    return (Get-Command uv -ErrorAction Stop).Source
}

function Invoke-BrokerCommandE2E {
    <#  One real command through the broker; returns $true on a verified Notepad + ACK.  #>
    param([hashtable]$Headers, [string]$BaseUrl, [string]$DeviceId, [string]$Label)

    $online = $false
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        try {
            $devices = Invoke-RestMethod -Uri "$BaseUrl/v1/devices" -Headers $Headers -TimeoutSec 10
            $mine = $devices.devices | Where-Object { $_.device_id -eq $DeviceId }
            if ($mine -and $mine.status -eq "online") { $online = $true; break }
        }
        catch { }
        Start-Sleep -Seconds 2
    }
    if (-not $online) {
        Write-Warning "[$Label] device did not come online within 90s"
        return $false
    }
    Write-Host "[$Label] device online at the broker"

    $command = Invoke-RestMethod -Uri "$BaseUrl/v1/devices/$DeviceId/commands" -Method Post `
        -Headers $Headers -ContentType "application/json" -TimeoutSec 15 `
        -Body (@{ capability = "desktop.open_application"; payload = @{ application = "notepad" }; timeout_s = 60 } | ConvertTo-Json)

    $result = $null
    $commandDeadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $commandDeadline) {
        $result = Invoke-RestMethod -Uri "$BaseUrl/v1/devices/$DeviceId/commands/$($command.command_id)" -Headers $Headers -TimeoutSec 10
        if ($result.status -in @("succeeded", "failed", "expired", "cancelled")) { break }
        Start-Sleep -Milliseconds 500
    }

    Write-Host "[$Label] command $($command.command_id): status=$($result.status) pid=$($result.result.pid) trace=$($result.trace_id)"
    if ($result.status -ne "succeeded") { return $false }

    if ($result.result.pid) {
        $proc = Get-Process -Id $result.result.pid -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "[$Label] real process verified: '$($proc.ProcessName)' pid $($proc.Id) session $($proc.SessionId)"
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            Write-Host "[$Label] closed the Notepad the command opened"
        }
    }
    return $true
}

Assert-Elevated
$uv = Get-Uv

# ---------------------------------------------------------------- step 1: update the agent

Write-Host "=== step 1: update the agent binaries in place (enrollment preserved) ===" -ForegroundColor Cyan

# Stop the companion so the swap is clean; the logon task and this script both restart it.
Get-Process -Name "PagentOS.SessionCompanion" -ErrorAction SilentlyContinue | Stop-Process -Force
& (Join-Path $PSScriptRoot "install-device-service.ps1")

$companionExe = Join-Path $InstallRoot "companion\PagentOS.SessionCompanion.exe"
if (-not (Get-Process -Name "PagentOS.SessionCompanion" -ErrorAction SilentlyContinue)) {
    Write-Host "starting the companion from the pinned path"
    Start-Process -FilePath $companionExe -WindowStyle Hidden
}

# ------------------------------------------------- step 2: restore the broker registration

Write-Host ""
Write-Host "=== step 2: restore the device's broker registration (NOT re-enrollment) ===" -ForegroundColor Cyan

$state = Get-Content -LiteralPath (Join-Path $DataDir "state.json") -Raw | ConvertFrom-Json
Write-Host "device on this machine: device_id=$($state.device_id) name=$($state.name)"

# Derive the PUBLIC key from the device's own private key. Elevated read; the key never
# leaves this process and only the SPKI (public) half is passed on.
$pem = Get-Content -LiteralPath (Join-Path $DataDir "device.key") -Raw
$ecdsa = [System.Security.Cryptography.ECDsa]::Create()
$ecdsa.ImportFromPem($pem)
$pem = $null
$spki = [Convert]::ToBase64String($ecdsa.ExportSubjectPublicKeyInfo())
$ecdsa.Dispose()

# Make sure the Cloud Core is up on its dedicated database, then restore the row there.
$agentConfig = Get-Content -LiteralPath (Join-Path $InstallRoot "service\appsettings.json") -Raw | ConvertFrom-Json
$baseUrl = $agentConfig.BrokerRestUrl.TrimEnd('/')
try {
    $null = Invoke-RestMethod -Uri "$baseUrl/v1/system/health" -TimeoutSec 5
}
catch {
    Write-Host "starting the Cloud Core (dedicated database pagentos_prod)..."
    & (Join-Path $PSScriptRoot "dev-broker.ps1") -SkipInfra
}

$env:PAGENTOS_DATABASE_URL = "postgresql+psycopg://pagentos:pagentos-dev@127.0.0.1:15432/pagentos_prod"
try {
    $restore = Invoke-MachineReadableProcess -FilePath $uv -Arguments @(
        "run", "python", "scripts/restore_device_registration.py",
        "--device-id", $state.device_id,
        "--name", $state.name,
        "--public-key-spki-b64", $spki
    ) -WorkingDirectory $apiRoot -TimeoutSeconds 120 -Activity "device registration restore" -SensitiveOutput $false
    Write-Host "restore outcome: $($restore.outcome)"
    if ($restore.outcome -eq "refused") { throw "restore refused: $($restore.reason)" }
}
finally {
    Remove-Item Env:\PAGENTOS_DATABASE_URL -ErrorAction SilentlyContinue
}

# The Cloud Core must actually be serving the prod database for the agent to be known.
Write-Host "restarting the Cloud Core on the dedicated database..."
& (Join-Path $PSScriptRoot "dev-broker.ps1") -Stop
& (Join-Path $PSScriptRoot "dev-broker.ps1") -SkipInfra

# ----------------------------------------------------- step 3: session for the E2E commands

Write-Host ""
Write-Host "=== step 3: owner session for the E2E (new database has no sessions) ===" -ForegroundColor Cyan
Write-Host "The dedicated database starts with no sessions, so one sign-in is needed."
$secure = Read-Host -Prompt "Owner credential (input hidden; used in memory only)" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $credential = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }

try {
    $session = Invoke-RestMethod -Uri "$baseUrl/v1/identity/sessions" -Method Post -TimeoutSec 30 `
        -ContentType "application/json" `
        -Body (@{ owner_credential = $credential; client_kind = "cli"; label = "final-qualification" } | ConvertTo-Json)
}
finally {
    $credential = $null
}
$headers = @{ Authorization = "Bearer $($session.token)" }
Write-Host "session established"

# ------------------------------------- step 4: E2E #1 (doubles as service-restart -> command)

Write-Host ""
Write-Host "=== step 4: Notepad E2E after a real DeviceService restart ===" -ForegroundColor Cyan
Restart-Service -Name $ServiceName -Force
(Get-Service -Name $ServiceName).WaitForStatus("Running", (New-TimeSpan -Seconds 45))
$svc = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
$svcProc = Get-CimInstance Win32_Process -Filter "ProcessId=$($svc.ProcessId)"
Write-Host "service restarted: pid=$($svc.ProcessId) account=$($svc.StartName) session=$($svcProc.SessionId)"
$e2e1 = Invoke-BrokerCommandE2E -Headers $headers -BaseUrl $baseUrl -DeviceId $state.device_id -Label "svc-restart"

# ------------------------------------------- step 5: Cloud Core restart -> reconnect -> command

Write-Host ""
Write-Host "=== step 5: Cloud Core restart -> agent reconnect -> command ===" -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "dev-broker.ps1") -Stop
& (Join-Path $PSScriptRoot "dev-broker.ps1") -SkipInfra
$e2e2 = Invoke-BrokerCommandE2E -Headers $headers -BaseUrl $baseUrl -DeviceId $state.device_id -Label "core-restart"

# ---------------------------------------------------------------- step 6: full verification

Write-Host ""
Write-Host "=== step 6: qualification report (elevated, so the audit is readable) ===" -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "verify-device-service.ps1")

Write-Host ""
Write-Host "E2E results: service-restart=$e2e1 core-restart=$e2e2"
if (-not ($e2e1 -and $e2e2)) {
    Write-Warning "an E2E leg did not pass; fix before rotating so the rotation is final"
    if (-not $SkipRotation) { return }
}

# --------------------------------------------------------------------- step 7: final rotation

if ($SkipRotation) {
    Write-Host "rotation skipped by flag"
    return
}

Write-Host ""
Write-Host "=== step 7: FINAL owner-credential rotation ===" -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "rotate-owner-credential.ps1")
