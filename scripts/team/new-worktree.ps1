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
    [string]$Base = "main",
    # Every git call's bound (card cycle-watchdog, 2026-10-06: a `worktree add` hung with an
    # inner `reset --hard` at 0 CPU, twice). Past it git AND its children are killed and the
    # half-made worktree and branch are removed before the script stops with the reason.
    [int]$GitTimeoutSeconds = 300,
    # A test hook: the repository the worktree is made in (default: this checkout).
    [string]$RepoRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptsRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $scriptsRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $scriptsRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $scriptsRoot "scripts\lib\TeamRun.ps1")
. (Join-Path $scriptsRoot "scripts\lib\TeamTickKeep.ps1")
. (Join-Path $scriptsRoot "scripts\lib\TeamWatchdog.ps1")
$targetRoot = if ($RepoRoot) { (Resolve-Path -LiteralPath $RepoRoot).Path } else { $scriptsRoot }

$branch = Get-TeamBranchName -CycleId $CycleId -Role $Role -Slug $Slug
$tree = New-TeamWorktreeGuarded -RepoRoot $targetRoot -Branch $branch -Base $Base -Git (Get-TeamGit) -TimeoutSeconds $GitTimeoutSeconds
if ($tree.Created) { Write-Host "created: $($tree.Branch) at $($tree.Path)" }
else { Write-Host "$($tree.Note): $($tree.Branch) at $($tree.Path)" }
exit 0
