<#
.SYNOPSIS
    Repair the NTFS protection on the installed agent's machine material, in place, and start
    the service.

.DESCRIPTION
    Proven root cause of the service failing to start: `device.key` was created by an
    owner-context enrollment run and protected with a DACL naming only that user, so
    LocalSystem could not read it. The service died in `DeviceIdentity.LoadOrCreate` with
    `UnauthorizedAccessException`, which the SCM reported as 1067.

    This script fixes the descriptors and touches nothing else:

      * the device identity and enrollment state are PRESERVED. A wrong ACL is never a reason
        to reissue a device identity, and this script has no path that deletes or regenerates
        either;
      * the SCM recovery policy is suspended first, so the crash loop stops while the repair
        runs, and restored afterwards. Recovery is not removed;
      * the two security domains stay apart. Machine material gets SYSTEM plus Administrators
        and nothing else — no Users, no Authenticated Users, no Everyone. Owner-session
        material (the DPAPI secret store under the owner's profile) is not touched at all;
      * every change is verified by re-reading the descriptor afterwards.

    Requires elevation, because it rewrites descriptors on ProgramData and controls a service.

.EXAMPLE
    # From an ELEVATED PowerShell, at the repository root:
    .\scripts\repair-device-material.ps1
#>
[CmdletBinding()]
param(
    [string]$ServiceName = "PagentOSDeviceAgent",
    [string]$DataDir = (Join-Path $env:ProgramData "PagentOS\agent"),
    [switch]$SkipStart
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "lib\NativeProcess.ps1")

$SidSystem = "S-1-5-18"
$SidAdministrators = "S-1-5-32-544"

function Assert-Elevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Repairing machine material and controlling a service both require elevation. Run this from an administrator PowerShell."
    }
}

function Get-Descriptor {
    param([string]$Path)
    $item = Get-Item -LiteralPath $Path -Force
    $sections = [System.Security.AccessControl.AccessControlSections]::Access -bor [System.Security.AccessControl.AccessControlSections]::Owner
    if ($item.PSIsContainer) {
        return ([System.IO.DirectoryInfo]$item.FullName).GetAccessControl($sections)
    }
    return ([System.IO.FileInfo]$item.FullName).GetAccessControl($sections)
}

function Set-Descriptor {
    param([string]$Path, $Security)
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) {
        ([System.IO.DirectoryInfo]$item.FullName).SetAccessControl($Security)
    }
    else {
        ([System.IO.FileInfo]$item.FullName).SetAccessControl($Security)
    }
}

function Show-Descriptor {
    param([string]$Label, [string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Host ("  {0,-22} MISSING  {1}" -f $Label, $Path)
        return
    }
    try {
        $acl = Get-Descriptor -Path $Path
        $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
        $who = ($rules | ForEach-Object { "$($_.IdentityReference.Value)=$($_.FileSystemRights)" }) -join ", "
        Write-Host ("  {0,-22} owner={1} protected={2}" -f $Label, $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value, $acl.AreAccessRulesProtected)
        Write-Host ("  {0,-22} aces: {1}" -f "", $(if ($who) { $who } else { "<none - denies everyone>" }))
    }
    catch {
        Write-Host ("  {0,-22} UNREADABLE from this account: {1}" -f $Label, $_.Exception.Message)
    }
}

function Repair-Path {
    <#
    .SYNOPSIS
        Apply the intended machine-material protection to one path.

    .PARAMETER Kind
        Secret  = SYSTEM read only (the device private key; the service never rewrites it)
        State   = SYSTEM full control (state the service updates)
        Directory = SYSTEM full control, inheritable
    #>
    param(
        [string]$Path,
        [ValidateSet("Secret", "State", "Directory")][string]$Kind
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }

    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes.HasFlag([System.IO.FileAttributes]::ReadOnly)) {
        $item.Attributes = $item.Attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)
    }

    $isDirectory = $item.PSIsContainer
    $systemRights = if ($Kind -eq "Secret") {
        [System.Security.AccessControl.FileSystemRights]::Read
    }
    else {
        [System.Security.AccessControl.FileSystemRights]::FullControl
    }

    $security = if ($isDirectory) {
        New-Object System.Security.AccessControl.DirectorySecurity
    }
    else {
        New-Object System.Security.AccessControl.FileSecurity
    }
    $security.SetAccessRuleProtection($true, $false)

    $inherit = if ($isDirectory) {
        [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    }
    else {
        [System.Security.AccessControl.InheritanceFlags]::None
    }

    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        (New-Object System.Security.Principal.SecurityIdentifier($SidSystem)),
        $systemRights, $inherit, [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow)))
    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        (New-Object System.Security.Principal.SecurityIdentifier($SidAdministrators)),
        [System.Security.AccessControl.FileSystemRights]::FullControl, $inherit,
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow)))

    Set-Descriptor -Path $Path -Security $security
    return $true
}

function Test-Repaired {
    param([string]$Path, [string]$Kind)
    $acl = Get-Descriptor -Path $Path
    $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))

    $sids = @($rules | ForEach-Object { $_.IdentityReference.Value })
    $unexpected = @($sids | Where-Object { $_ -ne $SidSystem -and $_ -ne $SidAdministrators })
    if (@($unexpected).Count -gt 0) {
        return "unexpected principals: $($unexpected -join ', ')"
    }

    $systemRules = @($rules | Where-Object { $_.IdentityReference.Value -eq $SidSystem })
    if (@($systemRules).Count -eq 0) {
        return "SYSTEM has no ACE"
    }
    if ($Kind -eq "Secret") {
        if (-not ($systemRules | Where-Object { $_.FileSystemRights.HasFlag([System.Security.AccessControl.FileSystemRights]::Read) })) {
            return "SYSTEM cannot read"
        }
    }
    elseif (-not ($systemRules | Where-Object { $_.FileSystemRights.HasFlag([System.Security.AccessControl.FileSystemRights]::FullControl) })) {
        return "SYSTEM lacks full control"
    }

    return $null
}

function Get-FailureActionsSnapshot {
    <#
    .SYNOPSIS
        The service's recovery policy as the exact registry bytes, or $null when unset.

    .DESCRIPTION
        The SCM stores the policy in the FailureActions REG_BINARY value. Capturing bytes
        rather than parsing `sc qfailure` matters twice over: the parse would be of localized
        text (this machine reports in Turkish), and "restore the original" must mean the
        original — byte-for-byte — not this script's opinion of what the policy should be.
    #>
    param([string]$Name)
    $key = "HKLM:\SYSTEM\CurrentControlSet\Services\$Name"
    $item = Get-ItemProperty -Path $key -ErrorAction SilentlyContinue
    $snapshot = @{ Actions = $null; NonCrash = $null }
    if ($item) {
        if ($item.PSObject.Properties.Name -contains "FailureActions") {
            $snapshot.Actions = [byte[]]$item.FailureActions
        }
        # A sibling value, verified present on the real machine as FALSE. `sc failure` does
        # not touch it, but "restore the exact original" should not depend on that staying
        # true of future Windows versions.
        if ($item.PSObject.Properties.Name -contains "FailureActionsOnNonCrashFailures") {
            $snapshot.NonCrash = [int]$item.FailureActionsOnNonCrashFailures
        }
    }
    return $snapshot
}

function Restore-FailureActionsSnapshot {
    param([string]$Name, [hashtable]$Snapshot)
    $key = "HKLM:\SYSTEM\CurrentControlSet\Services\$Name"
    if ($null -ne $Snapshot.Actions) {
        Set-ItemProperty -Path $key -Name "FailureActions" -Value $Snapshot.Actions -Type Binary
    }
    else {
        Remove-ItemProperty -Path $key -Name "FailureActions" -ErrorAction SilentlyContinue
    }
    if ($null -ne $Snapshot.NonCrash) {
        Set-ItemProperty -Path $key -Name "FailureActionsOnNonCrashFailures" -Value $Snapshot.NonCrash -Type DWord
    }
}

Assert-Elevated
$sc = Get-SystemTool -Name "sc.exe"

if (-not (Test-Path -LiteralPath $DataDir)) {
    throw "no agent data directory at $DataDir; there is nothing to repair"
}

Write-Host "=== before ===" -ForegroundColor Cyan
Show-Descriptor -Label "data directory" -Path $DataDir
Show-Descriptor -Label "device.key" -Path (Join-Path $DataDir "device.key")
Show-Descriptor -Label "state.json" -Path (Join-Path $DataDir "state.json")

# --- stop the crash loop while we work ------------------------------------------------------
# The SCM restart policy is what turns one failure into a loop. It is suspended, not removed,
# and restored below whatever happens.

$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
$policySnapshot = $null
$recoverySuspended = $false
try {
    if ($service) {
        Write-Host ""
        # SNAPSHOT FIRST, announce second, act third. An earlier version announced the
        # suspension and then crashed inside the sc invocation — the policy was untouched,
        # but the transcript claimed otherwise and the real state had to be inspected by hand
        # to know. The snapshot is the exact registry bytes, restored verbatim in `finally`
        # whatever happens below, including a crash between here and there.
        $policySnapshot = Get-FailureActionsSnapshot -Name $ServiceName
        $actionBytes = if ($null -ne $policySnapshot.Actions) { "$(@($policySnapshot.Actions).Count) bytes" } else { "not set" }
        Write-Host "captured the recovery policy (FailureActions: $actionBytes; NonCrashFlag: $(if ($null -ne $policySnapshot.NonCrash) { $policySnapshot.NonCrash } else { 'not set' }))"

        Write-Host "suspending the service recovery policy and stopping $ServiceName"
        $suspend = Invoke-NativeProcess -FilePath $sc `
            -Arguments @("failure", $ServiceName, "reset=", "0", "actions=", "")
        Assert-NativeSuccess -Result $suspend -Activity "sc failure (suspend recovery)"
        $recoverySuspended = $true

        if ($service.Status -ne "Stopped") {
            Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
            try { (Get-Service -Name $ServiceName).WaitForStatus("Stopped", (New-TimeSpan -Seconds 30)) } catch { }
        }
    }

    # --- repair ------------------------------------------------------------------------------

    Write-Host ""
    Write-Host "repairing machine material (device identity and enrollment state are preserved)"

    $targets = @(
        @{ Path = $DataDir;                              Kind = "Directory"; Label = "data directory" },
        @{ Path = (Join-Path $DataDir "device.key");     Kind = "Secret";    Label = "device.key" },
        @{ Path = (Join-Path $DataDir "state.json");     Kind = "State";     Label = "state.json" },
        @{ Path = (Join-Path $DataDir "idempotency.json"); Kind = "State";   Label = "idempotency.json" },
        @{ Path = (Join-Path $DataDir "audit");          Kind = "Directory"; Label = "audit directory" },
        @{ Path = (Join-Path $DataDir "logs");           Kind = "Directory"; Label = "logs directory" }
    )

    foreach ($target in $targets) {
        if (Repair-Path -Path $target.Path -Kind $target.Kind) {
            Write-Host ("  repaired {0}" -f $target.Label)
        }
    }

    # Anything else that already exists under the data directory inherits from it now.
    foreach ($child in @(Get-ChildItem -LiteralPath $DataDir -Recurse -Force -ErrorAction SilentlyContinue)) {
        if ($child.FullName -eq (Join-Path $DataDir "device.key")) { continue }
        try {
            $acl = Get-Descriptor -Path $child.FullName
            $acl.SetAccessRuleProtection($false, $false)
            foreach ($rule in @($acl.GetAccessRules($true, $false, [System.Security.Principal.SecurityIdentifier]))) {
                [void]$acl.RemoveAccessRuleSpecific($rule)
            }
            Set-Descriptor -Path $child.FullName -Security $acl
        }
        catch {
            Write-Warning "could not reset $($child.FullName): $($_.Exception.Message)"
        }
    }

    # --- verify ------------------------------------------------------------------------------

    Write-Host ""
    Write-Host "=== after ===" -ForegroundColor Cyan
    Show-Descriptor -Label "data directory" -Path $DataDir
    Show-Descriptor -Label "device.key" -Path (Join-Path $DataDir "device.key")
    Show-Descriptor -Label "state.json" -Path (Join-Path $DataDir "state.json")

    $problems = @()
    foreach ($target in $targets) {
        if (-not (Test-Path -LiteralPath $target.Path)) { continue }
        $problem = Test-Repaired -Path $target.Path -Kind $target.Kind
        if ($problem) { $problems += "$($target.Label): $problem" }
    }

    if (@($problems).Count -gt 0) {
        throw ("the repair did not produce the intended protection:" + [Environment]::NewLine + ($problems -join [Environment]::NewLine))
    }

    Write-Host ""
    Write-Host "verified: SYSTEM and Administrators only; no Users, Authenticated Users or Everyone." -ForegroundColor Green

    $statePath = Join-Path $DataDir "state.json"
    if (Test-Path -LiteralPath $statePath) {
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        Write-Host "device identity preserved: device_id=$($state.device_id) enrolled_at=$($state.enrolled_at)"
    }
}
finally {
    if ($recoverySuspended) {
        Write-Host ""
        Write-Host "restoring the ORIGINAL recovery policy (exact bytes, not a rewrite)"
        try {
            Restore-FailureActionsSnapshot -Name $ServiceName -Snapshot $policySnapshot
        }
        catch {
            # The one state worse than a suspended policy is a silently suspended one.
            Write-Warning "FAILED to restore the recovery policy: $($_.Exception.Message)"
            Write-Warning "Restore it manually: sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/15000/restart/60000"
        }
    }
}

if (-not $SkipStart -and $service) {
    Write-Host ""
    Write-Host "starting $ServiceName ..."
    Start-Service -Name $ServiceName
    (Get-Service -Name $ServiceName).WaitForStatus("Running", (New-TimeSpan -Seconds 45))

    $running = Get-CimInstance -ClassName Win32_Service -Filter "Name='$ServiceName'"
    $process = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$($running.ProcessId)" -ErrorAction SilentlyContinue
    Write-Host "service: state=$($running.State) account=$($running.StartName) pid=$($running.ProcessId) session=$($process.SessionId)"
}

Write-Host ""
Write-Host "Next: .\scripts\verify-device-service.ps1" -ForegroundColor Green
