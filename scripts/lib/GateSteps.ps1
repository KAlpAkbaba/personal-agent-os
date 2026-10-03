<#
.SYNOPSIS
    Runs a group of the gate's steps side by side: each step its own powershell.exe, at most N
    at once, each with a deadline, stdout and stderr in two files per step.

.DESCRIPTION
    team/plans/gate-faster-adr.md. scripts/quality-gate.ps1 ran every step strictly one after
    another although most of its PowerShell suites touch nothing the others touch; the gate is
    the team's narrowest point. The gate builds its group FROM its own Invoke-Step blocks (each
    keeps its name and its Assert-ExitCode) and hands the list to Invoke-GateStepGroup.

    A step's pass or fail is its exit code, as Assert-ExitCode reads it in the gate: 0 passes,
    anything else fails. What a step writes to stderr is kept and printed, and is never a
    failure by itself (native stderr is not an error). A step past its deadline is killed
    with its whole process tree and is a failed step with the outcome TIMEOUT - never a hang.

    Steps that share something (a port, a folder, a machine-wide resource) carry the same Lane:
    a lane runs its steps one after another, in the listed order, while other lanes and the
    lane-less steps run beside it.

    Windows PowerShell 5.1. Dot-sourcing this file defines functions and a few `$script:`
    settings and changes nothing else in the caller; each function sets StrictMode for its
    own scope.
#>

#: How many steps run at once unless the caller says otherwise (quality-gate.ps1 -GateMaxParallel).
$script:GateStepMaxParallel = 3
#: A step's deadline unless it names its own: a hang guard, far above the longest suite measured
#: under load (team-integrate, about 25 minutes on a quiet machine, two to six times that beside
#: the owner's VR session).
$script:GateStepDeadlineSeconds = 7200
#: The outcome of a step killed at its deadline (and the word its FAILED line carries).
$script:GateStepTimeoutWord = "TIMEOUT"
#: How often the group looks at its steps.
$script:GateStepPollMilliseconds = 250
#: After a step's exit, how long its logs may take to drain (a process it left behind can hold
#: the pipe open; the group does not wait for that).
$script:GateStepLogDrainSeconds = 30

function New-GateStep {
    <#
        One step of a group: its name (the gate's step name), the script powershell.exe runs
        with -File, that script's arguments, what Assert-ExitCode calls it (the gate's FAILED
        line), its lane ("" = none) and its own deadline (0 = the default).
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Script,
        [AllowEmptyCollection()][string[]]$Arguments = @(),
        [string]$What = "",
        [string]$Lane = "",
        [int]$DeadlineSeconds = 0
    )
    Set-StrictMode -Version Latest
    return [pscustomobject]@{
        Name            = $Name
        Script          = $Script
        Arguments       = @($Arguments)
        What            = $(if ($What) { $What } else { $Name })
        Lane            = $Lane
        DeadlineSeconds = $DeadlineSeconds
    }
}

function ConvertTo-GateArgumentLine {
    <# One command line by the CommandLineToArgvW rules (quote what has a space or a quote). #>
    param([AllowEmptyCollection()][AllowEmptyString()][string[]]$Arguments = @())
    Set-StrictMode -Version Latest
    $parts = foreach ($a in @($Arguments)) {
        if ($a -ne "" -and $a -notmatch '[\s"]') { $a; continue }
        $sb = New-Object System.Text.StringBuilder
        [void]$sb.Append('"')
        $slashes = 0
        foreach ($ch in $a.ToCharArray()) {
            if ($ch -eq '\') { $slashes++; continue }
            if ($ch -eq '"') { [void]$sb.Append('\' * (2 * $slashes + 1)); [void]$sb.Append('"') }
            else { if ($slashes) { [void]$sb.Append('\' * $slashes) }; [void]$sb.Append($ch) }
            $slashes = 0
        }
        if ($slashes) { [void]$sb.Append('\' * (2 * $slashes)) }
        [void]$sb.Append('"')
        $sb.ToString()
    }
    return (@($parts) -join " ")
}

function Stop-GateStepTree {
    <#
        Kills a step and everything it started (taskkill /T /F walks the parent links while the
        step is still alive), then waits for the step itself to be gone.
    #>
    param([Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process)
    Set-StrictMode -Version Latest
    $taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $taskkill
    $psi.Arguments = "/PID $($Process.Id) /T /F"
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $kill = [System.Diagnostics.Process]::Start($psi)
    $null = $kill.StandardOutput.ReadToEndAsync()
    $null = $kill.StandardError.ReadToEndAsync()
    [void]$kill.WaitForExit(60000)
    # A kill is not an exit: HasExited lags it under load, so wait for the step itself.
    if (-not $Process.WaitForExit(60000)) { try { $Process.Kill() } catch { } }
}

function Close-GateStepLog {
    <#
        After a step's exit: lets its two copies finish and closes the files. Bounded: a process
        the step left behind can hold the pipe open for ever, and the group does not wait for
        it (what it writes after this is lost, the step's own output is not).
    #>
    param([Parameter(Mandatory = $true)]$State)
    Set-StrictMode -Version Latest
    foreach ($copy in @($State.OutCopy, $State.ErrCopy)) {
        if ($null -eq $copy) { continue }
        try { [void]$copy.Wait($script:GateStepLogDrainSeconds * 1000) } catch { }
    }
    foreach ($file in @($State.OutFile, $State.ErrFile)) {
        if ($null -eq $file) { continue }
        try { $file.Flush(); $file.Dispose() } catch { }
    }
}

function Invoke-GateStepGroup {
    <#
    .SYNOPSIS
        Runs the steps, at most MaxParallel at once, and returns one result per step in the
        LISTED order: Name, What, Lane, Outcome (PASS / FAIL / TIMEOUT), ExitCode, Seconds,
        StdoutPath, StderrPath.

    .DESCRIPTION
        Steps start in the listed order as places free up; a step whose lane is busy waits for
        it and the next step that can start does. Each step is
        `powershell.exe -NoProfile -ExecutionPolicy Bypass -File <script> <arguments>` with
        stdout and stderr each drained into a file of its own as it is written (never merged,
        and no pipe is left to fill) and stdin closed. A step past its deadline is killed with
        its process tree. The group always returns.
    #>
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Steps,
        [Parameter(Mandatory = $true)][string]$LogRoot,
        [int]$MaxParallel = $script:GateStepMaxParallel,
        [int]$DeadlineSeconds = $script:GateStepDeadlineSeconds,
        [string]$PowerShellPath = (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe")
    )
    Set-StrictMode -Version Latest
    if ($MaxParallel -lt 1) { throw "MaxParallel must be at least 1 (got $MaxParallel)" }
    New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null

    $states = New-Object System.Collections.ArrayList
    $index = 0
    foreach ($step in @($Steps)) {
        $index++
        $slug = ($step.Name -replace '[^A-Za-z0-9]+', '-').Trim('-')
        if ($slug.Length -gt 60) { $slug = $slug.Substring(0, 60) }
        $deadline = if ($step.DeadlineSeconds -gt 0) { $step.DeadlineSeconds } else { $DeadlineSeconds }
        [void]$states.Add([pscustomobject]@{
                Step       = $step
                Deadline   = $deadline
                Stdout     = (Join-Path $LogRoot ("{0:D2}-{1}.stdout.log" -f $index, $slug))
                Stderr     = (Join-Path $LogRoot ("{0:D2}-{1}.stderr.log" -f $index, $slug))
                Process    = $null
                OutFile    = $null
                ErrFile    = $null
                OutCopy    = $null
                ErrCopy    = $null
                Clock      = $null
                State      = "waiting"
                ExitCode   = $null
                TimedOut   = $false
                Seconds    = 0.0
            })
    }

    while ($true) {
        # Reap: what ended, and what is past its deadline.
        foreach ($s in @($states | Where-Object { $_.State -eq "running" })) {
            if ($s.Process.HasExited) {
                $s.Process.WaitForExit()
                $s.ExitCode = $s.Process.ExitCode
                $s.Clock.Stop()
                $s.Seconds = [math]::Round($s.Clock.Elapsed.TotalSeconds, 1)
                Close-GateStepLog -State $s
                $s.State = "ended"
            }
            elseif ($s.Clock.Elapsed.TotalSeconds -gt $s.Deadline) {
                Stop-GateStepTree -Process $s.Process
                $s.TimedOut = $true
                $s.ExitCode = $(if ($s.Process.HasExited) { $s.Process.ExitCode } else { -1 })
                $s.Clock.Stop()
                $s.Seconds = [math]::Round($s.Clock.Elapsed.TotalSeconds, 1)
                Close-GateStepLog -State $s
                $s.State = "ended"
            }
        }

        # Start: in the listed order, as long as there is room and the step's lane is free.
        $running = @($states | Where-Object { $_.State -eq "running" })
        $busyLanes = @($running | Where-Object { $_.Step.Lane } | ForEach-Object { $_.Step.Lane })
        $room = $MaxParallel - @($running).Count
        foreach ($s in @($states | Where-Object { $_.State -eq "waiting" })) {
            if ($room -le 0) { break }
            if ($s.Step.Lane -and ($busyLanes -contains $s.Step.Lane)) { continue }
            $psi = New-Object System.Diagnostics.ProcessStartInfo
            $psi.FileName = $PowerShellPath
            $psi.Arguments = ConvertTo-GateArgumentLine -Arguments (@("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $s.Step.Script) + @($s.Step.Arguments))
            $psi.UseShellExecute = $false
            $psi.RedirectStandardOutput = $true
            $psi.RedirectStandardError = $true
            $psi.RedirectStandardInput = $true
            $psi.CreateNoWindow = $true
            $s.Clock = [System.Diagnostics.Stopwatch]::StartNew()
            $s.Process = [System.Diagnostics.Process]::Start($psi)
            $s.Process.StandardInput.Close()
            # Each stream is drained into its own file by .NET on a pool thread, so neither pipe
            # fills and blocks the step. Not Start-Process -RedirectStandardOutput: measured on
            # this machine it writes every line through to the disk - about 120 lines a second
            # on E: - and a chatty suite would be timed by its disk.
            $share = [System.IO.FileShare]::ReadWrite
            $s.OutFile = New-Object System.IO.FileStream($s.Stdout, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, $share)
            $s.ErrFile = New-Object System.IO.FileStream($s.Stderr, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, $share)
            $s.OutCopy = $s.Process.StandardOutput.BaseStream.CopyToAsync($s.OutFile)
            $s.ErrCopy = $s.Process.StandardError.BaseStream.CopyToAsync($s.ErrFile)
            $s.State = "running"
            $room--
            if ($s.Step.Lane) { $busyLanes += $s.Step.Lane }
        }

        if (@($states | Where-Object { $_.State -ne "ended" }).Count -eq 0) { break }
        Start-Sleep -Milliseconds $script:GateStepPollMilliseconds
    }

    $results = foreach ($s in $states) {
        $outcome = if ($s.TimedOut) { $script:GateStepTimeoutWord } elseif ($s.ExitCode -eq 0) { "PASS" } else { "FAIL" }
        [pscustomobject]@{
            Name       = $s.Step.Name
            What       = $s.Step.What
            Lane       = $s.Step.Lane
            Outcome    = $outcome
            ExitCode   = $s.ExitCode
            Seconds    = $s.Seconds
            Deadline   = $s.Deadline
            StdoutPath = $s.Stdout
            StderrPath = $s.Stderr
        }
    }
    return @($results)
}

function Test-GateStepGroupPassed {
    <# A group passed when every one of its steps did. #>
    param([AllowEmptyCollection()][object[]]$Results = @())
    Set-StrictMode -Version Latest
    return (@(@($Results) | Where-Object { $_.Outcome -ne "PASS" }).Count -eq 0)
}
