<#
.SYNOPSIS
    After a real voice session: fetch its state and latency benchmark from the Cloud Core
    (ids and timings only) so the numbers can be recorded as qualification evidence.

.DESCRIPTION
    Mints ONE owner session from the cloud Owner Credential typed into a masked prompt
    (never a parameter, never stored, never echoed), reads
    GET /v1/voice/realtime/sessions/{id} and .../benchmark, prints both as JSON, then
    revokes the session it minted. The benchmark carries the five M12 metrics with their
    targets and per-metric verdicts (spec §8); nothing in it is audio or a secret.

.EXAMPLE
    .\scripts\voice\fetch-benchmark.ps1 -SessionId 6f1c...   # the id shown on /voice
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SessionId,
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    [ValidateSet("web", "mobile", "desktop", "device", "cli")][string]$ClientKind = "cli",
    [string]$OutFile = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "..\lib\NativeProcess.ps1")

if ($SessionId -cnotmatch '^[0-9a-fA-F-]{36}$') { throw "SessionId must be the session UUID shown on the /voice page" }
$base = "http://${BrokerHost}:$ApiPort"

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

try {
    $state = Invoke-RestMethod -Uri "$base/v1/voice/realtime/sessions/$SessionId" -Headers $headers -TimeoutSec 20
    $bench = Invoke-RestMethod -Uri "$base/v1/voice/realtime/sessions/$SessionId/benchmark" -Headers $headers -TimeoutSec 20
    $report = [ordered]@{
        fetched_at = (Get-Date).ToUniversalTime().ToString("o")
        session    = $state
        benchmark  = $bench
    }
    $json = $report | ConvertTo-Json -Depth 12
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
