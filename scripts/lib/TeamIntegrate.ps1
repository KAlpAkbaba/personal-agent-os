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
# "error" is an attempt that broke AFTER its lead run was paid for: uncounted, it would be
# repeated every half hour, a lead run each time.
$script:TeamGateStrikeResults = @("red", "lead_refused", "lead_failed", "error")
$script:TeamGateMaxStrikes = 2
# The step holds the team lock while the lead's run and the gate go, and a lock is taken over
# after six hours: together with the environment's build (three parts, a quarter of an hour each, twice at
# most) the caps must end well before that, or a later run resets the worktree under a gate.
$script:TeamGateMaxCapMinutes = 240
$script:TeamEnvironmentStepSeconds = 900
# The files the gate worktree's environment is built from.
$script:TeamEnvironmentInputs = @("pyproject.toml", "uv.lock", "pnpm-lock.yaml", "package.json", "pnpm-workspace.yaml")
# An attempt that stops the branch AT ONCE: a ref moved under the lead's run. A second try would
# merge the moved main into the branch, gate it and push it - the move would ride out on a green gate.
$script:TeamGateStopNowResults = @("refs_moved")
# A task whose code is on an integration branch has PASSED when it is in one of these states. In
# any other state (returned, being reworked, inspected again, stopped) the branch is held.
$script:TeamBranchPassedStates = @("merged", "awaiting_release", "released", "awaiting_real_evidence", "done")
# What Invoke-NativeProcess can start. A .ps1 (pnpm.ps1 is what PowerShell finds first for
# "pnpm") is not an application: CreateProcess refuses it.
$script:TeamToolExtensions = @(".exe", ".cmd")
$script:TeamReasonMaxLength = 900
# The suite's two seams at the start of the lead's run (Wait-TeamRunHold, Start-TeamHeldRun).
$script:TeamRunHoldVariable = "PAGENTOS_TEAM_INTEGRATE_HOLD_BEFORE_JOB"
$script:TeamRunHoldMilliseconds = 10000
$script:TeamRunJobFailsVariable = "PAGENTOS_TEAM_INTEGRATE_JOB_FAILS"

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

function Get-TeamBranchHeldTasks {
    <#
    .SYNOPSIS
        The tasks that HOLD an integration branch: their code was merged into it (the cycle wrote
        `integration_branch`) and they are not 'merged' any more - returned by a red gate or by
        the inspector, being reworked, or stopped.

    .DESCRIPTION
        A branch goes onto main whole or not at all: a green gate on it would put the returned
        task's code on main with the others. While one task holds it, nothing of it is gated.
        The worker's fix is merged into the same branch by the cycle; the task is 'merged' again,
        the tip is new, and one gate judges everything.
    #>
    param($Queue, [Parameter(Mandatory = $true)][string]$Branch)
    return @(Get-TeamTasks -Queue $Queue | Where-Object {
            ([string](Get-TeamProperty -InputObject $_ -Name "integration_branch" -Default "")) -eq $Branch -and
            $script:TeamBranchPassedStates -notcontains [string](Get-TeamProperty -InputObject $_ -Name "state" -Default "")
        })
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
    [void]$lines.Add("The script also compares main, $Branch and the remote's main before and after your run (do not")
    [void]$lines.Add("fetch either): a ref that moved refuses the run and stops the branch until a person has looked.")
    [void]$lines.Add("Your run ENDS with your final message. Wait for every command you started and start nothing in the background:")
    [void]$lines.Add("when the run ends the script stops every process it left going, and what such a process would have")
    [void]$lines.Add("written is never committed. The allow-list is held against what was COMMITTED, not against what you meant.")
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

# ---------------------------------------------------------------------------- the model policy
#
# The setting itself (which model the lead runs on, whether a run may be lowered) is the cycle's:
# Read-TeamModelSetting, Get-TeamRunModel and Test-TeamModelLimited in TeamQueue.ps1. What is
# here is what this step adds: what the cycles learnt, and what one of its own runs just said.

function Read-TeamLimitedModels {
    <#
    .SYNOPSIS
        What the cycles on this machine learnt about the usage limits (team/limits.json, the
        cycle's file - read, never written here): model id -> { until }. Only a model of the
        chain with a dated limit is taken; a file that is missing or cannot be read is no
        knowledge - the run will say the limit again.
    #>
    param([Parameter(Mandatory = $true)][string]$Path)
    $limited = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $limited }
    try {
        $known = Get-TeamProperty -InputObject (Read-TeamJson -Path $Path) -Name "models"
        if ($known -is [System.Management.Automation.PSCustomObject]) {
            foreach ($property in $known.PSObject.Properties) {
                $until = [string](Get-TeamProperty -InputObject $property.Value -Name "until" -Default "")
                if ((Test-TeamModelId -Model ([string]$property.Name)) -and $until) { $limited[[string]$property.Name] = [pscustomobject]@{ until = $until } }
            }
        }
    }
    catch { return @{} }
    return $limited
}

function Set-TeamModelClosed {
    <#
    .SYNOPSIS
        A run came back with the usage limit: -Limited now holds what that closes, for the rest
        of this step. The answer is the ids that were closed.

    .DESCRIPTION
        As the cycle's Register-Limit: a session or weekly limit closes every model; a model's
        own limit closes that model; and the model the run was ON is closed in every case
        ("out of usage credits" does not say whose limit it is), so the same run is never
        started twice on a model that just refused it. A reset that has passed, or was never
        said, would hand the same model out again at once: it is kept undated - closed for as
        long as -Limited lives, which is this step.
    #>
    param([Parameter(Mandatory = $true)][hashtable]$Limited, [Parameter(Mandatory = $true)]$Result, [string]$RunModel = "")
    $ids = New-Object System.Collections.ArrayList
    if ([string](Get-TeamProperty -InputObject $Result -Name "LimitScope" -Default "") -eq "all") {
        foreach ($id in @(Get-TeamModelChain)) { [void]$ids.Add([string]$id) }
    }
    else {
        $named = [string](Get-TeamProperty -InputObject $Result -Name "LimitedModel" -Default "")
        if (Test-TeamModelId -Model $named) { [void]$ids.Add($named) }
    }
    if ((Test-TeamModelId -Model $RunModel) -and $ids -notcontains $RunModel) { [void]$ids.Add($RunModel) }
    $until = [string](Get-TeamProperty -InputObject $Result -Name "ResetsAt" -Default "")
    foreach ($id in $ids) {
        $Limited[$id] = [pscustomobject]@{ until = $until }
        if (-not (Test-TeamModelLimited -Limited $Limited -Model $id)) { $Limited[$id] = [pscustomobject]@{ until = "" } }
    }
    return @($ids.ToArray())
}

# ---------------------------------------------------------------------------- the run's process tree
#
# The first real lead run ended with "tests are running in the background; I will write the
# report when they finish". Its own process was gone and what it had started was not: a file
# written after the diff was judged reached main. `taskkill /T` finds children through a parent
# that is alive; here the parent is the one thing that has ended. A Windows job object holds
# every process started from the run's own, whoever its parent was by the end.

function Initialize-TeamRunJobType {
    <#
        kernel32's job object and a process that is created SUSPENDED, declared once in a session.
        System.Diagnostics.Process cannot start a process suspended, so the three pipes and the
        CreateProcess call are made here - as Process.Start makes them (inherited handles, no
        window, the caller's environment plus what the caller adds).
    #>
    if ($null -ne ("PagentOS.Team.RunJob" -as [type])) { return }
    Add-Type -ReferencedAssemblies "System.Core" -TypeDefinition @"
using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;

namespace PagentOS.Team {
    public sealed class SuspendedRun {
        public IntPtr ProcessHandle;
        public IntPtr ThreadHandle;
        public int ProcessId;
        public Stream StdIn;
        public Stream StdOut;
        public Stream StdErr;
    }

    public static class RunJob {
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern IntPtr CreateJobObject(IntPtr attributes, IntPtr name);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool TerminateJobObject(IntPtr job, uint exitCode);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool QueryInformationJobObject(IntPtr job, int infoClass, IntPtr info, int length, IntPtr returned);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool TerminateProcess(IntPtr process, uint exitCode);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern uint ResumeThread(IntPtr thread);

        [StructLayout(LayoutKind.Sequential)]
        struct STARTUPINFO {
            public int cb;
            public IntPtr lpReserved, lpDesktop, lpTitle;
            public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
            public short wShowWindow, cbReserved2;
            public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        struct PROCESS_INFORMATION {
            public IntPtr hProcess, hThread;
            public int dwProcessId, dwThreadId;
        }

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        static extern bool CreateProcessW(string application, StringBuilder commandLine, IntPtr processAttributes, IntPtr threadAttributes,
            bool inheritHandles, uint flags, byte[] environment, string workingDirectory, ref STARTUPINFO startup, out PROCESS_INFORMATION made);

        const uint CREATE_SUSPENDED = 0x00000004, CREATE_UNICODE_ENVIRONMENT = 0x00000400, CREATE_NO_WINDOW = 0x08000000;
        const int STARTF_USESTDHANDLES = 0x00000100;

        // The process exists and has run nothing: its first thread waits for Resume.
        public static SuspendedRun StartSuspended(string commandLine, string workingDirectory, IDictionary extraEnvironment) {
            SortedDictionary<string, string> variables = new SortedDictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (DictionaryEntry entry in Environment.GetEnvironmentVariables()) { variables[(string)entry.Key] = (string)entry.Value; }
            if (extraEnvironment != null) { foreach (DictionaryEntry entry in extraEnvironment) { variables[Convert.ToString(entry.Key)] = Convert.ToString(entry.Value); } }
            StringBuilder block = new StringBuilder();
            foreach (KeyValuePair<string, string> variable in variables) { block.Append(variable.Key).Append('=').Append(variable.Value).Append('\0'); }
            block.Append('\0');

            AnonymousPipeServerStream input = new AnonymousPipeServerStream(PipeDirection.Out, HandleInheritability.Inheritable);
            AnonymousPipeServerStream output = new AnonymousPipeServerStream(PipeDirection.In, HandleInheritability.Inheritable);
            AnonymousPipeServerStream error = new AnonymousPipeServerStream(PipeDirection.In, HandleInheritability.Inheritable);
            STARTUPINFO startup = new STARTUPINFO();
            startup.cb = Marshal.SizeOf(typeof(STARTUPINFO));
            startup.dwFlags = STARTF_USESTDHANDLES;
            startup.hStdInput = input.ClientSafePipeHandle.DangerousGetHandle();
            startup.hStdOutput = output.ClientSafePipeHandle.DangerousGetHandle();
            startup.hStdError = error.ClientSafePipeHandle.DangerousGetHandle();
            PROCESS_INFORMATION made;
            bool created = CreateProcessW(null, new StringBuilder(commandLine), IntPtr.Zero, IntPtr.Zero, true,
                CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW, Encoding.Unicode.GetBytes(block.ToString()), workingDirectory, ref startup, out made);
            int code = Marshal.GetLastWin32Error();
            // This side's copies of the command's ends: with them open, the pipes would never end.
            input.DisposeLocalCopyOfClientHandle();
            output.DisposeLocalCopyOfClientHandle();
            error.DisposeLocalCopyOfClientHandle();
            if (!created) {
                input.Dispose(); output.Dispose(); error.Dispose();
                throw new Win32Exception(code);
            }
            SuspendedRun run = new SuspendedRun();
            run.ProcessHandle = made.hProcess; run.ThreadHandle = made.hThread; run.ProcessId = made.dwProcessId;
            run.StdIn = input; run.StdOut = output; run.StdErr = error;
            return run;
        }

        // 0, or the Win32 error (read here: PowerShell makes calls of its own before it could ask).
        public static int Assign(IntPtr job, IntPtr process) {
            return AssignProcessToJobObject(job, process) ? 0 : Math.Max(1, Marshal.GetLastWin32Error());
        }

        public static int Resume(IntPtr thread) {
            return ResumeThread(thread) != 0xFFFFFFFF ? 0 : Math.Max(1, Marshal.GetLastWin32Error());
        }

        // The prompt, written off this thread: a command that never reads it must not hold the step past its cap.
        public static Task Feed(Stream stream, byte[] bytes) {
            return Task.Run(() => {
                try { stream.Write(bytes, 0, bytes.Length); }
                catch (IOException) { }
                catch (ObjectDisposedException) { }
                finally { try { stream.Dispose(); } catch (IOException) { } }
            });
        }
    }
}
"@
}

function Wait-TeamRunHold {
    <#
    .SYNOPSIS
        The suite's seam at the one moment that matters: just before the run is put into its
        job. When PAGENTOS_TEAM_INTEGRATE_HOLD_BEFORE_JOB names a file, the step waits HERE until
        that file exists (ten seconds at most), and -Passed writes "<file>.passed" once the
        assignment is behind it. Unset - always, outside the suite - it does nothing.
    #>
    param([switch]$Passed)
    $file = [string][Environment]::GetEnvironmentVariable($script:TeamRunHoldVariable)
    if (-not $file) { return }
    if ($Passed) { [System.IO.File]::WriteAllText("$file.passed", "passed"); return }
    $until = [datetime]::UtcNow.AddMilliseconds($script:TeamRunHoldMilliseconds)
    while (-not (Test-Path -LiteralPath $file) -and [datetime]::UtcNow -lt $until) { Start-Sleep -Milliseconds 20 }
}

function Start-TeamHeldRun {
    <#
    .SYNOPSIS
        Start one role run INSIDE a job object of its own: the command is created suspended, put
        into the job, and only then let run - so every process it ever starts is in the job,
        whoever its parent is by the end, and the whole tree can be stopped once the run has ended.

    .DESCRIPTION
        Until 2026-10-02 the run was started (Start-TeamRun) and put into its job on the next line.
        A process the run started in between was in no job, and a run that had ended by then could
        not be assigned at all: nothing was stopped, and what was left going wrote after the diff
        was read (1 of 78 under load, at the merge). There is no "in between" now: the command has
        not run one instruction when it is assigned.

        Held is false when the run could NOT be put into a job (Why says so). The command was then
        never let run: it is ended as it was created, nothing was paid for, and the caller starts
        no run. A command that cannot be created at all throws, as Start-TeamRun does.

        Run has Start-TeamRun's shape (Process, StdOut, StdErr, Started): Wait-TeamRun reads it.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Prompt,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )
    Initialize-TeamRunJobType
    $api = [PagentOS.Team.RunJob]
    $none = [IntPtr]::Zero
    $job = $api::CreateJobObject($none, $none)
    if ($job -eq $none) { return [pscustomobject]@{ Held = $false; Why = "CreateJobObject failed"; Run = $null; Job = $null } }
    $file = if ($FilePath.StartsWith('"')) { $FilePath } else { '"' + $FilePath + '"' }
    $line = $file + " " + (ConvertTo-NativeArgumentLine -Arguments $Arguments)
    $started = $null
    # The tool can switch a session off the model it was started on by itself: see Start-TeamRun.
    try { $started = $api::StartSuspended($line, (Resolve-Path -LiteralPath $WorkingDirectory).Path, @{ CLAUDE_CODE_NO_MODEL_FALLBACK = "1" }) }
    catch { [void]$api::CloseHandle($job); throw }
    $why = ""
    $run = $null
    try {
        Wait-TeamRunHold
        $code = if ([Environment]::GetEnvironmentVariable($script:TeamRunJobFailsVariable)) { -1 } else { $api::Assign($job, $started.ProcessHandle) }
        if ($code -ne 0) { $why = "AssignProcessToJobObject failed (Win32 $code)" }
        else {
            Wait-TeamRunHold -Passed
            # Its own handle on the process, taken while the process cannot end: the exit code is read through it.
            $process = [System.Diagnostics.Process]::GetProcessById($started.ProcessId)
            [void]$process.Handle
            $stdout = (New-Object System.IO.StreamReader($started.StdOut, [System.Text.Encoding]::UTF8)).ReadToEndAsync()
            $stderr = (New-Object System.IO.StreamReader($started.StdErr, [System.Text.Encoding]::UTF8)).ReadToEndAsync()
            $code = $api::Resume($started.ThreadHandle)
            if ($code -ne 0) { $why = "ResumeThread failed (Win32 $code)" }
            else {
                [void]$api::Feed($started.StdIn, (New-Object System.Text.UTF8Encoding($false)).GetBytes($Prompt))
                $run = [pscustomobject]@{ Process = $process; StdOut = $stdout; StdErr = $stderr; Started = [datetime]::UtcNow }
            }
        }
    }
    catch { $why = [string]$_.Exception.Message }
    finally {
        if ($null -eq $run) {
            # Never let run, or not known to be running inside the job: it is ended where it stands.
            [void]$api::TerminateJobObject($job, 1)
            [void]$api::TerminateProcess($started.ProcessHandle, 1)
            foreach ($stream in @($started.StdIn, $started.StdOut, $started.StdErr)) { try { $stream.Dispose() } catch { } }
            [void]$api::CloseHandle($job)
        }
        [void]$api::CloseHandle($started.ThreadHandle)
        [void]$api::CloseHandle($started.ProcessHandle)
    }
    if ($null -eq $run) { return [pscustomobject]@{ Held = $false; Why = $(if ($why) { $why } else { "the run could not be started inside its job" }); Run = $null; Job = $null } }
    return [pscustomobject]@{ Held = $true; Why = ""; Run = $run; Job = [pscustomobject]@{ Handle = $job; Held = $true; Why = "" } }
}

function Get-TeamRunJobProcessIds {
    <# The ids of the processes that are in a job NOW. #>
    param([Parameter(Mandatory = $true)]$Job)
    if (-not $Job.Held) { return @() }
    $marshal = [System.Runtime.InteropServices.Marshal]
    $size = 8 + ([IntPtr]::Size * 2048)
    $buffer = $marshal::AllocHGlobal($size)
    try {
        # JobObjectBasicProcessIdList (3): assigned, in-the-list (two DWORDs), then the ids, pointer-sized.
        if (-not [PagentOS.Team.RunJob]::QueryInformationJobObject($Job.Handle, 3, $buffer, $size, [IntPtr]::Zero)) { return @() }
        $listed = $marshal::ReadInt32($buffer, 4)
        $ids = New-Object System.Collections.ArrayList
        for ($index = 0; $index -lt $listed; $index++) {
            [void]$ids.Add([int]($marshal::ReadIntPtr($buffer, 8 + ($index * [IntPtr]::Size)).ToInt64()))
        }
        return @($ids.ToArray())
    }
    finally { $marshal::FreeHGlobal($buffer) }
}

function Stop-TeamRunJob {
    <#
    .SYNOPSIS
        Stop everything a run left going and WAIT until it is gone: nothing of the run writes
        after this returns. The answer is the names of what was still alive (a console's own
        conhost is not one).
    #>
    param([Parameter(Mandatory = $true)]$Job, [int]$WaitSeconds = 30)
    if (-not $Job.Held) { return @() }
    $left = New-Object System.Collections.ArrayList
    try {
        foreach ($id in @(Get-TeamRunJobProcessIds -Job $Job)) {
            $process = Get-Process -Id $id -ErrorAction SilentlyContinue
            if ($null -ne $process -and [string]$process.ProcessName -ne "conhost") { [void]$left.Add([string]$process.ProcessName) }
        }
        [void][PagentOS.Team.RunJob]::TerminateJobObject($Job.Handle, 1)
        $until = [datetime]::UtcNow.AddSeconds($WaitSeconds)
        while (@(Get-TeamRunJobProcessIds -Job $Job).Count -gt 0 -and [datetime]::UtcNow -lt $until) { Start-Sleep -Milliseconds 50 }
    }
    finally {
        [void][PagentOS.Team.RunJob]::CloseHandle($Job.Handle)
        $Job.Held = $false
    }
    return @($left.ToArray())
}

function Wait-TeamLeadRun {
    <#
    .SYNOPSIS
        Wait for the lead's wiring run until its deadline, then stop EVERYTHING it started -
        whether it ended by itself or was cut at its cap - BEFORE what it printed, moved or
        wrote is read.

    .DESCRIPTION
        The output pipes are read after the stop: a process the run left going may hold the
        run's standard output open, and the read would then wait for it and come back empty -
        a finished run judged "no result". Left is what a run that ENDED BY ITSELF had still
        going (names); a run cut at its cap is TimedOut, and everything it had was going.
    #>
    param([Parameter(Mandatory = $true)]$Run, [Parameter(Mandatory = $true)]$Job, [Parameter(Mandatory = $true)][datetime]$Deadline)
    $remaining = [int][Math]::Max(0, [Math]::Min([double]([int]::MaxValue - 1), ($Deadline.ToUniversalTime() - [datetime]::UtcNow).TotalMilliseconds))
    $exited = $Run.Process.WaitForExit($remaining)
    $left = @(Stop-TeamRunJob -Job $Job)
    # Wait-TeamRun reads the pipes and the exit code. The run is in the job (Start-TeamHeldRun
    # starts no run outside one), so it has ended by now, whether by itself or with the job.
    $grace = if ($exited) { 15 } else { 0 }
    $finished = Wait-TeamRun -Run $Run -Deadline ([datetime]::UtcNow.AddSeconds($grace))
    return [pscustomobject]@{
        ExitCode = $(if ($exited) { $finished.ExitCode } else { -1 }); StdOut = $finished.StdOut; StdErr = $finished.StdErr
        TimedOut = (-not $exited); Seconds = $finished.Seconds; Left = $(if ($exited) { @($left) } else { @() })
    }
}

# ---------------------------------------------------------------------------- the gate's words

function Test-TeamCapMinutes {
    <#
    .SYNOPSIS
        Why these caps may not be run with; "" when they may. No cap (0) is refused: a gate that
        hangs would hold the team lock until the six-hour takeover.
    #>
    param([double]$GateMinutes, [double]$LeadMinutes)
    if ($GateMinutes -le 0) { return "-GateMinutes must be more than 0: a gate that hangs would hold the team lock until the six-hour takeover" }
    if ($LeadMinutes -le 0) { return "-LeadMinutes must be more than 0: a lead run that hangs would hold the team lock until the six-hour takeover" }
    if (($GateMinutes + $LeadMinutes) -gt $script:TeamGateMaxCapMinutes) {
        return "-GateMinutes + -LeadMinutes may be $($script:TeamGateMaxCapMinutes) at most: the lock is taken over after six hours, and the step must have ended by then"
    }
    return ""
}

function Get-TeamGateFailingLines {
    <#
    .SYNOPSIS
        The lines of a step that SAY a failure: a line that begins with a failure's mark (and
        the indented lines under it), a line with a place in a file (path:line, path(line,col)),
        a compiler's "error", pytest's progress line with an F or an E.

    .DESCRIPTION
        A line that says PASS is never one, whatever else it holds: the script-syntax suite
        prints "PASS  scripts\..." for every script it parsed, inside the step that one bad
        script fails - and each of those lines names a file of some task.
    #>
    param([string[]]$Lines = @())
    $kept = New-Object System.Collections.ArrayList
    $under = -1
    foreach ($raw in @($Lines)) {
        $line = [string]$raw
        if (-not $line.Trim()) { $under = -1; continue }
        $indent = $line.Length - $line.TrimStart().Length
        if ($line -cmatch '\bPASS(ED)?\b' -or $line -cmatch '^\s*(Passed|Ge\S{1,2}ti|ok)\s' -or $line -match '^\s*[✓√]') { $under = -1; continue }
        if ($line -cmatch '^\s*(FAILED|FAIL|ERROR|Failed|Error|Ba\S{1,2}ar\S{1,2}s\S{1,2}z|×|✗|✘|❯)(\s|:|$)' -or $line -cmatch '^E\s{2,}') {
            [void]$kept.Add($line); $under = $indent; continue
        }
        if ($under -ge 0 -and $indent -gt $under) { [void]$kept.Add($line); continue }
        $under = -1
        if ($line -match '[\w./\\-]+\.\w{1,8}(:\d+|\(\d+(,\d+)?\))' -or $line -match '\berror\b' -or
            $line -cmatch '^\S+\.py\s+[.sxXFE]*[FE][.sxXFE]*(\s|$)') { [void]$kept.Add($line) }
    }
    return @($kept.ToArray())
}

function Read-TeamGateLog {
    <#
    .SYNOPSIS
        What a run of scripts/quality-gate.ps1 said: green or not, which steps failed, the first
        failing test, and the FAILING LINES of the failing steps (Get-TeamGateFailingLines):
        what a red gate names. A gate that died without a step saying it failed names nothing -
        its whole log is not a failure, and a task named in a step that passed did not break it.

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
        foreach ($line in @(Get-TeamGateFailingLines -Lines @($sections[$step].ToArray()))) { [void]$failing.Add([string]$line) }
        foreach ($line in $sections[$step]) {
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
    # With no step to point at, the failure names nothing: nobody is blamed from the whole log.
    $failureText = if (@($failing).Count -gt 0) { (($failing.ToArray()) -join "`n") } else { "" }
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

function Get-TeamGateStopKind {
    <#
    .SYNOPSIS
        Why a branch is stopped until the lead looks: "strikes" (two failed attempts in a row),
        "refs_moved" (a ref moved under the lead's run: stopped at once), or "" (not stopped).
    #>
    param([object[]]$Records = @())
    $moved = $false
    foreach ($record in @($Records)) {
        $result = [string](Get-TeamProperty -InputObject $record -Name "result" -Default "")
        if ($script:TeamGateStopNowResults -contains $result) { $moved = $true }
        elseif ($result -eq "green" -or $result -eq "cleared") { $moved = $false }
    }
    if ($moved) { return "refs_moved" }
    if ((Get-TeamGateStrikes -Records $Records) -ge $script:TeamGateMaxStrikes) { return "strikes" }
    return ""
}

function Test-TeamGateStopped {
    param([object[]]$Records = @())
    return ((Get-TeamGateStopKind -Records $Records) -ne "")
}

function Test-TeamGateAlreadyRed {
    <#
    .SYNOPSIS
        Whether the LAST attempt on a branch was a red gate on exactly this commit. Gating it
        again is an hour holding the lock for an answer that is known: the step waits for a new
        tip (a fix merged in, main moved) or for the lead's -ClearGateStop.
    #>
    param([object[]]$Records = @(), [string]$Sha = "")
    $all = @($Records)
    if (-not $Sha -or @($all).Count -eq 0) { return $false }
    $last = $all[@($all).Count - 1]
    return ([string](Get-TeamProperty -InputObject $last -Name "result" -Default "") -eq "red" -and
        [string](Get-TeamProperty -InputObject $last -Name "sha" -Default "") -eq $Sha)
}

function Get-TeamGateUnappliedVerdict {
    <#
    .SYNOPSIS
        The record of a red gate whose verdict never reached the queue: the LAST attempt on the
        branch, red, on exactly this commit, and marked `applied: false`. $null otherwise.

    .DESCRIPTION
        The record is written before the queue is (the gate's hour must not be lost), and the
        queue's write can fail: a task changed in the store while the gate ran (a stale write),
        the Cloud Core restarting. The record keeps who was named and the words, so the next
        run writes them instead of waiting in silence on "the gate was red on this commit". A
        record without the mark is from before it existed: its verdict was written, or lost.
    #>
    param([object[]]$Records = @(), [string]$Sha = "")
    $all = @($Records)
    if (-not $Sha -or @($all).Count -eq 0) { return $null }
    $last = $all[@($all).Count - 1]
    if ([string](Get-TeamProperty -InputObject $last -Name "result" -Default "") -ne "red") { return $null }
    if ([string](Get-TeamProperty -InputObject $last -Name "sha" -Default "") -ne $Sha) { return $null }
    $applied = Get-TeamProperty -InputObject $last -Name "applied" -Default $null
    if ($null -eq $applied -or [bool]$applied) { return $null }
    return $last
}

function Write-TeamGateRecord {
    param([Parameter(Mandatory = $true)][string]$Directory, [Parameter(Mandatory = $true)][int]$Number, [Parameter(Mandatory = $true)]$Record)
    if (-not (Test-Path -LiteralPath $Directory)) { [void](New-Item -ItemType Directory -Force -Path $Directory) }
    Write-TeamJson -Path (Join-Path $Directory "gate-$Number.json") -Document $Record
}

# ---------------------------------------------------------------------------- the worktree's environment

function Resolve-TeamToolPath {
    <#
    .SYNOPSIS
        Where a native tool (docker, uv, pnpm) is, as something a process can START: an .exe or a
        .cmd. "" when there is none.

    .DESCRIPTION
        Not Get-Command: for "pnpm" it answers pnpm.ps1, which CreateProcess refuses ("not a
        valid application"), and every run would end in "the environment could not be built".
        PATH is walked folder by folder, .exe before .cmd in each; then the fallbacks, held to
        the same rule. A path that is GIVEN is taken as given, and refused by name when it is
        not one of the two.
    #>
    param(
        [string]$Given = "",
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$Fallbacks = @(),
        [AllowEmptyString()][string]$SearchPath = $env:PATH
    )
    if ($Given) {
        if ($script:TeamToolExtensions -notcontains [System.IO.Path]::GetExtension($Given).ToLowerInvariant()) {
            throw "'$Given' is given for $Name and is neither an .exe nor a .cmd: a process cannot start it"
        }
        return $Given
    }
    foreach ($folder in @(([string]$SearchPath) -split ";")) {
        $directory = $folder.Trim().Trim('"')
        if (-not $directory) { continue }
        foreach ($extension in $script:TeamToolExtensions) {
            try {
                $candidate = Join-Path $directory ($Name + $extension)
                if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
            }
            catch { }
        }
    }
    foreach ($fallback in @($Fallbacks)) {
        $expanded = [Environment]::ExpandEnvironmentVariables([string]$fallback)
        if ($script:TeamToolExtensions -notcontains [System.IO.Path]::GetExtension($expanded).ToLowerInvariant()) { continue }
        if (Test-Path -LiteralPath $expanded -PathType Leaf) { return $expanded }
    }
    return ""
}

function Test-TeamEnvironmentInput {
    <# Whether a changed file decides what the worktree's environment holds (a project file, a lock file). #>
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Path)
    $file = ($Path -replace '\\', '/').Trim().ToLowerInvariant()
    if (-not $file) { return $false }
    return ($script:TeamEnvironmentInputs -contains $file.Substring($file.LastIndexOf('/') + 1))
}

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

function Test-TeamSameTree {
    <#
    .SYNOPSIS
        Whether two commits hold exactly the same files. False when either cannot be read: an
        answer that is not known is not "the same" (Get-TeamRevision asks for a COMMIT, so a
        tree asked through it is "" on both sides - and "" equals "").
    #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [Parameter(Mandatory = $true)][string]$A, [Parameter(Mandatory = $true)][string]$B)
    $trees = New-Object System.Collections.ArrayList
    foreach ($revision in @($A, $B)) {
        $result = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("rev-parse", "--verify", "--quiet", "$revision^{tree}")
        if (-not $result.Success -or $result.StdOut.Trim() -notmatch '^[0-9a-f]{40}$') { return $false }
        [void]$trees.Add($result.StdOut.Trim())
    }
    return ($trees[0] -eq $trees[1])
}

function Get-TeamRefValues {
    <# name -> 40-hex sha ("" when the ref does not exist), for the refs a run may not move. #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [string[]]$Names = @())
    $values = @{}
    foreach ($name in @($Names)) { $values[[string]$name] = Get-TeamRevision -RepoRoot $RepoRoot -Revision ([string]$name) }
    return $values
}

function Compare-TeamRefValues {
    <#
    .SYNOPSIS
        The refs that are not where they were: moved, made or deleted. Each with where it was
        and where it is ("" = it does not exist). Empty when nothing moved.
    #>
    param([Parameter(Mandatory = $true)][hashtable]$Before, [Parameter(Mandatory = $true)][hashtable]$After)
    $moved = New-Object System.Collections.ArrayList
    foreach ($name in @(@($Before.Keys) + @($After.Keys) | Sort-Object -Unique)) {
        $was = if ($Before.ContainsKey($name)) { [string]$Before[$name] } else { "" }
        $is = if ($After.ContainsKey($name)) { [string]$After[$name] } else { "" }
        if ($was -ne $is) { [void]$moved.Add([pscustomobject]@{ Name = [string]$name; Before = $was; After = $is }) }
    }
    return @($moved.ToArray())
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

function Test-TeamWorktreeRegistered {
    <# Whether git has a worktree recorded at a path - whether or not the folder is still there. #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [Parameter(Mandatory = $true)][string]$Path)
    $result = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("worktree", "list", "--porcelain")
    if (-not $result.Success) { throw "git worktree list failed: $($result.StdErr.Trim())" }
    $wanted = ($Path -replace '/', '\').TrimEnd('\')
    foreach ($line in @($result.StdOut -split "`r?`n")) {
        if ($line -match '^worktree (.+)$' -and (($Matches[1].Trim() -replace '/', '\').TrimEnd('\') -ieq $wanted)) { return $true }
    }
    return $false
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
    # The tree is not there. A folder somebody deleted by hand (each tree is a gigabyte) leaves git's
    # record of the worktree behind, and `git worktree add` refuses a path it still has registered:
    # THIS path's record is removed first - no other worktree's, which `git worktree prune` would do.
    if (Test-Path -LiteralPath $path) {
        if (@(Get-ChildItem -LiteralPath $path -Force).Count -gt 0) {
            throw "what is left of the gate worktree of $Branch has no .git and is not empty: $path - this step does not delete a folder it cannot tell is its own; delete it by hand and the next run makes the tree again"
        }
        [System.IO.Directory]::Delete($path, $false)
    }
    if (Test-TeamWorktreeRegistered -RepoRoot $RepoRoot -Path $path) {
        $remove = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("worktree", "remove", $path)
        if (-not $remove.Success) { throw "git's record of the missing gate worktree of $Branch could not be removed: $($remove.StdErr.Trim())" }
    }
    $parent = Split-Path -Parent $path
    if (-not (Test-Path -LiteralPath $parent)) { [void](New-Item -ItemType Directory -Force -Path $parent) }
    $add = Invoke-TeamGit -WorkingDirectory $RepoRoot -Arguments @("worktree", "add", "--detach", $path, $At)
    if (-not $add.Success) { throw "git worktree add failed for the gate of ${Branch}: $($add.StdErr.Trim())" }
    return $path
}

function Get-TeamCommittedFiles {
    <#
    .SYNOPSIS
        Every file two COMMITS differ in. This is what the lead's wiring is judged by: the diff
        of what was committed, never the working tree as it looked a moment before staging - a
        file that appeared in between was committed unseen (cycle-auto-integrate, 2026-10-02).

    .DESCRIPTION
        --no-renames: with git's rename detection a file MOVED out of a task's area into docs/
        is listed by its new name only - an allowed one - and the deletion inside the area is
        never seen. Without it a move is what it is: one file gone, one file new.
    #>
    param([Parameter(Mandatory = $true)][string]$Worktree, [Parameter(Mandatory = $true)][string]$From, [Parameter(Mandatory = $true)][string]$To)
    $result = Invoke-TeamGit -WorkingDirectory $Worktree -Arguments @("diff", "--name-only", "--no-renames", $From, $To)
    if (-not $result.Success) { throw "git diff --name-only $From $To failed: $($result.StdErr.Trim())" }
    return @($result.StdOut -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
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
        # Never without a cap: the caller holds the team lock while this waits.
        [Parameter(Mandatory = $true)][double]$TimeoutMinutes
    )
    if ($TimeoutMinutes -le 0) { throw "the gate is not run without a cap on its minutes" }
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
    if (-not $process.WaitForExit([int]($TimeoutMinutes * 60000))) {
        $timedOut = $true
        Stop-TeamProcessTree -ProcessId $process.Id
        [void]$process.WaitForExit(15000)
    }
    $exitCode = if ($timedOut) { -1 } else { $process.ExitCode }
    $process.Dispose()
    return [pscustomobject]@{ ExitCode = $exitCode; TimedOut = $timedOut; Seconds = [int]([datetime]::UtcNow - $started).TotalSeconds }
}

# ---------------------------------------------------------------------------- the report

function Add-TeamSkippedLine {
    <#
    .SYNOPSIS
        One line for a run that started nothing (the lock is somebody's, Docker is down), appended
        to a file that keeps the newest -Keep lines. Such a run writes no report for the branch:
        the branch's report still says what the last run that DID something found.
    #>
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Line, [int]$Keep = 200)
    $folder = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
    $lines = New-Object System.Collections.ArrayList
    if (Test-Path -LiteralPath $Path) {
        foreach ($old in @([System.IO.File]::ReadAllLines($Path, [System.Text.Encoding]::UTF8))) { if ($old.Trim()) { [void]$lines.Add($old) } }
    }
    [void]$lines.Add(($Line -replace '\s+', ' ').Trim())
    $kept = @($lines.ToArray() | Select-Object -Last ([Math]::Max(1, $Keep)))
    [System.IO.File]::WriteAllText($Path, ($kept -join "`n") + "`n", (New-Object System.Text.UTF8Encoding($false)))
}

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
