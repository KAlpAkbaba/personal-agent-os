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
        ActivityProbe: scriptblock(session_id) -> the session's activity record; a session
        qualifies only when it carries a succeeded activity.explain call.
    #>
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Sessions,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$BaselineIds,
        [Parameter(Mandatory = $true)][scriptblock]$ActivityProbe,
        [AllowNull()][Nullable[DateTimeOffset]]$ReadyAt = $null,
        [AllowNull()][Nullable[DateTimeOffset]]$NotBefore = $null,
        [string[]]$ClientKinds = @("web"),
        [int]$ToleranceSec = 1
    )
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
        $calls = @($activity.tool_calls)
        $explained = @($calls | Where-Object { [string]$_.name -eq "activity.explain" -and [string]$_.status -eq "succeeded" })
        if ($explained.Count -gt 0) {
            return [pscustomobject]@{ SessionId = $candidate.Id; Session = $candidate.Session; Activity = $activity; Started = $candidate.Started }
        }
    }
    return $null
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
        [scriptblock]$OnWaiting = $null
    )
    $started = [double](& $Clock)
    $attempts = 0
    while ($true) {
        $attempts++
        $sessions = @(& $ListSessions)
        $selected = Select-QualificationSession -Sessions $sessions -BaselineIds $BaselineIds `
            -ActivityProbe $ActivityProbe -ReadyAt $ReadyAt -NotBefore $NotBefore
        $elapsed = [double](& $Clock) - $started
        if ($null -ne $selected) {
            return [pscustomobject]@{ Selected = $selected; Attempts = $attempts; ElapsedSec = $elapsed }
        }
        if ($elapsed -ge $TimeoutSec) {
            return [pscustomobject]@{ Selected = $null; Attempts = $attempts; ElapsedSec = $elapsed }
        }
        if ($null -ne $OnWaiting) { & $OnWaiting $attempts $elapsed }
        & $Sleep $IntervalSec
    }
}
