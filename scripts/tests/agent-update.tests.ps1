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
    Component = "device-service"; AssemblyVersion = "0.2.0"; CapabilityManifestVersion = "aabbccdd1122"
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

    # ADR-0097 Decision 4 addendum (2026-09-09): the installer's .Count lint now reads its file
    # list from disk, so it covers AgentUpdate.ps1, and this branch reads .Count on a MAP. The
    # lint's usual remedy - @($expected).Count - is WRONG here: @( ) does not enumerate an
    # IDictionary, so the test would read `1 -eq 0` and a component listing no file would
    # qualify silently, unnoticed, because nothing exercised this branch. It does now.
    $noFiles = Read-AgentCandidateManifest -Path $path
    $noFiles.components.service.files = [pscustomobject]@{}
    $vNoFiles = Test-AgentCandidateManifest -Manifest $noFiles -StagingRoot $staging
    Assert-True (-not $vNoFiles.Ok -and ($vNoFiles.Reasons -join " ") -match "component 'service' lists no file") "a component whose manifest lists no file is refused, and named"

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

    # ---------------------------------------------------------------------------------
    # 2026-09-08 PRODUCTION INCIDENT: candidate 0.6.0 staged, promoted, started, was seen by
    # Cloud Core with all 40 of its capabilities - and this check reported
    #   "the device reports software version '', candidate is 0.6.0"
    # after 92.6 s, so the engine rolled a healthy release back.
    #
    # Why the suite above did not catch it: `New-Listing` invents a device row, and the
    # invented row carries a TOP-LEVEL software_version. The real GET /v1/devices row never
    # had one - the version lived at row.health.software_version. The fake was a picture of
    # a contract nobody had written down, and it passed for months.
    #
    # These cases use the row shape Cloud Core actually returns (asserted from the other
    # side by services/api/tests/unit/test_device_identity_contract.py), in both directions.
    # ---------------------------------------------------------------------------------
    Write-Host "the production device row (2026-09-08 incident)"

    function New-CoreRow {
        <#  The real /v1/devices row shape: identity at the top, health nested.  #>
        param([string]$Presence, [string]$Version, [string[]]$Caps, [switch]$NoTopLevelVersion, [switch]$NoVersionAnywhere)
        $health = [pscustomobject]@{
            last_hello_at = "2026-09-08T20:23:00Z"
            software_version = $(if ($NoVersionAnywhere) { $null } else { $Version })
            heartbeat_age_s = 2.0
            recent_outcomes = @()
        }
        $row = [ordered]@{
            device_id = $device
            name = "owner-pc"
            platform = "windows"
            status = $Presence
            presence = $Presence
            capabilities = $Caps
            capability_count = @($Caps).Count
            last_seen_at = "2026-09-08T20:23:00Z"
            health = $health
        }
        if (-not $NoTopLevelVersion -and -not $NoVersionAnywhere) { $row["software_version"] = $Version }
        return [pscustomobject]@{ devices = @([pscustomobject]$row) }
    }

    $m183 = @("desktop.open_application", "browser.chrome", "browser.media_play", "browser.media_volume", "browser.media_status", "browser.media_stop")

    $script:clock = [datetime]"2026-09-08T20:22:00Z"
    $live = { New-CoreRow -Presence "online" -Version "0.6.0" -Caps $m183 }
    $hb6 = Test-AgentHeartbeatOnCore -FetchDevices $live -DeviceId $device -ExpectedVersion "0.6.0" -ExpectedCapabilities $m183 -TimeoutSeconds 90 -PollSeconds 3 -Sleep $sleep -Now $now
    Assert-True ($hb6.Ok -and $hb6.Observed.software_version -eq "0.6.0" -and $hb6.Observed.capability_count -eq 6) "FORWARD: candidate 0.6.0 -> the production device row -> the verifier observes 0.6.0 and passes (this is the case that wrongly failed on 2026-09-08)"

    $script:clock = [datetime]"2026-09-08T20:22:00Z"
    $nestedOnly = { New-CoreRow -Presence "online" -Version "0.6.0" -Caps $m183 -NoTopLevelVersion }
    $hb7 = Test-AgentHeartbeatOnCore -FetchDevices $nestedOnly -DeviceId $device -ExpectedVersion "0.6.0" -ExpectedCapabilities $m183 -TimeoutSeconds 90 -PollSeconds 3 -Sleep $sleep -Now $now
    Assert-True ($hb7.Ok -and $hb7.Observed.software_version -eq "0.6.0") "a Cloud Core that has not been deployed yet (version only under health) is still read correctly - the installer does not require a lockstep deploy"

    $script:clock = [datetime]"2026-09-08T20:22:00Z"
    $noVersionAnywhere = { New-CoreRow -Presence "online" -Version "0.6.0" -Caps $m183 -NoVersionAnywhere }
    $hb8 = Test-AgentHeartbeatOnCore -FetchDevices $noVersionAnywhere -DeviceId $device -ExpectedVersion "0.6.0" -ExpectedCapabilities $m183 -TimeoutSeconds 6 -PollSeconds 3 -Sleep $sleep -Now $now
    Assert-True (-not $hb8.Ok -and ($hb8.Reasons -join " ") -match "carries no software version at all" -and ($hb8.Reasons -join " ") -match "Cloud Core contract fault") "REVERSE: a row that names no version ANYWHERE fails the candidate truthfully, and says the fault is Cloud Core's row - it never passes"

    $script:clock = [datetime]"2026-09-08T20:22:00Z"
    $wrongVersion = { New-CoreRow -Presence "online" -Version "0.1.0" -Caps $m183 }
    $hb9 = Test-AgentHeartbeatOnCore -FetchDevices $wrongVersion -DeviceId $device -ExpectedVersion "0.6.0" -ExpectedCapabilities $m183 -TimeoutSeconds 6 -PollSeconds 3 -Sleep $sleep -Now $now
    Assert-True (-not $hb9.Ok -and ($hb9.Reasons -join " ") -match "reports software version '0.1.0', the candidate is 0.6.0" -and ($hb9.Reasons -join " ") -notmatch "contract fault") "a device still on the OLD version fails as a version mismatch, distinct from a missing-version contract fault"

    $script:clock = [datetime]"2026-09-08T20:22:00Z"
    $preMedia = { New-CoreRow -Presence "online" -Version "0.6.0" -Caps @("desktop.open_application", "browser.chrome") }
    $hb10 = Test-AgentHeartbeatOnCore -FetchDevices $preMedia -DeviceId $device -ExpectedVersion "0.6.0" -ExpectedCapabilities $m183 -TimeoutSeconds 6 -PollSeconds 3 -Sleep $sleep -Now $now
    Assert-True (-not $hb10.Ok -and ($hb10.Reasons -join " ") -match "browser.media_play") "the right version with the wrong capability manifest still fails, naming the missing browser media operations"

    Write-Host "the candidate's identity (component, stamped version, capability fingerprint)"
    $staging3 = New-Staging "c"
    $identityManifest = New-AgentCandidateManifest -StagingRoot $staging3 -Components @("service", "companion") -ServiceManifest $serviceManifest
    Assert-True ($identityManifest.component -eq "device-service" -and $identityManifest.assembly_version -eq "0.2.0" -and $identityManifest.capability_manifest_version -eq "aabbccdd1122") "the candidate manifest carries the whole identity, not only the version"
    $vId = Test-AgentCandidateManifest -Manifest $identityManifest -StagingRoot $staging3 -RequireIdentity
    Assert-True ($vId.Ok) "a candidate that names itself fully verifies"

    $drifted = [pscustomobject]@{
        Ok = $true; Capabilities = @("desktop.open_application"); BrowserEnabled = $false; ExitCode = 0; StdErr = ""
        SoftwareVersion = "0.6.0"; Component = "device-service"; AssemblyVersion = "0.5.0"; CapabilityManifestVersion = "aabbccdd1122"
    }
    $driftedManifest = New-AgentCandidateManifest -StagingRoot $staging3 -Components @("service", "companion") -ServiceManifest $drifted
    $vDrift = Test-AgentCandidateManifest -Manifest $driftedManifest -StagingRoot $staging3 -RequireIdentity
    Assert-True (-not $vDrift.Ok -and ($vDrift.Reasons -join " ") -match "would announce 0.6.0 but its binary is stamped 0.5.0") "ONE canonical identity: a candidate whose announced version and binary stamp disagree is refused before the swap"

    $anonymous = [pscustomobject]@{
        Ok = $true; Capabilities = @("desktop.open_application"); BrowserEnabled = $false; ExitCode = 0; StdErr = ""; SoftwareVersion = "0.1.0"
    }
    $anonymousManifest = New-AgentCandidateManifest -StagingRoot $staging3 -Components @("service", "companion") -ServiceManifest $anonymous
    $vAnon = Test-AgentCandidateManifest -Manifest $anonymousManifest -StagingRoot $staging3 -RequireIdentity
    Assert-True (-not $vAnon.Ok -and ($vAnon.Reasons -join " ") -match "carries no assembly version" -and ($vAnon.Reasons -join " ") -match "does not name which component") "a binary older than the identity contract cannot become a candidate..."
    $vAnonOld = Test-AgentCandidateManifest -Manifest $anonymousManifest -StagingRoot $staging3
    Assert-True ($vAnonOld.Ok) "...but describing that same older binary (no -RequireIdentity) still works, which is what a rollback needs"

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
