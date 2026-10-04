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
