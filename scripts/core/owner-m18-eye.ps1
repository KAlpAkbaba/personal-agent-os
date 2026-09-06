<#
.SYNOPSIS
    The short M18 Eye/Voice qualification, third form: from /core alone, the owner says
    "Gozunu ac", "Gozunu kapat", "Gozunu ac" - and the run finishes by itself the moment the
    third terminal receipt is verified. No Enter, no blind waits.

.DESCRIPTION
    What it proves, every point from the Cloud Core's own records (docs\M18_ACTION_CONTRACT.md):

      - the Core's voice session is correlated by its canonical id - the one the Core names
        on the UI-state bus - never inferred from a state name;
      - each spoken eye command became ONE tool call whose receipt says what the browser
        observed (local state, media track readiness, the action trace) and what the Cloud
        Core read back, with terminal_status=verified and the exact acknowledgement;
      - after each step the runtime read-back (/v1/presence/state) and the live-state path
        (/v1/state/now?scope=eye) agree with the browser;
      - no hidden second mutation path: every voice-attributed eye row in the ledger falls
        inside the window of a receipt of this run;
      - receipts, ledger rows and the session share one session identity; action ids are
        unique; exactly one web session of this run.

    While it waits it prints, whenever something changes (and at least every 30 s):

        Core Voice session:  <id>
        last routed action:  eye.enable / eye.disable
        last action_id:      <call id>
        browser eye state:   ACTIVE / DISABLED / ERROR (media track live/ended)
        receipt:             <capability> <terminal> "<speech>"
        state.now:           <the live-state answer for the eye>
        runtime read-back:   eye_enabled=<bool>

    It stops with the exact missing evidence: no Core session within ConnectWaitSec; a step
    not reached within StepWaitSec of the previous one; TotalWaitSec in all.

    Preflight (unchanged): the deployed Cloud Core must advertise eye.enable / eye.disable /
    state.now (released once if not, -CloudCoreUpdate auto|never|force); nothing else may
    already listen on -WebPort; the web shell is started and stopped as a whole tree. The eye
    starts closed (a durable disable with an attributed reason if it was open) and is left
    closed at the end (same attribution) - both printed.

.EXAMPLE
    .\scripts\core\owner-m18-eye.ps1 -OutFile m18-eye-3.json
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
    [ValidateRange(30, 600)][int]$StepWaitSec = 120,
    [ValidateRange(60, 1800)][int]$TotalWaitSec = 480,
    [ValidateRange(2, 30)][int]$PollSec = 3,
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
$runStart = [DateTimeOffset]::UtcNow

# Turkish from character codes: this file stays pure ASCII.
$o_uml = [char]0x00F6; $u_uml = [char]0x00FC; $c_ced = [char]0x00E7; $i_dot = [char]0x0131
$phraseEyeOff = "G" + $o_uml + "z" + $u_uml + "n" + $u_uml + " kapat."
$phraseEyeOn = "G" + $o_uml + "z" + $u_uml + "n" + $u_uml + " a" + $c_ced + "."
$speechClosed = "G" + $o_uml + "z" + $u_uml + "m" + $u_uml + " kapatt" + $i_dot + "m efendim."
$speechOpened = "G" + $o_uml + "z" + $u_uml + "m" + $u_uml + " a" + $c_ced + "t" + $i_dot + "m efendim."
$expectedSteps = @("eye.enable", "eye.disable", "eye.enable")
$expectedSpeech = @($speechOpened, $speechClosed, $speechOpened)

$presenceDetailAllowed = @("to_state", "confidence", "reason", "signal_sources", "signal_count")

$evidence = [ordered]@{
    run_id      = $runId
    started_at  = $startedAt.ToString("o")
    cloud       = $BaseUrl
    web_shell   = $null
    core_state  = $null
    session     = $null
    steps       = @()
    receipts    = @()
    read_backs  = @()
    state_now   = $null
    ledger      = $null
    checks      = @()
    verdict     = "FAIL"
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
function Get-JsonOrNull { param([string]$Path) try { return Invoke-JsonUtf8 -Uri "$BaseUrl$Path" -Headers $headers -TimeoutSec 30 } catch { return $null } }

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

function Get-DeployedVoiceTools {
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

function Get-EyeStateNow {
    <#  The live-state path's own answer for the eye (null on a Cloud Core without the route).  #>
    $doc = Get-JsonOrNull "/v1/state/now?scope=eye"
    if ($null -eq $doc) { return $null }
    $enabled = $null
    foreach ($f in (Get-ArrayProperty -InputObject $doc -Name "facts")) {
        if ([string](Get-OptionalProperty -InputObject $f -Name "key") -eq "eye.enabled") { $enabled = Get-OptionalProperty -InputObject $f -Name "value" }
    }
    return [pscustomobject]@{ Speech = [string](Get-OptionalProperty -InputObject $doc -Name "speech"); Enabled = $enabled }
}

function Format-EyeDiagnostics {
    param([string]$SessionId, $Steps, [AllowNull()]$ReadBack, [AllowNull()]$StateNow, [double]$ElapsedSec)
    $last = if ($null -ne $Steps -and $Steps.Receipts.Count -gt 0) { $Steps.Receipts[$Steps.Receipts.Count - 1] } else { $null }
    $lines = @()
    $lines += ("      [{0,4:N0} s] Core Voice session:  {1}" -f $ElapsedSec, $(if ($SessionId) { $SessionId } else { "none yet - connect voice on /core" }))
    $lines += ("               steps verified:      {0}/{1} ({2})" -f $(if ($null -ne $Steps) { $Steps.Satisfied } else { 0 }), $expectedSteps.Count, ($expectedSteps -join " -> "))
    $lines += ("               last routed action:  {0}" -f $(if ($null -ne $last) { $last.Name } else { "none" }))
    $lines += ("               last action_id:      {0}" -f $(if ($null -ne $last) { $last.CallId } else { "none" }))
    $lines += ("               browser eye state:   {0}" -f $(if ($null -ne $last) { "{0} (media track {1})" -f $(if ($last.LocalState) { $last.LocalState } else { "not reported" }), $(if ($last.Track) { $last.Track } else { "unknown" }) } else { "none" }))
    $lines += ("               receipt:             {0}" -f $(if ($null -ne $last) { "{0} {1}{2} `"{3}`"" -f $last.Name, $last.Terminal, $(if ($last.ErrorClass) { " (" + $last.ErrorClass + ")" } else { "" }), $last.Speech } else { "none" }))
    if ($null -ne $last -and $last.Trace.Count -gt 0) { $lines += ("               action trace:        {0}" -f ($last.Trace -join " > ")) }
    $lines += ("               state.now:           {0}" -f $(if ($null -ne $StateNow) { $StateNow.Speech } else { "route not deployed yet" }))
    $lines += ("               runtime read-back:   eye_enabled={0}" -f $(if ($null -ne $ReadBack) { $ReadBack } else { "unknown" }))
    return , $lines
}

$webProcess = $null
$exitCode = 2
$eyeLeftOpen = $false
try {
 do {
    # ------------------------------------------------------------------ the deployed contract

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
    $shell = [ordered]@{ url = $coreUrl; started = $false; ready = $false; ready_after_s = $null; baseline_sessions = $baselineIds.Count; existing_listener = $null }
    if (-not $SkipWeb) {
        $listener = Get-ListeningProcess -Port $WebPort
        if ($null -ne $listener) {
            $shell.existing_listener = [ordered]@{ pid = $listener.Pid; name = $listener.Name; started_at = $listener.StartedAt }
            $evidence.web_shell = $shell
            throw ("port $WebPort is already in use by pid $($listener.Pid) ($($listener.Name), started $($listener.StartedAt)) - a web shell from an earlier run, or one you started. " +
                   "Stop it (taskkill /PID $($listener.Pid) /T /F) so this run starts a shell with the current code, or pass -SkipWeb to qualify against that one deliberately.")
        }
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

    $uiState = Get-Json "/v1/ui/state"
    $current = Get-OptionalProperty -InputObject $uiState -Name "current"
    $contractVersion = [int](Get-OptionalProperty -InputObject $uiState -Name "contract_version")
    $currentState = if ($null -ne $current) { [string](Get-OptionalProperty -InputObject $current -Name "state") } else { "" }
    $serverAge = Get-OptionalProperty -InputObject $uiState -Name "current_age_s"
    $currentAt = if ($null -ne $current) { ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $current -Name "at")) } else { $null }
    $currentAgeS = if ($null -ne $serverAge) { [math]::Round([double]$serverAge) } elseif ($null -ne $currentAt) { [math]::Round(([DateTimeOffset]::UtcNow - [DateTimeOffset]$currentAt).TotalSeconds) } else { $null }
    $currentSubsystem = if ($null -ne $current) { [string](Get-OptionalProperty -InputObject $current -Name "subsystem") } else { "" }
    $currentSession = if ($null -ne $current) { [string](Get-OptionalProperty -InputObject $current -Name "session_id") } else { "" }
    $currentSessionState = if ($currentSession -and $baselineSessions.ContainsKey($currentSession)) { $baselineSessions[$currentSession] } else { "" }
    $fromLiveSession = ($currentSession -eq "") -or ($currentSessionState -eq "active")
    $evidence.core_state = [ordered]@{ contract_version = $contractVersion; current_state = $currentState; subsystem = $currentSubsystem; age_s = $currentAgeS; session = $currentSession; session_state = $currentSessionState }
    $stateDescription = if ($currentState -eq "") { "no event since the Cloud Core started (nothing invented)" } else { "current=$currentState from $currentSubsystem, $currentAgeS s old$(if ($currentSession) { "; session $currentSession $currentSessionState" })" }
    Add-Check -Name "core.real_state" -Ok ($contractVersion -ge 2) -Detail "contract v$contractVersion; $stateDescription"
    Add-Check -Name "core.state_not_stale" -Ok ($currentState -eq "" -or $fromLiveSession -or $currentState -eq "agent.idle" -or ($null -ne $currentAgeS -and $currentAgeS -le 120)) -Detail $(if ($currentState -eq "" -or $fromLiveSession -or $currentState -eq "agent.idle") { "the current event is not a closed session's leftover" } else { "current=$currentState belongs to session $currentSession ($currentSessionState), $currentAgeS s old - a leftover" })

    # The eye starts CLOSED, so the first "ac" is an observable change. An attributed
    # mutation by this harness, printed - never a hidden one.
    $state0 = Get-Json "/v1/presence/state"
    if ([bool](Get-OptionalProperty -InputObject $state0 -Name "eye_enabled")) {
        Write-Host "      the eye is enabled; closing it durably (reason owner-m18-eye:start_closed) so the run starts from closed" -ForegroundColor Yellow
        Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/presence/eye/disable" -Headers $headers -Body '{"reason":"owner-m18-eye:start_closed"}' | Out-Null
    }

    # ------------------------------------------------------------------ the owner's script

    Write-Host ""
    Write-Host "Open $coreUrl, sign in, and connect voice THERE (the voice control under the Core). Then say, in order:" -ForegroundColor Cyan
    Write-Host ("  1. {0}   - it must answer '{1}'" -f $phraseEyeOn, $speechOpened)
    Write-Host ("  2. {0}   - it must answer '{1}'" -f $phraseEyeOff, $speechClosed)
    Write-Host ("  3. {0}   - it must answer '{1}'" -f $phraseEyeOn, $speechOpened)
    Write-Host "This script finishes by itself when the third receipt is verified. Nothing to press."
    Write-Host ""

    # ------------------------------------------------------------------ the watch

    $sessionId = ""
    $busIds = @()
    $lastSignature = ""
    $lastPrintAt = [DateTimeOffset]::UtcNow.AddSeconds(-60)
    $lastAdvanceAt = [DateTimeOffset]::UtcNow
    $satisfied = 0
    $steps = $null
    $stopReason = ""
    $acceptSince = $runStart.AddSeconds(-5)
    while ($true) {
        $now = [DateTimeOffset]::UtcNow
        $elapsed = ($now - $runStart).TotalSeconds
        # Correlation: the session the Core names on the bus, then the sessions of this run.
        $busDoc = Get-JsonOrNull "/v1/ui/state?limit=64"
        $busEvents = Get-ArrayProperty -InputObject $busDoc -Name "events"
        $busIds = Get-BusVoiceSessionIds -Events $busEvents -Since $acceptSince
        $sessionsDoc = Get-JsonOrNull "/v1/voice/realtime/sessions?limit=50"
        $allSessions = Get-ArrayProperty -InputObject $sessionsDoc -Name "sessions"
        $mine = Get-NewSessions -Sessions $allSessions -BaselineIds $baselineIds -ReadyAt $null -AcceptIds $busIds
        if (-not $sessionId) {
            $named = @($mine | Where-Object { $busIds -contains [string]$_.session_id })
            $pick = if ($named.Count -gt 0) { $named[$named.Count - 1] } elseif ($mine.Count -gt 0) { $mine[0] } else { $null }
            if ($null -ne $pick) {
                $sessionId = [string]$pick.session_id
                $lastAdvanceAt = $now
                Write-Host "      Core Voice session correlated: $sessionId (named by the bus: $($busIds -contains $sessionId))"
            }
        }
        $readBack = $null
        $stateNow = $null
        if ($sessionId) {
            $activity = Get-JsonOrNull "/v1/voice/realtime/sessions/$sessionId/activity"
            $calls = Get-ArrayProperty -InputObject $activity -Name "tool_calls"
            $steps = Get-EyeReceiptSteps -Calls $calls -Expected $expectedSteps
            $presenceDoc = Get-JsonOrNull "/v1/presence/state"
            if ($null -ne $presenceDoc) { $readBack = [bool](Get-OptionalProperty -InputObject $presenceDoc -Name "eye_enabled") }
            $stateNow = Get-EyeStateNow
            if ($steps.Satisfied -gt $satisfied) {
                # A step advanced: read back NOW, against this receipt.
                for ($i = $satisfied; $i -lt $steps.Satisfied; $i++) {
                    $r = $steps.Matched[$i]
                    $wantEnabled = ($expectedSteps[$i] -eq "eye.enable")
                    $agree = ($null -ne $readBack -and $readBack -eq $wantEnabled)
                    $nowAgrees = $(if ($null -ne $stateNow -and $null -ne $stateNow.Enabled) { ([bool]$stateNow.Enabled) -eq $wantEnabled } else { $null })
                    $evidence.read_backs += [ordered]@{ step = ($i + 1); capability = $r.Name; action_id = $r.CallId; runtime_eye_enabled = $readBack; agrees = $agree; state_now_agrees = $nowAgrees; state_now = $(if ($null -ne $stateNow) { $stateNow.Speech } else { $null }); local_state = $r.LocalState; media_track = $r.Track; speech = $r.Speech; at = $now.ToString("o") }
                    Write-Host ("      step {0} verified: {1} action_id={2} browser={3}/{4} runtime eye_enabled={5} speech `"{6}`"" -f ($i + 1), $r.Name, $r.CallId, $r.LocalState, $r.Track, $readBack, $r.Speech) -ForegroundColor Green
                }
                $satisfied = $steps.Satisfied
                $lastAdvanceAt = $now
            }
            if ($steps.Done) { break }
        }
        $signature = "{0}|{1}|{2}|{3}" -f $sessionId, $(if ($null -ne $steps) { $steps.Receipts.Count } else { 0 }), $readBack, $(if ($null -ne $steps -and $steps.Receipts.Count) { $steps.Receipts[$steps.Receipts.Count - 1].Terminal } else { "" })
        if ($signature -ne $lastSignature -or ($now - $lastPrintAt).TotalSeconds -ge 30) {
            foreach ($line in (Format-EyeDiagnostics -SessionId $sessionId -Steps $steps -ReadBack $readBack -StateNow $stateNow -ElapsedSec $elapsed)) { Write-Host $line -ForegroundColor DarkGray }
            $lastSignature = $signature
            $lastPrintAt = $now
        }
        if (-not $sessionId -and $elapsed -ge $ConnectWaitSec) { $stopReason = "no Core voice session within $ConnectWaitSec s (bus named: $($busIds.Count); new web sessions: $($mine.Count)) - connect voice on /core"; break }
        if ($sessionId -and ($now - $lastAdvanceAt).TotalSeconds -ge $StepWaitSec) { $stopReason = "step $($satisfied + 1) ($($expectedSteps[$satisfied])) not verified within $StepWaitSec s of the previous one"; break }
        if ($elapsed -ge $TotalWaitSec) { $stopReason = "budget of $TotalWaitSec s spent at step $($satisfied + 1)"; break }
        Start-Sleep -Seconds $PollSec
    }
    if ($stopReason) { Write-Host "      stopped: $stopReason" -ForegroundColor Yellow }

    # ------------------------------------------------------------------ the checks

    $evidence.session = [ordered]@{ id = $sessionId; bus_named = ($busIds -contains $sessionId); bus_ids = @($busIds) }
    Add-Check -Name "voice.core_session_correlated" -Ok ($sessionId -ne "" -and ($busIds -contains $sessionId)) -Detail $(if ($sessionId) { "session $sessionId; named by the Core's own bus events: $($busIds -contains $sessionId)" } else { $stopReason })

    # Assigned inside the branch, never through an if-EXPRESSION: an empty array through
    # one becomes $null, a one-element array becomes its element (OwnerHarness.ps1).
    $receipts = @()
    if ($null -ne $steps) { $receipts = $steps.Receipts }
    foreach ($r in $receipts) {
        $evidence.receipts += [ordered]@{ name = $r.Name; action_id = $r.CallId; terminal = $r.Terminal; error_class = $r.ErrorClass; local_state = $r.LocalState; media_track = $r.Track; trace = @($r.Trace); speech = $r.Speech; session_id = $r.SessionId; created_at = $r.CreatedAt }
    }
    for ($i = 0; $i -lt $expectedSteps.Count; $i++) {
        $name = "eye.step$($i + 1)_$($expectedSteps[$i].Replace('eye.', ''))_verified"
        $ok = ($null -ne $steps -and $steps.Satisfied -gt $i)
        $detail = ""
        if ($ok) {
            $r = $steps.Matched[$i]
            $wantLocal = if ($expectedSteps[$i] -eq "eye.enable") { "ACTIVE" } else { "DISABLED" }
            $wantTrack = if ($expectedSteps[$i] -eq "eye.enable") { "live" } else { "ended" }
            $localOk = ($r.LocalState -eq $wantLocal)
            $trackOk = ($r.Track -eq "" -or $r.Track -eq $wantTrack)
            $speechOk = $r.Speech.StartsWith($expectedSpeech[$i])
            $ok = $localOk -and $trackOk -and $speechOk
            $detail = "action_id=$($r.CallId) browser=$($r.LocalState) track=$(if ($r.Track) { $r.Track } else { 'not reported' }) speech `"$($r.Speech)`""
            if (-not $ok) { $detail += " - expected browser $wantLocal, track $wantTrack, speech '$($expectedSpeech[$i])'" }
        }
        else {
            $failed = @($receipts | Where-Object { $_.Name -eq $expectedSteps[$i] -and $_.Terminal -ne "verified" })
            $detail = if ($failed.Count) { "not verified; the $($expectedSteps[$i]) receipts said: " + (($failed | ForEach-Object { "{0}{1} `"{2}`"" -f $_.Terminal, $(if ($_.ErrorClass) { " (" + $_.ErrorClass + ")" } else { "" }), $_.Speech }) -join "; ") } else { "no $($expectedSteps[$i]) receipt reached (stopped: $stopReason)" }
        }
        Add-Check -Name $name -Ok $ok -Detail $detail
        $evidence.steps += [ordered]@{ step = ($i + 1); capability = $expectedSteps[$i]; ok = $ok; detail = $detail }
    }
    $rbAll = @($evidence.read_backs)
    $rbOk = ($rbAll.Count -eq $expectedSteps.Count) -and (@($rbAll | Where-Object { -not $_.agrees }).Count -eq 0)
    Add-Check -Name "eye.runtime_read_back_agrees" -Ok $rbOk -Detail $(if ($rbAll.Count) { ($rbAll | ForEach-Object { "step $($_.step): eye_enabled=$($_.runtime_eye_enabled)" }) -join ", " } else { "no step to read back" })
    $snowKnown = @($rbAll | Where-Object { $null -ne $_.state_now_agrees })
    $evidence.state_now = [ordered]@{ available = ($snowKnown.Count -gt 0); samples = @($rbAll | ForEach-Object { $_.state_now }) }
    Add-Check -Name "eye.state_now_agrees" -Ok ($rbAll.Count -eq $expectedSteps.Count -and ($snowKnown.Count -eq 0 -or @($snowKnown | Where-Object { -not $_.state_now_agrees }).Count -eq 0)) -Detail $(if ($snowKnown.Count) { "/v1/state/now?scope=eye agreed with the browser on $($snowKnown.Count) step(s)" } elseif ($rbAll.Count) { "the live-state route is not deployed yet; the tool's answer is on the session record" } else { "no step to compare" })

    # one identity across command, receipt and ledger; unique action ids
    $since = $startedAt.ToString("yyyy-MM-ddTHH:mm:ssZ")
    $receiptRows = Get-LedgerSince -SinceIso $since -EventType "action.receipt"
    $ids = @($receipts | ForEach-Object { $_.CallId })
    $uniqueIds = @($ids | Sort-Object -Unique)
    $sessionsOnReceipts = @($receipts | ForEach-Object { $_.SessionId } | Where-Object { $_ } | Sort-Object -Unique)
    $rowSessions = @($receiptRows | ForEach-Object { [string](Get-Detail -Event $_ -Name "session_id") } | Where-Object { $_ } | Sort-Object -Unique)
    $correlated = ($receipts.Count -gt 0) -and ($ids.Count -eq $uniqueIds.Count) -and ($sessionsOnReceipts.Count -le 1) -and ($sessionsOnReceipts.Count -eq 0 -or $sessionsOnReceipts[0] -eq $sessionId) -and ($rowSessions.Count -eq 0 -or ($rowSessions.Count -eq 1 -and $rowSessions[0] -eq $sessionId))
    Add-Check -Name "eye.receipts_correlated" -Ok $correlated -Detail "$($receipts.Count) receipt(s), $($uniqueIds.Count) unique action id(s); session on receipts: $(if ($sessionsOnReceipts.Count) { $sessionsOnReceipts -join ',' } else { 'not exposed' }); on ledger rows: $(if ($rowSessions.Count) { $rowSessions -join ',' } else { 'not exposed' }); $($receiptRows.Count) action.receipt row(s)"

    # one canonical mutation path
    # Assigned first: @(helper-call) of a , @(...) return would wrap the whole array as ONE
    # element (the third array trap); @($variable) keeps the rows.
    $disabledRows = Get-LedgerSince -SinceIso $since -EventType "eye.disabled"
    $enabledRows = Get-LedgerSince -SinceIso $since -EventType "eye.enabled"
    $eyeRows = @($disabledRows) + @($enabledRows)
    $hidden = Test-HiddenEyeMutation -LedgerRows $eyeRows -Receipts $receiptRows
    Add-Check -Name "eye.no_hidden_mutation" -Ok ($hidden.Count -eq 0) -Detail $(if ($hidden.Count -eq 0) { "every voice-attributed eye row falls inside a receipt window ($($eyeRows.Count) eye row(s), $($receiptRows.Count) receipt(s))" } else { "unreceipted voice mutation(s): " + ($hidden -join "; ") })

    # exactly one web session of this run
    $runWeb = @($mine | ForEach-Object { [string]$_.session_id } | Sort-Object -Unique)
    Add-Check -Name "voice.single_session" -Ok ($runWeb.Count -eq 1) -Detail "$($runWeb.Count) web session(s) of this run: $($runWeb -join ', ')"

    # no raw camera archive
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
    $evidence.ledger = [ordered]@{ since = $since; events = $events2.Count; receipts = $receiptRows.Count; eye_rows = $eyeRows.Count; privacy_violations = @($violations) }

    $eyeLeftOpen = ($null -ne $steps -and $steps.Done)
    $failed = @($evidence.checks | Where-Object { -not $_.ok })
    if ($failed.Count -eq 0) { $evidence.verdict = "PASS"; $exitCode = 0 } else { $evidence.verdict = "FAIL"; $exitCode = 2 }
 } while ($false)
}
finally {
    if ($eyeLeftOpen) {
        # The owner's sequence ends with the eye open. It is closed here, attributed and
        # printed, so no camera is left running after a test - the local loop stops when
        # the Core's bus says eye.disabled.
        try {
            Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/presence/eye/disable" -Headers $headers -Body '{"reason":"owner-m18-eye:end"}' | Out-Null
            Write-Host "      the eye was left open by the third step; closed durably now (reason owner-m18-eye:end)"
        }
        catch { Write-Host "      could not close the eye at the end; close it from the Core (Gozu kapat)" -ForegroundColor Yellow }
    }
    if ($OutFile) {
        $evidence.finished_at = (Get-Date).ToUniversalTime().ToString("o")
        $json = $evidence | ConvertTo-Json -Depth 14
        [System.IO.File]::WriteAllText($OutFile, $json, (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "      evidence written: $OutFile"
    }
    if ($null -ne $webProcess) { Stop-WebShellProcess -Process $webProcess }
    try { Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions/$mintedId/revoke" -Headers $headers -Body "{}" | Out-Null } catch { }
    $headers = $null; $token = $null
}

Write-Host "OWNER M18 EYE: $($evidence.verdict)" -ForegroundColor $(if ($evidence.verdict -eq "PASS") { "Green" } else { "Red" })
exit $exitCode
