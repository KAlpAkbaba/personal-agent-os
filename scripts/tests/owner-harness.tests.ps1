<#
    scripts\lib\OwnerHarness.ps1: the two PowerShell shape traps that crashed a real owner run.
    Run: powershell -NoProfile -File scripts\tests\owner-harness.tests.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\OwnerHarness.ps1")

$script:Failures = 0
$script:Passes = 0

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Passes++; Write-Host "  PASS  $Name" }
    catch { $script:Failures++; Write-Host "  FAIL  $Name" -ForegroundColor Red; Write-Host "        $($_.Exception.Message)" -ForegroundColor Red }
}
function Assert-Equal { param($Expected, $Actual, [string]$Message) if ("$Expected" -ne "$Actual") { throw "$Message (expected '$Expected', got '$Actual')" } }

$firingOne = [pscustomobject]@{ dispatch_results = @([pscustomobject]@{ kind = "alarm"; status = "succeeded" }) }
$firingTwo = [pscustomobject]@{ dispatch_results = @([pscustomobject]@{ kind = "alarm" }, [pscustomobject]@{ kind = "voice_briefing" }) }
$firingNone = [pscustomobject]@{ dispatch_results = @() }
$firingAbsent = [pscustomobject]@{ status = "skipped" }

Test-Case "absent property is an empty array, Count 0 (not @(null).Count = 1)" {
    $a = Get-ArrayProperty -InputObject $firingAbsent -Name "dispatch_results"
    Assert-Equal 0 $a.Count "count"
}
Test-Case "null input is an empty array" {
    $a = Get-ArrayProperty -InputObject $null -Name "x"
    Assert-Equal 0 $a.Count "count"
}
Test-Case "empty list stays empty" {
    $a = Get-ArrayProperty -InputObject $firingNone -Name "dispatch_results"
    Assert-Equal 0 $a.Count "count"
}
Test-Case "ONE result (the alarm case) is an array of one, and .Count works under StrictMode" {
    $a = Get-ArrayProperty -InputObject $firingOne -Name "dispatch_results"
    Assert-Equal 1 $a.Count "count"
    Assert-Equal "alarm" $a[0].kind "element"
}
Test-Case "two results count two" {
    $a = Get-ArrayProperty -InputObject $firingTwo -Name "dispatch_results"
    Assert-Equal 2 $a.Count "count"
}
Test-Case "a scalar value becomes a one-element array" {
    $a = ConvertTo-Array -Value "x"
    Assert-Equal 1 $a.Count "count"
}
Test-Case "the trap, reproduced: a PARENTHESISED call inside an if-expression unrolls a one-element array" {
    # Exactly the committed harness line. Without the parentheses the array survives; with
    # them PowerShell 5.1 enumerates it into the pipeline and the single element comes out.
    $x = if ($true) { (Get-ArrayProperty -InputObject $firingOne -Name "dispatch_results") } else { @() }
    if ($x -is [array]) { throw "expected the if-expression to unroll the array (PowerShell 5.1 semantics changed?)" }
    $threw = $false
    try { $null = $x.Count } catch { $threw = $true }
    if (-not $threw) { throw "expected .Count on the unrolled PSCustomObject to throw under StrictMode" }
}
Test-Case "the fix: direct assignment keeps the array, so the same read is safe" {
    $x = Get-ArrayProperty -InputObject $firingOne -Name "dispatch_results"
    if (-not ($x -is [array])) { throw "expected an array" }
    Assert-Equal 1 $x.Count "count"
}

Write-Host ""
Write-Host "owner-harness tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
