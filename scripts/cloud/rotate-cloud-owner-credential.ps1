<#
.SYNOPSIS
    Rotate the Cloud Owner Credential on the DEPLOYED Cloud Core, without the replacement
    ever being printed, logged, or written anywhere but the local DPAPI store.

.DESCRIPTION
    Use this when the owner credential for the deployed Cloud Core is lost or suspect.

    Why this exists next to scripts\rotate-owner-credential.ps1: that script runs
    `app.identity.recover --rotate` LOCALLY, in services\api, where the identity root
    resolves to services\api\var\identity - the DEV root. When the live Cloud Core is the
    remote host, rotating there changes a root the API never reads, the verification against
    the remote core then fails, and the `finally` clears the replacement. The rotation has
    committed and the new credential is gone. That is the "rotation committed, replacement
    lost" failure the sibling script's own comments describe, reached by a different door.

    The deployed identity root lives on the durable volume (`/mnt/pagentos-data/identity`,
    ADR-0027) and is bind-mounted into the api container. So rotation runs INSIDE that
    container, over Tailscale SSH, which is also the only authorization that exists for it:
    `app.identity.recover` is deliberately not an API operation, because a "forgot my
    credential" endpoint is an unauthenticated way to mint owner authority. The authority
    here is filesystem access to the identity root, which the owner has and the network
    does not.

    What rotation does and does not do:

      * the stored SHA-256 verifier is replaced, so the lost credential stops working;
      * the SAME owner identity is kept - `created_at` is preserved and `rotations` is
        incremented. This does not create a second owner. Both are asserted, and a
        mismatch is a hard stop;
      * every existing session is revoked, because a credential you had to recover may
        already have been exchanged for one;
      * nothing else is touched: no provider key, no Hetzner or Tailscale credential, no
        Windows component, no deployment.

    THE REPLACEMENT IS NEVER PRINTED BY THIS MODE. It is captured in memory, verified, and
    written to the local DPAPI store, so it can be revealed later, once, by the owner:

        .\scripts\cloud\rotate-cloud-owner-credential.ps1 -ShowStoredCredential

    That separation is what lets an agent perform the rotation safely: the plaintext never
    reaches stdout, so it cannot reach a transcript, a log, or a captured tool result.

.PARAMETER ShowStoredCredential
    Reveal the stored credential once, in this console, and exit. Refuses to run under
    PowerShell transcription - there is no safe way to print a secret into a transcript.

.PARAMETER VerifyLostCredential
    Off by default, and deliberately so. If you still HAVE the old credential (rotating a
    suspect one rather than a lost one), pass this to be prompted for it once, masked, so
    the script can prove it is rejected afterwards. A credential that is genuinely lost
    cannot be tested, and this script says the check was skipped rather than reporting a
    pass it did not perform.

.EXAMPLE
    # Rotate. Prints no secret; stores it encrypted for the reveal step.
    .\scripts\cloud\rotate-cloud-owner-credential.ps1

.EXAMPLE
    # Show it once, then put it in your password manager.
    .\scripts\cloud\rotate-cloud-owner-credential.ps1 -ShowStoredCredential
#>
[CmdletBinding(DefaultParameterSetName = "Rotate")]
param(
    [Parameter(ParameterSetName = "Show", Mandatory = $true)]
    [switch]$ShowStoredCredential,

    [Parameter(ParameterSetName = "Rotate")]
    [string]$BrokerHost = "pagentos-core",

    [Parameter(ParameterSetName = "Rotate")]
    [string]$Container = "pagentos-prod-api",

    [Parameter(ParameterSetName = "Rotate")]
    [string]$BaseUrl,

    [Parameter(ParameterSetName = "Rotate")]
    [string]$AgentConfig = (Join-Path $env:ProgramFiles "PagentOS\agent\service\appsettings.json"),

    [Parameter(ParameterSetName = "Rotate")]
    [string]$SshPath = (Join-Path $env:SystemRoot "System32\OpenSSH\ssh.exe"),

    [Parameter(ParameterSetName = "Rotate")]
    [switch]$VerifyLostCredential,

    # Prove the whole remote path - ssh, docker exec, the venv interpreter, the single-JSON
    # contract - and then stop, changing nothing. Rotation is irreversible and the old
    # credential is unrecoverable, so there is a dry run.
    [Parameter(ParameterSetName = "Rotate")]
    [switch]$StatusOnly,

    [string]$StoreRoot = (Join-Path $env:LOCALAPPDATA "PagentOS\secrets")
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")

$CredentialSecretName = "PAGENTOS_OWNER_CREDENTIAL"
$SessionSecretName = "PAGENTOS_OWNER_SESSION_TOKEN"

# The venv interpreter, not the image's bare `python`: the latter has no site-packages and
# fails on `import structlog` before it can reach the identity root.
$RemotePython = "/srv/pagentos/.venv/bin/python"

function Assert-NoTranscription {
    foreach ($path in @(
            "HKLM:\Software\Policies\Microsoft\Windows\PowerShell\Transcription",
            "HKCU:\Software\Policies\Microsoft\Windows\PowerShell\Transcription")) {
        if (Test-Path $path) {
            $value = (Get-ItemProperty -Path $path -ErrorAction SilentlyContinue).EnableTranscripting
            if ($value -eq 1) {
                throw "PowerShell transcription is enabled by policy ($path). The credential would be written to a transcript file. Disable it, or run this from a console where it is off."
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

function Protect-StoreDirectory {
    <#
        Owner-only, inheritance stripped - the same posture scripts\secret-store.ps1 applies,
        for the same reason: DPAPI makes the bytes useless to other accounts, the ACL means
        they do not get to hold the bytes.

        Best effort, and never fatal. icacls is resolved by ABSOLUTE path because a spawned
        non-interactive PowerShell on this machine does not reliably inherit a usable PATH,
        and a bare `icacls` threw here on 2026-09-05 - after the rotation had committed and
        before the replacement was written, which lost it. Hardening the directory is
        defence in depth; failing to harden it must never cost the secret.
    #>
    param([string]$Root)
    try {
        $icacls = Join-Path $env:SystemRoot "System32\icacls.exe"
        if (-not (Test-Path -LiteralPath $icacls)) { return $false }
        $sid = ([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value
        & $icacls $Root /inheritance:r /grant:r ("*" + $sid + ":(OI)(CI)F") /Q 2>$null | Out-Null
        return $true
    }
    catch { return $false }
}

function Set-StoredSecretValue {
    <#
        Write first, harden second. The ordering is the lesson from the incident above: any
        step between "the secret exists" and "the secret is durably captured" is a step that
        can lose it.
    #>
    param([string]$Name, [string]$Value, [string]$Root)
    if (-not (Test-Path $Root)) { New-Item -ItemType Directory -Force -Path $Root | Out-Null }
    $secure = ConvertTo-SecureString -String $Value -AsPlainText -Force
    try {
        $secure | ConvertFrom-SecureString |
            Set-Content -Path (Join-Path $Root ($Name + ".dpapi")) -Encoding ASCII
    }
    finally { $secure = $null }
    if (-not (Protect-StoreDirectory -Root $Root)) {
        Write-Warning "could not tighten the ACL on $Root; the value is still DPAPI-encrypted to this account"
    }
}

function ConvertFrom-SecureStringPlain {
    param([System.Security.SecureString]$Secure)
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Get-SessionStatusCode {
    <#
        Post a credential and return the HTTP status, never the body. Used for the negative
        control, where a 401 is the expected and desired answer.
    #>
    param([string]$Base, [string]$Credential, [string]$Label)
    try {
        $body = @{ owner_credential = $Credential; client_kind = "cli"; label = $Label } | ConvertTo-Json -Compress
        Invoke-RestMethod -Uri "$Base/v1/identity/sessions" -Method Post -TimeoutSec 30 `
            -ContentType "application/json" -Body $body | Out-Null
        return 201
    }
    catch {
        if ($_.Exception.PSObject.Properties.Name -contains "Response" -and $_.Exception.Response) {
            return $_.Exception.Response.StatusCode.value__
        }
        return -1
    }
}

function Invoke-RemoteIdentity {
    <#
        Run app.identity.recover inside the deployed api container. `-SensitiveOutput` is
        what keeps the rotation honest: on ANY failure path the helper refuses to quote
        stdout, and for --rotate stdout IS the credential.
    #>
    param([string[]]$RecoverArgs, [string]$Activity, [bool]$Sensitive, [int]$TimeoutSeconds = 180)
    $remote = "docker exec $Container $RemotePython -m app.identity.recover " + ($RecoverArgs -join " ")
    return Invoke-MachineReadableProcess -FilePath $SshPath `
        -Arguments @("-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-n",
                     "root@$BrokerHost", (ConvertTo-NativeCallArgument -Value $remote)) `
        -TimeoutSeconds $TimeoutSeconds -Activity $Activity -SensitiveOutput $Sensitive
}

# ------------------------------------------------------------------ reveal mode

if ($PSCmdlet.ParameterSetName -eq "Show") {
    Assert-NoTranscription
    $credential = Get-StoredSecretValue -Name $CredentialSecretName -StoreRoot $StoreRoot
    try {
        Write-Host ""
        Write-Host "========= CLOUD OWNER CREDENTIAL - PRIVATE, SHOWN ON REQUEST =========" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  $credential"
        Write-Host ""
        Write-Host "======================================================================" -ForegroundColor Cyan
        Write-Host "Put it in your password manager now, replacing the lost one." -ForegroundColor Yellow
        Write-Host "The server keeps only its SHA-256 hash. This console is the only place" -ForegroundColor Yellow
        Write-Host "it is ever displayed. DO NOT paste this block anywhere." -ForegroundColor Yellow
        Write-Host ""
        Write-Host "It also stays in the local DPAPI store, readable only by this Windows" -ForegroundColor Gray
        Write-Host "account on this machine, so scripts do not have to ask you to retype it." -ForegroundColor Gray
        Write-Host "To remove it from there once your password manager has it:" -ForegroundColor Gray
        Write-Host "  .\scripts\secret-store.ps1 -Remove $CredentialSecretName" -ForegroundColor Gray
    }
    finally {
        $credential = $null
        [System.GC]::Collect()
    }
    return
}

# ------------------------------------------------------------------ rotate mode

if (-not $BaseUrl) {
    if (-not (Test-Path -LiteralPath $AgentConfig)) {
        throw "no installed agent configuration at $AgentConfig; pass -BaseUrl explicitly"
    }
    $agent = Get-Content -LiteralPath $AgentConfig -Raw | ConvertFrom-Json
    $BaseUrl = $agent.BrokerRestUrl.TrimEnd('/')
}
if ($BrokerHost -cnotmatch '^[A-Za-z0-9.-]+$') { throw "unsafe BrokerHost '$BrokerHost'" }
if ($Container -cnotmatch '^[A-Za-z0-9_.-]+$') { throw "unsafe container name '$Container'" }
if (-not (Test-Path -LiteralPath $SshPath)) { throw "ssh not found at $SshPath" }

# Reachability is REQUIRED to rotate - rotating a root the API cannot read would strand the
# owner with a credential nothing accepts. It is NOT required to look: -StatusOnly reads the
# identity root over SSH and has no reason to need a healthy API, and a diagnostic that
# refuses to run precisely when things are broken is a diagnostic you cannot use.
$healthStatus = "unreachable"
$healthy = $false
try {
    $health = Invoke-RestMethod -Uri "$BaseUrl/v1/system/health" -TimeoutSec 15 -ErrorAction Stop
    $healthStatus = [string]$health.status
    $healthy = $true
}
catch {
    if (-not $StatusOnly) {
        throw "the Cloud Core is not answering at $BaseUrl. Rotating a root the API cannot read would strand you; stop and fix connectivity first."
    }
}
Write-Host "cloud core : $BaseUrl (health $healthStatus)"
Write-Host "identity   : $Container on $BrokerHost"

$before = Invoke-RemoteIdentity -RecoverArgs @("--status", "--json") `
    -Activity "identity root status (before)" -Sensitive $false -TimeoutSeconds 120
if (-not $before.bootstrapped) {
    throw "there is no owner credential on this Cloud Core to rotate. Bootstrap one instead, on the host: curl -X POST http://127.0.0.1:8001/v1/identity/bootstrap"
}
$rotationsBefore = [int]$before.rotations
$createdBefore = [string]$before.created_at
$rootBefore = [string]$before.root.path
$sessionsBefore = $before.active_sessions
Write-Host "  root=$rootBefore"
Write-Host "  rotations=$rotationsBefore created_at=$createdBefore active_sessions=$sessionsBefore"

if ($StatusOnly) {
    Write-Host ""
    Write-Host "STATUS ONLY: the remote path works and nothing was changed." -ForegroundColor Green
    Write-Host "Rotating would take rotations to $($rotationsBefore + 1), revoke $sessionsBefore session(s),"
    Write-Host "and leave created_at and the root path exactly as they are."
    if (-not $healthy) {
        Write-Warning "the Cloud Core did not answer at $BaseUrl. A real rotation refuses to run in this state."
    }
    return
}

$lostCredential = $null
if ($VerifyLostCredential) {
    Write-Host ""
    Write-Host "Paste the OLD credential once, only to prove it stops working. Enter alone skips." -ForegroundColor Yellow
    $secure = Read-Host -Prompt "Old credential (input hidden)" -AsSecureString
    if ($secure.Length -gt 0) { $lostCredential = ConvertFrom-SecureStringPlain -Secure $secure }
}

$newCredential = $null
try {
    Write-Host ""
    Write-Host "rotating on the deployed Cloud Core (host-side; not an API operation)..."

    $payload = Invoke-RemoteIdentity -RecoverArgs @("--rotate", "--json") `
        -Activity "owner credential rotation" -Sensitive $true -TimeoutSeconds 180
    $newCredential = [string]$payload.owner_credential
    if (-not $newCredential) { throw "rotation returned a JSON document with no owner_credential field" }

    # Store it BEFORE any further step can fail. A replacement that exists but was never
    # captured is worse than no rotation at all, and every line after this one can throw.
    Set-StoredSecretValue -Name $CredentialSecretName -Value $newCredential -Root $StoreRoot
    Write-Host "  captured to the DPAPI store       : $CredentialSecretName (owner-only, this machine)"
    Write-Host "  sessions revoked by the rotation  : $($payload.sessions_revoked)"

    Write-Host ""
    Write-Host "verifying..."

    # 1. a real owner-auth exchange with the new credential
    $body = @{ owner_credential = $newCredential; client_kind = "cli"; label = "rotation-check" } | ConvertTo-Json -Compress
    $session = Invoke-RestMethod -Uri "$BaseUrl/v1/identity/sessions" -Method Post -TimeoutSec 30 `
        -ContentType "application/json" -Body $body
    $checkToken = [string]$session.token
    $checkId = [string]$session.session_id
    Write-Host "  new credential authenticates      : YES (session $checkId)"

    # 2. that session really carries owner authority, not just a 201
    $current = Invoke-RestMethod -Uri "$BaseUrl/v1/identity/sessions/current" -TimeoutSec 30 `
        -Headers @{ Authorization = "Bearer $checkToken" }
    Write-Host "  the session is usable             : YES (client_kind=$($current.client_kind))"

    # 3. negative control: a well-formed credential that is not the stored one is refused.
    #    This proves the verifier is live - that a 201 above means the hash matched, rather
    #    than the endpoint accepting anything shaped like a credential.
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $bytes = New-Object byte[] 32
        $rng.GetBytes($bytes)
        $decoy = "pagentos_ok_" + ([Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_'))
    }
    finally { $rng.Dispose() }
    $decoyCode = Get-SessionStatusCode -Base $BaseUrl -Credential $decoy -Label "negative-control"
    $decoy = $null
    if ($decoyCode -ne 401) {
        throw "a random credential was answered $decoyCode instead of 401. The verifier is not behaving; stop and investigate."
    }
    Write-Host "  a non-matching credential is 401  : YES (verifier is live)"

    # 4. the old one, only if the owner still had it
    if ($lostCredential) {
        $oldCode = Get-SessionStatusCode -Base $BaseUrl -Credential $lostCredential -Label "should-fail"
        $lostCredential = $null
        if ($oldCode -ne 401) {
            throw "the old credential was answered $oldCode instead of 401 after rotation. Stop and investigate."
        }
        Write-Host "  old credential rejected (401)     : YES"
    }
    else {
        Write-Host "  old credential rejected           : NOT CHECKED - it was lost, so it cannot be presented"
    }

    # 5. one owner identity, not two. Hard failures: each is a claim made to the owner.
    $after = Invoke-RemoteIdentity -RecoverArgs @("--status", "--json") `
        -Activity "identity root status (after)" -Sensitive $false -TimeoutSeconds 120
    $rotationsAfter = [int]$after.rotations
    $createdAfter = [string]$after.created_at
    $rootAfter = [string]$after.root.path
    if ($rotationsAfter -ne ($rotationsBefore + 1)) {
        throw "the rotation counter went $rotationsBefore -> $rotationsAfter (expected $($rotationsBefore + 1)). The credential state is ambiguous; do not proceed."
    }
    if ($createdAfter -ne $createdBefore) {
        throw "created_at changed ($createdBefore -> $createdAfter): that is a NEW owner identity, not a rotation. Stop."
    }
    if ($rootAfter -ne $rootBefore) {
        throw "the identity root path changed ($rootBefore -> $rootAfter). Two roots would mean two owners; stop."
    }
    Write-Host "  same owner identity preserved     : rotations $rotationsBefore -> $rotationsAfter, created_at unchanged, root unchanged"

    # 6. replace the local automation session the rotation revoked, so ordinary scripts work
    #    again without the owner retyping anything.
    $autoBody = @{ owner_credential = $newCredential; client_kind = "cli"; label = "local-automation"; ttl_s = 86400 } | ConvertTo-Json -Compress
    $automation = Invoke-RestMethod -Uri "$BaseUrl/v1/identity/sessions" -Method Post -TimeoutSec 30 `
        -ContentType "application/json" -Body $autoBody
    Set-StoredSecretValue -Name $SessionSecretName -Value ([string]$automation.token) -Root $StoreRoot
    Write-Host "  local-automation session          : replaced (session $($automation.session_id), DPAPI, 24h)"

    # tidy: the throwaway check session is not left active
    try {
        Invoke-RestMethod -Uri "$BaseUrl/v1/identity/sessions/$checkId/revoke" -Method Post -TimeoutSec 20 `
            -Headers @{ Authorization = "Bearer $checkToken" } -ContentType "application/json" -Body "{}" | Out-Null
    }
    catch { }

    Write-Host ""
    Write-Host "ROTATION OK. The replacement was NOT printed anywhere." -ForegroundColor Green
    Write-Host "To see it once and put it in your password manager:" -ForegroundColor Green
    Write-Host "  .\scripts\cloud\rotate-cloud-owner-credential.ps1 -ShowStoredCredential"
}
finally {
    $newCredential = $null
    $lostCredential = $null
    $body = $null
    $autoBody = $null
    $checkToken = $null
    [System.GC]::Collect()
}
