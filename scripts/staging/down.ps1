<#
.SYNOPSIS
    Stops the STAGING stack (compose project pagentos-staging). Its data is kept unless
    -Wipe is given.
.DESCRIPTION
    Only ever acts on the staging compose file and project: the dev stack and production are
    never named here. -Wipe removes the pagentos-staging-* volumes too (the test data, the
    staging identity root); the next up.ps1 + seed.ps1 starts from a clean state.
    Exit codes: 0 stopped; 1 docker/compose failure.
.EXAMPLE
    powershell -NoProfile -File scripts\staging\down.ps1
    powershell -NoProfile -File scripts\staging\down.ps1 -Wipe
#>
[CmdletBinding()]
param(
    [switch]$Wipe
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$composeFile = Join-Path $repoRoot "infra\docker\docker-compose.staging.yml"
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
$docker = if ($dockerCmd) { $dockerCmd.Source } else { "C:\Program Files\Docker\Docker\resources\bin\docker.exe" }
if (-not (Test-Path $docker)) { Write-Host "STAGING FAILED: docker not found"; exit 1 }

$downArgs = @("compose", "-f", $composeFile, "-p", "pagentos-staging", "down", "--remove-orphans")
if ($Wipe) { $downArgs += "--volumes" }
& $docker @downArgs
if ($LASTEXITCODE -ne 0) { Write-Host "STAGING FAILED: compose down exited $LASTEXITCODE"; exit 1 }
if ($Wipe) { Write-Host "STAGING DOWN: stopped, staging volumes removed" } else { Write-Host "STAGING DOWN: stopped, data kept" }
exit 0
