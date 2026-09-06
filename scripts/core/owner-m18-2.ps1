<#
.SYNOPSIS
    M18.2 owner check, from /core alone: Test A (speaking is a lifecycle - the Core stays
    SPEAKING through the pauses of a long answer and ends at the last audio) and Test B (a real
    research question is answered with its findings, and the diagnostics only when asked).

.DESCRIPTION
    Evidence, all from the Cloud Core's record of the Core's own voice session (correlated by
    the session id the Core publishes on the bus, never by a tool name):

      A1  a turn whose first_audio precedes its audio_done: the session's own timing events,
          with the playback basis (provider = output_audio_buffer.stopped; silence; cap) and the
          audible duration - a multi-sentence answer is one turn, however many pauses it has;
      A2  no audio_done basis "interrupted" is required; if the owner says "Dur" during the
          answer, the interrupted end is reported as such (an owner choice, not a defect);
      B1  a research.start tool call that reached a terminal receipt (succeeded) whose spoken
          head is the findings - none of: elendi, eledi, interstitial, dedup, aday, N sayfa;
      B2  a follow-up technical request (activity.explain at the technical level, or the
          rejected_pages / research_problems kinds) AFTER the result - the diagnostics on request.

    Windows are per step and bounded; the run finishes by itself when B2 is recorded. Preflight
    gates on the deployed action-contract version (this test needs v4: the research terminal
    schema) and releases the Cloud Core once if it is older.

.EXAMPLE
    .\scripts\core\owner-m18-2.ps1 -OutFile m18-2-1.json
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    [int]$WebPort = 3000,
    [string]$BaseUrl = "",
    [string]$OutFile = "",
    [switch]$SkipWeb,
    [string]$PnpmPath = "pnpm",
    [ValidateSet("auto", "never", "force")][string]$CloudCoreUpdate = "auto",
    [ValidateRange(30, 600)][int]$ConnectWaitSec = 120,
    [ValidateRange(30, 600)][int]$SpeakWaitSec = 180,
    [ValidateRange(60, 1800)][int]$ResearchWaitSec = 600,
    [ValidateRange(30, 600)][int]$TechnicalWaitSec = 120,
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
$runId = "owner-m18-2-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$startedAt = (Get-Date).ToUniversalTime()
$runStart = [DateTimeOffset]::UtcNow
$requiredContractVersion = 4

$u_uml = [char]0x00FC; $c_ced = [char]0x00E7; $g_br = [char]0x011F; $i_dot = [char]0x0131; $s_ced = [char]0x015F
$phraseLong = "Bana PagentOS'un ne oldu" + $g_br + "unu be" + $s_ced + " c" + $u_uml + "mleyle anlat."
$phraseResearch = "Son " + $u_uml + $c_ced + " g" + $u_uml + "ndeki yapay zek" + [char]0x00E2 + " ajan geli" + $s_ced + "melerini ara" + $s_ced + "t" + $i_dot + "r."
$phraseTechnical = "Teknik anlat."
$bannedInResult = @("elendi", "eledi", "interstitial", "dedup", " aday", "sayfay" + $i_dot, "sayfa ele")

$evidence = [ordered]@{ run_id = $runId; started_at = $startedAt.ToString("o"); cloud = $BaseUrl; web_shell = $null; session = $null; speaking = @(); research = $null; technical = $null; checks = @(); verdict = "FAIL" }

Write-Host "PagentOS owner M18.2 ($runId)"
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

function Get-SpeakingTurns {
    <#  Turns with a first_audio and, when present, their audio_done (basis, audible_ms), from the session's own events.  #>
    param($Events)
    $turns = @{}
    foreach ($e in (ConvertTo-Array -Value $Events)) {
        $kind = [string](Get-OptionalProperty -InputObject $e -Name "kind")
        if ($kind -notin @("first_audio", "audio_done", "response_done")) { continue }
        $turn = [int](Get-OptionalProperty -InputObject $e -Name "turn")
        if (-not $turns.ContainsKey($turn)) { $turns[$turn] = [ordered]@{ turn = $turn; first_audio_ms = $null; response_done_ms = $null; audio_done_ms = $null; basis = ""; audible_ms = $null } }
        $t = [double](Get-OptionalProperty -InputObject $e -Name "t_ms")
        $p = Get-OptionalProperty -InputObject $e -Name "payload"
        switch ($kind) {
            "first_audio"   { if ($null -eq $turns[$turn].first_audio_ms) { $turns[$turn].first_audio_ms = $t } }
            "response_done" { $turns[$turn].response_done_ms = $t }
            "audio_done"    { $turns[$turn].audio_done_ms = $t; $turns[$turn].basis = $(if ($null -ne $p) { [string](Get-OptionalProperty -InputObject $p -Name "basis") } else { "" }); $turns[$turn].audible_ms = $(if ($null -ne $p) { Get-OptionalProperty -InputObject $p -Name "audible_ms" } else { $null }) }
        }
    }
    $out = @()
    foreach ($k in ($turns.Keys | Sort-Object)) { $out += $turns[$k] }
    return , $out
}

function Test-ResultSpeechClean {
    param([string]$Text)
    $lower = $Text.ToLowerInvariant()
    foreach ($w in $bannedInResult) { if ($lower.Contains($w.ToLowerInvariant())) { return $false } }
    if ($lower -match "\d+\s*sayfa") { return $false }
    return $true
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
    if ($stale -and $CloudCoreUpdate -eq "never") { throw "the deployed Cloud Core is stale (v$deployedVersion < v$requiredContractVersion); -CloudCoreUpdate never refuses to release" }
    if ($releaseCloud -and $releaseBlockers.Blocked) { Write-ReleaseBlockers -Blockers $releaseBlockers; throw "a Cloud Core release is required but the working tree has $($blockerChanges.Count) uncommitted change(s); commit or revert them, then rerun" }
    if ($releaseCloud) {
        Write-Host "      the deployed Cloud Core predates the research result schema: releasing it once" -ForegroundColor Yellow
        Invoke-CloudCoreRelease
        $health = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/system/health" -TimeoutSec 20
        $deployedVersion = Get-DeployedContractVersion -Health $health
        $stale = ($deployedVersion -lt $requiredContractVersion)
    }
    Add-Check -Name "cloud.contract_deployed" -Ok (-not $stale) -Detail "action contract v$deployedVersion$(if ($releaseCloud) { ' (released in this run)' })"
    if ($stale) { break }

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
    Write-Host "Open $coreUrl, sign in, connect voice THERE. Then, in order:" -ForegroundColor Cyan
    Write-Host ("  A. say: {0}   (a long answer; watch the Core stay in SPEAKING through the pauses)" -f $phraseLong)
    Write-Host ("  B. say: {0}   (a real research run: a few minutes; the Core researches, then presents the findings)" -f $phraseResearch)
    Write-Host ("  C. say: {0}   (only now should you hear what was eliminated and why)" -f $phraseTechnical)
    Write-Host "This script finishes by itself after C. Nothing to press."
    Write-Host ""

    # ------------------------------------------------------------------ the watch
    $sessionId = ""
    $phase = "await_session"
    $phaseStarted = [DateTimeOffset]::UtcNow
    $lastPrint = [DateTimeOffset]::UtcNow.AddSeconds(-60)
    $lastSig = ""
    $stopReason = ""
    $speakingTurns = @()
    $researchCall = $null
    $technicalCall = $null
    while ($true) {
        $now = [DateTimeOffset]::UtcNow
        $inPhase = ($now - $phaseStarted).TotalSeconds
        $elapsed = ($now - $runStart).TotalSeconds
        $busEvents = Get-ArrayProperty -InputObject (Get-JsonOrNull "/v1/ui/state?limit=64") -Name "events"
        $busIds = Get-BusVoiceSessionIds -Events $busEvents -Since $runStart.AddSeconds(-5)
        $allSessions = Get-ArrayProperty -InputObject (Get-JsonOrNull "/v1/voice/realtime/sessions?limit=50") -Name "sessions"
        $mine = Get-NewSessions -Sessions $allSessions -BaselineIds $baselineIds -ReadyAt $null -AcceptIds $busIds
        if (-not $sessionId) {
            $named = @($mine | Where-Object { $busIds -contains [string]$_.session_id })
            $pick = if ($named.Count -gt 0) { $named[$named.Count - 1] } elseif ($mine.Count -gt 0) { $mine[0] } else { $null }
            if ($null -ne $pick) { $sessionId = [string]$pick.session_id; $phase = "await_speaking"; $phaseStarted = $now; Write-Host "      Core Voice connected: $sessionId" -ForegroundColor Green }
            elseif ($inPhase -ge $ConnectWaitSec) { $stopReason = "no Core voice session within $ConnectWaitSec s - connect voice on /core" }
        }
        $activity = $null
        if ($sessionId) { $activity = Get-JsonOrNull "/v1/voice/realtime/sessions/$sessionId/activity" }
        $events = Get-ArrayProperty -InputObject $activity -Name "client_events"
        $calls = Get-ArrayProperty -InputObject $activity -Name "tool_calls"
        $speakingTurns = Get-SpeakingTurns -Events $events
        $completed = @($speakingTurns | Where-Object { $null -ne $_.first_audio_ms -and $null -ne $_.audio_done_ms })
        $researchCalls = @($calls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "name") -eq "research.start" })
        $researchDone = @($researchCalls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "status") -in @("succeeded", "failed") })
        $technicalCalls = @($calls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "name") -eq "activity.explain" -and [string](Get-OptionalProperty -InputObject $_ -Name "status") -eq "succeeded" -and ([string](Get-OptionalProperty -InputObject $_ -Name "level") -in @("technical", "full") -or [string](Get-OptionalProperty -InputObject $_ -Name "query_kind") -in @("technical", "research_problems", "rejected_pages", "research_detail")) })
        switch ($phase) {
            "await_speaking" {
                if ($completed.Count -ge 1) { $phase = "await_research"; $phaseStarted = $now; Write-Host ("      A recorded: turn {0} first_audio {1} s -> audio_done {2} s (basis {3}, audible {4} ms)" -f $completed[0].turn, [math]::Round($completed[0].first_audio_ms / 1000, 1), [math]::Round($completed[0].audio_done_ms / 1000, 1), $completed[0].basis, $completed[0].audible_ms) -ForegroundColor Green }
                elseif ($inPhase -ge $SpeakWaitSec) { $stopReason = "no completed spoken turn (first_audio -> audio_done) within $SpeakWaitSec s; turns seen: $($speakingTurns.Count)" }
            }
            "await_research" {
                if ($researchDone.Count -ge 1) { $researchCall = $researchDone[$researchDone.Count - 1]; $phase = "await_technical"; $phaseStarted = $now; Write-Host ("      B recorded: research.start {0} -> {1}; spoken head: `"{2}`"" -f (Get-OptionalProperty -InputObject $researchCall -Name "call_id"), (Get-OptionalProperty -InputObject $researchCall -Name "status"), (Get-OptionalProperty -InputObject $researchCall -Name "speech_head")) -ForegroundColor Green }
                elseif ($inPhase -ge $ResearchWaitSec) { $stopReason = "no research.start reached a terminal result within $ResearchWaitSec s (calls: $($researchCalls.Count), running: $(@($researchCalls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name 'status') -eq 'running' }).Count))" }
            }
            "await_technical" {
                $after = @($technicalCalls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "created_at") -gt [string](Get-OptionalProperty -InputObject $researchCall -Name "completed_at") })
                if ($after.Count -ge 1) { $technicalCall = $after[0]; $phase = "done"; Write-Host ("      C recorded: {0} kind={1} level={2}" -f (Get-OptionalProperty -InputObject $technicalCall -Name "name"), (Get-OptionalProperty -InputObject $technicalCall -Name "query_kind"), (Get-OptionalProperty -InputObject $technicalCall -Name "level")) -ForegroundColor Green }
                elseif ($inPhase -ge $TechnicalWaitSec) { $stopReason = "no technical follow-up (activity.explain technical / rejected_pages / research_problems) within $TechnicalWaitSec s of the result" }
            }
        }
        if ($phase -eq "done" -or $stopReason) { break }
        $sig = "{0}|{1}|{2}|{3}|{4}" -f $phase, $speakingTurns.Count, $completed.Count, $researchCalls.Count, $researchDone.Count
        if ($sig -ne $lastSig -or ($now - $lastPrint).TotalSeconds -ge 30) {
            $lastTurn = if ($speakingTurns.Count) { $speakingTurns[$speakingTurns.Count - 1] } else { $null }
            $summary = Get-SessionRouterSummary -Activity $activity
            Write-Host ("      [{0,4:N0} s] {1}   session: {2}" -f $elapsed, $phase, $(if ($sessionId) { $sessionId } else { "none yet" })) -ForegroundColor DarkGray
            Write-Host ("               last spoken turn: {0}" -f $(if ($null -ne $lastTurn) { "turn {0} first_audio={1} response_done={2} audio_done={3} basis={4}" -f $lastTurn.turn, $lastTurn.first_audio_ms, $lastTurn.response_done_ms, $lastTurn.audio_done_ms, $lastTurn.basis } else { "none" })) -ForegroundColor DarkGray
            Write-Host ("               router events: {0}" -f $summary.ToolCalls) -ForegroundColor DarkGray
            $lastSig = $sig; $lastPrint = $now
        }
        Start-Sleep -Seconds $PollSec
    }
    if ($stopReason) { Write-Host "      stopped: $stopReason" -ForegroundColor Yellow }

    # ------------------------------------------------------------------ the checks
    $evidence.session = [ordered]@{ id = $sessionId }
    Add-Check -Name "voice.core_session_correlated" -Ok ($sessionId -ne "") -Detail $(if ($sessionId) { "session $sessionId" } else { $stopReason })
    foreach ($t in $speakingTurns) { $evidence.speaking += $t }
    $completed = @($speakingTurns | Where-Object { $null -ne $_.first_audio_ms -and $null -ne $_.audio_done_ms })
    $long = @($completed | Where-Object { $null -ne $_.audible_ms -and [double]$_.audible_ms -ge 6000 })
    Add-Check -Name "speaking.lifecycle_recorded" -Ok ($completed.Count -ge 1) -Detail $(if ($completed.Count) { "$($completed.Count) turn(s) with first_audio -> audio_done; bases: " + (($completed | ForEach-Object { $_.basis }) -join ",") } else { "no turn reached audio_done: $stopReason" })
    Add-Check -Name "speaking.held_through_a_long_answer" -Ok ($long.Count -ge 1) -Detail $(if ($long.Count) { "a turn audible for $([math]::Round([double]$long[0].audible_ms / 1000, 1)) s ended by $($long[0].basis)" } else { "no turn audible for 6 s or more (the answer was short or interrupted): " + (($completed | ForEach-Object { "{0} ms/{1}" -f $_.audible_ms, $_.basis }) -join ", ") })
    $providerEnded = @($completed | Where-Object { $_.basis -eq "provider" -or $_.basis -eq "silence" })
    Add-Check -Name "speaking.ended_at_last_audio" -Ok ($providerEnded.Count -ge 1) -Detail $(if ($providerEnded.Count) { "ended by the provider's output_audio_buffer.stopped or the silence release, not at response_done" } else { "no turn ended by provider/silence (bases: " + (($completed | ForEach-Object { $_.basis }) -join ",") + ")" })
    $rc = $researchCall
    $rStatus = if ($null -ne $rc) { [string](Get-OptionalProperty -InputObject $rc -Name "status") } else { "" }
    $rHead = if ($null -ne $rc) { [string](Get-OptionalProperty -InputObject $rc -Name "speech_head") } else { "" }
    $evidence.research = [ordered]@{ call_id = $(if ($null -ne $rc) { Get-OptionalProperty -InputObject $rc -Name "call_id" } else { $null }); status = $rStatus; speech_head = $rHead; error_class = $(if ($null -ne $rc) { Get-OptionalProperty -InputObject $rc -Name "error_class" } else { $null }) }
    Add-Check -Name "research.real_run_completed" -Ok ($rStatus -eq "succeeded") -Detail $(if ($rStatus) { "research.start -> $rStatus $(if ($rStatus -ne 'succeeded') { '(' + [string](Get-OptionalProperty -InputObject $rc -Name 'error_class') + ')' })" } else { "no research.start reached a terminal result" })
    Add-Check -Name "research.spoken_result_is_findings" -Ok ($rStatus -eq "succeeded" -and $rHead.Length -gt 0 -and (Test-ResultSpeechClean -Text $rHead)) -Detail $(if ($rHead) { "spoken head: `"$rHead`"" } else { "no spoken result" })
    $evidence.technical = if ($null -ne $technicalCall) { [ordered]@{ call_id = (Get-OptionalProperty -InputObject $technicalCall -Name "call_id"); query_kind = (Get-OptionalProperty -InputObject $technicalCall -Name "query_kind"); level = (Get-OptionalProperty -InputObject $technicalCall -Name "level"); speech_head = (Get-OptionalProperty -InputObject $technicalCall -Name "speech_head") } } else { $null }
    Add-Check -Name "research.diagnostics_only_on_request" -Ok ($null -ne $technicalCall) -Detail $(if ($null -ne $technicalCall) { "technical follow-up after the result: kind=$(Get-OptionalProperty -InputObject $technicalCall -Name 'query_kind') level=$(Get-OptionalProperty -InputObject $technicalCall -Name 'level')" } else { "no technical follow-up recorded: $stopReason" })
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

Write-Host "OWNER M18.2: $($evidence.verdict)" -ForegroundColor $(if ($evidence.verdict -eq "PASS") { "Green" } else { "Red" })
exit $exitCode
