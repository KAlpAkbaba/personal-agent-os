<#
.SYNOPSIS
    Provisioning of the Browser Worker (services\browser) for the installed Windows agent
    (M13, BROWSER_CAPABILITIES.md §7) — the pure, testable pieces of the installer step.

.DESCRIPTION
    The worker is a Python package that the Session Companion starts as a child process.
    The installer copies the package into <InstallRoot>\browser through the same staging /
    publish mechanism as the service and companion, creates its virtual environment there
    with uv, proves the worker can start (its --self-check prints the hello and exits 0),
    and only then writes the companion's BrowserWorker* settings and the service's
    BrowserEnabled=true. Anything that fails leaves the previous tree untouched.

    Two things about the venv are easy to get wrong and are checked here rather than
    discovered on the owner's machine:

      * it is created in STAGING and then moved. A venv is relocatable only if nothing in it
        names the staging path: the interpreter it points at (pyvenv.cfg `home`) must live
        outside the tree being moved, and the package must be installed as a real copy, not
        an editable .pth pointing back at staging. Test-VenvStagingReferences finds either;
      * it lives in Program Files, read-only to the owner. The worker therefore writes only
        under the companion's browser data directory, which the installer creates with an
        explicit grant for the owner's SID — ProgramData's default ACL would otherwise make
        a directory created by an elevated process unwritable for the owner's companion.

    Windows PowerShell 5.1. Nothing here elevates or contacts the network by itself.
#>

Set-StrictMode -Version Latest

function Resolve-UvPath {
    <#
    .SYNOPSIS
        Absolute path to uv, resolved the way scripts\preflight.ps1 resolves it — never by
        assuming PATH, which is not trustworthy in a spawned or elevated shell.
    #>
    [CmdletBinding()]
    param(
        [string]$Explicit,
        [string[]]$Fallbacks = @(
            "%USERPROFILE%\.local\bin\uv.exe",
            "%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe"
        )
    )

    if ($Explicit) {
        if (-not (Test-Path -LiteralPath $Explicit)) {
            throw "uv was given explicitly but does not exist: $Explicit"
        }
        return (Resolve-Path -LiteralPath $Explicit).Path
    }

    $command = Get-Command uv -ErrorAction SilentlyContinue
    if ($command -and $command.Source -and (Test-Path -LiteralPath $command.Source)) {
        return $command.Source
    }

    foreach ($candidate in $Fallbacks) {
        $expanded = [Environment]::ExpandEnvironmentVariables($candidate)
        if (Test-Path -LiteralPath $expanded) {
            return $expanded
        }
    }

    throw ("uv was not found on PATH or at any known location (" + ($Fallbacks -join ", ") + "). " +
        "Install uv (winget install astral-sh.uv) or pass -UvPath, or rerun with -SkipBrowser.")
}

function Copy-BrowserPackageTree {
    <#
    .SYNOPSIS
        Copy exactly what the worker needs from services\browser into a staging directory:
        pyproject.toml, uv.lock, README.md and browser_agent\** without caches.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    foreach ($required in @("pyproject.toml", "uv.lock", "browser_agent")) {
        if (-not (Test-Path -LiteralPath (Join-Path $Source $required))) {
            throw "the browser package at $Source is incomplete: missing $required"
        }
    }

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $copied = 0
    foreach ($file in @("pyproject.toml", "uv.lock", "README.md")) {
        $path = Join-Path $Source $file
        if (Test-Path -LiteralPath $path) {
            Copy-Item -LiteralPath $path -Destination (Join-Path $Destination $file) -Force
            $copied++
        }
    }

    # Resolve to the on-disk long form: Get-ChildItem reports long-form FullNames, while a
    # caller may hand us an 8.3 short path (the GitHub runner's TEMP is C:\Users\RUNNER~1\...),
    # and a Substring on the short length would misplace every nested file.
    $packageSource = (Get-Item -LiteralPath (Join-Path $Source "browser_agent")).FullName
    $packageDestination = Join-Path $Destination "browser_agent"
    foreach ($item in @(Get-ChildItem -LiteralPath $packageSource -Recurse -File -Force)) {
        $relative = $item.FullName.Substring($packageSource.Length).TrimStart('\')
        if ($relative -match '(^|\\)__pycache__(\\|$)' -or $item.Extension -in @(".pyc", ".pyo")) {
            continue
        }
        $target = Join-Path $packageDestination $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath $item.FullName -Destination $target -Force
        $copied++
    }

    return $copied
}

function Get-BrowserWorkerPython {
    <#  The interpreter the companion will be configured to run.  #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$BrowserRoot)
    return (Join-Path $BrowserRoot ".venv\Scripts\python.exe")
}

function Get-BrowserWorkerArgs {
    <#  The leading arguments the companion prepends (BROWSER_CAPABILITIES.md §7 CLI).  #>
    [CmdletBinding()]
    param()
    return "-m browser_agent.worker"
}

function New-BrowserWorkerSelfCheckArgumentList {
    <#
    .SYNOPSIS
        argv for `python -m browser_agent.worker --self-check`: the worker prints its hello
        JSON and exits 0, or exits non-zero when Chrome or the package is missing.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Channel,
        [Parameter(Mandatory = $true)][string]$DataDir
    )

    return @(
        "-m", "browser_agent.worker",
        "--self-check",
        "--channel", $Channel,
        "--headless",
        "--data-dir", $DataDir,
        "--profile-dir", (Join-Path $DataDir "profile")
    )
}

function ConvertFrom-WorkerHelloOutput {
    <#
    .SYNOPSIS
        The hello document from a self-check's stdout: the LAST line that parses as a JSON
        object with type=hello. Anything else on stdout is tolerated (and returned as noise).
    #>
    [CmdletBinding()]
    param([AllowEmptyString()][AllowNull()][string]$StdOut)

    $hello = $null
    $noise = New-Object System.Collections.ArrayList
    foreach ($line in @(($StdOut -split "`r?`n") | Where-Object { $_ -and $_.Trim() })) {
        $parsed = $null
        try { $parsed = $line.Trim() | ConvertFrom-Json } catch { $parsed = $null }
        if ($parsed -and (Test-ObjectProperty -InputObject $parsed -Name "type") -and $parsed.type -eq "hello") {
            $hello = $parsed
        }
        else {
            [void]$noise.Add($line)
        }
    }

    $capabilities = @()
    if ($hello -and (Test-ObjectProperty -InputObject $hello -Name "capabilities")) {
        $capabilities = @($hello.capabilities)
    }

    return [pscustomobject]@{
        Hello        = $hello
        Capabilities = @($capabilities)
        Noise        = @($noise)
    }
}

function Invoke-BrowserWorkerSelfCheck {
    <#
    .SYNOPSIS
        Run the worker's self-check and return what it said. Never throws on a non-zero
        exit; the caller decides, with the full stderr in hand.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$Channel,
        [Parameter(Mandatory = $true)][string]$DataDir,
        [string]$WorkingDirectory,
        [int]$TimeoutSeconds = 180
    )

    if (-not (Test-Path -LiteralPath $Python)) {
        return [pscustomobject]@{
            Ok = $false; ExitCode = -1; Hello = $null; Capabilities = @(); StdOut = ""; StdErr = "interpreter not found: $Python"
        }
    }

    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    $arguments = New-BrowserWorkerSelfCheckArgumentList -Channel $Channel -DataDir $DataDir
    if (-not $WorkingDirectory) { $WorkingDirectory = $DataDir }

    $result = Invoke-NativeProcess -FilePath $Python -Arguments $arguments -TimeoutSeconds $TimeoutSeconds -WorkingDirectory $WorkingDirectory
    $parsed = ConvertFrom-WorkerHelloOutput -StdOut $result.StdOut

    return [pscustomobject]@{
        Ok           = ($result.ExitCode -eq 0 -and $null -ne $parsed.Hello)
        ExitCode     = $result.ExitCode
        Hello        = $parsed.Hello
        Capabilities = @($parsed.Capabilities)
        StdOut       = $result.StdOut
        StdErr       = $result.StdErr
    }
}

function Get-VenvHome {
    <#  The `home =` line of pyvenv.cfg: where the venv's interpreter really lives.  #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$VenvDir)

    $cfg = Join-Path $VenvDir "pyvenv.cfg"
    if (-not (Test-Path -LiteralPath $cfg)) {
        return $null
    }
    foreach ($line in @(Get-Content -LiteralPath $cfg)) {
        if ($line -match '^\s*home\s*=\s*(.+?)\s*$') {
            return $Matches[1]
        }
    }
    return $null
}

function Test-VenvStagingReferences {
    <#
    .SYNOPSIS
        Anything inside the venv that names the staging root will break the moment the tree
        is moved into place. Returns the offending references (empty = relocatable).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$VenvDir,
        [Parameter(Mandatory = $true)][string]$StagingRoot
    )

    $offenders = New-Object System.Collections.ArrayList
    $needle = $StagingRoot.TrimEnd('\')

    $venvHome = Get-VenvHome -VenvDir $VenvDir
    if ($venvHome -and $venvHome.StartsWith($needle, [System.StringComparison]::OrdinalIgnoreCase)) {
        [void]$offenders.Add("pyvenv.cfg home points into staging: $venvHome")
    }

    $sitePackages = Join-Path $VenvDir "Lib\site-packages"
    if (Test-Path -LiteralPath $sitePackages) {
        foreach ($pth in @(Get-ChildItem -LiteralPath $sitePackages -Filter "*.pth" -File -ErrorAction SilentlyContinue)) {
            foreach ($line in @(Get-Content -LiteralPath $pth.FullName)) {
                if ($line -and $line.Trim().StartsWith($needle, [System.StringComparison]::OrdinalIgnoreCase)) {
                    [void]$offenders.Add("$($pth.Name) points into staging (editable install?): $($line.Trim())")
                }
            }
        }
    }

    return @($offenders)
}

function New-CompanionBrowserSettings {
    <#
    .SYNOPSIS
        The companion appsettings keys for the provisioned worker, as an ordered hashtable
        the caller merges into its configuration. Paths only, no secrets, nothing per-PC
        beyond the install and data roots the caller chose.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$BrowserRoot,
        [Parameter(Mandatory = $true)][string]$BrowserDataDir,
        [string]$Channel = "chrome",
        [bool]$Visible = $true
    )

    return [ordered]@{
        BrowserWorkerCommand = (Get-BrowserWorkerPython -BrowserRoot $BrowserRoot)
        BrowserWorkerArgs    = (Get-BrowserWorkerArgs)
        BrowserDataDir       = $BrowserDataDir
        BrowserProfileDir    = (Join-Path $BrowserDataDir "profile")
        BrowserChannel       = $Channel
        BrowserVisible       = $(if ($Visible) { "true" } else { "false" })
        BrowserIdleTimeoutS  = "600"
        BrowserWorkerEager   = "false"
    }
}

function Set-OwnerWritableDirectory {
    <#
    .SYNOPSIS
        Create a directory (if needed) and grant one SID Modify on it and everything below,
        as an explicit inheritable ACE. Existing inheritance is kept.

    .DESCRIPTION
        Used for the companion's browser data directory under ProgramData. An elevated
        installer creating that directory makes Administrators its owner, and ProgramData's
        inherited ACL gives ordinary users no write there — so the owner's non-elevated
        companion (and the worker it starts) could not write the profile, downloads or logs.
        The grant is what makes "the worker writes only under BrowserDataDir" possible.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$OwnerSid
    )

    New-Item -ItemType Directory -Force -Path $Path | Out-Null
    $identity = New-Object System.Security.Principal.SecurityIdentifier($OwnerSid)
    $acl = Get-Acl -LiteralPath $Path
    $inherit = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
               [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $identity,
        [System.Security.AccessControl.FileSystemRights]::Modify,
        $inherit,
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow)
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Test-OwnerWritableDirectory {
    <#  Does an explicit, inheritable Modify (or better) ACE for this SID exist on the directory?  #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$OwnerSid
    )

    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $acl = Get-Acl -LiteralPath $Path
    foreach ($ace in @($acl.GetAccessRules($true, $false, [System.Security.Principal.SecurityIdentifier]))) {
        if ($ace.IdentityReference.Value -ne $OwnerSid) { continue }
        if ($ace.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) { continue }
        $rights = $ace.FileSystemRights
        $modify = [System.Security.AccessControl.FileSystemRights]::Modify
        if (($rights -band $modify) -eq $modify -and
            ($ace.InheritanceFlags -band [System.Security.AccessControl.InheritanceFlags]::ContainerInherit)) {
            return $true
        }
    }
    return $false
}
