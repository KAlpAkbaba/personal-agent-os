<#
.SYNOPSIS
    M18.3 owner qualification A - the Living Core. Starts the web shell, proves the served
    build IS the Living Core, opens the door for the owner's visual review, and records from
    the Cloud Core's own record that the Core really listened and really spoke while the
    owner looked at it.

.DESCRIPTION
    The owner reviews scale, depth, the gold/amber identity, speaking, listening, the eye and
    the research constellation with their own eyes; nothing here asks a yes/no question. What
    the record CAN prove is proven:

      1  the served /core document carries the Living Core build marker
         (<meta name="pagentos-core-build" content="living-core-1">, server-rendered, no session);
      2  a Core voice session is correlated by the ids the bus publishes (never by a tool name);
      3  the Core listened: the session's own mic_speech_start;
      4  the Core spoke: a turn with first_audio -> audio_done (ADR-0066: SPEAKING ends at the
         last audio, never at response.done).

    No Cloud Core release and no agent install: the Living Core is web-only (a v2 bus still
    renders). Windows are bounded; the run finishes by itself once 3 and 4 are on the record.

.EXAMPLE
    .\scripts\core\owner-m18-3-core.ps1 -OutFile m18-3-core-1.json
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
    [ValidateRange(30, 900)][int]$ConnectWaitSec = 180,
    [ValidateRange(30, 900)][int]$ReviewWaitSec = 300,
    [ValidateRange(2, 30)][int]$PollSec = 4,
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
$runId = "owner-m18-3-core-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$startedAt = (Get-Date).ToUniversalTime()
$runStart = [DateTimeOffset]::UtcNow
$expectedBuild = "living-core-1"

$u_uml = [char]0x00FC; $g_br = [char]0x011F; $o_uml = [char]0x00F6; $s_ced = [char]0x015F; $i_dot = [char]0x0131; $c_ced = [char]0x00E7
$phraseHello = "Merhaba, beni duyuyor musun? Bana k" + $i_dot + "saca kendini anlat."
$phraseEyeOn = "G" + $o_uml + "z" + $u_uml + "n" + $u_uml + " a" + $c_ced + "."
$phraseEyeOff = "G" + $o_uml + "z" + $u_uml + "n" + $u_uml + " kapat."
$phraseResearch = "Bug" + $u_uml + "n" + $u_uml + "n teknoloji haberlerini k" + $i_dot + "saca ara" + $s_ced + "t" + $i_dot + "r."

$evidence = [ordered]@{ run_id = $runId; started_at = $startedAt.ToString("o"); cloud = $BaseUrl; web_shell = $null; build = $null; session = $null; listening = $null; speaking = @(); checks = @(); verdict = "FAIL" }

Write-Host "PagentOS owner M18.3 A - the Living Core ($runId)"
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

function Get-ServedCoreBuild {
    <#  The build marker in the server-rendered /core document, fetched with no session. Empty when absent.  #>
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 30
        $html = [string]$response.Content
    }
    catch { return "" }
    $m = [regex]::Match($html, 'name="pagentos-core-build"\s+content="([^"]+)"')
    if (-not $m.Success) { $m = [regex]::Match($html, 'content="([^"]+)"\s+name="pagentos-core-build"') }
    if ($m.Success) { return $m.Groups[1].Value }
    return ""
}

$webProcess = $null
$exitCode = 2
try {
 do {
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

    $servedBuild = Get-ServedCoreBuild -Url $coreUrl
    $evidence.build = [ordered]@{ expected = $expectedBuild; served = $servedBuild }
    Add-Check -Name "core.living_build_served" -Ok ($servedBuild -eq $expectedBuild) -Detail $(if ($servedBuild) { "served build marker: $servedBuild" } else { "no pagentos-core-build marker in the served /core document (an older build, or the shell serves a different checkout)" })
    if ($servedBuild -ne $expectedBuild) { break }

    Write-Host ""
    Write-Host "Open $coreUrl, sign in, and LOOK: the Core should fill most of the window - gold and amber," -ForegroundColor Cyan
    Write-Host "layered and deep, breathing slowly. Try the fullscreen control (Esc leaves it). Then connect voice there and:" -ForegroundColor Cyan
    Write-Host ("  1. say: {0}   (watch it pull inward while you speak, then hold SPEAKING through its whole answer)" -f $phraseHello)
    Write-Host ("  2. say: {0}  then  {1}   (the aperture appears and disappears with the real camera)" -f $phraseEyeOn, $phraseEyeOff)
    Write-Host ("  3. optional: {0}   (a bounded constellation while it researches)" -f $phraseResearch)
    Write-Host "Scale, depth, colour and motion are yours to judge. This script finishes by itself once it has"
    Write-Host "recorded the Core listening and speaking; nothing to press."
    Write-Host ""

    # ------------------------------------------------------------------ the watch
    $sessionId = ""
    $phase = "await_session"
    $phaseStarted = [DateTimeOffset]::UtcNow
    $lastPrint = [DateTimeOffset]::UtcNow.AddSeconds(-60)
    $lastSig = ""
    $stopReason = ""
    $speakingTurns = @()
    $listenCount = 0
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
            if ($null -ne $pick) { $sessionId = [string]$pick.session_id; $phase = "await_review"; $phaseStarted = $now; Write-Host "      Core Voice connected: $sessionId" -ForegroundColor Green }
            elseif ($inPhase -ge $ConnectWaitSec) { $stopReason = "no Core voice session within $ConnectWaitSec s - connect voice on /core" }
        }
        $activity = $null
        if ($sessionId) { $activity = Get-JsonOrNull "/v1/voice/realtime/sessions/$sessionId/activity" }
        $events = Get-ArrayProperty -InputObject $activity -Name "client_events"
        $listenEvents = @($events | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "kind") -eq "mic_speech_start" })
        $listenCount = $listenEvents.Count
        $speakingTurns = Get-SpeechTurns -Events $events
        $completed = @($speakingTurns | Where-Object { $null -ne $_.first_audio_ms -and $null -ne $_.audio_done_ms })
        if ($phase -eq "await_review") {
            if ($listenCount -ge 1 -and $completed.Count -ge 1) { $phase = "done"; Write-Host ("      recorded: the Core listened ({0} mic_speech_start) and spoke ({1} completed turn(s); first audible {2} ms)" -f $listenCount, $completed.Count, $completed[0].audible_ms) -ForegroundColor Green }
            elseif ($inPhase -ge $ReviewWaitSec) { $stopReason = "within $ReviewWaitSec s of connecting: listening events $listenCount, completed spoken turns $($completed.Count)" }
        }
        if ($phase -eq "done" -or $stopReason) { break }
        $sig = "{0}|{1}|{2}|{3}" -f $phase, $listenCount, $speakingTurns.Count, $completed.Count
        if ($sig -ne $lastSig -or ($now - $lastPrint).TotalSeconds -ge 30) {
            Write-Host ("      [{0,4:N0} s] {1}   session: {2}   listened: {3}   spoken turns: {4} (completed {5})" -f $elapsed, $phase, $(if ($sessionId) { $sessionId } else { "none yet" }), $listenCount, $speakingTurns.Count, $completed.Count) -ForegroundColor DarkGray
            $lastSig = $sig; $lastPrint = $now
        }
        Start-Sleep -Seconds $PollSec
    }
    if ($stopReason) { Write-Host "      stopped: $stopReason" -ForegroundColor Yellow }

    # ------------------------------------------------------------------ the checks
    $evidence.session = [ordered]@{ id = $sessionId }
    Add-Check -Name "voice.core_session_correlated" -Ok ($sessionId -ne "") -Detail $(if ($sessionId) { "session $sessionId" } else { $stopReason })
    $evidence.listening = [ordered]@{ mic_speech_start = $listenCount }
    Add-Check -Name "core.listening_recorded" -Ok ($listenCount -ge 1) -Detail $(if ($listenCount -ge 1) { "$listenCount mic_speech_start event(s) on the session (the inward pull was a real gate opening)" } else { "no mic_speech_start on the session: $stopReason" })
    foreach ($t in $speakingTurns) { $evidence.speaking += $t }
    $completed = @($speakingTurns | Where-Object { $null -ne $_.first_audio_ms -and $null -ne $_.audio_done_ms })
    Add-Check -Name "core.speaking_recorded" -Ok ($completed.Count -ge 1) -Detail $(if ($completed.Count) { "$($completed.Count) turn(s) first_audio -> audio_done; bases: " + (($completed | ForEach-Object { $_.basis }) -join ",") } else { "no turn reached audio_done: $stopReason" })
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

Write-Host "OWNER M18.3 A (LIVING CORE): $($evidence.verdict)" -ForegroundColor $(if ($evidence.verdict -eq "PASS") { "Green" } else { "Red" })
exit $exitCode
