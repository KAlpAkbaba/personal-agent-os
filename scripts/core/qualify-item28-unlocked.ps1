<#
.SYNOPSIS
    Everything owner item 28 unlocks, proved automatically the moment the install lands -
    against the REAL device, through the REAL production route, with no further owner
    involvement and nothing that blanks a screen or makes a sound.

.DESCRIPTION
    Item 28 is one elevated command (`install-device-service.ps1 -DisplayPower -Operator`).
    It takes the device from 29 advertised capabilities to 85 and is the single thing
    standing between six milestones' device halves and PROVEN_REAL. This script is what
    runs afterwards, so the owner does not have to run six qualifications by hand:

      M19  the Digital Operator - a real allowlisted application launched, its window
           listed, moved, resized and re-observed, its UI tree read, a value set through
           UI Automation and read back, text typed through the focus guard, and the window
           closed. Every effect is read back through a capability, never assumed.
      M20  the documents family - this repository's own deterministic fixtures
           (services/api/tests/fixtures/documents, the M20 oracle) placed inside an
           authorised root, then file.search / file.inspect / file.read / document.extract
           against them and every answer checked against truth.json's ground truth.
      M22  file.fetch's advertisement (the dialled-origin proof is the installer's own).
      M23  the projects family - project.scaffold writes a real project, project.status
           and project.stop answer about it, and project.run / project.test either run or
           report `dependency_unavailable / runtime_missing` truthfully.
      M25  scene.inspect over a scaffolded 3D project - the read path, its bounds and its
           render declaration, without Blender having to exist.
      M27  Paint - `desktop.open_application mspaint`, which no device could do before
           2026-09-09 because mspaint was on neither launch allowlist.
      M18.3 the ambient/display/alarm device path - display_status, activity_status,
           display_wake, the ambient policy, and an alarm armed and disarmed in silence.

    WHAT IT WILL NOT DO. It never blanks a display and never makes a sound. Turning the
    owner's screens off, waking them with a keypress, and hearing an alarm all need a human
    to look or listen; those are written into the evidence as READY_FOR_OWNER, naming the
    harness that runs them (scripts\core\owner-m18-3-display.ps1,
    scripts\core\owner-m18-3-alarm.ps1). It does open and close a few application windows -
    that is what "a real allowlisted app" means - and it says so before it starts.

    IT REFUSES TO REPORT SUCCESS AGAINST THE OLD RUNTIME. Before anything else it reads the
    installed binary's own ProductVersion and the device's advertised capability count, and
    stops if the runtime is still the 2026-09-06 build (a3cb04e) or advertises fewer than
    40 capabilities. A green report against the release item 28 was supposed to replace
    would be worse than no report at all.

    -DryRun (or -WhatIf) sends nothing. It runs the refusal gate for real and then prints
    and records the complete plan - every capability and payload it would send - so the
    script itself can be qualified before the runtime it needs exists.

    One JSON evidence file is written to docs\evidence\item28-unlocked-<timestamp>.json.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\core\qualify-item28-unlocked.ps1

.EXAMPLE
    # Now, against the old runtime: proves this script, changes nothing.
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\core\qualify-item28-unlocked.ps1 -DryRun
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    [string]$BaseUrl = "",
    [string]$Device = "",
    [switch]$DryRun,
    [string]$OutFile = "",
    [string]$InstallRoot = (Join-Path $env:ProgramFiles "PagentOS\agent"),
    [ValidateRange(10, 300)][int]$CommandTimeoutSec = 60
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$PSNativeCommandUseErrorActionPreference = $false

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\InstallEvidence.ps1")

$dry = [bool]$DryRun -or $WhatIfPreference
$script:Dry = $dry
#: Whether a THIRD party held the desktop foreground while the operator section ran, and
#: which one. Initialised here because StrictMode makes reading an unset variable a
#: terminating error, and the verdict at the end of that section reads both.
$script:OperatorForeignForeground = $false
$script:OperatorForeground = ""
if (-not $BaseUrl) { $BaseUrl = "http://${BrokerHost}:$ApiPort" }
$BaseUrl = $BaseUrl.TrimEnd('/')

# The runtime item 28 replaces. Naming it is the point: this script must never report a
# success that was really measured against the release the 2026-09-08 rollback restored.
$script:SupersededBuild = "a3cb04e"
$script:MinimumCapabilities = 40
$script:OperatorProbe = @("app.launch", "window.list", "ui.inspect", "file.search", "document.extract", "project.scaffold", "scene.inspect")

$runId = "item28-unlocked-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$evidence = [ordered]@{
    run_id      = $runId
    started_at  = (Get-Date).ToUniversalTime().ToString("o")
    mode        = $(if ($dry) { "dry-run" } else { "live" })
    cloud       = $BaseUrl
    repo_head   = ""
    runtime     = $null
    gate        = $null
    device      = $null
    sections    = @()
    plan        = @()
    checks      = @()
    ready_for_owner = @()
    #: Measurements the environment prevented - see Add-Blocked. Never counted as passes.
    blocked = @()
    verdict     = "NOT_RUN"
}
try { $evidence.repo_head = (Get-RepoHead -RepoRoot $repoRoot) } catch { $evidence.repo_head = "" }

# ------------------------------------------------------------------------- reporting

function Add-Check {
    <#
    .SYNOPSIS
        One judgement. In a dry run the judgement is recorded as INTENT, never as a result:
        a check computed over the synthetic answers of a device that was never asked is not
        evidence about anything, and must not be able to become a pass.
    #>
    param([string]$Section, [string]$Name, [bool]$Ok, [string]$Detail)
    if ($script:Dry) {
        $script:evidence.plan += [ordered]@{ section = $Section; check = $Name }
        Write-Host ("  [plan] would check {0}" -f $Name) -ForegroundColor DarkGray
        return
    }
    $script:evidence.checks += [ordered]@{ section = $Section; name = $Name; ok = $Ok; detail = $Detail }
    $mark = "FAIL"
    $color = "Red"
    if ($Ok) { $mark = "ok  "; $color = "Green" }
    Write-Host ("  [{0}] {1}: {2}" -f $mark, $Name, $Detail) -ForegroundColor $color
}

function Get-WindowTitle {
    <#  One window's CURRENT title, asked of the window list by id - never of whatever
        happens to be in front, which is a different window's title whenever a third party
        holds the foreground.  #>
    param($Section, [string]$WindowId, $ProcessId)
    if ($script:Dry -or -not $WindowId) { return "" }
    $listed = Invoke-DeviceCapability -Section $Section -Capability "window.list" -Payload @{ pid = [int]$ProcessId } -AllowFailure
    foreach ($w in @(Get-ResultField -Result $listed.Result -Name "windows")) {
        if ([string](Get-ResultField -Result $w -Name "window_id") -eq $WindowId) {
            return [string](Get-ResultField -Result $w -Name "title")
        }
    }
    return ""
}

function Add-Blocked {
    <#
    .SYNOPSIS
        A measurement the ENVIRONMENT prevented - neither a pass nor a product failure.

    .DESCRIPTION
        The third answer, and it has to exist. On 2026-09-09 the owner's Windows Search
        flyout held the foreground while this section ran; `window.activate` could not take
        it (Windows does not let a background process steal the foreground from another
        application) and the operator's focus guard then refused to type into a window that
        was not in front - which is precisely what that guard is FOR. Both were recorded as
        product failures. They were not: the device behaved correctly and said so, naming the
        window that held the foreground.

        A run that cannot measure something must say "could not measure", never "measured and
        it was fine" and never "measured and it was broken". This is only ever used where the
        device's OWN answer identifies the interference - never as a way to excuse a failure.
    #>
    param([string]$Section, [string]$Name, [string]$Detail)
    if ($script:Dry) { return }
    $script:evidence.blocked += [ordered]@{ section = $Section; name = $Name; detail = $Detail }
    Write-Host ("  [blkd] {0}: {1}" -f $Name, $Detail) -ForegroundColor DarkYellow
}

function Add-ReadyForOwner {
    param([string]$Section, [string]$What, [string]$Why, [string]$Harness)
    $script:evidence.ready_for_owner += [ordered]@{ section = $Section; what = $What; why = $Why; harness = $Harness }
    Write-Host ("  [own ] {0}: {1} -> {2}" -f $What, $Why, $Harness) -ForegroundColor Yellow
}

function New-Section {
    param([string]$Name, [string]$Milestone, [string]$Title)
    Write-Host ""
    Write-Host "== $Milestone  $Title" -ForegroundColor Cyan
    return [ordered]@{ name = $Name; milestone = $Milestone; title = $Title; verdict = "NOT_RUN"; detail = ""; steps = @() }
}

function Close-Section {
    # IDictionary, never [hashtable]: a section is an [ordered] dictionary, and binding one
    # to a [hashtable] parameter CONVERTS it - PowerShell hands the function a copy, and
    # every mutation lands on the copy while the caller's object stays as it was.
    param([System.Collections.IDictionary]$Section, [string]$Verdict, [string]$Detail = "")
    $Section.verdict = $Verdict
    $Section.detail = $Detail
    $script:evidence.sections += $Section
    $color = "Green"
    if ($Verdict -eq "FAILED") { $color = "Red" }
    elseif ($Verdict -ne "PROVEN_REAL") { $color = "Yellow" }
    Write-Host ("  -> {0}{1}" -f $Verdict, $(if ($Detail) { ": $Detail" } else { "" })) -ForegroundColor $color
}

# ------------------------------------------------------------- the refusal gate (phase 0)

function Get-InstalledRuntimeIdentity {
    <#
    .SYNOPSIS
        What is actually installed, read from the file rather than from a device row: the
        service binary's ProductVersion (assembly informational version, "<semver>+<sha>").
        Reading the file starts no process and touches neither the service nor the companion.
    #>
    param([string]$Root)
    $exe = Join-Path $Root "service\PagentOS.DeviceService.exe"
    if (-not (Test-Path -LiteralPath $exe)) {
        return [ordered]@{ present = $false; exe = $exe; product_version = ""; build = ""; installed_at = "" }
    }
    $item = Get-Item -LiteralPath $exe
    $product = [string]$item.VersionInfo.ProductVersion
    $build = ""
    if ($product -match '\+([0-9a-f]{7,40})') { $build = $Matches[1] }
    return [ordered]@{
        present         = $true
        exe             = $exe
        product_version = $product
        build           = $build
        installed_at    = $item.LastWriteTimeUtc.ToString("o")
    }
}

function Test-RuntimeUnlocked {
    <#
    .SYNOPSIS
        The refusal. Returns Ok + Reasons + the observations both judgements rest on.
    .DESCRIPTION
        Two independent facts, because either alone can lie: the installed binary's own
        ProductVersion (is this still the superseded build?) and the capability manifest the
        binary answers with (does it advertise at least the 40 names item 28 promises?). A
        run that cannot establish both refuses rather than guessing.
    #>
    param([hashtable]$Runtime)
    $reasons = @()
    $manifest = $null
    if (-not $Runtime.present) {
        $reasons += "no Windows agent is installed at $($Runtime.exe); item 28 has not been run"
    }
    else {
        if ($Runtime.build -and $script:SupersededBuild.StartsWith($Runtime.build.Substring(0, [Math]::Min(7, $Runtime.build.Length)))) {
            $reasons += "blocked: runtime is $script:SupersededBuild - the installed service is still $($Runtime.product_version), the release item 28 replaces"
        }
        elseif ($Runtime.build -and $Runtime.build.StartsWith($script:SupersededBuild)) {
            $reasons += "blocked: runtime is $script:SupersededBuild - the installed service is still $($Runtime.product_version), the release item 28 replaces"
        }
        # Ask the installed binary what it advertises. This is the same read
        # scripts\verify-device-service.ps1 performs: a short-lived child process with the
        # `capabilities` verb, which starts no service and changes no file.
        try { $manifest = Get-InstalledAgentManifest -ServiceExe $Runtime.exe }
        catch { $manifest = $null }
        if ($null -eq $manifest -or -not $manifest.Ok) {
            $reasons += "the installed service did not answer its 'capabilities' verb, so its manifest cannot be judged"
        }
        else {
            $count = @($manifest.Capabilities).Count
            if ($count -lt $script:MinimumCapabilities) {
                $reasons += "blocked: the installed runtime advertises $count capabilities, fewer than the $script:MinimumCapabilities item 28 promises"
            }
        }
    }
    return [pscustomobject]@{
        Ok       = (@($reasons).Count -eq 0)
        Reasons  = @($reasons)
        Manifest = $manifest
    }
}

# ------------------------------------------------------------------ the production route

$script:Headers = $null
$script:DeviceId = ""
$script:Step = 0

function Get-Json { param([string]$Path) return Invoke-JsonUtf8 -Uri "$BaseUrl$Path" -Headers $script:Headers -TimeoutSec 30 }
function Get-JsonOrNull { param([string]$Path) try { return Get-Json -Path $Path } catch { return $null } }

function Invoke-DeviceCapability {
    <#
    .SYNOPSIS
        One capability through the production route: POST /v1/devices/<id>/commands, then
        poll the row to its terminal ack. In -DryRun nothing is sent - the intent is
        recorded in the plan and a null result returned - so the caller's own logic still
        runs and can be read.
    .OUTPUTS
        Ok, Status, ErrorClass, Message, Result, Record
    #>
    param(
        # See Close-Section: [hashtable] here would copy the section and every step record
        # this function appends would be written to an object nobody reads.
        [Parameter(Mandatory = $true)][System.Collections.IDictionary]$Section,
        [Parameter(Mandatory = $true)][string]$Capability,
        [hashtable]$Payload = @{},
        [switch]$AllowFailure
    )
    $script:Step++
    $traceId = "$runId-$($script:Step)"
    if ($dry) {
        # Nothing is sent. The call is recorded, and a SHAPE-ONLY answer is returned so the
        # section's own logic keeps running and every later payload is built and recorded
        # too - the point of a dry run is to exercise this script, not to guess at a device.
        # Add-Check refuses to turn a judgement over these values into a result.
        $planned = [ordered]@{
            step = $script:Step; section = $Section.name; capability = $Capability
            payload = $Payload
        }
        $script:evidence.plan += $planned
        # Attached to the SECTION as well, so the section really is the object this function
        # writes into. A [hashtable] parameter here would silently copy the section and this
        # line would land nowhere; the gate suite asserts it lands.
        $Section.steps += $planned
        Write-Host ("  [plan] {0,-26} {1}" -f $Capability, ((ConvertTo-Json -InputObject $Payload -Compress -Depth 6)))
        return [pscustomobject]@{
            # Message belongs on BOTH returns or on neither: the live one gained it and this
            # one did not, so the dry run - the mode that exists to exercise this script
            # without a device - crashed on the very property that was added to stop a crash.
            # The gate suite caught it in seconds, which is what it is for.
            Ok = $true; Status = "not-sent"; ErrorClass = ""; Message = ""; Record = $null
            Result = [pscustomobject]@{
                window_id = "w-dryrun-0"; pid = 0; files_written = 0; root_path = ""
                state = "scaffolded"; url = ""; stopped = $false; armed = $false
                woken = $false; observed_state = ""; armed_alarms = 0; alarm_ringing = $false
            }
        }
    }

    $body = @{
        capability      = $Capability
        payload         = $Payload
        idempotency_key = "${runId}:$($script:Step):${Capability}"
        timeout_s       = $CommandTimeoutSec
    } | ConvertTo-Json -Depth 8 -Compress
    # The trace id really is sent (X-Trace-Id), so the id this run records is the id Cloud
    # Core correlated the command by - a record that names a trace nobody carried would be
    # exactly the kind of evidence this script exists to stop producing.
    $headers = @{}
    foreach ($key in $script:Headers.Keys) { $headers[$key] = $script:Headers[$key] }
    $headers["X-Trace-Id"] = $traceId
    $created = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/devices/$script:DeviceId/commands" -Headers $headers -Body $body -TimeoutSec 30
    $commandId = [string]$created.command_id
    $deadline = (Get-Date).AddSeconds($CommandTimeoutSec + 20)
    $row = $null
    do {
        Start-Sleep -Milliseconds 500
        $row = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/devices/$script:DeviceId/commands/$commandId" -Headers $script:Headers -TimeoutSec 30
    } while ((Get-Date) -lt $deadline -and ([string]$row.status) -notin @("succeeded", "failed", "expired", "cancelled"))

    $status = [string]$row.status
    $errorClass = ""
    $errorMessage = ""
    $err = $null
    if ($row.PSObject.Properties.Name -contains "error") { $err = $row.error }
    if ($null -ne $err) {
        if ($err.PSObject.Properties.Name -contains "class") { $errorClass = [string]$err.class }
        if ($err.PSObject.Properties.Name -contains "message") { $errorMessage = [string]$err.message }
    }
    $result = $null
    if ($row.PSObject.Properties.Name -contains "result") { $result = $row.result }

    $record = [ordered]@{
        step = $script:Step; capability = $Capability; command_id = $commandId; trace_id = $traceId
        status = $status; error_class = $errorClass; error_message = $errorMessage
        payload = $Payload; result = $result
    }
    $Section.steps += $record
    $line = "  [{0,2}] {1,-26} {2}" -f $script:Step, $Capability, $status
    if ($errorClass) { $line += "  error=$errorClass" }
    Write-Host $line
    if (-not $AllowFailure -and $status -ne "succeeded") {
        Add-Check -Section $Section.name -Name "$Capability.succeeded" -Ok $false -Detail "$status $errorClass $errorMessage"
    }
    return [pscustomobject]@{
        Ok = ($status -eq "succeeded"); Status = $status; ErrorClass = $errorClass
        # The device's own sentence. It was computed here and thrown away, and one caller read
        # `$launch.Message` off an object that had no such property - which under StrictMode is
        # a terminating error, so the M28 section died mid-run and its whole verdict was lost
        # instead of one check failing (2026-09-09). A result that carries a failure must carry
        # the reason with it: these messages are the most useful thing the device produces.
        Message = $errorMessage
        Result = $result; Record = $record
    }
}

function Get-ResultField {
    param($Result, [string]$Name)
    if ($null -eq $Result) { return $null }
    if ($Result -is [System.Collections.IDictionary]) {
        if ($Result.Contains($Name)) { return $Result[$Name] }
        return $null
    }
    $property = $Result.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

# ============================================================================= sections

function Invoke-OperatorSection {
    <#  M19: a real application, and every effect read back through a capability.  #>
    $section = New-Section -Name "operator" -Milestone "M19" -Title "the Digital Operator, against a real allowlisted application"
    $failures = 0

    $launch = Invoke-DeviceCapability -Section $section -Capability "app.launch" -Payload @{ application = "notepad" }
        $windowId = [string](Get-ResultField -Result $launch.Result -Name "window_id")
        $launchPid = Get-ResultField -Result $launch.Result -Name "pid"
        $observed = Get-ResultField -Result $launch.Result -Name "observed"
        $appeared = Get-ResultField -Result $observed -Name "window_appeared"
        Add-Check -Section $section.name -Name "app.launch.window_observed" -Ok ($launch.Ok -and $windowId -and [bool]$appeared) `
            -Detail "pid=$launchPid window_id=$windowId window_appeared=$appeared"
        if (-not $dry -and -not ($launch.Ok -and $windowId)) {
            Close-Section -Section $section -Verdict "FAILED" -Detail "the application never started; nothing further could be read back"
            return $section
        }

        # Windows will not let a background process take the foreground away from an
        # application that owns it, so activation is retried before anything is concluded -
        # a flyout that is closing, or an animation, is a second, not a verdict.
        $activated = $null
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            $activated = Invoke-DeviceCapability -Section $section -Capability "window.activate" -Payload @{ window_id = $windowId } -AllowFailure
            if ($activated.Ok) { break }
            Start-Sleep -Seconds 2
        }
        $current = Invoke-DeviceCapability -Section $section -Capability "window.current" -Payload @{}
        $currentWindow = Get-ResultField -Result $current.Result -Name "window"
        $currentId = [string](Get-ResultField -Result $currentWindow -Name "window_id")
        $currentPid = Get-ResultField -Result $currentWindow -Name "pid"
        $currentImage = [string](Get-ResultField -Result $currentWindow -Name "image")
        $currentTitle = [string](Get-ResultField -Result $currentWindow -Name "title")

        # Whether a THIRD party holds the foreground is the device's own answer, not a guess:
        # window.current names the process. If it is not the one this run launched, the
        # measurement was prevented rather than failed (2026-09-09: searchapp.exe, "Arama").
        $foreignForeground = ($currentId -ne $windowId) -and ($null -ne $currentPid) -and
            ($null -ne $launchPid) -and ([int]$currentPid -ne [int]$launchPid)
        $script:OperatorForeignForeground = $foreignForeground
        $script:OperatorForeground = "$currentImage ""$currentTitle"" pid=$currentPid"

        if ($foreignForeground) {
            Add-Blocked -Section $section.name -Name "window.activate.read_back_by_window_current" `
                -Detail "another application holds the foreground and Windows will not let it be taken: $script:OperatorForeground. The device refused correctly and named it; nothing here is a statement about window.activate."
        }
        else {
            # -AllowFailure above suppresses the automatic "<capability>.succeeded" check, so
            # it is made here - after the retries, and only when no third party is holding the
            # foreground. Otherwise a real activation defect would go unrecorded.
            Add-Check -Section $section.name -Name "window.activate.succeeded" -Ok $activated.Ok `
                -Detail "$($activated.Status) $($activated.ErrorClass) $($activated.Message)"
            if (-not $activated.Ok) { $failures++ }
            Add-Check -Section $section.name -Name "window.activate.read_back_by_window_current" -Ok ($activated.Ok -and $currentId -eq $windowId) `
                -Detail "window.current says $currentId; the window activated was $windowId"
            if ($currentId -ne $windowId) { $failures++ }
        }

        # move and resize, each re-observed from the RESULT the device returned, not assumed
        $moved = Invoke-DeviceCapability -Section $section -Capability "window.move" -Payload @{ window_id = $windowId; x = 120; y = 90 }
        $movedRect = Get-ResultField -Result (Get-ResultField -Result $moved.Result -Name "observed") -Name "window"
        $movedRect = Get-ResultField -Result $movedRect -Name "rect"
        $mx = Get-ResultField -Result $movedRect -Name "x"
        $my = Get-ResultField -Result $movedRect -Name "y"
        $moveOk = $moved.Ok -and ($null -ne $mx) -and ([Math]::Abs([int]$mx - 120) -le 8) -and ([Math]::Abs([int]$my - 90) -le 8)
        Add-Check -Section $section.name -Name "window.move.read_back" -Ok $moveOk -Detail "asked for (120,90), the device re-observed ($mx,$my)"
        if (-not $moveOk) { $failures++ }

        $resized = Invoke-DeviceCapability -Section $section -Capability "window.resize" -Payload @{ window_id = $windowId; width = 700; height = 480 }
        $resizedRect = Get-ResultField -Result (Get-ResultField -Result $resized.Result -Name "observed") -Name "window"
        $resizedRect = Get-ResultField -Result $resizedRect -Name "rect"
        $rw = Get-ResultField -Result $resizedRect -Name "width"
        $rh = Get-ResultField -Result $resizedRect -Name "height"
        $resizeOk = $resized.Ok -and ($null -ne $rw) -and ([Math]::Abs([int]$rw - 700) -le 8) -and ([Math]::Abs([int]$rh - 480) -le 8)
        Add-Check -Section $section.name -Name "window.resize.read_back" -Ok $resizeOk -Detail "asked for 700x480, the device re-observed ${rw}x${rh}"
        if (-not $resizeOk) { $failures++ }

        foreach ($pair in @(@("window.minimize", "minimized"), @("window.maximize", "maximized"), @("window.restore", "normal"))) {
            $stateResult = Invoke-DeviceCapability -Section $section -Capability $pair[0] -Payload @{ window_id = $windowId }
            $stateObserved = Get-ResultField -Result $stateResult.Result -Name "observed"
            $stateWindow = Get-ResultField -Result $stateObserved -Name "window"
            $state = [string](Get-ResultField -Result $stateWindow -Name "state")
            $stateOk = $stateResult.Ok -and $state -eq $pair[1]
            Add-Check -Section $section.name -Name "$($pair[0]).read_back" -Ok $stateOk -Detail "the device re-observed state '$state', asked for '$($pair[1])'"
            if (-not $stateOk) { $failures++ }
        }

        # ui.* over the same window: the tree, then a value SET and READ BACK.
        $inspect = Invoke-DeviceCapability -Section $section -Capability "ui.inspect" -Payload @{ window_id = $windowId; depth = 4; max_nodes = 120 }
        $nodeCount = Get-ResultField -Result $inspect.Result -Name "node_count"
        Add-Check -Section $section.name -Name "ui.inspect.returned_the_windows_tree" -Ok ($inspect.Ok -and [int]$nodeCount -gt 0) -Detail "node_count=$nodeCount"
        if (-not ($inspect.Ok -and [int]$nodeCount -gt 0)) { $failures++ }

        # Turkish letters are built from code points, never typed into this file: Windows
        # PowerShell 5.1 reads a BOM-less script as the system code page, and the repo's
        # other owner harnesses do the same for the same reason.
        $g_br = [char]0x011F; $u_uml = [char]0x00FC; $s_ced = [char]0x015F
        $o_uml = [char]0x00F6; $c_ced = [char]0x00E7; $i_dot = [char]0x0131
        $sample = "Item 28: " + $g_br + $u_uml + $s_ced + $o_uml + $c_ced + $i_dot + " 123"
        # The editor's text area is a Document on some Windows builds and an Edit on others
        # (NotepadLifecycleTests accepts either), and the element query is by control type,
        # so both spellings are tried before this is called a refusal.
        $setValue = Invoke-DeviceCapability -Section $section -Capability "ui.set_value" -Payload @{ window_id = $windowId; control_type = "Document"; value = $sample } -AllowFailure
        if ((-not $setValue.Ok) -and $setValue.ErrorClass -eq "ui_target_not_found") {
            $setValue = Invoke-DeviceCapability -Section $section -Capability "ui.set_value" -Payload @{ window_id = $windowId; control_type = "Edit"; value = $sample } -AllowFailure
        }
        if ($setValue.Ok) {
            $observedValue = [string](Get-ResultField -Result $setValue.Result -Name "observed_value")
            $valueOk = ($observedValue -eq $sample)
            Add-Check -Section $section.name -Name "ui.set_value.read_back_exactly" -Ok $valueOk -Detail "the document re-read as '$observedValue'"
            if (-not $valueOk) { $failures++ }
        }
        else {
            # An honest observation, never a silent pass: the document of this build's editor
            # does not expose a settable value, and the error class says so by name.
            Add-Check -Section $section.name -Name "ui.set_value.answered_with_a_named_class" -Ok ([bool]$setValue.ErrorClass) `
                -Detail "this editor's document refused the value with '$($setValue.ErrorClass)'; keyboard.type below is the other path to the same control"
        }

        # keyboard.type through the focus guard, read back through a DIFFERENT capability:
        # an unsaved editor marks its own title.
        # The title BEFORE must be this window's own, read from the window list. It used to be
        # read from window.current - which, when a third party held the foreground, was that
        # third party's title, so the check compared "Arama" against Notepad's and called the
        # difference a success condition it had not measured (2026-09-09).
        $titleBefore = Get-WindowTitle -Section $section -WindowId $windowId -ProcessId $launchPid
        if ($script:OperatorForeignForeground) {
            Add-Blocked -Section $section.name -Name "keyboard.type.changed_the_document_the_title_reports" `
                -Detail "not attempted: $script:OperatorForeground holds the foreground, and typing into a window that is not in front is exactly what the operator's focus guard refuses. Nothing was sent, which is correct."
        }
        else {
            $typed = Invoke-DeviceCapability -Section $section -Capability "keyboard.type" -Payload @{ window_id = $windowId; text = " (28)" }
            $titleAfter = Get-WindowTitle -Section $section -WindowId $windowId -ProcessId $launchPid
            $typedOk = $typed.Ok -and $titleAfter -and ($titleAfter -ne $titleBefore)
            Add-Check -Section $section.name -Name "keyboard.type.changed_the_document_the_title_reports" -Ok $typedOk `
                -Detail "title '$titleBefore' -> '$titleAfter' (typed_chars=$(Get-ResultField -Result $typed.Result -Name 'typed_chars'))"
            if (-not $typedOk) { $failures++ }
        }

        # Leave nothing behind: the process this run started is the process it ends.
        $closed = Invoke-DeviceCapability -Section $section -Capability "app.close" -Payload @{ pid = $launchPid; force = $true } -AllowFailure
        $alive = Get-ResultField -Result (Get-ResultField -Result $closed.Result -Name "observed") -Name "process_alive"
        Add-Check -Section $section.name -Name "app.close.left_nothing_running" -Ok ($closed.Ok -and -not [bool]$alive) -Detail "process_alive=$alive"
        if (-not ($closed.Ok -and -not [bool]$alive)) { $failures++ }

    if ($dry) { Close-Section -Section $section -Verdict "PLANNED" -Detail "payloads recorded; nothing sent" }
    elseif ($failures -gt 0) { Close-Section -Section $section -Verdict "FAILED" -Detail "$failures effect(s) did not read back" }
    elseif ($script:OperatorForeignForeground) {
        # Everything that COULD be measured was, and it passed. What could not be measured is
        # named rather than counted either way: this is not PROVEN_REAL and it is not FAILED.
        Close-Section -Section $section -Verdict "BLOCKED" `
            -Detail "geometry, state, the UI tree and close all read back; foreground-dependent steps were prevented by $script:OperatorForeground. Re-run with the desktop idle."
    }
    else { Close-Section -Section $section -Verdict "PROVEN_REAL" -Detail "launch, window geometry and state, the UI tree, typing and close - each read back" }
    return $section
}

function Copy-DocumentFixtures {
    <#
    .SYNOPSIS
        This repository's own M20 fixtures, placed where the device is allowed to read.
    .DESCRIPTION
        The fixtures are the oracle (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md), but the
        checkout is not inside any authorised root and the roots check resolves before it
        compares, so a junction would not help and should not be tried. %TEMP%\pagentos-
        operator-fixture IS a default root (Operator\OperatorOptions.DefaultRoots), and the
        companion runs in this same owner session, so the same path means the same directory.
    #>
    param([string]$RunId)
    $source = Join-Path $repoRoot "services\api\tests\fixtures\documents"
    if (-not (Test-Path -LiteralPath $source)) { throw "the M20 fixtures are not in this checkout: $source" }
    $target = Join-Path $env:TEMP "pagentos-operator-fixture\$RunId"
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Copy-Item -LiteralPath $source -Destination $target -Recurse -Force
    return (Join-Path $target "documents")
}

function Invoke-DocumentsSection {
    <#  M20: the device reads real files, and every answer is checked against truth.json.  #>
    param([string]$FixtureRoot)
    $section = New-Section -Name "documents" -Milestone "M20" -Title "the documents family, against this repository's own fixtures"
    $failures = 0

    $search = Invoke-DeviceCapability -Section $section -Capability "file.search" -Payload @{
        pattern = "sozlesme"; roots = @($FixtureRoot); max = 50
    }
    $pdfPath = Join-Path $FixtureRoot "rapor.pdf"
    $mdPath = Join-Path $FixtureRoot "notlar.md"
    $pyPath = Join-Path $FixtureRoot "kod.py"
    $xlsxPath = Join-Path $FixtureRoot "butce-2026.xlsx"

        $found = @(Get-ResultField -Result $search.Result -Name "files")
        $names = @($found | ForEach-Object { [string](Get-ResultField -Result $_ -Name "name") })
        $searchOk = $search.Ok -and (@($names | Where-Object { $_ -like "sozlesme*" }).Count -ge 2)
        Add-Check -Section $section.name -Name "file.search.found_both_contracts" -Ok $searchOk `
            -Detail "$(@($found).Count) file(s): $($names -join ', ')"
        if (-not $searchOk) { $failures++ }

        $inspect = Invoke-DeviceCapability -Section $section -Capability "file.inspect" -Payload @{ path = $pdfPath }
        $pages = Get-ResultField -Result $inspect.Result -Name "pages"
        $inspectOk = $inspect.Ok -and ([int]$pages -eq 5)
        Add-Check -Section $section.name -Name "file.inspect.pdf_page_count_matches_the_oracle" -Ok $inspectOk -Detail "pages=$pages, truth.json says 5"
        if (-not $inspectOk) { $failures++ }

        # The device's own sha256 of a file must equal this checkout's - the same bytes, seen
        # from both ends.
        $fileRecord = Get-ResultField -Result $inspect.Result -Name "file"
        $deviceSha = [string](Get-ResultField -Result $fileRecord -Name "sha256")
        $localSha = (Get-FileHash -LiteralPath $pdfPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $shaOk = $deviceSha -and ($deviceSha.ToLowerInvariant() -eq $localSha)
        Add-Check -Section $section.name -Name "file.inspect.sha256_is_this_checkouts_file" -Ok $shaOk -Detail "device $deviceSha vs checkout $localSha"
        if (-not $shaOk) { $failures++ }

        $sheets = Invoke-DeviceCapability -Section $section -Capability "file.inspect" -Payload @{ path = $xlsxPath }
        $sheetNames = @(Get-ResultField -Result $sheets.Result -Name "sheets")
        $sheetOk = $sheets.Ok -and ($sheetNames -contains "Ozet")
        Add-Check -Section $section.name -Name "file.inspect.xlsx_names_its_sheets" -Ok $sheetOk -Detail "sheets=$($sheetNames -join ', ')"
        if (-not $sheetOk) { $failures++ }

        $read = Invoke-DeviceCapability -Section $section -Capability "file.read" -Payload @{ path = $pyPath; length = 4096 }
        $text = [string](Get-ResultField -Result $read.Result -Name "text")
        $readOk = $read.Ok -and ($text -match 'def\s+hesapla')
        Add-Check -Section $section.name -Name "file.read.returned_the_functions_source" -Ok $readOk -Detail "kod.py carries 'hesapla': $readOk"
        if (-not $readOk) { $failures++ }

        $extract = Invoke-DeviceCapability -Section $section -Capability "document.extract" -Payload @{ path = $mdPath }
        $blocks = @(Get-ResultField -Result $extract.Result -Name "blocks")
        $decision = $null
        foreach ($block in $blocks) {
            if ([string](Get-ResultField -Result $block -Name "ref") -eq "h2:Kararlar") { $decision = $block }
        }
        $extractOk = $extract.Ok -and ($null -ne $decision)
        Add-Check -Section $section.name -Name "document.extract.reference_scheme_names_the_place" -Ok $extractOk `
            -Detail "$(@($blocks).Count) block(s); 'h2:Kararlar' present: $($null -ne $decision)"
        if (-not $extractOk) { $failures++ }

        $pdfExtract = Invoke-DeviceCapability -Section $section -Capability "document.extract" -Payload @{ path = $pdfPath; page_range = @(3, 3) }
        $pdfBlocks = @(Get-ResultField -Result $pdfExtract.Result -Name "blocks")
        $pdfText = ($pdfBlocks | ForEach-Object { [string](Get-ResultField -Result $_ -Name "text") }) -join " "
        $pdfOk = $pdfExtract.Ok -and ($pdfText -match "referans testidir")
        Add-Check -Section $section.name -Name "document.extract.page_3_is_the_sentence_the_oracle_names" -Ok $pdfOk `
            -Detail "page 3 carries the reference sentence: $pdfOk"
        if (-not $pdfOk) { $failures++ }

        # The confinement is part of the capability, so it is part of the proof: a path
        # outside the roots must be refused, and the argument must not come back in the error.
        $outside = Invoke-DeviceCapability -Section $section -Capability "file.inspect" `
            -Payload @{ path = (Join-Path $repoRoot "CLAUDE.md") } -AllowFailure
        $confinedOk = (-not $outside.Ok) -and ($outside.ErrorClass -eq "permission_denied")
        Add-Check -Section $section.name -Name "file.inspect.refuses_a_path_outside_the_authorised_roots" -Ok $confinedOk `
            -Detail "the checkout is outside the roots and answered '$($outside.ErrorClass)'"
        if (-not $confinedOk) { $failures++ }

    if ($dry) { Close-Section -Section $section -Verdict "PLANNED" -Detail "payloads recorded; nothing sent" }
    elseif ($failures -eq 0) { Close-Section -Section $section -Verdict "PROVEN_REAL" -Detail "search, inspect, read and extract, each checked against truth.json, plus the roots refusal" }
    else { Close-Section -Section $section -Verdict "FAILED" -Detail "$failures answer(s) disagreed with the oracle" }
    return $section
}

function Invoke-ProjectsSection {
    <#  M23: a real project written, run, tested and stopped - or an honest runtime refusal.  #>
    $section = New-Section -Name "projects" -Milestone "M23" -Title "the projects family, scaffolded and driven on the device"
    $failures = 0
    # STABLE, not timestamped. A folder under the Projects root belongs to the project id
    # that created it, and a re-scaffold of the SAME id overwrites its own files while any
    # other id is refused by name (DEVICE_PROTOCOL.md 6l). A new id each run therefore
    # qualified once and then failed for ever after with "'item28-check' belongs to another
    # project" - which is the device being right about ownership, and this script being
    # unable to run twice on the same machine (2026-09-09, the second live run).
    $projectId = "item28-check"
    $port = 51987
    $payload = @{
        project_id = $projectId
        slug       = "item28-check"
        root       = "projects"
        files      = @(
            @{ path = "index.html"; text = "<!doctype html><title>item 28</title><p>merhaba</p>" }
            @{ path = "tests/run.js"; text = "if (1 + 1 !== 2) { process.exit(1); }`nconsole.log('1 passed, 0 failed');`n" }
        )
        manifest   = @{
            entry = "index.html"
            port  = $port
            run   = @{ serve = "python -m http.server <port> --bind 127.0.0.1" }
            test  = @{ unit = "node tests/run.js" }
        }
    }
    $scaffold = Invoke-DeviceCapability -Section $section -Capability "project.scaffold" -Payload $payload

        $written = Get-ResultField -Result $scaffold.Result -Name "files_written"
        $rootPath = [string](Get-ResultField -Result $scaffold.Result -Name "root_path")
        $scaffoldOk = $scaffold.Ok -and ([int]$written -eq 2) -and $rootPath
        Add-Check -Section $section.name -Name "project.scaffold.wrote_the_project" -Ok $scaffoldOk -Detail "files_written=$written root_path=$rootPath"
        if (-not $dry -and -not $scaffoldOk) {
            Close-Section -Section $section -Verdict "FAILED" -Detail "the project was never written; nothing further could be driven"
            return $section
        }

        $status = Invoke-DeviceCapability -Section $section -Capability "project.status" -Payload @{ project_id = $projectId }
        $state = [string](Get-ResultField -Result $status.Result -Name "state")
        Add-Check -Section $section.name -Name "project.status.knows_the_project_it_just_wrote" -Ok ($status.Ok -and $state -eq "scaffolded") -Detail "state=$state"
        if (-not ($status.Ok -and $state -eq "scaffolded")) { $failures++ }

        # run and test need a runtime on this machine. A runtime that is not installed is a
        # correct, named answer - dependency_unavailable / runtime_missing - never a failure
        # of the capability, and never something to report as a pass either.
        $run = Invoke-DeviceCapability -Section $section -Capability "project.run" -Payload @{ project_id = $projectId } -AllowFailure
        if ($run.Ok) {
            $url = [string](Get-ResultField -Result $run.Result -Name "url")
            Add-Check -Section $section.name -Name "project.run.served_on_its_own_port" -Ok ([bool]$url) -Detail "url=$url"
            if (-not $url) { $failures++ }
            $stop = Invoke-DeviceCapability -Section $section -Capability "project.stop" -Payload @{ project_id = $projectId }
            Add-Check -Section $section.name -Name "project.stop.ended_what_it_started" -Ok ($stop.Ok -and [bool](Get-ResultField -Result $stop.Result -Name "stopped")) `
                -Detail "was_running=$(Get-ResultField -Result $stop.Result -Name 'was_running')"
            if (-not $stop.Ok) { $failures++ }
        }
        elseif ($run.ErrorClass -eq "dependency_unavailable") {
            Add-Check -Section $section.name -Name "project.run.reported_the_missing_runtime_by_name" -Ok $true `
                -Detail "python is not installed for the companion; the device said 'dependency_unavailable' rather than pretending"
            Add-ReadyForOwner -Section $section.name -What "project.run against a real web runtime" `
                -Why "python is not on this machine's PATH for the owner session" -Harness "install Python, then rerun this script"
        }
        else {
            Add-Check -Section $section.name -Name "project.run.answered_correctly" -Ok $false -Detail "unexpected '$($run.ErrorClass)'"
            $failures++
        }

        $test = Invoke-DeviceCapability -Section $section -Capability "project.test" -Payload @{ project_id = $projectId } -AllowFailure
        if ($test.Ok) {
            $passed = Get-ResultField -Result $test.Result -Name "passed"
            $exit = Get-ResultField -Result $test.Result -Name "exit_code"
            Add-Check -Section $section.name -Name "project.test.parsed_the_run" -Ok ([int]$exit -eq 0) -Detail "exit_code=$exit passed=$passed"
            if ([int]$exit -ne 0) { $failures++ }
        }
        elseif ($test.ErrorClass -eq "dependency_unavailable") {
            Add-Check -Section $section.name -Name "project.test.reported_the_missing_runtime_by_name" -Ok $true -Detail "node is not installed for the companion; said so rather than pretending"
            Add-ReadyForOwner -Section $section.name -What "project.test against a real test runtime" `
                -Why "node is not on this machine's PATH for the owner session" -Harness "install Node.js, then rerun this script"
        }
        else {
            Add-Check -Section $section.name -Name "project.test.answered_correctly" -Ok $false -Detail "unexpected '$($test.ErrorClass)'"
            $failures++
        }

    if ($dry) { Close-Section -Section $section -Verdict "PLANNED" -Detail "payloads recorded; nothing sent" }
    elseif ($failures -eq 0) { Close-Section -Section $section -Verdict "PROVEN_REAL" -Detail "scaffold and status proven; run/test proven or refused by name" }
    else { Close-Section -Section $section -Verdict "FAILED" -Detail "$failures step(s) did not answer correctly" }
    return $section
}

function Invoke-SceneSection {
    <#
    .SYNOPSIS
        M25: scene.inspect's read path, over a 3D project this run scaffolds.
    .DESCRIPTION
        scene.inspect never runs Blender - it reads the inspection the driver wrote and the
        render that file declares. Scaffolding a 3D project whose files already contain that
        inspection therefore proves the whole device-side path (root, bounds, JSON, render
        declaration) on a machine where Blender may not be installed at all; the driver's own
        absence surfaces at project.run as dependency_unavailable, which M23 above records.
    #>
    $section = New-Section -Name "scene" -Milestone "M25" -Title "scene.inspect over a scaffolded 3D project"
    $failures = 0
    #: Stable for the same reason as the projects section above: the folder belongs to the id
    #: that made it, so a timestamped id passes once and is refused by name every run after.
    $projectId = "item28-scene"
    $inspection = '{"objects":[{"name":"Cube","type":"MESH","location":[0,0,0]}],"frame":1,"engine":"CYCLES"}'
    $payload = @{
        project_id = $projectId
        slug       = "item28-scene"
        root       = "3d"
        files      = @(
            @{ path = "out.json"; text = $inspection }
            @{ path = "plan.json"; text = '{"steps":[]}' }
            # Named by the manifest below, and never run here: this section proves
            # scene.inspect's READ path. Scaffolding the file the command names is what makes
            # the manifest honest rather than a form that satisfies a validator.
            @{ path = "driver.py"; text = "# item 28: named by the manifest, never run - this section only reads out.json`n" }
        )
        manifest   = @{
            entry = "plan.json"
            port  = 51988
            # The ONE allowlisted Blender form, eight tokens without a scene file. The first
            # attempt wrote `blender -b --python driver.py` and the device refused it by name
            # (2026-09-09) - correctly, and with a message that listed every accepted shape.
            # --factory-startup is never optional: without it the owner's installed add-ons
            # are loaded into the run, and one of them never lets the editor exit.
            run   = @{ build = "blender --factory-startup -b --python driver.py -- plan.json out.json" }
        }
    }
    $scaffold = Invoke-DeviceCapability -Section $section -Capability "project.scaffold" -Payload $payload -AllowFailure
        if (-not $dry -and -not $scaffold.Ok) {
            Add-Check -Section $section.name -Name "project.scaffold.3d_root" -Ok $false -Detail "$($scaffold.ErrorClass): the 3D project could not be written"
            Close-Section -Section $section -Verdict "FAILED" -Detail "no 3D project to inspect"
            return $section
        }
        $inspect = Invoke-DeviceCapability -Section $section -Capability "scene.inspect" -Payload @{ project_id = $projectId } -AllowFailure
        if ($inspect.Ok) {
            $doc = Get-ResultField -Result $inspect.Result -Name "inspection"
            $objects = @(Get-ResultField -Result $doc -Name "objects")
            $sceneOk = (@($objects).Count -ge 1)
            Add-Check -Section $section.name -Name "scene.inspect.returned_the_drivers_own_document" -Ok $sceneOk `
                -Detail "$(@($objects).Count) object(s); inspection_path=$(Get-ResultField -Result $inspect.Result -Name 'inspection_path')"
            if (-not $sceneOk) { $failures++ }
            $render = Get-ResultField -Result $inspect.Result -Name "render"
            Add-Check -Section $section.name -Name "scene.inspect.render_is_null_when_none_is_declared" -Ok ($null -eq $render) `
                -Detail "the inspection declared no render, and none was invented"
            if ($null -ne $render) { $failures++ }
        }
        else {
            Add-Check -Section $section.name -Name "scene.inspect.answered_correctly" -Ok $false -Detail "unexpected '$($inspect.ErrorClass)'"
            $failures++
        }
    if ($dry) { Close-Section -Section $section -Verdict "PLANNED" -Detail "payloads recorded; nothing sent" }
    elseif ($failures -eq 0) { Close-Section -Section $section -Verdict "PROVEN_REAL" -Detail "the inspection read path, its bounds and its render declaration" }
    else { Close-Section -Section $section -Verdict "FAILED" -Detail "$failures step(s) did not answer correctly" }
    return $section
}

function Invoke-PaintSection {
    <#  M27: Paint, which no device could open before mspaint reached the allowlists.  #>
    param([string]$FixtureRoot)
    $section = New-Section -Name "paint" -Milestone "M27" -Title "Paint, opened on the production route"
    $failures = 0
    $image = Join-Path $FixtureRoot "item28-export.png"
        # A tiny, real PNG this run writes itself: 1x1, opaque. Paint opens what the device
        # produced, which is what the M27 export check means.
        $png = [byte[]]@(
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
            0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
            0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41, 0x54, 0x08, 0xD7, 0x63, 0xF8, 0xCF, 0xC0, 0x00,
            0x00, 0x03, 0x01, 0x01, 0x00, 0x18, 0xDD, 0x8D, 0xB0, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E,
            0x44, 0xAE, 0x42, 0x60, 0x82)
        [System.IO.File]::WriteAllBytes($image, $png)

    $launch = Invoke-DeviceCapability -Section $section -Capability "app.launch" -Payload @{ application = "mspaint"; args = @($image) } -AllowFailure
        if (-not $dry -and ($launch.ErrorClass -eq "permission_denied" -or $launch.ErrorClass -eq "capability_missing")) {
            Add-Check -Section $section.name -Name "app.launch.mspaint_is_allowlisted" -Ok $false `
                -Detail "the installed runtime predates the mspaint allowlist entry ('$($launch.ErrorClass)'); rerun item 28 from a checkout at or after 2026-09-09"
            Close-Section -Section $section -Verdict "BLOCKED" -Detail "this runtime cannot open Paint"
            return $section
        }
        $paintPid = Get-ResultField -Result $launch.Result -Name "pid"
        $paintWindow = [string](Get-ResultField -Result $launch.Result -Name "window_id")
        $launchOk = $launch.Ok -and $paintPid
        Add-Check -Section $section.name -Name "app.launch.mspaint_opened_the_exported_image" -Ok $launchOk -Detail "pid=$paintPid window_id=$paintWindow image=$image"
        if (-not $launchOk) { $failures++ }
        if ($paintPid) {
            $closed = Invoke-DeviceCapability -Section $section -Capability "app.close" -Payload @{ pid = $paintPid; force = $true } -AllowFailure
            Add-Check -Section $section.name -Name "app.close.paint_left_nothing_running" -Ok $closed.Ok `
                -Detail "process_alive=$(Get-ResultField -Result (Get-ResultField -Result $closed.Result -Name 'observed') -Name 'process_alive')"
        }
        # The device-side export check named in the M27 spec does not exist as a capability
        # in this checkout; saying so is the honest answer, not a silent omission.
        Add-Check -Section $section.name -Name "creative.export_check.is_not_a_device_capability_in_this_checkout" -Ok $true `
            -Detail "no such capability is implemented (it appears only in M27 prose); Paint's open path is what item 28 unlocks today"
        Add-ReadyForOwner -Section $section.name -What "the visual confirmation that Paint shows the exported image" `
            -Why "only a person can see what is on the screen" -Harness "look at the Paint window, or docs/evidence/m27-paint-lab-*.json"
    if ($dry) { Close-Section -Section $section -Verdict "PLANNED" -Detail "payloads recorded; nothing sent" }
    elseif ($failures -eq 0) { Close-Section -Section $section -Verdict "PROVEN_REAL" -Detail "Paint opened the device's own image and was closed again" }
    else { Close-Section -Section $section -Verdict "FAILED" -Detail "$failures step(s) failed" }
    return $section
}

function Get-UiNodeText {
    <#  The text of one node in a ui.inspect tree, by automation id.

        `ui.inspect` answers with a TREE (root, node_count, truncated, depth), not with the
        one element asked about, so the node has to be found. And a WPF TextBlock has no
        Value pattern: UI Automation exposes its text as the element's NAME, and
        UiAutomationInspector only writes `value` when a value pattern exists. Reading
        `value` alone would come back empty against a working application - which is the
        worst kind of failing check, because it reads like a product defect.
    #>
    param($Tree, [string]$AutomationId)
    if ($null -eq $Tree) { return $null }
    $root = $null
    if ($Tree.PSObject.Properties.Name -contains "root") { $root = $Tree.root } else { $root = $Tree }
    $stack = New-Object System.Collections.Stack
    $stack.Push($root)
    while ($stack.Count -gt 0) {
        $node = $stack.Pop()
        if ($null -eq $node) { continue }
        $names = $node.PSObject.Properties.Name
        if (($names -contains "automation_id") -and ($node.automation_id -eq $AutomationId)) {
            if (($names -contains "name") -and $node.name) { return [string]$node.name }
            if (($names -contains "value") -and $node.value) { return [string]$node.value }
            return ""
        }
        if ($names -contains "children") {
            foreach ($child in @($node.children)) { $stack.Push($child) }
        }
    }
    return $null
}

function Invoke-NativeAppSection {
    <#  M28: an application this system BUILT, launched and driven like any other.

        The lab (scripts\tests\native-windows-lab.py) already proves the build end to end
        and validates the artefact with an independent reader; what it cannot do is launch
        it, because launching belongs to M19 and M19 needs this runtime. So this section
        starts where the lab stops: it builds a fresh EXE, then treats it as an application.
    #>
    param([string]$FixtureRoot)
    $section = New-Section -Name "nativeapp" -Milestone "M28" -Title "an application this system built, launched and driven"
    $failures = 0

    $repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $python = Join-Path $repoRoot "services\api\.venv\Scripts\python.exe"
    $lab = Join-Path $repoRoot "scripts\tests\native-windows-lab.py"
    $exe = $null

    if ($dry) {
        Add-Check -Section $section.name -Name "native.build.planned" -Ok $true `
            -Detail "would run $lab --keep and read the EXE path out of its evidence file"
    }
    elseif (-not (Test-Path $python) -or -not (Test-Path $lab)) {
        Add-Check -Section $section.name -Name "native.build.lab_present" -Ok $false `
            -Detail "the native lab or its interpreter is missing from this checkout"
        Close-Section -Section $section -Verdict "BLOCKED" -Detail "nothing to build"
        return $section
    }
    else {
        # BUILD. The real pipeline, the real compiler, the real independent reader - the
        # same script the milestone's evidence comes from, so this is not a second path.
        #
        # Built INTO THE NATIVE ROOT, because the device can only start what lies inside one
        # of the owner's authorised roots. The first live run of this section built a perfect
        # 162,304-byte EXE into %TEMP% and `file.open` refused it with permission_denied
        # (2026-09-09) - the device was right, and the artefact was simply somewhere nothing
        # was allowed to reach. The native root is where the protocol says a compiler's output
        # is "read back from" (DEVICE_PROTOCOL.md 6n), so this widens no authority at all.
        $nativeRoot = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "PagentOS Projects\native"
        $buildDir = Join-Path $nativeRoot "item28-build"
        New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
        $labOut = & $python $lab --workdir $buildDir 2>&1
        $labExit = $LASTEXITCODE
        $keptLine = ($labOut | Where-Object { $_ -match "kept_at|notlarim\.exe" } | Select-Object -First 1)
        Add-Check -Section $section.name -Name "native.build.exe_produced" -Ok ($labExit -eq 0) `
            -Detail "the lab exited $labExit; $keptLine"
        if ($labExit -ne 0) {
            Close-Section -Section $section -Verdict "FAILED" -Detail "the build did not produce an artefact"
            return $section
        }

        $evidenceDir = Join-Path $repoRoot "docs\evidence"
        $newest = Get-ChildItem $evidenceDir -Filter "m28-native-windows-lab-*.json" -ErrorAction SilentlyContinue |
                  Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($newest) {
            $ev = Get-Content $newest.FullName -Raw | ConvertFrom-Json
            $kept = $ev.kept_at
            if ($kept) { $exe = Join-Path $kept "notlarim.exe" }
            $facts = $ev.artifact.facts
            Add-Check -Section $section.name -Name "native.build.independently_validated" -Ok ([bool]$ev.artifact.ok) `
                -Detail "the reader saw $($facts.architecture) $($facts.subsystem) version $($facts.version), sha256 $($facts.sha256.Substring(0,16))"
            if (-not $ev.artifact.ok) { $failures++ }
        }
        if (-not $exe -or -not (Test-Path $exe)) {
            Add-Check -Section $section.name -Name "native.build.artifact_on_disk" -Ok $false `
                -Detail "the lab reported success but no EXE was kept where the evidence names"
            Close-Section -Section $section -Verdict "FAILED" -Detail "nothing to launch"
            return $section
        }
    }

    # LAUNCH, through `file.open` rather than `app.launch`, and the difference is the point.
    #
    # `app.launch` allowlists a NAME, or an absolute path under Program Files / Windows, so
    # it cannot start a freshly built application. `file.open` takes `payload.path` through
    # RequireAuthorisedPath - resolve-then-contain against the owner's authorised roots -
    # and with no `application` runs it with ShellExecute, answering with pid, window_id and
    # the window it observed. The native root IS an authorised root (the M28 device work
    # added it: "where a compiler runs and where the artefact it produced is read back
    # from"), so this needs no device change and widens no authority: the file must already
    # be inside a root the owner authorised, which is the same root a compiler runs in.
    # PowerShell 5.1: no null-coalescing operator, on purpose (docs/OWNER_ACTIONS.md).
    $launchTarget = if ($exe) { $exe } else { "<the freshly built EXE>" }
    $launch = Invoke-DeviceCapability -Section $section -Capability "file.open" -Payload @{ path = $launchTarget } -AllowFailure
    if (-not $dry -and -not $launch.Ok) {
        Add-Check -Section $section.name -Name "native.launch.started_from_the_authorised_root" -Ok $false `
            -Detail "file.open refused the built application with '$($launch.ErrorClass)': $($launch.Message)"
        Close-Section -Section $section -Verdict "FAILED" -Detail "built and validated; the device would not start it"
        return $section
    }

    $launchPid = Get-ResultField -Result $launch.Result -Name "pid"
    $windowId = [string](Get-ResultField -Result $launch.Result -Name "window_id")
    if (-not $dry -and -not $windowId -and $launchPid) {
        # file.open waits only three seconds for a window, and a self-contained WPF
        # application's first cold start can take longer. A missing window_id here is a
        # slow start, not a failure - so ask again by pid rather than concluding anything.
        Start-Sleep -Seconds 5
        $listed = Invoke-DeviceCapability -Section $section -Capability "window.list" -Payload @{ pid = [int]$launchPid } -AllowFailure
        $windows = Get-ResultField -Result $listed.Result -Name "windows"
        if ($windows -and @($windows).Count -gt 0) { $windowId = [string](@($windows)[0].window_id) }
    }
    Add-Check -Section $section.name -Name "native.launch.window_observed" -Ok ($dry -or ($launch.Ok -and $windowId)) `
        -Detail "pid=$launchPid window_id=$windowId"

    # DRIVE. Type a note, press the button, and read the COUNT back out of the application's
    # own status line - not out of the list, which would be counting our own typing.
    $note = "item28 dogrulama notu"
    [void](Invoke-DeviceCapability -Section $section -Capability "ui.set_value" -Payload @{ window_id = $windowId; automation_id = "NoteInput"; value = $note })
    [void](Invoke-DeviceCapability -Section $section -Capability "ui.invoke" -Payload @{ window_id = $windowId; automation_id = "AddButton" })
    $status = Invoke-DeviceCapability -Section $section -Capability "ui.inspect" -Payload @{ window_id = $windowId; depth = 6; max_nodes = 200 } -AllowFailure
    $statusText = [string](Get-UiNodeText -Tree $status.Result -AutomationId "StatusText")
    $driveOk = $dry -or ($status.Ok -and $statusText -match "\d+\s+not")
    Add-Check -Section $section.name -Name "native.drive.status_line_read_back" -Ok $driveOk `
        -Detail "the application's own status line says '$statusText' after one note was added through UI Automation"
    if (-not $driveOk) { $failures++ }

    # RELAUNCH. Close it, start it again, and require the note to still be there - the
    # persistence the generated project's own tests prove headlessly, now through a window.
    [void](Invoke-DeviceCapability -Section $section -Capability "window.close" -Payload @{ window_id = $windowId } -AllowFailure)
    Start-Sleep -Seconds 2
    $relaunch = Invoke-DeviceCapability -Section $section -Capability "file.open" -Payload @{ path = $launchTarget } -AllowFailure
    $reWindow = [string](Get-ResultField -Result $relaunch.Result -Name "window_id")
    $rePid = Get-ResultField -Result $relaunch.Result -Name "pid"
    if (-not $dry -and -not $reWindow -and $rePid) {
        Start-Sleep -Seconds 5
        $reListed = Invoke-DeviceCapability -Section $section -Capability "window.list" -Payload @{ pid = [int]$rePid } -AllowFailure
        $reWindows = Get-ResultField -Result $reListed.Result -Name "windows"
        if ($reWindows -and @($reWindows).Count -gt 0) { $reWindow = [string](@($reWindows)[0].window_id) }
    }
    $reStatus = Invoke-DeviceCapability -Section $section -Capability "ui.inspect" -Payload @{ window_id = $reWindow; depth = 6; max_nodes = 200 } -AllowFailure
    $reText = [string](Get-UiNodeText -Tree $reStatus.Result -AutomationId "StatusText")
    $persistOk = $dry -or ($reStatus.Ok -and $reText -match "[1-9]\d*\s+not")
    Add-Check -Section $section.name -Name "native.relaunch.note_survived" -Ok $persistOk `
        -Detail "after a close and a fresh launch the status line says '$reText'"
    if (-not $persistOk) { $failures++ }

    # THE APPLICATION'S OWN LOG. A crash the owner never saw is still evidence (spec §5).
    # Beside the executable, which for this factory's output is inside the authorised roots.
    # LOCALAPPDATA is NOT one of them (Documents, Desktop, Downloads, Pictures, Videos,
    # Music, the fixture root, Projects, 3d, native), so a log written there could never be
    # read back - which is why the template keeps its data beside the binary instead.
    $logDir = if ($exe) { Join-Path (Split-Path -Parent $exe) "data" } else { "<the app data dir>" }
    $logPath = Join-Path $logDir "app.log"
    $logRead = Invoke-DeviceCapability -Section $section -Capability "file.read" -Payload @{ path = $logPath } -AllowFailure
    $logText = [string](Get-ResultField -Result $logRead.Result -Name "text")
    $logOk = $dry -or ($logRead.Ok -and $logText -match "started")
    Add-Check -Section $section.name -Name "native.log.read_back" -Ok $logOk `
        -Detail "the application's own log under its data directory carries its startup line"
    if (-not $logOk) { $failures++ }

    [void](Invoke-DeviceCapability -Section $section -Capability "window.close" -Payload @{ window_id = $reWindow } -AllowFailure)

    if ($dry) { Close-Section -Section $section -Verdict "PLANNED" -Detail "payloads recorded; nothing sent" }
    elseif ($failures -eq 0) { Close-Section -Section $section -Verdict "PROVEN_REAL" -Detail "built, validated, launched, driven, relaunched with its state intact, and its own log read" }
    else { Close-Section -Section $section -Verdict "FAILED" -Detail "$failures step(s) did not answer correctly" }
    return $section
}

function Invoke-AmbientSection {
    <#
    .SYNOPSIS
        M18.3: the display and activity device path, and the ambient policy - without ever
        darkening a screen.
    .DESCRIPTION
        desktop.display_off blanks the owner's monitors, and only a person can see that it
        happened and that a keypress undoes it. That step therefore stays READY_FOR_OWNER and
        names the harness that runs it properly (owner-m18-3-display.ps1, which arms the real
        production receipt through POST /v1/ambient/test-display and warns first). What can
        be proved without a human is proved here: the device reports its display state and
        its idle counter, a wake is a real, acknowledged receipt (waking a display that is
        already on changes nothing the owner can notice), and Cloud Core's ambient policy
        answers for itself.
    #>
    $section = New-Section -Name "ambient" -Milestone "M18.3" -Title "display, activity and the ambient policy (nothing is darkened)"
    $failures = 0

    $status = Invoke-DeviceCapability -Section $section -Capability "desktop.display_status" -Payload @{}
    $activity = Invoke-DeviceCapability -Section $section -Capability "desktop.activity_status" -Payload @{}
    $wake = Invoke-DeviceCapability -Section $section -Capability "desktop.display_wake" -Payload @{ reason = "item28_qualification" }

        $state = [string](Get-ResultField -Result $status.Result -Name "observed_state")
        Add-Check -Section $section.name -Name "desktop.display_status.observed_a_real_state" -Ok ($status.Ok -and $state) -Detail "observed_state=$state"
        if (-not ($status.Ok -and $state)) { $failures++ }

        $keys = @()
        if ($null -ne $activity.Result) { $keys = @($activity.Result.PSObject.Properties.Name) }
        $expected = @("input_idle_s", "display_state", "display_observed_at", "alarm_ringing", "ringing_alarm_id", "armed_alarms", "next_alarm_at")
        $missing = @($expected | Where-Object { $keys -notcontains $_ })
        Add-Check -Section $section.name -Name "desktop.activity_status.carries_exactly_the_seven_documented_keys" -Ok ($activity.Ok -and @($missing).Count -eq 0) `
            -Detail "$(@($keys).Count) key(s)$(if (@($missing).Count) { "; MISSING $($missing -join ', ')" })"
        if (-not ($activity.Ok -and @($missing).Count -eq 0)) { $failures++ }

        $woken = Get-ResultField -Result $wake.Result -Name "woken"
        Add-Check -Section $section.name -Name "desktop.display_wake.is_a_receipt_not_a_no_op" -Ok ($wake.Ok -and [bool]$woken) `
            -Detail "woken=$woken method=$(Get-ResultField -Result $wake.Result -Name 'method')"
        if (-not ($wake.Ok -and [bool]$woken)) { $failures++ }

        $policy = Get-JsonOrNull -Path "/v1/ambient/policy"
        Add-Check -Section $section.name -Name "ambient.policy.answers_for_itself" -Ok ($null -ne $policy) `
            -Detail $(if ($null -ne $policy) { (ConvertTo-Json -InputObject $policy -Compress -Depth 3) } else { "GET /v1/ambient/policy did not answer" })
        if ($null -eq $policy) { $failures++ }

        Add-ReadyForOwner -Section $section.name -What "displays actually going dark, and one keypress waking them" `
            -Why "only a person can see a blank screen and press a key; this script never darkens a display" `
            -Harness "scripts\core\owner-m18-3-display.ps1 (arms the real receipt through POST /v1/ambient/test-display and warns first)"
    if ($dry) { Close-Section -Section $section -Verdict "PLANNED" -Detail "payloads recorded; nothing sent" }
    elseif ($failures -eq 0) { Close-Section -Section $section -Verdict "PROVEN_REAL" -Detail "display state, the activity contract, a wake receipt and the policy" }
    else { Close-Section -Section $section -Verdict "FAILED" -Detail "$failures step(s) did not answer correctly" }
    return $section
}

function Invoke-AlarmSection {
    <#
    .SYNOPSIS
        M18.3: the alarm DEVICE path - armed, seen in the device's own status, disarmed.
        Silently.
    .DESCRIPTION
        desktop.alarm_arm only registers an instant; nothing rings until the clock reaches it
        and desktop.alarm_start is issued. Arming an alarm an hour away, watching
        desktop.activity_status change to match, and disarming it again therefore exercises
        the whole device-side arm/disarm contract without a sound. Ringing (alarm_start /
        play_audio) needs ears, so it stays READY_FOR_OWNER and names its harness.
    #>
    $section = New-Section -Name "alarm" -Milestone "M18.3" -Title "the alarm device path, armed and disarmed in silence"
    $failures = 0
    $alarmId = "item28-" + [guid]::NewGuid().ToString("N").Substring(0, 12)
    $fireAt = (Get-Date).ToUniversalTime().AddHours(1).ToString("o")

    $before = Invoke-DeviceCapability -Section $section -Capability "desktop.activity_status" -Payload @{}
    $arm = Invoke-DeviceCapability -Section $section -Capability "desktop.alarm_arm" -Payload @{ alarm_id = $alarmId; fire_at = $fireAt; grace_s = 60; label = "item 28 qualification (never rings)" }
    $after = Invoke-DeviceCapability -Section $section -Capability "desktop.activity_status" -Payload @{}
    $disarm = Invoke-DeviceCapability -Section $section -Capability "desktop.alarm_disarm" -Payload @{ alarm_id = $alarmId }
    $final = Invoke-DeviceCapability -Section $section -Capability "desktop.activity_status" -Payload @{}

        $armedOk = $arm.Ok -and [bool](Get-ResultField -Result $arm.Result -Name "armed")
        Add-Check -Section $section.name -Name "desktop.alarm_arm.accepted_the_instant" -Ok $armedOk `
            -Detail "fire_at=$(Get-ResultField -Result $arm.Result -Name 'fire_at') armed_count=$(Get-ResultField -Result $arm.Result -Name 'armed_count')"
        if (-not $armedOk) { $failures++ }

        $countBefore = [int](Get-ResultField -Result $before.Result -Name "armed_alarms")
        $countAfter = [int](Get-ResultField -Result $after.Result -Name "armed_alarms")
        $seenOk = $after.Ok -and ($countAfter -gt $countBefore)
        Add-Check -Section $section.name -Name "desktop.activity_status.saw_the_alarm_the_arm_registered" -Ok $seenOk `
            -Detail "armed_alarms $countBefore -> $countAfter, next_alarm_at=$(Get-ResultField -Result $after.Result -Name 'next_alarm_at')"
        if (-not $seenOk) { $failures++ }

        $ringing = Get-ResultField -Result $after.Result -Name "alarm_ringing"
        Add-Check -Section $section.name -Name "arming_made_no_sound" -Ok (-not [bool]$ringing) -Detail "alarm_ringing=$ringing throughout"
        if ([bool]$ringing) { $failures++ }

        $countFinal = [int](Get-ResultField -Result $final.Result -Name "armed_alarms")
        $disarmOk = $disarm.Ok -and ($countFinal -eq $countBefore)
        Add-Check -Section $section.name -Name "desktop.alarm_disarm.left_the_device_as_it_found_it" -Ok $disarmOk `
            -Detail "armed_alarms back to $countFinal (was $countBefore before this run)"
        if (-not $disarmOk) { $failures++ }

        Add-ReadyForOwner -Section $section.name -What "an alarm actually ringing, and the wake music" `
            -Why "only a person can hear it; this script never issues desktop.alarm_start or desktop.play_audio" `
            -Harness 'scripts\core\owner-m18-3-alarm.ps1 -MusicUrl <your song> (creates a "test" alarm through Cloud Core and warns first)'
        Add-ReadyForOwner -Section $section.name -What "desktop.play_audio on the real render endpoint" `
            -Why "it needs a WAV served from the dialled broker origin with its sha256; level:0 is the silent lever if a later run wants it" `
            -Harness "scripts\core\owner-m18-3-alarm.ps1"
    if ($dry) { Close-Section -Section $section -Verdict "PLANNED" -Detail "payloads recorded; nothing sent" }
    elseif ($failures -eq 0) { Close-Section -Section $section -Verdict "PROVEN_REAL" -Detail "arm, observe, disarm - and the device was left exactly as it was found" }
    else { Close-Section -Section $section -Verdict "FAILED" -Detail "$failures step(s) did not answer correctly" }
    return $section
}

# ================================================================================= run

$exitCode = 1
$fixtureRoot = ""
try {
    Write-Host "PagentOS - everything owner item 28 unlocks ($runId)" -ForegroundColor Cyan
    Write-Host "  mode      : $(if ($dry) { 'DRY RUN - nothing is sent' } else { 'live' })"
    Write-Host "  cloud     : $BaseUrl"
    Write-Host "  install   : $InstallRoot"
        Write-Host ""
        Write-Host "  This run opens and closes a few application windows on your desktop." -ForegroundColor Yellow
        Write-Host "  It never blanks a display and never makes a sound; anything that would is" -ForegroundColor Yellow
        Write-Host "  written into the evidence as READY_FOR_OWNER instead." -ForegroundColor Yellow

    # ------------------------------------------------------------------- phase 0: refuse
    Write-Host ""
    Write-Host "== gate  the installed runtime, before anything is claimed about it" -ForegroundColor Cyan
    $runtime = Get-InstalledRuntimeIdentity -Root $InstallRoot
    $evidence.runtime = $runtime
    Write-Host "  installed service : $(if ($runtime.present) { "$($runtime.product_version) (written $($runtime.installed_at))" } else { 'NOT INSTALLED' })"
    $gate = Test-RuntimeUnlocked -Runtime $runtime
    $advertised = 0
    if ($null -ne $gate.Manifest -and $gate.Manifest.Ok) { $advertised = @($gate.Manifest.Capabilities).Count }
    $operatorMissing = @()
    if ($advertised -gt 0) { $operatorMissing = @($script:OperatorProbe | Where-Object { @($gate.Manifest.Capabilities) -notcontains $_ }) }
    $evidence.gate = [ordered]@{
        ok                    = $gate.Ok
        reasons               = @($gate.Reasons)
        advertised            = $advertised
        minimum               = $script:MinimumCapabilities
        superseded_build      = $script:SupersededBuild
        operator_families_missing = @($operatorMissing)
    }
    Write-Host "  advertises        : $advertised capabilities (item 28 promises at least $script:MinimumCapabilities)"

    if (-not $gate.Ok) {
        foreach ($reason in $gate.Reasons) { Write-Host "  $reason" -ForegroundColor Red }
        Write-Host ""
        Write-Host "REFUSED: this run would have measured the release item 28 replaces. Nothing was sent." -ForegroundColor Red
        Write-Host "  Run the qualification, then the one elevated command (docs/OWNER_ACTIONS.md item 28):"
        Write-Host "    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\qualify-staged-update.ps1"
        Write-Host "    .\scripts\install-device-service.ps1 -DisplayPower -Operator"
        $evidence.verdict = "BLOCKED"
        if ($dry) {
            # A dry run is how this script is qualified before the runtime it needs exists, so
            # it still walks every section - in plan mode, sending nothing - and records the
            # complete list of capability calls it WOULD make. The verdict stays BLOCKED: a
            # plan is never evidence about a device.
            Write-Host ""
            Write-Host "DRY RUN: the refusal above is the correct answer against this runtime, and it is what this script did." -ForegroundColor Green
            Write-Host "Walking every section in plan mode so the payloads themselves can be read:" -ForegroundColor Green
            $fixtureRoot = Copy-DocumentFixtures -RunId $runId
            Write-Host "  fixtures          : $fixtureRoot (inside an authorised root)"
            [void](Invoke-OperatorSection)
            [void](Invoke-DocumentsSection -FixtureRoot $fixtureRoot)
            [void](Invoke-ProjectsSection)
            [void](Invoke-SceneSection)
            [void](Invoke-PaintSection -FixtureRoot $fixtureRoot)
            [void](Invoke-NativeAppSection -FixtureRoot $fixtureRoot)
            [void](Invoke-AmbientSection)
            [void](Invoke-AlarmSection)
            $exitCode = 0
        }
        else { $exitCode = 2 }
    }
    else {
        Add-Check -Section "gate" -Name "runtime.is_not_the_superseded_build" -Ok $true -Detail "$($runtime.product_version)"
        Add-Check -Section "gate" -Name "runtime.advertises_the_unlocked_manifest" -Ok $true -Detail "$advertised capabilities"
        if (@($operatorMissing).Count -gt 0) {
            Add-Check -Section "gate" -Name "runtime.carries_the_operator_families" -Ok $false `
                -Detail "the install ran without -Operator: MISSING $($operatorMissing -join ', ')"
        }

        # -------------------------------------------------------------- the owner session
        $token = ""
            $credential = $null
            try { $credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL" } catch { $credential = $null }
            if (-not $credential) {
                throw "no owner credential is stored on this machine (PAGENTOS_OWNER_CREDENTIAL, DPAPI). Run scripts\bootstrap-owner-credential.ps1 once; this script never prompts, so it does not block."
            }
            try {
                $body = @{ owner_credential = $credential; client_kind = "cli"; label = $runId } | ConvertTo-Json -Compress
                $issued = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body $body -TimeoutSec 30
                $token = [string]$issued.token
            }
            finally { $credential = $null; $body = $null }
            $script:Headers = @{ Authorization = "Bearer $token" }

            $listing = Get-Json -Path "/v1/devices"
            $chosen = $null
            foreach ($row in @($listing.devices)) {
                $caps = @($row.capabilities)
                if ($Device) {
                    if ([string]$row.device_id -eq $Device -or [string]$row.name -eq $Device) { $chosen = $row }
                }
                elseif ([string]$row.presence -eq "online" -and $caps.Count -ge $script:MinimumCapabilities) { $chosen = $row }
                if ($null -ne $chosen) { break }
            }
            if ($null -eq $chosen) { throw "no online device advertising at least $script:MinimumCapabilities capabilities; Cloud Core does not see the updated agent yet" }
            $script:DeviceId = [string]$chosen.device_id
            $evidence.device = [ordered]@{
                device_id = $script:DeviceId; name = [string]$chosen.name; presence = [string]$chosen.presence
                software_version = [string]$chosen.software_version; capabilities = @($chosen.capabilities).Count
            }
            Write-Host "  device            : $($chosen.name) ($script:DeviceId) $($chosen.presence), version $($chosen.software_version), $(@($chosen.capabilities).Count) capabilities"
            Add-Check -Section "gate" -Name "cloud_core.sees_the_updated_device" -Ok $true `
                -Detail "$($chosen.software_version) with $(@($chosen.capabilities).Count) capabilities"

        $fixtureRoot = Copy-DocumentFixtures -RunId $runId
        Write-Host "  fixtures          : $fixtureRoot (inside an authorised root)"

        [void](Invoke-OperatorSection)
        [void](Invoke-DocumentsSection -FixtureRoot $fixtureRoot)
        [void](Invoke-ProjectsSection)
        [void](Invoke-SceneSection)
        [void](Invoke-PaintSection -FixtureRoot $fixtureRoot)
        [void](Invoke-NativeAppSection -FixtureRoot $fixtureRoot)
        [void](Invoke-AmbientSection)
        [void](Invoke-AlarmSection)

        $failed = @($evidence.checks | Where-Object { -not $_.ok })
        if ($dry) { $evidence.verdict = "PLANNED"; $exitCode = 0 }
        elseif (@($failed).Count -eq 0) { $evidence.verdict = "PROVEN_REAL"; $exitCode = 0 }
        else { $evidence.verdict = "FAILED"; $exitCode = 1 }
    }
}
catch {
    Write-Host ""
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace
    $evidence.verdict = "ERROR"
    $evidence.checks += [ordered]@{ section = "run"; name = "run.completed"; ok = $false; detail = $_.Exception.Message }
    $exitCode = 1
}
finally {
    if ($fixtureRoot -and (Test-Path -LiteralPath $fixtureRoot)) {
        Remove-Item -LiteralPath (Split-Path -Parent $fixtureRoot) -Recurse -Force -ErrorAction SilentlyContinue
    }
    $evidence.finished_at = (Get-Date).ToUniversalTime().ToString("o")
    if (-not $OutFile) {
        $OutFile = Join-Path $repoRoot "docs\evidence\item28-unlocked-$(Get-Date -Format 'yyyyMMdd-HHmmss').json"
    }
    elseif (-not [System.IO.Path]::IsPathRooted($OutFile)) {
        $OutFile = Join-Path $repoRoot "docs\evidence\$OutFile"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutFile) | Out-Null
    [System.IO.File]::WriteAllText($OutFile, (ConvertTo-Json -InputObject $evidence -Depth 12) + "`n", (New-Object System.Text.UTF8Encoding($false)))
    Write-Host ""
    Write-Host "evidence: $OutFile"
}

$passed = @($evidence.checks | Where-Object { $_.ok }).Count
$failedCount = @($evidence.checks | Where-Object { -not $_.ok }).Count
Write-Host ""
switch ($evidence.verdict) {
    "PROVEN_REAL" { Write-Host "ITEM 28 UNLOCKED AND QUALIFIED: $passed checks passed across $(@($evidence.sections).Count) milestones." -ForegroundColor Green }
    "PLANNED" { Write-Host "DRY RUN COMPLETE: $(@($evidence.plan).Count) capability calls planned, none sent." -ForegroundColor Green }
    "BLOCKED" {
        Write-Host "ITEM 28 NOT RUN YET: the installed runtime is the one it replaces." -ForegroundColor Yellow
        if ($dry) { Write-Host "  (dry run: $(@($evidence.plan).Count) capability calls were planned and none sent; the refusal is the result.)" -ForegroundColor Yellow }
    }
    default { Write-Host "ITEM 28 QUALIFICATION $($evidence.verdict): $failedCount of $($passed + $failedCount) checks failed." -ForegroundColor Red }
}
if (@($evidence.blocked).Count -gt 0) {
    Write-Host "PREVENTED ($(@($evidence.blocked).Count)): measurements the environment did not allow - neither passes nor product failures." -ForegroundColor DarkYellow
    foreach ($b in @($evidence.blocked)) { Write-Host "  - $($b.name): $($b.detail)" -ForegroundColor DarkYellow }
}
if (@($evidence.ready_for_owner).Count -gt 0) {
    Write-Host "READY_FOR_OWNER ($(@($evidence.ready_for_owner).Count)): steps only a person can see or hear - each names its harness in the evidence."
}
exit $exitCode
