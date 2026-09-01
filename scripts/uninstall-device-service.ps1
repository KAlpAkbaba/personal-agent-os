<#
.SYNOPSIS
    Removes the Device Service, the companion's logon task and (optionally) the installed
    files. Leaves the agent's data directory alone unless asked.

.DESCRIPTION
    Deliberately does NOT delete the data directory by default: it holds the device's
    private key, its enrollment state and its audit log. Deleting the key silently would
    turn "uninstall the service" into "lose the device identity and every record of what it
    did", which is not what the words mean. Pass -RemoveData to say it explicitly.

.EXAMPLE
    .\scripts\uninstall-device-service.ps1
#>
[CmdletBinding()]
param(
    [string]$ServiceName = "PagentOSDeviceAgent",
    [string]$InstallRoot = (Join-Path $env:ProgramFiles "PagentOS\agent"),
    [string]$DataDir = (Join-Path $env:ProgramData "PagentOS\agent"),
    [switch]$RemoveData
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

. (Join-Path $PSScriptRoot "lib\NativeProcess.ps1")

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Removing a Windows Service requires elevation. Run this from an administrator PowerShell."
}

$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($service) {
    if ($service.Status -ne "Stopped") {
        Write-Host "stopping $ServiceName"
        Stop-Service -Name $ServiceName -Force
        $service.WaitForStatus("Stopped", (New-TimeSpan -Seconds 30))
    }
    # 1072 is "already marked for delete": the service is on its way out, which is the
    # outcome asked for, so it is not a failure.
    $deleted = Invoke-NativeProcess -FilePath (Get-SystemTool -Name "sc.exe") `
        -Arguments @("delete", $ServiceName) -SuccessExitCodes @(0, 1072)
    Assert-NativeSuccess -Result $deleted -Activity "sc delete $ServiceName"
    Write-Host "deleted service $ServiceName"
}
else {
    Write-Host "no service named $ServiceName"
}

$task = Get-ScheduledTask -TaskName "PagentOS Session Companion" -ErrorAction SilentlyContinue
if ($task) {
    Get-Process -Name "PagentOS.SessionCompanion" -ErrorAction SilentlyContinue | Stop-Process -Force
    Unregister-ScheduledTask -TaskName "PagentOS Session Companion" -Confirm:$false
    Write-Host "removed the companion logon task"
}

if (Test-Path $InstallRoot) {
    Remove-Item -Path $InstallRoot -Recurse -Force
    Write-Host "removed $InstallRoot"
}

if ($RemoveData) {
    if (Test-Path $DataDir) {
        Remove-Item -Path $DataDir -Recurse -Force
        Write-Host "removed $DataDir (device key, enrollment state and audit log are gone)"
    }
}
elseif (Test-Path $DataDir) {
    Write-Host "kept $DataDir - it holds the device key, enrollment state and audit log."
    Write-Host "Pass -RemoveData if you really want those gone; revoke the device in the cloud first."
}
