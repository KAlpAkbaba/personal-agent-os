<#
    Shape-safe accessors for owner qualification harnesses (PowerShell 5.1, StrictMode).

    Two PowerShell facts these exist for, both of which have crashed a real owner run:

      1. @($null).Count is 1, not 0 - so an absent list property read as
         @(Get-OptionalProperty ...) looks like a list with one (null) element.
      2. an if-expression UNROLLS a one-element array: $x = if ($c) { <array of 1> } else { @() }
         leaves $x holding the single element, and under StrictMode $x.Count on a
         PSCustomObject throws "The property 'Count' cannot be found on this object."
         That was the 2026-09-06 owner-m18 crash, on the alarm firing's one dispatch result.

    Get-ArrayProperty answers an empty array for absent/null and a real array for everything
    else; ConvertTo-Array makes any value a real array WITHOUT going through an if-expression.
    Use them at every .Count.
#>

function Get-OptionalProperty {
    param($InputObject, [string]$Name)
    if ($null -eq $InputObject) { return $null }
    $prop = $InputObject.PSObject.Properties[$Name]
    if ($null -ne $prop) { return $prop.Value }
    return $null
}

function ConvertTo-Array {
    # Always a real array: $null -> @(), scalar -> @(scalar), array -> the same elements.
    # The leading comma keeps a one-element array from unrolling on the way out.
    param($Value)
    if ($null -eq $Value) { return , @() }
    return , @($Value)
}

function Get-ArrayProperty {
    param($InputObject, [string]$Name)
    return ConvertTo-Array -Value (Get-OptionalProperty -InputObject $InputObject -Name $Name)
}
