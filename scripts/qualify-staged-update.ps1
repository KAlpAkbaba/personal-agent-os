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
      0. contract     - the fake device row below is DERIVED from Cloud Core's own source
                        (app/devices/types.py DEVICE_IDENTITY_KEYS), never restated here;
                        a row a PowerShell suite invents for itself is what hid 2026-09-08
      1. stage        - a candidate is described file by file and re-verified unchanged
      2. identity     - component, announced version, binary stamp, capability fingerprint
      3. manifest     - the complete expected capability manifest, including -DisplayPower
                        and the four browser.media_* names the wake alarm is built on
     3b. operator     - the -Operator variant (docs/OWNER_ACTIONS.md item 28 folds it into
                        the same UAC prompt) ADDS the four gated families and subtracts
                        nothing, so either spelling of the retry is a known shape
      4. promote      - engine swap of ALL THREE components (service, companion and the
                        browser worker) -> candidate startup -> health -> Cloud Core sees
                        the candidate's version AND every capability -> committed
     4b. stale worker - a companion that restarted the PREVIOUS worker out of a stale venv
                        (the 2026-09-04 defect) rolls the deployment back. Until 2026-09-09
                        the browser worker "proof" was a counter the health handler
                        incremented on itself, in a sandbox where no browser tree was ever
                        staged; this gate is the run that falsifies it
      5. displaypower - enabled for BOTH the service and the companion, and the three
                        display names advertised
      6. invalid      - a tampered candidate is refused BEFORE the swap (the browser
                        worker's package digest included), and an empty staged browser tree
                        is refused too, live trees intact
      7. rollback     - a candidate Cloud Core cannot see is rolled back, all three trees
                        are restored and the previous release is judged by its OWN
                        baseline predicate
      8. real verifier - 2026-09-09: all 71 gates above were green and the owner's install
                        still rolled back after 90.6 s, because every one of them hands the
                        health handler a FAKE fetch. The one part none of them built was
                        New-CoreDeviceFetcher, whose closure could not resolve
                        Invoke-JsonUtf8. So this gate builds the REAL fetcher in a clean
                        child process - nothing preloaded - and reads a real device row
                        through it, in BOTH invocation modes (`& script.ps1`, which is what
                        the owner types, and `-File`, which is what every harness here
                        uses; only the second one worked). Then it audits every library on
                        disk for the same shape.

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
# Gate 8 runs the REAL Cloud Core verifier in a clean child process; these two hold the
# loopback stub and the static closure guard, shared with scripts\tests so the
# qualification and the regression cannot drift apart.
. (Join-Path $repoRoot "scripts\tests\lib\LoopbackJson.ps1")
. (Join-Path $repoRoot "scripts\tests\lib\ClosureGuard.ps1")

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
    param([string]$DeviceId, [string]$Presence, [string]$Version, [string[]]$Capabilities, [string]$BuildId = "")
    # A row with no capability list is a caller bug, not an offline device: say so here
    # rather than letting @($null).Count fail deep inside a health handler.
    if ($null -eq $Capabilities) { throw "New-CoreListing was given no capability list for device $DeviceId" }
    return [pscustomobject]@{
        devices = @(
            [pscustomobject]@{
                device_id        = $DeviceId
                name             = "owner-pc"
                platform         = "windows"
                status           = $Presence
                presence         = $Presence
                software_version = $Version
                build_id         = $BuildId
                capabilities     = $Capabilities
                capability_count = @($Capabilities).Count
                last_seen_at     = (Get-Date).ToUniversalTime().ToString("o")
                health           = [pscustomobject]@{
                    last_hello_at    = (Get-Date).ToUniversalTime().ToString("o")
                    software_version = $Version
                    build_id         = $BuildId
                    heartbeat_age_s  = 1.0
                    recent_outcomes  = @()
                }
            }
        )
    }
}

function Get-CloudCoreIdentityKeys {
    <#
    .SYNOPSIS
        DEVICE_IDENTITY_KEYS, read out of CLOUD CORE'S OWN SOURCE rather than restated here.
    .DESCRIPTION
        The fake above is the same kind of object that hid the 2026-09-08 failure: a device
        row a PowerShell suite invented for itself. Inventing it is safe only while it is
        derived from the file that really emits it. This parses
        services/api/app/devices/types.py, so renaming a canonical key on the Cloud Core
        side fails THIS script instead of leaving it green against a row nobody serves.
        (The other direction is held by services/api/tests/unit/test_device_identity_contract.py,
        which reads scripts/lib/AgentUpdate.ps1 and this file.)
    #>
    param([Parameter(Mandatory = $true)][string]$TypesPath)
    if (-not (Test-Path -LiteralPath $TypesPath)) { throw "Cloud Core's device view types are not in this checkout: $TypesPath" }
    $text = [System.IO.File]::ReadAllText($TypesPath)
    $match = [regex]::Match($text, 'DEVICE_IDENTITY_KEYS[^=]*=\s*\(([^)]*)\)')
    if (-not $match.Success) { throw "DEVICE_IDENTITY_KEYS is not declared in $TypesPath; the device-row contract moved" }
    return @([regex]::Matches($match.Groups[1].Value, '"([a-z_]+)"') | ForEach-Object { $_.Groups[1].Value })
}

# ------------------------------------------------------------------ a sandbox install root

function New-QualificationBrowserTree {
    <#
    .SYNOPSIS
        A services\browser-shaped tree: the source package the release identity is read
        from, and the venv copy that is what actually executes.
    .DESCRIPTION
        Both copies exist because the 2026-09-04 defect was precisely their disagreement -
        a fresh source tree and a stale site-packages copy - and Test-LiveBrowserWorker
        exists to catch it. A browser tree with only one of them cannot exercise it.
    #>
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$WorkerVersion)
    $package = Join-Path $Path "browser_agent"
    $venvPackage = Join-Path $Path ".venv\Lib\site-packages\browser_agent"
    $scripts = Join-Path $Path ".venv\Scripts"
    foreach ($dir in @($package, $venvPackage, $scripts)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $worker = @(
        "WORKER_VERSION = `"$WorkerVersion`"",
        'CONTRACTS: dict[str, int] = {"browser.search": 3, "browser.media": 1}',
        "",
        "def main() -> None:",
        "    raise SystemExit(0)"
    ) -join "`n"
    foreach ($dir in @($package, $venvPackage)) {
        Set-Content -LiteralPath (Join-Path $dir "worker.py") -Value $worker -Encoding ASCII -NoNewline
        Set-Content -LiteralPath (Join-Path $dir "__init__.py") -Value "" -Encoding ASCII -NoNewline
    }
    Set-Content -LiteralPath (Join-Path $scripts "python.exe") -Value "python-$WorkerVersion" -Encoding ASCII
}

function Write-WorkerStartAudit {
    <#  One companion audit row in the shape Get-LatestWorkerStartAudit really parses.  #>
    param(
        [Parameter(Mandatory = $true)][string]$AuditPath,
        [Parameter(Mandatory = $true)][string]$WorkerVersion,
        [Parameter(Mandatory = $true)][string]$Module,
        [Parameter(Mandatory = $true)][int]$WorkerProcessId
    )
    $dir = Split-Path -Parent $AuditPath
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $row = [pscustomobject]@{
        ts     = (Get-Date).ToUniversalTime().ToString("o")
        event  = "browser_worker_started"
        detail = "pid=$WorkerProcessId; worker_version=$WorkerVersion; module=$Module"
    }
    Add-Content -LiteralPath $AuditPath -Value ($row | ConvertTo-Json -Compress) -Encoding UTF8
}

function New-QualificationRoot {
    <#
    .SYNOPSIS
        Live service/companion/browser trees plus a staged candidate, shaped like a real
        install - INCLUDING the browser component, which the installer really deploys
        (`-Components service,companion,browser -NonExecutableComponents browser`).
    #>
    param(
        [string]$Name, [string]$PreviousVersion = "0.1.0", [string]$CandidateVersion = "0.6.0",
        [string]$PreviousWorkerVersion = "0.4.0", [string]$CandidateWorkerVersion = "0.5.0"
    )
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
    # The Browser Worker (M13) carries no PagentOS executable - it is a Python environment -
    # so the engine promotes it as a NonExecutableComponent. It is still a component: staged,
    # described in the candidate manifest, swapped, proven live, and restored on rollback.
    New-QualificationBrowserTree -Path (Join-Path $root "browser") -WorkerVersion $PreviousWorkerVersion
    New-QualificationBrowserTree -Path (Join-Path $root ".staging\browser") -WorkerVersion $CandidateWorkerVersion
    New-Item -ItemType Directory -Force -Path (Join-Path $root "browser-data") | Out-Null
    return $root
}

function New-QualificationHealth {
    <#
    .SYNOPSIS
        The TestHealth handler this qualification hands the engine, shaped like the
        installer's own: the swapped trees must BE the candidate, the live browser worker
        must be proven BY THE INSTALLER'S OWN FUNCTIONS, and only then may Cloud Core be
        asked.
    .DESCRIPTION
        Every predicate here can fail. The one it replaces could not: the old handler
        incremented `$core.WorkerProofs++` on itself and then asserted the counter was
        non-zero, so "the browser worker proof was required before the commit" was true of
        a run in which no browser tree was ever staged, swapped or looked at. Gate 4b
        falsifies this handler by handing it a companion that restarted the PREVIOUS
        worker - the 2026-09-04 stale-venv defect - and requires the deployment to roll back.
    .PARAMETER ReportedWorkerVersion
        What the companion's audit will claim it started after the swap. Equal to the staged
        release in the healthy case; the previous release in gate 4b.
    .PARAMETER CoreSeesCandidate
        Whether the fake Cloud Core moves to the candidate's identity once it is live.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][hashtable]$State,
        [Parameter(Mandatory = $true)][string]$DeviceId,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion,
        [Parameter(Mandatory = $true)][string[]]$ExpectedCapabilities,
        [Parameter(Mandatory = $true)][string]$StagedWorkerVersion,
        [Parameter(Mandatory = $true)][string]$ReportedWorkerVersion,
        [bool]$CoreSeesCandidate = $true,
        [int]$CoreTimeoutSeconds = 15
    )
    $browserRoot = Join-Path $Root "browser"
    $browserDataDir = Join-Path $Root "browser-data"
    $auditPath = Join-Path $Root "audit\companion-audit.jsonl"
    $workerProcessId = 424242
    # Every function this closure calls, RESOLVED NOW and carried in a variable.
    #
    # 2026-09-09: the installer's Cloud Core fetcher was built the same way this handler is -
    # `.GetNewClosure()` - and called Invoke-JsonUtf8 by name. A closure's module is linked to
    # the GLOBAL session state, so a name that only exists in a dot-sourced SCRIPT scope does
    # not resolve, and the owner's install rolled back after 90.6 s. It only ever worked
    # because `-File` makes the outermost script the top-level scope; `.\qualify-staged-update.ps1`
    # would have failed here in exactly the same way. A qualification that passes only in the
    # invocation mode its harness happens to use is not evidence.
    $fn = @{}
    foreach ($name in @("Get-ExpectedWorkerRelease", "Write-WorkerStartAudit", "Wait-LiveBrowserWorkerAudit",
            "Test-LiveBrowserWorker", "Test-AgentHeartbeatOnCore", "New-CoreListing")) {
        $found = Get-Command -Name $name -CommandType Function -ErrorAction SilentlyContinue
        if (-not $found) { throw "the health handler cannot be built: $name is not defined here" }
        $fn[$name] = $found
    }
    return {
        $State.HealthCalls++
        # The candidate really is the tree that is now live.
        if ((Get-Content -LiteralPath (Join-Path $Root "service\PagentOS.service.exe")) -notlike "candidate-*") {
            $State.Problems = @("the live service tree is not the candidate")
            return $false
        }
        $State.CompanionBeats++

        # ---- the browser worker, judged by scripts\lib\BrowserRelease.ps1 itself ---------
        $deployStartedAt = (Get-Date).AddSeconds(-2)
        $release = & $fn["Get-ExpectedWorkerRelease"] -BrowserSource $browserRoot
        $State.LiveWorkerRelease = $release.Version
        if ($release.Version -ne $StagedWorkerVersion) {
            $State.Problems = @("the promoted browser tree is worker $($release.Version), the staged release is $StagedWorkerVersion")
            return $false
        }
        $module = Join-Path $browserRoot ".venv\Lib\site-packages\browser_agent\worker.py"
        $python = Join-Path $browserRoot ".venv\Scripts\python.exe"
        & $fn["Write-WorkerStartAudit"] -AuditPath $auditPath -WorkerVersion $ReportedWorkerVersion -Module $module -WorkerProcessId $workerProcessId
        $audit = & $fn["Wait-LiveBrowserWorkerAudit"] -AuditPath $auditPath -Since $deployStartedAt -TimeoutSeconds 5
        if ($null -eq $audit) {
            $State.Problems = @("the companion recorded no browser_worker_started row after the swap")
            return $false
        }
        # Win32_Process-shaped, injected: the qualification starts no python and reads no
        # real process table, but Test-LiveBrowserWorker is the production function.
        $processes = @([pscustomobject]@{
                ProcessId      = $workerProcessId
                Name           = "python.exe"
                ExecutablePath = $python
                CommandLine    = "`"$python`" -m browser_agent.worker --data-dir `"$browserDataDir`""
                CreationDate   = (Get-Date)
            })
        $problems = @(& $fn["Test-LiveBrowserWorker"] -Audit $audit -Expected $release -BrowserRoot $browserRoot `
                -BrowserDataDir $browserDataDir -DeployStartedAt $deployStartedAt -PreviousPids @() -Processes $processes)
        $State.Problems = @($problems)
        if (@($problems).Count -gt 0) { return $false }
        $State.WorkerProofs++

        # ---- and only now Cloud Core ----------------------------------------------------
        if ($CoreSeesCandidate) {
            $State.Version = $ExpectedVersion
            $State.Caps = $ExpectedCapabilities
        }
        # NOT .GetNewClosure(). This block is already inside a closure; GetNewClosure copies
        # only the CURRENT scope into a fresh module, and $State/$DeviceId live in the
        # enclosing closure's module scope - so a nested GetNewClosure here silently handed
        # New-CoreListing $null and every deployment failed as "Cloud Core could not be
        # read". A plain script block keeps this session state and resolves both.
        $fetch = { & $fn["New-CoreListing"] -DeviceId $DeviceId -Presence "online" -Version $State.Version -Capabilities $State.Caps }
        $heartbeat = & $fn["Test-AgentHeartbeatOnCore"] -FetchDevices $fetch -DeviceId $DeviceId `
            -ExpectedVersion $ExpectedVersion -ExpectedCapabilities $ExpectedCapabilities `
            -TimeoutSeconds $CoreTimeoutSeconds -PollSeconds 1
        $State.LastHeartbeat = $heartbeat
        if (-not $heartbeat.Ok) { $State.Problems = @($heartbeat.Reasons) }
        return [bool]$heartbeat.Ok
    }.GetNewClosure()
}

function New-QualificationState {
    <#  The mutable holder the health closure and the assertions share (by reference).  #>
    param([string]$BaselineVersion = "0.1.0")
    return @{
        Version           = $BaselineVersion
        Caps              = @("desktop.open_application", "browser.chrome")
        HealthCalls       = 0
        CompanionBeats    = 0
        WorkerProofs      = 0
        BaselineChecks    = 0
        Problems          = @()
        LiveWorkerRelease = ""
        LastHeartbeat     = $null
    }
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

    # -------------------------------------------------- gate 0: the device-row contract
    # The fake Cloud Core below is exactly the kind of object that hid the 2026-09-08
    # failure: a device row a PowerShell suite wrote for itself. It is only safe while it
    # is DERIVED from the file that really serves it, so read that file and check.
    Write-Gate "gate 0 - the fake Cloud Core's row is the shape Cloud Core's own source emits"
    $typesPath = Join-Path $repoRoot "services\api\app\devices\types.py"
    $identityKeys = @(Get-CloudCoreIdentityKeys -TypesPath $typesPath)
    Assert-True (@($identityKeys).Count -ge 6) "app/devices/types.py declares its canonical identity keys ($($identityKeys -join ', '))"
    $sampleRow = @((New-CoreListing -DeviceId "shape-probe" -Presence "online" -Version "0.0.1" -Capabilities @("desktop.open_application")).devices)[0]
    $rowKeys = @($sampleRow.PSObject.Properties.Name)
    $missingKeys = @($identityKeys | Where-Object { $rowKeys -notcontains $_ })
    Assert-True (@($missingKeys).Count -eq 0) "every canonical identity key is on the row this qualification judges$(if (@($missingKeys).Count) { ": MISSING $($missingKeys -join ', ')" })"
    Assert-True ($identityKeys -contains "software_version") "software_version is canonical AT THE TOP OF THE ROW - the key whose absence rolled a healthy 0.6.0 back"

    # ------------------------------------------------------------------ gate 2: identity
    Write-Gate "gate 2 - the candidate can name itself"
    $candidate = Get-ConfiguredManifest -Exe $serviceExe -BrowserEnabled $true -DisplayPowerEnabled $true
    Assert-True ($candidate.Ok) "the candidate answers its own 'capabilities' verb"
    Assert-True ($candidate.SoftwareVersion -match '^\d+\.\d+\.\d+$') "it announces a software version ($($candidate.SoftwareVersion))"
    Assert-True ($candidate.Component -eq "device-service") "it names which component announced it ($($candidate.Component))"
    Assert-True ($candidate.AssemblyVersion -eq $candidate.SoftwareVersion) "ONE canonical version identity: the binary is stamped $($candidate.AssemblyVersion), the same number it announces"
    # ADR-0118. The line above is the 2026-09-08 lesson: one canonical PRODUCT version. The
    # lines below are the other half of it. A product version is meant to STAY STILL across
    # builds - M28 deliberately did not bump 0.6.0 - so it can never prove which BUILD is
    # running, which is the question this whole script exists to answer.
    # An EMPTY answer here almost always means the candidate binary predates ADR-0118 -
    # this qualification judges bin\Release, which is not what `dotnet build` produces by
    # default, so a stale Release tree is judged as if it were the candidate. Say that,
    # rather than leaving a reader to work it out from an empty string.
    Assert-True ($candidate.BuildId -match '^[0-9a-f]{16}$') $(if ($candidate.BuildId) { "it also announces a BUILD identity ($($candidate.BuildId)) - 16 hex derived from its own assemblies, not a number anyone maintains" } else { "it also announces a BUILD identity - the candidate answered NOTHING, which means this binary predates ADR-0118: rebuild it with 'dotnet build devices\windows-agent\PagentOS.WindowsAgent.sln -c Release' and re-run, because a stale candidate proves nothing about the tree" })
    Assert-True ($candidate.BuildId -ne $candidate.SoftwareVersion -and $candidate.BuildId -ne $candidate.AssemblyVersion) "the build identity is NOT the product version wearing a different name"
    # A literal, like the shape probe above: $DeviceId is a parameter of the scenario
    # functions further down, not a script-scope variable.
    $identityProbeDevice = "build-identity-probe"
    $sameVersionOtherBuild = New-CoreListing -DeviceId $identityProbeDevice -Presence "online" -Version $candidate.SoftwareVersion -Capabilities $candidate.Capabilities -BuildId ("0" * 16)
    $swapNotTaken = Test-AgentHeartbeatOnCore -FetchDevices { $sameVersionOtherBuild } -DeviceId $identityProbeDevice -ExpectedVersion $candidate.SoftwareVersion -ExpectedBuildId $candidate.BuildId -ExpectedCapabilities $candidate.Capabilities -TimeoutSeconds 4 -PollSeconds 2
    Assert-True (-not $swapNotTaken.Ok -and ($swapNotTaken.Reasons -join " ") -match "reports build") "BUILD A != BUILD B: a row on the SAME product version but a different build is correctly refused, which the product version alone could never do"
    $sameVersionSameBuild = New-CoreListing -DeviceId $identityProbeDevice -Presence "online" -Version $candidate.SoftwareVersion -Capabilities $candidate.Capabilities -BuildId $candidate.BuildId
    $swapTaken = Test-AgentHeartbeatOnCore -FetchDevices { $sameVersionSameBuild } -DeviceId $identityProbeDevice -ExpectedVersion $candidate.SoftwareVersion -ExpectedBuildId $candidate.BuildId -ExpectedCapabilities $candidate.Capabilities -TimeoutSeconds 8 -PollSeconds 2
    Assert-True ($swapTaken.Ok -and $swapTaken.Observed.build_id -eq $candidate.BuildId) "...and the candidate's own build is accepted, with the identity recorded"
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
    # The components the INSTALLER really deploys, in its own spelling: the browser worker
    # is a component of every staged update, not a capability prefix.
    $deployComponents = @("service", "companion", "browser")
    $stagedWorkerVersion = "0.5.0"
    $previousWorkerVersion = "0.4.0"
    $stagedBrowserRelease = Get-ExpectedWorkerRelease -BrowserSource (Join-Path $staging "browser")
    Assert-True ($stagedBrowserRelease.Version -eq $stagedWorkerVersion) "the staged browser tree names its own worker release ($($stagedBrowserRelease.Version))"
    $manifest = New-AgentCandidateManifest -StagingRoot $staging -Components $deployComponents `
        -ServiceManifest $stagedManifest -BrowserRelease $stagedBrowserRelease -RepoHead "qualification"
    $manifestPath = Write-AgentCandidateManifest -Manifest $manifest -Path (Get-CandidateManifestPath -StagingRoot $staging)
    $verdict = Test-AgentCandidateManifest -Manifest (Read-AgentCandidateManifest -Path $manifestPath) -StagingRoot $staging `
        -RequireCapabilities @("desktop.open_application", "browser.chrome", "desktop.display_off") -RequireIdentity
    Assert-True ($verdict.Ok) "the staged candidate verifies against its manifest, file by file$(if (-not $verdict.Ok) { ": $($verdict.Reasons -join '; ')" })"
    Assert-True ($manifest.software_version -eq $candidate.SoftwareVersion -and $manifest.component -eq "device-service") "the manifest carries the candidate's whole identity into the deployment"
    Assert-True ([string]$manifest.components["browser"]["package_sha256"] -eq $stagedBrowserRelease.PackageSha256) "the manifest carries the staged BROWSER WORKER's release identity too (worker $($manifest.components["browser"]["worker_version"]), package $($stagedBrowserRelease.PackageSha256.Substring(0,12)))"

    $deviceId = "qualification-device"
    # A shared, MUTABLE holder rather than $script: variables: .GetNewClosure() copies the
    # variables it captures into the closure's own module scope, so a $script: assignment made
    # after the closure was created is invisible to it. A hashtable is captured by reference,
    # so the fake Cloud Core really does change its answer when the candidate starts.
    $core = New-QualificationState
    $health = New-QualificationHealth -Root $root -State $core -DeviceId $deviceId `
        -ExpectedVersion ([string]$manifest.software_version) -ExpectedCapabilities @($manifest.capabilities) `
        -StagedWorkerVersion $stagedWorkerVersion -ReportedWorkerVersion $stagedWorkerVersion
    $rollbackHealth = { return $true }
    # A failure here is a qualification RESULT, not a crash: the engine throws on an unhealthy
    # candidate, and the reasons the health handler recorded are the only useful evidence.
    $committed = $false
    try {
        $committed = Invoke-AgentDeployment -Root $root -Components $deployComponents -NonExecutableComponents @("browser") `
            -StopRuntime { } -StartRuntime { } -TestHealth $health -TestRollbackHealth $rollbackHealth
    }
    catch { Write-Host "  ....  the healthy candidate was refused: $($_.Exception.Message); handler said: $(@($core.Problems) -join '; ')" -ForegroundColor Yellow }
    Assert-True ([bool]$committed) "the engine promoted the candidate and committed"
    Assert-True ((Get-Content -LiteralPath (Join-Path $root "service\PagentOS.service.exe")) -eq "candidate-$($candidate.SoftwareVersion)") "the live service tree IS the candidate"
    Assert-True ((Get-Content -LiteralPath (Join-Path $root "companion\appsettings.json")) -match '"DisplayPowerEnabled":true') "the promoted companion configuration carries DisplayPowerEnabled - the companion reconnects with display power enabled"
    Assert-True ((Get-ExpectedWorkerRelease -BrowserSource (Join-Path $root "browser")).Version -eq $stagedWorkerVersion) "the live BROWSER tree is the candidate's worker ($stagedWorkerVersion), not the previous $previousWorkerVersion - the browser worker is promoted by the same transaction"
    Assert-True ($core.CompanionBeats -ge 1 -and $core.WorkerProofs -ge 1) "the companion heartbeat and the browser worker proof were both required before the commit"
    Assert-True (@($core.Problems).Count -eq 0) "...and the worker proof is Test-LiveBrowserWorker itself - version, module inside the promoted venv, pid, --data-dir and creation time all agreed$(if (@($core.Problems).Count) { ": $($core.Problems -join '; ')" })"
    Assert-True ($core.LastHeartbeat.Ok -and $core.LastHeartbeat.Observed.software_version -eq $candidate.SoftwareVersion) "Cloud Core saw the candidate's version ($($core.LastHeartbeat.Observed.software_version)) - THE 2026-09-08 FAILURE, now passing"
    Assert-True ($core.LastHeartbeat.Observed.capability_count -eq 40) "...and its complete capability manifest ($($core.LastHeartbeat.Observed.capability_count) capabilities)"
    Assert-True ((Read-DeployJournal -Root $root).phase -eq "committed") "the journal says committed"

    # ------------------------------- gate 4b: the browser worker proof is not a formality
    # Until 2026-09-09 the assertion above was satisfied by a counter the health handler
    # incremented on itself, in a sandbox where no browser tree was ever staged. It was
    # true of every possible run, including one in which the companion restarted the OLD
    # worker out of a stale venv - the 2026-09-04 defect the production installer rolls
    # back on. This gate is that run.
    Write-Gate "gate 4b - a stale browser worker rolls the deployment back"
    $staleRoot = New-QualificationRoot -Name "stale-worker"
    $staleState = New-QualificationState
    $staleHealth = New-QualificationHealth -Root $staleRoot -State $staleState -DeviceId $deviceId `
        -ExpectedVersion $candidate.SoftwareVersion -ExpectedCapabilities $caps `
        -StagedWorkerVersion $stagedWorkerVersion -ReportedWorkerVersion $previousWorkerVersion
    $staleThrew = $false
    try {
        Invoke-AgentDeployment -Root $staleRoot -Components $deployComponents -NonExecutableComponents @("browser") `
            -StopRuntime { } -StartRuntime { } -TestHealth $staleHealth -TestRollbackHealth { $true } | Out-Null
    }
    catch { $staleThrew = $_.Exception.Message -match "did not become healthy" }
    Assert-True $staleThrew "a companion that restarted the PREVIOUS worker fails the deployment"
    Assert-True (($staleState.Problems -join " ") -match "the companion started worker '$previousWorkerVersion', the staged release is '$stagedWorkerVersion'") "...for the reason Test-LiveBrowserWorker names, not a generic failure"
    Assert-True ($staleState.WorkerProofs -eq 0) "the worker proof was never granted - it is a predicate over the live process, not a counter the handler increments on itself"
    Assert-True ($staleState.CompanionBeats -ge 1) "...and it failed AFTER the service tree had already been accepted, so it is the browser half that refused"
    Assert-True ((Get-ExpectedWorkerRelease -BrowserSource (Join-Path $staleRoot "browser")).Version -eq $previousWorkerVersion) "the previous browser worker is live again"
    Assert-True ((Get-ExpectedWorkerRelease -BrowserSource (Join-Path $staleRoot ".staging\browser")).Version -eq $stagedWorkerVersion) "and the candidate worker went back to staging"

    # --------------------------------------------- gate 6: a deliberately invalid candidate
    Write-Gate "gate 6 - an invalid candidate never reaches the live tree"
    $badRoot = New-QualificationRoot -Name "invalid"
    $badStaging = Join-Path $badRoot ".staging"
    $badManifest = New-AgentCandidateManifest -StagingRoot $badStaging -Components $deployComponents -ServiceManifest $stagedManifest `
        -BrowserRelease (Get-ExpectedWorkerRelease -BrowserSource (Join-Path $badStaging "browser"))
    Set-Content -LiteralPath (Join-Path $badStaging "service\PagentOS.service.exe") -Value "TAMPERED" -Encoding ASCII
    $badVerdict = Test-AgentCandidateManifest -Manifest $badManifest -StagingRoot $badStaging -RequireIdentity
    Assert-True (-not $badVerdict.Ok -and ($badVerdict.Reasons -join " ") -match "changed since it was staged") "a candidate whose bytes changed after staging is refused, and the file is named"
    Assert-True ((Get-Content -LiteralPath (Join-Path $badRoot "service\PagentOS.service.exe")) -eq "previous-0.1.0") "the live tree was never touched"

    # The browser tree is verified by package DIGEST, not by file hashes, so it needs its own
    # tamper case: an edited worker.py after staging must be refused the same way.
    $badBrowserRoot = New-QualificationRoot -Name "invalid-browser"
    $badBrowserStaging = Join-Path $badBrowserRoot ".staging"
    $badBrowserManifest = New-AgentCandidateManifest -StagingRoot $badBrowserStaging -Components $deployComponents -ServiceManifest $stagedManifest `
        -BrowserRelease (Get-ExpectedWorkerRelease -BrowserSource (Join-Path $badBrowserStaging "browser"))
    Add-Content -LiteralPath (Join-Path $badBrowserStaging "browser\browser_agent\worker.py") -Value "# tampered after staging"
    $badBrowserVerdict = Test-AgentCandidateManifest -Manifest $badBrowserManifest -StagingRoot $badBrowserStaging -RequireIdentity
    Assert-True (-not $badBrowserVerdict.Ok -and ($badBrowserVerdict.Reasons -join " ") -match "staged browser package digest is") "a staged BROWSER WORKER whose package changed after staging is refused, and the digest is named"

    $emptyRoot = New-QualificationRoot -Name "empty-candidate"
    Remove-Item -LiteralPath (Join-Path $emptyRoot ".staging\service\PagentOS.service.exe") -Force
    $refused = $false
    try {
        Invoke-AgentDeployment -Root $emptyRoot -Components $deployComponents -NonExecutableComponents @("browser") `
            -StopRuntime { } -StartRuntime { } -TestHealth { $true } | Out-Null
    }
    catch { $refused = $_.Exception.Message -match "no PagentOS executable" }
    Assert-True $refused "a candidate with no executable is refused before the runtime is even stopped"

    # The browser component carries no PagentOS executable by design, so its own emptiness
    # check is the only thing standing between a failed `uv sync` and a promoted empty tree.
    $emptyBrowserRoot = New-QualificationRoot -Name "empty-browser"
    Remove-Item -LiteralPath (Join-Path $emptyBrowserRoot ".staging\browser") -Recurse -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $emptyBrowserRoot ".staging\browser") | Out-Null
    $browserRefused = $false
    try {
        Invoke-AgentDeployment -Root $emptyBrowserRoot -Components $deployComponents -NonExecutableComponents @("browser") `
            -StopRuntime { } -StartRuntime { } -TestHealth { $true } | Out-Null
    }
    catch { $browserRefused = $_.Exception.Message -match "staged 'browser' is empty" }
    Assert-True $browserRefused "an EMPTY staged browser worker is refused before the runtime is stopped, even though it is allowed to carry no executable"
    Assert-True ((Get-ExpectedWorkerRelease -BrowserSource (Join-Path $emptyBrowserRoot "browser")).Version -eq $previousWorkerVersion) "...and the working browser worker is still live"

    # ------------------------------------------------------------------- gate 7: rollback
    Write-Gate "gate 7 - rollback still works, and reports the previous release truthfully"
    $rollbackRoot = New-QualificationRoot -Name "rollback"
    $rollbackState = New-QualificationState
    # The candidate is locally perfect - service swapped, browser worker proven - and Cloud
    # Core still never comes to see it. That is the 2026-09-08 shape exactly.
    $failingHealth = New-QualificationHealth -Root $rollbackRoot -State $rollbackState -DeviceId $deviceId `
        -ExpectedVersion $candidate.SoftwareVersion -ExpectedCapabilities $caps `
        -StagedWorkerVersion $stagedWorkerVersion -ReportedWorkerVersion $stagedWorkerVersion `
        -CoreSeesCandidate $false -CoreTimeoutSeconds 2
    $baseline = { $rollbackState.BaselineChecks++; return $true }.GetNewClosure()
    $threw = $false
    try {
        Invoke-AgentDeployment -Root $rollbackRoot -Components $deployComponents -NonExecutableComponents @("browser") `
            -StopRuntime { } -StartRuntime { } -TestHealth $failingHealth -TestRollbackHealth $baseline | Out-Null
    }
    catch { $threw = $_.Exception.Message -match "did not become healthy" }
    Assert-True $threw "a candidate Cloud Core cannot see fails the deployment"
    Assert-True (($rollbackState.Problems -join " ") -match "reports software version '0.1.0'") "...for the right, named reason"
    Assert-True ($rollbackState.WorkerProofs -ge 1) "...and it is CLOUD CORE that refused: the browser worker had already been proven live"
    Assert-True ((Get-Content -LiteralPath (Join-Path $rollbackRoot "service\PagentOS.service.exe")) -eq "previous-0.1.0") "the previous release is live again"
    Assert-True ((Get-Content -LiteralPath (Join-Path $rollbackRoot "companion\PagentOS.companion.exe")) -eq "previous-0.1.0") "BOTH halves are restored, not only the service"
    Assert-True ((Get-ExpectedWorkerRelease -BrowserSource (Join-Path $rollbackRoot "browser")).Version -eq $previousWorkerVersion) "ALL THREE trees are restored: the browser worker is the previous release ($previousWorkerVersion) again, not the candidate's"
    Assert-True ((Get-Content -LiteralPath (Join-Path $rollbackRoot "companion\appsettings.json")) -match '"DisplayPowerEnabled":false') "and the restored configuration is the previous one - a rolled-back device does not keep the candidate's -DisplayPower"
    Assert-True (Test-Path -LiteralPath (Join-Path $rollbackRoot ".staging\service")) "the candidate went back to staging so a retry costs nothing"
    Assert-True ((Get-ExpectedWorkerRelease -BrowserSource (Join-Path $rollbackRoot ".staging\browser")).Version -eq $stagedWorkerVersion) "...the candidate browser worker included"
    Assert-True ($rollbackState.HealthCalls -eq 1 -and $rollbackState.BaselineChecks -eq 1) "the candidate's contract was asked once, about the candidate; the restored release was judged by its own baseline predicate"
    $journal = Read-DeployJournal -Root $rollbackRoot
    Assert-True ($journal.phase -eq "rolled_back") "the journal records the rollback"
    $rolled = @($journal.history | Where-Object { $_.phase -eq "rolled_back" })[-1]
    Assert-True ($rolled.detail -match "restored and healthy") "and reports the restored release as healthy, not as a capability regression that never happened"

    # -------------------------------------------------- gate 8: the REAL Cloud Core verifier
    # Every gate above this line hands the health handler a FAKE fetch script block, which is
    # why all 71 of them were green on 2026-09-09 while the installer could not read Cloud
    # Core at all. The thing that failed was the one part no gate had ever built:
    # New-CoreDeviceFetcher, invoked the way the owner invokes the installer.
    #
    # So this gate builds the real fetcher in a CLEAN child process - nothing preloaded, no
    # profile - and points it at a loopback HTTP stub. It runs it BOTH ways, because the two
    # differ and only one of them is what the owner does:
    #
    #     command   `& install.ps1`      a child script scope   <- the owner
    #     file      `-File install.ps1`  the top-level scope    <- every harness here
    #
    # A pass that depends on Invoke-JsonUtf8 already being loaded in the developer's shell
    # would be worthless, so "nothing was preloaded" is itself asserted first.
    Write-Gate "gate 8 - the REAL Cloud Core verifier, from a clean process, in the owner's invocation mode"
    $verifierCaps = @("desktop.open_application", "browser.chrome", "desktop.display_off")
    $verifierVersion = $candidate.SoftwareVersion
    $stub = Start-JsonStub -Json (New-CoreDeviceListingJson -DeviceId $deviceId -Version $verifierVersion -Capabilities $verifierCaps)
    try {
        foreach ($mode in @("command", "file")) {
            $verdict = Invoke-CoreVerifierChild -Mode $mode -RepoRoot $repoRoot -Sandbox $script:Sandbox -Port $stub.Port `
                -DeviceId $deviceId -ExpectedVersion $verifierVersion -ExpectedCapabilities $verifierCaps
            Assert-True (-not $verdict.preloaded_globally) "[$mode] the child had NO Invoke-JsonUtf8 preloaded - without this the rest of this gate proves nothing"
            Assert-True ([bool]$verdict.built) "[$mode] New-CoreDeviceFetcher built a fetcher$(if ($verdict.build_error) { ": $($verdict.build_error)" })"
            Assert-True ([bool]$verdict.ok) "[$mode] the REAL fetcher read the device row and the candidate verified$(if (@($verdict.reasons).Count) { ": $(@($verdict.reasons) -join '; ')" })"
            Assert-True (-not [bool]$verdict.verifier_fault) "[$mode] no verifier fault"
            Assert-True ($verdict.observed_version -eq $verifierVersion -and [int]$verdict.observed_caps -eq $verifierCaps.Count) "[$mode] the whole chain ran, UTF-8 body decode included: '$($verdict.observed_version)', $($verdict.observed_caps) capabilities"
            Assert-True ([int]$verdict.attempts -eq 1) "[$mode] one attempt - no timeout was spent on a fault waiting cannot fix"
        }
    }
    finally { Stop-JsonStub -Stub $stub }

    # And the class of defect, not only this instance: no closure anywhere in the installer's
    # own libraries may call a command that a fresh PowerShell does not know.
    # The library list is READ FROM DISK, not typed here. The 2026-09-06 guard had a
    # hand-maintained list, AgentUpdate.ps1 was never added to it, and three days later
    # AgentUpdate.ps1 shipped the same defect. A new library is covered the moment it exists.
    $closureAudited = @("scripts\install-device-service.ps1", "scripts\qualify-staged-update.ps1")
    foreach ($lib in @(Get-ChildItem -LiteralPath (Join-Path $repoRoot "scripts\lib") -Filter *.ps1 -File)) {
        $closureAudited += "scripts\lib\$($lib.Name)"
    }
    $closureRisks = @()
    foreach ($rel in $closureAudited) {
        # ASSIGNED, not wrapped in @(): `return , $empty` yields one element when it is
        # wrapped and none when it is assigned, which is how this audit first reported a
        # risk in all 21 clean files.
        $found = Get-ClosureCommandRisks -Path (Join-Path $repoRoot $rel)
        foreach ($site in $found) { $closureRisks += "$rel $site" }
    }
    Assert-True (@($closureAudited).Count -ge 10) "the closure audit read its file list from disk ($(@($closureAudited).Count) files), so a new library cannot be forgotten the way AgentUpdate.ps1 was"
    Assert-True (@($closureRisks).Count -eq 0) "no closure in the installer or its libraries calls a name a fresh PowerShell cannot resolve$(if (@($closureRisks).Count) { ": $($closureRisks -join '; ')" })"

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
    Write-Host "  device-row contract read from Cloud Core's own source -> stage -> manifest verified"
    Write-Host "  file by file (browser worker package digest included) -> candidate identity -> complete"
    Write-Host "  capability manifest (40 with -DisplayPower, browser.media_* included) -> promotion of"
    Write-Host "  service, companion AND browser worker -> live worker proven by Test-LiveBrowserWorker"
    Write-Host "  -> Cloud Core sees the candidate's version and capabilities -> committed. A stale"
    Write-Host "  worker, a tampered or empty candidate, and a candidate Cloud Core cannot see each"
    Write-Host "  roll back with all three trees restored. Gate 8 then builds the REAL fetcher in a"
    Write-Host "  clean child process, in BOTH invocation modes, and reads a device row through it -"
    Write-Host "  the step whose absence let 71 green checks precede the 2026-09-09 rollback."
}
else {
    Write-Host "STAGED UPDATE NOT QUALIFIED: $script:Failures of $($script:Passes + $script:Failures) checks failed." -ForegroundColor Red
}
exit $exitCode
