<#
.SYNOPSIS
    A stand-in for the Cloud Core's /v1/team/queue in scripts/tests/team-cycle.tests.ps1.

.DESCRIPTION
    An in-memory queue and lock behind a System.Net.HttpListener on 127.0.0.1, with the rules
    the real routes have (services/api/app/team/routes.py): a Bearer token, PUT of a task with
    an expected_updated_at (409 when stale), POST of the lock (six-hour staleness), POST of a
    report. Every request is one line in -Log ("METHOD path status"); GET /__state prints the
    queue, the lock and the reports; GET /__stop ends it. -Ready is written once it listens.
    A seed with `late` ({ after_task_puts, tasks }) plays ANOTHER WRITER of the store - the owner
    in the Onay Merkezi, the lead, the feeder: once that many task PUTs have been stored, the
    listener puts those tasks into its queue itself (new ones are added, known ones replaced).
    `late.on_status_run` ("<role>:<task>") does the same at another moment: when a live status
    naming that run in flight comes in - somebody decides WHILE that run works. With
    `late.after_queue_gets` N beside it, the writer then waits for N more GETs of the queue to be
    answered first (N = 1: the change lands right AFTER the cycle's own look at the store when
    that run ends). `late.remove` is a list of ids the writer takes OUT of the queue.
    A seed with `faults` breaks the store on purpose: `queue_get_after` N answers 503 to every
    GET of the queue after the N-th; `task_put` { id, status } answers that status to every PUT
    of that task - only when the written state is `state` and the written assignee is
    `assignee`, where those are given, and only the first `times` such PUTs, if that is given;
    `task_put_when_runs` { runs, status } answers that status to EVERY task PUT while the last
    live status names exactly that many runs in flight (an outage in the middle of a batch);
    `proposal_post` { status, times } answers that status to the first `times` POSTs of a
    proposal (every one when `times` is absent).
    POST /v1/team/queue/proposals keeps a proposal's text (shown by /__state).
    It is NOT the server: the Python suite holds the server to its rules, and a test in
    services/api/tests/unit/test_team_state.py holds the client's paths and fields to it.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][int]$Port,
    [Parameter(Mandatory = $true)][string]$Seed,
    [Parameter(Mandatory = $true)][string]$Log,
    [Parameter(Mandatory = $true)][string]$Ready,
    [string]$Token = "test-token",
    # Every PUT of the live status answers 500 (a failing status write must not stop a cycle).
    [switch]$FailStatus,
    # A Cloud Core from before the model policy: a status that carries `limits`, or a run that
    # carries `model`, is 422 (the real route forbids a key it does not know).
    [switch]$LegacyStatus,
    # A Cloud Core from before the model policy: /v1/team/queue/models is 404.
    [switch]$NoModels
)
$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)
# The rules of the model setting are the cycle's own (Read-TeamModelSetting): one list of models.
. (Join-Path (Split-Path -Parent $PSScriptRoot) "lib\TeamQueue.ps1")
# The library switches StrictMode on in the scope that dot-sources it; this listener reads
# optional fields of whatever body came in, as it did before.
Set-StrictMode -Off

$state = ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText($Seed, $utf8))
# The model setting (ADR-0214 addendum 7): what the seed holds, or nothing stored yet.
$models = $null
if ($null -ne $state.PSObject.Properties["models"]) { $models = $state.models }
$tasks = [ordered]@{}
foreach ($task in @($state.queue.tasks)) { $tasks[[string]$task.id] = $task }
$lock = $state.lock
$late = $null
if ($null -ne $state.PSObject.Properties["late"]) { $late = $state.late }
$taskPuts = 0
$lateDone = $false
$faults = $null
if ($null -ne $state.PSObject.Properties["faults"]) { $faults = $state.faults }
$queueGets = 0
$proposals = [ordered]@{}

$lateArmed = $false
$lateGetsLeft = 0
$proposalPosts = 0
$taskPutFaults = 0

function Add-LateTasks {
    # The second writer's moment has come: once.
    if ($script:lateDone -or $null -eq $script:late) { return }
    $script:lateDone = $true
    foreach ($other in @($script:late.tasks)) { if ($null -ne $other) { $script:tasks[[string]$other.id] = $other } }
    foreach ($gone in @($script:late.remove)) { if ($gone) { $script:tasks.Remove([string]$gone) } }
}
$reports = [ordered]@{}
$liveStatus = $null
$statusHistory = New-Object System.Collections.ArrayList

function Send-Json {
    param($Context, [int]$Status, $Body)
    $bytes = $utf8.GetBytes((ConvertTo-Json -InputObject $Body -Depth 12 -Compress))
    $Context.Response.StatusCode = $Status
    $Context.Response.ContentType = "application/json; charset=utf-8"
    $Context.Response.ContentLength64 = $bytes.Length
    $Context.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    $Context.Response.Close()
    return $Status
}

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://127.0.0.1:$Port/")
$listener.Start()
[System.IO.File]::WriteAllText($Ready, "ready", $utf8)

$running = $true
while ($running) {
    $context = $listener.GetContext()
    $request = $context.Request
    $path = $request.Url.AbsolutePath
    # Read now: once a client has dropped the connection on an error answer the request no
    # longer says what it was, and the log line came out without its method.
    $method = [string]$request.HttpMethod
    $reader = New-Object System.IO.StreamReader($request.InputStream, $utf8)
    $raw = $reader.ReadToEnd()
    $body = $null
    if ($raw) { $body = ConvertFrom-Json -InputObject $raw }
    $status = 500
    try {
        if ($path -eq "/__stop") { $running = $false; $status = Send-Json -Context $context -Status 200 -Body @{ stopped = $true } }
        elseif ($path -eq "/__state") {
            $status = Send-Json -Context $context -Status 200 -Body @{ tasks = @($tasks.Values); lock = $lock; reports = $reports; status = $liveStatus; statuses = @($statusHistory); models = $models; proposals = $proposals }
        }
        elseif ($request.Headers["Authorization"] -ne "Bearer $Token") {
            $status = Send-Json -Context $context -Status 401 -Body @{ detail = "unauthorized" }
        }
        elseif ($path -eq "/v1/team/queue" -and $request.HttpMethod -eq "GET") {
            $queueGets++
            if ($null -ne $faults -and $null -ne $faults.queue_get_after -and $queueGets -gt [int]$faults.queue_get_after) {
                $status = Send-Json -Context $context -Status 503 -Body @{ detail = "the store is away" }
            }
            else {
                $status = Send-Json -Context $context -Status 200 -Body @{ version = 1; tasks = @($tasks.Values) }
                # The writer that waits for the cycle's own look: it writes AFTER this answer.
                if ($lateArmed -and -not $lateDone) {
                    $lateGetsLeft--
                    if ($lateGetsLeft -le 0) { Add-LateTasks }
                }
            }
        }
        elseif ($path -eq "/v1/team/queue/proposals" -and $request.HttpMethod -eq "POST") {
            $proposalPosts++
            $broken = ($null -ne $faults -and $null -ne $faults.proposal_post -and ($null -eq $faults.proposal_post.times -or $proposalPosts -le [int]$faults.proposal_post.times))
            if ($broken) { $status = Send-Json -Context $context -Status ([int]$faults.proposal_post.status) -Body @{ detail = "the store did not keep the text" } }
            else {
                $proposals[[string]$body.name] = [string]$body.text
                $status = Send-Json -Context $context -Status 200 -Body @{ ok = $true }
            }
        }
        elseif ($path -match '^/v1/team/queue/tasks/([a-z0-9-]+)$' -and $request.HttpMethod -eq "PUT") {
            $id = $Matches[1]
            $stored = $tasks[$id]
            $expected = $body.expected_updated_at
            if ($null -ne $faults -and $null -ne $faults.task_put -and [string]$faults.task_put.id -eq $id -and ($null -eq $faults.task_put.state -or [string]$faults.task_put.state -eq [string]$body.task.state) -and ($null -eq $faults.task_put.assignee -or [string]$faults.task_put.assignee -eq [string]$body.task.assignee) -and ($null -eq $faults.task_put.times -or $taskPutFaults -lt [int]$faults.task_put.times)) {
                $taskPutFaults++
                $status = Send-Json -Context $context -Status ([int]$faults.task_put.status) -Body @{ detail = "the store refused the write" }
            }
            elseif ($null -ne $faults -and $null -ne $faults.task_put_when_runs -and $null -ne $liveStatus -and @($liveStatus.runs).Count -eq [int]$faults.task_put_when_runs.runs) {
                $status = Send-Json -Context $context -Status ([int]$faults.task_put_when_runs.status) -Body @{ detail = "the store is away" }
            }
            elseif ($null -eq $stored -and $null -ne $expected) { $status = Send-Json -Context $context -Status 409 -Body @{ detail = @{ code = "stale_write" } } }
            elseif ($null -ne $stored -and [string]$stored.updated_at -ne [string]$expected) { $status = Send-Json -Context $context -Status 409 -Body @{ detail = @{ code = "stale_write" } } }
            else {
                $tasks[$id] = $body.task
                $status = Send-Json -Context $context -Status 200 -Body $body.task
                $taskPuts++
                if ($null -ne $late -and $null -eq $late.on_status_run -and $taskPuts -eq [int]$late.after_task_puts) { Add-LateTasks }
            }
        }
        elseif ($path -eq "/v1/team/queue/lock" -and $request.HttpMethod -eq "GET") {
            $status = Send-Json -Context $context -Status 200 -Body $lock
        }
        elseif ($path -eq "/v1/team/queue/lock" -and $request.HttpMethod -eq "POST") {
            if ($body.action -eq "release") {
                if ($lock.held -eq $true -and [string]$lock.machine -ne [string]$body.machine) {
                    $status = Send-Json -Context $context -Status 409 -Body @{ detail = @{ code = "stale_write" } }
                }
                else { $lock = [pscustomobject]@{ held = $false }; $status = Send-Json -Context $context -Status 200 -Body @{ released = $true } }
            }
            else {
                $verdict = "free"
                if ($lock.held -eq $true) {
                    $at = [datetime]::Parse([string]$lock.acquired_at, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AssumeUniversal -bor [System.Globalization.DateTimeStyles]::AdjustToUniversal)
                    if (([datetime]::UtcNow - $at).TotalHours -ge 6) { $verdict = "stale" }
                    elseif ([string]$lock.machine -eq [string]$body.machine) { $verdict = $(if ($body.takeover_dead -eq $true) { "dead" } else { "ours" }) }
                    else { $verdict = "held" }
                }
                $ok = @("free", "stale", "dead") -contains $verdict
                $answer = @{ acquired = $ok; kind = $verdict; holder = [string]$lock.machine; since = [string]$lock.acquired_at; pid = $(if ($lock.held -eq $true) { $lock.pid } else { 0 }) }
                if ($ok) {
                    $lock = [pscustomobject]@{ held = $true; machine = $body.machine; cycle_id = $body.cycle_id; pid = $body.pid; acquired_at = [datetime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", [System.Globalization.CultureInfo]::InvariantCulture) }
                }
                $status = Send-Json -Context $context -Status 200 -Body $answer
            }
        }
        elseif ($path -eq "/v1/team/queue/status" -and $request.HttpMethod -eq "PUT") {
            # The cycle's live status (office-cycle-status): the latest is kept, and every document
            # that came in, so a test can see what was written while the cycle waited.
            if ($FailStatus) { $status = Send-Json -Context $context -Status 500 -Body @{ detail = "status store down" } }
            elseif ($LegacyStatus -and ($null -ne $body.PSObject.Properties["limits"] -or @($body.runs | Where-Object { $null -ne $_ -and $null -ne $_.PSObject.Properties["model"] }).Count -gt 0)) {
                $status = Send-Json -Context $context -Status 422 -Body @{ detail = "extra fields not permitted" }
            }
            else {
                $liveStatus = $body
                [void]$statusHistory.Add($body)
                $status = Send-Json -Context $context -Status 200 -Body $body
                if ($null -ne $late -and $null -ne $late.on_status_run) {
                    foreach ($live in @($body.runs)) {
                        if ($null -ne $live -and ("$($live.role):$($live.task)" -eq [string]$late.on_status_run)) {
                            if ($null -ne $late.after_queue_gets -and [int]$late.after_queue_gets -gt 0) {
                                if (-not $lateArmed) { $lateArmed = $true; $lateGetsLeft = [int]$late.after_queue_gets }
                            }
                            else { Add-LateTasks }
                        }
                    }
                }
            }
        }
        elseif ($path -eq "/v1/team/queue/status" -and $request.HttpMethod -eq "GET") {
            $status = Send-Json -Context $context -Status 200 -Body $(if ($null -ne $liveStatus) { $liveStatus } else { @{} })
        }
        elseif ($path -eq "/v1/team/queue/models" -and $NoModels) {
            $status = Send-Json -Context $context -Status 404 -Body @{ detail = "Not Found" }
        }
        elseif ($path -eq "/v1/team/queue/models" -and $request.HttpMethod -eq "GET") {
            # Nothing stored: the defaults. What is stored is handed out as it is - the cycle
            # validates what it reads, whoever wrote it.
            $status = Send-Json -Context $context -Status 200 -Body $(if ($null -ne $models) { $models } else { Get-TeamModelDefaults })
        }
        elseif ($path -eq "/v1/team/queue/models" -and $request.HttpMethod -eq "PUT") {
            $read = Read-TeamModelSetting -Document $body -Strict
            if (-not $read.Ok) { $status = Send-Json -Context $context -Status 422 -Body @{ detail = @{ code = $read.Code; problems = @($read.Problems) } } }
            else { $models = $read.Setting; $status = Send-Json -Context $context -Status 200 -Body $models }
        }
        elseif ($path -eq "/v1/team/queue/reports" -and $request.HttpMethod -eq "POST") {
            $reports[[string]$body.name] = [string]$body.text
            $status = Send-Json -Context $context -Status 200 -Body @{ stored = $body.name }
        }
        else { $status = Send-Json -Context $context -Status 404 -Body @{ detail = "no such route" } }
    }
    catch { try { $status = Send-Json -Context $context -Status 500 -Body @{ detail = $_.Exception.Message } } catch { } }
    [System.IO.File]::AppendAllText($Log, "$method $path $status`n", $utf8)
}
$listener.Stop()
