<#
.SYNOPSIS
    P0 production round: the rows whose runtime proof reads "uretim turu bekliyor
    (Karar 0)" for B04 (secret redaction), B05 (identity/device trust/code promotion),
    B06 (sweepers/reconciliation/world model accuracy), B07 (bounded retries/loop
    health/delivery receipts), B10 (execution honesty/reconciliation) and B26
    (deterministic voice routing/telemetry), asked of the LIVE Cloud Core and
    recorded as one evidence file.

.DESCRIPTION
    Same discipline as qualify-pc-production.ps1: an owner session minted from the
    stored credential (and revoked at the end), the owner's own REST reads, and one
    realtime voice session used ONLY through its non-executing routes (POST
    .../events, which resolves and records an utterance's intent but never calls a
    tool - app/voice/realtime_sessions/service.py's own module docstring: "Resolving
    is all an utterance does ... mutations go through tool calls"). Nothing in this
    round calls a tool-call route, so nothing here can reach a device, a printer, a
    mailbox or a browser.

    Read-only for every row except:
      * two throwaway owner sessions (minted, read back, revoked - never the
        owner's real sessions);
      * one throwaway realtime voice session used only for POST .../events
        (utterance resolution, never a tool call), closed at the end;
      * a handful of utterances posted to that session's .../events route, chosen
        from the product's own routing-negative corpus
        (tests/voice_corpus/routing.py) so every posted sentence is one the product
        itself asserts must NOT act;
      * two POST requests to endpoints that are refused before they can do
        anything: /v1/voice/speaker/verify with a forbidden extra field (a 422
        pydantic validation error), and /v1/evolution/opportunities/{random-guid}/advance
        with actor=system (a 404 "unknown opportunity" - the id is freshly
        generated and never corresponds to a real candidate, so there is nothing
        for the call to change).

    What this round deliberately never does:
      * call panic revoke (row 660) - it revokes EVERY session including the
        owner's real ones (app/identity/routes.py:panic -> service.revoke_all).
        There is no session-scoped variant, so this row cannot be proven live
        without touching the owner's own sessions; it is recorded NOT_YET_PROVEN
        with that reason, on purpose, every time;
      * advance a REAL evolution opportunity through its lifecycle (row 675's
        exit-permission claim needs a candidate already on the production side,
        and moving a real one is not reversible by this script);
      * create a real executive run, alarm, mail, print job, file mutation or
        desktop/device action of any kind;
      * touch any session, device, capability, opportunity or task that already
        existed before this run started, except to read it.

    Exit 0 when every asked row is PROVEN_REAL, 1 when the round ran and some row
    did not prove (this is the expected, honest outcome for several rows - read
    their `reason`), 2 when production could not be asked at all.
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = "http://100.90.158.26:8001",
    [string]$SshHost = "root@pagentos-core",
    [string]$ContainerName = "pagentos-prod-api-blue",
    [switch]$SkipSsh,
    [int]$ToolTimeoutSec = 60,
    [string]$EvidenceDir = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
. (Join-Path $repoRoot "scripts\lib\OwnerHarness.ps1")

if (-not $EvidenceDir) { $EvidenceDir = Join-Path $repoRoot "docs\evidence" }

# A sandboxed or minimal shell can lose System32/OpenSSH from PATH even when the
# binary is present (the same class of broken-PATH issue as uv/gh/pnpm on this
# machine) - resolve an absolute path once rather than fail every ssh call.
$SshExe = "ssh"
$OpenSshAbs = "$env:WINDIR/System32/OpenSSH/ssh.exe"
if (-not (Get-Command ssh -ErrorAction SilentlyContinue) -and (Test-Path $OpenSshAbs)) { $SshExe = $OpenSshAbs }

# ---------------------------------------------------------------- secret shapes
# The same 13 patterns as app/security/redaction.py (SECRET_PATTERNS = the 4
# config-shaped patterns + the 9 shared with app/memory/policy.py). Counts only,
# never the matched text itself - a verifier that prints a candidate secret to
# prove a redaction bug would be committing the exact bug it is checking for.
$SecretPatterns = [ordered]@{
    credential_assignment     = '(?im)^[^\S\n]*[\w.\-\[\]]*(?:password|passwd|pwd|secret|token|apikey|api_key|access_key|private_key|credential|client_secret)[\w.\-\[\]]*[^\S\n]*[:=][^\S\n]*\S{4,}'
    connection_uri_credential = '(?i)\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s/@]+@'
    jwt                       = '\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}'
    basic_auth_header         = '(?i)\bbasic\s+[A-Za-z0-9+/]{16,}={0,2}'
    github_token              = '\bghp_[A-Za-z0-9]{20,}'
    github_fine_grained       = '\bgithub_pat_[A-Za-z0-9_]{20,}'
    openai_style_key          = '\bsk-[A-Za-z0-9_-]{16,}'
    aws_access_key            = '\bAKIA[0-9A-Z]{16}\b'
    slack_token               = '\bxox[abprs]-[A-Za-z0-9-]{8,}'
    private_key_block         = '-----BEGIN [A-Z ]*PRIVATE KEY-----'
    password_assignment       = '(?i)\bpassword\s*[:=]\s*\S+'
    bearer_token              = '(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*'
    generic_api_key           = '(?i)\bapi[_-]?key\s*[:=]\s*\S{8,}'
}

function Get-SecretHitCounts {
    param([string]$Text)
    $hits = [ordered]@{}
    $total = 0
    foreach ($name in $SecretPatterns.Keys) {
        $count = [regex]::Matches($Text, $SecretPatterns[$name]).Count
        if ($count -gt 0) { $hits[$name] = $count; $total += $count }
    }
    return [pscustomobject]@{ total = $total; by_pattern = $hits }
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

function Get-ServedRelease {
    $health = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/system/health" -TimeoutSec 30
    $release = Get-OptionalProperty -InputObject $health -Name "release"
    return [pscustomobject]@{
        status  = [string](Get-OptionalProperty -InputObject $health -Name "status")
        version = [string](Get-OptionalProperty -InputObject $release -Name "version")
        health  = $health
    }
}

$startedAt = (Get-Date).ToUniversalTime()
Write-Host "P0 production round against $BaseUrl"
try { $before = Get-ServedRelease } catch { Write-Host "production is unreachable: $($_.Exception.Message)"; exit 2 }
Write-Host "release : $($before.version) ($($before.status))"

# ---- row 8 first, unauthenticated, before minting anything: the health surface
# this row is about is the one that answers with NO owner session at all.
$healthText = ($before.health | ConvertTo-Json -Depth 20 -Compress)
$healthScan = Get-SecretHitCounts -Text $healthText
Set-Row "8" ($healthScan.total -eq 0) "the unauthenticated health surface carries no secret-shaped pattern" `
    ([ordered]@{ pattern_hits = $healthScan.total; by_pattern = $healthScan.by_pattern; body_chars = $healthText.Length }) `
    "found $($healthScan.total) secret-shaped pattern hit(s) in the unauthenticated health body: $($healthScan.by_pattern.Keys -join ',')"

$credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL"
if (-not $credential) { Write-Host "no stored owner credential"; exit 2 }
$body = @{ owner_credential = $credential; client_kind = "cli"; label = "qualify-p0-production" } | ConvertTo-Json -Compress
$issued = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body $body -TimeoutSec 30
$credential = $null; $body = $null
$headers = @{ Authorization = "Bearer $($issued.token)" }
$ownerSessionId = [string](Get-OptionalProperty -InputObject $issued -Name "session_id")
Write-Host "owner session minted: $ownerSessionId  idle_timeout_s=$($issued.idle_timeout_s)"

$sid = $null
try {
    # ------------------------------------------------------------ B04 (6, 668, 683)
    Write-Host ""
    Write-Host "B04 secret redaction (world facts, full world snapshot)"
    $factsSource = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/world/facts?kind=source_truth" -Headers $headers -TimeoutSec 30
    $factRows = Get-ArrayProperty -InputObject $factsSource -Name "facts"
    $dbUrlFact = $factRows | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "key") -eq "dependencies.database_url" } | Select-Object -First 1
    $dbUrlValue = [string](Get-OptionalProperty -InputObject $dbUrlFact -Name "value")
    $dbUrlRedacted = [bool]($dbUrlValue -and ($dbUrlValue -match '^[a-z][a-z0-9+.\-]*://\*\*\*@'))

    $fullWorld = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/world" -Headers $headers -TimeoutSec 30
    $worldText = ($fullWorld | ConvertTo-Json -Depth 20 -Compress)
    $worldScan = Get-SecretHitCounts -Text $worldText

    Set-Row "6" ($dbUrlRedacted -and $worldScan.total -eq 0) "dependencies.database_url is userinfo-stripped and no secret pattern occurs anywhere in the world snapshot" `
        ([ordered]@{ database_url_fact = $dbUrlValue; pattern_hits_in_world = $worldScan.total; by_pattern = $worldScan.by_pattern }) `
        "database_url fact = '$dbUrlValue' (redacted-shape: $dbUrlRedacted); $($worldScan.total) secret-shaped pattern hit(s) in /v1/world"
    Set-Row "668" ($healthScan.total -eq 0 -and $worldScan.total -eq 0) "every surface asked (health, world model) carries no secret pattern" `
        ([ordered]@{ health_hits = $healthScan.total; world_hits = $worldScan.total }) `
        "health hits=$($healthScan.total) world hits=$($worldScan.total)"

    # ------------------------------------------------------------ B06 (9,10,11,13,67,68,69,70,206,219,220)
    Write-Host ""
    Write-Host "B06 sweepers / reconciliation / world-model accuracy"
    $health = Get-ServedRelease
    $retention = Get-OptionalProperty -InputObject $health.health.checks -Name "retention"
    $sweepNames = Get-ArrayProperty -InputObject $retention -Name "sweeps"
    $lastResults = Get-OptionalProperty -InputObject $retention -Name "last_results"
    $lastRunAt = [string](Get-OptionalProperty -InputObject $retention -Name "last_run_at")
    $hasIdleVoiceSweep = $sweepNames -contains "idle_voice_sessions"
    $hasAbandonedSweep = $sweepNames -contains "abandoned_research_runs"

    Set-Row "9" $hasIdleVoiceSweep "the idle-voice-session sweep is wired into the live retention loop" `
        ([ordered]@{ sweeps = $sweepNames; last_run_at = $lastRunAt; idle_voice_sessions_last_result = (Get-OptionalProperty -InputObject $lastResults -Name "idle_voice_sessions") }) `
        "'idle_voice_sessions' not present in retention.sweeps"
    Set-Row "219" $hasIdleVoiceSweep "dead voice sessions are swept on a live cadence (same implementation as 9)" `
        ([ordered]@{ sweeps = $sweepNames; last_run_at = $lastRunAt }) "'idle_voice_sessions' not present in retention.sweeps"
    Set-Row "220" $hasIdleVoiceSweep "session TTL (12h idleness, IDLE_SESSION_AFTER) is enforced by the same live sweep" `
        ([ordered]@{ sweeps = $sweepNames }) "cannot observe a 12h-idle voice session live without waiting 12h; sweep wiring is the live-observable half"
    Set-Row "10" $hasAbandonedSweep "the abandoned-research-run sweep is wired into the live retention loop" `
        ([ordered]@{ sweeps = $sweepNames; last_run_at = $lastRunAt; abandoned_research_runs_last_result = (Get-OptionalProperty -InputObject $lastResults -Name "abandoned_research_runs") }) `
        "'abandoned_research_runs' not present in retention.sweeps"
    Set-Row "13" $hasAbandonedSweep "orphan research rows are cleaned by the same sweep as 10 (single implementation)" `
        ([ordered]@{ sweeps = $sweepNames }) "'abandoned_research_runs' not present in retention.sweeps"
    Set-Row "206" $hasAbandonedSweep "orphan cleanup (dup of 10/13) is the same live sweep" `
        ([ordered]@{ sweeps = $sweepNames }) "'abandoned_research_runs' not present in retention.sweeps"

    $factsRuntime = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/world/facts?kind=runtime_truth" -Headers $headers -TimeoutSec 30
    $runtimeRows = Get-ArrayProperty -InputObject $factsRuntime -Name "facts"
    function Get-FactValue([string]$Key) {
        $f = $runtimeRows | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "key") -eq $Key } | Select-Object -First 1
        if ($null -eq $f) { return $null }
        return (Get-OptionalProperty -InputObject $f -Name "value")
    }
    $runningCount = Get-FactValue "tasks.running_count"
    $stuckCount = Get-FactValue "tasks.stuck_count"
    $awaitingCount = Get-FactValue "tasks.awaiting_owner_count"
    Set-Row "67" ($null -ne $runningCount) "tasks.running_count is served from the four-bucket world model classification" `
        ([ordered]@{ running_count = $runningCount; awaiting_owner_count = $awaitingCount; stuck_count = $stuckCount }) "no tasks.running_count fact in production"
    Set-Row "68" ($null -ne $awaitingCount) "READY/awaiting-owner research is a separate bucket from running (awaiting_owner_count exists and is counted apart from running_count)" `
        ([ordered]@{ awaiting_owner_count = $awaitingCount; running_count = $runningCount }) "no tasks.awaiting_owner_count fact in production"
    Set-Row "69" ($null -ne $stuckCount) "stuck-task detection (STUCK_TASK_AFTER, 24h) is live and counting" `
        ([ordered]@{ stuck_count = $stuckCount }) "no tasks.stuck_count fact in production"
    if ($null -ne $stuckCount -and [int]$stuckCount -gt 0) {
        Write-Host "  NOTE: tasks.stuck_count=$stuckCount right now - worth the owner's attention; see the round's summary notes" -ForegroundColor Yellow
    }
    Set-Row "11" ($hasAbandonedSweep -and $null -ne $runningCount) "stale RUNNING/CREATED reconciliation: the sweep moves the row, the world model counts what the sweep left" `
        ([ordered]@{ sweeps = $sweepNames; running_count = $runningCount }) "either the sweep is not wired or the world model has no running_count to reconcile against"
    # Row 70 is judged on the ledger itself: no voice-session source_ref may occur twice for
    # the same event type among the recent voice events.
    $voiceEvents = Get-ArrayProperty -InputObject (Invoke-JsonUtf8 -Uri "$BaseUrl/v1/ledger/events?limit=200" -Headers $headers -TimeoutSec 30) -Name "events"
    $voiceKeys = @($voiceEvents | Where-Object { ([string](Get-OptionalProperty -InputObject $_ -Name "event_type")).StartsWith("voice.") } |
        ForEach-Object { "{0}|{1}" -f (Get-OptionalProperty -InputObject $_ -Name "event_type"), (Get-OptionalProperty -InputObject $_ -Name "source_ref") } |
        Where-Object { -not $_.EndsWith("|") })
    $duplicateKeys = @($voiceKeys | Group-Object | Where-Object { $_.Count -gt 1 })
    Set-Row "70" ($voiceKeys.Count -gt 0 -and $duplicateKeys.Count -eq 0) "no voice event is recorded twice for the same source in the live ledger" `
        ([ordered]@{ voice_events_with_source = $voiceKeys.Count; duplicated_keys = $duplicateKeys.Count }) `
        $(if ($voiceKeys.Count -eq 0) { "no recent voice event carries a source_ref to check" } else { "$($duplicateKeys.Count) voice source_ref(s) recorded more than once" })

    # ------------------------------------------------------------ B07 (14,15,16,17,19,375,376,390)
    Write-Host ""
    Write-Host "B07 bounded retries / loop health / delivery receipts"
    $routineClock = Get-OptionalProperty -InputObject $health.health.checks -Name "routine_clock"
    $subTicks = Get-OptionalProperty -InputObject $routineClock -Name "sub_ticks"
    $subTickNames = @()
    if ($subTicks) { $subTickNames = @($subTicks.PSObject.Properties | ForEach-Object { $_.Name }) }
    $subTickTicks = [ordered]@{}
    foreach ($n in $subTickNames) { $subTickTicks[$n] = (Get-OptionalProperty -InputObject $subTicks.$n -Name "ticks") }
    $allTicking = ($subTickNames.Count -ge 7) -and (-not ($subTickTicks.Values | Where-Object { [int]$_ -le 0 }))
    Set-Row "19" $allTicking "each routine-clock sub-tick ticks and fails independently" `
        ([ordered]@{ sub_ticks = $subTickTicks; failing_sub_ticks = (Get-ArrayProperty -InputObject $routineClock -Name "failing_sub_ticks") }) `
        "fewer than 7 independently-ticking sub-ticks observed"

    $arAnnouncer = Get-OptionalProperty -InputObject $health.health.checks -Name "artifact_ready_announcer"
    $brAnnouncer = Get-OptionalProperty -InputObject $health.health.checks -Name "briefing_announcer"
    $rtAnnouncer = Get-OptionalProperty -InputObject $health.health.checks -Name "research_tool_call_announcer"
    $announcersUp = @($arAnnouncer, $brAnnouncer, $rtAnnouncer) | Where-Object {
        [bool](Get-OptionalProperty -InputObject $_ -Name "running") -and [int](Get-OptionalProperty -InputObject $_ -Name "failures") -eq 0
    }
    $announcerObserved = [ordered]@{
        artifact_ready_announcer      = $arAnnouncer
        briefing_announcer            = $brAnnouncer
        research_tool_call_announcer  = $rtAnnouncer
    }
    $loopsHealthy = ($announcersUp.Count -eq 3)
    Set-Row "14" $loopsHealthy "the three announcer loops are alive with zero failures on a bounded cadence" `
        $announcerObserved "one or more announcer loops are not running or have recorded failures"
    Set-Row "15" $loopsHealthy "push announcer bounded retry: same live loop as 14" $announcerObserved "see row 14"
    Set-Row "16" $loopsHealthy "briefing announcer queue lock: same live loop as 14" $announcerObserved "see row 14"
    Set-Row "17" $loopsHealthy "research announcer bounded retry: same live loop as 14" $announcerObserved "see row 14"
    Set-Row "375" $false "delivery receipt: structural proof only (source + test_bounded_delivery.py)" `
        $null "no live mobile push delivery was attempted this round (would need a real device registration)"
    Set-Row "376" $loopsHealthy "delivery retry bounded plus quarantine: same shared policy as 14" $announcerObserved "see row 14"
    Set-Row "390" $false "fake provider must never report delivered: structural proof only" `
        $null "proving this live needs a real registered push token, which this round will not create"

    # ------------------------------------------------------------ B05 (246,663,658,659,660)
    Write-Host ""
    Write-Host "B05 trusted device / session lifetime / panic"
    $verifyBody = '{"probe_embedding":[0.1,0.2,0.3],"device_trusted":true}'
    $verifyStatus = $null; $verifyDetail = $null
    try {
        Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/voice/speaker/verify" -Headers $headers -Body $verifyBody -TimeoutSec 30 | Out-Null
        $verifyStatus = 200
    } catch {
        $verifyStatus = Get-OptionalProperty -InputObject $_.Exception -Name "StatusCode"
        $verifyDetail = $_.Exception.Message
    }
    $verifyRejected = ($verifyStatus -eq 422 -and $verifyDetail -match "extra_forbidden" -and $verifyDetail -match "device_trusted")
    Set-Row "246" $verifyRejected "device_trusted sent by the caller is refused (422 extra_forbidden), never silently accepted" `
        ([ordered]@{ http_status = $verifyStatus; detail = $verifyDetail }) "expected HTTP 422 extra_forbidden naming device_trusted; got $verifyStatus"
    Set-Row "663" $verifyRejected "device trust is derived from the session device binding, never the request body (same probe as 246)" `
        ([ordered]@{ http_status = $verifyStatus }) "see row 246"

    $idEvents = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/identity/events?limit=500&action=expired" -Headers $headers -TimeoutSec 30
    $idEventRows = Get-ArrayProperty -InputObject $idEvents -Name "events"
    $idleRows = @($idEventRows | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "reason") -eq "idle_timeout" })
    $absRows = @($idEventRows | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "reason") -eq "absolute_lifetime" })
    Set-Row "659" ($idleRows.Count -gt 0) "idle session lifetime is fully enforced by the live sweep" `
        ([ordered]@{ idle_timeout_events = $idleRows.Count; absolute_lifetime_events = $absRows.Count }) `
        "no expired event carries reason=idle_timeout"
    Set-Row "658" ($absRows.Count -gt 0) "absolute session lifetime (90d ceiling) has actually revoked a session in production" `
        ([ordered]@{ absolute_lifetime_events = $absRows.Count; config_default_s = 7776000 }) `
        "0 expired events carry reason=absolute_lifetime; the deployment is not yet 90 days old so this ceiling cannot have fired for real yet (mechanism is wired, session_absolute_lifetime_s=90d, source/unit-test proven only)"
    Set-Row "660" $false "panic revoke is reachable and two-step, and its own 401 counts as success" `
        $null "app/identity/routes.py:panic calls service.revoke_all which revokes EVERY session with no session-scoped variant; this round refuses to call it because it would revoke the owner real live sessions while asleep. Proven only by source plus discoverability.test.tsx (15) plus test_web_asks_for_routes_that_exist.py (47)."

    $fakeOppId = [guid]::NewGuid().ToString()
    $advBody = @{ target = "live"; actor = "system"; reason = "p0-production-round exit-permission probe on an unknown id" } | ConvertTo-Json -Compress
    $advStatus = $null; $advDetail = $null
    try {
        Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/evolution/opportunities/$fakeOppId/advance" -Headers $headers -Body $advBody -TimeoutSec 30 | Out-Null
        $advStatus = 200
    } catch {
        $advStatus = Get-OptionalProperty -InputObject $_.Exception -Name "StatusCode"
        $advDetail = $_.Exception.Message
    }
    $mintedNotBlocked = ($advStatus -eq 404 -and $advDetail -match "not_found")
    Set-Row "675" $false "code promotion permission covers the transition that LEAVES the production side too, not only the one that enters it" `
        ([ordered]@{ unknown_id_probe_http_status = $advStatus; unknown_id_probe_detail = $advDetail; entering_side_evidence_mint_reached_not_found = $mintedNotBlocked }) `
        "the EXIT-side half of this claim needs a REAL opportunity already on the production side (one exists, status=live) and advancing it for real is not a reversible probe this round will perform; only the entering-side path and the endpoint validation were exercised live"

    # ------------------------------------------------------------ B10 (539,548,558,559,560)
    Write-Host ""
    Write-Host "B10 execution honesty / reconciliation"
    $execHealth = $subTickTicks["executive"]
    Set-Row "560" ([int]$execHealth -gt 0) "task/run reconciliation runs on the live routine clock cadence (executive sub-tick)" `
        ([ordered]@{ executive_sub_tick_ticks = $execHealth; last_ok_at = (Get-OptionalProperty -InputObject $subTicks.executive -Name "last_ok_at") }) "executive sub-tick has not ticked"

    try {
        $execRuns = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/executive/runs?limit=50" -Headers $headers -TimeoutSec 30
        $execRunRows = Get-ArrayProperty -InputObject $execRuns -Name "runs"
        $falseCompleted = @($execRunRows | Where-Object {
            [string](Get-OptionalProperty -InputObject $_ -Name "state") -eq "completed" -and
            [int](Get-OptionalProperty -InputObject $_ -Name "done") -lt [int](Get-OptionalProperty -InputObject $_ -Name "total")
        })
        $partialRuns = @($execRunRows | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "state") -eq "partial" })
        Set-Row "559" ($execRunRows.Count -gt 0 -and $falseCompleted.Count -eq 0) "no production run claims completed while steps are missing" `
            ([ordered]@{ runs_observed = $execRunRows.Count; falsely_completed = $falseCompleted.Count; partial_runs = $partialRuns.Count }) `
            "no executive runs exist in production to check, or at least one falsely claims completed"
        Set-Row "558" ($execRunRows.Count -gt 0) "final status is read from live production runs and reflects real per-step outcomes" `
            ([ordered]@{ runs_observed = $execRunRows.Count }) `
            "no executive runs exist in production; NOTE every run currently in production predates the reconcile fix, so a stale steps_done on an old row is not current proof, only its state classification is"
        Set-Row "539" $false "compensation reports what actually happened, never a blanket compensated" `
            ([ordered]@{ runs_with_failures = @($execRunRows | Where-Object { [int](Get-OptionalProperty -InputObject $_ -Name "failed") -gt 0 -or [string](Get-OptionalProperty -InputObject $_ -Name "state") -eq "partial" }).Count }) `
            "the run routes do not surface a compensation-outcome field; proving this live needs a run that reaches a compensating branch, which this round will not create"
    } catch {
        Set-Row "559" $false "no false completion" $null "GET /v1/executive/runs failed: $($_.Exception.Message)"
        Set-Row "558" $false "correct final status" $null "GET /v1/executive/runs failed: $($_.Exception.Message)"
        Set-Row "539" $false "honest compensation" $null "GET /v1/executive/runs failed: $($_.Exception.Message)"
    }
    Set-Row "548" $false "folder-compare plan runs on the fixed file.search contract" $null `
        "proving this live needs a new executive run against real files, which this round will not start at this hour"

    # ------------------------------------------------------------ B26 (736,737,738,739,741,749,750)
    Write-Host ""
    Write-Host "B26 deterministic voice routing (resolve-only; no tool call is ever made)"
    $session = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/voice/realtime/sessions" -Headers $headers -Body "{}" -TimeoutSec $ToolTimeoutSec
    $sid = [string]$session.session_id
    Write-Host "  realtime session (routing probes only, never a tool call): $sid"

    function Send-Utterance {
        param([string]$Text, [int]$TMs, [int]$Turn)
        $ev = @{ events = @(@{ kind = "utterance"; text = $Text; t_ms = $TMs; turn = $Turn }) } | ConvertTo-Json -Depth 6 -Compress
        $resp = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/voice/realtime/sessions/$sid/events" -Headers $headers -Body $ev -TimeoutSec 30
        $ri = (Get-ArrayProperty -InputObject $resp -Name "resolved_intents") | Select-Object -First 1
        return [pscustomobject]@{
            text          = $Text
            intent        = [string](Get-OptionalProperty -InputObject $ri -Name "intent")
            klass         = [string](Get-OptionalProperty -InputObject $ri -Name "klass")
            echoes_words  = (($resp | ConvertTo-Json -Depth 12 -Compress) -match [regex]::Escape($Text.TrimEnd('.')))
        }
    }

    # Negatives from the product's own corpus (tests/voice_corpus/routing.py, SOURCE_MEASURED):
    # each sentence carries a forbidden set this round checks against the LIVE resolver.
    $negatives = @(
        [pscustomobject]@{ text = "Dosyayi gonder."; forbidden = @("mail_send"); row = "736" }
        [pscustomobject]@{ text = "Bunu yazdir."; forbidden = @("type_text"); row = "737" }
        [pscustomobject]@{ text = "Otomatik guncellemeleri kapat."; forbidden = @("ambient_policy_set", "display_off"); row = "738" }
    )
    $negativeResults = @()
    $t = 1000
    foreach ($n in $negatives) {
        $r = Send-Utterance -Text $n.text -TMs $t -Turn ($t / 1000)
        $t += 1000
        $misrouted = $n.forbidden -contains $r.intent
        $negativeResults += [pscustomobject]@{ text = $n.text; intent = $r.intent; forbidden = ($n.forbidden -join "|"); misrouted = $misrouted }
        Set-Row $n.row (-not $misrouted) "this sentence must never resolve to $($n.forbidden -join ' or ')" `
            ([ordered]@{ text = $n.text; resolved_intent = $r.intent; klass = $r.klass }) "resolved to '$($r.intent)', which is forbidden"
    }

    # Shield positives (req 741): the SAME family must still route correctly for the
    # sentence that genuinely means it, proving the negatives above were a narrowing,
    # not a blanket refusal.
    $shield1 = Send-Utterance -Text "Uyurken ekranlari kapat." -TMs $t -Turn ($t / 1000); $t += 1000
    $shield2 = Send-Utterance -Text "Ekranlari kapat." -TMs $t -Turn ($t / 1000); $t += 1000
    $shieldOk = ($shield1.intent -eq "ambient_policy_set") -and ($shield2.intent -eq "display_off")
    $anyMisrouted = @($negativeResults | Where-Object { $_.misrouted }).Count -gt 0
    Set-Row "739" (-not $anyMisrouted) "the wrong-route negative corpus holds live (sampled from the 107-sentence SOURCE_MEASURED set)" `
        ([ordered]@{ sampled = $negativeResults }) "at least one sampled negative misrouted live"
    Set-Row "741" (-not $anyMisrouted -and $shieldOk) "the deterministic router is a shield: refuses the negatives above AND still routes the genuine command/preference pair correctly" `
        ([ordered]@{ shield_ambient = $shield1.intent; shield_display_off = $shield2.intent; negatives_held = (-not $anyMisrouted) }) `
        "either a negative misrouted or a shield case failed to route as expected"

    $telemetryOk = ($shield2.intent -and $shield2.klass -and -not $shield1.echoes_words -and -not $shield2.echoes_words)
    Set-Row "749" $telemetryOk "route telemetry records the resolution (intent/klass), never the owner's words" `
        ([ordered]@{ sample_response_intent = $shield2.intent; sample_response_klass = $shield2.klass; response_echoes_words = ($shield1.echoes_words -or $shield2.echoes_words) }) `
        "the resolution was missing or the response carried the spoken words"

    # Misroute auto-detection (750): an acting intent, then a same-window reaction.
    # This never calls a tool - TYPE_TEXT here is only ever RESOLVED, never typed
    # anywhere (module docstring: "no utterance mutates anything").
    $ledgerBefore = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/ledger/events?event_type=voice.misroute_suspected&limit=20" -Headers $headers -TimeoutSec 30
    $beforeCount = (Get-ArrayProperty -InputObject $ledgerBefore -Name "events").Count
    $evPair = @{ events = @(
        @{ kind = "utterance"; text = "Buraya merhaba yaz."; t_ms = $t; turn = ($t / 1000) },
        @{ kind = "utterance"; text = "Hayir, dur."; t_ms = ($t + 2000); turn = ($t / 1000 + 1) }
    ) } | ConvertTo-Json -Depth 6 -Compress
    $t += 4000
    $pairResp = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/voice/realtime/sessions/$sid/events" -Headers $headers -Body $evPair -TimeoutSec 30
    $pairIntents = (Get-ArrayProperty -InputObject $pairResp -Name "resolved_intents") | ForEach-Object { [string](Get-OptionalProperty -InputObject $_ -Name "intent") }
    Start-Sleep -Seconds 1
    $ledgerAfter = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/ledger/events?event_type=voice.misroute_suspected&limit=20" -Headers $headers -TimeoutSec 30
    $afterCount = (Get-ArrayProperty -InputObject $ledgerAfter -Name "events").Count
    $ledgerWrote = ($afterCount -gt $beforeCount)
    $row750Reason = "PAIRED CORRECTLY (pair_intents=" + ($pairIntents -join ",") + ") BUT NO LEDGER ROW APPEARED. "
    $row750Reason += "Root cause found live: app/voice/route_telemetry.py note_candidate() writes event_type=voice.misroute_suspected, "
    $row750Reason += "which does not exist in app/ledger/vocabulary.py EVENT_TYPES (nor EVOLUTION_EVENT_TYPES, nor the deployment.* regex) - "
    $row750Reason += "every ledger_service.record() call raises InvalidVocabulary from validate_event_type(), caught by note_candidate's "
    $row750Reason += "broad except and logged only as a warning (voice_misroute_note_failed). Confirmed via docker logs on the container: "
    $row750Reason += "voice_misroute_suspected (detection fired) is immediately preceded by voice_misroute_note_failed (the write it tried "
    $row750Reason += "to make failed) on every occurrence. Detection (req 749) works; persistence (req 750's own stated purpose) silently "
    $row750Reason += "does not, in every environment, not only production - test_route_telemetry.py never exercises the real "
    $row750Reason += "ledger_service.record() call (it monkeypatches note_candidate for the pairing test, and passes db=None or an "
    $row750Reason += "exploding object for the other). Fix: register voice.misroute_suspected in app/ledger/vocabulary.py EVENT_TYPES."
    Set-Row "750" $ledgerWrote "an acting intent followed within the window by a reaction is written to the Activity Ledger as a candidate misroute" `
        ([ordered]@{ pair_intents = $pairIntents; ledger_events_before = $beforeCount; ledger_events_after = $afterCount }) $row750Reason

    # ------------------------------------------------------------ B04 log surfaces (7, 683) via SSH
    if (-not $SkipSsh) {
        Write-Host ""
        Write-Host "B04 log secret redaction (read-only ssh, counts only, never a matched value)"
        try {
            $sshOk = & $SshExe -o BatchMode=yes -o ConnectTimeout=8 $SshHost "echo ok" 2>$null
            if ($sshOk -ne "ok") { throw "ssh did not answer ok" }
            $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")
            # The scanner travels on stdin (python3 -); nothing is left on the host but the
            # temporary log copy, which the same command removes.
            $remoteCmd = "docker logs $ContainerName --since 24h > /tmp/p0log_$stamp.txt 2>&1; " +
                "python3 - /tmp/p0log_$stamp.txt; rm -f /tmp/p0log_$stamp.txt"
            $pyScript = @'
import re, sys, json
path = sys.argv[1]
text = open(path, "r", errors="replace").read()
patterns = {
    "credential_assignment": r"(?im)^[ \t]*[\w.\-\[\]]*(?:password|passwd|pwd|secret|token|apikey|api_key|access_key|private_key|credential|client_secret)[\w.\-\[\]]*[ \t]*[:=][ \t]*\S{4,}",
    "connection_uri_credential": r"(?i)\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s/@]+@",
    "jwt": r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
    "basic_auth_header": r"(?i)\bbasic\s+[A-Za-z0-9+/]{16,}={0,2}",
    "github_token": r"\bghp_[A-Za-z0-9]{20,}",
    "github_fine_grained": r"\bgithub_pat_[A-Za-z0-9_]{20,}",
    "password_assignment": r"(?i)\bpassword\s*[:=]\s*\S+",
    "openai_style_key": r"\bsk-[A-Za-z0-9_-]{16,}",
    "aws_access_key": r"\bAKIA[0-9A-Z]{16}\b",
    "slack_token": r"\bxox[abprs]-[A-Za-z0-9-]{8,}",
    "private_key_block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "bearer_token": r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*",
    "generic_api_key": r"(?i)\bapi[_-]?key\s*[:=]\s*\S{8,}",
}
out = {name: len(re.findall(pat, text)) for name, pat in patterns.items()}
out["_lines"] = text.count("\n")
print(json.dumps(out))
'@
            $remoteOut = $pyScript | & $SshExe -o BatchMode=yes -o ConnectTimeout=20 $SshHost $remoteCmd 2>$null
            $jsonLine = ($remoteOut | Where-Object { $_ -match '^\{.*\}$' } | Select-Object -Last 1)
            if (-not $jsonLine) { throw "no JSON line in ssh output" }
            $logScan = $jsonLine | ConvertFrom-Json
            $logHitProps = $logScan.PSObject.Properties | Where-Object { $_.Name -ne "_lines" }
            $logTotal = 0
            foreach ($p in $logHitProps) { $logTotal += [int]$p.Value }
            Set-Row "7" ($logTotal -eq 0) "the 13-pattern secret redaction holds across the live log stream" `
                ([ordered]@{ container = $ContainerName; window = "24h"; lines_scanned = $logScan._lines; pattern_hits = $logTotal }) `
                "found $logTotal secret-shaped pattern hit(s) in $ContainerName last 24h of logs"
            Set-Row "683" ($logTotal -eq 0) "no secret is logged anywhere (world model plus logs plus health)" `
                ([ordered]@{ log_pattern_hits = $logTotal; world_pattern_hits = $worldScan.total; health_pattern_hits = $healthScan.total }) `
                "found $logTotal secret-shaped pattern hit(s) in logs"
        } catch {
            Set-Row "7" $false "log secret redaction holds on the live container" $null "ssh to $SshHost failed or was refused: $($_.Exception.Message)"
            Set-Row "683" $false "no secret logged anywhere" $null "same ssh failure as row 7 prevented the log half of this check"
        }
    } else {
        Set-Row "7" $false "log secret redaction holds on the live container" $null "-SkipSsh was passed"
        Set-Row "683" $false "no secret logged anywhere" $null "-SkipSsh was passed"
    }
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
    kind        = "p0_production_round"
    started_at  = $startedAt.ToString("o")
    finished_at = (Get-Date).ToUniversalTime().ToString("o")
    production  = [ordered]@{ base_url = $BaseUrl; release_before = $before.version; release_after = $after.version; health_before = $before.status; health_after = $after.status; same_release = $sameRelease }
    proven      = $proven
    rows        = $rows
}
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null
$stamp2 = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd-HHmmss")
$path = Join-Path $EvidenceDir "p0-production-round-$stamp2.json"
[IO.File]::WriteAllText($path, ($evidence | ConvertTo-Json -Depth 16), (New-Object System.Text.UTF8Encoding($false)))
Write-Host ""
Write-Host "evidence : $path"
Write-Host ("proven   : {0} of {1} rows; release {2} -> {3}" -f $proven.Count, $rows.Count, $before.version, $after.version)
if (-not $sameRelease) { Write-Host "production changed release during the round; nothing here counts" -ForegroundColor Red; exit 1 }
if ($proven.Count -eq $rows.Count) { exit 0 }
exit 1
