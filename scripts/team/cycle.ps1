<#
.SYNOPSIS
    One cycle of the agent team (docs/TEAM_PROTOCOL.md section 3).

.DESCRIPTION
    Reads `team/queue.json`, takes the lock, and for every task that is not at one of the
    owner's three gates starts the run its state calls for: a FRESH `claude -p` process with
    the role file as its system prompt and the task card as its prompt. The run's raw output
    goes to `team/reports/<cycle-id>/`; at most forty lines of its report go into the queue.

    After the researcher step, a proposal that serves a roadmap row and has no area yet is
    split into tasks: one fresh `lead` run (Read and Write only) writes
    `team/plans/<cycle>-split-<id>.json`, and THIS script validates it (Test-TeamSplit) and
    queues the tasks as `approved`, or refuses the whole split and says why in the report.

    It never waits for a human. It ends when nothing in the queue can run - every task is at
    a gate, done or stopped - or when a cap is reached, and it writes
    `team/reports/<cycle-id>.md` in Turkish either way.

    What it does NOT do, on purpose:
      * it does not run the quality gate and does not merge to main. Approved branches are
        merged into `integrate/<cycle-id>`; the gate on that branch and the merge to main
        are the lead's;
      * it does not release, register a scheduled task, push, or touch main's checkout
        beyond the files under `team/`;
      * it does not approve anything. A task at a gate moves when the OWNER changes its
        state.

    Idempotent: a branch, a worktree or a merge that exists is used as it is, and a task
    a killed run left `in_progress` is taken up again.

.PARAMETER MaxUsd
    The cycle's money cap; 0 (the default since the owner's decision of 2026-09-30, ADR-0214
    addendum 3) is NO cap: the subscription has none, and the USD the tool reports is kept
    in the report as an estimate only.

.PARAMETER RunMaxUsd
    One run's money cap; 0 is no cap (then a task's own budget.max_usd is an estimate too).

.PARAMETER RunMinutes
    One run's time cap in minutes; 0 is no cap. A killed run is harmless: the cycle is
    idempotent and the next one takes the task up again.

.PARAMETER CycleMinutes
    The cycle's time cap in minutes; 0 is no cap.

.PARAMETER WaitForUsageLimit
    The one stop the team has is the subscription's usage limit (Max). When a run hits it
    the task goes back to where it was, and with this switch (the default) the cycle WAITS
    until the limit lifts and carries on from there; without it, or when the tool did not
    say when, the cycle stops and says so in the report - the next cycle with the same
    -CycleId continues where it left off.

.PARAMETER ClaudePrefixArguments
    Arguments placed before the ones this script builds. The tests use it to put a fake
    in place of the model: -ClaudePath powershell.exe -ClaudePrefixArguments -File,fake.ps1

.EXAMPLE
    .\scripts\team\cycle.ps1 -CycleId pilot-01 -Research
#>
[CmdletBinding()]
param(
    [string]$CycleId = "",
    [string]$TeamRoot = "",
    [int]$MaxParallel = 2,
    [double]$MaxUsd = 0,
    [double]$RunMaxUsd = 0,
    [double]$RunMinutes = 0,
    [int]$CycleMinutes = 0,
    [int]$MaxRunsPerTask = 4,
    [bool]$WaitForUsageLimit = $true,
    [string]$ClaudePath = (Join-Path $env:USERPROFILE ".local\bin\claude.exe"),
    [string[]]$ClaudePrefixArguments = @(),
    [string]$Model = "",
    [string]$Machine = $env:COMPUTERNAME,
    [string]$Base = "main",
    [switch]$Research,
    # What the lead asks the researcher to study, one line per subject. It is placed in the
    # researcher's prompt under a heading of its own; the role file stays the role.
    [string[]]$ResearchBrief = @(),
    # The first step of a cycle by itself: the researcher writes its proposals, they are
    # queued for the owner, and NO task is run or moved - not even an approved one.
    [switch]$ResearchOnly,
    [switch]$DryRun,
    # The Cloud Core's queue (pilot-02): with -QueueUrl the queue, the lock and the report go
    # through /v1/team/queue there, so an approval can be given with this PC off and the other
    # PC sees the same queue. Without it, the files under -TeamRoot, as before.
    [string]$QueueUrl = "",
    # A PATH to a file the owner wrote holding the owner-session token. A token is never a
    # parameter on the command line: it would sit in the process list.
    [string]$QueueToken = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")

if (-not $TeamRoot) { $TeamRoot = Join-Path $repoRoot "team" }
if (-not $CycleId) { $CycleId = "c" + (Get-Date).ToString("yyyyMMdd-HHmm") }
if ($CycleId -cnotmatch '^[a-z0-9][a-z0-9.-]{0,40}$') { throw "a cycle id is lower-case letters, digits, '.' and '-': '$CycleId'" }
if ($MaxParallel -lt 1) { throw "-MaxParallel is at least 1" }
# A parameter is never assigned over (provision.tests.ps1 holds every script to it).
$runResearch = [bool]$Research -or [bool]$ResearchOnly

$queuePath = Join-Path $TeamRoot "queue.json"
$lockPath = Join-Path $TeamRoot "lock.json"
$reportsRoot = Join-Path $TeamRoot "reports"
$cycleDir = Join-Path $reportsRoot $CycleId
$agentsRoot = Join-Path $repoRoot ".claude\agents"

$useApi = [bool]$QueueUrl
if ($useApi -and -not $QueueToken) { throw "-QueueUrl needs -QueueToken: the path of a file holding the token" }
$apiStore = $null
if ($useApi) { $apiStore = New-TeamApiStore -Url $QueueUrl -TokenFile $QueueToken }

function Save-Queue {
    param($Document)
    if ($useApi) { Save-TeamQueueApi -Store $apiStore -Queue $Document }
    else { Write-TeamJson -Path $queuePath -Document $Document }
}

$queue = if ($useApi) { Read-TeamQueueApi -Store $apiStore } else { Read-TeamJson -Path $queuePath }
$problems = @(Test-TeamQueue -Queue $queue)
if (@($problems).Count -gt 0) {
    Write-Host "the queue breaks the protocol; nothing was run:"
    foreach ($problem in $problems) { Write-Host "  - $problem" }
    exit 2
}

$started = [datetime]::UtcNow
$cycle = [pscustomobject]@{
    cycle_id   = $CycleId
    machine    = $Machine
    started_at = (Get-TeamTimestamp -Now $started)
    ended_at   = ""
    max_usd    = $MaxUsd
    spent_usd  = 0.0
    conflicts  = 0
    returned   = 0
    runs       = @()
    stops      = @()
    risks      = @()
    gaps       = @()
}

function Add-CycleNote {
    param([string]$List, [string]$Text)
    $script:cycle.$List = @(@($script:cycle.$List) + $Text)
}

function Save-Report {
    $script:cycle.ended_at = (Get-TeamTimestamp)
    if (-not (Test-Path -LiteralPath $reportsRoot)) { [void](New-Item -ItemType Directory -Force -Path $reportsRoot) }
    $text = New-TeamCycleReport -CycleId $CycleId -Queue $script:queue -Cycle $script:cycle
    $path = Join-Path $reportsRoot "$CycleId.md"
    [System.IO.File]::WriteAllText($path, $text + "`n", (New-Object System.Text.UTF8Encoding($false)))
    if ($useApi) {
        # The file above is the report; the store keeps its text so the Onay Merkezi on the Cloud
        # Core can show it. A post that fails does not lose the report.
        try { Send-TeamReportApi -Store $apiStore -Name "$CycleId.md" -Text $text }
        catch { Write-Host "the report was not posted to the queue store: $($_.Exception.Message)" }
    }
    return $path
}

# ------------------------------------------------------------------ the lock

$lock = $null
if ($useApi) { $lock = Get-TeamLockApi -Store $apiStore }
elseif (Test-Path -LiteralPath $lockPath) { $lock = Read-TeamJson -Path $lockPath }
$decision = Get-TeamLockDecision -Lock $lock -Machine $Machine -Now $started
if ($decision.Kind -eq "ours") {
    # Ours, and fresh. If the process that took it is gone, the run died and the lock with it.
    $holderPid = [int](Get-TeamProperty -InputObject $lock -Name "pid" -Default 0)
    $alive = $false
    if ($holderPid -gt 0) { $alive = ($null -ne (Get-Process -Id $holderPid -ErrorAction SilentlyContinue)) }
    if (-not $alive) {
        $decision = [pscustomobject]@{ MayRun = $true; Kind = "dead"; Holder = $decision.Holder; Since = $decision.Since }
    }
}
if (-not $decision.MayRun) {
    Add-CycleNote -List "stops" -Text "kilit $($decision.Holder) makinesinde ($($decision.Since)); bu döngü hiçbir şey çalıştırmadı"
    $path = Save-Report
    Write-Host "the lock is held by $($decision.Holder) since $($decision.Since); report: $path"
    exit 3
}
if ($decision.Kind -eq "stale") {
    Add-CycleNote -List "risks" -Text "bayat kilit devralındı: $($decision.Holder), $($decision.Since)"
}
if ($decision.Kind -eq "dead") {
    Add-CycleNote -List "risks" -Text "bu makinenin ölmüş bir koşusunun kilidi devralındı ($($decision.Since))"
}

if ($DryRun) {
    foreach ($task in (Get-TeamTasks -Queue $queue)) {
        $next = Get-TeamNextRole -Task $task
        Write-Host ("{0,-40} {1,-22} -> {2} {3}{4}" -f $task.id, $task.state, $next.Kind, $next.Role, $next.Gate)
    }
    exit 0
}

if ($useApi) {
    $taken = Set-TeamLockApi -Store $apiStore -Machine $Machine -CycleId $CycleId -TakeoverDead ($decision.Kind -eq "dead")
    if (-not [bool]$taken.acquired) {
        # The other machine took it between our read and our write.
        Add-CycleNote -List "stops" -Text "kilit $($taken.holder) makinesinde ($($taken.since)); bu döngü hiçbir şey çalıştırmadı"
        $path = Save-Report
        Write-Host "the lock was taken by $($taken.holder); report: $path"
        exit 3
    }
}
else { Write-TeamJson -Path $lockPath -Document (New-TeamLock -Machine $Machine -CycleId $CycleId -Now $started) }

try {
    if (-not (Test-Path -LiteralPath $cycleDir)) { [void](New-Item -ItemType Directory -Force -Path $cycleDir) }
    $runCount = @{}

    function Test-CapReached {
        # A cap of 0 is no cap (owner decision 2026-09-30).
        if ($MaxUsd -gt 0 -and $script:cycle.spent_usd -ge $MaxUsd) {
            Add-CycleNote -List "stops" -Text ("bütçe tavanı: {0:0.00} / {1:0.00} USD" -f $script:cycle.spent_usd, $MaxUsd)
            return $true
        }
        if ($CycleMinutes -gt 0 -and ([datetime]::UtcNow - $started).TotalMinutes -ge $CycleMinutes) {
            Add-CycleNote -List "stops" -Text "süre tavanı: $CycleMinutes dakika"
            return $true
        }
        return $false
    }

    function Start-RoleRun {
        param($Task, [string]$Role, [string]$WorkingDirectory, [string]$Prompt = "", [string[]]$ExcludeTools = @())
        $roleFile = Join-Path $agentsRoot "$Role.md"
        if (-not (Test-Path -LiteralPath $roleFile)) { throw "there is no role file for '$Role': $roleFile" }
        # A run cap of 0 is no cap at all: then the task's budget.max_usd is an estimate for
        # the report, and the tool gets no --max-budget-usd (owner decision 2026-09-30).
        $cap = $RunMaxUsd
        if ($cap -gt 0) {
            if ($null -ne $Task) {
                $own = [double](Get-TeamProperty -InputObject (Get-TeamProperty -InputObject $Task -Name "budget") -Name "max_usd" -Default 0)
                if ($own -gt 0 -and $own -lt $cap) { $cap = $own }
            }
            if ($MaxUsd -gt 0) {
                $left = $MaxUsd - $script:cycle.spent_usd
                if ($left -lt $cap) { $cap = [Math]::Max(0.01, $left) }
            }
        }
        $arguments = Get-TeamRunArguments -RoleFile $roleFile -MaxUsd $cap -Model $Model -PrefixArguments $ClaudePrefixArguments -ExcludeTools $ExcludeTools
        if ($Prompt) { $prompt = $Prompt }
        elseif ($null -ne $Task) { $prompt = New-TeamTaskCard -Task $Task -Role $Role -CycleId $CycleId }
        else {
            $prompt = "# Run ($Role, cycle $CycleId)`n`nWork as your role file says. Write your proposals under team/proposals/. " +
            "Return your report as your final message, at most 40 lines, naming each file you wrote."
            $subjects = @($ResearchBrief | Where-Object { ([string]$_).Trim() })
            if (@($subjects).Count -gt 0) {
                $prompt += "`n`n## The subjects the lead asks for (one proposal each, at most three)`n"
                foreach ($subject in $subjects) { $prompt += "`n- " + ([string]$subject).Trim() }
            }
        }
        $run = Start-TeamRun -FilePath $ClaudePath -Arguments $arguments -Prompt $prompt -WorkingDirectory $WorkingDirectory
        $deadline = if ($RunMinutes -gt 0) { [datetime]::UtcNow.AddMinutes($RunMinutes) } else { [datetime]::MaxValue }
        return [pscustomobject]@{ Task = $Task; Role = $Role; Run = $run; Deadline = $deadline }
    }

    function Complete-RoleRun {
        param($Started)
        $finished = Wait-TeamRun -Run $Started.Run -Deadline $Started.Deadline
        $result = Read-TeamRunResult -StdOut $finished.StdOut -ExitCode $finished.ExitCode -StdErr $finished.StdErr
        $taskId = if ($null -ne $Started.Task) { [string]$Started.Task.id } else { "cycle" }
        $number = 1
        while (Test-Path -LiteralPath (Join-Path $cycleDir "$taskId-$($Started.Role)-$number.json")) { $number++ }
        $stem = Join-Path $cycleDir "$taskId-$($Started.Role)-$number"
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText("$stem.json", [string]$finished.StdOut, $utf8)
        if ($finished.StdErr.Trim()) { [System.IO.File]::WriteAllText("$stem.stderr.txt", [string]$finished.StdErr, $utf8) }
        if ($result.Text) { [System.IO.File]::WriteAllText("$stem.md", $result.Text.TrimEnd() + "`n", $utf8) }

        $outcome = if ($finished.TimedOut) { "süre doldu ($RunMinutes dk)" } elseif ($result.Ok) { "tamam" } else { "başarısız: $($result.Why)" }
        $script:cycle.spent_usd = [double]$script:cycle.spent_usd + [double]$result.CostUsd
        $script:cycle.runs = @(@($script:cycle.runs) + [pscustomobject]@{
                task = $taskId; role = $Started.Role; cost_usd = $result.CostUsd; seconds = $finished.Seconds; outcome = $outcome
            })
        $relative = "team/reports/$CycleId/$taskId-$($Started.Role)-$number"
        return [pscustomobject]@{
            Ok = ($result.Ok -and -not $finished.TimedOut); Text = $result.Text; Outcome = $outcome
            File = $(if ($result.Text) { "$relative.md" } else { "$relative.json" }); CostUsd = $result.CostUsd
            UsageLimited = [bool]$result.UsageLimited; ResetsAt = [string]$result.ResetsAt
        }
    }

    function Wait-UsageLimit {
        <# The subscription's limit was hit. True when the cycle may go on (it waited it
           out); false when it must stop here and the next cycle continues. #>
        param([string]$ResetsAt)
        if (-not $WaitForUsageLimit -or -not $ResetsAt) {
            Add-CycleNote -List "stops" -Text ("Max kullanım limiti; " + $(if ($ResetsAt) { "sıfırlanma $ResetsAt; " } else { "ne zaman açılacağı söylenmedi; " }) +
                "limit açılınca aynı -CycleId ile yeniden başlat: kaldığı yerden devam eder")
            return $false
        }
        $until = ([datetime]::Parse($ResetsAt, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AdjustToUniversal)).AddSeconds(90)
        $wait = $until - [datetime]::UtcNow
        Add-CycleNote -List "risks" -Text ("Max kullanım limiti: {0} sıfırlanmasına kadar beklendi ({1:0} dk)" -f $ResetsAt, [Math]::Max(0, $wait.TotalMinutes))
        if ($wait.TotalSeconds -gt 0) { Start-Sleep -Seconds ([int][Math]::Min([int]::MaxValue, $wait.TotalSeconds)) }
        return $true
    }

    function Add-TaskReport {
        param($Task, [string]$Role, $Done)
        $entry = [pscustomobject]@{
            cycle    = $CycleId
            role     = $Role
            at       = (Get-TeamTimestamp)
            file     = $Done.File
            cost_usd = $Done.CostUsd
            outcome  = $Done.Outcome
            summary  = @(Get-TeamSummary -Text $Done.Text)
        }
        Set-TeamProperty -InputObject $Task -Name "reports" -Value @(@(Get-TeamProperty -InputObject $Task -Name "reports" -Default @()) + $entry)
        Set-TeamProperty -InputObject $Task -Name "updated_at" -Value (Get-TeamTimestamp)
    }

    function Stop-Task {
        param($Task, [string]$Reason)
        Set-TeamProperty -InputObject $Task -Name "state" -Value "stopped"
        Set-TeamProperty -InputObject $Task -Name "reason" -Value $Reason
        Set-TeamProperty -InputObject $Task -Name "updated_at" -Value (Get-TeamTimestamp)
    }

    # ---------------------------------------------------------------- the researcher
    if ($runResearch -and -not (Test-CapReached)) {
        $proposals = Join-Path $TeamRoot "proposals"
        if (-not (Test-Path -LiteralPath $proposals)) { [void](New-Item -ItemType Directory -Force -Path $proposals) }
        $done = Complete-RoleRun -Started (Start-RoleRun -Task $null -Role "researcher" -WorkingDirectory $repoRoot)
        if (-not $done.Ok) { Add-CycleNote -List "stops" -Text "araştırmacı: $($done.Outcome)" }
        $known = @(Get-TeamTasks -Queue $queue | ForEach-Object { [string](Get-TeamProperty -InputObject $_ -Name "proposal" -Default "") })
        foreach ($file in @(Get-ChildItem -LiteralPath $proposals -Filter *.md -File | Sort-Object -Property Name)) {
            $relative = "team/proposals/$($file.Name)"
            if ($known -contains $relative) { continue }
            $first = @(Get-Content -LiteralPath $file.FullName -Encoding UTF8 -TotalCount 5 | Where-Object { $_ -match '\S' })
            $title = if (@($first).Count -gt 0) { ([string]$first[0]).TrimStart("#", " ").Trim() } else { $file.BaseName }
            $id = ("idea-" + ($file.BaseName.ToLowerInvariant() -replace '[^a-z0-9-]', '-')).Trim("-")
            if ($id.Length -gt 64) { $id = $id.Substring(0, 64).Trim("-") }
            $now = Get-TeamTimestamp
            $task = [pscustomobject]@{
                id = $id; title = $title; roadmap_row = ""; state = "awaiting_owner"; area = @(); branch = ""
                worktree = ""; assignee = "researcher"; reports = @(); budget = [pscustomobject]@{ max_usd = $RunMaxUsd }
                created_at = $now; updated_at = $now; proposal = $relative
            }
            Set-TeamProperty -InputObject $queue -Name "tasks" -Value @(@(Get-TeamTasks -Queue $queue) + $task)
        }
        Save-Queue -Document $queue
    }

    # ---------------------------------------------------------------- the lead's split
    # A proposal that serves a roadmap row is approved in advance (TEAM_PROTOCOL 3a) but has no
    # area yet. ONE fresh lead run per proposal writes the split; THIS script judges it
    # (Test-TeamSplit) and takes it whole or refuses it whole. A refused split leaves the
    # proposal where it was; the next cycle asks again.
    if (-not $ResearchOnly) {
        foreach ($proposal in @(Get-TeamSplitCandidates -Queue $queue)) {
            if (Test-CapReached) { break }
            $proposalId = [string]$proposal.id
            $splitRelative = "team/plans/$CycleId-split-$proposalId.json"
            $splitPath = Join-Path $repoRoot ($splitRelative -replace '/', '\')
            # A file left by an earlier run is not this run's answer.
            if (Test-Path -LiteralPath $splitPath) { Remove-Item -LiteralPath $splitPath -Force }
            $splitFolder = Split-Path -Parent $splitPath
            if (-not (Test-Path -LiteralPath $splitFolder)) { [void](New-Item -ItemType Directory -Force -Path $splitFolder) }
            $card = New-TeamSplitCard -Task $proposal -Queue $queue -CycleId $CycleId -SplitFile $splitRelative
            $done = Complete-RoleRun -Started (Start-RoleRun -Task $proposal -Role "lead" -WorkingDirectory $repoRoot -Prompt $card -ExcludeTools @("Bash", "Edit"))
            if ($done.UsageLimited -and (Wait-UsageLimit -ResetsAt $done.ResetsAt)) {
                $done = Complete-RoleRun -Started (Start-RoleRun -Task $proposal -Role "lead" -WorkingDirectory $repoRoot -Prompt $card -ExcludeTools @("Bash", "Edit"))
            }
            if (-not $done.Ok) {
                Add-CycleNote -List "risks" -Text "bölme koşusu: ${proposalId}: $($done.Outcome)"
                continue
            }
            $read = Read-TeamSplitFile -Path $splitPath
            $why = @()
            if (-not $read.Ok) { $why = @($read.Why) }
            else { $why = @(Test-TeamSplit -Split $read.Split -Queue $queue) }
            if (@($why).Count -eq 0) {
                $made = @(ConvertTo-TeamSplitTasks -Split $read.Split -Proposal $proposal -MaxUsd $RunMaxUsd)
                $trial = [pscustomobject]@{ version = 1; tasks = @(@(Get-TeamTasks -Queue $queue) + $made) }
                $why = @(Test-TeamQueue -Queue $trial)
                if (@($why).Count -eq 0) {
                    Set-TeamProperty -InputObject $queue -Name "tasks" -Value @(@(Get-TeamTasks -Queue $queue) + $made)
                    Set-TeamProperty -InputObject $proposal -Name "state" -Value "done"
                    Set-TeamProperty -InputObject $proposal -Name "reason" -Value ("bölündü: " + ((@($made) | ForEach-Object { [string]$_.id }) -join ", "))
                    Set-TeamProperty -InputObject $proposal -Name "updated_at" -Value (Get-TeamTimestamp)
                    Save-Queue -Document $queue
                    continue
                }
            }
            Add-CycleNote -List "risks" -Text ("bölme reddedildi: ${proposalId}: " + ((@($why) | ForEach-Object { ([string]$_) -replace '\s+', ' ' }) -join "; "))
        }
    }

    # ---------------------------------------------------------------- the tasks
    $capped = [bool]$ResearchOnly
    while (-not $capped) {
        $runnable = New-Object System.Collections.ArrayList
        $moved = $false
        foreach ($task in (Get-TeamTasks -Queue $queue)) {
            $next = Get-TeamNextRole -Task $task
            if ($next.Kind -ne "rest" -and $next.Kind -ne "gate") {
                # A task whose dependencies are not on main yet waits, and says so once.
                $unmet = @(Get-TeamUnmetDependencies -Task $task -Queue $queue)
                if (@($unmet).Count -gt 0) {
                    $waitNote = "bekliyor: $($task.id) -> $($unmet -join ', ') main'e girince"
                    if (@($script:cycle.gaps) -notcontains $waitNote) { Add-CycleNote -List "gaps" -Text $waitNote }
                    continue
                }
            }
            if ($next.Kind -eq "move") {
                Set-TeamProperty -InputObject $task -Name "state" -Value $next.NextState
                Set-TeamProperty -InputObject $task -Name "updated_at" -Value (Get-TeamTimestamp)
                $moved = $true
                continue
            }
            if ($next.Kind -ne "run") { continue }
            $id = [string]$task.id
            if (-not $runCount.ContainsKey($id)) { $runCount[$id] = 0 }
            if ($runCount[$id] -ge $MaxRunsPerTask) {
                Stop-Task -Task $task -Reason "bu döngüde $MaxRunsPerTask koşu yapıldı ve iş bitmedi"
                $moved = $true
                continue
            }
            [void]$runnable.Add([pscustomobject]@{ Task = $task; Next = $next })
        }
        if ($moved) { Save-Queue -Document $queue }
        if (@($runnable).Count -eq 0) {
            if ($moved) { continue }
            break
        }
        if (Test-CapReached) { $capped = $true; break }

        $batch = @($runnable | Select-Object -First $MaxParallel)
        $startedRuns = New-Object System.Collections.ArrayList
        foreach ($item in $batch) {
            $task = $item.Task
            $role = [string]$item.Next.Role
            $runCount[[string]$task.id] = $runCount[[string]$task.id] + 1
            $where = $repoRoot
            try {
                if ($role -eq "worker" -or $role -eq "inspector") {
                    $branch = [string](Get-TeamProperty -InputObject $task -Name "branch" -Default "")
                    if (-not $branch) {
                        $slug = ([string]$task.id)
                        $branch = Get-TeamBranchName -CycleId $CycleId -Role "worker" -Slug $slug
                        Set-TeamProperty -InputObject $task -Name "branch" -Value $branch
                    }
                    $tree = New-TeamWorktree -RepoRoot $repoRoot -Branch $branch -Base $Base
                    Set-TeamProperty -InputObject $task -Name "worktree" -Value (".claude/worktrees/" + $branch)
                    $where = $tree.Path
                }
                if ($role -eq "worker") { Set-TeamProperty -InputObject $task -Name "state" -Value "in_progress" }
                Set-TeamProperty -InputObject $task -Name "assignee" -Value $role
                [void]$startedRuns.Add((Start-RoleRun -Task $task -Role $role -WorkingDirectory $where))
            }
            catch {
                Stop-Task -Task $task -Reason "koşu başlatılamadı: $($_.Exception.Message)"
            }
        }
        Save-Queue -Document $queue

        $limitHit = ""
        $limitSeen = $false
        foreach ($startedRun in $startedRuns) {
            $task = $startedRun.Task
            $role = [string]$startedRun.Role
            $done = Complete-RoleRun -Started $startedRun
            Add-TaskReport -Task $task -Role $role -Done $done

            if ($done.UsageLimited) {
                # Not the task's failure and not a try spent: it goes back to where it was
                # and is taken up again when the limit lifts (owner decision 2026-09-30).
                $limitSeen = $true
                if ($done.ResetsAt) { $limitHit = $done.ResetsAt }
                $runCount[[string]$task.id] = [Math]::Max(0, $runCount[[string]$task.id] - 1)
                if ($role -eq "worker") { Set-TeamProperty -InputObject $task -Name "state" -Value "assigned" }
                Set-TeamProperty -InputObject $task -Name "updated_at" -Value (Get-TeamTimestamp)
                continue
            }
            if (-not $done.Ok) {
                $failures = [int](Get-TeamProperty -InputObject $task -Name "failed_runs" -Default 0) + 1
                Set-TeamProperty -InputObject $task -Name "failed_runs" -Value $failures
                if ($failures -ge 2) { Stop-Task -Task $task -Reason "iki koşu sonuç vermedi ($($done.Outcome))" }
                elseif ($role -eq "worker") { Set-TeamProperty -InputObject $task -Name "state" -Value "assigned" }
                continue
            }
            switch ($role) {
                "integrator" {
                    $plan = "team/plans/$($task.id)-integration.md"
                    if (Test-Path -LiteralPath (Join-Path $repoRoot ($plan -replace '/', '\'))) {
                        Set-TeamProperty -InputObject $task -Name "plan" -Value $plan
                        Set-TeamProperty -InputObject $task -Name "state" -Value "assigned"
                    }
                    else { Stop-Task -Task $task -Reason "entegratör plan dosyasını yazmadı ($plan)" }
                }
                "worker" {
                    $branch = [string]$task.branch
                    $outside = @(Get-TeamChangedFiles -RepoRoot $repoRoot -Branch $branch -Base $Base |
                        Where-Object { -not (Test-TeamPathInsideArea -Path $_ -Area @($task.area)) })
                    if (@($outside).Count -gt 0) {
                        # Section 4: a worker never leaves its area. Not the inspector's to find.
                        $script:cycle.returned = [int]$script:cycle.returned + 1
                        $after = Get-TeamStateAfterInspection -Task $task -Verdict "RETURN"
                        Set-TeamProperty -InputObject $task -Name "returns" -Value $after.Returns
                        Set-TeamProperty -InputObject $task -Name "state" -Value $after.State
                        Set-TeamProperty -InputObject $task -Name "reason" -Value ("alan dışı dosya: " + (($outside | Select-Object -First 5) -join ", "))
                    }
                    else {
                        $sha = Invoke-TeamGit -WorkingDirectory $repoRoot -Arguments @("rev-parse", "refs/heads/$branch")
                        if ($sha.Success) { Set-TeamProperty -InputObject $task -Name "sha" -Value $sha.StdOut.Trim() }
                        Set-TeamProperty -InputObject $task -Name "state" -Value "inspecting"
                    }
                }
                "inspector" {
                    $verdict = Get-TeamVerdict -Report $done.Text
                    $after = Get-TeamStateAfterInspection -Task $task -Verdict $verdict.Verdict
                    Set-TeamProperty -InputObject $task -Name "returns" -Value $after.Returns
                    $reason = if ($verdict.Detail) { $verdict.Detail } else { $after.Reason }
                    if ($after.State -eq "merged") {
                        $merge = Merge-TeamBranch -RepoRoot $repoRoot -CycleId $CycleId -Branch ([string]$task.branch) -Base $Base
                        if ($merge.Merged) {
                            Set-TeamProperty -InputObject $task -Name "state" -Value "merged"
                            Set-TeamProperty -InputObject $task -Name "integration_branch" -Value $merge.Integration
                        }
                        else {
                            $script:cycle.conflicts = [int]$script:cycle.conflicts + 1
                            $script:cycle.returned = [int]$script:cycle.returned + 1
                            $back = Get-TeamStateAfterInspection -Task $task -Verdict "RETURN"
                            Set-TeamProperty -InputObject $task -Name "returns" -Value $back.Returns
                            Set-TeamProperty -InputObject $task -Name "state" -Value $back.State
                            Set-TeamProperty -InputObject $task -Name "reason" -Value "entegrasyon dalında çakışma"
                        }
                    }
                    else {
                        if ($after.State -eq "returned") { $script:cycle.returned = [int]$script:cycle.returned + 1 }
                        Set-TeamProperty -InputObject $task -Name "state" -Value $after.State
                        Set-TeamProperty -InputObject $task -Name "reason" -Value $reason
                    }
                }
            }
            Set-TeamProperty -InputObject $task -Name "updated_at" -Value (Get-TeamTimestamp)
        }
        Save-Queue -Document $queue
        if ($limitSeen -and -not (Wait-UsageLimit -ResetsAt $limitHit)) { $capped = $true }
    }

    $merged = @(Get-TeamTasks -Queue $queue | Where-Object { $_.state -eq "merged" })
    if (@($merged).Count -gt 0) {
        Add-CycleNote -List "gaps" -Text "integrate/$CycleId üzerinde tam kapı ve main'e birleştirme bu betikte yok; lead yapar, sonra işler 'awaiting_release' olur"
    }
    $path = Save-Report
    Write-Host "cycle $CycleId ended; report: $path"
}
finally {
    if ($useApi) {
        try { Clear-TeamLockApi -Store $apiStore -Machine $Machine -CycleId $CycleId }
        catch { Write-Host "the lock was not released in the queue store: $($_.Exception.Message)" }
    }
    else { Write-TeamJson -Path $lockPath -Document (New-TeamLockReleased) }
}
exit 0
