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

function Stop-WebShellProcess {
    <#
        Stop the web shell AND everything it started. Stop-Process on the PowerShell
        wrapper alone leaves `next dev` (node) listening on the port: the harness's dry run
        of 2026-09-06 left pid 17176 behind, its stdout pipe kept the calling command open,
        and the NEXT run would have found "the Core" answering in 3 s from a stale shell
        running old code. taskkill /T ends the tree; the absolute path matters because a
        spawned shell's PATH on this machine is not to be trusted.
    #>
    param([Parameter(Mandatory = $true)]$Process)
    if ($null -eq $Process) { return }
    $taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
    try {
        if (-not $Process.HasExited) { & $taskkill /PID $Process.Id /T /F 2>&1 | Out-Null }
    }
    catch { }
    try { if (-not $Process.HasExited) { Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue } } catch { }
}

function Get-ListeningProcess {
    <#
        Who is listening on a local TCP port: @{ Pid; Name; StartedAt } or $null. Used before
        starting a web shell, so a stale shell from an earlier run (old code!) is named and
        refused instead of silently qualifying the owner against it.
    #>
    param([Parameter(Mandatory = $true)][int]$Port)
    $conn = $null
    try { $conn = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) } catch { $conn = @() }
    if ($conn.Count -eq 0) { return $null }
    $owner = [int]$conn[0].OwningProcess
    $proc = $null
    try { $proc = Get-Process -Id $owner -ErrorAction SilentlyContinue } catch { $proc = $null }
    return [pscustomobject]@{
        Pid       = $owner
        Name      = $(if ($null -ne $proc) { [string]$proc.ProcessName } else { "unknown" })
        StartedAt = $(if ($null -ne $proc) { try { $proc.StartTime.ToString("o") } catch { "" } } else { "" })
    }
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
        [AllowNull()][scriptblock]$Qualifier = $null,
        # Ids the Core itself named on the UI-state bus after the run started (voice events
        # carry session_id). Accepted regardless of baseline and readiness: the owner's run
        # of 2026-09-06 (later) connected voice BEFORE the harness took its baseline, so the
        # live Core session was excluded as "old" while the bus was naming it every turn.
        # Correlation by the canonical session id, never inference from a state name.
        [AllowEmptyCollection()][string[]]$AcceptIds = @()
    )
    if ($null -eq $Qualifier) { $Qualifier = ${function:Test-ExplainQualification} }
    # sel-prefixed locals: this function invokes two callbacks (ActivityProbe, Qualifier),
    # and a callback resolves its free variables by dynamic scope, nearest first - a local
    # named $activity or $session here would shadow the caller's own inside its block.
    $selCandidates = @()
    foreach ($selSession in $Sessions) {
        $selId = [string]$selSession.session_id
        if (-not $selId) { continue }
        $selKind = [string]$selSession.client_kind
        if ($ClientKinds.Count -gt 0 -and $ClientKinds -notcontains $selKind) { continue }
        $selStarted = ConvertTo-SessionInstant -Raw ([string]$selSession.started_at)
        if ($null -eq $selStarted) { continue }
        $selAccepted = ($AcceptIds -contains $selId)
        if (-not $selAccepted) {
            if ($BaselineIds -contains $selId) { continue }
            # $null -ne <param> is the presence test. The value is used directly rather than
            # through .Value, because Windows PowerShell 5.1 binds a Nullable[T] parameter as a
            # plain T once it has a value, and StrictMode then refuses the .Value access.
            if ($null -ne $ReadyAt -and
                -not (Test-InstantAtOrAfter -Instant $selStarted -Floor ([DateTimeOffset]$ReadyAt) -ToleranceSec $ToleranceSec)) { continue }
            if ($null -ne $NotBefore -and
                -not (Test-InstantAtOrAfter -Instant $selStarted -Floor ([DateTimeOffset]$NotBefore) -ToleranceSec 0)) { continue }
        }
        $selCandidates += [pscustomobject]@{ Session = $selSession; Id = $selId; Started = $selStarted }
    }
    foreach ($selCandidate in ($selCandidates | Sort-Object -Property Started -Descending)) {
        $selActivity = $null
        try { $selActivity = & $ActivityProbe $selCandidate.Id } catch { $selActivity = $null }
        if ($null -eq $selActivity) { continue }
        $selQualifies = $false
        try { $selQualifies = [bool](& $Qualifier $selActivity) } catch { $selQualifies = $false }
        if ($selQualifies) {
            return [pscustomobject]@{ SessionId = $selCandidate.Id; Session = $selCandidate.Session; Activity = $selActivity; Started = $selCandidate.Started }
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

function Get-CoreQualifyingTools {
    <#
        The tool calls a Core voice session can make that prove the owner really spoke to it
        through the canonical router (docs\M18_ACTION_CONTRACT.md section 2). A function, not
        a $script: variable: a dot-sourced library's $script: scope is whichever script
        dot-sourced it, and a closure cannot see it at all.
    #>
    return , @("state.now", "eye.enable", "eye.disable", "activity.explain", "release.promote")
}

function Test-CoreQualification {
    <#  M18: the session made at least one succeeded call through the Core's router.  #>
    param($Activity)
    $tools = Get-CoreQualifyingTools
    $calls = Get-SucceededToolCalls -Activity $Activity
    $hits = @($calls | Where-Object { $tools -contains [string]$_.name })
    return ($hits.Count -gt 0)
}

function Get-SessionRouterSummary {
    <#
        What a session did, as three short strings for a diagnostic line or a give-up
        reason: every tool call with its status, every resolved intent with its class, and
        the eye/production receipts with their terminal status. "none" when empty.
    #>
    param([AllowNull()]$Activity)
    $calls = @()
    $receipts = @()
    foreach ($c in (Get-ArrayProperty -InputObject $Activity -Name "tool_calls")) {
        $name = [string](Get-OptionalProperty -InputObject $c -Name "name")
        $calls += ("{0}:{1}" -f $name, (Get-OptionalProperty -InputObject $c -Name "status"))
        if ($name -like "eye.*" -or $name -eq "release.promote") {
            $receipts += ("{0}={1}" -f $name, (Get-OptionalProperty -InputObject $c -Name "terminal_status"))
        }
    }
    $intents = @()
    foreach ($i in (Get-ArrayProperty -InputObject $Activity -Name "intents")) {
        $intents += ("{0}/{1}" -f (Get-OptionalProperty -InputObject $i -Name "intent"), (Get-OptionalProperty -InputObject $i -Name "klass"))
    }
    $last = "none"
    $all = Get-ArrayProperty -InputObject $Activity -Name "intents"
    if ($all.Count -gt 0) {
        $i = $all[$all.Count - 1]
        $last = "{0} klass={1} query_kind={2} capability={3}" -f (Get-OptionalProperty -InputObject $i -Name "intent"), (Get-OptionalProperty -InputObject $i -Name "klass"), (Get-OptionalProperty -InputObject $i -Name "query_kind"), (Get-OptionalProperty -InputObject $i -Name "capability")
    }
    return [pscustomobject]@{
        ToolCalls = $(if ($calls.Count) { $calls -join ", " } else { "none" })
        Intents   = $(if ($intents.Count) { $intents -join ", " } else { "none" })
        Receipts  = $(if ($receipts.Count) { $receipts -join ", " } else { "none" })
        LastKind  = $last
    }
}

function Get-CheckoutActionContractVersion {
    <#
        The action-contract version THIS CHECKOUT carries (app/actions/receipt.py's
        ACTION_CONTRACT_VERSION), so a harness gates the deployed Cloud Core against the code it
        would release rather than against a literal that goes stale with every contract bump
        (M18.2's harness said 4 while the tree had moved on). Throws when the file or the
        constant is missing: a harness with no contract to compare against must not run.
    #>
    param([Parameter(Mandatory)][string]$RepoRoot)
    $receipt = Join-Path $RepoRoot "services\api\app\actions\receipt.py"
    if (-not (Test-Path -LiteralPath $receipt)) { throw "this checkout has no action contract ($receipt); nothing to qualify" }
    $match = [regex]::Match([System.IO.File]::ReadAllText($receipt), '(?m)^ACTION_CONTRACT_VERSION\s*(?::\s*Final)?\s*=\s*(\d+)')
    if (-not $match.Success) { throw "ACTION_CONTRACT_VERSION not found in $receipt" }
    return [int]$match.Groups[1].Value
}

function ConvertTo-TextList {
    <#
        Every input as a [string], whatever shape it arrived in: a string, ONE character (a
        [char] has no ToLowerInvariant - the owner's M18.2 run crashed on exactly that), any
        scalar, an array of any of those, $null (-> empty). Empty strings are dropped.
    #>
    param([AllowNull()]$Value)
    $out = @()
    foreach ($item in (ConvertTo-Array -Value $Value)) {
        if ($null -eq $item) { continue }
        $s = [string]$item
        if ($s.Length -gt 0) { $out += $s }
    }
    return , $out
}

function Test-TextContainsAny {
    <#  True when Text contains any of Words, case-insensitively (invariant), after both are coerced to strings.  #>
    param([AllowNull()]$Text, [AllowNull()]$Words)
    $haystack = ([string]$Text).ToLowerInvariant()
    if ($haystack.Length -eq 0) { return $false }
    foreach ($w in (ConvertTo-TextList -Value $Words)) {
        if ($haystack.Contains($w.ToLowerInvariant())) { return $true }
    }
    return $false
}

function Get-SpeechTurns {
    <#
        Spoken turns from a session's client_events, paired BY ORDER: each first_audio opens a
        turn; the next response_done and audio_done close it. The stored rows carry kind and
        t_ms reliably; turn and payload are not relied on (the owner's real record of
        2026-09-06 had neither on these rows). AudibleMs is audio_done - first_audio;
        EndedAfterGeneration is audio_done later than response_done - i.e. playback, not the
        provider's response.done, ended SPEAKING (the M18.2 product rule).
    #>
    param([AllowNull()]$Events)
    $turns = @()
    $current = $null
    foreach ($e in (ConvertTo-Array -Value $Events)) {
        $kind = [string](Get-OptionalProperty -InputObject $e -Name "kind")
        if ($kind -notin @("first_audio", "response_done", "audio_done")) { continue }
        $t = [double](Get-OptionalProperty -InputObject $e -Name "t_ms")
        $p = Get-OptionalProperty -InputObject $e -Name "payload"
        switch ($kind) {
            "first_audio" {
                if ($null -ne $current) { $turns += $current }
                $current = [ordered]@{ first_audio_ms = $t; response_done_ms = $null; audio_done_ms = $null; basis = ""; audible_ms = $null; ended_after_generation = $false }
            }
            "response_done" { if ($null -ne $current -and $null -eq $current.response_done_ms) { $current.response_done_ms = $t } }
            "audio_done" {
                if ($null -eq $current) { continue }
                $current.audio_done_ms = $t
                $current.basis = $(if ($null -ne $p) { [string](Get-OptionalProperty -InputObject $p -Name "basis") } else { "" })
                $current.audible_ms = [math]::Round($t - $current.first_audio_ms)
                $current.ended_after_generation = ($null -ne $current.response_done_ms -and $t -gt $current.response_done_ms)
                $turns += $current
                $current = $null
            }
        }
    }
    if ($null -ne $current) { $turns += $current }
    return , $turns
}

function Test-SpokenAfterToolDone {
    <#
        "" when the session's first_audio of the same turn came AT OR AFTER the tool_done
        of the given call (the confirmation was spoken only after the terminal ACK);
        otherwise the reason it cannot be shown. Pure over the activity's client_events.
    #>
    param([AllowNull()]$Events, [string]$CallId)
    $done = $null
    foreach ($e in (ConvertTo-Array -Value $Events)) {
        if ([string](Get-OptionalProperty -InputObject $e -Name "kind") -ne "tool_done") { continue }
        $p = Get-OptionalProperty -InputObject $e -Name "payload"
        if ($null -ne $p -and [string](Get-OptionalProperty -InputObject $p -Name "call_id") -eq $CallId) { $done = $e; break }
    }
    if ($null -eq $done) { return "no tool_done event for $CallId" }
    $doneT = [double](Get-OptionalProperty -InputObject $done -Name "t_ms")
    $doneTurn = [int](Get-OptionalProperty -InputObject $done -Name "turn")
    foreach ($e in (ConvertTo-Array -Value $Events)) {
        if ([string](Get-OptionalProperty -InputObject $e -Name "kind") -ne "first_audio") { continue }
        $t = [double](Get-OptionalProperty -InputObject $e -Name "t_ms")
        $turn = [int](Get-OptionalProperty -InputObject $e -Name "turn")
        if ($turn -eq $doneTurn -and $t -ge $doneT) { return "" }
    }
    return "no first_audio after tool_done in turn $doneTurn"
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
        # Fail fast, inside the library, with the exact missing evidence (owner, 2026-09-06:
        # "remained indefinitely at waiting"). 0 disables either bound.
        #   ConnectWaitSec: no session of this run within this many seconds -> give up.
        #   RouterWaitSec:  a session of this run exists but has made no qualifying call
        #                   this many seconds after it was first seen -> give up, naming
        #                   the tool calls and intents it did make.
        [int]$ConnectWaitSec = 0,
        [int]$RouterWaitSec = 0,
        # Bounded diagnostic progress every ProgressEverySec (0 = none): the current session
        # of this run, its router events, last query/action kind, eye receipts, and the
        # Core's latest state (CoreProbe: scriptblock() -> the /v1/ui/state current object).
        [int]$ProgressEverySec = 0,
        [AllowNull()][scriptblock]$CoreProbe = $null,
        [scriptblock]$Log = { param($Line) Write-Host $Line -ForegroundColor DarkGray },
        # OPTIONAL extra policy: scriptblock(attempts, elapsedSec, sessions, newSessions) ->
        # a non-empty string to stop NOW with that reason, or $null/"" to keep waiting. It
        # receives everything it needs as arguments and must not reach for library
        # functions: a closure (GetNewClosure) cannot see a dot-sourced script's functions,
        # which is exactly how the owner's run of 2026-09-06 crashed.
        [AllowNull()][scriptblock]$GiveUp = $null,
        # Ids the Core named on the bus (Get-BusVoiceSessionIds): accepted as this run's
        # regardless of baseline/readiness. Optionally refreshed each poll via AcceptProbe
        # (scriptblock() -> string[]), because the owner may connect after the wait began.
        [AllowEmptyCollection()][string[]]$AcceptIds = @(),
        [AllowNull()][scriptblock]$AcceptProbe = $null
    )
    # Every local here is wait-prefixed. A callback invoked from this function resolves
    # its free variables by DYNAMIC scope, nearest first - so a plain local named $act or
    # $sessions in here would shadow the caller's own $act/$sessions inside its callback
    # (owner-explain.tests.ps1 12e/12g caught exactly that). Callbacks are handed what they
    # need as arguments; they must never depend on a name this function happens to use.
    $waitStarted = [double](& $Clock)
    $waitAttempts = 0
    $waitConnectedAt = $null
    $waitLastProgressAt = [double]::NegativeInfinity
    $waitProgress = @()
    $waitAccept = @($AcceptIds)
    while ($true) {
        $waitAttempts++
        if ($null -ne $AcceptProbe) {
            try { foreach ($waitId in @(& $AcceptProbe)) { if ($waitId -and $waitAccept -notcontains $waitId) { $waitAccept += $waitId } } } catch { }
        }
        $waitSessions = @(& $ListSessions)
        $waitSelected = Select-QualificationSession -Sessions $waitSessions -BaselineIds $BaselineIds `
            -ActivityProbe $ActivityProbe -ReadyAt $ReadyAt -NotBefore $NotBefore -Qualifier $Qualifier -AcceptIds $waitAccept
        $waitElapsed = [double](& $Clock) - $waitStarted
        if ($null -ne $waitSelected) {
            return [pscustomobject]@{ Selected = $waitSelected; Attempts = $waitAttempts; ElapsedSec = $waitElapsed; GaveUp = $null; Progress = $waitProgress }
        }
        if ($waitElapsed -ge $TimeoutSec) {
            return [pscustomobject]@{ Selected = $null; Attempts = $waitAttempts; ElapsedSec = $waitElapsed; GaveUp = "budget of $TimeoutSec s spent"; Progress = $waitProgress }
        }
        $waitNew = Get-NewSessions -Sessions $waitSessions -BaselineIds $BaselineIds -ReadyAt $ReadyAt -AcceptIds $waitAccept
        $waitNewest = $null
        if ($waitNew.Count -gt 0) {
            $waitSorted = @($waitNew | Sort-Object -Property { ConvertTo-SessionInstant -Raw ([string]$_.started_at) } -Descending)
            $waitNewest = $waitSorted[0]
            if ($null -eq $waitConnectedAt) { $waitConnectedAt = $waitElapsed }
        }
        if ($ConnectWaitSec -gt 0 -and $waitNew.Count -eq 0 -and $waitElapsed -ge $ConnectWaitSec) {
            $waitReason = "no web voice session connected within $ConnectWaitSec s (sessions of this run: 0; connect voice on /core)"
            return [pscustomobject]@{ Selected = $null; Attempts = $waitAttempts; ElapsedSec = $waitElapsed; GaveUp = $waitReason; Progress = $waitProgress }
        }
        if ($RouterWaitSec -gt 0 -and $null -ne $waitConnectedAt -and ($waitElapsed - $waitConnectedAt) -ge $RouterWaitSec) {
            $waitActivity = $null
            try { $waitActivity = & $ActivityProbe ([string]$waitNewest.session_id) } catch { $waitActivity = $null }
            $waitSummary = Get-SessionRouterSummary -Activity $waitActivity
            $waitReason = ("session {0} connected {1} s ago but made no succeeded router call ({2}) within {3} s; tool calls seen: {4}; intents resolved: {5}" -f `
                $waitNewest.session_id, [math]::Round($waitElapsed - $waitConnectedAt), ((Get-CoreQualifyingTools) -join " / "), $RouterWaitSec, $waitSummary.ToolCalls, $waitSummary.Intents)
            return [pscustomobject]@{ Selected = $null; Attempts = $waitAttempts; ElapsedSec = $waitElapsed; GaveUp = $waitReason; Progress = $waitProgress }
        }
        if ($null -ne $GiveUp) {
            $waitReason = $null
            try { $waitReason = [string](& $GiveUp $waitAttempts $waitElapsed $waitSessions $waitNew) } catch { $waitReason = $null }
            if ($waitReason) {
                return [pscustomobject]@{ Selected = $null; Attempts = $waitAttempts; ElapsedSec = $waitElapsed; GaveUp = $waitReason; Progress = $waitProgress }
            }
        }
        if ($ProgressEverySec -gt 0 -and ($waitElapsed - $waitLastProgressAt) -ge $ProgressEverySec) {
            $waitLastProgressAt = $waitElapsed
            $waitActivity = $null
            if ($null -ne $waitNewest) { try { $waitActivity = & $ActivityProbe ([string]$waitNewest.session_id) } catch { $waitActivity = $null } }
            $waitCore = $null
            if ($null -ne $CoreProbe) { try { $waitCore = & $CoreProbe } catch { $waitCore = $null } }
            $waitProgress = Format-QualificationProgress -Session $waitNewest -Activity $waitActivity -CoreCurrent $waitCore -ElapsedSec $waitElapsed
            foreach ($waitLine in $waitProgress) { & $Log $waitLine }
        }
        if ($null -ne $OnWaiting) { & $OnWaiting $waitAttempts $waitElapsed }
        & $Sleep $IntervalSec
    }
}

function Get-NewSessions {
    <#  The sessions of THIS run: not in the baseline, of the given kinds, started at or after ReadyAt.  #>
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Sessions,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$BaselineIds,
        [AllowNull()][Nullable[DateTimeOffset]]$ReadyAt = $null,
        [string[]]$ClientKinds = @("web"),
        [AllowEmptyCollection()][string[]]$AcceptIds = @()
    )
    $out = @()
    foreach ($nsSession in $Sessions) {
        $nsId = [string]$nsSession.session_id
        if (-not $nsId) { continue }
        if ($ClientKinds.Count -gt 0 -and $ClientKinds -notcontains [string]$nsSession.client_kind) { continue }
        $nsStarted = ConvertTo-SessionInstant -Raw ([string]$nsSession.started_at)
        if ($null -eq $nsStarted) { continue }
        if ($AcceptIds -notcontains $nsId) {
            if ($BaselineIds -contains $nsId) { continue }
            if ($null -ne $ReadyAt -and -not (Test-InstantAtOrAfter -Instant $nsStarted -Floor ([DateTimeOffset]$ReadyAt) -ToleranceSec 1)) { continue }
        }
        $out += $nsSession
    }
    return , $out
}

function Get-BusVoiceSessionIds {
    <#
        The realtime session ids the Core itself named on the UI-state bus since an instant:
        every voice-subsystem event carries the canonical session_id. Newest last, distinct.
        This is how a harness correlates to the session the Core is REALLY in.
    #>
    param([AllowNull()]$Events, [AllowNull()][Nullable[DateTimeOffset]]$Since = $null)
    $ids = @()
    foreach ($ev in (ConvertTo-Array -Value $Events)) {
        if ([string](Get-OptionalProperty -InputObject $ev -Name "subsystem") -ne "voice") { continue }
        $id = [string](Get-OptionalProperty -InputObject $ev -Name "session_id")
        if (-not $id) { continue }
        if ($null -ne $Since) {
            $at = ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $ev -Name "at"))
            if ($null -eq $at -or -not (Test-InstantAtOrAfter -Instant $at -Floor ([DateTimeOffset]$Since) -ToleranceSec 1)) { continue }
        }
        if ($ids -contains $id) { $ids = @($ids | Where-Object { $_ -ne $id }) }
        $ids += $id
    }
    return , $ids
}

function Get-EyeReceiptSteps {
    <#
        The owner's three-step eye test (aç, kapat, aç) read off a session's tool calls:
        which VERIFIED eye receipts arrived, in order, and how many of the expected steps
        (enable, disable, enable) they satisfy. Also every eye receipt seen, verified or
        not, so a failed step is reported with its own error class.
    #>
    param([AllowNull()]$Calls, [string[]]$Expected = @("eye.enable", "eye.disable", "eye.enable"))
    $receipts = @()
    foreach ($c in (ConvertTo-Array -Value $Calls)) {
        $name = [string](Get-OptionalProperty -InputObject $c -Name "name")
        if ($name -notlike "eye.*") { continue }
        $local = $null
        $after = Get-OptionalProperty -InputObject $c -Name "observed_after"
        if ($null -ne $after) { $local = Get-OptionalProperty -InputObject $after -Name "local" }
        # Built OUTSIDE the hashtable literal: a $( ) subexpression there unrolls a
        # one-element trace to a string (no .Count under StrictMode) and turns an empty one
        # into $null. Assigned first, then cast, it stays a string[] of any length.
        # The receipt stores the trace at its top level (the server clips it there); the
        # client's relay carries it under local. Either, top level first.
        $trace = ConvertTo-Array -Value (Get-OptionalProperty -InputObject $c -Name "action_trace")
        if ($trace.Count -eq 0 -and $null -ne $local) { $trace = ConvertTo-Array -Value (Get-OptionalProperty -InputObject $local -Name "action_trace") }
        $receipts += [pscustomobject]@{
            Name       = $name
            CallId     = [string](Get-OptionalProperty -InputObject $c -Name "call_id")
            Status     = [string](Get-OptionalProperty -InputObject $c -Name "status")
            Terminal   = [string](Get-OptionalProperty -InputObject $c -Name "terminal_status")
            ErrorClass = [string](Get-OptionalProperty -InputObject $c -Name "error_class")
            Speech     = [string](Get-OptionalProperty -InputObject $c -Name "speech_head")
            LocalState = $(if ($null -ne $local) { [string](Get-OptionalProperty -InputObject $local -Name "state") } else { "" })
            Track      = $(if ($null -ne $local) { [string](Get-OptionalProperty -InputObject $local -Name "media_track_ready_state") } else { "" })
            Trace      = [string[]]$trace
            SessionId  = [string](Get-OptionalProperty -InputObject $c -Name "session_id")
            CreatedAt  = [string](Get-OptionalProperty -InputObject $c -Name "created_at")
        }
    }
    $satisfied = 0
    $matched = @()
    foreach ($r in $receipts) {
        if ($satisfied -ge $Expected.Count) { break }
        if ($r.Name -eq $Expected[$satisfied] -and $r.Terminal -eq "verified") { $matched += $r; $satisfied++ }
    }
    return [pscustomobject]@{ Receipts = $receipts; Satisfied = $satisfied; Matched = $matched; Expected = $Expected; Done = ($satisfied -ge $Expected.Count) }
}

function Test-HiddenEyeMutation {
    <#
        Every durable eye row written by voice (reason voice:*) since the run must fall
        inside the window of some eye receipt of this run (started_at .. completed_at,
        widened by ToleranceSec) - otherwise a second, unreceipted path mutated the eye.
        Returns the unexplained rows' descriptions (empty = one canonical path).
    #>
    param([AllowNull()]$LedgerRows, [AllowNull()]$Receipts, [int]$ToleranceSec = 3, [int]$LeadSec = 15)
    # A receipt is either a ledger action.receipt ROW (its fields under detail_json) or the
    # receipt dict itself (fields at the top). The owner's sixth run found the check reading
    # started_at off the row's top level: no windows, every voice row "unexplained".
    $ids = @{}
    $windows = @()
    foreach ($r in (ConvertTo-Array -Value $Receipts)) {
        $src = Get-OptionalProperty -InputObject $r -Name "detail_json"
        if ($null -eq $src) { $src = $r }
        $rid = [string](Get-OptionalProperty -InputObject $src -Name "action_id")
        if ($rid) { $ids[$rid] = $true }
        $from = ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $src -Name "started_at"))
        $to = ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $src -Name "completed_at"))
        if ($null -eq $from) { continue }
        if ($null -eq $to) { $to = $from }
        # The browser writes the durable row BEFORE relaying the tool call (durable-first on
        # disable; camera-first then durable on enable), so a row legitimately precedes its
        # receipt's started_at - by the local action's duration, up to a permission prompt.
        $windows += [pscustomobject]@{ From = ([DateTimeOffset]$from).AddSeconds(-$LeadSec); To = ([DateTimeOffset]$to).AddSeconds($ToleranceSec); Id = $rid }
    }
    $unexplained = @()
    foreach ($row in (ConvertTo-Array -Value $LedgerRows)) {
        $detail = Get-OptionalProperty -InputObject $row -Name "detail_json"
        $reason = if ($null -ne $detail) { [string](Get-OptionalProperty -InputObject $detail -Name "reason") } else { "" }
        if (-not $reason.StartsWith("voice:")) { continue }
        # By identity first (contract §5.5: the row carries the action_id that caused it).
        $rowAction = if ($null -ne $detail) { [string](Get-OptionalProperty -InputObject $detail -Name "action_id") } else { "" }
        if ($rowAction -and $ids.ContainsKey($rowAction)) { continue }
        $at = ConvertTo-SessionInstant -Raw ([string](Get-OptionalProperty -InputObject $row -Name "occurred_at"))
        $covered = $false
        foreach ($w in $windows) { if ($null -ne $at -and ([DateTimeOffset]$at) -ge $w.From -and ([DateTimeOffset]$at) -le $w.To) { $covered = $true; break } }
        if (-not $covered) { $unexplained += ("{0} {1} ({2}{3})" -f (Get-OptionalProperty -InputObject $row -Name "occurred_at"), (Get-OptionalProperty -InputObject $row -Name "event_type"), $reason, $(if ($rowAction) { "; action_id " + $rowAction + " matches no receipt" } else { "; no action_id on the row" })) }
    }
    return , $unexplained
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
        $summary = Get-SessionRouterSummary -Activity $Activity
        $lines += ("               router events seen: {0}" -f $summary.ToolCalls)
        $lines += ("               last query/action kind: {0}" -f $summary.LastKind)
        $lines += ("               eye receipts seen: {0}" -f $summary.Receipts)
    }
    $lines += ("               latest Core state: {0}" -f $(if ($null -ne $CoreCurrent) { "{0} (subsystem {1}, session {2}, at {3})" -f (Get-OptionalProperty -InputObject $CoreCurrent -Name "state"), (Get-OptionalProperty -InputObject $CoreCurrent -Name "subsystem"), (Get-OptionalProperty -InputObject $CoreCurrent -Name "session_id"), (Get-OptionalProperty -InputObject $CoreCurrent -Name "at") } else { "unknown" }))
    return , $lines
}
