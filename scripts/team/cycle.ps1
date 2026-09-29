<#
.SYNOPSIS
    One cycle of the agent team (docs/TEAM_PROTOCOL.md section 3).

.DESCRIPTION
    Reads `team/queue.json`, takes the lock, and for every task that is not at one of the
    owner's three gates starts the run its state calls for: a FRESH `claude -p` process with
    the role file as its system prompt and the task card as its prompt. The run's raw output
    goes to `team/reports/<cycle-id>/`; at most forty lines of its report go into the queue.

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
    The cycle's money cap. No run is started once it is reached.

.PARAMETER RunMaxUsd
    One run's money cap (a task's own budget.max_usd, when lower, wins).

.PARAMETER ClaudePrefixArguments
    Arguments placed before the ones this script builds. The tests use it to put a fake
    in place of the model: -ClaudePath powershell.exe -ClaudePrefixArguments -File,fake.ps1

.EXAMPLE
    .\scripts\team\cycle.ps1 -CycleId pilot-01 -MaxUsd 15 -Research
#>
[CmdletBinding()]
param(
    [string]$CycleId = "",
    [string]$TeamRoot = "",
    [int]$MaxParallel = 2,
    [double]$MaxUsd = 20,
    [double]$RunMaxUsd = 5,
    [double]$RunMinutes = 45,
    [int]$CycleMinutes = 240,
    [int]$MaxRunsPerTask = 4,
    [string]$ClaudePath = (Join-Path $env:USERPROFILE ".local\bin\claude.exe"),
    [string[]]$ClaudePrefixArguments = @(),
    [string]$Model = "",
    [string]$Machine = $env:COMPUTERNAME,
    [string]$Base = "main",
    [switch]$Research,
    # What the lead asks the researcher to study, one line per subject. It is placed in the
    # researcher's prompt under a heading of its own; the role file stays the role.
    [string[]]$ResearchBrief = @(),
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")

if (-not $TeamRoot) { $TeamRoot = Join-Path $repoRoot "team" }
if (-not $CycleId) { $CycleId = "c" + (Get-Date).ToString("yyyyMMdd-HHmm") }
if ($CycleId -cnotmatch '^[a-z0-9][a-z0-9.-]{0,40}$') { throw "a cycle id is lower-case letters, digits, '.' and '-': '$CycleId'" }
if ($MaxParallel -lt 1) { throw "-MaxParallel is at least 1" }

$queuePath = Join-Path $TeamRoot "queue.json"
$lockPath = Join-Path $TeamRoot "lock.json"
$reportsRoot = Join-Path $TeamRoot "reports"
$cycleDir = Join-Path $reportsRoot $CycleId
$agentsRoot = Join-Path $repoRoot ".claude\agents"

$queue = Read-TeamJson -Path $queuePath
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
    return $path
}

# ------------------------------------------------------------------ the lock

$lock = $null
if (Test-Path -LiteralPath $lockPath) { $lock = Read-TeamJson -Path $lockPath }
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

Write-TeamJson -Path $lockPath -Document (New-TeamLock -Machine $Machine -CycleId $CycleId -Now $started)

try {
    if (-not (Test-Path -LiteralPath $cycleDir)) { [void](New-Item -ItemType Directory -Force -Path $cycleDir) }
    $runCount = @{}

    function Test-CapReached {
        if ($script:cycle.spent_usd -ge $MaxUsd) {
            Add-CycleNote -List "stops" -Text ("bütçe tavanı: {0:0.00} / {1:0.00} USD" -f $script:cycle.spent_usd, $MaxUsd)
            return $true
        }
        if (([datetime]::UtcNow - $started).TotalMinutes -ge $CycleMinutes) {
            Add-CycleNote -List "stops" -Text "süre tavanı: $CycleMinutes dakika"
            return $true
        }
        return $false
    }

    function Start-RoleRun {
        param($Task, [string]$Role, [string]$WorkingDirectory)
        $roleFile = Join-Path $agentsRoot "$Role.md"
        if (-not (Test-Path -LiteralPath $roleFile)) { throw "there is no role file for '$Role': $roleFile" }
        $cap = $RunMaxUsd
        if ($null -ne $Task) {
            $own = [double](Get-TeamProperty -InputObject (Get-TeamProperty -InputObject $Task -Name "budget") -Name "max_usd" -Default 0)
            if ($own -gt 0 -and $own -lt $cap) { $cap = $own }
        }
        $left = $MaxUsd - $script:cycle.spent_usd
        if ($left -lt $cap) { $cap = [Math]::Max(0.01, $left) }
        $arguments = Get-TeamRunArguments -RoleFile $roleFile -MaxUsd $cap -Model $Model -PrefixArguments $ClaudePrefixArguments
        if ($null -ne $Task) { $prompt = New-TeamTaskCard -Task $Task -Role $Role -CycleId $CycleId }
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
        return [pscustomobject]@{ Task = $Task; Role = $Role; Run = $run; Deadline = ([datetime]::UtcNow.AddMinutes($RunMinutes)) }
    }

    function Complete-RoleRun {
        param($Started)
        $finished = Wait-TeamRun -Run $Started.Run -Deadline $Started.Deadline
        $result = Read-TeamRunResult -StdOut $finished.StdOut -ExitCode $finished.ExitCode
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
        }
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
    if ($Research -and -not (Test-CapReached)) {
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
        Write-TeamJson -Path $queuePath -Document $queue
    }

    # ---------------------------------------------------------------- the tasks
    $capped = $false
    while (-not $capped) {
        $runnable = New-Object System.Collections.ArrayList
        $moved = $false
        foreach ($task in (Get-TeamTasks -Queue $queue)) {
            $next = Get-TeamNextRole -Task $task
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
        if ($moved) { Write-TeamJson -Path $queuePath -Document $queue }
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
        Write-TeamJson -Path $queuePath -Document $queue

        foreach ($startedRun in $startedRuns) {
            $task = $startedRun.Task
            $role = [string]$startedRun.Role
            $done = Complete-RoleRun -Started $startedRun
            Add-TaskReport -Task $task -Role $role -Done $done

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
        Write-TeamJson -Path $queuePath -Document $queue
    }

    $merged = @(Get-TeamTasks -Queue $queue | Where-Object { $_.state -eq "merged" })
    if (@($merged).Count -gt 0) {
        Add-CycleNote -List "gaps" -Text "integrate/$CycleId üzerinde tam kapı ve main'e birleştirme bu betikte yok; lead yapar, sonra işler 'awaiting_release' olur"
    }
    $path = Save-Report
    Write-Host "cycle $CycleId ended; report: $path"
}
finally {
    Write-TeamJson -Path $lockPath -Document (New-TeamLockReleased)
}
exit 0
