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
    [switch]$AllowDestroy,
    [int]$ExpectAdd,
    [int]$ExpectChange,
    [int]$ExpectDestroy,
    [int]$TailnetJoinTimeoutSeconds = 900,
    [string]$TofuVersion = "1.10.6",
    [string]$SshPublicKeyPath = (Join-Path $env:USERPROFILE ".ssh\id_kurek.pub"),

    # Injectable so the apply/plan paths can be driven by a fake tofu in tests instead of
    # only ever being exercised against real, paid infrastructure.
    [string]$TofuPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "..\lib\NativeProcess.ps1")

# Capture the caller's intent ONCE, into a plain boolean with a name no result object will
# ever want. PowerShell variable names are case-INSENSITIVE, so `$apply = <result>` later
# in the script is not a new variable - it is an assignment to the [switch]$Apply
# parameter, and PowerShell then tries to convert the result object to a SwitchParameter:
#
#     Cannot convert value "System.Management.Automation.PSCustomObject"
#     to type "System.Management.Automation.SwitchParameter"
#
# That is exactly what happened on the first real provisioning run, and where it happened
# matters: the assignment is evaluated AFTER the child process returns, so `tofu apply` had
# already created the infrastructure when the script died. A crash that looks like "it
# stopped before applying" while four resources were live and billing.
$shouldApply = [bool]$Apply

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$tofuRoot = Join-Path $repoRoot "infra\opentofu"

function Get-Tofu {
    <#  A pinned OpenTofu, user-scoped. No elevation, no PATH changes.  #>
    param([string]$Version, [string]$ExplicitPath)

    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        if (-not (Test-Path -LiteralPath $ExplicitPath)) { throw "-TofuPath does not exist: $ExplicitPath" }
        return $ExplicitPath
    }

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
    if ($shouldApply) { $missing += "TF_VAR_tailscale_auth_key (pre-authorized, single-use, NOT ephemeral)" }
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

$tofu = Get-Tofu -Version $TofuVersion -ExplicitPath $TofuPath
$tofuVersionResult = Invoke-NativeProcess -FilePath $tofu -Arguments @("version") -TimeoutSeconds 120
Write-Host "  opentofu                  : $(($tofuVersionResult.StdOut -split "`n")[0].Trim())"

# ------------------------------------------------------------------------- init + plan
Write-Host ""
Write-Host "=== init ===" -ForegroundColor Cyan
$initResult = Invoke-NativeProcess -FilePath $tofu -Arguments @("init", "-input=false") -WorkingDirectory $tofuRoot -TimeoutSeconds 600
Assert-NativeSuccess -Result $initResult -Activity "tofu init"
Write-Host "providers ready"

Write-Host ""
Write-Host "=== plan (creates nothing) ===" -ForegroundColor Cyan
# A saved plan embeds the variable values it was planned with - including the Tailscale
# auth key - so it is secret-bearing at rest. It is written to a fresh temp file, applied,
# and deleted in a finally, rather than left lying in the repository as tfplan.binary.
$planFile = Join-Path ([System.IO.Path]::GetTempPath()) "pagentos-plan-$([guid]::NewGuid().ToString('N')).tfplan"
$planArgs = @("plan", "-input=false", "-no-color")
if ($shouldApply) { $planArgs += @("-out", $planFile) }
$planResult = Invoke-NativeProcess -FilePath $tofu -Arguments $planArgs -WorkingDirectory $tofuRoot -TimeoutSeconds 900
if ($planResult.ExitCode -ne 0) {
    # Provider errors (a bad token, a sold-out SKU) surface here, before anything exists.
    Write-Host $planResult.StdOut
    Write-Host $planResult.StdErr
    throw "tofu plan failed with exit code $($planResult.ExitCode) - nothing was created"
}
Write-Host $planResult.StdOut

if (-not $shouldApply) {
    Write-Host ""
    Write-Host "PLAN ONLY - nothing was created." -ForegroundColor Green
    Write-Host "Read the plan above. To build it, set TF_VAR_tailscale_auth_key and rerun with -Apply."
    return
}

try {
    # --------------------------------------------------------- what will actually happen
    # The plan text above is for humans; this is the machine-checkable version, read back
    # from the SAVED plan that is about to be applied. Counting from the plan file rather
    # than from scraped stdout means the numbers shown are the ones that will execute.
    $showResult = Invoke-NativeProcess -FilePath $tofu -Arguments @("show", "-json", $planFile) `
        -WorkingDirectory $tofuRoot -TimeoutSeconds 300
    Assert-NativeSuccess -Result $showResult -Activity "tofu show -json (saved plan)"

    $planDocument = $showResult.StdOut | ConvertFrom-Json
    $changes = @(Get-OptionalProperty -InputObject $planDocument -Name "resource_changes")
    $toAdd = 0; $toChange = 0; $toDestroy = 0
    foreach ($change in $changes) {
        $actions = @(Get-OptionalProperty -InputObject $change.change -Name "actions")
        if ($actions -contains "create") { $toAdd++ }
        if ($actions -contains "update") { $toChange++ }
        if ($actions -contains "delete") { $toDestroy++ }
    }

    Write-Host ""
    Write-Host "=== the change about to be applied ===" -ForegroundColor Yellow
    Write-Host "  $toAdd to add, $toChange to change, $toDestroy to destroy"
    foreach ($change in $changes) {
        $actions = (@(Get-OptionalProperty -InputObject $change.change -Name "actions")) -join "+"
        Write-Host "    $actions  $($change.address)"
    }

    # Destroying real infrastructure is never an implicit consequence of "provision".
    if ($toDestroy -gt 0 -and -not $AllowDestroy) {
        throw "this plan would DESTROY $toDestroy resource(s). Refusing. Re-run with -AllowDestroy only if that is genuinely intended."
    }
    # When the caller states an expectation, a mismatch stops the run: it means the world
    # moved between the plan that was reviewed and the plan about to execute.
    if ($PSBoundParameters.ContainsKey("ExpectAdd") -and $toAdd -ne $ExpectAdd) {
        throw "expected $ExpectAdd resource(s) to be added but this plan adds $toAdd. Refusing; re-review the plan."
    }
    if ($PSBoundParameters.ContainsKey("ExpectChange") -and $toChange -ne $ExpectChange) {
        throw "expected $ExpectChange change(s) but this plan changes $toChange. Refusing; re-review the plan."
    }
    if ($PSBoundParameters.ContainsKey("ExpectDestroy") -and $toDestroy -ne $ExpectDestroy) {
        throw "expected $ExpectDestroy destroy(s) but this plan destroys $toDestroy. Refusing; re-review the plan."
    }

    if ($toAdd -eq 0 -and $toChange -eq 0 -and $toDestroy -eq 0) {
        Write-Host ""
        Write-Host "Nothing to do - the infrastructure already matches the configuration." -ForegroundColor Green
        return
    }

    # ------------------------------------------------------------------------- apply
    Write-Host ""
    Write-Host "=== apply (this creates paid infrastructure) ===" -ForegroundColor Yellow
    $applyResult = Invoke-NativeProcess -FilePath $tofu -Arguments @("apply", "-input=false", "-no-color", $planFile) `
        -WorkingDirectory $tofuRoot -TimeoutSeconds 1800
    Write-Host $applyResult.StdOut
    if ($applyResult.ExitCode -ne 0) {
        Write-Host $applyResult.StdErr
        throw "tofu apply failed with exit code $($applyResult.ExitCode); inspect the state before retrying"
    }
}
finally {
    # The saved plan holds the auth key. It does not outlive the run, on any path.
    Remove-Item -LiteralPath $planFile -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "=== outputs ===" -ForegroundColor Cyan
$outputResult = Invoke-NativeProcess -FilePath $tofu -Arguments @("output", "-json") -WorkingDirectory $tofuRoot -TimeoutSeconds 120
Assert-NativeSuccess -Result $outputResult -Activity "tofu output"
$parsedOutputs = $outputResult.StdOut | ConvertFrom-Json
foreach ($outputName in @($parsedOutputs.PSObject.Properties.Name)) {
    Write-Host "  $outputName = $($parsedOutputs.$outputName.value)"
}

# --------------------------------------------------- provisioning is not done until it is
# `tofu apply` succeeding only means Hetzner created the resources. The first real run
# ended exactly here, reporting success, while the host had silently failed to join the
# tailnet — leaving a machine with no tailnet and, by design, no public SSH. Since the
# tailnet IS the management path, a host that is not on it is a failed provision, and this
# says so rather than leaving it to be discovered later.
# Schema-safe (ADR-0031): outputs are a document from another tool, so an absent key is a
# possibility to handle, not a crash to suffer. Caught by the provisioning tests, whose
# fake tofu returned a narrower output set than the real one.
$hostnameOutput = Get-OptionalProperty -InputObject $parsedOutputs -Name "tailscale_hostname"
$expectedHostname = if ($null -ne $hostnameOutput) { Get-OptionalProperty -InputObject $hostnameOutput -Name "value" } else { $null }
if ([string]::IsNullOrWhiteSpace($expectedHostname)) {
    Write-Warning "the configuration declares no 'tailscale_hostname' output, so this run cannot confirm the host joined the tailnet."
    Write-Warning "Confirm it yourself before relying on the host: it must appear in 'tailscale status'."
    return
}

$tailscaleExe = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
if (-not (Test-Path -LiteralPath $tailscaleExe)) {
    Write-Warning "Tailscale is not installed on THIS machine, so the host's enrolment cannot be confirmed from here."
    Write-Warning "Check it yourself before relying on the host: it must appear as '$expectedHostname' on the tailnet."
    return
}

Write-Host ""
Write-Host "=== confirming the host joined the tailnet ===" -ForegroundColor Cyan
Write-Host "  waiting for '$expectedHostname' (cloud-init installs and enrols it; this takes a few minutes)"
$joined = $false
$joinDeadline = (Get-Date).AddSeconds($TailnetJoinTimeoutSeconds)
while ((Get-Date) -lt $joinDeadline) {
    $peerResult = Invoke-NativeProcess -FilePath $tailscaleExe -Arguments @("status") -TimeoutSeconds 60 -SuccessExitCodes @(0, 1)
    $peerLine = @($peerResult.StdOut -split "`n" | Where-Object { $_ -match [regex]::Escape($expectedHostname) })
    if (@($peerLine).Count -gt 0) {
        Write-Host "  joined: $($peerLine[0].Trim())" -ForegroundColor Green
        $joined = $true
        break
    }
    Start-Sleep -Seconds 15
}

if (-not $joined) {
    throw ("the host was created but never joined the tailnet within $TailnetJoinTimeoutSeconds seconds. " +
        "It has no public SSH by design, so recover with:  .\scripts\cloud\breakglass-ssh.ps1  " +
        "(it opens SSH to your IP only, reads /etc/pagentos/PROVISIONING_FAILED and the cloud-init log, " +
        "joins the node interactively without an auth key, and closes the rule again). " +
        "The infrastructure itself is intact - do NOT destroy or re-create it.")
}

Write-Host ""
Write-Host "Provisioned and reachable over the tailnet." -ForegroundColor Green
Write-Host "Next: copy this repository to the host and run  sudo ./scripts/cloud/deploy-cloud-core.sh"

Write-Host ""
