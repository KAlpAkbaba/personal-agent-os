<#
.SYNOPSIS
    B33 req 473: the checks and store operations behind scripts\trust-native-signing-cert.ps1.

.DESCRIPTION
    The Session Companion signs the MSIX packages the native factory builds with a
    SELF-SIGNED code-signing certificate it creates in the owner's CurrentUser\My store
    (owner decision 2026-09-16). Windows installs such a package only when the signer is
    trusted on the machine, which is an elevated change the companion never makes. This
    library is what the owner's one elevated step runs, kept apart from the script so the
    tests can drive every check against throwaway CURRENT-USER stores and never touch a
    LocalMachine store.

    What is imported is ONLY the public certificate the companion exported
    (%LOCALAPPDATA%\PagentOS\signing\owner-test-signing.cer), and only after it is proven to
    be exactly the companion's identity:
      - the file carries no private key;
      - its thumbprint is the one the companion recorded (identity.json) or the owner named;
      - subject "CN=PagentOS Owner Test Signing", self-issued, signature valid;
      - an end entity (basic constraints CA=false) whose ONLY extended key usage is code
        signing (1.3.6.1.5.5.7.3.3);
      - currently valid;
      - present, WITH its private key, in the owner's own store - the certificate the
        companion signs with, not a file that merely looks like it.

    No certificate tool is run. Windows PowerShell 5.1 / .NET Framework only.
#>

Set-StrictMode -Version Latest

$script:NativeSigningSubject = "CN=PagentOS Owner Test Signing"
$script:CodeSigningOid = "1.3.6.1.5.5.7.3.3"
$script:NativeSigningCertificateFile = "owner-test-signing.cer"
$script:NativeSigningStateFile = "identity.json"

function Get-NativeSigningDirectory {
    [CmdletBinding()]
    param()
    return (Join-Path $env:LOCALAPPDATA "PagentOS\signing")
}

function Get-NativeSigningRecordedThumbprint {
    <# The thumbprint the companion recorded, or $null. #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$StateDirectory)
    $statePath = Join-Path $StateDirectory $script:NativeSigningStateFile
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { return $null }
    try {
        $state = [System.IO.File]::ReadAllText($statePath) | ConvertFrom-Json
    }
    catch {
        return $null
    }
    $value = [string]$state.thumbprint
    if ($value -match '^[0-9A-Fa-f]{40}$') { return $value.ToUpperInvariant() }
    return $null
}

function Read-NativeSigningCertificate {
    <# The public certificate file, loaded without ever asking for a key. #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "no certificate file at $Path - the companion exports it the first time it signs a package"
    }
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -gt 65536) { throw "$Path is $($bytes.Length) bytes - not a single public certificate" }
    return (New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList (, $bytes))
}

function Test-NativeSigningCertificate {
    <#
    Returns @{ Ok = [bool]; Reason = [string] }. Every refusal names its reason; the first
    failing check wins.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate,
        [Parameter(Mandatory = $true)][string]$ExpectedThumbprint,
        [Parameter(Mandatory = $true)][string]$OwnerStoreName,
        [datetime]$Now = [datetime]::UtcNow
    )
    function Refuse([string]$why) { return @{ Ok = $false; Reason = $why } }

    if ($Certificate.HasPrivateKey) { return (Refuse "the file carries a private key; only the public certificate is ever imported") }
    if ($Certificate.Thumbprint -ne $ExpectedThumbprint.ToUpperInvariant()) {
        return (Refuse "thumbprint $($Certificate.Thumbprint) is not the companion's $($ExpectedThumbprint.ToUpperInvariant())")
    }
    if (-not ($Certificate.Subject -ceq $script:NativeSigningSubject)) { return (Refuse "subject '$($Certificate.Subject)' is not '$($script:NativeSigningSubject)'") }
    if (-not ($Certificate.Issuer -ceq $Certificate.Subject)) { return (Refuse "the certificate is not self-issued") }

    $basic = @($Certificate.Extensions | Where-Object { $_ -is [System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension] })
    if (@($basic).Count -ne 1 -or $basic[0].CertificateAuthority) { return (Refuse "the certificate is not an end entity (basic constraints CA=false)") }
    $eku = @($Certificate.Extensions | Where-Object { $_ -is [System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension] })
    if (@($eku).Count -ne 1) { return (Refuse "the certificate has no single extended key usage extension") }
    $usages = @($eku[0].EnhancedKeyUsages | ForEach-Object { $_.Value })
    if (@($usages).Count -ne 1 -or $usages[0] -ne $script:CodeSigningOid) { return (Refuse "code signing must be the certificate's only extended key usage") }

    if ($Certificate.NotBefore.ToUniversalTime() -gt $Now -or $Certificate.NotAfter.ToUniversalTime() -le $Now) {
        return (Refuse "the certificate is not currently valid ($($Certificate.NotBefore.ToString('u')) .. $($Certificate.NotAfter.ToString('u')))")
    }

    $chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
    $chain.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
    $chain.ChainPolicy.VerificationFlags = [System.Security.Cryptography.X509Certificates.X509VerificationFlags]::AllowUnknownCertificateAuthority
    [void]$chain.Build($Certificate)
    $badSignature = @($chain.ChainStatus | Where-Object { $_.Status -eq [System.Security.Cryptography.X509Certificates.X509ChainStatusFlags]::NotSignatureValid })
    if (@($badSignature).Count -gt 0 -or $chain.ChainElements.Count -ne 1) { return (Refuse "the certificate's self-signature does not verify") }

    $owner = New-Object System.Security.Cryptography.X509Certificates.X509Store -ArgumentList $OwnerStoreName, ([System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser)
    try {
        $owner.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly -bor [System.Security.Cryptography.X509Certificates.OpenFlags]::OpenExistingOnly)
    }
    catch {
        return (Refuse "the owner's store CurrentUser\$OwnerStoreName could not be opened - run this from the owner's own account")
    }
    try {
        $found = @($owner.Certificates.Find([System.Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint, $Certificate.Thumbprint, $false))
        if (@($found).Count -eq 0) {
            return (Refuse "CurrentUser\$OwnerStoreName holds no certificate $($Certificate.Thumbprint) - this is not the companion's identity on this account (run elevated from the owner's own account)")
        }
        if (-not $found[0].HasPrivateKey) {
            return (Refuse "CurrentUser\$OwnerStoreName holds $($Certificate.Thumbprint) without its private key - it is not the identity the companion signs with")
        }
    }
    finally {
        $owner.Close()
    }

    return @{ Ok = $true; Reason = "the companion's self-signed code-signing certificate" }
}

function Open-NativeSigningTargetStore {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$StoreName,
        [Parameter(Mandatory = $true)][System.Security.Cryptography.X509Certificates.StoreLocation]$StoreLocation,
        [switch]$ReadOnly
    )
    $store = New-Object System.Security.Cryptography.X509Certificates.X509Store -ArgumentList $StoreName, $StoreLocation
    if ($ReadOnly) {
        $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
    }
    else {
        $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
    }
    return $store
}

function Test-NativeSigningTrusted {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Thumbprint,
        [Parameter(Mandatory = $true)][string]$StoreName,
        [Parameter(Mandatory = $true)][System.Security.Cryptography.X509Certificates.StoreLocation]$StoreLocation
    )
    $store = Open-NativeSigningTargetStore -StoreName $StoreName -StoreLocation $StoreLocation -ReadOnly
    try {
        return (@($store.Certificates.Find([System.Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint, $Thumbprint, $false)).Count -gt 0)
    }
    finally {
        $store.Close()
    }
}

function Add-NativeSigningTrust {
    <# Idempotent. Returns "added" or "already_trusted"; throws when the store does not read back. #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate,
        [Parameter(Mandatory = $true)][string]$StoreName,
        [Parameter(Mandatory = $true)][System.Security.Cryptography.X509Certificates.StoreLocation]$StoreLocation
    )
    if ($Certificate.HasPrivateKey) { throw "refusing to add a certificate that carries a private key" }
    if (Test-NativeSigningTrusted -Thumbprint $Certificate.Thumbprint -StoreName $StoreName -StoreLocation $StoreLocation) {
        return "already_trusted"
    }
    $store = Open-NativeSigningTargetStore -StoreName $StoreName -StoreLocation $StoreLocation
    try {
        $store.Add($Certificate)
    }
    finally {
        $store.Close()
    }
    if (-not (Test-NativeSigningTrusted -Thumbprint $Certificate.Thumbprint -StoreName $StoreName -StoreLocation $StoreLocation)) {
        throw "the certificate was added to $StoreLocation\$StoreName but does not read back"
    }
    return "added"
}

function Remove-NativeSigningTrust {
    <#
    Idempotent. Removes ONLY the certificate with this thumbprint, and only when it is the
    companion's subject. Returns "removed" or "not_present".
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Thumbprint,
        [Parameter(Mandatory = $true)][string]$StoreName,
        [Parameter(Mandatory = $true)][System.Security.Cryptography.X509Certificates.StoreLocation]$StoreLocation
    )
    $store = Open-NativeSigningTargetStore -StoreName $StoreName -StoreLocation $StoreLocation
    try {
        $found = @($store.Certificates.Find([System.Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint, $Thumbprint, $false))
        if (@($found).Count -eq 0) { return "not_present" }
        foreach ($certificate in $found) {
            if (-not ($certificate.Subject -ceq $script:NativeSigningSubject)) {
                throw "refusing to remove $Thumbprint from $StoreLocation\${StoreName}: its subject '$($certificate.Subject)' is not the companion's"
            }
        }
        foreach ($certificate in $found) { $store.Remove($certificate) }
    }
    finally {
        $store.Close()
    }
    if (Test-NativeSigningTrusted -Thumbprint $Thumbprint -StoreName $StoreName -StoreLocation $StoreLocation) {
        throw "$Thumbprint is still in $StoreLocation\$StoreName after the removal"
    }
    return "removed"
}
