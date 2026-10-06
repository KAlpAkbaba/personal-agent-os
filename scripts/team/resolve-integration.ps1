<#
.SYNOPSIS
    The Proje Yöneticisi's `resolve_integration` duty action in a process of its own
    (pm-resolves-integration-conflicts; the owner, 2026-10-06: "çalışan 2'nin direk sana değil
    proje yöneticisine gitmeli").

.DESCRIPTION
    cycle.ps1 runs this after a duty decision file named `resolve_integration` for one or more
    stopped tasks: scripts/lib/TeamDuty.ps1's Invoke-TeamDutyResolveIntegration merges each task
    branch into integrate/<cycle>, keeps both sides of an additive conflict, re-points a new
    migration after the chain's head, runs the guards and the task's tests, and sets the task
    merged - or escalates it to the Danışman with the reason. A process of its own so the
    library's script-scope state never mixes with the cycle's. Writes one JSON line:
    { results: [ { Task, Outcome, Reason, Sha } ] }; exit 0 when it ran, 1 when it could not.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$CycleId,
    [Parameter(Mandatory = $true)][string]$QueueUrl,
    [Parameter(Mandatory = $true)][string]$QueueToken,
    [string]$Base = "main",
    [string]$Python = "",
    [string]$TaskIds = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\TeamDuty.ps1")

try {
    $store = New-TeamApiStore -Url $QueueUrl -TokenFile $QueueToken
    $ids = @($TaskIds -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $results = @(Invoke-TeamDutyResolveIntegration -RepoRoot $repoRoot -CycleId $CycleId -Store $store -Base $Base -Python $Python -TaskIds $ids)
    Write-Output (ConvertTo-Json -InputObject ([ordered]@{ results = $results }) -Depth 5 -Compress)
    exit 0
}
catch {
    Write-Output (ConvertTo-Json -InputObject ([ordered]@{ error = (([string]$_.Exception.Message) -replace '\s+', ' ') }) -Compress)
    exit 1
}
