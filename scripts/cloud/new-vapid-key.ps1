<#
.SYNOPSIS
    Generate a fresh VAPID key pair (RFC 8292) for Web Push, locally, and store the
    private key and the VAPID subject in the owner's local DPAPI secret store - ready
    to ship to the Cloud Core with the existing set-cloud-secret.ps1 pipeline.

.DESCRIPTION
    B11 req 372's only owner action: WebPush needs one P-256 key pair to sign the
    Authorization header a push service checks (RFC 8292). It is NOT an account, a
    signup, or a paid credential - "a locally generated P-256 key pair" is the whole
    of it, and this script is that generation step.

    What it does, in order:

      1. Generates a P-256 key pair with Windows CNG (`ECDsaCng`), as an EPHEMERAL key
         (no key name -> nothing is left in any Windows key store; the process's own
         memory is the only place the private key ever exists before step 2).
      2. Encodes the private key as the RAW 32-byte big-endian scalar, base64url - the
         exact format `app.webpush.vapid.load_private_key` parses, and deliberately
         never PEM (`-----BEGIN ... PRIVATE KEY-----`): scripts\quality-gate.ps1's
         secret-hygiene scan refuses any tracked file containing that block on sight,
         and this value is meant to sit in a plain `.env` line on the host.
      3. Writes the private key AND the VAPID subject to the SAME local DPAPI store
         `scripts\secret-store.ps1 -Set` uses (encrypted to this Windows account, on
         this machine only) - bypassing its interactive masked prompt only because the
         value is GENERATED here, never typed, so there is nothing to protect from a
         shell history the way `secret-store.ps1 -Set` protects a pasted value.
      4. Prints the PUBLIC key only (safe - RFC 8292 hands it to every push service
         openly as the `k` parameter) and the exact next commands to ship both values
         to the deployed Cloud Core. The private key is never printed, logged, or
         returned by this script in any form.

    Shipping to the host is the EXISTING pipeline, unchanged: `set-cloud-secret.ps1`
    reads a value out of this same local DPAPI store and sends it to the host over one
    SSH session, on stdin only - this script's whole job is putting a value there that
    was generated rather than typed.

.PARAMETER Subject
    The RFC 8292 "sub" claim: a "mailto:" or "https:" contact URI a push service can
    reach about abuse. Owner-provided, never hardcoded here (CLAUDE.md: never invent or
    commit personal data) - required, and validated against RFC 8292's own shape before
    anything is generated.

.PARAMETER Force
    Overwrite an already-stored VAPID private key. Refused without this flag: generating
    a SECOND key pair without shipping the first invalidates every browser subscription
    signed against the old public key (a subscription is bound to the key that created
    it), so this is a deliberately unusual thing to do twice.

.EXAMPLE
    .\scripts\cloud\new-vapid-key.ps1 -Subject mailto:owner@example.com
    .\scripts\cloud\set-cloud-secret.ps1 -Name PAGENTOS_WEBPUSH_VAPID_PRIVATE_KEY -ExpectProvider ""
    .\scripts\cloud\set-cloud-secret.ps1 -Name PAGENTOS_WEBPUSH_VAPID_SUBJECT -ExpectProvider ""
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Subject,

    [string]$PrivateKeySecretName = "PAGENTOS_WEBPUSH_VAPID_PRIVATE_KEY",
    [string]$SubjectSecretName = "PAGENTOS_WEBPUSH_VAPID_SUBJECT",
    [string]$StoreRoot = (Join-Path $env:LOCALAPPDATA "PagentOS\secrets"),
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")

if (-not (Test-SecretName -Name $PrivateKeySecretName)) { throw "unsafe secret name '$PrivateKeySecretName'" }
if (-not (Test-SecretName -Name $SubjectSecretName)) { throw "unsafe secret name '$SubjectSecretName'" }

# RFC 8292 §2: 'sub' MUST be a contact URI, either 'mailto:' or 'https:'. Checked here,
# BEFORE any key material exists, so a typo in the subject never costs a regenerated key.
if ($Subject -cnotmatch '^(mailto:|https://)\S+$') {
    throw "Subject must be a 'mailto:' or 'https://' URI with no whitespace (RFC 8292 section 2), got '$Subject'"
}

$privateKeyPath = Join-Path $StoreRoot "$PrivateKeySecretName.dpapi"
if ((Test-Path -LiteralPath $privateKeyPath) -and -not $Force) {
    throw (
        "$PrivateKeySecretName is already stored locally. Regenerating invalidates every " +
        "browser subscription signed against the OLD public key (each subscription is " +
        "bound to the key that created it) - pass -Force only if that is intended, and " +
        "ship the new key with set-cloud-secret.ps1 immediately so the two never drift."
    )
}

function Protect-VapidStoreDirectory {
    # Same ACL discipline as scripts\secret-store.ps1's Initialize-Store and
    # scripts\cloud\rotate-cloud-owner-credential.ps1's Protect-StoreDirectory: DPAPI
    # already makes the bytes useless to another account, this just stops another
    # account from holding a copy of them at all. A failure here is a warning, not a
    # fatal error - see Initialize-Store's own comment on why a machine that genuinely
    # cannot run icacls must not lose the ability to store a secret.
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

function Set-VapidStoredSecret {
    # Write first, harden second (the same ordering scripts\cloud\
    # rotate-cloud-owner-credential.ps1's Set-StoredSecretValue documents): any step
    # between "the secret exists" and "the secret is durably captured" is a step that
    # can lose it.
    param([string]$Name, [string]$Value, [string]$Root)
    if (-not (Test-Path $Root)) { New-Item -ItemType Directory -Force -Path $Root | Out-Null }
    $secure = ConvertTo-SecureString -String $Value -AsPlainText -Force
    try {
        $secure | ConvertFrom-SecureString | Set-Content -Path (Join-Path $Root "$Name.dpapi") -Encoding ASCII
    }
    finally {
        $secure = $null
    }
    if (-not (Protect-VapidStoreDirectory -Root $Root)) {
        Write-Warning "could not tighten the ACL on $Root; the value is still DPAPI-encrypted to this account"
    }
}

function ConvertTo-Base64Url {
    param([byte[]]$Bytes)
    return [Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

# ------------------------------------------------------------------ generate the key pair

# An EPHEMERAL CNG key (no key name passed to Create): nothing is written to any Windows
# key store. `AllowPlaintextExport` is required or ExportParameters refuses outright - the
# default policy assumes a key that never leaves CNG, which is the opposite of what a
# portable env-file secret needs.
$creationParams = New-Object System.Security.Cryptography.CngKeyCreationParameters
$creationParams.ExportPolicy = [System.Security.Cryptography.CngExportPolicies]::AllowPlaintextExport
$cngKey = [System.Security.Cryptography.CngKey]::Create(
    [System.Security.Cryptography.CngAlgorithm]::ECDsaP256, $null, $creationParams
)
try {
    $ecdsa = New-Object System.Security.Cryptography.ECDsaCng($cngKey)
    try {
        $params = $ecdsa.ExportParameters($true)
        if ($params.D.Length -ne 32 -or $params.Q.X.Length -ne 32 -or $params.Q.Y.Length -ne 32) {
            throw "generated key has unexpected field width (D=$($params.D.Length) X=$($params.Q.X.Length) Y=$($params.Q.Y.Length)); refusing to use it"
        }

        $privateKeyB64Url = ConvertTo-Base64Url -Bytes $params.D
        # RFC 8291/9.62 uncompressed point: 0x04 || X(32) || Y(32) - the same 65-byte
        # shape app.webpush.ece/vapid decode on the Python side.
        $publicPoint = New-Object byte[] 65
        $publicPoint[0] = 4
        [Array]::Copy($params.Q.X, 0, $publicPoint, 1, 32)
        [Array]::Copy($params.Q.Y, 0, $publicPoint, 33, 32)
        $publicKeyB64Url = ConvertTo-Base64Url -Bytes $publicPoint

        Set-VapidStoredSecret -Name $PrivateKeySecretName -Value $privateKeyB64Url -Root $StoreRoot
        Set-VapidStoredSecret -Name $SubjectSecretName -Value $Subject -Root $StoreRoot
    }
    finally {
        # Zero what can be zeroed. $privateKeyB64Url is a managed string and cannot be
        # reliably wiped (the same limit every .NET secret-handling script in this repo
        # accepts) - it goes out of scope with the function and is not returned,
        # printed, or logged anywhere.
        if ($params -and $params.D) { [Array]::Clear($params.D, 0, $params.D.Length) }
        $ecdsa.Dispose()
    }
}
finally {
    $cngKey.Delete()  # ephemeral in memory already; this also drops the CNG handle
}

Write-Host ""
Write-Host "VAPID key pair generated. The PRIVATE key is stored ONLY in the local DPAPI" -ForegroundColor Cyan
Write-Host "store below - it is never printed." -ForegroundColor Cyan
Write-Host ""
Write-Host "  Public key (safe to share - RFC 8292 sends this to every push service):"
Write-Host "    $publicKeyB64Url"
Write-Host ""
Write-Host "  Subject: $Subject"
Write-Host ""
Write-Host "  Stored locally as:"
Write-Host "    $PrivateKeySecretName  ->  $(Join-Path $StoreRoot "$PrivateKeySecretName.dpapi")"
Write-Host "    $SubjectSecretName  ->  $(Join-Path $StoreRoot "$SubjectSecretName.dpapi")"
Write-Host ""
Write-Host "Next: ship both to the deployed Cloud Core (value travels on stdin only," -ForegroundColor Yellow
Write-Host 'never echoed - -ExpectProvider "" because these are not the voice provider' -ForegroundColor Yellow
Write-Host "set-cloud-secret.ps1 otherwise checks for by default):" -ForegroundColor Yellow
Write-Host ""
Write-Host "  .\scripts\cloud\set-cloud-secret.ps1 -Name $PrivateKeySecretName -ExpectProvider `"`""
Write-Host "  .\scripts\cloud\set-cloud-secret.ps1 -Name $SubjectSecretName -ExpectProvider `"`""
Write-Host ""
Write-Host "Then the one owner action left in the browser: open the app's Settings page" -ForegroundColor Gray
Write-Host "and click 'Bildirimlere izin ver ve ac' (Web Push settings section) once per" -ForegroundColor Gray
Write-Host "browser that should receive push notifications." -ForegroundColor Gray
