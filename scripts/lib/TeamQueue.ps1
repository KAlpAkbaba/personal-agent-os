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
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Force }
    Move-Item -LiteralPath $temporary -Destination $Path
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
            # Section 4: two concurrent tasks never share an area.
            foreach ($area in $taskAreas) {
                $key = ([string]$area).TrimEnd("/", "*").ToLowerInvariant()
                foreach ($other in @($areas.Keys)) {
                    if ($key -eq $other -or $key.StartsWith($other + "/") -or $other.StartsWith($key + "/")) {
                        [void]$problems.Add("${label}: the area '$area' overlaps the area of $($areas[$other])")
                    }
                }
            }
            foreach ($area in $taskAreas) {
                $areas[([string]$area).TrimEnd("/", "*").ToLowerInvariant()] = $label
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
        if ($text -cmatch '^(APPROVE|RETURN|REJECT)\b\s*[:(-]?\s*(.*?)\)?\s*$') {
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

function Read-TeamRunResult {
    <#
    .SYNOPSIS
        What `claude -p --output-format json` printed: the text, the cost, whether it failed.

    .DESCRIPTION
        Output that is not the JSON document is not trusted to be a report: the run FAILED,
        and its raw output stays in its file.
    #>
    param([string]$StdOut, [int]$ExitCode = 0, [string]$StdErr = "")
    $text = ""
    $cost = 0.0
    $ok = $false
    $why = ""
    $usageLimited = $false
    $resetsAt = ""
    try {
        $document = ConvertFrom-Json -InputObject ([string]$StdOut)
        $text = [string](Get-TeamProperty -InputObject $document -Name "result" -Default "")
        $cost = [double](Get-TeamProperty -InputObject $document -Name "total_cost_usd" -Default 0)
        $isError = [bool](Get-TeamProperty -InputObject $document -Name "is_error" -Default $false)
        $subtype = [string](Get-TeamProperty -InputObject $document -Name "subtype" -Default "")
        $ok = ($ExitCode -eq 0) -and (-not $isError) -and ($text.Trim().Length -gt 0)
        if (-not $ok) {
            # The tool's own words first ("Not logged in"): a run that failed with the
            # subtype 'success' was reported to the owner as 'failed: success'
            # (pilot-01, 2026-09-30). ASCII, one line, bounded: it goes into a report.
            $said = (($text -split "`r?`n")[0] -replace '[^\x20-\x7E]', ' ').Trim()
            if ($said.Length -gt 120) { $said = $said.Substring(0, 120) }
            if ($isError -and $said) { $why = $said }
            elseif ($subtype -and $subtype -ne "success") { $why = $subtype }
            elseif (-not $text.Trim()) { $why = "the run returned an empty report" }
            else { $why = "exit $ExitCode" }
            # What an error printed is not a report, and is not kept as one.
            if ($isError) { $text = "" }
        }
    }
    catch {
        $why = "the run printed no result document (exit $ExitCode)"
    }
    if (-not $ok) {
        # Owner decision 2026-09-30: the ONE stop the team has is the subscription's usage
        # limit. The tool says it in its result ("Claude AI usage limit reached|<epoch>",
        # "You've hit your limit ...") or on stderr; the epoch, when given, is when it lifts.
        $said = ([string]$StdOut) + "`n" + ([string]$StdErr)
        if ($said -match "(?i)usage limit|hit your (usage |rate )?limit|limit reached|out of extra usage") {
            $usageLimited = $true
            $why = "Max kullanım limiti"
            if ($said -match "limit reached\|(\d{10})") {
                $resetsAt = ([DateTimeOffset]::FromUnixTimeSeconds([long]$Matches[1])).UtcDateTime.ToString("yyyy-MM-ddTHH:mm:ssZ")
            }
        }
    }
    return [pscustomobject]@{ Ok = $ok; Text = $text; CostUsd = $cost; Why = $why; UsageLimited = $usageLimited; ResetsAt = $resetsAt }
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
    return [pscustomobject]@{ Base = $Url.TrimEnd("/"); Token = $token; Baseline = @{} }
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

function Read-TeamQueueApi {
    <# The whole queue, and a note of each task as it was read. #>
    param([Parameter(Mandatory = $true)]$Store)
    $queue = Invoke-TeamApi -Store $Store -Method "GET" -Path "/v1/team/queue"
    $Store.Baseline.Clear()
    foreach ($task in (Get-TeamTasks -Queue $queue)) {
        $Store.Baseline[[string]$task.id] = [pscustomobject]@{
            Updated = [string]$task.updated_at
            Json    = (ConvertTo-Json -InputObject $task -Depth 12 -Compress)
        }
    }
    return $queue
}

function Save-TeamQueueApi {
    <# Writes back the tasks that changed (or are new), each with the version it was read at.
       A stale write throws: the queue moved under us and the cycle must not overwrite it. #>
    param([Parameter(Mandatory = $true)]$Store, [Parameter(Mandatory = $true)]$Queue)
    foreach ($task in (Get-TeamTasks -Queue $Queue)) {
        $id = [string]$task.id
        $json = ConvertTo-Json -InputObject $task -Depth 12 -Compress
        $known = $Store.Baseline[$id]
        if ($null -ne $known -and $known.Json -ceq $json) { continue }
        $expected = $null
        if ($null -ne $known) { $expected = $known.Updated }
        $body = [ordered]@{ task = $task; expected_updated_at = $expected }
        [void](Invoke-TeamApi -Store $Store -Method "PUT" -Path "/v1/team/queue/tasks/$id" -Body $body)
        $Store.Baseline[$id] = [pscustomobject]@{ Updated = [string]$task.updated_at; Json = $json }
    }
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
