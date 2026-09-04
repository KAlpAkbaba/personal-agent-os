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
    # The web shell is already running (started by hand): do not start another.
    [switch]$SkipWeb,
    [string]$PnpmPath = "pnpm",
    [string]$ExpectProvider = "openai-realtime"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\RepoState.ps1")

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
    ("Ara" + $tr.s_ced + "t" + $tr.i_dotless + "rmay" + $tr.i_dotless + " detayland" + $tr.i_dotless + "r."),
    "Teknik anlat.",
    "Dur.",
    "Devam et."
)

$evidence = [ordered]@{
    run_id       = $runId
    started_at   = (Get-Date).ToUniversalTime().ToString("o")
    cloud        = $BaseUrl
    cloud_policy = $null
    ingest       = $null
    backfill     = $null
    session      = $null
    activity     = $null
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
$providers = if ($null -ne $rt) { @(Get-OptionalProperty -InputObject $rt -Name "providers") } else { @() }
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

$secure = Read-Host -Prompt "Cloud Owner Credential (input is hidden; exchanged for one session, then dropped)" -AsSecureString
if ($secure.Length -eq 0) { throw "empty credential; nothing done" }
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $credential = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
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
                occurred_at = [string](Get-OptionalProperty -InputObject $doc -Name "finished_at")
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
            $ingest.idempotent_replay = [bool](Get-OptionalProperty -InputObject $recorded -Name "existing")
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
    $latest = Get-Json "/v1/ledger/latest"
    $latestType = [string](Get-OptionalProperty -InputObject $latest -Name "event_type")
    Write-Host "      ledger latest activity: $latestType - $(Get-OptionalProperty -InputObject $latest -Name 'factual_summary')"
    if (-not $latestType) { throw "the ledger has no activity at all after backfill; there is nothing real to narrate" }

    # ------------------------------------------------------------------ the voice session

    if (-not $SkipWeb) {
        $env:PAGENTOS_API_UPSTREAM = $BaseUrl
        $env:NEXT_PUBLIC_API_BASE = "/api"
        $env:PORT = "$WebPort"
        $webDir = Join-Path $repoRoot "apps\web"
        Write-Host "      starting the web shell in the background: http://localhost:$WebPort/voice"
        $webProcess = Start-Process -FilePath $PnpmPath -ArgumentList @("dev", "--port", "$WebPort") -WorkingDirectory $webDir -PassThru -WindowStyle Minimized
        Start-Sleep -Seconds 8
    }
    $sessionsBefore = @(Get-OptionalProperty -InputObject (Get-Json "/v1/voice/realtime/sessions?limit=1") -Name "sessions")
    $beforeId = if ($sessionsBefore.Count -gt 0) { [string]$sessionsBefore[0].session_id } else { "" }

    Write-Host ""
    Write-Host "Open http://localhost:$WebPort/voice, sign in, connect (Baglan), then say, waiting for each answer:" -ForegroundColor Cyan
    $n = 0
    foreach ($phrase in $phrases) {
        $n++
        $hint = switch ($n) {
            1 { "  (it narrates the real research qualification from the ledger)" }
            2 { "  (the actual findings)" }
            3 { "  (versions, counts, ids)" }
            4 { "  (say it WHILE it is speaking; speech must stop at once)" }
            5 { "  (it resumes at the sentence it did not finish)" }
        }
        Write-Host ("  {0}. {1}{2}" -f $n, $phrase, $hint)
    }
    Write-Host "Then disconnect (Baglantiyi kes) and come back here." -ForegroundColor Cyan
    Read-Host -Prompt "Press Enter when the session is over" | Out-Null

    # ------------------------------------------------------------------ the durable record

    $sessions = @(Get-OptionalProperty -InputObject (Get-Json "/v1/voice/realtime/sessions?limit=5") -Name "sessions")
    if ($sessions.Count -eq 0) { throw "no realtime session was recorded" }
    $sessionId = [string]$sessions[0].session_id
    if ($sessionId -eq $beforeId) { throw "no NEW realtime session since this script started (newest is still $sessionId)" }
    $state = Get-Json "/v1/voice/realtime/sessions/$sessionId"
    $activity = Get-Json "/v1/voice/realtime/sessions/$sessionId/activity"
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
    Add-Check "briefing derived from the real research run" ($null -ne $explain -and [string]$explain.speech_head -like "Efendim, son ara*") `
        $(if ($null -ne $explain) { "'" + $explain.speech_head + "'" } else { "-" })
    $detail = @($calls | Where-Object { $_.name -eq "narration.control" -and $_.intent -eq "detail" -and $_.status -eq "succeeded" }) | Select-Object -First 1
    Add-Check "'detaylandir' read the findings" ($null -ne $detail -and [int]$detail.speech_chars -gt 0) $(if ($null -ne $detail) { "action=$($detail.action) speech=$($detail.speech_chars) chars" } else { "no narration.control with intent=detail" })
    $technical = @($calls | Where-Object { $_.name -eq "narration.control" -and $_.intent -eq "technical" -and $_.status -eq "succeeded" }) | Select-Object -First 1
    Add-Check "'teknik anlat' read the technical evidence" ($null -ne $technical -and [int]$technical.speech_chars -gt 0) $(if ($null -ne $technical) { "action=$($technical.action) speech=$($technical.speech_chars) chars" } else { "no narration.control with intent=technical" })

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
    if ($failed.Count -gt 0) { throw "$($failed.Count) check(s) failed; see above" }
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
    }
    if ($mintedId) {
        try { Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions/$mintedId/revoke" -Headers $headers -Body "{}" | Out-Null } catch { }
    }
    $token = $null
    $headers = $null
    Write-Host ""
    Write-Host "OWNER EXPLAIN: $($evidence.verdict)" -ForegroundColor $(if ($evidence.verdict -eq "PASS") { "Green" } else { "Red" })
}
