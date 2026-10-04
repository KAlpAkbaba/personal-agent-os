<#
.SYNOPSIS
    Windows PowerShell 5.1 StrictMode tests for the staging stack's scripts
    (scripts/staging/*.ps1, team/plans ADR "staging-stack"). Nothing here starts a container.
.DESCRIPTION
    Proven here:
      * deploy.ps1 refuses a commit that is on neither main nor the lead branch (a dangling
        commit made for the test), and an unknown sha - exit 2, nothing built;
      * deploy.ps1 accepts a commit on main (-CheckOnly: decides, builds nothing);
      * up.ps1 refuses to start under 6 GB free memory (exit 3, says so) and passes the
        memory check above it;
      * up.ps1 waits for `status: ok` in the health BODY: a fake health answering HTTP 200 +
        `degraded` makes it recreate the api once and then exit 1; `ok` -> exit 0, nothing
        recreated; a health url off this PC is refused;
      * seed.ps1 refuses any api that is not staging's own loopback port (the Cloud Core's
        tailnet address included) before it calls anything;
      * down.ps1 names only the staging compose file and project;
      * every staging script parses under Windows PowerShell 5.1 and sets StrictMode.
    The isolation of the compose file itself is services/api/tests/unit/test_staging_isolation.py.
    Run: powershell -NoProfile -File scripts\tests\staging.tests.ps1
#>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$stagingDir = Join-Path $repoRoot "scripts\staging"
$script:Failures = 0
$script:Passes = 0

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) { $script:Passes++; Write-Host "  PASS  $Message" }
    else { $script:Failures++; Write-Host "  FAIL  $Message" -ForegroundColor Red }
}

function Invoke-Script {
    param([string]$Name, [string[]]$Arguments)
    $path = Join-Path $stagingDir $Name
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $path @Arguments 2>&1 | Out-String
        $rc = $LASTEXITCODE
    } finally { $ErrorActionPreference = $prev }
    return [pscustomobject]@{ Rc = $rc; Out = $out }
}

function Invoke-Git {
    param([string[]]$Arguments)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $out = & git -C $repoRoot @Arguments 2>$null; $rc = $LASTEXITCODE } finally { $ErrorActionPreference = $prev }
    return [pscustomobject]@{ Rc = $rc; Out = ("$out").Trim() }
}

Write-Host "staging: deploy refusals"
# A commit made for this test only: its tree is HEAD's, its parent is nothing, no ref holds it.
$dangling = Invoke-Git @("commit-tree", "HEAD^{tree}", "-m", "staging test: a commit on no branch")
Assert-True ($dangling.Rc -eq 0 -and $dangling.Out -match '^[0-9a-f]{40}$') "a dangling commit was made for the test"
$r = Invoke-Script "deploy.ps1" @($dangling.Out, "-CheckOnly", "-NoFetch")
Assert-True ($r.Rc -eq 2) "deploy refuses a commit on neither main nor the lead branch (exit $($r.Rc))"
Assert-True ($r.Out -match "STAGING DEPLOY REFUSED: .* is not on main or on the lead branch") "deploy says why it refused"

$r = Invoke-Script "deploy.ps1" @("0000000000000000000000000000000000000bad", "-CheckOnly", "-NoFetch")
Assert-True ($r.Rc -eq 2 -and $r.Out -match "is not a commit") "deploy refuses an unknown sha (exit $($r.Rc))"

$mainRef = $null
foreach ($ref in @("origin/main")) {
    if ((Invoke-Git @("rev-parse", "--verify", "--quiet", "$ref^{commit}")).Rc -eq 0) { $mainRef = $ref; break }
}
if ($mainRef) {
    $mainSha = (Invoke-Git @("rev-parse", "$mainRef^{commit}")).Out
    $r = Invoke-Script "deploy.ps1" @($mainSha, "-CheckOnly", "-NoFetch")
    Assert-True ($r.Rc -eq 0 -and $r.Out -match "may be deployed") "deploy accepts the tip of $mainRef (exit $($r.Rc))"
} else {
    Write-Host "  SKIP  no main ref in this checkout (shallow CI clone): the accepting path is not exercised"
}

Write-Host "staging: up memory floor"
$r = Invoke-Script "up.ps1" @("-AssumeFreeMB", "4000", "-CheckOnly")
Assert-True ($r.Rc -eq 3) "up refuses with 4000 MB free (exit $($r.Rc))"
Assert-True ($r.Out -match "STAGING REFUSED: only 4000 MB of memory is free; staging needs at least 6144 MB") "up says how much is free and how much it needs"
$r = Invoke-Script "up.ps1" @("-AssumeFreeMB", "6143", "-CheckOnly")
Assert-True ($r.Rc -eq 3) "up refuses one megabyte under the floor (exit $($r.Rc))"
$r = Invoke-Script "up.ps1" @("-AssumeFreeMB", "8000", "-CheckOnly")
Assert-True ($r.Rc -ne 3 -and $r.Out -match "memory: 8000 MB free") "up passes the memory check with 8000 MB free (exit $($r.Rc))"
# Real run 2026-10-04: 13.2 GB free before deploy's builds, 3.6 GB after - the build's cache
# stays in the Docker VM - so deploy hands up.ps1 the figure it measured BEFORE building.
$r = Invoke-Script "up.ps1" @("-MeasuredFreeMB", "8000", "-CheckOnly")
Assert-True ($r.Rc -ne 3 -and $r.Out -match "memory: 8000 MB free \(measured by deploy before its build\)") "up takes deploy's pre-build measurement (exit $($r.Rc))"
$r = Invoke-Script "up.ps1" @("-MeasuredFreeMB", "4000", "-CheckOnly")
Assert-True ($r.Rc -eq 3) "up still refuses when deploy's pre-build measurement is under the floor (exit $($r.Rc))"
$deployText = [IO.File]::ReadAllText((Join-Path $stagingDir "deploy.ps1"))
Assert-True ($deployText -match '-MeasuredFreeMB \$freeBeforeBuild') "deploy passes its pre-build measurement to up"

Write-Host "staging: up waits for status ok, not HTTP 200"
# Inspector 2026-10-04: after a Docker restart the live staging api answered HTTP 200 with
# {"status":"degraded","failing_checks":"temporal_worker"} for ~2 h and up.ps1 counted it UP.
# A fake health (a loopback TcpListener in a job) answers every request with one fixed body;
# a fake docker (.cmd) records what up.ps1 asked compose to do.
function Start-FakeHealth {
    param([string]$Body)
    $probe = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
    $probe.Start(); $port = $probe.LocalEndpoint.Port; $probe.Stop()
    $job = Start-Job -ArgumentList $port, $Body -ScriptBlock {
        param($port, $body)
        $l = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $port)
        $l.Start()
        while ($true) {
            $c = $l.AcceptTcpClient()
            $s = $c.GetStream()
            $reader = New-Object IO.StreamReader($s)
            while ($true) { $line = $reader.ReadLine(); if ($null -eq $line -or $line -eq "") { break } }
            $b = [Text.Encoding]::UTF8.GetBytes($body)
            $h = [Text.Encoding]::ASCII.GetBytes("HTTP/1.1 200 OK`r`nContent-Type: application/json`r`nContent-Length: $($b.Length)`r`nConnection: close`r`n`r`n")
            $s.Write($h, 0, $h.Length); $s.Write($b, 0, $b.Length); $s.Flush(); $c.Close()
        }
    }
    return [pscustomobject]@{ Job = $job; Url = "http://127.0.0.1:$port/" }
}
$fakeDir = Join-Path ([IO.Path]::GetTempPath()) ("staging-tests-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $fakeDir | Out-Null
$fakeDocker = Join-Path $fakeDir "docker.cmd"
$dockerLog = Join-Path $fakeDir "docker-calls.log"
[IO.File]::WriteAllText($fakeDocker, "@echo %*>> `"%~dp0docker-calls.log`"`r`n@exit /b 0`r`n")
try {
    $degraded = Start-FakeHealth '{"status":"degraded","failing_checks":"temporal_worker"}'
    $r = Invoke-Script "up.ps1" @("-HealthOnly", "-TimeoutSec", "20", "-DegradedGraceSec", "3",
        "-ApiHealthUrl", "$($degraded.Url)v1/system/health", "-WebUrl", $degraded.Url, "-DockerExe", $fakeDocker)
    $calls = if (Test-Path $dockerLog) { [IO.File]::ReadAllText($dockerLog) } else { "" }
    Assert-True ($r.Rc -eq 1) "up exits 1 on a degraded api that answers HTTP 200 (exit $($r.Rc))"
    Assert-True ($r.Out -notmatch "STAGING UP") "up does not say STAGING UP on a degraded api"
    Assert-True ($r.Out -match "STAGING FAILED: api still degraded .*temporal_worker") "up says the api is still degraded and why"
    Assert-True ($calls -match "up -d --no-build --force-recreate api") "up recreated the degraded api once before giving up"
    Stop-Job $degraded.Job; Remove-Job -Force $degraded.Job

    Remove-Item -Force $dockerLog -ErrorAction SilentlyContinue
    $ok = Start-FakeHealth '{"status":"ok"}'
    $r = Invoke-Script "up.ps1" @("-HealthOnly", "-TimeoutSec", "20", "-DegradedGraceSec", "3",
        "-ApiHealthUrl", "$($ok.Url)v1/system/health", "-WebUrl", $ok.Url, "-DockerExe", $fakeDocker)
    Assert-True ($r.Rc -eq 0 -and $r.Out -match "STAGING UP") "up says STAGING UP when the body says ok (exit $($r.Rc))"
    Assert-True (-not (Test-Path $dockerLog)) "up recreates nothing when the api is ok"
    Stop-Job $ok.Job; Remove-Job -Force $ok.Job
} finally { Remove-Item -Recurse -Force $fakeDir -ErrorAction SilentlyContinue }
$r = Invoke-Script "up.ps1" @("-HealthOnly", "-ApiHealthUrl", "http://100.90.158.26:8001/v1/system/health")
Assert-True ($r.Rc -eq 1 -and $r.Out -match "not a loopback url") "up refuses a health url off this PC"
Assert-True ($deployText -match '& powershell -NoProfile -File \$upScript -TimeoutSec') "deploy waits for health through up.ps1 (status ok, degraded recreate)"

Write-Host "staging: seed only talks to staging"
foreach ($base in @("http://100.90.158.26:8001", "http://127.0.0.1:8001", "http://127.0.0.1:28000")) {
    $r = Invoke-Script "seed.ps1" @("-ApiBase", $base)
    Assert-True ($r.Rc -eq 1 -and $r.Out -match "STAGING SEED REFUSED") "seed refuses $base"
}

Write-Host "staging: down names only staging"
$downText = [IO.File]::ReadAllText((Join-Path $stagingDir "down.ps1"))
Assert-True ($downText -match "docker-compose\.staging\.yml" -and $downText -match '"pagentos-staging"') "down uses the staging compose file and project"
Assert-True ($downText -notmatch "docker-compose\.(dev|prod)\.yml") "down never names the dev or prod compose file"

Write-Host "staging: PS 5.1 parse + StrictMode"
foreach ($name in @("up.ps1", "down.ps1", "deploy.ps1", "seed.ps1")) {
    $path = Join-Path $stagingDir $name
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
    Assert-True (@($errors).Count -eq 0) "$name parses under PowerShell $($PSVersionTable.PSVersion)"
    Assert-True ([IO.File]::ReadAllText($path) -match "(?m)^Set-StrictMode -Version Latest") "$name sets StrictMode"
}

Write-Host ""
Write-Host "staging tests: $($script:Passes) passed, $($script:Failures) failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
