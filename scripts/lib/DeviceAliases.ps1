<#
.SYNOPSIS
    The parts of scripts/core/set-device-aliases.ps1 that decide something, as functions a
    test can call (ADR-0205).

.DESCRIPTION
    The first version of that script kept these inline and was never run before the owner
    ran it: it read the device list through @(Get-ArrayProperty ...), which wraps the
    already-wrapped array once more, so two devices arrived as ONE row with no fields and
    "GMKADIRAKBABA" was reported missing from an inventory that contained it. Every
    collection here is therefore exercised at 0, 1 and many in
    scripts/tests/device-aliases.tests.ps1, from JSON parsed the way the script parses it.

    Requires OwnerHarness.ps1 (Get-OptionalProperty, ConvertTo-Array) to be dot-sourced.
#>

function Get-DeviceRows {
    # The rows of a GET /v1/devices document: always a real array, one element per device.
    param($Document)
    $rows = ConvertTo-Array -Value (Get-OptionalProperty -InputObject $Document -Name "devices")
    return , $rows
}

function Get-DeviceAliases {
    param($Row)
    $names = ConvertTo-Array -Value (Get-OptionalProperty -InputObject $Row -Name "aliases")
    return , $names
}

function Select-DeviceRowByName {
    # Exactly one device carries the name, or this throws: a name that matches nothing or
    # two devices must never be resolved by picking one.
    param([object[]]$Rows, [Parameter(Mandatory = $true)][string]$Name)
    $found = New-Object System.Collections.ArrayList
    $seen = New-Object System.Collections.ArrayList
    foreach ($row in (ConvertTo-Array -Value $Rows)) {
        $rowName = [string](Get-OptionalProperty -InputObject $row -Name "name")
        # A row with no name is said, not skipped: that is what a mis-parsed list looks like.
        if ($rowName) { [void]$seen.Add($rowName) } else { [void]$seen.Add("<a row with no name>") }
        if ($rowName -ieq $Name) { [void]$found.Add($row) }
    }
    $foundCount = @($found.ToArray()).Count
    $seenCount = @($seen.ToArray()).Count
    if ($foundCount -ne 1) {
        # The refusal names what WAS listed, so "found 0" can be told apart at a glance:
        # a device that is not enrolled yet, a name typed differently, or a list that was
        # read wrongly (2026-09-28: two devices arrived as one nameless row).
        $listed = if ($seenCount -gt 0) { @($seen.ToArray()) -join ", " } else { "none" }
        throw "expected exactly one device named '$Name', found $foundCount among $seenCount listed ($listed); nothing was changed."
    }
    return $found[0]
}

function ConvertTo-AliasPatchBody {
    # ConvertTo-Json unrolls a one-element array into a scalar on Windows PowerShell 5.1;
    # the list is built by hand so ONE alias is still a list.
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Aliases)
    $quoted = New-Object System.Collections.ArrayList
    foreach ($alias in $Aliases) { [void]$quoted.Add((ConvertTo-Json -InputObject ([string]$alias) -Compress)) }
    return '{"aliases":[' + (@($quoted.ToArray()) -join ",") + ']}'
}

function Get-MissingAliases {
    # The wanted aliases the row does not carry; empty when the read-back agrees.
    param($Row, [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Wanted)
    $have = Get-DeviceAliases -Row $Row
    $missing = New-Object System.Collections.ArrayList
    foreach ($alias in $Wanted) { if ($have -notcontains $alias) { [void]$missing.Add($alias) } }
    return , @($missing.ToArray())
}

function Format-DeviceInventoryLine {
    # One device, one line, and nothing on it that is a secret.
    param($Row)
    $names = Get-DeviceAliases -Row $Row
    return ("  {0}  {1,-16} {2,-8} agent {3,-8} {4,3} capabilities  last seen {5}  aliases [{6}]" -f `
            (Get-OptionalProperty -InputObject $Row -Name "device_id"),
            (Get-OptionalProperty -InputObject $Row -Name "name"),
            (Get-OptionalProperty -InputObject $Row -Name "presence"),
            (Get-OptionalProperty -InputObject $Row -Name "software_version"),
            (Get-OptionalProperty -InputObject $Row -Name "capability_count"),
            (Get-OptionalProperty -InputObject $Row -Name "last_seen_at"),
            ($names -join ", "))
}
