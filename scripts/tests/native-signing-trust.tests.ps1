<#
.SYNOPSIS
    Windows PowerShell 5.1 tests for the owner's native signing trust step (B33 req 473):
    scripts\trust-native-signing-cert.ps1 and scripts\lib\NativeSigningTrust.ps1.

.DESCRIPTION
    The step imports the Session Companion's self-signed PUBLIC certificate into
    LocalMachine\TrustedPeople. These tests NEVER write a LocalMachine store: every store
    operation is driven against two throwaway CURRENT-USER stores (a lab "owner" store and a
    lab "target" store) that are deleted at the end, with generated certificates whose keys
    are deleted too. What is proved:
      - every refusal the script promises (private key in the file, wrong thumbprint, wrong
        subject, not self-issued, a CA, a wrong or extra EKU, expired, not in the owner's
        store, in the owner's store without its key);
      - add is idempotent and reads back; remove removes only that thumbprint and refuses a
        certificate of another subject; remove is idempotent;
      - the script refuses to run unelevated before touching anything, fixes its target to
        LocalMachine\TrustedPeople, never names Root, never elevates itself and runs no
        certificate tool;
      - the subject and file names equal the device's and the Cloud Core's (read from their
        sources, not restated).

    Run: powershell -NoProfile -File scripts\tests\native-signing-trust.tests.ps1
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeSigningTrust.ps1")

$script:Failures = 0
$script:Passes = 0
function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) { $script:Passes++; Write-Host "  PASS  $Message" }
    else { $script:Failures++; Write-Host "  FAIL  $Message" -ForegroundColor Red }
}

$X509 = "System.Security.Cryptography.X509Certificates"
$CurrentUser = [System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser
$runId = [guid]::NewGuid().ToString("N").Substring(0, 10)
$ownerStore = "PagentOSTrustLabOwner-$runId"
$targetStore = "PagentOSTrustLabTarget-$runId"
$subject = "CN=PagentOS Owner Test Signing"
$codeSigning = "1.3.6.1.5.5.7.3.3"

function New-LabCertificate {
    param(
        [string]$Subject = "CN=PagentOS Owner Test Signing",
        [bool]$Ca = $false,
        [string[]]$Ekus = @("1.3.6.1.5.5.7.3.3"),
        [int]$StartDays = -1,
        [int]$EndDays = 365
    )
    $rsa = [System.Security.Cryptography.RSA]::Create(2048)
    $request = New-Object System.Security.Cryptography.X509Certificates.CertificateRequest -ArgumentList $Subject, $rsa, ([System.Security.Cryptography.HashAlgorithmName]::SHA256), ([System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
    $request.CertificateExtensions.Add((New-Object System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension -ArgumentList $Ca, $false, 0, $true))
    $oids = New-Object System.Security.Cryptography.OidCollection
    foreach ($eku in $Ekus) { [void]$oids.Add((New-Object System.Security.Cryptography.Oid -ArgumentList $eku)) }
    $request.CertificateExtensions.Add((New-Object System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension -ArgumentList $oids, $false))
    $now = [DateTimeOffset]::UtcNow
    return $request.CreateSelfSigned($now.AddDays($StartDays), $now.AddDays($EndDays))
}

function Get-PublicOnly {
    param($Certificate)
    $der = $Certificate.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert)
    return (New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList (, $der))
}

function Get-KeyFileCount {
    $count = 0
    foreach ($dir in @((Join-Path $env:APPDATA "Microsoft\Crypto\Keys"), (Join-Path $env:APPDATA "Microsoft\Crypto\RSA"))) {
        if (Test-Path -LiteralPath $dir) { $count += @(Get-ChildItem -LiteralPath $dir -Recurse -File -Force -ErrorAction SilentlyContinue).Count }
    }
    return $count
}

$persisted = New-Object System.Collections.ArrayList
function Add-ToOwnerStore {
    <# Imports the certificate WITH a persisted key into the lab owner store (what the companion's store holds). #>
    param($CertificateWithKey)
    $password = "lab-" + $runId
    $pfx = $CertificateWithKey.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Pfx, $password)
    $flags = [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::PersistKeySet -bor [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::UserKeySet
    $imported = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList $pfx, $password, $flags
    [void]$persisted.Add($imported)
    $store = New-Object System.Security.Cryptography.X509Certificates.X509Store -ArgumentList $ownerStore, $CurrentUser
    $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
    $store.Add($imported)
    $store.Close()
}

function Add-PublicToOwnerStore {
    param($Certificate)
    $store = New-Object System.Security.Cryptography.X509Certificates.X509Store -ArgumentList $ownerStore, $CurrentUser
    $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
    $store.Add((Get-PublicOnly $Certificate))
    $store.Close()
}

$keyFilesBefore = Get-KeyFileCount

try {
    Write-Host ""
    Write-Host "native signing trust (PS $($PSVersionTable.PSVersion))"

    # ------------------------------------------------------------ the good certificate
    $good = New-LabCertificate
    Add-ToOwnerStore $good
    $goodPublic = Get-PublicOnly $good
    $verdict = Test-NativeSigningCertificate -Certificate $goodPublic -ExpectedThumbprint $good.Thumbprint.ToLowerInvariant() -OwnerStoreName $ownerStore
    Assert-True ($verdict.Ok) "the companion's public certificate, present with its key in the owner's store, is accepted ($($verdict.Reason))"

    # ------------------------------------------------------------ every refusal
    $refusals = @(
        @{ Name = "a file carrying a private key"; Cert = $good; Thumb = $good.Thumbprint; Expect = "private key" },
        @{ Name = "a thumbprint that is not the recorded one"; Cert = $goodPublic; Thumb = ("A" * 40); Expect = "is not the companion's" }
    )
    $wrongSubject = New-LabCertificate -Subject "CN=Someone Else"
    Add-ToOwnerStore $wrongSubject
    $refusals += @{ Name = "another subject"; Cert = (Get-PublicOnly $wrongSubject); Thumb = $wrongSubject.Thumbprint; Expect = "subject" }
    $authority = New-LabCertificate -Ca $true
    Add-ToOwnerStore $authority
    $refusals += @{ Name = "a certificate authority"; Cert = (Get-PublicOnly $authority); Thumb = $authority.Thumbprint; Expect = "end entity" }
    $serverAuth = New-LabCertificate -Ekus @("1.3.6.1.5.5.7.3.1")
    Add-ToOwnerStore $serverAuth
    $refusals += @{ Name = "a server-authentication certificate"; Cert = (Get-PublicOnly $serverAuth); Thumb = $serverAuth.Thumbprint; Expect = "only extended key usage" }
    $twoEkus = New-LabCertificate -Ekus @($codeSigning, "1.3.6.1.5.5.7.3.1")
    Add-ToOwnerStore $twoEkus
    $refusals += @{ Name = "code signing plus another usage"; Cert = (Get-PublicOnly $twoEkus); Thumb = $twoEkus.Thumbprint; Expect = "only extended key usage" }
    $expired = New-LabCertificate -StartDays -30 -EndDays -1
    Add-ToOwnerStore $expired
    $refusals += @{ Name = "an expired certificate"; Cert = (Get-PublicOnly $expired); Thumb = $expired.Thumbprint; Expect = "not currently valid" }
    $stranger = New-LabCertificate
    $refusals += @{ Name = "a lookalike that is not in the owner's store"; Cert = (Get-PublicOnly $stranger); Thumb = $stranger.Thumbprint; Expect = "holds no certificate" }
    $keyless = New-LabCertificate
    Add-PublicToOwnerStore $keyless
    $refusals += @{ Name = "a lookalike in the owner's store without its key"; Cert = (Get-PublicOnly $keyless); Thumb = $keyless.Thumbprint; Expect = "without its private key" }

    foreach ($case in $refusals) {
        $verdict = Test-NativeSigningCertificate -Certificate $case.Cert -ExpectedThumbprint $case.Thumb -OwnerStoreName $ownerStore
        Assert-True ((-not $verdict.Ok) -and $verdict.Reason.Contains($case.Expect)) "refused: $($case.Name) ($($verdict.Reason))"
    }

    $noStore = Test-NativeSigningCertificate -Certificate $goodPublic -ExpectedThumbprint $good.Thumbprint -OwnerStoreName ("PagentOSTrustLabMissing-" + $runId)
    Assert-True ((-not $noStore.Ok) -and $noStore.Reason.Contains("could not be opened")) "refused: an owner store that does not exist is not created ($($noStore.Reason))"
    Assert-True (-not (Test-Path ("HKCU:\Software\Microsoft\SystemCertificates\PagentOSTrustLabMissing-" + $runId))) "  ...and checking it created nothing"

    # ------------------------------------------------------------ the file readers
    $labDir = Join-Path ([System.IO.Path]::GetTempPath()) ("pagentos-trust-lab-" + $runId)
    New-Item -ItemType Directory -Path $labDir -Force | Out-Null
    [System.IO.File]::WriteAllBytes((Join-Path $labDir "owner-test-signing.cer"), $goodPublic.RawData)
    [System.IO.File]::WriteAllText((Join-Path $labDir "identity.json"), ('{"thumbprint":"' + $good.Thumbprint.ToLowerInvariant() + '","subject":"' + $subject + '"}'))
    Assert-True ((Get-NativeSigningRecordedThumbprint -StateDirectory $labDir) -eq $good.Thumbprint) "the recorded thumbprint is read from identity.json and upper-cased"
    $read = Read-NativeSigningCertificate -Path (Join-Path $labDir "owner-test-signing.cer")
    Assert-True ($read.Thumbprint -eq $good.Thumbprint -and -not $read.HasPrivateKey) "the exported .cer loads as a public certificate"
    [System.IO.File]::WriteAllText((Join-Path $labDir "identity.json"), '{"thumbprint":"not-a-thumbprint"}')
    Assert-True ($null -eq (Get-NativeSigningRecordedThumbprint -StateDirectory $labDir)) "a malformed recorded thumbprint is ignored, not trusted"
    Assert-True ($null -eq (Get-NativeSigningRecordedThumbprint -StateDirectory (Join-Path $labDir "absent"))) "no identity.json means no recorded thumbprint"
    $threw = $false
    try { [void](Read-NativeSigningCertificate -Path (Join-Path $labDir "missing.cer")) } catch { $threw = $_.Exception.Message.Contains("exports it the first time") }
    Assert-True $threw "a missing certificate file is refused with the reason"

    # ------------------------------------------------------------ add / remove (lab target store)
    $first = Add-NativeSigningTrust -Certificate $goodPublic -StoreName $targetStore -StoreLocation $CurrentUser
    Assert-True ($first -eq "added") "the certificate is added to the target store and reads back"
    Assert-True (Test-NativeSigningTrusted -Thumbprint $good.Thumbprint -StoreName $targetStore -StoreLocation $CurrentUser) "  ...and is found there by thumbprint"
    $second = Add-NativeSigningTrust -Certificate $goodPublic -StoreName $targetStore -StoreLocation $CurrentUser
    Assert-True ($second -eq "already_trusted") "adding it again changes nothing (idempotent)"
    $target = New-Object System.Security.Cryptography.X509Certificates.X509Store -ArgumentList $targetStore, $CurrentUser
    $target.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
    $inTarget = @($target.Certificates)
    $target.Close()
    Assert-True (@($inTarget).Count -eq 1 -and -not $inTarget[0].HasPrivateKey) "the target store holds exactly one certificate, without a key"

    $threw = $false
    try { [void](Add-NativeSigningTrust -Certificate $good -StoreName $targetStore -StoreLocation $CurrentUser) } catch { $threw = $_.Exception.Message.Contains("private key") }
    Assert-True $threw "a certificate carrying a private key is never added"

    $foreign = Get-PublicOnly $wrongSubject
    [void](Add-NativeSigningTrust -Certificate $foreign -StoreName $targetStore -StoreLocation $CurrentUser)
    $threw = $false
    try { [void](Remove-NativeSigningTrust -Thumbprint $wrongSubject.Thumbprint -StoreName $targetStore -StoreLocation $CurrentUser) } catch { $threw = $_.Exception.Message.Contains("is not the companion's") }
    Assert-True $threw "remove refuses a certificate whose subject is not the companion's"
    Assert-True (Test-NativeSigningTrusted -Thumbprint $wrongSubject.Thumbprint -StoreName $targetStore -StoreLocation $CurrentUser) "  ...and leaves it where it was"

    $removed = Remove-NativeSigningTrust -Thumbprint $good.Thumbprint -StoreName $targetStore -StoreLocation $CurrentUser
    Assert-True ($removed -eq "removed") "remove takes the companion's certificate out"
    Assert-True (-not (Test-NativeSigningTrusted -Thumbprint $good.Thumbprint -StoreName $targetStore -StoreLocation $CurrentUser)) "  ...and it no longer reads back"
    Assert-True (Test-NativeSigningTrusted -Thumbprint $wrongSubject.Thumbprint -StoreName $targetStore -StoreLocation $CurrentUser) "  ...while the other certificate is untouched"
    $again = Remove-NativeSigningTrust -Thumbprint $good.Thumbprint -StoreName $targetStore -StoreLocation $CurrentUser
    Assert-True ($again -eq "not_present") "removing it again changes nothing (idempotent)"

    # ------------------------------------------------------------ the script itself
    $scriptPath = Join-Path $repoRoot "scripts\trust-native-signing-cert.ps1"
    $scriptText = [System.IO.File]::ReadAllText($scriptPath)
    $libText = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\lib\NativeSigningTrust.ps1"))
    Assert-True ($scriptText.Contains('$TargetStoreName = "TrustedPeople"')) "the script's target store is fixed to TrustedPeople"
    Assert-True ($scriptText.Contains('StoreLocation]::LocalMachine')) "  ...in LocalMachine"
    Assert-True (-not ($scriptText -match '(?i)"Root"|AuthRoot|\[StoreName\]::Root')) "the script never names a root store"
    Assert-True (-not ($libText -match 'StoreLocation\]::LocalMachine')) "the library never chooses LocalMachine by itself (the script passes it)"
    foreach ($forbidden in @("Start-Process", "RunAs", "certutil", "certmgr", "signtool", "Import-Certificate", "Import-PfxCertificate", "Invoke-Expression")) {
        Assert-True (-not ($scriptText -match [regex]::Escape($forbidden)) -and -not ($libText -match [regex]::Escape($forbidden))) "neither the script nor the library uses $forbidden"
    }
    $parameters = (Get-Command $scriptPath).Parameters.Keys
    Assert-True (-not ($parameters -contains "StoreName") -and -not ($parameters -contains "StoreLocation") -and -not ($parameters -contains "TargetStoreName")) "the target store is not a parameter"

    $principal = New-Object System.Security.Principal.WindowsPrincipal([System.Security.Principal.WindowsIdentity]::GetCurrent())
    if ($principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
        # An elevated runner (CI) must NEVER run the real script: it would write LocalMachine.
        Write-Host "  SKIP  the unelevated refusal (this process is elevated; the script is not run)"
    }
    else {
        $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
        $output = & $powershell -NoProfile -ExecutionPolicy Bypass -File $scriptPath -Thumbprint $good.Thumbprint 2>&1 | Out-String
        $code = $LASTEXITCODE
        Assert-True ($code -eq 2) "unelevated, the script refuses with exit 2 (got $code)"
        Assert-True ($output.Contains("needs an elevated PowerShell") -and $output.Contains("trust-native-signing-cert.ps1")) "  ...and says how to run it"
        $output = & $powershell -NoProfile -ExecutionPolicy Bypass -File $scriptPath -Remove -Thumbprint $good.Thumbprint 2>&1 | Out-String
        Assert-True ($LASTEXITCODE -eq 2) "unelevated, -Remove refuses too, before touching any store"
    }

    # ------------------------------------------------------------ the three halves agree
    $csharp = [System.IO.File]::ReadAllText((Join-Path $repoRoot "devices\windows-agent\src\PagentOS.Agent.Core\Protocol\ProtocolConstants.cs"))
    $identity = [System.IO.File]::ReadAllText((Join-Path $repoRoot "devices\windows-agent\src\PagentOS.SessionCompanion\Native\OwnerSigningIdentity.cs"))
    $python = [System.IO.File]::ReadAllText((Join-Path $repoRoot "services\api\app\nativefactory\signing.py"))
    Assert-True ($csharp.Contains('TestSigningSubject = "' + $script:NativeSigningSubject + '"')) "the subject equals the device's NativeCapabilityNames.TestSigningSubject"
    Assert-True ($python.Contains('TEST_SIGNING_SUBJECT: Final = "' + $script:NativeSigningSubject + '"')) "the subject equals the Cloud Core's TEST_SIGNING_SUBJECT"
    Assert-True ($csharp.Contains('CodeSigningOid = "' + $script:CodeSigningOid + '"')) "the EKU equals the device's"
    Assert-True ($identity.Contains('CertificateFileName = "' + $script:NativeSigningCertificateFile + '"')) "the certificate file name equals the companion's export"
    Assert-True ($identity.Contains('StateFileName = "' + $script:NativeSigningStateFile + '"')) "the state file name equals the companion's"
    Assert-True ($identity.Contains('"PagentOS", "signing"')) "the directory is the companion's %LOCALAPPDATA%\PagentOS\signing"
    Assert-True ($csharp.Contains('TrustScript = @"scripts\trust-native-signing-cert.ps1"')) "the device names this script as the trust step"
    Assert-True ($python.Contains('TRUST_SCRIPT: Final = "scripts\\trust-native-signing-cert.ps1"')) "the Cloud Core names this script as the trust step"
}
finally {
    foreach ($imported in $persisted) {
        try {
            $key = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($imported)
            if ($key -is [System.Security.Cryptography.RSACng]) { $key.Key.Delete() }
            elseif ($key -is [System.Security.Cryptography.RSACryptoServiceProvider]) { $key.PersistKeyInCsp = $false; $key.Clear() }
        }
        catch { Write-Host "  (cleanup) key: $($_.Exception.Message)" }
    }
    foreach ($name in @($ownerStore, $targetStore)) {
        $registryPath = "HKCU:\Software\Microsoft\SystemCertificates\$name"
        if (Test-Path $registryPath) { Remove-Item -LiteralPath $registryPath -Recurse -Force }
    }
    if ((Get-Variable -Name labDir -ErrorAction SilentlyContinue) -and (Test-Path -LiteralPath $labDir)) { Remove-Item -LiteralPath $labDir -Recurse -Force }
}

Assert-True (-not (Test-Path "HKCU:\Software\Microsoft\SystemCertificates\$ownerStore") -and -not (Test-Path "HKCU:\Software\Microsoft\SystemCertificates\$targetStore")) "cleanup: both lab stores are gone"
$keyFilesAfter = Get-KeyFileCount
Assert-True ($keyFilesAfter -eq $keyFilesBefore) "cleanup: no key file is left in the profile ($keyFilesBefore before, $keyFilesAfter after)"

Write-Host ""
Write-Host "native signing trust: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
