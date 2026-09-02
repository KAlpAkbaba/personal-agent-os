<#
.SYNOPSIS
    RQ-2 headline proof and recovery matrix against the REAL Hetzner Cloud Core:

        Hetzner Cloud Core -> Tailscale -> Windows DeviceService (LocalSystem, Session 0)
        -> Session Companion (Session 1) -> desktop.open_application -> REAL Notepad
        -> ACK + audit

    then recovery, without owner intervention, from: Cloud Core process restart, VPS
    reboot, Tailscale disconnect/reconnect, Windows-side network loss, DeviceService
    restart.

.DESCRIPTION
    Every command in this script is POSTed to the cloud broker on the tailnet and its
    result is read back from the SAME broker, so "the command originated from the Hetzner
    Cloud Core" is a property of where the bytes went, not a claim. Each scenario is:

        disrupt -> wait for the device to be ONLINE at the cloud broker again (bounded)
                -> one real command -> assert succeeded, real Notepad pid alive, closed
                -> row in the matrix

    Nothing here is marked PROVEN_REAL by anything but that sequence completing. A scenario
    that does not recover within its budget is written up as such and the run continues,
    so one failure never hides the others.

    Elevation is needed for two scenarios only (DeviceService restart, and reading the
    agent's audit log under ProgramData); the rest run over Tailscale SSH against the host.

.EXAMPLE
    # ELEVATED PowerShell at the repository root, after migrate-agent-to-cloud.ps1:
    .\scripts\cloud\qualify-cloud.ps1 -BrokerHost 100.90.158.26
    .\scripts\cloud\qualify-cloud.ps1 -BrokerHost 100.90.158.26 -Scenarios baseline,core-restart
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BrokerHost,
    [int]$Port = 8001,
    [string]$CloudUser = "root",
    [string]$ServiceName = "PagentOSDeviceAgent",
    [string]$InstallRoot = (Join-Path $env:ProgramFiles "PagentOS\agent"),
    [string]$DataDir = (Join-Path $env:ProgramData "PagentOS\agent"),
    [string]$SessionTokenFile = (Join-Path $env:LOCALAPPDATA "PagentOS\secrets\PAGENTOS_CLOUD_SESSION_TOKEN.dpapi"),
    # "audit-only": ONE harmless real command, correlated end-to-end to its persisted audit
    # rows by command_id + trace_id. No disruption of anything. This is the re-run for the
    # single matrix row the first qualification left open.
    [ValidateSet("baseline", "audit-only", "core-restart", "vps-reboot", "tailscale-reconnect", "windows-netloss", "service-restart")]
    [string[]]$Scenarios = @("baseline", "core-restart", "vps-reboot", "tailscale-reconnect", "windows-netloss", "service-restart"),
    [int]$OnlineTimeoutSeconds = 240,
    [int]$RebootTimeoutSeconds = 420
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "..\lib\NativeProcess.ps1")
. (Join-Path $PSScriptRoot "..\lib\InstallAcl.ps1")
. (Join-Path $PSScriptRoot "..\lib\AgentAudit.ps1")

$ssh = Join-Path $env:SystemRoot "System32\OpenSSH\ssh.exe"
$tailscaleExe = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
$baseUrl = "http://${BrokerHost}:$Port"
$results = New-Object System.Collections.ArrayList

function Assert-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this from an ELEVATED PowerShell: the DeviceService restart scenario and the audit log need it."
    }
}

function Add-Result {
    param([string]$Scenario, [string]$Status, [string]$Evidence)
    [void]$results.Add([pscustomobject]@{ Scenario = $Scenario; Status = $Status; Evidence = $Evidence })
    $colour = switch ($Status) { "PROVEN_REAL" { "Green" } "NOT_YET_PROVEN" { "Yellow" } default { "Red" } }
    Write-Host ("  [{0}] {1} - {2}" -f $Status, $Scenario, $Evidence) -ForegroundColor $colour
}

function Get-StoredSessionHeaders {
    if (-not (Test-Path -LiteralPath $SessionTokenFile)) {
        throw "no stored cloud session at $SessionTokenFile - run migrate-agent-to-cloud.ps1 first (it mints and stores one)."
    }
    $secure = Get-Content -LiteralPath $SessionTokenFile | ConvertTo-SecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    $headers = @{ Authorization = "Bearer $token" }
    $token = $null
    return $headers
}

function Invoke-Remote {
    param([string]$Command, [int]$TimeoutSeconds = 120)
    return Invoke-NativeProcess -FilePath $ssh -Arguments @(
        "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20", "-o", "BatchMode=yes",
        "${CloudUser}@${BrokerHost}", $Command
    ) -TimeoutSeconds $TimeoutSeconds -SuccessExitCodes @(0, 1, 2, 255)
}

function Wait-CloudHealth {
    param([int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $h = Invoke-RestMethod -Uri "$baseUrl/v1/system/health" -TimeoutSec 8
            if ($h.status -in @("ok", "degraded")) { return $true }
        }
        catch { }
        Start-Sleep -Seconds 5
    }
    return $false
}

function Wait-DeviceOnline {
    param([hashtable]$Headers, [string]$DeviceId, [int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $devices = Invoke-RestMethod -Uri "$baseUrl/v1/devices" -Headers $Headers -TimeoutSec 10
            $mine = @($devices.devices | Where-Object { $_.device_id -eq $DeviceId })
            if (@($mine).Count -gt 0 -and $mine[0].status -eq "online") { return $true }
        }
        catch { }
        Start-Sleep -Seconds 3
    }
    return $false
}

function Invoke-CloudNotepad {
    <#
    .SYNOPSIS
        One real command from the CLOUD broker to the Windows device; returns evidence text
        or throws with the reason.
    #>
    param([hashtable]$Headers, [string]$DeviceId, [string]$Label)

    $command = Invoke-RestMethod -Uri "$baseUrl/v1/devices/$DeviceId/commands" -Method Post `
        -Headers $Headers -ContentType "application/json" -TimeoutSec 15 `
        -Body (@{ capability = "desktop.open_application"; payload = @{ application = "notepad" }; timeout_s = 60 } | ConvertTo-Json)

    $result = $null
    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline) {
        $result = Invoke-RestMethod -Uri "$baseUrl/v1/devices/$DeviceId/commands/$($command.command_id)" -Headers $Headers -TimeoutSec 10
        if ($result.status -in @("succeeded", "failed", "expired", "cancelled")) { break }
        Start-Sleep -Milliseconds 500
    }
    if ($null -eq $result -or $result.status -ne "succeeded") {
        throw "[$Label] command $($command.command_id) ended as '$(if ($result) { $result.status } else { 'no result' })'"
    }

    $resultPid = Get-OptionalProperty -InputObject $result.result -Name "pid"
    if (-not $resultPid) { throw "[$Label] succeeded but reported no pid - the real-process proof is missing" }
    $proc = Get-Process -Id $resultPid -ErrorAction SilentlyContinue
    if (-not $proc) { throw "[$Label] reported pid $resultPid but no such process is alive" }
    $evidence = "cmd=$($command.command_id) trace=$($result.trace_id) -> '$($proc.ProcessName)' pid $($proc.Id) session $($proc.SessionId), ACK from $baseUrl"
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    # The ACK's identity travels with the evidence so the audit trail can be correlated to
    # THIS command, not to "some command".
    return [pscustomobject]@{
        Evidence  = $evidence
        CommandId = [string]$command.command_id
        TraceId   = [string](Get-OptionalProperty -InputObject $result -Name "trace_id")
        Pid       = [int]$proc.Id
    }
}

function Test-AgentAuditForCommand {
    <#
    .SYNOPSIS
        The agent's own audit trail must hold the received + ack rows for EXACTLY the command
        the broker acknowledged. Schema-true (ts/event/command_id/trace_id) via AgentAudit.ps1.
    #>
    param([string]$AuditPath, [string]$CommandId, [string]$TraceId, [datetime]$Since)
    # The dispatcher appends the ack row after replying to the broker; give the disk a moment.
    $deadline = (Get-Date).AddSeconds(15)
    do {
        $report = Find-AgentAuditCommandRows -AuditPath $AuditPath -CommandId $CommandId -TraceId $TraceId -Since $Since
        if ($report.Proven) { return $report }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    return $report
}

function Format-AuditReport {
    param($Report)
    if (-not $Report.Exists) { return "audit file absent: $($Report.Path)" }
    $svc = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
    $comp = Get-Process -Name "PagentOS.SessionCompanion" -ErrorAction SilentlyContinue | Select-Object -First 1
    $tsList = @($Report.Rows | ForEach-Object { Get-OptionalProperty -InputObject $_ -Name "ts" }) -join ", "
    return ("command_id=$($Report.CommandId) trace_id=$($Report.TraceId) events=[$($Report.Events -join ',')] ts=[$tsList] " +
            "service pid=$(if ($svc) { $svc.ProcessId } else { '?' }) companion pid=$(if ($comp) { $comp.Id } else { '?' }) " +
            "file=$($Report.Path) rows=$($Report.TotalRows) unparseable=$($Report.Unparseable) stale=$($Report.StaleRows) traceMismatch=$($Report.TraceMismatch)")
}

function Invoke-Scenario {
    param([string]$Name, [scriptblock]$Disrupt, [int]$RecoverBudget, [hashtable]$Headers, [string]$DeviceId, [string]$Extra = "")
    Write-Host ""
    Write-Host "=== scenario: $Name ===" -ForegroundColor Cyan
    $started = Get-Date
    try {
        & $Disrupt
        if (-not (Wait-CloudHealth -TimeoutSeconds $RecoverBudget)) { throw "cloud broker did not answer health within ${RecoverBudget}s" }
        if (-not (Wait-DeviceOnline -Headers $Headers -DeviceId $DeviceId -TimeoutSeconds $RecoverBudget)) {
            throw "device did not come back ONLINE at the cloud broker within ${RecoverBudget}s"
        }
        $recovered = [int]((Get-Date) - $started).TotalSeconds
        $ack = Invoke-CloudNotepad -Headers $Headers -DeviceId $DeviceId -Label $Name
        Add-Result -Scenario $Name -Status "PROVEN_REAL" -Evidence "recovered in ${recovered}s, no owner action; $($ack.Evidence)$Extra"
    }
    catch {
        Add-Result -Scenario $Name -Status "NOT_YET_PROVEN" -Evidence $_.Exception.Message
    }
}

# ---------------------------------------------------------------------------- preflight
Assert-Elevated
Write-Host "=== preflight ===" -ForegroundColor Cyan
$headers = Get-StoredSessionHeaders
$health = Invoke-RestMethod -Uri "$baseUrl/v1/system/health" -TimeoutSec 15
Write-Host "  cloud  : $baseUrl status=$($health.status)"

$config = [System.IO.File]::ReadAllText((Join-Path $InstallRoot "service\appsettings.json")) | ConvertFrom-Json
if ($config.BrokerRestUrl -ne $baseUrl) {
    throw "the installed agent dials $($config.BrokerRestUrl), not $baseUrl - run migrate-agent-to-cloud.ps1 first"
}
$stateRaw = Get-MachineStateDocument -DataDir $DataDir
if ($null -eq $stateRaw) { throw "no enrolment state under $DataDir" }
$deviceId = ($stateRaw | ConvertFrom-Json).device_id
Write-Host "  device : $deviceId (agent config points at the cloud broker)"

$tailnetBefore = ""
if (Test-Path -LiteralPath $tailscaleExe) {
    $ts = Invoke-NativeProcess -FilePath $tailscaleExe -Arguments @("status") -TimeoutSeconds 30 -SuccessExitCodes @(0, 1)
    $tailnetBefore = (@($ts.StdOut -split "`n" | Where-Object { $_ -match "pagentos-core" })[0] -split "\s+")[0]
    Write-Host "  tailnet: pagentos-core at $tailnetBefore"
}
$auditPath = Join-Path $DataDir "audit\agent-audit.jsonl"
$runStart = (Get-Date).ToUniversalTime()

# ------------------------------------------------------------------- baseline headline
if ("baseline" -in $Scenarios) {
    Write-Host ""
    Write-Host "=== headline: Hetzner Cloud Core -> Tailscale -> DeviceService -> Companion -> real Notepad -> ACK ===" -ForegroundColor Cyan
    try {
        if (-not (Wait-DeviceOnline -Headers $headers -DeviceId $deviceId -TimeoutSeconds $OnlineTimeoutSeconds)) {
            throw "device is not ONLINE at the cloud broker (waited ${OnlineTimeoutSeconds}s)"
        }
        $ack = Invoke-CloudNotepad -Headers $headers -DeviceId $deviceId -Label "baseline"
        Add-Result -Scenario "baseline: cloud -> real Notepad -> ACK" -Status "PROVEN_REAL" -Evidence $ack.Evidence
        $report = Test-AgentAuditForCommand -AuditPath $auditPath -CommandId $ack.CommandId -TraceId $ack.TraceId -Since $runStart
        Add-Result -Scenario "baseline: agent audit trail records the command" -Status $(if ($report.Proven) { "PROVEN_REAL" } else { "NOT_YET_PROVEN" }) `
            -Evidence (Format-AuditReport -Report $report)
    }
    catch {
        Add-Result -Scenario "baseline: cloud -> real Notepad -> ACK" -Status "NOT_YET_PROVEN" -Evidence $_.Exception.Message
    }
}

# ---------------------------------------------------- audit-only: one command, correlated
if ("audit-only" -in $Scenarios) {
    Write-Host ""
    Write-Host "=== audit-only: one harmless command, correlated to its persisted audit rows ===" -ForegroundColor Cyan
    try {
        if (-not (Wait-DeviceOnline -Headers $headers -DeviceId $deviceId -TimeoutSeconds $OnlineTimeoutSeconds)) {
            throw "device is not ONLINE at the cloud broker (waited ${OnlineTimeoutSeconds}s)"
        }
        $ack = Invoke-CloudNotepad -Headers $headers -DeviceId $deviceId -Label "audit-only"
        Write-Host "  ACK: $($ack.Evidence)"
        $report = Test-AgentAuditForCommand -AuditPath $auditPath -CommandId $ack.CommandId -TraceId $ack.TraceId -Since $runStart
        Add-Result -Scenario "baseline: agent audit trail records the command" -Status $(if ($report.Proven) { "PROVEN_REAL" } else { "NOT_YET_PROVEN" }) `
            -Evidence (Format-AuditReport -Report $report)
        if ($report.Proven) {
            Write-Host ""
            Write-Host "  persisted rows for this command:" -ForegroundColor DarkCyan
            foreach ($row in $report.Rows) { Write-Host "    $($row | ConvertTo-Json -Compress)" }
        }
    }
    catch {
        Add-Result -Scenario "baseline: agent audit trail records the command" -Status "NOT_YET_PROVEN" -Evidence $_.Exception.Message
    }
}

# ------------------------------------------------------------------------- recovery
if ("core-restart" -in $Scenarios) {
    Invoke-Scenario -Name "Cloud Core process restart" -Headers $headers -DeviceId $deviceId -RecoverBudget $OnlineTimeoutSeconds -Disrupt {
        $r = Invoke-Remote -Command "cd /opt/pagentos/app/infra/docker && docker compose -f docker-compose.prod.yml --env-file /opt/pagentos/.env restart api 2>&1 | tail -2" -TimeoutSeconds 180
        Write-Host "  restarted api container: $($r.StdOut.Trim())"
    }
}

if ("vps-reboot" -in $Scenarios) {
    Invoke-Scenario -Name "Hetzner VPS reboot" -Headers $headers -DeviceId $deviceId -RecoverBudget $RebootTimeoutSeconds -Disrupt {
        [void](Invoke-Remote -Command "systemctl reboot" -TimeoutSeconds 30)
        Write-Host "  reboot issued; waiting for the host to go away and come back..."
        Start-Sleep -Seconds 20
    } -Extra ""
    # Properties a reboot must preserve, checked explicitly rather than folded into "it came back".
    try {
        $post = Invoke-Remote -Command "tailscale ip -4 | head -n1; findmnt -no SOURCE /mnt/pagentos-data; docker exec pagentos-prod-postgres psql -U pagentos -d pagentos_prod -tAc 'SELECT count(*) FROM devices'" -TimeoutSeconds 120
        $lines = @($post.StdOut -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        $ipSame = ($lines.Count -ge 1 -and $lines[0] -eq $BrokerHost)
        $onVolume = ($lines.Count -ge 2 -and $lines[1] -match "sd[b-z]")
        $rowKept = ($lines.Count -ge 3 -and [int]$lines[2] -ge 1)
        Add-Result -Scenario "VPS reboot: tailnet address unchanged" -Status $(if ($ipSame) { "PROVEN_REAL" } else { "NOT_YET_PROVEN" }) -Evidence "after reboot: $($lines[0]) (expected $BrokerHost) - non-ephemeral node"
        Add-Result -Scenario "VPS reboot: data volume re-mounted from fstab" -Status $(if ($onVolume) { "PROVEN_REAL" } else { "NOT_YET_PROVEN" }) -Evidence "/mnt/pagentos-data on $($lines[1])"
        Add-Result -Scenario "VPS reboot: device registration survived" -Status $(if ($rowKept) { "PROVEN_REAL" } else { "NOT_YET_PROVEN" }) -Evidence "devices rows=$($lines[2]) in pagentos_prod"
    }
    catch {
        Add-Result -Scenario "VPS reboot: post-reboot properties" -Status "NOT_YET_PROVEN" -Evidence $_.Exception.Message
    }
}

if ("tailscale-reconnect" -in $Scenarios) {
    Invoke-Scenario -Name "Tailscale disconnect/reconnect (host side)" -Headers $headers -DeviceId $deviceId -RecoverBudget $OnlineTimeoutSeconds -Disrupt {
        # Detached on the host: dropping the tailnet also drops THIS ssh session.
        # `tailscale up` with NO flags: it restores the saved prefs (RunSSH, CorpDNS, the
        # pagentos-core hostname). Passing some flags but not all is refused by Tailscale
        # ("requires mentioning all non-default flags") - checked against the real host
        # before this ran, where exactly that refusal appeared.
        [void](Invoke-Remote -Command "nohup sh -c 'tailscale down; sleep 25; tailscale up' >/dev/null 2>&1 &" -TimeoutSeconds 30)
        Write-Host "  tailnet dropped on the host for ~25s"
        Start-Sleep -Seconds 30
    }
}

if ("windows-netloss" -in $Scenarios) {
    Invoke-Scenario -Name "Windows-side network loss (tailnet down/up on this PC)" -Headers $headers -DeviceId $deviceId -RecoverBudget $OnlineTimeoutSeconds -Disrupt {
        if (-not (Test-Path -LiteralPath $tailscaleExe)) { throw "Tailscale CLI not found on this PC" }
        $down = Invoke-NativeProcess -FilePath $tailscaleExe -Arguments @("down") -TimeoutSeconds 60 -SuccessExitCodes @(0, 1)
        Write-Host "  tailscale down on this PC (exit $($down.ExitCode)); holding 30s"
        Start-Sleep -Seconds 30
        $up = Invoke-NativeProcess -FilePath $tailscaleExe -Arguments @("up") -TimeoutSeconds 120 -SuccessExitCodes @(0, 1)
        Write-Host "  tailscale up on this PC (exit $($up.ExitCode))"
        if ($up.ExitCode -ne 0) { throw "tailscale up failed on this PC: $($up.StdErr.Trim())" }
    }
}

if ("service-restart" -in $Scenarios) {
    Invoke-Scenario -Name "Windows DeviceService restart" -Headers $headers -DeviceId $deviceId -RecoverBudget $OnlineTimeoutSeconds -Disrupt {
        Restart-Service -Name $ServiceName -Force
        (Get-Service -Name $ServiceName).WaitForStatus("Running", (New-TimeSpan -Seconds 45))
        $svc = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
        Write-Host "  service restarted: pid=$($svc.ProcessId) account=$($svc.StartName)"
    }
}

# ----------------------------------------------------------------------------- matrix
Write-Host ""
Write-Host "=== RQ-2 cloud qualification matrix ===" -ForegroundColor Cyan
$results | Format-Table -AutoSize -Wrap | Out-String | Write-Host
$failed = @($results | Where-Object { $_.Status -ne "PROVEN_REAL" })
if (@($failed).Count -gt 0) {
    Write-Warning "$(@($failed).Count) row(s) not proven; the milestone stays open."
    exit 1
}
Write-Host "every scenario PROVEN_REAL against the actual Hetzner host." -ForegroundColor Green
exit 0
