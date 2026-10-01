<#
.SYNOPSIS
    The scheduled task's one action: feed the queue from the roadmap when it runs low, then
    run the cycle.

.DESCRIPTION
    Owner rule 2026-10-01 (ADR-0214 addendum 8): no agent idles while the roadmap names work.
    `feed.ps1` cuts the roadmap's next items into cards when fewer tasks are runnable than
    there are worker seats; `cycle.ps1` then runs what is runnable. The feeder never decides
    whether the cycle runs: whatever it answers (nothing to do, the lock is held, a refused
    feed file, an error) the cycle is started, and the feeder's exit code is only printed.

    It never releases, never merges to main and never pushes: it starts the two scripts.

.EXAMPLE
    .\scripts\team\tick.ps1 -MaxParallel 6 -DailyId -Research -ResearchEveryHours 6
#>
[CmdletBinding()]
param(
    [int]$MaxParallel = 2,
    [double]$MaxUsd = 0,
    [int]$CycleMinutes = 0,
    [switch]$Research,
    [switch]$DailyId,
    [double]$ResearchEveryHours = 0,
    [string]$Base = "",
    [string]$QueueUrl = "",
    [string]$QueueToken = "",
    # The cycle alone, as before the feeder existed.
    [switch]$NoFeed,
    # The tests put fakes in place of the two scripts.
    [string]$FeedPath = "",
    [string]$CyclePath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$feedScript = if ($FeedPath) { $FeedPath } else { Join-Path $PSScriptRoot "feed.ps1" }
$cycleScript = if ($CyclePath) { $CyclePath } else { Join-Path $PSScriptRoot "cycle.ps1" }
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

function Invoke-Script {
    # A child process, so a script that calls `exit` or throws ends itself and not the tick.
    param([string]$Path, [string[]]$Arguments)
    $all = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$Path`"") + $Arguments
    $process = Start-Process -FilePath $powershell -ArgumentList $all -NoNewWindow -Wait -PassThru
    return [int]$process.ExitCode
}

$store = @()
if ($QueueUrl) {
    if (-not $QueueToken) { throw "-QueueUrl needs -QueueToken (the path of the token file)" }
    $store = @("-QueueUrl", $QueueUrl, "-QueueToken", "`"$QueueToken`"")
}

if (-not $NoFeed) {
    $feedExit = -1
    try { $feedExit = Invoke-Script -Path $feedScript -Arguments $store }
    catch { Write-Host "tick: the feeder could not be started: $($_.Exception.Message)" }
    Write-Host "tick: the feeder ended with exit $feedExit; the cycle runs whatever it said"
}

$cycleArguments = @(
    "-MaxUsd", $MaxUsd.ToString("0.##", [System.Globalization.CultureInfo]::InvariantCulture),
    "-MaxParallel", "$MaxParallel", "-CycleMinutes", "$CycleMinutes"
)
if ($Research) { $cycleArguments += "-Research" }
if ($DailyId) { $cycleArguments += "-DailyId" }
if ($ResearchEveryHours -gt 0) {
    $cycleArguments += @("-ResearchEveryHours", $ResearchEveryHours.ToString("0.##", [System.Globalization.CultureInfo]::InvariantCulture))
}
if ($Base) {
    if ($Base -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._/-]{0,80}$') { throw "-Base is a branch name" }
    $cycleArguments += @("-Base", $Base)
}
$cycleArguments += $store

$cycleExit = Invoke-Script -Path $cycleScript -Arguments $cycleArguments
Write-Host "tick: the cycle ended with exit $cycleExit"
exit $cycleExit
