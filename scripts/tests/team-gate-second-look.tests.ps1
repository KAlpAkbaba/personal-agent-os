<#
.SYNOPSIS
    The second look at a red gate (team/plans/gate-second-look-adr.md): which tests a red gate
    log names, how each is re-run, how the counts become a class, and how a polluter is found.

.DESCRIPTION
    Four halves.

    The reader (Get-GateFailedTests) is driven over gate logs written in the gate's own shape
    (scripts/quality-gate.ps1: "=== <step> ===", "FAILED: <why>", the summary table) under
    scripts/tests/fixtures/gate-second-look/: pytest, vitest, the PowerShell suites, and steps
    it cannot read.

    The classifier (Get-GateRedClass) is pure: the counts in, a class out - and no input of any
    shape gives anything that reads as green.

    The bisection (Find-GatePolluter) is driven with a fake -Invoke, and once with real pytest
    over the sandbox under fixtures/gate-second-look/sandbox/ (copied to TEMP; the services/api
    virtual environment, with a pytest.ini of its own so the service's conftest stays out).

    The script (scripts/team/gate-second-look.ps1) is run end to end with a fake runner: the
    JSON it writes, a zero budget, and a BEKLE from a real test-slot queue in a temp store.

    No test of the repository is run, nothing outside TEMP is written.

    Run: powershell -NoProfile -File scripts\tests\team-gate-second-look.tests.ps1
#>

[CmdletBinding()]
param(
    # Runs only the cases whose name matches this pattern (the mutation proofs re-run a slice).
    [string]$Filter = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\TeamGateSecondLook.ps1")
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")

$fixtures = Join-Path $PSScriptRoot "fixtures\gate-second-look"
$secondLook = Join-Path $repoRoot "scripts\team\gate-second-look.ps1"
$slotScript = Join-Path $repoRoot "scripts\team\test-slot.ps1"
$ps5 = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
# A test-slot child must not post to the team's board from a test.
foreach ($name in @("PAGENTOS_TEAM_URL", "PAGENTOS_TEAM_TOKEN_FILE", "PAGENTOS_TEAM_SEAT")) { Remove-Item -Path ("Env:" + $name) -ErrorAction SilentlyContinue }

$script:TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("gsl-" + [guid]::NewGuid().ToString("N").Substring(0, 10))
[void](New-Item -ItemType Directory -Path $script:TempRoot -Force)

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

function New-CaseDir {
    $d = Join-Path $script:TempRoot ("c-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    [void](New-Item -ItemType Directory -Path $d -Force)
    return $d
}

function Get-Fixture { param([string]$Name) return (Join-Path $fixtures $Name) }

function Assert-NoGreen {
    param($Result, [string]$Because)
    $text = ($Result | ConvertTo-Json -Depth 6 -Compress)
    Assert-True (-not ($text -match '(?i):"(yesil|ye\u015fil|green|pass|passed|gecti|ge\u00e7ti)"')) "$Because - a green-like value came out: $text"
}

# ============================================================================ the reader

Write-Host ""
Write-Host "the reader: which tests a red gate log names"

Test-Case "pytest: the summary's FAILED and ERROR lines, one record per test (parameters cut), the green step not read" {
    $r = @(Get-GateFailedTests -LogPath (Get-Fixture "gate-pytest.log"))
    Assert-Equal 3 @($r).Count "three tests: the logging one, the route one (two parameters, one test) and the ledger setup error: $(@($r | ForEach-Object { $_.id }) -join ' | ')"
    Assert-Equal "tests/unit/test_logging_context.py::test_task_id_defaults_to_none_in_logs" $r[0].id "the first FAILED line, id relative to services/api"
    Assert-Equal "tests/unit/test_voice_routes.py::test_route_matches" $r[1].id "[misheard-2] and [misheard-3] are one test"
    Assert-Equal "tests/unit/test_ledger_explain.py::test_ledger_explain_over_real_runs" $r[2].id "ERROR (a setup failure) is a failed test too"
    foreach ($x in $r) {
        Assert-Equal "api-unit" $x.paket "the API unit step's tests are api-unit"
        Assert-Equal "API unit tests" $x.adim "the step is named"
    }
    Assert-Equal "tests/unit/test_logging_context.py" $r[0].dosya "the file is the id's part before ::"
}

Test-Case "vitest: the FAIL line gives the file and the test's name, the build step not read" {
    $r = @(Get-GateFailedTests -LogPath (Get-Fixture "gate-vitest.log"))
    Assert-Equal 1 @($r).Count "one failed web test: $(@($r | ForEach-Object { $_.id }) -join ' | ')"
    Assert-Equal "web" $r[0].paket "the web step's tests are web"
    Assert-Equal "tests/voice/session-storm.test.ts" $r[0].dosya "the file"
    Assert-Equal "tests/voice/session-storm.test.ts > session storm > survives fifty reconnects" $r[0].id "the whole FAIL line"
    Assert-Equal "survives fifty reconnects" $r[0].ad "the test's own name, for -t"
}

Test-Case "the PowerShell suites: the repository's '  FAIL  <case>' and Pester's '[-] <case>', each with its .tests.ps1 file" {
    $r = @(Get-GateFailedTests -LogPath (Get-Fixture "gate-pester.log"))
    Assert-Equal 2 @($r).Count "one case per red step: $(@($r | ForEach-Object { $_.id }) -join ' | ')"
    Assert-Equal "a worker run that ends with no commit is returned" $r[0].id "the runner's FAIL line"
    Assert-Equal "ps-suite" $r[0].paket "a PowerShell suite"
    Assert-Equal "scripts/tests/team-cycle.tests.ps1" $r[0].dosya "the step's .tests.ps1, read from the gate script"
    Assert-Equal "posts a note with a reply id" $r[1].id "Pester's [-] line, its time cut"
    Assert-Equal "scripts/tests/team-board.tests.ps1" $r[1].dosya "the board step's file"
}

Test-Case "a red step it cannot read is one record with no id and paket 'tanınmadı', and no class" {
    $r = @(Get-GateFailedTests -LogPath (Get-Fixture "gate-unknown.log"))
    Assert-Equal 2 @($r).Count "the secret step and the dotnet step"
    foreach ($x in $r) {
        Assert-Equal "" $x.id "no id is guessed"
        Assert-Equal "tan$([char]0x0131)nmad$([char]0x0131)" $x.paket "the record says it was not recognised"
        Assert-True (-not ($x.PSObject.Properties.Name -contains "sinif")) "the reader gives no class"
    }
    Assert-Equal "Secret hygiene" $r[0].adim "the step is named"
}

Test-Case "a log with a byte-order mark and CRLF lines reads the same" {
    $d = New-CaseDir
    $text = [System.IO.File]::ReadAllText((Get-Fixture "gate-pytest.log")) -replace "`r?`n", "`r`n"
    $path = Join-Path $d "gate-1.log"
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding $true))
    $r = @(Get-GateFailedTests -LogPath $path)
    Assert-Equal 3 @($r).Count "the same three tests"
    Assert-Equal "tests/unit/test_logging_context.py::test_task_id_defaults_to_none_in_logs" $r[0].id "no CR left on the id"
}

Test-Case "the re-run table: one place per package, the id and file in the command, the database marked" {
    $t = [pscustomobject]@{ id = "tests/unit/test_a.py::test_b"; paket = "api-unit"; adim = "API unit tests"; dosya = "tests/unit/test_a.py"; ad = "" }
    $c = Get-GateRerunCommand -Test $t -Kind tek -Root "C:\gate"
    Assert-True ((@($c.args) -join " ") -match [regex]::Escape("pytest -q -p no:cacheprovider tests/unit/test_a.py::test_b")) "the alone run names the test: $(@($c.args) -join ' ')"
    Assert-Equal "C:\gate\services\api" $c.cwd "pytest runs in services/api"
    Assert-True ($c.env.ContainsKey("PAGENTOS_TEST_SHARD") -and $null -eq $c.env["PAGENTOS_TEST_SHARD"]) "the shard variable is cleared, or the test may not be selected and look green"
    $f = Get-GateRerunCommand -Test $t -Kind dosya -Root "C:\gate"
    Assert-True ((@($f.args) -join " ") -match 'tests/unit/test_a\.py$') "the file run names the file only: $(@($f.args) -join ' ')"
    $i = Get-GateRerunCommand -Test ([pscustomobject]@{ id = "tests/integration/test_x.py::test_y"; paket = "api-integration"; adim = "API integration tests"; dosya = "tests/integration/test_x.py"; ad = "" }) -Kind tek -Root "C:\gate"
    Assert-True ([bool]$i.db) "the integration package needs the gate's own database"
    $w = Get-GateRerunCommand -Test ([pscustomobject]@{ id = "tests/v.test.ts > s > n"; paket = "web"; adim = "x"; dosya = "tests/v.test.ts"; ad = "n (1)" }) -Kind tek -Root "C:\gate"
    Assert-True ((@($w.args) -join " ") -match 'vitest run tests/v\.test\.ts -t \^n\\ \\\(1\\\)\$') "vitest runs the file with the name as an escaped pattern: $(@($w.args) -join ' ')"
}

Test-Case "the re-run table: a PowerShell suite with -Filter runs one case; one without has no alone run" {
    $t = [pscustomobject]@{ id = "the lock is released"; paket = "ps-suite"; adim = "x"; dosya = "scripts/tests/team-cycle.tests.ps1"; ad = "" }
    $c = Get-GateRerunCommand -Test $t -Kind tek -Root $repoRoot
    Assert-True ((@($c.args) -join " ") -match [regex]::Escape("-Filter ^the\ lock\ is\ released$")) "the case alone, by an anchored pattern: $(@($c.args) -join ' ')"
    $d = New-CaseDir
    [void](New-Item -ItemType Directory -Path (Join-Path $d "scripts\tests") -Force)
    Set-Content -LiteralPath (Join-Path $d "scripts\tests\plain.tests.ps1") -Value "param()`n" -Encoding UTF8
    $p = [pscustomobject]@{ id = "a case"; paket = "ps-suite"; adim = "x"; dosya = "scripts/tests/plain.tests.ps1"; ad = "" }
    Assert-True ($null -eq (Get-GateRerunCommand -Test $p -Kind tek -Root $d)) "no -Filter, no alone run"
    Assert-True ($null -ne (Get-GateRerunCommand -Test $p -Kind dosya -Root $d)) "the file run is still there"
}

# ============================================================================ the classifier

Write-Host ""
Write-Host "the classifier: counts in, a class out"

Test-Case "fails 5/5 alone and in its file -> gercek" {
    $c = Get-GateRedClass -Alone 0 -InFile 0
    Assert-Equal "gercek" $c.sinif "fails every time"
    Assert-Equal $false $c.main_de_de "no main run, no main claim"
    $m = Get-GateRedClass -Alone 0 -InFile 0 -OnMain 0
    Assert-Equal $true $m.main_de_de "it fails on main too"
}

Test-Case "passes 2/5 alone -> kararsiz, and 0/5 is NOT kararsiz" {
    Assert-Equal "kararsiz" (Get-GateRedClass -Alone 2 -InFile 0).sinif "passes sometimes"
    Assert-Equal "kararsiz" (Get-GateRedClass -Alone 0 -InFile 3).sinif "passes sometimes in its file"
    Assert-True ("kararsiz" -ne (Get-GateRedClass -Alone 0 -InFile 0).sinif) "never passing is not flaky"
    Assert-Equal $false (Get-GateRedClass -Alone 2 -InFile 2 -OnMain 5).main_de_de "main passed 5/5: the branch's flake"
}

Test-Case "passes 2/5 on main too -> kararsiz with main_de_de" {
    $c = Get-GateRedClass -Alone 2 -InFile 3 -OnMain 2
    Assert-Equal "kararsiz" $c.sinif "flaky"
    Assert-Equal $true $c.main_de_de "flaky on main too: not this branch's fault"
}

Test-Case "passes 5/5 alone but failed in the suite -> siraya_bagli (earlier files, or the same file)" {
    $c = Get-GateRedClass -Alone 5 -InFile 5 -FailedInSuite $true
    Assert-Equal "siraya_bagli" $c.sinif "alone and in its file green, red in the suite"
    Assert-Equal "onceki_dosyalar" $c.yer "the polluter is in an earlier file"
    $s = Get-GateRedClass -Alone 5 -InFile 0 -FailedInSuite $true
    Assert-Equal "siraya_bagli" $s.sinif "alone green, red in its own file"
    Assert-Equal "ayni_dosya" $s.yer "the polluter is in the same file"
}

Test-Case "over the budget -> yarim and no class; nothing counted -> yarim" {
    $c = Get-GateRedClass -Alone 0 -InFile 0 -OverBudget $true
    Assert-Equal "yarim" $c.sinif "over the budget"
    Assert-Equal "yarim" (Get-GateRedClass).sinif "no counts"
}

Test-Case "no input of any shape gives a green-like class (the whole grid, with nulls)" {
    $values = @($null, 0, 1, 2, 3, 4, 5)
    $allowed = @("gercek", "kararsiz", "siraya_bagli", "yarim")
    $n = 0
    foreach ($a in $values) { foreach ($f in $values) { foreach ($m in $values) { foreach ($s in @($true, $false)) { foreach ($b in @($true, $false)) {
        $c = Get-GateRedClass -Alone $a -InFile $f -OnMain $m -FailedInSuite $s -OverBudget $b
        Assert-True ($allowed -contains $c.sinif) "a class outside the four: <$($c.sinif)> for $a/$f/$m/$s/$b"
        Assert-NoGreen $c "alone $a, file $f, main $m"
        $n++
    } } } } }
    Assert-Equal 1372 $n "every combination was asked"
}

# ============================================================================ the bisection

Write-Host ""
Write-Host "the bisection: the polluter by halves"

Test-Case "16 candidate files, the polluter found by name in at most 5 runs" {
    $files = @(1..16 | ForEach-Object { "tests/unit/test_c{0:D2}.py" -f $_ })
    $count = @{ Runs = 0 }
    $r = Find-GatePolluter -Candidates $files -Target "tests/unit/test_z.py::test_v" -Invoke {
        param($Files, $Target)
        $count.Runs++
        return (@($Files) -contains "tests/unit/test_c11.py")
    }.GetNewClosure()
    Assert-Equal "bulundu" $r.durum "found"
    Assert-Equal "tests/unit/test_c11.py" $r.kirleten "the polluter by name"
    Assert-True ($r.adimlar -le 5) "at most 5 runs, took $($r.adimlar)"
    Assert-Equal $count.Runs $r.adimlar "the steps are the runs"
    foreach ($p in @(1, 8, 9, 16)) {
        $want = "tests/unit/test_c{0:D2}.py" -f $p
        $x = Find-GatePolluter -Candidates $files -Target "t" -Invoke { param($Files, $Target) return (@($Files) -contains $want) }.GetNewClosure()
        Assert-Equal $want $x.kirleten "polluter at $p"
    }
}

Test-Case "no polluter -> 'bulunamadı' after one run" {
    $files = @(1..16 | ForEach-Object { "tests/unit/test_c{0:D2}.py" -f $_ })
    $r = Find-GatePolluter -Candidates $files -Target "t" -Invoke { param($Files, $Target) return $false }
    Assert-Equal "bulunamad$([char]0x0131)" $r.durum "not found"
    Assert-True ($null -eq $r.kirleten -or "" -eq $r.kirleten) "no name"
    Assert-Equal 1 $r.adimlar "one run tells"
}

Test-Case "past the deadline -> yarim with the range still open; an inconclusive run -> yarim" {
    $files = @(1..16 | ForEach-Object { "f{0:D2}" -f $_ })
    $r = Find-GatePolluter -Candidates $files -Target "t" -Deadline ([datetime]::UtcNow.AddMinutes(-1)) -Invoke { param($Files, $Target) return $true }
    Assert-Equal "yarim" $r.durum "no time"
    Assert-Equal 16 $r.aralik "all 16 still candidates"
    $q = Find-GatePolluter -Candidates $files -Target "t" -Invoke { param($Files, $Target) return $null }
    Assert-Equal "yarim" $q.durum "a run that said nothing"
}

Test-Case "real pytest in a TEMP sandbox: the polluter found by name" {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    $api = Join-Path $repoRoot "services\api"
    if ($null -eq $uv -or -not (Test-Path -LiteralPath (Join-Path $api ".venv"))) { Write-Host "        SKIP: no uv or no services/api/.venv"; return }
    $box = New-CaseDir
    Copy-Item -Path (Join-Path $fixtures "sandbox\*") -Destination $box
    Set-Content -LiteralPath (Join-Path $box "pytest.ini") -Value "[pytest]" -Encoding ASCII
    $candidates = @(Get-ChildItem -LiteralPath $box -Filter "test_c*.py" | Sort-Object Name | ForEach-Object { $_.Name })
    Assert-Equal 16 @($candidates).Count "16 candidate files"
    $calls = New-Object System.Collections.ArrayList
    # A closure sees only what it captured: the function comes along as a value.
    $native = ${function:Invoke-NativeProcess}
    $invoke = {
        param($Files, $Target)
        $argv = @("run", "--offline", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", $box, "-c", (Join-Path $box "pytest.ini")) + @($Files | ForEach-Object { Join-Path $box $_ }) + @(Join-Path $box $Target)
        $p = & $native -FilePath $uv.Source -Arguments $argv -WorkingDirectory $api -SuccessExitCodes @(0, 1) -TimeoutSeconds 300
        $last = @(($p.StdOut -split "`r?`n") | Where-Object { $_ -match '\d+ (passed|failed)' }) | Select-Object -Last 1
        [void]$calls.Add(("{0} file(s) -> exit {1}: {2}" -f @($Files).Count, $p.ExitCode, $last))
        if ($p.ExitCode -eq 1 -and $p.StdOut -match 'FAILED \S*test_z_victim\.py::test_victim_needs_a_clean_flag') { return $true }
        if ($p.ExitCode -eq 0) { return $false }
        return $null
    }.GetNewClosure()
    $r = Find-GatePolluter -Candidates $candidates -Target "test_z_victim.py::test_victim_needs_a_clean_flag" -Invoke $invoke
    foreach ($line in $calls) { Write-Host "        pytest: $line" }
    Assert-Equal "bulundu" $r.durum "found with real pytest"
    Assert-Equal "test_c07_polluter.py" $r.kirleten "the polluter by name"
    Assert-True ($r.adimlar -le 5) "at most 5 real runs, took $($r.adimlar)"
}

# ============================================================================ the script

Write-Host ""
Write-Host "the script: end to end with a fake runner"

function New-FakeRunner {
    <# The runner the script calls instead of starting a process. Records every command. #>
    $state = @{ Calls = (New-Object System.Collections.ArrayList); Seen = @{} }
    $script:FakeCalls = $state.Calls
    return {
        param($Command)
        [void]$state.Calls.Add($Command)
        $joined = @($Command.args) -join " "
        $onMain = ([string]$Command.cwd) -like "*main-tree*"
        if ($joined -match '--collect-only') {
            $lines = @(1..16 | ForEach-Object { "tests/unit/test_c{0:D2}.py::test_one" -f $_ })
            $lines += "tests/unit/test_logging_context.py::test_task_id_defaults_to_none_in_logs"
            $lines += "tests/unit/test_ledger_explain.py::test_ledger_explain_over_real_runs"
            $lines += "tests/unit/test_voice_routes.py::test_route_matches"
            return [pscustomobject]@{ ExitCode = 0; Output = (($lines + "", "19 tests collected in 1.20s") -join "`n"); TimedOut = $false }
        }
        if ($joined -match 'test_logging_context') {
            if ($joined -match 'test_c07') {
                return [pscustomobject]@{ ExitCode = 1; Output = "FAILED tests/unit/test_logging_context.py::test_task_id_defaults_to_none_in_logs - AssertionError`n1 failed, 8 passed in 1.0s"; TimedOut = $false }
            }
            return [pscustomobject]@{ ExitCode = 0; Output = "1 passed in 0.3s"; TimedOut = $false }
        }
        if ($joined -match 'test_voice_routes') {
            $key = "$onMain|" + ($joined -match '::')
            if (-not $state.Seen.ContainsKey($key)) { $state.Seen[$key] = 0 }
            $state.Seen[$key]++
            if ($state.Seen[$key] -le 2) { return [pscustomobject]@{ ExitCode = 0; Output = "3 passed in 0.3s"; TimedOut = $false } }
            return [pscustomobject]@{ ExitCode = 1; Output = "FAILED tests/unit/test_voice_routes.py::test_route_matches[misheard-2] - assert`n1 failed, 2 passed"; TimedOut = $false }
        }
        if ($joined -match 'test_ledger_explain') {
            return [pscustomobject]@{ ExitCode = 1; Output = "ERROR tests/unit/test_ledger_explain.py::test_ledger_explain_over_real_runs - OperationalError`n1 error in 0.5s"; TimedOut = $false }
        }
        return [pscustomobject]@{ ExitCode = 4; Output = "unexpected command: $joined"; TimedOut = $false }
    }.GetNewClosure()
}

function Invoke-SecondLook {
    param([hashtable]$Arguments)
    & $secondLook @Arguments | Out-Null
    return (Get-Content -LiteralPath $Arguments.OutFile -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function New-FreeStore { return (Join-Path $script:TempRoot ("s-" + [guid]::NewGuid().ToString("N").Substring(0, 8))) }

Test-Case "end to end: three pytest reds become gercek, kararsiz (on main too) and siraya_bagli with the polluter; one summary line" {
    $d = New-CaseDir
    $main = Join-Path $d "main-tree"; [void](New-Item -ItemType Directory -Path $main -Force)
    $out = Join-Path $d "second-look.json"
    $env:PAGENTOS_TEST_SHARD = "2/4"
    try {
        $j = Invoke-SecondLook @{ LogPath = (Get-Fixture "gate-pytest.log"); Worktree = $repoRoot; MainWorktree = $main; OutFile = $out; Invoke = (New-FakeRunner); TestSlotStore = (New-FreeStore) }
    } finally { Remove-Item Env:PAGENTOS_TEST_SHARD -ErrorAction SilentlyContinue }
    Assert-Equal "tamam" $j.durum "the look finished"
    $byId = @{}; foreach ($t in @($j.testler)) { $byId[[string]$t.id] = $t }
    Assert-Equal 3 @($j.testler).Count "three tests"
    $log = $byId["tests/unit/test_logging_context.py::test_task_id_defaults_to_none_in_logs"]
    Assert-Equal "siraya_bagli" $log.sinif "green alone and in its file, red in the suite"
    Assert-Equal "tests/unit/test_c07.py" $log.kirleten "the polluter by name"
    Assert-Equal 5 $log.sayilar.tek.gecti "5/5 alone"
    $route = $byId["tests/unit/test_voice_routes.py::test_route_matches"]
    Assert-Equal "kararsiz" $route.sinif "2/5"
    Assert-Equal 2 $route.sayilar.tek.gecti "2/5 alone"
    Assert-Equal 2 $route.sayilar.main.gecti "2/5 on main"
    Assert-Equal $true $route.main_de_de "flaky on main too"
    $ledger = $byId["tests/unit/test_ledger_explain.py::test_ledger_explain_over_real_runs"]
    Assert-Equal "gercek" $ledger.sinif "fails every time"
    Assert-Equal 0 $ledger.sayilar.dosya.gecti "0/5 in its file"
    Assert-True ([string]$j.ozet -match "^kap$([char]0x0131) k$([char]0x0131)rm$([char]0x0131)z$([char]0x0131): ") "the summary line: $($j.ozet)"
    Assert-True ([string]$j.ozet -match [regex]::Escape("kararsız test tests/unit/test_voice_routes.py::test_route_matches (tek başına 2/5, dosya sırasında 2/5, main'de 2/5)")) "the flaky test with its counts: $($j.ozet)"
    $shards = @($script:FakeCalls | Where-Object { $_.env.ContainsKey("PAGENTOS_TEST_SHARD") -and $null -eq $_.env["PAGENTOS_TEST_SHARD"] })
    Assert-Equal @($script:FakeCalls).Count @($shards).Count "every pytest run clears the shard variable"
    Assert-NoGreen $j "the record"
}

Test-Case "a zero budget: 'ikinci bakış yarım kaldı', no class, no run" {
    $d = New-CaseDir; $out = Join-Path $d "o.json"
    $j = Invoke-SecondLook @{ LogPath = (Get-Fixture "gate-pytest.log"); Worktree = $repoRoot; OutFile = $out; BudgetMinutes = 0; Invoke = (New-FakeRunner); TestSlotStore = (New-FreeStore) }
    Assert-Equal "yarim" $j.durum "half done"
    Assert-True ([string]$j.ozet -match "ikinci bak$([char]0x0131)$([char]0x015f) yar$([char]0x0131)m kald$([char]0x0131)") "the summary says so: $($j.ozet)"
    foreach ($t in @($j.testler)) { Assert-Equal "yarim" $t.sinif "no class for $($t.id)" }
    Assert-Equal 0 @($script:FakeCalls).Count "nothing was run"
}

Test-Case "test-slot BEKLE: no run, 'yarim'" {
    $d = New-CaseDir; $out = Join-Path $d "o.json"; $store = New-FreeStore
    # heavy holds more than one: others take slots until the queue says BEKLE to one of them.
    $codes = @()
    foreach ($i in 1..6) {
        $hold = Invoke-NativeProcess -FilePath $ps5 -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $slotScript, "ask", "-Kind", "heavy", "-Task", "someone-else-$i", "-Role", "worker", "-What", "a heavy run", "-Store", $store) -SuccessExitCodes @(0, 3)
        $codes += $hold.ExitCode
        if ($hold.ExitCode -eq 3) { break }
    }
    Assert-Equal 3 $codes[-1] "the heavy slots are full: $($codes -join ',')"
    $j = Invoke-SecondLook @{ LogPath = (Get-Fixture "gate-pytest.log"); Worktree = $repoRoot; OutFile = $out; Invoke = (New-FakeRunner); TestSlotStore = $store }
    Assert-Equal "yarim" $j.durum "half done"
    Assert-True ([string]$j.ozet -match "BEKLE") "the summary names the queue's answer: $($j.ozet)"
    foreach ($t in @($j.testler)) { Assert-Equal "yarim" $t.sinif "no class for $($t.id)" }
    Assert-Equal 0 @($script:FakeCalls).Count "never run outside the queue"
}

Test-Case "a database test without the gate's own database: never run, 'yarim'" {
    $d = New-CaseDir; $out = Join-Path $d "o.json"
    $log = Join-Path $d "gate-2.log"
    $text = "=== API integration tests ===`nFAILED tests/integration/test_watch.py::test_purge - x`n1 failed, 9 passed`nFAILED: pytest (integration) exited with code 1`n`n=== Quality gate summary ===`nQUALITY GATE: FAIL`n"
    [System.IO.File]::WriteAllText($log, $text)
    $j = Invoke-SecondLook @{ LogPath = $log; Worktree = $repoRoot; OutFile = $out; Invoke = (New-FakeRunner); TestSlotStore = (New-FreeStore) }
    Assert-Equal 1 @($j.testler).Count "one test"
    Assert-Equal "yarim" $j.testler[0].sinif "no class"
    Assert-True ([string]$j.testler[0].sebep -match "veritaban") "the reason names the database: $($j.testler[0].sebep)"
    Assert-Equal 0 @($script:FakeCalls).Count "the shared dev database is never touched"
}

Test-Case "an unreadable red step is in the record with no class and named in the summary" {
    $d = New-CaseDir; $out = Join-Path $d "o.json"
    $j = Invoke-SecondLook @{ LogPath = (Get-Fixture "gate-unknown.log"); Worktree = $repoRoot; OutFile = $out; Invoke = (New-FakeRunner); TestSlotStore = (New-FreeStore) }
    Assert-Equal 2 @($j.testler).Count "two steps"
    foreach ($t in @($j.testler)) { Assert-True ($null -eq $t.sinif) "no class for an unread step" }
    Assert-True ([string]$j.ozet -match "Secret hygiene") "the summary names the step: $($j.ozet)"
    Assert-Equal 0 @($script:FakeCalls).Count "nothing to re-run"
}

# ============================================================================ done

Remove-Item -LiteralPath $script:TempRoot -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "gate-second-look: $($script:Passes) passed, $($script:Failures) failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
