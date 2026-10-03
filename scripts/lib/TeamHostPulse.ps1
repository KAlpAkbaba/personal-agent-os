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
        when its time budget runs out (the count so far is kept; running out is the finding).
      * PURE functions (Get-TeamHostPulse, Get-TeamOrphanTree, Test-TeamHostPulse,
        Get-TeamPulseThresholds, Compare-TeamTempGrowth, Format-TeamPulseLine) do no I/O.

    Thresholds compare strictly in one direction: below a minimum or above a maximum fails,
    equal passes. Orphans never fail the check (they are closed, not waited on); they are named
    in the line. An orphan is a live descendant of a RECORDED run root whose run has finished,
    found by a ParentProcessId walk - never a process selected by its name.

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

function Get-TeamTempPrefix {
    # The leading run of letters before the first digit, underscore, dash or other non-letter.
    param([string]$Name)
    $match = [regex]::Match($Name, '^\p{L}+')
    if ($match.Success) { return $match.Value }
    return ''
}

function Measure-TeamTempItems {
    param([string]$Path = $env:TEMP, [int]$BudgetMs = 2000)
    Set-StrictMode -Version Latest
    $count = 0
    $tooLarge = $false
    $prefixes = @{}
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    $entries = [System.IO.Directory]::EnumerateFileSystemEntries($Path).GetEnumerator()
    try {
        while ($entries.MoveNext()) {
            if ($clock.ElapsedMilliseconds -ge $BudgetMs) { $tooLarge = $true; break }
            $count++
            $prefix = Get-TeamTempPrefix ([System.IO.Path]::GetFileName($entries.Current))
            if ($prefix) {
                if ($prefixes.ContainsKey($prefix)) { $prefixes[$prefix]++ } else { $prefixes[$prefix] = 1 }
            }
        }
    } finally {
        $entries.Dispose()
    }
    $top = ''
    $best = 0
    foreach ($key in @($prefixes.Keys | Sort-Object)) {
        if ($prefixes[$key] -gt $best) { $best = $prefixes[$key]; $top = $key }
    }
    return [pscustomobject]@{ Count = $count; TooLarge = $tooLarge; TopPrefix = $top }
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
    param([object[]]$Processes, $Root)
    $started = ConvertTo-TeamPulseUtc (Get-TeamPulseValue $Root 'StartedAt' $null)
    $rootPid = [int](Get-TeamPulseValue $Root 'Pid' 0)
    if ($null -eq $started -or $rootPid -le 0) { return }
    $visited = @{ $rootPid = $true }
    $queue = New-Object System.Collections.Queue
    $queue.Enqueue(@($rootPid, $started))
    while ($queue.Count -gt 0) {
        $node = $queue.Dequeue()
        $parentPid = [int]$node[0]
        $parentCreated = [datetime]$node[1]
        foreach ($process in $Processes) {
            if ([int](Get-TeamPulseValue $process 'ParentProcessId' 0) -ne $parentPid) { continue }
            $childPid = [int](Get-TeamPulseValue $process 'ProcessId' 0)
            if ($visited.ContainsKey($childPid)) { continue }
            $created = ConvertTo-TeamPulseUtc (Get-TeamPulseValue $process 'CreationDate' $null)
            if ($null -eq $created) { continue }
            if ($created -lt $parentCreated) { continue }
            $visited[$childPid] = $true
            $process
            $queue.Enqueue(@($childPid, $created))
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
    if ($Pulse.TempTooLarge) {
        $reasons.Add('TEMP sayımı süreye sığmadı (' + $Pulse.TempCount + '+ öğe)' + $prefix)
    } elseif ([int64]$Pulse.TempCount -gt [int64]$Thresholds.max_temp_items) {
        $reasons.Add('TEMP ' + $Pulse.TempCount + ' öğe (sınır ' + $Thresholds.max_temp_items + ')' + $prefix)
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
