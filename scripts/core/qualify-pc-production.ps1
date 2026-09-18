<#
.SYNOPSIS
    PC v1 production round: the rows whose runtime proof reads "uretim turu bekliyor
    (Karar 0)" (B11 notifications, B12 event notices, B13 alarm history, B15 morning
    briefing), asked of the LIVE Cloud Core and recorded as one evidence file.

.DESCRIPTION
    Same discipline as qualify-native-production.ps1: an owner session minted from the stored
    credential (and revoked at the end), the owner's own REST reads, and the voice tools
    through the real realtime tool-call relay. Every row's verdict is taken from what
    production answered - never from an exit code or a receipt the run did not read back.

    What this round deliberately does NOT do:
      * fire an alarm (a test alarm plays the owner's wake song and wakes the display - that
        is an owner-timed step, not something to start at an arbitrary hour);
      * trigger rollback / backup-failure / approval notices (no safe production path; see
        the per-row reasons in the evidence).

    One write is made on purpose: a research request for a device that does not exist
    (-SkipFailureProbe to leave it out). Production answers 409 and records a FAILED task
    and its `task.failed` notification (row 382); the notice later reaches the owner as a
    toast titled with the probe text below, which says what it was.

    Exit 0 when every asked row is PROVEN_REAL, 1 when the round ran and some row did not
    prove, 2 when production could not be asked.
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = "http://100.90.158.26:8001",
    [switch]$SkipFailureProbe,
    [int]$ToolTimeoutSec = 120,
    [string]$EvidenceDir = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
. (Join-Path $repoRoot "scripts\lib\OwnerHarness.ps1")

if (-not $EvidenceDir) { $EvidenceDir = Join-Path $repoRoot "docs\evidence" }

$ProbeText = "PagentOS uretim turu denetimi (bilerek basarisiz arastirma)"

function Get-ServedRelease {
    $health = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/system/health" -TimeoutSec 30
    $release = Get-OptionalProperty -InputObject $health -Name "release"
    return [pscustomobject]@{
        status  = [string](Get-OptionalProperty -InputObject $health -Name "status")
        version = [string](Get-OptionalProperty -InputObject $release -Name "version")
        health  = $health
    }
}

$rows = [ordered]@{}
function Set-Row {
    param([string]$Id, [bool]$Proven, [string]$Claim, $Observed, [string]$Reason = "")
    $rows[$Id] = [ordered]@{
        status   = $(if ($Proven) { "PROVEN_REAL" } else { "NOT_YET_PROVEN" })
        claim    = $Claim
        observed = $Observed
        reason   = $(if ($Proven) { $null } else { $Reason })
    }
    $color = if ($Proven) { "Green" } else { "Yellow" }
    Write-Host ("  {0,-4} {1,-15} {2}" -f $Id, $rows[$Id].status, $Claim) -ForegroundColor $color
    if (-not $Proven -and $Reason) { Write-Host "       $Reason" -ForegroundColor Yellow }
}

$startedAt = (Get-Date).ToUniversalTime()
Write-Host "PC v1 production round against $BaseUrl"
try { $before = Get-ServedRelease } catch { Write-Host "production is unreachable: $($_.Exception.Message)"; exit 2 }
Write-Host "release : $($before.version) ($($before.status))"

$credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL"
if (-not $credential) { Write-Host "no stored owner credential"; exit 2 }
$body = @{ owner_credential = $credential; client_kind = "cli"; label = "qualify-pc-production" } | ConvertTo-Json -Compress
$issued = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body $body -TimeoutSec 30
$credential = $null; $body = $null
$headers = @{ Authorization = "Bearer $($issued.token)" }
$ownerSessionId = [string](Get-OptionalProperty -InputObject $issued -Name "session_id")

$sid = $null
$failureProbe = $null
try {
    # ------------------------------------------------------------ B11 notifications
    Write-Host ""
    Write-Host "B11 / B12 notifications"
    $inbox = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/notifications?unread=false&limit=200" -Headers $headers -TimeoutSec 30
    $inboxRows = Get-ArrayProperty -InputObject $inbox -Name "notifications"

    if (-not $SkipFailureProbe) {
        $probeBody = @{ input = $ProbeText; target_device = "qualification-no-such-device" } | ConvertTo-Json -Compress
        $status = $null; $detail = $null
        try {
            $answer = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/research" -Headers $headers -Body $probeBody -TimeoutSec 60
            $status = 202; $detail = $answer
        }
        catch {
            $status = Get-OptionalProperty -InputObject $_.Exception -Name "StatusCode"
            $text = $_.Exception.Message
            $jsonStart = $text.IndexOf("{")
            if ($jsonStart -ge 0) { try { $detail = $text.Substring($jsonStart) | ConvertFrom-Json } catch { $detail = $text } } else { $detail = $text }
        }
        $inner = Get-OptionalProperty -InputObject $detail -Name "detail"
        $taskId = [string](Get-OptionalProperty -InputObject $inner -Name "task_id")
        $failureProbe = [ordered]@{ http_status = $status; task_id = $taskId; error_class = Get-OptionalProperty -InputObject $inner -Name "error_class" }
        Write-Host "  failure probe: HTTP $status task $taskId"
    }

    $history = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/notifications/history?limit=500" -Headers $headers -TimeoutSec 30
    $historyRows = Get-ArrayProperty -InputObject $history -Name "history"
    $kinds = @($historyRows | ForEach-Object { [string](Get-OptionalProperty -InputObject $_ -Name "kind") } | Sort-Object -Unique)
    $priorities = @($historyRows | ForEach-Object { [string](Get-OptionalProperty -InputObject $_ -Name "priority") } | Sort-Object -Unique)
    $oldest = $historyRows | ForEach-Object { [string](Get-OptionalProperty -InputObject $_ -Name "created_at") } | Sort-Object | Select-Object -First 1
    $delivered = @($historyRows | Where-Object { Get-OptionalProperty -InputObject $_ -Name "delivered_at" })
    $undelivered = @($historyRows | Where-Object { -not (Get-OptionalProperty -InputObject $_ -Name "delivered_at") })
    $attemptedToast = @($historyRows | Where-Object { (Get-ArrayProperty -InputObject $_ -Name "attempted") -contains "toast" })
    $superseded = @($historyRows | Where-Object { Get-OptionalProperty -InputObject $_ -Name "superseded_at" })
    $deferred = @($historyRows | Where-Object { Get-OptionalProperty -InputObject $_ -Name "deferred_until" })
    $via = @($delivered | ForEach-Object { [string](Get-OptionalProperty -InputObject $_ -Name "delivered_via") } | Sort-Object -Unique)

    $probeRow = $null
    if ($failureProbe -and $failureProbe.task_id) {
        $probeRow = $historyRows | Where-Object {
            [string](Get-OptionalProperty -InputObject $_ -Name "kind") -eq "task.failed" -and
            [string](Get-OptionalProperty -InputObject $_ -Name "group_key") -eq "task:$($failureProbe.task_id)"
        } | Select-Object -First 1
    }

    Set-Row "367" ($historyRows.Count -gt 0 -and $null -ne $oldest) "notifications are rows in production's table" `
        ([ordered]@{ history_rows = $historyRows.Count; oldest_created_at = $oldest; probe_row_written = [bool]$probeRow; probe_row = $probeRow }) "no notification row in production"
    Set-Row "368" ($null -ne (Get-OptionalProperty -InputObject $inbox -Name "unread")) "the inbox is read from the table (unread count + rows)" `
        ([ordered]@{ inbox_rows = $inboxRows.Count; unread = Get-OptionalProperty -InputObject $inbox -Name "unread" }) "inbox answered without an unread count"

    # The probe had to be a REAL notification to prove the path, but once proven it is this
    # round's own noise. Owner report 2026-09-18: three "İş başarısız oldu" rows from these
    # rounds sat unread in the owner's inbox beside the real ones. Marked read here, after
    # every assertion above has seen it - never deleted, so the history still shows it.
    if ($probeRow) {
        $probeId = [string](Get-OptionalProperty -InputObject $probeRow -Name "id")
        if ($probeId) {
            try {
                $null = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/notifications/$probeId/read" -Headers $headers -Body "{}" -TimeoutSec 30
                $failureProbe.notice_marked_read = $true
                Write-Host "  failure probe: its notice marked read (the owner's inbox keeps only real ones)"
            }
            catch {
                $failureProbe.notice_marked_read = $false
                Write-Host "  failure probe: could not mark its notice read ($($_.Exception.Message))"
            }
        }
    }
    Set-Row "377" ($historyRows.Count -gt 0) "delivery history lists delivered and unreached notices" `
        ([ordered]@{ delivered = $delivered.Count; not_delivered = $undelivered.Count; delivered_via = $via }) "empty history"
    Set-Row "378" ($priorities.Count -ge 2) "more than one priority level occurs in production" `
        ([ordered]@{ priorities = $priorities }) "only '$($priorities -join ',')' observed"
    # 379 is judged on THIS run's probe notice when the run falls in the owner's quiet hours
    # (23:00-07:30 Europe/Istanbul): it must carry deferred_until at the next 07:30.
    $istanbul = [TimeZoneInfo]::FindSystemTimeZoneById("Turkey Standard Time")
    $localNow = [TimeZoneInfo]::ConvertTimeFromUtc((Get-Date).ToUniversalTime(), $istanbul)
    $inQuiet = ($localNow.TimeOfDay -ge [TimeSpan]"23:00:00") -or ($localNow.TimeOfDay -lt [TimeSpan]"07:30:00")
    $probeDeferred = if ($probeRow) { [string](Get-OptionalProperty -InputObject $probeRow -Name "deferred_until") } else { "" }
    $probeDeferredLocal = ""
    if ($probeDeferred) {
        $probeDeferredLocal = [TimeZoneInfo]::ConvertTimeFromUtc(([DateTimeOffset]::Parse($probeDeferred)).UtcDateTime, $istanbul).ToString("yyyy-MM-dd HH:mm")
    }
    $quietProven = $inQuiet -and $probeDeferredLocal.EndsWith("07:30")
    Set-Row "379" $quietProven "this run's non-urgent notice, recorded in the owner's quiet hours, waits until 07:30 Istanbul" `
        ([ordered]@{ run_local_time = $localNow.ToString("yyyy-MM-dd HH:mm"); in_quiet_hours = $inQuiet; probe_deferred_until_utc = $probeDeferred; probe_deferred_until_local = $probeDeferredLocal; deferred_rows_in_history = $deferred.Count }) `
        $(if ($inQuiet) { "the probe notice carries no 07:30 deferral ('$probeDeferredLocal')" } else { "the round ran outside quiet hours; 379 is judged only inside them" })
    Set-Row "380" ($superseded.Count -gt 0) "an older unread sibling in the same group is superseded" `
        ([ordered]@{ superseded_rows = $superseded.Count }) "no superseded row in production history yet"
    Set-Row "389" ($attemptedToast.Count -gt 0 -and $via.Count -gt 0) "the ladder attempted the device toast and recorded where each notice landed" `
        ([ordered]@{ toast_attempted = $attemptedToast.Count; delivered_via = $via; ladder_last_run = Get-OptionalProperty -InputObject (Get-OptionalProperty -InputObject (Get-OptionalProperty -InputObject $before.health -Name "checks") -Name "retention") -Name "last_run_at" }) "no ladder attempt recorded yet"

    $eventRows = [ordered]@{ "381" = "task.completed"; "382" = "task.failed"; "383" = "owner.approval_required"; "384" = "release.rolled_back"; "385" = "backup.failed"; "386" = "alarm.failed"; "387" = "research.finished"; "388" = "selfdev.candidate_ready" }
    foreach ($id in $eventRows.Keys) {
        $kind = $eventRows[$id]
        $matching = @($historyRows | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "kind") -eq $kind })
        $proven = $matching.Count -gt 0
        $reason = "no '$kind' notice in production history"
        if ($id -eq "382" -and $failureProbe) {
            $proven = [bool]$probeRow
            $reason = "the failure probe (HTTP $($failureProbe.http_status)) left no task.failed notice for task $($failureProbe.task_id)"
        }
        Set-Row $id $proven "a '$kind' notice exists in production" `
            ([ordered]@{ rows = $matching.Count; latest = ($matching | ForEach-Object { [string](Get-OptionalProperty -InputObject $_ -Name "created_at") } | Sort-Object | Select-Object -Last 1) }) $reason
    }

    # ------------------------------------------------------------------ B13 alarms
    Write-Host ""
    Write-Host "B13 alarm history (read only; no alarm is fired)"
    $alarmHistory = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/alarms/history?include_tests=true&limit=100" -Headers $headers -TimeoutSec 30
    $alarmEvents = Get-ArrayProperty -InputObject $alarmHistory -Name "history"
    $eventTypes = @($alarmEvents | ForEach-Object { [string](Get-OptionalProperty -InputObject $_ -Name "event_type") } | Sort-Object -Unique)
    Set-Row "285" ($alarmEvents.Count -gt 0) "alarm history is served from the ledger" `
        ([ordered]@{ events = $alarmEvents.Count; event_types = $eventTypes }) "no alarm history in production"
    $alarms = Get-ArrayProperty -InputObject (Invoke-JsonUtf8 -Uri "$BaseUrl/v1/alarms?include_terminal=true" -Headers $headers -TimeoutSec 30) -Name "alarms"
    $snoozed = @($alarms | Where-Object { [int](Get-OptionalProperty -InputObject $_ -Name "snooze_count") -gt 0 })
    Set-Row "259" ($snoozed.Count -gt 0 -or ($eventTypes -contains "alarm.snoozed")) "an alarm was snoozed in production" `
        ([ordered]@{ snoozed_alarms = $snoozed.Count; alarms = $alarms.Count }) "no snooze recorded yet (needs a ringing alarm: owner-timed)"
    $fallback = @($alarms | Where-Object {
        [string](Get-OptionalProperty -InputObject $_ -Name "greeting_failure") -eq "no_tts_key" -or
        [string](Get-OptionalProperty -InputObject $_ -Name "media_kind") -eq "tone_fallback" })
    Set-Row "267" ($fallback.Count -gt 0) "a production alarm recorded its fallback honestly (no_tts_key / tone_fallback)" `
        ([ordered]@{ fallback_alarms = $fallback.Count }) "no alarm with a recorded fallback yet"

    # --------------------------------------------------------------- B15 briefing
    Write-Host ""
    Write-Host "B15 morning briefing (voice tool relay; nothing reaches the device)"
    $session = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/voice/realtime/sessions" -Headers $headers -Body "{}" -TimeoutSec 60
    $sid = [string]$session.session_id

    function Invoke-Tool {
        param([string]$ToolName, [hashtable]$Arguments = @{})
        $payload = @{ call_id = "qpc-$([guid]::NewGuid())"; name = $ToolName; arguments = $Arguments } | ConvertTo-Json -Depth 6 -Compress
        $answer = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/voice/realtime/sessions/$sid/tool-calls" -Headers $headers -Body $payload -TimeoutSec $ToolTimeoutSec
        $result = Get-OptionalProperty -InputObject $answer -Name "result"
        return [pscustomobject]@{
            status   = [string](Get-OptionalProperty -InputObject $answer -Name "status")
            terminal = [string](Get-OptionalProperty -InputObject $result -Name "terminal_status")
            speech   = [string](Get-OptionalProperty -InputObject $result -Name "speech")
            server   = Get-OptionalProperty -InputObject (Get-OptionalProperty -InputObject $result -Name "observed_after") -Name "server"
            result   = $result
        }
    }

    $clock = Invoke-Tool "clock.now"
    Set-Row "271" ($clock.status -eq "succeeded" -and $clock.speech -match "\d") "clock.now answers the local time in words" `
        ([ordered]@{ status = $clock.status; speech = $clock.speech }) "clock.now: $($clock.status)"
    $location = Invoke-Tool "location.get_default"
    Set-Row "273" ($location.status -eq "succeeded") "the default location is read" `
        ([ordered]@{ status = $location.status; speech = $location.speech }) "location.get_default: $($location.status)"
    $weather = Invoke-Tool "weather.current"
    Set-Row "272" ($weather.status -eq "succeeded" -and $weather.terminal -eq "verified") "live weather for the default location" `
        ([ordered]@{ status = $weather.status; terminal = $weather.terminal; speech = $weather.speech }) "weather.current: $($weather.status)/$($weather.terminal)"
    $system = Invoke-Tool "briefing.system_status"
    Set-Row "274" ($system.status -eq "succeeded" -and $system.speech) "system status from production health" `
        ([ordered]@{ status = $system.status; speech = $system.speech }) "briefing.system_status: $($system.status)"
    $overnight = Invoke-Tool "briefing.overnight_work"
    Set-Row "275" ($overnight.status -eq "succeeded" -and $overnight.speech) "overnight work summary" `
        ([ordered]@{ status = $overnight.status; speech = $overnight.speech }) "briefing.overnight_work: $($overnight.status)"
    $morning = Invoke-Tool "briefing.morning"
    $sections = $morning.server
    $sectionNames = @()
    if ($sections) {
        $s = Get-OptionalProperty -InputObject $sections -Name "sections"
        if ($s) { $sectionNames = @($s.PSObject.Properties | ForEach-Object { $_.Name }) }
    }
    Set-Row "280" ($morning.status -eq "succeeded" -and $morning.terminal -eq "verified") "the combined morning briefing" `
        ([ordered]@{ status = $morning.status; terminal = $morning.terminal; sections = $sectionNames; speech = $morning.speech }) "briefing.morning: $($morning.status)/$($morning.terminal)"
    Set-Row "270" ($morning.speech -match "^(G\u00fcnayd\u0131n|\u0130yi|Iyi|Merhaba|T\u00fcnayd\u0131n)") "the briefing opens with a greeting" `
        ([ordered]@{ opening = $(if ($morning.speech.Length -gt 40) { $morning.speech.Substring(0, 40) } else { $morning.speech }) }) "no greeting at the start of the briefing"
    Set-Row "279" ($sectionNames -contains "news_summary") "the briefing carries a news section" `
        ([ordered]@{ sections = $sectionNames }) "no news_summary section"
    Set-Row "276" ($sectionNames -contains "research_completed") "the briefing summarises research finished in the last 12 h" `
        ([ordered]@{ sections = $sectionNames }) "no research finished in the last 12 h (section is conditional)"
}
finally {
    if ($sid) {
        try { Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/voice/realtime/sessions/$sid/close" -Headers $headers -Body '{"reason":"client_closed"}' -TimeoutSec 30 | Out-Null } catch { }
    }
    if ($ownerSessionId) {
        try { Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions/$ownerSessionId/revoke" -Headers $headers -Body "{}" -TimeoutSec 30 | Out-Null } catch { }
    }
    $headers = $null
}

$after = Get-ServedRelease
$sameRelease = ($before.version -and $before.version -eq $after.version)
$proven = @($rows.Keys | Where-Object { $rows[$_].status -eq "PROVEN_REAL" })
$evidence = [ordered]@{
    kind          = "pc_v1_production_round"
    started_at    = $startedAt.ToString("o")
    finished_at   = (Get-Date).ToUniversalTime().ToString("o")
    production    = [ordered]@{ base_url = $BaseUrl; release_before = $before.version; release_after = $after.version; health_before = $before.status; health_after = $after.status; same_release = $sameRelease }
    failure_probe = $failureProbe
    realtime_session_id = $sid
    proven        = $proven
    rows          = $rows
}
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd-HHmmss")
$path = Join-Path $EvidenceDir "pc-production-round-$stamp.json"
[IO.File]::WriteAllText($path, ($evidence | ConvertTo-Json -Depth 12), (New-Object System.Text.UTF8Encoding($false)))
Write-Host ""
Write-Host "evidence : $path"
Write-Host ("proven   : {0} of {1} rows; release {2} -> {3}" -f $proven.Count, $rows.Count, $before.version, $after.version)
if (-not $sameRelease) { Write-Host "production changed release during the round; nothing here counts" -ForegroundColor Red; exit 1 }
if ($proven.Count -eq $rows.Count) { exit 0 }
exit 1
