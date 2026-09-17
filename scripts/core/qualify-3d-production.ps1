<#
.SYNOPSIS
    3D production round: a Blender scene (B44 rows 520/521) and a Unity scene with a
    behaviour test and a Windows player build (B50 rows 530-533), both made through the LIVE
    Cloud Core's POST /v1/scenes on the owner's enrolled device, and read back.

.DESCRIPTION
    The same discipline as qualify-pc-production.ps1: an owner session minted from the stored
    credential and revoked at the end; every verdict is taken from production's own read-back
    (the scene row's state and compare result, which Cloud Core computed from what the DEVICE
    read), never from an exit code.

    Side effects, stated: two new scene projects under the owner's 3D root
    (Documents\PagentOS Projects\3d), each with its render, the Blender GLB export, and the
    Unity player build (Build\<scene>.exe). Editors run headless in batch mode; no window is
    opened. Nothing is deleted.

    Exit 0 when every row is PROVEN_REAL, 1 otherwise, 2 when production could not be asked.
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = "http://100.90.158.26:8001",
    [switch]$SkipUnity,
    [switch]$SkipBlender,
    [string]$EvidenceDir = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
. (Join-Path $repoRoot "scripts\lib\OwnerHarness.ps1")
if (-not $EvidenceDir) { $EvidenceDir = Join-Path $repoRoot "docs\evidence" }

$rows = [ordered]@{}
function Set-Row {
    param([string]$Id, [bool]$Proven, [string]$Claim, $Observed, [string]$Reason = "")
    $rows[$Id] = [ordered]@{
        status   = $(if ($Proven) { "PROVEN_REAL" } else { "NOT_YET_PROVEN" })
        claim    = $Claim
        observed = $Observed
        reason   = $(if ($Proven) { $null } else { $Reason })
    }
    $color = if ($Proven) { "Green" } else { "Yellow" }
    Write-Host ("  {0,-4} {1,-15} {2}" -f $Id, $rows[$Id].status, $Claim) -ForegroundColor $color
    if (-not $Proven -and $Reason) { Write-Host "       $Reason" -ForegroundColor Yellow }
}

function Get-Release {
    $health = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/system/health" -TimeoutSec 30
    return [string](Get-OptionalProperty -InputObject (Get-OptionalProperty -InputObject $health -Name "release") -Name "version")
}

function New-Scene {
    param([hashtable]$Plan, [int]$TimeoutSec)
    $body = @{ plan = $Plan } | ConvertTo-Json -Depth 12 -Compress
    $started = Get-Date
    try {
        $answer = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/scenes" -Headers $script:headers -Body $body -TimeoutSec $TimeoutSec
        $status = 201
    }
    catch {
        $answer = $null
        $status = Get-OptionalProperty -InputObject $_.Exception -Name "StatusCode"
        Write-Host "  POST /v1/scenes -> $($_.Exception.Message)" -ForegroundColor Yellow
    }
    $seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)
    $sceneId = [string](Get-OptionalProperty -InputObject $answer -Name "scene_id")
    $row = $null
    if ($sceneId) { $row = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/scenes/$sceneId" -Headers $script:headers -TimeoutSec 30 }
    return [pscustomobject]@{ status = $status; seconds = $seconds; answer = $answer; row = $row; scene_id = $sceneId }
}

$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
$startedAt = (Get-Date).ToUniversalTime()
Write-Host "3D production round against $BaseUrl"
try { $releaseBefore = Get-Release } catch { Write-Host "production is unreachable: $($_.Exception.Message)"; exit 2 }
Write-Host "release : $releaseBefore"

$credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL"
if (-not $credential) { Write-Host "no stored owner credential"; exit 2 }
$issued = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body (@{ owner_credential = $credential; client_kind = "cli"; label = "qualify-3d-production" } | ConvertTo-Json -Compress) -TimeoutSec 30
$credential = $null
$script:headers = @{ Authorization = "Bearer $($issued.token)" }
$ownerSessionId = [string](Get-OptionalProperty -InputObject $issued -Name "session_id")

$blender = $null
$unity = $null
try {
    if (-not $SkipBlender) {
        Write-Host ""
        Write-Host "B44 Blender scene (headless)"
        $plan = @{
            tool = "blender"; project = "qualification"; scene = "blender-$stamp"
            operations = @(
                @{ op = "create_scene" },
                @{ op = "add_primitive"; kind = "cube"; name = "Kutu"; location = @(0.0, 0.0, 0.5) },
                @{ op = "add_primitive"; kind = "light_sun"; name = "Gunes"; location = @(2.0, -2.0, 5.0) },
                @{ op = "add_primitive"; kind = "camera"; name = "Kamera"; location = @(4.0, -4.0, 3.0) },
                @{ op = "transform"; name = "Kutu"; rotation = @(0.0, 0.0, 30.0) },
                @{ op = "set_material"; name = "Kutu"; color = @(0.2, 0.5, 0.9, 1.0) },
                @{ op = "set_camera"; name = "Kamera"; look_at = "Kutu" },
                @{ op = "set_light"; name = "Gunes"; energy = 3.0 },
                @{ op = "render"; width = 320; height = 240 },
                @{ op = "export"; format = "glb" },
                @{ op = "inspect" }
            )
        }
        $blender = New-Scene -Plan $plan -TimeoutSec 420
        $compare = Get-OptionalProperty -InputObject $blender.row -Name "compare"
        $ok = [bool](Get-OptionalProperty -InputObject $compare -Name "ok")
        $exports = Get-ArrayProperty -InputObject $blender.row -Name "exports"
        $verifiedExports = @($exports | Where-Object { Get-OptionalProperty -InputObject $_ -Name "verified" })
        Set-Row "521" ($blender.status -eq 201 -and $ok) "a Blender scene made through production's POST /v1/scenes on the device, verified" `
            ([ordered]@{ http = $blender.status; seconds = $blender.seconds; scene_id = $blender.scene_id; state = Get-OptionalProperty -InputObject $blender.row -Name "state"; checked = Get-OptionalProperty -InputObject $compare -Name "checked"; mismatches = Get-OptionalProperty -InputObject $compare -Name "mismatches" }) `
            "the scene was not verified (HTTP $($blender.status))"
        Set-Row "520" ($ok -and [bool](Get-OptionalProperty -InputObject $blender.row -Name "has_render") -and @($verifiedExports).Count -gt 0) "the device read back the render and the GLB export it made" `
            ([ordered]@{ has_render = Get-OptionalProperty -InputObject $blender.row -Name "has_render"; exports = $exports }) "no verified render/export read back"
    }

    if (-not $SkipUnity) {
        Write-Host ""
        Write-Host "B50 Unity scene, behaviour test and Windows player (batch mode; first import takes minutes)"
        $fixture = Get-Content -Raw -Encoding UTF8 (Join-Path $repoRoot "services\api\tests\fixtures\creative3d\unity-plan.json") | ConvertFrom-Json
        $plan = @{ tool = "unity"; project = "qualification"; scene = "unity-$stamp"; operations = @($fixture.operations) }
        $unity = New-Scene -Plan $plan -TimeoutSec 900
        $compare = Get-OptionalProperty -InputObject $unity.row -Name "compare"
        $ok = [bool](Get-OptionalProperty -InputObject $compare -Name "ok")
        $inspection = Get-OptionalProperty -InputObject $unity.row -Name "inspection"
        $build = Get-OptionalProperty -InputObject $inspection -Name "build"
        $tests = Get-ArrayProperty -InputObject $inspection -Name "tests"
        $passedTests = @($tests | Where-Object { (Get-OptionalProperty -InputObject $_ -Name "passed") -eq $true })
        $base = [ordered]@{ http = $unity.status; seconds = $unity.seconds; scene_id = $unity.scene_id; state = Get-OptionalProperty -InputObject $unity.row -Name "state"; checked = Get-OptionalProperty -InputObject $compare -Name "checked"; mismatches = Get-OptionalProperty -InputObject $compare -Name "mismatches" }
        Set-Row "530" ($unity.status -eq 201 -and $ok) "a Unity project scaffolded and run in the licensed editor through production" $base "the Unity scene was not verified (HTTP $($unity.status))"
        Set-Row "531" ($ok -and [bool](Get-OptionalProperty -InputObject $unity.row -Name "has_render")) "the scene, its objects and its render, read back and compared by production" `
            ([ordered]@{ objects = Get-OptionalProperty -InputObject $unity.row -Name "objects"; has_render = Get-OptionalProperty -InputObject $unity.row -Name "has_render" }) "no verified scene/render"
        Set-Row "532" ($ok -and [string](Get-OptionalProperty -InputObject $build -Name "result") -eq "Succeeded") "a Windows player built by the editor, read back by the device as a PE with the editor's hash" `
            ([ordered]@{ build = $build }) "no verified player build"
        Set-Row "533" ($ok -and $tests.Count -gt 0 -and $passedTests.Count -eq $tests.Count) "every attached catalogue script was stepped and acted" `
            ([ordered]@{ tests = $tests }) "the behaviour test did not pass"
    }
}
finally {
    if ($ownerSessionId) {
        try { Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions/$ownerSessionId/revoke" -Headers $script:headers -Body "{}" -TimeoutSec 30 | Out-Null } catch { }
    }
    $script:headers = $null
}

$releaseAfter = Get-Release
$proven = @($rows.Keys | Where-Object { $rows[$_].status -eq "PROVEN_REAL" })
$evidence = [ordered]@{
    kind        = "3d_production_round"
    started_at  = $startedAt.ToString("o")
    finished_at = (Get-Date).ToUniversalTime().ToString("o")
    production  = [ordered]@{ base_url = $BaseUrl; release_before = $releaseBefore; release_after = $releaseAfter; same_release = ($releaseBefore -eq $releaseAfter) }
    proven      = $proven
    rows        = $rows
}
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null
$path = Join-Path $EvidenceDir ("3d-production-round-{0}.json" -f (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd-HHmmss"))
[IO.File]::WriteAllText($path, ($evidence | ConvertTo-Json -Depth 14), (New-Object System.Text.UTF8Encoding($false)))
Write-Host ""
Write-Host "evidence : $path"
Write-Host ("proven   : {0} of {1} rows; release {2} -> {3}" -f $proven.Count, $rows.Count, $releaseBefore, $releaseAfter)
if ($releaseBefore -ne $releaseAfter) { exit 1 }
if ($proven.Count -eq $rows.Count) { exit 0 }
exit 1
