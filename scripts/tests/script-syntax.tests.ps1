<#
.SYNOPSIS
    Every repository script must parse under Windows PowerShell 5.1.

.DESCRIPTION
    These scripts run on the owner's machine in Windows PowerShell 5.1, but they are written
    in an environment where PowerShell 7 syntax looks perfectly normal. A PowerShell 7-only
    construct is not a subtle difference: it is a parse error, so the script fails on its
    first line rather than at the point of use, and only on the owner's machine.

    This has already happened once — `??` in `rotate-owner-credential.ps1`, which would have
    failed at the very moment the owner ran a security-critical credential rotation. The
    parser is the cheapest possible check for the whole class, so it runs in the gate.

    The test asserts it is itself running on 5.1: parsing with the 7 parser would prove
    nothing about the engine these scripts actually meet.

    Run: powershell -NoProfile -File scripts\tests\script-syntax.tests.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$failures = 0
$checked = 0

if ($PSVersionTable.PSVersion.Major -ne 5) {
    Write-Host "  FAIL  these must be parsed by Windows PowerShell 5.1, not $($PSVersionTable.PSVersion)" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Windows PowerShell 5.1 syntax ($($PSVersionTable.PSVersion))"

# .NET APIs that read as perfectly normal but DO NOT EXIST on Windows PowerShell 5.1's
# .NET Framework. A real qualification run died on ECDsa.ImportFromPem mid-flow — parseable,
# discoverable only at the call. Key handling belongs in the .NET 10 identity helper
# (`PagentOS.DeviceService identity`), never in script. Detected here so the gate finds the
# next one before the owner's machine does.
$frameworkMissingApis = @(
    "ImportFromPem", "ImportPkcs8PrivateKey", "ExportPkcs8PrivateKey",
    "ImportECPrivateKey", "ExportECPrivateKey", "ImportSubjectPublicKeyInfo",
    "ExportSubjectPublicKeyInfo", "CreateFromPem", "ImportRSAPrivateKey"
)
$forbiddenPattern = "\.(" + ($frameworkMissingApis -join "|") + ")\("

foreach ($file in @(Get-ChildItem -Path (Join-Path $repoRoot "scripts") -Filter "*.ps1" -Recurse -File)) {
    $relative = $file.FullName.Substring($repoRoot.Length + 1)
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$null, [ref]$errors)
    $checked++

    if ($errors -and @($errors).Count -gt 0) {
        $failures++
        Write-Host "  FAIL  $relative" -ForegroundColor Red
        foreach ($parseError in @($errors | Select-Object -First 3)) {
            Write-Host ("        line {0}: {1}" -f $parseError.Extent.StartLineNumber, $parseError.Message) -ForegroundColor Red
        }
        continue
    }

    # This test file names the forbidden APIs on purpose; every other script is checked.
    if ($file.FullName -ne $PSCommandPath) {
        $apiHits = @(Select-String -LiteralPath $file.FullName -Pattern $forbiddenPattern)
        if (@($apiHits).Count -gt 0) {
            $failures++
            Write-Host "  FAIL  $relative" -ForegroundColor Red
            foreach ($hit in @($apiHits | Select-Object -First 3)) {
                Write-Host ("        line {0}: .NET-Core-only crypto API not available on the 5.1 Framework host - use the identity helper: {1}" -f $hit.LineNumber, $hit.Line.Trim()) -ForegroundColor Red
            }
            continue
        }
    }

    Write-Host "  PASS  $relative"
}

Write-Host ""
Write-Host "$checked script(s) checked, $failures failed"
exit $failures
