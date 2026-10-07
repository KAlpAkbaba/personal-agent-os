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

Test-Case "own lock (6, the rule): what a wiring took out of the gate script - a moved line and a comment are not a removed step; a case-only rename is" {
    $before = "# steps fail via Assert-ExitCode`nInvoke-Step `"A`" {`n  Assert-ExitCode `"a`"`n}`nInvoke-Step `"B`" {`n  Assert-ExitCode `"b`"`n}`nWrite-Host `"QUALITY GATE: PASS`"`nexit 0"
    Assert-Equal -Expected 0 -Actual @(Get-TeamGateShrink -Before $before -After $before).Count -Because "the same file"
    $moved = "# steps fail via Assert-ExitCode`nInvoke-Step `"B`" {`n  Assert-ExitCode `"b`"`n}`nInvoke-Step `"A`" {`n  Assert-ExitCode `"a`"`n}`nWrite-Host `"QUALITY GATE: PASS`"`nexit 0"
    Assert-Equal -Expected 0 -Actual @(Get-TeamGateShrink -Before $before -After $moved).Count -Because "two steps that swapped places"
    Assert-Equal -Expected 0 -Actual @(Get-TeamGateShrink -Before $before -After ($before -replace "# steps fail via Assert-ExitCode", "# a step fails")).Count -Because "a comment that named Assert-ExitCode"
    Assert-Equal -Expected 0 -Actual @(Get-TeamGateShrink -Before $before -After $before.Replace("`r`n", "`n").Replace("`n", "`r`n")).Count -Because "CRLF for LF"
    Assert-Equal -Expected 'silinen satır: Assert-ExitCode "a"' -Actual (@(Get-TeamGateShrink -Before $before -After ($before -creplace '  Assert-ExitCode "a"', '  Assert-ExitCode "A"')) -join "|") -Because "a rename by case only is a rename"
    $gone = @(Get-TeamGateShrink -Before $before -After "")
    Assert-Equal -Expected 5 -Actual @($gone).Count -Because "a deleted file takes every step, every check and the last word: $($gone -join ' | ')"
    Assert-Equal -Expected 0 -Actual @(Get-TeamGateShrink -Before "" -After $before).Count -Because "a new gate takes nothing"
}

Test-Case "own lock (4, the rule): a result is written onto the store's copy only while it is still the task the step took" {
    $was = [pscustomobject]@{ id = "t-one"; state = "merged"; integration_branch = "integrate/c1"; sha = ("a" * 40); title = "x" }
    Assert-Equal -Expected "" -Actual (Get-TeamStepResultConflict -Was $was -Now ([pscustomobject]@{ id = "t-one"; state = "merged"; integration_branch = "integrate/c1"; sha = ("a" * 40); title = "renamed" })) -Because "another field changed: no conflict"
    Assert-True -Condition ((Get-TeamStepResultConflict -Was $was -Now ([pscustomobject]@{ id = "t-one"; state = "returned"; integration_branch = "integrate/c1"; sha = ("a" * 40) })) -match "state") -Because "returned by a new inspection"
    Assert-True -Condition ((Get-TeamStepResultConflict -Was $was -Now ([pscustomobject]@{ id = "t-one"; state = "merged"; integration_branch = "integrate/c2"; sha = ("a" * 40) })) -match "integration_branch") -Because "on another integration branch"
    Assert-True -Condition ((Get-TeamStepResultConflict -Was $was -Now ([pscustomobject]@{ id = "t-one"; state = "merged"; integration_branch = "integrate/c1"; sha = ("b" * 40) })) -match "sha") -Because "merged again with another sha"
    Assert-True -Condition ((Get-TeamStepResultConflict -Was $was -Now $null) -match "yok") -Because "gone from the store"
}

Test-Case "the lead's card carries each task's section and the rule the script will hold it to" {
    $notes = @([pscustomobject]@{ Id = "task-one"; Title = "the first"; Area = @("src/a"); Worker = "mount it in src/wiring/mount.txt"; Inspector = ""; Named = @("src/wiring/mount.txt") })
    $card = New-TeamLeadMergeCard -CycleId "c1" -Branch "integrate/c1" -Notes $notes
    Assert-True -Condition ($card -match "(?m)^## task-one - the first$") -Because "the task is named"
    Assert-True -Condition ($card.Contains("mount it in src/wiring/mount.txt")) -Because "the worker's words reach the lead"
    Assert-True -Condition ($card -match "inspector's newest report\)\s+nothing named") -Because "an empty section says so"
    Assert-True -Condition ($card -match "Do not commit, push, tag, switch branch, run the gate or release") -Because "the lead's run does not release"
    Assert-True -Condition ($card -notmatch "(?m)^- split_file:") -Because "it is not a split card"
    Assert-True -Condition ($card -match "Wait for every command you started" -and $card -match "nothing in the background") -Because "the lead is told that its run ends with its final message: what it left going is stopped and never committed"
}

Test-Case "what a usage limit closes is remembered for the rest of the step: the model that ran, the model the limit names, or every model; a reset that has passed is not trusted twice" {
    $later = Get-TeamTimestamp -Now ([datetime]::UtcNow.AddHours(2))
    $limited = @{}
    $closed = @(Set-TeamModelClosed -Limited $limited -RunModel "claude-fable-5-1" -Result ([pscustomobject]@{ LimitScope = "unknown"; LimitedModel = ""; ResetsAt = "" }))
    Assert-Equal -Expected "claude-fable-5-1" -Actual ($closed -join ",") -Because "'out of usage credits' does not say whose limit it is: the model that ran"
    Assert-True -Condition (Test-TeamModelLimited -Limited $limited -Model "claude-fable-5-1") -Because "undated: closed for as long as this step lives"
    Assert-True -Condition (-not (Test-TeamModelLimited -Limited $limited -Model "claude-opus-5-5")) -Because "the next model down is open"
    $limited = @{}
    $closed = @(Set-TeamModelClosed -Limited $limited -RunModel "claude-opus-5-5" -Result ([pscustomobject]@{ LimitScope = "model"; LimitedModel = "claude-fable-5-1"; ResetsAt = $later }))
    Assert-Equal -Expected "claude-fable-5-1,claude-opus-5-5" -Actual (($closed | Sort-Object) -join ",") -Because "the model the limit names AND the model that just refused the run"
    Assert-Equal -Expected $later -Actual ([string]$limited["claude-fable-5-1"].until) -Because "with its reset"
    $limited = @{}
    $closed = @(Set-TeamModelClosed -Limited $limited -RunModel "claude-fable-5-1" -Result ([pscustomobject]@{ LimitScope = "all"; LimitedModel = ""; ResetsAt = $later }))
    Assert-Equal -Expected 3 -Actual @($closed).Count -Because "a session or weekly limit closes every model"
    Assert-True -Condition ($null -eq (Get-TeamRunModel -Configured "claude-fable-5-1" -Limited $limited -Fallback $true).Model) -Because "and no run is started"
    $limited = @{}
    [void](Set-TeamModelClosed -Limited $limited -RunModel "claude-fable-5-1" -Result ([pscustomobject]@{ LimitScope = "unknown"; LimitedModel = ""; ResetsAt = "2026-10-01T06:00:00Z" }))
    Assert-True -Condition (Test-TeamModelLimited -Limited $limited -Model "claude-fable-5-1") -Because "a reset already past would hand the same model out again: the model stays closed for this step"
}

Test-Case "what the cycles learnt about the limits is read from team/limits.json, and a file that cannot be read is no knowledge" {
    $work = Join-Path $env:TEMP ("pagentos-limits-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    try {
        [void](New-Item -ItemType Directory -Force -Path $work)
        $path = Join-Path $work "limits.json"
        Assert-Equal -Expected 0 -Actual @((Read-TeamLimitedModels -Path $path).Keys).Count -Because "no file, nothing known"
        [System.IO.File]::WriteAllText($path, '{"models":{"claude-fable-5-1":{"until":"2099-01-01T00:00:00Z","type":"seven_day_overage_included"},"gpt-9":{"until":"2099-01-01T00:00:00Z"},"claude-opus-5-5":{"until":""}},"windows":{}}')
        $known = Read-TeamLimitedModels -Path $path
        Assert-Equal -Expected "claude-fable-5-1" -Actual (@($known.Keys) -join ",") -Because "a model of the chain with a dated limit; a name that is not a model and an undated entry are left out"
        Assert-True -Condition (Test-TeamModelLimited -Limited $known -Model "claude-fable-5-1") -Because "and it is limited now"
        [System.IO.File]::WriteAllText($path, "{ not json")
        Assert-Equal -Expected 0 -Actual @((Read-TeamLimitedModels -Path $path).Keys).Count -Because "a broken file never stops the step: the run will say the limit again"
    }
    finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
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

Test-Case "only the FAILING lines of a failing step are the failure's text: a PASS line names nobody, and neither does a gate that died" {
    $log = "=== Required files ===`n  PASS  src/c/three.ps1`n`n=== Script syntax (PowerShell 5.1) ===`n  PASS  scripts\lib\One.ps1`n  FAIL  scripts\lib\Two.ps1`n        unexpected token in scripts\lib\Five.ps1 at line 3`n  PASS  scripts\lib\Four.ps1`nFAILED: script syntax tests exited with code 1`n`n" +
    "=== API unit tests ===`ntests/unit/test_b.py ....                 [ 50%]`ntests/unit/test_a.py ..F.                 [100%]`ntests/unit/test_c.py::test_error_path PASSED       [ 60%]`nE   AssertionError in app/e.py`napp/d.py:12: AssertionError`nFAILED tests/unit/test_a.py::test_it - AssertionError`nFAILED: pytest (unit) exited with code 1`nQUALITY GATE: FAIL"
    $gate = Read-TeamGateLog -Text $log -ExitCode 1
    foreach ($named in @("Two\.ps1", "Five\.ps1", "test_a\.py", "app/e\.py", "app/d\.py")) { Assert-True -Condition ($gate.FailureText -match $named) -Because "'$named' is on a failing line: $($gate.FailureText)" }
    foreach ($passing in @("One\.ps1", "Four\.ps1", "three\.ps1", "test_b\.py", "test_c\.py")) { Assert-True -Condition ($gate.FailureText -notmatch $passing) -Because "'$passing' is only on a passing line: $($gate.FailureText)" }
    $files = @{
        "task-one" = @("scripts/lib/One.ps1"); "task-two" = @("scripts/lib/Two.ps1"); "task-three" = @("src/c/three.ps1")
        "task-a" = @("services/api/tests/unit/test_a.py"); "task-b" = @("services/api/tests/unit/test_b.py"); "task-c" = @("services/api/tests/unit/test_c.py")
    }
    Assert-Equal -Expected "task-a,task-two" -Actual (@(Get-TeamGateBlamedTasks -FailureText $gate.FailureText -TaskFiles $files) -join ",") -Because "one bad script does not return every task that touched a script"
    $died = Read-TeamGateLog -Text "=== Required files ===`n  PASS  scripts\lib\One.ps1`n=== API unit tests ===`ntests/unit/test_a.py ..F" -ExitCode 1
    Assert-True -Condition (-not [bool]$died.Green -and $died.Why -match "son sözünü") -Because "a gate that died is red"
    Assert-Equal -Expected "" -Actual $died.FailureText -Because "and it names nobody: no step said it failed, and the whole log is not a failure"
    Assert-Equal -Expected "" -Actual (Read-TeamGateLog -Text "=== Required files ===`n  PASS  scripts\lib\One.ps1`n" -ExitCode -1 -TimedOut $true).FailureText -Because "nor does a gate that was killed at its cap"
}

Test-Case "which lines of a step are failing lines: the marks of pytest, the PowerShell suites, tsc, ruff, vitest and dotnet - and never a line that says PASS" {
    $failing = @(
        "  FAIL  scripts\lib\Two.ps1", "FAILED tests/unit/test_a.py::test_it - AssertionError", "ERROR tests/unit/test_x.py - ImportError",
        "E   AssertionError: app/a.py", "tests/unit/test_a.py ..F.   [100%]", "app/d.py:12: AssertionError", "app/a.py:3:1: F401 unused import",
        "apps/web/src/b/page.tsx(12,3): error TS2322: no", " FAIL  src/b/page.test.tsx > renders", "  Failed Foo.Baz [2 ms]",
        "X.cs(3,4): error CS1002: ; expected [E:\x\Y.csproj]", "FAILED: pytest (unit) exited with code 1")
    foreach ($line in $failing) { Assert-Equal -Expected 1 -Actual @(Get-TeamGateFailingLines -Lines @($line)).Count -Because "'$line' is a failing line" }
    $passing = @(
        "  PASS  scripts\lib\ErrorHandling.ps1", "tests/unit/test_b.py ....   [ 50%]", "tests/unit/test_error.py::test_fail_path PASSED [ 5%]",
        " ✓ src/b/ok.test.tsx (3 tests)", "  Passed Foo.Bar [1 ms]", "all present", "5400 passed", "", "collected 12 items from tests/unit/test_a.py")
    foreach ($line in $passing) { Assert-Equal -Expected 0 -Actual @(Get-TeamGateFailingLines -Lines @($line)).Count -Because "'$line' is not a failing line" }
    $block = @(Get-TeamGateFailingLines -Lines @("  PASS  a", "  FAIL  the lock is released", "        expected: <False> in src/a/x.txt", "          actual  : <True>", "  PASS  src/b/y.txt", "        not a failure's line: src/c/z.txt"))
    Assert-Equal -Expected 3 -Actual @($block).Count -Because "a failing case and the indented lines under it, until the next case: $($block -join ' / ')"
    Assert-True -Condition (($block -join "`n") -match "src/a/x\.txt" -and ($block -join "`n") -notmatch "src/b/y\.txt|src/c/z\.txt") -Because "what follows a PASS line is not the failure's"
}

Test-Case "a red verdict that was not written to the queue is found again: the LAST attempt, red, on this commit, marked not applied" {
    $rec = { param([string]$Result, [string]$Sha, $Applied) $r = [pscustomobject]@{ n = 1; branch = "integrate/c1"; result = $Result; sha = $Sha }; if ($null -ne $Applied) { $r | Add-Member -NotePropertyName applied -NotePropertyValue $Applied }; $r }
    $a = "a" * 40; $b = "b" * 40
    Assert-True -Condition ($null -ne (Get-TeamGateUnappliedVerdict -Records @((& $rec "red" $a $false)) -Sha $a)) -Because "red on this commit and the queue never got it"
    Assert-True -Condition ($null -eq (Get-TeamGateUnappliedVerdict -Records @((& $rec "red" $a $true)) -Sha $a)) -Because "the queue has it"
    Assert-True -Condition ($null -eq (Get-TeamGateUnappliedVerdict -Records @((& $rec "red" $a $null)) -Sha $a)) -Because "a record from before the mark existed is not written twice"
    Assert-True -Condition ($null -eq (Get-TeamGateUnappliedVerdict -Records @((& $rec "red" $a $false)) -Sha $b)) -Because "a new tip gets a new gate, not an old verdict"
    Assert-True -Condition ($null -eq (Get-TeamGateUnappliedVerdict -Records @((& $rec "red" $a $false), (& $rec "cleared" "" $null)) -Sha $a)) -Because "the lead looked"
    Assert-True -Condition ($null -eq (Get-TeamGateUnappliedVerdict -Records @() -Sha $a)) -Because "no attempt yet"
}

Test-Case "the files the worktree's environment is built from: when the lead's wiring changes one, the environment is built again" {
    foreach ($file in @("services/api/pyproject.toml", "services\api\uv.lock", "pnpm-lock.yaml", "apps/web/package.json", "pnpm-workspace.yaml")) { Assert-True -Condition (Test-TeamEnvironmentInput -Path $file) -Because "'$file' decides what is installed" }
    foreach ($file in @("docs/DECISIONS.md", "services/api/app/main.py", "docs/pyproject.toml.md", "scripts/quality-gate.ps1", "")) { Assert-True -Condition (-not (Test-TeamEnvironmentInput -Path $file)) -Because "'$file' does not" }
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
    Assert-True -Condition (Test-TeamGateStopped -Records (& $r @("error", "error"))) -Because "an attempt that broke AFTER its lead run was paid for is counted: it is not repeated every half hour"
    Assert-True -Condition (Test-TeamGateStopped -Records (& $r @("refs_moved"))) -Because "a ref that moved under the lead's run stops the branch at once, not at the second time"
    Assert-Equal -Expected "refs_moved" -Actual (Get-TeamGateStopKind -Records (& $r @("red", "refs_moved"))) -Because "and the stop says which kind it is"
    Assert-Equal -Expected "strikes" -Actual (Get-TeamGateStopKind -Records (& $r @("red", "red"))) -Because "two failed attempts"
    Assert-Equal -Expected "" -Actual (Get-TeamGateStopKind -Records (& $r @("red"))) -Because "not stopped"
    Assert-True -Condition (-not (Test-TeamGateStopped -Records (& $r @("refs_moved", "cleared")))) -Because "until the lead looked"
}

Test-Case "a commit the gate was red on is not gated again: the same sha as the LAST attempt, and that attempt red" {
    $rec = { param([string]$Result, [string]$Sha) [pscustomobject]@{ n = 1; branch = "integrate/c1"; result = $Result; sha = $Sha } }
    $a = "a" * 40; $b = "b" * 40
    Assert-True -Condition (Test-TeamGateAlreadyRed -Records @((& $rec "red" $a)) -Sha $a) -Because "red on this very commit"
    Assert-True -Condition (-not (Test-TeamGateAlreadyRed -Records @((& $rec "red" $a)) -Sha $b)) -Because "a new tip is a new question"
    Assert-True -Condition (-not (Test-TeamGateAlreadyRed -Records @((& $rec "red" $a), (& $rec "cleared" "")) -Sha $a)) -Because "the lead looked: gate it again"
    Assert-True -Condition (-not (Test-TeamGateAlreadyRed -Records @((& $rec "lead_failed" $a)) -Sha $a)) -Because "a lead run that gave no result is tried again"
    Assert-True -Condition (-not (Test-TeamGateAlreadyRed -Records @((& $rec "green" $a)) -Sha $a)) -Because "green is not red"
    Assert-True -Condition (-not (Test-TeamGateAlreadyRed -Records @() -Sha $a)) -Because "no attempt yet"
    Assert-True -Condition (-not (Test-TeamGateAlreadyRed -Records @((& $rec "red" "")) -Sha "")) -Because "no sha is not a commit"
}

Test-Case "a branch is held while a task whose code is on it is not 'merged': a returned task's code does not ride to main with the others" {
    $queue = New-Queue -Tasks @(
        (New-Task -Id "one-task" -Integration "integrate/c1"), (New-Task -Id "two-task" -State "returned" -Integration "integrate/c1"),
        (New-Task -Id "three-task" -State "in_progress" -Integration "integrate/c1"), (New-Task -Id "four-task" -State "stopped" -Integration "integrate/c1"),
        (New-Task -Id "five-task" -State "awaiting_release" -Integration "integrate/c1"), (New-Task -Id "six-task" -State "done" -Integration "integrate/c1"),
        (New-Task -Id "seven-task" -State "returned" -Integration "integrate/c2"), (New-Task -Id "eight-task" -State "returned"),
        (New-Task -Id "nine-task" -State "inspecting" -Integration "integrate/c1"), (New-Task -Id "ten-task" -State "released" -Integration "integrate/c1"))
    Assert-Equal -Expected "two-task,three-task,four-task,nine-task" -Actual (@(Get-TeamBranchHeldTasks -Queue $queue -Branch "integrate/c1" | ForEach-Object { $_.id }) -join ",") -Because "returned, being reworked, inspected again or stopped: their code is on the branch and has not passed"
    Assert-Equal -Expected "seven-task" -Actual (@(Get-TeamBranchHeldTasks -Queue $queue -Branch "integrate/c2" | ForEach-Object { $_.id }) -join ",") -Because "by branch"
    Assert-Equal -Expected 0 -Actual @(Get-TeamBranchHeldTasks -Queue $queue -Branch "integrate/c3").Count -Because "a branch nobody is on is held by nobody"
    Assert-Equal -Expected 0 -Actual @(Get-TeamBranchHeldTasks -Queue (New-Queue) -Branch "integrate/c1").Count -Because "an empty queue"
}

Test-Case "the refs the lead's run may not move are compared before and after: moved, made and deleted are all named" {
    $a = "a" * 40; $b = "b" * 40
    $before = @{ "refs/heads/main" = $a; "refs/heads/integrate/c1" = $b; "refs/remotes/origin/main" = "" }
    Assert-Equal -Expected 0 -Actual @(Compare-TeamRefValues -Before $before -After $before.Clone()).Count -Because "nothing moved"
    $moved = @(Compare-TeamRefValues -Before $before -After @{ "refs/heads/main" = $b; "refs/heads/integrate/c1" = ""; "refs/remotes/origin/main" = $a })
    Assert-Equal -Expected "refs/heads/integrate/c1,refs/heads/main,refs/remotes/origin/main" -Actual (@($moved | ForEach-Object { $_.Name }) -join ",") -Because "all three, by name"
    $main = @($moved | Where-Object { $_.Name -eq "refs/heads/main" })[0]
    Assert-Equal -Expected "$a>$b" -Actual "$($main.Before)>$($main.After)" -Because "with where it was and where it is"
    Assert-Equal -Expected 1 -Actual @(Compare-TeamRefValues -Before @{ "refs/heads/main" = $a } -After @{}).Count -Because "a ref that is gone has moved"
}

Test-Case "a tool is an .exe or a .cmd, never a .ps1: on PATH, in the fallbacks, and when it is given" {
    $work = Join-Path $env:TEMP ("pagentos-tool-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    try {
        foreach ($folder in @("first", "second", "third")) { [void](New-Item -ItemType Directory -Force -Path (Join-Path $work $folder)) }
        foreach ($file in @("first\pnpm.ps1", "first\pnpm", "second\pnpm.ps1", "second\pnpm.cmd", "third\pnpm.exe", "third\pnpm.cmd", "third\uv.ps1")) {
            Set-Content -LiteralPath (Join-Path $work $file) -Value "x" -Encoding ASCII
        }
        $path = (@("first", "", "no-such-folder", "second", "third") | ForEach-Object { if ($_) { Join-Path $work $_ } else { "" } }) -join ";"
        Assert-Equal -Expected (Join-Path $work "second\pnpm.cmd") -Actual (Resolve-TeamToolPath -Name "pnpm" -SearchPath $path) -Because "the .ps1 and the extensionless shim that come first are not what a process can start"
        Assert-Equal -Expected (Join-Path $work "third\pnpm.exe") -Actual (Resolve-TeamToolPath -Name "pnpm" -SearchPath (Join-Path $work "third")) -Because "in one folder the .exe is taken before the .cmd"
        Assert-Equal -Expected "" -Actual (Resolve-TeamToolPath -Name "uv" -SearchPath $path) -Because "only a uv.ps1 on PATH is no uv"
        Assert-Equal -Expected (Join-Path $work "third\pnpm.exe") -Actual (Resolve-TeamToolPath -Name "uv" -SearchPath $path -Fallbacks @((Join-Path $work "third\uv.ps1"), (Join-Path $work "nowhere\uv.exe"), (Join-Path $work "third\pnpm.exe"))) -Because "a fallback is held to the same rule, and must exist"
        Assert-Equal -Expected (Join-Path $work "second\pnpm.cmd") -Actual (Resolve-TeamToolPath -Given (Join-Path $work "second\pnpm.cmd") -Name "pnpm" -SearchPath "") -Because "a given path is taken as given"
        $threw = $false
        try { [void](Resolve-TeamToolPath -Given (Join-Path $work "first\pnpm.ps1") -Name "pnpm" -SearchPath $path) } catch { $threw = $true }
        Assert-True -Condition $threw -Because "a given .ps1 is refused with its name, not started and failed an hour later"
    }
    finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
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
    Assert-True -Condition ($real -match '(?s)if \(\$(script:)?failed\) \{\s*Write-Host "QUALITY GATE: FAIL"[^\r\n]*\s*exit 1') -Because "the real gate exits 1 when it says FAIL"
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

Test-Case "the step never waits for a usage limit while it holds the team lock: it has no sleep at all, and the library's only one is counted in milliseconds" {
    $step = [System.IO.File]::ReadAllText($integrateScript, [System.Text.Encoding]::UTF8)
    Assert-True -Condition ($step -notmatch "Start-Sleep") -Because "integrate.ps1 holds the lock from the Docker probe to its last line: a wait there stops every cycle (2026-10-02: the feeder waited three days)"
    $library = [System.IO.File]::ReadAllText($integrateLib, [System.Text.Encoding]::UTF8)
    Assert-True -Condition ($library -notmatch "Start-Sleep\s+(-Seconds\s+)?\d" -and $library -notmatch "Start-Sleep -Seconds") -Because "TeamIntegrate.ps1 sleeps only while a stopped process tree dies, in milliseconds"
    foreach ($text in @($step, $library)) { Assert-True -Condition ($text -notmatch "WaitForUsageLimit|MaxLimitWaitMinutes") -Because "there is no switch that would make it wait" }
}

Test-Case "the step has ONE way to start the lead's run, the one that holds it: it never starts a run and puts it into a job afterwards" {
    $step = [System.IO.File]::ReadAllText($integrateScript, [System.Text.Encoding]::UTF8)
    Assert-True -Condition ($step -match "Start-TeamHeldRun " -and $step -notmatch "Start-TeamRun\b") -Because "integrate.ps1 starts the run with Start-TeamHeldRun and never with Start-TeamRun (started first, assigned on the next line: a process started in between was in no job)"
    Assert-True -Condition ($null -eq (Get-Command -Name "Start-TeamRunJob" -ErrorAction SilentlyContinue)) -Because "there is no function that puts an already running process into a job"
    $library = [System.IO.File]::ReadAllText($integrateLib, [System.Text.Encoding]::UTF8)
    Assert-True -Condition ($library.IndexOf("CREATE_SUSPENDED | ") -gt 0) -Because "the command is created suspended"
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
# Steps a test started without waiting (two steps at once): stopped, with their trees, in the end.
$stepProcesses = New-Object System.Collections.ArrayList
$utf8 =New-Object System.Text.UTF8Encoding($false)

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
# The tree as this run FOUND it, one line per run ("<model>|<git status --porcelain, ';'-joined>"):
# a retry one model down must start on a clean tree, whatever the limited run wrote.
if ($env:PAGENTOS_FAKE_LEAD_STATUS) {
    $found = @(& git.exe status --porcelain 2>$null | Where-Object { $_ }) -join ";"
    Add-Content -LiteralPath $env:PAGENTOS_FAKE_LEAD_STATUS -Value "$model|$found" -Encoding UTF8
}
# What the run leaves behind is started FIRST, before its input is read - as the real tool starts
# its hooks and servers. A command that is let run before it is in its job (created running, or
# resumed before the assignment) starts it outside the job, fed or not.
if ($env:PAGENTOS_FAKE_LEAD_LEAVES) {
    # A run that ends while something it started is still going ("tests are running in the background;
    # I will write the report when they finish"). 'escaped' is started by the WMI service: it is no
    # child of this run, so stopping the run's process tree does not reach it.
    $shell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $line = '-NoProfile -ExecutionPolicy Bypass -File "' + $env:PAGENTOS_FAKE_LEAD_LEAVES + '"'
    # Who this run is, for what it leaves behind (it can then tell when the run has ended).
    [System.IO.File]::WriteAllText(($env:PAGENTOS_FAKE_LEAD_LEAVES + ".lead"), [string]$PID)
    if ($env:PAGENTOS_FAKE_LEAD_LEAVES_HOW -eq "escaped") {
        $startup = New-CimInstance -ClassName Win32_ProcessStartup -Property @{ ShowWindow = [uint16]0 } -ClientOnly
        $made = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = ('"' + $shell + '" ' + $line); ProcessStartupInformation = $startup }
        if ($made.ReturnValue -ne 0) { throw "the escaped process was not started (Win32_Process.Create answered $($made.ReturnValue))" }
    }
    else { Start-Process -FilePath $shell -ArgumentList $line -WindowStyle Hidden }
    # The run ends only once what it leaves behind is really there.
    $until = [datetime]::UtcNow.AddSeconds(40)
    while (-not (Test-Path -LiteralPath ($env:PAGENTOS_FAKE_LEAD_LEAVES + ".started"))) {
        if ([datetime]::UtcNow -gt $until) { throw "what the run leaves behind did not start" }
        Start-Sleep -Milliseconds 50
    }
    if ($env:PAGENTOS_FAKE_LEAD_RELEASES) {
        # The step is held just before it puts this run into its job (the step's own seam): it is
        # let go only NOW, with the process already started, and this run stays alive until the
        # step is past the assignment.
        [System.IO.File]::WriteAllText($env:PAGENTOS_FAKE_LEAD_RELEASES, "the process is started")
        $until = [datetime]::UtcNow.AddSeconds(40)
        while (-not (Test-Path -LiteralPath ($env:PAGENTOS_FAKE_LEAD_RELEASES + ".passed")) -and [datetime]::UtcNow -lt $until) { Start-Sleep -Milliseconds 20 }
    }
}
# A run that never reads its input and never ends: the step's cap is what ends it, prompt unread.
if ($env:PAGENTOS_FAKE_LEAD_NO_READ -eq "1") { Start-Sleep -Seconds 120 }
$card = [Console]::In.ReadToEnd()
$here = (Get-Location).ProviderPath
$utf8 = New-Object System.Text.UTF8Encoding($false)
if ($env:PAGENTOS_FAKE_CLAUDE_LOG) {
    $entry = [pscustomobject]@{ role = [System.IO.Path]::GetFileNameWithoutExtension($roleFile); cwd = $here; tools = $tools; model = $model }
    Add-Content -LiteralPath $env:PAGENTOS_FAKE_CLAUDE_LOG -Value ($entry | ConvertTo-Json -Compress) -Encoding UTF8
}
if ($env:PAGENTOS_FAKE_LEAD_CARD) { [System.IO.File]::WriteAllText($env:PAGENTOS_FAKE_LEAD_CARD, $card, $utf8) }
# The model policy: a run started on a model the test named as limited answers the line the real
# tool answered on 2026-10-02 (no rate_limit_event; the result starts with "duration_api_ms") and does nothing else.
$limitedModels = @(([string]$env:PAGENTOS_FAKE_LEAD_LIMITED_MODELS).Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
# A copy of a lock file as it is WHILE the run works (before a limited run answers, too).
if ($env:PAGENTOS_FAKE_LEAD_LOCK -and $env:PAGENTOS_FAKE_LEAD_LOCK_COPY -and (Test-Path -LiteralPath $env:PAGENTOS_FAKE_LEAD_LOCK)) {
    # Read as a holder lets it be read (the step's own lock is held open while it works).
    $held = [System.IO.File]::Open($env:PAGENTOS_FAKE_LEAD_LOCK, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try { $text = (New-Object System.IO.StreamReader($held)).ReadToEnd() } finally { $held.Dispose() }
    [System.IO.File]::WriteAllText($env:PAGENTOS_FAKE_LEAD_LOCK_COPY, $text)
}
if ($model -and $limitedModels -contains $model) {
    # A limited run that WROTE before it answered (the real tool can work for minutes before the limit).
    foreach ($relative in @(([string]$env:PAGENTOS_FAKE_LEAD_LIMITED_WRITES) -split ";" | Where-Object { $_.Trim() })) {
        $target = Join-Path $here ($relative.Trim() -replace "/", "\")
        $folder = Split-Path -Parent $target
        if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
        Add-Content -LiteralPath $target -Value "written by a run that was then limited" -Encoding ASCII
    }
    [Console]::Out.Write('{"duration_api_ms":0,"total_cost_usd":0,"modelUsage":{},"terminal_reason":"api_error","is_error":true,"num_turns":1,"subtype":"success","api_error_status":429,"api_error":"model_requires_usage_credits","result":"You''re out of usage credits. Switch to another model, or manage usage credits at claude.ai/settings/usage?from=cc_cli_limit_message, to continue.","type":"result","duration_ms":640}')
    exit 1
}
# A run that never ends by itself: the step's cap is what ends it.
if ($env:PAGENTOS_FAKE_LEAD_HANG -eq "1") { Start-Sleep -Seconds 120 }
function Invoke-LeadGit {
    # A lead that does with git what its card forbids: one command per ';', before or after it writes.
    param([string]$Lines)
    $ErrorActionPreference = "Continue"
    foreach ($line in @(([string]$Lines) -split ";" | Where-Object { $_.Trim() })) { & git.exe @($line.Trim() -split " ") 2>&1 | Out-Null }
}
Invoke-LeadGit -Lines $env:PAGENTOS_FAKE_LEAD_GIT_BEFORE
foreach ($relative in @(([string]$env:PAGENTOS_FAKE_LEAD_WRITES) -split ";" | Where-Object { $_.Trim() })) {
    $target = Join-Path $here ($relative.Trim() -replace "/", "\")
    $folder = Split-Path -Parent $target
    if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
    Add-Content -LiteralPath $target -Value "wired by the lead" -Encoding ASCII
}
# A wiring that EDITS a file (the gate script): a script the test wrote, run in the worktree.
if ($env:PAGENTOS_FAKE_LEAD_EDIT) { & $env:PAGENTOS_FAKE_LEAD_EDIT }
Invoke-LeadGit -Lines $env:PAGENTOS_FAKE_LEAD_GIT_AFTER
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
            "@echo off`r`necho %CD%^|$tool %*>>`"%PAGENTOS_FAKE_TOOL_LOG%`"`r`n" +
            # A tool that rewrites a tracked file where it runs (uv sync and its lock file), and one that fails once a file is there.
            "if not `"%PAGENTOS_FAKE_${upper}_TOUCH%`"==`"`" echo scribbled by $tool>>`"%PAGENTOS_FAKE_${upper}_TOUCH%`"`r`n" +
            "if not `"%PAGENTOS_FAKE_${upper}_FAIL_IF%`"==`"`" if exist `"%PAGENTOS_FAKE_${upper}_FAIL_IF%`" exit /b 3`r`n" +
            "if `"%PAGENTOS_FAKE_${upper}_EXIT%`"==`"`" exit /b 0`r`nexit /b %PAGENTOS_FAKE_${upper}_EXIT%")
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
        [string]$QueueUrl = "", [string]$QueueTokenFile = "", [string]$Machine = "MAIL", [string]$ExtraArguments = "",
        # -ToolsFromPath passes NO -UvPath and NO -PnpmPath: the step finds them itself, on a PATH that begins with -PathPrefix.
        [switch]$ToolsFromPath, [string]$PathPrefix = "",
        # The caps as the call gives them ("" = none given: the step's own defaults), and a gate script other than the fake.
        [string]$Caps = "-GateMinutes 3 -LeadMinutes 3", [string]$GateScript = "",
        # > 0: the step's output goes to a FILE and its exit is waited for this long at most. A command
        # the step leaked inherits the step's handles; on a pipe the read would wait for it for ever.
        [int]$BoundSeconds = 0,
        # The step's own lock file (API mode). Always the sandbox's: a test never touches the machine's real one.
        [string]$StepLock = "",
        # Started and NOT waited for: the answer is the process and its output file (Wait-IntegrateProcess).
        [switch]$NoWait
    )
    $tools = "$Root-tools"
    if (-not $StepLock) { $StepLock = Join-Path $tools "integrate-step.lock" }
    $leadScript = if ($Lead -eq "stand-in") { Join-Path $tools "lead-stand-in.ps1" } else { Join-Path $Root "scripts\tests\lib\fake-claude.ps1" }
    $set = @{
        PAGENTOS_FAKE_GATE_SCENARIO = $Gate; PAGENTOS_FAKE_GATE_NAMES = $Names; PAGENTOS_FAKE_GATE_LOG = (Join-Path $tools "gate.log")
        PAGENTOS_FAKE_CLAUDE_SCENARIO = "approve"; PAGENTOS_FAKE_CLAUDE_LOG = (Join-Path $tools "lead.log")
        PAGENTOS_FAKE_LEAD_WRITES = $LeadWrites; PAGENTOS_FAKE_TOOL_LOG = (Join-Path $tools "tools.log")
    }
    foreach ($name in @($Environment.Keys)) { $set[$name] = [string]$Environment[$name] }
    foreach ($name in @($set.Keys)) { Set-Item -Path "Env:\$name" -Value $set[$name] }
    $pathBefore = $env:PATH
    if ($PathPrefix) { $env:PATH = $PathPrefix + ";" + $pathBefore }
    try {
        $command = "& '" + (Join-Path $Root "scripts\team\integrate.ps1") + "' -Machine '$Machine'" +
        " -GatePath '" + $(if ($GateScript) { $GateScript } else { Join-Path $Root "scripts\tests\lib\fake-gate.ps1" }) + "'" + $(if ($Caps) { " " + $Caps } else { "" }) +
        " -DockerPath '" + (Join-Path $tools "docker-$Docker.cmd") + "'" +
        $(if ($ToolsFromPath) { "" } else { " -UvPath '" + (Join-Path $tools "uv.cmd") + "' -PnpmPath '" + (Join-Path $tools "pnpm.cmd") + "'" }) +
        " -ClaudePath '$powershell' -ClaudePrefixArguments '-NoProfile','-ExecutionPolicy','Bypass','-File','$leadScript'" +
        $(if ($QueueUrl) { " -QueueUrl '$QueueUrl' -QueueToken '$QueueTokenFile'" } else { "" }) +
        " -StepLockPath '$StepLock'" +
        $(if ($ExtraArguments) { " " + $ExtraArguments } else { "" })
        if ($NoWait) {
            $outFile = Join-Path $tools ("step-output-" + [guid]::NewGuid().ToString("N").Substring(0, 8) + ".txt")
            $start = New-Object System.Diagnostics.ProcessStartInfo
            $start.FileName = $powershell
            $start.Arguments = ConvertTo-NativeArgumentLine -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ($command + " *> '$outFile'; exit `$LASTEXITCODE"))
            $start.WorkingDirectory = $Root
            $start.UseShellExecute = $false
            $start.CreateNoWindow = $true
            $process = [System.Diagnostics.Process]::Start($start)
            [void]$stepProcesses.Add($process)
            return [pscustomobject]@{ Process = $process; OutFile = $outFile }
        }
        if ($BoundSeconds -gt 0) {
            $outFile = Join-Path $tools "step-output.txt"
            $start = New-Object System.Diagnostics.ProcessStartInfo
            $start.FileName = $powershell
            $start.Arguments = ConvertTo-NativeArgumentLine -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ($command + " *> '$outFile'; exit `$LASTEXITCODE"))
            $start.WorkingDirectory = $Root
            $start.UseShellExecute = $false
            $start.CreateNoWindow = $true
            $step = [System.Diagnostics.Process]::Start($start)
            if (-not $step.WaitForExit($BoundSeconds * 1000)) {
                try { $step.Kill() } catch { }
                throw "the step did not end within $BoundSeconds s"
            }
            $said = if (Test-Path -LiteralPath $outFile) { Get-Content -LiteralPath $outFile -Raw } else { "" }
            $result = [pscustomobject]@{ ExitCode = $step.ExitCode; StdOut = [string]$said; StdErr = "" }
        }
        else {
            $result = Invoke-NativeProcess -FilePath $powershell -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ($command + "; exit `$LASTEXITCODE")) `
                -WorkingDirectory $Root -TimeoutSeconds 600
        }
    }
    finally {
        $env:PATH = $pathBefore
        foreach ($name in @($set.Keys)) { Remove-Item -Path "Env:\$name" -ErrorAction SilentlyContinue }
    }
    $lines ={ param($Path) if (Test-Path -LiteralPath $Path) { @(Get-Content -LiteralPath $Path -Encoding UTF8 | Where-Object { $_.Trim() }) } else { @() } }
    $report = Join-Path $Root "team\reports\c1-integrate.md"
    return [pscustomobject]@{
        ExitCode = $result.ExitCode; Output = ($result.StdOut + $result.StdErr)
        Queue = (Read-TeamJson -Path (Join-Path $Root "team\queue.json")); Lock = (Read-TeamJson -Path (Join-Path $Root "team\lock.json"))
        GateCalls = @(& $lines (Join-Path $tools "gate.log")); ToolCalls = @(& $lines (Join-Path $tools "tools.log"))
        LeadCalls = @(& $lines (Join-Path $tools "lead.log") | ForEach-Object { ConvertFrom-Json -InputObject $_ })
        Report = $(if (Test-Path -LiteralPath $report) { [System.IO.File]::ReadAllText($report, [System.Text.Encoding]::UTF8) } else { "" })
        # What the runs that started NOTHING said (the lock, Docker): one line each, beside the reports.
        Skipped = @(& $lines (Join-Path $Root "team\reports\integrate-skipped.log"))
    }
}

function Wait-IntegrateProcess {
    <# A step started with -NoWait, waited for $Seconds at most; one that does not end is stopped WITH its tree, then said. #>
    param($Started, [int]$Seconds = 300)
    if (-not $Started.Process.WaitForExit($Seconds * 1000)) {
        Stop-TeamProcessTree -ProcessId $Started.Process.Id
        [void]$Started.Process.WaitForExit(15000)
        throw "the step started without waiting did not end within $Seconds s (stopped with its tree)"
    }
    $said = if (Test-Path -LiteralPath $Started.OutFile) { [string](Get-Content -LiteralPath $Started.OutFile -Raw) } else { "" }
    return [pscustomobject]@{ ExitCode = $Started.Process.ExitCode; Output = $said }
}

function Get-StepLockPath { param([string]$Root) return (Join-Path "$Root-tools" "integrate-step.lock") }

function Get-TaskById {
    param($Queue, [string]$Id)
    return @(Get-TeamTasks -Queue $Queue | Where-Object { $_.id -eq $Id })[0]
}

function Get-Sha { param([string]$Root, [string]$Revision) return (Invoke-SandboxGit -Root $Root -Arguments @("rev-parse", $Revision)) }

# A git.exe that is put FIRST on the step's PATH and hands every call to the real one. It makes
# one moment of the step visible to a test: just BEFORE `git add -A` runs ("add"), or just AFTER
# `git commit` returned ("commit"), it creates the signal file and waits for the awaited one - so
# a process that writes "after the check" writes at that moment in every run, not in 2 of 10.
$gitShimSource = @'
using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
public static class GitShim {
    public static int Main(string[] args) {
        string real = Environment.GetEnvironmentVariable("PAGENTOS_FAKE_GIT_REAL");
        string on = Environment.GetEnvironmentVariable("PAGENTOS_FAKE_GIT_SIGNAL_ON") ?? "";
        string line = Environment.CommandLine;
        int cut = line.StartsWith("\"") ? line.IndexOf('"', 1) + 1 : line.IndexOf(' ');
        string tail = cut <= 0 ? "" : line.Substring(cut).TrimStart();
        bool isAdd = args.Length > 1 && args[0] == "add" && Array.IndexOf(args, "-A") > 0;
        bool isCommit = args.Length > 0 && args[0] == "commit";
        if (on == "add" && isAdd) { Signal(); }
        ProcessStartInfo psi = new ProcessStartInfo(real, tail);
        psi.UseShellExecute = false;
        int code;
        using (Process process = Process.Start(psi)) { process.WaitForExit(); code = process.ExitCode; }
        if (on == "commit" && isCommit) { Signal(); }
        return code;
    }
    static void Signal() {
        string signal = Environment.GetEnvironmentVariable("PAGENTOS_FAKE_GIT_SIGNAL");
        string awaited = Environment.GetEnvironmentVariable("PAGENTOS_FAKE_GIT_AWAIT");
        if (string.IsNullOrEmpty(signal) || File.Exists(signal)) { return; }
        File.WriteAllText(signal, "now");
        DateTime until = DateTime.UtcNow.AddMilliseconds(4000);
        while (!string.IsNullOrEmpty(awaited) && !File.Exists(awaited) && DateTime.UtcNow < until) { Thread.Sleep(20); }
    }
}
'@
$script:gitShimFolder = ""

function Get-GitShimFolder {
    <# The folder that holds the shim, compiled once for the whole suite. #>
    if ($script:gitShimFolder) { return $script:gitShimFolder }
    $folder = Join-Path $env:TEMP ("pagentos-integ-shim-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    [void](New-Item -ItemType Directory -Force -Path $folder)
    [void]$sandboxes.Add($folder)
    Add-Type -TypeDefinition $gitShimSource -OutputAssembly (Join-Path $folder "git.exe") -OutputType ConsoleApplication
    $script:gitShimFolder = $folder
    return $folder
}

function New-LeftBehind {
    <#
        What a lead run leaves behind when it ends: a process that waits for the shim's signal and
        then writes -Target into the gate worktree. -How "child" is a process the run started
        (in its process tree); "escaped" is one the WMI service started (in nobody's tree).
        -On is the moment it writes at: "add" (before the step stages) or "commit" (after it committed).
        -HeldBy holds the STEP just before it puts the run into its job (the step's seam,
        PAGENTOS_TEAM_INTEGRATE_HOLD_BEFORE_JOB) until the process is started: "lead" lets the step
        go while the run is still alive, "ended" only once the run's own process is gone.
    #>
    param([string]$Root, [string]$Target, [string]$How = "child", [string]$On = "add", [string]$HeldBy = "")
    $tools = "$Root-tools"
    $writer = Join-Path $tools "left-behind.ps1"
    $signal = Join-Path $tools "left-behind.signal"
    $written = Join-Path $tools "left-behind.written"
    $file = Join-Path (Join-Path $Root ".claude\worktrees\gate\integrate\c1") ($Target -replace "/", "\")
    $hold = Join-Path $tools "job-hold"
    $ended = @()
    if ($HeldBy -eq "ended") {
        $ended = @(
            "`$lead = 0; [void][int]::TryParse(([System.IO.File]::ReadAllText('$writer.lead')).Trim(), [ref]`$lead)",
            "`$gone = [datetime]::UtcNow.AddSeconds(60)",
            "while ((Get-Process -Id `$lead -ErrorAction SilentlyContinue) -and [datetime]::UtcNow -lt `$gone) { Start-Sleep -Milliseconds 20 }",
            "Set-Content -LiteralPath '$hold' -Value 'the run has ended' -Encoding ASCII")
    }
    [System.IO.File]::WriteAllText($writer, ((@(
                "Set-Content -LiteralPath '$writer.started' -Value `$PID -Encoding ASCII") + $ended + @(
                "`$until = [datetime]::UtcNow.AddSeconds(90)",
                "while (-not (Test-Path -LiteralPath '$signal')) { if ([datetime]::UtcNow -gt `$until) { exit 0 }; Start-Sleep -Milliseconds 20 }",
                "[void](New-Item -ItemType Directory -Force -Path '$(Split-Path -Parent $file)')",
                "Set-Content -LiteralPath '$file' -Value 'written after the run ended' -Encoding ASCII",
                "Set-Content -LiteralPath '$written' -Value 'done' -Encoding ASCII")) -join "`r`n"), $utf8)
    $held = @{}
    if ($HeldBy) { $held["PAGENTOS_TEAM_INTEGRATE_HOLD_BEFORE_JOB"] = $hold }
    if ($HeldBy -eq "lead") { $held["PAGENTOS_FAKE_LEAD_RELEASES"] = $hold }
    return [pscustomobject]@{
        Started = "$writer.started"; Written = $written; Status = (Join-Path $tools "gate-status.log")
        Environment = $held + @{
            PAGENTOS_FAKE_LEAD_LEAVES = $writer; PAGENTOS_FAKE_LEAD_LEAVES_HOW = $How
            PAGENTOS_FAKE_GIT_REAL = (Get-TeamGit); PAGENTOS_FAKE_GIT_SIGNAL_ON = $On
            PAGENTOS_FAKE_GIT_SIGNAL = $signal; PAGENTOS_FAKE_GIT_AWAIT = $written
            PAGENTOS_FAKE_GATE_STATUS = (Join-Path $tools "gate-status.log")
        }
    }
}

function Get-LeftBehindProcess {
    <# The process a run left behind, while it lives; $null once it is gone (or never started). #>
    param($Left)
    if (-not (Test-Path -LiteralPath $Left.Started)) { return $null }
    $id = 0
    if (-not [int]::TryParse(([System.IO.File]::ReadAllText($Left.Started)).Trim(), [ref]$id)) { return $null }
    return (Get-Process -Id $id -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match "powershell" })
}

function Get-StandInProcess {
    <#
        The lead's command of one sandbox while it lives, found by its command line (the stand-in's
        path is the sandbox's own): a command that never ran one instruction wrote no pid anywhere.
    #>
    param([string]$Root)
    $standIn = Join-Path "$Root-tools" "lead-stand-in.ps1"
    return @(Get-CimInstance -ClassName Win32_Process -Filter "Name='powershell.exe'" |
            Where-Object { ([string]$_.CommandLine).IndexOf($standIn, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 })
}

function Wait-StandInGone {
    <# Waits for the sandbox's lead command to be gone, $Seconds at most; returns what is still there. #>
    param([string]$Root, [int]$Seconds = 15)
    $until = [datetime]::UtcNow.AddSeconds($Seconds)
    do {
        $alive = @(Get-StandInProcess -Root $Root)
        if (@($alive).Count -eq 0) { return @() }
        Start-Sleep -Milliseconds 200
    } while ([datetime]::UtcNow -lt $until)
    return $alive
}

function Test-OnBranch {
    <# Whether a revision holds a file. #>
    param([string]$Root, [string]$Revision, [string]$File)
    return [bool](Invoke-TeamGit -WorkingDirectory $Root -Arguments @("cat-file", "-e", "${Revision}:$File")).Success
}

function Set-SandboxModels {
    <# team/models.json of the sandbox: the setting's FILE form, as a person writes it. #>
    param([string]$Root, [string]$Lead, $Fallback = $null)
    $document = [ordered]@{ roles = [ordered]@{ lead = $Lead } }
    if ($null -ne $Fallback) { $document["fallback"] = [bool]$Fallback }
    Write-TeamJson -Path (Join-Path $Root "team\models.json") -Document ([pscustomobject]$document)
}

function Set-SandboxLimits {
    <# team/limits.json of the sandbox: what the cycles on this machine learnt (the cycle's file). #>
    param([string]$Root, [string[]]$Models, [string]$Until)
    $entries = [ordered]@{}
    foreach ($id in @($Models)) { $entries[$id] = [ordered]@{ until = $Until; type = "seven_day_overage_included"; seen_at = (Get-TeamTimestamp) } }
    Write-TeamJson -Path (Join-Path $Root "team\limits.json") -Document ([pscustomobject]@{ models = [pscustomobject]$entries; windows = [pscustomobject]@{} })
}

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

    Test-Case "a commit the gate was red on is not gated a second time; a second red gate, on a NEW tip, stops the branch with the TEAM_PROTOCOL 10 line until the lead looks" {
        $root = New-Sandbox -Work @(@{ Id = "task-one"; Area = "src/a" }, @{ Id = "task-two"; Area = "src/b"; Extra = "src/shared/base.txt" })
        $mainBefore = Get-Sha -Root $root -Revision "main"
        # The gate names a file task-two's branch carries OUTSIDE its area: that is not task-two's file.
        $first = Invoke-Integrate -Root $root -Gate "red" -Names "src/shared/base.txt"
        Assert-Equal -Expected 6 -Actual $first.ExitCode -Because $first.Output
        Assert-True -Condition ($first.Report -notmatch "TEAM_PROTOCOL 10") -Because "one red gate does not stop the branch"
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $first.Queue -Id "task-two").state -Because "a file outside its area names nobody: it stays merged"
        Assert-True -Condition ((Get-TaskById -Queue $first.Queue -Id "task-one").reason -match "yeni bir commit") -Because "the reason says what the branch waits for: $((Get-TaskById -Queue $first.Queue -Id 'task-one').reason)"
        # Nothing moved: the same commit, the same answer. A gate that WOULD be green must not even be started.
        $same = Invoke-Integrate -Root $root -Gate "green"
        Assert-Equal -Expected 0 -Actual $same.ExitCode -Because $same.Output
        Assert-Equal -Expected 1 -Actual @($same.GateCalls).Count -Because "the commit the gate was red on is not gated again"
        Assert-Equal -Expected 1 -Actual @($same.LeadCalls).Count -Because "nor is a lead run spent on it"
        Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "main is where it was"
        Assert-True -Condition ($same.Output -match "integrate/c1" -and $same.Output -match "red on") -Because "it says why it waits: $($same.Output)"
        Assert-True -Condition ($same.Report -match "kapı kırmızı") -Because "the red gate's report is still the report"
        Assert-Equal -Expected $false -Actual ([bool]$same.Lock.held) -Because "the lock is not held by a wait"
        # main moves: a new tip, a new question.
        Set-Content -LiteralPath (Join-Path $root "src\area\elsewhere.txt") -Value "main moved" -Encoding ASCII
        [void](Invoke-SandboxGit -Root $root -Arguments @("add", "src/area/elsewhere.txt"))
        [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-m", "main moved"))
        $mainMoved = Get-Sha -Root $root -Revision "main"
        $second = Invoke-Integrate -Root $root -Gate "red" -Names "src/shared/base.txt"
        Assert-Equal -Expected 8 -Actual $second.ExitCode -Because $second.Output
        Assert-True -Condition ($second.Report -match "iki kez kırmızı.*TEAM_PROTOCOL 10") -Because "the stop is a line in the report: $($second.Report)"
        Assert-True -Condition ((Get-TaskById -Queue $second.Queue -Id "task-two").reason -match "TEAM_PROTOCOL 10") -Because "and the task says why it waits"
        Assert-Equal -Expected 2 -Actual @($second.GateCalls).Count -Because "two gates ran, on two commits"
        Assert-True -Condition (@($second.GateCalls[0] -split "\|")[1] -ne @($second.GateCalls[1] -split "\|")[1]) -Because "two different commits"
        $third = Invoke-Integrate -Root $root -Gate "green"
        Assert-Equal -Expected 8 -Actual $third.ExitCode -Because $third.Output
        Assert-Equal -Expected 2 -Actual @($third.GateCalls).Count -Because "a stopped branch is not gated again, even by a gate that would be green"
        Assert-Equal -Expected 2 -Actual @($third.LeadCalls).Count -Because "nor is the lead run again"
        Assert-Equal -Expected $mainMoved -Actual (Get-Sha -Root $root -Revision "main") -Because "main is where it was"
        Assert-Equal -Expected $false -Actual ([bool]$third.Lock.held) -Because "the lock is not left held by a stop"
        $cleared = Invoke-Integrate -Root $root -Gate "green" -ExtraArguments "-ClearGateStop"
        Assert-Equal -Expected 0 -Actual $cleared.ExitCode -Because $cleared.Output
        foreach ($id in @("task-one", "task-two")) {
            Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $cleared.Queue -Id $id).state -Because "after the lead looked, a green gate lets $id through"
        }
    }

    Test-Case "the lead's -ClearGateStop gates a waiting commit again (a gate that was red for a reason outside the commit)" {
        $root = New-Sandbox -Work $one
        $red = Invoke-Integrate -Root $root -Gate "red"
        Assert-Equal -Expected 6 -Actual $red.ExitCode -Because $red.Output
        $wait = Invoke-Integrate -Root $root -Gate "green"
        Assert-Equal -Expected 0 -Actual $wait.ExitCode -Because $wait.Output
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $wait.Queue -Id "task-one").state -Because "it waits"
        $again = Invoke-Integrate -Root $root -Gate "green" -ExtraArguments "-ClearGateStop"
        Assert-Equal -Expected 0 -Actual $again.ExitCode -Because $again.Output
        Assert-Equal -Expected 2 -Actual @($again.GateCalls).Count -Because "the lead asked for the gate again"
        Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $again.Queue -Id "task-one").state -Because "and it was green"
    }

    Test-Case "a returned task's code does not reach main: the branch waits WHOLE until the task is merged again, then everything passes one gate" {
        $root = New-Sandbox -Work $two
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $red = Invoke-Integrate -Root $root -Gate "red" -Names "src/b/task-two.txt"
        Assert-Equal -Expected 6 -Actual $red.ExitCode -Because $red.Output
        Assert-Equal -Expected "returned" -Actual (Get-TaskById -Queue $red.Queue -Id "task-two").state -Because "the gate named its file"
        Assert-True -Condition ((Get-TaskById -Queue $red.Queue -Id "task-one").reason -match "task-two") -Because "the task that stays merged says whom it waits for: $((Get-TaskById -Queue $red.Queue -Id 'task-one').reason)"
        # The run gated twice: the whole branch, and the branch rebuilt without task-two (red as well: this fake gate is always red).
        Assert-Equal -Expected 2 -Actual @($red.GateCalls).Count -Because "the whole branch and the rebuilt one"
        # The lead's look does not open this either: a branch goes onto main whole, and task-two has not passed.
        foreach ($extra in @("", "-ClearGateStop")) {
            $wait = Invoke-Integrate -Root $root -Gate "green" -ExtraArguments $extra
            Assert-Equal -Expected 0 -Actual $wait.ExitCode -Because "'$extra': $($wait.Output)"
            Assert-Equal -Expected 2 -Actual @($wait.GateCalls).Count -Because "'$extra': no gate while task-two is returned"
            Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "'$extra': main is where it was - task-two's code is on the branch"
            Assert-Equal -Expected $mainBefore -Actual (Invoke-SandboxGit -Root "$root-origin.git" -Arguments @("rev-parse", "main")) -Because "'$extra': and so is origin's"
            Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $wait.Queue -Id "task-one").state -Because "'$extra': task-one waits"
            Assert-Equal -Expected "returned" -Actual (Get-TaskById -Queue $wait.Queue -Id "task-two").state -Because "'$extra': task-two is the worker's"
            Assert-True -Condition ($wait.Output -match "task-two" -and $wait.Output -match "returned") -Because "'$extra': it says whom it waits for: $($wait.Output)"
        }
        # The worker fixes it, the inspector approves, the cycle merges the branch again.
        $tree = Join-Path "$root-tools" "fix-two"
        [void](Invoke-SandboxGit -Root $root -Arguments @("worktree", "add", "-q", $tree, "team/c1/worker-task-two"))
        Set-Content -LiteralPath (Join-Path $tree "src\b\task-two.txt") -Value "fixed" -Encoding ASCII
        [void](Invoke-SandboxGit -Root $tree -Arguments @("commit", "-q", "-am", "the fix"))
        [void](Invoke-SandboxGit -Root $root -Arguments @("worktree", "remove", "--force", $tree))
        $merge = Merge-TeamBranch -RepoRoot $root -CycleId "c1" -Branch "team/c1/worker-task-two" -Base "main"
        Assert-True -Condition ([bool]$merge.Merged -and -not [bool]$merge.Already) -Because "the fix is merged into the integration branch"
        $queue = Read-TeamJson -Path (Join-Path $root "team\queue.json")
        (Get-TaskById -Queue $queue -Id "task-two").state = "merged"
        Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document $queue
        $green = Invoke-Integrate -Root $root -Gate "green"
        Assert-Equal -Expected 0 -Actual $green.ExitCode -Because $green.Output
        Assert-Equal -Expected 3 -Actual @($green.GateCalls).Count -Because "one gate for the whole branch"
        foreach ($id in @("task-one", "task-two")) { Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $green.Queue -Id $id).state -Because "$id is on main" }
        Assert-Equal -Expected "fixed" -Actual (Invoke-SandboxGit -Root $root -Arguments @("show", "main:src/b/task-two.txt")) -Because "what is on main is the fixed file"
    }

    Test-Case "a gate that leaves a tracked file changed in its worktree does not break the merge: main gets exactly the gated commit" {
        $root = New-Sandbox -Work $one
        $run = Invoke-Integrate -Root $root -Gate "green" -Environment @{ PAGENTOS_FAKE_GATE_TOUCH = "src/a/task-one.txt" }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "the gate was green"
        Assert-Equal -Expected (Get-Sha -Root $root -Revision "integrate/c1^{tree}") -Actual (Get-Sha -Root $root -Revision "main^{tree}") -Because "what the gate scribbled is not on main"
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

    Test-Case "a lead run that moves main, the integration branch or origin's main is refused LOUDLY and stops the branch: the move is named, never 'nothing merged'" {
        $moves = @(
            # main is checked out NOWHERE (the main checkout sits on another branch), so the gate worktree can take it.
            @{ Name = "main"; Ref = "refs/heads/main"; Before = "checkout -q main"; Writes = "src/b/extra.txt"; Commit = "1"; After = "" },
            @{ Name = "the integration branch"; Ref = "refs/heads/integrate/c1"; Before = ""; Writes = ""; Commit = ""; After = "update-ref refs/heads/integrate/c1 refs/heads/main" },
            @{ Name = "origin's main"; Ref = "refs/remotes/origin/main"; Before = ""; Writes = ""; Commit = ""; After = "push -q origin HEAD:refs/heads/main" })
        foreach ($move in $moves) {
            $how = $move.Name
            $root = New-Sandbox -Work $two
            [void](Invoke-SandboxGit -Root $root -Arguments @("checkout", "-q", "-b", "owner-work"))
            $refBefore = Get-Sha -Root $root -Revision $move.Ref
            $originBefore = Invoke-SandboxGit -Root "$root-origin.git" -Arguments @("rev-parse", "main")
            $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -LeadWrites $move.Writes -Environment @{
                PAGENTOS_FAKE_LEAD_GIT_BEFORE = $move.Before; PAGENTOS_FAKE_LEAD_GIT_AFTER = $move.After; PAGENTOS_FAKE_LEAD_COMMIT = $move.Commit
            }
            $refAfter = Get-Sha -Root $root -Revision $move.Ref
            Assert-True -Condition ($refAfter -ne $refBefore) -Because "${how}: the stand-in did move $($move.Ref) (else this case proves nothing)"
            Assert-Equal -Expected 13 -Actual $run.ExitCode -Because "${how}: $($run.Output)"
            Assert-Equal -Expected 0 -Actual @($run.GateCalls).Count -Because "${how}: the gate did not run"
            Assert-True -Condition ($run.Report.Contains($move.Ref) -and $run.Report.Contains($refBefore) -and $run.Report.Contains($refAfter)) -Because "${how}: the report names the ref, where it was and where it is: $($run.Report)"
            Assert-True -Condition ($run.Report.Contains("git update-ref $($move.Ref) $refBefore $refAfter")) -Because "${how}: and the command that puts it back: $($run.Report)"
            Assert-True -Condition ($run.Report -notmatch "hiçbir şey birleştirilmedi" -and $run.Output -notmatch "nothing merged") -Because "${how}: it does not say nothing happened: $($run.Report)"
            Assert-True -Condition ($run.Output.Contains($move.Ref)) -Because "${how}: the console names the ref too: $($run.Output)"
            foreach ($id in @("task-one", "task-two")) {
                $task = Get-TaskById -Queue $run.Queue -Id $id
                Assert-Equal -Expected "merged" -Actual $task.state -Because "${how}: $id did not pass"
                Assert-True -Condition ($task.reason.Contains($move.Ref) -and $task.reason -notmatch "hiçbir şey birleştirilmedi") -Because "${how}: the reason of $id names the ref: $($task.reason)"
            }
            Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "${how}: the lock was released"
            Assert-Equal -Expected "HEAD" -Actual (Invoke-SandboxGit -Root (Join-Path $root ".claude\worktrees\gate\integrate\c1") -Arguments @("rev-parse", "--abbrev-ref", "HEAD")) -Because "${how}: the gate worktree does not keep a branch checked out"
            # The branch is stopped AT ONCE: a gate that would be green must not carry the move to origin.
            $originAfterRun = Invoke-SandboxGit -Root "$root-origin.git" -Arguments @("rev-parse", "main")
            $next = Invoke-Integrate -Root $root -Gate "green"
            Assert-Equal -Expected 8 -Actual $next.ExitCode -Because "${how}: $($next.Output)"
            Assert-Equal -Expected 0 -Actual @($next.GateCalls).Count -Because "${how}: no gate until the lead looked"
            Assert-Equal -Expected 1 -Actual @($next.LeadCalls).Count -Because "${how}: no second lead run"
            Assert-True -Condition ($next.Report.Contains($move.Ref) -and $next.Report -match "TEAM_PROTOCOL 10") -Because "${how}: the stop names the ref: $($next.Report)"
            Assert-Equal -Expected $originAfterRun -Actual (Invoke-SandboxGit -Root "$root-origin.git" -Arguments @("rev-parse", "main")) -Because "${how}: nothing was pushed by the next run"
            if ($move.Ref -ne "refs/remotes/origin/main") { Assert-Equal -Expected $originBefore -Actual $originAfterRun -Because "${how}: origin's main is where it was" }
            Assert-Equal -Expected $refAfter -Actual (Get-Sha -Root $root -Revision $move.Ref) -Because "${how}: the step did not move the ref back by itself (it cannot know whose move it was)"
        }
    }

    Test-Case "a lead run that leaves the branch's history (an amended commit) is refused, even when every file it changed is the lead's" {
        $root = New-Sandbox -Work $one
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $tipBefore = Get-Sha -Root $root -Revision "integrate/c1"
        $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -LeadWrites "docs/DECISIONS.md" -Environment @{ PAGENTOS_FAKE_LEAD_GIT_AFTER = "add -A;commit -q --amend --no-edit" }
        Assert-Equal -Expected 7 -Actual $run.ExitCode -Because $run.Output
        $task = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-True -Condition ($task.reason -match "HEAD dalın dışına") -Because "the reason says the run left the branch: $($task.reason)"
        Assert-True -Condition ($task.reason -notmatch "DECISIONS") -Because "docs/ is the lead's; the file is not what was wrong: $($task.reason)"
        Assert-Equal -Expected 0 -Actual @($run.GateCalls).Count -Because "a commit that is not on the branch is not gated"
        Assert-Equal -Expected $tipBefore -Actual (Get-Sha -Root $root -Revision "integrate/c1") -Because "the integration branch is where it was"
        Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "and so is main"
        Assert-Equal -Expected "lead_refused" -Actual (Read-TeamJson -Path (Join-Path $root "team\reports\c1\gate-1.json")).result -Because "it is a refused lead run: a failed attempt, counted"
        Assert-Equal -Expected $tipBefore -Actual (Get-Sha -Root (Join-Path $root ".claude\worktrees\gate\integrate\c1") -Revision "HEAD") -Because "the gate worktree is back on the branch's tip"
    }

    Test-Case "uv and pnpm are found by the step itself as .exe or .cmd: a pnpm.ps1 and a uv.ps1 that come first on PATH are never what is started" {
        $root = New-Sandbox -Work $one -Services
        $bin = Join-Path "$root-tools" "bin"
        [void](New-Item -ItemType Directory -Force -Path $bin)
        foreach ($tool in @("uv", "pnpm")) {
            Copy-Item -LiteralPath (Join-Path "$root-tools" "$tool.cmd") -Destination (Join-Path $bin "$tool.cmd")
            Set-Content -LiteralPath (Join-Path $bin "$tool.ps1") -Encoding ASCII -Value "Add-Content -LiteralPath '$(Join-Path "$root-tools" "decoy.log")' -Value '$tool.ps1 was started'"
        }
        # No -UvPath, no -PnpmPath.
        $run = Invoke-Integrate -Root $root -Gate "green" -ToolsFromPath -PathPrefix $bin
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        $calls = @($run.ToolCalls | ForEach-Object { $_.ToLowerInvariant().Trim() })
        Assert-Equal -Expected 3 -Actual @($calls).Count -Because "uv twice and pnpm once, through the .cmd files: $($calls -join '; ')"
        Assert-True -Condition ($calls[2] -match "\|pnpm install --frozen-lockfile --prefer-offline$") -Because "pnpm ran: $($calls[2])"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path "$root-tools" "decoy.log"))) -Because "no .ps1 was started"
        Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "the environment was built and the gate ran"
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

    # ------------------------------------------------------------------ only what the diff check saw is committed
    # The first real lead run ended with "tests are running in the background; I will write the
    # report when they finish". What such a run leaves going writes AFTER the step judged the diff.

    Test-Case "a process the lead's run leaves behind is stopped BEFORE its diff is read: what it was about to write inside a task's area reaches neither the branch nor main" {
        $root = New-Sandbox -Work $one
        $left = New-LeftBehind -Root $root -Target "src/a/late.txt" -How "child" -On "add"
        try {
            $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -LeadWrites "docs/DECISIONS.md" -Environment $left.Environment -PathPrefix (Get-GitShimFolder)
            Assert-True -Condition (Test-Path -LiteralPath $left.Started) -Because "the run did leave a process behind (else this case proves nothing): $($run.Output)"
            Assert-True -Condition (-not (Test-Path -LiteralPath $left.Written)) -Because "the process was stopped with the run's process tree, before the step read the diff: it never wrote"
            Assert-True -Condition ($null -eq (Get-LeftBehindProcess -Left $left)) -Because "it is gone"
            Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
            foreach ($revision in @("main", "integrate/c1")) {
                Assert-True -Condition (-not (Test-OnBranch -Root $root -Revision $revision -File "src/a/late.txt")) -Because "a file the diff check never saw is not on $revision"
                Assert-True -Condition (Test-OnBranch -Root $root -Revision $revision -File "src/a/task-one.txt") -Because "the task's own file is on $revision"
            }
            Assert-True -Condition ((Get-Content -LiteralPath (Join-Path $root "docs\DECISIONS.md") -Raw) -match "wired by the lead") -Because "what the lead wired before it ended is on main"
            Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "the run itself kept the rule: the task goes on"
            Assert-True -Condition ($run.Report -match "arkasında \d+ süreç bıraktı") -Because "the report says the run left something going, and that it was stopped: $($run.Report)"
            Assert-True -Condition (Test-Path -LiteralPath $left.Status) -Because "the gate ran"
            Assert-Equal -Expected "" -Actual ([System.IO.File]::ReadAllText($left.Status).Trim()) -Because "on a clean tree"
        }
        finally { Get-LeftBehindProcess -Left $left | Stop-Process -Force -ErrorAction SilentlyContinue }
    }

    # The run was started and only THEN put into its job (2026-10-02, at the merge: 1 of 78 under
    # load - the process WROTE). A process started in between is in no job; a run that has ended by
    # then cannot be assigned at all. The step is held at that very moment, so the window is hit
    # in every run, not in one of 78. The stand-in starts the process BEFORE it reads its input, so
    # a command let run before the assignment - created running, or resumed early and not fed -
    # starts it outside the job: only a command that has run nothing when it is assigned passes.
    foreach ($heldBy in @("lead", "ended")) {
        $moment = if ($heldBy -eq "lead") { "the run still alive when the step goes on" } else { "the run already ended when the step goes on" }
        Test-Case "the lead's run is inside its job BEFORE it can start anything: a process it starts while the step is held before the assignment is stopped with it and never writes ($moment)" {
            $root = New-Sandbox -Work $one
            $left = New-LeftBehind -Root $root -Target "src/a/late.txt" -How "child" -On "add" -HeldBy $heldBy
            try {
                $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -LeadWrites "docs/DECISIONS.md" -Environment $left.Environment -PathPrefix (Get-GitShimFolder) -BoundSeconds 300
                Assert-True -Condition (Test-Path -LiteralPath $left.Started) -Because "the run did start a process (else this case proves nothing): $($run.Output)"
                Assert-True -Condition (-not (Test-Path -LiteralPath $left.Written)) -Because "${moment}: the process was in the run's job from its first moment and was stopped before the step read the diff - it never wrote"
                Assert-True -Condition ($null -eq (Get-LeftBehindProcess -Left $left)) -Because "it is gone"
                Assert-Equal -Expected 0 -Actual @(Wait-StandInGone -Root $root).Count -Because "and so is the run's own command"
                Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
                foreach ($revision in @("main", "integrate/c1")) { Assert-True -Condition (-not (Test-OnBranch -Root $root -Revision $revision -File "src/a/late.txt")) -Because "a file the diff check never saw is not on $revision" }
                Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "the run itself kept the rule: the task goes on"
                Assert-True -Condition ($run.Report -match "arkasında \d+ süreç bıraktı") -Because "the report says the run left something going, and that it was stopped: $($run.Report)"
                Assert-True -Condition ($run.Report -notmatch "tutulamadı") -Because "the run was held: $($run.Report)"
            }
            finally {
                Get-LeftBehindProcess -Left $left | Stop-Process -Force -ErrorAction SilentlyContinue
                Get-StandInProcess -Root $root | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
            }
        }
    }

    Test-Case "a run that cannot be put into a job is refused BEFORE the lead's command starts: no run, no attempt, the command is ended as it was created, the report says it, and the next step goes on" {
        $root = New-Sandbox -Work $one
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $tipBefore = Get-Sha -Root $root -Revision "integrate/c1"
        try {
            # Bounded: a refused command left suspended holds the step's handles, and a read of a pipe would wait for it.
            $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -LeadWrites "docs/DECISIONS.md" -Environment @{ PAGENTOS_TEAM_INTEGRATE_JOB_FAILS = "1" } -BoundSeconds 180
            $leaked = @(Wait-StandInGone -Root $root -Seconds 15)
            Assert-Equal -Expected 0 -Actual @($leaked).Count -Because "the refused command is gone, not left suspended (pids: $(@($leaked | ForEach-Object { $_.ProcessId }) -join ','))"
        }
        finally { Get-StandInProcess -Root $root | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }
        Assert-Equal -Expected 7 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected 0 -Actual @($run.LeadCalls).Count -Because "the lead's command never ran one instruction: it logged nothing"
        Assert-Equal -Expected 0 -Actual @($run.GateCalls).Count -Because "no gate"
        Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "main is where it was"
        Assert-Equal -Expected $tipBefore -Actual (Get-Sha -Root $root -Revision "integrate/c1") -Because "and so is the integration branch"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $root "team\reports\c1\gate-1.json"))) -Because "nothing was paid for: no attempt is counted"
        $task = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "merged" -Actual $task.state -Because "the task waits for the next step"
        Assert-True -Condition ($task.reason -match "süreç ağacı tutulamadı" -and $task.reason -match "sayılmadı") -Because "the reason says why there was no run: $($task.reason)"
        Assert-True -Condition ($run.Report -match "lead koşusu başlatılmadı" -and $run.Report -match "süreç ağacı tutulamadı") -Because "the report says it: $($run.Report)"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock was released"
        $again = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -LeadWrites "docs/DECISIONS.md"
        Assert-Equal -Expected 0 -Actual $again.ExitCode -Because "the next step, with a job: $($again.Output)"
        Assert-Equal -Expected "green" -Actual (Read-TeamJson -Path (Join-Path $root "team\reports\c1\gate-1.json")).result -Because "it is the FIRST attempt: the refused start took no number"
    }

    Test-Case "a writer the step cannot stop (no child of the run) that writes before the commit: the COMMITTED diff is what is checked, the run is refused and nothing is merged" {
        $root = New-Sandbox -Work $one
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $tipBefore = Get-Sha -Root $root -Revision "integrate/c1"
        $left = New-LeftBehind -Root $root -Target "src/a/late.txt" -How "escaped" -On "add"
        try {
            $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -LeadWrites "docs/DECISIONS.md" -Environment $left.Environment -PathPrefix (Get-GitShimFolder)
            Assert-True -Condition (Test-Path -LiteralPath $left.Written) -Because "the file was written after the lead's run ended and before the step committed (else this case proves nothing): $($run.Output)"
            Assert-Equal -Expected 7 -Actual $run.ExitCode -Because $run.Output
            Assert-Equal -Expected 0 -Actual @($run.GateCalls).Count -Because "what was committed holds a file outside the rule: no gate"
            Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "nothing reached main"
            Assert-Equal -Expected $tipBefore -Actual (Get-Sha -Root $root -Revision "integrate/c1") -Because "nor the integration branch"
            $task = Get-TaskById -Queue $run.Queue -Id "task-one"
            Assert-Equal -Expected "merged" -Actual $task.state -Because "the task waits for the next attempt"
            Assert-True -Condition ($task.reason -match "src/a/late\.txt" -and $task.reason -notmatch "DECISIONS") -Because "the reason names the file, and only it: $($task.reason)"
            Assert-Equal -Expected "lead_refused" -Actual (Read-TeamJson -Path (Join-Path $root "team\reports\c1\gate-1.json")).result -Because "a refused lead run: a failed attempt, counted"
            $gateTree = Join-Path $root ".claude\worktrees\gate\integrate\c1"
            Assert-Equal -Expected "" -Actual (Invoke-SandboxGit -Root $gateTree -Arguments @("status", "--porcelain")) -Because "the gate worktree holds nothing of the run"
            Assert-Equal -Expected $tipBefore -Actual (Get-Sha -Root $gateTree -Revision "HEAD") -Because "and is back on the branch's tip"
        }
        finally { Get-LeftBehindProcess -Left $left | Stop-Process -Force -ErrorAction SilentlyContinue }
    }

    Test-Case "a writer the step cannot stop that writes AFTER the commit: the file is not committed, the gate does not run on it, and main gets exactly what was checked" {
        $root = New-Sandbox -Work $one
        $left = New-LeftBehind -Root $root -Target "src/a/late.txt" -How "escaped" -On "commit"
        try {
            $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -LeadWrites "docs/DECISIONS.md" -Environment $left.Environment -PathPrefix (Get-GitShimFolder)
            Assert-True -Condition (Test-Path -LiteralPath $left.Written) -Because "the file was written after the step's commit (else this case proves nothing): $($run.Output)"
            Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
            Assert-Equal -Expected 1 -Actual @($run.GateCalls).Count -Because "the gate ran once"
            Assert-Equal -Expected "" -Actual ([System.IO.File]::ReadAllText($left.Status).Trim()) -Because "on the commit and nothing else: the tree was put back on it before the gate"
            foreach ($revision in @("main", "integrate/c1")) { Assert-True -Condition (-not (Test-OnBranch -Root $root -Revision $revision -File "src/a/late.txt")) -Because "the late file is not on $revision" }
            Assert-Equal -Expected (Get-Sha -Root $root -Revision "integrate/c1") -Actual @($run.GateCalls[0] -split "\|")[1] -Because "the gate ran on the commit that was checked"
            Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "what was checked and gated is on main"
        }
        finally { Get-LeftBehindProcess -Left $left | Stop-Process -Force -ErrorAction SilentlyContinue }
    }

    Test-Case "a lead run that does not end is cut at its cap with everything it started: a failed attempt, nothing merged, nothing left going" {
        $root = New-Sandbox -Work $one
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $left = New-LeftBehind -Root $root -Target "src/a/late.txt" -How "child" -On "add"
        $environment = $left.Environment.Clone()
        $environment["PAGENTOS_FAKE_LEAD_HANG"] = "1"
        try {
            $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -Environment $environment -Caps "-GateMinutes 3 -LeadMinutes 0.4"
            Assert-True -Condition (Test-Path -LiteralPath $left.Started) -Because "the run had started a process of its own before it hung (else this case proves less): $($run.Output)"
            Assert-Equal -Expected 7 -Actual $run.ExitCode -Because $run.Output
            Assert-True -Condition ($null -eq (Get-LeftBehindProcess -Left $left)) -Because "what the run had started is gone with it"
            $task = Get-TaskById -Queue $run.Queue -Id "task-one"
            Assert-True -Condition ($task.reason -match "süre doldu") -Because "the reason says the cap: $($task.reason)"
            Assert-Equal -Expected "lead_failed" -Actual (Read-TeamJson -Path (Join-Path $root "team\reports\c1\gate-1.json")).result -Because "a run that was paid for and gave nothing is an attempt"
            Assert-Equal -Expected 0 -Actual @($run.GateCalls).Count -Because "no gate"
            Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "main is where it was"
            Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock was released"
        }
        finally { Get-LeftBehindProcess -Left $left | Stop-Process -Force -ErrorAction SilentlyContinue }
    }

    Test-Case "a lead run that never reads its input is cut at -LeadMinutes all the same: a prompt larger than the pipe does not hold the step" {
        # 2 MB for the lead at merge: far more than an anonymous pipe holds, so the write of the prompt cannot finish.
        $padding = @(1..20000 | ForEach-Object { "- " + ("~" * 98) })
        $big = @([pscustomobject]@{ cycle = "c1"; role = "inspector"; at = "2026-10-01T03:00:00Z"; file = ""; cost_usd = 0; outcome = "tamam"; summary = @(@("For the lead at merge: number the ADR") + $padding) })
        $root = New-Sandbox -Work @(@{ Id = "task-one"; Area = "src/a"; Reports = $big })
        $mainBefore = Get-Sha -Root $root -Revision "main"
        try {
            $began = [datetime]::UtcNow
            $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -Environment @{ PAGENTOS_FAKE_LEAD_NO_READ = "1" } -Caps "-GateMinutes 3 -LeadMinutes 0.2" -BoundSeconds 180
            $took = ([datetime]::UtcNow - $began).TotalSeconds
            Assert-Equal -Expected 7 -Actual $run.ExitCode -Because $run.Output
            Assert-True -Condition ($took -lt 100) -Because "the step ended at the cap, not at the stand-in's own 120 s ($([int]$took) s)"
            Assert-Equal -Expected 0 -Actual @($run.LeadCalls).Count -Because "the command never read its input (else this case proves nothing)"
            Assert-Equal -Expected 0 -Actual @(Wait-StandInGone -Root $root).Count -Because "the command was stopped at the cap"
            $task = Get-TaskById -Queue $run.Queue -Id "task-one"
            Assert-True -Condition ($task.reason -match "süre doldu") -Because "the reason says the cap: $($task.reason)"
            Assert-Equal -Expected 0 -Actual @($run.GateCalls).Count -Because "no gate"
            Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "main is where it was"
            Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock was released"
        }
        finally { Get-StandInProcess -Root $root | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }
    }

    # ------------------------------------------------------------------ the model policy (TEAM_PROTOCOL 9a, ADR-0214 addenda 7, 10, 13)

    $leadModels = { param($Run) (@($Run.LeadCalls | ForEach-Object { [string]$_.model }) -join ",") }

    Test-Case "the lead's run is given the model the team's setting names for the lead: the default when nothing is stored, the file's when there is one; -Model only fills what the setting does not name" {
        $root = New-Sandbox -Work $one
        $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected "claude-fable-5-1" -Actual (& $leadModels $run) -Because "nothing stored: the lead runs on the strongest model, as in the cycle (Get-TeamModelDefaults)"
        Assert-True -Condition ($run.Report -match "model claude-fable-5-1" -and $run.Report -notmatch "model düşürüldü") -Because "the report names the model; nothing was lowered: $($run.Report)"

        $root = New-Sandbox -Work $one
        Set-SandboxModels -Root $root -Lead "claude-opus-5-5"
        $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -ExtraArguments "-Model claude-sonnet-5-5"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected "claude-opus-5-5" -Actual (& $leadModels $run) -Because "team/models.json names the lead's model, and the setting wins over -Model"

        $root = New-Sandbox -Work $one
        $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -ExtraArguments "-Model claude-sonnet-5-5"
        Assert-Equal -Expected "claude-sonnet-5-5" -Actual (& $leadModels $run) -Because "-Model is the model of a role the setting does not name"

        $root = New-Sandbox -Work $one
        $before = Get-SandboxState -Root $root
        $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -ExtraArguments "-Model 'claude-3 --dangerously-skip-permissions'"
        Assert-Equal -Expected 2 -Actual $run.ExitCode -Because "a value that is not one of the three ids never reaches a command line: $($run.Output)"
        Assert-Equal -Expected $before -Actual (Get-SandboxState -Root $root) -Because "and nothing was done"
        Assert-Equal -Expected 0 -Actual @($run.LeadCalls).Count -Because "no run"
    }

    Test-Case "a lead model the cycles know as limited (team/limits.json) is not started: the run goes one model down and the report says 'model düşürüldü'; a reset that has passed is no limit" {
        $tomorrow = Get-TeamTimestamp -Now ([datetime]::UtcNow.AddDays(1))
        $root = New-Sandbox -Work $one
        Set-SandboxModels -Root $root -Lead "claude-fable-5-1"
        Set-SandboxLimits -Root $root -Models @("claude-fable-5-1") -Until $tomorrow
        $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected "claude-opus-5-5" -Actual (& $leadModels $run) -Because "ONE run, on the next open model down the chain"
        Assert-True -Condition ($run.Report -match "model düşürüldü: claude-fable-5-1 -> claude-opus-5-5") -Because "the report says so: $($run.Report)"
        Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "and the branch is integrated while the lead's model is closed"

        $root = New-Sandbox -Work $one
        Set-SandboxLimits -Root $root -Models @("claude-fable-5-1") -Until (Get-TeamTimestamp -Now ([datetime]::UtcNow.AddHours(-1)))
        $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in"
        Assert-Equal -Expected "claude-fable-5-1" -Actual (& $leadModels $run) -Because "the reset is past: the lead's own model again"
    }

    Test-Case "with the fallback switched off, or every model limited, NO lead run is started and nothing is built or counted: the step says the limit and comes back the next time" {
        $tomorrow = Get-TeamTimestamp -Now ([datetime]::UtcNow.AddDays(1))
        $settings = @(
            @{ Name = "fallback off"; Fallback = $false; Limited = @("claude-fable-5-1") },
            @{ Name = "all limited"; Fallback = $true; Limited = @("claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5") })
        foreach ($setting in $settings) {
            $how = $setting.Name
            $root = New-Sandbox -Work $one -Services
            $mainBefore = Get-Sha -Root $root -Revision "main"
            Set-SandboxModels -Root $root -Lead "claude-fable-5-1" -Fallback $setting.Fallback
            Set-SandboxLimits -Root $root -Models $setting.Limited -Until $tomorrow
            foreach ($attempt in @(1, 2, 3)) {
                $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in"
                Assert-Equal -Expected 7 -Actual $run.ExitCode -Because "${how}, run ${attempt}: never the TEAM_PROTOCOL 10 stop - a usage limit is not a failed attempt: $($run.Output)"
            }
            Assert-Equal -Expected 0 -Actual @($run.LeadCalls).Count -Because "${how}: no lead run was started on a model that is closed"
            Assert-Equal -Expected 0 -Actual @($run.ToolCalls).Count -Because "${how}: the environment is not built for a run that cannot start"
            Assert-Equal -Expected 0 -Actual @($run.GateCalls).Count -Because "${how}: no gate"
            Assert-Equal -Expected 0 -Actual @(Get-TeamGateRecords -Directory (Join-Path $root "team\reports\c1") -Branch "integrate/c1").Count -Because "${how}: nothing is recorded as an attempt"
            Assert-True -Condition ($run.Report -match "Max kullanım limiti" -and $run.Report.Contains($tomorrow) -and $run.Report -match "sayılmadı" -and $run.Report -match "beklenmedi") -Because "${how}: the report says the limit, its reset, and that it neither counted nor waited: $($run.Report)"
            Assert-True -Condition ($run.Report -notmatch "TEAM_PROTOCOL 10") -Because "${how}: the branch is not stopped"
            $task = Get-TaskById -Queue $run.Queue -Id "task-one"
            Assert-Equal -Expected "merged" -Actual $task.state -Because "${how}: the task waits"
            Assert-True -Condition ($task.reason -match "Max kullanım limiti") -Because "${how}: and says why: $($task.reason)"
            Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "${how}: main is where it was"
            Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "${how}: the lock was released"
        }
    }

    Test-Case "the first model answers 'out of usage credits': the same wiring run is started again AT ONCE one model down, the report says 'model düşürüldü', and nothing is counted" {
        $root = New-Sandbox -Work $one
        Set-SandboxModels -Root $root -Lead "claude-fable-5-1"
        $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -LeadWrites "docs/DECISIONS.md" -Environment @{ PAGENTOS_FAKE_LEAD_LIMITED_MODELS = "claude-fable-5-1" }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected "claude-fable-5-1,claude-opus-5-5" -Actual (& $leadModels $run) -Because "the lead's model first, then one down - in the same step, not half an hour later"
        Assert-True -Condition ($run.Report -match "model düşürüldü: claude-fable-5-1 -> claude-opus-5-5") -Because "the report says the run was lowered: $($run.Report)"
        Assert-True -Condition ($run.Report -match "Max kullanım limiti") -Because "and why: $($run.Report)"
        Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "the wiring was done on the lower model and the gate ran"
        Assert-True -Condition ((Get-Content -LiteralPath (Join-Path $root "docs\DECISIONS.md") -Raw) -match "wired by the lead") -Because "what the second run wired is on main"
        $records = @(Get-TeamGateRecords -Directory (Join-Path $root "team\reports\c1") -Branch "integrate/c1")
        Assert-Equal -Expected "green" -Actual (@($records | ForEach-Object { $_.result }) -join ",") -Because "the limited run left no failed attempt behind"
        Assert-Equal -Expected 1 -Actual @($run.GateCalls).Count -Because "one gate"
    }

    Test-Case "every model out of usage credits, run after run: never a failed attempt, never the TEAM_PROTOCOL 10 stop, never waited for - and the branch goes on when a model opens" {
        $root = New-Sandbox -Work $one
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $all = "claude-fable-5-1,claude-opus-5-5,claude-sonnet-5-5"
        foreach ($attempt in @(1, 2, 3)) {
            $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -Environment @{ PAGENTOS_FAKE_LEAD_LIMITED_MODELS = $all }
            Assert-Equal -Expected 7 -Actual $run.ExitCode -Because "run ${attempt}: $($run.Output)"
            Assert-Equal -Expected (3 * $attempt) -Actual @($run.LeadCalls).Count -Because "run ${attempt}: one try per model of the chain, and no more"
        }
        Assert-Equal -Expected "$all,$all,$all" -Actual (& $leadModels $run) -Because "down the chain each time, never up"
        Assert-Equal -Expected 0 -Actual @(Get-TeamGateRecords -Directory (Join-Path $root "team\reports\c1") -Branch "integrate/c1").Count -Because "a usage limit is never counted against the branch"
        Assert-True -Condition ($run.Report -match "Max kullanım limiti" -and $run.Report -match "sayılmadı" -and $run.Report -match "beklenmedi" -and $run.Report -notmatch "TEAM_PROTOCOL 10") -Because $run.Report
        Assert-Equal -Expected 0 -Actual @($run.GateCalls).Count -Because "no gate without the wiring"
        Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "main is where it was"
        Assert-Equal -Expected "" -Actual (Invoke-SandboxGit -Root (Join-Path $root ".claude\worktrees\gate\integrate\c1") -Arguments @("status", "--porcelain")) -Because "the gate worktree is clean"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock was released"
        $open = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -Environment @{ PAGENTOS_FAKE_LEAD_LIMITED_MODELS = "claude-fable-5-1" }
        Assert-Equal -Expected 0 -Actual $open.ExitCode -Because "a model opened: $($open.Output)"
        Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $open.Queue -Id "task-one").state -Because "and the branch is on main"
    }

    Test-Case "a limit that says when it lifts is not waited for while the lock is held, however soon; a session limit closes every model, so nothing is lowered into it" {
        # The repository's own fake: the real tool's `rejected` event with the limit's type and its reset.
        $root = New-Sandbox -Work $one
        $all = "claude-fable-5-1,claude-opus-5-5,claude-sonnet-5-5"
        $run = Invoke-Integrate -Root $root -Gate "green" -Environment @{ PAGENTOS_FAKE_CLAUDE_LIMITED_MODELS = $all; PAGENTOS_FAKE_CLAUDE_LIMIT_RESET_SECONDS = "120" }
        Assert-Equal -Expected 7 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected $all -Actual (& $leadModels $run) -Because "each model's own limit: one try each - and no try after a wait for the reset two minutes away"
        Assert-True -Condition ($run.Report -match "beklenmedi" -and $run.Report -match "sıfırlanma 20\d\d-") -Because "the report says the reset and that it was not waited for: $($run.Report)"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock was released"

        $root = New-Sandbox -Work $one
        $run = Invoke-Integrate -Root $root -Gate "green" -Environment @{ PAGENTOS_FAKE_CLAUDE_LIMITED_MODELS = $all; PAGENTOS_FAKE_CLAUDE_LIMIT_TYPE = "five_hour" }
        Assert-Equal -Expected 7 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected "claude-fable-5-1" -Actual (& $leadModels $run) -Because "the session limit closes every model: a lower run would hit the same limit"
        Assert-Equal -Expected 0 -Actual @(Get-TeamGateRecords -Directory (Join-Path $root "team\reports\c1") -Branch "integrate/c1").Count -Because "not counted"
    }

    Test-Case "-DryRun prints what it would do and changes nothing" {
        $root = New-Sandbox -Work $two
        $before = Get-SandboxState -Root $root
        $run = Invoke-Integrate -Root $root -Gate "green" -ExtraArguments "-DryRun"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected $before -Actual (Get-SandboxState -Root $root) -Because "no ref, worktree, queue or lock changed"
        Assert-Equal -Expected 0 -Actual (@($run.GateCalls).Count + @($run.LeadCalls).Count) -Because "nothing was started"
        Assert-True -Condition ($run.Output -match "claude-fable-5-1") -Because "it says which model the lead's run would be started on: $($run.Output)"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $root "team\reports"))) -Because "not even a report was written"
        Assert-True -Condition ($run.Output -match "integrate/c1" -and $run.Output -match "task-one" -and $run.Output -match "task-two" -and $run.Output -match "DRY RUN") -Because "it says what it would do: $($run.Output)"
    }

    Test-Case "Docker down stops the step with the sentence and changes nothing - not even the branch's report of the last run that did something" {
        $root = New-Sandbox -Work $one
        # The report the last red gate left: it is what the lead reads until a run DOES something again.
        [void](New-Item -ItemType Directory -Force -Path (Join-Path $root "team\reports"))
        $reportFile = Join-Path $root "team\reports\c1-integrate.md"
        [System.IO.File]::WriteAllText($reportFile, "# Entegrasyon raporu`n`nkapı kırmızı: API unit tests`n", $utf8)
        $before = Get-SandboxState -Root $root
        $run = Invoke-Integrate -Root $root -Gate "green" -Docker "down"
        Assert-Equal -Expected 4 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected $before -Actual (Get-SandboxState -Root $root) -Because "no ref, worktree, queue or lock changed"
        Assert-Equal -Expected 0 -Actual (@($run.GateCalls).Count + @($run.LeadCalls).Count) -Because "nothing was started"
        Assert-Equal -Expected "# Entegrasyon raporu`n`nkapı kırmızı: API unit tests`n" -Actual $run.Report -Because "a run that started nothing does not overwrite the report of one that did"
        Assert-Equal -Expected 1 -Actual @($run.Skipped).Count -Because "it leaves one line of its own: $($run.Skipped -join ' / ')"
        Assert-True -Condition ($run.Skipped[0].Contains("Docker çalışmıyor") -and $run.Skipped[0].Contains("integrate/c1") -and $run.Skipped[0].Contains("MAIL")) -Because "the sentence, the branch and the machine: $($run.Skipped[0])"
        Assert-True -Condition ($run.Output -match "Docker") -Because "and on the console: $($run.Output)"
        $again = Invoke-Integrate -Root $root -Gate "green" -Docker "down"
        Assert-Equal -Expected 2 -Actual @($again.Skipped).Count -Because "a line a run, appended"
    }

    Test-Case "the lines of the runs that started nothing are bounded: the oldest go" {
        $work = Join-Path $env:TEMP ("pagentos-skipped-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
        try {
            $file = Join-Path $work "reports\integrate-skipped.log"
            foreach ($n in 1..205) { Add-TeamSkippedLine -Path $file -Line "line $n çalışmıyor" -Keep 200 }
            $kept = @(Get-Content -LiteralPath $file -Encoding UTF8)
            Assert-Equal -Expected 200 -Actual @($kept).Count -Because "the last two hundred"
            Assert-Equal -Expected "line 6 çalışmıyor|line 205 çalışmıyor" -Actual ($kept[0] + "|" + $kept[199]) -Because "the oldest five went, Turkish text is whole"
        }
        finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
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
        # The environment is built BEFORE the lead's run: a broken uv costs no model run, however often the step comes back.
        foreach ($n in 2..3) {
            $broken = Invoke-Integrate -Root $root -Gate "green" -Environment @{ PAGENTOS_FAKE_UV_EXIT = "3" }
            Assert-Equal -Expected 10 -Actual $broken.ExitCode -Because "run ${n}: $($broken.Output)"
        }
        Assert-Equal -Expected 0 -Actual @($broken.LeadCalls).Count -Because "three runs that could not build the environment started no lead run"
        Assert-Equal -Expected 0 -Actual @($broken.GateCalls).Count -Because "and no gate"
    }

    Test-Case "what the environment's build scribbles on a tracked file is not held against the lead's run, and is not what is gated" {
        $root = New-Sandbox -Work $one -Services
        $run = Invoke-Integrate -Root $root -Gate "green" -Environment @{ PAGENTOS_FAKE_UV_TOUCH = "pyproject.toml" }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "the lead's run changed nothing and was not refused"
        Assert-Equal -Expected "[project]" -Actual (Invoke-SandboxGit -Root $root -Arguments @("show", "main:services/api/pyproject.toml")) -Because "the scribble did not reach main"
    }

    $envReports = @([pscustomobject]@{ cycle = "c1"; role = "worker"; at = "2026-10-01T01:00:00Z"; file = ""; cost_usd = 0; outcome = "tamam"
            summary = @("## For the lead at merge", "- add the dependency line to services/api/pyproject.toml", "- and the marker services/api/break.cfg") })

    Test-Case "when the lead's wiring changes a file the environment is built from, it is built again before the gate; a build that then fails is a failed attempt, counted" {
        $root = New-Sandbox -Work @(@{ Id = "task-one"; Area = "src/a"; Reports = $envReports }) -Services
        $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -LeadWrites "services/api/pyproject.toml"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected 6 -Actual @($run.ToolCalls).Count -Because "built before the lead's run and again after it: $($run.ToolCalls -join '; ')"
        Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "and the gate was green"

        $root = New-Sandbox -Work @(@{ Id = "task-one"; Area = "src/a"; Reports = $envReports }) -Services
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $tipBefore = Get-Sha -Root $root -Revision "integrate/c1"
        $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -LeadWrites "services/api/pyproject.toml;services/api/break.cfg" -Environment @{ PAGENTOS_FAKE_UV_FAIL_IF = "break.cfg" }
        Assert-Equal -Expected 7 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected 0 -Actual @($run.GateCalls).Count -Because "no gate in an environment the wiring broke"
        Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "main is where it was"
        Assert-Equal -Expected $tipBefore -Actual (Get-Sha -Root $root -Revision "integrate/c1") -Because "and the wiring that broke the environment is not on the integration branch"
        Assert-Equal -Expected "lead_failed" -Actual (Read-TeamJson -Path (Join-Path $root "team\reports\c1\gate-1.json")).result -Because "a lead run was paid for: the attempt is counted"
        Assert-True -Condition ((Get-TaskById -Queue $run.Queue -Id "task-one").reason -match "ortam") -Because "the reason says what broke: $((Get-TaskById -Queue $run.Queue -Id 'task-one').reason)"
    }

    Test-Case "a lead run that MOVES a file out of a task's area into docs/ is refused: a rename is a deletion in the area, and nothing reaches main" {
        foreach ($commits in @("", "1")) {
            $root = New-Sandbox -Work $two
            $mainBefore = Get-Sha -Root $root -Revision "main"
            $tipBefore = Get-Sha -Root $root -Revision "integrate/c1"
            $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -Environment @{ PAGENTOS_FAKE_LEAD_GIT_AFTER = "mv src/a/task-one.txt docs/task-one.txt"; PAGENTOS_FAKE_LEAD_COMMIT = $commits }
            $how = if ($commits) { "committed by the lead itself" } else { "left staged" }
            Assert-Equal -Expected 7 -Actual $run.ExitCode -Because "${how}: $($run.Output)"
            Assert-Equal -Expected 0 -Actual @($run.GateCalls).Count -Because "${how}: the gate did not run"
            Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "${how}: nothing reached main"
            Assert-Equal -Expected $tipBefore -Actual (Get-Sha -Root $root -Revision "integrate/c1") -Because "${how}: nor the integration branch"
            $task = Get-TaskById -Queue $run.Queue -Id "task-one"
            Assert-Equal -Expected "merged" -Actual $task.state -Because "${how}: the task did not pass"
            Assert-True -Condition ($task.reason -match "src/a/task-one\.txt") -Because "${how}: the reason names the file that left the area: $($task.reason)"
            Assert-Equal -Expected "work on task-one" -Actual (Invoke-SandboxGit -Root $root -Arguments @("show", "integrate/c1:src/a/task-one.txt")) -Because "${how}: the task's file is where the worker put it"
        }
    }

    Test-Case "a PASS line names nobody, inside a failing step or in the log of a gate that died: only the task on a failing line goes back" {
        $root = New-Sandbox -Work $two
        $run = Invoke-Integrate -Root $root -Gate "red" -Names "src/a/task-one.txt" -Environment @{ PAGENTOS_FAKE_GATE_PASSES = "src/b/task-two.txt" }
        Assert-Equal -Expected 6 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected "returned" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "the failing line names its file"
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "task-two").state -Because "task-two's file is only on PASS lines"
        $root = New-Sandbox -Work $two
        $died = Invoke-Integrate -Root $root -Gate "silent" -Environment @{ PAGENTOS_FAKE_GATE_PASSES = "src/a/task-one.txt" }
        Assert-Equal -Expected 6 -Actual $died.ExitCode -Because $died.Output
        foreach ($id in @("task-one", "task-two")) {
            $task = Get-TaskById -Queue $died.Queue -Id $id
            Assert-Equal -Expected "merged" -Actual $task.state -Because "a gate that died names nobody: $id stays"
            Assert-True -Condition ($task.reason -match "son sözünü") -Because "and says what happened: $($task.reason)"
        }
        Assert-True -Condition ($died.Report -match "geri verilen: yok") -Because "the report says nobody was returned: $($died.Report)"
    }

    Test-Case "the step never runs uncapped: its own defaults are caps, a cap of 0 is refused before anything, and a gate that hangs is killed at the cap with the lock released" {
        $tokens = $null; $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($integrateScript, [ref]$tokens, [ref]$errors)
        foreach ($name in @("GateMinutes", "LeadMinutes")) {
            $parameter = @($ast.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -eq $name })[0]
            Assert-True -Condition ($null -ne $parameter.DefaultValue -and [double]$parameter.DefaultValue.Extent.Text -gt 0) -Because "-$name has a default cap: the scheduled call need not remember it"
        }
        $root = New-Sandbox -Work $one
        $before = Get-SandboxState -Root $root
        foreach ($caps in @("-GateMinutes 0 -LeadMinutes 3", "-GateMinutes 3 -LeadMinutes 0", "-GateMinutes 300 -LeadMinutes 100")) {
            $refused = Invoke-Integrate -Root $root -Gate "green" -Caps $caps
            Assert-True -Condition ($refused.ExitCode -ne 0) -Because "'$caps' is refused: $($refused.Output)"
            Assert-True -Condition ($refused.Output -match "GateMinutes|LeadMinutes") -Because "'$caps': it says which: $($refused.Output)"
            Assert-Equal -Expected $before -Actual (Get-SandboxState -Root $root) -Because "'$caps': nothing changed"
            Assert-Equal -Expected 0 -Actual (@($refused.GateCalls).Count + @($refused.LeadCalls).Count) -Because "'$caps': nothing was started"
        }
        $defaults = Invoke-Integrate -Root $root -Gate "green" -Caps "" -ExtraArguments "-DryRun"
        Assert-Equal -Expected 0 -Actual $defaults.ExitCode -Because "with no cap given the defaults are taken: $($defaults.Output)"
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $hung = Invoke-Integrate -Root $root -Gate "hang" -Caps "-GateMinutes 0.05 -LeadMinutes 3"
        Assert-Equal -Expected 6 -Actual $hung.ExitCode -Because $hung.Output
        Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "main is where it was"
        $task = Get-TaskById -Queue $hung.Queue -Id "task-one"
        Assert-Equal -Expected "merged" -Actual $task.state -Because "a gate that was killed names nobody"
        Assert-True -Condition ($task.reason -match "süresinde bitmedi") -Because "the reason says the gate did not end in its time: $($task.reason)"
        Assert-Equal -Expected $false -Actual ([bool]$hung.Lock.held) -Because "the lock is not held by a gate that hung"
    }

    Test-Case "an attempt that breaks after its lead run is counted: the second stops the branch, and no third lead run is paid for" {
        $root = New-Sandbox -Work $one
        $missing = Join-Path "$root-tools" "no-such-gate.ps1"
        $first = Invoke-Integrate -Root $root -Gate "green" -GateScript $missing
        Assert-Equal -Expected 12 -Actual $first.ExitCode -Because $first.Output
        Assert-Equal -Expected "error" -Actual (Read-TeamJson -Path (Join-Path $root "team\reports\c1\gate-1.json")).result -Because "the attempt is recorded"
        $second = Invoke-Integrate -Root $root -Gate "green" -GateScript $missing
        Assert-Equal -Expected 8 -Actual $second.ExitCode -Because $second.Output
        Assert-True -Condition ($second.Report -match "TEAM_PROTOCOL 10") -Because "the stop is in the report: $($second.Report)"
        $third = Invoke-Integrate -Root $root -Gate "green" -GateScript $missing
        Assert-Equal -Expected 8 -Actual $third.ExitCode -Because $third.Output
        Assert-Equal -Expected 2 -Actual @($third.LeadCalls).Count -Because "two lead runs were paid for, not one every half hour"
        Assert-Equal -Expected $false -Actual ([bool]$third.Lock.held) -Because "the lock was released"
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

    Test-Case "main moving WHILE the gate runs: nothing is merged, the tasks stay merged, and the next run gates again - on a commit that holds the new main" {
        $root = New-Sandbox -Work $two
        $mainBefore = Get-Sha -Root $root -Revision "main"
        # What somebody else does during the gate's hour: a commit on main, in the main checkout.
        $hook = Join-Path "$root-tools" "move-main.ps1"
        $marker = Join-Path "$root-tools" "main-moved-once"
        [System.IO.File]::WriteAllText($hook, (@(
                    "`$ErrorActionPreference = 'Continue'",
                    "if (Test-Path -LiteralPath '$marker') { return }",
                    "Set-Content -LiteralPath '$marker' -Value 'done' -Encoding ASCII",
                    "Set-Content -LiteralPath '$(Join-Path $root 'docs\moved.txt')' -Value 'main moved while the gate ran' -Encoding ASCII",
                    "& git.exe -C '$root' add docs/moved.txt 2>&1 | Out-Null",
                    "& git.exe -C '$root' commit -q -m 'main moved while the gate ran' -- docs/moved.txt 2>&1 | Out-Null") -join "`r`n"), $utf8)
        $first = Invoke-Integrate -Root $root -Gate "green" -Environment @{ PAGENTOS_FAKE_GATE_HOOK = $hook }
        $moved = Get-Sha -Root $root -Revision "main"
        Assert-True -Condition ((Test-Path -LiteralPath $marker) -and $moved -ne $mainBefore) -Because "main moved while the gate ran (else this case proves nothing): $($first.Output)"
        Assert-Equal -Expected "main moved while the gate ran" -Actual (Invoke-SandboxGit -Root $root -Arguments @("log", "-1", "--format=%s", "main")) -Because "main is the other writer's commit and nothing else: no merge was put on it"
        Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main^1") -Because "one commit past where it was"
        Assert-Equal -Expected 11 -Actual $first.ExitCode -Because "the gate was green on a commit that does not hold the new main: $($first.Output)"
        Assert-Equal -Expected 1 -Actual @($first.GateCalls).Count -Because "the gate did run"
        $gatedFirst = @($first.GateCalls[0] -split "\|")[1]
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $root "src\a\task-one.txt"))) -Because "the tasks' files did not reach the main checkout"
        Assert-Equal -Expected $mainBefore -Actual (Invoke-SandboxGit -Root "$root-origin.git" -Arguments @("rev-parse", "main")) -Because "nothing was pushed"
        foreach ($id in @("task-one", "task-two")) {
            $task = Get-TaskById -Queue $first.Queue -Id $id
            Assert-Equal -Expected "merged" -Actual $task.state -Because "$id stays merged: it neither passed onto main nor failed"
            Assert-True -Condition ($task.reason -match "main ilerledi") -Because "and says why it waits: $($task.reason)"
        }
        Assert-Equal -Expected $false -Actual ([bool]$first.Lock.held) -Because "the lock was released"

        # The next run: the green of the first is NOT taken for this main. main is merged in, and the gate runs again.
        $second = Invoke-Integrate -Root $root -Gate "green" -Environment @{ PAGENTOS_FAKE_GATE_HOOK = $hook }
        Assert-Equal -Expected 0 -Actual $second.ExitCode -Because $second.Output
        Assert-Equal -Expected 2 -Actual @($second.GateCalls).Count -Because "a second gate: the first judged a commit without the new main"
        $gatedSecond = @($second.GateCalls[1] -split "\|")[1]
        Assert-True -Condition ($gatedSecond -ne $gatedFirst) -Because "on another commit"
        Assert-True -Condition ((Invoke-TeamGit -WorkingDirectory $root -Arguments @("merge-base", "--is-ancestor", $moved, $gatedSecond)).ExitCode -eq 0) -Because "one that holds the commit main moved to"
        Assert-Equal -Expected $moved -Actual (Get-Sha -Root $root -Revision "main^1") -Because "main went forward from where the other writer left it"
        Assert-Equal -Expected $gatedSecond -Actual (Get-Sha -Root $root -Revision "main^2") -Because "by a merge of exactly what the second gate ran on"
        Assert-Equal -Expected (Get-Sha -Root $root -Revision "$gatedSecond^{tree}") -Actual (Get-Sha -Root $root -Revision "main^{tree}") -Because "file for file"
        foreach ($id in @("task-one", "task-two")) { Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $second.Queue -Id $id).state -Because "$id is on main now" }
        Assert-True -Condition ((Test-Path -LiteralPath (Join-Path $root "docs\moved.txt")) -and (Test-Path -LiteralPath (Join-Path $root "src\a\task-one.txt"))) -Because "main holds both the other writer's commit and the tasks"
    }

    Test-Case "a gate worktree whose folder was deleted by hand is made again: the registration git still holds does not stop the branch" {
        $root = New-Sandbox -Work $one
        # A run that gets as far as the worktree and no further (the usage limit: nothing is counted).
        $first = Invoke-Integrate -Root $root -Lead "stand-in" -Environment @{ PAGENTOS_FAKE_LEAD_FAIL = "limit" }
        Assert-Equal -Expected 7 -Actual $first.ExitCode -Because $first.Output
        $gateTree = Join-Path $root ".claude\worktrees\gate\integrate\c1"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $gateTree ".git")) -Because "the gate worktree was made"
        # Somebody frees the disk: the folder goes, git's record of the worktree stays.
        Remove-Item -LiteralPath $gateTree -Recurse -Force
        Assert-True -Condition ((Invoke-SandboxGit -Root $root -Arguments @("worktree", "list", "--porcelain")) -match "gate/integrate/c1") -Because "git still has it registered (else this case proves nothing)"
        $other = Join-Path "$root-tools" "wt-other"
        [void](Invoke-SandboxGit -Root $root -Arguments @("worktree", "add", "-q", "--detach", $other, "main"))
        Remove-Item -LiteralPath $other -Recurse -Force

        $second = Invoke-Integrate -Root $root -Gate "green"
        Assert-Equal -Expected 0 -Actual $second.ExitCode -Because "the tree is made again and the branch goes on: $($second.Output)"
        Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $second.Queue -Id "task-one").state -Because "the task reached main"
        Assert-Equal -Expected 1 -Actual @($second.GateCalls).Count -Because "the gate ran"
        Assert-Equal -Expected $gateTree.ToLowerInvariant() -Actual (@($second.GateCalls[0] -split "\|")[0]).ToLowerInvariant() -Because "in the worktree that was made again"
        $listed = Invoke-SandboxGit -Root $root -Arguments @("worktree", "list", "--porcelain")
        Assert-Equal -Expected 1 -Actual @([regex]::Matches($listed, "(?m)^worktree .*gate/integrate/c1\s*$")).Count -Because "registered once: $listed"
        Assert-True -Condition ($listed -match "wt-other") -Because "another worktree's record, missing too, is not this step's to clear: $listed"
    }

    Test-Case "the merge for main is held to the gated commit's files: the same tree is the same, another file is not, and a commit that cannot be read is never 'the same'" {
        $root = New-Sandbox -Work $one
        $main = Get-Sha -Root $root -Revision "main"
        $tip = Get-Sha -Root $root -Revision "integrate/c1"
        Assert-True -Condition (-not (Test-TeamSameTree -RepoRoot $root -A $main -B $tip)) -Because "the branch holds a file main does not"
        Assert-True -Condition (Test-TeamSameTree -RepoRoot $root -A $tip -B $tip) -Because "a commit holds what it holds"
        # Another commit with exactly the branch's files (what a --no-ff merge of the branch onto its own ancestor is).
        $twin = Invoke-SandboxGit -Root $root -Arguments @("commit-tree", "$tip^{tree}", "-p", $main, "-m", "the same files, another commit")
        Assert-True -Condition ($twin -ne $tip -and (Test-TeamSameTree -RepoRoot $root -A $twin -B $tip)) -Because "two commits, one tree"
        Assert-True -Condition (-not (Test-TeamSameTree -RepoRoot $root -A ("0" * 40) -B ("0" * 40))) -Because "two commits that cannot be read are not known to be the same"
        Assert-True -Condition (-not (Test-TeamSameTree -RepoRoot $root -A $tip -B "no-such-revision")) -Because "nor is one"
    }

    Test-Case "what is left of a gate worktree without its .git (a folder half deleted) is not deleted by the step: it stops with the folder's name" {
        $root = New-Sandbox -Work $one
        $gateTree = Reset-TeamGateWorktree -RepoRoot $root -Branch "integrate/c1" -At (Get-Sha -Root $root -Revision "integrate/c1")
        Remove-Item -LiteralPath (Join-Path $gateTree ".git") -Force
        Set-Content -LiteralPath (Join-Path $gateTree "left-behind.txt") -Value "a file that was open" -Encoding ASCII
        $said = ""
        try { [void](Reset-TeamGateWorktree -RepoRoot $root -Branch "integrate/c1" -At (Get-Sha -Root $root -Revision "integrate/c1")) } catch { $said = [string]$_.Exception.Message }
        Assert-True -Condition ($said -match "gate\\integrate\\c1" -and $said -match "delete") -Because "the error names the folder and what to do with it: $said"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $gateTree "left-behind.txt")) -Because "nothing was deleted"
        # An EMPTY folder that is left is nothing to keep: the tree is made in it.
        Remove-Item -Path (Join-Path $gateTree "*") -Recurse -Force
        $again = Reset-TeamGateWorktree -RepoRoot $root -Branch "integrate/c1" -At (Get-Sha -Root $root -Revision "integrate/c1")
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $again ".git")) -Because "an empty folder is taken"
        Assert-Equal -Expected (Get-Sha -Root $root -Revision "integrate/c1") -Actual (Invoke-SandboxGit -Root $again -Arguments @("rev-parse", "HEAD")) -Because "at the commit"
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
        param([object[]]$Tasks = @(), $Lock = $null, $Models = $null, $Faults = $null)
        $work = Join-Path $env:TEMP ("pagentos-integapi-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
        [void](New-Item -ItemType Directory -Force -Path $work)
        [void]$sandboxes.Add($work)
        $lockDocument = if ($null -ne $Lock) { $Lock } else { New-TeamLockReleased }
        $seed = [pscustomobject]@{ queue = (New-Queue -Tasks $Tasks); lock = $lockDocument }
        # The store broken on purpose (the listener's `faults`): a task's write refused, and so on.
        if ($null -ne $Faults) { $seed | Add-Member -NotePropertyName faults -NotePropertyValue $Faults }
        # The model setting the store holds (ADR-0214 addendum 7); without one the listener answers the defaults.
        if ($null -ne $Models) { $seed | Add-Member -NotePropertyName models -NotePropertyValue $Models }
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

    Test-Case "in API mode the lead's model is read where the cycle reads it: the team store's setting, before the local file" {
        $root = New-Sandbox -Work $one
        $tasks = @(Get-TeamTasks -Queue (Read-TeamJson -Path (Join-Path $root "team\queue.json")))
        $stored = Get-TeamModelDefaults
        $stored.roles.lead = "claude-sonnet-5-5"
        $api = Start-FakeApi -Tasks $tasks -Models $stored
        Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document (New-Queue)
        # The file says another model: the store's setting is the one the Ofis page writes, and it wins.
        Set-SandboxModels -Root $root -Lead "claude-opus-5-5"
        $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected "claude-sonnet-5-5" -Actual (@($run.LeadCalls | ForEach-Object { [string]$_.model }) -join ",") -Because "the store's setting names the lead's model"
        Assert-Equal -Expected 1 -Actual @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match "^GET /v1/team/queue/models 200" }).Count -Because "it was asked once"
    }

    Test-Case "a red gate whose verdict could not be written to the store (the store refused the write) is not a silent wait: the next run writes it, without a second gate" {
        $root = New-Sandbox -Work $two
        $tasks = @(Get-TeamTasks -Queue (Read-TeamJson -Path (Join-Path $root "team\queue.json")))
        # The store refuses the first write of task-one's verdict. (A task that was only CHANGED while the gate ran no
        # longer stops the write: the step reads it again and writes its result on top - integrate-own-lock.)
        $api = Start-FakeApi -Tasks $tasks -Faults ([pscustomobject]@{ task_put = [pscustomobject]@{ id = "task-one"; state = "returned"; status = 503; times = 1 } })
        Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document (New-Queue)
        # What somebody else does during the gate's hour: task-one is written in the store.
        $hook = Join-Path "$root-tools" "change-task-one.ps1"
        $marker = Join-Path "$root-tools" "changed-once"
        [System.IO.File]::WriteAllText($hook, (@(
                    "if (Test-Path -LiteralPath '$marker') { return }",
                    ". '$(Join-Path $repoRoot 'scripts\lib\HttpJson.ps1')'", ". '$(Join-Path $repoRoot 'scripts\lib\TeamQueue.ps1')'",
                    "`$store = New-TeamApiStore -Url '$($api.Url)' -TokenFile '$($api.TokenFile)'",
                    "`$queue = Read-TeamQueueApi -Store `$store",
                    "`$task = @(Get-TeamTasks -Queue `$queue | Where-Object { `$_.id -eq 'task-one' })[0]",
                    "`$task.title = 'changed while the gate ran'; `$task.updated_at = '2026-10-02T09:00:00Z'",
                    "Save-TeamQueueApi -Store `$store -Queue `$queue",
                    "Set-Content -LiteralPath '$marker' -Value 'done' -Encoding ASCII") -join "`r`n"), $utf8)
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $first = Invoke-Integrate -Root $root -Gate "red" -Names "src/a/task-one.txt" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile -Environment @{ PAGENTOS_FAKE_GATE_HOOK = $hook }
        Assert-True -Condition (Test-Path -LiteralPath $marker) -Because "the task was changed while the gate ran (else this case proves nothing): $($first.Output)"
        Assert-Equal -Expected 12 -Actual $first.ExitCode -Because $first.Output
        $state = Get-FakeApiState -Api $api
        $stored = @($state.tasks | Where-Object { $_.id -eq "task-one" })[0]
        Assert-Equal -Expected "merged" -Actual $stored.state -Because "the write was refused: the store does not have the verdict yet"
        Assert-Equal -Expected "changed while the gate ran" -Actual $stored.title -Because "and the other writer's change was not overwritten"
        Assert-True -Condition ($first.Report -match "kuyruk yazılamadı") -Because "the report says the queue was not written: $($first.Report)"
        Assert-Equal -Expected $false -Actual ([bool]$state.lock.held) -Because "the lock was released"

        # The next run: a gate that WOULD be green is not started; the verdict the gate gave is written.
        # The first run gated twice: the whole branch, then the branch rebuilt without task-one (red too, the fake is red).
        Assert-Equal -Expected 2 -Actual @($first.GateCalls).Count -Because "the whole branch and the rebuilt one"
        $second = Invoke-Integrate -Root $root -Gate "green" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 6 -Actual $second.ExitCode -Because "the red gate is said again, not waited on in silence: $($second.Output)"
        Assert-Equal -Expected 2 -Actual @($second.GateCalls).Count -Because "the gate was not run a second time on the same commit"
        Assert-Equal -Expected 1 -Actual @($second.LeadCalls).Count -Because "nor a second lead run paid for"
        $state = Get-FakeApiState -Api $api
        $named = @($state.tasks | Where-Object { $_.id -eq "task-one" })[0]
        $other = @($state.tasks | Where-Object { $_.id -eq "task-two" })[0]
        Assert-Equal -Expected "returned" -Actual $named.state -Because "the gate named its file: it goes back to the worker"
        Assert-Equal -Expected "changed while the gate ran" -Actual $named.title -Because "on top of what the other writer wrote"
        Assert-Equal -Expected "merged" -Actual $other.state -Because "the gate did not name task-two"
        foreach ($task in @($named, $other)) {
            Assert-True -Condition ($task.reason -match "API unit tests" -and $task.reason -match "test_the_change_holds" -and $task.reason -match "team/reports/c1/gate-1\.log") -Because "the gate's words are in the reason of $($task.id): $($task.reason)"
        }
        Assert-True -Condition ($other.reason -match "task-one") -Because "the task that stays says whom it waits for: $($other.reason)"
        Assert-Equal -Expected $true -Actual ([bool](Read-TeamJson -Path (Join-Path $root "team\reports\c1\gate-1.json")).applied) -Because "the attempt's record says the queue has the verdict now"
        Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "main is where it was"
        Assert-Equal -Expected $false -Actual ([bool]$state.lock.held) -Because "the lock was released"

        $third = Invoke-Integrate -Root $root -Gate "green" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $third.ExitCode -Because $third.Output
        Assert-True -Condition ($third.Output -match "held by task-one \(returned\)") -Because "from here the branch waits for the worker, and says so: $($third.Output)"
        Assert-Equal -Expected 2 -Actual @($third.GateCalls).Count -Because "still the first run's two gates"
    }

    Test-Case "the other machine's lock stops the step before anything: no gate, no worktree, no write" {
        $root = New-Sandbox -Work $one
        $tasks = @(Get-TeamTasks -Queue (Read-TeamJson -Path (Join-Path $root "team\queue.json")))
        $held = New-TeamLock -Machine "GMKADIRAKBABA" -CycleId "office" -Now ([datetime]::UtcNow.AddHours(-1))
        $api = Start-FakeApi -Tasks $tasks -Lock $held
        # The branch's report as its last red gate left it: in the store (the Onay Merkezi shows it) and beside the queue.
        $lastReport = "# Entegrasyon raporu`n`nkapı kırmızı: API unit tests"
        Send-TeamReportApi -Store (New-TeamApiStore -Url $api.Url -TokenFile $api.TokenFile) -Name "c1-integrate.md" -Text $lastReport
        [void](New-Item -ItemType Directory -Force -Path (Join-Path $root "team\reports"))
        [System.IO.File]::WriteAllText((Join-Path $root "team\reports\c1-integrate.md"), $lastReport, $utf8)
        $requestsBefore = @(Get-FakeApiRequests -Api $api).Count
        $before = Get-SandboxState -Root $root
        $run = Invoke-Integrate -Root $root -Gate "green" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 3 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected $before -Actual (Get-SandboxState -Root $root) -Because "no ref, worktree, queue or lock changed"
        Assert-Equal -Expected 0 -Actual (@($run.GateCalls).Count + @($run.LeadCalls).Count) -Because "nothing was started"
        $state = Get-FakeApiState -Api $api
        Assert-Equal -Expected "GMKADIRAKBABA" -Actual $state.lock.machine -Because "the lock is still theirs"
        Assert-Equal -Expected "merged" -Actual @($state.tasks)[0].state -Because "the queue was not written"
        $requests = @(Get-FakeApiRequests -Api $api | Select-Object -Skip $requestsBefore)
        Assert-True -Condition (@($requests | Where-Object { $_ -match "^GET " }).Count -ge 1) -Because "the step did ask the store (else this case proves nothing): $($requests -join '; ')"
        Assert-Equal -Expected 0 -Actual @($requests | Where-Object { $_ -notmatch "^GET " }).Count -Because "no write at all - the lock, a task, a report: $($requests -join '; ')"
        Assert-Equal -Expected $lastReport -Actual ([string]$state.reports.PSObject.Properties["c1-integrate.md"].Value) -Because "the store's report still says what the last run that DID something found"
        Assert-Equal -Expected $lastReport -Actual $run.Report -Because "and so does the file"
        Assert-Equal -Expected 1 -Actual @($run.Skipped).Count -Because "the stop is one line of its own: $($run.Skipped -join ' / ')"
        Assert-True -Condition ($run.Skipped[0] -match "kilit GMKADIRAKBABA makinesinde" -and $run.Skipped[0].Contains("integrate/c1")) -Because $run.Skipped[0]
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
        Assert-Equal -Expected "" -Actual $run.Report -Because "a run that started nothing writes no report for the branch"
        Assert-True -Condition (@($run.Skipped).Count -eq 1 -and $run.Skipped[0] -match "kilit GMKADIRAKBABA makinesinde") -Because "it says so in its own line: $($run.Skipped -join ' / ')"
    }

    # ------------------------------------------------------------------ the step's own lock (integrate-own-lock)
    Write-Host ""
    Write-Host "the step's own lock: the cycle is not paused for a gate; two gates never overlap; a result the cycle overtook is dropped"

    function New-ApiSandbox {
        <# A sandbox whose queue lives in the fake store (the files hold none), and the store; -Lock is the CYCLE's lock there. #>
        param([hashtable[]]$Work, $Lock = $null)
        $root = New-Sandbox -Work $Work
        $tasks = @(Get-TeamTasks -Queue (Read-TeamJson -Path (Join-Path $root "team\queue.json")))
        $api = Start-FakeApi -Tasks $tasks -Lock $Lock
        Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document (New-Queue)
        return [pscustomobject]@{ Root = $root; Api = $api }
    }

    function Get-StoredTask { param($Api, [string]$Id) return @((Get-FakeApiState -Api $Api).tasks | Where-Object { $_.id -eq $Id })[0] }

    # A cycle of THIS machine that is running now: its lock names this machine and a live process (this suite's own).
    $liveCycleLock = { New-TeamLock -Machine "MAIL" -CycleId "d20261003" -Now ([datetime]::UtcNow.AddMinutes(-10)) }

    Test-Case "own lock (1): with -BesideCycle the step runs beside a live cycle of this machine and never touches the cycle's lock - the store's lock is equal before and after, no POST to the lock route, and the branch ends on main" {
        $box = New-ApiSandbox -Work $one -Lock (& $liveCycleLock)
        $lockBefore = ConvertTo-Json -InputObject (Get-FakeApiState -Api $box.Api).lock -Compress
        $run = Invoke-Integrate -Root $box.Root -Gate "green" -QueueUrl $box.Api.Url -QueueTokenFile $box.Api.TokenFile -ExtraArguments "-BesideCycle"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected $lockBefore -Actual (ConvertTo-Json -InputObject (Get-FakeApiState -Api $box.Api).lock -Compress) -Because "the cycle's lock is the cycle's: not taken, not released, not written"
        $posts = @(Get-FakeApiRequests -Api $box.Api | Where-Object { $_ -match "^POST /v1/team/queue/lock" })
        Assert-Equal -Expected 0 -Actual @($posts).Count -Because "no POST to the lock route: $($posts -join '; ')"
        $task = Get-StoredTask -Api $box.Api -Id "task-one"
        Assert-Equal -Expected "awaiting_release" -Actual $task.state -Because "the branch went through the gate as in the lock-free case"
        Assert-Equal -Expected (Get-Sha -Root $box.Root -Revision "main") -Actual $task.sha -Because "with main's sha"
        Assert-True -Condition (Test-OnBranch -Root $box.Root -Revision "main" -File "src/a/task-one.txt") -Because "the task's file is on main"
        Assert-Equal -Expected 1 -Actual @($run.GateCalls).Count -Because "one gate"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Get-StepLockPath -Root $box.Root))) -Because "the step's own lock is gone after it"
    }

    Test-Case "own lock (2): without -BesideCycle a live cycle of this machine still stops the step: exit 3, the skipped line, nothing written" {
        $box = New-ApiSandbox -Work $one -Lock (& $liveCycleLock)
        $lockBefore = ConvertTo-Json -InputObject (Get-FakeApiState -Api $box.Api).lock -Compress
        $requestsBefore = @(Get-FakeApiRequests -Api $box.Api).Count
        $run = Invoke-Integrate -Root $box.Root -Gate "green" -QueueUrl $box.Api.Url -QueueTokenFile $box.Api.TokenFile
        Assert-Equal -Expected 3 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected 0 -Actual (@($run.GateCalls).Count + @($run.LeadCalls).Count) -Because "nothing was started"
        $requests = @(Get-FakeApiRequests -Api $box.Api | Select-Object -Skip $requestsBefore)
        Assert-Equal -Expected 0 -Actual @($requests | Where-Object { $_ -notmatch "^GET " }).Count -Because "no write at all: $($requests -join '; ')"
        Assert-Equal -Expected $lockBefore -Actual (ConvertTo-Json -InputObject (Get-FakeApiState -Api $box.Api).lock -Compress) -Because "the cycle's lock is untouched"
        Assert-True -Condition (@($run.Skipped).Count -eq 1 -and $run.Skipped[0] -match "kilit MAIL makinesinde") -Because "the skipped line: $($run.Skipped -join ' / ')"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Get-StepLockPath -Root $box.Root))) -Because "the step's own lock is not left behind"
    }

    Test-Case "own lock (3): two steps at once - exactly one gate runs, the second says the lock is held and exits 3, and the first finishes" {
        $box = New-ApiSandbox -Work $one
        $tools = "$($box.Root)-tools"
        $holding = Join-Path $tools "gate-holding"
        $release = Join-Path $tools "gate-release"
        $hook = Join-Path $tools "hold-gate.ps1"
        [System.IO.File]::WriteAllText($hook, (@(
                    "Set-Content -LiteralPath '$holding' -Value 'the gate runs' -Encoding ASCII",
                    "`$until = [datetime]::UtcNow.AddSeconds(150)",
                    "while (-not (Test-Path -LiteralPath '$release') -and [datetime]::UtcNow -lt `$until) { Start-Sleep -Milliseconds 100 }") -join "`r`n"), $utf8)
        $first = Invoke-Integrate -Root $box.Root -Gate "green" -QueueUrl $box.Api.Url -QueueTokenFile $box.Api.TokenFile -ExtraArguments "-BesideCycle" -Environment @{ PAGENTOS_FAKE_GATE_HOOK = $hook } -NoWait
        try {
            $until = [datetime]::UtcNow.AddSeconds(180)
            while (-not (Test-Path -LiteralPath $holding)) {
                if ($first.Process.HasExited) { throw "the first step ended before its gate held: $(Get-Content -LiteralPath $first.OutFile -Raw -ErrorAction SilentlyContinue)" }
                if ([datetime]::UtcNow -gt $until) { throw "the first step's gate did not start within 180 s" }
                Start-Sleep -Milliseconds 200
            }
            Assert-True -Condition (Test-Path -LiteralPath (Get-StepLockPath -Root $box.Root)) -Because "the first step holds its own lock while its gate runs"
            $second = Invoke-Integrate -Root $box.Root -Gate "green" -QueueUrl $box.Api.Url -QueueTokenFile $box.Api.TokenFile -ExtraArguments "-BesideCycle"
            Assert-Equal -Expected 3 -Actual $second.ExitCode -Because $second.Output
            Assert-True -Condition (@($second.Skipped).Count -eq 1 -and $second.Skipped[0] -match "entegrasyon adımı" -and $second.Skipped[0] -match "pid $($first.Process.Id)") -Because "the second says whose the lock is: $($second.Skipped -join ' / ')"
        }
        finally { Set-Content -LiteralPath $release -Value "go" -Encoding ASCII }
        $done = Wait-IntegrateProcess -Started $first -Seconds 300
        Assert-Equal -Expected 0 -Actual $done.ExitCode -Because $done.Output
        Assert-Equal -Expected 1 -Actual @(Get-Content -LiteralPath (Join-Path $tools "gate.log") | Where-Object { $_.Trim() }).Count -Because "exactly one gate ran"
        Assert-Equal -Expected "awaiting_release" -Actual (Get-StoredTask -Api $box.Api -Id "task-one").state -Because "the first step finished the branch"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Get-StepLockPath -Root $box.Root))) -Because "and its lock is gone"
    }

    Test-Case "own lock (3): a lock file left by a dead step is taken over, and the report says so" {
        $box = New-ApiSandbox -Work $one
        $dead = Start-Process -FilePath (Join-Path $env:SystemRoot "System32\cmd.exe") -ArgumentList "/c exit 0" -PassThru -WindowStyle Hidden
        if (-not $dead.WaitForExit(30000)) { try { $dead.Kill() } catch { }; throw "the short process did not end" }
        $deadPid = $dead.Id
        $left = [pscustomobject]@{ pid = $deadPid; process_started_at = "2026-10-03T01:02:03.0000000Z"; machine = "MAIL"; taken_at = "2026-10-03T01:02:04Z" }
        [System.IO.File]::WriteAllText((Get-StepLockPath -Root $box.Root), (ConvertTo-Json -InputObject $left -Compress), $utf8)
        $run = Invoke-Integrate -Root $box.Root -Gate "green" -QueueUrl $box.Api.Url -QueueTokenFile $box.Api.TokenFile -ExtraArguments "-BesideCycle"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-True -Condition ($run.Report -match "devralındı" -and $run.Report -match "pid $deadPid") -Because "the takeover is said, with the dead pid: $($run.Report)"
        Assert-Equal -Expected "awaiting_release" -Actual (Get-StoredTask -Api $box.Api -Id "task-one").state -Because "the step ran"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Get-StepLockPath -Root $box.Root))) -Because "and its lock is gone"
    }

    Test-Case "own lock (3): the step's lock is held while it works and gone after each of six ends - green, red gate, refused wiring, moved ref, every model limited, a thrown error" {
        $all = "claude-fable-5-1,claude-opus-5-5,claude-sonnet-5-5"
        $ends = @(
            @{ Name = "green"; Code = 0; Gate = "green"; Writes = ""; Environment = @{}; GateScript = "" },
            @{ Name = "red gate"; Code = 6; Gate = "red"; Writes = ""; Environment = @{}; GateScript = "" },
            @{ Name = "refused wiring"; Code = 7; Gate = "green"; Writes = "src/a/extra.txt"; Environment = @{}; GateScript = "" },
            @{ Name = "moved ref"; Code = 13; Gate = "green"; Writes = ""; Environment = @{ PAGENTOS_FAKE_LEAD_GIT_AFTER = "update-ref refs/heads/integrate/c1 refs/heads/main" }; GateScript = "" },
            @{ Name = "every model limited"; Code = 7; Gate = "green"; Writes = ""; Environment = @{ PAGENTOS_FAKE_LEAD_LIMITED_MODELS = $all }; GateScript = "" },
            @{ Name = "a thrown error"; Code = 12; Gate = "green"; Writes = ""; Environment = @{}; GateScript = "missing" })
        foreach ($end in $ends) {
            $how = $end.Name
            $box = New-ApiSandbox -Work $one
            $lockPath = Get-StepLockPath -Root $box.Root
            $copy = Join-Path "$($box.Root)-tools" "step-lock-during.json"
            $environment = @{ PAGENTOS_FAKE_LEAD_LOCK = $lockPath; PAGENTOS_FAKE_LEAD_LOCK_COPY = $copy }
            foreach ($name in @($end.Environment.Keys)) { $environment[$name] = $end.Environment[$name] }
            $gateScript = if ($end.GateScript) { Join-Path "$($box.Root)-tools" "no-such-gate.ps1" } else { "" }
            $run = Invoke-Integrate -Root $box.Root -Gate $end.Gate -Names "src/a/task-one.txt" -Lead "stand-in" -LeadWrites $end.Writes -Environment $environment -GateScript $gateScript `
                -QueueUrl $box.Api.Url -QueueTokenFile $box.Api.TokenFile -ExtraArguments "-BesideCycle"
            Assert-Equal -Expected $end.Code -Actual $run.ExitCode -Because "${how}: $($run.Output)"
            Assert-True -Condition (Test-Path -LiteralPath $copy) -Because "${how}: the lock file was there while the lead's run worked"
            $during = ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText($copy))
            Assert-True -Condition ([int]$during.pid -gt 0 -and [int]$during.pid -ne $PID) -Because "${how}: it named the step's process: $([System.IO.File]::ReadAllText($copy))"
            Assert-True -Condition (-not (Test-Path -LiteralPath $lockPath)) -Because "${how}: the step's lock is gone after it"
        }
    }

    Test-Case "own lock (4): a task the cycle moved while the gate ran is not written - returned by a new inspection, or merged again with another sha; the report names it, the branch's other tasks are written" {
        $three = @(@{ Id = "task-one"; Area = "src/a" }, @{ Id = "task-two"; Area = "src/b" }, @{ Id = "task-three"; Area = "src/c" })
        $box = New-ApiSandbox -Work $three
        $other = "f" * 40
        $hook = Join-Path "$($box.Root)-tools" "cycle-moves.ps1"
        $marker = Join-Path "$($box.Root)-tools" "moved-once"
        [System.IO.File]::WriteAllText($hook, (@(
                    "if (Test-Path -LiteralPath '$marker') { return }",
                    ". '$(Join-Path $repoRoot 'scripts\lib\HttpJson.ps1')'", ". '$(Join-Path $repoRoot 'scripts\lib\TeamQueue.ps1')'",
                    "`$store = New-TeamApiStore -Url '$($box.Api.Url)' -TokenFile '$($box.Api.TokenFile)'",
                    "`$queue = Read-TeamQueueApi -Store `$store",
                    "foreach (`$task in @(Get-TeamTasks -Queue `$queue)) {",
                    "    if (`$task.id -eq 'task-one') { `$task.state = 'returned'; Set-TeamProperty -InputObject `$task -Name 'reason' -Value 'a new inspection returned it'; `$task.updated_at = '2026-10-03T09:00:00Z' }",
                    "    if (`$task.id -eq 'task-two') { `$task.sha = '$other'; `$task.updated_at = '2026-10-03T09:00:01Z' }",
                    "    if (`$task.id -eq 'task-three') { `$task.title = 'renamed while the gate ran'; `$task.updated_at = '2026-10-03T09:00:02Z' }",
                    "}",
                    "Save-TeamQueueApi -Store `$store -Queue `$queue",
                    "Set-Content -LiteralPath '$marker' -Value 'done' -Encoding ASCII") -join "`r`n"), $utf8)
        $run = Invoke-Integrate -Root $box.Root -Gate "green" -QueueUrl $box.Api.Url -QueueTokenFile $box.Api.TokenFile -ExtraArguments "-BesideCycle" -Environment @{ PAGENTOS_FAKE_GATE_HOOK = $hook }
        Assert-True -Condition (Test-Path -LiteralPath $marker) -Because "the cycle did move the tasks while the gate ran (else this case proves nothing): $($run.Output)"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        $returned = Get-StoredTask -Api $box.Api -Id "task-one"
        Assert-Equal -Expected "returned" -Actual $returned.state -Because "the new inspection's word stands: not written as awaiting_release"
        Assert-Equal -Expected "a new inspection returned it" -Actual $returned.reason -Because "and its reason is the inspection's"
        $again = Get-StoredTask -Api $box.Api -Id "task-two"
        Assert-Equal -Expected "merged" -Actual $again.state -Because "a task merged again with another sha was not gated as it is now"
        Assert-Equal -Expected $other -Actual $again.sha -Because "its sha is the cycle's"
        $written = Get-StoredTask -Api $box.Api -Id "task-three"
        Assert-Equal -Expected "awaiting_release" -Actual $written.state -Because "the branch's other task is written"
        Assert-Equal -Expected (Get-Sha -Root $box.Root -Revision "main") -Actual $written.sha -Because "with main's sha"
        Assert-Equal -Expected "renamed while the gate ran" -Actual $written.title -Because "on top of what the other writer changed, which stays"
        foreach ($id in @("task-one", "task-two")) {
            Assert-True -Condition ($run.Report -match "(?m)^.*kapı koşarken.*\b$id\b.*$") -Because "the report names $id on a line that says why it was not written: $($run.Report)"
        }
        Assert-True -Condition ($run.Report -notmatch "(?m)^.*kapı koşarken.*\btask-three\b.*$") -Because "and not the task that was written: $($run.Report)"
    }

    Write-Host ""
    Write-Host "the wiring run may grow the gate, never shrink it (integrate-own-lock)"

    $sandboxGate = @'
function Invoke-Step { param([string]$Name, [scriptblock]$Body) & $Body }
function Assert-ExitCode { param([string]$What) if ($LASTEXITCODE -ne 0) { throw "$What exited with code $LASTEXITCODE" } }

# the first step
Invoke-Step "First" {
    & cmd.exe /c exit 0
    Assert-ExitCode "first"
}

Invoke-Step "Second" {
    & cmd.exe /c exit 0
    Assert-ExitCode "second"
}

Write-Host "QUALITY GATE: PASS"
exit 0
'@ -replace "`r`n", "`n"

    Test-Case "own lock (6): a wiring that removes a step's Invoke-Step line, an Assert-ExitCode line or the PASS line is refused and quoted; one that adds a step or edits a comment is accepted" {
        $shapes = @(
            # Only the Invoke-Step line goes (with its brace): the body and its Assert-ExitCode stay, unwrapped.
            @{ Name = "a step's Invoke-Step line removed"; Old = "Invoke-Step `"Second`" {`n    & cmd.exe /c exit 0`n    Assert-ExitCode `"second`"`n}`n"; New = "& cmd.exe /c exit 0`nAssert-ExitCode `"second`"`n"; Refused = $true; Quote = 'Invoke-Step "Second" {' },
            @{ Name = "an Assert-ExitCode line removed"; Old = "    Assert-ExitCode `"first`"`n"; New = ""; Refused = $true; Quote = 'Assert-ExitCode "first"' },
            @{ Name = "the PASS line removed"; Old = "Write-Host `"QUALITY GATE: PASS`"`n"; New = ""; Refused = $true; Quote = 'QUALITY GATE: PASS' },
            @{ Name = "a step added"; Old = "Write-Host `"QUALITY GATE: PASS`""; New = "Invoke-Step `"Third`" {`n    & cmd.exe /c exit 0`n    Assert-ExitCode `"third`"`n}`n`nWrite-Host `"QUALITY GATE: PASS`""; Refused = $false; Quote = "" },
            @{ Name = "a comment and a body edited"; Old = "# the first step`nInvoke-Step `"First`" {`n    & cmd.exe /c exit 0`n"; New = "# the first step: it proves the sandbox`nInvoke-Step `"First`" {`n    & cmd.exe /c exit 0 # quiet`n"; Refused = $false; Quote = "" })
        foreach ($shape in $shapes) {
            $how = $shape.Name
            $root = New-Sandbox -Work $one
            $tools = "$root-tools"
            [System.IO.File]::WriteAllText((Join-Path $root "scripts\quality-gate.ps1"), $sandboxGate, $utf8)
            [void](Invoke-SandboxGit -Root $root -Arguments @("add", "scripts/quality-gate.ps1"))
            [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-m", "the sandbox's gate"))
            $mainBefore = Get-Sha -Root $root -Revision "main"
            $tipBefore = Get-Sha -Root $root -Revision "integrate/c1"
            [System.IO.File]::WriteAllText((Join-Path $tools "edit-old.txt"), $shape.Old, $utf8)
            [System.IO.File]::WriteAllText((Join-Path $tools "edit-new.txt"), $shape.New, $utf8)
            $edit = Join-Path $tools "edit-gate.ps1"
            [System.IO.File]::WriteAllText($edit, (@(
                        "`$utf8 = New-Object System.Text.UTF8Encoding(`$false)",
                        "`$file = Join-Path (Get-Location).ProviderPath 'scripts\quality-gate.ps1'",
                        "`$text = [System.IO.File]::ReadAllText(`$file, `$utf8)",
                        "`$old = [System.IO.File]::ReadAllText('$(Join-Path $tools "edit-old.txt")', `$utf8)",
                        "`$new = [System.IO.File]::ReadAllText('$(Join-Path $tools "edit-new.txt")', `$utf8)",
                        "if (-not `$text.Contains(`$old)) { throw 'the edit does not apply' }",
                        "[System.IO.File]::WriteAllText(`$file, `$text.Replace(`$old, `$new), `$utf8)") -join "`r`n"), $utf8)
            $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -Environment @{ PAGENTOS_FAKE_LEAD_EDIT = $edit }
            $gateTree = Join-Path $root ".claude\worktrees\gate\integrate\c1"
            if ($shape.Refused) {
                Assert-Equal -Expected 7 -Actual $run.ExitCode -Because "${how}: refused - $($run.Output)"
                Assert-Equal -Expected 0 -Actual @($run.GateCalls).Count -Because "${how}: the gate did not run"
                Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "${how}: nothing reached main"
                Assert-Equal -Expected $tipBefore -Actual (Get-Sha -Root $root -Revision "integrate/c1") -Because "${how}: nor the integration branch"
                Assert-Equal -Expected "lead_refused" -Actual (Read-TeamJson -Path (Join-Path $root "team\reports\c1\gate-1.json")).result -Because "${how}: counted as a refused lead run"
                Assert-True -Condition ($run.Report.Contains($shape.Quote) -and $run.Report -match "quality-gate\.ps1") -Because "${how}: the report quotes the line: $($run.Report)"
                Assert-Equal -Expected "" -Actual (Invoke-SandboxGit -Root $gateTree -Arguments @("status", "--porcelain")) -Because "${how}: the tree was reset"
                Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "${how}: the task waits"
            }
            else {
                Assert-Equal -Expected 0 -Actual $run.ExitCode -Because "${how}: accepted - $($run.Output)"
                $onMain = [System.IO.File]::ReadAllText((Join-Path $root "scripts\quality-gate.ps1"), $utf8)
                Assert-True -Condition ($onMain.Contains($shape.New)) -Because "${how}: the wiring is on main: $onMain"
                Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "${how}: and the task with it"
            }
        }
    }

    Test-Case "own lock (7): a limited run that WROTE before it answered - inside the area and under docs/ - leaves nothing: the retry starts on a clean tree and its commit holds neither file" {
        $root = New-Sandbox -Work $one
        Set-SandboxModels -Root $root -Lead "claude-fable-5-1"
        $status = Join-Path "$root-tools" "lead-status.log"
        $run = Invoke-Integrate -Root $root -Gate "green" -Lead "stand-in" -LeadWrites "docs/DECISIONS.md" -Environment @{
            PAGENTOS_FAKE_LEAD_LIMITED_MODELS = "claude-fable-5-1"; PAGENTOS_FAKE_LEAD_LIMITED_WRITES = "src/a/limited.txt;docs/limited.md"; PAGENTOS_FAKE_LEAD_STATUS = $status
        }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        $found = @(Get-Content -LiteralPath $status -Encoding UTF8 | Where-Object { $_.Trim() })
        Assert-Equal -Expected "claude-fable-5-1|,claude-opus-5-5|" -Actual ($found -join ",") -Because "each run started on a clean tree - the retry too, after the limited run wrote two files"
        $wiring = Invoke-SandboxGit -Root $root -Arguments @("log", "-1", "--format=%H", "--grep=the lead's merge wiring", "integrate/c1")
        Assert-True -Condition ($wiring -cmatch "^[0-9a-f]{40}$") -Because "the retry's wiring was committed: $wiring"
        Assert-Equal -Expected "docs/DECISIONS.md" -Actual ((Invoke-SandboxGit -Root $root -Arguments @("diff", "--name-only", "--no-renames", "$wiring^", $wiring)) -replace "\s+", ",") -Because "the retry's commit holds what the retry wrote and nothing the limited run wrote"
        foreach ($file in @("src/a/limited.txt", "docs/limited.md")) {
            Assert-True -Condition (-not (Test-OnBranch -Root $root -Revision "main" -File $file)) -Because "$file is not on main"
            Assert-True -Condition (-not (Test-OnBranch -Root $root -Revision "integrate/c1" -File $file)) -Because "$file is not on the integration branch"
        }
        Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "the branch went through"
    }

    # ---- a red card does not hold the green ones (the owner, 2026-10-06: "testler yeşil olanları otomatik yayına alsak")
    $three = @(@{ Id = "task-a"; Area = "src/a" }, @{ Id = "task-b"; Area = "src/b" }, @{ Id = "task-c"; Area = "src/c" })

    function New-RedWhileGate {
        <# A gate of the test's: red (naming -File) while -File is in the tree it runs in, green once it is not. #>
        param([string]$Root, [string]$File)
        $path = Join-Path "$Root-tools" "red-while-gate.ps1"
        [System.IO.File]::WriteAllText($path, (@(
                    "`$env:PAGENTOS_FAKE_GATE_SCENARIO = if (Test-Path -LiteralPath (Join-Path (Get-Location).ProviderPath '$($File -replace '/', '\')')) { 'red' } else { 'green' }",
                    "`$env:PAGENTOS_FAKE_GATE_NAMES = '$File'",
                    "& '$(Join-Path $Root 'scripts\tests\lib\fake-gate.ps1')'",
                    "exit `$LASTEXITCODE") -join "`r`n"), $utf8)
        return $path
    }

    function Get-RebuiltBranches {
        param([string]$Root)
        return @((Invoke-SandboxGit -Root $Root -Arguments @("for-each-ref", "--format=%(refname:short)", "refs/heads/integrate/")) -split "`r?`n" |
                ForEach-Object { $_.Trim() } | Where-Object { $_ -and $_ -ne "integrate/c1" })
    }

    Test-Case "red card apart (1): the gate blames task-a; the branch is rebuilt from main without it, gated again in the same run, and task-b and task-c reach main and the green record release.ps1 reads" {
        $root = New-Sandbox -Work $three
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $run = Invoke-Integrate -Root $root -GateScript (New-RedWhileGate -Root $root -File "src/a/task-a.txt")
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected 2 -Actual @($run.GateCalls).Count -Because "the whole branch, then the rebuilt one: $($run.GateCalls -join ' / ')"
        Assert-True -Condition ($run.GateCalls[0] -match "\|red$" -and $run.GateCalls[1] -match "\|green$") -Because "red with task-a, green without it: $($run.GateCalls -join ' / ')"
        $rebuilt = @(Get-RebuiltBranches -Root $root)
        Assert-Equal -Expected 1 -Actual @($rebuilt).Count -Because "one rebuilt branch: $($rebuilt -join ', ')"
        $second = @($run.GateCalls[1] -split "\|")[1]
        Assert-Equal -Expected (Get-Sha -Root $root -Revision $rebuilt[0]) -Actual $second -Because "the second gate ran on the rebuilt branch's tip"
        Assert-True -Condition (-not (Test-OnBranch -Root $root -Revision $second -File "src/a/task-a.txt")) -Because "the rebuilt branch does not hold task-a"
        foreach ($file in @("src/b/task-b.txt", "src/c/task-c.txt")) { Assert-True -Condition (Test-OnBranch -Root $root -Revision $second -File $file) -Because "the rebuilt branch holds $file" }
        Assert-True -Condition (Test-TeamAncestor -RepoRoot $root -Ancestor $mainBefore -Of $second) -Because "it is built on main"

        $mainAfter = Get-Sha -Root $root -Revision "main"
        Assert-True -Condition ($mainAfter -ne $mainBefore) -Because "main moved"
        Assert-True -Condition (-not (Test-OnBranch -Root $root -Revision "main" -File "src/a/task-a.txt")) -Because "task-a is not on main"
        Assert-Equal -Expected (Get-Sha -Root $root -Revision "$second^{tree}") -Actual (Get-Sha -Root $root -Revision "main^{tree}") -Because "main holds exactly what the second gate ran on"
        Assert-Equal -Expected "returned" -Actual (Get-TaskById -Queue $run.Queue -Id "task-a").state -Because "task-a goes back to its worker"
        foreach ($id in @("task-b", "task-c")) {
            $task = Get-TaskById -Queue $run.Queue -Id $id
            Assert-Equal -Expected "awaiting_release" -Actual $task.state -Because "$id passed"
            Assert-Equal -Expected $mainAfter -Actual $task.sha -Because "$id carries main's sha"
            Assert-True -Condition ($task.reason -match "kırmızı iş ayrıldı: task-a" -and $task.reason.Contains($rebuilt[0])) -Because "the Onay Merkezi line says what was left out and where: $($task.reason)"
        }
        . (Join-Path $repoRoot "scripts\lib\TeamRelease.ps1")
        $evidence = Find-TeamReleaseGate -ReportsRoot (Join-Path $root "team\reports") -Sha $mainAfter
        Assert-True -Condition ([bool]$evidence.Found -and [bool]$evidence.Pass) -Because "release.ps1 finds a green record and a PASS log for main: $($evidence.Why)"
        Assert-True -Condition ($run.Report -match "kırmızı iş ayrıldı: task-a; kalanlar yeniden kapıda" -and $run.Report.Contains($rebuilt[0])) -Because "the report names the rebuilt branch and who was left out: $($run.Report)"
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $run.Queue).Count -Because "the queue keeps the protocol"
    }

    Test-Case "red card apart (2): a red gate that blames nobody gates nothing more: everything waits with today's words" {
        $root = New-Sandbox -Work $three
        $run = Invoke-Integrate -Root $root -Gate "red" -Names "src/shared/nobody.txt"
        Assert-Equal -Expected 6 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected 1 -Actual @($run.GateCalls).Count -Because "no second gate"
        Assert-Equal -Expected 0 -Actual @(Get-RebuiltBranches -Root $root).Count -Because "nothing is rebuilt"
        foreach ($id in @("task-a", "task-b", "task-c")) {
            $task = Get-TaskById -Queue $run.Queue -Id $id
            Assert-Equal -Expected "merged" -Actual $task.state -Because "$id waits"
            Assert-True -Condition ($task.reason -match "aynı commit yeniden kapıya girmez: yeni bir commit ya da lead'in -ClearGateStop'u beklenir" -and $task.reason -notmatch "ayrıldı") -Because "today's words: $($task.reason)"
        }
    }

    Test-Case "red card apart (3): the rebuilt branch is red too - no third gate, everything waits, and the Danışman is told" {
        $root = New-Sandbox -Work $three
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $run = Invoke-Integrate -Root $root -Gate "red" -Names "src/a/task-a.txt"
        Assert-Equal -Expected 6 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected 2 -Actual @($run.GateCalls).Count -Because "one extra gate at most: $($run.GateCalls -join ' / ')"
        Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "main") -Because "main is where it was"
        Assert-Equal -Expected "returned" -Actual (Get-TaskById -Queue $run.Queue -Id "task-a").state -Because "task-a was blamed"
        foreach ($id in @("task-b", "task-c")) {
            $task = Get-TaskById -Queue $run.Queue -Id $id
            Assert-Equal -Expected "merged" -Actual $task.state -Because "$id waits"
            Assert-True -Condition ($task.reason -match "Danışman") -Because "$id says the Danışman was told: $($task.reason)"
        }
        Assert-True -Condition ($run.Report -match "Danışman" -and $run.Report -match "kırmızı iş ayrıldı: task-a") -Because "the report has the Danışman's line: $($run.Report)"
        # The next run gates nothing: task-a holds the branch, as before.
        $again = Invoke-Integrate -Root $root -Gate "green"
        Assert-Equal -Expected 2 -Actual @($again.GateCalls).Count -Because "no gate while task-a is returned"
    }

    Test-Case "red card apart (4): when the blamed task is the only one on the branch there is nothing to rebuild and no second gate" {
        $root = New-Sandbox -Work @(@{ Id = "task-a"; Area = "src/a" })
        $run = Invoke-Integrate -Root $root -Gate "red" -Names "src/a/task-a.txt"
        Assert-Equal -Expected 6 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected 1 -Actual @($run.GateCalls).Count -Because "no second gate"
        Assert-Equal -Expected 0 -Actual @(Get-RebuiltBranches -Root $root).Count -Because "nothing is rebuilt"
        Assert-Equal -Expected "returned" -Actual (Get-TaskById -Queue $run.Queue -Id "task-a").state -Because "task-a goes back"
        $reason = (Get-TaskById -Queue $run.Queue -Id "task-a").reason
        Assert-True -Condition ($reason -match "düzeltilip yeniden birleşene kadar" -and $reason -notmatch "ayrıldı") -Because "today's words, nothing set apart: $reason"
    }

    Test-Case "red card apart (5): a task whose branch was built on the blamed one's is left out too, and named; the others pass" {
        $root = New-Sandbox -Work @(@{ Id = "task-a"; Area = "src/a" }, @{ Id = "task-b"; Area = "src/b" })
        $tree = Join-Path "$root-tools" "wt-task-d"
        [void](Invoke-SandboxGit -Root $root -Arguments @("branch", "team/c1/worker-task-d", "team/c1/worker-task-a"))
        [void](Invoke-SandboxGit -Root $root -Arguments @("worktree", "add", "-q", $tree, "team/c1/worker-task-d"))
        [void](New-Item -ItemType Directory -Force -Path (Join-Path $tree "src\d"))
        Set-Content -LiteralPath (Join-Path $tree "src\d\task-d.txt") -Value "work on task-d" -Encoding ASCII
        [void](Invoke-SandboxGit -Root $tree -Arguments @("add", "-A"))
        [void](Invoke-SandboxGit -Root $tree -Arguments @("commit", "-q", "-m", "work on task-d"))
        [void](Invoke-SandboxGit -Root $root -Arguments @("worktree", "remove", "--force", $tree))
        $merge = Merge-TeamBranch -RepoRoot $root -CycleId "c1" -Branch "team/c1/worker-task-d" -Base "main"
        Assert-True -Condition ([bool]$merge.Merged) -Because "task-d is merged: $($merge.Detail)"
        $queue = Read-TeamJson -Path (Join-Path $root "team\queue.json")
        $d = New-Task -Id "task-d" -Area @("src/d") -Branch "team/c1/worker-task-d" -Integration "integrate/c1"
        $d | Add-Member -NotePropertyName sha -NotePropertyValue (Get-Sha -Root $root -Revision "team/c1/worker-task-d")
        $queue.tasks = @(@($queue.tasks) + @($d))
        Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document $queue

        $run = Invoke-Integrate -Root $root -GateScript (New-RedWhileGate -Root $root -File "src/a/task-a.txt")
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Output
        Assert-Equal -Expected 2 -Actual @($run.GateCalls).Count -Because "two gates"
        $second = @($run.GateCalls[1] -split "\|")[1]
        Assert-True -Condition (-not (Test-OnBranch -Root $root -Revision $second -File "src/d/task-d.txt")) -Because "task-d rides on task-a's code: left out"
        Assert-Equal -Expected "returned" -Actual (Get-TaskById -Queue $run.Queue -Id "task-a").state -Because "task-a was blamed"
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "task-d").state -Because "task-d was not blamed: it waits for task-a"
        Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $run.Queue -Id "task-b").state -Because "task-b passed"
        Assert-True -Condition ($run.Report -match "task-d") -Because "the report names what was dropped with it: $($run.Report)"
        Assert-True -Condition ((Get-TaskById -Queue $run.Queue -Id "task-d").reason -match "task-a") -Because "task-d says whom it waits for: $((Get-TaskById -Queue $run.Queue -Id 'task-d').reason)"
    }

    Write-Host ""
    Write-Host "an older cycle's integration branch that holds merged, unreleased cards is carried forward"

    function New-CarrySandbox {
        <#
            main; integrate/da with task-a's work, never released (2026-10-05: integrate/d20261004,
            26 commits ahead of main); integrate/din, whose work main already has; and
            team/db/worker-x, the new cycle's first approved branch. -Conflict: main changed
            task-a's file another way after integrate/da left it.
        #>
        param([switch]$Conflict)
        $root = Join-Path $env:TEMP ("pagentos-carry-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
        [void]$sandboxes.Add($root)
        [void](New-Item -ItemType Directory -Force -Path (Join-Path $root "src"))
        Set-Content -LiteralPath (Join-Path $root ".gitignore") -Value ".claude/worktrees/" -Encoding ASCII
        Set-Content -LiteralPath (Join-Path $root "src\shared.txt") -Value "base" -Encoding ASCII
        [void](Invoke-SandboxGit -Root $root -Arguments @("init", "-q", "-b", "main"))
        [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.name", "team test"))
        [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.email", "team@example.invalid"))
        [void](Invoke-SandboxGit -Root $root -Arguments @("config", "core.autocrlf", "false"))
        [void](Invoke-SandboxGit -Root $root -Arguments @("add", "-A"))
        [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-m", "the sandbox"))
        $commitOn = {
            param([string]$Branch, [hashtable]$Files, [string]$Message)
            [void](Invoke-SandboxGit -Root $root -Arguments @("branch", $Branch, "main"))
            $tree = "$root-wt"
            [void](Invoke-SandboxGit -Root $root -Arguments @("worktree", "add", "-q", $tree, $Branch))
            foreach ($name in $Files.Keys) { Set-Content -LiteralPath (Join-Path $tree $name) -Value $Files[$name] -Encoding ASCII }
            [void](Invoke-SandboxGit -Root $tree -Arguments @("add", "-A"))
            [void](Invoke-SandboxGit -Root $tree -Arguments @("commit", "-q", "-m", $Message))
            [void](Invoke-SandboxGit -Root $root -Arguments @("worktree", "remove", "--force", $tree))
        }
        & $commitOn "integrate/da" @{ "src\shared.txt" = "task-a changed it"; "src\a.txt" = "work on task-a" } "work on task-a"
        & $commitOn "integrate/din" @{ "src\in.txt" = "work on task-in" } "work on task-in"
        [void](Invoke-SandboxGit -Root $root -Arguments @("merge", "-q", "--no-ff", "-m", "released: integrate/din", "integrate/din"))
        if ($Conflict) {
            Set-Content -LiteralPath (Join-Path $root "src\shared.txt") -Value "main changed it another way" -Encoding ASCII
            [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-am", "main moved on"))
        }
        & $commitOn "team/db/worker-x" @{ "src\x.txt" = "work on task-x" } "work on task-x"
        return $root
    }

    function New-CarryQueue {
        return (New-Queue -Tasks @(
                (New-Task -Id "task-a" -Integration "integrate/da"), (New-Task -Id "task-a2" -Integration "integrate/da"),
                (New-Task -Id "task-in" -Integration "integrate/din"),
                (New-Task -Id "task-gone" -State "awaiting_release" -Integration "integrate/da"),
                (New-Task -Id "task-now" -Integration "integrate/db")))
    }

    Test-Case "carry (a): the new cycle's integration branch takes in an older one whose merged cards main does not have, by a 'carried forward' merge" {
        $root = New-CarrySandbox
        $queue = New-CarryQueue
        $merge = Merge-TeamBranch -RepoRoot $root -CycleId "db" -Branch "team/db/worker-x" -Base "main" -Queue $queue
        Assert-True -Condition ([bool]$merge.Merged -and -not [bool]$merge.Already) -Because "the worker's branch is merged: $($merge.Detail)"
        $log = Invoke-SandboxGit -Root $root -Arguments @("log", "--first-parent", "--format=%s", "main..integrate/db")
        Write-Host "        git log --first-parent main..integrate/db:`n          $($log -replace "`n", "`n          ")"
        Assert-Equal -Expected "merge: team/db/worker-x into integrate/db`ncarried forward: integrate/da" -Actual $log -Because "the carry-over first, then the cycle's own merge"
        Assert-True -Condition (Test-TeamAncestor -RepoRoot $root -Ancestor "integrate/da" -Of "integrate/db") -Because "task-a's commit is on the new branch"
        Assert-True -Condition (-not (Test-TeamAncestor -RepoRoot $root -Ancestor "integrate/da" -Of "main")) -Because "and still not on main: the release takes it"
        foreach ($id in @("task-a", "task-a2")) {
            $task = Get-TaskById -Queue $queue -Id $id
            Assert-Equal -Expected "merged" -Actual $task.state -Because "$id is still merged"
            Assert-Equal -Expected "integrate/db" -Actual $task.integration_branch -Because "$id is gated with the new branch now"
        }
        Assert-Equal -Expected "integrate/da" -Actual (Get-TaskById -Queue $queue -Id "task-gone").integration_branch -Because "a card that is not 'merged' is not moved"
        Assert-Equal -Expected 1 -Actual @($merge.CarriedForward).Count -Because "one branch carried"
        Assert-True -Condition ([bool]$merge.CarriedForward[0].Carried -and $merge.CarriedForward[0].Branch -eq "integrate/da") -Because "it says which"
        $again = Merge-TeamBranch -RepoRoot $root -CycleId "db" -Branch "team/db/worker-x" -Base "main" -Queue (New-CarryQueue)
        Assert-True -Condition ([bool]$again.Already -and @($again.CarriedForward).Count -eq 0) -Because "a branch that is already there carries nothing again"
    }

    Test-Case "carry (b): an older integration branch main already holds is not merged again" {
        $root = New-CarrySandbox
        $queue = New-Queue -Tasks @((New-Task -Id "task-in" -Integration "integrate/din"))
        $merge = Merge-TeamBranch -RepoRoot $root -CycleId "db" -Branch "team/db/worker-x" -Base "main" -Queue $queue
        Assert-True -Condition ([bool]$merge.Merged) -Because "the worker's branch is merged: $($merge.Detail)"
        Assert-Equal -Expected "2" -Actual (Invoke-SandboxGit -Root $root -Arguments @("rev-list", "--count", "main..integrate/db")) -Because "the worker's commit and its merge, nothing else"
        Assert-Equal -Expected "merge: team/db/worker-x into integrate/db" -Actual (Invoke-SandboxGit -Root $root -Arguments @("log", "--first-parent", "--format=%s", "main..integrate/db")) -Because "no carry-over"
        Assert-Equal -Expected 0 -Actual @($merge.CarriedForward).Count -Because "nothing carried"
        Assert-Equal -Expected "integrate/din" -Actual (Get-TaskById -Queue $queue -Id "task-in").integration_branch -Because "task-in is left as it was"
    }

    Test-Case "carry (c): a carry-over that conflicts leaves the new branch at main and stops the cards for the Danisman, by name" {
        $root = New-CarrySandbox -Conflict
        $queue = New-CarryQueue
        $mainBefore = Get-Sha -Root $root -Revision "main"
        $merge = Merge-TeamBranch -RepoRoot $root -CycleId "db" -Branch "team/db/worker-x" -Base "main" -Queue $queue
        Assert-True -Condition ([bool]$merge.Merged) -Because "the worker's own branch is still merged: $($merge.Detail)"
        Assert-Equal -Expected $mainBefore -Actual (Get-Sha -Root $root -Revision "integrate/db^1") -Because "integrate/db was at main when the worker's branch came in"
        Assert-True -Condition (-not (Test-TeamAncestor -RepoRoot $root -Ancestor "integrate/da" -Of "integrate/db")) -Because "nothing of integrate/da is on it"
        $tree = Get-TeamWorktreePath -RepoRoot $root -Branch "integrate/db"
        Assert-Equal -Expected "" -Actual (Invoke-SandboxGit -Root $tree -Arguments @("status", "--porcelain")) -Because "the conflict was aborted"
        foreach ($id in @("task-a", "task-a2")) {
            $task = Get-TaskById -Queue $queue -Id $id
            Assert-Equal -Expected "stopped" -Actual $task.state -Because "$id goes to the Danisman"
            Assert-Equal -Expected "yetim entegrasyon: integrate/da" -Actual $task.reason -Because "$id says why"
        }
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $queue -Id "task-in").state -Because "task-in is main's already"
        $carry = @($merge.CarriedForward)
        Assert-True -Condition ($carry.Count -eq 1 -and -not [bool]$carry[0].Carried) -Because "one carry-over, not made"
        Assert-True -Condition ($carry[0].Line -match "integrate/da" -and $carry[0].Line -match "task-a, task-a2" -and $carry[0].Line -match "Danışman") -Because "a Turkish line names the branch and the cards: $($carry[0].Line)"
        Write-Host "        $($carry[0].Line)"
    }

    Test-Case "carry (d): Get-TeamOrphanMerges names the merged cards of a branch neither current nor in main, and the cycle report says so" {
        $root = New-CarrySandbox
        $queue = New-CarryQueue
        $inMain = @(Get-TeamBranchesInMain -RepoRoot $root -Branches @("integrate/da", "integrate/din", "integrate/db") -Base "main")
        Assert-Equal -Expected "integrate/din" -Actual ($inMain -join ",") -Because "only integrate/din is in main (integrate/db does not exist yet)"
        $orphans = @(Get-TeamOrphanMerges -Queue $queue -Current "integrate/db" -InMain $inMain)
        Assert-Equal -Expected "task-a,task-a2" -Actual (@($orphans | ForEach-Object { $_.id }) -join ",") -Because "exactly the cards of (a)"
        Assert-Equal -Expected "task-now" -Actual ((@(Get-TeamOrphanMerges -Queue $queue -Current "integrate/da" -InMain $inMain) | ForEach-Object { $_.id }) -join ",") -Because "the current branch is nobody's orphan (and a branch that does not exist is not in main)"
        Assert-Equal -Expected 0 -Actual @(Get-TeamOrphanMerges -Queue (New-Queue) -Current "integrate/db" -InMain @()).Count -Because "an empty queue"
        $cycle = [pscustomobject]@{ machine = "test"; started_at = "s"; ended_at = "e"; runs = @(); stops = @(); risks = @(); gaps = @() }
        $report = New-TeamCycleReport -CycleId "db" -Queue $queue -Cycle $cycle -RepoRoot $root -Base "main"
        Assert-True -Condition ($report -match "task-a .*integrate/da" -and $report -match "task-a2 .*integrate/da") -Because "the report names them: $report"
        Assert-True -Condition ($report -notmatch "task-in .*integrate/din" -and $report -notmatch "task-now .*integrate/db") -Because "and none other: $report"
    }
}
finally {
    foreach ($step in $stepProcesses) { try { if (-not $step.HasExited) { Stop-TeamProcessTree -ProcessId $step.Id } } catch { } }
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
