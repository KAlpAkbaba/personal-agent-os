<#
.SYNOPSIS
    M18.2 follow-up-only owner check, third form (ADR-0076): the owner points at ONE existing
    research result, then says two sentences, and the record must show exact identities:

      select/open a completed research (in /core's cockpit or /research)   -> focus = X (by id)
      "Bunu teknik anlat."                                                  -> explained job == X
      "Bir onceki arastirmayi anlat."                                       -> explained job == Y (the previous focus)

    with zero new research jobs, zero crawls, the exact existing reports reused, and no
    deployment from the first sentence on. No research is re-run for this.

.DESCRIPTION
    Identity, never titles or timestamps (owner directive after the real runs of 2026-09-06):
    the focus document (GET /v1/research/focus) names the current and the previous research
    by job id; every research-bound explanation on the session names the job and artifact it
    read (research_job_id / research_artifact_id on the tool-call record); the check compares
    ids. A clarification question is allowed at most once; a second one in a row is the loop
    the owner hit and is reported as such.

    Preflight releases the Cloud Core once, BEFORE the check, when the deployed action
    contract predates this checkout's (the focus, the resolver and the guard must be running
    there); `followup.no_deployment_repeated` is measured from the owner's first sentence on.

.PARAMETER SelectTaskId
    Sets the focus through the API instead of waiting for the owner's click - a PROXY for the
    UI selection, named as such in the evidence. Without it the harness waits for a focus
    whose source is owner_selected_in_ui.

.EXAMPLE
    .\scripts\core\owner-m18-2-followup.ps1 -OutFile m18-2-followup-2.json
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    [int]$WebPort = 3000,
    [string]$BaseUrl = "",
    [string]$OutFile = "",
    [string]$SelectTaskId = "",
    [switch]$SkipWeb,
    [string]$PnpmPath = "pnpm",
    [ValidateSet("auto", "never", "force")][string]$CloudCoreUpdate = "auto",
    [ValidateRange(30, 900)][int]$ConnectWaitSec = 180,
    [ValidateRange(30, 1800)][int]$SelectWaitSec = 300,
    [ValidateRange(30, 1800)][int]$StepWaitSec = 300,
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
$requiredContractVersion = Get-CheckoutActionContractVersion -RepoRoot $repoRoot
if ($requiredContractVersion -lt 8) { throw "this checkout carries action contract v$requiredContractVersion; the focus/resolver check needs v8 or later (ADR-0076)" }

$u_uml = [char]0x00FC; $i_dot = [char]0x0131; $o_uml = [char]0x00F6; $s_ced = [char]0x015F; $c_ced = [char]0x00E7
$phraseThis = "Bunu teknik anlat."
$phrasePrevious = "Bir " + $o_uml + "nceki ara" + $s_ced + "t" + $i_dot + "rmay" + $i_dot + " anlat."
$wordPagesTr = "sayfay" + $i_dot
$wordEvidenceTr = "kan" + $i_dot + "t"
$wordVersionTr = "s" + $u_uml + "r" + $u_uml + "m"
$diagnosticWords = @("elendi", "eledi", "interstitial", "dedup", "aday", $wordPagesTr, "sayfa", $wordEvidenceTr, $wordVersionTr, "policy", "cooldown")
$clarificationHead = "Efendim, iki tamamlanm"
$askWhichHead = "Hangi ara"
$explainTools = @("research.explain", "research.sources", "research.finding_detail", "activity.explain")
$technicalKinds = @("technical", "research_problems", "rejected_pages", "research_detail")

$evidence = [ordered]@{ run_id = $runId; started_at = $startedAt.ToString("o"); cloud = $BaseUrl; web_shell = $null; reports = @(); focus_before = $null; focus_selected = $null; focus_after = $null; session = $null; steps = @(); checks = @(); verdict = "FAIL" }

Write-Host "PagentOS owner M18.2 follow-up, third form ($runId)"
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
    param([string]$Name, [bool]$Ok, [string]$Detail, [switch]$Optional)
    $script:evidence.checks += [ordered]@{ name = $Name; ok = $Ok; detail = $Detail; optional = [bool]$Optional }
    $mark = if ($Ok) { "ok  " } elseif ($Optional) { "skip" } else { "FAIL" }
    $color = if ($Ok) { "Green" } elseif ($Optional) { "Yellow" } else { "Red" }
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
    $doc = Get-JsonOrNull "/v1/devices"
    $out = @{}
    foreach ($d in (Get-ArrayProperty -InputObject $doc -Name "devices")) {
        $id = [string](Get-OptionalProperty -InputObject $d -Name "device_id")
        $healthDoc = Get-OptionalProperty -InputObject $d -Name "health"
        $version = ""
        if ($null -ne $healthDoc) { $version = [string](Get-OptionalProperty -InputObject $healthDoc -Name "software_version") }
        if (-not $version) { $version = [string](Get-OptionalProperty -InputObject $d -Name "software_version") }
        $out[$id] = $version
    }
    return $out
}

function Get-CompletedReports {
    <#  READY research tasks, newest first, with the fields identity rests on.  #>
    $doc = Get-JsonOrNull "/v1/research"
    $ready = @()
    foreach ($t in (Get-ArrayProperty -InputObject $doc -Name "tasks")) {
        if ([string](Get-OptionalProperty -InputObject $t -Name "status") -ne "READY") { continue }
        $ready += [pscustomobject]@{
            TaskId     = [string](Get-OptionalProperty -InputObject $t -Name "task_id")
            Topic      = [string](Get-OptionalProperty -InputObject $t -Name "topic")
            ReadyAt    = [string](Get-OptionalProperty -InputObject $t -Name "ready_at")
            ArtifactId = [string](Get-OptionalProperty -InputObject $t -Name "artifact_id")
            Mode       = [string](Get-OptionalProperty -InputObject $t -Name "mode")
        }
    }
    $sorted = @($ready | Sort-Object -Property ReadyAt -Descending)
    return , $sorted
}

function Get-ResearchTaskIds {
    $doc = Get-JsonOrNull "/v1/research"
    $ids = @()
    foreach ($t in (Get-ArrayProperty -InputObject $doc -Name "tasks")) { $ids += [string](Get-OptionalProperty -InputObject $t -Name "task_id") }
    return , $ids
}

function Get-Focus { return Get-JsonOrNull "/v1/research/focus" }

function Get-FocusEntryId { param($Entry) if ($null -eq $Entry) { return "" }; return [string](Get-OptionalProperty -InputObject $Entry -Name "research_job_id") }

function Get-BoundJob {
    <#  The job a tool call names: top level first (ADR-0076), then its provenance.  #>
    param($Call)
    $job = [string](Get-OptionalProperty -InputObject $Call -Name "research_job_id")
    if (-not $job) {
        $prov = Get-OptionalProperty -InputObject $Call -Name "provenance"
        if ($null -ne $prov) { $job = [string](Get-OptionalProperty -InputObject $prov -Name "research_job_id") }
    }
    return $job
}

function Get-BoundArtifact {
    param($Call)
    $art = [string](Get-OptionalProperty -InputObject $Call -Name "research_artifact_id")
    if (-not $art) {
        $prov = Get-OptionalProperty -InputObject $Call -Name "provenance"
        if ($null -ne $prov) { $art = [string](Get-OptionalProperty -InputObject $prov -Name "research_artifact_id") }
    }
    return $art
}

function Get-ExplainCallsAfter {
    <#  Succeeded research-bound explanations created after Since, oldest first.  #>
    param($Calls, [DateTimeOffset]$Since)
    $out = @()
    foreach ($c in (ConvertTo-Array -Value $Calls)) {
        if ($explainTools -notcontains [string](Get-OptionalProperty -InputObject $c -Name "name")) { continue }
        if ([string](Get-OptionalProperty -InputObject $c -Name "status") -ne "succeeded") { continue }
        $at = ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $c -Name "created_at"))
        if ($null -eq $at -or -not (Test-InstantAtOrAfter -Instant $at -Floor $Since -ToleranceSec 1)) { continue }
        $out += $c
    }
    $sorted = @($out | Sort-Object -Property { [string](Get-OptionalProperty -InputObject $_ -Name "created_at") })
    return , $sorted
}

function Test-ClarificationHead {
    param([AllowNull()]$Text)
    $t = [string]$Text
    return ($t.StartsWith($clarificationHead) -or $t.StartsWith($askWhichHead))
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
    if ($stale -and $CloudCoreUpdate -eq "never") { throw "the deployed Cloud Core is stale (v$deployedVersion < v$requiredContractVersion): the focus/resolver is not running there; -CloudCoreUpdate never refuses to release" }
    if ($releaseCloud -and $releaseBlockers.Blocked) { Write-ReleaseBlockers -Blockers $releaseBlockers; throw "a Cloud Core release is required but the working tree has $($blockerChanges.Count) uncommitted change(s); commit or revert them, then rerun" }
    if ($releaseCloud) {
        Write-Host "      the deployed Cloud Core predates the research focus (v$deployedVersion < v$requiredContractVersion): releasing it once, BEFORE the check" -ForegroundColor Yellow
        Invoke-CloudCoreRelease
        $health = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/system/health" -TimeoutSec 20
        $deployedVersion = Get-DeployedContractVersion -Health $health
        $stale = ($deployedVersion -lt $requiredContractVersion)
    }
    Add-Check -Name "cloud.contract_deployed" -Ok (-not $stale) -Detail "action contract v$deployedVersion$(if ($releaseCloud) { ' (released once before the check)' })"
    if ($stale) { break }

    # ------------------------------------------------------------------ what is already there
    $reports = Get-CompletedReports
    foreach ($r in $reports) { $evidence.reports += [ordered]@{ task_id = $r.TaskId; topic = $r.Topic; ready_at = $r.ReadyAt; artifact_id = $r.ArtifactId; mode = $r.Mode } }
    Add-Check -Name "research.two_completed_reports_exist" -Ok ($reports.Count -ge 2) -Detail "$($reports.Count) completed report(s); the two most recent: $(if ($reports.Count -ge 2) { $reports[0].TaskId + ' (' + $reports[0].ReadyAt + '), ' + $reports[1].TaskId + ' (' + $reports[1].ReadyAt + ')' } else { 'fewer than two - the retest needs a previous one to point back to' })"
    if ($reports.Count -lt 2) { break }
    $focusBefore = Get-Focus
    $evidence.focus_before = $focusBefore
    Add-Check -Name "research.focus_route_present" -Ok ($null -ne $focusBefore) -Detail $(if ($null -ne $focusBefore) { "current: " + (Get-FocusEntryId -Entry (Get-OptionalProperty -InputObject $focusBefore -Name "current")) + "; previous: " + (Get-FocusEntryId -Entry (Get-OptionalProperty -InputObject $focusBefore -Name "previous")) } else { "GET /v1/research/focus did not answer" })
    if ($null -eq $focusBefore) { break }
    $taskIdsBefore = Get-ResearchTaskIds
    $versionsBefore = Get-DeviceVersions

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

    # ------------------------------------------------------------------ 1. the owner points at one
    $selectedSource = ""
    $focusSelected = $null
    if ($SelectTaskId) {
        $null = Send-Json -Method "POST" -Path "/v1/research/$SelectTaskId/focus" -Body "{}"
        $focusSelected = Get-Focus
        $selectedSource = "api_proxy"
        Write-Host "      focus set through the API (a proxy for the UI selection): $SelectTaskId" -ForegroundColor Yellow
    }
    else {
        Write-Host ""
        Write-Host "Open $coreUrl (the cockpit's research panel) or http://localhost:$WebPort/research, sign in, and CLICK one" -ForegroundColor Cyan
        Write-Host "completed research - any one; two may have the same title, the second line tells them apart." -ForegroundColor Cyan
        Write-Host "This script continues by itself when your selection becomes the conversation focus."
        Write-Host ""
        $waitStart = [DateTimeOffset]::UtcNow
        while (([DateTimeOffset]::UtcNow - $waitStart).TotalSeconds -lt $SelectWaitSec) {
            $f = Get-Focus
            $cur = if ($null -ne $f) { Get-OptionalProperty -InputObject $f -Name "current" } else { $null }
            $src = if ($null -ne $cur) { [string](Get-OptionalProperty -InputObject $cur -Name "source_of_focus") } else { "" }
            $selAt = if ($null -ne $cur) { ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $cur -Name "selected_at")) } else { $null }
            if ($src -eq "owner_selected_in_ui" -and $null -ne $selAt -and (Test-InstantAtOrAfter -Instant $selAt -Floor $runStart -ToleranceSec 2)) { $focusSelected = $f; $selectedSource = $src; break }
            Start-Sleep -Seconds $PollSec
        }
    }
    $evidence.focus_selected = $focusSelected
    $current = if ($null -ne $focusSelected) { Get-OptionalProperty -InputObject $focusSelected -Name "current" } else { $null }
    $previous = if ($null -ne $focusSelected) { Get-OptionalProperty -InputObject $focusSelected -Name "previous" } else { $null }
    $jobX = Get-FocusEntryId -Entry $current
    $jobY = Get-FocusEntryId -Entry $previous
    $artifactX = if ($null -ne $current) { [string](Get-OptionalProperty -InputObject $current -Name "artifact_id") } else { "" }
    $artifactY = if ($null -ne $previous) { [string](Get-OptionalProperty -InputObject $previous -Name "artifact_id") } else { "" }
    Add-Check -Name "focus.selected_by_the_owner" -Ok ($jobX -ne "" -and $selectedSource -ne "") -Detail $(if ($jobX) { "focus X = $jobX (source $selectedSource); previous Y = $(if ($jobY) { $jobY } else { 'none' })" } else { "no owner selection became the focus within $SelectWaitSec s" })
    if (-not $jobX) { break }
    Add-Check -Name "focus.has_a_previous_to_point_back_to" -Ok ($jobY -ne "" -and $jobY -ne $jobX) -Detail $(if ($jobY) { "previous focus $jobY" } else { "the focus stack has no previous entry" })
    if (-not $jobY -or $jobY -eq $jobX) { break }

    # ------------------------------------------------------------------ the voice session
    $sessionId = ""
    $phaseStarted = [DateTimeOffset]::UtcNow
    Write-Host ""
    Write-Host "Now connect voice on $coreUrl (if it is not connected) and say, one after the other, waiting for each answer:" -ForegroundColor Cyan
    Write-Host ("  1. {0}" -f $phraseThis)
    Write-Host ("  2. {0}" -f $phrasePrevious)
    Write-Host "It finishes by itself. Nothing to press."
    Write-Host ""
    while (-not $sessionId -and ([DateTimeOffset]::UtcNow - $phaseStarted).TotalSeconds -lt $ConnectWaitSec) {
        $busEvents = Get-ArrayProperty -InputObject (Get-JsonOrNull "/v1/ui/state?limit=64") -Name "events"
        $busIds = Get-BusVoiceSessionIds -Events $busEvents -Since $runStart.AddSeconds(-5)
        $allSessions = Get-ArrayProperty -InputObject (Get-JsonOrNull "/v1/voice/realtime/sessions?limit=50") -Name "sessions"
        $mine = Get-NewSessions -Sessions $allSessions -BaselineIds $baselineIds -ReadyAt $null -AcceptIds $busIds
        $named = @($mine | Where-Object { $busIds -contains [string]$_.session_id })
        $pick = if ($named.Count -gt 0) { $named[$named.Count - 1] } elseif ($mine.Count -gt 0) { $mine[0] } else { $null }
        if ($null -ne $pick) { $sessionId = [string]$pick.session_id; Write-Host "      Core Voice connected: $sessionId" -ForegroundColor Green; break }
        Start-Sleep -Seconds $PollSec
    }
    $evidence.session = [ordered]@{ id = $sessionId }
    Add-Check -Name "voice.core_session_correlated" -Ok ($sessionId -ne "") -Detail $(if ($sessionId) { "session $sessionId" } else { "no Core voice session within $ConnectWaitSec s" })
    if (-not $sessionId) { break }
    $stepStart = [DateTimeOffset]::UtcNow

    # ------------------------------------------------------------------ 2 + 3. the two sentences
    $stepCalls = @()
    $stopReason = ""
    $lastPrint = [DateTimeOffset]::UtcNow.AddSeconds(-60)
    $expected = @($jobX, $jobY)
    $stepIndex = 0
    $stepStartedAt = $stepStart
    while ($stepIndex -lt 2) {
        $now = [DateTimeOffset]::UtcNow
        $activity = Get-JsonOrNull "/v1/voice/realtime/sessions/$sessionId/activity"
        $calls = Get-ArrayProperty -InputObject $activity -Name "tool_calls"
        $explains = Get-ExplainCallsAfter -Calls $calls -Since $stepStartedAt
        $bound = @($explains | Where-Object { (Get-BoundJob -Call $_) -ne "" })
        if ($bound.Count -ge 1) {
            $call = $bound[0]
            $stepCalls += $call
            $stepIndex++
            $stepStartedAt = (ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $call -Name "created_at"))).AddSeconds(1)
            Write-Host ("      step {0} recorded: {1} bound to {2} ({3} chars)" -f $stepIndex, (Get-OptionalProperty -InputObject $call -Name "name"), (Get-BoundJob -Call $call), (Get-OptionalProperty -InputObject $call -Name "speech_chars")) -ForegroundColor Green
            continue
        }
        if (($now - $stepStartedAt).TotalSeconds -ge $StepWaitSec) { $stopReason = "no research-bound explanation for step $($stepIndex + 1) within $StepWaitSec s"; break }
        if (($now - $lastPrint).TotalSeconds -ge 30) {
            $summary = Get-SessionRouterSummary -Activity $activity
            Write-Host ("      [{0,4:N0} s] waiting for step {1}   router events: {2}" -f ($now - $runStart).TotalSeconds, ($stepIndex + 1), $summary.ToolCalls) -ForegroundColor DarkGray
            $lastPrint = $now
        }
        Start-Sleep -Seconds $PollSec
    }
    if ($stopReason) { Write-Host "      stopped: $stopReason" -ForegroundColor Yellow }

    # ------------------------------------------------------------------ the checks
    $activity = Get-JsonOrNull "/v1/voice/realtime/sessions/$sessionId/activity"
    $calls = Get-ArrayProperty -InputObject $activity -Name "tool_calls"
    foreach ($c in $stepCalls) { $evidence.steps += [ordered]@{ call_id = (Get-OptionalProperty -InputObject $c -Name "call_id"); name = (Get-OptionalProperty -InputObject $c -Name "name"); level = (Get-OptionalProperty -InputObject $c -Name "level"); query_kind = (Get-OptionalProperty -InputObject $c -Name "query_kind"); research_job_id = (Get-BoundJob -Call $c); research_artifact_id = (Get-BoundArtifact -Call $c); resolution_reason = (Get-OptionalProperty -InputObject $c -Name "resolution_reason"); speech_head = (Get-OptionalProperty -InputObject $c -Name "speech_head"); speech_chars = (Get-OptionalProperty -InputObject $c -Name "speech_chars") } }
    $step1 = if ($stepCalls.Count -ge 1) { $stepCalls[0] } else { $null }
    $step2 = if ($stepCalls.Count -ge 2) { $stepCalls[1] } else { $null }
    $job1 = if ($null -ne $step1) { Get-BoundJob -Call $step1 } else { "" }
    $job2 = if ($null -ne $step2) { Get-BoundJob -Call $step2 } else { "" }
    $art1 = if ($null -ne $step1) { Get-BoundArtifact -Call $step1 } else { "" }
    $art2 = if ($null -ne $step2) { Get-BoundArtifact -Call $step2 } else { "" }
    $head1 = if ($null -ne $step1) { [string](Get-OptionalProperty -InputObject $step1 -Name "speech_head") } else { "" }
    $chars1 = if ($null -ne $step1) { [int](Get-OptionalProperty -InputObject $step1 -Name "speech_chars") } else { 0 }
    $chars2 = if ($null -ne $step2) { [int](Get-OptionalProperty -InputObject $step2 -Name "speech_chars") } else { 0 }
    Add-Check -Name "step1.bound_to_the_selected_research" -Ok ($job1 -ne "" -and $job1 -eq $jobX -and ($art1 -eq "" -or $art1 -eq $artifactX)) -Detail "selected X = $jobX; explained job = $(if ($job1) { $job1 } else { 'none' })$(if ($art1) { '; artifact ' + $art1 + ' (X: ' + $artifactX + ')' }); reason $(if ($null -ne $step1) { Get-OptionalProperty -InputObject $step1 -Name 'resolution_reason' })"
    $tech1 = ($null -ne $step1) -and ([string](Get-OptionalProperty -InputObject $step1 -Name "level") -in @("technical", "full") -or [string](Get-OptionalProperty -InputObject $step1 -Name "query_kind") -in $technicalKinds -or (Test-TextContainsAny -Text $head1 -Words $diagnosticWords))
    Add-Check -Name "step1.technical_diagnostics_from_that_report" -Ok $tech1 -Detail $(if ($head1) { "head: `"$head1`"" } else { "no step-1 speech" })
    Add-Check -Name "step2.bound_to_the_previous_research" -Ok ($job2 -ne "" -and $job2 -eq $jobY -and ($art2 -eq "" -or $art2 -eq $artifactY)) -Detail "previous Y = $jobY; explained job = $(if ($job2) { $job2 } else { 'none' })$(if ($art2) { '; artifact ' + $art2 + ' (Y: ' + $artifactY + ')' }); reason $(if ($null -ne $step2) { Get-OptionalProperty -InputObject $step2 -Name 'resolution_reason' })"
    $focusAfter = Get-Focus
    $evidence.focus_after = $focusAfter
    $currentAfter = Get-FocusEntryId -Entry (Get-OptionalProperty -InputObject $focusAfter -Name "current")
    Add-Check -Name "focus.moved_to_the_previous_after_step2" -Optional -Ok ($currentAfter -eq $jobY) -Detail "focus after: $currentAfter (Y = $jobY)"
    $starts = @($calls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "name") -eq "research.start" })
    $refusedStarts = @($starts | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "status") -eq "succeeded" -and ([string](Get-OptionalProperty -InputObject $_ -Name "speech_head")).StartsWith("Yeni bir ara") })
    $crawlStarts = @($starts | Where-Object { $refusedStarts -notcontains $_ })
    $taskIdsAfter = Get-ResearchTaskIds
    $newTasks = @($taskIdsAfter | Where-Object { $taskIdsBefore -notcontains $_ })
    Add-Check -Name "followup.no_second_crawl" -Ok ($crawlStarts.Count -eq 0 -and $newTasks.Count -eq 0) -Detail "research.start that ran: $($crawlStarts.Count) (refused by the guard: $($refusedStarts.Count)); new research tasks: $($newTasks.Count)"
    $reportsAfter = Get-CompletedReports
    $unchanged = $true
    foreach ($id in @($jobX, $jobY)) {
        $b = @($reports | Where-Object { $_.TaskId -eq $id })
        $a = @($reportsAfter | Where-Object { $_.TaskId -eq $id })
        if ($b.Count -ne 1 -or $a.Count -ne 1 -or $b[0].ReadyAt -ne $a[0].ReadyAt -or $b[0].ArtifactId -ne $a[0].ArtifactId) { $unchanged = $false }
    }
    Add-Check -Name "followup.exact_reports_reused" -Ok $unchanged -Detail "X and Y keep their ready_at and artifact ids (no recomputation, no new synthesis)"
    $clarifications = @($calls | Where-Object { $explainTools -contains [string](Get-OptionalProperty -InputObject $_ -Name "name") -and (Test-ClarificationHead -Text (Get-OptionalProperty -InputObject $_ -Name "speech_head")) })
    Add-Check -Name "followup.no_clarification_loop" -Ok ($clarifications.Count -le 1) -Detail "clarification questions on the session: $($clarifications.Count) (at most one is allowed; the owner's real run heard six)"
    $healthAfter = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/system/health" -TimeoutSec 20
    $versionAfter = Get-DeployedContractVersion -Health $healthAfter
    $versionsAfter = Get-DeviceVersions
    $deviceSame = $true
    foreach ($k in $versionsBefore.Keys) { if (-not $versionsAfter.ContainsKey($k) -or $versionsAfter[$k] -ne $versionsBefore[$k]) { $deviceSame = $false } }
    Add-Check -Name "followup.no_deployment_repeated" -Ok ($versionAfter -eq $deployedVersion -and $deviceSame) -Detail "from the first sentence on: contract v$deployedVersion -> v$versionAfter; device versions unchanged: $deviceSame"
    Add-Check -Name "followup.concise" -Ok ($chars1 -gt 0 -and $chars1 -le $ConciseMaxChars -and ($null -eq $step2 -or ($chars2 -gt 0 -and $chars2 -le $ConciseMaxChars))) -Detail "spoken characters: step 1 $chars1, step 2 $chars2 (limit $ConciseMaxChars)"
    $failed = @($evidence.checks | Where-Object { -not $_.ok -and -not $_.optional })
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
