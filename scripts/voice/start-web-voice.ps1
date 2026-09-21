<#
.SYNOPSIS
    Start the web shell on this PC against the real Hetzner Cloud Core for the M12
    real-microphone / WebRTC qualification session.

.DESCRIPTION
    Runs `pnpm --dir apps/web dev` with the API proxied same-origin to the Cloud Core over
    the tailnet (see apps/web/next.config.ts): no CORS change and no touch of the running
    api container. Then open http://localhost:3000/voice, sign in ONCE with the cloud
    Owner Credential (it is exchanged for a session and dropped), allow the microphone,
    and run the Turkish set in docs/OWNER_ACTIONS.md item 7. Nothing here handles a secret.

    Checks first that the Cloud Core answers over the tailnet and lists the realtime
    provider, so a failed session is never blamed on the wrong thing.

.EXAMPLE
    .\scripts\voice\start-web-voice.ps1
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    [int]$WebPort = 3000,
    [string]$ExpectProvider = "openai-realtime",
    [string]$PnpmPath = "pnpm",
    [switch]$NoPreflight
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "..\lib\NativeProcess.ps1")

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$upstream = "http://${BrokerHost}:$ApiPort"

if (-not $NoPreflight) {
    $doc = Invoke-RestMethod -Uri "$upstream/v1/system/health" -TimeoutSec 20
    $checks = Get-OptionalProperty -InputObject $doc -Name "checks"
    $rt = if ($null -ne $checks) { Get-OptionalProperty -InputObject $checks -Name "voice_realtime" } else { $null }
    $providers = if ($null -ne $rt) { @(Get-OptionalProperty -InputObject $rt -Name "providers") } else { @() }
    if ($providers -notcontains $ExpectProvider) {
        throw "Cloud Core at $upstream does not list realtime provider '$ExpectProvider' (providers: $($providers -join ', ')); nothing started"
    }
    Write-Host "Cloud Core ${upstream}: status=$(Get-OptionalProperty -InputObject $doc -Name 'status'), realtime providers=[$($providers -join ', ')]"
}

$env:PAGENTOS_API_UPSTREAM = $upstream
$env:NEXT_PUBLIC_API_BASE = "/api"
# ADR-0197: God's Eye View lives on the Cloud Core's aux socket, on the same tailnet host.
if (-not $env:NEXT_PUBLIC_GODS_EYE_URL) { $env:NEXT_PUBLIC_GODS_EYE_URL = "http://${BrokerHost}:4173/" }
$env:PORT = "$WebPort"
Write-Host "starting the web shell: http://localhost:$WebPort/voice  (API -> $upstream via same-origin /api rewrite)"
Write-Host "sign in once with the cloud Owner Credential; then allow the microphone. Ctrl+C stops the server."
Push-Location (Join-Path $repoRoot "apps\web")
try {
    & $PnpmPath dev --port $WebPort
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
