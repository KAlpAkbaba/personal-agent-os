<#
.SYNOPSIS
    Remove a team worktree that is clean. The branch is kept.

.DESCRIPTION
    A worktree with uncommitted work in it is somebody's work: it is left as it is and the
    script says so (exit 4). Nothing is forced.

.EXAMPLE
    .\scripts\team\close-worktree.ps1 -Branch team/pilot-01/worker-onay-merkezi
#>
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Branch)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")

$result = Remove-TeamWorktree -RepoRoot $repoRoot -Branch $Branch
if ($result.Removed) {
    Write-Host "removed the worktree of $Branch; the branch is kept"
    exit 0
}
Write-Host "$($result.Note): $Branch"
if ($result.Note -match "not clean") { exit 4 }
exit 0
