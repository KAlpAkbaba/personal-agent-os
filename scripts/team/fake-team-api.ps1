<#
.SYNOPSIS
    A stand-in for the Cloud Core's /v1/team/queue in scripts/tests/team-cycle.tests.ps1.

.DESCRIPTION
    An in-memory queue and lock behind a System.Net.HttpListener on 127.0.0.1, with the rules
    the real routes have (services/api/app/team/routes.py): a Bearer token, PUT of a task with
    an expected_updated_at (409 when stale), POST of the lock (six-hour staleness), POST of a
    report. Every request is one line in -Log ("METHOD path status"); GET /__state prints the
    queue, the lock and the reports; GET /__stop ends it. -Ready is written once it listens.
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
    [switch]$FailStatus
)
$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)

$state = ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText($Seed, $utf8))
$tasks = [ordered]@{}
foreach ($task in @($state.queue.tasks)) { $tasks[[string]$task.id] = $task }
$lock = $state.lock
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
    $reader = New-Object System.IO.StreamReader($request.InputStream, $utf8)
    $raw = $reader.ReadToEnd()
    $body = $null
    if ($raw) { $body = ConvertFrom-Json -InputObject $raw }
    $status = 500
    try {
        if ($path -eq "/__stop") { $running = $false; $status = Send-Json -Context $context -Status 200 -Body @{ stopped = $true } }
        elseif ($path -eq "/__state") {
            $status = Send-Json -Context $context -Status 200 -Body @{ tasks = @($tasks.Values); lock = $lock; reports = $reports; status = $liveStatus; statuses = @($statusHistory) }
        }
        elseif ($request.Headers["Authorization"] -ne "Bearer $Token") {
            $status = Send-Json -Context $context -Status 401 -Body @{ detail = "unauthorized" }
        }
        elseif ($path -eq "/v1/team/queue" -and $request.HttpMethod -eq "GET") {
            $status = Send-Json -Context $context -Status 200 -Body @{ version = 1; tasks = @($tasks.Values) }
        }
        elseif ($path -match '^/v1/team/queue/tasks/([a-z0-9-]+)$' -and $request.HttpMethod -eq "PUT") {
            $id = $Matches[1]
            $stored = $tasks[$id]
            $expected = $body.expected_updated_at
            if ($null -eq $stored -and $null -ne $expected) { $status = Send-Json -Context $context -Status 409 -Body @{ detail = @{ code = "stale_write" } } }
            elseif ($null -ne $stored -and [string]$stored.updated_at -ne [string]$expected) { $status = Send-Json -Context $context -Status 409 -Body @{ detail = @{ code = "stale_write" } } }
            else { $tasks[$id] = $body.task; $status = Send-Json -Context $context -Status 200 -Body $body.task }
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
            else {
                $liveStatus = $body
                [void]$statusHistory.Add($body)
                $status = Send-Json -Context $context -Status 200 -Body $body
            }
        }
        elseif ($path -eq "/v1/team/queue/status" -and $request.HttpMethod -eq "GET") {
            $status = Send-Json -Context $context -Status 200 -Body $(if ($null -ne $liveStatus) { $liveStatus } else { @{} })
        }
        elseif ($path -eq "/v1/team/queue/reports" -and $request.HttpMethod -eq "POST") {
            $reports[[string]$body.name] = [string]$body.text
            $status = Send-Json -Context $context -Status 200 -Body @{ stored = $body.name }
        }
        else { $status = Send-Json -Context $context -Status 404 -Body @{ detail = "no such route" } }
    }
    catch { try { $status = Send-Json -Context $context -Status 500 -Body @{ detail = $_.Exception.Message } } catch { } }
    [System.IO.File]::AppendAllText($Log, "$($request.HttpMethod) $path $status`n", $utf8)
}
$listener.Stop()
