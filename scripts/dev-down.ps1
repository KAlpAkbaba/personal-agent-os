# dev-down.ps1 — stop the local development infrastructure.
# Keeps named volumes by default; -Purge also removes volumes (data loss, dev only).
# Windows PowerShell 5.1 compatible; never relies on PATH for docker.

param(
  [switch]$Purge
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $repoRoot "infra\docker\docker-compose.dev.yml"

function Resolve-Tool {
  param([string]$Name, [string[]]$Fallbacks)
  $c = Get-Command $Name -ErrorAction SilentlyContinue
  if ($c -and $c.Source) { return $c.Source }
  foreach ($f in $Fallbacks) {
    $expanded = [Environment]::ExpandEnvironmentVariables($f)
    if (Test-Path $expanded) { return $expanded }
  }
  return $null
}

$docker = Resolve-Tool "docker" @("C:\Program Files\Docker\Docker\resources\bin\docker.exe")
if (-not $docker) { Write-Error "docker not found"; exit 1 }
if (-not (Test-Path $composeFile)) { Write-Error "compose file not found: $composeFile"; exit 1 }

if ($Purge) {
  Write-Host "Stopping pagentos dev stack and REMOVING volumes..."
  & $docker compose -f $composeFile down -v
} else {
  Write-Host "Stopping pagentos dev stack (volumes preserved)..."
  & $docker compose -f $composeFile down
}
if ($LASTEXITCODE -ne 0) { Write-Error "docker compose down failed"; exit 1 }
Write-Host "Done."
exit 0
