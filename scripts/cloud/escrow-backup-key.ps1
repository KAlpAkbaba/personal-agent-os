<#
.SYNOPSIS
    Put the Cloud Core backup repository's password OFF the host: into this PC's DPAPI
    secret store as PAGENTOS_BACKUP_PASSWORD (ADR-0122).

.DESCRIPTION
    install-backup.sh generates the restic repository password on the host and never prints
    it. If it exists only there, losing the host loses every backup with it - an off-host
    copy included, which is unreadable without it. This reads it once over the tailnet (the
    same root ssh channel releases use), stores it in exactly the format
    scripts\secret-store.ps1 -Set writes (ConvertFrom-SecureString: DPAPI, this user, this
    machine), and proves the stored copy IS the host's by comparing SHA-256 fingerprints
    computed on each side. The value is never printed, logged or written anywhere else.

    To restore on a rebuilt host, the value goes back the way every secret does - on stdin,
    never on a command line.

    Run: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\cloud\escrow-backup-key.ps1
    Exit 0 when escrowed and verified; 1 when the host has no password or the copies differ.
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "pagentos-core",
    [string]$CloudUser = "root",
    [string]$PasswordPath = "/opt/pagentos/backup.password",
    [string]$SshPath = (Join-Path $env:SystemRoot "System32\OpenSSH\ssh.exe"),
    [string]$StoreRoot = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")

$name = "PAGENTOS_BACKUP_PASSWORD"
$target = "$CloudUser@$BrokerHost"
$sshArgs = @("-n", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", $target)

function Get-Sha256Hex {
    param([string]$Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::ASCII.GetBytes($Text)
        return ([BitConverter]::ToString($sha.ComputeHash($bytes)) -replace "-", "").ToLowerInvariant()
    }
    finally { $sha.Dispose() }
}

# The host's fingerprint of the file, computed there. The password file holds no newline
# (install-backup.sh strips it), so the file's hash is the value's hash.
$hostHash = [string](& $SshPath @sshArgs "sha256sum '$PasswordPath' 2>/dev/null | cut -d' ' -f1")
$hostHash = $hostHash.Trim()
if ($LASTEXITCODE -ne 0 -or $hostHash -notmatch '^[0-9a-f]{64}$') {
    Write-Host "the host has no backup password at $PasswordPath (run install-backup.sh there first)"
    exit 1
}

$value = [string](& $SshPath @sshArgs "cat '$PasswordPath'")
$value = $value.Trim()
if ($LASTEXITCODE -ne 0 -or -not $value -or (Get-Sha256Hex -Text $value) -ne $hostHash) {
    $value = $null
    Write-Host "the password read over ssh does not match the host's own fingerprint; nothing was stored"
    exit 1
}

$path = Get-SecretStorePath -Name $name -StoreRoot $StoreRoot
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
$secure = ConvertTo-SecureString -String $value -AsPlainText -Force
$secure | ConvertFrom-SecureString | Set-Content -Path $path -Encoding ASCII
$value = $null
$secure = $null

# Read it back through the same library every other consumer uses, and compare fingerprints.
$stored = Get-StoredSecretValue -Name $name -StoreRoot $StoreRoot
$storedHash = Get-Sha256Hex -Text $stored
$stored = $null
if ($storedHash -ne $hostHash) {
    Write-Host "the stored copy does not match the host's; remove $path and run this again"
    exit 1
}
Write-Host "ESCROWED: $name is in this PC's DPAPI store and matches the host (sha256 $($hostHash.Substring(0, 12))...)"
exit 0
