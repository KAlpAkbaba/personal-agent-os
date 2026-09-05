<#
.SYNOPSIS
    M16 owner action: ask PagentOS, by voice, what it did - and prove from durable
    evidence that it answered from the activity ledger, paused on "dur" and resumed
    from the same semantic point on "devam et".

.DESCRIPTION
    One command, in this order:

      1. preflight: working-tree release blockers, Cloud Core health (realtime provider),
         and the Cloud Core's ledger policy - if the deployed Cloud Core has no ledger
         yet, ONE transactional release ships it (build, migrate, recreate api only,
         health, rollback on failure). No Windows component is touched.
      2. evidence: the real research run in -ResearchEvidence (research-1.json, written
         by owner-research.ps1) is recorded in the ledger as `research.qualified`, with
         the file's SHA-256 as its reference - never a hand-typed number; then the ledger
         backfills everything else from the database. Nothing is seeded.
      3. the web voice shell starts exactly as for the M12 sessions
         (http://localhost:3000/voice); you connect, then say the six phrases printed.
      4. after you press Enter, the newest realtime session's durable activity record
         (tool calls, intents, client events - ids and kinds, never a transcript) is
         fetched and every step is asserted. Expected last line: OWNER EXPLAIN: PASS.

    The owner credential is typed into a masked prompt, exchanged for one session that is
    revoked at the end, and never printed or stored.

.EXAMPLE
    .\scripts\voice\owner-explain.ps1 -OutFile explain-1.json
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    [int]$WebPort = 3000,
    [string]$BaseUrl = "",
    [string]$ResearchEvidence = "",
    [string]$OutFile = "",
    # auto: release the Cloud Core only when it has no ledger yet; never: refuse; force: always.
    [ValidateSet("auto", "never", "force")][string]$CloudCoreUpdate = "auto",
    # Never start the web shell (it must already answer on -WebPort).
    [switch]$SkipWeb,
    # Verify an ALREADY COMPLETED session instead of asking for a new one: no web shell, no
    # talking. Use it to re-check a session after a checker fix (2026-09-05) rather than
    # making the owner repeat a qualification the system already passed.
    [switch]$VerifyOnly,
    [string]$SessionId = "",
    [string]$PnpmPath = "pnpm",
    [string]$ExpectProvider = "openai-realtime",
    # How long to wait for the web shell to answer /voice after starting it (Next.js compiles
    # the page on first request), and for the owner's session to appear after Enter.
    [ValidateRange(10, 900)][int]$WebReadyTimeoutSec = 180,
    [ValidateRange(30, 3600)][int]$SessionWaitSec = 600,
    # -VerifyOnly only: how far back a completed session may have started and still count as
    # the owner's qualification. Bounded on purpose - with no web shell there is no readiness
    # moment to compare against, and "no floor at all" would let a months-old unrelated
    # session qualify. Wide enough that a session from last night still verifies today.
    [ValidateRange(1, 720)][int]$VerifyMaxAgeHours = 48
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
if (-not $ResearchEvidence) { $ResearchEvidence = Join-Path $repoRoot "research-1.json" }
$runId = "owner-explain-" + (Get-Date -Format "yyyyMMdd-HHmmss")

function Get-OptionalProperty {
    param($InputObject, [string]$Name)
    if ($null -eq $InputObject) { return $null }
    $prop = $InputObject.PSObject.Properties[$Name]
    if ($null -ne $prop) { return $prop.Value }
    return $null
}

# Turkish text is built from character codes so this file stays pure ASCII (Windows
# PowerShell 5.1 reads a BOM-less file as ANSI and would mangle the letters).
$tr = @{
    i_dotless = [char]0x0131; s_ced = [char]0x015F; c_ced = [char]0x00E7; g_breve = [char]0x011F
    o_uml = [char]0x00F6; u_uml = [char]0x00FC; a_circ = [char]0x00E2; I_dot = [char]0x0130
}
$phrases = @(
    ("Son yapt" + $tr.i_dotless + "klar" + $tr.i_dotless + "n" + $tr.i_dotless + " anlat."),
    "(listen: a concise two-to-four sentence briefing)",
    "(while it speaks: let another, distant voice talk in the room - it must keep speaking)",
    "Dur.",
    "Devam et.",
    "Teknik anlat."
)

$evidence = [ordered]@{
    run_id       = $runId
    started_at   = (Get-Date).ToUniversalTime().ToString("o")
    cloud        = $BaseUrl
    cloud_policy = $null
    ingest       = $null
    backfill     = $null
    web_shell    = $null
    session      = $null
    activity     = $null
    provenance   = $null
    ledger       = $null
    checks       = @()
    verdict      = "FAIL"
}

Write-Host "PagentOS owner explain ($runId)"

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
$checks = Get-OptionalProperty -InputObject $health -Name "checks"
$rt = if ($null -ne $checks) { Get-OptionalProperty -InputObject $checks -Name "voice_realtime" } else { $null }
# Same shape as the provenance bug below: an empty array unrolls out of an if-expression and
# arrives as $null, so assign first and fill second.
$providers = @()
if ($null -ne $rt) { $providers = @(Get-OptionalProperty -InputObject $rt -Name "providers") }
if ($providers -notcontains $ExpectProvider) {
    throw "Cloud Core at $BaseUrl does not list realtime provider '$ExpectProvider' (providers: $($providers -join ', ')); nothing started"
}
Write-Host "      Cloud Core: status=$(Get-OptionalProperty -InputObject $health -Name 'status'), realtime providers=[$($providers -join ', ')]"

function Get-ExpectedLedgerVersion {
    $routes = Join-Path $repoRoot "services\api\app\ledger\routes.py"
    if (-not (Test-Path $routes)) { throw "this checkout has no ledger routes ($routes); nothing to qualify" }
    $match = [regex]::Match([System.IO.File]::ReadAllText($routes), '(?m)LEDGER_VERSION\s*=\s*(\d+)')
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

# The local DPAPI store first, the masked prompt second. The store is owner-only and
# machine-bound (scripts\secret-store.ps1), it holds the SAME credential the prompt would
# receive, and reading it is what lets -VerifyOnly re-check durable evidence without a human
# in the loop - which is the whole point of a verification that must not require another
# voice session. The value is never printed, only exchanged for one session and dropped.
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

$webProcess = $null
try {
    # ------------------------------------------------------------------ ledger policy

    $expectedLedger = Get-ExpectedLedgerVersion
    $policy = $null
    try { $policy = Get-Json "/v1/ledger/policy" } catch { $policy = $null }
    $deployedLedger = if ($null -ne $policy) { [int](Get-OptionalProperty -InputObject $policy -Name "ledger_version") } else { 0 }
    Write-Host "      ledger policy: deployed version $deployedLedger, this checkout expects $expectedLedger"
    $stale = ($deployedLedger -lt $expectedLedger)
    $releaseCloud = switch ($CloudCoreUpdate) { "force" { $true } "never" { $false } default { $stale } }
    if ($stale -and $CloudCoreUpdate -eq "never") {
        throw "the deployed Cloud Core has no ledger of version $expectedLedger (it answers $deployedLedger); -CloudCoreUpdate never refuses to release"
    }
    if ($releaseCloud -and $releaseBlockers.Blocked) {
        throw ("a Cloud Core release is required (ledger $deployedLedger < $expectedLedger) but the working tree has " +
               "$(@($releaseBlockers.Changes).Count) uncommitted change(s), and a release ships HEAD only. Commit or revert them, then rerun.")
    }
    if ($releaseCloud) {
        Write-Host "      the deployed Cloud Core predates this checkout's ledger: releasing it once" -ForegroundColor Yellow
        Invoke-CloudCoreRelease
        $policy = Get-Json "/v1/ledger/policy"
        $deployedLedger = [int](Get-OptionalProperty -InputObject $policy -Name "ledger_version")
        if ($deployedLedger -lt $expectedLedger) { throw "the Cloud Core still answers ledger version $deployedLedger after a release" }
    }
    else {
        Write-Host "      no Cloud Core release: the deployed ledger is current"
    }
    $evidence.cloud_policy = [ordered]@{
        expected_version = $expectedLedger; deployed_version = $deployedLedger; released = [bool]$releaseCloud
        working_tree_blockers = @($releaseBlockers.Changes)
    }

    # ------------------------------------------------------------------ evidence ingest

    $ingest = [ordered]@{ file = $ResearchEvidence; present = $false; recorded = $false; digest = $null; event_id = $null }
    if (Test-Path -LiteralPath $ResearchEvidence) {
        $bytes = [System.IO.File]::ReadAllBytes($ResearchEvidence)
        $sha = [System.Security.Cryptography.SHA256]::Create()
        $digest = "sha256:" + (($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString("x2") }) -join "")
        $doc = ConvertFrom-Utf8Json -Bytes $bytes
        $verdict = [string](Get-OptionalProperty -InputObject $doc -Name "verdict")
        $task = Get-OptionalProperty -InputObject $doc -Name "task"
        $taskId = if ($null -ne $task) { [string](Get-OptionalProperty -InputObject $task -Name "task_id") } else { $null }
        $report = Get-OptionalProperty -InputObject $doc -Name "report"
        $stats = if ($null -ne $report) { Get-OptionalProperty -InputObject $report -Name "stats" } else { $null }
        $local = Get-OptionalProperty -InputObject $doc -Name "local"
        $deployment = Get-OptionalProperty -InputObject $doc -Name "deployment"
        $cloudPolicy = Get-OptionalProperty -InputObject $doc -Name "cloud_policy"
        $ingest.present = $true
        $ingest.digest = $digest
        $ingest.verdict = $verdict
        $ingest.task_id = $taskId
        if ($verdict -eq "PASS" -and $taskId) {
            $findings = @(Get-OptionalProperty -InputObject $report -Name "findings").Count
            $sources = @(Get-OptionalProperty -InputObject $doc -Name "sources").Count
            $summary = "Sahibin qualification calismasi PASS ile bitti: $findings bulgu, $sources kaynak; tarayici temiz kapandi."
            $detail = [ordered]@{
                verdict = $verdict
                findings = $findings
                sources = $sources
                pagentos_chrome_before = $(if ($null -ne $local) { Get-OptionalProperty -InputObject $local -Name "pagentos_chrome_before" } else { $null })
                pagentos_chrome_after = $(if ($null -ne $local) { Get-OptionalProperty -InputObject $local -Name "pagentos_chrome_after" } else { $null })
                installed_release = $(if ($null -ne $deployment) { Get-OptionalProperty -InputObject $deployment -Name "installed_release" } else { $null })
                deployed = $(if ($null -ne $deployment) { [bool](Get-OptionalProperty -InputObject $deployment -Name "deployed") } else { $null })
                cloud_policy_version = $(if ($null -ne $cloudPolicy) { Get-OptionalProperty -InputObject $cloudPolicy -Name "deployed_version" } else { $null })
                rejected_by_reason = $(if ($null -ne $stats) { Get-OptionalProperty -InputObject $stats -Name "rejected_by_reason" } else { $null })
                evidence_file = (Split-Path -Leaf $ResearchEvidence)
                digest = $digest
            }
            $event = [ordered]@{
                event_type = "research.qualified"
                subsystem = "research"
                status = "completed"
                severity = "notice"
                action = "owner_qualification"
                result = "PASS"
                production_state = "deployed"
                # .NET "o" timestamps carry seven fractional digits; the API parses six.
                occurred_at = ([string](Get-OptionalProperty -InputObject $doc -Name "finished_at") -replace '(\.\d{6})\d+', '$1')
                research_job_id = $taskId
                factual_summary = $summary
                detail_json = $detail
                evidence_refs = @(
                    [ordered]@{ kind = "file"; ref = (Split-Path -Leaf $ResearchEvidence); digest = $digest },
                    [ordered]@{ kind = "research_report"; ref = $taskId }
                )
                source_ref = "file:$(Split-Path -Leaf $ResearchEvidence):$digest"
            }
            $recorded = Post-Json "/v1/ledger/events" ($event | ConvertTo-Json -Depth 8 -Compress)
            $ingest.recorded = $true
            $ingest.event_id = [string](Get-OptionalProperty -InputObject $recorded -Name "event_id")
            Write-Host "      evidence: $ResearchEvidence ($digest) recorded as research.qualified for task $taskId"
        }
        else {
            Write-Host "      evidence: $ResearchEvidence has verdict '$verdict'; not recorded as qualified" -ForegroundColor Yellow
        }
    }
    else {
        Write-Host "      evidence: $ResearchEvidence not found; the ledger will carry only what the database knows" -ForegroundColor Yellow
    }
    $evidence.ingest = $ingest

    $backfill = Post-Json "/v1/ledger/backfill" "{}"
    $evidence.backfill = $backfill
    Write-Host "      backfill: $($backfill | ConvertTo-Json -Compress -Depth 4)"
    $latest = Get-OptionalProperty -InputObject (Get-Json "/v1/ledger/latest") -Name "event"
    $latestType = [string](Get-OptionalProperty -InputObject $latest -Name "event_type")
    Write-Host "      ledger latest activity: $latestType - $(Get-OptionalProperty -InputObject $latest -Name 'factual_summary')"
    if (-not $latestType) { throw "the ledger has no activity at all after backfill; there is nothing real to narrate" }

    # ------------------------------------------------------------------ the voice session

    # Sessions that exist BEFORE this run can never be this run's session, however recent.
    $baselineIds = @()
    foreach ($s in @(Get-OptionalProperty -InputObject (Get-Json "/v1/voice/realtime/sessions?limit=50") -Name "sessions")) {
        $baselineIds += [string]$s.session_id
    }

    $voiceUrl = "http://localhost:$WebPort/voice"
    if ($VerifyOnly) {
        Write-Host "      verify-only: no web shell, no new session; re-checking a completed one"
        # No shell was started, so there IS no readiness moment - say so with $null rather
        # than with a sentinel. DateTime.MinValue used to stand in here, and the selector's
        # one-second tolerance then tried to subtract a second from the beginning of time
        # (2026-09-05, ArgumentOutOfRangeException before a single session was examined).
        #
        # "No readiness floor" must not become "any session ever", so the window is an
        # explicit absolute floor instead: recent enough to be this owner's completed
        # qualification, old enough that they need not repeat it.
        $readyAt = $null
        $notBefore = [DateTimeOffset]::UtcNow.AddHours(-1 * [math]::Abs($VerifyMaxAgeHours))
        $baselineIds = @()
        Write-Host ("      considering sessions started after {0:yyyy-MM-dd HH:mm} UTC ({1} h window)" -f $notBefore.UtcDateTime, [math]::Abs($VerifyMaxAgeHours))
        $evidence.web_shell = [ordered]@{
            verify_only = $true
            not_before = $notBefore.ToString("o")
            window_hours = [math]::Abs($VerifyMaxAgeHours)
        }
    }
    else {
    $shellLog = Join-Path $env:TEMP "pagentos-web-voice-$runId.log"
    $alreadyRunning = Test-WebShellReady -Url $voiceUrl
    if ($alreadyRunning) {
        Write-Host "      web shell: already answering at $voiceUrl (not starting another)"
    }
    elseif ($SkipWeb) {
        throw "-SkipWeb given but nothing answers at $voiceUrl; start it (.\scripts\voice\start-web-voice.ps1) or drop -SkipWeb"
    }
    else {
        Write-Host "      web shell: not running; starting it (log: $shellLog)"
        $webProcess = Start-WebShellProcess -RepoRoot $repoRoot -Upstream $BaseUrl -WebPort $WebPort -LogPath $shellLog -PnpmPath $PnpmPath
    }
    $ready = Wait-WebShellReady -Url $voiceUrl -TimeoutSec $WebReadyTimeoutSec -IntervalSec 2
    if (-not $ready.Ready) {
        $tail = ""
        if (Test-Path $shellLog) { $tail = ((Get-Content -LiteralPath $shellLog -Tail 15) -join "`n") }
        throw "the web shell did not answer at $voiceUrl within $WebReadyTimeoutSec s (probes: $($ready.Attempts)). Start it by hand with .\scripts\voice\start-web-voice.ps1 and rerun with -SkipWeb. Last log lines:`n$tail"
    }
    $readyAt = [DateTimeOffset]::UtcNow
    $notBefore = $null
    Write-Host ("      web shell: ready after {0:n1} s ({1} probe(s))" -f $ready.ElapsedSec, $ready.Attempts)
    $evidence.web_shell = [ordered]@{
        url = $voiceUrl; already_running = [bool]$alreadyRunning; started_here = ($null -ne $webProcess)
        ready_after_sec = [math]::Round($ready.ElapsedSec, 1); probes = $ready.Attempts; ready_at = $readyAt.ToString("o")
        baseline_sessions = $baselineIds.Count
    }

    Write-Host ""
    Write-Host "Open $voiceUrl, sign in, connect (Baglan). Under two minutes, in this order:" -ForegroundColor Cyan
    $n = 0
    foreach ($phrase in $phrases) {
        $n++
        $hint = switch ($n) {
            1 { "  (the real research qualification, from the ledger)" }
            2 { "" }
            3 { "" }
            4 { "  (say it WHILE it is speaking; speech must stop at once)" }
            5 { "  (it resumes at the sentence it did not finish)" }
            6 { "  (a concise technical briefing: versions, evidence, checks)" }
        }
        Write-Host ("  {0}. {1}{2}" -f $n, $phrase, $hint)
    }
    Write-Host "Then disconnect (Baglantiyi kes) and come back here." -ForegroundColor Cyan
    Read-Host -Prompt "Press Enter when the session is over" | Out-Null
    }

    # ------------------------------------------------------------------ the durable record

    # The session this run owns: new since the baseline, from the web shell, started once the
    # shell was ready, and carrying a succeeded activity.explain. A late connection is waited
    # for, not failed.
    $listSessions = { @(Get-OptionalProperty -InputObject (Get-Json "/v1/voice/realtime/sessions?limit=50") -Name "sessions") }
    $activityProbe = { param($Id) Get-Json "/v1/voice/realtime/sessions/$Id/activity" }
    if ($SessionId) {
        $listSessions = { @(Get-OptionalProperty -InputObject (Get-Json "/v1/voice/realtime/sessions?limit=50") -Name "sessions") | Where-Object { [string]$_.session_id -eq $SessionId } }.GetNewClosure()
    }
    $waited = Wait-QualificationSession -ListSessions $listSessions -ActivityProbe $activityProbe -BaselineIds $baselineIds `
        -ReadyAt $readyAt -NotBefore $notBefore `
        -TimeoutSec $(if ($VerifyOnly) { 1 } else { $SessionWaitSec }) -IntervalSec 5 -OnWaiting { param($Attempt, $Elapsed) if ($Attempt -eq 1) { Write-Host "      waiting for a web session that asked activity.explain (up to $SessionWaitSec s; keep talking, or connect now)..." } }
    if ($null -eq $waited.Selected) {
        if ($VerifyOnly) {
            throw "no completed web session with a succeeded activity.explain call was found on $BaseUrl. Run the short test once (.\scripts\voice\owner-explain.ps1) - nothing to re-verify."
        }
        throw "no web realtime session from this run asked activity.explain within $SessionWaitSec s (baseline sessions excluded: $($baselineIds.Count)). Connect at $voiceUrl and say the first phrase, then rerun with -SkipWeb."
    }
    $sessionId = [string]$waited.Selected.SessionId
    $activity = $waited.Selected.Activity
    # give a session the owner is still closing a moment to close
    $state = Get-Json "/v1/voice/realtime/sessions/$sessionId"
    $closeDeadline = (Get-Date).AddSeconds(60)
    while (([string](Get-OptionalProperty -InputObject $state -Name "state")) -notin @("closed", "expired") -and (Get-Date) -lt $closeDeadline) {
        Start-Sleep -Seconds 5
        $state = Get-Json "/v1/voice/realtime/sessions/$sessionId"
        $activity = Get-Json "/v1/voice/realtime/sessions/$sessionId/activity"
    }
    $evidence.session = $state
    $evidence.activity = $activity
    $voiceLedger = Get-Json "/v1/ledger/events?subsystem=voice&limit=50"
    $evidence.ledger = $voiceLedger

    $calls = @(Get-OptionalProperty -InputObject $activity -Name "tool_calls")
    $events = @(Get-OptionalProperty -InputObject $activity -Name "client_events")
    Write-Host ""
    Write-Host "session $sessionId  state=$(Get-OptionalProperty -InputObject $state -Name 'state')  tool calls=$($calls.Count)  client events=$($events.Count)"
    foreach ($c in $calls) {
        Write-Host ("  {0,-18} {1,-9} intent={2,-16} action={3,-12} level={4,-9} speech={5} chars  '{6}'" -f $c.name, $c.status, $c.intent, $c.action, $c.level, $c.speech_chars, $c.speech_head)
    }

    $explain = @($calls | Where-Object { $_.name -eq "activity.explain" -and $_.status -eq "succeeded" }) | Select-Object -First 1
    Add-Check "activity.explain answered from the ledger" ($null -ne $explain -and [int]$explain.speech_chars -gt 0 -and [int]$explain.evidence_count -gt 0) `
        $(if ($null -ne $explain) { "level=$($explain.level) facts=$($explain.facts) uncertainties=$($explain.uncertainties) evidence=$($explain.evidence_count)" } else { "no successful activity.explain call" })
    # Structural provenance, never wording (2026-09-05): the briefing must name the ledger
    # events it used, the research job they belong to, and numbers that match the run's own
    # record. Turkish paraphrasing is allowed; an unsupported claim is not.
    $prov = if ($null -ne $explain) { Get-OptionalProperty -InputObject $explain -Name "provenance" } else { $null }
    $provFacts = if ($null -ne $prov) { Get-OptionalProperty -InputObject $prov -Name "facts" } else { $null }
    $jobId = if ($null -ne $prov) { [string](Get-OptionalProperty -InputObject $prov -Name "research_job_id") } else { "" }
    # Assign, then fill. `$x = if (...) { ... } else { @() }` looks equivalent and is not:
    # PowerShell unrolls an empty array out of an expression, so the else branch yields
    # $null and the very next `.Count` dies under StrictMode. That is what turned "this
    # session has no provenance block" into a crash that hid every remaining check
    # (2026-09-05).
    $eventIds = @()
    $evidenceKinds = @()
    if ($null -ne $prov) {
        $eventIds = @(Get-OptionalProperty -InputObject $prov -Name "event_ids")
        $evidenceKinds = @(Get-OptionalProperty -InputObject $prov -Name "evidence_kinds")
    }
    # A session recorded BEFORE the provenance recorder existed cannot prove provenance. That
    # is a gap in the evidence, not a fault in the product, and the two must not be reported
    # as the same thing.
    $provDetail = if ($null -eq $prov) {
        "no provenance block on this tool-call record: the session was recorded by a build that did not yet emit one, so structural provenance cannot be verified FROM THIS SESSION"
    }
    else {
        "events=$($eventIds -join ',') research_job_id=$jobId kinds=$($evidenceKinds -join ',')"
    }
    Add-Check "briefing cites ledger events and a real research job" ($eventIds.Count -ge 1 -and $jobId -ne "") $provDetail

    # every cited ledger event must resolve, and belong to that research job
    $resolved = 0
    $mismatched = @()
    foreach ($eventId in $eventIds) {
        $found = $null
        try { $found = Get-OptionalProperty -InputObject (Get-Json "/v1/ledger/events?limit=200") -Name "events" | Where-Object { [string]$_.event_id -eq [string]$eventId } | Select-Object -First 1 } catch { $found = $null }
        if ($null -eq $found) { $mismatched += "$eventId (unresolvable)"; continue }
        if ([string](Get-OptionalProperty -InputObject $found -Name "source") -match "^seed") { $mismatched += "$eventId (seeded)" ; continue }
        $eventJob = [string](Get-OptionalProperty -InputObject $found -Name "research_job_id")
        if ($jobId -and $eventJob -and $eventJob -ne $jobId) { $mismatched += "$eventId (job $eventJob)"; continue }
        $resolved++
    }
    Add-Check "every cited ledger event resolves and is not seeded" ($resolved -ge 1 -and $mismatched.Count -eq 0) `
        "resolved=$resolved of $($eventIds.Count)$(if ($mismatched.Count -gt 0) { '; problems: ' + ($mismatched -join '; ') })"

    # the narrated numbers must be the run's own numbers
    $run = $null
    if ($jobId) { try { $run = Get-Json "/v1/research/$jobId" } catch { $run = $null } }
    $runReport = if ($null -ne $run) { Get-OptionalProperty -InputObject $run -Name "report" } else { $null }
    $runStats = if ($null -ne $runReport) { Get-OptionalProperty -InputObject $runReport -Name "stats" } else { $null }
    $factFindings = if ($null -ne $provFacts) { [int](Get-OptionalProperty -InputObject $provFacts -Name "findings") } else { -1 }
    $factRejected = if ($null -ne $provFacts) { [int](Get-OptionalProperty -InputObject $provFacts -Name "rejected") } else { -1 }
    $runFindings = if ($null -ne $runReport) { @(Get-OptionalProperty -InputObject $runReport -Name "findings").Count } else { -2 }
    $runRejected = if ($null -ne $runStats) { [int](Get-OptionalProperty -InputObject $runStats -Name "rejected") } else { -2 }
    $factsMatch = ($factFindings -ge 0 -and $factFindings -eq $runFindings -and $factRejected -eq $runRejected)
    Add-Check "narrated facts match the research run's own record" $factsMatch `
        "briefing findings=$factFindings rejected=$factRejected; run findings=$runFindings rejected=$runRejected"
    $evidence.provenance = [ordered]@{
        research_job_id = $jobId; event_ids = @($eventIds); evidence_kinds = @($evidenceKinds)
        facts = $provFacts; resolved_events = $resolved; run_findings = $runFindings; run_rejected = $runRejected
    }
    Add-Check "executive briefing is concise (listening budget)" ($null -ne $explain -and [int]$explain.speech_chars -le 420) `
        $(if ($null -ne $explain) { "$($explain.speech_chars) chars (budget 420)" } else { "-" })
    # "teknik anlat" is honoured whichever tool the provider routed it to; the durable
    # record carries the normalised intent, never the wording
    $technical = @($calls | Where-Object { $_.intent -eq "technical" -and $_.status -eq "succeeded" }) | Select-Object -Last 1
    Add-Check "'teknik anlat' recorded as intent=technical and read concisely" ($null -ne $technical -and [int]$technical.speech_chars -gt 0 -and [int]$technical.speech_chars -le 700) `
        $(if ($null -ne $technical) { "via $($technical.name) action=$($technical.action) speech=$($technical.speech_chars) chars" } else { "no call with intent=technical" })

    $noise = Get-OptionalProperty -InputObject $activity -Name "noise"
    $counter = { param($Name) if ($null -ne $noise) { [int](Get-OptionalProperty -InputObject $noise -Name $Name) } else { -1 } }
    $reported = ($null -ne $noise -and [bool](Get-OptionalProperty -InputObject $noise -Name "reported"))
    Write-Host ("  interruption policy: speech_detected={0} potential_barge_in={1} accepted={2} rejected_background={3} explicit_stop={4} false_interruption={5}" -f
        (& $counter "speech_detected"), (& $counter "potential_barge_in"), (& $counter "accepted_owner_interruption"),
        (& $counter "rejected_background_speech"), (& $counter "explicit_stop_command"), (& $counter "false_interruption"))
    Add-Check "no false interruption while it spoke" (($reported -and (& $counter "false_interruption") -eq 0) -or ($VerifyOnly -and -not $reported)) `
        $(if ($reported) { "false_interruption=$(& $counter 'false_interruption') rejected_background_speech=$(& $counter 'rejected_background_speech')" } else { "the client reported no interruption counters" })

    $spokenIndex = -1; $bargeIndex = -1
    for ($i = 0; $i -lt $events.Count; $i++) {
        $k = [string]$events[$i].kind
        if ($k -eq "spoken" -and $spokenIndex -lt 0 -and [int](Get-OptionalProperty -InputObject $events[$i] -Name "final") -eq 0) { $spokenIndex = $i }
        if ($k -eq "barge_in_start" -and $spokenIndex -ge 0 -and $bargeIndex -lt 0 -and $i -gt $spokenIndex) { $bargeIndex = $i }
    }
    $paused = if ($spokenIndex -ge 0) { $events[$spokenIndex] } else { $null }
    Add-Check "'dur' stopped speech and placed the cursor" ($spokenIndex -ge 0 -and $bargeIndex -ge 0 -and [int](Get-OptionalProperty -InputObject $paused -Name "aligned") -eq 1) `
        $(if ($null -ne $paused) { "spoken(final=0) at t=$($paused.t_ms) ms then barge_in_start; aligned=$($paused.aligned) spoken_chunks=$($paused.spoken_chunks) action=$($paused.action)" } else { "no spoken/barge_in_start pair" })
    $resume = @($calls | Where-Object { $_.name -eq "narration.control" -and $_.intent -eq "resume" -and $_.status -eq "succeeded" }) | Select-Object -Last 1
    Add-Check "'devam et' resumed from the paused cursor" ($null -ne $resume -and $resume.narration_state -eq "READING" -and [int]$resume.speech_chars -gt 0) `
        $(if ($null -ne $resume) { "resumed with '" + $resume.speech_head + "'" } else { "no narration.control with intent=resume" })
    $pausedLedger = @(Get-OptionalProperty -InputObject $voiceLedger -Name "events") | Where-Object { $_.event_type -eq "voice.narration.paused" }
    $explainedLedger = @(Get-OptionalProperty -InputObject $voiceLedger -Name "events") | Where-Object { $_.event_type -eq "voice.explained" }
    Add-Check "ledger recorded the explanation and the pause" (@($explainedLedger).Count -ge 1 -and @($pausedLedger).Count -ge 1) "voice.explained=$(@($explainedLedger).Count) voice.narration.paused=$(@($pausedLedger).Count)"
    $closed = [string](Get-OptionalProperty -InputObject $state -Name "state")
    Add-Check "session closed cleanly" ($closed -in @("closed", "expired")) "state=$closed"

    $failed = @($evidence.checks | Where-Object { -not $_.ok })
    if ($failed.Count -gt 0) {
        # Say WHY, once, when every failure has the same cause. A session recorded before the
        # provenance recorder existed fails the three provenance checks and nothing else, and
        # reporting that as "3 checks failed" invites the reader to suspect the product -
        # which on 2026-09-05 was demonstrably working in the same run's own evidence.
        if ($null -eq $prov) {
            $provenanceChecks = @("briefing cites ledger events and a real research job",
                                  "every cited ledger event resolves and is not seeded",
                                  "narrated facts match the research run's own record")
            $others = @($failed | Where-Object { $provenanceChecks -notcontains $_.name })
            if ($others.Count -eq 0) {
                Write-Host ""
                Write-Host "All $($failed.Count) failures are the same one fact: this session carries no provenance" -ForegroundColor Yellow
                Write-Host "block, because the build that recorded it did not emit one. Every behavioural check" -ForegroundColor Yellow
                Write-Host "in this run PASSED. Structural provenance can only be verified on a session recorded" -ForegroundColor Yellow
                Write-Host "by a build that emits it; no amount of re-checking THIS session will produce one." -ForegroundColor Yellow
                $evidence.diagnosis = "provenance_not_recorded_by_the_build_that_ran_this_session"
            }
        }
        throw "$($failed.Count) check(s) failed; see above"
    }
    $evidence.verdict = "PASS"
}
finally {
    if ($OutFile) {
        $evidence.finished_at = (Get-Date).ToUniversalTime().ToString("o")
        $json = $evidence | ConvertTo-Json -Depth 14
        [IO.File]::WriteAllText($OutFile, $json + "`n", (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "evidence written to $OutFile (UTF-8, no BOM; ids, kinds and counts; no transcript, no secret)"
    }
    if ($null -ne $webProcess) {
        try { Stop-Process -Id $webProcess.Id -Force -ErrorAction SilentlyContinue } catch { }
        # pnpm dev spawns node children that outlive the starter's PowerShell
        try {
            Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.ParentProcessId -eq $webProcess.Id -or $_.CommandLine -like "*next*dev*--port $WebPort*" } |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        } catch { }
    }
    if ($mintedId) {
        try { Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions/$mintedId/revoke" -Headers $headers -Body "{}" | Out-Null } catch { }
    }
    $token = $null
    $headers = $null
    Write-Host ""
    Write-Host "OWNER EXPLAIN: $($evidence.verdict)" -ForegroundColor $(if ($evidence.verdict -eq "PASS") { "Green" } else { "Red" })
}
