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
    $manifestNames = @($ServiceManifest.PSObject.Properties.Name)
    $identity = [ordered]@{}
    # SoftwareVersion / Component / AssemblyVersion / CapabilityManifestVersion, each read
    # defensively: the staged binary always has them, an older one does not, and this must
    # describe both truthfully rather than throw under StrictMode.
    foreach ($field in @("SoftwareVersion", "Component", "AssemblyVersion", "CapabilityManifestVersion", "BuildId", "SourceRevision")) {
        $identity[$field] = if (($manifestNames -contains $field) -and $ServiceManifest.$field) { [string]$ServiceManifest.$field } else { "" }
    }
    $version = $identity["SoftwareVersion"]
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
            # A MAP, so @($hashes).Count would be 1 for a tree of any size, not the file
            # count. Counting the keys is both the honest read and the one the installer's
            # .Count lint accepts (scripts\tests\installer-strictmode.tests.ps1).
            file_count = @($hashes.Keys).Count
            files      = $hashes
        }
    }
    return [ordered]@{
        schema           = $script:CandidateManifestSchema
        created_at       = (Get-Date).ToUniversalTime().ToString("o")
        # The candidate's identity, from the staged binary's own `capabilities` verb plus the
        # checkout it was published from. Together with `capabilities` this is the whole
        # answer to "what is this candidate": component, software version, build identity,
        # capability-manifest fingerprint (2026-09-08 incident).
        repo_head        = $RepoHead
        software_version = $version
        component        = $identity["Component"]
        assembly_version = $identity["AssemblyVersion"]
        capability_manifest_version = $identity["CapabilityManifestVersion"]
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
    .PARAMETER RequireIdentity
        Also require the full candidate identity (component, assembly version agreeing with
        the announced version, capability-manifest fingerprint). The installer always passes
        it — it builds the candidate from this checkout, so a candidate that cannot name
        itself is a build problem, not an old device.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Manifest,
        [Parameter(Mandatory = $true)][string]$StagingRoot,
        [string[]]$RequireCapabilities = @(),
        [switch]$RequireIdentity
    )
    $reasons = @()
    $version = [string](Get-ManifestMember $Manifest "software_version")
    if (-not $version) { $reasons += "the candidate names no software version" }
    elseif ($version -notmatch '^\d+\.\d+\.\d+') { $reasons += "the candidate's software version '$version' is not a version" }
    # ONE canonical version identity (2026-09-08): the version the candidate will ANNOUNCE
    # and the version its binary was STAMPED with must be the same number. Two of them is
    # how "which version is actually running" stops being answerable.
    $assemblyVersion = [string](Get-ManifestMember $Manifest "assembly_version")
    if ($RequireIdentity) {
        if (-not $assemblyVersion) { $reasons += "the candidate carries no assembly version (a binary older than the identity contract cannot be a candidate)" }
        elseif ($version -and $assemblyVersion -ne $version) { $reasons += "the candidate would announce $version but its binary is stamped $assemblyVersion; there must be one version identity" }
        if (-not (Get-ManifestMember $Manifest "component")) { $reasons += "the candidate does not name which component it is" }
        if (-not (Get-ManifestMember $Manifest "capability_manifest_version")) { $reasons += "the candidate carries no capability manifest fingerprint" }
    }
    $capabilities = @(Get-ManifestMember $Manifest "capabilities" | ForEach-Object { [string]$_ })
    if (@($capabilities).Count -eq 0) { $reasons += "the candidate advertises no capability" }
    foreach ($required in $RequireCapabilities) {
        if ($capabilities -notcontains $required) { $reasons += "the candidate does not advertise $required" }
    }
    $components = Get-ManifestMember $Manifest "components"
    if ($null -eq $components) {
        $reasons += "the manifest lists no component"
        return [pscustomobject]@{
            Ok = $false; Reasons = @($reasons); Version = $version; Capabilities = $capabilities
            Component = [string](Get-ManifestMember $Manifest "component")
            AssemblyVersion = $assemblyVersion
            CapabilityManifestVersion = [string](Get-ManifestMember $Manifest "capability_manifest_version")
        }
    }
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
        # Keys again, not @($expected): a map never enumerates, so the list remedy would make
        # this test read `1 -eq 0` and a component listing no file would qualify silently.
        if (@($expected.Keys).Count -eq 0) { $reasons += "component '$name' lists no file"; continue }
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
        Component                 = [string](Get-ManifestMember $Manifest "component")
        AssemblyVersion           = $assemblyVersion
        CapabilityManifestVersion = [string](Get-ManifestMember $Manifest "capability_manifest_version")
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
    <#
    .SYNOPSIS
        The real GET /v1/devices, as the script block Test-AgentHeartbeatOnCore expects -
        carrying its dependency instead of hoping to find it.

    .DESCRIPTION
        The owner's 2026-09-09 install rolled back after 90.6 s with thirty-one identical
        "The term 'Invoke-JsonUtf8' is not recognized" errors, and the cause is here.

        `.GetNewClosure()` binds a script block to a NEW dynamic module linked to the GLOBAL
        session state. A function dot-sourced into a SCRIPT's scope - which is where
        install-device-service.ps1 puts every one of these libraries - is not in that path,
        so the fetcher could not see Invoke-JsonUtf8 even though the function that built it
        could, three lines earlier.

        It looked fine for two days because of how the callers were started:

            powershell -File script.ps1     the script IS the top-level scope -> resolves
            .\script.ps1  /  & script.ps1   the script gets a child scope     -> DOES NOT

        Every test harness in scripts/tests runs with -File, and a developer shell that had
        dot-sourced HttpJson.ps1 at the prompt has the function globally. The owner typed
        `.\scripts\install-device-service.ps1`, which is the one form nothing exercised.

        So the dependency is now RESOLVED AND CARRIED at construction time: $json holds the
        FunctionInfo, `& $json` runs the function in its own defining session state (its
        helpers - Read-AllBytes, ConvertFrom-Utf8Json - resolve with it), and the closure
        resolves no command by name at all.

        Resolution failing is also moved to the right MOMENT. The fetcher is built before
        the swap, so a missing dependency now throws while the previous release is still
        live and nothing has moved - instead of being discovered inside the post-swap health
        loop, where the only remaining outcome is a rollback of a candidate that was fine.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$BaseUrl, [Parameter(Mandatory = $true)][string]$Token)
    $uri = "$($BaseUrl.TrimEnd('/'))/v1/devices"
    $headers = @{ Authorization = "Bearer $Token" }
    $json = Get-Command -Name "Invoke-JsonUtf8" -CommandType Function -ErrorAction SilentlyContinue
    if (-not $json) {
        throw "Invoke-JsonUtf8 is not available to build the Cloud Core device fetcher; scripts\lib\HttpJson.ps1 must be dot-sourced by whatever loaded AgentUpdate.ps1. Refusing to build a verifier that would fail only after the swap."
    }
    return { & $json -Uri $uri -Headers $headers -TimeoutSec 20 }.GetNewClosure()
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

function Get-DeviceRowSoftwareVersion {
    <#
    .SYNOPSIS
        The canonical software version of a /v1/devices row, or "" when the row carries
        none at all.
    .DESCRIPTION
        2026-09-08 incident. This function read `software_version` off the device row; the
        row has never carried that key. The version was reachable only through
        `health.software_version`, so a LIVE, CORRECT 0.6.0 candidate was reported as
        announcing "" and the deployment engine rolled it back after 92.6 s.

        Cloud Core now emits the canonical key at the top of the row
        (`app.devices.types.DEVICE_IDENTITY_KEYS`). The nested location is still read as a
        FALLBACK, so this installer keeps working against a Cloud Core that has not been
        deployed yet - and both readings are explicit, so neither half can drift silently
        again (services/api/tests/unit/test_device_identity_contract.py binds them).

        "" means the row named no version anywhere. That is a contract fault, never a pass.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Row)
    $version = [string](Get-ManifestMember $Row "software_version")
    if ($version) { return $version }
    $health = Get-ManifestMember $Row "health"
    if ($null -ne $health) {
        $nested = [string](Get-ManifestMember $health "software_version")
        if ($nested) { return $nested }
    }
    return ""
}

function Get-DeviceRowBuildId {
    <#
    .SYNOPSIS
        The build identity of a /v1/devices row, or "" when the row carries none.
    .DESCRIPTION
        ADR-0118. `software_version` is the PRODUCT release and is meant to stay still across
        builds - the agent announced 0.6.0 while advertising the 85-capability M28 manifest,
        so two different builds were indistinguishable to the one comparison that decides
        whether a staged update took. `build_id` is derived on the device from its own
        assemblies and changes by construction.

        Read from the top of the row first and from `health` as a FALLBACK, the same two
        locations and the same order as Get-DeviceRowSoftwareVersion - that function exists
        because the 2026-09-08 incident was a reader looking in one place, and a new field
        with a one-place reader would be the same defect with a different name.

        "" means the row named no build identity anywhere. That is an agent older than
        ADR-0118, or one that could not read its own files. It is never a match.
    #>
    param([Parameter(Mandatory = $true)]$Row)
    $top = [string](Get-ManifestMember $Row "build_id")
    if ($top) { return $top }
    $health = Get-ManifestMember $Row "health"
    if ($null -ne $health) {
        $nested = [string](Get-ManifestMember $health "build_id")
        if ($nested) { return $nested }
    }
    return ""
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
        <#  ADR-0118. The candidate's own AgentInfo.BuildId. When supplied it is the
            DECIDING comparison: a product version that is meant to stay still cannot prove
            a swap took. Left empty the check degrades to the pre-ADR-0118 behaviour, so an
            older caller keeps working.  #>
        [string]$ExpectedBuildId = "",
        [string[]]$ExpectedCapabilities = @(),
        [int]$TimeoutSeconds = 90,
        [int]$PollSeconds = 3,
        [scriptblock]$Sleep = { param($s) Start-Sleep -Seconds $s },
        [scriptblock]$Now = { Get-Date }
    )
    $started = & $Now
    $observed = [ordered]@{ presence = $null; software_version = $null; build_id = $null; capability_count = 0; last_seen_at = $null; fetch_error = $null }
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
            # A broken VERIFIER is not a slow Cloud Core. On 2026-09-09 the fetcher could not
            # resolve Invoke-JsonUtf8, and this loop retried that thirty-one times over 90.6 s
            # before reporting "Cloud Core does not see the candidate" - a sentence about the
            # wrong component, arrived at after the maximum possible delay. A fault that no
            # amount of waiting can change is returned NOW, named for what it is. Still
            # Ok = $false, so the engine still rolls back: this makes the answer truthful and
            # fast, it does not make the gate softer.
            if ($_.Exception -is [System.Management.Automation.CommandNotFoundException]) {
                return [pscustomobject]@{
                    Ok            = $false
                    VerifierFault = $true
                    Reasons       = @("the Cloud Core verifier is broken, not the candidate: $($_.Exception.Message) This is a defect in the installer's own code - Cloud Core was never asked anything.")
                    Observed      = $observed
                    Waited        = [math]::Round(((& $Now) - $started).TotalSeconds, 1)
                    Attempts      = $attempt
                }
            }
            $reasons += "Cloud Core could not be read: $($_.Exception.Message)"
        }
        if ($null -eq $row -and -not $observed.fetch_error) {
            $reasons += "Cloud Core does not list device $DeviceId"
        }
        elseif ($null -ne $row) {
            $presence = [string](Get-ManifestMember $row "presence")
            if (-not $presence) { $presence = [string](Get-ManifestMember $row "status") }
            $version = Get-DeviceRowSoftwareVersion -Row $row
            $caps = @(Get-ManifestMember $row "capabilities" | ForEach-Object { [string]$_ })
            $observed.presence = $presence
            $observed.software_version = $version
            $observed.capability_count = @($caps).Count
            $observed.last_seen_at = [string](Get-ManifestMember $row "last_seen_at")
            if ($presence -ne "online") { $reasons += "the device is '$presence', not online" }
            if (-not $version) {
                # Never a pass, and never confused with "it announced the wrong version":
                # an absent version identity is a Cloud Core contract fault, and saying so
                # is what would have sent the 2026-09-08 investigation to the right file.
                $reasons += "Cloud Core's device row carries no software version at all (neither row.software_version nor row.health.software_version); the candidate is $ExpectedVersion - this is a Cloud Core contract fault, not a candidate fault"
            }
            elseif ($version -ne $ExpectedVersion) { $reasons += "the device reports software version '$version', the candidate is $ExpectedVersion" }
            # ADR-0118: the product version above is necessary and NOT sufficient. It is
            # meant to stay still across builds, so matching it proves only that the row is
            # not some other product release - never that the CANDIDATE is what is running.
            $rowBuild = Get-DeviceRowBuildId -Row $row
            $observed.build_id = $rowBuild
            if ($ExpectedBuildId) {
                if (-not $rowBuild) {
                    $reasons += "Cloud Core's device row carries no build identity (neither row.build_id nor row.health.build_id); the candidate is $ExpectedBuildId - an agent older than ADR-0118, or one that could not read its own assemblies. A swap cannot be proven from the product version alone"
                }
                elseif ($rowBuild -eq "unknown") {
                    $reasons += "the device announces build identity 'unknown' - it could not read its own assemblies, so it cannot prove which build it is; the candidate is $ExpectedBuildId"
                }
                elseif ($rowBuild -ne $ExpectedBuildId) {
                    $reasons += "the device reports build '$rowBuild', the candidate is '$ExpectedBuildId' - the swap has not taken effect on Cloud Core"
                }
            }
            $missing = @($ExpectedCapabilities | Where-Object { $caps -notcontains $_ })
            if (@($missing).Count -gt 0) { $reasons += "the device does not advertise: $($missing -join ', ')" }
        }
        $elapsed = ((& $Now) - $started).TotalSeconds
        if (@($reasons).Count -eq 0) {
            return [pscustomobject]@{ Ok = $true; VerifierFault = $false; Reasons = @(); Observed = $observed; Waited = [math]::Round($elapsed, 1); Attempts = $attempt }
        }
        if ($elapsed -ge $TimeoutSeconds) {
            return [pscustomobject]@{ Ok = $false; VerifierFault = $false; Reasons = @($reasons); Observed = $observed; Waited = [math]::Round($elapsed, 1); Attempts = $attempt }
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
    $identityLine = "candidate: component $(if ($Verdict.Component) { $Verdict.Component } else { 'UNNAMED' }), version $($Verdict.Version)"
    if ($Verdict.AssemblyVersion) { $identityLine += " (binary stamped $($Verdict.AssemblyVersion))" }
    if ($Verdict.CapabilityManifestVersion) { $identityLine += ", capability manifest $($Verdict.CapabilityManifestVersion)" }
    $identityLine += ", $(@($Verdict.Capabilities).Count) capabilities; $($parts -join '; ')"
    Write-Host $identityLine
    if ($Verdict.Ok) { Write-Host "candidate manifest verified file by file (nothing changed since staging)" -ForegroundColor Green }
    else { foreach ($reason in $Verdict.Reasons) { Write-Host "candidate: $reason" -ForegroundColor Red } }
}
