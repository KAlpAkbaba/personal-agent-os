<#
.SYNOPSIS
    M18 owner qualification, integrated: from /core alone - voice, cognition, the camera, a
    presence transition, "Gozunu kapat" by voice, one quiet alarm - every claim asserted from
    durable evidence, and the whole thing one run.

.DESCRIPTION
    The twelve points this proves (owner directive, 2026-09-06), and the evidence each rests on:

       1. the Core loads real state           GET /v1/ui/state has a current event, contract v2
       2. voice connects FROM /core           a new web realtime session since the baseline
       3. the owner speaks, Core listens      the session's own mic_speech_start timing event
       4. it answers through the real system  a succeeded tool call with spoken characters
       5. the Core reacts to generated speech the session's first_audio timing event - the event
                                              the Core's speaking visual is published from
       6. a cognitive request reaches its     a succeeded tool call carrying the engine's own
          subsystem                           query_kind + subsystem (never inferred from prose)
       7. the Active Eye can be enabled       eye_enabled=true observed; eye.enabled in the ledger
       8. a current presence observation      a non-unknown assertion with confidence; the World
          reaches World Model / Core          Model fact owner.presence; an owner.* bus event
       9. "Gozunu kapat" disables perception  eye.disabled in the ledger with reason voice:*
      10. routine / alarm still works         the firing dispatched succeeded over the device path
      11. session and processes close         the realtime session closed; the web shell stopped;
                                              the test routine cancelled; the owner session revoked
      12. no duplicate realtime session       no two web sessions of this run were open at once
          (the single-microphone guarantee is a client property, proven by the web unit suite)

    Plus the checks that were already real: a presence TRANSITION (two distinct states), the
    ledger carrying all of it, no raw camera archive, and the SHADOW_READY set unchanged.

    Nothing here is typed by the owner except the credential (masked, or DPAPI) and a final
    Enter. The script watches the Cloud Core while the owner follows a short printed script.
    Preflight is unchanged from the first run: release blockers, health, the routines policy
    (one transactional Cloud Core release if the deployed core predates this checkout), and
    the installed Windows agent must advertise the alarm pair (else NEEDS_AGENT_INSTALL).

.EXAMPLE
    .\scripts\core\owner-m18.ps1 -OutFile m18-2.json
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    [int]$WebPort = 3000,
    [string]$BaseUrl = "",
    [string]$OutFile = "",
    # auto: release the Cloud Core only when it predates this checkout's routines surface;
    # never: refuse; force: always.
    [ValidateSet("auto", "never", "force")][string]$CloudCoreUpdate = "auto",
    # Never start the web shell (it must already answer on -WebPort).
    [switch]$SkipWeb,
    [string]$PnpmPath = "pnpm",
    [ValidateRange(20, 600)][int]$AlarmDelaySec = 20,
    [ValidateRange(10, 120)][int]$AlarmSeconds = 15,
    [ValidateRange(60, 1800)][int]$SessionWaitSec = 600,
    [ValidateRange(60, 1800)][int]$PresenceWaitSec = 420,
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
$runId = "owner-m18-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$startedAt = (Get-Date).ToUniversalTime()

# Turkish text from character codes so this file stays pure ASCII (Windows PowerShell 5.1
# reads a BOM-less file as ANSI and would mangle the letters).
$o_uml = [char]0x00F6; $u_uml = [char]0x00FC; $i_dot = [char]0x0131; $c_ced = [char]0x00E7
$phraseToday = "Son yapt" + $i_dot + "klar" + $i_dot + "n" + $i_dot + " anlat."
$phraseWorld = "Kendi sisteminde ne g" + $o_uml + "r" + $u_uml + "yorsun?"
$phraseEyeOff = "G" + $o_uml + "z" + $u_uml + "n" + $u_uml + " kapat."
$labelEyeOn = "G" + $o_uml + "z" + $u_uml + " a" + $c_ced

# What a presence ledger row may carry. Anything outside this set - and in particular
# anything image-shaped - is a privacy failure, whatever else passed.
$presenceDetailAllowed = @("to_state", "confidence", "reason", "signal_sources", "signal_count")
$presentStates = @("present", "returned", "awake", "resting", "likely_asleep")

$evidence = [ordered]@{
    run_id              = $runId
    started_at          = $startedAt.ToString("o")
    cloud               = $BaseUrl
    cloud_policy        = $null
    agent               = $null
    web_shell           = $null
    core_state          = $null
    shadow_ready_before = $null
    voice               = $null
    presence            = $null
    eye                 = $null
    alarm               = $null
    world               = $null
    ledger              = $null
    shadow_ready_after  = $null
    checks              = @()
    verdict             = "FAIL"
}

Write-Host "PagentOS owner M18 ($runId)"

# ------------------------------------------------------------------ preflight

$releaseBlockers = Get-ReleaseBlockers -RepoRoot $repoRoot
$blockerChanges = ConvertTo-Array -Value $releaseBlockers.Changes
if ($releaseBlockers.Blocked) {
    Write-Host "      working tree: $($blockerChanges.Count) uncommitted change(s) - a Cloud Core release would be refused:" -ForegroundColor Yellow
    Write-ReleaseBlockers -Blockers $releaseBlockers
}
elseif ($releaseBlockers.Checked) {
    Write-Host "      working tree: clean"
}

$health = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/system/health" -TimeoutSec 20
Write-Host "      Cloud Core: status=$(Get-OptionalProperty -InputObject $health -Name 'status')"

function Get-ExpectedRoutinesVersion {
    $routes = Join-Path $repoRoot "services\api\app\routines\routes.py"
    if (-not (Test-Path $routes)) { throw "this checkout has no routines routes ($routes); nothing to qualify" }
    $match = [regex]::Match([System.IO.File]::ReadAllText($routes), '(?m)ROUTINES_VERSION\s*=\s*(\d+)')
    if (-not $match.Success) { return 1 }
    return [int]$match.Groups[1].Value
}

function Invoke-CloudCoreRelease {
    $release = Join-Path $repoRoot "scripts\cloud\release-cloud-core.ps1"
    Write-Host "      releasing the Cloud Core (transactional: build, migrate, recreate api only, health, rollback on failure)..." -ForegroundColor Yellow
    & $release
    if ($LASTEXITCODE -ne 0) { throw "the Cloud Core release exited $LASTEXITCODE; nothing was qualified" }
}

# ------------------------------------------------------------------ owner session

$credential = $null
try { $credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL" } catch { $credential = $null }
if ($credential) {
    Write-Host "      using the stored Cloud Owner Credential (DPAPI, this account only)"
}
else {
    $secure = Read-Host -Prompt "Cloud Owner Credential (input is hidden; exchanged for one session, then dropped)" -AsSecureString
    if ($secure.Length -eq 0) { throw "empty credential; nothing done. Store it once with .\scripts\secret-store.ps1 -Set PAGENTOS_OWNER_CREDENTIAL to stop being asked." }
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
function Post-Json { param([string]$Path, [string]$Body) return Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl$Path" -Headers $headers -Body $Body -TimeoutSec 120 }

function Add-Check {
    param([string]$Name, [bool]$Ok, [string]$Detail)
    $script:evidence.checks += [ordered]@{ name = $Name; ok = $Ok; detail = $Detail }
    $mark = if ($Ok) { "ok  " } else { "FAIL" }
    $color = if ($Ok) { "Green" } else { "Red" }
    Write-Host ("  [{0}] {1}: {2}" -f $mark, $Name, $Detail) -ForegroundColor $color
}

function Get-ShadowReadySnapshot {
    $ready = Get-Json "/v1/evolution/shadow-ready?limit=50"
    $items = Get-ArrayProperty -InputObject $ready -Name "awaiting_approval"
    $statuses = @()
    foreach ($item in $items) { $statuses += ("{0}:{1}" -f (Get-OptionalProperty -InputObject $item -Name "opportunity_id"), (Get-OptionalProperty -InputObject $item -Name "status")) }
    return [ordered]@{ count = $items.Count; statuses = @($statuses | Sort-Object) }
}

function Get-LedgerSince {
    param([string]$SinceIso, [string]$EventType)
    $doc = Get-Json ("/v1/ledger/events?since=" + [uri]::EscapeDataString($SinceIso) + "&event_type=" + $EventType + "&limit=100")
    return , (Get-ArrayProperty -InputObject $doc -Name "events")
}

function Get-DetailReason {
    param($Event)
    $detail = Get-OptionalProperty -InputObject $Event -Name "detail_json"
    if ($null -eq $detail) { return "" }
    return [string](Get-OptionalProperty -InputObject $detail -Name "reason")
}

function Get-WebSessionsSinceBaseline {
    param([string[]]$Baseline)
    $out = @()
    foreach ($s in (Get-ArrayProperty -InputObject (Get-Json "/v1/voice/realtime/sessions?limit=50") -Name "sessions")) {
        $id = [string](Get-OptionalProperty -InputObject $s -Name "session_id")
        if (-not $id -or $Baseline -contains $id) { continue }
        if ([string](Get-OptionalProperty -InputObject $s -Name "client_kind") -ne "web") { continue }
        $out += $s
    }
    return , $out
}

$webProcess = $null
$routineId = $null
$exitCode = 2
try {
 do {
    # ------------------------------------------------------------------ routines policy

    $expectedRoutines = Get-ExpectedRoutinesVersion
    $policy = $null
    try { $policy = Get-Json "/v1/routines/policy" } catch { $policy = $null }
    $deployedRoutines = if ($null -ne $policy) { [int](Get-OptionalProperty -InputObject $policy -Name "routines_version") } else { 0 }
    Write-Host "      routines policy: deployed version $deployedRoutines, this checkout expects $expectedRoutines"
    $stale = ($deployedRoutines -lt $expectedRoutines)
    $releaseCloud = switch ($CloudCoreUpdate) { "force" { $true } "never" { $false } default { $stale } }
    if ($stale -and $CloudCoreUpdate -eq "never") {
        throw "the deployed Cloud Core has no routines of version $expectedRoutines (it answers $deployedRoutines); -CloudCoreUpdate never refuses to release"
    }
    if ($releaseCloud -and $releaseBlockers.Blocked) {
        throw ("a Cloud Core release is required (routines $deployedRoutines < $expectedRoutines) but the working tree has " +
               "$($blockerChanges.Count) uncommitted change(s), and a release ships HEAD only. Commit or revert them, then rerun.")
    }
    if ($releaseCloud) {
        Write-Host "      the deployed Cloud Core predates this checkout: releasing it once" -ForegroundColor Yellow
        Invoke-CloudCoreRelease
        $policy = Get-Json "/v1/routines/policy"
        $deployedRoutines = [int](Get-OptionalProperty -InputObject $policy -Name "routines_version")
        if ($deployedRoutines -lt $expectedRoutines) { throw "the Cloud Core still answers routines version $deployedRoutines after a release" }
    }
    else {
        Write-Host "      no Cloud Core release: the deployed routines surface is current"
    }
    $evidence.cloud_policy = [ordered]@{
        expected_version = $expectedRoutines; deployed_version = $deployedRoutines; released = [bool]$releaseCloud
        working_tree_blockers = @($blockerChanges)
    }

    # ------------------------------------------------------------------ the installed agent

    $devicesDoc = Get-Json "/v1/devices"
    $devices = Get-ArrayProperty -InputObject $devicesDoc -Name "devices"
    if ($devices.Count -eq 0 -and $devicesDoc -is [array]) { $devices = @($devicesDoc) }
    $alarmDevices = @()
    $seen = @()
    foreach ($d in $devices) {
        $caps = Get-ArrayProperty -InputObject $d -Name "capabilities"
        $presence = Get-OptionalProperty -InputObject $d -Name "presence"
        $online = if ($null -ne $presence) { [string](Get-OptionalProperty -InputObject $presence -Name "state") } else { "" }
        $seen += ("{0} ({1}; {2} capabilities)" -f (Get-OptionalProperty -InputObject $d -Name "name"), $online, $caps.Count)
        if ($caps -contains "desktop.alarm_start" -and $caps -contains "desktop.alarm_stop") { $alarmDevices += $d }
    }
    $evidence.agent = [ordered]@{ devices = @($seen); alarm_capable = $alarmDevices.Count }
    Write-Host "      devices: $($seen -join '; ')"
    if ($alarmDevices.Count -eq 0) {
        Write-Host ""
        Write-Host "The installed Windows agent does not advertise desktop.alarm_start / desktop.alarm_stop." -ForegroundColor Yellow
        Write-Host "Update it once (one UAC prompt), then rerun this command:" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "    .\scripts\install-device-service.ps1" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "Nothing else was changed or qualified."
        $evidence.verdict = "NEEDS_AGENT_INSTALL"
        $exitCode = 3
        break
    }
    Add-Check -Name "agent.alarm_capable" -Ok $true -Detail "$($alarmDevices.Count) device(s) advertise the alarm pair"

    # ------------------------------------------------------------------ the Core

    # Sessions that exist BEFORE this run can never be this run's session, however recent.
    $baselineIds = @()
    foreach ($s in (Get-ArrayProperty -InputObject (Get-Json "/v1/voice/realtime/sessions?limit=50") -Name "sessions")) {
        $baselineIds += [string](Get-OptionalProperty -InputObject $s -Name "session_id")
    }

    $coreUrl = "http://localhost:$WebPort/core"
    $shell = [ordered]@{ url = $coreUrl; started = $false; ready = $false; ready_after_s = $null; baseline_sessions = $baselineIds.Count }
    if (-not $SkipWeb) {
        $logPath = Join-Path $env:TEMP "$runId-web.log"
        $webProcess = Start-WebShellProcess -RepoRoot $repoRoot -Upstream $BaseUrl -WebPort $WebPort -LogPath $logPath -PnpmPath $PnpmPath
        $shell.started = $true
        $shell.log = $logPath
    }
    $ready = Wait-WebShellReady -Url $coreUrl -TimeoutSec $WebReadyTimeoutSec
    $shell.ready = [bool]$ready.Ready
    $shell.ready_after_s = [math]::Round([double]$ready.ElapsedSec, 1)
    $evidence.web_shell = $shell
    if (-not $ready.Ready) { throw "the web shell did not answer $coreUrl within $WebReadyTimeoutSec s (log: $($shell.log))" }
    $readyAt = [DateTimeOffset]::UtcNow
    Add-Check -Name "core.reachable" -Ok $true -Detail "$coreUrl answered after $($shell.ready_after_s) s"

    # 1. The Core loads real state: the same document the page renders from.
    $uiState = Get-Json "/v1/ui/state"
    $current = Get-OptionalProperty -InputObject $uiState -Name "current"
    $contractVersion = [int](Get-OptionalProperty -InputObject $uiState -Name "contract_version")
    $currentState = if ($null -ne $current) { [string](Get-OptionalProperty -InputObject $current -Name "state") } else { "" }
    $evidence.core_state = [ordered]@{ contract_version = $contractVersion; current_state = $currentState }
    Add-Check -Name "core.real_state" -Ok ($contractVersion -ge 2 -and $currentState -ne "") -Detail "contract v$contractVersion; current=$currentState"

    $evidence.shadow_ready_before = Get-ShadowReadySnapshot
    Write-Host "      SHADOW_READY candidates before the run: $($evidence.shadow_ready_before.count)"

    # ------------------------------------------------------------------ the owner's script

    Write-Host ""
    Write-Host "Open $coreUrl, sign in, and connect voice THERE (the voice control under the Core). Then, in order:" -ForegroundColor Cyan
    Write-Host ("  1. say: {0}" -f $phraseToday)
    Write-Host ("  2. say: {0}" -f $phraseWorld)
    Write-Host ("  3. enable the camera ({0}) and stay in view about 30 seconds" -f $labelEyeOn)
    Write-Host "  4. leave the room for about 2.5 minutes, then come back and sit down"
    Write-Host ("  5. say: {0}" -f $phraseEyeOff)
    Write-Host "  6. a quiet alarm will ring for $AlarmSeconds s and stop by itself"
    Write-Host "  7. disconnect voice on the Core, then press Enter here"
    Write-Host "This script watches the Cloud Core the whole time and continues on its own."
    Write-Host ""

    # ------------------------------------------------------------------ phase V: voice from /core

    $listSessions = { Get-ArrayProperty -InputObject (Get-Json "/v1/voice/realtime/sessions?limit=50") -Name "sessions" }
    $activityProbe = { param($Id) Get-Json "/v1/voice/realtime/sessions/$Id/activity" }
    $waited = Wait-QualificationSession -ListSessions $listSessions -ActivityProbe $activityProbe -BaselineIds $baselineIds `
        -ReadyAt $readyAt -NotBefore $null -TimeoutSec $SessionWaitSec -IntervalSec 5 `
        -OnWaiting { param($Attempt, $Elapsed) if ($Attempt -eq 1) { Write-Host "      waiting for a web voice session that asked activity.explain (up to $SessionWaitSec s)..." } }
    $sessionId = ""
    if ($null -ne $waited.Selected) {
        $sessionId = [string]$waited.Selected.SessionId
        Write-Host "      voice session $sessionId seen after $([math]::Round([double]$waited.ElapsedSec)) s"
    }
    Add-Check -Name "voice.connected_from_core" -Ok ($sessionId -ne "") -Detail $(if ($sessionId) { "web session $sessionId, new since the baseline, asked activity.explain" } else { "no new web session asked activity.explain within $SessionWaitSec s" })

    # ------------------------------------------------------------------ phase P: presence + the eye

    $presenceEvidence = [ordered]@{ states_seen = @(); samples = 0; confidences = @() }
    $eyeEvidence = [ordered]@{ enabled_observed = $false; disabled_observed = $false; disabled_after_s = $null }
    $deadline = (Get-Date).AddSeconds($PresenceWaitSec)
    $statesSeen = @{}
    $t0 = Get-Date
    Write-Host "      watching the Presence Engine (up to $PresenceWaitSec s)..."
    while ((Get-Date) -lt $deadline) {
        $state = $null
        try { $state = Get-Json "/v1/presence/state" } catch { $state = $null }
        if ($null -ne $state) {
            $presenceEvidence.samples++
            $eyeEnabled = [bool](Get-OptionalProperty -InputObject $state -Name "eye_enabled")
            if ($eyeEnabled) { $eyeEvidence.enabled_observed = $true }
            $assertion = Get-OptionalProperty -InputObject $state -Name "assertion"
            if ($null -ne $assertion) {
                $name = [string](Get-OptionalProperty -InputObject $assertion -Name "state")
                $conf = Get-OptionalProperty -InputObject $assertion -Name "confidence"
                if ($name -and $name -ne "unknown" -and -not $statesSeen.ContainsKey($name)) {
                    $statesSeen[$name] = $conf
                    $presenceEvidence.confidences += [ordered]@{ state = $name; confidence = $conf }
                    Write-Host ("      presence: {0} (confidence {1})" -f $name, $conf)
                }
            }
            # The disable is its own fact, never gated on presence (2026-09-06: a real
            # "Gozunu kapat" was reported as never having happened for that reason).
            if ($eyeEvidence.enabled_observed -and -not $eyeEnabled -and -not $eyeEvidence.disabled_observed) {
                $eyeEvidence.disabled_observed = $true
                $eyeEvidence.disabled_after_s = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
                Write-Host "      eye: disabled after $($eyeEvidence.disabled_after_s) s"
            }
            if ($eyeEvidence.disabled_observed -and $statesSeen.Count -ge 2) { break }
            # The owner closed the eye without a second state ever appearing: nothing more
            # can happen here, and waiting out the budget would only hide that.
            if ($eyeEvidence.disabled_observed -and ((Get-Date) - $t0).TotalSeconds -gt 90) { break }
        }
        Start-Sleep -Seconds 5
    }
    $presenceEvidence.states_seen = @($statesSeen.Keys | Sort-Object)
    $evidence.presence = $presenceEvidence
    $evidence.eye = $eyeEvidence

    Add-Check -Name "eye.enabled" -Ok $eyeEvidence.enabled_observed -Detail $(if ($eyeEvidence.enabled_observed) { "eye_enabled=true observed" } else { "the eye was never seen enabled" })
    $presentSeen = @($presenceEvidence.states_seen | Where-Object { $presentStates -contains $_ })
    Add-Check -Name "presence.current_observation" -Ok ($presentSeen.Count -ge 1) -Detail ("a present-family state was asserted while you were in view: " + $(if ($presentSeen.Count) { $presentSeen -join ", " } else { "none (states seen: " + ($presenceEvidence.states_seen -join ", ") + ")" }))
    $allHaveConfidence = $true
    foreach ($entry in $presenceEvidence.confidences) { if ($null -eq $entry.confidence) { $allHaveConfidence = $false } }
    Add-Check -Name "presence.stated_with_confidence" -Ok ($allHaveConfidence -and $presenceEvidence.confidences.Count -gt 0) -Detail "every observed state carried the engine's own confidence"
    Add-Check -Name "presence.transition_observed" -Ok ($statesSeen.Count -ge 2) -Detail ("distinct states: " + ($presenceEvidence.states_seen -join ", "))
    Add-Check -Name "eye.disabled" -Ok $eyeEvidence.disabled_observed -Detail $(if ($eyeEvidence.disabled_observed) { "eye off after $($eyeEvidence.disabled_after_s) s" } else { "the eye was never seen disabled" })

    # ------------------------------------------------------------------ phase A: the alarm

    $dueAt = (Get-Date).ToUniversalTime().AddSeconds($AlarmDelaySec)
    $routineBody = [ordered]@{
        name         = "M18 test alarm"
        trigger_kind = "at"
        trigger      = [ordered]@{ at = $dueAt.ToString("yyyy-MM-ddTHH:mm:ssZ") }
        conditions   = @()
        actions      = @(
            [ordered]@{
                kind   = "alarm"
                detail = [ordered]@{
                    wake_volume    = [ordered]@{ start = 0.05; end = 0.4; ramp_seconds = 10 }
                    max_duration_s = $AlarmSeconds
                }
            }
        )
        source       = "owner"
        source_ref   = "owner-m18:$runId"
    }
    $created = Post-Json "/v1/routines" ($routineBody | ConvertTo-Json -Depth 8 -Compress)
    $routineId = [string](Get-OptionalProperty -InputObject $created -Name "routine_id")
    Write-Host ""
    Write-Host ("In about {0} s a quiet alarm will ramp up over 10 s, ring for {1} s and stop by itself." -f $AlarmDelaySec, $AlarmSeconds) -ForegroundColor Cyan
    Write-Host "      routine $routineId armed for $($routineBody.trigger.at)"
    $waitUntil = $dueAt.AddSeconds(2)
    while ((Get-Date).ToUniversalTime() -lt $waitUntil) { Start-Sleep -Seconds 1 }

    $evaluated = Post-Json "/v1/routines/evaluate" "{}"
    $outcome = $null
    foreach ($o in (Get-ArrayProperty -InputObject $evaluated -Name "outcomes")) {
        if ([string](Get-OptionalProperty -InputObject $o -Name "routine_id") -eq $routineId) { $outcome = $o }
    }
    $outcomeStatus = if ($null -ne $outcome) { [string](Get-OptionalProperty -InputObject $outcome -Name "status") } else { "not evaluated" }
    Add-Check -Name "alarm.routine_triggered" -Ok ($outcomeStatus -eq "triggered") -Detail "evaluate -> $outcomeStatus $(if ($null -ne $outcome) { Get-OptionalProperty -InputObject $outcome -Name 'reason' })"

    $firingsDoc = Get-Json "/v1/routines/$routineId/firings"
    $firings = Get-ArrayProperty -InputObject $firingsDoc -Name "firings"
    $firing = if ($firings.Count -gt 0) { $firings[0] } else { $null }
    $dispatchStatus = if ($null -ne $firing) { [string](Get-OptionalProperty -InputObject $firing -Name "dispatch_status") } else { "" }
    # NOT through an if-expression: that unrolls a one-element array, and the alarm firing
    # has exactly one dispatch result - the 2026-09-06 crash (scripts\lib\OwnerHarness.ps1).
    $dispatchResults = Get-ArrayProperty -InputObject $firing -Name "dispatch_results"
    $alarmResult = if ($dispatchResults.Count -gt 0) { $dispatchResults[0] } else { $null }
    $alarmDetail = if ($null -ne $alarmResult) { Get-OptionalProperty -InputObject $alarmResult -Name "detail" } else { $null }
    $started = if ($null -ne $alarmDetail) { [bool](Get-OptionalProperty -InputObject $alarmDetail -Name "started") } else { $false }
    Add-Check -Name "alarm.dispatched_over_device_path" -Ok ($dispatchStatus -eq "succeeded" -and $started) -Detail ("dispatch_status={0}; device answered started={1}; reason={2}" -f $dispatchStatus, $started, $(if ($null -ne $alarmResult) { Get-OptionalProperty -InputObject $alarmResult -Name "reason" }))
    $startVolume = if ($null -ne $alarmDetail) { Get-OptionalProperty -InputObject $alarmDetail -Name "start_volume" } else { $null }
    $endVolume = if ($null -ne $alarmDetail) { Get-OptionalProperty -InputObject $alarmDetail -Name "end_volume" } else { $null }
    Add-Check -Name "alarm.ramp_reported_by_device" -Ok ($null -ne $startVolume -and $null -ne $endVolume -and [double]$startVolume -lt 0.5 -and [double]$endVolume -le 0.85) -Detail "device will ramp $startVolume -> $endVolume"
    $evidence.alarm = [ordered]@{
        routine_id = $routineId; due_at = $routineBody.trigger.at; outcome = $outcomeStatus
        dispatch_status = $dispatchStatus; dispatch_results = $dispatchResults
    }
    Start-Sleep -Seconds ($AlarmSeconds + 12)

    # ------------------------------------------------------------------ the end of the owner's part

    Write-Host ""
    Read-Host -Prompt "Disconnect voice on the Core, then press Enter" | Out-Null

    # ------------------------------------------------------------------ phase D: the durable record

    $voiceRecord = [ordered]@{ session_id = $sessionId; state = ""; tool_calls = 0; spoken_calls = 0; client_event_kinds = @(); cognitive = @(); web_sessions_since_baseline = 0; overlapping = @() }
    if ($sessionId) {
        $state = Get-Json "/v1/voice/realtime/sessions/$sessionId"
        $closeDeadline = (Get-Date).AddSeconds(60)
        while (([string](Get-OptionalProperty -InputObject $state -Name "state")) -notin @("closed", "expired") -and (Get-Date) -lt $closeDeadline) {
            Start-Sleep -Seconds 5
            $state = Get-Json "/v1/voice/realtime/sessions/$sessionId"
        }
        $activity = Get-Json "/v1/voice/realtime/sessions/$sessionId/activity"
        $calls = Get-ArrayProperty -InputObject $activity -Name "tool_calls"
        $clientEvents = Get-ArrayProperty -InputObject $activity -Name "client_events"
        $kinds = @{}
        foreach ($e in $clientEvents) { $k = [string](Get-OptionalProperty -InputObject $e -Name "kind"); if ($k) { $kinds[$k] = $true } }
        $spoken = @($calls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "status") -eq "succeeded" -and [int](Get-OptionalProperty -InputObject $_ -Name "speech_chars") -gt 0 })
        $cognitive = @($calls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "status") -eq "succeeded" -and [string](Get-OptionalProperty -InputObject $_ -Name "query_kind") -ne "" -and [string](Get-OptionalProperty -InputObject $_ -Name "subsystem") -ne "" })
        $cogSummary = @()
        foreach ($c in $cognitive) { $cogSummary += ("{0}->{1}" -f (Get-OptionalProperty -InputObject $c -Name "query_kind"), (Get-OptionalProperty -InputObject $c -Name "subsystem")) }
        $sessionState = [string](Get-OptionalProperty -InputObject $state -Name "state")
        $voiceRecord.state = $sessionState
        $voiceRecord.tool_calls = $calls.Count
        $voiceRecord.spoken_calls = $spoken.Count
        $voiceRecord.client_event_kinds = @($kinds.Keys | Sort-Object)
        $voiceRecord.cognitive = @($cogSummary)
        # 3/5: the session's own timing events. mic_speech_start is what the client sends when
        # the owner's speech gate opens; first_audio when the first generated audio arrives -
        # and those two are exactly the events the service publishes agent.listening /
        # agent.speaking from, i.e. the Core's real state, not a visual effect.
        Add-Check -Name "voice.owner_spoke_core_listened" -Ok ($kinds.ContainsKey("mic_speech_start")) -Detail ("client events: " + ($voiceRecord.client_event_kinds -join ", "))
        Add-Check -Name "voice.answered_through_realtime" -Ok ($spoken.Count -ge 1) -Detail "$($spoken.Count) succeeded tool call(s) with spoken characters"
        Add-Check -Name "voice.core_reacted_to_speech" -Ok ($kinds.ContainsKey("first_audio")) -Detail "first_audio is the event agent.speaking (the Core's speaking visual) is published from"
        Add-Check -Name "voice.cognitive_request_reached_subsystem" -Ok ($cognitive.Count -ge 1) -Detail $(if ($cognitive.Count) { $cogSummary -join ", " } else { "no succeeded tool call carried the engine's query_kind + subsystem" })
        Add-Check -Name "voice.session_closed" -Ok ($sessionState -in @("closed", "expired")) -Detail "state=$sessionState"
    }
    else {
        foreach ($n in "voice.owner_spoke_core_listened", "voice.answered_through_realtime", "voice.core_reacted_to_speech", "voice.cognitive_request_reached_subsystem", "voice.session_closed") {
            Add-Check -Name $n -Ok $false -Detail "no voice session from this run"
        }
    }

    # 12. No two web sessions of this run were ever open at the same time. A reconnect after
    # a disconnect is sequential and allowed; a second session while the first is live is the
    # duplicate the Core must prevent. A session with no ended_at is open until now.
    $runSessions = Get-WebSessionsSinceBaseline -Baseline $baselineIds
    $intervals = @()
    foreach ($s in $runSessions) {
        $from = ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $s -Name "started_at"))
        $to = ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $s -Name "ended_at"))
        if ($null -eq $from) { continue }
        if ($null -eq $to) { $to = [DateTimeOffset]::UtcNow }
        $intervals += [pscustomobject]@{ Id = [string](Get-OptionalProperty -InputObject $s -Name "session_id"); From = [DateTimeOffset]$from; To = [DateTimeOffset]$to }
    }
    $overlapping = @()
    for ($i = 0; $i -lt $intervals.Count; $i++) {
        for ($j = $i + 1; $j -lt $intervals.Count; $j++) {
            $a = $intervals[$i]; $b = $intervals[$j]
            if ($a.From -lt $b.To -and $b.From -lt $a.To) { $overlapping += ("{0}~{1}" -f $a.Id, $b.Id) }
        }
    }
    $voiceRecord.web_sessions_since_baseline = $runSessions.Count
    $voiceRecord.overlapping = @($overlapping)
    $evidence.voice = $voiceRecord
    Add-Check -Name "voice.single_session" -Ok ($runSessions.Count -ge 1 -and $overlapping.Count -eq 0) -Detail "$($runSessions.Count) web realtime session(s) this run, $($overlapping.Count) overlapping (the single-microphone rule is a client property, proven by the web unit suite)"

    # 8. The World Model carries the owner's presence as a fact, and the bus carried owner.*.
    $worldFacts = Get-ArrayProperty -InputObject (Get-Json "/v1/world/facts") -Name "facts"
    $presenceFact = $null
    foreach ($f in $worldFacts) { if ([string](Get-OptionalProperty -InputObject $f -Name "key") -eq "owner.presence") { $presenceFact = $f } }
    $factValue = if ($null -ne $presenceFact) { [string](Get-OptionalProperty -InputObject $presenceFact -Name "value") } else { "" }
    $factConfidence = if ($null -ne $presenceFact) { Get-OptionalProperty -InputObject $presenceFact -Name "confidence" } else { $null }
    $evidence.world = [ordered]@{ owner_presence = $factValue; confidence = $factConfidence; fact_present = ($null -ne $presenceFact) }
    Add-Check -Name "world.presence_fact" -Ok ($null -ne $presenceFact) -Detail $(if ($null -ne $presenceFact) { "owner.presence=$factValue (confidence $factConfidence)" } else { "no owner.presence fact in the World Model" })
    $busTail = Get-ArrayProperty -InputObject (Get-Json "/v1/ui/state?limit=64") -Name "events"
    $ownerEvents = @($busTail | Where-Object { ([string](Get-OptionalProperty -InputObject $_ -Name "state")).StartsWith("owner.") })
    $voiceEvents = @($busTail | Where-Object { ([string](Get-OptionalProperty -InputObject $_ -Name "subsystem")) -eq "voice" })
    Add-Check -Name "core.presence_event_published" -Ok ($ownerEvents.Count -ge 1) -Detail "$($ownerEvents.Count) owner.* event(s) in the bus tail of 64 ($($voiceEvents.Count) from the voice subsystem)"

    # ------------------------------------------------------------------ the ledger

    $since = $startedAt.ToString("yyyy-MM-ddTHH:mm:ssZ")
    # 9. The eye went off BY VOICE: the realtime service writes eye.disabled with reason
    # "voice:<matched phrase>"; the control writes "owner_stop". Only the former proves it.
    $eyeDisabledRows = Get-LedgerSince -SinceIso $since -EventType "eye.disabled"
    $viaVoice = @($eyeDisabledRows | Where-Object { (Get-DetailReason -Event $_).StartsWith("voice:") })
    $eyeDetail = "no eye.disabled row since the run started"
    if ($viaVoice.Count -gt 0) { $eyeDetail = "eye.disabled with reason " + (Get-DetailReason -Event $viaVoice[0]) }
    elseif ($eyeDisabledRows.Count -gt 0) { $eyeDetail = "the eye was disabled, but not by voice (reason " + (Get-DetailReason -Event $eyeDisabledRows[0]) + ")" }
    Add-Check -Name "eye.disabled_by_voice" -Ok ($viaVoice.Count -ge 1) -Detail $eyeDetail
    # Assignment first, .Count second - never .Count on a parenthesised call (OwnerHarness.ps1).
    $eyeEnabledRows = Get-LedgerSince -SinceIso $since -EventType "eye.enabled"
    $transitionRows = Get-LedgerSince -SinceIso $since -EventType "presence.state_changed"
    $triggeredRows = Get-LedgerSince -SinceIso $since -EventType "routine.triggered"
    $executedRows = Get-LedgerSince -SinceIso $since -EventType "routine.executed"
    Add-Check -Name "ledger.eye_enabled_recorded" -Ok ($eyeEnabledRows.Count -ge 1) -Detail "eye.enabled=$($eyeEnabledRows.Count)"
    Add-Check -Name "ledger.presence_transition_recorded" -Ok ($transitionRows.Count -ge 1) -Detail "presence.state_changed=$($transitionRows.Count)"
    Add-Check -Name "ledger.routine_fired_and_executed" -Ok ($triggeredRows.Count -ge 1 -and $executedRows.Count -ge 1) -Detail "routine.triggered=$($triggeredRows.Count) routine.executed=$($executedRows.Count)"

    # No raw camera archive: every presence row is the seven-field summary and nothing else.
    $allDoc = Get-Json ("/v1/ledger/events?since=" + [uri]::EscapeDataString($since) + "&limit=200")
    $events = Get-ArrayProperty -InputObject $allDoc -Name "events"
    $types = @{}
    foreach ($e in $events) { $t = [string](Get-OptionalProperty -InputObject $e -Name "event_type"); if ($t) { $types[$t] = 1 + $(if ($types.ContainsKey($t)) { $types[$t] } else { 0 }) } }
    $typeSummary = ($types.Keys | Sort-Object | ForEach-Object { "{0}={1}" -f $_, $types[$_] }) -join ", "
    Write-Host "      ledger since $since : $typeSummary"
    $privacyViolations = @()
    foreach ($e in $events) {
        $t = [string](Get-OptionalProperty -InputObject $e -Name "event_type")
        if ($t -match "observation|frame|image|snapshot") { $privacyViolations += "event type '$t'" }
        if ($t -notlike "presence.*") { continue }
        $detail = Get-OptionalProperty -InputObject $e -Name "detail_json"
        if ($null -eq $detail) { continue }
        foreach ($prop in $detail.PSObject.Properties) {
            if ($presenceDetailAllowed -notcontains $prop.Name) { $privacyViolations += "$t carries '$($prop.Name)'" }
            $value = [string]$prop.Value
            if ($value.Length -gt 300) { $privacyViolations += "$t.$($prop.Name) is $($value.Length) chars" }
            if ($value -match '^[A-Za-z0-9+/=]{200,}$') { $privacyViolations += "$t.$($prop.Name) is base64-shaped" }
        }
    }
    Add-Check -Name "privacy.no_raw_camera_archive" -Ok ($privacyViolations.Count -eq 0) -Detail $(if ($privacyViolations.Count -eq 0) { "presence rows carry only the structured summary" } else { $privacyViolations -join "; " })
    $evidence.ledger = [ordered]@{ since = $since; event_types = $typeSummary; privacy_violations = @($privacyViolations) }

    # ------------------------------------------------------------------ authority

    $evidence.shadow_ready_after = Get-ShadowReadySnapshot
    $unchanged = (($evidence.shadow_ready_before.statuses -join "|") -eq ($evidence.shadow_ready_after.statuses -join "|"))
    Add-Check -Name "authority.nothing_deployed" -Ok $unchanged -Detail "SHADOW_READY set before == after ($($evidence.shadow_ready_after.count) candidate(s)); this run changed no candidate"

    $failed = @($evidence.checks | Where-Object { -not $_.ok })
    if ($failed.Count -eq 0) { $evidence.verdict = "PASS"; $exitCode = 0 } else { $evidence.verdict = "FAIL"; $exitCode = 2 }
 } while ($false)
}
finally {
    # 11. Everything this run opened is closed here, on every path.
    if ($routineId) {
        try { Post-Json "/v1/routines/$routineId/cancel" '{"reason":"owner-m18 finished"}' | Out-Null } catch { }
    }
    if ($OutFile) {
        $evidence.finished_at = (Get-Date).ToUniversalTime().ToString("o")
        $json = $evidence | ConvertTo-Json -Depth 14
        [System.IO.File]::WriteAllText($OutFile, $json, (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "      evidence written: $OutFile"
    }
    if ($null -ne $webProcess -and -not $webProcess.HasExited) {
        try { Stop-Process -Id $webProcess.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
    try { Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions/$mintedId/revoke" -Headers $headers -Body "{}" | Out-Null } catch { }
    $headers = $null; $token = $null
}

Write-Host "OWNER M18: $($evidence.verdict)" -ForegroundColor $(if ($evidence.verdict -eq "PASS") { "Green" } elseif ($evidence.verdict -eq "NEEDS_AGENT_INSTALL") { "Yellow" } else { "Red" })
exit $exitCode
