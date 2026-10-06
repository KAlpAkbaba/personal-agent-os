<#
.SYNOPSIS
    The rerun of a red gate's failed steps (card gate-rerun-failed-steps,
    team/plans/gate-rerun-failed-steps-adr.md; scripts/lib/TeamGateRerun.ps1).

.DESCRIPTION
    Three halves.

    The decision (Get-TeamGateRerunDecision) is pure: records, the sha, the changed files, the
    ancestry, the gate's text and the red log in; full or partial out. The map's step names are
    held to the REAL scripts/quality-gate.ps1.

    The slice's log and the chain (Test-TeamGateRerunLog, Test-TeamGateRerunChain) over logs
    in the gate's own shape and a real git repository in TEMP.

    End to end: scripts/team/integrate.ps1 in a sandbox repository with fakes - the repository's
    fake claude, and a fake gate (written here) that declares its steps as Invoke-Step lines,
    honours -OnlyStep as the real gate does, and logs every call's sha and -OnlyStep.

    Run: powershell -NoProfile -File scripts\tests\team-gate-rerun.tests.ps1 [-Filter <regex>]
#>
[CmdletBinding()]
param([string]$Filter = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamIntegrate.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRelease.ps1")

$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$utf8 = New-Object System.Text.UTF8Encoding($false)
$script:Failures = 0
$script:Passes = 0
$sandboxes = New-Object System.Collections.ArrayList
foreach ($name in @("PAGENTOS_TEAM_URL", "PAGENTOS_TEAM_TOKEN_FILE", "PAGENTOS_TEAM_SEAT")) { Remove-Item -Path ("Env:" + $name) -ErrorAction SilentlyContinue }

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    if ($Filter -and $Name -notmatch $Filter) { return }
    try { & $Body; $script:Passes++; Write-Host "  PASS  $Name" }
    catch {
        $script:Failures++
        Write-Host "  FAIL  $Name" -ForegroundColor Red
        Write-Host "        $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Assert-Equal {
    param($Expected, $Actual, [string]$Because)
    if ($Expected -ne $Actual) { throw "$Because`n          expected: <$Expected>`n          actual  : <$Actual>" }
}

function Assert-True {
    param([bool]$Condition, [string]$Because)
    if (-not $Condition) { throw $Because }
}

# The gate's steps as the fake gate and the decisions name them (a real subset, held below to the real gate).
$fakeSteps = @("Required files", "Secret hygiene", "API lint (ruff)", "API unit tests", "Dev stack up (docker compose)", "Alembic upgrade head", "API integration tests", "Gate database dropped", "Web shell build")
$fakeGateText = (@($fakeSteps) | ForEach-Object { "Invoke-Step `"$_`" {" }) -join "`n"

function New-GateLog {
    <# A log in the real gate's shape: one section per step, FAILED lines for -Red, the summary, the last word. #>
    param([string[]]$Steps = $fakeSteps, [string[]]$Red = @(), [string]$RedLine = "E   AssertionError: broke", [switch]$NoLastWord, [string[]]$Skipped = @())
    $lines = New-Object System.Collections.ArrayList
    foreach ($s in $Steps) {
        if ($Skipped -contains $s) { continue }
        [void]$lines.Add(""); [void]$lines.Add("=== $s ===")
        if ($Red -contains $s) { [void]$lines.Add($RedLine); [void]$lines.Add("FAILED: the step failed") } else { [void]$lines.Add("ok") }
    }
    if ($NoLastWord) { return (($lines.ToArray()) -join "`n") }
    [void]$lines.Add(""); [void]$lines.Add("=== Quality gate summary ===")
    foreach ($s in $Steps) {
        $r = if ($Skipped -contains $s) { "SKIPPED" } elseif ($Red -contains $s) { "FAIL" } else { "PASS" }
        [void]$lines.Add("$s $r 1.0 0")
    }
    [void]$lines.Add($(if (@($Red).Count -gt 0) { "QUALITY GATE: FAIL" } else { "QUALITY GATE: PASS" }))
    return (($lines.ToArray()) -join "`n")
}

function New-RedRecord {
    param([int]$N = 1, [string]$Sha = "a" * 40, [string[]]$Steps = @("API integration tests"), [hashtable]$More = @{})
    $r = [ordered]@{ n = $N; branch = "integrate/c1"; at = "2026-10-07T00:00:00Z"; result = "red"; sha = $Sha; steps = @($Steps); log = "team/reports/c1/gate-$N.log"; applied = $true }
    foreach ($k in @($More.Keys)) { $r[$k] = $More[$k] }
    return [pscustomobject]$r
}

$shaA = "a" * 40
$shaB = "b" * 40

# ============================================================================ the decision

Write-Host ""
Write-Host "the decision: full gate or only the red steps"

Test-Case "a fix inside the map: only the red step, what it needs and the cheap steps - never another heavy step" {
    $d = Get-TeamGateRerunDecision -Records @(New-RedRecord) -Sha $shaB -Changed @("services/api/tests/integration/test_x.py") -Descends $true -GateText $fakeGateText -LastLogText (New-GateLog -Red @("API integration tests"))
    Assert-Equal -Expected "partial" -Actual $d.Mode -Because $d.Why
    foreach ($s in @("API integration tests", "Dev stack up (docker compose)", "Alembic upgrade head", "Gate database dropped", "Required files", "API lint (ruff)")) {
        Assert-True -Condition (@($d.Steps) -contains $s) -Because "$s is in the rerun: $($d.Steps -join '; ')"
    }
    foreach ($s in @("API unit tests", "Web shell build")) { Assert-True -Condition (@($d.Steps) -notcontains $s) -Because "$s was green and the fix is not its: $($d.Steps -join '; ')" }
    Assert-Equal -Expected $shaA -Actual $d.From -Because "the rerun rests on the red gate's sha"
    Assert-Equal -Expected 1 -Actual $d.FromNumber -Because "and its record"
}

Test-Case "a fix outside the map, in code a green step shares, in the gate, a lock file or a migration: the full gate" {
    $log = New-GateLog -Red @("API integration tests")
    foreach ($file in @("services/browser-agent/src/x.ts", "services/api/app/conversations/service.py", "scripts/quality-gate.ps1", "services/api/uv.lock", "services/api/alembic/versions/0073_x.py", "apps/web/package.json", "scripts/lib/TeamQueue.ps1")) {
        $d = Get-TeamGateRerunDecision -Records @(New-RedRecord) -Sha $shaB -Changed @("services/api/tests/integration/test_x.py", $file) -Descends $true -GateText $fakeGateText -LastLogText $log
        Assert-Equal -Expected "full" -Actual $d.Mode -Because "$file must send it to the full gate: $($d.Why)"
    }
}

Test-Case "a family is rerun whole: shared test support with one of its steps red reruns both; app code is the full gate even when both were red" {
    $d = Get-TeamGateRerunDecision -Records @(New-RedRecord -Steps @("API unit tests")) -Sha $shaB -Changed @("services/api/tests/selfmodel_support.py") -Descends $true -GateText $fakeGateText -LastLogText (New-GateLog -Red @("API unit tests"))
    Assert-Equal -Expected "partial" -Actual $d.Mode -Because $d.Why
    Assert-True -Condition (@($d.Steps) -contains "API integration tests" -and @($d.Steps) -contains "API unit tests") -Because "what both test steps use is judged by both: $($d.Steps -join '; ')"
    $both = @("API unit tests", "API integration tests")
    $app = Get-TeamGateRerunDecision -Records @(New-RedRecord -Steps $both) -Sha $shaB -Changed @("services/api/app/x.py") -Descends $true -GateText $fakeGateText -LastLogText (New-GateLog -Red $both)
    Assert-Equal -Expected "full" -Actual $app.Mode -Because "app code is shared by other steps: $($app.Why)"
}

Test-Case "an environment's red with no code change: the same steps on the same sha; another red on the same content: the full gate" {
    $envLog = New-GateLog -Red @("API integration tests") -RedLine "E   psycopg.OperationalError: FATAL: sorry, too many clients already"
    $d = Get-TeamGateRerunDecision -Records @(New-RedRecord) -Sha $shaA -GateText $fakeGateText -LastLogText $envLog
    Assert-Equal -Expected "partial" -Actual $d.Mode -Because $d.Why
    Assert-True -Condition ([bool]$d.SameContent) -Because "the same content"
    Assert-True -Condition (@($d.Steps) -contains "API integration tests") -Because "the red step again"
    $codeLog = New-GateLog -Red @("API integration tests") -RedLine "E   AssertionError: 3 != 4"
    $again = Get-TeamGateRerunDecision -Records @(New-RedRecord) -Sha $shaA -GateText $fakeGateText -LastLogText $codeLog
    Assert-Equal -Expected "full" -Actual $again.Mode -Because "a code red on unchanged content is not rerun in part: $($again.Why)"
    foreach ($line in @("FAILED: dev-up.ps1: Cannot connect to the Docker daemon at npipe", "FAILED: pnpm: EPERM node_modules junction")) {
        Assert-True -Condition (Test-TeamGateEnvironmentRed -FailureText $line) -Because "an environment's words: $line"
    }
}

Test-Case "never two partial reruns in a row: after a rerun's red, the cleared mark, a guards red or an unfinished gate, the full gate" {
    $log = New-GateLog -Red @("API integration tests")
    $file = @("services/api/tests/integration/test_x.py")
    $afterRerun = Get-TeamGateRerunDecision -Records @((New-RedRecord), (New-RedRecord -N 2 -Sha $shaB -More @{ rerun_of = 1; gate_sha = $shaA })) -Sha ("c" * 40) -Changed $file -Descends $true -GateText $fakeGateText -LastLogText $log
    Assert-Equal -Expected "full" -Actual $afterRerun.Mode -Because "the second red in a row: $($afterRerun.Why)"
    $cleared = Get-TeamGateRerunDecision -Records @((New-RedRecord), [pscustomobject]@{ n = 2; branch = "integrate/c1"; result = "cleared" }) -Sha $shaB -Changed $file -Descends $true -GateText $fakeGateText -LastLogText $log
    Assert-Equal -Expected "full" -Actual $cleared.Mode -Because "the lead looked: a new full gate"
    $guards = Get-TeamGateRerunDecision -Records @(New-RedRecord -More @{ by = "guards" }) -Sha $shaB -Changed $file -Descends $true -GateText $fakeGateText -LastLogText $log
    Assert-Equal -Expected "full" -Actual $guards.Mode -Because "a guards red is not the gate's"
    $unfinished = Get-TeamGateRerunDecision -Records @(New-RedRecord) -Sha $shaB -Changed $file -Descends $true -GateText $fakeGateText -LastLogText (New-GateLog -Red @("API integration tests") -NoLastWord)
    Assert-Equal -Expected "full" -Actual $unfinished.Mode -Because "a gate that never said FAIL did not run every step"
    $notDown = Get-TeamGateRerunDecision -Records @(New-RedRecord) -Sha $shaB -Changed $file -Descends $false -GateText $fakeGateText -LastLogText $log
    Assert-Equal -Expected "full" -Actual $notDown.Mode -Because "B does not descend from A"
}

Test-Case "a document a test reads: its readers' steps are added; a reader no step owns sends it to the full gate" {
    $log = New-GateLog -Red @("API unit tests")
    $d = Get-TeamGateRerunDecision -Records @(New-RedRecord -Steps @("API unit tests")) -Sha $shaB -Changed @("services/api/tests/unit/test_x.py", "docs/HANDOFF.md") -Descends $true -GateText $fakeGateText -LastLogText $log -Readers @{ "docs/HANDOFF.md" = [string[]]@("API unit tests", "API integration tests") }
    Assert-Equal -Expected "partial" -Actual $d.Mode -Because $d.Why
    Assert-True -Condition (@($d.Steps) -contains "API integration tests") -Because "a reader's step is added: $($d.Steps -join '; ')"
    $none = Get-TeamGateRerunDecision -Records @(New-RedRecord -Steps @("API unit tests")) -Sha $shaB -Changed @("docs/HANDOFF.md") -Descends $true -GateText $fakeGateText -LastLogText $log -Readers @{ "docs/HANDOFF.md" = [string[]]@() }
    Assert-Equal -Expected "partial" -Actual $none.Mode -Because "nobody reads it: $($none.Why)"
    $unknown = Get-TeamGateRerunDecision -Records @(New-RedRecord -Steps @("API unit tests")) -Sha $shaB -Changed @("docs/HANDOFF.md") -Descends $true -GateText $fakeGateText -LastLogText $log -Readers @{ "docs/HANDOFF.md" = $null }
    Assert-Equal -Expected "full" -Actual $unknown.Mode -Because "an unknown reader"
}

Test-Case "who reads a document: an API test reads it for both API steps, a gate suite for its step; a suite the gate does not run is not known" {
    $tree = Join-Path $env:TEMP ("pagentos-readers-" + [guid]::NewGuid().ToString("N").Substring(0, 10))
    [void]$sandboxes.Add($tree)
    foreach ($f in @("services\api\tests\unit", "scripts\tests", "scripts\lib")) { [void](New-Item -ItemType Directory -Force -Path (Join-Path $tree $f)) }
    Set-Content -LiteralPath (Join-Path $tree "services\api\tests\unit\test_adr.py") -Value "DOC = 'docs/DECISIONS.md'" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $tree "scripts\lib\Lists.ps1") -Value "'docs/HANDOFF.md', 'docs/DECISIONS.md'" -Encoding ASCII
    $gate = $fakeGateText + "`nInvoke-Step `"Agent team area widening rules (PS5.1, no model)`" {`n  `$s = Join-Path `$repoRoot `"scripts\tests\team-area.tests.ps1`"`n}"
    $found = Get-TeamGateReaderSteps -Tree $tree -Path "docs/DECISIONS.md" -GateText $gate
    Assert-True -Condition ($found.Known -and @($found.Steps) -contains "API unit tests" -and @($found.Steps) -contains "API integration tests") -Because "an API test reads it: $($found.Steps -join '; ')"
    $nobody = Get-TeamGateReaderSteps -Tree $tree -Path "docs/HANDOFF.md" -GateText $gate
    Assert-True -Condition ($nobody.Known -and @($nobody.Steps).Count -eq 0) -Because "a library's list of names is not a reader"
    Set-Content -LiteralPath (Join-Path $tree "scripts\tests\team-area.tests.ps1") -Value "# reads docs/HANDOFF.md" -Encoding ASCII
    $suite = Get-TeamGateReaderSteps -Tree $tree -Path "docs/HANDOFF.md" -GateText $gate
    Assert-True -Condition ($suite.Known -and @($suite.Steps) -contains "Agent team area widening rules (PS5.1, no model)") -Because "the suite's step: $($suite.Steps -join '; ')"
    Set-Content -LiteralPath (Join-Path $tree "scripts\tests\not-in-gate.tests.ps1") -Value "# reads docs/HANDOFF.md" -Encoding ASCII
    $unknown = Get-TeamGateReaderSteps -Tree $tree -Path "docs/HANDOFF.md" -GateText $gate
    Assert-True -Condition (-not $unknown.Known) -Because "a suite the gate does not run reads it: not known"
}

Test-Case "the map is the real gate's: every step it names, needs or always runs is a step of scripts/quality-gate.ps1" {
    $real = @(Get-TeamGateStepNames -GateText ([System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\quality-gate.ps1"))))
    Assert-True -Condition (@($real).Count -gt 30) -Because "the real gate's steps are read: $(@($real).Count)"
    $named = @($script:TeamGateRerunAlways) + @($script:TeamGateRerunNeeds.Keys) + @($script:TeamGateRerunNeeds.Values | ForEach-Object { @($_) })
    foreach ($family in $script:TeamGateRerunFamilies) { $named += @($family.Steps) }
    foreach ($pattern in $named) { Assert-True -Condition (@($real | Where-Object { $_ -like $pattern }).Count -gt 0) -Because "'$pattern' names no step of the real gate" }
    foreach ($s in $fakeSteps) { Assert-True -Condition ($real -contains $s) -Because "the fake gate's '$s' is a real step" }
    $suites = @(Get-TeamGateSuiteFamilies -GateText ([System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\quality-gate.ps1"))))
    Assert-True -Condition (@($suites | Where-Object { $_.Paths[0] -eq "scripts/tests/team-integrate.tests.ps1" -and $_.Steps[0] -like "Agent team integrate step*" }).Count -eq 1) -Because "a suite file maps to the step that runs it"
}

Test-Case "the -OnlyStep patterns: a name with a comma is cut before it; the list is one comma-joined argument" {
    Assert-Equal -Expected "Agent team automatic release*" -Actual (ConvertTo-TeamOnlyStepPattern -Name "Agent team automatic release, resume after a limit, staging scripts (PS5.1, fakes)") -Because "cut at the comma"
    Assert-Equal -Expected "API unit tests" -Actual (ConvertTo-TeamOnlyStepPattern -Name "API unit tests") -Because "kept"
    $a = @(Get-TeamGateRerunArguments -Patterns @("API unit tests", "Required files"))
    Assert-Equal -Expected 2 -Actual @($a).Count -Because "one switch, one value"
    Assert-Equal -Expected "API unit tests,Required files" -Actual $a[1] -Because "comma-joined"
}

# ============================================================================ the slice's log and the chain

Write-Host ""
Write-Host "the slice's log and the chain the release step checks"

Test-Case "a slice is green only when every step it was asked for ran and passed" {
    $steps = @("Required files", "API integration tests")
    $ok = Test-TeamGateRerunLog -Text (New-GateLog -Skipped @($fakeSteps | Where-Object { $steps -notcontains $_ })) -Steps $steps
    Assert-True -Condition $ok.Ok -Because $ok.Why
    $skipped = Test-TeamGateRerunLog -Text (New-GateLog -Skipped @($fakeSteps | Where-Object { $_ -ne "Required files" })) -Steps $steps
    Assert-True -Condition (-not $skipped.Ok) -Because "a step that was skipped did not pass"
    $unmatched = Test-TeamGateRerunLog -Text ((New-GateLog) + "`nOnlyStep: no step matched 'API x'") -Steps $steps
    Assert-True -Condition (-not $unmatched.Ok) -Because "a pattern that matched nothing"
    $red = Test-TeamGateRerunLog -Text (New-GateLog -Red @("API integration tests")) -ExitCode 1 -Steps $steps
    Assert-True -Condition (-not $red.Ok) -Because "red is red"
}

function New-ChainRepo {
    $root = Join-Path $env:TEMP ("pagentos-rerun-" + [guid]::NewGuid().ToString("N").Substring(0, 10))
    [void]$sandboxes.Add($root)
    [void](New-Item -ItemType Directory -Force -Path $root)
    foreach ($a in @(@("init", "-q", "-b", "main"), @("config", "user.name", "t"), @("config", "user.email", "t@example.invalid"))) { [void](Invoke-TeamGit -WorkingDirectory $root -Arguments $a) }
    $commit = { param([string]$Name) Set-Content -LiteralPath (Join-Path $root $Name) -Value $Name -Encoding ASCII; [void](Invoke-TeamGit -WorkingDirectory $root -Arguments @("add", "-A")); [void](Invoke-TeamGit -WorkingDirectory $root -Arguments @("commit", "-q", "-m", $Name)); (Invoke-TeamGit -WorkingDirectory $root -Arguments @("rev-parse", "HEAD")).StdOut.Trim() }
    $a = & $commit "a.txt"
    $b = & $commit "b.txt"
    [void](Invoke-TeamGit -WorkingDirectory $root -Arguments @("checkout", "-q", "-b", "side", "$a~0"))
    [void](Invoke-TeamGit -WorkingDirectory $root -Arguments @("reset", "-q", "--hard", $a))
    $side = & $commit "side.txt"
    $reports = Join-Path $root "reports\c1"
    [void](New-Item -ItemType Directory -Force -Path $reports)
    return [pscustomobject]@{ Root = $root; A = $a; B = $b; Side = $side; Reports = $reports }
}

function Write-ChainRecords {
    param($Repo, [string]$B, [string[]]$RerunSteps, [string]$ALog = "", [string]$BLog = "")
    $red = New-RedRecord -N 1 -Sha $Repo.A -Steps @("API integration tests")
    Write-TeamJson -Path (Join-Path $Repo.Reports "gate-1.json") -Document $red
    if (-not $ALog) { $ALog = New-GateLog -Red @("API integration tests") }
    [System.IO.File]::WriteAllText((Join-Path $Repo.Reports "gate-1.log"), $ALog, $utf8)
    if (-not $BLog) { $BLog = New-GateLog -Skipped @($fakeSteps | Where-Object { $RerunSteps -notcontains $_ }) }
    [System.IO.File]::WriteAllText((Join-Path $Repo.Reports "gate-2.log"), $BLog, $utf8)
    $green = [pscustomobject]@{ n = 2; branch = "integrate/c1"; at = "2026-10-07T01:00:00Z"; result = "green"; sha = $B; main = "m" * 40; log = "team/reports/c1/gate-1.log"
        rerun_of = 1; gate_sha = $Repo.A; gate_log = "team/reports/c1/gate-1.log"; rerun_log = "team/reports/c1/gate-2.log"; rerun_steps = @($RerunSteps) }
    Write-TeamJson -Path (Join-Path $Repo.Reports "gate-2.json") -Document $green
    return $green
}

Test-Case "release: a 'gate A + rerun B' record is accepted only when B descends from A and both logs say what it says" {
    $repo = New-ChainRepo
    $steps = @("Required files", "Dev stack up (docker compose)", "Alembic upgrade head", "API integration tests", "Gate database dropped")
    $ok = Test-TeamGateRerunChain -RepoRoot $repo.Root -Directory $repo.Reports -Record (Write-ChainRecords -Repo $repo -B $repo.B -RerunSteps $steps)
    Assert-True -Condition $ok.Ok -Because "chained: $($ok.Why)"
    $side = Test-TeamGateRerunChain -RepoRoot $repo.Root -Directory $repo.Reports -Record (Write-ChainRecords -Repo $repo -B $repo.Side -RerunSteps $steps)
    Assert-True -Condition ($side.Ok) -Because "side descends from A too (A is its parent): $($side.Why)"
    # B that is NOT a descendant: A is the side commit, B is main's tip.
    $red = New-RedRecord -N 1 -Sha $repo.Side -Steps @("API integration tests")
    Write-TeamJson -Path (Join-Path $repo.Reports "gate-1.json") -Document $red
    $record = Read-TeamJson -Path (Join-Path $repo.Reports "gate-2.json")
    $record.gate_sha = $repo.Side; $record.sha = $repo.B
    $unchained = Test-TeamGateRerunChain -RepoRoot $repo.Root -Directory $repo.Reports -Record $record
    Assert-True -Condition (-not $unchained.Ok) -Because "B does not descend from A: refused"
    $missingStep = Test-TeamGateRerunChain -RepoRoot $repo.Root -Directory $repo.Reports -Record (Write-ChainRecords -Repo $repo -B $repo.B -RerunSteps @("Required files"))
    Assert-True -Condition (-not $missingStep.Ok) -Because "the red step was not rerun: refused"
    $otherRed = Test-TeamGateRerunChain -RepoRoot $repo.Root -Directory $repo.Reports -Record (Write-ChainRecords -Repo $repo -B $repo.B -RerunSteps $steps -ALog (New-GateLog -Red @("API integration tests", "Web shell build")))
    Assert-True -Condition (-not $otherRed.Ok) -Because "A's log has a red step the record does not name: refused"
    $redSlice = Test-TeamGateRerunChain -RepoRoot $repo.Root -Directory $repo.Reports -Record (Write-ChainRecords -Repo $repo -B $repo.B -RerunSteps $steps -BLog (New-GateLog -Red @("API integration tests")))
    Assert-True -Condition (-not $redSlice.Ok) -Because "a red slice: refused"
}

Test-Case "release (scripts/lib/TeamRelease.ps1 - ALAN_ISTEGI): Find-TeamReleaseGate passes a chained 'gate + rerun' record and refuses an unchained one" {
    $repo = New-ChainRepo
    $steps = @("Required files", "Dev stack up (docker compose)", "Alembic upgrade head", "API integration tests", "Gate database dropped")
    [void](Write-ChainRecords -Repo $repo -B $repo.B -RerunSteps $steps)
    $found = Find-TeamReleaseGate -ReportsRoot (Join-Path $repo.Root "reports") -Sha ("m" * 40)
    Assert-True -Condition ([bool]$found.Found -and [bool]$found.Pass) -Because "a chained rerun record is the gate's evidence: $($found.Why)"
    $record = Read-TeamJson -Path (Join-Path $repo.Reports "gate-2.json")
    $record.sha = $repo.Side
    Write-TeamJson -Path (Join-Path $repo.Reports "gate-1.json") -Document (New-RedRecord -N 1 -Sha $repo.B -Steps @("API integration tests"))
    $record.gate_sha = $repo.B
    Write-TeamJson -Path (Join-Path $repo.Reports "gate-2.json") -Document $record
    $refused = Find-TeamReleaseGate -ReportsRoot (Join-Path $repo.Root "reports") -Sha ("m" * 40)
    Assert-True -Condition (-not [bool]$refused.Pass) -Because "an unchained one is not"
}

# ============================================================================ end to end: integrate.ps1

Write-Host ""
Write-Host "end to end: scripts/team/integrate.ps1 with a fake gate"

$fakeGateSource = @'
[CmdletBinding()]
param([string[]]$OnlyStep = @())
$ErrorActionPreference = "Continue"
$here = (Get-Location).ProviderPath
$head = (& git.exe rev-parse HEAD 2>$null | Out-String).Trim()
$only = @($OnlyStep | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
Add-Content -LiteralPath $env:PAGENTOS_RERUN_GATE_LOG -Value ("$head|" + ($only -join ',')) -Encoding UTF8
$script:results = New-Object System.Collections.ArrayList
$script:failed = $false
function Invoke-Step {
    param([string]$Name, [scriptblock]$Action)
    if ($only.Count -gt 0 -and @($only | Where-Object { $Name -like $_ }).Count -eq 0) { [void]$script:results.Add("$Name SKIPPED 0 0"); return }
    Write-Host ""
    Write-Host "=== $Name ==="
    $ok = $true
    try { & $Action } catch { Write-Host "FAILED: $($_.Exception.Message)"; $ok = $false; $script:failed = $true }
    [void]$script:results.Add("$Name " + $(if ($ok) { "PASS" } else { "FAIL" }) + " 1.0 0")
}
Invoke-Step "Required files" { "all present" }
Invoke-Step "Secret hygiene" { "clean" }
Invoke-Step "API lint (ruff)" { "ok" }
Invoke-Step "API unit tests" { "5400 passed" }
Invoke-Step "Dev stack up (docker compose)" { "up" }
Invoke-Step "Alembic upgrade head" { "head" }
Invoke-Step "API integration tests" {
    if ($env:PAGENTOS_RERUN_ENV_FLAG -and (Test-Path -LiteralPath $env:PAGENTOS_RERUN_ENV_FLAG)) {
        Write-Host "E   psycopg.OperationalError: FATAL: sorry, too many clients already"
        throw "pytest (integration) exited with code 1"
    }
    $broken = Join-Path $here "services\api\tests\integration\broken.txt"
    if (Test-Path -LiteralPath $broken) {
        Write-Host "FAILED services/api/tests/integration/broken.txt::test_holds - AssertionError"
        throw "pytest (integration) exited with code 1"
    }
    "300 passed"
}
Invoke-Step "Gate database dropped" { "dropped" }
Invoke-Step "Web shell build" { "built" }
Write-Host ""
Write-Host "=== Quality gate summary ==="
foreach ($r in $script:results) { Write-Host $r }
if ($script:failed) { Write-Host "QUALITY GATE: FAIL"; exit 1 }
Write-Host "QUALITY GATE: PASS"
exit 0
'@

function Invoke-SandboxGit {
    param([string]$Root, [string[]]$Arguments)
    $result = Invoke-TeamGit -WorkingDirectory $Root -Arguments $Arguments
    if (-not $result.Success) { throw "git $($Arguments -join ' '): $($result.StdErr)" }
    return $result.StdOut.Trim()
}

function New-Sandbox {
    <# main, and one task's branch (a file in services/api/tests/integration, and -Broken there) merged into integrate/c1. #>
    param([switch]$Broken)
    $root = Join-Path $env:TEMP ("pagentos-rerun-e2e-" + [guid]::NewGuid().ToString("N").Substring(0, 10))
    $tools = "$root-tools"
    [void]$sandboxes.Add($root); [void]$sandboxes.Add($tools)
    foreach ($folder in @("scripts\lib", "scripts\team", "scripts\tests\lib", ".claude\agents", "team", "services\api\tests\integration", "docs")) { [void](New-Item -ItemType Directory -Force -Path (Join-Path $root $folder)) }
    [void](New-Item -ItemType Directory -Force -Path $tools)
    foreach ($name in @("NativeProcess.ps1", "TeamQueue.ps1", "TeamRun.ps1", "HttpJson.ps1", "TeamIntegrate.ps1", "TeamGateRerun.ps1")) {
        $source = Join-Path $repoRoot "scripts\lib\$name"
        if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination (Join-Path $root "scripts\lib\$name") }
    }
    Copy-Item -Path (Join-Path $repoRoot "scripts\team\*.ps1") -Destination (Join-Path $root "scripts\team")
    Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\tests\lib\fake-claude.ps1") -Destination (Join-Path $root "scripts\tests\lib\fake-claude.ps1")
    foreach ($role in @("lead", "researcher", "integrator", "worker", "inspector")) { Copy-Item -LiteralPath (Join-Path $repoRoot ".claude\agents\$role.md") -Destination (Join-Path $root ".claude\agents\$role.md") }
    Set-Content -LiteralPath (Join-Path $root ".gitignore") -Value ".claude/worktrees/`nteam/reports/" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $root "services\api\tests\integration\README.txt") -Value "the area" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $root "docs\DECISIONS.md") -Value "# decisions" -Encoding ASCII
    [System.IO.File]::WriteAllText((Join-Path $tools "gate.ps1"), $fakeGateSource, $utf8)
    Set-Content -LiteralPath (Join-Path $tools "docker-ok.cmd") -Value "@exit /b 0" -Encoding ASCII
    foreach ($tool in @("uv", "pnpm")) { Set-Content -LiteralPath (Join-Path $tools "$tool.cmd") -Value "@exit /b 0" -Encoding ASCII }
    Write-TeamJson -Path (Join-Path $root "team\lock.json") -Document (New-TeamLockReleased)
    Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document ([pscustomobject]@{ version = 1; tasks = @() })
    foreach ($a in @(@("init", "-q", "-b", "main"), @("config", "user.name", "team test"), @("config", "user.email", "team@example.invalid"), @("config", "core.autocrlf", "false"), @("add", "-A"), @("commit", "-q", "-m", "the sandbox"))) { [void](Invoke-SandboxGit -Root $root -Arguments $a) }
    $branch = "team/c1/worker-task-one"
    [void](Invoke-SandboxGit -Root $root -Arguments @("branch", $branch, "main"))
    $tree = Join-Path $tools "wt-task-one"
    [void](Invoke-SandboxGit -Root $root -Arguments @("worktree", "add", "-q", $tree, $branch))
    Set-Content -LiteralPath (Join-Path $tree "services\api\tests\integration\task-one.txt") -Value "work on task-one" -Encoding ASCII
    if ($Broken) { Set-Content -LiteralPath (Join-Path $tree "services\api\tests\integration\broken.txt") -Value "broken" -Encoding ASCII }
    [void](Invoke-SandboxGit -Root $tree -Arguments @("add", "-A"))
    [void](Invoke-SandboxGit -Root $tree -Arguments @("commit", "-q", "-m", "work on task-one"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("worktree", "remove", "--force", $tree))
    $merge = Merge-TeamBranch -RepoRoot $root -CycleId "c1" -Branch $branch -Base "main"
    if (-not $merge.Merged) { throw "the sandbox could not merge: $($merge.Detail)" }
    $task = [pscustomobject]@{
        id = "task-one"; title = "the task task-one"; roadmap_row = "row"; state = "merged"; area = @("services/api/tests/integration")
        branch = $branch; worktree = ""; assignee = ""; reports = @(); budget = [pscustomobject]@{ max_usd = 0 }
        created_at = "2026-10-01T00:00:00Z"; updated_at = "2026-10-01T00:00:00Z"; integration_branch = "integrate/c1"
        sha = (Invoke-SandboxGit -Root $root -Arguments @("rev-parse", $branch))
    }
    Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document ([pscustomobject]@{ version = 1; tasks = @($task) })
    return $root
}

function Add-Fix {
    <# A commit on integrate/c1 that changes -Remove / -Write (relative paths), as the cycle's merge of a fix would; the task is 'merged' again. #>
    param([string]$Root, [string[]]$Remove = @(), [string[]]$Write = @())
    $tools = "$Root-tools"
    $tree = Join-Path $tools ("fix-" + [guid]::NewGuid().ToString("N").Substring(0, 6))
    $before = Invoke-SandboxGit -Root $Root -Arguments @("rev-parse", "integrate/c1")
    [void](Invoke-SandboxGit -Root $Root -Arguments @("worktree", "add", "-q", "--detach", $tree, $before))
    foreach ($r in $Remove) { [void](Invoke-SandboxGit -Root $tree -Arguments @("rm", "-q", $r)) }
    foreach ($w in $Write) {
        $target = Join-Path $tree ($w -replace "/", "\")
        [void](New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target))
        Set-Content -LiteralPath $target -Value "the fix" -Encoding ASCII
    }
    [void](Invoke-SandboxGit -Root $tree -Arguments @("add", "-A"))
    [void](Invoke-SandboxGit -Root $tree -Arguments @("commit", "-q", "-m", "the fix"))
    $after = Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD")
    [void](Invoke-SandboxGit -Root $Root -Arguments @("worktree", "remove", "--force", $tree))
    [void](Invoke-SandboxGit -Root $Root -Arguments @("update-ref", "refs/heads/integrate/c1", $after, $before))
    Set-TaskMerged -Root $Root
    return $after
}

function Set-TaskMerged {
    param([string]$Root)
    $path = Join-Path $Root "team\queue.json"
    $queue = Read-TeamJson -Path $path
    foreach ($t in @($queue.tasks)) { $t.state = "merged" }
    Write-TeamJson -Path $path -Document $queue
}

function Invoke-Integrate {
    param([string]$Root, [string]$Extra = "")
    $tools = "$Root-tools"
    $set = @{ PAGENTOS_RERUN_GATE_LOG = (Join-Path $tools "gate-calls.log"); PAGENTOS_RERUN_ENV_FLAG = (Join-Path $tools "env-red.flag")
        PAGENTOS_FAKE_CLAUDE_SCENARIO = "approve"; PAGENTOS_FAKE_CLAUDE_LOG = (Join-Path $tools "lead.log") }
    foreach ($name in @($set.Keys)) { Set-Item -Path "Env:\$name" -Value $set[$name] }
    try {
        $command = "& '" + (Join-Path $Root "scripts\team\integrate.ps1") + "' -Machine 'MAIL' -GatePath '" + (Join-Path $tools "gate.ps1") + "' -GateMinutes 3 -LeadMinutes 3" +
        " -DockerPath '" + (Join-Path $tools "docker-ok.cmd") + "' -UvPath '" + (Join-Path $tools "uv.cmd") + "' -PnpmPath '" + (Join-Path $tools "pnpm.cmd") + "'" +
        " -ClaudePath '$powershell' -ClaudePrefixArguments '-NoProfile','-ExecutionPolicy','Bypass','-File','" + (Join-Path $Root "scripts\tests\lib\fake-claude.ps1") + "'" +
        " -StepLockPath '" + (Join-Path $tools "integrate-step.lock") + "'" + $(if ($Extra) { " $Extra" } else { "" })
        $result = Invoke-NativeProcess -FilePath $powershell -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ($command + "; exit `$LASTEXITCODE")) -WorkingDirectory $Root -TimeoutSeconds 600
    }
    finally { foreach ($name in @($set.Keys)) { Remove-Item -Path "Env:\$name" -ErrorAction SilentlyContinue } }
    $calls = Join-Path $tools "gate-calls.log"
    return [pscustomobject]@{
        ExitCode = $result.ExitCode; Output = ($result.StdOut + $result.StdErr)
        Calls = @($(if (Test-Path -LiteralPath $calls) { @(Get-Content -LiteralPath $calls -Encoding UTF8 | Where-Object { $_.Trim() }) } else { @() }))
        Records = @(Get-TeamGateRecords -Directory (Join-Path $Root "team\reports\c1") -Branch "integrate/c1")
        Queue = (Read-TeamJson -Path (Join-Path $Root "team\queue.json"))
    }
}

function Get-OnlyOf { param([string]$Call) return (($Call -split '\|', 2)[1]) }

Test-Case "end to end: a fix inside the map reruns only the red steps; the green record says 'gate A + rerun B' and main gets it" {
    $root = New-Sandbox -Broken
    $first = Invoke-Integrate -Root $root
    Assert-Equal -Expected 6 -Actual $first.ExitCode -Because "the first gate is red: $($first.Output)"
    Assert-Equal -Expected "" -Actual (Get-OnlyOf $first.Calls[0]) -Because "the first gate is the full gate"
    $a = [string]$first.Records[-1].sha
    $b = Add-Fix -Root $root -Remove @("services/api/tests/integration/broken.txt")
    $second = Invoke-Integrate -Root $root
    Assert-Equal -Expected 0 -Actual $second.ExitCode -Because $second.Output
    Assert-Equal -Expected 2 -Actual @($second.Calls).Count -Because "one more gate call"
    $only = Get-OnlyOf $second.Calls[1]
    Assert-True -Condition ($only -match 'API integration tests' -and $only -match 'Alembic upgrade head' -and $only -match 'Gate database dropped') -Because "the red step and what it needs: $only"
    Assert-True -Condition ($only -notmatch 'API unit tests' -and $only -notmatch 'Web shell build') -Because "and no green heavy step: $only"
    $green = $second.Records[-1]
    Assert-Equal -Expected "green" -Actual ([string]$green.result) -Because "green"
    Assert-Equal -Expected 1 -Actual ([int]$green.rerun_of) -Because "it rests on gate 1"
    Assert-Equal -Expected $a -Actual ([string]$green.gate_sha) -Because "A is the red gate's sha"
    Assert-True -Condition (Test-TeamAncestor -RepoRoot $root -Ancestor $a -Of ([string]$green.sha)) -Because "B descends from A"
    Assert-True -Condition (@($green.rerun_steps) -contains "API integration tests") -Because "the steps are named"
    Assert-True -Condition ([string]$green.main -match '^[0-9a-f]{40}$') -Because "main moved: $($green.main)"
    $chain = Test-TeamGateRerunChain -RepoRoot $root -Directory (Join-Path $root "team\reports\c1") -Record $green
    Assert-True -Condition $chain.Ok -Because "the release check accepts the record integrate wrote: $($chain.Why)"
    $task = @($second.Queue.tasks)[0]
    Assert-Equal -Expected "awaiting_release" -Actual ([string]$task.state) -Because "the task waits for the release"
    Assert-True -Condition ([string]$task.reason -match 'rerun|yeniden') -Because "its reason says it was a rerun: $($task.reason)"
}

Test-Case "end to end: a fix outside the map runs the full gate" {
    $root = New-Sandbox -Broken
    $first = Invoke-Integrate -Root $root
    Assert-Equal -Expected 6 -Actual $first.ExitCode -Because $first.Output
    [void](Add-Fix -Root $root -Remove @("services/api/tests/integration/broken.txt") -Write @("services/browser-agent/src/other.ts"))
    $second = Invoke-Integrate -Root $root
    Assert-Equal -Expected 0 -Actual $second.ExitCode -Because $second.Output
    Assert-Equal -Expected "" -Actual (Get-OnlyOf $second.Calls[1]) -Because "the full gate: no -OnlyStep"
    $green = $second.Records[-1]
    Assert-True -Condition ($null -eq $green.PSObject.Properties["rerun_of"]) -Because "a full green record has no rerun_of"
}

Test-Case "end to end: an environment's red with no commit reruns the same steps on the same sha" {
    $root = New-Sandbox
    $flag = Join-Path "$root-tools" "env-red.flag"
    Set-Content -LiteralPath $flag -Value "red" -Encoding ASCII
    $first = Invoke-Integrate -Root $root
    Assert-Equal -Expected 6 -Actual $first.ExitCode -Because $first.Output
    Remove-Item -LiteralPath $flag -Force
    $second = Invoke-Integrate -Root $root
    Assert-Equal -Expected 0 -Actual $second.ExitCode -Because $second.Output
    Assert-Equal -Expected 2 -Actual @($second.Calls).Count -Because "the branch did not wait on 'red on this commit'"
    Assert-Equal -Expected (($first.Calls[0] -split '\|')[0]) -Actual (($second.Calls[1] -split '\|')[0]) -Because "the same sha"
    Assert-True -Condition ((Get-OnlyOf $second.Calls[1]) -match 'API integration tests') -Because "only the red steps"
    Assert-Equal -Expected 1 -Actual ([int]$second.Records[-1].rerun_of) -Because "the record says so"
}

Test-Case "end to end: after a partial rerun's red the branch stops, and the next gate is the full one" {
    $root = New-Sandbox -Broken
    $first = Invoke-Integrate -Root $root
    Assert-Equal -Expected 6 -Actual $first.ExitCode -Because $first.Output
    # The 'fix' inside the map that does not fix it: the partial rerun is red.
    [void](Add-Fix -Root $root -Write @("services/api/tests/integration/test_more.py"))
    $second = Invoke-Integrate -Root $root
    Assert-True -Condition ((Get-OnlyOf $second.Calls[1]) -match 'API integration tests') -Because "the second gate is a partial rerun: $($second.Calls[1])"
    Assert-Equal -Expected 8 -Actual $second.ExitCode -Because "two reds in a row stop the branch: $($second.Output)"
    Assert-Equal -Expected 1 -Actual ([int]$second.Records[-1].rerun_of) -Because "the partial red is marked"
    [void](Add-Fix -Root $root -Remove @("services/api/tests/integration/broken.txt"))
    $third = Invoke-Integrate -Root $root -Extra "-ClearGateStop"
    Assert-Equal -Expected 0 -Actual $third.ExitCode -Because $third.Output
    Assert-Equal -Expected "" -Actual (Get-OnlyOf $third.Calls[-1]) -Because "the full gate after a partial red: $($third.Calls[-1])"
}

# ============================================================================

foreach ($path in @($sandboxes)) {
    foreach ($attempt in 1..3) {
        try { if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop }; break } catch { Start-Sleep -Milliseconds 300 }
    }
}
Write-Host ""
Write-Host "team-gate-rerun: $($script:Passes) passed, $($script:Failures) failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
