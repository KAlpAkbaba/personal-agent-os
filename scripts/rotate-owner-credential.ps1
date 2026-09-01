<#
.SYNOPSIS
    Rotate a compromised Owner Credential, prove the old one is dead and the new one works,
    then resume the device bring-up from existing state.

.DESCRIPTION
    The Owner Credential was exposed in a screenshot and must be treated as compromised.
    This runs the existing host-side recovery path — `python -m app.identity.recover --rotate`
    — which is deliberately not an API operation: a credential you have lost or leaked cannot
    authenticate the request that replaces it.

    What rotation does and does not do:

      * the identity root's stored SHA-256 verifier is replaced, so the exposed credential
        stops working. Only the hash is ever stored; the plaintext is displayed once here and
        exists nowhere else afterwards;
      * the SAME owner identity is kept. `created_at` is preserved and `rotations` is
        incremented — this does not create a second owner;
      * every existing session is revoked, because a leaked credential may already have been
        exchanged for one. Signed-in clients must sign in again;
      * the enrolled DEVICE is untouched. Device identity is an ECDSA key on the machine and
        an enrollment row in the broker, neither of which depends on the owner credential.
        The device is not re-enrolled.

    The replacement is printed exactly once, to this console, inside a block marked private.
    It is never written to disk, never logged, never passed to another process, and the
    script refuses to run under PowerShell transcription — there is no safe way to print a
    secret into a transcript file.

.PARAMETER VerifyOldCredential
    Prompt (masked) for the exposed credential, solely to prove it is rejected AFTER
    rotation, then discard it. On by default. Skipping it is honest — the script then says
    the check was skipped rather than reporting a pass it did not perform.

.EXAMPLE
    # From an ELEVATED PowerShell, at the repository root:
    .\scripts\rotate-owner-credential.ps1
#>
[CmdletBinding()]
param(
    [string]$AgentConfig = (Join-Path $env:ProgramFiles "PagentOS\agent\service\appsettings.json"),
    [bool]$VerifyOldCredential = $true,
    [switch]$SkipResume
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "lib\NativeProcess.ps1")
. (Join-Path $PSScriptRoot "lib\InstallAcl.ps1")
. (Join-Path $PSScriptRoot "lib\IdentityStatus.ps1")

$repoRoot = Split-Path -Parent $PSScriptRoot
$apiRoot = Join-Path $repoRoot "services\api"

function Assert-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this from an elevated PowerShell: the replacement credential must be shown in the local elevated console, and the steps after rotation control a Windows Service."
    }
}

function Assert-NoTranscription {
    foreach ($path in @(
        "HKLM:\Software\Policies\Microsoft\Windows\PowerShell\Transcription",
        "HKCU:\Software\Policies\Microsoft\Windows\PowerShell\Transcription")) {
        if (Test-Path $path) {
            $value = (Get-ItemProperty -Path $path -ErrorAction SilentlyContinue).EnableTranscripting
            if ($value -eq 1) {
                throw "PowerShell transcription is enabled by policy ($path). The replacement credential would be written to a transcript file. Disable it, or rotate from a console where it is off."
            }
        }
    }

    try {
        $null = Stop-Transcript -ErrorAction Stop
        throw "A transcript was running in this session and has been stopped. Start a fresh console and run this again, so no part of the credential reaches that file."
    }
    catch [System.InvalidOperationException] {
        # No transcript running, which is what we want.
    }
}

function Get-Uv {
    $candidate = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe"
    if (Test-Path -LiteralPath $candidate) { return $candidate }
    $found = Get-Command uv -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    throw "uv not found; it is needed to run the host-side recovery tool"
}

function ConvertFrom-SecureStringPlain {
    param([System.Security.SecureString]$Secure)
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Get-IdentityStatus {
    param([string]$Uv)
    # Status carries no secret, so its stderr may be quoted on failure.
    return Invoke-MachineReadableProcess -FilePath $Uv `
        -Arguments @("run", "python", "-m", "app.identity.recover", "--status", "--json") `
        -WorkingDirectory $apiRoot -TimeoutSeconds 120 `
        -Activity "identity root status" -SensitiveOutput $false
}

Assert-Elevated
Assert-NoTranscription

$uv = Get-Uv

if (-not (Test-Path -LiteralPath $AgentConfig)) {
    throw "no installed agent configuration at $AgentConfig"
}
$agent = Get-Content -LiteralPath $AgentConfig -Raw | ConvertFrom-Json
$baseUrl = $agent.BrokerRestUrl.TrimEnd('/')

try {
    $health = Invoke-RestMethod -Uri "$baseUrl/v1/system/health" -TimeoutSec 10 -ErrorAction Stop
}
catch {
    throw "the Cloud Core is not answering at $baseUrl. Start it first (non-elevated): .\scripts\dev-broker.ps1"
}
Write-Host "cloud core: $baseUrl (health $($health.status))"

# --- capture what we are about to change -----------------------------------------------------

$before = Get-IdentityStatus -Uv $uv
if (-not $before.bootstrapped) {
    throw "there is no owner credential to rotate. Use .\scripts\complete-device-enrollment.ps1 to bootstrap one instead."
}
# Top-level `rotations`, through the tested accessor. The first version read
# `root.rotations` — a field that has never existed — and a defensive existence check turned
# the mistake into a silent zero plus a spurious warning while the real counter advanced.
$rotationsBefore = Get-IdentityRotationCount -Status $before
$rootPathBefore = Get-IdentityRootPath -Status $before
$createdAtBefore = Get-IdentityCreatedAt -Status $before
Write-Host "identity root: $rootPathBefore"
Write-Host "  bootstrapped, rotations=$rotationsBefore, created_at=$createdAtBefore"

$oldCredential = $null
if ($VerifyOldCredential) {
    Write-Host ""
    Write-Host "Paste the EXPOSED credential once. It is used only to prove it stops working" -ForegroundColor Yellow
    Write-Host "after rotation, and is discarded immediately. Press Enter alone to skip." -ForegroundColor Yellow
    $secure = Read-Host -Prompt "Exposed credential (input hidden)" -AsSecureString
    if ($secure.Length -gt 0) {
        $oldCredential = ConvertFrom-SecureStringPlain -Secure $secure
    }
}

# --- rotate ----------------------------------------------------------------------------------

$newCredential = $null
try {
    Write-Host ""
    Write-Host "rotating the owner credential (host-side; not an API operation)..."

    # Exit code first, then a strict single-document parse, and no failure path that quotes
    # stdout — stdout IS the credential here. An earlier version used the general-purpose
    # helper, whose error message includes stdout, and parsed leniently; the rotation
    # committed and the replacement was lost when the parse failed.
    $payload = Invoke-MachineReadableProcess -FilePath $uv `
        -Arguments @("run", "python", "-m", "app.identity.recover", "--rotate", "--json") `
        -WorkingDirectory $apiRoot -TimeoutSeconds 180 `
        -Activity "owner credential rotation" -SensitiveOutput $true

    $newCredential = $payload.owner_credential
    if (-not $newCredential) {
        throw "rotation returned a JSON document with no owner_credential field"
    }

    Write-Host "sessions revoked by the rotation: $($payload.sessions_revoked)"

    # --- prove it ----------------------------------------------------------------------------

    Write-Host ""
    Write-Host "verifying..."

    # 1. the new credential authenticates
    $session = Invoke-RestMethod -Uri "$baseUrl/v1/identity/sessions" -Method Post -TimeoutSec 30 `
        -ContentType "application/json" `
        -Body (@{ owner_credential = $newCredential; client_kind = "cli"; label = "rotation-check" } | ConvertTo-Json)
    Write-Host "  new credential authenticates      : YES (session $($session.session_id))"

    # 2. the exposed one no longer does
    if ($oldCredential) {
        $oldRejected = $false
        try {
            Invoke-RestMethod -Uri "$baseUrl/v1/identity/sessions" -Method Post -TimeoutSec 30 `
                -ContentType "application/json" `
                -Body (@{ owner_credential = $oldCredential; client_kind = "cli"; label = "should-fail" } | ConvertTo-Json) | Out-Null
        }
        catch {
            $status = $null
            if ($_.Exception.PSObject.Properties.Name -contains "Response" -and $_.Exception.Response) {
                $status = $_.Exception.Response.StatusCode.value__
            }
            $oldRejected = ($status -eq 401)
        }
        finally {
            $oldCredential = $null
        }

        if (-not $oldRejected) {
            throw "the exposed credential was NOT rejected after rotation. Stop and investigate before continuing."
        }
        Write-Host "  exposed credential rejected (401)  : YES"
    }
    else {
        Write-Host "  exposed credential rejected        : NOT CHECKED (skipped at the prompt)"
    }

    # 3. one owner identity, not two
    $after = Get-IdentityStatus -Uv $uv
    $rotationsAfter = Get-IdentityRotationCount -Status $after
    $rootPathAfter = Get-IdentityRootPath -Status $after
    $createdAtAfter = Get-IdentityCreatedAt -Status $after

    # Hard failures, not warnings: each of these is a claim this script makes to the owner,
    # and a claim that cannot be verified is a stop, not a footnote.
    if ($rotationsAfter -ne ($rotationsBefore + 1)) {
        throw "the rotation counter went $rotationsBefore -> $rotationsAfter (expected $($rotationsBefore + 1)). The credential state is ambiguous; do not proceed."
    }
    if ($rootPathAfter -ne $rootPathBefore) {
        throw "the identity root path changed during rotation ($rootPathBefore -> $rootPathAfter). Two roots would mean two owners; stop."
    }
    if ($createdAtAfter -ne $createdAtBefore) {
        throw "created_at changed during rotation ($createdAtBefore -> $createdAtAfter): that is a NEW owner identity, not a rotation. Stop."
    }
    Write-Host "  same owner identity preserved      : rotations $rotationsBefore -> $rotationsAfter, created_at unchanged, root unchanged"

    # 4. the enrolled device is untouched. Strict read (ADR-0029): Test-Path would report
    # an access-DENIED state file as "none on this machine yet", which is exactly wrong.
    $stateRaw = Get-MachineStateDocument -DataDir $agent.DataDir
    if ($null -ne $stateRaw) {
        $state = $stateRaw | ConvertFrom-Json
        Write-Host "  device enrollment preserved        : device_id=$($state.device_id)"
    }
    else {
        Write-Host "  device enrollment                  : none on this machine yet"
    }

    # --- the one owner action ----------------------------------------------------------------

    Write-Host ""
    Write-Host "============ REPLACEMENT OWNER CREDENTIAL - PRIVATE, SHOWN ONCE ============" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  $newCredential"
    Write-Host ""
    Write-Host "===========================================================================" -ForegroundColor Cyan
    Write-Host "Store it in your password manager NOW, replacing the exposed one." -ForegroundColor Yellow
    Write-Host "The server keeps only its SHA-256 hash. Nothing here writes it to disk." -ForegroundColor Yellow
    Write-Host "DO NOT paste this block anywhere. Everything below the line is safe to share." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "---------------------------------------------------------------------------"
    Write-Host ""

    # --- re-mint the automation session the rotation just revoked ----------------------------

    $automation = Invoke-RestMethod -Uri "$baseUrl/v1/identity/sessions" -Method Post -TimeoutSec 30 `
        -ContentType "application/json" `
        -Body (@{ owner_credential = $newCredential; client_kind = "cli"; label = "local-automation"; ttl_s = 86400 } | ConvertTo-Json)

    $storeRoot = Join-Path $env:LOCALAPPDATA "PagentOS\secrets"
    New-Item -ItemType Directory -Force -Path $storeRoot | Out-Null
    (ConvertTo-SecureString -String $automation.token -AsPlainText -Force) | ConvertFrom-SecureString |
        Set-Content -Path (Join-Path $storeRoot "PAGENTOS_OWNER_SESSION_TOKEN.dpapi") -Encoding ASCII
    Write-Host "replaced the local-automation session (session_id=$($automation.session_id), DPAPI-encrypted, 24h)"

    # --- resume from existing state ----------------------------------------------------------

    if (-not $SkipResume) {
        Write-Host ""
        Write-Host "resuming device bring-up from existing state (no re-bootstrap, no re-enrollment)..."
        $secureNew = ConvertTo-SecureString -String $newCredential -AsPlainText -Force
        & (Join-Path $PSScriptRoot "complete-device-enrollment.ps1") -OwnerCredential $secureNew -Resume
    }
}
finally {
    $newCredential = $null
    $oldCredential = $null
    [System.GC]::Collect()
}
