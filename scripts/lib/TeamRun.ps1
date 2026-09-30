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

function New-TeamWorktree {
    <#
    .SYNOPSIS
        A branch and its worktree, from a base. Both are left alone when they exist.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$Branch,
        [string]$Base = "main"
    )
    $path = Get-TeamWorktreePath -RepoRoot $RepoRoot -Branch $Branch
    $hasBranch = Test-TeamBranch -RepoRoot $RepoRoot -Branch $Branch
    if (Test-Path -LiteralPath (Join-Path $path ".git")) {
        return [pscustomobject]@{ Path = $path; Branch = $Branch; Created = $false; Note = "the worktree is already there" }
    }
    $parent = Split-Path -Parent $path
    if (-not (Test-Path -LiteralPath $parent)) { [void](New-Item -ItemType Directory -Force -Path $parent) }
    if ($hasBranch) {
        $result = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("worktree", "add", $path, $Branch)
    }
    else {
        $result = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("worktree", "add", "-b", $Branch, $path, $Base)
    }
    if (-not $result.Success) {
        throw "git worktree add failed for ${Branch}: $($result.StdErr.Trim())"
    }
    return [pscustomobject]@{ Path = $path; Branch = $Branch; Created = $true; Note = "" }
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
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$CycleId,
        [Parameter(Mandatory = $true)][string]$Branch,
        [string]$Base = "main"
    )
    $integration = "integrate/$CycleId"
    $tree = New-TeamWorktree -RepoRoot $RepoRoot -Branch $integration -Base $Base
    $ancestor = Invoke-TeamGit -WorkingDirectory $tree.Path -Arguments @("merge-base", "--is-ancestor", $Branch, "HEAD")
    if ($ancestor.ExitCode -eq 0) {
        return [pscustomobject]@{ Merged = $true; Already = $true; Conflict = $false; Integration = $integration; Detail = "" }
    }
    $message = "merge: $Branch into $integration"
    $merge = Invoke-TeamGit -WorkingDirectory $tree.Path -Arguments @("merge", "--no-ff", "-m", $message, $Branch)
    if ($merge.Success) {
        return [pscustomobject]@{ Merged = $true; Already = $false; Conflict = $false; Integration = $integration; Detail = "" }
    }
    [void](Invoke-TeamGit -WorkingDirectory $tree.Path -Arguments @("merge", "--abort"))
    $detail = ($merge.StdOut + "`n" + $merge.StdErr).Trim()
    return [pscustomobject]@{ Merged = $false; Already = $false; Conflict = $true; Integration = $integration; Detail = $detail }
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
    if ($taken.Count -eq 0) { [void]$lines.Add("- none") } else { foreach ($row in $taken) { [void]$lines.Add([string]$row) } }
    [void]$lines.Add("")
    [void]$lines.Add("Return your report as your final message, at most 40 lines: which tasks, and why that split.")
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
    foreach ($argument in @(
            "-p", "--output-format", "json", "--no-session-persistence",
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
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
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
    }
}

function Stop-TeamProcessTree {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    $taskkill = Join-Path (Join-Path $env:SystemRoot "System32") "taskkill.exe"
    try {
        [void](Invoke-NativeProcess -FilePath $taskkill -Arguments @("/PID", "$ProcessId", "/T", "/F") -TimeoutSeconds 30)
    }
    catch { }
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
    $timedOut = -not $Run.Process.WaitForExit($remaining)
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

function New-TeamCycleReport {
    <#
    .SYNOPSIS
        The cycle report the owner reads, in Turkish, in the shape the protocol names.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$CycleId,
        [Parameter(Mandatory = $true)]$Queue,
        [Parameter(Mandatory = $true)]$Cycle
    )
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
            else { foreach ($trial in $rows) { "$($_.id): $trial" } }
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
        $budget += ("{0} / {1}: {2:0.00} USD, {3} sn, {4}" -f $run.task, $run.role, [double]$run.cost_usd, [int]$run.seconds, $run.outcome)
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
