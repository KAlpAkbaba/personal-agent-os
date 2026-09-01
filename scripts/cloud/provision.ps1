<#
.SYNOPSIS
    Provision the Hetzner host with OpenTofu — preflight, plan, then (only when asked) apply.

.DESCRIPTION
    RQ-2 step 1. Everything about this is designed so the destructive/paid step is the last
    thing that happens and is never a surprise:

      * `-Plan` (the default) CREATES NOTHING. It reports exactly what would be built.
        Run it, read it, and only then rerun with -Apply.
      * secrets arrive as environment variables and are never written to disk, never
        echoed, and never passed on a command line (OpenTofu reads TF_VAR_* itself). The
        script prints whether each is SET, never its value.
      * OpenTofu itself is fetched to a user-scoped tools directory if absent — no
        elevation, no PATH surgery, pinned version, checked by `tofu version`.

    What it does NOT do: hold, store or transmit the credentials anywhere. They live in the
    shell you launch it from and die with it.

.PARAMETER Apply
    Actually create the infrastructure. Without this the script plans and stops.

.EXAMPLE
    # In the shell that has the credentials (they are never typed into chat):
    $env:TF_VAR_hcloud_token       = '...'   # Hetzner API token (Read+Write)
    $env:TF_VAR_tailscale_auth_key = '...'   # only needed for -Apply
    .\scripts\cloud\provision.ps1            # plan only, creates nothing
    .\scripts\cloud\provision.ps1 -Apply     # create it
#>
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$TofuVersion = "1.10.6",
    [string]$SshPublicKeyPath = (Join-Path $env:USERPROFILE ".ssh\id_kurek.pub")
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "..\lib\NativeProcess.ps1")

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$tofuRoot = Join-Path $repoRoot "infra\opentofu"

function Get-Tofu {
    <#  A pinned OpenTofu, user-scoped. No elevation, no PATH changes.  #>
    param([string]$Version)

    $existing = Get-Command tofu -ErrorAction SilentlyContinue
    if ($existing) { return $existing.Source }

    $toolsDir = Join-Path $env:LOCALAPPDATA "PagentOS\tools\opentofu-$Version"
    $exe = Join-Path $toolsDir "tofu.exe"
    if (Test-Path -LiteralPath $exe) { return $exe }

    Write-Host "fetching OpenTofu $Version (user-scoped, no elevation)..."
    New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
    $zip = Join-Path $toolsDir "tofu.zip"
    $url = "https://github.com/opentofu/opentofu/releases/download/v$Version/tofu_${Version}_windows_amd64.zip"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    Expand-Archive -LiteralPath $zip -DestinationPath $toolsDir -Force
    Remove-Item -LiteralPath $zip -Force
    if (-not (Test-Path -LiteralPath $exe)) { throw "OpenTofu did not unpack to $exe" }
    return $exe
}

# ------------------------------------------------------------------------- preflight
Write-Host "=== preflight ===" -ForegroundColor Cyan

$missing = @()

# Presence only. A script that echoes a token to prove it has one is the leak it exists to avoid.
if ([string]::IsNullOrWhiteSpace($env:TF_VAR_hcloud_token)) { $missing += "TF_VAR_hcloud_token (Hetzner API token, Read+Write)" }
else { Write-Host "  TF_VAR_hcloud_token       : SET" }

if ([string]::IsNullOrWhiteSpace($env:TF_VAR_tailscale_auth_key)) {
    if ($Apply) { $missing += "TF_VAR_tailscale_auth_key (pre-authorized, single-use, NOT ephemeral)" }
    else {
        # Plan does not consume it, but OpenTofu still requires every variable to have a
        # value. A placeholder keeps `-Plan` usable with the Hetzner token alone.
        $env:TF_VAR_tailscale_auth_key = "placeholder-plan-only-not-a-real-key"
        Write-Host "  TF_VAR_tailscale_auth_key : not set - using a placeholder (plan only; -Apply will require the real key)"
    }
}
else { Write-Host "  TF_VAR_tailscale_auth_key : SET" }

if ([string]::IsNullOrWhiteSpace($env:TF_VAR_owner_ssh_public_key)) {
    if (-not (Test-Path -LiteralPath $SshPublicKeyPath)) {
        $missing += "TF_VAR_owner_ssh_public_key (or an SSH public key at $SshPublicKeyPath)"
    }
    else {
        # The PUBLIC half only, and it is not a secret.
        $env:TF_VAR_owner_ssh_public_key = (Get-Content -LiteralPath $SshPublicKeyPath -Raw).Trim()
        Write-Host "  owner_ssh_public_key      : read from $SshPublicKeyPath (public half; break-glass access only)"
    }
}
else { Write-Host "  owner_ssh_public_key      : SET from the environment" }

if (@($missing).Count -gt 0) {
    Write-Host ""
    Write-Warning "missing input(s):"
    foreach ($item in $missing) { Write-Host "  - $item" }
    Write-Host ""
    Write-Host "Set them in THIS shell (they are never stored by this script):"
    Write-Host '  $env:TF_VAR_hcloud_token = ''...'''
    throw "preflight failed; nothing was contacted and nothing was created"
}

$tofu = Get-Tofu -Version $TofuVersion
$version = Invoke-NativeProcess -FilePath $tofu -Arguments @("version") -TimeoutSeconds 120
Write-Host "  opentofu                  : $(($version.StdOut -split "`n")[0].Trim())"

# ------------------------------------------------------------------------- init + plan
Write-Host ""
Write-Host "=== init ===" -ForegroundColor Cyan
$init = Invoke-NativeProcess -FilePath $tofu -Arguments @("init", "-input=false") -WorkingDirectory $tofuRoot -TimeoutSeconds 600
Assert-NativeSuccess -Result $init -Activity "tofu init"
Write-Host "providers ready"

Write-Host ""
Write-Host "=== plan (creates nothing) ===" -ForegroundColor Cyan
$planArgs = @("plan", "-input=false", "-no-color")
if ($Apply) { $planArgs += @("-out", "tfplan.binary") }
$plan = Invoke-NativeProcess -FilePath $tofu -Arguments $planArgs -WorkingDirectory $tofuRoot -TimeoutSeconds 900
if ($plan.ExitCode -ne 0) {
    # Provider errors (a bad token, a sold-out SKU) surface here, before anything exists.
    Write-Host $plan.StdOut
    Write-Host $plan.StdErr
    throw "tofu plan failed with exit code $($plan.ExitCode) - nothing was created"
}
Write-Host $plan.StdOut

if (-not $Apply) {
    Write-Host ""
    Write-Host "PLAN ONLY - nothing was created." -ForegroundColor Green
    Write-Host "Read the plan above. To build it, set TF_VAR_tailscale_auth_key and rerun with -Apply."
    return
}

# ------------------------------------------------------------------------- apply
Write-Host ""
Write-Host "=== apply (this creates paid infrastructure) ===" -ForegroundColor Yellow
$apply = Invoke-NativeProcess -FilePath $tofu -Arguments @("apply", "-input=false", "-no-color", "tfplan.binary") `
    -WorkingDirectory $tofuRoot -TimeoutSeconds 1800
Write-Host $apply.StdOut
if ($apply.ExitCode -ne 0) {
    Write-Host $apply.StdErr
    throw "tofu apply failed with exit code $($apply.ExitCode); inspect the state before retrying"
}

Write-Host ""
Write-Host "=== outputs ===" -ForegroundColor Cyan
$outputs = Invoke-NativeProcess -FilePath $tofu -Arguments @("output", "-json") -WorkingDirectory $tofuRoot -TimeoutSeconds 120
Assert-NativeSuccess -Result $outputs -Activity "tofu output"
$parsed = $outputs.StdOut | ConvertFrom-Json
foreach ($name in @($parsed.PSObject.Properties.Name)) {
    Write-Host "  $name = $($parsed.$name.value)"
}

Write-Host ""
Write-Host "Host created. Next, in order:" -ForegroundColor Green
Write-Host "  1. wait for cloud-init to finish, then confirm it joined the tailnet:  tailscale status"
Write-Host "  2. copy this repository to the host and run (over Tailscale SSH):"
Write-Host "       sudo ./scripts/cloud/deploy-cloud-core.sh"
Write-Host "  3. bootstrap the owner credential ONCE on the host (loopback guard; shown once)"
Write-Host "  4. restore the device row, then switch the agent:  .\scripts\switch-agent-broker.ps1 -BrokerHost <tailnet-ip>"
