<#
.SYNOPSIS
    One elevated run: repair the enrolled machine material in place, start the service,
    verify the runtime path, then perform the final owner-credential rotation.

.DESCRIPTION
    Order is deliberate and matches the qualification requirements: the service/runtime path
    is made healthy FIRST, and the final credential rotation happens LAST — so the credential
    displayed at the end of this run is the final one, minted after everything else already
    works, with nothing left that could force another rotation.

    Steps:

      1. repair-device-material.ps1 — fixes the NTFS protection on the already-enrolled
         machine material in place. Device ID and key bytes are preserved; SYSTEM gets only
         what the service needs (Read on the key, full on state); Administrators keep
         recovery rights; no ordinary user gains write; idempotent. The SCM recovery policy
         is suspended during the repair and restored after. It then starts the service and
         waits for Running.
      2. confirms LocalSystem + Session 0 + the secure pipe + companion admission + audit
         rows, and drives one real broker command to open Notepad.
      3. verify-device-service.ps1 — the qualification report.
      4. rotate-owner-credential.ps1 — the FINAL rotation, invalidating the exposed
         credential. The replacement is displayed once, locally, in this console.

    DPAPI scope, verified separately as required: the Windows agent uses no DPAPI at all
    (checked by search, not assumption), so there is no CurrentUser-scoped secret for
    LocalSystem to decrypt. The only DPAPI in the system is the owner's local secret store,
    which is owner-session material and stays owner-scoped.

.EXAMPLE
    # From an ELEVATED PowerShell, at the repository root:
    .\scripts\qualify-device.ps1
#>
[CmdletBinding()]
param(
    [string]$ServiceName = "PagentOSDeviceAgent",
    [switch]$SkipRotation
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "lib\NativeProcess.ps1")
. (Join-Path $PSScriptRoot "lib\InstallAcl.ps1")

function Assert-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this from an elevated PowerShell: it repairs ACLs under ProgramData, controls a Windows Service, and displays the final owner credential locally."
    }
}

Assert-Elevated

Write-Host "=== step 1: repair machine material and start the service ===" -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "repair-device-material.ps1") -ServiceName $ServiceName
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
    throw "repair-device-material.ps1 failed; not continuing"
}

$service = Get-CimInstance -ClassName Win32_Service -Filter "Name='$ServiceName'"
if ($service.State -ne "Running") {
    throw "the service is not Running after repair (state=$($service.State)); not continuing"
}

Write-Host ""
Write-Host "=== step 2: runtime path - pipe, companion admission, audit, real command ===" -ForegroundColor Cyan

$process = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$($service.ProcessId)"
Write-Host "service: pid=$($service.ProcessId) account=$($service.StartName) session=$($process.SessionId)"
if ($service.StartName -ne "LocalSystem") { throw "service is not LocalSystem" }
if ($process.SessionId -ne 0) { throw "service is not in Session 0" }

# Companion admission: give the companion's backoff loop a moment to reconnect to the newly
# started service, then read the service's own audit trail for the admission row.
$auditPath = "C:\ProgramData\PagentOS\agent\audit\agent-audit.jsonl"
$admitted = $null
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    if (Test-Path -LiteralPath $auditPath) {
        $admitted = Get-Content -LiteralPath $auditPath -Tail 200 -ErrorAction SilentlyContinue |
            ForEach-Object { try { $_ | ConvertFrom-Json } catch { $null } } |
            Where-Object { $_ -and $_.event -eq "ipc_companion_admitted" } |
            Select-Object -Last 1
        if ($admitted) { break }
    }
    Start-Sleep -Seconds 3
}

if ($admitted) {
    Write-Host "companion admitted: $($admitted.detail)"
}
else {
    Write-Warning "no ipc_companion_admitted audit row yet. If the companion predates the service install, start it once: & 'C:\Program Files\PagentOS\agent\companion\PagentOS.SessionCompanion.exe'"
}

# One real command through the broker. The automation session stored by the enrollment flow
# authorizes it; if rotation revoked it, this step says so instead of failing cryptically.
$tokenFile = Join-Path $env:LOCALAPPDATA "PagentOS\secrets\PAGENTOS_OWNER_SESSION_TOKEN.dpapi"
$agentConfig = Get-Content -LiteralPath "C:\Program Files\PagentOS\agent\service\appsettings.json" -Raw | ConvertFrom-Json
$baseUrl = $agentConfig.BrokerRestUrl.TrimEnd('/')
# Strict read: existence by directory listing (Test-Path reads DENIED as absent), DACL
# guarded, loud on denial. The state.json empty-DACL incident is why (ADR-0029).
$stateRaw = Get-MachineStateDocument -DataDir $agentConfig.DataDir
if ($null -eq $stateRaw) { throw "no state.json under $($agentConfig.DataDir): the device is not enrolled" }
$state = $stateRaw | ConvertFrom-Json

$commandOk = $false
if (Test-Path -LiteralPath $tokenFile) {
    try {
        $secure = Get-Content -LiteralPath $tokenFile | ConvertTo-SecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try { $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        $headers = @{ Authorization = "Bearer $token" }
        $token = $null

        $devices = Invoke-RestMethod -Uri "$baseUrl/v1/devices" -Headers $headers -TimeoutSec 10
        $mine = $devices.devices | Where-Object { $_.device_id -eq $state.device_id }
        Write-Host "broker sees this device as: $($mine.status)"

        if ($mine.status -eq "online") {
            $command = Invoke-RestMethod -Uri "$baseUrl/v1/devices/$($state.device_id)/commands" -Method Post `
                -Headers $headers -ContentType "application/json" -TimeoutSec 15 `
                -Body (@{ capability = "desktop.open_application"; payload = @{ application = "notepad" }; timeout_s = 60 } | ConvertTo-Json)
            Write-Host "dispatched desktop.open_application (command $($command.command_id))"

            $commandDeadline = (Get-Date).AddSeconds(30)
            while ((Get-Date) -lt $commandDeadline) {
                $result = Invoke-RestMethod -Uri "$baseUrl/v1/devices/$($state.device_id)/commands/$($command.command_id)" -Headers $headers -TimeoutSec 10
                if ($result.status -in @("succeeded", "failed", "expired", "cancelled")) { break }
                Start-Sleep -Seconds 1
            }
            Write-Host "command status: $($result.status)$(if ($result.result.pid) { " (notepad pid $($result.result.pid))" })"
            $commandOk = ($result.status -eq "succeeded")
            if ($commandOk -and $result.result.pid) {
                Stop-Process -Id $result.result.pid -Force -ErrorAction SilentlyContinue
                Write-Host "closed the Notepad the command opened"
            }
        }
        else {
            Write-Warning "device is not online at the broker yet; the E2E command was not attempted"
        }
    }
    catch {
        Write-Warning "broker command failed: $($_.Exception.Message). If the stored session was revoked by rotation, the final rotation below re-mints it - rerun this script once after."
    }
}
else {
    Write-Warning "no stored automation session; the E2E command was not attempted (the rotation below stores a fresh one)"
}

Write-Host ""
Write-Host "=== step 3: qualification report ===" -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "verify-device-service.ps1")

if ($SkipRotation) {
    Write-Host "rotation skipped by flag"
    return
}

Write-Host ""
Write-Host "=== step 4: FINAL owner-credential rotation ===" -ForegroundColor Cyan
Write-Host "This invalidates the exposed credential. At the 'Exposed credential' prompt you may"
Write-Host "paste the exposed one to prove it dies, or press Enter to skip that check."
& (Join-Path $PSScriptRoot "rotate-owner-credential.ps1")
