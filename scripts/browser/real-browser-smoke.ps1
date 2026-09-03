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

$evidence = [ordered]@{
    run_id       = $runId
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
    $listing = Get-Json "/v1/devices"
    $devices = @(Get-OptionalProperty -InputObject $listing -Name "devices")
    $chosen = $null
    foreach ($d in $devices) {
        $caps = @(Get-OptionalProperty -InputObject $d -Name "capabilities")
        $status = [string](Get-OptionalProperty -InputObject $d -Name "status")
        if ($Device) {
            if ([string]$d.device_id -eq $Device -or [string]$d.name -eq $Device) { $chosen = $d; break }
        }
        elseif ($status -eq "online" -and ($caps -contains "browser.chrome")) { $chosen = $d; break }
    }
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
    Write-Host "      worker $($status.result.worker_version), browser channel=$($browser.channel) version=$($browser.version) available=$($browser.available)"

    $opened = Invoke-DeviceCommand -Capability "browser.session_open" -Payload @{
        session_id = $sessionId; profile = "research"
        policy = @{ allowed_risk_classes = @("READ", "NAVIGATE"); visible = $true }; channel = "chrome"
    }
    Write-Host "      session created=$($opened.result.created) channel=$($opened.result.channel) browser=$($opened.result.browser_version)"

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
    $evidence.verdict = "FAIL: $($_.Exception.Message)"
    Write-Host "FAILED: $($_.Exception.Message)" -ForegroundColor Red
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
