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
    store under %TEMP%. The scratch database starts at the parent of this tree's alembic head
    (Base) and the fake run migrates it to the head (Head): the "newer head" a branch brings.
    Both are read from the tree (`alembic heads`, `alembic show <head>`), never written here.

      1  a run that migrates and succeeds leaves the database at the recorded revision;
      2  a run that migrates and FAILS is restored too, and its exit code still passes through;
      3  a run that changes nothing triggers no downgrade;
      4  a downgrade that fails holds the database: the next database ask and run are refused
         with a DURDU line naming what happened, a heavy ask is not, `unblock` lifts the hold;
      5  a run without the database kind records nothing;
      6  a wrapper killed (with its command) after migrating: no `finally` ran, but its guard
         record stays, and the next database run restores the database from the dead run's
         tree before its own command starts;
      7  a wrapper killed while its command still runs: the next database ask is granted with a
         warning, its run is refused (DURDU, the command's pid named, nothing touched) until
         that command is gone, then the next run restores;
      8  a dead run's guard whose restore fails holds the database: the next run never starts;
      9  the guard record is the only source of the database: a dead run's record for one
         scratch database, found by a run whose settings point at ANOTHER one, restores the
         recorded database (no hold) and leaves the run's own database alone (a); a record whose
         database is gone from the server is dropped, no hold (b); the other way round - the
         record names the database the settings point at by default, the run's own settings
         a scratch one - restores the recorded one (c). The 'pagentos' database itself is never
         written by a test: a second scratch database stands in for it.

    Run: powershell -NoProfile -File scripts\tests\test-slots.tests.ps1 [-Filter <regex>]
    Needs the dev stack (scripts\dev-up.ps1); about four minutes.
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
# A test-slot child must not post to the team's board from a test.
foreach ($name in @("PAGENTOS_TEAM_URL", "PAGENTOS_TEAM_TOKEN_FILE", "PAGENTOS_TEAM_SEAT")) { Remove-Item -Path ("Env:" + $name) -ErrorAction SilentlyContinue }

$script:Failures = 0
$script:Passes = 0
$script:TempRoot = Join-Path $env:TEMP ("pagentos-slot-schema-tests-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
[void](New-Item -ItemType Directory -Path $script:TempRoot -Force)

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

# Head and Base come from this tree's own migrations (`alembic heads`, the head's Parent), so the
# next migration that lands moves both and the cases still test "a branch brings one newer head".
$script:Head = ""
$script:Base = ""
try {
    $hr = Invoke-NativeProcess -FilePath $uv -Arguments @("run", "alembic", "heads") -WorkingDirectory $apiDir -TimeoutSeconds 900
    $heads = @([regex]::Matches([string]$hr.StdOut, '(?m)^(\S+) \(head\)') | ForEach-Object { $_.Groups[1].Value })
    if (@($heads).Count -ne 1) { throw "this tree has $(@($heads).Count) alembic heads: $($hr.StdOut) $($hr.StdErr)" }
    $script:Head = $heads[0]
    $sr = Invoke-NativeProcess -FilePath $uv -Arguments @("run", "alembic", "show", $script:Head) -WorkingDirectory $apiDir -TimeoutSeconds 300
    $parent = [regex]::Match([string]$sr.StdOut, '(?m)^Parent: (\S+)\s*$')
    if (-not $parent.Success -or $parent.Groups[1].Value -match '^<' -or $parent.Groups[1].Value -like "*,*") { throw "the head $($script:Head) has no single parent: $($sr.StdOut)" }
    $script:Base = $parent.Groups[1].Value
}
catch {
    Write-Host "  FAIL  setup: this tree's alembic head and its parent ($($_.Exception.Message))" -ForegroundColor Red
    exit 1
}
Write-Host "  (Base $($script:Base) -> Head $($script:Head), from this tree's migrations)"

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

function Start-SlotRunDetached {
    <# test-slot.ps1 run started and NOT waited for (so it can be killed); output to files. #>
    param([string]$Store, [string]$Ticket, [string]$Script)
    $argLine = ConvertTo-NativeArgumentLine -Arguments (@("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $slotScript, "run", "-Ticket", $Ticket, "-Store", $Store, "--", $ps5, "-NoProfile", "-Command", $Script))
    $log = Join-Path $script:TempRoot ("detached-" + [guid]::NewGuid().ToString("N").Substring(0, 6))
    return (Start-Process -FilePath $ps5 -ArgumentList $argLine -WorkingDirectory $apiDir -PassThru -WindowStyle Hidden -RedirectStandardOutput "$log.out" -RedirectStandardError "$log.err")
}

function Wait-ScratchAt {
    param([string]$Revision, [int]$Seconds = 300)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if ((Get-ScratchRevision) -eq $Revision) { return }
        Start-Sleep -Seconds 2
    }
    throw "the scratch database did not reach $Revision in $Seconds s (at $(Get-ScratchRevision))"
}

function Stop-Tree {
    param([int]$ProcessId)
    $tk = Join-Path $env:SystemRoot "System32\taskkill.exe"
    [void](Invoke-NativeProcess -FilePath $tk -Arguments @("/PID", [string]$ProcessId, "/T", "/F") -SuccessExitCodes @(0, 1, 128, 255) -TimeoutSeconds 60)
}

function Get-GuardRecord {
    param([string]$Store)
    $p = Join-Path $Store "database-guard.json"
    Assert-True (Test-Path -LiteralPath $p) "a database run writes its guard record before its command: $p"
    return ([System.IO.File]::ReadAllText($p) | ConvertFrom-Json)
}

function Wait-GuardChild {
    <# The guard names the command's pid once it started. #>
    param([string]$Store, [int]$Seconds = 120)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        $p = Join-Path $Store "database-guard.json"
        if (Test-Path -LiteralPath $p) {
            try { $g = [System.IO.File]::ReadAllText($p) | ConvertFrom-Json; if ([int]$g.child_pid -gt 0) { return $g } } catch { }
        }
        Start-Sleep -Milliseconds 500
    }
    throw "no guard record with the command's pid in $Seconds s ($Store)"
}

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

Test-Case "6 a wrapper killed with its command after migrating: the next database run restores first" {
    Set-ScratchAt $script:Base
    $store = New-Store
    $t = Get-Ticket -Store $store
    $w = Start-SlotRunDetached -Store $store -Ticket $t -Script ($migrateHead + "; Start-Sleep 600")
    try { Wait-ScratchAt $script:Head } finally { Stop-Tree -ProcessId $w.Id }
    Start-Sleep -Seconds 1
    Assert-Equal $script:Head (Get-ScratchRevision) "the killed run left the newer head (no finally ran)"
    $g = Get-GuardRecord -Store $store
    Assert-Equal $script:Base ([string]$g.expected) "the guard record names the revision to restore"
    $t2 = Get-Ticket -Store $store
    $marker = Join-Path $script:TempRoot ("rev-" + [guid]::NewGuid().ToString("N").Substring(0, 6))
    # The next run's command sees the database already restored.
    $r = Invoke-SlotRun -Store $store -Ticket $t2 -Script "& '$docker' exec $($script:Container) psql -U pagentos -d $($script:Db) -tAc 'select version_num from alembic_version' | Set-Content -LiteralPath '$marker'; exit 0"
    Assert-Equal 0 $r.ExitCode "the next run goes ($($r.StdErr))"
    Assert-True ($r.StdErr -match "SEMA_KORUMA olu_kosu geri_alindi $($script:Head) -> $($script:Base)") "the dead run's restore is said: $($r.StdErr)"
    Assert-Equal $script:Base (([string](Get-Content -LiteralPath $marker -Raw)).Trim()) "the next command started on the restored schema"
    Assert-Equal $script:Base (Get-ScratchRevision) "and left it there"
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $store "database-guard.json"))) "no guard record is left after a finished run"
}

Test-Case "7 a wrapper killed while its command still runs: the next database ticket waits for that command" {
    Set-ScratchAt $script:Base
    $store = New-Store
    $t = Get-Ticket -Store $store
    $w = Start-SlotRunDetached -Store $store -Ticket $t -Script ($migrateHead + "; Start-Sleep 600")
    $g = $null
    try {
        Wait-ScratchAt $script:Head
        $g = Wait-GuardChild -Store $store
    }
    finally { Stop-Process -Id $w.Id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
    try {
        # The slot belonged to the killed wrapper and is free (team-test-slots case 6); the ask
        # warns, the run refuses before its command starts.
        $a = Invoke-Slot @("ask", "-Kind", "database", "-Task", "next-task", "-Role", "worker", "-What", "next", "-Store", $store)
        Assert-Equal 0 $a.ExitCode "the killed wrapper's slot is free at the next ask ($($a.StdOut) $($a.StdErr))"
        Assert-True ($a.StdErr -match [string]$g.child_pid) "the ask warns, naming the command's pid: $($a.StdErr)"
        $tn = [regex]::Match($a.StdOut, 'ts-[0-9a-f]+').Value
        $marker = Join-Path $script:TempRoot ("ran-" + [guid]::NewGuid().ToString("N").Substring(0, 6))
        $rn = Invoke-SlotRun -Store $store -Ticket $tn -Script "Set-Content -LiteralPath '$marker' -Value x; exit 0"
        Assert-Equal 6 $rn.ExitCode "the run is refused while the dead run's command runs ($($rn.StdErr))"
        Assert-True ($rn.StdErr -match "DURDU" -and $rn.StdErr -match [string]$g.child_pid) "a DURDU line naming the command's pid: $($rn.StdErr)"
        Assert-True (-not (Test-Path -LiteralPath $marker)) "the command never started"
        $h = Invoke-Slot @("ask", "-Kind", "heavy", "-Task", "other", "-Role", "worker", "-What", "unit", "-Store", $store)
        Assert-Equal 0 $h.ExitCode "a heavy ask is not held ($($h.StdOut))"
        [void](Invoke-Slot @("release", "-Ticket", ([regex]::Match($h.StdOut, 'ts-[0-9a-f]+').Value), "-Store", $store))
        Assert-Equal $script:Head (Get-ScratchRevision) "nothing is touched under a running command"
    }
    finally { Stop-Tree -ProcessId ([int]$g.child_pid) }
    Start-Sleep -Seconds 1
    $t2 = Get-Ticket -Store $store
    $r = Invoke-SlotRun -Store $store -Ticket $t2 -Script "exit 0"
    Assert-Equal 0 $r.ExitCode "once the command is gone the next run goes ($($r.StdErr))"
    Assert-True ($r.StdErr -match "SEMA_KORUMA olu_kosu geri_alindi") "and restores first: $($r.StdErr)"
    Assert-Equal $script:Base (Get-ScratchRevision) "restored"
}

Test-Case "8 a dead run's guard whose restore fails holds the database; the next command never starts" {
    Set-ScratchAt $script:Base
    $store = New-Store
    [void](New-Item -ItemType Directory -Path $store -Force)
    [void](Invoke-Psql -Database $script:Db -Sql "update alembic_version set version_num='zz_unknown_head'")
    # A guard left by a wrapper that died (this pid with another start time is not it).
    $guard = [ordered]@{ at = "2026-10-04T17:00:00Z"; task = "dead-task"; role = "worker"; ticket = "ts-000000000000"; database = $script:Db
        tree = $apiDir; expected = $script:Base; holder_pid = $PID; holder_start = "1"; child_pid = 0; child_start = "" }
    [System.IO.File]::WriteAllText((Join-Path $store "database-guard.json"), ($guard | ConvertTo-Json))
    $t = Get-Ticket -Store $store
    $marker = Join-Path $script:TempRoot ("ran-" + [guid]::NewGuid().ToString("N").Substring(0, 6))
    $r = Invoke-SlotRun -Store $store -Ticket $t -Script "Set-Content -LiteralPath '$marker' -Value x; exit 0"
    Assert-Equal 6 $r.ExitCode "the run is refused ($($r.StdErr))"
    Assert-True (-not (Test-Path -LiteralPath $marker)) "the command never started"
    Assert-True ($r.StdErr -match "DURDU" -and $r.StdErr -match "zz_unknown_head" -and $r.StdErr -match "dead-task") "a DURDU line naming the found revision and the dead run: $($r.StdErr)"
    $a = Invoke-Slot @("ask", "-Kind", "database", "-Task", "next-task", "-Role", "worker", "-What", "next", "-Store", $store)
    Assert-Equal 6 $a.ExitCode "the hold stands for the next ask ($($a.StdOut))"
    [void](Invoke-Psql -Database $script:Db -Sql "update alembic_version set version_num='$($script:Base)'")
    $u = Invoke-Slot @("unblock", "-Store", $store)
    Assert-Equal 0 $u.ExitCode "unblock ($($u.StdErr))"
    $t2 = Get-Ticket -Store $store
    $r2 = Invoke-SlotRun -Store $store -Ticket $t2 -Script "exit 0"
    Assert-Equal 0 $r2.ExitCode "after unblock a database run goes ($($r2.StdErr))"
}

# ------------------------------------------------------------- 9: the record names the database
# A second scratch database, copied from the first at Base: the "other" database a run's settings
# point at (a) or the record names (c).
$script:Db2 = $script:Db + "_b"
$script:Url = $env:PAGENTOS_DATABASE_URL
$script:Url2 = "postgresql+psycopg://pagentos:pagentos-dev@127.0.0.1:15432/$($script:Db2)"

function Get-RevisionOf {
    param([string]$Database)
    return (Invoke-Psql -Database $Database -Sql "select version_num from alembic_version")
}

function Use-DatabaseUrl {
    <# Runs $Body with the slot children's PAGENTOS_DATABASE_URL set to $Url ('' = unset). #>
    param([string]$Url, [scriptblock]$Body)
    $saved = $env:PAGENTOS_DATABASE_URL
    try {
        if ($Url) { $env:PAGENTOS_DATABASE_URL = $Url } else { Remove-Item Env:PAGENTOS_DATABASE_URL -ErrorAction SilentlyContinue }
        return (& $Body)
    }
    finally { $env:PAGENTOS_DATABASE_URL = $saved }
}

function Write-DeadGuard {
    <# A guard left by a wrapper that died (this pid with another start time is not it). #>
    param([string]$Store, [string]$Database, [string]$Task = "dead-task")
    [void](New-Item -ItemType Directory -Path $Store -Force)
    $guard = [ordered]@{ at = "2026-10-07T06:00:00Z"; task = $Task; role = "worker"; ticket = "ts-000000000000"; database = $Database
        tree = $apiDir; expected = $script:Base; holder_pid = $PID; holder_start = "1"; child_pid = 0; child_start = "" }
    [System.IO.File]::WriteAllText((Join-Path $Store "database-guard.json"), ($guard | ConvertTo-Json))
}

function Invoke-AfterDeadGuard {
    <# One database run (ticket and run) with the given settings URL; a marker file shows the command ran. #>
    param([string]$Store, [string]$Url)
    $marker = Join-Path $script:TempRoot ("ran-" + [guid]::NewGuid().ToString("N").Substring(0, 6))
    $r = Use-DatabaseUrl -Url $Url -Body {
        $t = Get-Ticket -Store $Store
        Invoke-SlotRun -Store $Store -Ticket $t -Script "Set-Content -LiteralPath '$marker' -Value x; exit 0"
    }
    return [pscustomobject]@{ Run = $r; Ran = (Test-Path -LiteralPath $marker) }
}

function Assert-NoHold {
    param([string]$Store, $Run)
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $Store "database-hold.json"))) "no hold is written: $($Run.StdErr)"
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $Store "database-guard.json"))) "the dead run's record is settled: $($Run.StdErr)"
    Assert-True ($Run.StdErr -notmatch "DURDU|BASARISIZ") "no DURDU, no failure: $($Run.StdErr)"
}

$haveDb2 = $false
if ($Filter -eq "" -or @(@("9a ", "9b ", "9c ", "9d ") | Where-Object { $_ -match $Filter }).Count -gt 0) {
    try {
        Set-ScratchAt $script:Base
        [void](Invoke-Psql -Database "postgres" -Sql "CREATE DATABASE $($script:Db2) TEMPLATE $($script:Db)")
        $haveDb2 = $true
    }
    catch { $script:Failures++; Write-Host "  FAIL  9 setup: the second scratch database ($($_.Exception.Message))" -ForegroundColor Red }
}

if ($haveDb2) {
    Test-Case "9a a dead scratch record found by a run on another database: the recorded one is restored, no hold" {
        Set-ScratchAt $script:Head
        $store = New-Store
        Write-DeadGuard -Store $store -Database $script:Db
        $before2 = Get-RevisionOf $script:Db2
        $x = Invoke-AfterDeadGuard -Store $store -Url $script:Url2
        Assert-Equal 0 $x.Run.ExitCode "the run goes ($($x.Run.StdErr))"
        Assert-True $x.Ran "the command ran"
        Assert-NoHold -Store $store -Run $x.Run
        Assert-True ($x.Run.StdErr -match "SEMA_KORUMA olu_kosu geri_alindi $($script:Head) -> $($script:Base) \('$($script:Db)'\)") "the recorded database is restored: $($x.Run.StdErr)"
        Assert-Equal $script:Base (Get-ScratchRevision) "the recorded database is back at the recorded revision"
        Assert-Equal $before2 (Get-RevisionOf $script:Db2) "the run's own database is untouched"
    }

    Test-Case "9b a dead record whose database is gone is dropped, no hold" {
        $store = New-Store
        $gone = "pagentos_slotguard_gone_" + [guid]::NewGuid().ToString("N").Substring(0, 8)
        Write-DeadGuard -Store $store -Database $gone
        $x = Invoke-AfterDeadGuard -Store $store -Url $script:Url2
        Assert-Equal 0 $x.Run.ExitCode "the run goes ($($x.Run.StdErr))"
        Assert-True $x.Ran "the command ran"
        Assert-NoHold -Store $store -Run $x.Run
        Assert-True ($x.Run.StdErr -match "SEMA_KORUMA olu_kosu kayit_dusuruldu $gone") "the dropped record is said: $($x.Run.StdErr)"
    }

    Test-Case "9c the other way round: the record names the settings' database, the run a scratch one" {
        # Db2 stands in for the shared database; the run's settings point at the scratch Db.
        Set-ScratchAt $script:Base
        $up = Use-DatabaseUrl -Url $script:Url2 -Body { Invoke-NativeProcess -FilePath $uv -Arguments @("run", "alembic", "upgrade", $script:Head) -WorkingDirectory $apiDir -TimeoutSeconds 900 }
        Assert-True $up.Success "the stand-in migrated to Head ($($up.StdErr))"
        $store = New-Store
        Write-DeadGuard -Store $store -Database $script:Db2
        $x = Invoke-AfterDeadGuard -Store $store -Url $script:Url
        Assert-Equal 0 $x.Run.ExitCode "the run goes ($($x.Run.StdErr))"
        Assert-True $x.Ran "the command ran"
        Assert-NoHold -Store $store -Run $x.Run
        Assert-Equal $script:Base (Get-RevisionOf $script:Db2) "the recorded database is restored"
        Assert-Equal $script:Base (Get-ScratchRevision) "the run's own database is untouched"
    }

    Test-Case "9d a hold on a dead record names that database and its URL in the Danışman's command" {
        $store = New-Store
        [void](Invoke-Psql -Database $script:Db2 -Sql "update alembic_version set version_num='zz_unknown_head'")
        Write-DeadGuard -Store $store -Database $script:Db2
        $x = Invoke-AfterDeadGuard -Store $store -Url $script:Url
        [void](Invoke-Psql -Database $script:Db2 -Sql "update alembic_version set version_num='$($script:Base)'")
        Assert-Equal 6 $x.Run.ExitCode "the run is refused ($($x.Run.StdErr))"
        Assert-True (-not $x.Ran) "the command never started"
        $line = @(([string]$x.Run.StdErr) -split "\r?\n" | Where-Object { $_ -match "DURDU" }) -join " "
        Assert-True ($line -match "PAGENTOS_DATABASE_URL=\S*/$($script:Db2)\b") "the command names the recorded database's URL: $line"
        Assert-True ($line -notmatch "pagentos-dev") "the password is not in the line: $line"
        Assert-True ($line -notmatch "/$($script:Db)\b") "never the run's own database: $line"
        [void](Invoke-Slot @("unblock", "-Store", $store))
    }
}

foreach ($d in @($script:Db2, $script:Db)) {
    try { [void](Invoke-Psql -Database "postgres" -Sql "DROP DATABASE IF EXISTS $d WITH (FORCE)") }
    catch { Write-Host "  (scratch database left: $d)" }
}
try { Remove-Item -LiteralPath $script:TempRoot -Recurse -Force -ErrorAction Stop } catch { Write-Host "  (temp folder left: $script:TempRoot)" }

Write-Host ""
Write-Host "test-slots tests:$script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
