<#
.SYNOPSIS
    The home PC's pulse: free memory, the %TEMP% item count, free disk, and the processes a
    finished run left behind - read, judged, and put into one Turkish line.

.DESCRIPTION
    Two halves, kept apart so the rules are tested without a machine:

      * READERS (Get-TeamHostMemory, Measure-TeamTempItems, Get-TeamDriveFree,
        Get-TeamProcessSnapshot) are the only functions that touch the machine. Each takes its
        source as a parameter (a script block, or the folder) so a test injects it. They only
        read: the TEMP count is top level only, never recursive, never opens a file, and stops
        when its time budget runs out (the count so far is kept and shown as "<n>+"; only a
        count above max_temp_items fails). A TEMP path that does not exist comes back Missing.
      * PURE functions (Get-TeamHostPulse, Get-TeamOrphanTree, Test-TeamHostPulse,
        Get-TeamPulseThresholds, Compare-TeamTempGrowth, Format-TeamPulseLine) do no I/O.

    Thresholds compare strictly in one direction: below a minimum or above a maximum fails,
    equal passes. Orphans never fail the check (they are closed, not waited on); they are named
    in the line. An orphan is a live descendant of a RECORDED run root whose run has finished,
    found by a ParentProcessId walk - never a process selected by its name. A run root is
    { Pid, TaskId, Finished, StartedAt, FinishedAt }; the root's children count only between
    StartedAt and FinishedAt, and a finished root without FinishedAt yields nothing.

    Nothing here is wired into the cycle; see team/plans/host-pulse-rules-adr.md for where
    the wiring card calls what. Dot-sourceable, no top-level side effects.
    Windows PowerShell 5.1, StrictMode.
#>

function Get-TeamPulseValue {
    # A property of a PSCustomObject or a key of a hashtable; $Default when absent.
    param($Object, [string]$Name, $Default = $null)
    if ($null -eq $Object) { return $Default }
    if ($Object -is [System.Collections.IDictionary]) {
        if ($Object.Contains($Name)) { return $Object[$Name] }
        return $Default
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $Default }
    return $property.Value
}

function ConvertTo-TeamPulseUtc {
    # A CIM DateTime (local kind) or an ISO string -> UTC DateTime; $null when unknown.
    param($Value)
    if ($null -eq $Value) { return $null }
    if ($Value -is [datetime]) { return $Value.ToUniversalTime() }
    if ($Value -is [System.DateTimeOffset]) { return $Value.UtcDateTime }
    $parsed = [System.DateTimeOffset]::MinValue
    $styles = [System.Globalization.DateTimeStyles]::AssumeUniversal
    if ([System.DateTimeOffset]::TryParse([string]$Value, [System.Globalization.CultureInfo]::InvariantCulture, $styles, [ref]$parsed)) {
        return $parsed.UtcDateTime
    }
    return $null
}

function Get-TeamPulseGb {
    # Whole gigabytes, rounded down: a drive with 19.6 GB free never reads "20 GB".
    param([double]$Bytes)
    return [int64][math]::Floor($Bytes / 1GB)
}

# ---------------------------------------------------------------------------------- READERS

function Get-TeamHostMemory {
    param([scriptblock]$Source = { Get-CimInstance -ClassName Win32_OperatingSystem })
    Set-StrictMode -Version Latest
    $os = & $Source
    $free = [double](Get-TeamPulseValue $os 'FreePhysicalMemory' 0) * 1KB
    $total = [double](Get-TeamPulseValue $os 'TotalVisibleMemorySize' 0) * 1KB
    $percent = 0.0
    if ($total -gt 0) { $percent = [math]::Round(100.0 * $free / $total, 1) }
    return [pscustomobject]@{ FreeBytes = [int64]$free; TotalBytes = [int64]$total; FreePercent = $percent }
}

function Measure-TeamTempItems {
    # The prefix is taken from the first -PrefixSample names only: a per-name prefix costs ~8 us
    # in PowerShell 5.1 (~114 000 names/s), a bare MoveNext ~2 us (~540 000/s, measured on the home
    # PC 2026-10-03), so only a sampled prefix lets the 2 s budget reach max_temp_items. The clock
    # is read every 256 names. A path that does not exist comes back Missing, Count 0.
    param([string]$Path = $env:TEMP, [int]$BudgetMs = 2000, [int]$PrefixSample = 20000)
    Set-StrictMode -Version Latest
    $count = 0
    $tooLarge = $false
    $prefixes = @{}
    if (-not [System.IO.Directory]::Exists($Path)) {
        return [pscustomobject]@{ Count = 0; TooLarge = $false; TopPrefix = ''; Missing = $true }
    }
    $letters = [regex]'^\p{L}+'
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    $entries = [System.IO.Directory]::EnumerateFileSystemEntries($Path).GetEnumerator()
    try {
        if ($BudgetMs -le 0) { $tooLarge = $entries.MoveNext() }
        while (-not $tooLarge -and $entries.MoveNext()) {
            $count++
            if ($count -le $PrefixSample) {
                $prefix = $letters.Match([System.IO.Path]::GetFileName($entries.Current)).Value
                if ($prefix) { $prefixes[$prefix] = 1 + [int]$prefixes[$prefix] }
            }
            if (($count -band 255) -eq 0 -and $clock.ElapsedMilliseconds -ge $BudgetMs) { $tooLarge = $true }
        }
    } finally {
        $entries.Dispose()
    }
    $top = ''
    $best = 0
    foreach ($key in @($prefixes.Keys | Sort-Object)) {
        if ($prefixes[$key] -gt $best) { $best = $prefixes[$key]; $top = $key }
    }
    return [pscustomobject]@{ Count = $count; TooLarge = $tooLarge; TopPrefix = $top; Missing = $false }
}

function Get-TeamDriveFree {
    param(
        [string[]]$Drives = @('C', 'E'),
        [scriptblock]$Source = {
            param($drive)
            $info = New-Object System.IO.DriveInfo ($drive + ':')
            if (-not $info.IsReady) { return $null }
            return $info.AvailableFreeSpace
        }
    )
    Set-StrictMode -Version Latest
    foreach ($drive in $Drives) {
        $letter = ([string]$drive).TrimEnd(':', '\').ToUpperInvariant()
        $free = $null
        try { $free = & $Source $letter } catch { $free = $null }
        if ($null -eq $free) {
            [pscustomobject]@{ Drive = $letter; FreeBytes = $null; Missing = $true }
        } else {
            [pscustomobject]@{ Drive = $letter; FreeBytes = [int64]$free; Missing = $false }
        }
    }
}

function Get-TeamProcessSnapshot {
    param([scriptblock]$Source = { Get-CimInstance -ClassName Win32_Process })
    Set-StrictMode -Version Latest
    foreach ($process in @(& $Source)) {
        [pscustomobject]@{
            ProcessId       = [int](Get-TeamPulseValue $process 'ProcessId' 0)
            ParentProcessId = [int](Get-TeamPulseValue $process 'ParentProcessId' 0)
            Name            = [string](Get-TeamPulseValue $process 'Name' '')
            WorkingSetBytes = [int64](Get-TeamPulseValue $process 'WorkingSetSize' 0)
            CreationDate    = Get-TeamPulseValue $process 'CreationDate' $null
        }
    }
}

# ----------------------------------------------------------------------------------- PURE

function Get-TeamRunTree {
    # Every live descendant of one recorded root, by a ParentProcessId walk. A child must be
    # created at or after its parent, and the root's creation is taken as its StartedAt, so
    # every descendant is created at or after the run started: a ParentProcessId can name a
    # pid that was reused, and a process older than the run is never the run's (pid-reuse guard).
    # The root pid itself is not returned. A process with no known CreationDate is skipped.
    # The root is the one pid the walk starts from without seeing it alive, so it is the one a
    # later process can take over after the root died: a live process holding the root's pid
    # and created AFTER StartedAt is a reuser, and only the root's children created before the
    # reuser count - everything created from then on under that pid is the reuser's (inspector
    # 2026-10-03: the owner's Chrome took a finished run's pid and its renderer was returned).
    # A reuser that has itself exited leaves no holder to see (Danışman 2026-10-03 21:00: a launcher
    # took the dead root's pid, started Chrome and exited), so a FINISHED root also needs its
    # FinishedAt: a root child counts only when created at or after StartedAt and strictly before
    # FinishedAt. A finished root without FinishedAt yields nothing - in doubt a process is the
    # owner's. A running root (Finished false, used for Largest only) is bounded by a holder alone.
    # Every deeper node is a live process of the snapshot, so its own creation bounds its children.
    param([object[]]$Processes, $Root)
    $started = ConvertTo-TeamPulseUtc (Get-TeamPulseValue $Root 'StartedAt' $null)
    $rootPid = [int](Get-TeamPulseValue $Root 'Pid' 0)
    if ($null -eq $started -or $rootPid -le 0) { return }
    $rootBefore = $null
    if ([bool](Get-TeamPulseValue $Root 'Finished' $false)) {
        $rootBefore = ConvertTo-TeamPulseUtc (Get-TeamPulseValue $Root 'FinishedAt' $null)
        if ($null -eq $rootBefore) { return }
    }
    foreach ($process in $Processes) {
        if ([int](Get-TeamPulseValue $process 'ProcessId' 0) -ne $rootPid) { continue }
        $holderCreated = ConvertTo-TeamPulseUtc (Get-TeamPulseValue $process 'CreationDate' $null)
        if ($null -eq $holderCreated) { return }  # cannot tell the root from a reuser: close nothing
        if ($holderCreated -gt $started -and ($null -eq $rootBefore -or $holderCreated -lt $rootBefore)) {
            $rootBefore = $holderCreated
        }
    }
    $visited = @{ $rootPid = $true }
    $queue = New-Object System.Collections.Queue
    $queue.Enqueue(@($rootPid, $started, $rootBefore))
    while ($queue.Count -gt 0) {
        $node = $queue.Dequeue()
        $parentPid = [int]$node[0]
        $parentCreated = [datetime]$node[1]
        $before = $node[2]
        foreach ($process in $Processes) {
            if ([int](Get-TeamPulseValue $process 'ParentProcessId' 0) -ne $parentPid) { continue }
            $childPid = [int](Get-TeamPulseValue $process 'ProcessId' 0)
            if ($visited.ContainsKey($childPid)) { continue }
            $created = ConvertTo-TeamPulseUtc (Get-TeamPulseValue $process 'CreationDate' $null)
            if ($null -eq $created) { continue }
            if ($created -lt $parentCreated) { continue }
            if ($null -ne $before -and $created -ge [datetime]$before) { continue }
            $visited[$childPid] = $true
            $process
            $queue.Enqueue(@($childPid, $created, $null))
        }
    }
}

function Get-TeamOrphanTree {
    param([object[]]$Processes = @(), [object[]]$RunRoots = @())
    Set-StrictMode -Version Latest
    $seen = @{}
    foreach ($root in @($RunRoots)) {
        if (-not [bool](Get-TeamPulseValue $root 'Finished' $false)) { continue }
        foreach ($process in @(Get-TeamRunTree -Processes @($Processes) -Root $root)) {
            $processId = [int](Get-TeamPulseValue $process 'ProcessId' 0)
            if ($seen.ContainsKey($processId)) { continue }
            $seen[$processId] = $true
            $process
        }
    }
}

function Get-TeamLargestRunProcess {
    # The biggest process inside any recorded run tree: a running root's own process and every
    # root's descendants.
    param([object[]]$Processes, [object[]]$RunRoots)
    $largest = $null
    foreach ($root in @($RunRoots)) {
        $taskId = [string](Get-TeamPulseValue $root 'TaskId' '')
        $members = @(Get-TeamRunTree -Processes $Processes -Root $root)
        if (-not [bool](Get-TeamPulseValue $root 'Finished' $false)) {
            $rootPid = [int](Get-TeamPulseValue $root 'Pid' 0)
            $members += @($Processes | Where-Object { [int](Get-TeamPulseValue $_ 'ProcessId' 0) -eq $rootPid })
        }
        foreach ($process in $members) {
            $size = [int64](Get-TeamPulseValue $process 'WorkingSetBytes' 0)
            if ($null -eq $largest -or $size -gt $largest.WorkingSetBytes) {
                $largest = [pscustomobject]@{
                    Name = [string](Get-TeamPulseValue $process 'Name' ''); WorkingSetBytes = $size; TaskId = $taskId
                }
            }
        }
    }
    return $largest
}

function Get-TeamHostPulse {
    param($Memory, $Temp, [object[]]$Drives = @(), [object[]]$Processes = @(), [object[]]$RunRoots = @())
    Set-StrictMode -Version Latest
    $driveFree = [ordered]@{}
    foreach ($drive in @($Drives)) {
        $letter = [string](Get-TeamPulseValue $drive 'Drive' '')
        if ([bool](Get-TeamPulseValue $drive 'Missing' $false)) { $driveFree[$letter] = $null }
        else { $driveFree[$letter] = [int64](Get-TeamPulseValue $drive 'FreeBytes' 0) }
    }
    return [pscustomobject]@{
        FreePercent   = [double](Get-TeamPulseValue $Memory 'FreePercent' 0)
        TempCount     = [int64](Get-TeamPulseValue $Temp 'Count' 0)
        TempTooLarge  = [bool](Get-TeamPulseValue $Temp 'TooLarge' $false)
        TempMissing   = [bool](Get-TeamPulseValue $Temp 'Missing' $false)
        TempTopPrefix = [string](Get-TeamPulseValue $Temp 'TopPrefix' '')
        DriveFree     = $driveFree
        Orphans       = @(Get-TeamOrphanTree -Processes @($Processes) -RunRoots @($RunRoots))
        Largest       = Get-TeamLargestRunProcess -Processes @($Processes) -RunRoots @($RunRoots)
    }
}

function Get-TeamPulseThresholds {
    param($Settings)
    Set-StrictMode -Version Latest
    $defaults = [ordered]@{
        min_free_memory_percent = 15
        max_temp_items          = 500000
        min_drive_free_gb       = 20
        temp_growth_alarm       = 100000
    }
    $thresholds = [ordered]@{}
    foreach ($key in $defaults.Keys) {
        $value = Get-TeamPulseValue $Settings $key $null
        $number = 0.0
        if ($null -ne $value -and -not ($value -is [bool]) -and
            [double]::TryParse([string]$value, [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$number) -and
            $number -ge 0) {
            $thresholds[$key] = $value
        } else {
            $thresholds[$key] = $defaults[$key]
        }
    }
    return [pscustomobject]$thresholds
}

function Format-TeamPulsePercent {
    param([double]$Percent)
    return [int64][math]::Floor($Percent)
}

function Test-TeamHostPulse {
    param($Pulse, $Thresholds)
    Set-StrictMode -Version Latest
    $reasons = New-Object System.Collections.Generic.List[string]

    $percent = [double]$Pulse.FreePercent
    if ($percent -lt [double]$Thresholds.min_free_memory_percent) {
        $reason = 'Bellek %' + (Format-TeamPulsePercent $percent) + ' kaldı'
        if ($null -ne $Pulse.Largest) {
            $reason += '; en büyük süreç: ' + $Pulse.Largest.Name + ', ' +
                [int64][math]::Round($Pulse.Largest.WorkingSetBytes / 1GB, [System.MidpointRounding]::AwayFromZero) +
                ' GB, ' + $Pulse.Largest.TaskId
        }
        $reasons.Add($reason)
    }

    $prefix = ''
    if ($Pulse.TempTopPrefix) { $prefix = ', en sık önek ' + $Pulse.TempTopPrefix }
    # Only the maximum fails: a count that ran out of time under it is unknown, not bad (shown as
    # "<n>+" in the line); one that ran out of time above it is already over.
    if ([int64]$Pulse.TempCount -gt [int64]$Thresholds.max_temp_items) {
        $more = ''
        if ($Pulse.TempTooLarge) { $more = '+' }
        $reasons.Add('TEMP ' + $Pulse.TempCount + $more + ' öğe (sınır ' + $Thresholds.max_temp_items + ')' + $prefix)
    }

    $minDriveBytes = [double]$Thresholds.min_drive_free_gb * 1GB
    foreach ($letter in $Pulse.DriveFree.Keys) {
        $free = $Pulse.DriveFree[$letter]
        if ($null -eq $free) { continue }  # a missing drive is named in the line, not failed
        if ([double]$free -lt $minDriveBytes) {
            $reasons.Add($letter + ': ' + (Get-TeamPulseGb $free) + ' GB boş (sınır ' + $Thresholds.min_drive_free_gb + ' GB)')
        }
    }

    return [pscustomobject]@{ Ok = ($reasons.Count -eq 0); Reasons = $reasons.ToArray() }
}

function Compare-TeamTempGrowth {
    param($Today, $Yesterday, [int64]$Threshold = 100000)
    Set-StrictMode -Version Latest
    $todayCount = Get-TeamTempCountOf $Today
    $yesterdayCount = Get-TeamTempCountOf $Yesterday
    if ($null -eq $todayCount -or $null -eq $yesterdayCount) {
        return [pscustomobject]@{ Grew = $false; Delta = 0; Line = '' }
    }
    $delta = [int64]($todayCount - $yesterdayCount)
    if ($delta -le $Threshold) {
        return [pscustomobject]@{ Grew = $false; Delta = $delta; Line = '' }
    }
    $prefix = [string](Get-TeamPulseValue $Today 'TopPrefix' (Get-TeamPulseValue $Today 'TempTopPrefix' ''))
    if (-not $prefix) { $prefix = '-' }
    return [pscustomobject]@{
        Grew = $true; Delta = $delta; Line = 'geçici klasör büyüyor: +' + $delta + ', en sık önek ' + $prefix
    }
}

function Get-TeamTempCountOf {
    # A count, a Measure-TeamTempItems result (Count) or a pulse (TempCount); $null when unknown.
    param($Value)
    if ($null -eq $Value) { return $null }
    if ($Value -is [ValueType]) { return [int64]$Value }
    $count = Get-TeamPulseValue $Value 'Count' (Get-TeamPulseValue $Value 'TempCount' $null)
    if ($null -eq $count) { return $null }
    return [int64]$count
}

function Format-TeamPulseLine {
    param($Pulse, $Check)
    Set-StrictMode -Version Latest
    $temp = [string]$Pulse.TempCount
    if ($Pulse.TempTooLarge) { $temp += '+' }
    if ((Get-TeamPulseValue $Pulse 'TempMissing' $false)) { $temp = 'yok' }
    $line = 'Makine: bellek %' + (Format-TeamPulsePercent $Pulse.FreePercent) + ', TEMP ' + $temp
    foreach ($letter in $Pulse.DriveFree.Keys) {
        $free = $Pulse.DriveFree[$letter]
        if ($null -eq $free) { $line += ', ' + $letter + ': yok' }
        else { $line += ', ' + $letter + ': ' + (Get-TeamPulseGb $free) + ' GB' }
    }
    $orphans = @($Pulse.Orphans)
    if ($orphans.Count -gt 0) {
        $names = @($orphans | ForEach-Object { [string](Get-TeamPulseValue $_ 'Name' '') }) -join ', '
        $line += ' - biten koşudan kalan süreç: ' + $orphans.Count + ' (' + $names + ')'
    }
    if (-not $Check.Ok) {
        $line += ' - yeni iş başlatılmadı: ' + (@($Check.Reasons) -join ' / ')
    }
    return $line
}
