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

function Select-QualificationSession {
    <#
        The session this qualification owns, or $null.

        Sessions: objects with session_id, client_kind, started_at (ISO), state.
        BaselineIds: ids that existed before the run - excluded unconditionally.
        ReadyAt: the UTC time the shell was confirmed ready; sessions started before it
        cannot have come from this run.
        ActivityProbe: scriptblock(session_id) -> the session's activity record; a session
        qualifies only when it carries a succeeded activity.explain call.
    #>
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Sessions,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$BaselineIds,
        [Parameter(Mandatory = $true)][datetime]$ReadyAt,
        [Parameter(Mandatory = $true)][scriptblock]$ActivityProbe,
        [string[]]$ClientKinds = @("web")
    )
    $candidates = @()
    foreach ($session in $Sessions) {
        $id = [string]$session.session_id
        if (-not $id -or $BaselineIds -contains $id) { continue }
        $kind = [string]$session.client_kind
        if ($ClientKinds.Count -gt 0 -and $ClientKinds -notcontains $kind) { continue }
        $startedRaw = [string]$session.started_at
        if (-not $startedRaw) { continue }
        $started = ([datetime]::Parse($startedRaw, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AdjustToUniversal)).ToUniversalTime()
        if ($started -lt $ReadyAt.ToUniversalTime().AddSeconds(-1)) { continue }
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
        [Parameter(Mandatory = $true)][datetime]$ReadyAt,
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
        $selected = Select-QualificationSession -Sessions $sessions -BaselineIds $BaselineIds -ReadyAt $ReadyAt -ActivityProbe $ActivityProbe
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
