<#
.SYNOPSIS
    Starts the STAGING stack (compose project pagentos-staging) on the home PC, runs the
    migrations and waits until the api says `status: ok` and the web shell answers.
.DESCRIPTION
    Staging is a copy of the real server for the test team (team/plans ADR "staging-stack"):
    the same api and web images, its OWN Postgres, Temporal namespace + task queue, Redis,
    MinIO bucket and identities. It shares nothing with the dev stack the gate resets and
    cannot reach production (services/api/tests/unit/test_staging_isolation.py).

    Memory: refuses to start when less than 6 GB of physical memory is free (exit 3) - the
    home PC also runs the dev stack, gates and agents (2026-10-03: several whole-suite runs
    exhausted its 48 GB). The measured footprint is in the ADR.

    The images are whatever PAGENTOS_STAGING_RELEASE_TAG names (deploy.ps1 sets it to the
    sha it built). Unset: the sha deploy.ps1 last recorded in
    %LOCALAPPDATA%\PagentOS\staging\deployed.json, else `:local` (which -Build builds).

    Health is the BODY, not the status code: /v1/system/health answers HTTP 200 also when it
    says `degraded` (inspector 2026-10-04: after a Docker restart the api came up before
    Temporal, its embedded worker failed once and never retried, and staging ran ~2 h without
    a worker while this script would have said UP). A `degraded` api for -DegradedGraceSec is
    recreated once (`up -d --force-recreate api`) and waited for again. After the PC or Docker
    restarts, run this script: it is how staging recovers.

    Exit codes: 0 up and healthy; 1 docker/compose failure, or still degraded after the
    recreate; 3 not enough free memory; 4 not healthy within -TimeoutSec; 5 migration failed.
.PARAMETER Build
    Build the `:local` images from this tree first (`docker compose build`).
.PARAMETER AssumeFreeMB
    For tests only: use this as the free-memory figure instead of measuring it.
.PARAMETER MeasuredFreeMB
    deploy.ps1 passes the free memory it measured BEFORE building: the build's cache stays in
    the Docker VM (2026-10-04: 13.2 GB free before two builds, 3.6 GB after, staging itself
    0.84 GB), so a second measurement would refuse the deploy the first one allowed.
.PARAMETER CheckOnly
    Run the refusals (memory, docker) and stop before starting anything.
.PARAMETER HealthOnly
    Start nothing: only the health wait (and the degraded recreate). With -ApiHealthUrl,
    -WebUrl and -DockerExe it is how scripts/tests/staging.tests.ps1 drives a fake health.
.PARAMETER DegradedGraceSec
    How long the api may say `degraded` before it is recreated (startup: the worker connects).
.EXAMPLE
    powershell -NoProfile -File scripts\staging\up.ps1 -Build
#>
[CmdletBinding()]
param(
    [int]$TimeoutSec = 300,
    [switch]$Build,
    [int]$AssumeFreeMB = -1,
    [int]$MeasuredFreeMB = -1,
    [switch]$CheckOnly,
    [switch]$HealthOnly,
    [int]$DegradedGraceSec = 60,
    [string]$ApiHealthUrl = "http://127.0.0.1:28001/v1/system/health",
    [string]$WebUrl = "http://127.0.0.1:28000/",
    [string]$DockerExe = ""
)
Set-StrictMode -Version Latest
# "Continue": docker compose reports progress on stderr; failures are exit-code checks.
$ErrorActionPreference = "Continue"

$MinFreeMB = 6144
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$composeFile = Join-Path $repoRoot "infra\docker\docker-compose.staging.yml"
foreach ($url in @($ApiHealthUrl, $WebUrl)) {
    if ($url -notmatch '^http://(127\.0\.0\.1|localhost):\d+/') {
        Write-Host "STAGING FAILED: $url is not a loopback url - staging is only ever checked on this PC"
        exit 1
    }
}

function Get-FreeMemoryMB {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    return [int][math]::Floor([double]$os.FreePhysicalMemory / 1024)
}

if (-not $HealthOnly) {
    $source = ""
    if ($AssumeFreeMB -ge 0) { $freeMB = $AssumeFreeMB }
    elseif ($MeasuredFreeMB -ge 0) { $freeMB = $MeasuredFreeMB; $source = " (measured by deploy before its build)" }
    else { $freeMB = Get-FreeMemoryMB }
    if ($freeMB -lt $MinFreeMB) {
        Write-Host "STAGING REFUSED: only $freeMB MB of memory is free$source; staging needs at least $MinFreeMB MB free to start (a gate or agents may be running - ask the test-slot queue for 'heavy', or try later)."
        exit 3
    }
    Write-Host "memory: $freeMB MB free$source (minimum $MinFreeMB MB) - ok"
}

if ($DockerExe) { $docker = $DockerExe }
else {
    $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
    $docker = if ($dockerCmd) { $dockerCmd.Source } else { "C:\Program Files\Docker\Docker\resources\bin\docker.exe" }
}
if (-not (Test-Path $docker)) { Write-Host "STAGING FAILED: docker not found"; exit 1 }
if (-not (Test-Path $composeFile)) { Write-Host "STAGING FAILED: compose file not found: $composeFile"; exit 1 }
if ($CheckOnly) { Write-Host "check only: nothing started"; exit 0 }

# A plain start (after a reboot) keeps the images deploy.ps1 last put there, not `:local`.
if (-not $env:PAGENTOS_STAGING_RELEASE_TAG -and -not $Build) {
    $deployedFile = Join-Path $env:LOCALAPPDATA "PagentOS\staging\deployed.json"
    if (Test-Path $deployedFile) {
        try {
            $deployed = Get-Content -Raw $deployedFile | ConvertFrom-Json
            if ("$($deployed.sha)" -match '^[0-9a-f]{40}$') {
                $env:PAGENTOS_STAGING_RELEASE_TAG = "$($deployed.sha)".Substring(0, 12)
                $env:PAGENTOS_STAGING_RELEASE = "$($deployed.sha)"
                Write-Host "images: :$($env:PAGENTOS_STAGING_RELEASE_TAG) (the sha deploy.ps1 recorded)"
            }
        } catch { Write-Host "warning: $deployedFile unreadable; images :local" }
    }
}

function Invoke-Compose {
    & $docker compose -f $composeFile @args
}

# "ok", "degraded (<why>)" or "down (<why>)". HTTP 200 alone is not "ok".
function Get-ApiState {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri $ApiHealthUrl -TimeoutSec 5
        $body = $r.Content | ConvertFrom-Json
        $status = if ($body.PSObject.Properties["status"]) { "$($body.status)" } else { "" }
        if ($r.StatusCode -eq 200 -and $status -eq "ok") { return "ok" }
        $failing = if ($body.PSObject.Properties["failing_checks"]) { "$($body.failing_checks)" } else { "" }
        return "degraded (HTTP $($r.StatusCode), status '$status', failing: $failing)"
    } catch { return "down ($($_.Exception.Message))" }
}

function Test-Web {
    try { return ((Invoke-WebRequest -UseBasicParsing -Uri $WebUrl -TimeoutSec 5).StatusCode -eq 200) }
    catch { return $false }
}

# Waits until the api is "ok" and the web answers; "degraded" for -DegradedGraceSec ends it early.
function Wait-Healthy {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $degradedSince = $null
    $state = "down (not asked yet)"
    $webOk = $false
    while ((Get-Date) -lt $deadline) {
        $state = Get-ApiState
        if (-not $webOk) { $webOk = Test-Web }
        if ($state -eq "ok" -and $webOk) { break }
        if ($state.StartsWith("degraded")) {
            if (-not $degradedSince) { $degradedSince = Get-Date }
            if (((Get-Date) - $degradedSince).TotalSeconds -ge $DegradedGraceSec) { break }
        } else { $degradedSince = $null }
        Start-Sleep -Seconds 3
    }
    return [pscustomobject]@{ State = $state; WebOk = $webOk }
}

if (-not $HealthOnly) {
    if ($Build) {
        Write-Host "building the :local staging images from this tree..."
        Invoke-Compose build api web
        if ($LASTEXITCODE -ne 0) { Write-Host "STAGING FAILED: image build failed"; exit 1 }
    }

    Write-Host "starting the staging data services..."
    Invoke-Compose up -d --wait --wait-timeout $TimeoutSec postgres redis minio temporal
    if ($LASTEXITCODE -ne 0) { Write-Host "STAGING FAILED: data services did not become healthy"; exit 4 }

    Write-Host "running the migrations (alembic upgrade head) on staging's own database..."
    $migrate = Invoke-Compose run --rm --no-deps --entrypoint uv api run alembic upgrade head 2>&1
    $migrateRc = $LASTEXITCODE
    $migrate | Select-Object -Last 3 | ForEach-Object { Write-Host "  $_" }
    if ($migrateRc -ne 0) {
        # Moving back to an older image: the database is at a revision that image does not know.
        # The schema is expand-only (the release's rule), so the older code runs on it as is.
        if ((($migrate | Out-String) -match "Can't locate revision")) {
            Write-Host "migration: the database is ahead of this image (expand-only schema) - kept as is"
        } else {
            Write-Host "STAGING FAILED: migration exited $migrateRc"
            exit 5
        }
    }

    Invoke-Compose up -d --no-build api web
    if ($LASTEXITCODE -ne 0) { Write-Host "STAGING FAILED: api/web did not start"; exit 1 }
}

$result = Wait-Healthy
if ($result.State.StartsWith("degraded")) {
    Write-Host "api is $($result.State) - recreating the api once (its embedded worker does not retry)"
    Invoke-Compose up -d --no-build --force-recreate api
    if ($LASTEXITCODE -ne 0) { Write-Host "STAGING FAILED: could not recreate the api"; exit 1 }
    $result = Wait-Healthy
    if ($result.State -ne "ok") {
        Write-Host "STAGING FAILED: api still $($result.State) after recreating it"
        exit 1
    }
}
if (-not ($result.State -eq "ok" -and $result.WebOk)) {
    Write-Host "STAGING FAILED: not healthy within ${TimeoutSec}s (api $($result.State), web=$($result.WebOk))"
    exit 4
}
$health = Invoke-WebRequest -UseBasicParsing -Uri $ApiHealthUrl -TimeoutSec 5
Write-Host "api health: $($health.Content)"
Write-Host "STAGING UP: $WebUrl (api $($ApiHealthUrl -replace '/v1/system/health$', ''))"
exit 0
