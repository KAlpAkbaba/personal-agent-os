<#
.SYNOPSIS
    Starts the STAGING stack (compose project pagentos-staging) on the home PC, runs the
    migrations and waits until the api and the web shell answer.
.DESCRIPTION
    Staging is a copy of the real server for the test team (team/plans ADR "staging-stack"):
    the same api and web images, its OWN Postgres, Temporal namespace + task queue, Redis,
    MinIO bucket and identities. It shares nothing with the dev stack the gate resets and
    cannot reach production (services/api/tests/unit/test_staging_isolation.py).

    Memory: refuses to start when less than 6 GB of physical memory is free (exit 3) - the
    home PC also runs the dev stack, gates and agents (2026-10-03: several whole-suite runs
    exhausted its 48 GB). The measured footprint is in the ADR.

    The images are whatever PAGENTOS_STAGING_RELEASE_TAG names (deploy.ps1 sets it to the
    sha it built); unset = `:local`, which -Build builds from this tree.

    Exit codes: 0 up and healthy; 1 docker/compose failure; 3 not enough free memory;
    4 not healthy within -TimeoutSec; 5 migration failed.
.PARAMETER Build
    Build the `:local` images from this tree first (`docker compose build`).
.PARAMETER AssumeFreeMB
    For tests only: use this as the free-memory figure instead of measuring it.
.PARAMETER CheckOnly
    Run the refusals (memory, docker) and stop before starting anything.
.EXAMPLE
    powershell -NoProfile -File scripts\staging\up.ps1 -Build
#>
[CmdletBinding()]
param(
    [int]$TimeoutSec = 300,
    [switch]$Build,
    [int]$AssumeFreeMB = -1,
    [switch]$CheckOnly
)
Set-StrictMode -Version Latest
# "Continue": docker compose reports progress on stderr; failures are exit-code checks.
$ErrorActionPreference = "Continue"

$MinFreeMB = 6144
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$composeFile = Join-Path $repoRoot "infra\docker\docker-compose.staging.yml"
$webUrl = "http://127.0.0.1:28000/"
$apiHealth = "http://127.0.0.1:28001/v1/system/health"

function Get-FreeMemoryMB {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    return [int][math]::Floor([double]$os.FreePhysicalMemory / 1024)
}

$freeMB = if ($AssumeFreeMB -ge 0) { $AssumeFreeMB } else { Get-FreeMemoryMB }
if ($freeMB -lt $MinFreeMB) {
    Write-Host "STAGING REFUSED: only $freeMB MB of memory is free; staging needs at least $MinFreeMB MB free to start (a gate or agents may be running - ask the test-slot queue for 'heavy', or try later)."
    exit 3
}
Write-Host "memory: $freeMB MB free (minimum $MinFreeMB MB) - ok"

$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
$docker = if ($dockerCmd) { $dockerCmd.Source } else { "C:\Program Files\Docker\Docker\resources\bin\docker.exe" }
if (-not (Test-Path $docker)) { Write-Host "STAGING FAILED: docker not found"; exit 1 }
if (-not (Test-Path $composeFile)) { Write-Host "STAGING FAILED: compose file not found: $composeFile"; exit 1 }
if ($CheckOnly) { Write-Host "check only: nothing started"; exit 0 }

function Invoke-Compose {
    & $docker compose -f $composeFile @args
}

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

$deadline = (Get-Date).AddSeconds($TimeoutSec)
$apiOk = $false
$webOk = $false
while ((Get-Date) -lt $deadline -and -not ($apiOk -and $webOk)) {
    if (-not $apiOk) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri $apiHealth -TimeoutSec 5
            $apiOk = ($r.StatusCode -eq 200)
        } catch { $apiOk = $false }
    }
    if (-not $webOk) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -Uri $webUrl -TimeoutSec 5
            $webOk = ($r.StatusCode -eq 200)
        } catch { $webOk = $false }
    }
    if (-not ($apiOk -and $webOk)) { Start-Sleep -Seconds 3 }
}
if (-not ($apiOk -and $webOk)) {
    Write-Host "STAGING FAILED: not healthy within ${TimeoutSec}s (api=$apiOk web=$webOk)"
    exit 4
}
$health = Invoke-WebRequest -UseBasicParsing -Uri $apiHealth -TimeoutSec 5
Write-Host "api health: $($health.Content)"
Write-Host "STAGING UP: $webUrl (api http://127.0.0.1:28001)"
exit 0
