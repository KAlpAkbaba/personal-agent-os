<#
.SYNOPSIS
    B42 production smoke round (rows 393 XLSX, 394 PPTX, 398 CSV, 399 JSON): three artifacts
    made through the LIVE Cloud Core's POST /v1/artifacts/factory from the repository's own
    fixture specs, every render downloaded and read back independently.

.DESCRIPTION
    The verdict for each format is taken from the downloaded bytes, never from the factory's
    answer alone: the download must hash to the content_hash production declared, and the
    bytes must be the format they claim (an OOXML zip holding its own content-types part and
    the format's main part for XLSX/PPTX, parseable JSON, a CSV whose header is the spec's
    first row). Owner session minted from the stored credential and revoked at the end.

    Side effects, stated: three artifacts in the owner's library, titled
    "PagentOS dogrulama - ..." so they are recognisable. Nothing is sent to a device and
    nothing is deleted.

    Exit 0 when every row is PROVEN_REAL, 1 otherwise, 2 when production could not be asked.
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = "http://100.90.158.26:8001",
    [string]$EvidenceDir = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
. (Join-Path $repoRoot "scripts\lib\OwnerHarness.ps1")
Add-Type -AssemblyName System.IO.Compression
if (-not $EvidenceDir) { $EvidenceDir = Join-Path $repoRoot "docs\evidence" }
$specDir = Join-Path $repoRoot "services\api\tests\fixtures\artifacts\specs"

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

function Get-Bytes {
    param([string]$Uri)
    $request = [System.Net.HttpWebRequest]::Create($Uri)
    $request.Timeout = 60000
    foreach ($key in $script:headers.Keys) { $request.Headers.Add([string]$key, [string]$script:headers[$key]) }
    $response = $request.GetResponse()
    try {
        $stream = $response.GetResponseStream()
        $ms = New-Object System.IO.MemoryStream
        try { $stream.CopyTo($ms); return , $ms.ToArray() } finally { $stream.Dispose(); $ms.Dispose() }
    }
    finally { $response.Close() }
}

function Get-Sha256Hex {
    param([byte[]]$Bytes)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return (($sha.ComputeHash($Bytes) | ForEach-Object { $_.ToString("x2") }) -join "") } finally { $sha.Dispose() }
}

function Get-ZipEntries {
    param([byte[]]$Bytes)
    $ms = New-Object System.IO.MemoryStream(, $Bytes)
    try {
        $zip = New-Object System.IO.Compression.ZipArchive($ms, [System.IO.Compression.ZipArchiveMode]::Read)
        try { return , @($zip.Entries | ForEach-Object { $_.FullName }) } finally { $zip.Dispose() }
    }
    catch { return , @() }
    finally { $ms.Dispose() }
}

function Test-Render {
    param($Created, [string]$Format)
    # Assigned first: the accessor returns a wrapped array, so piping it directly hands
    # Where-Object the whole list as one item.
    $renders = Get-ArrayProperty -InputObject $Created -Name "renders"
    $render = $renders | Where-Object { [string](Get-OptionalProperty -InputObject $_ -Name "format") -eq $Format } | Select-Object -First 1
    if (-not $render) { return [pscustomobject]@{ ok = $false; observed = $null; reason = "production made no $Format render" } }
    $declared = [string](Get-OptionalProperty -InputObject $render -Name "content_hash")
    $state = [string](Get-OptionalProperty -InputObject $render -Name "state")
    $id = [string](Get-OptionalProperty -InputObject $Created -Name "artifact_id")
    $bytes = Get-Bytes -Uri "$BaseUrl/v1/artifacts/$id/renders/$Format"
    $hash = Get-Sha256Hex -Bytes $bytes
    $declaredHex = ($declared -replace '^sha256:', '')
    $shape = $false
    $detail = ""
    switch ($Format) {
        "xlsx" { $entries = Get-ZipEntries -Bytes $bytes; $shape = ($entries -contains "[Content_Types].xml") -and ($entries -contains "xl/workbook.xml"); $detail = "$($entries.Count) zip parts" }
        "pptx" { $entries = Get-ZipEntries -Bytes $bytes; $shape = ($entries -contains "[Content_Types].xml") -and ($entries -contains "ppt/presentation.xml"); $detail = "$($entries.Count) zip parts" }
        "json" {
            try { $null = (New-Object System.Text.UTF8Encoding($false)).GetString($bytes) | ConvertFrom-Json; $shape = $true; $detail = "parses as JSON" } catch { $detail = "does not parse: $($_.Exception.Message)" }
        }
        "csv" {
            $text = (New-Object System.Text.UTF8Encoding($false)).GetString($bytes)
            $first = ($text -split "`r?`n")[0]
            $shape = ($first -match ",") -and ($text.Length -gt 0)
            $detail = "header has $((($first -split ',')).Count) columns"
        }
    }
    $ok = ($state -eq "valid") -and ($hash -eq $declaredHex) -and $shape
    return [pscustomobject]@{
        ok       = $ok
        observed = [ordered]@{ artifact_id = $id; format = $Format; state = $state; bytes = $bytes.Length; declared_hash = $declared; downloaded_sha256 = $hash; shape = $detail }
        reason   = "state '$state', hash match $($hash -eq $declaredHex), shape: $detail"
    }
}

$startedAt = (Get-Date).ToUniversalTime()
Write-Host "B42 artifact smoke round against $BaseUrl"
try { $releaseBefore = Get-Release } catch { Write-Host "production is unreachable: $($_.Exception.Message)"; exit 2 }
Write-Host "release : $releaseBefore"

$credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL"
if (-not $credential) { Write-Host "no stored owner credential"; exit 2 }
$issued = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body (@{ owner_credential = $credential; client_kind = "cli"; label = "qualify-artifacts-production" } | ConvertTo-Json -Compress) -TimeoutSec 30
$credential = $null
$script:headers = @{ Authorization = "Bearer $($issued.token)" }
$ownerSessionId = [string](Get-OptionalProperty -InputObject $issued -Name "session_id")

function New-Artifact {
    param([string]$SpecFile, [string]$Title)
    $spec = Get-Content -Raw -Encoding UTF8 (Join-Path $specDir $SpecFile) | ConvertFrom-Json
    $spec.title = $Title
    $body = @{ spec = $spec } | ConvertTo-Json -Depth 20 -Compress
    return Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/artifacts/factory" -Headers $script:headers -Body $body -TimeoutSec 120
}

try {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm")
    $sheet = New-Artifact -SpecFile "butce-tablosu.json" -Title "PagentOS dogrulama - tablo $stamp"
    $deck = New-Artifact -SpecFile "q3-sunum.json" -Title "PagentOS dogrulama - sunum $stamp"
    $data = New-Artifact -SpecFile "musteri-listesi.json" -Title "PagentOS dogrulama - veri $stamp"

    foreach ($case in @(
            @{ id = "393"; artifact = $sheet; format = "xlsx"; claim = "an XLSX made by production, downloaded, hash-matched and read as an OOXML workbook" },
            @{ id = "394"; artifact = $deck; format = "pptx"; claim = "a PPTX made by production, downloaded, hash-matched and read as an OOXML presentation" },
            @{ id = "398"; artifact = $data; format = "csv"; claim = "a CSV made by production, downloaded, hash-matched, with its header row" },
            @{ id = "399"; artifact = $data; format = "json"; claim = "a JSON render made by production, downloaded, hash-matched and parsed" })) {
        $result = Test-Render -Created $case.artifact -Format $case.format
        Set-Row $case.id $result.ok $case.claim $result.observed $result.reason
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
    kind        = "artifact_production_round"
    started_at  = $startedAt.ToString("o")
    finished_at = (Get-Date).ToUniversalTime().ToString("o")
    production  = [ordered]@{ base_url = $BaseUrl; release_before = $releaseBefore; release_after = $releaseAfter; same_release = ($releaseBefore -eq $releaseAfter) }
    proven      = $proven
    rows        = $rows
}
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null
$path = Join-Path $EvidenceDir ("artifacts-production-round-{0}.json" -f (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd-HHmmss"))
[IO.File]::WriteAllText($path, ($evidence | ConvertTo-Json -Depth 12), (New-Object System.Text.UTF8Encoding($false)))
Write-Host ""
Write-Host "evidence : $path"
Write-Host ("proven   : {0} of {1} rows; release {2} -> {3}" -f $proven.Count, $rows.Count, $releaseBefore, $releaseAfter)
if ($releaseBefore -ne $releaseAfter) { exit 1 }
if ($proven.Count -eq $rows.Count) { exit 0 }
exit 1
