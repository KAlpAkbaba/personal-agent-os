<#
.SYNOPSIS
    What a team cycle DOES: git worktrees and branches, role runs, the cycle report.

.DESCRIPTION
    `TeamQueue.ps1` decides; this file acts. It is dot-sourced after `NativeProcess.ps1` and
    `TeamQueue.ps1`.

      * git is run through Invoke-NativeProcess with an explicit working directory; nothing
        here checks out, resets or cleans the MAIN checkout, and nothing names `main`
        as a branch to write to;
      * a role run is one fresh `claude -p` process: the role file is its system prompt, the
        task card is its prompt, the tools are the ones the role file grants, and it has a
        money cap and a time cap of its own. Its raw output goes to a file;
      * every step is idempotent: a branch, a worktree or a merge that is already there is
        reported as already there.

    Windows PowerShell 5.1, StrictMode.
#>

Set-StrictMode -Version Latest

# The repository this file is in (scripts\lib\..\..): the cycle report's default for its orphan check.
$script:TeamRunRepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Get-TeamGit {
    $command = Get-Command "git.exe" -ErrorAction SilentlyContinue
    if ($null -ne $command -and $command.Source) { return $command.Source }
    foreach ($candidate in @("$env:ProgramFiles\Git\cmd\git.exe", "$env:ProgramFiles\Git\bin\git.exe")) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    throw "git.exe was not found"
}

function Invoke-TeamGit {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [int]$TimeoutSeconds = 300
    )
    return (Invoke-NativeProcess -FilePath (Get-TeamGit) -Arguments $Arguments `
            -WorkingDirectory $WorkingDirectory -TimeoutSeconds $TimeoutSeconds)
}

function Test-TeamBranch {
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [Parameter(Mandatory = $true)][string]$Branch)
    $result = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("rev-parse", "--verify", "--quiet", "refs/heads/$Branch")
    return $result.Success
}

function Test-TeamWorktreeHealthy {
    <#
    .SYNOPSIS
        Whether a worktree is whole: its .git file is there, git can read it, and its admin
        folder has an 'index' and neither 'locked' nor 'index.lock'. A pure check; it removes
        nothing.

    .DESCRIPTION
        2026-10-06: a 'git worktree add' killed at 300 s left the folder and its .git file but an
        admin folder with 'locked' and an empty 'index.lock' and no 'index'; the old ".git is
        there" check sent a worker into it. Returns Healthy, Reason (Turkish: which condition)
        and GitDir (the admin folder, $null when git could not name it).
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$Branch = ""
    )
    $answer = { param($ok, $reason, $gitDir) [pscustomobject]@{ Healthy = $ok; Reason = $reason; GitDir = $gitDir } }
    if (-not (Test-Path -LiteralPath (Join-Path $Path ".git") -PathType Leaf)) { return (& $answer $false "ağaçta .git dosyası yok" $null) }
    try { $rev = Invoke-TeamGit -WorkingDirectory $Path -Arguments @("rev-parse", "--git-dir") }
    catch { return (& $answer $false "git rev-parse --git-dir koşamadı: $($_.Exception.Message)" $null) }
    if (-not $rev.Success) { return (& $answer $false "git rev-parse --git-dir düştü: $($rev.StdErr.Trim())" $null) }
    $gitDir = $rev.StdOut.Trim() -replace '/', '\'
    if (-not [System.IO.Path]::IsPathRooted($gitDir)) { $gitDir = Join-Path $Path $gitDir }
    if (Test-Path -LiteralPath (Join-Path $gitDir "locked")) { return (& $answer $false "yönetim klasöründe 'locked' var" $gitDir) }
    if (Test-Path -LiteralPath (Join-Path $gitDir "index.lock")) { return (& $answer $false "yönetim klasöründe 'index.lock' var" $gitDir) }
    if (-not (Test-Path -LiteralPath (Join-Path $gitDir "index"))) { return (& $answer $false "yönetim klasöründe 'index' yok" $gitDir) }
    return (& $answer $true "" $gitDir)
}

function New-TeamHostSlowError {
    <# The fixed-prefix error of a 'worktree add' that failed twice (or whose lock never came): Data['PagentosReason'] = 'host-slow'. #>
    param([Parameter(Mandatory = $true)][string]$Detail)
    $exception = New-Object System.Exception ("host yavaş: git worktree add " + $Detail)
    $exception.Data["PagentosReason"] = "host-slow"
    return $exception
}

function Get-TeamWorktreeWork {
    <#
    .SYNOPSIS
        What in a worktree folder could be somebody's work: $null when there is none, else the
        reason in Turkish. Used only before the repair removes a half-made tree.

    .DESCRIPTION
        With its index there, plain 'git status --porcelain --untracked-files=all' must be empty.
        With the index missing (the half-made case) git status reports every file as deleted and
        untracked, so it is asked against a TEMPORARY index read from HEAD (the locked admin
        folder is never written); there a ' D' line is a file the killed checkout never wrote,
        not work, and anything else is. When git cannot be asked at all, the careful path: any
        file in the folder beside .git keeps the folder.
    #>
    param([Parameter(Mandatory = $true)][string]$Path, $GitDir)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $lines = $null
    if ($GitDir -and (Test-Path -LiteralPath (Join-Path $GitDir "index"))) {
        try {
            $status = Invoke-TeamGit -WorkingDirectory $Path -Arguments @("status", "--porcelain", "--untracked-files=all")
            if ($status.Success) { $lines = @($status.StdOut -split "`r?`n" | Where-Object { $_.Trim() }) }
        }
        catch { $lines = $null }
    }
    elseif ($GitDir) {
        $tempIndex = Join-Path ([System.IO.Path]::GetTempPath()) ("pagentos-wt-index-" + [guid]::NewGuid().ToString("N"))
        $previous = $env:GIT_INDEX_FILE
        try {
            $env:GIT_INDEX_FILE = $tempIndex
            $read = Invoke-TeamGit -WorkingDirectory $Path -Arguments @("read-tree", "HEAD")
            if ($read.Success) {
                $status = Invoke-TeamGit -WorkingDirectory $Path -Arguments @("status", "--porcelain", "--untracked-files=all")
                if ($status.Success) { $lines = @($status.StdOut -split "`r?`n" | Where-Object { $_.Trim() -and $_ -notmatch '^ D ' }) }
            }
        }
        catch { $lines = $null }
        finally {
            $env:GIT_INDEX_FILE = $previous
            foreach ($f in @($tempIndex, "$tempIndex.lock")) { if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue } }
        }
    }
    if ($null -ne $lines) {
        if (@($lines).Count -gt 0) { return "kirli: $(@($lines).Count) değişiklik" }
        return $null
    }
    $files = @(Get-ChildItem -LiteralPath $Path -Force | Where-Object { $_.Name -ne ".git" })
    if (@($files).Count -gt 0) { return "kirli: git status koşamadı ve klasörde .git dışında $(@($files).Count) öğe var" }
    return $null
}

function Repair-TeamWorktree {
    <#
    .SYNOPSIS
        Clear a half-made worktree so it can be added again - ONLY when all three hold: it is
        unhealthy, its branch has no commit over Base (no branch counts as 0), and its folder is
        missing or clean. Anything else throws 'yarım ağaç onarılmadı: ...' and touches nothing.

    .DESCRIPTION
        The branch is never deleted: with 0 commits it equals Base and is attached again without
        -b. The admin entry removed by hand is only one under the repository's common git dir
        whose 'gitdir' file points at this tree's .git.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Branch,
        [string]$Base = "main",
        [string]$Why = ""
    )
    $health = Test-TeamWorktreeHealthy -RepoRoot $RepoRoot -Path $Path -Branch $Branch
    if ($health.Healthy -and -not $Why) { return }
    $reason = if ($Why) { $Why } else { $health.Reason }
    $commits = 0
    if (Test-TeamBranch -RepoRoot $RepoRoot -Branch $Branch) {
        $count = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("rev-list", "--count", "$Base..refs/heads/$Branch")
        if (-not $count.Success) { throw "yarım ağaç onarılmadı: $reason (commit sayısı okunamadı: $($count.StdErr.Trim()))" }
        $commits = [int]$count.StdOut.Trim()
    }
    if ($commits -gt 0) { throw "yarım ağaç onarılmadı: $reason ($commits commit)" }
    $work = Get-TeamWorktreeWork -Path $Path -GitDir $health.GitDir
    if ($work) { throw "yarım ağaç onarılmadı: $reason ($work)" }

    try { [void](Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("worktree", "unlock", $Path)) } catch { }
    try { [void](Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("worktree", "remove", "--force", "--force", $Path)) } catch { }
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Recurse -Force }
    $common = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("rev-parse", "--git-common-dir")
    if ($common.Success) {
        $commonDir = $common.StdOut.Trim() -replace '/', '\'
        if (-not [System.IO.Path]::IsPathRooted($commonDir)) { $commonDir = Join-Path $RepoRoot $commonDir }
        $admins = Join-Path $commonDir "worktrees"
        $want = [System.IO.Path]::GetFullPath((Join-Path $Path ".git")).TrimEnd('\')
        if (Test-Path -LiteralPath $admins) {
            foreach ($entry in @(Get-ChildItem -LiteralPath $admins -Directory -Force)) {
                $pointer = Join-Path $entry.FullName "gitdir"
                if (-not (Test-Path -LiteralPath $pointer)) { continue }
                $target = ([System.IO.File]::ReadAllText($pointer).Trim()) -replace '/', '\'
                if ($target -and [System.IO.Path]::IsPathRooted($target) -and
                    ([System.IO.Path]::GetFullPath($target).TrimEnd('\') -ieq $want)) {
                    Remove-Item -LiteralPath $entry.FullName -Recurse -Force
                }
            }
        }
    }
    [void](Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("worktree", "prune"))
}

function Invoke-TeamWorktreeAddLock {
    <#
    .SYNOPSIS
        Run a block holding the machine's one 'git worktree add' lock (the cycle, a test round,
        an integration and a duty run share one disk; on 6 October their adds piled up past
        300 s). Global\ first, Local\ when Global\ cannot be opened. A lock not had within the
        bound throws the 'host yavaş' error.
    #>
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Body,
        [int]$TimeoutSeconds = 900
    )
    $mutex = $null
    try { $mutex = New-Object System.Threading.Mutex($false, "Global\PagentOS-git-worktree-add") }
    catch { $mutex = New-Object System.Threading.Mutex($false, "Local\PagentOS-git-worktree-add") }
    $held = $false
    try {
        try { $held = $mutex.WaitOne($TimeoutSeconds * 1000) }
        catch [System.Threading.AbandonedMutexException] { $held = $true }
        if (-not $held) { throw (New-TeamHostSlowError -Detail "(kilit $TimeoutSeconds sn içinde alınamadı)") }
        return (& $Body)
    }
    finally {
        if ($held) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

function New-TeamWorktree {
    <#
    .SYNOPSIS
        A branch and its worktree, from a base. Both are left alone when they exist and the
        worktree is healthy.

    .DESCRIPTION
        2026-10-06 (card worktree-half-made-heals): a half-made worktree - unhealthy, or its add
        failed - is repaired (Repair-TeamWorktree: only with 0 commits and nothing in it) and
        added ONCE more; a second failure throws the 'host yavaş: git worktree add' error with
        Data['PagentosReason'] = 'host-slow'. The add and the repair hold the machine's
        'worktree add' lock; the 'already there' answer does not take it.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$Branch,
        [string]$Base = "main",
        [int]$LockTimeoutSeconds = 900
    )
    $path = Get-TeamWorktreePath -RepoRoot $RepoRoot -Branch $Branch
    if (Test-Path -LiteralPath (Join-Path $path ".git")) {
        if ((Test-TeamWorktreeHealthy -RepoRoot $RepoRoot -Path $path -Branch $Branch).Healthy) {
            return [pscustomobject]@{ Path = $path; Branch = $Branch; Created = $false; Note = "the worktree is already there" }
        }
    }
    return (Invoke-TeamWorktreeAddLock -TimeoutSeconds $LockTimeoutSeconds -Body {
            $add = {
                $parent = Split-Path -Parent $path
                if (-not (Test-Path -LiteralPath $parent)) { [void](New-Item -ItemType Directory -Force -Path $parent) }
                try {
                    if (Test-TeamBranch -RepoRoot $RepoRoot -Branch $Branch) {
                        $r = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("worktree", "add", $path, $Branch)
                    }
                    else {
                        $r = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("worktree", "add", "-b", $Branch, $path, $Base)
                    }
                    if ($r.Success) { return "" }
                    return $r.StdErr.Trim()
                }
                catch { return $_.Exception.Message }
            }
            # Looked at again inside the lock: another process may have just finished this tree.
            $why = ""
            if (Test-Path -LiteralPath (Join-Path $path ".git")) {
                $health = Test-TeamWorktreeHealthy -RepoRoot $RepoRoot -Path $path -Branch $Branch
                if ($health.Healthy) {
                    return [pscustomobject]@{ Path = $path; Branch = $Branch; Created = $false; Note = "the worktree is already there" }
                }
                $why = $health.Reason
            }
            else {
                $failure = & $add
                if (-not $failure) { return [pscustomobject]@{ Path = $path; Branch = $Branch; Created = $true; Note = "" } }
                $why = "git worktree add düştü: $failure"
            }
            Repair-TeamWorktree -RepoRoot $RepoRoot -Path $path -Branch $Branch -Base $Base -Why $why
            $failure = & $add
            if ($failure) { throw (New-TeamHostSlowError -Detail "ikinci denemede de düştü (${Branch}): $failure") }
            return [pscustomobject]@{ Path = $path; Branch = $Branch; Created = $true; Note = "yarım ağaç onarıldı ve yeniden eklendi: $why" }
        })
}

function Remove-TeamWorktree {
    <#
    .SYNOPSIS
        Remove a worktree that is CLEAN. A worktree with work in it is somebody's work.

    .DESCRIPTION
        The branch is kept: it is the record of what was done, and the merge names it.
    #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [Parameter(Mandatory = $true)][string]$Branch)
    $path = Get-TeamWorktreePath -RepoRoot $RepoRoot -Branch $Branch
    if (-not (Test-Path -LiteralPath $path)) {
        return [pscustomobject]@{ Removed = $false; Note = "there is no worktree" }
    }
    $status = Invoke-TeamGit -WorkingDirectory $path -Arguments @("status", "--porcelain")
    if (-not $status.Success) { throw "git status failed in ${path}: $($status.StdErr.Trim())" }
    if ($status.StdOut.Trim().Length -gt 0) {
        return [pscustomobject]@{ Removed = $false; Note = "the worktree is not clean; it was left as it is" }
    }
    $result = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("worktree", "remove", $path)
    if (-not $result.Success) { throw "git worktree remove failed: $($result.StdErr.Trim())" }
    return [pscustomobject]@{ Removed = $true; Note = "" }
}

function Get-TeamChangedFiles {
    <# The files a branch changed since it left its base, repository-relative. #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [Parameter(Mandatory = $true)][string]$Branch, [string]$Base = "main")
    $result = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("diff", "--name-only", "$Base...$Branch")
    if (-not $result.Success) { throw "git diff failed: $($result.StdErr.Trim())" }
    return @($result.StdOut -split "`r?`n" | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() })
}

function Merge-TeamBranch {
    <#
    .SYNOPSIS
        Merge an approved task branch into the cycle's integration branch, in the
        integration branch's OWN worktree.

    .DESCRIPTION
        A branch that is already merged is reported as merged. A conflict is aborted, the
        integration branch is left as it was, and the answer says so: the task goes back.
        When the integration branch is made by this call, older integration branches with
        merged, unreleased cards are carried into it first (-Queue; CarriedForward says how).
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$CycleId,
        [Parameter(Mandatory = $true)][string]$Branch,
        [string]$Base = "main",
        $Queue = $null
    )
    $integration = "integrate/$CycleId"
    $tree = New-TeamWorktree -RepoRoot $RepoRoot -Branch $integration -Base $Base
    # A branch made just now starts from main alone: what an older cycle merged and no release
    # took is carried over first (Invoke-TeamCarryForward), or it is never released.
    $carried = @()
    if ($tree.Created) {
        if ($null -eq $Queue) {
            # The cycle (scripts/team/cycle.ps1) keeps its live queue in $script:queue and saves
            # the whole document; a caller without one (integration-branch.ps1, a test) carries nothing.
            $found = Get-Variable -Name "queue" -Scope Script -ErrorAction SilentlyContinue
            if ($null -ne $found) { $Queue = $found.Value }
        }
        if ($null -ne $Queue) {
            $carried = @(Invoke-TeamCarryForward -RepoRoot $RepoRoot -TreePath $tree.Path -Integration $integration -Queue $Queue -Base $Base)
        }
    }
    $ancestor = Invoke-TeamGit -WorkingDirectory $tree.Path -Arguments @("merge-base", "--is-ancestor", $Branch, "HEAD")
    if ($ancestor.ExitCode -eq 0) {
        return [pscustomobject]@{ Merged = $true; Already = $true; Conflict = $false; Integration = $integration; Detail = ""; CarriedForward = $carried }
    }
    $message = "merge: $Branch into $integration"
    $merge = Invoke-TeamGit -WorkingDirectory $tree.Path -Arguments @("merge", "--no-ff", "-m", $message, $Branch)
    if ($merge.Success) {
        return [pscustomobject]@{ Merged = $true; Already = $false; Conflict = $false; Integration = $integration; Detail = ""; CarriedForward = $carried }
    }
    [void](Invoke-TeamGit -WorkingDirectory $tree.Path -Arguments @("merge", "--abort"))
    $detail = ($merge.StdOut + "`n" + $merge.StdErr).Trim()
    return [pscustomobject]@{ Merged = $false; Already = $false; Conflict = $true; Integration = $integration; Detail = $detail; CarriedForward = $carried }
}

function Get-TeamOrphanMerges {
    <#
    .SYNOPSIS
        The cards 'merged' into an integration branch that is neither the current cycle's nor in
        main: work that passed and that no release will take (2026-10-05..07: five cards on
        integrate/d20261004 while integrate/d20261005 and the releases were cut from main).
        A merged card with NO integration branch is an orphan too unless its id is in HeldIds
        (2026-10-07: three of the five, dev-db-branch-migration-leak among them, had none).
        Pure: which branches main holds is the caller's answer (Get-TeamBranchesInMain), and
        which unbranched cards' work is held is too (Get-TeamUnbranchedHeld).
    #>
    param($Queue, [Parameter(Mandatory = $true)][string]$Current, [string[]]$InMain = @(), [string[]]$HeldIds = @())
    return @(Get-TeamTasks -Queue $Queue | Where-Object {
            $branch = [string](Get-TeamProperty -InputObject $_ -Name "integration_branch" -Default "")
            ([string](Get-TeamProperty -InputObject $_ -Name "state" -Default "")) -eq "merged" -and $(
                if ($branch.Trim()) { $branch -ne $Current -and @($InMain) -notcontains $branch }
                else { @($HeldIds) -notcontains [string]$_.id })
        } | Sort-Object -Property @{ Expression = { ([string](Get-TeamProperty -InputObject $_ -Name "integration_branch" -Default "")).Trim() } }, id)
}

function Get-TeamUnbranchedHeld {
    <#
        Of the merged cards with no integration branch, the ids whose own work main (or the
        current integration branch) holds: their sha, else their task branch's tip. A card with
        neither cannot be shown to be in main and is not in the answer.
    #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot, $Queue, [string]$Current = "", [string]$Base = "main")
    $targets = @($Base)
    if ($Current -and (Test-TeamBranch -RepoRoot $RepoRoot -Branch $Current)) { $targets += "refs/heads/$Current" }
    $held = New-Object System.Collections.ArrayList
    foreach ($task in @(Get-TeamTasks -Queue $Queue)) {
        if ([string](Get-TeamProperty -InputObject $task -Name "state" -Default "") -ne "merged") { continue }
        if (([string](Get-TeamProperty -InputObject $task -Name "integration_branch" -Default "")).Trim()) { continue }
        $revision = ([string](Get-TeamProperty -InputObject $task -Name "sha" -Default "")).Trim()
        $branch = ([string](Get-TeamProperty -InputObject $task -Name "branch" -Default "")).Trim()
        if (-not $revision -and $branch -and (Test-TeamBranch -RepoRoot $RepoRoot -Branch $branch)) { $revision = "refs/heads/$branch" }
        if (-not $revision) { continue }
        foreach ($target in $targets) {
            if ((Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("merge-base", "--is-ancestor", $revision, $target)).ExitCode -eq 0) {
                [void]$held.Add([string]$task.id)
                break
            }
        }
    }
    return @($held.ToArray())
}

function Get-TeamBranchesInMain {
    <# Of the named branches, the ones whose tip main holds. A branch that does not exist is not in it. #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [string[]]$Branches = @(), [string]$Base = "main")
    $held = New-Object System.Collections.ArrayList
    foreach ($branch in @($Branches | Where-Object { $_ } | Sort-Object -Unique)) {
        if (-not (Test-TeamBranch -RepoRoot $RepoRoot -Branch $branch)) { continue }
        $result = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("merge-base", "--is-ancestor", "refs/heads/$branch", $Base)
        if ($result.ExitCode -eq 0) { [void]$held.Add($branch) }
    }
    return @($held.ToArray())
}

function Invoke-TeamCarryForward {
    <#
    .SYNOPSIS
        Into a cycle's integration branch made just now: every older integration branch that
        holds 'merged' cards and is not in main, oldest first, --no-ff, "carried forward: <branch>".

    .DESCRIPTION
        Carried, its cards name the new branch, so the integration step gates them with it. A
        conflict is aborted, the branch is left as it was, and the cards are STOPPED with the
        reason "yetim entegrasyon: <branch>" - the Danışman's, never dropped without a word.
        Returns one row per branch: Branch, Tasks (ids), Carried, Line (Turkish).
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$TreePath,
        [Parameter(Mandatory = $true)][string]$Integration,
        [Parameter(Mandatory = $true)]$Queue,
        [string]$Base = "main"
    )
    $candidates = @(Get-TeamTasks -Queue $Queue | ForEach-Object { [string](Get-TeamProperty -InputObject $_ -Name "integration_branch" -Default "") })
    $inMain = @(Get-TeamBranchesInMain -RepoRoot $RepoRoot -Branches $candidates -Base $Base)
    # A card with no integration branch has no branch to carry; the cycle report names it.
    $orphans = @(Get-TeamOrphanMerges -Queue $Queue -Current $Integration -InMain $inMain | Where-Object {
            ([string](Get-TeamProperty -InputObject $_ -Name "integration_branch" -Default "")).Trim() })
    $rows = New-Object System.Collections.ArrayList
    foreach ($group in @($orphans | Group-Object -Property { [string](Get-TeamProperty -InputObject $_ -Name "integration_branch" -Default "") })) {
        $branch = [string]$group.Name
        $tasks = @($group.Group)
        $ids = (@($tasks | ForEach-Object { [string]$_.id }) -join ", ")
        if (-not (Test-TeamBranch -RepoRoot $RepoRoot -Branch $branch)) {
            $merge = [pscustomobject]@{ Success = $false; StdOut = ""; StdErr = "the branch does not exist" }
        }
        elseif ((Invoke-TeamGit -WorkingDirectory $TreePath -Arguments @("merge-base", "--is-ancestor", "refs/heads/$branch", "HEAD")).ExitCode -eq 0) {
            $merge = [pscustomobject]@{ Success = $true; StdOut = ""; StdErr = "" }
        }
        else {
            $merge = Invoke-TeamGit -WorkingDirectory $TreePath -Arguments @("merge", "--no-ff", "-m", "carried forward: $branch", "refs/heads/$branch")
        }
        if ($merge.Success) {
            foreach ($task in $tasks) { Set-TeamProperty -InputObject $task -Name "integration_branch" -Value $Integration }
            $line = "$branch $Integration dalına taşındı (birleşmiş ama yayınlanmamış): $ids"
            [void]$rows.Add([pscustomobject]@{ Branch = $branch; Tasks = @($tasks | ForEach-Object { [string]$_.id }); Carried = $true; Line = $line })
            continue
        }
        [void](Invoke-TeamGit -WorkingDirectory $TreePath -Arguments @("merge", "--abort"))
        foreach ($task in $tasks) {
            Set-TeamProperty -InputObject $task -Name "state" -Value "stopped"
            Set-TeamProperty -InputObject $task -Name "reason" -Value "yetim entegrasyon: $branch"
            Set-TeamProperty -InputObject $task -Name "updated_at" -Value (Get-TeamTimestamp)
        }
        $line = "$branch $Integration dalına taşınamadı (çakışma ya da dal yok); işler durduruldu, Danışman'a: $ids"
        [void]$rows.Add([pscustomobject]@{ Branch = $branch; Tasks = @($tasks | ForEach-Object { [string]$_.id }); Carried = $false; Line = $line })
    }
    return @($rows.ToArray())
}

function Undo-TeamMerge {
    <#
    .SYNOPSIS
        Take back the merge Merge-TeamBranch just made - when, and only when, it is still the
        integration branch's last commit.

    .DESCRIPTION
        For the one case where the team's store refuses the task's "merged" right after the
        merge (somebody stopped the task in that moment). The integration branch is written by
        the lock's holder alone, so its HEAD is that merge unless something else was merged
        since. Checked, all three: HEAD has two parents, the second is the task branch's tip,
        and the worktree is clean. Then the branch is put back on the first parent (a reset,
        not a revert: after a revert the task's branch would still be an ancestor, and a later
        merge of it would answer "already merged" for content that is gone). Returns $true
        when the merge was taken back; anything else leaves everything as it is.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$CycleId,
        [Parameter(Mandatory = $true)][string]$Branch
    )
    $path = Join-Path $RepoRoot (".claude\worktrees\integrate\" + $CycleId)
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    $second = Invoke-TeamGit -WorkingDirectory $path -Arguments @("rev-parse", "--verify", "--quiet", "HEAD^2")
    $tip = Invoke-TeamGit -WorkingDirectory $path -Arguments @("rev-parse", "--verify", "--quiet", "refs/heads/$Branch")
    if (-not $second.Success -or -not $tip.Success) { return $false }
    if ($second.StdOut.Trim() -ne $tip.StdOut.Trim()) { return $false }
    $dirty = Invoke-TeamGit -WorkingDirectory $path -Arguments @("status", "--porcelain")
    if (-not $dirty.Success -or $dirty.StdOut.Trim()) { return $false }
    $reset = Invoke-TeamGit -WorkingDirectory $path -Arguments @("reset", "--hard", "--quiet", "HEAD^1")
    return [bool]$reset.Success
}

# ---------------------------------------------------------------------------- a role run

function New-TeamTaskCard {
    <# The prompt of a run: the task, and nothing of any other task. #>
    param([Parameter(Mandatory = $true)]$Task, [Parameter(Mandatory = $true)][string]$Role, [Parameter(Mandatory = $true)][string]$CycleId)
    $lines = New-Object System.Collections.ArrayList
    [void]$lines.Add("# Task card ($Role, cycle $CycleId)")
    [void]$lines.Add("")
    foreach ($name in @("id", "title", "roadmap_row", "goal", "acceptance", "evidence_expected", "branch", "worktree", "plan", "proposal")) {
        $value = Get-TeamProperty -InputObject $Task -Name $name
        if ($null -ne $value -and ([string]$value).Trim()) { [void]$lines.Add("- ${name}: $value") }
    }
    $area = @(Get-TeamProperty -InputObject $Task -Name "area" -Default @())
    [void]$lines.Add("- area: " + (($area | ForEach-Object { [string]$_ }) -join ", "))
    # Why it came back, in the words of whoever sent it back (the inspector's list, the
    # cycle's area check, the lead). pilot-01, 2026-09-30: the lead's return note was in
    # the queue and never in the card, and the worker re-checked git and changed nothing.
    $reason = [string](Get-TeamProperty -InputObject $Task -Name "reason" -Default "")
    $state = [string](Get-TeamProperty -InputObject $Task -Name "state" -Default "")
    if ($reason.Trim() -and @("returned", "in_progress") -contains $state) {
        [void]$lines.Add("")
        [void]$lines.Add("## Why this task came back - address every point")
        [void]$lines.Add($reason.Trim())
    }
    $reports = @(Get-TeamProperty -InputObject $Task -Name "reports" -Default @())
    if (@($reports).Count -gt 0) {
        $last = $reports[@($reports).Count - 1]
        [void]$lines.Add("")
        [void]$lines.Add("## The last report on this task ($(Get-TeamProperty -InputObject $last -Name 'role' -Default '?'))")
        foreach ($line in @(Get-TeamProperty -InputObject $last -Name "summary" -Default @())) { [void]$lines.Add([string]$line) }
    }
    [void]$lines.Add("")
    [void]$lines.Add("Work as your role file says. Return your report as your final message, at most 40 lines.")
    return (($lines.ToArray()) -join "`n")
}

function New-TeamSplitCard {
    <#
    .SYNOPSIS
        The prompt of the lead's split run: one proposal, the areas that are taken, the shape
        of the file to write. The run has Read and Write only; the cycle validates the file.
    #>
    param(
        [Parameter(Mandatory = $true)]$Task,
        [Parameter(Mandatory = $true)]$Queue,
        [Parameter(Mandatory = $true)][string]$CycleId,
        [Parameter(Mandatory = $true)][string]$SplitFile
    )
    $lines = New-Object System.Collections.ArrayList
    [void]$lines.Add("# Split run (lead, cycle $CycleId)")
    [void]$lines.Add("")
    [void]$lines.Add("This run does ONE thing: split the proposal below into tasks. You have Read and Write; you run")
    [void]$lines.Add("no command, edit no file and dispatch no agent. Write exactly one file, the one named in split_file.")
    [void]$lines.Add("")
    foreach ($name in @("id", "title", "roadmap_row", "proposal")) {
        $value = Get-TeamProperty -InputObject $Task -Name $name
        if ($null -ne $value -and ([string]$value).Trim()) { [void]$lines.Add("- ${name}: $value") }
    }
    [void]$lines.Add("- split_file: $SplitFile")
    [void]$lines.Add("")
    [void]$lines.Add("Read the proposal, docs/ROADMAP.md, docs/TEAM_PROTOCOL.md (sections 3a and 4) and the earlier splits")
    [void]$lines.Add("under team/plans/*-split.md for the shape of a good one.")
    [void]$lines.Add("")
    [void]$lines.Add("split_file is a JSON list of task objects. Each has: id (a-z, 0-9, '-'; 3-64; not in the queue),")
    [void]$lines.Add("title, roadmap_row (the row it serves), area (a list of repository-relative paths, at most 25),")
    [void]$lines.Add("goal, acceptance, evidence_expected; optionally depends_on (ids) and needs_integration (true).")
    [void]$lines.Add("The cycle - not you - checks the list and takes it WHOLE or refuses it whole: an area inside")
    [void]$lines.Add("another task's in work, a shared file (docs/HANDOFF.md, docs/DECISIONS.md, state/BUILD_STATE.json,")
    [void]$lines.Add("docs/THIRD_PARTY_COMPONENTS.md, team/queue.json) or a directory holding one, a missing field, an area")
    [void]$lines.Add("outside the repository, main or hand-gestures, all refuse it. Two tasks of yours may share an area")
    [void]$lines.Add("only when one lists the other in depends_on.")
    $taken = New-Object System.Collections.ArrayList
    foreach ($other in @(Get-TeamTasks -Queue $Queue)) {
        $state = [string](Get-TeamProperty -InputObject $other -Name "state" -Default "")
        if (@("approved", "assigned", "in_progress", "inspecting", "returned") -notcontains $state) { continue }
        $area = @(Get-TeamProperty -InputObject $other -Name "area" -Default @())
        if (@($area).Count -gt 0) { [void]$taken.Add("- $($other.id) [$state]: " + (($area | ForEach-Object { [string]$_ }) -join ", ")) }
    }
    [void]$lines.Add("")
    [void]$lines.Add("## Areas that are taken (do not overlap)")
    if (@($taken).Count -eq 0) { [void]$lines.Add("- none") } else { foreach ($row in $taken) { [void]$lines.Add([string]$row) } }
    [void]$lines.Add("")
    [void]$lines.Add("Return your report as your final message, at most 40 lines: which tasks, and why that split.")
    return (($lines.ToArray()) -join "`n")
}

function New-TeamDutyCard {
    <#
    .SYNOPSIS
        The prompt of the Proje Yöneticisi's duty run (pm-duty-stopped): the stopped tasks, each
        with what it needs to be judged, and the one file to write. The run has Read, Grep, Glob
        and Write; the cycle validates the file (Test-TeamDuty) and applies it.
    .DESCRIPTION
        No line starts with "- id:" - the run is about several tasks, not one.
    #>
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Tasks,
        [Parameter(Mandatory = $true)][string]$CycleId,
        [Parameter(Mandatory = $true)][string]$DutyFile,
        # Runs in flight with no sign of life (TeamLiveness.ps1): run ("<task>/<role>"),
        # idle_minutes, last_activity_at, restarts, child (a stuck tool process, or empty).
        [object[]]$StuckRuns = @()
    )
    $flat = { param($Value) return ((([string]$Value) -replace '\s+', ' ').Trim()) }
    $lines = New-Object System.Collections.ArrayList
    [void]$lines.Add("# Nöbet run (lead / Proje Yöneticisi, cycle $CycleId)")
    [void]$lines.Add("")
    [void]$lines.Add("This run does ONE thing: for each stopped task below, decide what happens to it. You have Read, Grep, Glob")
    [void]$lines.Add("and Write; you run no command, edit no file and dispatch no agent. Write exactly one file, the one named")
    [void]$lines.Add("in duty_file. Follow the section 'Nöbet: duran işler (Proje Yöneticisi)' of your role file.")
    [void]$lines.Add("")
    [void]$lines.Add("- duty_file: $DutyFile")
    [void]$lines.Add("")
    [void]$lines.Add("duty_file is ONE JSON object: { `"decisions`": [ { `"task`": `"<id>`", `"action`": `"return`" | `"grant_and_return`" | `"escalate`",")
    [void]$lines.Add("`"grant`": [`"repo/relative/path`"], `"reason`": `"<Türkçe, en çok 1200 karakter: işçinin ne yapacağı>`" } ] }.")
    [void]$lines.Add("At most one decision per task; a task you leave out stays stopped. `"grant`" only with grant_and_return: 1 to 5")
    [void]$lines.Add("plain repository-relative paths (no '..', no drive, no leading slash). The cycle - not you - checks the file and")
    [void]$lines.Add("takes it WHOLE or refuses it whole: a task not listed here, an unknown action, an empty reason, a grant of a")
    [void]$lines.Add("lead-protected path (the shared files, .claude/agents, the constitution, CLAUDE.md, secrets, the recovery roots,")
    [void]$lines.Add("the list file scripts/lib/TeamAreaProtected.ps1 and the rest of that list) all refuse it. The area logic beside")
    [void]$lines.Add("the list (TeamArea.ps1) is an ordinary team script you may grant. A return that would put the task beside")
    [void]$lines.Add("another task holding the same files is not forced: the task waits, stopped, until that work is done.")
    [void]$lines.Add("")
    [void]$lines.Add("## Duran işler")
    foreach ($task in @($Tasks)) {
        [void]$lines.Add("")
        [void]$lines.Add("### $([string](Get-TeamProperty -InputObject $task -Name 'id' -Default '?'))")
        foreach ($name in @("title", "branch", "sha", "returns", "failed_runs", "updated_at")) {
            $value = Get-TeamProperty -InputObject $task -Name $name
            if ($null -ne $value -and ([string]$value).Trim()) { [void]$lines.Add("  - ${name}: " + (& $flat $value)) }
        }
        $area = @(Get-TeamProperty -InputObject $task -Name "area" -Default @() | ForEach-Object { [string]$_ })
        [void]$lines.Add("  - area: " + ($area -join ", "))
        $depends = @(Get-TeamProperty -InputObject $task -Name "depends_on" -Default @() | ForEach-Object { [string]$_ })
        [void]$lines.Add("  - depends_on: " + $(if (@($depends).Count -gt 0) { $depends -join ", " } else { "-" }))
        $reason = & $flat (Get-TeamProperty -InputObject $task -Name "reason" -Default "")
        if ($reason.Length -gt 1500) { $reason = $reason.Substring(0, 1500) + " [...]" }
        [void]$lines.Add("  - stop reason: " + $(if ($reason) { $reason } else { "(none written)" }))
        $reports = @(Get-TeamProperty -InputObject $task -Name "reports" -Default @() | Where-Object { $null -ne $_ })
        $last = if (@($reports).Count -gt 3) { @($reports[(@($reports).Count - 3)..(@($reports).Count - 1)]) } else { @($reports) }
        if (@($last).Count -eq 0) { [void]$lines.Add("  - last reports: none") }
        else {
            [void]$lines.Add("  - last reports (oldest first; read the inspector's in full):")
            foreach ($entry in $last) {
                $file = [string](Get-TeamProperty -InputObject $entry -Name "file" -Default "")
                $role = [string](Get-TeamProperty -InputObject $entry -Name "role" -Default "?")
                $cycle = [string](Get-TeamProperty -InputObject $entry -Name "cycle" -Default "?")
                [void]$lines.Add("    - $(if ($file) { $file } else { '(no file)' }) ($role, cycle $cycle)")
            }
        }
    }
    $stuck = @($StuckRuns | Where-Object { $null -ne $_ })
    if (@($stuck).Count -gt 0) {
        [void]$lines.Add("")
        [void]$lines.Add("## Takılmış olabilecek koşular")
        [void]$lines.Add("")
        [void]$lines.Add("These runs (or a tool process of theirs) showed no sign of life - no write in their temp folder or")
        [void]$lines.Add("worktree, no output, no CPU - for the minutes named. Decide each in the same file, under `"stuck`": [ { `"run`":")
        [void]$lines.Add("`"<task>/<role>`", `"action`": `"wait`" | `"restart`" | `"escalate`", `"reason`": `"<Türkçe, en çok 1200 karakter>`" } ].")
        [void]$lines.Add("wait: it is working (say what you saw); restart: the cycle stops that run's process tree and starts it again")
        [void]$lines.Add("from its worktree (its commits stay); escalate: the Danışman's. A run idle 90 minutes is restarted once")
        [void]$lines.Add("without you, the second time escalated.")
        foreach ($entry in $stuck) {
            $run = [string](Get-TeamProperty -InputObject $entry -Name "run" -Default "?")
            $idle = [int](Get-TeamProperty -InputObject $entry -Name "idle_minutes" -Default 0)
            $line = "- ${run}: $idle dk iz yok"
            $last = [string](Get-TeamProperty -InputObject $entry -Name "last_activity_at" -Default "")
            if ($last) { $line += " (son iz $last)" }
            $line += "; yeniden başlatma: $([int](Get-TeamProperty -InputObject $entry -Name 'restarts' -Default 0))"
            $child = [string](Get-TeamProperty -InputObject $entry -Name "child" -Default "")
            if ($child) { $line += "; takılı çocuk süreç: " + (& $flat $child) }
            [void]$lines.Add($line)
        }
    }
    [void]$lines.Add("")
    [void]$lines.Add("Return your report as your final message, at most 40 lines: one line per task, the action and why.")
    return (($lines.ToArray()) -join "`n")
}

function Get-TeamRunArguments {
    <#
    .SYNOPSIS
        The arguments of one `claude -p` run. Built here so a test can read them.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RoleFile,
        [double]$MaxUsd = 0,
        [string]$Model = "",
        [string[]]$PrefixArguments = @(),
        # Tools the role file grants but THIS run must not have (the lead's split run: no
        # Bash, no Edit). They are left out of --allowedTools and named in --disallowedTools.
        [string[]]$ExcludeTools = @()
    )
    $tools = @(Get-TeamRoleTools -RoleFile $RoleFile)
    if (@($tools).Count -eq 0) { throw "the role file grants no tools: $RoleFile" }
    # A run never starts agents of its own: the cycle is what dispatches.
    $tools = @($tools | Where-Object { $_ -ne "Agent" -and $_ -ne "Task" -and @($ExcludeTools) -notcontains $_ })
    $arguments = New-Object System.Collections.ArrayList
    foreach ($argument in @($PrefixArguments)) { [void]$arguments.Add([string]$argument) }
    # The line stream, not the single document: the usage limit's type, its reset and the two
    # percentages are only in the stream's `rate_limit_event` (model-policy-cycle). Never
    # --fallback-model: it does not fire on a usage limit and would lower a run without a word.
    foreach ($argument in @(
            "-p", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
            "--append-system-prompt-file", $RoleFile,
            "--allowedTools", ($tools -join ","),
            "--permission-mode", "acceptEdits"
        )) { [void]$arguments.Add([string]$argument) }
    if (@($ExcludeTools).Count -gt 0) {
        [void]$arguments.Add("--disallowedTools")
        [void]$arguments.Add((@($ExcludeTools) -join ","))
    }
    if ($MaxUsd -gt 0) {
        # Owner decision 2026-09-30 (ADR-0214 addendum 3): the subscription has no money cap,
        # so a run is given none by default; the flag only appears when a caller names one.
        [void]$arguments.Add("--max-budget-usd")
        [void]$arguments.Add($MaxUsd.ToString("0.##", [System.Globalization.CultureInfo]::InvariantCulture))
    }
    if ($Model) { [void]$arguments.Add("--model"); [void]$arguments.Add($Model) }
    return @($arguments.ToArray())
}

function Start-TeamRun {
    <#
    .SYNOPSIS
        Start one role run. The prompt goes in on standard input; nothing waits here.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Prompt,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [string]$TempDirectory = "",
        [hashtable]$Environment = @{}
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.Arguments = (ConvertTo-NativeArgumentLine -Arguments $Arguments)
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $psi.WorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory).Path
    # The tool can switch a session off the model it was started on by itself (found in the
    # 2.1.285 binary; the variable is not on the documented page - best effort). The proof
    # that a run was not lowered is its modelUsage, which the cycle compares after every run.
    $psi.EnvironmentVariables["CLAUDE_CODE_NO_MODEL_FALLBACK"] = "1"
    # A run of the cycle is never woken again: its final message is its result. On 2026-10-03
    # five runs of one night started their suites in the background, ended with "I will report
    # when it finishes", and were judged as empty work. The tool's own switch takes the
    # background parameter out of the run's Bash tool (proven by the lead on 2.1.285: the call
    # is refused as an unexpected parameter), so a long command runs in the foreground.
    $psi.EnvironmentVariables["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] = "1"
    # ... which needs a foreground call that may last as long as a suite: the tool cuts a call at
    # ten minutes by default (measured: 'Command timed out after 10m 0s'; an 11-minute command
    # finished with this limit). An hour; a longer suite runs in slices.
    $psi.EnvironmentVariables["BASH_MAX_TIMEOUT_MS"] = "3600000"
    # The run's own temp folder (team/cycle-settings.json 'run_temp_root', owner 2026-10-03): the
    # tests a run starts write their temp folders there, on the data drive, and the folder is
    # emptied when the run ends (Remove-TeamRunTemp; the folder itself stays, it may be Git Bash's
    # /tmp for the whole machine) - C: filled to zero at 12:00 that day, and %TEMP%
    # held 2.67 million leaked folders the day before. Unset: the machine's TEMP, as before.
    if ($TempDirectory) {
        [void](New-Item -ItemType Directory -Force -Path $TempDirectory)
        foreach ($name in @("TEMP", "TMP", "TMPDIR")) { $psi.EnvironmentVariables[$name] = $TempDirectory }
    }
    # What the run needs to reach the team's board (ADR team-board): the address, the token
    # file's PATH (never the token), its seat and its task. Empty values are not set.
    foreach ($name in @($Environment.Keys)) {
        $value = [string]$Environment[$name]
        if ($value) { $psi.EnvironmentVariables[[string]$name] = $value }
    }
    $process = [System.Diagnostics.Process]::Start($psi)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($Prompt)
    $process.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
    $process.StandardInput.Close()
    return [pscustomobject]@{
        Process = $process
        StdOut  = $stdout
        StdErr  = $stderr
        Started = [datetime]::UtcNow
        # What it was started with: Restart-TeamRun starts the same run again from it.
        Launch  = @{
            FilePath = $FilePath; Arguments = $Arguments; Prompt = $Prompt; WorkingDirectory = $WorkingDirectory
            TempDirectory = $TempDirectory; Environment = $Environment
        }
    }
}

function Get-TeamGitBash {
    <# Git for Windows' own bash.exe, found beside git.exe - never System32's bash (WSL). #>
    $git = Get-TeamGit
    $dir = Split-Path -Parent $git
    for ($up = 0; $up -lt 3 -and $dir; $up++) {
        $candidate = Join-Path $dir "bin\bash.exe"
        if (Test-Path -LiteralPath $candidate) { return $candidate }
        $dir = Split-Path -Parent $dir
    }
    throw "Git's bash.exe was not found beside $git"
}

function Read-TeamGitBashMount {
    <# What Git Bash's `mount` prints, or $null when bash cannot be asked. #>
    try {
        $result = Invoke-NativeProcess -FilePath (Get-TeamGitBash) -Arguments @("-c", "mount") -TimeoutSeconds 30
        if ($result.ExitCode -ne 0) { return $null }
        return [string]$result.StdOut
    }
    catch { return $null }
}

function ConvertFrom-TeamGitBashMount {
    <# The Windows folder `mount` text names as /tmp, or $null when it names none. #>
    param([string]$Text)
    if (-not $Text) { return $null }
    foreach ($line in ($Text -split "`r?`n")) {
        $m = [regex]::Match($line, '^(?<source>.+?) on /tmp type ')
        if ($m.Success) { return ($m.Groups["source"].Value -replace '/', '\').TrimEnd('\') }
    }
    return $null
}

function Invoke-TeamRunTempSweep {
    <#
    .SYNOPSIS
        Remove the run folders under the run temp root that are empty, older than a day and
        not Git Bash's /tmp.

    .DESCRIPTION
        Git for Windows mounts /tmp as 'usertemp': the TEMP of the first msys process of the
        logon session, shared by every msys process until the last one exits. A run's bash can
        be that first process, so its folder may be the whole machine's /tmp long after the run
        (2026-10-06 01:50: the folder was deleted and every bash lost /tmp; mktemp failed in the
        gate). When `mount` cannot be read, nothing is removed.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [double]$MaxAgeHours = 24,
        [scriptblock]$ReadMount = { Read-TeamGitBashMount }
    )
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return }
    $text = $null
    try { $text = & $ReadMount } catch { return }
    $mounted = ConvertFrom-TeamGitBashMount -Text ([string]$text)
    if (-not $mounted) { return }
    try { $mounted = [System.IO.Path]::GetFullPath($mounted).TrimEnd('\') } catch { return }
    $cutoff = [datetime]::UtcNow.AddHours(-$MaxAgeHours)
    foreach ($folder in @(Get-ChildItem -LiteralPath $Root -Directory -Force -ErrorAction SilentlyContinue)) {
        if ($folder.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { continue }
        if ($folder.LastWriteTimeUtc -gt $cutoff) { continue }
        if ([string]::Equals($folder.FullName.TrimEnd('\'), $mounted, [System.StringComparison]::OrdinalIgnoreCase)) { continue }
        if (@(Get-ChildItem -LiteralPath $folder.FullName -Force -ErrorAction SilentlyContinue).Count -gt 0) { continue }
        try { Remove-Item -LiteralPath $folder.FullName -Force -ErrorAction Stop } catch { }
    }
}

function Restart-TeamRun {
    <#
    .SYNOPSIS
        Stop one run's process tree - that run's and no other's - and start it again, the same
        card in the same worktree (pm-stuck-run-check: a run the Proje Yöneticisi, or the 90-
        minute rule, judged stuck). Nothing in git is touched: what the run committed stays,
        and the run starts again from it. Returns the new run, in Start-TeamRun's shape, with
        ReleasedTickets.
    .PARAMETER SlotStore
        The test queue's store (scripts/lib/TeamTestSlots.ps1). A slot held by a process of this
        run's tree is given back at once, not when the next ask notices its holder is gone: on
        2026-10-04 a stuck child held the heavy slot and the release gate waited 140 minutes.
        A sibling run's slot is never touched.
    #>
    param([Parameter(Mandatory = $true)]$Run, [string]$SlotStore = "")
    $launchProperty = $Run.PSObject.Properties["Launch"]
    if ($null -eq $launchProperty -or $null -eq $launchProperty.Value) { throw "the run carries no launch record: it cannot be started again" }
    $launch = $launchProperty.Value
    $held = @()
    if ($SlotStore) {
        if (-not (Get-Command Get-TestSlotEntries -ErrorAction SilentlyContinue)) { . (Join-Path $PSScriptRoot "TeamTestSlots.ps1") }
        if (-not (Get-Command Get-TeamDescendants -ErrorAction SilentlyContinue)) { . (Join-Path $PSScriptRoot "TeamLiveness.ps1") }
        if (-not $Run.Process.HasExited) {
            $tree = @{ ([int]$Run.Process.Id) = $true }
            foreach ($row in @(Get-TeamDescendants -ProcessTable @(Get-TeamProcessTable) -RootProcessId $Run.Process.Id)) { $tree[[int]$row.Id] = $true }
            $held = @(Get-TestSlotEntries -Store $SlotStore | Where-Object { $_.state -eq "running" -and $tree.ContainsKey([int]$_.holder_pid) } | ForEach-Object { [string]$_.ticket })
        }
    }
    if (-not $Run.Process.HasExited) {
        Stop-TeamProcessTree -ProcessId $Run.Process.Id
        [void]$Run.Process.WaitForExit(15000)
    }
    try { $Run.Process.Dispose() } catch { }
    $released = New-Object System.Collections.ArrayList
    foreach ($ticket in $held) {
        if (Remove-TestSlotTicket -Store $SlotStore -Ticket $ticket) { [void]$released.Add($ticket) }
    }
    $again = Start-TeamRun @launch
    $again | Add-Member -NotePropertyName ReleasedTickets -NotePropertyValue @($released.ToArray())
    return $again
}

function Remove-TeamRunTemp {
    <#
    .SYNOPSIS
        Empty a finished run's temp folder and keep the folder itself; then sweep the run temp
        root (Invoke-TeamRunTempSweep).

    .DESCRIPTION
        The folder stays because Git Bash may have it mounted as /tmp for the whole machine
        (see Invoke-TeamRunTempSweep). Best effort: a file still held open stays and is named
        in a warning, and a folder with a link inside is left whole (a recursive delete would
        follow it). Nothing is written to the pipeline.
    #>
    [CmdletBinding()]
    param(
        [string]$Path,
        [scriptblock]$ReadMount = { Read-TeamGitBashMount }
    )
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return }
    $links = @(Get-ChildItem -LiteralPath $Path -Recurse -Force -Attributes ReparsePoint -ErrorAction SilentlyContinue)
    if (@($links).Count -gt 0) { return }
    foreach ($child in @(Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue)) {
        try { Remove-Item -LiteralPath $child.FullName -Recurse -Force -ErrorAction Stop } catch { }
    }
    $left = @(Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction SilentlyContinue)
    if (@($left).Count -gt 0) {
        $names = @($left | Select-Object -First 10 | ForEach-Object { $_.FullName })
        Write-Warning ("Remove-TeamRunTemp: {0} file(s) left in {1} (held open?): {2}" -f @($left).Count, $Path, ($names -join ", "))
    }
    try { Invoke-TeamRunTempSweep -Root (Split-Path -Parent $Path) -ReadMount $ReadMount } catch { }
}

function Stop-TeamProcessTree {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    $taskkill = Join-Path (Join-Path $env:SystemRoot "System32") "taskkill.exe"
    try {
        [void](Invoke-NativeProcess -FilePath $taskkill -Arguments @("/PID", "$ProcessId", "/T", "/F") -TimeoutSeconds 30)
    }
    catch { }
}

function Test-TeamRunOver {
    <#
    .SYNOPSIS
        Whether a run has ended, or is past its deadline. Nothing waits here: it is what a
        caller asks that keeps several runs in flight and blocks on none of them (the cycle's
        pool). Wait-TeamRun then collects such a run at once, killing it if it is still going.
    #>
    param([Parameter(Mandatory = $true)]$Run, [Parameter(Mandatory = $true)][datetime]$Deadline)
    if ($Run.Process.HasExited) { return $true }
    return ([datetime]::UtcNow -ge $Deadline.ToUniversalTime())
}

function Wait-TeamRun {
    <#
    .SYNOPSIS
        Wait for a run until its deadline. A run past its deadline is killed with its
        children, and what it printed is kept.
    #>
    param([Parameter(Mandatory = $true)]$Run, [Parameter(Mandatory = $true)][datetime]$Deadline)
    # [datetime]::MaxValue is "no deadline" (owner decision 2026-09-30: no time cap on a
    # run); WaitForExit(-1) waits for ever, and a span that large would not fit an int.
    $remainingMs = ($Deadline.ToUniversalTime() - [datetime]::UtcNow).TotalMilliseconds
    $remaining = if ($remainingMs -ge [int]::MaxValue) { -1 } else { [int][Math]::Max(0, $remainingMs) }
    $exited = $Run.Process.WaitForExit($remaining)
    $timedOut = -not $exited
    if ($timedOut) {
        Stop-TeamProcessTree -ProcessId $Run.Process.Id
        [void]$Run.Process.WaitForExit(15000)
    }
    $out = ""
    $err = ""
    if ($Run.StdOut.Wait(15000)) { $out = [string]$Run.StdOut.Result }
    if ($Run.StdErr.Wait(15000)) { $err = [string]$Run.StdErr.Result }
    $exitCode = if ($timedOut) { -1 } else { $Run.Process.ExitCode }
    $seconds = [int]([datetime]::UtcNow - $Run.Started).TotalSeconds
    $Run.Process.Dispose()
    return [pscustomobject]@{ ExitCode = $exitCode; StdOut = $out; StdErr = $err; TimedOut = $timedOut; Seconds = $seconds }
}

# ---------------------------------------------------------------------------- the report

function Format-TeamOwnerTrial {
    <#
    .SYNOPSIS
        One owner trial as one Turkish report line (ADR-0258): the object form names its
        sentence, machine, expectation and state; the old plain-string form prints as it is.
    #>
    param([Parameter(Mandatory = $true)][string]$TaskId, [Parameter(Mandatory = $true)]$Trial)
    if ($Trial -is [string]) { return "${TaskId}: $Trial" }
    $sentence = [string](Get-TeamProperty -InputObject $Trial -Name "sentence" -Default "")
    $machine = [string](Get-TeamProperty -InputObject $Trial -Name "machine" -Default "?")
    $expect = [string](Get-TeamProperty -InputObject $Trial -Name "expect" -Default "")
    $verdict = [string](Get-TeamProperty -InputObject $Trial -Name "verdict" -Default "")
    $said = [string](Get-TeamProperty -InputObject $Trial -Name "said" -Default "")
    $state = switch ($verdict) {
        "oldu" { "oldu" }
        "olmadi" { if ($said) { "olmadı ($said)" } else { "olmadı" } }
        default { "denenmedi" }
    }
    return "${TaskId}: `"$sentence`" — makine: $machine — beklenen: $expect — durum: $state"
}

function New-TeamCycleReport {
    <#
    .SYNOPSIS
        The cycle report the owner reads, in Turkish, in the shape the protocol names.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$CycleId,
        [Parameter(Mandatory = $true)]$Queue,
        [Parameter(Mandatory = $true)]$Cycle,
        # The repository whose main the orphan check reads; by default the one this file is in.
        [string]$RepoRoot = "",
        [string]$Base = "main"
    )
    if (-not $RepoRoot) { $RepoRoot = $script:TeamRunRepoRoot }
    $tasks = @(Get-TeamTasks -Queue $Queue)
    $gates = Get-TeamOwnerGates
    $lines = New-Object System.Collections.ArrayList
    [void]$lines.Add("# Döngü raporu — $CycleId")
    [void]$lines.Add("")
    [void]$lines.Add("Makine: $(Get-TeamProperty -InputObject $Cycle -Name 'machine' -Default '?') · başladı $(Get-TeamProperty -InputObject $Cycle -Name 'started_at' -Default '?') · bitti $(Get-TeamProperty -InputObject $Cycle -Name 'ended_at' -Default '?')")
    [void]$lines.Add("")

    function Add-Section {
        param([string]$Title, [object[]]$Rows)
        [void]$lines.Add("## $Title")
        [void]$lines.Add("")
        if (@($Rows).Count -eq 0) { [void]$lines.Add("Yok.") }
        else { foreach ($row in @($Rows)) { [void]$lines.Add("- $row") } }
        [void]$lines.Add("")
    }

    $ready = @($tasks | Where-Object { @("merged", "awaiting_release", "released", "done") -contains $_.state } | ForEach-Object {
            $sha = [string](Get-TeamProperty -InputObject $_ -Name "sha" -Default "")
            $reason = [string](Get-TeamProperty -InputObject $_ -Name "reason" -Default "")
            $shaText = if ($sha) { $sha } elseif ($reason -like "bölündü:*") { $reason } else { "sha yok" }
            "$($_.id) — $($_.title) [$($_.state)] ($shaText)"
        })
    Add-Section -Title "Hazır olanlar (sha)" -Rows $ready

    # Merged work no release will take: named on the day it happens, not found days later.
    $current = "integrate/$CycleId"
    $mergedBranches = @($tasks | Where-Object { [string]$_.state -eq "merged" } | ForEach-Object { ([string](Get-TeamProperty -InputObject $_ -Name "integration_branch" -Default "")).Trim() })
    $held = @($mergedBranches | Where-Object { $_ -and $_ -ne $current })
    $unbranched = @($mergedBranches | Where-Object { -not $_ })
    if ((@($held).Count -gt 0 -or @($unbranched).Count -gt 0) -and $RepoRoot) {
        $inMain = @(Get-TeamBranchesInMain -RepoRoot $RepoRoot -Branches $held -Base $Base)
        $heldIds = @(Get-TeamUnbranchedHeld -RepoRoot $RepoRoot -Queue $Queue -Current $current -Base $Base)
        $orphans = @(Get-TeamOrphanMerges -Queue $Queue -Current $current -InMain $inMain -HeldIds $heldIds | ForEach-Object {
                $branch = ([string](Get-TeamProperty -InputObject $_ -Name 'integration_branch' -Default '')).Trim()
                if ($branch) { "$($_.id) — $($_.title): $branch dalında birleşmiş, $Base'de değil ve bu döngünün dalı değil - yayına ulaşmaz" }
                else {
                    $sha = ([string](Get-TeamProperty -InputObject $_ -Name 'sha' -Default '')).Trim()
                    $taskBranch = ([string](Get-TeamProperty -InputObject $_ -Name 'branch' -Default '')).Trim()
                    $shaText = if ($sha) { $sha } elseif ($taskBranch) { $taskBranch } else { "sha yok" }
                    "$($_.id) — $($_.title): birleşmiş ama entegrasyon dalı yok, işi ($shaText) $Base'de değil - yayına ulaşmaz"
                }
            })
        if (@($orphans).Count -gt 0) { Add-Section -Title "Yetim entegrasyon (yayına ulaşmayan birleşmiş işler)" -Rows $orphans }
    }

    $ideas = @($tasks | Where-Object { $_.state -eq "awaiting_owner" } | ForEach-Object {
            $proposal = [string](Get-TeamProperty -InputObject $_ -Name "proposal" -Default "")
            $where = if ($proposal) { " — $proposal" } else { "" }
            "FİKİR: $($_.id) — $($_.title)$where"
        })
    $releases = @($tasks | Where-Object { $_.state -eq "awaiting_release" } | ForEach-Object {
            # ADR-0217: the owner's release approval is a flag on the task, never a state
            # the cycle would read as "assign a worker". The release itself is the lead's.
            if ([bool](Get-TeamProperty -InputObject $_ -Name "release_approved" -Default $false)) {
                "YAYIN ONAYLANDI ($([string](Get-TeamProperty -InputObject $_ -Name 'release_approved_at' -Default '?'))): $($_.id) — $($_.title) — lead yayınlar"
            }
            else { "YAYIN: $($_.id) — $($_.title)" }
        })
    Add-Section -Title "Onay bekleyenler (fikir / yayın)" -Rows @($ideas + $releases)

    $real = @($tasks | Where-Object { $_.state -eq "awaiting_real_evidence" } | ForEach-Object {
            $rows = @(Get-TeamProperty -InputObject $_ -Name "owner_trials" -Default @())
            if (@($rows).Count -eq 0) { "$($_.id) — $($_.title): deneme cümlesi yazılmamış" }
            else { foreach ($trial in $rows) { Format-TeamOwnerTrial -TaskId $_.id -Trial $trial } }
        })
    Add-Section -Title "Sahibin gerçek cihazda deneyecekleri (cümle cümle, hangi makinede)" -Rows $real

    $returned = @($tasks | Where-Object { $_.state -eq "returned" } | ForEach-Object {
            "$($_.id) — $($_.title): $([string](Get-TeamProperty -InputObject $_ -Name 'reason' -Default 'denetleyici geri verdi'))"
        })
    Add-Section -Title "Geri verilenler ve nedeni" -Rows $returned

    $stopped = @($tasks | Where-Object { $_.state -eq "stopped" } | ForEach-Object {
            "$($_.id) — $($_.title): $([string](Get-TeamProperty -InputObject $_ -Name 'reason' -Default 'neden yazılmamış'))"
        })
    $stops = @(Get-TeamProperty -InputObject $Cycle -Name "stops" -Default @())
    Add-Section -Title "Durdurulanlar" -Rows @($stopped + @($stops | ForEach-Object { "döngü: $_" }))

    $spent = [double](Get-TeamProperty -InputObject $Cycle -Name "spent_usd" -Default 0)
    $cap = [double](Get-TeamProperty -InputObject $Cycle -Name "max_usd" -Default 0)
    $runs = @(Get-TeamProperty -InputObject $Cycle -Name "runs" -Default @())
    # The USD is the tool's own estimate, kept as information (abonelik, API değil - owner,
    # 2026-09-30); a cap is named only when the cycle was given one.
    $capText = if ($cap -gt 0) { (" / tavan {0:0.00} USD" -f $cap) } else { " (tavan yok)" }
    $budget = @(
        ("tahmini {0:0.00} USD{1}" -f $spent, $capText),
        "koşu sayısı: $(@($runs).Count); çakışma: $([int](Get-TeamProperty -InputObject $Cycle -Name 'conflicts' -Default 0)); geri verilen: $([int](Get-TeamProperty -InputObject $Cycle -Name 'returned' -Default 0))"
    )
    foreach ($run in $runs) {
        $line = ("{0} / {1}: {2:0.00} USD, {3} sn, {4}" -f $run.task, $run.role, [double]$run.cost_usd, [int]$run.seconds, $run.outcome)
        $ranOn = [string](Get-TeamProperty -InputObject $run -Name "model" -Default "")
        if ($ranOn) { $line += ", model $ranOn" }
        # The model policy (ADR-0214 addendum 7): a run the cycle started below the model its
        # role is set to says so on its own line; so does one the TOOL ran on another model.
        $from = [string](Get-TeamProperty -InputObject $run -Name "lowered_from" -Default "")
        if ($from) { $line += ", model düşürüldü: $from -> $ranOn" }
        $really = [string](Get-TeamProperty -InputObject $run -Name "ran_model" -Default "")
        if ([bool](Get-TeamProperty -InputObject $run -Name "substituted" -Default $false)) {
            $line += ", model düşürüldü (araç): $ranOn -> $(if ($really) { $really } else { '?' })"
        }
        $budget += $line
    }
    Add-Section -Title "Harcanan bütçe" -Rows $budget
    Add-Section -Title "Açık riskler" -Rows @(Get-TeamProperty -InputObject $Cycle -Name "risks" -Default @())
    Add-Section -Title "Protokol boşlukları" -Rows @(Get-TeamProperty -InputObject $Cycle -Name "gaps" -Default @())

    $waiting = @($tasks | Where-Object { $gates.ContainsKey([string]$_.state) })
    if (@($waiting).Count -gt 0) {
        [void]$lines.Add("Onay vermek için (Onay Merkezi hazır olana kadar): ``team/queue.json`` içinde ilgili işin ``state`` alanını ``approved`` yapın ve kaydedin; bir sonraki döngü bunu okur.")
        [void]$lines.Add("")
    }
    return (($lines.ToArray()) -join "`n")
}

function Measure-TeamAreaProgress {
    <#
    .SYNOPSIS
        How far a run's change has reached into its card's area (the owner, 2026-10-05: "kodun
        yuzde kacini yazdigi"). Measured from the files, never from what the run says.

    .DESCRIPTION
        An area entry ending in "/" is a folder and counts ONCE, touched when any changed path
        is inside it; any other entry is touched when that exact path changed. Paths compare
        with "/" and case-insensitively (Windows). Pure: the caller hands in the changed paths.
    #>
    param([string[]]$Area = @(), [string[]]$Changed = @())
    $norm = { param($p) (([string]$p) -replace '\\', '/').Trim().TrimStart('.', '/').ToLowerInvariant() }
    $changedSet = @(@($Changed) | Where-Object { $_ } | ForEach-Object { & $norm $_ })
    $entries = @(@($Area) | Where-Object { $_ } | ForEach-Object { & $norm $_ } | Select-Object -Unique)
    $touched = 0
    foreach ($entry in $entries) {
        if ($entry.EndsWith("/")) {
            if (@($changedSet | Where-Object { $_.StartsWith($entry) }).Count -gt 0) { $touched++ }
        }
        elseif ($changedSet -contains $entry) { $touched++ }
    }
    $tests = @($changedSet | Where-Object { $_ -match '(^|/)tests?/' -or $_ -match '\.tests\.ps1$' -or $_ -match '\.test\.tsx?$' }).Count -gt 0
    return [pscustomobject]@{ AreaTotal = @($entries).Count; AreaTouched = $touched; TestsChanged = $tests }
}

function Get-TeamRunProgress {
    <#
    .SYNOPSIS
        The live run's progress for the status document, or $null when its worktree cannot be
        read. Cheap: three git calls, no model. Never throws (a status write must not fail on it).
    #>
    param([string]$Worktree, [string]$Base, [string[]]$Area = @(), [string]$TaskId = "")
    try {
        if (-not $Worktree -or -not (Test-Path -LiteralPath (Join-Path $Worktree ".git"))) { return $null }
        $changed = New-Object System.Collections.ArrayList
        $commits = 0
        if ($Base) {
            $diff = Invoke-TeamGit -WorkingDirectory $Worktree -Arguments @("diff", "--name-only", "$Base...HEAD") -TimeoutSeconds 30
            if ($diff.Success) { foreach ($l in @(([string]$diff.StdOut) -split "`r?`n")) { if ($l.Trim()) { [void]$changed.Add($l.Trim()) } } }
            $count = Invoke-TeamGit -WorkingDirectory $Worktree -Arguments @("rev-list", "--count", "$Base..HEAD") -TimeoutSeconds 30
            if ($count.Success) { [void][int]::TryParse(([string]$count.StdOut).Trim(), [ref]$commits) }
        }
        $status = Invoke-TeamGit -WorkingDirectory $Worktree -Arguments @("status", "--porcelain", "--untracked-files=all") -TimeoutSeconds 30
        $newest = $null
        if ($status.Success) {
            foreach ($l in @(([string]$status.StdOut) -split "`r?`n")) {
                if ($l.Length -lt 4) { continue }
                $path = $l.Substring(3).Trim('"')
                if ($path.Contains(" -> ")) { $path = $path.Substring($path.IndexOf(" -> ") + 4) }
                [void]$changed.Add($path)
                $full = Join-Path $Worktree $path
                if (Test-Path -LiteralPath $full -PathType Leaf) {
                    $t = (Get-Item -LiteralPath $full).LastWriteTimeUtc
                    if ($null -eq $newest -or $t -gt $newest) { $newest = $t }
                }
            }
        }
        $last = Invoke-TeamGit -WorkingDirectory $Worktree -Arguments @("log", "-1", "--format=%cI") -TimeoutSeconds 30
        if ($last.Success -and ([string]$last.StdOut).Trim()) {
            $t = ([datetime]::Parse(([string]$last.StdOut).Trim())).ToUniversalTime()
            if ($null -eq $newest -or $t -gt $newest) { $newest = $t }
        }
        $m = Measure-TeamAreaProgress -Area $Area -Changed @($changed.ToArray())
        $adr = $false
        if ($TaskId) { $adr = Test-Path -LiteralPath (Join-Path $Worktree "team\plans\$TaskId-adr.md") }
        $doc = [ordered]@{
            area_total     = [Math]::Min(500, [int]$m.AreaTotal)
            area_touched   = [Math]::Min(500, [int]$m.AreaTouched)
            tests_changed  = [bool]$m.TestsChanged
            adr_draft      = [bool]$adr
            commits        = [Math]::Min(10000, [int]$commits)
            last_change_at = $(if ($null -ne $newest) { $newest.ToString("yyyy-MM-ddTHH:mm:ssZ", [System.Globalization.CultureInfo]::InvariantCulture) } else { $null })
        }
        return $doc
    }
    catch { return $null }
}
