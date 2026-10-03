<#
.SYNOPSIS
    The agent team's state on disk: the queue, the lock, the role runs (TEAM_PROTOCOL.md).

.DESCRIPTION
    Everything `scripts/team/*.ps1` decides is decided here, as functions that take their
    inputs and return their answer, so that the tests drive the decisions without starting a
    model, a worktree or a clock:

      * the queue (`team/queue.json`) and the rules a task must satisfy. The rules are the
        SAME rules `team/queue.schema.json` states; a test holds the two lists of states
        together by reading the schema, because two lists of states is two protocols;
      * the lock (`team/lock.json`): one machine runs a cycle at a time, a lock is stale
        after six hours, and a lock held by the OTHER machine stops the cycle (section 8);
      * what a task's state means for the cycle: which role runs next, and which states are
        the owner's three gates, where nothing runs;
      * what a role's report means: the verdict line, the 40-line summary, the money spent.

    The queue can also live in the Cloud Core's database (pilot-02): the functions of the
    'store on the Cloud Core' section at the end are the ONLY ones that touch the network or
    read a secret (the token file); everything above them takes its inputs and returns.

    Windows PowerShell 5.1, StrictMode.
#>

Set-StrictMode -Version Latest

# The states of a task, in the order a task moves through them. team/queue.schema.json
# carries the same list; scripts/tests/team-cycle.tests.ps1 reads it and compares.
$script:TeamStates = @(
    "proposed", "awaiting_owner", "approved", "assigned", "in_progress", "inspecting",
    "returned", "merged", "awaiting_release", "released", "awaiting_real_evidence", "done",
    "stopped"
)

# The owner's three gates (TEAM_PROTOCOL section 3). Nothing runs for a task that is at one.
$script:TeamOwnerGates = @{
    "awaiting_owner"         = "fikir"
    "awaiting_release"       = "yayin"
    "awaiting_real_evidence" = "gercek_cihaz"
}

$script:TeamRequiredFields = @(
    "id", "title", "roadmap_row", "state", "area", "branch", "worktree", "assignee",
    "reports", "budget", "created_at", "updated_at"
)

# Files only the lead writes, at merge time (TEAM_PROTOCOL section 4) - plus the queue and the
# lock, which are the cycle's. A split never gives a worker one of them, nor a directory
# that holds one.
$script:TeamSharedFiles = @(
    "docs/HANDOFF.md", "docs/DECISIONS.md", "state/BUILD_STATE.json", "docs/THIRD_PARTY_COMPONENTS.md",
    "team/queue.json", "team/lock.json"
)
$script:TeamSplitRequired = @("id", "title", "roadmap_row", "goal", "acceptance", "evidence_expected")
$script:TeamMaxAreaEntries = 25
# A task in these states is, or is about to be, worked on: its area is taken.
$script:TeamStatesInWork = @("approved", "assigned", "in_progress", "inspecting", "returned")

$script:TeamLockStaleHours = 6
$script:TeamSummaryMaxLines = 40
$script:TeamMaxReturns = 2

function Get-TeamStates { return @($script:TeamStates) }

function Get-TeamOwnerGates { return $script:TeamOwnerGates.Clone() }

function Get-TeamProperty {
    <# A property of a parsed JSON object, or the default when it is not there. #>
    param($InputObject, [Parameter(Mandatory = $true)][string]$Name, $Default = $null)
    if ($null -eq $InputObject) { return $Default }
    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property) { return $Default }
    return $property.Value
}

function Set-TeamProperty {
    param([Parameter(Mandatory = $true)]$InputObject, [Parameter(Mandatory = $true)][string]$Name, $Value)
    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property) {
        $InputObject | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
    else {
        $property.Value = $Value
    }
}

function Get-TeamTimestamp {
    param([datetime]$Now = [datetime]::UtcNow)
    return $Now.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ", [System.Globalization.CultureInfo]::InvariantCulture)
}

function ConvertFrom-TeamTimestamp {
    <# A timestamp this module wrote, as UTC; $null for anything else. #>
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
    $parsed = [datetime]::MinValue
    $styles = [System.Globalization.DateTimeStyles]::AssumeUniversal -bor [System.Globalization.DateTimeStyles]::AdjustToUniversal
    if ([datetime]::TryParse($Text, [System.Globalization.CultureInfo]::InvariantCulture, $styles, [ref]$parsed)) {
        return $parsed
    }
    return $null
}

function Read-TeamJson {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { throw "missing: $Path" }
    $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    return (ConvertFrom-Json -InputObject $text)
}

function Write-TeamJson {
    <# UTF-8 without a byte-order mark, LF, written beside the target and moved over it. #>
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)]$Document)
    $json = ($Document | ConvertTo-Json -Depth 12)
    $json = ($json -replace "`r`n", "`n").TrimEnd() + "`n"
    $temporary = "$Path.tmp"
    [System.IO.File]::WriteAllText($temporary, $json, (New-Object System.Text.UTF8Encoding($false)))
    # A reader holding the file for an instant (the lead reading lock.json while a cycle
    # started, 2026-10-01) made the delete fail and ended the cycle with the lock half
    # written. The swap is retried for a few seconds; the last failure is the one thrown.
    for ($attempt = 1; ; $attempt++) {
        try {
            if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Force -ErrorAction Stop }
            Move-Item -LiteralPath $temporary -Destination $Path -ErrorAction Stop
            break
        }
        catch {
            if ($attempt -ge 20) { throw }
            Start-Sleep -Milliseconds 250
        }
    }
}

function Get-TeamTasks {
    <# The queue's tasks as an array, at 0, 1 and many. #>
    param($Queue)
    $tasks = Get-TeamProperty -InputObject $Queue -Name "tasks"
    if ($null -eq $tasks) { return @() }
    return @($tasks)
}

function Test-TeamQueue {
    <#
    .SYNOPSIS
        Every way the queue breaks the protocol, as sentences. Empty when it breaks none.
    #>
    param($Queue)
    $problems = New-Object System.Collections.ArrayList
    if ($null -eq $Queue) { [void]$problems.Add("the queue is empty or not JSON"); return @($problems.ToArray()) }
    if ((Get-TeamProperty -InputObject $Queue -Name "version") -ne 1) {
        [void]$problems.Add("version must be 1")
    }
    if ($null -eq $Queue.PSObject.Properties["tasks"]) {
        [void]$problems.Add("tasks is missing")
        return @($problems.ToArray())
    }
    $seen = @{}
    $areas = @{}
    foreach ($task in (Get-TeamTasks -Queue $Queue)) {
        $id = [string](Get-TeamProperty -InputObject $task -Name "id" -Default "")
        $label = if ($id) { $id } else { "(a task without an id)" }
        foreach ($field in $script:TeamRequiredFields) {
            if ($null -eq $task.PSObject.Properties[$field]) {
                [void]$problems.Add("${label}: the field '$field' is missing")
            }
        }
        if ($id -cnotmatch '^[a-z0-9][a-z0-9-]{2,63}$') {
            [void]$problems.Add("${label}: an id is 3-64 characters of a-z, 0-9 and '-'")
        }
        if ($seen.ContainsKey($id)) { [void]$problems.Add("${label}: the id is used twice") }
        $seen[$id] = $true

        $state = [string](Get-TeamProperty -InputObject $task -Name "state" -Default "")
        if ($script:TeamStates -notcontains $state) {
            [void]$problems.Add("${label}: '$state' is not a state")
        }
        if ([string]::IsNullOrWhiteSpace([string](Get-TeamProperty -InputObject $task -Name "title" -Default ""))) {
            [void]$problems.Add("${label}: the title is empty")
        }
        $taskAreas = @(Get-TeamProperty -InputObject $task -Name "area" -Default @())
        foreach ($area in $taskAreas) {
            $text = [string]$area
            if ($text -match '^[\\/]' -or $text -match '\.\.' -or $text -match '^[A-Za-z]:') {
                [void]$problems.Add("${label}: the area '$text' must be a path inside the repository")
            }
            elseif (@("", ".") -contains (Get-TeamAreaKey -Area $text)) {
                # '*', '.', './': everything. An area names files or folders; "the whole
                # repository" would hold every other task out of work, or - compared by another
                # key - none (the two rules disagreed on exactly these, 2026-10-02).
                [void]$problems.Add("${label}: the area '$text' is the whole repository; an area names files or folders inside it")
            }
        }
        $branch = [string](Get-TeamProperty -InputObject $task -Name "branch" -Default "")
        if ($branch -match 'hand-gestures' -or $branch -eq "main") {
            [void]$problems.Add("${label}: the branch '$branch' is not a team branch")
        }
        foreach ($dependency in @(Get-TeamProperty -InputObject $task -Name "depends_on" -Default @())) {
            $name = [string]$dependency
            if ($name -eq $id) { [void]$problems.Add("${label}: a task cannot depend on itself") }
            elseif (@(Get-TeamTasks -Queue $Queue | Where-Object { [string]$_.id -eq $name }).Count -eq 0) {
                [void]$problems.Add("${label}: depends on '$name', which is not in the queue")
            }
        }
        $budget = Get-TeamProperty -InputObject $task -Name "budget"
        if ($null -ne $budget) {
            $cap = Get-TeamProperty -InputObject $budget -Name "max_usd" -Default 0
            if (-not ($cap -is [ValueType]) -or [double]$cap -lt 0) {
                [void]$problems.Add("${label}: budget.max_usd must be a number, zero or more")
            }
        }
        if (@("assigned", "in_progress", "inspecting", "returned") -contains $state) {
            if (@($taskAreas).Count -eq 0) {
                [void]$problems.Add("${label}: a task that is being worked on names its file area")
            }
            # Section 4: two concurrent tasks never share an area. ONE rule for it
            # (Test-TeamAreasOverlap): the split's judge, the cycle's holder check
            # (Get-TeamAreaHolders) and this one compare the same key.
            foreach ($area in $taskAreas) {
                foreach ($other in @($areas.Keys)) {
                    if (Test-TeamAreasOverlap -First ([string]$area) -Second ([string]$other)) {
                        [void]$problems.Add("${label}: the area '$area' overlaps the area of $($areas[$other])")
                    }
                }
            }
            foreach ($area in $taskAreas) {
                $areas[(Get-TeamAreaKey -Area ([string]$area))] = $label
            }
        }
    }
    return @($problems.ToArray())
}

function Get-TeamUnmetDependencies {
    <#
    .SYNOPSIS
        The ids in a task's `depends_on` that are not yet ON MAIN, so the task must wait.

    .DESCRIPTION
        A worker's branch is opened from main, so a dependency serves it only once it is
        there: `awaiting_release`, `released` or `done`. `merged` is the cycle's integration
        branch, not main - a task that depends on it waits for the lead's merge (ADR-0224
        needed this: layer 3 reads the code of layers 1 and 2).
    #>
    param([Parameter(Mandatory = $true)]$Task, [Parameter(Mandatory = $true)]$Queue)
    $onMain = @("awaiting_release", "released", "done")
    $unmet = New-Object System.Collections.ArrayList
    foreach ($dependency in @(Get-TeamProperty -InputObject $Task -Name "depends_on" -Default @())) {
        $name = [string]$dependency
        $other = @(Get-TeamTasks -Queue $Queue | Where-Object { [string]$_.id -eq $name })
        $state = if (@($other).Count -gt 0) { [string](Get-TeamProperty -InputObject $other[0] -Name "state" -Default "") } else { "" }
        if ($onMain -notcontains $state) { [void]$unmet.Add($name) }
    }
    return @($unmet.ToArray())
}

function Test-TeamSplitCandidate {
    <#
    .SYNOPSIS
        Whether a task is a proposal waiting for the lead's split into tasks with areas.

    .DESCRIPTION
        A proposal (it has a `proposal` file) with no area yet, that the owner approved, or
        that serves a roadmap row and is therefore approved in advance (TEAM_PROTOCOL 3a).
        A proposal with no roadmap row is the owner's to approve and is not one.
    #>
    param([Parameter(Mandatory = $true)]$Task)
    if (-not ([string](Get-TeamProperty -InputObject $Task -Name "proposal" -Default "")).Trim()) { return $false }
    if (@(Get-TeamProperty -InputObject $Task -Name "area" -Default @()).Count -gt 0) { return $false }
    $state = [string](Get-TeamProperty -InputObject $Task -Name "state" -Default "")
    if ($state -eq "approved") { return $true }
    $row = [string](Get-TeamProperty -InputObject $Task -Name "roadmap_row" -Default "")
    return ($state -eq "proposed" -and $row.Trim().Length -gt 0)
}

function Get-TeamSplitCandidates {
    param($Queue)
    return @(Get-TeamTasks -Queue $Queue | Where-Object { Test-TeamSplitCandidate -Task $_ })
}

function Get-TeamNextRole {
    <#
    .SYNOPSIS
        What the cycle does next for a task in this state.

    .DESCRIPTION
        Returns an object with Kind and Role:
          run   - a fresh run of Role;
          move  - no run; the task moves to NextState;
          gate  - one of the owner's three gates; nothing runs;
          rest  - nothing to do (done, stopped, released, merged).
    #>
    param([Parameter(Mandatory = $true)]$Task)
    $state = [string](Get-TeamProperty -InputObject $Task -Name "state" -Default "")
    if ($script:TeamOwnerGates.ContainsKey($state)) {
        return [pscustomobject]@{ Kind = "gate"; Role = ""; NextState = ""; Gate = $script:TeamOwnerGates[$state] }
    }
    # A proposal waiting for its split is the lead's, before the cycle's task loop: it is not
    # run, and - without an area - it never becomes a worker's.
    if (Test-TeamSplitCandidate -Task $Task) {
        return [pscustomobject]@{ Kind = "rest"; Role = ""; NextState = ""; Gate = "" }
    }
    switch ($state) {
        "proposed" {
            # A proposal is the owner's to approve; it never reaches a worker on its own.
            return [pscustomobject]@{ Kind = "move"; Role = ""; NextState = "awaiting_owner"; Gate = "" }
        }
        "approved" {
            $needsPlan = [bool](Get-TeamProperty -InputObject $Task -Name "needs_integration" -Default $false)
            $plan = [string](Get-TeamProperty -InputObject $Task -Name "plan" -Default "")
            if ($needsPlan -and -not $plan) {
                return [pscustomobject]@{ Kind = "run"; Role = "integrator"; NextState = "assigned"; Gate = "" }
            }
            return [pscustomobject]@{ Kind = "move"; Role = ""; NextState = "assigned"; Gate = "" }
        }
        "assigned" { return [pscustomobject]@{ Kind = "run"; Role = "worker"; NextState = "inspecting"; Gate = "" } }
        "returned" { return [pscustomobject]@{ Kind = "run"; Role = "worker"; NextState = "inspecting"; Gate = "" } }
        "in_progress" {
            # A run that was killed left the task here. The next cycle takes it up again.
            return [pscustomobject]@{ Kind = "run"; Role = "worker"; NextState = "inspecting"; Gate = "" }
        }
        "inspecting" { return [pscustomobject]@{ Kind = "run"; Role = "inspector"; NextState = ""; Gate = "" } }
        default { return [pscustomobject]@{ Kind = "rest"; Role = ""; NextState = ""; Gate = "" } }
    }
}

function Get-TeamAreaHolders {
    <# The ids of the tasks IN WORK (assigned, in_progress, inspecting, returned) whose area
       overlaps this task's. Section 4: two concurrent tasks never share an area - Test-TeamQueue
       refuses a queue that has two such tasks, so a task is not moved into work beside one. #>
    param([Parameter(Mandatory = $true)]$Task, [Parameter(Mandatory = $true)]$Queue)
    $id = [string](Get-TeamProperty -InputObject $Task -Name "id" -Default "")
    $mine = @(Get-TeamProperty -InputObject $Task -Name "area" -Default @())
    $holders = New-Object System.Collections.ArrayList
    foreach ($other in (Get-TeamTasks -Queue $Queue)) {
        $otherId = [string](Get-TeamProperty -InputObject $other -Name "id" -Default "")
        if ($otherId -eq $id) { continue }
        $state = [string](Get-TeamProperty -InputObject $other -Name "state" -Default "")
        if (@("assigned", "in_progress", "inspecting", "returned") -notcontains $state) { continue }
        $shared = $false
        foreach ($theirs in @(Get-TeamProperty -InputObject $other -Name "area" -Default @())) {
            foreach ($area in $mine) {
                if (Test-TeamAreasOverlap -First ([string]$area) -Second ([string]$theirs)) { $shared = $true }
            }
        }
        if ($shared) { [void]$holders.Add($otherId) }
    }
    return @($holders.ToArray())
}

# ------------------------------------------------------------------ the lead's split

function Get-TeamAreaKey {
    <# An area as it is compared: forward slashes, no trailing glob or slash, lower case. #>
    param([string]$Area)
    $text = ($Area -replace '\\', '/').Trim().ToLowerInvariant()
    while ($text.StartsWith("./")) { $text = $text.Substring(2) }
    return $text.TrimEnd("/", "*")
}

function Test-TeamAreasOverlap {
    param([string]$First, [string]$Second)
    $one = Get-TeamAreaKey -Area $First
    $two = Get-TeamAreaKey -Area $Second
    if (-not $one -or -not $two) { return $false }
    return ($one -eq $two -or $one.StartsWith($two + "/") -or $two.StartsWith($one + "/"))
}

# ------------------------------------------------------------------ the pool's seats

function Select-TeamSeatFill {
    <#
    .SYNOPSIS
        Which of the runs that could start now get a seat (cycle-seat-pool).

    .DESCRIPTION
        Seats are per role: -Seats maps a role to how many of its runs may be in flight (a
        role it does not name has one). -Candidates are the runs that could start, in the
        order they are served (queue order), each with Task and Role; -InFlight are the runs
        in flight, each with Task and Role. A candidate is passed over - it waits for the
        next refill - when

          * its task already has a run in flight or chosen (one run a task);
          * every seat of ITS role is taken, by runs in flight and by candidates chosen
            before it. A role never takes another role's seat: three inspections do not
            keep a worker seat empty;
          * it works on files (a worker, an inspector) and its task's area overlaps the
            area of ANOTHER task whose worker or inspector is in flight or was chosen
            before it. Section 4: two concurrent tasks never share an area
            (Test-TeamAreasOverlap, the one rule). A run in flight is the cycle's own copy
            of its task - the queue's rules cannot see it once the store says otherwise.

        A candidate that is passed over does not hold back the ones behind it.
    #>
    param([object[]]$Candidates = @(), [object[]]$InFlight = @(), [Parameter(Mandatory = $true)][hashtable]$Seats)
    $onFiles = @("worker", "inspector")
    $taken = @{}
    $busy = @{}
    $held = New-Object System.Collections.ArrayList
    foreach ($run in @($InFlight)) {
        $role = [string]$run.Role
        $taken[$role] = 1 + [int]$taken[$role]
        if ($null -eq $run.Task) { continue }
        $busy[[string]$run.Task.id] = $true
        if ($onFiles -contains $role) { [void]$held.Add($run.Task) }
    }
    $chosen = New-Object System.Collections.ArrayList
    foreach ($candidate in @($Candidates)) {
        $role = [string]$candidate.Role
        $task = $candidate.Task
        $id = if ($null -ne $task) { [string]$task.id } else { "" }
        if ($id -and $busy.ContainsKey($id)) { continue }
        $limit = if ($Seats.ContainsKey($role)) { [int]$Seats[$role] } else { 1 }
        if ([int]$taken[$role] -ge $limit) { continue }
        if ($null -ne $task -and $onFiles -contains $role) {
            $shared = $false
            foreach ($other in $held) {
                if ([string]$other.id -eq $id) { continue }
                foreach ($theirs in @(Get-TeamProperty -InputObject $other -Name "area" -Default @())) {
                    foreach ($area in @(Get-TeamProperty -InputObject $task -Name "area" -Default @())) {
                        if (Test-TeamAreasOverlap -First ([string]$area) -Second ([string]$theirs)) { $shared = $true }
                    }
                }
            }
            if ($shared) { continue }
            [void]$held.Add($task)
        }
        $taken[$role] = 1 + [int]$taken[$role]
        if ($id) { $busy[$id] = $true }
        [void]$chosen.Add($candidate)
    }
    return @($chosen.ToArray())
}

function Read-TeamCycleSettings {
    <#
    .SYNOPSIS
        The seats a running cycle works with: its parameters, or what `team/cycle-settings.json`
        says when that file is there.

    .DESCRIPTION
        A cycle process is bound to the arguments it started with and does not end while there
        is work (2026-10-01: a setting changed at 16:45 took effect at 19:35). The file is read
        at every refill: { "max_parallel": 3, "max_inspectors": 2, "max_integrators": 1 } -
        each optional, each a whole number from 1 to 16; another key is not this function's.
        A file that cannot be read, or a value that is not such a number, changes NOTHING - the
        parameters stand, not half of the file - and the problem is returned as a sentence.
    #>
    param([Parameter(Mandatory = $true)][string]$Path, [int]$Workers, [int]$Inspectors, [int]$Integrators)
    $named = [ordered]@{ max_parallel = $Workers; max_inspectors = $Inspectors; max_integrators = $Integrators }
    $problems = New-Object System.Collections.ArrayList
    if (Test-Path -LiteralPath $Path) {
        $document = $null
        try { $document = Read-TeamJson -Path $Path } catch { [void]$problems.Add("team/cycle-settings.json okunamadı: " + (([string]$_.Exception.Message) -replace '\s+', ' ')) }
        if (@($problems).Count -eq 0 -and $document -isnot [System.Management.Automation.PSCustomObject]) {
            [void]$problems.Add("team/cycle-settings.json bir ayar nesnesi değil")
        }
        if (@($problems).Count -eq 0) {
            foreach ($name in @($named.Keys)) {
                if ($null -eq $document.PSObject.Properties[$name]) { continue }
                $value = $document.$name
                if (($value -is [int] -or $value -is [long]) -and $value -ge 1 -and $value -le 16) { $named[$name] = [int]$value }
                else { [void]$problems.Add("team/cycle-settings.json: '$name' 1 ile 16 arasında bir tam sayı olmalı ('$value' değil)") }
            }
        }
    }
    if (@($problems).Count -gt 0) { $named = [ordered]@{ max_parallel = $Workers; max_inspectors = $Inspectors; max_integrators = $Integrators } }
    return [pscustomobject]@{
        Workers = [int]$named["max_parallel"]; Inspectors = [int]$named["max_inspectors"]; Integrators = [int]$named["max_integrators"]
        Problems = @($problems.ToArray())
    }
}

function Read-TeamRunTempRoot {
    <#
    .SYNOPSIS
        The folder under which each run of the cycle gets its own temp folder: the
        'run_temp_root' of team/cycle-settings.json, an absolute path, or "" (the machine's TEMP).
    #>
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return "" }
    try { $document = Read-TeamJson -Path $Path } catch { return "" }
    if ($document -isnot [System.Management.Automation.PSCustomObject]) { return "" }
    if ($null -eq $document.PSObject.Properties["run_temp_root"]) { return "" }
    $value = [string]$document.run_temp_root
    if (-not $value -or -not [System.IO.Path]::IsPathRooted($value)) { return "" }
    return $value
}

function Read-TeamSplitFile {
    <#
    .SYNOPSIS
        The file the lead's split run wrote: a JSON list of task objects (or an object whose
        `tasks` is one). The answer says whether it could be read, and why not.
    #>
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{ Ok = $false; Split = @(); Why = "the lead wrote no split file: $Path" }
    }
    try {
        $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
        if (-not $text.Trim()) { return [pscustomobject]@{ Ok = $false; Split = @(); Why = "the split file is empty: $Path" } }
        $document = ConvertFrom-Json -InputObject $text
        $list = if ($null -ne $document -and $null -ne $document.PSObject.Properties["tasks"]) { $document.tasks } else { $document }
        return [pscustomobject]@{ Ok = $true; Split = @($list); Why = "" }
    }
    catch {
        return [pscustomobject]@{ Ok = $false; Split = @(); Why = "the split file is not JSON: $($_.Exception.Message)" }
    }
}

function Test-TeamSplit {
    <#
    .SYNOPSIS
        Every reason a lead's split is refused, as sentences; empty when it is sound. The
        SCRIPT judges, not the model: a split is taken whole or refused whole.

    .DESCRIPTION
        Per task: the fields the queue's shape needs; an id that is free; areas that are paths
        inside the repository, at most 25 of them, none a shared file or a directory holding
        one, none overlapping a task in work (or another task of the same split that does not
        wait for it); never main, never the frozen hand-gestures branch; dependencies that exist.
    #>
    param($Split, [Parameter(Mandatory = $true)]$Queue)
    $problems = New-Object System.Collections.ArrayList
    $items = @($Split)
    if (@($items).Count -eq 0) { [void]$problems.Add("the split holds no task"); return @($problems.ToArray()) }
    $existing = @(Get-TeamTasks -Queue $Queue)
    $inWork = @($existing | Where-Object { $script:TeamStatesInWork -contains [string](Get-TeamProperty -InputObject $_ -Name "state" -Default "") })
    $ids = @{}
    foreach ($item in $items) {
        if ($item -is [System.Management.Automation.PSCustomObject]) {
            $named = [string](Get-TeamProperty -InputObject $item -Name "id" -Default "")
            if ($named) { $ids[$named] = $true }
        }
    }
    $seen = @{}
    $previous = New-Object System.Collections.ArrayList
    foreach ($item in $items) {
        if ($null -eq $item -or $item -isnot [System.Management.Automation.PSCustomObject]) {
            [void]$problems.Add("an entry of the split is not a task object")
            continue
        }
        $id = [string](Get-TeamProperty -InputObject $item -Name "id" -Default "")
        $label = if ($id) { $id } else { "(a task without an id)" }
        foreach ($field in $script:TeamSplitRequired) {
            $value = Get-TeamProperty -InputObject $item -Name $field
            if ($null -eq $value -or $value -isnot [string] -or -not $value.Trim()) {
                [void]$problems.Add("${label}: the field '$field' is missing")
            }
        }
        if ($id -and $id -cnotmatch '^[a-z0-9][a-z0-9-]{2,63}$') {
            [void]$problems.Add("${label}: an id is 3-64 characters of a-z, 0-9 and '-'")
        }
        if ($id) {
            if (@($existing | Where-Object { [string]$_.id -eq $id }).Count -gt 0) { [void]$problems.Add("${label}: the id is already in the queue") }
            if ($seen.ContainsKey($id)) { [void]$problems.Add("${label}: the id is used twice in the split") }
            $seen[$id] = $true
        }
        $areaValue = Get-TeamProperty -InputObject $item -Name "area"
        $areas = @($areaValue | Where-Object { $null -ne $_ })
        if (@($areas).Count -eq 0 -or @($areas | Where-Object { $_ -isnot [string] -or -not ([string]$_).Trim() }).Count -gt 0) {
            [void]$problems.Add("${label}: the field 'area' is missing")
            $areas = @($areas | Where-Object { $_ -is [string] -and ([string]$_).Trim() })
        }
        if (@($areas).Count -gt $script:TeamMaxAreaEntries) {
            [void]$problems.Add("${label}: the area names $(@($areas).Count) files; a task is at most $script:TeamMaxAreaEntries")
        }
        $depends = @(Get-TeamProperty -InputObject $item -Name "depends_on" -Default @())
        foreach ($area in $areas) {
            $text = [string]$area
            if ($text -match '^[\\/]' -or $text -match '\.\.' -or $text -match '^[A-Za-z]:') {
                [void]$problems.Add("${label}: the area '$text' must be a path inside the repository")
                continue
            }
            if ($text -match '(?i)hand-gestures') {
                [void]$problems.Add("${label}: the area '$text' is the frozen hand-gestures work")
                continue
            }
            $key = Get-TeamAreaKey -Area $text
            foreach ($shared in $script:TeamSharedFiles) {
                if (-not $key -or $key -eq "." -or $key -eq $shared -or $shared.StartsWith($key + "/")) {
                    [void]$problems.Add("${label}: the area '$text' is, or holds, the shared file $shared (the lead writes it)")
                    break
                }
            }
            foreach ($other in $inWork) {
                foreach ($otherArea in @(Get-TeamProperty -InputObject $other -Name "area" -Default @())) {
                    if (Test-TeamAreasOverlap -First $text -Second ([string]$otherArea)) {
                        [void]$problems.Add("${label}: the area '$text' overlaps the area of $($other.id)")
                    }
                }
            }
            foreach ($earlier in $previous) {
                if (@($earlier.depends) -contains $id -or $depends -contains $earlier.id) { continue }
                foreach ($earlierArea in @($earlier.areas)) {
                    if (Test-TeamAreasOverlap -First $text -Second ([string]$earlierArea)) {
                        [void]$problems.Add("${label}: the area '$text' overlaps the area of $($earlier.id)")
                    }
                }
            }
        }
        $branch = [string](Get-TeamProperty -InputObject $item -Name "branch" -Default "")
        if ($branch -ieq "main" -or $branch -match '(?i)hand-gestures') {
            [void]$problems.Add("${label}: the branch '$branch' is not a team branch")
        }
        foreach ($dependency in $depends) {
            $name = [string]$dependency
            if ($name -eq $id) { [void]$problems.Add("${label}: a task cannot depend on itself") }
            elseif (-not $ids.ContainsKey($name) -and @($existing | Where-Object { [string]$_.id -eq $name }).Count -eq 0) {
                [void]$problems.Add("${label}: depends on '$name', which is not in the queue or the split")
            }
        }
        [void]$previous.Add([pscustomobject]@{ id = $id; areas = @($areas); depends = @($depends) })
    }
    return @($problems.ToArray())
}

function ConvertTo-TeamSplitTasks {
    <#
    .SYNOPSIS
        A sound split as queue tasks: approved in advance, traced to their proposal, named
        by the cycle (no branch, no worktree) - only the fields the script knows are kept.
    #>
    param([Parameter(Mandatory = $true)]$Split, [Parameter(Mandatory = $true)]$Proposal, [double]$MaxUsd = 0, [datetime]$Now = [datetime]::UtcNow)
    $stamp = Get-TeamTimestamp -Now $Now
    $made = New-Object System.Collections.ArrayList
    foreach ($item in @($Split)) {
        $task = [ordered]@{
            id = [string]$item.id; title = ([string]$item.title).Trim(); roadmap_row = ([string]$item.roadmap_row).Trim()
            state = "approved"; area = @(@($item.area) | ForEach-Object { [string]$_ }); branch = ""; worktree = ""; assignee = ""
            reports = @(); budget = [pscustomobject]@{ max_usd = $MaxUsd }; created_at = $stamp; updated_at = $stamp
            goal = ([string]$item.goal).Trim(); acceptance = ([string]$item.acceptance).Trim()
            evidence_expected = ([string]$item.evidence_expected).Trim()
            proposal = [string](Get-TeamProperty -InputObject $Proposal -Name "proposal" -Default "")
        }
        $depends = @(Get-TeamProperty -InputObject $item -Name "depends_on" -Default @())
        if (@($depends).Count -gt 0) { $task["depends_on"] = @($depends | ForEach-Object { [string]$_ }) }
        if ([bool](Get-TeamProperty -InputObject $item -Name "needs_integration" -Default $false)) { $task["needs_integration"] = $true }
        [void]$made.Add([pscustomobject]$task)
    }
    return @($made.ToArray())
}

# ------------------------------------------------------------------ the Proje Yöneticisi's duty
# (pm-duty-stopped, the owner of 2026-10-03: "Böyle bulgular bulunduğunda konuyu proje
# yöneticisine iletsinler, proje yöneticisi de sana iletsin.") A task the cycle stopped wakes ONE
# lead run that writes a decision file; as with a split, THIS side judges the file and takes it
# whole or refuses it whole. The lead-protected paths are TeamArea.ps1's list - one list; without
# it loaded nothing is accepted.

# What a decision writes in front of the reason. A stopped task whose reason starts with the
# first is the Danışman's: it is never handed to a duty run again until somebody else moves it.
$script:TeamDutyEscalated = "Danışman'a iletildi: "
$script:TeamDutyReturned = "Proje Yöneticisi: "
$script:TeamDutyActions = @("return", "grant_and_return", "escalate")
$script:TeamDutyMaxGrants = 5
$script:TeamDutyMaxReason = 1200
$script:TeamDutyMaxTasks = 8

function Get-TeamDutyPrefix {
    <# The text a decision puts in front of a reason: 'escalated' or 'returned'. #>
    param([Parameter(Mandatory = $true)][ValidateSet("escalated", "returned")][string]$Kind)
    if ($Kind -eq "escalated") { return $script:TeamDutyEscalated }
    return $script:TeamDutyReturned
}

function Get-TeamDutyCandidates {
    <#
    .SYNOPSIS
        The stopped tasks a duty run of the Proje Yöneticisi is handed now, in queue order.

    .DESCRIPTION
        A task in `stopped` - but not one the Danışman already has (its reason starts with
        "Danışman'a iletildi: "), not one in -Skip (ids the caller set aside), and not one
        handed at THIS stop: -Handed maps an id to the updated_at it had when it was handed, so
        a task stopped again later (a new updated_at) is handed again. At most -Max a run; the
        rest are handed when it ends.
    #>
    param($Queue, [hashtable]$Handed = @{}, [hashtable]$Skip = @{}, [int]$Max = $script:TeamDutyMaxTasks)
    $found = New-Object System.Collections.ArrayList
    foreach ($task in @(Get-TeamTasks -Queue $Queue)) {
        if (@($found).Count -ge $Max) { break }
        if ([string](Get-TeamProperty -InputObject $task -Name "state" -Default "") -ne "stopped") { continue }
        $id = [string](Get-TeamProperty -InputObject $task -Name "id" -Default "")
        if (-not $id -or $Skip.ContainsKey($id)) { continue }
        $reason = [string](Get-TeamProperty -InputObject $task -Name "reason" -Default "")
        if ($reason.StartsWith($script:TeamDutyEscalated, [System.StringComparison]::Ordinal)) { continue }
        $stamp = [string](Get-TeamProperty -InputObject $task -Name "updated_at" -Default "")
        if ($Handed.ContainsKey($id) -and [string]$Handed[$id] -ceq $stamp) { continue }
        [void]$found.Add($task)
    }
    return @($found.ToArray())
}

function Read-TeamDutyFile {
    <#
    .SYNOPSIS
        The file a duty run wrote: { "decisions": [ ... ] }. The answer says whether it could
        be read, and why not.
    #>
    param([Parameter(Mandatory = $true)][string]$Path)
    $refuse = { param([string]$Why) return [pscustomobject]@{ Ok = $false; Decisions = @(); Why = $Why } }
    if (-not (Test-Path -LiteralPath $Path)) { return (& $refuse "the Proje Yöneticisi wrote no decision file: $Path") }
    try {
        $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
        if (-not $text.Trim()) { return (& $refuse "the decision file is empty: $Path") }
        $document = ConvertFrom-Json -InputObject $text
    }
    catch { return (& $refuse "the decision file is not JSON: $($_.Exception.Message)") }
    if ($document -isnot [System.Management.Automation.PSCustomObject] -or $null -eq $document.PSObject.Properties["decisions"]) {
        return (& $refuse "the decision file is not an object holding 'decisions'")
    }
    return [pscustomobject]@{ Ok = $true; Decisions = @($document.decisions | Where-Object { $null -ne $_ }); Why = "" }
}

function Test-TeamDuty {
    <#
    .SYNOPSIS
        Every reason a duty run's decisions are refused, as sentences; empty when they are
        sound. Taken whole or refused whole: one bad decision and NOTHING is applied.

    .DESCRIPTION
        Each decision is an object { task, action, grant, reason }:
          * task    one of -Listed (the stopped tasks this run was handed), once;
          * action  return | grant_and_return | escalate (as written, lower case);
          * grant   only with grant_and_return: a list of 1 to 5 plainly written
                    repository-relative paths (ConvertTo-TeamAreaPath: no '..', no drive, no
                    leading slash, no empty or dotted segment), none lead-protected
                    (Get-TeamAreaProtection), and the area stays within 25 entries;
          * reason  Turkish text for the worker, not blank, at most 1200 characters.
    #>
    param($Decisions, [string[]]$Listed = @(), [Parameter(Mandatory = $true)]$Queue)
    $problems = New-Object System.Collections.ArrayList
    # The protected list is TeamArea.ps1's; without it a grant cannot be judged, and a judge
    # that cannot judge refuses.
    foreach ($needed in @("ConvertTo-TeamAreaPath", "Get-TeamAreaProtection")) {
        if ($null -eq (Get-Command -Name $needed -CommandType Function -ErrorAction SilentlyContinue)) {
            [void]$problems.Add("the protected-path list (scripts/lib/TeamArea.ps1) is not loaded: no decision is accepted")
            return @($problems.ToArray())
        }
    }
    $items = @($Decisions | Where-Object { $null -ne $_ })
    if (@($items).Count -eq 0) { [void]$problems.Add("the decision file holds no decision"); return @($problems.ToArray()) }
    $seen = @{}
    foreach ($item in $items) {
        if ($item -isnot [System.Management.Automation.PSCustomObject]) {
            [void]$problems.Add("an entry of the decision file is not a decision object")
            continue
        }
        $id = [string](Get-TeamProperty -InputObject $item -Name "task" -Default "")
        $label = if ($id) { $id } else { "(a decision without a task)" }
        if (@($Listed) -cnotcontains $id) { [void]$problems.Add("${label}: not one of the stopped tasks this run was given") }
        if ($id -and $seen.ContainsKey($id)) { [void]$problems.Add("${label}: more than one decision for one task") }
        $seen[$id] = $true
        $actionValue = Get-TeamProperty -InputObject $item -Name "action" -Default ""
        $action = if ($actionValue -is [string]) { $actionValue } else { "" }
        if ($script:TeamDutyActions -cnotcontains $action) { [void]$problems.Add("${label}: '$actionValue' is not an action (return, grant_and_return, escalate)") }
        $reasonValue = Get-TeamProperty -InputObject $item -Name "reason" -Default ""
        if ($reasonValue -isnot [string] -or -not $reasonValue.Trim()) { [void]$problems.Add("${label}: the reason is empty") }
        elseif ($reasonValue.Trim().Length -gt $script:TeamDutyMaxReason) { [void]$problems.Add("${label}: the reason is $($reasonValue.Trim().Length) characters; at most $script:TeamDutyMaxReason") }

        # The value as written: Get-TeamProperty would unroll a one-entry list into its entry.
        $grantProperty = $item.PSObject.Properties["grant"]
        # NOT `$grantValue = if (...) { $grantProperty.Value }`: an if-expression's output is
        # enumerated, so a one-entry list came out as its entry and was refused as "not a list".
        $grantValue = $null
        if ($null -ne $grantProperty) { $grantValue = $grantProperty.Value }
        if ($action -ne "grant_and_return") {
            if ($null -ne $grantValue -and -not ($grantValue -is [array] -and @($grantValue).Count -eq 0)) {
                [void]$problems.Add("${label}: a grant goes only with grant_and_return")
            }
            continue
        }
        if ($null -ne $grantValue -and $grantValue -isnot [array]) {
            [void]$problems.Add("${label}: the grant must be a list of paths")
            continue
        }
        $grants = @($grantValue | Where-Object { $null -ne $_ })
        if (@($grants).Count -lt 1 -or @($grants).Count -gt $script:TeamDutyMaxGrants) {
            [void]$problems.Add("${label}: grant_and_return names 1 to $script:TeamDutyMaxGrants paths, not $(@($grants).Count)")
            continue
        }
        $task = @(Get-TeamTasks -Queue $Queue | Where-Object { [string]$_.id -ceq $id })
        $area = if (@($task).Count -gt 0) { @(Get-TeamProperty -InputObject $task[0] -Name "area" -Default @() | ForEach-Object { [string]$_ }) } else { @() }
        $added = New-Object System.Collections.ArrayList
        foreach ($grant in $grants) {
            $path = if ($grant -is [string]) { ConvertTo-TeamAreaPath -Text $grant } else { $null }
            if ($null -eq $path -or -not $path.Inside) {
                [void]$problems.Add("${label}: the grant '$grant' is not a plain repository-relative path")
                continue
            }
            $entry = Get-TeamAreaProtection -Path $path.Path
            if ($null -ne $entry) {
                [void]$problems.Add("${label}: the grant '$($path.Path)' is a lead-protected path ($($entry.Name)); the Danışman decides it")
                continue
            }
            if (-not (Test-TeamPathInsideArea -Path $path.Path -Area $area) -and @($added) -notcontains (Get-TeamAreaKey -Area $path.Path)) {
                [void]$added.Add((Get-TeamAreaKey -Area $path.Path))
            }
        }
        $total = @($area).Count + @($added).Count
        if ($total -gt $script:TeamMaxAreaEntries) {
            [void]$problems.Add("${label}: the grant takes the area to $total entries; a task is at most $script:TeamMaxAreaEntries")
        }
    }
    return @($problems.ToArray())
}

function Get-TeamVerdict {
    <#
    .SYNOPSIS
        The inspector's verdict: the LAST line of its report that is one, and nothing else.

    .DESCRIPTION
        A report ends with exactly one of APPROVE | RETURN (list) | REJECT (reason). A report
        with no such line is not an approval: it is NONE, and the task is returned.
    #>
    param([string]$Report)
    $verdict = "NONE"
    $detail = ""
    foreach ($line in @(([string]$Report) -split "`r?`n")) {
        $text = $line.Trim().Trim('`', '*', ' ')
        # "**Verdict:** `RETURN (...)`" is how inspectors often write it (four reports in two
        # cycles, 2026-10-01): the label is dropped, and the list after it reaches the worker
        # instead of "the report did not end with a verdict".
        $text = ($text -creplace '^(Verdict|Karar)\s*:?\s*\**\s*:?\s*', '').Trim().Trim('`', '*', ' ')
        if ($text -cmatch '^(APPROVE|RETURN|REJECT)\b\s*[:(-]?\s*(.*?)\)?\s*$') {
            $verdict = $Matches[1]
            $detail = $Matches[2].Trim()
        }
        # A closing SENTENCE that restates the verdict: an inspector woken after its report (a
        # command it left running reported back) ends on one, and the last message is all the
        # cycle reads. Only this shape: "... verdict is / remains / stands [unchanged][:] `X`"
        # (hüküm / karar değişmedi / aynı / geçerli) with the verdict in backticks and NOTHING
        # after it but a full stop - not "could be", not a quotation of somebody else's verdict.
        elseif ($line -cmatch '(?i:\b(?:verdict|karar|hüküm)\s+(?:is|remains|stands|stays|değişmedi|aynı|geçerli)(?:\s+unchanged)?)\s*:?\s*`(APPROVE|RETURN|REJECT)\b\s*[:(-]?\s*([^`]*?)\)?\s*`\s*\.?\s*$') {
            $verdict = $Matches[1]
            $detail = $Matches[2].Trim()
        }
    }
    return [pscustomobject]@{ Verdict = $verdict; Detail = $detail }
}

function Get-TeamStateAfterInspection {
    <# Where a task goes after the inspector's verdict, and how many times it came back. #>
    param([Parameter(Mandatory = $true)]$Task, [Parameter(Mandatory = $true)][string]$Verdict)
    $returns = [int](Get-TeamProperty -InputObject $Task -Name "returns" -Default 0)
    switch ($Verdict) {
        "APPROVE" { return [pscustomobject]@{ State = "merged"; Returns = $returns; Reason = "" } }
        "REJECT" { return [pscustomobject]@{ State = "stopped"; Returns = $returns; Reason = "denetleyici reddetti" } }
        default {
            $returns = $returns + 1
            if ($returns -ge $script:TeamMaxReturns) {
                return [pscustomobject]@{ State = "stopped"; Returns = $returns; Reason = "ayni is iki kez geri verildi" }
            }
            $why = if ($Verdict -eq "NONE") { "rapor bir hukumle bitmedi" } else { "" }
            return [pscustomobject]@{ State = "returned"; Returns = $returns; Reason = $why }
        }
    }
}

function Get-TeamSummary {
    <# At most forty lines of a report: what goes into the queue and into another run. #>
    param([string]$Text, [int]$MaxLines = $script:TeamSummaryMaxLines)
    $lines = @(([string]$Text) -split "`r?`n" | ForEach-Object { $_.TrimEnd() })
    while (@($lines).Count -gt 0 -and [string]::IsNullOrWhiteSpace($lines[-1])) {
        if (@($lines).Count -eq 1) { $lines = @() } else { $lines = @($lines[0..(@($lines).Count - 2)]) }
    }
    if (@($lines).Count -le $MaxLines) { return @($lines) }
    # The END of a report carries the verdict and the numbers, so the end is what is kept.
    $kept = @($lines[(@($lines).Count - ($MaxLines - 1))..(@($lines).Count - 1)])
    return @(@("[... $(@($lines).Count - ($MaxLines - 1)) satir kesildi; tamami rapor dosyasinda]") + $kept)
}

function ConvertFrom-TeamEpoch {
    <# Unix seconds as the timestamp this module writes; "" for anything that is not a number. #>
    param($Seconds)
    if ($null -eq $Seconds -or $Seconds -isnot [ValueType] -or $Seconds -is [bool]) { return "" }
    try { return (Get-TeamTimestamp -Now ([DateTimeOffset]::FromUnixTimeSeconds([long]$Seconds)).UtcDateTime) }
    catch { return "" }
}

function Read-TeamRunResult {
    <#
    .SYNOPSIS
        What `claude -p` printed: the text, the cost, whether it failed - and, from the line
        stream (`--output-format stream-json --verbose`), the model that really ran, the usage
        limit it hit and the two percentages the tool itself reports.

    .DESCRIPTION
        Both shapes are read: the line stream of a run, and the single result document (the
        old runs' files). In a stream only three kinds of line are parsed, each ALONE: the
        result, a `rate_limit_event`, and the tool's own notice that it switched the model.
        The whole output is never parsed at once: Windows PowerShell 5.1 refuses JSON over
        2 MB and a worker's stream is larger. A line is taken by its PARSED type - the real
        result line starts with {"duration_api_ms", not {"type", and an assistant line can
        quote any of these words.

        Output with no result document is not trusted to be a report: the run FAILED, and its
        raw output stays in its file.

        The usage limit (team/plans/model-policy-cycle-integration.md, tool 2.1.285): the last
        `rate_limit_event` says `rejected`, with the limit's type and its reset as an epoch;
        or the result says "You've hit your <Fable|Opus|Sonnet|session|weekly> limit". What
        the limit closes is LimitScope: one model (LimitedModel), all of them, or unknown.
    #>
    param([string]$StdOut, [int]$ExitCode = 0, [string]$StdErr = "", [string]$Model = "")
    $text = ""
    $cost = 0.0
    $ok = $false
    $why = ""
    $usageLimited = $false
    $resetsAt = ""
    $limitType = ""
    $limitScope = ""
    $limitedModel = ""
    $ranModel = ""
    $substituted = $false
    $resultLine = ""
    $document = $null
    $lastEvent = $null
    $windows = @{ fable = $null; all = $null; session = $null }
    $windowNames = @{ "seven_day_overage_included" = "fable"; "seven_day" = "all"; "five_hour" = "session" }
    $eventLines = New-Object System.Collections.ArrayList
    $raw = [string]$StdOut
    foreach ($line in @($raw -split "`r?`n")) {
        $isResult = $line.Contains('"type":"result"')
        $isEvent = $line.Contains('"type":"rate_limit_event"')
        if (-not ($isResult -or $isEvent -or $line.Contains('"subtype":"model_consent_fallback"'))) { continue }
        $parsed = $null
        try { $parsed = ConvertFrom-Json -InputObject $line } catch { continue }
        $kind = [string](Get-TeamProperty -InputObject $parsed -Name "type" -Default "")
        if ($kind -eq "result") { $document = $parsed; $resultLine = $line }
        elseif ($kind -eq "rate_limit_event") {
            $info = Get-TeamProperty -InputObject $parsed -Name "rate_limit_info"
            if ($null -eq $info) { continue }
            $lastEvent = $info
            [void]$eventLines.Add($line)
            $unified = Get-TeamProperty -InputObject $info -Name "unifiedWindows"
            if ($null -eq $unified) { continue }
            foreach ($property in $unified.PSObject.Properties) {
                if (-not $windowNames.ContainsKey([string]$property.Name)) { continue }
                $used = Get-TeamProperty -InputObject $property.Value -Name "utilization"
                # The tool's own number in the tool's own unit (a fraction of 1): shown as a
                # percentage, never computed from anything else. Not a number, no value.
                if ($null -eq $used -or $used -isnot [ValueType] -or $used -is [bool]) { continue }
                $windows[$windowNames[[string]$property.Name]] = [pscustomobject]@{
                    used_pct  = [int][Math]::Round(100 * [double]$used, [System.MidpointRounding]::AwayFromZero)
                    resets_at = (ConvertFrom-TeamEpoch -Seconds (Get-TeamProperty -InputObject $property.Value -Name "resetsAt"))
                }
            }
        }
        elseif ([string](Get-TeamProperty -InputObject $parsed -Name "subtype" -Default "") -eq "model_consent_fallback") { $substituted = $true }
    }
    if ($null -eq $document -and $raw.Length -le 1000000) {
        # One document and nothing else: what `--output-format json` printed.
        try {
            $whole = ConvertFrom-Json -InputObject $raw
            if ($whole -is [System.Management.Automation.PSCustomObject]) { $document = $whole }
        }
        catch { }
    }
    $said = ""
    if ($null -ne $document) {
        $text = [string](Get-TeamProperty -InputObject $document -Name "result" -Default "")
        $cost = [double](Get-TeamProperty -InputObject $document -Name "total_cost_usd" -Default 0)
        $isError = [bool](Get-TeamProperty -InputObject $document -Name "is_error" -Default $false)
        # Only an ERROR result is the tool speaking; a report is the run's own words.
        if ($isError) { $said = $text }
        $subtype = [string](Get-TeamProperty -InputObject $document -Name "subtype" -Default "")
        # The model that really ran: side models (a small one for titles) appear beside it,
        # so it is the entry that cost the most.
        $usage = Get-TeamProperty -InputObject $document -Name "modelUsage"
        if ($usage -is [System.Management.Automation.PSCustomObject]) {
            $most = -1.0
            foreach ($property in $usage.PSObject.Properties) {
                $spent = Get-TeamProperty -InputObject $property.Value -Name "costUSD" -Default 0
                $spent = if ($spent -is [ValueType] -and $spent -isnot [bool]) { [double]$spent } else { 0.0 }
                if ($spent -gt $most) { $most = $spent; $ranModel = [string]$property.Name }
            }
        }
        $ok = ($ExitCode -eq 0) -and (-not $isError) -and ($text.Trim().Length -gt 0)
        if (-not $ok) {
            # The tool's own words first ("Not logged in"): a run that failed with the
            # subtype 'success' was reported to the owner as 'failed: success'
            # (pilot-01, 2026-09-30). ASCII, one line, bounded: it goes into a report.
            $first = (($text -split "`r?`n")[0] -replace '[^\x20-\x7E]', ' ').Trim()
            if ($first.Length -gt 120) { $first = $first.Substring(0, 120) }
            if ($isError -and $first) { $why = $first }
            elseif ($subtype -and $subtype -ne "success") { $why = $subtype }
            elseif (-not $text.Trim()) { $why = "the run returned an empty report" }
            else { $why = "exit $ExitCode" }
            # What an error printed is not a report, and is not kept as one.
            if ($isError) { $text = "" }
        }
    }
    else {
        # Prose instead of a document is not the tool's limit shape: the run's transcript may
        # hold any sentence at all (the limit is then read from stderr alone).
        $why = "the run printed no result document (exit $ExitCode)"
    }
    # A model the tool ran in place of the one asked for (it can switch a session off Fable
    # by itself; CLAUDE_CODE_NO_MODEL_FALLBACK is best effort). Only a model of the chain is
    # compared: a name this script does not know proves nothing.
    if ($Model -and (Test-TeamModelId -Model $ranModel) -and $ranModel -cne $Model) { $substituted = $true }
    if (-not $ok) {
        # Owner decision 2026-09-30: the ONE stop the team has is the subscription's usage
        # limit. It is read only from the tool's own error shape (ADR-0214 addendum 10): a
        # rejected event, an error result whose text STARTS with the tool's limit sentence, or
        # stderr's first line being it. A failed run that merely QUOTES those words (a test's
        # output, a report about limits) is a plain failure - its model is not barred.
        $sentencePattern = "(?i)^\s*(You.ve hit your (\w+ ){0,3}limit|You.re out of (extra usage|usage credits)|(Claude AI )?usage limit reached)"
        $sentence = ""
        $firstSaid = [string](@(([string]$said).TrimStart() -split "`r?`n")[0])
        $firstErr = [string](@(([string]$StdErr).TrimStart() -split "`r?`n")[0])
        if ($firstSaid -match $sentencePattern) { $sentence = $firstSaid }
        elseif ($firstErr -match $sentencePattern) { $sentence = $firstErr }
        $rejected = ($null -ne $lastEvent -and [string](Get-TeamProperty -InputObject $lastEvent -Name "status" -Default "") -eq "rejected")
        if ($rejected -or $sentence) {
            $usageLimited = $true
            $why = "Max kullanım limiti"
            if ($rejected) {
                $limitType = [string](Get-TeamProperty -InputObject $lastEvent -Name "rateLimitType" -Default "")
                $resetsAt = ConvertFrom-TeamEpoch -Seconds (Get-TeamProperty -InputObject $lastEvent -Name "resetsAt")
            }
            if (-not $resetsAt -and $sentence -match "limit reached\|(\d{10})") { $resetsAt = ConvertFrom-TeamEpoch -Seconds ([long]$Matches[1]) }
            $closes = Get-TeamLimitScope -Type $limitType -Text $sentence
            $limitScope = $closes.Scope
            $limitedModel = $closes.Model
        }
    }
    return [pscustomobject]@{
        Ok = $ok; Text = $text; CostUsd = $cost; Why = $why; UsageLimited = $usageLimited; ResetsAt = $resetsAt
        LimitType = $limitType; LimitScope = $limitScope; LimitedModel = $limitedModel
        RanModel = $ranModel; Substituted = $substituted
        Windows = [pscustomobject]@{ fable = $windows["fable"]; all = $windows["all"]; session = $windows["session"] }
        ResultLine = $resultLine; EventLines = @($eventLines.ToArray())
    }
}

# ------------------------------------------------------------------ the model policy
#
# ADR-0214 addendum 7 (owner, 2026-10-01). Three models, strongest first: the order IS the
# fallback chain and the meaning of "weaker". One setting document in the team store; the
# rules of that document are here, so the cycle and the fake API judge it with one list.

$script:TeamModelChain = @("claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5")
$script:TeamModelRoles = @("lead", "researcher", "integrator", "worker", "inspector")
# What a limit of the tool closes (the tool's own table, 2.1.285): one model, or every model.
$script:TeamLimitOfModel = @{
    "seven_day_overage_included" = "claude-fable-5-1"
    "seven_day_opus"             = "claude-opus-5-5"
    "seven_day_sonnet"           = "claude-sonnet-5-5"
}
$script:TeamLimitOfAll = @("five_hour", "seven_day")

function Get-TeamModelChain { return @($script:TeamModelChain) }

function Test-TeamModelId {
    <# Whether a text is one of the three model ids. Nothing else ever reaches a command line. #>
    param([string]$Model)
    return (@($script:TeamModelChain) -ccontains $Model)
}

function Get-TeamModelRank {
    <# The place in the chain: 0 is the strongest; -1 is not a model. A HIGHER rank is WEAKER. #>
    param([string]$Model)
    for ($index = 0; $index -lt @($script:TeamModelChain).Count; $index++) {
        if ($script:TeamModelChain[$index] -ceq $Model) { return $index }
    }
    return -1
}

function Get-TeamModelDefaults {
    <# The setting when nothing is stored: lead and inspector on the strongest, the rest one below. #>
    param([datetime]$Now = [datetime]::UtcNow)
    return [pscustomobject]@{
        roles      = [pscustomobject][ordered]@{
            lead = "claude-fable-5-1"; researcher = "claude-opus-5-5"; integrator = "claude-opus-5-5"
            worker = "claude-opus-5-5"; inspector = "claude-fable-5-1"
        }
        fallback   = $true
        updated_at = (Get-TeamTimestamp -Now $Now)
    }
}

function Read-TeamModelSetting {
    <#
    .SYNOPSIS
        The model setting as the contract states it, or every way a document breaks it.

    .DESCRIPTION
        {"roles": {"lead": m, "researcher": m, "integrator": m, "worker": m, "inspector": m},
         "fallback": bool, "updated_at": "<UTC Z>"}, every m one of the three ids. Refused,
        with the code of the first problem: a key or a role the contract does not have, a model
        that is not one of the three, a fallback that is not true or false, and an inspector
        WEAKER than the worker (inspector_weaker_than_worker).

        -Strict is the PUT: all five roles and `fallback` must be there. Without it (the file
        a person wrote, or nothing stored) what is missing is filled: a role from -DefaultModel
        when given, else from the defaults; fallback on.
    #>
    param($Document, [switch]$Strict, [string]$DefaultModel = "")
    $problems = New-Object System.Collections.ArrayList
    $codes = New-Object System.Collections.ArrayList
    $defaults = Get-TeamModelDefaults
    $roles = [ordered]@{}
    foreach ($role in $script:TeamModelRoles) {
        $roles[$role] = if ($DefaultModel) { $DefaultModel } else { [string]$defaults.roles.$role }
    }
    $fallback = $true
    $updated = [string]$defaults.updated_at
    $named = @{}
    if ($null -ne $Document -and $Document -isnot [System.Management.Automation.PSCustomObject]) {
        [void]$problems.Add("the setting is not an object"); [void]$codes.Add("invalid")
    }
    elseif ($null -eq $Document) {
        if ($Strict) { [void]$problems.Add("the setting is empty"); [void]$codes.Add("invalid") }
    }
    else {
        foreach ($property in $Document.PSObject.Properties) {
            if (@("roles", "fallback", "updated_at") -cnotcontains [string]$property.Name) {
                [void]$problems.Add("'$($property.Name)' is not a key of the setting"); [void]$codes.Add("unknown_key")
            }
        }
        $rolesNode = Get-TeamProperty -InputObject $Document -Name "roles"
        if ($rolesNode -is [System.Management.Automation.PSCustomObject]) {
            foreach ($property in $rolesNode.PSObject.Properties) {
                $role = [string]$property.Name
                $model = [string]$property.Value
                if (@($script:TeamModelRoles) -cnotcontains $role) {
                    [void]$problems.Add("'$role' is not a role"); [void]$codes.Add("unknown_role")
                    continue
                }
                if ($property.Value -isnot [string] -or -not (Test-TeamModelId -Model $model)) {
                    [void]$problems.Add("'$model' is not a model (role $role): one of $(@($script:TeamModelChain) -join ', ')"); [void]$codes.Add("unknown_model")
                    continue
                }
                $roles[$role] = $model
                $named[$role] = $true
            }
        }
        elseif ($null -ne $rolesNode -or $Strict) { [void]$problems.Add("'roles' is missing or not an object"); [void]$codes.Add("invalid") }
        $fallbackNode = Get-TeamProperty -InputObject $Document -Name "fallback"
        if ($fallbackNode -is [bool]) { $fallback = $fallbackNode }
        elseif ($null -ne $fallbackNode -or $Strict) { [void]$problems.Add("'fallback' is true or false"); [void]$codes.Add("invalid") }
        $stamp = [string](Get-TeamProperty -InputObject $Document -Name "updated_at" -Default "")
        if ($stamp) { $updated = $stamp }
    }
    if ($Strict) {
        foreach ($role in $script:TeamModelRoles) {
            if (-not $named.ContainsKey($role) -and @($codes | Where-Object { $_ -eq "invalid" }).Count -eq 0) {
                [void]$problems.Add("the role '$role' is missing"); [void]$codes.Add("missing_role")
            }
        }
    }
    # The inspector never runs on a weaker model than the worker (owner, 2026-10-01).
    if ((Get-TeamModelRank -Model $roles["inspector"]) -gt (Get-TeamModelRank -Model $roles["worker"])) {
        [void]$problems.Add("the inspector's model ($($roles['inspector'])) is weaker than the worker's ($($roles['worker']))")
        [void]$codes.Add("inspector_weaker_than_worker")
    }
    $setting = [pscustomobject]@{ roles = [pscustomobject]$roles; fallback = $fallback; updated_at = $updated }
    $code = if (@($codes).Count -gt 0) { [string]$codes[0] } else { "" }
    return [pscustomobject]@{ Ok = (@($problems).Count -eq 0); Setting = $setting; Problems = @($problems.ToArray()); Code = $code }
}

function Get-TeamLimitScope {
    <#
    .SYNOPSIS
        What a usage limit closes: Scope `model` (with Model), `all`, or `unknown`.

    .DESCRIPTION
        From the event's type when there is one, else from the limit's name in the sentence.
        A session or weekly limit closes every model: lowering would start runs that hit the
        same limit. `overage` / "out of usage credits" does not say whose limit it was: it is
        `unknown`, the cycle marks the model that ran and the chain finds out the rest.
    #>
    param([string]$Type = "", [string]$Text = "")
    if ($Type) {
        if ($script:TeamLimitOfModel.ContainsKey($Type)) { return [pscustomobject]@{ Scope = "model"; Model = [string]$script:TeamLimitOfModel[$Type] } }
        if (@($script:TeamLimitOfAll) -ccontains $Type) { return [pscustomobject]@{ Scope = "all"; Model = "" } }
        return [pscustomobject]@{ Scope = "unknown"; Model = "" }
    }
    if ($Text -match '(?i)hit your (fable|opus|sonnet) limit') {
        $byName = @{ "fable" = "claude-fable-5-1"; "opus" = "claude-opus-5-5"; "sonnet" = "claude-sonnet-5-5" }
        return [pscustomobject]@{ Scope = "model"; Model = [string]$byName[$Matches[1].ToLowerInvariant()] }
    }
    if ($Text -match '(?i)hit your (session|weekly) limit') { return [pscustomobject]@{ Scope = "all"; Model = "" } }
    return [pscustomobject]@{ Scope = "unknown"; Model = "" }
}

function Test-TeamModelLimited {
    <#
    .SYNOPSIS
        Whether the cycle remembers a model as limited NOW. -Limited maps an id to an object
        with `until` (UTC Z): a reset that has passed is no limit; no `until` at all is a
        limit nobody dated, which holds for as long as the map does (one cycle).
    #>
    param($Limited, [string]$Model, [datetime]$Now = [datetime]::UtcNow)
    if ($null -eq $Limited -or -not $Limited.ContainsKey($Model)) { return $false }
    $until = [string](Get-TeamProperty -InputObject $Limited[$Model] -Name "until" -Default "")
    if (-not $until) { return $true }
    $at = ConvertFrom-TeamTimestamp -Text $until
    return ($null -eq $at -or $Now.ToUniversalTime() -lt $at)
}

function Get-TeamRunModel {
    <#
    .SYNOPSIS
        The model a run of a role starts on now, or Model = $null: it must WAIT.

    .DESCRIPTION
        The configured model when it is open. When it is limited and -Fallback is on, the next
        open model DOWN the chain - never up: the strongest model's limit is the scarce thing
        and a worker is not sent to it. With -Fallback off, only the configured model.

        -Floor is the inspector's rule: the model the worker's run of that task really used.
        The run starts on nothing weaker - a configured model below the floor is raised to it,
        the chain stops at it, and before waiting a STRONGER open model is taken ("at least
        as strong"). When every model at least that strong is limited the answer is $null:
        the inspection waits; it is neither lowered nor skipped.

        ResetsAt (when Model is $null) is the earliest reset among the models the run may
        use, "" when nobody said when. Lowered is true when Model is weaker than Intended.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Configured,
        $Limited = @{},
        [bool]$Fallback = $true,
        [string]$Floor = "",
        [datetime]$Now = [datetime]::UtcNow
    )
    $chain = @($script:TeamModelChain)
    $start = Get-TeamModelRank -Model $Configured
    if ($start -lt 0) { throw "'$Configured' is not a model: one of $($chain -join ', ')" }
    $floorRank = Get-TeamModelRank -Model $Floor
    if ($floorRank -ge 0 -and $floorRank -lt $start) { $start = $floorRank }
    $last = if ($Fallback) { @($chain).Count - 1 } else { $start }
    if ($floorRank -ge 0 -and $last -gt $floorRank) { $last = $floorRank }
    $candidates = New-Object System.Collections.ArrayList
    for ($index = $start; $index -le $last; $index++) { [void]$candidates.Add($chain[$index]) }
    if ($floorRank -ge 0 -and $Fallback) {
        for ($index = $start - 1; $index -ge 0; $index--) { [void]$candidates.Add($chain[$index]) }
    }
    $picked = $null
    $resets = ""
    foreach ($candidate in $candidates) {
        if (-not (Test-TeamModelLimited -Limited $Limited -Model $candidate -Now $Now)) { $picked = $candidate; break }
        $until = [string](Get-TeamProperty -InputObject $Limited[$candidate] -Name "until" -Default "")
        if ($until -and (-not $resets -or [string]::CompareOrdinal($until, $resets) -lt 0)) { $resets = $until }
    }
    return [pscustomobject]@{
        Model      = $picked
        Intended   = $chain[$start]
        Lowered    = ($null -ne $picked -and (Get-TeamModelRank -Model $picked) -gt $start)
        ResetsAt   = $(if ($null -eq $picked) { $resets } else { "" })
        Candidates = @($candidates.ToArray())
        Floor      = $Floor
    }
}

function Get-TeamOkOutcome {
    <# The outcome of a run that finished, as its report entry keeps it: with the model that
       really ran, so a later inspection - in this cycle or another - knows its floor. The
       queue's schema has no field for it; `outcome` is free text and travels with the task. #>
    param([string]$Model = "")
    if (Test-TeamModelId -Model $Model) { return "tamam (model $Model)" }
    return "tamam"
}

function Get-TeamStrongerModel {
    <# The stronger of two models (a text that is not a model loses; both not models: ""). #>
    param([string]$First, [string]$Second)
    $one = Get-TeamModelRank -Model $First
    $two = Get-TeamModelRank -Model $Second
    if ($one -lt 0) { return $(if ($two -ge 0) { $Second } else { "" }) }
    if ($two -lt 0 -or $one -le $two) { return $First }
    return $Second
}

function Get-TeamWorkerModel {
    <# The STRONGEST model among the task's FINISHED worker runs that name one ("" when no
       entry says: a run from before the policy - Get-TeamInspectionFloor then takes the
       configured worker model). Not the last run's: a branch written on Fable, returned and
       reworked on Sonnet is still mostly Fable's work (ADR-0214 addendum 10). The reader of
       Get-TeamOkOutcome. #>
    param($Task)
    $model = ""
    foreach ($report in @(Get-TeamProperty -InputObject $Task -Name "reports" -Default @())) {
        if ([string](Get-TeamProperty -InputObject $report -Name "role" -Default "") -ne "worker") { continue }
        $outcome = [string](Get-TeamProperty -InputObject $report -Name "outcome" -Default "")
        if ($outcome -cmatch '^tamam \(model ([A-Za-z0-9._-]+)\)$' -and (Test-TeamModelId -Model $Matches[1])) { $model = Get-TeamStrongerModel -First $model -Second $Matches[1] }
    }
    return $model
}

function Get-TeamInspectionFloor {
    <# The model an inspection of this task is never started below, and never takes a verdict
       below: the STRONGEST model among the task's finished worker runs (a task keeps one
       branch for its life, so these are the runs of its current branch). An entry that names
       no model (a worker that finished before the policy) counts as the model the setting
       gives the worker; no entry at all (a task queued by hand) is that model too - an
       unknown is not "any model will do". Recorded is true when the floor is a model an
       entry named. #>
    param($Task, [Parameter(Mandatory = $true)]$Setting)
    $recorded = Get-TeamWorkerModel -Task $Task
    $configured = [string]$Setting.roles.worker
    $unnamed = @(@(Get-TeamProperty -InputObject $Task -Name "reports" -Default @()) | Where-Object {
            [string](Get-TeamProperty -InputObject $_ -Name "role" -Default "") -eq "worker" -and
            [string](Get-TeamProperty -InputObject $_ -Name "outcome" -Default "") -ceq "tamam"
        })
    if ($recorded -and (@($unnamed).Count -eq 0 -or (Get-TeamStrongerModel -First $recorded -Second $configured) -ceq $recorded)) {
        return [pscustomobject]@{ Model = $recorded; Recorded = $true }
    }
    return [pscustomobject]@{ Model = $configured; Recorded = $false }
}

function Get-TeamRoleTools {
    <# The tools a role file grants, read from its frontmatter. No frontmatter, no tools. #>
    param([Parameter(Mandatory = $true)][string]$RoleFile)
    $text = [System.IO.File]::ReadAllText($RoleFile, [System.Text.Encoding]::UTF8)
    if ($text -notmatch '(?s)\A---\r?\n(.*?)\r?\n---') { return @() }
    $front = $Matches[1]
    foreach ($line in @($front -split "`r?`n")) {
        if ($line -match '^tools:\s*(.+)$') {
            return @($Matches[1].Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        }
    }
    return @()
}

# ---------------------------------------------------------------------------- the lock

function Get-TeamLockDecision {
    <#
    .SYNOPSIS
        Whether this machine may run a cycle now, from the lock as it is.

    .DESCRIPTION
        free      - nobody holds it;
        stale     - held for more than six hours: taken over, and said so;
        ours      - held by this machine: a run of ours died, or is running;
        held      - held by the other machine: the cycle stops.
    #>
    param($Lock, [Parameter(Mandatory = $true)][string]$Machine, [datetime]$Now = [datetime]::UtcNow)
    if ($null -eq $Lock -or -not [bool](Get-TeamProperty -InputObject $Lock -Name "held" -Default $false)) {
        return [pscustomobject]@{ MayRun = $true; Kind = "free"; Holder = ""; Since = "" }
    }
    $holder = [string](Get-TeamProperty -InputObject $Lock -Name "machine" -Default "")
    $since = [string](Get-TeamProperty -InputObject $Lock -Name "acquired_at" -Default "")
    $at = ConvertFrom-TeamTimestamp -Text $since
    # A lock that does not say when it was taken cannot be shown to be fresh.
    if ($null -eq $at -or ($Now.ToUniversalTime() - $at).TotalHours -ge $script:TeamLockStaleHours) {
        return [pscustomobject]@{ MayRun = $true; Kind = "stale"; Holder = $holder; Since = $since }
    }
    if ($holder -and $holder.ToUpperInvariant() -eq $Machine.ToUpperInvariant()) {
        return [pscustomobject]@{ MayRun = $false; Kind = "ours"; Holder = $holder; Since = $since }
    }
    return [pscustomobject]@{ MayRun = $false; Kind = "held"; Holder = $holder; Since = $since }
}

function New-TeamLock {
    param(
        [Parameter(Mandatory = $true)][string]$Machine,
        [Parameter(Mandatory = $true)][string]$CycleId,
        [datetime]$Now = [datetime]::UtcNow
    )
    return [pscustomobject]@{
        held        = $true
        machine     = $Machine
        cycle_id    = $CycleId
        pid         = $PID
        acquired_at = (Get-TeamTimestamp -Now $Now)
    }
}

function New-TeamLockReleased { return [pscustomobject]@{ held = $false } }

# ---------------------------------------------------------------------------- names

function Get-TeamBranchName {
    param([Parameter(Mandatory = $true)][string]$CycleId, [Parameter(Mandatory = $true)][string]$Role, [Parameter(Mandatory = $true)][string]$Slug)
    foreach ($part in @($CycleId, $Role, $Slug)) {
        if ($part -cnotmatch '^[a-z0-9][a-z0-9.-]{0,62}$') { throw "'$part' cannot be part of a branch name" }
    }
    return "team/$CycleId/$Role-$Slug"
}

function Get-TeamWorktreePath {
    <# Where a branch's worktree lives: under .claude/worktrees, which git ignores. #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [Parameter(Mandatory = $true)][string]$Branch)
    if ($Branch -cnotmatch '^(team|integrate)/[a-z0-9][a-z0-9./-]{0,120}$' -or $Branch -match '\.\.') {
        throw "'$Branch' is not a team branch"
    }
    return (Join-Path (Join-Path $RepoRoot ".claude\worktrees") ($Branch -replace '/', '\'))
}

function Test-TeamPathInsideArea {
    <# Whether a changed file (repository-relative) is inside one of a task's areas. #>
    param([Parameter(Mandatory = $true)][string]$Path, [string[]]$Area)
    $file = ($Path -replace '\\', '/').TrimStart("/").ToLowerInvariant()
    foreach ($entry in @($Area)) {
        $root = ([string]$entry -replace '\\', '/').TrimEnd("*").TrimEnd("/").ToLowerInvariant()
        if (-not $root) { continue }
        if ($file -eq $root -or $file.StartsWith($root + "/")) { return $true }
    }
    return $false
}

# ---------------------------------------------------- the store on the Cloud Core (pilot-02)
#
# The same queue and lock, served by /v1/team/queue (services/api/app/team/routes.py). The
# rules are the server's: a task is validated on write, a write carries the updated_at the
# writer last read (409 when stale), and the lock is stale after six hours. What this side
# adds is bookkeeping: what each task looked like when it was read, so that only a task that
# CHANGED is written back, and only with the version it was read at.
# services/api/tests/unit/test_team_state.py reads this file's paths and field names.

function New-TeamApiStore {
    <# -TokenFile is a PATH: the token is read from the file, never taken on a command line. #>
    param([Parameter(Mandatory = $true)][string]$Url, [Parameter(Mandatory = $true)][string]$TokenFile)
    if (-not (Test-Path -LiteralPath $TokenFile)) { throw "the queue token file does not exist: $TokenFile" }
    $token = [System.IO.File]::ReadAllText($TokenFile, [System.Text.Encoding]::UTF8).Trim()
    if (-not $token) { throw "the queue token file is empty: $TokenFile" }
    return [pscustomobject]@{
        Base     = $Url.TrimEnd("/")
        Token    = $token
        Baseline = @{}
        # The ids Save-TeamQueueApi -SkipStale was refused (409), AS THEY HAPPEN: a caller that
        # catches a later task's error in the same save still learns of them.
        Refused  = New-Object System.Collections.ArrayList
    }
}

function Invoke-TeamApi {
    <# One call, UTF-8 both ways (Invoke-JsonUtf8, scripts/lib/HttpJson.ps1). #>
    param(
        [Parameter(Mandatory = $true)]$Store,
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Path,
        $Body = $null
    )
    $json = $null
    if ($null -ne $Body) { $json = ConvertTo-Json -InputObject $Body -Depth 12 -Compress }
    return (Invoke-JsonUtf8 -Uri ($Store.Base + $Path) -Method $Method -Headers @{ Authorization = ("Bearer " + $Store.Token) } -Body $json)
}

function Get-TeamQueueApi {
    <# The whole queue as the store has it now. Nothing is noted: a caller that may still refuse
       what it read (a queue that breaks the protocol) keeps the versions it was working from. #>
    param([Parameter(Mandatory = $true)]$Store)
    return (Invoke-TeamApi -Store $Store -Method "GET" -Path "/v1/team/queue")
}

function Set-TeamQueueBaseline {
    <# Notes each task of a queue as it was read: the version a later write is made from. #>
    param([Parameter(Mandatory = $true)]$Store, [Parameter(Mandatory = $true)]$Queue)
    $Store.Baseline.Clear()
    foreach ($task in (Get-TeamTasks -Queue $Queue)) {
        $Store.Baseline[[string]$task.id] = [pscustomobject]@{
            Updated = [string]$task.updated_at
            Json    = (ConvertTo-Json -InputObject $task -Depth 12 -Compress)
        }
    }
}

function Read-TeamQueueApi {
    <# The whole queue, and a note of each task as it was read. #>
    param([Parameter(Mandatory = $true)]$Store)
    $queue = Get-TeamQueueApi -Store $Store
    Set-TeamQueueBaseline -Store $Store -Queue $queue
    return $queue
}

function Save-TeamQueueApi {
    <# Writes back the tasks that changed (or are new), each with the version it was read at.
       A stale write throws: the queue moved under us and the cycle must not overwrite it.
       With -SkipStale a stale write (409) is that ONE task's: it is left as the store has it,
       the other tasks are still written, and the ids that were left are returned - somebody
       else decided about that task while we worked, and their word stands. Without the switch
       nothing is returned. #>
    param([Parameter(Mandatory = $true)]$Store, [Parameter(Mandatory = $true)]$Queue, [switch]$SkipStale)
    $stale = New-Object System.Collections.ArrayList
    foreach ($task in (Get-TeamTasks -Queue $Queue)) {
        $id = [string]$task.id
        $json = ConvertTo-Json -InputObject $task -Depth 12 -Compress
        $known = $Store.Baseline[$id]
        if ($null -ne $known -and $known.Json -ceq $json) { continue }
        $expected = $null
        if ($null -ne $known) { $expected = $known.Updated }
        $body = [ordered]@{ task = $task; expected_updated_at = $expected }
        try { [void](Invoke-TeamApi -Store $Store -Method "PUT" -Path "/v1/team/queue/tasks/$id" -Body $body) }
        catch {
            if ($SkipStale -and ([string]$_.Exception.Message) -match '^HTTP 409 ') {
                [void]$stale.Add($id)
                [void]$Store.Refused.Add($id)
                # Noted as "nothing new to write", so the same refused write is not sent again by
                # every later save of the pass; the version stays the stale one, so a further
                # change of ours to this task is refused as well, until the queue is read again.
                $Store.Baseline[$id] = [pscustomobject]@{ Updated = $expected; Json = $json }
                continue
            }
            throw
        }
        $Store.Baseline[$id] = [pscustomobject]@{ Updated = [string]$task.updated_at; Json = $json }
    }
    if ($SkipStale) { return @($stale.ToArray()) }
}

function Set-TeamTaskWritten {
    <# Notes a task's present content as "nothing new to write" WITHOUT touching its version:
       for a copy the caller put back after the store refused its write. #>
    param([Parameter(Mandatory = $true)]$Store, [Parameter(Mandatory = $true)]$Task)
    $id = [string]$Task.id
    $known = $Store.Baseline[$id]
    $version = if ($null -ne $known) { $known.Updated } else { $null }
    $Store.Baseline[$id] = [pscustomobject]@{ Updated = $version; Json = (ConvertTo-Json -InputObject $Task -Depth 12 -Compress) }
}

function Get-TeamLockApi {
    param([Parameter(Mandatory = $true)]$Store)
    return (Invoke-TeamApi -Store $Store -Method "GET" -Path "/v1/team/queue/lock")
}

function Set-TeamLockApi {
    <# Takes the lock. The answer says acquired, kind (free/stale/ours/dead/held), holder, since. #>
    param(
        [Parameter(Mandatory = $true)]$Store,
        [Parameter(Mandatory = $true)][string]$Machine,
        [Parameter(Mandatory = $true)][string]$CycleId,
        [bool]$TakeoverDead = $false
    )
    $body = [ordered]@{ action = "acquire"; machine = $Machine; cycle_id = $CycleId; pid = $PID; takeover_dead = $TakeoverDead }
    return (Invoke-TeamApi -Store $Store -Method "POST" -Path "/v1/team/queue/lock" -Body $body)
}

function Clear-TeamLockApi {
    param([Parameter(Mandatory = $true)]$Store, [Parameter(Mandatory = $true)][string]$Machine, [Parameter(Mandatory = $true)][string]$CycleId)
    $body = [ordered]@{ action = "release"; machine = $Machine; cycle_id = $CycleId }
    [void](Invoke-TeamApi -Store $Store -Method "POST" -Path "/v1/team/queue/lock" -Body $body)
}

function Send-TeamReportApi {
    <# The report as text, so the Onay Merkezi on the Cloud Core can show it. #>
    param([Parameter(Mandatory = $true)]$Store, [Parameter(Mandatory = $true)][string]$Name, [Parameter(Mandatory = $true)][string]$Text)
    [void](Invoke-TeamApi -Store $Store -Method "POST" -Path "/v1/team/queue/reports" -Body ([ordered]@{ name = $Name; text = $Text }))
}

function Send-TeamProposalApi {
    <# One file of team/proposals/ as text, so the Onay Merkezi's "Detay" has it (ADR-0236). #>
    param([Parameter(Mandatory = $true)]$Store, [Parameter(Mandatory = $true)][string]$Name, [Parameter(Mandatory = $true)][string]$Text)
    [void](Invoke-TeamApi -Store $Store -Method "POST" -Path "/v1/team/queue/proposals" -Body ([ordered]@{ name = $Name; text = $Text }))
}

function Get-TeamModelsApi {
    <# The model setting from the team store (ADR-0214 addendum 7). $null when this Cloud Core
       does not have the route yet (404): the caller then reads the local file, then the defaults. #>
    param([Parameter(Mandatory = $true)]$Store)
    try { return (Invoke-TeamApi -Store $Store -Method "GET" -Path "/v1/team/queue/models") }
    catch {
        if ($_.Exception.Message -match '^HTTP 404 ') { return $null }
        throw
    }
}

function Save-TeamStatusApi {
    <# The cycle's live status (office-cycle-status): the Cloud Core keeps the latest document. #>
    param([Parameter(Mandatory = $true)]$Store, [Parameter(Mandatory = $true)]$Status)
    [void](Invoke-TeamApi -Store $Store -Method "PUT" -Path "/v1/team/queue/status" -Body $Status)
}
