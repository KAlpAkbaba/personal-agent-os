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

# owner-m18.ps1 v2 wraps Get-ArrayProperty in its own helpers (Get-LedgerSince,
# Get-WebSessionsSinceBaseline) that `return , (...)`. The same three shapes, through that
# extra layer, read the way the harness reads them: assigned first, counted second.
function Get-RowsLike {
    param($Doc)
    return , (Get-ArrayProperty -InputObject $Doc -Name "events")
}
$docNone = [pscustomobject]@{ events = @() }
$docOne = [pscustomobject]@{ events = @([pscustomobject]@{ event_type = "eye.disabled"; detail_json = [pscustomobject]@{ reason = "voice:gozunu kapat" } }) }
$docTwo = [pscustomobject]@{ events = @([pscustomobject]@{ event_type = "a" }, [pscustomobject]@{ event_type = "b" }) }

Test-Case "a wrapping helper that returns , (Get-ArrayProperty ...) keeps zero rows countable" {
    $rows = Get-RowsLike -Doc $docNone
    Assert-Equal 0 $rows.Count "count"
}
Test-Case "a wrapping helper keeps ONE row an array (the eye.disabled-by-voice case)" {
    $rows = Get-RowsLike -Doc $docOne
    if (-not ($rows -is [array])) { throw "expected an array" }
    Assert-Equal 1 $rows.Count "count"
    $viaVoice = @($rows | Where-Object { ([string]$_.detail_json.reason).StartsWith("voice:") })
    Assert-Equal 1 $viaVoice.Count "filtered"
}
Test-Case "a wrapping helper keeps two rows" {
    $rows = Get-RowsLike -Doc $docTwo
    Assert-Equal 2 $rows.Count "count"
}
Test-Case "the third trap: a , @(...) return PIPED directly arrives as ONE item (the whole array)" {
    # 2026-09-06, scripts\lib\VoiceShell.ps1: `Get-Rows | Where-Object { $_.name -eq 'x' }`
    # matched nothing, because $_ was the array itself and $_.name a list of names.
    $items = 0
    Get-RowsLike -Doc $docTwo | ForEach-Object { $items++ }
    Assert-Equal 1 $items "piped directly: one pipeline item"
    $direct = @(Get-RowsLike -Doc $docTwo | Where-Object { [string]$_.event_type -eq "a" })
    Assert-Equal 0 $direct.Count "so a per-row filter over the direct pipe finds nothing"
    # The fix, and the rule: assign first, pipe the variable.
    $rows = Get-RowsLike -Doc $docTwo
    $viaVariable = @($rows | Where-Object { [string]$_.event_type -eq "a" })
    Assert-Equal 1 $viaVariable.Count "assigned first, the filter sees each row"
}
Test-Case "the fourth trap: an EMPTY array through an if-expression is null, and @(helper-call) wraps the whole array as one element" {
    # 2026-09-06, owner-m18-eye.ps1: `$receipts = if ($null -ne $steps) { ... } else { @() }`
    # left $receipts null (no session), and `.Count` threw after every other check had run.
    $x = if ($false) { @(1) } else { @() }
    if ($null -ne $x) { throw "expected the empty array to have become null (PowerShell 5.1 semantics changed?)" }
    $threw = $false
    try { $null = $x.Count } catch { $threw = $true }
    if (-not $threw) { throw "expected .Count on null to throw under StrictMode" }
    # The fix: assign inside the branch.
    $y = @()
    if ($false) { $y = @(1) }
    Assert-Equal 0 $y.Count "assigned inside the branch, an empty array stays an array"
    # And `@(Get-RowsLike ...)` around a `, @(...)` return: ONE element, the array itself.
    $wrapped = @(Get-RowsLike -Doc $docTwo)
    Assert-Equal 1 $wrapped.Count "the whole two-row array became one element"
    $rows = Get-RowsLike -Doc $docTwo
    $kept = @($rows)
    Assert-Equal 2 $kept.Count "assigned first, @(variable) keeps the rows"
}
Test-Case "the fifth trap: inside @( ... ) the comma binds tighter than +, so a concatenation splits into elements - one of them a bare [char]" {
    # 2026-09-06, owner-m18-2.ps1: `@("a", "sayfay" + $i, "b")` had SIX elements, and the
    # [char] element crashed `.ToLowerInvariant()` after the whole run had otherwise passed.
    $i = [char]0x0131
    $split = @("a", "sayfay" + $i, "b")
    Assert-Equal 4 $split.Count "four elements, not three"
    if (-not ($split[2] -is [char])) { throw "expected the [char] to be a bare element (PowerShell 5.1 semantics changed?)" }
    $whole = "sayfay" + $i
    $safe = @("a", $whole, "b")
    Assert-Equal 3 $safe.Count "built before the literal: three strings"
    $paren = @("a", ("sayfay" + $i), "b")
    Assert-Equal 3 $paren.Count "or parenthesised: three strings"
}
Test-Case "a scalar-in-a-collection helper output filtered to nothing is an empty array, not null" {
    $rows = Get-RowsLike -Doc $docOne
    $none = @($rows | Where-Object { $_.event_type -eq "never" })
    Assert-Equal 0 $none.Count "count"
}

Test-Case "wrapping a Get-ArrayProperty call in @() hands back ONE element: the array itself" {
    # The helper returns , @(...) so a BARE assignment receives the elements. Wrapping the
    # call in @() keeps that protective comma intact and yields a one-element array whose
    # element is the whole array - every field then reads empty. Row 26.16's first two
    # production runs died on exactly this ("no online device" against a device the same
    # endpoint reported as online), after the same trap was already documented in
    # qualify-m18-4.ps1. Pinned here so the shape is a fact, not a surprise.
    $wrapped = @(Get-ArrayProperty -InputObject $firingTwo -Name "dispatch_results")
    Assert-Equal 1 $wrapped.Count "wrapped: one element"
    if (-not ($wrapped[0] -is [object[]])) { throw "expected the element to be the array itself" }
    $bare = Get-ArrayProperty -InputObject $firingTwo -Name "dispatch_results"
    Assert-Equal 2 $bare.Count "bare: the elements"
}
function Find-WrappedArrayPropertyCall {
    # The lines of a script where @( ) wraps a Get-ArrayProperty CALL - read from the parse
    # tree, not from the text. The text version (a regex per line, skipping lines that begin
    # with #) failed main on 2026-09-29 over two places that only NAME the trap: a sentence
    # inside a <# #> help block (DeviceAliases.ps1) and the pattern string of another guard
    # (device-aliases.tests.ps1). A guard that fires on its own documentation gets switched
    # off; this one now fires on code and on nothing else.
    param([Parameter(Mandatory = $true)][string]$Path)
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    $wraps = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.ArrayExpressionAst] }, $true))
    $lines = @()
    foreach ($wrap in $wraps) {
        $statements = @($wrap.SubExpression.Statements)
        if ($statements.Count -ne 1) { continue }
        $pipeline = $statements[0]
        if (-not ($pipeline -is [System.Management.Automation.Language.PipelineAst])) { continue }
        $elements = @($pipeline.PipelineElements)
        # Only the bare call is the trap: `@(Get-ArrayProperty ... | Where-Object ...)` pipes
        # the ELEMENTS on, and wrapping that is the correct way to count what is left.
        if ($elements.Count -ne 1) { continue }
        $command = $elements[0]
        if (-not ($command -is [System.Management.Automation.Language.CommandAst])) { continue }
        if ($command.GetCommandName() -eq "Get-ArrayProperty") { $lines += $wrap.Extent.StartLineNumber }
    }
    return , @($lines)
}

Test-Case "the guard finds a wrapped call and is silent about a comment, a help block and a string" {
    $probeDir = Join-Path $env:TEMP "pagentos-owner-harness-guard-$([guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Force -Path $probeDir | Out-Null
    try {
        $offender = Join-Path $probeDir "offender.ps1"
        Set-Content -LiteralPath $offender -Encoding ASCII -Value @(
            '$doc = $null',
            '$rows = @(Get-ArrayProperty -InputObject $doc -Name "devices")',
            '$also = @(  Get-ArrayProperty -InputObject $doc -Name "devices"  )'
        )
        $found = Find-WrappedArrayPropertyCall -Path $offender
        Assert-Equal "2; 3" ($found -join "; ") "both wrapped calls, by line"

        $innocent = Join-Path $probeDir "innocent.ps1"
        Set-Content -LiteralPath $innocent -Encoding ASCII -Value @(
            '<#',
            '.DESCRIPTION',
            '    it read the list through @(Get-ArrayProperty ...), which wraps it twice',
            '#>',
            '# @(Get-ArrayProperty ...) is the trap',
            '$doc = $null',
            '$pattern = "@\(\s*Get-ArrayProperty"',
            '$bare = Get-ArrayProperty -InputObject $doc -Name "devices"',
            '$left = @(Get-ArrayProperty -InputObject $doc -Name "devices" | Where-Object { $_ })'
        )
        $none = Find-WrappedArrayPropertyCall -Path $innocent
        Assert-Equal 0 $none.Count "naming the trap, or piping the elements on, is not the trap"
    }
    finally {
        Remove-Item -LiteralPath $probeDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "no script wraps a Get-ArrayProperty call in @()" {
    $root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $offenders = @()
    foreach ($file in Get-ChildItem -Path (Join-Path $root "scripts") -Recurse -Filter *.ps1) {
        # This file wraps one on purpose, to pin what the wrap returns (the case above it).
        if ($file.Name -eq "owner-harness.tests.ps1") { continue }
        foreach ($line in (Find-WrappedArrayPropertyCall -Path $file.FullName)) {
            $offenders += "$($file.Name):$line"
        }
    }
    Assert-Equal "" ($offenders -join "; ") "scripts that wrap Get-ArrayProperty in @()"
}

Write-Host ""
Write-Host "owner-harness tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
