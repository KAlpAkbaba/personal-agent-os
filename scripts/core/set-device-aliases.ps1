<#
.SYNOPSIS
    Name an enrolled device the way the owner says it: set its aliases on Cloud Core and
    read them back (ADR-0205).

.DESCRIPTION
    The aliases are owner data kept centrally (devices.metadata_json.aliases), never a
    machine name hardcoded anywhere. This signs in as the owner for one short session,
    finds the device by NAME, PATCHes its aliases, reads the inventory back and revokes the
    session again.

    What it prints: the inventory without a single secret - device id, name, presence,
    agent version, capability count, last seen, aliases. No token, no credential, no key.

    The owner credential comes from this account's DPAPI store when it is there, otherwise
    from a hidden prompt. It is never an argument, never written to disk, never printed.

.EXAMPLE
    .\scripts\core\set-device-aliases.ps1 -Device GMKADIRAKBABA -Aliases ofis,is     # type the second one with its cedilla
    .\scripts\core\set-device-aliases.ps1                     # only list the inventory
#>
[CmdletBinding()]
param(
    [string]$Device = "",
    [string[]]$Aliases = @(),
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    [string]$BaseUrl = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
. (Join-Path $repoRoot "scripts\lib\OwnerHarness.ps1")
. (Join-Path $repoRoot "scripts\lib\DeviceAliases.ps1")

if (-not $BaseUrl) { $BaseUrl = "http://${BrokerHost}:$ApiPort" }
if ($Aliases.Count -gt 0 -and -not $Device) { throw "-Aliases needs -Device: say which device gets the names." }

function Write-Inventory {
    param([object[]]$Rows)
    foreach ($row in $Rows) { Write-Host (Format-DeviceInventoryLine -Row $row) }
}

$credential = $null
try { $credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL" } catch { $credential = $null }
if (-not $credential) {
    $secure = Read-Host -Prompt "Cloud Owner Credential (input is hidden; exchanged for one session, then dropped)" -AsSecureString
    if ($secure.Length -eq 0) { throw "empty credential; nothing done." }
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $credential = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}
try {
    $body = @{ owner_credential = $credential; client_kind = "cli"; label = "set-device-aliases"; ttl_s = 300 } | ConvertTo-Json -Compress
    $issued = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body $body
}
finally { $credential = $null; $body = $null }
$headers = @{ Authorization = "Bearer $([string]$issued.token)" }
$sessionId = [string](Get-OptionalProperty -InputObject $issued -Name "session_id")

try {
    $rows = Get-DeviceRows -Document (Invoke-JsonUtf8 -Uri "$BaseUrl/v1/devices" -Headers $headers -TimeoutSec 30)
    Write-Host "devices on $BaseUrl ($($rows.Count)):"
    Write-Inventory -Rows $rows

    if ($Device) {
        $target = Select-DeviceRowByName -Rows $rows -Name $Device
        $deviceId = [string](Get-OptionalProperty -InputObject $target -Name "device_id")

        if ($Aliases.Count -gt 0) {
            $patch = ConvertTo-AliasPatchBody -Aliases $Aliases
            Invoke-JsonUtf8 -Method PATCH -Uri "$BaseUrl/v1/devices/$deviceId" -Headers $headers -Body $patch | Out-Null
            Write-Host "PATCH /v1/devices/$deviceId aliases [$($Aliases -join ', ')]"
        }

        $afterRows = Get-DeviceRows -Document (Invoke-JsonUtf8 -Uri "$BaseUrl/v1/devices" -Headers $headers -TimeoutSec 30)
        Write-Host "read back:"
        Write-Inventory -Rows $afterRows
        if ($Aliases.Count -gt 0) {
            $row = Select-DeviceRowByName -Rows $afterRows -Name $Device
            $missing = Get-MissingAliases -Row $row -Wanted $Aliases
            if ($missing.Count -gt 0) { throw "the read-back does not carry [$($missing -join ', ')]; the aliases were NOT set." }
            Write-Host "ALIASES SET: $Device answers to [$((Get-DeviceAliases -Row $row) -join ', ')]" -ForegroundColor Green
        }
    }
}
finally {
    if ($sessionId) {
        try { Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions/$sessionId/revoke" -Headers $headers -Body "{}" | Out-Null; Write-Host "session revoked" }
        catch { Write-Host "session NOT revoked; it expires within 300 s" -ForegroundColor Yellow }
    }
    $headers = $null
}
