<#
.SYNOPSIS
    Evidence for an agent install: which commit was published, which bytes were built,
    staged and installed, which executables the service/task actually point at, and
    whether the installed service binary supports M13 (the `capabilities` verb and the
    browser family).

.DESCRIPTION
    Written after a real owner run in which the installer printed "installed:" while the
    live tree still held the previous release. Nothing here trusts a printed path: hashes
    are computed from the files, the service path is read back from the SCM, the companion
    path from the registered task, the worker path from the companion's own configuration,
    and the capability manifest from the INSTALLED executable answering its own verb.
#>

Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "NativeProcess.ps1")

# The artifacts whose bytes must be identical from build to install.
$script:EvidenceArtifacts = @(
    @{ Component = "service";   File = "PagentOS.DeviceService.dll" },
    @{ Component = "service";   File = "PagentOS.Agent.Core.dll" },
    @{ Component = "companion"; File = "PagentOS.SessionCompanion.dll" },
    @{ Component = "companion"; File = "PagentOS.Agent.Core.dll" }
)

# BROWSER_CAPABILITIES.md §1: the family marker plus the 24 operations.
$script:BrowserFamilyMarker = "browser.chrome"
$script:BrowserOperations = @(
    "browser.session_open", "browser.session_close", "browser.worker_status",
    "browser.navigate", "browser.back", "browser.forward",
    "browser.tab_list", "browser.tab_new", "browser.tab_close", "browser.tab_select",
    "browser.inspect", "browser.find", "browser.click", "browser.fill", "browser.select_option",
    "browser.set_checked", "browser.scroll", "browser.wait", "browser.extract", "browser.snapshot",
    "browser.screenshot", "browser.download", "browser.search", "browser.fetch_evidence"
)

function Get-RepoHead {
    <#
    .SYNOPSIS
        The commit the repository checkout is at, read from .git directly (git.exe is not
        assumed on an elevated PATH). Returns "<sha> (<ref>)" or "unknown (<reason>)".
    #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot)
    $gitDir = Join-Path $RepoRoot ".git"
    if (Test-Path -LiteralPath $gitDir -PathType Leaf) {
        # A worktree: ".git" is a file pointing at the real gitdir.
        $pointer = ([System.IO.File]::ReadAllText($gitDir)).Trim()
        if ($pointer -match '^gitdir:\s*(.+)$') { $gitDir = $Matches[1].Trim() }
    }
    $headFile = Join-Path $gitDir "HEAD"
    if (-not (Test-Path -LiteralPath $headFile)) { return "unknown (no .git/HEAD under $RepoRoot)" }
    $head = ([System.IO.File]::ReadAllText($headFile)).Trim()
    if ($head -match '^[0-9a-f]{40}$') { return "$head (detached)" }
    if ($head -notmatch '^ref:\s*(.+)$') { return "unknown (unreadable HEAD)" }
    $ref = $Matches[1].Trim()
    # A worktree's HEAD refs live in the common dir.
    $refRoots = @($gitDir)
    $commonFile = Join-Path $gitDir "commondir"
    if (Test-Path -LiteralPath $commonFile) {
        $common = ([System.IO.File]::ReadAllText($commonFile)).Trim()
        if (-not [System.IO.Path]::IsPathRooted($common)) { $common = Join-Path $gitDir $common }
        $refRoots += $common
    }
    foreach ($root in $refRoots) {
        $refFile = Join-Path $root ($ref -replace '/', '\')
        if (Test-Path -LiteralPath $refFile) {
            return "$(([System.IO.File]::ReadAllText($refFile)).Trim()) ($ref)"
        }
        $packed = Join-Path $root "packed-refs"
        if (Test-Path -LiteralPath $packed) {
            foreach ($line in [System.IO.File]::ReadAllLines($packed)) {
                if ($line -match "^([0-9a-f]{40}) $([regex]::Escape($ref))$") { return "$($Matches[1]) ($ref, packed)" }
            }
        }
    }
    return "unknown ($ref not resolvable)"
}

function Get-ArtifactHash {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return "MISSING" }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-ArtifactHashes {
    <#  Hashes of the evidence artifacts under a root that holds <component>\<file>.  #>
    param([Parameter(Mandatory = $true)][string]$Root)
    $result = [ordered]@{}
    foreach ($artifact in $script:EvidenceArtifacts) {
        $key = "$($artifact.Component)\$($artifact.File)"
        $result[$key] = Get-ArtifactHash -Path (Join-Path (Join-Path $Root $artifact.Component) $artifact.File)
    }
    return $result
}

function Compare-ArtifactHashes {
    <#  Every key present in Expected must match Actual; returns the mismatching keys.  #>
    param($Expected, $Actual)
    $mismatch = @()
    foreach ($key in $Expected.Keys) {
        $left = [string]$Expected[$key]
        $right = if ($Actual.Contains($key)) { [string]$Actual[$key] } else { "MISSING" }
        if ($left -ne $right) { $mismatch += "$key expected $left got $right" }
    }
    return @($mismatch)
}

function Get-InstalledAgentManifest {
    <#
    .SYNOPSIS
        Ask the INSTALLED service executable for its capability manifest (`capabilities`
        verb, M13). Returns Ok/Capabilities/BrowserEnabled/ExitCode/StdErr; a binary that
        predates the verb answers with usage on stderr and exit 2.
    #>
    param([Parameter(Mandatory = $true)][string]$ServiceExe, [int]$TimeoutSeconds = 30)
    if (-not (Test-Path -LiteralPath $ServiceExe)) {
        return [pscustomobject]@{ Ok = $false; Capabilities = @(); BrowserEnabled = $false; ExitCode = -1; StdErr = "missing: $ServiceExe" }
    }
    $result = Invoke-NativeProcess -FilePath $ServiceExe -Arguments @("capabilities") -TimeoutSeconds $TimeoutSeconds -WorkingDirectory (Split-Path -Parent $ServiceExe)
    $caps = @()
    $browserEnabled = $false
    $ok = $false
    if ($result.ExitCode -eq 0 -and $result.StdOut) {
        try {
            $doc = ($result.StdOut.Trim() -split "`n" | Where-Object { $_.Trim().StartsWith("{") } | Select-Object -Last 1) | ConvertFrom-Json
            $caps = @($doc.capabilities)
            $browserEnabled = [bool]$doc.browser_enabled
            $ok = $true
        }
        catch { $ok = $false }
    }
    return [pscustomobject]@{
        Ok             = $ok
        Capabilities   = @($caps)
        BrowserEnabled = $browserEnabled
        ExitCode       = $result.ExitCode
        StdErr         = [string]$result.StdErr
    }
}

function Assert-InstalledAgentSupportsM13 {
    <#
    .SYNOPSIS
        Fail loudly unless the installed service answers the capabilities verb and, when the
        browser family was provisioned, advertises browser.chrome plus all 24 operations.
    #>
    param(
        [Parameter(Mandatory = $true)]$Manifest,
        [Parameter(Mandatory = $true)][bool]$ExpectBrowser
    )
    if (-not $Manifest.Ok) {
        throw "the installed PagentOS.DeviceService does not answer the 'capabilities' verb (exit $($Manifest.ExitCode)); the binary in the live tree predates M13 - the deployment did not take. stderr: $($Manifest.StdErr)"
    }
    $caps = @($Manifest.Capabilities)
    if ($caps -notcontains "desktop.open_application") {
        throw "the installed service manifest lacks desktop.open_application: [$($caps -join ', ')]"
    }
    if ($ExpectBrowser) {
        $missing = @()
        foreach ($required in @($script:BrowserFamilyMarker) + $script:BrowserOperations) {
            if ($caps -notcontains $required) { $missing += $required }
        }
        if (@($missing).Count -gt 0) {
            throw "the installed service was expected to advertise the browser family but lacks: $($missing -join ', ') (BrowserEnabled=$($Manifest.BrowserEnabled))"
        }
        if (-not $Manifest.BrowserEnabled) {
            throw "the installed service advertises browser operations but reports BrowserEnabled=false"
        }
    }
    elseif ($caps -contains $script:BrowserFamilyMarker) {
        throw "the installed service advertises browser.chrome although the browser worker was not provisioned (-SkipBrowser)"
    }
}

function Get-RegisteredExecutablePaths {
    <#  What the SCM and the logon task will actually run - read back, not assumed.  #>
    param([Parameter(Mandatory = $true)][string]$ServiceName, [string]$CompanionTaskName = "PagentOS Session Companion")
    $servicePath = "unregistered"
    $cim = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
    if ($cim -and $cim.PathName) {
        $servicePath = $cim.PathName
        if ($servicePath -match '^"([^"]+)"') { $servicePath = $Matches[1] }
    }
    $companionPath = "unregistered"
    $task = Get-ScheduledTask -TaskName $CompanionTaskName -ErrorAction SilentlyContinue
    if ($task -and $task.Actions -and $task.Actions[0].Execute) { $companionPath = $task.Actions[0].Execute }
    return [pscustomobject]@{ Service = $servicePath; Companion = $companionPath }
}

function Get-RunningAgentImages {
    <#  The image paths of the service and companion processes now running (or "not running").  #>
    param([Parameter(Mandatory = $true)][string]$ServiceName)
    $servicePath = "not running"
    $cim = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
    if ($cim -and $cim.ProcessId -gt 0) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($cim.ProcessId)" -ErrorAction SilentlyContinue
        if ($proc -and $proc.ExecutablePath) { $servicePath = $proc.ExecutablePath }
    }
    $companionPath = "not running"
    $companion = Get-CimInstance Win32_Process -Filter "Name='PagentOS.SessionCompanion.exe'" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($companion -and $companion.ExecutablePath) { $companionPath = $companion.ExecutablePath }
    return [pscustomobject]@{ Service = $servicePath; Companion = $companionPath }
}

function Write-InstallEvidence {
    param([Parameter(Mandatory = $true)]$Evidence)
    Write-Host ""
    Write-Host "=== install evidence ===" -ForegroundColor Cyan
    Write-Host "repo HEAD                       $($Evidence.RepoHead)"
    foreach ($key in $Evidence.SourceHashes.Keys) {
        Write-Host ("source artifact hash            {0,-40} {1}" -f $key, $Evidence.SourceHashes[$key])
    }
    foreach ($key in $Evidence.StagedHashes.Keys) {
        Write-Host ("staged artifact hash            {0,-40} {1}" -f $key, $Evidence.StagedHashes[$key])
    }
    foreach ($key in $Evidence.InstalledHashes.Keys) {
        Write-Host ("installed artifact hash         {0,-40} {1}" -f $key, $Evidence.InstalledHashes[$key])
    }
    Write-Host "service executable path         $($Evidence.ServiceExe) (SCM: $($Evidence.RegisteredService); running: $($Evidence.RunningService))"
    Write-Host "companion executable path       $($Evidence.CompanionExe) (task: $($Evidence.RegisteredCompanion); running: $($Evidence.RunningCompanion))"
    Write-Host "browser worker path             $($Evidence.BrowserWorker)"
    Write-Host "installed capability manifest   $($Evidence.CapabilitySummary)"
    Write-Host "deployment journal              $($Evidence.Journal)"
    Write-Host "install log                     $($Evidence.LogPath)"
}
