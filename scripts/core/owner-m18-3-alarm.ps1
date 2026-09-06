<#
.SYNOPSIS
    M18.3 owner qualification B - the wake alarm, end to end, from one spoken sentence:
    schedule -> device armed -> fired by the clock -> display wake -> real media -> ramp ->
    "Gunaydin efendim" over ducked music -> restore -> "Alarmi kapat." -> stopped -> cleaned up.

.DESCRIPTION
    Every step is read off the Cloud Core's durable record (the alarm row, its routine's
    firing, the action receipts in the ledger, the device's heartbeat status) - never off a
    yes/no from the owner. Preflight gates on the deployed action contract (this checkout's
    receipt.py; released once if stale) and on the device's advertised capabilities (the
    elevated install command is printed and awaited when the installed agent predates M18.3).

    What the owner does: opens /core, connects voice, says the test-alarm sentence, waits
    ~90 s while the alarm fires, listens, and says "Alarmi kapat." when told. With -MusicUrl
    the harness first registers that YouTube item as the owner's chosen wake song, so
    "sectigim YouTube muzigi" resolves to it.

.EXAMPLE
    .\scripts\core\owner-m18-3-alarm.ps1 -MusicUrl "https://www.youtube.com/watch?v=..." -OutFile m18-3-alarm-1.json
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    [int]$WebPort = 3000,
    [string]$BaseUrl = "",
    [string]$OutFile = "",
    [string]$MusicUrl = "",
    [string]$MusicTitle = "",
    [switch]$SkipWeb,
    [string]$PnpmPath = "pnpm",
    [ValidateSet("auto", "never", "force")][string]$CloudCoreUpdate = "auto",
    [ValidateRange(30, 900)][int]$ConnectWaitSec = 180,
    [ValidateRange(30, 900)][int]$CreateWaitSec = 240,
    [ValidateRange(10, 300)][int]$FireGraceSec = 60,
    [ValidateRange(30, 600)][int]$PlayWaitSec = 150,
    [ValidateRange(30, 600)][int]$StopWaitSec = 180,
    [ValidateRange(0, 1800)][int]$AgentUpdateWaitSec = 600,
    [ValidateRange(2, 30)][int]$PollSec = 4,
    [ValidateRange(10, 900)][int]$WebReadyTimeoutSec = 180
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
$runId = "owner-m18-3-alarm-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$startedAt = (Get-Date).ToUniversalTime()
$runStart = [DateTimeOffset]::UtcNow
$runStartIso = $runStart.ToString("o")
$requiredContractVersion = Get-CheckoutActionContractVersion -RepoRoot $repoRoot
if ($requiredContractVersion -lt 6) { throw "this checkout carries action contract v$requiredContractVersion; M18.3 needs v6 or later" }

# ------------------------------------------------------------- the record's surface (M18.3 spec)
$routeAlarms = "/v1/alarms"
$routeWakeSong = "/v1/alarms/wake-song"
$routeDevices = "/v1/devices"
$requiredDeviceCaps = @("desktop.alarm_arm", "desktop.alarm_disarm", "desktop.display_wake", "desktop.play_audio", "desktop.activity_status")
$mediaDeviceCap = "browser.media_play"
$activeStates = @("firing", "display_waking", "media_starting", "playing", "greeting")
$playingStates = @("playing", "greeting")
$terminalStates = @("stopped", "completed", "failed", "cancelled")

$u_uml = [char]0x00FC; $g_br = [char]0x011F; $i_dot = [char]0x0131; $s_ced = [char]0x015F; $c_ced = [char]0x00E7
$phraseCreate = "90 saniye sonra se" + $c_ced + "ti" + $g_br + "im YouTube m" + $u_uml + "zi" + $g_br + "iyle test alarm" + $i_dot + " kur."
$phraseCreateTone = "90 saniye sonra test alarm" + $i_dot + " kur."
$phraseStop = "Alarm" + $i_dot + " kapat."

$evidence = [ordered]@{ run_id = $runId; started_at = $startedAt.ToString("o"); cloud = $BaseUrl; device = $null; web_shell = $null; session = $null; alarm = $null; firing = $null; receipts = @(); device_status_after = $null; checks = @(); verdict = "FAIL" }

Write-Host "PagentOS owner M18.3 B - the wake alarm ($runId)"
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
    <#  The one online device, with its advertised capabilities; $null when none is online.  #>
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

function Get-LedgerRows {
    <#  Every ledger row since the run started (all types; filtered client-side).  #>
    $doc = Get-JsonOrNull ("/v1/ledger/events?since=" + [uri]::EscapeDataString($runStartIso) + "&limit=300")
    return , (Get-ArrayProperty -InputObject $doc -Name "events")
}

function Get-AlarmId { param($Alarm) $id = [string](Get-OptionalProperty -InputObject $Alarm -Name "alarm_id"); if (-not $id) { $id = [string](Get-OptionalProperty -InputObject $Alarm -Name "id") }; return $id }

function Get-RunAlarm {
    <#  The most recent alarm created since the run started (test alarms first).  #>
    $doc = Get-JsonOrNull "$routeAlarms`?limit=20"
    $alarms = Get-ArrayProperty -InputObject $doc -Name "alarms"
    $mine = @()
    foreach ($a in $alarms) {
        $created = ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $a -Name "created_at"))
        if ($null -eq $created) { continue }
        if (Test-InstantAtOrAfter -Instant $created -Floor $runStart -ToleranceSec 2) { $mine += $a }
    }
    if ($mine.Count -eq 0) { return $null }
    $tests = @($mine | Where-Object { [bool](Get-OptionalProperty -InputObject $_ -Name "is_test") })
    if ($tests.Count -gt 0) { return $tests[$tests.Count - 1] }
    return $mine[$mine.Count - 1]
}

function Get-AlarmState { param($Alarm) return ([string](Get-OptionalProperty -InputObject $Alarm -Name "state")).ToLowerInvariant() }

$installCommand = "Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File `"" + (Join-Path $repoRoot "scripts\install-device-service.ps1") + "`" -DisplayPower'"

$webProcess = $null
$exitCode = 2
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
        else { Write-Host ("The installed agent predates M18.3 (missing: {0}). Update it in an ELEVATED window - one UAC prompt:" -f ($missing -join ", ")) -ForegroundColor Yellow }
        Write-Host "  $installCommand" -ForegroundColor Cyan
        Write-Host "This run waits up to $AgentUpdateWaitSec s for the device to come back with the M18.3 capabilities."
        $waitStart = [DateTimeOffset]::UtcNow
        while (([DateTimeOffset]::UtcNow - $waitStart).TotalSeconds -lt $AgentUpdateWaitSec) {
            Start-Sleep -Seconds 10
            $device = Get-OnlineDevice
            if ($null -ne $device) { $missing = Get-MissingCapabilities -Device $device -Required $requiredDeviceCaps; if ($missing.Count -eq 0) { break } }
        }
    }
    $deviceId = if ($null -ne $device) { Get-DeviceId -Device $device } else { "" }
    $deviceCaps = @()
    if ($null -ne $device) { $deviceCaps = @(ConvertTo-Array -Value (Get-OptionalProperty -InputObject $device -Name "capabilities") | ForEach-Object { [string]$_ }) }
    $mediaCapable = ($deviceCaps -contains $mediaDeviceCap)
    $evidence.device = [ordered]@{ id = $deviceId; capabilities = $deviceCaps; media_capable = $mediaCapable; missing = $missing }
    Add-Check -Name "device.capabilities_current" -Ok ($null -ne $device -and $missing.Count -eq 0) -Detail $(if ($null -eq $device) { "no online device" } elseif ($missing.Count) { "still missing: " + ($missing -join ", ") } else { "device $deviceId advertises the M18.3 capabilities" })
    if ($null -eq $device -or $missing.Count -gt 0) { break }
    Add-Check -Name "device.media_capable" -Ok $mediaCapable -Detail $(if ($mediaCapable) { "$mediaDeviceCap advertised: the YouTube path is available" } else { "$mediaDeviceCap not advertised (browser worker not provisioned?): the alarm will ring the tone, truthfully - row 14.7 cannot pass on this device" })

    # ------------------------------------------------------------------ the chosen song
    $song = $null
    if ($MusicUrl) {
        $title = if ($MusicTitle) { $MusicTitle } else { "owner's wake song" }
        $songBody = @{ url = $MusicUrl; title = $title } | ConvertTo-Json -Compress
        $song = Send-Json -Method "PUT" -Path $routeWakeSong -Body $songBody
        Write-Host "      wake song registered: $MusicUrl"
    }

    # ------------------------------------------------------------------ the Core
    $baselineIds = @()
    foreach ($s in (Get-ArrayProperty -InputObject (Get-Json "/v1/voice/realtime/sessions?limit=50") -Name "sessions")) { $baselineIds += [string](Get-OptionalProperty -InputObject $s -Name "session_id") }
    $coreUrl = "http://localhost:$WebPort/core"
    $shell = [ordered]@{ url = $coreUrl; started = $false; ready = $false; ready_after_s = $null }
    if (-not $SkipWeb) {
        $listener = Get-ListeningProcess -Port $WebPort
        if ($null -ne $listener) { throw "port $WebPort is already in use by pid $($listener.Pid) ($($listener.Name), started $($listener.StartedAt)); stop it (taskkill /PID $($listener.Pid) /T /F) or pass -SkipWeb" }
        $logPath = Join-Path $env:TEMP "$runId-web.log"
        $webProcess = Start-WebShellProcess -RepoRoot $repoRoot -Upstream $BaseUrl -WebPort $WebPort -LogPath $logPath -PnpmPath $PnpmPath
        $shell.started = $true; $shell.log = $logPath
    }
    $ready = Wait-WebShellReady -Url $coreUrl -TimeoutSec $WebReadyTimeoutSec
    $shell.ready = [bool]$ready.Ready; $shell.ready_after_s = [math]::Round([double]$ready.ElapsedSec, 1)
    $evidence.web_shell = $shell
    if (-not $ready.Ready) { throw "the web shell did not answer $coreUrl within $WebReadyTimeoutSec s (log: $($shell.log))" }
    Add-Check -Name "core.reachable" -Ok $true -Detail "$coreUrl answered after $($shell.ready_after_s) s"

    $phraseToSay = if ($MusicUrl -or $null -ne $song) { $phraseCreate } else { $phraseCreateTone }
    Write-Host ""
    Write-Host "Open $coreUrl, sign in, connect voice THERE, then:" -ForegroundColor Cyan
    Write-Host ("  1. say: {0}" -f $phraseToSay)
    Write-Host "     (it answers only from the receipt; then wait: ~90 s later the displays wake, the music starts low and rises,"
    Write-Host "      and it greets you over the music)"
    Write-Host ("  2. when this script says so, say: {0}" -f $phraseStop)
    Write-Host "It finishes by itself. Nothing to press."
    Write-Host ""

    # ------------------------------------------------------------------ the watch
    $sessionId = ""
    $phase = "await_session"
    $phaseStarted = [DateTimeOffset]::UtcNow
    $lastPrint = [DateTimeOffset]::UtcNow.AddSeconds(-60)
    $lastSig = ""
    $stopReason = ""
    $alarm = $null
    $alarmId = ""
    $scheduledFor = $null
    $stopPrompted = $false
    $rows = @()
    while ($true) {
        $now = [DateTimeOffset]::UtcNow
        $inPhase = ($now - $phaseStarted).TotalSeconds
        $elapsed = ($now - $runStart).TotalSeconds
        if (-not $sessionId) {
            $busEvents = Get-ArrayProperty -InputObject (Get-JsonOrNull "/v1/ui/state?limit=64") -Name "events"
            $busIds = Get-BusVoiceSessionIds -Events $busEvents -Since $runStart.AddSeconds(-5)
            $allSessions = Get-ArrayProperty -InputObject (Get-JsonOrNull "/v1/voice/realtime/sessions?limit=50") -Name "sessions"
            $mine = Get-NewSessions -Sessions $allSessions -BaselineIds $baselineIds -ReadyAt $null -AcceptIds $busIds
            $named = @($mine | Where-Object { $busIds -contains [string]$_.session_id })
            $pick = if ($named.Count -gt 0) { $named[$named.Count - 1] } elseif ($mine.Count -gt 0) { $mine[0] } else { $null }
            if ($null -ne $pick) { $sessionId = [string]$pick.session_id; $phase = "await_created"; $phaseStarted = $now; Write-Host "      Core Voice connected: $sessionId" -ForegroundColor Green }
            elseif ($inPhase -ge $ConnectWaitSec) { $stopReason = "no Core voice session within $ConnectWaitSec s - connect voice on /core" }
        }
        $latest = Get-RunAlarm
        if ($null -ne $latest) { $alarm = $latest; $alarmId = Get-AlarmId -Alarm $alarm; $scheduledFor = ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $alarm -Name "scheduled_for")) }
        $state = if ($null -ne $alarm) { Get-AlarmState -Alarm $alarm } else { "" }
        $rows = Get-LedgerRows
        $receipts = Get-ReceiptRows -Rows $rows
        $greetingReceipts = @($receipts | Where-Object { $_.Capability -eq "greeting.play" })
        switch ($phase) {
            "await_created" {
                if ($null -ne $alarm) { $phase = "await_fired"; $phaseStarted = $now; Write-Host ("      alarm created: {0} state={1} scheduled_for={2}" -f $alarmId, $state, (Get-OptionalProperty -InputObject $alarm -Name "scheduled_for")) -ForegroundColor Green }
                elseif ($inPhase -ge $CreateWaitSec) { $stopReason = "no alarm created since the run started within $CreateWaitSec s (say the sentence on /core)" }
            }
            "await_fired" {
                if ($activeStates -contains $state -or $terminalStates -contains $state) { $phase = "await_playing"; $phaseStarted = $now; Write-Host ("      alarm fired: state={0} triggered_at={1}" -f $state, (Get-OptionalProperty -InputObject $alarm -Name "triggered_at")) -ForegroundColor Green }
                elseif ($null -ne $scheduledFor -and ($now - $scheduledFor).TotalSeconds -ge $FireGraceSec) { $stopReason = "the alarm did not fire within $FireGraceSec s of its scheduled instant (state $state)" }
                elseif ($null -eq $scheduledFor -and $inPhase -ge ($CreateWaitSec + $FireGraceSec)) { $stopReason = "the alarm row carries no scheduled_for (state $state)" }
            }
            "await_playing" {
                $playingNow = ($playingStates -contains $state)
                if ($playingNow -and $greetingReceipts.Count -ge 1 -and -not $stopPrompted) {
                    $stopPrompted = $true
                    Write-Host ""
                    Write-Host ("  NOW say: {0}" -f $phraseStop) -ForegroundColor Cyan
                    Write-Host ""
                    $phase = "await_stopped"; $phaseStarted = $now
                }
                elseif ($terminalStates -contains $state) { $phase = "await_stopped"; $phaseStarted = $now }
                elseif ($inPhase -ge $PlayWaitSec) {
                    if ($playingNow -and -not $stopPrompted) { $stopPrompted = $true; Write-Host ("  NOW say: {0}   (no greeting receipt yet; stopping anyway)" -f $phraseStop) -ForegroundColor Cyan; $phase = "await_stopped"; $phaseStarted = $now }
                    else { $stopReason = "the alarm did not reach PLAYING within $PlayWaitSec s of firing (state $state)" }
                }
            }
            "await_stopped" {
                if ($terminalStates -contains $state) { $phase = "done"; Write-Host ("      alarm terminal: {0}" -f $state) -ForegroundColor Green }
                elseif ($inPhase -ge $StopWaitSec) { $stopReason = "the alarm did not reach a terminal state within $StopWaitSec s (state $state)" }
            }
        }
        if ($phase -eq "done" -or $stopReason) { break }
        $sig = "{0}|{1}|{2}|{3}" -f $phase, $state, $receipts.Count, $greetingReceipts.Count
        if ($sig -ne $lastSig -or ($now - $lastPrint).TotalSeconds -ge 30) {
            $untilFire = if ($null -ne $scheduledFor) { [math]::Round(($scheduledFor - $now).TotalSeconds, 0) } else { $null }
            Write-Host ("      [{0,4:N0} s] {1}   session: {2}   alarm: {3} {4}   fires in: {5}   receipts: {6}" -f $elapsed, $phase, $(if ($sessionId) { $sessionId } else { "none yet" }), $(if ($alarmId) { $alarmId } else { "none yet" }), $state, $(if ($null -ne $untilFire) { "$untilFire s" } else { "?" }), (($receipts | ForEach-Object { "{0}:{1}" -f $_.Capability, $_.Terminal }) -join ",")) -ForegroundColor DarkGray
            $lastSig = $sig; $lastPrint = $now
        }
        Start-Sleep -Seconds $PollSec
    }
    if ($stopReason) { Write-Host "      stopped: $stopReason" -ForegroundColor Yellow }

    # ------------------------------------------------------------------ the checks
    $evidence.session = [ordered]@{ id = $sessionId }
    Add-Check -Name "voice.core_session_correlated" -Ok ($sessionId -ne "") -Detail $(if ($sessionId) { "session $sessionId" } else { $stopReason })
    $activity = $null
    if ($sessionId) { $activity = Get-JsonOrNull "/v1/voice/realtime/sessions/$sessionId/activity" }
    $calls = Get-ArrayProperty -InputObject $activity -Name "tool_calls"
    $createCalls = @($calls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "name") -eq "alarm.create" -and [string](Get-OptionalProperty -InputObject $_ -Name "status") -eq "succeeded" })
    $stopCalls = @($calls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "name") -eq "alarm.stop" -and [string](Get-OptionalProperty -InputObject $_ -Name "status") -eq "succeeded" })
    $state = if ($null -ne $alarm) { Get-AlarmState -Alarm $alarm } else { "" }
    $evidence.alarm = $alarm
    Add-Check -Name "alarm.created_by_voice" -Ok ($null -ne $alarm -and $createCalls.Count -ge 1) -Detail $(if ($null -ne $alarm) { "alarm $alarmId (is_test=$(Get-OptionalProperty -InputObject $alarm -Name 'is_test')); alarm.create calls succeeded on the session: $($createCalls.Count)" } else { "no alarm created since the run started: $stopReason" })
    $armedAt = if ($null -ne $alarm) { [string](Get-OptionalProperty -InputObject $alarm -Name "armed_at") } else { "" }
    $alarmDevice = if ($null -ne $alarm) { [string](Get-OptionalProperty -InputObject $alarm -Name "device_id") } else { "" }
    Add-Check -Name "alarm.armed_on_device" -Ok ($armedAt -ne "") -Detail $(if ($armedAt) { "armed_at $armedAt on device $alarmDevice" } else { "the alarm row has no armed_at (state $state)" })
    $triggeredAt = if ($null -ne $alarm) { ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $alarm -Name "triggered_at")) } else { $null }
    $onTime = $false; $lateBy = $null
    if ($null -ne $triggeredAt -and $null -ne $scheduledFor) { $lateBy = [math]::Round(($triggeredAt - $scheduledFor).TotalSeconds, 1); $onTime = ($lateBy -ge -2 -and $lateBy -le $FireGraceSec) }
    $routineId = if ($null -ne $alarm) { [string](Get-OptionalProperty -InputObject $alarm -Name "routine_id") } else { "" }
    $firings = @()
    if ($routineId) { $firings = Get-ArrayProperty -InputObject (Get-JsonOrNull "/v1/routines/$routineId/firings") -Name "firings" }
    $evidence.firing = [ordered]@{ routine_id = $routineId; firings = $firings.Count; triggered_at = $(if ($null -ne $triggeredAt) { $triggeredAt.ToString("o") } else { $null }); late_by_s = $lateBy }
    Add-Check -Name "alarm.fired_by_the_clock_on_time" -Ok ($onTime -and $firings.Count -ge 1) -Detail $(if ($null -ne $triggeredAt) { "triggered $lateBy s after scheduled_for; routine $routineId firings: $($firings.Count)" } else { "never triggered (state $state): $stopReason" })
    $firingRows = @($rows | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "event_type") -eq "alarm.firing" })
    Add-Check -Name "alarm.no_duplicate_firing" -Ok ($firings.Count -le 1 -and $firingRows.Count -le 1) -Detail "routine firings: $($firings.Count); alarm.firing ledger rows: $($firingRows.Count)"
    $receipts = Get-ReceiptRows -Rows $rows
    foreach ($r in $receipts) { $evidence.receipts += [ordered]@{ capability = $r.Capability; action_id = $r.ActionId; execution = $r.Execution; terminal = $r.Terminal; error_class = $r.ErrorClass; at = $r.OccurredAt } }
    $wake = @($receipts | Where-Object { $_.Capability -eq "display.wake" })
    Add-Check -Name "alarm.display_wake_receipted" -Ok ($wake.Count -ge 1) -Detail $(if ($wake.Count) { "display.wake $($wake[0].Execution)/$($wake[0].Terminal)$(if ($wake[0].ErrorClass) { ' (' + $wake[0].ErrorClass + ' - recorded, the alarm went on)' })" } else { "no display.wake receipt" })
    $play = @($receipts | Where-Object { $_.Capability -eq "media.play" })
    $playVerified = @($play | Where-Object { $_.Terminal -eq "verified" })
    $tone = @($receipts | Where-Object { $_.Capability -eq "alarm.start" })
    Add-Check -Name "alarm.media_really_played" -Ok ($playVerified.Count -ge 1) -Detail $(if ($playVerified.Count) { "media.play verified (the media element advanced)" } elseif ($play.Count) { "media.play $($play[0].Execution)/$($play[0].Terminal) $($play[0].ErrorClass); tone fallback receipts: $($tone.Count) - truthful, but row 14.7 needs the real item" } elseif ($tone.Count) { "no media.play receipt; the tone rang ($($tone.Count) alarm.start receipt(s))$(if (-not $mediaCapable) { ' - the device advertises no browser media capability' })" } else { "neither media.play nor alarm.start receipts" })
    Add-Check -Name "alarm.audio_path_taken" -Ok ($playVerified.Count -ge 1 -or (@($tone | Where-Object { $_.Terminal -in @("verified", "already") })).Count -ge 1) -Detail $(if ($playVerified.Count) { "music" } elseif ($tone.Count) { "tone (" + $tone[0].Terminal + ")" } else { "no audio path reached the device" })
    $volume = @($receipts | Where-Object { $_.Capability -eq "media.volume" })
    Add-Check -Name "alarm.volume_ramped" -Ok ($volume.Count -ge 1 -or $tone.Count -ge 1) -Detail $(if ($volume.Count) { "$($volume.Count) media.volume receipt(s) (ramp, duck, restore)" } elseif ($tone.Count) { "the tone's own ramp (alarm.start carries start/end/ramp_seconds)" } else { "no ramp receipt" })
    $greeting = @($receipts | Where-Object { $_.Capability -eq "greeting.play" })
    $greetingOk = @($greeting | Where-Object { $_.Terminal -eq "verified" })
    Add-Check -Name "alarm.greeting_played" -Ok ($greetingOk.Count -ge 1) -Detail $(if ($greetingOk.Count) { "greeting.play verified at $($greetingOk[0].OccurredAt)" } elseif ($greeting.Count) { "greeting.play $($greeting[0].Execution)/$($greeting[0].Terminal) $($greeting[0].ErrorClass)" } else { "no greeting.play receipt" })
    Add-Check -Name "alarm.ducked_and_restored" -Ok ($playVerified.Count -eq 0 -or $volume.Count -ge 3) -Detail $(if ($playVerified.Count) { "media.volume receipts around the greeting: $($volume.Count) (ramp + duck + restore expected)" } else { "not applicable on the tone path" })
    Add-Check -Name "alarm.stopped_by_voice" -Ok ($stopCalls.Count -ge 1 -and $state -in @("stopped", "completed")) -Detail $(if ($stopCalls.Count) { "alarm.stop succeeded on the session; state $state" } else { "no succeeded alarm.stop on the session; state $state" })
    $cleanRows = @($rows | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "event_type") -eq "alarm.cleaned_up" })
    $statusAfter = Get-DeviceStatus -DeviceId $deviceId
    $evidence.device_status_after = $statusAfter
    $armedAfter = @(ConvertTo-Array -Value (Get-OptionalProperty -InputObject $statusAfter -Name "armed_alarms"))
    $ringingAfter = if ($null -ne $statusAfter) { [bool](Get-OptionalProperty -InputObject $statusAfter -Name "alarm_ringing") } else { $false }
    Add-Check -Name "alarm.cleaned_up" -Ok ($terminalStates -contains $state -and $cleanRows.Count -ge 1 -and $armedAfter.Count -eq 0 -and -not $ringingAfter) -Detail "state $state; alarm.cleaned_up rows: $($cleanRows.Count); device armed alarms after: $($armedAfter.Count); ringing after: $ringingAfter"
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
    if ($null -ne $webProcess) { Stop-WebShellProcess -Process $webProcess }
    try { Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions/$mintedId/revoke" -Headers $headers -Body "{}" | Out-Null } catch { }
    $headers = $null; $token = $null
}

Write-Host "OWNER M18.3 B (WAKE ALARM): $($evidence.verdict)" -ForegroundColor $(if ($evidence.verdict -eq "PASS") { "Green" } else { "Red" })
exit $exitCode
