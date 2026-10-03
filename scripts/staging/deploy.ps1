<#
.SYNOPSIS
    Moves the STAGING stack to a commit: builds that commit's api and web images and
    (re)starts staging on them, runs the migrations, waits for health, prints the url.
.DESCRIPTION
    Only a commit that is on main or on the lead branch (team/nightly/lead) may be deployed:
    staging is where the test team tries what is about to be, or already is, the owner's
    product - never an unreviewed worker branch (exit 2 otherwise, nothing built).

    The commit's tree is exported with `git archive` into -WorkRoot (never the checkout
    itself, so a dirty tree or another branch is never what gets built) and the images are
    tagged pagentos-staging/cloud-core:<sha12> and pagentos-staging/web:<sha12>. The
    stack itself is started by up.ps1 with PAGENTOS_STAGING_RELEASE_TAG set, so the memory
    refusal (exit 3), the migrations and the health wait are the same as a plain start.
    Moving BACK to an older commit keeps the database as it is: the schema is expand-only
    (the release's own rule), so an older image runs on a newer schema.

    The deployed sha is written to %LOCALAPPDATA%\PagentOS\staging\deployed.json.

    Exit codes: 0 deployed; 1 build/docker failure; 2 sha refused; 3 not enough memory;
    4 not healthy; 5 migration failed.
.PARAMETER Sha
    The commit to deploy (any form git rev-parse understands).
.PARAMETER CheckOnly
    Only decide whether the sha may be deployed; build and start nothing.
.PARAMETER NoFetch
    Do not `git fetch` main and the lead branch first (tests, offline).
.EXAMPLE
    powershell -NoProfile -File scripts\staging\deploy.ps1 a5e68d92
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Sha,
    [switch]$CheckOnly,
    [switch]$NoFetch,
    [string]$WorkRoot = "",
    [int]$TimeoutSec = 300
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$allowedRefs = @("origin/main", "main", "origin/team/nightly/lead", "team/nightly/lead")

if (-not $NoFetch) {
    & git -C $repoRoot fetch --quiet origin main team/nightly/lead 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "warning: git fetch failed; deciding on the refs this checkout already has" }
}

$full = (& git -C $repoRoot rev-parse --verify --quiet "$Sha^{commit}" 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $full) {
    Write-Host "STAGING DEPLOY REFUSED: '$Sha' is not a commit in this repository"
    exit 2
}
$full = "$full".Trim()
$onRef = $null
foreach ($ref in $allowedRefs) {
    & git -C $repoRoot rev-parse --verify --quiet "$ref^{commit}" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { continue }
    & git -C $repoRoot merge-base --is-ancestor $full $ref 2>$null
    if ($LASTEXITCODE -eq 0) { $onRef = $ref; break }
}
if (-not $onRef) {
    Write-Host "STAGING DEPLOY REFUSED: $full is not on main or on the lead branch (team/nightly/lead); only those may go to staging"
    exit 2
}
$tag = $full.Substring(0, 12)
Write-Host "sha $full is on $onRef - may be deployed (images :$tag)"
if ($CheckOnly) { exit 0 }

$upScript = Join-Path $PSScriptRoot "up.ps1"
& powershell -NoProfile -File $upScript -CheckOnly
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
$docker = if ($dockerCmd) { $dockerCmd.Source } else { "C:\Program Files\Docker\Docker\resources\bin\docker.exe" }

if (-not $WorkRoot) { $WorkRoot = Join-Path (Split-Path -Parent $repoRoot) "pagentos-staging-work" }
$tree = Join-Path $WorkRoot $tag
if (Test-Path $tree) { Remove-Item -Recurse -Force $tree }
New-Item -ItemType Directory -Force $tree | Out-Null
$tarFile = Join-Path $WorkRoot "$tag.tar"
& git -C $repoRoot archive --format=tar -o $tarFile $full
if ($LASTEXITCODE -ne 0) { Write-Host "STAGING DEPLOY FAILED: git archive exited $LASTEXITCODE"; exit 1 }
& tar -xf $tarFile -C $tree
$tarRc = $LASTEXITCODE
Remove-Item -Force $tarFile
if ($tarRc -ne 0) { Write-Host "STAGING DEPLOY FAILED: could not unpack the tree"; exit 1 }

Write-Host "building pagentos-staging/cloud-core:$tag ..."
& $docker build -q -t "pagentos-staging/cloud-core:$tag" (Join-Path $tree "services\api")
if ($LASTEXITCODE -ne 0) { Write-Host "STAGING DEPLOY FAILED: api image build failed"; exit 1 }
Write-Host "building pagentos-staging/web:$tag ..."
& $docker build -q -f (Join-Path $tree "infra\docker\web\Dockerfile") `
    --build-arg NEXT_PUBLIC_API_BASE=/api --build-arg PAGENTOS_API_UPSTREAM=http://api:8001 `
    -t "pagentos-staging/web:$tag" $tree
if ($LASTEXITCODE -ne 0) { Write-Host "STAGING DEPLOY FAILED: web image build failed"; exit 1 }
Remove-Item -Recurse -Force $tree

$env:PAGENTOS_STAGING_RELEASE_TAG = $tag
$env:PAGENTOS_STAGING_RELEASE = $full
& powershell -NoProfile -File $upScript -TimeoutSec $TimeoutSec
$upRc = $LASTEXITCODE
if ($upRc -ne 0) { Write-Host "STAGING DEPLOY FAILED: up.ps1 exited $upRc"; exit $upRc }

$stateDir = Join-Path $env:LOCALAPPDATA "PagentOS\staging"
New-Item -ItemType Directory -Force $stateDir | Out-Null
$record = [ordered]@{ sha = $full; ref = $onRef; deployed_at = (Get-Date).ToUniversalTime().ToString("o") }
[IO.File]::WriteAllText((Join-Path $stateDir "deployed.json"), ($record | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
Write-Host "STAGING DEPLOYED: $full at http://127.0.0.1:28000/"
exit 0
