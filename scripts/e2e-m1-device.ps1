# e2e-m1-device.ps1 — M1 deterministic end-to-end acceptance test.
#
# Flow: dev stack -> alembic -> API/broker (uvicorn) -> enroll fresh agent
# identity -> DeviceService + SessionCompanion -> open Notepad command ->
# ack + audit verification -> broker restart recovery -> agent restart
# recovery. Exit 0 only if every step passes. Windows PowerShell 5.1.
#
# Runs against an isolated temp agent data dir; never touches a real
# enrolled agent state.

param(
  [int]$ApiPort = 8001,
  [switch]$SkipBuild
)

# "Continue", not "Stop": docker/compose/dotnet write progress to stderr and
# PS 5.1 would otherwise convert those lines into terminating errors. Steps
# fail via explicit exit-code checks and throws; cmdlet calls that must fail
# loudly carry -ErrorAction Stop.
$ErrorActionPreference = "Continue"
$repoRoot = Split-Path -Parent $PSScriptRoot
$apiRoot = Join-Path $repoRoot "services\api"
$agentRoot = Join-Path $repoRoot "devices\windows-agent"
$baseUrl = "http://127.0.0.1:$ApiPort"

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

$uv = Resolve-Tool "uv" @("%USERPROFILE%\.local\bin\uv.exe", "%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe")
# The user-scope install carries the SDK; the machine-wide one is runtime-only,
# so probe the user-scope location BEFORE falling back to PATH.
$dotnet = $null
foreach ($cand in @("$env:LOCALAPPDATA\Microsoft\dotnet\dotnet.exe", "C:\Program Files\dotnet\dotnet.exe")) {
  if (Test-Path $cand) {
    $sdks = & $cand --list-sdks 2>$null
    if ($LASTEXITCODE -eq 0 -and $sdks) { $dotnet = $cand; break }
  }
}
$docker = Resolve-Tool "docker" @("C:\Program Files\Docker\Docker\resources\bin\docker.exe")
$powershell5 = Resolve-Tool "powershell" @("C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
if (-not $uv) { Write-Error "uv not found"; exit 1 }
if (-not $dotnet) { Write-Error "dotnet (with SDK) not found"; exit 1 }
$env:DOTNET_ROOT = Split-Path -Parent $dotnet
$env:MSBUILDDISABLENODEREUSE = "1"   # no lingering build daemons holding handles

$results = New-Object System.Collections.ArrayList
$failed = $false
$procs = @{}   # name -> Process
$dataDir = Join-Path $env:TEMP ("pagentos-e2e-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$notepadPids = New-Object System.Collections.ArrayList

function Invoke-Step {
  param([string]$Name, [scriptblock]$Action)
  if ($script:failed) {
    [void]$script:results.Add([pscustomobject]@{ Step = $Name; Result = "SKIP"; Seconds = 0 })
    return
  }
  Write-Host ""
  Write-Host "=== $Name ===" -ForegroundColor Cyan
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  $ok = $false
  try { & $Action; $ok = $true }
  catch { Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red }
  $sw.Stop()
  [void]$script:results.Add([pscustomobject]@{
    Step = $Name
    Result = $(if ($ok) { "PASS" } else { "FAIL" })
    Seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1)
  })
  if (-not $ok) { $script:failed = $true }
}

function Start-Broker {
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $uv
  # --ws-max-size: cap WS frames well above any legal protocol frame
  # (defense in depth; uvicorn default is 16 MiB).
  $psi.Arguments = "run uvicorn app.main:app --host 127.0.0.1 --port $ApiPort --ws-max-size 65536"
  $psi.WorkingDirectory = $apiRoot
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $script:procs["broker"] = [System.Diagnostics.Process]::Start($psi)
}

function Wait-Health {
  param([int]$TimeoutSec = 40)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $r = Invoke-RestMethod -Uri "$baseUrl/v1/system/health" -TimeoutSec 3
      if ($r.status) { return }
    } catch { Start-Sleep -Milliseconds 500 }
  }
  throw "API health did not answer within ${TimeoutSec}s"
}

function Start-AgentService {
  $exe = Join-Path $agentRoot "src\PagentOS.DeviceService\bin\Debug\net10.0-windows\PagentOS.DeviceService.exe"
  if (-not (Test-Path $exe)) { throw "DeviceService.exe not built: $exe" }
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $exe
  $psi.Arguments = "run"
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $psi.EnvironmentVariables["PAGENTOS_AGENT_DataDir"] = $dataDir
  $psi.EnvironmentVariables["PAGENTOS_AGENT_BrokerRestUrl"] = $baseUrl
  $psi.EnvironmentVariables["PAGENTOS_AGENT_BrokerWsUrl"] = "ws://127.0.0.1:$ApiPort/v1/devices/connect"
  $psi.EnvironmentVariables["PAGENTOS_AGENT_PipeName"] = "pagentos-e2e-" + (Split-Path $dataDir -Leaf)
  $psi.EnvironmentVariables["PAGENTOS_AGENT_BackoffBaseSeconds"] = "1"
  $psi.EnvironmentVariables["PAGENTOS_AGENT_BackoffMaxSeconds"] = "3"
  $psi.EnvironmentVariables["DOTNET_ROOT"] = $env:DOTNET_ROOT
  $script:procs["service"] = [System.Diagnostics.Process]::Start($psi)
}

function Start-Companion {
  $exe = Join-Path $agentRoot "src\PagentOS.SessionCompanion\bin\Debug\net10.0-windows\PagentOS.SessionCompanion.exe"
  if (-not (Test-Path $exe)) { throw "SessionCompanion.exe not built: $exe" }
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $exe
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $psi.EnvironmentVariables["PAGENTOS_AGENT_PipeName"] = "pagentos-e2e-" + (Split-Path $dataDir -Leaf)
  $psi.EnvironmentVariables["DOTNET_ROOT"] = $env:DOTNET_ROOT
  $script:procs["companion"] = [System.Diagnostics.Process]::Start($psi)
}

function Wait-DeviceOnline {
  param([int]$TimeoutSec = 45)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $resp = Invoke-RestMethod -Uri "$baseUrl/v1/devices" -TimeoutSec 3 -ErrorAction Stop
      $dev = $resp.devices | Where-Object { $_.device_id -eq $script:deviceId }
      if ($dev -and $dev.status -eq "online") { return }
    } catch {}
    Start-Sleep -Milliseconds 500
  }
  throw "device $script:deviceId not online within ${TimeoutSec}s"
}

function Send-OpenNotepad {
  param([string]$TraceId)
  $body = @{
    capability = "desktop.open_application"
    payload = @{ application = "notepad" }
    idempotency_key = [guid]::NewGuid().ToString()
    timeout_s = 30
  } | ConvertTo-Json
  $headers = @{ "X-Trace-Id" = $TraceId }
  $resp = Invoke-RestMethod -Method Post -Uri "$baseUrl/v1/devices/$($script:deviceId)/commands" -Body $body -ContentType "application/json" -Headers $headers -TimeoutSec 10 -ErrorAction Stop
  return $resp.command_id
}

function Wait-CommandSucceeded {
  param([string]$CommandId, [int]$TimeoutSec = 30)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    $cmd = Invoke-RestMethod -Uri "$baseUrl/v1/devices/$($script:deviceId)/commands/$CommandId" -TimeoutSec 5 -ErrorAction Stop
    if ($cmd.status -eq "succeeded") { return $cmd }
    if ($cmd.status -in @("failed", "expired", "cancelled")) {
      throw "command terminal but not succeeded: $($cmd.status) $($cmd.error.class) $($cmd.error.message)"
    }
    Start-Sleep -Milliseconds 400
  }
  throw "command $CommandId not succeeded within ${TimeoutSec}s"
}

function Assert-NotepadRunning {
  param($Cmd)
  $procId = $Cmd.result.pid
  if (-not $procId) { throw "no pid in command result" }
  $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
  if (-not $p) { throw "no process with pid $procId" }
  if ($p.ProcessName -notmatch "(?i)notepad") { throw "pid $procId is '$($p.ProcessName)', not notepad" }
  [void]$script:notepadPids.Add($procId)
  Write-Host "Notepad running, pid=$procId"
}

# ------------------------------------------------------------------ steps

Invoke-Step "Dev stack + migrations" {
  & $powershell5 -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot "scripts\dev-up.ps1")
  if ($LASTEXITCODE -ne 0) { throw "dev-up failed" }
  Push-Location $apiRoot
  try { & $uv run alembic upgrade head; if ($LASTEXITCODE -ne 0) { throw "alembic failed" } }
  finally { Pop-Location }
}

Invoke-Step "Build Windows agent" {
  if ($SkipBuild) { Write-Host "skipped by flag"; return }
  Push-Location $agentRoot
  try { & $dotnet build PagentOS.WindowsAgent.sln --nologo -v q; if ($LASTEXITCODE -ne 0) { throw "dotnet build failed" } }
  finally { Pop-Location }
}

Invoke-Step "Start broker (uvicorn)" {
  Start-Broker
  Wait-Health
}

Invoke-Step "Enroll fresh device" {
  New-Item -ItemType Directory -Force $dataDir | Out-Null
  $tok = Invoke-RestMethod -Method Post -Uri "$baseUrl/v1/devices/enrollment-tokens" -TimeoutSec 10 -ErrorAction Stop
  $exe = Join-Path $agentRoot "src\PagentOS.DeviceService\bin\Debug\net10.0-windows\PagentOS.DeviceService.exe"
  $env:PAGENTOS_AGENT_DataDir = $dataDir
  $env:PAGENTOS_AGENT_BrokerRestUrl = $baseUrl
  & $exe enroll --broker-url $baseUrl --token $tok.token --name "e2e-test-pc"
  if ($LASTEXITCODE -ne 0) { throw "enroll exited $LASTEXITCODE" }
  Remove-Item Env:\PAGENTOS_AGENT_DataDir, Env:\PAGENTOS_AGENT_BrokerRestUrl -ErrorAction SilentlyContinue
  $state = Get-Content (Join-Path $dataDir "state.json") -Raw | ConvertFrom-Json
  $script:deviceId = $state.device_id
  if (-not $script:deviceId) { throw "device_id not found in agent state" }
  Write-Host "enrolled device_id=$script:deviceId"
}

Invoke-Step "Start agent service + companion, device online" {
  Start-AgentService
  Start-Companion
  Wait-DeviceOnline
}

Invoke-Step "E2E: open Notepad via command" {
  $script:traceId = "e2e-m1-" + [guid]::NewGuid().ToString("N")
  $cid = Send-OpenNotepad -TraceId $script:traceId
  $cmd = Wait-CommandSucceeded -CommandId $cid
  Assert-NotepadRunning $cmd
  $script:firstCommandId = $cid
}

Invoke-Step "Audit trail (broker DB + agent JSONL)" {
  $sql = "SELECT action FROM audit_events WHERE command_id = '$($script:firstCommandId)' ORDER BY id;"
  $actions = & $docker exec pagentos-postgres psql -U pagentos -d pagentos -t -A -c $sql
  if ($LASTEXITCODE -ne 0) { throw "psql query failed" }
  Write-Host "broker audit actions: $($actions -join ', ')"
  foreach ($needed in @("command_created", "command_delivered")) {
    if (-not ($actions -match $needed)) { throw "missing broker audit action: $needed" }
  }
  if (-not ($actions -match "succeeded" -or $actions -match "ack")) { throw "no ack/succeeded audit action" }
  $traceRows = & $docker exec pagentos-postgres psql -U pagentos -d pagentos -t -A -c "SELECT count(*) FROM audit_events WHERE command_id = '$($script:firstCommandId)' AND trace_id = '$($script:traceId)';"
  if ([int]($traceRows | Select-Object -First 1) -lt 1) { throw "trace_id not propagated into audit" }
  $auditFile = Get-ChildItem $dataDir -Recurse -Filter "*.jsonl" | Select-Object -First 1
  if (-not $auditFile) { throw "agent JSONL audit log not found under $dataDir" }
  $agentAudit = Get-Content $auditFile.FullName -Raw
  if ($agentAudit -notmatch $script:firstCommandId) { throw "command_id missing from agent audit log" }
  Write-Host "agent audit log OK: $($auditFile.FullName)"
}

Invoke-Step "Recovery: broker restart" {
  & C:\Windows\System32\taskkill.exe /PID $procs["broker"].Id /T /F 2>$null | Out-Null
  Start-Sleep -Seconds 2
  Start-Broker
  Wait-Health
  Wait-DeviceOnline -TimeoutSec 60   # agent must reconnect on its own
  $cid = Send-OpenNotepad -TraceId ("e2e-m1-recovery1-" + [guid]::NewGuid().ToString("N"))
  $cmd = Wait-CommandSucceeded -CommandId $cid
  Assert-NotepadRunning $cmd
}

Invoke-Step "Recovery: agent restart" {
  $procs["service"].Kill()
  Start-Sleep -Seconds 2
  Start-AgentService
  Wait-DeviceOnline -TimeoutSec 60
  $cid = Send-OpenNotepad -TraceId ("e2e-m1-recovery2-" + [guid]::NewGuid().ToString("N"))
  $cmd = Wait-CommandSucceeded -CommandId $cid
  Assert-NotepadRunning $cmd
}

# ---------------------------------------------------------------- cleanup

Write-Host ""
Write-Host "=== Cleanup ===" -ForegroundColor Cyan
foreach ($procId in $notepadPids) {
  Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}
# Kill full process trees: `uv run uvicorn` spawns python children that would
# otherwise be orphaned and keep port 8001 (and this console's pipe) open.
foreach ($name in @("companion", "service", "broker")) {
  $p = $procs[$name]
  if ($p -and -not $p.HasExited) {
    & C:\Windows\System32\taskkill.exe /PID $p.Id /T /F 2>$null | Out-Null
  }
}
Remove-Item -Recurse -Force $dataDir -ErrorAction SilentlyContinue

# ---------------------------------------------------------------- summary

Write-Host ""
Write-Host "=== M1 device E2E summary ===" -ForegroundColor Cyan
$results | Format-Table -AutoSize | Out-String | Write-Host

if ($failed) {
  Write-Host "M1 DEVICE E2E: FAIL" -ForegroundColor Red
  exit 1
}
Write-Host "M1 DEVICE E2E: PASS" -ForegroundColor Green
exit 0
