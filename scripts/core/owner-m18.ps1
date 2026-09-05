<#
.SYNOPSIS
    M18 owner action: the Core, the camera, a presence transition, and one short alarm -
    in one command, against the real Cloud Core and the real Windows agent - with every
    claim asserted from durable evidence.

.DESCRIPTION
    One command, in this order:

      1. preflight: working-tree release blockers, Cloud Core health, then the deployed
         Cloud Core's routines policy. If the deployed core predates M18 (no /v1/routines),
         ONE transactional release ships it (build, migrate 0017..0020, recreate api only,
         health, rollback on failure). No Windows component is touched by this script.
      2. the installed Windows agent must advertise desktop.alarm_start. If it does not, the
         script stops with verdict NEEDS_AGENT_INSTALL and prints the exact install command
         (UAC - a human-only step). It never installs anything itself.
      3. the web shell starts and /core is probed until it answers. You sign in, enable the
         camera, stay ~30 s, leave the room for ~1 minute, come back, then say "Gozunu kapat"
         (or click the disable control). The script polls the Presence Engine the whole
         time: it needs to SEE at least two distinct presence states, each with the engine's
         own confidence, and then the eye going off durably.
      4. a one-shot routine is created that rings the alarm ~45 s later, quietly (ramp
         0.05 -> 0.40 over 10 s), for 15 s, and stops itself. The script evaluates the
         routine when it is due, asserts the firing dispatched successfully over the device
         path, and asks you whether you heard it ramp up and stop.
      5. the Activity Ledger is read since the run started: presence transitions, the eye
         going off, the routine firing and executing - and, structurally, that no presence
         row carries anything but the seven-field summary: no image, no frame, nothing
         base64-shaped. The SHADOW_READY candidate count is asserted unchanged: this run
         deploys nothing and could not.

    The owner credential is read from the local DPAPI store (or a masked prompt), exchanged
    for one session that is revoked at the end, and never printed or stored. Expected last
    line: OWNER M18: PASS.

.EXAMPLE
    .\scripts\core\owner-m18.ps1 -OutFile m18-1.json
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    [int]$WebPort = 3000,
    [string]$BaseUrl = "",
    [string]$OutFile = "",
    # auto: release the Cloud Core only when it predates M18; never: refuse; force: always.
    [ValidateSet("auto", "never", "force")][string]$CloudCoreUpdate = "auto",
    # Never start the web shell (it must already answer on -WebPort).
    [switch]$SkipWeb,
    # Skip the camera/presence part (proves the alarm and the ledger only).
    [switch]$SkipPresence,
    [string]$PnpmPath = "pnpm",
    [ValidateRange(20, 600)][int]$AlarmDelaySec = 45,
    [ValidateRange(10, 120)][int]$AlarmSeconds = 15,
    [ValidateRange(60, 1800)][int]$PresenceWaitSec = 300,
    [ValidateRange(10, 900)][int]$WebReadyTimeoutSec = 180
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\RepoState.ps1")
. (Join-Path $repoRoot "scripts\lib\VoiceShell.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")

if (-not $BaseUrl) { $BaseUrl = "http://${BrokerHost}:$ApiPort" }
$BaseUrl = $BaseUrl.TrimEnd('/')
$runId = "owner-m18-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$startedAt = (Get-Date).ToUniversalTime()

function Get-OptionalProperty {
    param($InputObject, [string]$Name)
    if ($null -eq $InputObject) { return $null }
    $prop = $InputObject.PSObject.Properties[$Name]
    if ($null -ne $prop) { return $prop.Value }
    return $null
}

function Get-ArrayProperty {
    # An absent or null property is an EMPTY array, never @($null): a missing list must read
    # as "nothing there", which @($null).Count = 1 does not.
    param($InputObject, [string]$Name)
    $value = Get-OptionalProperty -InputObject $InputObject -Name $Name
    if ($null -eq $value) { return , @() }
    return , @($value)
}

# Turkish text from character codes so this file stays pure ASCII (Windows PowerShell 5.1
# reads a BOM-less file as ANSI and would mangle the letters).
$o_uml = [char]0x00F6; $u_uml = [char]0x00FC
$eyeOffPhrase = "G" + $o_uml + "z" + $u_uml + "n" + $u_uml + " kapat"

# What a presence ledger row may carry. Anything outside this set - and in particular
# anything image-shaped - is a privacy failure, whatever else passed.
$presenceDetailAllowed = @("to_state", "confidence", "reason", "signal_sources", "signal_count")

$evidence = [ordered]@{
    run_id              = $runId
    started_at          = $startedAt.ToString("o")
    cloud               = $BaseUrl
    cloud_policy        = $null
    agent               = $null
    web_shell           = $null
    shadow_ready_before = $null
    presence            = $null
    eye                 = $null
    alarm               = $null
    owner_verdicts      = $null
    ledger              = $null
    shadow_ready_after  = $null
    checks              = @()
    verdict             = "FAIL"
}

Write-Host "PagentOS owner M18 ($runId)"

# ------------------------------------------------------------------ preflight

$releaseBlockers = Get-ReleaseBlockers -RepoRoot $repoRoot
if ($releaseBlockers.Blocked) {
    Write-Host "      working tree: $(@($releaseBlockers.Changes).Count) uncommitted change(s) - a Cloud Core release would be refused:" -ForegroundColor Yellow
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
    $items = (Get-ArrayProperty -InputObject $ready -Name "awaiting_approval")
    $statuses = @()
    foreach ($item in $items) { $statuses += ("{0}:{1}" -f (Get-OptionalProperty -InputObject $item -Name "opportunity_id"), (Get-OptionalProperty -InputObject $item -Name "status")) }
    return [ordered]@{ count = $items.Count; statuses = @($statuses | Sort-Object) }
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
               "$(@($releaseBlockers.Changes).Count) uncommitted change(s), and a release ships HEAD only. Commit or revert them, then rerun.")
    }
    if ($releaseCloud) {
        Write-Host "      the deployed Cloud Core predates M18: releasing it once" -ForegroundColor Yellow
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
        working_tree_blockers = @($releaseBlockers.Changes)
    }

    # ------------------------------------------------------------------ the installed agent

    $devicesDoc = Get-Json "/v1/devices"
    $devices = (Get-ArrayProperty -InputObject $devicesDoc -Name "devices")
    if ($devices.Count -eq 0 -and $devicesDoc -is [array]) { $devices = @($devicesDoc) }  # a bare list, if the route ever answers one
    $alarmDevices = @()
    $seen = @()
    foreach ($d in $devices) {
        $caps = (Get-ArrayProperty -InputObject $d -Name "capabilities")
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
        Write-Host "That build predates M18. Update it once (one UAC prompt), then rerun this command:" -ForegroundColor Yellow
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

    $coreUrl = "http://localhost:$WebPort/core"
    $shell = [ordered]@{ url = $coreUrl; started = $false; ready = $false; ready_after_s = $null }
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
    Add-Check -Name "core.reachable" -Ok $true -Detail "$coreUrl answered after $($shell.ready_after_s) s"

    $evidence.shadow_ready_before = Get-ShadowReadySnapshot
    Write-Host "      SHADOW_READY candidates before the run: $($evidence.shadow_ready_before.count)"

    # ------------------------------------------------------------------ presence + the eye

    $presenceEvidence = [ordered]@{ skipped = [bool]$SkipPresence; states_seen = @(); samples = 0; confidences = @() }
    $eyeEvidence = [ordered]@{ disabled_observed = $false; disabled_after_s = $null }
    if (-not $SkipPresence) {
        Write-Host ""
        Write-Host "Open $coreUrl and sign in. Then:" -ForegroundColor Cyan
        Write-Host "  1. enable the camera (the eye control under the Core) and stay in view for about 30 seconds;"
        Write-Host "  2. leave the room for about a minute, then come back;"
        Write-Host ("  3. say '{0}' - or click the disable control." -f $eyeOffPhrase)
        Write-Host "This script watches the Presence Engine meanwhile (up to $PresenceWaitSec s) and continues on its own."
        Write-Host ""

        $deadline = (Get-Date).AddSeconds($PresenceWaitSec)
        $statesSeen = @{}
        $lastState = ""
        $eyeWasEnabled = $false
        $t0 = Get-Date
        while ((Get-Date) -lt $deadline) {
            $state = $null
            try { $state = Get-Json "/v1/presence/state" } catch { $state = $null }
            if ($null -ne $state) {
                $presenceEvidence.samples++
                $eyeEnabled = [bool](Get-OptionalProperty -InputObject $state -Name "eye_enabled")
                if ($eyeEnabled) { $eyeWasEnabled = $true }
                $assertion = Get-OptionalProperty -InputObject $state -Name "assertion"
                if ($null -ne $assertion) {
                    $name = [string](Get-OptionalProperty -InputObject $assertion -Name "state")
                    $conf = Get-OptionalProperty -InputObject $assertion -Name "confidence"
                    if ($name -and $name -ne "unknown") {
                        if (-not $statesSeen.ContainsKey($name)) {
                            $statesSeen[$name] = $conf
                            $presenceEvidence.confidences += [ordered]@{ state = $name; confidence = $conf }
                            Write-Host ("      presence: {0} (confidence {1})" -f $name, $conf)
                        }
                    }
                    if ($name -ne $lastState) { $lastState = $name }
                }
                if ($eyeWasEnabled -and -not $eyeEnabled -and $statesSeen.Count -ge 2) {
                    $eyeEvidence.disabled_observed = $true
                    $eyeEvidence.disabled_after_s = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
                    break
                }
            }
            Start-Sleep -Seconds 5
        }
        $presenceEvidence.states_seen = @($statesSeen.Keys | Sort-Object)
        $presenceEvidence.eye_was_enabled = $eyeWasEnabled

        Add-Check -Name "presence.transition_observed" -Ok ($statesSeen.Count -ge 2) -Detail ("states seen: " + ($presenceEvidence.states_seen -join ", "))
        $allHaveConfidence = $true
        foreach ($entry in $presenceEvidence.confidences) { if ($null -eq $entry.confidence) { $allHaveConfidence = $false } }
        Add-Check -Name "presence.stated_with_confidence" -Ok ($allHaveConfidence -and $presenceEvidence.confidences.Count -gt 0) -Detail "every observed state carried the engine's own confidence"
        Add-Check -Name "eye.enabled_then_disabled" -Ok ($eyeWasEnabled -and $eyeEvidence.disabled_observed) -Detail $(if ($eyeEvidence.disabled_observed) { "eye off after $($eyeEvidence.disabled_after_s) s" } else { "the eye was never seen enabled and then disabled" })
    }
    $evidence.presence = $presenceEvidence
    $evidence.eye = $eyeEvidence

    # ------------------------------------------------------------------ the alarm

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
    $firings = (Get-ArrayProperty -InputObject $firingsDoc -Name "firings")
    $firing = if ($firings.Count -gt 0) { $firings[0] } else { $null }
    $dispatchStatus = if ($null -ne $firing) { [string](Get-OptionalProperty -InputObject $firing -Name "dispatch_status") } else { "" }
    $dispatchResults = if ($null -ne $firing) { (Get-ArrayProperty -InputObject $firing -Name "dispatch_results") } else { @() }
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

    # Let it ring and stop by itself before asking - the answer is about the whole thing.
    Start-Sleep -Seconds ($AlarmSeconds + 12)
    $heard = Read-Host -Prompt "Did you hear the alarm ramp up quietly and then stop by itself? (y/n)"
    $coreShowed = Read-Host -Prompt "Did the Core show the camera state, your presence, and the candidate awaiting approval? (y/n)"
    $evidence.owner_verdicts = [ordered]@{ alarm_heard_and_stopped = $heard; core_showed_state = $coreShowed }
    Add-Check -Name "owner.alarm_heard_and_stopped" -Ok ($heard -match '^[yYeE]') -Detail "owner answered '$heard'"
    Add-Check -Name "owner.core_showed_state" -Ok ($coreShowed -match '^[yYeE]') -Detail "owner answered '$coreShowed'"

    # ------------------------------------------------------------------ the ledger

    $since = $startedAt.ToString("yyyy-MM-ddTHH:mm:ssZ")
    $eventsDoc = Get-Json ("/v1/ledger/events?since=" + [uri]::EscapeDataString($since) + "&limit=200")
    $events = (Get-ArrayProperty -InputObject $eventsDoc -Name "events")
    $types = @{}
    foreach ($e in $events) { $t = [string](Get-OptionalProperty -InputObject $e -Name "event_type"); if ($t) { $types[$t] = 1 + $(if ($types.ContainsKey($t)) { $types[$t] } else { 0 }) } }
    $typeSummary = ($types.Keys | Sort-Object | ForEach-Object { "{0}={1}" -f $_, $types[$_] }) -join ", "
    Write-Host "      ledger since $since : $typeSummary"

    Add-Check -Name "ledger.routine_fired_and_executed" -Ok ($types.ContainsKey("routine.triggered") -and $types.ContainsKey("routine.executed")) -Detail "routine.triggered + routine.executed present"
    if (-not $SkipPresence) {
        Add-Check -Name "ledger.eye_disabled_recorded" -Ok ($types.ContainsKey("eye.disabled")) -Detail "eye.disabled present"
        Add-Check -Name "ledger.presence_transition_recorded" -Ok ($types.ContainsKey("presence.state_changed")) -Detail "presence.state_changed present"
    }

    # No raw camera archive: every presence row is the seven-field summary and nothing else.
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
