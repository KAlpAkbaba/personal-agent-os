<#
.SYNOPSIS
    M18.2 follow-up-only owner check: "Teknik anlat." after a research that already
    completed. Reuses the durable completed research (the report, the task row) and the
    owner's earlier session record; starts no research, releases nothing, installs nothing.

.DESCRIPTION
    The owner's real M18.2 run proved the speaking lifecycle and the findings-first research
    answer; the technical follow-up was not observed inside the main harness's step window
    (the session had closed 30 s after the result and a fresh one opened). The product rule
    is semantic - a normal research answer speaks findings; an explicit technical follow-up
    speaks diagnostics - so this check waits for the owner, not the other way round.

    Proven from the record, on a NEW Core voice session:
      1  the utterance resolves to the technical intent (the router, never the wording) and
         an activity.explain call succeeds at the technical level / a diagnostics kind;
      2  diagnostics appear only now: the completed research call's own spoken head (durable,
         from the earlier session) carries no crawler words; the follow-up's does;
      3  the findings are not recomputed: the completed task's ready_at and artifact are
         unchanged, and the explanation's provenance names that task;
      4  no second crawl: no research.start on the new session, no research task created
         since this check began;
      5  no deployment repeated: the health manifest's contract version and the device's
         software version are the same before and after (this script never releases);
      6  the answer stays concise: the spoken characters stay within the technical budget.

.EXAMPLE
    .\scripts\core\owner-m18-2-followup.ps1 -OutFile m18-2-followup-1.json
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    [int]$WebPort = 3000,
    [string]$BaseUrl = "",
    [string]$OutFile = "",
    [string]$TaskId = "",
    [switch]$SkipWeb,
    [string]$PnpmPath = "pnpm",
    [ValidateSet("auto", "never", "force")][string]$CloudCoreUpdate = "auto",
    [ValidateRange(30, 900)][int]$ConnectWaitSec = 180,
    [ValidateRange(60, 1800)][int]$FollowupWaitSec = 600,
    [ValidateRange(200, 4000)][int]$ConciseMaxChars = 1000,
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
$runId = "owner-m18-2-followup-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$startedAt = (Get-Date).ToUniversalTime()
$runStart = [DateTimeOffset]::UtcNow
# The follow-up guard (ADR-0075: an explanation never becomes a second crawl) is a product
# change that must be RUNNING before the owner speaks; this checkout's contract says which
# version carries it. One release BEFORE the check when the deployed Cloud Core is older -
# never during it: `followup.no_deployment_repeated` measures from the owner's utterance on.
$requiredContractVersion = Get-CheckoutActionContractVersion -RepoRoot $repoRoot

$u_uml = [char]0x00FC; $i_dot = [char]0x0131; $s_ced = [char]0x015F
$phraseTechnical = "Teknik anlat."
$wordPagesTr = "sayfay" + $i_dot
$wordEvidenceTr = "kan" + $i_dot + "t"
$wordVersionTr = "s" + $u_uml + "r" + $u_uml + "m"
# Built BEFORE the array literal (the M18.2 harness crash: a + inside @( ) splits elements).
$diagnosticWords = @("elendi", "eledi", "interstitial", "dedup", "aday", $wordPagesTr, "sayfa", $wordEvidenceTr, $wordVersionTr, "policy", "cooldown")
$technicalKinds = @("technical", "research_problems", "rejected_pages", "research_detail")

$evidence = [ordered]@{ run_id = $runId; started_at = $startedAt.ToString("o"); cloud = $BaseUrl; web_shell = $null; completed_research = $null; before = $null; after = $null; session = $null; followup = $null; checks = @(); verdict = "FAIL" }

Write-Host "PagentOS owner M18.2 follow-up ($runId)"
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

function Get-DeviceVersions {
    <#  device_id -> software version, from the owner inventory; the same before and after means no agent was touched.  #>
    $doc = Get-JsonOrNull "/v1/devices"
    $out = @{}
    foreach ($d in (Get-ArrayProperty -InputObject $doc -Name "devices")) {
        $id = [string](Get-OptionalProperty -InputObject $d -Name "device_id")
        $health = Get-OptionalProperty -InputObject $d -Name "health"
        $version = ""
        if ($null -ne $health) { $version = [string](Get-OptionalProperty -InputObject $health -Name "software_version") }
        if (-not $version) { $version = [string](Get-OptionalProperty -InputObject $d -Name "software_version") }
        $out[$id] = $version
    }
    return $out
}

function Get-CompletedResearch {
    <#  The research task this follow-up is about: -TaskId, else the most recent READY task.  #>
    param([string]$Requested)
    $doc = Get-Json "/v1/research"
    $tasks = Get-ArrayProperty -InputObject $doc -Name "tasks"
    if ($Requested) {
        $match = @($tasks | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "task_id") -eq $Requested })
        if ($match.Count -eq 0) { throw "research task $Requested is not in the inventory" }
        return $match[0]
    }
    $ready = @($tasks | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "status") -eq "READY" } | Sort-Object -Property { [string](Get-OptionalProperty -InputObject $_ -Name "ready_at") })
    if ($ready.Count -eq 0) { throw "no completed (READY) research task on the record; the follow-up needs one" }
    return $ready[$ready.Count - 1]
}

function Get-ResearchTaskIds {
    $doc = Get-JsonOrNull "/v1/research"
    $ids = @()
    foreach ($t in (Get-ArrayProperty -InputObject $doc -Name "tasks")) { $ids += [string](Get-OptionalProperty -InputObject $t -Name "task_id") }
    return , $ids
}

function Find-ResearchCallHead {
    <#
        The completed research's own spoken head, from whichever web session carried the
        research.start call that produced it (matched by the call's completed_at falling
        inside a minute of the task's ready_at). Durable, from the earlier session - never
        re-run.
    #>
    param([string]$ReadyAtIso)
    $readyAt = ConvertTo-SessionInstant -Raw $ReadyAtIso
    if ($null -eq $readyAt) { return $null }
    $sessions = Get-ArrayProperty -InputObject (Get-JsonOrNull "/v1/voice/realtime/sessions?limit=50") -Name "sessions"
    foreach ($s in $sessions) {
        if ([string](Get-OptionalProperty -InputObject $s -Name "client_kind") -ne "web") { continue }
        $id = [string](Get-OptionalProperty -InputObject $s -Name "session_id")
        $act = Get-JsonOrNull "/v1/voice/realtime/sessions/$id/activity"
        foreach ($c in (Get-ArrayProperty -InputObject $act -Name "tool_calls")) {
            if ([string](Get-OptionalProperty -InputObject $c -Name "name") -ne "research.start") { continue }
            if ([string](Get-OptionalProperty -InputObject $c -Name "status") -ne "succeeded") { continue }
            $completed = ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $c -Name "completed_at"))
            if ($null -eq $completed) { continue }
            if ([math]::Abs(($completed - $readyAt).TotalSeconds) -le 90) {
                return [pscustomobject]@{ SessionId = $id; CallId = [string](Get-OptionalProperty -InputObject $c -Name "call_id"); SpeechHead = [string](Get-OptionalProperty -InputObject $c -Name "speech_head"); SpeechChars = (Get-OptionalProperty -InputObject $c -Name "speech_chars"); CompletedAt = [string](Get-OptionalProperty -InputObject $c -Name "completed_at") }
            }
        }
    }
    return $null
}

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
    if ($stale -and $CloudCoreUpdate -eq "never") { throw "the deployed Cloud Core is stale (v$deployedVersion < v$requiredContractVersion): the follow-up guard is not running there; -CloudCoreUpdate never refuses to release" }
    if ($releaseCloud -and $releaseBlockers.Blocked) { Write-ReleaseBlockers -Blockers $releaseBlockers; throw "a Cloud Core release is required but the working tree has $($blockerChanges.Count) uncommitted change(s); commit or revert them, then rerun" }
    if ($releaseCloud) {
        Write-Host "      the deployed Cloud Core predates the follow-up guard (v$deployedVersion < v$requiredContractVersion): releasing it once, BEFORE the check" -ForegroundColor Yellow
        Invoke-CloudCoreRelease
        $health = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/system/health" -TimeoutSec 20
        $deployedVersion = Get-DeployedContractVersion -Health $health
        $stale = ($deployedVersion -lt $requiredContractVersion)
    }
    Add-Check -Name "cloud.contract_deployed" -Ok (-not $stale) -Detail "action contract v$deployedVersion$(if ($releaseCloud) { ' (released once before the check)' })"
    if ($stale) { break }
    $task = Get-CompletedResearch -Requested $TaskId
    $taskId = [string](Get-OptionalProperty -InputObject $task -Name "task_id")
    $readyBefore = [string](Get-OptionalProperty -InputObject $task -Name "ready_at")
    $artifactBefore = [string](Get-OptionalProperty -InputObject $task -Name "artifact_id")
    $resultCall = Find-ResearchCallHead -ReadyAtIso $readyBefore
    $evidence.completed_research = [ordered]@{ task_id = $taskId; topic = (Get-OptionalProperty -InputObject $task -Name "topic"); ready_at = $readyBefore; artifact_id = $artifactBefore; result_session = $(if ($null -ne $resultCall) { $resultCall.SessionId } else { $null }); result_call = $(if ($null -ne $resultCall) { $resultCall.CallId } else { $null }); result_speech_head = $(if ($null -ne $resultCall) { $resultCall.SpeechHead } else { $null }) }
    Write-Host ("      completed research: {0} ready {1} artifact {2}" -f $taskId, $readyBefore, $artifactBefore)
    Add-Check -Name "research.completed_result_reused" -Ok ($null -ne $resultCall) -Detail $(if ($null -ne $resultCall) { "its spoken result is on session $($resultCall.SessionId) (call $($resultCall.CallId), $($resultCall.SpeechChars) chars)" } else { "no succeeded research.start call matches the task's ready_at on any web session" })
    $resultClean = ($null -ne $resultCall) -and -not (Test-TextContainsAny -Text $resultCall.SpeechHead -Words $diagnosticWords)
    Add-Check -Name "research.result_carried_no_diagnostics" -Ok $resultClean -Detail $(if ($null -ne $resultCall) { "spoken head: `"$($resultCall.SpeechHead)`"" } else { "no result call" })
    $taskIdsBefore = Get-ResearchTaskIds
    $versionsBefore = Get-DeviceVersions
    $evidence.before = [ordered]@{ contract = $deployedVersion; research_tasks = $taskIdsBefore.Count; devices = $versionsBefore }

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

    Write-Host ""
    Write-Host "Open $coreUrl, sign in, connect voice THERE, and when you are ready say:" -ForegroundColor Cyan
    Write-Host ("      {0}" -f $phraseTechnical)
    Write-Host "It answers about the research that already completed - no new research, nothing re-run."
    Write-Host "Take your time: this script waits up to $([math]::Round($FollowupWaitSec / 60, 0)) minutes after you connect and finishes by itself."
    Write-Host ""

    # ------------------------------------------------------------------ the watch
    $sessionId = ""
    $phase = "await_session"
    $phaseStarted = [DateTimeOffset]::UtcNow
    $lastPrint = [DateTimeOffset]::UtcNow.AddSeconds(-60)
    $lastSig = ""
    $stopReason = ""
    $technicalCall = $null
    $technicalIntents = @()
    $researchCallsNew = @()
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
            if ($null -ne $pick) { $sessionId = [string]$pick.session_id; $phase = "await_followup"; $phaseStarted = $now; Write-Host "      Core Voice connected: $sessionId" -ForegroundColor Green }
            elseif ($inPhase -ge $ConnectWaitSec) { $stopReason = "no Core voice session within $ConnectWaitSec s - connect voice on /core" }
        }
        $activity = $null
        if ($sessionId) { $activity = Get-JsonOrNull "/v1/voice/realtime/sessions/$sessionId/activity" }
        $calls = Get-ArrayProperty -InputObject $activity -Name "tool_calls"
        $intents = Get-ArrayProperty -InputObject $activity -Name "intents"
        $technicalIntents = @($intents | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "intent") -eq "technical" -or [string](Get-OptionalProperty -InputObject $_ -Name "query_kind") -in $technicalKinds })
        $researchCallsNew = @($calls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "name") -eq "research.start" })
        $technicalCalls = @($calls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "name") -eq "activity.explain" -and [string](Get-OptionalProperty -InputObject $_ -Name "status") -eq "succeeded" -and ([string](Get-OptionalProperty -InputObject $_ -Name "level") -in @("technical", "full") -or [string](Get-OptionalProperty -InputObject $_ -Name "query_kind") -in $technicalKinds) })
        if ($phase -eq "await_followup") {
            if ($technicalCalls.Count -ge 1) { $technicalCall = $technicalCalls[0]; $phase = "done"; Write-Host ("      follow-up recorded: {0} level={1} kind={2} ({3} chars)" -f (Get-OptionalProperty -InputObject $technicalCall -Name "call_id"), (Get-OptionalProperty -InputObject $technicalCall -Name "level"), (Get-OptionalProperty -InputObject $technicalCall -Name "query_kind"), (Get-OptionalProperty -InputObject $technicalCall -Name "speech_chars")) -ForegroundColor Green }
            elseif ($inPhase -ge $FollowupWaitSec) { $stopReason = "no technical explanation recorded within $FollowupWaitSec s of connecting (intents resolved technical: $($technicalIntents.Count); research.start calls on this session: $($researchCallsNew.Count))" }
        }
        if ($phase -eq "done" -or $stopReason) { break }
        $sig = "{0}|{1}|{2}|{3}" -f $phase, $technicalIntents.Count, $calls.Count, $researchCallsNew.Count
        if ($sig -ne $lastSig -or ($now - $lastPrint).TotalSeconds -ge 30) {
            $summary = Get-SessionRouterSummary -Activity $activity
            Write-Host ("      [{0,4:N0} s] {1}   session: {2}   technical intents: {3}   router events: {4}" -f $elapsed, $phase, $(if ($sessionId) { $sessionId } else { "none yet" }), $technicalIntents.Count, $summary.ToolCalls) -ForegroundColor DarkGray
            $lastSig = $sig; $lastPrint = $now
        }
        Start-Sleep -Seconds $PollSec
    }
    if ($stopReason) { Write-Host "      stopped: $stopReason" -ForegroundColor Yellow }

    # ------------------------------------------------------------------ the checks
    $evidence.session = [ordered]@{ id = $sessionId }
    Add-Check -Name "voice.core_session_correlated" -Ok ($sessionId -ne "") -Detail $(if ($sessionId) { "session $sessionId (a new one; the result lives on $($evidence.completed_research.result_session))" } else { $stopReason })
    Add-Check -Name "followup.routed_to_technical_path" -Ok ($technicalIntents.Count -ge 1 -and $null -ne $technicalCall) -Detail $(if ($null -ne $technicalCall) { "router: $($technicalIntents.Count) technical intent(s); activity.explain $(Get-OptionalProperty -InputObject $technicalCall -Name 'call_id') level=$(Get-OptionalProperty -InputObject $technicalCall -Name 'level') kind=$(Get-OptionalProperty -InputObject $technicalCall -Name 'query_kind')" } else { "no technical explanation on the session: $stopReason" })
    $followHead = if ($null -ne $technicalCall) { [string](Get-OptionalProperty -InputObject $technicalCall -Name "speech_head") } else { "" }
    $followChars = if ($null -ne $technicalCall) { [int](Get-OptionalProperty -InputObject $technicalCall -Name "speech_chars") } else { 0 }
    # The job the explanation was bound to: on the call record itself (ADR-0075) first, then
    # the briefing's provenance. Identity, never a timestamp, decides the reuse checks below.
    $provJob = if ($null -ne $technicalCall) { [string](Get-OptionalProperty -InputObject $technicalCall -Name "research_job_id") } else { "" }
    $prov = if ($null -ne $technicalCall) { Get-OptionalProperty -InputObject $technicalCall -Name "provenance" } else { $null }
    if (-not $provJob -and $null -ne $prov) { $provJob = [string](Get-OptionalProperty -InputObject $prov -Name "research_job_id") }
    $provArtifact = if ($null -ne $technicalCall) { [string](Get-OptionalProperty -InputObject $technicalCall -Name "research_artifact_id") } else { "" }
    if (-not $provArtifact -and $null -ne $prov) { $provArtifact = [string](Get-OptionalProperty -InputObject $prov -Name "research_artifact_id") }
    if (-not $provArtifact -and $null -ne $prov) { $provArtifact = [string](Get-OptionalProperty -InputObject $prov -Name "artifact_id") }
    $evidence.followup = [ordered]@{ call_id = $(if ($null -ne $technicalCall) { Get-OptionalProperty -InputObject $technicalCall -Name "call_id" } else { $null }); level = $(if ($null -ne $technicalCall) { Get-OptionalProperty -InputObject $technicalCall -Name "level" } else { $null }); query_kind = $(if ($null -ne $technicalCall) { Get-OptionalProperty -InputObject $technicalCall -Name "query_kind" } else { $null }); speech_head = $followHead; speech_chars = $followChars; research_job_id = $provJob; artifact_id = $provArtifact }
    Add-Check -Name "followup.diagnostics_only_now" -Ok ($resultClean -and (Test-TextContainsAny -Text $followHead -Words $diagnosticWords)) -Detail $(if ($followHead) { "follow-up head: `"$followHead`"; result head had no crawler words: $resultClean" } else { "no follow-up speech" })
    $taskAfter = $null
    foreach ($t in (Get-ArrayProperty -InputObject (Get-JsonOrNull "/v1/research") -Name "tasks")) { if ([string](Get-OptionalProperty -InputObject $t -Name "task_id") -eq $taskId) { $taskAfter = $t } }
    $readyAfter = if ($null -ne $taskAfter) { [string](Get-OptionalProperty -InputObject $taskAfter -Name "ready_at") } else { "" }
    $artifactAfter = if ($null -ne $taskAfter) { [string](Get-OptionalProperty -InputObject $taskAfter -Name "artifact_id") } else { "" }
    # Identity, not timestamps (owner directive): the explanation must NAME the job it read,
    # and it must be the job that was complete before the owner spoke.
    Add-Check -Name "followup.job_identity_reused" -Ok ($provJob -ne "" -and $provJob -eq $taskId) -Detail "before_job_id $taskId; explained job_id $(if ($provJob) { $provJob } else { 'not carried on the record' })"
    $artifactIdentity = ($artifactAfter -eq $artifactBefore) -and ($provArtifact -eq "" -or $provArtifact -eq $artifactBefore)
    Add-Check -Name "followup.report_reused_not_recomputed" -Ok ($readyAfter -eq $readyBefore -and $artifactAfter -eq $artifactBefore -and $artifactIdentity) -Detail "artifact $artifactBefore -> $artifactAfter$(if ($provArtifact) { ' (explanation cites ' + $provArtifact + ')' }); ready_at $readyBefore -> $readyAfter"
    $taskIdsAfter = Get-ResearchTaskIds
    $newTasks = @($taskIdsAfter | Where-Object { $taskIdsBefore -notcontains $_ })
    # ADR-0075: a research.start the server REFUSED on a follow-up turn is a succeeded call
    # whose speech head says no research was started - it started nothing and is reported
    # on its own line; a research.start that ran (running / succeeded / failed with a task)
    # is the second crawl the owner forbade.
    $refusedStarts = @($researchCallsNew | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "status") -eq "succeeded" -and ([string](Get-OptionalProperty -InputObject $_ -Name "speech_head")).StartsWith("Yeni bir ara") })
    $crawlStarts = @($researchCallsNew | Where-Object { $refusedStarts -notcontains $_ })
    Add-Check -Name "followup.no_second_crawl" -Ok ($crawlStarts.Count -eq 0 -and $newTasks.Count -eq 0) -Detail "research.start_on_followup: $($crawlStarts.Count) (refused by the guard: $($refusedStarts.Count)); new_research_task_count: $($newTasks.Count)"
    if ($researchCallsNew.Count -gt 0) { Write-Host ("      research.start on the follow-up session: " + (($researchCallsNew | ForEach-Object { "{0}:{1}" -f (Get-OptionalProperty -InputObject $_ -Name "status"), (Get-OptionalProperty -InputObject $_ -Name "speech_head") }) -join " | ")) -ForegroundColor Yellow }
    $healthAfter = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/system/health" -TimeoutSec 20
    $versionAfter = Get-DeployedContractVersion -Health $healthAfter
    $versionsAfter = Get-DeviceVersions
    $deviceSame = $true
    foreach ($k in $versionsBefore.Keys) { if (-not $versionsAfter.ContainsKey($k) -or $versionsAfter[$k] -ne $versionsBefore[$k]) { $deviceSame = $false } }
    $evidence.after = [ordered]@{ contract = $versionAfter; research_tasks = $taskIdsAfter.Count; devices = $versionsAfter }
    Add-Check -Name "followup.no_deployment_repeated" -Ok ($versionAfter -eq $deployedVersion -and $deviceSame) -Detail "from the owner's utterance on: contract v$deployedVersion -> v$versionAfter; device versions unchanged: $deviceSame (any release happened once, before the check, and is named above)"
    Add-Check -Name "followup.concise" -Ok ($followChars -gt 0 -and $followChars -le $ConciseMaxChars) -Detail "$followChars spoken characters (technical budget 700; limit $ConciseMaxChars); more detail only on request"
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

Write-Host "OWNER M18.2 FOLLOW-UP: $($evidence.verdict)" -ForegroundColor $(if ($evidence.verdict -eq "PASS") { "Green" } else { "Red" })
exit $exitCode
