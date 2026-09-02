<#
.SYNOPSIS
    Ship one locally stored secret to the Cloud Core host's /opt/pagentos/.env over
    Tailscale SSH and prove the running api workload carries it. The value travels on
    stdin only.

.DESCRIPTION
    The owner action for a provider credential (M12: PAGENTOS_VOICE_OPENAI_API_KEY) is:

        .\scripts\secret-store.ps1 -Set PAGENTOS_VOICE_OPENAI_API_KEY      # masked prompt, DPAPI
        .\scripts\cloud\set-cloud-secret.ps1 -Name PAGENTOS_VOICE_OPENAI_API_KEY

    This script decrypts the stored value (scripts/lib/SecretStore.ps1), opens ONE ssh
    session to the host over the tailnet and writes the value to the remote script's
    STDIN. The host side is scripts/cloud/install-env-secret.sh (shipped by the release),
    which runs the deployment transaction (ADR-0042):

        atomic env-file update (temp + mv, 0600 root) -> posture verification
        -> docker compose config validation -> the compose must WIRE the variable
        -> recreate ONLY the api workload (--no-deps --force-recreate --wait)
        -> the variable is PRESENT inside the actual running container (else FAIL)
        -> /v1/system/health lists the expected provider
        -> one real provider call from the Hetzner host

    The ssh command line, the remote command, this script's output and every log line
    carry the NAME only; runtime verification reports PRESENT/MISSING, the length and a
    SHA-256 fingerprint. A restart that changed nothing is a FAILURE (exit 68), and a host
    tree whose compose does not wire the variable is refused (exit 67: release first).

.PARAMETER ExpectProvider
    After the recreate, checks.voice_realtime.providers in the health document must
    contain this name (default: openai-realtime). Empty string disables the check.

.PARAMETER VerifyCommand
    Command run INSIDE the api container as the real provider self-test. Default for
    PAGENTOS_VOICE_OPENAI_API_KEY: one minimal client-secret mint via realtime_smoke.py.

.EXAMPLE
    .\scripts\cloud\set-cloud-secret.ps1 -Name PAGENTOS_VOICE_OPENAI_API_KEY
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Name,

    [string]$BrokerHost = "pagentos-core",
    [string]$CloudUser = "root",
    [string]$HostRepoRoot = "/opt/pagentos/app",
    [string]$HostEnvFile = "/opt/pagentos/.env",
    [string]$ExpectProvider = "openai-realtime",
    [AllowEmptyString()][string]$VerifyCommand = "<default>",
    [string]$HealthUrl = "",
    [string]$SshPath = (Join-Path $env:SystemRoot "System32\OpenSSH\ssh.exe"),
    [string]$StoreRoot = "",
    [switch]$SkipRestart,
    [switch]$SkipVerify,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "..\lib\SecretStore.ps1")
. (Join-Path $PSScriptRoot "..\lib\NativeProcess.ps1")

$defaultVerify = @{ "PAGENTOS_VOICE_OPENAI_API_KEY" = "uv run python scripts/realtime_smoke.py --mode minimal" }

# The remote command builder lives in scripts/lib/SecretStore.ps1 (New-RemoteSecretInstallCommand)
# so the test suite asserts the exact bytes the host receives.

if (-not (Test-SecretName -Name $Name)) {
    throw "secret names must look like environment variables (letters, digits, underscore): '$Name'"
}
if ($CloudUser -cnotmatch '^[A-Za-z_][A-Za-z0-9_-]{0,31}$') { throw "unsafe CloudUser '$CloudUser'" }
if ($BrokerHost -cnotmatch '^[A-Za-z0-9.-]+$') { throw "unsafe BrokerHost '$BrokerHost'" }
if ($VerifyCommand -eq "<default>") {
    # A [string] parameter cannot carry $null (5.1 coerces it to ""), so the sentinel
    # means "the per-name default": one minimal client-secret mint for the M12 key.
    $VerifyCommand = if ($defaultVerify.ContainsKey($Name)) { $defaultVerify[$Name] } else { "" }
}
$expect = if ($SkipVerify) { "" } else { $ExpectProvider }
$verify = if ($SkipVerify -or $SkipRestart) { "" } else { $VerifyCommand }

$remote = New-RemoteSecretInstallCommand -Name $Name -EnvFile $HostEnvFile -RepoRoot $HostRepoRoot `
    -ExpectProvider $expect -Verify $verify -Restart:(-not $SkipRestart)
# ConvertTo-NativeCallArgument: 5.1 would otherwise hand ssh.exe the remote command with its
# embedded quotes unescaped (see scripts/lib/SecretStore.ps1).
$sshArgs = @("-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes", "${CloudUser}@${BrokerHost}",
             (ConvertTo-NativeCallArgument -Value $remote))

Write-Host "set-cloud-secret: $Name -> ${CloudUser}@${BrokerHost}:$HostEnvFile (value travels on stdin only)"
if ($DryRun) {
    Write-Host "DRY RUN - nothing read from the store, no ssh opened. Would run:"
    Write-Host ("  " + $SshPath + " " + (($sshArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '))
    exit 0
}

if (-not (Test-Path -LiteralPath $SshPath)) { throw "ssh client not found at $SshPath" }

$value = Get-StoredSecretValue -Name $Name -StoreRoot $StoreRoot
try {
    if ($value -match '[\s"''#$\\]') {
        throw "the stored value for $Name contains whitespace or quoting characters and cannot go into an env file; re-enter it"
    }
    # The pipeline is the ONLY channel that carries the value. $OutputEncoding governs what a
    # native process receives on stdin under Windows PowerShell 5.1.
    $previousEncoding = $OutputEncoding
    $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    try {
        $value | & $SshPath @sshArgs | ForEach-Object {
            if ($_ -inotmatch 'bearer|sk-[A-Za-z0-9]') { Write-Host "  host: $_" }
        }
        $exit = $LASTEXITCODE
    }
    finally {
        $OutputEncoding = $previousEncoding
    }
}
finally {
    $value = $null
}

$meanings = @{
    64  = "the host received nothing on stdin; the value was not installed"
    65  = "the host refused the value or name as unsafe for an env file; nothing was installed"
    66  = "$HostEnvFile does not exist on the host; run the deployment first"
    67  = "the host's compose does not wire $Name into any service, so a restart would change nothing. Release the current Cloud Core first: .\scripts\cloud\release-cloud-core.ps1"
    68  = "$Name is in $HostEnvFile but MISSING inside the recreated api container - the workload did not pick it up"
    69  = "the api came up but /v1/system/health does not list '$ExpectProvider'"
    70  = "the real provider self-test from the host FAILED (details above; no secret shown)"
    71  = "docker compose config is INVALID on the host; nothing was recreated"
    72  = "the host env file is not 600 root:root after the update"
    127 = "the host tree has no scripts/cloud/install-env-secret.sh yet. Release the current Cloud Core first: .\scripts\cloud\release-cloud-core.ps1"
    255 = "ssh could not reach ${CloudUser}@${BrokerHost} (Tailscale up? key-based auth in BatchMode?)"
}
if ($exit -ne 0) {
    $why = if ($meanings.ContainsKey($exit)) { $meanings[$exit] } else { "ssh/remote install failed with exit $exit; nothing verified" }
    throw "set-cloud-secret FAILED (exit $exit): $why"
}
Write-Host "installed $Name on the host and verified inside the running api workload"

if ($SkipVerify -or $SkipRestart) { exit 0 }

if (-not $HealthUrl) { $HealthUrl = "http://${BrokerHost}:8001/v1/system/health" }
$doc = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 20
$checks = Get-OptionalProperty -InputObject $doc -Name "checks"
$rt = if ($null -ne $checks) { Get-OptionalProperty -InputObject $checks -Name "voice_realtime" } else { $null }
$providers = if ($null -ne $rt) { @(Get-OptionalProperty -InputObject $rt -Name "providers") } else { @() }
if ($ExpectProvider -and ($providers -notcontains $ExpectProvider)) {
    throw ("the host verified the secret, but $HealthUrl seen from this machine does not list " +
           "'$ExpectProvider' (providers: $($providers -join ', '))")
}
Write-Host "verified from this machine over the tailnet: $HealthUrl lists realtime providers [$($providers -join ', ')]"
exit 0
