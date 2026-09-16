<#
.SYNOPSIS
    B48: scripts\core\qualify-device-camera.ps1 qualified without a camera, a device or a Cloud
    Core - its dry run must plan every gate, send nothing, prompt nobody and judge nothing.

.DESCRIPTION
    The physical camera evaluation is the owner's. What CAN be proved here is that the script
    the owner will run is whole: it parses under Windows PowerShell 5.1, walks every gate, plans
    the production requests those gates rest on (the three camera modes, the eye switch, the
    monitor power read), records a PLAN rather than a result, and writes its evidence file.
    The base URL points at a closed port: any request the dry run made would fail the run.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$PSNativeCommandUseErrorActionPreference = $false

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script = Join-Path $repoRoot "scripts\core\qualify-device-camera.ps1"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$sandbox = Join-Path $env:TEMP "pagentos-camera-qual-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $sandbox | Out-Null

$script:Passed = 0
$script:Failed = 0
function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) { $script:Passed++; Write-Host "  ok    $Message" }
    else { $script:Failed++; Write-Host "  FAIL  $Message" -ForegroundColor Red }
}

function Invoke-DryRun {
    param([string]$Name, [string[]]$Extra = @())
    $outFile = Join-Path $sandbox "$Name.json"
    $arguments = @(
        "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $script,
        "-DryRun", "-BaseUrl", "http://127.0.0.1:9", "-OutFile", $outFile
    ) + $Extra
    $output = & $powershell @arguments
    $code = $LASTEXITCODE
    $evidence = $null
    if (Test-Path -LiteralPath $outFile) {
        $evidence = [System.IO.File]::ReadAllText($outFile, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    }
    return [pscustomobject]@{ Code = $code; Evidence = $evidence; Output = ($output -join "`n") }
}

try {
    Write-Host "qualify-device-camera.ps1 -DryRun, every optional gate on"
    $full = Invoke-DryRun -Name "full" -Extra @("-IncludePrivacyCheck", "-SleepTrial")
    Assert-True ($full.Code -eq 0) "a dry run exits 0 (exit $($full.Code))"
    Assert-True ($null -ne $full.Evidence) "it writes its evidence file"
    if ($null -ne $full.Evidence) {
        $ev = $full.Evidence
        Assert-True ($ev.verdict -eq "PLANNED") "the verdict is PLANNED, never a pass ($($ev.verdict))"
        Assert-True ($ev.mode -eq "dry-run") "the evidence says dry-run"
        $gates = @($ev.gates.PSObject.Properties)
        Assert-True ($gates.Count -eq 12) "all twelve gates are recorded ($($gates.Count))"
        Assert-True (@($gates | Where-Object { $_.Value.verdict -ne "PLANNED" }).Count -eq 0) "every gate is PLANNED - a dry run judges nothing"
        $plan = @($ev.plan)
        $bodies = @($plan | Where-Object { $_.PSObject.Properties.Name -contains "body" -and $null -ne $_.body } | ForEach-Object { ConvertTo-Json -InputObject $_.body -Compress })
        foreach ($mode in @("periodic", "continuous", "off")) {
            Assert-True (@($bodies | Where-Object { $_ -like "*`"camera_mode`":`"$mode`"*" }).Count -ge 1) "it plans camera_mode=$mode through PUT /v1/ambient/policy"
        }
        $paths = @($plan | Where-Object { $_.PSObject.Properties.Name -contains "path" } | ForEach-Object { "$($_.method) $($_.path)" })
        Assert-True ($paths -contains "POST /v1/presence/eye/disable") "it plans the eye switch that must close the device camera"
        Assert-True ($paths -contains "GET /v1/devices/<device>/status") "it reads the device heartbeat's camera block"
        Assert-True (@($bodies | Where-Object { $_ -like "*desktop.display_status*" -and $_ -like "*probe_power*" }).Count -eq 1) "it plans one DDC power read of the monitors (row 320)"
        Assert-True (@($plan | Where-Object { $_.PSObject.Properties.Name -contains "ask_owner" }).Count -ge 5) "the owner's judgements are planned, not answered"
        Assert-True (@($plan | Where-Object { $_.PSObject.Properties.Name -contains "wait_for" }).Count -ge 10) "every machine wait is planned"
    }
    Assert-True ($full.Output -notmatch "FAILED") "nothing failed on the way"

    Write-Host "qualify-device-camera.ps1 -DryRun, optional gates off"
    $short = Invoke-DryRun -Name "short"
    Assert-True ($short.Code -eq 0) "exits 0"
    if ($null -ne $short.Evidence) {
        foreach ($gate in @("G9_privacy_switch", "G11_sleep_trial", "G12_monitors_dark")) {
            Assert-True ($short.Evidence.gates.$gate.verdict -eq "SKIPPED") "$gate is SKIPPED, with the switch that enables it named"
        }
    }

    $text = [System.IO.File]::ReadAllText($script)
    Assert-True ($text -match '(?s)finally\s*\{.*?/v1/ambient/policy.*?Save-Evidence') "the owner's policy is restored in finally, before the evidence is saved"
    Assert-True ($text -notmatch 'desktop\.display_off') "the script never sends desktop.display_off itself - only the owner's policy can"
    Assert-True ([System.IO.File]::ReadAllBytes($script)[0] -eq 0xEF) "it carries a UTF-8 BOM (it holds Turkish prompts; PowerShell 5.1 needs the mark)"
}
finally {
    Remove-Item -LiteralPath $sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "device-camera-qualification: $script:Passed passed, $script:Failed failed"
if ($script:Failed -gt 0) { exit 1 }
exit 0
