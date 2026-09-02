<#
.SYNOPSIS
    Windows PowerShell 5.1 StrictMode tests for the Cloud Core release path (ADR-0042):
    scripts/cloud/release-cloud-core.ps1 (Windows side, through a real native fake
    ssh/scp) and scripts/cloud/release-cloud-core.sh (host side, under Git Bash with a
    fake docker/curl and a sandbox in place of /opt/pagentos).

.DESCRIPTION
    The incident: the host ran a copied tree from the first deployment; the M12 code and
    the compose wiring for the provider key never reached it, so "restart the api" was a
    no-op. The release is therefore tested as a transaction:

      * preflight validates the new tree's compose against the host env file and leaves
        NOTHING behind;
      * a release keeps the previous tree and image, builds, migrates, recreates ONLY the
        api (--no-deps --force-recreate --wait), and verifies: health, key PRESENT inside
        the container (68), provider listed (69), one real self-test (70);
      * a failure after the swap ROLLS BACK: previous tree restored, previous image
        retagged, api recreated from it;
      * the Windows driver ships `git archive HEAD` only, deletes the local tarball, and
        maps the host's exit codes to typed failures.

    No network, no real ssh/scp, no real docker, no real host.

    Run: powershell -NoProfile -File scripts\tests\cloud-release.tests.ps1
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$driver = Join-Path $repoRoot "scripts\cloud\release-cloud-core.ps1"
$hostScript = Join-Path $repoRoot "scripts\cloud\release-cloud-core.sh"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$bash = Join-Path $env:ProgramFiles "Git\bin\bash.exe"

. (Join-Path $repoRoot "scripts\tests\lib\CloudFakes.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")

$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-cloud-release-tests-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) { $script:Passes++; Write-Host "  PASS  $Message" }
    else { $script:Failures++; Write-Host "  FAIL  $Message" -ForegroundColor Red }
}

# ------------------------------------------------------------- Windows driver
$fakeDir = Join-Path $script:Sandbox "fake"
$fakeSsh = New-NativeFakeSsh -Directory $fakeDir
$fakeScp = Join-Path $fakeDir "scp.exe"
Copy-Item -LiteralPath $fakeSsh -Destination $fakeScp
$headSha = (& git -C $repoRoot rev-parse HEAD).Trim()

function Invoke-Driver {
    param([string[]]$Arguments)
    Remove-Item -LiteralPath (Join-Path $fakeDir "calls.txt"), (Join-Path $fakeDir "argv.txt") -ErrorAction SilentlyContinue
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $driver @Arguments 2>&1 | Out-String
        $exit = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previous }
    $calls = if (Test-Path (Join-Path $fakeDir "calls.txt")) { (Get-Content (Join-Path $fakeDir "calls.txt") -Raw) -split "`n---`n" | Where-Object { $_.Trim() } } else { @() }
    return [pscustomobject]@{ Output = $output; Exit = $exit; Calls = @($calls) }
}

# ------------------------------------------------------------- host side
$hostBase = Join-Path $script:Sandbox "host"
$fakeBin = Join-Path $script:Sandbox "bin"
New-FakeDockerAndCurl -Directory $fakeBin
$u = { param($p) ($p -replace '\\', '/') }
# MSYS bash accepts C:/... for file arguments but wants /c/... inside PATH.
$posix = { param($p) $p = ($p -replace '\\', '/'); if ($p -match '^([A-Za-z]):(.*)$') { '/' + $Matches[1].ToLower() + $Matches[2] } else { $p } }

function Reset-Host {
    param([bool]$WithKey = $true, [bool]$WithCurrent = $true)
    Remove-Item -LiteralPath $hostBase -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path (Join-Path $hostBase "app.next\infra\docker"), (Join-Path $hostBase "app.next\scripts\cloud"), (Join-Path $hostBase "state") | Out-Null
    [IO.File]::WriteAllText((Join-Path $hostBase "app.next\infra\docker\docker-compose.prod.yml"), "services: {}`n")
    [IO.File]::WriteAllText((Join-Path $hostBase "app.next\NEW_TREE"), "new`n")
    if ($WithCurrent) {
        New-Item -ItemType Directory -Force -Path (Join-Path $hostBase "app\infra\docker") | Out-Null
        [IO.File]::WriteAllText((Join-Path $hostBase "app\infra\docker\docker-compose.prod.yml"), "services: {}`n")
        [IO.File]::WriteAllText((Join-Path $hostBase "app\OLD_TREE"), "old`n")
    }
    $envText = "PAGENTOS_BIND_IP=100.64.0.1`n" + $(if ($WithKey) { "PAGENTOS_VOICE_OPENAI_API_KEY=dummy-not-a-secret-value-0123456789abcdef`n" } else { "" })
    [IO.File]::WriteAllText((Join-Path $hostBase ".env"), $envText)
}

function Invoke-HostRelease {
    param([string]$Sha = "0123456789abcdef0123456789abcdef01234567", [string]$Mode = "", [hashtable]$Env = @{})
    $cmd = "PAGENTOS_ALLOW_NONROOT_ENV=1 PAGENTOS_BASE='$(& $u $hostBase)' PAGENTOS_API_CONTAINER=fake-api PAGENTOS_HEALTH_URL=http://fake/health FAKE_STATE='$(& $u (Join-Path $hostBase 'state'))' " +
           (($Env.GetEnumerator() | ForEach-Object { "$($_.Key)='$($_.Value)' " }) -join "") +
           "PATH='$(& $posix $fakeBin):'`"`$PATH`" bash '$(& $u $hostScript)' $Sha $Mode 2>&1"
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
    return [pscustomobject]@{ Output = $out; Exit = $exit; Calls = $calls }
}

try {
    Write-Host "Windows driver (release-cloud-core.ps1)"
    $d0 = Invoke-Driver -Arguments @("-SshPath", $fakeSsh, "-ScpPath", $fakeScp, "-DryRun", "-AllowDirty")
    Assert-True ($d0.Exit -eq 0 -and $d0.Calls.Count -eq 0 -and $d0.Output -match "git archive" -and $d0.Output -match $headSha) "dry run shows the plan with HEAD's sha and opens nothing"

    $env:FAKE_SSH_STDOUT = $headSha
    try { $d1 = Invoke-Driver -Arguments @("-SshPath", $fakeSsh, "-ScpPath", $fakeScp, "-SkipVerify", "-AllowDirty") } finally { Remove-Item Env:FAKE_SSH_STDOUT -ErrorAction SilentlyContinue }
    Assert-True ($d1.Exit -eq 0) "release through the fakes exits 0 (last line: $(($d1.Output.Trim() -split "`n")[-1]))"
    Assert-True ($d1.Calls.Count -eq 2) "exactly two native calls: scp then ssh"
    $scpArgs = ($d1.Calls[0] -split "`n"); $sshArgs = ($d1.Calls[1] -split "`n")
    $short = $headSha.Substring(0, 7)
    Assert-True (($scpArgs -join " ") -match "BatchMode=yes" -and $scpArgs[-1] -eq "root@pagentos-core:/tmp/pagentos-release-$short.tar" -and $scpArgs[-2] -match "pagentos-release-$short\.tar$") "scp uploads the HEAD archive to /tmp/pagentos-release-<sha>.tar in BatchMode"
    Assert-True (-not (Test-Path (Join-Path $env:TEMP "pagentos-release-$short.tar"))) "the local tarball is deleted afterwards"
    Assert-True ($sshArgs[-2] -eq "root@pagentos-core" -and $sshArgs[-1] -ceq "set -eu; rm -rf '/opt/pagentos/app.next'; mkdir -p '/opt/pagentos/app.next'; tar -xf '/tmp/pagentos-release-$short.tar' -C '/opt/pagentos/app.next'; rm -f '/tmp/pagentos-release-$short.tar'; bash '/opt/pagentos/app.next/scripts/cloud/release-cloud-core.sh' $headSha") "the remote command extracts to app.next and runs the shipped release script with the full sha (byte for byte after 5.1 quoting)"
    $d2 = Invoke-Driver -Arguments @("-SshPath", $fakeSsh, "-ScpPath", $fakeScp, "-Preflight", "-AllowDirty")
    Assert-True ($d2.Exit -eq 0 -and (($d2.Calls[-1] -split "`n")[-1]) -match " --preflight$" -and $d2.Output -match "preflight OK") "-Preflight passes --preflight to the host and reports validate-only"
    foreach ($case in @(@{ Exit = "71"; Pattern = "INVALID" }, @{ Exit = "68"; Pattern = "MISSING inside" }, @{ Exit = "70"; Pattern = "self-test" })) {
        $env:FAKE_SSH_EXIT = $case.Exit
        try { $dd = Invoke-Driver -Arguments @("-SshPath", $fakeSsh, "-ScpPath", $fakeScp, "-SkipVerify", "-AllowDirty") } finally { Remove-Item Env:FAKE_SSH_EXIT -ErrorAction SilentlyContinue }
        Assert-True ($dd.Exit -ne 0 -and $dd.Output -match "exit $($case.Exit)" -and $dd.Output -match $case.Pattern) "host exit $($case.Exit) -> typed failure"
    }

    if (-not (Test-Path -LiteralPath $bash)) {
        Write-Host "  SKIP  host-side release tests: Git Bash not found at $bash"
    }
    else {
        Write-Host "host side (release-cloud-core.sh under Git Bash, fake docker/curl)"
        Reset-Host
        $p = Invoke-HostRelease -Mode "--preflight"
        Assert-True ($p.Exit -eq 0 -and $p.Output -match "preflight: tree .* compose valid" -and $p.Output -match "PAGENTOS_VOICE_OPENAI_API_KEY on host: PRESENT, wired in compose: 1") "preflight validates the new tree against the host env file and reports wiring"
        Assert-True (-not (Test-Path (Join-Path $hostBase "app.next")) -and (Test-Path (Join-Path $hostBase "app\OLD_TREE")) -and -not ($p.Calls -match " up |build|alembic")) "preflight leaves nothing behind and changes nothing"

        Reset-Host
        $r = Invoke-HostRelease
        Assert-True ($r.Exit -eq 0 -and $r.Output -match "RELEASE OK: 0123456789abcdef0123456789abcdef01234567 is running as fake-api") "release exits 0"
        Assert-True ((Get-Content (Join-Path $hostBase "app\RELEASE") -Raw).Trim() -eq "0123456789abcdef0123456789abcdef01234567" -and (Test-Path (Join-Path $hostBase "app\NEW_TREE")) -and (Test-Path (Join-Path $hostBase "app.prev\OLD_TREE"))) "the new tree is live with its RELEASE marker; the previous tree is kept at app.prev"
        $calls = $r.Calls
        $iTag = [array]::IndexOf($calls, ($calls | Where-Object { $_ -match "^docker tag pagentos/cloud-core:local pagentos/cloud-core:prev" } | Select-Object -First 1))
        $iBuild = [array]::IndexOf($calls, ($calls | Where-Object { $_ -match " build api$" } | Select-Object -First 1))
        $iMigrate = [array]::IndexOf($calls, ($calls | Where-Object { $_ -match "alembic upgrade head" } | Select-Object -First 1))
        $iUp = [array]::IndexOf($calls, ($calls | Where-Object { $_ -match " up " } | Select-Object -First 1))
        $iPresent = [array]::IndexOf($calls, ($calls | Where-Object { $_ -match "echo PRESENT" } | Select-Object -First 1))
        $iSmoke = [array]::IndexOf($calls, ($calls | Where-Object { $_ -match "realtime_smoke" } | Select-Object -First 1))
        Assert-True ($iTag -ge 0 -and $iTag -lt $iBuild -and $iBuild -lt $iMigrate -and $iMigrate -lt $iUp -and $iUp -lt $iPresent -and $iPresent -lt $iSmoke) "order: keep prev image -> build -> migrate -> recreate api -> in-container key check -> provider self-test"
        $ups = @($calls | Where-Object { $_ -match " up " })
        Assert-True ($ups.Count -eq 1 -and $ups[0] -match "--no-deps --force-recreate --wait api$" -and -not ($calls -match "postgres|redis|minio|temporal")) "only the api workload is recreated; PostgreSQL/Redis/MinIO/Temporal are never named"
        Assert-True ($r.Output -match "in-container: PAGENTOS_VOICE_OPENAI_API_KEY PRESENT length=40 sha256=[0-9a-f]{12}" -and $r.Output -match "health lists provider openai-realtime" -and $r.Output -notmatch "dummy-not-a-secret-value") "verification reports PRESENT/length/fingerprint and the provider, never the value"

        Reset-Host
        $rb = Invoke-HostRelease -Env @{ FAKE_UP_EXIT = "1" }
        Assert-True ($rb.Exit -ne 0 -and $rb.Output -match "ROLLBACK") "a failed recreate rolls back"
        Assert-True ((Test-Path (Join-Path $hostBase "app\OLD_TREE")) -and -not (Test-Path (Join-Path $hostBase "app.prev"))) "...restoring the previous tree"
        Assert-True (($rb.Calls -match "^docker tag pagentos/cloud-core:prev pagentos/cloud-core:local").Count -eq 1 -and @($rb.Calls | Where-Object { $_ -match " up " }).Count -eq 2) "...retagging the previous image and recreating the api from it"

        Reset-Host
        $r68 = Invoke-HostRelease -Env @{ FAKE_NEVER_PRESENT = "1" }
        Assert-True ($r68.Exit -eq 68 -and $r68.Output -match "MISSING inside" -and (Test-Path (Join-Path $hostBase "app\OLD_TREE"))) "key on host but not in the recreated container -> exit 68 and rollback"

        Reset-Host
        $r71 = Invoke-HostRelease -Env @{ FAKE_CONFIG_EXIT = "1" }
        Assert-True ($r71.Exit -eq 71 -and (Test-Path (Join-Path $hostBase "app\OLD_TREE")) -and -not (Test-Path (Join-Path $hostBase "app.next")) -and -not ($r71.Calls -match " up |build")) "invalid compose in the new tree -> exit 71 before anything changes"

        Reset-Host -WithKey $false
        $rk = Invoke-HostRelease
        Assert-True ($rk.Exit -eq 0 -and $rk.Output -match "not on the host; realtime provider verification skipped" -and -not ($rk.Calls -match "realtime_smoke")) "without the key on the host the release still succeeds and says the provider check was skipped"
    }
}
finally {
    Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "cloud-release tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
