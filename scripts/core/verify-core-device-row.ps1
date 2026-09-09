<#
.SYNOPSIS
    Read the LIVE Cloud Core device row through the installer's OWN verifier, and prove it
    agrees with what the installed agent says about itself. Read-only; nothing is installed,
    restarted, swapped or written on the device.

.DESCRIPTION
    2026-09-09: the owner's install rolled a healthy 0.6.0 candidate back after 90.6 s
    because `New-CoreDeviceFetcher` could not resolve `Invoke-JsonUtf8`, and Cloud Core was
    never asked anything at all. The installer then reported the emptiness of a reading it
    had not taken as evidence about Cloud Core's device row.

    Nothing before the swap had ever exercised that path against production. This script is
    that step, and it is deliberately the one thing the qualification's loopback stub cannot
    be: the REAL fetcher, a REAL owner session, the REAL Cloud Core, and the REAL device row
    of the machine it runs on - compared against the installed service's own answer to its
    `capabilities` verb.

    It proves, before any elevated command is offered:

      * the fetcher builds and resolves its dependency in whatever shell the owner is using
        (and refuses to claim anything if Invoke-JsonUtf8 was preloaded there, which would
        make the result an artefact of the shell rather than of the code);
      * an owner session can be minted from the DPAPI-stored credential;
      * Cloud Core lists THIS device, online, with a software version and a capability list;
      * that capability set is EXACTLY the set the installed binary advertises - which is
        what the health gate compares at install time, one candidate later.

    What it cannot prove is the candidate: that needs the candidate to be live, which needs
    the elevated install. It proves the READING, which is the half that failed.

    Run (either invocation form - both are exercised deliberately):
        powershell -NoProfile -ExecutionPolicy Bypass -File scripts\core\verify-core-device-row.ps1
        .\scripts\core\verify-core-device-row.ps1

    Exit code is the number of failed assertions.
#>
[CmdletBinding()]
param(
    [string]$BrokerRestUrl,
    [string]$DeviceId,
    [string]$InstallRoot = (Join-Path $env:ProgramFiles "PagentOS\agent"),
    [string]$EvidenceDir,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

#: scripts\core -> scripts -> repo root.
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

#: Whether the caller's shell already had it. Read BEFORE anything is dot-sourced: a pass
#: that depends on the developer's session is not evidence, and this is how that is caught
#: rather than assumed.
$preloaded = [bool](Get-Command -Name "Invoke-JsonUtf8" -CommandType Function -ErrorAction SilentlyContinue)

. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\InstallEvidence.ps1")
. (Join-Path $repoRoot "scripts\lib\AgentUpdate.ps1")

$script:Failures = 0
$script:Passes = 0
$script:Checks = @()

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) { $script:Passes++; if (-not $Quiet) { Write-Host "  PASS  $Message" } }
    else { $script:Failures++; Write-Host "  FAIL  $Message" -ForegroundColor Red }
    $script:Checks += [ordered]@{ ok = [bool]$Condition; message = $Message }
}

if (-not $BrokerRestUrl) {
    $settings = Join-Path $InstallRoot "service\appsettings.json"
    if (Test-Path -LiteralPath $settings) {
        # Read defensively: the installed file is flat (BrokerRestUrl at the top), an older
        # one nested it under Agent, and StrictMode turns a wrong guess into a crash rather
        # than a message about what is missing.
        $config = Get-Content -LiteralPath $settings -Raw | ConvertFrom-Json
        $top = @($config.PSObject.Properties.Name)
        if ($top -contains "BrokerRestUrl") { $BrokerRestUrl = [string]$config.BrokerRestUrl }
        elseif ($top -contains "Agent") { $BrokerRestUrl = [string]$config.Agent.BrokerRestUrl }
    }
}
if (-not $BrokerRestUrl) { throw "no broker REST url: pass -BrokerRestUrl, or install the agent first" }

Write-Host "the live Cloud Core device row, read through the installer's own verifier" -ForegroundColor Cyan
Write-Host "  broker: $BrokerRestUrl"

Assert-True (-not $preloaded) "Invoke-JsonUtf8 was NOT already defined in this shell - so what follows measures the code, not the session"

$serviceExe = Join-Path $InstallRoot "service\PagentOS.DeviceService.exe"
Assert-True (Test-Path -LiteralPath $serviceExe) "the installed service binary is where the installer expects it ($serviceExe)"

$installed = Get-InstalledAgentManifest -ServiceExe $serviceExe
Assert-True ($installed.Ok) "the installed service answers its own 'capabilities' verb (exit $($installed.ExitCode))"
$installedCaps = @($installed.Capabilities | ForEach-Object { [string]$_ } | Sort-Object -Culture "")
Write-Host "  installed: $(if ($installed.SoftwareVersion) { $installed.SoftwareVersion } else { 'pre-0.2.0 (no version verb)' }), $($installedCaps.Count) capabilities"

# The installer asks the service; the service reads its state from a directory only
# administrators can open, so unelevated the `identity` verb yields nothing. That is a fact
# about this script's privileges, not a defect, and it must not be reported as one - so the
# fallback is stated, checked, and recorded rather than guessed.
$deviceId = if ($DeviceId) { $DeviceId } else { Get-AgentDeviceId -ServiceExe $serviceExe }
$deviceIdSource = if ($DeviceId) { "parameter" } elseif ($deviceId) { "the service's identity verb" } else { "" }
if (-not $deviceId) {
    Write-Host "  NOTE: the installed service did not answer its 'identity' verb (its state directory is administrators-only, and this shell is not elevated). Falling back to the enrolled row named after this machine, which is checked below." -ForegroundColor Yellow
}

$token = Get-OwnerSessionToken -BaseUrl $BrokerRestUrl -Label "verify-core-device-row"
Assert-True ([bool]$token) "an owner session was minted from the DPAPI-stored credential (bootstrap-owner-credential.ps1 stores it)"

$row = $null
$rowError = ""
if ($token) {
    # The REAL fetcher - the object that failed on 2026-09-09 - built here and used here.
    $fetch = New-CoreDeviceFetcher -BaseUrl $BrokerRestUrl -Token $token
    Assert-True ($null -ne $fetch) "New-CoreDeviceFetcher built a fetcher without throwing"
    $listing = $null
    try { $listing = & $fetch }
    catch { $rowError = $_.Exception.Message }
    Assert-True ($rowError -eq "") "the fetcher READ Cloud Core - the step that never happened on 2026-09-09$(if ($rowError) { ": $rowError" })"
    if ($null -ne $listing) {
        if ($deviceId) {
            $row = Get-DeviceRowFromListing -Listing $listing -DeviceId $deviceId
        }
        else {
            $named = @(Get-ManifestMember $listing "devices" | Where-Object { [string](Get-ManifestMember $_ "name") -eq $env:COMPUTERNAME })
            Assert-True (@($named).Count -eq 1) "exactly one enrolled device is named '$env:COMPUTERNAME' ($(@($named).Count) found) - so the row below is unambiguously this machine's"
            if (@($named).Count -eq 1) {
                $row = $named[0]
                $deviceId = [string](Get-ManifestMember $row "device_id")
                $deviceIdSource = "the enrolled row named $env:COMPUTERNAME"
            }
        }
    }
}
$token = $null

Assert-True ($null -ne $row) "Cloud Core lists device $deviceId"

$rowVersion = ""
$rowCaps = @()
$presence = ""
$lastSeen = ""
if ($null -ne $row) {
    $names = @($row.PSObject.Properties.Name)
    $presence = if ($names -contains "presence") { [string]$row.presence } elseif ($names -contains "status") { [string]$row.status } else { "" }
    $rowVersion = [string](Get-DeviceRowSoftwareVersion -Row $row)
    $rowCaps = @(Get-ManifestMember $row "capabilities" | ForEach-Object { [string]$_ } | Sort-Object -Culture "")
    $lastSeen = [string](Get-ManifestMember $row "last_seen_at")

    Assert-True ($presence -eq "online") "the device is online ($presence)"
    Assert-True ($rowVersion -ne "") "the row carries a software version ('$rowVersion') - ADR-0090's contract fault, held against production"
    Assert-True ($rowCaps.Count -gt 0) "the row carries a capability list ($($rowCaps.Count) names)"
    Assert-True ([bool]$lastSeen) "the row carries a heartbeat timestamp ($lastSeen)"

    # The comparison the health gate makes at install time, made now against the release
    # that is actually running. If these two disagree TODAY, they would disagree about a
    # candidate too, and the owner would be asked to install into a rollback.
    $missing = @($installedCaps | Where-Object { $rowCaps -notcontains $_ })
    $extra = @($rowCaps | Where-Object { $installedCaps -notcontains $_ })
    Assert-True (@($missing).Count -eq 0) "every capability the installed binary advertises is on the row$(if (@($missing).Count) { ": MISSING $($missing -join ', ')" })"
    Assert-True (@($extra).Count -eq 0) "and the row advertises nothing the binary does not$(if (@($extra).Count) { ": EXTRA $($extra -join ', ')" })"

    if ($installed.SoftwareVersion) {
        Assert-True ($rowVersion -eq $installed.SoftwareVersion) "the row's version is the installed binary's own ($rowVersion vs $($installed.SoftwareVersion))"
    }
    else {
        # Never silently skipped: an old binary that cannot name itself is a fact about the
        # runtime, and it is exactly why item 28 exists.
        Write-Host "  NOTE: the installed release predates the version verb, so it cannot be compared with the row's '$rowVersion'. The candidate item 28 installs announces its version; this comparison becomes meaningful then." -ForegroundColor Yellow
    }
}

if (-not $EvidenceDir) { $EvidenceDir = Join-Path $repoRoot "docs\evidence" }
New-Item -ItemType Directory -Force -Path $EvidenceDir | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
$evidencePath = Join-Path $EvidenceDir "core-device-row-$stamp.json"
([ordered]@{
        record            = "the live Cloud Core device row, read through the installer's real verifier"
        recorded_at       = (Get-Date).ToUniversalTime().ToString("o")
        broker            = $BrokerRestUrl
        invocation        = @{ preloaded_invoke_jsonutf8 = $preloaded; ps_version = $PSVersionTable.PSVersion.ToString() }
        device_id         = $deviceId
        device_id_source  = $deviceIdSource
        installed_runtime = [ordered]@{
            software_version = [string]$installed.SoftwareVersion
            capability_count = $installedCaps.Count
            capabilities     = $installedCaps
        }
        device_row        = [ordered]@{
            presence         = $presence
            software_version = $rowVersion
            capability_count = $rowCaps.Count
            capabilities     = $rowCaps
            last_seen_at     = $lastSeen
            read_error       = $rowError
        }
        checks            = $script:Checks
        passed            = $script:Passes
        failed            = $script:Failures
    } | ConvertTo-Json -Depth 8) | Set-Content -LiteralPath $evidencePath -Encoding UTF8

Write-Host ""
Write-Host "evidence: $evidencePath"
if ($script:Failures -eq 0) {
    Write-Host "CLOUD CORE OBSERVATION PROVEN: $script:Passes checks passed - the real fetcher read the real row for this device, and its capability set is exactly the installed binary's." -ForegroundColor Green
}
else {
    Write-Host "CLOUD CORE OBSERVATION NOT PROVEN: $script:Failures of $($script:Passes + $script:Failures) checks failed." -ForegroundColor Red
}
exit $script:Failures
