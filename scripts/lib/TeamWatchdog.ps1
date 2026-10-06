<#
.SYNOPSIS
    The cycle watchdog (card cycle-watchdog) and a git call that cannot leave an orphan.

.DESCRIPTION
    2026-10-06: the cycle (pid 4736) stopped iterating at 15:15 - zero CPU, its status never
    written again although its heartbeat is every 120 s - while the runs it had started kept
    working. The Ofis showed every seat idle and the owner had to ask "neyi bekliyorlar". A
    `git worktree add` hung with an inner `git reset --hard` at 0 CPU the same day, twice.

      * Get-TeamWatchdogDecision: a lock held by a LIVE cycle.ps1 process of this machine
        whose status is older than 15 minutes is "restart"; a fresh status, another machine's
        lock, a free lock or a holder that is gone (the next cycle takes that lock over
        itself) is left alone. A restart inside two hours of the last one is "loop": a loop is
        not a fix, the Danisman is told instead;
      * Get-TeamWatchdogStopPlan: the cycle, everything under it and the tick (and its
        wrapper) above it - never a machine service (scripts/lib/TeamTickKeep.ps1) nor what
        runs under one;
      * Invoke-TeamWatchdog: the actions, through an injected set of functions (the suite's
        fakes; scripts/team/watchdog.ps1 the real ones). The lock is released only once the
        holder is gone - the way a dead holder's lock is taken over - and only then is the
        scheduled task started again;
      * Invoke-TeamTreeProcess / New-TeamWorktreeGuarded: a git call whose timeout kills the
        WHOLE process tree, and a `worktree add` that cleans up its half-made worktree and
        branch before it says why it stopped.

    Dot-sourced after NativeProcess.ps1, TeamQueue.ps1 and TeamTickKeep.ps1. Windows
    PowerShell 5.1, StrictMode. ASCII only (5.1 reads a BOM-less script as cp1252).
#>

Set-StrictMode -Version Latest

$script:TeamWatchdogStaleMinutes = 15
$script:TeamWatchdogLoopHours = 2
# What a cycle's and a tick's command line runs (the scheduled task: wrapper -> tick -> cycle).
$script:TeamWatchdogCyclePattern = '(?i)[\\/]team[\\/]cycle\.ps1'
$script:TeamWatchdogTickPattern = '(?i)[\\/]team[\\/]tick\.ps1|team-tick-wrapper\.ps1'

# ---------------------------------------------------------------------------- the decision

function Get-TeamWatchdogDecision {
    <#
    .SYNOPSIS
        free / elsewhere / not-live / fresh / restart / loop, with the holder's pid, the
        status' age in minutes and a Turkish line for the board and the report.
    #>
    param(
        $Lock,
        $Status,
        [Parameter(Mandatory = $true)][string]$Machine,
        # The lock's pid is alive AND its command line runs cycle.ps1.
        [bool]$HolderIsCycle,
        [AllowEmptyCollection()][datetime[]]$Restarts = @(),
        [datetime]$Now = [datetime]::UtcNow,
        [double]$StaleMinutes = $script:TeamWatchdogStaleMinutes,
        [double]$LoopHours = $script:TeamWatchdogLoopHours
    )
    $now = $Now.ToUniversalTime()
    $answer = [pscustomobject]@{ Kind = "free"; Pid = 0; CycleId = ""; AgeMinutes = 0.0; Text = "" }
    if ($null -eq $Lock -or -not [bool](Get-TeamProperty -InputObject $Lock -Name "held" -Default $false)) { return $answer }
    $answer.CycleId = [string](Get-TeamProperty -InputObject $Lock -Name "cycle_id" -Default "")
    $answer.Pid = [int](Get-TeamProperty -InputObject $Lock -Name "pid" -Default 0)
    $holder = [string](Get-TeamProperty -InputObject $Lock -Name "machine" -Default "")
    if (-not $holder -or $holder.ToUpperInvariant() -ne $Machine.ToUpperInvariant()) { $answer.Kind = "elsewhere"; return $answer }
    if ($answer.Pid -le 0 -or -not $HolderIsCycle) { $answer.Kind = "not-live"; return $answer }

    # The status' time counts when it is THIS holder's; else (an older cycle's document) the
    # time the lock was taken: a new cycle that never wrote its status is as stuck as one that
    # stopped writing it. The newer of the two is the last sign of life.
    $last = ConvertFrom-TeamTimestamp -Text ([string](Get-TeamProperty -InputObject $Lock -Name "acquired_at" -Default ""))
    if ($null -ne $Status) {
        $statusPid = [int](Get-TeamProperty -InputObject $Status -Name "pid" -Default 0)
        $written = ConvertFrom-TeamTimestamp -Text ([string](Get-TeamProperty -InputObject $Status -Name "updated_at" -Default ""))
        if ($statusPid -eq $answer.Pid -and $null -ne $written -and ($null -eq $last -or $written -gt $last)) { $last = $written }
    }
    # Nothing says when it last lived: never guessed into a stop.
    if ($null -eq $last) { $answer.Kind = "fresh"; return $answer }
    $answer.AgeMinutes = [Math]::Round(($now - $last).TotalMinutes, 1)
    if ($answer.AgeMinutes -lt $StaleMinutes) { $answer.Kind = "fresh"; return $answer }

    $age = [int][Math]::Floor($answer.AgeMinutes)
    $recent = @($Restarts | Where-Object { ($now - $_.ToUniversalTime()).TotalHours -lt $LoopHours })
    if (@($recent).Count -gt 0) {
        $answer.Kind = "loop"
        $answer.Text = "Bekci: dongu (pid $($answer.Pid), $($answer.CycleId)) yine takildi, durumu $age dk eski; son $LoopHours saat icinde zaten bir kez yeniden baslatildi - ikinci kez yapilmadi. Danisman bakmali (dongu bir cozum degil)."
        return $answer
    }
    $answer.Kind = "restart"
    $answer.Text = "Bekci: dongu (pid $($answer.Pid), $($answer.CycleId)) takildi - durumu $age dk eski, kosular ilerlerken yazmiyor. Dongu ve tick durduruldu (Docker/WSL degil), kilit birakildi, zamanlanmis gorev yeniden baslatildi."
    return $answer
}

# ---------------------------------------------------------------------------- the stop plan

function Get-TeamWatchdogSnapshot {
    <# Every process as Id, ParentId, Name, CommandLine, Created (UTC). #>
    $all = New-Object System.Collections.ArrayList
    foreach ($p in @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop)) {
        $created = [datetime]::MinValue
        if ($null -ne $p.CreationDate) { $created = ([datetime]$p.CreationDate).ToUniversalTime() }
        [void]$all.Add([pscustomobject]@{ Id = [int]$p.ProcessId; ParentId = [int]$p.ParentProcessId; Name = [string]$p.Name; CommandLine = [string]$p.CommandLine; Created = $created })
    }
    return @($all)
}

function Get-TeamWatchdogDescendants {
    <# The processes under a root, leaves first. A "child" created before its parent is a pid
       Windows gave out again, not a child, and is left out. Under a kept process (a machine
       service) nothing is listed: it is kept whole. #>
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Snapshot, [Parameter(Mandatory = $true)][int]$RootId, [switch]$Keep)
    $byId = @{}
    foreach ($p in $Snapshot) { $byId[[int]$p.Id] = $p }
    $children = @{}
    foreach ($p in $Snapshot) {
        $parent = $byId[[int]$p.ParentId]
        if ($null -eq $parent -or [int]$p.Id -eq [int]$p.ParentId) { continue }
        if ($p.Created -lt $parent.Created) { continue }
        if (-not $children.ContainsKey([int]$p.ParentId)) { $children[[int]$p.ParentId] = New-Object System.Collections.ArrayList }
        [void]$children[[int]$p.ParentId].Add($p)
    }
    $order = New-Object System.Collections.ArrayList
    $kept = New-Object System.Collections.ArrayList
    $seen = @{ $RootId = $true }
    function Visit([int]$Id) {
        if (-not $children.ContainsKey($Id)) { return }
        foreach ($child in $children[$Id]) {
            if ($seen.ContainsKey([int]$child.Id)) { continue }
            $seen[[int]$child.Id] = $true
            if ($Keep -and (Test-TeamTickKeep -Name ([string]$child.Name) -CommandLine ([string]$child.CommandLine))) { [void]$kept.Add($child); continue }
            Visit ([int]$child.Id)
            [void]$order.Add($child)
        }
    }
    Visit $RootId
    return [pscustomobject]@{ Stop = @($order); Keep = @($kept) }
}

function Get-TeamWatchdogStopPlan {
    <# What a restart stops, in order: the cycle's runs (leaves first), the cycle, then the
       tick and its wrapper above it. Kept: machine services under the cycle (and what runs
       under them), and a console host beside one. #>
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Snapshot, [Parameter(Mandatory = $true)][int]$CyclePid)
    $below = Get-TeamWatchdogDescendants -Snapshot $Snapshot -RootId $CyclePid -Keep
    $stop = New-Object System.Collections.ArrayList
    $keep = New-Object System.Collections.ArrayList
    foreach ($p in $below.Keep) { [void]$keep.Add($p) }
    $keeping = @($keep).Count -gt 0
    foreach ($p in $below.Stop) {
        # A console host beside a kept service may be that service's own console (the tick's rule).
        if ($keeping -and [string]$p.Name -eq "conhost.exe") { [void]$keep.Add($p); continue }
        [void]$stop.Add($p)
    }
    $byId = @{}
    foreach ($p in $Snapshot) { $byId[[int]$p.Id] = $p }
    $cycle = $byId[$CyclePid]
    if ($null -ne $cycle) {
        [void]$stop.Add($cycle)
        $at = $cycle
        for ($depth = 0; $depth -lt 4; $depth++) {
            $parent = $byId[[int]$at.ParentId]
            if ($null -eq $parent -or $parent.Created -gt $at.Created) { break }
            if ([string]$parent.CommandLine -notmatch $script:TeamWatchdogTickPattern) { break }
            [void]$stop.Add($parent)
            $at = $parent
        }
    }
    return [pscustomobject]@{ Stop = @($stop); Keep = @($keep) }
}

# ---------------------------------------------------------------------------- history

function Read-TeamWatchdogHistory {
    <# The watchdog's restarts, as UTC times; an unreadable file is an empty history. #>
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    try { $document = Read-TeamJson -Path $Path } catch { return @() }
    $times = New-Object System.Collections.ArrayList
    foreach ($text in @(Get-TeamProperty -InputObject $document -Name "restarts" -Default @())) {
        $at = ConvertFrom-TeamTimestamp -Text ([string]$text)
        if ($null -ne $at) { [void]$times.Add($at) }
    }
    return @($times)
}

function Write-TeamWatchdogHistory {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][AllowEmptyCollection()][datetime[]]$Restarts)
    $folder = Split-Path -Parent $Path
    if ($folder -and -not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
    # Only what the next decision can read: the last day.
    $kept = @($Restarts | Where-Object { ([datetime]::UtcNow - $_.ToUniversalTime()).TotalHours -lt 24 } | ForEach-Object { Get-TeamTimestamp -Now $_ })
    Write-TeamJson -Path $Path -Document ([pscustomobject]@{ restarts = @($kept) })
}

# ---------------------------------------------------------------------------- the actions

function Invoke-TeamWatchdog {
    <#
    .SYNOPSIS
        One look. -Io holds the functions it acts through: ReadLock, ReadStatus, Snapshot,
        IsAlive(id), StopProcess(id), ReleaseLock(lock), StartTask, Post(kind, to, text),
        Report(cycleId, text), Log(text) and HistoryPath.
    #>
    param(
        [Parameter(Mandatory = $true)][hashtable]$Io,
        [Parameter(Mandatory = $true)][string]$Machine,
        [datetime]$Now = [datetime]::UtcNow,
        [double]$StaleMinutes = $script:TeamWatchdogStaleMinutes,
        [double]$LoopHours = $script:TeamWatchdogLoopHours,
        [int]$DeathWaitSeconds = 20
    )
    $lock = & $Io.ReadLock
    $status = $null
    try { $status = & $Io.ReadStatus } catch { & $Io.Log "the status could not be read: $($_.Exception.Message)" }
    $snapshot = @(& $Io.Snapshot)
    $holderPid = [int](Get-TeamProperty -InputObject $lock -Name "pid" -Default 0)
    $holder = @($snapshot | Where-Object { [int]$_.Id -eq $holderPid })
    $isCycle = $holderPid -gt 0 -and @($holder).Count -gt 0 -and ([string]$holder[0].CommandLine -match $script:TeamWatchdogCyclePattern)
    $restarts = @(Read-TeamWatchdogHistory -Path $Io.HistoryPath)
    $decision = Get-TeamWatchdogDecision -Lock $lock -Status $status -Machine $Machine -HolderIsCycle $isCycle `
        -Restarts $restarts -Now $Now -StaleMinutes $StaleMinutes -LoopHours $LoopHours
    & $Io.Log "decision $($decision.Kind) (pid $($decision.Pid), cycle '$($decision.CycleId)', status $($decision.AgeMinutes) min old)"

    if ($decision.Kind -eq "loop") {
        & $Io.Post "soru" "danisman" $decision.Text
        & $Io.Report $decision.CycleId $decision.Text
        return $decision
    }
    if ($decision.Kind -ne "restart") { return $decision }

    $plan = Get-TeamWatchdogStopPlan -Snapshot $snapshot -CyclePid $decision.Pid
    foreach ($p in $plan.Keep) { & $Io.Log "keeping pid $($p.Id) $($p.Name) (a machine service; never stopped)" }
    foreach ($p in $plan.Stop) {
        & $Io.Log "stopping pid $($p.Id) $($p.Name)"
        try { & $Io.StopProcess ([int]$p.Id) } catch { & $Io.Log "pid $($p.Id) could not be stopped: $($_.Exception.Message)" }
    }
    # A restart is recorded as soon as it is tried: a holder that will not die is not tried
    # again every half hour.
    Write-TeamWatchdogHistory -Path $Io.HistoryPath -Restarts (@($restarts) + @($Now.ToUniversalTime()))

    $deadline = [datetime]::UtcNow.AddSeconds($DeathWaitSeconds)
    while ((& $Io.IsAlive $decision.Pid) -and [datetime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 250 }
    if (& $Io.IsAlive $decision.Pid) {
        # The lock of a live holder is never released: two cycles would run at once.
        $decision.Kind = "stuck-holder"
        $decision.Text = "Bekci: takilan dongu (pid $($decision.Pid), $($decision.CycleId)) durdurulamadi; kilit birakilmadi, gorev yeniden baslatilmadi. Danisman bakmali."
        & $Io.Post "soru" "danisman" $decision.Text
        & $Io.Report $decision.CycleId $decision.Text
        return $decision
    }
    & $Io.ReleaseLock $lock
    & $Io.Post "bilgi" "herkes" $decision.Text
    & $Io.Report $decision.CycleId $decision.Text
    & $Io.StartTask
    return $decision
}

function Add-TeamWatchdogReportLine {
    <# A line under "Bekci" in the cycle's local report (team/reports/<cycle>.md). #>
    param([Parameter(Mandatory = $true)][string]$ReportsRoot, [string]$CycleId, [Parameter(Mandatory = $true)][string]$Text)
    if ($CycleId -cnotmatch '^[a-z0-9][a-z0-9.-]{0,40}$') { $CycleId = "watchdog" }
    if (-not (Test-Path -LiteralPath $ReportsRoot)) { [void](New-Item -ItemType Directory -Force -Path $ReportsRoot) }
    $report = Join-Path $ReportsRoot "$CycleId.md"
    $heading = "## Bekci (dongu takildi)"
    $lines = @()
    if (-not (Test-Path -LiteralPath $report) -or -not ([System.IO.File]::ReadAllText($report)).Contains($heading)) { $lines += @("", $heading, "") }
    $lines += "- " + (Get-TeamTimestamp) + " " + $Text
    [System.IO.File]::AppendAllText($report, (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))
}

# ---------------------------------------------------------------------------- a git that cannot hang

function Stop-TeamProcessTree {
    <# The process and everything under it, leaves first. Returns the ids it stopped. #>
    param([Parameter(Mandatory = $true)][int]$RootId, [object[]]$Snapshot = $null)
    if ($null -eq $Snapshot) { $Snapshot = @(Get-TeamWatchdogSnapshot) }
    $ids = New-Object System.Collections.ArrayList
    foreach ($p in (Get-TeamWatchdogDescendants -Snapshot $Snapshot -RootId $RootId).Stop) { [void]$ids.Add([int]$p.Id) }
    [void]$ids.Add($RootId)
    foreach ($id in $ids) { try { Stop-Process -Id $id -Force -ErrorAction Stop } catch { } }
    return @($ids)
}

function Invoke-TeamTreeProcess {
    <#
    .SYNOPSIS
        Invoke-NativeProcess's answer (ExitCode, StdOut, StdErr, Success) plus TimedOut, for a
        tool whose children may hang: on timeout the WHOLE tree is killed (git and the hook,
        the `reset --hard`, the shell it started), so nothing is left holding a pipe. When the
        tool exits but a child still holds its output, that child is killed as well.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][AllowEmptyString()][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [int]$TimeoutSeconds = 300,
        [int]$OutputGraceSeconds = 15
    )
    $commandLine = ConvertTo-NativeArgumentLine -Arguments $Arguments
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.Arguments = $commandLine
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.WorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory).Path
    $process = [System.Diagnostics.Process]::Start($psi)
    $rootId = $process.Id
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $timedOut = $false
    $exitCode = -1
    try {
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $timedOut = $true
            [void](Stop-TeamProcessTree -RootId $rootId)
            [void]$process.WaitForExit(10000)
        }
        else { $exitCode = $process.ExitCode }
        $tasks = [System.Threading.Tasks.Task[]]@($stdoutTask, $stderrTask)
        if (-not [System.Threading.Tasks.Task]::WaitAll($tasks, $OutputGraceSeconds * 1000)) {
            # The tool is gone; a child it started still holds the pipe. Its parent id still
            # names the dead tool, so the snapshot finds it.
            [void](Stop-TeamProcessTree -RootId $rootId)
            [void][System.Threading.Tasks.Task]::WaitAll($tasks, 10000)
        }
        $stdout = if ($stdoutTask.IsCompleted) { $stdoutTask.GetAwaiter().GetResult() } else { "" }
        $stderr = if ($stderrTask.IsCompleted) { $stderrTask.GetAwaiter().GetResult() } else { "" }
    }
    finally { try { $process.Dispose() } catch { } }
    return [pscustomobject]@{
        FilePath    = $FilePath
        CommandLine = $commandLine
        ExitCode    = $exitCode
        StdOut      = $stdout
        StdErr      = $stderr
        TimedOut    = $timedOut
        Success     = (-not $timedOut -and $exitCode -eq 0)
    }
}

function New-TeamWorktreeGuarded {
    <#
    .SYNOPSIS
        New-TeamWorktree (scripts/lib/TeamRun.ps1) with every git call under a tree-killing
        timeout. A `worktree add` that times out or fails is cleaned - the worktree removed by
        force (twice: git locks a worktree while it is being made), pruned, its folder gone,
        and the branch deleted when this call made it - and then it throws with the reason.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$Branch,
        [string]$Base = "main",
        [Parameter(Mandatory = $true)][string]$Git,
        [int]$TimeoutSeconds = 300
    )
    $path = Get-TeamWorktreePath -RepoRoot $RepoRoot -Branch $Branch
    if (Test-Path -LiteralPath (Join-Path $path ".git")) {
        return [pscustomobject]@{ Path = $path; Branch = $Branch; Created = $false; Note = "the worktree is already there" }
    }
    $probe = Invoke-TeamTreeProcess -FilePath $Git -Arguments @("rev-parse", "--verify", "--quiet", "refs/heads/$Branch") -WorkingDirectory $RepoRoot -TimeoutSeconds $TimeoutSeconds
    if ($probe.TimedOut) { throw "git rev-parse timed out after $TimeoutSeconds s for ${Branch}; nothing was made" }
    $hadBranch = $probe.Success
    $parent = Split-Path -Parent $path
    if (-not (Test-Path -LiteralPath $parent)) { [void](New-Item -ItemType Directory -Force -Path $parent) }
    $addArgs = if ($hadBranch) { @("worktree", "add", $path, $Branch) } else { @("worktree", "add", "-b", $Branch, $path, $Base) }
    $add = Invoke-TeamTreeProcess -FilePath $Git -Arguments $addArgs -WorkingDirectory $RepoRoot -TimeoutSeconds $TimeoutSeconds
    if ($add.Success) { return [pscustomobject]@{ Path = $path; Branch = $Branch; Created = $true; Note = "" } }

    $why = if ($add.TimedOut) { "git worktree add timed out after $TimeoutSeconds s (git and its children were killed)" } else { "git worktree add failed: $($add.StdErr.Trim())" }
    $cleanup = New-Object System.Collections.ArrayList
    $steps = @(, @("worktree", "remove", "--force", "--force", $path)) + @(, @("worktree", "prune"))
    foreach ($step in $steps) {
        $ran = Invoke-TeamTreeProcess -FilePath $Git -Arguments $step -WorkingDirectory $RepoRoot -TimeoutSeconds 60
        [void]$cleanup.Add("git $($step[0]) $($step[1]): " + $(if ($ran.Success) { "ok" } elseif ($ran.TimedOut) { "timed out" } else { "exit $($ran.ExitCode)" }))
    }
    if (Test-Path -LiteralPath $path) {
        try { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop; [void]$cleanup.Add("folder removed") }
        catch { [void]$cleanup.Add("folder NOT removed: $($_.Exception.Message)") }
        [void](Invoke-TeamTreeProcess -FilePath $Git -Arguments @("worktree", "prune") -WorkingDirectory $RepoRoot -TimeoutSeconds 60)
    }
    if (-not $hadBranch) {
        $deleted = Invoke-TeamTreeProcess -FilePath $Git -Arguments @("branch", "-D", $Branch) -WorkingDirectory $RepoRoot -TimeoutSeconds 60
        [void]$cleanup.Add("branch -D: " + $(if ($deleted.Success) { "ok" } else { "exit $($deleted.ExitCode)" }))
    }
    throw "$why for $Branch; cleaned: $($cleanup -join ', ')"
}
