<#
.SYNOPSIS
    Windows PowerShell 5.1 StrictMode tests for scripts/lib/SecretStore.ps1 and
    scripts/cloud/set-cloud-secret.ps1: the owner's provider-credential path.

.DESCRIPTION
    What must hold, proven on the real 5.1 engine with a FAKE ssh that is a real native
    .exe (compiled here with Add-Type) recording its parsed argv and its stdin separately:

      1. a value stored the way secret-store.ps1 stores it (ConvertFrom-SecureString, DPAPI)
         is read back exactly by the library;
      2. the value reaches ssh on STDIN and nowhere else - not in argv, not in the script's
         output, not in the remote command;
      3. the remote command survives 5.1's native-argument quoting BYTE FOR BYTE (the
         escaper is proven against a real CRT parser, not described), is name-only, refuses
         shell-unsafe names/paths, and ends with the compose restart unless -SkipRestart;
      4. -DryRun opens no ssh and needs no stored secret;
      5. a remote refusal (exit 65) surfaces as a typed failure that still shows no value.

    No network, no real ssh, no real host.

    Run: powershell -NoProfile -File scripts\tests\cloud-secret.tests.ps1
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$scriptUnderTest = Join-Path $repoRoot "scripts\cloud\set-cloud-secret.ps1"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")

$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-cloud-secret-tests-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) { $script:Passes++; Write-Host "  PASS  $Message" }
    else { $script:Failures++; Write-Host "  FAIL  $Message" -ForegroundColor Red }
}

# ---------------------------------------------------------------- fixtures
$store = Join-Path $script:Sandbox "store"
New-Item -ItemType Directory -Force -Path $store | Out-Null
$secretName = "PAGENTOS_TEST_PROVIDER_KEY"
$secretValue = "dummy-not-a-secret-" + [guid]::NewGuid().ToString("N")
# exactly how secret-store.ps1 -Set writes it
$secure = ConvertTo-SecureString -String $secretValue -AsPlainText -Force
$secure | ConvertFrom-SecureString | Set-Content -Path (Join-Path $store "$secretName.dpapi") -Encoding ASCII

# A REAL native fake ssh: argv (after the C runtime parsed it) one per line -> argv.txt,
# stdin verbatim -> stdin.txt, exit code from FAKE_SSH_EXIT.
$fakeDir = Join-Path $script:Sandbox "fake"
New-Item -ItemType Directory -Force -Path $fakeDir | Out-Null
$fakeSsh = Join-Path $fakeDir "ssh.exe"
$fakeSource = @'
using System;
using System.IO;
public static class FakeSsh
{
    public static int Main(string[] args)
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        File.WriteAllText(Path.Combine(dir, "argv.txt"), string.Join("\n", args));
        File.WriteAllText(Path.Combine(dir, "stdin.txt"), Console.In.ReadToEnd());
        string exit = Environment.GetEnvironmentVariable("FAKE_SSH_EXIT");
        return string.IsNullOrEmpty(exit) ? 0 : int.Parse(exit);
    }
}
'@
Add-Type -TypeDefinition $fakeSource -OutputAssembly $fakeSsh -OutputType ConsoleApplication | Out-Null

function Invoke-ScriptUnderTest {
    param([string[]]$Arguments)
    Remove-Item -LiteralPath (Join-Path $fakeDir "argv.txt"), (Join-Path $fakeDir "stdin.txt") -ErrorAction SilentlyContinue
    # Under $ErrorActionPreference = "Stop", 5.1 turns a native child's stderr line into a
    # terminating NativeCommandError before the exit code can be read; the failure modes
    # under test WRITE to stderr on purpose, so relax it for the child call only.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $scriptUnderTest @Arguments 2>&1 | Out-String
        $exit = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    return [pscustomobject]@{ Output = $output; Exit = $exit }
}

function Get-FakeArgv { return @(Get-Content -LiteralPath (Join-Path $fakeDir "argv.txt")) }

# The remote command exactly as the script builds it (its function is script-scoped, so
# reproduce the call through the script's own file in a child engine).
function Get-ExpectedRemote {
    param([switch]$Restart)
    $probe = @"
`$ErrorActionPreference = 'Stop'
`$text = Get-Content -LiteralPath '$scriptUnderTest' -Raw
`$start = `$text.IndexOf('function New-RemoteSecretInstallCommand')
`$end = `$text.IndexOf('function Get-RealtimeProviders')
. (Join-Path '$repoRoot' 'scripts\lib\SecretStore.ps1')
Invoke-Expression `$text.Substring(`$start, `$end - `$start)
New-RemoteSecretInstallCommand -Name '$secretName' -EnvFile '/opt/pagentos/.env' -RepoRoot '/opt/pagentos/app' -Restart:`$$(if ($Restart) { 'true' } else { 'false' })
"@
    return (& $powershell -NoProfile -NonInteractive -Command $probe | Out-String).TrimEnd("`r", "`n")
}

try {
    Write-Host "SecretStore library"
    Assert-True ((Get-StoredSecretValue -Name $secretName -StoreRoot $store) -eq $secretValue) "round-trips a value stored like secret-store.ps1 -Set"
    $threw = $false; $msg = ""
    try { Get-StoredSecretValue -Name "NOPE_MISSING" -StoreRoot $store | Out-Null } catch { $threw = $true; $msg = $_.Exception.Message }
    Assert-True ($threw -and $msg -match "secret-store.ps1 -Set NOPE_MISSING") "a missing secret names the command to store it"
    Assert-True (-not (Test-SecretName -Name "bad-name")) "rejects a hyphenated name"
    Assert-True (-not (Test-SecretName -Name 'X; rm -rf /')) "rejects shell syntax in a name"
    Assert-True (Test-SecretName -Name "PAGENTOS_VOICE_OPENAI_API_KEY") "accepts the M12 key name"
    # The owner's machine is tr-TR. A case-INsensitive -match folds "I" through the culture,
    # where it becomes dotless "ı" and falls outside [a-z], so every name containing an I
    # (…_API_KEY, …_ID) was refused on exactly the machine that matters. Ordinal -cmatch.
    $culture = [Threading.Thread]::CurrentThread.CurrentCulture
    try {
        [Threading.Thread]::CurrentThread.CurrentCulture = [Globalization.CultureInfo]::GetCultureInfo("tr-TR")
        Assert-True (Test-SecretName -Name "PAGENTOS_VOICE_OPENAI_API_KEY") "accepts a name containing I under tr-TR (Turkish-I case folding)"
        Assert-True (-not (Test-SecretName -Name "PAGENTOS_VOİCE")) "still rejects a real non-ASCII letter under tr-TR"
    }
    finally {
        [Threading.Thread]::CurrentThread.CurrentCulture = $culture
    }

    Write-Host "native-argument escaping, proven against a real CRT parser"
    foreach ($sample in @(
        'grep -v "^$name=" "$envf" > "$tmp"',
        'a "quoted \" inner" b',
        'trailing backslash \',
        'two \\ then "q" and \\\"',
        'plain'
    )) {
        Remove-Item -LiteralPath (Join-Path $fakeDir "argv.txt") -ErrorAction SilentlyContinue
        "" | & $fakeSsh "first" (ConvertTo-NativeArgument -Value $sample) | Out-Null
        $parsed = Get-FakeArgv
        Assert-True ($parsed.Count -eq 2 -and $parsed[1] -ceq $sample) "round-trips [$sample]"
    }

    Write-Host "set-cloud-secret.ps1 through the fake ssh"
    $common = @("-Name", $secretName, "-StoreRoot", $store, "-SshPath", $fakeSsh, "-BrokerHost", "pagentos-core", "-SkipVerify")
    $r = Invoke-ScriptUnderTest -Arguments $common
    $argv = Get-FakeArgv
    $argvText = $argv -join "`n"
    $stdin = Get-Content -LiteralPath (Join-Path $fakeDir "stdin.txt") -Raw
    Assert-True ($r.Exit -eq 0) "exits 0 when the fake host accepts"
    Assert-True ($stdin.Trim() -eq $secretValue) "the value reaches ssh on stdin, exactly as stored"
    Assert-True ($stdin -match "`r?`n$") "stdin ends with a newline so the remote 'read' completes"
    Assert-True ($argvText -notmatch [regex]::Escape($secretValue)) "the value is NOT in ssh's argv"
    Assert-True ($r.Output -notmatch [regex]::Escape($secretValue)) "the value is NOT in the script's output"
    Assert-True ($argv[-2] -eq "root@pagentos-core") "targets root@pagentos-core"
    Assert-True ($argvText -match "BatchMode=yes") "ssh runs in BatchMode (never prompts for a password)"
    $expected = Get-ExpectedRemote -Restart
    Assert-True ($argv[-1] -ceq $expected) "the remote command arrives byte-for-byte after 5.1 native quoting"
    Assert-True ($expected -match "name='$secretName'" -and $expected -match "/opt/pagentos/.env") "the remote command carries the name and the env path"
    Assert-True ($expected -match [regex]::Escape('value=${value%$' + "'" + '\r' + "'" + '}')) "the remote command strips the CR that a Windows pipe appends"
    Assert-True ($expected -match "docker compose -f docker-compose.prod.yml --env-file") "the remote command restarts the api through the deployment's compose invocation"
    Assert-True ($expected -match "read -r value" -and $expected -match "exit 65") "the remote command reads stdin and refuses unsafe values"
    Assert-True ($expected -match "mktemp" -and $expected -match "mv -f" -and $expected -match "chmod 600") "the env file is rewritten atomically, 0600"

    $r2 = Invoke-ScriptUnderTest -Arguments ($common + @("-SkipRestart"))
    $argv2 = Get-FakeArgv
    Assert-True ($r2.Exit -eq 0 -and $argv2[-1] -notmatch "docker compose" -and $argv2[-1] -ceq (Get-ExpectedRemote)) "-SkipRestart installs without restarting"

    Write-Host "-DryRun"
    $r3 = Invoke-ScriptUnderTest -Arguments @("-Name", "PAGENTOS_VOICE_OPENAI_API_KEY", "-StoreRoot", (Join-Path $script:Sandbox "no-such-store"), "-SshPath", $fakeSsh, "-DryRun")
    Assert-True ($r3.Exit -eq 0) "dry run succeeds without any stored secret"
    Assert-True (-not (Test-Path (Join-Path $fakeDir "argv.txt"))) "dry run opens no ssh"
    Assert-True ($r3.Output -match "PAGENTOS_VOICE_OPENAI_API_KEY" -and $r3.Output -match "Would run") "dry run shows the plan by name"

    Write-Host "remote refusal"
    $env:FAKE_SSH_EXIT = "65"
    try { $r4 = Invoke-ScriptUnderTest -Arguments $common } finally { Remove-Item Env:FAKE_SSH_EXIT -ErrorAction SilentlyContinue }
    Assert-True ($r4.Exit -ne 0 -and $r4.Output -match "exit 65") "a host refusal (65) fails the script with the typed reason"
    Assert-True ($r4.Output -notmatch [regex]::Escape($secretValue)) "...and still shows no value"

    Write-Host "validation before anything runs"
    $r5 = Invoke-ScriptUnderTest -Arguments @("-Name", "bad-name", "-StoreRoot", $store, "-SshPath", $fakeSsh, "-SkipVerify")
    Assert-True ($r5.Exit -ne 0 -and -not (Test-Path (Join-Path $fakeDir "argv.txt"))) "an invalid name is refused before ssh"
    $r6 = Invoke-ScriptUnderTest -Arguments ($common + @("-HostEnvFile", "/opt/x; rm -rf /"))
    Assert-True ($r6.Exit -ne 0 -and -not (Test-Path (Join-Path $fakeDir "argv.txt"))) "an unsafe remote path is refused before ssh"
}
finally {
    Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "cloud-secret tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
