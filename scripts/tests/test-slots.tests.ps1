<#
.SYNOPSIS
    The 'database' slot leaves the dev database at the schema it found
    (scripts/lib/TestSlots.ps1, scripts/team/test-slot.ps1 run).

.DESCRIPTION
    2026-10-04 17:33: the release gate on 29aa2232 failed at 'Alembic upgrade head' with
    "Can't locate revision identified by '0066_watches'". A worker's Postgres tests, run through
    a 'database' slot on the SHARED dev stack, had migrated the dev database to its branch's
    new head and left it there; no other tree knows that revision.

    Every case runs against a SCRATCH database on the dev stack's Postgres (container
    pagentos-postgres, 127.0.0.1:15432) - never the 'pagentos' database - and its own queue
    store under %TEMP%. The scratch database starts at 0064 and the fake run migrates it to
    this tree's head (0065): the "newer head" a branch brings.

      1  a run that migrates and succeeds leaves the database at the recorded revision;
      2  a run that migrates and FAILS is restored too, and its exit code still passes through;
      3  a run that changes nothing triggers no downgrade;
      4  a downgrade that fails holds the database: the next database ask and run are refused
         with a DURDU line naming what happened, a heavy ask is not, `unblock` lifts the hold;
      5  a run without the database kind records nothing.

    Run: powershell -NoProfile -File scripts\tests\test-slots.tests.ps1 [-Filter <regex>]
    Needs the dev stack (scripts\dev-up.ps1); about two minutes.
#>

[CmdletBinding()]
param([string]$Filter = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$slotScript = Join-Path $repoRoot "scripts\team\test-slot.ps1"
$apiDir = Join-Path $repoRoot "services\api"
$ps5 = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")

$script:Failures = 0
$script:Passes = 0
$script:TempRoot = Join-Path $env:TEMP ("pagentos-slot-schema-tests-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
[void](New-Item -ItemType Directory -Path $script:TempRoot -Force)

$script:Base = "0064_memory_vocabulary_class"
$script:Head = "0065_misheard_utterances"
$script:Container = "pagentos-postgres"
$script:Db = "pagentos_slotguard_" + [guid]::NewGuid().ToString("N").Substring(0, 8)

function Resolve-Exe {
    param([string]$Name, [string[]]$Fallbacks)
    $c = @(Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue) | Select-Object -First 1
    if ($null -ne $c) { return $c.Source }
    foreach ($f in $Fallbacks) {
        $x = [Environment]::ExpandEnvironmentVariables($f)
        if (Test-Path -LiteralPath $x) { return $x }
    }
    throw "$Name not found"
}
$docker = Resolve-Exe "docker" @("C:\Program Files\Docker\Docker\resources\bin\docker.exe")
$uv = Resolve-Exe "uv" @("%USERPROFILE%\.local\bin\uv.exe")

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    if ($Filter -and $Name -notmatch $Filter) { return }
    try {
        & $Body
        $script:Passes++; Write-Host "  PASS  $Name"
    }
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

function Invoke-Psql {
    param([string]$Database, [string]$Sql)
    $r = Invoke-NativeProcess -FilePath $docker -Arguments @("exec", $script:Container, "psql", "-U", "pagentos", "-d", $Database, "-v", "ON_ERROR_STOP=1", "-tAc", $Sql) -TimeoutSeconds 60
    if (-not $r.Success) { throw "psql failed ($($r.ExitCode)): $($r.StdErr)" }
    return ([string]$r.StdOut).Trim()
}

function Get-ScratchRevision { return (Invoke-Psql -Database $script:Db -Sql "select version_num from alembic_version") }

function Set-ScratchAt {
    <# The scratch database at $Revision (from wherever it is now). #>
    param([string]$Revision)
    $now = Get-ScratchRevision
    if ($now -ne $script:Base -and $now -ne $script:Head) {
        # Only case 4 writes a bogus revision, over a schema at $Base; a case that failed half-way left it.
        [void](Invoke-Psql -Database $script:Db -Sql "update alembic_version set version_num='$($script:Base)'")
        $now = $script:Base
    }
    if ($now -eq $Revision) { return }
    $verb = if ($now -eq $script:Head) { "downgrade" } else { "upgrade" }
    $r = Invoke-NativeProcess -FilePath $uv -Arguments @("run", "alembic", $verb, $Revision) -WorkingDirectory $apiDir -TimeoutSeconds 900
    if (-not $r.Success) { throw "alembic $verb $Revision failed: $($r.StdErr)" }
}

function New-Store { return (Join-Path $script:TempRoot ("s-" + [guid]::NewGuid().ToString("N").Substring(0, 10))) }

function Invoke-Slot {
    <# test-slot.ps1 under PS 5.1, run from this tree's services\api (where a worker runs its suite). #>
    param([string[]]$Arguments)
    return (Invoke-NativeProcess -FilePath $ps5 -Arguments (@("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $slotScript) + $Arguments) -WorkingDirectory $apiDir -SuccessExitCodes @(0, 1, 2, 3, 4, 5, 6, 7) -TimeoutSeconds 900)
}

function Get-Ticket {
    param([string]$Store, [string]$Kind = "database")
    $a = Invoke-Slot @("ask", "-Kind", $Kind, "-Task", "slot-schema-test", "-Role", "worker", "-What", "schema guard test", "-Store", $Store)
    Assert-Equal 0 $a.ExitCode "ask is ONAY on an empty store ($($a.StdOut) $($a.StdErr))"
    $m = [regex]::Match($a.StdOut, 'ONAY (ts-[0-9a-f]+)')
    Assert-True $m.Success "an ONAY line: $($a.StdOut)"
    return $m.Groups[1].Value
}

function Invoke-SlotRun {
    <# A fake run: a PS 5.1 command line given as one script text. #>
    param([string]$Store, [string]$Ticket, [string]$Script)
    return (Invoke-Slot @("run", "-Ticket", $Ticket, "-Store", $Store, "--", $ps5, "-NoProfile", "-Command", $Script))
}

$migrateHead = "& '$uv' run alembic upgrade head; if (`$LASTEXITCODE -ne 0) { exit 9 }"

# ------------------------------------------------------------------------------- the scratch DB
$env:PAGENTOS_DATABASE_URL = "postgresql+psycopg://pagentos:pagentos-dev@127.0.0.1:15432/$($script:Db)"
try {
    [void](Invoke-Psql -Database "postgres" -Sql "CREATE DATABASE $($script:Db)")
    $up = Invoke-NativeProcess -FilePath $uv -Arguments @("run", "alembic", "upgrade", $script:Base) -WorkingDirectory $apiDir -TimeoutSeconds 900
    if (-not $up.Success) { throw "alembic upgrade $($script:Base) on the scratch database failed: $($up.StdErr)" }
}
catch {
    Write-Host "  FAIL  setup: the scratch database on the dev stack ($($_.Exception.Message)) - is scripts\dev-up.ps1 up?" -ForegroundColor Red
    exit 1
}

Test-Case "1 a run that migrates to a newer head and succeeds leaves the database at the recorded revision" {
    Set-ScratchAt $script:Base
    $store = New-Store
    $t = Get-Ticket -Store $store
    $r = Invoke-SlotRun -Store $store -Ticket $t -Script ($migrateHead + "; exit 0")
    Assert-Equal 0 $r.ExitCode "the command's exit code ($($r.StdErr))"
    Assert-Equal $script:Base (Get-ScratchRevision) "the database is back where the run found it"
    Assert-True ($r.StdErr -match "SEMA_KORUMA kayit $($script:Base)") "the record is said: $($r.StdErr)"
    Assert-True ($r.StdErr -match "SEMA_KORUMA geri_alindi $($script:Head) -> $($script:Base)") "the restore is said: $($r.StdErr)"
}

Test-Case "2 a run that migrates and fails is restored too; its exit code passes through" {
    Set-ScratchAt $script:Base
    $store = New-Store
    $t = Get-Ticket -Store $store
    $r = Invoke-SlotRun -Store $store -Ticket $t -Script ($migrateHead + "; exit 7")
    Assert-Equal 7 $r.ExitCode "the command's exit code ($($r.StdErr))"
    Assert-Equal $script:Base (Get-ScratchRevision) "the failed run's migration is undone"
}

Test-Case "3 a run that changes nothing downgrades nothing" {
    Set-ScratchAt $script:Base
    $store = New-Store
    $t = Get-Ticket -Store $store
    $r = Invoke-SlotRun -Store $store -Ticket $t -Script "exit 0"
    Assert-Equal 0 $r.ExitCode "exit code ($($r.StdErr))"
    Assert-True ($r.StdErr -match "SEMA_KORUMA ayni $($script:Base)") "unchanged is said: $($r.StdErr)"
    Assert-True ($r.StdErr -notmatch "geri_alindi") "no downgrade"
    Assert-Equal $script:Base (Get-ScratchRevision) "unchanged"
}

Test-Case "4 a failed downgrade holds the database: the next database ticket is refused until unblock" {
    Set-ScratchAt $script:Base
    $store = New-Store
    $t = Get-Ticket -Store $store
    # The run leaves a revision no tree knows: the downgrade cannot locate it (the 0066 case).
    $bogus = "& '$docker' exec $($script:Container) psql -U pagentos -d $($script:Db) -c `"update alembic_version set version_num='zz_unknown_head'`"; exit 0"
    $r = Invoke-SlotRun -Store $store -Ticket $t -Script $bogus
    Assert-True ($r.StdErr -match "SEMA_KORUMA BASARISIZ") "the failed downgrade is loud: $($r.StdErr)"
    Assert-True ($r.ExitCode -ne 0) "a run whose restore failed does not report success"
    $a = Invoke-Slot @("ask", "-Kind", "database", "-Task", "next-task", "-Role", "worker", "-What", "next", "-Store", $store)
    Assert-Equal 6 $a.ExitCode "the next database ask is refused ($($a.StdOut))"
    Assert-True ($a.StdOut -match "^DURDU") "a DURDU line: $($a.StdOut)"
    Assert-True ($a.StdOut -match "zz_unknown_head" -and $a.StdOut -match $script:Base) "it names the revision found and the one expected: $($a.StdOut)"
    Assert-True ($a.StdOut -match "unblock") "it says how the hold is lifted: $($a.StdOut)"
    $h = Invoke-Slot @("ask", "-Kind", "heavy", "-Task", "other", "-Role", "worker", "-What", "unit", "-Store", $store)
    Assert-Equal 0 $h.ExitCode "a heavy ask is not held ($($h.StdOut))"
    $st = Invoke-Slot @("status", "-Store", $store)
    Assert-True ($st.StdOut -match "DURDU") "status shows the hold: $($st.StdOut)"
    # The Danışman repairs the database, then lifts the hold.
    [void](Invoke-Psql -Database $script:Db -Sql "update alembic_version set version_num='$($script:Base)'")
    $u = Invoke-Slot @("unblock", "-Store", $store)
    Assert-Equal 0 $u.ExitCode "unblock ($($u.StdErr))"
    $t2 = Get-Ticket -Store $store
    $r2 = Invoke-SlotRun -Store $store -Ticket $t2 -Script "exit 0"
    Assert-Equal 0 $r2.ExitCode "after unblock a database run goes ($($r2.StdErr))"
}

Test-Case "4b a database ticket granted before the hold is refused at run" {
    Set-ScratchAt $script:Base
    $store = New-Store
    $t = Get-Ticket -Store $store
    $bogus = "& '$docker' exec $($script:Container) psql -U pagentos -d $($script:Db) -c `"update alembic_version set version_num='zz_unknown_head'`"; exit 0"
    [void](Invoke-SlotRun -Store $store -Ticket $t -Script $bogus)
    [void](Invoke-Psql -Database $script:Db -Sql "update alembic_version set version_num='$($script:Base)'")
    # The hold is on; a ticket obtained by hand from the library (as the gate would) must not run.
    . (Join-Path $repoRoot "scripts\lib\TeamTestSlots.ps1")
    $g = Invoke-TestSlotAsk -Store $store -Kind "database" -Task "late" -Role "worker" -What "late"
    Assert-Equal "ONAY" $g.Decision "the library itself grants (the hold lives in test-slot.ps1)"
    $marker = Join-Path $script:TempRoot ("ran-" + [guid]::NewGuid().ToString("N").Substring(0, 6))
    $r = Invoke-SlotRun -Store $store -Ticket $g.Ticket -Script "Set-Content -LiteralPath '$marker' -Value x; exit 0"
    Assert-Equal 6 $r.ExitCode "the run is refused ($($r.StdOut) $($r.StdErr))"
    Assert-True (-not (Test-Path -LiteralPath $marker)) "the command never started"
    Assert-True ($r.StdErr -match "DURDU") "a DURDU line: $($r.StdErr)"
}

Test-Case "5 a run without the database kind records nothing" {
    Set-ScratchAt $script:Base
    $store = New-Store
    $t = Get-Ticket -Store $store -Kind "heavy"
    $r = Invoke-SlotRun -Store $store -Ticket $t -Script ($migrateHead + "; exit 0")
    Assert-Equal 0 $r.ExitCode "exit code"
    Assert-True ($r.StdErr -notmatch "SEMA_KORUMA") "no guard on a heavy-only run: $($r.StdErr)"
    Assert-Equal $script:Head (Get-ScratchRevision) "left as the heavy run made it (it holds no database slot)"
}

try { [void](Invoke-Psql -Database "postgres" -Sql "DROP DATABASE IF EXISTS $($script:Db) WITH (FORCE)") }
catch { Write-Host "  (scratch database left: $($script:Db))" }
try { Remove-Item -LiteralPath $script:TempRoot -Recurse -Force -ErrorAction Stop } catch { Write-Host "  (temp folder left: $script:TempRoot)" }

Write-Host ""
Write-Host "test-slots tests:$script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
