<#
.SYNOPSIS
    The test queue (test sırası): an agent asks before a heavy run and is answered ONAY or
    BEKLE (scripts/lib/TeamTestSlots.ps1, scripts/team/test-slot.ps1, the gate's use of it).

.DESCRIPTION
    The owner's idea of 2026-10-02: an agent about to run a heavy test tells the others, and
    the others go on by "onay" or "bekle". Every case has its own store folder under %TEMP%;
    nothing here touches the machine's real queue. Waits are hang guards only: each case
    waits for a marker a real process wrote, never for a number of seconds.

    The cases, by number (the card's acceptance list):
      1  ONAY, then BEKLE naming the holder, then ONAY when the holder's command ends;
      2  heavy admits three real wrappers and the fourth waits;
      3  first come, first served whatever order the waiters ask again in;
      4  the gate goes ahead of the waiting, never ahead of the running;
      5  database + heavy waits holding nothing;
      6  a killed wrapper, and a recycled pid, free the slot;
      7  an unused ticket is void, a forgotten place is dropped (injected clock);
      8  run passes exit codes and both streams through, and 100 000 lines do not block;
      9  two asks at the same instant for the last slot: one ONAY, twenty rounds;
      10 a half-written entry and a missing store are tolerated;
      11 status speaks Turkish; the log has one line per finished run (run as `& script`);
      12 the role files name the rule, and their example commands pass -DryRun;
      13 quality-gate.ps1 asks before a heavy step, waits, and -NoTestSlots asks nothing;
      14 the board: a take, a BEKLE and a freed slot are one bilgi note each (a fake board);
      15 the board down never blocks or changes ONAY/BEKLE;
      16 `who` speaks Turkish by seat name;
      17 the notes' names and bounds.

    Run: powershell -NoProfile -File scripts\tests\team-test-slots.tests.ps1 [-Filter <regex>]
#>

[CmdletBinding()]
param(
    # Runs only the cases whose name matches this pattern (the mutation proofs re-run a slice).
    [string]$Filter = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$slotScript = Join-Path $repoRoot "scripts\team\test-slot.ps1"
$gateScript = Join-Path $repoRoot "scripts\quality-gate.ps1"
$ps5 = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
$libPath = Join-Path $repoRoot "scripts\lib\TeamTestSlots.ps1"
$script:LibLoaded = $false
if (Test-Path -LiteralPath $libPath) { . $libPath; $script:LibLoaded = $true }

$script:Failures = 0
$script:Passes = 0
# This run's own board must never hear the cases: no child inherits an address, a token or a seat.
foreach ($name in @("PAGENTOS_TEAM_URL", "PAGENTOS_TEAM_TOKEN_FILE", "PAGENTOS_TEAM_SEAT")) { Remove-Item -Path ("Env:" + $name) -ErrorAction SilentlyContinue }
$script:TempRoot = Join-Path $env:TEMP ("pagentos-test-slots-tests-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
[void](New-Item -ItemType Directory -Path $script:TempRoot -Force)
$script:Children = New-Object System.Collections.ArrayList
$script:CaseDirs = New-Object System.Collections.ArrayList

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    if ($Filter -and $Name -notmatch $Filter) { return }
    try {
        if (-not $script:LibLoaded) { throw "scripts\lib\TeamTestSlots.ps1 does not exist" }
        if (-not (Test-Path -LiteralPath $slotScript)) { throw "scripts\team\test-slot.ps1 does not exist" }
        & $Body
        $script:Passes++; Write-Host "  PASS  $Name"
    }
    catch {
        $script:Failures++
        Write-Host "  FAIL  $Name" -ForegroundColor Red
        Write-Host "        $($_.Exception.Message)" -ForegroundColor Red
    }
    finally { Stop-Children }
}

function Assert-Equal {
    param($Expected, $Actual, [string]$Because)
    if ($Expected -ne $Actual) { throw "$Because`n          expected: <$Expected>`n          actual  : <$Actual>" }
}

function Assert-True {
    param([bool]$Condition, [string]$Because)
    if (-not $Condition) { throw $Because }
}

function New-Store {
    <# A store path that does NOT exist yet: the library creates it on first use. #>
    return (Join-Path $script:TempRoot ("s-" + [guid]::NewGuid().ToString("N").Substring(0, 10)))
}

function New-CaseDir {
    $d = Join-Path $script:TempRoot ("c-" + [guid]::NewGuid().ToString("N").Substring(0, 10))
    [void](New-Item -ItemType Directory -Path $d -Force)
    [void]$script:CaseDirs.Add($d)
    return $d
}

function Start-PsChild {
    <# Windows PowerShell 5.1 child with both streams captured separately (read async, so a
       chatty child never blocks on a full pipe). #>
    param([string[]]$Arguments)
    $psi = New-Object System.Diagnostics.ProcessStartInfo $ps5
    $psi.Arguments = ConvertTo-NativeArgumentLine -Arguments (@("-NoProfile", "-ExecutionPolicy", "Bypass") + $Arguments)
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $child = [pscustomobject]@{ Process = $p; Out = $p.StandardOutput.ReadToEndAsync(); Err = $p.StandardError.ReadToEndAsync() }
    [void]$script:Children.Add($child)
    return $child
}

function Start-Slot {
    param([string[]]$Arguments)
    return (Start-PsChild -Arguments (@("-File", $slotScript) + $Arguments))
}

function Wait-Child {
    <# The bound is a hang guard: a child that never ends fails the case instead of the suite hanging. #>
    param($Child, [int]$GuardMs = 120000)
    if (-not $Child.Process.WaitForExit($GuardMs)) {
        try { $Child.Process.Kill() } catch { }
        throw "hang guard: a child did not end within $GuardMs ms"
    }
    $Child.Process.WaitForExit()
    if (-not $Child.Out.Wait($GuardMs)) { throw "hang guard: a child's stdout never closed" }
    if (-not $Child.Err.Wait($GuardMs)) { throw "hang guard: a child's stderr never closed" }
    return [pscustomobject]@{ Code = $Child.Process.ExitCode; Out = [string]$Child.Out.Result; Err = [string]$Child.Err.Result }
}

function Invoke-Slot {
    param([string[]]$Arguments)
    return (Wait-Child -Child (Start-Slot -Arguments $Arguments))
}

function Stop-Children {
    # Every waiting command of a case waits on <case dir>\release: create it first, so a
    # failed case never leaves a command behind holding a pipe open (a mutation run hung on
    # exactly that: four orphaned commands kept the suite's own stdout open).
    foreach ($d in @($script:CaseDirs)) {
        try { [System.IO.File]::WriteAllText((Join-Path $d "release"), "") } catch { }
    }
    $script:CaseDirs.Clear()
    foreach ($c in @($script:Children)) {
        try { if (-not $c.Process.HasExited) { $c.Process.Kill() } } catch { }
    }
    $script:Children.Clear()
}

function Wait-Until {
    <# Polls a condition; the bound is a hang guard, never the claim. #>
    param([scriptblock]$Condition, [string]$What, [int]$GuardSeconds = 90)
    $deadline = [DateTime]::UtcNow.AddSeconds($GuardSeconds)
    while (-not (& $Condition)) {
        if ([DateTime]::UtcNow -gt $deadline) { throw "hang guard: $What did not happen within $GuardSeconds s" }
        Start-Sleep -Milliseconds 100
    }
}

function Get-Ticket {
    param($Result, [string]$Because)
    Assert-Equal 0 $Result.Code "$Because (exit code; out: $($Result.Out.Trim()) err: $($Result.Err.Trim()))"
    $m = [regex]::Match($Result.Out, '(?m)^ONAY (\S+)\s*$')
    Assert-True $m.Success "$Because - the answer is 'ONAY <ticket>': $($Result.Out)"
    return $m.Groups[1].Value
}

function Get-WaitCommand {
    <# The argument list of a command that marks it started and waits for a release file - or
       for its case folder to vanish, so the suite's own cleanup can never strand it. #>
    param([string]$Started, [string]$Release, [int]$ExitCode = 0)
    $dir = Split-Path -Parent $Release
    $text = "New-Item -ItemType File -Path '$Started' -Force | Out-Null; while ((Test-Path -LiteralPath '$dir') -and -not (Test-Path -LiteralPath '$Release')) { Start-Sleep -Milliseconds 100 }; exit $ExitCode"
    return @($ps5, "-NoProfile", "-Command", $text)
}

function Ask {
    param([string]$Store, [string]$Kind, [string]$Task, [string]$Role = "worker", [string]$What = "a heavy run", [string]$Now = "")
    $a = @("ask", "-Kind", $Kind, "-Task", $Task, "-Role", $Role, "-What", $What, "-Store", $Store)
    if ($Now) { $a += @("-NowUtc", $Now) }
    return (Invoke-Slot -Arguments $a)
}

function Assert-Bekle {
    param($Result, [int]$Position, [string]$Because)
    Assert-Equal 3 $Result.Code "$Because (exit code; out: $($Result.Out.Trim()) err: $($Result.Err.Trim()))"
    $m = [regex]::Match($Result.Out, '(?m)^BEKLE (\d+) \|')
    Assert-True $m.Success "$Because - the answer is 'BEKLE <position> | ...': $($Result.Out)"
    Assert-Equal $Position ([int]$m.Groups[1].Value) "$Because - position"
}

function Release {
    param([string]$Store, [string]$Ticket)
    $r = Invoke-Slot -Arguments @("release", "-Ticket", $Ticket, "-Store", $Store)
    Assert-Equal 0 $r.Code "release of $Ticket (err: $($r.Err.Trim()))"
}

function Get-LogLines {
    param([string]$Store)
    $path = Get-TestSlotLogPath -Store $Store
    if (-not (Test-Path -LiteralPath $path)) { return @() }
    return @([System.IO.File]::ReadAllLines($path) | Where-Object { $_.Trim() })
}

Write-Host "team-test-slots tests (PowerShell $($PSVersionTable.PSVersion))"

# ------------------------------------------------------------------------------------ 1
Test-Case "1 first ask is ONAY; a second database ask while it RUNS is BEKLE 1 naming the holder; ONAY after it ends" {
    $s = New-Store; $d = New-CaseDir
    $t1 = Get-Ticket (Ask -Store $s -Kind database -Task "task-holder" -Role inspector -What "api integration suite") "the first ask of a free kind"
    $started = Join-Path $d "started"; $release = Join-Path $d "release"
    $wrapper = Start-Slot -Arguments (@("run", "-Ticket", $t1, "-Store", $s, "--") + (Get-WaitCommand -Started $started -Release $release))
    Wait-Until { Test-Path -LiteralPath $started } "the holder's command started"
    $r = Ask -Store $s -Kind database -Task "task-waiter" -What "alembic by hand"
    Assert-Bekle $r 1 "a second database ask while the first runs"
    Assert-True ($r.Out -match 'inspector') "BEKLE names the holder's role: $($r.Out)"
    Assert-True ($r.Out -match 'task-holder') "BEKLE names the holder's task: $($r.Out)"
    Assert-True ($r.Out -match 'api integration suite') "BEKLE names the holder's -What: $($r.Out)"
    [void](New-Item -ItemType File -Path $release)
    $w = Wait-Child $wrapper
    Assert-Equal 0 $w.Code "the holder's run ended cleanly"
    [void](Get-Ticket (Ask -Store $s -Kind database -Task "task-waiter" -What "alembic by hand") "the waiter's next ask after the holder ended")
}

# ------------------------------------------------------------------------------------ 2
Test-Case "2 heavy admits three real wrappers at once and the fourth waits" {
    $s = New-Store; $d = New-CaseDir
    $driver = Join-Path $d "driver.ps1"
    $driverText = @'
param([string]$Slot, [string]$Store, [string]$Task, [string]$Dir, [string]$Ps5)
$answer = @(& $Slot ask -Kind heavy -Task $Task -Role worker -What "case two $Task" -Store $Store)
$code = $LASTEXITCODE
if ($code -eq 3) { [System.IO.File]::WriteAllText((Join-Path $Dir "$Task.bekle"), ($answer -join "`n")); exit 3 }
if ($code -ne 0) { exit 50 }
$ticket = (($answer -join " ").Trim() -split " ")[1]
$started = Join-Path $Dir "$Task.started"; $release = Join-Path $Dir "release"
$text = "New-Item -ItemType File -Path '$started' -Force | Out-Null; while ((Test-Path -LiteralPath '$Dir') -and -not (Test-Path -LiteralPath '$release')) { Start-Sleep -Milliseconds 100 }"
& $Slot run -Ticket $ticket -Store $Store -- $Ps5 -NoProfile -Command $text
exit $LASTEXITCODE
'@
    [System.IO.File]::WriteAllText($driver, $driverText)
    $kids = @()
    foreach ($n in 1..4) { $kids += Start-PsChild -Arguments @("-File", $driver, $slotScript, $s, "heavy-$n", $d, $ps5) }
    $count = { @(Get-ChildItem -LiteralPath $d -Filter "*.started").Count + @(Get-ChildItem -LiteralPath $d -Filter "*.bekle").Count }
    Wait-Until { (& $count) -ge 4 } "four answers (started or BEKLE)"
    $startedN = @(Get-ChildItem -LiteralPath $d -Filter "*.started").Count
    $bekle = @(Get-ChildItem -LiteralPath $d -Filter "*.bekle")
    Assert-Equal 3 $startedN "three heavy runs started before anything was released"
    Assert-Equal 1 @($bekle).Count "the fourth got BEKLE"
    Assert-True ([System.IO.File]::ReadAllText($bekle[0].FullName) -match '^BEKLE 1 \|') "the fourth is first in line"
    [void](New-Item -ItemType File -Path (Join-Path $d "release"))
    $codes = @($kids | ForEach-Object { (Wait-Child $_).Code } | Sort-Object)
    Assert-Equal "0 0 0 3" ($codes -join " ") "three runs ended 0, the waiter 3"
    Assert-Equal 3 @(Get-LogLines -Store $s).Count "one log line per finished run"
}

# ------------------------------------------------------------------------------------ 3
Test-Case "3 first come, first served: three waiters are granted in the order they first asked" {
    $s = New-Store
    $h = Get-Ticket (Ask -Store $s -Kind database -Task "holder") "the holder"
    Assert-Bekle (Ask -Store $s -Kind database -Task "wa") 1 "A asks first"
    Assert-Bekle (Ask -Store $s -Kind database -Task "wb") 2 "B asks second"
    Assert-Bekle (Ask -Store $s -Kind database -Task "wc") 3 "C asks third"
    # They ask again in the reverse order: the line does not change.
    Assert-Bekle (Ask -Store $s -Kind database -Task "wc") 3 "C again"
    Assert-Bekle (Ask -Store $s -Kind database -Task "wb") 2 "B again"
    Assert-Bekle (Ask -Store $s -Kind database -Task "wa") 1 "A again"
    Release -Store $s -Ticket $h
    Assert-Bekle (Ask -Store $s -Kind database -Task "wc") 3 "C asks first after the release and still waits"
    Assert-Bekle (Ask -Store $s -Kind database -Task "wb") 2 "B still waits behind A"
    $ta = Get-Ticket (Ask -Store $s -Kind database -Task "wa") "A is granted first"
    Release -Store $s -Ticket $ta
    Assert-Bekle (Ask -Store $s -Kind database -Task "wc") 2 "C behind B"
    $tb = Get-Ticket (Ask -Store $s -Kind database -Task "wb") "B is granted second"
    Release -Store $s -Ticket $tb
    [void](Get-Ticket (Ask -Store $s -Kind database -Task "wc") "C is granted third")
}

# ------------------------------------------------------------------------------------ 4
Test-Case "4 the gate goes ahead of two waiting requests and not ahead of the running one" {
    $s = New-Store
    $h = Get-Ticket (Ask -Store $s -Kind database -Task "holder" -What "the running one") "the holder"
    Assert-Bekle (Ask -Store $s -Kind database -Task "w1") 1 "W1"
    Assert-Bekle (Ask -Store $s -Kind database -Task "w2") 2 "W2"
    $g = Ask -Store $s -Kind database -Task "gate-run" -Role gate -What "API integration tests"
    Assert-Bekle $g 1 "the gate is placed first in line, but waits for the holder"
    Assert-True ($g.Out -match 'the running one') "the gate is told who holds it: $($g.Out)"
    Assert-Bekle (Ask -Store $s -Kind database -Task "w1") 2 "W1 is now behind the gate"
    Assert-Bekle (Ask -Store $s -Kind database -Task "w2") 3 "W2 is now behind the gate"
    Release -Store $s -Ticket $h
    Assert-Bekle (Ask -Store $s -Kind database -Task "w1") 2 "W1 still waits after the release: the gate is ahead"
    [void](Get-Ticket (Ask -Store $s -Kind database -Task "gate-run" -Role gate -What "API integration tests") "the gate is granted")
}

# ------------------------------------------------------------------------------------ 5
Test-Case "5 database + heavy: with database busy the ask is BEKLE and holds no heavy slot meanwhile" {
    $s = New-Store
    [void](Get-Ticket (Ask -Store $s -Kind database -Task "db-holder") "database holder")
    [void](Get-Ticket (Ask -Store $s -Kind heavy -Task "h1") "heavy 1")
    [void](Get-Ticket (Ask -Store $s -Kind heavy -Task "h2") "heavy 2")
    Assert-Bekle (Ask -Store $s -Kind "database,heavy" -Task "integration" -What "api integration suite") 1 "database busy, heavy free"
    [void](Get-Ticket (Ask -Store $s -Kind heavy -Task "third-party") "a third party's heavy ask takes the last heavy slot: the waiter holds none")
}

# ------------------------------------------------------------------------------------ 6
Test-Case "6 a killed wrapper frees its slot; a pid now owned by another process is not the holder" {
    $s = New-Store; $d = New-CaseDir
    $t = Get-Ticket (Ask -Store $s -Kind database -Task "doomed") "the doomed holder"
    $started = Join-Path $d "started"; $release = Join-Path $d "release"
    $wrapper = Start-Slot -Arguments (@("run", "-Ticket", $t, "-Store", $s, "--") + (Get-WaitCommand -Started $started -Release $release))
    Wait-Until { Test-Path -LiteralPath $started } "the doomed command started"
    Assert-Bekle (Ask -Store $s -Kind database -Task "next") 1 "while the wrapper lives the slot is held"
    $wrapper.Process.Kill()
    [void]$wrapper.Process.WaitForExit(30000)
    [void](Get-Ticket (Ask -Store $s -Kind database -Task "next") "the killed wrapper's slot is free at the next ask")
    [void](New-Item -ItemType File -Path $release)   # lets the orphaned command end

    $s2 = New-Store
    $t2 = Get-Ticket (Ask -Store $s2 -Kind database -Task "recycled") "a holder"
    # This process is alive, but the slot names a different start time: a recycled pid.
    [void](Start-TestSlotRun -Store $s2 -Ticket $t2 -HolderPid $PID -HolderStart "0123456789")
    [void](Get-Ticket (Ask -Store $s2 -Kind database -Task "next") "a slot whose pid belongs to another process is free")
    $s3 = New-Store
    $t3 = Get-Ticket (Ask -Store $s3 -Kind database -Task "alive") "a live holder"
    [void](Start-TestSlotRun -Store $s3 -Ticket $t3 -HolderPid $PID)
    Assert-Bekle (Ask -Store $s3 -Kind database -Task "next") 1 "the same pid with ITS start time is the holder"
}

# ------------------------------------------------------------------------------------ 7
Test-Case "7 an unused ticket is void after its bound and the next is granted; a forgotten place is dropped" {
    $s = New-Store
    $t0 = [datetime]::new(2026, 10, 3, 9, 0, 0, [System.DateTimeKind]::Utc)
    $at = { param([int]$Minutes) $t0.AddMinutes($Minutes).ToString("o") }
    $unused = Get-Ticket (Ask -Store $s -Kind database -Task "slow" -Now (& $at 0)) "granted, never used"
    Assert-Bekle (Ask -Store $s -Kind database -Task "next" -Now (& $at 1)) 1 "the ticket holds the slot within its bound"
    Assert-Bekle (Ask -Store $s -Kind database -Task "next" -Now (& $at 4)) 1 "still held at four minutes"
    [void](Get-Ticket (Ask -Store $s -Kind database -Task "next" -Now (& $at 6)) "past five minutes the ticket is void and the next is granted")
    $r = Invoke-Slot -Arguments @("run", "-Ticket", $unused, "-Store", $s, "-NowUtc", (& $at 6), "--", $ps5, "-NoProfile", "-Command", "exit 0")
    Assert-Equal 4 $r.Code "running a void ticket is refused with its own code (err: $($r.Err.Trim()))"

    $s2 = New-Store
    $h = Get-Ticket (Ask -Store $s2 -Kind database -Task "holder" -Now (& $at 0)) "the holder"
    [void](Start-TestSlotRun -Store $s2 -Ticket $h -HolderPid $PID -NowUtc $t0)   # a live run: never void
    Assert-Bekle (Ask -Store $s2 -Kind database -Task "forgetful" -Now (& $at 0)) 1 "the forgetful one"
    Assert-Bekle (Ask -Store $s2 -Kind database -Task "patient" -Now (& $at 0)) 2 "the patient one"
    Assert-Bekle (Ask -Store $s2 -Kind database -Task "patient" -Now (& $at 6)) 2 "within ten minutes the place is kept"
    Assert-Bekle (Ask -Store $s2 -Kind database -Task "patient" -Now (& $at 11)) 1 "after ten minutes unasked, the forgetful place is dropped"
}

# ------------------------------------------------------------------------------------ 8
Test-Case "8 run passes the exit code (0, 1, 7) and both streams through, separately, and releases each time" {
    foreach ($code in @(0, 1, 7)) {
        $s = New-Store
        $t = Get-Ticket (Ask -Store $s -Kind database -Task "code-$code") "ask"
        $text = "[Console]::Out.WriteLine('to-stdout-$code'); [Console]::Error.WriteLine('to-stderr-$code'); exit $code"
        $r = Invoke-Slot -Arguments @("run", "-Ticket", $t, "-Store", $s, "--", $ps5, "-NoProfile", "-Command", $text)
        Assert-Equal $code $r.Code "the command's exit code is the wrapper's"
        Assert-True ($r.Out -match "to-stdout-$code") "stdout passed through: <$($r.Out)>"
        Assert-True ($r.Out -notmatch "to-stderr") "stderr did not leak into stdout: <$($r.Out)>"
        Assert-True ($r.Err -match "to-stderr-$code") "stderr passed through: <$($r.Err)>"
        Assert-True ($r.Err -notmatch "to-stdout") "stdout did not leak into stderr: <$($r.Err)>"
        [void](Get-Ticket (Ask -Store $s -Kind database -Task "after-$code") "released after exit code $code")
        $log = @(Get-LogLines -Store $s)
        Assert-Equal 1 $log.Count "one log line"
        Assert-True ($log[0] -match "exit=$code(\s|$)") "the log records exit=$($code): $($log[0])"
    }
    $s = New-Store
    $t = Get-Ticket (Ask -Store $s -Kind heavy -Task "chatty") "ask"
    $text = "`$w = [Console]::Out; for (`$i = 1; `$i -le 100000; `$i++) { `$w.WriteLine('line ' + `$i) }; exit 0"
    $r = Invoke-Slot -Arguments @("run", "-Ticket", $t, "-Store", $s, "--", $ps5, "-NoProfile", "-Command", $text)
    Assert-Equal 0 $r.Code "the chatty command ended"
    $lines = @($r.Out -split "`r?`n" | Where-Object { $_ -like "line *" })
    Assert-Equal 100000 $lines.Count "every line came through"
    Assert-Equal "line 100000" $lines[-1] "the last line is the last line"
}

# ------------------------------------------------------------------------------------ 9
Test-Case "9 two asks at the same instant for the last slot: exactly one ONAY, twenty rounds" {
    $d = New-CaseDir
    $driver = Join-Path $d "racer.ps1"
    $driverText = @'
param([string]$Slot, [string]$Store, [string]$Task, [string]$Dir)
[System.IO.File]::WriteAllText((Join-Path $Dir "$Task.ready"), "")
while (-not (Test-Path -LiteralPath (Join-Path $Dir "go"))) { Start-Sleep -Milliseconds 2 }
$null = & $Slot ask -Kind database -Task $Task -Role worker -What "race" -Store $Store
exit $LASTEXITCODE
'@
    [System.IO.File]::WriteAllText($driver, $driverText)
    foreach ($round in 1..20) {
        $s = New-Store; $rd = Join-Path $d "r$round"; [void](New-Item -ItemType Directory -Path $rd)
        $a = Start-PsChild -Arguments @("-File", $driver, $slotScript, $s, "racer-a", $rd)
        $b = Start-PsChild -Arguments @("-File", $driver, $slotScript, $s, "racer-b", $rd)
        Wait-Until { (Test-Path -LiteralPath (Join-Path $rd "racer-a.ready")) -and (Test-Path -LiteralPath (Join-Path $rd "racer-b.ready")) } "both racers ready"
        [void](New-Item -ItemType File -Path (Join-Path $rd "go"))
        $codes = @(@((Wait-Child $a).Code, (Wait-Child $b).Code) | Sort-Object)
        Assert-Equal "0 3" ($codes -join " ") "round $($round): one ONAY and one BEKLE"
    }
}

# ------------------------------------------------------------------------------------ 10
Test-Case "10 a half-written entry file and a store that does not exist yet are both tolerated" {
    $s = New-Store
    Assert-True (-not (Test-Path -LiteralPath $s)) "the store does not exist yet"
    [void](Get-Ticket (Ask -Store $s -Kind desktop -Task "first") "the first ask creates the store")
    $s2 = New-Store
    $entries = Get-TestSlotEntryDirectory -Store $s2
    [void](New-Item -ItemType Directory -Path $entries -Force)
    [System.IO.File]::WriteAllText((Join-Path $entries "ts-halfwritten.json"), '{"ticket":"ts-halfwritten","kinds":["data')
    [void](Get-Ticket (Ask -Store $s2 -Kind database -Task "after-crash") "a half-written entry is ignored, not a holder and not a crash")
    $st = Invoke-Slot -Arguments @("status", "-Store", $s2)
    Assert-Equal 0 $st.Code "status reads past the half-written file (err: $($st.Err.Trim()))"
}

# ------------------------------------------------------------------------------------ 11
Test-Case "11 status names holders and waiters in Turkish (run as & script); one log line per finished run" {
    $s = New-Store
    $h = Get-Ticket (Ask -Store $s -Kind database -Task "status-holder" -Role inspector -What "integration probe") "holder"
    [void](Start-TestSlotRun -Store $s -Ticket $h -HolderPid $PID)
    Assert-Bekle (Ask -Store $s -Kind "database,heavy" -Task "status-waiter" -Role worker -What "integration suite") 1 "waiter"
    $out = @(& $slotScript status -Store $s)
    Assert-Equal 0 $LASTEXITCODE "status exit code under & script"
    $text = $out -join "`n"
    Assert-True ($text -match 'Test sırası') "a Turkish title: $text"
    Assert-True ($text -match '(?m)^.*çalışıyor.*inspector.*status-holder.*integration probe') "the holder in Turkish: $text"
    Assert-True ($text -match '(?m)^.*bekliyor.*worker.*status-waiter.*integration suite') "the waiter in Turkish: $text"
    Assert-True ($text -match 'database 1/1') "the kind's use: $text"
    Assert-True ($text -match 'heavy 0/3') "heavy is free: $text"
    Complete-TestSlotRun -Store $s -Ticket $h -ExitCode 0
    $log = @(Get-LogLines -Store $s)
    Assert-Equal 1 $log.Count "one line for the finished run"
    foreach ($field in @('role=inspector', 'task=status-holder', 'kinds=database', 'waited_s=\d+', 'ran_s=\d+', 'exit=0')) {
        Assert-True ($log[0] -match $field) "the log line has $($field): $($log[0])"
    }
    Assert-True ($log[0] -match '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\t') "the line starts with its UTC time: $($log[0])"
    $bad = Invoke-Slot -Arguments @("ask", "-Kind", "nonsense", "-Task", "x", "-Role", "worker", "-What", "y", "-Store", $s)
    Assert-Equal 2 $bad.Code "an unknown kind is a bad request"
    Assert-True ($bad.Err -match "unknown kind 'nonsense'") "and says why on stderr: $($bad.Err)"
}

# ------------------------------------------------------------------------------------ 12
Test-Case "12 the role files name the rule, and the example commands they show pass -DryRun" {
    foreach ($role in @("worker", "inspector", "lead")) {
        $path = Join-Path $repoRoot ".claude\agents\$role.md"
        $text = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
        foreach ($word in @('`database`', '`desktop`', '`heavy`', 'ONAY', 'BEKLE', 'test-slot.ps1 run', 'NOT_RUN')) {
            Assert-True ($text.Contains($word)) "$role.md names $word"
        }
        # the same files forbid the whole api unit suite to the roles (the 14 GB crash of 2026-10-03):
        # the slot rule must not offer it as a run to ask for
        $rule = [regex]::Match($text, '(?s)\*\*Test sırası.*?needs no slot\.').Value
        Assert-True ($rule.Length -gt 0) "$role.md has the Test sırası rule"
        Assert-True (-not ($rule -match 'whole\s+api\s+unit\s+suite')) "$role.md's slot rule does not list the whole api unit suite as a heavy run"
        $examples = @([regex]::Matches($text, '`(powershell [^`]*test-slot\.ps1 (?:ask|run) [^`]*)`') | ForEach-Object { $_.Groups[1].Value })
        Assert-True (@($examples | Where-Object { $_ -match 'test-slot\.ps1 ask ' }).Count -ge 1) "$role.md shows an ask example"
        Assert-True (@($examples | Where-Object { $_ -match 'test-slot\.ps1 run ' }).Count -ge 1) "$role.md shows a run example"
        foreach ($example in $examples) {
            $line = $example.Replace("<task-id>", "task-x").Replace("<ticket>", "ts-0123456789ab")
            $tokens = $null; $errors = $null
            $ast = [System.Management.Automation.Language.Parser]::ParseInput($line, [ref]$tokens, [ref]$errors)
            Assert-Equal 0 @($errors).Count "$role.md example parses: $line"
            $cmd = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true)
            $elements = @($cmd.CommandElements | ForEach-Object {
                    if ($_ -is [System.Management.Automation.Language.StringConstantExpressionAst]) { $_.Value }
                    elseif ($_ -is [System.Management.Automation.Language.CommandParameterAst]) { "-" + $_.ParameterName }
                    else { $_.Extent.Text } })
            $at = [array]::IndexOf($elements, ($elements | Where-Object { $_ -match 'test-slot\.ps1$' } | Select-Object -First 1))
            Assert-True ($at -ge 1) "$role.md example names the script: $line"
            $verb = $elements[$at + 1]
            $args2 = @($verb, "-DryRun", "-Store", (New-Store)) + @($elements[($at + 2)..($elements.Count - 1)])
            if ($verb -eq "ask") {
                $roleAt = [array]::IndexOf($elements, "-Role")
                Assert-Equal $role $elements[$roleAt + 1] "$role.md's ask example asks as $role"
            }
            $r = Invoke-Slot -Arguments $args2
            Assert-Equal 0 $r.Code "$role.md example accepted by the real script with -DryRun: $line (err: $($r.Err.Trim()))"
            Assert-True ($r.Out -match '^DRYRUN ') "the dry run says so: $($r.Out)"
        }
    }
}

# ------------------------------------------------------------------------------------ 13
Test-Case "13 quality-gate.ps1 (-StepList) asks before a heavy step, waits while it is held, starts when freed; -NoTestSlots asks nothing" {
    $s = New-Store; $d = New-CaseDir
    $marker = Join-Path $d "fake-step-ran"
    $steps = Join-Path $d "steps.ps1"
    [System.IO.File]::WriteAllText($steps, "Invoke-Step `"Fake integration`" -Kinds database,heavy { [System.IO.File]::WriteAllText('$marker', 'ran') }`r`nInvoke-Step `"Fake light step`" { Write-Host 'light' }`r`n")
    $h = Get-Ticket (Ask -Store $s -Kind database -Task "test-holder" -Role inspector -What "held by the test") "the test holds database"
    [void](Start-TestSlotRun -Store $s -Ticket $h -HolderPid $PID)
    $gate = Start-PsChild -Arguments @("-File", $gateScript, "-StepList", $steps, "-TestSlotStore", $s, "-TestSlotPollSeconds", "1")
    $gateEntry = { @(Get-TestSlotEntries -Store $s | Where-Object { $_.role -eq "gate" -and $_.state -eq "waiting" }) }
    Wait-Until { @(& $gateEntry).Count -eq 1 } "the gate asked for its step's kinds"
    $firstAsk = (& $gateEntry)[0].last_asked
    Wait-Until { $e = @(& $gateEntry); $e.Count -eq 1 -and $e[0].last_asked -ne $firstAsk } "the gate asked again"
    Assert-True (-not (Test-Path -LiteralPath $marker)) "the heavy step did not start while database was held"
    Assert-True (-not $gate.Process.HasExited) "the gate waits, it does not give up"
    Complete-TestSlotRun -Store $s -Ticket $h -ExitCode 0
    $g = Wait-Child $gate
    Assert-Equal 0 $g.Code "the gate passed (out: $($g.Out))"
    Assert-True (Test-Path -LiteralPath $marker) "the heavy step ran once the slot was free"
    Assert-True ($g.Out -match 'BEKLE 1 \|') "the gate printed its BEKLE line: $($g.Out)"
    Assert-True ($g.Out -match 'WaitSeconds') "the timing table has the wait column: $($g.Out)"
    Assert-True ($g.Out -match 'Fake light step') "the light step ran too"
    $gateLines = @(Get-LogLines -Store $s | Where-Object { $_ -match 'role=gate' })
    Assert-Equal 1 $gateLines.Count "the gate's run is in the log"
    Assert-True ($gateLines[0] -match 'kinds=database,heavy') "with both kinds: $($gateLines[0])"
    Assert-Equal 0 @(Get-TestSlotEntries -Store $s).Count "the gate released its slot after the step"

    # -NoTestSlots: the same held slot, and the gate runs as today, asking nothing.
    $s2 = New-Store; Remove-Item -LiteralPath $marker
    $h2 = Get-Ticket (Ask -Store $s2 -Kind database -Task "test-holder" -What "held by the test") "held again"
    [void](Start-TestSlotRun -Store $s2 -Ticket $h2 -HolderPid $PID)
    $g2 = Wait-Child (Start-PsChild -Arguments @("-File", $gateScript, "-StepList", $steps, "-TestSlotStore", $s2, "-NoTestSlots"))
    Assert-Equal 0 $g2.Code "the gate passed without slots"
    Assert-True (Test-Path -LiteralPath $marker) "the step ran beside the held slot"
    Assert-Equal 0 @(Get-TestSlotEntries -Store $s2 | Where-Object { $_.role -eq "gate" }).Count "the gate asked nothing"
    Assert-True ($g2.Out -notmatch 'BEKLE') "no BEKLE line"
}

# ------------------------------------------------------------------------------------ gate wiring
# ------------------------------------------------------------------------------------ 14-17: the board
# The office SEES the queue (owner, 2026-10-03): a take, a BEKLE and a freed slot are one
# 'bilgi' note each on the team's board; the queue decides, the board only reports.

function Start-FakeBoard {
    <# A fake board in its own process: every POST body is one line of <dir>\sink.jsonl. #>
    param([string]$Dir)
    $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $probe.Start(); $port = $probe.LocalEndpoint.Port; $probe.Stop()
    $server = Join-Path $Dir "fake-board.ps1"
    $code = @'
param([int]$Port, [string]$Ready, [string]$Sink)
$utf8 = New-Object System.Text.UTF8Encoding($false)
$l = New-Object System.Net.HttpListener
$l.Prefixes.Add("http://127.0.0.1:$Port/")
$l.Start()
[System.IO.File]::WriteAllText($Ready, "ready")
$n = 0
while ($true) {
    $c = $l.GetContext()
    $raw = (New-Object System.IO.StreamReader($c.Request.InputStream, $utf8)).ReadToEnd()
    if ($c.Request.Url.AbsolutePath -eq "/__stop") { $c.Response.Close(); break }
    [System.IO.File]::AppendAllText($Sink, ($raw -replace "[\r\n]+", " ") + "`n", $utf8)
    $n++
    $b = $utf8.GetBytes(('{{"note":{{"id":"n-20261004T0900{0:00}000000Z-0000abcd","at":"2026-10-04T09:00:00Z"}}}}' -f ($n % 60)))
    $c.Response.ContentType = "application/json; charset=utf-8"
    $c.Response.OutputStream.Write($b, 0, $b.Length)
    $c.Response.Close()
}
$l.Stop()
'@
    [System.IO.File]::WriteAllText($server, $code, (New-Object System.Text.UTF8Encoding($true)))
    $ready = Join-Path $Dir "board-ready"
    $sink = Join-Path $Dir "sink.jsonl"
    $child = Start-PsChild -Arguments @("-File", $server, "-Port", [string]$port, "-Ready", $ready, "-Sink", $sink)
    Wait-Until { Test-Path -LiteralPath $ready } "the fake board started" 40
    $token = Join-Path $Dir "team.token"
    [System.IO.File]::WriteAllText($token, "fake-board-token")
    return [pscustomobject]@{ Url = "http://127.0.0.1:$port"; Token = $token; Sink = $sink; Child = $child }
}

function Get-BoardNotes {
    param($Board)
    if (-not (Test-Path -LiteralPath $Board.Sink)) { return @() }
    return @([System.IO.File]::ReadAllLines($Board.Sink, [System.Text.Encoding]::UTF8) | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
}

function Use-Board {
    <# The children of a case inherit the board's address and token file; cleared afterwards. #>
    param([string]$Url, [string]$Token, [scriptblock]$Body)
    $env:PAGENTOS_TEAM_URL = $Url; $env:PAGENTOS_TEAM_TOKEN_FILE = $Token
    try { & $Body }
    finally { Remove-Item Env:PAGENTOS_TEAM_URL, Env:PAGENTOS_TEAM_TOKEN_FILE -ErrorAction SilentlyContinue }
}

function Ask-As {
    param([string]$Store, [string]$Seat, [string]$Task, [string]$What, [string]$Kind = "database")
    return (Invoke-Slot -Arguments @("ask", "-Kind", $Kind, "-Task", $Task, "-Role", "worker", "-Seat", $Seat, "-What", $What, "-Store", $Store))
}

Test-Case "14 board: a take posts one bilgi (seat, kind of test, estimate); a BEKLE names the holder; freeing names who is next" {
    $s = New-Store; $d = New-CaseDir
    [void](New-Item -ItemType Directory -Path $s -Force)
    # two earlier runs of the same command: 300 s and 420 s - the estimate is their mean, 6 min
    $old = "2026-10-03T08:00:00Z`trole=worker`ttask=x-task`tkinds=database`twaited_s=0`tran_s={0}`texit=0`twhat=birim testleri`r`n"
    [System.IO.File]::WriteAllText((Get-TestSlotLogPath -Store $s), (($old -f 300) + ($old -f 420)))
    $board = Start-FakeBoard -Dir $d
    Use-Board -Url $board.Url -Token $board.Token -Body {
        $t = Get-Ticket (Ask-As -Store $s -Seat "worker-2" -Task "slot-holder" -What "birim testleri") "worker-2 takes the slot"
        $notes = @(Get-BoardNotes $board)
        Assert-Equal 1 $notes.Count "one note on take: $(@($notes) | ConvertTo-Json -Compress)"
        $n = $notes[0]
        Assert-Equal "worker-2|slot-holder|bilgi" ("{0}|{1}|{2}" -f $n.seat, $n.task, $n.kind) "the holder's seat and task"
        Assert-True ($n.text -match '^Çalışan 2: birim testleri başlatıyorum \(veritabanı\), tahmini 6 dk$') "plain Turkish: $($n.text)"
        Assert-Equal "take|database|worker-2|6" ("{0}|{1}|{2}|{3}" -f $n.slot.state, (@($n.slot.kinds) -join ","), (@($n.slot.holders) -join ","), $n.slot.estimate_min) "the snapshot"
        [void](Get-Ticket (Ask-As -Store $s -Seat "worker-2" -Task "slot-holder" -What "birim testleri") "asking again with the ticket unused")
        Assert-Equal 1 @(Get-BoardNotes $board).Count "asking again posts nothing"

        $started = Join-Path $d "started"; $release = Join-Path $d "release"
        $wrapper = Start-Slot -Arguments (@("run", "-Ticket", $t, "-Store", $s, "--") + (Get-WaitCommand -Started $started -Release $release))
        Wait-Until { Test-Path -LiteralPath $started } "the holder's command started"
        Assert-Bekle (Ask-As -Store $s -Seat "worker-3" -Task "slot-waiter" -What "entegrasyon testleri") 1 "worker-3 waits"
        $notes = @(Get-BoardNotes $board)
        Assert-Equal 2 $notes.Count "one note for the BEKLE"
        Assert-True ($notes[1].text -match '^Çalışan 3: test sırası bekliyorum \(veritabanı: entegrasyon testleri\), sıram 1, önümde Çalışan 2$') "the waiting note: $($notes[1].text)"
        Assert-Equal "wait|worker-2|worker-3" ("{0}|{1}|{2}" -f $notes[1].slot.state, (@($notes[1].slot.holders) -join ","), (@($notes[1].slot.waiting) -join ",")) "the snapshot"
        Assert-Bekle (Ask-As -Store $s -Seat "worker-3" -Task "slot-waiter" -What "entegrasyon testleri") 1 "worker-3 asks again"
        Assert-Equal 2 @(Get-BoardNotes $board).Count "asking again in line posts nothing"

        [void](New-Item -ItemType File -Path $release)
        $w = Wait-Child $wrapper
        Assert-Equal 0 $w.Code "the holder's run ended cleanly: $($w.Err)"
        $notes = @(Get-BoardNotes $board)
        Assert-Equal 3 $notes.Count "one note when the slot frees"
        Assert-Equal "worker-2|slot-holder" ("{0}|{1}" -f $notes[2].seat, $notes[2].task) "the freeing seat writes it"
        Assert-True ($notes[2].text -match '^Çalışan 2: birim testleri bitti \(\d+ dk, çıkış 0\); sıradaki: Çalışan 3$') "who is next: $($notes[2].text)"
        Assert-Equal "free||worker-3" ("{0}|{1}|{2}" -f $notes[2].slot.state, (@($notes[2].slot.holders) -join ","), (@($notes[2].slot.waiting) -join ",")) "the snapshot"
        [void](Get-Ticket (Ask-As -Store $s -Seat "worker-3" -Task "slot-waiter" -What "entegrasyon testleri") "the waiter's turn")
        Assert-Equal 4 @(Get-BoardNotes $board).Count "and its take is on the board too"
    }
    try { [void](Invoke-WebRequest -UseBasicParsing -Uri ($board.Url + "/__stop") -TimeoutSec 5) } catch { }
}

Test-Case "15 board down: a closed port, a missing token file or a refusing board never blocks or changes ONAY/BEKLE" {
    $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $probe.Start(); $closed = $probe.LocalEndpoint.Port; $probe.Stop()
    $d = New-CaseDir
    $token = Join-Path $d "t.token"; [System.IO.File]::WriteAllText($token, "x")
    foreach ($case in @(@("http://127.0.0.1:$closed", $token), @("http://127.0.0.1:$closed", (Join-Path $d "absent.token")), @("", ""))) {
        $s = New-Store
        Use-Board -Url $case[0] -Token $case[1] -Body {
            $sw = [System.Diagnostics.Stopwatch]::StartNew()
            $t = Get-Ticket (Ask-As -Store $s -Seat "worker-2" -Task "down-holder" -What "birim testleri") "ONAY with the board down ($($case[0]))"
            Assert-Bekle (Ask-As -Store $s -Seat "worker-3" -Task "down-waiter" -What "entegrasyon") 1 "BEKLE with the board down"
            $r = Invoke-Slot -Arguments @("run", "-Ticket", $t, "-Store", $s, "--", $ps5, "-NoProfile", "-Command", "exit 7")
            Assert-Equal 7 $r.Code "run's exit code is the command's, board or no board"
            Assert-True ($sw.Elapsed.TotalSeconds -lt 60) "nothing waited on the board: $($sw.Elapsed.TotalSeconds) s"
            [void](Get-Ticket (Ask-As -Store $s -Seat "worker-3" -Task "down-waiter" -What "entegrasyon") "the waiter's turn, the board still down")
        }
    }
    $script:sent = 0
    $ok = Publish-TestSlotBoardNote -Note ([pscustomobject]@{ seat = "worker-2"; task = "x-task"; text = "t"; slot = @{} }) -Sender { param($n) $script:sent++; throw "the board is on fire" }
    Assert-True (($script:sent -eq 1) -and ($ok -eq $false)) "a sender that throws is swallowed: sent $script:sent, ok $ok"
}

function Invoke-SlotUtf8 {
    <# test-slot.ps1 with its stdout read as UTF-8 (the verbs that write UTF-8: who). #>
    param([string[]]$Arguments)
    $psi = New-Object System.Diagnostics.ProcessStartInfo $ps5
    $psi.Arguments = ConvertTo-NativeArgumentLine -Arguments (@("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $slotScript) + $Arguments)
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $p = [System.Diagnostics.Process]::Start($psi)
    $child = [pscustomobject]@{ Process = $p; Out = $p.StandardOutput.ReadToEndAsync(); Err = $p.StandardError.ReadToEndAsync() }
    [void]$script:Children.Add($child)
    return (Wait-Child -Child $child)
}

Test-Case "16 who: the holder and the line in Turkish, by seat name, an agent can ask before it plans" {
    $s = New-Store
    $h = Get-Ticket (Ask-As -Store $s -Seat "worker-2" -Task "who-holder" -What "birim testleri") "holder"
    [void](Start-TestSlotRun -Store $s -Ticket $h -HolderPid $PID)
    Assert-Bekle (Ask-As -Store $s -Seat "worker-3" -Task "who-waiter" -What "entegrasyon testleri" -Kind "database,heavy") 1 "waiter"
    Assert-Bekle (Invoke-Slot -Arguments @("ask", "-Kind", "database", "-Task", "who-gate", "-Role", "gate", "-What", "kapı", "-Store", $s)) 1 "the gate goes first"
    $r = Invoke-SlotUtf8 -Arguments @("who", "-Store", $s)
    Assert-Equal 0 $r.Code "who exits 0: $($r.Err)"
    $lines = @($r.Out.Trim() -split "`r?`n")
    Assert-True ($lines[0] -match '^Şu an test yapan: 1, sırada: 2') "the head line: $($r.Out)"
    Assert-True ($r.Out -match '(?m)^  TEST    Çalışan 2 - birim testleri \(veritabanı\), \d+ dk') "the holder: $($r.Out)"
    Assert-True ($r.Out -match '(?m)^  SIRA 1  Kapı - kapı \(veritabanı\)') "the gate first: $($r.Out)"
    Assert-True ($r.Out -match '(?m)^  SIRA 2  Çalışan 3 - entegrasyon testleri \(veritabanı, ağır\)') "then worker-3: $($r.Out)"
    Complete-TestSlotRun -Store $s -Ticket $h -ExitCode 0
    $empty = Invoke-SlotUtf8 -Arguments @("who", "-Store", (New-Store))
    Assert-True ($empty.Out -match 'Şu an test yapan yok, sırada kimse yok\.') "an empty line: $($empty.Out)"
}

Test-Case "17 board notes: Turkish seat names, at most 280 characters, the gate writes as the lead, no seat no note" {
    Assert-Equal "Çalışan 2|Denetleyici|Denetleyici 2|Proje Yöneticisi|Kapı|Araştırmacı|Entegratör|reviewer" ((@("worker-2", "inspector", "inspector-2", "lead", "gate", "researcher", "integrator", "reviewer") | ForEach-Object { Get-TestSlotSeatName -Name $_ }) -join "|") "names"
    $mk = { param($seat, $role, $what) [pscustomobject]@{ ticket = "ts-1"; kinds = @("heavy"); role = $role; task = "long-task"; what = $what; seat = $seat; state = "granted"; seq = 1; first_asked = ""; last_asked = ""; granted_at = ""; started_at = ""; holder_pid = 0; holder_start = "" } }
    $long = & $mk "worker-4" "worker" ("ş" * 400)
    $n = New-TestSlotBoardNote -Event take -Entry $long -Entries @($long) -Store (New-Store)
    Assert-True ($n.text.Length -le 280 -and $n.text.StartsWith("Çalışan 4: ")) "bounded: $($n.text.Length)"
    $gate = & $mk "" "gate" "tam kapı"
    Assert-Equal "lead" (New-TestSlotBoardNote -Event take -Entry $gate -Entries @($gate) -Store (New-Store)).seat "the gate writes as the lead"
    Assert-True ($null -eq (New-TestSlotBoardNote -Event take -Entry (& $mk "" "worker" "x") -Entries @() -Store (New-Store))) "a worker with no seat posts nothing"
    $badTask = & $mk "worker-1" "worker" "x"; $badTask.task = "Not A Task"
    Assert-True ($null -eq (New-TestSlotBoardNote -Event take -Entry $badTask -Entries @() -Store (New-Store))) "a task the board does not know posts nothing"
}

Test-Case "gate: quality-gate.ps1 runs this suite as its own step under PS 5.1, and asks before its heavy steps" {
    $gate = [System.IO.File]::ReadAllText($gateScript, [System.Text.Encoding]::UTF8)
    $step = [regex]::Match($gate, '(?s)Invoke-Step "Agent team test queue[^"]*" \{(.*?)\n  \}')
    Assert-True $step.Success "the step is registered"
    $body = $step.Groups[1].Value
    Assert-True ($body -match 'scripts\\tests\\team-test-slots\.tests\.ps1') "it names this file"
    Assert-True ($body -match 'Assert-ExitCode "team-test-slots tests"') "a red suite fails the gate"
    foreach ($pair in @(@('API unit tests', 'heavy'), @('API integration tests', 'database,heavy'), @('Alembic upgrade head', 'database'), @('Windows agent build \+ tests', 'heavy'))) {
        Assert-True ($gate -match ('Invoke-Step "' + $pair[0] + '" -Kinds ' + [regex]::Escape($pair[1]) + ' \{')) "the step '$($pair[0])' asks for $($pair[1])"
    }
    $ci = [System.IO.File]::ReadAllText((Join-Path $repoRoot ".github\workflows\ci.yml"), [System.Text.Encoding]::UTF8)
    # Since ci-ps-suites-from-glob CI runs every scripts\tests\*.tests.ps1 it finds, less a named
    # skip list: the suite is in CI when the glob is there and the skip list does not name it.
    Assert-True ($ci -match [regex]::Escape("Get-ChildItem -Path 'scripts\tests\*.tests.ps1'")) "CI runs the suites by glob"
    Assert-True (-not ($ci -match "'team-test-slots\.tests\.ps1'\s*=")) "CI does not skip the suite"
}

Stop-Children
try { Remove-Item -LiteralPath $script:TempRoot -Recurse -Force -ErrorAction Stop } catch { Write-Host "  (temp folder left: $script:TempRoot)" }

Write-Host ""
Write-Host "team-test-slots tests:$script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
