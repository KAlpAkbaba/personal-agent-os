<#
.SYNOPSIS
    After a real voice session: fetch its durable evidence record (state + latency
    benchmark + noise counters) from the Cloud Core, ids and timings only.

.DESCRIPTION
    Mints ONE owner session from the cloud Owner Credential typed into a masked prompt
    (never a parameter, never stored, never echoed), reads the session and its benchmark,
    prints both as JSON, writes them to -OutFile when given, and revokes the session it
    minted. The record is computed from the Cloud Core's durable audit rows (and a
    snapshot taken at close), never from browser memory, so a closed session is
    fetchable any time later. Nothing in it is audio or a secret.

    Bodies are decoded as UTF-8 regardless of the response charset
    (scripts/lib/HttpJson.ps1): a real record once came back as "Ã"/"Å" because Windows
    PowerShell 5.1 decoded a charset-less JSON body as Latin-1 while the database held
    correct UTF-8. The file is written as UTF-8 without BOM.

    Never transcribe a UUID by hand: -Latest fetches the newest session; without it the
    id must be the full UUID (use the page's "Session ID kopyala" button). On a 404 the
    script lists the recent sessions so the right one can be picked.

.EXAMPLE
    .\scripts\voice\fetch-benchmark.ps1 -Latest -OutFile voice-session-2.json
.EXAMPLE
    .\scripts\voice\fetch-benchmark.ps1 -SessionId 2b3517ed-3272-461b-8ff7-47898ea23334
#>
[CmdletBinding(DefaultParameterSetName = "ById")]
param(
    [Parameter(ParameterSetName = "ById", Mandatory = $true)][string]$SessionId,
    [Parameter(ParameterSetName = "Latest", Mandatory = $true)][switch]$Latest,
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    [ValidateSet("web", "mobile", "desktop", "device", "cli")][string]$ClientKind = "cli",
    [string]$OutFile = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "..\lib\NativeProcess.ps1")
. (Join-Path $PSScriptRoot "..\lib\HttpJson.ps1")

$base = "http://${BrokerHost}:$ApiPort"
if (-not $Latest) {
    if ($SessionId -cnotmatch '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
        throw "SessionId must be the FULL session UUID (use the page's 'Session ID kopyala' button, or -Latest)"
    }
}

$secure = Read-Host -Prompt "Cloud Owner Credential (input is hidden; exchanged for one session, then dropped)" -AsSecureString
if ($secure.Length -eq 0) { throw "empty credential; nothing done" }
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $credential = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }

$issued = $null
try {
    $body = @{ owner_credential = $credential; client_kind = $ClientKind; label = "fetch-benchmark" } | ConvertTo-Json -Compress
    $issued = Invoke-JsonUtf8 -Method POST -Uri "$base/v1/identity/sessions" -Body $body
}
finally {
    $credential = $null
    $body = $null
}
$token = Get-OptionalProperty -InputObject $issued -Name "token"
if (-not $token) { throw "the identity service issued no session token (wrong credential? it never says which)" }
$headers = @{ Authorization = "Bearer $token" }
$mintedId = Get-OptionalProperty -InputObject $issued -Name "session_id"

function Show-RecentSessions {
    param([hashtable]$Headers)
    $listing = Invoke-JsonUtf8 -Uri "$base/v1/voice/realtime/sessions?limit=10" -Headers $Headers
    $sessions = @(Get-OptionalProperty -InputObject $listing -Name "sessions")
    Write-Host "recent sessions on $BrokerHost (newest first):"
    foreach ($s in $sessions) {
        Write-Host ("  {0}  {1,-7} voice={2,-6} started={3}  ended={4}" -f $s.session_id, $s.state, $s.voice, $s.started_at, $s.ended_at)
    }
    return $sessions
}

try {
    if ($Latest) {
        $sessions = Show-RecentSessions -Headers $headers
        if ($sessions.Count -eq 0) { throw "no realtime sessions recorded yet" }
        $SessionId = [string]$sessions[0].session_id
        Write-Host "using the newest session: $SessionId"
    }
    try {
        $state = Invoke-JsonUtf8 -Uri "$base/v1/voice/realtime/sessions/$SessionId" -Headers $headers
    }
    catch {
        $status = $null
        try { $status = [int](Get-OptionalProperty -InputObject $_.Exception -Name "StatusCode") } catch { }
        if ($status -eq 404) {
            Write-Host "no session with id $SessionId (a mistyped character is the usual cause; nothing was lost)."
            Show-RecentSessions -Headers $headers | Out-Null
            throw "unknown session id; pick one from the list above or re-run with -Latest"
        }
        throw
    }
    $bench = Invoke-JsonUtf8 -Uri "$base/v1/voice/realtime/sessions/$SessionId/benchmark" -Headers $headers
    $report = [ordered]@{
        fetched_at = (Get-Date).ToUniversalTime().ToString("o")
        session    = $state
        benchmark  = $bench
    }
    # A compact, human-readable summary first (the full JSON follows): what met its
    # target, the noise counters, and the sub-phase breakdown - numbers only.
    Write-Host ""
    Write-Host ("== session {0}  state={1}  voice={2}  profile={3}  model={4}" -f $SessionId,
        (Get-OptionalProperty -InputObject $state -Name "state"),
        (Get-OptionalProperty -InputObject $state -Name "voice"),
        (Get-OptionalProperty -InputObject $state -Name "voice_profile"),
        (Get-OptionalProperty -InputObject (Get-OptionalProperty -InputObject $bench -Name "context") -Name "model"))
    $check = Get-OptionalProperty -InputObject $bench -Name "target_check"
    if ($null -ne $check) {
        foreach ($prop in $check.PSObject.Properties) {
            $v = $prop.Value
            $line = "  {0,-24} target {1,5} ms  p95 {2,6}  met={3}" -f $prop.Name,
                (Get-OptionalProperty -InputObject $v -Name "target_ms"),
                (Get-OptionalProperty -InputObject $v -Name "observed_p95_ms"),
                (Get-OptionalProperty -InputObject $v -Name "met")
            Write-Host $line
        }
    }
    $ctx = Get-OptionalProperty -InputObject $bench -Name "context"
    $noise = if ($null -ne $ctx) { Get-OptionalProperty -InputObject $ctx -Name "noise" } else { $null }
    if ($null -ne $noise) {
        Write-Host ("  noise: false_starts={0} false_barge_ins={1} false_turns={2} gate_opens={3} calibrations={4} calibration_measured={5}" -f
            (Get-OptionalProperty -InputObject $noise -Name "false_starts"), (Get-OptionalProperty -InputObject $noise -Name "false_barge_ins"),
            (Get-OptionalProperty -InputObject $noise -Name "false_turns"), (Get-OptionalProperty -InputObject $noise -Name "gate_opens"),
            (Get-OptionalProperty -InputObject $noise -Name "calibrations"), (Get-OptionalProperty -InputObject $noise -Name "calibration_measured"))
    }
    $breakdown = if ($null -ne $ctx) { Get-OptionalProperty -InputObject $ctx -Name "breakdown" } else { $null }
    if ($null -ne $breakdown) {
        foreach ($kind in $breakdown.PSObject.Properties) {
            $parts = @()
            foreach ($field in $kind.Value.PSObject.Properties) {
                if ($field.Value -is [System.Management.Automation.PSCustomObject]) {
                    $parts += ("{0} n={1} p50={2} p95={3}" -f $field.Name, $field.Value.n, $field.Value.p50_ms, $field.Value.p95_ms)
                }
                elseif ($field.Name -match "_count$|^events$|^without_breakdown$") { $parts += ("{0}={1}" -f $field.Name, $field.Value) }
            }
            Write-Host ("  breakdown {0}: {1}" -f $kind.Name, ($parts -join "; "))
        }
    }
    Write-Host ""
    $json = $report | ConvertTo-Json -Depth 14
    Write-Output $json
    if ($OutFile) {
        [IO.File]::WriteAllText($OutFile, $json + "`n", (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "written to $OutFile (UTF-8, no BOM; ids and timings only; no audio, no secret)"
    }
}
finally {
    if ($mintedId) {
        try { Invoke-JsonUtf8 -Method POST -Uri "$base/v1/identity/sessions/$mintedId/revoke" -Headers $headers -Body "{}" | Out-Null; Write-Host "the session minted for this fetch was revoked" }
        catch { Write-Host "note: could not revoke the fetch session ($($_.Exception.Message)); it expires on its own" }
    }
    $token = $null
    $headers = $null
}
