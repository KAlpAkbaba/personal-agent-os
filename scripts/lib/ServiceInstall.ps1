<#
.SYNOPSIS
    Decisions and actions for registering the Device Service, kept apart on purpose.

.DESCRIPTION
    `Get-ServiceInstallPlan` is pure: given what the machine currently reports about the
    service, it returns what should happen. That makes the interesting cases — a partially
    failed install, a service left running, one marked for deletion, one already pointing at
    the right binary — testable without elevation and without touching the real Service
    Control Manager, which is what the installer's regression tests exercise.

    `Install-DeviceServiceRegistration` performs a plan through the hardened native-process
    helper. Everything that could be a fragile command string goes through argv arrays.
#>

Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "NativeProcess.ps1")

function Get-ServiceRegistration {
    <#
    .SYNOPSIS
        What the machine currently says about a service, or $null if there is none.

    .DESCRIPTION
        Win32_Service rather than Get-Service because the installer needs the configured
        image path, the account and the start mode — the three things an idempotent rerun has
        to compare against.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$ServiceName)

    $service = Get-CimInstance -ClassName Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
    if (-not $service) {
        return $null
    }

    return [pscustomobject]@{
        Name      = $service.Name
        PathName  = $service.PathName
        StartName = $service.StartName
        StartMode = $service.StartMode
        State     = $service.State
        ProcessId = $service.ProcessId
    }
}

function Test-ServicePathEquivalent {
    <#
    .SYNOPSIS
        Compare two service image paths the way the Service Control Manager treats them.

    .DESCRIPTION
        Windows paths are case-insensitive, and the SCM may return the value with different
        surrounding whitespace than it was given. Comparing ordinally would make a purely
        cosmetic difference look like a stale registration — the installer would then
        "repoint" a correct service on every run, and the post-install verification would
        fail an install that actually succeeded. Anything beyond case and outer whitespace is
        treated as a genuine difference.
    #>
    [CmdletBinding()]
    param([string]$Left, [string]$Right)

    if ($null -eq $Left -or $null -eq $Right) {
        return ($Left -eq $Right)
    }

    return [string]::Equals($Left.Trim(), $Right.Trim(), [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-ServiceInstallPlan {
    <#
    .SYNOPSIS
        Decide what to do about the service, given its current registration.

    .OUTPUTS
        An object with Action (Create | Reconfigure | AlreadyCorrect), StopFirst (bool) and
        Reason (a sentence for the operator).

    .DESCRIPTION
        Rerunning the installer after a partial failure has to be safe and boring. The three
        states that matter:

        - nothing registered (including the case this fix exists for: publishing and ACL
          hardening succeeded, then registration failed) -> Create;
        - registered but pointing somewhere else, or running under the wrong account or start
          mode -> Reconfigure, stopping it first if it is running;
        - registered exactly as intended -> AlreadyCorrect, and the installer does not touch
          it. Re-registering a healthy service would restart the owner's desktop agent for no
          reason.
    #>
    [CmdletBinding()]
    param(
        [psobject]$Current,
        [Parameter(Mandatory = $true)][string]$ExpectedPathName,
        [string]$ExpectedAccount = "LocalSystem",
        [string]$ExpectedStartMode = "Auto"
    )

    if (-not $Current) {
        return [pscustomobject]@{
            Action    = "Create"
            StopFirst = $false
            Reason    = "no service is registered yet"
        }
    }

    $differences = @()
    if (-not (Test-ServicePathEquivalent -Left $Current.PathName -Right $ExpectedPathName)) {
        $differences += "image path"
    }
    if (-not [string]::Equals($Current.StartName, $ExpectedAccount, [System.StringComparison]::OrdinalIgnoreCase)) {
        $differences += "account ($($Current.StartName))"
    }
    if ($Current.StartMode -ne $ExpectedStartMode) {
        $differences += "start mode ($($Current.StartMode))"
    }

    if ($differences.Count -eq 0) {
        return [pscustomobject]@{
            Action    = "AlreadyCorrect"
            StopFirst = $false
            Reason    = "the registered service already matches this install"
        }
    }

    return [pscustomobject]@{
        Action    = "Reconfigure"
        StopFirst = ($Current.State -eq "Running")
        Reason    = "the registered service differs in: $($differences -join ', ')"
    }
}

function Install-DeviceServiceRegistration {
    <#
    .SYNOPSIS
        Create or repoint the service, then verify what the SCM actually stored.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ServiceName,
        [Parameter(Mandatory = $true)][string]$ExecutablePath,
        [string[]]$ServiceArguments = @("run"),
        [string]$Account = "LocalSystem",
        [string]$StartMode = "auto",
        [string]$DisplayName = "Personal Agent OS Device Agent",
        [string]$Description = "Personal Agent OS device agent: outbound-only link to the owner's cloud core; executes desktop actions through the owner-session companion."
    )

    $sc = Get-SystemTool -Name "sc.exe"
    $binPathValue = New-ScBinaryPathValue -ExecutablePath $ExecutablePath -ServiceArguments $ServiceArguments

    $current = Get-ServiceRegistration -ServiceName $ServiceName
    $plan = Get-ServiceInstallPlan -Current $current -ExpectedPathName $binPathValue -ExpectedAccount $Account

    Write-Host "service registration: $($plan.Action) - $($plan.Reason)"

    if ($plan.StopFirst) {
        Write-Host "stopping $ServiceName before reconfiguring"
        Stop-Service -Name $ServiceName -Force -ErrorAction Stop
        (Get-Service -Name $ServiceName).WaitForStatus("Stopped", (New-TimeSpan -Seconds 30))
    }

    switch ($plan.Action) {
        "Create" {
            $arguments = New-ScCreateArgumentList -ServiceName $ServiceName -BinaryPathValue $binPathValue `
                -Account $Account -StartMode $StartMode -DisplayName $DisplayName
            # 1073 is "already exists": possible if something registered the service between
            # our read and our write. Treat it as a signal to fall through to config rather
            # than as a failure.
            $result = Invoke-NativeProcess -FilePath $sc -Arguments $arguments -SuccessExitCodes @(0, 1073)
            Assert-NativeSuccess -Result $result -Activity "sc create $ServiceName"
            if ($result.ExitCode -eq 1073) {
                Write-Host "service appeared concurrently; reconfiguring it instead"
                $configure = New-ScConfigArgumentList -ServiceName $ServiceName -BinaryPathValue $binPathValue `
                    -Account $Account -StartMode $StartMode -DisplayName $DisplayName
                Assert-NativeSuccess -Result (Invoke-NativeProcess -FilePath $sc -Arguments $configure) `
                    -Activity "sc config $ServiceName"
            }
        }

        "Reconfigure" {
            $arguments = New-ScConfigArgumentList -ServiceName $ServiceName -BinaryPathValue $binPathValue `
                -Account $Account -StartMode $StartMode -DisplayName $DisplayName
            Assert-NativeSuccess -Result (Invoke-NativeProcess -FilePath $sc -Arguments $arguments) `
                -Activity "sc config $ServiceName"
        }

        "AlreadyCorrect" {
            # Nothing to do, and doing nothing is the point.
        }
    }

    if ($plan.Action -ne "AlreadyCorrect") {
        Assert-NativeSuccess -Result (Invoke-NativeProcess -FilePath $sc -Arguments @("description", $ServiceName, $Description)) `
            -Activity "sc description $ServiceName"
        Assert-NativeSuccess -Result (Invoke-NativeProcess -FilePath $sc -Arguments (New-ScFailureArgumentList -ServiceName $ServiceName)) `
            -Activity "sc failure $ServiceName"
    }

    # Verify against what the SCM stored, not against what we believe we sent. A service
    # whose image path was mangled starts fine and then fails to launch, hours later, with a
    # message about a file that does not exist.
    $verified = Get-ServiceRegistration -ServiceName $ServiceName
    if (-not $verified) {
        throw "the service is still not registered after $($plan.Action); nothing else will work until this succeeds"
    }
    if (-not (Test-ServicePathEquivalent -Left $verified.PathName -Right $binPathValue)) {
        throw @(
            "the Service Control Manager stored a different image path than intended:",
            "  intended: $binPathValue",
            "  stored  : $($verified.PathName)"
        ) -join [Environment]::NewLine
    }
    if (-not [string]::Equals($verified.StartName, $Account, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "the service is registered to run as '$($verified.StartName)', not '$Account'"
    }

    Write-Host "verified: $ServiceName runs $($verified.PathName) as $($verified.StartName) ($($verified.StartMode))"
    return $verified
}
