<#
.SYNOPSIS
    Automated qualification of the Windows agent's STAGED UPDATE, end to end, before the
    owner is asked to run install-device-service.ps1 again.

.DESCRIPTION
    2026-09-08 production incident. The owner ran
    `install-device-service.ps1 -DisplayPower`. Candidate 0.6.0 staged, promoted and
    started; Cloud Core saw it with all 40 of its capabilities; and the installer still
    rolled it back after 92.6 s with

        Cloud Core does not see the candidate: the device reports software version ""

    because its verifier read a device-row key Cloud Core had never emitted. Nothing in
    any gate had ever walked that chain end to end: the PowerShell half tested itself
    against a device row it had invented, and the Cloud Core half never looked at the
    verifier at all.

    This script walks the chain, deterministically, with no elevation, no service, no
    network, and WITHOUT TOUCHING THE LIVE INSTALL. It uses the REAL parts wherever a real
    part is what failed:

      * the REAL DeviceService binary answers its own `capabilities` verb (configured
        through PAGENTOS_AGENT_* environment variables, so no file in any build or install
        tree is modified);
      * the REAL candidate manifest functions describe and re-verify the staged trees;
      * the REAL journaled deployment engine performs the swap, health check, commit and
        rollback against sandbox directories;
      * the REAL Cloud Core verifier reads a device row built to the shape
        `services/api/app/devices/types.py` actually emits (that file's own test,
        tests/unit/test_device_identity_contract.py, holds the other end).

    Gates, in order:
      1. stage        - a candidate is described file by file and re-verified unchanged
      2. identity     - component, announced version, binary stamp, capability fingerprint
      3. manifest     - the complete expected capability manifest, including -DisplayPower
                        and the four browser.media_* names the wake alarm is built on
     3b. operator     - the -Operator variant (docs/OWNER_ACTIONS.md item 28 folds it into
                        the same UAC prompt) ADDS the four gated families and subtracts
                        nothing, so either spelling of the retry is a known shape
      4. promote      - engine swap -> candidate startup -> health -> Cloud Core sees the
                        candidate's version AND every capability -> committed
      5. displaypower - enabled for BOTH the service and the companion, and the three
                        display names advertised
      6. invalid      - a tampered candidate is refused BEFORE the swap, live tree intact
      7. rollback     - a candidate Cloud Core cannot see is rolled back, the previous
                        release is restored and judged by its OWN baseline predicate

    Run: powershell -NoProfile -File scripts\qualify-staged-update.ps1
    Exit code 0 means the owner's retry is expected to succeed for the reasons above.
#>
[CmdletBinding()]
param(
    [string]$ServiceExe,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$PSNativeCommandUseErrorActionPreference = $false

$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\InstallEvidence.ps1")
. (Join-Path $repoRoot "scripts\lib\AgentUpdate.ps1")
. (Join-Path $repoRoot "scripts\lib\Deployment.ps1")

$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-staged-update-qualification-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null

function Write-Gate { param([string]$Name) if (-not $Quiet) { Write-Host ""; Write-Host "== $Name" -ForegroundColor Cyan } }
function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) { $script:Passes++; if (-not $Quiet) { Write-Host "  PASS  $Message" } }
    else { $script:Failures++; Write-Host "  FAIL  $Message" -ForegroundColor Red }
}

# ---------------------------------------------------------------- the real service binary

function Resolve-BuiltServiceExe {
    <#  The DeviceService produced by `dotnet build -c Release`, never the installed one.  #>
    param([string]$Explicit)
    if ($Explicit) {
        if (-not (Test-Path -LiteralPath $Explicit)) { throw "-ServiceExe '$Explicit' does not exist" }
        return $Explicit
    }
    $candidates = @(
        (Join-Path $repoRoot "devices\windows-agent\src\PagentOS.DeviceService\bin\Release\net10.0-windows\PagentOS.DeviceService.exe"),
        (Join-Path $repoRoot "devices\windows-agent\src\PagentOS.DeviceService\bin\Debug\net10.0-windows\PagentOS.DeviceService.exe")
    )
    foreach ($candidate in $candidates) { if (Test-Path -LiteralPath $candidate) { return $candidate } }
    throw ("no built PagentOS.DeviceService.exe found. Build it first:`n" +
        "  dotnet build devices\windows-agent\PagentOS.WindowsAgent.sln -c Release`n" +
        "(this qualification never reads the INSTALLED service; it must judge the candidate, not production)")
}

function Get-ConfiguredManifest {
    <#
    .SYNOPSIS
        The capability manifest the built binary would advertise under a given install
        configuration - obtained by asking the binary, through the environment.
    .DESCRIPTION
        Configuration comes from PAGENTOS_AGENT_* rather than a written appsettings.json so
        this qualification cannot modify any tree: not the build output, and certainly not
        the owner's live install.
    #>
    param([string]$Exe, [bool]$BrowserEnabled, [bool]$DisplayPowerEnabled, [bool]$OperatorEnabled = $false)
    $saved = @{}
    $names = @("PAGENTOS_AGENT_BrowserEnabled", "PAGENTOS_AGENT_DisplayPowerEnabled", "PAGENTOS_AGENT_OperatorEnabled")
    foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name) }
    try {
        $env:PAGENTOS_AGENT_BrowserEnabled = "$BrowserEnabled".ToLowerInvariant()
        $env:PAGENTOS_AGENT_DisplayPowerEnabled = "$DisplayPowerEnabled".ToLowerInvariant()
        $env:PAGENTOS_AGENT_OperatorEnabled = "$OperatorEnabled".ToLowerInvariant()
        return Get-InstalledAgentManifest -ServiceExe $Exe
    }
    finally {
        foreach ($name in $names) {
            if ($null -eq $saved[$name]) { Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue }
            else { Set-Item -Path "Env:$name" -Value $saved[$name] }
        }
    }
}

# ------------------------------------------------------------------- the fake Cloud Core

function New-CoreListing {
    <#
    .SYNOPSIS
        A /v1/devices document in the shape Cloud Core really returns.
    .DESCRIPTION
        The identity keys are at the TOP of the row (app.devices.types.DEVICE_IDENTITY_KEYS)
        and the same version is repeated inside `health`. Getting this shape wrong - by
        inventing one - is what made the 2026-09-08 failure invisible to every suite.
    #>
    param([string]$DeviceId, [string]$Presence, [string]$Version, [string[]]$Capabilities)
    return [pscustomobject]@{
        devices = @(
            [pscustomobject]@{
                device_id        = $DeviceId
                name             = "owner-pc"
                platform         = "windows"
                status           = $Presence
                presence         = $Presence
                software_version = $Version
                capabilities     = $Capabilities
                capability_count = @($Capabilities).Count
                last_seen_at     = (Get-Date).ToUniversalTime().ToString("o")
                health           = [pscustomobject]@{
                    last_hello_at    = (Get-Date).ToUniversalTime().ToString("o")
                    software_version = $Version
                    heartbeat_age_s  = 1.0
                    recent_outcomes  = @()
                }
            }
        )
    }
}

# ------------------------------------------------------------------ a sandbox install root

function New-QualificationRoot {
    <#  live service/companion trees plus a staged candidate, shaped like a real install.  #>
    param([string]$Name, [string]$PreviousVersion = "0.1.0", [string]$CandidateVersion = "0.6.0")
    $root = Join-Path $script:Sandbox $Name
    foreach ($component in @("service", "companion")) {
        New-Item -ItemType Directory -Force -Path (Join-Path $root $component) | Out-Null
        Set-Content -LiteralPath (Join-Path $root "$component\PagentOS.$component.exe") -Value "previous-$PreviousVersion" -Encoding ASCII
        Set-Content -LiteralPath (Join-Path $root "$component\appsettings.json") -Value '{"DisplayPowerEnabled":false}' -Encoding ASCII
        New-Item -ItemType Directory -Force -Path (Join-Path $root ".staging\$component") | Out-Null
        Set-Content -LiteralPath (Join-Path $root ".staging\$component\PagentOS.$component.exe") -Value "candidate-$CandidateVersion" -Encoding ASCII
        # -DisplayPower writes the SAME flag into BOTH staged configuration files.
        Set-Content -LiteralPath (Join-Path $root ".staging\$component\appsettings.json") -Value '{"DisplayPowerEnabled":true}' -Encoding ASCII
    }
    return $root
}

$exitCode = 1
try {
    $serviceExe = Resolve-BuiltServiceExe -Explicit $ServiceExe
    if (-not $Quiet) {
        Write-Host "staged-update qualification"
        Write-Host "  candidate binary : $serviceExe"
        Write-Host "  sandbox          : $script:Sandbox"
        Write-Host "  the live install, the running service, the companion and the browser worker are never touched."
    }

    # ------------------------------------------------------------------ gate 2: identity
    Write-Gate "gate 2 - the candidate can name itself"
    $candidate = Get-ConfiguredManifest -Exe $serviceExe -BrowserEnabled $true -DisplayPowerEnabled $true
    Assert-True ($candidate.Ok) "the candidate answers its own 'capabilities' verb"
    Assert-True ($candidate.SoftwareVersion -match '^\d+\.\d+\.\d+$') "it announces a software version ($($candidate.SoftwareVersion))"
    Assert-True ($candidate.Component -eq "device-service") "it names which component announced it ($($candidate.Component))"
    Assert-True ($candidate.AssemblyVersion -eq $candidate.SoftwareVersion) "ONE canonical version identity: the binary is stamped $($candidate.AssemblyVersion), the same number it announces"
    Assert-True ($candidate.CapabilityManifestVersion -match '^[0-9a-f]{12}$') "it carries a capability manifest fingerprint ($($candidate.CapabilityManifestVersion))"

    # ------------------------------------------------------- gate 3: the whole manifest
    Write-Gate "gate 3 - the complete expected capability manifest"
    $caps = @($candidate.Capabilities)
    $expectedDesktop = @(
        "desktop.open_application", "desktop.open_artifact",
        "desktop.alarm_start", "desktop.alarm_stop",
        "desktop.display_wake", "desktop.display_status", "desktop.activity_status",
        "desktop.alarm_arm", "desktop.alarm_disarm", "desktop.play_audio",
        "desktop.display_off"
    )
    $missingDesktop = @($expectedDesktop | Where-Object { $caps -notcontains $_ })
    Assert-True (@($missingDesktop).Count -eq 0) "every desktop name the M18/M18.3 architecture needs is advertised$(if (@($missingDesktop).Count) { ": MISSING $($missingDesktop -join ', ')" })"
    $expectedBrowser = @("browser.chrome") + @(
        "browser.session_open", "browser.session_close", "browser.worker_status",
        "browser.navigate", "browser.back", "browser.forward",
        "browser.tab_list", "browser.tab_new", "browser.tab_close", "browser.tab_select",
        "browser.inspect", "browser.find", "browser.click", "browser.fill", "browser.select_option",
        "browser.set_checked", "browser.scroll", "browser.wait", "browser.extract", "browser.snapshot",
        "browser.screenshot", "browser.download", "browser.search", "browser.fetch_evidence",
        "browser.media_play", "browser.media_volume", "browser.media_status", "browser.media_stop")
    $missingBrowser = @($expectedBrowser | Where-Object { $caps -notcontains $_ })
    Assert-True (@($missingBrowser).Count -eq 0) "the whole browser family is advertised, contract v1.2 included$(if (@($missingBrowser).Count) { ": MISSING $($missingBrowser -join ', ')" })"
    $media = @("browser.media_play", "browser.media_volume", "browser.media_status", "browser.media_stop")
    $missingMedia = @($media | Where-Object { $caps -notcontains $_ })
    Assert-True (@($missingMedia).Count -eq 0) "the four wake-alarm media operations are present by name (the YouTube wake-music path is built on exactly these)"
    Assert-True ($caps.Count -eq 40) "the candidate advertises 40 capabilities with -DisplayPower and the browser worker - the number Cloud Core observed at 20:23Z (actual: $($caps.Count))"
    Assert-True (@($caps | Group-Object | Where-Object { $_.Count -gt 1 }).Count -eq 0) "no capability is advertised twice"

    # The owner's own action list (docs/OWNER_ACTIONS.md item 28/F) folds -Operator into the
    # same UAC prompt, so the retry may well carry it. Prove that variant is an ADDITION -
    # every name of the -DisplayPower manifest still there, plus the four gated families -
    # rather than a different manifest that would surprise the health check.
    Write-Gate "gate 3b - the -Operator variant adds, and subtracts nothing"
    $withOperator = Get-ConfiguredManifest -Exe $serviceExe -BrowserEnabled $true -DisplayPowerEnabled $true -OperatorEnabled $true
    $operatorCaps = @($withOperator.Capabilities)
    $lost = @($caps | Where-Object { $operatorCaps -notcontains $_ })
    Assert-True (@($lost).Count -eq 0) "-Operator keeps every name the -DisplayPower manifest advertises$(if (@($lost).Count) { ": LOST $($lost -join ', ')" })"
    Assert-True ($operatorCaps.Count -gt $caps.Count) "...and adds the gated families ($($operatorCaps.Count) capabilities with -Operator, $($caps.Count) without)"
    foreach ($probe in @("app.launch", "file.search", "project.scaffold", "scene.inspect")) {
        Assert-True ($operatorCaps -contains $probe) "$probe appears only behind -Operator"
        Assert-True ($caps -notcontains $probe) "...and is absent without it"
    }
    Assert-True ($withOperator.CapabilityManifestVersion -eq $candidate.CapabilityManifestVersion) "the capability manifest fingerprint describes the BINARY's whole vocabulary, so it does not change with a flag"

    # --------------------------------------------------------------- gate 5: DisplayPower
    Write-Gate "gate 5 - -DisplayPower, on both halves"
    Assert-True ($candidate.DisplayPowerEnabled) "the service reports display_power_enabled=true when it is configured"
    Assert-True ($caps -contains "desktop.display_off") "...and advertises desktop.display_off"
    foreach ($name in @("desktop.display_wake", "desktop.display_status")) {
        Assert-True ($caps -contains $name) "$name is advertised (it adds; it never needed the flag)"
    }
    $withoutFlag = Get-ConfiguredManifest -Exe $serviceExe -BrowserEnabled $true -DisplayPowerEnabled $false
    Assert-True (@($withoutFlag.Capabilities) -notcontains "desktop.display_off") "without the flag the same binary does NOT advertise desktop.display_off - the flag is what does it, not the version"
    Assert-True (-not $withoutFlag.DisplayPowerEnabled) "...and reports display_power_enabled=false"
    Assert-True (@($withoutFlag.Capabilities).Count -eq 39) "which is exactly one capability fewer (39)"
    $installer = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\install-device-service.ps1"))
    Assert-True ($installer -match '-DisplayPowerEnabled \(\[bool\]\$DisplayPower\)') "the installer writes DisplayPowerEnabled into the SERVICE's staged configuration"
    Assert-True ($installer -match '(?s)if \(\$DisplayPower\) \{\s*\$companionConfig\["DisplayPowerEnabled"\] = \$true') "the installer writes DisplayPowerEnabled into the COMPANION's staged configuration too"
    Assert-True ($installer -match '(?s)Write-JsonFile -Path \(Join-Path \$stagedCompanionDir "appsettings.json"\)') "...and that configuration is written into STAGING, so a promotion carries it and a rollback does not"

    # ------------------------------------------- gates 1, 4: stage, verify, promote, commit
    Write-Gate "gates 1 and 4 - stage, verify file by file, promote, commit"
    $root = New-QualificationRoot -Name "promote"
    $staging = Join-Path $root ".staging"
    $stagedManifest = [pscustomobject]@{
        Ok = $true; Capabilities = $caps; BrowserEnabled = $true; ExitCode = 0; StdErr = ""
        SoftwareVersion = $candidate.SoftwareVersion; Component = $candidate.Component
        AssemblyVersion = $candidate.AssemblyVersion; CapabilityManifestVersion = $candidate.CapabilityManifestVersion
    }
    $manifest = New-AgentCandidateManifest -StagingRoot $staging -Components @("service", "companion") `
        -ServiceManifest $stagedManifest -RepoHead "qualification"
    $manifestPath = Write-AgentCandidateManifest -Manifest $manifest -Path (Get-CandidateManifestPath -StagingRoot $staging)
    $verdict = Test-AgentCandidateManifest -Manifest (Read-AgentCandidateManifest -Path $manifestPath) -StagingRoot $staging `
        -RequireCapabilities @("desktop.open_application", "browser.chrome", "desktop.display_off") -RequireIdentity
    Assert-True ($verdict.Ok) "the staged candidate verifies against its manifest, file by file$(if (-not $verdict.Ok) { ": $($verdict.Reasons -join '; ')" })"
    Assert-True ($manifest.software_version -eq $candidate.SoftwareVersion -and $manifest.component -eq "device-service") "the manifest carries the candidate's whole identity into the deployment"

    $deviceId = "qualification-device"
    # A shared, MUTABLE holder rather than $script: variables: .GetNewClosure() copies the
    # variables it captures into the closure's own module scope, so a $script: assignment made
    # after the closure was created is invisible to it. A hashtable is captured by reference,
    # so the fake Cloud Core really does change its answer when the candidate starts.
    $core = @{
        Version      = "0.1.0"
        Caps         = @("desktop.open_application", "browser.chrome")
        CompanionBeats = 0
        WorkerProofs = 0
        LastHeartbeat = $null
    }
    $fetch = { New-CoreListing -DeviceId $deviceId -Presence "online" -Version $core.Version -Capabilities $core.Caps }.GetNewClosure()
    $health = {
        # The candidate really is the tree that is now live.
        if ((Get-Content -LiteralPath (Join-Path $root "service\PagentOS.service.exe")) -notlike "candidate-*") { return $false }
        # The companion reconnected and the browser worker came up as the staged release.
        $core.CompanionBeats++
        $core.WorkerProofs++
        # The candidate has now announced itself; Cloud Core's row moves to the new identity.
        $core.Version = $candidate.SoftwareVersion
        $core.Caps = $caps
        $heartbeat = Test-AgentHeartbeatOnCore -FetchDevices $fetch -DeviceId $deviceId `
            -ExpectedVersion ([string]$manifest.software_version) -ExpectedCapabilities @($manifest.capabilities) `
            -TimeoutSeconds 15 -PollSeconds 1
        $core.LastHeartbeat = $heartbeat
        return [bool]$heartbeat.Ok
    }.GetNewClosure()
    $rollbackHealth = { return $true }
    $committed = Invoke-AgentDeployment -Root $root -Components @("service", "companion") `
        -StopRuntime { } -StartRuntime { } -TestHealth $health -TestRollbackHealth $rollbackHealth
    Assert-True ([bool]$committed) "the engine promoted the candidate and committed"
    Assert-True ((Get-Content -LiteralPath (Join-Path $root "service\PagentOS.service.exe")) -eq "candidate-$($candidate.SoftwareVersion)") "the live service tree IS the candidate"
    Assert-True ((Get-Content -LiteralPath (Join-Path $root "companion\appsettings.json")) -match '"DisplayPowerEnabled":true') "the promoted companion configuration carries DisplayPowerEnabled - the companion reconnects with display power enabled"
    Assert-True ($core.CompanionBeats -ge 1 -and $core.WorkerProofs -ge 1) "the companion heartbeat and the browser worker proof were both required before the commit"
    Assert-True ($core.LastHeartbeat.Ok -and $core.LastHeartbeat.Observed.software_version -eq $candidate.SoftwareVersion) "Cloud Core saw the candidate's version ($($core.LastHeartbeat.Observed.software_version)) - THE 2026-09-08 FAILURE, now passing"
    Assert-True ($core.LastHeartbeat.Observed.capability_count -eq 40) "...and its complete capability manifest ($($core.LastHeartbeat.Observed.capability_count) capabilities)"
    Assert-True ((Read-DeployJournal -Root $root).phase -eq "committed") "the journal says committed"

    # --------------------------------------------- gate 6: a deliberately invalid candidate
    Write-Gate "gate 6 - an invalid candidate never reaches the live tree"
    $badRoot = New-QualificationRoot -Name "invalid"
    $badStaging = Join-Path $badRoot ".staging"
    $badManifest = New-AgentCandidateManifest -StagingRoot $badStaging -Components @("service", "companion") -ServiceManifest $stagedManifest
    Set-Content -LiteralPath (Join-Path $badStaging "service\PagentOS.service.exe") -Value "TAMPERED" -Encoding ASCII
    $badVerdict = Test-AgentCandidateManifest -Manifest $badManifest -StagingRoot $badStaging -RequireIdentity
    Assert-True (-not $badVerdict.Ok -and ($badVerdict.Reasons -join " ") -match "changed since it was staged") "a candidate whose bytes changed after staging is refused, and the file is named"
    Assert-True ((Get-Content -LiteralPath (Join-Path $badRoot "service\PagentOS.service.exe")) -eq "previous-0.1.0") "the live tree was never touched"

    $emptyRoot = New-QualificationRoot -Name "empty-candidate"
    Remove-Item -LiteralPath (Join-Path $emptyRoot ".staging\service\PagentOS.service.exe") -Force
    $refused = $false
    try {
        Invoke-AgentDeployment -Root $emptyRoot -Components @("service", "companion") `
            -StopRuntime { } -StartRuntime { } -TestHealth { $true } | Out-Null
    }
    catch { $refused = $_.Exception.Message -match "no PagentOS executable" }
    Assert-True $refused "a candidate with no executable is refused before the runtime is even stopped"

    # ------------------------------------------------------------------- gate 7: rollback
    Write-Gate "gate 7 - rollback still works, and reports the previous release truthfully"
    $rollbackRoot = New-QualificationRoot -Name "rollback"
    $counters = @{ Candidate = 0; Baseline = 0; Reasons = @() }
    $failingHealth = {
        $counters.Candidate++
        # Cloud Core never comes to see this candidate.
        $stuck = { New-CoreListing -DeviceId $deviceId -Presence "online" -Version "0.1.0" -Capabilities @("desktop.open_application") }.GetNewClosure()
        $heartbeat = Test-AgentHeartbeatOnCore -FetchDevices $stuck -DeviceId $deviceId `
            -ExpectedVersion $candidate.SoftwareVersion -ExpectedCapabilities $caps -TimeoutSeconds 2 -PollSeconds 1
        $counters.Reasons = $heartbeat.Reasons
        return [bool]$heartbeat.Ok
    }.GetNewClosure()
    $baseline = { $counters.Baseline++; return $true }.GetNewClosure()
    $threw = $false
    try {
        Invoke-AgentDeployment -Root $rollbackRoot -Components @("service", "companion") `
            -StopRuntime { } -StartRuntime { } -TestHealth $failingHealth -TestRollbackHealth $baseline | Out-Null
    }
    catch { $threw = $_.Exception.Message -match "did not become healthy" }
    Assert-True $threw "a candidate Cloud Core cannot see fails the deployment"
    Assert-True (($counters.Reasons -join " ") -match "reports software version '0.1.0'") "...for the right, named reason"
    Assert-True ((Get-Content -LiteralPath (Join-Path $rollbackRoot "service\PagentOS.service.exe")) -eq "previous-0.1.0") "the previous release is live again"
    Assert-True ((Get-Content -LiteralPath (Join-Path $rollbackRoot "companion\PagentOS.companion.exe")) -eq "previous-0.1.0") "BOTH halves are restored, not only the service"
    Assert-True ((Get-Content -LiteralPath (Join-Path $rollbackRoot "companion\appsettings.json")) -match '"DisplayPowerEnabled":false') "and the restored configuration is the previous one - a rolled-back device does not keep the candidate's -DisplayPower"
    Assert-True (Test-Path -LiteralPath (Join-Path $rollbackRoot ".staging\service")) "the candidate went back to staging so a retry costs nothing"
    Assert-True ($counters.Candidate -eq 1 -and $counters.Baseline -eq 1) "the candidate's contract was asked once, about the candidate; the restored release was judged by its own baseline predicate"
    $journal = Read-DeployJournal -Root $rollbackRoot
    Assert-True ($journal.phase -eq "rolled_back") "the journal records the rollback"
    $rolled = @($journal.history | Where-Object { $_.phase -eq "rolled_back" })[-1]
    Assert-True ($rolled.detail -match "restored and healthy") "and reports the restored release as healthy, not as a capability regression that never happened"

    $exitCode = if ($script:Failures -eq 0) { 0 } else { 1 }
}
catch {
    Write-Host ""
    Write-Host "QUALIFICATION ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace
    $script:Failures++
    $exitCode = 1
}
finally {
    Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
if ($script:Failures -eq 0) {
    Write-Host "STAGED UPDATE QUALIFIED: $script:Passes checks passed." -ForegroundColor Green
    Write-Host "  stage -> manifest verified file by file -> candidate identity -> complete capability"
    Write-Host "  manifest (40 with -DisplayPower, browser.media_* included) -> promotion -> companion and"
    Write-Host "  browser worker health -> Cloud Core sees the candidate's version and capabilities ->"
    Write-Host "  committed; an invalid candidate is refused before the swap; a candidate Cloud Core"
    Write-Host "  cannot see is rolled back with both halves restored."
}
else {
    Write-Host "STAGED UPDATE NOT QUALIFIED: $script:Failures of $($script:Passes + $script:Failures) checks failed." -ForegroundColor Red
}
exit $exitCode
