<#
.SYNOPSIS
    Windows PowerShell 5.1 StrictMode tests for the owner's provider-credential path:
    scripts/lib/SecretStore.ps1, scripts/cloud/set-cloud-secret.ps1 (Windows side) and
    scripts/cloud/install-env-secret.sh (host side, run here under Git Bash with a fake
    docker/curl).

.DESCRIPTION
    The incident (ADR-0042): the key was in /opt/pagentos/.env, the api was "restarted",
    and the running container still had no such variable - the host's compose did not
    wire it and a restart of an unchanged definition changes nothing. So the host-side
    transaction is tested as a TRANSACTION, with a fake docker whose container only
    exposes the variable after `up --no-deps --force-recreate`:

      1. a value stored like secret-store.ps1 -Set is read back exactly; names are
         checked ordinally (tr-TR Turkish-I);
      2. the Windows driver puts the value on ssh's STDIN and nowhere else, and the
         remote command survives 5.1 native quoting byte for byte (real CRT fake);
      3. host: env file updated atomically -> compose validated -> compose must WIRE the
         name (67) -> only the api recreated, --no-deps --force-recreate -> PRESENT inside
         the container or FAIL (68) -> health lists the provider (69) -> one provider
         self-test inside the container (70);
      4. -DryRun opens no ssh; a remote refusal is typed and shows no value.

    No network, no real ssh, no real docker, no real host.

    Run: powershell -NoProfile -File scripts\tests\cloud-secret.tests.ps1
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$scriptUnderTest = Join-Path $repoRoot "scripts\cloud\set-cloud-secret.ps1"
$hostScript = Join-Path $repoRoot "scripts\cloud\install-env-secret.sh"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$bash = Join-Path $env:ProgramFiles "Git\bin\bash.exe"

. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
. (Join-Path $repoRoot "scripts\tests\lib\CloudFakes.ps1")

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
$secure = ConvertTo-SecureString -String $secretValue -AsPlainText -Force
$secure | ConvertFrom-SecureString | Set-Content -Path (Join-Path $store "$secretName.dpapi") -Encoding ASCII

$fakeDir = Join-Path $script:Sandbox "fake"
$fakeSsh = New-NativeFakeSsh -Directory $fakeDir

function Invoke-ScriptUnderTest {
    param([string[]]$Arguments)
    Remove-Item -LiteralPath (Join-Path $fakeDir "argv.txt"), (Join-Path $fakeDir "stdin.txt") -ErrorAction SilentlyContinue
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

function Get-ExpectedRemote {
    param([string]$Name, [string]$Expect, [string]$Verify, [switch]$Restart)
    return New-RemoteSecretInstallCommand -Name $Name -EnvFile '/opt/pagentos/.env' -RepoRoot '/opt/pagentos/app' `
        -ExpectProvider $Expect -Verify $Verify -Restart:$Restart
}

# host-side transaction harness (Git Bash + fake docker/curl)
$hostBase = Join-Path $script:Sandbox "host"
$fakeBin = Join-Path $script:Sandbox "bin"
New-FakeDockerAndCurl -Directory $fakeBin
$u = { param($p) ($p -replace '\\', '/') }
# MSYS bash accepts C:/... for file arguments but wants /c/... inside PATH.
$posix = { param($p) $p = ($p -replace '\\', '/'); if ($p -match '^([A-Za-z]):(.*)$') { '/' + $Matches[1].ToLower() + $Matches[2] } else { $p } }

function Invoke-HostInstall {
    param([string]$Value, [hashtable]$Env = @{}, [string]$Expect = "openai-realtime", [string]$Recreate = "1",
          [string]$Verify = "uv run python scripts/realtime_smoke.py --mode minimal", [string]$Name = "PAGENTOS_VOICE_OPENAI_API_KEY")
    Remove-Item -LiteralPath $hostBase -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path (Join-Path $hostBase "app\infra\docker"), (Join-Path $hostBase "state") | Out-Null
    [IO.File]::WriteAllText((Join-Path $hostBase ".env"), "PAGENTOS_BIND_IP=100.64.0.1`nPAGENTOS_DB_PASSWORD=x`n")
    [IO.File]::WriteAllText((Join-Path $hostBase "app\infra\docker\docker-compose.prod.yml"), "services: {}`n")
    [IO.File]::WriteAllText((Join-Path $hostBase "value.txt"), "$Value`r`n")   # CRLF on purpose: what a Windows pipe sends
    $cmd = "PAGENTOS_ALLOW_NONROOT_ENV=1 PAGENTOS_API_CONTAINER=fake-api PAGENTOS_HEALTH_URL=http://fake/health FAKE_STATE='$(& $u (Join-Path $hostBase 'state'))' " +
           (($Env.GetEnumerator() | ForEach-Object { "$($_.Key)='$($_.Value)' " }) -join "") +
           "PATH='$(& $posix $fakeBin):'`"`$PATH`" bash '$(& $u $hostScript)' '$Name' '$(& $u (Join-Path $hostBase '.env'))' '$(& $u (Join-Path $hostBase 'app'))' '$Expect' $Recreate '$Verify' < '$(& $u (Join-Path $hostBase 'value.txt'))' 2>&1"
    # stderr is merged INSIDE bash: PowerShell would otherwise render it as error records
    # wrapped at console width, splitting a message where a regex expects it whole.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # 5.1 would hand bash the "$PATH" inside $cmd with its quotes unescaped.
        $out = & $bash -c (ConvertTo-NativeCallArgument -Value $cmd) 2>&1 | Out-String
        $exit = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    $calls = if (Test-Path (Join-Path $hostBase "state\calls.log")) { @(Get-Content (Join-Path $hostBase "state\calls.log")) } else { @() }
    return [pscustomobject]@{ Output = $out; Exit = $exit; Calls = $calls; EnvFile = (Get-Content (Join-Path $hostBase ".env") -Raw) }
}

try {
    Write-Host "SecretStore library"
    Assert-True ((Get-StoredSecretValue -Name $secretName -StoreRoot $store) -eq $secretValue) "round-trips a value stored like secret-store.ps1 -Set"
    $threw = $false; $msg = ""
    try { Get-StoredSecretValue -Name "NOPE_MISSING" -StoreRoot $store | Out-Null } catch { $threw = $true; $msg = $_.Exception.Message }
    Assert-True ($threw -and $msg -match "secret-store.ps1 -Set NOPE_MISSING") "a missing secret names the command to store it"
    Assert-True (-not (Test-SecretName -Name "bad-name")) "rejects a hyphenated name"
    Assert-True (-not (Test-SecretName -Name 'X; rm -rf /')) "rejects shell syntax in a name"
    $culture = [Threading.Thread]::CurrentThread.CurrentCulture
    try {
        [Threading.Thread]::CurrentThread.CurrentCulture = [Globalization.CultureInfo]::GetCultureInfo("tr-TR")
        Assert-True (Test-SecretName -Name "PAGENTOS_VOICE_OPENAI_API_KEY") "accepts a name containing I under tr-TR (Turkish-I case folding)"
        Assert-True (-not (Test-SecretName -Name "PAGENTOS_VOİCE")) "still rejects a real non-ASCII letter under tr-TR"
    }
    finally { [Threading.Thread]::CurrentThread.CurrentCulture = $culture }

    Write-Host "native-argument escaping, proven against a real CRT parser"
    foreach ($sample in @('grep -v "^$name=" "$envf" > "$tmp"', 'a "quoted \" inner" b', 'trailing backslash \', 'plain', "bash '/x/y.sh' 'A' 1 'uv run x --mode minimal'")) {
        Remove-Item -LiteralPath (Join-Path $fakeDir "argv.txt") -ErrorAction SilentlyContinue
        "" | & $fakeSsh "first" (ConvertTo-NativeCallArgument -Value $sample) | Out-Null
        $parsed = Get-FakeArgv
        Assert-True ($parsed.Count -eq 2 -and $parsed[1] -ceq $sample) "round-trips [$sample]"
    }

    Write-Host "Windows driver through the fake ssh"
    $common = @("-Name", $secretName, "-StoreRoot", $store, "-SshPath", $fakeSsh, "-BrokerHost", "pagentos-core", "-SkipVerify")
    $r = Invoke-ScriptUnderTest -Arguments $common
    $argv = Get-FakeArgv
    $stdin = Get-Content -LiteralPath (Join-Path $fakeDir "stdin.txt") -Raw
    Assert-True ($r.Exit -eq 0) "exits 0 when the fake host accepts"
    Assert-True ($stdin.Trim() -eq $secretValue -and $stdin -match "`r?`n$") "the value reaches ssh on stdin, exactly as stored, newline-terminated"
    Assert-True (($argv -join "`n") -notmatch [regex]::Escape($secretValue)) "the value is NOT in ssh's argv"
    Assert-True ($r.Output -notmatch [regex]::Escape($secretValue)) "the value is NOT in the script's output"
    Assert-True ($argv[-2] -eq "root@pagentos-core" -and ($argv -join " ") -match "BatchMode=yes") "targets root@pagentos-core in BatchMode"
    $expected = Get-ExpectedRemote -Name $secretName -Expect "" -Verify "" -Restart
    Assert-True ($argv[-1] -ceq $expected) "the remote command arrives byte-for-byte after 5.1 native quoting"
    Assert-True ($expected -ceq "bash '/opt/pagentos/app/scripts/cloud/install-env-secret.sh' '$secretName' '/opt/pagentos/.env' '/opt/pagentos/app' '' 1 ''") "the remote command is the shipped transaction script with name-only arguments"
    $r2 = Invoke-ScriptUnderTest -Arguments (@("-Name", "PAGENTOS_VOICE_OPENAI_API_KEY", "-StoreRoot", $store, "-SshPath", $fakeSsh, "-DryRun"))
    Assert-True ($r2.Exit -eq 0 -and $r2.Output -match "realtime_smoke.py --mode minimal" -and $r2.Output -match "'openai-realtime' 1") "for the M12 key the default self-test is one minimal client-secret mint and the expected provider is openai-realtime"
    Assert-True (-not (Test-Path (Join-Path $fakeDir "argv.txt"))) "dry run opens no ssh"

    Write-Host "remote refusals are typed and show no value"
    foreach ($case in @(@{ Exit = "67"; Pattern = "release-cloud-core" }, @{ Exit = "68"; Pattern = "MISSING inside the recreated" }, @{ Exit = "127"; Pattern = "release-cloud-core" })) {
        $env:FAKE_SSH_EXIT = $case.Exit
        try { $rr = Invoke-ScriptUnderTest -Arguments $common } finally { Remove-Item Env:FAKE_SSH_EXIT -ErrorAction SilentlyContinue }
        Assert-True ($rr.Exit -ne 0 -and $rr.Output -match "exit $($case.Exit)" -and $rr.Output -match $case.Pattern -and $rr.Output -notmatch [regex]::Escape($secretValue)) "host exit $($case.Exit) -> typed failure naming the remedy, no value"
    }
    $r5 = Invoke-ScriptUnderTest -Arguments @("-Name", "bad-name", "-StoreRoot", $store, "-SshPath", $fakeSsh, "-SkipVerify")
    Assert-True ($r5.Exit -ne 0 -and -not (Test-Path (Join-Path $fakeDir "argv.txt"))) "an invalid name is refused before ssh"

    if (-not (Test-Path -LiteralPath $bash)) {
        Write-Host "  SKIP  host-side transaction tests: Git Bash not found at $bash"
    }
    else {
        Write-Host "host-side transaction (install-env-secret.sh under Git Bash, fake docker/curl)"
        $h = Invoke-HostInstall -Value $secretValue
        Assert-True ($h.Exit -eq 0) "happy path exits 0 (last: $(($h.Output.Trim() -split "`n")[-1]))"
        Assert-True ($h.EnvFile -match "(?m)^PAGENTOS_VOICE_OPENAI_API_KEY=$([regex]::Escape($secretValue))$" -and $h.EnvFile -match "PAGENTOS_BIND_IP=100.64.0.1") "the env file gains exactly the line, other lines kept, CR stripped"
        Assert-True ($h.Output -notmatch [regex]::Escape($secretValue)) "the transaction's output never shows the value"
        Assert-True ($h.Output -match "in-container: PRESENT length=40 sha256=[0-9a-f]{12}") "runtime verification reports PRESENT + length + fingerprint only"
        Assert-True ($h.Output -match "health lists provider openai-realtime" -and $h.Output -match "provider self-test from this host: OK" -and $h.Output -match "SECRET OK") "provider listed and the in-container self-test ran"
        $order = @($h.Calls)
        $iConfigQ = [array]::IndexOf($order, ($order | Where-Object { $_ -match " config -q$" } | Select-Object -First 1))
        $iConfig = [array]::IndexOf($order, ($order | Where-Object { $_ -match " config$" } | Select-Object -First 1))
        $iUp = [array]::IndexOf($order, ($order | Where-Object { $_ -match " up " } | Select-Object -First 1))
        $iPresent = [array]::IndexOf($order, ($order | Where-Object { $_ -match "echo PRESENT" } | Select-Object -First 1))
        $iSmoke = [array]::IndexOf($order, ($order | Where-Object { $_ -match "realtime_smoke" } | Select-Object -First 1))
        Assert-True ($iConfigQ -ge 0 -and $iConfigQ -lt $iConfig -and $iConfig -lt $iUp -and $iUp -lt $iPresent -and $iPresent -lt $iSmoke) "order: config -q -> config (wiring) -> up -> in-container check -> self-test"
        $ups = @($order | Where-Object { $_ -match " up " })
        Assert-True ($ups.Count -eq 1 -and $ups[0] -match "--no-deps" -and $ups[0] -match "--force-recreate" -and $ups[0] -match "--wait api$" -and $ups[0] -notmatch "postgres|redis|minio|temporal") "only the api workload is recreated, --no-deps --force-recreate --wait; dependencies untouched"

        $h2 = Invoke-HostInstall -Value $secretValue -Env @{ FAKE_WIRED = "0" }
        Assert-True ($h2.Exit -eq 67 -and $h2.Output -match "does not wire" -and -not ($h2.Calls -match " up ")) "compose that does not wire the name -> exit 67, nothing recreated (the incident)"
        $h3 = Invoke-HostInstall -Value $secretValue -Env @{ FAKE_NEVER_PRESENT = "1" }
        Assert-True ($h3.Exit -eq 68 -and $h3.Output -match "MISSING inside the running") "env file has it, workload never exposes it -> exit 68 (a restart that changed nothing FAILS)"
        $h4 = Invoke-HostInstall -Value $secretValue -Env @{ FAKE_PROVIDER_LISTED = "0" }
        Assert-True ($h4.Exit -eq 69 -and $h4.Output -match "does not list openai-realtime") "provider absent from health -> exit 69"
        $h5 = Invoke-HostInstall -Value $secretValue -Env @{ FAKE_SMOKE_EXIT = "1" }
        Assert-True ($h5.Exit -eq 70 -and $h5.Output -match "self-test FAILED" -and $h5.Output -notmatch "Bearer" -and $h5.Output -notmatch [regex]::Escape($secretValue)) "provider self-test failure -> exit 70; stderr scrubbed of bearer lines, no value"
        $h6 = Invoke-HostInstall -Value $secretValue -Env @{ FAKE_CONFIG_EXIT = "1" }
        Assert-True ($h6.Exit -eq 71 -and -not ($h6.Calls -match " up ")) "invalid compose -> exit 71, nothing recreated"
        $h7 = Invoke-HostInstall -Value "has space"
        Assert-True ($h7.Exit -eq 65 -and $h7.EnvFile -notmatch "has space") "an unsafe value is refused before the env file is touched"
        $h8 = Invoke-HostInstall -Value ""
        Assert-True ($h8.Exit -eq 64) "empty stdin -> exit 64"
    }
}
finally {
    Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "cloud-secret tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
