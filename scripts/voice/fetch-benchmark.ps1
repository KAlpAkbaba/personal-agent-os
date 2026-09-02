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
    $issued = Invoke-RestMethod -Method Post -Uri "$base/v1/identity/sessions" -ContentType "application/json" -Body $body -TimeoutSec 20
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
    $listing = Invoke-RestMethod -Uri "$base/v1/voice/realtime/sessions?limit=10" -Headers $Headers -TimeoutSec 20
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
        $state = Invoke-RestMethod -Uri "$base/v1/voice/realtime/sessions/$SessionId" -Headers $headers -TimeoutSec 20
    }
    catch {
        $status = $null
        try { $status = [int]$_.Exception.Response.StatusCode } catch { }
        if ($status -eq 404) {
            Write-Host "no session with id $SessionId (a mistyped character is the usual cause; nothing was lost)."
            Show-RecentSessions -Headers $headers | Out-Null
            throw "unknown session id; pick one from the list above or re-run with -Latest"
        }
        throw
    }
    $bench = Invoke-RestMethod -Uri "$base/v1/voice/realtime/sessions/$SessionId/benchmark" -Headers $headers -TimeoutSec 20
    $report = [ordered]@{
        fetched_at = (Get-Date).ToUniversalTime().ToString("o")
        session    = $state
        benchmark  = $bench
    }
    $json = $report | ConvertTo-Json -Depth 14
    Write-Output $json
    if ($OutFile) {
        [IO.File]::WriteAllText($OutFile, $json + "`n", (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "written to $OutFile (ids and timings only; no audio, no secret)"
    }
}
finally {
    if ($mintedId) {
        try { Invoke-RestMethod -Method Post -Uri "$base/v1/identity/sessions/$mintedId/revoke" -Headers $headers -TimeoutSec 20 | Out-Null; Write-Host "the session minted for this fetch was revoked" }
        catch { Write-Host "note: could not revoke the fetch session ($($_.Exception.Message)); it expires on its own" }
    }
    $token = $null
    $headers = $null
}
