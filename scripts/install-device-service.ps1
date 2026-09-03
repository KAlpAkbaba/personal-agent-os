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

.PARAMETER UvPath
    Explicit path to uv.exe. By default uv is resolved the way scripts\preflight.ps1
    resolves it (PATH, then the known install locations) — never assumed.

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
    [string]$UvPath
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
        [bool]$BrowserEnabled
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
        Write-Host "creating the worker environment: uv sync --frozen --no-dev --no-editable (python under $($env:UV_PYTHON_INSTALL_DIR))"
        $sync = Invoke-NativeProcess -FilePath $uv -Arguments @("sync", "--frozen", "--no-dev", "--no-editable") `
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

    # The self-check launches the real channel headless, prints the hello and exits. It
    # runs against a throwaway data directory so nothing owned by this ELEVATED process
    # lands where the owner's companion must later write.
    $probeData = Join-Path $env:TEMP "pagentos-browser-selfcheck-$([guid]::NewGuid().ToString('N'))"
    try {
        Write-Host "self-check: python -m browser_agent.worker --self-check --channel $Channel"
        $check = Invoke-BrowserWorkerSelfCheck -Python $python -Channel $Channel -DataDir $probeData -WorkingDirectory $staged
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

    return [pscustomobject]@{
        StagedPath   = $staged
        Hello        = $check.Hello
        Capabilities = @($check.Capabilities)
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
$OwnerSid = Resolve-OwnerSid -Explicit $OwnerSid
$serviceDir = Join-Path $InstallRoot "service"

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
    -DataDir $DataDir -BrokerRestUrl $BrokerRestUrl -BrokerWsUrl $BrokerWsUrl -BrowserEnabled (-not $SkipBrowser)

# The companion reads its own settings from its own directory.
$companionConfig = [ordered]@{
    PipeName = "pagentos-companion-$OwnerSid"
    DataDir  = $companionDataDir
}
if (-not $SkipBrowser) {
    # Paths the companion will use at runtime: the LIVE browser tree (not staging) and a
    # data directory the OWNER can write, created below with an explicit grant.
    foreach ($entry in (New-CompanionBrowserSettings -BrowserRoot $browserDir -BrowserDataDir $browserDataDir -Channel $BrowserChannel).GetEnumerator()) {
        $companionConfig[$entry.Key] = $entry.Value
    }
}
Write-JsonFile -Path (Join-Path $stagedCompanionDir "appsettings.json") -Content ($companionConfig | ConvertTo-Json -Depth 4)

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

# --- swap staging into place, atomically ---------------------------------------------------
$deployed = @()
if (-not $SkipBuild) {
    Publish-StagedDirectory -Root $InstallRoot -Component "service" -StagedPath $stagedServiceDir | Out-Null
    Publish-StagedDirectory -Root $InstallRoot -Component "companion" -StagedPath $stagedCompanionDir | Out-Null
    $deployed += @("service", "companion")
}
if ($browserStaging) {
    Publish-StagedDirectory -Root $InstallRoot -Component "browser" -StagedPath $browserStaging.StagedPath | Out-Null
    $deployed += "browser"
}
if (@($deployed).Count -gt 0) {
    Remove-Item -LiteralPath (Join-Path $InstallRoot ".staging") -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "deployed $($deployed -join ', ')"
}

# --- harden last, then prove it ------------------------------------------------------------
Set-InstallAcl -Path $InstallRoot
Assert-InstallPosture -Path $InstallRoot

if ($browserStaging) {
    # The venv was built in staging and moved: prove it still starts from its final,
    # hardened location. (The verifier repeats this later as the OWNER, unelevated.)
    $probeData = Join-Path $env:TEMP "pagentos-browser-postinstall-$([guid]::NewGuid().ToString('N'))"
    try {
        $live = Invoke-BrowserWorkerSelfCheck -Python (Get-BrowserWorkerPython -BrowserRoot $browserDir) -Channel $BrowserChannel `
            -DataDir $probeData -WorkingDirectory $browserDir
    }
    finally {
        Remove-Item -LiteralPath $probeData -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (-not $live.Ok) {
        throw "the Browser Worker passed its self-check in staging but not from $browserDir (exit $($live.ExitCode)). Rerun the installer; if it repeats, report stderr:`n$($live.StdErr)"
    }
    Write-Host "browser worker starts from the installed tree: $($live.Hello.worker_version), $(@($live.Capabilities).Count) capabilities"
}

$enrolled = Test-Path (Join-Path $DataDir "state.json")
if ($EnrollmentToken) {
    Write-Host "enrolling the device with the broker at $BrokerRestUrl"
    $enrollResult = Invoke-NativeProcess -FilePath $serviceExe -Arguments @(
        "enroll", "--broker-url", $BrokerRestUrl, "--token", $EnrollmentToken, "--name", $env:COMPUTERNAME
    ) -TimeoutSeconds 120
    Assert-NativeSuccess -Result $enrollResult -Activity "device enrollment"
    if ($enrollResult.StdOut) { Write-Host $enrollResult.StdOut.Trim() }
    $enrolled = $true
}

Install-Service -Name $ServiceName -Exe $serviceExe | Out-Null
Register-CompanionAutostart -CompanionExe $companionExe -OwnerSid $OwnerSid

# Starting is conditional on enrollment, and that is not a caveat — an unenrolled agent
# exits immediately by design, so starting it here would turn "the install worked" into a
# service-start failure and hide the fact that registration succeeded.
if ($enrolled) {
    Start-Service -Name $ServiceName
    (Get-Service -Name $ServiceName).WaitForStatus("Running", (New-TimeSpan -Seconds 30))
    Write-Host "service started"
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
Write-Host "next: sign out and back in (or start the companion by hand once), then verify with"
Write-Host "  .\scripts\verify-device-service.ps1"
