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

# Scripts that held non-ASCII text without a BOM when the BOM rule arrived. Every entry is a
# debt with an owner card; the test fails on an entry that is no longer needed.
$bomWaiverReason = "BOM-less on 2026-10-04; fixed by card bom-fix-offenders"
$bomWaiver = @(
    @{ Path = "scripts/bootstrap-owner-credential.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/browser/enroll-owner-chrome.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/browser/real-browser-smoke.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/cloud/breakglass-ssh.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/cloud/migrate-agent-to-cloud.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/cloud/new-vapid-key.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/cloud/provision.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/complete-device-enrollment.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/core/evolution-advance.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/core/owner-m18-eye.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/core/probe-window-by-name.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/core/qualify-item28-unlocked.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/core/qualify-pc-production.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/core/voice-routing-qualification.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/dev-broker.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/dev-down.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/dev-up.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/e2e-m1-device.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/e2e-m13-research.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/finalize-qualification.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/install-device-service.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/lib/AgentUpdate.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/lib/BrowserProvision.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/lib/BrowserSmokeEvidence.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/lib/Deployment.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/lib/DevBroker.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/lib/HttpJson.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/lib/IdentityStatus.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/lib/InstallAcl.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/lib/InstallEvidence.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/lib/NativeProcess.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/lib/ServiceInstall.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/lib/VoiceShell.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/qualify-device.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/quality-gate.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/repair-device-material.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/rotate-owner-credential.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/tests/agent-release-currency.tests.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/tests/cloud-release-bluegreen.tests.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/tests/cloud-secret.tests.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/tests/identity-restore.tests.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/tests/installer-acl.tests.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/tests/installer-browser.tests.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/tests/installer-deploy.tests.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/tests/installer-invocation.tests.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/tests/installer-strictmode.tests.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/tests/machine-readable.tests.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/tests/make-ocr-fixture.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/tests/provision.tests.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/tests/utf8-json.tests.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/verify-device-service.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/voice/fetch-benchmark.ps1"; Reason = $bomWaiverReason },
    @{ Path = "scripts/voice/tts-loopback-qualification.ps1"; Reason = $bomWaiverReason }
)

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

# A parameter token glued to its value (`-InputObject$doc`, from a search-and-replace that
# ate the space) PARSES cleanly: 5.1 reads the whole thing as one parameter name and only
# fails at bind time, with "A parameter cannot be found that matches parameter name
# 'InputObject$doc'". A real release died in its final report on exactly that. Parameter
# names can only look like identifiers, so anything else is a glued token.
function Get-GluedParameterTokens {
    param([Parameter(Mandatory = $true)][System.Management.Automation.Language.Ast]$Ast)
    return @($Ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandParameterAst] }, $true) |
        Where-Object { $_.ParameterName -cnotmatch '^[A-Za-z][A-Za-z0-9_]*$' -and $_.Extent.Text -ne '--' })
}

# the lint proves itself first: the exact defect must be caught, the correct form must not
$gluedProbe = [System.Management.Automation.Language.Parser]::ParseInput('Get-OptionalProperty -InputObject$doc -Name "status"', [ref]$null, [ref]$null)
$cleanProbe = [System.Management.Automation.Language.Parser]::ParseInput('Get-OptionalProperty -InputObject $doc -Name "status"', [ref]$null, [ref]$null)
if (@(Get-GluedParameterTokens -Ast $gluedProbe).Count -eq 1 -and @(Get-GluedParameterTokens -Ast $cleanProbe).Count -eq 0) {
    Write-Host "  PASS  glued-parameter lint catches -InputObject`$doc and accepts -InputObject `$doc"
}
else {
    $failures++
    Write-Host "  FAIL  glued-parameter lint does not distinguish -InputObject`$doc from -InputObject `$doc" -ForegroundColor Red
}

# A BOM-less script with non-ASCII text (an em dash, 'ö') is read by Windows PowerShell 5.1
# as ANSI: an em dash's third byte becomes a closing quote and the file stops parsing, and
# Turkish text the script writes comes out as "YÃ¶neticisi" (28 team records broken this way
# on 2026-10-03). A rule over every script, not a list of known files.
function Test-NonAsciiWithoutBom {
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][byte[]]$Bytes)
    if ($Bytes.Length -ge 3 -and $Bytes[0] -eq 0xEF -and $Bytes[1] -eq 0xBB -and $Bytes[2] -eq 0xBF) {
        return $false
    }
    foreach ($byte in $Bytes) {
        if ($byte -gt 127) { return $true }
    }
    return $false
}

# The waiver shrinks itself: an entry whose file now has a BOM, no longer holds non-ASCII
# text or no longer exists is a failure, so the list cannot outlive the fix.
function Get-BomWaiverProblems {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Waiver
    )
    $problems = @()
    foreach ($entry in $Waiver) {
        $path = Join-Path $Root ($entry.Path -replace '/', '\')
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or
            -not (Test-NonAsciiWithoutBom -Bytes ([System.IO.File]::ReadAllBytes($path)))) {
            $problems += "waiver no longer needed: remove $($entry.Path)"
        }
    }
    return @($problems)
}

$bomMessage = "non-ASCII text without a byte-order mark - Windows PowerShell 5.1 reads it as ANSI (save as UTF-8 with BOM)"

# the BOM rule proves itself first, on probe files in a fresh temp folder (never the repo)
$probeRoot = Join-Path $env:TEMP ("script-syntax-bom-probe-" + [guid]::NewGuid().ToString("N"))
try {
    New-Item -ItemType Directory -Path $probeRoot | Out-Null
    $utf8Bom = [byte[]](0xEF, 0xBB, 0xBF)
    $probes = @(
        @{ Name = "no-bom-o-umlaut.ps1"; Bytes = [System.Text.Encoding]::UTF8.GetBytes("# " + [char]0x00F6 + "`r`n"); Caught = $true; Label = "a BOM-less o-umlaut (U+00F6)" },
        @{ Name = "bom-o-umlaut.ps1"; Bytes = $utf8Bom + [System.Text.Encoding]::UTF8.GetBytes("# " + [char]0x00F6 + "`r`n"); Caught = $false; Label = "an o-umlaut with a BOM" },
        @{ Name = "ascii.ps1"; Bytes = [System.Text.Encoding]::ASCII.GetBytes("# plain`r`n"); Caught = $false; Label = "pure ASCII without a BOM" },
        @{ Name = "no-bom-em-dash.ps1"; Bytes = [System.Text.Encoding]::UTF8.GetBytes("# a " + [char]0x2014 + " b`r`n"); Caught = $true; Label = "a BOM-less em dash" }
    )
    foreach ($probe in $probes) {
        $probePath = Join-Path $probeRoot $probe.Name
        [System.IO.File]::WriteAllBytes($probePath, [byte[]]$probe.Bytes)
        $caught = Test-NonAsciiWithoutBom -Bytes ([System.IO.File]::ReadAllBytes($probePath))
        $verb = if ($probe.Caught) { "catches" } else { "accepts" }
        if ($caught -eq $probe.Caught) {
            Write-Host "  PASS  BOM rule $verb $($probe.Label)"
        }
        else {
            $failures++
            Write-Host "  FAIL  BOM rule should have $(if ($probe.Caught) { 'caught' } else { 'accepted' }) $($probe.Label)" -ForegroundColor Red
        }
    }

    $stillNeeded = @(Get-BomWaiverProblems -Root $probeRoot -Waiver @(@{ Path = "no-bom-o-umlaut.ps1" }))
    $noLongerNeeded = @(Get-BomWaiverProblems -Root $probeRoot -Waiver @(@{ Path = "bom-o-umlaut.ps1" }, @{ Path = "ascii.ps1" }, @{ Path = "gone.ps1" }))
    if ($stillNeeded.Count -eq 0 -and $noLongerNeeded.Count -eq 3 -and
        $noLongerNeeded[0] -eq "waiver no longer needed: remove bom-o-umlaut.ps1") {
        Write-Host "  PASS  BOM waiver keeps a BOM-less non-ASCII file and refuses one with a BOM, a pure-ASCII one and a missing one"
    }
    else {
        $failures++
        Write-Host "  FAIL  BOM waiver does not shrink itself (still needed: $($stillNeeded -join '; ') / no longer needed: $($noLongerNeeded -join '; '))" -ForegroundColor Red
    }
}
finally {
    if (Test-Path -LiteralPath $probeRoot) { Remove-Item -LiteralPath $probeRoot -Recurse -Force }
}

$bomWaived = @{}
foreach ($entry in $bomWaiver) { $bomWaived[$entry.Path] = $true }

foreach ($file in @(Get-ChildItem -Path (Join-Path $repoRoot "scripts") -Filter "*.ps1" -Recurse -File)) {
    $relative = $file.FullName.Substring($repoRoot.Length + 1)
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$null, [ref]$errors)
    $checked++

    # checked whether or not the file parses: a BOM-less em dash is exactly the file that does not
    $bomLine = $null
    if (-not $bomWaived.ContainsKey(($relative -replace '\\', '/')) -and
        (Test-NonAsciiWithoutBom -Bytes ([System.IO.File]::ReadAllBytes($file.FullName)))) {
        $bomLine = "        ${relative}: $bomMessage"
    }

    if (-not $errors -or @($errors).Count -eq 0) {
        $glued = @(Get-GluedParameterTokens -Ast $ast)
        if ($glued.Count -gt 0) {
            $failures++
            Write-Host "  FAIL  $relative" -ForegroundColor Red
            if ($bomLine) { Write-Host $bomLine -ForegroundColor Red }
            foreach ($token in @($glued | Select-Object -First 3)) {
                Write-Host ("        line {0}: parameter token glued to its value (binds as parameter name {1}): {2}" -f $token.Extent.StartLineNumber, $token.ParameterName, $token.Extent.Text) -ForegroundColor Red
            }
            continue
        }
    }

    if ($errors -and @($errors).Count -gt 0) {
        $failures++
        Write-Host "  FAIL  $relative" -ForegroundColor Red
        if ($bomLine) { Write-Host $bomLine -ForegroundColor Red }
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
            if ($bomLine) { Write-Host $bomLine -ForegroundColor Red }
            foreach ($hit in @($apiHits | Select-Object -First 3)) {
                Write-Host ("        line {0}: .NET-Core-only crypto API not available on the 5.1 Framework host - use the identity helper: {1}" -f $hit.LineNumber, $hit.Line.Trim()) -ForegroundColor Red
            }
            continue
        }
    }

    if ($bomLine) {
        $failures++
        Write-Host "  FAIL  $relative" -ForegroundColor Red
        Write-Host $bomLine -ForegroundColor Red
        continue
    }

    Write-Host "  PASS  $relative"
}

foreach ($problem in @(Get-BomWaiverProblems -Root $repoRoot -Waiver $bomWaiver)) {
    $failures++
    Write-Host "  FAIL  $problem" -ForegroundColor Red
}

Write-Host ""
Write-Host "$checked script(s) checked, $failures failed"
exit $failures
