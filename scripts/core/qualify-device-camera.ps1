<#
.SYNOPSIS
    B48 OWNER_REQUIRED: the physical evaluation of the device camera - a real presence check,
    the tray indicator, the owner's switches, the privacy switch, no stored frame, and (with
    -SleepTrial) a resting posture, LIKELY_ASLEEP and one real "Uyurken ekranı kapat".

.DESCRIPTION
    Every automated test of the camera path uses fake frames; no test opens the owner's camera.
    This script is the other half: it drives the REAL device through the REAL production route
    (PUT /v1/ambient/policy, POST /v1/presence/eye/*, GET /v1/devices/<id>/status,
    POST /v1/devices/<id>/commands) and asks the owner, in Turkish, for the few judgements only
    a person can make (is the tray icon there, is the camera light on, did the monitors go dark).

    Gates (each PASS / FAIL / SKIPPED, all recorded in the evidence file):

      G1  device_camera_path      the device's heartbeat carries a camera block (the companion
                                  has the B48 camera path) and advertises desktop.camera_mode
      G2  periodic_check          camera_mode=periodic -> the device reports periodic, a check
                                  time, and a DERIVED observation (seven fields, source camera)
      G3  indicator_armed         owner: the PagentOS tray icon is visible while a mode is on
      G4  continuous_capture      camera_mode=continuous -> state capturing, indicator open;
                                  owner: the camera light and the "AÇIK" tray icon are on;
                                  the observation says person_present=true while the owner sits
      G5  absence_seen            owner leaves the frame -> person_present=false; returns -> true
      G6  off_closes              camera_mode=off -> state off, indicator hidden, no observation;
                                  owner: the light and the icon are gone
      G7  eye_disable_closes      "Kamerayı kapat" (eye disable) closes the device camera; eye
                                  enable restores the owner's mode
      G8  tray_veto               owner closes the camera from the tray menu -> state vetoed and
                                  the light is off; re-allowing restores capture
      G9  privacy_switch          (-IncludePrivacyCheck) Windows' camera switch off -> state
                                  blocked with the switch named, nothing opened; back on -> capture
      G10 no_raw_frames           no image/video file appeared under the agent's folders or TEMP
                                  during the run, and the companion log holds no base64 blob
      G11 sleep_trial             (-SleepTrial) the owner sits still in front of the camera, no
                                  input, no sound: RESTING (row 307), then LIKELY_ASLEEP (row
                                  308), then a real display.off with reason owner_likely_asleep
                                  (row 333) - honouring the owner's quiet hours
      G12 monitors_dark           (-SleepTrial) owner: every monitor went dark; plus each
                                  monitor's own DDC/CI power reading (row 320)

    The owner's ambient policy and eye state are recorded first and RESTORED at the end, on
    every path. The camera mode is left "off" unless the owner started with another mode.

    -DryRun prints and records every gate and request without contacting anything or prompting.

    Evidence: docs\evidence\b48-device-camera-<timestamp>.json (or -OutFile).

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\core\qualify-device-camera.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\core\qualify-device-camera.ps1 -IncludePrivacyCheck -SleepTrial

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\core\qualify-device-camera.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    [string]$BaseUrl = "",
    [string]$Device = "",
    [string]$OutFile = "",
    [switch]$DryRun,
    [switch]$IncludePrivacyCheck,
    [switch]$SleepTrial,
    [ValidateRange(20, 180)][int]$SleepTrialMinutes = 75,
    [ValidateRange(1, 30)][int]$PollSec = 3
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$PSNativeCommandUseErrorActionPreference = $false

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
. (Join-Path $repoRoot "scripts\lib\OwnerHarness.ps1")

if (-not $BaseUrl) { $BaseUrl = "http://${BrokerHost}:$ApiPort" }
$BaseUrl = $BaseUrl.TrimEnd('/')
$runId = "b48-device-camera-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$runStart = Get-Date
if (-not $OutFile) { $OutFile = Join-Path $repoRoot "docs\evidence\$runId.json" }

$script:Dry = [bool]$DryRun
$script:Headers = @{}
$script:DeviceId = ""
$script:Step = 0
$script:Gates = [ordered]@{
    G1_device_camera_path  = "the device heartbeat carries a camera block and advertises desktop.camera_mode"
    G2_periodic_check      = "periodic mode produces a derived seven-field observation"
    G3_indicator_armed     = "owner: tray icon visible while a mode is on"
    G4_continuous_capture  = "continuous mode: capturing, indicator open, light on, person present"
    G5_absence_seen        = "owner leaves the frame: person_present false; returns: true"
    G6_off_closes          = "off: state off, indicator hidden, no observation, light off"
    G7_eye_disable_closes  = "eye disable closes the device camera; enable restores the mode"
    G8_tray_veto           = "tray menu veto closes the camera and outranks the cloud mode"
    G9_privacy_switch      = "Windows camera switch off is reported as blocked, nothing opened"
    G10_no_raw_frames      = "no image or video file and no base64 blob appeared during the run"
    G11_sleep_trial        = "RESTING, LIKELY_ASLEEP and one real sleep display-off from device signals"
    G12_monitors_dark      = "every monitor went dark (owner) and each monitor's DDC power reading"
}
$evidence = [ordered]@{
    run_id     = $runId
    started_at = $runStart.ToUniversalTime().ToString("o")
    mode       = $(if ($script:Dry) { "dry-run" } else { "live" })
    cloud      = $BaseUrl
    device     = $null
    restored   = $null
    gates      = [ordered]@{}
    plan       = @()
    timeline   = @()
    verdict    = "NOT_RUN"
}

function Save-Evidence {
    $dir = Split-Path -Parent $OutFile
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $json = ConvertTo-Json -InputObject $evidence -Depth 12
    [System.IO.File]::WriteAllText($OutFile, $json, (New-Object System.Text.UTF8Encoding($false)))
}

function Set-Gate {
    param([string]$Gate, [ValidateSet("PASS", "FAIL", "SKIPPED", "PLANNED")][string]$Verdict, [string]$Detail = "")
    $evidence.gates[$Gate] = [ordered]@{ verdict = $Verdict; detail = $Detail; what = $script:Gates[$Gate] }
    $color = switch ($Verdict) { "PASS" { "Green" } "FAIL" { "Red" } default { "Yellow" } }
    Write-Host ("  [{0,-7}] {1}: {2}" -f $Verdict, $Gate, $Detail) -ForegroundColor $color
}

function Add-Timeline {
    param([string]$What, [string]$Detail = "")
    $evidence.timeline += [ordered]@{ at = (Get-Date).ToUniversalTime().ToString("o"); what = $What; detail = $Detail }
    Write-Host "      $What $Detail"
}

function Invoke-Api {
    param([string]$Method = "GET", [string]$Path, $Body = $null)
    $script:Step++
    $json = if ($null -ne $Body) { ConvertTo-Json -InputObject $Body -Depth 8 -Compress } else { $null }
    if ($script:Dry) {
        $evidence.plan += [ordered]@{ step = $script:Step; method = $Method; path = $Path; body = $Body }
        Write-Host ("  [plan] {0,-5} {1} {2}" -f $Method, $Path, $json) -ForegroundColor DarkGray
        return $null
    }
    return Invoke-JsonUtf8 -Method $Method -Uri "$BaseUrl$Path" -Headers $script:Headers -Body $json -TimeoutSec 30
}

function Ask-Owner {
    <#  A yes/no judgement only a person can make. Never asked in a dry run.  #>
    param([string]$Question)
    if ($script:Dry) {
        $evidence.plan += [ordered]@{ step = (++$script:Step); ask_owner = $Question }
        Write-Host "  [plan] ask the owner: $Question" -ForegroundColor DarkGray
        return $true
    }
    while ($true) {
        $answer = Read-Host "  $Question (E/H)"
        if ($answer -match '^\s*[eEyY]') { return $true }
        if ($answer -match '^\s*[hHnN]') { return $false }
    }
}

function Wait-Owner {
    param([string]$Instruction)
    if ($script:Dry) {
        $evidence.plan += [ordered]@{ step = (++$script:Step); instruct_owner = $Instruction }
        Write-Host "  [plan] tell the owner: $Instruction" -ForegroundColor DarkGray
        return
    }
    [void](Read-Host "  $Instruction  [Enter]")
}

function Get-Camera {
    <#  The device's camera block and derived observation from its latest heartbeat.  #>
    $doc = Invoke-Api -Path "/v1/devices/$script:DeviceId/status"
    $status = Get-OptionalProperty -InputObject $doc -Name "status"
    return [pscustomobject]@{
        Camera      = Get-OptionalProperty -InputObject $status -Name "camera"
        Observation = Get-OptionalProperty -InputObject $status -Name "camera_observation"
        Display     = [string](Get-OptionalProperty -InputObject $status -Name "display_state")
    }
}

function Wait-Camera {
    <#  Poll the heartbeat until the predicate holds or the time is up. Returns the last read.  #>
    param([scriptblock]$Until, [int]$TimeoutSec, [string]$What)
    if ($script:Dry) {
        # The read the wait polls is planned too, so a dry run exercises the whole read path.
        $read = Get-Camera
        $evidence.plan += [ordered]@{ step = (++$script:Step); wait_for = $What; timeout_s = $TimeoutSec }
        Write-Host "  [plan] wait up to $TimeoutSec s for $What" -ForegroundColor DarkGray
        return [pscustomobject]@{ Ok = $true; Read = $read }
    }
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $read = $null
    do {
        $read = Get-Camera
        if (& $Until $read) { return [pscustomobject]@{ Ok = $true; Read = $read } }
        Start-Sleep -Seconds $PollSec
    } while ((Get-Date) -lt $deadline)
    return [pscustomobject]@{ Ok = $false; Read = $read }
}

function Get-CameraField {
    param($Read, [string]$Name)
    if ($null -eq $Read) { return $null }
    return Get-OptionalProperty -InputObject $Read.Camera -Name $Name
}

function Get-ObservationField {
    param($Read, [string]$Name)
    if ($null -eq $Read) { return $null }
    return Get-OptionalProperty -InputObject $Read.Observation -Name $Name
}

function Set-CameraMode {
    param([string]$Mode)
    [void](Invoke-Api -Method PUT -Path "/v1/ambient/policy" -Body @{ camera_mode = $Mode })
    Add-Timeline "camera_mode" $Mode
}

function Invoke-DeviceCommand {
    <#  One capability through POST /v1/devices/<id>/commands, polled to its terminal state.  #>
    param([string]$Capability, [hashtable]$Payload)
    $created = Invoke-Api -Method POST -Path "/v1/devices/$script:DeviceId/commands" -Body @{
        capability = $Capability; payload = $Payload; idempotency_key = "${runId}:$($script:Step):$Capability"; timeout_s = 60
    }
    if ($script:Dry) { return $null }
    $commandId = [string](Get-OptionalProperty -InputObject $created -Name "command_id")
    $deadline = (Get-Date).AddSeconds(80)
    do {
        Start-Sleep -Milliseconds 500
        $row = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/devices/$script:DeviceId/commands/$commandId" -Headers $script:Headers -TimeoutSec 30
    } while ((Get-Date) -lt $deadline -and ([string](Get-OptionalProperty -InputObject $row -Name "status")) -notin @("succeeded", "failed", "expired", "cancelled"))
    return $row
}

$original = $null
$originalEye = $null
$exitCode = 1
try {
    Write-Host "PagentOS B48 device camera qualification ($runId)$(if ($script:Dry) { ' - DRY RUN, nothing is sent' })"

    # ------------------------------------------------------------------ the owner session
    if (-not $script:Dry) {
        $credential = $null
        try { $credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL" } catch { $credential = $null }
        if (-not $credential) {
            $secure = Read-Host -Prompt "Cloud Owner Credential (gizli; bir oturuma çevrilip atılır)" -AsSecureString
            $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
            try { $credential = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
            finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        }
        try {
            $body = @{ owner_credential = $credential; client_kind = "cli"; label = $runId } | ConvertTo-Json -Compress
            $issued = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body $body -TimeoutSec 30
        }
        finally { $credential = $null; $body = $null }
        $script:Headers = @{ Authorization = "Bearer $([string]$issued.token)" }
    }

    # ------------------------------------------------------------------ G1 device path
    $listing = Invoke-Api -Path "/v1/devices"
    if (-not $script:Dry) {
        $chosen = $null
        foreach ($row in (Get-ArrayProperty -InputObject $listing -Name "devices")) {
            $caps = Get-ArrayProperty -InputObject $row -Name "capabilities"
            $isMatch = if ($Device) { ([string]$row.device_id -eq $Device -or [string]$row.name -eq $Device) } else { ([string]$row.presence -eq "online" -and $caps -contains "desktop.camera_mode") }
            if ($isMatch) { $chosen = $row; break }
        }
        if ($null -eq $chosen) { throw "no online device advertises desktop.camera_mode; install the B48 agent first" }
        $script:DeviceId = [string]$chosen.device_id
        $evidence.device = [ordered]@{ device_id = $script:DeviceId; name = [string]$chosen.name; software_version = [string](Get-OptionalProperty -InputObject $chosen -Name "software_version") }
        Write-Host "  device: $($chosen.name) ($script:DeviceId)"
    }
    else {
        $script:DeviceId = "<device>"
    }

    $policyDoc = Invoke-Api -Path "/v1/ambient/policy"
    $stateDoc = Invoke-Api -Path "/v1/presence/state"
    if (-not $script:Dry) {
        $p = Get-OptionalProperty -InputObject $policyDoc -Name "policy"
        $original = [ordered]@{
            camera_mode       = [string](Get-OptionalProperty -InputObject $p -Name "camera_mode")
            auto_off_enabled  = [bool](Get-OptionalProperty -InputObject $p -Name "auto_off_enabled")
            off_when_asleep   = [bool](Get-OptionalProperty -InputObject $p -Name "off_when_asleep")
            asleep_after_s    = [int](Get-OptionalProperty -InputObject $p -Name "asleep_after_s")
        }
        $originalEye = [bool](Get-OptionalProperty -InputObject $stateDoc -Name "eye_enabled")
        if (-not $original.camera_mode) { throw "this Cloud Core does not report camera_mode; deploy the B48 Cloud Core first" }
        if (-not $originalEye) { [void](Invoke-Api -Method POST -Path "/v1/presence/eye/enable" -Body @{ reason = "b48_qualification" }) }
    }

    $first = Get-Camera
    if ($script:Dry) { Set-Gate "G1_device_camera_path" "PLANNED" }
    elseif ($null -ne $first.Camera) { Set-Gate "G1_device_camera_path" "PASS" "mode=$(Get-CameraField $first 'mode') state=$(Get-CameraField $first 'state')" }
    else { Set-Gate "G1_device_camera_path" "FAIL" "the heartbeat carries no camera block" ; throw "no camera path on the device; the remaining gates cannot run" }

    # ------------------------------------------------------------------ G2 / G3 periodic
    Set-CameraMode "periodic"
    $periodic = Wait-Camera -TimeoutSec 150 -What "periodic mode with a derived observation" -Until {
        param($r) (Get-CameraField $r "mode") -eq "periodic" -and (Get-CameraField $r "last_check_at") -and $null -ne $r.Observation
    }
    if ($script:Dry) { Set-Gate "G2_periodic_check" "PLANNED" }
    elseif ($periodic.Ok -and (Get-ObservationField $periodic.Read "source") -eq "camera") {
        Set-Gate "G2_periodic_check" "PASS" ("present={0} posture={1} activity={2} confidence={3}" -f (Get-ObservationField $periodic.Read "person_present"), (Get-ObservationField $periodic.Read "posture"), (Get-ObservationField $periodic.Read "activity_level"), (Get-ObservationField $periodic.Read "presence_confidence"))
    }
    else { Set-Gate "G2_periodic_check" "FAIL" "state=$(Get-CameraField $periodic.Read 'state') error=$(Get-CameraField $periodic.Read 'error')" }
    $armed = Ask-Owner "Sistem tepsisinde PagentOS kamera simgesi (kalkan) görünüyor mu?"
    Set-Gate "G3_indicator_armed" $(if ($script:Dry) { "PLANNED" } elseif ($armed) { "PASS" } else { "FAIL" }) "owner judgement"

    # ------------------------------------------------------------------ G4 continuous
    Wait-Owner "Kameranın karşısına oturun; sürekli izleme açılacak."
    Set-CameraMode "continuous"
    $continuous = Wait-Camera -TimeoutSec 60 -What "capturing with the indicator open and the owner present" -Until {
        param($r) (Get-CameraField $r "state") -eq "capturing" -and (Get-CameraField $r "indicator") -eq "open" -and (Get-ObservationField $r "person_present") -eq $true
    }
    $light = Ask-Owner "Kamera ışığı yanıyor ve tepsi simgesi 'PagentOS kamerası AÇIK' diyor mu?"
    if ($script:Dry) { Set-Gate "G4_continuous_capture" "PLANNED" }
    else { Set-Gate "G4_continuous_capture" $(if ($continuous.Ok -and $light) { "PASS" } else { "FAIL" }) "machine=$($continuous.Ok) owner=$light" }

    # ------------------------------------------------------------------ G5 absence
    Wait-Owner "Kameranın görüş alanından çıkın (en az 30 saniye), sonra Enter'a basmadan önce dönmeyin - çıkınca Enter."
    $away = Wait-Camera -TimeoutSec 90 -What "person_present=false" -Until { param($r) (Get-ObservationField $r "person_present") -eq $false }
    Wait-Owner "Şimdi kameranın karşısına geri dönün."
    $back = Wait-Camera -TimeoutSec 60 -What "person_present=true" -Until { param($r) (Get-ObservationField $r "person_present") -eq $true }
    if ($script:Dry) { Set-Gate "G5_absence_seen" "PLANNED" }
    else { Set-Gate "G5_absence_seen" $(if ($away.Ok -and $back.Ok) { "PASS" } else { "FAIL" }) "away_seen=$($away.Ok) return_seen=$($back.Ok)" }

    # ------------------------------------------------------------------ G6 off
    Set-CameraMode "off"
    $off = Wait-Camera -TimeoutSec 45 -What "state off, indicator hidden, no observation" -Until {
        param($r) (Get-CameraField $r "state") -eq "off" -and (Get-CameraField $r "indicator") -eq "hidden" -and $null -eq $r.Observation
    }
    $dark = Ask-Owner "Kamera ışığı söndü ve tepsi simgesi kayboldu mu?"
    if ($script:Dry) { Set-Gate "G6_off_closes" "PLANNED" }
    else { Set-Gate "G6_off_closes" $(if ($off.Ok -and $dark) { "PASS" } else { "FAIL" }) "machine=$($off.Ok) owner=$dark" }

    # ------------------------------------------------------------------ G7 eye disable
    Set-CameraMode "continuous"
    $open = Wait-Camera -TimeoutSec 60 -What "capturing" -Until { param($r) (Get-CameraField $r "state") -eq "capturing" }
    [void](Invoke-Api -Method POST -Path "/v1/presence/eye/disable" -Body @{ reason = "b48_qualification" })
    Add-Timeline "eye" "disabled"
    $closed = Wait-Camera -TimeoutSec 45 -What "mode off after the eye was disabled" -Until { param($r) (Get-CameraField $r "mode") -eq "off" -and (Get-CameraField $r "state") -eq "off" }
    [void](Invoke-Api -Method POST -Path "/v1/presence/eye/enable" -Body @{ reason = "b48_qualification" })
    Add-Timeline "eye" "enabled"
    $restored = Wait-Camera -TimeoutSec 45 -What "continuous again after the eye was enabled" -Until { param($r) (Get-CameraField $r "mode") -eq "continuous" }
    if ($script:Dry) { Set-Gate "G7_eye_disable_closes" "PLANNED" }
    else { Set-Gate "G7_eye_disable_closes" $(if ($open.Ok -and $closed.Ok -and $restored.Ok) { "PASS" } else { "FAIL" }) "opened=$($open.Ok) closed=$($closed.Ok) restored=$($restored.Ok)" }

    # ------------------------------------------------------------------ G8 tray veto
    Wait-Owner "Tepsideki PagentOS kamera simgesine sağ tıklayıp 'Kamerayı bu cihazda kapat'ı seçin."
    $vetoed = Wait-Camera -TimeoutSec 45 -What "state vetoed" -Until { param($r) (Get-CameraField $r "state") -eq "vetoed" }
    $vetoLight = Ask-Owner "Kamera ışığı söndü mü?"
    Wait-Owner "Aynı menüden 'Kameraya yeniden izin ver'i seçin."
    $unvetoed = Wait-Camera -TimeoutSec 45 -What "capturing again" -Until { param($r) (Get-CameraField $r "state") -eq "capturing" }
    if ($script:Dry) { Set-Gate "G8_tray_veto" "PLANNED" }
    else { Set-Gate "G8_tray_veto" $(if ($vetoed.Ok -and $vetoLight -and $unvetoed.Ok) { "PASS" } else { "FAIL" }) "vetoed=$($vetoed.Ok) light_off=$vetoLight restored=$($unvetoed.Ok)" }

    # ------------------------------------------------------------------ G9 privacy switch
    if ($IncludePrivacyCheck) {
        Wait-Owner "Ayarlar > Gizlilik > Kamera: 'Masaüstü uygulamalarının kameraya erişmesine izin ver'i KAPATIN."
        $blocked = Wait-Camera -TimeoutSec 60 -What "state blocked with the switch named" -Until {
            param($r) (Get-CameraField $r "state") -eq "blocked" -and ([string](Get-CameraField $r "error")) -like "privacy_*"
        }
        Wait-Owner "Aynı anahtarı yeniden AÇIN."
        $allowed = Wait-Camera -TimeoutSec 60 -What "capturing again" -Until { param($r) (Get-CameraField $r "state") -eq "capturing" }
        if ($script:Dry) { Set-Gate "G9_privacy_switch" "PLANNED" }
        else { Set-Gate "G9_privacy_switch" $(if ($blocked.Ok -and $allowed.Ok) { "PASS" } else { "FAIL" }) "blocked=$($blocked.Ok) error=$(Get-CameraField $blocked.Read 'error') restored=$($allowed.Ok)" }
    }
    else { Set-Gate "G9_privacy_switch" "SKIPPED" "run with -IncludePrivacyCheck" }

    # ------------------------------------------------------------------ G11 / G12 sleep
    if ($SleepTrial) {
        [void](Invoke-Api -Method PUT -Path "/v1/ambient/policy" -Body @{ auto_off_enabled = $true; off_when_asleep = $true; asleep_after_s = 60; camera_mode = "continuous" })
        $explainBefore = Invoke-Api -Path "/v1/ambient/explain"
        $quiet = [string](Get-OptionalProperty -InputObject $explainBefore -Name "quiet_hours")
        Add-Timeline "sleep_trial" "quiet_hours=$quiet (inside: ~27 min; outside: ~66 min)"
        Wait-Owner "Işık açık, kameraya dönük, hareketsiz oturun; klavye/fareye dokunmayın, ses çalmasın. Enter'dan sonra başlar."
        $seen = @{ resting = $false; likely_asleep = $false; off = $false }
        $deadline = (Get-Date).AddMinutes($SleepTrialMinutes)
        $offRead = $null
        while (-not $script:Dry -and (Get-Date) -lt $deadline -and -not $seen.off) {
            Start-Sleep -Seconds 15
            $state = Invoke-Api -Path "/v1/presence/state"
            $assertion = Get-OptionalProperty -InputObject $state -Name "assertion"
            $now = [string](Get-OptionalProperty -InputObject $assertion -Name "state")
            if ($now -eq "resting" -and -not $seen.resting) { $seen.resting = $true; Add-Timeline "presence" "resting (sources $((Get-ArrayProperty -InputObject $assertion -Name 'sources') -join ','))" }
            if ($now -eq "likely_asleep" -and -not $seen.likely_asleep) { $seen.likely_asleep = $true; Add-Timeline "presence" "likely_asleep" }
            $read = Get-Camera
            if ($read.Display -eq "off") { $seen.off = $true; $offRead = $read; Add-Timeline "display" "off" }
        }
        if ($script:Dry) {
            Set-Gate "G11_sleep_trial" "PLANNED"
            [void](Invoke-DeviceCommand -Capability "desktop.display_status" -Payload @{ probe_power = $true })
            [void](Invoke-Api -Path "/v1/ambient/explain")
            [void](Ask-Owner "(Bir tuşa basmadan önce) TÜM monitörler karardı mı?")
            Wait-Owner "Şimdi bir tuşa basıp ekranları uyandırın."
            Set-Gate "G12_monitors_dark" "PLANNED"
        }
        else {
            $powerRow = Invoke-DeviceCommand -Capability "desktop.display_status" -Payload @{ probe_power = $true }
            $explain = Invoke-Api -Path "/v1/ambient/explain"
            $last = Get-OptionalProperty -InputObject $explain -Name "last_display_action"
            $reason = [string](Get-OptionalProperty -InputObject $last -Name "reason")
            $ok11 = $seen.resting -and $seen.likely_asleep -and $seen.off -and $reason -eq "owner_likely_asleep"
            Set-Gate "G11_sleep_trial" $(if ($ok11) { "PASS" } else { "FAIL" }) "resting=$($seen.resting) likely_asleep=$($seen.likely_asleep) display_off=$($seen.off) reason=$reason quiet_hours=$quiet"
            $result = Get-OptionalProperty -InputObject $powerRow -Name "result"
            $poweredOn = Get-OptionalProperty -InputObject $result -Name "monitors_powered_on"
            $unknown = Get-OptionalProperty -InputObject $result -Name "monitors_power_unknown"
            $allDark = Ask-Owner "(Bir tuşa basmadan önce) TÜM monitörler karardı mı?"
            Wait-Owner "Şimdi bir tuşa basıp ekranları uyandırın."
            Set-Gate "G12_monitors_dark" $(if ($allDark -and $seen.off) { "PASS" } else { "FAIL" }) "owner=$allDark ddc_powered_on=$poweredOn ddc_unknown=$unknown"
        }
    }
    else {
        Set-Gate "G11_sleep_trial" "SKIPPED" "run with -SleepTrial (about 30-70 minutes of sitting still)"
        Set-Gate "G12_monitors_dark" "SKIPPED" "part of -SleepTrial"
    }

    # ------------------------------------------------------------------ G10 no frames
    if ($script:Dry) { Set-Gate "G10_no_raw_frames" "PLANNED" }
    else {
        Set-CameraMode "off"
        $roots = @((Join-Path $env:LOCALAPPDATA "PagentOS"), $env:TEMP, (Join-Path $env:ProgramData "PagentOS")) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
        $pictures = @(Get-ChildItem -LiteralPath $roots -Recurse -Depth 4 -File -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -ge $runStart -and $_.Extension -match '^\.(jpe?g|png|bmp|gif|tiff?|webp|heic|raw|yuv|nv12|mp4|avi|wmv|mkv|mov)$' })
        $blobLines = 0
        $log = Join-Path $env:LOCALAPPDATA "PagentOS\agent\logs\companion.log"
        if (Test-Path -LiteralPath $log) {
            $blobLines = @(Select-String -LiteralPath $log -Pattern '[A-Za-z0-9+/=]{200,}' -ErrorAction SilentlyContinue).Count
        }
        Set-Gate "G10_no_raw_frames" $(if ($pictures.Count -eq 0 -and $blobLines -eq 0) { "PASS" } else { "FAIL" }) "image_files=$($pictures.Count) base64_log_lines=$blobLines ($($pictures | Select-Object -First 3 | ForEach-Object { $_.FullName }))"
    }

    $failed = @($evidence.gates.Values | Where-Object { $_.verdict -eq "FAIL" }).Count
    if ($script:Dry) { $evidence.verdict = "PLANNED"; $exitCode = 0 }
    elseif ($failed -eq 0) { $evidence.verdict = "PASS"; $exitCode = 0 }
    else { $evidence.verdict = "FAIL" }
}
catch {
    $evidence.verdict = "FAIL"
    $evidence.error = $_.Exception.Message
    Write-Host "  FAILED: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    if (-not $script:Dry -and $null -ne $original) {
        try {
            [void](Invoke-JsonUtf8 -Method PUT -Uri "$BaseUrl/v1/ambient/policy" -Headers $script:Headers -TimeoutSec 30 -Body (ConvertTo-Json -Compress -InputObject @{
                        camera_mode = $original.camera_mode; auto_off_enabled = $original.auto_off_enabled
                        off_when_asleep = $original.off_when_asleep; asleep_after_s = $original.asleep_after_s
                    }))
            if (-not $originalEye) {
                [void](Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/presence/eye/disable" -Headers $script:Headers -TimeoutSec 30 -Body '{"reason":"b48_qualification_restore"}')
            }
            $evidence.restored = [ordered]@{ policy = $original; eye_enabled = $originalEye }
        }
        catch { $evidence.restored = "FAILED: $($_.Exception.Message)" }
    }
    $evidence.finished_at = (Get-Date).ToUniversalTime().ToString("o")
    Save-Evidence
    Write-Host ""
    Write-Host "VERDICT: $($evidence.verdict)   evidence: $OutFile"
}
exit $exitCode
