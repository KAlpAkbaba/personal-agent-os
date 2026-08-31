# dev-up.ps1 — start the local development infrastructure (compose project "pagentos")
# and wait until every service is healthy/reachable.
# Windows PowerShell 5.1 compatible; never relies on PATH for docker.

param(
  [int]$TimeoutSec = 180
)

# "Continue": docker compose reports progress on stderr; with "Stop" and
# redirected output PS 5.1 turns those lines into terminating errors.
# Failures are handled via explicit exit-code checks.
$ErrorActionPreference = "Continue"
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

Write-Host "Starting pagentos dev stack..."
& $docker compose -f $composeFile up -d
if ($LASTEXITCODE -ne 0) { Write-Error "docker compose up failed"; exit 1 }

function Test-TcpPort {
  param([string]$TargetHost, [int]$Port, [int]$TimeoutMs = 2000)
  try {
    $client = New-Object System.Net.Sockets.TcpClient
    $async = $client.BeginConnect($TargetHost, $Port, $null, $null)
    $ok = $async.AsyncWaitHandle.WaitOne($TimeoutMs)
    if ($ok -and $client.Connected) { $client.Close(); return $true }
    $client.Close(); return $false
  } catch { return $false }
}

function Get-ContainerHealth {
  param([string]$Name)
  $out = & $docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" $Name 2>$null
  if ($LASTEXITCODE -ne 0) { return "missing" }
  return ("$out").Trim()
}

$deadline = (Get-Date).AddSeconds($TimeoutSec)
$healthTargets = @("pagentos-postgres", "pagentos-redis", "pagentos-minio")
$tcpTargets = @(
  @{ Name = "temporal-frontend"; Port = 17233 },
  @{ Name = "temporal-ui";       Port = 18233 }
)

Write-Host "Waiting for services to become healthy (timeout ${TimeoutSec}s)..."
while ($true) {
  $pending = @()
  foreach ($name in $healthTargets) {
    $h = Get-ContainerHealth -Name $name
    if ($h -ne "healthy") { $pending += "$name($h)" }
  }
  foreach ($t in $tcpTargets) {
    if (-not (Test-TcpPort -TargetHost "127.0.0.1" -Port $t.Port)) { $pending += "$($t.Name)(:$($t.Port))" }
  }
  if ($pending.Count -eq 0) { break }
  if ((Get-Date) -gt $deadline) {
    Write-Error ("Timed out waiting for: " + ($pending -join ", "))
    exit 1
  }
  Write-Host ("  waiting: " + ($pending -join ", "))
  Start-Sleep -Seconds 3
}

Write-Host ""
Write-Host "pagentos dev stack is up:"
Write-Host "  PostgreSQL+pgvector  127.0.0.1:15432 (user pagentos / db pagentos)"
Write-Host "  Redis                127.0.0.1:16379"
Write-Host "  MinIO S3 API         http://127.0.0.1:19000 (console http://127.0.0.1:19001)"
Write-Host "  Temporal gRPC        127.0.0.1:17233"
Write-Host "  Temporal Web UI      http://127.0.0.1:18233"
exit 0
