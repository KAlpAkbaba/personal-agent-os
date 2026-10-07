<#
.SYNOPSIS
    The cycle's lock on the Cloud Core: this machine's dead run is taken over, nobody else's.

.DESCRIPTION
    Measured 2026-10-07 08:19-08:21 (account switch): the old cycle (pid 20832, lock taken
    2026-10-06T18:48:57Z) was stopped, and both new cycles exited 3. The client judged the lock
    STALE by its age, so the acquire carried takeover_dead = false; the server, which counts the
    six hours from the holder's last status, said "ours" and refused - two clocks for one
    decision, and a report that named neither. The rule now (scripts/lib/TeamQueue.ps1):
    for THIS machine's lock the holder's pid is looked at BEFORE the age - gone is "dead" (the
    acquire says takeover_dead = true), alive is "ours" whatever the age; and a refusal names
    the server's answer.

    The store is a fake inside this file: Invoke-TeamApi is replaced by one that keeps every
    body it was sent and answers with the server's rule (services/api/app/team/store.py
    lock_decision): a holder whose status shows life is never stale; this machine's lock is
    given up only to takeover_dead = true.

    Run: powershell -NoProfile -File scripts\tests\team-cycle-lock.tests.ps1
#>

[CmdletBinding()]
param([string]$Filter = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")

$script:Failures = 0
$script:Passes = 0

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    if ($Filter -and $Name -notmatch $Filter) { return }
    try { & $Body; $script:Passes++; Write-Host "  PASS  $Name" }
    catch {
        $script:Failures++
        Write-Host "  FAIL  $Name" -ForegroundColor Red
        Write-Host "        $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Assert-Equal {
    param($Expected, $Actual, [string]$Because)
    if ($Expected -ne $Actual) { throw "$Because`n          expected: <$Expected>`n          actual  : <$Actual>" }
}

function Assert-True {
    param([bool]$Condition, [string]$Because)
    if (-not $Condition) { throw $Because }
}

# ------------------------------------------------------------------ the fake store

$script:FakeLock = $null
$script:FakeStatusAlive = $true
$script:FakeBodies = New-Object System.Collections.ArrayList
$script:FakeNow = [datetime]::SpecifyKind([datetime]"2026-10-07T08:20:00", [System.DateTimeKind]::Utc)

function Reset-FakeStore {
    param($Lock, [bool]$StatusAlive = $true)
    $script:FakeLock = $Lock
    $script:FakeStatusAlive = $StatusAlive
    $script:FakeBodies.Clear()
}

function Invoke-TeamApi {
    <# The fake: the lock route with the server's rule; every body is kept. #>
    param($Store, [string]$Method, [string]$Path, $Body = $null)
    if ($Path -ne "/v1/team/queue/lock") { throw "the fake has no route $Method $Path" }
    if ($Method -eq "GET") { return $script:FakeLock }
    [void]$script:FakeBodies.Add($Body)
    $lock = $script:FakeLock
    $base = @{ holder = ""; since = ""; pid = 0 }
    $kind = "free"
    if ($null -ne $lock -and [bool]$lock.held) {
        $base = @{ holder = [string]$lock.machine; since = [string]$lock.acquired_at; pid = [int]$lock.pid }
        $at = ConvertFrom-TeamTimestamp -Text ([string]$lock.acquired_at)
        $running = $script:FakeStatusAlive -or ($null -ne $at -and ($script:FakeNow - $at).TotalHours -lt 6)
        if (-not $running) { $kind = "stale" }
        elseif ([string]$lock.machine -eq [string]$Body.machine) { $kind = $(if ([bool]$Body.takeover_dead) { "dead" } else { "ours" }) }
        else { $kind = "held" }
    }
    $acquired = @("free", "stale", "dead") -contains $kind
    if ($acquired) { $script:FakeLock = [pscustomobject]@{ held = $true; machine = $Body.machine; cycle_id = $Body.cycle_id; pid = $Body.pid; acquired_at = "2026-10-07T08:20:00Z" } }
    return [pscustomobject]@{ acquired = $acquired; kind = $kind; holder = $base.holder; since = $base.since; pid = $base.pid }
}

$store = [pscustomobject]@{ Base = "http://fake"; Token = "t"; Baseline = @{}; Refused = (New-Object System.Collections.ArrayList) }

# The lock of the night of 2026-10-06, as the server had it.
function New-NightLock {
    param([string]$Machine = "MAIL", [string]$At = "2026-10-06T18:48:57Z", [int]$HolderPid = 20832)
    return [pscustomobject]@{ held = $true; machine = $Machine; cycle_id = "d20261006"; pid = $HolderPid; acquired_at = $At }
}

$script:Probed = New-Object System.Collections.ArrayList
$deadProbe = { param([int]$ProcessId, [string]$Since) [void]$script:Probed.Add($ProcessId); return $false }
$liveProbe = { param([int]$ProcessId, [string]$Since) [void]$script:Probed.Add($ProcessId); return $true }

# ------------------------------------------------------------------ the cases

Write-Host ""
Write-Host "the lock: this machine's dead run, and nobody else's"

Test-Case "this machine's lock, its pid gone, taken 13 hours ago: dead, taken over, takeover_dead = true sent" {
    $lock = New-NightLock
    Reset-FakeStore -Lock $lock -StatusAlive $true
    $script:Probed.Clear()
    $decision = Get-TeamLockDecision -Lock $lock -Machine "MAIL" -Now $script:FakeNow -ProcessAlive $deadProbe
    Assert-Equal -Expected "dead" -Actual $decision.Kind -Because "the pid is looked at before the age"
    Assert-Equal -Expected $true -Actual $decision.MayRun -Because "a dead run's lock is taken over"
    Assert-Equal -Expected "20832" -Actual (@($script:Probed) -join ",") -Because "the holder's pid was the one looked at"
    $entered = Enter-TeamLockApi -Store $store -Machine "MAIL" -CycleId "d20261007" -Decision $decision
    Assert-Equal -Expected 1 -Actual $script:FakeBodies.Count -Because "one acquire"
    Assert-Equal -Expected $true -Actual ([bool]$script:FakeBodies[0].takeover_dead) -Because "the acquire says the holder is dead"
    Assert-Equal -Expected $true -Actual $entered.Acquired -Because "the server gives this machine's lock up to takeover_dead"
    Assert-Equal -Expected "d20261007" -Actual $script:FakeLock.cycle_id -Because "the lock is the new cycle's now"
}

Test-Case "this machine's fresh lock, its pid gone: dead, taken over" {
    $lock = New-NightLock -At "2026-10-07T08:00:00Z"
    Reset-FakeStore -Lock $lock
    $decision = Get-TeamLockDecision -Lock $lock -Machine "mail" -Now $script:FakeNow -ProcessAlive $deadProbe
    Assert-Equal -Expected "dead" -Actual $decision.Kind -Because "twenty minutes old, the run is gone"
    $entered = Enter-TeamLockApi -Store $store -Machine "MAIL" -CycleId "d20261007" -Decision $decision
    Assert-Equal -Expected $true -Actual $entered.Acquired -Because "taken over"
}

Test-Case "this machine's lock with a live pid is never taken over, fresh or 13 hours old" {
    foreach ($at in @("2026-10-07T08:00:00Z", "2026-10-06T18:48:57Z", "")) {
        $script:Probed.Clear()
        $decision = Get-TeamLockDecision -Lock (New-NightLock -At $at) -Machine "MAIL" -Now $script:FakeNow -ProcessAlive $liveProbe
        Assert-Equal -Expected "ours" -Actual $decision.Kind -Because "'$at': the run is alive"
        Assert-Equal -Expected $false -Actual $decision.MayRun -Because "'$at': a running cycle of ours is never taken over"
        Assert-Equal -Expected "20832" -Actual (@($script:Probed) -join ",") -Because "'$at': the pid was looked at"
    }
}

Test-Case "another machine's fresh lock is never taken over, and its pid is not looked at here" {
    $lock = New-NightLock -Machine "GMKADIRAKBABA" -At "2026-10-07T07:00:00Z"
    foreach ($probe in @($deadProbe, $liveProbe)) {
        $script:Probed.Clear()
        $decision = Get-TeamLockDecision -Lock $lock -Machine "MAIL" -Now $script:FakeNow -ProcessAlive $probe
        Assert-Equal -Expected "held" -Actual $decision.Kind -Because "another machine's run"
        Assert-Equal -Expected $false -Actual $decision.MayRun -Because "two machines never run one cycle"
        Assert-Equal -Expected 0 -Actual $script:Probed.Count -Because "a pid of another machine means nothing in this process table"
    }
}

Test-Case "another machine's old lock is stale, and the acquire never says it is dead" {
    $lock = New-NightLock -Machine "GMKADIRAKBABA"
    Reset-FakeStore -Lock $lock -StatusAlive $false
    $decision = Get-TeamLockDecision -Lock $lock -Machine "MAIL" -Now $script:FakeNow -ProcessAlive $deadProbe
    Assert-Equal -Expected "stale" -Actual $decision.Kind -Because "13 hours, another machine"
    $entered = Enter-TeamLockApi -Store $store -Machine "MAIL" -CycleId "d20261007" -Decision $decision
    Assert-Equal -Expected $false -Actual ([bool]$script:FakeBodies[0].takeover_dead) -Because "only this machine's dead run is claimed dead"
    Assert-Equal -Expected $true -Actual $entered.Acquired -Because "the server agrees it is stale"
}

Test-Case "a refusal names the server's answer: its kind, the holder, and what was sent" {
    # Old by the client's clock, alive by the server's (its status shows life): refused.
    $lock = New-NightLock -Machine "GMKADIRAKBABA"
    Reset-FakeStore -Lock $lock -StatusAlive $true
    $decision = Get-TeamLockDecision -Lock $lock -Machine "MAIL" -Now $script:FakeNow -ProcessAlive $deadProbe
    $entered = Enter-TeamLockApi -Store $store -Machine "MAIL" -CycleId "d20261007" -Decision $decision
    Assert-Equal -Expected $false -Actual $entered.Acquired -Because "the server said held"
    Assert-Equal -Expected "held" -Actual $entered.Answer.kind -Because "the server's answer is kept"
    Assert-True -Condition ($entered.Stop -match "sunucu") -Because "the stop names the server: $($entered.Stop)"
    Assert-True -Condition ($entered.Stop -match "kind=held") -Because "the stop names the server's kind: $($entered.Stop)"
    Assert-True -Condition ($entered.Stop -match "GMKADIRAKBABA") -Because "the stop names the holder: $($entered.Stop)"
    Assert-True -Condition ($entered.Stop -match "takeover_dead=false") -Because "the stop names what was sent: $($entered.Stop)"
    Assert-True -Condition ($entered.Stop -match "hiçbir şey çalıştırmadı") -Because "the stop still says nothing ran: $($entered.Stop)"
    Assert-Equal -Expected "GMKADIRAKBABA" -Actual $script:FakeLock.machine -Because "the lock is still theirs"
}

Test-Case "the night's refusal itself: this machine's lock sent as not dead is answered 'ours', and the stop says so" {
    $lock = New-NightLock
    Reset-FakeStore -Lock $lock -StatusAlive $true
    $stale = [pscustomobject]@{ MayRun = $true; Kind = "stale"; Holder = "MAIL"; Since = "2026-10-06T18:48:57Z" }
    $entered = Enter-TeamLockApi -Store $store -Machine "MAIL" -CycleId "d20261007" -Decision $stale
    Assert-Equal -Expected $false -Actual $entered.Acquired -Because "the server keeps this machine's lock from a cycle that does not say it is dead"
    Assert-True -Condition ($entered.Stop -match "kind=ours") -Because "the stop names the server's kind: $($entered.Stop)"
}

Test-Case "the holder's pid, read from this machine's process table" {
    Assert-Equal -Expected $true -Actual (Test-TeamLockHolderAlive -ProcessId $PID -Since (Get-TeamTimestamp)) -Because "this process is alive"
    Assert-Equal -Expected $false -Actual (Test-TeamLockHolderAlive -ProcessId 0 -Since (Get-TeamTimestamp)) -Because "no pid is no holder"
    $gone = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "exit 0" -PassThru -WindowStyle Hidden
    $gone.WaitForExit()
    $gonePid = $gone.Id
    $gone.Dispose()
    if ($null -eq (Get-Process -Id $gonePid -ErrorAction SilentlyContinue)) {
        Assert-Equal -Expected $false -Actual (Test-TeamLockHolderAlive -ProcessId $gonePid -Since (Get-TeamTimestamp)) -Because "an ended process is no holder"
    }
    # A pid that is alive but started after the lock was taken is another process with a reused number.
    Assert-Equal -Expected $false -Actual (Test-TeamLockHolderAlive -ProcessId $PID -Since "2000-01-01T00:00:00Z") -Because "this process did not exist in 2000"
}

Test-Case "a live holder whose lock the server stamped before it started (clock skew up to 15 min) is still the holder" {
    # In API mode acquired_at is the server's clock and StartTime is this machine's: a local clock
    # ahead of the server makes the stamp look older than the process (inspector, 2026-10-07).
    $started = (Get-Process -Id $PID).StartTime.ToUniversalTime()
    foreach ($minutes in @(2, 10, 14)) {
        $since = Get-TeamTimestamp -Now $started.AddMinutes(-$minutes)
        Assert-Equal -Expected $true -Actual (Test-TeamLockHolderAlive -ProcessId $PID -Since $since) -Because "a stamp $minutes min before the start is skew, not a reused pid"
    }
    $since = Get-TeamTimestamp -Now $started.AddMinutes(-30)
    Assert-Equal -Expected $false -Actual (Test-TeamLockHolderAlive -ProcessId $PID -Since $since) -Because "a process started 30 min after the lock is a reused pid"
}

Test-Case "the cycle asks the pid through the decision and takes the lock through Enter-TeamLockApi" {
    $text = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\team\cycle.ps1"), [System.Text.Encoding]::UTF8)
    Assert-True -Condition ($text -match 'Get-TeamLockDecision\s[^\r\n]*-ProcessAlive') -Because "the cycle gives the decision its process table"
    Assert-True -Condition ($text -match 'Enter-TeamLockApi\s') -Because "the cycle takes the lock through the one function the tests drive"
    Assert-True -Condition ($text -notmatch 'Set-TeamLockApi\s') -Because "no second acquire path beside it"
}

Write-Host ""
Write-Host ("{0} passed, {1} failed" -f $script:Passes, $script:Failures)
if ($script:Failures -gt 0) { exit 1 }
exit 0
