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
      * a downgrade that fails writes <store>\database-hold.json: every later 'database' ask or
        run is refused with a DURDU line until the Danışman repairs it and runs `unblock`;
      * nothing is recorded when there was no alembic_version table (restoring would mean
        'downgrade base' - dropping everything), and a record that cannot be read is said
        loudly and the run goes on unguarded.

    Windows PowerShell 5.1, StrictMode.
#>

Set-StrictMode -Version Latest

# One line of Python, run with `uv run` from the tree's services\api: the revision row(s) and
# the database name, read raw (no alembic: a revision this tree does not know must still read).
$script:TestSlotRevisionProbe = 'from sqlalchemy import create_engine, text; from app.config import get_settings; e = create_engine(get_settings().database_url); c = e.connect(); t = c.execute(text("select to_regclass(''alembic_version'')")).scalar(); rows = list(c.execute(text("select version_num from alembic_version")).scalars()) if t else []; print("DB=" + str(e.url.database)); print("TABLE=" + ("1" if t else "0")); print("REV=" + ",".join(sorted(rows)))'

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

function Format-TestSlotHoldLine {
    param([Parameter(Mandatory = $true)]$Hold)
    return ("DURDU | dev veritabanı '{0}' {1} şemasında kaldı, beklenen {2} | görev {3} ({4}), bilet {5}, {6} | ağaç: {7} | hata: {8} | Danışman: o ağacın services\api klasöründen 'uv run alembic downgrade {2}' (ya da elle onarım), sonra 'test-slot.ps1 unblock'" -f `
            $Hold.database, $Hold.found, $Hold.expected, $Hold.task, $Hold.role, $Hold.ticket, $Hold.at, $Hold.tree, $Hold.error)
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
    <# Ok, Database, HasTable, Revision ('' when no row), Error. Never throws. #>
    param([Parameter(Mandatory = $true)][string]$AlembicDir, [string]$Uv = "")
    $fail = { param($m) [pscustomobject]@{ Ok = $false; Database = ""; HasTable = $false; Revision = ""; Error = $m } }
    if (-not $Uv) { $Uv = Resolve-TestSlotUv }
    if (-not $Uv) { return (& $fail "uv not found") }
    try { $r = Invoke-NativeProcess -FilePath $Uv -Arguments @("run", "python", "-c", $script:TestSlotRevisionProbe) -WorkingDirectory $AlembicDir -TimeoutSeconds 300 }
    catch { return (& $fail $_.Exception.Message) }
    $out = [string]$r.StdOut
    $db = [regex]::Match($out, '(?m)^DB=(.*?)\r?$')
    $table = [regex]::Match($out, '(?m)^TABLE=([01])\r?$')
    $rev = [regex]::Match($out, '(?m)^REV=(.*?)\r?$')
    if (-not $r.Success -or -not $rev.Success -or -not $table.Success) {
        $tail = @(([string]$r.StdErr) -split "\r?\n" | Where-Object { $_.Trim() } | Select-Object -Last 2) -join " / "
        return (& $fail "the revision probe failed (exit $($r.ExitCode)): $tail")
    }
    return [pscustomobject]@{ Ok = $true; Database = $db.Groups[1].Value; HasTable = ($table.Groups[1].Value -eq "1"); Revision = $rev.Groups[1].Value; Error = "" }
}

function Invoke-TestSlotSchemaRestore {
    <# `uv run alembic downgrade <Revision>` from the run's tree. Ok, Error. Never throws. #>
    param([Parameter(Mandatory = $true)][string]$AlembicDir, [Parameter(Mandatory = $true)][string]$Revision, [string]$Uv = "")
    if (-not $Uv) { $Uv = Resolve-TestSlotUv }
    if (-not $Uv) { return [pscustomobject]@{ Ok = $false; Error = "uv not found" } }
    try { $r = Invoke-NativeProcess -FilePath $Uv -Arguments @("run", "alembic", "downgrade", $Revision) -WorkingDirectory $AlembicDir -TimeoutSeconds 1800 }
    catch { return [pscustomobject]@{ Ok = $false; Error = $_.Exception.Message } }
    if ($r.Success) { return [pscustomobject]@{ Ok = $true; Error = "" } }
    $tail = @(([string]$r.StdErr + "`n" + [string]$r.StdOut) -split "\r?\n" | Where-Object { $_.Trim() } | Select-Object -Last 2) -join " / "
    return [pscustomobject]@{ Ok = $false; Error = "alembic downgrade $Revision exit $($r.ExitCode): $tail" }
}
