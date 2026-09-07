<#
.SYNOPSIS
    The Windows agent's staged update, made checkable (M18.4 gap 3, ADR-0081 addendum 3):
    a candidate manifest written at staging time and verified right before the swap, and the
    heartbeat / capability verification on Cloud Core that decides whether the candidate
    stays live or the journaled engine rolls it back.

.DESCRIPTION
    The installer already stages (publish into .staging), swaps through the journaled engine
    (stop by PID, same-volume renames, restart, health, commit / rollback) and proves the
    live browser worker. What was missing, and lives here:

      * New-AgentCandidateManifest / Test-AgentCandidateManifest - the candidate is a set of
        files with hashes, a software version and a capability manifest (the staged service
        binary's own `capabilities` verb), plus the browser worker's release identity. The
        manifest is written beside the staged trees and re-verified file by file immediately
        before the engine moves anything: a candidate that changed, lost a file or does not
        name a version is refused with the previous install untouched.
      * Test-AgentHeartbeatOnCore - after the engine started the candidate, Cloud Core must
        SEE it: the device online, reporting the candidate's software version and every
        capability the manifest promised. This runs inside the engine's TestHealth handler,
        so "Cloud Core does not see the candidate" is a rollback to the previous trees, not a
        warning. The fetch is injected (a script block), so the decision is unit-tested with
        fakes and the installer passes the real owner-session GET.
      * Get-OwnerSessionToken - the owner session for that read, from the DPAPI-stored owner
        credential (never printed, never logged) - or $null, in which case the verification
        is reported as skipped (READY_FOR_OWNER: store the credential, rerun).

    Windows PowerShell 5.1, StrictMode. No elevation needed by the functions themselves.
#>

Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "NativeProcess.ps1")
. (Join-Path $PSScriptRoot "BrowserRelease.ps1")
. (Join-Path $PSScriptRoot "InstallEvidence.ps1")
. (Join-Path $PSScriptRoot "HttpJson.ps1")
. (Join-Path $PSScriptRoot "SecretStore.ps1")

$script:CandidateManifestSchema = 1
$script:CandidateManifestFileName = "candidate-manifest.json"

function Get-TreeFileHashes {
    <#
    .SYNOPSIS
        Relative path (forward slashes, ordinal) -> sha256 for every file under a tree.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Root)
    if (-not (Test-Path -LiteralPath $Root)) { throw "tree does not exist: $Root" }
    $full = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $result = [ordered]@{}
    $files = @(Get-ChildItem -LiteralPath $full -File -Recurse -Force | Sort-Object -Property FullName -Culture "")
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($full.Length).TrimStart('\', '/') -replace '\\', '/'
        $result[$relative] = Get-FileSha256Hex -Path $file.FullName
    }
    return $result
}

function New-AgentCandidateManifest {
    <#
    .SYNOPSIS
        Describe the staged candidate: version + capabilities from the staged service binary,
        per-component file hashes, the browser worker's release identity.
    .PARAMETER ServiceManifest
        The staged service's own answer to its `capabilities` verb (Get-InstalledAgentManifest
        on the STAGED exe): Ok/Capabilities/BrowserEnabled/SoftwareVersion.
    .PARAMETER BrowserRelease
        Get-ExpectedWorkerRelease of the staged browser tree, or $null when not provisioned.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$StagingRoot,
        [Parameter(Mandatory = $true)][string[]]$Components,
        [Parameter(Mandatory = $true)]$ServiceManifest,
        $BrowserRelease = $null,
        [string]$RepoHead = ""
    )
    if (-not $ServiceManifest.Ok) {
        throw "the staged service does not answer its capabilities verb (exit $($ServiceManifest.ExitCode)); refusing to describe a candidate that cannot describe itself. stderr: $($ServiceManifest.StdErr)"
    }
    $version = ""
    if ($ServiceManifest.PSObject.Properties.Name -contains "SoftwareVersion" -and $ServiceManifest.SoftwareVersion) {
        $version = [string]$ServiceManifest.SoftwareVersion
    }
    # PowerShell variables are case-insensitive: a local `$components` here would BE the
    # `$Components` parameter (the first run of the tests iterated the table's own name).
    $componentTable = [ordered]@{}
    foreach ($component in $Components) {
        $tree = Join-Path $StagingRoot $component
        if ($component -eq "browser") {
            if ($null -eq $BrowserRelease) { throw "component 'browser' is staged but no browser release identity was given" }
            $componentTable[$component] = [ordered]@{
                worker_version = [string]$BrowserRelease.Version
                package_sha256 = [string]$BrowserRelease.PackageSha256
            }
            continue
        }
        $hashes = Get-TreeFileHashes -Root $tree
        $componentTable[$component] = [ordered]@{
            file_count = $hashes.Count
            files      = $hashes
        }
    }
    return [ordered]@{
        schema           = $script:CandidateManifestSchema
        created_at       = (Get-Date).ToUniversalTime().ToString("o")
        repo_head        = $RepoHead
        software_version = $version
        browser_enabled  = [bool]$ServiceManifest.BrowserEnabled
        capabilities     = @($ServiceManifest.Capabilities | ForEach-Object { [string]$_ })
        components       = $componentTable
    }
}

function Get-CandidateManifestPath {
    param([Parameter(Mandatory = $true)][string]$StagingRoot)
    return Join-Path $StagingRoot $script:CandidateManifestFileName
}

function Write-AgentCandidateManifest {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Manifest, [Parameter(Mandatory = $true)][string]$Path)
    $json = $Manifest | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($Path, $json + "`n", [Text.UTF8Encoding]::new($false))
    return $Path
}

function Read-AgentCandidateManifest {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { throw "no candidate manifest at $Path" }
    return (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json)
}

function ConvertTo-StringMap {
    # A manifest read back from JSON carries PSCustomObjects; a fresh one carries ordered
    # hashtables. Both become a plain name -> value table here.
    param($Value)
    $map = @{}
    if ($null -eq $Value) { return $map }
    if ($Value -is [System.Collections.IDictionary]) {
        foreach ($key in $Value.Keys) { $map[[string]$key] = [string]$Value[$key] }
        return $map
    }
    foreach ($property in $Value.PSObject.Properties) { $map[[string]$property.Name] = [string]$property.Value }
    return $map
}

function Get-ManifestMember {
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    if ($Object -is [System.Collections.IDictionary]) { if ($Object.Contains($Name)) { return $Object[$Name] } else { return $null } }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Test-AgentCandidateManifest {
    <#
    .SYNOPSIS
        Re-verify the staged candidate against its manifest, file by file, right before the
        swap. Returns Ok + Reasons (every reason is one sentence naming the file or field).
    .PARAMETER RequireCapabilities
        Names the candidate must advertise (the installer passes the desktop marker and, when
        the browser was provisioned, the browser family).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Manifest,
        [Parameter(Mandatory = $true)][string]$StagingRoot,
        [string[]]$RequireCapabilities = @()
    )
    $reasons = @()
    $version = [string](Get-ManifestMember $Manifest "software_version")
    if (-not $version) { $reasons += "the candidate names no software version" }
    elseif ($version -notmatch '^\d+\.\d+\.\d+') { $reasons += "the candidate's software version '$version' is not a version" }
    $capabilities = @(Get-ManifestMember $Manifest "capabilities" | ForEach-Object { [string]$_ })
    if ($capabilities.Count -eq 0) { $reasons += "the candidate advertises no capability" }
    foreach ($required in $RequireCapabilities) {
        if ($capabilities -notcontains $required) { $reasons += "the candidate does not advertise $required" }
    }
    $components = Get-ManifestMember $Manifest "components"
    if ($null -eq $components) { $reasons += "the manifest lists no component"; return [pscustomobject]@{ Ok = $false; Reasons = @($reasons); Version = $version; Capabilities = $capabilities } }
    $componentNames = @(if ($components -is [System.Collections.IDictionary]) { $components.Keys } else { $components.PSObject.Properties.Name })
    foreach ($name in $componentNames) {
        $entry = Get-ManifestMember $components $name
        $tree = Join-Path $StagingRoot $name
        if (-not (Test-Path -LiteralPath $tree)) { $reasons += "staged component '$name' is missing at $tree"; continue }
        if ($name -eq "browser") {
            $expectedDigest = [string](Get-ManifestMember $entry "package_sha256")
            $package = Join-Path $tree "browser_agent"
            if (-not (Test-Path -LiteralPath $package)) { $reasons += "the staged browser tree has no browser_agent package"; continue }
            $actualDigest = Get-BrowserPackageDigest -PackageDir $package
            if ($actualDigest -ne $expectedDigest) { $reasons += "the staged browser package digest is $($actualDigest.Substring(0,12)), the manifest says $($expectedDigest.Substring(0,[Math]::Min(12,$expectedDigest.Length)))" }
            continue
        }
        $expected = ConvertTo-StringMap (Get-ManifestMember $entry "files")
        if ($expected.Count -eq 0) { $reasons += "component '$name' lists no file"; continue }
        $actual = ConvertTo-StringMap (Get-TreeFileHashes -Root $tree)
        foreach ($key in ($expected.Keys | Sort-Object)) {
            if (-not $actual.ContainsKey($key)) { $reasons += "$name/$key is in the manifest but missing from staging" }
            elseif ($actual[$key] -ne $expected[$key]) { $reasons += "$name/$key changed since it was staged" }
        }
        foreach ($key in ($actual.Keys | Sort-Object)) {
            if (-not $expected.ContainsKey($key)) { $reasons += "$name/$key is in staging but not in the manifest" }
        }
    }
    return [pscustomobject]@{
        Ok           = (@($reasons).Count -eq 0)
        Reasons      = @($reasons)
        Version      = $version
        Capabilities = $capabilities
    }
}

function ConvertFrom-IdentityOutput {
    <#  The `identity` verb's one JSON document -> device_id (or $null).  #>
    param([AllowNull()][AllowEmptyString()][string]$StdOut)
    if (-not $StdOut) { return $null }
    $line = ($StdOut.Trim() -split "`n" | Where-Object { $_.Trim().StartsWith("{") } | Select-Object -Last 1)
    if (-not $line) { return $null }
    try { $doc = $line | ConvertFrom-Json } catch { return $null }
    $id = Get-ManifestMember $doc "device_id"
    if ($id) { return [string]$id } else { return $null }
}

function Get-AgentDeviceId {
    <#  Ask the INSTALLED service for its device id (identity verb; load-only, never mints).  #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$ServiceExe, [int]$TimeoutSeconds = 30)
    if (-not (Test-Path -LiteralPath $ServiceExe)) { return $null }
    $result = Invoke-NativeProcess -FilePath $ServiceExe -Arguments @("identity") -TimeoutSeconds $TimeoutSeconds -WorkingDirectory (Split-Path -Parent $ServiceExe)
    if ($result.ExitCode -ne 0) { return $null }
    return ConvertFrom-IdentityOutput -StdOut $result.StdOut
}

function Get-OwnerSessionToken {
    <#
    .SYNOPSIS
        An owner session from the DPAPI-stored credential, or $null when none is stored or
        Cloud Core refuses. The credential is never printed; the token is returned only.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$BaseUrl, [string]$Label = "install-device-service")
    $credential = $null
    try { $credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL" } catch { $credential = $null }
    if (-not $credential) { return $null }
    try {
        $body = @{ owner_credential = $credential; client_kind = "cli"; label = $Label } | ConvertTo-Json -Compress
        $issued = Invoke-JsonUtf8 -Method POST -Uri "$($BaseUrl.TrimEnd('/'))/v1/identity/sessions" -Body $body -TimeoutSec 30
        $token = [string](Get-ManifestMember $issued "token")
        if ($token) { return $token } else { return $null }
    }
    catch { return $null }
    finally { $credential = $null; $body = $null }
}

function New-CoreDeviceFetcher {
    <#  The real GET /v1/devices, as the script block Test-AgentHeartbeatOnCore expects.  #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$BaseUrl, [Parameter(Mandatory = $true)][string]$Token)
    $uri = "$($BaseUrl.TrimEnd('/'))/v1/devices"
    $headers = @{ Authorization = "Bearer $Token" }
    return { Invoke-JsonUtf8 -Uri $uri -Headers $headers -TimeoutSec 20 }.GetNewClosure()
}

function Get-DeviceRowFromListing {
    param($Listing, [string]$DeviceId)
    $rows = @()
    $devices = Get-ManifestMember $Listing "devices"
    if ($null -ne $devices) { $rows = @($devices) }
    foreach ($row in $rows) {
        if ($null -eq $row) { continue }
        if ([string](Get-ManifestMember $row "device_id") -eq $DeviceId) { return $row }
    }
    return $null
}

function Test-AgentHeartbeatOnCore {
    <#
    .SYNOPSIS
        Wait until Cloud Core lists the device ONLINE with the candidate's software version
        and every capability the manifest promised. Returns Ok/Reasons/Observed/Waited.
    .PARAMETER FetchDevices
        Script block returning the /v1/devices document (the real GET, or a fake in tests).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][scriptblock]$FetchDevices,
        [Parameter(Mandatory = $true)][string]$DeviceId,
        [Parameter(Mandatory = $true)][string]$ExpectedVersion,
        [string[]]$ExpectedCapabilities = @(),
        [int]$TimeoutSeconds = 90,
        [int]$PollSeconds = 3,
        [scriptblock]$Sleep = { param($s) Start-Sleep -Seconds $s },
        [scriptblock]$Now = { Get-Date }
    )
    $started = & $Now
    $observed = [ordered]@{ presence = $null; software_version = $null; capability_count = 0; last_seen_at = $null; fetch_error = $null }
    $reasons = @()
    $attempt = 0
    while ($true) {
        $attempt++
        $reasons = @()
        $row = $null
        try {
            $listing = & $FetchDevices
            $observed.fetch_error = $null
            $row = Get-DeviceRowFromListing -Listing $listing -DeviceId $DeviceId
        }
        catch {
            $observed.fetch_error = $_.Exception.Message
            $reasons += "Cloud Core could not be read: $($_.Exception.Message)"
        }
        if ($null -eq $row -and -not $observed.fetch_error) {
            $reasons += "Cloud Core does not list device $DeviceId"
        }
        elseif ($null -ne $row) {
            $presence = [string](Get-ManifestMember $row "presence")
            if (-not $presence) { $presence = [string](Get-ManifestMember $row "status") }
            $version = [string](Get-ManifestMember $row "software_version")
            $caps = @(Get-ManifestMember $row "capabilities" | ForEach-Object { [string]$_ })
            $observed.presence = $presence
            $observed.software_version = $version
            $observed.capability_count = $caps.Count
            $observed.last_seen_at = [string](Get-ManifestMember $row "last_seen_at")
            if ($presence -ne "online") { $reasons += "the device is '$presence', not online" }
            if ($version -ne $ExpectedVersion) { $reasons += "the device reports software version '$version', the candidate is $ExpectedVersion" }
            $missing = @($ExpectedCapabilities | Where-Object { $caps -notcontains $_ })
            if ($missing.Count -gt 0) { $reasons += "the device does not advertise: $($missing -join ', ')" }
        }
        $elapsed = ((& $Now) - $started).TotalSeconds
        if ($reasons.Count -eq 0) {
            return [pscustomobject]@{ Ok = $true; Reasons = @(); Observed = $observed; Waited = [math]::Round($elapsed, 1); Attempts = $attempt }
        }
        if ($elapsed -ge $TimeoutSeconds) {
            return [pscustomobject]@{ Ok = $false; Reasons = @($reasons); Observed = $observed; Waited = [math]::Round($elapsed, 1); Attempts = $attempt }
        }
        & $Sleep $PollSeconds
    }
}

function Write-CandidateSummary {
    param([Parameter(Mandatory = $true)]$Manifest, [Parameter(Mandatory = $true)]$Verdict)
    $components = Get-ManifestMember $Manifest "components"
    $parts = @()
    foreach ($name in @(if ($components -is [System.Collections.IDictionary]) { $components.Keys } else { $components.PSObject.Properties.Name })) {
        $entry = Get-ManifestMember $components $name
        if ($name -eq "browser") { $parts += "browser worker $(Get-ManifestMember $entry 'worker_version') package $(([string](Get-ManifestMember $entry 'package_sha256')).Substring(0,12))" }
        else { $parts += "$name $(Get-ManifestMember $entry 'file_count') files" }
    }
    Write-Host "candidate: version $($Verdict.Version), $(@($Verdict.Capabilities).Count) capabilities; $($parts -join '; ')"
    if ($Verdict.Ok) { Write-Host "candidate manifest verified file by file (nothing changed since staging)" -ForegroundColor Green }
    else { foreach ($reason in $Verdict.Reasons) { Write-Host "candidate: $reason" -ForegroundColor Red } }
}
