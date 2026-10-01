<#
.SYNOPSIS
    What the integration step decides and does (scripts/team/integrate.ps1): which merged tasks
    wait for the gate, what the lead's wiring run may touch, what a gate log says, who a red gate
    names, and how a branch is moved forward without forcing anything.

.DESCRIPTION
    Dot-sourced after `NativeProcess.ps1`, `TeamQueue.ps1` and `TeamRun.ps1`. The first half is
    decisions: functions that take their inputs and return their answer, so the tests drive them
    without a repository, a model or a gate. The second half acts, through git only:

      * nothing here names a release script, a tag or the last-known-good record;
      * a branch is only ever moved FORWARD: by `git merge --ff-only` in the worktree that has
        it checked out (git itself refuses when local changes would be overwritten), or by a
        compare-and-swap `git update-ref` when no worktree has it. Nothing is reset, forced or
        checked out in a worktree this step did not make;
      * the gate worktree (.claude/worktrees/gate/<branch>) is this step's own scratch tree, on
        a detached HEAD: it is the one place that is reset.

    Windows PowerShell 5.1, StrictMode.
#>

Set-StrictMode -Version Latest

# What the lead's wiring run may change without a report naming it (TEAM_PROTOCOL section 4:
# the shared files are the lead's). Everything else must be named, as a path, under a report's
# "For the lead at merge".
$script:TeamLeadOpenPrefixes = @("docs/", ".github/", "team/")
$script:TeamLeadOpenFiles = @("scripts/quality-gate.ps1", "state/build_state.json")
# The queue and the lock are the cycle's, never a run's: a committed lock that is held stops
# every cycle for six hours.
$script:TeamLeadClosedFiles = @("team/queue.json", "team/lock.json")
# The results of an attempt that count towards "two red gates on the same branch" (section 10).
$script:TeamGateStrikeResults = @("red", "lead_refused", "lead_failed")
$script:TeamGateMaxStrikes = 2
$script:TeamReasonMaxLength = 900

# ---------------------------------------------------------------------------- names

function Get-TeamIntegrationCycleId {
    <# "integrate/<cycle-id>" -> "<cycle-id>"; anything else is not an integration branch. #>
    param([Parameter(Mandatory = $true)][string]$Branch)
    if ($Branch -cnotmatch '^integrate/([a-z0-9][a-z0-9.-]{0,40})$' -or $Branch -match '\.\.') {
        throw "'$Branch' is not an integration branch"
    }
    return $Matches[1]
}

function Get-TeamGateWorktreePath {
    <# Where a branch is gated: a worktree of its own, never the main checkout. #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [Parameter(Mandatory = $true)][string]$Branch)
    [void](Get-TeamIntegrationCycleId -Branch $Branch)
    return (Join-Path (Join-Path $RepoRoot ".claude\worktrees\gate") ($Branch -replace '/', '\'))
}

function Get-TeamMergedGroups {
    <#
    .SYNOPSIS
        The tasks in state 'merged', by the integration branch that holds them. A merged task
        that names no integration branch is nobody's to gate and is left out.
    #>
    param($Queue)
    $byBranch = [ordered]@{}
    foreach ($task in (Get-TeamTasks -Queue $Queue)) {
        if ([string](Get-TeamProperty -InputObject $task -Name "state" -Default "") -ne "merged") { continue }
        $branch = [string](Get-TeamProperty -InputObject $task -Name "integration_branch" -Default "")
        if (-not $branch.Trim()) { continue }
        if (-not $byBranch.Contains($branch)) { $byBranch[$branch] = New-Object System.Collections.ArrayList }
        [void]$byBranch[$branch].Add($task)
    }
    $groups = New-Object System.Collections.ArrayList
    foreach ($branch in @($byBranch.Keys | Sort-Object)) {
        [void]$groups.Add([pscustomobject]@{ Branch = [string]$branch; Tasks = @($byBranch[$branch].ToArray()) })
    }
    return @($groups.ToArray())
}

# ---------------------------------------------------------------------------- the lead's wiring run

function Get-TeamLeadMergeSection {
    <#
    .SYNOPSIS
        The "For the lead at merge" part of a report, as text; empty when the report has none.

    .DESCRIPTION
        The phrase must BEGIN a line (after a heading mark, a bullet or bold marks): a sentence
        that merely mentions it is not the section. Under a markdown heading the section runs to
        the next heading; under a label ("For the lead at merge: ...") it runs over the lines
        that follow until a blank line, a heading or the next label - what a report says after
        it (its open risks) names no file for the lead.
    #>
    param([string]$Text)
    $kept = New-Object System.Collections.ArrayList
    $inside = $false
    $underHeading = $false
    $content = 0
    foreach ($line in @(([string]$Text) -split "`r?`n")) {
        if ($line -match '(?i)^\s*(#{1,6}\s*)?(?:[-*]\s+)?(?:\*\*|__)?\s*For the lead at merge(.*)$') {
            $inside = $true
            $underHeading = [bool]$Matches[1]
            $content = 0
            $rest = ([string]$Matches[2]).Trim().Trim('*', '_', ':', ' ').Trim()
            if ($rest) { [void]$kept.Add($rest); $content++ }
            continue
        }
        if (-not $inside) { continue }
        if ($line -match '^\s*#{1,6}\s') { $inside = $false; continue }
        if (-not $underHeading) {
            if (-not $line.Trim()) {
                if ($content -gt 0) { $inside = $false }
                continue
            }
            $isBullet = ($line -match '^\s*([-*]|\d+[.)])\s')
            if (-not $isBullet -and $line -match '^\s*(\*\*|__)?[^\s:*_][^:]{1,60}:(\*\*|__)?(\s|$)') { $inside = $false; continue }
        }
        [void]$kept.Add($line.TrimEnd())
        if ($line.Trim()) { $content++ }
    }
    return ((($kept.ToArray()) -join "`n").Trim())
}

function Get-TeamLeadNamedFiles {
    <#
    .SYNOPSIS
        The FILES a "For the lead at merge" text names: tokens with at least one directory and an
        extension, forward slashes, lower case. A bare file name ("main.py") names nothing: it
        would open every file of that name.
    #>
    param([string]$Text)
    $found = New-Object System.Collections.ArrayList
    foreach ($match in [regex]::Matches([string]$Text, '(?<![A-Za-z0-9_.\-/\\:])\.?[A-Za-z0-9_\-][A-Za-z0-9_.\-]*(?:[/\\][A-Za-z0-9_.\-]+)+')) {
        $token = ($match.Value -replace '\\', '/').TrimEnd('.').ToLowerInvariant()
        while ($token.StartsWith("./")) { $token = $token.Substring(2) }
        if ($token -match '\.\.' -or -not $token) { continue }
        $last = $token.Substring($token.LastIndexOf('/') + 1)
        if ($last -notmatch '^[^.].*\.[a-z0-9]{1,8}$' -and $last -notmatch '^\.[a-z0-9]+\.[a-z0-9]{1,8}$') { continue }
        if ($found -notcontains $token) { [void]$found.Add($token) }
    }
    return @($found.ToArray())
}

function Test-TeamLeadFileAllowed {
    <#
    .SYNOPSIS
        Whether the lead's wiring run may have changed this file: a shared place of the lead's
        (docs/, .github/, team/, the gate's list, BUILD_STATE), or a file a report NAMED.
    #>
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Path, [string[]]$NamedFiles = @())
    $file = ($Path -replace '\\', '/').Trim().TrimStart("/").ToLowerInvariant()
    while ($file.StartsWith("./")) { $file = $file.Substring(2) }
    if (-not $file -or $file -match '(^|/)\.\.(/|$)') { return $false }
    if ($script:TeamLeadClosedFiles -contains $file) { return $false }
    foreach ($prefix in $script:TeamLeadOpenPrefixes) { if ($file.StartsWith($prefix)) { return $true } }
    if ($script:TeamLeadOpenFiles -contains $file) { return $true }
    foreach ($named in @($NamedFiles)) {
        $name = ([string]$named -replace '\\', '/').Trim().TrimStart("/").ToLowerInvariant()
        if (-not $name -or $name -notmatch '/') { continue }
        if ($file -eq $name -or $file.EndsWith("/" + $name)) { return $true }
    }
    return $false
}

function Get-TeamLeadRefusedFiles {
    <# The files of a lead run's diff that it had no right to change. Empty when it kept the rule. #>
    param([string[]]$Changed = @(), [string[]]$NamedFiles = @())
    return @(@($Changed) | Where-Object { ([string]$_).Trim() } |
            Where-Object { -not (Test-TeamLeadFileAllowed -Path ([string]$_) -NamedFiles $NamedFiles) })
}

function Get-TeamNewestReportText {
    <#
    .SYNOPSIS
        A task's newest report by one role: the report file when it is there, else the forty
        lines the queue kept. Empty when that role never reported.
    #>
    param([Parameter(Mandatory = $true)]$Task, [Parameter(Mandatory = $true)][string]$Role, [Parameter(Mandatory = $true)][string]$RepoRoot)
    $ofRole = @(@(Get-TeamProperty -InputObject $Task -Name "reports" -Default @()) |
            Where-Object { [string](Get-TeamProperty -InputObject $_ -Name "role" -Default "") -eq $Role })
    if (@($ofRole).Count -eq 0) { return "" }
    $newest = $ofRole[@($ofRole).Count - 1]
    $file = [string](Get-TeamProperty -InputObject $newest -Name "file" -Default "")
    if ($file -and $file -notmatch '\.\.' -and $file -match '\.md$') {
        $path = Join-Path $RepoRoot ($file -replace '/', '\')
        if (Test-Path -LiteralPath $path) { return [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8) }
    }
    return ((@(Get-TeamProperty -InputObject $newest -Name "summary" -Default @()) | ForEach-Object { [string]$_ }) -join "`n")
}

function Get-TeamLeadMergeNotes {
    <# Per merged task: what its newest worker and inspector reports leave for the lead. #>
    param([object[]]$Tasks = @(), [Parameter(Mandatory = $true)][string]$RepoRoot)
    $notes = New-Object System.Collections.ArrayList
    foreach ($task in @($Tasks)) {
        $worker = Get-TeamLeadMergeSection -Text (Get-TeamNewestReportText -Task $task -Role "worker" -RepoRoot $RepoRoot)
        $inspector = Get-TeamLeadMergeSection -Text (Get-TeamNewestReportText -Task $task -Role "inspector" -RepoRoot $RepoRoot)
        [void]$notes.Add([pscustomobject]@{
                Id = [string]$task.id; Title = [string](Get-TeamProperty -InputObject $task -Name "title" -Default "")
                Area = @(Get-TeamProperty -InputObject $task -Name "area" -Default @()); Worker = $worker; Inspector = $inspector
                Named = @(Get-TeamLeadNamedFiles -Text ($worker + "`n" + $inspector))
            })
    }
    return @($notes.ToArray())
}

function New-TeamLeadMergeCard {
    <#
    .SYNOPSIS
        The prompt of the lead's wiring run: the merged tasks, what each report left for the
        lead, what the run may touch. The script - not the run - checks the diff and commits.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$CycleId,
        [Parameter(Mandatory = $true)][string]$Branch,
        [object[]]$Notes = @()
    )
    $lines = New-Object System.Collections.ArrayList
    [void]$lines.Add("# Merge wiring run (lead, cycle $CycleId)")
    [void]$lines.Add("")
    [void]$lines.Add("This run does ONE thing: wire the shared files for the tasks below, which are merged into $Branch")
    [void]$lines.Add("and wait for the gate. You are in a worktree of that branch with main merged in. Work only here.")
    [void]$lines.Add("")
    [void]$lines.Add("What the workers may not touch is yours: number each team/plans/<task>-adr.md into docs/DECISIONS.md,")
    [void]$lines.Add("docs/THIRD_PARTY_COMPONENTS.md, the gate's and ci's suite lists (scripts/quality-gate.ps1, .github/),")
    [void]$lines.Add("state/BUILD_STATE.json, and each wiring line a report names below (a router mount, a ledger word).")
    [void]$lines.Add("")
    [void]$lines.Add("You may change ONLY: files under docs/, .github/ and team/ (never team/queue.json or team/lock.json),")
    [void]$lines.Add("scripts/quality-gate.ps1, state/BUILD_STATE.json, and the files named, as paths, in the sections below.")
    [void]$lines.Add("The script checks the diff: one file outside that list refuses the WHOLE run and nothing is merged.")
    [void]$lines.Add("Do not commit, push, tag, switch branch, run the gate or release: the script commits what you")
    [void]$lines.Add("changed and runs the gate. If nothing needs wiring, change nothing and say so.")
    foreach ($note in @($Notes)) {
        [void]$lines.Add("")
        [void]$lines.Add("## $($note.Id) - $($note.Title)")
        [void]$lines.Add("- area: " + ((@($note.Area) | ForEach-Object { [string]$_ }) -join ", "))
        [void]$lines.Add("")
        [void]$lines.Add("### For the lead at merge (worker's newest report)")
        [void]$lines.Add($(if ($note.Worker) { [string]$note.Worker } else { "nothing named" }))
        [void]$lines.Add("")
        [void]$lines.Add("### For the lead at merge (inspector's newest report)")
        [void]$lines.Add($(if ($note.Inspector) { [string]$note.Inspector } else { "nothing named" }))
    }
    [void]$lines.Add("")
    [void]$lines.Add("Return your report as your final message, at most 40 lines: each file you changed and why.")
    return (($lines.ToArray()) -join "`n")
}

function Get-TeamRoleModel {
    <# The model a role runs on: team/models.json {"roles": {...}}, else the fallback (ADR-0214 addendum 7). #>
    param([Parameter(Mandatory = $true)][string]$TeamRoot, [Parameter(Mandatory = $true)][string]$Role, [string]$Fallback = "")
    $path = Join-Path $TeamRoot "models.json"
    if (-not (Test-Path -LiteralPath $path)) { return $Fallback }
    $roles = Get-TeamProperty -InputObject (Read-TeamJson -Path $path) -Name "roles"
    $name = [string](Get-TeamProperty -InputObject $roles -Name $Role -Default "")
    if (-not $name) { return $Fallback }
    # The value goes onto a command line.
    if ($name -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') { throw "team/models.json: '$name' is not a model name (role $Role)" }
    return $name
}

# ---------------------------------------------------------------------------- the gate's words

function Read-TeamGateLog {
    <#
    .SYNOPSIS
        What a run of scripts/quality-gate.ps1 said: green or not, which steps failed, the first
        failing test, and the text of the failing steps.

    .DESCRIPTION
        GREEN needs BOTH the exit code 0 and the gate's own last word, "QUALITY GATE: PASS": a
        script that printed PASS and died, and a script that exited 0 without being the gate,
        are not green. A step failed when its section holds the "FAILED: " line the gate's
        Invoke-Step writes (the summary table is read too, but it is cut at the console's width).
    #>
    param([string]$Text, [int]$ExitCode = 0, [bool]$TimedOut = $false)
    $lines = @(([string]$Text).TrimStart([char]0xFEFF) -split "`r?`n")
    $order = New-Object System.Collections.ArrayList
    $sections = @{}
    $current = ""
    $inSummary = $false
    $saidPass = $false
    $saidFail = $false
    $failed = New-Object System.Collections.ArrayList
    foreach ($line in $lines) {
        if ($line -match '^=== (.+) ===\s*$') {
            $current = $Matches[1].Trim()
            $inSummary = ($current -eq "Quality gate summary")
            if (-not $inSummary -and -not $sections.ContainsKey($current)) {
                $sections[$current] = New-Object System.Collections.ArrayList
                [void]$order.Add($current)
            }
            continue
        }
        if ($line -match '^QUALITY GATE: PASS\s*$') { $saidPass = $true; continue }
        if ($line -match '^QUALITY GATE: FAIL\s*$') { $saidFail = $true; continue }
        if ($inSummary) {
            if ($line -match '^(.+?)\s+FAIL\s+[\d.,]+\s*$') {
                $name = $Matches[1].Trim()
                if ($failed -notcontains $name) { [void]$failed.Add($name) }
            }
            continue
        }
        if ($current) {
            [void]$sections[$current].Add($line)
            if ($line -match '^FAILED: ' -and $failed -notcontains $current) { [void]$failed.Add($current) }
        }
    }
    $green = ($ExitCode -eq 0) -and $saidPass -and (-not $saidFail) -and (-not $TimedOut) -and (@($failed).Count -eq 0)
    # In the gate's own order, not the order they were noticed in.
    $steps = @($order | Where-Object { $failed -contains $_ })
    foreach ($name in $failed) { if ($steps -notcontains $name) { $steps += $name } }

    $first = ""
    $failing = New-Object System.Collections.ArrayList
    foreach ($step in $steps) {
        if (-not $sections.ContainsKey($step)) { continue }
        foreach ($line in $sections[$step]) {
            [void]$failing.Add([string]$line)
            if ($first) { continue }
            if ($line -match '^\s*FAILED\s+(\S+::\S+)') { $first = $Matches[1] }                       # pytest
            elseif ($line -match '^\s*FAIL\s{2,}(\S.*)$') { $first = $Matches[1].Trim() }              # the PowerShell suites
            elseif ($line -match '^\s*(?:FAIL|×|✗)\s+(\S.*)$') { $first = $Matches[1].Trim() } # vitest
            elseif ($line -match '^\s*(?:Failed|Ba\S{1,2}ar\S{1,2}s\S{1,2}z)\s+(\S+)\s+\[') { $first = $Matches[1] } # dotnet test, English or Turkish
        }
        if (-not $first) {
            $said = @($sections[$step] | Where-Object { $_ -match '^FAILED: ' })
            if (@($said).Count -gt 0) { $first = ([string]$said[0]).Substring(8).Trim() }
        }
    }
    $why = ""
    if (-not $green) {
        if ($TimedOut) { $why = "kapı süresinde bitmedi" }
        elseif (@($steps).Count -gt 0) { $why = "kırılan adımlar: " + ($steps -join "; ") }
        elseif ($saidPass) { $why = "kapı PASS dedi ama çıkış kodu $ExitCode" }
        elseif ($saidFail) { $why = "kapı FAIL dedi, adım adı okunamadı (çıkış kodu $ExitCode)" }
        else { $why = "kapı son sözünü söylemedi (çıkış kodu $ExitCode)" }
    }
    # With no step to point at, the whole log is what the failure "names".
    $failureText = if (@($failing).Count -gt 0) { (($failing.ToArray()) -join "`n") } elseif (-not $green) { ($lines -join "`n") } else { "" }
    return [pscustomobject]@{
        Green = $green; FailedSteps = @($steps); FirstFailure = $first; FailureText = $failureText; Why = $why
    }
}

function Get-TeamGateBlamedTasks {
    <#
    .SYNOPSIS
        The ids of the tasks a failure NAMES: the failing steps' text holds the path of a file
        the task changed - the whole path, or its last directories ("tests/unit/test_x.py" is
        how pytest prints services/api/tests/unit/test_x.py).

    .DESCRIPTION
        -TaskFiles is id -> the repository-relative files the task's branch changed. A bare file
        name is not a match: "README.md" in a log names nobody.
    #>
    param([string]$FailureText, [Parameter(Mandatory = $true)][hashtable]$TaskFiles)
    $text = (([string]$FailureText) -replace '\\', '/').ToLowerInvariant()
    $blamed = New-Object System.Collections.ArrayList
    if (-not $text.Trim()) { return @() }
    foreach ($id in @($TaskFiles.Keys | Sort-Object)) {
        $hit = $false
        foreach ($file in @($TaskFiles[$id])) {
            $path = (([string]$file) -replace '\\', '/').Trim().TrimStart("/").ToLowerInvariant()
            if (-not $path) { continue }
            $parts = @($path.Split("/"))
            for ($start = 0; $start -le (@($parts).Count - 2); $start++) {
                $candidate = ($parts[$start..(@($parts).Count - 1)] -join "/")
                $pattern = '(?<![a-z0-9_.\-])' + [regex]::Escape($candidate) + '(?![a-z0-9_/\-])'
                if ([regex]::IsMatch($text, $pattern)) { $hit = $true; break }
            }
            if ($hit) { break }
        }
        if ($hit) { [void]$blamed.Add([string]$id) }
    }
    return @($blamed.ToArray())
}

function Get-TeamGateReason {
    <# The gate's words as they go into a task's reason (and so into a returned worker's card). #>
    param([Parameter(Mandatory = $true)]$Gate, [Parameter(Mandatory = $true)][string]$LogFile, [int]$Attempt = 0)
    $text = "kapı kırmızı"
    if ($Attempt -gt 0) { $text += " ($Attempt. deneme)" }
    $text += ": " + [string]$Gate.Why
    if ($Gate.FirstFailure) { $text += "; ilk kırılan: " + [string]$Gate.FirstFailure }
    $text += "; kayıt: $LogFile"
    $text = ($text -replace '\s+', ' ').Trim()
    if ($text.Length -gt $script:TeamReasonMaxLength) { $text = $text.Substring(0, $script:TeamReasonMaxLength) }
    return $text
}

# ---------------------------------------------------------------------------- the attempts on a branch

function Get-TeamGateRecords {
    <# The attempts recorded for one branch (team/reports/<cycle>/gate-<n>.json), oldest first. #>
    param([Parameter(Mandatory = $true)][string]$Directory, [Parameter(Mandatory = $true)][string]$Branch)
    if (-not (Test-Path -LiteralPath $Directory)) { return @() }
    $records = New-Object System.Collections.ArrayList
    foreach ($file in @(Get-ChildItem -LiteralPath $Directory -Filter "gate-*.json" -File)) {
        if ($file.Name -notmatch '^gate-(\d+)\.json$') { continue }
        try { $record = Read-TeamJson -Path $file.FullName } catch { continue }
        if ([string](Get-TeamProperty -InputObject $record -Name "branch" -Default "") -ne $Branch) { continue }
        [void]$records.Add($record)
    }
    return @($records.ToArray() | Sort-Object -Property @{ Expression = { [int](Get-TeamProperty -InputObject $_ -Name "n" -Default 0) } })
}

function Get-TeamGateNextNumber {
    <# The next attempt number in a cycle's report folder: one past the highest gate-<n>.* there. #>
    param([Parameter(Mandatory = $true)][string]$Directory)
    $highest = 0
    if (Test-Path -LiteralPath $Directory) {
        foreach ($file in @(Get-ChildItem -LiteralPath $Directory -Filter "gate-*" -File)) {
            if ($file.Name -match '^gate-(\d+)[.-]') { $highest = [Math]::Max($highest, [int]$Matches[1]) }
        }
    }
    return ($highest + 1)
}

function Get-TeamGateStrikes {
    <#
    .SYNOPSIS
        How many attempts in a row failed on this branch. A green gate, or the lead's "I looked"
        (result 'cleared'), starts the count again. Two stop the branch (TEAM_PROTOCOL 10).
    #>
    param([object[]]$Records = @())
    $strikes = 0
    foreach ($record in @($Records)) {
        $result = [string](Get-TeamProperty -InputObject $record -Name "result" -Default "")
        if ($script:TeamGateStrikeResults -contains $result) { $strikes++ }
        elseif ($result -eq "green" -or $result -eq "cleared") { $strikes = 0 }
    }
    return $strikes
}

function Test-TeamGateStopped {
    param([object[]]$Records = @())
    return ((Get-TeamGateStrikes -Records $Records) -ge $script:TeamGateMaxStrikes)
}

function Write-TeamGateRecord {
    param([Parameter(Mandatory = $true)][string]$Directory, [Parameter(Mandatory = $true)][int]$Number, [Parameter(Mandatory = $true)]$Record)
    if (-not (Test-Path -LiteralPath $Directory)) { [void](New-Item -ItemType Directory -Force -Path $Directory) }
    Write-TeamJson -Path (Join-Path $Directory "gate-$Number.json") -Document $Record
}

# ---------------------------------------------------------------------------- the worktree's environment

function Get-TeamGateEnvironmentPlan {
    <#
    .SYNOPSIS
        What a gate worktree needs before the gate can run in it, for the parts that are there:
        `uv sync` in services/api and services/browser, and the web shell's packages (pnpm's
        lock file lives at the repository root, so that is where it is installed from).
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Worktree,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$UvPath,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$PnpmPath
    )
    $steps = New-Object System.Collections.ArrayList
    foreach ($service in @("services\api", "services\browser")) {
        $directory = Join-Path $Worktree $service
        if (Test-Path -LiteralPath (Join-Path $directory "pyproject.toml")) {
            [void]$steps.Add([pscustomobject]@{ Name = ($service -replace '\\', '/') + " (uv sync)"; Directory = $directory; FilePath = $UvPath; Arguments = @("sync") })
        }
    }
    $web = ""
    if (Test-Path -LiteralPath (Join-Path $Worktree "pnpm-lock.yaml")) { $web = $Worktree }
    elseif (Test-Path -LiteralPath (Join-Path $Worktree "apps\web\pnpm-lock.yaml")) { $web = Join-Path $Worktree "apps\web" }
    if ($web) {
        [void]$steps.Add([pscustomobject]@{ Name = "apps/web (pnpm install)"; Directory = $web; FilePath = $PnpmPath; Arguments = @("install", "--frozen-lockfile", "--prefer-offline") })
    }
    return @($steps.ToArray())
}

# ---------------------------------------------------------------------------- acting: git

function Get-TeamRevision {
    <# The 40-hex sha of a revision, or "" when it does not exist. #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [Parameter(Mandatory = $true)][string]$Revision)
    $result = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("rev-parse", "--verify", "--quiet", "$Revision^{commit}")
    if (-not $result.Success) { return "" }
    return $result.StdOut.Trim()
}

function Test-TeamAncestor {
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [Parameter(Mandatory = $true)][string]$Ancestor, [Parameter(Mandatory = $true)][string]$Of)
    $result = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("merge-base", "--is-ancestor", $Ancestor, $Of)
    return ($result.ExitCode -eq 0)
}

function Get-TeamBranchCheckout {
    <# The worktree that has a branch checked out, or "" when none has. #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [Parameter(Mandatory = $true)][string]$Branch)
    $result = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("worktree", "list", "--porcelain")
    if (-not $result.Success) { throw "git worktree list failed: $($result.StdErr.Trim())" }
    $path = ""
    foreach ($line in @($result.StdOut -split "`r?`n")) {
        if ($line -match '^worktree (.+)$') { $path = $Matches[1].Trim() }
        elseif ($line -eq "branch refs/heads/$Branch") { return ($path -replace '/', '\') }
    }
    return ""
}

function Move-TeamBranchForward {
    <#
    .SYNOPSIS
        Move a branch forward to a commit that descends from where it is. Never backwards, never
        sideways, never by force.

    .DESCRIPTION
        A branch that a worktree has checked out is moved THERE by `git merge --ff-only`, so that
        worktree's files follow and git refuses if somebody's uncommitted work is in the way. A
        branch nobody has checked out is moved by `git update-ref` with the value it must still
        have: if it moved meanwhile, nothing is written.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$Branch,
        [Parameter(Mandatory = $true)][string]$To,
        [Parameter(Mandatory = $true)][string]$Expected
    )
    $now = Get-TeamRevision -RepoRoot $RepoRoot -Revision "refs/heads/$Branch"
    if ($now -eq $To) { return [pscustomobject]@{ Moved = $true; Detail = "already there" } }
    if ($now -ne $Expected) { return [pscustomobject]@{ Moved = $false; Detail = "$Branch moved meanwhile (it is $now, not $Expected)" } }
    if (-not (Test-TeamAncestor -RepoRoot $RepoRoot -Ancestor $Expected -Of $To)) {
        return [pscustomobject]@{ Moved = $false; Detail = "$To does not descend from $Branch" }
    }
    $where = Get-TeamBranchCheckout -RepoRoot $RepoRoot -Branch $Branch
    if ($where) {
        $merge = Invoke-TeamGit -WorkingDirectory $where -Arguments @("merge", "--ff-only", $To)
        if (-not $merge.Success) {
            return [pscustomobject]@{ Moved = $false; Detail = ("${where}: " +(($merge.StdOut + " " + $merge.StdErr) -replace '\s+', ' ').Trim()) }
        }
        return [pscustomobject]@{ Moved = $true; Detail = "fast-forwarded in $where" }
    }
    $update = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("update-ref", "-m", "team integrate: forward to $To", "refs/heads/$Branch", $To, $Expected)
    if (-not $update.Success) { return [pscustomobject]@{ Moved = $false; Detail = $update.StdErr.Trim() } }
    return [pscustomobject]@{ Moved = $true; Detail = "the ref was moved" }
}

function Reset-TeamGateWorktree {
    <#
    .SYNOPSIS
        The gate worktree, on a detached HEAD at a commit, with nothing left over from an earlier
        attempt. It is this step's own scratch tree; what git ignores (.venv, node_modules) stays.
    #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [Parameter(Mandatory = $true)][string]$Branch, [Parameter(Mandatory = $true)][string]$At)
    $path = Get-TeamGateWorktreePath -RepoRoot $RepoRoot -Branch $Branch
    if (Test-Path -LiteralPath (Join-Path $path ".git")) {
        [void](Invoke-TeamGit -WorkingDirectory $path -Arguments @("merge", "--abort"))
        foreach ($arguments in @(@("reset", "--hard", "--quiet"), @("clean", "-fd", "--quiet"), @("checkout", "--detach", "--quiet", $At))) {
            $step = Invoke-TeamGit -WorkingDirectory $path -Arguments $arguments
            if (-not $step.Success) { throw "the gate worktree could not be prepared (git $($arguments -join ' ')): $($step.StdErr.Trim())" }
        }
        return $path
    }
    $parent = Split-Path -Parent $path
    if (-not (Test-Path -LiteralPath $parent)) { [void](New-Item -ItemType Directory -Force -Path $parent) }
    $add = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("worktree", "add", "--detach", $path, $At)
    if (-not $add.Success) { throw "git worktree add failed for the gate of ${Branch}: $($add.StdErr.Trim())" }
    return $path
}

function Get-TeamWorktreeChanges {
    <# Every file a worktree differs in from a commit: committed since, staged, unstaged, new. #>
    param([Parameter(Mandatory = $true)][string]$Worktree, [Parameter(Mandatory = $true)][string]$Since)
    $files = New-Object System.Collections.ArrayList
    foreach ($arguments in @(@("diff", "--name-only", $Since), @("diff", "--name-only", $Since, "HEAD"), @("ls-files", "--others", "--exclude-standard"))) {
        $result = Invoke-TeamGit -WorkingDirectory $Worktree -Arguments $arguments
        if (-not $result.Success) { throw "git $($arguments -join ' ') failed: $($result.StdErr.Trim())" }
        foreach ($line in @($result.StdOut -split "`r?`n")) {
            $name = $line.Trim()
            if ($name -and $files -notcontains $name) { [void]$files.Add($name) }
        }
    }
    return @($files.ToArray())
}

# ---------------------------------------------------------------------------- acting: the gate

function Invoke-TeamGate {
    <#
    .SYNOPSIS
        Run the gate script in a worktree, everything it prints going to a log file AS IT RUNS
        (a gate takes the better part of an hour; the lead can read the log meanwhile).

    .DESCRIPTION
        The answer is the exit code and nothing else: Read-TeamGateLog judges. cmd.exe does the
        redirection so that no pipe of ours can fill, and the code page is UTF-8 so the log is.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$GatePath,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [double]$TimeoutMinutes = 0
    )
    foreach ($path in @($GatePath, $LogPath)) {
        if ($path -match '["&|<>^%!]') { throw "a path the gate is started with holds a character cmd.exe would read: $path" }
    }
    if (-not (Test-Path -LiteralPath $GatePath)) { throw "the gate script does not exist: $GatePath" }
    $folder = Split-Path -Parent $LogPath
    if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
    $system = Join-Path $env:SystemRoot "System32"
    $shell = Join-Path $system "WindowsPowerShell\v1.0\powershell.exe"
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = Join-Path $system "cmd.exe"
    $psi.Arguments = '/d /s /c "chcp 65001 >nul & "' + $shell + '" -NoProfile -ExecutionPolicy Bypass -File "' + $GatePath + '" > "' + $LogPath + '" 2>&1"'
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.WorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory).Path
    $started = [datetime]::UtcNow
    $process = [System.Diagnostics.Process]::Start($psi)
    $timedOut = $false
    if ($TimeoutMinutes -gt 0) {
        if (-not $process.WaitForExit([int]($TimeoutMinutes * 60000))) {
            $timedOut = $true
            Stop-TeamProcessTree -ProcessId $process.Id
            [void]$process.WaitForExit(15000)
        }
    }
    else { $process.WaitForExit() }
    $exitCode = if ($timedOut) { -1 } else { $process.ExitCode }
    $process.Dispose()
    return [pscustomobject]@{ ExitCode = $exitCode; TimedOut = $timedOut; Seconds = [int]([datetime]::UtcNow - $started).TotalSeconds }
}

# ---------------------------------------------------------------------------- the report

function New-TeamIntegrateReport {
    <#
    .SYNOPSIS
        What the integration step did, in Turkish, for the lead and the owner: per branch the
        outcome, the shas, what the environment took, the gate's log, and every stop.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Machine,
        [Parameter(Mandatory = $true)][string]$StartedAt,
        [object[]]$Outcomes = @(),
        [string[]]$Stops = @(),
        [string[]]$Risks = @()
    )
    $lines = New-Object System.Collections.ArrayList
    [void]$lines.Add("# Entegrasyon raporu")
    [void]$lines.Add("")
    [void]$lines.Add("Makine: $Machine · başladı $StartedAt · bitti $(Get-TeamTimestamp)")
    [void]$lines.Add("")
    [void]$lines.Add("Bu adım yayın YAPMAZ: yeşil kapıdan geçen iş main'e girer ve 'yayın bekliyor' olur.")
    [void]$lines.Add("")
    if (@($Outcomes).Count -eq 0) { [void]$lines.Add("Kapıya girecek dal yok."); [void]$lines.Add("") }
    foreach ($outcome in @($Outcomes)) {
        [void]$lines.Add("## $($outcome.Branch) — $($outcome.Result)")
        [void]$lines.Add("")
        foreach ($row in @($outcome.Lines)) { [void]$lines.Add("- $row") }
        [void]$lines.Add("")
    }
    [void]$lines.Add("## Durdurulanlar")
    [void]$lines.Add("")
    if (@($Stops).Count -eq 0) { [void]$lines.Add("Yok.") } else { foreach ($row in @($Stops)) { [void]$lines.Add("- $row") } }
    [void]$lines.Add("")
    [void]$lines.Add("## Açık riskler")
    [void]$lines.Add("")
    if (@($Risks).Count -eq 0) { [void]$lines.Add("Yok.") } else { foreach ($row in @($Risks)) { [void]$lines.Add("- $row") } }
    [void]$lines.Add("")
    return (($lines.ToArray()) -join "`n")
}
