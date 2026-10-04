<#
.SYNOPSIS
    The gate's own database (scripts/lib/GateDatabase.ps1, team/plans/gate-faster-adr.md): the
    gate and the inspectors no longer reset the one dev database `pagentos` under each other.

.DESCRIPTION
    ADR-0254 open decision 5, ruled (b): an inspection lost its probe to `relation
    "owner_sessions" does not exist` while a gate reset `pagentos`. The gate now makes a
    database of its own on the dev stack's PostgreSQL server (inside the running container,
    no password anywhere), runs the PostgreSQL steps against it and drops it in a finally.

    The cases, by number (the card's acceptance names them):

      1  New- makes a database the application's migrations run on to head; Remove- drops it
         (the REAL dev server);
      2  the name rule: nine names, each refused by New- AND by Remove- before any command is
         sent (a fake docker records nothing) - eighteen refusals;
      3  two gate databases do not see each other, and `pagentos` is the same before and after
         the whole suite (its tables, and the rows of three named tables);
      4  the sweep drops a gate database older than the bound and leaves a younger one, a
         scratch one and `pagentos` (an injected clock and the name's own stamp; no sleeping);
      5  no password: the fake docker's recorded command lines and everything the functions
         printed are scanned for the dev password and for `PGPASSWORD=` with a value;
      6  without Docker (a fake docker that says "not running") the server cases are skipped
         with a printed count and the suite exits 0;
      7  the gate's own database block, read from scripts/quality-gate.ps1 and run with a fake
         uv whose integration run fails: the database is dropped all the same, the children
         saw the gate's database, and a later step sees the variable's original value.

    Server cases run only when the dev stack's PostgreSQL answers (TEAM_PROTOCOL 9a: on this
    machine that is every run); without it they are counted and skipped, never passed.

    Run: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\tests\gate-database.tests.ps1
#>

[CmdletBinding()]
param(
    # Runs only the cases whose name matches this pattern (the mutation proofs re-run a slice).
    [string]$Filter = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$apiRoot = Join-Path $repoRoot "services\api"
$gatePath = Join-Path $repoRoot "scripts\quality-gate.ps1"
. (Join-Path $repoRoot "scripts\lib\GateDatabase.ps1")

$script:Failures = 0
$script:Passes = 0
$script:Skipped = 0
$script:Printed = New-Object System.Collections.ArrayList
$sandbox = Join-Path $env:TEMP ("pagentos-gatedb-tests-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $sandbox | Out-Null

# The docker the library found for this machine, before any case points it at a fake.
$realDocker = $script:GateDockerPath
$serverUp = Test-GateDatabaseServer
# Case 6 runs this file again as a child with a docker that is "not running".
$isChildRun = [bool]$env:PAGENTOS_GATE_DB_TESTS_CHILD

# The dev password, read from the compose file rather than typed here: the scan looks for
# whatever the dev stack really uses.
$composeText = [System.IO.File]::ReadAllText((Join-Path $repoRoot "infra\docker\docker-compose.dev.yml"))
$devPassword = ""
if ($composeText -match 'POSTGRES_PASSWORD:\s*([^\s#]+)') { $devPassword = $Matches[1] }

function Test-Case {
    param([string]$Name, [scriptblock]$Body, [switch]$Server)
    if ($Filter -and $Name -notmatch $Filter) { return }
    if ($Server -and -not $serverUp) {
        $script:Skipped++
        Write-Host "  SKIP  $Name (the dev stack's PostgreSQL does not answer)"
        return
    }
    $script:GateDockerPath = $realDocker
    try { & $Body; $script:Passes++; Write-Host "  PASS  $Name" }
    catch {
        $script:Failures++
        Write-Host "  FAIL  $Name" -ForegroundColor Red
        Write-Host "        $($_.Exception.Message)" -ForegroundColor Red
    }
    finally { $script:GateDockerPath = $realDocker }
}

function Assert-Equal {
    param($Expected, $Actual, [string]$Because)
    if ($Expected -ne $Actual) { throw "$Because`n          expected: <$Expected>`n          actual  : <$Actual>" }
}

function Assert-True {
    param([bool]$Condition, [string]$Because)
    if (-not $Condition) { throw $Because }
}

function Invoke-Captured {
    <# Runs a library call, keeps everything it printed (for case 5) and the error it threw. #>
    param([scriptblock]$Body)
    $errorText = $null
    $lines = @()
    try { $lines = @(& $Body *>&1 | ForEach-Object { [string]$_ }) }
    catch { $errorText = $_.Exception.Message }
    $text = ($lines -join "`n")
    [void]$script:Printed.Add($text)
    if ($errorText) { [void]$script:Printed.Add($errorText) }
    return [pscustomobject]@{ Output = $text; Error = $errorText }
}

function New-FakeDocker {
    <# A docker.cmd that appends its command line to calls.log and exits with $Exit. #>
    param([string]$Name, [int]$Exit = 0)
    $dir = Join-Path $sandbox $Name
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $say = if ($Exit -ne 0) { "echo Cannot connect to the Docker daemon. Is the docker daemon running? 1>&2" } else { "rem" }
    $body = "@echo off`r`n>>`"%~dp0calls.log`" echo %*`r`n$say`r`nexit /b $Exit`r`n"
    [System.IO.File]::WriteAllText((Join-Path $dir "docker.cmd"), $body, (New-Object System.Text.ASCIIEncoding))
    return [pscustomobject]@{ Path = (Join-Path $dir "docker.cmd"); Log = (Join-Path $dir "calls.log") }
}

function Get-FakeCalls {
    param($Fake)
    if (-not (Test-Path -LiteralPath $Fake.Log)) { return @() }
    return @([System.IO.File]::ReadAllLines($Fake.Log) | Where-Object { $_.Trim() })
}

function Invoke-ServerSql {
    <# One query through the real dev container, for the test's own reads and writes. #>
    param([string]$Database, [string]$Sql)
    $r = Invoke-GateProcess -FilePath $realDocker -Arguments @("exec", $script:GateDatabaseContainer, "psql", "-U", $script:GateDatabaseUser, "-d", $Database, "-v", "ON_ERROR_STOP=1", "-tAc", $Sql) -TimeoutSeconds 60
    if ($r.ExitCode -ne 0) { throw "psql on $Database exited $($r.ExitCode): $($r.StdErr.Trim())" }
    return @(($r.StdOut -split "`r?`n") | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() })
}

function Get-ServerDatabases {
    return @(Invoke-ServerSql -Database "postgres" -Sql "SELECT datname FROM pg_database ORDER BY datname")
}

function Get-PagentosShape {
    <# What case 3 holds still: the public tables of `pagentos` and the rows of three of them. #>
    $tables = @(Invoke-ServerSql -Database "pagentos" -Sql "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename") -join ","
    $counts = @(Invoke-ServerSql -Database "pagentos" -Sql "SELECT (SELECT count(*) FROM alembic_version)::text || '/' || (SELECT count(*) FROM owner)::text || '/' || (SELECT count(*) FROM devices)::text")
    return "$tables | alembic_version/owner/devices = $($counts -join '')"
}

$pagentosBefore = $null
if ($serverUp) { $pagentosBefore = Get-PagentosShape }

Write-Host "gate database tests (server: $(if ($serverUp) { 'the dev stack answers' } else { 'not reachable - server cases are skipped' }))"

# ------------------------------------------------------------------ 2: the name rule

$refusedNames = @(
    "pagentos",
    "postgres",
    "template1",
    "pagentos_prod",
    ("pagentos_gate_" + ("a" * 50)),
    "pagentos_gate_it's",
    "pagentos_gate_a;drop",
    "pagentos_gate_a b",
    "pagentos_gate_Upper"
)

Test-Case "2. the name rule: nine names, each refused by New- and by Remove- before any command is sent (18 refusals)" {
    Assert-Equal 9 @($refusedNames).Count "the card names nine names"
    Assert-Equal 64 ("pagentos_gate_" + ("a" * 50)).Length "the long name is 64 characters, one over PostgreSQL's limit"
    $fake = New-FakeDocker -Name "docker-records"
    $script:GateDockerPath = $fake.Path
    $refusals = 0
    foreach ($name in $refusedNames) {
        foreach ($verb in @("New", "Remove")) {
            $r = Invoke-Captured { if ($verb -eq "New") { New-GateDatabase -Name $name } else { Remove-GateDatabase -Name $name } }
            Assert-True ($null -ne $r.Error -and $r.Error -match "refused") "$verb-GateDatabase '$name' must be refused (got: $($r.Error))"
            $refusals++
        }
    }
    Assert-Equal 18 $refusals "eighteen refusals"
    Assert-Equal 0 @(Get-FakeCalls $fake).Count "no command reached docker for a refused name"
}

Test-Case "2b. the names the gate and the hand-run use pass the rule; the derived name is lower case, [a-z0-9_], at most 63" {
    foreach ($ok in @("pagentos_gate_20261003120000_x", "pagentos_scratch_misheard", ("pagentos_gate_" + ("a" * 49)))) {
        Assert-True (Test-GateDatabaseName -Name $ok) "'$ok' passes"
    }
    $now = [datetime]::new(2026, 10, 3, 12, 0, 0, [System.DateTimeKind]::Utc)
    $derived = Get-GateDatabaseName -RunId "Gate Run #7 / İstanbul ÇALIŞMA team-d20261003-worker-gate-faster-and-more-and-more" -Now $now
    Assert-True ($derived -cmatch '^pagentos_gate_20261003120000_[a-z0-9_]+$') "derived '$derived' is pagentos_gate_<stamp>_<run>, only [a-z0-9_]"
    Assert-True ($derived.Length -le 63) "derived '$derived' is at most 63 characters ($($derived.Length))"
    Assert-True (Test-GateDatabaseName -Name $derived) "the derived name passes the rule it is checked by"
}

# --------------------------------------------------------------- 4: the sweep (pure)

Test-Case "4a. the sweep's choice: older than the bound goes; younger, scratch, pagentos and an unstamped gate name stay" {
    $now = [datetime]::new(2026, 10, 3, 12, 0, 0, [System.DateTimeKind]::Utc)
    $names = @(
        "pagentos",
        "pagentos_gate_20261002110000_old",
        "pagentos_gate_20261002130000_young",
        "pagentos_gate_nostamp",
        "pagentos_scratch_20261001000000_hand",
        "postgres"
    )
    $chosen = @(Select-GateDatabaseToSweep -Names $names -Now $now -MaxAgeHours 24)
    Assert-Equal "pagentos_gate_20261002110000_old" ($chosen -join ",") "only the gate database 25 hours old is chosen"
    Assert-Equal 24 $script:GateDatabaseMaxAgeHours "the bound is the named constant, 24 hours"
}

# ----------------------------------------------------------------- 1: the real server

Test-Case "1. New- makes a database the migrations run on to head (alembic upgrade head exits 0, alembic current is head); Remove- drops it" -Server {
    $uv = Resolve-GateUv
    Assert-True ([bool]$uv) "uv is found"
    $name = Get-GateDatabaseName -RunId ("case1_" + [guid]::NewGuid().ToString("N").Substring(0, 6)) -Now ([datetime]::UtcNow)
    $made = Invoke-Captured { New-GateDatabase -Name $name }
    try {
        Assert-True ($null -eq $made.Error) "New-GateDatabase succeeds: $($made.Error)"
        Assert-True ((Get-ServerDatabases) -contains $name) "$name is on the server"
        $url = Get-GateDatabaseUrl -BaseUrl (Get-GateSettingsDatabaseUrl -Uv $uv -ApiRoot $apiRoot) -Name $name
        Invoke-WithGateDatabase -Variable "PAGENTOS_DATABASE_URL" -Value $url -Action {
            $script:up = Invoke-GateProcess -FilePath $uv -Arguments @("run", "alembic", "upgrade", "head") -WorkingDirectory $apiRoot -TimeoutSeconds 900
            $script:current = Invoke-GateProcess -FilePath $uv -Arguments @("run", "alembic", "current") -WorkingDirectory $apiRoot -TimeoutSeconds 300
        }
        Assert-Equal 0 $script:up.ExitCode "alembic upgrade head on $name exits 0 (stderr tail: $(($script:up.StdErr -split "`n" | Select-Object -Last 3) -join ' / '))"
        Assert-True ($script:current.StdOut -match '\(head\)') "alembic current on $name is head (got: $($script:current.StdOut.Trim()))"
        $tables = @(Invoke-ServerSql -Database $name -Sql "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
        Assert-True ([int]$tables[0] -gt 50) "the migrated database has the application's tables ($($tables[0]))"
    }
    finally {
        $gone = Invoke-Captured { Remove-GateDatabase -Name $name }
    }
    Assert-True ($null -eq $gone.Error) "Remove-GateDatabase succeeds: $($gone.Error)"
    Assert-True (-not ((Get-ServerDatabases) -contains $name)) "$name is gone from the server"
}

# ------------------------------------------------------ 3: two at once, pagentos untouched

Test-Case "3. two gate databases at once do not see each other" -Server {
    $stamp = [datetime]::UtcNow
    $a = Get-GateDatabaseName -RunId ("case3a_" + [guid]::NewGuid().ToString("N").Substring(0, 6)) -Now $stamp
    $b = Get-GateDatabaseName -RunId ("case3b_" + [guid]::NewGuid().ToString("N").Substring(0, 6)) -Now $stamp
    [void](Invoke-Captured { New-GateDatabase -Name $a })
    [void](Invoke-Captured { New-GateDatabase -Name $b })
    try {
        [void](Invoke-ServerSql -Database $a -Sql "CREATE TABLE only_in_a (id int); INSERT INTO only_in_a VALUES (1)")
        $inA = @(Invoke-ServerSql -Database $a -Sql "SELECT count(*) FROM pg_tables WHERE tablename = 'only_in_a'")
        $inB = @(Invoke-ServerSql -Database $b -Sql "SELECT count(*) FROM pg_tables WHERE tablename = 'only_in_a'")
        Assert-Equal "1" $inA[0] "the table written in $a is there"
        Assert-Equal "0" $inB[0] "the table written in $a is absent in $b"
        $vector = @(Invoke-ServerSql -Database $b -Sql "SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
        Assert-Equal "1" $vector[0] "a new gate database has the vector extension the first migration needs"
    }
    finally {
        [void](Invoke-Captured { Remove-GateDatabase -Name $a })
        [void](Invoke-Captured { Remove-GateDatabase -Name $b })
    }
}

# ------------------------------------------------------------- 4: the sweep, for real

Test-Case "4b. the sweep on the server: an old gate database goes; a young one, a scratch one and pagentos stay" -Server {
    $now = [datetime]::UtcNow
    $tag = [guid]::NewGuid().ToString("N").Substring(0, 6)
    $old = Get-GateDatabaseName -RunId "sweepold_$tag" -Now ($now.AddHours(-25))
    $young = Get-GateDatabaseName -RunId "sweepyoung_$tag" -Now ($now.AddHours(-1))
    $scratch = "pagentos_scratch_sweep_$tag"
    foreach ($n in @($old, $young, $scratch)) { [void](Invoke-Captured { New-GateDatabase -Name $n }) }
    try {
        $swept = Invoke-Captured { Invoke-GateDatabaseSweep -Now $now }
        Assert-True ($null -eq $swept.Error) "the sweep runs: $($swept.Error)"
        Assert-True ($swept.Output -match 'gate database sweep: \d+ ') "the sweep says how many it dropped: $($swept.Output)"
        $left = Get-ServerDatabases
        Assert-True (-not ($left -contains $old)) "$old (25 h by its stamp) is dropped"
        Assert-True ($left -contains $young) "$young (1 h) stays"
        Assert-True ($left -contains $scratch) "$scratch stays: the sweep is for gate databases only"
        Assert-True ($left -contains "pagentos") "pagentos stays"
    }
    finally {
        foreach ($n in @($old, $young, $scratch)) { [void](Invoke-Captured { Remove-GateDatabase -Name $n }) }
    }
}

# --------------------------------------------- 7: the gate's own block, with a fake uv

function Get-GateText { return [System.IO.File]::ReadAllText($gatePath) }

function Get-GateFunctionText {
    <# The text of one function of scripts/quality-gate.ps1, found by the parser. #>
    param([string]$Name)
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($gatePath, [ref]$tokens, [ref]$errors)
    $f = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $Name }, $true)
    if ($null -eq $f) { throw "scripts/quality-gate.ps1 has no function $Name" }
    return $f.Extent.Text
}

function Get-GateDatabaseBlockText {
    <# The try/finally of scripts/quality-gate.ps1 that holds the integration step. #>
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($gatePath, [ref]$tokens, [ref]$errors)
    $tries = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.TryStatementAst] -and $n.Body.Extent.Text -match 'Invoke-Step "API integration tests"' }, $true))
    if (@($tries).Count -ne 1) { throw "expected one try block around the integration step in scripts/quality-gate.ps1, found $(@($tries).Count)" }
    return $tries[0].Extent.Text
}

$gateVariable = ""
if ((Get-GateText) -match '\$script:GateDatabaseVariable\s*=\s*"([A-Za-z_][A-Za-z0-9_]*)"') { $gateVariable = $Matches[1] }

function New-FakeUv {
    <# A uv.cmd: `run python` answers a database URL (with a password in it); `run alembic|pytest`
       record the variable they saw and exit 0, except pytest, which exits $PytestExit. #>
    param([int]$PytestExit = 1)
    $dir = Join-Path $sandbox ("uv-" + [guid]::NewGuid().ToString("N").Substring(0, 6))
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $body = @(
        "@echo off",
        "if `"%~2`"==`"python`" (",
        "  echo postgresql+psycopg://pagentos:fake-secret-in-url@127.0.0.1:15432/pagentos",
        "  exit /b 0",
        ")",
        ">>`"%~dp0seen.log`" echo %~2 %$gateVariable%",
        "if `"%~2`"==`"pytest`" exit /b $PytestExit",
        "exit /b 0"
    ) -join "`r`n"
    [System.IO.File]::WriteAllText((Join-Path $dir "uv.cmd"), $body + "`r`n", (New-Object System.Text.ASCIIEncoding))
    return [pscustomobject]@{ Path = (Join-Path $dir "uv.cmd"); Seen = (Join-Path $dir "seen.log"); Dir = $dir }
}

function Invoke-GateBlock {
    <# Runs the gate's own database block with the gate's own Invoke-Step / Assert-ExitCode,
       a fake uv and the given docker. Returns what it printed and what the children saw. #>
    param([string]$Docker, [int]$PytestExit = 1)
    $fakeUv = New-FakeUv -PytestExit $PytestExit
    $script:GateDockerPath = $Docker
    $script:results = New-Object System.Collections.ArrayList
    $script:failed = $false
    $script:GateGroup = $null
    $script:GateRecording = $null
    $script:GateDatabaseVariable = $gateVariable
    $script:GateDatabase = Get-GateDatabaseName -RunId ("block_" + [guid]::NewGuid().ToString("N").Substring(0, 6)) -Now ([datetime]::UtcNow)
    $script:GateDatabaseUrl = $null
    $script:GateDatabaseCreated = $false
    $uv = $fakeUv.Path
    $apiRoot = $fakeUv.Dir
    . ([scriptblock]::Create((Get-GateFunctionText "Invoke-Step")))
    . ([scriptblock]::Create((Get-GateFunctionText "Assert-ExitCode")))
    # The block's steps ask the test queue for their kinds; here they ask nothing (-NoTestSlots).
    . ([scriptblock]::Create((Get-GateFunctionText "Enter-GateTestSlot")))
    . ([scriptblock]::Create((Get-GateFunctionText "Exit-GateTestSlot")))
    $NoTestSlots = $true
    $block = [scriptblock]::Create((Get-GateDatabaseBlockText))
    $printed = Invoke-Captured { . $block }
    $seen = @()
    if (Test-Path -LiteralPath $fakeUv.Seen) { $seen = @([System.IO.File]::ReadAllLines($fakeUv.Seen)) }
    return [pscustomobject]@{ Printed = $printed; Seen = $seen; Name = $script:GateDatabase; Results = @($script:results.ToArray()) }
}

Test-Case "7a. the gate's block (fake docker): the integration run fails, the database is dropped all the same, the children saw it, the variable is back" {
    Assert-True ([bool]$gateVariable) "scripts/quality-gate.ps1 names the variable in `$script:GateDatabaseVariable"
    $fake = New-FakeDocker -Name ("docker-block-" + [guid]::NewGuid().ToString("N").Substring(0, 6))
    $before = [Environment]::GetEnvironmentVariable($gateVariable, "Process")
    $run = Invoke-GateBlock -Docker $fake.Path -PytestExit 1
    $after = [Environment]::GetEnvironmentVariable($gateVariable, "Process")
    # Compared, never printed: the value is a URL with a password in it.
    Assert-True ("$before" -ceq "$after") "a later step sees the variable's original value (it still holds the block's URL; value not shown)"
    $calls = @(Get-FakeCalls $fake)
    $create = @($calls | Where-Object { $_ -match "CREATE DATABASE $($run.Name)\b" })
    $drop = @($calls | Where-Object { $_ -match "DROP DATABASE IF EXISTS $($run.Name)\b" })
    Assert-Equal 1 @($create).Count "the block creates its database once (calls: $($calls -join ' || '))"
    Assert-Equal 1 @($drop).Count "the block drops its database although the integration run failed (calls: $($calls -join ' || '))"
    Assert-True (-not (@($calls) | Where-Object { $_ -match '(-d|--dbname)\s+"?pagentos"?(\s|$)' })) "no command connects to the dev database pagentos"
    $alembic = @($run.Seen | Where-Object { $_ -match '^alembic ' })
    $pytest = @($run.Seen | Where-Object { $_ -match '^pytest ' })
    Assert-True (@($alembic).Count -eq 1 -and $alembic[0] -match "/$($run.Name)$") "alembic ran against the gate's database (saw: $($run.Seen -join ' || '))"
    Assert-True (@($pytest).Count -eq 1 -and $pytest[0] -match "/$($run.Name)$") "pytest ran against the gate's database"
    $failedSteps = @($run.Results | Where-Object { $_.Result -eq "FAIL" } | ForEach-Object { $_.Step })
    Assert-Equal "API integration tests" ($failedSteps -join ",") "only the integration step failed"
    Assert-True ($run.Printed.Output -match [regex]::Escape($run.Name)) "the log names the gate's database"
}

Test-Case "7b. the gate's block on the server: the database is gone afterwards and pagentos was never its target" -Server {
    $run = Invoke-GateBlock -Docker $realDocker -PytestExit 1
    Assert-True (-not ((Get-ServerDatabases) -contains $run.Name)) "$($run.Name) is gone after the block"
    $pytest = @($run.Seen | Where-Object { $_ -match '^pytest ' })
    Assert-True (@($pytest).Count -eq 1 -and $pytest[0] -match "/$($run.Name)$") "the integration child was pointed at $($run.Name)"
}

# ------------------------------------------------------------------ 5: no password

Test-Case "5. no password: not in the fake docker's command lines, not in anything the functions printed" {
    $fake = New-FakeDocker -Name ("docker-scan-" + [guid]::NewGuid().ToString("N").Substring(0, 6))
    $script:GateDockerPath = $fake.Path
    $name = Get-GateDatabaseName -RunId "scan" -Now ([datetime]::UtcNow)
    [void](Invoke-Captured { New-GateDatabase -Name $name })
    [void](Invoke-Captured { Remove-GateDatabase -Name $name })
    [void](Invoke-Captured { Invoke-GateDatabaseSweep -Now ([datetime]::UtcNow) })
    $calls = @(Get-FakeCalls $fake)
    Assert-True (@($calls).Count -ge 3) "the fake docker saw the calls ($(@($calls).Count))"
    Assert-True ([bool]$devPassword) "the dev password is read from the compose file"
    $everything = @($calls) + @($script:Printed) + @(Get-ChildItem -LiteralPath $sandbox -Recurse -Filter "calls.log" | ForEach-Object { [System.IO.File]::ReadAllText($_.FullName) })
    foreach ($text in $everything) {
        Assert-True (-not ([string]$text).Contains($devPassword)) "the dev password appears in: $text"
        Assert-True (-not ([string]$text).Contains("fake-secret-in-url")) "the URL's password appears in: $text"
        Assert-True (-not ([string]$text -match 'PGPASSWORD=\S')) "PGPASSWORD= with a value appears in: $text"
    }
}

# ------------------------------------------------------------- 6: without Docker

Test-Case "6. without Docker the server cases are skipped with a printed count and the suite exits 0" {
    if ($isChildRun) { Write-Host "        (the child run does not start another child)"; return }
    $down = New-FakeDocker -Name "docker-down" -Exit 1
    $script:GateDockerPath = $down.Path
    Assert-True (-not (Test-GateDatabaseServer)) "a docker that says 'not running' is not a server"
    $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $env:PAGENTOS_GATE_DOCKER = $down.Path
    $env:PAGENTOS_GATE_DB_TESTS_CHILD = "1"
    try {
        $child = Invoke-GateProcess -FilePath $powershell -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath) -TimeoutSeconds 600
    }
    finally {
        Remove-Item Env:\PAGENTOS_GATE_DOCKER -ErrorAction SilentlyContinue
        Remove-Item Env:\PAGENTOS_GATE_DB_TESTS_CHILD -ErrorAction SilentlyContinue
    }
    Assert-Equal 0 $child.ExitCode "the suite without Docker exits 0 (tail: $(($child.StdOut -split "`n" | Select-Object -Last 4) -join ' / '))"
    Assert-True ($child.StdOut -match 'skipped (\d+) server case') "the child prints how many server cases it skipped"
    Assert-True ([int]$Matches[1] -ge 4) "it skipped every server case: 1, 3, 4b and 7b ($($Matches[1]))"
    Assert-True ($child.StdOut -notmatch '\bFAIL\b') "the pure cases still ran and passed"
}

# --------------------------------------------------- 3 (end): pagentos is unchanged

if ($serverUp -and -not $Filter) {
    Test-Case "3b. pagentos has the same tables and the same rows of alembic_version, owner and devices as before the suite" -Server {
        $after = Get-PagentosShape
        Assert-Equal $pagentosBefore $after "pagentos before and after the whole suite"
    }
}

Remove-Item -LiteralPath $sandbox -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
if ($script:Skipped -gt 0) { Write-Host "skipped $($script:Skipped) server case(s): the dev stack's PostgreSQL did not answer" }
Write-Host "gate database: $script:Passes passed, $script:Failures failed, $script:Skipped skipped"
if ($script:Failures -gt 0) { exit 1 }
exit 0
