<#
.SYNOPSIS
    Ship one locally stored secret to the Cloud Core host's /opt/pagentos/.env over
    Tailscale SSH, and restart the API so it takes effect. The value travels on stdin only.

.DESCRIPTION
    The owner action for a provider credential (M12: PAGENTOS_VOICE_OPENAI_API_KEY) is two
    commands, neither of which shows the value anywhere:

        .\scripts\secret-store.ps1 -Set PAGENTOS_VOICE_OPENAI_API_KEY      # masked prompt, DPAPI
        .\scripts\cloud\set-cloud-secret.ps1 -Name PAGENTOS_VOICE_OPENAI_API_KEY

    This script decrypts the stored value (scripts/lib/SecretStore.ps1), opens ONE ssh
    session to the host over the tailnet and writes the value to the remote shell's STDIN.
    The ssh command line, the remote command, this script's output, and every log line
    carry the NAME only. On the host the remote command (root, umask 077) rewrites
    /opt/pagentos/.env atomically (temp file + mv, 0600 root), replacing any existing line
    for that name, then restarts the api service through the same compose invocation the
    deployment uses, and this script verifies from Windows through the tailnet that the
    realtime voice surface now lists the provider.

    Refuses values that could break or escape an env file (whitespace, quotes, '#', '$').
    Never prints a value, never writes a value to a file on this machine, never puts a
    value in a process argument. -DryRun shows exactly what would run, without reading
    the store or opening ssh.

.PARAMETER Name
    The environment-variable name (also the secret-store name).

.PARAMETER ExpectProvider
    After the restart, the health document's checks.voice_realtime.providers must contain
    this name (default: openai-realtime, the M12 adapter). -SkipVerify disables the check.

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

function New-RemoteSecretInstallCommand {
    <#
    .SYNOPSIS
        The bash the host runs. Contains the NAME and paths only - the value arrives on
        stdin. Exit codes: 64 nothing on stdin, 65 unsafe characters, 66 env file missing.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$EnvFile,
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [switch]$Restart
    )
    if (-not (Test-SecretName -Name $Name)) { throw "invalid secret name '$Name'" }
    foreach ($p in @($EnvFile, $RepoRoot)) {
        if ($p -cnotmatch '^/[A-Za-z0-9_./-]+$') { throw "unsafe remote path '$p'" }
    }
    $lines = @(
        'set -eu',
        'umask 077',
        "name='$Name'",
        "envf='$EnvFile'",
        'IFS= read -r value || true',
        # a value piped from Windows PowerShell arrives with CRLF; the CR would otherwise be refused as whitespace
        'value=${value%$''\r''}',
        'if [ -z "$value" ]; then echo "no value received on stdin" >&2; exit 64; fi',
        'case "$value" in *[[:space:]]*|*"#"*|*''"''*|*"''"*|*"$"*|*''\''*) echo "value contains characters unsafe for an env file" >&2; exit 65;; esac',
        'if [ ! -f "$envf" ]; then echo "$envf missing; deploy first" >&2; exit 66; fi',
        'tmp="$(mktemp "$envf.XXXXXX")"',
        'grep -v "^$name=" "$envf" > "$tmp" || true',
        'printf ''%s=%s\n'' "$name" "$value" >> "$tmp"',
        'unset value',
        'chmod 600 "$tmp"',
        'chown root:root "$tmp"',
        'mv -f "$tmp" "$envf"',
        'echo "installed $name into $envf (value not shown)"'
    )
    if ($Restart) {
        $lines += "cd '$RepoRoot/infra/docker'"
        $lines += 'docker compose -f docker-compose.prod.yml --env-file "$envf" up -d --wait api 2>&1 | tail -3'
    }
    return ($lines -join '; ')
}

function Get-RealtimeProviders {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Url)
    $doc = Invoke-RestMethod -Uri $Url -TimeoutSec 20
    $checks = $doc.PSObject.Properties["checks"]
    if ($null -eq $checks) { return @() }
    $rt = $checks.Value.PSObject.Properties["voice_realtime"]
    if ($null -eq $rt) { return @() }
    $providers = $rt.Value.PSObject.Properties["providers"]
    if ($null -eq $providers) { return @() }
    return @($providers.Value)
}

if (-not (Test-SecretName -Name $Name)) {
    throw "secret names must look like environment variables (letters, digits, underscore): '$Name'"
}
if ($CloudUser -cnotmatch '^[A-Za-z_][A-Za-z0-9_-]{0,31}$') { throw "unsafe CloudUser '$CloudUser'" }
if ($BrokerHost -cnotmatch '^[A-Za-z0-9.-]+$') { throw "unsafe BrokerHost '$BrokerHost'" }

$remote = New-RemoteSecretInstallCommand -Name $Name -EnvFile $HostEnvFile -RepoRoot $HostRepoRoot -Restart:(-not $SkipRestart)
# ConvertTo-NativeArgument: 5.1 would otherwise hand ssh.exe the remote command with its
# embedded double quotes unescaped (see scripts/lib/SecretStore.ps1).
$sshArgs = @("-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes", "${CloudUser}@${BrokerHost}", (ConvertTo-NativeArgument -Value $remote))

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
        $value | & $SshPath @sshArgs
        $exit = $LASTEXITCODE
    }
    finally {
        $OutputEncoding = $previousEncoding
    }
}
finally {
    $value = $null
}

switch ($exit) {
    0 { }
    64 { throw "the host received nothing on stdin (exit 64); the value was not installed" }
    65 { throw "the host refused the value as unsafe for an env file (exit 65); nothing was installed" }
    66 { throw "$HostEnvFile does not exist on the host (exit 66); run the deployment first" }
    default { throw "ssh/remote install failed with exit $exit; nothing verified" }
}
Write-Host "installed $Name on the host"

if ($SkipVerify -or $SkipRestart) { exit 0 }

if (-not $HealthUrl) { $HealthUrl = "http://${BrokerHost}:8001/v1/system/health" }
$providers = Get-RealtimeProviders -Url $HealthUrl
if ($providers -contains $ExpectProvider) {
    Write-Host "verified: $HealthUrl lists realtime provider '$ExpectProvider' (providers: $($providers -join ', '))"
    exit 0
}
throw ("the secret is installed but $HealthUrl does not list '$ExpectProvider' (providers: " +
       "$($providers -join ', ')). The API may still be starting, or the value was rejected " +
       "at startup - check the container log with names only: docker logs pagentos-prod-api")
