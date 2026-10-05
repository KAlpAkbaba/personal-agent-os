<#
.SYNOPSIS
    A database of its own for the gate (and for an inspector's or a worker's hand-run) on the dev
    stack's PostgreSQL server: made, swept and dropped inside the running container.

.DESCRIPTION
    ADR-0254 open decision 5, ruled (b); team/plans/gate-faster-adr.md. scripts/quality-gate.ps1
    and every inspector's integration run used the dev stack's ONE database `pagentos` and reset
    it (alembic upgrade, the suite's schema round trip), so a gate and an inspection broke each
    other: `relation "owner_sessions" does not exist`. Now the gate runs its PostgreSQL steps
    against `pagentos_gate_<stamp>_<run>` and drops it in a finally.

    Every command goes through `docker exec pagentos-postgres psql -U pagentos` - the dev
    container, its local socket, no password on any command line, in any environment
    variable or in any log. The application is pointed at the database by the variable its
    Settings already read (the caller names it; the gate's is in quality-gate.ps1), set for one
    step's children only (Invoke-WithGateDatabase).

    A name that does not start with `pagentos_gate_` or `pagentos_scratch_`, is longer than 63
    characters or holds anything but [a-z0-9_] is refused by New- and Remove- before any
    command is sent: nothing here can drop `pagentos` or a production-looking name.

    By hand (an inspector or a worker), from the repository root:

        . .\scripts\lib\GateDatabase.ps1
        $db = Get-GateDatabaseName -RunId "insp_mytask" -Now ([datetime]::UtcNow); New-GateDatabase -Name $db
        try { Invoke-WithGateDatabase -Variable PAGENTOS_DATABASE_URL -Value (Get-GateDatabaseUrl -BaseUrl (Get-GateSettingsDatabaseUrl -Uv <uv> -ApiRoot services\api) -Name $db) -Action { <uv> run pytest tests/integration -q -m integration } } finally { Remove-GateDatabase -Name $db }

    Windows PowerShell 5.1. Dot-sourcing this file defines functions and a few `$script:`
    settings and changes nothing else in the caller (no StrictMode, no preference variables);
    each function sets StrictMode for its own scope.
#>

# ConvertTo-GateArgumentLine (one exact command line) lives with the step group.
. (Join-Path $PSScriptRoot "GateSteps.ps1")

#: The dev stack's container and role (infra/docker/docker-compose.dev.yml). Never another host.
$script:GateDatabaseContainer = "pagentos-postgres"
$script:GateDatabaseUser = "pagentos"
#: A gate database older than this, by the stamp in its name, is dropped by the next gate.
$script:GateDatabaseMaxAgeHours = 24
#: How long one docker / psql call may take before it is a failure (a hang guard).
$script:GateDatabaseCommandSeconds = 120

function Resolve-GateDockerPath {
    <# PAGENTOS_GATE_DOCKER (the tests' fake) first, then docker on PATH, then Docker Desktop's. #>
    if ($env:PAGENTOS_GATE_DOCKER) { return $env:PAGENTOS_GATE_DOCKER }
    $c = Get-Command "docker" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c -and $c.Source) { return $c.Source }
    $desktop = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (Test-Path -LiteralPath $desktop) { return $desktop }
    return ""
}

$script:GateDockerPath = Resolve-GateDockerPath

function Resolve-GateUv {
    <# uv the way the gate finds it: PATH, then the two places it is installed on this machine. #>
    $c = Get-Command "uv" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c -and $c.Source) { return $c.Source }
    foreach ($f in @("%USERPROFILE%\.local\bin\uv.exe", "%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe")) {
        $expanded = [Environment]::ExpandEnvironmentVariables($f)
        if (Test-Path -LiteralPath $expanded) { return $expanded }
    }
    return ""
}

function Invoke-GateProcess {
    <#
        Runs a program with an exact command line; both streams read at once (a full pipe
        never blocks it); a deadline. Returns ExitCode, StdOut, StdErr. Never prints anything,
        so a caller decides what of it may be shown.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [AllowEmptyCollection()][AllowEmptyString()][string[]]$Arguments = @(),
        [string]$WorkingDirectory = "",
        [int]$TimeoutSeconds = 120
    )
    Set-StrictMode -Version Latest
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.Arguments = ConvertTo-GateArgumentLine -Arguments $Arguments
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $true
    $psi.CreateNoWindow = $true
    $psi.WorkingDirectory = if ($WorkingDirectory) { (Resolve-Path -LiteralPath $WorkingDirectory).ProviderPath } else { (Get-Location -PSProvider FileSystem).ProviderPath }
    $process = [System.Diagnostics.Process]::Start($psi)
    $process.StandardInput.Close()
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        try { $process.Kill() } catch { }
        throw "$([System.IO.Path]::GetFileName($FilePath)) did not end within $TimeoutSeconds s"
    }
    $process.WaitForExit()
    $result = [pscustomobject]@{ ExitCode = $process.ExitCode; StdOut = $stdout.GetAwaiter().GetResult(); StdErr = $stderr.GetAwaiter().GetResult() }
    $process.Dispose()
    return $result
}

function Test-GateDatabaseName {
    <# The name rule: pagentos_gate_ or pagentos_scratch_, then [a-z0-9_]; at most 63 characters. #>
    param([AllowEmptyString()][string]$Name)
    Set-StrictMode -Version Latest
    if ($null -eq $Name -or $Name.Length -gt 63) { return $false }
    return ($Name -cmatch '^pagentos_(gate|scratch)_[a-z0-9_]+$')
}

function Assert-GateDatabaseName {
    param([AllowEmptyString()][string]$Name)
    if (-not (Test-GateDatabaseName -Name $Name)) {
        throw "refused: '$Name' is not a gate database name (pagentos_gate_* or pagentos_scratch_*, only [a-z0-9_], at most 63 characters)"
    }
}

function Get-GateDatabaseName {
    <#
        pagentos_gate_<yyyyMMddHHmmss UTC>_<run id>: the stamp is what the sweep reads the age
        from; the run id is lower-cased, every other character becomes '_', and the whole name
        is cut to 63 characters (PostgreSQL's limit).
    #>
    param([string]$RunId = "", [Parameter(Mandatory = $true)][datetime]$Now)
    Set-StrictMode -Version Latest
    # -creplace: a case-insensitive class lets the Turkish dotted capital I (U+0130) through as an 'i' under tr-TR
    $run = ([string]$RunId).ToLowerInvariant() -creplace '[^a-z0-9_]+', '_'
    $run = $run.Trim('_')
    if (-not $run) { $run = [guid]::NewGuid().ToString("N").Substring(0, 8) }
    $name = "pagentos_gate_" + $Now.ToUniversalTime().ToString("yyyyMMddHHmmss", [System.Globalization.CultureInfo]::InvariantCulture) + "_" + $run
    if ($name.Length -gt 63) { $name = $name.Substring(0, 63).TrimEnd('_') }
    return $name
}

function Get-GateDatabaseStamp {
    <# The UTC time in a gate database's name, or $null when it has none. #>
    param([string]$Name)
    Set-StrictMode -Version Latest
    if ($Name -cnotmatch '^pagentos_gate_(\d{14})(_|$)') { return $null }
    $parsed = [datetime]::MinValue
    $ok = [datetime]::TryParseExact($Matches[1], "yyyyMMddHHmmss", [System.Globalization.CultureInfo]::InvariantCulture,
        ([System.Globalization.DateTimeStyles]::AssumeUniversal -bor [System.Globalization.DateTimeStyles]::AdjustToUniversal), [ref]$parsed)
    if (-not $ok) { return $null }
    return $parsed
}

function Select-GateDatabaseToSweep {
    <# Of these names, the gate databases whose stamp is older than the bound. Nothing else, ever. #>
    param([AllowEmptyCollection()][string[]]$Names = @(), [Parameter(Mandatory = $true)][datetime]$Now, [int]$MaxAgeHours = $script:GateDatabaseMaxAgeHours)
    Set-StrictMode -Version Latest
    $bound = $Now.ToUniversalTime().AddHours(-$MaxAgeHours)
    $chosen = foreach ($n in @($Names)) {
        if (-not ($n -cmatch '^pagentos_gate_') -or -not (Test-GateDatabaseName -Name $n)) { continue }
        $stamp = Get-GateDatabaseStamp -Name $n
        if ($null -ne $stamp -and $stamp -lt $bound) { $n }
    }
    return @($chosen)
}

function Invoke-GatePsql {
    <# One SQL command through the dev container's psql. Throws on a non-zero exit with psql's own words. #>
    param([Parameter(Mandatory = $true)][string]$Database, [Parameter(Mandatory = $true)][string]$Sql)
    Set-StrictMode -Version Latest
    if (-not $script:GateDockerPath) { throw "docker was not found; the dev stack's PostgreSQL cannot be reached" }
    $r = Invoke-GateProcess -FilePath $script:GateDockerPath -TimeoutSeconds $script:GateDatabaseCommandSeconds -Arguments @(
        "exec", $script:GateDatabaseContainer, "psql", "-U", $script:GateDatabaseUser, "-d", $Database, "-v", "ON_ERROR_STOP=1", "-tAc", $Sql)
    if ($r.ExitCode -ne 0) {
        $said = ([string]$r.StdErr).Trim()
        throw "psql in $($script:GateDatabaseContainer) (database $Database) exited with code $($r.ExitCode): $said"
    }
    return @(([string]$r.StdOut -split "`r?`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Test-GateDatabaseServer {
    <# Does the dev stack's PostgreSQL answer? False for a missing docker, a stopped engine or container. #>
    Set-StrictMode -Version Latest
    if (-not $script:GateDockerPath) { return $false }
    try {
        $r = Invoke-GateProcess -FilePath $script:GateDockerPath -TimeoutSeconds 30 -Arguments @(
            "exec", $script:GateDatabaseContainer, "pg_isready", "-U", $script:GateDatabaseUser, "-d", "postgres")
        return ($r.ExitCode -eq 0)
    }
    catch { return $false }
}

function Get-GateDatabaseList {
    <# Every database on the dev server, by name. #>
    Set-StrictMode -Version Latest
    return @(Invoke-GatePsql -Database "postgres" -Sql "SELECT datname FROM pg_database ORDER BY datname")
}

function New-GateDatabase {
    <#
        Creates the database on the dev server, owned by the dev role, with the vector extension
        the first migration needs. A database of that name that already exists is an error: a
        gate's database is always fresh. If the extension cannot be made, the database is dropped
        again before the error is thrown.
    #>
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Name)
    Set-StrictMode -Version Latest
    Assert-GateDatabaseName -Name $Name
    [void](Invoke-GatePsql -Database "postgres" -Sql "CREATE DATABASE $Name OWNER $($script:GateDatabaseUser)")
    try { [void](Invoke-GatePsql -Database $Name -Sql "CREATE EXTENSION IF NOT EXISTS vector") }
    catch {
        try { [void](Invoke-GatePsql -Database "postgres" -Sql "DROP DATABASE IF EXISTS $Name WITH (FORCE)") } catch { }
        throw
    }
    Write-Host "gate database: created $Name on the dev server ($($script:GateDatabaseContainer)), with the vector extension"
}

function Remove-GateDatabase {
    <# Drops the database (its open connections too: WITH (FORCE)). No such database is no error. #>
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Name)
    Set-StrictMode -Version Latest
    Assert-GateDatabaseName -Name $Name
    [void](Invoke-GatePsql -Database "postgres" -Sql "DROP DATABASE IF EXISTS $Name WITH (FORCE)")
    Write-Host "gate database: dropped $Name"
}

function Invoke-GateDatabaseSweep {
    <#
        Drops the gate databases a killed gate left behind: older than the bound by the stamp in
        their names. A scratch database, `pagentos` and anything else are never touched. Says how
        many it dropped and returns that number.
    #>
    param([Parameter(Mandatory = $true)][datetime]$Now, [int]$MaxAgeHours = $script:GateDatabaseMaxAgeHours)
    Set-StrictMode -Version Latest
    $all = @(Get-GateDatabaseList)
    $gates = @($all | Where-Object { $_ -cmatch '^pagentos_gate_' })
    $stale = @(Select-GateDatabaseToSweep -Names $all -Now $Now -MaxAgeHours $MaxAgeHours)
    foreach ($n in $stale) { Remove-GateDatabase -Name $n }
    Write-Host "gate database sweep: $(@($stale).Count) older than $MaxAgeHours h dropped (of $(@($gates).Count) pagentos_gate_* on the dev server)"
    return @($stale).Count
}

function Get-GateDatabaseUrl {
    <# The base URL with its database replaced by Name. The result holds a password: never print it. #>
    param([Parameter(Mandatory = $true)][string]$BaseUrl, [Parameter(Mandatory = $true)][string]$Name)
    Set-StrictMode -Version Latest
    Assert-GateDatabaseName -Name $Name
    if ($BaseUrl -notmatch '^(?<head>[a-z0-9+]+://[^/?#]+/)(?<db>[^/?#]*)(?<tail>[?#].*)?$') {
        throw "the database setting is not a URL with a database name (it is not shown: it holds a password)"
    }
    $tail = if ($Matches.ContainsKey("tail")) { $Matches["tail"] } else { "" }
    return $Matches["head"] + $Name + $tail
}

function Get-GateSettingsDatabaseUrl {
    <#
        The database URL the application's Settings resolve here (its .env and its environment
        included), asked of the application itself - so no URL and no password is typed in any
        script. Never printed.
    #>
    param([Parameter(Mandatory = $true)][string]$Uv, [Parameter(Mandatory = $true)][string]$ApiRoot)
    Set-StrictMode -Version Latest
    $r = Invoke-GateProcess -FilePath $Uv -WorkingDirectory $ApiRoot -TimeoutSeconds 900 -Arguments @(
        "run", "python", "-c", "from app.config import Settings; print(Settings().database_url)")
    if ($r.ExitCode -ne 0) { throw "reading the application's database setting failed (uv exited with code $($r.ExitCode))" }
    $line = @(([string]$r.StdOut -split "`r?`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^postgres' }) | Select-Object -Last 1
    if (-not $line) { throw "the application's database setting did not come back as a postgresql URL" }
    return $line
}

function Invoke-WithGateDatabase {
    <#
        Runs Action with the variable set to Value for this process and its children, and puts
        the variable back as it was (or unset) afterwards, whatever Action did: one step's
        children see the gate's database, a later step sees what it saw before.
    #>
    param([Parameter(Mandatory = $true)][string]$Variable, [Parameter(Mandatory = $true)][string]$Value, [Parameter(Mandatory = $true)][scriptblock]$Action)
    $before = [Environment]::GetEnvironmentVariable($Variable, "Process")
    [Environment]::SetEnvironmentVariable($Variable, $Value, "Process")
    try { & $Action }
    finally { [Environment]::SetEnvironmentVariable($Variable, $before, "Process") }
}
