<#
.SYNOPSIS
    The presence-only M18 qualification: the one thing the durable record does not yet hold -
    the owner leaving the room and coming back, seen by the camera and asserted by the Presence
    Engine as a real transition (present -> away -> present/returned).

.DESCRIPTION
    Everything else in M18 is proven from the record (scripts\core\reconcile-m18.ps1,
    docs\evidence\m18-reconciliation-2026-09-06.json): voice from /core, real states, cognition,
    the eye by voice in both directions, receipts, one mutation path, the alarm, the ledger,
    privacy. This run asks for nothing that is already proven: no voice session is needed, no
    deployment, no long wait. The camera is opened from the Core's control (or by voice, either
    is fine) and the script reads the Presence Engine's DURABLE rows (presence.state_changed)
    and its live assertion, with per-step windows sized by the engine's own temporal policy:

      step 1  present    within PresentWindowSec of the camera opening (2 observations,
                         20 s window, 45 s sustain: ~60 s)
      step 2  away       within AwayWindowSec after you leave (the client remembers movement
                         for 90 s, then the engine needs 45 s of sustained absence: ~2.5 min;
                         the window is 5 min so that a slow walk out is not a failure)
      step 3  present/   within ReturnWindowSec after you come back (~60 s)
              returned

    Every 10 s it prints the live assertion (state, confidence, signals, held for) and the
    rows so far. It finishes by itself when step 3 is recorded, then closes the eye durably
    (printed, attributed). Each step that is not reached is named with what WAS seen; nothing
    already proven is re-asked or invalidated.

.EXAMPLE
    .\scripts\core\owner-m18-presence.ps1 -OutFile m18-presence-1.json
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
    [ValidateRange(30, 600)][int]$EyeWaitSec = 180,
    [ValidateRange(30, 600)][int]$PresentWindowSec = 90,
    [ValidateRange(60, 900)][int]$AwayWindowSec = 300,
    [ValidateRange(30, 600)][int]$ReturnWindowSec = 120,
    [ValidateRange(2, 30)][int]$PollSec = 5,
    [ValidateRange(10, 900)][int]$WebReadyTimeoutSec = 180
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
$runId = "owner-m18-presence-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$startedAt = (Get-Date).ToUniversalTime()
$since = $startedAt.ToString("yyyy-MM-ddTHH:mm:ssZ")

$o_uml = [char]0x00F6; $u_uml = [char]0x00FC; $c_ced = [char]0x00E7
$labelEyeOn = "G" + $o_uml + "z" + $u_uml + " a" + $c_ced
$phraseEyeOn = "G" + $o_uml + "z" + $u_uml + "n" + $u_uml + " a" + $c_ced + "."
$presentStates = @("present", "returned", "awake", "resting", "likely_asleep")
$presenceDetailAllowed = @("to_state", "confidence", "reason", "signal_sources", "signal_count")

$evidence = [ordered]@{
    run_id     = $runId
    started_at = $startedAt.ToString("o")
    cloud      = $BaseUrl
    web_shell  = $null
    steps      = @()
    rows       = @()
    samples    = @()
    checks     = @()
    verdict    = "FAIL"
}

Write-Host "PagentOS owner M18 presence ($runId)"
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

function Get-PresenceRows {
    <#  presence.state_changed rows since the run started, chronological, as (at, state, confidence, signals).  #>
    $doc = Get-JsonOrNull ("/v1/ledger/events?since=" + [uri]::EscapeDataString($since) + "&event_type=presence.state_changed&limit=100")
    $raw = Get-ArrayProperty -InputObject $doc -Name "events"
    $out = @()
    foreach ($e in ($raw | Sort-Object -Property occurred_at)) {
        $d = Get-OptionalProperty -InputObject $e -Name "detail_json"
        $out += [pscustomobject]@{
            At         = [string](Get-OptionalProperty -InputObject $e -Name "occurred_at")
            State      = $(if ($null -ne $d) { [string](Get-OptionalProperty -InputObject $d -Name "to_state") } else { "" })
            Confidence = $(if ($null -ne $d) { Get-OptionalProperty -InputObject $d -Name "confidence" } else { $null })
            Signals    = $(if ($null -ne $d) { Get-OptionalProperty -InputObject $d -Name "signal_count" } else { $null })
            Sources    = $(if ($null -ne $d) { (ConvertTo-Array -Value (Get-OptionalProperty -InputObject $d -Name "signal_sources")) -join "," } else { "" })
            Reason     = $(if ($null -ne $d) { [string](Get-OptionalProperty -InputObject $d -Name "reason") } else { "" })
        }
    }
    return , $out
}

function Format-Live {
    param($State, $Rows, [string]$Phase, [double]$ElapsedSec)
    $a = if ($null -ne $State) { Get-OptionalProperty -InputObject $State -Name "assertion" } else { $null }
    $live = if ($null -ne $a) { "{0} (confidence {1}, {2} signals, held {3} s, reason {4})" -f (Get-OptionalProperty -InputObject $a -Name "state"), (Get-OptionalProperty -InputObject $a -Name "confidence"), (Get-OptionalProperty -InputObject $a -Name "signal_count"), [math]::Round([double](Get-OptionalProperty -InputObject $a -Name "held_for_s")), (Get-OptionalProperty -InputObject $a -Name "reason") } else { "unknown" }
    $eye = if ($null -ne $State) { [bool](Get-OptionalProperty -InputObject $State -Name "eye_enabled") } else { $null }
    $seq = if ($Rows.Count) { ($Rows | ForEach-Object { "{0} {1}" -f $_.At.Substring(11, 8), $_.State }) -join " -> " } else { "none yet" }
    return , @(
        ("      [{0,4:N0} s] {1}" -f $ElapsedSec, $Phase),
        ("               eye_enabled: {0}   live assertion: {1}" -f $eye, $live),
        ("               durable transitions: {0}" -f $seq)
    )
}

$webProcess = $null
$exitCode = 2
$eyeToClose = $false
try {
 do {
    $coreUrl = "http://localhost:$WebPort/core"
    $shell = [ordered]@{ url = $coreUrl; started = $false; ready = $false; ready_after_s = $null }
    if (-not $SkipWeb) {
        $listener = Get-ListeningProcess -Port $WebPort
        if ($null -ne $listener) {
            throw ("port $WebPort is already in use by pid $($listener.Pid) ($($listener.Name), started $($listener.StartedAt)). " +
                   "Stop it (taskkill /PID $($listener.Pid) /T /F) so this run starts a shell with the current code, or pass -SkipWeb to use it deliberately.")
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

    # Start from closed so the opening is this run's.
    $state0 = Get-Json "/v1/presence/state"
    if ([bool](Get-OptionalProperty -InputObject $state0 -Name "eye_enabled")) {
        Write-Host "      the eye is enabled; closing it durably (reason owner-m18-presence:start_closed) so the run starts from closed" -ForegroundColor Yellow
        Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/presence/eye/disable" -Headers $headers -Body '{"reason":"owner-m18-presence:start_closed"}' | Out-Null
    }

    Write-Host ""
    Write-Host "Open $coreUrl and sign in. Then:" -ForegroundColor Cyan
    Write-Host ("  1. open the camera: press {0} (or connect voice and say {1}); sit in view and move a little" -f $labelEyeOn, $phraseEyeOn)
    Write-Host "  2. when this script prints 'present recorded - LEAVE NOW', leave the room and stay out until it prints 'away recorded - COME BACK'"
    Write-Host "  3. come back and sit down in view"
    Write-Host "It finishes by itself when your return is recorded. Nothing to press."
    Write-Host ""

    $t0 = [DateTimeOffset]::UtcNow
    $phase = "await_eye"
    $phaseStarted = $t0
    $lastPrint = $t0.AddSeconds(-60)
    $stopReason = ""
    $rows = @()
    $stepAt = @{}
    while ($true) {
        $now = [DateTimeOffset]::UtcNow
        $elapsed = ($now - $t0).TotalSeconds
        $inPhase = ($now - $phaseStarted).TotalSeconds
        $state = Get-JsonOrNull "/v1/presence/state"
        $rows = Get-PresenceRows
        $eyeOn = if ($null -ne $state) { [bool](Get-OptionalProperty -InputObject $state -Name "eye_enabled") } else { $false }
        $assertion = if ($null -ne $state) { Get-OptionalProperty -InputObject $state -Name "assertion" } else { $null }
        $liveState = if ($null -ne $assertion) { [string](Get-OptionalProperty -InputObject $assertion -Name "state") } else { "" }
        if ($null -ne $state) {
            $evidence.samples += [ordered]@{ at = $now.ToString("o"); eye_enabled = $eyeOn; state = $liveState; confidence = $(if ($null -ne $assertion) { Get-OptionalProperty -InputObject $assertion -Name "confidence" } else { $null }) }
        }
        $states = @($rows | ForEach-Object { $_.State })
        switch ($phase) {
            "await_eye" {
                if ($eyeOn) { $eyeToClose = $true; $stepAt["eye"] = $now; $phase = "await_present"; $phaseStarted = $now; Write-Host "      eye opened at $($now.ToString('HH:mm:ss')) - stay in view, move a little" -ForegroundColor Green }
                elseif ($inPhase -ge $EyeWaitSec) { $stopReason = "the camera was not opened within $EyeWaitSec s (eye_enabled stayed false)" }
            }
            "await_present" {
                $present = @($rows | Where-Object { $presentStates -contains $_.State -and $_.Sources -like "*camera*" })
                if ($present.Count -gt 0) { $stepAt["present"] = $now; $phase = "await_away"; $phaseStarted = $now; Write-Host ("      present recorded at {0} ({1}, confidence {2}) - LEAVE NOW and stay out" -f $present[0].At, $present[0].State, $present[0].Confidence) -ForegroundColor Green }
                elseif (-not $eyeOn) { $stopReason = "the camera closed before a present state was recorded" }
                elseif ($inPhase -ge $PresentWindowSec) { $stopReason = "no present state recorded within $PresentWindowSec s of the camera opening (live: $liveState; rows: $($states -join ','))" }
            }
            "await_away" {
                $away = @($rows | Where-Object { $_.State -eq "away" -and (ConvertTo-SessionInstant -Raw $_.At) -ge $stepAt["present"] })
                if ($away.Count -gt 0) { $stepAt["away"] = $now; $phase = "await_return"; $phaseStarted = $now; Write-Host ("      away recorded at {0} (confidence {1}) - COME BACK and sit in view" -f $away[0].At, $away[0].Confidence) -ForegroundColor Green }
                elseif (-not $eyeOn) { $stopReason = "the camera closed while waiting for away" }
                elseif ($inPhase -ge $AwayWindowSec) { $stopReason = "no away state recorded within $AwayWindowSec s of leaving (live: $liveState; rows: $($states -join ','))" }
            }
            "await_return" {
                $back = @($rows | Where-Object { $presentStates -contains $_.State -and (ConvertTo-SessionInstant -Raw $_.At) -gt $stepAt["away"] })
                if ($back.Count -gt 0) { $stepAt["return"] = $now; $phase = "done"; Write-Host ("      return recorded at {0} ({1}, confidence {2})" -f $back[0].At, $back[0].State, $back[0].Confidence) -ForegroundColor Green }
                elseif (-not $eyeOn) { $stopReason = "the camera closed while waiting for the return" }
                elseif ($inPhase -ge $ReturnWindowSec) { $stopReason = "no present/returned state recorded within $ReturnWindowSec s of coming back (live: $liveState; rows: $($states -join ','))" }
            }
        }
        if ($phase -eq "done" -or $stopReason) { break }
        if (($now - $lastPrint).TotalSeconds -ge 10) {
            foreach ($line in (Format-Live -State $state -Rows $rows -Phase $phase -ElapsedSec $elapsed)) { Write-Host $line -ForegroundColor DarkGray }
            $lastPrint = $now
        }
        Start-Sleep -Seconds $PollSec
    }
    if ($stopReason) { Write-Host "      stopped: $stopReason" -ForegroundColor Yellow }

    $rows = Get-PresenceRows
    foreach ($r in $rows) { $evidence.rows += [ordered]@{ at = $r.At; state = $r.State; confidence = $r.Confidence; signals = $r.Signals; sources = $r.Sources; reason = $r.Reason } }
    Add-Check -Name "eye.opened" -Ok ($stepAt.ContainsKey("eye")) -Detail $(if ($stepAt.ContainsKey("eye")) { "eye_enabled=true at $($stepAt['eye'].ToString('HH:mm:ss'))" } else { $stopReason })
    Add-Check -Name "presence.present_recorded" -Ok ($stepAt.ContainsKey("present")) -Detail $(if ($stepAt.ContainsKey("present")) { "a present-family state from the camera, with the engine's confidence" } else { "not reached: $stopReason" })
    Add-Check -Name "presence.away_recorded" -Ok ($stepAt.ContainsKey("away")) -Detail $(if ($stepAt.ContainsKey("away")) { "away after the present, at " + $stepAt['away'].ToString('HH:mm:ss') } else { "not reached: $stopReason" })
    Add-Check -Name "presence.return_recorded" -Ok ($stepAt.ContainsKey("return")) -Detail $(if ($stepAt.ContainsKey("return")) { "present/returned after the away, at " + $stepAt['return'].ToString('HH:mm:ss') } else { "not reached: $stopReason" })
    $allWithConfidence = $true
    foreach ($r in $rows) { if ($null -eq $r.Confidence) { $allWithConfidence = $false } }
    Add-Check -Name "presence.stated_with_confidence" -Ok ($rows.Count -gt 0 -and $allWithConfidence) -Detail "$($rows.Count) row(s): " + (($rows | ForEach-Object { "{0}={1}" -f $_.State, $_.Confidence }) -join ", ")

    $allDoc = Get-Json ("/v1/ledger/events?since=" + [uri]::EscapeDataString($since) + "&limit=200")
    $events = Get-ArrayProperty -InputObject $allDoc -Name "events"
    $violations = @()
    foreach ($e in $events) {
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

    $failed = @($evidence.checks | Where-Object { -not $_.ok })
    if ($failed.Count -eq 0) { $evidence.verdict = "PASS"; $exitCode = 0 } else { $evidence.verdict = "FAIL"; $exitCode = 2 }
 } while ($false)
}
finally {
    if ($eyeToClose) {
        try {
            Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/presence/eye/disable" -Headers $headers -Body '{"reason":"owner-m18-presence:end"}' | Out-Null
            Write-Host "      the eye is closed durably now (reason owner-m18-presence:end); the camera light goes out with the Core's next poll"
        }
        catch { Write-Host "      could not close the eye at the end; close it from the Core" -ForegroundColor Yellow }
    }
    if ($OutFile) {
        $evidence.finished_at = (Get-Date).ToUniversalTime().ToString("o")
        [System.IO.File]::WriteAllText($OutFile, ($evidence | ConvertTo-Json -Depth 12), (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "      evidence written: $OutFile"
    }
    if ($null -ne $webProcess) { Stop-WebShellProcess -Process $webProcess }
    try { Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions/$mintedId/revoke" -Headers $headers -Body "{}" | Out-Null } catch { }
    $headers = $null; $token = $null
}

Write-Host "OWNER M18 PRESENCE: $($evidence.verdict)" -ForegroundColor $(if ($evidence.verdict -eq "PASS") { "Green" } else { "Red" })
exit $exitCode
