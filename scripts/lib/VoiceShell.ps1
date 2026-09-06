<#
.SYNOPSIS
    Web voice shell readiness and realtime-session correlation for the owner voice
    qualifications - pure decisions with injectable probes, so they can be tested.

.DESCRIPTION
    Owner incident (2026-09-04, M16 first qualification): owner-explain.ps1 started the web
    shell with a fire-and-forget Start-Process, slept eight seconds, and went on to wait for a
    realtime session. The shell never became available on localhost:3000, so no session could
    exist, and the harness failed with "no NEW realtime session since this script started"
    while the product itself worked the moment the owner started the shell by hand.

    Two decisions are made here, and only here:

      Wait-WebShellReady          - poll an HTTP probe until the shell answers /voice, with a
                                    bounded wait and a clock/sleep that tests can replace;
      Select-QualificationSession - of the realtime sessions the Cloud Core lists, the one
                                    this qualification owns: NOT in the baseline taken before
                                    the run (an older session never qualifies, however recent),
                                    started once the shell was ready, from a web client, and
                                    carrying a succeeded activity.explain call; the newest such
                                    session when several exist;
      Wait-QualificationSession   - keep asking for that session for a bounded time, so an
                                    owner who connects late is still detected.

    Windows PowerShell 5.1, ASCII only. The real HTTP probe and the real shell start live in
    Start-WebShellProcess / Test-WebShellReady; everything else is deterministic.
#>

Set-StrictMode -Version Latest

# The shape helpers (Get-ArrayProperty / Get-OptionalProperty): every activity record this
# library reads has one-element arrays in it somewhere.
. (Join-Path $PSScriptRoot "OwnerHarness.ps1")

function Test-WebShellReady {
    <#  One HTTP probe of the shell: $true when GET <Url> answers 2xx/3xx.  #>
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [scriptblock]$Probe = $null
    )
    if ($null -eq $Probe) {
        $Probe = {
            param($ProbeUrl)
            $request = [System.Net.HttpWebRequest]::Create($ProbeUrl)
            $request.Method = "GET"
            $request.Timeout = 5000
            $request.AllowAutoRedirect = $false
            try {
                $response = $request.GetResponse()
                try { return [int]$response.StatusCode } finally { $response.Close() }
            }
            catch [System.Net.WebException] {
                if ($null -ne $_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
                throw
            }
        }
    }
    try {
        $status = [int](& $Probe $Url)
        return ($status -ge 200 -and $status -lt 400)
    }
    catch {
        return $false
    }
}

function Wait-WebShellReady {
    <#
        Poll until the shell is ready or the budget is spent. Returns an object with Ready,
        Attempts and ElapsedSec. Clock and Sleep are injectable so a test runs in no time.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSec = 120,
        [int]$IntervalSec = 2,
        [scriptblock]$Probe = $null,
        [scriptblock]$Sleep = { param($Seconds) Start-Sleep -Seconds $Seconds },
        [scriptblock]$Clock = { [double](Get-Date).ToUniversalTime().Subtract([datetime]'1970-01-01').TotalSeconds }
    )
    $started = [double](& $Clock)
    $attempts = 0
    while ($true) {
        $attempts++
        if (Test-WebShellReady -Url $Url -Probe $Probe) {
            return [pscustomobject]@{ Ready = $true; Attempts = $attempts; ElapsedSec = ([double](& $Clock) - $started) }
        }
        $elapsed = [double](& $Clock) - $started
        if ($elapsed -ge $TimeoutSec) {
            return [pscustomobject]@{ Ready = $false; Attempts = $attempts; ElapsedSec = $elapsed }
        }
        & $Sleep $IntervalSec
    }
}

function Start-WebShellProcess {
    <#
        Start the web shell through the SAME script the owner runs by hand
        (scripts\voice\start-web-voice.ps1), in its own PowerShell process, with stdout and
        stderr captured to a log so a failure to start is visible instead of silent.
        Returns the process. Readiness is decided by Wait-WebShellReady, never assumed.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$Upstream,
        [int]$WebPort = 3000,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [string]$PnpmPath = "pnpm"
    )
    $starter = Join-Path $RepoRoot "scripts\voice\start-web-voice.ps1"
    if (-not (Test-Path $starter)) { throw "web shell starter not found: $starter" }
    $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $upstreamUri = [Uri]$Upstream
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$starter`"",
        "-NoPreflight", "-BrokerHost", $upstreamUri.Host, "-ApiPort", "$($upstreamUri.Port)",
        "-WebPort", "$WebPort", "-PnpmPath", "`"$PnpmPath`""
    )
    $errorLog = [IO.Path]::ChangeExtension($LogPath, ".err.log")
    return Start-Process -FilePath $powershell -ArgumentList $arguments -PassThru -WindowStyle Minimized `
        -RedirectStandardOutput $LogPath -RedirectStandardError $errorLog
}

function ConvertTo-SessionInstant {
    <#
        One session's started_at as a UTC DateTimeOffset, or $null when it is absent or
        malformed. Returning $null - rather than throwing, or falling back to "now", or
        falling back to a sentinel - is what makes a bad timestamp a DETERMINISTIC rejection
        of that one session instead of a failed run or an accidental match.

        AssumeUniversal matters: the Cloud Core emits `...Z`, but a naive string without an
        offset must not be read as local time, or a session would appear to start hours
        before or after it did depending on where the harness ran.
    #>
    param([AllowNull()][string]$Raw)
    if ([string]::IsNullOrWhiteSpace($Raw)) { return $null }
    $parsed = [DateTimeOffset]::MinValue
    $styles = [Globalization.DateTimeStyles]::RoundtripKind -bor
              [Globalization.DateTimeStyles]::AssumeUniversal -bor
              [Globalization.DateTimeStyles]::AllowWhiteSpaces
    if (-not [DateTimeOffset]::TryParse($Raw, [Globalization.CultureInfo]::InvariantCulture, $styles, [ref]$parsed)) {
        return $null
    }
    return $parsed.ToUniversalTime()
}

function Test-InstantAtOrAfter {
    <#
        Is $Instant at or after $Floor, allowing $ToleranceSec of slack, WITHOUT moving
        either endpoint?

        The bug this replaces: the floor was mutated with `.AddSeconds(-1)` to express the
        tolerance. In -VerifyOnly the floor was DateTime.MinValue (a sentinel for "no
        baseline"), and DateTime.MinValue has no room below it, so the harness died with
        ArgumentOutOfRangeException before it could look at a single session (2026-09-05).

        Subtracting two DateTimeOffsets cannot overflow: the widest possible difference is
        about ten thousand years, and TimeSpan holds roughly twenty-nine thousand. So the
        comparison is done on the DIFFERENCE, and neither endpoint is touched.
    #>
    param(
        [Parameter(Mandatory = $true)][DateTimeOffset]$Instant,
        [Parameter(Mandatory = $true)][DateTimeOffset]$Floor,
        [int]$ToleranceSec = 0
    )
    return (($Instant - $Floor).TotalSeconds -ge [double](-1 * [math]::Abs($ToleranceSec)))
}

function Select-QualificationSession {
    <#
        The session this qualification owns, or $null.

        Sessions: objects with session_id, client_kind, started_at (ISO), state.
        BaselineIds: ids that existed before the run - excluded unconditionally.
        ReadyAt: OPTIONAL. When given, the moment the web shell was confirmed ready; a
        session started before it cannot have come from this run. $null means there is no
        such moment, which is the honest state in -VerifyOnly: no shell was started, so
        nothing can be said about shell readiness. Absence is represented as absence, never
        as DateTime.MinValue standing in for it.
        NotBefore: OPTIONAL absolute floor. -VerifyOnly uses it to bound how far back a
        session may be and still be considered this owner's completed qualification, so
        "no ReadyAt" never means "any session ever".
        ActivityProbe: scriptblock(session_id) -> the session's activity record.
        Qualifier: OPTIONAL scriptblock(activity) -> bool deciding whether that record is
        the owner's qualification. The default is the M17 rule (a succeeded activity.explain
        call). M18 passes Test-CoreQualification instead: the Core session is recognised by
        WHAT it did (a live-state answer, an eye action, a briefing), never by one tool name -
        requiring activity.explain to prove that the Core's voice works was the wrong test
        (owner, 2026-09-06).
    #>
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Sessions,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$BaselineIds,
        [Parameter(Mandatory = $true)][scriptblock]$ActivityProbe,
        [AllowNull()][Nullable[DateTimeOffset]]$ReadyAt = $null,
        [AllowNull()][Nullable[DateTimeOffset]]$NotBefore = $null,
        [string[]]$ClientKinds = @("web"),
        [int]$ToleranceSec = 1,
        [AllowNull()][scriptblock]$Qualifier = $null
    )
    if ($null -eq $Qualifier) { $Qualifier = ${function:Test-ExplainQualification} }
    $candidates = @()
    foreach ($session in $Sessions) {
        $id = [string]$session.session_id
        if (-not $id -or $BaselineIds -contains $id) { continue }
        $kind = [string]$session.client_kind
        if ($ClientKinds.Count -gt 0 -and $ClientKinds -notcontains $kind) { continue }
        $started = ConvertTo-SessionInstant -Raw ([string]$session.started_at)
        if ($null -eq $started) { continue }
        # $null -ne <param> is the presence test. The value is used directly rather than
        # through .Value, because Windows PowerShell 5.1 binds a Nullable[T] parameter as a
        # plain T once it has a value, and StrictMode then refuses the .Value access.
        if ($null -ne $ReadyAt -and
            -not (Test-InstantAtOrAfter -Instant $started -Floor ([DateTimeOffset]$ReadyAt) -ToleranceSec $ToleranceSec)) { continue }
        if ($null -ne $NotBefore -and
            -not (Test-InstantAtOrAfter -Instant $started -Floor ([DateTimeOffset]$NotBefore) -ToleranceSec 0)) { continue }
        $candidates += [pscustomobject]@{ Session = $session; Id = $id; Started = $started }
    }
    foreach ($candidate in ($candidates | Sort-Object -Property Started -Descending)) {
        $activity = $null
        try { $activity = & $ActivityProbe $candidate.Id } catch { $activity = $null }
        if ($null -eq $activity) { continue }
        $qualifies = $false
        try { $qualifies = [bool](& $Qualifier $activity) } catch { $qualifies = $false }
        if ($qualifies) {
            return [pscustomobject]@{ SessionId = $candidate.Id; Session = $candidate.Session; Activity = $activity; Started = $candidate.Started }
        }
    }
    return $null
}

function Get-SucceededToolCalls {
    <#  The succeeded tool calls of an activity record, as an array whatever their number.  #>
    param($Activity)
    if ($null -eq $Activity) { return , @() }
    $calls = @()
    $raw = $null
    try { $raw = $Activity.tool_calls } catch { $raw = $null }
    foreach ($c in @($raw)) {
        if ($null -eq $c) { continue }
        if ([string]$c.status -eq "succeeded") { $calls += $c }
    }
    return , $calls
}

function Test-ExplainQualification {
    <#  M17: the session asked activity.explain and it succeeded.  #>
    param($Activity)
    # Assigned BEFORE it is piped: a `, @(...)` return piped directly arrives in the
    # pipeline as ONE item (the whole array), so `$_.name` would be a list of names and
    # the string comparison would match nothing (owner-explain.tests.ps1 caught it).
    $calls = Get-SucceededToolCalls -Activity $Activity
    $explained = @($calls | Where-Object { [string]$_.name -eq "activity.explain" })
    return ($explained.Count -gt 0)
}

#: The tool calls a Core voice session can make that prove the owner really spoke to it
#: through the canonical router (docs\M18_ACTION_CONTRACT.md section 2).
$script:CoreQualifyingTools = @("state.now", "eye.enable", "eye.disable", "activity.explain", "release.promote")

function Test-CoreQualification {
    <#  M18: the session made at least one succeeded call through the Core's router.  #>
    param($Activity)
    $calls = Get-SucceededToolCalls -Activity $Activity
    $hits = @($calls | Where-Object { $script:CoreQualifyingTools -contains [string]$_.name })
    return ($hits.Count -gt 0)
}

function Wait-QualificationSession {
    <#
        Ask for the qualification session until it exists or the budget is spent. Returns
        the selection (or $null) plus how long it looked. A late connection is a wait, not a
        failure: the owner may have needed a minute to open the page.
    #>
    param(
        [Parameter(Mandatory = $true)][scriptblock]$ListSessions,
        [Parameter(Mandatory = $true)][scriptblock]$ActivityProbe,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$BaselineIds,
        [AllowNull()][Nullable[DateTimeOffset]]$ReadyAt = $null,
        [AllowNull()][Nullable[DateTimeOffset]]$NotBefore = $null,
        [int]$TimeoutSec = 600,
        [int]$IntervalSec = 5,
        [scriptblock]$Sleep = { param($Seconds) Start-Sleep -Seconds $Seconds },
        [scriptblock]$Clock = { [double](Get-Date).ToUniversalTime().Subtract([datetime]'1970-01-01').TotalSeconds },
        [scriptblock]$OnWaiting = $null,
        [AllowNull()][scriptblock]$Qualifier = $null,
        # OPTIONAL scriptblock(attempts, elapsedSec, sessions) -> a non-empty string to stop
        # waiting NOW with that reason (returned as GaveUp), or $null/"" to keep waiting. A
        # harness uses it to fail fast with the exact missing evidence instead of sitting
        # out a ten-minute budget (owner, 2026-09-06: "remained indefinitely at waiting").
        [AllowNull()][scriptblock]$GiveUp = $null
    )
    $started = [double](& $Clock)
    $attempts = 0
    while ($true) {
        $attempts++
        $sessions = @(& $ListSessions)
        $selected = Select-QualificationSession -Sessions $sessions -BaselineIds $BaselineIds `
            -ActivityProbe $ActivityProbe -ReadyAt $ReadyAt -NotBefore $NotBefore -Qualifier $Qualifier
        $elapsed = [double](& $Clock) - $started
        if ($null -ne $selected) {
            return [pscustomobject]@{ Selected = $selected; Attempts = $attempts; ElapsedSec = $elapsed; GaveUp = $null }
        }
        if ($elapsed -ge $TimeoutSec) {
            return [pscustomobject]@{ Selected = $null; Attempts = $attempts; ElapsedSec = $elapsed; GaveUp = "budget of $TimeoutSec s spent" }
        }
        if ($null -ne $GiveUp) {
            $reason = $null
            try { $reason = [string](& $GiveUp $attempts $elapsed $sessions) } catch { $reason = $null }
            if ($reason) {
                return [pscustomobject]@{ Selected = $null; Attempts = $attempts; ElapsedSec = $elapsed; GaveUp = $reason }
            }
        }
        if ($null -ne $OnWaiting) { & $OnWaiting $attempts $elapsed }
        & $Sleep $IntervalSec
    }
}

function Get-NewSessions {
    <#  The sessions of THIS run: not in the baseline, of the given kinds, started at or after ReadyAt.  #>
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Sessions,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$BaselineIds,
        [AllowNull()][Nullable[DateTimeOffset]]$ReadyAt = $null,
        [string[]]$ClientKinds = @("web")
    )
    $out = @()
    foreach ($session in $Sessions) {
        $id = [string]$session.session_id
        if (-not $id -or $BaselineIds -contains $id) { continue }
        if ($ClientKinds.Count -gt 0 -and $ClientKinds -notcontains [string]$session.client_kind) { continue }
        $started = ConvertTo-SessionInstant -Raw ([string]$session.started_at)
        if ($null -eq $started) { continue }
        if ($null -ne $ReadyAt -and -not (Test-InstantAtOrAfter -Instant $started -Floor ([DateTimeOffset]$ReadyAt) -ToleranceSec 1)) { continue }
        $out += $session
    }
    return , $out
}

function Format-QualificationProgress {
    <#
        The bounded diagnostic line block printed while a harness waits: which session of
        this run exists, what router calls it made, its last intent, the eye receipts, and
        the Core's latest state - so the owner can see WHICH transition is missing.
        Pure: every input is handed in; returns the lines.
    #>
    param(
        [AllowNull()]$Session,
        [AllowNull()]$Activity,
        [AllowNull()]$CoreCurrent,
        [double]$ElapsedSec = 0
    )
    $lines = @()
    $lines += ("      [{0,4:N0} s] current web session: {1}" -f $ElapsedSec, $(if ($null -ne $Session) { "{0} ({1}, started {2})" -f $Session.session_id, $Session.state, $Session.started_at } else { "none yet - connect voice on /core" }))
    if ($null -ne $Activity) {
        $calls = Get-ArrayProperty -InputObject $Activity -Name "tool_calls"
        $router = @()
        $receipts = @()
        foreach ($c in $calls) {
            $name = [string](Get-OptionalProperty -InputObject $c -Name "name")
            $router += ("{0}:{1}" -f $name, (Get-OptionalProperty -InputObject $c -Name "status"))
            if ($name -like "eye.*" -or $name -eq "release.promote") {
                $receipts += ("{0}={1}" -f $name, (Get-OptionalProperty -InputObject $c -Name "terminal_status"))
            }
        }
        $intents = Get-ArrayProperty -InputObject $Activity -Name "intents"
        $last = if ($intents.Count -gt 0) { $intents[$intents.Count - 1] } else { $null }
        $lastKind = if ($null -ne $last) { "{0} klass={1} query_kind={2} capability={3}" -f (Get-OptionalProperty -InputObject $last -Name "intent"), (Get-OptionalProperty -InputObject $last -Name "klass"), (Get-OptionalProperty -InputObject $last -Name "query_kind"), (Get-OptionalProperty -InputObject $last -Name "capability") } else { "none" }
        $lines += ("               router events seen: {0}" -f $(if ($router.Count) { $router -join ", " } else { "none" }))
        $lines += ("               last query/action kind: {0}" -f $lastKind)
        $lines += ("               eye receipts seen: {0}" -f $(if ($receipts.Count) { $receipts -join ", " } else { "none" }))
    }
    $lines += ("               latest Core state: {0}" -f $(if ($null -ne $CoreCurrent) { "{0} (subsystem {1}, session {2}, at {3})" -f (Get-OptionalProperty -InputObject $CoreCurrent -Name "state"), (Get-OptionalProperty -InputObject $CoreCurrent -Name "subsystem"), (Get-OptionalProperty -InputObject $CoreCurrent -Name "session_id"), (Get-OptionalProperty -InputObject $CoreCurrent -Name "at") } else { "unknown" }))
    return , $lines
}
