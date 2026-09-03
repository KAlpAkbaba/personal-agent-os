# e2e-m13-research.ps1 — M13 local real-chain proof (dev topology, real Chrome, live Internet).
#
# Flow: dev stack -> alembic -> API with the embedded Temporal worker -> enroll a fresh agent
# identity with browser support -> DeviceService + SessionCompanion + Browser Worker (real
# Google Chrome, dedicated profile) -> device advertises browser.chrome -> device selection
# -> POST /v1/research on the first owner use case -> discovery through APIs and the real
# browser's search -> every source fetched through Chrome -> report -> artifact -> memory ->
# assertions on provenance -> recovery (Cloud Core restart mid-job, no duplicate evidence).
# Exit 0 only if every step passes. Windows PowerShell 5.1.
#
# This proves the browser/research chain against the REAL browser and the LIVE Internet,
# but against the local dev Cloud Core, so QUALIFICATION.md records it as PROVEN_PROXY for
# the cloud leg. The real leg (Hetzner -> Tailscale -> installed agent) is the owner's run.
#
# Runs against an isolated temp agent data dir and a temp identity root; never touches the
# installed agent, its state, or the owner's Chrome profile.
param(
  [int]$ApiPort = 0,
  [switch]$SkipBuild,
  [switch]$KeepData,
  [string]$Topic = "",
  # auto = best configured synthesis provider (an OpenAI key in the environment makes it
  # real); deterministic = offline synthesis, still a real browser/evidence run.
  [ValidateSet("auto", "deterministic")][string]$Synthesis = "auto",
  [int]$MaxSources = 12,
  [int]$ResearchTimeoutSec = 900,
  [switch]$SkipRecovery,
  [string]$OutDir = ""
)

$ErrorActionPreference = "Continue"
$repoRoot = Split-Path -Parent $PSScriptRoot
$apiRoot = Join-Path $repoRoot "services\api"
$browserRoot = Join-Path $repoRoot "services\browser"
$agentRoot = Join-Path $repoRoot "devices\windows-agent"

if (-not $Topic) {
  # Built from code points so the file's own encoding can never corrupt the Turkish topic.
  $Topic = [string]::Join("", @(
      "Son ", [char]0x00FC, [char]0x00E7, " g", [char]0x00FC, "ndeki yapay zek", [char]0x00E2,
      " ajanlar", [char]0x0131, "yla ilgili ", [char]0x00F6, "nemli geli", [char]0x015F, "meleri ara",
      [char]0x015F, "t", [char]0x0131, "r."))
}

if ($ApiPort -eq 0) {
  $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
  $listener.Start()
  $ApiPort = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
  $listener.Stop()
}
$baseUrl = "http://127.0.0.1:$ApiPort"
Write-Host "E2E broker port: $ApiPort"
if (-not $OutDir) { $OutDir = Join-Path $env:TEMP ("pagentos-e2e-m13-" + (Get-Date -Format "yyyyMMdd-HHmmss")) }
New-Item -ItemType Directory -Force $OutDir | Out-Null

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
$env:MSBUILDDISABLENODEREUSE = "1"

$results = New-Object System.Collections.ArrayList
$failed = $false
$procs = @{}
$dataDir = Join-Path $env:TEMP ("pagentos-e2e-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$identityDir = Join-Path $env:TEMP ("pagentos-e2e-identity-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$browserDataDir = Join-Path $dataDir "browser"
# The harness owns a database of its own inside the dev Postgres container: the shared
# `pagentos` dev database is truncated and re-migrated by the integration suites, which
# once wiped a real run mid-fetch (tasks and runs gone, the status endpoint answering 500).
# Same credentials as the dev default in app/config.py (a dev-only, non-secret value).
$E2eDb = "pagentos_e2e_m13"
$e2eDbUrl = "postgresql+psycopg://pagentos:pagentos-dev@127.0.0.1:15432/$E2eDb"
$script:ownerHeaders = @{}
$script:deviceId = $null
$pipeName = "pagentos-e2e-" + (Split-Path $dataDir -Leaf)

. (Join-Path $PSScriptRoot "lib\NativeProcess.ps1")
. (Join-Path $PSScriptRoot "lib\HttpJson.ps1")

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

function Remove-ProtectedTree {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return }
  try {
    $security = New-Object System.Security.AccessControl.DirectorySecurity
    $security.SetAccessRuleProtection($false, $false)
    ([System.IO.DirectoryInfo]$Path).SetAccessControl($security)
  } catch { }
  foreach ($child in @(Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue)) {
    if ($child.PSIsContainer) { Remove-ProtectedTree -Path $child.FullName }
  }
  Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
}

function Wait-PortFree {
  param([int]$Port, [int]$TimeoutSec = 20)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    $busy = $false
    try {
      $probe = New-Object System.Net.Sockets.TcpClient
      $async = $probe.BeginConnect("127.0.0.1", $Port, $null, $null)
      if ($async.AsyncWaitHandle.WaitOne(300) -and $probe.Connected) { $busy = $true }
      $probe.Close()
    } catch { $busy = $false }
    if (-not $busy) { return }
    Start-Sleep -Milliseconds 400
  }
  throw "port $Port still in use after ${TimeoutSec}s"
}

function Get-Json {
  param([string]$Path, [int]$TimeoutSec = 10)
  return Invoke-JsonUtf8 -Uri "$baseUrl$Path" -Headers $script:ownerHeaders -TimeoutSec $TimeoutSec
}

function Send-Json {
  param([string]$Path, $Body, [ValidateSet("POST", "PATCH")][string]$Method = "POST", [int]$TimeoutSec = 20, [hashtable]$ExtraHeaders = @{})
  $headers = @{}
  foreach ($k in $script:ownerHeaders.Keys) { $headers[$k] = $script:ownerHeaders[$k] }
  foreach ($k in $ExtraHeaders.Keys) { $headers[$k] = $ExtraHeaders[$k] }
  $json = if ($Body -is [string]) { $Body } else { $Body | ConvertTo-Json -Depth 8 -Compress }
  return Invoke-JsonUtf8 -Method $Method -Uri "$baseUrl$Path" -Headers $headers -Body $json -TimeoutSec $TimeoutSec
}

function Post-Json {
  param([string]$Path, $Body, [int]$TimeoutSec = 20)
  return Send-Json -Path $Path -Body $Body -Method POST -TimeoutSec $TimeoutSec
}

function Start-Broker {
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  # Through cmd.exe so the API's own log (uvicorn + structlog) lands in the evidence
  # directory; a 500 seen by the harness is otherwise undiagnosable once cleanup runs.
  $apiLog = Join-Path $OutDir "api.log"
  $psi.FileName = Join-Path $env:SystemRoot "System32\cmd.exe"
  $psi.Arguments = '/d /c ""' + $uv + '" run uvicorn app.main:app --host 127.0.0.1 --port ' + $ApiPort + ' --ws-max-size 262144 >> "' + $apiLog + '" 2>&1"'
  $psi.WorkingDirectory = $apiRoot
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $psi.EnvironmentVariables["PAGENTOS_IDENTITY_ROOT_DIR"] = $identityDir
  $psi.EnvironmentVariables["PAGENTOS_DATABASE_URL"] = $e2eDbUrl
  # The research workflow runs in the API process (ADR-0050 §9).
  $psi.EnvironmentVariables["PAGENTOS_WORKER_MODE"] = "embedded"
  $script:procs["broker"] = [System.Diagnostics.Process]::Start($psi)
}

function Wait-Health {
  param([int]$TimeoutSec = 60)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $r = Invoke-RestMethod -Uri "$baseUrl/v1/system/health" -TimeoutSec 3
      if ($r.status) { return $r }
    } catch { Start-Sleep -Milliseconds 500 }
  }
  throw "API health did not answer within ${TimeoutSec}s"
}

function Initialize-OwnerSession {
  if ($script:ownerHeaders.Count -gt 0) { return }
  $boot = Invoke-RestMethod -Method Post -Uri "$baseUrl/v1/identity/bootstrap" -TimeoutSec 10 -ErrorAction Stop
  $body = @{ owner_credential = $boot.owner_credential; client_kind = "cli"; label = "e2e-m13-research" } | ConvertTo-Json
  $sess = Invoke-RestMethod -Method Post -Uri "$baseUrl/v1/identity/sessions" -Body $body -ContentType "application/json" -TimeoutSec 10 -ErrorAction Stop
  $script:ownerHeaders = @{ Authorization = "Bearer $($sess.token)" }
}

function Get-WorkerPython {
  $py = Join-Path $browserRoot ".venv\Scripts\python.exe"
  if (-not (Test-Path $py)) { throw "browser worker venv missing: $py (run '<uv> sync' in services\browser)" }
  return $py
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
  $psi.EnvironmentVariables["PAGENTOS_AGENT_PipeName"] = $pipeName
  $psi.EnvironmentVariables["PAGENTOS_AGENT_MachineMaterialMode"] = "developer"
  $psi.EnvironmentVariables["PAGENTOS_AGENT_BrowserEnabled"] = "true"
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
  $psi.EnvironmentVariables["PAGENTOS_AGENT_PipeName"] = $pipeName
  $psi.EnvironmentVariables["PAGENTOS_AGENT_ServiceTrustMode"] = "developer"
  $psi.EnvironmentVariables["PAGENTOS_AGENT_DataDir"] = (Join-Path $dataDir "companion")
  $psi.EnvironmentVariables["PAGENTOS_AGENT_BrowserWorkerCommand"] = (Get-WorkerPython)
  $psi.EnvironmentVariables["PAGENTOS_AGENT_BrowserWorkerArgs"] = "-m browser_agent.worker"
  $psi.EnvironmentVariables["PAGENTOS_AGENT_BrowserDataDir"] = $browserDataDir
  $psi.EnvironmentVariables["PAGENTOS_AGENT_BrowserChannel"] = "chrome"
  $psi.EnvironmentVariables["PAGENTOS_AGENT_BrowserVisible"] = "true"
  $psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8"
  $psi.EnvironmentVariables["DOTNET_ROOT"] = $env:DOTNET_ROOT
  $psi.WorkingDirectory = $browserRoot
  $script:procs["companion"] = [System.Diagnostics.Process]::Start($psi)
}

function Wait-DeviceOnline {
  param([int]$TimeoutSec = 60, [switch]$RequireBrowser)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $resp = Get-Json "/v1/devices" -TimeoutSec 3
      $dev = $resp.devices | Where-Object { $_.device_id -eq $script:deviceId }
      if ($dev) {
        $online = ($dev.status -eq "online") -or ((Get-OptionalProperty -InputObject $dev -Name "presence") -eq "online")
        $caps = @(Get-OptionalProperty -InputObject $dev -Name "capabilities")
        if ($online -and ((-not $RequireBrowser) -or ($caps -contains "browser.chrome"))) { return $dev }
      }
    } catch {}
    Start-Sleep -Milliseconds 500
  }
  throw "device $script:deviceId not online$(if ($RequireBrowser) { ' with browser.chrome' }) within ${TimeoutSec}s"
}

function Wait-Research {
  param([string]$TaskId, [int]$TimeoutSec)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  $lastStage = ""
  while ((Get-Date) -lt $deadline) {
    $r = Get-Json "/v1/research/$TaskId" -TimeoutSec 10
    $stage = [string](Get-OptionalProperty -InputObject $r -Name "stage")
    $progress = Get-OptionalProperty -InputObject $r -Name "progress"
    $line = "  stage=$stage"
    if ($null -ne $progress) {
      $line += ("  discovered={0} fetched={1}/{2} failed={3} evidence={4}" -f
        (Get-OptionalProperty -InputObject $progress -Name "discovered"),
        (Get-OptionalProperty -InputObject $progress -Name "fetch_done"),
        (Get-OptionalProperty -InputObject $progress -Name "fetch_total"),
        (Get-OptionalProperty -InputObject $progress -Name "fetch_failed"),
        (Get-OptionalProperty -InputObject $progress -Name "evidence"))
    }
    if ($line -ne $lastStage) { Write-Host $line; $lastStage = $line }
    if ($stage -in @("ready", "failed", "cancelled")) { return $r }
    Start-Sleep -Seconds 3
  }
  throw "research $TaskId not terminal within ${TimeoutSec}s"
}

function Assert-Report {
  param($Research, [string]$Label)
  $report = Get-OptionalProperty -InputObject $Research -Name "report"
  if ($null -eq $report) { throw "no report on the research record" }
  $findings = @(Get-OptionalProperty -InputObject $report -Name "findings")
  $sources = @(Get-OptionalProperty -InputObject $report -Name "sources")
  $summary = [string](Get-OptionalProperty -InputObject $report -Name "executive_summary")
  Write-Host ("  findings={0} sources={1} synthesis={2}" -f $findings.Count, $sources.Count, (Get-OptionalProperty -InputObject $report -Name "synthesis_provider"))
  if (-not $summary) { throw "empty executive summary" }
  if ($findings.Count -gt 7) { throw "more than 7 findings: $($findings.Count)" }
  if ($sources.Count -lt 3) { throw "fewer than 3 sources fetched through the browser: $($sources.Count)" }
  $sourceIds = @{}
  $publishers = @{}
  foreach ($s in $sources) {
    $sourceIds[[string]$s.id] = $true
    $pub = [string](Get-OptionalProperty -InputObject $s -Name "publisher")
    if (-not $pub) { $pub = ([uri]$s.url).Host }
    $publishers[$pub] = $true
    if (-not $s.url -or -not (Get-OptionalProperty -InputObject $s -Name "retrieved_at")) { throw "source $($s.id) lacks url/retrieved_at" }
    if (-not (Get-OptionalProperty -InputObject $s -Name "command_id")) { throw "source $($s.id) lacks the device command provenance" }
  }
  if ($publishers.Count -lt 2) { throw "evidence comes from a single publisher; expected multiple live sources" }
  $facts = 0
  foreach ($f in $findings) {
    if ($f.label -eq "source_fact") {
      $facts++
      $ids = @(Get-OptionalProperty -InputObject $f -Name "evidence_ids")
      if ($ids.Count -eq 0) { throw "source_fact finding '$($f.title)' cites nothing" }
      foreach ($id in $ids) { if (-not $sourceIds.ContainsKey([string]$id)) { throw "finding cites unknown evidence id $id" } }
    }
  }
  $stats = Get-OptionalProperty -InputObject $report -Name "stats"
  if ($null -ne $stats) { Write-Host ("  stats: " + ($stats | ConvertTo-Json -Compress)) }
  $path = Join-Path $OutDir "$Label-report.json"
  [IO.File]::WriteAllText($path, ($report | ConvertTo-Json -Depth 12), (New-Object System.Text.UTF8Encoding($false)))
  Write-Host "  report written: $path"
  return $report
}

# ------------------------------------------------------------------ steps

Invoke-Step "Dev stack + migrations" {
  & $powershell5 -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot "scripts\dev-up.ps1")
  if ($LASTEXITCODE -ne 0) { throw "dev-up failed" }
  $exists = & $docker exec pagentos-postgres psql -U pagentos -d pagentos -t -A -c "SELECT 1 FROM pg_database WHERE datname = '$E2eDb';"
  if (($exists | Out-String).Trim() -ne "1") {
    & $docker exec pagentos-postgres psql -U pagentos -d pagentos -c "CREATE DATABASE $E2eDb;" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "could not create $E2eDb" }
    Write-Host "  created isolated database $E2eDb"
  }
  Push-Location $apiRoot
  try {
    $env:PAGENTOS_DATABASE_URL = $e2eDbUrl
    try { & $uv run alembic upgrade head; if ($LASTEXITCODE -ne 0) { throw "alembic failed" } }
    finally { Remove-Item Env:\PAGENTOS_DATABASE_URL -ErrorAction SilentlyContinue }
  }
  finally { Pop-Location }
}

Invoke-Step "Build Windows agent + browser worker self-check" {
  if (-not $SkipBuild) {
    Push-Location $agentRoot
    try { & $dotnet build PagentOS.WindowsAgent.sln --nologo -v q; if ($LASTEXITCODE -ne 0) { throw "dotnet build failed" } }
    finally { Pop-Location }
    Push-Location $browserRoot
    try { & $uv sync --frozen; if ($LASTEXITCODE -ne 0) { throw "uv sync (browser) failed" } }
    finally { Pop-Location }
  }
  $py = Get-WorkerPython
  Push-Location $browserRoot
  try {
    $hello = & $py -m browser_agent.worker --self-check --channel chrome --data-dir $browserDataDir 2>&1
    if ($LASTEXITCODE -ne 0) { throw "worker self-check failed: $hello" }
    Write-Host "  worker hello: $(($hello | Select-Object -Last 1).ToString().Substring(0, [Math]::Min(160, ($hello | Select-Object -Last 1).ToString().Length)))"
  } finally { Pop-Location }
}

Invoke-Step "Start Cloud Core (embedded worker)" {
  Start-Broker
  $health = Wait-Health
  $worker = Get-OptionalProperty -InputObject $health.checks -Name "temporal_worker"
  if ($null -eq $worker -or $worker.status -ne "ok") { throw "embedded Temporal worker not healthy: $($worker | ConvertTo-Json -Compress)" }
  Initialize-OwnerSession
}

Invoke-Step "Enroll fresh device (browser-capable)" {
  New-Item -ItemType Directory -Force $dataDir | Out-Null
  $tok = Post-Json "/v1/devices/enrollment-tokens" "{}"
  $exe = Join-Path $agentRoot "src\PagentOS.DeviceService\bin\Debug\net10.0-windows\PagentOS.DeviceService.exe"
  $env:PAGENTOS_AGENT_DataDir = $dataDir
  $env:PAGENTOS_AGENT_BrokerRestUrl = $baseUrl
  $env:PAGENTOS_AGENT_MachineMaterialMode = "developer"
  $env:PAGENTOS_AGENT_BrowserEnabled = "true"
  & $exe enroll --broker-url $baseUrl --token $tok.token --name "e2e-research-pc"
  if ($LASTEXITCODE -ne 0) { throw "enroll exited $LASTEXITCODE" }
  Remove-Item Env:\PAGENTOS_AGENT_DataDir, Env:\PAGENTOS_AGENT_BrokerRestUrl, Env:\PAGENTOS_AGENT_MachineMaterialMode, Env:\PAGENTOS_AGENT_BrowserEnabled -ErrorAction SilentlyContinue
  $state = Get-Content (Join-Path $dataDir "state.json") -Raw | ConvertFrom-Json
  $script:deviceId = $state.device_id
  if (-not $script:deviceId) { throw "device_id not found in agent state" }
  Write-Host "enrolled device_id=$script:deviceId"
}

Invoke-Step "Start agent service + companion; device online with browser.chrome" {
  Start-AgentService
  Start-Companion
  $dev = Wait-DeviceOnline -RequireBrowser
  Write-Host ("  capabilities: " + ((@(Get-OptionalProperty -InputObject $dev -Name "capabilities")) -join ", "))
}

Invoke-Step "Device selection (auto and explicit Turkish alias)" {
  # Devices enrolled by earlier runs persist in the harness database and may still hold
  # the alias; aliases are unique across enrolled devices (409 alias_conflict), so retire
  # every device that is not the one this run enrolled.
  $listing = Get-Json "/v1/devices"
  foreach ($d in @($listing.devices)) {
    if ([string]$d.device_id -ne [string]$script:deviceId -and $d.status -ne "revoked") {
      Post-Json "/v1/devices/$($d.device_id)/revoke" "{}" | Out-Null
      Write-Host "  revoked stale harness device $($d.device_id)"
    }
  }
  Send-Json -Path "/v1/devices/$($script:deviceId)" -Method PATCH -Body @{ aliases = @("ev") } | Out-Null
  $sel = Post-Json "/v1/devices/select" @{ capability = "browser.chrome" }
  if ([string](Get-OptionalProperty -InputObject $sel -Name "device_id") -ne [string]$script:deviceId) { throw "auto selection did not pick the enrolled device: $($sel | ConvertTo-Json -Compress)" }
  $target = [string]::Join("", @("ev bilgisayar", [char]0x0131, "mda ara", [char]0x015F, "t", [char]0x0131, "r"))
  $sel2 = Post-Json "/v1/devices/select" @{ capability = "browser.chrome"; target = $target }
  if ([string](Get-OptionalProperty -InputObject $sel2 -Name "device_id") -ne [string]$script:deviceId) { throw "explicit Turkish target did not resolve: $($sel2 | ConvertTo-Json -Compress)" }
  Write-Host "  selection ok (auto + '$target')"
}

Invoke-Step "Real research: first owner use case through Chrome + live Internet" {
  $started = Post-Json "/v1/research" @{ input = $Topic; synthesis = $Synthesis; max_sources = $MaxSources }
  $script:taskId = [string]$started.task_id
  Write-Host "  task_id=$script:taskId device=$((Get-OptionalProperty -InputObject $started -Name 'device') | ConvertTo-Json -Compress)"
  $r = Wait-Research -TaskId $script:taskId -TimeoutSec $ResearchTimeoutSec
  if ($r.stage -ne "ready") { throw "research ended $($r.stage): $((Get-OptionalProperty -InputObject $r -Name 'error') | ConvertTo-Json -Compress)" }
  $script:report1 = Assert-Report -Research $r -Label "run1"
  $script:artifactId = [string](Get-OptionalProperty -InputObject $r -Name "artifact_id")
  $script:memoryId = [string](Get-OptionalProperty -InputObject $r -Name "memory_id")
  if (-not $script:artifactId) { throw "no artifact_id on the ready research" }
  if (-not $script:memoryId) { throw "no memory_id on the ready research" }
}

Invoke-Step "Artifact (renders, citations) and memory (provenance, no raw page text)" {
  $art = Get-Json "/v1/artifacts/$($script:artifactId)"
  $renders = @(Get-OptionalProperty -InputObject $art -Name "available_renders")
  Write-Host ("  artifact state={0} renders={1}" -f $art.state, (($renders | ForEach-Object { $_.format }) -join ","))
  if (-not (Get-OptionalProperty -InputObject $art -Name "executive_summary")) { throw "artifact has no executive summary" }
  $canon = Invoke-WebRequest -Uri "$baseUrl/v1/artifacts/$($script:artifactId)/canonical" -Headers $script:ownerHeaders -TimeoutSec 20 -UseBasicParsing
  # PS 5.1 hands back a string for text/* responses and bytes otherwise.
  $body = if ($canon.Content -is [byte[]]) { [System.Text.Encoding]::UTF8.GetString($canon.Content) } else { [string]$canon.Content }
  [IO.File]::WriteAllText((Join-Path $OutDir "run1-canonical.md"), $body, (New-Object System.Text.UTF8Encoding($false)))
  if ($body -notmatch "\[e\d+\]") { throw "canonical body carries no [eN] citation markers" }
  $mem = Get-Json "/v1/memory/$($script:memoryId)"
  $memJson = $mem | ConvertTo-Json -Depth 12 -Compress
  if ($memJson -notmatch "research:") { throw "memory entry is not keyed as a research episode" }
  $longest = 0
  foreach ($s in @($script:report1.sources)) { if ($s.excerpt -and $s.excerpt.Length -gt $longest) { $longest = $s.excerpt.Length } }
  if ($memJson.Length -gt 0 -and $longest -gt 0) {
    foreach ($s in @($script:report1.sources)) {
      if ($s.excerpt -and $s.excerpt.Length -ge 200 -and $memJson.Contains($s.excerpt.Substring(0, 200))) { throw "raw excerpt text leaked into memory" }
    }
  }
  Write-Host "  memory ok: $script:memoryId"
}

Invoke-Step "Recovery: Cloud Core restart mid-job, no duplicate evidence" {
  if ($SkipRecovery) { Write-Host "skipped by flag"; return }
  $started = Post-Json "/v1/research" @{ input = $Topic; synthesis = "deterministic"; max_sources = 6 }
  $tid = [string]$started.task_id
  # Wait until it is fetching, then kill the API (and its embedded worker) hard.
  $deadline = (Get-Date).AddSeconds(240)
  do {
    Start-Sleep -Seconds 2
    $r = Get-Json "/v1/research/$tid" -TimeoutSec 10
  } while ((Get-Date) -lt $deadline -and $r.stage -notin @("fetching", "ranking", "synthesizing", "persisting", "ready", "failed"))
  if ($r.stage -notin @("fetching")) { Write-Host "  note: job reached '$($r.stage)' before the restart; restart still exercised" }
  & C:\Windows\System32\taskkill.exe /PID $procs["broker"].Id /T /F 2>$null | Out-Null
  Wait-PortFree -Port $ApiPort
  Start-Broker
  Wait-Health | Out-Null
  Wait-DeviceOnline -TimeoutSec 90 -RequireBrowser | Out-Null
  $r = Wait-Research -TaskId $tid -TimeoutSec $ResearchTimeoutSec
  if ($r.stage -ne "ready") { throw "recovered research ended $($r.stage)" }
  $rep = Assert-Report -Research $r -Label "run2-recovered"
  $urls = @($rep.sources | ForEach-Object { $_.url })
  if ($urls.Count -ne @($urls | Select-Object -Unique).Count) { throw "duplicate evidence after recovery" }
  $sql = "SELECT count(*) - count(DISTINCT url) FROM research_evidence WHERE task_id = '$tid';"
  $dups = & $docker exec pagentos-postgres psql -U pagentos -d $E2eDb -t -A -c $sql
  if ($LASTEXITCODE -eq 0 -and [int]($dups | Select-Object -Last 1) -ne 0) { throw "duplicate research_evidence rows: $dups" }
  Write-Host "  recovered without duplicate evidence"
}

# ---------------------------------------------------------------- cleanup

Write-Host ""
Write-Host "=== Cleanup ===" -ForegroundColor Cyan
foreach ($name in @("companion", "service", "broker")) {
  $p = $procs[$name]
  if ($p -and -not $p.HasExited) {
    & C:\Windows\System32\taskkill.exe /PID $p.Id /T /F 2>$null | Out-Null
  }
}
if ($KeepData) {
  Write-Host "keeping agent data for inspection: $dataDir"
} else {
  Remove-ProtectedTree -Path $dataDir
  Remove-Item -LiteralPath $identityDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
$results | Format-Table -AutoSize | Out-String | Write-Host
Write-Host "evidence directory: $OutDir"
if ($failed) { Write-Host "E2E M13 RESULT: FAIL" -ForegroundColor Red; exit 1 }
Write-Host "E2E M13 RESULT: PASS" -ForegroundColor Green
exit 0
