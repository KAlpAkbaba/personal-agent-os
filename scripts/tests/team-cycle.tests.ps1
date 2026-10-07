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
param(
    # Runs only the cases whose name matches this pattern (the mutation proofs re-run a slice).
    [string]$Filter = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
# The lead-protected list the duty's grants are judged against (pm-duty-stopped).
. (Join-Path $repoRoot "scripts\lib\TeamArea.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")

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

Test-Case "one task's problem is that task's: it is set aside, once; only the queue's own problems stop everything" {
    # 2026-10-06 21:02-21:16: seven test-team cards in 'assigned' without an area, and every
    # cycle refused the WHOLE queue for over an hour - every worker seat asleep.
    $bad = New-Task -Id "test-fail-nobet-1" -State "assigned" -Area @()
    $good = New-Task -Id "task-two" -Area @("src/b")
    $queue = New-Queue -Tasks @($bad, $good)
    $found = Get-TeamQueueProblems -Queue $queue
    Assert-Equal -Expected 0 -Actual @($found.Queue).Count -Because "not a problem of the queue: $(@($found.Queue) -join '; ')"
    Assert-Equal -Expected "test-fail-nobet-1" -Actual (@($found.Tasks.Keys) -join ",") -Because "the problem is that task's"
    $aside = @(Set-TeamTasksAside -Queue $queue -Problems $found -Now ([datetime]"2026-10-06T21:02:00Z"))
    Assert-Equal -Expected "test-fail-nobet-1" -Actual (@($aside | ForEach-Object { $_.Id }) -join ",") -Because "it alone is set aside"
    Assert-Equal -Expected "stopped" -Actual $bad.state -Because "set aside = stopped"
    Assert-True -Condition ([string]$bad.reason -like "alan yok: önce dosya alanı*") -Because "the reason says what is missing: $($bad.reason)"
    Assert-True -Condition ([string]$bad.reason -match "names its file area") -Because "and the rule: $($bad.reason)"
    Assert-Equal -Expected "approved" -Actual $good.state -Because "the rest is not touched"
    Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $queue).Count -Because "the queue keeps the protocol now: $((Test-TeamQueue -Queue $queue) -join '; ')"
    Assert-Equal -Expected 0 -Actual @(Set-TeamTasksAside -Queue $queue -Problems (Get-TeamQueueProblems -Queue $queue)).Count -Because "written once: a stopped task is not set aside again"

    $branch = New-Queue -Tasks @((New-Task -Id "bad-branch" -State "stopped" -Branch "feat/hand-gestures-stage1"))
    Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $branch).Count -Because "a stopped task is held to the queue's rules only"

    foreach ($case in @(
            @{ Name = "an id used twice"; Tasks = @((New-Task -Id "same-id"), (New-Task -Id "same-id" -Area @("src/b"))); Says = "used twice" },
            @{ Name = "a task without an id"; Tasks = @((New-Task -Id "")); Says = "an id is" },
            @{ Name = "a cycle in depends_on"; Tasks = @(
                    (New-Task -Id "dep-a" -Area @("src/a") | Add-Member -NotePropertyName depends_on -NotePropertyValue @("dep-b") -PassThru),
                    (New-Task -Id "dep-b" -Area @("src/b") | Add-Member -NotePropertyName depends_on -NotePropertyValue @("dep-a") -PassThru)); Says = "a cycle in depends_on" })) {
        $whole = Get-TeamQueueProblems -Queue (New-Queue -Tasks $case.Tasks)
        Assert-True -Condition (@(@($whole.Queue) | Where-Object { $_ -match [regex]::Escape($case.Says) }).Count -ge 1) -Because "$($case.Name) is the queue's problem: $(@($whole.Queue) -join '; ')"
    }
}

Test-Case "a run in flight is the cycle's copy: -Skip keeps it from being set aside, the others are" {
    # The inspector of 11365bac (M-D): the store's copy of an in-flight task turned bad meanwhile;
    # the cycle's copy, which a run is working on, is not stopped under it.
    $flying = New-Task -Id "task-flying" -State "in_progress" -Area @()
    $idle = New-Task -Id "task-idle" -State "assigned" -Area @()
    $queue = New-Queue -Tasks @($flying, $idle)
    $aside = @(Set-TeamTasksAside -Queue $queue -Problems (Get-TeamQueueProblems -Queue $queue) -Skip @("task-flying"))
    Assert-Equal -Expected "task-idle" -Actual (@($aside | ForEach-Object { $_.Id }) -join ",") -Because "only the task not in flight is set aside"
    Assert-Equal -Expected "in_progress" -Actual $flying.state -Because "the run's copy is not stopped under it"
    Assert-Equal -Expected "" -Actual ([string](Get-TeamProperty -InputObject $flying -Name "reason" -Default "")) -Because "nor given a reason"
    Assert-Equal -Expected "stopped" -Actual $idle.state -Because "the other is"
}

Test-Case "a move into work without an area is refused, in Turkish" {
    $bare = New-Task -Id "test-fail-alarm-1" -State "approved" -Area @()
    foreach ($state in @("assigned", "in_progress", "returned")) {
        Assert-Equal -Expected "alan yok: önce dosya alanı" -Actual (Get-TeamMoveRefusal -Task $bare -State $state) -Because "into '$state' without an area"
    }
    Assert-Equal -Expected $null -Actual (Get-TeamMoveRefusal -Task $bare -State "awaiting_owner") -Because "a gate needs no area"
    Assert-Equal -Expected $null -Actual (Get-TeamMoveRefusal -Task (New-Task -Id "with-area" -Area @("src/a")) -State "assigned") -Because "with an area it may"
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
Write-Host "the lead's split of a proposal (cycle-lead-run, ADR-0214 addendum 2)"

function New-Proposal {
    param([string]$Id = "idea-one", [string]$State = "approved", [string]$Row = "row", [string[]]$Area = @())
    $task = New-Task -Id $Id -State $State -Area $Area
    $task.roadmap_row = $Row
    $task | Add-Member -NotePropertyName proposal -NotePropertyValue "team/proposals/$Id.md"
    return $task
}

function New-SplitTask {
    param([string]$Id = "part-one", [string[]]$Area = @("src/s1"), [hashtable]$Without = @{}, [hashtable]$With = @{})
    $task = [ordered]@{ id = $Id; title = "the part $Id"; roadmap_row = "row"; area = @($Area); goal = "g"; acceptance = "a"; evidence_expected = "PROVEN_AUTOMATED" }
    foreach ($name in @($Without.Keys)) { $task.Remove($name) }
    foreach ($name in @($With.Keys)) { $task[$name] = $With[$name] }
    return [pscustomobject]$task
}

function Get-SplitProblems {
    param([object[]]$Split, [object[]]$Tasks = @())
    return @(Test-TeamSplit -Split @($Split) -Queue (New-Queue -Tasks $Tasks))
}

Test-Case "split: a proposal is split when it is approved, or proposed WITH a roadmap row, and has no area yet" {
    $ids = { param($tasks) (@(Get-TeamSplitCandidates -Queue (New-Queue -Tasks $tasks) | ForEach-Object { $_.id }) -join ",") }
    Assert-Equal -Expected "idea-one" -Actual (& $ids @((New-Proposal))) -Because "approved, no area"
    Assert-Equal -Expected "idea-one" -Actual (& $ids @((New-Proposal -State "proposed"))) -Because "proposed, with a roadmap row"
    Assert-Equal -Expected "" -Actual (& $ids @((New-Proposal -State "proposed" -Row ""))) -Because "proposed without a roadmap row waits for the owner"
    Assert-Equal -Expected "" -Actual (& $ids @((New-Proposal -State "proposed" -Row "   "))) -Because "a blank row is no row"
    Assert-Equal -Expected "" -Actual (& $ids @((New-Proposal -State "awaiting_owner"))) -Because "the owner's gate is not crossed"
    Assert-Equal -Expected "" -Actual (& $ids @((New-Proposal -Area @("src/x")))) -Because "it has its area already"
    Assert-Equal -Expected "" -Actual (& $ids @((New-Proposal -State "done"))) -Because "a split one is over"
    Assert-Equal -Expected "" -Actual (& $ids @((New-Task -Id "no-proposal" -Area @()))) -Because "a task that is not a proposal is not split"
}

Test-Case "split: a proposal that awaits its split is neither run nor moved past the owner's gate" {
    Assert-Equal -Expected "rest" -Actual (Get-TeamNextRole -Task (New-Proposal)).Kind -Because "approved, no area: it waits for the split, never a worker without an area"
    Assert-Equal -Expected "rest" -Actual (Get-TeamNextRole -Task (New-Proposal -State "proposed")).Kind -Because "proposed with a row waits for the split"
    $held = Get-TeamNextRole -Task (New-Proposal -State "proposed" -Row "")
    Assert-Equal -Expected "move" -Actual $held.Kind -Because "proposed without a row goes to the owner, as before"
    Assert-Equal -Expected "awaiting_owner" -Actual $held.NextState -Because "the first gate"
    Assert-Equal -Expected "move" -Actual (Get-TeamNextRole -Task (New-Proposal -Area @("src/x"))).Kind -Because "an approved proposal that has an area goes on to a worker"
}

Test-Case "split: a good split is accepted, and each task becomes an approved, queue-valid task" {
    $split = @((New-SplitTask -Id "part-one" -Area @("src/s1")), (New-SplitTask -Id "part-two" -Area @("src/s2")))
    Assert-Equal -Expected 0 -Actual @(Get-SplitProblems -Split $split).Count -Because ((Get-SplitProblems -Split $split) -join "; ")
    $proposal = New-Proposal
    $made = @(ConvertTo-TeamSplitTasks -Split $split -Proposal $proposal -MaxUsd 0)
    Assert-Equal -Expected "approved,approved" -Actual (@($made | ForEach-Object { $_.state }) -join ",") -Because "approved in advance"
    Assert-Equal -Expected "team/proposals/idea-one.md" -Actual $made[0].proposal -Because "traced to its proposal"
    Assert-Equal -Expected "" -Actual $made[0].branch -Because "the cycle names the branch, not the lead"
    Assert-Equal -Expected "" -Actual (@(Test-TeamQueue -Queue (New-Queue -Tasks $made)) -join "; ") -Because "the protocol holds"
}

Test-Case "split: a task missing a field is rejected, for each field; a full one is not" {
    foreach ($field in @("id", "title", "roadmap_row", "area", "goal", "acceptance", "evidence_expected")) {
        $problems = Get-SplitProblems -Split @((New-SplitTask -Without @{ $field = 1 }))
        Assert-True -Condition (@($problems | Where-Object { $_ -match "'$field'" }).Count -ge 1) -Because "a split without '$field': $($problems -join '; ')"
    }
    $blank = Get-SplitProblems -Split @((New-SplitTask -With @{ acceptance = "  " }))
    Assert-True -Condition (@($blank | Where-Object { $_ -match "'acceptance'" }).Count -ge 1) -Because "a blank field is a missing one"
    Assert-Equal -Expected 0 -Actual @(Get-SplitProblems -Split @((New-SplitTask))).Count -Because "a full task"
    foreach ($bad in @($null, @(), "text", 5)) {
        Assert-True -Condition (@(Test-TeamSplit -Split $bad -Queue (New-Queue)).Count -ge 1) -Because "not a list of tasks: <$bad>"
    }
}

Test-Case "split: an area inside another task's in work is rejected; a neighbour, a gate and a finished task are not" {
    $busy = New-Task -Id "busy-one" -State "assigned" -Area @("src/busy")
    $hit = Get-SplitProblems -Split @((New-SplitTask -Area @("src/busy/deep"))) -Tasks @($busy)
    Assert-True -Condition (@($hit | Where-Object { $_ -match "overlaps the area of busy-one" }).Count -eq 1) -Because ($hit -join "; ")
    Assert-True -Condition (@(Get-SplitProblems -Split @((New-SplitTask -Area @("src"))) -Tasks @($busy)).Count -ge 1) -Because "a parent directory of the area"
    Assert-True -Condition (@(Get-SplitProblems -Split @((New-SplitTask -Area @("src/busy/"))) -Tasks @($busy)).Count -ge 1) -Because "the same directory with a slash"
    Assert-True -Condition (@(Get-SplitProblems -Split @((New-SplitTask -Area @("src/elsewhere", "SRC/BUSY/x"))) -Tasks @($busy)).Count -ge 1) -Because "any one of several areas, in any letter case"
    foreach ($state in @("approved", "in_progress", "inspecting", "returned")) {
        $other = New-Task -Id "busy-two" -State $state -Area @("src/busy")
        Assert-True -Condition (@(Get-SplitProblems -Split @((New-SplitTask -Area @("src/busy"))) -Tasks @($other)).Count -ge 1) -Because "a task at '$state' is in work"
    }
    Assert-Equal -Expected 0 -Actual @(Get-SplitProblems -Split @((New-SplitTask -Area @("src/busy2"))) -Tasks @($busy)).Count -Because "'busy' and 'busy2' are two directories"
    foreach ($state in @("awaiting_owner", "awaiting_release", "merged", "done", "stopped")) {
        $rest = New-Task -Id "rest-one" -State $state -Area @("src/busy")
        Assert-Equal -Expected 0 -Actual @(Get-SplitProblems -Split @((New-SplitTask -Area @("src/busy"))) -Tasks @($rest)).Count -Because "a task at '$state' is not in work"
    }
}

Test-Case "split: two tasks of one split that share an area are rejected unless one waits for the other" {
    $clash = @((New-SplitTask -Id "part-one" -Area @("src/s1")), (New-SplitTask -Id "part-two" -Area @("src/s1/inner")))
    Assert-True -Condition (@(Get-SplitProblems -Split $clash | Where-Object { $_ -match "overlaps the area of part-one" }).Count -eq 1) -Because "they would run side by side"
    $ordered = @((New-SplitTask -Id "part-one" -Area @("src/s1")), (New-SplitTask -Id "part-two" -Area @("src/s1/inner") -With @{ depends_on = @("part-one") }))
    Assert-Equal -Expected 0 -Actual @(Get-SplitProblems -Split $ordered).Count -Because "one after the other is not a clash"
}

Test-Case "split: a shared file, or an area that holds one, is rejected; its neighbours are not" {
    foreach ($shared in @("docs/HANDOFF.md", "docs/DECISIONS.md", "state/BUILD_STATE.json", "docs/THIRD_PARTY_COMPONENTS.md", "team/queue.json", "docs", "docs/*", "state", ".", "*")) {
        $problems = Get-SplitProblems -Split @((New-SplitTask -Area @("src/s1", $shared)))
        Assert-True -Condition (@($problems | Where-Object { $_ -match "shared file" }).Count -ge 1) -Because "'$shared': $($problems -join '; ')"
    }
    foreach ($fine in @("docs/guides", "docs/HANDOFF.md.bak", "state/other.json", "docs2", "team/plans/x-adr.md", "scripts/team/cycle.ps1")) {
        Assert-Equal -Expected 0 -Actual @(Get-SplitProblems -Split @((New-SplitTask -Area @($fine))) | Where-Object { $_ -match "shared file" }).Count -Because "'$fine' is not a shared file"
    }
}

Test-Case "split: an area that leaves the repository is rejected" {
    foreach ($bad in @("../elsewhere", "C:/Windows", "/etc", "\\share\x", "a/../../b")) {
        Assert-True -Condition (@(Get-SplitProblems -Split @((New-SplitTask -Area @($bad))) | Where-Object { $_ -match "inside the repository" }).Count -ge 1) -Because "'$bad'"
    }
    Assert-Equal -Expected 0 -Actual @(Get-SplitProblems -Split @((New-SplitTask -Area @("services/api/app/team", "apps/web/src/*")))).Count -Because "paths inside the repository, with a trailing glob"
}

Test-Case "split: a task that names more than 25 files is rejected; 25 is the limit" {
    $twentyFive = @(1..25 | ForEach-Object { "src/many/file$_.py" })
    Assert-Equal -Expected 0 -Actual @(Get-SplitProblems -Split @((New-SplitTask -Area $twentyFive))).Count -Because "25 is allowed"
    $tooMany = @(1..26 | ForEach-Object { "src/many/file$_.py" })
    Assert-True -Condition (@(Get-SplitProblems -Split @((New-SplitTask -Area $tooMany)) | Where-Object { $_ -match "25" }).Count -ge 1) -Because "26 is not"
}

Test-Case "split: main and the hand-gestures branch are never named; a task of its own name is fine" {
    foreach ($branch in @("main", "feat/hand-gestures-stage1", "Main", "release/hand-gestures")) {
        $problems = Get-SplitProblems -Split @((New-SplitTask -With @{ branch = $branch }))
        Assert-True -Condition (@($problems | Where-Object { $_ -match "not a team branch" }).Count -ge 1) -Because "'$branch': $($problems -join '; ')"
    }
    $area = Get-SplitProblems -Split @((New-SplitTask -Area @("src/hand-gestures")))
    Assert-True -Condition (@($area | Where-Object { $_ -match "hand-gestures" }).Count -ge 1) -Because "an area of the frozen work: $($area -join '; ')"
    Assert-Equal -Expected 0 -Actual @(Get-SplitProblems -Split @((New-SplitTask -With @{ branch = "" }))).Count -Because "no branch named is the normal case"
    Assert-Equal -Expected 0 -Actual @(Get-SplitProblems -Split @((New-SplitTask -Id "gestures-notes" -Area @("src/gestures"))) ).Count -Because "'gestures' alone is not the frozen branch"
}

Test-Case "split: an id that is taken, used twice, or malformed, and a dependency that does not exist, are rejected" {
    $taken = Get-SplitProblems -Split @((New-SplitTask -Id "busy-one")) -Tasks @((New-Task -Id "busy-one" -State "done"))
    Assert-True -Condition (@($taken | Where-Object { $_ -match "already in the queue" }).Count -eq 1) -Because ($taken -join "; ")
    $twice = Get-SplitProblems -Split @((New-SplitTask -Id "part-one" -Area @("src/a")), (New-SplitTask -Id "part-one" -Area @("src/b")))
    Assert-True -Condition (@($twice | Where-Object { $_ -match "used twice" }).Count -eq 1) -Because ($twice -join "; ")
    Assert-True -Condition (@(Get-SplitProblems -Split @((New-SplitTask -Id "Bad Id"))).Count -ge 1) -Because "an id is a-z, 0-9 and '-'"
    $orphan = Get-SplitProblems -Split @((New-SplitTask -With @{ depends_on = @("nobody") }))
    Assert-True -Condition (@($orphan | Where-Object { $_ -match "depends on 'nobody'" }).Count -eq 1) -Because ($orphan -join "; ")
    $known = Get-SplitProblems -Split @((New-SplitTask -With @{ depends_on = @("busy-one") })) -Tasks @((New-Task -Id "busy-one" -State "done"))
    Assert-Equal -Expected 0 -Actual @($known).Count -Because "a dependency that is in the queue"
}

Test-Case "split: a split file is read as a list, as an object holding one, or refused with the reason" {
    $work = Join-Path $env:TEMP ("pagentos-split-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    [void](New-Item -ItemType Directory -Force -Path $work)
    try {
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        $task = ConvertTo-Json -InputObject (New-SplitTask) -Compress
        $cases = @(
            @{ Name = "list.json"; Text = "[$task,$task]"; Ok = $true; Count = 2 },
            @{ Name = "one.json"; Text = "[$task]"; Ok = $true; Count = 1 },
            @{ Name = "object.json"; Text = "{`"tasks`":[$task]}"; Ok = $true; Count = 1 },
            @{ Name = "broken.json"; Text = "[{`"id`":"; Ok = $false; Count = 0 },
            @{ Name = "empty.json"; Text = ""; Ok = $false; Count = 0 })
        foreach ($case in $cases) {
            [System.IO.File]::WriteAllText((Join-Path $work $case.Name), $case.Text, $utf8)
            $read = Read-TeamSplitFile -Path (Join-Path $work $case.Name)
            Assert-Equal -Expected $case.Ok -Actual ([bool]$read.Ok) -Because "$($case.Name): $($read.Why)"
            Assert-Equal -Expected $case.Count -Actual @($read.Split).Count -Because "$($case.Name): the tasks"
        }
        $missing = Read-TeamSplitFile -Path (Join-Path $work "nothing.json")
        Assert-True -Condition ((-not $missing.Ok) -and $missing.Why) -Because "the lead wrote no file: '$($missing.Why)'"
    }
    finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "split: the lead's run holds Read and Write and neither Bash nor Edit, and the ordinary lead keeps its tools" {
    $file = Join-Path $repoRoot ".claude\agents\lead.md"
    $plain = @(Get-TeamRoleTools -RoleFile $file)
    Assert-True -Condition ($plain -contains "Bash" -and $plain -contains "Edit") -Because "the role file grants them (the excluding is the cycle's)"
    $arguments = @(Get-TeamRunArguments -RoleFile $file -ExcludeTools @("Bash", "Edit"))
    $tools = @($arguments[[array]::IndexOf($arguments, "--allowedTools") + 1].Split(","))
    Assert-True -Condition ($tools -contains "Read" -and $tools -contains "Write") -Because "read and write the one file: $($tools -join ',')"
    Assert-True -Condition ($tools -notcontains "Bash" -and $tools -notcontains "Edit") -Because "no shell and no edit: $($tools -join ',')"
    Assert-Equal -Expected "Bash,Edit" -Actual $arguments[[array]::IndexOf($arguments, "--disallowedTools") + 1] -Because "denied by name too"
    $open = @(Get-TeamRunArguments -RoleFile $file)
    Assert-True -Condition ($open -notcontains "--disallowedTools") -Because "without an exclusion nothing is denied"
    Assert-True -Condition ($open[[array]::IndexOf($open, "--allowedTools") + 1] -match "Bash") -Because "the default run is as it was"
}

Write-Host ""
Write-Host "the Proje Yöneticisi's duty for stopped tasks (pm-duty-stopped)"

function New-Stopped {
    param([string]$Id = "stuck-one", [string]$Reason = "ayni is iki kez geri verildi", [string[]]$Area = @("src/area"), [string]$Updated = "2026-10-03T10:00:00Z")
    $task = New-Task -Id $Id -State "stopped" -Area $Area
    $task.updated_at = $Updated
    $task | Add-Member -NotePropertyName reason -NotePropertyValue $Reason
    $task | Add-Member -NotePropertyName returns -NotePropertyValue 2
    return $task
}

function New-Decision {
    param([string]$Task = "stuck-one", [string]$Action = "return", $Grant = $null, [string]$Reason = "Testi ekle")
    $decision = [ordered]@{ task = $Task; action = $Action; reason = $Reason }
    if ($null -ne $Grant) { $decision["grant"] = $Grant }
    return $decision
}

function ConvertTo-DutyJson {
    param([object[]]$Decisions)
    return (ConvertTo-Json -InputObject ([ordered]@{ decisions = @($Decisions) }) -Depth 6 -Compress)
}

function Get-DutyProblems {
    <# The decisions as the run writes them (JSON, read back), judged against the listed tasks. #>
    param([object[]]$Decisions, [string[]]$Listed = @("stuck-one"), [object[]]$Tasks = @((New-Stopped)))
    $document = ConvertFrom-Json -InputObject (ConvertTo-DutyJson -Decisions $Decisions)
    return @(Test-TeamDuty -Decisions @($document.decisions) -Listed $Listed -Queue (New-Queue -Tasks $Tasks))
}

Test-Case "progress: a run's progress is measured from its worktree - a folder entry counts once, the working tree's changes count, an ADR draft is seen" {
    # The owner, 2026-10-05: "tikladigimda ajanlarin calistiklari kisimda kodun yuzde kacini yazdigi".
    $m = Measure-TeamAreaProgress -Area @("services/api/app/x.py", "apps/web/app/y/", "docs/z.md", "scripts\tests\a.tests.ps1") -Changed @("services/api/app/x.py", "apps/web/app/y/one.tsx", "apps/web/app/y/two.tsx", "other/file.py")
    Assert-Equal -Expected "4|2|False" -Actual ("{0}|{1}|{2}" -f $m.AreaTotal, $m.AreaTouched, $m.TestsChanged) -Because "two of four: a file and a folder (once, for two files inside it); no test changed"
    $t = Measure-TeamAreaProgress -Area @("scripts/tests/a.tests.ps1") -Changed @("scripts\tests\a.tests.ps1")
    Assert-Equal -Expected "1|1|True" -Actual ("{0}|{1}|{2}" -f $t.AreaTotal, $t.AreaTouched, $t.TestsChanged) -Because "backslashes and a test file"
    $none = Measure-TeamAreaProgress -Area @("a.py") -Changed @()
    Assert-Equal -Expected 0 -Actual $none.AreaTouched -Because "nothing changed"

    $repo = Join-Path ([System.IO.Path]::GetTempPath()) ("progress-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    try {
        [void](New-Item -ItemType Directory -Force -Path $repo)
        foreach ($a in @(@("init", "-q", "-b", "main"), @("config", "user.email", "t@example.com"), @("config", "user.name", "t"))) { [void](Invoke-TeamGit -WorkingDirectory $repo -Arguments $a) }
        [System.IO.File]::WriteAllText((Join-Path $repo "base.txt"), "x")
        [void](Invoke-TeamGit -WorkingDirectory $repo -Arguments @("add", "."))
        [void](Invoke-TeamGit -WorkingDirectory $repo -Arguments @("commit", "-q", "-m", "base"))
        [void](Invoke-TeamGit -WorkingDirectory $repo -Arguments @("checkout", "-q", "-b", "work"))
        [void](New-Item -ItemType Directory -Force -Path (Join-Path $repo "src"))
        [System.IO.File]::WriteAllText((Join-Path $repo "src\one.py"), "1")
        [void](Invoke-TeamGit -WorkingDirectory $repo -Arguments @("add", "."))
        [void](Invoke-TeamGit -WorkingDirectory $repo -Arguments @("commit", "-q", "-m", "one"))
        [System.IO.File]::WriteAllText((Join-Path $repo "src\two.py"), "2")   # not committed: still counts
        [void](New-Item -ItemType Directory -Force -Path (Join-Path $repo "team\plans"))
        [System.IO.File]::WriteAllText((Join-Path $repo "team\plans\t-one-adr.md"), "# adr")
        $p = Get-TeamRunProgress -Worktree $repo -Base "main" -Area @("src/one.py", "src/two.py", "src/three.py", "team/plans/t-one-adr.md") -TaskId "t-one"
        Assert-True -Condition ($null -ne $p) -Because "a readable worktree has a progress"
        Assert-Equal -Expected "4|3|1|True|False" -Actual ("{0}|{1}|{2}|{3}|{4}" -f $p.area_total, $p.area_touched, $p.commits, $p.adr_draft, $p.tests_changed) -Because "one committed, one uncommitted, the ADR; three of four"
        Assert-True -Condition ([string]$p.last_change_at -match '^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$') -Because "a stamp: $($p.last_change_at)"
        Assert-True -Condition ($null -eq (Get-TeamRunProgress -Worktree (Join-Path $repo "nope") -Base "main" -Area @("a"))) -Because "no worktree: no progress, no throw"
    }
    finally { Remove-Item -LiteralPath $repo -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "duty: the stopped tasks are handed once per stop; an escalated one, one set aside and a task in any other state are not" {
    $ids = { param($tasks, $handed, $skip) (@(Get-TeamDutyCandidates -Queue (New-Queue -Tasks $tasks) -Handed $handed -Skip $skip | ForEach-Object { $_.id }) -join ",") }
    $tasks = @((New-Stopped), (New-Stopped -Id "stuck-two" -Reason "Danışman'a iletildi: güvenlik kararı" -Area @("src/b")),
        (New-Task -Id "busy-one" -State "assigned" -Area @("src/c")), (New-Task -Id "done-one" -State "done" -Area @("src/d")))
    Assert-Equal -Expected "stuck-one" -Actual (& $ids $tasks @{} @{}) -Because "the stopped task; not the escalated one, not one in work, not a finished one"
    Assert-Equal -Expected "" -Actual (& $ids $tasks @{ "stuck-one" = "2026-10-03T10:00:00Z" } @{}) -Because "handed at this stop already"
    Assert-Equal -Expected "stuck-one" -Actual (& $ids $tasks @{ "stuck-one" = "2026-10-03T09:00:00Z" } @{}) -Because "stopped AGAIN since it was handed (a new updated_at)"
    Assert-Equal -Expected "" -Actual (& $ids $tasks @{} @{ "stuck-one" = $true }) -Because "a task the cycle set aside"
    Assert-Equal -Expected "" -Actual (& $ids @((New-Task -Id "task-one"))  @{} @{}) -Because "no stopped task, nothing to hand"
    $many = @(1..10 | ForEach-Object { New-Stopped -Id "stuck-$_" -Area @("src/m$_") })
    Assert-Equal -Expected 8 -Actual @(Get-TeamDutyCandidates -Queue (New-Queue -Tasks $many) -Handed @{} -Skip @{}).Count -Because "at most eight a run; the rest are handed when it ends"
}

Test-Case "duty: a sound decision file passes, for each of the three actions" {
    $two = @((New-Stopped), (New-Stopped -Id "stuck-two" -Area @("src/b")), (New-Stopped -Id "stuck-three" -Area @("src/c")))
    $sound = @((New-Decision), (New-Decision -Task "stuck-two" -Action "grant_and_return" -Grant @("docs/extra.md", "services/api/app/x.py")), (New-Decision -Task "stuck-three" -Action "escalate" -Reason "Güvenlik kararı: Danışman"))
    $problems = @(Get-DutyProblems -Decisions $sound -Listed @("stuck-one", "stuck-two", "stuck-three") -Tasks $two)
    Assert-Equal -Expected 0 -Actual @($problems).Count -Because ($problems -join "; ")
    Assert-Equal -Expected 0 -Actual @(Get-DutyProblems -Decisions @((New-Decision -Action "grant_and_return" -Grant @("docs/one.md")))).Count -Because "one grant, written as a one-entry list"
    # The first real duty run (2026-10-04 15:13) wrote a sound 1341-character instruction that
    # changed the approach on a fourth return; a 1200 bound refused the WHOLE file, both decisions.
    Assert-Equal -Expected 0 -Actual @(Get-DutyProblems -Decisions @((New-Decision -Reason ("ç" * 1341)))).Count -Because "a long, real instruction is a decision, not a broken file"
}

$dutyBroken = @(
    @{ Name = "a task that was not handed"; Decision = { New-Decision -Task "busy-one" }; Says = "not one of the stopped tasks" },
    @{ Name = "an action that is not one"; Decision = { New-Decision -Action "Return" }; Says = "is not an action" },
    @{ Name = "an action of another kind"; Decision = { New-Decision -Action "merge" }; Says = "is not an action" },
    @{ Name = "an empty reason"; Decision = { New-Decision -Reason "" }; Says = "reason" },
    @{ Name = "a blank reason"; Decision = { New-Decision -Reason "   " }; Says = "reason" },
    @{ Name = "a reason over 4000 characters"; Decision = { New-Decision -Reason ("x" * 4001) }; Says = "4000" },
    @{ Name = "a grant with a plain return"; Decision = { New-Decision -Grant @("docs/extra.md") }; Says = "only with grant_and_return" },
    @{ Name = "a grant with an escalation"; Decision = { New-Decision -Action "escalate" -Grant @("docs/extra.md") }; Says = "only with grant_and_return" },
    @{ Name = "grant_and_return without a grant"; Decision = { New-Decision -Action "grant_and_return" }; Says = "1 to 5" },
    @{ Name = "grant_and_return with an empty grant"; Decision = { New-Decision -Action "grant_and_return" -Grant @() }; Says = "1 to 5" },
    @{ Name = "six grants"; Decision = { New-Decision -Action "grant_and_return" -Grant @(1..6 | ForEach-Object { "src/g$_.py" }) }; Says = "1 to 5" },
    @{ Name = "a grant that is a string, not a list"; Decision = { New-Decision -Action "grant_and_return" -Grant "docs/extra.md" }; Says = "list" },
    @{ Name = "a grant that climbs out"; Decision = { New-Decision -Action "grant_and_return" -Grant @("../elsewhere.md") }; Says = "plain repository-relative path" },
    @{ Name = "a grant with a drive"; Decision = { New-Decision -Action "grant_and_return" -Grant @("C:/Windows/x.txt") }; Says = "plain repository-relative path" },
    @{ Name = "a grant with a leading slash"; Decision = { New-Decision -Action "grant_and_return" -Grant @("/etc/passwd") }; Says = "plain repository-relative path" },
    @{ Name = "a grant spelt around the rule"; Decision = { New-Decision -Action "grant_and_return" -Grant @("docs//HANDOFF.md") }; Says = "plain repository-relative path" },
    @{ Name = "a grant that is not text"; Decision = { New-Decision -Action "grant_and_return" -Grant @(5) }; Says = "plain repository-relative path" }
)
foreach ($case in $dutyBroken) {
    Test-Case "duty: a decision file with $($case.Name) is refused, with the reason" {
        $problems = @(Get-DutyProblems -Decisions @((& $case.Decision)) -Tasks @((New-Stopped), (New-Task -Id "busy-one" -State "assigned" -Area @("src/c"))))
        Assert-True -Condition (@($problems | Where-Object { $_ -match [regex]::Escape($case.Says) }).Count -ge 1) -Because "'$($case.Says)' in: $($problems -join '; ')"
    }
}

Test-Case "duty: two decisions for one task, a decision that is no object, and a file with no decision are refused" {
    $twice = @(Get-DutyProblems -Decisions @((New-Decision), (New-Decision -Action "escalate")))
    Assert-True -Condition (@($twice | Where-Object { $_ -match "more than one decision" }).Count -eq 1) -Because ($twice -join "; ")
    Assert-True -Condition (@(Test-TeamDuty -Decisions @("return") -Listed @("stuck-one") -Queue (New-Queue -Tasks @((New-Stopped)))).Count -ge 1) -Because "a string is not a decision"
    Assert-True -Condition (@(Test-TeamDuty -Decisions @() -Listed @("stuck-one") -Queue (New-Queue -Tasks @((New-Stopped)))).Count -ge 1) -Because "no decision at all"
}

Test-Case "duty: a grant of a lead-protected path refuses the decision - a shared file, a role file, the protected list itself, a secret, a recovery root, a directory holding one" {
    foreach ($protected in @("docs/HANDOFF.md", "docs/DECISIONS.md", "state/BUILD_STATE.json", "team/queue.json", ".claude/agents/worker.md",
            "scripts/lib/TeamArea.ps1", "PROJECT_CONSTITUTION.md", "docs/ROADMAP.md", "docs/TEAM_PROTOCOL.md", "apps/web/.env.local",
            "services/recovery-supervisor/app.py", "scripts/cloud/release-cloud-core.ps1", "docs", "CLAUDE.md")) {
        $problems = @(Get-DutyProblems -Decisions @((New-Decision -Action "grant_and_return" -Grant @("src/fine.py", $protected))))
        Assert-True -Condition (@($problems | Where-Object { $_ -match "protected" }).Count -ge 1) -Because "'$protected': $($problems -join '; ')"
    }
    foreach ($fine in @("docs/guides/x.md", "scripts/team/cycle.ps1", "scripts/lib/TeamQueue.ps1", "services/api/app/team/routes.py", "docs/HANDOFF.md.bak")) {
        $problems = @(Get-DutyProblems -Decisions @((New-Decision -Action "grant_and_return" -Grant @($fine))))
        Assert-Equal -Expected 0 -Actual @($problems).Count -Because "'$fine' is not protected: $($problems -join '; ')"
    }
}

Test-Case "duty: a grant that takes the area past 25 entries is refused; 25 is the limit" {
    $wide = New-Stopped -Area @(1..23 | ForEach-Object { "src/many/file$_.py" })
    Assert-Equal -Expected 0 -Actual @(Get-DutyProblems -Decisions @((New-Decision -Action "grant_and_return" -Grant @("src/x1.py", "src/x2.py"))) -Tasks @($wide)).Count -Because "23 + 2 = 25"
    $over = @(Get-DutyProblems -Decisions @((New-Decision -Action "grant_and_return" -Grant @("src/x1.py", "src/x2.py", "src/x3.py"))) -Tasks @($wide))
    Assert-True -Condition (@($over | Where-Object { $_ -match "25" }).Count -ge 1) -Because "23 + 3 = 26: $($over -join '; ')"
}

Test-Case "duty: a decision file is read as an object holding its decisions, or refused with the reason" {
    $work = Join-Path $env:TEMP ("pagentos-duty-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    [void](New-Item -ItemType Directory -Force -Path $work)
    try {
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        $one = ConvertTo-Json -InputObject (New-Decision) -Compress
        $cases = @(
            @{ Name = "object.json"; Text = "{`"decisions`":[$one,$one]}"; Ok = $true; Count = 2 },
            @{ Name = "single.json"; Text = "{`"decisions`":[$one]}"; Ok = $true; Count = 1 },
            @{ Name = "list.json"; Text = "[$one]"; Ok = $false; Count = 0 },
            @{ Name = "nodecisions.json"; Text = "{`"other`":1}"; Ok = $false; Count = 0 },
            @{ Name = "broken.json"; Text = "{`"decisions`":["; Ok = $false; Count = 0 },
            @{ Name = "empty.json"; Text = ""; Ok = $false; Count = 0 })
        foreach ($case in $cases) {
            [System.IO.File]::WriteAllText((Join-Path $work $case.Name), $case.Text, $utf8)
            $read = Read-TeamDutyFile -Path (Join-Path $work $case.Name)
            Assert-Equal -Expected $case.Ok -Actual ([bool]$read.Ok) -Because "$($case.Name): $($read.Why)"
            Assert-Equal -Expected $case.Count -Actual @($read.Decisions).Count -Because "$($case.Name): the decisions"
            if (-not $case.Ok) { Assert-True -Condition ([bool]$read.Why) -Because "$($case.Name): a refusal says why" }
        }
        $missing = Read-TeamDutyFile -Path (Join-Path $work "nothing.json")
        Assert-True -Condition ((-not $missing.Ok) -and $missing.Why) -Because "the run wrote no file: '$($missing.Why)'"
    }
    finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "duty: the card lists each stopped task - its area, branch, sha, the stop reason, its last reports - and the one file to write" {
    $task = New-Stopped -Reason "alan dışı dosya: docs/outside.md"
    $task.branch = "team/c0/worker-stuck-one"
    $task | Add-Member -NotePropertyName sha -NotePropertyValue ("a" * 40)
    $task | Add-Member -NotePropertyName depends_on -NotePropertyValue @("base-one")
    $task.reports = @(
        [pscustomobject]@{ cycle = "c0"; role = "worker"; file = "team/reports/c0/stuck-one-worker-1.md"; outcome = "tamam"; summary = @() },
        [pscustomobject]@{ cycle = "c0"; role = "inspector"; file = "team/reports/c0/stuck-one-inspector-1.md"; outcome = "tamam"; summary = @() })
    $other = New-Stopped -Id "stuck-two" -Reason "iki koşu sonuç vermedi" -Area @("src/b")
    $card = New-TeamDutyCard -Tasks @($task, $other) -CycleId "c1" -DutyFile "team/plans/c1-duty-1.json"
    foreach ($expected in @("- duty_file: team/plans/c1-duty-1.json", "### stuck-one", "### stuck-two", "alan dışı dosya: docs/outside.md", "iki koşu sonuç vermedi",
            "team/reports/c0/stuck-one-inspector-1.md", "team/reports/c0/stuck-one-worker-1.md", "team/c0/worker-stuck-one", ("a" * 40), "base-one", "src/area",
            "grant_and_return", "escalate", "Nöbet: duran işler")) {
        Assert-True -Condition ($card.Contains($expected)) -Because "the card names '$expected':`n$card"
    }
    Assert-True -Condition ($card -notmatch '(?m)^- id: ') -Because "no line reads as ONE task's id (the run is about several)"
}

Test-Case "duty: without the protected-path list loaded no decision file is accepted (it fails closed)" {
    $work = Join-Path $env:TEMP ("pagentos-duty-closed-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    [void](New-Item -ItemType Directory -Force -Path $work)
    try {
        $probe = Join-Path $work "probe.ps1"
        $text = "Set-StrictMode -Version Latest`r`n. '" + (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1") + "'`r`n" +
        "`$task = [pscustomobject]@{ id = 'stuck-one'; title = 't'; roadmap_row = 'r'; state = 'stopped'; area = @('src/area'); branch = ''; worktree = ''; assignee = ''; reports = @(); budget = [pscustomobject]@{ max_usd = 0 }; created_at = '2026-10-03T10:00:00Z'; updated_at = '2026-10-03T10:00:00Z' }`r`n" +
        "`$decision = [pscustomobject]@{ task = 'stuck-one'; action = 'return'; reason = 'x' }`r`n" +
        "`$why = @(Test-TeamDuty -Decisions @(`$decision) -Listed @('stuck-one') -Queue ([pscustomobject]@{ version = 1; tasks = @(`$task) }))`r`n" +
        "Write-Output ('COUNT=' + @(`$why).Count)`r`n"
        [System.IO.File]::WriteAllText($probe, $text, (New-Object System.Text.UTF8Encoding($true)))
        $shell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
        $result = Invoke-NativeProcess -FilePath $shell -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $probe) -WorkingDirectory $work -TimeoutSeconds 120 -SuccessExitCodes @(0, 1)
        Assert-True -Condition ($result.StdOut -match 'COUNT=([1-9]\d*)') -Because "a refusal, not an empty list: $($result.StdOut) $($result.StdErr)"
    }
    finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
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
    @{ Text = "**Verdict:** ``RETURN (1: add a test; 2: a heartbeat test)``"; Verdict = "RETURN"; Detail = "1: add a test; 2: a heartbeat test" },
    @{ Text = "Verdict: APPROVE"; Verdict = "APPROVE"; Detail = "" },
    @{ Text = "**Verdict: APPROVE**"; Verdict = "APPROVE"; Detail = "" },
    @{ Text = "The verdict could be APPROVE"; Verdict = "NONE"; Detail = "" },
    # 2026-10-02: an inspector that had left a command running was woken after its report, and
    # its LAST message - all the cycle reads - was one sentence. An approved task was stopped.
    @{ Text = "That notification is only the last waiter loop exiting; the report above stands unchanged and the verdict remains ``APPROVE``."; Verdict = "APPROVE"; Detail = "" },
    @{ Text = "Nothing new. The verdict is ``RETURN (1: add the test; 2: fix the ADR)``."; Verdict = "RETURN"; Detail = "1: add the test; 2: fix the ADR" },
    @{ Text = "The verdict stands: ``REJECT (it writes outside its area)``"; Verdict = "REJECT"; Detail = "it writes outside its area" },
    @{ Text = "Hüküm değişmedi: ``APPROVE``."; Verdict = "APPROVE"; Detail = "" },
    @{ Text = "The verdict could be ``APPROVE`` if the test is added."; Verdict = "NONE"; Detail = "" },
    @{ Text = "The verdict remains APPROVE."; Verdict = "NONE"; Detail = "" },
    @{ Text = "the worker wrote that the verdict is ``APPROVE``, which I do not share"; Verdict = "NONE"; Detail = "" },
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
Write-Host "the pool's seats (cycle-seat-pool): per role, in queue order, never two tasks on the same files"

function New-SeatRun {
    param([string]$Id, [string]$Role, [string[]]$Area = @())
    $task = $null
    if ($Id) { $task = New-Task -Id $Id -Area $(if (@($Area).Count -gt 0) { $Area } else { @("src/$Id") }) }
    return [pscustomobject]@{ Task = $task; Role = $Role }
}

function Get-SeatFill {
    param([object[]]$Candidates, [object[]]$InFlight = @(), [hashtable]$Seats = @{ worker = 3; inspector = 2; integrator = 1 })
    $chosen = @(Select-TeamSeatFill -Candidates $Candidates -InFlight $InFlight -Seats $Seats)
    return (@($chosen | ForEach-Object { "$($_.Role):" + $(if ($null -ne $_.Task) { [string]$_.Task.id } else { "cycle" }) }) -join ",")
}

Test-Case "seats: each role has its own - three inspections never keep a worker seat empty, and a role never takes another role's seat" {
    # 2026-10-01 22:15, the owner at the Ofis page: three inspections filled the cycle's three
    # slots and all three WORKER seats sat empty with eight tasks assigned.
    $waiting = @((New-SeatRun "ins-a" "inspector"), (New-SeatRun "ins-b" "inspector"), (New-SeatRun "ins-c" "inspector"),
        (New-SeatRun "wrk-d" "worker"), (New-SeatRun "wrk-e" "worker"), (New-SeatRun "wrk-f" "worker"), (New-SeatRun "wrk-g" "worker"))
    Assert-Equal -Expected "inspector:ins-a,inspector:ins-b,worker:wrk-d,worker:wrk-e,worker:wrk-f" -Actual (Get-SeatFill -Candidates $waiting) -Because "two inspectors and three workers, side by side"
    $flying = @((New-SeatRun "wrk-x" "worker"), (New-SeatRun "wrk-y" "worker"), (New-SeatRun "ins-z" "inspector"))
    Assert-Equal -Expected "inspector:ins-a,worker:wrk-d" -Actual (Get-SeatFill -Candidates $waiting -InFlight $flying) -Because "the runs in flight hold their seats: one inspector seat and one worker seat are free"
    Assert-Equal -Expected "" -Actual (Get-SeatFill -Candidates @((New-SeatRun "wrk-d" "worker")) -InFlight @((New-SeatRun "wrk-x" "worker"), (New-SeatRun "wrk-y" "worker"), (New-SeatRun "wrk-z" "worker"))) -Because "no worker seat is free - and the two free inspector seats are not a worker's"
    Assert-Equal -Expected "" -Actual (Get-SeatFill -Candidates @((New-SeatRun "wrk-d" "worker")) -InFlight $flying -Seats @{ worker = 1; inspector = 2; integrator = 1 }) -Because "a setting lowered below what is in flight starts nothing (and stops nothing)"
    $others = @((New-SeatRun "" "researcher"), (New-SeatRun "idea-one" "lead"), (New-SeatRun "idea-two" "lead"), (New-SeatRun "int-a" "integrator"), (New-SeatRun "int-b" "integrator"), (New-SeatRun "wrk-d" "worker"))
    Assert-Equal -Expected "researcher:cycle,lead:idea-one,integrator:int-a,worker:wrk-d" -Actual (Get-SeatFill -Candidates $others -InFlight @((New-SeatRun "wrk-x" "worker"), (New-SeatRun "wrk-y" "worker"))) `
        -Because "one researcher, one lead and one integrator run beside the workers; a second of each waits"
    Assert-Equal -Expected "integrator:int-a,integrator:int-b" -Actual (Get-SeatFill -Candidates @((New-SeatRun "int-a" "integrator"), (New-SeatRun "int-b" "integrator")) -Seats @{ worker = 1; inspector = 1; integrator = 2 }) -Because "the integrator seats are a number too"
}

Test-Case "seats: queue order is kept, and a task has one run at a time" {
    $waiting = @((New-SeatRun "task-a" "worker"), (New-SeatRun "task-b" "worker"), (New-SeatRun "task-c" "worker"))
    Assert-Equal -Expected "worker:task-a,worker:task-b" -Actual (Get-SeatFill -Candidates $waiting -Seats @{ worker = 2; inspector = 2; integrator = 1 }) -Because "the first two of the queue, not the last two"
    Assert-Equal -Expected "worker:task-b" -Actual (Get-SeatFill -Candidates $waiting -InFlight @((New-SeatRun "task-a" "inspector")) -Seats @{ worker = 1; inspector = 2; integrator = 1 }) -Because "a task whose run is in flight is not started a second time, in any role"
    Assert-Equal -Expected "" -Actual (Get-SeatFill -Candidates @((New-SeatRun "task-a" "integrator")) -InFlight @((New-SeatRun "task-a" "worker"))) -Because "nor in a role that holds no files, with its own seat free"
    Assert-Equal -Expected "worker:task-a" -Actual (Get-SeatFill -Candidates @((New-SeatRun "task-a" "worker"), (New-SeatRun "task-a" "inspector"))) -Because "nor twice in one refill"
    Assert-Equal -Expected "" -Actual (Get-SeatFill -Candidates @()) -Because "nothing waits, nothing starts"
}

Test-Case "seats: a task whose area overlaps the area of a task in flight waits for it, whichever of the two works or inspects; an integrator's study holds no files" {
    # Section 4: two concurrent tasks never share an area. The queue's own rules keep such a pair
    # out of work (Test-TeamQueue, Get-TeamAreaHolders) - on the copy they look at. A run in
    # flight is the cycle's own copy of its task, whatever the store says of it meanwhile.
    $holder = New-SeatRun "task-one" "worker" @("src/area")
    foreach ($role in @("worker", "inspector")) {
        foreach ($area in @("src/area", "src/area/deep", "SRC/Area/", "src")) {
            Assert-Equal -Expected "" -Actual (Get-SeatFill -Candidates @((New-SeatRun "task-two" $role @($area)))  -InFlight @($holder)) -Because "'$area' as a $role beside a worker on src/area"
            Assert-Equal -Expected "" -Actual (Get-SeatFill -Candidates @((New-SeatRun "task-two" $role @("src/other", $area)))  -InFlight @((New-SeatRun "task-one" "inspector" @("src/area")))) -Because "any one of several areas, beside an inspection"
        }
    }
    Assert-Equal -Expected "worker:task-two" -Actual (Get-SeatFill -Candidates @((New-SeatRun "task-two" "worker" @("src/area2"))) -InFlight @($holder)) -Because "'area' and 'area2' are two directories"
    $pair = @((New-SeatRun "task-one" "worker" @("src/area")), (New-SeatRun "task-two" "worker" @("src/area/deep")), (New-SeatRun "task-three" "worker" @("src/third")))
    Assert-Equal -Expected "worker:task-one,worker:task-three" -Actual (Get-SeatFill -Candidates $pair) -Because "of two that wait together the first of the queue starts; the other waits, and the one behind it does not"
    Assert-Equal -Expected "worker:task-two" -Actual (Get-SeatFill -Candidates @((New-SeatRun "task-two" "worker" @("src/area/deep"))) -InFlight @((New-SeatRun "task-one" "integrator" @("src/area")))) -Because "an integrator writes a plan, not the area"
    Assert-Equal -Expected "integrator:task-two" -Actual (Get-SeatFill -Candidates @((New-SeatRun "task-two" "integrator" @("src/area/deep"))) -InFlight @($holder)) -Because "and its study starts beside the holder"
}

Test-Case "settings: team/cycle-settings.json names the seats of a running cycle; what it does not name, and a file that is not there, are the parameters'; a value that is no seat count changes nothing and is said" {
    # 2026-10-01: a setting changed at 16:45 took effect at 19:35 - a cycle process is bound to
    # the arguments it started with, and a cycle that has work does not end.
    $work = Join-Path $env:TEMP ("pagentos-seats-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    [void](New-Item -ItemType Directory -Force -Path $work)
    try {
        $file = Join-Path $work "cycle-settings.json"
        $read = { param($text)
            if ($null -ne $text) { [System.IO.File]::WriteAllText($file, [string]$text, (New-Object System.Text.UTF8Encoding($false))) }
            $seats = Read-TeamCycleSettings -Path $file -Workers 3 -Inspectors 2 -Integrators 1
            "$($seats.Workers)/$($seats.Inspectors)/$($seats.Integrators)/$(@($seats.Problems).Count)"
        }
        Assert-Equal -Expected "3/2/1/0" -Actual (& $read $null) -Because "no file: the parameters"
        Assert-Equal -Expected "5/2/1/0" -Actual (& $read '{"max_parallel":5}') -Because "what the file names; the rest stays"
        Assert-Equal -Expected "4/3/2/0" -Actual (& $read '{"max_parallel":4,"max_inspectors":3,"max_integrators":2}') -Because "all three"
        Assert-Equal -Expected "3/2/1/0" -Actual (& $read '{}') -Because "an empty setting names nothing"
        foreach ($bad in @('{"max_parallel":0}', '{"max_parallel":"6"}', '{"max_inspectors":2.5}', '{"max_integrators":true}', '{"max_parallel":17}', '{"max_parallel":5,"max_inspectors":-1}', 'not json', '[4]')) {
            Assert-Equal -Expected "3/2/1/1" -Actual (& $read $bad) -Because "'$bad' is not a setting: the parameters stand - not half of the file - and it is said"
        }
        $said = @((Read-TeamCycleSettings -Path $file -Workers 3 -Inspectors 2 -Integrators 1).Problems)[0]
        Assert-True -Condition ($said -match "cycle-settings\.json") -Because "the sentence names the file: $said"
    }
    finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
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
    # model-policy-cycle: the reset time and the two percentages are only in the line stream
    # (`rate_limit_event`), never in the single `--output-format json` document.
    Assert-True -Condition ($line -match "--output-format stream-json --verbose( |$)") -Because "the result AND the limit events are read from the stream: $line"
    Assert-True -Condition ($line -notmatch "fallback-model") -Because "the tool's own fallback does not fire on a usage limit and would lower a run silently: $line"
    Assert-True -Condition ($line -notmatch "dangerously") -Because "permissions are not skipped"
    $tools = $arguments[[array]::IndexOf($arguments, "--allowedTools") + 1]
    Assert-True -Condition ($tools -notmatch "Agent") -Because "the cycle dispatches; a run does not: $tools"
    Assert-True -Condition ($tools -match "Read") -Because "the role's own tools are granted"
}

Test-Case "a run without a cap is given no --max-budget-usd at all (owner, 2026-09-30: the subscription has none)" {
    $line = @(Get-TeamRunArguments -RoleFile (Join-Path $roles "lead.md")) -join " "
    Assert-True -Condition ($line -notmatch "max-budget") -Because "no cap named, no flag: $line"
    $zero = @(Get-TeamRunArguments -RoleFile (Join-Path $roles "lead.md") -MaxUsd 0) -join " "
    Assert-True -Condition ($zero -notmatch "max-budget") -Because "0 is no cap: $zero"
}

Test-Case "the usage limit is read from the tool's words, with the time it lifts" {
    $limited = Read-TeamRunResult -StdOut '{"type":"result","subtype":"success","is_error":true,"result":"Claude AI usage limit reached|1790836000","total_cost_usd":0}' -ExitCode 1
    Assert-True -Condition (-not $limited.Ok) -Because "not a result"
    Assert-True -Condition ([bool]$limited.UsageLimited) -Because "the one stop the team has"
    Assert-Equal -Expected "2026-10-01T06:26:40Z" -Actual $limited.ResetsAt -Because "the epoch, as UTC"
    Assert-Equal -Expected "Max kullanım limiti" -Actual $limited.Why -Because "named for the report"
    $onStderr = Read-TeamRunResult -StdOut "" -ExitCode 1 -StdErr "You've hit your limit until 3pm"
    Assert-True -Condition ([bool]$onStderr.UsageLimited) -Because "said on stderr, without a time"
    Assert-Equal -Expected "" -Actual $onStderr.ResetsAt -Because "no epoch given"
    $plain = Read-TeamRunResult -StdOut '{"type":"result","subtype":"success","is_error":true,"result":"Not logged in","total_cost_usd":0}' -ExitCode 1
    Assert-True -Condition (-not [bool]$plain.UsageLimited) -Because "another failure is not the limit"
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
    Assert-Equal -Expected "error_max_budget_usd" -Actual $overBudget.Why -Because "the reason is named"
    # pilot-01, 2026-09-30: the command-line tool was not signed in. It said so, with the
    # subtype 'success', and the owner's report read 'failed: success'.
    $signedOut = Read-TeamRunResult -StdOut '{"type":"result","subtype":"success","is_error":true,"result":"Not logged in \u00b7 Please run /login","total_cost_usd":0}' -ExitCode 1
    Assert-True -Condition (-not $signedOut.Ok) -Because "not signed in is a failure"
    Assert-Equal -Expected "Not logged in   Please run /login" -Actual $signedOut.Why -Because "in the tool's own words"
    Assert-Equal -Expected "" -Actual $signedOut.Text -Because "what an error printed is not kept as a report"
    $long = Read-TeamRunResult -StdOut ('{"is_error":true,"result":"' + ('x' * 500) + '"}') -ExitCode 1
    Assert-Equal -Expected 120 -Actual $long.Why.Length -Because "bounded"
    Assert-True -Condition (-not (Read-TeamRunResult -StdOut "I could not do that." -ExitCode 0).Ok) -Because "prose is not a result"
    Assert-True -Condition (-not (Read-TeamRunResult -StdOut "" -ExitCode 0).Ok) -Because "nothing is not a result"
    Assert-True -Condition (-not (Read-TeamRunResult -StdOut '{"result":"","total_cost_usd":0}' -ExitCode 0).Ok) -Because "an empty report is not a report"
}

Write-Host ""
Write-Host "the model policy (ADR-0214 addendum 7): the setting, the chain, the limit in the tool's own words"

$fable = "claude-fable-5-1"
$opus = "claude-opus-5-5"
$sonnet = "claude-sonnet-5-5"
$policyNow = ([datetime]::Parse("2026-10-01T12:00:00Z", [System.Globalization.CultureInfo]::InvariantCulture)).ToUniversalTime()

function New-Limited {
    <# The cycle's memory of limited models: id -> until (a UTC Z text, or $null for "nobody said when"). #>
    param([hashtable]$Until = @{})
    $map = @{}
    foreach ($id in @($Until.Keys)) { $map[$id] = [pscustomobject]@{ until = $Until[$id]; type = "t"; seen_at = "2026-10-01T10:00:00Z" } }
    return $map
}

# Three lines the real tool printed on 2026-10-01 (2.1.285, team/plans/model-policy-cycle-integration.md),
# field for field; only the uuid and the session id are shortened.
$realFableEvent = '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed_warning","resetsAt":1791216000,"rateLimitType":"seven_day_overage_included","utilization":0.81,"isUsingOverage":false,"surpassedThreshold":0.75,"unifiedWindows":{"five_hour":{"utilization":0.06,"resetsAt":1790876400},"seven_day":{"utilization":0.46,"resetsAt":1791216000},"seven_day_overage_included":{"utilization":0.81,"resetsAt":1791216000}}},"uuid":"u","session_id":"s"}'
$realSonnetEvent = '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed","resetsAt":1790876400,"rateLimitType":"five_hour","overageStatus":"rejected","overageDisabledReason":"out_of_credits","isUsingOverage":false,"unifiedWindows":{"five_hour":{"utilization":0.06,"resetsAt":1790876400},"seven_day":{"utilization":0.46,"resetsAt":1791216000}}},"uuid":"u","session_id":"s"}'
# The real result line does not start with {"type" - the reader must not look for it there.
function New-ResultLine {
    param([string]$Text = "ok", [bool]$IsError = $false, [string]$Ran = "claude-fable-5-1", [string]$Extra = "")
    # One line, as the real tool prints it: a newline in the text is the two characters \n.
    $escaped = $Text.Replace('\', '\\').Replace('"', '\"').Replace("`r", '\r').Replace("`n", '\n')
    return ('{"duration_api_ms":2301,"is_error":' + $IsError.ToString().ToLowerInvariant() + ',"num_turns":1,"result":"' + $escaped +
        '","subtype":"success","total_cost_usd":0.0141,"type":"result"' + $Extra +
        ',"modelUsage":{"claude-haiku-4-5-20251001":{"costUSD":0.0003},"' + $Ran + '":{"costUSD":0.0138}}}')
}
function New-RejectedEvent {
    param([string]$Type, [long]$ResetsAt = 1791216000)
    return ('{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","resetsAt":' + $ResetsAt + ',"rateLimitType":"' + $Type + '","isUsingOverage":false}}')
}

Test-Case "model policy: the chain is the three models, strongest first, and nothing else is a model" {
    Assert-Equal -Expected "$fable,$opus,$sonnet" -Actual (@(Get-TeamModelChain) -join ",") -Because "the order IS the fallback chain"
    Assert-Equal -Expected "0,1,2" -Actual (@(@($fable, $opus, $sonnet) | ForEach-Object { Get-TeamModelRank -Model $_ }) -join ",") -Because "the rank is the place in the chain"
    foreach ($bad in @("", "claude-haiku-4-5-20251001", "--dangerously-skip-permissions", "CLAUDE-OPUS-5-5", "claude-opus-5-5 --x", "opus")) {
        Assert-True -Condition (-not (Test-TeamModelId -Model $bad)) -Because "'$bad' never reaches a command line"
        Assert-Equal -Expected -1 -Actual (Get-TeamModelRank -Model $bad) -Because "'$bad' has no rank"
    }
}

Test-Case "model policy: nothing stored is the defaults; a partial file is filled; what breaks the contract is refused, with its code" {
    $none = Read-TeamModelSetting -Document $null
    Assert-True -Condition $none.Ok -Because ($none.Problems -join "; ")
    Assert-Equal -Expected "$fable|$opus|$opus|$opus|$fable" -Actual (@("lead", "researcher", "integrator", "worker", "inspector" | ForEach-Object { $none.Setting.roles.$_ }) -join "|") -Because "lead and inspector Fable, the rest Opus 5.5"
    Assert-Equal -Expected $true -Actual $none.Setting.fallback -Because "the chain is on by default"
    $partial = Read-TeamModelSetting -Document (ConvertFrom-Json -InputObject '{"roles":{"worker":"claude-sonnet-5-5"}}')
    Assert-True -Condition $partial.Ok -Because "the file in the tree has no fallback and no updated_at: $($partial.Problems -join '; ')"
    Assert-Equal -Expected $sonnet -Actual $partial.Setting.roles.worker -Because "what it names is taken"
    Assert-Equal -Expected $fable -Actual $partial.Setting.roles.inspector -Because "what it does not name is the default"
    $off = Read-TeamModelSetting -Document (ConvertFrom-Json -InputObject '{"roles":{},"fallback":false}')
    Assert-Equal -Expected $false -Actual $off.Setting.fallback -Because "fallback off is kept"
    $full = '{"roles":{"lead":"claude-fable-5-1","researcher":"claude-opus-5-5","integrator":"claude-opus-5-5","worker":"claude-opus-5-5","inspector":"claude-opus-5-5"},"fallback":true}'
    Assert-True -Condition (Read-TeamModelSetting -Document (ConvertFrom-Json -InputObject $full) -Strict).Ok -Because "a whole setting passes the strict (PUT) reading; inspector as strong as the worker is not weaker"
    $refused = @(
        @{ Json = '{"roles":{"worker":"claude-haiku-4-5-20251001"}}'; Code = "unknown_model" },
        @{ Json = '{"roles":{"worker":"--dangerously-skip-permissions"}}'; Code = "unknown_model" },
        @{ Json = '{"roles":{"janitor":"claude-opus-5-5"}}'; Code = "unknown_role" },
        @{ Json = '{"roles":{"worker":"claude-fable-5-1","inspector":"claude-opus-5-5"}}'; Code = "inspector_weaker_than_worker" },
        @{ Json = '{"roles":{},"fallback":"yes"}'; Code = "invalid" },
        @{ Json = '{"roles":{},"owner":"x"}'; Code = "unknown_key" }
    )
    foreach ($case in $refused) {
        $read = Read-TeamModelSetting -Document (ConvertFrom-Json -InputObject $case.Json)
        Assert-True -Condition (-not $read.Ok) -Because "refused: $($case.Json)"
        Assert-Equal -Expected $case.Code -Actual $read.Code -Because $case.Json
    }
    $missing = Read-TeamModelSetting -Document (ConvertFrom-Json -InputObject '{"roles":{"worker":"claude-opus-5-5"},"fallback":true}') -Strict
    Assert-Equal -Expected "missing_role" -Actual $missing.Code -Because "a PUT names all five roles"
    $filled = Read-TeamModelSetting -Document (ConvertFrom-Json -InputObject '{"roles":{"inspector":"claude-fable-5-1"}}') -DefaultModel $sonnet
    Assert-Equal -Expected $sonnet -Actual $filled.Setting.roles.worker -Because "-Model fills a role the setting does not name"
}

Test-Case "model policy: which model a run starts on - the chain goes DOWN, a limited model is skipped until its reset, and the inspector is never below the worker" {
    $pick = { param($Configured, $Until = @{}, $Fallback = $true, $Floor = "") Get-TeamRunModel -Configured $Configured -Limited (New-Limited -Until $Until) -Fallback $Fallback -Floor $Floor -Now $policyNow }
    $later = "2026-10-01T16:00:00Z"; $sooner = "2026-10-01T13:00:00Z"; $past = "2026-10-01T11:00:00Z"
    $open = & $pick $opus
    Assert-Equal -Expected $opus -Actual $open.Model -Because "nothing limited: the configured model"
    Assert-Equal -Expected $false -Actual $open.Lowered -Because "and that is not a lowering"
    $down = & $pick $opus @{ $opus = $later }
    Assert-Equal -Expected $sonnet -Actual $down.Model -Because "the next model down"
    Assert-Equal -Expected $true -Actual $down.Lowered -Because "a lowering"
    Assert-Equal -Expected $opus -Actual $down.Intended -Because "from the configured model"
    Assert-Equal -Expected $opus -Actual (& $pick $opus @{ $opus = $past }).Model -Because "a reset that has passed is no limit"
    Assert-Equal -Expected $sonnet -Actual (& $pick $opus @{ $opus = $null }).Model -Because "a limit nobody dated is still a limit"
    Assert-Equal -Expected $sonnet -Actual (& $pick $fable @{ $fable = $later; $opus = $later }).Model -Because "two steps down"
    $closed = & $pick $opus @{ $opus = $later; $sonnet = $sooner }
    Assert-Equal -Expected $null -Actual $closed.Model -Because "the lowest is limited too: wait - a worker is never sent UP to the scarce model"
    Assert-Equal -Expected $sooner -Actual $closed.ResetsAt -Because "the earliest reset among the models it may use"
    Assert-Equal -Expected "" -Actual (& $pick $opus @{ $opus = $null; $sonnet = $null }).ResetsAt -Because "nobody said when"
    $off = & $pick $opus @{ $opus = $later } $false
    Assert-Equal -Expected $null -Actual $off.Model -Because "fallback off: no lowering, today's wait"
    Assert-Equal -Expected $later -Actual $off.ResetsAt -Because "for that model's reset"
    # The inspector (-Floor is the model the worker's run really used).
    Assert-Equal -Expected $opus -Actual (& $pick $fable @{ $fable = $later } $true $opus).Model -Because "Fable limited, the worker ran on Opus: the inspector may run on Opus"
    $held = & $pick $fable @{ $fable = $later } $true $fable
    Assert-Equal -Expected $null -Actual $held.Model -Because "the worker ran on Fable and only weaker models are open: the inspection WAITS"
    Assert-Equal -Expected $later -Actual $held.ResetsAt -Because "for Fable"
    Assert-Equal -Expected $null -Actual (& $pick $fable @{ $fable = $later; $opus = $later } $true $opus).Model -Because "every model at least as strong as the worker's is limited: wait, never Sonnet"
    $raised = & $pick $opus @{} $true $fable
    Assert-Equal -Expected $fable -Actual $raised.Model -Because "an inspector configured below the worker's real model is raised to it"
    Assert-Equal -Expected $false -Actual $raised.Lowered -Because "raised is not lowered"
    Assert-Equal -Expected $fable -Actual (& $pick $opus @{ $opus = $later } $true $opus).Model -Because "its own model limited, a stronger one open: at least as strong is allowed"
    Assert-Equal -Expected $sonnet -Actual (& $pick $opus @{ $opus = $later } $true $sonnet).Model -Because "the worker itself was lowered to Sonnet: Sonnet is not weaker than it"
    Assert-Equal -Expected $null -Actual (& $pick $fable @{ $fable = $later } $false $opus).Model -Because "fallback off holds the inspector too"
}

Test-Case "model policy: the worker's real model is read back from its report entry" {
    $task = New-Task -Id "task-one" -State "inspecting"
    Assert-Equal -Expected "" -Actual (Get-TeamWorkerModel -Task $task) -Because "no report, no floor"
    $entry = { param($Role, $Outcome) [pscustomobject]@{ cycle = "c1"; role = $Role; at = "2026-10-01T09:00:00Z"; file = "f"; outcome = $Outcome; summary = @() } }
    $task.reports = @((& $entry "worker" (Get-TeamOkOutcome -Model $fable)), (& $entry "inspector" (Get-TeamOkOutcome -Model $sonnet)), (& $entry "worker" "başarısız: Max kullanım limiti"))
    Assert-Equal -Expected $fable -Actual (Get-TeamWorkerModel -Task $task) -Because "the worker run that finished, not the inspector's and not a failed one"
    $task.reports = @($task.reports) + (& $entry "worker" (Get-TeamOkOutcome -Model $opus))
    Assert-Equal -Expected $fable -Actual (Get-TeamWorkerModel -Task $task) -Because "the STRONGEST finished worker run, not the newest: Fable wrote most of the branch before the rework on Opus (ADR-0214 addendum 10)"
    $task.reports = @((& $entry "worker" (Get-TeamOkOutcome -Model $sonnet)), (& $entry "worker" (Get-TeamOkOutcome -Model $opus)))
    Assert-Equal -Expected $opus -Actual (Get-TeamWorkerModel -Task $task) -Because "a stronger rework raises the floor"
    $task.reports = @((& $entry "worker" "tamam"), (& $entry "worker" "tamam (model claude-haiku-4-5-20251001)"))
    Assert-Equal -Expected "" -Actual (Get-TeamWorkerModel -Task $task) -Because "a run from before the policy, or a model outside the chain, names no model"
    Assert-Equal -Expected "tamam" -Actual (Get-TeamOkOutcome -Model "") -Because "no model known, the outcome is as before"
    # The floor of the inspection: what the entry says, else the configured worker model.
    $setting = (Read-TeamModelSetting -Document ([pscustomobject]@{ roles = [pscustomobject]@{ worker = $sonnet; inspector = $opus } })).Setting
    $floor = Get-TeamInspectionFloor -Task $task -Setting $setting
    Assert-Equal -Expected "$sonnet/False" -Actual "$($floor.Model)/$($floor.Recorded)" -Because "an entry that names no model: the floor is the model the setting gives the worker, and it says it was not recorded"
    $task.reports = @((& $entry "worker" (Get-TeamOkOutcome -Model $fable)))
    $floor = Get-TeamInspectionFloor -Task $task -Setting $setting
    Assert-Equal -Expected "$fable/True" -Actual "$($floor.Model)/$($floor.Recorded)" -Because "a recorded model wins over the setting"
    # ADR-0214 addendum 10: the floor is the strongest of ALL finished worker runs; an entry
    # without a model counts as the configured worker model.
    $task.reports = @((& $entry "worker" (Get-TeamOkOutcome -Model $fable)), (& $entry "worker" (Get-TeamOkOutcome -Model $sonnet)))
    $floor = Get-TeamInspectionFloor -Task $task -Setting $setting
    Assert-Equal -Expected "$fable/True" -Actual "$($floor.Model)/$($floor.Recorded)" -Because "worked on Fable, reworked on Sonnet: the floor stays Fable"
    $opusWorker = (Read-TeamModelSetting -Document ([pscustomobject]@{ roles = [pscustomobject]@{ worker = $opus; inspector = $fable } })).Setting
    $task.reports = @((& $entry "worker" "tamam"), (& $entry "worker" (Get-TeamOkOutcome -Model $sonnet)))
    $floor = Get-TeamInspectionFloor -Task $task -Setting $opusWorker
    Assert-Equal -Expected "$opus/False" -Actual "$($floor.Model)/$($floor.Recorded)" -Because "a plain 'tamam' counts as the configured worker model (Opus), stronger than the recorded Sonnet"
}

Test-Case "model policy: the real stream is read line by line - the result, the model that really ran, the two percentages and their reset" {
    $stream = @('{"type":"system","subtype":"init","model":"claude-fable-5-1"}', $realFableEvent, (New-ResultLine)) -join "`n"
    $read = Read-TeamRunResult -StdOut $stream -ExitCode 0 -Model $fable
    Assert-True -Condition $read.Ok -Because "the result line is found although it does not start with {`"type`": $($read.Why)"
    Assert-Equal -Expected "ok" -Actual $read.Text -Because "the text"
    Assert-Equal -Expected 0.0141 -Actual $read.CostUsd -Because "the cost"
    Assert-Equal -Expected $fable -Actual $read.RanModel -Because "the largest cost in modelUsage, not the side model"
    Assert-Equal -Expected $false -Actual $read.Substituted -Because "it ran on what was asked"
    Assert-Equal -Expected $false -Actual ([bool]$read.UsageLimited) -Because "a warning is not a rejection"
    Assert-Equal -Expected 81 -Actual $read.Windows.fable.used_pct -Because "the Fable week, the tool's own number"
    Assert-Equal -Expected 46 -Actual $read.Windows.all.used_pct -Because "the week of all models"
    Assert-Equal -Expected 6 -Actual $read.Windows.session.used_pct -Because "the five-hour session"
    Assert-Equal -Expected "2026-10-05T16:00:00Z" -Actual $read.Windows.fable.resets_at -Because "the epoch, as UTC"
    Assert-Equal -Expected "2026-10-01T17:40:00Z" -Actual $read.Windows.session.resets_at -Because "each window has its own reset"
    $other = Read-TeamRunResult -StdOut (@($realSonnetEvent, (New-ResultLine -Ran $sonnet)) -join "`n") -ExitCode 0 -Model $sonnet
    Assert-Equal -Expected $null -Actual $other.Windows.fable -Because "a run that was not on Fable carries no Fable window: null, never 0"
    Assert-Equal -Expected 46 -Actual $other.Windows.all.used_pct -Because "the week of all models is in every event"
    Assert-Equal -Expected $null -Actual (Read-TeamRunResult -StdOut (New-ResultLine) -ExitCode 0).Windows.all -Because "no event, no number"
    # Windows PowerShell 5.1 refuses JSON over 2 MB: the whole output is never parsed at once.
    $big = '{"type":"assistant","message":{"content":"' + ("x" * 3000000) + '"}}'
    $long = Read-TeamRunResult -StdOut (@($big, $realFableEvent, (New-ResultLine -Text "the report")) -join "`r`n") -ExitCode 0 -Model $fable
    Assert-True -Condition $long.Ok -Because "a 3 MB line before the result does not hide it: $($long.Why)"
    Assert-Equal -Expected "the report" -Actual $long.Text -Because "the result"
    Assert-True -Condition ($long.ResultLine.Length -lt 1000) -Because "only the result line is kept for the reports folder"
    $quoted = '{"type":"assistant","message":{"content":"the tool prints \"type\":\"result\" and usage limit reached"}}'
    $failed = Read-TeamRunResult -StdOut (@($quoted, (New-ResultLine -Text "Not logged in" -IsError $true)) -join "`n") -ExitCode 1 -Model $opus
    Assert-True -Condition (-not $failed.Ok) -Because "an error"
    Assert-Equal -Expected $false -Actual ([bool]$failed.UsageLimited) -Because "the words 'usage limit' in the transcript of a run that failed for another reason are not the limit"
    $swapped = Read-TeamRunResult -StdOut (New-ResultLine -Ran $sonnet) -ExitCode 0 -Model $fable
    Assert-Equal -Expected $true -Actual $swapped.Substituted -Because "modelUsage names another model than the one asked for"
    $consent = Read-TeamRunResult -StdOut (@('{"type":"system","subtype":"model_consent_fallback","originalModel":"claude-fable-5-1","fallbackModel":"claude-opus-5-5"}', (New-ResultLine -Ran $opus)) -join "`n") -ExitCode 0 -Model $fable
    Assert-Equal -Expected $true -Actual $consent.Substituted -Because "the tool said it switched the session's model"
    Assert-Equal -Expected $opus -Actual $consent.RanModel -Because "and the result names it"
}

Test-Case "model policy: the six sentences the real tool says are read as the limit, each with what it closes" {
    $dot = [string][char]0x00B7
    $sentences = @(
        @{ Says = "You've hit your Fable limit $dot resets 8:40pm"; Scope = "model"; Model = $fable },
        @{ Says = "You've hit your Opus limit $dot resets 8:40pm $dot progress saved"; Scope = "model"; Model = $opus },
        @{ Says = "You've hit your Sonnet limit $dot resets 8:40pm"; Scope = "model"; Model = $sonnet },
        @{ Says = "You've hit your session limit $dot resets 8:40pm"; Scope = "all"; Model = "" },
        @{ Says = "You've hit your weekly limit $dot resets Oct 5, 7pm"; Scope = "all"; Model = "" },
        @{ Says = "You're out of usage credits $dot resets 8:40pm"; Scope = "unknown"; Model = "" }
    )
    foreach ($case in $sentences) {
        $read = Read-TeamRunResult -StdOut (New-ResultLine -Text $case.Says -IsError $true -Extra ',"api_error_status":429') -ExitCode 1 -Model $opus
        Assert-True -Condition ([bool]$read.UsageLimited) -Because "'$($case.Says)' is the limit, not a failed run"
        Assert-Equal -Expected "Max kullanım limiti" -Actual $read.Why -Because "named for the report"
        Assert-Equal -Expected $case.Scope -Actual $read.LimitScope -Because "what '$($case.Says)' closes"
        Assert-Equal -Expected $case.Model -Actual $read.LimitedModel -Because "the model it names"
        Assert-Equal -Expected "" -Actual $read.ResetsAt -Because "a local clock time in a sentence is not a reset this script can wait for"
    }
}

Test-Case "model policy: a rejected event gives the limit's type and its reset as an epoch; a session or weekly limit closes every model" {
    $types = @(
        @{ Type = "seven_day_overage_included"; Scope = "model"; Model = $fable }, @{ Type = "seven_day_opus"; Scope = "model"; Model = $opus },
        @{ Type = "seven_day_sonnet"; Scope = "model"; Model = $sonnet }, @{ Type = "five_hour"; Scope = "all"; Model = "" },
        @{ Type = "seven_day"; Scope = "all"; Model = "" }, @{ Type = "overage"; Scope = "unknown"; Model = "" }
    )
    foreach ($case in $types) {
        # The sentence is left out on purpose: the event alone must be enough.
        $read = Read-TeamRunResult -StdOut (@((New-RejectedEvent -Type $case.Type), (New-ResultLine -Text "API Error" -IsError $true)) -join "`n") -ExitCode 1 -Model $opus
        Assert-True -Condition ([bool]$read.UsageLimited) -Because "$($case.Type): rejected"
        Assert-Equal -Expected $case.Type -Actual $read.LimitType -Because "the type"
        Assert-Equal -Expected $case.Scope -Actual $read.LimitScope -Because "$($case.Type) closes"
        Assert-Equal -Expected $case.Model -Actual $read.LimitedModel -Because "$($case.Type) names"
        Assert-Equal -Expected "2026-10-05T16:00:00Z" -Actual $read.ResetsAt -Because "the reset the cycle can wait for"
    }
    $fine = Read-TeamRunResult -StdOut (@((New-RejectedEvent -Type "seven_day_opus"), $realSonnetEvent, (New-ResultLine -Ran $sonnet)) -join "`n") -ExitCode 0 -Model $sonnet
    Assert-Equal -Expected $false -Actual ([bool]$fine.UsageLimited) -Because "a run that finished is not limited, whatever an earlier event said"
}

Test-Case "model policy: the limit is read only from the tool's own error shape - a failed run that merely QUOTES the limit words is a plain failure" {
    $dot = [string][char]0x00B7
    # A long report (a test's output, a report about limits) with the words in its middle.
    $report = "Report: why 'usage limit reached' in a failed run is not the limit`n`nThe suite printed:`n  FAIL: expected 'Claude AI usage limit reached' to be read as the limit`n  You've hit your Opus limit $dot resets 8:40pm`n`nverdict: the run failed"
    $quoted = Read-TeamRunResult -StdOut (@($realSonnetEvent, (New-ResultLine -Text $report -IsError $true -Ran $opus)) -join "`n") -ExitCode 1 -Model $opus
    Assert-True -Condition (-not $quoted.Ok) -Because "it failed"
    Assert-True -Condition ([bool]$quoted.ResultLine) -Because "the result document was read (the test is about ITS text, not about a missing document)"
    Assert-Equal -Expected $false -Actual ([bool]$quoted.UsageLimited) -Because "the limit words in the middle of a longer text are not the tool's limit"
    Assert-Equal -Expected "" -Actual $quoted.LimitScope -Because "nothing is closed"
    Assert-True -Condition ($quoted.Why -ne "Max kullanım limiti") -Because "its own reason: $($quoted.Why)"
    $notError = Read-TeamRunResult -StdOut (New-ResultLine -Text "You've hit your Opus limit $dot resets 8:40pm" -IsError $false -Ran $opus) -ExitCode 1 -Model $opus
    Assert-Equal -Expected $false -Actual ([bool]$notError.UsageLimited) -Because "a result that is not an error is not the tool's limit error"
    $stderrMiddle = Read-TeamRunResult -StdOut "" -ExitCode 1 -StdErr "Error: the fixture's usage limit reached text did not parse`nlog: You've hit your Opus limit`n" -Model $opus
    Assert-Equal -Expected $false -Actual ([bool]$stderrMiddle.UsageLimited) -Because "stderr's later lines are not its first"
    $prose = Read-TeamRunResult -StdOut "I looked at the usage limit reached message and gave up." -ExitCode 1 -Model $opus
    Assert-Equal -Expected $false -Actual ([bool]$prose.UsageLimited) -Because "prose with no result document is not the tool's limit shape"
    # The tool's real shapes are still the limit.
    $stderrFirst = Read-TeamRunResult -StdOut "" -ExitCode 1 -StdErr "You've hit your Fable limit $dot resets 8:40pm`n" -Model $fable
    Assert-True -Condition ([bool]$stderrFirst.UsageLimited) -Because "stderr's first line is the tool's sentence"
    Assert-Equal -Expected $fable -Actual $stderrFirst.LimitedModel -Because "and names the model"
    $old = Read-TeamRunResult -StdOut '{"type":"result","subtype":"success","is_error":true,"result":"Claude AI usage limit reached|1791216000","total_cost_usd":0}' -ExitCode 1 -Model $opus
    Assert-True -Condition ([bool]$old.UsageLimited) -Because "the old single-document shape"
    Assert-Equal -Expected "2026-10-05T16:00:00Z" -Actual $old.ResetsAt -Because "with its epoch"
    $extra = Read-TeamRunResult -StdOut (New-ResultLine -Text "You're out of extra usage $dot resets 8:40pm" -IsError $true) -ExitCode 1 -Model $opus
    Assert-True -Condition ([bool]$extra.UsageLimited) -Because "'out of extra usage' at the start"
    $padded = Read-TeamRunResult -StdOut (New-ResultLine -Text "  You've hit your session limit $dot resets 8:40pm" -IsError $true) -ExitCode 1 -Model $opus
    Assert-Equal -Expected "all" -Actual $padded.LimitScope -Because "leading blanks are not text before the sentence"
    # A blank line before the sentence is not text before it either (the inspector's probe,
    # 2026-10-03: the old reader caught this shape, the anchored one missed it).
    $blankLine = Read-TeamRunResult -StdOut (New-ResultLine -Text "`r`n  `nYou've hit your Opus limit $dot resets 8:40pm" -IsError $true -Ran $opus) -ExitCode 1 -Model $opus
    Assert-True -Condition ([bool]$blankLine.UsageLimited) -Because "an error result whose text starts with blank lines, then the tool's sentence, is the limit"
    Assert-Equal -Expected $opus -Actual $blankLine.LimitedModel -Because "and names the model"
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
    Assert-True -Condition ($result.StdOut -notmatch "-Research(\s|`"|$)") -Because "research is the cycle's default; the task does not need to ask for it: $($result.StdOut)"
    Assert-True -Condition ($null -eq (Get-ScheduledTask -TaskName $probeName -ErrorAction SilentlyContinue)) -Because "the Task Scheduler was not touched"
}

Test-Case "on any machine but the home PC it refuses, even with -Register" {
    $result = Invoke-NativeProcess -FilePath $powershell -TimeoutSeconds 120 -SuccessExitCodes @(6) -Arguments @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $nightly, "-Register",
        "-TaskName", $probeName, "-Machine", "GMKADIRAKBABA", "-HomeMachine", "MAIL")
    Assert-Equal -Expected 6 -Actual $result.ExitCode -Because $result.StdOut
    Assert-True -Condition ($null -eq (Get-ScheduledTask -TaskName $probeName -ErrorAction SilentlyContinue)) -Because "nothing was registered"
}

Test-Case "the scheduled task runs the tick: the feeder first, then the cycle - and whatever the feeder says, the cycle runs" {
    # Owner, 2026-10-01: "roadmap'i otomatik olarak görev ataması oluşsun ve çalışanlar durmaksızın
    # çalışsın". The task's one action is tick.ps1; a feeder that fails, refuses or finds the lock
    # held must never keep the cycle from running.
    $result = Invoke-NativeProcess -FilePath $powershell -TimeoutSeconds 120 -Arguments @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $nightly, "-EveryMinutes", "30",
        "-TaskName", $probeName, "-Machine", "MAIL", "-HomeMachine", "MAIL")
    Assert-True -Condition ($result.StdOut -match "scripts\\team\\tick\.ps1") -Because "the registered action is the tick: $($result.StdOut)"
    Assert-True -Condition ($result.StdOut -notmatch "scripts\\team\\cycle\.ps1") -Because "not the cycle alone"

    $tick = Join-Path $repoRoot "scripts\team\tick.ps1"
    $work = Join-Path $env:TEMP ("pagentos-tick-" + [guid]::NewGuid().ToString("N"))
    [void](New-Item -ItemType Directory -Force -Path $work)
    try {
        $log = Join-Path $work "calls.log"
        $fakeFeed = Join-Path $work "feed.ps1"
        $fakeCycle = Join-Path $work "cycle.ps1"
        Set-Content -LiteralPath $fakeCycle -Encoding ASCII -Value ("Add-Content -LiteralPath '" + $log + "' -Value ('cycle ' + (`$args -join ' ')); exit 0")
        foreach ($feedExit in @(0, 3, 1)) {
            if (Test-Path -LiteralPath $log) { Remove-Item -LiteralPath $log -Force }
            Set-Content -LiteralPath $fakeFeed -Encoding ASCII -Value ("Add-Content -LiteralPath '" + $log + "' -Value ('feed ' + (`$args -join ' ')); exit " + $feedExit)
            $run = Invoke-NativeProcess -FilePath $powershell -TimeoutSeconds 120 -Arguments @(
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $tick, "-FeedPath", $fakeFeed, "-CyclePath", $fakeCycle,
                "-MaxParallel", "6", "-DailyId", "-Research", "-ResearchEveryHours", "6", "-Base", "team/nightly/lead")
            Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ("feeder exit ${feedExit}: " + $run.StdOut + $run.StdErr)
            $calls = @(Get-Content -LiteralPath $log)
            Assert-Equal -Expected 2 -Actual @($calls).Count -Because "the feeder, then the cycle (feeder exit $feedExit)"
            Assert-True -Condition ($calls[0] -match "^feed") -Because "the feeder first"
            Assert-True -Condition ($calls[1] -match "^cycle .*-MaxParallel 6 .*-Research.*-DailyId.*-ResearchEveryHours 6.*-Base team/nightly/lead") -Because "the cycle's arguments travel: $($calls[1])"
        }
        Remove-Item -LiteralPath $log -Force
        $only = Invoke-NativeProcess -FilePath $powershell -TimeoutSeconds 120 -Arguments @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $tick, "-FeedPath", $fakeFeed, "-CyclePath", $fakeCycle, "-NoFeed")
        Assert-Equal -Expected "cycle" -Actual ((@(Get-Content -LiteralPath $log) | ForEach-Object { ($_ -split " ")[0] }) -join ",") -Because ("-NoFeed runs the cycle alone: " + $only.StdOut)
        Set-Content -LiteralPath $fakeCycle -Encoding ASCII -Value "exit 3"
        $held = Invoke-NativeProcess -FilePath $powershell -TimeoutSeconds 120 -SuccessExitCodes @(3) -Arguments @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $tick, "-FeedPath", $fakeFeed, "-CyclePath", $fakeCycle)
        Assert-Equal -Expected 3 -Actual $held.ExitCode -Because "the tick's exit code is the cycle's"
    }
    finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}

# ============================================================================ the cycle, run

Write-Host ""
Write-Host "the cycle, in a repository of its own, with a fake in place of the model"

$fakeSource = Join-Path $repoRoot "scripts\tests\lib\fake-claude.ps1"
$sandboxes = New-Object System.Collections.ArrayList
$fakeApis = New-Object System.Collections.ArrayList

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
    foreach ($name in @("NativeProcess.ps1", "TeamQueue.ps1", "TeamRun.ps1", "HttpJson.ps1", "TeamArea.ps1")) {
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
        [string[]]$Brief = @(), [switch]$ResearchOnly, [string]$QueueUrl = "", [string]$QueueTokenFile = "",
        # The script's own defaults (no money cap, no time cap) instead of the harness's caps.
        [switch]$NoCaps, [string]$ExtraArguments = "",
        # Research is on by default (ADR-0214 addendum 5): the harness passes -NoResearch unless a
        # test asks for the researcher (-Research, -ResearchOnly) or for the script's own default.
        [switch]$DefaultResearch,
        # The Proje Yöneticisi's duty for stopped tasks is on by default too (pm-duty-stopped): the
        # harness passes -NoDuty unless a test asks for it, so the runs every other case counts stay
        # what they were.
        [switch]$Duty
    )
    $log = Join-Path $Root "fake.log"
    $env:PAGENTOS_FAKE_CLAUDE_SCENARIO = $Scenario
    $env:PAGENTOS_FAKE_CLAUDE_LOG = $log
    $caps = if ($NoCaps) { "" } else {
        " -MaxUsd $($MaxUsd.ToString([System.Globalization.CultureInfo]::InvariantCulture))" +
        " -RunMinutes $($RunMinutes.ToString([System.Globalization.CultureInfo]::InvariantCulture))"
    }
    try {
        $arguments = @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
            ("& '" + (Join-Path $Root "scripts\team\cycle.ps1") + "' -CycleId '$CycleId'" + $caps + $(if ($ExtraArguments) { " " + $ExtraArguments } else { "" }) +
            " -MaxParallel $MaxParallel -Machine '$Machine'" +
            " -ClaudePath '$powershell' -ClaudePrefixArguments '-NoProfile','-ExecutionPolicy','Bypass','-File','" +
            (Join-Path $Root "scripts\tests\lib\fake-claude.ps1") + "'" + $(if ($Research) { " -Research" } else { "" }) +
            $(if ($ResearchOnly) { " -ResearchOnly" } else { "" }) +
            $(if (-not ($Research -or $ResearchOnly -or $DefaultResearch)) { " -NoResearch" } else { "" }) +
            $(if (-not $Duty) { " -NoDuty" } else { "" }) +
            $(if ($QueueUrl) { " -QueueUrl '$QueueUrl' -QueueToken '$QueueTokenFile'" } else { "" }) +
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
    Test-Case "two fakes that write the call log at the same instant both write it: a writer waits for the other, it does not die" {
    # A harness defect, found as a test of the cycle that failed one run in three under load: the
    # second fake of a batch died on the shared log, the cycle counted a failed run and ran the
    # task again - a run the test did not expect, and no line for the one that died.
    $work = Join-Path $env:TEMP ("pagentos-fakelog-" + [guid]::NewGuid().ToString("N"))
    [void](New-Item -ItemType Directory -Force -Path $work)
    $shared = Join-Path $work "calls.log"
    $marker = Join-Path $work "trying"
    [System.IO.File]::WriteAllText($shared, "")
    $held = [System.IO.File]::Open($shared, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
    $child = $null
    try {
        $env:PAGENTOS_FAKE_CLAUDE_LOG = $shared
        $env:PAGENTOS_FAKE_CLAUDE_APPEND_ONLY = '{"role":"probe"}'
        $env:PAGENTOS_FAKE_CLAUDE_APPEND_MARKER = $marker
        $child = Start-Process -FilePath $powershell -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + $fakeSource + '"')) -PassThru -WindowStyle Hidden
        $deadline = [datetime]::UtcNow.AddSeconds(90)
        while (-not (Test-Path -LiteralPath $marker)) {
            if ([datetime]::UtcNow -gt $deadline) { throw "the fake did not reach its write" }
            Start-Sleep -Milliseconds 50
        }
        Start-Sleep -Milliseconds 500
        $child.Refresh()
        Assert-True -Condition (-not $child.HasExited) -Because "while somebody else holds the log the writer WAITS (it used to die on the sharing violation)"
    }
    finally {
        $held.Dispose()
        foreach ($name in @("PAGENTOS_FAKE_CLAUDE_LOG", "PAGENTOS_FAKE_CLAUDE_APPEND_ONLY", "PAGENTOS_FAKE_CLAUDE_APPEND_MARKER")) { Remove-Item -Path "Env:\$name" -ErrorAction SilentlyContinue }
    }
    try {
        Assert-True -Condition ($child.WaitForExit(60000)) -Because "and it ends once the log is free"
        Assert-Equal -Expected 0 -Actual $child.ExitCode -Because "having written its line"
        Assert-Equal -Expected '{"role":"probe"}' -Actual (@(Get-Content -LiteralPath $shared -Encoding UTF8 | Where-Object { $_.Trim() }) -join "|") -Because "the line is in the log, once"
    }
    finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}

    function Read-Marker {
        <# A marker of the fake, read again while another process holds it open (the inspector,
           2026-10-03: ReadAllText refused a freshly renamed .started 3 times in 21 runs under
           load - most likely a scanner or indexer opening the new file). The bound is a hang
           guard, not an assertion. #>
        param([string]$Path, [int]$Seconds = 60)
        $deadline = [datetime]::UtcNow.AddSeconds($Seconds)
        while ($true) {
            try { return (Read-TeamJson -Path $Path) }
            catch {
                # A .NET call's failure arrives wrapped (MethodInvocationException): look down the chain.
                $sharing = $false
                for ($cause = $_.Exception; $cause; $cause = $cause.InnerException) {
                    if ($cause -is [System.IO.IOException] -or $cause -is [System.UnauthorizedAccessException]) { $sharing = $true }
                }
                if (-not $sharing -or [datetime]::UtcNow -gt $deadline) { throw }
                Start-Sleep -Milliseconds 50
            }
        }
    }

    Test-Case "a marker another process holds open is read once it is free: the reader waits inside its hang guard, it does not fail" {
        $work = Join-Path $env:TEMP ("pagentos-markerlock-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
        [void]$sandboxes.Add($work)
        [void](New-Item -ItemType Directory -Force -Path $work)
        $marker = Join-Path $work "inspector-task-one.started"
        [System.IO.File]::WriteAllText($marker, '{"in_flight":"task-one:inspector"}')
        $held = Join-Path $work "held"
        $release = Join-Path $work "release"
        # The holder lets go 1.5 s after it is told to: the read below starts while it still holds.
        $script = "`$f = [System.IO.File]::Open('$marker', 'Open', 'Read', 'None'); [System.IO.File]::WriteAllText('$held', 'held'); " +
            "`$until = [datetime]::UtcNow.AddSeconds(60); while (-not (Test-Path -LiteralPath '$release') -and [datetime]::UtcNow -lt `$until) { Start-Sleep -Milliseconds 50 }; " +
            "Start-Sleep -Milliseconds 1500; `$f.Dispose()"
        $holder = Start-Process -FilePath $powershell -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($script))) -PassThru -WindowStyle Hidden
        try {
            $deadline = [datetime]::UtcNow.AddSeconds(60)
            while (-not (Test-Path -LiteralPath $held)) {
                if ([datetime]::UtcNow -gt $deadline -or $holder.HasExited) { throw "the holder did not open the marker (hang guard 60 s)" }
                Start-Sleep -Milliseconds 50
            }
            $refused = $false
            try { [void](Read-TeamJson -Path $marker) } catch { $refused = $true }
            Assert-True -Condition $refused -Because "the lock is real: a plain read is refused while it is held (the failure the inspector saw)"
            [System.IO.File]::WriteAllText($release, "go")
            Assert-Equal -Expected "task-one:inspector" -Actual ([string](Read-Marker -Path $marker).in_flight) -Because "the marker reader waits for the holder and reads the marker whole"
            Assert-True -Condition ($holder.WaitForExit(60000)) -Because "the holder ended (hang guard 60 s)"
        }
        finally { if (-not $holder.HasExited) { $holder.Kill() } }
    }

    function Start-FakeAlone {
        <# The fake as the cycle starts it - role file, card on standard input - with no cycle around it. #>
        param([string]$Work, [string]$Role, [string]$TaskId)
        $info = New-Object System.Diagnostics.ProcessStartInfo
        $info.FileName = $powershell
        $info.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $fakeSource + '" -p --append-system-prompt-file "' + (Join-Path $Work "$Role.md") + '"'
        $info.WorkingDirectory = $Work
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardInput = $true
        $info.RedirectStandardOutput = $true
        $child = [System.Diagnostics.Process]::Start($info)
        $child.StandardInput.Write("# Task`n- id: $TaskId`n")
        $child.StandardInput.Close()
        return [pscustomobject]@{ Process = $child; Output = $child.StandardOutput.ReadToEndAsync() }
    }

    Test-Case "the fake's barrier: a run told to wait writes its 'started' marker, does not end before the file appears, ends after it; with no barrier its answer is as before and no marker is written" {
        $work = Join-Path $env:TEMP ("pagentos-fakebarrier-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
        [void]$sandboxes.Add($work)
        $markers = Join-Path $work "markers"
        [void](New-Item -ItemType Directory -Force -Path $markers)
        $open = Join-Path $work "open"
        $names = @("PAGENTOS_FAKE_CLAUDE_SCENARIO", "PAGENTOS_FAKE_CLAUDE_MARKERS", "PAGENTOS_FAKE_CLAUDE_BARRIER")
        # The answer of today's fake to this card (inspector, approve, no --output-format): no clock in it.
        $expected = '{"type":"result","subtype":"success","is_error":false,"result":"' + ((1..60 | ForEach-Object { "line $_ of the inspection" }) -join '\n') + '\nAPPROVE","total_cost_usd":0.25}'
        try {
            $env:PAGENTOS_FAKE_CLAUDE_SCENARIO = "approve"
            $plain = Start-FakeAlone -Work $work -Role "inspector" -TaskId "task-plain"
            Assert-True -Condition ($plain.Process.WaitForExit(60000)) -Because "with no barrier it ends by itself (hang guard 60 s)"
            Assert-Equal -Expected $expected -Actual $plain.Output.Result -Because "with no barrier configured the answer is byte for byte the one it always was"
            Assert-Equal -Expected 0 -Actual @(Get-ChildItem -LiteralPath $markers).Count -Because "and no marker is written without PAGENTOS_FAKE_CLAUDE_MARKERS"

            $env:PAGENTOS_FAKE_CLAUDE_MARKERS = $markers
            $env:PAGENTOS_FAKE_CLAUDE_BARRIER = "worker:task-other=$open,inspector:task-one=$open"
            $held = Start-FakeAlone -Work $work -Role "inspector" -TaskId "task-one"
            $deadline = [datetime]::UtcNow.AddSeconds(60)
            while (-not (Test-Path -LiteralPath (Join-Path $markers "inspector-task-one.started"))) {
                if ([datetime]::UtcNow -gt $deadline -or $held.Process.HasExited) { throw "the run wrote no 'started' marker (hang guard 60 s)" }
                Start-Sleep -Milliseconds 50
            }
            Assert-Equal -Expected "task-one:inspector" -Actual (Read-Marker -Path (Join-Path $markers "inspector-task-one.started")).in_flight -Because "the marker names the runs in flight - this one"
            Assert-True -Condition (-not $held.Process.WaitForExit(3000)) -Because "the file is not there: the run does not end"
            Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $markers "inspector-task-one.ended"))) -Because "nor does it say it ended"
            $opened = [datetime]::UtcNow
            [System.IO.File]::WriteAllText($open, "open")
            Assert-True -Condition ($held.Process.WaitForExit(60000)) -Because "the file is there: the run ends (hang guard 60 s)"
            $ended = Read-Marker -Path (Join-Path $markers "inspector-task-one.ended")
            Assert-Equal -Expected "file" -Actual $ended.barrier -Because "it was the file that let it go, not the guard"
            # The ORDER, not a lag: it let go after the file was written (a one-second ceiling here
            # read 1.07 s under load, 2026-10-03). How soon is bounded by the hang guard above only.
            $released = [datetime]::Parse([string]$ended.released_at, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AdjustToUniversal)
            Assert-True -Condition ($released -ge $opened) -Because "it let go after the file appeared, not before: released $($released.ToString('o')), file written from $($opened.ToString('o'))"
            Assert-Equal -Expected $expected -Actual $held.Output.Result -Because "and it answers as the scenario says"
        }
        finally { foreach ($name in $names) { Remove-Item -Path "Env:\$name" -ErrorAction SilentlyContinue } }
    }

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

    Test-Case "with -TestTeam the test team's round runs beside the cycle in its own process; the software team's workers keep working" {
        # test-team (the owner, 2026-10-03): two teams, separate. The round is a stand-in that
        # writes what it was started with; the software tasks run on their own seats as ever.
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"), (New-Task -Id "task-two"))
        $marker = Join-Path $root "round.marker"
        $fakeRound = Join-Path $root "fake-round.ps1"
        [System.IO.File]::WriteAllText($fakeRound, "[System.IO.File]::WriteAllText('$marker', (`$args -join ' '))", (New-Object System.Text.UTF8Encoding($false)))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -ExtraArguments "-TestTeam -TestRoundScript '$fakeRound'"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        for ($i = 0; $i -lt 50 -and -not (Test-Path -LiteralPath $marker); $i++) { Start-Sleep -Milliseconds 200 }
        Assert-True -Condition (Test-Path -LiteralPath $marker) -Because "the round was started: $($run.StdOut)"
        $started = [System.IO.File]::ReadAllText($marker)
        Assert-True -Condition ($started -match '-Round t-c1\b') -Because "its own round, named after the cycle: $started"
        Assert-True -Condition ($started -match [regex]::Escape((Join-Path $root "team"))) -Because "the same team root: $started"
        Assert-Equal -Expected "merged,merged" -Actual ((@(Get-TeamTasks -Queue $run.Queue) | ForEach-Object { $_.state }) -join ",") -Because "both software tasks were worked on: $($run.StdOut)"
        Assert-Equal -Expected 4 -Actual @($run.Calls).Count -Because "a worker and an inspector each, no software seat for the test team"
        Assert-True -Condition ($run.StdOut -match "test ekibi turu t-c1") -Because "the cycle says it: $($run.StdOut)"

        Remove-Item -LiteralPath $marker -Force
        $plain = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $plain -Scenario "approve" -ExtraArguments "-TestRoundScript '$fakeRound'"
        Start-Sleep -Milliseconds 800
        Assert-True -Condition (-not (Test-Path -LiteralPath $marker)) -Because "without -TestTeam no round starts"
    }

    # test-rounds-after-release-and-hourly (the owner, 2026-10-06: 'neden testçiler iş bekliyor'): a
    # round also after every release that reaches staging and every 'test_round_every_hours' while
    # the cycle lives; never two at once; each its own name. The cycle is kept alive by a worker run
    # of ten-odd seconds; staging is a file the stand-in round itself rewrites (no clock in a test).
    function Invoke-RoundCycle {
        param([string]$Root, [string]$RoundBody, [double]$EveryHours, [int]$WorkerSeconds = 12)
        $staging = Join-Path $Root "staging-health.json"
        [System.IO.File]::WriteAllText($staging, '{"release":{"version":"aaaaaaa"}}', (New-Object System.Text.UTF8Encoding($false)))
        Write-TeamJson -Path (Join-Path $Root "team\cycle-settings.json") -Document ([pscustomobject]@{ max_parallel = 2; test_parallel = 4; test_round_every_hours = $EveryHours })
        $fakeRound = Join-Path $Root "fake-round.ps1"
        [System.IO.File]::WriteAllText($fakeRound, $RoundBody.Replace("@ROOT@", $Root), (New-Object System.Text.UTF8Encoding($true)))
        $env:PAGENTOS_FAKE_CLAUDE_SECONDS = "worker:*=$WorkerSeconds"
        try {
            return Invoke-Cycle -Root $Root -Scenario "approve" -ExtraArguments "-TestTeam -TestRoundScript '$fakeRound' -StagingHealthUrl '$staging' -StagingPollSeconds 1"
        }
        finally { Remove-Item Env:\PAGENTOS_FAKE_CLAUDE_SECONDS -ErrorAction SilentlyContinue }
    }
    function Get-StartedRounds {
        param([string]$Root)
        $path = Join-Path $Root "rounds.log"
        for ($i = 0; $i -lt 10 -and -not (Test-Path -LiteralPath $path); $i++) { Start-Sleep -Milliseconds 200 }
        if (-not (Test-Path -LiteralPath $path)) { return @() }
        return @([System.IO.File]::ReadAllLines($path) | Where-Object { $_ })
    }
    # Writes its -Round to rounds.log; the FIRST round turns staging to a new sha (a release landed).
    $releasingRound = @'
$i = [array]::IndexOf($args, '-Round'); $name = $args[$i + 1]
[System.IO.File]::AppendAllText('@ROOT@\rounds.log', $name + "`r`n")
$staging = '@ROOT@\staging-health.json'
if ([System.IO.File]::ReadAllText($staging) -match 'aaaaaaa') { [System.IO.File]::WriteAllText($staging, '{"release":{"version":"bbbbbbb"}}') }
'@

    Test-Case "test rounds: one at the start, another when the staging sha changes, each with its own name" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-RoundCycle -Root $root -RoundBody $releasingRound -EveryHours 100
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $rounds = @(Get-StartedRounds -Root $root)
        Assert-Equal -Expected "t-c1-1,t-c1-2" -Actual ($rounds -join ",") -Because "the start's round, then the release's (and no third: staging did not change again): $($run.StdOut)"
        Assert-True -Condition ($run.StdOut -match "staging") -Because "the cycle says why the second round started: $($run.StdOut)"
    }

    Test-Case "test rounds: 'test_round_every_hours' starts another while the cycle lives; the names differ" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        # Staging never changes here: only the interval (0.0008 h = about 3 s) can start a round.
        $steady = $releasingRound.Replace("if ([System.IO.File]", "if (`$false -and [System.IO.File]")
        $run = Invoke-RoundCycle -Root $root -RoundBody $steady -EveryHours 0.0008
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $rounds = @(Get-StartedRounds -Root $root)
        Assert-True -Condition (@($rounds).Count -ge 2) -Because "the interval started more rounds than the start's one: $($rounds -join ',') / $($run.StdOut)"
        Assert-Equal -Expected @($rounds).Count -Actual @($rounds | Select-Object -Unique).Count -Because "every round its own name: $($rounds -join ',')"
        Assert-Equal -Expected "t-c1-1" -Actual $rounds[0] -Because "numbered from one"

        # With the interval at 0 and staging steady, the start's round is the only one.
        $off = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-RoundCycle -Root $off -RoundBody $steady -EveryHours 0 -WorkerSeconds 6
        Assert-Equal -Expected "t-c1-1" -Actual (@(Get-StartedRounds -Root $off) -join ",") -Because "0 = no interval: $($run.StdOut)"
    }

    Test-Case "test rounds: a round still running blocks a second one, whatever staging and the interval say" {
        # The round in flight is one an earlier cycle process started: the test starts it and names
        # it in the lock (a round the cycle starts itself would inherit the cycle's output pipe and
        # hold the harness). Staging turns over mid-cycle and the interval is due all along.
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $holder = Start-Process -FilePath $powershell -ArgumentList @("-NoProfile", "-Command", "Start-Sleep -Seconds 120") -WindowStyle Hidden -PassThru
        $flip = $null
        try {
            [void](New-Item -ItemType Directory -Force -Path (Join-Path $root "team\reports"))
            Write-TeamJson -Path (Join-Path $root "team\reports\test-round.json") -Document ([pscustomobject]@{
                    pid = $holder.Id; process_started = [string]$holder.StartTime.ToUniversalTime().Ticks; round = "t-c0-1"; cycle_id = "c0"; why = "test"
                })
            $staging = Join-Path $root "staging-health.json"
            $flip = Start-Job -ArgumentList $staging -ScriptBlock {
                param($Path)
                Start-Sleep -Seconds 5
                [System.IO.File]::WriteAllText($Path, '{"release":{"version":"ccccccc"}}')
            }
            $steady = $releasingRound.Replace("if ([System.IO.File]", "if (`$false -and [System.IO.File]")
            $run = Invoke-RoundCycle -Root $root -RoundBody $steady -EveryHours 0.0008
            Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
            Assert-Equal -Expected "ccccccc" -Actual ([string](Get-Content -Raw -LiteralPath $staging | ConvertFrom-Json).release.version) -Because "staging did change while the cycle lived (else this case proves nothing)"
            Assert-Equal -Expected "" -Actual (@(Get-StartedRounds -Root $root) -join ",") -Because "the running round is the lock - at the start, on the release, on the interval: $($run.StdOut)"
            Assert-True -Condition ($run.StdOut -match "turu zaten") -Because "the cycle says why: $($run.StdOut)"

            # The round ends: the next cycle starts one again.
            Stop-Process -Id $holder.Id -Force
            $holder.WaitForExit(10000) | Out-Null
            $again = Invoke-RoundCycle -Root $root -RoundBody $steady -EveryHours 100 -WorkerSeconds 1
            Assert-Equal -Expected "t-c1-1" -Actual (@(Get-StartedRounds -Root $root) -join ",") -Because "the lock went with its process: $($again.StdOut)"
        }
        finally {
            if ($null -ne $flip) { $flip | Wait-Job -Timeout 30 | Out-Null; $flip | Remove-Job -Force }
            Stop-Process -Id $holder.Id -Force -ErrorAction SilentlyContinue
        }
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
        Assert-True -Condition ($run.Report -match "tahmini 0[.,]50 USD / tavan 20[.,]00 USD") -Because "two runs of 0.25, as an estimate: $($run.Report)"
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

    Test-Case "a trial object is one Turkish line with its sentence, machine and state, and the old string still prints" {
        $try = New-Task -Id "try-one" -State "awaiting_real_evidence"
        $trial = [pscustomobject]@{
            id = "ses-saat"; sentence = "Saat kaç?"; machine = "ev PC"; expect = "saati Türkçe söyler"
            verdict = $null; said = $null; at = $null
        }
        $try | Add-Member -NotePropertyName owner_trials -NotePropertyValue @($trial, "Telefonda Ofis'i aç")
        $root = New-Sandbox -Tasks @($try)
        $run = Invoke-Cycle -Root $root -Scenario "approve"
        Assert-True -Condition ($run.Report -match "- try-one: ""Saat kaç\?"" — makine: ev PC — beklenen: saati Türkçe söyler — durum: denenmedi") -Because $run.Report
        Assert-True -Condition ($run.Report.Contains("- try-one: Telefonda Ofis'i aç")) -Because "the old string form prints as before: $($run.Report)"
        Assert-True -Condition (-not $run.Report.Contains("@{")) -Because "no raw PowerShell object: $($run.Report)"
    }

    Test-Case "a decided trial names its verdict in Turkish" {
        $passed = [pscustomobject]@{ id = "a"; sentence = "S"; machine = "M"; expect = "E"; verdict = "oldu"; said = $null; at = "2026-10-03T00:00:00Z" }
        $failed = [pscustomobject]@{ id = "b"; sentence = "S"; machine = "M"; expect = "E"; verdict = "olmadi"; said = "ses yok"; at = "2026-10-03T00:00:00Z" }
        $rows = @((Format-TeamOwnerTrial -TaskId "t" -Trial $passed), (Format-TeamOwnerTrial -TaskId "t" -Trial $failed))
        Assert-Equal -Expected 't: "S" — makine: M — beklenen: E — durum: oldu' -Actual $rows[0] -Because "oldu"
        Assert-Equal -Expected 't: "S" — makine: M — beklenen: E — durum: olmadı (ses yok)' -Actual $rows[1] -Because "olmadı with the owner's words"
    }

    Test-Case "a release the owner approved is told apart from one that waits, and still starts nobody" {
        $approved = New-Task -Id "ship-two" -State "awaiting_release"
        $approved | Add-Member -NotePropertyName release_approved -NotePropertyValue $true
        $approved | Add-Member -NotePropertyName release_approved_at -NotePropertyValue "2026-09-30T07:00:00Z"
        $approved | Add-Member -NotePropertyName release_approved_by -NotePropertyValue "shell"
        $root = New-Sandbox -Tasks @((New-Task -Id "ship-one" -State "awaiting_release"), $approved)
        $run = Invoke-Cycle -Root $root -Scenario "approve"
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "an approved release is the lead's to run, not a worker's"
        Assert-Equal -Expected "awaiting_release" -Actual (Get-TaskById -Queue $run.Queue -Id "ship-two").state -Because "the state did not move"
        Assert-True -Condition ($run.Report -match "YAYIN ONAYLANDI \(2026-09-30T07:00:00Z\): ship-two") -Because $run.Report
        Assert-True -Condition ($run.Report -match "YAYIN: ship-one") -Because "the one that waits still waits"
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
        Assert-Equal -Expected $false -Actual ([bool]$workers[0].came_back) -Because "the first run is not a return"
        Assert-Equal -Expected $true -Actual ([bool]$workers[1].came_back) -Because "the second run is told why it came back"
    }

    Test-Case "a task the lead returned by hand carries the lead's reason into the card" {
        $task = New-Task -Id "task-one" -State "returned"
        $task | Add-Member -NotePropertyName reason -NotePropertyValue "the area was wrong; also write the flag"
        $card = New-TeamTaskCard -Task $task -Role "worker" -CycleId "c1"
        Assert-True -Condition ($card.Contains("## Why this task came back")) -Because $card
        Assert-True -Condition ($card.Contains("also write the flag")) -Because "the words themselves"
        $fresh = New-TeamTaskCard -Task (New-Task -Id "task-two") -Role "worker" -CycleId "c1"
        Assert-True -Condition (-not $fresh.Contains("came back")) -Because "a task that never came back says nothing of it"
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
        # Seats are per role (cycle-seat-pool): after task-one's worker (4 USD, under the cap) its
        # inspection and task-two's worker start side by side. Whichever of the two ends first,
        # the money is spent by then (8, or 12 when both ended) and nothing further starts.
        Assert-Equal -Expected "inspector:task-one,worker:task-one,worker:task-two" -Actual (@($run.Calls | ForEach-Object { "$($_.role):$($_.task)" } | Sort-Object) -join ",") -Because "4 is under 6, 8 is over: task-two's inspection never started"
        Assert-True -Condition ($run.Report -match "bütçe tavanı: (8|12)[.,]00 / 6[.,]00 USD") -Because $run.Report
        Assert-Equal -Expected 1 -Actual ([regex]::Matches($run.Report, "bütçe tavanı")).Count -Because "said once, however many refills followed: $($run.Report)"
        Assert-True -Condition ([double]$run.Calls[1].budget -le 2.0) -Because "the second run was given what was left, not its own cap: $($run.Calls[1].budget)"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "a stopped cycle releases the lock"
    }

    Test-Case "without caps (the defaults) a run gets no budget flag, the report says 'tavan yok', and the work is done" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -NoCaps
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "" -Actual ([string]$run.Calls[0].budget) -Because "no --max-budget-usd was passed: $($run.Calls[0] | ConvertTo-Json -Compress)"
        Assert-True -Condition ($run.Report -match "tahmini 0[.,]50 USD \(tavan yok\)") -Because $run.Report
    }

    Test-Case "the usage limit: the task goes back, nothing is counted against it, the cycle waits it out and finishes the work" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "limited" -NoCaps
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $task = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "merged" -Actual $task.state -Because ($run.StdOut + $run.StdErr + $run.Report)
        Assert-Equal -Expected "worker,worker,inspector" -Actual (@($run.Calls | ForEach-Object { $_.role }) -join ",") -Because "the limited run, the run after the wait, the inspection"
        Assert-Equal -Expected 0 -Actual ([int](Get-TeamProperty -InputObject $task -Name "failed_runs" -Default 0)) -Because "the limit is not the task's failure"
        Assert-True -Condition ($run.Report -match "Max kullanım limiti: .* sıfırlanmasına kadar beklendi") -Because $run.Report
        Assert-True -Condition ($run.Report -match "task-one / worker: .*Max kullanım limiti") -Because "the limited run is in the run list: $($run.Report)"
    }

    Test-Case "the usage limit without waiting: the cycle stops, says how to continue, and the task is where it was" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "limited" -NoCaps -ExtraArguments '-WaitForUsageLimit:$false'
        $task = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "assigned" -Actual $task.state -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected 1 -Actual @($run.Calls).Count -Because "nothing after the limit"
        Assert-True -Condition ($run.Report -match "döngü: Max kullanım limiti; sıfırlanma .*aynı -CycleId ile yeniden başlat") -Because $run.Report
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "a stopped cycle releases the lock"
    }

    # ------------------------------------------------------------------ the live status and the safe stop
    function Use-FakeHooks {
        param([hashtable]$Environment, [scriptblock]$Body)
        foreach ($name in @($Environment.Keys)) { Set-Item -Path "Env:\$name" -Value ([string]$Environment[$name]) }
        try { & $Body }
        finally { foreach ($name in @($Environment.Keys)) { Remove-Item -Path "Env:\$name" -ErrorAction SilentlyContinue } }
    }

    Test-Case "the live status: while two runs are in flight team/status.json names the cycle and both runs; after the cycle no run is left and the estimate is the report's" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/area")), (New-Task -Id "task-two" -Area @("src/area2")))
        $snapshots = Join-Path $root "snapshots"
        $hooks = @{ PAGENTOS_FAKE_CLAUDE_SNAPSHOT = $snapshots; PAGENTOS_FAKE_CLAUDE_STATUS = (Join-Path $root "team\status.json") }
        $run = $null
        Use-FakeHooks -Environment $hooks -Body { $script:statusRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 2 }
        $run = $script:statusRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $seen = Read-TeamJson -Path (Join-Path $snapshots "worker-task-one.json")
        Assert-Equal -Expected "c1" -Actual $seen.cycle_id -Because "the cycle is named"
        Assert-Equal -Expected "MAIL" -Actual $seen.machine -Because "and its machine"
        Assert-True -Condition ([int]$seen.pid -gt 0) -Because "and the process that holds the lock"
        Assert-True -Condition ([string]$seen.started_at -match '^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$') -Because "started_at: $($seen.started_at)"
        $runs = @($seen.runs)
        Assert-Equal -Expected "task-one:worker,task-two:worker" -Actual (@($runs | ForEach-Object { "$($_.task):$($_.role)" } | Sort-Object) -join ",") -Because "both runs in flight: $($seen | ConvertTo-Json -Compress)"
        foreach ($item in $runs) { Assert-True -Condition ([string]$item.started_at -match '^\d{4}-') -Because "each run says when it started" }
        Assert-Equal -Expected "ok" -Actual $seen.usage_limit.state -Because "no limit"
        $final = Read-TeamJson -Path (Join-Path $root "team\status.json")
        Assert-Equal -Expected 0 -Actual @($final.runs).Count -Because "nothing in flight after the cycle"
        $estimate = [regex]::Match($run.Report, 'tahmini (\d+[.,]\d+) USD').Groups[1].Value -replace ',', '.'
        Assert-Equal -Expected ([double]::Parse($estimate, [System.Globalization.CultureInfo]::InvariantCulture)) -Actual ([double]$final.estimated_usd) -Because "the report's estimate: $($run.Report)"
        Assert-True -Condition ([double]$final.estimated_usd -gt 0) -Because "four runs at 0.25"
    }

    Test-Case "an approved task's worker starts in the SAME batch as another task's integrator: nobody waits a round for a state change" {
        # 2026-10-01, the owner looking at the Ofis page: one integrator working, three worker seats
        # empty, eight approved tasks ready. The loop moved 'approved' to 'assigned' and only
        # looked at the task again in the NEXT round - after the integrators of that round had
        # finished (web research: minutes). A task is looked at again in the pass that moved it.
        $plain = New-Task -Id "task-plain" -Area @("src/area")
        $withPlan = New-Task -Id "task-study" -Area @("src/area2")
        $withPlan | Add-Member -NotePropertyName "needs_integration" -NotePropertyValue $true
        $root = New-Sandbox -Tasks @($withPlan, $plain)
        $snapshots = Join-Path $root "snapshots"
        $hooks = @{ PAGENTOS_FAKE_CLAUDE_SNAPSHOT = $snapshots; PAGENTOS_FAKE_CLAUDE_STATUS = (Join-Path $root "team\status.json") }
        Use-FakeHooks -Environment $hooks -Body { $script:sameBatch = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 2 }
        Assert-Equal -Expected 0 -Actual $script:sameBatch.ExitCode -Because ($script:sameBatch.StdOut + $script:sameBatch.StdErr)
        $seen = Read-TeamJson -Path (Join-Path $snapshots "integrator-task-study.json")
        Assert-Equal -Expected "task-plain:worker,task-study:integrator" -Actual (@(@($seen.runs) | ForEach-Object { "$($_.task):$($_.role)" } | Sort-Object) -join ",") -Because "while the integrator studies, the other task's worker is already at work: $($seen | ConvertTo-Json -Depth 5 -Compress)"
        Assert-Equal -Expected "merged,merged" -Actual (@(Get-TeamTasks -Queue $script:sameBatch.Queue | ForEach-Object { $_.state }) -join ",") -Because "and both end merged"
    }

    Test-Case "the live status: a finished run is gone from the list before the next one starts, and the estimate has risen" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/area")), (New-Task -Id "task-two" -Area @("src/area2")))
        $snapshots = Join-Path $root "snapshots"
        $hooks = @{ PAGENTOS_FAKE_CLAUDE_SNAPSHOT = $snapshots; PAGENTOS_FAKE_CLAUDE_STATUS = (Join-Path $root "team\status.json") }
        Use-FakeHooks -Environment $hooks -Body { $script:seqRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 1 }
        Assert-Equal -Expected 0 -Actual $script:seqRun.ExitCode -Because ($script:seqRun.StdOut + $script:seqRun.StdErr)
        $second = Read-TeamJson -Path (Join-Path $snapshots "worker-task-two.json")
        # One worker seat: task-two's worker starts when task-one's has ended. (Task-one's
        # inspection has a seat of its own since cycle-seat-pool and may be listed beside it.)
        $listed = @($second.runs | ForEach-Object { "$($_.task):$($_.role)" })
        Assert-True -Condition ($listed -contains "task-two:worker" -and $listed -notcontains "task-one:worker") -Because "the finished worker is gone from the list, the one in flight is in it: $($second | ConvertTo-Json -Compress)"
        Assert-True -Condition ([double]$second.estimated_usd -ge 0.25) -Because "the first run's cost is already in: $($second.estimated_usd)"
    }

    Test-Case "the live status: the heartbeat refreshes updated_at while one long run goes on" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $beat = Join-Path $root "heartbeat.txt"
        $hooks = @{ PAGENTOS_FAKE_CLAUDE_HEARTBEAT = $beat; PAGENTOS_FAKE_CLAUDE_STATUS = (Join-Path $root "team\status.json"); PAGENTOS_CYCLE_STATUS_TICK_SECONDS = 1 }
        Use-FakeHooks -Environment $hooks -Body { $script:beatRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 1 }
        Assert-Equal -Expected 0 -Actual $script:beatRun.ExitCode -Because ($script:beatRun.StdOut + $script:beatRun.StdErr)
        $stamps = @(Get-Content -LiteralPath $beat -Encoding UTF8 | Where-Object { $_.Trim() } | Sort-Object -Unique)
        Assert-True -Condition (@($stamps).Count -ge 3) -Because "a seven-second run with a one-second tick shows several updated_at values, not just the start's: $($stamps -join ' ')"
    }

    Test-Case "the live status: the usage limit is 'stopped' with its reset time when the cycle stops for it, 'ok' when it was waited out" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "limited" -NoCaps -ExtraArguments '-WaitForUsageLimit:$false'
        $final = Read-TeamJson -Path (Join-Path $root "team\status.json")
        Assert-Equal -Expected "stopped" -Actual $final.usage_limit.state -Because ($run.StdOut + $run.StdErr)
        Assert-True -Condition ([string]$final.usage_limit.resets_at -match '^\d{4}-') -Because "the reset time is kept: $($final | ConvertTo-Json -Compress)"
        Assert-Equal -Expected 0 -Actual @($final.runs).Count -Because "no run left"
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "limited" -NoCaps
        $final = Read-TeamJson -Path (Join-Path $root "team\status.json")
        Assert-Equal -Expected "ok" -Actual $final.usage_limit.state -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected $null -Actual $final.usage_limit.resets_at -Because "nothing to wait for any more"
    }

    Test-Case "the safe stop: with team/stop.flag present before the second batch the first run is recorded, nothing else starts, the stop line is in the report, the flag is gone and the lock is released" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/area")), (New-Task -Id "task-two" -Area @("src/area2")))
        $flag = Join-Path $root "team\stop.flag"
        $run = $null
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_STOPFLAG = $flag } -Body { $script:stopRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 1 }
        $run = $script:stopRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "worker" -Actual (@($run.Calls | ForEach-Object { $_.role }) -join ",") -Because "the run in flight finished; no further run started"
        $one = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "inspecting" -Actual $one.state -Because "the finished worker waits for the next cycle's inspector"
        Assert-Equal -Expected 1 -Actual @($one.reports).Count -Because "the finished run is recorded"
        Assert-Equal -Expected "assigned" -Actual (Get-TaskById -Queue $run.Queue -Id "task-two").state -Because "the second task was only moved to assigned (no plan needed), never started"
        Assert-True -Condition ($run.Report -match 'döngü: sahip/lead durdurdu \(team/stop\.flag\); kaldığı yerden devam eder') -Because $run.Report
        Assert-True -Condition (-not (Test-Path -LiteralPath $flag)) -Because "the flag is removed"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock is released"
    }

    Test-Case "the safe stop: an inspection in flight is still merged when the flag came up during it" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/area")), (New-Task -Id "task-two" -Area @("src/area2")))
        $flag = Join-Path $root "team\stop.flag"
        $run = $null
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_STOPFLAG = $flag; PAGENTOS_FAKE_CLAUDE_STOPFLAG_ROLE = "inspector" } -Body { $script:stopRun2 = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 1 }
        $run = $script:stopRun2
        # Seats are per role (cycle-seat-pool): task-two's worker took the worker seat in the same
        # refill that started task-one's inspection - before the flag. Both were in flight when it
        # came up, both finish and are recorded, and nothing starts after it.
        Assert-Equal -Expected "inspector:task-one,worker:task-one,worker:task-two" -Actual (@($run.Calls | ForEach-Object { "$($_.role):$($_.task)" } | Sort-Object) -join ",") -Because "the runs in flight finished, nothing after the flag: no inspection of task-two"
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "an approved inspection is still merged"
        $two = Get-TaskById -Queue $run.Queue -Id "task-two"
        Assert-Equal -Expected "inspecting" -Actual $two.state -Because "the worker in flight finished; its inspection is the next cycle's"
        Assert-Equal -Expected 1 -Actual @($two.reports).Count -Because "and its run is recorded"
        Assert-True -Condition (-not (Test-Path -LiteralPath $flag)) -Because "the flag is removed"
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

    Test-Case "a task waits for its dependencies to be on main; the report says so; a dependency must exist" {
        $first = New-Task -Id "layer-one" -Area @("src/one")
        $second = New-Task -Id "layer-three" -Area @("src/three")
        $second | Add-Member -NotePropertyName "depends_on" -NotePropertyValue @("layer-one")
        Assert-Equal -Expected "layer-one" -Actual (@(Get-TeamUnmetDependencies -Task $second -Queue (New-Queue -Tasks @($first, $second))) -join ",") -Because "approved is not on main"
        $first.state = "merged"
        Assert-Equal -Expected "layer-one" -Actual (@(Get-TeamUnmetDependencies -Task $second -Queue (New-Queue -Tasks @($first, $second))) -join ",") -Because "merged is the integration branch, not main"
        $first.state = "awaiting_release"
        Assert-Equal -Expected 0 -Actual @(Get-TeamUnmetDependencies -Task $second -Queue (New-Queue -Tasks @($first, $second))).Count -Because "gated main is main"
        $orphan = New-Task -Id "orphan"
        $orphan | Add-Member -NotePropertyName "depends_on" -NotePropertyValue @("nobody")
        $problems = @(Test-TeamQueue -Queue (New-Queue -Tasks @($orphan)))
        Assert-True -Condition (@($problems | Where-Object { $_ -match "depends on 'nobody', which is not in the queue" }).Count -eq 1) -Because ($problems -join "; ")

        $first.state = "approved"
        $root = New-Sandbox -Tasks @($first, $second)
        $run = Invoke-Cycle -Root $root -Scenario "approve" -NoCaps
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "layer-one").state -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "approved" -Actual (Get-TaskById -Queue $run.Queue -Id "layer-three").state -Because "it waited: $($run.Report)"
        Assert-Equal -Expected 0 -Actual @($run.Calls | Where-Object { $_.task -eq "layer-three" }).Count -Because "no run for the waiting task"
        Assert-True -Condition ($run.Report -match "bekliyor: layer-three -> layer-one main'e girince") -Because $run.Report
    }

    Test-Case "split: a proposal that serves a roadmap row becomes its tasks and they run in the same cycle; the lead run has no Bash and no Edit" {
        $root = New-Sandbox -Tasks @((New-Proposal -Id "idea-one"))
        $run = Invoke-Cycle -Root $root -Scenario "split" -NoCaps
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $lead = @($run.Calls | Where-Object { $_.role -eq "lead" })
        Assert-Equal -Expected 1 -Actual @($lead).Count -Because "one fresh lead run for the proposal"
        $tools = @(([string]$lead[0].tools).Split(","))
        Assert-True -Condition ($tools -contains "Read" -and $tools -contains "Write") -Because "read, and write the one file: $($tools -join ',')"
        Assert-True -Condition ($tools -notcontains "Bash" -and $tools -notcontains "Edit" -and $tools -notcontains "Agent") -Because "the lead run's tools: $($tools -join ',')"
        Assert-Equal -Expected ([string]$root).ToLowerInvariant() -Actual ([string]$lead[0].cwd).ToLowerInvariant() -Because "it works in the checkout, where team/plans is"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $root "team\plans\c1-split-idea-one.json")) -Because "the lead wrote the file the cycle named"
        Assert-Equal -Expected "merged,merged" -Actual (@("idea-one-a", "idea-one-b" | ForEach-Object { (Get-TaskById -Queue $run.Queue -Id $_).state }) -join ",") -Because ($run.StdOut + $run.StdErr + $run.Report)
        Assert-Equal -Expected "lead,worker,worker,inspector,inspector" -Actual (@($run.Calls | ForEach-Object { $_.role } | Sort-Object { @("lead", "worker", "inspector").IndexOf($_) }) -join ",") -Because "split first, then the work"
        Assert-Equal -Expected "lead" -Actual $run.Calls[0].role -Because "the split is the first run of the cycle"
        $proposal = Get-TaskById -Queue $run.Queue -Id "idea-one"
        Assert-Equal -Expected "done" -Actual $proposal.state -Because "the proposal is over once it is its tasks"
        Assert-True -Condition ($proposal.reason -match "bölündü: idea-one-a, idea-one-b") -Because $proposal.reason
        Assert-Equal -Expected "team/proposals/idea-one.md" -Actual (Get-TaskById -Queue $run.Queue -Id "idea-one-a").proposal -Because "a task is traced to its proposal"
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $run.Queue).Count -Because "the queue the cycle wrote keeps the protocol"
        Assert-True -Condition ($run.Report -match "idea-one .*bölündü") -Because "the report says where the proposal went: $($run.Report)"
    }

    Test-Case "split: a proposed proposal WITH a roadmap row is split too" {
        $root = New-Sandbox -Tasks @((New-Proposal -Id "idea-one" -State "proposed"))
        $run = Invoke-Cycle -Root $root -Scenario "split" -NoCaps
        Assert-Equal -Expected 1 -Actual @($run.Calls | Where-Object { $_.role -eq "lead" }).Count -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "idea-one-a").state -Because $run.Report
    }

    Test-Case "split: a proposal without a roadmap row waits for the owner and no lead is started" {
        $root = New-Sandbox -Tasks @((New-Proposal -Id "idea-one" -State "proposed" -Row ""))
        $run = Invoke-Cycle -Root $root -Scenario "split" -NoCaps
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "nobody started"
        Assert-Equal -Expected "awaiting_owner" -Actual (Get-TaskById -Queue $run.Queue -Id "idea-one").state -Because "the first gate"
        Assert-Equal -Expected 1 -Actual @(Get-TeamTasks -Queue $run.Queue).Count -Because "nothing was appended"
    }

    $rejected = @(
        @{ Name = "an area that overlaps a task in work"; Scenario = "split-overlap"; Says = "overlaps the area of busy-one" },
        @{ Name = "a shared file"; Scenario = "split-shared"; Says = "shared file" },
        @{ Name = "a missing field"; Scenario = "split-missing"; Says = "'acceptance' is missing" },
        @{ Name = "no file written at all"; Scenario = "approve"; Says = "wrote no split" }
    )
    foreach ($case in $rejected) {
        Test-Case "split: a split with $($case.Name) is rejected whole, the reason is in the report, and the proposal stays" {
            $root = New-Sandbox -Tasks @((New-Proposal -Id "idea-one"), (New-Task -Id "busy-one" -State "assigned" -Area @("src/busy")))
            $run = Invoke-Cycle -Root $root -Scenario $case.Scenario -NoCaps
            Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
            Assert-Equal -Expected 2 -Actual @(Get-TeamTasks -Queue $run.Queue).Count -Because "not one of the tasks was queued - the good half included: $($run.Report)"
            $proposal = Get-TaskById -Queue $run.Queue -Id "idea-one"
            Assert-Equal -Expected "approved" -Actual $proposal.state -Because "the proposal stays where it was"
            Assert-Equal -Expected 1 -Actual @($run.Calls | Where-Object { $_.role -eq "lead" }).Count -Because "one try a cycle"
            Assert-Equal -Expected 0 -Actual @($run.Calls | Where-Object { $_.task -like "idea-one*" -and $_.role -ne "lead" }).Count -Because "no worker for a proposal with no area"
            Assert-True -Condition ($run.Report -match "bölme reddedildi: idea-one: .*$([regex]::Escape($case.Says))") -Because "the reason is in the report: $($run.Report)"
            Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "busy-one").state -Because "the rest of the queue ran on"
        }
    }

    Test-Case "split: a second cycle does not split the same proposal again, and a rejected one is tried once a cycle" {
        $root = New-Sandbox -Tasks @((New-Proposal -Id "idea-one"))
        $first = Invoke-Cycle -Root $root -Scenario "split" -NoCaps
        $second = Invoke-Cycle -Root $root -Scenario "split" -NoCaps -CycleId "c2"
        Assert-Equal -Expected 0 -Actual $second.ExitCode -Because ($second.StdOut + $second.StdErr)
        Assert-Equal -Expected @($first.Calls).Count -Actual @($second.Calls).Count -Because "nothing was started again, the lead included"
        Assert-Equal -Expected 3 -Actual @(Get-TeamTasks -Queue $second.Queue).Count -Because "the tasks were not added twice"
        $bad = New-Sandbox -Tasks @((New-Proposal -Id "idea-one"), (New-Task -Id "busy-one" -State "assigned" -Area @("src/busy")))
        [void](Invoke-Cycle -Root $bad -Scenario "split-overlap" -NoCaps)
        $again = Invoke-Cycle -Root $bad -Scenario "split" -NoCaps -CycleId "c2"
        Assert-Equal -Expected "done" -Actual (Get-TaskById -Queue $again.Queue -Id "idea-one").state -Because "the next cycle's lead may get it right: $($again.Report)"
    }

    # ------------------------------------------------------------------ the Proje Yöneticisi's duty (pm-duty-stopped)
    # The owner, 2026-10-03: "Böyle bulgular bulunduğunda konuyu proje yöneticisine iletsinler, proje
    # yöneticisi de sana iletsin; her seferinde bu süreci ben takip etmeyeyim." A stopped task wakes
    # ONE lead run (Read/Grep/Glob/Write); the run writes a decision file; the SCRIPT judges it whole.
    function Invoke-DutyCycle {
        param([string]$Root, [object[]]$Decisions = @(), [string]$Scenario = "approve", [string]$CycleId = "c1",
            [string]$QueueUrl = "", [string]$QueueTokenFile = "", [hashtable]$Hooks = @{})
        $cards = Join-Path $Root "duty-cards.txt"
        $environment = @{ PAGENTOS_FAKE_CLAUDE_DUTY_CARD = $cards }
        if (@($Decisions).Count -gt 0) { $environment["PAGENTOS_FAKE_CLAUDE_DUTY_JSON"] = (ConvertTo-DutyJson -Decisions $Decisions) }
        foreach ($name in @($Hooks.Keys)) { $environment[$name] = $Hooks[$name] }
        Use-FakeHooks -Environment $environment -Body {
            $script:dutyRun = Invoke-Cycle -Root $Root -Scenario $Scenario -CycleId $CycleId -NoCaps -Duty -QueueUrl $QueueUrl -QueueTokenFile $QueueTokenFile
        }
        $run = $script:dutyRun
        $text = if (Test-Path -LiteralPath $cards) { [System.IO.File]::ReadAllText($cards, [System.Text.Encoding]::UTF8) } else { "" }
        $run | Add-Member -NotePropertyName Cards -NotePropertyValue $text
        return $run
    }

    function Get-Roles {
        param($Run)
        return (@($Run.Calls | ForEach-Object { [string]$_.role }) -join ",")
    }

    # pm-stuck-run-check (the owner, 2026-10-04: "arada gerçekten işte çalışıp çalışmadıklarını da
    # kontrol etsin, iş takılmış olmasın"). Two workers sleep ("slow"); one of them is kept alive by
    # writes into its own temp folder every two seconds, as a long test suite writes. With
    # run_idle_minutes 1 the silent one is flagged, handed to the duty, and the duty's 'restart'
    # starts it again; the writing one is never flagged, however long it runs.
    Test-Case "liveness: a run that leaves no trace for run_idle_minutes is flagged in the status and handed to the duty, whose 'restart' starts it again; a long run that writes is never flagged" {
        $root = New-Sandbox -Tasks @((New-Task -Id "hung-one" -Area @("src/area")), (New-Task -Id "live-one" -Area @("src/area2")))
        Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\lib\TeamLiveness.ps1") -Destination (Join-Path $root "scripts\lib\TeamLiveness.ps1")
        $temps = Join-Path $root "run-temps"
        Write-TeamJson -Path (Join-Path $root "team\cycle-settings.json") -Document ([ordered]@{ run_idle_minutes = 1; run_temp_root = $temps })
        $snapshots = Join-Path $root "snapshots"
        $cards = Join-Path $root "duty-cards.txt"
        $decision = '{"decisions":[],"stuck":[{"run":"hung-one/worker","action":"restart","reason":"bir dakikadır iz yok; yeniden başlat"}]}'
        $hooks = @{
            PAGENTOS_FAKE_CLAUDE_SNAPSHOT = $snapshots; PAGENTOS_FAKE_CLAUDE_STATUS = (Join-Path $root "team\status.json")
            PAGENTOS_FAKE_CLAUDE_DUTY_CARD = $cards; PAGENTOS_FAKE_CLAUDE_DUTY_JSON = $decision; PAGENTOS_CYCLE_LIVENESS_SECONDS = "10"
            # Each worker run sits 100 s without a sound before it answers (and commits) as "approve".
            PAGENTOS_FAKE_CLAUDE_SECONDS = "worker:*=100"
        }
        # The live run's work: a write into its own temp folder every two seconds, for as long as the
        # cycle runs - and the hung run's too once it was started again (the restart cured it).
        $writer = Start-Job -ArgumentList $temps, (Join-Path $root "fake.log") -ScriptBlock {
            param($Temps, $Log)
            for ($i = 0; $i -lt 200; $i++) {
                $working = @("live-one-worker-*")
                $hungRuns = @(Get-Content -LiteralPath $Log -ErrorAction SilentlyContinue | Where-Object { $_ -match '"role":"worker","task":"hung-one"' }).Count
                if ($hungRuns -ge 2) { $working += "hung-one-worker-*" }
                foreach ($folder in @($working | ForEach-Object { Get-ChildItem -LiteralPath $Temps -Directory -Filter $_ -ErrorAction SilentlyContinue })) {
                    Set-Content -LiteralPath (Join-Path $folder.FullName "progress.txt") -Value $i -Encoding ASCII -ErrorAction SilentlyContinue
                }
                Start-Sleep -Seconds 2
            }
        }
        try {
            Use-FakeHooks -Environment $hooks -Body { $script:livenessRun = Invoke-Cycle -Root $root -Scenario "approve" -Duty -RunMinutes 3.6 -ExtraArguments "-CycleMinutes 3" }
        }
        finally { Stop-Job -Job $writer -ErrorAction SilentlyContinue; Remove-Job -Job $writer -Force -ErrorAction SilentlyContinue }
        $run = $script:livenessRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $card = if (Test-Path -LiteralPath $cards) { [System.IO.File]::ReadAllText($cards, [System.Text.Encoding]::UTF8) } else { "" }
        # (The fake reads its card in the console's code page: only the ASCII parts are compared.)
        Assert-True -Condition ($card -match '(?m)^## Tak\S* olabilecek ' -and $card -match '(?m)^- hung-one/worker: \d+ dk iz yok') -Because "the silent run is handed to the duty:`n$card`n$($run.Report)"
        Assert-True -Condition (-not $card.Contains("live-one/worker")) -Because "the run that writes is never handed:`n$card"
        $workers = @($run.Calls | Where-Object { $_.role -eq "worker" } | ForEach-Object { [string]$_.task })
        Assert-Equal -Expected 2 -Actual @($workers | Where-Object { $_ -eq "hung-one" }).Count -Because "the duty's restart started it again: $($workers -join ',')`n$($run.Report)"
        Assert-Equal -Expected 1 -Actual @($workers | Where-Object { $_ -eq "live-one" }).Count -Because "the sibling was never restarted: $($workers -join ',')"
        Assert-True -Condition ($run.Report.Contains("takılmış olabilir: hung-one/worker") -and -not $run.Report.Contains("takılmış olabilir: live-one/worker")) -Because $run.Report
        # The status a reader saw while the duty ran: both runs measured, only the silent one idle.
        $seen = Read-TeamJson -Path (Join-Path $snapshots "lead-.json")
        Assert-Equal -Expected 1 -Actual ([int]$seen.run_idle_minutes) -Because "the cycle's bound is in the status"
        $hung = @($seen.runs | Where-Object { $_.task -eq "hung-one" })[0]
        $live = @($seen.runs | Where-Object { $_.task -eq "live-one" })[0]
        Assert-True -Condition ([int]$hung.idle_minutes -ge 1 -and [string]$hung.last_activity_at) -Because "the silent run: $($hung | ConvertTo-Json -Compress)"
        Assert-Equal -Expected 0 -Actual ([int]$live.idle_minutes) -Because "the writing run: $($live | ConvertTo-Json -Compress)"
    }

    # The inspector's probe (return 3): the PM decides on the card's minutes, but the silent run
    # starts writing every two seconds while the duty runs. Its 'restart' is NOT applied - the run
    # is looked at again first - and the report says it came back to life.
    Test-Case "liveness: a run that writes again while the duty decides is not restarted - the PM's 'restart' is turned into a wait ('yeniden canlandı')" {
        $root = New-Sandbox -Tasks @((New-Task -Id "hung-one" -Area @("src/area")))
        Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\lib\TeamLiveness.ps1") -Destination (Join-Path $root "scripts\lib\TeamLiveness.ps1")
        $temps = Join-Path $root "run-temps"
        Write-TeamJson -Path (Join-Path $root "team\cycle-settings.json") -Document ([ordered]@{ run_idle_minutes = 1; run_temp_root = $temps })
        $cards = Join-Path $root "duty-cards.txt"
        $decision = '{"decisions":[],"stuck":[{"run":"hung-one/worker","action":"restart","reason":"bir dakikadır iz yok; yeniden başlat"}]}'
        $hooks = @{
            PAGENTOS_FAKE_CLAUDE_DUTY_CARD = $cards; PAGENTOS_FAKE_CLAUDE_DUTY_JSON = $decision; PAGENTOS_CYCLE_LIVENESS_SECONDS = "10"
            PAGENTOS_FAKE_CLAUDE_SECONDS = "worker:*=170,lead:*=30"
        }
        # Silent until the duty run starts (its line in the fake's log); from then on a write every
        # two seconds - while the PM is still deciding on the card's minutes.
        $writer = Start-Job -ArgumentList $temps, (Join-Path $root "fake.log") -ScriptBlock {
            param($Temps, $Log)
            for ($i = 0; $i -lt 200; $i++) {
                if (@(Get-Content -LiteralPath $Log -ErrorAction SilentlyContinue | Where-Object { $_ -match '"role":"lead"' }).Count -gt 0) {
                    foreach ($folder in @(Get-ChildItem -LiteralPath $Temps -Directory -Filter "hung-one-worker-*" -ErrorAction SilentlyContinue)) {
                        Set-Content -LiteralPath (Join-Path $folder.FullName "progress.txt") -Value $i -Encoding ASCII -ErrorAction SilentlyContinue
                    }
                }
                Start-Sleep -Seconds 2
            }
        }
        try {
            Use-FakeHooks -Environment $hooks -Body { $script:revivedRun = Invoke-Cycle -Root $root -Scenario "approve" -Duty -RunMinutes 4.5 -ExtraArguments "-CycleMinutes 4" }
        }
        finally { Stop-Job -Job $writer -ErrorAction SilentlyContinue; Remove-Job -Job $writer -Force -ErrorAction SilentlyContinue }
        $run = $script:revivedRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $card = if (Test-Path -LiteralPath $cards) { [System.IO.File]::ReadAllText($cards, [System.Text.Encoding]::UTF8) } else { "" }
        Assert-True -Condition ($card -match '(?m)^- hung-one/worker: \d+ dk iz yok') -Because "it was silent when handed:`n$card`n$($run.Report)"
        $workers = @($run.Calls | Where-Object { $_.role -eq "worker" -and [string]$_.task -eq "hung-one" })
        Assert-Equal -Expected 1 -Actual @($workers).Count -Because "a run that writes again is never restarted:`n$($run.Report)"
        Assert-True -Condition ($run.Report.Contains("hung-one/worker: yeniden canland") -and -not $run.Report.Contains("yeniden başlatıldı")) -Because $run.Report
    }

    Test-Case "duty: a stopped task starts exactly one Proje Yöneticisi run (no Bash, no Edit); its card lists the task and its reason; 'return' sends it back with the prefixed reason" {
        $root = New-Sandbox -Tasks @((New-Stopped -Reason "ayni is iki kez geri verildi"))
        $run = Invoke-DutyCycle -Root $root -Decisions @((New-Decision -Reason "Eksik testi ekle; çağrıyı düzelt"))
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $lead = @($run.Calls | Where-Object { $_.role -eq "lead" })
        Assert-Equal -Expected 1 -Actual @($lead).Count -Because "one duty run: $(Get-Roles -Run $run)"
        Assert-Equal -Expected "lead,worker,inspector" -Actual (Get-Roles -Run $run) -Because "the duty first, then the worker it sent the task back to"
        $tools = @(([string]$lead[0].tools).Split(","))
        Assert-True -Condition ($tools -contains "Read" -and $tools -contains "Write" -and $tools -contains "Grep" -and $tools -contains "Glob") -Because "it reads and writes one file: $($tools -join ',')"
        Assert-True -Condition ($tools -notcontains "Bash" -and $tools -notcontains "Edit" -and $tools -notcontains "Agent") -Because "no shell, no edit, no agents: $($tools -join ',')"
        Assert-Equal -Expected "lead" -Actual ([string]$lead[0].team_seat) -Because "the lead's seat on the board"
        Assert-Equal -Expected ([string]$root).ToLowerInvariant() -Actual ([string]$lead[0].cwd).ToLowerInvariant() -Because "it works in the checkout, where team/plans is"
        foreach ($expected in @("### stuck-one", "ayni is iki kez geri verildi", "- duty_file: team/plans/c1-duty-1.json")) {
            Assert-True -Condition ($run.Cards.Contains($expected)) -Because "the card names '$expected':`n$($run.Cards)"
        }
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $root "team\plans\c1-duty-1.json")) -Because "the run wrote the file the cycle named"
        $worker = @($run.Calls | Where-Object { $_.role -eq "worker" })
        Assert-True -Condition ([bool]$worker[0].came_back) -Because "the worker's card says why the task came back"
        $task = Get-TaskById -Queue $run.Queue -Id "stuck-one"
        Assert-Equal -Expected "merged" -Actual $task.state -Because "returned, worked, inspected: $($run.Report)"
        Assert-True -Condition ([string]$task.reason -ceq "Proje Yöneticisi: Eksik testi ekle; çağrıyı düzelt") -Because "the reason the worker got: '$($task.reason)'"
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $run.Queue).Count -Because "the queue keeps the protocol"
    }

    Test-Case "duty: 'grant_and_return' adds the granted file to the task's area, then sends it back" {
        $root = New-Sandbox -Tasks @((New-Stopped -Reason "alan dışı dosya: docs/extra.md"))
        $run = Invoke-DutyCycle -Root $root -Decisions @((New-Decision -Action "grant_and_return" -Grant @("docs/extra.md") -Reason "docs/extra.md alana eklendi; oradaki düzeltmeyi yap"))
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $task = Get-TaskById -Queue $run.Queue -Id "stuck-one"
        Assert-Equal -Expected "src/area,docs/extra.md" -Actual (@($task.area) -join ",") -Because "the grant is in the area: $($run.Report)"
        Assert-True -Condition ([string]$task.reason -like "Proje Yöneticisi: docs/extra.md alana eklendi*") -Because "'$($task.reason)'"
        Assert-Equal -Expected "lead,worker,inspector" -Actual (Get-Roles -Run $run) -Because "sent back to its worker"
    }

    Test-Case "duty: a lead-protected grant refuses the WHOLE decision file - the sound decision beside it is not applied either, and the report says why" {
        $root = New-Sandbox -Tasks @((New-Stopped), (New-Stopped -Id "stuck-two" -Area @("src/b") -Reason "alan dışı dosya: docs/HANDOFF.md"))
        $run = Invoke-DutyCycle -Root $root -Decisions @((New-Decision), (New-Decision -Task "stuck-two" -Action "grant_and_return" -Grant @("docs/HANDOFF.md")))
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "lead" -Actual (Get-Roles -Run $run) -Because "one duty run, and nothing sent back"
        $one = Get-TaskById -Queue $run.Queue -Id "stuck-one"
        $two = Get-TaskById -Queue $run.Queue -Id "stuck-two"
        Assert-Equal -Expected "stopped|ayni is iki kez geri verildi|2026-10-03T10:00:00Z" -Actual ("{0}|{1}|{2}" -f $one.state, $one.reason, $one.updated_at) -Because "the sound decision was NOT applied"
        Assert-Equal -Expected "stopped|alan dışı dosya: docs/HANDOFF.md|src/b" -Actual ("{0}|{1}|{2}" -f $two.state, $two.reason, (@($two.area) -join ",")) -Because "the protected path was not granted"
        Assert-True -Condition ($run.Report -match "nöbet kararı reddedildi \(duty-1\): .*docs/HANDOFF\.md.*protected") -Because "the reason is in the report: $($run.Report)"
    }

    Test-Case "duty: 'escalate' keeps the task stopped with the Danışman prefix, says it under the risks, and it is not handed again - in this cycle or the next" {
        $root = New-Sandbox -Tasks @((New-Stopped -Reason "entegrasyon dalında çakışma"))
        $run = Invoke-DutyCycle -Root $root -Decisions @((New-Decision -Action "escalate" -Reason "Entegrasyon dalında çakışma: Danışman çözsün"))
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "lead" -Actual (Get-Roles -Run $run) -Because "one duty run, though the task is still stopped (with a new updated_at)"
        $task = Get-TaskById -Queue $run.Queue -Id "stuck-one"
        Assert-Equal -Expected "stopped" -Actual $task.state -Because $run.Report
        Assert-True -Condition ([string]$task.reason -ceq "Danışman'a iletildi: Entegrasyon dalında çakışma: Danışman çözsün") -Because "'$($task.reason)'"
        Assert-True -Condition ($run.Report -match "Danışman'a iletildi: stuck-one") -Because "a line under the risks: $($run.Report)"
        $next = Invoke-DutyCycle -Root $root -Decisions @((New-Decision -Reason "yanlışlıkla geri ver")) -CycleId "c2"
        Assert-Equal -Expected "lead" -Actual (Get-Roles -Run $next) -Because "the next cycle does not hand an escalated task to the duty again (the log is the two cycles')"
        Assert-Equal -Expected "stopped" -Actual (Get-TaskById -Queue $next.Queue -Id "stuck-one").state -Because "it waits for the Danışman"
    }

    Test-Case "duty: a return the protocol refuses (the area is held by a task in work) is not forced: the task stays stopped and the reason says why" {
        $gate = New-Task -Id "idea-gate" -State "awaiting_owner" -Area @("src/other")
        $busy = New-Task -Id "busy-one" -State "returned" -Area @("src/area")
        $busy | Add-Member -NotePropertyName depends_on -NotePropertyValue @("idea-gate")
        $root = New-Sandbox -Tasks @((New-Stopped), $busy, $gate)
        $run = Invoke-DutyCycle -Root $root -Decisions @((New-Decision -Reason "Testi ekle"))
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "lead" -Actual (Get-Roles -Run $run) -Because "no worker beside the task that holds the files"
        $task = Get-TaskById -Queue $run.Queue -Id "stuck-one"
        Assert-Equal -Expected "stopped" -Actual $task.state -Because $run.Report
        Assert-True -Condition ([string]$task.reason -ceq "Proje Yöneticisi: Testi ekle (alan çakışması: busy-one; o iş bitince)") -Because "'$($task.reason)'"
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $run.Queue).Count -Because "the queue was never broken: $((Test-TeamQueue -Queue $run.Queue) -join '; ')"
    }

    # duty-waits-survive-restart (2026-10-06): a held return lived only in the cycle's memory; the
    # cycle restarted at 15:00 and nothing reopened conversation-followups when money-ledger merged.
    # The next cycle reads the hold back from the stopped task's reason in the store.
    $heldReason = "Proje Yöneticisi: Testi ekle (alan çakışması: busy-one; o iş bitince)"

    Test-Case "duty wait from the store: a cycle started with a held return keeps it stopped while its holder is in work - not handed to the Proje Yöneticisi again" {
        $gate = New-Task -Id "idea-gate" -State "awaiting_owner" -Area @("src/other")
        $busy = New-Task -Id "busy-one" -State "returned" -Area @("src/area")
        $busy | Add-Member -NotePropertyName depends_on -NotePropertyValue @("idea-gate")
        $root = New-Sandbox -Tasks @((New-Stopped -Reason $heldReason), $busy, $gate)
        $run = Invoke-DutyCycle -Root $root -Decisions @((New-Decision -Reason "yanlışlıkla ikinci karar"))
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "" -Actual (Get-Roles -Run $run) -Because "the hold was read back: no duty run, no worker beside the holder: $($run.Report)"
        $task = Get-TaskById -Queue $run.Queue -Id "stuck-one"
        Assert-Equal -Expected "stopped|$heldReason|2026-10-03T10:00:00Z" -Actual ("{0}|{1}|{2}" -f $task.state, $task.reason, $task.updated_at) -Because "untouched while held"
    }

    Test-Case "duty wait from the store: a held return read at cycle start is made when its holder leaves the work (merged) in that cycle" {
        $busy = New-Task -Id "busy-one" -State "returned" -Area @("src/area") -Branch "team/c1/worker-busy-one"
        $root = New-Sandbox -Tasks @((New-Stopped -Reason $heldReason), $busy)
        [void](Invoke-SandboxGit -Root $root -Arguments @("branch", "team/c1/worker-busy-one", "main"))
        $run = Invoke-DutyCycle -Root $root
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $order = @($run.Calls | ForEach-Object { "$($_.role):$($_.task)" }) -join ","
        Assert-Equal -Expected "worker:busy-one,inspector:busy-one,worker:stuck-one,inspector:stuck-one" -Actual $order -Because "the holder first, then the held return, and no duty run: $($run.Report)"
        Assert-Equal -Expected "merged,merged" -Actual ("{0},{1}" -f (Get-TaskById -Queue $run.Queue -Id "busy-one").state, (Get-TaskById -Queue $run.Queue -Id "stuck-one").state) -Because $run.Report
    }

    Test-Case "duty wait from the store: a held return whose holders are already merged or gone is made at once, with the Proje Yöneticisi's reason" {
        $reason = "Proje Yöneticisi: Testi ekle (alan çakışması: done-one, gone-one; o iş bitince)"
        $root = New-Sandbox -Tasks @((New-Stopped -Reason $reason), (New-Task -Id "done-one" -State "merged" -Area @("src/area")))
        $run = Invoke-DutyCycle -Root $root
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $order = @($run.Calls | ForEach-Object { "$($_.role):$($_.task)" }) -join ","
        Assert-Equal -Expected "worker:stuck-one,inspector:stuck-one" -Actual $order -Because "returned at once, not handed to the duty: $($run.Report)"
        Assert-True -Condition ([bool]@($run.Calls)[0].came_back) -Because "the worker's card says why it came back"
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "stuck-one").state -Because $run.Report
    }

    Test-Case "duty wait from the store: a stopped task without the held-return suffix (or without the Proje Yöneticisi's prefix) is not read as a hold" {
        $plain = New-Stopped -Reason "Proje Yöneticisi: Testi ekle"
        $other = New-Stopped -Id "stuck-two" -Area @("src/b") -Reason "inceleme durdu (alan çakışması: gone-one; o iş bitince)"
        $root = New-Sandbox -Tasks @($plain, $other)
        $run = Invoke-DutyCycle -Root $root
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "lead" -Actual (Get-Roles -Run $run) -Because "both go to the duty as any stop does (it wrote no decision): $($run.Report)"
        foreach ($expected in @(@("stuck-one", "Proje Yöneticisi: Testi ekle"), @("stuck-two", "inceleme durdu (alan çakışması: gone-one; o iş bitince)"))) {
            $task = Get-TaskById -Queue $run.Queue -Id $expected[0]
            Assert-Equal -Expected "stopped|$($expected[1])" -Actual ("{0}|{1}" -f $task.state, $task.reason) -Because "untouched"
        }
    }

    Test-Case "duty: a task stopped AGAIN after the duty sent it back is handed again; a task handed three times in one cycle is left to the Danışman" {
        $root = New-Sandbox -Tasks @((New-Stopped))
        $run = Invoke-DutyCycle -Root $root -Decisions @((New-Decision -Reason "Yeniden dene")) -Scenario "return"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "lead,worker,inspector,lead,worker,inspector,lead,worker,inspector" -Actual (Get-Roles -Run $run) -Because "each new stop is handed once, three at most: $($run.Report)"
        Assert-Equal -Expected "stopped" -Actual (Get-TaskById -Queue $run.Queue -Id "stuck-one").state -Because "the inspector stopped it the third time"
        Assert-True -Condition ($run.Report -match "stuck-one: bu döngüde 3 kez") -Because "the report says why it is not handed a fourth time: $($run.Report)"
    }

    Test-Case "duty: no stopped task (or only one the Danışman already has) starts no duty run" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"), (New-Stopped -Id "old-stuck" -Area @("src/old") -Reason "Danışman'a iletildi: eski karar"))
        $run = Invoke-DutyCycle -Root $root -Decisions @((New-Decision -Task "old-stuck"))
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "worker,inspector" -Actual (Get-Roles -Run $run) -Because "no lead run"
        Assert-Equal -Expected "stopped" -Actual (Get-TaskById -Queue $run.Queue -Id "old-stuck").state -Because "left as it was"
        $off = New-Sandbox -Tasks @((New-Stopped))
        $plain = Invoke-Cycle -Root $off -Scenario "approve" -NoCaps
        Assert-Equal -Expected 0 -Actual @($plain.Calls).Count -Because "-NoDuty starts no duty run"
    }

    Test-Case "duty: a run that writes no decision file changes nothing, is a line in the report, and is not handed again in the cycle" {
        $root = New-Sandbox -Tasks @((New-Stopped))
        $run = Invoke-DutyCycle -Root $root -Decisions @()
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "lead" -Actual (Get-Roles -Run $run) -Because "one try for this stop"
        $task = Get-TaskById -Queue $run.Queue -Id "stuck-one"
        Assert-Equal -Expected "stopped|2026-10-03T10:00:00Z" -Actual ("{0}|{1}" -f $task.state, $task.updated_at) -Because "nothing changed"
        Assert-True -Condition ($run.Report -match "nöbet kararı reddedildi \(duty-1\): .*no decision file") -Because $run.Report
    }

    Test-Case "duty: the ledger outlives the cycle - the next cycle does not hand the same stop again, and a task handed three times in all is the Danışman's" {
        # Review 2026-10-04: the hand-over record lived in one cycle's memory, so every tick
        # handed the same stop to a paid lead run again, for ever.
        $root = New-Sandbox -Tasks @((New-Stopped))
        $first = Invoke-DutyCycle -Root $root -Decisions @()
        Assert-Equal -Expected 0 -Actual $first.ExitCode -Because ($first.StdOut + $first.StdErr)
        Assert-Equal -Expected "lead" -Actual (Get-Roles -Run $first) -Because "the stop is handed once"
        $ledger = Read-TeamJson -Path (Join-Path $root "team\duty-ledger.json")
        Assert-Equal -Expected 1 -Actual ([int]$ledger."stuck-one".times) -Because "the ledger counts the hand-over"
        Assert-Equal -Expected "2026-10-03T10:00:00Z" -Actual ([string]$ledger."stuck-one".stamp) -Because "and keeps the stop it was handed at"
        Remove-Item -LiteralPath (Join-Path $root "fake.log") -Force -ErrorAction SilentlyContinue
        $second = Invoke-DutyCycle -Root $root -Decisions @() -CycleId "c2"
        Assert-Equal -Expected 0 -Actual $second.ExitCode -Because ($second.StdOut + $second.StdErr)
        Assert-Equal -Expected "" -Actual (Get-Roles -Run $second) -Because "the next cycle does not hand the same stop again: $($second.Report)"

        $worn = New-Sandbox -Tasks @((New-Stopped -Updated "2026-10-04T10:00:00Z"))
        Write-TeamJson -Path (Join-Path $worn "team\duty-ledger.json") -Document ([ordered]@{ "stuck-one" = [ordered]@{ stamp = "2026-10-03T10:00:00Z"; times = 3 } })
        $run = Invoke-DutyCycle -Root $worn -Decisions @((New-Decision -Reason "Yeniden dene"))
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "" -Actual (Get-Roles -Run $run) -Because "a NEW stop, but handed three times in all: the Danışman's"
        Assert-Equal -Expected "stopped" -Actual (Get-TaskById -Queue $run.Queue -Id "stuck-one").state -Because "left as it was"
        Assert-True -Condition ($run.Report -match "stuck-one: toplam 3 kez") -Because "the report says why: $($run.Report)"

        $broken = New-Sandbox -Tasks @((New-Stopped))
        [System.IO.File]::WriteAllText((Join-Path $broken "team\duty-ledger.json"), "{ not json")
        $run = Invoke-DutyCycle -Root $broken -Decisions @()
        Assert-Equal -Expected "lead" -Actual (Get-Roles -Run $run) -Because "a broken ledger costs one hand-over, never the team"
        Assert-True -Condition ($run.Report -match "nöbet defteri okunamadı") -Because "and is said: $($run.Report)"
    }

    Test-Case "duty: a task the owner rejected in the Onay Merkezi is his - never handed, his words kept" {
        $root = New-Sandbox -Tasks @((New-Stopped -Reason "Sahip reddetti: gerek yok"))
        $run = Invoke-DutyCycle -Root $root -Decisions @((New-Decision -Action "grant_and_return" -Reason "Yeniden dene"))
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-True -Condition (-not ((Get-Roles -Run $run) -match "lead")) -Because "no duty run for an owner's rejection: $(Get-Roles -Run $run)"
        $task = Get-TaskById -Queue $run.Queue -Id "stuck-one"
        Assert-Equal -Expected "stopped|Sahip reddetti: gerek yok" -Actual ("{0}|{1}" -f $task.state, $task.reason) -Because "his stop and his words stay"
        $api = (Get-Content -LiteralPath (Join-Path $repoRoot "services\api\app\team\approvals.py") -Raw -Encoding UTF8)
        Assert-True -Condition ($api -match [regex]::Escape('OWNER_REJECTED_PREFIX = "' + $script:TeamOwnerRejected + '"')) -Because "the API writes the prefix this script skips (contract halves)"
    }

    Test-Case "duty: the split and the duty share the one lead seat - never two lead runs at once - and both are done" {
        $root = New-Sandbox -Tasks @((New-Proposal -Id "idea-one"), (New-Stopped -Area @("src/stuck")))
        $snapshots = Join-Path $root "snapshots"
        $hooks = @{ PAGENTOS_FAKE_CLAUDE_SNAPSHOT = $snapshots; PAGENTOS_FAKE_CLAUDE_STATUS = (Join-Path $root "team\status.json"); PAGENTOS_FAKE_CLAUDE_SNAPSHOT_SECONDS = 1 }
        $run = Invoke-DutyCycle -Root $root -Decisions @((New-Decision -Action "escalate" -Reason "Danışman baksın")) -Scenario "split" -Hooks $hooks
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected 2 -Actual @($run.Calls | Where-Object { $_.role -eq "lead" }).Count -Because "the duty and the split: $(Get-Roles -Run $run)"
        Assert-Equal -Expected "lead" -Actual $run.Calls[0].role -Because "a lead run first"
        foreach ($name in @("lead-.json", "lead-idea-one.json")) {
            $seen = Read-TeamJson -Path (Join-Path $snapshots $name)
            Assert-Equal -Expected 1 -Actual @(@($seen.runs) | Where-Object { $_.role -eq "lead" }).Count -Because "${name}: one lead run in flight"
        }
        Assert-Equal -Expected "done" -Actual (Get-TaskById -Queue $run.Queue -Id "idea-one").state -Because "the split was made: $($run.Report)"
        Assert-True -Condition ([string](Get-TaskById -Queue $run.Queue -Id "stuck-one").reason -like "Danışman'a iletildi: *") -Because "the duty decided"
    }

    Test-Case "a queue that breaks the protocol runs nothing: an id used twice stops everything" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"), (New-Task -Id "task-one" -Area @("src/b")))
        $run = Invoke-Cycle -Root $root -Scenario "approve"
        Assert-Equal -Expected 2 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "nothing ran"
        Assert-True -Condition ($run.StdOut -match "used twice") -Because $run.StdOut
    }

    Test-Case "one task that breaks the protocol is set aside with its reason, once; the rest of the queue runs" {
        # 2026-10-06 21:02-21:16: seven test-team cards in 'assigned' without an area; the cycle
        # said "the queue breaks the protocol; nothing was run" for over an hour.
        $root = New-Sandbox -Tasks @(
            (New-Task -Id "test-fail-nobet-1" -State "assigned" -Area @()),
            (New-Task -Id "bad-branch" -State "approved" -Area @("src/c") -Branch "feat/hand-gestures-stage1"),
            (New-Task -Id "task-two"))
        $run = Invoke-Cycle -Root $root -Scenario "approve"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "task-two").state -Because "the rest ran: $($run.Report)"
        Assert-Equal -Expected 0 -Actual @($run.Calls | Where-Object { [string]$_.task -match "test-fail-nobet-1|bad-branch" }).Count -Because "nobody was started for a task set aside"
        $aside = Get-TaskById -Queue $run.Queue -Id "test-fail-nobet-1"
        Assert-Equal -Expected "stopped" -Actual $aside.state -Because "set aside"
        Assert-True -Condition ([string]$aside.reason -like "alan yok: önce dosya alanı*") -Because "with its reason: $($aside.reason)"
        Assert-Equal -Expected "stopped" -Actual (Get-TaskById -Queue $run.Queue -Id "bad-branch").state -Because "every task with a problem of its own"
        Assert-True -Condition ($run.Report -match "test-fail-nobet-1 .*alan yok") -Because "the report's stopped list names it: $($run.Report)"
        Assert-True -Condition ($run.Report -match "Danışman'a iletildi: kenara alındı: test-fail-nobet-1") -Because "the Danışman's line: $($run.Report)"
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $run.Queue).Count -Because "the queue it left keeps the protocol"
        $written = [string]$aside.updated_at
        $second = Invoke-Cycle -Root $root -Scenario "approve" -CycleId "c2"
        Assert-Equal -Expected 0 -Actual $second.ExitCode -Because ($second.StdOut + $second.StdErr)
        Assert-Equal -Expected $written -Actual ([string](Get-TaskById -Queue $second.Queue -Id "test-fail-nobet-1").updated_at) -Because "the reason is written once"
        Assert-True -Condition ($second.Report -notmatch "kenara alındı: test-fail-nobet-1") -Because "and not said again"
    }

    Test-Case "an approved task without an area is not moved into work: it stops with 'alan yok: önce dosya alanı'" {
        $root = New-Sandbox -Tasks @((New-Task -Id "test-fail-alarm-1" -State "approved" -Area @()), (New-Task -Id "task-two"))
        $run = Invoke-Cycle -Root $root -Scenario "approve"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $bare = Get-TaskById -Queue $run.Queue -Id "test-fail-alarm-1"
        Assert-Equal -Expected "stopped" -Actual $bare.state -Because "never 'assigned' without an area"
        Assert-True -Condition ([string]$bare.reason -like "alan yok: önce dosya alanı*") -Because $bare.reason
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "task-two").state -Because "the rest ran"
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $run.Queue).Count -Because "the queue it left keeps the protocol"
    }

    Test-Case "a task back from its integrator with a plan but no area is not moved into work: it stops with 'alan yok: önce dosya alanı'" {
        # The inspector of 11365bac: the integrator's completion path set 'assigned' without asking,
        # and the area-less card went to a worker.
        $study = New-Task -Id "test-fail-nobet-2" -Area @()
        $study | Add-Member -NotePropertyName "needs_integration" -NotePropertyValue $true
        $root = New-Sandbox -Tasks @($study, (New-Task -Id "task-two"))
        $run = Invoke-Cycle -Root $root -Scenario "approve"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "integrator" -Actual (@($run.Calls | Where-Object { [string]$_.task -eq "test-fail-nobet-2" } | ForEach-Object { [string]$_.role }) -join ",") -Because "its integrator ran, nobody else for it"
        $back = Get-TaskById -Queue $run.Queue -Id "test-fail-nobet-2"
        Assert-Equal -Expected "stopped" -Actual $back.state -Because "never 'assigned' without an area"
        Assert-True -Condition ([string]$back.reason -like "alan yok: önce dosya alanı*") -Because $back.reason
        Assert-Equal -Expected "team/plans/test-fail-nobet-2-integration.md" -Actual ([string]$back.plan) -Because "the plan is kept for when the area comes"
        Assert-True -Condition ($run.Report -match "Danışman'a iletildi: kenara alındı: test-fail-nobet-2: alan yok") -Because "the Danışman's line: $($run.Report)"
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "task-two").state -Because "the rest ran"
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $run.Queue).Count -Because "the queue it left keeps the protocol"
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

    Test-Case "the continuous cycle: the researcher is throttled by its last run, and a day's cycles share one id" {
        # Owner, 2026-10-01: the cycle runs all day ("sürekli, kontrollü"), not once a night. A
        # web scan in every half-hourly cycle is not control: with -ResearchEveryHours the
        # researcher runs only when its last finished run is older than that.
        $root = New-Sandbox -Tasks @()
        $first = Invoke-Cycle -Root $root -Scenario "approve" -Research -ExtraArguments "-ResearchEveryHours 6"
        Assert-Equal -Expected "researcher" -Actual (@($first.Calls | ForEach-Object { $_.role }) -join ",") -Because "no marker yet: it runs"
        $marker = Join-Path $root "team\research-last.txt"
        Assert-True -Condition (Test-Path -LiteralPath $marker) -Because "its finished run is recorded"
        $second = Invoke-Cycle -Root $root -Scenario "approve" -CycleId "c2" -Research -ExtraArguments "-ResearchEveryHours 6"
        Assert-Equal -Expected 1 -Actual @($second.Calls).Count -Because "a run minutes ago: the researcher is not started again"
        [System.IO.File]::WriteAllText($marker, (Get-TeamTimestamp -Now ([datetime]::UtcNow.AddHours(-7))))
        $third = Invoke-Cycle -Root $root -Scenario "approve" -CycleId "c3" -Research -ExtraArguments "-ResearchEveryHours 6"
        Assert-Equal -Expected 2 -Actual @($third.Calls).Count -Because "seven hours later it runs again"
        $always = Invoke-Cycle -Root $root -Scenario "approve" -CycleId "c4" -Research
        Assert-Equal -Expected 3 -Actual @($always.Calls).Count -Because "without the throttle it runs in every cycle"

        $daily = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $daily -Scenario "approve" -CycleId "" -ExtraArguments "-DailyId"
        $expected = "d" + (Get-Date).ToString("yyyyMMdd")
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $daily "team\reports\$expected.md")) -Because "the day's id names the report: $($run.StdOut + $run.StdErr)"
        Assert-Equal -Expected "integrate/$expected" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").integration_branch -Because "and one integration branch for the day"
    }

    Test-Case "each role runs on the model the team's setting names for it (owner, 2026-10-01)" {
        # Interim form of the model policy (ADR-0214 addendum 7): team/models.json maps a role to a
        # model; a role it does not name runs on -Model, and with neither the tool's own default.
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        [System.IO.File]::WriteAllText((Join-Path $root "team\models.json"), '{"roles":{"worker":"claude-opus-5-5","inspector":"claude-fable-5-1"}}')
        $run = Invoke-Cycle -Root $root -Scenario "approve"
        $byRole = @{}; foreach ($call in $run.Calls) { $byRole[[string]$call.role] = [string]$call.model }
        Assert-Equal -Expected "claude-opus-5-5" -Actual $byRole["worker"] -Because "the worker's model: $($run.StdOut + $run.StdErr)"
        Assert-Equal -Expected "claude-fable-5-1" -Actual $byRole["inspector"] -Because "the inspector's model"
        Assert-True -Condition ($run.Report -match "task-one / worker: .*claude-opus-5-5") -Because "the report names the model of each run: $($run.Report)"
        # model-policy-cycle: nothing stored is the contract's defaults, not the tool's own default.
        $plain = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $none = Invoke-Cycle -Root $plain -Scenario "approve"
        Assert-Equal -Expected "worker=$opus,inspector=$fable" -Actual (@($none.Calls | ForEach-Object { "$($_.role)=$($_.model)" }) -join ",") -Because "no setting: worker Opus 5.5, inspector Fable"
        foreach ($name in @("--dangerously-skip-permissions", "claude-haiku-4-5-20251001", "claude-opus-9")) {
            $bad = New-Sandbox -Tasks @((New-Task -Id "task-one"))
            [System.IO.File]::WriteAllText((Join-Path $bad "team\models.json"), ('{"roles":{"worker":"' + $name + '"}}'))
            $refused = Invoke-Cycle -Root $bad -Scenario "approve"
            Assert-Equal -Expected 0 -Actual @($refused.Calls).Count -Because "'$name' is not one of the three ids and starts nothing: $($refused.StdOut + $refused.StdErr)"
            Assert-Equal -Expected 2 -Actual $refused.ExitCode -Because "it stops before the lock, as a queue that breaks the protocol does"
            Assert-True -Condition ($refused.StdOut -match [regex]::Escape($name)) -Because "and names the value: $($refused.StdOut)"
        }
        foreach ($call in $none.Calls) {
            Assert-Equal -Expected "stream-json" -Actual ([string]$call.output) -Because "the limit events are only in the stream"
            Assert-Equal -Expected "1" -Actual ([string]$call.no_fallback_env) -Because "the tool is told not to substitute the model itself"
            # 2026-10-03: five runs of one night ended with "the suite is running in the background, I
            # will report when it finishes" - a run of the cycle is never woken again, so the work was
            # judged empty and tasks were returned and stopped for nothing. The tool's own switch
            # removes the background parameter from the run's Bash tool (the lead proved it: the call
            # is refused with "An unexpected parameter `run_in_background` was provided").
            Assert-Equal -Expected "1" -Actual ([string]$call.no_background_env) -Because "a run of the cycle cannot start a background command it will never be told about"
            # Without the background, a long suite must fit one foreground call: the tool cuts a call at
            # ten minutes unless told otherwise (the lead measured both: 'Command timed out after 10m 0s'
            # by default, an 11-minute command finished with the limit raised).
            Assert-Equal -Expected "3600000" -Actual ([string]$call.bash_max_timeout_env) -Because "one foreground command may run for an hour"
            Assert-Equal -Expected $false -Actual ([bool]$call.fallback_flag) -Because "--fallback-model is never passed"
        }
    }

    Test-Case "each run gets its own temp folder under run_temp_root, removed when the run ends (owner, 2026-10-03)" {
        # C: filled to zero at 12:00 on 2026-10-03 and the day before %TEMP% held 2.67 million leaked
        # folders: the tests a run starts write their temp folders into the run's own folder on the
        # data drive, and the cycle empties it when the run is over. The folder itself is kept: Git
        # Bash may hold it as the machine's /tmp (2026-10-06 01:50, card run-temp-keeps-git-bash-tmp).
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $tempRoot = Join-Path $root "run-temp"
        $json = '{"max_parallel": 3, "run_temp_root": ' + (ConvertTo-Json -InputObject $tempRoot) + '}'
        [System.IO.File]::WriteAllText((Join-Path $root "team\cycle-settings.json"), $json)
        $run = Invoke-Cycle -Root $root -Scenario "approve"
        Assert-Equal -Expected 2 -Actual @($run.Calls).Count -Because "a worker and an inspector ran: $($run.StdOut + $run.StdErr)"
        $folders = @()
        foreach ($call in $run.Calls) {
            $temp = [string]$call.temp_env
            Assert-True -Condition ($temp.StartsWith($tempRoot + "\", [System.StringComparison]::OrdinalIgnoreCase)) -Because "the $($call.role)'s TEMP is under run_temp_root: '$temp'"
            Assert-Equal -Expected $temp -Actual ([string]$call.tmp_env) -Because "TMP is the same folder"
            Assert-True -Condition ($temp -match "task-one-$([string]$call.role)-[0-9a-f]{8}$") -Because "named by task and role: '$temp'"
            Assert-True -Condition ((Test-Path -LiteralPath $temp -PathType Container) -and (@(Get-ChildItem -LiteralPath $temp -Force).Count -eq 0)) -Because "the run's folder is kept (Git Bash may hold it as /tmp) and emptied when the run ends: '$temp'"
            $folders += $temp
        }
        Assert-Equal -Expected 2 -Actual @($folders | Sort-Object -Unique).Count -Because "each run has its own folder"
        # No setting: the machine's TEMP, as before.
        $plain = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $none = Invoke-Cycle -Root $plain -Scenario "approve"
        foreach ($call in $none.Calls) {
            Assert-True -Condition (-not ([string]$call.temp_env).StartsWith($plain, [System.StringComparison]::OrdinalIgnoreCase)) -Because "without run_temp_root the run keeps the machine's TEMP: '$($call.temp_env)'"
        }
    }

    # ------------------------------------------------------------------ the model policy, in the cycle
    Write-Host ""
    Write-Host "the model policy in the cycle: the chain, the remembered limit, the inspector's rule"

    function Set-SandboxFile {
        param([string]$Root, [string]$Name, [string]$Json)
        [System.IO.File]::WriteAllText((Join-Path $Root "team\$Name"), $Json, (New-Object System.Text.UTF8Encoding($false)))
    }
    function Get-CallModels {
        param([object[]]$Calls)
        return (@($Calls | ForEach-Object { "$($_.role)=$($_.model)" }) -join ",")
    }
    $noWait = '-WaitForUsageLimit:$false'
    $allRoles = '"lead":"claude-fable-5-1","researcher":"claude-opus-5-5","integrator":"claude-opus-5-5"'

    Test-Case "the fallback chain: a worker limited on Opus is started again at once on Sonnet - merged, nothing counted against it, 'model düşürüldü' in the report and in the status; no later run starts on the limited model, in this cycle or the next" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/area")), (New-Task -Id "task-two" -Area @("src/area2")))
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_LIMITED_MODELS = $opus } -Body { $script:lowRun = Invoke-Cycle -Root $root -Scenario "approve" -NoCaps -MaxParallel 1 -ExtraArguments $noWait }
        $run = $script:lowRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $one = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "merged,merged" -Actual (@(Get-TeamTasks -Queue $run.Queue | ForEach-Object { $_.state }) -join ",") -Because ($run.StdOut + $run.StdErr + $run.Report)
        Assert-Equal -Expected "worker=$opus,worker=$sonnet,inspector=$fable,worker=$sonnet,inspector=$fable" -Actual (Get-CallModels -Calls $run.Calls) `
            -Because "the limited run, the SAME task at once one model down, its inspection; the second task starts on Sonnet without trying Opus again"
        Assert-Equal -Expected "task-one,task-one" -Actual (@($run.Calls[0..1] | ForEach-Object { $_.task }) -join ",") -Because "the same task, straight away"
        Assert-Equal -Expected 0 -Actual ([int](Get-TeamProperty -InputObject $one -Name "failed_runs" -Default 0)) -Because "the limit is not the task's failure"
        Assert-True -Condition ($run.Report -match "task-one / worker: [^\r\n]*model $sonnet, model düşürüldü: $opus -> $sonnet") -Because "the lowered run's line says so: $($run.Report)"
        Assert-True -Condition ($run.Report -match "task-one / worker: [^\r\n]*Max kullanım limiti, model $opus") -Because "the limited run is in the list with its model"
        Assert-True -Condition ($run.Report -notmatch "beklendi" -and $run.Report -notmatch "döngü: Max kullanım limiti") -Because "the cycle neither waited nor stopped: $($run.Report)"
        Assert-Equal -Expected "tamam (model $sonnet)" -Actual ([string]@($one.reports | Where-Object { $_.role -eq "worker" })[-1].outcome) -Because "the worker's report entry records the model it really ran on"
        $status = Read-TeamJson -Path (Join-Path $root "team\status.json")
        $lowered = @($status.limits.lowered)
        Assert-Equal -Expected "task-one/worker/$opus/$sonnet,task-two/worker/$opus/$sonnet" -Actual (@($lowered | ForEach-Object { "$($_.task)/$($_.role)/$($_.from)/$($_.to)" }) -join ",") -Because "this cycle's downgrades, newest last: $($status | ConvertTo-Json -Depth 6 -Compress)"
        Assert-True -Condition ([string]$lowered[0].at -match '^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$') -Because "each says when"
        Assert-Equal -Expected $true -Actual $status.limits.fallback -Because "the chain is on"
        Assert-Equal -Expected "ok" -Actual $status.limits.fable.state -Because "Fable was not limited"
        Assert-Equal -Expected "ok" -Actual $status.limits.all.state -Because "one model's limit is not the limit of all"
        $kept = Read-TeamJson -Path (Join-Path $root "team\limits.json")
        Assert-True -Condition ([string]$kept.models.$opus.until -match '^\d{4}-') -Because "the limited model is remembered with its reset: $($kept | ConvertTo-Json -Depth 6 -Compress)"
        Assert-Equal -Expected "seven_day_opus" -Actual $kept.models.$opus.type -Because "and the limit's type"

        # The next cycle: the fake limits nothing now, so only the remembered limit keeps Opus out.
        Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document (New-Queue -Tasks @((New-Task -Id "task-three" -Area @("src/area3"))))
        $second = Invoke-Cycle -Root $root -Scenario "approve" -CycleId "c2" -NoCaps
        Assert-Equal -Expected "worker=$sonnet,inspector=$fable" -Actual (Get-CallModels -Calls @($second.Calls | Select-Object -Skip 5)) -Because "across cycles: no run on the limited model until its reset"
        Assert-True -Condition ($second.Report -match "task-three / worker: [^\r\n]*model düşürüldü: $opus -> $sonnet") -Because $second.Report
        # Its reset has passed: the entry is ignored and the model is used again.
        Set-SandboxFile -Root $root -Name "limits.json" -Json ('{"models":{"' + $opus + '":{"until":"2026-09-30T00:00:00Z","type":"seven_day_opus","seen_at":"2026-09-29T00:00:00Z"}},"windows":{}}')
        Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document (New-Queue -Tasks @((New-Task -Id "task-four" -Area @("src/area4"))))
        $third = Invoke-Cycle -Root $root -Scenario "approve" -CycleId "c3" -NoCaps
        Assert-Equal -Expected "worker=$opus,inspector=$fable" -Actual (Get-CallModels -Calls @($third.Calls | Select-Object -Skip 7)) -Because "after the reset the configured model is back"
    }

    Test-Case "the inspector's rule: Fable limited and the worker ran on Opus - the inspection runs on Opus, and the report says the model was lowered" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_LIMITED_MODELS = $fable } -Body { $script:inspRun = Invoke-Cycle -Root $root -Scenario "approve" -NoCaps -ExtraArguments $noWait }
        $run = $script:inspRun
        Assert-Equal -Expected "worker=$opus,inspector=$fable,inspector=$opus" -Actual (Get-CallModels -Calls $run.Calls) -Because "Opus is not weaker than the worker's Opus: $($run.StdOut + $run.StdErr)"
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because $run.Report
        Assert-True -Condition ($run.Report -match "task-one / inspector: [^\r\n]*model düşürüldü: $fable -> $opus") -Because $run.Report
        $status = Read-TeamJson -Path (Join-Path $root "team\status.json")
        Assert-Equal -Expected "limited" -Actual $status.limits.fable.state -Because "Fable came back limited"
        Assert-True -Condition ([string]$status.limits.fable.resets_at -match '^\d{4}-') -Because "with its reset: $($status | ConvertTo-Json -Depth 6 -Compress)"
        Assert-Equal -Expected "inspector" -Actual @($status.limits.lowered)[0].role -Because "the downgrade is recorded"
    }

    Test-Case "the inspector's rule: the worker ran on Fable and only weaker models are open - the inspection WAITS: no inspector run is started, it is not lowered, and the report says why" {
        # The worker's run is on record (an earlier cycle) and Fable is remembered as limited.
        $task = New-Task -Id "task-one" -State "inspecting"
        $task.reports = @([pscustomobject]@{ cycle = "c0"; role = "worker"; at = "2026-10-01T09:00:00Z"; file = "team/reports/c0/task-one-worker-1.md"; cost_usd = 0.25; outcome = "tamam (model $fable)"; summary = @("sha: see the branch") })
        $root = New-Sandbox -Tasks @($task)
        $until = Get-TeamTimestamp -Now ([datetime]::UtcNow.AddHours(1))
        Set-SandboxFile -Root $root -Name "limits.json" -Json ('{"models":{"' + $fable + '":{"until":"' + $until + '","type":"seven_day_overage_included","seen_at":"2026-10-01T09:30:00Z"}},"windows":{}}')
        $run = Invoke-Cycle -Root $root -Scenario "approve" -NoCaps -ExtraArguments $noWait
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "no inspector run was started - not on Fable, and not on a weaker model: $(Get-CallModels -Calls $run.Calls)"
        Assert-Equal -Expected "inspecting" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "the inspection waits; it is not skipped"
        Assert-True -Condition ($run.Report -match "denetim bekliyor: task-one[^\r\n]*işçi $fable[^\r\n]*daha zayıf modelde denetlenmez") -Because "the report says why: $($run.Report)"
        Assert-True -Condition ($run.Report -match "döngü: Max kullanım limiti; sıfırlanma $until") -Because "and the existing stop line names Fable's reset: $($run.Report)"
        $status = Read-TeamJson -Path (Join-Path $root "team\status.json")
        Assert-Equal -Expected "limited" -Actual $status.limits.fable.state -Because "the remembered limit is in the status"
        Assert-Equal -Expected $until -Actual $status.limits.fable.resets_at -Because "with its reset"
        Assert-Equal -Expected 0 -Actual @($status.limits.lowered).Count -Because "nothing was lowered"

        # The same rule when the limit is found by the inspector's own run, in one cycle.
        $live = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        Set-SandboxFile -Root $live -Name "models.json" -Json ('{"roles":{' + $allRoles + ',"worker":"' + $fable + '","inspector":"' + $fable + '"},"fallback":true}')
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_LIMITED_MODELS = $fable; PAGENTOS_FAKE_CLAUDE_LIMITED_ROLES = "inspector" } -Body { $script:holdRun = Invoke-Cycle -Root $live -Scenario "approve" -NoCaps -ExtraArguments $noWait }
        $held = $script:holdRun
        Assert-Equal -Expected "worker=$fable,inspector=$fable" -Actual (Get-CallModels -Calls $held.Calls) -Because "after the limited inspection no inspector run is started on Opus or Sonnet"
        $one = Get-TaskById -Queue $held.Queue -Id "task-one"
        Assert-Equal -Expected "inspecting" -Actual $one.state -Because $held.Report
        Assert-Equal -Expected 0 -Actual ([int](Get-TeamProperty -InputObject $one -Name "failed_runs" -Default 0)) -Because "the limit is not counted against the task"
        Assert-True -Condition ($held.Report -match "denetim bekliyor: task-one") -Because $held.Report
    }

    Test-Case "the inspector's rule holds against the tool too: an inspection the tool itself ran on a weaker model than the worker's gives no verdict" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_RAN_MODEL = "inspector=$sonnet" } -Body { $script:swapRun = Invoke-Cycle -Root $root -Scenario "approve" -NoCaps -ExtraArguments $noWait }
        $run = $script:swapRun
        $one = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "inspecting" -Actual $one.state -Because "an APPROVE from Sonnet over an Opus worker is not an approval: $($run.Report)"
        Assert-True -Condition ($run.Report -match "task-one / inspector: [^\r\n]*model düşürüldü \(araç\): $fable -> $sonnet") -Because "the run list names the substitution: $($run.Report)"
        Assert-True -Condition ($run.Report -match "hüküm alınmadı") -Because "and why no verdict was taken"
        $threw = $false
        try { [void](Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "--verify", "--quiet", "refs/heads/integrate/c1")) } catch { $threw = $true }
        Assert-True -Condition $threw -Because "nothing was merged"
    }

    Test-Case "the lead's split run and the researcher follow the setting and the chain like any role" {
        $root = New-Sandbox -Tasks @((New-Proposal -Id "idea-one"))
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_LIMITED_MODELS = $fable; PAGENTOS_FAKE_CLAUDE_LIMITED_ROLES = "lead" } -Body { $script:leadRun = Invoke-Cycle -Root $root -Scenario "split" -NoCaps -ExtraArguments $noWait }
        $run = $script:leadRun
        Assert-Equal -Expected "lead=$fable,lead=$opus" -Actual (Get-CallModels -Calls @($run.Calls | Select-Object -First 2)) -Because "the split, limited on Fable, is run again at once on Opus: $($run.StdOut + $run.StdErr)"
        Assert-True -Condition (@($run.Calls[0..1] | Where-Object { ([string]$_.tools) -match "Bash|Edit" }).Count -eq 0) -Because "the second lead run has the split run's tools, not the role's"
        Assert-Equal -Expected "merged,merged" -Actual (@("idea-one-a", "idea-one-b" | ForEach-Object { (Get-TaskById -Queue $run.Queue -Id $_).state }) -join ",") -Because "the split was taken and its tasks ran: $($run.Report)"
        Assert-True -Condition ($run.Report -match "idea-one / lead: [^\r\n]*model düşürüldü: $fable -> $opus") -Because $run.Report
        Assert-Equal -Expected 0 -Actual @($run.Calls | Select-Object -Skip 1 | Where-Object { $_.model -eq $fable }).Count -Because "Fable is remembered as limited: the inspectors of this cycle are not started on it either"
        $research = New-Sandbox -Tasks @()
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_LIMITED_MODELS = $opus } -Body { $script:researchRun = Invoke-Cycle -Root $research -Scenario "approve" -NoCaps -Research -ExtraArguments $noWait }
        Assert-Equal -Expected "researcher=$opus,researcher=$sonnet" -Actual (Get-CallModels -Calls $script:researchRun.Calls) -Because "the researcher too"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $research "team\research-last.txt")) -Because "and its finished run is recorded"
        Assert-True -Condition ($script:researchRun.Report -match "cycle / researcher: [^\r\n]*model düşürüldü: $opus -> $sonnet") -Because $script:researchRun.Report
    }

    Test-Case "fallback off: a limited run is not lowered - the cycle waits or stops as it always did" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        Set-SandboxFile -Root $root -Name "models.json" -Json ('{"roles":{' + $allRoles + ',"worker":"' + $opus + '","inspector":"' + $fable + '"},"fallback":false}')
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_LIMITED_MODELS = $opus } -Body { $script:offRun = Invoke-Cycle -Root $root -Scenario "approve" -NoCaps -ExtraArguments $noWait }
        $run = $script:offRun
        Assert-Equal -Expected "worker=$opus" -Actual (Get-CallModels -Calls $run.Calls) -Because "no run on Sonnet"
        Assert-Equal -Expected "assigned" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "the task is where it was"
        Assert-True -Condition ($run.Report -match "döngü: Max kullanım limiti; sıfırlanma \d{4}-[^;]*; limit açılınca aynı -CycleId ile yeniden başlat") -Because "today's stop line, now with the reset the event gave: $($run.Report)"
        Assert-True -Condition ($run.Report -notmatch "model düşürüldü") -Because "nothing was lowered"
        $status = Read-TeamJson -Path (Join-Path $root "team\status.json")
        Assert-Equal -Expected $false -Actual $status.limits.fallback -Because "the status says the chain is off"
        Assert-Equal -Expected "stopped" -Actual $status.usage_limit.state -Because "the existing field is kept"
        # And the wait: the old document, reset already past - waited out, run again on the SAME model.
        $wait = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        Set-SandboxFile -Root $wait -Name "models.json" -Json ('{"roles":{},"fallback":false}')
        $waited = Invoke-Cycle -Root $wait -Scenario "limited" -NoCaps
        Assert-Equal -Expected "worker=$opus,worker=$opus,inspector=$fable" -Actual (Get-CallModels -Calls $waited.Calls) -Because "the run after the wait is on the same model"
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $waited.Queue -Id "task-one").state -Because $waited.Report
        Assert-True -Condition ($waited.Report -match "sıfırlanmasına kadar beklendi") -Because $waited.Report
    }

    Test-Case "every model of the chain limited: after the last one the existing stop line, and the task is where it was" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_LIMITED_MODELS = "$fable,$opus,$sonnet" } -Body { $script:allRun = Invoke-Cycle -Root $root -Scenario "approve" -NoCaps -ExtraArguments $noWait }
        $run = $script:allRun
        Assert-Equal -Expected "worker=$opus,worker=$sonnet" -Actual (Get-CallModels -Calls $run.Calls) -Because "down the chain once, and no further: $($run.StdOut + $run.StdErr)"
        $one = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "assigned" -Actual $one.state -Because $run.Report
        Assert-Equal -Expected 0 -Actual ([int](Get-TeamProperty -InputObject $one -Name "failed_runs" -Default 0)) -Because "neither limited run is the task's failure"
        Assert-True -Condition ($run.Report -match "döngü: Max kullanım limiti; sıfırlanma \d{4}-") -Because $run.Report
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "a stopped cycle releases the lock"
    }

    Test-Case "a limit whose reset is already past is believed once: the second time the same model refuses the same task it is closed for the cycle - lowered with fallback on, the stop line with it off - and the cycle never spins" {
        # The inspector's probe (2026-10-01): "Opus limited, reset ten minutes ago" gave 123 worker
        # runs in a minute - the model is not marked (its reset has passed), the wait is 0 s and the
        # try is handed back. -CycleMinutes is the hang guard only; what is asserted is the run list.
        $stale = @{ PAGENTOS_FAKE_CLAUDE_LIMITED_MODELS = $opus; PAGENTOS_FAKE_CLAUDE_LIMIT_RESET_SECONDS = "-600" }
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        Use-FakeHooks -Environment $stale -Body { $script:staleRun = Invoke-Cycle -Root $root -Scenario "approve" -NoCaps -ExtraArguments "-CycleMinutes 2" }
        $run = $script:staleRun
        Assert-Equal -Expected "worker=$opus,worker=$opus,worker=$sonnet,inspector=$fable" -Actual (Get-CallModels -Calls $run.Calls) `
            -Because "one retry after the reset it named, then Opus is closed and the task goes one model down: $(@($run.Calls).Count) runs"
        $one = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "merged" -Actual $one.state -Because $run.Report
        Assert-Equal -Expected 0 -Actual ([int](Get-TeamProperty -InputObject $one -Name "failed_runs" -Default 0)) -Because "the limit is not the task's failure"
        Assert-True -Condition ($run.Report -match "task-one / worker: [^\r\n]*model düşürüldü: $opus -> $sonnet") -Because $run.Report
        Assert-True -Condition ($run.Report -match "$opus[^\r\n]*sıfırlanma saati geçmişte[^\r\n]*bu döngüde kapalı sayıldı") -Because "the report says why Opus was closed: $($run.Report)"
        Assert-True -Condition ($run.Report -notmatch "süre tavanı") -Because "the hang guard did not end it: $($run.Report)"
        $kept = Read-TeamJson -Path (Join-Path $root "team\limits.json")
        Assert-True -Condition ($null -eq $kept.models.PSObject.Properties[$opus]) -Because "a limit nobody dated believably is not carried to the next cycle: $($kept | ConvertTo-Json -Depth 6 -Compress)"

        $off = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        Set-SandboxFile -Root $off -Name "models.json" -Json ('{"roles":{},"fallback":false}')
        Use-FakeHooks -Environment $stale -Body { $script:staleOff = Invoke-Cycle -Root $off -Scenario "approve" -NoCaps -ExtraArguments "-CycleMinutes 2" }
        $run = $script:staleOff
        Assert-Equal -Expected "worker=$opus,worker=$opus" -Actual (Get-CallModels -Calls $run.Calls) -Because "fallback off: the retry, and then nothing: $(@($run.Calls).Count) runs"
        Assert-Equal -Expected "assigned" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because $run.Report
        Assert-True -Condition ($run.Report -match "döngü: Max kullanım limiti; ne zaman açılacağı söylenmedi") -Because "the stop line of a limit nobody dated: $($run.Report)"
        Assert-True -Condition ($run.Report -notmatch "süre tavanı") -Because $run.Report

        $all = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $every = @{ PAGENTOS_FAKE_CLAUDE_LIMITED_MODELS = "$fable,$opus,$sonnet"; PAGENTOS_FAKE_CLAUDE_LIMIT_RESET_SECONDS = "-600" }
        Use-FakeHooks -Environment $every -Body { $script:staleAll = Invoke-Cycle -Root $all -Scenario "approve" -NoCaps -ExtraArguments "-CycleMinutes 2" }
        $run = $script:staleAll
        Assert-Equal -Expected "worker=$opus,worker=$opus,worker=$sonnet,worker=$sonnet" -Actual (Get-CallModels -Calls $run.Calls) -Because "two tries a model, down the chain, and no further: $(@($run.Calls).Count) runs"
        Assert-True -Condition ($run.Report -match "döngü: Max kullanım limiti; ne zaman açılacağı söylenmedi") -Because $run.Report
        Assert-True -Condition ($run.Report -notmatch "süre tavanı") -Because $run.Report
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "a stopped cycle releases the lock"

        # The researcher's path (Invoke-RoleRun) closes the model the same way.
        $research = New-Sandbox -Tasks @()
        Use-FakeHooks -Environment $stale -Body { $script:staleResearch = Invoke-Cycle -Root $research -Scenario "approve" -NoCaps -Research -ExtraArguments "-CycleMinutes 2" }
        Assert-Equal -Expected "researcher=$opus,researcher=$opus,researcher=$sonnet" -Actual (Get-CallModels -Calls $script:staleResearch.Calls) -Because "the same bound for a run the cycle waits for by itself"
    }

    Test-Case "the inspector's rule for a worker entry that names no model (a run from before the policy): the floor is the configured worker model - no inspection on Sonnet, and no verdict from a tool that ran it there" {
        # The inspector's probe (2026-10-01): plain 'tamam', Fable and Opus limited - the
        # inspection ran on Sonnet and the task was merged.
        $entry = [pscustomobject]@{ cycle = "c0"; role = "worker"; at = "2026-10-01T09:00:00Z"; file = "team/reports/c0/task-one-worker-1.md"; cost_usd = 0.25; outcome = "tamam"; summary = @("sha: see the branch") }
        $task = New-Task -Id "task-one" -State "inspecting"
        $task.reports = @($entry)
        $root = New-Sandbox -Tasks @($task)
        $until = Get-TeamTimestamp -Now ([datetime]::UtcNow.AddHours(1))
        $closed = '{"until":"' + $until + '","type":"seven_day","seen_at":"2026-10-01T09:30:00Z"}'
        Set-SandboxFile -Root $root -Name "limits.json" -Json ('{"models":{"' + $fable + '":' + $closed + ',"' + $opus + '":' + $closed + '},"windows":{}}')
        $run = Invoke-Cycle -Root $root -Scenario "approve" -NoCaps -ExtraArguments $noWait
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "the worker is set to Opus: no inspector run on Sonnet: $(Get-CallModels -Calls $run.Calls)"
        Assert-Equal -Expected "inspecting" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "the inspection waits; it is not skipped: $($run.Report)"
        Assert-True -Condition ($run.Report -match "denetim bekliyor: task-one[^\r\n]*işçinin modeli kayıtlı değil[^\r\n]*$opus[^\r\n]*daha zayıf modelde denetlenmez") -Because "the report says which floor and why: $($run.Report)"
        Assert-True -Condition ($run.Report -match "döngü: Max kullanım limiti; sıfırlanma $until") -Because $run.Report

        # Only Fable limited: Opus is at least as strong as the configured worker model, so it runs there.
        $open = New-Task -Id "task-one" -State "inspecting"
        $open.reports = @($entry)
        $openRoot = New-Sandbox -Tasks @($open)
        Set-SandboxFile -Root $openRoot -Name "limits.json" -Json ('{"models":{"' + $fable + '":' + $closed + '},"windows":{}}')
        $ran = Invoke-Cycle -Root $openRoot -Scenario "approve" -NoCaps -ExtraArguments $noWait
        Assert-Equal -Expected "inspector=$opus" -Actual (Get-CallModels -Calls $ran.Calls) -Because "the floor is a floor, not a wait: $($ran.StdOut + $ran.StdErr)"

        # The tool-substitution check uses the same floor.
        $swapped = New-Task -Id "task-one" -State "inspecting"
        $swapped.reports = @($entry)
        $swapRoot = New-Sandbox -Tasks @($swapped)
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_RAN_MODEL = "inspector=$sonnet" } -Body { $script:plainSwap = Invoke-Cycle -Root $swapRoot -Scenario "approve" -NoCaps -ExtraArguments $noWait }
        $swap = $script:plainSwap
        Assert-Equal -Expected "inspecting" -Actual (Get-TaskById -Queue $swap.Queue -Id "task-one").state -Because "an APPROVE the tool ran on Sonnet is not an approval of an Opus-configured worker's task: $($swap.Report)"
        Assert-True -Condition ($swap.Report -match "hüküm alınmadı") -Because $swap.Report
        Assert-True -Condition (@($swap.Calls | Where-Object { $_.model -eq $sonnet }).Count -eq 0) -Because "and no inspector run was STARTED on Sonnet either: $(Get-CallModels -Calls $swap.Calls)"
    }

    Test-Case "the inspector's floor is the STRONGEST worker run of the task, not the last: worked on Fable, reworked on Sonnet - no inspection on Sonnet (ADR-0214 addendum 10)" {
        # The inspection of model-policy-cycle: a Fable worker run, returned, reworked on Sonnet
        # (lowered by the chain) was inspected on Sonnet and merged.
        $worked = [pscustomobject]@{ cycle = "c0"; role = "worker"; at = "2026-10-01T09:00:00Z"; file = "team/reports/c0/task-one-worker-1.md"; cost_usd = 0.25; outcome = "tamam (model $fable)"; summary = @("sha: see the branch") }
        $reworked = [pscustomobject]@{ cycle = "c0"; role = "worker"; at = "2026-10-01T11:00:00Z"; file = "team/reports/c0/task-one-worker-2.md"; cost_usd = 0.25; outcome = "tamam (model $sonnet)"; summary = @("sha: see the branch") }
        $task = New-Task -Id "task-one" -State "inspecting"
        $task.reports = @($worked, $reworked)
        $root = New-Sandbox -Tasks @($task)
        $until = Get-TeamTimestamp -Now ([datetime]::UtcNow.AddHours(1))
        $closed = '{"until":"' + $until + '","type":"seven_day","seen_at":"2026-10-01T09:30:00Z"}'
        Set-SandboxFile -Root $root -Name "limits.json" -Json ('{"models":{"' + $fable + '":' + $closed + ',"' + $opus + '":' + $closed + '},"windows":{}}')
        $run = Invoke-Cycle -Root $root -Scenario "approve" -NoCaps -ExtraArguments $noWait
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "Fable and Opus limited: no inspector run on Sonnet: $(Get-CallModels -Calls $run.Calls)"
        Assert-Equal -Expected "inspecting" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "the inspection waits: $($run.Report)"
        Assert-True -Condition ($run.Report -match "denetim bekliyor: task-one[^\r\n]*işçi $fable[^\r\n]*daha zayıf modelde denetlenmez") -Because "the report says the inspection waits for Fable: $($run.Report)"

        # Fable open (Opus limited): the inspection runs on Fable.
        $open = New-Task -Id "task-one" -State "inspecting"
        $open.reports = @($worked, $reworked)
        $openRoot = New-Sandbox -Tasks @($open)
        Set-SandboxFile -Root $openRoot -Name "limits.json" -Json ('{"models":{"' + $opus + '":' + $closed + '},"windows":{}}')
        $ran = Invoke-Cycle -Root $openRoot -Scenario "approve" -NoCaps -ExtraArguments $noWait
        Assert-Equal -Expected "inspector=$fable" -Actual (Get-CallModels -Calls $ran.Calls) -Because "inspected on Fable: $($ran.StdOut + $ran.StdErr)"
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $ran.Queue -Id "task-one").state -Because $ran.Report
    }

    Test-Case "a failed run whose long report merely QUOTES 'usage limit reached' is a plain failure: no model is barred and nothing is lowered" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_QUOTED_LIMIT_ROLES = "worker" } -Body { $script:quoteRun = Invoke-Cycle -Root $root -Scenario "approve" -NoCaps -ExtraArguments $noWait }
        $run = $script:quoteRun
        Assert-True -Condition (@($run.Calls).Count -ge 1) -Because "the worker ran: $($run.StdOut + $run.StdErr)"
        Assert-Equal -Expected 0 -Actual @($run.Calls | Where-Object { $_.model -ne $opus }).Count -Because "every run on the configured Opus - nothing lowered: $(Get-CallModels -Calls $run.Calls)"
        Assert-True -Condition ($run.Report -notmatch "model düşürüldü") -Because "no lowering line: $($run.Report)"
        Assert-True -Condition ($run.Report -notmatch "Max kullanım limiti") -Because "not read as the limit: $($run.Report)"
        Assert-True -Condition ([int](Get-TeamProperty -InputObject (Get-TaskById -Queue $run.Queue -Id "task-one") -Name "failed_runs" -Default 0) -ge 1) -Because "it is the task's failure"
        $limitsFile = Join-Path $root "team\limits.json"
        if (Test-Path -LiteralPath $limitsFile) {
            $kept = Read-TeamJson -Path $limitsFile
            Assert-True -Condition ($null -eq $kept.models.PSObject.Properties[$opus]) -Because "Opus is not barred: $($kept | ConvertTo-Json -Depth 6 -Compress)"
        }
        $status = Read-TeamJson -Path (Join-Path $root "team\status.json")
        Assert-Equal -Expected 0 -Actual @($status.limits.lowered).Count -Because "nothing lowered in the status"
    }

    Test-Case "a session limit closes every model: no lowered run is started, the status says 'all' is limited, and the existing stop line follows" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_LIMITED_MODELS = $opus; PAGENTOS_FAKE_CLAUDE_LIMIT_TYPE = "five_hour" } -Body { $script:sessionRun = Invoke-Cycle -Root $root -Scenario "approve" -NoCaps -ExtraArguments $noWait }
        $run = $script:sessionRun
        Assert-Equal -Expected "worker=$opus" -Actual (Get-CallModels -Calls $run.Calls) -Because "a run on Sonnet would hit the same limit: none is started"
        Assert-True -Condition ($run.Report -notmatch "model düşürüldü") -Because "and no false 'model düşürüldü' line: $($run.Report)"
        Assert-True -Condition ($run.Report -match "döngü: Max kullanım limiti; sıfırlanma \d{4}-") -Because $run.Report
        $status = Read-TeamJson -Path (Join-Path $root "team\status.json")
        Assert-Equal -Expected "limited" -Actual $status.limits.all.state -Because "the limit of all models"
        Assert-True -Condition ([string]$status.limits.all.resets_at -match '^\d{4}-') -Because "with its reset"
        Assert-Equal -Expected "limited" -Actual $status.limits.fable.state -Because "and so Fable too"
    }

    Test-Case "the two percentages are the tool's own numbers: null until a run gave one, never computed, gone once their window has reset; every run in the status carries its model" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $snapshots = Join-Path $root "snapshots"
        $hooks = @{ PAGENTOS_FAKE_CLAUDE_SNAPSHOT = $snapshots; PAGENTOS_FAKE_CLAUDE_STATUS = (Join-Path $root "team\status.json") }
        Use-FakeHooks -Environment $hooks -Body { $script:pctRun = Invoke-Cycle -Root $root -Scenario "approve" }
        Assert-Equal -Expected 0 -Actual $script:pctRun.ExitCode -Because ($script:pctRun.StdOut + $script:pctRun.StdErr)
        $during = Read-TeamJson -Path (Join-Path $snapshots "worker-task-one.json")
        Assert-Equal -Expected "worker=$opus" -Actual (@($during.runs | ForEach-Object { "$($_.role)=$($_.model)" }) -join ",") -Because "the run in flight names the model it was started on: $($during | ConvertTo-Json -Depth 6 -Compress)"
        Assert-True -Condition ($null -ne $during.limits.fable.PSObject.Properties["used_pct"] -and $null -eq $during.limits.fable.used_pct) -Because "no run has finished: Fable's percentage is null (the page says 'bilinmiyor'), not 0"
        Assert-Equal -Expected $null -Actual $during.limits.all.used_pct -Because "and so is the one of all models"
        Assert-Equal -Expected "ok" -Actual $during.limits.fable.state -Because "nothing said otherwise"
        $inspecting = Read-TeamJson -Path (Join-Path $snapshots "inspector-task-one.json")
        Assert-Equal -Expected 46 -Actual $inspecting.limits.all.used_pct -Because "the worker's run on Opus carried the week of all models"
        Assert-Equal -Expected $null -Actual $inspecting.limits.fable.used_pct -Because "but no Fable window: still null while the first Fable run is in flight"
        $final = Read-TeamJson -Path (Join-Path $root "team\status.json")
        Assert-Equal -Expected 81 -Actual $final.limits.fable.used_pct -Because "the inspector's run on Fable gave the Fable week"
        Assert-Equal -Expected 46 -Actual $final.limits.all.used_pct -Because "the week of all models"
        Assert-Equal -Expected 0 -Actual @($final.limits.lowered).Count -Because "nothing was lowered"
        # The next cycle starts from what the last one saw; a window whose reset has passed is dropped.
        $kept = Read-TeamJson -Path (Join-Path $root "team\limits.json")
        Assert-Equal -Expected 81 -Actual $kept.windows.fable.used_pct -Because "the numbers are kept with their reset and when they were seen: $($kept | ConvertTo-Json -Depth 6 -Compress)"
        Set-SandboxFile -Root $root -Name "limits.json" -Json '{"models":{},"windows":{"fable":{"used_pct":81,"resets_at":"2026-09-30T00:00:00Z","observed_at":"2026-09-29T00:00:00Z"},"all":{"used_pct":46,"resets_at":"2099-01-01T00:00:00Z","observed_at":"2026-09-29T00:00:00Z"}}}'
        Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document (New-Queue -Tasks @())
        [void](Invoke-Cycle -Root $root -Scenario "approve" -CycleId "c2")
        $next = Read-TeamJson -Path (Join-Path $root "team\status.json")
        Assert-Equal -Expected $null -Actual $next.limits.fable.used_pct -Because "that week is over: the number describes a window that no longer exists"
        Assert-Equal -Expected 46 -Actual $next.limits.all.used_pct -Because "a window still open keeps its last seen number"
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

    Test-Case "an approved task whose files a task in work holds waits for it, and is run when they are free" {
        # Two approved tasks that share a file and no dependency were both moved into work: a
        # queue Test-TeamQueue refuses, so the NEXT cycle ran nothing at all. (On 2026-10-02 the
        # live queue held such a pair, waiting only for their common dependency to reach main.)
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/area")), (New-Task -Id "task-two" -Area @("src/area/deep", "src/second")))
        $snapshots = Join-Path $root "snapshots"
        $hooks = @{ PAGENTOS_FAKE_CLAUDE_SNAPSHOT = $snapshots; PAGENTOS_FAKE_CLAUDE_STATUS = (Join-Path $root "team\status.json") }
        Use-FakeHooks -Environment $hooks -Body { $script:holderRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 4 }
        $run = $script:holderRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $order = @($run.Calls | ForEach-Object { "$($_.role):$($_.task)" }) -join ","
        Assert-Equal -Expected "worker:task-one,inspector:task-one,worker:task-two,inspector:task-two" -Actual $order -Because "one after the other, in the same cycle"
        # The order of the STARTS is the same when task-two's worker starts BESIDE task-one's
        # inspector (the inspector of 3d52902e: 'inspecting' as a holder was unproven). What was
        # in flight while each run worked says it: the holder's inspection ran alone, and so did
        # the waiter's worker.
        foreach ($alone in @("inspector-task-one", "worker-task-two")) {
            $seen = Read-TeamJson -Path (Join-Path $snapshots "$alone.json")
            $expected = ($alone -replace '^(\w+)-(.+)$', '$2:$1')
            Assert-Equal -Expected $expected -Actual (@(@($seen.runs) | ForEach-Object { "$($_.task):$($_.role)" } | Sort-Object) -join ",") -Because "alone in flight during ${alone}: $($seen | ConvertTo-Json -Depth 5 -Compress)"
        }
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "the first"
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "task-two").state -Because "and the one that waited"
        Assert-True -Condition ($run.Report -match "bekliyor: task-two -> task-one aynı dosyaları bırakınca") -Because "the wait is said: $($run.Report)"
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $run.Queue).Count -Because "the queue it left keeps the protocol"
    }

    Test-Case "a returned task holds its files too: an approved task that shares them is not started beside its rework" {
        $back = New-Task -Id "task-one" -State "returned" -Area @("src/area") -Branch "team/c1/worker-task-one"
        $root = New-Sandbox -Tasks @($back, (New-Task -Id "task-two" -Area @("src/area/deep")))
        [void](Invoke-SandboxGit -Root $root -Arguments @("branch", "team/c1/worker-task-one", "main"))
        $snapshots = Join-Path $root "snapshots"
        $hooks = @{ PAGENTOS_FAKE_CLAUDE_SNAPSHOT = $snapshots; PAGENTOS_FAKE_CLAUDE_STATUS = (Join-Path $root "team\status.json") }
        Use-FakeHooks -Environment $hooks -Body { $script:returnedRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 4 }
        Assert-Equal -Expected 0 -Actual $script:returnedRun.ExitCode -Because ($script:returnedRun.StdOut + $script:returnedRun.StdErr)
        $seen = Read-TeamJson -Path (Join-Path $snapshots "worker-task-one.json")
        Assert-Equal -Expected "task-one:worker" -Actual (@(@($seen.runs) | ForEach-Object { "$($_.task):$($_.role)" } | Sort-Object) -join ",") -Because "the rework ran alone: $($seen | ConvertTo-Json -Depth 5 -Compress)"
        Assert-Equal -Expected "merged,merged" -Actual (@(Get-TeamTasks -Queue $script:returnedRun.Queue | ForEach-Object { $_.state }) -join ",") -Because "and both end merged"
    }

    Test-Case "a task that comes back from its integrator with a plan waits for the holder of its files as any approved task does" {
        $plain = New-Task -Id "task-plain" -Area @("src/area")
        $study = New-Task -Id "task-study" -Area @("src/area/deep")
        $study | Add-Member -NotePropertyName "needs_integration" -NotePropertyValue $true
        $study.created_at = "2026-09-30T00:00:01Z"
        $root = New-Sandbox -Tasks @($plain, $study)
        $snapshots = Join-Path $root "snapshots"
        $hooks = @{ PAGENTOS_FAKE_CLAUDE_SNAPSHOT = $snapshots; PAGENTOS_FAKE_CLAUDE_STATUS = (Join-Path $root "team\status.json") }
        Use-FakeHooks -Environment $hooks -Body { $script:studyRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 4 }
        Assert-Equal -Expected 0 -Actual $script:studyRun.ExitCode -Because ($script:studyRun.StdOut + $script:studyRun.StdErr)
        $seen = Read-TeamJson -Path (Join-Path $snapshots "inspector-task-plain.json")
        Assert-Equal -Expected "task-plain:inspector" -Actual (@(@($seen.runs) | ForEach-Object { "$($_.task):$($_.role)" } | Sort-Object) -join ",") -Because "the planned task's worker was NOT started beside the holder's inspection: $($seen | ConvertTo-Json -Depth 5 -Compress)"
        Assert-Equal -Expected "merged,merged" -Actual (@(Get-TeamTasks -Queue $script:studyRun.Queue | ForEach-Object { $_.state }) -join ",") -Because "and both end merged"
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $script:studyRun.Queue).Count -Because "the queue it left keeps the protocol"
    }

    Test-Case "the two area rules are one rule: what the cycle would move into work, the queue's judge accepts - and an area that is the whole repository is no area" {
        # The inspector of 3d52902e: Get-TeamAreaHolders and Test-TeamQueue compared areas with two
        # different keys and disagreed on 4 of 17 pairs, in the dangerous direction - the cycle
        # moved the task and the judge refused the queue it had made.
        $pairs = @(
            @("src/area", "src/area"), @("src/area", "src/area/deep"), @("src/area/", "src/area/deep"), @("src/area/*", "src/area/deep"),
            @("SRC/Area", "src/area/deep"), @("src\area", "src/area/deep"), @("./src/area", "src/area/deep"), @(" src/area", "src/area/deep"),
            @("src/area ", "src/area /deep"), @("src/area", "src/area2"), @("src/area", "src/are"), @("src/a.py", "src/a.pyc"),
            @("docs", "src"), @("src/area/x.py", "src/area")
        )
        foreach ($pair in $pairs) {
            $holder = New-Task -Id "task-one" -State "in_progress" -Area @($pair[0])
            $waiter = New-Task -Id "task-two" -Area @($pair[1])
            $held = @(Get-TeamAreaHolders -Task $waiter -Queue (New-Queue -Tasks @($holder, $waiter))).Count -gt 0
            $moved = New-Task -Id "task-two" -State "assigned" -Area @($pair[1])
            $refused = @(Test-TeamQueue -Queue (New-Queue -Tasks @($holder, $moved)) | Where-Object { $_ -match "overlaps" }).Count -gt 0
            Assert-Equal -Expected $refused -Actual $held -Because "'$($pair[0])' against '$($pair[1])': the judge refuses the pair = the cycle holds the task back"
        }
        foreach ($whole in @("*", ".", "./", "/*", ".\")) {
            $problems = @(Test-TeamQueue -Queue (New-Queue -Tasks @((New-Task -Id "task-one" -Area @($whole)))))
            Assert-True -Condition (@($problems | Where-Object { $_ -match "whole repository|inside the repository" }).Count -gt 0) -Because "'$whole' is refused as an area: $($problems -join '; ')"
        }
    }

    # ------------------------------------------------------------------ the queue on the Cloud Core
    Write-Host ""
    Write-Host "the queue and the lock on the Cloud Core (a fake listener with the real routes' rules)"

    function Start-FakeApi {
        param([object[]]$Tasks = @(), $Lock = $null, [switch]$FailStatus, $Models = $null, [switch]$NoModels, [switch]$LegacyStatus,
            # Another writer of the store: these tasks are put into it once -LateAfter task PUTs came in,
            # or - with -LateOnRun "<role>:<task>" - when the live status names that run in flight.
            [object[]]$Late = @(), [int]$LateAfter = 1, [string]$LateOnRun = "",
            # With -LateOnRun: wait for this many more GETs of the queue first; and ids to take out.
            [int]$LateAfterGets = 0, [string[]]$LateRemove = @(),
            # The store broken on purpose: @{ queue_get_after = N } and/or @{ task_put = @{ id; status } }.
            $Faults = $null)
        $work = Join-Path $env:TEMP ("pagentos-teamapi-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
        [void](New-Item -ItemType Directory -Force -Path $work)
        [void]$sandboxes.Add($work)
        $lockDocument = if ($null -ne $Lock) { $Lock } else { New-TeamLockReleased }
        $seed = [pscustomobject]@{ queue = (New-Queue -Tasks $Tasks); lock = $lockDocument }
        if ($null -ne $Models) { $seed | Add-Member -NotePropertyName models -NotePropertyValue $Models }
        if (@($Late).Count -gt 0 -or @($LateRemove).Count -gt 0) {
            $second = [pscustomobject]@{ after_task_puts = $LateAfter; tasks = @($Late); remove = @($LateRemove) }
            if ($LateOnRun) { $second | Add-Member -NotePropertyName on_status_run -NotePropertyValue $LateOnRun }
            if ($LateAfterGets -gt 0) { $second | Add-Member -NotePropertyName after_queue_gets -NotePropertyValue $LateAfterGets }
            $seed | Add-Member -NotePropertyName late -NotePropertyValue $second
        }
        if ($null -ne $Faults) { $seed | Add-Member -NotePropertyName faults -NotePropertyValue $Faults }
        [System.IO.File]::WriteAllText((Join-Path $work "seed.json"), (ConvertTo-Json -InputObject $seed -Depth 12), (New-Object System.Text.UTF8Encoding($false)))
        $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
        $probe.Start(); $port = $probe.LocalEndpoint.Port; $probe.Stop()
        $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + (Join-Path $repoRoot "scripts\team\fake-team-api.ps1") + '"'),
            "-Port", $port, "-Seed", ('"' + (Join-Path $work "seed.json") + '"'), "-Log", ('"' + (Join-Path $work "requests.log") + '"'),
            "-Ready", ('"' + (Join-Path $work "ready") + '"'), "-Token", "test-token")
        if ($FailStatus) { $arguments += "-FailStatus" }
        if ($NoModels) { $arguments += "-NoModels" }
        if ($LegacyStatus) { $arguments += "-LegacyStatus" }
        $process = Start-Process -FilePath $powershell -ArgumentList $arguments -PassThru -WindowStyle Hidden
        [void]$fakeApis.Add($process)
        $deadline = [datetime]::UtcNow.AddSeconds(40)
        while (-not (Test-Path -LiteralPath (Join-Path $work "ready"))) {
            if ([datetime]::UtcNow -gt $deadline -or $process.HasExited) { throw "the fake team API did not start" }
            Start-Sleep -Milliseconds 200
        }
        $tokenFile = Join-Path $work "token.txt"
        [System.IO.File]::WriteAllText($tokenFile, "test-token`n", (New-Object System.Text.UTF8Encoding($false)))
        return [pscustomobject]@{ Url = "http://127.0.0.1:$port"; Port = $port; Work = $work; TokenFile = $tokenFile; Process = $process }
    }

    function Get-FakeApiState {
        param($Api)
        return (Invoke-JsonUtf8 -Uri ($Api.Url + "/__state"))
    }

    function Get-FakeApiRequests {
        param($Api)
        # The fake writes a request's line AFTER it has answered it, so a reader that comes
        # straight from the answer can find the log one line short (the gate, 2026-09-30:
        # "expected 1, actual 0"). The listener serves one request at a time: by the time it
        # answers THIS call, every earlier line is written. The barrier's own lines are not
        # requests of the code under test and are left out.
        [void](Get-FakeApiState -Api $Api)
        $log = Join-Path $Api.Work "requests.log"
        if (-not (Test-Path -LiteralPath $log)) { return @() }
        return @(Get-Content -LiteralPath $log -Encoding UTF8 | Where-Object { $_.Trim() -and $_ -notmatch ' /__state ' })
    }

    # Four tests below replay a store that refuses or fails a write at a moment defined by the
    # ORDER of a pair's runs: the second task's inspection is in flight when the first one's
    # inspection ends, and stays in flight until the first one's result is applied. They pinned
    # that order with one poll every six seconds and hoped both runs of a pair ended inside it;
    # at about forty processes they did not (cycle-seat-pool, inspector 2). The fake's barrier
    # now decides it (cycle-pool-test-barriers): task-one's worker waits until task-two's
    # inspector has started, and task-two's inspector waits until task-one's work is on the
    # integration branch. The cycle is one thread: once that merge is there, nothing of
    # task-two's inspection is looked at before task-one's result has been written - or tried.
    function Get-PairBarrier {
        param([string]$Root, [string]$Markers)
        $merged = Join-Path $Root ".claude\worktrees\integrate\c1\src\area\task-one.txt"
        return @{ PAGENTOS_FAKE_CLAUDE_MARKERS = $Markers; PAGENTOS_FAKE_CLAUDE_BARRIER = "worker:task-one=inspector-task-two.started,inspector:task-two=$merged" }
    }
    function Assert-BarriersOpened {
        <# Every barrier of the case was opened by the event it waited for, none by the hang guard:
           a run let go by the guard means the order the case names did not happen. #>
        param([string]$Markers, [string[]]$Runs)
        foreach ($name in $Runs) {
            $path = Join-Path $Markers "$name.ended"
            Assert-True -Condition (Test-Path -LiteralPath $path) -Because "$name ended"
            Assert-Equal -Expected "file" -Actual ([string](Read-Marker -Path $path).barrier) -Because "$name was let go by the event it waited for, not by the hang guard"
        }
    }

    Test-Case "each run is told how to reach the team's board: its seat, its task, the address and the token file's path" {
        # The board (ADR team-board, the owner's idea of 2026-10-03): board.ps1 reads
        # PAGENTOS_TEAM_URL / PAGENTOS_TEAM_TOKEN_FILE; a note names its seat (worker-1..9).
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one"))
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $byRole = @{}; foreach ($call in $run.Calls) { $byRole[[string]$call.role] = $call }
        Assert-Equal -Expected "worker-1" -Actual ([string]$byRole["worker"].team_seat) -Because "a worker run sits at worker-1 when no other worker runs"
        Assert-Equal -Expected "inspector" -Actual ([string]$byRole["inspector"].team_seat) -Because "the inspector's seat"
        foreach ($call in $run.Calls) {
            Assert-Equal -Expected "task-one" -Actual ([string]$call.team_task) -Because "$($call.role): the task it works on"
            Assert-Equal -Expected $api.Url -Actual ([string]$call.team_url) -Because "$($call.role): the board's address is the queue's"
            Assert-Equal -Expected $api.TokenFile -Actual ([string]$call.team_token_file) -Because "$($call.role): the token file's PATH"
            Assert-True -Condition (([string]$call.team_token_file) -ne (Get-Content -Raw -LiteralPath $api.TokenFile).Trim()) -Because "never the token itself"
        }
        # without the API (files), no address is handed out, and the seat still is
        $plain = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $files = Invoke-Cycle -Root $plain -Scenario "approve"
        foreach ($call in $files.Calls) {
            Assert-Equal -Expected "" -Actual ([string]$call.team_url) -Because "no queue URL, no board address"
            Assert-True -Condition ([bool][string]$call.team_seat) -Because "the seat is set: $($call.role)"
        }
        # the status carries the board's seat only as a worker run's number (office-stable-seats):
        # `seat: 1` for worker-1, nothing for the inspector - never the board's "worker-1" / "inspector"
        $statuses = @((Get-FakeApiState -Api $api).statuses)
        $line = (@($statuses | ForEach-Object { @($_.runs) | Where-Object { $null -ne $_ } | ForEach-Object { "$($_.role)@$(if ($null -ne $_.PSObject.Properties['seat']) { $_.seat } else { '-' })" } }) -join ",")
        Assert-True -Condition ($line -match "worker@1" -and $line -match "inspector@-" -and $line -notmatch "worker@worker|@inspector") -Because "a worker's number, no seat for the inspector: $line"
    }

    Test-Case "the test team's round is told the board's address too: the queue URL and the token file's path in API mode, neither in file mode" {
        # test-round-board-address (2026-10-06): every round printed 'pano: UYARI: ... panonun adresi
        # verilmedi' - the round was started without PAGENTOS_TEAM_URL / PAGENTOS_TEAM_TOKEN_FILE, so
        # the test seats never posted and the Ofis showed them idle while they worked.
        $saved = @{}; foreach ($name in @("PAGENTOS_TEAM_URL", "PAGENTOS_TEAM_TOKEN_FILE")) { $saved[$name] = [Environment]::GetEnvironmentVariable($name) }
        try {
            foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $null) }
            $api = Start-FakeApi -Tasks @((New-Task -Id "task-one"))
            $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
            $marker = Join-Path $root "round-env.json"
            $fakeRound = Join-Path $root "fake-round.ps1"
            [System.IO.File]::WriteAllText($fakeRound, ("[System.IO.File]::WriteAllText('$marker', (@{ url = [string]`$env:PAGENTOS_TEAM_URL; token_file = [string]`$env:PAGENTOS_TEAM_TOKEN_FILE } | ConvertTo-Json -Compress))"), (New-Object System.Text.UTF8Encoding($false)))
            $run = Invoke-Cycle -Root $root -Scenario "approve" -ExtraArguments "-TestTeam -TestRoundScript '$fakeRound'" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
            Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
            for ($i = 0; $i -lt 50 -and -not (Test-Path -LiteralPath $marker); $i++) { Start-Sleep -Milliseconds 200 }
            Assert-True -Condition (Test-Path -LiteralPath $marker) -Because "the round was started: $($run.StdOut)"
            $seen = [System.IO.File]::ReadAllText($marker) | ConvertFrom-Json
            Assert-Equal -Expected $api.Url -Actual ([string]$seen.url) -Because "the round's board address is the queue's"
            Assert-Equal -Expected $api.TokenFile -Actual ([string]$seen.token_file) -Because "the token file's PATH"
            Assert-True -Condition (([string]$seen.token_file) -ne (Get-Content -Raw -LiteralPath $api.TokenFile).Trim()) -Because "never the token itself"

            # file mode: no address, even when the cycle's own process carries one
            [Environment]::SetEnvironmentVariable("PAGENTOS_TEAM_URL", "https://decoy.invalid")
            [Environment]::SetEnvironmentVariable("PAGENTOS_TEAM_TOKEN_FILE", "C:\decoy.token")
            $plain = New-Sandbox -Tasks @((New-Task -Id "task-one"))
            $plainMarker = Join-Path $plain "round-env.json"
            $plainRound = Join-Path $plain "fake-round.ps1"
            [System.IO.File]::WriteAllText($plainRound, ("[System.IO.File]::WriteAllText('$plainMarker', (@{ url = [string]`$env:PAGENTOS_TEAM_URL; token_file = [string]`$env:PAGENTOS_TEAM_TOKEN_FILE } | ConvertTo-Json -Compress))"), (New-Object System.Text.UTF8Encoding($false)))
            $files = Invoke-Cycle -Root $plain -Scenario "approve" -ExtraArguments "-TestTeam -TestRoundScript '$plainRound'"
            Assert-Equal -Expected 0 -Actual $files.ExitCode -Because ($files.StdOut + $files.StdErr)
            for ($i = 0; $i -lt 50 -and -not (Test-Path -LiteralPath $plainMarker); $i++) { Start-Sleep -Milliseconds 200 }
            Assert-True -Condition (Test-Path -LiteralPath $plainMarker) -Because "the file-mode round was started: $($files.StdOut)"
            $seen = [System.IO.File]::ReadAllText($plainMarker) | ConvertFrom-Json
            Assert-Equal -Expected "" -Actual ([string]$seen.url) -Because "no queue URL, no board address"
            Assert-Equal -Expected "" -Actual ([string]$seen.token_file) -Because "no queue, no token file"
        }
        finally {
            foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name]) }
        }
    }

    Test-Case "duty in API mode: the Proje Yöneticisi's decision is written to the store, through the cycle's own writes" {
        $api = Start-FakeApi -Tasks @((New-Stopped))
        $root = New-Sandbox -Tasks @((New-Stopped))
        $run = Invoke-DutyCycle -Root $root -Decisions @((New-Decision -Reason "API: testi ekle")) -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "lead,worker,inspector" -Actual (Get-Roles -Run $run) -Because $run.Report
        $state = Get-FakeApiState -Api $api
        $task = @($state.tasks | Where-Object { $_.id -eq "stuck-one" })[0]
        Assert-Equal -Expected "merged" -Actual $task.state -Because "the store has the task the duty sent back, worked and merged"
        Assert-True -Condition ([string]$task.reason -ceq "Proje Yöneticisi: API: testi ekle") -Because "'$($task.reason)'"
        Assert-True -Condition (@(Get-FakeApiRequests -Api $api | Where-Object { $_ -match '^PUT /v1/team/queue/tasks/stuck-one 200' }).Count -ge 1) -Because "written through the task route"
        Assert-Equal -Expected "stopped" -Actual (Get-TaskById -Queue (Read-TeamJson -Path (Join-Path $root "team\queue.json")) -Id "stuck-one").state -Because "the file queue is not the store: left alone"
    }

    Test-Case "in API mode the cycle takes the lock through the API, writes the task's states there, posts the report, and leaves the files alone" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one"))
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $state = Get-FakeApiState -Api $api
        $task = @($state.tasks | Where-Object { $_.id -eq "task-one" })[0]
        Assert-Equal -Expected "merged" -Actual $task.state -Because "the state was written to the API"
        Assert-True -Condition ($task.sha -match "^[0-9a-f]{40}$") -Because "the sha reached the API too: $($task.sha)"
        Assert-Equal -Expected 2 -Actual @($task.reports).Count -Because "the worker's and the inspector's report"
        Assert-Equal -Expected $false -Actual ([bool]$state.lock.held) -Because "the lock was released through the API"
        $requests = @(Get-FakeApiRequests -Api $api)
        Assert-Equal -Expected 2 -Actual @($requests | Where-Object { $_ -match "^POST /v1/team/queue/lock 200" }).Count -Because "one acquire, one release"
        Assert-True -Condition (@($requests | Where-Object { $_ -match "^PUT /v1/team/queue/tasks/task-one 200" }).Count -ge 2) -Because ($requests -join "; ")
        Assert-True -Condition (@($requests | Where-Object { $_ -match "^GET /v1/team/queue 200" }).Count -ge 2) -Because "the queue is read at the start and again before each pass: $($requests -join '; ')"
        Assert-True -Condition ($state.reports.PSObject.Properties["c1.md"].Value -match "pilot|c1") -Because "the report text is in the store"
        Assert-Equal -Expected "approved" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "the sandbox's queue.json was not written in API mode"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "nor was its lock.json ever held"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $root "team\reports\c1.md")) -Because "the report is still a file"
    }

    Test-Case "a task that reaches the store while the cycle runs is run by that same cycle" {
        # 2026-10-02: the cycle read the queue once, at its start, and a cycle with work never
        # ends. The owner's "4. çalışan koltuğu" card, put into the store nine minutes after the
        # cycle began, waited four hours beside idle seats; so would a card the feeder cut and a
        # decision the owner clicked in the Onay Merkezi.
        $late = New-Task -Id "task-two" -Area @("src/other")
        $late.created_at = "2026-09-30T00:00:01Z"; $late.updated_at = "2026-09-30T00:00:01Z"
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Late @($late) -LateAfter 1
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $state = Get-FakeApiState -Api $api
        Assert-Equal -Expected "merged" -Actual (@($state.tasks | Where-Object { $_.id -eq "task-one" })[0]).state -Because "the task the cycle began with"
        $two = @($state.tasks | Where-Object { $_.id -eq "task-two" })[0]
        Assert-Equal -Expected "merged" -Actual $two.state -Because "the task that came while the cycle ran was run, not left for the next cycle"
        Assert-Equal -Expected 2 -Actual @($two.reports).Count -Because "its worker and its inspector"
        Assert-Equal -Expected 0 -Actual @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match " 409$" }).Count -Because "and nothing was written over a version the cycle had not read"
    }

    Test-Case "a decision made in the store while the cycle runs is the cycle's next pass: a proposed task that became approved is run" {
        $waiting = New-Task -Id "task-two" -State "proposed" -Area @("src/other")
        $decided = New-Task -Id "task-two" -Area @("src/other")
        $decided.updated_at = "2026-09-30T09:00:00Z"
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one"), $waiting) -Late @($decided) -LateAfter 1
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $state = Get-FakeApiState -Api $api
        Assert-Equal -Expected "merged" -Actual (@($state.tasks | Where-Object { $_.id -eq "task-two" })[0]).state -Because "the owner's word was seen in the same cycle"
        # The decision landed between the cycle's read and its own write of that task (proposed ->
        # awaiting_owner): that ONE write is refused by the store and dropped, and nothing else is.
        Assert-Equal -Expected 1 -Actual @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match " 409$" }).Count -Because "the cycle's stale write of the task was refused, once"
        Assert-True -Condition ($run.Report -match "task-two: depoda başkası değiştirdi") -Because "and the report says whose word stood: $($run.Report)"
        Assert-Equal -Expected "merged" -Actual (@($state.tasks | Where-Object { $_.id -eq "task-one" })[0]).state -Because "the other task was written as ever"
    }

    Test-Case "a task whose write the store refused is not run on the copy the cycle has: the store's 'stopped' stands, no worker is started" {
        # The inspector of 3d52902e, PROBE-C: the lead stopped a task between the cycle's read and
        # its first save; the write was refused, the copy stayed 'assigned' and a worker was started.
        $stopped = New-Task -Id "task-two" -State "stopped" -Area @("src/other")
        $stopped.updated_at = "2026-09-30T09:00:00Z"
        $stopped | Add-Member -NotePropertyName reason -NotePropertyValue "the lead stopped it"
        $first = New-Task -Id "task-one"
        $second = New-Task -Id "task-two" -Area @("src/other")
        $second.created_at = "2026-09-30T00:00:01Z"
        $api = Start-FakeApi -Tasks @($first, $second) -Late @($stopped) -LateAfter 1
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected 0 -Actual @($run.Calls | Where-Object { $_.task -eq "task-two" }).Count -Because "nothing was started for the stopped task: $(@($run.Calls | ForEach-Object { "$($_.role):$($_.task)" }) -join ',')"
        $state = Get-FakeApiState -Api $api
        Assert-Equal -Expected "stopped" -Actual (@($state.tasks | Where-Object { $_.id -eq "task-two" })[0]).state -Because "the store's word stands"
        Assert-Equal -Expected "merged" -Actual (@($state.tasks | Where-Object { $_.id -eq "task-one" })[0]).state -Because "the other task went on"
        Assert-Equal -Expected 1 -Actual @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match "^PUT /v1/team/queue/tasks/task-two 409" }).Count -Because "one refused write, not one per save"
    }

    Test-Case "a task stopped in the store while its inspector runs is not merged: the run's result is not applied" {
        # The inspector of 3d52902e, PROBE-A: the merge into the integration branch ran BEFORE the
        # write the store then refused, so stopped work reached the branch the lead gates.
        $stopped = New-Task -Id "task-one" -State "stopped"
        $stopped.updated_at = "2026-09-30T09:00:00Z"
        $stopped | Add-Member -NotePropertyName reason -NotePropertyValue "the lead stopped it"
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Late @($stopped) -LateOnRun "inspector:task-one"
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $state = Get-FakeApiState -Api $api
        $task = @($state.tasks | Where-Object { $_.id -eq "task-one" })[0]
        Assert-Equal -Expected "stopped" -Actual $task.state -Because "the store's word stands"
        $integration = Join-Path $root ".claude\worktrees\integrate\c1"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $integration "src\area\task-one.txt"))) -Because "the stopped task's work is NOT on the integration branch"
        Assert-True -Condition ($run.Report -match "task-one: koşu \(inspector\) sürerken depoda başkası değiştirdi; koşunun sonucu uygulanmadı") -Because "and the report says so: $($run.Report)"
        Assert-True -Condition ($run.Report -notmatch "task-one[^\r\n]*\[merged\]") -Because "the report does not call it merged"
        Assert-Equal -Expected 0 -Actual @($task.reports).Count -Because "nothing of the run was written over the stop"
        # The stop arrives as the run starts: the cycle's own "an inspector took it" write is the
        # one refused write. The RESULT is never sent, so it is never refused.
        Assert-True -Condition (@(Get-FakeApiRequests -Api $api | Where-Object { $_ -match " 409$" }).Count -le 1) -Because "at most the start's write was refused: $((Get-FakeApiRequests -Api $api) -join '; ')"
    }

    Test-Case "a card edited while its worker runs: the run's result is not applied, the edit is kept, and the task is worked again from the store's version" {
        # The store changes AFTER the cycle's own "a worker took it" write: nothing of the cycle's
        # was refused, so only asking the store when the run ends can see it. The price is said
        # in the ADR: the first run's work is repeated (its report stays in its file).
        $edited = New-Task -Id "task-one" -State "in_progress" -Branch "team/c1/worker-task-one"
        $edited.assignee = "worker"; $edited.worktree = ".claude/worktrees/team/c1/worker-task-one"
        $edited.updated_at = "2026-09-30T09:00:00Z"
        $edited | Add-Member -NotePropertyName goal -NotePropertyValue "the lead's new wording"
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Late @($edited) -LateAfter 2
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "worker:task-one,worker:task-one,inspector:task-one" -Actual (@($run.Calls | ForEach-Object { "$($_.role):$($_.task)" }) -join ",") -Because "worked again from the store's version, then inspected"
        $task = @((Get-FakeApiState -Api $api).tasks | Where-Object { $_.id -eq "task-one" })[0]
        Assert-Equal -Expected "merged" -Actual $task.state -Because "and it ends merged"
        Assert-Equal -Expected "the lead's new wording" -Actual ([string]$task.goal) -Because "the edit was not written over"
        Assert-Equal -Expected 0 -Actual @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match " 409$" }).Count -Because "the first run's result was never sent: $((Get-FakeApiRequests -Api $api) -join '; ')"
        Assert-True -Condition ($run.Report -match "task-one: koşu \(worker\) sürerken depoda başkası değiştirdi; koşunun sonucu uygulanmadı") -Because "the report says it: $($run.Report)"
    }

    Test-Case "each run's result is written when it is applied, not when the batch ends: the store has the first task's merge while the second inspection still runs" {
        # The inspector of 3d1be9fc, PROBE-I: the store was asked when a run ended, the merge was
        # made, and NOTHING was written until the last run of the batch ended - with six seats, the
        # window in which a stop could arrive unseen was the rest of the batch.
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one" -Area @("src/area")), (New-Task -Id "task-two" -Area @("src/area2")))
        $root = New-Sandbox -Tasks @()
        $markers = Join-Path $root "markers"
        Use-FakeHooks -Environment (Get-PairBarrier -Root $root -Markers $markers) -Body {
            $script:appliedRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 2 -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        }
        $run = $script:appliedRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-BarriersOpened -Markers $markers -Runs @("worker-task-one", "inspector-task-two")
        $lines = @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match "^(GET /v1/team/queue|PUT /v1/team/queue/tasks/task-(one|two)) 200" } | ForEach-Object { ($_ -replace '^(\w+) /v1/team/queue/?(tasks/)?', '$1 ') -replace ' 200$', '' })
        $tail = @($lines)[(@($lines).Count - 6)..(@($lines).Count - 1)] -join " | "
        # Task-one's look and its write; the refill's read; task-two's look and its write; the last read.
        # Written when the batch ended, task-one's 'merged' would follow task-two's look.
        Assert-Equal -Expected "GET  | PUT task-one | GET  | GET  | PUT task-two | GET " -Actual $tail -Because "asked, written - while task-two's inspection was held in flight; then asked, written: $($lines -join ' | ') :: calls $(@($run.Calls | ForEach-Object { "$($_.role):$($_.task)" }) -join ',') :: $(Get-FakeApiRequests -Api $api | Select-Object -Last 14) :: $((Get-FakeApiState -Api $api).tasks | ConvertTo-Json -Depth 6 -Compress)"
    }

    Test-Case "a merge whose write could only be tried at the batch's end and was refused there cannot be taken back: the report NAMES the merge" {
        # The store is away when task-one's merge is to be written (the other inspection is still in
        # flight), task-two is merged on top of it, and when the store answers again task-one has been
        # stopped there. The integration branch has moved on: nothing is reset, the lead is told.
        $stopped = New-Task -Id "task-one" -State "stopped" -Area @("src/area")
        $stopped.updated_at = "2026-09-30T09:00:00Z"
        # A broken card arrives with the stop: the store cannot be read again afterwards, so what
        # the report says of task-one is what the cycle's OWN copy says (the inspector's PROBE-Z).
        $broken = New-Task -Id "task-three" -Area @("src/third")
        $broken | Add-Member -NotePropertyName depends_on -NotePropertyValue @("task-three")
        $outage = [pscustomobject]@{ task_put_when_runs = [pscustomobject]@{ runs = 1; status = 503 } }
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one" -Area @("src/area")), (New-Task -Id "task-two" -Area @("src/area2"))) -Faults $outage `
            -Late @($stopped, $broken) -LateOnRun "inspector:task-one" -LateAfterGets 1
        $root = New-Sandbox -Tasks @()
        $markers = Join-Path $root "markers"
        Use-FakeHooks -Environment (Get-PairBarrier -Root $root -Markers $markers) -Body {
            $script:namedRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 2 -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        }
        $run = $script:namedRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-BarriersOpened -Markers $markers -Runs @("worker-task-one", "inspector-task-two")
        $state = Get-FakeApiState -Api $api
        Assert-Equal -Expected "stopped" -Actual (@($state.tasks | Where-Object { $_.id -eq "task-one" })[0]).state -Because "the store's word stands"
        Assert-Equal -Expected "merged" -Actual (@($state.tasks | Where-Object { $_.id -eq "task-two" })[0]).state -Because "the other task was written"
        Assert-True -Condition ($run.Report -match "task-one: entegrasyon dalına \(integrate/c1\) BİRLEŞTİRİLDİ, sonra depo yazmayı reddetti[^\r\n]*o birleştirme dalda duruyor") -Because "the lead is told there is a merge to take back: $($run.Report)"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $root ".claude\worktrees\integrate\c1\src\area2\task-two.txt")) -Because "task-two's merge, made on top of it, was not thrown away"
        Assert-True -Condition ($run.Report -match "kuyruk yeniden okunamadı") -Because "the store could not be read again: $($run.Report)"
        Assert-True -Condition ($run.Report -notmatch "task-one[^\r\n]*\[merged\]") -Because "and the cycle's own copy does not call the refused task merged"
    }

    Test-Case "a store that refuses the same write after every read does not spin the cycle: it ends, says so, and the stop flag and the lock are honoured" {
        # The inspector of 3d1be9fc, PROBE-N: 13 578 reads and 13 576 refused writes in 100 seconds,
        # the stop flag and the time cap ignored, the lock held.
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Faults ([pscustomobject]@{ task_put = [pscustomobject]@{ id = "task-one"; status = 409 } })
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "nothing was started for a task the store will not let the cycle take"
        $requests = @(Get-FakeApiRequests -Api $api)
        Assert-True -Condition (@($requests | Where-Object { $_ -match "^GET /v1/team/queue 200" }).Count -le 6) -Because "a handful of reads, not thousands: $(@($requests).Count) requests"
        Assert-True -Condition ($run.Report -match "depo aynı yazmayı üst üste reddetti") -Because "the report says why it ended: $($run.Report)"
        Assert-Equal -Expected $false -Actual ([bool](Get-FakeApiState -Api $api).lock.held) -Because "the lock is released"
    }

    Test-Case "a task whose write was refused is not looked at again while the store cannot be read: no run on the stale copy in any later pass" {
        $stopped = New-Task -Id "task-two" -State "stopped" -Area @("src/other")
        $stopped.updated_at = "2026-09-30T09:00:00Z"
        $broken = New-Task -Id "task-three" -Area @("src/third")
        $broken | Add-Member -NotePropertyName depends_on -NotePropertyValue @("task-three")
        $second = New-Task -Id "task-two" -Area @("src/other")
        $second.created_at = "2026-09-30T00:00:01Z"
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one"), $second) -Late @($stopped, $broken) -LateAfter 1
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "worker:task-one,inspector:task-one" -Actual (@($run.Calls | ForEach-Object { "$($_.role):$($_.task)" }) -join ",") -Because "the stopped task was never run, in the first pass or after"
        Assert-Equal -Expected "stopped" -Actual (@((Get-FakeApiState -Api $api).tasks | Where-Object { $_.id -eq "task-two" })[0]).state -Because "the store's word stands"
    }

    Test-Case "a task taken out of the store while its inspector runs is not merged" {
        # Taken out right AFTER the cycle's own "an inspector took it" write (its fourth write of the task):
        # nothing of the cycle's is refused, so only the look at the store when the run ends can see it.
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -LateRemove @("task-one") -LateAfter 4
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected 0 -Actual @((Get-FakeApiState -Api $api).tasks | Where-Object { $_.id -eq "task-one" }).Count -Because "it is not written back into the store"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $root ".claude\worktrees\integrate\c1\src\area\task-one.txt"))) -Because "and its work is not on the integration branch"
        Assert-True -Condition ($run.Report -match "task-one: koşu \(inspector\) sürerken depoda başkası değiştirdi") -Because "the report says it: $($run.Report)"
    }

    Test-Case "a dropped run that came back limited: the model's limit is still registered, and the try is handed back" {
        $edited = New-Task -Id "task-one" -State "in_progress" -Branch "team/c1/worker-task-one"
        $edited.assignee = "worker"; $edited.worktree = ".claude/worktrees/team/c1/worker-task-one"
        $edited.updated_at = "2026-09-30T09:00:00Z"
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Late @($edited) -LateAfter 2
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_LIMITED_MODELS = $opus; PAGENTOS_FAKE_CLAUDE_LIMITED_ROLES = "worker" } -Body {
            $script:droppedLimited = Invoke-Cycle -Root $root -Scenario "approve" -NoCaps -ExtraArguments ($noWait + " -MaxRunsPerTask 2") -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        }
        $run = $script:droppedLimited
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "worker=$opus,worker=$sonnet,inspector=$fable" -Actual (Get-CallModels -Calls $run.Calls) -Because "the second worker did not try the limited model again: the limit was registered though the run was dropped"
        Assert-Equal -Expected "merged" -Actual (@((Get-FakeApiState -Api $api).tasks | Where-Object { $_.id -eq "task-one" })[0]).state -Because "with two tries allowed the task still finished: the dropped run was not counted"
    }

    Test-Case "a store that does not keep an idea's text does not stop the cycle; the NEXT cycle posts it - an idea never stays without its text" {
        # The inspector of 3d1be9fc, PROBE-L: only the ideas queued in that run were posted, so one
        # failed POST left the idea without text for ever - the incident of 2026-10-02 again.
        $api = Start-FakeApi -Tasks @() -Faults ([pscustomobject]@{ proposal_post = [pscustomobject]@{ status = 500; times = 1 } })
        $root = New-Sandbox -Tasks @()
        $first = Invoke-Cycle -Root $root -Scenario "approve" -Research -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $first.ExitCode -Because ($first.StdOut + $first.StdErr)
        Assert-True -Condition ($first.Report -match "fikrin metni depoya yazılamadı") -Because "a line in the report, not a stop: $($first.Report)"
        $state = Get-FakeApiState -Api $api
        Assert-Equal -Expected 0 -Actual @($state.proposals.PSObject.Properties).Count -Because "the store did not keep it"
        Assert-Equal -Expected $false -Actual ([bool]$state.lock.held) -Because "and the lock is released"
        $second = Invoke-Cycle -Root $root -Scenario "approve" -CycleId "c2" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $second.ExitCode -Because ($second.StdOut + $second.StdErr)
        $state = Get-FakeApiState -Api $api
        $idea = @($state.tasks | Where-Object { $_.state -eq "awaiting_owner" })[0]
        $name = [System.IO.Path]::GetFileName([string]$idea.proposal)
        Assert-True -Condition ($null -ne $state.proposals.PSObject.Properties[$name]) -Because "the next cycle - with no researcher run of its own - posted the waiting idea's text"
    }

    Test-Case "a store that is away while a batch is in flight does not end the cycle: the result is written when the store answers again" {
        # The inspector of 91c70543, PROBE-O: one 503 on the write of a finished run's result ended
        # the cycle with the other run still in flight - both tasks left in_progress, the lock
        # released, the finished worker's result lost. The cycle before that write existed lived
        # through the same outage.
        $outage = [pscustomobject]@{ task_put_when_runs = [pscustomobject]@{ runs = 1; status = 503 } }
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one" -Area @("src/area")), (New-Task -Id "task-two" -Area @("src/area2"))) -Faults $outage
        $root = New-Sandbox -Tasks @()
        $run = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 2 -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $state = Get-FakeApiState -Api $api
        Assert-Equal -Expected "merged,merged" -Actual (@($state.tasks | ForEach-Object { $_.state }) -join ",") -Because "both tasks finished and were written"
        Assert-True -Condition (@(Get-FakeApiRequests -Api $api | Where-Object { $_ -match "^PUT /v1/team/queue/tasks/task-\w+ 503" }).Count -ge 2) -Because "the outage was really met, in both batches"
        Assert-True -Condition ($run.Report -match "sonuç depoya şimdi yazılamadı") -Because "and it is a line in the report: $($run.Report)"
        Assert-Equal -Expected $false -Actual ([bool]$state.lock.held) -Because "the lock is released at the END, not in the middle"
    }

    Test-Case "a task whose runs are dropped again and again is left alone after three: the hand-back of the try is not a way to run it for ever" {
        # The inspector of 91c70543, PROBE-P: a store that takes the move and refuses every "a worker
        # took it" write - 133 paid worker runs in 61 seconds with no budget cap.
        $refuses = [pscustomobject]@{ task_put = [pscustomobject]@{ id = "task-one"; status = 409; state = "in_progress" } }
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Faults $refuses
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        # The harness's budget cap is left ON: a cycle that does not stop by itself ends on the cap, and the count says so.
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected 3 -Actual @($run.Calls | Where-Object { $_.role -eq "worker" -and $_.task -eq "task-one" }).Count -Because "three runs whose result could not be applied, then none"
        Assert-True -Condition ($run.Report -match "task-one: üç koşusunun sonucu uygulanamadı") -Because "the report says the cycle left it: $($run.Report)"
        Assert-Equal -Expected $false -Actual ([bool](Get-FakeApiState -Api $api).lock.held) -Because "the lock is released"
    }

    Test-Case "a merge the store then refuses is taken back at once when it is still the integration branch's last commit" {
        $stopped = New-Task -Id "task-one" -State "stopped"
        $stopped.updated_at = "2026-09-30T09:00:00Z"
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Late @($stopped) -LateOnRun "inspector:task-one" -LateAfterGets 1
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $tree = Join-Path $root ".claude\worktrees\integrate\c1"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $tree "src\area\task-one.txt"))) -Because "the stopped task's work is not on the integration branch any more"
        Assert-Equal -Expected "" -Actual (Invoke-SandboxGit -Root $tree -Arguments @("status", "--porcelain")) -Because "and the integration worktree is clean"
        Assert-Equal -Expected (Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "main")).Trim() -Actual (Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD")).Trim() -Because "it is back where it was before the merge"
        Assert-True -Condition (Test-TeamBranch -RepoRoot $root -Branch "team/c1/worker-task-one") -Because "the task's own branch is untouched"
        Assert-True -Condition ($run.Report -match "task-one: entegrasyon dalına \(integrate/c1\) BİRLEŞTİRİLDİ, sonra depo yazmayı reddetti[^\r\n]*birleştirme GERİ ALINDI") -Because "the report says both: $($run.Report)"
    }

    Test-Case "a refused 'merged' is not forgotten when the same save then fails on another task: the merge is still named" {
        # The inspector of 3ce11180, PROBE-S: the store refused task-one's 'merged' and, in the same
        # save, answered an error to task-two's write. The refusal was known only at the END of that
        # save; the error threw first. The stopped task's merge stayed on the integration branch,
        # neither taken back nor named, and the report sent the lead to gate that branch.
        $stopped = New-Task -Id "task-one" -State "stopped" -Area @("src/area")
        $stopped.updated_at = "2026-09-30T09:00:00Z"
        $faults = [pscustomobject]@{
            task_put_when_runs = [pscustomobject]@{ runs = 1; status = 503 }
            task_put           = [pscustomobject]@{ id = "task-two"; status = 503; state = "merged"; times = 1 }
        }
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one" -Area @("src/area")), (New-Task -Id "task-two" -Area @("src/area2"))) -Faults $faults `
            -Late @($stopped) -LateOnRun "inspector:task-one" -LateAfterGets 1
        $root = New-Sandbox -Tasks @()
        $markers = Join-Path $root "markers"
        Use-FakeHooks -Environment (Get-PairBarrier -Root $root -Markers $markers) -Body {
            $script:forgottenRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 2 -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        }
        $run = $script:forgottenRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-BarriersOpened -Markers $markers -Runs @("worker-task-one", "inspector-task-two")
        $requests = @(Get-FakeApiRequests -Api $api)
        Assert-Equal -Expected 1 -Actual @($requests | Where-Object { $_ -match "^PUT /v1/team/queue/tasks/task-one 409" }).Count -Because "task-one's 'merged' was refused"
        Assert-True -Condition (@($requests | Where-Object { $_ -match "^PUT /v1/team/queue/tasks/task-two 503" }).Count -ge 1) -Because "and task-two's write failed in the same save: $($requests -join '; ')"
        $state = Get-FakeApiState -Api $api
        Assert-Equal -Expected "stopped" -Actual (@($state.tasks | Where-Object { $_.id -eq "task-one" })[0]).state -Because "the store's word stands"
        Assert-Equal -Expected "merged" -Actual (@($state.tasks | Where-Object { $_.id -eq "task-two" })[0]).state -Because "task-two was written when the store answered"
        Assert-True -Condition ($run.Report -match "task-one: depoda başkası değiştirdi") -Because "the refusal is said: $($run.Report)"
        Assert-True -Condition ($run.Report -match "task-one: entegrasyon dalına \(integrate/c1\) BİRLEŞTİRİLDİ, sonra depo yazmayı reddetti") -Because "and the merge is named: $($run.Report)"
    }

    Test-Case "a refused 'merged' is TAKEN BACK when the same save then fails on another task: the refusal is acted on in that iteration" {
        # The inspector's PROBE-S, as it ran it: task-two's "an inspector took it" write fails, so
        # task-two is still unwritten when task-one's result is saved - the store refuses task-one
        # and THEN answers an error to task-two in that one save. The refusal must be known in
        # that iteration (the merge is still the branch's last commit), not only at the batch's end.
        $stopped = New-Task -Id "task-one" -State "stopped" -Area @("src/area")
        $stopped.updated_at = "2026-09-30T09:00:00Z"
        # Every write of task-two as 'inspecting' by an inspector fails (it used to be the first two:
        # the number of saves between them was the batch's). Its 'merged' is written.
        $faults = [pscustomobject]@{ task_put = [pscustomobject]@{ id = "task-two"; status = 503; state = "inspecting"; assignee = "inspector" } }
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one" -Area @("src/area")), (New-Task -Id "task-two" -Area @("src/area2"))) -Faults $faults `
            -Late @($stopped) -LateOnRun "inspector:task-one" -LateAfterGets 1
        $root = New-Sandbox -Tasks @()
        $markers = Join-Path $root "markers"
        Use-FakeHooks -Environment (Get-PairBarrier -Root $root -Markers $markers) -Body {
            $script:takenBackRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 2 -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        }
        $run = $script:takenBackRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-BarriersOpened -Markers $markers -Runs @("worker-task-one", "inspector-task-two")
        $requests = @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match "^(GET /v1/team/queue|PUT /v1/team/queue/tasks/task-\w+) " } | ForEach-Object { ($_ -replace '^(\w+) /v1/team/queue/?(tasks/)?', '$1 ') })
        $refused = [array]::IndexOf([string[]]$requests, "PUT task-one 409")
        Assert-True -Condition ($refused -gt 0 -and [array]::LastIndexOf([string[]]$requests, "PUT task-one 409") -eq $refused) -Because "task-one's 'merged' was refused, once: $($requests -join ' | ')"
        Assert-True -Condition (@($requests[0..($refused - 1)] | Where-Object { $_ -eq "PUT task-two 503" }).Count -ge 1) -Because "task-two's start was a failed write before it: $($requests -join ' | ')"
        Assert-Equal -Expected "GET  200 | PUT task-one 409 | PUT task-two 503" -Actual ($requests[($refused - 1)..($refused + 1)] -join " | ") -Because "task-one's look at the store, then the refusal and the failure in ONE save: $($requests -join ' | ')"
        $state = Get-FakeApiState -Api $api
        Assert-Equal -Expected "stopped" -Actual (@($state.tasks | Where-Object { $_.id -eq "task-one" })[0]).state -Because "the store's word stands"
        Assert-Equal -Expected "merged" -Actual (@($state.tasks | Where-Object { $_.id -eq "task-two" })[0]).state -Because "task-two finished"
        $tree = Join-Path $root ".claude\worktrees\integrate\c1"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $tree "src\area\task-one.txt"))) -Because "the stopped task's merge was taken back"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $tree "src\area2\task-two.txt")) -Because "and task-two's stands"
        Assert-True -Condition ($run.Report -match "task-one: entegrasyon dalına \(integrate/c1\) BİRLEŞTİRİLDİ, sonra depo yazmayı reddetti[^\r\n]*birleştirme GERİ ALINDI") -Because "the report says so: $($run.Report)"
    }

    Test-Case "after a refused write the cycle's own copy says what the store let it say: the report does not list a merge that was taken back" {
        # The inspector of 3ce11180, PROBE-U: the merge was taken back and the store could not be
        # read again (another card broke the queue); the report listed the task as merged and
        # sent the lead to gate a branch that did not hold the work.
        $stopped = New-Task -Id "task-one" -State "stopped"
        $stopped.updated_at = "2026-09-30T09:00:00Z"
        $broken = New-Task -Id "task-three" -Area @("src/third")
        $broken | Add-Member -NotePropertyName depends_on -NotePropertyValue @("task-three")
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Late @($stopped, $broken) -LateOnRun "inspector:task-one" -LateAfterGets 1
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-True -Condition ($run.Report -match "birleştirme GERİ ALINDI") -Because "the merge was taken back: $($run.Report)"
        Assert-True -Condition ($run.Report -match "kuyruk yeniden okunamadı") -Because "and the store could not be read again"
        Assert-True -Condition ($run.Report -notmatch "task-one[^\r\n]*\[merged\]") -Because "the task is not listed as merged: $($run.Report)"
        Assert-True -Condition ($run.Report -notmatch "üzerinde tam kapı ve main'e birleştirme") -Because "and the lead is not sent to gate a branch that holds nothing of it"
        Assert-Equal -Expected 1 -Actual @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match "^PUT /v1/team/queue/tasks/task-one 409" }).Count -Because "the refused write is not sent again"
    }

    Test-Case "an idea's file is read from the proposals folder of -TeamRoot, wherever that is" {
        $root = New-Sandbox -Tasks @()
        $elsewhere = Join-Path $root "other-team"
        foreach ($folder in @((Join-Path $root "team\proposals"), (Join-Path $elsewhere "proposals"))) { [void](New-Item -ItemType Directory -Force -Path $folder) }
        Set-Content -LiteralPath (Join-Path $root "team\proposals\waiting.md") -Value "the repository's own folder" -Encoding UTF8
        Set-Content -LiteralPath (Join-Path $elsewhere "proposals\waiting.md") -Value "the folder of -TeamRoot" -Encoding UTF8
        $idea = New-Task -Id "idea-one" -State "awaiting_owner" -Area @()
        $idea.roadmap_row = ""
        $idea | Add-Member -NotePropertyName proposal -NotePropertyValue "team/proposals/waiting.md"
        $api = Start-FakeApi -Tasks @($idea)
        $run = Invoke-Cycle -Root $root -Scenario "approve" -ExtraArguments ("-TeamRoot '" + $elsewhere + "'") -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $posted = (Get-FakeApiState -Api $api).proposals.PSObject.Properties["waiting.md"]
        Assert-True -Condition ($null -ne $posted) -Because "the idea's text was posted"
        Assert-True -Condition ([string]$posted.Value -match "the folder of -TeamRoot") -Because "from the folder the cycle was told is the team's: $($posted.Value)"
    }

    Test-Case "a store that is away at the moment a run starts does not end the cycle either: the run goes on and its result is written" {
        $outage = [pscustomobject]@{ task_put_when_runs = [pscustomobject]@{ runs = 1; status = 503 } }
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Faults $outage
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 1 -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "worker:task-one,inspector:task-one" -Actual (@($run.Calls | ForEach-Object { "$($_.role):$($_.task)" }) -join ",") -Because "each run once"
        Assert-Equal -Expected "merged" -Actual (@((Get-FakeApiState -Api $api).tasks | Where-Object { $_.id -eq "task-one" })[0]).state -Because "written when the store answered again"
        Assert-True -Condition (@(Get-FakeApiRequests -Api $api | Where-Object { $_ -match "^PUT /v1/team/queue/tasks/task-one 503" }).Count -ge 2) -Because "the write after each start met the outage"
        Assert-True -Condition ($run.Report -match "koşuların başlangıcı: sonuç depoya şimdi yazılamadı") -Because "a line in the report: $($run.Report)"
    }

    Test-Case "a branch that was ALREADY on the integration branch is never taken back: only the merge this run made is the cycle's to undo" {
        $root = New-Sandbox -Tasks @()
        [void](Invoke-SandboxGit -Root $root -Arguments @("checkout", "-q", "-b", "team/c1/worker-task-one", "main"))
        Set-Content -LiteralPath (Join-Path $root "src\area\task-one.txt") -Value "work on task-one" -Encoding ASCII
        [void](Invoke-SandboxGit -Root $root -Arguments @("add", "-A"))
        [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-m", "work on task-one"))
        [void](Invoke-SandboxGit -Root $root -Arguments @("checkout", "-q", "main"))
        $earlier = Merge-TeamBranch -RepoRoot $root -CycleId "c1" -Branch "team/c1/worker-task-one" -Base "main"
        Assert-True -Condition ($earlier.Merged -and -not $earlier.Already) -Because "an earlier cycle's merge"
        $tree = Join-Path $root ".claude\worktrees\integrate\c1"
        $before = (Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD")).Trim()
        $inspecting = New-Task -Id "task-one" -State "inspecting" -Branch "team/c1/worker-task-one"
        $inspecting.worktree = ".claude/worktrees/team/c1/worker-task-one"
        $stopped = New-Task -Id "task-one" -State "stopped"
        $stopped.updated_at = "2026-09-30T09:00:00Z"
        $api = Start-FakeApi -Tasks @($inspecting) -Late @($stopped) -LateOnRun "inspector:task-one" -LateAfterGets 1
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected $before -Actual (Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD")).Trim() -Because "the integration branch is where the earlier merge left it"
        Assert-True -Condition ($run.Report -match "task-one: entegrasyon dalına \(integrate/c1\) BİRLEŞTİRİLDİ, sonra depo yazmayı reddetti[^\r\n]*o birleştirme dalda duruyor") -Because "named, not undone: $($run.Report)"
    }

    Test-Case "Undo-TeamMerge takes back the last merge of that branch and nothing else: not an older merge, not from a dirty tree, not a plain commit" {
        $root = New-Sandbox -Tasks @()
        foreach ($name in @("a", "b")) {
            [void](Invoke-SandboxGit -Root $root -Arguments @("checkout", "-q", "-b", "team/c1/worker-$name", "main"))
            Set-Content -LiteralPath (Join-Path $root "src\area\$name.txt") -Value "work of $name" -Encoding ASCII
            [void](Invoke-SandboxGit -Root $root -Arguments @("add", "-A"))
            [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-m", "work of $name"))
            [void](Invoke-SandboxGit -Root $root -Arguments @("checkout", "-q", "main"))
        }
        Assert-Equal -Expected $false -Actual (Undo-TeamMerge -RepoRoot $root -CycleId "c1" -Branch "team/c1/worker-a") -Because "no integration worktree yet"
        [void](Merge-TeamBranch -RepoRoot $root -CycleId "c1" -Branch "team/c1/worker-a" -Base "main")
        $tree = Join-Path $root ".claude\worktrees\integrate\c1"
        $afterA = (Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD")).Trim()
        [void](Merge-TeamBranch -RepoRoot $root -CycleId "c1" -Branch "team/c1/worker-b" -Base "main")
        $afterB = (Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD")).Trim()
        Assert-Equal -Expected $false -Actual (Undo-TeamMerge -RepoRoot $root -CycleId "c1" -Branch "team/c1/worker-a") -Because "a is not the last merge: b stands on it"
        Assert-Equal -Expected $afterB -Actual (Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD")).Trim() -Because "nothing moved"
        Set-Content -LiteralPath (Join-Path $tree "unsaved.txt") -Value "somebody's work" -Encoding ASCII
        Assert-Equal -Expected $false -Actual (Undo-TeamMerge -RepoRoot $root -CycleId "c1" -Branch "team/c1/worker-b") -Because "the worktree is not clean"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $tree "unsaved.txt")) -Because "and the stray file is still there"
        Remove-Item -LiteralPath (Join-Path $tree "unsaved.txt") -Force
        Assert-Equal -Expected $true -Actual (Undo-TeamMerge -RepoRoot $root -CycleId "c1" -Branch "team/c1/worker-b") -Because "b is the last merge"
        Assert-Equal -Expected $afterA -Actual (Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD")).Trim() -Because "back on a's merge"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $tree "src\area\b.txt"))) -Because "b's file is gone from the integration branch"
        Assert-True -Condition (Test-TeamBranch -RepoRoot $root -Branch "team/c1/worker-b") -Because "b's own branch is untouched"
        $again = Merge-TeamBranch -RepoRoot $root -CycleId "c1" -Branch "team/c1/worker-b" -Base "main"
        Assert-True -Condition ($again.Merged -and -not $again.Already) -Because "and it can be merged again: a reset, not a revert"
        Set-Content -LiteralPath (Join-Path $tree "plain.txt") -Value "a plain commit" -Encoding ASCII
        [void](Invoke-SandboxGit -Root $tree -Arguments @("add", "-A"))
        [void](Invoke-SandboxGit -Root $tree -Arguments @("commit", "-q", "-m", "a plain commit on the integration branch"))
        Assert-Equal -Expected $false -Actual (Undo-TeamMerge -RepoRoot $root -CycleId "c1" -Branch "team/c1/worker-b") -Because "HEAD is not a merge"
    }

    Test-Case "only a file of team/proposals is ever posted as an idea's text, for a proposed idea as for a waiting one" {
        # The inspector of 91c70543: with the path guard removed, team/plans/secret.md, a path that
        # climbs out of the folder and a source file went to the store.
        $root = New-Sandbox -Tasks @()
        $folder = Join-Path $root "team\proposals"
        [void](New-Item -ItemType Directory -Force -Path $folder)
        foreach ($name in @("waiting.md", "proposed.md", "other.md")) { Set-Content -LiteralPath (Join-Path $folder $name) -Value "# Öneri: $name" -Encoding UTF8 }
        [void](New-Item -ItemType Directory -Force -Path (Join-Path $root "team\plans"))
        Set-Content -LiteralPath (Join-Path $root "team\plans\secret.md") -Value "not a proposal" -Encoding UTF8
        Set-Content -LiteralPath (Join-Path $root "outside.md") -Value "not a proposal" -Encoding UTF8
        $ideas = @()
        $number = 0
        foreach ($pair in @(@("awaiting_owner", "team/proposals/waiting.md"), @("proposed", "team/proposals/proposed.md"), @("awaiting_owner", "team/plans/secret.md"),
                @("awaiting_owner", "team/proposals/../../outside.md"), @("awaiting_owner", "src/area/README.txt"), @("awaiting_owner", "team/proposals/missing.md"), @("awaiting_owner", "team/plans/other.md"))) {
            $number++
            $idea = New-Task -Id "idea-$number" -State $pair[0] -Area @()
            $idea.roadmap_row = ""
            $idea | Add-Member -NotePropertyName proposal -NotePropertyValue $pair[1]
            $ideas += $idea
        }
        $api = Start-FakeApi -Tasks $ideas
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $posted = @((Get-FakeApiState -Api $api).proposals.PSObject.Properties | ForEach-Object { $_.Name } | Sort-Object) -join ","
        Assert-Equal -Expected "proposed.md,waiting.md" -Actual $posted -Because "the two proposals, and nothing that is not one"
    }

    Test-Case "only a stale write is one task's: a write the store answers with an error ends the cycle as before" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Faults ([pscustomobject]@{ task_put = [pscustomobject]@{ id = "task-one"; status = 500 } })
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-True -Condition ($run.ExitCode -ne 0 -or ($run.StdOut + $run.StdErr) -match "HTTP 500") -Because "a 500 is not swallowed as 'somebody else changed it': exit $($run.ExitCode) $($run.StdOut) $($run.StdErr)"
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "and nothing was started on a store that does not take the cycle's writes"
        Assert-Equal -Expected $false -Actual ([bool](Get-FakeApiState -Api $api).lock.held) -Because "the lock is released all the same"
    }

    Test-Case "a store that stops answering while the cycle runs changes nothing: the cycle finishes on the copy it has, and says so once" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Faults ([pscustomobject]@{ queue_get_after = 1 })
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "merged" -Actual (@((Get-FakeApiState -Api $api).tasks | Where-Object { $_.id -eq "task-one" })[0]).state -Because "the cycle's task was finished and written"
        $notes = @($run.Report -split "`n" | Where-Object { $_ -match "kuyruk yeniden okunamadı" })
        Assert-Equal -Expected 1 -Actual @($notes).Count -Because "said once: $($run.Report)"
        Assert-True -Condition ($notes[0] -match "503") -Because "with what the store answered: $($notes[0])"
    }

    Test-Case "an idea the researcher wrote is in the store WITH its text: the Onay Merkezi's Detay has something to show" {
        # 2026-10-02: two ideas waited for the owner with proposal_text of 0 characters. ADR-0236
        # gave the store a place for the text and nothing posted it.
        $api = Start-FakeApi -Tasks @()
        $root = New-Sandbox -Tasks @()
        $run = Invoke-Cycle -Root $root -Scenario "approve" -Research -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $state = Get-FakeApiState -Api $api
        $idea = @($state.tasks | Where-Object { $_.state -eq "awaiting_owner" })[0]
        Assert-True -Condition ($null -ne $idea) -Because "the idea is queued for the owner"
        $name = [System.IO.Path]::GetFileName([string]$idea.proposal)
        $onDisk = [System.IO.File]::ReadAllText((Join-Path $root ("team\proposals\" + $name)), [System.Text.Encoding]::UTF8)
        Assert-True -Condition ($null -ne $state.proposals.PSObject.Properties[$name]) -Because "its text was posted: $(@($state.proposals.PSObject.Properties | ForEach-Object { $_.Name }) -join ',')"
        Assert-Equal -Expected $onDisk -Actual ([string]$state.proposals.PSObject.Properties[$name].Value) -Because "the file's text, whole"
    }

    Test-Case "a queue that breaks the protocol while the cycle runs changes nothing: the cycle finishes on the copy it has, and says so" {
        $broken = New-Task -Id "task-two" -Area @("src/other")
        $broken | Add-Member -NotePropertyName depends_on -NotePropertyValue @("task-two")
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Late @($broken) -LateAfter 1
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $state = Get-FakeApiState -Api $api
        Assert-Equal -Expected "merged" -Actual (@($state.tasks | Where-Object { $_.id -eq "task-one" })[0]).state -Because "the cycle's own task was finished"
        Assert-Equal -Expected "approved" -Actual (@($state.tasks | Where-Object { $_.id -eq "task-two" })[0]).state -Because "the broken card was neither run nor written"
        $notes = @($run.Report -split "`n" | Where-Object { $_ -match "kuyruk yeniden okunamadı" })
        Assert-Equal -Expected 1 -Actual @($notes).Count -Because "said once, not once per pass: $($run.Report)"
        Assert-True -Condition ($notes[0] -match "depend on itself") -Because "with the reason: $($notes[0])"
    }

    Test-Case "a card that arrives in the store mid-cycle in work without an area is set aside there, with its reason; the cycle goes on reading the store" {
        # 2026-10-06 21:02-21:16: the cards were moved to 'assigned' while cycles ran.
        $bare = New-Task -Id "test-fail-nobet-1" -State "assigned" -Area @()
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Late @($bare) -LateAfter 1
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $state = Get-FakeApiState -Api $api
        Assert-Equal -Expected "merged" -Actual (@($state.tasks | Where-Object { $_.id -eq "task-one" })[0]).state -Because "the cycle's own task was finished"
        $aside = @($state.tasks | Where-Object { $_.id -eq "test-fail-nobet-1" })[0]
        Assert-Equal -Expected "stopped" -Actual $aside.state -Because "set aside in the store: $($run.Report)"
        Assert-True -Condition ([string]$aside.reason -like "alan yok: önce dosya alanı*") -Because "with its reason: $($aside.reason)"
        Assert-Equal -Expected 0 -Actual @($run.Calls | Where-Object { [string]$_.task -eq "test-fail-nobet-1" }).Count -Because "nobody was started for it"
        Assert-True -Condition ($run.Report -notmatch "kuyruk yeniden okunamadı") -Because "the store was read: $($run.Report)"
    }

    Test-Case "in API mode the live status goes through PUT /v1/team/queue/status: the same documents, the limit's wait included, and the end with no run" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one"), (New-Task -Id "task-two" -Area @("src/b")))
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"), (New-Task -Id "task-two" -Area @("src/b")))
        $run = Invoke-Cycle -Root $root -Scenario "limited" -NoCaps -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $requests = @(Get-FakeApiRequests -Api $api)
        Assert-True -Condition (@($requests | Where-Object { $_ -match "^PUT /v1/team/queue/status 200" }).Count -ge 4) -Because ($requests -join "; ")
        $state = Get-FakeApiState -Api $api
        $history = @($state.statuses)
        Assert-True -Condition (@($history | Where-Object { @($_.runs).Count -eq 2 }).Count -ge 1) -Because "a document with both runs in flight"
        Assert-True -Condition (@($history | Where-Object { $_.cycle_id -eq "c1" -and $_.machine -eq "MAIL" }).Count -eq @($history).Count) -Because "every document names the cycle and machine"
        $waiting = @($history | Where-Object { $_.usage_limit.state -eq "waiting" })
        Assert-True -Condition (@($waiting).Count -ge 1 -and [string]$waiting[0].usage_limit.resets_at -match '^\d{4}-') -Because "waiting, with the reset time: $($history | ConvertTo-Json -Depth 6 -Compress)"
        $last = $history[@($history).Count - 1]
        Assert-Equal -Expected 0 -Actual @($last.runs).Count -Because "the last document has no run"
        Assert-Equal -Expected "ok" -Actual $last.usage_limit.state -Because "and no limit"
        Assert-True -Condition ([double]$last.estimated_usd -gt 0) -Because "the estimate is carried"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $root "team\status.json"))) -Because "API mode writes no status file"
    }

    Test-Case "in API mode a failing status PUT is a line under the risks and never stops the cycle" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -FailStatus
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "merged" -Actual @((Get-FakeApiState -Api $api).tasks)[0].state -Because "the work was done"
        Assert-True -Condition ($run.Report -match "canlı durum yazılamadı") -Because "the risk line: $($run.Report)"
    }

    Test-Case "in API mode the other machine's fresh lock stops the cycle before it starts anything, and stays theirs" {
        $held = New-TeamLock -Machine "GMKADIRAKBABA" -CycleId "office" -Now ([datetime]::UtcNow.AddHours(-1))
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Lock $held
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 3 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "nothing ran"
        $state = Get-FakeApiState -Api $api
        Assert-Equal -Expected "GMKADIRAKBABA" -Actual $state.lock.machine -Because "the lock is still theirs"
        Assert-Equal -Expected "approved" -Actual @($state.tasks)[0].state -Because "the queue was not written"
        Assert-Equal -Expected 0 -Actual @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match "^(PUT|DELETE)" }).Count -Because "no write at all"
        Assert-True -Condition ($run.Report -match "kilit GMKADIRAKBABA makinesinde") -Because "the stop is a line in the report"
    }

    Test-Case "in API mode a lock the other machine held for seven hours is taken over, and the report says so" {
        $held = New-TeamLock -Machine "GMKADIRAKBABA" -CycleId "office" -Now ([datetime]::UtcNow.AddHours(-7))
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Lock $held
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected "merged" -Actual @((Get-FakeApiState -Api $api).tasks)[0].state -Because ($run.StdOut + $run.StdErr)
        Assert-True -Condition ($run.Report -match "bayat kilit devral") -Because $run.Report
    }

    Test-Case "a task another writer changed since the cycle read it is not overwritten, and one nobody changed is not written at all" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one"), (New-Task -Id "task-two" -Area @("src/b")))
        $store = New-TeamApiStore -Url $api.Url -TokenFile $api.TokenFile
        $queue = Read-TeamQueueApi -Store $store
        # Someone else (the owner, in the Onay Merkezi) moves task-one after the cycle read it.
        $theirs = New-Task -Id "task-one" -State "stopped"
        $theirs.updated_at = "2026-09-30T05:00:00Z"
        [void](Invoke-TeamApi -Store $store -Method "PUT" -Path "/v1/team/queue/tasks/task-one" `
                -Body ([ordered]@{ task = $theirs; expected_updated_at = "2026-09-30T00:00:00Z" }))
        $before = @(Get-FakeApiRequests -Api $api).Count
        Save-TeamQueueApi -Store $store -Queue $queue
        Assert-Equal -Expected $before -Actual @(Get-FakeApiRequests -Api $api).Count -Because "nothing changed locally: no PUT"
        Set-TeamProperty -InputObject @(Get-TeamTasks -Queue $queue)[1] -Name "state" -Value "assigned"
        Save-TeamQueueApi -Store $store -Queue $queue
        Assert-Equal -Expected ($before + 1) -Actual @(Get-FakeApiRequests -Api $api).Count -Because "task-two changed and was written, with the version it was read at"
        Set-TeamProperty -InputObject @(Get-TeamTasks -Queue $queue)[0] -Name "state" -Value "assigned"
        $message = ""
        try { Save-TeamQueueApi -Store $store -Queue $queue } catch { $message = $_.Exception.Message }
        Assert-True -Condition ($message -match "HTTP 409") -Because "a stale write throws: '$message'"
        Assert-Equal -Expected "stopped" -Actual @((Get-FakeApiState -Api $api).tasks | Where-Object { $_.id -eq "task-one" })[0].state -Because "the owner's decision stands"
    }

    Test-Case "a wrong token is refused by the API, and a token file that is missing or empty is refused before any call" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one"))
        $wrong = Join-Path $api.Work "wrong.txt"
        [System.IO.File]::WriteAllText($wrong, "not-the-token", (New-Object System.Text.UTF8Encoding($false)))
        $message = ""
        try { [void](Read-TeamQueueApi -Store (New-TeamApiStore -Url $api.Url -TokenFile $wrong)) } catch { $message = $_.Exception.Message }
        Assert-True -Condition ($message -match "HTTP 401") -Because "'$message'"
        $threw = $false
        try { [void](New-TeamApiStore -Url $api.Url -TokenFile (Join-Path $api.Work "nothing.txt")) } catch { $threw = $true }
        Assert-True -Condition $threw -Because "a missing token file"
        $empty = Join-Path $api.Work "empty.txt"
        [System.IO.File]::WriteAllText($empty, "  `n", (New-Object System.Text.UTF8Encoding($false)))
        $threw = $false
        try { [void](New-TeamApiStore -Url $api.Url -TokenFile $empty) } catch { $threw = $true }
        Assert-True -Condition $threw -Because "an empty token file"
        Assert-Equal -Expected 1 -Actual @(Get-FakeApiRequests -Api $api).Count -Because "only the wrong-token call reached the API; the missing and empty files made none"
    }

    Test-Case "-QueueUrl without -QueueToken runs nothing, and the token is a path parameter, never the secret itself" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
            ("& '" + (Join-Path $root "scripts\team\cycle.ps1") + "' -CycleId c1 -QueueUrl http://127.0.0.1:9; exit `$LASTEXITCODE"))
        $result = Invoke-NativeProcess -FilePath $powershell -Arguments $arguments -WorkingDirectory $root -TimeoutSeconds 60 -SuccessExitCodes @(0, 1, 2, 3)
        Assert-True -Condition ($result.ExitCode -ne 0) -Because "it must not start"
        Assert-True -Condition (($result.StdOut + $result.StdErr) -match "QueueToken") -Because "it says what is missing: $($result.StdErr)"
        $script = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\team\cycle.ps1"), [System.Text.Encoding]::UTF8)
        Assert-True -Condition ($script -match '\[string\]\$QueueToken') -Because "the parameter is a string path"
        Assert-True -Condition ($script -match 'TokenFile \$QueueToken') -Because "and it is handed to the reader of the file"
    }

    function New-ModelSetting {
        param([string]$Worker = $opus, [string]$Inspector = $fable, [bool]$Fallback = $true)
        return [pscustomobject]@{
            roles = [pscustomobject]@{ lead = $fable; researcher = $opus; integrator = $opus; worker = $Worker; inspector = $Inspector }
            fallback = $Fallback; updated_at = "2026-10-01T10:00:00Z"
        }
    }

    Test-Case "in API mode the model setting comes from GET /v1/team/queue/models and wins over the local file; every run in the status carries its model and the status carries the limits" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Models (New-ModelSetting -Worker $sonnet -Inspector $opus)
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        Set-SandboxFile -Root $root -Name "models.json" -Json ('{"roles":{"worker":"' + $opus + '","inspector":"' + $fable + '"}}')
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "worker=$sonnet,inspector=$opus" -Actual (Get-CallModels -Calls $run.Calls) -Because "the team store's setting, not the file's"
        Assert-True -Condition (@(Get-FakeApiRequests -Api $api | Where-Object { $_ -match "^GET /v1/team/queue/models 200" }).Count -eq 1) -Because "read once, through the API"
        $history = @((Get-FakeApiState -Api $api).statuses)
        $flying = @($history | ForEach-Object { @($_.runs) } | Where-Object { $null -ne $_ })
        Assert-True -Condition (@($flying).Count -ge 2) -Because "runs were seen in flight"
        Assert-Equal -Expected 0 -Actual @($flying | Where-Object { -not (Test-TeamModelId -Model ([string]$_.model)) }).Count -Because "each run carries the id it was started on: $($history | ConvertTo-Json -Depth 6 -Compress)"
        Assert-True -Condition (@($flying | Where-Object { $_.role -eq "worker" -and $_.model -eq $sonnet }).Count -ge 1) -Because "the worker's is the store's"
        $last = $history[@($history).Count - 1]
        Assert-Equal -Expected "fable,all,fallback,lowered" -Actual (@($last.limits.PSObject.Properties | ForEach-Object { $_.Name }) -join ",") -Because "the contract's four keys"
        foreach ($name in @("fable", "all")) {
            Assert-Equal -Expected "state,resets_at,used_pct" -Actual (@($last.limits.$name.PSObject.Properties | ForEach-Object { $_.Name }) -join ",") -Because "limits.$name has the contract's three keys"
        }
        Assert-Equal -Expected 46 -Actual $last.limits.all.used_pct -Because "the week of all models, from the runs' own events"
        Assert-Equal -Expected $null -Actual $last.limits.fable.used_pct -Because "no run was on Fable in this cycle: null"
        Assert-Equal -Expected "ok" -Actual $last.usage_limit.state -Because "what the status had is still there"
    }

    Test-Case "in API mode a Cloud Core without the models route (404) does not stop the cycle: the local file is used, and without one the defaults" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -NoModels
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        Set-SandboxFile -Root $root -Name "models.json" -Json ('{"roles":{"worker":"' + $sonnet + '","inspector":"' + $opus + '"}}')
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "worker=$sonnet,inspector=$opus" -Actual (Get-CallModels -Calls $run.Calls) -Because "the file's setting"
        Assert-True -Condition (@(Get-FakeApiRequests -Api $api | Where-Object { $_ -match "^GET /v1/team/queue/models 404" }).Count -eq 1) -Because "the route was asked and said 404: $(@(Get-FakeApiRequests -Api $api) -join '; ')"
        Assert-Equal -Expected "merged" -Actual @((Get-FakeApiState -Api $api).tasks)[0].state -Because "the work was done"
        $bare = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -NoModels
        $plain = Invoke-Cycle -Root (New-Sandbox -Tasks @((New-Task -Id "task-one"))) -Scenario "approve" -QueueUrl $bare.Url -QueueTokenFile $bare.TokenFile
        Assert-Equal -Expected "worker=$opus,inspector=$fable" -Actual (Get-CallModels -Calls $plain.Calls) -Because "no route and no file: the defaults"
    }

    Test-Case "in API mode a setting that names an unknown model starts nothing and takes no lock" {
        $setting = New-ModelSetting
        $setting.roles.worker = "claude-haiku-4-5-20251001"
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -Models $setting
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 2 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "a model id outside the three never reaches a command line"
        Assert-True -Condition ($run.StdOut -match "claude-haiku-4-5-20251001") -Because "it says which: $($run.StdOut)"
        $state = Get-FakeApiState -Api $api
        Assert-Equal -Expected $false -Actual ([bool]$state.lock.held) -Because "the lock was never taken"
        Assert-Equal -Expected 0 -Actual @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match "^(PUT|POST)" }).Count -Because "no write at all"
    }

    Test-Case "in API mode a Cloud Core that does not know the status' new fields yet (422) still gets the status it knows, and the report says so once" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one")) -LegacyStatus
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $state = Get-FakeApiState -Api $api
        Assert-Equal -Expected "merged" -Actual @($state.tasks)[0].state -Because "the work was done"
        $history = @($state.statuses)
        Assert-True -Condition (@($history | Where-Object { @($_.runs).Count -ge 1 }).Count -ge 2) -Because "the Ofis page still sees the runs in flight: $(@(Get-FakeApiRequests -Api $api) -join '; ')"
        Assert-Equal -Expected 0 -Actual @($history | Where-Object { $null -ne $_.PSObject.Properties["limits"] }).Count -Because "in the form that Cloud Core accepts"
        # office-stable-seats: the legacy form is the one a Cloud Core without `seat` accepts too.
        Assert-Equal -Expected 0 -Actual @($history | ForEach-Object { @($_.runs) } | Where-Object { $null -ne $_ -and $null -ne $_.PSObject.Properties["seat"] }).Count -Because "and no run of it carries a seat: $($history | ConvertTo-Json -Depth 6 -Compress)"
        Assert-Equal -Expected 1 -Actual @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match "^PUT /v1/team/queue/status 422" }).Count -Because "the new form is tried once, not at every write"
        Assert-Equal -Expected 1 -Actual ([regex]::Matches($run.Report, "model ve limit alanlarını henüz tanımıyor")).Count -Because "one line under the risks: $($run.Report)"
    }

    Test-Case "the fake's models route keeps the contract: a whole setting is stored and read back; an unknown model, a missing role, another key and an inspector weaker than the worker are 422" {
        $api = Start-FakeApi -Tasks @()
        $store = New-TeamApiStore -Url $api.Url -TokenFile $api.TokenFile
        $default = Get-TeamModelsApi -Store $store
        Assert-Equal -Expected "$fable/$opus/true" -Actual "$($default.roles.inspector)/$($default.roles.worker)/$("$($default.fallback)".ToLowerInvariant())" -Because "nothing stored: the defaults"
        $stored = Invoke-TeamApi -Store $store -Method "PUT" -Path "/v1/team/queue/models" -Body (New-ModelSetting -Worker $sonnet -Inspector $opus -Fallback $false)
        Assert-Equal -Expected $sonnet -Actual $stored.roles.worker -Because "the PUT answers the setting"
        $back = Get-TeamModelsApi -Store $store
        Assert-Equal -Expected "$sonnet/$opus/false" -Actual "$($back.roles.worker)/$($back.roles.inspector)/$("$($back.fallback)".ToLowerInvariant())" -Because "and GET hands it back"
        $weaker = New-ModelSetting -Worker $fable -Inspector $opus
        $unknown = New-ModelSetting; $unknown.roles.worker = "claude-opus-9"
        $missing = New-ModelSetting; $missing.roles.PSObject.Properties.Remove("lead")
        $extra = New-ModelSetting; $extra | Add-Member -NotePropertyName owner -NotePropertyValue "x"
        $cases = @(@{ Body = $weaker; Code = "inspector_weaker_than_worker" }, @{ Body = $unknown; Code = "unknown_model" }, @{ Body = $missing; Code = "missing_role" }, @{ Body = $extra; Code = "unknown_key" })
        foreach ($case in $cases) {
            $message = ""
            try { [void](Invoke-TeamApi -Store $store -Method "PUT" -Path "/v1/team/queue/models" -Body $case.Body) } catch { $message = $_.Exception.Message }
            Assert-True -Condition ($message -match "HTTP 422" -and $message -match $case.Code) -Because "$($case.Code): '$message'"
        }
        Assert-Equal -Expected $sonnet -Actual (Get-TeamModelsApi -Store $store).roles.worker -Because "a refused PUT changed nothing"
        $gone = Start-FakeApi -Tasks @() -NoModels
        Assert-Equal -Expected $null -Actual (Get-TeamModelsApi -Store (New-TeamApiStore -Url $gone.Url -TokenFile $gone.TokenFile)) -Because "404 is 'this Cloud Core has no such route', not an error"
    }

    # ------------------------------------------------------------------ the pool (cycle-seat-pool)
    Write-Host ""
    Write-Host "the pool: a seat is filled when it is free, the seats are per role, the store is read at every refill"

    function Get-RunNames {
        <# The runs a live status names, as "task:role", sorted. #>
        param($Status)
        return @(@($Status.runs) | Where-Object { $null -ne $_ } | ForEach-Object { "$($_.task):$($_.role)" } | Sort-Object)
    }
    function Get-SnapshotRuns {
        param([string]$Folder, [string]$Name)
        return ((Get-RunNames -Status (Read-TeamJson -Path (Join-Path $Folder "$Name.json"))) -join ",")
    }
    function Get-PoolHooks {
        <# Every run copies the live status a second after it started; the named runs take longer. #>
        param([string]$Root, [string]$Seconds = "")
        $hooks = @{ PAGENTOS_FAKE_CLAUDE_SNAPSHOT = (Join-Path $Root "snapshots"); PAGENTOS_FAKE_CLAUDE_STATUS = (Join-Path $Root "team\status.json"); PAGENTOS_FAKE_CLAUDE_SNAPSHOT_SECONDS = 1 }
        if ($Seconds) { $hooks["PAGENTOS_FAKE_CLAUDE_SECONDS"] = $Seconds }
        return $hooks
    }

    Test-Case "the pool: a seat that is free is filled at once - the third task's worker and the first task's inspector start while the second task's worker is still running" {
        # Cycle adr0224-02: three workers recorded at 3310 / 3295 / 3275 seconds - the two short
        # ones waited for the long one, because a batch was started and then waited for WHOLE.
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/a1")), (New-Task -Id "task-two" -Area @("src/a2")), (New-Task -Id "task-three" -Area @("src/a3")))
        $hooks = Get-PoolHooks -Root $root -Seconds "worker:task-two=8"
        Use-FakeHooks -Environment $hooks -Body { $script:poolRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 2 }
        $run = $script:poolRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $snapshots = Join-Path $root "snapshots"
        $third = Get-SnapshotRuns -Folder $snapshots -Name "worker-task-three"
        Assert-True -Condition ($third -match "task-two:worker" -and $third -match "task-three:worker") -Because "task-three's worker took the seat task-one's left, beside task-two's: $third"
        $inspection = Get-SnapshotRuns -Folder $snapshots -Name "inspector-task-one"
        Assert-True -Condition ($inspection -match "task-two:worker") -Because "task-one was inspected while task-two was still worked on: $inspection"
        foreach ($file in @(Get-ChildItem -LiteralPath $snapshots -Filter *.json)) {
            $names = @(Get-RunNames -Status (Read-TeamJson -Path $file.FullName))
            Assert-True -Condition (@($names | Where-Object { $_ -match ":worker$" }).Count -le 2) -Because "never more workers than worker seats ($($file.Name)): $($names -join ',')"
        }
        Assert-Equal -Expected "merged,merged,merged" -Actual (@(Get-TeamTasks -Queue $run.Queue | ForEach-Object { $_.state }) -join ",") -Because ($run.StdOut + $run.StdErr + $run.Report)
        Assert-Equal -Expected 6 -Actual @($run.Calls).Count -Because "a worker and an inspector each, no run twice"
        Assert-Equal -Expected 6 -Actual ([regex]::Matches($run.Report, "(?m)^- task-(one|two|three) / (worker|inspector): ")).Count -Because "and every run is in the report's run list: $($run.Report)"
    }

    Test-Case "the pool: never more runs of a role in flight than the role has seats - on every status the cycle wrote - and the worker seats are in use beside the inspections" {
        $tasks = @(1..5 | ForEach-Object { New-Task -Id "task-$_" -Area @("src/p$_") })
        $api = Start-FakeApi -Tasks $tasks
        $root = New-Sandbox -Tasks @()
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_SECONDS = "worker:*=2,inspector:*=2" } -Body {
            $script:seatRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 2 -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        }
        Assert-Equal -Expected 0 -Actual $script:seatRun.ExitCode -Because ($script:seatRun.StdOut + $script:seatRun.StdErr)
        $state = Get-FakeApiState -Api $api
        $history = @($state.statuses)
        $most = 0
        foreach ($document in $history) {
            $names = @(Get-RunNames -Status $document)
            $workers = @($names | Where-Object { $_ -match ":worker$" }).Count
            $inspectors = @($names | Where-Object { $_ -match ":inspector$" }).Count
            Assert-True -Condition ($workers -le 2) -Because "-MaxParallel 2 is two worker seats: $($names -join ',')"
            Assert-True -Condition ($inspectors -le 2) -Because "two inspector seats by default: $($names -join ',')"
            Assert-Equal -Expected @($names).Count -Actual @($names | Sort-Object -Unique).Count -Because "a task has one run at a time: $($names -join ',')"
            if ($workers -eq 2 -and $inspectors -ge 1) { $most = [Math]::Max($most, @($names).Count) }
        }
        Assert-True -Condition ($most -ge 3) -Because "both worker seats were in use while an inspection ran: an inspection does not take a worker's seat ($(@($history).Count) statuses)"
        Assert-Equal -Expected "merged,merged,merged,merged,merged" -Actual (@($state.tasks | ForEach-Object { $_.state }) -join ",") -Because "and all the work was done"
    }

    Test-Case "the pool: two tasks with overlapping areas are never in flight together, a third beside them is, and all finish" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one" -Area @("src/area")), (New-Task -Id "task-two" -Area @("src/area/deep", "src/second")), (New-Task -Id "task-three" -Area @("src/third")))
        $root = New-Sandbox -Tasks @()
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_SECONDS = "worker:task-one=3,inspector:task-one=2,worker:task-three=3" } -Body {
            $script:overlapRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 3 -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        }
        Assert-Equal -Expected 0 -Actual $script:overlapRun.ExitCode -Because ($script:overlapRun.StdOut + $script:overlapRun.StdErr)
        $state = Get-FakeApiState -Api $api
        $beside = 0
        foreach ($document in @($state.statuses)) {
            $names = (Get-RunNames -Status $document) -join ","
            Assert-True -Condition (-not ($names -match "task-one:" -and $names -match "task-two:")) -Because "the two that share files, in flight together: $names"
            if ($names -match "task-one:" -and $names -match "task-three:") { $beside++ }
        }
        Assert-True -Condition ($beside -ge 1) -Because "the task on other files ran beside the first"
        Assert-Equal -Expected "merged,merged,merged" -Actual (@($state.tasks | ForEach-Object { $_.state }) -join ",") -Because "and the one that waited was run when the files were free: $($script:overlapRun.Report)"
    }

    Test-Case "the pool: a task the store put into work beside a run in flight that holds its files waits for that run to end; a task on other files is started beside it at once" {
        # The store's rules keep two tasks off the same files on the copy THEY see. The lead stops
        # task-one in the store and puts two tasks into work, one on task-one's files - while the
        # cycle's worker of task-one is still running. Until that run ends the files are its.
        $stopped = New-Task -Id "task-one" -State "stopped" -Area @("src/area")
        $stopped.updated_at = "2026-09-30T09:00:00Z"
        $sameFiles = New-Task -Id "task-two" -State "assigned" -Area @("src/area/deep")
        $otherFiles = New-Task -Id "task-three" -State "assigned" -Area @("src/third")
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-zero" -Area @("src/zero")), (New-Task -Id "task-one" -Area @("src/area"))) `
            -Late @($stopped, $sameFiles, $otherFiles) -LateOnRun "worker:task-one"
        $root = New-Sandbox -Tasks @()
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_SECONDS = "worker:task-one=8,worker:task-three=3" } -Body {
            $script:heldRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 3 -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        }
        $run = $script:heldRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $state = Get-FakeApiState -Api $api
        $beside = 0
        foreach ($document in @($state.statuses)) {
            $names = (Get-RunNames -Status $document) -join ","
            Assert-True -Condition (-not ($names -match "task-one:worker" -and $names -match "task-two:")) -Because "task-two was started on files a run in flight still held: $names"
            if ($names -match "task-one:worker" -and $names -match "task-three:worker") { $beside++ }
        }
        Assert-True -Condition ($beside -ge 1) -Because "the store was read again while task-one's worker ran, and the task on other files took a free seat beside it: $(@($state.statuses | ForEach-Object { (Get-RunNames -Status $_) -join '+' }) -join ' | ')"
        $final = @{}; foreach ($task in @($state.tasks)) { $final[[string]$task.id] = [string]$task.state }
        Assert-Equal -Expected "merged/stopped/merged/merged" -Actual "$($final['task-zero'])/$($final['task-one'])/$($final['task-two'])/$($final['task-three'])" -Because "the lead's stop stands, and the task that waited was run once the files were free: $($run.Report)"
        Assert-True -Condition ($run.Report -match "task-one: koşu \(worker\) sürerken depoda başkası değiştirdi; koşunun sonucu uygulanmadı") -Because "the stopped task's run was not applied: $($run.Report)"
    }

    Test-Case "the pool: two approved inspections that end in the same poll are merged one after the other, and both land" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/a1")), (New-Task -Id "task-two" -Area @("src/a2")))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 2 -ExtraArguments "-PollMilliseconds 5000"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "merged,merged" -Actual (@(Get-TeamTasks -Queue $run.Queue | ForEach-Object { $_.state }) -join ",") -Because ($run.StdOut + $run.StdErr + $run.Report)
        $merges = @((Invoke-SandboxGit -Root $root -Arguments @("log", "--merges", "--format=%s", "main..integrate/c1")) -split "`r?`n" | Where-Object { $_.Trim() } | Sort-Object)
        Assert-Equal -Expected "merge: team/c1/worker-task-one into integrate/c1|merge: team/c1/worker-task-two into integrate/c1" -Actual ($merges -join "|") -Because "two merge commits, one for each"
        $tree = Join-Path $root ".claude\worktrees\integrate\c1"
        Assert-True -Condition ((Test-Path -LiteralPath (Join-Path $tree "src\a1\task-one.txt")) -and (Test-Path -LiteralPath (Join-Path $tree "src\a2\task-two.txt"))) -Because "both tasks' work is on the integration branch"
        Assert-Equal -Expected "" -Actual (Invoke-SandboxGit -Root $tree -Arguments @("status", "--porcelain")) -Because "and its worktree is clean: no merge was left half done"
    }

    Test-Case "the pool: seats are per role - with three inspections and three workers runnable, three workers and two inspectors are in flight together, and the third inspection starts when one ends" {
        # 2026-10-01 22:15, the owner at the Ofis page: three inspections held the cycle's three
        # slots and every worker seat was empty, with eight tasks assigned.
        # The order is the barrier's, not a clock's (a snapshot one second after a start was ten
        # seconds late under load, cycle-seat-pool inspector 2): ins-a ends only when the other
        # four runs of the first refill have started, and ins-b and the three workers end only
        # when ins-c has started. Every status the cycle wrote is in the store's history.
        $tasks = @("ins-a", "ins-b", "ins-c" | ForEach-Object { New-Task -Id $_ -State "inspecting" -Area @("src/$_") })
        $tasks += @("wrk-d", "wrk-e", "wrk-f" | ForEach-Object { New-Task -Id $_ -Area @("src/$_") })
        $api = Start-FakeApi -Tasks $tasks
        $root = New-Sandbox -Tasks @()
        $markers = Join-Path $root "markers"
        $hooks = @{
            PAGENTOS_FAKE_CLAUDE_MARKERS = $markers
            PAGENTOS_FAKE_CLAUDE_BARRIER = ("inspector:ins-a=inspector-ins-b.started+worker-wrk-d.started+worker-wrk-e.started+worker-wrk-f.started," +
                "inspector:ins-b=inspector-ins-c.started,worker:wrk-d=inspector-ins-c.started,worker:wrk-e=inspector-ins-c.started,worker:wrk-f=inspector-ins-c.started")
        }
        Use-FakeHooks -Environment $hooks -Body { $script:roleRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 3 -QueueUrl $api.Url -QueueTokenFile $api.TokenFile }
        $run = $script:roleRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-BarriersOpened -Markers $markers -Runs @("inspector-ins-a", "inspector-ins-b", "worker-wrk-d", "worker-wrk-e", "worker-wrk-f")
        $state = Get-FakeApiState -Api $api
        $seen = @(@($state.statuses) | ForEach-Object { (Get-RunNames -Status $_) -join "," })
        Assert-True -Condition ($seen -contains "ins-a:inspector,ins-b:inspector,wrk-d:worker,wrk-e:worker,wrk-f:worker") `
            -Because "five runs: the three worker seats are full BESIDE the two inspections; the third inspection waits for an inspector's seat, not a worker's: $($seen -join ' | ')"
        foreach ($names in $seen) {
            Assert-True -Condition (@($names -split "," | Where-Object { $_ -match ":inspector$" }).Count -le 2) -Because "never a third inspection beside two: $names"
        }
        Assert-Equal -Expected "ins-b:inspector,ins-c:inspector,wrk-d:worker,wrk-e:worker,wrk-f:worker" -Actual ([string](Read-Marker -Path (Join-Path $markers "inspector-ins-c.started")).in_flight) `
            -Because "the third inspection took the seat the first one left, while everything else was still running (ins-b and the workers were held until it started)"
        Assert-True -Condition ($seen -contains "ins-b:inspector,ins-c:inspector,wrk-d:worker,wrk-e:worker,wrk-f:worker") -Because "and the cycle's own status says the same: $($seen -join ' | ')"
        Assert-Equal -Expected "merged,merged,merged,merged,merged,merged" -Actual (@($state.tasks | ForEach-Object { $_.state }) -join ",") -Because ($run.StdOut + $run.StdErr + $run.Report)
    }

    Test-Case "the pool: -MaxRunsPerTask holds under the pool - two tasks side by side, one run each, and both are stopped with the reason" {
        # The inspector of cycle-seat-pool, PROBE-P1: the ADR said the cap holds 'by the tests that
        # held it'; none asserted the stop.
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/a1")), (New-Task -Id "task-two" -Area @("src/a2")))
        $run = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 2 -ExtraArguments "-MaxRunsPerTask 1"
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "worker:task-one,worker:task-two" -Actual (@($run.Calls | ForEach-Object { "$($_.role):$($_.task)" } | Sort-Object) -join ",") -Because "one run a task: no inspection is started after it"
        foreach ($id in @("task-one", "task-two")) {
            $task = Get-TaskById -Queue $run.Queue -Id $id
            Assert-Equal -Expected "stopped" -Actual $task.state -Because "${id}: $($run.Report)"
            Assert-Equal -Expected "bu döngüde 1 koşu yapıldı ve iş bitmedi" -Actual ([string]$task.reason) -Because "${id} says why"
            Assert-Equal -Expected 1 -Actual @($task.reports).Count -Because "${id}: its worker's report is kept"
        }
    }

    Test-Case "the pool: two RETURNs stop a task while another task's worker is in flight - the stop is applied at once, and the run in flight is completed as ever" {
        # PROBE-P2 of the same inspection. Task-two's worker is held until the cycle has collected
        # task-one's second inspection (its report file is written when the cycle collects the run,
        # and the stop is applied in that same step), so task-one is stopped while it is in flight.
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/a1")), (New-Task -Id "task-two" -Area @("src/a2")))
        $markers = Join-Path $root "markers"
        $collected = Join-Path $root "team\reports\c1\task-one-inspector-2.json"
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_MARKERS = $markers; PAGENTOS_FAKE_CLAUDE_BARRIER = "worker:task-two=$collected" } -Body {
            $script:returnRun = Invoke-Cycle -Root $root -Scenario "return" -MaxParallel 2
        }
        $run = $script:returnRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-BarriersOpened -Markers $markers -Runs @("worker-task-two")
        $one = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "stopped/2" -Actual "$($one.state)/$([int]$one.returns)" -Because "two RETURNs stop it: $($run.Report)"
        $first = @($run.Calls | ForEach-Object { "$($_.role):$($_.task)" })
        Assert-Equal -Expected "worker:task-one,inspector:task-one,worker:task-one,inspector:task-one" -Actual (@($first | Where-Object { $_ -match "task-one$" }) -join ",") -Because "worked, returned, worked again, returned again - and nothing after the stop"
        $two = Get-TaskById -Queue $run.Queue -Id "task-two"
        Assert-Equal -Expected "stopped/2" -Actual "$($two.state)/$([int]$two.returns)" -Because "the run in flight was completed and its task went on to its own two RETURNs: $($run.Report)"
        Assert-True -Condition ([string]$two.sha -match "^[0-9a-f]{40}$") -Because "its held worker's result was applied: $($two.sha)"
        Assert-Equal -Expected "worker:task-two,inspector:task-two,worker:task-two,inspector:task-two" -Actual (@($first | Where-Object { $_ -match "task-two$" }) -join ",") -Because "every run once"
    }

    Test-Case "the pool: a changed team/cycle-settings.json is honoured at the next refill - no restart, and the arguments the cycle started with do not bind it" {
        # 2026-10-01: a setting changed at 16:45 took effect at 19:35, when the cycle that was
        # running at last had nothing left to do.
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/a1")), (New-Task -Id "task-two" -Area @("src/a2")), (New-Task -Id "task-three" -Area @("src/a3")))
        $hooks = Get-PoolHooks -Root $root -Seconds "worker:task-two=5,worker:task-three=5"
        $hooks["PAGENTOS_FAKE_CLAUDE_SETTINGS"] = (Join-Path $root "team\cycle-settings.json")
        $hooks["PAGENTOS_FAKE_CLAUDE_SETTINGS_JSON"] = '{"max_parallel":3}'
        $hooks["PAGENTOS_FAKE_CLAUDE_SETTINGS_RUN"] = "worker:task-one"
        Use-FakeHooks -Environment $hooks -Body { $script:settingRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 1 }
        $run = $script:settingRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $snapshots = Join-Path $root "snapshots"
        Assert-Equal -Expected "task-one:worker" -Actual (Get-SnapshotRuns -Folder $snapshots -Name "worker-task-one") -Because "started with one worker seat"
        $third = Get-SnapshotRuns -Folder $snapshots -Name "worker-task-three"
        Assert-True -Condition ($third -match "task-two:worker" -and $third -match "task-three:worker") -Because "three seats from the refill after the file changed: two workers side by side in a cycle started with -MaxParallel 1: $third"
        Assert-Equal -Expected "merged,merged,merged" -Actual (@(Get-TeamTasks -Queue $run.Queue | ForEach-Object { $_.state }) -join ",") -Because ($run.StdOut + $run.StdErr + $run.Report)
        # A file that is no setting changes nothing and is a line in the report, once.
        $bad = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/a1")), (New-Task -Id "task-two" -Area @("src/a2")))
        Set-SandboxFile -Root $bad -Name "cycle-settings.json" -Json '{"max_parallel":"many"}'
        Use-FakeHooks -Environment (Get-PoolHooks -Root $bad) -Body { $script:badSetting = Invoke-Cycle -Root $bad -Scenario "approve" -MaxParallel 1 }
        Assert-Equal -Expected "merged,merged" -Actual (@(Get-TeamTasks -Queue $script:badSetting.Queue | ForEach-Object { $_.state }) -join ",") -Because ($script:badSetting.StdOut + $script:badSetting.StdErr)
        Assert-True -Condition ((Get-SnapshotRuns -Folder (Join-Path $bad "snapshots") -Name "worker-task-two") -notmatch "task-one:worker") -Because "the parameters stand: one worker seat"
        Assert-Equal -Expected 1 -Actual ([regex]::Matches($script:badSetting.Report, "cycle-settings\.json")).Count -Because "said once, not at every refill: $($script:badSetting.Report)"
    }

    Test-Case "the pool: after -MaxHours the cycle starts nothing new and ends when its runs do - the scheduler's next start runs the current script" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/a1")), (New-Task -Id "task-two" -Area @("src/a2")))
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_SECONDS = "worker:task-one=5" } -Body {
            $script:hoursRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 1 -ExtraArguments "-MaxHours 0.0005"
        }
        $run = $script:hoursRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "worker:task-one" -Actual (@($run.Calls | ForEach-Object { "$($_.role):$($_.task)" }) -join ",") -Because "the run in flight finished; 1.8 seconds had passed, so nothing else was started"
        $one = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-Equal -Expected "inspecting" -Actual $one.state -Because "the finished worker's result is recorded; its inspection is the next cycle's"
        Assert-Equal -Expected 1 -Actual @($one.reports).Count -Because "with its report"
        Assert-Equal -Expected "assigned" -Actual (Get-TaskById -Queue $run.Queue -Id "task-two").state -Because "the second task was never started"
        Assert-True -Condition ($run.Report -match "döngü: çalışma süresi doldu \(-MaxHours") -Because "the report says why it ended with work left: $($run.Report)"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "and the lock is released for the next start"
        $again = Invoke-Cycle -Root $root -Scenario "approve" -CycleId "c2" -MaxParallel 1
        Assert-Equal -Expected "merged,merged" -Actual (@(Get-TeamTasks -Queue $again.Queue | ForEach-Object { $_.state }) -join ",") -Because "the next cycle carries on from there: $($again.Report)"
    }

    Test-Case "the pool: with one long run in flight, a task that reaches the store is STARTED before that run ends - the store is read again whenever a run ends, not once per batch" {
        # ADR-0214 addendum 11 read the store before every PASS; a pass was a batch, so a card
        # that arrived during a batch was seen when its slowest run ended.
        $late = New-Task -Id "task-two" -Area @("src/other")
        $late.created_at = "2026-09-30T00:00:01Z"; $late.updated_at = "2026-09-30T00:00:01Z"
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-zero" -Area @("src/zero")), (New-Task -Id "task-one" -Area @("src/area"))) -Late @($late) -LateOnRun "worker:task-one"
        $root = New-Sandbox -Tasks @()
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_SECONDS = "worker:task-one=8,worker:task-two=2" } -Body {
            $script:lateRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 3 -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        }
        Assert-Equal -Expected 0 -Actual $script:lateRun.ExitCode -Because ($script:lateRun.StdOut + $script:lateRun.StdErr)
        $state = Get-FakeApiState -Api $api
        $both = @(@($state.statuses) | Where-Object { $names = (Get-RunNames -Status $_) -join ","; $names -match "task-one:worker" -and $names -match "task-two:worker" })
        Assert-True -Condition (@($both).Count -ge 1) -Because "a status names the late task's worker beside the long run: $(@($state.statuses | ForEach-Object { (Get-RunNames -Status $_) -join '+' }) -join ' | ')"
        Assert-Equal -Expected "merged,merged,merged" -Actual (@($state.tasks | ForEach-Object { $_.state }) -join ",") -Because "and all three end merged: $($script:lateRun.Report)"
        Assert-Equal -Expected 0 -Actual @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match " 409$" }).Count -Because "nothing was written over a version the cycle had not read"
        # The store was read again while task-one's worker ran: the run's own task stayed the
        # cycle's copy, and its result was applied to THAT copy and written when it ended.
        Assert-Equal -Expected "inspector:task-one,inspector:task-two,inspector:task-zero,worker:task-one,worker:task-two,worker:task-zero" -Actual (@($script:lateRun.Calls | ForEach-Object { "$($_.role):$($_.task)" } | Sort-Object) -join ",") `
            -Because "every task was worked on once: a re-read does not put the store's 'in_progress' in place of a run in flight"
    }

    Test-Case "the pool: the usage limit is waited out without blocking - nothing new starts until it lifts, and a run in flight is completed meanwhile" {
        # The batch loop slept in the wait: a run that ended during it was collected when the
        # wait was over. The inspector's model is limited here (no fallback); its reset was 80
        # seconds ago, so with the tool's 90-second margin the cycle waits about ten seconds.
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one" -Area @("src/a1")), (New-Task -Id "task-two" -Area @("src/a2"))) -Models (New-ModelSetting -Worker $sonnet -Inspector $opus -Fallback $false)
        $root = New-Sandbox -Tasks @()
        $hooks = @{ PAGENTOS_FAKE_CLAUDE_LIMITED_MODELS = $opus; PAGENTOS_FAKE_CLAUDE_LIMITED_ROLES = "inspector"; PAGENTOS_FAKE_CLAUDE_LIMIT_RESET_SECONDS = "-80"; PAGENTOS_FAKE_CLAUDE_SECONDS = "worker:task-two=6" }
        Use-FakeHooks -Environment $hooks -Body {
            $script:waitRun = Invoke-Cycle -Root $root -Scenario "approve" -NoCaps -MaxParallel 2 -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        }
        $run = $script:waitRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $waiting = @(@((Get-FakeApiState -Api $api).statuses) | Where-Object { $_.usage_limit.state -eq "waiting" })
        Assert-True -Condition (@($waiting).Count -ge 2) -Because "the wait is in the live status"
        $during = @($waiting | ForEach-Object { (Get-RunNames -Status $_) -join "+" })
        Assert-True -Condition ($during -contains "task-two:worker") -Because "task-two's worker was in flight when the wait began: $($during -join ' | ')"
        Assert-Equal -Expected 0 -Actual @($during | Where-Object { $_ -and $_ -ne "task-two:worker" }).Count -Because "nothing was STARTED while the limit was waited out: $($during -join ' | ')"
        Assert-True -Condition ($during -contains "") -Because "and the run in flight was completed during the wait, not after it: $($during -join ' | ')"
        Assert-True -Condition ($run.Report -match "sıfırlanmasına kadar beklendi") -Because $run.Report
        Assert-Equal -Expected "inspector:task-one,inspector:task-one,inspector:task-two,worker:task-one,worker:task-two" -Actual (@($run.Calls | ForEach-Object { "$($_.role):$($_.task)" } | Sort-Object) -join ",") `
            -Because "after the wait both inspections were started; the limit that came back a second time is not waited for again"
        $states = @((Get-FakeApiState -Api $api).tasks | ForEach-Object { "$($_.state)/$([int](Get-TeamProperty -InputObject $_ -Name 'failed_runs' -Default 0))" }) -join ","
        Assert-Equal -Expected "inspecting/0,inspecting/0" -Actual $states -Because "both tasks wait for their inspection, and the limit is nobody's failure: $($run.Report)"
    }

    Test-Case "the pool: with nothing ending, the store is still read again every -RefillSeconds - a card stored beside ONE long run is started while it runs" {
        $late = New-Task -Id "task-two" -Area @("src/other")
        $late.created_at = "2026-09-30T00:00:01Z"; $late.updated_at = "2026-09-30T00:00:01Z"
        $api = Start-FakeApi -Tasks @((New-Task -Id "task-one" -Area @("src/area"))) -Late @($late) -LateOnRun "worker:task-one"
        $root = New-Sandbox -Tasks @()
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_SECONDS = "worker:task-one=8,worker:task-two=2" } -Body {
            $script:tickRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 2 -ExtraArguments "-RefillSeconds 1" -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        }
        Assert-Equal -Expected 0 -Actual $script:tickRun.ExitCode -Because ($script:tickRun.StdOut + $script:tickRun.StdErr)
        $state = Get-FakeApiState -Api $api
        $both = @(@($state.statuses) | Where-Object { $names = (Get-RunNames -Status $_) -join ","; $names -match "task-one:worker" -and $names -match "task-two:worker" })
        Assert-True -Condition (@($both).Count -ge 1) -Because "no run ended, and the card was still seen and started: $(@($state.statuses | ForEach-Object { (Get-RunNames -Status $_) -join '+' }) -join ' | ')"
        Assert-Equal -Expected "merged,merged" -Actual (@($state.tasks | ForEach-Object { $_.state }) -join ",") -Because $script:tickRun.Report
    }

    Test-Case "the pool: the stop flag starts nothing new and lets the runs in flight finish - a seat that frees after the flag stays empty" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/a1")), (New-Task -Id "task-two" -Area @("src/a2")), (New-Task -Id "task-three" -Area @("src/a3")))
        $flag = Join-Path $root "team\stop.flag"
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_STOPFLAG = $flag; PAGENTOS_FAKE_CLAUDE_SECONDS = "worker:task-two=6" } -Body {
            $script:poolStop = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 2
        }
        $run = $script:poolStop
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "worker:task-one,worker:task-two" -Actual (@($run.Calls | ForEach-Object { "$($_.role):$($_.task)" } | Sort-Object) -join ",") -Because "task-one's seat was free for five seconds after the flag: neither its inspector nor task-three's worker was started"
        $two = Get-TaskById -Queue $run.Queue -Id "task-two"
        Assert-Equal -Expected "inspecting/inspecting/assigned" -Actual "$((Get-TaskById -Queue $run.Queue -Id 'task-one').state)/$($two.state)/$((Get-TaskById -Queue $run.Queue -Id 'task-three').state)" -Because "both runs in flight finished and were recorded; the third task never started"
        Assert-Equal -Expected 1 -Actual @($two.reports).Count -Because "the long run's report is kept"
        Assert-True -Condition ($run.Report -match 'döngü: sahip/lead durdurdu \(team/stop\.flag\)') -Because $run.Report
        Assert-Equal -Expected 1 -Actual ([regex]::Matches($run.Report, "sahip/lead durdurdu")).Count -Because "said once"
        Assert-True -Condition (-not (Test-Path -LiteralPath $flag)) -Because "the flag is removed"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock is released"
    }

    Test-Case "the pool: two runs that meet the usage limit stop the cycle once - one stop line, both tasks where they were" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/a1")), (New-Task -Id "task-two" -Area @("src/a2")))
        $run = Invoke-Cycle -Root $root -Scenario "limited" -NoCaps -MaxParallel 2 -ExtraArguments '-WaitForUsageLimit:$false'
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "worker:task-one,worker:task-two" -Actual (@($run.Calls | ForEach-Object { "$($_.role):$($_.task)" } | Sort-Object) -join ",") -Because "the two limited runs, and nothing after the limit"
        Assert-Equal -Expected "assigned,assigned" -Actual (@(Get-TeamTasks -Queue $run.Queue | ForEach-Object { $_.state }) -join ",") -Because "both tasks went back: $($run.Report)"
        Assert-Equal -Expected 1 -Actual ([regex]::Matches($run.Report, "döngü: Max kullanım limiti")).Count -Because "one stop line for one limit, not one per run that met it: $($run.Report)"
        Assert-Equal -Expected 2 -Actual ([regex]::Matches($run.Report, "(?m)^- task-(one|two) / worker: [^\r\n]*Max kullanım limiti")).Count -Because "and each limited run is in the run list"
    }

    Test-Case "the pool: a cycle that dies while a run is in flight kills that run - nothing writes to a worktree after the lock is released" {
        # The integration branch's worktree cannot be made (a file stands where its folder goes):
        # task-one's merge throws while task-two's worker is still running, and the cycle ends
        # there, as it always did on an error. The run it leaves behind must not go on working.
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/a1")), (New-Task -Id "task-two" -Area @("src/a2")))
        [void](New-Item -ItemType Directory -Force -Path (Join-Path $root ".claude\worktrees\integrate"))
        Set-Content -LiteralPath (Join-Path $root ".claude\worktrees\integrate\c1") -Value "not a folder" -Encoding ASCII
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_SECONDS = "worker:task-two=12" } -Body { $script:deadRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 2 }
        $run = $script:deadRun
        Assert-True -Condition ($run.ExitCode -ne 0) -Because "the cycle ended on the error: $($run.StdOut + $run.StdErr)"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "the lock is released"
        Assert-Equal -Expected 0 -Actual @((Read-TeamJson -Path (Join-Path $root "team\status.json")).runs).Count -Because "and the status names no run"
        # Had it been left alone, task-two's worker would commit its work some seconds from now.
        $deadline = [datetime]::UtcNow.AddSeconds(16)
        while ([datetime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 500 }
        Assert-Equal -Expected "" -Actual (Invoke-SandboxGit -Root $root -Arguments @("log", "--format=%s", "main..team/c1/worker-task-two")) -Because "the run that was in flight was stopped: it committed nothing after the cycle let go of the lock"
    }

    Test-Case "the pool: the researcher runs beside the workers, not before them, and its idea still waits for the owner" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        $hooks = Get-PoolHooks -Root $root -Seconds "researcher:*=5"
        Use-FakeHooks -Environment $hooks -Body { $script:besideRun = Invoke-Cycle -Root $root -Scenario "approve" -Research }
        $run = $script:besideRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $during = Get-SnapshotRuns -Folder (Join-Path $root "snapshots") -Name "worker-task-one"
        Assert-Equal -Expected "cycle:researcher,task-one:worker" -Actual $during -Because "the worker did not wait for the web scan to end"
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because ($run.StdOut + $run.StdErr + $run.Report)
        $ideas = @(Get-TeamTasks -Queue $run.Queue | Where-Object { $_.state -eq "awaiting_owner" })
        Assert-Equal -Expected 1 -Actual @($ideas).Count -Because "the researcher's proposal is queued for the owner: $($run.Report)"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $root "team\research-last.txt")) -Because "and its finished run is recorded"
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $run.Queue).Count -Because "the queue keeps the protocol"
    }

    # ------------------------------------------------------------------ stable seats (office-stable-seats)
    function Get-RunSeat {
        <# A run entry's `seat`, or $null when it carries none. #>
        param($Run)
        $property = $Run.PSObject.Properties["seat"]
        if ($null -eq $property) { return $null }
        return $property.Value
    }
    function Get-SeatLine {
        <# "task:role@seat" for every run of a status, sorted - what an assertion prints. #>
        param($Status)
        return ((@(@($Status.runs) | Where-Object { $null -ne $_ } | ForEach-Object { "$($_.task):$($_.role)@$(Get-RunSeat -Run $_)" } | Sort-Object)) -join ",")
    }

    Test-Case "stable seats: four worker runs sit on seats 1-4; when the run on seat 1 ends the next status keeps the others on 2, 3 and 4; the next worker takes seat 1 (the lowest free); no seat is ever held twice" {
        # 2026-10-02 15:50, the owner at the Ofis page: the run on Çalışan 1 ended and the
        # page drew Çalışan 2's task on Çalışan 1 - the seats were the list's positions.
        $tasks = @("task-one", "task-two", "task-three", "task-four", "task-five" | ForEach-Object { New-Task -Id $_ -Area @("src/$_") })
        $api = Start-FakeApi -Tasks $tasks
        $root = New-Sandbox -Tasks @()
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_SECONDS = "worker:task-one=10,worker:task-two=25,worker:task-three=25,worker:task-four=25" } -Body {
            $script:seatsRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 4 -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        }
        Assert-Equal -Expected 0 -Actual $script:seatsRun.ExitCode -Because ($script:seatsRun.StdOut + $script:seatsRun.StdErr)
        $history = @((Get-FakeApiState -Api $api).statuses)
        $all = (@($history | ForEach-Object { Get-SeatLine -Status $_ }) -join " | ")
        $seatOf = @{}
        foreach ($document in $history) {
            $held = @()
            foreach ($live in @(@($document.runs) | Where-Object { $null -ne $_ -and $_.role -eq "worker" })) {
                $seat = Get-RunSeat -Run $live
                Assert-True -Condition ($seat -is [int] -and $seat -ge 1) -Because "every worker run carries a seat, an integer from 1: $(Get-SeatLine -Status $document)"
                $held += $seat
                $task = [string]$live.task
                if ($seatOf.ContainsKey($task)) { Assert-Equal -Expected $seatOf[$task] -Actual $seat -Because "a run keeps its seat for its whole life ($task): $all" }
                else { $seatOf[$task] = $seat }
            }
            Assert-Equal -Expected @($held).Count -Actual @($held | Sort-Object -Unique).Count -Because "no two live worker runs on one seat: $(Get-SeatLine -Status $document)"
        }
        Assert-Equal -Expected "1/2/3/4/1" -Actual (@("task-one", "task-two", "task-three", "task-four", "task-five" | ForEach-Object { $seatOf[$_] }) -join "/") -Because "four starts take 1-4 (the fourth gets 4), the fifth the seat task-one left: $all"
        $wasLive = $false; $next = $null
        foreach ($document in $history) {
            $line = Get-SeatLine -Status $document
            if ($line -match "task-one:worker@") { $wasLive = $true; continue }
            if ($wasLive) { $next = $line; break }
        }
        Assert-True -Condition ($null -ne $next -and $next -match "task-two:worker@2" -and $next -match "task-three:worker@3" -and $next -match "task-four:worker@4") -Because "the NEXT status after seat 1's run ended keeps the others where they sat: <$next> in $all"
        $fifth = @($history | ForEach-Object { Get-SeatLine -Status $_ } | Where-Object { $_ -match "task-five:worker@" })[0]
        Assert-True -Condition ($fifth -match "task-five:worker@1" -and $fifth -match "task-two:worker@2" -and $fifth -match "task-three:worker@3") -Because "a run started while 2 and 3 are held and 1 is free gets 1: $fifth"
        Assert-Equal -Expected "merged,merged,merged,merged,merged" -Actual (@((Get-FakeApiState -Api $api).tasks | ForEach-Object { $_.state }) -join ",") -Because "and the work was done: $($script:seatsRun.Report)"
    }

    Test-Case "stable seats: a run of the lead, the researcher, the integrator or the inspector carries no seat; every worker run does" {
        $study = New-Task -Id "task-study" -Area @("src/study")
        $study | Add-Member -NotePropertyName "needs_integration" -NotePropertyValue $true
        $root = New-Sandbox -Tasks @((New-Proposal -Id "idea-one"), $study)
        $hooks = Get-PoolHooks -Root $root -Seconds "researcher:*=3"
        Use-FakeHooks -Environment $hooks -Body { $script:rolesRun = Invoke-Cycle -Root $root -Scenario "split" -NoCaps -Research -MaxParallel 3 }
        Assert-Equal -Expected 0 -Actual $script:rolesRun.ExitCode -Because ($script:rolesRun.StdOut + $script:rolesRun.StdErr)
        $seen = @{}
        foreach ($file in @(Get-ChildItem -LiteralPath (Join-Path $root "snapshots") -Filter *.json)) {
            $document = Read-TeamJson -Path $file.FullName
            foreach ($live in @(@($document.runs) | Where-Object { $null -ne $_ })) {
                $seen[[string]$live.role] = $true
                $seat = Get-RunSeat -Run $live
                if ($live.role -eq "worker") { Assert-True -Condition ($seat -is [int] -and $seat -ge 1) -Because "a worker run has its seat ($($file.Name)): $(Get-SeatLine -Status $document)" }
                else { Assert-True -Condition ($null -eq $live.PSObject.Properties["seat"]) -Because "a $($live.role) run carries no seat ($($file.Name)): $(Get-SeatLine -Status $document)" }
            }
        }
        Assert-Equal -Expected "inspector,integrator,lead,researcher,worker" -Actual (@($seen.Keys | Sort-Object) -join ",") -Because "every role was seen in flight: $($script:rolesRun.Report)"
    }

    Test-Case "research is on by default: a cycle started with no research flag runs the researcher, even with an empty queue, and its idea waits for the owner" {
        # The owner, 2026-10-01 (ADR-0214 addendum 5): the researcher runs in EVERY cycle. It used
        # to run only with -Research.
        $root = New-Sandbox -Tasks @()
        $run = Invoke-Cycle -Root $root -Scenario "approve" -DefaultResearch
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "researcher" -Actual (@($run.Calls | ForEach-Object { $_.role }) -join ",") -Because "no flag, no task: the researcher still ran"
        $ideas = @(Get-TeamTasks -Queue $run.Queue | Where-Object { $_.state -eq "awaiting_owner" })
        Assert-Equal -Expected "team/proposals/2026-09-30-anlati.md" -Actual (@($ideas | ForEach-Object { $_.proposal }) -join ",") -Because "its proposal is queued for the owner: $($run.Report)"
        Assert-True -Condition ($run.Report -match "(?m)^- cycle / researcher: ") -Because "its run is in the report's run list: $($run.Report)"
        $status = Read-TeamJson -Path (Join-Path $root "team\status.json")
        Assert-Equal -Expected 0 -Actual @($status.runs).Count -Because "and its live status entry is gone after it"
    }

    Test-Case "research is on by default: -NoResearch runs none, and -Research is still accepted" {
        $root = New-Sandbox -Tasks @()
        $off = Invoke-Cycle -Root $root -Scenario "approve" -DefaultResearch -ExtraArguments "-NoResearch"
        Assert-Equal -Expected 0 -Actual $off.ExitCode -Because ($off.StdOut + $off.StdErr)
        Assert-True -Condition ($off.Report -and -not $off.StdErr.Trim()) -Because "the cycle ran (an unknown parameter would end it before its report): $($off.StdErr)"
        Assert-Equal -Expected 0 -Actual @($off.Calls).Count -Because "-NoResearch: nobody was started"
        Assert-Equal -Expected 0 -Actual @(Get-TeamTasks -Queue $off.Queue).Count -Because "and nothing was queued"
        $on = Invoke-Cycle -Root $root -Scenario "approve" -CycleId "c2" -Research
        Assert-Equal -Expected "researcher" -Actual (@($on.Calls | ForEach-Object { $_.role }) -join ",") -Because "the registered nightly task's -Research still works: $($on.StdOut + $on.StdErr)"
    }

    Test-Case "research is on by default: with two approved tasks and two worker seats the researcher and BOTH workers are in flight together" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -Area @("src/a1")), (New-Task -Id "task-two" -Area @("src/a2")))
        # The workers' snapshots carry the claim: each is taken inside a worker run and names the
        # researcher still in flight (nobody waited for it) beside the OTHER worker (it took no
        # worker seat). The researcher's own snapshot, a second after its start, is not asserted:
        # on a loaded PC the workers' worktrees take longer than that (a stopwatch, not a claim).
        # The durations are generous so that every run is still in flight when the others take
        # their snapshots.
        $hooks = Get-PoolHooks -Root $root -Seconds "researcher:*=20,worker:*=10"
        $hooks["PAGENTOS_FAKE_CLAUDE_SNAPSHOT_SECONDS"] = 5
        Use-FakeHooks -Environment $hooks -Body { $script:threeRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 2 -DefaultResearch }
        $run = $script:threeRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        foreach ($name in @("worker-task-one", "worker-task-two")) {
            Assert-Equal -Expected "cycle:researcher,task-one:worker,task-two:worker" -Actual (Get-SnapshotRuns -Folder (Join-Path $root "snapshots") -Name $name) `
                -Because "a snapshot taken inside the $name run names all three: the researcher took no worker seat and nobody waited for it"
        }
        $entry = @((Read-TeamJson -Path (Join-Path $root "snapshots\worker-task-one.json")).runs | Where-Object { $_.role -eq "researcher" })[0]
        Assert-Equal -Expected "cycle" -Actual ([string]$entry.task) -Because "the Ofis page's researcher seat reads {task: cycle, role: researcher}"
        Assert-True -Condition ([string]$entry.started_at -match '^\d{4}-') -Because "with when it started"
        Assert-Equal -Expected "merged,merged" -Actual (@("task-one", "task-two" | ForEach-Object { (Get-TaskById -Queue $run.Queue -Id $_).state }) -join ",") -Because $run.Report
        Assert-Equal -Expected 1 -Actual @(Get-TeamTasks -Queue $run.Queue | Where-Object { $_.state -eq "awaiting_owner" }).Count -Because "and its idea waits for the owner"
    }

    Test-Case "research is on by default: in API mode the store receives the new proposal's name and text, once" {
        $api = Start-FakeApi -Tasks @()
        $root = New-Sandbox -Tasks @()
        $run = Invoke-Cycle -Root $root -Scenario "approve" -DefaultResearch -QueueUrl $api.Url -QueueTokenFile $api.TokenFile
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $posts = @(Get-FakeApiRequests -Api $api | Where-Object { $_ -match 'POST /v1/team/queue/proposals' })
        Assert-Equal -Expected 1 -Actual @($posts).Count -Because "one new proposal, one post: $($posts -join ' | ')"
        $state = Get-FakeApiState -Api $api
        $name = "2026-09-30-anlati.md"
        Assert-Equal -Expected $name -Actual (@($state.proposals.PSObject.Properties | ForEach-Object { $_.Name }) -join ",") -Because "by its file name"
        $onDisk = [System.IO.File]::ReadAllText((Join-Path $root "team\proposals\$name"), [System.Text.Encoding]::UTF8)
        Assert-Equal -Expected $onDisk -Actual ([string]$state.proposals.$name) -Because "with the file's text"
        Assert-Equal -Expected "awaiting_owner" -Actual (@($state.tasks | ForEach-Object { $_.state }) -join ",") -Because "the idea waits for the owner in the store"
    }

    Test-Case "research is on by default: a researcher at the usage limit is not a failed cycle - a limited run in the list, no finished-run marker" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one"))
        Use-FakeHooks -Environment @{ PAGENTOS_FAKE_CLAUDE_LIMITED_MODELS = "$fable,$opus,$sonnet"; PAGENTOS_FAKE_CLAUDE_LIMITED_ROLES = "researcher" } -Body {
            $script:limitedResearch = Invoke-Cycle -Root $root -Scenario "approve" -NoCaps -DefaultResearch -ExtraArguments '-WaitForUsageLimit:$false'
        }
        $run = $script:limitedResearch
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-True -Condition (@($run.Calls | Where-Object { $_.role -eq "researcher" }).Count -ge 1) -Because "the researcher was started"
        Assert-True -Condition ($run.Report -match "(?m)^- cycle / researcher: [^\r\n]*Max kullanım limiti") -Because "its run is in the run list as limited: $($run.Report)"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $root "team\research-last.txt"))) -Because "a limited run is not a finished one: the next cycle runs it again"
        # The rule of every run (no model left, -WaitForUsageLimit off): the cycle starts nothing
        # new and says how to continue - the limit's line, not a failure of the researcher.
        Assert-True -Condition ($run.Report -match "döngü: Max kullanım limiti; .*aynı -CycleId ile yeniden başlat") -Because "the limit's own stop line: $($run.Report)"
        Assert-True -Condition ($run.Report -notmatch "araştırmacı: başarısız") -Because "the limit is not the researcher's failure: $($run.Report)"
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $run.Queue).Count -Because "the queue keeps the protocol"
        $task = Get-TaskById -Queue $run.Queue -Id "task-one"
        Assert-True -Condition (@("approved", "assigned", "inspecting", "merged") -contains [string]$task.state) -Because "the task is where its own runs left it, never stopped for the researcher's limit: $($run.Report)"
        Assert-Equal -Expected 0 -Actual ([int](Get-TeamProperty -InputObject $task -Name "failed_runs" -Default 0)) -Because "and nobody's failure"
    }

    function Set-WorktreeBlocked {
        <# A file stands where the task's worktree folder goes: its run cannot be started. #>
        param([string]$Root, [string]$Id, [string]$CycleId = "c1")
        $folder = Join-Path $Root ".claude\worktrees\team\$CycleId"
        [void](New-Item -ItemType Directory -Force -Path $folder)
        Set-Content -LiteralPath (Join-Path $folder "worker-$Id") -Value "not a folder" -Encoding ASCII
    }

    Test-Case "the pool: a start that fails with nothing else in flight does not end the cycle - the task is stopped with the reason and the next ones take the seat" {
        # The inspector's finding on the first pool: Start-PoolRun answered $false, nothing was
        # started and nothing was in flight, and the loop read that as "nothing can be started":
        # exit 0 with two assigned tasks never run. The batch loop went on to them.
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -State "assigned" -Area @("src/a1")), (New-Task -Id "task-two" -State "assigned" -Area @("src/a2")), (New-Task -Id "task-three" -State "assigned" -Area @("src/a3")))
        Set-WorktreeBlocked -Root $root -Id "task-one"
        $run = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 1
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "stopped,merged,merged" -Actual (@(Get-TeamTasks -Queue $run.Queue | ForEach-Object { $_.state }) -join ",") -Because ($run.StdOut + $run.StdErr + $run.Report)
        Assert-True -Condition ([string](Get-TaskById -Queue $run.Queue -Id "task-one").reason -match "koşu başlatılamadı") -Because "the stop says why: $((Get-TaskById -Queue $run.Queue -Id 'task-one').reason)"
        Assert-Equal -Expected "worker:task-two,inspector:task-two,worker:task-three,inspector:task-three" -Actual (@($run.Calls | ForEach-Object { "$($_.role):$($_.task)" }) -join ",") -Because "one worker seat: the two that could start, one after the other, and no run for the one that could not"
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $run.Queue).Count -Because "the queue keeps the protocol"
    }

    Test-Case "the pool: four starts that fail in a row are four stopped tasks, not the end of the cycle - the fifth task is run" {
        # A failed start is not an idle pass: three of those in a row end the cycle's work.
        $tasks = @(1..5 | ForEach-Object { New-Task -Id "task-$_" -State "assigned" -Area @("src/f$_") })
        $root = New-Sandbox -Tasks $tasks
        foreach ($number in 1..4) { Set-WorktreeBlocked -Root $root -Id "task-$number" }
        $run = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 1
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "stopped,stopped,stopped,stopped,merged" -Actual (@(Get-TeamTasks -Queue $run.Queue | ForEach-Object { $_.state }) -join ",") -Because ($run.StdOut + $run.StdErr + $run.Report)
        Assert-Equal -Expected "worker:task-5,inspector:task-5" -Actual (@($run.Calls | ForEach-Object { "$($_.role):$($_.task)" }) -join ",") -Because "only the task that could start was run"
    }

    Test-Case "the pool: a start that fails beside a run in flight leaves its seat to the next task at once - not when that run ends" {
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -State "assigned" -Area @("src/a1")), (New-Task -Id "task-two" -State "assigned" -Area @("src/a2")), (New-Task -Id "task-three" -State "assigned" -Area @("src/a3")))
        Set-WorktreeBlocked -Root $root -Id "task-two"
        $hooks = Get-PoolHooks -Root $root -Seconds "worker:task-one=8"
        Use-FakeHooks -Environment $hooks -Body { $script:failBesideRun = Invoke-Cycle -Root $root -Scenario "approve" -MaxParallel 2 }
        $run = $script:failBesideRun
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        $third = Get-SnapshotRuns -Folder (Join-Path $root "snapshots") -Name "worker-task-three"
        Assert-Equal -Expected "task-one:worker,task-three:worker" -Actual $third -Because "task-three's worker took the seat task-two could not use, while task-one's was still running"
        Assert-Equal -Expected "merged,stopped,merged" -Actual (@(Get-TeamTasks -Queue $run.Queue | ForEach-Object { $_.state }) -join ",") -Because ($run.StdOut + $run.StdErr + $run.Report)
    }

    Test-Case "the pool: a researcher that cannot be started is a line in the report, not the end of the cycle - the task beside it is worked and merged" {
        # It used to throw out of the refill: the cycle died, and its 'finally' killed every run in flight.
        $root = New-Sandbox -Tasks @((New-Task -Id "task-one" -State "assigned"))
        Remove-Item -LiteralPath (Join-Path $root ".claude\agents\researcher.md") -Force
        $run = Invoke-Cycle -Root $root -Scenario "approve" -Research
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because ($run.StdOut + $run.StdErr + $run.Report)
        Assert-Equal -Expected "worker:task-one,inspector:task-one" -Actual (@($run.Calls | ForEach-Object { "$($_.role):$($_.task)" }) -join ",") -Because "no researcher ran, and it was tried once"
        Assert-Equal -Expected 1 -Actual ([regex]::Matches($run.Report, "araştırmacı: koşu başlatılamadı")).Count -Because "said once, under the stops: $($run.Report)"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $root "team\research-last.txt"))) -Because "a run that never started is not the researcher's last run: the next cycle tries again"
    }

    Test-Case "the pool: a lead split that cannot be started is a line in the report, not the end of the cycle - the proposal stays, the task beside it is merged" {
        $root = New-Sandbox -Tasks @((New-Proposal -Id "idea-one"), (New-Task -Id "task-one" -State "assigned"))
        $before = (Get-TaskById -Queue (Read-TeamJson -Path (Join-Path $root "team\queue.json")) -Id "idea-one").state
        Remove-Item -LiteralPath (Join-Path $root ".claude\agents\lead.md") -Force
        $run = Invoke-Cycle -Root $root -Scenario "split" -NoCaps
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because ($run.StdOut + $run.StdErr + $run.Report)
        Assert-Equal -Expected $before -Actual (Get-TaskById -Queue $run.Queue -Id "idea-one").state -Because "the proposal is where it was: the next cycle asks again"
        Assert-Equal -Expected "worker:task-one,inspector:task-one" -Actual (@($run.Calls | ForEach-Object { "$($_.role):$($_.task)" }) -join ",") -Because "no lead ran"
        Assert-Equal -Expected 1 -Actual ([regex]::Matches($run.Report, "bölme koşusu: idea-one: koşu başlatılamadı")).Count -Because "said once, under the risks - one try a cycle: $($run.Report)"
    }
}
finally {
    foreach ($api in $fakeApis) { try { if (-not $api.HasExited) { $api.Kill() } } catch { } }
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
