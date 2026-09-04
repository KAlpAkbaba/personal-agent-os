<#
.SYNOPSIS
    Stop / start / health-check the installed agent runtime (service, companion, and any
    Browser Worker process executing from the install tree) for the deployment engine.

.DESCRIPTION
    These are the production handlers `Invoke-AgentDeployment` (scripts/lib/Deployment.ps1)
    is driven with. They exist as a library because the installer's own inline swap
    reproduced, on 2026-09-03, the exact incident the engine was written for on
    2026-09-01: NTFS refuses to rename a directory whose subtree holds a running process's
    mapped images, the first Move-Item died, `.previous` stayed empty, the M13 candidate
    stayed in `.staging`, and the old binaries kept running while the installer looked as
    if it had worked.

    Stop is verified by PID exit, never by "Stopped" alone, and it also ends any process
    whose executable lives under the install root (M13: the Browser Worker's python.exe
    runs from `<root>\browser\.venv`, and it would lock the `browser` tree just like the
    service locks `service`).

    Stopping the worker is not the end of it (2026-09-03, the second incident): the Chrome
    that worker launched on the PagentOS profile does not die with python.exe, it keeps
    the profile locked, and every later launch on the locked profile opens one more window
    in the orphan. So the stop also ends chrome.exe MAIN processes whose command line names
    the PagentOS profile directory - that profile only, never the owner's own Chrome - and
    prints their PIDs.
#>

Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "BrowserRelease.ps1")

function Get-DefaultBrowserProfileDir {
    <#
    .SYNOPSIS
        Where the installer puts the companion's dedicated Chrome profile
        (New-CompanionBrowserSettings: <ProgramData>\PagentOS\companion\browser\profile).
    #>
    return (Join-Path $env:ProgramData "PagentOS\companion\browser\profile")
}

function Test-BrowserProfileCommandLine {
    <#
    .SYNOPSIS
        Pure: does this command line carry --user-data-dir=<ProfileDir>? The value must be
        the whole profile path (case-insensitive, trailing separators and quotes ignored);
        a prefix such as "<ProfileDir>2" or the parent directory is NOT a match.
    #>
    param(
        [AllowNull()][AllowEmptyString()][string]$CommandLine,
        [Parameter(Mandatory = $true)][string]$ProfileDir
    )
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    $wanted = $ProfileDir.Trim().Trim('"').TrimEnd('\', '/')
    if ([string]::IsNullOrWhiteSpace($wanted)) { return $false }
    $switch = '--user-data-dir='
    $index = $CommandLine.IndexOf($switch, [System.StringComparison]::OrdinalIgnoreCase)
    while ($index -ge 0) {
        $valueStart = $index + $switch.Length
        $rest = $CommandLine.Substring($valueStart)
        $value = $rest
        if ($rest.StartsWith('"')) {
            # --user-data-dir="C:\path with spaces"
            $close = $rest.IndexOf('"', 1)
            if ($close -gt 0) { $value = $rest.Substring(1, $close - 1) } else { $value = $rest.Substring(1) }
        }
        elseif ($index -gt 0 -and $CommandLine[$index - 1] -eq '"') {
            # "--user-data-dir=C:\path with spaces" (one quoted argument)
            $close = $rest.IndexOf('"')
            if ($close -ge 0) { $value = $rest.Substring(0, $close) }
        }
        else {
            $end = $rest.IndexOfAny([char[]]@(' ', '"'))
            if ($end -ge 0) { $value = $rest.Substring(0, $end) }
        }
        if ([string]::Equals($value.TrimEnd('\', '/'), $wanted, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
        $index = $CommandLine.IndexOf($switch, $valueStart, [System.StringComparison]::OrdinalIgnoreCase)
    }
    return $false
}

function Select-OrphanBrowserProcess {
    <#
    .SYNOPSIS
        Pure filter over process records (Win32_Process or fakes with Name / ProcessId /
        CommandLine): chrome.exe, a MAIN process (no --type=), on the PagentOS profile.
        Renderer and helper children die with their main process; anything else - the
        owner's Chrome, Edge, the worker's python.exe - is never selected.
    #>
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Processes,
        [Parameter(Mandatory = $true)][string]$ProfileDir
    )
    $selected = @()
    foreach ($process in @($Processes)) {
        if ($null -eq $process) { continue }
        if (-not [string]::Equals([string]$process.Name, 'chrome.exe', [System.StringComparison]::OrdinalIgnoreCase)) { continue }
        $commandLine = [string]$process.CommandLine
        if ($commandLine.IndexOf('--type=', [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { continue }
        if (Test-BrowserProfileCommandLine -CommandLine $commandLine -ProfileDir $ProfileDir) { $selected += $process }
    }
    return @($selected)
}

function Stop-OrphanBrowserProcesses {
    <#
    .SYNOPSIS
        End every chrome.exe main process holding the PagentOS profile, print each PID,
        wait for them to be gone, and return the PIDs that were stopped.
    #>
    param([Parameter(Mandatory = $true)][string]$ProfileDir)
    $all = @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue)
    $orphans = @(Select-OrphanBrowserProcess -Processes $all -ProfileDir $ProfileDir)
    if (@($orphans).Count -eq 0) { return @() }
    $stopped = @()
    foreach ($p in $orphans) {
        Write-Host "stopping orphan chrome.exe (pid $($p.ProcessId)) holding the PagentOS browser profile $ProfileDir"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        $stopped += [int]$p.ProcessId
    }
    [void](Wait-ProcessGone -ProcessIds @($stopped) -TimeoutSeconds 15)
    return @($stopped)
}

function Get-ProcessesExecutingUnder {
    param([Parameter(Mandatory = $true)][string]$Root)
    if (-not (Test-Path -LiteralPath $Root)) { return @() }
    $prefix = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\') + '\'
    return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
    })
}

function Wait-ProcessGone {
    param([int[]]$ProcessIds, [int]$TimeoutSeconds = 30)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $alive = @($ProcessIds | Where-Object { $_ -gt 0 -and (Get-Process -Id $_ -ErrorAction SilentlyContinue) })
        if (@($alive).Count -eq 0) { return $true }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

function Stop-AgentRuntime {
    <#
    .SYNOPSIS
        Companion first (so it stops asking the service for anything), then the service,
        then anything else still executing from the install root, then the Chrome the
        Browser Worker left on the PagentOS profile; returns only when the PIDs are gone.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$ServiceName,
        [string]$InstallRoot,
        [string]$BrowserProfileDir = (Get-DefaultBrowserProfileDir)
    )
    $companion = @(Get-Process -Name "PagentOS.SessionCompanion" -ErrorAction SilentlyContinue)
    if (@($companion).Count -gt 0) {
        $companion | Stop-Process -Force -ErrorAction SilentlyContinue
        [void](Wait-ProcessGone -ProcessIds @($companion | ForEach-Object { $_.Id }) -TimeoutSeconds 15)
    }

    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($service -and $service.Status -ne "Stopped") {
        $servicePid = 0
        $cim = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
        if ($cim) { $servicePid = [int]$cim.ProcessId }
        Stop-Service -Name $ServiceName -Force
        (Get-Service -Name $ServiceName).WaitForStatus("Stopped", (New-TimeSpan -Seconds 45))
        if ($servicePid -gt 0) {
            # SCM reports Stopped before the process has exited; wait for the PID itself.
            [void](Wait-ProcessGone -ProcessIds @($servicePid) -TimeoutSeconds 30)
        }
    }

    if ($InstallRoot) {
        # Browser Worker (python.exe from <root>\browser\.venv) or any straggler.
        $others = @(Get-ProcessesExecutingUnder -Root $InstallRoot)
        if (@($others).Count -gt 0) {
            foreach ($p in $others) {
                Write-Host "stopping $($p.Name) (pid $($p.ProcessId)) still executing from the install tree"
                Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            }
            [void](Wait-ProcessGone -ProcessIds @($others | ForEach-Object { [int]$_.ProcessId }) -TimeoutSeconds 15)
        }
    }

    # The Browser Worker is two processes: the venv's python.exe (under the install root, a
    # trampoline) and the real interpreter it launches, which runs from the base Python
    # OUTSIDE the install root with the same `-m browser_agent.worker` command line. Killing
    # the companion never reaches either (2026-09-04: an old worker survived the swap and kept
    # answering commands). Stop every process running the worker module, then wait.
    $workers = @(Select-BrowserWorkerProcess -Processes @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue))
    if (@($workers).Count -gt 0) {
        foreach ($p in $workers) {
            Write-Host "stopping browser worker $($p.Name) (pid $($p.ProcessId)) still running -m browser_agent.worker"
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
        [void](Wait-ProcessGone -ProcessIds @($workers | ForEach-Object { [int]$_.ProcessId }) -TimeoutSeconds 15)
    }

    if (-not [string]::IsNullOrWhiteSpace($BrowserProfileDir)) {
        # The worker is gone; its Chrome is not. Only chrome.exe main processes whose
        # command line names THIS profile - the owner's Chrome never does.
        [void](Stop-OrphanBrowserProcesses -ProfileDir $BrowserProfileDir)
    }
}

function Start-AgentRuntime {
    <#
    .SYNOPSIS
        Service, then the companion through its logon task (so it runs in the owner's
        interactive session, not in the elevated installer's).
    #>
    param(
        [Parameter(Mandatory = $true)][string]$ServiceName,
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [string]$CompanionTaskName = "PagentOS Session Companion"
    )
    Start-Service -Name $ServiceName
    (Get-Service -Name $ServiceName).WaitForStatus("Running", (New-TimeSpan -Seconds 45))
    & (Join-Path $env:SystemRoot "System32\schtasks.exe") /Run /TN $CompanionTaskName | Out-Null
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline -and -not (Get-Process -Name "PagentOS.SessionCompanion" -ErrorAction SilentlyContinue)) {
        Start-Sleep -Milliseconds 500
    }
    if (-not (Get-Process -Name "PagentOS.SessionCompanion" -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath (Join-Path $InstallRoot "companion\PagentOS.SessionCompanion.exe") -WindowStyle Hidden
    }
}

function Test-AgentRuntimeHealth {
    <#
    .SYNOPSIS
        Running is not health: service Running AND a companion process AND the pipe present
        in the namespace, within the timeout.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$ServiceName,
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [int]$TimeoutSeconds = 30
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
        if ($service -and $service.State -eq "Running" -and
            (Get-Process -Name "PagentOS.SessionCompanion" -ErrorAction SilentlyContinue)) {
            $config = [System.IO.File]::ReadAllText($ConfigPath) | ConvertFrom-Json
            $listed = @([System.IO.Directory]::GetFiles("\\.\pipe\") | Where-Object { $_ -match [regex]::Escape($config.PipeName) })
            if (@($listed).Count -ge 1) { return $true }
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    return $false
}
