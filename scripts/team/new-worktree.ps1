<#
.SYNOPSIS
    A team branch and its worktree under .claude\worktrees (TEAM_PROTOCOL.md section 4).

.DESCRIPTION
    Idempotent: a branch or a worktree that exists is used as it is. The main checkout is
    never checked out, reset or cleaned.

.EXAMPLE
    .\scripts\team\new-worktree.ps1 -CycleId pilot-01 -Role worker -Slug onay-merkezi
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$CycleId,
    [Parameter(Mandatory = $true)][ValidateSet("worker", "integrator", "inspector")][string]$Role,
    [Parameter(Mandatory = $true)][string]$Slug,
    [string]$Base = "main"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")

$branch = Get-TeamBranchName -CycleId $CycleId -Role $Role -Slug $Slug
$tree = New-TeamWorktree -RepoRoot $repoRoot -Branch $branch -Base $Base
if ($tree.Created) { Write-Host "created: $($tree.Branch) at $($tree.Path)" }
else { Write-Host "$($tree.Note): $($tree.Branch) at $($tree.Path)" }
exit 0
