<#
.SYNOPSIS
    The one thing scripts\core\qualify-item28-unlocked.ps1 must never do: report success
    against the runtime item 28 replaces.

.DESCRIPTION
    That script exists to run itself the moment the owner's elevated install lands, with no
    further owner involvement. Its whole value rests on a refusal: if it ever printed a green
    report while the device was still the 2026-09-06 build (1.0.0+a3cb04e, 29 capabilities),
    the owner would have evidence for six milestones that was measured against the release
    the 2026-09-08 rollback restored - worse than no evidence at all.

    So these tests drive the real script, with no device, no Cloud Core and no elevation:

      * blocked, and it says WHY, naming the superseded build;
      * blocked in live mode exits NON-ZERO, so a scheduled or chained run cannot mistake
        the refusal for a pass;
      * blocked in dry-run mode exits ZERO - that is the mode that qualifies the script
        itself before the runtime it needs exists;
      * a dry run records a PLAN and records NO CHECKS, because a judgement computed over
        answers no device gave is not evidence;
      * the evidence file is written on every path, so a refusal is as recorded as a pass.

    Windows PowerShell 5.1. Nothing here touches the live install: every run is pointed at a
    directory that does not exist.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$PSNativeCommandUseErrorActionPreference = $false

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$script = Join-Path $repoRoot "scripts\core\qualify-item28-unlocked.ps1"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$sandbox = Join-Path $env:TEMP "pagentos-item28-gate-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $sandbox | Out-Null

$script:Passed = 0
$script:Failed = 0
function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) { $script:Passed++; Write-Host "  ok    $Message" }
    else { $script:Failed++; Write-Host "  FAIL  $Message" -ForegroundColor Red }
}

function Invoke-Qualification {
    <#  The real script, against an install root that does not exist. Returns exit code + evidence.  #>
    param([string]$Name, [switch]$Dry)
    $outFile = Join-Path $sandbox "$Name.json"
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $script,
        "-InstallRoot", (Join-Path $sandbox "no-such-install"),
        "-OutFile", $outFile
    )
    if ($Dry) { $arguments += "-DryRun" }
    $output = & $powershell @arguments 2>&1
    $code = $LASTEXITCODE
    $evidence = $null
    if (Test-Path -LiteralPath $outFile) { $evidence = Get-Content -LiteralPath $outFile -Raw | ConvertFrom-Json }
    return [pscustomobject]@{ ExitCode = $code; Evidence = $evidence; Output = ($output -join "`n"); OutFile = $outFile }
}

try {
    Write-Host "item 28 post-install qualification - the refusal"
    Assert-True (Test-Path -LiteralPath $script) "scripts\core\qualify-item28-unlocked.ps1 is in this checkout"

    # ------------------------------------------------------------------ the source itself
    $source = [System.IO.File]::ReadAllText($script)
    Assert-True ($source -match 'SupersededBuild\s*=\s*"a3cb04e"') "it names the superseded build it must never report against (a3cb04e)"
    Assert-True ($source -match 'MinimumCapabilities\s*=\s*40') "it names the capability floor item 28 promises (40)"
    Assert-True ($source -match 'install-device-service\.ps1 -DisplayPower -Operator') "and it prints the exact elevated command that fixes the refusal"
    # The refusal must come first: the gate is evaluated before anything can be sent.
    # The CALL SITE, not the function definition, which is necessarily earlier in the file.
    $gateAt = $source.IndexOf("Test-RuntimeUnlocked -Runtime")
    $sendAt = $source.IndexOf("[void](Invoke-OperatorSection)")
    Assert-True ($gateAt -gt 0 -and $sendAt -gt $gateAt) "the runtime gate is evaluated before any section is invoked"

    # ------------------------------------------------------------------------- live mode
    Write-Host ""
    Write-Host "live mode, no runtime installed"
    $live = Invoke-Qualification -Name "live"
    Assert-True ($live.ExitCode -ne 0) "a blocked live run exits NON-ZERO (got $($live.ExitCode)), so nothing downstream reads it as a pass"
    Assert-True ($live.Output -match "REFUSED") "...and says REFUSED in as many words"
    Assert-True ($null -ne $live.Evidence) "the evidence file is written even when the run refuses"
    if ($null -ne $live.Evidence) {
        Assert-True ($live.Evidence.verdict -eq "BLOCKED") "the verdict is BLOCKED, never PROVEN_REAL ($($live.Evidence.verdict))"
        Assert-True (@($live.Evidence.gate.reasons).Count -gt 0) "the gate records why: $(@($live.Evidence.gate.reasons) -join '; ')"
        Assert-True ($live.Evidence.gate.minimum -eq 40) "the evidence carries the floor it judged against"
        Assert-True (@($live.Evidence.sections).Count -eq 0) "no section ran, so no milestone can be claimed"
    }

    # -------------------------------------------------------------------------- dry run
    Write-Host ""
    Write-Host "dry run, no runtime installed"
    $dry = Invoke-Qualification -Name "dry" -Dry
    Assert-True ($dry.ExitCode -eq 0) "a blocked dry run exits CLEANLY (got $($dry.ExitCode)) - this is how the script is qualified before the runtime exists"
    Assert-True ($dry.Output -match "REFUSED") "...while still refusing, in the same words"
    if ($null -ne $dry.Evidence) {
        Assert-True ($dry.Evidence.verdict -eq "BLOCKED") "the verdict is still BLOCKED, not PLANNED-as-success ($($dry.Evidence.verdict))"
        Assert-True ($dry.Evidence.mode -eq "dry-run") "the evidence says which mode produced it"
        $plan = @($dry.Evidence.plan)
        Assert-True ($plan.Count -gt 30) "it plans the whole run rather than the first step only ($($plan.Count) entries)"
        $capabilities = @($plan | Where-Object { $_.PSObject.Properties.Name -contains "capability" } | ForEach-Object { [string]$_.capability })
        foreach ($required in @("app.launch", "window.move", "ui.inspect", "file.search", "document.extract", "project.scaffold", "scene.inspect", "desktop.alarm_arm", "desktop.alarm_disarm")) {
            Assert-True ($capabilities -contains $required) "the plan covers $required"
        }
        Assert-True ($capabilities -notcontains "desktop.display_off") "the plan NEVER darkens a display"
        Assert-True ($capabilities -notcontains "desktop.alarm_start" -and $capabilities -notcontains "desktop.play_audio") "...and never makes a sound"
        # A dry run judges nothing: the answers it saw came from no device.
        Assert-True (@($dry.Evidence.checks).Count -eq 0) "a dry run records NO checks - a judgement over answers no device gave is not evidence"
        # Every call is attached to the SECTION it belongs to, not only to the flat plan.
        # This is the guard for a PowerShell trap that produced exactly nothing visible: a
        # section is an [ordered] dictionary, and binding one to a [hashtable] parameter
        # CONVERTS it, so the callee mutates a copy and every step record is lost.
        $sectionsWithSteps = @(@($dry.Evidence.sections) | Where-Object { @($_.steps).Count -gt 0 })
        Assert-True (@($sectionsWithSteps).Count -eq @($dry.Evidence.sections).Count) `
            "every section carries its own calls ($(@($sectionsWithSteps).Count) of $(@($dry.Evidence.sections).Count)) - the section is the object the recorder writes into, not a copy of it"
        $stepTotal = 0
        foreach ($s in @($dry.Evidence.sections)) { $stepTotal += @($s.steps).Count }
        $planCalls = @(@($dry.Evidence.plan) | Where-Object { $_.PSObject.Properties.Name -contains "capability" }).Count
        Assert-True ($stepTotal -eq $planCalls) "and the sections' calls and the flat plan are the same $planCalls calls, counted twice"
    }
    else { Assert-True $false "the dry run wrote no evidence file" }

    # ------------------------------------------------- the harnesses it defers to must exist
    Write-Host ""
    Write-Host "what it refuses to do itself"
    foreach ($harness in @("scripts\core\owner-m18-3-display.ps1", "scripts\core\owner-m18-3-alarm.ps1")) {
        Assert-True (Test-Path -LiteralPath (Join-Path $repoRoot $harness)) "$harness exists, so a READY_FOR_OWNER row points somewhere real"
        Assert-True ($source -match [regex]::Escape($harness)) "...and the script names it"
    }
}
finally {
    Remove-Item -LiteralPath $sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

# --------------------------------- the two halves of the launch, held against each other
# The qualification asks the device to start what it built; the device decides whether it may.
# Both sides can be green while they drift apart, and on 2026-09-09 they did: the script sent
# `file.open`, which refuses executables BY NAME, and the device's own rule knew only Program
# Files. So each half is asserted against the OTHER FILE's source here.
Write-Host ""
Write-Host "the launch, as both halves spell it"

$qualifier = Get-Content -LiteralPath (Join-Path $repoRoot "scripts\core\qualify-item28-unlocked.ps1") -Raw
$launchSites = @([regex]::Matches($qualifier, 'Capability "app\.launch" -Payload @\{ application = \$launchTarget')).Count
Assert-True ($launchSites -eq 2) `
    "BOTH the launch and the relaunch start the built application with app.launch and an absolute path ($launchSites site(s)) - a count, because a single -match is satisfied by either one of them alone"
Assert-True (-not ($qualifier -match 'Capability "file\.open" -Payload @\{ path = \$launchTarget')) `
    "...and never with file.open, which answers 'file.open opens documents, app.launch runs programs'"

$companion = Get-Content -LiteralPath (Join-Path $repoRoot "devices\windows-agent\src\PagentOS.SessionCompanion\Operator\OperatorCapabilities.cs") -Raw
Assert-True ($companion -match "ResolveNativeBuiltExecutable") `
    "the device half exists: app.launch resolves an application built under the native root (ADR-0098)"
Assert-True ($companion -match "IsWithin\(resolvedExe, resolvedRoot\)") `
    "...by resolve-then-contain against that ONE root, not a prefix compare"
Assert-True ($companion -match "EffectiveProjectsRootNative") `
    "...and the root it uses is the configured native root, not a literal path"

# Every ui.inspect payload this script sends must stay inside the bounds the COMPANION
# declares, read from its own source rather than restated here. Asking for depth 6 against a
# MaxDepth of 5 is a validation_error raised before the device touches a window, and it cost
# two steps of the M28 section on its first live run.
$inspector = Get-Content -LiteralPath (Join-Path $repoRoot "devices\windows-agent\src\PagentOS.SessionCompanion\Operator\UiAutomationInspector.cs") -Raw
$maxDepth = 0
if ($inspector -match "public const int MaxDepth = (\d+);") { $maxDepth = [int]$Matches[1] }
$maxNodes = 0
if ($inspector -match "public const int MaxNodes = (\d+);") { $maxNodes = [int]$Matches[1] }
Assert-True ($maxDepth -gt 0 -and $maxNodes -gt 0) "the companion's own inspector bounds were read from its source (MaxDepth=$maxDepth, MaxNodes=$maxNodes)"
$depths = @([regex]::Matches($qualifier, "depth = (\d+)") | ForEach-Object { [int]$_.Groups[1].Value })
$nodes = @([regex]::Matches($qualifier, "max_nodes = (\d+)") | ForEach-Object { [int]$_.Groups[1].Value })
Assert-True (@($depths).Count -ge 3) "the qualification sends ui.inspect with an explicit depth ($(@($depths).Count) site(s))"
Assert-True (@($depths | Where-Object { $_ -gt $maxDepth }).Count -eq 0) "no ui.inspect asks for a depth beyond the companion's MaxDepth of $maxDepth (asked: $($depths -join ', '))"
Assert-True (@($nodes | Where-Object { $_ -gt $maxNodes }).Count -eq 0) "and none asks for more nodes than its MaxNodes of $maxNodes (asked: $($nodes -join ', '))"

$lab = Get-Content -LiteralPath (Join-Path $repoRoot "scripts\tests\native-windows-lab.py") -Raw
Assert-True ($lab -match "--workdir") `
    "the lab can build where the device can reach, instead of only in a temp directory"
Assert-True ($qualifier.Contains('"PagentOS Projects\native"')) `
    "and the qualification points it at the native root"


Write-Host ""
if ($script:Failed -eq 0) { Write-Host "item28-gate tests: $script:Passed passed, 0 failed" -ForegroundColor Green; exit 0 }
Write-Host "item28-gate tests: $script:Passed passed, $script:Failed failed" -ForegroundColor Red
exit 1
