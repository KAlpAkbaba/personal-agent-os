<#
.SYNOPSIS
    A stand-in for scripts/quality-gate.ps1 in scripts/tests/team-integrate.tests.ps1. It runs
    no test, starts no service and touches no file but its own log.

.DESCRIPTION
    scripts/team/integrate.ps1 is given this file with -GatePath. It prints what the real gate
    prints - a "=== step ===" line per step, the "FAILED: " line of a step that failed, the
    summary table and the last word - and exits as the real gate exits. The scenario is the
    test's, in PAGENTOS_FAKE_GATE_SCENARIO:

      green         every step passes; "QUALITY GATE: PASS"; exit 0
      red           the step "API unit tests" fails; the failing test's line names the text of
                    PAGENTOS_FAKE_GATE_NAMES (a file path); "QUALITY GATE: FAIL"; exit 1
      pass-exit-1   prints "QUALITY GATE: PASS" and exits 1 (a gate that died after its last word)
      fail-exit-0   prints "QUALITY GATE: FAIL" and exits 0
      silent        prints two steps and no last word; exit 0 (a script that is not the gate)

    Every call appends one line to PAGENTOS_FAKE_GATE_LOG: "<working directory>|<HEAD sha>|<scenario>",
    so a test can see WHERE the gate ran and on WHICH commit - and that it ran at all.
    PAGENTOS_FAKE_GATE_TOUCH names a tracked file (relative) the gate appends a line to, in any
    scenario: a gate that leaves its worktree dirty.
    A test in the suite holds the words printed here to the real gate's source.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$scenario = [string]$env:PAGENTOS_FAKE_GATE_SCENARIO
if (-not $scenario) { $scenario = "green" }
$names = [string]$env:PAGENTOS_FAKE_GATE_NAMES
$log = [string]$env:PAGENTOS_FAKE_GATE_LOG

$here = (Get-Location).ProviderPath
if ($log) {
    $head = (& git.exe rev-parse HEAD 2>$null | Out-String).Trim()
    Add-Content -LiteralPath $log -Value "$here|$head|$scenario" -Encoding UTF8
}
# A gate that leaves a tracked file changed behind it (a formatter, a regenerated lock file).
$touch = [string]$env:PAGENTOS_FAKE_GATE_TOUCH
if ($touch) { Add-Content -LiteralPath (Join-Path $here ($touch -replace "/", "\")) -Value "left by the gate" -Encoding ASCII }

Write-Host ""
Write-Host "=== Required files ==="
Write-Host "all present"
Write-Host ""
Write-Host "=== API unit tests ==="
$unit = "PASS"
if ($scenario -eq "red") {
    $unit = "FAIL"
    Write-Host "tests/unit/test_gate_probe.py F"
    Write-Host "E   AssertionError: the change in $names broke it"
    Write-Host "FAILED tests/unit/test_gate_probe.py::test_the_change_holds - AssertionError"
    Write-Host "FAILED: pytest (unit) exited with code 1"
}
else { Write-Host "5400 passed" }

if ($scenario -eq "silent") { exit 0 }

Write-Host ""
Write-Host "=== Quality gate summary ==="
Write-Host ""
Write-Host "Step           Result Seconds"
Write-Host "----           ------ -------"
Write-Host "Required files PASS       0.1"
Write-Host "API unit tests $unit       1.2"
Write-Host ""

switch ($scenario) {
    "red" { Write-Host "QUALITY GATE: FAIL"; exit 1 }
    "fail-exit-0" { Write-Host "QUALITY GATE: FAIL"; exit 0 }
    "pass-exit-1" { Write-Host "QUALITY GATE: PASS"; exit 1 }
    default { Write-Host "QUALITY GATE: PASS"; exit 0 }
}
