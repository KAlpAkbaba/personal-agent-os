<#
.SYNOPSIS
    Static guard for the owner harnesses and the libraries they dot-source: every command a
    script invokes is declared somewhere the script can actually reach, and no script binds a
    callback with GetNewClosure.

.DESCRIPTION
    Third harness-class PowerShell defect of M18 (owner run, 2026-09-06): a callback created
    with .GetNewClosure() called Get-NewSessions, a function dot-sourced into the harness's
    script scope. A closure runs in a fresh module scope that sees only global functions, so
    the name did not resolve and the run crashed after a successful Cloud Core release.
    A test with plain script blocks had passed, because plain blocks DO see the script scope.

    This guard parses each script with the PowerShell AST (no execution of the harness), and
    for every CommandAst asserts the command name resolves to one of:
      - a function defined in the script itself (at any depth),
      - a function defined in a library the script dot-sources (scripts\lib\*.ps1, followed
        transitively), or
      - a command PowerShell itself knows (Get-Command in this fresh -NoProfile process).
    It also fails on any .GetNewClosure() invocation, and checks each library on its own:
    a library must resolve with only what IT dot-sources.

    Run: powershell -NoProfile -File scripts\tests\harness-symbols.tests.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

$script:Failures = 0
$script:Passes = 0
function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Passes++; Write-Host "  PASS  $Name" }
    catch { $script:Failures++; Write-Host "  FAIL  $Name" -ForegroundColor Red; Write-Host "        $($_.Exception.Message)" -ForegroundColor Red }
}

function Get-ScriptAst {
    param([string]$Path)
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    if ($errors.Count -gt 0) { throw "parse errors in ${Path}: $($errors[0].Message)" }
    return $ast
}

function Get-DotSourcedLibs {
    <#  The scripts\lib\*.ps1 files a script dot-sources, transitively, in load order.  #>
    param([string]$Path, [hashtable]$Seen = @{})
    $text = [System.IO.File]::ReadAllText($Path)
    $libs = @()
    foreach ($m in [regex]::Matches($text, 'scripts\\lib\\([A-Za-z0-9_]+\.ps1)')) {
        $lib = Join-Path $repoRoot ("scripts\lib\" + $m.Groups[1].Value)
        if ($Seen.ContainsKey($lib)) { continue }
        $Seen[$lib] = $true
        $libs += Get-DotSourcedLibs -Path $lib -Seen $Seen
        $libs += $lib
    }
    # A library dot-sourcing a sibling by $PSScriptRoot ("OwnerHarness.ps1").
    foreach ($m in [regex]::Matches($text, 'Join-Path \$PSScriptRoot "([A-Za-z0-9_]+\.ps1)"')) {
        $lib = Join-Path (Split-Path -Parent $Path) $m.Groups[1].Value
        if ($Seen.ContainsKey($lib)) { continue }
        $Seen[$lib] = $true
        $libs += Get-DotSourcedLibs -Path $lib -Seen $Seen
        $libs += $lib
    }
    return , $libs
}

function Get-DefinedFunctions {
    param($Ast)
    $names = @()
    foreach ($f in $Ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) { $names += $f.Name }
    return , $names
}

function Get-InvokedCommands {
    <#  Command names invoked by name (not `& $variable`, not a dot-source of an expression).  #>
    param($Ast)
    $names = @{}
    foreach ($c in $Ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true)) {
        $name = $c.GetCommandName()
        if ([string]::IsNullOrEmpty($name)) { continue }
        if ($name -match '^[\\./]' -or $name -match '\.ps1$') { continue }  # a path, not a symbol
        $names[$name] = $true
    }
    return , @($names.Keys | Sort-Object)
}

function Get-ClosureSites {
    param($Ast)
    $sites = @()
    foreach ($m in $Ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.InvokeMemberExpressionAst] }, $true)) {
        $member = $m.Member
        if ($member -is [System.Management.Automation.Language.StringConstantExpressionAst] -and $member.Value -eq "GetNewClosure") {
            $sites += "line $($m.Extent.StartLineNumber)"
        }
    }
    return , $sites
}

function Get-ArrayLiteralConcatenations {
    <#
        Elements of an @( ... ) literal that are a `+` expression. Inside a literal the comma
        binds tighter than +, so `@("a", "x" + $c, "b")` is not three elements but the
        concatenation of two arrays - and a [char] operand becomes a bare element with no
        string methods (the owner's M18.2 harness crash). Build the string first, or
        parenthesise.
    #>
    param($Ast)
    # The parser does not put the + INSIDE the literal: `@("a", "x" + $c, "b")` parses as
    # `("a", "x") + ($c, "b")` - a Plus whose operand is itself an array literal, sitting
    # directly under the @( ). That shape is the trap.
    $sites = @()
    foreach ($bin in $Ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.BinaryExpressionAst] }, $true)) {
        if ($bin.Operator -ne "Plus") { continue }
        $leftIsList = $bin.Left -is [System.Management.Automation.Language.ArrayLiteralAst]
        $rightIsList = $bin.Right -is [System.Management.Automation.Language.ArrayLiteralAst]
        if ($leftIsList -or $rightIsList) { $sites += "line $($bin.Extent.StartLineNumber)" }
    }
    return , $sites
}

function Get-UnresolvedCommands {
    <#
        Names the script invokes that neither it nor its libraries define and PowerShell does
        not know. Resolution is checked in THIS process with nothing dot-sourced: builtins via
        Get-Command, everything else must be declared. Keywords and aliases PowerShell parses
        as commands (exit, throw are keywords; `?`, `%` aliases) resolve via Get-Command too.
    #>
    param([string]$Path)
    $ast = Get-ScriptAst -Path $Path
    $declared = @{}
    foreach ($n in (Get-DefinedFunctions -Ast $ast)) { $declared[$n] = "script" }
    foreach ($lib in (Get-DotSourcedLibs -Path $Path)) {
        foreach ($n in (Get-DefinedFunctions -Ast (Get-ScriptAst -Path $lib))) { $declared[$n] = (Split-Path -Leaf $lib) }
    }
    $unresolved = @()
    foreach ($name in (Get-InvokedCommands -Ast $ast)) {
        if ($declared.ContainsKey($name)) { continue }
        $known = $null
        try { $known = Get-Command -Name $name -ErrorAction SilentlyContinue } catch { $known = $null }
        if ($null -eq $known) { $unresolved += $name }
    }
    return , $unresolved
}

$harnesses = @(
    "scripts\core\owner-m18-eye.ps1",
    "scripts\core\owner-m18-presence.ps1",
    "scripts\core\owner-m18-2.ps1",
    "scripts\core\owner-m18-3-core.ps1",
    "scripts\core\owner-m18.ps1",
    "scripts\core\reconcile-m18.ps1",
    "scripts\voice\owner-explain.ps1"
)
$libraries = @(
    "scripts\lib\VoiceShell.ps1",
    "scripts\lib\OwnerHarness.ps1"
)

Write-Host "harness symbols: every invoked command is declared where the script can see it"

foreach ($rel in $harnesses + $libraries) {
    $path = Join-Path $repoRoot $rel
    Test-Case "$rel invokes no undeclared command" {
        $missing = Get-UnresolvedCommands -Path $path
        if ($missing.Count -gt 0) { throw "undeclared: $($missing -join ', ')" }
    }
    Test-Case "$rel binds no callback with GetNewClosure" {
        $sites = Get-ClosureSites -Ast (Get-ScriptAst -Path $path)
        if ($sites.Count -gt 0) { throw "GetNewClosure at $($sites -join ', ') - a closure cannot see dot-sourced functions" }
    }
    Test-Case "$rel has no + expression as an element of an array literal" {
        $sites = Get-ArrayLiteralConcatenations -Ast (Get-ScriptAst -Path $path)
        if ($sites.Count -gt 0) { throw "a + inside @( ... ) at $($sites -join ', ') - the comma binds tighter; build the string first or parenthesise" }
    }
}

Test-Case "the guard catches the M18.2 shape: a concatenation as an array-literal element" {
    $tmp = Join-Path $env:TEMP ("harness-array-probe-" + [guid]::NewGuid().ToString("N") + ".ps1")
    try {
        $body = '$c = [char]0x0131' + "`r`n" + '$bad = @("a", "x" + $c, "b")' + "`r`n" + '$ok = @("a", ("x" + $c), "b")' + "`r`n"
        [System.IO.File]::WriteAllText($tmp, $body)
        $sites = Get-ArrayLiteralConcatenations -Ast (Get-ScriptAst -Path $tmp)
        if ($sites.Count -ne 1) { throw "expected exactly one site (the unparenthesised one), got $($sites.Count)" }
    }
    finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
}

Test-Case "the guard itself catches the 2026-09-06 shape: a call to a function no library declares" {
    $tmp = Join-Path $env:TEMP ("harness-symbols-probe-" + [guid]::NewGuid().ToString("N") + ".ps1")
    try {
        $body = '$repoRoot = "x"' + "`r`n" +
                '. (Join-Path $repoRoot "scripts\lib\OwnerHarness.ps1")' + "`r`n" +
                '$x = Get-ArrayProperty -InputObject $null -Name "a"' + "`r`n" +
                '$y = Get-NewSessions -Sessions @() -BaselineIds @()' + "`r`n" +
                '$z = { Get-Json "/x" }.GetNewClosure()' + "`r`n"
        [System.IO.File]::WriteAllText($tmp, $body)
        $missing = Get-UnresolvedCommands -Path $tmp
        if (($missing -join ",") -ne "Get-Json,Get-NewSessions") { throw "expected Get-Json,Get-NewSessions undeclared (OwnerHarness declares Get-ArrayProperty), got '$($missing -join ',')'" }
        $sites = Get-ClosureSites -Ast (Get-ScriptAst -Path $tmp)
        if ($sites.Count -ne 1) { throw "expected one closure site, got $($sites.Count)" }
    }
    finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
}

Test-Case "VoiceShell.ps1 declares Get-NewSessions and dot-sources OwnerHarness.ps1 itself" {
    $vs = Join-Path $repoRoot "scripts\lib\VoiceShell.ps1"
    $fns = Get-DefinedFunctions -Ast (Get-ScriptAst -Path $vs)
    if ($fns -notcontains "Get-NewSessions") { throw "Get-NewSessions missing" }
    if ($fns -notcontains "Wait-QualificationSession") { throw "Wait-QualificationSession missing" }
    $libs = @(Get-DotSourcedLibs -Path $vs | ForEach-Object { Split-Path -Leaf $_ })
    if ($libs -notcontains "OwnerHarness.ps1") { throw "VoiceShell must dot-source OwnerHarness.ps1 (it uses Get-ArrayProperty)" }
}

Write-Host ""
Write-Host "harness symbols: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
