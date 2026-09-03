<#
.SYNOPSIS
    M13 real browser action through the whole owner path: Cloud Core -> device command ->
    DeviceService (Session 0) -> Session Companion (owner session) -> Browser Worker ->
    real Chrome -> result back through the same path, with correlated evidence.

.DESCRIPTION
    Deterministic and harmless: open https://example.com/ (a stable public page), read its
    title and visible text, run one web search, and prove the risk policy by asking for a
    download the research policy must refuse. Every step is one device command with its own
    trace id and idempotency key, awaited to a terminal ack, and read back from the Cloud
    Core's durable command rows (command_id, trace_id, created_at, terminal_at, result).

    "Executed in the installed owner-session worker, not a simulator" is proven from this
    machine, not asserted: while the browser session is open the script records the
    companion process image (must be the installed companion), the worker process (python
    from the installed browser tree, parented by that companion, running as this user) and
    the Chrome process using the PagentOS profile under ProgramData; afterwards it reads the
    companion's audit log for the browser_request rows written for these commands.

    The owner credential is typed into a masked prompt, exchanged for one session that is
    revoked at the end, and never printed. Results are ids, timings, a page title and a
    short text excerpt from example.com; no secret, no cookie, no profile path leaves the
    device except in this report you choose to paste.

.EXAMPLE
    .\scripts\browser\real-browser-smoke.ps1 -OutFile browser-smoke-1.json
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    # Full base URL; overrides BrokerHost/ApiPort (the dev harness passes http://127.0.0.1:<port>).
    [string]$BaseUrl = "",
    # Pick a device explicitly (id or exact name); default: the online device advertising browser.chrome.
    [string]$Device = "",
    [string]$OutFile = "",
    [string]$SearchQuery = "Personal Agent OS",
    # full = example.com + inspect/extract + search + policy refusal (M13 smoke, PROVEN_REAL);
    # search = session + one browser.search through the provider abstraction; PASS only when
    # the recorded provider is the requested primary (google) with no fallback.
    # lifecycle = one visible Chrome window, several operations on the SAME session (identity
    # proven per command: session_uid, browser_pid, tab_count; exactly one PagentOS-profile
    # Chrome process throughout), then a clean exit (zero PagentOS-profile Chrome processes).
    [ValidateSet("full", "search", "lifecycle")][string]$Mode = "full",
    [string]$ExpectProvider = "google",
    # Run the elevated installer first (one UAC prompt; journaled engine, preserves broker
    # endpoints and identity) and wait for the device to come back online on the Cloud Core,
    # so a stale installed worker is updated in the same action as the qualification.
    [switch]$UpdateAgentFirst,
    # browser.search response schema this script consumes (BROWSER_CAPABILITIES.md §3).
    [int]$RequiredSearchSchema = 2,
    # Search mode only: interstitial=handoff (contract §3a). If Google shows a verification or
    # consent page, Chrome is brought to the front and this script WAITS for you to complete
    # it (nothing is solved or bypassed), then re-issues the same search on the same session.
    [switch]$Handoff,
    [int]$HandoffTimeoutSec = 600,
    # DEV/TEST ONLY: take an already-minted owner session token from PAGENTOS_SMOKE_TOKEN
    # instead of the masked credential prompt (the e2e harness uses this; never for the owner).
    [switch]$SessionTokenFromEnv,
    # Skip the local process/audit evidence (when this script does not run on the device itself).
    [switch]$SkipLocalEvidence,
    [int]$CommandTimeoutSec = 90
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "..\lib\NativeProcess.ps1")
. (Join-Path $PSScriptRoot "..\lib\HttpJson.ps1")

if (-not $BaseUrl) { $BaseUrl = "http://${BrokerHost}:$ApiPort" }
$BaseUrl = $BaseUrl.TrimEnd('/')
$runId = "browser-smoke-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$startedAt = (Get-Date).ToUniversalTime()

# ------------------------------------------------------------------ owner session

$token = $null
$mintedId = $null
if ($SessionTokenFromEnv) {
    $token = [Environment]::GetEnvironmentVariable("PAGENTOS_SMOKE_TOKEN")
    if (-not $token) { throw "-SessionTokenFromEnv given but PAGENTOS_SMOKE_TOKEN is empty" }
}
else {
    $secure = Read-Host -Prompt "Cloud Owner Credential (input is hidden; exchanged for one session, then dropped)" -AsSecureString
    if ($secure.Length -eq 0) { throw "empty credential; nothing done" }
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $credential = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    try {
        $body = @{ owner_credential = $credential; client_kind = "cli"; label = $runId } | ConvertTo-Json -Compress
        $issued = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body $body
    }
    finally { $credential = $null; $body = $null }
    $token = Get-OptionalProperty -InputObject $issued -Name "token"
    if (-not $token) { throw "the identity service issued no session token (wrong credential? it never says which)" }
    $mintedId = Get-OptionalProperty -InputObject $issued -Name "session_id"
}
$headers = @{ Authorization = "Bearer $token" }

function Get-Json { param([string]$Path) return Invoke-JsonUtf8 -Uri "$BaseUrl$Path" -Headers $headers -TimeoutSec 30 }
function Post-Json {
    param([string]$Path, $Body, [string]$TraceId = "")
    $h = @{}
    foreach ($k in $headers.Keys) { $h[$k] = $headers[$k] }
    if ($TraceId) { $h["X-Trace-Id"] = $TraceId }
    $json = if ($Body -is [string]) { $Body } else { $Body | ConvertTo-Json -Depth 8 -Compress }
    return Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl$Path" -Headers $h -Body $json -TimeoutSec 30
}

if ($UpdateAgentFirst) {
    $installer = Join-Path (Split-Path -Parent $PSScriptRoot) "install-device-service.ps1"
    Write-Host "updating the installed agent first (elevated installer, journaled deployment; one UAC prompt)..."
    $proc = Start-Process -FilePath (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -Verb RunAs -Wait -PassThru `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$installer`"")
    if ($proc.ExitCode -ne 0) {
        throw "the installer exited $($proc.ExitCode); see the newest log under $env:ProgramData\PagentOS\install-logs before rerunning"
    }
    Write-Host "installer finished (exit 0); waiting for the device to reconnect to the Cloud Core..."
}

$evidence = [ordered]@{
    run_id       = $runId
    update_agent = [bool]$UpdateAgentFirst
    started_at   = $startedAt.ToString("o")
    cloud        = $BaseUrl
    device       = $null
    selection    = $null
    commands     = @()
    local        = $null
    verdict      = $null
    finished_at  = $null
}

try {
    # ---------------------------------------------------------------- device
    $chosen = $null
    $waitUntil = (Get-Date).AddSeconds($(if ($UpdateAgentFirst) { 150 } else { 1 }))
    do {
        $listing = Get-Json "/v1/devices"
        $devices = @(Get-OptionalProperty -InputObject $listing -Name "devices")
        foreach ($d in $devices) {
            $caps = @(Get-OptionalProperty -InputObject $d -Name "capabilities")
            $status = [string](Get-OptionalProperty -InputObject $d -Name "status")
            if ($Device) {
                if (([string]$d.device_id -eq $Device -or [string]$d.name -eq $Device) -and (-not $UpdateAgentFirst -or $status -eq "online")) { $chosen = $d; break }
            }
            elseif ($status -eq "online" -and ($caps -contains "browser.chrome")) { $chosen = $d; break }
        }
        if ($null -eq $chosen -and (Get-Date) -lt $waitUntil) { Start-Sleep -Seconds 3 }
    } while ($null -eq $chosen -and (Get-Date) -lt $waitUntil)
    if ($null -eq $chosen) {
        Write-Host "devices known to the Cloud Core:"
        foreach ($d in $devices) { Write-Host ("  {0}  {1,-8} {2}  caps={3}" -f $d.device_id, $d.status, $d.name, @(Get-OptionalProperty -InputObject $d -Name "capabilities").Count) }
        throw "no online device advertising browser.chrome (or -Device did not match)"
    }
    $deviceId = [string]$chosen.device_id
    $chosenCaps = @(Get-OptionalProperty -InputObject $chosen -Name "capabilities")
    $evidence.device = [ordered]@{
        device_id = $deviceId; name = [string]$chosen.name; status = [string]$chosen.status
        capabilities = $chosenCaps.Count; browser_family = ($chosenCaps -contains "browser.chrome")
        last_seen_at = (Get-OptionalProperty -InputObject $chosen -Name "last_seen_at")
    }
    Write-Host "device: $($chosen.name) ($deviceId) status=$($chosen.status) capabilities=$($chosenCaps.Count) browser.chrome=$($chosenCaps -contains 'browser.chrome')"
    if (-not ($chosenCaps -contains "browser.chrome")) { throw "the device does not advertise browser.chrome; the agent update did not take" }

    # Cloud-side device selection (M13 devices layer), when the released API has it.
    try {
        $sel = Post-Json "/v1/devices/select" @{ capability = "browser.chrome" }
        $evidence.selection = $sel
        Write-Host "selection: $((ConvertTo-Json -InputObject $sel -Compress -Depth 4))"
    }
    catch { Write-Host "selection endpoint not answered ($($_.Exception.Message.Substring(0, [Math]::Min(80, $_.Exception.Message.Length)))); continuing" }

    # ---------------------------------------------------------------- commands
    $sessionId = "$runId-s1"
    $step = 0
    function Invoke-DeviceCommand {
        param([string]$Capability, [hashtable]$Payload, [switch]$ExpectFailure, [string]$ExpectedErrorClass = "")
        $script:step++
        $traceId = "$runId-$($script:step)-" + ($Capability -replace '[^a-z_]', '')
        $key = "${runId}:$($script:step):${Capability}"
        $created = Post-Json "/v1/devices/$deviceId/commands" @{ capability = $Capability; payload = $Payload; idempotency_key = $key; timeout_s = $CommandTimeoutSec } -TraceId $traceId
        $commandId = [string]$created.command_id
        $deadline = (Get-Date).AddSeconds($CommandTimeoutSec + 15)
        $row = $null
        do {
            Start-Sleep -Milliseconds 400
            $row = Get-Json "/v1/devices/$deviceId/commands/$commandId"
        } while ((Get-Date) -lt $deadline -and $row.status -notin @("succeeded", "failed", "expired", "cancelled"))
        $err = Get-OptionalProperty -InputObject $row -Name "error"
        $errClass = if ($null -ne $err) { [string](Get-OptionalProperty -InputObject $err -Name "class") } else { "" }
        $record = [ordered]@{
            step = $script:step; capability = $Capability; command_id = $commandId; trace_id = $traceId
            trace_id_recorded = [string](Get-OptionalProperty -InputObject $row -Name "trace_id")
            status = [string]$row.status; error_class = $errClass
            created_at = (Get-OptionalProperty -InputObject $row -Name "created_at"); delivered_at = (Get-OptionalProperty -InputObject $row -Name "delivered_at"); terminal_at = (Get-OptionalProperty -InputObject $row -Name "terminal_at")
            result = (Get-OptionalProperty -InputObject $row -Name "result")
        }
        $script:evidence.commands += $record
        Write-Host ("  [{0}] {1,-24} {2,-9} command_id={3} trace={4} {5}" -f $script:step, $Capability, $row.status, $commandId, $traceId, $(if ($errClass) { "error=$errClass" } else { "" }))
        if ($record.trace_id_recorded -ne $traceId) { throw "trace id not correlated on the Cloud Core row: sent $traceId, recorded '$($record.trace_id_recorded)'" }
        if ($ExpectFailure) {
            if ($row.status -ne "failed" -or ($ExpectedErrorClass -and $errClass -ne $ExpectedErrorClass)) {
                throw "$Capability was expected to fail with $ExpectedErrorClass but ended $($row.status) $errClass"
            }
        }
        elseif ($row.status -ne "succeeded") {
            $msg = if ($null -ne $err) { [string](Get-OptionalProperty -InputObject $err -Name "message") } else { "" }
            throw "$Capability ended $($row.status) $errClass $msg"
        }
        return $row
    }

    Write-Host "commands (each awaited to its terminal ack on the Cloud Core):"
    $status = Invoke-DeviceCommand -Capability "browser.worker_status" -Payload @{}
    $browser = Get-OptionalProperty -InputObject $status.result -Name "browser"
    $contracts = Get-OptionalProperty -InputObject $status.result -Name "contracts"
    $searchSchema = 0
    if ($null -ne $contracts) { $v = Get-OptionalProperty -InputObject $contracts -Name "browser.search"; if ($null -ne $v) { $searchSchema = [int]$v } }
    Write-Host "      worker $($status.result.worker_version), browser channel=$($browser.channel) version=$($browser.version) available=$($browser.available), browser.search schema=$searchSchema"
    $evidence.worker = [ordered]@{ worker_version = [string]$status.result.worker_version; search_schema = $searchSchema; browser_version = [string]$browser.version }
    if ($Mode -eq "search" -and $searchSchema -lt $RequiredSearchSchema) {
        throw "contract/version mismatch: the installed worker ($($status.result.worker_version)) answers browser.search with schema $searchSchema; this qualification needs schema $RequiredSearchSchema (provider evidence). The installed tree predates the search-provider change - rerun with -UpdateAgentFirst (one UAC prompt) to update it through the journaled installer."
    }

    $opened = Invoke-DeviceCommand -Capability "browser.session_open" -Payload @{
        session_id = $sessionId; profile = "research"
        policy = @{ allowed_risk_classes = @("READ", "NAVIGATE"); visible = $true }; channel = "chrome"
    }
    Write-Host "      session created=$($opened.result.created) channel=$($opened.result.channel) browser=$($opened.result.browser_version)"

    if ($Mode -eq "lifecycle") {
        # Runtime invariant (ADR-0050 item 14): one research job = one worker + one Chrome
        # process/profile + one window; every command reuses it; bounded tabs; clean exit.
        $profileMarker = "PagentOS\companion\browser\profile"
        function Get-ProfileChromes {
            if ($SkipLocalEvidence) { return @() }
            return @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*$profileMarker*" -and $_.CommandLine -notlike "*--type=*" })
        }
        function Get-ChromeWindowCount {
            param($Pids)
            if (@($Pids).Count -eq 0) { return 0 }
            return @(Get-Process -Id $Pids -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 }).Count
        }
        $lc = Get-OptionalProperty -InputObject $opened.result -Name "lifecycle"
        if ($null -eq $lc) { throw "contract/version mismatch: session_open returned no 'lifecycle' identity; the installed worker predates the lifecycle guards - rerun with -UpdateAgentFirst" }
        $sessionUid = [string]$lc.session_uid; $browserPid = [int]$lc.browser_pid
        Write-Host "      identity: session_uid=$sessionUid browser_pid=$browserPid tab_count=$($lc.tab_count) max_tabs=$($lc.max_tabs) max_windows=$($lc.max_windows) reused=$($lc.reused)"
        # ADR-0050 item 15 guards: the installed worker must be the one with the OS-level launch
        # lock, the kill-on-close job object and the launch classification; an older worker
        # (no such fields) is a contract mismatch, never a pass.
        $workerPid = Get-OptionalProperty -InputObject $lc -Name "worker_pid"
        $launchKind = Get-OptionalProperty -InputObject $lc -Name "launch_kind"
        $launchLock = Get-OptionalProperty -InputObject $lc -Name "launch_lock"
        $jobAssigned = Get-OptionalProperty -InputObject $lc -Name "job_object_assigned"
        if ($null -eq $workerPid -or $null -eq $launchKind -or $null -eq $jobAssigned) { throw "contract/version mismatch: session_open lifecycle lacks worker_pid/launch_kind/job_object_assigned; the installed worker predates the launch guards (ADR-0050 item 15) - rerun with -UpdateAgentFirst" }
        Write-Host "      guards: worker_pid=$workerPid launch_kind=$launchKind launch_lock=$launchLock job_object_assigned=$jobAssigned"
        if (-not $SkipLocalEvidence) {
            if ($jobAssigned -ne $true) { throw "the worker could not place its Chrome in a kill-on-close job object (job_object_assigned=$jobAssigned)" }
            if (-not (Get-Process -Id ([int]$workerPid) -ErrorAction SilentlyContinue)) { throw "the worker reports worker_pid=$workerPid but no such process exists" }
        }
        function Get-OwnerChromeWindowCount {
            # windows of chrome.exe main processes that are NOT on the PagentOS profile: the owner's
            # own Chrome must never gain a window from this test
            if ($SkipLocalEvidence) { return 0 }
            $ownerPids = @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -notlike "*$profileMarker*" -and $_.CommandLine -notlike "*--type=*" } | ForEach-Object { $_.ProcessId })
            return (Get-ChromeWindowCount $ownerPids)
        }
        $ownerWindowsBefore = Get-OwnerChromeWindowCount
        $chromes = Get-ProfileChromes
        $startPids = @($chromes | ForEach-Object { $_.ProcessId })
        Write-Host "      PagentOS-profile Chrome main processes: $(@($chromes).Count) (pids $($startPids -join ',')); windows: $(Get-ChromeWindowCount $startPids)"
        if (-not $SkipLocalEvidence) {
            if (@($chromes).Count -ne 1) { throw "browser_lifecycle_violation: expected exactly 1 PagentOS-profile Chrome process, found $(@($chromes).Count)" }
            if ([int]$chromes[0].ProcessId -ne $browserPid) { throw "the worker reports browser_pid=$browserPid but the profile is held by pid $($chromes[0].ProcessId)" }
        }
        $ops = @(
            @{ cap = "browser.navigate"; payload = @{ url = "https://example.com/"; timeout_ms = 30000 } },
            @{ cap = "browser.inspect"; payload = @{} },
            @{ cap = "browser.extract"; payload = @{ mode = "text"; max_chars = 300 } },
            @{ cap = "browser.tab_new"; payload = @{ url = "https://example.org/" } },
            @{ cap = "browser.tab_list"; payload = @{} },
            @{ cap = "browser.inspect"; payload = @{} },
            @{ cap = "browser.tab_close"; payload = @{ index = 1 } },
            @{ cap = "browser.navigate"; payload = @{ url = "https://example.net/"; timeout_ms = 30000 } },
            @{ cap = "browser.back"; payload = @{} },
            @{ cap = "browser.forward"; payload = @{} },
            @{ cap = "browser.fetch_evidence"; payload = @{ url = "https://example.com/"; tab = "new"; excerpt_chars = 200 } },
            @{ cap = "browser.worker_status"; payload = @{} }
        )
        $evidence.lifecycle = [ordered]@{ session_uid = $sessionUid; browser_pid = $browserPid; worker_pid = $workerPid; launch_kind = $launchKind; launch_lock = $launchLock; job_object_assigned = $jobAssigned; owner_chrome_windows_before = $ownerWindowsBefore; start_processes = @($chromes).Count; operations = @() }
        $n = 0
        foreach ($op in $ops) {
            $n++
            $payload = @{}
            foreach ($k in $op.payload.Keys) { $payload[$k] = $op.payload[$k] }
            if ($op.cap -ne "browser.worker_status") { $payload["session_id"] = $sessionId }
            $r = Invoke-DeviceCommand -Capability $op.cap -Payload $payload
            $rl = Get-OptionalProperty -InputObject $r.result -Name "lifecycle"
            $now = Get-ProfileChromes
            $line = [ordered]@{ n = $n; capability = $op.cap; session_uid = $(if ($rl) { [string]$rl.session_uid } else { $null }); browser_pid = $(if ($rl) { [int]$rl.browser_pid } else { $null }); tab_count = $(if ($rl) { [int]$rl.tab_count } else { $null }); processes = @($now).Count; windows = (Get-ChromeWindowCount @($now | ForEach-Object { $_.ProcessId })) }
            $evidence.lifecycle.operations += $line
            Write-Host ("      op {0,2} {1,-24} session_uid_same={2} browser_pid_same={3} tabs={4} processes={5} windows={6}" -f $n, $op.cap, ($line.session_uid -eq $sessionUid), ($line.browser_pid -eq $browserPid), $line.tab_count, $line.processes, $line.windows)
            if ($op.cap -ne "browser.worker_status") {
                if ($null -eq $rl) { throw "op $n ($($op.cap)) returned no lifecycle identity" }
                if ($line.session_uid -ne $sessionUid -or $line.browser_pid -ne $browserPid) { throw "op $n ($($op.cap)) ran on a different browser session (uid $($line.session_uid), pid $($line.browser_pid))" }
                if ($line.tab_count -gt [int]$lc.max_tabs) { throw "op $n exceeded the tab budget: $($line.tab_count) > $($lc.max_tabs)" }
            }
            if (-not $SkipLocalEvidence) {
                if ($line.processes -ne 1) { throw "browser_lifecycle_violation after op ${n}: $($line.processes) PagentOS-profile Chrome processes" }
                if ($line.windows -gt 1) { throw "browser_lifecycle_violation after op ${n}: $($line.windows) Chrome windows on the PagentOS profile" }
            }
        }
        $closed = Invoke-DeviceCommand -Capability "browser.session_close" -Payload @{ session_id = $sessionId }
        Start-Sleep -Seconds 2
        $left = Get-ProfileChromes
        $evidence.lifecycle.end_processes = @($left).Count
        $evidence.lifecycle.browser_pid_exited = (Get-OptionalProperty -InputObject $closed.result -Name "browser_pid_exited")
        Write-Host "      closed: browser_pid_exited=$($evidence.lifecycle.browser_pid_exited); PagentOS-profile Chrome processes left: $(@($left).Count)"
        if (-not $SkipLocalEvidence -and @($left).Count -ne 0) { throw "session_close left $(@($left).Count) PagentOS-profile Chrome process(es) running" }
        $ownerWindowsAfter = Get-OwnerChromeWindowCount
        $evidence.lifecycle.owner_chrome_windows_after = $ownerWindowsAfter
        Write-Host "      owner's own Chrome windows: before=$ownerWindowsBefore after=$ownerWindowsAfter"
        if (-not $SkipLocalEvidence -and $ownerWindowsAfter -gt $ownerWindowsBefore) { throw "the owner's own Chrome gained $($ownerWindowsAfter - $ownerWindowsBefore) window(s) during the test - a PagentOS component touched the owner's browser" }
        if (-not $SkipLocalEvidence) {
            # Durable ownership + fault files written by the worker under the companion's browser data dir.
            $browserData = Join-Path $env:ProgramData "PagentOS\companion\browser"
            $ownershipPath = Join-Path $browserData "browser-ownership.json"
            $faultPath = Join-Path $browserData "browser-lifecycle-fault.json"
            if (-not (Test-Path -LiteralPath $ownershipPath)) { throw "no browser-ownership.json under $browserData - the installed worker predates the ownership record" }
            $ownership = Get-Content -LiteralPath $ownershipPath -Raw | ConvertFrom-Json
            $evidence.lifecycle.ownership = $ownership
            Write-Host "      ownership: job=$($ownership.research_job_id) chrome_root_pid=$($ownership.chrome_root_pid) started=$($ownership.chrome_start_time) closed_at=$($ownership.closed_at) browser_pid_exited=$($ownership.browser_pid_exited)"
            if ([int]$ownership.chrome_root_pid -ne $browserPid) { throw "ownership record names chrome_root_pid=$($ownership.chrome_root_pid), the session reported $browserPid" }
            if ($null -eq $ownership.closed_at -or $ownership.browser_pid_exited -ne $true) { throw "ownership record was not closed cleanly (closed_at=$($ownership.closed_at) browser_pid_exited=$($ownership.browser_pid_exited))" }
            if (Test-Path -LiteralPath $faultPath) { $evidence.lifecycle.fault = (Get-Content -LiteralPath $faultPath -Raw | ConvertFrom-Json); throw "a durable browser lifecycle fault is recorded at $faultPath - the launch-rate breaker tripped; paste the evidence, do not rerun" }
        }
        $evidence.verdict = "PASS"
        throw [System.Management.Automation.RuntimeException]::new("__done__")
    }

    if ($Mode -eq "search") {
        # Search-provider qualification: one browser.search through the provider abstraction.
        # PASS requires the recorded provider to be the requested primary with no fallback;
        # a fallback is reported honestly with its reason and FAILS this mode.
        $interstitial = if ($Handoff) { "handoff" } else { "fallback" }
        $search = Invoke-DeviceCommand -Capability "browser.search" -Payload @{ session_id = $sessionId; query = $SearchQuery; engine = "auto"; max_results = 8; interstitial = $interstitial }
        $sr = $search.result
        $handoffRounds = 0
        while ([string](Get-OptionalProperty -InputObject $sr -Name "state") -eq "waiting_for_owner_verification" -and $Handoff) {
            $handoffRounds++
            $vurl = [string](Get-OptionalProperty -InputObject $sr -Name "verification_url")
            Write-Host ""
            Write-Host "      WAITING_FOR_OWNER_VERIFICATION: Google shows a $((Get-OptionalProperty -InputObject $sr -Name 'page_kind')) page. Chrome was brought to the front." -ForegroundColor Yellow
            Write-Host "      Complete the page by hand in that Chrome window (nothing is solved or bypassed here); this script resumes the SAME session automatically." -ForegroundColor Yellow
            if ($vurl) { Write-Host "      page: $vurl" }
            $evidence.handoff = [ordered]@{ rounds = $handoffRounds; page_kind = (Get-OptionalProperty -InputObject $sr -Name "page_kind"); verification_url = $vurl; cleared = $false }
            $deadline = (Get-Date).AddSeconds($HandoffTimeoutSec)
            $cleared = $false
            while ((Get-Date) -lt $deadline -and -not $cleared) {
                $wait = Invoke-DeviceCommand -Capability "browser.wait" -Payload @{ session_id = $sessionId; for = "verification_cleared"; timeout_ms = 45000 }
                $cleared = [bool](Get-OptionalProperty -InputObject $wait.result -Name "satisfied")
            }
            if (-not $cleared) { throw "the verification page was not completed within $HandoffTimeoutSec s; the research session was left as it is" }
            $evidence.handoff.cleared = $true
            Write-Host "      verification cleared; re-issuing the same search on the same session"
            $search = Invoke-DeviceCommand -Capability "browser.search" -Payload @{ session_id = $sessionId; query = $SearchQuery; engine = "auto"; max_results = 8; interstitial = $interstitial }
            $sr = $search.result
            if ($handoffRounds -ge 3) { throw "Google kept asking for verification three times; stopping honestly" }
        }
        $resultSchema = Get-OptionalProperty -InputObject $sr -Name "schema_version"
        if ($null -eq $resultSchema -or [int]$resultSchema -lt $RequiredSearchSchema) {
            throw "contract/version mismatch: browser.search answered with schema '$resultSchema' (needs $RequiredSearchSchema); the worker that executed it predates the provider abstraction - rerun with -UpdateAgentFirst"
        }
        foreach ($field in @("requested_provider", "provider", "fallback", "query", "result_count", "attempts", "locale")) {
            if (-not (Test-ObjectProperty -InputObject $sr -Name $field)) { throw "provider evidence field '$field' missing from the schema-$resultSchema search result" }
        }
        $results = @(Get-OptionalProperty -InputObject $sr -Name "results")
        $attempts = @(Get-OptionalProperty -InputObject $sr -Name "attempts")
        Write-Host ("      state={0} path={1}" -f (Get-OptionalProperty -InputObject $sr -Name "state"), (Get-OptionalProperty -InputObject $sr -Name "path"))
        Write-Host ("      requested_provider={0} provider={1} fallback={2} fallback_reason={3} query='{4}' result_count={5} locale={6}" -f
            (Get-OptionalProperty -InputObject $sr -Name "requested_provider"), (Get-OptionalProperty -InputObject $sr -Name "provider"),
            (Get-OptionalProperty -InputObject $sr -Name "fallback"), (Get-OptionalProperty -InputObject $sr -Name "fallback_reason"),
            (Get-OptionalProperty -InputObject $sr -Name "query"), (Get-OptionalProperty -InputObject $sr -Name "result_count"),
            (Get-OptionalProperty -InputObject $sr -Name "locale"))
        foreach ($a in $attempts) { Write-Host ("      attempt: {0} -> {1} {2}" -f $a.provider, $a.outcome, $a.detail) }
        $shown = 0
        foreach ($r in $results) { if ($shown -lt 5) { Write-Host ("      #{0} {1} | {2}" -f $r.rank, $r.title, $r.url); $shown++ } }
        $evidence.search = [ordered]@{
            requested_provider = (Get-OptionalProperty -InputObject $sr -Name "requested_provider"); provider = (Get-OptionalProperty -InputObject $sr -Name "provider")
            fallback = (Get-OptionalProperty -InputObject $sr -Name "fallback"); fallback_reason = (Get-OptionalProperty -InputObject $sr -Name "fallback_reason")
            query = (Get-OptionalProperty -InputObject $sr -Name "query"); result_count = (Get-OptionalProperty -InputObject $sr -Name "result_count")
            locale = (Get-OptionalProperty -InputObject $sr -Name "locale"); attempts = $attempts
            results = @($results | ForEach-Object { [ordered]@{ rank = $_.rank; title = $_.title; url = $_.url } })
        }
        if ($ExpectProvider -ne "any") {
            if ([string](Get-OptionalProperty -InputObject $sr -Name "requested_provider") -ne $ExpectProvider) { throw "requested provider is not $ExpectProvider" }
            if ([string](Get-OptionalProperty -InputObject $sr -Name "provider") -ne $ExpectProvider) {
                throw "the search fell back to $((Get-OptionalProperty -InputObject $sr -Name 'provider')) (reason: $((Get-OptionalProperty -InputObject $sr -Name 'fallback_reason'))); $ExpectProvider did not produce results"
            }
        }
        if (@($results).Count -lt 1) { throw "$ExpectProvider produced no organic results" }
        foreach ($r in $results) { if (-not $r.title -or -not $r.url -or -not ($r.url -like "http*")) { throw "malformed result #$($r.rank)" } }
        if (-not $SkipLocalEvidence) {
            $companionProc = Get-CimInstance Win32_Process -Filter "Name='PagentOS.SessionCompanion.exe'" -ErrorAction SilentlyContinue | Select-Object -First 1
            $workers = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.ExecutablePath -like "*\PagentOS\agent\browser\*" })
            $evidence.local = [ordered]@{
                companion_image = $(if ($companionProc) { $companionProc.ExecutablePath } else { "not running" })
                worker_image = $(if (@($workers).Count -gt 0) { $workers[0].ExecutablePath } else { "not found" })
                worker_parent_is_companion = $(if (@($workers).Count -gt 0 -and $companionProc) { $workers[0].ParentProcessId -eq $companionProc.ProcessId } else { $false })
            }
            Write-Host "      local: companion=$($evidence.local.companion_image) worker=$($evidence.local.worker_image) (parent is companion: $($evidence.local.worker_parent_is_companion))"
            if ($evidence.local.worker_image -notlike "C:\Program Files\PagentOS\agent\browser\*") { throw "the worker is not the installed one: $($evidence.local.worker_image)" }
            if (-not $evidence.local.worker_parent_is_companion) { throw "the worker is not a child of the installed companion" }
        }
        [void](Invoke-DeviceCommand -Capability "browser.session_close" -Payload @{ session_id = $sessionId })
        $evidence.verdict = "PASS"
        throw [System.Management.Automation.RuntimeException]::new("__done__")
    }

    $nav = Invoke-DeviceCommand -Capability "browser.navigate" -Payload @{ session_id = $sessionId; url = "https://example.com/"; timeout_ms = 30000 }
    Write-Host "      navigated: url=$($nav.result.url) title='$($nav.result.title)' http_status=$($nav.result.http_status) page_kind=$($nav.result.page_kind)"
    if ([string]$nav.result.title -ne "Example Domain") { throw "unexpected title for example.com: '$($nav.result.title)'" }
    if ([string]$nav.result.page_kind -ne "ok") { throw "example.com classified as $($nav.result.page_kind)" }

    $inspect = Invoke-DeviceCommand -Capability "browser.inspect" -Payload @{ session_id = $sessionId }
    $extract = Invoke-DeviceCommand -Capability "browser.extract" -Payload @{ session_id = $sessionId; mode = "text"; max_chars = 400 }
    $text = [string](Get-OptionalProperty -InputObject $extract.result -Name "text")
    Write-Host "      text ($($text.Length) chars): $($text.Substring(0, [Math]::Min(120, $text.Length)).Replace("`n", ' '))"
    # The page's wording changes over time (it did on 2026-09-03); its heading does not.
    if ($text -notmatch "Example Domain" -or $text.Length -lt 50) { throw "example.com text did not contain its heading / was too short" }

    # Local evidence while the session (and therefore the worker and Chrome) is alive.
    if (-not $SkipLocalEvidence) {
        $me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $companionProc = Get-CimInstance Win32_Process -Filter "Name='PagentOS.SessionCompanion.exe'" -ErrorAction SilentlyContinue | Select-Object -First 1
        $workers = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.ExecutablePath -like "*\PagentOS\agent\browser\*" })
        $chromes = @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*PagentOS*companion*browser*" })
        $workerOwner = $null
        if (@($workers).Count -gt 0) {
            $o = Invoke-CimMethod -InputObject $workers[0] -MethodName GetOwner -ErrorAction SilentlyContinue
            if ($o) { $workerOwner = "$($o.Domain)\$($o.User)" }
        }
        $evidence.local = [ordered]@{
            user = $me
            companion_image = $(if ($companionProc) { $companionProc.ExecutablePath } else { "not running" })
            companion_pid = $(if ($companionProc) { $companionProc.ProcessId } else { $null })
            worker_image = $(if (@($workers).Count -gt 0) { $workers[0].ExecutablePath } else { "not found" })
            worker_pid = $(if (@($workers).Count -gt 0) { $workers[0].ProcessId } else { $null })
            worker_parent_is_companion = $(if (@($workers).Count -gt 0 -and $companionProc) { $workers[0].ParentProcessId -eq $companionProc.ProcessId } else { $false })
            worker_owner = $workerOwner
            chrome_with_pagentos_profile = @($chromes).Count
        }
        Write-Host "      local: companion=$($evidence.local.companion_image) worker=$($evidence.local.worker_image) (pid $($evidence.local.worker_pid), parent is companion: $($evidence.local.worker_parent_is_companion), owner $workerOwner) chrome(profile)=$(@($chromes).Count)"
        if ($evidence.local.worker_image -notlike "C:\Program Files\PagentOS\agent\browser\*") { throw "the worker is not the installed one: $($evidence.local.worker_image)" }
        if (-not $evidence.local.worker_parent_is_companion) { throw "the worker is not a child of the installed companion" }
        if (@($chromes).Count -lt 1) { throw "no Chrome process is using the PagentOS profile" }
    }

    $search = Invoke-DeviceCommand -Capability "browser.search" -Payload @{ session_id = $sessionId; query = $SearchQuery; engine = "auto"; max_results = 5 }
    $results = @(Get-OptionalProperty -InputObject $search.result -Name "results")
    Write-Host "      search engine=$($search.result.engine) results=$(@($results).Count) first=$(if (@($results).Count -gt 0) { $results[0].url } else { '-' })"
    if (@($results).Count -lt 1) { throw "the search returned no results" }

    # Risk policy, proven on the real worker: a READ+NAVIGATE session must refuse a download.
    [void](Invoke-DeviceCommand -Capability "browser.download" -Payload @{ session_id = $sessionId; target = @{ text = "More information..." } } -ExpectFailure -ExpectedErrorClass "security_scope_error")
    Write-Host "      download refused with security_scope_error (risk policy enforced in the installed worker)"

    [void](Invoke-DeviceCommand -Capability "browser.session_close" -Payload @{ session_id = $sessionId })

    # ---------------------------------------------------------------- audit correlation
    if (-not $SkipLocalEvidence) {
        $auditPath = Join-Path $env:ProgramData "PagentOS\companion\audit\companion-audit.jsonl"
        $rows = @()
        if (Test-Path -LiteralPath $auditPath) {
            foreach ($line in [System.IO.File]::ReadAllLines($auditPath)) {
                if ($line -notmatch "browser_request") { continue }
                try { $obj = $line | ConvertFrom-Json } catch { continue }
                $at = Get-OptionalProperty -InputObject $obj -Name "ts"
                if ($at) { try { if ([DateTime]::Parse($at).ToUniversalTime() -lt $startedAt.AddSeconds(-5)) { continue } } catch { } }
                $rows += $obj
            }
        }
        $evidence.local.companion_audit_rows = @($rows).Count
        $evidence.local.companion_audit_capabilities = @($rows | ForEach-Object { Get-OptionalProperty -InputObject $_ -Name "capability" })
        Write-Host "      companion audit: $(@($rows).Count) browser_request rows since start ($auditPath)"
        if (@($rows).Count -lt $evidence.commands.Count) { throw "companion audit has $(@($rows).Count) browser_request rows for $($evidence.commands.Count) commands" }
    }

    $evidence.verdict = "PASS"
}
catch {
    if ($_.Exception.Message -ne "__done__") {
        $evidence.verdict = "FAIL: $($_.Exception.Message)"
        Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
    }
}
finally {
    $evidence.finished_at = (Get-Date).ToUniversalTime().ToString("o")
    if ($mintedId) {
        try { Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions/$mintedId/revoke" -Headers $headers -Body "{}" -TimeoutSec 15 | Out-Null; Write-Host "the session minted for this run was revoked" }
        catch { Write-Host "note: could not revoke the run's session ($($_.Exception.Message)); it expires on its own" }
    }
    $token = $null
    $headers = $null
}

$json = ConvertTo-Json -InputObject $evidence -Depth 10
if ($OutFile) {
    [IO.File]::WriteAllText($OutFile, $json + "`n", (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "evidence written to $OutFile (ids, timings, example.com title/text, process images; no secret)"
}
Write-Host ""
Write-Host "REAL BROWSER SMOKE: $($evidence.verdict)" -ForegroundColor $(if ($evidence.verdict -eq "PASS") { "Green" } else { "Red" })
if ($evidence.verdict -ne "PASS") { exit 1 }
exit 0
