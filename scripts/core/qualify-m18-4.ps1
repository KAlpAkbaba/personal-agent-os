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

    M18.4 final gap closure (owner directive 2026-09-07, "before M19"):

      G  INTERRUPTED PROMOTION RECOVERY (before B): HEAD is staged as a real candidate
         (release-cloud-core.ps1 -StageOnly) and the host script is SIGKILLed at a chosen
         point (PAGENTOS_INTERRUPT_AT=after_idle_up: both colours up, edge untouched; then
         after_switch: the edge already on the candidate, RELEASE not yet written); after
         each crash `--reconcile` must bring the last COMPLETED promotion back, stop the
         half-promoted candidate and restore the tree - never make the candidate live.
         Measured by both probers.
      H  the boot-time reconcile unit (infra/systemd/pagentos-bluegreen-reconcile.service)
         is installed and enabled on the host and run once on the consistent host after B:
         RECONCILE OK from the unit's own journal. The reboot itself is NOT performed
         (production; no autonomous reboot) - that part stays PROVEN_PROXY.

    Every switch is now also measured by a DEVICE prober (GET /v1/devices every second with
    the owner session): the window in which no device is online is the device-presence
    gap. The release script hands the device sessions to the new colour (device upstream,
    drain, wait) BEFORE the new colour takes HTTP; the expected window is the agent's own
    reconnect (~1-2 s), never the old 1-3 minute presence gap.

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
    # The first cutover happened already (it stops the legacy container; not repeatable):
    # measure the repeatable phases only, against whatever colour is active now.
    [switch]$SkipFirstCutover,
    # Gap closure phases (G: interrupted promotions + reconcile; H: the systemd unit).
    [switch]$SkipInterruption,
    [switch]$SkipSystemdUnit,
    [int]$DeviceGapMaxSeconds = 15,
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

function Get-DeviceRows {
    # Assign first, then iterate: a `return , @(...)` piped straight into ForEach-Object
    # arrives as ONE item (the whole array) and every field reads empty - which is exactly
    # what the first real run recorded (PS 5.1; see scripts/tests/owner-harness.tests.ps1).
    param($Doc)
    # A BARE call, assigned: `@(Get-ArrayProperty ...)` wraps the returned array as ONE
    # element (measured on the live listing: count 1, type Object[]); the bare assignment
    # receives the elements. The first two real runs recorded empty device rows for this.
    $rows = Get-ArrayProperty -InputObject $Doc -Name "devices"
    $out = @()
    foreach ($d in $rows) {
        if ($null -eq $d) { continue }
        $presence = Get-OptionalProperty -InputObject $d -Name "presence"
        if ($null -eq $presence) { $presence = Get-OptionalProperty -InputObject $d -Name "status" }
        $version = Get-OptionalProperty -InputObject $d -Name "software_version"
        $caps = Get-ArrayProperty -InputObject $d -Name "capabilities"
        $out += [ordered]@{
            device_id        = [string](Get-OptionalProperty -InputObject $d -Name "device_id")
            name             = [string](Get-OptionalProperty -InputObject $d -Name "name")
            presence         = [string]$presence
            version          = [string]$(if ($null -ne $version) { $version } else { "unknown" })
            capability_count = $caps.Count
            last_seen_at     = [string](Get-OptionalProperty -InputObject $d -Name "last_seen_at")
        }
    }
    return , $out
}

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
        devices                 = (Get-DeviceRows $devices)
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
# HttpWebRequest, not HttpClient: Windows PowerShell 5.1 does not load System.Net.Http by
# default, and a null client made every probe a RuntimeException on the first real run.
[Net.ServicePointManager]::DefaultConnectionLimit = 8
while (-not (Test-Path $StopPath)) {
    $t = (Get-Date).ToUniversalTime().ToString("o")
    $ok = $false; $code = 0; $err = ""
    try {
        $req = [Net.HttpWebRequest]::Create($Url)
        $req.Method = "GET"
        $req.Timeout = 3000
        $req.ReadWriteTimeout = 3000
        $req.KeepAlive = $false
        $resp = $req.GetResponse()
        $code = [int]$resp.StatusCode
        $ok = ($code -ge 200 -and $code -lt 300)
        $resp.Close()
    } catch [Net.WebException] {
        $err = "WebException:" + $_.Exception.Status
        if ($null -ne $_.Exception.Response) { try { $code = [int]$_.Exception.Response.StatusCode } catch { } }
    } catch { $err = $_.Exception.GetType().Name }
    [IO.File]::AppendAllText($LogPath, ("{0}`t{1}`t{2}`t{3}`n" -f $t, $(if ($ok) { "ok" } else { "fail" }), $code, $err))
    Start-Sleep -Milliseconds 250
}
'@
$proberFile = Join-Path $proberDir "prober.ps1"
[IO.File]::WriteAllText($proberFile, $proberScript)

# The device prober: is at least one device online, once a second, through the edge with
# the owner session (the token reaches the child through its environment, never a file or
# an argument). Logs "<t>\t<online>\t<total>\t<error>".
$deviceProberScript = @'
param([string]$Url, [string]$LogPath, [string]$StopPath)
$token = $env:PAGENTOS_QUAL_TOKEN
while (-not (Test-Path $StopPath)) {
    $t = (Get-Date).ToUniversalTime().ToString("o")
    $online = -1; $total = -1; $err = ""
    try {
        $req = [Net.HttpWebRequest]::Create($Url)
        $req.Method = "GET"
        $req.Timeout = 3000
        $req.ReadWriteTimeout = 3000
        $req.KeepAlive = $false
        $req.Headers.Add("Authorization", "Bearer $token")
        $resp = $req.GetResponse()
        $reader = New-Object IO.StreamReader($resp.GetResponseStream())
        $body = $reader.ReadToEnd(); $reader.Close(); $resp.Close()
        $doc = $body | ConvertFrom-Json
        $rows = @()
        if ($null -ne $doc -and $null -ne $doc.PSObject.Properties["devices"]) { $rows = @($doc.devices) }
        $total = $rows.Count
        $online = @($rows | Where-Object { $null -ne $_ -and (($null -ne $_.PSObject.Properties["presence"] -and $_.presence -eq "online") -or ($null -ne $_.PSObject.Properties["status"] -and $_.status -eq "online")) }).Count
    } catch [Net.WebException] {
        $err = "WebException:" + $_.Exception.Status
    } catch { $err = $_.Exception.GetType().Name }
    [IO.File]::AppendAllText($LogPath, ("{0}`t{1}`t{2}`t{3}`n" -f $t, $online, $total, $err))
    Start-Sleep -Seconds 1
}
'@
$deviceProberFile = Join-Path $proberDir "device-prober.ps1"
[IO.File]::WriteAllText($deviceProberFile, $deviceProberScript)

function Start-Prober {
    param([string]$Name)
    $log = Join-Path $proberDir "$Name.tsv"
    $stop = Join-Path $proberDir "$Name.stop"
    $deviceLog = Join-Path $proberDir "$Name.devices.tsv"
    Remove-Item -LiteralPath $log, $stop, $deviceLog -ErrorAction SilentlyContinue
    $proc = Start-Process -FilePath $powershell -ArgumentList @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $proberFile, "-Url", "$BaseUrl/v1/system/health", "-LogPath", $log, "-StopPath", $stop) -PassThru -WindowStyle Hidden
    $env:PAGENTOS_QUAL_TOKEN = $token
    try {
        $deviceProc = Start-Process -FilePath $powershell -ArgumentList @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $deviceProberFile, "-Url", "$BaseUrl/v1/devices", "-LogPath", $deviceLog, "-StopPath", $stop) -PassThru -WindowStyle Hidden
    }
    finally { $env:PAGENTOS_QUAL_TOKEN = $null }
    Start-Sleep -Seconds 3
    return [pscustomobject]@{ Name = $Name; Log = $log; Stop = $stop; Process = $proc; DeviceLog = $deviceLog; DeviceProcess = $deviceProc }
}

function Measure-DeviceLog {
    # The device-presence gap: from the first poll that saw NO device online to the next
    # poll that saw one. Polls that could not be answered at all count as "unknown", not
    # as offline (the HTTP prober measures availability; this one measures presence).
    param([string]$Path)
    $rows = @()
    if (Test-Path $Path) { $rows = @(Get-Content $Path | Where-Object { $_ }) }
    $polls = $rows.Count
    $offline = @($rows | Where-Object { ($_ -split "`t")[1] -eq "0" })
    $unknown = @($rows | Where-Object { ($_ -split "`t")[1] -eq "-1" })
    $window = 0.0; $firstOffline = $null; $backAt = $null
    if ($offline.Count -gt 0) {
        $firstOffline = [datetime]::Parse(($offline[0] -split "`t")[0])
        $after = @($rows | Where-Object { [datetime]::Parse(($_ -split "`t")[0]) -gt $firstOffline -and [int](($_ -split "`t")[1]) -ge 1 })
        if ($after.Count -gt 0) { $backAt = [datetime]::Parse(($after[0] -split "`t")[0]); $window = [math]::Round(($backAt - $firstOffline).TotalSeconds, 2) }
        else { $window = -1 }
    }
    return [ordered]@{
        device_polls            = $polls
        device_offline_polls    = $offline.Count
        device_unknown_polls    = $unknown.Count
        device_first_offline_at = if ($firstOffline) { $firstOffline.ToUniversalTime().ToString("o") } else { $null }
        device_back_at          = if ($backAt) { $backAt.ToUniversalTime().ToString("o") } else { $null }
        device_offline_window_s = $window
        device_interval_s       = 1
    }
}

function Stop-Prober {
    param($Prober)
    [IO.File]::WriteAllText($Prober.Stop, "stop")
    $Prober.Process.WaitForExit(15000) | Out-Null
    $Prober.DeviceProcess.WaitForExit(15000) | Out-Null
    $device = Measure-DeviceLog -Path $Prober.DeviceLog
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
        device_polls            = $device.device_polls
        device_offline_polls    = $device.device_offline_polls
        device_unknown_polls    = $device.device_unknown_polls
        device_first_offline_at = $device.device_first_offline_at
        device_back_at          = $device.device_back_at
        device_offline_window_s = $device.device_offline_window_s
    }
}

function Add-DeviceGapCheck {
    param([string]$Phase, $Measurement)
    $w = [double]$Measurement.device_offline_window_s
    Add-Check "$Phase`: device presence gap" ($Measurement.device_polls -gt 0 -and $w -ge 0 -and $w -le $DeviceGapMaxSeconds) "polls=$($Measurement.device_polls) offline_polls=$($Measurement.device_offline_polls) window=$($w)s (limit $DeviceGapMaxSeconds s; the agent's own reconnect)"
}

function Invoke-StageHead {
    # Ship HEAD to /opt/pagentos/app.next without a host transaction (release-cloud-core.ps1 -StageOnly).
    $release = Join-Path $repoRoot "scripts\cloud\release-cloud-core.ps1"
    $previous = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $out = & $powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $release -BlueGreen -StageOnly 2>&1 | Out-String
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $previous
    return [pscustomobject]@{ Exit = $exit; Output = $out }
}

# --------------------------------------------------------------------- phases

try {
    Write-Host "PagentOS M18.4 production qualification ($($evidence.run_id))"
    Write-Host "== A: snapshot before"
    $before = Get-Snapshot
    $evidence.phases.A_before = $before
    Add-Check "the Cloud Core answers before the run" ($before.health_status -eq "ok") "release=$(if ($before.release) { $before.release.version } else { 'none (pre-M18.4 build)' }) contract=$($before.action_contract_version)"
    Save-Evidence

    $firstCutoverPending = ($null -eq $before.edge_active -or $before.edge_active -notin @("blue", "green"))
    if (-not $SkipInterruption -and -not $firstCutoverPending -and -not $SkipFirstCutover) {
        Write-Host "== G: interrupted promotion recovery (a real candidate, a real SIGKILL, then --reconcile)"
        $activeBefore = $before.edge_active
        $otherColour = if ($activeBefore -eq "blue") { "green" } else { "blue" }
        $servedBefore = if ($before.release) { [string]$before.release.version } else { "" }
        foreach ($point in @("after_idle_up", "after_switch")) {
            $staged = Invoke-StageHead
            Add-Check "G/$point`: HEAD staged as the candidate" ($staged.Exit -eq 0 -and $staged.Output -match "staged: ") (($staged.Output -split "`n") | Where-Object { $_ -match "staged" } | Select-Object -Last 1)
            $prober = Start-Prober -Name "G_${point}_crash"
            $crash = Invoke-Host "cd /opt/pagentos && PAGENTOS_INTERRUPT_AT=$point PAGENTOS_WAIT_STEP_S=3 PAGENTOS_DRAIN_S=5 bash app.next/scripts/cloud/release-cloud-core-bluegreen.sh $($evidence.head_sha) 2>&1; echo EXIT=`$?"
            Start-Sleep -Seconds 3
            $mCrash = Stop-Prober $prober
            $evidence.measurements["G_${point}_crash"] = $mCrash
            $evidence.phases["G_${point}_crash_output_tail"] = (($crash.Output -split "`n") | Select-Object -Last 20) -join "`n"
            $crashed = ($crash.Output -match "INTERRUPT: simulated crash at '$point'")
            $crashExit = if ($crash.Output -match "EXIT=(\d+)") { [int]$Matches[1] } else { -1 }
            Add-Check "G/$point`: the host script was killed mid-promotion" ($crashed -and $crashExit -ne 0) "exit=$crashExit"
            $ps = Invoke-Host "docker ps --format '{{.Names}} {{.Status}}' | grep -E 'api-(blue|green)|edge'; echo; cat /mnt/pagentos-data/edge/active.txt; cat /mnt/pagentos-data/edge/upstream.conf; echo RELEASE=`$(cat /opt/pagentos/RELEASE)"
            $evidence.phases["G_${point}_host_after_crash"] = $ps.Output
            $mid = Get-Snapshot
            $evidence.phases["G_${point}_snapshot_after_crash"] = $mid
            $bothUp = ($ps.Output -match "api-blue" -and $ps.Output -match "api-green")
            $servedMid = if ($mid.release) { [string]$mid.release.version } else { "" }
            if ($point -eq "after_idle_up") {
                Add-Check "G/$point`: both colours run; the edge still serves the previous release" ($bothUp -and $mid.edge_active -eq $activeBefore -and $servedMid -eq $servedBefore) "active=$($mid.edge_active) served=$servedMid"
            }
            else {
                Add-Check "G/$point`: both colours run; the edge already serves the candidate, RELEASE not yet written" ($bothUp -and $mid.edge_active -eq $otherColour -and $servedMid -eq $evidence.head_sha -and $ps.Output -match "RELEASE=$servedBefore") "active=$($mid.edge_active) served=$servedMid"
            }
            $prober = Start-Prober -Name "G_${point}_reconcile"
            $rec = Invoke-Host "cd /opt/pagentos && PAGENTOS_WAIT_STEP_S=3 bash app/scripts/cloud/release-cloud-core-bluegreen.sh --reconcile 2>&1; echo EXIT=`$?"
            Start-Sleep -Seconds 3
            $mRec = Stop-Prober $prober
            $evidence.measurements["G_${point}_reconcile"] = $mRec
            $evidence.phases["G_${point}_reconcile_output"] = (($rec.Output -split "`n") | Select-Object -Last 25) -join "`n"
            $afterRec = Get-Snapshot
            $evidence.phases["G_${point}_snapshot_after_reconcile"] = $afterRec
            $servedAfter = if ($afterRec.release) { [string]$afterRec.release.version } else { "" }
            $hostAfter = Invoke-Host "docker ps --format '{{.Names}} {{.Status}}' | grep -E 'api-(blue|green)'; echo; cat /opt/pagentos/RELEASE; cat /opt/pagentos/app/RELEASE; ls -d /opt/pagentos/app.interrupted 2>/dev/null; cat /opt/pagentos/LAST_RECONCILE 2>/dev/null"
            $evidence.phases["G_${point}_host_after_reconcile"] = $hostAfter.Output
            Add-Check "G/$point`: reconcile made the last COMPLETED promotion canonical" ($rec.Output -match "RECONCILE OK: api-$activeBefore is canonical \(release $servedBefore\)" -and $afterRec.edge_active -eq $activeBefore -and $servedAfter -eq $servedBefore) "active=$($afterRec.edge_active) served=$servedAfter"
            Add-Check "G/$point`: the half-promoted candidate was drained and stopped, never live" ($rec.Output -match "half-promoted candidate; draining and stopping it" -and -not ($hostAfter.Output -match "api-$otherColour")) ""
            Add-Check "G/$point`: the canonical tree is back (app/RELEASE == served)" ($hostAfter.Output -match "app.interrupted" -and ($hostAfter.Output -split "`n" | Where-Object { $_.Trim() -eq $servedBefore }).Count -ge 2) ""
            if ($crash.Output -match "edge RECREATED: its compose definition changed") {
                # the candidate tree changed the edge's compose definition: compose recreated
                # the edge once during this crash run (before the switch) - one bounded gap
                $evidence.phases["G_${point}_edge_recreated"] = $true
                Add-Check "G/$point`: ONE bounded gap (the edge recreation in the crash run), none in the reconcile" ($mCrash.gap_seconds -ge 0 -and $mCrash.gap_seconds -le 10 -and $mRec.dropped -eq 0) "crash: probes=$($mCrash.probes) dropped=$($mCrash.dropped) gap=$($mCrash.gap_seconds)s (edge recreated); reconcile: probes=$($mRec.probes) dropped=$($mRec.dropped) gap=$($mRec.gap_seconds)s"
            }
            else {
                Add-Check "G/$point`: no dropped probes across crash + reconcile" ($mCrash.dropped -eq 0 -and $mRec.dropped -eq 0) "crash: probes=$($mCrash.probes) dropped=$($mCrash.dropped); reconcile: probes=$($mRec.probes) dropped=$($mRec.dropped) gap=$($mRec.gap_seconds)s"
            }
            $deviceBackG = $false; $waitedG = 0
            while (-not $deviceBackG -and $waitedG -lt 60) { if ((@((Get-Snapshot).devices | Where-Object { $_.presence -eq "online" })).Count -gt 0) { $deviceBackG = $true; break }; Start-Sleep -Seconds 5; $waitedG += 5 }
            Add-Check "G/$point`: the device is online on the canonical colour after the reconcile" $deviceBackG "after ${waitedG}s; presence gap during reconcile: $($mRec.device_offline_window_s)s"
            Save-Evidence
        }
    }
    elseif (-not $SkipInterruption) {
        Write-Host "== G: skipped (first cutover pending or -SkipFirstCutover)"
    }

    if ($SkipFirstCutover) {
        Write-Host "== B: first cutover already done (skipped); verifying the current state"
        $afterB = Get-Snapshot
        $evidence.phases.B_release = [ordered]@{ skipped = $true; note = "the first cutover happened in an earlier run; see that run's evidence" }
    }
    else {
    Write-Host "== B: blue/green release of HEAD (release-cloud-core.ps1 -BlueGreen)$(if ($null -eq $before.edge_active) { ' - the FIRST cutover' } else { '' })"
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
    $firstCutover = ($null -eq $before.edge_active -or $before.edge_active -notin @("blue", "green"))
    if ($firstCutover) {
        Add-Check "first cutover measured" ($mB.probes -gt 0 -and $mB.gap_seconds -ge 0) "probes=$($mB.probes) dropped=$($mB.dropped) gap=$($mB.gap_seconds)s (a gap is expected on the FIRST cutover: the edge takes the socket)"
    }
    elseif ($releaseOut -match "edge RECREATED: its compose definition changed") {
        # The edge container's definition changed in this tree (M18.4 gap 1: the edge now
        # runs its configuration from the persistent edge dir). Compose recreated it once,
        # before the switch: one bounded gap, reported as such - not a zero-downtime release.
        $evidence.phases.B_edge_recreated = $true
        Add-Check "blue/green release: ONE gap, the edge recreation (its definition changed in this tree)" ($mB.probes -gt 0 -and $mB.gap_seconds -ge 0 -and $mB.gap_seconds -le 10) "probes=$($mB.probes) dropped=$($mB.dropped) gap=$($mB.gap_seconds)s (limit 10 s; later releases must show 0 dropped)"
        $handoffLine = (($releaseOut -split "`n") | Where-Object { $_ -match "device handoff:" } | Select-Object -First 1)
        Add-Check "release: the device sessions were handed to the new colour BEFORE it took HTTP" ($null -ne $handoffLine -and $handoffLine -match "device handoff: (\d+)/(\d+) after (\d+)s" -and [int]$Matches[1] -ge 1 -and [int]$Matches[1] -ge [int]$Matches[2]) "$(if ($handoffLine) { $handoffLine.Trim() } else { 'no handoff line in the release output' })"
        Add-DeviceGapCheck "release" $mB
    }
    else {
        Add-Check "blue/green release with no gap" ($mB.probes -gt 0 -and $mB.dropped -eq 0) "probes=$($mB.probes) dropped=$($mB.dropped) gap=$($mB.gap_seconds)s"
        $handoffLine = (($releaseOut -split "`n") | Where-Object { $_ -match "device handoff:" } | Select-Object -First 1)
        Add-Check "release: the device sessions were handed to the new colour BEFORE it took HTTP" ($null -ne $handoffLine -and $handoffLine -match "device handoff: (\d+)/(\d+) after (\d+)s" -and [int]$Matches[1] -ge 1 -and [int]$Matches[1] -ge [int]$Matches[2]) "$(if ($handoffLine) { $handoffLine.Trim() } else { 'no handoff line in the release output' })"
        Add-DeviceGapCheck "release" $mB
    }
    $afterB = Get-Snapshot
    }
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
    Add-Check "the Windows agent is online through the edge" $deviceBack "after ${waited}s"
    Save-Evidence

    function Test-DeviceOnline {
        $snap = Get-Snapshot
        return (@($snap.devices | Where-Object { $_.presence -eq "online" }).Count -gt 0)
    }
    function Record-Deployment {
        param([string]$EventType, [string]$Summary, [string]$Ref)
        try {
            Post-Json "/v1/ledger/events" @{
                event_type = $EventType; subsystem = "deployment"; action = "cloud_core_bluegreen"
                factual_summary = $Summary; source_ref = $Ref; result = $evidence.head_sha
                version = "1"; evidence_refs = @()
            } | Out-Null
        } catch { Write-Host "      (ledger row not recorded: $($_.Exception.Message))" }
    }
    if (-not $SkipFirstCutover) { Record-Deployment "deployment.cloud_core.released" "Cloud Core $($evidence.head_sha) canliya alindi (ilk blue/green gecisi, kenar + api-blue)." "bluegreen:first:$($evidence.head_sha):$stamp" }

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
        Add-Check "controlled failure: the device stayed online" (Test-DeviceOnline)
        Add-Check "controlled failure: the rollback took the device sessions back first" ($c.Output -match "ROLLBACK: device sessions returning to api-$($afterB.edge_active): (\d+)/(\d+)") "$(if ($c.Output -match 'ROLLBACK: device sessions returning[^\r\n]*') { $Matches[0] } else { 'no line' })"
        Add-DeviceGapCheck "controlled failure" $mC
        Record-Deployment "deployment.cloud_core.rolled_back" "Kontrollu hata: kenar api-green'e gecti, dogrulama basarisiz oldu, api-blue'ya geri dondu." "bluegreen:controlled-failure:$stamp"
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
        $deviceAfterRollback = $false; $waitedD = 0
        while (-not $deviceAfterRollback -and $waitedD -lt 60) { if (Test-DeviceOnline) { $deviceAfterRollback = $true; break }; Start-Sleep -Seconds 5; $waitedD += 5 }
        Add-Check "explicit rollback: the device is online on the other colour" $deviceAfterRollback "after ${waitedD}s"
        Add-Check "explicit rollback: devices first (the handoff line)" ($d.Output -match "device handoff: (\d+)/(\d+) after (\d+)s" -and [int]$Matches[1] -ge 1) "$(if ($d.Output -match 'device handoff:[^\r\n]*') { $Matches[0] } else { 'no line' })"
        Add-DeviceGapCheck "explicit rollback" $mD1
        Record-Deployment "deployment.cloud_core.rolled_back" "Acik geri alma: kenar diger renge gecti ($($afterD1.edge_active))." "bluegreen:rollback:$stamp"

        $prober = Start-Prober -Name "D_roll_forward"
        $f = Invoke-Host "set -e; cd /opt/pagentos; rm -rf app.next; cp -a app app.next; PAGENTOS_DRAIN_S=20 bash app.next/scripts/cloud/release-cloud-core-bluegreen.sh $($evidence.head_sha) 2>&1; echo EXIT=`$?"
        Start-Sleep -Seconds 5
        $mD2 = Stop-Prober $prober
        $evidence.measurements.D_roll_forward = $mD2
        $evidence.phases.D_forward_output_tail = (($f.Output -split "`n") | Select-Object -Last 15) -join "`n"
        $afterD2 = Get-Snapshot
        Add-Check "roll-forward landed HEAD on the other colour with no gap" ($f.Output -match "RELEASE OK" -and $afterD2.release -and $afterD2.release.version -eq $evidence.head_sha -and $mD2.dropped -eq 0) "active=$($afterD2.edge_active) probes=$($mD2.probes) dropped=$($mD2.dropped) gap=$($mD2.gap_seconds)s"
        $deviceAfterForward = $false; $waitedF = 0
        while (-not $deviceAfterForward -and $waitedF -lt 60) { if (Test-DeviceOnline) { $deviceAfterForward = $true; break }; Start-Sleep -Seconds 5; $waitedF += 5 }
        Add-Check "roll-forward: the device is online on the new colour" $deviceAfterForward "after ${waitedF}s"
        Add-Check "roll-forward: devices first (the handoff line)" ($f.Output -match "device handoff: (\d+)/(\d+) after (\d+)s" -and [int]$Matches[1] -ge 1) "$(if ($f.Output -match 'device handoff:[^\r\n]*') { $Matches[0] } else { 'no line' })"
        Add-DeviceGapCheck "roll-forward" $mD2
        Record-Deployment "deployment.cloud_core.released" "Ileri gecis: Cloud Core $($evidence.head_sha) $($afterD2.edge_active) renginde canli." "bluegreen:forward:$stamp"
        Save-Evidence
    }

    Write-Host "== E: supervisor + pause/resume on the real Cloud Core"
    $scan1 = Post-Json "/v1/evolution/supervisor/scan" $null
    $evidence.phases.E_scan_1 = $scan1
    $opened1 = Get-ArrayProperty -InputObject $scan1 -Name "opened"
    Add-Check "the supervisor scanned production signals" ([string](Get-OptionalProperty -InputObject $scan1 -Name "status") -eq "scanned") "signals=$(Get-OptionalProperty -InputObject $scan1 -Name 'signals') opened=$($opened1.Count) tracked=$(Get-OptionalProperty -InputObject $scan1 -Name 'already_tracked')"
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

    if (-not $SkipSystemdUnit) {
        Write-Host "== H: the boot-time reconcile unit (installed, enabled, run once on the consistent host)"
        $unit = Invoke-Host "set -e; cd /opt/pagentos; test -f app/infra/systemd/pagentos-bluegreen-reconcile.service; install -m 0644 app/infra/systemd/pagentos-bluegreen-reconcile.service /etc/systemd/system/pagentos-bluegreen-reconcile.service; systemctl daemon-reload; systemctl enable pagentos-bluegreen-reconcile.service 2>&1 | tail -1; systemctl start pagentos-bluegreen-reconcile.service; echo START_EXIT=`$?; systemctl is-enabled pagentos-bluegreen-reconcile.service; journalctl -u pagentos-bluegreen-reconcile.service --no-pager -n 25 -o cat"
        $evidence.phases.H_systemd_unit = $unit.Output
        Add-Check "H: the reconcile unit is enabled and ran RECONCILE OK from its journal" ($unit.Output -match "START_EXIT=0" -and $unit.Output -match "(?m)^enabled" -and $unit.Output -match "RECONCILE OK: api-(blue|green) is canonical \(release $($evidence.head_sha)\)") (($unit.Output -split "`n") | Where-Object { $_ -match "RECONCILE OK|enabled|START_EXIT" } | Select-Object -Last 3) -join " | "
        Add-Check "H: the unit's reconcile changed nothing on a consistent host" (-not ($unit.Output -match "half-promoted|starting it from its recorded image|EMERGENCY")) ""
        $afterH = Get-Snapshot
        Add-Check "H: production unchanged after the unit ran" ($afterH.release -and $afterH.release.version -eq $evidence.head_sha -and (@($afterH.devices | Where-Object { $_.presence -eq "online" })).Count -gt 0) "served=$(if ($afterH.release) { $afterH.release.version } else { 'none' })"
        Save-Evidence
    }

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
    $env:PAGENTOS_QUAL_TOKEN = $null
}
