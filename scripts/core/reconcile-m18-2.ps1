<#
.SYNOPSIS
    Closes M18.2 from the durable record (ADR-0064 discipline): evaluates Stage 13 of
    docs/QUALIFICATION.md from the Cloud Core's own rows - the owner's session, its research
    call, the research task and its ledger rows - and writes the evidence file.

.DESCRIPTION
    Nothing here asks the owner anything or runs anything. The session is the latest web
    session that carries a SUCCEEDED research.start call (or -SessionId). Each row names the
    evidence it rests on; a row the record cannot prove stays NOT_YET_PROVEN or PROVEN_PROXY
    and says why. The evening's failed research runs are listed as attributed findings, not
    hidden.

.EXAMPLE
    .\scripts\core\reconcile-m18-2.ps1
    .\scripts\core\reconcile-m18-2.ps1 -SessionId be6d49ce-de93-46bd-ade9-6472b4ef4c37 -OutFile docs\evidence\m18-2-reconciliation-2026-09-06.json
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    [string]$BaseUrl = "",
    [string]$SessionId = "",
    [string]$OutFile = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\VoiceShell.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
. (Join-Path $repoRoot "scripts\lib\OwnerHarness.ps1")

if (-not $BaseUrl) { $BaseUrl = "http://${BrokerHost}:$ApiPort" }
$BaseUrl = $BaseUrl.TrimEnd('/')
if (-not $OutFile) { $OutFile = Join-Path $repoRoot ("docs\evidence\m18-2-reconciliation-" + (Get-Date -Format "yyyy-MM-dd") + ".json") }

$i_dot = [char]0x0131; $u_uml = [char]0x00FC
$wordPagesTr = "sayfay" + $i_dot
$wordEvidenceTr = "kan" + $i_dot + "t"
$wordVersionTr = "s" + $u_uml + "r" + $u_uml + "m"
$diagnosticWords = @("elendi", "eledi", "interstitial", "dedup", " aday", $wordPagesTr, "sayfa ele", $wordEvidenceTr, $wordVersionTr, "policy")
# Built BEFORE the array literal: inside @( ... ) the comma binds tighter than + (the M18.2
# harness crash; harness-symbols.tests.ps1 fails any + inside a literal).
$wordRecordsLook = "kay" + $i_dot + "tlara bak"
$wordRecordsCheck = "kay" + $i_dot + "tlar" + $i_dot + " kontrol"
$recordWords = @($wordRecordsLook, $wordRecordsCheck)
$technicalKinds = @("technical", "research_problems", "rejected_pages", "research_detail")

Write-Host "PagentOS M18.2 reconciliation from the durable record"
$health = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/system/health" -TimeoutSec 20
$credential = $null
try { $credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL" } catch { $credential = $null }
if (-not $credential) {
    $secure = Read-Host -Prompt "Cloud Owner Credential (input is hidden; exchanged for one session, then dropped)" -AsSecureString
    if ($secure.Length -eq 0) { throw "empty credential; nothing done." }
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $credential = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
try {
    $body = @{ owner_credential = $credential; client_kind = "cli"; label = "reconcile-m18-2" } | ConvertTo-Json -Compress
    $issued = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body $body
}
finally { $credential = $null; $body = $null }
$token = [string]$issued.token
$mintedId = [string](Get-OptionalProperty -InputObject $issued -Name "session_id")
$headers = @{ Authorization = "Bearer $token" }
function Get-Json { param([string]$Path) return Invoke-JsonUtf8 -Uri "$BaseUrl$Path" -Headers $headers -TimeoutSec 30 }
function Get-JsonOrNull { param([string]$Path) try { return Invoke-JsonUtf8 -Uri "$BaseUrl$Path" -Headers $headers -TimeoutSec 30 } catch { return $null } }

$rows = @()
function Add-Row {
    param([string]$Row, [string]$Capability, [string]$Status, [string]$Evidence, [string]$Note = "")
    $script:rows += [ordered]@{ row = $Row; capability = $Capability; status = $Status; evidence = $Evidence; note = $Note }
    $color = switch ($Status) { "PROVEN_REAL" { "Green" } "PROVEN_PROXY" { "Yellow" } default { "Red" } }
    Write-Host ("  {0,-5} {1,-16} {2}" -f $Row, $Status, $Evidence) -ForegroundColor $color
    if ($Note) { Write-Host ("        {0}" -f $Note) -ForegroundColor DarkGray }
}

function Get-Activity { param([string]$Id) return Get-JsonOrNull "/v1/voice/realtime/sessions/$Id/activity" }

try {
    # ------------------------------------------------------------------ the session and its research call
    $sessions = Get-ArrayProperty -InputObject (Get-Json "/v1/voice/realtime/sessions?limit=50") -Name "sessions"
    $webSessions = @($sessions | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "client_kind") -eq "web" })
    $chosen = $null; $researchCall = $null; $activity = $null
    if ($SessionId) {
        $chosen = @($webSessions | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "session_id") -eq $SessionId })
        if ($chosen.Count -eq 0) { throw "session $SessionId is not a web session on the record" }
        $chosen = $chosen[0]
        $activity = Get-Activity -Id $SessionId
        $calls = Get-ArrayProperty -InputObject $activity -Name "tool_calls"
        $ok = @($calls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "name") -eq "research.start" -and [string](Get-OptionalProperty -InputObject $_ -Name "status") -eq "succeeded" })
        if ($ok.Count -gt 0) { $researchCall = $ok[$ok.Count - 1] }
    }
    else {
        $ordered = @($webSessions | Sort-Object -Property { [string](Get-OptionalProperty -InputObject $_ -Name "started_at") } -Descending)
        foreach ($s in $ordered) {
            $id = [string](Get-OptionalProperty -InputObject $s -Name "session_id")
            $act = Get-Activity -Id $id
            $calls = Get-ArrayProperty -InputObject $act -Name "tool_calls"
            $ok = @($calls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "name") -eq "research.start" -and [string](Get-OptionalProperty -InputObject $_ -Name "status") -eq "succeeded" })
            if ($ok.Count -gt 0) { $chosen = $s; $activity = $act; $researchCall = $ok[$ok.Count - 1]; break }
        }
    }
    if ($null -eq $chosen -or $null -eq $researchCall) { throw "no web session with a succeeded research.start call on the record" }
    $sessionId = [string](Get-OptionalProperty -InputObject $chosen -Name "session_id")
    $callCreated = ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $researchCall -Name "created_at"))
    $callCompleted = ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $researchCall -Name "completed_at"))
    $head = [string](Get-OptionalProperty -InputObject $researchCall -Name "speech_head")
    $chars = Get-OptionalProperty -InputObject $researchCall -Name "speech_chars"
    Write-Host ("      session {0}, research.start {1} succeeded {2} -> {3}, {4} spoken chars" -f $sessionId, (Get-OptionalProperty -InputObject $researchCall -Name "call_id"), (Get-OptionalProperty -InputObject $researchCall -Name "created_at"), (Get-OptionalProperty -InputObject $researchCall -Name "completed_at"), $chars)

    # ------------------------------------------------------------------ the research task and its ledger rows
    $tasks = Get-ArrayProperty -InputObject (Get-Json "/v1/research") -Name "tasks"
    $task = $null
    foreach ($t in $tasks) {
        $created = ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $t -Name "created_at"))
        if ($null -ne $created -and $null -ne $callCreated -and [math]::Abs(($created - $callCreated).TotalSeconds) -le 5) { $task = $t }
    }
    $taskId = if ($null -ne $task) { [string](Get-OptionalProperty -InputObject $task -Name "task_id") } else { "" }
    $detail = if ($taskId) { Get-JsonOrNull "/v1/research/$taskId" } else { $null }
    $plan = if ($null -ne $detail) { Get-OptionalProperty -InputObject $detail -Name "plan" } else { $null }
    $policy = if ($null -ne $plan) { Get-OptionalProperty -InputObject $plan -Name "policy" } else { $null }
    $progress = if ($null -ne $detail) { Get-OptionalProperty -InputObject $detail -Name "progress" } else { $null }
    $report = if ($null -ne $detail) { Get-OptionalProperty -InputObject $detail -Name "report" } else { $null }
    $findings = Get-ArrayProperty -InputObject $report -Name "findings"
    $sinceIso = if ($null -ne $callCreated) { $callCreated.AddMinutes(-15).ToString("o") } else { [DateTimeOffset]::UtcNow.AddHours(-24).ToString("o") }
    $ledger = Get-ArrayProperty -InputObject (Get-JsonOrNull ("/v1/ledger/events?since=" + [uri]::EscapeDataString($sinceIso) + "&limit=200")) -Name "events"
    $completedRows = @($ledger | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "event_type") -eq "research.completed" })
    $completedRow = $null
    foreach ($r in $completedRows) {
        $at = ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $r -Name "occurred_at"))
        if ($null -ne $at -and $null -ne $callCompleted -and [math]::Abs(($at - $callCompleted).TotalSeconds) -le 90) { $completedRow = $r }
    }
    $completedDetail = if ($null -ne $completedRow) { Get-OptionalProperty -InputObject $completedRow -Name "detail_json" } else { $null }
    $failedRows = @($ledger | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "event_type") -eq "research.failed" })

    # ------------------------------------------------------------------ the rows
    $events = Get-ArrayProperty -InputObject $activity -Name "client_events"
    $turns = Get-SpeechTurns -Events $events
    $completed = @($turns | Where-Object { $null -ne $_.first_audio_ms -and $null -ne $_.audio_done_ms })
    $long = @($completed | Where-Object { [double]$_.audible_ms -ge 6000 })
    $afterGeneration = @($completed | Where-Object { $_.ended_after_generation })
    $longAfter = @($long | Where-Object { $_.ended_after_generation })
    Add-Row -Row "13.1" -Capability "SPEAKING is a lifecycle: first audible playback to the actual end of playback, held through pauses, ended after response.done" -Status $(if ($longAfter.Count -ge 1) { "PROVEN_REAL" } else { "NOT_YET_PROVEN" }) -Evidence ("session {0}: {1} completed turn(s); {2} audible >= 6 s; {3} ended after response_done (e.g. {4})" -f $sessionId, $completed.Count, $long.Count, $afterGeneration.Count, $(if ($longAfter.Count) { "first_audio {0} -> response_done {1} -> audio_done {2} ms, audible {3} ms" -f $longAfter[0].first_audio_ms, $longAfter[0].response_done_ms, $longAfter[0].audio_done_ms, $longAfter[0].audible_ms } else { "none" }))
    $headClean = ($head.Length -gt 0) -and -not (Test-TextContainsAny -Text $head -Words $diagnosticWords)
    Add-Row -Row "13.2" -Capability "the spoken research answer is the findings, never crawler statistics" -Status $(if ($headClean -and $findings.Count -ge 1) { "PROVEN_REAL" } else { "NOT_YET_PROVEN" }) -Evidence ("research.start {0}: spoken head `"{1}`" ({2} chars, no crawler words: {3}); report findings: {4}" -f (Get-OptionalProperty -InputObject $researchCall -Name "call_id"), $head, $chars, $headClean, $findings.Count)
    $technical = $null
    foreach ($s in $webSessions) {
        $id = [string](Get-OptionalProperty -InputObject $s -Name "session_id")
        $act = if ($id -eq $sessionId) { $activity } else { Get-Activity -Id $id }
        foreach ($c in (Get-ArrayProperty -InputObject $act -Name "tool_calls")) {
            if ([string](Get-OptionalProperty -InputObject $c -Name "name") -ne "activity.explain") { continue }
            if ([string](Get-OptionalProperty -InputObject $c -Name "status") -ne "succeeded") { continue }
            if (-not ([string](Get-OptionalProperty -InputObject $c -Name "level") -in @("technical", "full") -or [string](Get-OptionalProperty -InputObject $c -Name "query_kind") -in $technicalKinds)) { continue }
            $at = ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $c -Name "created_at"))
            if ($null -eq $at -or $null -eq $callCompleted -or $at -le $callCompleted) { continue }
            if ($null -eq $technical) { $technical = [pscustomobject]@{ Session = $id; Call = $c } }
        }
    }
    Add-Row -Row "13.3" -Capability "diagnostics are spoken only on request (Teknik anlat. / Hangi sayfalar elendi? / Arastirma sirasinda ne sorun oldu?)" -Status $(if ($null -ne $technical) { "PROVEN_REAL" } else { "NOT_YET_PROVEN" }) -Evidence $(if ($null -ne $technical) { "activity.explain {0} on session {1} after the result: level={2} kind={3}, head `"{4}`"" -f (Get-OptionalProperty -InputObject $technical.Call -Name "call_id"), $technical.Session, (Get-OptionalProperty -InputObject $technical.Call -Name "level"), (Get-OptionalProperty -InputObject $technical.Call -Name "query_kind"), (Get-OptionalProperty -InputObject $technical.Call -Name "speech_head") } else { "no technical explanation after the result on any web session (the owner's page reloaded 30 s after it)" }) -Note $(if ($null -eq $technical) { "its own short check: scripts/core/owner-m18-2-followup.ps1 (no research is re-run)" } else { "" })
    Add-Row -Row "13.4" -Capability "the Core's lifecycle during research is truthful (RESEARCHING, then SPEAKING for the presentation)" -Status "PROVEN_PROXY" -Evidence "the bus history is not durable; the API publishes per stage (test_research_uistate.py) and the session's own first_audio/audio_done bracket the presentation" -Note "owner-observed on /core during the run; no durable row can say more"
    $recordy = Test-TextContainsAny -Text $head -Words $recordWords
    Add-Row -Row "13.5" -Capability "result-first: the answer, why it matters, details on request; no bookkeeping narration" -Status $(if ($headClean -and -not $recordy) { "PROVEN_REAL" } else { "NOT_YET_PROVEN" }) -Evidence ("the spoken head opens with the conclusion and names no records or pages: `"{0}`"" -f $head)
    $taskStatus = if ($null -ne $task) { [string](Get-OptionalProperty -InputObject $task -Name "status") } else { "" }
    Add-Row -Row "13.6" -Capability "a spoken Arastir is a real run: the same pipeline as the REST route, completed by the announcer with spoken_result" -Status $(if ($taskStatus -eq "READY") { "PROVEN_REAL" } else { "NOT_YET_PROVEN" }) -Evidence ("task {0} {1} ready {2} artifact {3}; the call completed {4}" -f $taskId, $taskStatus, (Get-OptionalProperty -InputObject $task -Name "ready_at"), (Get-OptionalProperty -InputObject $task -Name "artifact_id"), (Get-OptionalProperty -InputObject $researchCall -Name "completed_at"))
    $mode = if ($null -ne $policy) { [string](Get-OptionalProperty -InputObject $policy -Name "mode") } else { "" }
    $hardBudget = if ($null -ne $policy) { [double](Get-OptionalProperty -InputObject $policy -Name "hard_budget_s") } else { 0 }
    $elapsed = if ($null -ne $completedDetail) { [double](Get-OptionalProperty -InputObject $completedDetail -Name "elapsed_s") } else { -1 }
    $callSeconds = if ($null -ne $callCreated -and $null -ne $callCompleted) { [math]::Round(($callCompleted - $callCreated).TotalSeconds, 0) } else { -1 }
    $elapsedText = $elapsed.ToString("0.0", [cultureinfo]::InvariantCulture)
    $budgetText = $hardBudget.ToString("0", [cultureinfo]::InvariantCulture)
    Add-Row -Row "13.7" -Capability "QUICK is the default and respects its budget (target 90 s, hard 120 s)" -Status $(if ($mode -eq "quick" -and $elapsed -ge 0 -and $elapsed -le $hardBudget) { "PROVEN_REAL" } else { "NOT_YET_PROVEN" }) -Evidence ("plan mode {0}; research.completed elapsed {1} s of a {2} s hard budget; the voice call took {3} s end to end" -f $mode, $elapsedText, $budgetText, $callSeconds)
    # Assigned, not wrapped: @(ConvertTo-Array ...) makes the whole list ONE element (trap 4).
    $cooled = ConvertTo-Array -Value (Get-OptionalProperty -InputObject $progress -Name "cooled_domains")
    $challenged = if ($null -ne $progress) { Get-OptionalProperty -InputObject $progress -Name "challenged_pages" } else { $null }
    $skippedCooled = @((Get-ArrayProperty -InputObject $detail -Name "events") | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "detail") -like "*skipped: domain cooled*" })
    Add-Row -Row "13.8" -Capability "challenges are left alone: detected, recorded, abandoned without retry; the domain cooled; the next source taken" -Status $(if ($cooled.Count -ge 1 -and $skippedCooled.Count -ge 1) { "PROVEN_REAL" } else { "PROVEN_PROXY" }) -Evidence ("challenged pages {0}; cooled domains {1}; {2} 'skipped: domain cooled' event(s); the run still completed" -f $challenged, ($cooled -join ","), $skippedCooled.Count) -Note $(if ($cooled.Count -eq 0) { "this run met no challenge; the policy is proven by test_research_challenge.py" } else { "" })
    $waves = if ($null -ne $completedDetail) { Get-OptionalProperty -InputObject $completedDetail -Name "waves" } else { $null }
    $fetched = if ($null -ne $completedDetail) { Get-OptionalProperty -InputObject $completedDetail -Name "fetched" } else { $null }
    $discovered = if ($null -ne $completedDetail) { Get-OptionalProperty -InputObject $completedDetail -Name "discovered" } else { $null }
    Add-Row -Row "13.9" -Capability "two-stage pipeline: ranked before navigation, fetched in waves, early stop" -Status $(if ($null -ne $waves -and $null -ne $fetched -and $null -ne $discovered -and [int]$fetched -lt [int]$discovered) { "PROVEN_REAL" } else { "NOT_YET_PROVEN" }) -Evidence ("research.completed: waves {0}, fetched {1} of {2} discovered, evidence {3}, findings {4}, dedup {5}" -f $waves, $fetched, $discovered, (Get-OptionalProperty -InputObject $completedDetail -Name "evidence"), (Get-OptionalProperty -InputObject $completedDetail -Name "findings"), (Get-OptionalProperty -InputObject $completedDetail -Name "deduplicated"))
    Add-Row -Row "13.10" -Capability "coarse Turkish progress on the Core, never crawler counters" -Status "PROVEN_PROXY" -Evidence "the bus history is not durable; the labels are pinned in test_research_uistate.py and the readout prints a label verbatim" -Note "owner-observed on /core during the run"

    # ------------------------------------------------------------------ attributed findings
    $failures = @()
    foreach ($r in $failedRows) {
        $d = Get-OptionalProperty -InputObject $r -Name "detail_json"
        $failures += [ordered]@{ at = (Get-OptionalProperty -InputObject $r -Name "occurred_at"); error_class = $(if ($null -ne $d) { Get-OptionalProperty -InputObject $d -Name "error_class" } else { $null }); error = $(if ($null -ne $d) { Get-OptionalProperty -InputObject $d -Name "error" } else { $null }) }
    }
    if ($failures.Count) { Write-Host ("      {0} research run(s) failed in the window (attributed, not hidden): {1}" -f $failures.Count, (($failures | ForEach-Object { "{0} {1}" -f $_.at, $_.error_class }) -join "; ")) -ForegroundColor Yellow }

    $doc = [ordered]@{
        generated_at = (Get-Date).ToUniversalTime().ToString("o")
        cloud = $BaseUrl
        contract_version = (Get-OptionalProperty -InputObject (Get-OptionalProperty -InputObject (Get-OptionalProperty -InputObject $health -Name "checks") -Name "voice_realtime") -Name "action_contract_version")
        session = [ordered]@{ id = $sessionId; started_at = (Get-OptionalProperty -InputObject $chosen -Name "started_at"); state = (Get-OptionalProperty -InputObject $chosen -Name "state") }
        research_call = [ordered]@{ call_id = (Get-OptionalProperty -InputObject $researchCall -Name "call_id"); created_at = (Get-OptionalProperty -InputObject $researchCall -Name "created_at"); completed_at = (Get-OptionalProperty -InputObject $researchCall -Name "completed_at"); speech_head = $head; speech_chars = $chars }
        task = [ordered]@{ task_id = $taskId; status = $taskStatus; ready_at = (Get-OptionalProperty -InputObject $task -Name "ready_at"); artifact_id = (Get-OptionalProperty -InputObject $task -Name "artifact_id"); mode = $mode; progress = $progress; completed = $completedDetail }
        speaking_turns = @($turns | ForEach-Object { [ordered]@{ first_audio_ms = $_.first_audio_ms; response_done_ms = $_.response_done_ms; audio_done_ms = $_.audio_done_ms; audible_ms = $_.audible_ms; ended_after_generation = $_.ended_after_generation; basis = $_.basis } })
        rows = $rows
        failed_runs = $failures
    }
    $real = @($rows | Where-Object { $_.status -eq "PROVEN_REAL" })
    $open = @($rows | Where-Object { $_.status -eq "NOT_YET_PROVEN" })
    Write-Host ("      rows: {0} PROVEN_REAL, {1} PROVEN_PROXY, {2} open ({3})" -f $real.Count, (@($rows | Where-Object { $_.status -eq "PROVEN_PROXY" })).Count, $open.Count, (($open | ForEach-Object { $_.row }) -join ", "))
    $dir = Split-Path -Parent $OutFile
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    [System.IO.File]::WriteAllText($OutFile, ($doc | ConvertTo-Json -Depth 12), (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "      evidence written: $OutFile"
}
finally {
    try { Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions/$mintedId/revoke" -Headers $headers -Body "{}" | Out-Null } catch { }
    $headers = $null; $token = $null
}
exit 0
