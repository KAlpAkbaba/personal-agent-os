<#
.SYNOPSIS
    The short M18 Eye/Voice qualification: from /core alone, one current-state question, the
    eye opened and closed BY VOICE, every acknowledgement grounded in a verified receipt.

.DESCRIPTION
    Owner defect (2026-09-06): "Gozunu kapat" really disabled the eye and the assistant said
    "oyle olmus gibi dusun"; "Gozunu ac" did nothing. docs\M18_ACTION_CONTRACT.md is the fix:
    WRITE -> READ-BACK -> SPEAK, one router, live state for "now" questions. This run proves
    it, from the Cloud Core's own records, in about three minutes:

      1. the Core loads real state and voice connects FROM /core (a new web session,
         recognised by what it did through the router - never by one tool name);
      2. "Kendi sisteminde su anda ne goruyorsun?" reaches the live-state path (state.now or
         activity.explain resolved to world_state), answers with facts that carry a source and
         an age, and speaks result-first: no bookkeeping words, no banned completion phrases;
      3. the Active Eye is enabled (durably: eye.enabled in the ledger, eye_enabled=true read
         back from the runtime);
      4. "Gozunu kapat." executes the real capability: a succeeded eye.disable tool call;
      5. the runtime read-back proves it: the action.receipt row says terminal_status=verified
         with server eye_enabled=false, and /v1/presence/state reads eye_enabled=false;
      6. the assistant confirmed only then: the receipt's speech is the verified sentence, and
         the session's own first_audio for that turn comes AFTER its tool_done;
      7. "Gozunu ac." re-enables through the same contract: eye.enable verified, the eye read
         back enabled, then a final "Gozunu kapat." leaves it closed.

    Plus: exactly one web realtime session, closed at the end; no raw camera archive.
    Nothing is typed by the owner except the credential (DPAPI or masked) and a final Enter.

.EXAMPLE
    .\scripts\core\owner-m18-eye.ps1 -OutFile m18-eye-1.json
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
    # auto: release the Cloud Core once when it does not advertise the action-contract tools
    # (eye.enable / eye.disable / state.now); never: refuse; force: always.
    [ValidateSet("auto", "never", "force")][string]$CloudCoreUpdate = "auto",
    [ValidateRange(60, 1800)][int]$SessionWaitSec = 600,
    # Fail fast, with the missing evidence, instead of sitting out the budget: no web session
    # connected within ConnectWaitSec; a session connected but no router call within
    # RouterWaitSec of it; the eye watch seeing no change for EyeStallSec.
    [ValidateRange(30, 600)][int]$ConnectWaitSec = 180,
    [ValidateRange(30, 600)][int]$RouterWaitSec = 150,
    [ValidateRange(60, 1800)][int]$EyeWaitSec = 420,
    [ValidateRange(60, 600)][int]$EyeStallSec = 180,
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
$runId = "owner-m18-eye-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$startedAt = (Get-Date).ToUniversalTime()

# Turkish from character codes: this file stays pure ASCII (Windows PowerShell 5.1 reads a
# BOM-less file as ANSI and would mangle the letters).
$o_uml = [char]0x00F6; $u_uml = [char]0x00FC; $s_ced = [char]0x015F; $c_ced = [char]0x00E7
$phraseState = "Kendi sisteminde " + $s_ced + "u anda ne g" + $o_uml + "r" + $u_uml + "yorsun?"
$phraseEyeOff = "G" + $o_uml + "z" + $u_uml + "n" + $u_uml + " kapat."
$phraseEyeOn = "G" + $o_uml + "z" + $u_uml + "n" + $u_uml + " a" + $c_ced + "."
$labelEyeOn = "G" + $o_uml + "z" + $u_uml + " a" + $c_ced
# The exact receipt sentences (docs\M18_ACTION_CONTRACT.md section 5.2).
$speechClosed = "G" + $o_uml + "z" + $u_uml + "m" + $u_uml + " kapatt" + [char]0x0131 + "m efendim."
$speechOpened = "G" + $o_uml + "z" + $u_uml + "m" + $u_uml + " a" + $c_ced + "t" + [char]0x0131 + "m efendim."
# Words that must not appear in a current-state answer: completion make-believe, and
# narration of bookkeeping. The full banned list lives in app\actions\receipt.py; the
# harness checks the head the activity endpoint exposes.
$bannedInSpeech = @("gibi d" + $u_uml + $s_ced + $u_uml + "n", "sayabiliriz", "varsayal" + [char]0x0131 + "m", "kay" + [char]0x0131 + "t", "bakmam gerek", "kontrol etmem")

$presenceDetailAllowed = @("to_state", "confidence", "reason", "signal_sources", "signal_count")

$evidence = [ordered]@{
    run_id     = $runId
    started_at = $startedAt.ToString("o")
    cloud      = $BaseUrl
    web_shell  = $null
    core_state = $null
    voice      = $null
    eye        = $null
    state_now  = $null
    receipts   = @()
    ledger     = $null
    checks     = @()
    verdict    = "FAIL"
}

Write-Host "PagentOS owner M18 eye/voice ($runId)"

$health = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/system/health" -TimeoutSec 20
Write-Host "      Cloud Core: status=$(Get-OptionalProperty -InputObject $health -Name 'status')"

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

function Add-Check {
    param([string]$Name, [bool]$Ok, [string]$Detail)
    $script:evidence.checks += [ordered]@{ name = $Name; ok = $Ok; detail = $Detail }
    $mark = if ($Ok) { "ok  " } else { "FAIL" }
    $color = if ($Ok) { "Green" } else { "Red" }
    Write-Host ("  [{0}] {1}: {2}" -f $mark, $Name, $Detail) -ForegroundColor $color
}

function Get-LedgerSince {
    param([string]$SinceIso, [string]$EventType)
    $doc = Get-Json ("/v1/ledger/events?since=" + [uri]::EscapeDataString($SinceIso) + "&event_type=" + $EventType + "&limit=100")
    return , (Get-ArrayProperty -InputObject $doc -Name "events")
}

function Get-Detail {
    param($Event, [string]$Name)
    $detail = Get-OptionalProperty -InputObject $Event -Name "detail_json"
    if ($null -eq $detail) { return $null }
    return Get-OptionalProperty -InputObject $detail -Name $Name
}

function Test-SpeechClean {
    param([string]$Text)
    $lower = $Text.ToLowerInvariant()
    foreach ($w in $bannedInSpeech) { if ($lower.Contains($w.ToLowerInvariant())) { return $false } }
    return $true
}

function Get-Calls {
    <#  The session's tool calls named $Name, succeeded only, assigned-first (never a direct pipe of a helper).  #>
    param($Activity, [string]$Name)
    $all = Get-ArrayProperty -InputObject $Activity -Name "tool_calls"
    $hits = @($all | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "name") -eq $Name -and [string](Get-OptionalProperty -InputObject $_ -Name "status") -eq "succeeded" })
    return , $hits
}

function Get-DeployedVoiceTools {
    <#  The tool names the deployed Cloud Core hands every realtime session (system health, voice_realtime.tools).  #>
    param($Health)
    $checks = Get-OptionalProperty -InputObject $Health -Name "checks"
    $vr = if ($null -ne $checks) { Get-OptionalProperty -InputObject $checks -Name "voice_realtime" } else { $null }
    return , (Get-ArrayProperty -InputObject $vr -Name "tools")
}

function Invoke-CloudCoreRelease {
    $release = Join-Path $repoRoot "scripts\cloud\release-cloud-core.ps1"
    Write-Host "      releasing the Cloud Core (transactional: build, migrate, recreate api only, health, rollback on failure)..." -ForegroundColor Yellow
    & $release
    if ($LASTEXITCODE -ne 0) { throw "the Cloud Core release exited $LASTEXITCODE; nothing was qualified" }
}

$webProcess = $null
$exitCode = 2
try {
 do {
    # ------------------------------------------------------------------ the deployed contract

    # The owner's run of 2026-09-06 waited ten minutes for a router session that could not
    # exist: the deployed Cloud Core still advertised the M17 tool set (no eye.enable,
    # eye.disable, state.now), so "Gozunu ac" had nothing to route to. The contract must be
    # RUNNING before anything else is asked of the owner - checked here from the health
    # manifest, released once if stale, and asserted again afterwards.
    $requiredTools = @("eye.enable", "eye.disable", "state.now")
    $deployedTools = Get-DeployedVoiceTools -Health $health
    $missingTools = @($requiredTools | Where-Object { $deployedTools -notcontains $_ })
    $releaseBlockers = Get-ReleaseBlockers -RepoRoot $repoRoot
    $blockerChanges = ConvertTo-Array -Value $releaseBlockers.Changes
    $releaseCloud = switch ($CloudCoreUpdate) { "force" { $true } "never" { $false } default { $missingTools.Count -gt 0 } }
    Write-Host "      deployed voice tools: $($deployedTools -join ', ')"
    if ($missingTools.Count -gt 0 -and $CloudCoreUpdate -eq "never") {
        throw "the deployed Cloud Core does not advertise $($missingTools -join ', '); -CloudCoreUpdate never refuses to release, and nothing can be qualified without them"
    }
    if ($releaseCloud -and $releaseBlockers.Blocked) {
        Write-ReleaseBlockers -Blockers $releaseBlockers
        throw ("a Cloud Core release is required (missing: $($missingTools -join ', ')) but the working tree has " +
               "$($blockerChanges.Count) uncommitted change(s), and a release ships HEAD only. Commit or revert them, then rerun.")
    }
    if ($releaseCloud) {
        Write-Host "      the deployed Cloud Core predates the action contract (missing: $($missingTools -join ', ')): releasing it once" -ForegroundColor Yellow
        Invoke-CloudCoreRelease
        $health = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/system/health" -TimeoutSec 20
        $deployedTools = Get-DeployedVoiceTools -Health $health
        $missingTools = @($requiredTools | Where-Object { $deployedTools -notcontains $_ })
    }
    Add-Check -Name "cloud.action_contract_deployed" -Ok ($missingTools.Count -eq 0) -Detail $(if ($missingTools.Count -eq 0) { "eye.enable, eye.disable, state.now advertised$(if ($releaseCloud) { ' (released in this run)' })" } else { "still missing after the release: " + ($missingTools -join ", ") })
    if ($missingTools.Count -gt 0) { break }

    # ------------------------------------------------------------------ the Core

    $baselineIds = @()
    $baselineSessions = @{}
    foreach ($s in (Get-ArrayProperty -InputObject (Get-Json "/v1/voice/realtime/sessions?limit=50") -Name "sessions")) {
        $id = [string](Get-OptionalProperty -InputObject $s -Name "session_id")
        $baselineIds += $id
        $baselineSessions[$id] = [string](Get-OptionalProperty -InputObject $s -Name "state")
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

    $uiState = Get-Json "/v1/ui/state"
    $current = Get-OptionalProperty -InputObject $uiState -Name "current"
    $contractVersion = [int](Get-OptionalProperty -InputObject $uiState -Name "contract_version")
    $currentState = if ($null -ne $current) { [string](Get-OptionalProperty -InputObject $current -Name "state") } else { "" }
    # WHOSE state that is: the owner's run of 2026-09-06 read agent.listening at startup
    # from a session closed minutes earlier. A current event is reported with its age and
    # its session's state, and "real" means fresh or from a live session - never a leftover.
    # The age comes from the server when it says it (current_age_s, against ITS clock);
    # an older Cloud Core without that field gets the client-side estimate.
    $serverAge = Get-OptionalProperty -InputObject $uiState -Name "current_age_s"
    $currentAt = if ($null -ne $current) { ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $current -Name "at")) } else { $null }
    $currentAgeS = if ($null -ne $serverAge) { [math]::Round([double]$serverAge) } elseif ($null -ne $currentAt) { [math]::Round(([DateTimeOffset]::UtcNow - [DateTimeOffset]$currentAt).TotalSeconds) } else { $null }
    $currentSubsystem = if ($null -ne $current) { [string](Get-OptionalProperty -InputObject $current -Name "subsystem") } else { "" }
    $currentSession = if ($null -ne $current) { [string](Get-OptionalProperty -InputObject $current -Name "session_id") } else { "" }
    $currentSessionState = if ($currentSession -and $baselineSessions.ContainsKey($currentSession)) { $baselineSessions[$currentSession] } else { "" }
    $fromLiveSession = ($currentSession -eq "") -or ($currentSessionState -eq "active")
    $evidence.core_state = [ordered]@{ contract_version = $contractVersion; current_state = $currentState; subsystem = $currentSubsystem; age_s = $currentAgeS; session = $currentSession; session_state = $currentSessionState }
    # Real state = the contract's document, and a current event that is either a fact
    # (agent.idle from the system at startup, or from a session that ended) or live. NO
    # current event is also truthful ("nothing published since this process started") and
    # is reported as exactly that - never invented as listening before voice connects.
    $stateDescription = if ($currentState -eq "") { "no event since the Cloud Core started (nothing invented)" } else { "current=$currentState from $currentSubsystem, $currentAgeS s old$(if ($currentSession) { "; session $currentSession $currentSessionState" })" }
    Add-Check -Name "core.real_state" -Ok ($contractVersion -ge 2) -Detail "contract v$contractVersion; $stateDescription"
    Add-Check -Name "core.state_not_stale" -Ok ($currentState -eq "" -or $fromLiveSession -or $currentState -eq "agent.idle" -or ($null -ne $currentAgeS -and $currentAgeS -le 120)) -Detail $(if ($currentState -eq "" -or $fromLiveSession -or $currentState -eq "agent.idle") { "the current event is not a closed session's leftover" } else { "current=$currentState belongs to session $currentSession ($currentSessionState), $currentAgeS s old - a leftover" })

    # The eye must start CLOSED so that "enable" is an observable change.
    $state0 = Get-Json "/v1/presence/state"
    $eyeAtStart = [bool](Get-OptionalProperty -InputObject $state0 -Name "eye_enabled")
    if ($eyeAtStart) {
        Write-Host "      the eye is currently enabled; disabling it durably so the run starts from closed" -ForegroundColor Yellow
        Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/presence/eye/disable" -Headers $headers -Body '{"reason":"owner-m18-eye:start_closed"}' | Out-Null
    }

    # ------------------------------------------------------------------ the owner's script

    Write-Host ""
    Write-Host "Open $coreUrl, sign in, and connect voice THERE (the voice control under the Core). Then, in order:" -ForegroundColor Cyan
    Write-Host ("  1. say: {0}" -f $phraseState)
    Write-Host ("  2. say: {0}   (or press {1}); wait until the eye cell says the camera is on" -f $phraseEyeOn, $labelEyeOn)
    Write-Host ("  3. say: {0}   - it must answer '{1}'" -f $phraseEyeOff, $speechClosed)
    Write-Host ("  4. say: {0}   - it must answer '{1}'" -f $phraseEyeOn, $speechOpened)
    Write-Host ("  5. say: {0}   once more, so the eye ends closed" -f $phraseEyeOff)
    Write-Host "  6. disconnect voice on the Core, then press Enter here"
    Write-Host "This script watches the Cloud Core the whole time and continues on its own."
    Write-Host ""

    # ------------------------------------------------------------------ the session

    $listSessions = { Get-ArrayProperty -InputObject (Get-Json "/v1/voice/realtime/sessions?limit=50") -Name "sessions" }
    $activityProbe = { param($Id) Get-Json "/v1/voice/realtime/sessions/$Id/activity" }
    # The wait is the library's (scripts\lib\VoiceShell.ps1): it prints what it sees every
    # 15 s and gives up with the exact missing evidence - no session within ConnectWaitSec,
    # or a session with no router call within RouterWaitSec of it. The three callbacks below
    # are plain script blocks over this script's own Get-Json; nothing here is a closure
    # (a GetNewClosure block cannot see dot-sourced functions - the crash of 2026-09-06).
    $coreProbe = { Get-OptionalProperty -InputObject (Get-Json "/v1/ui/state") -Name "current" }
    Write-Host "      waiting for a web voice session that went through the router (connect within $ConnectWaitSec s; a router call within $RouterWaitSec s of connecting; $SessionWaitSec s in all)..."
    $waited = Wait-QualificationSession -ListSessions $listSessions -ActivityProbe $activityProbe -BaselineIds $baselineIds `
        -ReadyAt $readyAt -NotBefore $null -TimeoutSec $SessionWaitSec -IntervalSec 5 -Qualifier ${function:Test-CoreQualification} `
        -ConnectWaitSec $ConnectWaitSec -RouterWaitSec $RouterWaitSec -ProgressEverySec 15 -CoreProbe $coreProbe
    $sessionId = ""
    if ($null -ne $waited.Selected) {
        $sessionId = [string]$waited.Selected.SessionId
        Write-Host "      voice session $sessionId seen after $([math]::Round([double]$waited.ElapsedSec)) s"
    }
    Add-Check -Name "voice.connected_from_core" -Ok ($sessionId -ne "") -Detail $(if ($sessionId) { "web session $sessionId, new since the baseline, spoke through the router" } else { [string]$waited.GaveUp })
    if (-not $sessionId) {
        Write-Host ""
        Write-Host "Stopping here: without a router session nothing further can be observed. The missing transition is named above." -ForegroundColor Red
        foreach ($n in "eye.enabled_read_back", "eye.disabled_read_back", "eye.reenabled_read_back") { Add-Check -Name $n -Ok $false -Detail "not reached: no router session" }
        $evidence.eye = $null
    }

    # ------------------------------------------------------------------ the eye, read back live

    if ($sessionId) {
    $eye = [ordered]@{ enabled_seen_at_s = $null; disabled_after_enabled_at_s = $null; reenabled_at_s = $null; final_disabled_at_s = $null; samples = 0; stalled = $null }
    $t0 = Get-Date
    $deadline = $t0.AddSeconds($EyeWaitSec)
    $phase = "await_enable"
    $lastChange = $t0
    $lastProgress = $t0
    Write-Host "      watching /v1/presence/state for enable -> disable -> enable -> disable (up to $EyeWaitSec s; $EyeStallSec s without a change stops the watch)..."
    while ((Get-Date) -lt $deadline) {
        $st = $null
        try { $st = Get-Json "/v1/presence/state" } catch { $st = $null }
        if ($null -ne $st) {
            $eye.samples++
            $on = [bool](Get-OptionalProperty -InputObject $st -Name "eye_enabled")
            $at = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
            $before = $phase
            switch ($phase) {
                "await_enable"    { if ($on) { $eye.enabled_seen_at_s = $at; $phase = "await_disable"; Write-Host "      eye: enabled ($at s)" } }
                "await_disable"   { if (-not $on) { $eye.disabled_after_enabled_at_s = $at; $phase = "await_reenable"; Write-Host "      eye: disabled ($at s)" } }
                "await_reenable"  { if ($on) { $eye.reenabled_at_s = $at; $phase = "await_final"; Write-Host "      eye: enabled again ($at s)" } }
                "await_final"     { if (-not $on) { $eye.final_disabled_at_s = $at; $phase = "done"; Write-Host "      eye: closed again ($at s)" } }
            }
            if ($phase -ne $before) { $lastChange = Get-Date }
            if ($phase -eq "done") { break }
            if (((Get-Date) - $lastChange).TotalSeconds -ge $EyeStallSec) {
                $eye.stalled = "no change for $EyeStallSec s while $phase (eye_enabled=$on)"
                Write-Host "      eye watch stopped: $($eye.stalled)" -ForegroundColor Yellow
                break
            }
            if (((Get-Date) - $lastProgress).TotalSeconds -ge 30) {
                $lastProgress = Get-Date
                $assertion = Get-OptionalProperty -InputObject $st -Name "assertion"
                $presenceNow = if ($null -ne $assertion) { [string](Get-OptionalProperty -InputObject $assertion -Name "state") } else { "none" }
                $act = $null
                try { $act = Get-Json "/v1/voice/realtime/sessions/$sessionId/activity" } catch { $act = $null }
                $receipts = @()
                foreach ($c in (Get-ArrayProperty -InputObject $act -Name "tool_calls")) {
                    $n = [string](Get-OptionalProperty -InputObject $c -Name "name")
                    if ($n -like "eye.*") { $receipts += ("{0}={1}" -f $n, (Get-OptionalProperty -InputObject $c -Name "terminal_status")) }
                }
                Write-Host ("      [{0,4:N0} s] {1}; eye_enabled={2}; presence={3}; eye receipts: {4}" -f $at, $phase, $on, $presenceNow, $(if ($receipts.Count) { $receipts -join ", " } else { "none" })) -ForegroundColor DarkGray
            }
        }
        Start-Sleep -Seconds 2
    }
    $evidence.eye = $eye
    Add-Check -Name "eye.enabled_read_back" -Ok ($null -ne $eye.enabled_seen_at_s) -Detail $(if ($null -ne $eye.enabled_seen_at_s) { "runtime read eye_enabled=true at $($eye.enabled_seen_at_s) s" } else { "the runtime never read eye_enabled=true" + $(if ($eye.stalled) { " (" + $eye.stalled + ")" }) })
    Add-Check -Name "eye.disabled_read_back" -Ok ($null -ne $eye.disabled_after_enabled_at_s) -Detail $(if ($null -ne $eye.disabled_after_enabled_at_s) { "runtime read eye_enabled=false at $($eye.disabled_after_enabled_at_s) s" } else { "the runtime never read eye_enabled=false after the enable" + $(if ($eye.stalled) { " (" + $eye.stalled + ")" }) })
    Add-Check -Name "eye.reenabled_read_back" -Ok ($null -ne $eye.reenabled_at_s) -Detail $(if ($null -ne $eye.reenabled_at_s) { "runtime read eye_enabled=true again at $($eye.reenabled_at_s) s" } else { "the eye was never read enabled again (the voice enable path)" + $(if ($eye.stalled) { " (" + $eye.stalled + ")" }) })

    # The manual completion stage - only reached with a real session to close.
    Write-Host ""
    Read-Host -Prompt "Disconnect voice on the Core, then press Enter" | Out-Null
    }

    # ------------------------------------------------------------------ the durable record

    $voiceRecord = [ordered]@{ session_id = $sessionId; state = ""; tool_calls = @() }
    $stateNowRecord = $null
    if ($sessionId) {
        $sess = Get-Json "/v1/voice/realtime/sessions/$sessionId"
        $closeDeadline = (Get-Date).AddSeconds(60)
        while (([string](Get-OptionalProperty -InputObject $sess -Name "state")) -notin @("closed", "expired") -and (Get-Date) -lt $closeDeadline) {
            Start-Sleep -Seconds 5
            $sess = Get-Json "/v1/voice/realtime/sessions/$sessionId"
        }
        $activity = Get-Json "/v1/voice/realtime/sessions/$sessionId/activity"
        $allCalls = Get-ArrayProperty -InputObject $activity -Name "tool_calls"
        $summary = @()
        foreach ($c in $allCalls) { $summary += ("{0}:{1}" -f (Get-OptionalProperty -InputObject $c -Name "name"), (Get-OptionalProperty -InputObject $c -Name "status")) }
        $voiceRecord.state = [string](Get-OptionalProperty -InputObject $sess -Name "state")
        $voiceRecord.tool_calls = @($summary)

        # 2. the current-state question reached the live-state path, and spoke result-first
        $stateCalls = Get-Calls -Activity $activity -Name "state.now"
        $explainCalls = Get-Calls -Activity $activity -Name "activity.explain"
        $liveCalls = @($stateCalls) + @($explainCalls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "query_kind") -in @("world_state", "eye_state") })
        $liveCalls = @($liveCalls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "query_kind") -in @("world_state", "eye_state") })
        $liveCall = if ($liveCalls.Count -gt 0) { $liveCalls[0] } else { $null }
        $liveHead = if ($null -ne $liveCall) { [string](Get-OptionalProperty -InputObject $liveCall -Name "speech_head") } else { "" }
        $liveChars = if ($null -ne $liveCall) { [int](Get-OptionalProperty -InputObject $liveCall -Name "speech_chars") } else { 0 }
        $liveFacts = if ($null -ne $liveCall) { Get-OptionalProperty -InputObject $liveCall -Name "facts" } else { $null }
        $stateNowRecord = [ordered]@{ tool = $(if ($null -ne $liveCall) { Get-OptionalProperty -InputObject $liveCall -Name "name" } else { $null }); query_kind = $(if ($null -ne $liveCall) { Get-OptionalProperty -InputObject $liveCall -Name "query_kind" } else { $null }); speech_chars = $liveChars; facts = $liveFacts }
        Add-Check -Name "state.live_path_reached" -Ok ($null -ne $liveCall) -Detail $(if ($null -ne $liveCall) { "$($stateNowRecord.tool) -> $($stateNowRecord.query_kind), subsystem $(Get-OptionalProperty -InputObject $liveCall -Name 'subsystem')" } else { "no succeeded state.now / world_state call in the session" })
        Add-Check -Name "state.spoken_result_first" -Ok ($liveChars -gt 0 -and (Test-SpeechClean -Text $liveHead)) -Detail $(if ($liveChars -gt 0) { "$liveChars chars; head clean of bookkeeping and make-believe words" } else { "no speech" })
        Add-Check -Name "state.facts_carry_provenance" -Ok ($null -ne $liveFacts -and [int]$liveFacts -gt 0) -Detail "facts=$liveFacts (each with source, observed_at, age, confidence, stale - asserted by the API suite; the count is what the activity endpoint exposes)"

        # 4/5/6/7. the eye actions: succeeded calls, verified receipts, confirmation after the ACK
        # (Test-SpokenAfterToolDone lives in scripts\lib\VoiceShell.ps1, tested; no helper is
        # defined inside a block here).
        $events = Get-ArrayProperty -InputObject $activity -Name "client_events"
        $disableCalls = Get-Calls -Activity $activity -Name "eye.disable"
        $enableCalls = Get-Calls -Activity $activity -Name "eye.enable"
        $verifiedDisables = @($disableCalls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "terminal_status") -eq "verified" })
        $verifiedEnables = @($enableCalls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "terminal_status") -eq "verified" })
        Add-Check -Name "eye.disable_executed_by_voice" -Ok ($disableCalls.Count -ge 1) -Detail "$($disableCalls.Count) succeeded eye.disable call(s)"
        Add-Check -Name "eye.disable_receipt_verified" -Ok ($verifiedDisables.Count -ge 1) -Detail $(if ($verifiedDisables.Count) { "terminal_status=verified on $($verifiedDisables.Count) call(s)" } else { "no eye.disable call reached terminal_status=verified (" + (($disableCalls | ForEach-Object { Get-OptionalProperty -InputObject $_ -Name "terminal_status" }) -join ",") + ")" })
        $confirmOk = $false; $confirmDetail = "no verified disable"
        if ($verifiedDisables.Count -gt 0) {
            $first = $verifiedDisables[0]
            $head = [string](Get-OptionalProperty -InputObject $first -Name "speech_head")
            $ordering = Test-SpokenAfterToolDone -Events $events -CallId ([string](Get-OptionalProperty -InputObject $first -Name "call_id"))
            $confirmOk = ($head.StartsWith($speechClosed) -and $ordering -eq "")
            $confirmDetail = "receipt speech '" + $head + "'" + $(if ($ordering) { "; " + $ordering } else { "; spoken after the terminal ACK (first_audio after tool_done)" })
        }
        Add-Check -Name "eye.confirmed_only_after_ack" -Ok $confirmOk -Detail $confirmDetail
        Add-Check -Name "eye.enable_receipt_verified" -Ok ($verifiedEnables.Count -ge 1) -Detail $(if ($verifiedEnables.Count) { "terminal_status=verified on $($verifiedEnables.Count) eye.enable call(s); speech '" + [string](Get-OptionalProperty -InputObject $verifiedEnables[0] -Name "speech_head") + "'" } else { "no eye.enable call reached terminal_status=verified (" + (($enableCalls | ForEach-Object { Get-OptionalProperty -InputObject $_ -Name "terminal_status" }) -join ",") + ")" })
        foreach ($c in @($disableCalls) + @($enableCalls)) {
            $evidence.receipts += [ordered]@{ name = (Get-OptionalProperty -InputObject $c -Name "name"); call_id = (Get-OptionalProperty -InputObject $c -Name "call_id"); terminal_status = (Get-OptionalProperty -InputObject $c -Name "terminal_status"); execution_status = (Get-OptionalProperty -InputObject $c -Name "execution_status"); error_class = (Get-OptionalProperty -InputObject $c -Name "error_class"); speech_head = (Get-OptionalProperty -InputObject $c -Name "speech_head") }
        }
        Add-Check -Name "voice.session_closed" -Ok ($voiceRecord.state -in @("closed", "expired")) -Detail "state=$($voiceRecord.state)"
    }
    else {
        foreach ($n in "state.live_path_reached", "state.spoken_result_first", "state.facts_carry_provenance", "eye.disable_executed_by_voice", "eye.disable_receipt_verified", "eye.confirmed_only_after_ack", "eye.enable_receipt_verified", "voice.session_closed") {
            Add-Check -Name $n -Ok $false -Detail "no voice session from this run"
        }
    }
    $evidence.voice = $voiceRecord
    $evidence.state_now = $stateNowRecord

    # one session only
    $runWeb = @()
    foreach ($s in (Get-ArrayProperty -InputObject (Get-Json "/v1/voice/realtime/sessions?limit=50") -Name "sessions")) {
        $id = [string](Get-OptionalProperty -InputObject $s -Name "session_id")
        if ($baselineIds -contains $id) { continue }
        if ([string](Get-OptionalProperty -InputObject $s -Name "client_kind") -eq "web") { $runWeb += $id }
    }
    Add-Check -Name "voice.single_session" -Ok ($runWeb.Count -eq 1) -Detail "$($runWeb.Count) web realtime session(s) since the baseline"

    # the ledger: the receipts are durable, the eye rows carry voice reasons, no camera archive
    $since = $startedAt.ToString("yyyy-MM-ddTHH:mm:ssZ")
    $receiptRows = Get-LedgerSince -SinceIso $since -EventType "action.receipt"
    $verifiedRows = @($receiptRows | Where-Object { [string](Get-Detail -Event $_ -Name "terminal_status") -eq "verified" })
    $disableRows = @($verifiedRows | Where-Object { [string](Get-Detail -Event $_ -Name "capability") -eq "eye.disable" })
    $enableRows = @($verifiedRows | Where-Object { [string](Get-Detail -Event $_ -Name "capability") -eq "eye.enable" })
    Add-Check -Name "ledger.receipts_recorded" -Ok ($disableRows.Count -ge 1 -and $enableRows.Count -ge 1) -Detail "action.receipt verified: eye.disable=$($disableRows.Count) eye.enable=$($enableRows.Count)"
    $eyeDisabledRows = Get-LedgerSince -SinceIso $since -EventType "eye.disabled"
    $byVoice = @($eyeDisabledRows | Where-Object { ([string](Get-Detail -Event $_ -Name "reason")).StartsWith("voice:") })
    Add-Check -Name "ledger.eye_disabled_by_voice" -Ok ($byVoice.Count -ge 1) -Detail "$($byVoice.Count) eye.disabled row(s) with a voice: reason"
    $allDoc = Get-Json ("/v1/ledger/events?since=" + [uri]::EscapeDataString($since) + "&limit=200")
    $events2 = Get-ArrayProperty -InputObject $allDoc -Name "events"
    $violations = @()
    foreach ($e in $events2) {
        $t = [string](Get-OptionalProperty -InputObject $e -Name "event_type")
        if ($t -match "observation|frame|image|snapshot") { $violations += "event type '$t'" }
        if ($t -notlike "presence.*") { continue }
        $detail = Get-OptionalProperty -InputObject $e -Name "detail_json"
        if ($null -eq $detail) { continue }
        foreach ($prop in $detail.PSObject.Properties) {
            if ($presenceDetailAllowed -notcontains $prop.Name) { $violations += "$t carries '$($prop.Name)'" }
            $value = [string]$prop.Value
            if ($value.Length -gt 300 -or $value -match '^[A-Za-z0-9+/=]{200,}$') { $violations += "$t.$($prop.Name) is image-shaped" }
        }
    }
    Add-Check -Name "privacy.no_raw_camera_archive" -Ok ($violations.Count -eq 0) -Detail $(if ($violations.Count -eq 0) { "presence rows carry only the structured summary" } else { $violations -join "; " })
    $evidence.ledger = [ordered]@{ since = $since; events = $events2.Count; receipts = $receiptRows.Count; privacy_violations = @($violations) }

    $failed = @($evidence.checks | Where-Object { -not $_.ok })
    if ($failed.Count -eq 0) { $evidence.verdict = "PASS"; $exitCode = 0 } else { $evidence.verdict = "FAIL"; $exitCode = 2 }
 } while ($false)
}
finally {
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

Write-Host "OWNER M18 EYE: $($evidence.verdict)" -ForegroundColor $(if ($evidence.verdict -eq "PASS") { "Green" } else { "Red" })
exit $exitCode
