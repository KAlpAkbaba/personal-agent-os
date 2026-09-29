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

    Windows PowerShell 5.1, StrictMode. Nothing here reads a secret or the network.
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
    param([string]$StdOut, [int]$ExitCode = 0)
    $text = ""
    $cost = 0.0
    $ok = $false
    $why = ""
    try {
        $document = ConvertFrom-Json -InputObject ([string]$StdOut)
        $text = [string](Get-TeamProperty -InputObject $document -Name "result" -Default "")
        $cost = [double](Get-TeamProperty -InputObject $document -Name "total_cost_usd" -Default 0)
        $isError = [bool](Get-TeamProperty -InputObject $document -Name "is_error" -Default $false)
        $subtype = [string](Get-TeamProperty -InputObject $document -Name "subtype" -Default "")
        $ok = ($ExitCode -eq 0) -and (-not $isError) -and ($text.Trim().Length -gt 0)
        if (-not $ok) { $why = if ($subtype) { $subtype } else { "exit $ExitCode" } }
    }
    catch {
        $why = "the run printed no result document (exit $ExitCode)"
    }
    return [pscustomobject]@{ Ok = $ok; Text = $text; CostUsd = $cost; Why = $why }
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
