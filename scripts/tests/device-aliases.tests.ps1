<#
.SYNOPSIS
    scripts/lib/DeviceAliases.ps1 at 0, 1 and many, from JSON parsed the way
    set-device-aliases.ps1 parses it (ADR-0205).

.DESCRIPTION
    2026-09-28: the owner ran set-device-aliases.ps1 against production and was told
    "expected exactly one device named 'GMKADIRAKBABA', found 0" by an inventory of two
    devices, printed as one row with no fields. The script had never been run. Nothing here
    touches the network or a credential.

    Run: powershell -NoProfile -File scripts\tests\device-aliases.tests.ps1
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\OwnerHarness.ps1")
. (Join-Path $repoRoot "scripts\lib\DeviceAliases.ps1")

$script:Failures = 0
$script:Passes = 0

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Passes++; Write-Host "  PASS  $Name" }
    catch {
        $script:Failures++
        Write-Host "  FAIL  $Name" -ForegroundColor Red
        Write-Host "        $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Assert-Equal {
    param($Expected, $Actual, [string]$Because)
    if ($Expected -ne $Actual) { throw "$Because`n          expected: <$Expected>`n          actual  : <$Actual>" }
}

function Assert-True {
    param([bool]$Condition, [string]$Because)
    if (-not $Condition) { throw $Because }
}

# The production shape (GET /v1/devices), with the fields the script reads.
$sCedilla = [string][char]0x015F
$home1 = '{"device_id":"3f60fdb5-5022-48cf-bb3c-d7192466b701","name":"MAIL","presence":"online","software_version":"0.6.0","capability_count":105,"last_seen_at":"2026-09-28T13:56:44Z","aliases":["ev"]}'
$office = '{"device_id":"9efa9d8b-b0e6-4758-a03a-387c3e20a0d2","name":"GMKADIRAKBABA","presence":"online","software_version":"0.6.0","capability_count":13,"last_seen_at":"2026-09-28T13:56:47Z","aliases":[]}'
$none = ConvertFrom-Json '{"devices":[]}'
$one = ConvertFrom-Json ('{"devices":[' + $home1 + ']}')
$two = ConvertFrom-Json ('{"devices":[' + $home1 + ',' + $office + ']}')

Write-Host ""
Write-Host "the device list"

Test-Case "two devices are two rows, each with its fields (the owner's run saw one empty row)" {
    $rows = Get-DeviceRows -Document $two
    Assert-Equal -Expected 2 -Actual $rows.Count -Because "one row per device"
    Assert-Equal -Expected "MAIL" -Actual ([string]$rows[0].name) -Because "the first row is a device, not the list"
    Assert-Equal -Expected "GMKADIRAKBABA" -Actual ([string]$rows[1].name) -Because "the second row is a device"
}

Test-Case "one device is an array of one; none is an empty array" {
    $rows = Get-DeviceRows -Document $one
    Assert-Equal -Expected 1 -Actual $rows.Count -Because "a single device must not unroll into its properties"
    Assert-Equal -Expected "MAIL" -Actual ([string]$rows[0].name) -Because "and it is that device"
    Assert-Equal -Expected 0 -Actual (Get-DeviceRows -Document $none).Count -Because "no devices"
    Assert-Equal -Expected 0 -Actual (Get-DeviceRows -Document $null).Count -Because "no document"
}

Write-Host ""
Write-Host "finding the device by name"

Test-Case "the named device is found among two, case-insensitively" {
    $rows = Get-DeviceRows -Document $two
    $row = Select-DeviceRowByName -Rows $rows -Name "gmkadirakbaba"
    Assert-Equal -Expected "9efa9d8b-b0e6-4758-a03a-387c3e20a0d2" -Actual ([string]$row.device_id) -Because "the office machine"
}

Test-Case "the named device is found when it is the only one" {
    $row = Select-DeviceRowByName -Rows (Get-DeviceRows -Document $one) -Name "MAIL"
    Assert-Equal -Expected "3f60fdb5-5022-48cf-bb3c-d7192466b701" -Actual ([string]$row.device_id) -Because "the home machine"
}

Test-Case "a name nobody carries, and a name two devices carry, are both refused" {
    $threw = $false
    try { Select-DeviceRowByName -Rows (Get-DeviceRows -Document $two) -Name "LAPTOP" | Out-Null } catch { $threw = ($_.Exception.Message -match "found 0") }
    Assert-True -Condition $threw -Because "an unknown name must throw, naming the count"
    $twins = ConvertFrom-Json ('{"devices":[' + $home1 + ',' + $home1 + ']}')
    $threw = $false
    try { Select-DeviceRowByName -Rows (Get-DeviceRows -Document $twins) -Name "MAIL" | Out-Null } catch { $threw = ($_.Exception.Message -match "found 2") }
    Assert-True -Condition $threw -Because "two devices with one name must throw, never pick one"
    $threw = $false
    try { Select-DeviceRowByName -Rows (Get-DeviceRows -Document $none) -Name "MAIL" | Out-Null } catch { $threw = $true }
    Assert-True -Condition $threw -Because "an empty inventory has no such device"
}

Write-Host ""
Write-Host "the PATCH body"

Test-Case "one alias is still a list; two are two; Turkish letters survive as JSON" {
    Assert-Equal -Expected '{"aliases":["ofis"]}' -Actual (ConvertTo-AliasPatchBody -Aliases @("ofis")) -Because "ConvertTo-Json would have written a bare string"
    $body = ConvertTo-AliasPatchBody -Aliases @("ofis", ("i" + $sCedilla))
    $back = ConvertFrom-Json $body
    Assert-Equal -Expected 2 -Actual @($back.aliases).Count -Because $body
    Assert-Equal -Expected ("i" + $sCedilla) -Actual ([string]@($back.aliases)[1]) -Because "the cedilla must round-trip: $body"
    Assert-Equal -Expected '{"aliases":[]}' -Actual (ConvertTo-AliasPatchBody -Aliases @()) -Because "clearing the aliases is an empty list"
}

Test-Case "a quote inside an alias cannot break out of the body" {
    $back = ConvertFrom-Json (ConvertTo-AliasPatchBody -Aliases @('o"fis'))
    Assert-Equal -Expected 'o"fis' -Actual ([string]@($back.aliases)[0]) -Because "escaped, not injected"
}

Write-Host ""
Write-Host "the read-back"

Test-Case "missing aliases are named; a row that carries them all reports none" {
    $rows = Get-DeviceRows -Document $two
    $missing = Get-MissingAliases -Row $rows[1] -Wanted @("ofis", "is")
    Assert-Equal -Expected 2 -Actual $missing.Count -Because "the office row carries no alias yet"
    $missing = Get-MissingAliases -Row $rows[0] -Wanted @("ev")
    Assert-Equal -Expected 0 -Actual $missing.Count -Because "the home row carries 'ev'"
    $missing = Get-MissingAliases -Row $rows[0] -Wanted @("ev", "ofis")
    Assert-Equal -Expected 1 -Actual $missing.Count -Because "one of two is missing"
    Assert-Equal -Expected "ofis" -Actual ([string]$missing[0]) -Because "and it is named"
}

Write-Host ""
Write-Host "what is printed"

Test-Case "an inventory line carries the id, the name and the aliases - not a type name" {
    $rows = Get-DeviceRows -Document $two
    $line = Format-DeviceInventoryLine -Row $rows[0]
    Assert-True -Condition ($line -match "3f60fdb5-5022-48cf-bb3c-d7192466b701") -Because $line
    Assert-True -Condition ($line -match "MAIL") -Because $line
    Assert-True -Condition ($line -match "105 capabilities") -Because $line
    Assert-True -Condition ($line -match "aliases \[ev\]") -Because $line
    Assert-True -Condition ($line -notmatch "System\.Object") -Because "the owner's run printed 'aliases [System.Object[]]': $line"
    $empty = Format-DeviceInventoryLine -Row $rows[1]
    Assert-True -Condition ($empty -match "aliases \[\]") -Because "no aliases prints as an empty list: $empty"
}

Test-Case "the script itself reads the list through the library, never through a second wrap" {
    $source = Get-Content -LiteralPath (Join-Path $repoRoot "scripts\core\set-device-aliases.ps1") -Raw
    Assert-True -Condition ($source -match "DeviceAliases\.ps1") -Because "the script must dot-source the library"
    Assert-True -Condition ($source -match "Get-DeviceRows") -Because "the list is read by the tested function"
    Assert-True -Condition ($source -notmatch "@\(\s*Get-ArrayProperty") -Because "@(Get-ArrayProperty ...) wraps the wrapped array: the defect of 2026-09-28"
}

Write-Host ""
Write-Host "$($script:Passes) passed, $($script:Failures) failed"
exit $script:Failures
