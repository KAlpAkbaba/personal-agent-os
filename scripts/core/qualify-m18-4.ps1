<#
.SYNOPSIS
    M18.4 production qualification (owner directive 2026-09-07 evening): the first real
    blue/green Cloud Core cutover, measured; a controlled-failure rollback; an explicit
    rollback to last-known-good and the roll-forward; the Evolution Supervisor and the
    owner's pause/resume on the real Cloud Core; the deployed-version reconciliation.
    Evidence: docs/evidence/m18-4-qualification-<stamp>.json.

.DESCRIPTION
    Nothing here needs the owner's ears or hands. It does need the host reachable over
    Tailscale SSH (the release driver ships the tree over ssh/scp); -WaitForHost polls
    until that is true and then runs unattended.

    Phases (each measured by a health prober hitting the canonical edge every 250 ms,
    logging every request's outcome; a "gap" is the span from the first failed probe to
    the next successful one; "dropped" is the number of failed probes):

      A  snapshot BEFORE (health, release, devices, alarms, routines, incidents,
         opportunities, ledger summary)
      B  FIRST CUTOVER: release-cloud-core.ps1 -BlueGreen at HEAD (legacy api -> edge +
         blue). This one has a gap by design (the edge must take the socket); measured.
      C  CONTROLLED-FAILURE ROLLBACK: the host script is run for the SAME sha with the
         post-switch verification pointed at an unreachable URL; the edge switches to the
         idle colour, the verification fails, the script switches back. Measured: expect
         zero dropped probes (the old colour was up throughout).
      D  EXPLICIT ROLLBACK: release-cloud-core-bluegreen.sh --rollback brings the other
         colour up and switches to it, then the roll-forward is a normal blue/green
         release of HEAD again. Measured: expect zero dropped probes on both.
      E  SUPERVISOR + PAUSE/RESUME on the real Cloud Core: scan (real production signals),
         pause -> scan skipped_paused -> resume -> scan; the ledger rows behind each.
      F  RECONCILIATION: health.release, contracts, /edge/active, RELEASE and
         LAST_KNOWN_GOOD on the host, devices' reported versions, the web marker as the
         server knows it, the SLO report.

    Requires: the repo at HEAD with a clean tree (the driver ships HEAD), the DPAPI-stored
    owner credential (PAGENTOS_OWNER_CREDENTIAL), Tailscale up.
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "100.90.158.26",
    [int]$ApiPort = 8001,
    [string]$SshHost = "pagentos-core",
    [switch]$WaitForHost,
    [int]$WaitForHostMinutes = 240,
    [switch]$SkipControlledFailure,
    [switch]$SkipExplicitRollback,
    [string]$EvidenceDir = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
. (Join-Path $repoRoot "scripts\lib\OwnerHarness.ps1")
$ssh = Join-Path $env:SystemRoot "System32\OpenSSH\ssh.exe"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$BaseUrl = "http://${BrokerHost}:$ApiPort"
if (-not $EvidenceDir) { $EvidenceDir = Join-Path $repoRoot "docs\evidence" }
$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd-HHmmss")
$evidencePath = Join-Path $EvidenceDir "m18-4-qualification-$stamp.json"
$proberDir = Join-Path $env:TEMP "pagentos-m18-4-prober-$stamp"
New-Item -ItemType Directory -Force -Path $proberDir, $EvidenceDir | Out-Null

$evidence = [ordered]@{
    run_id      = "m18-4-$stamp"
    started_at  = (Get-Date).ToUniversalTime().ToString("o")
    cloud       = $BaseUrl
    head_sha    = (& git -C $repoRoot rev-parse HEAD).Trim()
    phases      = [ordered]@{}
    measurements = [ordered]@{}
    checks      = @()
    verdict     = "INCOMPLETE"
}

function Add-Check {
    param([string]$Name, [bool]$Pass, [string]$Detail = "")
    $script:evidence.checks += [ordered]@{ name = $Name; pass = $Pass; detail = $Detail }
    $tag = if ($Pass) { "PASS" } else { "FAIL" }
    Write-Host ("  {0}  {1}{2}" -f $tag, $Name, $(if ($Detail) { " - $Detail" } else { "" }))
}

function Save-Evidence {
    $script:evidence.updated_at = (Get-Date).ToUniversalTime().ToString("o")
    [IO.File]::WriteAllText($evidencePath, (($script:evidence | ConvertTo-Json -Depth 12) + "`n"), [Text.UTF8Encoding]::new($false))
}

# ------------------------------------------------------------------ host access

function Test-HostSsh {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $ssh -n -o BatchMode=yes -o ConnectTimeout=15 "root@$SshHost" "echo host-ok" 2>&1 | Out-String
        return ($LASTEXITCODE -eq 0 -and $out -match "host-ok")
    }
    finally { $ErrorActionPreference = $previous }
}

function Invoke-Host {
    param([Parameter(Mandatory = $true)][string]$Command)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $ssh -n -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15 "root@$SshHost" $Command 2>&1 | Out-String
        return [pscustomobject]@{ Exit = $LASTEXITCODE; Output = $out }
    }
    finally { $ErrorActionPreference = $previous }
}

if ($WaitForHost) {
    $deadline = (Get-Date).AddMinutes($WaitForHostMinutes)
    Write-Host "waiting for Tailscale SSH to $SshHost (the owner completes the check once; polling every 60 s until $($deadline.ToString('HH:mm')))..."
    while (-not (Test-HostSsh)) {
        if ((Get-Date) -gt $deadline) { throw "the host did not become reachable over Tailscale SSH within $WaitForHostMinutes minutes" }
        Start-Sleep -Seconds 60
    }
    Write-Host "host reachable."
}
elseif (-not (Test-HostSsh)) {
    throw "root@$SshHost is not reachable over Tailscale SSH (BatchMode). Complete the Tailscale check (ssh root@$SshHost exit) or run with -WaitForHost."
}

# ---------------------------------------------------------------- owner session

$credential = $null
try { $credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL" } catch { $credential = $null }
if (-not $credential) { throw "PAGENTOS_OWNER_CREDENTIAL is not stored (DPAPI); the qualification reads owner-gated routes" }
$loginBody = @{ owner_credential = $credential; client_kind = "cli"; label = $evidence.run_id } | ConvertTo-Json -Compress
$issued = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body $loginBody
$credential = $null; $loginBody = $null
$token = [string]$issued.token
$headers = @{ Authorization = "Bearer $token" }

function Get-Json { param([string]$Path) return Invoke-JsonUtf8 -Uri "$BaseUrl$Path" -Headers $headers -TimeoutSec 30 }
function Get-JsonOrNull { param([string]$Path) try { return Get-Json $Path } catch { return $null } }
function Post-Json { param([string]$Path, $Body) $json = if ($null -eq $Body) { "{}" } else { ($Body | ConvertTo-Json -Depth 8 -Compress) }; return Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl$Path" -Headers $headers -Body $json -TimeoutSec 120 }

function Get-Snapshot {
    $health = Get-JsonOrNull "/v1/system/health"
    $release = if ($null -ne $health) { Get-OptionalProperty -InputObject $health -Name "release" } else { $null }
    $checks = if ($null -ne $health) { Get-OptionalProperty -InputObject $health -Name "checks" } else { $null }
    $vr = if ($null -ne $checks) { Get-OptionalProperty -InputObject $checks -Name "voice_realtime" } else { $null }
    $devices = Get-JsonOrNull "/v1/devices"
    $alarms = Get-JsonOrNull "/v1/alarms"
    $routines = Get-JsonOrNull "/v1/routines"
    $incidents = Get-JsonOrNull "/v1/selfhealing/incidents?limit=50"
    $opportunities = Get-JsonOrNull "/v1/evolution/opportunities?limit=200"
    $ledger = Get-JsonOrNull "/v1/ledger/summary"
    $edgeActive = $null
    try { $edgeActive = (Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/edge/active" -TimeoutSec 10).Content.Trim() } catch { $edgeActive = $null }
    return [ordered]@{
        at                      = (Get-Date).ToUniversalTime().ToString("o")
        health_status           = if ($null -ne $health) { [string](Get-OptionalProperty -InputObject $health -Name "status") } else { $null }
        release                 = $release
        action_contract_version = if ($null -ne $vr) { Get-OptionalProperty -InputObject $vr -Name "action_contract_version" } else { $null }
        realtime_contract       = if ($null -ne $vr) { Get-OptionalProperty -InputObject $vr -Name "contract_version" } else { $null }
        edge_active             = $edgeActive
        devices                 = @(ConvertTo-Array -Value (Get-ArrayProperty -InputObject $devices -Name "devices") | ForEach-Object {
            [ordered]@{
                device_id = [string](Get-OptionalProperty -InputObject $_ -Name "device_id")
                name      = [string](Get-OptionalProperty -InputObject $_ -Name "name")
                presence  = [string]$(if ($null -ne (Get-OptionalProperty -InputObject $_ -Name "presence")) { Get-OptionalProperty -InputObject $_ -Name "presence" } else { Get-OptionalProperty -InputObject $_ -Name "status" })
                version   = [string]$(if ($null -ne (Get-OptionalProperty -InputObject $_ -Name "software_version")) { Get-OptionalProperty -InputObject $_ -Name "software_version" } else { "unknown" })
                capabilities = @(ConvertTo-Array -Value (Get-OptionalProperty -InputObject $_ -Name "capabilities") | ForEach-Object { [string]$_ })
                heartbeat_status = Get-OptionalProperty -InputObject $_ -Name "heartbeat_status"
            } })
        alarm_count             = @(ConvertTo-Array -Value (Get-ArrayProperty -InputObject $alarms -Name "alarms")).Count
        alarm_ids               = @(ConvertTo-Array -Value (Get-ArrayProperty -InputObject $alarms -Name "alarms") | ForEach-Object { [string](Get-OptionalProperty -InputObject $_ -Name "alarm_id") })
        routine_count           = @(ConvertTo-Array -Value (Get-ArrayProperty -InputObject $routines -Name "routines")).Count
        incident_count          = @(ConvertTo-Array -Value (Get-ArrayProperty -InputObject $incidents -Name "incidents")).Count
        opportunity_count       = @(ConvertTo-Array -Value (Get-ArrayProperty -InputObject $opportunities -Name "opportunities")).Count
        ledger_summary          = $ledger
    }
}

# --------------------------------------------------------------------- prober

$proberScript = @'
param([string]$Url, [string]$LogPath, [string]$StopPath)
$client = New-Object System.Net.Http.HttpClient
$client.Timeout = [TimeSpan]::FromSeconds(3)
$sw = [Diagnostics.Stopwatch]::StartNew()
while (-not (Test-Path $StopPath)) {
    $t = (Get-Date).ToUniversalTime().ToString("o")
    $ok = $false; $code = 0; $err = ""
    try {
        $resp = $client.GetAsync($Url).GetAwaiter().GetResult()
        $code = [int]$resp.StatusCode
        $ok = $resp.IsSuccessStatusCode
        $resp.Dispose()
    } catch { $err = $_.Exception.GetType().Name }
    [IO.File]::AppendAllText($LogPath, ("{0}`t{1}`t{2}`t{3}`n" -f $t, $(if ($ok) { "ok" } else { "fail" }), $code, $err))
    Start-Sleep -Milliseconds 250
}
'@
$proberFile = Join-Path $proberDir "prober.ps1"
[IO.File]::WriteAllText($proberFile, $proberScript)

function Start-Prober {
    param([string]$Name)
    $log = Join-Path $proberDir "$Name.tsv"
    $stop = Join-Path $proberDir "$Name.stop"
    Remove-Item -LiteralPath $log, $stop -ErrorAction SilentlyContinue
    $proc = Start-Process -FilePath $powershell -ArgumentList @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $proberFile, "-Url", "$BaseUrl/v1/system/health", "-LogPath", $log, "-StopPath", $stop) -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 2
    return [pscustomobject]@{ Name = $Name; Log = $log; Stop = $stop; Process = $proc }
}

function Stop-Prober {
    param($Prober)
    [IO.File]::WriteAllText($Prober.Stop, "stop")
    $Prober.Process.WaitForExit(15000) | Out-Null
    $rows = @()
    if (Test-Path $Prober.Log) { $rows = @(Get-Content $Prober.Log | Where-Object { $_ }) }
    $total = $rows.Count
    $failed = @($rows | Where-Object { $_ -match "`tfail`t" })
    $gapS = 0.0; $firstFail = $null; $recoveredAt = $null
    if ($failed.Count -gt 0) {
        $firstFail = [datetime]::Parse(($failed[0] -split "`t")[0])
        $after = @($rows | Where-Object { [datetime]::Parse(($_ -split "`t")[0]) -gt $firstFail -and $_ -match "`tok`t" })
        if ($after.Count -gt 0) { $recoveredAt = [datetime]::Parse(($after[0] -split "`t")[0]); $gapS = [math]::Round(($recoveredAt - $firstFail).TotalSeconds, 2) }
        else { $gapS = -1 }
    }
    return [ordered]@{
        phase            = $Prober.Name
        probes           = $total
        dropped          = $failed.Count
        first_failure_at = if ($firstFail) { $firstFail.ToUniversalTime().ToString("o") } else { $null }
        recovered_at     = if ($recoveredAt) { $recoveredAt.ToUniversalTime().ToString("o") } else { $null }
        gap_seconds      = $gapS
        interval_ms      = 250
    }
}

# --------------------------------------------------------------------- phases

try {
    Write-Host "PagentOS M18.4 production qualification ($($evidence.run_id))"
    Write-Host "== A: snapshot before"
    $before = Get-Snapshot
    $evidence.phases.A_before = $before
    Add-Check "the Cloud Core answers before the run" ($before.health_status -eq "ok") "release=$(if ($before.release) { $before.release.version } else { 'none (pre-M18.4 build)' }) contract=$($before.action_contract_version)"
    Save-Evidence

    Write-Host "== B: first blue/green cutover (release-cloud-core.ps1 -BlueGreen at HEAD)"
    $prober = Start-Prober -Name "B_first_cutover"
    $releaseStart = Get-Date
    $release = Join-Path $repoRoot "scripts\cloud\release-cloud-core.ps1"
    $previous = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $releaseOut = & $powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $release -BlueGreen 2>&1 | Out-String
    $releaseExit = $LASTEXITCODE
    $ErrorActionPreference = $previous
    $releaseEnd = Get-Date
    Start-Sleep -Seconds 5
    $mB = Stop-Prober $prober
    $evidence.measurements.B_first_cutover = $mB
    $evidence.phases.B_release = [ordered]@{ exit = $releaseExit; seconds = [math]::Round(($releaseEnd - $releaseStart).TotalSeconds, 1); output_tail = (($releaseOut -split "`n") | Select-Object -Last 25) -join "`n" }
    Add-Check "first cutover exits 0" ($releaseExit -eq 0) "release took $([math]::Round(($releaseEnd - $releaseStart).TotalSeconds, 1)) s"
    Add-Check "first cutover measured" $true "probes=$($mB.probes) dropped=$($mB.dropped) gap=$($mB.gap_seconds)s (a gap is expected on the FIRST cutover: the edge takes the socket)"
    $afterB = Get-Snapshot
    $evidence.phases.B_after = $afterB
    Add-Check "release reports HEAD" ($afterB.release -and $afterB.release.version -eq $evidence.head_sha) "served=$(if ($afterB.release) { $afterB.release.version } else { 'none' })"
    Add-Check "edge names a colour" ($afterB.edge_active -in @("blue", "green")) "active=$($afterB.edge_active)"
    Add-Check "alarms unchanged" ($afterB.alarm_count -eq $before.alarm_count) "before=$($before.alarm_count) after=$($afterB.alarm_count)"
    Add-Check "routines unchanged" ($afterB.routine_count -eq $before.routine_count) "before=$($before.routine_count) after=$($afterB.routine_count)"
    $hostState = Invoke-Host "cat /opt/pagentos/RELEASE; echo; cat /opt/pagentos/LAST_KNOWN_GOOD 2>/dev/null; echo; cat /mnt/pagentos-data/edge/active.txt 2>/dev/null; echo; docker ps --format '{{.Names}} {{.Status}}'"
    $evidence.phases.B_host = $hostState.Output
    Save-Evidence

    # the device reconnects on its own (Stage 5 proved reconnect); give it a minute
    $deviceBack = $false; $waited = 0
    while (-not $deviceBack -and $waited -lt 90) {
        $snap = Get-Snapshot
        $online = @($snap.devices | Where-Object { $_.presence -eq "online" })
        if ($online.Count -gt 0) { $deviceBack = $true; break }
        Start-Sleep -Seconds 5; $waited += 5
    }
    Add-Check "the Windows agent is online again through the edge" $deviceBack "after ${waited}s"
    Save-Evidence

    if (-not $SkipControlledFailure) {
        Write-Host "== C: controlled-failure rollback (post-switch verification pointed at an unreachable URL)"
        $prober = Start-Prober -Name "C_controlled_failure"
        $c = Invoke-Host "set -e; cd /opt/pagentos; rm -rf app.next; cp -a app app.next; PAGENTOS_HEALTH_URL=http://127.0.0.1:8001/v1/system/health-does-not-exist PAGENTOS_DRAIN_S=5 bash app.next/scripts/cloud/release-cloud-core-bluegreen.sh $($evidence.head_sha) 2>&1; echo EXIT=`$?"
        Start-Sleep -Seconds 5
        $mC = Stop-Prober $prober
        $evidence.measurements.C_controlled_failure = $mC
        $evidence.phases.C_output_tail = (($c.Output -split "`n") | Select-Object -Last 25) -join "`n"
        Add-Check "the controlled failure rolled back" ($c.Output -match "ROLLBACK: switching the edge back") "the script switched, failed its own verification, switched back"
        $afterC = Get-Snapshot
        Add-Check "after the rollback the edge serves HEAD on the original colour" ($afterC.release -and $afterC.release.version -eq $evidence.head_sha -and $afterC.edge_active -eq $afterB.edge_active) "active=$($afterC.edge_active)"
        Add-Check "controlled failure: dropped probes" ($mC.dropped -eq 0) "probes=$($mC.probes) dropped=$($mC.dropped) gap=$($mC.gap_seconds)s"
        Save-Evidence
    }

    if (-not $SkipExplicitRollback) {
        Write-Host "== D: explicit rollback to the other colour, then the roll-forward"
        $prober = Start-Prober -Name "D_explicit_rollback"
        $d = Invoke-Host "cd /opt/pagentos && PAGENTOS_WAIT_STEP_S=3 bash app/scripts/cloud/release-cloud-core-bluegreen.sh $($evidence.head_sha) --rollback 2>&1; echo EXIT=`$?"
        Start-Sleep -Seconds 5
        $mD1 = Stop-Prober $prober
        $evidence.measurements.D_explicit_rollback = $mD1
        $evidence.phases.D_rollback_output_tail = (($d.Output -split "`n") | Select-Object -Last 15) -join "`n"
        $afterD1 = Get-Snapshot
        Add-Check "explicit rollback switched the colour" ($d.Output -match "ROLLBACK OK" -and $afterD1.edge_active -ne $afterB.edge_active) "active=$($afterD1.edge_active) served=$(if ($afterD1.release) { $afterD1.release.version } else { 'none' })"
        Add-Check "explicit rollback: dropped probes" ($mD1.dropped -eq 0) "probes=$($mD1.probes) dropped=$($mD1.dropped) gap=$($mD1.gap_seconds)s"

        $prober = Start-Prober -Name "D_roll_forward"
        $f = Invoke-Host "set -e; cd /opt/pagentos; rm -rf app.next; cp -a app app.next; PAGENTOS_DRAIN_S=20 bash app.next/scripts/cloud/release-cloud-core-bluegreen.sh $($evidence.head_sha) 2>&1; echo EXIT=`$?"
        Start-Sleep -Seconds 5
        $mD2 = Stop-Prober $prober
        $evidence.measurements.D_roll_forward = $mD2
        $evidence.phases.D_forward_output_tail = (($f.Output -split "`n") | Select-Object -Last 15) -join "`n"
        $afterD2 = Get-Snapshot
        Add-Check "roll-forward landed HEAD on the other colour with no gap" ($f.Output -match "RELEASE OK" -and $afterD2.release -and $afterD2.release.version -eq $evidence.head_sha -and $mD2.dropped -eq 0) "active=$($afterD2.edge_active) probes=$($mD2.probes) dropped=$($mD2.dropped) gap=$($mD2.gap_seconds)s"
        Save-Evidence
    }

    Write-Host "== E: supervisor + pause/resume on the real Cloud Core"
    $scan1 = Post-Json "/v1/evolution/supervisor/scan" $null
    $evidence.phases.E_scan_1 = $scan1
    Add-Check "the supervisor scanned production signals" ([string](Get-OptionalProperty -InputObject $scan1 -Name "status") -eq "scanned") "signals=$(Get-OptionalProperty -InputObject $scan1 -Name 'signals') opened=$(@(ConvertTo-Array -Value (Get-OptionalProperty -InputObject $scan1 -Name 'opened')).Count) tracked=$(Get-OptionalProperty -InputObject $scan1 -Name 'already_tracked')"
    $paused = Post-Json "/v1/evolution/supervisor/pause" @{ reason = "m18-4 qualification" }
    $scan2 = Post-Json "/v1/evolution/supervisor/scan" $null
    Add-Check "paused: a scan opens nothing" ((Get-OptionalProperty -InputObject $paused -Name "paused") -eq $true -and [string](Get-OptionalProperty -InputObject $scan2 -Name "status") -eq "skipped_paused") "status=$(Get-OptionalProperty -InputObject $scan2 -Name 'status')"
    $healthPaused = Get-JsonOrNull "/v1/system/health"
    Add-Check "production stays available while paused" ($null -ne $healthPaused -and [string](Get-OptionalProperty -InputObject $healthPaused -Name "status") -eq "ok")
    $resumed = Post-Json "/v1/evolution/supervisor/resume" @{ reason = "m18-4 qualification" }
    $scan3 = Post-Json "/v1/evolution/supervisor/scan" $null
    Add-Check "resumed: the scan runs again" ((Get-OptionalProperty -InputObject $resumed -Name "paused") -eq $false -and [string](Get-OptionalProperty -InputObject $scan3 -Name "status") -eq "scanned")
    $evidence.phases.E_status = Get-JsonOrNull "/v1/evolution/supervisor"
    $ledgerEvo = Get-JsonOrNull "/v1/ledger/events?limit=10&subsystem=evolution"
    $evidence.phases.E_ledger = $ledgerEvo
    Save-Evidence

    Write-Host "== F: reconciliation"
    $final = Get-Snapshot
    $evidence.phases.F_final = $final
    $evidence.phases.F_release_current = Get-JsonOrNull "/v1/release/current"
    $evidence.phases.F_components = Get-JsonOrNull "/v1/release/components"
    $evidence.phases.F_slo = Get-JsonOrNull "/v1/release/slo"
    $evidence.phases.F_host = (Invoke-Host "cat /opt/pagentos/RELEASE; echo; cat /opt/pagentos/LAST_KNOWN_GOOD 2>/dev/null; echo; cat /mnt/pagentos-data/edge/active.txt 2>/dev/null; echo; grep -E '^PAGENTOS_(IMAGE|RELEASE)_(BLUE|GREEN)=|^PAGENTOS_LAST_KNOWN_GOOD=' /opt/pagentos/.env; docker ps --format '{{.Names}} {{.Image}} {{.Status}}'").Output
    Add-Check "final: release == HEAD, contract v12" ($final.release -and $final.release.version -eq $evidence.head_sha -and $final.action_contract_version -eq 12) "served=$(if ($final.release) { $final.release.version } else { 'none' }) contract=$($final.action_contract_version)"
    Add-Check "final: last-known-good exported" ($final.release -and $final.release.last_known_good) "lkg=$(if ($final.release) { $final.release.last_known_good } else { 'none' })"
    $failures = @($evidence.checks | Where-Object { -not $_.pass })
    $evidence.verdict = if ($failures.Count -eq 0) { "PASS" } else { "FAIL" }
    Save-Evidence
    Write-Host ""
    Write-Host "M18.4 QUALIFICATION: $($evidence.verdict) ($($evidence.checks.Count) checks, $($failures.Count) failed) -> $evidencePath"
    if ($failures.Count -gt 0) { exit 1 }
    exit 0
}
catch {
    $evidence.error = $_.Exception.Message
    $evidence.verdict = "ERROR"
    Save-Evidence
    Write-Host "M18.4 QUALIFICATION: ERROR $($_.Exception.Message) -> $evidencePath" -ForegroundColor Red
    exit 2
}
finally {
    $token = $null; $headers = $null
    Get-ChildItem -Path $proberDir -Filter "*.stop" -ErrorAction SilentlyContinue | Out-Null
    foreach ($f in Get-ChildItem -Path $proberDir -Filter "*.tsv" -ErrorAction SilentlyContinue) { Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $EvidenceDir "m18-4-probes-$stamp-$($f.BaseName).tsv") -ErrorAction SilentlyContinue }
}
