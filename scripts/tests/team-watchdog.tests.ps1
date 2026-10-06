<#
.SYNOPSIS
    The cycle watchdog (scripts/team/watchdog.ps1, scripts/lib/TeamWatchdog.ps1) and the
    guarded `git worktree add` of scripts/team/new-worktree.ps1.

.DESCRIPTION
    2026-10-06 (card cycle-watchdog): the cycle (pid 4736) stopped iterating at 15:15 - zero
    CPU, its status never written again - while its runs went on; the Ofis showed every seat
    idle until the owner asked "neyi bekliyorlar". A `git worktree add` hung with an inner
    `git reset --hard` at 0 CPU the same day, twice.

      * decisions: a live cycle of this machine with a 20-minute-old status is restarted; a
        fresh status, another machine's lock, a dead holder or a process that is not a cycle
        is left alone; a second restart inside two hours is not made and says so;
      * the stop list: the cycle, its runs and the tick above it - never Docker/WSL (and
        nothing under them), the rule of scripts/lib/TeamTickKeep.ps1;
      * Invoke-TeamWatchdog with fakes: what is stopped, released, re-run, posted, written;
      * watchdog.ps1 for real (-File) against a real fake cycle process tree (file mode);
      * new-worktree.ps1 for real against a temp repository whose post-checkout hook hangs
        with a child: git and the child are killed within the timeout, the half-made
        worktree and branch are removed.

    Every wait is a hang guard; every process started here is stopped by pid in a finally.

    Run: powershell -NoProfile -File scripts\tests\team-watchdog.tests.ps1
#>

[CmdletBinding()]
param([string]$Filter = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamTickKeep.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamWatchdog.ps1")
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
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

function New-TempFolder {
    $path = Join-Path ([System.IO.Path]::GetTempPath()) ("pagentos-watchdog-" + [guid]::NewGuid().ToString("N").Substring(0, 10))
    [void](New-Item -ItemType Directory -Force -Path $path)
    return $path
}

function Stop-Pids {
    param([int[]]$Ids)
    foreach ($id in @($Ids)) { if ($id -gt 0) { try { Stop-Process -Id $id -Force -ErrorAction Stop } catch { } } }
}

function Test-Alive {
    param([int]$Id)
    return ($null -ne (Get-Process -Id $Id -ErrorAction SilentlyContinue))
}

function Remove-Folder {
    # A copied .exe's image is unmapped a moment after its process ends: a delete is retried.
    param([string]$Path)
    for ($try = 0; $try -lt 20; $try++) {
        if (-not (Test-Path -LiteralPath $Path)) { return }
        try { Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop; return } catch { Start-Sleep -Milliseconds 250 }
    }
}

$now = [datetime]::UtcNow
$machine = "PC-OWNER"
function New-Lock { param([int]$HolderPid = 4736, [string]$Machine = "PC-OWNER", [double]$MinutesAgo = 40)
    [pscustomobject]@{ held = $true; machine = $Machine; cycle_id = "d20261006"; pid = $HolderPid; acquired_at = (Get-TeamTimestamp -Now $now.AddMinutes(-$MinutesAgo)) } }
function New-Status { param([double]$MinutesAgo, [int]$HolderPid = 4736)
    [pscustomobject]@{ cycle_id = "d20261006"; machine = "PC-OWNER"; pid = $HolderPid; updated_at = (Get-TeamTimestamp -Now $now.AddMinutes(-$MinutesAgo)) } }

Write-Host "team watchdog"

# ------------------------------------------------------------------ decisions

Test-Case "a live cycle of this machine with a 20-minute-old status is restarted" {
    $decision = Get-TeamWatchdogDecision -Lock (New-Lock) -Status (New-Status -MinutesAgo 20) -Machine $machine -HolderIsCycle $true -Restarts @() -Now $now
    Assert-Equal "restart" $decision.Kind "a stale status of a live cycle"
    Assert-Equal 4736 $decision.Pid "the lock's holder"
    Assert-True ($decision.AgeMinutes -ge 19.9 -and $decision.AgeMinutes -le 20.1) "the age is the status' age: $($decision.AgeMinutes)"
}

Test-Case "a fresh status is left alone" {
    $decision = Get-TeamWatchdogDecision -Lock (New-Lock) -Status (New-Status -MinutesAgo 5) -Machine $machine -HolderIsCycle $true -Restarts @() -Now $now
    Assert-Equal "fresh" $decision.Kind "five minutes is fresh"
    $edge = Get-TeamWatchdogDecision -Lock (New-Lock) -Status (New-Status -MinutesAgo 14.5) -Machine $machine -HolderIsCycle $true -Restarts @() -Now $now
    Assert-Equal "fresh" $edge.Kind "fourteen and a half minutes is still fresh"
}

Test-Case "a status of an older cycle is judged by when the lock was taken" {
    # The new cycle took the lock two minutes ago and has not written yet: not stuck.
    $decision = Get-TeamWatchdogDecision -Lock (New-Lock -HolderPid 9100 -MinutesAgo 2) -Status (New-Status -MinutesAgo 300 -HolderPid 4736) -Machine $machine -HolderIsCycle $true -Restarts @() -Now $now
    Assert-Equal "fresh" $decision.Kind "a lock taken two minutes ago"
    $never = Get-TeamWatchdogDecision -Lock (New-Lock -HolderPid 9100 -MinutesAgo 25) -Status (New-Status -MinutesAgo 300 -HolderPid 4736) -Machine $machine -HolderIsCycle $true -Restarts @() -Now $now
    Assert-Equal "restart" $never.Kind "a cycle that never wrote its status in 25 minutes"
}

Test-Case "another machine's lock, a free lock, a dead holder or a non-cycle process is never touched" {
    Assert-Equal "elsewhere" (Get-TeamWatchdogDecision -Lock (New-Lock -Machine "LAPTOP") -Status (New-Status -MinutesAgo 60) -Machine $machine -HolderIsCycle $true -Restarts @() -Now $now).Kind "the other PC's cycle"
    Assert-Equal "free" (Get-TeamWatchdogDecision -Lock ([pscustomobject]@{ held = $false }) -Status (New-Status -MinutesAgo 60) -Machine $machine -HolderIsCycle $false -Restarts @() -Now $now).Kind "nobody holds it"
    Assert-Equal "not-live" (Get-TeamWatchdogDecision -Lock (New-Lock) -Status (New-Status -MinutesAgo 60) -Machine $machine -HolderIsCycle $false -Restarts @() -Now $now).Kind "the pid is gone or is not cycle.ps1: the next cycle takes the lock over"
}

Test-Case "a second restart inside two hours is not made and says so" {
    $decision = Get-TeamWatchdogDecision -Lock (New-Lock) -Status (New-Status -MinutesAgo 20) -Machine $machine -HolderIsCycle $true -Restarts @($now.AddMinutes(-50)) -Now $now
    Assert-Equal "loop" $decision.Kind "one restart 50 minutes ago"
    Assert-True ($decision.Text -match '(?i)danisman' -and $decision.Text -match '2 saat') "the text tells the Danisman: $($decision.Text)"
    $later = Get-TeamWatchdogDecision -Lock (New-Lock) -Status (New-Status -MinutesAgo 20) -Machine $machine -HolderIsCycle $true -Restarts @($now.AddMinutes(-130)) -Now $now
    Assert-Equal "restart" $later.Kind "the last restart was 130 minutes ago"
}

# ------------------------------------------------------------------ the stop list

function New-Proc { param([int]$Id, [int]$Parent, [string]$Name, [string]$Line = "")
    [pscustomobject]@{ Id = $Id; ParentId = $Parent; Name = $Name; CommandLine = $Line; Created = $now.AddMinutes(-60 + $Id / 1000.0) } }

$snapshot = @(
    (New-Proc 100 4 "svchost.exe" "svchost -k netsvcs"),
    (New-Proc 200 100 "powershell.exe" "powershell -File C:\Users\x\.pagentos-team\team-tick-wrapper.ps1"),
    (New-Proc 300 200 "powershell.exe" "powershell -NoProfile -File `"E:\repo\scripts\team\tick.ps1`" -MaxParallel 6"),
    (New-Proc 4736 300 "powershell.exe" "powershell -NoProfile -File `"E:\repo\scripts\team\cycle.ps1`" -DailyId"),
    (New-Proc 5000 4736 "claude.exe" "claude -p worker"),
    (New-Proc 5001 5000 "bash.exe" "bash -c find ../../.."),
    (New-Proc 5100 4736 "Docker Desktop.exe" "`"C:\Program Files\Docker\Docker\Docker Desktop.exe`""),
    (New-Proc 5101 5100 "com.docker.backend.exe" "com.docker.backend"),
    (New-Proc 5102 5100 "helper.exe" "C:\Program Files\Docker\Docker\resources\helper.exe"),
    (New-Proc 5200 4736 "wsl.exe" "wsl -d docker-desktop"),
    (New-Proc 5300 4736 "conhost.exe" "conhost 0x4"),
    (New-Proc 6000 100 "claude.exe" "claude -p another unrelated")
)

Test-Case "the stop list: the cycle, its runs and the tick above it - never Docker/WSL or what runs under them" {
    $plan = Get-TeamWatchdogStopPlan -Snapshot $snapshot -CyclePid 4736
    $stop = @($plan.Stop | ForEach-Object { $_.Id })
    $keep = @($plan.Keep | ForEach-Object { $_.Id })
    foreach ($id in @(4736, 5000, 5001, 300, 200)) { Assert-True ($stop -contains $id) "pid $id is stopped (stop: $($stop -join ','))" }
    foreach ($id in @(5100, 5101, 5102, 5200, 5300)) { Assert-True ($stop -notcontains $id) "pid $id (Docker/WSL or under it) is never stopped (stop: $($stop -join ','))" }
    foreach ($id in @(5100, 5200)) { Assert-True ($keep -contains $id) "pid $id is named as kept" }
    foreach ($id in @(100, 6000)) { Assert-True ($stop -notcontains $id) "pid $id is outside the cycle" }
    # Leaves first, the cycle after its runs, the tick last.
    Assert-True ([array]::IndexOf($stop, 5001) -lt [array]::IndexOf($stop, 5000)) "a child before its parent"
    Assert-True ([array]::IndexOf($stop, 5000) -lt [array]::IndexOf($stop, 4736)) "the runs before the cycle"
}

Test-Case "a recycled pid is not a child: a process older than its parent is left alone" {
    $old = @($snapshot) + @([pscustomobject]@{ Id = 7000; ParentId = 4736; Name = "notepad.exe"; CommandLine = "notepad"; Created = $now.AddHours(-5) })
    $plan = Get-TeamWatchdogStopPlan -Snapshot $old -CyclePid 4736
    Assert-True (@($plan.Stop | ForEach-Object { $_.Id }) -notcontains 7000) "a process created before the cycle is not its child"
}

# ------------------------------------------------------------------ Invoke-TeamWatchdog with fakes

function New-FakeIo {
    param($Lock, $Status, [string]$HistoryPath)
    $calls = New-Object System.Collections.ArrayList
    $io = @{
        Calls       = $calls
        ReadLock    = { $Lock }.GetNewClosure()
        ReadStatus  = { $Status }.GetNewClosure()
        Snapshot    = { $snapshot }
        IsAlive     = { param($Id) $true }
        StopProcess = { param($Id) [void]$calls.Add("stop $Id") }.GetNewClosure()
        ReleaseLock = { param($Lock) [void]$calls.Add("release $($Lock.cycle_id)") }.GetNewClosure()
        StartTask   = { [void]$calls.Add("start-task") }.GetNewClosure()
        Post        = { param($Kind, $To, $Text) [void]$calls.Add("post $Kind $To $Text") }.GetNewClosure()
        Report      = { param($CycleId, $Text) [void]$calls.Add("report $CycleId $Text") }.GetNewClosure()
        Log         = { param($Text) [void]$calls.Add("log $Text") }.GetNewClosure()
        HistoryPath = $HistoryPath
    }
    return $io
}

Test-Case "a stale live cycle: the tree is stopped (never Docker), the lock released, the task re-run, the board and report told" {
    $folder = New-TempFolder
    try {
        $io = New-FakeIo -Lock (New-Lock) -Status (New-Status -MinutesAgo 20) -HistoryPath (Join-Path $folder "history.json")
        $io.IsAlive = { param($Id) $Id -ne 4736 -or -not ($io.Calls -contains "stop 4736") }.GetNewClosure()
        $result = Invoke-TeamWatchdog -Io $io -Machine $machine -Now $now
        Assert-Equal "restart" $result.Kind "the decision"
        $calls = @($io.Calls)
        foreach ($id in @(4736, 5000, 5001, 300, 200)) { Assert-True ($calls -contains "stop $id") "pid $id stopped: $($calls -join ' | ')" }
        foreach ($id in @(5100, 5101, 5102, 5200, 5300)) { Assert-True ($calls -notcontains "stop $id") "pid $id never stopped" }
        Assert-True ($calls -contains "release d20261006") "the lock is released"
        Assert-True ($calls -contains "start-task") "the scheduled task is started again"
        Assert-True ([array]::IndexOf($calls, "release d20261006") -lt [array]::IndexOf($calls, "start-task")) "released before the re-run"
        Assert-True (@($calls | Where-Object { $_ -like "post bilgi herkes *4736*" }).Count -eq 1) "one board note naming the pid"
        Assert-True (@($calls | Where-Object { $_ -like "report d20261006 *20 dk*" }).Count -eq 1) "the cycle's report has the risk line"
        $history = @(Read-TeamWatchdogHistory -Path $io.HistoryPath)
        Assert-Equal 1 @($history).Count "the restart is recorded"
    }
    finally { Remove-Folder $folder }
}

Test-Case "a fresh cycle: nothing is stopped, released, started or posted" {
    $folder = New-TempFolder
    try {
        $io = New-FakeIo -Lock (New-Lock) -Status (New-Status -MinutesAgo 3) -HistoryPath (Join-Path $folder "history.json")
        $result = Invoke-TeamWatchdog -Io $io -Machine $machine -Now $now
        Assert-Equal "fresh" $result.Kind "the decision"
        Assert-Equal 0 @($io.Calls | Where-Object { $_ -notlike "log *" }).Count "only a log line: $(@($io.Calls) -join ' | ')"
        Assert-True (-not (Test-Path -LiteralPath $io.HistoryPath)) "no restart recorded"
    }
    finally { Remove-Folder $folder }
}

Test-Case "a second stuck cycle within two hours: nothing is stopped, the Danisman is told" {
    $folder = New-TempFolder
    try {
        $historyPath = Join-Path $folder "history.json"
        Write-TeamWatchdogHistory -Path $historyPath -Restarts @($now.AddMinutes(-40))
        $io = New-FakeIo -Lock (New-Lock) -Status (New-Status -MinutesAgo 20) -HistoryPath $historyPath
        $result = Invoke-TeamWatchdog -Io $io -Machine $machine -Now $now
        Assert-Equal "loop" $result.Kind "the decision"
        $calls = @($io.Calls)
        Assert-Equal 0 @($calls | Where-Object { $_ -like "stop *" -or $_ -eq "start-task" -or $_ -like "release *" }).Count "nothing acted: $($calls -join ' | ')"
        Assert-True (@($calls | Where-Object { $_ -like "post soru danisman *" }).Count -eq 1) "a question to the Danisman: $($calls -join ' | ')"
        Assert-True (@($calls | Where-Object { $_ -like "report d20261006 *" }).Count -eq 1) "the report says so"
        Assert-Equal 1 @(Read-TeamWatchdogHistory -Path $historyPath).Count "no second restart recorded"
    }
    finally { Remove-Folder $folder }
}

Test-Case "a holder that will not die: the lock is NOT released and the task is not re-run" {
    $folder = New-TempFolder
    try {
        $io = New-FakeIo -Lock (New-Lock) -Status (New-Status -MinutesAgo 20) -HistoryPath (Join-Path $folder "history.json")
        $result = Invoke-TeamWatchdog -Io $io -Machine $machine -Now $now -DeathWaitSeconds 1
        $calls = @($io.Calls)
        Assert-Equal "stuck-holder" $result.Kind "the cycle did not end"
        Assert-True ($calls -notcontains "release d20261006" -and $calls -notcontains "start-task") "a live holder's lock is never released: $($calls -join ' | ')"
        Assert-True (@($calls | Where-Object { $_ -like "post soru danisman *" }).Count -eq 1) "the Danisman is told"
    }
    finally { Remove-Folder $folder }
}

# ------------------------------------------------------------------ watchdog.ps1 for real

Test-Case "watchdog.ps1 (-File): a real stuck cycle tree is stopped, Docker is kept, the lock released, the task re-run" {
    $folder = New-TempFolder
    $started = New-Object System.Collections.ArrayList
    try {
        $fakeDir = Join-Path $folder "repo\scripts\team"
        [void](New-Item -ItemType Directory -Force -Path $fakeDir)
        $teamRoot = Join-Path $folder "team"
        [void](New-Item -ItemType Directory -Force -Path $teamRoot)
        $dockerExe = Join-Path $folder "docker.exe"
        Copy-Item -LiteralPath (Join-Path $env:SystemRoot "System32\PING.EXE") -Destination $dockerExe
        $pidsFile = Join-Path $folder "children.txt"
        # The fake cycle: a "run" (ping) and a "Docker" (docker.exe) under it, then it hangs.
        $fakeCycle = Join-Path $fakeDir "cycle.ps1"
        [System.IO.File]::WriteAllText($fakeCycle, @"
`$run = Start-Process -FilePath "`$env:SystemRoot\System32\PING.EXE" -ArgumentList "-n 900 127.0.0.1" -WindowStyle Hidden -PassThru
`$dock = Start-Process -FilePath "$dockerExe" -ArgumentList "-n 900 127.0.0.1" -WindowStyle Hidden -PassThru
[System.IO.File]::WriteAllText("$pidsFile", "`$(`$run.Id) `$(`$dock.Id)")
Start-Sleep -Seconds 900
"@)
        $cycle = Start-Process -FilePath $powershell -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$fakeCycle`"") -WindowStyle Hidden -PassThru
        [void]$started.Add($cycle.Id)
        $deadline = [datetime]::UtcNow.AddSeconds(30)
        while (-not (Test-Path -LiteralPath $pidsFile) -and [datetime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 200 }
        Assert-True (Test-Path -LiteralPath $pidsFile) "the fake cycle started its children"
        $ids = @(([System.IO.File]::ReadAllText($pidsFile)).Trim() -split ' ' | ForEach-Object { [int]$_ })
        $runId = $ids[0]; $dockId = $ids[1]
        [void]$started.Add($runId); [void]$started.Add($dockId)

        $lockNow = [datetime]::UtcNow
        Write-TeamJson -Path (Join-Path $teamRoot "lock.json") -Document ([pscustomobject]@{ held = $true; machine = $env:COMPUTERNAME; cycle_id = "c-test"; pid = $cycle.Id; acquired_at = (Get-TeamTimestamp -Now $lockNow.AddMinutes(-40)) })
        Write-TeamJson -Path (Join-Path $teamRoot "status.json") -Document ([pscustomobject]@{ cycle_id = "c-test"; machine = $env:COMPUTERNAME; pid = $cycle.Id; updated_at = (Get-TeamTimestamp -Now $lockNow.AddMinutes(-20)) })
        $taskMarker = Join-Path $folder "task-run.txt"
        $runTask = Join-Path $folder "run-task.ps1"
        [System.IO.File]::WriteAllText($runTask, "param([string]`$TaskName) [System.IO.File]::WriteAllText(`"$taskMarker`", `$TaskName)")

        $output = & $powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot "scripts\team\watchdog.ps1") `
            -TeamRoot $teamRoot -TaskName "PagentOS Test Task" -RunTaskPath $runTask -HistoryPath (Join-Path $folder "history.json") -LogPath (Join-Path $folder "watchdog.log")
        $code = $LASTEXITCODE
        Assert-Equal 0 $code "watchdog.ps1 exit; output: $($output -join ' / ')"
        Start-Sleep -Milliseconds 500
        Assert-True (-not (Test-Alive $cycle.Id)) "the stuck cycle is stopped"
        Assert-True (-not (Test-Alive $runId)) "its run is stopped"
        Assert-True (Test-Alive $dockId) "the Docker-named process under it is kept"
        Assert-True (Test-Path -LiteralPath $taskMarker) "the scheduled task is run again"
        Assert-Equal "PagentOS Test Task" ([System.IO.File]::ReadAllText($taskMarker)) "by its name"
        $lockAfter = Read-TeamJson -Path (Join-Path $teamRoot "lock.json")
        Assert-True (-not [bool](Get-TeamProperty -InputObject $lockAfter -Name "held" -Default $false)) "the lock is released"
        $report = Join-Path $teamRoot "reports\c-test.md"
        Assert-True ((Test-Path -LiteralPath $report) -and ([System.IO.File]::ReadAllText($report) -match '20 dk')) "the cycle's report has the risk line"
    }
    finally {
        Stop-Pids -Ids @($started)
        Remove-Folder $folder
    }
}

Test-Case "watchdog.ps1 (-File): a fresh real cycle is never touched" {
    $folder = New-TempFolder
    $started = New-Object System.Collections.ArrayList
    try {
        $fakeDir = Join-Path $folder "repo\scripts\team"
        [void](New-Item -ItemType Directory -Force -Path $fakeDir)
        $teamRoot = Join-Path $folder "team"
        [void](New-Item -ItemType Directory -Force -Path $teamRoot)
        $fakeCycle = Join-Path $fakeDir "cycle.ps1"
        [System.IO.File]::WriteAllText($fakeCycle, "Start-Sleep -Seconds 900")
        $cycle = Start-Process -FilePath $powershell -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$fakeCycle`"") -WindowStyle Hidden -PassThru
        [void]$started.Add($cycle.Id)
        $lockNow = [datetime]::UtcNow
        Write-TeamJson -Path (Join-Path $teamRoot "lock.json") -Document ([pscustomobject]@{ held = $true; machine = $env:COMPUTERNAME; cycle_id = "c-test"; pid = $cycle.Id; acquired_at = (Get-TeamTimestamp -Now $lockNow.AddMinutes(-40)) })
        Write-TeamJson -Path (Join-Path $teamRoot "status.json") -Document ([pscustomobject]@{ cycle_id = "c-test"; machine = $env:COMPUTERNAME; pid = $cycle.Id; updated_at = (Get-TeamTimestamp -Now $lockNow.AddMinutes(-2)) })
        $taskMarker = Join-Path $folder "task-run.txt"
        $runTask = Join-Path $folder "run-task.ps1"
        [System.IO.File]::WriteAllText($runTask, "param([string]`$TaskName) [System.IO.File]::WriteAllText(`"$taskMarker`", `$TaskName)")
        $output = & $powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot "scripts\team\watchdog.ps1") `
            -TeamRoot $teamRoot -TaskName "PagentOS Test Task" -RunTaskPath $runTask -HistoryPath (Join-Path $folder "history.json") -LogPath (Join-Path $folder "watchdog.log")
        Assert-Equal 0 $LASTEXITCODE "watchdog.ps1 exit; output: $($output -join ' / ')"
        Assert-True (Test-Alive $cycle.Id) "the fresh cycle runs on"
        Assert-True (-not (Test-Path -LiteralPath $taskMarker)) "the task is not re-run"
        Assert-True ([bool](Read-TeamJson -Path (Join-Path $teamRoot "lock.json")).held) "the lock is still held"
    }
    finally {
        Stop-Pids -Ids @($started)
        Remove-Folder $folder
    }
}

# ------------------------------------------------------------------ new-worktree.ps1: a hanging git

function Invoke-NewWorktree {
    # Through Invoke-NativeProcess: a `2>&1` on a child powershell turns its stderr into a
    # terminating error here (ErrorActionPreference Stop), not into text to assert on.
    # The 200 s is a hang guard; the case asserts its own, shorter bound.
    param([string]$Repo, [string]$Slug, [int]$GitTimeoutSeconds)
    return (Invoke-NativeProcess -FilePath $powershell -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            (Join-Path $repoRoot "scripts\team\new-worktree.ps1"), "-CycleId", "t1", "-Role", "worker", "-Slug", $Slug,
            "-Base", "main", "-RepoRoot", $Repo, "-GitTimeoutSeconds", "$GitTimeoutSeconds") -SuccessExitCodes @(0) -TimeoutSeconds 200)
}

Test-Case "new-worktree.ps1: a hanging 'git worktree add' is killed with its child within the timeout, the half-made worktree and branch removed" {
    $folder = New-TempFolder
    $marker = Get-Random -Minimum 6100 -Maximum 6999
    try {
        $git = (Get-Command git.exe -ErrorAction Stop).Source
        $repo = Join-Path $folder "repo"
        [void](New-Item -ItemType Directory -Force -Path $repo)
        foreach ($step in @(@("init", "-q", "-b", "main"), @("config", "user.email", "t@example.invalid"), @("config", "user.name", "t"), @("config", "core.autocrlf", "false"))) {
            $r = Invoke-NativeProcess -FilePath $git -Arguments $step -WorkingDirectory $repo -TimeoutSeconds 60
            Assert-True $r.Success "git $($step -join ' '): $($r.StdErr)"
        }
        [System.IO.File]::WriteAllText((Join-Path $repo "a.txt"), "a`n")
        foreach ($step in @(@("add", "a.txt"), @("commit", "-q", "-m", "one"))) {
            $r = Invoke-NativeProcess -FilePath $git -Arguments $step -WorkingDirectory $repo -TimeoutSeconds 60
            Assert-True $r.Success "git $($step -join ' '): $($r.StdErr)"
        }
        # The hang: a post-checkout hook that waits on a child (ping with a count nobody else uses).
        $hook = Join-Path $repo ".git\hooks\post-checkout"
        [System.IO.File]::WriteAllText($hook, "#!/bin/sh`nping -n $marker 127.0.0.1 >/dev/null`n")
        $watch = [System.Diagnostics.Stopwatch]::StartNew()
        $ran = Invoke-NewWorktree -Repo $repo -Slug "hang" -GitTimeoutSeconds 10
        $code = $ran.ExitCode
        $watch.Stop()
        $text = ($ran.StdOut + " / " + $ran.StdErr) -replace '\s+', ' '
        Assert-True ($code -ne 0) "the task is stopped with a failure: exit $code, $text"
        Assert-True ($text -match 'timed out|zaman') "the reason is named: $text"
        # Hang guard: ten seconds of timeout plus the clean-up; the hook alone would wait 100 minutes.
        Assert-True ($watch.Elapsed.TotalSeconds -lt 90) "within the timeout: $($watch.Elapsed.TotalSeconds) s"
        Start-Sleep -Milliseconds 500
        $left = @(Get-CimInstance -ClassName Win32_Process -Filter "Name='PING.EXE' OR Name='ping.exe'" | Where-Object { ([string]$_.CommandLine) -match "-n $marker " })
        Assert-Equal 0 @($left).Count "the hook's child is killed with git (left: $(@($left | ForEach-Object { $_.ProcessId }) -join ','))"
        $tree = Join-Path $repo ".claude\worktrees\team\t1\worker-hang"
        Assert-True (-not (Test-Path -LiteralPath $tree)) "the half-made worktree folder is removed"
        $branches = Invoke-NativeProcess -FilePath $git -Arguments @("branch", "--list", "team/t1/worker-hang") -WorkingDirectory $repo -TimeoutSeconds 60
        Assert-Equal "" $branches.StdOut.Trim() "the half-made branch is deleted"
        $list = Invoke-NativeProcess -FilePath $git -Arguments @("worktree", "list", "--porcelain") -WorkingDirectory $repo -TimeoutSeconds 60
        Assert-True ($list.StdOut -notmatch 'worker-hang') "git no longer lists the worktree: $($list.StdOut)"
    }
    finally {
        Get-CimInstance -ClassName Win32_Process -Filter "Name='PING.EXE' OR Name='ping.exe'" | Where-Object { ([string]$_.CommandLine) -match "-n $marker " } |
            ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch { } }
        Remove-Folder $folder
    }
}

Test-Case "new-worktree.ps1: a git that answers in time makes the worktree as before" {
    $folder = New-TempFolder
    try {
        $git = (Get-Command git.exe -ErrorAction Stop).Source
        $repo = Join-Path $folder "repo"
        [void](New-Item -ItemType Directory -Force -Path $repo)
        foreach ($step in @(@("init", "-q", "-b", "main"), @("config", "user.email", "t@example.invalid"), @("config", "user.name", "t"))) {
            [void](Invoke-NativeProcess -FilePath $git -Arguments $step -WorkingDirectory $repo -TimeoutSeconds 60)
        }
        [System.IO.File]::WriteAllText((Join-Path $repo "a.txt"), "a`n")
        [void](Invoke-NativeProcess -FilePath $git -Arguments @("add", "a.txt") -WorkingDirectory $repo -TimeoutSeconds 60)
        [void](Invoke-NativeProcess -FilePath $git -Arguments @("commit", "-q", "-m", "one") -WorkingDirectory $repo -TimeoutSeconds 60)
        $first = Invoke-NewWorktree -Repo $repo -Slug "ok" -GitTimeoutSeconds 60
        Assert-Equal 0 $first.ExitCode "exit; $($first.StdOut) / $($first.StdErr)"
        Assert-True (Test-Path -LiteralPath (Join-Path $repo ".claude\worktrees\team\t1\worker-ok\a.txt")) "the worktree is there"
        $again = Invoke-NewWorktree -Repo $repo -Slug "ok" -GitTimeoutSeconds 60
        Assert-Equal 0 $again.ExitCode "a second call is idempotent; $($again.StdOut) / $($again.StdErr)"
        Assert-True ($again.StdOut -match 'already there') "and says so"
    }
    finally { Remove-Folder $folder }
}

Write-Host ""
Write-Host "team-watchdog: $($script:Passes) passed, $($script:Failures) failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
