<#
.SYNOPSIS
    Point the installed Windows agent at a new broker endpoint (loopback -> tailnet)
    transactionally, WITHOUT reinstalling, re-enrolling or touching the device identity.

.DESCRIPTION
    RQ-2: the Cloud Core moves from 127.0.0.1 to the Hetzner host's Tailscale address.
    Cryptographically nothing changes - the device keeps its id and P-256 key, and the cloud
    database already holds its registration - so the only change on this machine is the
    broker URL in the service's appsettings.json. That change is made as a transaction:

        probe the new endpoint     - refuse to switch to a dead broker
      -> read the current config   - and refuse a file that is not the installed agent's
      -> stage the updated config  - next to the live file, same volume
      -> validate                  - JSON, runtime identity preserved, endpoints well-formed
      -> stop the runtime          - companion, then service; wait for the PID to exit
      -> atomic replace            - File.Replace with a REAL backup on the same volume
      -> verify the ACL            - the service must be able to read what it will load
      -> start the runtime         - service, then companion
      -> verify                    - service + companion + pipe, and the cloud still answers
      -> commit                    - the backup stays as appsettings.previous.json

    Any failure after the replace rolls the previous configuration back (atomically, from
    that backup), restarts the runtime, re-verifies it, and only then reports the error.
    A journal next to the config records each phase, so an interrupted run can be read
    instead of guessed at.

    The first real attempt died inside File.Replace because PowerShell binds `$null` to a
    [string] parameter as an empty string, which .NET refuses as a path. The primitive now
    lives in scripts/lib/ConfigSwap.ps1, never passes an empty backup, and has its own
    Windows PowerShell 5.1 tests for Program Files-style paths, stale staging files,
    existing backups, rollback and idempotence.

.EXAMPLE
    # From an ELEVATED PowerShell after the cloud registration:
    .\scripts\switch-agent-broker.ps1 -BrokerHost 100.90.158.26
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
. (Join-Path $PSScriptRoot "lib\ConfigSwap.ps1")

# Intent captured once, under a name no result object will ever want (the [switch]
# collision class that broke provisioning: `$rollback = <object>` would bind to this parameter).
$shouldRollback = [bool]$Rollback

function Assert-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this from an elevated PowerShell: it rewrites the service configuration and restarts the service."
    }
}

function Write-SwitchPhase {
    <#  Phase journal beside the config: what happened, in order, survives a crash.  #>
    param([string]$JournalPath, [string]$Phase, [string]$Detail = "")
    $entry = [pscustomobject]@{ phase = $Phase; at = (Get-Date).ToUniversalTime().ToString("o"); detail = $Detail }
    $existing = @()
    if (Test-Path -LiteralPath $JournalPath) {
        try { $existing = @([System.IO.File]::ReadAllText($JournalPath) | ConvertFrom-Json) } catch { $existing = @() }
    }
    $existing += $entry
    [System.IO.File]::WriteAllText($JournalPath, (ConvertTo-Json -InputObject @($existing) -Depth 4), (New-Object System.Text.UTF8Encoding($false)))
}

function Stop-AgentRuntime {
    param([string]$ServiceName)
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
            # SCM "Stopped" precedes process exit; wait for the PID itself so the old process
            # cannot still be reading the file we are about to replace.
            $deadline = (Get-Date).AddSeconds(30)
            while ((Get-Date) -lt $deadline -and (Get-Process -Id $servicePid -ErrorAction SilentlyContinue)) {
                Start-Sleep -Milliseconds 250
            }
        }
    }
}

function Start-AgentRuntime {
    param([string]$ServiceName, [string]$InstallRoot)
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
    param([string]$ServiceName, [string]$ConfigPath, [int]$TimeoutSeconds = 30)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
        if ($service -and $service.State -eq "Running" -and
            (Get-Process -Name "PagentOS.SessionCompanion" -ErrorAction SilentlyContinue)) {
            $config = [System.IO.File]::ReadAllText($ConfigPath) | ConvertFrom-Json
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
$journalPath = Join-Path $InstallRoot "service\.broker-switch-journal.json"
if (-not (Test-Path -LiteralPath $configPath)) { throw "no installed agent configuration at $configPath" }

# --------------------------------------------------------------------------- rollback
if ($shouldRollback) {
    if (-not (Test-Path -LiteralPath $backupPath)) { throw "nothing to roll back to: $backupPath does not exist" }
    Write-Host "=== rollback to the previous broker configuration ===" -ForegroundColor Yellow
    Write-SwitchPhase -JournalPath $journalPath -Phase "rollback_requested"
    Stop-AgentRuntime -ServiceName $ServiceName
    $restored = Restore-ConfigFromBackup -ConfigPath $configPath -BackupPath $backupPath
    Write-SwitchPhase -JournalPath $journalPath -Phase "rollback_replaced" -Detail "failed config kept at $($restored.BackupPath)"
    Start-AgentRuntime -ServiceName $ServiceName -InstallRoot $InstallRoot
    if (-not (Test-AgentRuntimeHealth -ServiceName $ServiceName -ConfigPath $configPath)) {
        Write-SwitchPhase -JournalPath $journalPath -Phase "rollback_unhealthy"
        throw "runtime did not come back healthy after rollback - diagnose before anything else"
    }
    Write-SwitchPhase -JournalPath $journalPath -Phase "rollback_committed"
    $now = [System.IO.File]::ReadAllText($configPath) | ConvertFrom-Json
    Write-Host "rolled back; runtime healthy (service + companion + pipe); agent dials $($now.BrokerRestUrl)" -ForegroundColor Green
    return
}

if ([string]::IsNullOrWhiteSpace($BrokerHost)) { throw "pass -BrokerHost (the Cloud Core's Tailscale address) or -Rollback" }
$restUrl = "http://${BrokerHost}:$Port"

# ------------------------------------------------------------------- 1. probe the target
Write-Host "=== 1. the new broker answers ===" -ForegroundColor Cyan
$health = Invoke-RestMethod -Uri "$restUrl/v1/system/health" -TimeoutSec 15
Write-Host "  $restUrl -> status=$($health.status)"
Write-SwitchPhase -JournalPath $journalPath -Phase "probed" -Detail $restUrl

# ------------------------------------------------------------- 2. read + build + compare
Write-Host ""
Write-Host "=== 2. staging the updated configuration ===" -ForegroundColor Cyan
$currentContent = [System.IO.File]::ReadAllText($configPath)
$currentConfig = $currentContent | ConvertFrom-Json
$newContent = New-AgentBrokerConfigContent -CurrentContent $currentContent -BrokerHost $BrokerHost -Port $Port
Write-Host "  $($currentConfig.BrokerRestUrl) -> $restUrl"
Write-Host "  DataDir, PipeName, CompanionSid, CompanionImagePath: preserved verbatim"

$alreadyThere = [string]::Equals($currentContent, $newContent, [StringComparison]::Ordinal) -or ($currentConfig.BrokerRestUrl -eq $restUrl)

# --------------------------------------- 3..8 the transaction, with rollback on any failure
$replaced = $false
try {
    if ($alreadyThere) {
        Write-Host "  configuration already points at $restUrl - nothing to replace; verifying the runtime only"
        Write-SwitchPhase -JournalPath $journalPath -Phase "noop" -Detail $restUrl
    }
    else {
        Write-Host ""
        Write-Host "=== 3. stopping the runtime ===" -ForegroundColor Cyan
        Stop-AgentRuntime -ServiceName $ServiceName
        Write-SwitchPhase -JournalPath $journalPath -Phase "runtime_stopped"

        Write-Host "=== 4. atomic replace (real backup, same volume) ===" -ForegroundColor Cyan
        $validator = { param($stagedPath) Test-AgentBrokerConfig -StagedPath $stagedPath -ExpectedRestUrl $restUrl }
        $outcome = Invoke-AtomicConfigReplace -ConfigPath $configPath -NewContent $newContent -BackupPath $backupPath -Validate $validator
        $replaced = [bool]$outcome.Replaced
        Write-SwitchPhase -JournalPath $journalPath -Phase "replaced" -Detail "backup=$($outcome.BackupPath)"
        $aclReport = Test-ConfigFileAcl -Path $configPath
        Write-Host "  live file: replaced=$replaced, DACL rules=$($aclReport.RuleCount), SYSTEM can read=$($aclReport.SystemRead)"
        Write-SwitchPhase -JournalPath $journalPath -Phase "acl_verified"

        Write-Host "=== 5. starting the runtime ===" -ForegroundColor Cyan
        Start-AgentRuntime -ServiceName $ServiceName -InstallRoot $InstallRoot
        Write-SwitchPhase -JournalPath $journalPath -Phase "runtime_started"
    }

    Write-Host ""
    Write-Host "=== 6. verification ===" -ForegroundColor Cyan
    if (-not (Test-AgentRuntimeHealth -ServiceName $ServiceName -ConfigPath $configPath)) {
        throw "runtime not healthy (service + companion + pipe) after the switch"
    }
    Write-Host "  runtime: service Running, companion up, pipe live"
    $again = Invoke-RestMethod -Uri "$restUrl/v1/system/health" -TimeoutSec 15
    Write-Host "  cloud  : $restUrl -> status=$($again.status)"
    $liveConfig = [System.IO.File]::ReadAllText($configPath) | ConvertFrom-Json
    if ($liveConfig.BrokerRestUrl -ne $restUrl) { throw "the live configuration does not point at $restUrl after the switch" }
    Write-SwitchPhase -JournalPath $journalPath -Phase "committed" -Detail $restUrl
}
catch {
    $reason = $_.Exception.Message
    Write-SwitchPhase -JournalPath $journalPath -Phase "failed" -Detail $reason
    if ($replaced) {
        Write-Warning "switch failed after the replace ($reason); rolling back to the previous configuration"
        try {
            Stop-AgentRuntime -ServiceName $ServiceName
            [void](Restore-ConfigFromBackup -ConfigPath $configPath -BackupPath $backupPath)
            Start-AgentRuntime -ServiceName $ServiceName -InstallRoot $InstallRoot
            $healthyAgain = Test-AgentRuntimeHealth -ServiceName $ServiceName -ConfigPath $configPath
            Write-SwitchPhase -JournalPath $journalPath -Phase "rolled_back" -Detail "healthy=$healthyAgain"
            Write-Warning "rolled back; runtime healthy=$healthyAgain"
        }
        catch {
            Write-SwitchPhase -JournalPath $journalPath -Phase "rollback_failed" -Detail $_.Exception.Message
            Write-Warning "ROLLBACK FAILED: $($_.Exception.Message). Restore manually: .\scripts\switch-agent-broker.ps1 -Rollback"
        }
    }
    else {
        # Nothing went live. If the runtime was stopped, bring it back on the unchanged config.
        try { Start-AgentRuntime -ServiceName $ServiceName -InstallRoot $InstallRoot } catch { }
    }
    throw "broker switch failed: $reason"
}

Write-Host ""
Write-Host "switched. Runtime healthy; the agent now dials $restUrl. Previous config kept at $backupPath" -ForegroundColor Green
Write-Host "Rollback at any time:  .\scripts\switch-agent-broker.ps1 -Rollback"
