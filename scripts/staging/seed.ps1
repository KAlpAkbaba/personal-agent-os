<#
.SYNOPSIS
    Gives the test team a working STAGING owner session: mints staging's own owner
    credential and exchanges it for a web session on the staging api.
.DESCRIPTION
    The credential is minted INSIDE the staging api container through the host-side recovery
    path (`python -m app.identity.recover --rotate --json`, ADR-0027), into staging's own
    identity volume (pagentos-staging-identity). It is a TEST identity: it opens only the
    staging api on 127.0.0.1:28001 and is worthless anywhere else. The owner's real
    credential, his devices and his sessions live on the Cloud Core and are never read here.

    The credential and the session token are written to
    %LOCALAPPDATA%\PagentOS\staging\owner.json (outside the repository, current user only);
    nothing secret is printed. Running seed again rotates the staging credential and revokes
    the earlier staging sessions.

    Mail and calendar: staging connects NO account (no IMAP/SMTP host, no ICS url, sending
    and calendar writes off - see the compose file); the test team's mailbox and calendar
    are the repository's fake providers in the test suites, never the owner's real mail or calendar account.

    Exit codes: 0 seeded and the session proved; 1 failure.
.EXAMPLE
    powershell -NoProfile -File scripts\staging\seed.ps1
#>
[CmdletBinding()]
param(
    [string]$ApiBase = "http://127.0.0.1:28001"
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$uri = [Uri]$ApiBase
if ($uri.Host -ne "127.0.0.1" -or $uri.Port -ne 28001) {
    Write-Host "STAGING SEED REFUSED: $ApiBase is not the staging api (127.0.0.1:28001)"
    exit 1
}
$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
$docker = if ($dockerCmd) { $dockerCmd.Source } else { "C:\Program Files\Docker\Docker\resources\bin\docker.exe" }

$raw = & $docker exec pagentos-staging-api uv run --no-sync python -m app.identity.recover --rotate --json 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "STAGING SEED FAILED: credential rotation in pagentos-staging-api exited $LASTEXITCODE"; exit 1 }
$minted = ($raw | Out-String) | ConvertFrom-Json
$credential = [string]$minted.owner_credential
if (-not $credential) { Write-Host "STAGING SEED FAILED: no credential in the rotation output"; exit 1 }

$body = @{ owner_credential = $credential; client_kind = "web"; label = "staging test team" } | ConvertTo-Json
try {
    $session = Invoke-RestMethod -Method Post -Uri "$ApiBase/v1/identity/sessions" -ContentType "application/json" -Body $body -TimeoutSec 15
} catch {
    Write-Host "STAGING SEED FAILED: session exchange refused ($($_.Exception.Message))"
    exit 1
}
$token = ""
if ($session.PSObject.Properties.Name -contains "token") { $token = [string]$session.token }
if (-not $token) { Write-Host "STAGING SEED FAILED: no token in the session answer"; exit 1 }

try {
    $current = Invoke-RestMethod -Uri "$ApiBase/v1/identity/sessions/current" -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 15
} catch {
    Write-Host "STAGING SEED FAILED: the new session does not open the staging api ($($_.Exception.Message))"
    exit 1
}

$stateDir = Join-Path $env:LOCALAPPDATA "PagentOS\staging"
New-Item -ItemType Directory -Force $stateDir | Out-Null
$file = Join-Path $stateDir "owner.json"
$record = [ordered]@{
    api = $ApiBase
    web = "http://127.0.0.1:28000/"
    owner_credential = $credential
    session_token = $token
    seeded_at = (Get-Date).ToUniversalTime().ToString("o")
    note = "STAGING test identity only - opens nothing but the staging stack"
}
[IO.File]::WriteAllText($file, ($record | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
& icacls $file /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null

$sid = ""
if ($current.PSObject.Properties.Name -contains "session_id") { $sid = [string]$current.session_id }
elseif ($current.PSObject.Properties.Name -contains "id") { $sid = [string]$current.id }
Write-Host "STAGING SEEDED: staging owner session $sid is valid; credential and token in $file"
exit 0
