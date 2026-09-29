<#
.SYNOPSIS
    The agent team's cycle (docs/TEAM_PROTOCOL.md): the queue, the lock, the runs, the report.

.DESCRIPTION
    Two halves.

    The decisions (scripts/lib/TeamQueue.ps1) are driven as functions, with the near misses
    beside the hits, and the list of states is held to team/queue.schema.json by READING
    the schema: two lists of states is two protocols.

    The cycle itself (scripts/team/cycle.ps1) is run for real, in a git repository made for
    the test, with scripts/tests/lib/fake-claude.ps1 in place of the model. What is asserted
    is what is on disk afterwards: the queue, the branches, the report, the lock - and the
    fake's own log of where each run was started and with what cap.

    No model is started, nothing is registered with the Task Scheduler, and this
    repository's own branches, worktrees and team/ files are not written to.

    Run: powershell -NoProfile -File scripts\tests\team-cycle.tests.ps1
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")

$script:Failures = 0
$script:Passes = 0

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
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
    param([string]$Id, [string]$State = "approved", [string[]]$Area = @("src/area"), [string]$Branch = "")
    return [pscustomobject]@{
        id = $Id; title = "the task $Id"; roadmap_row = "row"; state = $State; area = @($Area)
        branch = $Branch; worktree = ""; assignee = ""; reports = @(); budget = [pscustomobject]@{ max_usd = 5 }
        created_at = "2026-09-30T00:00:00Z"; updated_at = "2026-09-30T00:00:00Z"
    }
}

function New-Queue {
    param([object[]]$Tasks = @())
    return [pscustomobject]@{ version = 1; tasks = @($Tasks) }
}

# ============================================================================ the decisions

Write-Host ""
Write-Host "the queue and its schema"

$schema = Read-TeamJson -Path (Join-Path $repoRoot "team\queue.schema.json")
$schemaTask = $schema.'$defs'.task

Test-Case "the states of the schema are the states of the script, in the same order" {
    $fromSchema = @($schemaTask.properties.state.enum)
    $fromScript = @(Get-TeamStates)
    Assert-Equal -Expected 13 -Actual @($fromScript).Count -Because "the protocol names thirteen states"
    Assert-Equal -Expected ($fromSchema -join ",") -Actual ($fromScript -join ",") -Because "one list of states"
}

Test-Case "the fields the schema requires are the fields the script requires" {
    $required = @($schemaTask.required | Sort-Object)
    $checked = @($script:TeamRequiredFields | Sort-Object)
    Assert-Equal -Expected ($required -join ",") -Actual ($checked -join ",") -Because "one list of required fields"
}

Test-Case "every gate is a state, and there are three" {
    $gates = Get-TeamOwnerGates
    Assert-Equal -Expected 3 -Actual @($gates.Keys).Count -Because "idea, release, real-world evidence"
    foreach ($gate in @($gates.Keys)) {
        Assert-True -Condition (@(Get-TeamStates) -contains $gate) -Because "$gate is a state"
    }
}

Test-Case "the queue in this repository keeps the protocol" {
    $queue = Read-TeamJson -Path (Join-Path $repoRoot "team\queue.json")
    $problems = @(Test-TeamQueue -Queue $queue)
    Assert-Equal -Expected 0 -Actual @($problems).Count -Because ("team/queue.json: " + ($problems -join "; "))
    foreach ($task in (Get-TeamTasks -Queue $queue)) {
        foreach ($name in @($task.PSObject.Properties | ForEach-Object { $_.Name })) {
            Assert-True -Condition ($null -ne $schemaTask.properties.PSObject.Properties[$name]) `
                -Because "the field '$name' of $($task.id) is not in the schema"
        }
    }
}

Test-Case "the lock in this repository is released" {
    $lock = Read-TeamJson -Path (Join-Path $repoRoot "team\lock.json")
    Assert-Equal -Expected $false -Actual ([bool]$lock.held) -Because "a committed lock that is held stops every cycle for six hours"
}

Test-Case "an empty queue, one task and many are all read as a list" {
    Assert-Equal -Expected 0 -Actual @(Get-TeamTasks -Queue (New-Queue)).Count -Because "none"
    Assert-Equal -Expected 1 -Actual @(Get-TeamTasks -Queue (New-Queue -Tasks @((New-Task -Id "one-task")))).Count -Because "one"
    Assert-Equal -Expected 2 -Actual @(Get-TeamTasks -Queue (New-Queue -Tasks @((New-Task -Id "one-task"), (New-Task -Id "two-task" -Area @("src/b"))))).Count -Because "two"
    Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue (New-Queue)).Count -Because "an empty queue keeps the protocol"
}

$broken = @(
    @{ Name = "a state that is not one"; Task = { New-Task -Id "bad-state" -State "finished" }; Says = "is not a state" },
    @{ Name = "an id with a capital letter"; Task = { New-Task -Id "Bad-Id" }; Says = "an id is" },
    @{ Name = "an area outside the repository"; Task = { New-Task -Id "bad-area" -Area @("../elsewhere") }; Says = "inside the repository" },
    @{ Name = "an area that is a drive path"; Task = { New-Task -Id "bad-drive" -Area @("C:/Windows") }; Says = "inside the repository" },
    @{ Name = "the branch nobody merges"; Task = { New-Task -Id "bad-branch" -Branch "feat/hand-gestures-stage1" }; Says = "not a team branch" },
    @{ Name = "main as a task branch"; Task = { New-Task -Id "bad-main" -Branch "main" }; Says = "not a team branch" },
    @{ Name = "work without an area"; Task = { New-Task -Id "no-area" -State "assigned" -Area @() }; Says = "names its file area" }
)
foreach ($case in $broken) {
    Test-Case "refused: $($case.Name)" {
        $problems = @(Test-TeamQueue -Queue (New-Queue -Tasks @((& $case.Task))))
        Assert-True -Condition (@($problems | Where-Object { $_ -match [regex]::Escape($case.Says) }).Count -ge 1) `
            -Because ("expected a problem saying '$($case.Says)', got: " + ($problems -join "; "))
    }
}

Test-Case "refused: a task without one of its fields" {
    $task = New-Task -Id "no-budget"
    $task.PSObject.Properties.Remove("budget")
    $problems = @(Test-TeamQueue -Queue (New-Queue -Tasks @($task)))
    Assert-True -Condition (@($problems | Where-Object { $_ -match "'budget' is missing" }).Count -eq 1) -Because ($problems -join "; ")
}

Test-Case "refused: an id used twice" {
    $problems = @(Test-TeamQueue -Queue (New-Queue -Tasks @((New-Task -Id "same-id"), (New-Task -Id "same-id"))))
    Assert-True -Condition (@($problems | Where-Object { $_ -match "used twice" }).Count -eq 1) -Because ($problems -join "; ")
}

Test-Case "refused: two tasks in work on one area, or on an area inside the other's" {
    $same = New-Queue -Tasks @((New-Task -Id "first-task" -State "assigned" -Area @("services/api/app/team")),
        (New-Task -Id "second-task" -State "in_progress" -Area @("services/api/app/team/")))
    Assert-True -Condition (@(Test-TeamQueue -Queue $same | Where-Object { $_ -match "overlaps" }).Count -ge 1) -Because "the same area"
    $inside = New-Queue -Tasks @((New-Task -Id "first-task" -State "assigned" -Area @("services/api")),
        (New-Task -Id "second-task" -State "inspecting" -Area @("services/api/app/team")))
    Assert-True -Condition (@(Test-TeamQueue -Queue $inside | Where-Object { $_ -match "overlaps" }).Count -ge 1) -Because "an area inside another"
    $apart = New-Queue -Tasks @((New-Task -Id "first-task" -State "assigned" -Area @("services/api/app/team")),
        (New-Task -Id "second-task" -State "assigned" -Area @("services/api/app/teams")))
    Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $apart).Count -Because "'team' and 'teams' are two directories"
    $waiting = New-Queue -Tasks @((New-Task -Id "first-task" -State "assigned" -Area @("services/api")),
        (New-Task -Id "second-task" -State "awaiting_owner" -Area @("services/api")))
    Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $waiting).Count -Because "a task at a gate is not in work"
}

Write-Host ""
Write-Host "what a state means for the cycle"

$expected = @(
    @{ State = "proposed"; Kind = "move"; Role = ""; Next = "awaiting_owner" },
    @{ State = "awaiting_owner"; Kind = "gate"; Role = ""; Next = "" },
    @{ State = "approved"; Kind = "move"; Role = ""; Next = "assigned" },
    @{ State = "assigned"; Kind = "run"; Role = "worker"; Next = "inspecting" },
    @{ State = "in_progress"; Kind = "run"; Role = "worker"; Next = "inspecting" },
    @{ State = "inspecting"; Kind = "run"; Role = "inspector"; Next = "" },
    @{ State = "returned"; Kind = "run"; Role = "worker"; Next = "inspecting" },
    @{ State = "merged"; Kind = "rest"; Role = ""; Next = "" },
    @{ State = "awaiting_release"; Kind = "gate"; Role = ""; Next = "" },
    @{ State = "released"; Kind = "rest"; Role = ""; Next = "" },
    @{ State = "awaiting_real_evidence"; Kind = "gate"; Role = ""; Next = "" },
    @{ State = "done"; Kind = "rest"; Role = ""; Next = "" },
    @{ State = "stopped"; Kind = "rest"; Role = ""; Next = "" }
)
Test-Case "every state has an answer, and the table names all thirteen" {
    Assert-Equal -Expected (@(Get-TeamStates) -join ",") -Actual (@($expected | ForEach-Object { $_.State }) -join ",") -Because "the table is the list"
    foreach ($row in $expected) {
        $next = Get-TeamNextRole -Task (New-Task -Id "any-task" -State $row.State)
        Assert-Equal -Expected $row.Kind -Actual $next.Kind -Because "$($row.State): kind"
        Assert-Equal -Expected $row.Role -Actual $next.Role -Because "$($row.State): role"
        Assert-Equal -Expected $row.Next -Actual $next.NextState -Because "$($row.State): next state"
    }
}

Test-Case "no run is ever started for a task at one of the owner's gates" {
    foreach ($gate in @((Get-TeamOwnerGates).Keys)) {
        $next = Get-TeamNextRole -Task (New-Task -Id "any-task" -State $gate)
        Assert-Equal -Expected "gate" -Actual $next.Kind -Because $gate
        Assert-Equal -Expected "" -Actual $next.Role -Because "$gate starts nobody"
    }
}

Test-Case "an approved task that needs a plan goes to the integrator, once" {
    $task = New-Task -Id "needs-plan"
    $task | Add-Member -NotePropertyName needs_integration -NotePropertyValue $true
    Assert-Equal -Expected "integrator" -Actual (Get-TeamNextRole -Task $task).Role -Because "no plan yet"
    $task | Add-Member -NotePropertyName plan -NotePropertyValue "team/plans/needs-plan-integration.md"
    Assert-Equal -Expected "move" -Actual (Get-TeamNextRole -Task $task).Kind -Because "the plan is there"
}

Write-Host ""
Write-Host "the inspector's verdict"

$verdicts = @(
    @{ Text = "numbers`nAPPROVE"; Verdict = "APPROVE"; Detail = "" },
    @{ Text = "numbers`n``APPROVE``"; Verdict = "APPROVE"; Detail = "" },
    @{ Text = "RETURN (the test is missing; the ADR has no number)"; Verdict = "RETURN"; Detail = "the test is missing; the ADR has no number" },
    @{ Text = "REJECT (it writes outside its area)"; Verdict = "REJECT"; Detail = "it writes outside its area" },
    @{ Text = "RETURN: two things"; Verdict = "RETURN"; Detail = "two things" },
    @{ Text = "APPROVE`nlater:`nRETURN (on reflection)"; Verdict = "RETURN"; Detail = "on reflection" },
    @{ Text = "I cannot APPROVE this."; Verdict = "NONE"; Detail = "" },
    @{ Text = "approve"; Verdict = "NONE"; Detail = "" },
    @{ Text = "APPROVED_BY nobody"; Verdict = "NONE"; Detail = "" },
    @{ Text = ""; Verdict = "NONE"; Detail = "" }
)
foreach ($case in $verdicts) {
    Test-Case ("'" + ($case.Text -replace "`n", " / ") + "' is $($case.Verdict)") {
        $verdict = Get-TeamVerdict -Report $case.Text
        Assert-Equal -Expected $case.Verdict -Actual $verdict.Verdict -Because "the verdict"
        Assert-Equal -Expected $case.Detail -Actual $verdict.Detail -Because "the detail"
    }
}

Test-Case "a report without a verdict is not an approval" {
    $after = Get-TeamStateAfterInspection -Task (New-Task -Id "any-task" -State "inspecting") -Verdict "NONE"
    Assert-Equal -Expected "returned" -Actual $after.State -Because "no verdict, no merge"
}

Test-Case "returned once it goes back; returned twice it is stopped" {
    $task = New-Task -Id "any-task" -State "inspecting"
    $first = Get-TeamStateAfterInspection -Task $task -Verdict "RETURN"
    Assert-Equal -Expected "returned" -Actual $first.State -Because "the first return"
    $task | Add-Member -NotePropertyName returns -NotePropertyValue $first.Returns
    $second = Get-TeamStateAfterInspection -Task $task -Verdict "RETURN"
    Assert-Equal -Expected "stopped" -Actual $second.State -Because "the second return stops the task (section 10)"
    Assert-Equal -Expected "stopped" -Actual (Get-TeamStateAfterInspection -Task $task -Verdict "REJECT").State -Because "a rejection stops it at once"
    Assert-Equal -Expected "merged" -Actual (Get-TeamStateAfterInspection -Task $task -Verdict "APPROVE").State -Because "an approval merges"
}

Write-Host ""
Write-Host "the forty lines"

Test-Case "a long report is cut to forty lines and keeps its END" {
    $text = (1..100 | ForEach-Object { "line $_" }) -join "`n"
    $summary = @(Get-TeamSummary -Text ($text + "`nAPPROVE`n`n"))
    Assert-Equal -Expected 40 -Actual @($summary).Count -Because "forty"
    Assert-Equal -Expected "APPROVE" -Actual $summary[39] -Because "the verdict is at the end, and the end is kept"
    Assert-True -Condition ($summary[0] -match "kesildi") -Because "the cut is said"
}

Test-Case "a short report, an empty one and one line are not padded" {
    Assert-Equal -Expected 2 -Actual @(Get-TeamSummary -Text "a`nb").Count -Because "two lines"
    Assert-Equal -Expected 1 -Actual @(Get-TeamSummary -Text "a").Count -Because "one line"
    Assert-Equal -Expected 0 -Actual @(Get-TeamSummary -Text "").Count -Because "nothing"
    Assert-Equal -Expected 40 -Actual @(Get-TeamSummary -Text ((1..40 | ForEach-Object { "l$_" }) -join "`n")).Count -Because "exactly forty is not cut"
}

Write-Host ""
Write-Host "the lock"

$now = [datetime]::SpecifyKind([datetime]"2026-09-30T02:00:00", [System.DateTimeKind]::Utc)
function New-HeldLock {
    param([string]$Machine, [string]$At)
    return [pscustomobject]@{ held = $true; machine = $Machine; cycle_id = "c1"; pid = 1; acquired_at = $At }
}

Test-Case "no lock, and a released one, are free" {
    Assert-Equal -Expected "free" -Actual (Get-TeamLockDecision -Lock $null -Machine "MAIL" -Now $now).Kind -Because "no file"
    Assert-Equal -Expected "free" -Actual (Get-TeamLockDecision -Lock (New-TeamLockReleased) -Machine "MAIL" -Now $now).Kind -Because "released"
}

Test-Case "the other machine's fresh lock stops the cycle" {
    $decision = Get-TeamLockDecision -Lock (New-HeldLock -Machine "GMKADIRAKBABA" -At "2026-09-30T01:00:00Z") -Machine "MAIL" -Now $now
    Assert-Equal -Expected "held" -Actual $decision.Kind -Because "one hour old"
    Assert-Equal -Expected $false -Actual $decision.MayRun -Because "two machines never write one checkout"
    Assert-Equal -Expected "GMKADIRAKBABA" -Actual $decision.Holder -Because "the report names the holder"
}

Test-Case "a lock is stale at six hours and not a minute before" {
    $before = Get-TeamLockDecision -Lock (New-HeldLock -Machine "GMKADIRAKBABA" -At "2026-09-29T20:01:00Z") -Machine "MAIL" -Now $now
    Assert-Equal -Expected "held" -Actual $before.Kind -Because "5 h 59 min"
    $at = Get-TeamLockDecision -Lock (New-HeldLock -Machine "GMKADIRAKBABA" -At "2026-09-29T20:00:00Z") -Machine "MAIL" -Now $now
    Assert-Equal -Expected "stale" -Actual $at.Kind -Because "6 h"
    Assert-Equal -Expected $true -Actual $at.MayRun -Because "a stale lock is taken over"
}

Test-Case "our own fresh lock is ours, in any letter case" {
    $decision = Get-TeamLockDecision -Lock (New-HeldLock -Machine "mail" -At "2026-09-30T01:30:00Z") -Machine "MAIL" -Now $now
    Assert-Equal -Expected "ours" -Actual $decision.Kind -Because "the same machine"
}

Test-Case "a lock that does not say when it was taken cannot be shown to be fresh" {
    foreach ($at in @("", "yesterday", $null)) {
        $decision = Get-TeamLockDecision -Lock (New-HeldLock -Machine "GMKADIRAKBABA" -At $at) -Machine "MAIL" -Now $now
        Assert-Equal -Expected "stale" -Actual $decision.Kind -Because "'$at'"
    }
}

Test-Case "the lock this script writes is one it can read" {
    $written = New-TeamLock -Machine "MAIL" -CycleId "c1" -Now $now
    Assert-Equal -Expected "2026-09-30T02:00:00Z" -Actual $written.acquired_at -Because "UTC, to the second"
    $decision = Get-TeamLockDecision -Lock $written -Machine "GMKADIRAKBABA" -Now $now.AddHours(5)
    Assert-Equal -Expected "held" -Actual $decision.Kind -Because "five hours later, from the other machine"
}

Write-Host ""
Write-Host "the scripts themselves"

Test-Case "a team script that holds Turkish text says which encoding it is in" {
    # Windows PowerShell 5.1 reads a file without a byte-order mark as ANSI. A Turkish letter
    # becomes two wrong ones in the owner's report, and the byte 0x94 of an em dash is read
    # as a closing quotation mark: the first draft of TeamRun.ps1 did not parse.
    $files = @(Get-ChildItem -LiteralPath (Join-Path $repoRoot "scripts\team") -Filter *.ps1 -File)
    $files += @(Get-Item -LiteralPath (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1"), (Join-Path $repoRoot "scripts\lib\TeamRun.ps1"))
    $files += @(Get-Item -LiteralPath (Join-Path $repoRoot "scripts\tests\team-cycle.tests.ps1"), (Join-Path $repoRoot "scripts\tests\lib\fake-claude.ps1"))
    Assert-True -Condition (@($files).Count -ge 10) -Because "the team's scripts were found"
    $withText = 0
    foreach ($file in $files) {
        $bytes = [System.IO.File]::ReadAllBytes($file.FullName)
        $high = @($bytes | Where-Object { $_ -gt 127 }).Count
        if ($high -eq 0) { continue }
        $withText++
        $marked = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
        Assert-True -Condition $marked -Because "$($file.Name) holds non-ASCII text and has no byte-order mark"
    }
    Assert-True -Condition ($withText -ge 2) -Because "the report is written in Turkish somewhere"
}

Write-Host ""
Write-Host "names and areas"

Test-Case "a branch name is the protocol's" {
    Assert-Equal -Expected "team/pilot-01/worker-onay-merkezi" -Actual (Get-TeamBranchName -CycleId "pilot-01" -Role "worker" -Slug "onay-merkezi") -Because "section 4"
    foreach ($bad in @("Pilot", "a b", "../x", "")) {
        $threw = $false
        try { [void](Get-TeamBranchName -CycleId "c1" -Role "worker" -Slug $bad) } catch { $threw = $true }
        Assert-True -Condition $threw -Because "'$bad' is not part of a branch name"
    }
}

Test-Case "a worktree is under .claude\worktrees and only for a team branch" {
    $path = Get-TeamWorktreePath -RepoRoot "E:\repo" -Branch "team/c1/worker-a"
    Assert-Equal -Expected "E:\repo\.claude\worktrees\team\c1\worker-a" -Actual $path -Because "the ignored directory"
    foreach ($bad in @("main", "feat/hand-gestures-stage1", "team/../../x", "integrate/")) {
        $threw = $false
        try { [void](Get-TeamWorktreePath -RepoRoot "E:\repo" -Branch $bad) } catch { $threw = $true }
        Assert-True -Condition $threw -Because "'$bad' gets no worktree"
    }
}

Test-Case "a file is inside an area or it is not" {
    $area = @("services/api/app/team", "apps/web/src/app/core/approvals/*")
    Assert-True -Condition (Test-TeamPathInsideArea -Path "services/api/app/team/routes.py" -Area $area) -Because "inside"
    Assert-True -Condition (Test-TeamPathInsideArea -Path "apps\web\src\app\core\approvals\page.tsx" -Area $area) -Because "inside, with backslashes"
    Assert-True -Condition (-not (Test-TeamPathInsideArea -Path "services/api/app/teams/x.py" -Area $area)) -Because "a directory that begins the same"
    Assert-True -Condition (-not (Test-TeamPathInsideArea -Path "docs/HANDOFF.md" -Area $area)) -Because "a shared file is the lead's"
    Assert-True -Condition (-not (Test-TeamPathInsideArea -Path "x.py" -Area @())) -Because "no area, nothing inside"
}

Write-Host ""
Write-Host "a run"

$roles = Join-Path $repoRoot ".claude\agents"
Test-Case "every role of the protocol has a file that grants tools" {
    foreach ($role in @("lead", "researcher", "integrator", "worker", "inspector")) {
        $file = Join-Path $roles "$role.md"
        Assert-True -Condition (Test-Path -LiteralPath $file) -Because "$role.md"
        Assert-True -Condition (@(Get-TeamRoleTools -RoleFile $file).Count -ge 3) -Because "$role grants tools"
    }
}

Test-Case "the inspector cannot edit, and the researcher cannot run a shell" {
    Assert-True -Condition (@(Get-TeamRoleTools -RoleFile (Join-Path $roles "inspector.md")) -notcontains "Edit") -Because "the inspector changes no code"
    Assert-True -Condition (@(Get-TeamRoleTools -RoleFile (Join-Path $roles "researcher.md")) -notcontains "Bash") -Because "the researcher writes proposals"
}

Test-Case "a run is capped, fresh, and cannot start agents of its own" {
    $arguments = @(Get-TeamRunArguments -RoleFile (Join-Path $roles "lead.md") -MaxUsd 2.5 -PrefixArguments @("-File", "x.ps1"))
    $line = $arguments -join " "
    Assert-Equal -Expected "-File" -Actual $arguments[0] -Because "the prefix comes first"
    Assert-True -Condition ($line -match "--max-budget-usd 2\.5( |$)") -Because "the cap, with a point: $line"
    Assert-True -Condition ($line -match "--no-session-persistence") -Because "a fresh run"
    Assert-True -Condition ($line -match "--output-format json") -Because "the cost is read from the result"
    Assert-True -Condition ($line -notmatch "dangerously") -Because "permissions are not skipped"
    $tools = $arguments[[array]::IndexOf($arguments, "--allowedTools") + 1]
    Assert-True -Condition ($tools -notmatch "Agent") -Because "the cycle dispatches; a run does not: $tools"
    Assert-True -Condition ($tools -match "Read") -Because "the role's own tools are granted"
}

Test-Case "a role file without tools starts nothing" {
    $file = Join-Path $env:TEMP ("pagentos-team-role-" + [guid]::NewGuid().ToString("N") + ".md")
    try {
        Set-Content -LiteralPath $file -Value "---`nname: nobody`n---`nbody" -Encoding UTF8
        $threw = $false
        try { [void](Get-TeamRunArguments -RoleFile $file -MaxUsd 1) } catch { $threw = $true }
        Assert-True -Condition $threw -Because "no tools, no run"
    }
    finally { Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue }
}

Test-Case "what a run printed is a result only when it is the result document" {
    $good = Read-TeamRunResult -StdOut '{"type":"result","subtype":"success","is_error":false,"result":"done","total_cost_usd":0.5}' -ExitCode 0
    Assert-True -Condition $good.Ok -Because "a result"
    Assert-Equal -Expected 0.5 -Actual $good.CostUsd -Because "the cost"
    $overBudget = Read-TeamRunResult -StdOut '{"type":"result","subtype":"error_max_budget_usd","is_error":true,"result":"","total_cost_usd":5.01}' -ExitCode 1
    Assert-True -Condition (-not $overBudget.Ok) -Because "over budget is a failure"
    Assert-Equal -Expected 5.01 -Actual $overBudget.CostUsd -Because "and it is still counted"
    Assert-True -Condition (-not (Read-TeamRunResult -StdOut "I could not do that." -ExitCode 0).Ok) -Because "prose is not a result"
    Assert-True -Condition (-not (Read-TeamRunResult -StdOut "" -ExitCode 0).Ok) -Because "nothing is not a result"
    Assert-True -Condition (-not (Read-TeamRunResult -StdOut '{"result":"","total_cost_usd":0}' -ExitCode 0).Ok) -Because "an empty report is not a report"
}

Write-Host ""
Write-Host "the nightly task"

$nightly = Join-Path $repoRoot "scripts\team\register-nightly.ps1"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$probeName = "PagentOS Team Nightly Cycle TEST " + [guid]::NewGuid().ToString("N").Substring(0, 8)

Test-Case "without -Register nothing is registered, and the script says so" {
    $result = Invoke-NativeProcess -FilePath $powershell -TimeoutSeconds 120 -Arguments @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $nightly, "-MaxUsd", "12.5",
        "-TaskName", $probeName, "-Machine", "MAIL", "-HomeMachine", "MAIL")
    Assert-Equal -Expected 0 -Actual $result.ExitCode -Because $result.StdErr
    Assert-True -Condition ($result.StdOut -match "NOT registered") -Because $result.StdOut
    Assert-True -Condition ($result.StdOut -match "02:00 Europe/Istanbul") -Because "the hour is the protocol's"
    Assert-True -Condition ($result.StdOut -match "-MaxUsd 12\.5 ") -Because "the cap travels, with a point: $($result.StdOut)"
    Assert-True -Condition ($null -eq (Get-ScheduledTask -TaskName $probeName -ErrorAction SilentlyContinue)) -Because "the Task Scheduler was not touched"
}

Test-Case "on any machine but the home PC it refuses, even with -Register" {
    $result = Invoke-NativeProcess -FilePath $powershell -TimeoutSeconds 120 -SuccessExitCodes @(6) -Arguments @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $nightly, "-Register",
        "-TaskName", $probeName, "-Machine", "GMKADIRAKBABA", "-HomeMachine", "MAIL")
    Assert-Equal -Expected 6 -Actual $result.ExitCode -Because $result.StdOut
    Assert-True -Condition ($null -eq (Get-ScheduledTask -TaskName $probeName -ErrorAction SilentlyContinue)) -Because "nothing was registered"
}

# ============================================================================ the cycle, run

Write-Host ""
Write-Host "the cycle, in a repository of its own, with a fake in place of the model"

$fakeSource = Join-Path $repoRoot "scripts\tests\lib\fake-claude.ps1"
$sandboxes = New-Object System.Collections.ArrayList

function Invoke-SandboxGit {
    param([string]$Root, [string[]]$Arguments)
    $result = Invoke-TeamGit -WorkingDirectory $Root -Arguments $Arguments
    if (-not $result.Success) { throw "git $($Arguments -join ' '): $($result.StdErr)" }
    return $result.StdOut.Trim()
}

function New-Sandbox {
    param([object[]]$Tasks, $Lock = $null)
    $root = Join-Path $env:TEMP ("pagentos-team-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    [void]$sandboxes.Add($root)
    foreach ($folder in @("scripts\lib", "scripts\team", "scripts\tests\lib", ".claude\agents", "team", "src\area")) {
        [void](New-Item -ItemType Directory -Force -Path (Join-Path $root $folder))
    }
    foreach ($name in @("NativeProcess.ps1", "TeamQueue.ps1", "TeamRun.ps1")) {
        Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\lib\$name") -Destination (Join-Path $root "scripts\lib\$name")
    }
    Copy-Item -Path (Join-Path $repoRoot "scripts\team\*.ps1") -Destination (Join-Path $root "scripts\team")
    Copy-Item -LiteralPath $fakeSource -Destination (Join-Path $root "scripts\tests\lib\fake-claude.ps1")
    foreach ($role in @("lead", "researcher", "integrator", "worker", "inspector")) {
        Copy-Item -LiteralPath (Join-Path $repoRoot ".claude\agents\$role.md") -Destination (Join-Path $root ".claude\agents\$role.md")
    }
    Set-Content -LiteralPath (Join-Path $root ".gitignore") -Value ".claude/worktrees/" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $root "src\area\README.txt") -Value "the area" -Encoding ASCII
    Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document (New-Queue -Tasks $Tasks)
    $lockDocument = if ($null -ne $Lock) { $Lock } else { New-TeamLockReleased }
    Write-TeamJson -Path (Join-Path $root "team\lock.json") -Document $lockDocument
    [void](Invoke-SandboxGit -Root $root -Arguments @("init", "-q", "-b", "main"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.name", "team test"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.email", "team@example.invalid"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "core.autocrlf", "false"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("add", "-A"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-m", "the sandbox"))
    return $root
}

function Invoke-Cycle {
    param(
        [string]$Root, [string]$Scenario, [string]$CycleId = "c1", [double]$MaxUsd = 20,
        [double]$RunMinutes = 2, [int]$MaxParallel = 2, [switch]$Research, [string]$Machine = "MAIL",
        [string[]]$Brief = @(), [switch]$ResearchOnly
    )
    $log = Join-Path $Root "fake.log"
    $env:PAGENTOS_FAKE_CLAUDE_SCENARIO = $Scenario
    $env:PAGENTOS_FAKE_CLAUDE_LOG = $log
    try {
        $arguments = @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
            ("& '" + (Join-Path $Root "scripts\team\cycle.ps1") + "' -CycleId '$CycleId' -MaxUsd $($MaxUsd.ToString([System.Globalization.CultureInfo]::InvariantCulture))" +
            " -RunMinutes $($RunMinutes.ToString([System.Globalization.CultureInfo]::InvariantCulture)) -MaxParallel $MaxParallel -Machine '$Machine'" +
            " -ClaudePath '$powershell' -ClaudePrefixArguments '-NoProfile','-ExecutionPolicy','Bypass','-File','" +
            (Join-Path $Root "scripts\tests\lib\fake-claude.ps1") + "'" + $(if ($Research) { " -Research" } else { "" }) +
            $(if ($ResearchOnly) { " -ResearchOnly" } else { "" }) +
            $(if (@($Brief).Count -gt 0) { " -ResearchBrief " + ((@($Brief) | ForEach-Object { "'" + $_ + "'" }) -join ",") } else { "" }) +
            "; exit `$LASTEXITCODE")
        )
        $result = Invoke-NativeProcess -FilePath $powershell -Arguments $arguments -WorkingDirectory $Root `
            -TimeoutSeconds 300 -SuccessExitCodes @(0, 2, 3)
    }
    finally {
        Remove-Item Env:\PAGENTOS_FAKE_CLAUDE_SCENARIO -ErrorAction SilentlyContinue
        Remove-Item Env:\PAGENTOS_FAKE_CLAUDE_LOG -ErrorAction SilentlyContinue
    }
    $calls = @()
    if (Test-Path -LiteralPath $log) {
        $calls = @(Get-Content -LiteralPath $log -Encoding UTF8 | Where-Object { $_.Trim() } | ForEach-Object { ConvertFrom-Json -InputObject $_ })
    }
    return [pscustomobject]@{
        ExitCode = $result.ExitCode; StdOut = $result.StdOut; StdErr = $result.StdErr; Calls = $calls
        Queue    = (Read-TeamJson -Path (Join-Path $Root "team\queue.json"))
        Lock     = (Read-TeamJson -Path (Join-Path $Root "team\lock.json"))
        Report   = $(if (Test-Path -LiteralPath (Join-Path $Root "team\reports\$CycleId.md")) {
                [System.IO.File]::ReadAllText((Join-Path $Root "team\reports\$CycleId.md"), [System.Text.Encoding]::UTF8)
            } else { "" })
    }
}

function Get-TaskById {
    param($Queue, [string]$Id)
    return @(Get-TeamTasks -Queue $Queue | Where-Object { $_.id -eq $Id })[0]
}

try {
    Test-Case "an approved task is worked on in its own worktree, inspected, and merged into the integration branch" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $main = Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "main")
        $run = Invoke-Cycle -Root $root -Scenario "approve"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $task = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "merged" -Actual $task.state -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "team/c1/worker-task-one" -Actual $task.branch -Because "the protocol's branch name"
        Assert-True -Condition ($task.sha -match "^[0-9a-f]{40}$") -Because "a forty-hex sha: $($task.sha)"
        Assert-Equal -Expected "worker,inspector" -Actual (@($run.Calls | ForEach-Object { $_.role }) -join ",") -Because "one run each"

        $worktree = Join-Path $root ".claude\worktrees\team\c1\worker-task-one"
        foreach ($call in $run.Calls) {
            Assert-Equal -Expected $worktree.ToLowerInvariant() -Actual ([string]$call.cwd).ToLowerInvariant() -Because "the $($call.role) ran in the task's worktree, not in the main checkout"
        }
        Assert-Equal -Expected $main -Actual (Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "main")) -Because "main was not written to"
        Assert-Equal -Expected "main" -Actual (Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "--abbrev-ref", "HEAD")) -Because "the main checkout stayed on its branch"
        $merged = Invoke-SandboxGit -Root $root -Arguments @("log", "--format=%s", "main..integrate/c1")
        Assert-True -Condition ($merged -match "merge: team/c1/worker-task-one into integrate/c1") -Because $merged
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $root "src\area\task-one.txt"))) -Because "the work is on its branch, not in the main checkout"
    }

    Test-Case "the report is the protocol's, in Turkish, and the queue holds forty lines of each run" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"), (New-Task -Id "idea-one" -State "awaiting_owner" -Area @()))
        $run = Invoke-Cycle -Root $root -Scenario "approve"
        foreach ($heading in @(
                "## Hazır olanlar (sha)", "## Onay bekleyenler (fikir / yayın)",
                "## Sahibin gerçek cihazda deneyecekleri (cümle cümle, hangi makinede)",
                "## Geri verilenler ve nedeni", "## Durdurulanlar", "## Harcanan bütçe",
                "## Açık riskler", "## Protokol boşlukları")) {
            Assert-True -Condition ($run.Report.Contains($heading)) -Because "the report has '$heading'"
        }
        Assert-True -Condition ($run.Report -match "FİKİR: idea-one") -Because "what waits for the owner is named"
        Assert-True -Condition ($run.Report -match "task-one .* \[merged\] \([0-9a-f]{40}\)") -Because "what is ready carries its sha"
        Assert-True -Condition ($run.Report -match "0[.,]50 USD / tavan 20[.,]00 USD") -Because "two runs of 0.25: $($run.Report)"
        Assert-True -Condition ($run.Report.Contains('`state` alanını `approved` yapın')) -Because "how to approve, in one line"
        $task = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected 2 -Actual @($task.reports).Count -Because "the worker's and the inspector's"
        $inspection = @($task.reports)[1]
        Assert-Equal -Expected 40 -Actual @($inspection.summary).Count -Because "sixty-one lines were written; forty are kept"
        Assert-Equal -Expected "APPROVE" -Actual @($inspection.summary)[39] -Because "the verdict is kept"
        $file = Join-Path $root ($inspection.file -replace "/", "\")
        Assert-Equal -Expected 61 -Actual @(Get-Content -LiteralPath $file).Count -Because "the whole report is in its file"
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $run.Queue).Count -Because "the queue the cycle wrote keeps the protocol"
    }

    Test-Case "a task at a gate starts nobody, and the owner's state is not touched" {
        $root = New-Sandbox -Tasks @(
            (New-Task -Id "idea-one" -State "awaiting_owner"), (New-Task -Id "ship-one" -State "awaiting_release"),
            (New-Task -Id "try-one" -State "awaiting_real_evidence"), (New-Task -Id "done-one" -State "done"))
        $run = Invoke-Cycle -Root $root -Scenario "approve"
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "nothing to run"
        Assert-Equal -Expected "awaiting_owner,awaiting_release,awaiting_real_evidence,done" `
            -Actual (@(Get-TeamTasks -Queue $run.Queue | ForEach-Object { $_.state }) -join ",") -Because "no state moved"
        Assert-True -Condition ($run.Report -match "YAYIN: ship-one") -Because "the release that waits is named"
    }

    Test-Case "a proposal never reaches a worker on its own" {
        $root = New-Sandbox -Tasks @((New-Task -Id "new-idea" -State "proposed"))
        $run = Invoke-Cycle -Root $root -Scenario "approve"
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "the owner has not approved it"
        Assert-Equal -Expected "awaiting_owner" -Actual (Get-TaskById -Queue $run.Queue -Id "new-idea").state -Because "it waits at the first gate"
    }

    Test-Case "a second cycle over the same queue starts nothing and breaks nothing" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $first = Invoke-Cycle -Root $root -Scenario "approve"
        $tip = Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "integrate/c1")
        $second = Invoke-Cycle -Root $root -Scenario "approve"
        Assert-Equal -Expected 0 -Actual $second.ExitCode -Because ($second.StdOut + $second.StdErr)
        Assert-Equal -Expected @($first.Calls).Count -Actual @($second.Calls).Count -Because "no run was started again"
        Assert-Equal -Expected $tip -Actual (Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "integrate/c1")) -Because "nothing was merged twice"
    }

    Test-Case "a task a killed run left in_progress is taken up again, in the worktree it has" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -State "in_progress" -Branch "team/c1/worker-task-one"))
        [void](Invoke-SandboxGit -Root $root -Arguments @("worktree", "add", "-q", "-b", "team/c1/worker-task-one", (Join-Path $root ".claude\worktrees\team\c1\worker-task-one"), "main"))
        $run = Invoke-Cycle -Root $root -Scenario "approve"
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because ($run.StdOut + $run.StdErr)
    }

    Test-Case "returned twice, the task is stopped and the report says why" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "return"
        $task = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "stopped" -Actual $task.state -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected 2 -Actual $task.returns -Because "twice"
        Assert-Equal -Expected "worker,inspector,worker,inspector" -Actual (@($run.Calls | ForEach-Object { $_.role }) -join ",") -Because "it went back once"
        Assert-True -Condition ($run.Report -match "task-one .*: the test is missing") -Because "the inspector's reason is in the report: $($run.Report)"
        $threw = $false
        try { [void](Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "--verify", "--quiet", "refs/heads/integrate/c1")) } catch { $threw = $true }
        Assert-True -Condition $threw -Because "nothing that was not approved reached an integration branch"
    }

    Test-Case "the second worker run is handed the inspector's words" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "return"
        $workers = @($run.Calls | Where-Object { $_.role -eq "worker" })
        Assert-True -Condition ([int]$workers[1].lines -gt [int]$workers[0].lines + 30) -Because "the card grew by the inspector's summary: $($workers[0].lines) -> $($workers[1].lines)"
    }

    Test-Case "a worker that leaves its area is sent back before anyone inspects it" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "outside"
        $task = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "stopped" -Actual $task.state -Because "it left its area twice"
        Assert-True -Condition ($task.reason -match "alan dışı dosya: docs/outside.md") -Because $task.reason
        Assert-Equal -Expected 0 -Actual @($run.Calls | Where-Object { $_.role -eq "inspector" }).Count -Because "the inspector was never started"
    }

    Test-Case "a run that prints no result is a failure, and two of them stop the task" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "silent"
        $task = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "stopped" -Actual $task.state -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected 2 -Actual @($run.Calls).Count -Because "two tries"
        Assert-True -Condition ($task.reason -match "iki koşu sonuç vermedi") -Because $task.reason
    }

    Test-Case "the cycle's cap stops it: no run is started once the money is spent" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/area")), (New-Task -Id "task-two" -Area @("src/other")))
        $run = Invoke-Cycle -Root $root -Scenario "costly" -MaxUsd 6 -MaxParallel 1
        Assert-Equal -Expected 2 -Actual @($run.Calls).Count -Because "4 + 4 is over 6: the third run never started"
        Assert-True -Condition ($run.Report -match "bütçe tavanı: 8[.,]00 / 6[.,]00 USD") -Because $run.Report
        Assert-True -Condition ([double]$run.Calls[1].budget -le 2.0) -Because "the second run was given what was left, not its own cap: $($run.Calls[1].budget)"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "a stopped cycle releases the lock"
    }

    Test-Case "two tasks on two areas run side by side, each in its own worktree" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/area")), (New-Task -Id "task-two" -Area @("src/area2")))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 2
        $why = @(Get-TeamTasks -Queue $run.Queue | ForEach-Object { [string](Get-TeamProperty -InputObject $_ -Name "reason" -Default "") }) -join " | "
        Assert-Equal -Expected "merged,merged" -Actual (@(Get-TeamTasks -Queue $run.Queue | ForEach-Object { $_.state }) -join ",") -Because ($run.StdOut + $run.StdErr + $why)
        $places = @($run.Calls | Where-Object { $_.role -eq "worker" } | ForEach-Object { $_.cwd } | Sort-Object -Unique)
        Assert-Equal -Expected 2 -Actual @($places).Count -Because "two worktrees"
    }

    Test-Case "a run past its time is killed, counted as a failure, and the lock is released" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $watch = [System.Diagnostics.Stopwatch]::StartNew()
        $run = Invoke-Cycle -Root $root -Scenario "slow" -RunMinutes 0.05
        Assert-True -Condition ($watch.Elapsed.TotalSeconds -lt 240) -Because "the fake sleeps ten minutes; the cycle did not wait for it"
        $task = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "stopped" -Actual $task.state -Because ($run.StdOut + $run.StdErr)
        Assert-True -Condition (@($task.reports)[0].outcome -match "süre doldu") -Because @($task.reports)[0].outcome
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "released"
    }

    Test-Case "the other machine's lock stops the cycle before it starts anything" {
        $held = New-TeamLock -Machine "GMKADIRAKBABA" -CycleId "office" -Now ([datetime]::UtcNow.AddHours(-1))
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one")) -Lock $held
        $run = Invoke-Cycle -Root $root -Scenario "approve"
        Assert-Equal -Expected 3 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "nothing ran"
        Assert-Equal -Expected "approved" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "the queue was not written"
        Assert-Equal -Expected "GMKADIRAKBABA" -Actual $run.Lock.machine -Because "the lock is still theirs"
        Assert-True -Condition ($run.Report -match "kilit GMKADIRAKBABA makinesinde") -Because "the stop is a line in the report"
    }

    Test-Case "a stale lock is taken over, and the report says it was" {
        $held = New-TeamLock -Machine "GMKADIRAKBABA" -CycleId "office" -Now ([datetime]::UtcNow.AddHours(-7))
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one")) -Lock $held
        $run = Invoke-Cycle -Root $root -Scenario "approve"
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because ($run.StdOut + $run.StdErr)
        Assert-True -Condition ($run.Report -match "bayat kilit devralındı: GMKADIRAKBABA") -Because $run.Report
    }

    Test-Case "a queue that breaks the protocol runs nothing" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -State "assigned" -Branch "feat/hand-gestures-stage1"))
        $run = Invoke-Cycle -Root $root -Scenario "approve"
        Assert-Equal -Expected 2 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "nothing ran"
        Assert-True -Condition ($run.StdOut -match "not a team branch") -Because $run.StdOut
    }

    Test-Case "the researcher's proposals wait for the owner, and are queued once" {
        $root = New-Sandbox -Tasks @()
        $first = Invoke-Cycle -Root $root -Scenario "approve" -Research
        $tasks = @(Get-TeamTasks -Queue $first.Queue)
        Assert-Equal -Expected 1 -Actual @($tasks).Count -Because ($first.StdOut + $first.StdErr)
        Assert-Equal -Expected "awaiting_owner" -Actual $tasks[0].state -Because "an idea is the owner's to approve"
        Assert-Equal -Expected "team/proposals/2026-09-30-anlati.md" -Actual $tasks[0].proposal -Because "the proposal is named"
        Assert-Equal -Expected "researcher" -Actual (@($first.Calls | ForEach-Object { $_.role }) -join ",") -Because "and nobody was started for it"
        Assert-True -Condition ([string]$first.Calls[0].tools -notmatch "Bash|Edit") -Because "the researcher's tools are the researcher's: $($first.Calls[0].tools)"
        $second = Invoke-Cycle -Root $root -Scenario "approve" -CycleId "c2" -Research
        Assert-Equal -Expected 1 -Actual @(Get-TeamTasks -Queue $second.Queue).Count -Because "the same proposal is not queued twice"
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $second.Queue).Count -Because "the queue keeps the protocol"
    }

    Test-Case "what the lead asks the researcher to study reaches it, subject by subject" {
        $root = New-Sandbox -Tasks @()
        $asked = Invoke-Cycle -Root $root -Scenario "approve" -Research -Brief @("execution in the cloud (ADR-0213)", "the narrative: bu hafta ne oldu")
        Assert-Equal -Expected 0 -Actual $asked.ExitCode -Because ($asked.StdOut + $asked.StdErr)
        $subjects = @($asked.Calls[0].subjects)
        Assert-Equal -Expected 2 -Actual @($subjects).Count -Because "two subjects: $($subjects -join ' / ')"
        Assert-Equal -Expected "- execution in the cloud (ADR-0213)" -Actual ([string]$subjects[0]).Trim() -Because "as written"
        Assert-Equal -Expected "- the narrative: bu hafta ne oldu" -Actual ([string]$subjects[1]).Trim() -Because "as written"
        $plain = Invoke-Cycle -Root (New-Sandbox -Tasks @()) -Scenario "approve" -Research
        Assert-Equal -Expected 0 -Actual @($plain.Calls[0].subjects).Count -Because "no brief, no heading"
    }

    Test-Case "research by itself runs the researcher and touches no task, not even an approved one" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"), (New-Task -Id "new-idea" -State "proposed" -Area @()))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -ResearchOnly
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "researcher" -Actual (@($run.Calls | ForEach-Object { $_.role }) -join ",") -Because "nobody else was started"
        Assert-Equal -Expected "approved" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "the approved task was not run"
        Assert-Equal -Expected "proposed" -Actual (Get-TaskById -Queue $run.Queue -Id "new-idea").state -Because "and no state was moved"
        Assert-Equal -Expected 3 -Actual @(Get-TeamTasks -Queue $run.Queue).Count -Because "the proposal was queued"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock is released"
        Assert-True -Condition ($run.Report.Contains("## Onay bekleyenler (fikir / yayın)")) -Because "the report is written"
        $threw = $false
        try { [void](Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "--verify", "--quiet", "refs/heads/team/c1/worker-task-one")) } catch { $threw = $true }
        Assert-True -Condition $threw -Because "no branch was made for the approved task"
    }

    Test-Case "the worktree of a finished task is closed only when it is clean" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        [void](Invoke-Cycle -Root $root -Scenario "approve")
        $tree = Join-Path $root ".claude\worktrees\team\c1\worker-task-one"
        Set-Content -LiteralPath (Join-Path $tree "unsaved.txt") -Value "somebody's work" -Encoding ASCII
        $close = Join-Path $root "scripts\team\close-worktree.ps1"
        $dirty = Invoke-NativeProcess -FilePath $powershell -WorkingDirectory $root -SuccessExitCodes @(4) -Arguments @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $close, "-Branch", "team/c1/worker-task-one")
        Assert-Equal -Expected 4 -Actual $dirty.ExitCode -Because $dirty.StdOut
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $tree "unsaved.txt")) -Because "the work is still there"
        Remove-Item -LiteralPath (Join-Path $tree "unsaved.txt") -Force
        $clean = Invoke-NativeProcess -FilePath $powershell -WorkingDirectory $root -Arguments @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $close, "-Branch", "team/c1/worker-task-one")
        Assert-Equal -Expected 0 -Actual $clean.ExitCode -Because ($clean.StdOut + $clean.StdErr)
        Assert-True -Condition (-not (Test-Path -LiteralPath $tree)) -Because "the worktree is gone"
        Assert-True -Condition (Test-TeamBranch -RepoRoot $root -Branch "team/c1/worker-task-one") -Because "the branch is kept"
    }

    Test-Case "a conflict leaves the integration branch as it was and says which branch" {
        $root = New-Sandbox -Tasks @()
        $integration = Join-Path $root "scripts\team\integration-branch.ps1"
        foreach ($name in @("a", "b")) {
            [void](Invoke-SandboxGit -Root $root -Arguments @("checkout", "-q", "-b", "team/c1/worker-$name", "main"))
            Set-Content -LiteralPath (Join-Path $root "src\area\README.txt") -Value "changed by $name" -Encoding ASCII
            [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-am", "change by $name"))
        }
        [void](Invoke-SandboxGit -Root $root -Arguments @("checkout", "-q", "main"))
        $result = Invoke-NativeProcess -FilePath $powershell -WorkingDirectory $root -SuccessExitCodes @(5) -Arguments @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
            "& '$integration' -CycleId c1 -Merge 'team/c1/worker-a','team/c1/worker-b','feat/hand-gestures-stage1'; exit `$LASTEXITCODE")
        Assert-Equal -Expected 5 -Actual $result.ExitCode -Because ($result.StdOut + $result.StdErr)
        Assert-True -Condition ($result.StdOut -match "merged\s+team/c1/worker-a") -Because $result.StdOut
        Assert-True -Condition ($result.StdOut -match "CONFLICT\s+team/c1/worker-b") -Because $result.StdOut
        Assert-True -Condition ($result.StdOut -match "REFUSED\s+feat/hand-gestures-stage1") -Because $result.StdOut
        $tree = Join-Path $root ".claude\worktrees\integrate\c1"
        Assert-Equal -Expected "" -Actual (Invoke-SandboxGit -Root $tree -Arguments @("status", "--porcelain")) -Because "the failed merge was aborted"
        Assert-Equal -Expected "changed by a" -Actual (Get-Content -LiteralPath (Join-Path $tree "src\area\README.txt") -TotalCount 1) -Because "the first merge stands"
    }
}
finally {
    foreach ($root in $sandboxes) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        try { [void](Invoke-TeamGit -WorkingDirectory $root -Arguments @("worktree", "prune")) } catch { }
        for ($attempt = 0; $attempt -lt 5; $attempt++) {
            try { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction Stop; break }
            catch { Start-Sleep -Milliseconds 400 }
        }
    }
}

Write-Host ""
Write-Host "team-cycle tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
