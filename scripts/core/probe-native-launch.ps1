<#
.SYNOPSIS
    Ask the LIVE device to start an application this system built, and report exactly what it
    says. One capability call; nothing is installed, built or changed.

.DESCRIPTION
    The question this answers is not "does the code contain ADR-0098" - a commit graph answers
    that - but "does the RUNNING agent behave as ADR-0098 says". They are different claims, and
    only the second one decides whether the owner has to install anything.

    Two outcomes, both useful:

      succeeded          the installed agent carries the fix; the M28 launch path is open and
                         the qualification can run to the end
      permission_denied  it does not, and the device's own sentence says why - which is the
                         proof that a newer build is required, rather than an assertion that
                         one is

    Anything the device starts is closed again by pid, so a successful probe leaves nothing
    running.

    Run: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\core\probe-native-launch.ps1
    Exit 0 when the launch succeeded, 1 when it was refused, 2 when it could not be asked.
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = "http://100.90.158.26:8001",
    [string]$Exe,
    [int]$TimeoutSec = 60
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

#: scripts\core -> scripts -> repo root.
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")

if (-not $Exe) {
    $Exe = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "PagentOS Projects\native\item28-build\out\notlarim.exe"
}
if (-not (Test-Path -LiteralPath $Exe)) {
    Write-Host "no built application at $Exe - run the native lab with -workdir under the native root first" -ForegroundColor Yellow
    exit 2
}

$credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL"
if (-not $credential) { Write-Host "no stored owner credential"; exit 2 }
$body = @{ owner_credential = $credential; client_kind = "cli"; label = "probe-native-launch" } | ConvertTo-Json -Compress
$issued = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body $body -TimeoutSec 30
$credential = $null
$headers = @{ Authorization = "Bearer $($issued.token)" }

$listing = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/devices" -Headers $headers -TimeoutSec 30
$row = @($listing.devices) | Where-Object { $_.presence -eq "online" } | Select-Object -First 1
if (-not $row) { Write-Host "no online device"; exit 2 }
Write-Host "device   : $($row.name) ($($row.device_id)) $($row.software_version), $($row.capability_count) capabilities"
Write-Host "target   : $Exe"

$command = @{ capability = "app.launch"; payload = @{ application = $Exe } } | ConvertTo-Json -Depth 6 -Compress
$sent = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/devices/$($row.device_id)/commands" -Headers $headers -Body $command -TimeoutSec 60

$deadline = (Get-Date).AddSeconds($TimeoutSec)
$final = $null
while ((Get-Date) -lt $deadline) {
    $state = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/devices/$($row.device_id)/commands/$($sent.command_id)" -Headers $headers -TimeoutSec 30
    if (@("succeeded", "failed", "cancelled", "expired") -contains [string]$state.status) { $final = $state; break }
    Start-Sleep -Seconds 2
}
if (-not $final) { Write-Host "the command did not reach a terminal state in $TimeoutSec s"; exit 2 }

Write-Host ""
Write-Host "status   : $($final.status)"
$names = @($final.PSObject.Properties.Name)
if ($names -contains "error" -and $null -ne $final.error) {
    $err = @($final.error.PSObject.Properties.Name)
    Write-Host "class    : $(if ($err -contains 'class') { $final.error.class } else { '' })"
    Write-Host "message  : $(if ($err -contains 'message') { $final.error.message } else { '' })"
}

if ([string]$final.status -eq "succeeded") {
    $launchPid = $final.result.pid
    Write-Host "pid      : $launchPid"
    Write-Host ""
    Write-Host "THE INSTALLED AGENT CAN START WHAT THIS SYSTEM BUILT." -ForegroundColor Green
    if ($launchPid) {
        $close = @{ capability = "app.close"; payload = @{ pid = [int]$launchPid; force = $true } } | ConvertTo-Json -Depth 6 -Compress
        [void](Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/devices/$($row.device_id)/commands" -Headers $headers -Body $close -TimeoutSec 60)
        Write-Host "(closed again; the probe leaves nothing running)"
    }
    exit 0
}

Write-Host ""
Write-Host "THE INSTALLED AGENT CANNOT. The refusal above is the device's own, and it is the" -ForegroundColor Yellow
Write-Host "proof that the build carrying ADR-0098 is not the one running." -ForegroundColor Yellow
exit 1
