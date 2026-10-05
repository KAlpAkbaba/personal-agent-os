<#
.SYNOPSIS
    The gate's suite group (scripts/lib/GateSteps.ps1, team/plans/gate-faster-adr.md): the
    PowerShell suites that share nothing run side by side, each as its own process.

.DESCRIPTION
    The gate ran its steps strictly one after another although most suites touch nothing the
    others touch. Invoke-GateStepGroup runs a list of steps, at most N at once, each in its own
    powershell.exe with stdout and stderr in two files, and answers per step: name, outcome,
    exit code, seconds, the two log paths. A step past its deadline is killed with its process
    tree and is a failed step, never a hang.

    The cases, by number (the card's acceptance names them). Every fake step is written by
    this file into a temp folder; bounds are hang guards, never the claim:

      1  three steps that each wait for a file this test creates only after it has seen all
         three 'started' markers: all three ARE running at once (run with -Case1MaxParallel 1
         for the red);
      2  -MaxParallel 2 with four steps: never more than two started without a matching end;
      3  one step exits 3 among passing ones: its name and code come back, the others ran to
         their end, the group failed;
      4  a step that never ends is killed at its deadline WITH its child, is reported with the
         timeout word, and the group returns;
      5  100 000 lines on each stream are complete in their own files (the pipe-full hang),
         and a step that writes to stderr and exits 0 passes;
      6  results come back in the listed order whatever order the steps ended in;
      7  the real quality-gate.ps1's printing functions: a failing step prints the same
         `FAILED:` line and the same final word grouped as it does sequentially.

    Run: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tests\gate-steps.tests.ps1
#>

[CmdletBinding()]
param(
    # Runs only the cases whose name matches this pattern (the mutation proofs re-run a slice).
    [string]$Filter = "",
    # Case 1's group width; 1 is the red run the card asks for.
    [int]$Case1MaxParallel = 3
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$gatePath = Join-Path $repoRoot "scripts\quality-gate.ps1"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
. (Join-Path $repoRoot "scripts\lib\GateSteps.ps1")

$script:Failures = 0
$script:Passes = 0
$sandbox = Join-Path $env:TEMP ("pagentos-gatesteps-tests-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $sandbox | Out-Null
$script:Leftovers = New-Object System.Collections.ArrayList

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

# One fake step for every case: it marks its start and its end in a folder and does what its
# mode says in between.
$fakeStep = Join-Path $sandbox "fake-step.ps1"
[System.IO.File]::WriteAllText($fakeStep, @'
param([string]$Dir, [string]$Id, [string]$Mode = "sleep", [int]$Exit = 0, [int]$Milliseconds = 0)
$ErrorActionPreference = "Stop"
function Set-Marker([string]$What) { [System.IO.File]::WriteAllText((Join-Path $Dir "$What-$Id"), [string][datetime]::UtcNow.Ticks) }
Set-Marker "started"
switch ($Mode) {
    "wait-release" {
        $deadline = [datetime]::UtcNow.AddSeconds(120)
        while (-not (Test-Path -LiteralPath (Join-Path $Dir "release")) -and [datetime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
    }
    "sleep" { Start-Sleep -Milliseconds $Milliseconds }
    "hang" {
        $ps = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
        $child = Start-Process -FilePath $ps -ArgumentList "-NoProfile -Command Start-Sleep -Seconds 600" -PassThru -WindowStyle Hidden
        [System.IO.File]::WriteAllText((Join-Path $Dir "pids-$Id"), "$PID $($child.Id)")
        Start-Sleep -Seconds 600
    }
    "flood" {
        $out = [Console]::Out; $err = [Console]::Error
        for ($i = 0; $i -lt 100000; $i++) { $out.WriteLine("out line $i"); $err.WriteLine("err line $i") }
    }
    "stderr" { [Console]::Error.WriteLine("a warning on stderr; the step still passes") }
}
Set-Marker "ended"
exit $Exit
'@, (New-Object System.Text.ASCIIEncoding))

function New-CaseDir {
    $dir = Join-Path $sandbox ([guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    return $dir
}

function New-FakeGateStep {
    param([string]$Dir, [string]$Id, [string]$Mode = "sleep", [int]$Exit = 0, [int]$Milliseconds = 0, [int]$DeadlineSeconds = 0, [string]$Lane = "")
    return New-GateStep -Name "step $Id" -Script $fakeStep -What "step $Id tests" -Lane $Lane -DeadlineSeconds $DeadlineSeconds `
        -Arguments @("-Dir", $Dir, "-Id", $Id, "-Mode", $Mode, "-Exit", [string]$Exit, "-Milliseconds", [string]$Milliseconds)
}

function Get-Marker {
    param([string]$Dir, [string]$What)
    return @(Get-ChildItem -LiteralPath $Dir -Filter "$What-*" -ErrorAction SilentlyContinue)
}

function Get-MarkerTicks {
    param([string]$Dir, [string]$What, [string]$Id)
    $path = Join-Path $Dir "$What-$Id"
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    return [long]([System.IO.File]::ReadAllText($path))
}

# -------------------------------------------------------------- 1: running at once

Test-Case "1. three steps waiting on a file the test makes only after all three started: all three run at once" {
    $dir = New-CaseDir
    $driver = Join-Path $dir "driver.ps1"
    $resultPath = Join-Path $dir "results.json"
    [System.IO.File]::WriteAllText($driver, @"
`$ErrorActionPreference = "Stop"
. "$(Join-Path $repoRoot "scripts\lib\GateSteps.ps1")"
`$steps = @(foreach (`$id in @("a", "b", "c")) { New-GateStep -Name "step `$id" -Script "$fakeStep" -What "step `$id" -Arguments @("-Dir", "$dir", "-Id", `$id, "-Mode", "wait-release") })
`$r = Invoke-GateStepGroup -Steps `$steps -MaxParallel $Case1MaxParallel -LogRoot "$(Join-Path $dir 'logs')"
[System.IO.File]::WriteAllText("$resultPath", (ConvertTo-Json -InputObject @(`$r | ForEach-Object { `$_.Outcome })))
"@, (New-Object System.Text.ASCIIEncoding))
    $proc = Start-Process -FilePath $powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$driver`"" -PassThru -WindowStyle Hidden
    [void]$script:Leftovers.Add($proc.Id)
    $seen = 0
    $endedBeforeRelease = 0
    $guard = [datetime]::UtcNow.AddSeconds(45)    # hang guard: three powershell starts take seconds
    while ([datetime]::UtcNow -lt $guard) {
        $seen = @(Get-Marker -Dir $dir -What "started").Count
        if ($seen -ge 3) { break }
        Start-Sleep -Milliseconds 200
    }
    $endedBeforeRelease = @(Get-Marker -Dir $dir -What "ended").Count
    [System.IO.File]::WriteAllText((Join-Path $dir "release"), "go")
    if (-not $proc.WaitForExit(180000)) { throw "the group did not return after the release (hang guard)" }
    Assert-Equal 0 $endedBeforeRelease "no step ended before the release"
    Assert-Equal 3 $seen "all three 'started' markers were there before the test released any step"
    # 5.1's ConvertFrom-Json hands a JSON array down the pipeline as ONE object: unroll it.
    $outcomes = @((ConvertFrom-Json ([System.IO.File]::ReadAllText($resultPath))) | ForEach-Object { $_ })
    Assert-Equal "PASS,PASS,PASS" ($outcomes -join ",") "all three passed once released"
}

# ------------------------------------------------------------ 2: never more than N

Test-Case "2. -MaxParallel 2 with four steps: never more than two started without a matching end, and two did overlap" {
    $dir = New-CaseDir
    $steps = @(foreach ($id in @("a", "b", "c", "d")) { New-FakeGateStep -Dir $dir -Id $id -Mode "sleep" -Milliseconds 3000 })
    $r = @(Invoke-GateStepGroup -Steps $steps -MaxParallel 2 -LogRoot (Join-Path $dir "logs"))
    Assert-Equal 4 @($r).Count "four results"
    $events = New-Object System.Collections.ArrayList
    foreach ($id in @("a", "b", "c", "d")) {
        [void]$events.Add([pscustomobject]@{ T = (Get-MarkerTicks $dir "started" $id); D = 1 })
        [void]$events.Add([pscustomobject]@{ T = (Get-MarkerTicks $dir "ended" $id); D = -1 })
    }
    # An end and a start at the same tick: the end first (it really happened before the start).
    $running = 0; $most = 0
    foreach ($e in @($events | Sort-Object -Property T, D)) {
        $running += $e.D
        if ($running -gt $most) { $most = $running }
    }
    Assert-True ($most -le 2) "at most two ran at once (saw $most)"
    Assert-Equal 2 $most "two did run at once (the group is not serial)"
}

# ------------------------------------------------------------- 3: a failing step

Test-Case "3. one step exits 3 among passing ones: its name and code come back, the others ran to their end, the group failed" {
    $dir = New-CaseDir
    $steps = @(
        (New-FakeGateStep -Dir $dir -Id "first" -Mode "sleep" -Milliseconds 300),
        (New-FakeGateStep -Dir $dir -Id "broken" -Mode "sleep" -Exit 3),
        (New-FakeGateStep -Dir $dir -Id "last" -Mode "sleep" -Milliseconds 2000)
    )
    $r = @(Invoke-GateStepGroup -Steps $steps -MaxParallel 3 -LogRoot (Join-Path $dir "logs"))
    $broken = @($r | Where-Object { $_.Outcome -ne "PASS" })
    Assert-Equal 1 @($broken).Count "exactly one step failed"
    Assert-Equal "step broken" $broken[0].Name "the failing step's name comes back"
    Assert-Equal 3 $broken[0].ExitCode "its exit code comes back"
    Assert-Equal "FAIL" $broken[0].Outcome "its outcome is FAIL"
    foreach ($id in @("first", "last")) { Assert-True ($null -ne (Get-MarkerTicks $dir "ended" $id)) "step $id ran to its end" }
    Assert-True (-not (Test-GateStepGroupPassed -Results $r)) "the group's result is failed"
    Assert-True (Test-GateStepGroupPassed -Results @($r | Where-Object { $_.Outcome -eq "PASS" })) "...and the passing ones alone pass"
}

# ------------------------------------------------------------- 4: the deadline

Test-Case "4. a step that never ends is killed at its deadline with its child, reported with the timeout word, and the group returns" {
    $dir = New-CaseDir
    $steps = @(
        (New-FakeGateStep -Dir $dir -Id "hangs" -Mode "hang" -DeadlineSeconds 8),
        (New-FakeGateStep -Dir $dir -Id "fine" -Mode "sleep" -Milliseconds 200)
    )
    $r = @(Invoke-GateStepGroup -Steps $steps -MaxParallel 2 -LogRoot (Join-Path $dir "logs"))
    $pidsPath = Join-Path $dir "pids-hangs"
    Assert-True (Test-Path -LiteralPath $pidsPath) "the hanging step started its child"
    $pids = @(([System.IO.File]::ReadAllText($pidsPath)).Trim() -split '\s+' | ForEach-Object { [int]$_ })
    foreach ($p in $pids) { [void]$script:Leftovers.Add($p) }
    Start-Sleep -Milliseconds 500    # Process.HasExited lags a kill under load; the claim is below
    $alive = @($pids | Where-Object { $null -ne (Get-Process -Id $_ -ErrorAction SilentlyContinue) })
    Assert-Equal "" ($alive -join ",") "the step ($($pids[0])) and its child ($($pids[1])) are both gone"
    $hung = @($r | Where-Object { $_.Name -eq "step hangs" })[0]
    Assert-Equal $script:GateStepTimeoutWord $hung.Outcome "the hanging step is reported with the timeout word"
    Assert-True (-not (Test-GateStepGroupPassed -Results $r)) "a timed-out step fails the group"
    Assert-Equal "PASS" (@($r | Where-Object { $_.Name -eq "step fine" })[0].Outcome) "the other step passed"
}

# ----------------------------------------------------------- 5: the two streams

Test-Case "5. 100 000 lines on each stream land whole in their own files; stderr with exit 0 is a pass" {
    $dir = New-CaseDir
    $steps = @(
        (New-FakeGateStep -Dir $dir -Id "flood" -Mode "flood" -DeadlineSeconds 300),
        (New-FakeGateStep -Dir $dir -Id "warns" -Mode "stderr")
    )
    $r = @(Invoke-GateStepGroup -Steps $steps -MaxParallel 2 -LogRoot (Join-Path $dir "logs"))
    $flood = $r[0]
    Assert-Equal "PASS" $flood.Outcome "the flooding step ended by itself and passed (it did not block)"
    $out = @([System.IO.File]::ReadAllLines($flood.StdoutPath) | Where-Object { $_ })
    $err = @([System.IO.File]::ReadAllLines($flood.StderrPath) | Where-Object { $_ })
    Assert-Equal 100000 @($out | Where-Object { $_ -like "out line *" }).Count "stdout holds every stdout line"
    Assert-Equal 100000 @($err | Where-Object { $_ -like "err line *" }).Count "stderr holds every stderr line"
    Assert-Equal 0 @($out | Where-Object { $_ -like "err line *" }).Count "no stderr line in the stdout file (never merged)"
    Assert-Equal 0 @($err | Where-Object { $_ -like "out line *" }).Count "no stdout line in the stderr file"
    Assert-Equal "PASS" $r[1].Outcome "a step that writes to stderr and exits 0 passes"
    Assert-True ([System.IO.File]::ReadAllText($r[1].StderrPath) -match "still passes") "...and what it wrote is kept"
}

# ----------------------------------------------------------- 6: the listed order

Test-Case "6. results come back in the listed order, whatever order the steps ended in" {
    $dir = New-CaseDir
    $steps = @(
        (New-FakeGateStep -Dir $dir -Id "slow" -Mode "sleep" -Milliseconds 4000),
        (New-FakeGateStep -Dir $dir -Id "middle" -Mode "sleep" -Milliseconds 2000),
        (New-FakeGateStep -Dir $dir -Id "quick" -Mode "sleep" -Milliseconds 10)
    )
    $r = @(Invoke-GateStepGroup -Steps $steps -MaxParallel 3 -LogRoot (Join-Path $dir "logs"))
    Assert-True ((Get-MarkerTicks $dir "ended" "quick") -lt (Get-MarkerTicks $dir "ended" "slow")) "the steps really ended out of order"
    Assert-Equal "step slow,step middle,step quick" (@($r | ForEach-Object { $_.Name }) -join ",") "the listed order"
    foreach ($x in $r) {
        Assert-True ($x.Seconds -ge 0 -and (Test-Path -LiteralPath $x.StdoutPath) -and (Test-Path -LiteralPath $x.StderrPath)) "$($x.Name): seconds and both log paths"
    }
}

Test-Case "6b. the named defaults: three at once, a deadline, a timeout word" {
    Assert-Equal 3 $script:GateStepMaxParallel "the default width is the named constant 3"
    Assert-True ($script:GateStepDeadlineSeconds -ge 3600) "the default deadline is a hang guard, not a stopwatch ($script:GateStepDeadlineSeconds s)"
    Assert-Equal "TIMEOUT" $script:GateStepTimeoutWord "the timeout word"
}

# ------------------------------------------- 7: the gate prints the same either way

function Get-GateFunctionText {
    <# The text of one function of scripts/quality-gate.ps1, found by the parser. #>
    param([string]$Name)
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($gatePath, [ref]$tokens, [ref]$errors)
    $f = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $Name }, $true)
    if ($null -eq $f) { throw "scripts/quality-gate.ps1 has no function $Name" }
    return $f.Extent.Text
}

function Invoke-GateTwoWays {
    <# One failing fake suite through the gate's own functions, sequential and grouped. #>
    param([string]$Suite, [string]$Mode = "fail", [string]$Passing = "")
    foreach ($fn in @("Invoke-Step", "Assert-ExitCode", "Get-StepFailureText", "Invoke-GateSuite", "Start-GateGroup", "Complete-GateGroup", "Write-GateSummary", "Enter-GateTestSlot", "Exit-GateTestSlot")) {
        . ([scriptblock]::Create((Get-GateFunctionText $fn)))
    }
    $powershell5 = $powershell
    # The gate's own last lines (the summary, the final word, the exit code), with `exit n`
    # turned into a printed "exit=n" so they can run here.
    $gateText = [System.IO.File]::ReadAllText($gatePath)
    $tail = $gateText.Substring($gateText.LastIndexOf("# -------------------------------------------------------------------- summary"))
    if ($tail -match '(?m)^\s*exit(?! [01]\s*$)') { throw "the gate's last lines must end in a literal 'exit 1' / 'exit 0' (team-integrate.tests.ps1 reads them): $($Matches[0])" }
    $tail = $tail.Replace('$gateClock.Elapsed.TotalSeconds', '1') -replace '(?m)^(\s*)exit (\d)\s*$', '$1Write-Output "exit=$2"; return'
    $out = @{}
    foreach ($way in @("serial", "group")) {
        $GateSerial = ($way -eq "serial")
        $GateMaxParallel = 0
        $script:results = New-Object System.Collections.ArrayList
        $script:failed = $false
        $script:GateGroup = $null
        $script:GateRecording = $null
        $lines = @(& {
                Start-GateGroup
                Invoke-Step "A fake suite that fails (PS5.1)" {
                    # "throw": the step dies before it names its suite, as a step does when
                    # its tool is missing (`throw "Windows PowerShell 5.1 not found"`).
                    if ($Mode -eq "throw") { throw "the fake step could not start" }
                    $script = $Suite
                    Invoke-GateSuite $script
                    Assert-ExitCode "fake suite tests"
                }
                if ($Passing) {
                    Invoke-Step "A fake suite that passes (PS5.1)" {
                        $script = $Passing
                        Invoke-GateSuite $script
                        Assert-ExitCode "passing suite tests"
                    }
                }
                Complete-GateGroup
                . ([scriptblock]::Create($tail))
            } *>&1 | ForEach-Object { [string]$_ })
        $out[$way] = $lines
    }
    return $out
}

Test-Case "7. a failing step prints the same FAILED line and the same final word grouped as sequentially (the real gate's functions)" {
    $suite = Join-Path $sandbox "failing-suite.ps1"
    [System.IO.File]::WriteAllText($suite, "Write-Host '  FAIL  the fake case'`r`nexit 3`r`n", (New-Object System.Text.ASCIIEncoding))
    $ways = Invoke-GateTwoWays -Suite $suite
    $failedSerial = @($ways["serial"] | Where-Object { $_ -match '^FAILED: ' })
    $failedGroup = @($ways["group"] | Where-Object { $_ -match '^FAILED: ' })
    Assert-Equal "FAILED: fake suite tests exited with code 3" ($failedSerial -join "|") "the sequential gate's line"
    Assert-Equal ($failedSerial -join "|") ($failedGroup -join "|") "the grouped step's FAILED line equals the sequential one"
    $wordSerial = @($ways["serial"] | Where-Object { $_ -match '^QUALITY GATE: ' })
    $wordGroup = @($ways["group"] | Where-Object { $_ -match '^QUALITY GATE: ' })
    Assert-Equal "QUALITY GATE: FAIL" ($wordSerial -join "|") "the sequential final word"
    Assert-Equal ($wordSerial -join "|") ($wordGroup -join "|") "the grouped final word equals the sequential one"
    Assert-True (@($ways["group"] | Where-Object { $_ -eq "exit=1" }).Count -eq 1) "the grouped gate exits 1"
    Assert-True (@($ways["group"] | Where-Object { $_ -eq "=== A fake suite that fails (PS5.1) ===" }).Count -eq 1) "the grouped step has its own section header"
    Assert-True (@($ways["group"] | Where-Object { $_ -match 'FAIL  the fake case' }).Count -ge 1) "the grouped step's log is printed"
}

Test-Case "8. the suites that read quality-gate.ps1's text still read it green (team-area's step, team-integrate's words)" {
    # 2026-10-03: the group's first real run was red in exactly these two: team-area wants its
    # step's 5.1 call written in the gate, team-integrate wants the final word and exit literal.
    foreach ($reader in @(
            @{ Suite = "team-area.tests.ps1"; Filter = "^gate: quality-gate\.ps1 runs this suite" },
            @{ Suite = "team-integrate.tests.ps1"; Filter = "^the fake gate speaks the real gate's words" })) {
        $lines = @(& $powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot "scripts\tests\$($reader.Suite)") -Filter $reader.Filter 2>&1 | ForEach-Object { [string]$_ })
        $code = $LASTEXITCODE
        $passed = @($lines | Where-Object { $_ -match '^\s*PASS\s' }).Count
        Assert-True ($code -eq 0 -and $passed -eq 1) "$($reader.Suite) -Filter '$($reader.Filter)': exit $code, $passed passed ($(@($lines | Where-Object { $_ -match 'FAIL|expected|actual' }) -join ' | '))"
    }
}

# ------------------------------------------------------------------ 9: the lanes

Test-Case "9. two steps of one lane never run at once, in the listed order, while a step of no lane runs beside them" {
    # The inspector's M2 (2026-10-04): the lane rule removed, every case stayed green.
    $dir = New-CaseDir
    $steps = @(
        (New-FakeGateStep -Dir $dir -Id "lane1" -Mode "sleep" -Milliseconds 3000 -Lane "shared"),
        (New-FakeGateStep -Dir $dir -Id "lane2" -Mode "sleep" -Milliseconds 3000 -Lane "shared"),
        (New-FakeGateStep -Dir $dir -Id "free" -Mode "sleep" -Milliseconds 3000)
    )
    $r = @(Invoke-GateStepGroup -Steps $steps -MaxParallel 3 -LogRoot (Join-Path $dir "logs"))
    Assert-True (Test-GateStepGroupPassed -Results $r) "all three passed"
    $end1 = Get-MarkerTicks $dir "ended" "lane1"
    $start2 = Get-MarkerTicks $dir "started" "lane2"
    Assert-True ($start2 -ge $end1) "lane2 started only after lane1 ended (start $start2, end $end1)"
    $startFree = Get-MarkerTicks $dir "started" "free"
    Assert-True ($startFree -lt $end1) "the lane-less step ran beside lane1 (the room was not wasted)"
}

Test-Case "9b. the gate puts the two suites that start the fake team API into one lane" {
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($gatePath, [ref]$tokens, [ref]$errors)
    foreach ($suite in @("team-integrate.tests.ps1", "team-feed.tests.ps1")) {
        $step = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq "Invoke-Step" -and $n.Extent.Text -like "*$suite*" }, $true)
        Assert-True ($null -ne $step) "the gate has a step for $suite"
        Assert-True ($step.Extent.Text -match 'Invoke-GateSuite \$script -Lane "fake-team-api"') "$suite runs in the lane fake-team-api"
    }
}

# ------------------------------------------- 10: a step that throws while recorded

Test-Case "10. a grouped step that throws before it names its suite fails in the sequential words, and the gate still prints its summary and the rest of the group runs" {
    $suite = Join-Path $sandbox "never-run-suite.ps1"
    [System.IO.File]::WriteAllText($suite, "exit 0`r`n", (New-Object System.Text.ASCIIEncoding))
    $passing = Join-Path $sandbox "passing-suite.ps1"
    [System.IO.File]::WriteAllText($passing, "Write-Host '  PASS  the passing case'`r`nexit 0`r`n", (New-Object System.Text.ASCIIEncoding))
    $ways = Invoke-GateTwoWays -Suite $suite -Mode "throw" -Passing $passing
    foreach ($way in @("serial", "group")) {
        $failed = @($ways[$way] | Where-Object { $_ -match '^FAILED: ' })
        Assert-Equal "FAILED: the fake step could not start" ($failed -join "|") "${way}: the step's FAILED line"
        Assert-Equal "QUALITY GATE: FAIL" (@($ways[$way] | Where-Object { $_ -match '^QUALITY GATE: ' }) -join "|") "${way}: the final word"
        Assert-True (@($ways[$way] | Where-Object { $_ -eq "=== Quality gate summary ===" }).Count -eq 1) "${way}: the summary is printed"
        Assert-True (@($ways[$way] | Where-Object { $_ -eq "exit=1" }).Count -eq 1) "${way}: the gate exits 1"
        Assert-True (@($ways[$way] | Where-Object { $_ -match 'PASS  the passing case' }).Count -eq 1) "${way}: the other step still ran"
    }
}

# ------------------------------------------- 11: the group and the test queue

Test-Case "11. the real gate (-StepList) asks the test queue once for its grouped steps' kinds, holds them while the steps run, and frees them after" {
    # The merge with main (2026-10-04): a step asks the queue ("test sirasi") for its kinds; a
    # grouped step's kinds are asked for by the group - one ticket, both kinds, released at its end.
    $dir = New-CaseDir
    $store = Join-Path $dir "slots"
    $held = Join-Path $dir "held-during-run.txt"
    $suite = Join-Path $dir "queue-suite.ps1"
    $libPath = Join-Path $repoRoot "scripts\lib\TeamTestSlots.ps1"
    # The suite looks at the queue while it runs: the gate's ticket must be "running" then.
    [System.IO.File]::WriteAllText($suite, (". '$libPath'`r`n" +
            "`$e = @(Get-TestSlotEntries -Store '$store' | Where-Object { `$_.role -eq 'gate' -and `$_.state -eq 'running' })`r`n" +
            "[System.IO.File]::WriteAllText('$held', ((`$e | ForEach-Object { (@(`$_.kinds) -join ',') + '|' + `$_.what }) -join ';'))`r`nexit 0`r`n"), (New-Object System.Text.ASCIIEncoding))
    $stepsFile = Join-Path $dir "steps.ps1"
    [System.IO.File]::WriteAllText($stepsFile, ("Start-GateGroup`r`n" +
            "Invoke-Step `"Queue step A`" -Kinds heavy { `$script = '$suite'; Invoke-GateSuite `$script; Assert-ExitCode `"queue suite A`" }`r`n" +
            "Invoke-Step `"Queue step B`" -Kinds database { `$script = '$suite'; Invoke-GateSuite `$script; Assert-ExitCode `"queue suite B`" }`r`n" +
            "Complete-GateGroup`r`n"), (New-Object System.Text.ASCIIEncoding))
    $out = @(& $powershell -NoProfile -ExecutionPolicy Bypass -File $gatePath -StepList $stepsFile -TestSlotStore $store -TestSlotPollSeconds 1 2>&1 | ForEach-Object { [string]$_ })
    $code = $LASTEXITCODE
    Assert-Equal 0 $code "the gate passed ($($out -join ' / '))"
    Assert-True (Test-Path -LiteralPath $held) "the grouped suite ran"
    $seen = [System.IO.File]::ReadAllText($held)
    Assert-True ($seen -match '^(heavy,database|database,heavy)\|suite group$') "while the group ran, the gate held ONE ticket with both kinds for the suite group: '$seen'"
    . $libPath
    Set-StrictMode -Off
    Assert-Equal 0 @(Get-TestSlotEntries -Store $store).Count "the ticket is released after the group"
    $log = @([System.IO.File]::ReadAllLines((Get-TestSlotLogPath -Store $store)) | Where-Object { $_ -match 'role=gate' })
    Assert-Equal 1 $log.Count "one run of the gate in the queue's log: $($log -join ' | ')"
    Assert-True (@($out | Where-Object { $_ -match '^\s*Queue step A\s+PASS' }).Count -eq 1 -and @($out | Where-Object { $_ -match '^\s*Queue step B\s+PASS' }).Count -eq 1) "both rows in the table"
    Assert-True (@($out | Where-Object { $_ -match '^Test queue wait, total: [0-9.]+ s$' }).Count -eq 1) "the summary counts the wait: $(@($out | Where-Object { $_ -match "queue wait" }) -join " | ")"
}

# ------------------------------------------- 12: a slice of the gate

Test-Case "12. -OnlyStep runs only the matching steps, grouped or not; every other step keeps its row as SKIPPED and is not run" {
    # The full gate is longer than one foreground call may last (60 min): it is measured in
    # slices, and a slice must say what it skipped instead of dropping the rows.
    $dir = New-CaseDir
    $ranRoot = Join-Path $dir "ran"
    New-Item -ItemType Directory -Force -Path $ranRoot | Out-Null
    $stepsFile = Join-Path $dir "steps.ps1"
    $lines = New-Object System.Collections.ArrayList
    foreach ($n in @("Kept A", "Dropped B")) {
        $suite = Join-Path $dir ("suite-" + ($n -replace ' ', '') + ".ps1")
        [System.IO.File]::WriteAllText($suite, "[System.IO.File]::WriteAllText('$(Join-Path $ranRoot ($n -replace ' ', ''))', 'x')`r`nexit 0`r`n", (New-Object System.Text.ASCIIEncoding))
        [void]$lines.Add("Invoke-Step `"Seq $n`" { `$script = '$suite'; Invoke-GateSuite `$script; Assert-ExitCode `"seq $n`" }")
    }
    [void]$lines.Add("Start-GateGroup")
    foreach ($n in @("Kept C", "Dropped D")) {
        $suite = Join-Path $dir ("suite-" + ($n -replace ' ', '') + ".ps1")
        [System.IO.File]::WriteAllText($suite, "[System.IO.File]::WriteAllText('$(Join-Path $ranRoot ($n -replace ' ', ''))', 'x')`r`nexit 0`r`n", (New-Object System.Text.ASCIIEncoding))
        [void]$lines.Add("Invoke-Step `"Grp $n`" { `$script = '$suite'; Invoke-GateSuite `$script; Assert-ExitCode `"grp $n`" }")
    }
    [void]$lines.Add("Complete-GateGroup")
    [System.IO.File]::WriteAllText($stepsFile, (($lines -join "`r`n") + "`r`n"), (New-Object System.Text.ASCIIEncoding))
    $out = @(& $powershell -NoProfile -ExecutionPolicy Bypass -File $gatePath -StepList $stepsFile -NoTestSlots -OnlyStep "*Kept*" 2>&1 | ForEach-Object { [string]$_ })
    Assert-Equal 0 $LASTEXITCODE "the slice passed ($($out -join ' / '))"
    $ran = @(Get-ChildItem -LiteralPath $ranRoot -File | ForEach-Object { $_.Name } | Sort-Object)
    Assert-Equal "KeptA|KeptC" ($ran -join "|") "only the matching steps ran"
    foreach ($row in @("Seq Kept A\s+PASS", "Seq Dropped B\s+SKIPPED", "Grp Kept C\s+PASS", "Grp Dropped D\s+SKIPPED")) {
        Assert-True (@($out | Where-Object { $_ -match "^\s*$row" }).Count -eq 1) "the table has the row '$row'"
    }
    # A list through -File arrives as ONE comma-joined string ("a,b"): the slice must still
    # run what the list names (2026-10-04: a nine-pattern slice skipped every step).
    Get-ChildItem -LiteralPath $ranRoot -File | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
    Assert-Equal 0 @(Get-ChildItem -LiteralPath $ranRoot -File).Count "the markers of the first slice are gone"
    $out = @(& $powershell -NoProfile -ExecutionPolicy Bypass -File $gatePath -StepList $stepsFile -NoTestSlots -OnlyStep "Nothing*,*Kept A,Grp Kept*" 2>&1 | ForEach-Object { [string]$_ })
    Assert-Equal 0 $LASTEXITCODE "the comma-listed slice passed ($($out -join ' / '))"
    $ran = @(Get-ChildItem -LiteralPath $ranRoot -File | ForEach-Object { $_.Name } | Sort-Object)
    Assert-Equal "KeptA|KeptC" ($ran -join "|") "a comma list through -File runs each pattern's steps"
}

Test-Case "13. a slice keeps a skipped grouped step in its LISTED place, and names a pattern that matched no step" {
    # 2026-10-05, the branch's own slice: the skipped grouped rows printed above the group's run
    # rows, and seven patterns cut at a comma inside a step name matched nothing, silently.
    $dir = New-CaseDir
    $suite = Join-Path $dir "suite-pass.ps1"
    [System.IO.File]::WriteAllText($suite, "exit 0`r`n", (New-Object System.Text.ASCIIEncoding))
    $lines = @("Start-GateGroup")
    foreach ($n in @("Dropped first", "Kept second", "Dropped third", "Kept fourth")) {
        $lines += "Invoke-Step `"Grp $n`" { `$script = '$suite'; Invoke-GateSuite `$script; Assert-ExitCode `"grp $n`" }"
    }
    $lines += "Complete-GateGroup"
    $stepsFile = Join-Path $dir "steps.ps1"
    [System.IO.File]::WriteAllText($stepsFile, (($lines -join "`r`n") + "`r`n"), (New-Object System.Text.ASCIIEncoding))
    $out = @(& $powershell -NoProfile -ExecutionPolicy Bypass -File $gatePath -StepList $stepsFile -NoTestSlots -OnlyStep "Grp Kept*,Nothing (has, a comma)" 2>&1 | ForEach-Object { [string]$_ })
    Assert-Equal 0 $LASTEXITCODE "the slice passed ($($out -join ' / '))"
    $rows = @($out | Where-Object { $_ -match '^\s*Grp (Dropped|Kept) \w+\s+(PASS|SKIPPED)' } | ForEach-Object { ($_ -replace '^\s*Grp (\w+ \w+)\s+(\w+).*$', '$1=$2') })
    Assert-Equal "Dropped first=SKIPPED|Kept second=PASS|Dropped third=SKIPPED|Kept fourth=PASS" ($rows -join "|") "the table keeps the listed order"
    $named = @($out | Where-Object { $_ -match '^OnlyStep: no step matched ' })
    Assert-Equal "OnlyStep: no step matched 'Nothing (has'|OnlyStep: no step matched 'a comma)'" ($named -join "|") "only the two patterns that matched nothing are named"
}

foreach ($p in @($script:Leftovers)) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
Remove-Item -LiteralPath $sandbox -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "gate steps: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
