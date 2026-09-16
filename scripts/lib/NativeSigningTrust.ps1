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
        companion signs with, not a file that merely looks like it;
      - that private key is a CNG key (not a legacy CSP key), its export policy is None
        (the companion creates it non-exportable), and its key name carries the companion's
        prefix (PagentOS-Owner-Test-Signing-). Security review 2026-09-17 (Medium): code
        running as the owner could otherwise plant a lookalike with an EXPORTABLE key, rewrite
        identity.json and the .cer, and have the owner's next elevated run trust it.
        Residual risk, recorded: same-user code can still create a non-exportable CNG key
        under that name - the owner's account is the trust boundary this step relies on.
      - an explicitly named -Thumbprint must still equal identity.json's unless the owner
        passes -AllowRenewedThumbprint (Resolve-NativeSigningThumbprint).

    No certificate tool is run. Windows PowerShell 5.1 / .NET Framework only.
#>

Set-StrictMode -Version Latest

$script:NativeSigningSubject = "CN=PagentOS Owner Test Signing"
$script:CodeSigningOid = "1.3.6.1.5.5.7.3.3"
$script:NativeSigningCertificateFile = "owner-test-signing.cer"
$script:NativeSigningStateFile = "identity.json"
# OwnerSigningIdentity.cs: SigningIdentityOptions.OwnerKeyNamePrefix + "-" + <date>-<guid>.
$script:NativeSigningKeyNamePrefix = "PagentOS-Owner-Test-Signing-"

function Resolve-NativeSigningThumbprint {
    <#
    Which thumbprint the trust step may import. Returns @{ Ok; Thumbprint; Reason }.
    - nothing requested: the recorded one (identity.json), or a refusal when none is recorded;
    - requested: it must EQUAL the recorded one, unless -AllowRenewedThumbprint is given
      (the owner deliberately trusting a thumbprint identity.json does not name, e.g. while
      the companion's record is being repaired). The other checks still all apply.
    #>
    [CmdletBinding()]
    param(
        [string]$Requested,
        [string]$Recorded,
        [switch]$AllowRenewedThumbprint
    )
    if (-not [string]::IsNullOrWhiteSpace($Requested)) {
        $normalized = ($Requested -replace '\s', '').ToUpperInvariant()
        if ($normalized -notmatch '^[0-9A-F]{40}$') {
            return @{ Ok = $false; Thumbprint = $null; Reason = "'$Requested' is not a certificate thumbprint (40 hex digits)" }
        }
        if ($AllowRenewedThumbprint) {
            return @{ Ok = $true; Thumbprint = $normalized; Reason = "named by the owner with -AllowRenewedThumbprint" }
        }
        if ([string]::IsNullOrWhiteSpace($Recorded)) {
            return @{ Ok = $false; Thumbprint = $null; Reason = "no signing identity is recorded in identity.json to compare $normalized with; pass -AllowRenewedThumbprint to trust it anyway" }
        }
        if ($normalized -ne $Recorded.ToUpperInvariant()) {
            return @{ Ok = $false; Thumbprint = $null; Reason = "$normalized is not the thumbprint the companion recorded ($($Recorded.ToUpperInvariant())); pass -AllowRenewedThumbprint only if you mean to trust it anyway" }
        }
        return @{ Ok = $true; Thumbprint = $normalized; Reason = "named and recorded" }
    }
    if ([string]::IsNullOrWhiteSpace($Recorded)) {
        return @{ Ok = $false; Thumbprint = $null; Reason = "no signing identity is recorded; the companion creates it the first time it signs an MSIX" }
    }
    return @{ Ok = $true; Thumbprint = $Recorded.ToUpperInvariant(); Reason = "recorded" }
}

function Test-NativeSigningPrivateKey {
    <#
    The owner-store certificate's private key must be the companion's: CNG, export policy
    None, key name with the companion's prefix. Returns $null when it is, else the reason.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate,
        [string]$KeyNamePrefix = $script:NativeSigningKeyNamePrefix
    )
    try {
        $key = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($Certificate)
    }
    catch {
        return "its private key could not be opened ($($_.Exception.Message))"
    }
    if ($null -eq $key) { return "it has no RSA private key" }
    try {
        if (-not ($key -is [System.Security.Cryptography.RSACng])) {
            return "its private key is not a CNG key ($($key.GetType().Name)); the companion's key is created in the key storage provider"
        }
        $cng = $key.Key
        if ($cng.ExportPolicy -ne [System.Security.Cryptography.CngExportPolicies]::None) {
            return "its private key is exportable (export policy $($cng.ExportPolicy)); the companion's key is created non-exportable"
        }
        $name = [string]$cng.KeyName
        if (-not $name.StartsWith($KeyNamePrefix, [System.StringComparison]::Ordinal)) {
            return "its private key '$name' is not named like the companion's ($KeyNamePrefix...)"
        }
        return $null
    }
    finally {
        $key.Dispose()
    }
}

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
        [datetime]$Now = [datetime]::UtcNow,
        [string]$KeyNamePrefix = $script:NativeSigningKeyNamePrefix
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
    if (@($badSignature).Count -gt 0 -or @($chain.ChainElements).Count -ne 1) { return (Refuse "the certificate's self-signature does not verify") }

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
        $keyProblem = Test-NativeSigningPrivateKey -Certificate $found[0] -KeyNamePrefix $KeyNamePrefix
        if ($null -ne $keyProblem) {
            return (Refuse "CurrentUser\$OwnerStoreName holds $($Certificate.Thumbprint), but $keyProblem - it is not the identity the companion signs with")
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
