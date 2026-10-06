<#
.SYNOPSIS
    Is a run working or stuck? Measured, never guessed (pm-stuck-run-check).

.DESCRIPTION
    The owner, 2026-10-04, looking at five tired faces on the Ofis: "Proje yöneticisine söyle
    arada gerçekten işte çalışıp çalışmadıklarını da kontrol etsin, iş takılmış olmasın." A
    tired face only says "long". This file reads cheap, local signs of life for a run in flight
    and keeps, per run, when the last one was seen:

      * the newest write in the run's own folders (its temp folder under run_temp_root, its
        worktree), node_modules, .venv and .git left out;
      * its output growing (when the caller can measure it);
      * the CPU its process tree used since the last look, counted only above a share of the
        time between looks (a hung test python sat at one CPU second per 30 s for two hours
        on 2026-10-04; a working suite uses a core).

    A run with no sign for run_idle_minutes (team/cycle-settings.json, 30 by default) is
    "takılmış olabilir". So is a CHILD the run's tools started (a process under a shell of the
    tree) whose own subtree showed no CPU for as long: on 2026-10-04 the worker's temp folder
    was written while its test python held the heavy test slot, stuck, for 140 minutes.

    What then (Get-TeamStuckAction): the Proje Yöneticisi's duty is handed the run once; at 90
    minutes the cycle restarts it once without asking (Restart-TeamRun, TeamRun.ps1) and the
    second time hands it to the Danışman. A run that showed life is never touched.

    Everything takes the clock and the process table as parameters: the tests drive hours of
    looks with one injected clock. Windows PowerShell 5.1, StrictMode.
#>

Set-StrictMode -Version Latest

$script:TeamRunIdleMinutesDefault = 30
$script:TeamRunIdleMinutesMax = 1440
$script:TeamRunAutoRestartMinutes = 90
# The share of one core, over the time between two looks, that counts as work.
$script:TeamLivenessMinCpuShare = 0.05
$script:TeamLivenessSkipDirectories = @("node_modules", ".venv", ".git")
# A process the run's tools started sits under one of these; conhost.exe is every console's
# companion and idles for ever by design.
$script:TeamLivenessShells = @("bash.exe", "sh.exe", "powershell.exe", "pwsh.exe", "cmd.exe")
$script:TeamLivenessIgnored = @("conhost.exe")
$script:TeamStuckActions = @("wait", "restart", "escalate")
$script:TeamStuckMaxReason = 1200

function Read-TeamRunIdleMinutes {
    <# 'run_idle_minutes' of team/cycle-settings.json: a whole number 1..1440, else 30. #>
    param([Parameter(Mandatory = $true)][string]$Path)
    $default = $script:TeamRunIdleMinutesDefault
    if (-not (Test-Path -LiteralPath $Path)) { return $default }
    try { $document = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $default }
    if ($document -isnot [System.Management.Automation.PSCustomObject]) { return $default }
    $property = $document.PSObject.Properties["run_idle_minutes"]
    if ($null -eq $property) { return $default }
    $value = $property.Value
    if (-not ($value -is [int] -or $value -is [long])) { return $default }
    if ($value -lt 1 -or $value -gt $script:TeamRunIdleMinutesMax) { return $default }
    return [int]$value
}

if (-not ("PagentOS.Team.NewestWrite" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
namespace PagentOS.Team {
    public static class NewestWrite {
        // The walk runs on a pool thread and is waited for at most `millis`: on 2026-10-06 the
        // cycle froze three times inside one directory enumeration (a worktree's __pycache__),
        // which no PowerShell timeout can interrupt. A walk past its time is abandoned; its
        // answer so far is not used (TimedOut is said instead).
        public static DateTime Find(string path, string[] skip, int millis, out bool timedOut) {
            var skipSet = new HashSet<string>(skip ?? new string[0], StringComparer.OrdinalIgnoreCase);
            var walk = Task.Run(() => Walk(new DirectoryInfo(path), skipSet));
            timedOut = !walk.Wait(millis);
            return timedOut ? DateTime.MinValue : walk.Result;
        }
        static DateTime Walk(DirectoryInfo root, HashSet<string> skip) {
            var newest = DateTime.MinValue;
            var pending = new Stack<DirectoryInfo>();
            pending.Push(root);
            while (pending.Count > 0) {
                var dir = pending.Pop();
                try { if (dir.LastWriteTimeUtc > newest) newest = dir.LastWriteTimeUtc; } catch { }
                FileSystemInfo[] entries;
                try { entries = dir.GetFileSystemInfos(); } catch { continue; }
                foreach (var entry in entries) {
                    var sub = entry as DirectoryInfo;
                    if (sub != null) {
                        if (skip.Contains(sub.Name)) continue;
                        // A link is not followed: it may point anywhere, a cycle included.
                        if ((sub.Attributes & FileAttributes.ReparsePoint) != 0) continue;
                        pending.Push(sub);
                    } else {
                        try { if (entry.LastWriteTimeUtc > newest) newest = entry.LastWriteTimeUtc; } catch { }
                    }
                }
            }
            return newest;
        }
    }
}
"@
}

function Get-TeamNewestWrite {
    <#
    .SYNOPSIS
        The newest last-write time (UTC) of a folder, its subfolders and their files, the
        skipped folders left out; $null when there is nothing to read, or when the walk took
        longer than -TimeoutMilliseconds (then it is no sign either way, and never a hang).
    .DESCRIPTION
        A folder's own time counts: a file made or deleted in it moves it, and a test suite
        does both all the time.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string[]]$Skip = $script:TeamLivenessSkipDirectories,
        [int]$TimeoutMilliseconds = 5000
    )
    if (-not $Path -or -not [System.IO.Directory]::Exists($Path)) { return $null }
    $timedOut = $false
    $newest = [PagentOS.Team.NewestWrite]::Find($Path, [string[]]@($Skip), $TimeoutMilliseconds, [ref]$timedOut)
    if ($timedOut -or $newest -eq [datetime]::MinValue) { return $null }
    return [datetime]::SpecifyKind($newest, [System.DateTimeKind]::Utc)
}

function Get-TeamProcessTable {
    <# Every process of the machine: Id, ParentId, Name, CpuSeconds, Created (UTC). One query. #>
    $rows = New-Object System.Collections.ArrayList
    foreach ($p in @(Get-CimInstance -ClassName Win32_Process -ErrorAction Stop)) {
        $created = $null
        if ($null -ne $p.CreationDate) { $created = ([datetime]$p.CreationDate).ToUniversalTime() }
        $cpu = ([double]$p.KernelModeTime + [double]$p.UserModeTime) / 10000000.0
        [void]$rows.Add([pscustomobject]@{ Id = [int]$p.ProcessId; ParentId = [int]$p.ParentProcessId; Name = [string]$p.Name; CpuSeconds = $cpu; Created = $created })
    }
    return @($rows.ToArray())
}

function Get-TeamDescendants {
    <#
    .SYNOPSIS
        The processes under a root, the root left out, each with Depth and Shelled (a shell
        is on its line below the root, itself included). A "child" that started before its
        parent is a reused pid, not a child.
    #>
    param([AllowEmptyCollection()][object[]]$ProcessTable = @(), [Parameter(Mandatory = $true)][int]$RootProcessId)
    $byParent = @{}
    $root = $null
    foreach ($row in @($ProcessTable)) {
        if ($row.Id -eq $RootProcessId) { $root = $row }
        $key = [int]$row.ParentId
        if (-not $byParent.ContainsKey($key)) { $byParent[$key] = New-Object System.Collections.ArrayList }
        [void]$byParent[$key].Add($row)
    }
    $found = New-Object System.Collections.ArrayList
    if ($null -eq $root) { return @() }
    $seen = @{ $RootProcessId = $true }
    $visit = {
        param($Row, [int]$Depth, [bool]$Shelled)
        $parentKey = [int]$Row.Id
        if (-not $byParent.ContainsKey($parentKey)) { return }
        foreach ($child in @($byParent[$parentKey])) {
            if ($seen.ContainsKey([int]$child.Id)) { continue }
            if ($null -ne $Row.Created -and $null -ne $child.Created -and $child.Created -lt $Row.Created) { continue }
            $seen[[int]$child.Id] = $true
            $shelled = $Shelled -or (@($script:TeamLivenessShells) -contains ([string]$child.Name).ToLowerInvariant())
            [void]$found.Add([pscustomobject]@{ Id = [int]$child.Id; ParentId = [int]$child.ParentId; Name = [string]$child.Name; CpuSeconds = [double]$child.CpuSeconds; Created = $child.Created; Depth = $Depth + 1; Shelled = $shelled })
            & $visit $child ($Depth + 1) $shelled
        }
    }
    & $visit $root 0 $false
    return @($found.ToArray())
}

function New-TeamLivenessState {
    <# What the cycle keeps for one run between looks. -Since: when the run started. #>
    param([Parameter(Mandatory = $true)][datetime]$Since)
    return @{
        LastActivity = $Since.ToUniversalTime()
        LastLook     = $null
        OutputLength = [long]-1
        Cpu          = @{}   # pid -> CPU seconds at the last look (the root and its descendants)
        Children     = @{}   # pid -> @{ LastActivity; Name; Created }
    }
}

function Get-TeamCpuDelta {
    <# The CPU the given processes used since the last look: a process new since then counts whole. #>
    param([object[]]$Rows, [hashtable]$Previous)
    $sum = 0.0
    foreach ($row in @($Rows)) {
        $before = 0.0
        if ($Previous.ContainsKey([int]$row.Id)) { $before = [double]$Previous[[int]$row.Id] }
        $sum += [Math]::Max(0.0, [double]$row.CpuSeconds - $before)
    }
    return $sum
}

function Update-TeamRunLiveness {
    <#
    .SYNOPSIS
        One look at one run. Returns LastActivityAt (the team's timestamp), IdleMinutes (whole
        minutes), Stuck, Signs (what showed life this time) and StuckChildren (pid, name,
        idle_minutes: the topmost tool process whose subtree showed no CPU for the bound).
    #>
    param(
        [Parameter(Mandatory = $true)][hashtable]$State,
        [Parameter(Mandatory = $true)][datetime]$Now,
        [string[]]$Paths = @(),
        [long]$OutputLength = -1,
        [int]$RootProcessId = 0,
        [AllowEmptyCollection()][object[]]$ProcessTable = @(),
        [int]$IdleMinutes = $script:TeamRunIdleMinutesDefault,
        [double]$MinCpuShare = $script:TeamLivenessMinCpuShare
    )
    $now = $Now.ToUniversalTime()
    $signs = New-Object System.Collections.ArrayList
    $elapsed = if ($null -ne $State.LastLook) { ($now - [datetime]$State.LastLook).TotalSeconds } else { 0.0 }

    foreach ($path in @($Paths | Where-Object { $_ })) {
        $written = Get-TeamNewestWrite -Path $path
        if ($null -eq $written) { continue }
        if ($written -gt $now) { $written = $now }
        if ($written -gt [datetime]$State.LastActivity) { $State.LastActivity = $written; [void]$signs.Add("write") }
    }
    if ($OutputLength -ge 0) {
        if ($OutputLength -gt [long]$State.OutputLength) { $State.LastActivity = $now; [void]$signs.Add("output") }
        $State.OutputLength = $OutputLength
    }

    $stuckChildren = New-Object System.Collections.ArrayList
    if ($RootProcessId -gt 0) {
        $root = @($ProcessTable | Where-Object { $_.Id -eq $RootProcessId })
        $descendants = @(Get-TeamDescendants -ProcessTable $ProcessTable -RootProcessId $RootProcessId)
        $tree = @($root) + @($descendants)
        if ($null -ne $State.LastLook -and $elapsed -gt 0 -and @($root).Count -gt 0) {
            $used = Get-TeamCpuDelta -Rows $tree -Previous $State.Cpu
            if ($used -ge $MinCpuShare * $elapsed) { $State.LastActivity = $now; [void]$signs.Add("cpu") }
        }
        # Each tool process: its own subtree's CPU since the last look.
        $children = @{}
        $candidates = @($descendants | Where-Object { $_.Shelled -and @($script:TeamLivenessIgnored) -notcontains $_.Name.ToLowerInvariant() })
        foreach ($candidate in $candidates) {
            $subtree = @($candidate) + @(Get-TeamDescendants -ProcessTable $descendants -RootProcessId $candidate.Id)
            $known = $State.Children[[int]$candidate.Id]
            if ($null -eq $known) {
                $known = @{ LastActivity = $now; Name = $candidate.Name }
            }
            elseif ($elapsed -gt 0 -and (Get-TeamCpuDelta -Rows $subtree -Previous $State.Cpu) -ge $MinCpuShare * $elapsed) {
                $known.LastActivity = $now
            }
            $children[[int]$candidate.Id] = $known
        }
        $idleIds = @{}
        foreach ($candidate in $candidates) {
            $minutes = [int][Math]::Floor(($now - [datetime]$children[[int]$candidate.Id].LastActivity).TotalMinutes)
            if ($minutes -ge $IdleMinutes) { $idleIds[[int]$candidate.Id] = $minutes }
        }
        foreach ($candidate in ($candidates | Sort-Object Depth, Id)) {
            if (-not $idleIds.ContainsKey([int]$candidate.Id)) { continue }
            if ($idleIds.ContainsKey([int]$candidate.ParentId)) { continue }  # its parent is the one to name
            [void]$stuckChildren.Add([pscustomobject]@{ pid = [int]$candidate.Id; name = [string]$candidate.Name; idle_minutes = [int]$idleIds[[int]$candidate.Id] })
        }
        $State.Children = $children
        $cpu = @{}
        foreach ($row in $tree) { $cpu[[int]$row.Id] = [double]$row.CpuSeconds }
        $State.Cpu = $cpu
    }
    $State.LastLook = $now

    $idle = [int][Math]::Max(0, [Math]::Floor(($now - [datetime]$State.LastActivity).TotalMinutes))
    return [pscustomobject]@{
        LastActivityAt = ([datetime]$State.LastActivity).ToString("yyyy-MM-ddTHH:mm:ssZ", [System.Globalization.CultureInfo]::InvariantCulture)
        IdleMinutes    = $idle
        Stuck          = ($idle -ge $IdleMinutes)
        Signs          = @($signs.ToArray())
        StuckChildren  = @($stuckChildren.ToArray())
    }
}

function Get-TeamStuckAction {
    <#
    .SYNOPSIS
        What happens to a run idle this long: none (it is alive), duty (hand it to the Proje
        Yöneticisi, once), wait (it is with the PM), restart (90 minutes: once, without asking),
        escalate (90 minutes again after a restart: the Danışman's).
    .DESCRIPTION
        -StuckChildren (Update-TeamRunLiveness's) climbs the same ladder: the run is as idle as
        its most idle stuck child. On 2026-10-04 the worker wrote while its test python sat
        idle for two hours, so the run's own minutes alone never moved.
    #>
    param(
        [Parameter(Mandatory = $true)][int]$IdleMinutes,
        [AllowEmptyCollection()][object[]]$StuckChildren = @(),
        [int]$Bound = $script:TeamRunIdleMinutesDefault,
        [int]$AutoMinutes = $script:TeamRunAutoRestartMinutes,
        [int]$Restarts = 0,
        [bool]$Handed = $false
    )
    foreach ($child in @($StuckChildren | Where-Object { $null -ne $_ })) {
        $minutes = [int]$child.idle_minutes
        if ($minutes -gt $IdleMinutes) { $IdleMinutes = $minutes }
    }
    if ($IdleMinutes -lt $Bound) { return "none" }
    if ($IdleMinutes -ge $AutoMinutes) {
        if ($Restarts -lt 1) { return "restart" }
        return "escalate"
    }
    if (-not $Handed) { return "duty" }
    return "wait"
}

function Test-TeamStuckDecisions {
    <#
    .SYNOPSIS
        Every reason the PM's stuck-run decisions are refused, as sentences; empty when sound.
        Each is { run: "<task>/<role>" one of -Listed, once; action: wait | restart | escalate;
        reason: not blank, at most 1200 characters }.
    #>
    param($Decisions, [string[]]$Listed = @())
    $problems = New-Object System.Collections.ArrayList
    $seen = @{}
    foreach ($item in @($Decisions | Where-Object { $null -ne $_ })) {
        if ($item -isnot [System.Management.Automation.PSCustomObject]) { [void]$problems.Add("an entry is not a decision object"); continue }
        $runProperty = $item.PSObject.Properties["run"]
        $run = if ($null -ne $runProperty) { [string]$runProperty.Value } else { "" }
        $label = if ($run) { $run } else { "(a decision without a run)" }
        if (@($Listed) -cnotcontains $run) { [void]$problems.Add("${label}: not one of the stuck runs this duty was given") }
        if ($run -and $seen.ContainsKey($run)) { [void]$problems.Add("${label}: more than one decision for one run") }
        $seen[$run] = $true
        $actionProperty = $item.PSObject.Properties["action"]
        $action = if ($null -ne $actionProperty -and $actionProperty.Value -is [string]) { [string]$actionProperty.Value } else { "" }
        if ($script:TeamStuckActions -cnotcontains $action) {
            $shown = if ($null -ne $actionProperty) { [string]$actionProperty.Value } else { "" }
            [void]$problems.Add("${label}: '$shown' is not an action (wait, restart, escalate)")
        }
        $reasonProperty = $item.PSObject.Properties["reason"]
        $reason = if ($null -ne $reasonProperty -and $reasonProperty.Value -is [string]) { ([string]$reasonProperty.Value).Trim() } else { "" }
        if (-not $reason) { [void]$problems.Add("${label}: the reason is empty") }
        elseif ($reason.Length -gt $script:TeamStuckMaxReason) { [void]$problems.Add("${label}: the reason is $($reason.Length) characters; at most $script:TeamStuckMaxReason") }
    }
    return @($problems.ToArray())
}
