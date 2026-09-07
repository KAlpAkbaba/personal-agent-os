<#
.SYNOPSIS
    Release the committed tree (HEAD) to the Hetzner Cloud Core over Tailscale SSH and
    prove the api workload runs it: build, migrate, recreate ONLY the api container,
    verify health, the realtime provider key inside the container, and one real provider
    call from the host (ADR-0042).

.DESCRIPTION
    The host runs a COPY of the repository at /opt/pagentos/app (not a git checkout). A
    release is therefore: `git archive HEAD` here -> scp -> extract to /opt/pagentos/app.next
    -> scripts/cloud/release-cloud-core.sh on the host, which validates the new compose
    against the host's env file BEFORE touching anything, swaps the tree (previous kept at
    app.prev, previous image kept as pagentos/cloud-core:prev), builds, runs alembic
    upgrade head, recreates the api workload with --no-deps --force-recreate --wait
    (PostgreSQL/Redis/MinIO/Temporal are never rebuilt or recreated), checks health, and -
    when PAGENTOS_VOICE_OPENAI_API_KEY is on the host - proves it is PRESENT inside the
    running container (length + SHA-256 fingerprint only), that /v1/system/health lists
    openai-realtime, and that one real client-secret mint from the host succeeds. Any
    failure after the swap rolls back tree and image and recreates the api.

    Nothing here prints a secret. Only committed content ships (git archive), so neither
    .env nor any local file can leak into the host tree.

.PARAMETER Preflight
    Ship and validate only (compose config against the host env file, posture, wiring);
    remove the staged tree; change nothing.

.EXAMPLE
    .\scripts\cloud\release-cloud-core.ps1            # release HEAD
    .\scripts\cloud\release-cloud-core.ps1 -Preflight # prove the release would work
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "pagentos-core",
    [string]$CloudUser = "root",
    [string]$HostBase = "/opt/pagentos",
    [string]$HealthUrl = "",
    [string]$SshPath = (Join-Path $env:SystemRoot "System32\OpenSSH\ssh.exe"),
    [string]$ScpPath = (Join-Path $env:SystemRoot "System32\OpenSSH\scp.exe"),
    # Bounded remote calls (2026-09-06): a connect that cannot complete fails in 20 s, and a
    # session whose peer stops answering is dropped after ~60 s of silence, instead of holding
    # the owner's terminal forever. The host-side transaction itself is unaffected - it runs
    # under nohup-less bash on the host and either finishes or rolls back on its own.
    [int]$SshConnectTimeoutSec = 20,
    [string]$GitPath = "git",
    [string]$RepoRoot = "",
    [switch]$Preflight,
    [switch]$AllowDirty,
    [switch]$SkipVerify,
    [switch]$VerifyOnly,
    [switch]$Force,
    [switch]$DryRun,
    # M18.4 (ADR-0081): run the zero-downtime host script (two api colours behind the edge)
    # instead of the single-container recreate. Opt-in until the first cutover is proven.
    [switch]$BlueGreen,
    # Ship HEAD to <HostBase>/app.next and stop: no host transaction. The M18.4 qualification
    # harness uses it to stage a real candidate tree before a controlled interruption
    # (release-cloud-core-bluegreen.sh with PAGENTOS_INTERRUPT_AT), which it drives itself.
    [switch]$StageOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "..\lib\SecretStore.ps1")
. (Join-Path $PSScriptRoot "..\lib\NativeProcess.ps1")

$exitMeanings = @{
    65  = "the staged tree was missing on the host"
    66  = "$HostBase/.env is missing on the host; run the first deployment (deploy-cloud-core.sh) first"
    67  = "the compose definition does not wire the variable"
    68  = "the realtime provider key is on the host but MISSING inside the recreated api container"
    69  = "the api came up but /v1/system/health does not list openai-realtime"
    70  = "the real provider self-test from the host FAILED (see the message above; no secret is shown)"
    71  = "docker compose config is INVALID for this tree; nothing was changed"
    72  = "the host env file is not 600 root:root"
    73  = "the api came up serving an OLDER realtime contract than the tree declares; rolled back"
    74  = "a candidate voice (marin/cedar) was not echoed unchanged by the provider; rolled back"
    75  = "the idle colour never answered health; rolled back"
    76  = "the served release did not match the sha; rolled back"
    77  = "the edge (nginx) reload failed; rolled back"
    78  = "the active colour marker on the host is neither blue nor green"
    79  = "the device sessions did not arrive on the new colour within PAGENTOS_HANDOFF_WAIT_S; rolled back (M18.4 gap 1)"
    127 = "the host tree has no scripts/cloud/release-cloud-core.sh (the archive did not extract)"
    255 = "ssh could not reach ${CloudUser}@${BrokerHost} (Tailscale up? key-based auth in BatchMode?)"
}

function New-RemoteReleaseCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Sha,
        [Parameter(Mandatory = $true)][string]$RemoteTar,
        [Parameter(Mandatory = $true)][string]$Base,
        [switch]$PreflightOnly,
        [switch]$BlueGreen,
        [switch]$StageOnly
    )
    foreach ($p in @($RemoteTar, $Base)) {
        if ($p -cnotmatch '^/[A-Za-z0-9_./-]+$') { throw "unsafe remote path '$p'" }
    }
    if ($Sha -cnotmatch '^[0-9a-f]{40}$') { throw "not a full commit sha: '$Sha'" }
    $mode = if ($PreflightOnly) { " --preflight" } else { "" }
    $lines = @(
        'set -eu',
        "rm -rf '$Base/app.next'",
        "mkdir -p '$Base/app.next'",
        "tar -xf '$RemoteTar' -C '$Base/app.next'",
        "rm -f '$RemoteTar'"
    )
    if ($StageOnly) {
        $lines += "echo 'staged $Sha at $Base/app.next (no host transaction)'"
    }
    else {
        $lines += "bash '$Base/app.next/scripts/cloud/$(if ($BlueGreen) { 'release-cloud-core-bluegreen.sh' } else { 'release-cloud-core.sh' })' $Sha$mode"
    }
    return ($lines -join '; ')
}

if ($CloudUser -cnotmatch '^[A-Za-z_][A-Za-z0-9_-]{0,31}$') { throw "unsafe CloudUser '$CloudUser'" }
if ($BrokerHost -cnotmatch '^[A-Za-z0-9.-]+$') { throw "unsafe BrokerHost '$BrokerHost'" }
# $PSScriptRoot is not yet set while parameter defaults are evaluated under 5.1.
if (-not $RepoRoot) { $RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }

Push-Location $RepoRoot
try {
    # Never `| Select-Object -First 1` on a native command: ending the pipeline early can
    # leave the tool with a non-zero exit (a race that only showed on the CI runner).
    $revParse = @(& $GitPath rev-parse HEAD 2>$null)
    if ($LASTEXITCODE -ne 0 -or $revParse.Count -eq 0 -or -not $revParse[0]) { throw "git rev-parse HEAD failed in $RepoRoot" }
    $sha = ([string]$revParse[0]).Trim()
    $short = $sha.Substring(0, 7)
    $dirty = @(& $GitPath status --porcelain --untracked-files=no 2>$null)
    if ($dirty.Count -gt 0 -and -not $AllowDirty -and -not $VerifyOnly) {
        throw ("the working tree has $($dirty.Count) uncommitted change(s); a release ships HEAD only. " +
               "Commit first, or -AllowDirty to ship HEAD without them.")
    }
    $localTar = Join-Path $env:TEMP "pagentos-release-$short.tar"
    $remoteTar = "/tmp/pagentos-release-$short.tar"
    $remote = New-RemoteReleaseCommand -Sha $sha -RemoteTar $remoteTar -Base $HostBase -PreflightOnly:$Preflight -BlueGreen:$BlueGreen -StageOnly:$StageOnly
    $target = "${CloudUser}@${BrokerHost}"

    Write-Host "release-cloud-core: HEAD $short -> $target ($(if ($StageOnly) { 'STAGE ONLY: ship the tree, no transaction' } elseif ($Preflight) { 'PREFLIGHT: validate only' } else { 'RELEASE' }))"
    if ($DryRun) {
        Write-Host "DRY RUN - nothing archived, no scp/ssh. Would run:"
        Write-Host "  $GitPath archive --format=tar -o $localTar HEAD"
        Write-Host "  $ScpPath -o BatchMode=yes $localTar ${target}:$remoteTar"
        Write-Host "  $SshPath -o BatchMode=yes $target `"$remote`""
        exit 0
    }
    foreach ($tool in @($SshPath, $ScpPath)) {
        if (-not (Test-Path -LiteralPath $tool)) { throw "OpenSSH client not found at $tool" }
    }

    # Idempotent: a release that already committed on the host (RELEASE marker == HEAD) is
    # not repeated - only the local verification/report runs. -VerifyOnly forces that
    # path; -Force repeats the release anyway.
    $alreadyReleased = $false
    if (-not $Preflight -and -not $StageOnly) {
        $markerLines = @($null | & $SshPath -n -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=$SshConnectTimeoutSec -o ServerAliveInterval=15 -o ServerAliveCountMax=4 $target "cat '$HostBase/app/RELEASE' 2>/dev/null || true")
        $marker = if ($markerLines.Count -gt 0) { ([string]$markerLines[0]).Trim() } else { "" }
        if ($marker -eq $sha) { $alreadyReleased = $true }
        if ($VerifyOnly -and -not $alreadyReleased) {
            throw "-VerifyOnly: the host runs '$(if ($marker) { $marker.Substring(0, 7) } else { 'no RELEASE marker' })', not HEAD $short; run a release first"
        }
    }
    if ($alreadyReleased -and -not $Force) {
        Write-Host "already released: the host's RELEASE marker is HEAD $short; skipping archive/upload/host transaction, verifying only"
    }
    elseif ($VerifyOnly) {
        throw "-VerifyOnly requires the host to run HEAD"
    }
    else {

    & $GitPath archive --format=tar -o $localTar HEAD
    if ($LASTEXITCODE -ne 0) { throw "git archive failed" }
    $size = [Math]::Round((Get-Item -LiteralPath $localTar).Length / 1MB, 1)
    Write-Host "archived HEAD $short ($size MB, committed content only)"
    try {
        # Nothing here needs stdin; hand the child a closed one so it can never block on
        # an inherited pipe (ssh gets -n for the same reason).
        $null | & $ScpPath -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=$SshConnectTimeoutSec -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -q $localTar "${target}:$remoteTar"
        if ($LASTEXITCODE -ne 0) { throw "scp failed with exit $LASTEXITCODE; nothing changed on the host" }
        Write-Host "uploaded to ${target}:$remoteTar"
    }
    finally {
        Remove-Item -LiteralPath $localTar -Force -ErrorAction SilentlyContinue
    }

    $null | & $SshPath -n -o StrictHostKeyChecking=accept-new -o BatchMode=yes -o ConnectTimeout=$SshConnectTimeoutSec -o ServerAliveInterval=15 -o ServerAliveCountMax=4 $target (ConvertTo-NativeCallArgument -Value $remote) |
        ForEach-Object { if ($_ -inotmatch 'bearer|sk-[A-Za-z0-9]') { Write-Host "  host: $_" } }
    $exit = $LASTEXITCODE
    if ($exit -ne 0) {
        $why = if ($exitMeanings.ContainsKey($exit)) { $exitMeanings[$exit] } else { "the host reported exit $exit" }
        throw "release FAILED (exit $exit): $why"
    }
    if ($Preflight) {
        Write-Host "preflight OK: the host can take $short; nothing was changed"
        exit 0
    }
    if ($StageOnly) {
        Write-Host "staged: $short is at $HostBase/app.next on $BrokerHost; nothing else was changed"
        exit 0
    }

    }  # end of the release branch

    if (-not $SkipVerify) {
        $releaseLines = @($null | & $SshPath -n -o BatchMode=yes -o ConnectTimeout=$SshConnectTimeoutSec -o ServerAliveInterval=15 -o ServerAliveCountMax=4 $target "cat '$HostBase/app/RELEASE'")
        $releasedSha = if ($releaseLines.Count -gt 0) { ([string]$releaseLines[0]).Trim() } else { "" }
        if ($LASTEXITCODE -ne 0 -or (-not $releasedSha) -or $releasedSha -ne $sha) {
            throw "post-release check: $HostBase/app/RELEASE is '$releasedSha', expected $sha"
        }
        if (-not $HealthUrl) { $HealthUrl = "http://${BrokerHost}:8001/v1/system/health" }
        $doc = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 20
        $status = Get-OptionalProperty -InputObject $doc -Name "status"
        $checks = Get-OptionalProperty -InputObject $doc -Name "checks"
        $rt = if ($null -ne $checks) { Get-OptionalProperty -InputObject $checks -Name "voice_realtime" } else { $null }
        $providers = if ($null -ne $rt) { @(Get-OptionalProperty -InputObject $rt -Name "providers") } else { @() }
        $contract = if ($null -ne $rt) { Get-OptionalProperty -InputObject $rt -Name "contract_version" } else { $null }
        Write-Host "verified from this machine over the tailnet: $HealthUrl status=$status realtime providers=[$($providers -join ', ')] contract_version=$(if ($null -eq $contract) { 'absent' } else { $contract })"
    }
    Write-Host "RELEASE OK: $short is running on $BrokerHost"
    exit 0
}
finally {
    Pop-Location
}
