<#
.SYNOPSIS
    The scheduled task's one action: feed the queue from the roadmap when it runs low, then
    run the cycle.

.DESCRIPTION
    Owner rule 2026-10-01 (ADR-0214 addendum 8): no agent idles while the roadmap names work.
    `feed.ps1` cuts the roadmap's next items into cards when fewer tasks are runnable than
    there are worker seats; `cycle.ps1` then runs what is runnable. The feeder never decides
    whether the cycle runs: whatever it answers (nothing to do, the lock is held, a refused
    feed file, an error) the cycle is started, and the feeder's exit code is only printed.

    It never releases, never merges to main and never pushes: it starts the two scripts.

.EXAMPLE
    .\scripts\team\tick.ps1 -MaxParallel 6 -DailyId -Research -ResearchEveryHours 6
#>
[CmdletBinding()]
param(
    [int]$MaxParallel = 2,
    [double]$MaxUsd = 0,
    [int]$CycleMinutes = 0,
    [switch]$Research,
    [switch]$DailyId,
    [double]$ResearchEveryHours = 0,
    [string]$Base = "",
    [string]$QueueUrl = "",
    [string]$QueueToken = "",
    # The cycle alone, as before the feeder existed.
    [switch]$NoFeed,
    # The tests put fakes in place of the two scripts.
    [string]$FeedPath = "",
    [string]$CyclePath = "",
    # Where the tick writes what it stopped (default team/logs/tick.log, git-ignored), and the
    # folder of the cycle reports that get an orphan line (default team/reports).
    [string]$LogPath = "",
    [string]$ReportsRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$feedScript = if ($FeedPath) { $FeedPath } else { Join-Path $PSScriptRoot "feed.ps1" }
$cycleScript = if ($CyclePath) { $CyclePath } else { Join-Path $PSScriptRoot "cycle.ps1" }
if (-not $LogPath) { $LogPath = Join-Path $repoRoot "team\logs\tick.log" }
if (-not $ReportsRoot) { $ReportsRoot = Join-Path $repoRoot "team\reports" }
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
# The suite's hook: set, the job object "cannot be created" (team-tick.tests.ps1 case 6).
$jobFailsVariable = "PAGENTOS_TEAM_TICK_JOB_FAILS"
$script:noJobSaid = $false
$script:leftovers = New-Object System.Collections.ArrayList

# 2026-10-03 (ADR-0214 addendum): `Start-Process -Wait` in Windows PowerShell 5.1 waits for the
# process AND every process it ever started. A `tail -f` an agent run had left behind held the
# 02:00 tick for two hours after its cycle had ended, and the scheduled task (IgnoreNew) skipped
# every trigger meanwhile. Now the script is started suspended inside a job object the tick
# owns, the tick waits for that one process, and once it has exited whatever is still in the
# job is named and stopped. The job is the boundary: nothing started outside it is touched.

function Initialize-TickJobType {
    if ($null -ne ("PagentOS.Team.TickJob" -as [type])) { return }
    Add-Type -TypeDefinition @"
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

namespace PagentOS.Team {
    public static class TickJob {
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern IntPtr CreateJobObject(IntPtr attributes, IntPtr name);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool TerminateJobObject(IntPtr job, uint exitCode);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool QueryInformationJobObject(IntPtr job, int infoClass, IntPtr info, int length, IntPtr returned);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)]
        static extern bool GetExitCodeProcess(IntPtr process, out uint exitCode);

        [StructLayout(LayoutKind.Sequential)]
        struct STARTUPINFO {
            public int cb;
            public IntPtr lpReserved, lpDesktop, lpTitle;
            public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
            public short wShowWindow, cbReserved2;
            public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct PROCESS_INFORMATION {
            public IntPtr hProcess, hThread;
            public int dwProcessId, dwThreadId;
        }

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        static extern bool CreateProcessW(string application, StringBuilder commandLine, IntPtr processAttributes, IntPtr threadAttributes,
            bool inheritHandles, uint flags, IntPtr environment, string workingDirectory, ref STARTUPINFO startup, out PROCESS_INFORMATION made);

        // As Start-Process -NoNewWindow made it (inherited handles and console, the caller's
        // environment), but suspended: it has run nothing when it is put into the job.
        public static PROCESS_INFORMATION StartSuspended(string commandLine, string workingDirectory) {
            STARTUPINFO startup = new STARTUPINFO();
            startup.cb = Marshal.SizeOf(typeof(STARTUPINFO));
            PROCESS_INFORMATION made;
            if (!CreateProcessW(null, new StringBuilder(commandLine), IntPtr.Zero, IntPtr.Zero, true, 0x00000004,
                IntPtr.Zero, workingDirectory, ref startup, out made)) { throw new Win32Exception(Marshal.GetLastWin32Error()); }
            return made;
        }

        // 0, or the Win32 error (read here: PowerShell makes calls of its own before it could ask).
        public static int Assign(IntPtr job, IntPtr process) {
            return AssignProcessToJobObject(job, process) ? 0 : Math.Max(1, Marshal.GetLastWin32Error());
        }

        public static int Resume(IntPtr thread) {
            return ResumeThread(thread) != 0xFFFFFFFF ? 0 : Math.Max(1, Marshal.GetLastWin32Error());
        }

        public static int ExitCode(IntPtr process) {
            uint code;
            if (!GetExitCodeProcess(process, out code)) { throw new Win32Exception(Marshal.GetLastWin32Error()); }
            return unchecked((int)code);
        }
    }
}
"@
}

function Write-TickLog {
    param([string]$Text)
    Write-Host "tick: $Text"
    try {
        $folder = Split-Path -Parent $LogPath
        if ($folder -and -not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
        $line = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss", [System.Globalization.CultureInfo]::InvariantCulture) + " " + $Text
        [System.IO.File]::AppendAllText($LogPath, $line + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
    }
    catch { Write-Host "tick: the log could not be written: $($_.Exception.Message)" }
}

function Get-JobProcessIds {
    # JobObjectBasicProcessIdList (3): assigned, in-the-list (two DWORDs), then the ids, pointer-sized.
    param([IntPtr]$Job)
    $marshal = [System.Runtime.InteropServices.Marshal]
    $size = 8 + ([IntPtr]::Size * 4096)
    $buffer = $marshal::AllocHGlobal($size)
    try {
        if (-not [PagentOS.Team.TickJob]::QueryInformationJobObject($Job, 3, $buffer, $size, [IntPtr]::Zero)) { return @() }
        $listed = $marshal::ReadInt32($buffer, 4)
        $ids = New-Object System.Collections.ArrayList
        for ($index = 0; $index -lt $listed; $index++) { [void]$ids.Add([int]$marshal::ReadIntPtr($buffer, 8 + $index * [IntPtr]::Size).ToInt64()) }
        return @($ids)
    }
    finally { $marshal::FreeHGlobal($buffer) }
}

function Stop-JobLeftovers {
    # Called only after the script's own process has exited: what is still in its job is named, then the job is ended.
    param([IntPtr]$Job, [string]$Label, [int]$OwnId)
    foreach ($id in @(Get-JobProcessIds -Job $Job)) {
        if ($id -eq $OwnId) { continue }
        $name = "?"; $line = ""
        try {
            $found = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId=$id" -ErrorAction Stop
            if ($null -ne $found) { $name = [string]$found.Name; $line = [string]$found.CommandLine }
        }
        catch { }
        if ($line.Length -gt 200) { $line = $line.Substring(0, 200) }
        $entry = "pid $id $name (left by the $Label): $line"
        [void]$script:leftovers.Add($entry)
        Write-TickLog "stopping $entry"
    }
    [void][PagentOS.Team.TickJob]::TerminateJobObject($Job, 1)
}

function Invoke-ScriptWithoutJob {
    param([string]$CommandArguments, [string]$Label, [string]$Why)
    if (-not $script:noJobSaid) {
        $script:noJobSaid = $true
        Write-TickLog "no job object ($Why): what the feeder or the cycle leaves behind will not be stopped"
    }
    # Its own process only: without -Wait, which would wait for every descendant as well.
    $process = Start-Process -FilePath $powershell -ArgumentList $CommandArguments -NoNewWindow -PassThru
    [void]$process.Handle   # the exit code is read through this handle
    $process.WaitForExit()
    return [int]$process.ExitCode
}

function Invoke-Script {
    # A child process, so a script that calls `exit` or throws ends itself and not the tick.
    param([string]$Path, [string[]]$Arguments, [string]$Label)
    $all = (@("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$Path`"") + $Arguments) -join " "
    $api = $null
    $job = [IntPtr]::Zero
    try {
        Initialize-TickJobType
        $api = [PagentOS.Team.TickJob]
        if (-not [Environment]::GetEnvironmentVariable($jobFailsVariable)) { $job = $api::CreateJobObject([IntPtr]::Zero, [IntPtr]::Zero) }
    }
    catch { $job = [IntPtr]::Zero }
    if ($job -eq [IntPtr]::Zero) { return (Invoke-ScriptWithoutJob -CommandArguments $all -Label $Label -Why "CreateJobObject failed") }

    try {
        $made = $api::StartSuspended("`"$powershell`" $all", (Get-Location).ProviderPath)
        try {
            $assigned = $api::Assign($job, $made.hProcess)
            if ($assigned -ne 0 -and -not $script:noJobSaid) {
                $script:noJobSaid = $true
                Write-TickLog "no job object (AssignProcessToJobObject failed, Win32 $assigned): what the feeder or the cycle leaves behind will not be stopped"
            }
            $resumed = $api::Resume($made.hThread)
            if ($resumed -ne 0) { throw "the $Label could not be resumed (Win32 $resumed)" }
            # Its own process, not its descendants: one-second slices so the tick stays interruptible.
            while ($api::WaitForSingleObject($made.hProcess, 1000) -ne 0) { }
            $code = $api::ExitCode($made.hProcess)
            if ($assigned -eq 0) { Stop-JobLeftovers -Job $job -Label $Label -OwnId $made.dwProcessId }
            return $code
        }
        finally {
            [void]$api::CloseHandle($made.hThread)
            [void]$api::CloseHandle($made.hProcess)
        }
    }
    finally { [void]$api::CloseHandle($job) }
}

function Add-ReportLeftovers {
    # A line per leftover in the cycle's report, when the tick knows its name (-DailyId) and it exists.
    param([string]$CycleId)
    if (@($script:leftovers).Count -eq 0 -or -not $CycleId) { return }
    $report = Join-Path $ReportsRoot "$CycleId.md"
    if (-not (Test-Path -LiteralPath $report)) { return }
    try {
        # "Geride kalan surecler" with its Turkish letters: this file stays ASCII for Windows PowerShell 5.1.
        $heading = "## Geride kalan s" + [char]0x00FC + "re" + [char]0x00E7 + "ler (tick durdurdu)"
        $text = [System.IO.File]::ReadAllText($report)
        $lines = @()
        if (-not $text.Contains($heading)) { $lines += @("", $heading, "") }
        $lines += @($script:leftovers | ForEach-Object { "- $_" })
        [System.IO.File]::AppendAllText($report, (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))
    }
    catch { Write-TickLog "the report $report could not be written: $($_.Exception.Message)" }
}

$store = @()
if ($QueueUrl) {
    if (-not $QueueToken) { throw "-QueueUrl needs -QueueToken (the path of the token file)" }
    $store = @("-QueueUrl", $QueueUrl, "-QueueToken", "`"$QueueToken`"")
}

if (-not $NoFeed) {
    $feedExit = -1
    try { $feedExit = Invoke-Script -Path $feedScript -Arguments $store -Label "feeder" }
    catch { Write-Host "tick: the feeder could not be started: $($_.Exception.Message)" }
    Write-Host "tick: the feeder ended with exit $feedExit; the cycle runs whatever it said"
}

$cycleArguments = @(
    "-MaxUsd", $MaxUsd.ToString("0.##", [System.Globalization.CultureInfo]::InvariantCulture),
    "-MaxParallel", "$MaxParallel", "-CycleMinutes", "$CycleMinutes"
)
if ($Research) { $cycleArguments += "-Research" }
if ($DailyId) { $cycleArguments += "-DailyId" }
if ($ResearchEveryHours -gt 0) {
    $cycleArguments += @("-ResearchEveryHours", $ResearchEveryHours.ToString("0.##", [System.Globalization.CultureInfo]::InvariantCulture))
}
if ($Base) {
    if ($Base -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._/-]{0,80}$') { throw "-Base is a branch name" }
    $cycleArguments += @("-Base", $Base)
}
$cycleArguments += $store

# cycle.ps1 -DailyId names the cycle by the day it starts on; without it the tick cannot know the report.
$cycleId = if ($DailyId) { "d" + (Get-Date).ToString("yyyyMMdd", [System.Globalization.CultureInfo]::InvariantCulture) } else { "" }
$cycleExit = Invoke-Script -Path $cycleScript -Arguments $cycleArguments -Label "cycle"
Write-Host "tick: the cycle ended with exit $cycleExit"
Add-ReportLeftovers -CycleId $cycleId
exit $cycleExit
