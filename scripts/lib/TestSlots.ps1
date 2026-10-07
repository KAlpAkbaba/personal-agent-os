<#
.SYNOPSIS
    The 'database' slot's schema guard: a run leaves the shared dev database at the alembic
    revision it found (scripts/team/test-slot.ps1 run; the queue itself is TeamTestSlots.ps1).

.DESCRIPTION
    2026-10-04 17:33: the release gate on 29aa2232 failed at 'Alembic upgrade head' with
    "Can't locate revision identified by '0066_watches'". A worker had run its Postgres tests
    through a 'database' slot on the SHARED dev stack; the suite migrated the dev database to
    its branch's new head and left it there, and no other tree (main included) knows 0066.

    The rule, in one place:
      * before a 'database' run, the database's revision is READ (the alembic_version table,
        through the run's own tree's settings - so the same URL its alembic uses);
      * after the run - success or failure - if the revision moved, the run's own tree (the
        only one that knows the newer revision) downgrades back to the recorded one;
      * the record is the only source of WHICH database: the check and the downgrade are pointed
        at the recorded database by name (PAGENTOS_SLOT_GUARD_DB for the probe, a derived
        PAGENTOS_DATABASE_URL for alembic), whatever a later run's settings name; a recorded
        database the server no longer has is dropped ('kayit_dusuruldu'), never a hold;
      * a downgrade that fails writes <store>\database-hold.json: every later 'database' ask or
        run is refused with a DURDU line until the Danışman repairs it and runs `unblock`;
      * the record is also WRITTEN, to <store>\database-guard.json (revision, tree, database,
        the wrapper's and the command's pid with start time), before the command and removed
        after the restore: a killed wrapper runs no `finally`, so the next 'database' run that
        finds a dead owner's record restores from that tree first (or holds), and while the
        dead run's command still runs a 'database' ask warns and a 'database' run is refused
        naming its pid (the slot itself belongs to the wrapper: a killed one frees it);
      * nothing is recorded when there was no alembic_version table (restoring would mean
        'downgrade base' - dropping everything), and a record that cannot be read is said
        loudly and the run goes on unguarded.

    Windows PowerShell 5.1, StrictMode.
#>

Set-StrictMode -Version Latest

# Python run with `uv run` from the tree's services\api: the revision row(s) and the database
# name, read raw (no alembic: a revision this tree does not know must still read). With
# PAGENTOS_SLOT_GUARD_DB set, that database is read on the settings' server whatever the
# settings name (a guard record is the only source of the database to restore), MISSING=1 when
# the server has no such database, and URL= is the URL for the restore's alembic (kept in
# memory only; SAFEURL= is the same without the password, for the lines a person reads).
$script:TestSlotRevisionProbe = @'
import os
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from app.config import get_settings
u = make_url(get_settings().database_url)
d = os.environ.get("PAGENTOS_SLOT_GUARD_DB", "")
if d:
    u = u.set(database=d)
print("DB=" + str(u.database))
print("SAFEURL=" + u.render_as_string(hide_password=True))
print("URL=" + u.render_as_string(hide_password=False))
try:
    c = create_engine(u).connect()
except Exception:
    if d:
        with create_engine(u.set(database="postgres")).connect() as m:
            n = m.execute(text("select count(*) from pg_database where datname = :d"), {"d": d}).scalar()
        if not n:
            print("MISSING=1")
            raise SystemExit(0)
    raise
t = c.execute(text("select to_regclass('alembic_version')")).scalar()
rows = list(c.execute(text("select version_num from alembic_version")).scalars()) if t else []
print("TABLE=" + ("1" if t else "0"))
print("REV=" + ",".join(sorted(rows)))
'@

function Invoke-WithTestSlotEnv {
    <# $Body with the environment variable $Name set to $Value ('' = unset) for the children it starts; restored after. #>
    param([Parameter(Mandatory = $true)][string]$Name, [string]$Value = "", [Parameter(Mandatory = $true)][scriptblock]$Body)
    $saved = [Environment]::GetEnvironmentVariable($Name, "Process")
    try {
        [Environment]::SetEnvironmentVariable($Name, $(if ($Value) { $Value } else { $null }), "Process")
        return (& $Body)
    }
    finally { [Environment]::SetEnvironmentVariable($Name, $saved, "Process") }
}

function Get-TestSlotHoldPath {
    param([Parameter(Mandatory = $true)][string]$Store)
    return (Join-Path $Store "database-hold.json")
}

function Get-TestSlotDatabaseHold {
    <# The hold, or $null. A hold file that does not parse is still a hold. #>
    param([Parameter(Mandatory = $true)][string]$Store)
    $path = Get-TestSlotHoldPath -Store $Store
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    $raw = ""
    try { $raw = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8) } catch { }
    try {
        $h = $raw | ConvertFrom-Json
        if ($null -ne $h) { return $h }
    }
    catch { }
    return [pscustomobject]@{ at = ""; task = "?"; role = "?"; ticket = "?"; database = "?"; tree = "?"; found = "?"; expected = "?"; error = "the hold file does not parse: $path" }
}

function Set-TestSlotDatabaseHold {
    param([Parameter(Mandatory = $true)][string]$Store, [Parameter(Mandatory = $true)]$Hold)
    if (-not (Test-Path -LiteralPath $Store)) { [void](New-Item -ItemType Directory -Path $Store -Force) }
    $path = Get-TestSlotHoldPath -Store $Store
    $tmp = "$path.$PID.tmp"
    [System.IO.File]::WriteAllText($tmp, ($Hold | ConvertTo-Json -Depth 4), (New-Object System.Text.UTF8Encoding $false))
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    Move-Item -LiteralPath $tmp -Destination $path
}

function Clear-TestSlotDatabaseHold {
    <# $true when a hold was lifted. #>
    param([Parameter(Mandatory = $true)][string]$Store)
    $path = Get-TestSlotHoldPath -Store $Store
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    Remove-Item -LiteralPath $path -Force
    return $true
}

# ------------------------------------------------------------------------------- the guard record
# A `finally` does not run when the wrapper is killed (an agent's Bash timeout kills it often).
# So the promise "put it back" is also written down: <store>\database-guard.json, before the
# command starts, removed only after the restore (or the hold). A later database ask/run that
# finds a guard whose wrapper is dead finishes the dead run's restore first.

function Get-TestSlotGuardPath {
    param([Parameter(Mandatory = $true)][string]$Store)
    return (Join-Path $Store "database-guard.json")
}

function Get-TestSlotDatabaseGuard {
    <# The guard record, or $null. One that does not parse comes back with expected = '' (it cannot be restored: a hold). #>
    param([Parameter(Mandatory = $true)][string]$Store)
    $path = Get-TestSlotGuardPath -Store $Store
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    $raw = ""
    try { $raw = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8) } catch { }
    try {
        $g = $raw | ConvertFrom-Json
        if ($null -ne $g -and @($g.PSObject.Properties.Name) -contains "expected") { return $g }
    }
    catch { }
    return [pscustomobject]@{ at = ""; task = "?"; role = "?"; ticket = "?"; database = "?"; tree = ""; expected = ""; holder_pid = 0; holder_start = ""; child_pid = 0; child_start = "" }
}

function Set-TestSlotDatabaseGuard {
    param([Parameter(Mandatory = $true)][string]$Store, [Parameter(Mandatory = $true)]$Guard)
    if (-not (Test-Path -LiteralPath $Store)) { [void](New-Item -ItemType Directory -Path $Store -Force) }
    $path = Get-TestSlotGuardPath -Store $Store
    $tmp = "$path.$PID.tmp"
    [System.IO.File]::WriteAllText($tmp, ($Guard | ConvertTo-Json -Depth 4), (New-Object System.Text.UTF8Encoding $false))
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    Move-Item -LiteralPath $tmp -Destination $path
}

function Clear-TestSlotDatabaseGuard {
    param([Parameter(Mandatory = $true)][string]$Store)
    $path = Get-TestSlotGuardPath -Store $Store
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
}

function Get-TestSlotGuardState {
    <# 'alive' (its wrapper runs), 'orphan' (the wrapper is dead, its command still runs) or
       'dead' (both gone). A pid is a process only with its start time (TeamTestSlots.ps1). #>
    param([Parameter(Mandatory = $true)]$Guard)
    $holder = [pscustomobject]@{ holder_pid = [int]$Guard.holder_pid; holder_start = [string]$Guard.holder_start }
    if ([int]$Guard.holder_pid -gt 0 -and (Test-TestSlotHolderAlive -Entry $holder)) { return "alive" }
    if ([int]$Guard.child_pid -gt 0) {
        $child = [pscustomobject]@{ holder_pid = [int]$Guard.child_pid; holder_start = [string]$Guard.child_start }
        if (Test-TestSlotHolderAlive -Entry $child) { return "orphan" }
    }
    return "dead"
}

function Format-TestSlotOrphanLine {
    <# -ProcessId: the process that still has the database (the dead run's command, or a wrapper that still runs). #>
    param([Parameter(Mandatory = $true)]$Guard, [Parameter(Mandatory = $true)][int]$ProcessId)
    return ("DURDU | bir database koşusunun süreci (pid {0}) hâlâ çalışıyor ve slotu tutmuyor: dev veritabanı '{1}' ona ait | görev {2} ({3}), bilet {4}, {5} | ağaç: {6} | o süreç bitince (ya da durdurulunca) sonraki database koşusu şemayı {7}'e geri alır" -f `
            $ProcessId, $Guard.database, $Guard.task, $Guard.role, $Guard.ticket, $Guard.at, $Guard.tree, $Guard.expected)
}

function Format-TestSlotHoldLine {
    <# The command names the held database itself (PAGENTOS_DATABASE_URL, password hidden), never
       the one the tree's settings point at. A hold written before the 'url' field: by name. #>
    param([Parameter(Mandatory = $true)]$Hold)
    $url = if (@($Hold.PSObject.Properties.Name) -contains "url") { [string]$Hold.url } else { "" }
    $target = if ($url) { "PAGENTOS_DATABASE_URL=$url (parola ağacın ayarlarındaki) ile" } else { "PAGENTOS_DATABASE_URL '$($Hold.database)' veritabanını gösterirken" }
    return ("DURDU | dev veritabanı '{0}' {1} şemasında kaldı, beklenen {2} | görev {3} ({4}), bilet {5}, {6} | ağaç: {7} | hata: {8} | Danışman: o ağacın services\api klasöründen {9} 'uv run alembic downgrade {2}' (ya da elle onarım), sonra 'test-slot.ps1 unblock'" -f `
            $Hold.database, $Hold.found, $Hold.expected, $Hold.task, $Hold.role, $Hold.ticket, $Hold.at, $Hold.tree, $Hold.error, $target)
}

function Find-TestSlotAlembicDir {
    <# The run's tree's services\api (the folder holding alembic.ini), from its working directory up; $null when none. #>
    param([Parameter(Mandatory = $true)][string]$StartDirectory)
    $dir = $StartDirectory
    while ($dir) {
        if (Test-Path -LiteralPath (Join-Path $dir "alembic.ini") -PathType Leaf) { return $dir }
        $api = Join-Path $dir "services\api"
        if (Test-Path -LiteralPath (Join-Path $api "alembic.ini") -PathType Leaf) { return $api }
        $parent = Split-Path -Parent $dir
        if ($parent -eq $dir) { break }
        $dir = $parent
    }
    return $null
}

function Resolve-TestSlotUv {
    $c = @(Get-Command "uv" -CommandType Application -ErrorAction SilentlyContinue) | Select-Object -First 1
    if ($null -ne $c) { return $c.Source }
    foreach ($f in @("%USERPROFILE%\.local\bin\uv.exe", "%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe")) {
        $x = [Environment]::ExpandEnvironmentVariables($f)
        if (Test-Path -LiteralPath $x) { return $x }
    }
    return $null
}

function Get-TestSlotSchemaRevision {
    <# Ok, Database, Missing, HasTable, Revision ('' when no row), Url, SafeUrl, Error. Never throws.
       -Database: read THAT database on the settings' server (the guard record's), not the one
       the tree's settings name; Missing is $true when the server has no such database. #>
    param([Parameter(Mandatory = $true)][string]$AlembicDir, [string]$Database = "", [string]$Uv = "")
    $fail = { param($m) [pscustomobject]@{ Ok = $false; Database = $Database; Missing = $false; HasTable = $false; Revision = ""; Url = ""; SafeUrl = ""; Error = $m } }
    if (-not $Uv) { $Uv = Resolve-TestSlotUv }
    if (-not $Uv) { return (& $fail "uv not found") }
    try {
        $r = Invoke-WithTestSlotEnv -Name "PAGENTOS_SLOT_GUARD_DB" -Value $Database -Body {
            Invoke-NativeProcess -FilePath $Uv -Arguments @("run", "python", "-c", $script:TestSlotRevisionProbe) -WorkingDirectory $AlembicDir -TimeoutSeconds 300
        }
    }
    catch { return (& $fail $_.Exception.Message) }
    $out = [string]$r.StdOut
    $line = { param($k) $m = [regex]::Match($out, "(?m)^$k=(.*?)\r?$"); if ($m.Success) { $m.Groups[1].Value } else { "" } }
    $db = & $line "DB"
    if ($r.Success -and $Database -and (& $line "MISSING") -eq "1") {
        return [pscustomobject]@{ Ok = $true; Database = $db; Missing = $true; HasTable = $false; Revision = ""; Url = ""; SafeUrl = (& $line "SAFEURL"); Error = "" }
    }
    $table = [regex]::Match($out, '(?m)^TABLE=([01])\r?$')
    $rev = [regex]::Match($out, '(?m)^REV=(.*?)\r?$')
    if (-not $r.Success -or -not $rev.Success -or -not $table.Success) {
        $tail = @(([string]$r.StdErr) -split "\r?\n" | Where-Object { $_.Trim() } | Select-Object -Last 2) -join " / "
        return (& $fail "the revision probe failed (exit $($r.ExitCode)): $tail")
    }
    return [pscustomobject]@{ Ok = $true; Database = $db; Missing = $false; HasTable = ($table.Groups[1].Value -eq "1"); Revision = $rev.Groups[1].Value; Url = (& $line "URL"); SafeUrl = (& $line "SAFEURL"); Error = "" }
}

function Invoke-TestSlotSchemaRestore {
    <# `uv run alembic downgrade <Revision>` from the run's tree, with PAGENTOS_DATABASE_URL =
       -DatabaseUrl (the guard record's database) when given. Ok, Error. Never throws. #>
    param([Parameter(Mandatory = $true)][string]$AlembicDir, [Parameter(Mandatory = $true)][string]$Revision, [string]$DatabaseUrl = "", [string]$Uv = "")
    if (-not $Uv) { $Uv = Resolve-TestSlotUv }
    if (-not $Uv) { return [pscustomobject]@{ Ok = $false; Error = "uv not found" } }
    try {
        $r = Invoke-WithTestSlotEnv -Name "PAGENTOS_DATABASE_URL" -Value $(if ($DatabaseUrl) { $DatabaseUrl } else { $env:PAGENTOS_DATABASE_URL }) -Body {
            Invoke-NativeProcess -FilePath $Uv -Arguments @("run", "alembic", "downgrade", $Revision) -WorkingDirectory $AlembicDir -TimeoutSeconds 1800
        }
    }
    catch { return [pscustomobject]@{ Ok = $false; Error = $_.Exception.Message } }
    if ($r.Success) { return [pscustomobject]@{ Ok = $true; Error = "" } }
    $tail = @(([string]$r.StdErr + "`n" + [string]$r.StdOut) -split "\r?\n" | Where-Object { $_.Trim() } | Select-Object -Last 2) -join " / "
    return [pscustomobject]@{ Ok = $false; Error = "alembic downgrade $Revision exit $($r.ExitCode): $tail" }
}
