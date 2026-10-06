<#
.SYNOPSIS
    The gate's API unit step runs in parallel (pytest-xdist) with a worker count from the
    machine, and serially when xdist is missing (team/plans/gate-unit-parallel-adr.md).

.DESCRIPTION
    Measured 2026-10-06: the step ran 17,000 tests in ONE process for 30-50 minutes, the
    longest step of a ~95-minute gate. The cases:

      1  Get-GateUnitWorkerCount, lifted from scripts/quality-gate.ps1: min(8, cores - 2),
         lowered by the free memory above the floor, serial (0) below two workers, serial
         without xdist, and the PAGENTOS_GATE_UNIT_WORKERS override;
      2  the REAL gate (-Fast -OnlyStep "API unit tests") with a fake uv on PATH that has
         xdist: pytest is called with -n <workers>;
      3  the same gate with a fake uv WITHOUT xdist: pytest is called without -n, and the
         step says why;
      4  a failing pytest is still a failed step under -n (the pass/fail meaning is kept);
      5  the REAL pytest under -n 2 on the five files whose test ids used to differ between
         processes (uuid4 parameters, set order): xdist refuses a run whose workers collected
         different ids, so this case is red until tests/conftest.py makes the ids stable.

    Run: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tests\gate-unit-parallel.tests.ps1
#>

[CmdletBinding()]
param([string]$Filter = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$apiRoot = Join-Path $repoRoot "services\api"
$gatePath = Join-Path $repoRoot "scripts\quality-gate.ps1"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

$script:Failures = 0
$script:Passes = 0
$sandbox = Join-Path $env:TEMP ("pagentos-gateunit-tests-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $sandbox | Out-Null

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

# The functions under test, lifted from the gate itself (never a copy).
$ast = [System.Management.Automation.Language.Parser]::ParseFile($gatePath, [ref]$null, [ref]$null)
function Import-GateFunction {
    param([string]$Name)
    $f = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $Name }, $true)
    if ($null -eq $f) { throw "scripts/quality-gate.ps1 has no function $Name" }
    . ([scriptblock]::Create($f.Extent.Text.Replace("function $Name", "function global:$Name")))
}

# A fake uv: records each call's arguments (one line per call) and answers "import xdist" with
# FAKE_UV_XDIST_EXIT; every other call exits FAKE_UV_PYTEST_EXIT.
$fakeBin = Join-Path $sandbox "bin"
New-Item -ItemType Directory -Force -Path $fakeBin | Out-Null
[System.IO.File]::WriteAllText((Join-Path $fakeBin "uv.cmd"), @'
@echo off
echo %*>>"%FAKE_UV_LOG%"
echo %* | findstr /C:"import xdist" >nul && exit /b %FAKE_UV_XDIST_EXIT%
exit /b %FAKE_UV_PYTEST_EXIT%
'@, (New-Object System.Text.ASCIIEncoding))

function Invoke-GateUnitStep {
    param([int]$XdistExit, [int]$PytestExit, [string]$Workers = "")
    $log = Join-Path $sandbox ([guid]::NewGuid().ToString("N").Substring(0, 8) + ".log")
    $saved = @{ PATH = $env:PATH; L = $env:FAKE_UV_LOG; X = $env:FAKE_UV_XDIST_EXIT; P = $env:FAKE_UV_PYTEST_EXIT; W = $env:PAGENTOS_GATE_UNIT_WORKERS }
    try {
        $env:PATH = $fakeBin + ";" + $env:PATH
        $env:FAKE_UV_LOG = $log
        $env:FAKE_UV_XDIST_EXIT = [string]$XdistExit
        $env:FAKE_UV_PYTEST_EXIT = [string]$PytestExit
        $env:PAGENTOS_GATE_UNIT_WORKERS = $Workers
        $out = @(& $powershell -NoProfile -ExecutionPolicy Bypass -File $gatePath -Fast -NoTestSlots -OnlyStep "API unit tests" 2>&1 | ForEach-Object { [string]$_ })
        $code = $LASTEXITCODE
    } finally {
        $env:PATH = $saved.PATH; $env:FAKE_UV_LOG = $saved.L; $env:FAKE_UV_XDIST_EXIT = $saved.X
        $env:FAKE_UV_PYTEST_EXIT = $saved.P; $env:PAGENTOS_GATE_UNIT_WORKERS = $saved.W
    }
    $calls = if (Test-Path -LiteralPath $log) { @([System.IO.File]::ReadAllLines($log) | ForEach-Object { $_.Trim() }) } else { @() }
    return [pscustomobject]@{ Out = $out; Code = $code; Calls = $calls; Pytest = @($calls | Where-Object { $_ -match '\bpytest\b' }) }
}

Write-Host "gate-unit-parallel"

Test-Case "1. the worker count: min(8, cores-2), lowered by memory above the floor, serial below two or without xdist, the override wins" {
    Import-GateFunction "Get-GateUnitWorkerCount"
    $rows = @(
        # cores, free GB, xdist, override, expected
        @(28, 40, $true, "", 8, "a big machine stops at eight"),
        @(6, 40, $true, "", 4, "cores minus two"),
        @(3, 40, $true, "", 0, "one worker is no parallel run: serial"),
        @(28, 14, $true, "", 3, "(14 - 8 GB floor) / 2 GB a worker = 3"),
        @(28, 11, $true, "", 0, "under two workers' memory above the floor: serial"),
        @(28, 4, $true, "", 0, "under the floor: serial"),
        @(28, 40, $false, "", 0, "no xdist: serial"),
        @(28, 40, $false, "6", 0, "no xdist: serial even with an override"),
        @(28, 4, $true, "6", 6, "the override wins over cores and memory"),
        @(28, 40, $true, "1", 0, "an override of one is serial"),
        @(28, 40, $true, "abc", 8, "a malformed override is ignored")
    )
    foreach ($r in $rows) {
        $got = Get-GateUnitWorkerCount -Cores $r[0] -FreeBytes ([int64]$r[1] * 1GB) -XdistPresent $r[2] -Override $r[3]
        Assert-Equal $r[4] $got ("cores {0}, {1} GB free, xdist {2}, override '{3}': {4}" -f $r[0], $r[1], $r[2], $r[3], $r[5])
    }
}

Test-Case "1b. the memory floor comes from team/cycle-settings.json test_memory_floor_gb (one copy, the test queue's), 8 when missing or malformed" {
    Import-GateFunction "Get-GateUnitMemoryFloorGb"
    Import-GateFunction "Get-GateUnitWorkerCount"
    $settings = Join-Path $sandbox "cycle-settings.json"
    foreach ($r in @(@('{"test_memory_floor_gb": 12}', 12), @('{"test_memory_floor_gb": 0}', 8), @('{"test_memory_floor_gb": "x"}', 8), @('{}', 8), @('not json', 8))) {
        [System.IO.File]::WriteAllText($settings, $r[0])
        Assert-Equal $r[1] (Get-GateUnitMemoryFloorGb -SettingsPath $settings) ("settings " + $r[0])
    }
    Assert-Equal 8 (Get-GateUnitMemoryFloorGb -SettingsPath (Join-Path $sandbox "missing.json")) "a missing file"
    Assert-Equal 4 (Get-GateUnitWorkerCount -Cores 28 -FreeBytes ([int64]20 * 1GB) -XdistPresent $true -FloorGb 12) "(20 GB free - 12 GB floor) / 2 GB a worker = 4"
    Assert-Equal 6 (Get-GateUnitWorkerCount -Cores 28 -FreeBytes ([int64]20 * 1GB) -XdistPresent $true -FloorGb 8) "(20 - 8) / 2 = 6"
}

Test-Case "2. the real gate with xdist present calls pytest with -n <workers> on tests/unit" {
    $run = Invoke-GateUnitStep -XdistExit 0 -PytestExit 0 -Workers "3"
    Assert-Equal 0 $run.Code ("the gate passes; output:`n" + ($run.Out -join "`n"))
    Assert-Equal 1 @($run.Pytest).Count ("one pytest call; calls: " + ($run.Calls -join " | "))
    Assert-True ($run.Pytest[0] -match 'pytest tests/unit .*-n 3\b') ("pytest is called with -n 3: " + $run.Pytest[0])
    Assert-True (@($run.Out | Where-Object { $_ -match 'API unit tests: 3 xdist workers' }).Count -eq 1) "the step names its worker count"
}

Test-Case "3. the real gate WITHOUT xdist runs pytest serially and says why" {
    $run = Invoke-GateUnitStep -XdistExit 1 -PytestExit 0 -Workers "3"
    Assert-Equal 0 $run.Code ("the gate passes; output:`n" + ($run.Out -join "`n"))
    Assert-Equal 1 @($run.Pytest).Count ("one pytest call; calls: " + ($run.Calls -join " | "))
    Assert-True ($run.Pytest[0] -notmatch '\s-n\s') ("no -n without xdist: " + $run.Pytest[0])
    Assert-True (@($run.Out | Where-Object { $_ -match 'API unit tests: serial .*xdist' }).Count -eq 1) "the step says it runs serially because xdist is missing"
}

Test-Case "4. a failing pytest under -n is still a failed step and a failed gate" {
    $run = Invoke-GateUnitStep -XdistExit 0 -PytestExit 1 -Workers "3"
    Assert-Equal 1 $run.Code "the gate fails"
    Assert-True (@($run.Out | Where-Object { $_ -match 'FAILED: pytest \(unit\)' }).Count -ge 1) "the step's FAILED line names pytest (unit)"
}

Test-Case "5. the real pytest under -n 2: every worker collects the same ids in the five files that used to differ" {
    $uv = (Get-Command uv -ErrorAction SilentlyContinue)
    $uvPath = if ($uv) { $uv.Source } else { Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" }
    Push-Location $apiRoot
    try {
        $files = @("tests/unit/test_identity_enforcement.py", "tests/unit/test_evolution_routes.py", "tests/unit/test_mobile_routes.py",
                   "tests/unit/test_uistate_contract_halves.py", "tests/unit/test_evolution_backlog.py")
        $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        $out = @(& $uvPath run pytest @files -q -n 2 -p no:cacheprovider 2>&1 | ForEach-Object { [string]$_ })
        $code = $LASTEXITCODE
        $ErrorActionPreference = $prev
    } finally { Pop-Location }
    $tail = ($out | Select-Object -Last 15) -join "`n"
    Assert-True (@($out | Where-Object { $_ -match 'Different tests were collected' }).Count -eq 0) ("the workers collected different tests:`n" + $tail)
    Assert-Equal 0 $code ("pytest -n 2 passes:`n" + $tail)
}

Test-Case "6. the gate runs this suite, inside its parallel suite group (CI is off: the gate is the only runner)" {
    $text = [System.IO.File]::ReadAllText($gatePath)
    $start = $text.IndexOf("`n  Start-GateGroup")
    $end = $text.IndexOf("`n  Complete-GateGroup")
    $at = $text.IndexOf("scripts\tests\gate-unit-parallel.tests.ps1")
    Assert-True ($at -ge 0) "scripts/quality-gate.ps1 never names gate-unit-parallel.tests.ps1"
    Assert-True ($start -ge 0 -and $end -gt $start) "the gate has no Start-GateGroup ... Complete-GateGroup block"
    Assert-True ($at -gt $start -and $at -lt $end) "the suite is called outside the gate-parallel-suites group (between Start-GateGroup and Complete-GateGroup)"
}

Test-Case "7. a finished test's FastAPI app is freed: FastAPI's callable caches do not keep every create_app() alive (the suite grew to 21 GB in one process)" {
    # A probe plugin counts the FastAPI apps still alive when the session ends. Before
    # tests/conftest.py cleared fastapi.dependencies.models' lru_caches after each test, all 25
    # apps of this file were alive (their endpoint closures are the caches' keys).
    $probeDir = Join-Path $sandbox "probe"
    New-Item -ItemType Directory -Force -Path $probeDir | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $probeDir "liveapps_probe.py"), @'
import gc
import os


def pytest_sessionfinish(session):
    gc.collect()
    from fastapi import FastAPI

    with open(os.environ["LIVEAPPS_OUT"], "w") as fh:
        fh.write("LIVE_FASTAPI_APPS=%d\n" % sum(1 for o in gc.get_objects() if isinstance(o, FastAPI)))
'@, (New-Object System.Text.ASCIIEncoding))
    $countFile = Join-Path $probeDir "count.txt"
    $uv = (Get-Command uv -ErrorAction SilentlyContinue)
    $uvPath = if ($uv) { $uv.Source } else { Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" }
    $savedPath = $env:PYTHONPATH; $savedOut = $env:LIVEAPPS_OUT
    Push-Location $apiRoot
    try {
        $env:PYTHONPATH = $probeDir; $env:LIVEAPPS_OUT = $countFile
        $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        $out = @(& $uvPath run pytest tests/unit/test_allowlist_editor.py -q -p no:cacheprovider -p liveapps_probe 2>&1 | ForEach-Object { [string]$_ })
        $code = $LASTEXITCODE
        $ErrorActionPreference = $prev
    } finally { $env:PYTHONPATH = $savedPath; $env:LIVEAPPS_OUT = $savedOut; Pop-Location }
    $tail = ($out | Select-Object -Last 8) -join "`n"
    Assert-Equal 0 $code ("the file passes:`n" + $tail)
    $written = if (Test-Path -LiteralPath $countFile) { @([System.IO.File]::ReadAllLines($countFile)) } else { @() }
    $line = @($written | Where-Object { $_ -match '^LIVE_FASTAPI_APPS=(\d+)' })
    Assert-Equal 1 $line.Count ("the probe printed its count:`n" + $tail)
    $live = [int]([regex]::Match($line[0], '\d+').Value)
    Assert-True ($live -le 2) ("$live FastAPI apps of 25 tests are still alive after the session (at most the last test's may be)")
}

Remove-Item -LiteralPath $sandbox -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ""
Write-Host ("gate-unit-parallel: {0} passed, {1} failed" -f $script:Passes, $script:Failures)
if ($script:Failures -gt 0) { exit 1 }
exit 0
