<#
.SYNOPSIS
    Is a run working or stuck? (pm-stuck-run-check: scripts/lib/TeamLiveness.ps1 and
    Restart-TeamRun in scripts/lib/TeamRun.ps1.)

.DESCRIPTION
    The owner, 2026-10-04: "Proje yöneticisine söyle arada gerçekten işte çalışıp
    çalışmadıklarını da kontrol etsin, iş takılmış olmasın." Liveness is MEASURED from cheap,
    local signs: the newest write in the run's temp folder and worktree, its output length, and
    the CPU its process tree used since the last look. The clock is injected, so four hours of
    looks take no time; the process cases (restart, the real process table) start real
    Windows PowerShell children and wait on what they wrote, never on a number of seconds.

    Run: powershell -NoProfile -File scripts\tests\team-liveness.tests.ps1 [-Filter <regex>]
#>

[CmdletBinding()]
param([string]$Filter = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ps5 = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
$libPath = Join-Path $repoRoot "scripts\lib\TeamLiveness.ps1"
$script:LibLoaded = $false
if (Test-Path -LiteralPath $libPath) { . $libPath; $script:LibLoaded = $true }

$script:Failures = 0
$script:Passes = 0
$script:TempRoot = Join-Path $env:TEMP ("pagentos-liveness-tests-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
[void](New-Item -ItemType Directory -Path $script:TempRoot -Force)
$script:Started = New-Object System.Collections.ArrayList

function Stop-Started {
    foreach ($p in @($script:Started.ToArray())) {
        try { if (-not $p.HasExited) { Stop-TeamProcessTree -ProcessId $p.Id } } catch { }
    }
    $script:Started.Clear()
}

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    if ($Filter -and $Name -notmatch $Filter) { return }
    try {
        if (-not $script:LibLoaded) { throw "scripts\lib\TeamLiveness.ps1 does not exist" }
        & $Body
        $script:Passes++; Write-Host "  PASS  $Name"
    }
    catch {
        $script:Failures++
        Write-Host "  FAIL  $Name" -ForegroundColor Red
        Write-Host "        $($_.Exception.Message)" -ForegroundColor Red
    }
    finally { Stop-Started }
}

function Assert-Equal {
    param($Expected, $Actual, [string]$Because)
    if ($Expected -ne $Actual) { throw "$Because`n          expected: <$Expected>`n          actual  : <$Actual>" }
}

function Assert-True {
    param([bool]$Condition, [string]$Because)
    if (-not $Condition) { throw $Because }
}

function New-CaseDir {
    $d = Join-Path $script:TempRoot ("c-" + [guid]::NewGuid().ToString("N").Substring(0, 10))
    [void](New-Item -ItemType Directory -Path $d -Force)
    return $d
}

function Set-Written {
    <# A file whose last write is $At: what a run did, without waiting for it to do it. #>
    param([string]$Path, [datetime]$At)
    if (-not (Test-Path -LiteralPath $Path)) { [System.IO.File]::WriteAllText($Path, "x") }
    [System.IO.File]::SetLastWriteTimeUtc($Path, $At)
    $dir = Split-Path -Parent $Path
    [System.IO.Directory]::SetLastWriteTimeUtc($dir, [datetime]::new(2000, 1, 1, 0, 0, 0, [System.DateTimeKind]::Utc))
}

function New-Row {
    param([int]$Id, [int]$Parent, [string]$Name, [double]$Cpu, [datetime]$Created)
    return [pscustomobject]@{ Id = $Id; ParentId = $Parent; Name = $Name; CpuSeconds = $Cpu; Created = $Created }
}

$t0 = [datetime]::new(2026, 10, 4, 11, 41, 0, [System.DateTimeKind]::Utc)
$old = [datetime]::new(2000, 1, 1, 0, 0, 0, [System.DateTimeKind]::Utc)

Test-Case "a run that writes every few seconds is never flagged, four hours long" {
    $temp = New-CaseDir
    $state = New-TeamLivenessState -Since $t0
    $flagged = 0
    for ($minute = 1; $minute -le 240; $minute++) {
        $now = $t0.AddMinutes($minute)
        Set-Written -Path (Join-Path $temp "pytest-of-x.txt") -At $now.AddSeconds(-5)
        $look = Update-TeamRunLiveness -State $state -Now $now -Paths @($temp) -ProcessTable @() -IdleMinutes 30
        if ($look.Stuck) { $flagged++ }
        Assert-True ($look.IdleMinutes -le 1) "minute ${minute}: idle $($look.IdleMinutes)"
    }
    Assert-Equal 0 $flagged "a run that writes is never 'takılmış olabilir'"
}

Test-Case "a run that stops writing is flagged at run_idle_minutes, not a minute before" {
    $temp = New-CaseDir
    Set-Written -Path (Join-Path $temp "last.txt") -At $t0
    $state = New-TeamLivenessState -Since $t0.AddMinutes(-5)
    $at29 = Update-TeamRunLiveness -State $state -Now $t0.AddMinutes(29).AddSeconds(59) -Paths @($temp) -ProcessTable @() -IdleMinutes 30
    Assert-True (-not $at29.Stuck) "29:59 idle is not stuck (idle $($at29.IdleMinutes))"
    Assert-Equal 29 $at29.IdleMinutes "whole minutes"
    Assert-Equal (Get-TeamTimestamp -Now $t0) $at29.LastActivityAt "the last sign of life is the last write"
    $at30 = Update-TeamRunLiveness -State $state -Now $t0.AddMinutes(30) -Paths @($temp) -ProcessTable @() -IdleMinutes 30
    Assert-True $at30.Stuck "30 minutes with no sign of life is 'takılmış olabilir'"
    Assert-Equal 30 $at30.IdleMinutes "idle minutes"
    $other = Update-TeamRunLiveness -State (New-TeamLivenessState -Since $t0) -Now $t0.AddMinutes(30) -Paths @($temp) -ProcessTable @() -IdleMinutes 45
    Assert-True (-not $other.Stuck) "the bound is the setting's"
}

Test-Case "node_modules, .venv and .git do not count as the run's writes" {
    $tree = New-CaseDir
    foreach ($skip in @("node_modules", ".venv", ".git")) {
        $d = Join-Path $tree "app\$skip"
        [void](New-Item -ItemType Directory -Force -Path $d)
        [System.IO.File]::WriteAllText((Join-Path $d "f.txt"), "x")
        [System.IO.File]::SetLastWriteTimeUtc((Join-Path $d "f.txt"), $t0.AddHours(1))
        [System.IO.Directory]::SetLastWriteTimeUtc($d, $t0.AddHours(1))
    }
    [System.IO.File]::WriteAllText((Join-Path $tree "app\code.py"), "x")
    [System.IO.File]::SetLastWriteTimeUtc((Join-Path $tree "app\code.py"), $t0)
    foreach ($d in @((Join-Path $tree "app"), $tree)) { [System.IO.Directory]::SetLastWriteTimeUtc($d, $old) }
    Assert-Equal $t0 (Get-TeamNewestWrite -Path $tree) "the newest write outside the skipped folders"
    Assert-True ($null -eq (Get-TeamNewestWrite -Path (Join-Path $tree "missing"))) "a missing folder is no write"
}

Test-Case "the output growing is a sign of life; the same length is not" {
    $state = New-TeamLivenessState -Since $t0
    $a = Update-TeamRunLiveness -State $state -Now $t0.AddMinutes(20) -OutputLength 100 -ProcessTable @() -IdleMinutes 30
    Assert-Equal 0 $a.IdleMinutes "the first length seen is a sign (it grew from nothing)"
    $b = Update-TeamRunLiveness -State $state -Now $t0.AddMinutes(50) -OutputLength 100 -ProcessTable @() -IdleMinutes 30
    Assert-True $b.Stuck "no growth for 30 minutes"
    $c = Update-TeamRunLiveness -State $state -Now $t0.AddMinutes(51) -OutputLength 101 -ProcessTable @() -IdleMinutes 30
    Assert-True (-not $c.Stuck) "it grew"
}

Test-Case "CPU of the run's tree is a sign of life only above a share of the time" {
    $state = New-TeamLivenessState -Since $t0
    $busy = 0.0; $idleCpu = 0.0
    for ($minute = 1; $minute -le 40; $minute++) {
        $busy += 30.0  # half a core: a test suite at work
        $table = @((New-Row 100 1 "claude.exe" 5 $t0), (New-Row 200 100 "bash.exe" 0.1 $t0), (New-Row 300 200 "python.exe" $busy $t0))
        $look = Update-TeamRunLiveness -State $state -Now $t0.AddMinutes($minute) -RootProcessId 100 -ProcessTable $table -IdleMinutes 30
        Assert-True (-not $look.Stuck) "minute ${minute}: a busy tree is alive"
    }
    $quiet = New-TeamLivenessState -Since $t0
    for ($minute = 1; $minute -le 30; $minute++) {
        $idleCpu += 2.0  # one CPU second per 30 s: the 2026-10-04 stuck python
        $table = @((New-Row 100 1 "claude.exe" 5 $t0), (New-Row 200 100 "bash.exe" 0.1 $t0), (New-Row 300 200 "python.exe" $idleCpu $t0))
        $look = Update-TeamRunLiveness -State $quiet -Now $t0.AddMinutes($minute) -RootProcessId 100 -ProcessTable $table -IdleMinutes 30
    }
    Assert-True $look.Stuck "a tree at 3% of a core for 30 minutes shows no life"
    $other = @((New-Row 999 1 "python.exe" 9999 $t0))
    $alone = New-TeamLivenessState -Since $t0
    [void](Update-TeamRunLiveness -State $alone -Now $t0.AddMinutes(1) -RootProcessId 100 -ProcessTable (@((New-Row 100 1 "claude.exe" 1 $t0)) + $other) -IdleMinutes 30)
    $other = @((New-Row 999 1 "python.exe" 99999 $t0))
    $l = Update-TeamRunLiveness -State $alone -Now $t0.AddMinutes(30) -RootProcessId 100 -ProcessTable (@((New-Row 100 1 "claude.exe" 1 $t0)) + $other) -IdleMinutes 30
    Assert-True $l.Stuck "another run's busy process is not this run's life"
}

Test-Case "a stuck child: the run writes, its test python sits idle - the child is flagged, the topmost one" {
    $temp = New-CaseDir
    $state = New-TeamLivenessState -Since $t0
    $cpu = 0.0
    $look = $null
    for ($minute = 1; $minute -le 31; $minute++) {
        $now = $t0.AddMinutes($minute)
        Set-Written -Path (Join-Path $temp "alive.txt") -At $now
        $cpu += 2.0
        $table = @(
            (New-Row 100 1 "claude.exe" (10 + $minute) $t0),
            (New-Row 101 100 "conhost.exe" 0 $t0),
            (New-Row 200 100 "bash.exe" 0.2 $t0.AddMinutes(1)),
            (New-Row 210 200 "uv.exe" 0.3 $t0.AddMinutes(1)),
            (New-Row 300 210 "python.exe" $cpu $t0.AddMinutes(1)),
            (New-Row 400 100 "bash.exe" 0.1 $t0.AddMinutes(1)),
            (New-Row 410 400 "node.exe" (60.0 * $minute) $t0.AddMinutes(1))
        )
        $look = Update-TeamRunLiveness -State $state -Now $now -Paths @($temp) -RootProcessId 100 -ProcessTable $table -IdleMinutes 30
    }
    Assert-True (-not $look.Stuck) "the run itself shows life"
    $children = @($look.StuckChildren)
    Assert-Equal 1 @($children).Count "one stuck child: $(@($children | ForEach-Object { $_.pid }) -join ',')"
    Assert-Equal 200 ([int]$children[0].pid) "the topmost idle tool process (its python inside), not conhost, not the busy node"
    Assert-Equal "bash.exe" ([string]$children[0].name) "named"
    Assert-True ([int]$children[0].idle_minutes -ge 30) "idle minutes"
}

Test-Case "a pid reused by a process older than its 'parent' is not the run's child" {
    $table = @((New-Row 100 1 "claude.exe" 1 $t0), (New-Row 200 100 "bash.exe" 1 $t0.AddMinutes(-10)), (New-Row 300 100 "bash.exe" 1 $t0.AddMinutes(1)))
    $ids = @(Get-TeamDescendants -ProcessTable $table -RootProcessId 100 | ForEach-Object { [int]$_.Id })
    Assert-Equal "300" ($ids -join ",") "only a child started after its parent"
}

Test-Case "the real process table knows this process and its parent" {
    $table = @(Get-TeamProcessTable)
    $me = @($table | Where-Object { $_.Id -eq $PID })
    Assert-Equal 1 @($me).Count "this process is in the table"
    Assert-True ($me[0].CpuSeconds -gt 0) "with its CPU"
    $child = Start-Process -FilePath $ps5 -ArgumentList @("-NoProfile", "-Command", "Start-Sleep -Seconds 60") -PassThru -WindowStyle Hidden
    [void]$script:Started.Add($child)
    $table = @(Get-TeamProcessTable)
    $ids = @(Get-TeamDescendants -ProcessTable $table -RootProcessId $PID | ForEach-Object { [int]$_.Id })
    Assert-True ($ids -contains $child.Id) "a child started here is a descendant"
}

Test-Case "the ladder, one injected clock: PM's duty at 30, a restart at 90, escalate the second time" {
    $temp = New-CaseDir
    Set-Written -Path (Join-Path $temp "x.txt") -At $old
    $run = [pscustomobject]@{ Restarts = 0; Handed = $false; State = (New-TeamLivenessState -Since $t0) }
    $log = New-Object System.Collections.ArrayList
    for ($minute = 1; $minute -le 200; $minute++) {
        $now = $t0.AddMinutes($minute)
        $look = Update-TeamRunLiveness -State $run.State -Now $now -Paths @($temp) -ProcessTable @() -IdleMinutes 30
        $action = Get-TeamStuckAction -IdleMinutes $look.IdleMinutes -Bound 30 -AutoMinutes 90 -Restarts $run.Restarts -Handed $run.Handed
        if ($action -eq "none" -or $action -eq "wait") { continue }
        [void]$log.Add("${minute}:$action")
        if ($action -eq "duty") { $run.Handed = $true }
        if ($action -eq "restart") { $run.Restarts++; $run.Handed = $false; $run.State = New-TeamLivenessState -Since $now }
        if ($action -eq "escalate") { break }
    }
    Assert-Equal "30:duty,90:restart,120:duty,180:escalate" ($log -join ",") "the ladder"
    Assert-Equal "none" (Get-TeamStuckAction -IdleMinutes 29 -Bound 30 -AutoMinutes 90 -Restarts 0 -Handed $false) "29 is not stuck"
    Assert-Equal "restart" (Get-TeamStuckAction -IdleMinutes 90 -Bound 30 -AutoMinutes 90 -Restarts 0 -Handed $true) "90 restarts once"
    Assert-Equal "wait" (Get-TeamStuckAction -IdleMinutes 89 -Bound 30 -AutoMinutes 90 -Restarts 0 -Handed $true) "with the PM, under 90"
}

Test-Case "run_idle_minutes: the setting, else 30" {
    $dir = New-CaseDir
    $path = Join-Path $dir "cycle-settings.json"
    Assert-Equal 30 (Read-TeamRunIdleMinutes -Path $path) "no file"
    [System.IO.File]::WriteAllText($path, '{"max_parallel": 4}')
    Assert-Equal 30 (Read-TeamRunIdleMinutes -Path $path) "no field"
    [System.IO.File]::WriteAllText($path, '{"run_idle_minutes": 45}')
    Assert-Equal 45 (Read-TeamRunIdleMinutes -Path $path) "the field"
    foreach ($bad in @('0', '-3', '"ten"', '100000', '2.5')) {
        [System.IO.File]::WriteAllText($path, '{"run_idle_minutes": ' + $bad + '}')
        Assert-Equal 30 (Read-TeamRunIdleMinutes -Path $path) "a bad value ($bad) is the default"
    }
}

Test-Case "the PM's stuck-run decisions: wait, restart or escalate, each with a reason, for a listed run" {
    $ok = ConvertFrom-Json '[{"run":"a-task/worker","action":"wait","reason":"uzun test yazıyor"},{"run":"b-task/inspector","action":"restart","reason":"30 dk iz yok"}]'
    Assert-Equal 0 @(Test-TeamStuckDecisions -Decisions $ok -Listed @("a-task/worker", "b-task/inspector")).Count "sound"
    $bad = ConvertFrom-Json '[{"run":"c-task/worker","action":"kill","reason":""},{"run":"a-task/worker","action":"wait","reason":"x"},{"run":"a-task/worker","action":"wait","reason":"y"}]'
    $problems = @(Test-TeamStuckDecisions -Decisions $bad -Listed @("a-task/worker"))
    Assert-True (($problems -join "|") -match "c-task/worker: not one of") "unlisted: $($problems -join '|')"
    Assert-True (($problems -join "|") -match "'kill' is not an action") "unknown action"
    Assert-True (($problems -join "|") -match "the reason is empty") "empty reason"
    Assert-True (($problems -join "|") -match "more than one decision") "twice"
    $long = ConvertFrom-Json ('[{"run":"a-task/worker","action":"escalate","reason":"' + ("a" * 1201) + '"}]')
    Assert-Equal 1 @(Test-TeamStuckDecisions -Decisions $long -Listed @("a-task/worker")).Count "a reason over 1200"
}

Test-Case "the duty card lists the stuck runs and the shape of the answer; without them it is as before" {
    $task = [pscustomobject]@{ id = "s-task"; title = "T"; state = "stopped"; area = @("a"); reports = @(); reason = "r" }
    $plain = New-TeamDutyCard -Tasks @($task) -CycleId "c1" -DutyFile "team/plans/c1-duty-1.json"
    Assert-True ($plain -notmatch "Takılmış") "no stuck section without stuck runs"
    $stuck = [pscustomobject]@{ run = "g-task/worker"; task = "g-task"; role = "worker"; idle_minutes = 34; last_activity_at = "2026-10-04T13:29:00Z"; restarts = 0; child = "python.exe (pid 300), 140 dk iz yok" }
    $card = New-TeamDutyCard -Tasks @($task) -CycleId "c1" -DutyFile "team/plans/c1-duty-1.json" -StuckRuns @($stuck)
    Assert-True ($card -match "## Takılmış olabilecek koşular") "the section"
    Assert-True ($card -match "g-task/worker: 34 dk iz yok") "the run and its idle minutes"
    Assert-True ($card -match "python.exe \(pid 300\)") "its stuck child"
    Assert-True ($card -match '"stuck"' -and $card -match '"wait"' -and $card -match '"restart"' -and $card -match '"escalate"') "the answer's shape"
    $only = New-TeamDutyCard -Tasks @() -CycleId "c1" -DutyFile "team/plans/c1-duty-1.json" -StuckRuns @($stuck)
    Assert-True ($only -match "g-task/worker") "a duty card with stuck runs only"
}

function Start-FakeRun {
    <# A fake claude: it reads its card, starts a child of its own and sleeps; the pid files are
       the markers the case waits on. #>
    param([string]$Dir, [string]$Name)
    $script = Join-Path $Dir "fake-$Name.ps1"
    $body = @'
$card = [Console]::In.ReadToEnd()
$child = Start-Process -FilePath "__PS5__" -ArgumentList @("-NoProfile", "-Command", "Start-Sleep -Seconds 300") -PassThru -WindowStyle Hidden
Set-Content -LiteralPath (Join-Path (Get-Location) "__NAME__-$PID.child") -Value $child.Id
Add-Content -LiteralPath (Join-Path (Get-Location) "__NAME__.starts") -Value $PID
Start-Sleep -Seconds 300
'@
    $body = $body.Replace("__PS5__", $ps5).Replace("__NAME__", $Name)
    [System.IO.File]::WriteAllText($script, $body)
    $run = Start-TeamRun -FilePath $ps5 -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $script) -Prompt "card $Name" -WorkingDirectory $Dir
    [void]$script:Started.Add($run.Process)
    return $run
}

function Wait-ChildFile {
    param([string]$Dir, [string]$Name, [int]$RunPid)
    $file = Join-Path $Dir "$Name-$RunPid.child"
    $guard = [datetime]::UtcNow.AddSeconds(60)
    while (-not (Test-Path -LiteralPath $file) -or -not ([string](Get-Content -LiteralPath $file -Raw)).Trim()) {
        if ([datetime]::UtcNow -gt $guard) { throw "hang guard: $Name never started its child" }
        Start-Sleep -Milliseconds 100
    }
    $child = [int]([string](Get-Content -LiteralPath $file -Raw)).Trim()
    $script:Started.Add((Get-Process -Id $child)) | Out-Null
    return $child
}

function Test-Alive { param([int]$Id) return ($null -ne (Get-Process -Id $Id -ErrorAction SilentlyContinue)) }

Test-Case "restart stops exactly that run's tree and starts it again from its worktree; the commits stay" {
    $repo = New-CaseDir
    $git = Get-TeamGit
    [void](Invoke-NativeProcess -FilePath $git -Arguments @("init", "-q") -WorkingDirectory $repo)
    [System.IO.File]::WriteAllText((Join-Path $repo "work.txt"), "done so far")
    [void](Invoke-NativeProcess -FilePath $git -Arguments @("add", "work.txt") -WorkingDirectory $repo)
    [void](Invoke-NativeProcess -FilePath $git -Arguments @("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "work so far") -WorkingDirectory $repo)
    $head = (Invoke-NativeProcess -FilePath $git -Arguments @("rev-parse", "HEAD") -WorkingDirectory $repo).StdOut.Trim()
    $sibling = New-CaseDir

    $a = Start-FakeRun -Dir $repo -Name "a"
    $b = Start-FakeRun -Dir $sibling -Name "b"
    $aChild = Wait-ChildFile -Dir $repo -Name "a" -RunPid $a.Process.Id
    $bChild = Wait-ChildFile -Dir $sibling -Name "b" -RunPid $b.Process.Id
    $oldPid = $a.Process.Id

    $again = Restart-TeamRun -Run $a
    [void]$script:Started.Add($again.Process)
    Assert-True (-not (Test-Alive $oldPid)) "the stuck run is gone"
    Assert-True (-not (Test-Alive $aChild)) "with its child"
    Assert-True ((Test-Alive $b.Process.Id) -and (Test-Alive $bChild)) "the sibling run and its child live on"
    $newChild = Wait-ChildFile -Dir $repo -Name "a" -RunPid $again.Process.Id
    Assert-True (Test-Alive $newChild) "the run started again, in its worktree, with its card"
    Assert-Equal 2 @(Get-Content -LiteralPath (Join-Path $repo "a.starts")).Count "started twice"
    Assert-Equal $head (Invoke-NativeProcess -FilePath $git -Arguments @("rev-parse", "HEAD") -WorkingDirectory $repo).StdOut.Trim() "the commits survive"
    Assert-True ($again.Launch.Prompt -ceq "card a") "the same card"
}

Stop-Started
try { Remove-Item -LiteralPath $script:TempRoot -Recurse -Force -ErrorAction Stop } catch { Write-Host "  (temp folder left: $script:TempRoot)" }

Write-Host ""
Write-Host "team-liveness tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
