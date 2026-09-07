<#
.SYNOPSIS
    Installs the Personal Agent OS Device Service as a real Windows Service (LocalSystem,
    Session 0) and registers the Session Companion to start in the owner's session.

.DESCRIPTION
    This is the step that turns the developer topology (two console processes owned by the
    same user) into the production one (a Session-0 service plus a desktop companion), and it
    is the reason ADR-0028's identity model exists. The install is what makes that model
    real, so it does three things a naive "sc create" would not:

      * it pins the owner's SID and the companion's installed path into the service's
        configuration, so the service admits exactly one account running exactly one binary;
      * it installs into Program Files with an ACL that denies write to non-administrators,
        which is what makes pinning the binary meaningful — an attacker running as the owner
        cannot replace the file the service will accept;
      * it keeps service state out of the owner's profile, because a Session-0 service
        writing to a user profile is a topology mistake waiting to be discovered later.

    Requires elevation (one UAC prompt). Nothing here contacts the network except the
    optional enrollment step, which talks to the broker the owner names.

.PARAMETER OwnerSid
    SID of the account whose companion may connect. Defaults to the user running this script
    via RunAs — i.e. the owner — which is normally correct.

.PARAMETER BrokerRestUrl / BrokerWsUrl
    Where the agent reaches the cloud core. Defaults to the local dev broker.

.PARAMETER EnrollmentToken
    Optional. When given, the device is enrolled during install using the service's own data
    directory, so the service starts already enrolled. Mint the token from an authenticated
    owner session: POST /v1/devices/enrollment-tokens.

.PARAMETER InstallRoot
    Defaults to "$env:ProgramFiles\PagentOS\agent".

.PARAMETER SkipBrowser
    Do not provision the Browser Worker (M13). The service then advertises the desktop
    family only (BrowserEnabled=false) and the companion is written without BrowserWorker*
    settings; an existing <InstallRoot>\browser tree is left alone but unused.

.PARAMETER BrowserChannel
    "chrome" (installed Google Chrome — the qualification target, default) or "chromium".

.PARAMETER DisplayPower
    Enable desktop.display_off (M18/M18.3). OFF by default and expected to stay off until
    display-off has passed its own owner qualification: a wrong inference that blanks the
    screen interrupts unrelated owner work. The switch writes DisplayPowerEnabled=true to
    BOTH configuration files, because the service and the companion gate it independently
    and neither knows about the other; setting one alone produces a device that either
    advertises a name it will refuse, or refuses a name it advertised.

    Waking and reporting a display (desktop.display_wake, desktop.display_status) need no
    switch. They add; they never subtract.

.PARAMETER Operator
    M19 (ADR-0082): enable the Digital Operator family (app.*, window.*, keyboard.*,
    pointer.*, ui.*, screen.*, file.open/reveal, terminal.*). OFF by default. Like
    -DisplayPower it writes OperatorEnabled=true to BOTH configuration files: the service
    advertises and routes the family, the companion executes it; one without the other is
    a device that lies about what it can do. Every action stays behind the focus guard,
    the path roots and the terminal allowlist (DEVICE_PROTOCOL.md §6i).

.PARAMETER UvPath
    Explicit path to uv.exe. By default uv is resolved the way scripts\preflight.ps1
    resolves it (PATH, then the known install locations) — never assumed.

.PARAMETER SkipCoreVerify
    M18.4 gap 3: after the candidate started, the installer normally asks Cloud Core (with
    the DPAPI-stored owner credential, never printed) whether the device is ONLINE and
    reports the candidate's software version and capabilities; a candidate Cloud Core does
    not see within -CoreVerifyTimeoutSeconds is rolled back to the previous trees by the
    journaled engine. This switch skips that read (a machine without the credential, or an
    offline install). Without the credential the read is reported as skipped, never faked.

.EXAMPLE
    # From an elevated PowerShell, at the repository root:
    .\scripts\install-device-service.ps1 -BrokerRestUrl http://100.x.y.z:8001 -EnrollmentToken <token>
#>
[CmdletBinding()]
param(
    [string]$OwnerSid,
    [string]$BrokerRestUrl = "http://127.0.0.1:8001",
    [string]$BrokerWsUrl,
    [string]$EnrollmentToken,
    [string]$InstallRoot = (Join-Path $env:ProgramFiles "PagentOS\agent"),
    [string]$DataDir = (Join-Path $env:ProgramData "PagentOS\agent"),
    [string]$ServiceName = "PagentOSDeviceAgent",
    [switch]$SkipBuild,
    [switch]$SkipBrowser,
    [ValidateSet("chrome", "chromium")][string]$BrowserChannel = "chrome",
    [switch]$DisplayPower,
    [switch]$Operator,
    [string]$UvPath,
    [switch]$SkipCoreVerify,
    [int]$CoreVerifyTimeoutSeconds = 90
)

$ErrorActionPreference = "Stop"
# Child processes (dotnet, sc.exe, icacls) write progress to stderr; under PowerShell 5.1
# redirection that would become a terminating error. Judge them by exit code instead.
$PSNativeCommandUseErrorActionPreference = $false

$repoRoot = Split-Path -Parent $PSScriptRoot
$agentRoot = Join-Path $repoRoot "devices\windows-agent"

# Native tools are invoked through these rather than by string concatenation. Windows
# PowerShell 5.1 does not escape an argument that itself contains quotes, which is what sent
# sc.exe a malformed binPath and produced ERROR_INVALID_COMMAND_LINE (1639) after publishing
# and ACL hardening had already succeeded. See scripts/lib/NativeProcess.ps1.
. (Join-Path $PSScriptRoot "lib\NativeProcess.ps1")
. (Join-Path $PSScriptRoot "lib\ServiceInstall.ps1")
. (Join-Path $PSScriptRoot "lib\InstallAcl.ps1")
. (Join-Path $PSScriptRoot "lib\BrowserProvision.ps1")
. (Join-Path $PSScriptRoot "lib\BrowserRelease.ps1")
# The journaled deployment engine (stop -> swap -> start -> health -> commit, rollback
# otherwise), its production runtime handlers, and the post-install evidence. The inline
# swap this replaced renamed live directories under running processes; NTFS refused, and
# on 2026-09-03 the owner's M13 update looked successful while the old binaries kept running.
. (Join-Path $PSScriptRoot "lib\Deployment.ps1")
. (Join-Path $PSScriptRoot "lib\AgentRuntime.ps1")
. (Join-Path $PSScriptRoot "lib\InstallEvidence.ps1")
# M18.4 gap 3: the candidate manifest and the heartbeat/capability verification on Cloud Core.
. (Join-Path $PSScriptRoot "lib\AgentUpdate.ps1")

function Assert-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "This script installs a Windows Service and must run elevated. Right-click PowerShell -> Run as administrator, then run it again."
    }
}

function Resolve-OwnerSid {
    param([string]$Explicit)
    if ($Explicit) { return $Explicit }

    # Under RunAs the elevated process still belongs to the owner's account, so its SID is
    # the one we want. An explicit -OwnerSid covers the case where the machine's
    # administrator is a different person than the agent's owner.
    $sid = ([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value
    Write-Host "owner SID (from the elevated session): $sid"
    return $sid
}

function Get-DotnetPath {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Microsoft\dotnet\dotnet.exe"),
        (Join-Path $env:ProgramFiles "dotnet\dotnet.exe"),
        "dotnet"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -eq "dotnet") {
            $found = Get-Command dotnet -ErrorAction SilentlyContinue
            if ($found) { return $found.Source }
        }
        elseif (Test-Path $candidate) { return $candidate }
    }
    throw "no dotnet SDK found; install .NET 10 SDK or pass -SkipBuild with a prepared publish output"
}

function Publish-Agent {
    param([string]$Dotnet, [string]$Project, [string]$Output)
    Write-Host "publishing $(Split-Path -Leaf $Project) -> $Output"
    # Self-contained: the service must not depend on a machine-wide .NET runtime that a
    # later update could remove out from under it. The owner's desktop agent going away
    # because an unrelated runtime was uninstalled is not an acceptable failure mode.
    $result = Invoke-NativeProcess -FilePath $Dotnet -Arguments @(
        "publish", $Project,
        "-c", "Release",
        "-r", "win-x64",
        "--self-contained", "true",
        "-p:PublishSingleFile=false",
        "-o", $Output,
        "--nologo"
    ) -TimeoutSeconds 900
    Assert-NativeSuccess -Result $result -Activity "dotnet publish $(Split-Path -Leaf $Project)"
}

function Set-InstallAcl {
    param([string]$Path)
    # Administrators and SYSTEM own the tree; everyone else may read and execute, never
    # write. This is precisely what makes CompanionImagePath pinning worth checking: if the
    # owner could overwrite the companion binary, "the peer is the installed companion"
    # would prove nothing about what that binary contains.
    #
    # Explicit ACEs on the root only; children inherit. The earlier version pushed (OI)(CI)
    # grants to every child with /T, and those flags do not apply to a leaf, so 219 files
    # were left with a protected empty DACL that denied even SYSTEM.
    Write-Host "restricting write access on $Path to SYSTEM and Administrators"
    Set-HardenedAcl -Root $Path
}

function Assert-InstallPosture {
    param([string]$Path)
    # Re-derived from disk, not from what was just applied. This is the check that would have
    # caught the empty-DACL breakage at install time instead of at the next run.
    $posture = Test-InstallAclPosture -Root $Path
    if (-not $posture.Ok) {
        $lines = @("the installed tree does not have the intended security posture:")
        $lines += ($posture.Violations | Select-Object -First 12 | ForEach-Object { "  - $_" })
        if (@($posture.Violations).Count -gt 12) {
            $lines += "  ... and $(@($posture.Violations).Count - 12) more"
        }
        throw ($lines -join [Environment]::NewLine)
    }
    Write-Host "verified posture on $($posture.Checked) objects: only SYSTEM and Administrators can write"
}

function Write-ServiceConfig {
    param(
        [string]$ServiceDir,
        [string]$CompanionExe,
        [string]$OwnerSid,
        [string]$DataDir,
        [string]$BrokerRestUrl,
        [string]$BrokerWsUrl,
        [bool]$BrowserEnabled,
        [bool]$DisplayPowerEnabled,
        [bool]$OperatorEnabled = $false
    )
    $config = [ordered]@{
        BrokerRestUrl      = $BrokerRestUrl
        BrokerWsUrl        = $BrokerWsUrl
        DataDir            = $DataDir
        # The pipe name is per-owner so two accounts on one machine never collide.
        PipeName           = "pagentos-companion-$OwnerSid"
        # ADR-0028: the service admits one account, running one binary, in an interactive
        # session. None of this is inferred from the service's own identity, because under
        # LocalSystem that identity is not the owner's.
        CompanionSid       = $OwnerSid
        CompanionImagePath = $CompanionExe
        # M13: advertise and route browser.* only when a worker was provisioned and proved
        # to start. Never true by default; a device must not promise what it cannot do.
        BrowserEnabled     = $BrowserEnabled
        # M18/M18.3: display-off is the one capability that takes something away from the
        # owner, so it is advertised and routed only when this was asked for out loud. The
        # companion carries the same flag; both must agree or the device lies one way or the
        # other about what it can do.
        DisplayPowerEnabled = $DisplayPowerEnabled
        # M19: the Digital Operator family, asked for out loud (-Operator); the companion
        # carries the same flag, for the same reason as display power.
        OperatorEnabled    = $OperatorEnabled
    }
    $path = Join-Path $ServiceDir "appsettings.json"
    Write-JsonFile -Path $path -Content ($config | ConvertTo-Json -Depth 4)
    Write-Host "wrote $path (no secrets: URLs, paths and the owner SID only)"
}

function Invoke-BrowserWorkerStaging {
    <#
    .SYNOPSIS
        Stage the Browser Worker (M13): copy services\browser, create its venv with uv,
        prove the venv is relocatable, and prove the worker starts (--self-check exit 0).
        Nothing touches the live tree; a failure here throws with the reason and leaves the
        previous install exactly as it was.
    #>
    param(
        [string]$RepoRoot,
        [string]$InstallRoot,
        [string]$Channel,
        [string]$ExplicitUv
    )

    $uv = Resolve-UvPath -Explicit $ExplicitUv
    Write-Host "uv: $uv"

    $source = Join-Path $RepoRoot "services\browser"
    $staged = New-StagingDirectory -Root $InstallRoot -Name "browser"
    $copied = Copy-BrowserPackageTree -Source $source -Destination $staged
    Write-Host "staged browser package ($copied files) -> $staged"

    # The interpreter goes under the install root too, so the tree is self-contained and
    # admin-protected like the binaries it serves; `copy` link mode keeps site-packages
    # from hard-linking into the installing account's cache. `--no-editable` installs the
    # package as a real copy: an editable .pth would name the STAGING path and break the
    # moment the directory is moved into place.
    $saved = @{
        UV_PYTHON_INSTALL_DIR = $env:UV_PYTHON_INSTALL_DIR
        UV_PYTHON_PREFERENCE  = $env:UV_PYTHON_PREFERENCE
        UV_LINK_MODE          = $env:UV_LINK_MODE
        UV_PROJECT_ENVIRONMENT = $env:UV_PROJECT_ENVIRONMENT
    }
    try {
        $env:UV_PYTHON_INSTALL_DIR = Join-Path $InstallRoot "python"
        $env:UV_PYTHON_PREFERENCE = "managed"
        $env:UV_LINK_MODE = "copy"
        $env:UV_PROJECT_ENVIRONMENT = $null
        # --reinstall-package: uv's build cache for a local project is keyed on the mtime of
        # pyproject.toml, and the staging path is the same on every release, so a code-only
        # release was served a STALE cached wheel (2026-09-04, ADR-0050 item 16). The package
        # is always rebuilt from the staged source.
        Write-Host "creating the worker environment: uv $((Get-BrowserWorkerSyncArguments) -join ' ') (python under $($env:UV_PYTHON_INSTALL_DIR))"
        $sync = Invoke-NativeProcess -FilePath $uv -Arguments (Get-BrowserWorkerSyncArguments) `
            -WorkingDirectory $staged -TimeoutSeconds 1800
        Assert-NativeSuccess -Result $sync -Activity "uv sync (browser worker environment)"
    }
    finally {
        foreach ($name in @($saved.Keys)) {
            if ($null -eq $saved[$name]) { Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue }
            else { Set-Item -Path "Env:$name" -Value $saved[$name] }
        }
    }

    $venv = Join-Path $staged ".venv"
    $python = Get-BrowserWorkerPython -BrowserRoot $staged
    if (-not (Test-Path -LiteralPath $python)) { throw "uv sync finished but $python does not exist" }
    $stagingRoot = Join-Path $InstallRoot ".staging"
    $references = @(Test-VenvStagingReferences -VenvDir $venv -StagingRoot $stagingRoot)
    if (@($references).Count -gt 0) {
        throw ("the worker environment names its staging path and would break when moved into place:`n  - " + ($references -join "`n  - "))
    }
    Write-Host "worker environment is relocatable (interpreter: $(Get-VenvHome -VenvDir $venv))"

    # The self-check resolves the real channel executable and reads its file version (it
    # never starts a browser - chrome.exe --version on Windows opens a window), prints the
    # hello and exits. It
    # runs against a throwaway data directory so nothing owned by this ELEVATED process
    # lands where the owner's companion must later write.
    $probeData = Join-Path $env:TEMP "pagentos-browser-selfcheck-$([guid]::NewGuid().ToString('N'))"
    try {
        Write-Host "self-check: python -m browser_agent.worker --self-check --channel $Channel"
        # The self-check runs the way the companion runs the worker: from the DATA directory,
        # never from the browser tree. With the tree as cwd, Python imports the source copy on
        # sys.path[0] and the check passes even when the venv's installed copy is stale.
        $check = Invoke-BrowserWorkerSelfCheck -Python $python -Channel $Channel -DataDir $probeData
    }
    finally {
        Remove-Item -LiteralPath $probeData -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (-not $check.Ok) {
        $lines = @(
            "the Browser Worker self-check failed (exit $($check.ExitCode)); the previous install is untouched.",
            "  A non-zero exit means Google Chrome (channel '$Channel') or the worker package is not usable on this machine.",
            "  Install Chrome (or pass -BrowserChannel chromium), or rerun with -SkipBrowser to install without the browser family.",
            "  command: $python -m browser_agent.worker --self-check --channel $Channel"
        )
        if ($check.StdErr -and $check.StdErr.Trim()) {
            $lines += "  stderr : $((($check.StdErr.Trim() -split "`r?`n") | Select-Object -Last 12) -join "`n           ")"
        }
        throw ($lines -join [Environment]::NewLine)
    }
    Write-Host "self-check ok: worker $($check.Hello.worker_version), $(@($check.Capabilities).Count) capabilities, browser $($check.Hello.browser.channel) $($check.Hello.browser.version)"

    # The worker that answered must be THE staged release, executing from the staged venv:
    # version, contracts, worker.py hash and whole-package digest all agree with the staged
    # source, and the installed site-packages copy is byte-identical to it.
    $expected = Get-ExpectedWorkerRelease -BrowserSource $staged
    $proof = Assert-WorkerHelloMatchesRelease -Hello $check.Hello -Expected $expected -BrowserRoot $staged -Label "staged worker"
    $copyDifferences = @(Compare-BrowserPackageCopies -Source (Join-Path $staged "browser_agent") -Installed (Get-SitePackagesBrowserAgentDir -BrowserRoot $staged))
    if (@($copyDifferences).Count -gt 0) {
        throw "the staged venv's installed package differs from the staged source: $($copyDifferences -join '; ')"
    }
    Write-Host "staged release proven: worker $($proof.Version), package digest $($proof.PackageSha256.Substring(0,12)), module $($proof.ModuleFile)"

    return [pscustomobject]@{
        StagedPath   = $staged
        Hello        = $check.Hello
        Capabilities = @($check.Capabilities)
        Release      = $expected
    }
}

function Install-Service {
    param([string]$Name, [string]$Exe)

    # All of the registration logic — including deciding whether this is a first install, a
    # repoint, or a rerun that should change nothing — lives in scripts/lib/ServiceInstall.ps1
    # so it can be tested without elevation and without touching the real SCM.
    return Install-DeviceServiceRegistration -ServiceName $Name -ExecutablePath $Exe -ServiceArguments @("run")
}

function Register-CompanionAutostart {
    param([string]$CompanionExe, [string]$OwnerSid)
    $taskName = "PagentOS Session Companion"
    $account = (New-Object Security.Principal.SecurityIdentifier($OwnerSid)).Translate([Security.Principal.NTAccount]).Value

    # A logon task rather than a Run key: it survives profile tooling, can be inspected, and
    # runs the pinned binary from Program Files rather than a copy someone dropped in the
    # user profile.
    $action = New-ScheduledTaskAction -Execute $CompanionExe
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $account
    $principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 3 `
        -ExecutionTimeLimit ([TimeSpan]::Zero)

    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force | Out-Null
    Write-Host "registered logon task '$taskName' for $account"
}

# ------------------------------------------------------------------------------- install

Assert-Elevated

# Every run is transcribed under ProgramData: an elevated window that closes on error must
# not take the only copy of the failure with it (that is how the 2026-09-03 rerun went
# undiagnosed). The path is printed first and again on failure.
$installLogDir = Join-Path $env:ProgramData "PagentOS\install-logs"
New-Item -ItemType Directory -Force -Path $installLogDir | Out-Null
$script:InstallLog = Join-Path $installLogDir ("install-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")
Start-Transcript -Path $script:InstallLog -Append | Out-Null
Write-Host "install log: $script:InstallLog"
trap {
    Write-Host ""
    Write-Host "INSTALL FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "nothing that failed was left half-done: the deployment engine restores the previous trees and restarts the runtime on any failure after the swap."
    Write-Host "install log: $script:InstallLog"
    try { Stop-Transcript | Out-Null } catch { }
    break
}

$OwnerSid = Resolve-OwnerSid -Explicit $OwnerSid
$serviceDir = Join-Path $InstallRoot "service"
Write-Host "repo HEAD: $(Get-RepoHead -RepoRoot $repoRoot)"
Write-Host "parameters: SkipBuild=$([bool]$SkipBuild) SkipBrowser=$([bool]$SkipBrowser) BrowserChannel=$BrowserChannel InstallRoot=$InstallRoot"

# M13 upgrade safety: a re-run that does not name a broker keeps the one the installed
# service already dials (after RQ-2 that is the Hetzner tailnet address, written in place by
# switch-agent-broker.ps1). The loopback default is for a FIRST install only.
$endpoints = Resolve-BrokerEndpoints -ExplicitRestUrl $BrokerRestUrl -ExplicitWsUrl $BrokerWsUrl `
    -RestUrlWasExplicit ($PSBoundParameters.ContainsKey("BrokerRestUrl")) `
    -Installed (Get-InstalledBrokerEndpoints -ServiceDir $serviceDir) `
    -DefaultRestUrl "http://127.0.0.1:8001"
$BrokerRestUrl = $endpoints.BrokerRestUrl
$BrokerWsUrl = $endpoints.BrokerWsUrl
Write-Host "broker endpoints ($($endpoints.Source)): $BrokerRestUrl | $BrokerWsUrl"
$companionDir = Join-Path $InstallRoot "companion"
$companionExe = Join-Path $companionDir "PagentOS.SessionCompanion.exe"
$serviceExe = Join-Path $serviceDir "PagentOS.DeviceService.exe"

# --- recover from whatever a previous run left behind -------------------------------------
#
# This installer's own earlier version could leave a tree it could not then update: hardening
# with /T stripped every existing file to an empty DACL. Recovering from that is the
# installer's job, not the owner's, so it happens first and unconditionally.

$recovery = Invoke-InstallRecovery -Root $InstallRoot -Components @("service", "companion", "browser")
foreach ($line in @($recovery.Messages)) {
    Write-Host $line
}
# The engine's own journal (.deploy-journal.json, versioned .previous\<version>\<component>):
# a run interrupted after its swap but before health verification is put back first.
$resolution = Resolve-InterruptedDeployment -Root $InstallRoot -Components @("service", "companion")
Write-Host "previous deployment state: $($resolution.Action) - $($resolution.Reason)"
if ($resolution.Action -eq "Blocked") {
    throw "refusing to continue: $($resolution.Reason)"
}
if ($resolution.Action -eq "RollbackToPrevious") {
    $journal = Read-DeployJournal -Root $InstallRoot
    foreach ($component in @("service", "companion", "browser")) {
        $live = Join-Path $InstallRoot $component
        $previous = Join-Path (Join-Path (Join-Path $InstallRoot ".previous") $journal.version) $component
        if ((Test-Path -LiteralPath $previous) -and -not (Test-Path -LiteralPath $live)) {
            Move-Item -LiteralPath $previous -Destination $live -Force
            Write-Host "restored '$component' from the interrupted deployment $($journal.version)"
        }
    }
    Write-DeployPhase -Root $InstallRoot -Version $journal.version -Phase "rolled_back" -Detail "restored by the installer before a new attempt"
}

# --- stage everything before touching the live tree ---------------------------------------
#
# Publishing and configuration both write into staging. Nothing is written into the live,
# hardened directories at all — which is what makes a rerun safe regardless of the ACLs the
# last run left, and what makes an interrupted run cost nothing.

if ($SkipBuild) {
    foreach ($required in @($serviceExe, $companionExe)) {
        if (-not (Test-Path $required)) { throw "-SkipBuild was passed but $required does not exist" }
    }
    $stagedServiceDir = $serviceDir
    $stagedCompanionDir = $companionDir
}
else {
    $dotnet = Get-DotnetPath
    $stagedServiceDir = New-StagingDirectory -Root $InstallRoot -Name "service"
    $stagedCompanionDir = New-StagingDirectory -Root $InstallRoot -Name "companion"

    Publish-Agent -Dotnet $dotnet -Project (Join-Path $agentRoot "src\PagentOS.DeviceService\PagentOS.DeviceService.csproj") -Output $stagedServiceDir
    Publish-Agent -Dotnet $dotnet -Project (Join-Path $agentRoot "src\PagentOS.SessionCompanion\PagentOS.SessionCompanion.csproj") -Output $stagedCompanionDir

    foreach ($required in @(
        (Join-Path $stagedServiceDir "PagentOS.DeviceService.exe"),
        (Join-Path $stagedCompanionDir "PagentOS.SessionCompanion.exe"))) {
        if (-not (Test-Path $required)) { throw "expected $required after publish; nothing to install" }
    }
}

# Evidence, part 1: the bytes the build produced (before any configuration is written).
$sourceHashes = if ($SkipBuild) { Get-ArtifactHashes -Root $InstallRoot } else { Get-ArtifactHashes -Root (Join-Path $InstallRoot ".staging") }

# --- browser worker (M13), staged and PROVEN before anything is swapped -------------------
#
# The worker is provisioned through the same staging/publish path as the binaries. Its
# self-check must pass in staging: a machine without Chrome, or a package that cannot
# import, fails the install here with the previous tree intact — and the service is never
# told to advertise a family it cannot serve.

$browserDir = Join-Path $InstallRoot "browser"
$companionDataDir = Join-Path $env:ProgramData "PagentOS\companion"
$browserDataDir = Join-Path $companionDataDir "browser"
$browserStaging = $null
if ($SkipBrowser) {
    Write-Host "browser worker: skipped (-SkipBrowser); the service will advertise the desktop family only"
}
else {
    $browserStaging = Invoke-BrowserWorkerStaging -RepoRoot $repoRoot -InstallRoot $InstallRoot -Channel $BrowserChannel -ExplicitUv $UvPath
}

# Service state lives under ProgramData, not the owner's profile: a Session-0 service
# writing into a user profile resolves to the system profile and is a topology bug.
# Root-only ACEs with inheritance — the old raw icacls /T call here reintroduced the
# empty-DACL bug on the service's own log file and killed its pipe server mid-run.
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
Set-MachineDataAcl -Root $DataDir
# device.key deliberately carries explicit ACEs (SYSTEM read-only); the /reset above made it
# inherit full control, so re-apply the narrower explicit protection.
$keyPath = Join-Path $DataDir "device.key"
if (Test-Path -LiteralPath $keyPath) {
    [void](Repair-InstallTreeAcl -Root $DataDir -Quiet)
    Protect-DeviceKeyAcl -KeyPath $keyPath
}

# Configuration is written into STAGING, against the final paths the service will use. This
# is the ordering fix: the previous version wrote config into the live tree after it had
# been hardened, and an existing appsettings.json could not be replaced.
Write-ServiceConfig -ServiceDir $stagedServiceDir -CompanionExe $companionExe -OwnerSid $OwnerSid `
    -DataDir $DataDir -BrokerRestUrl $BrokerRestUrl -BrokerWsUrl $BrokerWsUrl -BrowserEnabled (-not $SkipBrowser) `
    -DisplayPowerEnabled ([bool]$DisplayPower) -OperatorEnabled ([bool]$Operator)

# The companion reads its own settings from its own directory.
$companionConfig = [ordered]@{
    PipeName = "pagentos-companion-$OwnerSid"
    DataDir  = $companionDataDir
}
# The SECOND half of the display-off gate. The service decides whether to route the name and
# whether to advertise it; the companion decides whether to execute it. Writing only one of
# these is how a device ends up advertising a capability it will then refuse.
if ($DisplayPower) {
    $companionConfig["DisplayPowerEnabled"] = $true
}
# M19: the operator family's second half, same rule.
if ($Operator) {
    $companionConfig["OperatorEnabled"] = $true
}
if (-not $SkipBrowser) {
    # Paths the companion will use at runtime: the LIVE browser tree (not staging) and a
    # data directory the OWNER can write, created below with an explicit grant.
    foreach ($entry in (New-CompanionBrowserSettings -BrowserRoot $browserDir -BrowserDataDir $browserDataDir -Channel $BrowserChannel).GetEnumerator()) {
        $companionConfig[$entry.Key] = $entry.Value
    }
}
Write-JsonFile -Path (Join-Path $stagedCompanionDir "appsettings.json") -Content ($companionConfig | ConvertTo-Json -Depth 4)

if ($DisplayPower) {
    Write-Host "display power: ENABLED on BOTH the service and the companion (desktop.display_off is advertised)"
    Write-Host "  it still refuses while input is recent (holdoff 120 s by default) or while an alarm is ringing"
}
else {
    Write-Host "display power: disabled (pass -DisplayPower to enable desktop.display_off); waking and reporting stay available"
}
if ($Operator) {
    Write-Host "digital operator: ENABLED on BOTH the service and the companion (the app/window/keyboard/pointer/ui/screen/file/terminal families are advertised; DEVICE_PROTOCOL.md §6i)"
}
else {
    Write-Host "digital operator: disabled (pass -Operator to enable the M19 families)"
}

if (-not $SkipBrowser) {
    # ProgramData's inherited ACL would make a directory created by this ELEVATED process
    # unwritable for the owner's non-elevated companion — and the worker must write its
    # profile, downloads and logs exactly there and nowhere else (Program Files is
    # read-only to it). The grant is explicit and inheritable, on the companion's data root
    # and the browser directory under it.
    Set-OwnerWritableDirectory -Path $companionDataDir -OwnerSid $OwnerSid
    Set-OwnerWritableDirectory -Path $browserDataDir -OwnerSid $OwnerSid
    Write-Host "browser data directory $browserDataDir is writable by SID $OwnerSid"
}

# --- register, then deploy through the journaled engine ----------------------------------
#
# Registration happens BEFORE the swap because the engine starts the runtime inside its
# transaction (service through the SCM, companion through its logon task in the owner's
# session) and judges health on the result. Both registrations are idempotent and point at
# the final live paths.
Install-Service -Name $ServiceName -Exe $serviceExe | Out-Null
Register-CompanionAutostart -CompanionExe $companionExe -OwnerSid $OwnerSid

$enrolled = Test-Path (Join-Path $DataDir "state.json")
$components = @()
if (-not $SkipBuild) { $components += @("service", "companion") }
if ($browserStaging) { $components += "browser" }

# --- the candidate manifest (M18.4 gap 3): described at staging, verified before the swap ----
#
# The staged service binary describes itself (its `capabilities` verb: version, capability
# manifest), every staged file is hashed, and the browser worker's release identity is
# attached. The manifest is re-verified file by file immediately before the engine moves
# anything: a candidate that changed, lost a file or names no version never becomes live.
# After the swap the same manifest is what Cloud Core must see (below, in the health check).
$candidateManifest = $null
$candidateVerdict = $null
if (@($components).Count -gt 0 -and -not $SkipBuild) {
    $stagedServiceManifest = Get-InstalledAgentManifest -ServiceExe (Join-Path $stagedServiceDir "PagentOS.DeviceService.exe")
    $candidateManifest = New-AgentCandidateManifest -StagingRoot (Join-Path $InstallRoot ".staging") -Components $components `
        -ServiceManifest $stagedServiceManifest -BrowserRelease $(if ($browserStaging) { $browserStaging.Release } else { $null }) `
        -RepoHead $(try { (& git -C $repoRoot rev-parse HEAD 2>$null | Select-Object -First 1) } catch { "" })
    $candidateManifestPath = Write-AgentCandidateManifest -Manifest $candidateManifest -Path (Get-CandidateManifestPath -StagingRoot (Join-Path $InstallRoot ".staging"))
    $requiredCapabilities = @("desktop.open_application")
    if (-not $SkipBrowser) { $requiredCapabilities += "browser.chrome" }
    $candidateVerdict = Test-AgentCandidateManifest -Manifest (Read-AgentCandidateManifest -Path $candidateManifestPath) -StagingRoot (Join-Path $InstallRoot ".staging") -RequireCapabilities $requiredCapabilities
    Write-CandidateSummary -Manifest $candidateManifest -Verdict $candidateVerdict
    if (-not $candidateVerdict.Ok) {
        throw "the staged candidate does not verify against its manifest; nothing was swapped: $($candidateVerdict.Reasons -join '; ')"
    }
}

# Cloud Core's view of the candidate needs an owner session and the device's id. Both are
# read BEFORE the swap (the runtime is still up; the identity verb is load-only). No stored
# credential means the read is skipped and said so - it is never faked.
$coreVerify = $false
$coreFetch = $null
$coreDeviceId = $null
if (-not $SkipCoreVerify -and $enrolled -and $null -ne $candidateManifest) {
    $coreDeviceId = Get-AgentDeviceId -ServiceExe $serviceExe
    $coreToken = Get-OwnerSessionToken -BaseUrl $BrokerRestUrl -Label "install-device-service"
    if (-not $coreDeviceId) {
        Write-Host "Cloud Core verification: SKIPPED (the installed service did not yield a device id)" -ForegroundColor Yellow
    }
    elseif (-not $coreToken) {
        Write-Host "Cloud Core verification: SKIPPED (no stored owner credential; bootstrap-owner-credential.ps1 stores it, then rerun)" -ForegroundColor Yellow
    }
    else {
        $coreFetch = New-CoreDeviceFetcher -BaseUrl $BrokerRestUrl -Token $coreToken
        $coreVerify = $true
        Write-Host "Cloud Core verification: ON (device $coreDeviceId must come back online as $($candidateManifest.software_version) with $(@($candidateManifest.capabilities).Count) capabilities within $CoreVerifyTimeoutSeconds s, else rollback)"
    }
    $coreToken = $null
}
elseif (-not $SkipCoreVerify -and -not $enrolled) {
    Write-Host "Cloud Core verification: not applicable (the device is not enrolled yet)"
}

# Evidence, part 2: the staged bytes right before the swap (configuration written, nothing moved).
$stagedHashes = if ($SkipBuild) { Get-ArtifactHashes -Root $InstallRoot } else { Get-ArtifactHashes -Root (Join-Path $InstallRoot ".staging") }

$deployStartedAt = Get-Date
$script:LiveWorkerProof = $null
$script:CoreHeartbeatProof = $null
# Every process running the worker module before the swap (the venv trampoline AND the base
# interpreter it launches); after the swap none of them may be alive.
$preWorkerPids = @(Select-BrowserWorkerProcess -Processes @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue) | ForEach-Object { [int]$_.ProcessId })
if (@($preWorkerPids).Count -gt 0) { Write-Host "browser worker processes before the swap: $($preWorkerPids -join ', ')" }
$stopRuntime = { Stop-AgentRuntime -ServiceName $ServiceName -InstallRoot $InstallRoot }
$startRuntime = {
    # An unenrolled agent exits immediately by design; starting it would turn a correct
    # first install into a health failure and a rollback.
    if ($enrolled) { Start-AgentRuntime -ServiceName $ServiceName -InstallRoot $InstallRoot }
}
$testHealth = {
    # Health = the INSTALLED binary supports M13 (capabilities verb, browser family when
    # provisioned) AND, when enrolled, service + companion + pipe are up and both processes
    # run from the new trees. A pre-M13 binary here means the swap did not take: roll back.
    $manifest = Get-InstalledAgentManifest -ServiceExe $serviceExe
    try {
        Assert-InstalledAgentSupportsM13 -Manifest $manifest -ExpectBrowser (-not $SkipBrowser)
    }
    catch {
        Write-Warning "health: $($_.Exception.Message)"
        return $false
    }
    if (-not $enrolled) { return $true }
    if (-not (Test-AgentRuntimeHealth -ServiceName $ServiceName -ConfigPath (Join-Path $serviceDir "appsettings.json") -TimeoutSeconds 45)) {
        Write-Warning "health: service, companion or the pipe did not come up within 45 s"
        return $false
    }
    $images = Get-RunningAgentImages -ServiceName $ServiceName
    if (($images.Service -ne $serviceExe) -or ($images.Companion -ne $companionExe)) {
        Write-Warning "health: running images are not the installed binaries (service: $($images.Service); companion: $($images.Companion))"
        return $false
    }
    if ($browserStaging) {
        # Deployment truthfulness (2026-09-04): the process the companion actually started
        # after the swap must report the staged release from the installed venv, its pid
        # must be newer than this deployment, and every pre-swap worker pid must be gone.
        # Anything else rolls the deployment back.
        $auditPath = Join-Path $companionDataDir "audit\companion-audit.jsonl"
        $liveAudit = Wait-LiveBrowserWorkerAudit -AuditPath $auditPath -Since $deployStartedAt -TimeoutSeconds 90
        if ($null -eq $liveAudit) {
            Write-Warning "health: the companion recorded no browser_worker_started after $($deployStartedAt.ToString('o')) within 90 s ($auditPath)"
            return $false
        }
        $liveProblems = @(Test-LiveBrowserWorker -Audit $liveAudit -Expected $browserStaging.Release -BrowserRoot $browserDir `
            -BrowserDataDir $browserDataDir -DeployStartedAt $deployStartedAt -PreviousPids $preWorkerPids)
        if (@($liveProblems).Count -gt 0) {
            Write-Warning "health: the live browser worker is not the staged release: $($liveProblems -join '; ')"
            return $false
        }
        $script:LiveWorkerProof = $liveAudit
        Write-Host "live browser worker proven: pid $($liveAudit.Pid), worker $($liveAudit.WorkerVersion), module $($liveAudit.Module), started $($liveAudit.Ts.ToString('o'))"
    }
    if ($coreVerify) {
        # M18.4 gap 3: the candidate is live only when Cloud Core sees it - online, with the
        # version and the capabilities the manifest promised. Otherwise the engine rolls back.
        $heartbeat = Test-AgentHeartbeatOnCore -FetchDevices $coreFetch -DeviceId $coreDeviceId `
            -ExpectedVersion ([string]$candidateManifest.software_version) -ExpectedCapabilities @($candidateManifest.capabilities) `
            -TimeoutSeconds $CoreVerifyTimeoutSeconds
        if (-not $heartbeat.Ok) {
            Write-Warning "health: Cloud Core does not see the candidate after $($heartbeat.Waited) s: $($heartbeat.Reasons -join '; ')"
            return $false
        }
        $script:CoreHeartbeatProof = $heartbeat
        Write-Host "Cloud Core sees the candidate: online, version $($heartbeat.Observed.software_version), $($heartbeat.Observed.capability_count) capabilities, last seen $($heartbeat.Observed.last_seen_at) (after $($heartbeat.Waited) s)"
    }
    return $true
}
$applyAcl = {
    Set-InstallAcl -Path $InstallRoot
    Assert-InstallPosture -Path $InstallRoot
}

if (@($components).Count -gt 0) {
    Write-Host "deploying $($components -join ', ') through the journaled engine (runtime stopped by PID, same-volume renames, rollback on any failure)"
    [void](Invoke-AgentDeployment -Root $InstallRoot -Components $components -NonExecutableComponents @("browser") `
        -StopRuntime $stopRuntime -StartRuntime $startRuntime -TestHealth $testHealth -ApplyAcl $applyAcl)
    Write-Host "deployed $($components -join ', '); journal: $(Get-DeployJournalPath -Root $InstallRoot)"
}
else {
    Set-InstallAcl -Path $InstallRoot
    Assert-InstallPosture -Path $InstallRoot
}

if ($browserStaging) {
    # The venv was built in staging and moved: prove it still starts from its final,
    # hardened location. (The verifier repeats this later as the OWNER, unelevated.)
    $probeData = Join-Path $env:TEMP "pagentos-browser-postinstall-$([guid]::NewGuid().ToString('N'))"
    try {
        $live = Invoke-BrowserWorkerSelfCheck -Python (Get-BrowserWorkerPython -BrowserRoot $browserDir) -Channel $BrowserChannel `
            -DataDir $probeData
    }
    finally {
        Remove-Item -LiteralPath $probeData -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (-not $live.Ok) {
        throw "the Browser Worker passed its self-check in staging but not from $browserDir (exit $($live.ExitCode)). Rerun the installer; if it repeats, report stderr:`n$($live.StdErr)"
    }
    $installedRelease = Get-ExpectedWorkerRelease -BrowserSource $browserDir
    if ($installedRelease.PackageSha256 -ne $browserStaging.Release.PackageSha256) {
        throw "the installed browser source tree ($($installedRelease.PackageSha256.Substring(0,12))) is not the staged one ($($browserStaging.Release.PackageSha256.Substring(0,12)))"
    }
    $installedProof = Assert-WorkerHelloMatchesRelease -Hello $live.Hello -Expected $installedRelease -BrowserRoot $browserDir -Label "installed worker"
    $installedDifferences = @(Compare-BrowserPackageCopies -Source (Join-Path $browserDir "browser_agent") -Installed (Get-SitePackagesBrowserAgentDir -BrowserRoot $browserDir))
    if (@($installedDifferences).Count -gt 0) {
        throw "the installed venv's package differs from the installed source: $($installedDifferences -join '; ')"
    }
    Write-Host "browser worker starts from the installed tree: $($live.Hello.worker_version), $(@($live.Capabilities).Count) capabilities, module $($installedProof.ModuleFile)"
}

if ($EnrollmentToken) {
    Write-Host "enrolling the device with the broker at $BrokerRestUrl"
    $enrollResult = Invoke-NativeProcess -FilePath $serviceExe -Arguments @(
        "enroll", "--broker-url", $BrokerRestUrl, "--token", $EnrollmentToken, "--name", $env:COMPUTERNAME
    ) -TimeoutSeconds 120
    Assert-NativeSuccess -Result $enrollResult -Activity "device enrollment"
    if ($enrollResult.StdOut) { Write-Host $enrollResult.StdOut.Trim() }
    $enrolled = $true
}

# Starting is conditional on enrollment, and that is not a caveat — an unenrolled agent
# exits immediately by design, so starting it here would turn "the install worked" into a
# service-start failure and hide the fact that registration succeeded. (When the engine ran
# for an enrolled device it already started and health-checked the runtime.)
if ($enrolled) {
    if ((Get-Service -Name $ServiceName).Status -ne "Running" -or -not (Get-Process -Name "PagentOS.SessionCompanion" -ErrorAction SilentlyContinue)) {
        Start-AgentRuntime -ServiceName $ServiceName -InstallRoot $InstallRoot
        Write-Host "service and companion started"
    }
}
else {
    Write-Host ""
    Write-Warning "The service is REGISTERED but not started, because this device is not enrolled yet."
    Write-Warning "This is expected on a first install and is not a registration failure."
    Write-Warning "Mint a token from an authenticated owner session (POST /v1/devices/enrollment-tokens), then:"
    Write-Warning "  & '$serviceExe' enroll --broker-url $BrokerRestUrl --token <one-time-token> --name $env:COMPUTERNAME"
    Write-Warning "  Start-Service -Name $ServiceName"
    Write-Warning "Or rerun this installer with -EnrollmentToken <token>; rerunning is safe."
}

Write-Host ""
Write-Host "installed:"
Write-Host "  service   $serviceExe (LocalSystem, automatic start)"
Write-Host "  companion $companionExe (logon task, owner session)"
Write-Host "  data      $DataDir"
Write-Host "  admits    SID $OwnerSid running exactly that companion binary, in an interactive session"
if ($browserStaging) {
    Write-Host "  browser   $browserDir (worker $($browserStaging.Hello.worker_version), channel $BrowserChannel; data $browserDataDir)"
    Write-Host "            service advertises browser.chrome + $(@($browserStaging.Capabilities).Count) browser.* operations"
}
else {
    Write-Host "  browser   not provisioned (-SkipBrowser); desktop family only"
}
Write-Host ""
Write-Host "next: verify with"
Write-Host "  .\scripts\verify-device-service.ps1"

# --- evidence, part 3: what is actually installed, registered and running ----------------
$installedHashes = Get-ArtifactHashes -Root $InstallRoot
$hashMismatch = @(Compare-ArtifactHashes -Expected $stagedHashes -Actual $installedHashes)
$manifest = Get-InstalledAgentManifest -ServiceExe $serviceExe
$registered = Get-RegisteredExecutablePaths -ServiceName $ServiceName
$running = Get-RunningAgentImages -ServiceName $ServiceName
$workerPath = "not provisioned (-SkipBrowser)"
if ($browserStaging) {
    $liveCompanionConfig = [System.IO.File]::ReadAllText((Join-Path $companionDir "appsettings.json")) | ConvertFrom-Json
    $workerPath = if ($liveCompanionConfig.PSObject.Properties.Name -contains "BrowserWorkerCommand") { [string]$liveCompanionConfig.BrowserWorkerCommand } else { "MISSING from companion appsettings" }
}
$browserReleaseEvidence = @()
if ($browserStaging) {
    $expectedRel = $browserStaging.Release
    $installedSite = Get-SitePackagesBrowserAgentDir -BrowserRoot $browserDir
    $contractText = ($expectedRel.Contracts.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ','
    $browserReleaseEvidence += "expected (staged source)      worker $($expectedRel.Version) contracts $contractText worker.py $($expectedRel.WorkerSha256) package $($expectedRel.PackageSha256)"
    $browserReleaseEvidence += "installed source tree         package $((Get-BrowserPackageDigest -PackageDir (Join-Path $browserDir 'browser_agent')))"
    $browserReleaseEvidence += "installed venv site-packages  package $(if (Test-Path -LiteralPath $installedSite) { Get-BrowserPackageDigest -PackageDir $installedSite } else { 'MISSING' })"
    if ($script:LiveWorkerProof) {
        $browserReleaseEvidence += "live worker                   pid $($script:LiveWorkerProof.Pid) worker $($script:LiveWorkerProof.WorkerVersion) module $($script:LiveWorkerProof.Module) started $($script:LiveWorkerProof.Ts.ToString('o'))"
    }
    else {
        $browserReleaseEvidence += "live worker                   not started (device not enrolled yet; the verifier proves it once enrolled)"
    }
    $browserReleaseEvidence += "pre-swap worker pids          $(if (@($preWorkerPids).Count -gt 0) { ($preWorkerPids -join ', ') + ' (all gone)' } else { 'none' })"
}
$journalDoc = Read-DeployJournal -Root $InstallRoot
$journalSummary = if ($journalDoc) { "$($journalDoc.version) phase=$($journalDoc.phase)" } else { "none" }
$capabilitySummary = if ($manifest.Ok) {
    "$(@($manifest.Capabilities).Count) capabilities, browser_enabled=$($manifest.BrowserEnabled), family=$(@($manifest.Capabilities) -contains 'browser.chrome')"
} else { "capabilities verb NOT answered (exit $($manifest.ExitCode))" }
Write-InstallEvidence -Evidence ([pscustomobject]@{
    RepoHead            = Get-RepoHead -RepoRoot $repoRoot
    SourceHashes        = $sourceHashes
    StagedHashes        = $stagedHashes
    InstalledHashes     = $installedHashes
    ServiceExe          = $serviceExe
    RegisteredService   = $registered.Service
    RunningService      = $running.Service
    CompanionExe        = $companionExe
    RegisteredCompanion = $registered.Companion
    RunningCompanion    = $running.Companion
    BrowserWorker       = $workerPath
    BrowserRelease      = @($browserReleaseEvidence)
    CapabilitySummary   = $capabilitySummary
    Journal             = $journalSummary
    LogPath             = $script:InstallLog
})
if (@($hashMismatch).Count -gt 0) {
    throw "installed artifacts differ from what was staged: $($hashMismatch -join '; ')"
}
if ($registered.Service -ne $serviceExe) { throw "the SCM runs '$($registered.Service)', not the installed '$serviceExe'" }
if ($registered.Companion -ne $companionExe) { throw "the logon task runs '$($registered.Companion)', not the installed '$companionExe'" }
Assert-InstalledAgentSupportsM13 -Manifest $manifest -ExpectBrowser (-not $SkipBrowser)
if ($browserStaging -and ($workerPath -like "MISSING*" -or -not (Test-Path -LiteralPath $workerPath))) {
    throw "the companion's BrowserWorkerCommand is missing or does not exist: $workerPath"
}
if ($enrolled) {
    if ($running.Service -ne $serviceExe) { throw "the service process is not running the installed binary: $($running.Service)" }
    if ($running.Companion -ne $companionExe) { throw "the companion process is not running the installed binary: $($running.Companion)" }
}
Write-Host ""
if ($browserStaging -and $enrolled -and -not $script:LiveWorkerProof) {
    throw "the live browser worker was never proven after the swap (no health proof recorded); refusing to report success"
}
$verifiedLine = "INSTALL VERIFIED: the live tree is the staged build, the service and the companion point at it$(if ($enrolled) { ' and run from it' }), and the installed service supports M13"
if ($browserStaging) {
    $verifiedLine += if ($script:LiveWorkerProof) { "; the live browser worker (pid $($script:LiveWorkerProof.Pid)) runs release $($script:LiveWorkerProof.WorkerVersion) from the installed venv." } else { "; the browser worker release is proven from the installed venv (live worker follows enrollment)." }
}
else { $verifiedLine += "." }
Write-Host $verifiedLine -ForegroundColor Green
try { Stop-Transcript | Out-Null } catch { }
