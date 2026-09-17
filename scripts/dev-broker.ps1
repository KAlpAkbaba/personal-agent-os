<#
.SYNOPSIS
    Start the local Cloud Core (API + device broker) — the canonical way to run it.

.DESCRIPTION
    `dev-up.ps1` starts the infrastructure containers; `e2e-m1-device.ps1` starts a throwaway
    API on an ephemeral port against a temporary identity root. Neither runs the thing a
    real, installed Windows agent connects to, and until now nothing did — which is why a
    correctly installed Device Service found no listener on 127.0.0.1:8001.

    This script is that missing piece:

      * infrastructure first (compose stack), then migrations, then the API;
      * the port is not assumed. It defaults to whatever the INSTALLED agent is configured
        to dial, read from its own appsettings.json, so the broker and the agent cannot
        disagree about where they meet;
      * the identity root is the persistent one, not a temp directory, so an owner
        credential bootstrapped once survives restarts of this process;
      * it waits for /v1/system/health and reports the socket it is actually listening on
        rather than the one it was asked for.

    Run it from an ordinary (non-elevated) shell: the API needs no privileges, and giving it
    any would be a mistake.

.PARAMETER Port
    Overrides the port. By default it is taken from the installed agent's configuration, and
    falls back to 8001 when no agent is installed.

.EXAMPLE
    .\scripts\dev-broker.ps1
    .\scripts\dev-broker.ps1 -Stop
#>
[CmdletBinding()]
param(
    [int]$Port = 0,
    [string]$AgentConfig = (Join-Path $env:ProgramFiles "PagentOS\agent\service\appsettings.json"),

    # The database this Cloud Core uses. Defaults to a DEDICATED database, not the test one:
    # the real machine's enrollment row was destroyed because the "production" broker shared
    # `pagentos` with the integration suite, whose migration test round-trips the schema
    # (drop + recreate). Real state and test state never share a database again.
    [string]$DatabaseName = "pagentos_prod",

    [switch]$SkipInfra,
    [switch]$Stop,
    [int]$TimeoutSeconds = 90
)

$ErrorActionPreference = "Continue"
# Stated here, not inherited: the dot-sourced libraries set StrictMode and it propagates,
# so this script has ALWAYS run strict — a `.dependencies` access against a property the
# health schema never had died here in a real run. Declaring it makes that contract visible.
Set-StrictMode -Version Latest
$repoRoot = Split-Path -Parent $PSScriptRoot
$apiRoot = Join-Path $repoRoot "services\api"

. (Join-Path $PSScriptRoot "lib\NativeProcess.ps1")
. (Join-Path $PSScriptRoot "lib\DevBroker.ps1")

function Get-Uv {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe"),
        "uv"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -eq "uv") {
            $found = Get-Command uv -ErrorAction SilentlyContinue
            if ($found) { return $found.Source }
        }
        elseif (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    throw "uv not found; install it or add it to PATH"
}

function Get-ConfiguredPort {
    <#
    .SYNOPSIS
        The port the installed agent actually dials — read, not assumed.
    #>
    param([string]$ConfigPath, [int]$Explicit)

    if ($Explicit -gt 0) {
        Write-Host "port $Explicit (from -Port)"
        return $Explicit
    }

    if (Test-Path -LiteralPath $ConfigPath) {
        try {
            $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
            if ($config.BrokerRestUrl) {
                $uri = [Uri]$config.BrokerRestUrl
                Write-Host "port $($uri.Port) (from the installed agent's BrokerRestUrl: $($config.BrokerRestUrl))"
                return $uri.Port
            }
        }
        catch {
            Write-Warning "could not read $ConfigPath ($($_.Exception.Message)); falling back to 8001"
        }
    }

    Write-Host "port 8001 (default; no installed agent configuration found at $ConfigPath)"
    return 8001
}

if ($Stop) {
    $running = @(Get-BrokerProcesses)
    if (@($running).Count -eq 0) {
        Write-Host "no local broker is running"
        exit 0
    }
    $taskkill = Get-SystemTool -Name "taskkill.exe"
    foreach ($process in $running) {
        # /T: `uv run` spawns python children that would otherwise keep the port open.
        [void](Invoke-NativeProcess -FilePath $taskkill -Arguments @("/PID", "$($process.ProcessId)", "/T", "/F") -SuccessExitCodes @(0, 128))
    }
    Write-Host "stopped $(@($running).Count) broker process(es)"
    Remove-DevBrokerMarker
    exit 0
}

$Port = Get-ConfiguredPort -ConfigPath $AgentConfig -Explicit $Port
$baseUrl = "http://127.0.0.1:$Port"

# "Already running" is decided by process match OR a live listener on the port: an
# elevated-started broker hides its command line from a non-elevated query, and starting a
# duplicate against a bound port just dies while riding the existing instance's health —
# which is exactly the confusing outcome this branch exists to prevent.
$existing = @(Get-BrokerProcesses)
$portListeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
if (@($existing).Count -gt 0 -or @($portListeners).Count -gt 0) {
    $existingPid = if (@($existing).Count -gt 0) { $existing[0].ProcessId } else { $portListeners[0].OwningProcess }
    $servingDb = Get-DevBrokerDatabase
    if ($servingDb) {
        Write-Host "a local broker is already running (pid $existingPid, database $servingDb); checking its health"
    }
    else {
        Write-Warning "a local broker is already running (pid $existingPid) but its DATABASE cannot be proven (no matching marker). Health alone does not make it suitable; stop it with -Stop and rerun to get a provable instance."
    }
}
else {
    if (-not $SkipInfra) {
        Write-Host "starting the infrastructure stack..."
        & (Join-Path $PSScriptRoot "dev-up.ps1")
        if ($LASTEXITCODE -ne 0) { throw "dev-up.ps1 failed; the API cannot run without PostgreSQL" }
    }

    $uv = Get-Uv

    $databaseUrl = "postgresql+psycopg://pagentos:pagentos-dev@127.0.0.1:15432/$DatabaseName"
    Write-Host "database: $DatabaseName (isolated from the integration suite's 'pagentos')"

    # Create the database if it does not exist yet. CREATE DATABASE cannot run inside a
    # transaction, and psql inside the container is the simplest reliable path.
    $docker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    $exists = Invoke-NativeProcess -FilePath $docker -Arguments @(
        "exec", "pagentos-postgres", "psql", "-U", "pagentos", "-d", "postgres", "-tAc",
        "SELECT 1 FROM pg_database WHERE datname='$DatabaseName'"
    ) -TimeoutSeconds 60
    if ($exists.StdOut.Trim() -ne "1") {
        Write-Host "creating database $DatabaseName ..."
        $create = Invoke-NativeProcess -FilePath $docker -Arguments @(
            "exec", "pagentos-postgres", "psql", "-U", "pagentos", "-d", "postgres", "-c",
            "CREATE DATABASE $DatabaseName OWNER pagentos"
        ) -TimeoutSeconds 60
        Assert-NativeSuccess -Result $create -Activity "create database $DatabaseName"
    }

    Write-Host "applying migrations to $DatabaseName ..."
    $env:PAGENTOS_DATABASE_URL = $databaseUrl
    try {
        $migrate = Invoke-NativeProcess -FilePath $uv -Arguments @("run", "alembic", "upgrade", "head") `
            -WorkingDirectory $apiRoot -TimeoutSeconds 300
        Assert-NativeSuccess -Result $migrate -Activity "alembic upgrade head ($DatabaseName)"
    }
    finally {
        Remove-Item Env:\PAGENTOS_DATABASE_URL -ErrorAction SilentlyContinue
    }

    Write-Host "starting the Cloud Core on $baseUrl ..."
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $uv
    # --ws-max-size caps device WebSocket frames well above any legal protocol frame
    # (M1 security review #2; uvicorn's default of 16 MiB applies pre-auth).
    $psi.Arguments = "run uvicorn app.main:app --host 127.0.0.1 --port $Port --ws-max-size 1048576"
    $psi.WorkingDirectory = $apiRoot
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.EnvironmentVariables["PAGENTOS_DATABASE_URL"] = $databaseUrl
    $broker = [System.Diagnostics.Process]::Start($psi)
    # Record WHICH database this instance serves — a later orchestrator may only reuse a
    # running broker whose database it can prove (the enrollment row was once destroyed by
    # a broker silently sharing the test database).
    Write-DevBrokerMarker -BrokerPid $broker.Id -Port $Port -DatabaseName $DatabaseName
}

Write-Host "waiting for health..."
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$health = $null
while ((Get-Date) -lt $deadline) {
    try {
        $health = Invoke-RestMethod -Uri "$baseUrl/v1/system/health" -TimeoutSec 3 -ErrorAction Stop
        break
    }
    catch {
        Start-Sleep -Milliseconds 500
    }
}

if (-not $health) {
    throw "the Cloud Core did not answer $baseUrl/v1/system/health within ${TimeoutSeconds}s"
}

# Report the socket it is REALLY listening on, not the one we asked for.
$listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
Write-Host ""
Write-Host "Cloud Core is up:" -ForegroundColor Green
Write-Host "  health   : $baseUrl/v1/system/health -> $($health.status)"
foreach ($listener in $listeners) {
    Write-Host "  listening: $($listener.LocalAddress):$($listener.LocalPort) (pid $($listener.OwningProcess))"
}
if (@($listeners).Count -eq 0) {
    Write-Warning "health answered but no listening socket was found on port $Port - check for a proxy"
}

# Real health schema ({status, version, checks}): `checks` is a MAP of subsystem name ->
# check object. There is no `dependencies` array and never was — a run died here on exactly
# that imagined property under StrictMode. `status` above is required (direct access, loud
# if it ever vanishes); `checks` is read through the explicit optional accessor, and an
# absent or empty map on a healthy answer is REPORTED, not defaulted away.
$checks = Get-OptionalProperty -InputObject $health -Name "checks"
if ($null -eq $checks) {
    Write-Warning "the health document carried no 'checks' object - the Cloud Core answered, but subsystem states are unknown"
}
else {
    $checkProperties = @($checks.PSObject.Properties)
    if (@($checkProperties).Count -eq 0) {
        Write-Warning "the health document's 'checks' object is empty - no subsystem reported"
    }
    foreach ($check in $checkProperties) {
        $checkStatus = Get-OptionalProperty -InputObject $check.Value -Name "status"
        if ($null -eq $checkStatus) { $checkStatus = "(no status reported)" }
        Write-Host "  check    : $($check.Name) = $checkStatus"
    }
}

Write-Host ""
Write-Host "stop it with: .\scripts\dev-broker.ps1 -Stop"
