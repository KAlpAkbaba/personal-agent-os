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

Test-Case "8. provenance is structural, never the generated Turkish wording" {
    $text = [IO.File]::ReadAllText((Join-Path $repoRoot "scripts\voice\owner-explain.ps1"))
    # 2026-09-05: the acceptance matched a sentence prefix, so a paraphrase failed a system
    # that was working. Wording must never be a gate; structure must be.
    Assert-True ($text -notmatch "speech_head -like") "no check may match generated wording"
    Assert-True ($text -match "provenance") "the briefing's provenance block must be read"
    Assert-True ($text -match "briefing cites ledger events and a real research job") "cited events + job id"
    Assert-True ($text -match "every cited ledger event resolves and is not seeded") "events must resolve"
    Assert-True ($text -match "narrated facts match the research run") "numbers must match the run"
    Assert-True ($text -match "VerifyOnly") "a completed session must be re-checkable without a new one"
}

Test-Case "9. -VerifyOnly needs no shell, no wait and no owner speech" {
    $text = [IO.File]::ReadAllText((Join-Path $repoRoot "scripts\voice\owner-explain.ps1"))
    $marker = "if (" + [char]0x24 + "VerifyOnly) {"
    $verifyIndex = $text.IndexOf($marker)
    Assert-True ($verifyIndex -gt 0) "the verify-only branch must exist"
    $elseIndex = $text.IndexOf("else {", $verifyIndex)
    $branch = $text.Substring($verifyIndex, $elseIndex - $verifyIndex)
    Assert-True ($branch -notmatch "Start-WebShellProcess") "no shell start in verify-only"
    Assert-True ($branch -match "verify-only") "the branch says what it is doing"
    Assert-True ($text.Contains("(if (" + [char]0x24 + "VerifyOnly) { 1 }")) "verify-only must not wait for a new session"
    Assert-True ($text -match "Press Enter when the session is over") "the interactive path still waits for the owner"
}

# ------------------------------------------------------- timestamp selection (2026-09-05)
#
# The -VerifyOnly run failed before it looked at a single session: the caller passed
# DateTime.MinValue as a sentinel for "there is no shell-readiness baseline", and the
# selector expressed its one-second tolerance by MUTATING that floor with AddSeconds(-1).
# The beginning of time has nothing below it, so the harness died with
# ArgumentOutOfRangeException while the product it was checking was working perfectly.
#
# Absence is now absence ($null), the window is a separate explicit floor, and the
# comparison is made on the DIFFERENCE of two DateTimeOffsets, which cannot overflow.

Write-Host ""
Write-Host "timestamp selection"

$explainProbe = { param($Id) New-Activity -Id $Id }
$completed = New-Session -Id "completed-1" -StartedAt "2026-09-04T20:56:28.352803Z" -State "closed"
$stale = New-Session -Id "stale-1" -StartedAt "2026-09-02T19:51:39.187548Z" -State "closed"

Test-Case "no baseline: a completed session is selected without any readiness moment" {
    $selected = Select-QualificationSession -Sessions @($completed) -BaselineIds @() `
        -ActivityProbe $explainProbe -ReadyAt $null -NotBefore $null
    Assert-True ($null -ne $selected) "a null ReadyAt must not throw and must not exclude"
    Assert-Equal "completed-1" $selected.SessionId "the completed session"
}

Test-Case "no baseline: the former crash cannot recur even with a MinValue-shaped floor" {
    # Someone may still hand the old sentinel in. It must be answered, not thrown at.
    $selected = Select-QualificationSession -Sessions @($completed) -BaselineIds @() `
        -ActivityProbe $explainProbe -ReadyAt ([DateTimeOffset]::MinValue) -NotBefore $null
    Assert-True ($null -ne $selected) "the minimum representable floor must include everything, not overflow"
}

Test-Case "minimum timestamp: a session at the start of the calendar is handled, not fatal" {
    $ancient = New-Session -Id "ancient" -StartedAt "0001-01-01T00:00:00.0000000Z" -State "closed"
    $selected = Select-QualificationSession -Sessions @($ancient) -BaselineIds @() `
        -ActivityProbe $explainProbe -ReadyAt $null -NotBefore $null
    Assert-Equal "ancient" $selected.SessionId "with no floor it qualifies"
    $bounded = Select-QualificationSession -Sessions @($ancient) -BaselineIds @() `
        -ActivityProbe $explainProbe -ReadyAt $null -NotBefore ([DateTimeOffset]::UtcNow.AddHours(-48))
    Assert-True ($null -eq $bounded) "and the window excludes it deterministically"
}

Test-Case "maximum timestamp: comparing at the far end of the calendar cannot overflow" {
    $future = New-Session -Id "future" -StartedAt "9999-12-31T23:59:59.9999999Z" -State "active"
    $selected = Select-QualificationSession -Sessions @($future) -BaselineIds @() `
        -ActivityProbe $explainProbe -ReadyAt ([DateTimeOffset]::MaxValue) -NotBefore $null
    Assert-True ($null -ne $selected) "max instant against max floor must answer, not throw"
}

Test-Case "timezone offsets: the same instant written three ways selects identically" {
    # 2026-09-04T20:56:28Z is the same moment as 23:56:28+03:00 and 13:56:28-07:00.
    foreach ($written in @("2026-09-04T20:56:28.352803Z",
                           "2026-09-04T23:56:28.352803+03:00",
                           "2026-09-04T13:56:28.352803-07:00")) {
        $s = New-Session -Id "tz" -StartedAt $written -State "closed"
        $sel = Select-QualificationSession -Sessions @($s) -BaselineIds @() -ActivityProbe $explainProbe `
            -ReadyAt ([DateTimeOffset]::Parse("2026-09-04T20:00:00Z")) -NotBefore $null
        Assert-True ($null -ne $sel) "offset form [$written] must be accepted"
        $rejected = Select-QualificationSession -Sessions @($s) -BaselineIds @() -ActivityProbe $explainProbe `
            -ReadyAt ([DateTimeOffset]::Parse("2026-09-05T00:00:00Z")) -NotBefore $null
        Assert-True ($null -eq $rejected) "and the same instant is before a later floor, whatever its spelling"
    }
}

Test-Case "a naive timestamp is read as UTC, not as the harness's local time" {
    $naive = New-Session -Id "naive" -StartedAt "2026-09-04T20:56:28.352803" -State "closed"
    $sel = Select-QualificationSession -Sessions @($naive) -BaselineIds @() -ActivityProbe $explainProbe `
        -ReadyAt ([DateTimeOffset]::Parse("2026-09-04T20:30:00Z")) -NotBefore $null
    Assert-True ($null -ne $sel) "a missing offset must not shift the instant by the harness's own timezone"
}

Test-Case "stale unrelated session: rejected by the window even though it is a web session" {
    $selected = Select-QualificationSession -Sessions @($stale) -BaselineIds @() `
        -ActivityProbe $explainProbe -ReadyAt $null -NotBefore ([DateTimeOffset]::UtcNow.AddHours(-48))
    Assert-True ($null -eq $selected) "an old session must never pass as this qualification"
}

Test-Case "multiple candidates: the newest qualifying session wins" {
    $older = New-Session -Id "older" -StartedAt "2026-09-04T19:00:29.494506Z" -State "closed"
    $middle = New-Session -Id "middle" -StartedAt "2026-09-04T19:37:12.168482Z" -State "closed"
    $selected = Select-QualificationSession -Sessions @($older, $completed, $middle) -BaselineIds @() `
        -ActivityProbe $explainProbe -ReadyAt $null -NotBefore ([DateTimeOffset]::Parse("2026-09-04T00:00:00Z"))
    Assert-Equal "completed-1" $selected.SessionId "newest first"
}

Test-Case "multiple candidates: a newer session with no explain call yields to one that has it" {
    $newestSilent = New-Session -Id "newest-silent" -StartedAt "2026-09-04T23:00:00Z" -State "closed"
    $probe = { param($Id) if ($Id -eq "newest-silent") { New-Activity -Id $Id -Explained $false } else { New-Activity -Id $Id } }
    $selected = Select-QualificationSession -Sessions @($newestSilent, $completed) -BaselineIds @() `
        -ActivityProbe $probe -ReadyAt $null -NotBefore ([DateTimeOffset]::Parse("2026-09-04T00:00:00Z"))
    Assert-Equal "completed-1" $selected.SessionId "a session with no succeeded activity.explain never qualifies"
}

Test-Case "malformed timestamps: rejected deterministically, and never fatal" {
    foreach ($bad in @("", "not-a-date", "2026-13-45T99:99:99Z", "0", "2026-09-04T20:56:28+99:00")) {
        $s = New-Session -Id "bad" -StartedAt $bad -State "closed"
        $selected = Select-QualificationSession -Sessions @($s, $completed) -BaselineIds @() `
            -ActivityProbe $explainProbe -ReadyAt $null -NotBefore ([DateTimeOffset]::Parse("2026-09-04T00:00:00Z"))
        Assert-Equal "completed-1" $selected.SessionId "a malformed started_at [$bad] must skip that session only"
    }
    $onlyBad = Select-QualificationSession -Sessions @((New-Session -Id "bad" -StartedAt "rubbish")) -BaselineIds @() `
        -ActivityProbe $explainProbe -ReadyAt $null -NotBefore $null
    Assert-True ($null -eq $onlyBad) "and when it is the only candidate the answer is nothing, not a crash"
}

Test-Case "the baseline still excludes unconditionally, however recent" {
    $selected = Select-QualificationSession -Sessions @($completed) -BaselineIds @("completed-1") `
        -ActivityProbe $explainProbe -ReadyAt $null -NotBefore $null
    Assert-True ($null -eq $selected) "a session that existed before the run can never be this run's"
}

Test-Case "the tolerance is bounded and cannot move a floor off the calendar" {
    Assert-True (Test-InstantAtOrAfter -Instant ([DateTimeOffset]::MinValue) -Floor ([DateTimeOffset]::MinValue) -ToleranceSec 1) "min vs min with slack"
    Assert-True (Test-InstantAtOrAfter -Instant ([DateTimeOffset]::MaxValue) -Floor ([DateTimeOffset]::MaxValue) -ToleranceSec 1) "max vs max with slack"
    Assert-True (-not (Test-InstantAtOrAfter -Instant ([DateTimeOffset]::MinValue) -Floor ([DateTimeOffset]::MaxValue) -ToleranceSec 1)) "the widest possible gap still answers, and answers no"
    $floor = [DateTimeOffset]::Parse("2026-09-04T20:00:00Z")
    Assert-True (Test-InstantAtOrAfter -Instant $floor.AddSeconds(-1) -Floor $floor -ToleranceSec 1) "one second early is within a one-second tolerance"
    Assert-True (-not (Test-InstantAtOrAfter -Instant $floor.AddSeconds(-2) -Floor $floor -ToleranceSec 1)) "two seconds early is not"
}

Test-Case "an empty array is never assigned out of an if-expression" {
    # $x = if (...) { ... } else { @() } yields $null, because PowerShell unrolls an empty
    # array out of an expression - and the next .Count then dies under StrictMode. That is
    # what turned "this session has no provenance block" into a crash that hid every
    # remaining check on 2026-09-05. Assert the shape is gone from the harness.
    $path = Join-Path $repoRoot "scripts\voice\owner-explain.ps1"
    $code = (Get-Content -LiteralPath $path | Where-Object { $_ -notmatch '^\s*#' }) -join "`n"
    Assert-True (-not ($code -match '=\s*if\s*\(.*\)\s*\{[^}]*\}\s*else\s*\{\s*@\(\)\s*\}')) `
        "no assignment may take an empty array from an else branch"

    # and prove the hazard is real, so this test cannot be dismissed as superstition
    $viaExpression = if ($false) { @(1) } else { @() }
    Assert-True ($null -eq $viaExpression) "the hazard is real: an if-expression yielding @() assigns null"
    $assignedFirst = @()
    if ($false) { $assignedFirst = @(1) }
    Assert-Equal 0 $assignedFirst.Count "assign-then-fill keeps it an array"
}

Test-Case "a session with no provenance block is reported, not crashed on" {
    # The three provenance checks fail together and say why; nothing throws while getting
    # there, so the behavioural checks after them still run and still report.
    $path = Join-Path $repoRoot "scripts\voice\owner-explain.ps1"
    $text = [IO.File]::ReadAllText($path)
    Assert-True ($text -match "no provenance block on this tool-call record") "the absence is named explicitly"
    Assert-True ($text -match "cannot be verified FROM THIS SESSION") "and scoped to the session, not to the product"
    Assert-True ($text -match "provenance_not_recorded_by_the_build_that_ran_this_session") "the evidence file records the diagnosis"
}

Test-Case "VerifyOnly no longer uses a sentinel baseline" {
    $path = Join-Path $repoRoot "scripts\voice\owner-explain.ps1"
    $text = [IO.File]::ReadAllText($path)
    # Comment lines are stripped first: the comment that EXPLAINS the sentinel bug is worth
    # keeping, and an assertion that forbids naming a defect would delete its own history.
    $code = (Get-Content -LiteralPath $path | Where-Object { $_ -notmatch '^\s*#' }) -join "`n"
    Assert-True (-not ($code -match "MinValue")) "no DateTime.MinValue sentinel may remain in the harness code"
    Assert-True ($text -match "NotBefore") "the window floor is passed explicitly"
    Assert-True ($text -match "VerifyMaxAgeHours") "and it is an explicit, bounded parameter"
}

# ------------------------------------------------ provenance state (owner M17 run, 2026-09-05)
#
# The harness itself crashed AFTER every real voice check had completed:
#
#     The variable '$prov' cannot be retrieved because it has not been set.
#
# $prov was assigned only inside the M16 branch and read afterwards in the shared failure
# summary, so an M17 run reached that summary with the variable never set. A qualification
# harness must never turn an otherwise valid result into an opaque runtime exception, so
# every provenance variable is declared before the mode branch and every path assigns.
#
# These tests read the SOURCE, because the failure is a control-flow property: no arrangement
# of session data can prove that a variable is always defined, only that it was this time.

Write-Host ""
Write-Host "provenance state is defined on every path"

$explainSource = [IO.File]::ReadAllText((Join-Path $repoRoot "scripts\voice\owner-explain.ps1"))

Test-Case "every provenance variable is declared before the mode branch" {
    $declIndex = $explainSource.IndexOf('$prov = $null')
    Assert-True ($declIndex -gt 0) "provenance state must be declared unconditionally"
    foreach ($name in '$provFacts = $null', '$jobId = ""', '$eventIds = @()', '$evidenceKinds = @()', '$provenanceChecked = $false') {
        Assert-True ($explainSource.Contains($name)) "missing unconditional declaration: $name"
    }
    # ...and the declaration must come BEFORE the first read in the failure summary
    $readIndex = $explainSource.IndexOf('if ($provenanceChecked -and $null -eq $prov)')
    Assert-True ($readIndex -gt 0) "the failure summary must gate on whether provenance was checked"
    Assert-True ($declIndex -lt $readIndex) "the declaration must precede the read"
}

Test-Case "the M16-only provenance block is the only thing that sets provenanceChecked" {
    $count = ([regex]::Matches($explainSource, [regex]::Escape('$provenanceChecked = $true'))).Count
    Assert-Equal 1 $count "exactly one branch performs the provenance check"
}

Test-Case "StrictMode is still on - the bug was not fixed by turning it off" {
    Assert-True ($explainSource -match 'Set-StrictMode -Version Latest') "StrictMode must stay enabled"
    Assert-True (-not ($explainSource -match 'Set-StrictMode -Off')) "StrictMode must never be disabled to hide an unset variable"
}

Test-Case "the M17 path does not read M16 provenance state" {
    # The M17 block must not depend on $prov at all: its provenance is per-subsystem.
    $m17Start = $explainSource.IndexOf('$expected = @(')
    $m17End = $explainSource.IndexOf('# Everything from here to the narration checks is M16 acceptance')
    Assert-True ($m17Start -gt 0 -and $m17End -gt $m17Start) "the M17 block must be locatable"
    $m17 = $explainSource.Substring($m17Start, $m17End - $m17Start)
    Assert-True (-not $m17.Contains('$prov')) "the M17 checks must not reference M16 provenance state"

}

Test-Case "the M17 block checks all six cognitive kinds and no M16 controls" {
    $m17Start = $explainSource.IndexOf('$expected = @(')
    $m17End = $explainSource.IndexOf('# Everything from here to the narration checks is M16 acceptance')
    $m17 = $explainSource.Substring($m17Start, $m17End - $m17Start)
    foreach ($kind in "learned", "goals", "world_state", "self_code", "evolution", "can_deploy") {
        Assert-True ($m17.Contains($kind)) "M17 must assert the $kind path"
    }
    foreach ($m16 in "teknik anlat", "false_interruption", "narration.paused") {
        Assert-True (-not $m17.Contains($m16)) "M17 must not inherit the M16 control '$m16'"
    }
}

Test-Case "a failed subsystem invocation is reported as failed, not as never asked" {
    Assert-True ($explainSource.Contains("the question reached the tool and the tool FAILED")) `
        "a crashed tool call must be distinguished from an unrouted question"
    Assert-True ($explainSource.Contains("NEVER ASKED OR NEVER ROUTED")) `
        "an unrouted question must be named as such"
}


Write-Host ""
Write-Host "M18: the Core session qualifies by what it did, not by one tool name"

function New-CoreActivity {
    param([string]$Id, [string[]]$Succeeded = @(), [string[]]$Failed = @())
    $calls = @()
    foreach ($n in $Succeeded) { $calls += [pscustomobject]@{ name = $n; status = "succeeded" } }
    foreach ($n in $Failed) { $calls += [pscustomobject]@{ name = $n; status = "failed" } }
    return [pscustomobject]@{ session_id = $Id; tool_calls = $calls }
}

Test-Case "10. the default qualifier is unchanged: activity.explain succeeded (M17 scripts keep working)" {
    Assert-True (Test-ExplainQualification -Activity (New-Activity -Id "a")) "explained qualifies"
    Assert-True (-not (Test-ExplainQualification -Activity (New-Activity -Id "b" -Explained $false))) "narration only does not"
    Assert-True (-not (Test-ExplainQualification -Activity (New-CoreActivity -Id "c" -Succeeded @("state.now")))) "the M17 rule does not accept state.now"
}
Test-Case "11. a live-state answer alone qualifies a Core session (no activity.explain required)" {
    $s = New-Session -Id "core-1" -StartedAt "2026-09-04T20:01:00Z"
    $selected = Select-QualificationSession -Sessions @($s) -BaselineIds @() -ReadyAt $readyAt `
        -ActivityProbe { param($Id) New-CoreActivity -Id $Id -Succeeded @("state.now") } -Qualifier ${function:Test-CoreQualification}
    Assert-Equal "core-1" $selected.SessionId "selected"
}
Test-Case "11b. an eye action alone qualifies; a failed one does not; a non-router tool does not" {
    $s = New-Session -Id "core-2" -StartedAt "2026-09-04T20:01:00Z"
    $ok = Select-QualificationSession -Sessions @($s) -BaselineIds @() -ReadyAt $readyAt `
        -ActivityProbe { param($Id) New-CoreActivity -Id $Id -Succeeded @("eye.disable") } -Qualifier ${function:Test-CoreQualification}
    Assert-Equal "core-2" $ok.SessionId "eye.disable qualifies"
    $failed = Select-QualificationSession -Sessions @($s) -BaselineIds @() -ReadyAt $readyAt `
        -ActivityProbe { param($Id) New-CoreActivity -Id $Id -Failed @("eye.disable", "state.now") } -Qualifier ${function:Test-CoreQualification}
    Assert-True ($null -eq $failed) "failed calls do not qualify"
    $clock = Select-QualificationSession -Sessions @($s) -BaselineIds @() -ReadyAt $readyAt `
        -ActivityProbe { param($Id) New-CoreActivity -Id $Id -Succeeded @("clock.now", "narration.control") } -Qualifier ${function:Test-CoreQualification}
    Assert-True ($null -eq $clock) "clock.now / narration.control are not the router"
}
Test-Case "11c. ONE succeeded call is an array of one under the qualifier (the .Count trap, again)" {
    $one = Get-SucceededToolCalls -Activity (New-CoreActivity -Id "x" -Succeeded @("state.now"))
    Assert-True ($one -is [array]) "array"
    Assert-Equal 1 $one.Count "count"
    $none = Get-SucceededToolCalls -Activity (New-CoreActivity -Id "y")
    Assert-Equal 0 $none.Count "empty"
    $absent = Get-SucceededToolCalls -Activity ([pscustomobject]@{ session_id = "z" })
    Assert-Equal 0 $absent.Count "no tool_calls property at all"
}
Test-Case "11d. Wait-QualificationSession passes the qualifier through" {
    $fake = New-FakeClock
    $polls = @{ n = 0 }
    $list = { $polls.n++; if ($polls.n -ge 2) { @(New-Session -Id "core-3" -StartedAt "2026-09-04T20:01:00Z") } else { @() } }.GetNewClosure()
    $result = Wait-QualificationSession -ListSessions $list -ActivityProbe { param($Id) New-CoreActivity -Id $Id -Succeeded @("eye.enable") } `
        -BaselineIds @() -ReadyAt $readyAt -TimeoutSec 60 -IntervalSec 5 -Sleep $fake.Sleep -Clock $fake.Clock -Qualifier ${function:Test-CoreQualification}
    Assert-Equal "core-3" $result.Selected.SessionId "selected on the second poll"
    $never = Wait-QualificationSession -ListSessions $list -ActivityProbe { param($Id) New-CoreActivity -Id $Id -Succeeded @("eye.enable") } `
        -BaselineIds @() -ReadyAt $readyAt -TimeoutSec 20 -IntervalSec 5 -Sleep $fake.Sleep -Clock $fake.Clock
    Assert-True ($null -eq $never.Selected) "without the qualifier the M17 rule applies and eye.enable does not qualify"
}

Write-Host ""
Write-Host "M18: the wait fails fast with the missing evidence, and says what it sees meanwhile"

Test-Case "12. GiveUp stops the wait NOW with its reason; without it the budget is spent" {
    $fake = New-FakeClock
    $result = Wait-QualificationSession -ListSessions { @() } -ActivityProbe { param($Id) $null } -BaselineIds @() -ReadyAt $readyAt `
        -TimeoutSec 600 -IntervalSec 5 -Sleep $fake.Sleep -Clock $fake.Clock `
        -GiveUp { param($Attempts, $Elapsed, $Sessions) if ($Elapsed -ge 20) { "no web voice session connected within 20 s" } else { $null } }
    Assert-True ($null -eq $result.Selected) "nothing selected"
    Assert-Equal "no web voice session connected within 20 s" $result.GaveUp "the reason travels"
    Assert-True ($result.ElapsedSec -lt 60) "stopped long before the 600 s budget (elapsed $($result.ElapsedSec))"
    $spent = Wait-QualificationSession -ListSessions { @() } -ActivityProbe { param($Id) $null } -BaselineIds @() -ReadyAt $readyAt `
        -TimeoutSec 20 -IntervalSec 5 -Sleep $fake.Sleep -Clock $fake.Clock
    Assert-Equal "budget of 20 s spent" $spent.GaveUp "the budget is a reason too"
}
Test-Case "12b. GiveUp receives the sessions of this run as an argument: 'connected but silent' needs no library call from the callback" {
    $fake = New-FakeClock
    $s = New-Session -Id "quiet-1" -StartedAt "2026-09-04T20:01:00Z"
    # The callback is deliberately a CLOSURE - the shape that crashed the owner's run - and
    # it works because it reaches for nothing but its arguments.
    $result = Wait-QualificationSession -ListSessions { @($s) }.GetNewClosure() -ActivityProbe { param($Id) New-CoreActivity -Id $Id } -BaselineIds @() -ReadyAt $readyAt `
        -TimeoutSec 600 -IntervalSec 5 -Sleep $fake.Sleep -Clock $fake.Clock -Qualifier ${function:Test-CoreQualification} `
        -GiveUp { param($Attempts, $Elapsed, $Sessions, $New) if ($New.Count -gt 0 -and $Elapsed -ge 10) { "session $($New[0].session_id) connected but made no router call" } else { $null } }.GetNewClosure()
    Assert-Equal "session quiet-1 connected but made no router call" $result.GaveUp "reason names the silent session"
}
Test-Case "12d. ConnectWaitSec: no session of this run within the bound -> a bounded diagnostic failure, in the library" {
    $fake = New-FakeClock
    $old = New-Session -Id "old-closed" -StartedAt "2026-09-04T19:00:00Z" -State "closed"
    $result = Wait-QualificationSession -ListSessions { @($old) } -ActivityProbe { param($Id) New-Activity -Id $Id } -BaselineIds @("old-closed") -ReadyAt $readyAt `
        -TimeoutSec 600 -IntervalSec 5 -Sleep $fake.Sleep -Clock $fake.Clock -Qualifier ${function:Test-CoreQualification} -ConnectWaitSec 30
    Assert-True ($null -eq $result.Selected) "the old closed session never qualifies"
    Assert-True ($result.GaveUp -like "no web voice session connected within 30 s*") "reason: '$($result.GaveUp)'"
    Assert-True ($result.ElapsedSec -ge 30 -and $result.ElapsedSec -lt 60) "bounded (elapsed $($result.ElapsedSec))"
}
Test-Case "12e. RouterWaitSec: a session of this run with no router call -> give up naming what it did make" {
    $fake = New-FakeClock
    $s = New-Session -Id "core-quiet" -StartedAt "2026-09-04T20:01:00Z"
    $act = [pscustomobject]@{ session_id = "core-quiet"; tool_calls = @([pscustomobject]@{ name = "clock.now"; status = "succeeded" }); intents = @([pscustomobject]@{ intent = "none"; klass = "query" }) }
    $result = Wait-QualificationSession -ListSessions { @($s) } -ActivityProbe { param($Id) $act } -BaselineIds @() -ReadyAt $readyAt `
        -TimeoutSec 600 -IntervalSec 5 -Sleep $fake.Sleep -Clock $fake.Clock -Qualifier ${function:Test-CoreQualification} -ConnectWaitSec 300 -RouterWaitSec 20
    Assert-True ($result.GaveUp -like "session core-quiet connected * but made no succeeded router call*") "reason: '$($result.GaveUp)'"
    Assert-True ($result.GaveUp -match "tool calls seen: clock.now:succeeded") "the calls it did make are named"
    Assert-True ($result.GaveUp -match "intents resolved: none/query") "the intents are named"
    Assert-True ($result.ElapsedSec -lt 60) "bounded"
}
Test-Case "12f. a session that DOES go through the router is selected before either bound fires" {
    $fake = New-FakeClock
    $polls = @{ n = 0 }
    $list = { $polls.n++; if ($polls.n -ge 3) { @(New-Session -Id "core-ok" -StartedAt "2026-09-04T20:01:00Z") } else { @() } }.GetNewClosure()
    $probe = { param($Id) if ($polls.n -ge 5) { New-CoreActivity -Id $Id -Succeeded @("state.now") } else { New-CoreActivity -Id $Id } }.GetNewClosure()
    $result = Wait-QualificationSession -ListSessions $list -ActivityProbe $probe -BaselineIds @() -ReadyAt $readyAt `
        -TimeoutSec 600 -IntervalSec 5 -Sleep $fake.Sleep -Clock $fake.Clock -Qualifier ${function:Test-CoreQualification} -ConnectWaitSec 60 -RouterWaitSec 60
    Assert-Equal "core-ok" $result.Selected.SessionId "selected once the router call appeared"
    Assert-True ($null -eq $result.GaveUp) "no give-up"
}
Test-Case "12g. progress lines go to Log every ProgressEverySec, naming the session, its calls and the Core's state" {
    $fake = New-FakeClock
    $s = New-Session -Id "core-p" -StartedAt "2026-09-04T20:01:00Z"
    $act = [pscustomobject]@{ session_id = "core-p"; tool_calls = @([pscustomobject]@{ name = "eye.disable"; status = "succeeded"; terminal_status = "verified" }); intents = @() }
    $logged = New-Object System.Collections.ArrayList
    $log = { param($Line) [void]$logged.Add($Line) }.GetNewClosure()
    $result = Wait-QualificationSession -ListSessions { @($s) } -ActivityProbe { param($Id) $act } -BaselineIds @() -ReadyAt $readyAt `
        -TimeoutSec 30 -IntervalSec 5 -Sleep $fake.Sleep -Clock $fake.Clock -Qualifier { param($A) $false } `
        -ProgressEverySec 10 -CoreProbe { [pscustomobject]@{ state = "agent.idle"; subsystem = "system"; session_id = $null; at = "2026-09-04T20:00:00Z" } } -Log $log
    Assert-Equal "budget of 30 s spent" $result.GaveUp "ran out"
    $text = ($logged -join "`n")
    Assert-True ($text -match "current web session: core-p") "session named"
    Assert-True ($text -match "eye receipts seen: eye.disable=verified") "receipt named"
    Assert-True ($text -match "latest Core state: agent.idle \(subsystem system") "core state named"
    Assert-True ($logged.Count -ge 8) "several progress blocks (got $($logged.Count) lines)"
}
Test-Case "12h. the library's locals never shadow a callback's variables (dynamic scope resolves nearest first)" {
    # A caller's callbacks that use the most obvious names - $act, $sessions, $session,
    # $activity, $selected, $elapsed - must see the CALLER's values, not whatever the wait
    # or the selector happen to hold in a local of the same name at the moment of the call.
    $fake = New-FakeClock
    $sessions = @(New-Session -Id "shadow-1" -StartedAt "2026-09-04T20:01:00Z")
    $session = $sessions[0]
    $activity = New-CoreActivity -Id "shadow-1" -Succeeded @("state.now")
    $act = $activity
    $selected = "caller-owned"
    $elapsed = 12345
    $result = Wait-QualificationSession -ListSessions { $sessions } -ActivityProbe { param($Id) if ($Id -eq $session.session_id -and $selected -eq "caller-owned" -and $elapsed -eq 12345) { $act } else { $null } } `
        -BaselineIds @() -ReadyAt $readyAt -TimeoutSec 60 -IntervalSec 5 -Sleep $fake.Sleep -Clock $fake.Clock `
        -Qualifier { param($A) ($null -ne $activity) -and (Test-CoreQualification -Activity $A) } -ConnectWaitSec 30 -RouterWaitSec 30
    Assert-Equal "shadow-1" $result.Selected.SessionId "the probe saw the caller's own variables and the session was selected"
}
Test-Case "17. a session the Core named on the bus is accepted even when it predates the baseline and the shell (owner, 2026-09-06 later)" {
    # The owner connected voice BEFORE the harness took its baseline: the live session was
    # in the baseline, so it was excluded as "old" while agent.listening named it every turn.
    $live = New-Session -Id "live-before" -StartedAt "2026-09-04T19:50:00Z"
    $probe = { param($Id) New-CoreActivity -Id $Id -Succeeded @("eye.enable") }
    $excluded = Select-QualificationSession -Sessions @($live) -BaselineIds @("live-before") -ReadyAt $readyAt -ActivityProbe $probe -Qualifier ${function:Test-CoreQualification}
    Assert-True ($null -eq $excluded) "without the bus id it is (rightly) excluded"
    $accepted = Select-QualificationSession -Sessions @($live) -BaselineIds @("live-before") -ReadyAt $readyAt -ActivityProbe $probe -Qualifier ${function:Test-CoreQualification} -AcceptIds @("live-before")
    Assert-Equal "live-before" $accepted.SessionId "named by the bus: accepted"
    $new = Get-NewSessions -Sessions @($live) -BaselineIds @("live-before") -ReadyAt $readyAt -AcceptIds @("live-before")
    Assert-Equal 1 $new.Count "Get-NewSessions accepts it too"
}
Test-Case "17b. Get-BusVoiceSessionIds: voice events since the run start, distinct, newest last; other subsystems ignored" {
    $since = [DateTimeOffset]::Parse("2026-09-04T20:00:00Z", [Globalization.CultureInfo]::InvariantCulture)
    $events = @(
        [pscustomobject]@{ state = "agent.listening"; subsystem = "voice"; session_id = "old-s"; at = "2026-09-04T19:59:00Z" },
        [pscustomobject]@{ state = "agent.listening"; subsystem = "voice"; session_id = "s-1"; at = "2026-09-04T20:01:00Z" },
        [pscustomobject]@{ state = "eye.active"; subsystem = "presence"; session_id = $null; at = "2026-09-04T20:01:30Z" },
        [pscustomobject]@{ state = "agent.speaking"; subsystem = "voice"; session_id = "s-1"; at = "2026-09-04T20:02:00Z" },
        [pscustomobject]@{ state = "agent.idle"; subsystem = "voice"; session_id = "s-2"; at = "2026-09-04T20:03:00Z" }
    )
    $ids = Get-BusVoiceSessionIds -Events $events -Since $since
    Assert-Equal "s-1,s-2" ($ids -join ",") "distinct voice ids since the start, newest last"
    $one = Get-BusVoiceSessionIds -Events @($events[1]) -Since $since
    Assert-True ($one -is [array]) "one id is still an array"
    Assert-Equal 0 (Get-BusVoiceSessionIds -Events $null -Since $since).Count "no events"
}
Test-Case "17c. Wait-QualificationSession refreshes AcceptIds from AcceptProbe each poll and selects the bus-named session" {
    $fake = New-FakeClock
    $live = New-Session -Id "live-before" -StartedAt "2026-09-04T19:50:00Z"
    $polls = @{ n = 0 }
    $acceptProbe = { $polls.n++; if ($polls.n -ge 2) { @("live-before") } else { @() } }.GetNewClosure()
    $result = Wait-QualificationSession -ListSessions { @($live) } -ActivityProbe { param($Id) New-CoreActivity -Id $Id -Succeeded @("state.now") } `
        -BaselineIds @("live-before") -ReadyAt $readyAt -TimeoutSec 60 -IntervalSec 5 -Sleep $fake.Sleep -Clock $fake.Clock `
        -Qualifier ${function:Test-CoreQualification} -ConnectWaitSec 30 -AcceptProbe $acceptProbe
    Assert-Equal "live-before" $result.Selected.SessionId "selected once the bus named it"
}
Test-Case "18. Get-EyeReceiptSteps: ac -> kapat -> ac read off the receipts, verified only, in order" {
    $calls = @(
        [pscustomobject]@{ name = "state.now"; status = "succeeded" },
        [pscustomobject]@{ name = "eye.enable"; call_id = "c1"; status = "succeeded"; terminal_status = "failed"; error_class = "permission_denied"; speech_head = "Kamerayi acamadim"; observed_after = [pscustomobject]@{ local = [pscustomobject]@{ state = "ERROR"; media_track_ready_state = $null; action_trace = @("request:enable", "getUserMedia:NotAllowedError") } } },
        [pscustomobject]@{ name = "eye.enable"; call_id = "c2"; status = "succeeded"; terminal_status = "verified"; speech_head = "Gozumu actim efendim."; observed_after = [pscustomobject]@{ local = [pscustomobject]@{ state = "ACTIVE"; media_track_ready_state = "live"; action_trace = @("request:enable", "loop:started") } } },
        [pscustomobject]@{ name = "eye.disable"; call_id = "c3"; status = "succeeded"; terminal_status = "verified"; speech_head = "Gozumu kapattim efendim."; observed_after = [pscustomobject]@{ local = [pscustomobject]@{ state = "DISABLED"; media_track_ready_state = "ended" } } }
    )
    $steps = Get-EyeReceiptSteps -Calls $calls
    Assert-Equal 3 $steps.Receipts.Count "three eye receipts seen (state.now is not one)"
    Assert-Equal 0 $steps.Receipts[2].Trace.Count "no trace reported is an empty list, not null"
    $oneTrace = Get-EyeReceiptSteps -Calls @([pscustomobject]@{ name = "eye.enable"; call_id = "t1"; status = "succeeded"; terminal_status = "verified"; observed_after = [pscustomobject]@{ local = [pscustomobject]@{ state = "ACTIVE"; action_trace = @("request:enable") } } })
    Assert-Equal 1 $oneTrace.Receipts[0].Trace.Count "a one-entry trace is still a list"
    # The server stores the trace at the receipt's top level (session_activity exposes it there).
    $topTrace = Get-EyeReceiptSteps -Calls @([pscustomobject]@{ name = "eye.disable"; call_id = "t2"; status = "succeeded"; terminal_status = "verified"; action_trace = @("request:disable", "loop:stopped", "stream:ended"); observed_after = [pscustomobject]@{ local = [pscustomobject]@{ state = "DISABLED"; media_track_ready_state = "ended" } } })
    Assert-Equal 3 $topTrace.Receipts[0].Trace.Count "the top-level trace is read first"
    Assert-Equal "ended" $topTrace.Receipts[0].Track "and the track state from local"
    Assert-Equal 2 $steps.Satisfied "enable then disable satisfied; the third step is open"
    Assert-True (-not $steps.Done) "not done"
    Assert-Equal "c2,c3" (($steps.Matched | ForEach-Object { $_.CallId }) -join ",") "the failed enable did not count"
    Assert-Equal "permission_denied" $steps.Receipts[0].ErrorClass "the failed one keeps its class"
    Assert-Equal "live" $steps.Matched[0].Track "track state read off the receipt"
    Assert-Equal 2 $steps.Matched[0].Trace.Count "trace is a list"
    $done = Get-EyeReceiptSteps -Calls ($calls + @([pscustomobject]@{ name = "eye.enable"; call_id = "c4"; status = "succeeded"; terminal_status = "verified" }))
    Assert-True $done.Done "third verified receipt finishes the test"
    $one = Get-EyeReceiptSteps -Calls @([pscustomobject]@{ name = "eye.enable"; call_id = "only"; status = "succeeded"; terminal_status = "verified" })
    Assert-Equal 1 $one.Receipts.Count "one receipt is a list of one"
    Assert-Equal 0 (Get-EyeReceiptSteps -Calls $null).Receipts.Count "no calls"
}
Test-Case "19. Test-HiddenEyeMutation: a voice: eye row outside every receipt window is a second mutation path" {
    $receipts = @([pscustomobject]@{ action_id = "c1"; started_at = "2026-09-04T20:01:00Z"; completed_at = "2026-09-04T20:01:01Z" })
    $inside = [pscustomobject]@{ event_type = "eye.disabled"; occurred_at = "2026-09-04T20:01:00.5Z"; detail_json = [pscustomobject]@{ reason = "voice:gozunu kapat" } }
    $outside = [pscustomobject]@{ event_type = "eye.disabled"; occurred_at = "2026-09-04T20:05:00Z"; detail_json = [pscustomobject]@{ reason = "voice:gozunu kapat" } }
    $control = [pscustomobject]@{ event_type = "eye.enabled"; occurred_at = "2026-09-04T20:05:00Z"; detail_json = [pscustomobject]@{ reason = "owner_start" } }
    $none = Test-HiddenEyeMutation -LedgerRows @($inside, $control) -Receipts $receipts
    Assert-Equal 0 $none.Count "inside a window, or not by voice: explained"
    $bad = Test-HiddenEyeMutation -LedgerRows @($inside, $outside) -Receipts $receipts
    Assert-Equal 1 $bad.Count "one unexplained voice mutation"
    Assert-True ($bad[0] -like "*eye.disabled (voice:gozunu kapat; no action_id on the row)") "named: $($bad[0])"
    $noReceipts = Test-HiddenEyeMutation -LedgerRows @($outside) -Receipts $null
    Assert-Equal 1 $noReceipts.Count "no receipts at all: every voice row is unexplained"
}
Test-Case "19b. the receipts are ledger ROWS (fields under detail_json), and a row 71 ms BEFORE its receipt is explained (owner run, session 9df439af)" {
    # The production shape: action.receipt rows from /v1/ledger/events, the eye row written
    # by the browser durable-first, before the tool call was relayed.
    $receiptRow = [pscustomobject]@{ event_type = "action.receipt"; occurred_at = "2026-09-06T14:32:00.987141Z"; detail_json = [pscustomobject]@{ capability = "eye.disable"; action_id = "call_qDJyUhGO3eKZpDrq"; started_at = "2026-09-06T14:32:00.977128Z"; completed_at = "2026-09-06T14:32:00.987141Z" } }
    $eyeRow = [pscustomobject]@{ event_type = "eye.disabled"; occurred_at = "2026-09-06T14:32:00.903584Z"; detail_json = [pscustomobject]@{ reason = "voice:gozunu kapat" } }
    $r = Test-HiddenEyeMutation -LedgerRows @($eyeRow) -Receipts @($receiptRow)
    Assert-Equal 0 $r.Count "explained by the window (was unexplained before the fix: no windows were built from rows)"
    # A row that precedes its receipt by a permission prompt's worth is still explained...
    $early = [pscustomobject]@{ event_type = "eye.enabled"; occurred_at = "2026-09-06T14:31:49.000000Z"; detail_json = [pscustomobject]@{ reason = "voice:gozunu ac" } }
    $enableRow = [pscustomobject]@{ event_type = "action.receipt"; occurred_at = "2026-09-06T14:31:53.988463Z"; detail_json = [pscustomobject]@{ capability = "eye.enable"; action_id = "call_KVHgZm2aQO5f4ETh"; started_at = "2026-09-06T14:31:53.980894Z"; completed_at = "2026-09-06T14:31:53.988463Z" } }
    Assert-Equal 0 (Test-HiddenEyeMutation -LedgerRows @($early) -Receipts @($enableRow)).Count "5 s of camera prompt before the relay is inside the lead"
    # ...but a row a minute earlier is not.
    $stale = [pscustomobject]@{ event_type = "eye.enabled"; occurred_at = "2026-09-06T14:30:40.000000Z"; detail_json = [pscustomobject]@{ reason = "voice:gozunu ac" } }
    Assert-Equal 1 (Test-HiddenEyeMutation -LedgerRows @($stale) -Receipts @($enableRow)).Count "a minute before: unexplained"
}
Test-Case "19c. identity beats time: a row carrying a receipt's action_id is explained wherever it is; an unknown action_id is named" {
    $receiptRow = [pscustomobject]@{ event_type = "action.receipt"; occurred_at = "2026-09-06T14:32:00.987141Z"; detail_json = [pscustomobject]@{ action_id = "call_A"; started_at = "2026-09-06T14:32:00.977128Z"; completed_at = "2026-09-06T14:32:00.987141Z" } }
    $byId = [pscustomobject]@{ event_type = "eye.disabled"; occurred_at = "2026-09-06T14:20:00Z"; detail_json = [pscustomobject]@{ reason = "voice:gozunu kapat"; action_id = "call_A" } }
    Assert-Equal 0 (Test-HiddenEyeMutation -LedgerRows @($byId) -Receipts @($receiptRow)).Count "matched by action_id, far outside the window"
    $unknown = [pscustomobject]@{ event_type = "eye.disabled"; occurred_at = "2026-09-06T14:20:00Z"; detail_json = [pscustomobject]@{ reason = "voice:gozunu kapat"; action_id = "call_Z" } }
    $r = Test-HiddenEyeMutation -LedgerRows @($unknown) -Receipts @($receiptRow)
    Assert-Equal 1 $r.Count "unknown action id, outside every window"
    Assert-True ($r[0] -like "*action_id call_Z matches no receipt*") "the reason names the id: $($r[0])"
}
Test-Case "20. Test-TextContainsAny coerces every shape: string, one character, scalar, array, empty, null" {
    Assert-True (Test-TextContainsAny -Text "Kamera KAPANDI efendim" -Words @("kapandi")) "string, case-insensitive"
    $dotless = [char]0x0131
    Assert-True (Test-TextContainsAny -Text ("sayfay" + $dotless + " eledim") -Words $dotless) "a single [char] word does not crash and matches"
    Assert-True (Test-TextContainsAny -Text "abc" -Words "b") "a scalar string"
    Assert-True (Test-TextContainsAny -Text "x 42 y" -Words 42) "a scalar number is coerced"
    Assert-True (Test-TextContainsAny -Text "elendi" -Words @("zzz", [char]0x0131, "elendi")) "an array mixing shapes"
    Assert-True (-not (Test-TextContainsAny -Text "" -Words @("a"))) "empty text: false"
    Assert-True (-not (Test-TextContainsAny -Text $null -Words @("a"))) "null text: false"
    Assert-True (-not (Test-TextContainsAny -Text "abc" -Words @())) "empty words: false"
    Assert-True (-not (Test-TextContainsAny -Text "abc" -Words $null)) "null words: false"
    $list = ConvertTo-TextList -Value @("a", [char]0x0131, 7, $null, "")
    Assert-Equal 3 $list.Count "three non-empty strings out of five inputs (null and empty dropped)"
    foreach ($s in $list) { if (-not ($s -is [string])) { throw "every element is a string" } }
}
Test-Case "21. Get-SpeechTurns pairs first_audio / response_done / audio_done BY ORDER (the record carries no turn or payload) and measures from t_ms" {
    # The owner's real record, 2026-09-06 session a4455670, first answer: audible 13.1 s,
    # playback ended 6.0 s after the provider's response.done.
    $events = @(
        [pscustomobject]@{ kind = "mic_speech_start"; t_ms = 9000 },
        [pscustomobject]@{ kind = "first_audio"; t_ms = 13137 },
        [pscustomobject]@{ kind = "response_done"; t_ms = 20296 },
        [pscustomobject]@{ kind = "audio_done"; t_ms = 26282 },
        [pscustomobject]@{ kind = "first_audio"; t_ms = 54405 },
        [pscustomobject]@{ kind = "response_done"; t_ms = 56039 },
        [pscustomobject]@{ kind = "audio_done"; t_ms = 56546; payload = [pscustomobject]@{ basis = "provider" } },
        [pscustomobject]@{ kind = "first_audio"; t_ms = 393917 }
    )
    $turns = Get-SpeechTurns -Events $events
    Assert-Equal 3 $turns.Count "two completed turns and one still open"
    Assert-Equal 13145 $turns[0].audible_ms "audible = audio_done - first_audio"
    Assert-True $turns[0].ended_after_generation "playback ended after response_done"
    Assert-Equal "" $turns[0].basis "no payload: basis unknown, not invented"
    Assert-Equal "provider" $turns[1].basis "payload basis when present"
    Assert-Equal 2141 $turns[1].audible_ms "second turn"
    Assert-True ($null -eq $turns[2].audio_done_ms) "the open turn has no end"
    $one = Get-SpeechTurns -Events @([pscustomobject]@{ kind = "first_audio"; t_ms = 1 })
    Assert-Equal 1 $one.Count "one turn is a list of one"
    Assert-Equal 0 (Get-SpeechTurns -Events $null).Count "no events"
}
Test-Case "15. Get-SessionRouterSummary: none / one / many, through the array traps" {
    $none = Get-SessionRouterSummary -Activity ([pscustomobject]@{ session_id = "x" })
    Assert-Equal "none" $none.ToolCalls "no calls"
    Assert-Equal "none" $none.LastKind "no intents"
    $one = Get-SessionRouterSummary -Activity ([pscustomobject]@{ tool_calls = @([pscustomobject]@{ name = "state.now"; status = "succeeded" }); intents = @([pscustomobject]@{ intent = "explain"; klass = "query"; query_kind = "world_state"; capability = $null }) })
    Assert-Equal "state.now:succeeded" $one.ToolCalls "one call"
    Assert-Equal "explain/query" $one.Intents "one intent"
    Assert-True ($one.LastKind -like "explain klass=query query_kind=world_state*") "last kind"
}
Test-Case "16. Test-SpokenAfterToolDone: first_audio after tool_done in the same turn, and the two ways it is not" {
    $events = @(
        [pscustomobject]@{ kind = "tool_call"; t_ms = 100; turn = 2; payload = [pscustomobject]@{ call_id = "c-1" } },
        [pscustomobject]@{ kind = "tool_done"; t_ms = 400; turn = 2; payload = [pscustomobject]@{ call_id = "c-1" } },
        [pscustomobject]@{ kind = "first_audio"; t_ms = 650; turn = 2; payload = [pscustomobject]@{} }
    )
    Assert-Equal "" (Test-SpokenAfterToolDone -Events $events -CallId "c-1") "spoken after the ACK"
    $early = @(
        [pscustomobject]@{ kind = "first_audio"; t_ms = 50; turn = 2; payload = [pscustomobject]@{} },
        [pscustomobject]@{ kind = "tool_done"; t_ms = 400; turn = 2; payload = [pscustomobject]@{ call_id = "c-1" } }
    )
    Assert-True ((Test-SpokenAfterToolDone -Events $early -CallId "c-1") -like "no first_audio after tool_done*") "spoken before the ACK is refused"
    Assert-True ((Test-SpokenAfterToolDone -Events $events -CallId "c-9") -like "no tool_done event for c-9") "unknown call"
    Assert-True ((Test-SpokenAfterToolDone -Events $null -CallId "c-1") -like "no tool_done event*") "no events at all"
    $single = @([pscustomobject]@{ kind = "tool_done"; t_ms = 1; turn = 1; payload = [pscustomobject]@{ call_id = "c-1" } })
    Assert-True ((Test-SpokenAfterToolDone -Events $single -CallId "c-1") -like "no first_audio*") "one event is still a list"
}
Test-Case "12c. a GiveUp that throws is ignored (the wait goes on), never fatal" {
    $fake = New-FakeClock
    $result = Wait-QualificationSession -ListSessions { @() } -ActivityProbe { param($Id) $null } -BaselineIds @() -ReadyAt $readyAt `
        -TimeoutSec 10 -IntervalSec 5 -Sleep $fake.Sleep -Clock $fake.Clock -GiveUp { throw "boom" }
    Assert-Equal "budget of 10 s spent" $result.GaveUp "fell through to the budget"
}
Test-Case "13. Get-NewSessions: baseline, kind and readiness filter; ONE result is an array of one" {
    $old = New-Session -Id "old" -StartedAt "2026-09-04T20:01:00Z"
    $cli = New-Session -Id "cli" -StartedAt "2026-09-04T20:01:00Z" -Kind "cli"
    $early = New-Session -Id "early" -StartedAt "2026-09-04T19:59:00Z"
    $mine = New-Session -Id "mine" -StartedAt "2026-09-04T20:01:00Z"
    $new = Get-NewSessions -Sessions @($old, $cli, $early, $mine) -BaselineIds @("old") -ReadyAt $readyAt
    Assert-True ($new -is [array]) "array"
    Assert-Equal 1 $new.Count "one"
    Assert-Equal "mine" $new[0].session_id "the right one"
    $none = Get-NewSessions -Sessions @($old) -BaselineIds @("old") -ReadyAt $readyAt
    Assert-Equal 0 $none.Count "none"
}
Test-Case "14. Format-QualificationProgress names the missing transition from what it is handed" {
    $lines = Format-QualificationProgress -Session $null -Activity $null -CoreCurrent $null -ElapsedSec 15
    Assert-True (($lines -join "`n") -match "current web session: none yet") "no session yet"
    Assert-True (($lines -join "`n") -match "latest Core state: unknown") "no core state"
    $s = New-Session -Id "s-1" -StartedAt "2026-09-04T20:01:00Z"
    $act = [pscustomobject]@{
        session_id = "s-1"
        tool_calls = @([pscustomobject]@{ name = "state.now"; status = "succeeded"; terminal_status = $null }, [pscustomobject]@{ name = "eye.disable"; status = "succeeded"; terminal_status = "verified" })
        intents    = @([pscustomobject]@{ intent = "eye_disable"; klass = "action"; query_kind = $null; capability = "eye.disable" })
    }
    $core = [pscustomobject]@{ state = "agent.listening"; subsystem = "voice"; session_id = "s-1"; at = "2026-09-04T20:02:00Z" }
    $lines = Format-QualificationProgress -Session $s -Activity $act -CoreCurrent $core -ElapsedSec 30
    $text = $lines -join "`n"
    Assert-True ($text -match "current web session: s-1") "session named"
    Assert-True ($text -match "router events seen: state.now:succeeded, eye.disable:succeeded") "router events"
    Assert-True ($text -match "last query/action kind: eye_disable klass=action") "last kind"
    Assert-True ($text -match "eye receipts seen: eye.disable=verified") "receipts"
    Assert-True ($text -match "latest Core state: agent.listening") "core state"
    # ONE tool call is still rendered (the array trap, through this path too).
    $one = [pscustomobject]@{ session_id = "s-2"; tool_calls = @([pscustomobject]@{ name = "state.now"; status = "succeeded" }); intents = @() }
    $text1 = (Format-QualificationProgress -Session $s -Activity $one -CoreCurrent $null -ElapsedSec 5) -join "`n"
    Assert-True ($text1 -match "router events seen: state.now:succeeded") "one call rendered"
    Assert-True ($text1 -match "last query/action kind: none") "no intents"
}

Test-Case "22. Get-CheckoutActionContractVersion reads receipt.py, and refuses a checkout without it" {
    $v = Get-CheckoutActionContractVersion -RepoRoot $repoRoot
    Assert-True ($v -is [int]) "an int"
    Assert-True ($v -ge 4) "M18.2 raised the contract to 4; the checkout answers $v"
    $tmpRoot = Join-Path $env:TEMP ("no-contract-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmpRoot | Out-Null
    try {
        $threw = $false
        try { $null = Get-CheckoutActionContractVersion -RepoRoot $tmpRoot } catch { $threw = $true }
        Assert-True $threw "a checkout with no receipt.py is refused, not read as v1"
        $dir = Join-Path $tmpRoot "services\api\app\actions"
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        [System.IO.File]::WriteAllText((Join-Path $dir "receipt.py"), "x = 1`r`nACTION_CONTRACT_VERSION: Final = 12`r`n")
        Assert-Equal 12 (Get-CheckoutActionContractVersion -RepoRoot $tmpRoot) "the Final-annotated form"
        [System.IO.File]::WriteAllText((Join-Path $dir "receipt.py"), "ACTION_CONTRACT_VERSION = 7`r`n")
        Assert-Equal 7 (Get-CheckoutActionContractVersion -RepoRoot $tmpRoot) "the bare form"
    }
    finally { Remove-Item -LiteralPath $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "23. Get-ReceiptRows reads receipts off production-shaped ledger rows, filtered by capability, oldest first" {
    # The shape app/actions/receipt.py::record_receipt writes: event_type action.receipt, the
    # capability in `action`, the receipt (no speech) in detail_json.
    $rows = @(
        [pscustomobject]@{ event_type = "action.receipt"; action = "display.wake"; occurred_at = "2026-09-07T05:00:02Z"; detail_json = [pscustomobject]@{ capability = "display.wake"; action_id = "a-2"; execution_status = "executed"; terminal_status = "verified"; error_class = $null; requested_state = "on"; observed_after = [pscustomobject]@{ server = [pscustomobject]@{ state = "on" } } } },
        [pscustomobject]@{ event_type = "alarm.firing"; action = $null; occurred_at = "2026-09-07T05:00:01Z"; detail_json = [pscustomobject]@{ alarm_id = "x" } },
        [pscustomobject]@{ event_type = "action.receipt"; action = "media.play"; occurred_at = "2026-09-07T05:00:05Z"; detail_json = [pscustomobject]@{ capability = "media.play"; action_id = "a-3"; execution_status = "failed"; terminal_status = "failed"; error_class = "blocked"; requested_state = "playing" } },
        [pscustomobject]@{ event_type = "action.receipt"; action = "media.play"; occurred_at = "2026-09-07T05:00:00Z"; detail_json = $null }
    )
    $all = Get-ReceiptRows -Rows $rows
    Assert-Equal 3 $all.Count "three receipts, the alarm row skipped"
    Assert-Equal "media.play" $all[0].Capability "oldest first, capability from `action` when detail_json is missing"
    Assert-Equal "" $all[0].Terminal "an older row has empty receipt fields"
    $wake = Get-ReceiptRows -Rows $rows -Capability "display.wake"
    Assert-True ($wake -is [array]) "ONE result is still an array"
    Assert-Equal 1 $wake.Count "filtered"
    Assert-Equal "verified" $wake[0].Terminal "terminal status read"
    Assert-Equal "on" $wake[0].Observed.server.state "observed_after kept"
    $play = Get-ReceiptRows -Rows $rows -Capability "media.play"
    Assert-Equal 2 $play.Count "both media.play rows"
    Assert-Equal "blocked" $play[1].ErrorClass "error class read"
    $none = Get-ReceiptRows -Rows $rows -Capability "alarm.start"
    Assert-Equal 0 $none.Count "none"
    $empty = Get-ReceiptRows -Rows $null
    Assert-Equal 0 $empty.Count "null rows"
}

Write-Host ""
Write-Host "owner-explain harness: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
