<#
.SYNOPSIS
    The integration step of the agent team (scripts/team/integrate.ps1): merged tasks go through
    the lead's wiring run and the FULL gate in a worktree of their own, and reach main only green.

.DESCRIPTION
    Two halves, as in team-cycle.tests.ps1.

    The decisions (scripts/lib/TeamIntegrate.ps1) are driven as functions: what a report leaves
    "for the lead at merge", which files the lead's run may touch, what a gate log says, whom a
    red gate names, when a branch is stopped.

    The step itself is run for real, in a git repository made for the test, with
      * scripts/tests/lib/fake-gate.ps1 in place of the gate (-GatePath): the REAL gate is never
        run here;
      * scripts/tests/lib/fake-claude.ps1, or a stand-in this file writes, in place of the lead;
      * scripts/team/fake-team-api.ps1 in place of the Cloud Core's queue;
      * .cmd files in place of docker, uv and pnpm.
    What is asserted is what is in the repository and the queue afterwards.

    Nothing is released, no tag is made, and this repository's own branches, worktrees and
    team/ files are not written to.

    Run: powershell -NoProfile -File scripts\tests\team-integrate.tests.ps1
#>

[CmdletBinding()]
param(
    # Runs only the cases whose name matches this pattern (the mutation proofs re-run a slice).
    [string]$Filter = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
$integrateLib = Join-Path $repoRoot "scripts\lib\TeamIntegrate.ps1"
$integrateScript = Join-Path $repoRoot "scripts\team\integrate.ps1"
if (Test-Path -LiteralPath $integrateLib) { . $integrateLib }

$script:Failures = 0
$script:Passes = 0

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

function New-Task {
    param([string]$Id, [string]$State = "merged", [string[]]$Area = @("src/area"), [string]$Branch = "", [string]$Integration = "")
    $task = [pscustomobject]@{
        id = $Id; title = "the task $Id"; roadmap_row = "row"; state = $State; area = @($Area)
        branch = $Branch; worktree = ""; assignee = ""; reports = @(); budget = [pscustomobject]@{ max_usd = 0 }
        created_at = "2026-10-01T00:00:00Z"; updated_at = "2026-10-01T00:00:00Z"
    }
    if ($Integration) { $task | Add-Member -NotePropertyName integration_branch -NotePropertyValue $Integration }
    return $task
}

function New-Queue {
    param([object[]]$Tasks = @())
    return [pscustomobject]@{ version = 1; tasks = @($Tasks) }
}

# ============================================================================ the decisions

Write-Host ""
Write-Host "which merged tasks wait for the gate"

Test-Case "merged tasks are grouped by their integration branch; other states and a merged task without a branch are left out" {
    $queue = New-Queue -Tasks @(
        (New-Task -Id "one-task" -Integration "integrate/d2"), (New-Task -Id "two-task" -Integration "integrate/d1"),
        (New-Task -Id "three-task" -Integration "integrate/d2"), (New-Task -Id "four-task" -State "inspecting" -Integration "integrate/d2"),
        (New-Task -Id "five-task"), (New-Task -Id "six-task" -State "awaiting_release" -Integration "integrate/d1"))
    $groups = @(Get-TeamMergedGroups -Queue $queue)
    Assert-Equal -Expected "integrate/d1,integrate/d2" -Actual (@($groups | ForEach-Object { $_.Branch }) -join ",") -Because "two branches, in order"
    Assert-Equal -Expected "two-task" -Actual (@($groups[0].Tasks | ForEach-Object { $_.id }) -join ",") -Because "d1 holds one merged task"
    Assert-Equal -Expected "one-task,three-task" -Actual (@($groups[1].Tasks | ForEach-Object { $_.id }) -join ",") -Because "d2 holds two"
    Assert-Equal -Expected 0 -Actual @(Get-TeamMergedGroups -Queue (New-Queue)).Count -Because "an empty queue has nothing to gate"
    Assert-Equal -Expected 1 -Actual @(Get-TeamMergedGroups -Queue (New-Queue -Tasks @((New-Task -Id "one-task" -Integration "integrate/d1")))).Count -Because "one"
}

Test-Case "the gate worktree is under .claude\worktrees\gate, and only an integration branch gets one" {
    Assert-Equal -Expected "E:\repo\.claude\worktrees\gate\integrate\d20261001" -Actual (Get-TeamGateWorktreePath -RepoRoot "E:\repo" -Branch "integrate/d20261001") -Because "a tree of its own"
    Assert-Equal -Expected "d20261001" -Actual (Get-TeamIntegrationCycleId -Branch "integrate/d20261001") -Because "the cycle of the branch"
    foreach ($bad in @("main", "team/c1/worker-a", "integrate/", "integrate/../x", "feat/hand-gestures-stage1", "integrate/A")) {
        $threw = $false
        try { [void](Get-TeamGateWorktreePath -RepoRoot "E:\repo" -Branch $bad) } catch { $threw = $true }
        Assert-True -Condition $threw -Because "'$bad' is not gated"
    }
}

Write-Host ""
Write-Host "what a report leaves for the lead at merge"

Test-Case "the section is read under a heading, under a label and under a bold label; a sentence that mentions it is not one" {
    $heading = "sha: abc`n## For the lead at merge`n- add the suite to scripts/quality-gate.ps1`n- mount it in services/api/app/main.py`n`nstill the section`n## Open risks`n- touches src/other/file.py"
    $section = Get-TeamLeadMergeSection -Text $heading
    Assert-True -Condition ($section -match "quality-gate\.ps1" -and $section -match "app/main\.py" -and $section -match "still the section") -Because "under a heading it runs to the next heading: $section"
    Assert-True -Condition ($section -notmatch "src/other/file\.py") -Because "the next heading ends it"

    $label = "tests: 12`nFor the lead at merge: add scripts/tests/x.tests.ps1 to the gate`n- and to .github/workflows/ci.yml`nOpen risks: src/other/file.py is fragile"
    $section = Get-TeamLeadMergeSection -Text $label
    Assert-True -Condition ($section -match "x\.tests\.ps1" -and $section -match "ci\.yml") -Because "the label's line and its bullets: $section"
    Assert-True -Condition ($section -notmatch "src/other") -Because "the next label ends it: $section"

    $bold = "**For the lead at merge**`nnumber the ADR; wire src/wiring/mount.txt`n`n**Open risks**`nsrc/other/file.py"
    $section = Get-TeamLeadMergeSection -Text $bold
    Assert-True -Condition ($section -match "src/wiring/mount\.txt" -and $section -notmatch "src/other") -Because "a blank line ends a labelled section: $section"

    Assert-Equal -Expected "" -Actual (Get-TeamLeadMergeSection -Text "the suite is named under 'For the lead at merge' as asked, see src/other/file.py") -Because "a mention is not the section"
    Assert-Equal -Expected "" -Actual (Get-TeamLeadMergeSection -Text "sha: abc`ntests: 3") -Because "a report without one"
    Assert-Equal -Expected "" -Actual (Get-TeamLeadMergeSection -Text "") -Because "no report"
    $two = Get-TeamLeadMergeSection -Text "## For the lead at merge`na/one.txt`n## Other`nx`n## For the lead at merge`nb/two.txt"
    Assert-True -Condition ($two -match "a/one\.txt" -and $two -match "b/two\.txt") -Because "a report with two such sections gives both: $two"
}

Test-Case "the files a section names are paths with a directory and an extension, and nothing else" {
    $named = @(Get-TeamLeadNamedFiles -Text "add scripts\tests\x.tests.ps1 to quality-gate.ps1 and .github/workflows/ci.yml; mount in services/api/app/main.py. See https://example.com/a/b.html and/or C:\Windows\x.dll, and ../up/x.txt")
    Assert-Equal -Expected "scripts/tests/x.tests.ps1,.github/workflows/ci.yml,services/api/app/main.py" -Actual ($named -join ",") -Because "three paths; a bare name, a URL, 'and/or', a drive path and a parent path are not"
    Assert-Equal -Expected 0 -Actual @(Get-TeamLeadNamedFiles -Text "").Count -Because "nothing named"
    Assert-Equal -Expected 0 -Actual @(Get-TeamLeadNamedFiles -Text "nothing named").Count -Because "prose names nothing"
}

Test-Case "the lead's run may touch docs/, .github/, team/, the gate's list, BUILD_STATE and a NAMED file - and nothing else" {
    $named = @("services/api/app/main.py", "src/wiring/mount.txt")
    $allowed = @("docs/DECISIONS.md", "docs\THIRD_PARTY_COMPONENTS.md", ".github/workflows/ci.yml", "scripts/quality-gate.ps1", "team/plans/x-adr.md",
        "state/BUILD_STATE.json", "services/api/app/main.py", "SRC/wiring/mount.txt", "./docs/HANDOFF.md")
    foreach ($file in $allowed) { Assert-True -Condition (Test-TeamLeadFileAllowed -Path $file -NamedFiles $named) -Because "'$file' is the lead's to change" }
    $refused = @("src/a/extra.txt", "scripts/quality-gate.ps1.bak", "docsx/a.md", "scripts/team/integrate.ps1", "services/api/app/other.py",
        "other/main.py", "state/other.json", "team/queue.json", "team/lock.json", "docs/../src/a/x.txt", "", "github/workflows/ci.yml")
    foreach ($file in $refused) { Assert-True -Condition (-not (Test-TeamLeadFileAllowed -Path $file -NamedFiles $named)) -Because "'$file' is not the lead's to change" }
    Assert-True -Condition (Test-TeamLeadFileAllowed -Path "services/api/app/main.py" -NamedFiles @("app/main.py")) -Because "a report may name a file by its last directories"
    Assert-True -Condition (-not (Test-TeamLeadFileAllowed -Path "services/api/app/main.py" -NamedFiles @("main.py"))) -Because "a bare name opens nothing"
    Assert-True -Condition (-not (Test-TeamLeadFileAllowed -Path "services/api/xapp/main.py" -NamedFiles @("app/main.py"))) -Because "'xapp' is another directory"
    Assert-Equal -Expected "src/a/extra.txt" -Actual (@(Get-TeamLeadRefusedFiles -Changed @("docs/DECISIONS.md", "src/a/extra.txt", "src/wiring/mount.txt") -NamedFiles $named) -join ",") -Because "one file of three is outside the rule"
    Assert-Equal -Expected 0 -Actual @(Get-TeamLeadRefusedFiles -Changed @() -NamedFiles $named).Count -Because "a run that changed nothing kept the rule"
    Assert-Equal -Expected 0 -Actual @(Get-TeamLeadRefusedFiles -Changed @("docs/DECISIONS.md") -NamedFiles @()).Count -Because "one allowed file, nothing named"
}

Test-Case "the lead's card carries each task's section and the rule the script will hold it to" {
    $notes = @([pscustomobject]@{ Id = "task-one"; Title = "the first"; Area = @("src/a"); Worker = "mount it in src/wiring/mount.txt"; Inspector = ""; Named = @("src/wiring/mount.txt") })
    $card = New-TeamLeadMergeCard -CycleId "c1" -Branch "integrate/c1" -Notes $notes
    Assert-True -Condition ($card -match "(?m)^## task-one - the first$") -Because "the task is named"
    Assert-True -Condition ($card.Contains("mount it in src/wiring/mount.txt")) -Because "the worker's words reach the lead"
    Assert-True -Condition ($card -match "inspector's newest report\)\s+nothing named") -Because "an empty section says so"
    Assert-True -Condition ($card -match "Do not commit, push, tag, switch branch, run the gate or release") -Because "the lead's run does not release"
    Assert-True -Condition ($card -notmatch "(?m)^- split_file:") -Because "it is not a split card"
}

Write-Host ""
Write-Host "what a gate log says"

$greenLog = "`n=== Required files ===`nall present`n`n=== API unit tests ===`n5400 passed`n`n=== Quality gate summary ===`n`nStep           Result Seconds`n----           ------ -------`nRequired files PASS       0.1`nAPI unit tests PASS       1.2`n`nQUALITY GATE: PASS`n"
$redLog = "`n=== Required files ===`nall present`n`n=== API unit tests ===`ntests/unit/test_a.py F`nFAILED tests/unit/test_a.py::test_it - AssertionError`nFAILED: pytest (unit) exited with code 1`n`n=== Script syntax (PowerShell 5.1) ===`n  PASS  one`n  FAIL  the second case`nFAILED: script syntax tests exited with code 1`n`n=== Web shell build ===`nok`n`n=== Quality gate summary ===`n`nStep           Result Seconds`nRequired files PASS       0.1`nAPI unit tests FAIL       1.2`n`nQUALITY GATE: FAIL`n"

Test-Case "green is the exit code 0 AND the gate's last word; either alone is not green" {
    Assert-True -Condition ([bool](Read-TeamGateLog -Text $greenLog -ExitCode 0).Green) -Because "PASS and 0"
    Assert-True -Condition (-not [bool](Read-TeamGateLog -Text $greenLog -ExitCode 1).Green) -Because "PASS was printed but the gate exited 1"
    Assert-True -Condition ((Read-TeamGateLog -Text $greenLog -ExitCode 1).Why -match "PASS dedi ama") -Because "and the reason says which"
    Assert-True -Condition (-not [bool](Read-TeamGateLog -Text $greenLog -ExitCode -1 -TimedOut $true).Green) -Because "a gate that was killed is not green"
    Assert-True -Condition (-not [bool](Read-TeamGateLog -Text "=== Required files ===`nall present`n" -ExitCode 0).Green) -Because "exit 0 without the last word is not the gate"
    Assert-True -Condition (-not [bool](Read-TeamGateLog -Text "" -ExitCode 0).Green) -Because "an empty log is not green"
    Assert-True -Condition (-not [bool](Read-TeamGateLog -Text ($greenLog -replace "QUALITY GATE: PASS", "QUALITY GATE: FAIL") -ExitCode 0).Green) -Because "FAIL with exit 0 is not green"
    Assert-True -Condition (-not [bool](Read-TeamGateLog -Text ($greenLog + "QUALITY GATE: FAIL`n") -ExitCode 0).Green) -Because "both words is not green"
    Assert-True -Condition (-not [bool](Read-TeamGateLog -Text ("the gate says QUALITY GATE: PASS somewhere") -ExitCode 0).Green) -Because "the word inside another line is not the last word"
    Assert-True -Condition ([bool](Read-TeamGateLog -Text ([string][char]0xFEFF + $greenLog.Replace("`n", "`r`n")) -ExitCode 0).Green) -Because "a byte-order mark and CRLF change nothing"
}

Test-Case "a red log gives the failing steps in the gate's order, the first failing test, and only the failing steps' text" {
    $gate = Read-TeamGateLog -Text $redLog -ExitCode 1
    Assert-True -Condition (-not [bool]$gate.Green) -Because "red"
    Assert-Equal -Expected "API unit tests|Script syntax (PowerShell 5.1)" -Actual ($gate.FailedSteps -join "|") -Because "two steps failed; the summary table named only one of them"
    Assert-Equal -Expected "tests/unit/test_a.py::test_it" -Actual $gate.FirstFailure -Because "the first failing test of the first failing step"
    Assert-True -Condition ($gate.FailureText -match "test_a\.py" -and $gate.FailureText -match "the second case") -Because "the failing steps' text"
    Assert-True -Condition ($gate.FailureText -notmatch "all present" -and $gate.FailureText -notmatch "(?m)^ok$") -Because "a passing step's text is not a failure's"
    $suite = Read-TeamGateLog -Text "=== Agent team cycle (PS5.1 + git, no model) ===`n  PASS  a`n  FAIL  the lock is released`n        expected: <False>`nFAILED: team-cycle tests exited with code 1`nQUALITY GATE: FAIL" -ExitCode 1
    Assert-Equal -Expected "the lock is released" -Actual $suite.FirstFailure -Because "a PowerShell suite's failing case"
    $bare = Read-TeamGateLog -Text "=== Dev stack up (docker compose) ===`nFAILED: dev-up.ps1 exited with code 1`nQUALITY GATE: FAIL" -ExitCode 1
    Assert-Equal -Expected "dev-up.ps1 exited with code 1" -Actual $bare.FirstFailure -Because "with no test named, the step's own sentence"
    $reason = Get-TeamGateReason -Gate $gate -LogFile "team/reports/c1/gate-1.log" -Attempt 1
    Assert-True -Condition ($reason -match "API unit tests" -and $reason -match "test_a\.py::test_it" -and $reason -match "gate-1\.log") -Because "the reason carries the step, the test and the log: $reason"
    Assert-True -Condition ((Get-TeamGateReason -Gate ([pscustomobject]@{ Why = ("x" * 3000); FirstFailure = "" }) -LogFile "l" -Attempt 0).Length -le 900) -Because "a reason is bounded"
}

Test-Case "a failure names the task whose file it holds - the whole path or its last directories - and nobody for a bare name" {
    $files = @{ "task-one" = @("services/api/tests/unit/test_a.py", "services/api/app/a.py"); "task-two" = @("apps/web/src/b/page.tsx", "README.md") }
    Assert-Equal -Expected "task-one" -Actual (@(Get-TeamGateBlamedTasks -FailureText "FAILED tests/unit/test_a.py::test_it - AssertionError" -TaskFiles $files) -join ",") -Because "pytest prints the path from services/api"
    Assert-Equal -Expected "task-two" -Actual (@(Get-TeamGateBlamedTasks -FailureText "E:\repo\apps\web\src\b\page.tsx(12,3): error TS2322" -TaskFiles $files) -join ",") -Because "an absolute path with backslashes"
    Assert-Equal -Expected "task-one,task-two" -Actual (@(Get-TeamGateBlamedTasks -FailureText "app/a.py:3 and b/page.tsx:9" -TaskFiles $files) -join ",") -Because "both"
    Assert-Equal -Expected 0 -Actual @(Get-TeamGateBlamedTasks -FailureText "see README.md and page.tsx and test_a.py" -TaskFiles $files).Count -Because "a bare file name names nobody"
    Assert-Equal -Expected 0 -Actual @(Get-TeamGateBlamedTasks -FailureText "tests/unit/test_ab.py and xunit/test_a.py2 and app/a.pyc" -TaskFiles $files).Count -Because "a longer name is another file"
    Assert-Equal -Expected 0 -Actual @(Get-TeamGateBlamedTasks -FailureText "" -TaskFiles $files).Count -Because "an empty failure names nobody"
    Assert-Equal -Expected 0 -Actual @(Get-TeamGateBlamedTasks -FailureText "app/a.py" -TaskFiles @{}).Count -Because "no tasks, nobody"
}

Test-Case "two failed attempts in a row stop a branch; a green gate or the lead's look starts the count again" {
    $r = { param([string[]]$Results) $n = 0; @($Results | ForEach-Object { $n++; [pscustomobject]@{ n = $n; branch = "integrate/c1"; result = $_ } }) }
    Assert-Equal -Expected 0 -Actual (Get-TeamGateStrikes -Records @()) -Because "no attempt yet"
    Assert-True -Condition (-not (Test-TeamGateStopped -Records (& $r @("red")))) -Because "one red gate does not stop it"
    Assert-True -Condition (Test-TeamGateStopped -Records (& $r @("red", "red"))) -Because "two do (TEAM_PROTOCOL 10)"
    Assert-True -Condition (Test-TeamGateStopped -Records (& $r @("red", "lead_refused"))) -Because "a refused lead run is a failed attempt too"
    Assert-True -Condition (Test-TeamGateStopped -Records (& $r @("lead_failed", "red"))) -Because "and a lead run that gave no result"
    Assert-True -Condition (-not (Test-TeamGateStopped -Records (& $r @("red", "green", "red")))) -Because "a green gate between them"
    Assert-True -Condition (-not (Test-TeamGateStopped -Records (& $r @("red", "red", "cleared")))) -Because "the lead looked"
    Assert-True -Condition (-not (Test-TeamGateStopped -Records (& $r @("red", "red", "cleared", "red")))) -Because "one red after the lead looked"
    Assert-True -Condition (-not (Test-TeamGateStopped -Records (& $r @("red", "conflict", "environment")))) -Because "what is not the gate's verdict is not counted"
}

Test-Case "the attempts of a branch are read from its folder, in order, and the next number is one past the highest" {
    $work = Join-Path $env:TEMP ("pagentos-gaterec-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    try {
        Assert-Equal -Expected 0 -Actual @(Get-TeamGateRecords -Directory $work -Branch "integrate/c1").Count -Because "no folder, no attempt"
        Assert-Equal -Expected 1 -Actual (Get-TeamGateNextNumber -Directory $work) -Because "the first attempt"
        Write-TeamGateRecord -Directory $work -Number 10 -Record ([pscustomobject]@{ n = 10; branch = "integrate/c1"; result = "red" })
        Write-TeamGateRecord -Directory $work -Number 2 -Record ([pscustomobject]@{ n = 2; branch = "integrate/c1"; result = "green" })
        Write-TeamGateRecord -Directory $work -Number 3 -Record ([pscustomobject]@{ n = 3; branch = "integrate/other"; result = "red" })
        [System.IO.File]::WriteAllText((Join-Path $work "gate-11.log"), "a log without a record")
        $records = @(Get-TeamGateRecords -Directory $work -Branch "integrate/c1")
        Assert-Equal -Expected "2,10" -Actual (@($records | ForEach-Object { $_.n }) -join ",") -Because "this branch's, by number and not by name"
        Assert-Equal -Expected 1 -Actual (Get-TeamGateStrikes -Records $records) -Because "green, then red"
        Assert-Equal -Expected 12 -Actual (Get-TeamGateNextNumber -Directory $work) -Because "a log counts too: no attempt overwrites another's log"
    }
    finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "the gate worktree's environment: uv sync where a service is, pnpm where the lock file is, nothing where neither is" {
    $work = Join-Path $env:TEMP ("pagentos-gateenv-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    try {
        [void](New-Item -ItemType Directory -Force -Path $work)
        Assert-Equal -Expected 0 -Actual @(Get-TeamGateEnvironmentPlan -Worktree $work -UvPath "uv" -PnpmPath "pnpm").Count -Because "an empty tree needs nothing"
        foreach ($folder in @("services\api", "services\browser", "apps\web")) { [void](New-Item -ItemType Directory -Force -Path (Join-Path $work $folder)) }
        Set-Content -LiteralPath (Join-Path $work "services\api\pyproject.toml") -Value "[project]" -Encoding ASCII
        $one = @(Get-TeamGateEnvironmentPlan -Worktree $work -UvPath "uv" -PnpmPath "pnpm")
        Assert-Equal -Expected 1 -Actual @($one).Count -Because "one service"
        Assert-Equal -Expected "sync" -Actual ($one[0].Arguments -join " ") -Because "uv sync"
        Assert-Equal -Expected (Join-Path $work "services\api") -Actual $one[0].Directory -Because "there"
        Set-Content -LiteralPath (Join-Path $work "services\browser\pyproject.toml") -Value "[project]" -Encoding ASCII
        Set-Content -LiteralPath (Join-Path $work "pnpm-lock.yaml") -Value "lockfileVersion: 9" -Encoding ASCII
        $all = @(Get-TeamGateEnvironmentPlan -Worktree $work -UvPath "uv" -PnpmPath "pnpm")
        Assert-Equal -Expected "services/api (uv sync)|services/browser (uv sync)|apps/web (pnpm install)" -Actual (@($all | ForEach-Object { $_.Name }) -join "|") -Because "the three the gate needs"
        Assert-Equal -Expected "install --frozen-lockfile --prefer-offline" -Actual ($all[2].Arguments -join " ") -Because "the lock file is not rewritten and the store is used"
        Assert-Equal -Expected $work -Actual $all[2].Directory -Because "pnpm's lock file is at the repository root"
    }
    finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Host ""
Write-Host "the scripts themselves"

Test-Case "the fake gate speaks the real gate's words (the parser reads both)" {
    $real = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\quality-gate.ps1"), [System.Text.Encoding]::UTF8)
    $fake = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\tests\lib\fake-gate.ps1"), [System.Text.Encoding]::UTF8)
    foreach ($words in @('QUALITY GATE: PASS', 'QUALITY GATE: FAIL', '=== Quality gate summary ===')) {
        Assert-True -Condition ($real.Contains($words)) -Because "the real gate prints '$words'"
        Assert-True -Condition ($fake.Contains($words)) -Because "the fake gate prints '$words'"
    }
    Assert-True -Condition ($real.Contains('Write-Host "=== $Name ==="')) -Because "the real gate opens a step with === name ==="
    Assert-True -Condition ($real.Contains('Write-Host "FAILED: $($_.Exception.Message)"')) -Because "the real gate marks a failed step with 'FAILED: '"
    Assert-True -Condition ($real -match '(?s)if \(\$failed\) \{\s*Write-Host "QUALITY GATE: FAIL"[^\r\n]*\s*exit 1') -Because "the real gate exits 1 when it says FAIL"
    Assert-True -Condition ($real -match 'Write-Host "QUALITY GATE: PASS"[^\r\n]*\s*exit 0') -Because "and 0 when it says PASS"
    Assert-True -Condition ($fake -match '(?m)^Write-Host "=== API unit tests ==="' -and $fake.Contains('Write-Host "FAILED: pytest (unit) exited with code 1"')) -Because "the fake uses the same marks"
}

Test-Case "no code path of the integration step names a release, a tag or the last-known-good record" {
    Assert-True -Condition (Test-Path -LiteralPath $integrateScript) -Because "scripts/team/integrate.ps1 exists"
    foreach ($file in @($integrateScript, $integrateLib)) {
        $text = [System.IO.File]::ReadAllText($file, [System.Text.Encoding]::UTF8)
        foreach ($forbidden in @("release-cloud-core", "install-recovery-supervisor", "LAST_KNOWN_GOOD", "last-known-good.json", "--tags", "--force", "push -f", "reset --hard origin")) {
            Assert-True -Condition (-not $text.Contains($forbidden)) -Because "$(Split-Path -Leaf $file) names '$forbidden'"
        }
        $tokens = $null; $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$errors)
        Assert-Equal -Expected 0 -Actual @($errors).Count -Because "$(Split-Path -Leaf $file) parses on Windows PowerShell 5.1"
        # Every git verb that is written as a literal argument: none of them is 'tag', and a push names no tag.
        $literals = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.StringConstantExpressionAst] }, $true) | ForEach-Object { $_.Value })
        Assert-True -Condition ($literals -notcontains "tag") -Because "$(Split-Path -Leaf $file) makes no tag"
    }
}

Test-Case "a script of the integration step that holds Turkish text says which encoding it is in" {
    $files = @($integrateScript, $integrateLib, (Join-Path $repoRoot "scripts\tests\team-integrate.tests.ps1"), (Join-Path $repoRoot "scripts\tests\lib\fake-gate.ps1"))
    $withText = 0
    foreach ($file in $files) {
        Assert-True -Condition (Test-Path -LiteralPath $file) -Because "$file exists"
        $bytes = [System.IO.File]::ReadAllBytes($file)
        if (@($bytes | Where-Object { $_ -gt 127 }).Count -eq 0) { continue }
        $withText++
        Assert-True -Condition ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) -Because "$(Split-Path -Leaf $file) holds non-ASCII text and has no byte-order mark"
    }
    Assert-True -Condition ($withText -ge 2) -Because "the report is written in Turkish somewhere"
}

# ============================================================================ the step, run

Write-Host ""
Write-Host "the step, in a repository of its own, with fakes in place of the gate, the lead, docker and the queue"

$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$sandboxes = New-Object System.Collections.ArrayList
$fakeApis = New-Object System.Collections.ArrayList
$utf8 = New-Object System.Text.UTF8Encoding($false)

# The lead's run, when a test needs it to DO something (fake-claude's lead only writes splits).
# The same protocol: the role file in the arguments, the card on standard input, the result
# document on standard output. What it writes is the test's, in the environment.
$leadStandIn = @'
[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)
$ErrorActionPreference = "Stop"
$roleFile = ""; $tools = ""; $model = ""
for ($i = 0; $i -lt $Rest.Length; $i++) {
    if ($Rest[$i] -eq "--append-system-prompt-file") { $roleFile = $Rest[$i + 1] }
    if ($Rest[$i] -eq "--allowedTools") { $tools = $Rest[$i + 1] }
    if ($Rest[$i] -eq "--model") { $model = $Rest[$i + 1] }
}
$card = [Console]::In.ReadToEnd()
$here = (Get-Location).ProviderPath
$utf8 = New-Object System.Text.UTF8Encoding($false)
if ($env:PAGENTOS_FAKE_CLAUDE_LOG) {
    $entry = [pscustomobject]@{ role = [System.IO.Path]::GetFileNameWithoutExtension($roleFile); cwd = $here; tools = $tools; model = $model }
    Add-Content -LiteralPath $env:PAGENTOS_FAKE_CLAUDE_LOG -Value ($entry | ConvertTo-Json -Compress) -Encoding UTF8
}
if ($env:PAGENTOS_FAKE_LEAD_CARD) { [System.IO.File]::WriteAllText($env:PAGENTOS_FAKE_LEAD_CARD, $card, $utf8) }
if ($env:PAGENTOS_FAKE_LEAD_LOCK -and $env:PAGENTOS_FAKE_LEAD_LOCK_COPY) { Copy-Item -LiteralPath $env:PAGENTOS_FAKE_LEAD_LOCK -Destination $env:PAGENTOS_FAKE_LEAD_LOCK_COPY -Force }
foreach ($relative in @(([string]$env:PAGENTOS_FAKE_LEAD_WRITES) -split ";" | Where-Object { $_.Trim() })) {
    $target = Join-Path $here ($relative.Trim() -replace "/", "\")
    $folder = Split-Path -Parent $target
    if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
    Add-Content -LiteralPath $target -Value "wired by the lead" -Encoding ASCII
}
if ($env:PAGENTOS_FAKE_LEAD_COMMIT -eq "1") {
    & git.exe add -A 2>&1 | Out-Null
    & git.exe -c user.name=lead -c user.email=lead@example.invalid commit -q -m "the lead committed by itself" 2>&1 | Out-Null
}
if ($env:PAGENTOS_FAKE_LEAD_FAIL -eq "1") { [Console]::Out.Write("I could not do that."); exit 0 }
if ($env:PAGENTOS_FAKE_LEAD_FAIL -eq "limit") {
    [Console]::Out.Write('{"type":"result","subtype":"success","is_error":true,"result":"Claude AI usage limit reached|1790836000","total_cost_usd":0}')
    exit 1
}
$document = [pscustomobject]@{ type = "result"; subtype = "success"; is_error = $false; result = "wired"; total_cost_usd = 0.5 }
[Console]::Out.Write(($document | ConvertTo-Json -Compress))
exit 0
'@

function Invoke-SandboxGit {
    param([string]$Root, [string[]]$Arguments)
    $result = Invoke-TeamGit -WorkingDirectory $Root -Arguments $Arguments
    if (-not $result.Success) { throw "git $($Arguments -join ' '): $($result.StdErr)" }
    return $result.StdOut.Trim()
}

function New-Sandbox {
    <#
        A repository with main, and for each task a worker branch (one file inside its area)
        merged into integrate/c1 by the cycle's own Merge-TeamBranch - the state the cycle leaves.
        Beside it (never inside): <root>-tools with the fakes and their logs, and <root>-origin.git.
    #>
    param([hashtable[]]$Work = @(), [object[]]$ExtraTasks = @(), $Lock = $null, [switch]$Services, [switch]$NoOrigin)
    $root = Join-Path $env:TEMP ("pagentos-integ-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    $tools = "$root-tools"
    [void]$sandboxes.Add($root); [void]$sandboxes.Add($tools); [void]$sandboxes.Add("$root-origin.git")
    foreach ($folder in @("scripts\lib", "scripts\team", "scripts\tests\lib", ".claude\agents", "team", "src\area", "docs")) {
        [void](New-Item -ItemType Directory -Force -Path (Join-Path $root $folder))
    }
    [void](New-Item -ItemType Directory -Force -Path $tools)
    foreach ($name in @("NativeProcess.ps1", "TeamQueue.ps1", "TeamRun.ps1", "HttpJson.ps1", "TeamIntegrate.ps1")) {
        Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\lib\$name") -Destination (Join-Path $root "scripts\lib\$name")
    }
    Copy-Item -Path (Join-Path $repoRoot "scripts\team\*.ps1") -Destination (Join-Path $root "scripts\team")
    foreach ($name in @("fake-claude.ps1", "fake-gate.ps1")) {
        Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\tests\lib\$name") -Destination (Join-Path $root "scripts\tests\lib\$name")
    }
    foreach ($role in @("lead", "researcher", "integrator", "worker", "inspector")) {
        Copy-Item -LiteralPath (Join-Path $repoRoot ".claude\agents\$role.md") -Destination (Join-Path $root ".claude\agents\$role.md")
    }
    Set-Content -LiteralPath (Join-Path $root ".gitignore") -Value ".claude/worktrees/`nteam/reports/" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $root "src\area\README.txt") -Value "the area" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $root "docs\DECISIONS.md") -Value "# decisions" -Encoding ASCII
    if ($Services) {
        foreach ($service in @("services\api", "services\browser")) {
            [void](New-Item -ItemType Directory -Force -Path (Join-Path $root $service))
            Set-Content -LiteralPath (Join-Path $root "$service\pyproject.toml") -Value "[project]" -Encoding ASCII
        }
        Set-Content -LiteralPath (Join-Path $root "pnpm-lock.yaml") -Value "lockfileVersion: 9" -Encoding ASCII
    }
    Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document (New-Queue)
    Write-TeamJson -Path (Join-Path $root "team\lock.json") -Document (New-TeamLockReleased)
    [void](Invoke-SandboxGit -Root $root -Arguments @("init", "-q", "-b", "main"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.name", "team test"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.email", "team@example.invalid"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "core.autocrlf", "false"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("add", "-A"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-m", "the sandbox"))
    if (-not $NoOrigin) {
        [void](Invoke-TeamGit -WorkingDirectory $tools -Arguments @("init", "-q", "--bare", "$root-origin.git"))
        [void](Invoke-SandboxGit -Root $root -Arguments @("remote", "add", "origin", "$root-origin.git"))
        [void](Invoke-SandboxGit -Root $root -Arguments @("push", "-q", "origin", "main"))
    }

    $tasks = New-Object System.Collections.ArrayList
    foreach ($item in @($Work)) {
        $id = [string]$item.Id
        $area = [string]$item.Area
        $branch = "team/c1/worker-$id"
        [void](Invoke-SandboxGit -Root $root -Arguments @("branch", $branch, "main"))
        $tree = Join-Path $tools "wt-$id"
        [void](Invoke-SandboxGit -Root $root -Arguments @("worktree", "add", "-q", $tree, $branch))
        $target = Join-Path $tree (($area -replace "/", "\") + "\$id.txt")
        [void](New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target))
        Set-Content -LiteralPath $target -Value "work on $id" -Encoding ASCII
        if ($item.ContainsKey("Extra")) {
            # A file the branch carries that is NOT the task's own (its base was ahead of main).
            $extra = Join-Path $tree (([string]$item.Extra) -replace "/", "\")
            [void](New-Item -ItemType Directory -Force -Path (Split-Path -Parent $extra))
            Set-Content -LiteralPath $extra -Value "not the task's own" -Encoding ASCII
        }
        [void](Invoke-SandboxGit -Root $tree -Arguments @("add", "-A"))
        [void](Invoke-SandboxGit -Root $tree -Arguments @("commit", "-q", "-m", "work on $id"))
        [void](Invoke-SandboxGit -Root $root -Arguments @("worktree", "remove", "--force", $tree))
        $merge = Merge-TeamBranch -RepoRoot $root -CycleId "c1" -Branch $branch -Base "main"
        if (-not $merge.Merged) { throw "the sandbox could not merge ${branch}: $($merge.Detail)" }
        $task = New-Task -Id $id -Area @($area) -Branch $branch -Integration "integrate/c1"
        $task | Add-Member -NotePropertyName sha -NotePropertyValue (Invoke-SandboxGit -Root $root -Arguments @("rev-parse", $branch))
        if ($item.ContainsKey("Reports")) { $task.reports = @($item.Reports) }
        [void]$tasks.Add($task)
    }
    Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document (New-Queue -Tasks (@($tasks.ToArray()) + @($ExtraTasks)))
    $lockDocument = if ($null -ne $Lock) { $Lock } else { New-TeamLockReleased }
    Write-TeamJson -Path (Join-Path $root "team\lock.json") -Document $lockDocument

    Set-Content -LiteralPath (Join-Path $tools "docker-ok.cmd") -Value "@exit /b 0" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $tools "docker-down.cmd") -Value "@exit /b 1" -Encoding ASCII
    foreach ($tool in @("uv", "pnpm")) {
        $upper = $tool.ToUpperInvariant()
        Set-Content -LiteralPath (Join-Path $tools "$tool.cmd") -Encoding ASCII -Value (
            "@echo off`r`necho %CD%^|$tool %*>>`"%PAGENTOS_FAKE_TOOL_LOG%`"`r`nif `"%PAGENTOS_FAKE_${upper}_EXIT%`"==`"`" exit /b 0`r`nexit /b %PAGENTOS_FAKE_${upper}_EXIT%")
    }
    [System.IO.File]::WriteAllText((Join-Path $tools "lead-stand-in.ps1"), $leadStandIn, $utf8)
    return $root
}

function Get-SandboxState {
    <# Everything the step could have changed, as one string: refs, worktrees, the queue, the lock. #>
    param([string]$Root)
    $hash = { param($Path) (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash }
    return (@(
            (Invoke-SandboxGit -Root $Root -Arguments @("for-each-ref", "--format=%(refname) %(objectname)")),
            (Invoke-SandboxGit -Root $Root -Arguments @("worktree", "list", "--porcelain")),
            (& $hash (Join-Path $Root "team\queue.json")), (& $hash (Join-Path $Root "team\lock.json")),
            [string](Test-Path -LiteralPath (Join-Path $Root ".claude\worktrees\gate"))
        ) -join "`n")
}

function Invoke-Integrate {
    param(
        [string]$Root, [string]$Gate = "green", [string]$Names = "", [string]$Docker = "ok",
        # "fake-claude" is the repository's own fake (its lead changes nothing here); "stand-in" writes what -LeadWrites names.
        [string]$Lead = "fake-claude", [string]$LeadWrites = "", [hashtable]$Environment = @{},
        [string]$QueueUrl = "", [string]$QueueTokenFile = "", [string]$Machine = "MAIL", [string]$ExtraArguments = "", [switch]$RealTools
    )
    $tools = "$Root-tools"
    $leadScript = if ($Lead -eq "stand-in") { Join-Path $tools "lead-stand-in.ps1" } else { Join-Path $Root "scripts\tests\lib\fake-claude.ps1" }
    $set = @{
        PAGENTOS_FAKE_GATE_SCENARIO = $Gate; PAGENTOS_FAKE_GATE_NAMES = $Names; PAGENTOS_FAKE_GATE_LOG = (Join-Path $tools "gate.log")
        PAGENTOS_FAKE_CLAUDE_SCENARIO = "approve"; PAGENTOS_FAKE_CLAUDE_LOG = (Join-Path $tools "lead.log")
        PAGENTOS_FAKE_LEAD_WRITES = $LeadWrites; PAGENTOS_FAKE_TOOL_LOG = (Join-Path $tools "tools.log")
    }
    foreach ($name in @($Environment.Keys)) { $set[$name] = [string]$Environment[$name] }
    foreach ($name in @($set.Keys)) { Set-Item -Path "Env:\$name" -Value $set[$name] }
    try {
        $command = "& '" + (Join-Path $Root "scripts\team\integrate.ps1") + "' -Machine '$Machine'" +
        " -GatePath '" + (Join-Path $Root "scripts\tests\lib\fake-gate.ps1") + "' -GateMinutes 3 -LeadMinutes 3" +
        " -DockerPath '" + (Join-Path $tools "docker-$Docker.cmd") + "'" +
        $(if ($RealTools) { "" } else { " -UvPath '" + (Join-Path $tools "uv.cmd") + "' -PnpmPath '" + (Join-Path $tools "pnpm.cmd") + "'" }) +
        " -ClaudePath '$powershell' -ClaudePrefixArguments '-NoProfile','-ExecutionPolicy','Bypass','-File','$leadScript'" +
        $(if ($QueueUrl) { " -QueueUrl '$QueueUrl' -QueueToken '$QueueTokenFile'" } else { "" }) +
        $(if ($ExtraArguments) { " " + $ExtraArguments } else { "" }) + "; exit `$LASTEXITCODE"
        $result = Invoke-NativeProcess -FilePath $powershell -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command) `
            -WorkingDirectory $Root -TimeoutSeconds 600
    }
    finally { foreach ($name in @($set.Keys)) { Remove-Item -Path "Env:\$name" -ErrorAction SilentlyContinue } }
    $lines = { param($Path) if (Test-Path -LiteralPath $Path) { @(Get-Content -LiteralPath $Path -Encoding UTF8 | Where-Object { $_.Trim() }) } else { @() } }
    $report = Join-Path $Root "team\reports\c1-integrate.md"
    return [pscustomobject]@{
        ExitCode = $result.ExitCode; Output = ($result.StdOut + $result.StdErr)
        Queue = (Read-TeamJson -Path (Join-Path $Root "team\queue.json")); Lock = (Read-TeamJson -Path (Join-Path $Root "team\lock.json"))
        GateCalls = @(& $lines (Join-Path $tools "gate.log")); ToolCalls = @(& $lines (Join-Path $tools "tools.log"))
        LeadCalls = @(& $lines (Join-Path $tools "lead.log") | ForEach-Object { ConvertFrom-Json -InputObject $_ })
        Report = $(if (Test-Path -LiteralPath $report) { [System.IO.File]::ReadAllText($report, [System.Text.Encoding]::UTF8) } else { "" })
    }
}

function Get-TaskById {
    param($Queue, [string]$Id)
    return @(Get-TeamTasks -Queue $Queue | Where-Object { $_.id -eq $Id })[0]
}

function Get-Sha { param([string]$Root, [string]$Revision) return (Invoke-SandboxGit -Root $Root -Arguments @("rev-parse", $Revision)) }

$one = @(@{ Id = "task-one"; Area = "src/a" })
$two = @(@{ Id = "task-one"; Area = "src/a" }, @{ Id = "task-two"; Area = "src/b" })

try {
    Test-Case "green: main advances to the integration branch's tip by a --no-ff merge, is pushed, the task awaits release with the sha, the lock is released" {
        $root = New-Sandbox -Work $one
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $run = Invoke-Integrate -Root $root -Gate "green"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        $mainAfter = Get-Sha -Root $root -Revision "main"
        $tip = Get-Sha -Root $root -Revision "integrate/c1"
        $parents = @((Invoke-SandboxGit -Root $root -Arguments @("rev-list", "--parents", "-n", "1", "main")) -split " ")
        Assert-Equal -Expected 3 -Actual @($parents).Count -Because "main's new commit is a merge commit (--no-ff): $($parents -join ' ')"
        Assert-Equal -Expected $mainBefore -Actual $parents[1] -Because "its first parent is main as it was"
        Assert-Equal -Expected $tip -Actual $parents[2] -Because "its second parent is the integration branch's tip"
        Assert-True -Condition ((Invoke-SandboxGit -Root $root -Arguments @("log", "-1", "--format=%s", "main")).Contains($tip)) -Because "the merge's message names the gated sha"
        Assert-Equal -Expected (Get-Sha -Root $root -Revision "$tip^{tree}") -Actual (Get-Sha -Root $root -Revision "main^{tree}") -Because "what is on main is, file for file, what the gate ran on"
        Assert-Equal -Expected $mainAfter -Actual (Invoke-SandboxGit -Root "$root-origin.git" -Arguments @("rev-parse", "main")) -Because "main was pushed to origin"

        $task = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "awaiting_release" -Actual $task.state -Because "the Onay Merkezi shows it as 'yayın bekliyor'"
        Assert-True -Condition ($task.sha -cmatch "^[0-9a-f]{40}$") -Because "a forty-hex sha: $($task.sha)"
        Assert-Equal -Expected $mainAfter -Actual $task.sha -Because "the sha is main's"
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $run.Queue).Count -Because "the queue keeps the protocol"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock was released"

        $gateTree = Join-Path $root ".claude\worktrees\gate\integrate\c1"
        Assert-Equal -Expected 1 -Actual @($run.GateCalls).Count -Because "the gate ran once"
        $call = @($run.GateCalls[0] -split "\|")
        Assert-Equal -Expected $gateTree.ToLowerInvariant() -Actual $call[0].ToLowerInvariant() -Because "the gate ran in a worktree of its own, not in the main checkout"
        Assert-Equal -Expected $tip -Actual $call[1] -Because "on the commit that reached main"
        $log = Join-Path $root "team\reports\c1\gate-1.log"
        Assert-True -Condition ((Test-Path -LiteralPath $log) -and [System.IO.File]::ReadAllText($log).Contains("QUALITY GATE: PASS")) -Because "the gate's log is kept beside the reports"
        Assert-Equal -Expected 1 -Actual @($run.LeadCalls).Count -Because "one lead run"
        Assert-Equal -Expected "lead" -Actual $run.LeadCalls[0].role -Because "with the lead's role file"
        Assert-Equal -Expected $gateTree.ToLowerInvariant() -Actual ([string]$run.LeadCalls[0].cwd).ToLowerInvariant() -Because "in the gate worktree"
        Assert-True -Condition ($run.LeadCalls[0].tools -match "Bash" -and $run.LeadCalls[0].tools -match "Edit" -and $run.LeadCalls[0].tools -notmatch "Agent") -Because "its tools, and no agents of its own: $($run.LeadCalls[0].tools)"
        Assert-Equal -Expected "main" -Actual (Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "--abbrev-ref", "HEAD")) -Because "the main checkout stayed on its branch"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $root "src\a\task-one.txt")) -Because "and followed main forward"
        Assert-True -Condition ($run.Report -match "yayın bekliyor" -and $run.Report.Contains($mainAfter) -and $run.Report -match "gate-1\.log") -Because "the report says what is ready, with the sha and the log: $($run.Report)"
        Assert-Equal -Expected 0 -Actual @(Invoke-SandboxGit -Root $root -Arguments @("tag", "--list") | Where-Object { $_ }).Count -Because "no tag was made"
    }

    Test-Case "the lock is held as 'integrate-<branch>' while the step works" {
        $root = New-Sandbox -Work $one
        $copy = Join-Path "$root-tools" "lock-during.json"
        $run = Invoke-Integrate -Root $root -Lead "stand-in" -Environment @{ PAGENTOS_FAKE_LEAD_LOCK = (Join-Path $root "team\lock.json"); PAGENTOS_FAKE_LEAD_LOCK_COPY = $copy }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        $during = Read-TeamJson -Path $copy
        Assert-Equal -Expected $true -Actual ([bool]$during.held) -Because "held while the lead's run was going"
        Assert-Equal -Expected "integrate-integrate/c1" -Actual $during.cycle_id -Because "as the cycle 'integrate-<branch>'"
        Assert-Equal -Expected "MAIL" -Actual $during.machine -Because "by this machine"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "and released at the end"
    }

    Test-Case "red: nothing reaches main; the task whose file the gate named goes back with the gate's words, the other stays merged" {
        $root = New-Sandbox -Work $two
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $run = Invoke-Integrate -Root $root -Gate "red" -Names "src/a/task-one.txt"
        Assert-Equal -Expected 6 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "main is where it was"
        Assert-Equal -Expected $mainBefore -Actual (Invoke-SandboxGit -Root "$root-origin.git" -Arguments @("rev-parse", "main")) -Because "and so is origin's"
        $named = Get-TaskById -Queue $run.Queue -Id "task-one"
        $other = Get-TaskById -Queue $run.Queue -Id "task-two"
        Assert-Equal -Expected "returned" -Actual $named.state -Because "the gate named its file"
        Assert-Equal -Expected "merged" -Actual $other.state -Because "the gate did not name task-two"
        foreach ($task in @($named, $other)) {
            Assert-True -Condition ($task.reason -match "API unit tests") -Because "the failing step is in the reason of $($task.id): $($task.reason)"
            Assert-True -Condition ($task.reason -match "tests/unit/test_gate_probe\.py::test_the_change_holds") -Because "and the first failing test: $($task.reason)"
            Assert-True -Condition ($task.reason -match "team/reports/c1/gate-1\.log") -Because "and the log: $($task.reason)"
        }
        Assert-True -Condition ((New-TeamTaskCard -Task $named -Role "worker" -CycleId "c1") -match "Why this task came back[\s\S]*API unit tests") -Because "the worker's card carries the gate's words"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock was released"
        Assert-True -Condition ($run.Report -match "API unit tests" -and $run.Report -match "test_the_change_holds") -Because "the report names the step and the test: $($run.Report)"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $root "src\a\task-one.txt"))) -Because "the main checkout was not touched"
    }

    Test-Case "a gate that printed PASS and exited 1, said FAIL and exited 0, or never said its last word is not green: main stays" {
        foreach ($scenario in @("pass-exit-1", "fail-exit-0", "silent")) {
            $root = New-Sandbox -Work $one
            $mainBefore = Get-Sha -Root $root -Revision "main"
            $run = Invoke-Integrate -Root $root -Gate $scenario
            Assert-Equal -Expected 6 -Actual $run.ExitCode -Because "${scenario}: $($run.Output)"
            Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "${scenario}: main is where it was"
            Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "${scenario}: the task did not pass"
            Assert-Equal -Expected 1 -Actual @($run.GateCalls).Count -Because "${scenario}: the gate did run"
        }
    }

    Test-Case "a second red gate on the same branch stops it with the TEAM_PROTOCOL 10 line, and it stays stopped until the lead looks" {
        $root = New-Sandbox -Work @(@{ Id = "task-one"; Area = "src/a" }, @{ Id = "task-two"; Area = "src/b"; Extra = "src/shared/base.txt" })
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $first = Invoke-Integrate -Root $root -Gate "red" -Names "src/a/task-one.txt"
        Assert-Equal -Expected 6 -Actual $first.ExitCode -Because $first.Output
        Assert-True -Condition ($first.Report -notmatch "TEAM_PROTOCOL 10") -Because "one red gate does not stop the branch"
        # The gate names a file task-two's branch carries OUTSIDE its area: that is not task-two's file.
        $second = Invoke-Integrate -Root $root -Gate "red" -Names "src/shared/base.txt"
        Assert-Equal -Expected 8 -Actual $second.ExitCode -Because $second.Output
        Assert-True -Condition ($second.Report -match "iki kez kırmızı.*TEAM_PROTOCOL 10") -Because "the stop is a line in the report: $($second.Report)"
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $second.Queue -Id "task-two").state -Because "a file outside its area names nobody: it stays merged"
        Assert-True -Condition ((Get-TaskById -Queue $second.Queue -Id "task-two").reason -match "TEAM_PROTOCOL 10") -Because "and says why it waits"
        Assert-Equal -Expected 2 -Actual @($second.GateCalls).Count -Because "two gates ran"
        $third = Invoke-Integrate -Root $root -Gate "green"
        Assert-Equal -Expected 8 -Actual $third.ExitCode -Because $third.Output
        Assert-Equal -Expected 2 -Actual @($third.GateCalls).Count -Because "a stopped branch is not gated again, even by a gate that would be green"
        Assert-Equal -Expected 2 -Actual @($third.LeadCalls).Count -Because "nor is the lead run again"
        Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "main is where it was"
        Assert-Equal -Expected $false -Actual ([bool]$third.Lock.held) -Because "the lock is not left held by a stop"
        $cleared = Invoke-Integrate -Root $root -Gate "green" -ExtraArguments "-ClearGateStop"
        Assert-Equal -Expected 0 -Actual $cleared.ExitCode -Because $cleared.Output
        Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $cleared.Queue -Id "task-two").state -Because "after the lead looked, a green gate lets it through"
        Assert-Equal -Expected "returned" -Actual (Get-TaskById -Queue $cleared.Queue -Id "task-one").state -Because "a returned task is the worker's, not this step's"
    }

    Test-Case "main having moved is merged into the integration branch BEFORE the gate" {
        $root = New-Sandbox -Work $one
        Set-Content -LiteralPath (Join-Path $root "src\area\elsewhere.txt") -Value "main moved" -Encoding ASCII
        [void](Invoke-SandboxGit -Root $root -Arguments @("add", "src/area/elsewhere.txt"))
        [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-m", "main moved"))
        $mainMoved = Get-Sha -Root $root -Revision "main"
        $run = Invoke-Integrate -Root $root -Gate "green"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        $gated = @($run.GateCalls[0] -split "\|")[1]
        [void](Invoke-SandboxGit -Root $root -Arguments @("merge-base", "--is-ancestor", $mainMoved, $gated))
        Assert-Equal -Expected $gated -Actual (Get-Sha -Root $root -Revision "integrate/c1") -Because "the integration branch holds main's commit, and that is what was gated"
        $parents = @((Invoke-SandboxGit -Root $root -Arguments @("rev-list", "--parents", "-n", "1", "main")) -split " ")
        Assert-Equal -Expected $mainMoved -Actual $parents[1] -Because "main went forward from where it had moved to"
        Assert-Equal -Expected $gated -Actual $parents[2] -Because "to the gated commit"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $root "src\area\elsewhere.txt")) -Because "main's own commit is kept"
    }

    Test-Case "a conflict with main stops the tasks and forces nothing: no gate, main and the integration branch as they were" {
        $root = New-Sandbox -Work $two
        [void](New-Item -ItemType Directory -Force -Path (Join-Path $root "src\a"))
        Set-Content -LiteralPath (Join-Path $root "src\a\task-one.txt") -Value "main wrote this file too" -Encoding ASCII
        [void](Invoke-SandboxGit -Root $root -Arguments @("add", "src/a/task-one.txt"))
        [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-m", "main wrote the same file"))
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $tipBefore = Get-Sha -Root $root -Revision "integrate/c1"
        $run = Invoke-Integrate -Root $root -Gate "green"
        Assert-Equal -Expected 5 -Actual $run.ExitCode -Because $run.Output
        foreach ($id in @("task-one", "task-two")) {
            $task = Get-TaskById -Queue $run.Queue -Id $id
            Assert-Equal -Expected "stopped" -Actual $task.state -Because "$id is stopped"
            Assert-True -Condition ($task.reason -match "main ile çakışma") -Because "with the sentence: $($task.reason)"
        }
        Assert-True -Condition ((Get-TaskById -Queue $run.Queue -Id "task-one").reason -match "src/a/task-one\.txt") -Because "and the file that conflicts"
        Assert-Equal -Expected 0 -Actual @($run.GateCalls).Count -Because "the gate did not run"
        Assert-Equal -Expected 0 -Actual @($run.LeadCalls).Count -Because "nor the lead"
        Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "main is where it was"
        Assert-Equal -Expected $tipBefore -Actual (Get-Sha -Root $root -Revision "integrate/c1") -Because "the integration branch is where it was"
        Assert-Equal -Expected "" -Actual (Invoke-SandboxGit -Root (Join-Path $root ".claude\worktrees\gate\integrate\c1") -Arguments @("status", "--porcelain")) -Because "the failed merge was aborted"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock was released"
    }

    $reports = @(
        [pscustomobject]@{ cycle = "c1"; role = "worker"; at = "2026-10-01T01:00:00Z"; file = ""; cost_usd = 0; outcome = "tamam"; summary = @("## For the lead at merge", "- the OLD report named old/stale.txt") },
        [pscustomobject]@{ cycle = "c1"; role = "worker"; at = "2026-10-01T02:00:00Z"; file = "team/reports/c1/task-one-worker-2.md"; cost_usd = 0; outcome = "tamam"; summary = @("the summary is not read when the file is there") },
        [pscustomobject]@{ cycle = "c1"; role = "inspector"; at = "2026-10-01T03:00:00Z"; file = ""; cost_usd = 0; outcome = "tamam"; summary = @("ran 12 tests", "For the lead at merge: number the ADR of task-one", "APPROVE") })
    $withReports = @(@{ Id = "task-one"; Area = "src/a"; Reports = $reports }, @{ Id = "task-two"; Area = "src/b" })
    $workerReport = "sha: abc`n`n## For the lead at merge`n- mount it: one line in src/wiring/mount.txt`n- add the suite to scripts/quality-gate.ps1`n`n## Open risks`n- src/b/task-two.txt is close by`n"

    Test-Case "the lead's run gets the newest worker and inspector sections; what it wires in docs/ and in a NAMED file is committed on the integration branch and reaches main" {
        $root = New-Sandbox -Work $withReports
        [void](New-Item -ItemType Directory -Force -Path (Join-Path $root "team\reports\c1"))
        [System.IO.File]::WriteAllText((Join-Path $root "team\reports\c1\task-one-worker-2.md"), $workerReport, $utf8)
        $card = Join-Path "$root-tools" "card.txt"
        $run = Invoke-Integrate -Root $root -Lead "stand-in" -LeadWrites "docs/DECISIONS.md;src/wiring/mount.txt;team/plans/note.md" -Environment @{ PAGENTOS_FAKE_LEAD_CARD = $card }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        $text = [System.IO.File]::ReadAllText($card, [System.Text.Encoding]::UTF8)
        Assert-True -Condition ($text.Contains("one line in src/wiring/mount.txt")) -Because "the newest worker report's section, read from its file"
        Assert-True -Condition ($text.Contains("number the ADR of task-one")) -Because "the inspector's section"
        Assert-True -Condition (-not $text.Contains("old/stale.txt")) -Because "an older report is not the newest"
        Assert-True -Condition (-not $text.Contains("is close by")) -Because "what a report says after the section is not the lead's"
        Assert-True -Condition ($text -match "(?m)^## task-two - ") -Because "every merged task is named, with or without a section"
        $subjects = Invoke-SandboxGit -Root $root -Arguments @("log", "--format=%s", "integrate/c1")
        Assert-True -Condition ($subjects -match "(?m)^integrate: the lead's merge wiring") -Because "what the lead changed is a commit on the integration branch: $subjects"
        foreach ($file in @("src\wiring\mount.txt", "team\plans\note.md")) { Assert-True -Condition (Test-Path -LiteralPath (Join-Path $root $file)) -Because "$file reached main" }
        Assert-True -Condition ((Get-Content -LiteralPath (Join-Path $root "docs\DECISIONS.md") -Raw) -match "wired by the lead") -Because "the shared file was written"
        Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "and the gate ran on it"
        Assert-Equal -Expected (Get-Sha -Root $root -Revision "integrate/c1") -Actual @($run.GateCalls[0] -split "\|")[1] -Because "the gate ran on the commit WITH the lead's wiring"
    }

    Test-Case "a lead run that edits a file inside a task's area that no report named is refused: nothing is merged, the gate does not run" {
        foreach ($commits in @("", "1")) {
            $root = New-Sandbox -Work $withReports
            [void](New-Item -ItemType Directory -Force -Path (Join-Path $root "team\reports\c1"))
            [System.IO.File]::WriteAllText((Join-Path $root "team\reports\c1\task-one-worker-2.md"), $workerReport, $utf8)
            $mainBefore = Get-Sha -Root $root -Revision "main"
            $tipBefore = Get-Sha -Root $root -Revision "integrate/c1"
            # docs/ is allowed, src/wiring/mount.txt is named; src/b/extra.txt is inside task-two's area and named by nobody
            # (the worker's report mentions src/b only AFTER its section).
            $run = Invoke-Integrate -Root $root -Lead "stand-in" -LeadWrites "docs/DECISIONS.md;src/wiring/mount.txt;src/b/extra.txt" -Environment @{ PAGENTOS_FAKE_LEAD_COMMIT = $commits }
            $how = if ($commits) { "committed by the lead itself" } else { "left in the working tree" }
            Assert-Equal -Expected 7 -Actual $run.ExitCode -Because "${how}: $($run.Output)"
            Assert-Equal -Expected 0 -Actual @($run.GateCalls).Count -Because "${how}: the gate did not run"
            Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "${how}: nothing reached main"
            Assert-Equal -Expected $tipBefore -Actual (Get-Sha -Root $root -Revision "integrate/c1") -Because "${how}: nor the integration branch"
            foreach ($id in @("task-one", "task-two")) {
                $task = Get-TaskById -Queue $run.Queue -Id $id
                Assert-Equal -Expected "merged" -Actual $task.state -Because "${how}: $id waits for the next attempt"
                Assert-True -Condition ($task.reason -match "src/b/extra\.txt") -Because "${how}: the reason names the file: $($task.reason)"
                Assert-True -Condition ($task.reason -notmatch "mount\.txt" -and $task.reason -notmatch "DECISIONS") -Because "${how}: and only that file: $($task.reason)"
            }
            $gateTree = Join-Path $root ".claude\worktrees\gate\integrate\c1"
            Assert-Equal -Expected "" -Actual (Invoke-SandboxGit -Root $gateTree -Arguments @("status", "--porcelain")) -Because "${how}: what the lead wrote is gone from the gate worktree"
            Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $gateTree "src\b\extra.txt"))) -Because "${how}: the file is not there"
            Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "${how}: the lock was released"
        }
    }

    Test-Case "a lead run that gives no result merges nothing and counts as a failed attempt; the usage limit does not count" {
        $root = New-Sandbox -Work $one
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $limited = Invoke-Integrate -Root $root -Lead "stand-in" -Environment @{ PAGENTOS_FAKE_LEAD_FAIL = "limit" }
        Assert-Equal -Expected 7 -Actual $limited.ExitCode -Because $limited.Output
        Assert-True -Condition ($limited.Report -match "Max kullanım limiti") -Because "the limit is named: $($limited.Report)"
        $failed = Invoke-Integrate -Root $root -Lead "stand-in" -Environment @{ PAGENTOS_FAKE_LEAD_FAIL = "1" }
        Assert-Equal -Expected 7 -Actual $failed.ExitCode -Because $failed.Output
        Assert-Equal -Expected 0 -Actual @($failed.GateCalls).Count -Because "no gate without the lead's wiring"
        Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "main is where it was"
        $again = Invoke-Integrate -Root $root -Lead "stand-in" -Environment @{ PAGENTOS_FAKE_LEAD_FAIL = "1" }
        Assert-Equal -Expected 8 -Actual $again.ExitCode -Because "the limit was not a strike, the two failures are: $($again.Output)"
        Assert-True -Condition ($again.Report -match "TEAM_PROTOCOL 10") -Because $again.Report
    }

    Test-Case "-DryRun prints what it would do and changes nothing" {
        $root = New-Sandbox -Work $two
        $before = Get-SandboxState -Root $root
        $run = Invoke-Integrate -Root $root -Gate "green" -ExtraArguments "-DryRun"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected $before -Actual (Get-SandboxState -Root $root) -Because "no ref, worktree, queue or lock changed"
        Assert-Equal -Expected 0 -Actual (@($run.GateCalls).Count + @($run.LeadCalls).Count) -Because "nothing was started"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $root "team\reports"))) -Because "not even a report was written"
        Assert-True -Condition ($run.Output -match "integrate/c1" -and $run.Output -match "task-one" -and $run.Output -match "task-two" -and $run.Output -match "DRY RUN") -Because "it says what it would do: $($run.Output)"
    }

    Test-Case "Docker down stops the step with the sentence and changes nothing" {
        $root = New-Sandbox -Work $one
        $before = Get-SandboxState -Root $root
        $run = Invoke-Integrate -Root $root -Gate "green" -Docker "down"
        Assert-Equal -Expected 4 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected $before -Actual (Get-SandboxState -Root $root) -Because "no ref, worktree, queue or lock changed"
        Assert-Equal -Expected 0 -Actual (@($run.GateCalls).Count + @($run.LeadCalls).Count) -Because "nothing was started"
        Assert-True -Condition ($run.Report.Contains("Docker çalışmıyor")) -Because "the sentence is in the report: $($run.Report)"
        Assert-True -Condition ($run.Output -match "Docker") -Because "and on the console: $($run.Output)"
    }

    Test-Case "with nothing merged there is nothing to do: no lock, no gate, no report" {
        $root = New-Sandbox -Work @() -ExtraTasks @((New-Task -Id "task-one" -State "approved"), (New-Task -Id "ship-one" -State "awaiting_release" -Area @("src/s") -Integration "integrate/c0"))
        $before = Get-SandboxState -Root $root
        $run = Invoke-Integrate -Root $root -Gate "green" -Docker "down"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected $before -Actual (Get-SandboxState -Root $root) -Because "nothing changed"
        Assert-Equal -Expected 0 -Actual @($run.GateCalls).Count -Because "no gate"
        Assert-Equal -Expected "" -Actual $run.Report -Because "no report"
    }

    Test-Case "the worktree gets its own environment before the gate, and the report says what each part took; a part that fails stops the step" {
        $root = New-Sandbox -Work $one -Services
        $run = Invoke-Integrate -Root $root -Gate "green"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        $gateTree = (Join-Path $root ".claude\worktrees\gate\integrate\c1").ToLowerInvariant()
        $calls = @($run.ToolCalls | ForEach-Object { $_.ToLowerInvariant().Trim() })
        Assert-Equal -Expected 3 -Actual @($calls).Count -Because ($calls -join "; ")
        Assert-Equal -Expected "$gateTree\services\api|uv sync" -Actual $calls[0] -Because "uv sync in the worktree's services/api"
        Assert-Equal -Expected "$gateTree\services\browser|uv sync" -Actual $calls[1] -Because "and in services/browser"
        Assert-Equal -Expected "$gateTree|pnpm install --frozen-lockfile --prefer-offline" -Actual $calls[2] -Because "pnpm where the lock file is"
        foreach ($name in @("services/api \(uv sync\): \d+ sn", "services/browser \(uv sync\): \d+ sn", "apps/web \(pnpm install\): \d+ sn")) {
            Assert-True -Condition ($run.Report -match $name) -Because "the report says what '$name' took: $($run.Report)"
        }
        $root = New-Sandbox -Work $one -Services
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $broken = Invoke-Integrate -Root $root -Gate "green" -Environment @{ PAGENTOS_FAKE_UV_EXIT = "3" }
        Assert-Equal -Expected 10 -Actual $broken.ExitCode -Because $broken.Output
        Assert-Equal -Expected 0 -Actual @($broken.GateCalls).Count -Because "no gate in a worktree without its environment"
        Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "main is where it was"
        $task = Get-TaskById -Queue $broken.Queue -Id "task-one"
        Assert-Equal -Expected "merged" -Actual $task.state -Because "it waits for the next attempt"
        Assert-True -Condition ($task.reason -match "ortam kurulamadı.*services/api") -Because $task.reason
    }

    Test-Case "with main checked out nowhere it is moved as a ref, and the owner's checkout is not touched" {
        $root = New-Sandbox -Work $one
        [void](Invoke-SandboxGit -Root $root -Arguments @("checkout", "-q", "-b", "owner-work"))
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $run = Invoke-Integrate -Root $root -Gate "green"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main^1") -Because "main went forward by the merge"
        Assert-Equal -Expected "owner-work" -Actual (Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "--abbrev-ref", "HEAD")) -Because "the main checkout is on the owner's branch"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $root "src\a\task-one.txt"))) -Because "and its files were not touched"
        Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "the task is on main"
    }

    Test-Case "the owner's uncommitted file in the way of main is not overwritten: main stays, the task waits" {
        $root = New-Sandbox -Work $one
        [void](New-Item -ItemType Directory -Force -Path (Join-Path $root "src\a"))
        Set-Content -LiteralPath (Join-Path $root "src\a\task-one.txt") -Value "the owner's unsaved work" -Encoding ASCII
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $run = Invoke-Integrate -Root $root -Gate "green"
        Assert-Equal -Expected 11 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "main is where it was"
        Assert-Equal -Expected "the owner's unsaved work" -Actual (Get-Content -LiteralPath (Join-Path $root "src\a\task-one.txt") -TotalCount 1) -Because "his file is as he left it"
        $task = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "merged" -Actual $task.state -Because "the task waits"
        Assert-True -Condition ($task.reason -match "main ilerletilemedi") -Because $task.reason
        Assert-Equal -Expected $mainBefore -Actual (Invoke-SandboxGit -Root "$root-origin.git" -Arguments @("rev-parse", "main")) -Because "nothing was pushed"
    }

    Test-Case "a run killed after main moved is finished by the next one without a second gate" {
        $root = New-Sandbox -Work $one
        $first = Invoke-Integrate -Root $root -Gate "green"
        Assert-Equal -Expected 0 -Actual $first.ExitCode -Because $first.Output
        $main = Get-Sha -Root $root -Revision "main"
        # As if the run had died between moving main and writing the queue.
        $queue = Read-TeamJson -Path (Join-Path $root "team\queue.json")
        $task = Get-TaskById -Queue $queue -Id "task-one"
        $task.state = "merged"; $task.sha = (Get-Sha -Root $root -Revision "team/c1/worker-task-one")
        Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document $queue
        $second = Invoke-Integrate -Root $root -Gate "red" -Names "src/a/task-one.txt"
        Assert-Equal -Expected 0 -Actual $second.ExitCode -Because $second.Output
        $after = Get-TaskById -Queue $second.Queue -Id "task-one"
        Assert-Equal -Expected "awaiting_release" -Actual $after.state -Because "the gate was green on exactly this commit, and it is on main"
        Assert-Equal -Expected $main -Actual $after.sha -Because "with main's sha"
        Assert-Equal -Expected 1 -Actual @($second.GateCalls).Count -Because "the gate was not run again"
        Assert-Equal -Expected $main -Actual (Get-Sha -Root $root -Revision "main") -Because "main did not move again"
    }

    Test-Case "without an origin the step still merges, and says in the report that nothing was pushed" {
        $root = New-Sandbox -Work $one -NoOrigin
        $run = Invoke-Integrate -Root $root -Gate "green"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "it is on main"
        Assert-True -Condition ($run.Report -match "origin.*itilmedi") -Because "the report says so: $($run.Report)"
    }

    Test-Case "a push that fails is said loudly, and the task is still what it is: on main" {
        $root = New-Sandbox -Work $one
        [void](Invoke-SandboxGit -Root $root -Arguments @("remote", "set-url", "origin", (Join-Path "$root-tools" "no-such-repository.git")))
        $run = Invoke-Integrate -Root $root -Gate "green"
        Assert-Equal -Expected 9 -Actual $run.ExitCode -Because $run.Output
        $task = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "awaiting_release" -Actual $task.state -Because "the work is on main on this machine"
        Assert-Equal -Expected (Get-Sha -Root $root -Revision "main") -Actual $task.sha -Because "with main's sha"
        Assert-True -Condition ($run.Report -match "itilemedi") -Because "the report says the push failed: $($run.Report)"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock was released"
    }

    Test-Case "end to end: a task the CYCLE worked, inspected and merged is gated and put on main by the step that runs after it" {
        $root = New-Sandbox -Work @() -ExtraTasks @((New-Task -Id "task-one" -State "approved" -Area @("src/area")))
        $env:PAGENTOS_FAKE_CLAUDE_SCENARIO = "approve"
        try {
            $cycle = Invoke-NativeProcess -FilePath $powershell -WorkingDirectory $root -TimeoutSeconds 300 -Arguments @(
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                ("& '" + (Join-Path $root "scripts\team\cycle.ps1") + "' -CycleId c1 -Machine MAIL -ClaudePath '$powershell'" +
                " -ClaudePrefixArguments '-NoProfile','-ExecutionPolicy','Bypass','-File','" + (Join-Path $root "scripts\tests\lib\fake-claude.ps1") + "'; exit `$LASTEXITCODE"))
        }
        finally { Remove-Item Env:\PAGENTOS_FAKE_CLAUDE_SCENARIO -ErrorAction SilentlyContinue }
        Assert-Equal -Expected 0 -Actual $cycle.ExitCode -Because ($cycle.StdOut + $cycle.StdErr)
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue (Read-TeamJson -Path (Join-Path $root "team\queue.json")) -Id "task-one").state -Because "the cycle ends at 'merged'"
        $run = Invoke-Integrate -Root $root -Gate "green"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        $task = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "awaiting_release" -Actual $task.state -Because "and the step takes it to main"
        Assert-Equal -Expected (Get-Sha -Root $root -Revision "main") -Actual $task.sha -Because "with main's sha"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $root "src\area\task-one.txt")) -Because "the worker's file is on main"
        Assert-Equal -Expected 0 -Actual @(Get-TeamUnmetDependencies -Task ([pscustomobject]@{ id = "next"; depends_on = @("task-one") }) -Queue $run.Queue).Count -Because "a task that depends on it no longer waits for a person"
    }

    # ------------------------------------------------------------------ the queue on the Cloud Core
    Write-Host ""
    Write-Host "the queue and the lock on the Cloud Core (the fake listener of the cycle's tests)"

    function Start-FakeApi {
        param([object[]]$Tasks = @(), $Lock = $null)
        $work = Join-Path $env:TEMP ("pagentos-integapi-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
        [void](New-Item -ItemType Directory -Force -Path $work)
        [void]$sandboxes.Add($work)
        $lockDocument = if ($null -ne $Lock) { $Lock } else { New-TeamLockReleased }
        $seed = [pscustomobject]@{ queue = (New-Queue -Tasks $Tasks); lock = $lockDocument }
        [System.IO.File]::WriteAllText((Join-Path $work "seed.json"), (ConvertTo-Json -InputObject $seed -Depth 12), $utf8)
        $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
        $probe.Start(); $port = $probe.LocalEndpoint.Port; $probe.Stop()
        $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + (Join-Path $repoRoot "scripts\team\fake-team-api.ps1") + '"'),
            "-Port", $port, "-Seed", ('"' + (Join-Path $work "seed.json") + '"'), "-Log", ('"' + (Join-Path $work "requests.log") + '"'),
            "-Ready", ('"' + (Join-Path $work "ready") + '"'), "-Token", "test-token")
        $process = Start-Process -FilePath $powershell -ArgumentList $arguments -PassThru -WindowStyle Hidden
        [void]$fakeApis.Add($process)
        $deadline = [datetime]::UtcNow.AddSeconds(40)
        while (-not (Test-Path -LiteralPath (Join-Path $work "ready"))) {
            if ([datetime]::UtcNow -gt $deadline -or $process.HasExited) { throw "the fake team API did not start" }
            Start-Sleep -Milliseconds 200
        }
        $tokenFile = Join-Path $work "token.txt"
        [System.IO.File]::WriteAllText($tokenFile, "test-token`n", $utf8)
        return [pscustomobject]@{ Url = "http://127.0.0.1:$port"; Work = $work; TokenFile = $tokenFile }
    }

    function Get-FakeApiState { param($Api) return (Invoke-JsonUtf8 -Uri ($Api.Url + "/__state")) }

    function Get-FakeApiRequests {
        param($Api)
        # The listener writes a request's line after it answered it; this call is the barrier.
        [void](Get-FakeApiState -Api $Api)
        $log = Join-Path $Api.Work "requests.log"
        if (-not (Test-Path -LiteralPath $log)) { return @() }
        return @(Get-Content -LiteralPath $log -Encoding UTF8 | Where-Object { $_.Trim() -and $_ -notmatch ' /__state ' })
    }

    Test-Case "in API mode the task is read from and written to the API, the lock is taken and released there, the report is posted, and the files are left alone" {
        $root = New-Sandbox -Work $one
        $tasks = @(Get-TeamTasks -Queue (Read-TeamJson -Path (Join-Path $root "team\queue.json")))
        $api = Start-FakeApi -Tasks $tasks
        # The files hold nothing: what is done is done from the API's queue.
        Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document (New-Queue)
        $run = Invoke-Integrate -Root $root -Gate "green" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        $state = Get-FakeApiState -Api $api
        $task = @($state.tasks | Where-Object { $_.id -eq "task-one" })[0]
        Assert-Equal -Expected "awaiting_release" -Actual $task.state -Because "the state was written to the API"
        Assert-Equal -Expected (Get-Sha -Root $root -Revision "main") -Actual $task.sha -Because "with main's sha"
        Assert-Equal -Expected $false -Actual ([bool]$state.lock.held) -Because "the lock was released through the API"
        $requests = @(Get-FakeApiRequests -Api $api)
        Assert-Equal -Expected 2 -Actual @($requests | Where-Object { $_ -match "^POST /v1/team/queue/lock 200" }).Count -Because "one acquire, one release: $($requests -join '; ')"
        Assert-True -Condition (@($requests | Where-Object { $_ -match "^PUT /v1/team/queue/tasks/task-one 200" }).Count -ge 1) -Because ($requests -join "; ")
        Assert-True -Condition ($state.reports.PSObject.Properties["c1-integrate.md"].Value -match "yayın bekliyor") -Because "the report text is in the store"
        Assert-Equal -Expected 0 -Actual @(Get-TeamTasks -Queue $run.Queue).Count -Because "the sandbox's queue.json was not written in API mode"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "nor was its lock.json ever held"
    }

    Test-Case "the other machine's lock stops the step before anything: no gate, no worktree, no write" {
        $root = New-Sandbox -Work $one
        $tasks = @(Get-TeamTasks -Queue (Read-TeamJson -Path (Join-Path $root "team\queue.json")))
        $held = New-TeamLock -Machine "GMKADIRAKBABA" -CycleId "office" -Now ([datetime]::UtcNow.AddHours(-1))
        $api = Start-FakeApi -Tasks $tasks -Lock $held
        $before = Get-SandboxState -Root $root
        $run = Invoke-Integrate -Root $root -Gate "green" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 3 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected $before -Actual (Get-SandboxState -Root $root) -Because "no ref, worktree, queue or lock changed"
        Assert-Equal -Expected 0 -Actual (@($run.GateCalls).Count + @($run.LeadCalls).Count) -Because "nothing was started"
        $state = Get-FakeApiState -Api $api
        Assert-Equal -Expected "GMKADIRAKBABA" -Actual $state.lock.machine -Because "the lock is still theirs"
        Assert-Equal -Expected "merged" -Actual @($state.tasks)[0].state -Because "the queue was not written"
        Assert-Equal -Expected 0 -Actual @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match "^(PUT|DELETE)" -or $_ -match "^POST /v1/team/queue/lock" }).Count -Because "no write at all"
        Assert-True -Condition ($run.Report -match "kilit GMKADIRAKBABA makinesinde") -Because "the stop is a line in the report: $($run.Report)"
    }

    Test-Case "the other machine's lock in the FILE stops it too, and stays theirs" {
        $held = New-TeamLock -Machine "GMKADIRAKBABA" -CycleId "office" -Now ([datetime]::UtcNow.AddHours(-1))
        $root = New-Sandbox -Work $one -Lock $held
        $before = Get-SandboxState -Root $root
        $run = Invoke-Integrate -Root $root -Gate "green"
        Assert-Equal -Expected 3 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected $before -Actual (Get-SandboxState -Root $root) -Because "nothing changed"
        Assert-Equal -Expected "GMKADIRAKBABA" -Actual $run.Lock.machine -Because "the lock is still theirs"
        Assert-Equal -Expected 0 -Actual @($run.GateCalls).Count -Because "no gate"
    }
}
finally {
    foreach ($api in $fakeApis) { try { if (-not $api.HasExited) { $api.Kill() } } catch { } }
    foreach ($root in $sandboxes) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        if (Test-Path -LiteralPath (Join-Path $root ".git")) { try { [void](Invoke-TeamGit -WorkingDirectory $root -Arguments @("worktree", "prune")) } catch { } }
        for ($attempt = 0; $attempt -lt 5; $attempt++) {
            try { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction Stop; break }
            catch { Start-Sleep -Milliseconds 400 }
        }
    }
}

Write-Host ""
Write-Host "team-integrate tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
