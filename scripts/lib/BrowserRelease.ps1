<#
.SYNOPSIS
    Release identity of the Browser Worker: what the SOURCE says the release is, and whether
    a live worker's hello proves it is running THAT release from the installed venv.

.DESCRIPTION
    Deployment truthfulness defect (2026-09-04, ADR-0050 item 16): the installer reported
    INSTALL VERIFIED while the companion kept running a 0.1.0 worker. Two facts combined:

      * uv rebuilt the non-editable package from a stale cached wheel: the staging path is
        the same on every release and uv's cache key for a local project is the mtime of
        pyproject.toml, which code-only releases never touch. The source tree under
        <root>\browser\browser_agent was new; the copy under
        <root>\browser\.venv\Lib\site-packages\browser_agent was old.
      * the installer's self-check ran `python -m browser_agent.worker` WITH THE BROWSER TREE
        AS WORKING DIRECTORY, so Python imported the fresh source tree (sys.path[0] is the
        cwd) and the check passed; the companion runs the same command with its data
        directory as cwd and imported the stale site-packages copy.

    The functions here make the release explicit and checkable:

      Get-ExpectedWorkerRelease  - parse WORKER_VERSION / CONTRACTS out of a source tree's
                                   worker.py, hash it, and digest the whole package;
      Get-BrowserPackageDigest   - the package digest (same algorithm as browser_agent/release.py);
      Assert-WorkerHelloMatchesRelease - a hello's version, contracts and `module` block
                                   (file path, worker hash, package digest) must agree with the
                                   expected release AND the module must live inside the venv
                                   of the tree under test, never in a source directory;
      Compare-BrowserPackageCopies - source browser_agent vs site-packages browser_agent,
                                   file by file;
      Get-BrowserWorkerSyncArguments - the uv sync argv, with --reinstall-package so the
                                   build cache can never serve a stale wheel again.

    Windows PowerShell 5.1, pure (no elevation, no network); the OS-facing callers live in the
    installer and the verifier.
#>

Set-StrictMode -Version Latest

function Get-FileSha256Hex {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-BrowserPackageDigest {
    <#
    .SYNOPSIS
        SHA-256 over "<name>:<sha256>`n" for every *.py directly inside the package directory,
        sorted by name (ordinal). Mirrors browser_agent/release.py package_digest.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$PackageDir)
    if (-not (Test-Path -LiteralPath $PackageDir)) { throw "package directory does not exist: $PackageDir" }
    $files = @(Get-ChildItem -LiteralPath $PackageDir -File | Where-Object { $_.Extension -eq ".py" } | Sort-Object -Property @{ Expression = { $_.Name }; Ascending = $true } -Culture "")
    $builder = New-Object System.Text.StringBuilder
    foreach ($file in ([string[]]($files | ForEach-Object { $_.Name }) | Sort-Object { $_ } -Culture "" )) {
        $path = Join-Path $PackageDir $file
        [void]$builder.Append($file).Append(":").Append((Get-FileSha256Hex -Path $path)).Append("`n")
    }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($builder.ToString())
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ([System.BitConverter]::ToString($sha.ComputeHash($bytes)) -replace "-", "").ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-ExpectedWorkerRelease {
    <#
    .SYNOPSIS
        What a services\browser source tree says its worker release is.
    .OUTPUTS
        Version, Contracts (hashtable name->int), WorkerSha256, PackageSha256, PackageDir, WorkerFile
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$BrowserSource)
    $packageDir = Join-Path $BrowserSource "browser_agent"
    $workerFile = Join-Path $packageDir "worker.py"
    if (-not (Test-Path -LiteralPath $workerFile)) { throw "no worker.py under $packageDir" }
    $text = [System.IO.File]::ReadAllText($workerFile)
    $versionMatch = [regex]::Match($text, '(?m)^WORKER_VERSION\s*=\s*"([^"]+)"')
    if (-not $versionMatch.Success) { throw "WORKER_VERSION not found in $workerFile" }
    $contracts = @{}
    $contractsMatch = [regex]::Match($text, '(?m)^CONTRACTS[^=]*=\s*\{([^}]*)\}')
    if ($contractsMatch.Success) {
        foreach ($pair in [regex]::Matches($contractsMatch.Groups[1].Value, '"([^"]+)"\s*:\s*(\d+)')) {
            $contracts[$pair.Groups[1].Value] = [int]$pair.Groups[2].Value
        }
    }
    return [pscustomobject]@{
        Version       = $versionMatch.Groups[1].Value
        Contracts     = $contracts
        WorkerSha256  = Get-FileSha256Hex -Path $workerFile
        PackageSha256 = Get-BrowserPackageDigest -PackageDir $packageDir
        PackageDir    = $packageDir
        WorkerFile    = $workerFile
    }
}

function Test-PathInsideDirectory {
    param(
        [AllowNull()][AllowEmptyString()][string]$Path,
        [Parameter(Mandatory = $true)][string]$Directory
    )
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }
    $normalizedDir = $Directory.TrimEnd('\', '/') + '\'
    $normalizedPath = $Path.Replace('/', '\')
    return $normalizedPath.StartsWith($normalizedDir, [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-WorkerHelloMatchesRelease {
    <#
    .SYNOPSIS
        Throw unless the hello proves the worker runs the expected release from the venv of
        the tree under test. Returns a summary object when it does.
    .PARAMETER Hello
        The parsed hello (ConvertFrom-WorkerHelloOutput) or a browser.worker_status result
        carrying worker_version, contracts and module.
    .PARAMETER Expected
        Get-ExpectedWorkerRelease of the source tree this venv was built from.
    .PARAMETER BrowserRoot
        The tree under test (staging or installed): the module must live under its .venv.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Hello,
        [Parameter(Mandatory = $true)]$Expected,
        [Parameter(Mandatory = $true)][string]$BrowserRoot,
        [string]$Label = "worker"
    )
    $problems = @()
    $version = $null
    if ($Hello.PSObject.Properties.Name -contains "worker_version") { $version = [string]$Hello.worker_version }
    if ($version -ne $Expected.Version) { $problems += "worker_version is '$version', the staged source is '$($Expected.Version)'" }

    $module = $null
    if ($Hello.PSObject.Properties.Name -contains "module") { $module = $Hello.module }
    if ($null -eq $module) {
        $problems += "the hello carries no 'module' block (a worker older than release 0.3.0 is running)"
    }
    else {
        $file = if ($module.PSObject.Properties.Name -contains "file") { [string]$module.file } else { "" }
        $sha = if ($module.PSObject.Properties.Name -contains "sha256") { [string]$module.sha256 } else { "" }
        $pkg = if ($module.PSObject.Properties.Name -contains "package_sha256") { [string]$module.package_sha256 } else { "" }
        $venv = Join-Path $BrowserRoot ".venv"
        if (-not (Test-PathInsideDirectory -Path $file -Directory $venv)) {
            $problems += "the executing module is '$file', not inside the venv '$venv' (a source directory on sys.path shadowed the installed package)"
        }
        if ($sha -ne $Expected.WorkerSha256) { $problems += "worker.py sha256 is '$sha', the staged source is '$($Expected.WorkerSha256)'" }
        if ($pkg -ne $Expected.PackageSha256) { $problems += "package digest is '$pkg', the staged source is '$($Expected.PackageSha256)'" }
    }

    $liveContracts = @{}
    if ($Hello.PSObject.Properties.Name -contains "contracts" -and $null -ne $Hello.contracts) {
        foreach ($prop in $Hello.contracts.PSObject.Properties) { $liveContracts[$prop.Name] = [int]$prop.Value }
    }
    foreach ($name in $Expected.Contracts.Keys) {
        $live = if ($liveContracts.ContainsKey($name)) { $liveContracts[$name] } else { 0 }
        if ($live -ne $Expected.Contracts[$name]) { $problems += "contract $name is $live, the staged source is $($Expected.Contracts[$name])" }
    }

    if (@($problems).Count -gt 0) {
        throw ("deployment/version mismatch ($Label): " + ($problems -join "; "))
    }
    return [pscustomobject]@{
        Version       = $version
        ModuleFile    = [string]$module.file
        WorkerSha256  = [string]$module.sha256
        PackageSha256 = [string]$module.package_sha256
        Contracts     = $liveContracts
    }
}

function Compare-BrowserPackageCopies {
    <#
    .SYNOPSIS
        Source package directory vs the venv's site-packages copy, file by file (*.py).
        Returns the differences as strings; empty means identical.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Installed
    )
    $differences = @()
    if (-not (Test-Path -LiteralPath $Installed)) { return @("installed package directory missing: $Installed") }
    $sourceFiles = @{}
    foreach ($f in @(Get-ChildItem -LiteralPath $Source -File | Where-Object { $_.Extension -eq ".py" })) { $sourceFiles[$f.Name] = Get-FileSha256Hex -Path $f.FullName }
    $installedFiles = @{}
    foreach ($f in @(Get-ChildItem -LiteralPath $Installed -File | Where-Object { $_.Extension -eq ".py" })) { $installedFiles[$f.Name] = Get-FileSha256Hex -Path $f.FullName }
    foreach ($name in ($sourceFiles.Keys | Sort-Object)) {
        if (-not $installedFiles.ContainsKey($name)) { $differences += "$name missing from the installed copy"; continue }
        if ($installedFiles[$name] -ne $sourceFiles[$name]) { $differences += "$name differs (source $($sourceFiles[$name].Substring(0,12)), installed $($installedFiles[$name].Substring(0,12)))" }
    }
    foreach ($name in ($installedFiles.Keys | Sort-Object)) {
        if (-not $sourceFiles.ContainsKey($name)) { $differences += "$name present only in the installed copy (stale)" }
    }
    return @($differences)
}

function Get-SitePackagesBrowserAgentDir {
    <#  Where the non-editable install puts the package inside a venv.  #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$BrowserRoot)
    return (Join-Path $BrowserRoot ".venv\Lib\site-packages\browser_agent")
}

function Get-BrowserWorkerSyncArguments {
    <#
    .SYNOPSIS
        argv for uv when building the worker environment. --reinstall-package forces the
        project itself to be rebuilt from the staged source instead of uv's build cache.
    #>
    [CmdletBinding()]
    param()
    return @("sync", "--frozen", "--no-dev", "--no-editable", "--reinstall-package", "pagentos-browser")
}

function Select-BrowserWorkerProcess {
    <#
    .SYNOPSIS
        Pure filter over process records (Win32_Process or fakes with ProcessId /
        CommandLine): every process running `-m browser_agent.worker`, whatever its
        executable (the venv's python.exe is a trampoline whose child runs from the base
        interpreter outside the install root). Optionally only those whose command line
        names a specific --data-dir.
    #>
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Processes,
        [string]$DataDir
    )
    $selected = @()
    foreach ($process in @($Processes)) {
        if ($null -eq $process) { continue }
        # Only a python interpreter started with `-m browser_agent.worker`: a shell, editor or
        # log viewer whose command line merely mentions the module must never be selected.
        $name = [string]$process.Name
        if ($name -notmatch '^python(w)?(\d+(\.\d+)?)?\.exe$') { continue }
        $commandLine = [string]$process.CommandLine
        if ($commandLine -notmatch '(^|\s)-m\s+browser_agent\.worker(\s|$)') { continue }
        if ($DataDir) {
            $wanted = $DataDir.TrimEnd('\', '/')
            $match = [regex]::Match($commandLine, '--data-dir\s+("([^"]*)"|(\S+))', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
            if (-not $match.Success) { continue }
            $value = if ($match.Groups[2].Success) { $match.Groups[2].Value } else { $match.Groups[3].Value }
            if (-not [string]::Equals($value.TrimEnd('\', '/'), $wanted, [System.StringComparison]::OrdinalIgnoreCase)) { continue }
        }
        $selected += $process
    }
    return @($selected)
}

function Get-LatestWorkerStartAudit {
    <#
    .SYNOPSIS
        The newest browser_worker_started row of the companion audit (JSON lines) not older
        than -Since, parsed: Ts, Pid, WorkerVersion, Module (from the detail string).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$AuditPath,
        [datetime]$Since = [datetime]::MinValue
    )
    if (-not (Test-Path -LiteralPath $AuditPath)) { return $null }
    $best = $null
    foreach ($line in @(Get-Content -LiteralPath $AuditPath -Tail 500 -ErrorAction SilentlyContinue)) {
        if ($line -notlike '*browser_worker_started*') { continue }
        $row = $null
        try { $row = $line | ConvertFrom-Json } catch { continue }
        if ($null -eq $row -or $row.event -ne "browser_worker_started") { continue }
        $ts = [datetime]::Parse([string]$row.ts, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AdjustToUniversal)
        if ($ts -lt $Since.ToUniversalTime()) { continue }
        $detail = [string]$row.detail
        $pidMatch = [regex]::Match($detail, 'pid=(\d+)')
        $versionMatch = [regex]::Match($detail, 'worker_version=([^;]+)')
        $moduleMatch = [regex]::Match($detail, 'module=([^;]+)')
        $parsed = [pscustomobject]@{
            Ts            = $ts
            Pid           = $(if ($pidMatch.Success) { [int]$pidMatch.Groups[1].Value } else { 0 })
            WorkerVersion = $(if ($versionMatch.Success) { $versionMatch.Groups[1].Value.Trim() } else { "" })
            Module        = $(if ($moduleMatch.Success) { $moduleMatch.Groups[1].Value.Trim() } else { "" })
            Detail        = $detail
        }
        if ($null -eq $best -or $parsed.Ts -gt $best.Ts) { $best = $parsed }
    }
    return $best
}

function Wait-LiveBrowserWorkerAudit {
    <#  Poll the companion audit until a browser_worker_started row newer than -Since appears.  #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$AuditPath,
        [Parameter(Mandatory = $true)][datetime]$Since,
        [int]$TimeoutSeconds = 90
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $row = Get-LatestWorkerStartAudit -AuditPath $AuditPath -Since $Since
        if ($null -ne $row -and $row.Pid -gt 0) { return $row }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    return $null
}

function Test-LiveBrowserWorker {
    <#
    .SYNOPSIS
        Pure check of a live worker against the staged release. Returns problem strings;
        empty means the live process is proven. -Processes is injectable (Win32_Process
        shaped: ProcessId, ExecutablePath, CommandLine, CreationDate) for tests.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Audit,
        [Parameter(Mandatory = $true)]$Expected,
        [Parameter(Mandatory = $true)][string]$BrowserRoot,
        [Parameter(Mandatory = $true)][string]$BrowserDataDir,
        [Parameter(Mandatory = $true)][datetime]$DeployStartedAt,
        [AllowEmptyCollection()][int[]]$PreviousPids = @(),
        [object[]]$Processes = $null
    )
    if ($null -eq $Processes) { $Processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue) }
    $problems = @()
    if ($Audit.WorkerVersion -ne $Expected.Version) { $problems += "the companion started worker '$($Audit.WorkerVersion)', the staged release is '$($Expected.Version)'" }
    $venv = Join-Path $BrowserRoot ".venv"
    if ([string]::IsNullOrWhiteSpace($Audit.Module) -or $Audit.Module -eq "-") { $problems += "the audit row carries no module origin (a companion or worker older than release 0.3.0)" }
    elseif (-not (Test-PathInsideDirectory -Path $Audit.Module -Directory $venv)) { $problems += "the live worker executes '$($Audit.Module)', not a module inside '$venv'" }
    $process = $Processes | Where-Object { [int]$_.ProcessId -eq [int]$Audit.Pid } | Select-Object -First 1
    if ($null -eq $process) {
        $problems += "worker pid $($Audit.Pid) from the audit is not running"
    }
    else {
        $expectedExe = Join-Path $BrowserRoot ".venv\Scripts\python.exe"
        if (-not [string]::Equals([string]$process.ExecutablePath, $expectedExe, [System.StringComparison]::OrdinalIgnoreCase)) {
            $problems += "worker pid $($Audit.Pid) runs '$($process.ExecutablePath)', not '$expectedExe'"
        }
        if (@(Select-BrowserWorkerProcess -Processes @($process) -DataDir $BrowserDataDir).Count -eq 0) {
            $problems += "worker pid $($Audit.Pid) command line does not name --data-dir $BrowserDataDir"
        }
        $created = $null
        try { $created = [datetime]$process.CreationDate } catch { $created = $null }
        if ($null -eq $created) { $problems += "worker pid $($Audit.Pid) has no readable creation time" }
        elseif ($created -lt $DeployStartedAt.AddSeconds(-1)) { $problems += "worker pid $($Audit.Pid) was created $($created.ToString('o')), before this deployment ($($DeployStartedAt.ToString('o')))" }
    }
    foreach ($old in @($PreviousPids)) {
        if ($Processes | Where-Object { [int]$_.ProcessId -eq $old }) { $problems += "pre-swap worker pid $old is still alive" }
    }
    return @($problems)
}


# --------------------------------------------------------------------------- #
# Deployment lifecycle separation (owner decision, 2026-09-04)
#
# Normal execution must NOT run the installer: no staging, no swap, no service restart.
# A deployment happens only when the source/runtime release actually changed, the installed
# contract is incompatible, the owner asks for it, or a new release is being qualified.
# Test-AgentReleaseCurrent answers "is a deployment needed?" from files alone - it starts no
# process, touches no service and needs no elevation.
# --------------------------------------------------------------------------- #

function Test-AgentReleaseCurrent {
    <#
    .SYNOPSIS
        Compare this checkout's browser-worker release with the INSTALLED tree (its source
        copy and the non-editable copy inside its venv, which is what actually executes).
    .OUTPUTS
        Current            - $true when a deployment would change nothing
        ContractCompatible - $true when the installed contracts satisfy the checkout's
        Reasons            - why not current (empty when current)
        Expected/Installed - the two release objects (Installed is $null when nothing is installed)
        InstalledVenvDigest- the package digest of the copy that really runs
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$CheckoutBrowserSource,
        [string]$InstalledBrowserRoot = (Join-Path $env:ProgramFiles "PagentOS\agent\browser")
    )

    $expected = Get-ExpectedWorkerRelease -BrowserSource $CheckoutBrowserSource
    $reasons = @()
    $installed = $null
    $venvDigest = $null

    if (-not (Test-Path -LiteralPath (Join-Path $InstalledBrowserRoot "browser_agent\worker.py"))) {
        return [pscustomobject]@{
            Current = $false; ContractCompatible = $false
            Reasons = @("no browser worker is installed under $InstalledBrowserRoot")
            Expected = $expected; Installed = $null; InstalledVenvDigest = $null
            InstalledRoot = $InstalledBrowserRoot
        }
    }

    $installed = Get-ExpectedWorkerRelease -BrowserSource $InstalledBrowserRoot
    if ($installed.Version -ne $expected.Version) {
        $reasons += "installed worker release $($installed.Version), checkout $($expected.Version)"
    }
    if ($installed.PackageSha256 -ne $expected.PackageSha256) {
        $reasons += "installed source package digest $($installed.PackageSha256.Substring(0,12)), checkout $($expected.PackageSha256.Substring(0,12))"
    }

    $site = Get-SitePackagesBrowserAgentDir -BrowserRoot $InstalledBrowserRoot
    if (-not (Test-Path -LiteralPath $site)) {
        $reasons += "the installed venv has no browser_agent package ($site)"
    }
    else {
        $venvDigest = Get-BrowserPackageDigest -PackageDir $site
        if ($venvDigest -ne $expected.PackageSha256) {
            $reasons += "the copy that actually runs (installed venv) has digest $($venvDigest.Substring(0,12)), checkout $($expected.PackageSha256.Substring(0,12))"
        }
    }

    # Contract compatibility is the narrower question: can the installed worker still serve
    # this checkout's callers? (A newer patch release with the same contracts can.)
    $contractCompatible = $true
    foreach ($name in $expected.Contracts.Keys) {
        $live = if ($installed.Contracts.ContainsKey($name)) { $installed.Contracts[$name] } else { 0 }
        if ($live -lt $expected.Contracts[$name]) {
            $contractCompatible = $false
            $reasons += "installed contract $name is $live, this checkout needs $($expected.Contracts[$name])"
        }
    }

    return [pscustomobject]@{
        Current = (@($reasons).Count -eq 0)
        ContractCompatible = $contractCompatible
        Reasons = @($reasons)
        Expected = $expected
        Installed = $installed
        InstalledVenvDigest = $venvDigest
        InstalledRoot = $InstalledBrowserRoot
    }
}

function Write-AgentReleaseStatus {
    <#  One or two lines the owner can read: what is installed and whether it matches.  #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Status)
    $installedVersion = if ($Status.Installed) { $Status.Installed.Version } else { "(none)" }
    Write-Host "      release: checkout $($Status.Expected.Version), installed $installedVersion, package $(if ($Status.InstalledVenvDigest) { $Status.InstalledVenvDigest.Substring(0,12) } else { '-' }) vs $($Status.Expected.PackageSha256.Substring(0,12))"
    if ($Status.Current) {
        Write-Host "      the installed worker IS this release: no deployment, no staging/swap, no service restart" -ForegroundColor Green
    }
    else {
        foreach ($reason in $Status.Reasons) { Write-Host "      release difference: $reason" -ForegroundColor Yellow }
    }
}
