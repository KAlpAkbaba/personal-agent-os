<#
.SYNOPSIS
    OWNER STEP (elevated, once per signing identity): trust the Session Companion's
    self-signed code-signing certificate so the MSIX packages it signs can be installed.

.DESCRIPTION
    B33 req 473 (owner decision 2026-09-16: Windows apps are self-signed too):
    the native factory's MSIX packages are signed on this device, in the Session Companion's
    own process, with a self-signed certificate it keeps in YOUR CurrentUser\My store (the
    private key is not exportable and never leaves it). Windows installs such a package only
    when that certificate is trusted on the machine - LocalMachine\TrustedPeople - and writing
    a LocalMachine store needs elevation, which the companion never asks for and never takes.

    This script is that one step. It imports ONLY the public certificate the companion
    exported (%LOCALAPPDATA%\PagentOS\signing\owner-test-signing.cer), identified by the
    thumbprint the companion recorded, into LocalMachine\TrustedPeople - nothing else, and
    never LocalMachine\Root. It refuses any certificate that is not exactly the companion's
    self-signed code-signing identity (see scripts\lib\NativeSigningTrust.ps1 for every check).
    It is idempotent; -Remove undoes it for one thumbprint.

    Run it from an ELEVATED Windows PowerShell opened from your own account (so that
    %LOCALAPPDATA% and CurrentUser\My are yours), in the repository root:

        powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\trust-native-signing-cert.ps1

    When the companion renews its identity (a month before the two-year expiry), the install
    step will say so and this command is run once more; -Remove -Thumbprint <old> tidies up.

    Exit codes: 0 done (or already so), 1 refused, 2 not elevated.

.PARAMETER Thumbprint
    The certificate to trust. Default: the one the companion recorded in identity.json. When
    given, it must EQUAL the recorded one unless -AllowRenewedThumbprint is also given. With
    -Remove it names the certificate to remove and need not be the recorded one.

.PARAMETER CertificatePath
    The companion's exported public certificate. Default: %LOCALAPPDATA%\PagentOS\signing\owner-test-signing.cer.
    Whatever file is named, its thumbprint must be the resolved one and every other check applies.

.PARAMETER AllowRenewedThumbprint
    Trust a -Thumbprint that identity.json does not name. Only for the owner who knows why
    (for example the record was lost); the certificate must still pass every other check,
    including being in the owner's CurrentUser\My with the companion's non-exportable key.

.PARAMETER Remove
    Remove the certificate with this thumbprint from LocalMachine\TrustedPeople instead.
#>

[CmdletBinding()]
param(
    [string]$Thumbprint,
    [string]$CertificatePath,
    [switch]$AllowRenewedThumbprint,
    [switch]$Remove
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib\NativeSigningTrust.ps1")

# The ONE store this step writes. Fixed here, not a parameter: a script that could be
# pointed at LocalMachine\Root would be a way to make anything a trusted root.
$TargetStoreName = "TrustedPeople"
$TargetStoreLocation = [System.Security.Cryptography.X509Certificates.StoreLocation]::LocalMachine
$OwnerStoreName = "My"

$principal = New-Object System.Security.Principal.WindowsPrincipal([System.Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "REFUSED: this step writes LocalMachine\TrustedPeople and needs an elevated PowerShell." -ForegroundColor Red
    Write-Host "Open Windows PowerShell with 'Run as administrator' from your own account and run:"
    Write-Host "  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\trust-native-signing-cert.ps1"
    exit 2
}

$directory = Get-NativeSigningDirectory
$recorded = Get-NativeSigningRecordedThumbprint -StateDirectory $directory

if ($Remove) {
    # Removing is how an OLD thumbprint is tidied up after a renewal, so -Remove does not
    # require identity.json to name it; Remove-NativeSigningTrust still refuses any
    # certificate whose subject is not the companion's.
    $requested = if ([string]::IsNullOrWhiteSpace($Thumbprint)) { $recorded } else { ($Thumbprint -replace '\s', '').ToUpperInvariant() }
    if ([string]::IsNullOrWhiteSpace($requested) -or $requested -notmatch '^[0-9A-F]{40}$') {
        Write-Host "REFUSED: name the certificate to remove with -Thumbprint (40 hex digits)." -ForegroundColor Red
        exit 1
    }
    $Thumbprint = $requested
    try {
        $outcome = Remove-NativeSigningTrust -Thumbprint $Thumbprint -StoreName $TargetStoreName -StoreLocation $TargetStoreLocation
    }
    catch {
        Write-Host "REFUSED: $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
    if ($outcome -eq "removed") {
        Write-Host "OK: $Thumbprint removed from LocalMachine\TrustedPeople. MSIX packages it signed will no longer install here."
    }
    else {
        Write-Host "OK: $Thumbprint was not in LocalMachine\TrustedPeople; nothing to remove."
    }
    exit 0
}

$resolved = Resolve-NativeSigningThumbprint -Requested $Thumbprint -Recorded $recorded -AllowRenewedThumbprint:$AllowRenewedThumbprint
if (-not $resolved.Ok) {
    Write-Host "REFUSED: $($resolved.Reason)." -ForegroundColor Red
    exit 1
}
$Thumbprint = $resolved.Thumbprint

if ([string]::IsNullOrWhiteSpace($CertificatePath)) {
    $CertificatePath = Join-Path $directory $script:NativeSigningCertificateFile
}

try {
    $certificate = Read-NativeSigningCertificate -Path $CertificatePath
}
catch {
    Write-Host "REFUSED: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

$verdict = Test-NativeSigningCertificate -Certificate $certificate -ExpectedThumbprint $Thumbprint -OwnerStoreName $OwnerStoreName
if (-not $verdict.Ok) {
    Write-Host "REFUSED: $($verdict.Reason)." -ForegroundColor Red
    exit 1
}

try {
    $outcome = Add-NativeSigningTrust -Certificate $certificate -StoreName $TargetStoreName -StoreLocation $TargetStoreLocation
}
catch {
    Write-Host "REFUSED: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

if ($outcome -eq "added") {
    Write-Host "OK: $Thumbprint ($($certificate.Subject), valid to $($certificate.NotAfter.ToString('yyyy-MM-dd'))) is now trusted in LocalMachine\TrustedPeople."
}
else {
    Write-Host "OK: $Thumbprint is already trusted in LocalMachine\TrustedPeople; nothing changed."
}
Write-Host "Signed MSIX packages from the native factory can now be installed on this machine."
exit 0
