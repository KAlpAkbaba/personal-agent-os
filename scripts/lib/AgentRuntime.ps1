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
#>

Set-StrictMode -Version Latest

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
        then anything else still executing from the install root; returns only when the
        PIDs are gone.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$ServiceName,
        [string]$InstallRoot
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
