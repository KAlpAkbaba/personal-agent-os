<#
.SYNOPSIS
    B47 owner checkpoint - the device microphone, measured on the owner's own machine with the
    owner's own voice: privacy first, then recognition, then the browser-less and offline paths.

.DESCRIPTION
    Automated tests never open a microphone (they feed synthetic frames). This script is the
    PHYSICAL half of B47 and it is OWNER_REQUIRED: somebody has to speak, stay silent, press the
    mute key, pull the network. Everything the script concludes is READ - from the device's own
    `desktop.voice_status` (through the Cloud Core's device command route), from the device row's
    heartbeat status, from the realtime session record, from the alarm row and the ledger, and
    from the companion's own log on this disk - never from a yes/no typed by the owner.

    Phases (default: all, in this order; -Phase picks some):

      preflight   P1 Cloud Core reachable            P2 the Session Companion runs in this session
                  P3 voice is configured (user env)  P4 the device is online and advertises
                  desktop.voice_status               P5 voice_status: running, listening, a
                  microphone present, captured by the session companion, raw_audio_persisted
                  false, remote_enable_allowed false P6 the heartbeat carries the same voice state
      enroll      E1 the owner records each offline phrase (--voice-enroll) E2 every phrase is
                  available E3 the running companion reloaded them (voice_status says so)
      privacy     V1 30 s of silence -> 0 utterances, 0 admitted frames
                  V2 one spoken sentence -> >= 1 utterance and > 0 admitted frames
                  V3 the tray switch OFF -> not capturing, indicator off; speech -> 0 utterances;
                     switch ON -> capturing again
                  V4 the hardware mute -> mic_muted true, not capturing, indicator muted;
                     speech -> 0 utterances; unmute -> capturing again
                  V5 no audio file written anywhere this script can see, and the spoken test
                     words appear in no companion log line
      wake        W1 wake_word mode selected from the tray W2 5 sentences without the wake word
                  -> 0 utterances W3 5 x "wake word + request" -> >= 4 utterances
                  W4 -SoakMinutes of ordinary room sound -> <= 2 false wakes per hour
                  W5 back to continuous (the owner's chosen default)
      browserless B1 no owner browser is running B2 "Saat kac?" said to the device ->
                  a windows_desktop realtime session records the utterance and the answer
      offline     O1 a test alarm (snooze 2 min) is armed on the device O2 the Cloud Core rings it
                  O3 with the network OFF the owner says "ertele" -> the companion log records a
                  LOCAL snooze O4 "saat kac" offline -> the log records time.tell executed
                  O5 network ON -> the ledger records alarm.local_snoozed for that alarm and
                  voice_status counts >= 2 executed offline commands
                  O6 the device rings the snoozed alarm again on its own time

    Verdict: PASS only when every gate of every selected phase passed. Evidence JSON with every
    measurement goes to -OutFile. Nothing here deploys, installs or changes the Cloud Core.

.PARAMETER EnableVoice
    Writes PAGENTOS_AGENT_VoiceEnabled=true and PAGENTOS_AGENT_CloudCoreUrl=<BaseUrl> into the
    owner's USER environment and restarts the "PagentOS Session Companion" logon task, so the
    installed companion starts its voice service. Without it, P3 only reports what is missing.

.EXAMPLE
    .\scripts\core\qualify-device-voice.ps1 -EnableVoice -OutFile b47-device-voice-1.json

.EXAMPLE
    .\scripts\core\qualify-device-voice.ps1 -Phase privacy,offline -SkipEnroll -OutFile b47-2.json
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    [string]$BaseUrl = "",
    [string]$OutFile = "",
    [ValidateSet("all", "preflight", "enroll", "privacy", "wake", "browserless", "offline")][string[]]$Phase = @("all"),
    [ValidateRange(2, 5)][int]$Takes = 3,
    [ValidateRange(1, 120)][int]$SoakMinutes = 10,
    [string]$CompanionExe = "",
    [string]$CompanionDataDir = "",
    [switch]$EnableVoice,
    [switch]$SkipEnroll,
    [ValidateRange(1, 30)][int]$PollSec = 2
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
. (Join-Path $repoRoot "scripts\lib\OwnerHarness.ps1")

if (-not $BaseUrl) { $BaseUrl = "http://${BrokerHost}:$ApiPort" }
$BaseUrl = $BaseUrl.TrimEnd('/')
$runId = "b47-device-voice-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$runStart = [DateTimeOffset]::UtcNow
$selected = if ($Phase -contains "all") { @("preflight", "enroll", "privacy", "wake", "browserless", "offline") } else { @($Phase) }
if ($SkipEnroll) { $selected = @($selected | Where-Object { $_ -ne "enroll" }) }

# Turkish words, spelled with char codes so this file stays ASCII.
$c_ced = [char]0x00E7; $i_dot = [char]0x0131; $s_ced = [char]0x015F; $g_br = [char]0x011F; $u_uml = [char]0x00FC
$sayTime = "Saat ka" + $c_ced + "?"
$saySnooze = "ertele"
$sayOff = "dinlemeyi kapat"
$sayProbe = "Mavi kalem masan" + $i_dot + "n " + $u_uml + "st" + $u_uml + "nde duruyor."   # V2/V5: distinctive words
$probeWords = @("kalem", "masan")

$companionTask = "PagentOS Session Companion"
$voiceCap = "desktop.voice_status"
$evidence = [ordered]@{
    run_id = $runId; started_at = $runStart.ToString("o"); cloud = $BaseUrl; phases = $selected
    device = $null; measurements = [ordered]@{}; checks = @(); verdict = "FAIL"
}

function Add-Check {
    param([string]$Name, [bool]$Ok, [string]$Detail)
    $script:evidence.checks += [ordered]@{ name = $Name; ok = $Ok; detail = $Detail; at = [DateTimeOffset]::UtcNow.ToString("o") }
    $mark = if ($Ok) { "PASS" } else { "FAIL" }
    $color = if ($Ok) { "Green" } else { "Red" }
    Write-Host ("  [{0}] {1}: {2}" -f $mark, $Name, $Detail) -ForegroundColor $color
}

function Wait-Owner {
    param([string]$Instruction)
    Write-Host ""
    Write-Host ">>> $Instruction" -ForegroundColor Cyan
    [void](Read-Host ("    (Enter'a bas" + $i_dot + "n)"))
}

function Save-Evidence {
    if ($OutFile) {
        $json = $script:evidence | ConvertTo-Json -Depth 12
        [System.IO.File]::WriteAllText([System.IO.Path]::GetFullPath($OutFile), $json, (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "evidence: $OutFile"
    }
}

# --------------------------------------------------------------------- Cloud Core session

function Get-OwnerHeaders {
    $credential = $null
    try { $credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL" } catch { $credential = $null }
    if (-not $credential) {
        $secure = Read-Host -Prompt "Cloud Owner Credential (hidden; exchanged for one session)" -AsSecureString
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
    return @{ Authorization = "Bearer $([string]$issued.token)" }
}

function Test-CloudReachable {
    try { [void](Invoke-JsonUtf8 -Uri "$BaseUrl/v1/system/health" -TimeoutSec 5); return $true } catch { return $false }
}

function Get-Json { param([string]$Path) try { return Invoke-JsonUtf8 -Uri "$BaseUrl$Path" -Headers $script:headers -TimeoutSec 30 } catch { return $null } }
function Send-Json { param([string]$Path, [string]$Body) return Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl$Path" -Headers $script:headers -Body $Body -TimeoutSec 30 }

function Get-Device {
    $doc = Get-Json "/v1/devices"
    $devices = Get-ArrayProperty -InputObject $doc -Name "devices"
    $online = @($devices | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "presence") -eq "online" })
    if ($online.Count -eq 0) { return $null }
    return $online[0]
}

function Get-DeviceId { param($Device) $id = [string](Get-OptionalProperty -InputObject $Device -Name "device_id"); if (-not $id) { $id = [string](Get-OptionalProperty -InputObject $Device -Name "id") }; return $id }

function Get-VoiceStatus {
    <#  desktop.voice_status through the production command route; $null when it does not settle.  #>
    if (-not $script:deviceId) { return $null }
    $body = @{ capability = $voiceCap; payload = @{}; idempotency_key = ("b47-" + [guid]::NewGuid().ToString("N")); timeout_s = 20 } | ConvertTo-Json -Depth 4 -Compress
    try { $created = Send-Json -Path "/v1/devices/$script:deviceId/commands" -Body $body } catch { return $null }
    $commandId = [string](Get-OptionalProperty -InputObject $created -Name "command_id")
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 700
        $doc = Get-Json "/v1/devices/$script:deviceId/commands/$commandId"
        if ($null -eq $doc) { continue }
        $status = [string](Get-OptionalProperty -InputObject $doc -Name "status")
        if ($status -eq "succeeded") { return (Get-OptionalProperty -InputObject $doc -Name "result") }
        if ($status -in @("failed", "expired", "cancelled")) { return $null }
    }
    return $null
}

function Get-VoiceCounter { param($Status, [string]$Name) if ($null -eq $Status) { return -1 }; $c = Get-OptionalProperty -InputObject $Status -Name "counters"; return [long](Get-OptionalProperty -InputObject $c -Name $Name) }
function Get-Field { param($Status, [string]$Path) $node = $Status; foreach ($p in $Path.Split('.')) { if ($null -eq $node) { return $null }; $node = Get-OptionalProperty -InputObject $node -Name $p }; return $node }

function Watch-VoiceStatus {
    <#  Poll for $Seconds; returns every status seen (the indicator states along the way matter).  #>
    param([int]$Seconds)
    $seen = @()
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($Seconds)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $s = Get-VoiceStatus
        if ($null -ne $s) { $seen += $s }
        Start-Sleep -Seconds $PollSec
    }
    return , $seen
}

function Wait-VoiceCondition {
    param([scriptblock]$Condition, [int]$TimeoutSec = 30)
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSec)
    $last = $null
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $last = Get-VoiceStatus
        if ($null -ne $last -and (& $Condition $last)) { return $last }
        Start-Sleep -Seconds $PollSec
    }
    return $last
}

# --------------------------------------------------------------------- local facts

function Get-CompanionProcess { return @(Get-Process -Name "PagentOS.SessionCompanion" -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq (Get-Process -Id $PID).SessionId }) }

function Resolve-CompanionPaths {
    $proc = @(Get-CompanionProcess)
    $exe = $CompanionExe
    if (-not $exe -and $proc.Count -gt 0) { try { $exe = $proc[0].Path } catch { $exe = "" } }
    $dataDir = $CompanionDataDir
    if (-not $dataDir -and $exe) {
        $settings = Join-Path (Split-Path -Parent $exe) "appsettings.json"
        if (Test-Path -LiteralPath $settings) {
            try { $dataDir = [string]((Get-Content -LiteralPath $settings -Raw | ConvertFrom-Json).DataDir) } catch { $dataDir = "" }
        }
    }
    if (-not $dataDir) { $dataDir = Join-Path $env:LOCALAPPDATA "PagentOS\agent" }
    return [pscustomobject]@{ Exe = $exe; DataDir = $dataDir; Log = (Join-Path $dataDir "logs\companion.log") }
}

function Get-LogLinesSince {
    param([string]$Path, [DateTimeOffset]$Since, [string]$Pattern)
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    $lines = @()
    $share = [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, $share)
    try {
        $reader = New-Object System.IO.StreamReader($stream, (New-Object System.Text.UTF8Encoding($false)))
        while ($null -ne ($line = $reader.ReadLine())) {
            if ($line -notmatch $Pattern) { continue }
            $ts = $null
            try { $ts = [DateTimeOffset]::Parse(([string](($line | ConvertFrom-Json).ts)), [Globalization.CultureInfo]::InvariantCulture) } catch { $ts = $null }
            if ($null -eq $ts -or $ts -ge $Since) { $lines += $line }
        }
    }
    finally { $stream.Dispose() }
    return , $lines
}

function Get-OwnerBrowsers {
    $names = @("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe")
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $names -contains $_.Name })
    # The companion's own Browser Worker runs Chrome with a PagentOS profile; that is not the owner's browser.
    return , @($all | Where-Object { [string]$_.CommandLine -notmatch 'PagentOS' })
}

# ===================================================================== phases

Write-Host "PagentOS B47 - device voice qualification ($runId); phases: $($selected -join ', ')"
$exitCode = 1
try {
    $script:headers = Get-OwnerHeaders
    $paths = Resolve-CompanionPaths
    $evidence.measurements.companion = [ordered]@{ exe = $paths.Exe; data_dir = $paths.DataDir }
    $script:deviceId = $null
    $device = Get-Device
    if ($null -ne $device) { $script:deviceId = Get-DeviceId -Device $device }
    $evidence.device = $script:deviceId

    if ($selected -contains "preflight") {
        Write-Host "`n== preflight"
        Add-Check -Name "P1.cloud_reachable" -Ok (Test-CloudReachable) -Detail $BaseUrl
        Add-Check -Name "P2.companion_running" -Ok (@(Get-CompanionProcess).Count -gt 0) -Detail "exe=$($paths.Exe)"

        $enabled = [Environment]::GetEnvironmentVariable("PAGENTOS_AGENT_VoiceEnabled", "User")
        $cloudUrl = [Environment]::GetEnvironmentVariable("PAGENTOS_AGENT_CloudCoreUrl", "User")
        if ($EnableVoice -and (-not ($enabled -eq "true" -and $cloudUrl))) {
            [Environment]::SetEnvironmentVariable("PAGENTOS_AGENT_VoiceEnabled", "true", "User")
            [Environment]::SetEnvironmentVariable("PAGENTOS_AGENT_CloudCoreUrl", $BaseUrl, "User")
            $enabled = "true"; $cloudUrl = $BaseUrl
            Write-Host "      voice enabled in the user environment; restarting the companion task so it reads it" -ForegroundColor Yellow
            Get-CompanionProcess | ForEach-Object { Stop-Process -Id $_.Id -Force }
            try { Start-ScheduledTask -TaskName $companionTask } catch { Write-Host "      could not start '$companionTask' ($($_.Exception.Message)); sign out and back in" -ForegroundColor Yellow }
            Start-Sleep -Seconds 20
        }
        Add-Check -Name "P3.voice_configured" -Ok ($enabled -eq "true" -and [bool]$cloudUrl) -Detail "VoiceEnabled=$enabled CloudCoreUrl=$cloudUrl$(if (-not ($enabled -eq 'true' -and $cloudUrl)) { ' (rerun with -EnableVoice)' })"

        $device = Get-Device
        $caps = @()
        if ($null -ne $device) { $script:deviceId = Get-DeviceId -Device $device; $caps = @(ConvertTo-Array -Value (Get-OptionalProperty -InputObject $device -Name "capabilities") | ForEach-Object { [string]$_ }) }
        $evidence.device = $script:deviceId
        Add-Check -Name "P4.device_advertises_voice_status" -Ok ($caps -contains $voiceCap) -Detail "device=$script:deviceId capabilities=$($caps.Count)"

        $status = Wait-VoiceCondition -TimeoutSec 60 -Condition { param($s) (Get-Field $s "state") -eq "running" }
        $evidence.measurements.preflight_status = $status
        $ok = $null -ne $status `
            -and (Get-Field $status "state") -eq "running" `
            -and [bool](Get-Field $status "listening") `
            -and [bool](Get-Field $status "microphone.present") `
            -and (Get-Field $status "microphone.capture_process") -eq "session_companion" `
            -and -not [bool](Get-Field $status "privacy.raw_audio_persisted") `
            -and -not [bool](Get-Field $status "privacy.remote_enable_allowed")
        Add-Check -Name "P5.voice_service_running_privately" -Ok $ok -Detail "state=$(Get-Field $status 'state') indicator=$(Get-Field $status 'indicator') mode=$(Get-Field $status 'mode') capturing=$(Get-Field $status 'microphone.capturing') last_error=$(Get-Field $status 'last_error')"

        Start-Sleep -Seconds 12
        $row = Get-Device
        $hbVoice = Get-Field $row "heartbeat_status.voice"
        Add-Check -Name "P6.heartbeat_carries_voice" -Ok ($null -ne $hbVoice -and (Get-Field $hbVoice "state") -eq (Get-Field $status "state")) -Detail "heartbeat voice state=$(Get-Field $hbVoice 'state') restarts=$(Get-Field $hbVoice 'restarts')"
    }

    if ($selected -contains "enroll") {
        Write-Host "`n== enroll (your own voice, $Takes takes per phrase; only MFCC features are kept)"
        $ok = $false; $summary = $null
        if (-not $paths.Exe -or -not (Test-Path -LiteralPath $paths.Exe)) {
            Add-Check -Name "E1.enrollment_ran" -Ok $false -Detail "companion exe not found; pass -CompanionExe"
        }
        else {
            $out = & $paths.Exe --voice-enroll --takes $Takes
            $code = $LASTEXITCODE
            $last = @($out | Where-Object { $_ -match '^\{' } | Select-Object -Last 1)
            if ($last.Count -gt 0) { try { $summary = $last[0] | ConvertFrom-Json } catch { $summary = $null } }
            $evidence.measurements.enrollment = $summary
            Add-Check -Name "E1.enrollment_ran" -Ok ($code -eq 0 -and $null -ne $summary) -Detail "exit=$code"
            $available = @(ConvertTo-Array -Value (Get-OptionalProperty -InputObject $summary -Name "available") | ForEach-Object { [string]$_ })
            $wanted = @("wake", "alarm.snooze", "alarm.stop", "listening.off", "time.tell")
            $missing = @($wanted | Where-Object { $available -notcontains $_ })
            Add-Check -Name "E2.every_phrase_available" -Ok ($missing.Count -eq 0) -Detail "available=[$($available -join ', ')]$(if ($missing.Count) { " MISSING $($missing -join ', ')" })"
            $status = Wait-VoiceCondition -TimeoutSec 45 -Condition { param($s) [bool](Get-Field $s "wake_word.available") }
            $offline = @(ConvertTo-Array -Value (Get-Field $status "offline_commands.available") | ForEach-Object { [string]$_ })
            Add-Check -Name "E3.companion_reloaded_the_templates" -Ok ([bool](Get-Field $status "wake_word.available") -and $offline.Count -ge 4) -Detail "wake_word.available=$(Get-Field $status 'wake_word.available') offline=[$($offline -join ', ')]"
        }
    }

    if ($selected -contains "privacy") {
        Write-Host "`n== privacy (continuous mode)"
        $before = Get-VoiceStatus
        Wait-Owner "30 saniye boyunca KONUSMAYIN (oda sessiz kalsin). Enter'a basinca sayac baslar."
        $seen = Watch-VoiceStatus -Seconds 30
        $after = Get-VoiceStatus
        $dU = (Get-VoiceCounter $after "utterances") - (Get-VoiceCounter $before "utterances")
        $dF = (Get-VoiceCounter $after "frames_admitted") - (Get-VoiceCounter $before "frames_admitted")
        $sending = @($seen | Where-Object { (Get-Field $_ "indicator") -eq "sending" }).Count
        Add-Check -Name "V1.silence_never_leaves" -Ok ($null -ne $before -and $null -ne $after -and $dU -eq 0 -and $dF -eq 0 -and $sending -eq 0) -Detail "utterances +$dU, admitted frames +$dF, 'sending' seen $sending time(s)"

        $before = Get-VoiceStatus
        Wait-Owner "Enter'a basin, sonra normal sesle soyleyin: '$sayProbe'"
        $after = Wait-VoiceCondition -TimeoutSec 30 -Condition { param($s) (Get-VoiceCounter $s "utterances") -gt (Get-VoiceCounter $before "utterances") }
        Start-Sleep -Seconds 4
        $after = Get-VoiceStatus
        $dU = (Get-VoiceCounter $after "utterances") - (Get-VoiceCounter $before "utterances")
        $dF = (Get-VoiceCounter $after "frames_admitted") - (Get-VoiceCounter $before "frames_admitted")
        Add-Check -Name "V2.speech_is_sent" -Ok ($dU -ge 1 -and $dF -gt 0) -Detail "utterances +$dU, admitted frames +$dF, cloud_connected=$(Get-Field $after 'cloud_connected')"

        Wait-Owner "Gorev cubugundaki PagentOS mikrofon simgesine sag tiklayin ve 'Dinleme acik' ogesine tiklayarak dinlemeyi KAPATIN."
        $off = Wait-VoiceCondition -TimeoutSec 20 -Condition { param($s) -not [bool](Get-Field $s "listening") }
        $before = $off
        Wait-Owner "Dinleme kapaliyken Enter'a basin ve yuksek sesle bir cumle soyleyin."
        Start-Sleep -Seconds 8
        $after = Get-VoiceStatus
        $dU = (Get-VoiceCounter $after "utterances") - (Get-VoiceCounter $before "utterances")
        Add-Check -Name "V3a.switch_off_closes_the_microphone" -Ok ($null -ne $off -and (Get-Field $off "indicator") -eq "off" -and -not [bool](Get-Field $off "microphone.capturing") -and $dU -eq 0) -Detail "indicator=$(Get-Field $off 'indicator') capturing=$(Get-Field $off 'microphone.capturing') utterances +$dU"
        Wait-Owner "Ayni menuden dinlemeyi tekrar ACIN."
        $on = Wait-VoiceCondition -TimeoutSec 20 -Condition { param($s) [bool](Get-Field $s "microphone.capturing") }
        Add-Check -Name "V3b.switch_on_reopens_it" -Ok ($null -ne $on -and [bool](Get-Field $on "listening") -and [bool](Get-Field $on "microphone.capturing")) -Detail "indicator=$(Get-Field $on 'indicator')"

        Wait-Owner "Mikrofonu DONANIMDAN susturun (dizustu mikrofon-kapat tusu, kulaklik dugmesi ya da Ses ayarlarinda 'Sesi kapat')."
        $muted = Wait-VoiceCondition -TimeoutSec 20 -Condition { param($s) [bool](Get-Field $s "mic_muted") }
        $before = $muted
        Wait-Owner "Susturulmusken Enter'a basin ve bir cumle soyleyin."
        Start-Sleep -Seconds 8
        $after = Get-VoiceStatus
        $dU = (Get-VoiceCounter $after "utterances") - (Get-VoiceCounter $before "utterances")
        Add-Check -Name "V4a.hardware_mute_is_detected_and_stops_capture" -Ok ($null -ne $muted -and [bool](Get-Field $muted "mic_muted") -and -not [bool](Get-Field $muted "microphone.capturing") -and (Get-Field $muted "indicator") -eq "muted" -and $dU -eq 0) -Detail "mic_muted=$(Get-Field $muted 'mic_muted') capturing=$(Get-Field $muted 'microphone.capturing') indicator=$(Get-Field $muted 'indicator') utterances +$dU"
        Wait-Owner "Susturmayi KALDIRIN."
        $unmuted = Wait-VoiceCondition -TimeoutSec 20 -Condition { param($s) (Get-Field $s "mic_muted") -eq $false -and [bool](Get-Field $s "microphone.capturing") }
        Add-Check -Name "V4b.unmute_reopens_capture" -Ok ($null -ne $unmuted -and [bool](Get-Field $unmuted "microphone.capturing")) -Detail "mic_muted=$(Get-Field $unmuted 'mic_muted')"

        $audioExt = @(".wav", ".pcm", ".raw", ".mp3", ".ogg", ".opus", ".webm", ".flac", ".m4a", ".aac")
        $roots = @($paths.DataDir, $env:TEMP, (Join-Path $env:LOCALAPPDATA "PagentOS")) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique
        $written = @()
        foreach ($root in $roots) {
            $written += @(Get-ChildItem -LiteralPath $root -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTimeUtc -ge $runStart.UtcDateTime -and $audioExt -contains $_.Extension.ToLowerInvariant() } | ForEach-Object { $_.FullName })
        }
        $leaks = @()
        foreach ($word in $probeWords) { $leaks += @(Get-LogLinesSince -Path $paths.Log -Since $runStart -Pattern ([regex]::Escape($word))) }
        $evidence.measurements.audio_files_written = $written
        Add-Check -Name "V5.no_raw_audio_and_no_transcript_on_disk" -Ok ($written.Count -eq 0 -and $leaks.Count -eq 0) -Detail "audio files written: $($written.Count); log lines with the spoken words: $($leaks.Count) ($($roots -join '; '))"
    }

    if ($selected -contains "wake") {
        Write-Host "`n== wake word (optional mode)"
        Wait-Owner "Simgenin menusunden 'Dinleme bicimi' > 'Uyandirma sozcugu' secin."
        $wake = Wait-VoiceCondition -TimeoutSec 20 -Condition { param($s) (Get-Field $s "mode") -eq "wake_word" }
        Add-Check -Name "W1.wake_word_mode_selected" -Ok ($null -ne $wake -and (Get-Field $wake "indicator") -eq "wake_word") -Detail "mode=$(Get-Field $wake 'mode') indicator=$(Get-Field $wake 'indicator')"

        $before = Get-VoiceStatus
        Wait-Owner "Enter'a basin; sonra uyandirma sozcugunu KULLANMADAN 5 farkli cumle soyleyin (aralarda 2 sn), bitince bekleyin."
        Start-Sleep -Seconds 40
        $after = Get-VoiceStatus
        $dU = (Get-VoiceCounter $after "utterances") - (Get-VoiceCounter $before "utterances")
        Add-Check -Name "W2.unaddressed_speech_is_not_sent" -Ok ($dU -eq 0) -Detail "utterances +$dU of 5 unaddressed sentences"

        $before = Get-VoiceStatus
        Wait-Owner "Enter'a basin; sonra 5 kez: uyandirma sozcugu + kisa bir istek (ornek: '<sozcuk> $sayTime'), her seferinde cevabi bekleyin."
        Start-Sleep -Seconds 75
        $after = Get-VoiceStatus
        $dU = (Get-VoiceCounter $after "utterances") - (Get-VoiceCounter $before "utterances")
        Add-Check -Name "W3.addressed_speech_is_sent" -Ok ($dU -ge 4) -Detail "utterances +$dU of 5 addressed requests (>= 4 required)"

        $before = Get-VoiceStatus
        Wait-Owner "Simdi $SoakMinutes dakika boyunca odada normal yasam olsun (TV/muzik/konusma), cihaza SESLENMEYIN. Enter'a basinca sayac baslar."
        Start-Sleep -Seconds ($SoakMinutes * 60)
        $after = Get-VoiceStatus
        $dU = (Get-VoiceCounter $after "utterances") - (Get-VoiceCounter $before "utterances")
        $perHour = [math]::Round($dU * 60.0 / $SoakMinutes, 2)
        $evidence.measurements.false_wakes = [ordered]@{ minutes = $SoakMinutes; count = $dU; per_hour = $perHour }
        Add-Check -Name "W4.false_wakes_per_hour" -Ok ($perHour -le 2) -Detail "$dU false wake(s) in $SoakMinutes min = $perHour/h (<= 2 required)"

        Wait-Owner "Menuden 'Dinleme bicimi' > 'Surekli' secerek varsayilana donun."
        $back = Wait-VoiceCondition -TimeoutSec 20 -Condition { param($s) (Get-Field $s "mode") -eq "continuous" }
        Add-Check -Name "W5.back_to_continuous" -Ok ($null -ne $back) -Detail "mode=$(Get-Field $back 'mode')"
    }

    if ($selected -contains "browserless") {
        Write-Host "`n== browser-independent listening"
        Wait-Owner "TUM tarayici pencerelerini (Chrome/Edge/Firefox) KAPATIN; /core sekmesi de acik olmasin."
        $browsers = Get-OwnerBrowsers
        Add-Check -Name "B1.no_owner_browser_running" -Ok ($browsers.Count -eq 0) -Detail "owner browser processes: $($browsers.Count)"
        $asked = [DateTimeOffset]::UtcNow
        Wait-Owner "Enter'a basin ve cihaza soyleyin: '$sayTime' - cevabi hoparlorden dinleyin."
        $found = $null
        $deadline = [DateTimeOffset]::UtcNow.AddSeconds(90)
        while ([DateTimeOffset]::UtcNow -lt $deadline -and $null -eq $found) {
            Start-Sleep -Seconds 5
            $sessions = Get-ArrayProperty -InputObject (Get-Json "/v1/voice/realtime/sessions?limit=10") -Name "sessions"
            foreach ($session in @($sessions | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "client_kind") -eq "windows_desktop" })) {
                $sid = [string](Get-OptionalProperty -InputObject $session -Name "session_id")
                $activity = Get-Json "/v1/voice/realtime/sessions/$sid/activity"
                $text = if ($null -ne $activity) { $activity | ConvertTo-Json -Depth 20 -Compress } else { "" }
                if ($text -match '"utterance"' -and ($text -match '"first_audio"' -or $text -match '"response_done"')) { $found = $sid; break }
            }
        }
        $evidence.measurements.browserless_session = $found
        Add-Check -Name "B2.device_completed_a_command_without_a_browser" -Ok ($null -ne $found -and $browsers.Count -eq 0) -Detail "windows_desktop session=$found (asked at $($asked.ToString('o')))"
    }

    if ($selected -contains "offline") {
        Write-Host "`n== offline commands and the local snooze"
        $alarmBody = @{ when = @{ relative_seconds = 90 }; test = $true; label = "B47 offline snooze"; snooze_minutes = 2; greeting_enabled = $false } | ConvertTo-Json -Depth 4 -Compress
        $alarm = Send-Json -Path "/v1/alarms" -Body $alarmBody
        $alarmId = [string](Get-OptionalProperty -InputObject $alarm -Name "alarm_id"); if (-not $alarmId) { $alarmId = [string](Get-OptionalProperty -InputObject $alarm -Name "id") }
        $evidence.measurements.alarm_id = $alarmId
        $armed = $null
        $deadline = [DateTimeOffset]::UtcNow.AddSeconds(60)
        while ([DateTimeOffset]::UtcNow -lt $deadline) {
            $row = Get-Json "/v1/alarms/$alarmId"
            if ([string](Get-OptionalProperty -InputObject $row -Name "state") -in @("armed", "firing", "playing", "greeting")) { $armed = $row; break }
            Start-Sleep -Seconds 3
        }
        Add-Check -Name "O1.alarm_armed_on_the_device" -Ok ($null -ne $armed) -Detail "alarm=$alarmId state=$(Get-OptionalProperty -InputObject $armed -Name 'state')"

        $ringing = $null
        $deadline = [DateTimeOffset]::UtcNow.AddSeconds(180)
        while ([DateTimeOffset]::UtcNow -lt $deadline) {
            $row = Get-Json "/v1/alarms/$alarmId"
            if ([string](Get-OptionalProperty -InputObject $row -Name "state") -in @("playing", "greeting")) { $ringing = $row; break }
            Start-Sleep -Seconds 3
        }
        Add-Check -Name "O2.cloud_rang_the_alarm" -Ok ($null -ne $ringing) -Detail "state=$(Get-OptionalProperty -InputObject $ringing -Name 'state')"

        $offlineAt = [DateTimeOffset]::UtcNow
        $statusBefore = Get-VoiceStatus
        Wait-Owner "HEMEN ag baglantisini KESIN (Wi-Fi kapat / kabloyu cek), sonra Enter'a basin."
        $offline = -not (Test-CloudReachable)
        # The realtime leg notices a dead network within its keep-alive bound (~15 s); only then
        # do offline commands act. The tray tooltip says so too.
        Start-Sleep -Seconds 20
        Wait-Owner "Simge ipucunda 'Cloud Core'a bagli degil' yazdigini gorun; alarm calarken Enter'a basin ve '$saySnooze' deyin."
        Start-Sleep -Seconds 8
        $snoozeLines = Get-LogLinesSince -Path $paths.Log -Since $offlineAt -Pattern 'snoozed LOCALLY'
        Add-Check -Name "O3.local_snooze_without_the_cloud" -Ok ($offline -and $snoozeLines.Count -ge 1) -Detail "cloud unreachable=$offline; companion log 'snoozed LOCALLY' lines=$($snoozeLines.Count)"

        Wait-Owner "Hala cevrimdisiyken Enter'a basin ve '$sayTime' deyin (saat bir bildirim olarak gorunmeli)."
        Start-Sleep -Seconds 8
        $timeLines = Get-LogLinesSince -Path $paths.Log -Since $offlineAt -Pattern 'offline command time\.tell executed'
        Add-Check -Name "O4.time_offline" -Ok ($timeLines.Count -ge 1) -Detail "companion log time.tell executed lines=$($timeLines.Count)"

        Wait-Owner "Ag baglantisini GERI ACIN."
        $deadline = [DateTimeOffset]::UtcNow.AddSeconds(120)
        while ([DateTimeOffset]::UtcNow -lt $deadline -and -not (Test-CloudReachable)) { Start-Sleep -Seconds 3 }
        $recorded = $null
        $deadline = [DateTimeOffset]::UtcNow.AddSeconds(90)
        while ([DateTimeOffset]::UtcNow -lt $deadline -and $null -eq $recorded) {
            $events = Get-ArrayProperty -InputObject (Get-Json ("/v1/ledger/events?since=" + [uri]::EscapeDataString($offlineAt.ToString("o")) + "&limit=200")) -Name "events"
            $recorded = @($events | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "event_type") -eq "alarm.local_snoozed" -and [string](Get-OptionalProperty -InputObject $_ -Name "related_module_id") -eq "alarm:$alarmId" }) | Select-Object -First 1
            if ($null -eq $recorded) { Start-Sleep -Seconds 5 }
        }
        $statusAfter = Get-VoiceStatus
        $executed = [long](Get-Field $statusAfter "offline_commands.executed") - [long](Get-Field $statusBefore "offline_commands.executed")
        $row = Get-Json "/v1/alarms/$alarmId"
        $evidence.measurements.alarm_after_reconnect = $row
        Add-Check -Name "O5.cloud_recorded_the_local_snooze" -Ok ($null -ne $recorded -and $executed -ge 2) -Detail "ledger action=$(Get-OptionalProperty -InputObject $recorded -Name 'action') alarm state=$(Get-OptionalProperty -InputObject $row -Name 'state') snooze_count=$(Get-OptionalProperty -InputObject $row -Name 'snooze_count') offline commands executed +$executed"

        Write-Host "      waiting for the snoozed alarm to ring again (2 min + grace)..."
        $rangAgain = $false
        $deadline = [DateTimeOffset]::UtcNow.AddSeconds(240)
        while ([DateTimeOffset]::UtcNow -lt $deadline -and -not $rangAgain) {
            $dev = Get-Device
            $rangAgain = [bool](Get-Field $dev "heartbeat_status.alarm_ringing") -and [string](Get-Field $dev "heartbeat_status.ringing_alarm_id") -eq $alarmId
            if (-not $rangAgain) { Start-Sleep -Seconds 5 }
        }
        Add-Check -Name "O6.the_snoozed_alarm_rang_again" -Ok $rangAgain -Detail "device reported alarm $alarmId ringing after the snooze: $rangAgain"
        Wait-Owner ("Alarmi durdurun: 'Alarm" + $i_dot + " kapat' deyin ya da simge menusunden 'Calan alarmi durdur'.")
    }

    $failed = @($evidence.checks | Where-Object { -not $_.ok })
    $evidence.verdict = if ($evidence.checks.Count -gt 0 -and $failed.Count -eq 0) { "PASS" } else { "FAIL" }
    $exitCode = if ($evidence.verdict -eq "PASS") { 0 } else { 1 }
    Write-Host ""
    Write-Host "VERDICT: $($evidence.verdict) ($($evidence.checks.Count - $failed.Count)/$($evidence.checks.Count) gates)" -ForegroundColor $(if ($exitCode -eq 0) { "Green" } else { "Red" })
}
catch {
    $evidence.error = $_.Exception.Message
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    $evidence.finished_at = [DateTimeOffset]::UtcNow.ToString("o")
    Save-Evidence
}
exit $exitCode
