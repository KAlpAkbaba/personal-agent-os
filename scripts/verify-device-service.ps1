<#
.SYNOPSIS
    Qualifies an installed Device Service against the criteria in docs/QUALIFICATION.md
    Stage 1 and 2 that can only be proven once the service really runs as LocalSystem.

.DESCRIPTION
    The adversarial unit tests prove the identity *logic*. They cannot prove the topology:
    that the service is in Session 0 as LocalSystem, that the companion is in the owner's
    interactive session, and that the service admits it on the strength of the kernel's
    answer rather than because both halves happen to be the same user. That is what this
    script checks, on the owner's real machine, and it prints a verdict per criterion using
    the same PROVEN_REAL / NOT_YET_PROVEN vocabulary as the qualification matrix.

    Read-only: it starts nothing, installs nothing and changes no configuration.

.EXAMPLE
    .\scripts\verify-device-service.ps1
#>
[CmdletBinding()]
param(
    [string]$ServiceName = "PagentOSDeviceAgent",
    [string]$DataDir = (Join-Path $env:ProgramData "PagentOS\agent"),
    [string]$InstallRoot = (Join-Path $env:ProgramFiles "PagentOS\agent")
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "lib\NativeProcess.ps1")
. (Join-Path $PSScriptRoot "lib\InstallAcl.ps1")
. (Join-Path $PSScriptRoot "lib\BrowserProvision.ps1")
. (Join-Path $PSScriptRoot "lib\BrowserRelease.ps1")

$results = New-Object System.Collections.ArrayList

function Add-Result {
    param([string]$Id, [string]$Criterion, [string]$Status, [string]$Evidence)
    [void]$results.Add([pscustomobject]@{
        Id        = $Id
        Criterion = $Criterion
        Status    = $Status
        Evidence  = $Evidence
    })
}

# --- 2.1 service exists, runs as LocalSystem, starts automatically ---------------

$service = Get-CimInstance -ClassName Win32_Service -Filter "Name='$ServiceName'" -ErrorAction SilentlyContinue
if (-not $service) {
    Add-Result "2.1" "Service installed and running as LocalSystem" "NOT_YET_PROVEN" "no service named $ServiceName"
}
else {
    $account = $service.StartName
    $status = if ($service.State -eq "Running" -and $account -eq "LocalSystem" -and $service.StartMode -eq "Auto") { "PROVEN_REAL" } else { "NOT_YET_PROVEN" }
    Add-Result "2.1" "Service installed and running as LocalSystem" $status "state=$($service.State) account=$account start=$($service.StartMode) pid=$($service.ProcessId)"
}

# --- 1.11 service process really is in Session 0 --------------------------------

if ($service -and $service.ProcessId -gt 0) {
    $proc = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$($service.ProcessId)" -ErrorAction SilentlyContinue
    $sessionId = $proc.SessionId
    $status = if ($sessionId -eq 0) { "PROVEN_REAL" } else { "NOT_YET_PROVEN" }
    Add-Result "1.11" "Service process is in Session 0" $status "session=$sessionId"
}
else {
    Add-Result "1.11" "Service process is in Session 0" "NOT_YET_PROVEN" "service is not running"
}

# --- 2.2 companion is in the owner's interactive session ------------------------

$companion = Get-CimInstance -ClassName Win32_Process -Filter "Name='PagentOS.SessionCompanion.exe'" -ErrorAction SilentlyContinue
if (-not $companion) {
    Add-Result "2.2" "Companion runs in the owner's interactive session" "NOT_YET_PROVEN" "no companion process; sign out and back in, or start it once by hand"
}
else {
    $owner = Invoke-CimMethod -InputObject $companion -MethodName GetOwner -ErrorAction SilentlyContinue
    $companionSession = $companion.SessionId
    $status = if ($companionSession -ne 0) { "PROVEN_REAL" } else { "NOT_YET_PROVEN" }
    Add-Result "2.2" "Companion runs in the owner's interactive session" $status "session=$companionSession user=$($owner.Domain)\$($owner.User) path=$($companion.ExecutablePath)"
}

# --- 1.1 pipe DACL --------------------------------------------------------------
# Lifecycle facts, measured on this machine rather than assumed (scratch-pipe probes):
#   - while the companion is CONNECTED (the normal steady state), EVERY external open of a
#     single-instance pipe fails ERROR_PIPE_BUSY - including READ_CONTROL-only, including
#     the CreateFile inside Test-Path. The old probe read that 231 as "does not exist";
#   - against a LISTENING instance the same open CONNECTS, consuming the instance and
#     disrupting admission. So a runtime probe is either blind or invasive;
#   - namespace enumeration ([IO.Directory]::GetFiles("\\.\pipe\")) sees the pipe in both
#     states and never touches the channel.
# Therefore: presence via enumeration; the DACL from the `ipc_pipe_created` audit row, which
# the service writes from the REAL handle at creation - the one non-invasive observation
# point the pipe's life has. The name comes from the service's own configuration, never
# reconstructed.

$agentSettingsPath = Join-Path $InstallRoot "service\appsettings.json"
$expectedPipe = $null
$expectedOwnerSid = $null
if (Test-Path -LiteralPath $agentSettingsPath) {
    try {
        $agentSettings = Get-Content -LiteralPath $agentSettingsPath -Raw | ConvertFrom-Json
        $expectedPipe = $agentSettings.PipeName
        $expectedOwnerSid = $agentSettings.CompanionSid
    } catch { }
}
if (-not $expectedPipe) {
    $expectedPipe = "pagentos-companion-$(([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value)"
}
if (-not $expectedOwnerSid) {
    $expectedOwnerSid = ([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value
}

$pipeListed = @([System.IO.Directory]::GetFiles("\\.\pipe\") | Where-Object { $_ -match [regex]::Escape($expectedPipe) })
$pipeExists = (@($pipeListed).Count -ge 1)

# The audit lives in the machine-protected data directory: readable elevated, access-denied
# otherwise. Those are different findings and must not be reported identically.
$sddlRow = $null
$auditReadable = $false
$agentAudit = Join-Path $DataDir "audit\agent-audit.jsonl"
try {
    $auditLines = Get-Content -LiteralPath $agentAudit -Tail 500 -ErrorAction Stop
    $auditReadable = $true
    $sddlRow = $auditLines |
        ForEach-Object { try { $_ | ConvertFrom-Json } catch { $null } } |
        Where-Object { $_ -and $_.event -eq "ipc_pipe_created" -and $_.detail -match [regex]::Escape($expectedPipe) } |
        Select-Object -Last 1
}
catch [System.UnauthorizedAccessException] {
    # Correctly protected against this account; run elevated to read it.
}
catch {
    # Absent (or another read failure): $auditReadable stays false.
}

if (-not $pipeExists) {
    Add-Result "1.1" "Pipe DACL names the owner account explicitly" "NOT_YET_PROVEN" `
        "pipe '$expectedPipe' is not in the pipe namespace (service not running, or listening on another name)"
}
elseif ($sddlRow) {
    $sddl = ($sddlRow.detail -replace '^.*sddl=', '')
    $ownerNamed = ($sddl -match [regex]::Escape("(A;;") -and $sddl -match [regex]::Escape($expectedOwnerSid))
    $noBroadGrants = -not ($sddl -match "S-1-5-32-545|S-1-1-0|S-1-5-11")
    if ($ownerNamed -and $noBroadGrants) {
        Add-Result "1.1" "Pipe DACL names the owner account explicitly" "PROVEN_REAL" `
            "effective SDDL read from the live pipe HANDLE at creation and audited at $($sddlRow.ts): $sddl"
    }
    else {
        Add-Result "1.1" "Pipe DACL names the owner account explicitly" "NOT_YET_PROVEN" `
            "runtime SDDL does not match ADR-0028 (ownerNamed=$ownerNamed, broadGrants=$(-not $noBroadGrants)): $sddl"
    }
}
elseif (-not $auditReadable) {
    Add-Result "1.1" "Pipe DACL names the owner account explicitly" "NOT_YET_PROVEN" `
        "pipe '$expectedPipe' is live, but the agent audit at $agentAudit is not readable from this account (which is the intended protection). Run this verifier elevated."
}
else {
    Add-Result "1.1" "Pipe DACL names the owner account explicitly" "NOT_YET_PROVEN" `
        "pipe '$expectedPipe' is live, but no ipc_pipe_created audit row exists - the installed service binary predates the SDDL capture. The DACL cannot be read externally without disturbing the pipe (measured: busy=231 when connected, instance-consuming when listening), so this converts when the service binary is updated."
}

# --- 1.4-1.7 admission actually happened, per the service's own audit ------------

$auditPath = Join-Path $DataDir "audit\agent-audit.jsonl"
$auditAccessDenied = $false
$recent = @()
try {
    $recent = Get-Content $auditPath -Tail 400 -ErrorAction Stop | ForEach-Object {
        try { $_ | ConvertFrom-Json } catch { $null }
    } | Where-Object { $_ }
}
catch [System.UnauthorizedAccessException] { $auditAccessDenied = $true }
catch { }

if ($auditAccessDenied) {
    Add-Result "1.4-1.7" "Companion admitted on kernel-sourced identity" "NOT_YET_PROVEN" `
        "the agent audit is protected against this account (as intended); run this verifier elevated to read it"
}
elseif (@($recent).Count -gt 0) {

    $admitted = $recent | Where-Object { $_.event -eq "ipc_companion_admitted" } | Select-Object -Last 1
    if ($admitted) {
        Add-Result "1.4-1.7" "Companion admitted on kernel-sourced identity" "PROVEN_REAL" $admitted.detail
    }
    else {
        Add-Result "1.4-1.7" "Companion admitted on kernel-sourced identity" "NOT_YET_PROVEN" "no ipc_companion_admitted row in the last 400 audit entries"
    }

    $refused = $recent | Where-Object { $_.event -eq "ipc_peer_refused" }
    if ($refused) {
        Write-Host ""
        Write-Host "peers refused since this log was rotated (this is the interesting part):" -ForegroundColor Yellow
        $refused | Select-Object -Last 10 | ForEach-Object { Write-Host "  $($_.ts) $($_.status): $($_.detail)" }
    }
}
else {
    Add-Result "1.4-1.7" "Companion admitted on kernel-sourced identity" "NOT_YET_PROVEN" "no audit log at $auditPath"
}

# --- installed-tree security posture --------------------------------------------
# Re-derived from the ACLs on disk, independently of whatever the installer believes it
# applied. A previous installer version left 219 files with an empty DACL that denied even
# SYSTEM, and nothing checked — so this is the check that would have caught it.

$posture = Test-InstallAclPosture -Root $InstallRoot -SampleSize 200
if (-not $posture.Ok) {
    $summary = ($posture.Violations | Select-Object -First 3) -join " | "
    if (@($posture.Violations).Count -gt 3) { $summary += " (+$(@($posture.Violations).Count - 3) more)" }
    Add-Result "1.17" "Only SYSTEM and Administrators can write to the install tree" "NOT_YET_PROVEN" $summary
}
else {
    Add-Result "1.17" "Only SYSTEM and Administrators can write to the install tree" "PROVEN_REAL" `
        "$($posture.Checked) objects checked under $InstallRoot; no other principal holds write, and no empty DACLs"
}

$companionPin = Join-Path $InstallRoot "companion\PagentOS.SessionCompanion.exe"
if (Test-Path -LiteralPath $companionPin) {
    $pinReport = Get-AclReport -Path $companionPin
    $pinOk = $pinReport.Readable -and $pinReport.AceCount -gt 0
    Add-Result "1.7b" "The pinned companion binary is admin-protected and readable by SYSTEM" `
        ($(if ($pinOk) { "PROVEN_REAL" } else { "NOT_YET_PROVEN" })) `
        $(if ($pinOk) { "owner=$($pinReport.Owner) aces=$($pinReport.AceCount)" } else { "DACL unreadable or empty: pinning proves nothing if the file cannot be read" })
}
else {
    Add-Result "1.7b" "The pinned companion binary is admin-protected and readable by SYSTEM" "NOT_YET_PROVEN" "not installed at $companionPin"
}

# --- no inbound listener on the Windows machine ---------------------------------
# The device link is outbound-only by design; an inbound listener owned by the agent
# would be a silent architectural regression, so check rather than assume.

$agentPids = @()
if ($service -and $service.ProcessId) { $agentPids += $service.ProcessId }
if ($companion) { $agentPids += $companion.ProcessId }
if (@($agentPids).Count -eq 0) {
    # Nothing running means nothing was checked. "No listener found" would be true and
    # worthless here, and writing it up as PROVEN_REAL is exactly the kind of vacuous pass
    # this file exists to avoid.
    Add-Result "5.2" "No inbound listener owned by the agent" "NOT_YET_PROVEN" "no agent process was running, so nothing was checked"
}
else {
    $listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $agentPids -contains $_.OwningProcess }
    if ($listeners) {
        Add-Result "5.2" "No inbound listener owned by the agent" "NOT_YET_PROVEN" (($listeners | ForEach-Object { "$($_.LocalAddress):$($_.LocalPort)" }) -join ", ")
    }
    else {
        Add-Result "5.2" "No inbound listener owned by the agent" "PROVEN_REAL" "checked pids $($agentPids -join ', '): no listening TCP socket"
    }
}

# --- 6b browser worker (M13, BROWSER_CAPABILITIES.md §7) -------------------------
# Two facts, measured rather than read off the installer's output: the worker really
# starts from the hardened tree AS THIS (unelevated, owner) account and writes only to a
# throwaway data directory; and the manifest the service advertises agrees with what the
# worker says it can do. The service's `capabilities` verb reads only appsettings.json, so
# it runs unelevated too.

$companionSettingsPath = Join-Path $InstallRoot "companion\appsettings.json"
$workerCommand = $null
$workerChannel = "chrome"
if (Test-Path -LiteralPath $companionSettingsPath) {
    try {
        $companionSettings = Get-Content -LiteralPath $companionSettingsPath -Raw | ConvertFrom-Json
        if (Test-ObjectProperty -InputObject $companionSettings -Name "BrowserWorkerCommand") { $workerCommand = $companionSettings.BrowserWorkerCommand }
        if ((Test-ObjectProperty -InputObject $companionSettings -Name "BrowserChannel") -and $companionSettings.BrowserChannel) { $workerChannel = $companionSettings.BrowserChannel }
    } catch { }
}

$workerCheck = $null
if (-not $workerCommand) {
    Add-Result "6b.1" "Browser worker starts as the owner from the installed tree" "NOT_YET_PROVEN" `
        "the companion has no BrowserWorkerCommand (installed with -SkipBrowser, or before M13); rerun the installer to provision it"
}
elseif (-not (Test-Path -LiteralPath $workerCommand)) {
    Add-Result "6b.1" "Browser worker starts as the owner from the installed tree" "NOT_YET_PROVEN" "configured worker interpreter is missing: $workerCommand"
}
else {
    $probeData = Join-Path $env:TEMP "pagentos-browser-verify-$([guid]::NewGuid().ToString('N'))"
    try {
        $workerCheck = Invoke-BrowserWorkerSelfCheck -Python $workerCommand -Channel $workerChannel -DataDir $probeData -TimeoutSeconds 120
    }
    catch {
        $workerCheck = [pscustomobject]@{ Ok = $false; ExitCode = -1; Hello = $null; Capabilities = @(); StdOut = ""; StdErr = $_.Exception.Message }
    }
    finally {
        Remove-Item -LiteralPath $probeData -Recurse -Force -ErrorAction SilentlyContinue
    }

    if ($workerCheck.Ok) {
        $browserInfo = "-"
        if (Test-ObjectProperty -InputObject $workerCheck.Hello -Name "browser") {
            $browserInfo = "$($workerCheck.Hello.browser.channel) $($workerCheck.Hello.browser.version) available=$($workerCheck.Hello.browser.available)"
        }
        Add-Result "6b.1" "Browser worker starts as the owner from the installed tree" "PROVEN_REAL" `
            "self-check exit 0 as $([Security.Principal.WindowsIdentity]::GetCurrent().Name) from a neutral working directory: worker $($workerCheck.Hello.worker_version), browser $browserInfo, $(@($workerCheck.Capabilities).Count) capabilities"
    }
    else {
        $stderrTail = ""
        if ($workerCheck.StdErr) { $stderrTail = ((($workerCheck.StdErr.Trim() -split "`r?`n") | Select-Object -Last 3) -join " | ") }
        Add-Result "6b.1" "Browser worker starts as the owner from the installed tree" "NOT_YET_PROVEN" `
            "self-check exit $($workerCheck.ExitCode) ($workerCommand -m browser_agent.worker --self-check --channel $workerChannel): $stderrTail"
    }
}

$serviceExePath = Join-Path $InstallRoot "service\PagentOS.DeviceService.exe"
$advertised = $null
$browserEnabled = $null
# 2026-09-08: "which version is installed" must be answerable from this report alone, and
# with ONE number - the version the agent will ANNOUNCE and the stamp on its binary together.
$installedIdentity = "identity not answered (a binary older than the identity contract)"
if (Test-Path -LiteralPath $serviceExePath) {
    try {
        $capsResult = Invoke-NativeProcess -FilePath $serviceExePath -Arguments @("capabilities") -TimeoutSeconds 30 -WorkingDirectory (Split-Path -Parent $serviceExePath)
        if ($capsResult.ExitCode -eq 0 -and $capsResult.StdOut.Trim()) {
            $capsDoc = ConvertFrom-Json ($capsResult.StdOut.Trim() -split "`r?`n" | Select-Object -Last 1)
            $advertised = @($capsDoc.capabilities)
            $browserEnabled = [bool]$capsDoc.browser_enabled
            $capsNames = @($capsDoc.PSObject.Properties.Name)
            if ($capsNames -contains "software_version") {
                $component = if ($capsNames -contains "component") { [string]$capsDoc.component } else { "unnamed" }
                $stamp = if ($capsNames -contains "assembly_version") { [string]$capsDoc.assembly_version } else { "unstamped" }
                $fingerprint = if ($capsNames -contains "capability_manifest_version") { [string]$capsDoc.capability_manifest_version } else { "unfingerprinted" }
                $installedIdentity = "component $component version $([string]$capsDoc.software_version) (binary $stamp, capability manifest $fingerprint)"
            }
        }
    } catch { }
}

if ($null -eq $advertised) {
    Add-Result "6b.2" "Advertised capability manifest agrees with the worker" "NOT_YET_PROVEN" `
        "the installed service binary does not answer the 'capabilities' verb (predates M13, or not installed); reinstall to update it"
}
else {
    $hasFamily = ($advertised -contains "browser.chrome")
    $browserOps = @($advertised | Where-Object { $_ -like "browser.*" -and $_ -ne "browser.chrome" })
    $listing = "$installedIdentity; BrowserEnabled=$browserEnabled; advertised: $($advertised -join ', ')"
    if ($browserEnabled -ne $hasFamily) {
        Add-Result "6b.2" "Advertised capability manifest agrees with the worker" "NOT_YET_PROVEN" "family marker/BrowserEnabled disagree; $listing"
    }
    elseif (-not $browserEnabled) {
        $status = if ($workerCommand) { "NOT_YET_PROVEN" } else { "PROVEN_REAL" }
        Add-Result "6b.2" "Advertised capability manifest agrees with the worker" $status "desktop family only (BrowserEnabled=false, companion worker $(if ($workerCommand) { 'configured - mismatch' } else { 'not configured - consistent' })); $listing"
    }
    elseif ($workerCheck -and $workerCheck.Ok) {
        $missing = @($workerCheck.Capabilities | Where-Object { $advertised -notcontains $_ })
        $extra = @($browserOps | Where-Object { $workerCheck.Capabilities -notcontains $_ })
        if (@($missing).Count -eq 0 -and @($extra).Count -eq 0) {
            Add-Result "6b.2" "Advertised capability manifest agrees with the worker" "PROVEN_REAL" "worker and service agree on $(@($browserOps).Count) browser.* operations plus browser.chrome; $listing"
        }
        else {
            Add-Result "6b.2" "Advertised capability manifest agrees with the worker" "NOT_YET_PROVEN" "worker-only: [$($missing -join ', ')] service-only: [$($extra -join ', ')]; $listing"
        }
    }
    else {
        Add-Result "6b.2" "Advertised capability manifest agrees with the worker" "NOT_YET_PROVEN" "the service advertises the browser family but the worker self-check did not pass; $listing"
    }
}

# --- 6b.3 / 6b.4 the release that is actually executing (deployment truthfulness, 2026-09-04)
# 6b.1 proves a worker starts; it does not prove WHICH copy of the package it runs. The venv
# holds a non-editable copy under site-packages that can be stale while the source tree next
# to it is new, and a cwd inside the browser tree hides that. So: the self-check's hello
# (run from a neutral cwd) must name a module inside the installed venv, carry the installed
# source's version, contracts and package digest, and the site-packages copy must be
# byte-identical to the installed source; and the worker the companion is running right now
# (its audit row + live process) must be that release from that venv.
$browserRootInstalled = Join-Path $InstallRoot "browser"
if ($workerCommand -and $workerCheck -and $workerCheck.Ok -and (Test-Path -LiteralPath (Join-Path $browserRootInstalled "browser_agent\worker.py"))) {
    try {
        $installedRelease = Get-ExpectedWorkerRelease -BrowserSource $browserRootInstalled
        $proof = Assert-WorkerHelloMatchesRelease -Hello $workerCheck.Hello -Expected $installedRelease -BrowserRoot $browserRootInstalled -Label "installed worker"
        $copyDiff = @(Compare-BrowserPackageCopies -Source (Join-Path $browserRootInstalled "browser_agent") -Installed (Get-SitePackagesBrowserAgentDir -BrowserRoot $browserRootInstalled))
        if (@($copyDiff).Count -gt 0) { throw "site-packages copy differs from the installed source: $($copyDiff -join '; ')" }
        Add-Result "6b.3" "The worker executes the installed release from the installed venv" "PROVEN_REAL" `
            "worker $($proof.Version), module $($proof.ModuleFile), package digest $($proof.PackageSha256.Substring(0,12)) == installed source; site-packages copy byte-identical"
    }
    catch {
        Add-Result "6b.3" "The worker executes the installed release from the installed venv" "NOT_YET_PROVEN" $_.Exception.Message
    }

    $auditPath = Join-Path $env:ProgramData "PagentOS\companion\audit\companion-audit.jsonl"
    $liveRow = Get-LatestWorkerStartAudit -AuditPath $auditPath
    if ($null -eq $liveRow) {
        Add-Result "6b.4" "The companion's live worker is that release" "NOT_YET_PROVEN" "no browser_worker_started row in $auditPath (companion not started, or predates the audit)"
    }
    else {
        $liveProblems = @()
        try {
            $installedRelease = Get-ExpectedWorkerRelease -BrowserSource $browserRootInstalled
            $liveProblems = @(Test-LiveBrowserWorker -Audit $liveRow -Expected $installedRelease -BrowserRoot $browserRootInstalled `
                -BrowserDataDir (Join-Path $env:ProgramData "PagentOS\companion\browser") -DeployStartedAt ([datetime]::MinValue))
        }
        catch { $liveProblems = @($_.Exception.Message) }
        if (@($liveProblems).Count -eq 0) {
            Add-Result "6b.4" "The companion's live worker is that release" "PROVEN_REAL" "pid $($liveRow.Pid) started $($liveRow.Ts.ToString('o')): worker $($liveRow.WorkerVersion), module $($liveRow.Module)"
        }
        else {
            Add-Result "6b.4" "The companion's live worker is that release" "NOT_YET_PROVEN" "newest worker row (pid $($liveRow.Pid), $($liveRow.Ts.ToString('o'))): $($liveProblems -join '; ')"
        }
    }
}
elseif ($workerCommand) {
    Add-Result "6b.3" "The worker executes the installed release from the installed venv" "NOT_YET_PROVEN" "the self-check did not pass, or $browserRootInstalled has no worker source"
    Add-Result "6b.4" "The companion's live worker is that release" "NOT_YET_PROVEN" "depends on 6b.3"
}

# ------------------------------------------------------------------------ report

Write-Host ""
Write-Host "=== Device service qualification ===" -ForegroundColor Cyan
$results | Format-Table -AutoSize -Wrap

$blocked = $results | Where-Object { $_.Status -ne "PROVEN_REAL" }
if ($blocked) {
    Write-Host "$(@($blocked).Count) criteria not yet proven." -ForegroundColor Yellow
    exit 1
}

Write-Host "All checked criteria PROVEN_REAL." -ForegroundColor Green
exit 0
