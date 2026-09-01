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
    [switch]$SkipBuild
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
    Write-Host "restricting write access on $Path to administrators"
    $icacls = Get-SystemTool -Name "icacls.exe"
    $result = Invoke-NativeProcess -FilePath $icacls -Arguments @(
        $Path,
        "/inheritance:r",
        "/grant:r",
        "*S-1-5-32-544:(OI)(CI)F",
        "*S-1-5-18:(OI)(CI)F",
        "*S-1-5-32-545:(OI)(CI)RX",
        "/T",
        "/Q"
    ) -TimeoutSeconds 300
    Assert-NativeSuccess -Result $result -Activity "icacls $Path"
}

function Write-ServiceConfig {
    param(
        [string]$ServiceDir,
        [string]$CompanionExe,
        [string]$OwnerSid,
        [string]$DataDir,
        [string]$BrokerRestUrl,
        [string]$BrokerWsUrl
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
    }
    $path = Join-Path $ServiceDir "appsettings.json"
    # Written through .NET so the file is UTF-8 without a BOM: PowerShell 5.1's Set-Content
    # adds one and re-encodes non-ASCII, which has already corrupted files in this repo once.
    [System.IO.File]::WriteAllText(
        $path,
        ($config | ConvertTo-Json -Depth 4),
        (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "wrote $path (no secrets: URLs, paths and the owner SID only)"
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
if (-not $BrokerWsUrl) {
    $BrokerWsUrl = ($BrokerRestUrl -replace '^http', 'ws').TrimEnd('/') + "/v1/devices/connect"
}

$serviceDir = Join-Path $InstallRoot "service"
$companionDir = Join-Path $InstallRoot "companion"
$companionExe = Join-Path $companionDir "PagentOS.SessionCompanion.exe"
$serviceExe = Join-Path $serviceDir "PagentOS.DeviceService.exe"

if (-not $SkipBuild) {
    $dotnet = Get-DotnetPath
    Publish-Agent -Dotnet $dotnet -Project (Join-Path $agentRoot "src\PagentOS.DeviceService\PagentOS.DeviceService.csproj") -Output $serviceDir
    Publish-Agent -Dotnet $dotnet -Project (Join-Path $agentRoot "src\PagentOS.SessionCompanion\PagentOS.SessionCompanion.csproj") -Output $companionDir
}

foreach ($required in @($serviceExe, $companionExe)) {
    if (-not (Test-Path $required)) { throw "expected $required after publish; nothing to install" }
}

# Service state lives under ProgramData, not the owner's profile: a Session-0 service
# writing into a user profile resolves to the system profile and is a topology bug.
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
Assert-NativeSuccess -Activity "icacls $DataDir" -Result (Invoke-NativeProcess `
    -FilePath (Get-SystemTool -Name "icacls.exe") `
    -Arguments @($DataDir, "/inheritance:r", "/grant:r", "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F", "/T", "/Q") `
    -TimeoutSeconds 300)

Write-ServiceConfig -ServiceDir $serviceDir -CompanionExe $companionExe -OwnerSid $OwnerSid `
    -DataDir $DataDir -BrokerRestUrl $BrokerRestUrl -BrokerWsUrl $BrokerWsUrl

# The companion reads its own settings from its own directory.
$companionConfig = [ordered]@{
    PipeName = "pagentos-companion-$OwnerSid"
    DataDir  = (Join-Path $env:ProgramData "PagentOS\companion")
} | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText(
    (Join-Path $companionDir "appsettings.json"),
    $companionConfig,
    (New-Object System.Text.UTF8Encoding($false)))

Set-InstallAcl -Path $InstallRoot

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
Write-Host ""
Write-Host "next: sign out and back in (or start the companion by hand once), then verify with"
Write-Host "  .\scripts\verify-device-service.ps1"
