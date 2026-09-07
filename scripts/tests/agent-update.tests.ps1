<#
.SYNOPSIS
    Windows PowerShell 5.1 StrictMode tests for scripts/lib/AgentUpdate.ps1 (M18.4 gap 3):
    the candidate manifest (written at staging, re-verified before the swap) and the
    heartbeat / capability verification on Cloud Core, with fakes for the service binary
    and the Cloud Core read.
.DESCRIPTION
    Proven here, with no service, no elevation, no network:
      * a manifest describes the staged trees file by file, the version and the capability
        manifest of the staged service, and the browser worker's release identity;
      * it round-trips through JSON and verifies against an unchanged staging;
      * a changed, missing or extra file, a missing version, a missing required capability
        and a changed browser package are each refused with a sentence naming the culprit;
      * the heartbeat check waits (through an injected clock and sleep) until Cloud Core
        lists the device online with the candidate's version and capabilities, and fails
        with the reasons when the timeout passes with the old version still reported, the
        device offline, a capability missing, or Cloud Core unreadable;
      * the identity verb's output yields the device id, and garbage yields nothing.
    Run: powershell -NoProfile -File scripts\tests\agent-update.tests.ps1
#>
[CmdletBinding()]
param()
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\AgentUpdate.ps1")
$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-agent-update-tests-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) { $script:Passes++; Write-Host "  PASS  $Message" }
    else { $script:Failures++; Write-Host "  FAIL  $Message" -ForegroundColor Red }
}

function New-Staging {
    param([string]$Name)
    $staging = Join-Path $script:Sandbox "$Name\.staging"
    foreach ($component in @("service", "companion")) {
        New-Item -ItemType Directory -Force -Path (Join-Path $staging "$component\sub") | Out-Null
        [IO.File]::WriteAllText((Join-Path $staging "$component\PagentOS.$component.exe"), "binary-$component-v2")
        [IO.File]::WriteAllText((Join-Path $staging "$component\appsettings.json"), "{}")
        [IO.File]::WriteAllText((Join-Path $staging "$component\sub\lib.dll"), "lib-$component")
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $staging "browser\browser_agent") | Out-Null
    [IO.File]::WriteAllText((Join-Path $staging "browser\browser_agent\worker.py"), "WORKER_VERSION = '0.6.0'`nCONTRACTS = {}`n")
    [IO.File]::WriteAllText((Join-Path $staging "browser\browser_agent\__init__.py"), "")
    return $staging
}

$serviceManifest = [pscustomobject]@{
    Ok = $true; Capabilities = @("desktop.open_application", "desktop.alarm_arm", "browser.chrome", "browser.navigate"); BrowserEnabled = $true
    ExitCode = 0; StdErr = ""; SoftwareVersion = "0.2.0"
}

try {
    Write-Host "candidate manifest"
    $staging = New-Staging "a"
    $browserDigest = Get-BrowserPackageDigest -PackageDir (Join-Path $staging "browser\browser_agent")
    $browserRelease = [pscustomobject]@{ Version = "0.6.0"; PackageSha256 = $browserDigest }
    $manifest = New-AgentCandidateManifest -StagingRoot $staging -Components @("service", "companion", "browser") -ServiceManifest $serviceManifest -BrowserRelease $browserRelease -RepoHead "abc1234"
    Assert-True ($manifest.software_version -eq "0.2.0" -and $manifest.browser_enabled -and $manifest.capabilities.Count -eq 4 -and $manifest.components.service.file_count -eq 3 -and $manifest.components.companion.files.Contains("sub/lib.dll") -and $manifest.components.browser.package_sha256 -eq $browserDigest) "the manifest carries version, capabilities, per-file hashes (forward slashes) and the browser release identity"
    $path = Write-AgentCandidateManifest -Manifest $manifest -Path (Get-CandidateManifestPath -StagingRoot $staging)
    $back = Read-AgentCandidateManifest -Path $path
    $v = Test-AgentCandidateManifest -Manifest $back -StagingRoot $staging -RequireCapabilities @("desktop.open_application", "browser.chrome")
    Assert-True ($v.Ok -and $v.Reasons.Count -eq 0 -and $v.Version -eq "0.2.0") "an unchanged staging verifies against the manifest read back from JSON"
    $vFresh = Test-AgentCandidateManifest -Manifest $manifest -StagingRoot $staging
    Assert-True ($vFresh.Ok) "...and against the in-memory manifest too"

    [IO.File]::WriteAllText((Join-Path $staging "service\sub\lib.dll"), "tampered")
    $v2 = Test-AgentCandidateManifest -Manifest $back -StagingRoot $staging
    Assert-True (-not $v2.Ok -and ($v2.Reasons -join " ") -match "service/sub/lib.dll changed since it was staged") "a changed file is refused and named"
    Remove-Item (Join-Path $staging "companion\appsettings.json")
    [IO.File]::WriteAllText((Join-Path $staging "companion\extra.txt"), "x")
    $v3 = Test-AgentCandidateManifest -Manifest $back -StagingRoot $staging
    Assert-True (-not $v3.Ok -and ($v3.Reasons -join " ") -match "companion/appsettings.json is in the manifest but missing" -and ($v3.Reasons -join " ") -match "companion/extra.txt is in staging but not in the manifest") "a missing file and an extra file are each named"
    [IO.File]::WriteAllText((Join-Path $staging "browser\browser_agent\worker.py"), "WORKER_VERSION = '0.6.1'`n")
    $v4 = Test-AgentCandidateManifest -Manifest $back -StagingRoot $staging
    Assert-True (-not $v4.Ok -and ($v4.Reasons -join " ") -match "staged browser package digest is") "a changed browser package is refused by its digest"

    $staging2 = New-Staging "b"
    $noVersion = New-AgentCandidateManifest -StagingRoot $staging2 -Components @("service", "companion") -ServiceManifest ([pscustomobject]@{ Ok = $true; Capabilities = @("desktop.open_application"); BrowserEnabled = $false; ExitCode = 0; StdErr = ""; SoftwareVersion = "" })
    $v5 = Test-AgentCandidateManifest -Manifest $noVersion -StagingRoot $staging2 -RequireCapabilities @("browser.chrome")
    Assert-True (-not $v5.Ok -and ($v5.Reasons -join " ") -match "names no software version" -and ($v5.Reasons -join " ") -match "does not advertise browser.chrome") "a candidate without a version, or without a required capability, is refused"
    $threw = $false
    try { [void](New-AgentCandidateManifest -StagingRoot $staging2 -Components @("service") -ServiceManifest ([pscustomobject]@{ Ok = $false; Capabilities = @(); BrowserEnabled = $false; ExitCode = 2; StdErr = "usage" })) } catch { $threw = $true }
    Assert-True $threw "a staged service that cannot answer its capabilities verb cannot become a candidate"

    Write-Host "heartbeat on Cloud Core (fake fetch, fake clock)"
    $device = "dev-1"
    function New-Listing { param([string]$Presence, [string]$Version, [string[]]$Caps) return [pscustomobject]@{ devices = @([pscustomobject]@{ device_id = "other"; presence = "offline"; software_version = "0.0.1"; capabilities = @() }, [pscustomobject]@{ device_id = $device; presence = $Presence; software_version = $Version; capabilities = $Caps; last_seen_at = "2026-09-07T18:00:00Z" }) } }
    $script:clock = [datetime]"2026-09-07T18:00:00Z"
    $script:sequence = New-Object System.Collections.Queue
    $script:sequence.Enqueue((New-Listing "offline" "0.1.0" @()))
    $script:sequence.Enqueue((New-Listing "online" "0.1.0" @("desktop.open_application")))
    $script:sequence.Enqueue((New-Listing "online" "0.2.0" @("desktop.open_application", "browser.chrome")))
    $fetch = { if ($script:sequence.Count -gt 1) { $script:sequence.Dequeue() } else { $script:sequence.Peek() } }
    $sleep = { param($s) $script:clock = $script:clock.AddSeconds($s) }
    $now = { $script:clock }
    $hb = Test-AgentHeartbeatOnCore -FetchDevices $fetch -DeviceId $device -ExpectedVersion "0.2.0" -ExpectedCapabilities @("desktop.open_application", "browser.chrome") -TimeoutSeconds 90 -PollSeconds 3 -Sleep $sleep -Now $now
    Assert-True ($hb.Ok -and $hb.Attempts -eq 3 -and $hb.Waited -eq 6 -and $hb.Observed.software_version -eq "0.2.0" -and $hb.Observed.presence -eq "online") "the check waits through offline and the old version until the candidate is online with its version and capabilities (3 polls, 6 s)"

    $script:clock = [datetime]"2026-09-07T18:00:00Z"
    $stale = { New-Listing "online" "0.1.0" @("desktop.open_application") }
    $hb2 = Test-AgentHeartbeatOnCore -FetchDevices $stale -DeviceId $device -ExpectedVersion "0.2.0" -ExpectedCapabilities @("desktop.open_application", "browser.chrome") -TimeoutSeconds 30 -PollSeconds 10 -Sleep $sleep -Now $now
    Assert-True (-not $hb2.Ok -and ($hb2.Reasons -join " ") -match "reports software version '0.1.0', the candidate is 0.2.0" -and ($hb2.Reasons -join " ") -match "does not advertise: browser.chrome" -and $hb2.Waited -ge 30) "the old version still reported at the timeout fails with both reasons (version, capability)"

    $script:clock = [datetime]"2026-09-07T18:00:00Z"
    $offline = { New-Listing "offline" "0.2.0" @("desktop.open_application", "browser.chrome") }
    $hb3 = Test-AgentHeartbeatOnCore -FetchDevices $offline -DeviceId $device -ExpectedVersion "0.2.0" -TimeoutSeconds 5 -PollSeconds 5 -Sleep $sleep -Now $now
    Assert-True (-not $hb3.Ok -and ($hb3.Reasons -join " ") -match "is 'offline', not online") "a device that never comes online fails with its presence named"

    $script:clock = [datetime]"2026-09-07T18:00:00Z"
    $unreadable = { throw "connection refused" }
    $hb4 = Test-AgentHeartbeatOnCore -FetchDevices $unreadable -DeviceId $device -ExpectedVersion "0.2.0" -TimeoutSeconds 5 -PollSeconds 5 -Sleep $sleep -Now $now
    Assert-True (-not $hb4.Ok -and ($hb4.Reasons -join " ") -match "Cloud Core could not be read: connection refused" -and $hb4.Observed.fetch_error -eq "connection refused") "an unreadable Cloud Core is a failure with the error, never a pass"

    $script:clock = [datetime]"2026-09-07T18:00:00Z"
    $unknown = { [pscustomobject]@{ devices = @() } }
    $hb5 = Test-AgentHeartbeatOnCore -FetchDevices $unknown -DeviceId $device -ExpectedVersion "0.2.0" -TimeoutSeconds 5 -PollSeconds 5 -Sleep $sleep -Now $now
    Assert-True (-not $hb5.Ok -and ($hb5.Reasons -join " ") -match "does not list device dev-1") "a device Cloud Core does not list is a failure"

    Write-Host "identity output"
    Assert-True ((ConvertFrom-IdentityOutput -StdOut "diag line`n{`"device_id`":`"1d0c`",`"name`":`"MAIL`"}`n") -eq "1d0c") "the identity verb's JSON document yields the device id past diagnostics"
    Assert-True ($null -eq (ConvertFrom-IdentityOutput -StdOut "not json") -and $null -eq (ConvertFrom-IdentityOutput -StdOut "")) "garbage yields nothing"
    Assert-True ($null -eq (Get-AgentDeviceId -ServiceExe (Join-Path $script:Sandbox "missing.exe"))) "a missing service binary yields no device id"
}
finally {
    Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "agent-update tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
