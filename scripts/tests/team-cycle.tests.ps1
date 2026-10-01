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
    $escaped = $Text.Replace('\', '\\').Replace('"', '\"')
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
    Assert-Equal -Expected $fable -Actual (Get-TeamWorkerModel -Task $task) -Because "the last worker run that finished, not the inspector's and not a failed one"
    $task.reports = @($task.reports) + (& $entry "worker" (Get-TeamOkOutcome -Model $opus))
    Assert-Equal -Expected $opus -Actual (Get-TeamWorkerModel -Task $task) -Because "the newest finished worker run"
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
    foreach ($name in @("NativeProcess.ps1", "TeamQueue.ps1", "TeamRun.ps1", "HttpJson.ps1")) {
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
        [switch]$NoCaps, [string]$ExtraArguments = ""
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
        Assert-Equal -Expected 2 -Actual @($run.Calls).Count -Because "4 + 4 is over 6: the third run never started"
        Assert-True -Condition ($run.Report -match "bütçe tavanı: 8[.,]00 / 6[.,]00 USD") -Because $run.Report
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
        Assert-Equal -Expected "task-two:worker" -Actual (@($second.runs | ForEach-Object { "$($_.task):$($_.role)" }) -join ",") -Because "only the run in flight is listed: $($second | ConvertTo-Json -Compress)"
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
        Assert-Equal -Expected "worker,inspector" -Actual (@($run.Calls | ForEach-Object { $_.role }) -join ",") -Because "the inspection ran, nothing after it"
        Assert-Equal -Expected "merged" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "an approved inspection is still merged"
        Assert-Equal -Expected "assigned" -Actual (Get-TaskById -Queue $run.Queue -Id "task-two").state -Because "the second task was only moved to assigned (no plan needed), never started"
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
            Assert-Equal -Expected $false -Actual ([bool]$call.fallback_flag) -Because "--fallback-model is never passed"
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

    # ------------------------------------------------------------------ the queue on the Cloud Core
    Write-Host ""
    Write-Host "the queue and the lock on the Cloud Core (a fake listener with the real routes' rules)"

    function Start-FakeApi {
        param([object[]]$Tasks = @(), $Lock = $null, [switch]$FailStatus, $Models = $null, [switch]$NoModels, [switch]$LegacyStatus)
        $work = Join-Path $env:TEMP ("pagentos-teamapi-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
        [void](New-Item -ItemType Directory -Force -Path $work)
        [void]$sandboxes.Add($work)
        $lockDocument = if ($null -ne $Lock) { $Lock } else { New-TeamLockReleased }
        $seed = [pscustomobject]@{ queue = (New-Queue -Tasks $Tasks); lock = $lockDocument }
        if ($null -ne $Models) { $seed | Add-Member -NotePropertyName models -NotePropertyValue $Models }
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
        Assert-True -Condition (@($requests | Where-Object { $_ -match "^GET /v1/team/queue 200" }).Count -eq 1) -Because "the queue is read once"
        Assert-True -Condition ($state.reports.PSObject.Properties["c1.md"].Value -match "pilot|c1") -Because "the report text is in the store"
        Assert-Equal -Expected "approved" -Actual (Get-TaskById -Queue $run.Queue -Id "task-one").state -Because "the sandbox's queue.json was not written in API mode"
        Assert-Equal -Expected $false -Actual ([bool]$run.Lock.held) -Because "nor was its lock.json ever held"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $root "team\reports\c1.md")) -Because "the report is still a file"
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
