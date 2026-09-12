<#
.SYNOPSIS
    M28 row 26.16 against PRODUCTION: the owner's ask -> native.create -> native.build on the
    enrolled Windows device -> the durable row read back from production -> an evidence file.

.DESCRIPTION
    Row 26.16 is "a build startable from PRODUCTION". Every suite that covers it runs in
    process with fakes; this runs the real thing, the way the voice client does: an owner
    session minted from the stored credential, a real realtime session on the live Cloud
    Core, the real `native.create` and `native.build` tools through the same tool-call relay,
    and the real device doing the compiling. It then reads the build row back through
    /v1/native/{id} - the owner's panel's own read - rather than trusting the tool's receipt.

    The verdict is the ROW's, and it is taken literally:

      PROVEN_REAL     the row is `verified`, it was built on the device, and production
                      served the release this run recorded before and after the build
      NOT_YET_PROVEN  anything else, with the row's own state and error named. An agent
                      older than PeImageReader (ADR-0119) lands here honestly as
                      `unverified`: it built, and it could not say which build it made.

    Nothing is inferred from an exit code, and nothing is written as evidence except what
    production and the device answered.

    Run: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\core\qualify-native-production.ps1
    Exit 0 when PROVEN_REAL, 1 when the run completed and did not prove it, 2 when it could
    not be asked (no credential, no online device, production unreachable).
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = "http://100.90.158.26:8001",
    [string]$Name = "Notlarim",
    # A device build is scaffold + build + test + publish + inspect; the device bounds each
    # step itself. This only has to outlast them.
    [int]$BuildTimeoutSec = 1200,
    [string]$EvidenceDir = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

#: scripts\core -> scripts -> repo root.
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
. (Join-Path $repoRoot "scripts\lib\OwnerHarness.ps1")

if (-not $EvidenceDir) { $EvidenceDir = Join-Path $repoRoot "docs\evidence" }

function Get-ServedRelease {
    param([string]$Url)
    $health = Invoke-JsonUtf8 -Uri "$Url/v1/system/health" -TimeoutSec 30
    $release = Get-OptionalProperty -InputObject $health -Name "release"
    return [pscustomobject]@{
        status  = [string](Get-OptionalProperty -InputObject $health -Name "status")
        version = [string](Get-OptionalProperty -InputObject $release -Name "version")
    }
}

$startedAt = (Get-Date).ToUniversalTime()
Write-Host "M28 row 26.16 against production ($BaseUrl)"

try {
    $before = Get-ServedRelease -Url $BaseUrl
}
catch {
    Write-Host "production is unreachable: $($_.Exception.Message)"
    exit 2
}
Write-Host "production : release $($before.version) ($($before.status))"

$credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL"
if (-not $credential) { Write-Host "no stored owner credential"; exit 2 }
$body = @{ owner_credential = $credential; client_kind = "cli"; label = "qualify-native-production" } | ConvertTo-Json -Compress
$issued = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body $body -TimeoutSec 30
$credential = $null
$body = $null
$headers = @{ Authorization = "Bearer $($issued.token)" }

$listing = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/devices" -Headers $headers -TimeoutSec 30
# A BARE call, assigned: the helper returns , @(...) so wrapping it in @() would hand
# back ONE element - the whole array - and every field would read empty (the first two
# runs of this script died that way, on a device the same endpoint called online).
$devices = Get-ArrayProperty -InputObject $listing -Name "devices"
$device = $devices |
    Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "presence") -eq "online" } |
    Select-Object -First 1
if (-not $device) { Write-Host "no online device"; exit 2 }
$deviceFacts = [ordered]@{
    device_id        = [string](Get-OptionalProperty -InputObject $device -Name "device_id")
    name             = [string](Get-OptionalProperty -InputObject $device -Name "name")
    software_version = [string](Get-OptionalProperty -InputObject $device -Name "software_version")
    build_id         = Get-OptionalProperty -InputObject $device -Name "build_id"
    source_revision  = Get-OptionalProperty -InputObject $device -Name "source_revision"
}
Write-Host "device     : $($deviceFacts.name) $($deviceFacts.software_version) build $($deviceFacts.build_id)"

$session = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/voice/realtime/sessions" -Headers $headers -Body "{}" -TimeoutSec 60
$sid = [string]$session.session_id
Write-Host "session    : $sid"

function Invoke-Tool {
    param([string]$ToolName, [hashtable]$Arguments, [int]$TimeoutSec)
    $payload = @{ call_id = "q2616-$([guid]::NewGuid())"; name = $ToolName; arguments = $Arguments } |
        ConvertTo-Json -Depth 6 -Compress
    return Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/voice/realtime/sessions/$sid/tool-calls" `
        -Headers $headers -Body $payload -TimeoutSec $TimeoutSec
}

Write-Host ""
Write-Host "1. native.create { targets = windows_exe; name = $Name }"
$created = Invoke-Tool -ToolName "native.create" -Arguments @{ targets = @("windows_exe"); name = $Name } -TimeoutSec 60
$createdResult = Get-OptionalProperty -InputObject $created -Name "result"
$builds = Get-ArrayProperty -InputObject $createdResult -Name "builds"
Write-Host "   status : $($created.status)"
Write-Host "   speech : $(Get-OptionalProperty -InputObject $createdResult -Name 'speech')"
$planned = $builds | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "target") -eq "windows_exe" } | Select-Object -First 1
$buildId = if ($planned) { [string](Get-OptionalProperty -InputObject $planned -Name "build_id") } else { "" }
$plannedState = if ($planned) { [string](Get-OptionalProperty -InputObject $planned -Name "state") } else { "" }
Write-Host "   row    : $buildId ($plannedState)"

$built = $null
$builtResult = $null
$buildStarted = $null
$buildSeconds = $null
if ($buildId -and $plannedState -eq "planned") {
    Write-Host ""
    Write-Host "2. native.build { build_id = $buildId }   (compiles on the device; minutes)"
    $buildStarted = Get-Date
    $built = Invoke-Tool -ToolName "native.build" -Arguments @{ build_id = $buildId } -TimeoutSec $BuildTimeoutSec
    $buildSeconds = [math]::Round(((Get-Date) - $buildStarted).TotalSeconds, 1)
    $builtResult = Get-OptionalProperty -InputObject $built -Name "result"
    Write-Host "   status : $($built.status) after $buildSeconds s"
    Write-Host "   speech : $(Get-OptionalProperty -InputObject $builtResult -Name 'speech')"
}

$row = $null
if ($buildId) {
    Write-Host ""
    Write-Host "3. the row, read back from production (/v1/native/$buildId)"
    $row = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/native/$buildId" -Headers $headers -TimeoutSec 30
    Write-Host "   state  : $($row.state)"
}

$after = Get-ServedRelease -Url $BaseUrl
$rowState = if ($row) { [string](Get-OptionalProperty -InputObject $row -Name "state") } else { "" }
$builtOn = [string](Get-OptionalProperty -InputObject $builtResult -Name "built_on")
$sameRelease = ($before.version -and $before.version -eq $after.version)

$reasons = @()
if (-not $buildId) { $reasons += "native.create opened no windows_exe row" }
elseif ($plannedState -ne "planned") { $reasons += "the windows_exe row opened as '$plannedState', not planned" }
if ($builtOn -ne "device") { $reasons += "the build did not run on the device (built_on '$builtOn')" }
if ($rowState -ne "verified") { $reasons += "the row is '$rowState', not verified" }
if (-not $sameRelease) { $reasons += "production changed release during the run ($($before.version) -> $($after.version))" }
$verdict = if ($reasons.Count -eq 0) { "PROVEN_REAL" } else { "NOT_YET_PROVEN" }

$evidence = [ordered]@{
    row                   = "26.16"
    claim                 = "a native Windows build startable from PRODUCTION, built on the enrolled device and verified from the artefact's own identity"
    status                = $verdict
    reasons               = $reasons
    started_at            = $startedAt.ToString("o")
    finished_at           = (Get-Date).ToUniversalTime().ToString("o")
    production            = [ordered]@{ base_url = $BaseUrl; release_before = $before.version; release_after = $after.version; health_before = $before.status; health_after = $after.status }
    device                = $deviceFacts
    realtime_session_id   = $sid
    native_create         = [ordered]@{ status = $created.status; result = $createdResult }
    native_build          = [ordered]@{ status = $(if ($built) { $built.status } else { $null }); seconds = $buildSeconds; result = $builtResult }
    row_read_back         = $row
}
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd-HHmmss")
$path = Join-Path $EvidenceDir "m28-native-production-$stamp.json"
[IO.File]::WriteAllText($path, ($evidence | ConvertTo-Json -Depth 14), (New-Object System.Text.UTF8Encoding($false)))

Write-Host ""
Write-Host "evidence   : $path"
if ($verdict -eq "PROVEN_REAL") {
    Write-Host "26.16 PROVEN_REAL: production started it, the device built it, the artefact said which build it is" -ForegroundColor Green
    exit 0
}
Write-Host "26.16 NOT_YET_PROVEN:" -ForegroundColor Yellow
foreach ($reason in $reasons) { Write-Host "  - $reason" -ForegroundColor Yellow }
exit 1
