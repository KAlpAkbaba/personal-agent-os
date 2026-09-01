<#
.SYNOPSIS
    Shared knowledge about the locally run Cloud Core: how to find its processes, and the
    marker that records WHICH database a started instance serves.

.DESCRIPTION
    The marker exists for one question a health probe cannot answer: "is the broker that is
    answering on this port serving the dedicated pagentos_prod database, or something
    else?" The real enrollment row was once destroyed because a broker silently shared the
    integration suite's database — so 'reachable and healthy' must never be read as
    'suitable'. dev-broker.ps1 writes the marker when it starts an instance and removes it
    on -Stop; an orchestrator that finds a live broker whose marker names the right
    database may REUSE it instead of restarting it. No marker, or a marker that does not
    match a live broker process, means unknown — and unknown is not suitable.

    The marker is orchestration metadata only (pid, port, database name, start time).
    Nothing secret goes in it.
#>

Set-StrictMode -Version Latest

function Get-BrokerProcesses {
    <#  uvicorn started through `uv run` is a child; match on the command line.  #>
    return @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='uv.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match "uvicorn" -and $_.CommandLine -match "app\.main:app" })
}

function Get-DevBrokerMarkerPath {
    return (Join-Path $env:LOCALAPPDATA "PagentOS\dev-broker.json")
}

function Write-DevBrokerMarker {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][int]$BrokerPid,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$DatabaseName
    )

    $path = Get-DevBrokerMarkerPath
    $directory = Split-Path -Parent $path
    if (-not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }
    $marker = @{
        pid        = $BrokerPid
        port       = $Port
        database   = $DatabaseName
        started_at = (Get-Date).ToUniversalTime().ToString("o")
    } | ConvertTo-Json
    [System.IO.File]::WriteAllText($path, $marker, (New-Object System.Text.UTF8Encoding($false)))
}

function Remove-DevBrokerMarker {
    Remove-Item -LiteralPath (Get-DevBrokerMarkerPath) -Force -ErrorAction SilentlyContinue
}

function Get-DevBrokerDatabase {
    <#
    .SYNOPSIS
        The database name the CURRENTLY RUNNING local broker serves, or $null when that
        cannot be proven.

    .DESCRIPTION
        $null on: no marker, unreadable/invalid marker, marker whose pid is not among the
        live broker processes (stale file from an earlier instance). Every $null is
        deliberate: an orchestrator must treat "cannot prove the database" as "not
        suitable for reuse", never guess.
    #>
    [CmdletBinding()]
    param()

    $path = Get-DevBrokerMarkerPath
    if (-not (Test-Path -LiteralPath $path)) { return $null }

    try {
        $marker = [System.IO.File]::ReadAllText($path) | ConvertFrom-Json
    }
    catch {
        Write-Warning "dev-broker marker at $path is unreadable ($($_.Exception.Message)); treating the running broker's database as unknown"
        return $null
    }

    # Schema-safe: the marker is our own file, but an old or hand-edited one must degrade
    # to "unknown", not to PropertyNotFoundStrict.
    $markerPid = Get-OptionalProperty -InputObject $marker -Name "pid"
    $database = Get-OptionalProperty -InputObject $marker -Name "database"
    if ($null -eq $markerPid -or [string]::IsNullOrWhiteSpace([string]$database)) { return $null }

    $live = @(Get-BrokerProcesses | Where-Object { $_.ProcessId -eq [int]$markerPid })
    if (@($live).Count -eq 0) {
        # An ELEVATED-started broker hides its command line from a non-elevated querier, so
        # the match above can miss a live instance. Fall back to pid + image name; anything
        # else - dead pid, or a pid recycled to some unrelated process - stays unknown.
        $process = Get-Process -Id ([int]$markerPid) -ErrorAction SilentlyContinue
        if (-not $process -or $process.ProcessName -notin @("python", "uv")) { return $null }
    }

    return [string]$database
}
