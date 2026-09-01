<#
.SYNOPSIS
    Temporarily open SSH to this machine's public IP only, diagnose why the cloud host
    never joined the tailnet, join it interactively, and close the rule again — always.

.DESCRIPTION
    The documented break-glass path from infra/opentofu/main.tf: `ssh_admin_cidrs` is
    empty by default, which produces no public SSH rule at all. This script fills it with
    exactly one /32 for the duration of the session and empties it again in a `finally`,
    so the window is as small as the work requires.

    Deliberate properties, each because the alternative is worse:

      * the running server is NEVER touched. Only `hcloud_firewall.agent_os` is targeted,
        so no plan can involve the server — whose `user_data` is ForceNew, and would
        therefore be REPLACED (destroying the volume's attachment and the host) if the
        auth key it was built with were re-rendered differently. `prevent_destroy` is the
        second net, not the first.
      * no Tailscale auth key is needed, asked for, or stored. `tailscale up` is run
        WITHOUT `--authkey`, which prints a one-time login URL; the owner opens it in a
        browser and the node joins their existing tailnet as an ordinary, non-ephemeral,
        owner-authenticated machine. That is what the failed key was trying to achieve.
      * the Hetzner token is read from TF_VAR_hcloud_token and never printed.
      * the SSH rule is removed on every exit path, including failure.

.PARAMETER PublicIp
    Override the detected public IP (e.g. if you are behind CGNAT and know the address).

.PARAMETER KeepOpen
    Leave the SSH rule in place at the end. For deliberate multi-step debugging only; the
    script prints exactly how to close it.

.EXAMPLE
    $env:TF_VAR_hcloud_token = '...'      # never typed into chat
    .\scripts\cloud\breakglass-ssh.ps1
#>
[CmdletBinding()]
param(
    [string]$PublicIp,
    [string]$SshKeyPath = (Join-Path $env:USERPROFILE ".ssh\id_kurek"),
    [string]$AdminUser = "pagentos",
    [string]$TofuPath,
    [string]$TofuVersion = "1.10.6",
    [int]$JoinTimeoutSeconds = 600,
    [switch]$KeepOpen,

    # Shut the window and do nothing else. The safety valve: it must always be possible to
    # close the rule in one command, whatever state a previous run ended in.
    [switch]$CloseOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "..\lib\NativeProcess.ps1")

$shouldKeepOpen = [bool]$KeepOpen
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$tofuRoot = Join-Path $repoRoot "infra\opentofu"
$ssh = Join-Path $env:SystemRoot "System32\OpenSSH\ssh.exe"

function Get-TofuExecutable {
    param([string]$ExplicitPath, [string]$Version)
    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        if (-not (Test-Path -LiteralPath $ExplicitPath)) { throw "-TofuPath does not exist: $ExplicitPath" }
        return $ExplicitPath
    }
    $onPath = Get-Command tofu -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    $candidate = Join-Path $env:LOCALAPPDATA "PagentOS\tools\opentofu-$Version\tofu.exe"
    if (Test-Path -LiteralPath $candidate) { return $candidate }
    throw "OpenTofu not found. Run scripts\cloud\provision.ps1 once (it fetches a pinned copy), or pass -TofuPath."
}

function Get-PublicIpAddress {
    <#  Ask a plain-text echo service what this machine looks like from outside.  #>
    foreach ($url in @("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com")) {
        try {
            $answer = (Invoke-RestMethod -Uri $url -TimeoutSec 15).ToString().Trim()
            if ($answer -match '^\d{1,3}(\.\d{1,3}){3}$') { return $answer }
        }
        catch { continue }
    }
    throw "could not determine this machine's public IPv4. Pass it explicitly: -PublicIp <address>"
}

function Set-BreakGlassCidrs {
    <#
    .SYNOPSIS
        Apply ONLY the firewall resource with the given SSH source list.

    .DESCRIPTION
        -target is what keeps the running server out of the plan entirely. The auth-key
        variable still needs a value for OpenTofu to evaluate, but the firewall does not
        consume it, so a placeholder is correct here and no key is required to open or
        close the window.
    #>
    param([string]$Tofu, [string[]]$Cidrs, [string]$Activity)

    $rendered = "[" + (($Cidrs | ForEach-Object { '"' + $_ + '"' }) -join ",") + "]"
    if (-not $env:TF_VAR_tailscale_auth_key) {
        $env:TF_VAR_tailscale_auth_key = "placeholder-not-used-by-the-firewall"
    }
    $result = Invoke-NativeProcess -FilePath $Tofu -Arguments @(
        "apply", "-input=false", "-no-color", "-auto-approve",
        "-target=hcloud_firewall.agent_os",
        "-var", "ssh_admin_cidrs=$rendered"
    ) -WorkingDirectory $tofuRoot -TimeoutSeconds 900
    if ($result.ExitCode -ne 0) {
        Write-Host $result.StdOut
        Write-Host $result.StdErr
        throw "$Activity failed with exit code $($result.ExitCode)"
    }
    # Never let a replacement slip past unnoticed even though -target should prevent it.
    if ($result.StdOut -match "must be replaced" -or $result.StdOut -match "hcloud_server\.agent_os: Destroying") {
        throw "the plan touched the SERVER, which must never happen here. Nothing further was attempted."
    }
    Write-Host "  $Activity : done"
}

function Invoke-RemoteCommand {
    <#  One SSH command; returns the result rather than throwing, so diagnosis continues.  #>
    param([string]$HostAddress, [string]$Command, [int]$TimeoutSeconds = 120)

    return Invoke-NativeProcess -FilePath $ssh -Arguments @(
        "-i", $SshKeyPath,
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "UserKnownHostsFile=$knownHostsFile",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=15",
        "$AdminUser@$HostAddress", $Command
    ) -TimeoutSeconds $TimeoutSeconds -SuccessExitCodes @(0, 1, 2, 3, 4, 5, 255)
}

# --------------------------------------------------------------------------- preflight
Write-Host "=== preflight ===" -ForegroundColor Cyan
if ([string]::IsNullOrWhiteSpace($env:TF_VAR_hcloud_token)) {
    throw "TF_VAR_hcloud_token is not set in this shell. Set it here (it is never stored or printed) and re-run."
}
Write-Host "  TF_VAR_hcloud_token : SET"
if (-not (Test-Path -LiteralPath $SshKeyPath)) { throw "SSH private key not found: $SshKeyPath" }
if (-not (Test-Path -LiteralPath $ssh)) { throw "OpenSSH client not found at $ssh" }

$tofu = Get-TofuExecutable -ExplicitPath $TofuPath -Version $TofuVersion
$knownHostsFile = Join-Path ([System.IO.Path]::GetTempPath()) "pagentos-knownhosts-$([guid]::NewGuid().ToString('N'))"

if ($CloseOnly) {
    Write-Host ""
    Write-Host "=== closing break-glass SSH (close-only) ===" -ForegroundColor Yellow
    Set-BreakGlassCidrs -Tofu $tofu -Cidrs @() -Activity "close public SSH"
    Write-Host "public SSH is closed; administration is tailnet-only." -ForegroundColor Green
    return
}

$outputResult = Invoke-NativeProcess -FilePath $tofu -Arguments @("output", "-json") -WorkingDirectory $tofuRoot -TimeoutSeconds 120
Assert-NativeSuccess -Result $outputResult -Activity "tofu output"
$outputs = $outputResult.StdOut | ConvertFrom-Json
$hostAddress = $outputs.public_ipv4.value
Write-Host "  host                : $hostAddress (server $($outputs.server_id.value))"

if ([string]::IsNullOrWhiteSpace($PublicIp)) { $PublicIp = Get-PublicIpAddress }
Write-Host "  this machine        : $PublicIp (SSH will be opened to $PublicIp/32 only)"

$opened = $false
try {
    # ----------------------------------------------------------------- open the window
    Write-Host ""
    Write-Host "=== opening break-glass SSH (firewall only; the server is never in the plan) ===" -ForegroundColor Yellow
    Set-BreakGlassCidrs -Tofu $tofu -Cidrs @("$PublicIp/32") -Activity "open SSH to $PublicIp/32"
    $opened = $true

    Write-Host "  waiting for TCP/22..."
    $reachable = $false
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        $probe = Test-NetConnection -ComputerName $hostAddress -Port 22 -InformationLevel Quiet -WarningAction SilentlyContinue
        if ($probe) { $reachable = $true; break }
        Start-Sleep -Seconds 5
    }
    if (-not $reachable) { throw "TCP/22 did not open within 120s. The rule was applied; check the Hetzner console." }
    Write-Host "  TCP/22 reachable"

    # -------------------------------------------------------------------- diagnostics
    Write-Host ""
    Write-Host "=== diagnosis: why did it never join the tailnet? ===" -ForegroundColor Cyan
    $probes = @(
        @{ Label = "cloud-init status";      Command = "cloud-init status --long 2>&1 | head -20" },
        @{ Label = "cloud-init errors";      Command = "sudo grep -iE 'error|fail|traceback' /var/log/cloud-init-output.log 2>/dev/null | tail -25" },
        @{ Label = "tailscale binary";       Command = "command -v tailscale >/dev/null && tailscale version | head -3 || echo 'TAILSCALE NOT INSTALLED'" },
        @{ Label = "tailscaled service";     Command = "systemctl is-active tailscaled 2>&1; systemctl is-enabled tailscaled 2>&1" },
        @{ Label = "tailscale state";        Command = "tailscale status 2>&1 | head -10" },
        @{ Label = "tailscale up attempt";   Command = "sudo journalctl -u tailscaled --no-pager 2>/dev/null | grep -iE 'authkey|invalid|expired|unauthorized|logged out|NeedsLogin' | tail -15" },
        @{ Label = "docker";                 Command = "systemctl is-active docker 2>&1" },
        @{ Label = "data volume mounted";    Command = "lsblk -o NAME,SIZE,MOUNTPOINT | grep -v loop; echo '---'; findmnt -no SOURCE,TARGET --target /mnt 2>/dev/null || true" }
    )
    foreach ($probe in $probes) {
        $probeResult = Invoke-RemoteCommand -HostAddress $hostAddress -Command $probe.Command
        Write-Host ""
        Write-Host "--- $($probe.Label) ---" -ForegroundColor DarkCyan
        $text = ($probeResult.StdOut + $probeResult.StdErr).Trim()
        if ([string]::IsNullOrWhiteSpace($text)) { Write-Host "  (no output)" } else { Write-Host $text }
    }

    # ------------------------------------------------------- join, with no key involved
    Write-Host ""
    Write-Host "=== joining the tailnet (interactive login; no auth key) ===" -ForegroundColor Cyan
    # Started detached so `tailscale up` can block on the owner's browser login while this
    # script reads the URL it printed.
    $startCommand = "sudo rm -f /tmp/tsup.log; sudo nohup tailscale up --ssh --accept-dns=true --hostname=pagentos-core > /tmp/tsup.log 2>&1 & sleep 8; sudo cat /tmp/tsup.log"
    $joinResult = Invoke-RemoteCommand -HostAddress $hostAddress -Command $startCommand -TimeoutSeconds 180
    $joinText = ($joinResult.StdOut + $joinResult.StdErr)

    $loginUrl = $null
    $match = [regex]::Match($joinText, "https://login\.tailscale\.com/\S+")
    if ($match.Success) { $loginUrl = $match.Value }

    if ($loginUrl) {
        Write-Host ""
        Write-Host "OWNER ACTION - open this URL and approve the machine:" -ForegroundColor Yellow
        Write-Host "  $loginUrl"
        Write-Host ""
        Write-Host "It ties pagentos-core to YOUR tailnet as an ordinary, non-ephemeral machine."
        Write-Host "Waiting up to $JoinTimeoutSeconds seconds for it to appear..."
    }
    else {
        Write-Host $joinText.Trim()
        Write-Host ""
        Write-Host "No login URL was printed - it may already be authenticating. Still waiting." -ForegroundColor Yellow
    }

    # ------------------------------------------------------------------------- verify
    $tailscaleExe = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
    $joined = $false
    $joinDeadline = (Get-Date).AddSeconds($JoinTimeoutSeconds)
    while ((Get-Date) -lt $joinDeadline) {
        if (Test-Path -LiteralPath $tailscaleExe) {
            $peerResult = Invoke-NativeProcess -FilePath $tailscaleExe -Arguments @("status") -TimeoutSeconds 60 -SuccessExitCodes @(0, 1)
            if ($peerResult.StdOut -match "pagentos-core") {
                Write-Host ""
                Write-Host "JOINED - visible from this machine:" -ForegroundColor Green
                foreach ($line in @($peerResult.StdOut -split "`n" | Where-Object { $_ -match "pagentos-core" })) {
                    Write-Host "  $($line.Trim())"
                }
                $joined = $true
                break
            }
        }
        Start-Sleep -Seconds 10
    }

    if (-not $joined) {
        Write-Warning "pagentos-core has not appeared on the tailnet within $JoinTimeoutSeconds seconds."
        Write-Warning "The break-glass rule is being closed anyway; re-run this script to try again."
    }
    else {
        # Prove the thing the tailnet exists for, from this machine, before closing.
        Write-Host ""
        Write-Host "=== verifying over the tailnet (before closing public SSH) ===" -ForegroundColor Cyan
        $overTailnet = Invoke-RemoteCommand -HostAddress "pagentos-core" -Command "hostname; tailscale ip -4"
        Write-Host ($overTailnet.StdOut + $overTailnet.StdErr).Trim()
    }
}
finally {
    # The window closes on every path: success, failure, or a diagnosis that threw.
    if ($opened -and -not $shouldKeepOpen) {
        Write-Host ""
        Write-Host "=== closing break-glass SSH ===" -ForegroundColor Yellow
        try {
            Set-BreakGlassCidrs -Tofu $tofu -Cidrs @() -Activity "close public SSH"
            Write-Host "public SSH is closed again; administration is tailnet-only." -ForegroundColor Green
        }
        catch {
            Write-Warning "FAILED TO CLOSE THE SSH RULE: $($_.Exception.Message)"
            Write-Warning "Close it manually, from infra\opentofu:"
            Write-Warning "  tofu apply -target=hcloud_firewall.agent_os -var 'ssh_admin_cidrs=[]' -auto-approve"
        }
    }
    elseif ($opened) {
        Write-Host ""
        Write-Warning "-KeepOpen was set: TCP/22 is still open to $PublicIp/32."
        Write-Warning "Close it with:  .\scripts\cloud\breakglass-ssh.ps1 -CloseOnly   (or the tofu command in the docs)"
    }
    Remove-Item -LiteralPath $knownHostsFile -Force -ErrorAction SilentlyContinue
}
