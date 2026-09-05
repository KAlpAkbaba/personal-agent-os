<#
.SYNOPSIS
    Windows PowerShell 5.1 StrictMode tests for
    scripts/cloud/rotate-cloud-owner-credential.ps1, driven through a real native fake ssh.

.DESCRIPTION
    The incident these tests exist for happened on 2026-09-05, during a real rotation of the
    deployed Cloud Core. The rotation committed on the server, and the very next statement -
    storing the replacement - threw, because it called `icacls` by bare name and a spawned
    non-interactive PowerShell on this machine does not inherit a usable PATH. The `finally`
    then cleared the credential from memory. A 256-bit secret that now guarded production
    had been generated, shown to nobody, and lost. The owner had to rotate again.

    Two properties follow from that, and both are asserted here:

      * the replacement is written to the DPAPI store BEFORE any step that can fail. Every
        line between "the secret exists" and "the secret is durably captured" is a line that
        can lose it, so there must be none;
      * tightening the store ACL is best effort and never fatal. DPAPI already makes the
        bytes useless to another account; failing to also strip inheritance is not worth a
        lost credential.

    Plus the identity invariants the script promises the owner: it is a ROTATION, not a
    second owner, so a changed `created_at` or a changed root path is a hard stop rather
    than a warning, and the rotation counter must advance by exactly one.

    No network, no real ssh, no real docker, no real Cloud Core, and no real credential:
    the fake ssh returns a scripted JSON document per call.

    Run: powershell -NoProfile -File scripts\tests\cloud-owner-rotation.tests.ps1
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script:Target = Join-Path $repoRoot "scripts\cloud\rotate-cloud-owner-credential.ps1"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")

$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-owner-rotation-tests-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) { $script:Passes++; Write-Host "  PASS  $Message" }
    else { $script:Failures++; Write-Host "  FAIL  $Message" -ForegroundColor Red }
}

# ---------------------------------------------------------------- the fake ssh
#
# One binary, scripted per call: it reads the call index from a counter file and prints the
# matching document. That is enough to model "status, then rotate, then status", which is
# the whole shape of the script's remote conversation.

$fakeDir = Join-Path $script:Sandbox "fake"
New-Item -ItemType Directory -Force -Path $fakeDir | Out-Null
$fakeSsh = Join-Path $fakeDir "ssh.exe"

$source = @'
using System;
using System.IO;
public static class ScriptedSsh
{
    public static int Main(string[] args)
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        File.AppendAllText(Path.Combine(dir, "calls.txt"), string.Join(" ", args) + "\n");
        string counterPath = Path.Combine(dir, "counter.txt");
        int n = 0;
        if (File.Exists(counterPath)) { int.TryParse(File.ReadAllText(counterPath).Trim(), out n); }
        File.WriteAllText(counterPath, (n + 1).ToString());
        string replyPath = Path.Combine(dir, "reply" + n + ".json");
        if (!File.Exists(replyPath)) { Console.Error.WriteLine("no scripted reply " + n); return 3; }
        string exitPath = Path.Combine(dir, "exit" + n + ".txt");
        Console.Out.Write(File.ReadAllText(replyPath));
        if (File.Exists(exitPath)) { return int.Parse(File.ReadAllText(exitPath).Trim()); }
        return 0;
    }
}
'@
Add-Type -TypeDefinition $source -OutputAssembly $fakeSsh -OutputType ConsoleApplication | Out-Null

function Reset-Fake {
    Get-ChildItem -LiteralPath $fakeDir -Filter "*.txt" | Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem -LiteralPath $fakeDir -Filter "*.json" | Remove-Item -Force -ErrorAction SilentlyContinue
}

function Set-Reply {
    param([int]$Index, [string]$Json, [int]$ExitCode = 0)
    [IO.File]::WriteAllText((Join-Path $fakeDir ("reply$Index.json")), $Json)
    if ($ExitCode -ne 0) { [IO.File]::WriteAllText((Join-Path $fakeDir ("exit$Index.txt")), "$ExitCode") }
}

function New-StatusJson {
    param([int]$Rotations = 2, [string]$CreatedAt = "2026-09-02T10:16:01+00:00",
          [string]$RootPath = "/srv/pagentos/var/identity/owner_credential.json",
          [bool]$Bootstrapped = $true, [int]$ActiveSessions = 8)
    $b = if ($Bootstrapped) { "true" } else { "false" }
    return "{""action"":""status"",""bootstrapped"":$b,""rotations"":$Rotations," +
           """created_at"":""$CreatedAt"",""active_sessions"":$ActiveSessions," +
           """root"":{""path"":""$RootPath"",""kind"":""file"",""bootstrapped"":$b}}"
}

# A syntactically valid credential that is not, and never was, a real one.
$script:FakeCredential = "pagentos_ok_" + ("T" * 43)

function New-RotateJson {
    param([int]$Revoked = 8)
    return "{""action"":""rotate"",""owner_credential"":""$($script:FakeCredential)""," +
           """sessions_revoked"":$Revoked,""root"":{""path"":""/srv/pagentos/var/identity/owner_credential.json""}}"
}

function Invoke-Rotation {
    param([string[]]$ExtraArguments = @(), [string]$StoreRoot)
    $arguments = @("-BaseUrl", "http://127.0.0.1:9", "-SshPath", $fakeSsh, "-StoreRoot", $StoreRoot) + $ExtraArguments
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $script:Target @arguments 2>&1 | Out-String
        $exit = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    return [pscustomobject]@{ Output = $output; Exit = $exit }
}

function New-StoreRoot {
    $root = Join-Path $script:Sandbox ("store-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    return $root
}

function Test-CredentialStored {
    param([string]$StoreRoot)
    $path = Join-Path $StoreRoot "PAGENTOS_OWNER_CREDENTIAL.dpapi"
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    return (Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL" -StoreRoot $StoreRoot) -eq $script:FakeCredential
}

try {
    Write-Host "Cloud Core unreachable"
    # BaseUrl points at a closed port, so the health check fails before anything else. The
    # point is that it fails BEFORE the rotation, not after: rotating a root the API cannot
    # read would strand the owner.
    Reset-Fake
    $storeRoot = New-StoreRoot
    Set-Reply -Index 0 -Json (New-StatusJson)
    $r = Invoke-Rotation -StoreRoot $storeRoot
    Assert-True ($r.Exit -ne 0) "an unreachable Cloud Core stops a ROTATION"
    Assert-True ($r.Output -match "not answering") "and says so"
    $callsPath = Join-Path $fakeDir "calls.txt"
    $askedToRotate = (Test-Path -LiteralPath $callsPath) -and ((Get-Content -LiteralPath $callsPath -Raw) -match "--rotate")
    Assert-True (-not $askedToRotate) "and nothing was rotated"

    # ...but a read-only diagnostic must still work when the API is down, which is exactly
    # when the owner needs it. Requiring health to LOOK was a real defect these tests found.
    Reset-Fake
    Set-Reply -Index 0 -Json (New-StatusJson)
    $r = Invoke-Rotation -ExtraArguments @("-StatusOnly") -StoreRoot (New-StoreRoot)
    Assert-True ($r.Exit -eq 0) "-StatusOnly still reports with the Cloud Core down"
    Assert-True ($r.Output -match "A real rotation refuses to run in this state") "and warns that a rotation would refuse"

    Write-Host ""
    Write-Host "the capture-ordering guarantee (the 2026-09-05 incident)"
    # The rotation succeeds remotely, then verification fails because there is no Cloud Core
    # to authenticate against. The credential must ALREADY be in the store: this is exactly
    # the window in which the real incident lost one.
    #
    # Health is the first thing the script does, so this case is driven through -StatusOnly's
    # sibling path by pointing BaseUrl at a server that answers health and nothing else. With
    # no such server available in a unit test, the invariant is asserted structurally
    # instead - the store write must textually precede every verification call in the source.
    $src = [IO.File]::ReadAllText($script:Target)
    $storeIndex = $src.IndexOf("Set-StoredSecretValue -Name `$CredentialSecretName")
    $verifyIndex = $src.IndexOf("rotation-check")
    Assert-True ($storeIndex -gt 0 -and $verifyIndex -gt 0) "both the capture and the verification are present"
    Assert-True ($storeIndex -lt $verifyIndex) "the replacement is captured BEFORE the first step that can fail"

    $setFn = $src.Substring($src.IndexOf("function Set-StoredSecretValue"))
    $setFn = $setFn.Substring(0, $setFn.IndexOf("`nfunction "))
    $writeIdx = $setFn.IndexOf("ConvertFrom-SecureString")
    $aclIdx = $setFn.IndexOf("Protect-StoreDirectory")
    Assert-True ($writeIdx -gt 0 -and $aclIdx -gt 0) "the writer both writes and hardens"
    Assert-True ($writeIdx -lt $aclIdx) "it writes first and hardens second, never the reverse"
    Assert-True ($src.Contains('System32\icacls.exe')) "icacls is resolved by absolute path, not by PATH lookup"
    Assert-True ($src -match "Write-Warning ""could not tighten the ACL") "a failed ACL is a warning, not a lost credential"

    Write-Host ""
    Write-Host "identity invariants"
    Reset-Fake
    $storeRoot = New-StoreRoot
    Set-Reply -Index 0 -Json (New-StatusJson -Bootstrapped $false)
    $r = Invoke-Rotation -ExtraArguments @("-StatusOnly") -StoreRoot $storeRoot
    Assert-True ($r.Exit -ne 0) "an un-bootstrapped Cloud Core is refused"
    Assert-True ($r.Output -match "no owner credential on this Cloud Core") "and points at bootstrap instead"

    Reset-Fake
    $storeRoot = New-StoreRoot
    Set-Reply -Index 0 -Json (New-StatusJson -Rotations 2 -ActiveSessions 8)
    $r = Invoke-Rotation -ExtraArguments @("-StatusOnly") -StoreRoot $storeRoot
    Assert-True ($r.Exit -eq 0) "-StatusOnly succeeds"
    Assert-True ($r.Output -match "nothing was changed") "and says nothing was changed"
    Assert-True ($r.Output -match "rotations to 3" -and $r.Output -match "revoke 8 session") "and predicts exactly what a rotation would do"
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $storeRoot "PAGENTOS_OWNER_CREDENTIAL.dpapi"))) "-StatusOnly stores nothing"
    $cp = Join-Path $fakeDir "calls.txt"
    $calls = if (Test-Path -LiteralPath $cp) { Get-Content -LiteralPath $cp -Raw } else { "" }
    Assert-True ($calls -notmatch "--rotate") "-StatusOnly never asks the host to rotate"
    Assert-True ($calls -match "--status") "it does ask for status"
    Assert-True ($calls -match "/srv/pagentos/\.venv/bin/python") "it uses the venv interpreter, not the image's bare python"
    Assert-True ($calls -match "docker exec pagentos-prod-api") "it runs inside the deployed api container"

    Write-Host ""
    Write-Host "the reveal path"
    $storeRoot = New-StoreRoot
    $secure = ConvertTo-SecureString -String $script:FakeCredential -AsPlainText -Force
    $secure | ConvertFrom-SecureString | Set-Content -Path (Join-Path $storeRoot "PAGENTOS_OWNER_CREDENTIAL.dpapi") -Encoding ASCII
    # The reveal is its own parameter set: -BaseUrl and -SshPath belong to the rotate set and
    # cannot be combined with it. That is deliberate - the command the owner runs is exactly
    # `-ShowStoredCredential` and nothing else.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $script:Target `
            -ShowStoredCredential -StoreRoot $storeRoot 2>&1 | Out-String
        $r = [pscustomobject]@{ Output = $output; Exit = $LASTEXITCODE }
    }
    finally { $ErrorActionPreference = $previous }
    Assert-True ($r.Exit -eq 0) "-ShowStoredCredential succeeds when a credential is stored"
    Assert-True ($r.Output.Contains($script:FakeCredential)) "and shows it - this is the ONE place the value is ever printed"
    Assert-True ($r.Output -match "password manager") "and tells the owner what to do with it"

    Write-Host ""
    Write-Host "the value is not printed by the rotation path"
    # Every other mode must be silent about the secret. Asserted on the source, because the
    # rotation itself cannot be run without a Cloud Core.
    $rotateSection = $src.Substring($src.IndexOf("# ------------------------------------------------------------------ rotate mode"))
    Assert-True ($rotateSection -notmatch 'Write-Host "  \$newCredential"') "the rotate path never echoes the credential"
    Assert-True ($rotateSection -match "NOT printed anywhere") "and says so at the end"
}
finally {
    Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "cloud-owner-rotation tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
