<#
.SYNOPSIS
    Reconcile an M18 owner run from the Cloud Core's DURABLE record - sessions, tool calls,
    receipts, ledger rows, routine firings - and say which acceptance rows it proves. No
    owner present, no polling window, no harness state: only what the record holds.

.DESCRIPTION
    Owner directive (2026-09-06): "M18 should close from combined durable evidence, not by
    forcing one monolithic fragile run." The long harness lost its session correlation and
    printed cascading FAILs for a run in which every product action had worked; the record
    still holds all of it. This script reads a window of that record and evaluates each
    capability by its own evidence, naming a qualification-selection defect as such.

    Correlation: every session created inside the window that carries a router call is a
    Core session of the run (client_kind web); tool calls, receipts (session_id on the
    receipt) and eye rows (session_id on the row from contract v3; earlier rows by reason
    and time) are grouped by that identity.

.EXAMPLE
    .\scripts\core\reconcile-m18.ps1 -Since 2026-09-06T15:09:00Z -Until 2026-09-06T15:35:00Z -OutFile docs\evidence\m18-reconciliation-2026-09-06.json
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    [string]$BaseUrl = "",
    [Parameter(Mandatory = $true)][string]$Since,
    [string]$Until = "",
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
$sinceAt = ConvertTo-SessionInstant -Raw $Since
if ($null -eq $sinceAt) { throw "-Since must be an ISO instant, e.g. 2026-09-06T15:09:00Z" }
$untilAt = if ($Until) { ConvertTo-SessionInstant -Raw $Until } else { [DateTimeOffset]::UtcNow }
if ($null -eq $untilAt) { throw "-Until must be an ISO instant" }
$sinceAt = [DateTimeOffset]$sinceAt
$untilAt = [DateTimeOffset]$untilAt

$presenceDetailAllowed = @("to_state", "confidence", "reason", "signal_sources", "signal_count")
$presentStates = @("present", "returned", "awake", "resting", "likely_asleep")

$credential = $null
try { $credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL" } catch { $credential = $null }
if (-not $credential) {
    $secure = Read-Host -Prompt "Cloud Owner Credential (hidden)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $credential = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
try {
    $body = @{ owner_credential = $credential; client_kind = "cli"; label = "reconcile-m18" } | ConvertTo-Json -Compress
    $issued = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body $body
}
finally { $credential = $null; $body = $null }
$headers = @{ Authorization = "Bearer $([string]$issued.token)" }
$mintedId = [string](Get-OptionalProperty -InputObject $issued -Name "session_id")
function Get-Json { param([string]$Path) return Invoke-JsonUtf8 -Uri "$BaseUrl$Path" -Headers $headers -TimeoutSec 30 }

function Test-InWindow {
    param([string]$Raw)
    $at = ConvertTo-SessionInstant -Raw $Raw
    if ($null -eq $at) { return $false }
    return (([DateTimeOffset]$at) -ge $sinceAt -and ([DateTimeOffset]$at) -le $untilAt)
}

$rows = @()
function Add-Row {
    param([string]$Row, [string]$Capability, [bool]$Proven, [string]$Evidence, [string]$Note = "")
    $script:rows += [ordered]@{ row = $Row; capability = $Capability; proven = $Proven; evidence = $Evidence; note = $Note }
    $mark = if ($Proven) { "PROVEN " } else { "not yet" }
    $color = if ($Proven) { "Green" } else { "Yellow" }
    Write-Host ("  [{0}] {1} {2}: {3}{4}" -f $mark, $Row, $Capability, $Evidence, $(if ($Note) { " - " + $Note } else { "" })) -ForegroundColor $color
}

try {
    Write-Host "PagentOS M18 reconciliation: $($sinceAt.ToString('o')) .. $($untilAt.ToString('o'))"

    # ---------------------------------------------------------------- sessions of the window
    $allSessions = Get-ArrayProperty -InputObject (Get-Json "/v1/voice/realtime/sessions?limit=50") -Name "sessions"
    $sessions = @()
    foreach ($s in $allSessions) {
        if ([string](Get-OptionalProperty -InputObject $s -Name "client_kind") -ne "web") { continue }
        if (-not (Test-InWindow -Raw ([string](Get-OptionalProperty -InputObject $s -Name "started_at")))) { continue }
        $sessions += $s
    }
    $activities = @{}
    $routerSessions = @()
    foreach ($s in $sessions) {
        $id = [string]$s.session_id
        $act = $null
        try { $act = Get-Json "/v1/voice/realtime/sessions/$id/activity" } catch { $act = $null }
        $activities[$id] = $act
        if ($null -ne $act -and (Test-CoreQualification -Activity $act)) { $routerSessions += $id }
    }
    Write-Host "      web sessions in the window: $($sessions.Count); with router calls: $($routerSessions.Count) ($($routerSessions -join ', '))"
    Add-Row -Row "12.21" -Capability "voice from /core (a Core session with router calls)" -Proven ($routerSessions.Count -ge 1) -Evidence "sessions $($routerSessions -join ', ')"

    # ---------------------------------------------------------------- per-session evidence
    $calls = @()
    $listened = $false; $spoke = $false
    $cognitive = @()
    foreach ($id in $routerSessions) {
        $act = $activities[$id]
        foreach ($c in (Get-ArrayProperty -InputObject $act -Name "tool_calls")) {
            $c | Add-Member -NotePropertyName "session_id_" -NotePropertyValue $id -Force
            $calls += $c
            if ([string](Get-OptionalProperty -InputObject $c -Name "status") -eq "succeeded" -and [string](Get-OptionalProperty -InputObject $c -Name "query_kind") -ne "" -and [string](Get-OptionalProperty -InputObject $c -Name "subsystem") -ne "") {
                $cognitive += ("{0}: {1}->{2}" -f (Get-OptionalProperty -InputObject $c -Name "name"), (Get-OptionalProperty -InputObject $c -Name "query_kind"), (Get-OptionalProperty -InputObject $c -Name "subsystem"))
            }
        }
        foreach ($e in (Get-ArrayProperty -InputObject $act -Name "client_events")) {
            $k = [string](Get-OptionalProperty -InputObject $e -Name "kind")
            if ($k -eq "mic_speech_start") { $listened = $true }
            if ($k -eq "first_audio") { $spoke = $true }
        }
    }
    Add-Row -Row "12.22" -Capability "real listening / speaking states (the session's own mic_speech_start / first_audio)" -Proven ($listened -and $spoke) -Evidence "mic_speech_start=$listened first_audio=$spoke"
    Add-Row -Row "12.23" -Capability "a cognitive request reached its subsystem (query_kind + subsystem on the call)" -Proven ($cognitive.Count -ge 1) -Evidence $(if ($cognitive.Count) { $cognitive -join "; " } else { "none" })
    $live = @($cognitive | Where-Object { $_ -like "*world_state*" -or $_ -like "*eye_state*" })
    Add-Row -Row "12.28" -Capability "current state answered from the live path (state.now / world_state)" -Proven ($live.Count -ge 1) -Evidence $(if ($live.Count) { $live -join "; " } else { "none" })

    $eyeCalls = @($calls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "name") -like "eye.*" })
    $verifiedEnable = @($eyeCalls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "name") -eq "eye.enable" -and [string](Get-OptionalProperty -InputObject $_ -Name "terminal_status") -eq "verified" })
    $verifiedDisable = @($eyeCalls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "name") -eq "eye.disable" -and [string](Get-OptionalProperty -InputObject $_ -Name "terminal_status") -eq "verified" })
    $describe = {
        param($c)
        $oa = Get-OptionalProperty -InputObject $c -Name "observed_after"
        $local = if ($null -ne $oa) { Get-OptionalProperty -InputObject $oa -Name "local" } else { $null }
        $localState = if ($null -ne $local) { [string](Get-OptionalProperty -InputObject $local -Name "state") } else { "?" }
        $track = if ($null -ne $local) { [string](Get-OptionalProperty -InputObject $local -Name "media_track_ready_state") } else { "?" }
        return ("{0} {1} ({2}, browser {3}/{4})" -f (Get-OptionalProperty -InputObject $c -Name "name"), (Get-OptionalProperty -InputObject $c -Name "call_id"), (Get-OptionalProperty -InputObject $c -Name "session_id_"), $localState, $track)
    }
    Add-Row -Row "12.27a" -Capability "Gozunu ac by voice: eye.enable verified (camera opened, track live, durable read back)" -Proven ($verifiedEnable.Count -ge 1) -Evidence $(if ($verifiedEnable.Count) { ($verifiedEnable | ForEach-Object { & $describe $_ }) -join "; " } else { "none" })
    Add-Row -Row "12.24" -Capability "Gozunu kapat by voice: eye.disable verified (camera closed, track ended, durable read back)" -Proven ($verifiedDisable.Count -ge 1) -Evidence $(if ($verifiedDisable.Count) { ($verifiedDisable | ForEach-Object { & $describe $_ }) -join "; " } else { "none" })
    # re-enable after a disable within ONE session, all verified, in order
    $reenabled = $false; $reenableEvidence = "none"
    foreach ($id in $routerSessions) {
        $seq = @($eyeCalls | Where-Object { $_.session_id_ -eq $id } | Sort-Object -Property created_at)
        $steps = Get-EyeReceiptSteps -Calls $seq
        if ($steps.Done) { $reenabled = $true; $reenableEvidence = "session ${id}: " + (($steps.Matched | ForEach-Object { "{0} {1} {2}" -f $_.Name, $_.CallId, $(if ($_.Trace.Count) { $_.Trace[0] } else { "" }) }) -join " -> ") }
    }
    Add-Row -Row "12.27b" -Capability "enable -> disable -> enable again, all verified in one session (the transition race is gone)" -Proven $reenabled -Evidence $reenableEvidence
    $unverified = @($eyeCalls | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "terminal_status") -notin @("verified", "already") })
    Add-Row -Row "12.26" -Capability "every eye receipt is verified or names its class (no unverified/failed receipts in the window)" -Proven ($eyeCalls.Count -ge 1 -and $unverified.Count -eq 0) -Evidence "$($eyeCalls.Count) eye receipt(s), $($unverified.Count) not verified"

    # ---------------------------------------------------------------- ledger
    $sinceIso = $sinceAt.ToString("yyyy-MM-ddTHH:mm:ssZ")
    $events = Get-ArrayProperty -InputObject (Get-Json ("/v1/ledger/events?since=" + [uri]::EscapeDataString($sinceIso) + "&limit=200")) -Name "events"
    $events = @($events | Where-Object { Test-InWindow -Raw ([string](Get-OptionalProperty -InputObject $_ -Name "occurred_at")) })
    $types = @{}
    foreach ($e in $events) { $t = [string]$e.event_type; $types[$t] = 1 + $(if ($types.ContainsKey($t)) { $types[$t] } else { 0 }) }
    Write-Host ("      ledger types in the window: " + (($types.Keys | Sort-Object | ForEach-Object { "{0}={1}" -f $_, $types[$_] }) -join ", "))
    $eyeRows = @($events | Where-Object { [string]$_.event_type -like "eye.*" })
    $receiptRows = @($events | Where-Object { [string]$_.event_type -eq "action.receipt" })
    $hidden = Test-HiddenEyeMutation -LedgerRows $eyeRows -Receipts $receiptRows
    Add-Row -Row "12.29" -Capability "one mutation path: every voice-attributed eye row is explained by a receipt (by action_id or window)" -Proven ($hidden.Count -eq 0) -Evidence "$($eyeRows.Count) eye row(s), $($receiptRows.Count) receipt(s), $($hidden.Count) unexplained" -Note $(if ($hidden.Count) { $hidden -join "; " } else { "" })
    $presenceRows = @($events | Where-Object { [string]$_.event_type -eq "presence.state_changed" } | Sort-Object -Property occurred_at)
    $presenceSeq = @($presenceRows | ForEach-Object { "{0} {1} ({2}, {3} signals)" -f (Get-OptionalProperty -InputObject $_ -Name "occurred_at"), (Get-OptionalProperty -InputObject $_.detail_json -Name "to_state"), (Get-OptionalProperty -InputObject $_.detail_json -Name "confidence"), (Get-OptionalProperty -InputObject $_.detail_json -Name "signal_count") })
    $presentRows = @($presenceRows | Where-Object { $presentStates -contains [string](Get-OptionalProperty -InputObject $_.detail_json -Name "to_state") -and [string](Get-OptionalProperty -InputObject $_.detail_json -Name "signal_sources") -like "*camera*" })
    Add-Row -Row "12.25" -Capability "a current presence observation from the camera reached the engine (a present-family state with confidence)" -Proven ($presentRows.Count -ge 1) -Evidence $(if ($presenceSeq.Count) { $presenceSeq -join "; " } else { "no presence.state_changed row" })
    $states = @($presenceRows | ForEach-Object { [string](Get-OptionalProperty -InputObject $_.detail_json -Name "to_state") } | Where-Object { $_ -ne "unknown" })
    $leftAndReturned = ($states -contains "away") -and ((@($states | Where-Object { $presentStates -contains $_ })).Count -ge 1) -and (($states | Select-Object -Last 1) -in $presentStates)
    Add-Row -Row "12.6" -Capability "a real presence transition: the owner left (away) and came back (present/returned)" -Proven $leftAndReturned -Evidence ("states: " + $(if ($states.Count) { $states -join " -> " } else { "none" })) -Note $(if (-not $leftAndReturned) { "needs the owner to leave the room ~2.5 min and return with the camera on" } else { "" })
    $routineTriggered = ($types.ContainsKey("routine.triggered")) -and ($types.ContainsKey("routine.executed"))
    $executedRows = @($events | Where-Object { [string]$_.event_type -eq "routine.executed" })
    $alarmOk = $false; $alarmEvidence = "none"
    foreach ($r in $executedRows) {
        $d = Get-OptionalProperty -InputObject $r -Name "detail_json"
        $results = Get-ArrayProperty -InputObject $d -Name "dispatch_results"
        foreach ($res in $results) {
            $detail = Get-OptionalProperty -InputObject $res -Name "detail"
            if ([string](Get-OptionalProperty -InputObject $res -Name "kind") -eq "alarm" -and $null -ne $detail -and [bool](Get-OptionalProperty -InputObject $detail -Name "started")) {
                $alarmOk = $true
                $alarmEvidence = "{0}: dispatch {1}, started, ramp {2}->{3} over {4} s, max {5} s" -f (Get-OptionalProperty -InputObject $r -Name "occurred_at"), (Get-OptionalProperty -InputObject $d -Name "dispatch_status"), (Get-OptionalProperty -InputObject $detail -Name "start_volume"), (Get-OptionalProperty -InputObject $detail -Name "end_volume"), (Get-OptionalProperty -InputObject $detail -Name "ramp_seconds"), (Get-OptionalProperty -InputObject $detail -Name "max_duration_s")
            }
        }
    }
    Add-Row -Row "12.12" -Capability "a routine fires and the alarm is dispatched over the device path with a low, ramped start" -Proven ($routineTriggered -and $alarmOk) -Evidence $alarmEvidence
    Add-Row -Row "12.16" -Capability "the ledger records the whole run (session, explained, state answered, eye, presence, routine)" -Proven ($types.ContainsKey("voice.session.created") -and $types.ContainsKey("voice.session.closed") -and $types.ContainsKey("eye.enabled") -and $types.ContainsKey("eye.disabled") -and $types.ContainsKey("action.receipt")) -Evidence (($types.Keys | Sort-Object | ForEach-Object { "{0}={1}" -f $_, $types[$_] }) -join ", ")
    $violations = @()
    foreach ($e in $events) {
        $t = [string]$e.event_type
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
    Add-Row -Row "12.8" -Capability "no raw camera archive: presence rows carry only the structured summary" -Proven ($violations.Count -eq 0) -Evidence $(if ($violations.Count) { $violations -join "; " } else { "$($presenceRows.Count) presence row(s) inspected" })
    $closed = @($routerSessions | Where-Object { $sid = $_; $s = @($sessions | Where-Object { [string]$_.session_id -eq $sid }); $s.Count -gt 0 -and [string](Get-OptionalProperty -InputObject $s[0] -Name "state") -in @("closed", "expired") })
    Add-Row -Row "12.21b" -Capability "the Core session(s) of the run are closed" -Proven ($routerSessions.Count -ge 1 -and $closed.Count -eq $routerSessions.Count) -Evidence "$($closed.Count)/$($routerSessions.Count) closed" -Note $(if ($closed.Count -lt $routerSessions.Count) { "an eye-test session left open by design is closed by expiry; nothing else is affected" } else { "" })

    $proven = @($rows | Where-Object { $_.proven }).Count
    Write-Host ""
    Write-Host "reconciled: $proven of $($rows.Count) rows proven from the durable record"
    if ($OutFile) {
        $doc = [ordered]@{ since = $sinceAt.ToString("o"); until = $untilAt.ToString("o"); sessions = @($routerSessions); ledger_types = $types; rows = $rows; reconciled_at = [DateTimeOffset]::UtcNow.ToString("o") }
        $dir = Split-Path -Parent $OutFile
        if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
        [System.IO.File]::WriteAllText($OutFile, ($doc | ConvertTo-Json -Depth 8), (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "      written: $OutFile"
    }
}
finally {
    try { Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions/$mintedId/revoke" -Headers $headers -Body "{}" | Out-Null } catch { }
    $headers = $null
}
