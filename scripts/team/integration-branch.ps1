<#
.SYNOPSIS
    The cycle's integration branch, and the merge of approved task branches into it.

.DESCRIPTION
    `integrate/<cycle-id>` lives in a worktree of its own. With -Merge, each named branch is
    merged --no-ff; one that is already merged is reported as merged; a conflict is aborted,
    the integration branch is left as it was, and the script exits 5 naming the branch.
    It never merges to main and never pushes.

.EXAMPLE
    .\scripts\team\integration-branch.ps1 -CycleId pilot-01 -Merge team/pilot-01/worker-a,team/pilot-01/worker-b
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$CycleId,
    [string[]]$Merge = @(),
    [string]$Base = "main"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")

$tree = New-TeamWorktree -RepoRoot $repoRoot -Branch "integrate/$CycleId" -Base $Base
Write-Host "integration branch: $($tree.Branch) at $($tree.Path)"
$refused = 0
foreach ($branch in @($Merge)) {
    if ($branch -match "hand-gestures" -or $branch -notmatch "^team/") {
        Write-Host "  REFUSED         $branch is not a team branch"
        $refused++
        continue
    }
    $outcome = Merge-TeamBranch -RepoRoot $repoRoot -CycleId $CycleId -Branch $branch -Base $Base
    if ($outcome.Already) { Write-Host "  already merged  $branch" }
    elseif ($outcome.Merged) { Write-Host "  merged          $branch" }
    else {
        Write-Host "  CONFLICT        $branch"
        $refused++
    }
}
if ($refused -gt 0) { exit 5 }
exit 0
