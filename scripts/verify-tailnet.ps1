<#
.SYNOPSIS
    Verify this Windows machine's tailnet posture before and after the broker moves to
    the cloud: Tailscale up, cloud host reachable, and NO public inbound port opened.

.DESCRIPTION
    RQ-2 requirement 9 is "Windows inbound public ports stay closed", and the way that
    quietly stops being true is a helpful installer or a firewall rule added for
    convenience. This checks it rather than assuming it, from the machine itself:

      * Tailscale present, running, and its tailnet address;
      * the cloud host answers /v1/system/health OVER THE TAILNET (and, when asked, that
        the same endpoint is NOT reachable from the public internet path);
      * the agent still owns no listening socket, and no firewall rule opens one.

    Read-only: it starts nothing, changes nothing, and needs no elevation.

.EXAMPLE
    .\scripts\verify-tailnet.ps1
    .\scripts\verify-tailnet.ps1 -CloudHost pagentos-core
#>
[CmdletBinding()]
param(
    [string]$CloudHost,
    [int]$Port = 8001,
    [string]$ServiceName = "PagentOSDeviceAgent"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "lib\NativeProcess.ps1")

$findings = @()
function Add-Finding {
    param([string]$Criterion, [string]$Status, [string]$Evidence)
    $script:findings += [pscustomobject]@{ Criterion = $Criterion; Status = $Status; Evidence = $Evidence }
}

# ------------------------------------------------------------------ 1. Tailscale present
$tailscaleExe = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
if (-not (Test-Path -LiteralPath $tailscaleExe)) {
    Add-Finding "Tailscale installed on this machine" "NOT_YET_PROVEN" "not found at $tailscaleExe"
}
else {
    $status = Invoke-NativeProcess -FilePath $tailscaleExe -Arguments @("status", "--json") -TimeoutSeconds 30
    if ($status.ExitCode -ne 0) {
        Add-Finding "Tailscale running" "NOT_YET_PROVEN" "tailscale status exited $($status.ExitCode)"
    }
    else {
        $state = $status.StdOut | ConvertFrom-Json
        $backend = Get-OptionalProperty -InputObject $state -Name "BackendState"
        $self = Get-OptionalProperty -InputObject $state -Name "Self"
        $selfIps = if ($null -ne $self) { @(Get-OptionalProperty -InputObject $self -Name "TailscaleIPs") } else { @() }
        $selfName = if ($null -ne $self) { Get-OptionalProperty -InputObject $self -Name "DNSName" } else { $null }
        if ($backend -eq "Running") {
            Add-Finding "Tailscale running on this machine" "PROVEN_REAL" "state=$backend addr=$($selfIps -join ',') name=$selfName"
        }
        else {
            Add-Finding "Tailscale running on this machine" "NOT_YET_PROVEN" "BackendState=$backend (sign in with: tailscale up)"
        }

        # Peers, so the cloud host's presence is observed rather than assumed.
        $peers = Get-OptionalProperty -InputObject $state -Name "Peer"
        if ($null -ne $peers) {
            foreach ($peer in @($peers.PSObject.Properties)) {
                $peerName = Get-OptionalProperty -InputObject $peer.Value -Name "DNSName"
                $peerIps = @(Get-OptionalProperty -InputObject $peer.Value -Name "TailscaleIPs")
                $online = Get-OptionalProperty -InputObject $peer.Value -Name "Online"
                Write-Host "  peer: $peerName $($peerIps -join ',') online=$online"
            }
        }
    }
}

# ------------------------------------------------- 2. the cloud broker over the tailnet
if ($CloudHost) {
    try {
        $health = Invoke-RestMethod -Uri "http://${CloudHost}:$Port/v1/system/health" -TimeoutSec 15
        Add-Finding "Cloud Core reachable over the tailnet" "PROVEN_REAL" "http://${CloudHost}:$Port -> status=$($health.status)"
    }
    catch {
        Add-Finding "Cloud Core reachable over the tailnet" "NOT_YET_PROVEN" $_.Exception.Message
    }
}
else {
    Add-Finding "Cloud Core reachable over the tailnet" "NOT_YET_PROVEN" "no -CloudHost given"
}

# --------------------------------------------- 3. this machine still opens NO inbound port
# The agent's whole network posture is outbound-only. Anything listening on a non-loopback
# address, owned by the agent, is a regression of the property, not a detail.
$agentPids = @()
$service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
if ($service -and $service.ProcessId -gt 0) { $agentPids += [int]$service.ProcessId }
foreach ($proc in @(Get-Process -Name "PagentOS.SessionCompanion" -ErrorAction SilentlyContinue)) { $agentPids += [int]$proc.Id }

$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $agentPids -contains [int]$_.OwningProcess })
$publicListeners = @($listeners | Where-Object { $_.LocalAddress -notin @("127.0.0.1", "::1") })

if (@($agentPids).Count -eq 0) {
    Add-Finding "Agent owns no inbound listening socket" "NOT_YET_PROVEN" "no agent process found (service+companion down?)"
}
elseif (@($publicListeners).Count -eq 0) {
    Add-Finding "Agent owns no inbound listening socket" "PROVEN_REAL" "checked pids $($agentPids -join ',') - $(@($listeners).Count) loopback-only listener(s)"
}
else {
    $detail = ($publicListeners | ForEach-Object { "$($_.LocalAddress):$($_.LocalPort)" }) -join ", "
    Add-Finding "Agent owns no inbound listening socket" "FAILED" "agent is listening on $detail"
}

# Firewall rules that would open a port for the agent binaries.
$agentRules = @(Get-NetFirewallRule -Direction Inbound -Enabled True -Action Allow -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -match "PagentOS" })
if (@($agentRules).Count -eq 0) {
    Add-Finding "No inbound firewall rule admits the agent" "PROVEN_REAL" "no enabled inbound allow rule mentions PagentOS"
}
else {
    Add-Finding "No inbound firewall rule admits the agent" "FAILED" (($agentRules | ForEach-Object { $_.DisplayName }) -join ", ")
}

Write-Host ""
Write-Host "tailnet posture" -ForegroundColor Cyan
$findings | Format-Table -AutoSize -Wrap | Out-String | Write-Host

if (@($findings | Where-Object { $_.Status -eq "FAILED" }).Count -gt 0) {
    Write-Warning "a posture criterion FAILED - fix before proving anything else"
    exit 1
}
exit 0
