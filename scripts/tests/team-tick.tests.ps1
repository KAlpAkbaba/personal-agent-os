<#
.SYNOPSIS
    The scheduled task's tick (scripts/team/tick.ps1) ends when the feeder and the cycle end,
    whatever they left behind - and stops what they left behind, never anything else.

.DESCRIPTION
    2026-10-03 (local 02:00-04:15): the cycle had ended and released its lock, but the tick
    that started it was still alive: `Start-Process -Wait` in Windows PowerShell 5.1 waits for
    the process AND every process it ever started, and an agent run had left a `tail -f` and
    a `grep` reading it. The scheduled task (MultipleInstances IgnoreNew) skipped every later
    trigger and no cycle ran for two hours.

    The tick is run for real under Windows PowerShell 5.1 (-File) with fakes in place of the
    feeder and the cycle (its -FeedPath / -CyclePath hooks), written by this file into a temp
    folder. A fake leaves behind a powershell loop that appends to a marker file every second.
    Every wait below is a hang guard only; what is asserted is what the processes and files
    show afterwards. Nothing outside the temp folder is written; every process this file or
    a fake starts is stopped by its pid in a finally block.

    Run: powershell -NoProfile -File scripts\tests\team-tick.tests.ps1
#>

[CmdletBinding()]
param(
    # Runs only the cases whose name matches this pattern (the mutation proofs re-run a slice).
    [string]$Filter = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$tick = Join-Path $repoRoot "scripts\team\tick.ps1"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$script:Failures = 0
$script:Passes = 0
# A hang guard: a tick whose cycle has ended is gone in a few seconds; the old one never went.
$script:TickGuardSeconds = 60
$script:JobFailsVariable = "PAGENTOS_TEAM_TICK_JOB_FAILS"

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

function Test-Alive {
    param([int]$ProcessId)
    $found = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    return ($null -ne $found -and -not $found.HasExited)
}

function Stop-ById {
    param([int[]]$Ids)
    foreach ($id in @($Ids)) { if ($id -gt 0) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue } }
}

function Wait-Until {
    # A hang guard: true as soon as the condition holds, false when the guard ran out.
    param([scriptblock]$Condition, [int]$Seconds = 30)
    $deadline = [datetime]::UtcNow.AddSeconds($Seconds)
    while ([datetime]::UtcNow -lt $deadline) {
        if (& $Condition) { return $true }
        Start-Sleep -Milliseconds 200
    }
    return [bool](& $Condition)
}

function Get-MarkerLength {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return 0 }
    return (Get-Item -LiteralPath $Path).Length
}

function Read-Pid {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return 0 }
    $text = [System.IO.File]::ReadAllText($Path).Trim()
    if ($text -match '^\d+$') { return [int]$text }
    return 0
}

function New-Work {
    $work = Join-Path $env:TEMP ("ptick-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    [void](New-Item -ItemType Directory -Force -Path $work)
    # The orphan: a powershell loop that appends to its marker every second, for ever.
    Set-Content -LiteralPath (Join-Path $work "orphan.ps1") -Encoding ASCII -Value @(
        'param([string]$Marker)',
        'while ($true) { Add-Content -LiteralPath $Marker -Value "x"; Start-Sleep -Seconds 1 }'
    )
    return $work
}

function Remove-Work {
    # Every orphan a fake recorded is stopped by its pid, whatever the case did before it failed.
    param([string]$Work)
    foreach ($file in @(Get-ChildItem -LiteralPath $Work -Filter "*.orphan-pid" -ErrorAction SilentlyContinue)) {
        Stop-ById -Ids @(Read-Pid -Path $file.FullName)
    }
    Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
}

function Get-OrphanArguments {
    # The same list for the fakes and for the test's own twin: one command line.
    param([string]$Work, [string]$Marker)
    return @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + (Join-Path $Work "orphan.ps1") + '"'), ('"' + $Marker + '"'))
}

function New-FakeScript {
    <#
        A fake feeder / cycle. -Orphan: it starts the orphan loop (its pid into <Path>.orphan-pid)
        and waits until the loop has written once. -HoldFile: it then waits until that file exists
        (a hang guard of 60 s), so the test decides when it ends. -LogFile: one line per call.
    #>
    param([string]$Path, [int]$ExitCode = 0, [string]$Orphan = "", [string]$HoldFile = "", [string]$LogFile = "", [string]$Name = "", [string]$Work = "", [switch]$Throw)
    $lines = New-Object System.Collections.ArrayList
    if ($LogFile) { [void]$lines.Add("Add-Content -LiteralPath '$LogFile' -Value ('$Name ' + (`$args -join ' '))") }
    if ($Orphan) {
        $list = (Get-OrphanArguments -Work $Work -Marker $Orphan | ForEach-Object { "'" + ($_ -replace "'", "''") + "'" }) -join ","
        [void]$lines.Add("`$child = Start-Process -FilePath '$powershell' -ArgumentList @($list) -NoNewWindow -PassThru")
        [void]$lines.Add("Set-Content -LiteralPath '$Path.orphan-pid' -Value `$child.Id")
        [void]$lines.Add("`$until = [datetime]::UtcNow.AddSeconds(30); while (-not (Test-Path -LiteralPath '$Orphan') -and [datetime]::UtcNow -lt `$until) { Start-Sleep -Milliseconds 100 }")
    }
    if ($HoldFile) {
        [void]$lines.Add("`$until = [datetime]::UtcNow.AddSeconds(60); while (-not (Test-Path -LiteralPath '$HoldFile') -and [datetime]::UtcNow -lt `$until) { Start-Sleep -Milliseconds 100 }")
    }
    if ($Throw) { [void]$lines.Add("throw 'the fake feeder fails'") }
    [void]$lines.Add("exit $ExitCode")
    Set-Content -LiteralPath $Path -Encoding ASCII -Value @($lines)
}

function Get-ClosedPort {
    # A loopback port nobody listens on: bound, read, released.
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    $listener.Stop()
    return $port
}

function New-FakeBoard {
    # A stand-in for board.ps1: one line per call into <Work>\board.log, exit 0.
    param([string]$Work)
    $path = Join-Path $Work "board.ps1"
    $log = Join-Path $Work "board.log"
    Set-Content -LiteralPath $path -Encoding ASCII -Value @("Add-Content -LiteralPath '$log' -Encoding UTF8 -Value (`$args -join ' ')", "exit 0")
    return $path
}

function Start-Tick {
    # The tick under Windows PowerShell 5.1, -File, its output into files (an orphan holding a
    # pipe would hold the reader; a file holds nobody). A case that names no staging gets a
    # closed loopback port and a fake board: no case reaches the real staging or the real board.
    param([string]$Work, [string[]]$Arguments)
    if (@($Arguments) -notcontains "-StagingHealthUrl") { $Arguments = @($Arguments) + @("-StagingHealthUrl", "http://127.0.0.1:$(Get-ClosedPort)/v1/system/health") }
    if (@($Arguments) -notcontains "-BoardPath") { $Arguments = @($Arguments) + @("-BoardPath", ('"' + (New-FakeBoard -Work $Work) + '"')) }
    $stem = Join-Path $Work ("tick-" + [guid]::NewGuid().ToString("N").Substring(0, 6))
    $all = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + $tick + '"')) + $Arguments
    $process = Start-Process -FilePath $powershell -ArgumentList $all -NoNewWindow -PassThru `
        -RedirectStandardOutput "$stem.out" -RedirectStandardError "$stem.err"
    [void]$process.Handle   # the exit code is read through this handle later
    return [pscustomobject]@{ Process = $process; Out = "$stem.out"; Err = "$stem.err"; Started = [datetime]::UtcNow }
}

function Wait-Tick {
    <#
        Waits for the tick under the hang guard. When the guard runs out, the orphans named by
        -OrphanPidFiles are stopped by pid and the tick is given ten more seconds: the failure
        then says how long the old tick waited and whether it was the orphan that held it.
    #>
    param($Tick, [string[]]$OrphanPidFiles = @())
    $exited = $Tick.Process.WaitForExit($script:TickGuardSeconds * 1000)
    if (-not $exited) {
        $held = [int]([datetime]::UtcNow - $Tick.Started).TotalSeconds
        $ids = @($OrphanPidFiles | ForEach-Object { Read-Pid -Path $_ } | Where-Object { $_ -gt 0 })
        Stop-ById -Ids $ids
        $stopped = [datetime]::UtcNow
        $after = $Tick.Process.WaitForExit(10000)
        $tail = if ($after) { "it exited $([int]([datetime]::UtcNow - $stopped).TotalMilliseconds) ms after the test stopped the orphan(s) $($ids -join ',')" } else { "it was still alive 10 s after the orphans were stopped" }
        if (-not $after) { Stop-ById -Ids @($Tick.Process.Id) }
        throw "HANG GUARD: the tick was still alive $held s after it started although its cycle had ended; $tail"
    }
    $Tick.Process.WaitForExit()
    return [pscustomobject]@{ ExitCode = $Tick.Process.ExitCode; Out = (Read-Log -Path $Tick.Out); Err = (Read-Log -Path $Tick.Err) }
}

function Read-Log {
    # Shared read: an orphan the tick was not allowed to stop (case 6) still holds its inherited output file.
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return "" }
    $stream = New-Object System.IO.FileStream($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try { return (New-Object System.IO.StreamReader($stream)).ReadToEnd() }
    finally { $stream.Dispose() }
}

function Assert-OrphanStopped {
    param([int]$OrphanPid, [string]$Marker, [string]$Because)
    Assert-True -Condition ($OrphanPid -gt 0) -Because "the fake wrote the orphan's pid ($Because)"
    Assert-True -Condition (Wait-Until -Seconds 10 -Condition { -not (Test-Alive -ProcessId $OrphanPid) }) -Because "the orphan $OrphanPid is not alive after the tick ($Because)"
    $before = Get-MarkerLength -Path $Marker
    Start-Sleep -Milliseconds 2500
    Assert-Equal -Expected $before -Actual (Get-MarkerLength -Path $Marker) -Because "the orphan's marker stopped growing ($Because)"
}

Write-Host "the tick, under Windows PowerShell 5.1, with fakes in place of the feeder and the cycle"

Test-Case "(1) a cycle that leaves a never-ending grandchild and exits 0: the tick exits, the grandchild is stopped and named in the log and the report" {
    $work = New-Work
    $ids = @()
    try {
        $marker = Join-Path $work "marker-cycle.txt"
        $feed = Join-Path $work "feed.ps1"; New-FakeScript -Path $feed
        $cycle = Join-Path $work "cycle.ps1"; New-FakeScript -Path $cycle -Orphan $marker -Work $work
        $log = Join-Path $work "logs\tick.log"
        $reports = Join-Path $work "reports"
        [void](New-Item -ItemType Directory -Force -Path $reports)
        $days = @((Get-Date).ToString("yyyyMMdd"), (Get-Date).AddMinutes(2).ToString("yyyyMMdd")) | Select-Object -Unique
        foreach ($day in $days) { Set-Content -LiteralPath (Join-Path $reports "d$day.md") -Encoding UTF8 -Value "# report d$day" }
        $run = Start-Tick -Work $work -Arguments @("-FeedPath", ('"' + $feed + '"'), "-CyclePath", ('"' + $cycle + '"'),
            "-DailyId", "-LogPath", ('"' + $log + '"'), "-ReportsRoot", ('"' + $reports + '"'))
        $result = Wait-Tick -Tick $run -OrphanPidFiles @("$cycle.orphan-pid")
        $orphan = Read-Pid -Path "$cycle.orphan-pid"
        $ids += $orphan
        Assert-Equal -Expected 0 -Actual $result.ExitCode -Because ("the cycle's exit code: " + $result.Out + $result.Err)
        Assert-OrphanStopped -OrphanPid $orphan -Marker $marker -Because "the cycle's grandchild"
        $text = Read-Log -Path $log
        Assert-True -Condition ($text -match "\b$orphan\b") -Because "the tick's log names the orphan's pid ${orphan}: $text"
        Assert-True -Condition ($text.Contains((Join-Path $work "orphan.ps1"))) -Because "the tick's log carries the orphan's command line: $text"
        Assert-True -Condition ($text -match "powershell") -Because "the tick's log names the orphan's process: $text"
        $report = ($days | ForEach-Object { [System.IO.File]::ReadAllText((Join-Path $reports "d$_.md")) }) -join "`n"
        Assert-True -Condition ($report -match "\b$orphan\b" -and $report.Contains((Join-Path $work "orphan.ps1"))) -Because "the cycle's report has an orphan line: $report"
    }
    finally { Stop-ById -Ids $ids; Remove-Work -Work $work }
}

Test-Case "(2) a feeder that leaves a never-ending grandchild: the tick still runs the cycle and exits, the grandchild is stopped and named" {
    $work = New-Work
    $ids = @()
    try {
        $marker = Join-Path $work "marker-feed.txt"
        $calls = Join-Path $work "calls.log"
        $feed = Join-Path $work "feed.ps1"; New-FakeScript -Path $feed -Orphan $marker -Work $work -LogFile $calls -Name "feed"
        $cycle = Join-Path $work "cycle.ps1"; New-FakeScript -Path $cycle -LogFile $calls -Name "cycle"
        $log = Join-Path $work "tick.log"
        $run = Start-Tick -Work $work -Arguments @("-FeedPath", ('"' + $feed + '"'), "-CyclePath", ('"' + $cycle + '"'), "-LogPath", ('"' + $log + '"'))
        $result = Wait-Tick -Tick $run -OrphanPidFiles @("$feed.orphan-pid")
        $orphan = Read-Pid -Path "$feed.orphan-pid"
        $ids += $orphan
        Assert-Equal -Expected 0 -Actual $result.ExitCode -Because ($result.Out + $result.Err)
        Assert-Equal -Expected "feed,cycle" -Actual ((@(Get-Content -LiteralPath $calls) | ForEach-Object { ($_ -split " ")[0] }) -join ",") -Because "the feeder, then the cycle"
        Assert-OrphanStopped -OrphanPid $orphan -Marker $marker -Because "the feeder's grandchild"
        $text = Read-Log -Path $log
        Assert-True -Condition ($text -match "\b$orphan\b" -and $text.Contains((Join-Path $work "orphan.ps1"))) -Because "the tick's log names the feeder's orphan: $text"
    }
    finally { Stop-ById -Ids $ids; Remove-Work -Work $work }
}

Test-Case "(3) a process started OUTSIDE the tick with the orphan's very command line is still alive after the tick (the boundary is the job, not the name)" {
    $work = New-Work
    $ids = @()
    try {
        $marker = Join-Path $work "marker-same.txt"
        $twin = Start-Process -FilePath $powershell -ArgumentList (Get-OrphanArguments -Work $work -Marker $marker) -NoNewWindow -PassThru
        $ids += $twin.Id
        Assert-True -Condition (Wait-Until -Seconds 30 -Condition { (Get-MarkerLength -Path $marker) -gt 0 }) -Because "the twin began writing"
        $twinLine = [string](Get-CimInstance Win32_Process -Filter "ProcessId=$($twin.Id)").CommandLine
        $feed = Join-Path $work "feed.ps1"; New-FakeScript -Path $feed
        $cycle = Join-Path $work "cycle.ps1"; New-FakeScript -Path $cycle -Orphan $marker -Work $work
        $log = Join-Path $work "tick.log"
        $run = Start-Tick -Work $work -Arguments @("-FeedPath", ('"' + $feed + '"'), "-CyclePath", ('"' + $cycle + '"'), "-LogPath", ('"' + $log + '"'))
        $result = Wait-Tick -Tick $run -OrphanPidFiles @("$cycle.orphan-pid")
        $orphan = Read-Pid -Path "$cycle.orphan-pid"
        $ids += $orphan
        Assert-Equal -Expected 0 -Actual $result.ExitCode -Because ($result.Out + $result.Err)
        Assert-True -Condition (Wait-Until -Seconds 10 -Condition { -not (Test-Alive -ProcessId $orphan) }) -Because "the tick's own orphan $orphan was stopped"
        Assert-True -Condition ($twinLine.Length -gt 0) -Because "the twin's command line was read"
        Assert-True -Condition ((Read-Log -Path $log).Contains($twinLine.Substring(0, [Math]::Min(200, $twinLine.Length)))) -Because "the orphan the tick stopped had the twin's very command line: $twinLine"
        Start-Sleep -Milliseconds 1500
        Assert-True -Condition (Test-Alive -ProcessId $twin.Id) -Because "the test's own process $($twin.Id), started outside the tick, is alive"
    }
    finally { Stop-ById -Ids $ids; Remove-Work -Work $work }
}

Test-Case "(4) while the cycle runs, nothing of it is stopped: its child keeps writing until the cycle exits by itself" {
    $work = New-Work
    $ids = @()
    try {
        $marker = Join-Path $work "marker-running.txt"
        $hold = Join-Path $work "release.flag"
        $feed = Join-Path $work "feed.ps1"; New-FakeScript -Path $feed
        $cycle = Join-Path $work "cycle.ps1"; New-FakeScript -Path $cycle -Orphan $marker -Work $work -HoldFile $hold -ExitCode 0
        $log = Join-Path $work "tick.log"
        $run = Start-Tick -Work $work -Arguments @("-FeedPath", ('"' + $feed + '"'), "-CyclePath", ('"' + $cycle + '"'), "-LogPath", ('"' + $log + '"'))
        $started = Wait-Until -Seconds 30 -Condition { (Get-MarkerLength -Path $marker) -gt 0 }
        $child = Read-Pid -Path "$cycle.orphan-pid"
        $ids += $child
        Assert-True -Condition $started -Because "the running cycle's child began writing"
        $first = Get-MarkerLength -Path $marker
        Start-Sleep -Milliseconds 3500
        $second = Get-MarkerLength -Path $marker
        Assert-True -Condition (Test-Alive -ProcessId $child) -Because "the running cycle's child $child is alive"
        Assert-True -Condition ($second -gt $first) -Because "the running cycle's child keeps writing ($first -> $second bytes)"
        Assert-True -Condition (-not $run.Process.HasExited) -Because "the tick waits for its running cycle"
        Set-Content -LiteralPath $hold -Value "go"
        $result = Wait-Tick -Tick $run -OrphanPidFiles @("$cycle.orphan-pid")
        Assert-Equal -Expected 0 -Actual $result.ExitCode -Because ($result.Out + $result.Err)
        Assert-OrphanStopped -OrphanPid $child -Marker $marker -Because "once the cycle has exited"
    }
    finally { Stop-ById -Ids $ids; Remove-Work -Work $work }
}

Test-Case "(5) the tick's exit code is the cycle's (0, 3, 7), and a failing feeder never decides whether the cycle runs" {
    $work = New-Work
    try {
        $calls = Join-Path $work "calls.log"
        $cycle = Join-Path $work "cycle.ps1"
        $feed = Join-Path $work "feed.ps1"
        $log = Join-Path $work "tick.log"
        $plan = @(@{ Cycle = 0; Feed = 1; Throw = $false }, @{ Cycle = 3; Feed = 0; Throw = $true }, @{ Cycle = 7; Feed = 5; Throw = $false })
        foreach ($step in $plan) {
            if (Test-Path -LiteralPath $calls) { Remove-Item -LiteralPath $calls -Force }
            New-FakeScript -Path $feed -ExitCode $step.Feed -Throw:$step.Throw -LogFile $calls -Name "feed"
            New-FakeScript -Path $cycle -ExitCode $step.Cycle -LogFile $calls -Name "cycle"
            $run = Start-Tick -Work $work -Arguments @("-FeedPath", ('"' + $feed + '"'), "-CyclePath", ('"' + $cycle + '"'), "-LogPath", ('"' + $log + '"'), "-MaxParallel", "6")
            $result = Wait-Tick -Tick $run
            Assert-Equal -Expected $step.Cycle -Actual $result.ExitCode -Because ("the tick's exit code is the cycle's: " + $result.Out + $result.Err)
            $seen = @(Get-Content -LiteralPath $calls)
            Assert-Equal -Expected "feed,cycle" -Actual (($seen | ForEach-Object { ($_ -split " ")[0] }) -join ",") -Because "feeder exit $($step.Feed) (throw: $($step.Throw)): the cycle ran"
            Assert-True -Condition ($seen[1] -match "-MaxParallel 6") -Because "the cycle's arguments travel: $($seen[1])"
            Assert-True -Condition ($seen[1] -notmatch "-MaxHours") -Because "no -MaxHours given: the cycle keeps its own default: $($seen[1])"
        }
    }
    finally { Remove-Work -Work $work }
}

Test-Case "(5b) -MaxHours reaches the cycle (12, and 0 = no end); without it the cycle keeps its own default" {
    # 2026-10-03: the cycle stopped dispatching at its default four hours and drained for half an
    # hour, twice, with returned work waiting beside empty seats; the scheduled tick could not
    # pass a longer limit.
    $work = New-Work
    try {
        $calls = Join-Path $work "calls.log"
        $cycle = Join-Path $work "cycle.ps1"
        $feed = Join-Path $work "feed.ps1"
        $log = Join-Path $work "tick.log"
        foreach ($hours in @("12", "0")) {
            if (Test-Path -LiteralPath $calls) { Remove-Item -LiteralPath $calls -Force }
            New-FakeScript -Path $feed -ExitCode 0 -LogFile $calls -Name "feed"
            New-FakeScript -Path $cycle -ExitCode 0 -LogFile $calls -Name "cycle"
            $run = Start-Tick -Work $work -Arguments @("-FeedPath", ('"' + $feed + '"'), "-CyclePath", ('"' + $cycle + '"'), "-LogPath", ('"' + $log + '"'), "-MaxHours", $hours)
            $result = Wait-Tick -Tick $run
            Assert-Equal -Expected 0 -Actual $result.ExitCode -Because ($result.Out + $result.Err)
            $seen = @(Get-Content -LiteralPath $calls)
            Assert-True -Condition ($seen[1] -match "-MaxHours $hours(\s|$)") -Because "-MaxHours $hours travels to the cycle: $($seen[1])"
        }
    }
    finally { Remove-Work -Work $work }
}

Test-Case "(6) no job object: the tick still runs the cycle, waits only for its process, says ONCE that leftovers stay, and exits" {
    $work = New-Work
    $ids = @()
    $saved = [Environment]::GetEnvironmentVariable($script:JobFailsVariable)
    try {
        $marker = Join-Path $work "marker-nojob.txt"
        $calls = Join-Path $work "calls.log"
        $feed = Join-Path $work "feed.ps1"; New-FakeScript -Path $feed -LogFile $calls -Name "feed"
        $cycle = Join-Path $work "cycle.ps1"; New-FakeScript -Path $cycle -Orphan $marker -Work $work -LogFile $calls -Name "cycle" -ExitCode 3
        $log = Join-Path $work "tick.log"
        [Environment]::SetEnvironmentVariable($script:JobFailsVariable, "1")
        $run = Start-Tick -Work $work -Arguments @("-FeedPath", ('"' + $feed + '"'), "-CyclePath", ('"' + $cycle + '"'), "-LogPath", ('"' + $log + '"'))
        [Environment]::SetEnvironmentVariable($script:JobFailsVariable, $saved)
        $result = Wait-Tick -Tick $run -OrphanPidFiles @("$cycle.orphan-pid")
        $orphan = Read-Pid -Path "$cycle.orphan-pid"
        $ids += $orphan
        Assert-Equal -Expected 3 -Actual $result.ExitCode -Because ("the cycle's exit code: " + $result.Out + $result.Err)
        Assert-Equal -Expected "feed,cycle" -Actual ((@(Get-Content -LiteralPath $calls) | ForEach-Object { ($_ -split " ")[0] }) -join ",") -Because "the feeder and the cycle ran"
        $text = Read-Log -Path $log
        $said = [regex]::Matches($text, "will not be stopped").Count
        Assert-Equal -Expected 1 -Actual $said -Because "the log says once that leftovers will not be stopped: $text"
        Assert-True -Condition (Test-Alive -ProcessId $orphan) -Because "without a job the tick touches nothing: the orphan $orphan is alive (the test stops it)"
    }
    finally {
        [Environment]::SetEnvironmentVariable($script:JobFailsVariable, $saved)
        Stop-ById -Ids $ids
        Remove-Work -Work $work
    }
}

function Get-CycleLine {
    # The fake cycle's one line from calls.log ("cycle <its arguments>").
    param([string]$Calls)
    $line = @(Get-Content -LiteralPath $Calls | Where-Object { $_ -like "cycle*" })
    if ($line.Count -ne 1) { throw "the fake cycle ran $($line.Count) times: $(@(Get-Content -LiteralPath $Calls) -join ' | ')" }
    return [string]$line[0]
}

# 2026-10-06: the scheduled tick never passed -TestTeam to the cycle, so the five test seats
# waited all day beside a healthy staging. The tick now asks for the round by default; it is
# not asked for when staging does not answer (a risk line, never a failure).
. (Join-Path $PSScriptRoot "lib\LoopbackJson.ps1")

Test-Case "(7) a tick with no flag, staging answering: the cycle is started with -TestTeam, and the board is not written" {
    $work = New-Work
    $stub = Start-JsonStub -Json '{"status":"ok"}' -Seconds 90
    try {
        $calls = Join-Path $work "calls.log"
        $feed = Join-Path $work "feed.ps1"; New-FakeScript -Path $feed -LogFile $calls -Name "feed"
        $cycle = Join-Path $work "cycle.ps1"; New-FakeScript -Path $cycle -LogFile $calls -Name "cycle"
        $run = Start-Tick -Work $work -Arguments @("-FeedPath", ('"' + $feed + '"'), "-CyclePath", ('"' + $cycle + '"'), "-LogPath", ('"' + (Join-Path $work "tick.log") + '"'),
            "-StagingHealthUrl", "http://127.0.0.1:$($stub.Port)/v1/system/health")
        $result = Wait-Tick -Tick $run
        Assert-Equal -Expected 0 -Actual $result.ExitCode -Because ($result.Out + $result.Err)
        $line = Get-CycleLine -Calls $calls
        Assert-True -Condition ($line -match "(^|\s)-TestTeam(\s|$)") -Because "the cycle is asked for the test round: $line"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $work "board.log"))) -Because "staging answered: nothing to say on the board"
    }
    finally { Stop-JsonStub -Stub $stub; Remove-Work -Work $work }
}

Test-Case "(8) -NoTestTeam: the cycle is started without -TestTeam; -TestTeam with -NoTestTeam is refused before anything runs" {
    $work = New-Work
    $stub = Start-JsonStub -Json '{"status":"ok"}' -Seconds 90
    try {
        $calls = Join-Path $work "calls.log"
        $feed = Join-Path $work "feed.ps1"; New-FakeScript -Path $feed -LogFile $calls -Name "feed"
        $cycle = Join-Path $work "cycle.ps1"; New-FakeScript -Path $cycle -LogFile $calls -Name "cycle"
        $common = @("-FeedPath", ('"' + $feed + '"'), "-CyclePath", ('"' + $cycle + '"'), "-LogPath", ('"' + (Join-Path $work "tick.log") + '"'),
            "-StagingHealthUrl", "http://127.0.0.1:$($stub.Port)/v1/system/health")
        $result = Wait-Tick -Tick (Start-Tick -Work $work -Arguments ($common + @("-NoTestTeam")))
        Assert-Equal -Expected 0 -Actual $result.ExitCode -Because ($result.Out + $result.Err)
        $line = Get-CycleLine -Calls $calls
        Assert-True -Condition ($line -notmatch "-TestTeam") -Because "-NoTestTeam: no test round asked for: $line"
        Remove-Item -LiteralPath $calls -Force
        $both = Wait-Tick -Tick (Start-Tick -Work $work -Arguments ($common + @("-TestTeam", "-NoTestTeam")))
        Assert-True -Condition ($both.ExitCode -ne 0) -Because "both flags: the tick refuses ($($both.Out)$($both.Err))"
        Assert-True -Condition (-not (Test-Path -LiteralPath $calls)) -Because "both flags: neither the feeder nor the cycle ran"
    }
    finally { Stop-JsonStub -Stub $stub; Remove-Work -Work $work }
}

Test-Case "(9) every other forwarded switch reaches the cycle unchanged, with or without the test round" {
    $work = New-Work
    $stub = Start-JsonStub -Json '{"status":"ok"}' -Seconds 90
    try {
        $calls = Join-Path $work "calls.log"
        $feed = Join-Path $work "feed.ps1"; New-FakeScript -Path $feed -LogFile $calls -Name "feed"
        $cycle = Join-Path $work "cycle.ps1"; New-FakeScript -Path $cycle -LogFile $calls -Name "cycle"
        $reports = Join-Path $work "reports"
        $token = Join-Path $work "queue.token"
        $common = @("-FeedPath", ('"' + $feed + '"'), "-CyclePath", ('"' + $cycle + '"'), "-LogPath", ('"' + (Join-Path $work "tick.log") + '"'),
            "-ReportsRoot", ('"' + $reports + '"'), "-StagingHealthUrl", "http://127.0.0.1:$($stub.Port)/v1/system/health",
            "-MaxParallel", "6", "-MaxUsd", "2.5", "-CycleMinutes", "30", "-MaxHours", "12", "-Research", "-DailyId",
            "-ResearchEveryHours", "6", "-Base", "team/nightly/lead", "-QueueUrl", "https://queue.invalid", "-QueueToken", ('"' + $token + '"'))
        # What the tick passed before the test round existed, word for word.
        $before = "cycle -MaxUsd 2.5 -MaxParallel 6 -CycleMinutes 30 -MaxHours 12 -Research -DailyId -ResearchEveryHours 6 -Base team/nightly/lead -QueueUrl https://queue.invalid -QueueToken $token"
        $result = Wait-Tick -Tick (Start-Tick -Work $work -Arguments ($common + @("-NoTestTeam")))
        Assert-Equal -Expected 0 -Actual $result.ExitCode -Because ($result.Out + $result.Err)
        Assert-Equal -Expected $before -Actual (Get-CycleLine -Calls $calls) -Because "-NoTestTeam: the cycle's arguments are what they always were"
        Remove-Item -LiteralPath $calls -Force
        $result = Wait-Tick -Tick (Start-Tick -Work $work -Arguments $common)
        Assert-Equal -Expected 0 -Actual $result.ExitCode -Because ($result.Out + $result.Err)
        $line = Get-CycleLine -Calls $calls
        Assert-Equal -Expected 1 -Actual ([regex]::Matches($line, "(^|\s)-TestTeam(?=\s|$)").Count) -Because "one -TestTeam: $line"
        Assert-Equal -Expected $before -Actual ($line -replace " -TestTeam(?=\s|$)", "") -Because "with the test round, every other argument is unchanged"
    }
    finally { Stop-JsonStub -Stub $stub; Remove-Work -Work $work }
}

Test-Case "(10) staging not answering: the cycle runs without -TestTeam, exits as the cycle did, and the board, the log and the report carry a risk line" {
    $work = New-Work
    try {
        $calls = Join-Path $work "calls.log"
        $feed = Join-Path $work "feed.ps1"; New-FakeScript -Path $feed -LogFile $calls -Name "feed"
        $cycle = Join-Path $work "cycle.ps1"; New-FakeScript -Path $cycle -LogFile $calls -Name "cycle"
        $log = Join-Path $work "tick.log"
        $reports = Join-Path $work "reports"
        [void](New-Item -ItemType Directory -Force -Path $reports)
        $days = @((Get-Date).ToString("yyyyMMdd"), (Get-Date).AddMinutes(2).ToString("yyyyMMdd")) | Select-Object -Unique
        foreach ($day in $days) { Set-Content -LiteralPath (Join-Path $reports "d$day.md") -Encoding UTF8 -Value "# report d$day" }
        $url = "http://127.0.0.1:$(Get-ClosedPort)/v1/system/health"
        # The scheduled task has no PAGENTOS_TEAM_URL: the board's address is the queue the task names.
        $token = Join-Path $work "queue.token"
        $run = Start-Tick -Work $work -Arguments @("-FeedPath", ('"' + $feed + '"'), "-CyclePath", ('"' + $cycle + '"'), "-LogPath", ('"' + $log + '"'),
            "-DailyId", "-ReportsRoot", ('"' + $reports + '"'), "-StagingHealthUrl", $url,
            "-QueueUrl", "https://queue.invalid", "-QueueToken", ('"' + $token + '"'))
        $result = Wait-Tick -Tick $run
        Assert-Equal -Expected 0 -Actual $result.ExitCode -Because ("a risk, not a failure: " + $result.Out + $result.Err)
        $line = Get-CycleLine -Calls $calls
        Assert-True -Condition ($line -notmatch "-TestTeam") -Because "staging down: no test round asked for: $line"
        $board = Read-Log -Path (Join-Path $work "board.log")
        Assert-True -Condition ($board -match "^post " -and $board -match "-Seat test-lead" -and $board -match "-Kind bilgi" -and $board.Contains($url)) -Because "one board note names staging: $board"
        Assert-True -Condition ($board.Contains("-Url https://queue.invalid") -and $board.Contains("-TokenFile $token")) -Because "the note goes to the queue's board, not to an unset PAGENTOS_TEAM_URL: $board"
        Assert-True -Condition ((Read-Log -Path $log).Contains($url)) -Because "the tick's log names staging: $(Read-Log -Path $log)"
        $report = ($days | ForEach-Object { [System.IO.File]::ReadAllText((Join-Path $reports "d$_.md")) }) -join "`n"
        Assert-True -Condition ($report.Contains($url) -and $report -match "(?m)^## Riskler") -Because "the cycle's report has a risk line: $report"
    }
    finally { Remove-Work -Work $work }
}

Test-Case "(11) a machine service the cycle started is never a leftover: Docker Desktop and WSL stay, a stray child does not (2026-10-06 06:46 and 14:58)" {
    . (Join-Path $repoRoot "scripts\lib\TeamTickKeep.ps1")
    foreach ($kept in @(
            @("Docker Desktop.exe", ""), @("com.docker.backend.exe", ""), @("com.docker.build.exe", ""), @("wsl.exe", "wsl.exe -d docker-desktop -e /usr/bin/vpnkit-bridge"),
            @("wslhost.exe", ""), @("vmmemWSL", ""), @("DOCKER DESKTOP.EXE", ""), @("node.exe", "C:\Program Files\Docker\Docker\resources\x.js"))) {
        if (-not (Test-TeamTickKeep -Name $kept[0] -CommandLine $kept[1])) { throw "'$($kept[0])' would be stopped" }
    }
    foreach ($stopped in @(@("tail.exe", "tail -f x.log"), @("python.exe", "python -m pytest"), @("powershell.exe", "powershell -File cycle.ps1"), @("wsl-like.exe", ""))) {
        if (Test-TeamTickKeep -Name $stopped[0] -CommandLine $stopped[1]) { throw "'$($stopped[0])' would be kept" }
    }
    $source = [System.IO.File]::ReadAllText($tick)
    if ($source -notmatch 'Test-TeamTickKeep -Name') { throw "Stop-JobLeftovers does not ask Test-TeamTickKeep" }
    if ($source -notmatch 'if \(-not \$keeping\) \{ \[void\]\[PagentOS\.Team\.TickJob\]::TerminateJobObject') { throw "the job is ended even when a kept service is in it" }
}

Write-Host ""

Test-Case "(11) a machine service the cycle started is never a leftover: Docker Desktop and WSL stay, a stray child does not (2026-10-06 06:46 and 14:58)" {
    . (Join-Path $repoRoot "scripts\lib\TeamTickKeep.ps1")
    foreach ($kept in @(
            @("Docker Desktop.exe", ""), @("com.docker.backend.exe", ""), @("com.docker.build.exe", ""), @("wsl.exe", "wsl.exe -d docker-desktop -e /usr/bin/vpnkit-bridge"),
            @("wslhost.exe", ""), @("vmmemWSL", ""), @("DOCKER DESKTOP.EXE", ""), @("node.exe", "C:\Program Files\Docker\Docker\resources\x.js"))) {
        if (-not (Test-TeamTickKeep -Name $kept[0] -CommandLine $kept[1])) { throw "'$($kept[0])' would be stopped" }
    }
    foreach ($stopped in @(@("tail.exe", "tail -f x.log"), @("python.exe", "python -m pytest"), @("powershell.exe", "powershell -File cycle.ps1"), @("wsl-like.exe", ""))) {
        if (Test-TeamTickKeep -Name $stopped[0] -CommandLine $stopped[1]) { throw "'$($stopped[0])' would be kept" }
    }
    $source = [System.IO.File]::ReadAllText($tick)
    if ($source -notmatch 'Test-TeamTickKeep -Name') { throw "Stop-JobLeftovers does not ask Test-TeamTickKeep" }
    if ($source -notmatch 'if \(-not \$keeping\) \{ \[void\]\[PagentOS\.Team\.TickJob\]::TerminateJobObject') { throw "the job is ended even when a kept service is in it" }
}
Write-Host "team-tick: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
