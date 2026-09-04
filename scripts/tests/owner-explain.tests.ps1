<#
.SYNOPSIS
    The owner voice qualification harness (owner-explain.ps1): web shell readiness and
    realtime-session correlation, decided by scripts\lib\VoiceShell.ps1 with fakes.

.DESCRIPTION
    Owner incident (2026-09-04): the harness started the web shell without waiting for it,
    then required a realtime session "created after the script started" and failed, while
    the product itself worked once the owner started the shell by hand. The rules encoded
    here, with no network, no processes and no clock:

      1. web shell absent at start: the probe fails, the start is requested, the wait ends
         READY only when the probe answers - and NOT READY within the budget when it never does;
      2. web shell already running: the probe answers at once, nothing is started;
      3. delayed owner connection: the qualification session appears only on a later poll
         and is still detected, within the budget;
      4. stale older realtime session present: a session that existed before the run, or
         started before the shell was ready, or without a succeeded activity.explain, never
         qualifies - even when it is the newest one listed;
      5. when several new sessions qualify, the newest wins; identity is unambiguous.

    Run: powershell -NoProfile -File scripts\tests\owner-explain.tests.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\VoiceShell.ps1")

$script:Failures = 0
$script:Passes = 0

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Passes++; Write-Host "  PASS  $Name" }
    catch { $script:Failures++; Write-Host "  FAIL  $Name" -ForegroundColor Red; Write-Host "        $($_.Exception.Message)" -ForegroundColor Red }
}
function Assert-True { param([bool]$Condition, [string]$Message) if (-not $Condition) { throw $Message } }
function Assert-Equal { param($Expected, $Actual, [string]$Message) if ("$Expected" -ne "$Actual") { throw "$Message (expected '$Expected', got '$Actual')" } }

function New-FakeClock {
    <#  A clock that advances only when the fake Sleep is called.  #>
    $state = @{ Now = 1000.0; Slept = @() }
    return [pscustomobject]@{
        State = $state
        Clock = { $state.Now }.GetNewClosure()
        Sleep = { param($Seconds) $state.Now += $Seconds; $state.Slept += $Seconds }.GetNewClosure()
    }
}

function New-Session {
    param([string]$Id, [string]$StartedAt, [string]$Kind = "web", [string]$State = "active")
    return [pscustomobject]@{ session_id = $Id; client_kind = $Kind; started_at = $StartedAt; state = $State }
}

function New-Activity {
    param([string]$Id, [bool]$Explained = $true)
    $calls = @()
    if ($Explained) { $calls += [pscustomobject]@{ name = "activity.explain"; status = "succeeded" } }
    $calls += [pscustomobject]@{ name = "narration.control"; status = "succeeded" }
    return [pscustomobject]@{ session_id = $Id; tool_calls = $calls }
}

$readyAt = [datetime]::Parse("2026-09-04T20:00:00Z", [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AdjustToUniversal)

Write-Host "owner-explain harness rules"

Test-Case "1. web shell absent at start: the wait ends READY only once the probe answers" {
    $fake = New-FakeClock
    $answers = @($false, $false, $true)
    $index = @{ i = 0 }
    $probe = { param($Url) $i = $index.i; $index.i++; if ($answers[[math]::Min($i, $answers.Count - 1)]) { return 200 } else { throw "connection refused" } }.GetNewClosure()
    $result = Wait-WebShellReady -Url "http://localhost:3000/voice" -TimeoutSec 120 -IntervalSec 2 -Probe $probe -Sleep $fake.Sleep -Clock $fake.Clock
    Assert-True $result.Ready "shell must be reported ready once the probe answers"
    Assert-Equal 3 $result.Attempts "attempts until ready"
    Assert-Equal 4 $result.ElapsedSec "two sleeps of two seconds before the third probe"
}

Test-Case "1b. web shell never answers: NOT READY within the budget, never an endless wait" {
    $fake = New-FakeClock
    $probe = { param($Url) throw "connection refused" }
    $result = Wait-WebShellReady -Url "http://localhost:3000/voice" -TimeoutSec 30 -IntervalSec 5 -Probe $probe -Sleep $fake.Sleep -Clock $fake.Clock
    Assert-True (-not $result.Ready) "must give up"
    Assert-True ($result.ElapsedSec -ge 30) "must have spent the budget"
    Assert-Equal 7 $result.Attempts "one probe per five seconds over thirty seconds"
}

Test-Case "2. web shell already running: ready on the first probe, nothing to start" {
    $fake = New-FakeClock
    $probe = { param($Url) return 200 }
    $result = Wait-WebShellReady -Url "http://localhost:3000/voice" -TimeoutSec 120 -Probe $probe -Sleep $fake.Sleep -Clock $fake.Clock
    Assert-True $result.Ready "ready"
    Assert-Equal 1 $result.Attempts "no second probe"
    Assert-Equal 0 $fake.State.Slept.Count "no sleep at all"
    Assert-True (Test-WebShellReady -Url "http://localhost:3000/voice" -Probe { param($u) 307 }) "a redirect to sign-in still means the shell is up"
    Assert-True (-not (Test-WebShellReady -Url "http://localhost:3000/voice" -Probe { param($u) 502 })) "a gateway error is not ready"
}

Test-Case "3. delayed owner connection: the session appears on a later poll and is detected" {
    $fake = New-FakeClock
    $polls = @{ n = 0 }
    $list = {
        $polls.n++
        if ($polls.n -lt 4) { return @() }
        return @((New-Session -Id "new-1" -StartedAt "2026-09-04T20:03:00Z"))
    }.GetNewClosure()
    $probe = { param($Id) New-Activity -Id $Id }
    $result = Wait-QualificationSession -ListSessions $list -ActivityProbe $probe -BaselineIds @() -ReadyAt $readyAt -TimeoutSec 600 -IntervalSec 5 -Sleep $fake.Sleep -Clock $fake.Clock
    Assert-True ($null -ne $result.Selected) "the late session must be found"
    Assert-Equal "new-1" $result.Selected.SessionId "the late session"
    Assert-Equal 4 $result.Attempts "found on the fourth poll"
    Assert-Equal 15 $result.ElapsedSec "three waits of five seconds"
}

Test-Case "3b. no session within the budget: a bounded wait, then a clear null" {
    $fake = New-FakeClock
    $result = Wait-QualificationSession -ListSessions { @() } -ActivityProbe { param($Id) $null } -BaselineIds @() -ReadyAt $readyAt -TimeoutSec 20 -IntervalSec 5 -Sleep $fake.Sleep -Clock $fake.Clock
    Assert-True ($null -eq $result.Selected) "nothing selected"
    Assert-True ($result.ElapsedSec -ge 20) "budget spent"
}

Test-Case "4. a stale older session never qualifies, however recent or explained" {
    $stale = New-Session -Id "old-1" -StartedAt "2026-09-04T20:05:00Z"   # in the baseline
    $early = New-Session -Id "early-1" -StartedAt "2026-09-04T19:55:00Z" # before the shell was ready
    $silent = New-Session -Id "silent-1" -StartedAt "2026-09-04T20:04:00Z" # never asked to explain
    $probe = { param($Id) New-Activity -Id $Id -Explained ($Id -ne "silent-1") }
    $selected = Select-QualificationSession -Sessions @($stale, $early, $silent) -BaselineIds @("old-1") -ReadyAt $readyAt -ActivityProbe $probe
    Assert-True ($null -eq $selected) "none of them may qualify"
}

Test-Case "4b. a non-web client session from this run does not qualify by default" {
    $cli = New-Session -Id "cli-1" -StartedAt "2026-09-04T20:02:00Z" -Kind "cli"
    $selected = Select-QualificationSession -Sessions @($cli) -BaselineIds @() -ReadyAt $readyAt -ActivityProbe { param($Id) New-Activity -Id $Id }
    Assert-True ($null -eq $selected) "the owner speaks through the web shell in this qualification"
}

Test-Case "5. several new sessions: the newest explained one wins, unambiguously" {
    $first = New-Session -Id "new-1" -StartedAt "2026-09-04T20:01:00Z"
    $second = New-Session -Id "new-2" -StartedAt "2026-09-04T20:06:00Z"
    $third = New-Session -Id "new-3" -StartedAt "2026-09-04T20:09:00Z" # newest, but never explained
    $probe = { param($Id) New-Activity -Id $Id -Explained ($Id -ne "new-3") }
    $selected = Select-QualificationSession -Sessions @($third, $first, $second) -BaselineIds @() -ReadyAt $readyAt -ActivityProbe $probe
    Assert-True ($null -ne $selected) "one must be selected"
    Assert-Equal "new-2" $selected.SessionId "the newest session that actually explained"
    Assert-Equal 1 @($selected.Activity.tool_calls | Where-Object { $_.name -eq "activity.explain" }).Count "its activity is carried along"
}

Test-Case "6. the activity probe failing for one session does not hide the others" {
    $a = New-Session -Id "new-a" -StartedAt "2026-09-04T20:08:00Z"
    $b = New-Session -Id "new-b" -StartedAt "2026-09-04T20:07:00Z"
    $probe = { param($Id) if ($Id -eq "new-a") { throw "404" } ; New-Activity -Id $Id }
    $selected = Select-QualificationSession -Sessions @($a, $b) -BaselineIds @() -ReadyAt $readyAt -ActivityProbe $probe
    Assert-Equal "new-b" $selected.SessionId "the reachable one"
}

Test-Case "7. owner-explain.ps1 gates on readiness and waits for the session (no timestamp-only comparison)" {
    $text = [IO.File]::ReadAllText((Join-Path $repoRoot "scripts\voice\owner-explain.ps1"))
    Assert-True ($text -match "Wait-WebShellReady") "must wait for the shell to answer"
    Assert-True ($text -match "Wait-QualificationSession") "must wait for the qualification session"
    Assert-True ($text -notmatch "no NEW realtime session since this script started") "the timestamp-only failure must be gone"
    Assert-True ($text -match "BaselineIds") "must exclude sessions that existed before the run"
    $bytes = [IO.File]::ReadAllBytes((Join-Path $repoRoot "scripts\voice\owner-explain.ps1"))
    Assert-True (@($bytes | Where-Object { $_ -gt 127 }).Count -eq 0) "owner-explain.ps1 must stay ASCII"
}

Write-Host ""
Write-Host "owner-explain harness: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
