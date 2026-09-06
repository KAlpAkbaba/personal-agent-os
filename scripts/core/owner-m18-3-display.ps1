<#
.SYNOPSIS
    M18.3 owner qualification C - ambient display control, the input-wake half: with Active
    Eye DISABLED, a real display-off receipt darkens the owner's displays; one key or a mouse
    move wakes them at once; the activity is recorded; the holdoff keeps them on.

.DESCRIPTION
    Proves, from the record, that the camera is not on the wake path: the eye is disabled
    through the proven eye.disable route before anything else and must still be disabled at
    the end. The display test is armed through the production route (the same display.off
    receipt an automatic policy decision would produce, reason owner_test). Then the device's
    heartbeat status shows the display off, the owner's input resets the idle counter and the
    display comes back on, the ledger carries owner.input_active, and for HoldoffCheckSec no
    second display.off is issued although the presence state is still unknown/stale.

    No Cloud Core release unless the deployed contract predates this checkout's (released
    once); the agent must advertise desktop.display_off (DisplayPowerEnabled) - the elevated
    install command is printed and awaited when it does not.

.EXAMPLE
    .\scripts\core\owner-m18-3-display.ps1 -OutFile m18-3-display-1.json
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    [string]$BaseUrl = "",
    [string]$OutFile = "",
    [ValidateSet("auto", "never", "force")][string]$CloudCoreUpdate = "auto",
    [ValidateRange(5, 120)][int]$DelaySec = 20,
    [ValidateRange(30, 600)][int]$OffWaitSec = 90,
    [ValidateRange(30, 900)][int]$InputWaitSec = 300,
    [ValidateRange(30, 600)][int]$HoldoffCheckSec = 90,
    [ValidateRange(0, 1800)][int]$AgentUpdateWaitSec = 600,
    [ValidateRange(2, 30)][int]$PollSec = 3
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\RepoState.ps1")
. (Join-Path $repoRoot "scripts\lib\VoiceShell.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
. (Join-Path $repoRoot "scripts\lib\OwnerHarness.ps1")

if (-not $BaseUrl) { $BaseUrl = "http://${BrokerHost}:$ApiPort" }
$BaseUrl = $BaseUrl.TrimEnd('/')
$runId = "owner-m18-3-display-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$startedAt = (Get-Date).ToUniversalTime()
$runStart = [DateTimeOffset]::UtcNow
$runStartIso = $runStart.ToString("o")
$requiredContractVersion = Get-CheckoutActionContractVersion -RepoRoot $repoRoot
if ($requiredContractVersion -lt 6) { throw "this checkout carries action contract v$requiredContractVersion; M18.3 needs v6 or later" }

# ------------------------------------------------------------- the record's surface (M18.3 spec)
$routeDevices = "/v1/devices"
$routeTestDisplay = "/v1/ambient/test-display"
$routeAmbientPolicy = "/v1/ambient/policy"
$routePresence = "/v1/presence/state"
$routeEyeDisable = "/v1/presence/eye/disable"
$requiredDeviceCaps = @("desktop.display_off", "desktop.display_wake", "desktop.display_status", "desktop.activity_status")

$o_uml = [char]0x00F6; $u_uml = [char]0x00FC; $c_ced = [char]0x00E7
$phraseEyeOn = "G" + $o_uml + "z" + $u_uml + "n" + $u_uml + " a" + $c_ced + "."

$evidence = [ordered]@{ run_id = $runId; started_at = $startedAt.ToString("o"); cloud = $BaseUrl; device = $null; eye = $null; test = $null; timeline = @(); receipts = @(); checks = @(); verdict = "FAIL" }

Write-Host "PagentOS owner M18.3 C - ambient display, input wake ($runId)"
$health = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/system/health" -TimeoutSec 20
Write-Host "      Cloud Core: status=$(Get-OptionalProperty -InputObject $health -Name 'status')"

$credential = $null
try { $credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL" } catch { $credential = $null }
if ($credential) {
    Write-Host "      using the stored Cloud Owner Credential (DPAPI, this account only)"
}
else {
    $secure = Read-Host -Prompt "Cloud Owner Credential (input is hidden; exchanged for one session, then dropped)" -AsSecureString
    if ($secure.Length -eq 0) { throw "empty credential; nothing done." }
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $credential = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
try {
    $body = @{ owner_credential = $credential; client_kind = "cli"; label = $runId } | ConvertTo-Json -Compress
    $issued = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body $body
}
finally { $credential = $null; $body = $null }
$token = [string]$issued.token
$mintedId = [string](Get-OptionalProperty -InputObject $issued -Name "session_id")
$headers = @{ Authorization = "Bearer $token" }

function Get-Json { param([string]$Path) return Invoke-JsonUtf8 -Uri "$BaseUrl$Path" -Headers $headers -TimeoutSec 30 }
function Get-JsonOrNull { param([string]$Path) try { return Invoke-JsonUtf8 -Uri "$BaseUrl$Path" -Headers $headers -TimeoutSec 30 } catch { return $null } }
function Send-Json { param([string]$Method, [string]$Path, [string]$Body) return Invoke-JsonUtf8 -Method $Method -Uri "$BaseUrl$Path" -Headers $headers -Body $Body -TimeoutSec 30 }

function Add-Check {
    param([string]$Name, [bool]$Ok, [string]$Detail)
    $script:evidence.checks += [ordered]@{ name = $Name; ok = $Ok; detail = $Detail }
    $mark = if ($Ok) { "ok  " } else { "FAIL" }
    $color = if ($Ok) { "Green" } else { "Red" }
    Write-Host ("  [{0}] {1}: {2}" -f $mark, $Name, $Detail) -ForegroundColor $color
}

function Add-Timeline { param([string]$What, [string]$Detail) $script:evidence.timeline += [ordered]@{ at = (Get-Date).ToUniversalTime().ToString("o"); what = $What; detail = $Detail }; Write-Host ("      {0}: {1}" -f $What, $Detail) -ForegroundColor Green }

function Get-DeployedContractVersion {
    param($Health)
    $checks = Get-OptionalProperty -InputObject $Health -Name "checks"
    $vr = if ($null -ne $checks) { Get-OptionalProperty -InputObject $checks -Name "voice_realtime" } else { $null }
    $v = if ($null -ne $vr) { Get-OptionalProperty -InputObject $vr -Name "action_contract_version" } else { $null }
    if ($null -eq $v) { return 1 }
    return [int]$v
}

function Invoke-CloudCoreRelease {
    $release = Join-Path $repoRoot "scripts\cloud\release-cloud-core.ps1"
    Write-Host "      releasing the Cloud Core (transactional: build, migrate, recreate api only, health, rollback on failure)..." -ForegroundColor Yellow
    & $release
    if ($LASTEXITCODE -ne 0) { throw "the Cloud Core release exited $LASTEXITCODE; nothing was qualified" }
}

function Get-OnlineDevice {
    $doc = Get-JsonOrNull $routeDevices
    $devices = Get-ArrayProperty -InputObject $doc -Name "devices"
    $online = @($devices | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "presence") -eq "online" })
    if ($online.Count -eq 0) { return $null }
    return $online[0]
}

function Get-DeviceId { param($Device) $id = [string](Get-OptionalProperty -InputObject $Device -Name "device_id"); if (-not $id) { $id = [string](Get-OptionalProperty -InputObject $Device -Name "id") }; return $id }

function Get-MissingCapabilities {
    param($Device, [string[]]$Required)
    $caps = @(ConvertTo-Array -Value (Get-OptionalProperty -InputObject $Device -Name "capabilities") | ForEach-Object { [string]$_ })
    $missing = @()
    foreach ($r in $Required) { if ($caps -notcontains $r) { $missing += $r } }
    return , $missing
}

function Get-DeviceStatus {
    param([string]$DeviceId)
    if (-not $DeviceId) { return $null }
    $doc = Get-JsonOrNull "$routeDevices/$DeviceId/status"
    if ($null -eq $doc) { return $null }
    $inner = Get-OptionalProperty -InputObject $doc -Name "status"
    if ($null -ne $inner) { return $inner }
    return $doc
}

function Get-DisplayState {
    param($Status)
    if ($null -eq $Status) { return "" }
    $display = Get-OptionalProperty -InputObject $Status -Name "display"
    if ($null -ne $display) { return ([string](Get-OptionalProperty -InputObject $display -Name "state")).ToLowerInvariant() }
    return ([string](Get-OptionalProperty -InputObject $Status -Name "display_state")).ToLowerInvariant()
}

function Get-InputIdle {
    param($Status)
    if ($null -eq $Status) { return $null }
    $v = Get-OptionalProperty -InputObject $Status -Name "input_idle_s"
    if ($null -eq $v) { return $null }
    return [double]$v
}

function Get-LedgerRows {
    $doc = Get-JsonOrNull ("/v1/ledger/events?since=" + [uri]::EscapeDataString($runStartIso) + "&limit=200")
    return , (Get-ArrayProperty -InputObject $doc -Name "events")
}

function Get-EyeEnabled {
    $doc = Get-JsonOrNull $routePresence
    if ($null -eq $doc) { return $null }
    $eye = Get-OptionalProperty -InputObject $doc -Name "eye"
    if ($null -ne $eye) { $v = Get-OptionalProperty -InputObject $eye -Name "enabled"; if ($null -ne $v) { return [bool]$v } }
    $v = Get-OptionalProperty -InputObject $doc -Name "eye_enabled"
    if ($null -ne $v) { return [bool]$v }
    return $null
}

function Get-PresenceState {
    $doc = Get-JsonOrNull $routePresence
    if ($null -eq $doc) { return "" }
    $assertion = Get-OptionalProperty -InputObject $doc -Name "assertion"
    if ($null -ne $assertion) { return [string](Get-OptionalProperty -InputObject $assertion -Name "state") }
    return [string](Get-OptionalProperty -InputObject $doc -Name "state")
}

$installCommand = "Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File `"" + (Join-Path $repoRoot "scripts\install-device-service.ps1") + "`" -DisplayPower'"

$exitCode = 2
$eyeWasEnabled = $null
try {
 do {
    # ------------------------------------------------------------------ the deployed contract
    $deployedVersion = Get-DeployedContractVersion -Health $health
    $stale = ($deployedVersion -lt $requiredContractVersion)
    $releaseBlockers = Get-ReleaseBlockers -RepoRoot $repoRoot
    $blockerChanges = ConvertTo-Array -Value $releaseBlockers.Changes
    $releaseCloud = switch ($CloudCoreUpdate) { "force" { $true } "never" { $false } default { $stale } }
    Write-Host "      deployed action contract v$deployedVersion (this checkout: v$requiredContractVersion)"
    if ($stale -and $CloudCoreUpdate -eq "never") { throw "the deployed Cloud Core is stale (v$deployedVersion < v$requiredContractVersion); -CloudCoreUpdate never refuses to release" }
    if ($releaseCloud -and $releaseBlockers.Blocked) { Write-ReleaseBlockers -Blockers $releaseBlockers; throw "a Cloud Core release is required but the working tree has $($blockerChanges.Count) uncommitted change(s); commit or revert them, then rerun" }
    if ($releaseCloud) {
        Write-Host "      the deployed Cloud Core carries an older action contract (v$deployedVersion < v$requiredContractVersion): releasing it once" -ForegroundColor Yellow
        Invoke-CloudCoreRelease
        $health = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/system/health" -TimeoutSec 20
        $deployedVersion = Get-DeployedContractVersion -Health $health
        $stale = ($deployedVersion -lt $requiredContractVersion)
    }
    Add-Check -Name "cloud.contract_deployed" -Ok (-not $stale) -Detail "action contract v$deployedVersion$(if ($releaseCloud) { ' (released in this run)' })"
    if ($stale) { break }

    # ------------------------------------------------------------------ the device
    $device = Get-OnlineDevice
    $missing = @()
    if ($null -ne $device) { $missing = Get-MissingCapabilities -Device $device -Required $requiredDeviceCaps }
    if ($null -eq $device -or $missing.Count -gt 0) {
        Write-Host ""
        if ($null -eq $device) { Write-Host "No online device. Start or install the Windows agent, then this run continues by itself:" -ForegroundColor Yellow }
        else { Write-Host ("The installed agent does not advertise display control (missing: {0}). Update it in an ELEVATED window - one UAC prompt:" -f ($missing -join ", ")) -ForegroundColor Yellow }
        Write-Host "  $installCommand" -ForegroundColor Cyan
        Write-Host "This run waits up to $AgentUpdateWaitSec s for the device to come back with display control."
        $waitStart = [DateTimeOffset]::UtcNow
        while (([DateTimeOffset]::UtcNow - $waitStart).TotalSeconds -lt $AgentUpdateWaitSec) {
            Start-Sleep -Seconds 10
            $device = Get-OnlineDevice
            if ($null -ne $device) { $missing = Get-MissingCapabilities -Device $device -Required $requiredDeviceCaps; if ($missing.Count -eq 0) { break } }
        }
    }
    $deviceId = if ($null -ne $device) { Get-DeviceId -Device $device } else { "" }
    $evidence.device = [ordered]@{ id = $deviceId; missing = $missing }
    Add-Check -Name "device.display_capable" -Ok ($null -ne $device -and $missing.Count -eq 0) -Detail $(if ($null -eq $device) { "no online device" } elseif ($missing.Count) { "still missing: " + ($missing -join ", ") } else { "device $deviceId advertises display control and activity status" })
    if ($null -eq $device -or $missing.Count -gt 0) { break }
    $statusBefore = Get-DeviceStatus -DeviceId $deviceId
    Add-Check -Name "device.status_on_heartbeat" -Ok ($null -ne $statusBefore -and (Get-DisplayState -Status $statusBefore) -ne "") -Detail $(if ($null -ne $statusBefore) { "display " + (Get-DisplayState -Status $statusBefore) + ", input idle " + (Get-InputIdle -Status $statusBefore) + " s" } else { "no device status yet (the heartbeat carries none?)" })

    # ------------------------------------------------------------------ the eye, off
    $eyeWasEnabled = Get-EyeEnabled
    $disableBody = @{ reason = "qualification:m18-3-display"; action_id = "harness-" + $runId } | ConvertTo-Json -Compress
    try { $null = Send-Json -Method "POST" -Path $routeEyeDisable -Body $disableBody } catch { Write-Host "      eye.disable route: $($_.Exception.Message)" -ForegroundColor Yellow }
    $eyeNow = Get-EyeEnabled
    $evidence.eye = [ordered]@{ was_enabled = $eyeWasEnabled; disabled_for_test = $(if ($null -ne $eyeNow) { -not $eyeNow } else { $null }) }
    Add-Check -Name "eye.disabled_for_the_test" -Ok ($null -ne $eyeNow -and -not $eyeNow) -Detail $(if ($null -ne $eyeNow) { "eye enabled=$eyeNow (was $eyeWasEnabled)" } else { "the presence state does not report the eye" })
    if ($null -eq $eyeNow -or $eyeNow) { break }

    # ------------------------------------------------------------------ the test, armed
    Write-Host ""
    Write-Host "HANDS OFF the keyboard and mouse now. In about $DelaySec s the displays go dark." -ForegroundColor Cyan
    Write-Host "When they do, press SHIFT once (or move the mouse). Then hands off again until this script finishes." -ForegroundColor Cyan
    Write-Host ""
    # The route takes only the delay (app/ambient/routes.py); the reason is owner_test and the
    # device holdoff is short by construction on the test path (app/ambient/service.py).
    $testBody = @{ delay_seconds = $DelaySec } | ConvertTo-Json -Compress
    $armed = Send-Json -Method "POST" -Path $routeTestDisplay -Body $testBody
    $evidence.test = $armed
    Add-Timeline -What "test armed" -Detail ("scheduled for " + [string](Get-OptionalProperty -InputObject $armed -Name "scheduled_at"))

    # ------------------------------------------------------------------ the watch
    $phase = "await_off"
    $phaseStarted = [DateTimeOffset]::UtcNow
    $stopReason = ""
    $offAt = $null; $onAt = $null; $inputAt = $null
    $offReceipt = $null
    $lastSig = ""; $lastPrint = [DateTimeOffset]::UtcNow.AddSeconds(-60)
    $rows = @()
    while ($true) {
        $now = [DateTimeOffset]::UtcNow
        $inPhase = ($now - $phaseStarted).TotalSeconds
        $status = Get-DeviceStatus -DeviceId $deviceId
        $displayState = Get-DisplayState -Status $status
        $idle = Get-InputIdle -Status $status
        $rows = Get-LedgerRows
        $offReceipts = Get-ReceiptRows -Rows $rows -Capability "display.off"
        $inputRows = @($rows | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "event_type") -eq "owner.input_active" })
        switch ($phase) {
            "await_off" {
                if ($offReceipts.Count -ge 1 -and $null -eq $offReceipt) { $offReceipt = $offReceipts[0]; Add-Timeline -What "display.off receipt" -Detail ("{0}/{1} {2}" -f $offReceipt.Execution, $offReceipt.Terminal, $offReceipt.ErrorClass) }
                if ($displayState -eq "off") { $offAt = $now; $phase = "await_input"; $phaseStarted = $now; Add-Timeline -What "display observed off" -Detail "device status (heartbeat)" }
                elseif ($null -ne $offReceipt -and $offReceipt.Execution -eq "refused") { $stopReason = "display.off was refused by the device ($($offReceipt.ErrorClass)) - keep hands off longer and rerun" }
                elseif ($inPhase -ge ($DelaySec + $OffWaitSec)) { $stopReason = "the display was not observed off within $OffWaitSec s of the scheduled instant (receipts: $($offReceipts.Count), state '$displayState')" }
            }
            "await_input" {
                if ($displayState -eq "on") {
                    $onAt = $now
                    if ($inputRows.Count -ge 1) { $inputAt = ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $inputRows[0] -Name "occurred_at")) }
                    Add-Timeline -What "display observed on" -Detail ("input idle {0} s; owner.input_active rows {1}" -f $idle, $inputRows.Count)
                    $phase = "await_holdoff"; $phaseStarted = $now
                    Write-Host "      holding for $HoldoffCheckSec s: nothing may darken the display again (hands off)" -ForegroundColor DarkGray
                }
                elseif ($inPhase -ge $InputWaitSec) { $stopReason = "no input woke the display within $InputWaitSec s (press a key when it is dark)" }
            }
            "await_holdoff" {
                if ($inPhase -ge $HoldoffCheckSec) { $phase = "done" }
                elseif ($displayState -eq "off") { $stopReason = "the display went off again $([math]::Round($inPhase, 0)) s after the input - the holdoff did not hold" }
            }
        }
        if ($phase -eq "done" -or $stopReason) { break }
        $sig = "{0}|{1}|{2}|{3}" -f $phase, $displayState, $offReceipts.Count, $inputRows.Count
        if ($sig -ne $lastSig -or ($now - $lastPrint).TotalSeconds -ge 20) {
            Write-Host ("      [{0,4:N0} s] {1}   display: {2}   input idle: {3} s   display.off receipts: {4}   input rows: {5}" -f ($now - $runStart).TotalSeconds, $phase, $(if ($displayState) { $displayState } else { "?" }), $(if ($null -ne $idle) { $idle } else { "?" }), $offReceipts.Count, $inputRows.Count) -ForegroundColor DarkGray
            $lastSig = $sig; $lastPrint = $now
        }
        Start-Sleep -Seconds $PollSec
    }
    if ($stopReason) { Write-Host "      stopped: $stopReason" -ForegroundColor Yellow }

    # ------------------------------------------------------------------ the checks
    $rows = Get-LedgerRows
    $receipts = Get-ReceiptRows -Rows $rows
    foreach ($r in $receipts) { $evidence.receipts += [ordered]@{ capability = $r.Capability; action_id = $r.ActionId; execution = $r.Execution; terminal = $r.Terminal; error_class = $r.ErrorClass; at = $r.OccurredAt } }
    $offReceipts = Get-ReceiptRows -Rows $rows -Capability "display.off"
    $executedOff = @($offReceipts | Where-Object { $_.Execution -eq "executed" })
    Add-Check -Name "display.off_receipted" -Ok ($executedOff.Count -ge 1) -Detail $(if ($executedOff.Count) { "display.off executed/$($executedOff[0].Terminal) at $($executedOff[0].OccurredAt) (reason owner_test)" } elseif ($offReceipts.Count) { "display.off $($offReceipts[0].Execution)/$($offReceipts[0].Terminal) $($offReceipts[0].ErrorClass)" } else { "no display.off receipt: $stopReason" })
    Add-Check -Name "display.observed_off" -Ok ($null -ne $offAt) -Detail $(if ($null -ne $offAt) { "the device reported the display off at $($offAt.ToString('o'))" } else { "never observed off: $stopReason" })
    $wakeLatency = $null
    if ($null -ne $offAt -and $null -ne $onAt) { $wakeLatency = [math]::Round(($onAt - $offAt).TotalSeconds, 0) }
    Add-Check -Name "display.woke_on_input" -Ok ($null -ne $onAt) -Detail $(if ($null -ne $onAt) { "display on again $wakeLatency s after it went off (heartbeat granularity ~10 s)" } else { "the display did not come back on: $stopReason" })
    $inputRows = @($rows | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "event_type") -eq "owner.input_active" })
    Add-Check -Name "input.activity_recorded" -Ok ($inputRows.Count -ge 1) -Detail $(if ($inputRows.Count) { "$($inputRows.Count) owner.input_active row(s); first at " + [string](Get-OptionalProperty -InputObject $inputRows[0] -Name "occurred_at") } else { "no owner.input_active row" })
    $laterOff = @()
    if ($null -ne $onAt) { $laterOff = @($offReceipts | Where-Object { $at = ConvertTo-SessionInstant -Raw $_.OccurredAt; $null -ne $at -and (Test-InstantAtOrAfter -Instant $at -Floor $onAt -ToleranceSec 0) }) }
    Add-Check -Name "holdoff.no_re_off" -Ok ($null -ne $onAt -and $laterOff.Count -eq 0 -and $phase -eq "done") -Detail $(if ($null -ne $onAt) { "display.off receipts after the wake: $($laterOff.Count) over $HoldoffCheckSec s" } else { "not reached" })
    $policy = Get-JsonOrNull $routeAmbientPolicy
    $presence = Get-PresenceState
    Add-Check -Name "presence.not_on_the_wake_path" -Ok ($presence -in @("unknown", "", "away", "likely_asleep", "resting")) -Detail "presence state during the test: '$presence' (the eye was disabled; the wake came from input, not the camera)"
    $eyeEnd = Get-EyeEnabled
    Add-Check -Name "eye.stayed_disabled" -Ok ($null -ne $eyeEnd -and -not $eyeEnd) -Detail "eye enabled at the end: $eyeEnd"
    $evidence.policy = $policy
    $failed = @($evidence.checks | Where-Object { -not $_.ok })
    if ($failed.Count -eq 0) { $evidence.verdict = "PASS"; $exitCode = 0 } else { $evidence.verdict = "FAIL"; $exitCode = 2 }
 } while ($false)
}
finally {
    if ($OutFile) {
        $evidence.finished_at = (Get-Date).ToUniversalTime().ToString("o")
        [System.IO.File]::WriteAllText($OutFile, ($evidence | ConvertTo-Json -Depth 12), (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "      evidence written: $OutFile"
    }
    try { Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions/$mintedId/revoke" -Headers $headers -Body "{}" | Out-Null } catch { }
    $headers = $null; $token = $null
}

if ($eyeWasEnabled) { Write-Host ("The eye was on before this run; say '{0}' on /core when you want it back (the camera reopens only from the browser)." -f $phraseEyeOn) -ForegroundColor Yellow }
Write-Host "OWNER M18.3 C (AMBIENT DISPLAY): $($evidence.verdict)" -ForegroundColor $(if ($evidence.verdict -eq "PASS") { "Green" } else { "Red" })
exit $exitCode
