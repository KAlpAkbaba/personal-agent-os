<#
.SYNOPSIS
    Write (again) the cycle report from the queue as it is on disk.

.DESCRIPTION
    `cycle.ps1` writes `team/reports/<cycle-id>.md` when it ends. This script writes it from
    what is on disk - for a cycle that was killed before it could, or after the lead has
    changed the queue (a merge to main, a state the owner set). It starts no run.

.EXAMPLE
    .\scripts\team\collect-reports.ps1 -CycleId pilot-01 -Risk "PR-C bekliyor"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$CycleId,
    [string]$TeamRoot = "",
    [string[]]$Risk = @(),
    [string[]]$Gap = @(),
    [string[]]$Stop = @(),
    [string]$Machine = $env:COMPUTERNAME
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")

if (-not $TeamRoot) { $TeamRoot = Join-Path $repoRoot "team" }
$queue = Read-TeamJson -Path (Join-Path $TeamRoot "queue.json")
$problems = @(Test-TeamQueue -Queue $queue)
if (@($problems).Count -gt 0) {
    foreach ($problem in $problems) { Write-Host "  - $problem" }
    exit 2
}

$runs = New-Object System.Collections.ArrayList
$spent = 0.0
foreach ($task in (Get-TeamTasks -Queue $queue)) {
    foreach ($report in @(Get-TeamProperty -InputObject $task -Name "reports" -Default @())) {
        if ([string](Get-TeamProperty -InputObject $report -Name "cycle" -Default "") -ne $CycleId) { continue }
        $cost = [double](Get-TeamProperty -InputObject $report -Name "cost_usd" -Default 0)
        $spent += $cost
        [void]$runs.Add([pscustomobject]@{
                task     = [string]$task.id
                role     = [string](Get-TeamProperty -InputObject $report -Name "role" -Default "")
                cost_usd = $cost
                seconds  = [int](Get-TeamProperty -InputObject $report -Name "seconds" -Default 0)
                outcome  = [string](Get-TeamProperty -InputObject $report -Name "outcome" -Default "")
            })
    }
}
$cycle = [pscustomobject]@{
    cycle_id   = $CycleId
    machine    = $Machine
    started_at = ""
    ended_at   = (Get-TeamTimestamp)
    max_usd    = 0.0
    spent_usd  = $spent
    conflicts  = 0
    returned   = 0
    runs       = @($runs.ToArray())
    stops      = @($Stop)
    risks      = @($Risk)
    gaps       = @($Gap)
}
$reports = Join-Path $TeamRoot "reports"
if (-not (Test-Path -LiteralPath $reports)) { [void](New-Item -ItemType Directory -Force -Path $reports) }
$path = Join-Path $reports "$CycleId.md"
$text = New-TeamCycleReport -CycleId $CycleId -Queue $queue -Cycle $cycle
[System.IO.File]::WriteAllText($path, $text + "`n", (New-Object System.Text.UTF8Encoding($false)))
Write-Host "report: $path"
exit 0
