<#
.SYNOPSIS
    Point the installed Windows agent at a new broker endpoint (loopback -> tailnet)
    WITHOUT reinstalling, re-enrolling or touching the device identity.

.DESCRIPTION
    RQ-2: the Cloud Core moves from 127.0.0.1 to the Hetzner host's Tailscale address.
    Cryptographically nothing changes — the device keeps its id and P-256 key, and the
    cloud database gets the existing registration via scripts/cloud/restore-device-row.sh
    — so the ONLY change on this machine is the broker URL in the service's
    appsettings.json, applied atomically and followed by the same symmetric
    restart-and-verify the deployment engine uses.

    Refuses to switch unless the new endpoint actually answers health over the tailnet
    first: a config pointing at a dead endpoint would take a qualified runtime down for
    nothing. The old configuration is kept next to the live one for one-command rollback.

.EXAMPLE
    # From an ELEVATED PowerShell after the VPS deployment and device-row restore:
    .\scripts\switch-agent-broker.ps1 -BrokerHost 100.64.0.1
    .\scripts\switch-agent-broker.ps1 -Rollback   # restore the previous configuration
#>
[CmdletBinding()]
param(
    [string]$BrokerHost,
    [int]$Port = 8001,
    [string]$ServiceName = "PagentOSDeviceAgent",
    [string]$InstallRoot = (Join-Path $env:ProgramFiles "PagentOS\agent"),
    [switch]$Rollback
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "lib\NativeProcess.ps1")

function Assert-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this from an elevated PowerShell: it rewrites the service configuration and restarts the service."
    }
}

function Restart-AgentRuntime {
    <#  Symmetric: stop companion, stop service (wait for the PID), start service, start companion.  #>
    param([string]$ServiceName, [string]$InstallRoot)

    $companion = Get-Process -Name "PagentOS.SessionCompanion" -ErrorAction SilentlyContinue
    if ($companion) {
        $companion | Stop-Process -Force
        foreach ($proc in @($companion)) { try { [void]$proc.WaitForExit(15000) } catch { } }
    }
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($service -and $service.Status -ne "Stopped") {
        $servicePid = (Get-CimInstance Win32_Service -Filter "Name='$ServiceName'").ProcessId
        Stop-Service -Name $ServiceName -Force
        (Get-Service -Name $ServiceName).WaitForStatus("Stopped", (New-TimeSpan -Seconds 45))
        if ($servicePid -gt 0) {
            $deadline = (Get-Date).AddSeconds(30)
            while ((Get-Date) -lt $deadline -and (Get-Process -Id $servicePid -ErrorAction SilentlyContinue)) {
                Start-Sleep -Milliseconds 250
            }
        }
    }

    Start-Service -Name $ServiceName
    (Get-Service -Name $ServiceName).WaitForStatus("Running", (New-TimeSpan -Seconds 45))
    & (Join-Path $env:SystemRoot "System32\schtasks.exe") /Run /TN "PagentOS Session Companion" | Out-Null
    Start-Sleep -Seconds 3
    if (-not (Get-Process -Name "PagentOS.SessionCompanion" -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath (Join-Path $InstallRoot "companion\PagentOS.SessionCompanion.exe") -WindowStyle Hidden
    }
}

function Test-AgentRuntimeHealth {
    <#  Running is not health: service AND companion AND the pipe in the namespace.  #>
    param([string]$ServiceName, [string]$InstallRoot, [int]$TimeoutSeconds = 30)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
        if ($service -and $service.State -eq "Running" -and
            (Get-Process -Name "PagentOS.SessionCompanion" -ErrorAction SilentlyContinue)) {
            $config = Get-Content -LiteralPath (Join-Path $InstallRoot "service\appsettings.json") -Raw | ConvertFrom-Json
            $listed = @([System.IO.Directory]::GetFiles("\\.\pipe\") | Where-Object { $_ -match [regex]::Escape($config.PipeName) })
            if (@($listed).Count -ge 1) { return $true }
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    return $false
}

Assert-Elevated

$configPath = Join-Path $InstallRoot "service\appsettings.json"
$backupPath = Join-Path $InstallRoot "service\appsettings.previous.json"
if (-not (Test-Path -LiteralPath $configPath)) { throw "no installed agent configuration at $configPath" }

if ($Rollback) {
    if (-not (Test-Path -LiteralPath $backupPath)) { throw "nothing to roll back to: $backupPath does not exist" }
    Write-Host "rolling back to the previous broker configuration..."
    $restored = [System.IO.File]::ReadAllText($backupPath)
    $tempPath = "$configPath.tmp"
    [System.IO.File]::WriteAllText($tempPath, $restored, (New-Object System.Text.UTF8Encoding($false)))
    # File.Replace, not a 3-arg File.Move: the overwrite overload is .NET Core 3+ and does
    # not exist on this host's Framework - the same fails-only-at-the-call class as
    # ImportFromPem. Replace is atomic on NTFS and Framework has always had it.
    [System.IO.File]::Replace($tempPath, $configPath, $null)
    Restart-AgentRuntime -ServiceName $ServiceName -InstallRoot $InstallRoot
    if (-not (Test-AgentRuntimeHealth -ServiceName $ServiceName -InstallRoot $InstallRoot)) {
        throw "runtime did not come back healthy after rollback - diagnose before anything else"
    }
    Write-Host "rolled back; runtime healthy (service + companion + pipe)"
    return
}

if ([string]::IsNullOrWhiteSpace($BrokerHost)) { throw "pass -BrokerHost (the Cloud Core's Tailscale address) or -Rollback" }

$restUrl = "http://${BrokerHost}:$Port"
$wsUrl = "ws://${BrokerHost}:$Port/v1/devices/connect"

# Refuse to switch to an endpoint that is not answering. The health probe is also the
# tailnet-reachability proof from exactly the machine that must reach it.
Write-Host "probing $restUrl/v1/system/health over the tailnet..."
$health = Invoke-RestMethod -Uri "$restUrl/v1/system/health" -TimeoutSec 15
Write-Host "cloud broker answers: status=$($health.status)"

$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
if ($config.BrokerRestUrl -eq $restUrl) {
    Write-Host "configuration already points at $restUrl"
}
else {
    Write-Host "switching broker: $($config.BrokerRestUrl) -> $restUrl"
    # Keep the outgoing configuration for one-command rollback BEFORE touching the live file.
    [System.IO.File]::WriteAllText($backupPath, (Get-Content -LiteralPath $configPath -Raw), (New-Object System.Text.UTF8Encoding($false)))

    $config.BrokerRestUrl = $restUrl
    $config.BrokerWsUrl = $wsUrl
    $tempPath = "$configPath.tmp"
    [System.IO.File]::WriteAllText($tempPath, ($config | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
    # Atomic on NTFS, exists on .NET Framework (a 3-arg File.Move does not - the
    # ImportFromPem class again); ACL inherited from the hardened directory.
    [System.IO.File]::Replace($tempPath, $configPath, $null)
}

Restart-AgentRuntime -ServiceName $ServiceName -InstallRoot $InstallRoot
if (-not (Test-AgentRuntimeHealth -ServiceName $ServiceName -InstallRoot $InstallRoot)) {
    throw "runtime not healthy after the switch. Roll back with: .\scripts\switch-agent-broker.ps1 -Rollback"
}

Write-Host ""
Write-Host "switched. Runtime healthy (service + companion + pipe); the agent now dials $restUrl" -ForegroundColor Green
Write-Host "Prove the path end-to-end with the qualification flow before trusting it; rollback: -Rollback"
