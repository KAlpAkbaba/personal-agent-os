<#
.SYNOPSIS
    The test queue (test sırası): a machine-local line for HEAVY test runs, asked before the
    run and answered ONAY or BEKLE.

.DESCRIPTION
    The owner's idea, 2026-10-02: "bir ajan test yapacakken diğerlerine bilgi versin ki başka
    bir ajan orada güncel olarak test yapıyorsa birbirlerinin testlerini engellememiş olurlar;
    diğerleri onay alma ya da bekle komutuna göre devam etsin." That day a full gate beside
    three inspectors and a worker took 3.5 hours instead of 1.5, an inspection lost a probe
    because the gate reset the shared dev database under it, and a desktop test failed while
    something else held the GPU. The machine is strong; the heavy runs piled onto the same
    minutes and the same shared things.

    The store is a folder OUTSIDE the repository (default %LOCALAPPDATA%\PagentOS\test-slots),
    shared by every worktree on the machine:
        entries\<ticket>.json   one per request: waiting, granted (ONAY, ticket unused) or running
        lock                    held (FileShare.None) for every decision; the OS drops it when
                                its process dies, so a killed asker never wedges the line
        runs.log                one line per finished run: time, role, task, kinds, waited, ran
    Every write is a temp file then a move; every read skips a file that is half-written or
    gone. Nothing here kills anything, and no run is refused for being long.

    The rules, in one place:
      * a request takes ALL its kinds or none; kinds are counted in the table's order;
      * the line is first come, first served (the order of the FIRST ask, never the latest);
      * role 'gate' is placed ahead of every waiting request - never ahead of a holder;
      * a waiter that could be granted now has its kinds kept for it (it asks again within
        20 s); a waiter that cannot be granted keeps NOTHING, so `database,heavy` waiting on
        the database never holds a heavy slot;
      * a ticket unused for 5 minutes is void; a place not asked about for 10 minutes is
        dropped; a running slot whose holder (pid AND process start time) is gone is free.

    The office SEES the line (owner, 2026-10-03, team/plans/team-board-talk-adr.md): a take, a
    first BEKLE and a freed slot are one 'bilgi' note each on the team's board, with a snapshot
    of the line the Ofis draws. The note is built from what the queue already decided and sent
    after the decision; a board that is down or slow changes nothing here (one clock: this one).

    Windows PowerShell 5.1, StrictMode.
#>

Set-StrictMode -Version Latest

# The one named table. Order matters: kinds are always counted in this order.
$script:TestSlotKindTable = @(
    [pscustomobject]@{
        Kind     = "database"
        Capacity = 1
        Examples = "the api integration suite (pytest tests/integration -m integration); a hand-run alembic upgrade/downgrade; the gate's dev-up + alembic steps"
        Reason   = "one shared dev PostgreSQL: a second run that migrates or resets it pulls the floor from under the first (2026-10-02: an inspection lost its probe to the gate's reset)"
    },
    [pscustomobject]@{
        Kind     = "desktop"
        Capacity = 1
        Examples = "the operator lab (NotepadLifecycle pointer clicks); the Unity scene tests; a headed browser run; the M1 device E2E"
        Reason   = "one foreground window and one GPU: two of them steal focus from each other and both fail"
    },
    [pscustomobject]@{
        Kind     = "heavy"
        Capacity = 3
        Examples = "the whole api unit suite (56 min alone, 2 h 02 beside three others on 2026-10-02); the owner utterance corpus (11 min alone, 62 beside the gate); the whole web suite (build + vitest); the dotnet build + test of the Windows agent; the api integration suite; a suite over two minutes in the gate of 2026-10-03 (team-cycle.tests.ps1 820 s, cloud-release-bluegreen.tests.ps1 481 s, the browser agent lint + tests 213 s)"
        Reason   = "a ceiling, not a fit: four at once is where 2026-10-02's 3.5-hour gate came from, but three do not run free - one whole api unit run grew to 14 GB on 2026-10-03 (3 x 14 = 42 of 48 GB) and two whole unit runs side by side measured +27 % and +30 % over their times alone; the roles leave the whole unit suite to the lead's gate, and the lead decides the number from runs.log"
    }
)
$script:TestSlotTicketMinutes = 5
$script:TestSlotPlaceMinutes = 10
$script:TestSlotLockGuardSeconds = 60

function Get-TestSlotKinds {
    <# The kinds table: Kind, Capacity, Examples, Reason. #>
    return @($script:TestSlotKindTable)
}

function Get-TestSlotDefaultStore {
    return (Join-Path $env:LOCALAPPDATA "PagentOS\test-slots")
}

function Get-TestSlotEntryDirectory {
    param([Parameter(Mandatory = $true)][string]$Store)
    return (Join-Path $Store "entries")
}

function Get-TestSlotLogPath {
    param([Parameter(Mandatory = $true)][string]$Store)
    return (Join-Path $Store "runs.log")
}

function Resolve-TestSlotKinds {
    <# 'database,heavy', @('heavy','database'), ... -> the distinct kinds in the table's order. Throws on an unknown kind. #>
    param([object[]]$Kind)
    $asked = New-Object System.Collections.ArrayList
    foreach ($item in @($Kind)) {
        foreach ($part in @([string]$item -split ",")) {
            $k = $part.Trim().ToLowerInvariant()
            if ($k) { [void]$asked.Add($k) }
        }
    }
    if (@($asked).Count -eq 0) { throw "no kind given (database, desktop, heavy)" }
    $known = @($script:TestSlotKindTable | ForEach-Object { $_.Kind })
    foreach ($k in $asked) {
        if ($known -notcontains $k) { throw "unknown kind '$k' (database, desktop, heavy)" }
    }
    return @($known | Where-Object { $asked -contains $_ })
}

function ConvertTo-TestSlotTime {
    param([datetime]$Value)
    return $Value.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffffffZ", [System.Globalization.CultureInfo]::InvariantCulture)
}

function ConvertFrom-TestSlotTime {
    param([string]$Text)
    if (-not $Text) { return $null }
    $styles = [System.Globalization.DateTimeStyles]::AdjustToUniversal -bor [System.Globalization.DateTimeStyles]::AssumeUniversal
    return [datetime]::Parse($Text, [System.Globalization.CultureInfo]::InvariantCulture, $styles)
}

function Get-TestSlotProcessStamp {
    <# The process's start time as UTC ticks; $null when no such process runs; '?' when it runs but cannot be read. #>
    param([int]$ProcessId)
    try { $p = [System.Diagnostics.Process]::GetProcessById($ProcessId) } catch { return $null }
    try { if ($p.HasExited) { return $null } } catch { }
    try { return [string]$p.StartTime.ToUniversalTime().Ticks } catch { return "?" }
}

function Test-TestSlotHolderAlive {
    <# A slot belongs to a pid AND its start time: a recycled pid is not the holder. A process
       whose start time cannot be read (another user, elevated) is taken to be alive. #>
    param($Entry)
    $stamp = Get-TestSlotProcessStamp -ProcessId ([int]$Entry.holder_pid)
    if ($null -eq $stamp) { return $false }
    if ($stamp -eq "?") { return $true }
    return ($stamp -eq [string]$Entry.holder_start)
}

function Enter-TestSlotLock {
    <# Opens the store's lock file exclusively and returns the stream; the caller disposes it.
       The OS closes it when the process dies. The wait is a hang guard, not a policy. #>
    param([Parameter(Mandatory = $true)][string]$Store)
    [void][System.IO.Directory]::CreateDirectory((Get-TestSlotEntryDirectory -Store $Store))
    $lockPath = Join-Path $Store "lock"
    $deadline = [DateTime]::UtcNow.AddSeconds($script:TestSlotLockGuardSeconds)
    while ($true) {
        try {
            return [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        }
        catch {
            if ([DateTime]::UtcNow -gt $deadline) { throw "the test queue's lock ($lockPath) could not be taken in $($script:TestSlotLockGuardSeconds) s: $($_.Exception.Message)" }
            Start-Sleep -Milliseconds 15
        }
    }
}

function Get-TestSlotEntries {
    <# Every readable entry of the store. A half-written, foreign or vanished file is skipped. #>
    param([Parameter(Mandatory = $true)][string]$Store)
    $dir = Get-TestSlotEntryDirectory -Store $Store
    if (-not (Test-Path -LiteralPath $dir)) { return @() }
    $entries = New-Object System.Collections.ArrayList
    foreach ($file in @(Get-ChildItem -LiteralPath $dir -Filter "*.json" -File -ErrorAction SilentlyContinue)) {
        try {
            # Shared read/write/delete: a reader never stands in the way of the writer's replace.
            $share = [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete
            $stream = [System.IO.File]::Open($file.FullName, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, $share)
            try { $text = (New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)).ReadToEnd() }
            finally { $stream.Dispose() }
            $e = $text | ConvertFrom-Json -ErrorAction Stop
            $names = @($e.PSObject.Properties | ForEach-Object { $_.Name })
            $ok = $true
            foreach ($need in @("ticket", "kinds", "state", "role", "task", "what", "seq", "first_asked", "last_asked", "granted_at", "started_at", "holder_pid", "holder_start")) {
                if ($names -notcontains $need) { $ok = $false }
            }
            if ($ok) { [void]$entries.Add($e) }
        }
        catch { }
    }
    return @($entries)
}

function Write-TestSlotEntry {
    <# Atomic: a temp file in the same folder, then a move (or a replace over the old one). #>
    param([string]$Store, $Entry)
    $dir = Get-TestSlotEntryDirectory -Store $Store
    $dest = Join-Path $dir ("{0}.json" -f $Entry.ticket)
    $tmp = Join-Path $dir ("{0}.{1}.tmp" -f $Entry.ticket, [guid]::NewGuid().ToString("N"))
    $json = $Entry | ConvertTo-Json -Depth 4 -Compress
    [System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding($false)))
    # A reader outside the lock (status, a test) may hold the old file open for a moment: retry.
    $attempt = 0
    while ($true) {
        try {
            if ([System.IO.File]::Exists($dest)) { [System.IO.File]::Replace($tmp, $dest, [System.Management.Automation.Language.NullString]::Value) }
            else { [System.IO.File]::Move($tmp, $dest) }
            return
        }
        catch {
            $attempt++
            if ($attempt -ge 100) { try { [System.IO.File]::Delete($tmp) } catch { }; throw }
            Start-Sleep -Milliseconds 20
        }
    }
}

function Remove-TestSlotEntryFile {
    param([string]$Store, [string]$Ticket)
    $path = Join-Path (Get-TestSlotEntryDirectory -Store $Store) ("{0}.json" -f $Ticket)
    try { [System.IO.File]::Delete($path) } catch { }
}

function Write-TestSlotLogLine {
    param([string]$Store, $Entry, [datetime]$NowUtc, [string]$Exit)
    $clean = { param($s) ([string]$s -replace "[\t\r\n]+", " ") }
    $asked = ConvertFrom-TestSlotTime $Entry.first_asked
    $started = ConvertFrom-TestSlotTime $Entry.started_at
    $waited = 0; $ran = 0
    if ($null -ne $started -and $null -ne $asked) { $waited = [math]::Max(0, [math]::Round(($started - $asked).TotalSeconds)) }
    if ($null -ne $started) { $ran = [math]::Max(0, [math]::Round(($NowUtc - $started).TotalSeconds)) }
    $line = "{0}`trole={1}`ttask={2}`tkinds={3}`twaited_s={4}`tran_s={5}`texit={6}`twhat={7}" -f `
        $NowUtc.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ", [System.Globalization.CultureInfo]::InvariantCulture),
        (& $clean $Entry.role), (& $clean $Entry.task), (@($Entry.kinds) -join ","), $waited, $ran, $Exit, (& $clean $Entry.what)
    [System.IO.File]::AppendAllText((Get-TestSlotLogPath -Store $Store), $line + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
}

function Invoke-TestSlotPurge {
    <# Inside the lock: drops void tickets, forgotten places and slots whose holder is gone.
       Returns what is left. #>
    param([string]$Store, [datetime]$NowUtc)
    $left = New-Object System.Collections.ArrayList
    foreach ($e in @(Get-TestSlotEntries -Store $Store)) {
        $drop = $false
        if ($e.state -eq "granted") {
            $at = ConvertFrom-TestSlotTime $e.granted_at
            if ($null -eq $at -or ($NowUtc - $at).TotalMinutes -gt $script:TestSlotTicketMinutes) { $drop = $true }
        }
        elseif ($e.state -eq "waiting") {
            $at = ConvertFrom-TestSlotTime $e.last_asked
            if ($null -eq $at -or ($NowUtc - $at).TotalMinutes -gt $script:TestSlotPlaceMinutes) { $drop = $true }
        }
        elseif ($e.state -eq "running") {
            if (-not (Test-TestSlotHolderAlive -Entry $e)) {
                $drop = $true
                Write-TestSlotLogLine -Store $Store -Entry $e -NowUtc $NowUtc -Exit "holder-gone"
            }
        }
        else { $drop = $true }
        if ($drop) { Remove-TestSlotEntryFile -Store $Store -Ticket $e.ticket } else { [void]$left.Add($e) }
    }
    return @($left)
}

function Get-TestSlotQueue {
    <# The waiting entries in line order: the gate first, then by the sequence of the FIRST ask. #>
    param([object[]]$Entries)
    return @(@($Entries) | Where-Object { $_.state -eq "waiting" } |
            Sort-Object -Property @{ Expression = { if ($_.role -eq "gate") { 0 } else { 1 } } }, @{ Expression = { [long]$_.seq } })
}

function Get-TestSlotDecision {
    <# Pure. Whether the waiting entry $Ticket may be granted now, its position, who is ahead
       of it and who holds what it wants. Walks the line in order: a waiter that could be
       granted now has its kinds kept for it; one that cannot keeps nothing. #>
    param([object[]]$Entries, [string]$Ticket)
    $free = @{}
    foreach ($k in $script:TestSlotKindTable) { $free[$k.Kind] = [int]$k.Capacity }
    $holding = @(@($Entries) | Where-Object { $_.state -eq "granted" -or $_.state -eq "running" })
    foreach ($h in $holding) { foreach ($k in @($h.kinds)) { $free[$k] = $free[$k] - 1 } }
    $ahead = New-Object System.Collections.ArrayList
    $mineNow = @(@($Entries) | Where-Object { $_.ticket -eq $Ticket } | ForEach-Object { @($_.kinds) })
    foreach ($w in @(Get-TestSlotQueue -Entries $Entries)) {
        $fits = $true
        foreach ($k in @($w.kinds)) { if ($free[$k] -lt 1) { $fits = $false } }
        if ($w.ticket -eq $Ticket) {
            $mine = @($w.kinds)
            $holders = @($holding | Where-Object { $hk = @($_.kinds); @($mine | Where-Object { $hk -contains $_ }).Count -gt 0 })
            return [pscustomobject]@{
                Grant    = $fits
                Position = @($ahead).Count + 1
                Ahead    = @($ahead)
                Holders  = $holders
            }
        }
        if ($fits) { foreach ($k in @($w.kinds)) { $free[$k] = $free[$k] - 1 } }
        if (@(@($w.kinds) | Where-Object { $mineNow -contains $_ }).Count -gt 0) { [void]$ahead.Add($w) }
    }
    throw "ticket $Ticket is not waiting"
}

function Format-TestSlotWho {
    param($Entry)
    return ('{0} {1} "{2}"' -f $Entry.role, $Entry.task, $Entry.what)
}

function Format-TestSlotSince {
    param([string]$Time, [datetime]$NowUtc)
    $at = ConvertFrom-TestSlotTime $Time
    if ($null -eq $at) { return "" }
    $min = [math]::Max(0, [math]::Floor(($NowUtc - $at).TotalMinutes))
    return ("{0}Z'den beri ({1} dk)" -f $at.ToString("HH:mm:ss", [System.Globalization.CultureInfo]::InvariantCulture), $min)
}

function Invoke-TestSlotAsk {
    <# Never blocks (beyond the lock). Returns Decision ONAY|BEKLE, Ticket, Position, Line. #>
    param(
        [Parameter(Mandatory = $true)][string]$Store,
        [Parameter(Mandatory = $true)][object[]]$Kind,
        [Parameter(Mandatory = $true)][string]$Task,
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][string]$What,
        [Nullable[datetime]]$NowUtc = $null,
        # The asker's seat on the team's board (worker-2, inspector, ...): the notes' names.
        [string]$Seat = ""
    )
    $kinds = @(Resolve-TestSlotKinds -Kind $Kind)
    $now = if ($null -ne $NowUtc) { ([datetime]$NowUtc).ToUniversalTime() } else { [DateTime]::UtcNow }
    $role = $Role.Trim().ToLowerInvariant()
    $key = ($kinds -join ",")
    $lock = Enter-TestSlotLock -Store $Store
    try {
        $entries = @(Invoke-TestSlotPurge -Store $Store -NowUtc $now)
        $mine = @($entries | Where-Object { ($_.state -eq "waiting" -or $_.state -eq "granted") -and $_.role -eq $role -and $_.task -eq $Task -and (@($_.kinds) -join ",") -eq $key }) | Select-Object -First 1
        if ($null -ne $mine -and $mine.state -eq "granted") {
            return [pscustomobject]@{ Decision = "ONAY"; Ticket = $mine.ticket; Position = 0; Line = "ONAY $($mine.ticket)"; Fresh = $false; Entry = $mine }
        }
        $fresh = ($null -eq $mine)
        if ($null -eq $mine) {
            $seq = 1
            foreach ($e in $entries) { if ([long]$e.seq -ge $seq) { $seq = [long]$e.seq + 1 } }
            $mine = [pscustomobject][ordered]@{
                ticket = "ts-" + [guid]::NewGuid().ToString("N").Substring(0, 12)
                kinds = @($kinds); role = $role; task = $Task; what = $What; state = "waiting"; seq = $seq
                first_asked = (ConvertTo-TestSlotTime $now); last_asked = (ConvertTo-TestSlotTime $now)
                granted_at = ""; started_at = ""; holder_pid = 0; holder_start = ""; seat = $Seat
            }
            $entries = @($entries) + @($mine)
        }
        else {
            $mine.last_asked = ConvertTo-TestSlotTime $now
            $mine.what = $What
        }
        $decision = Get-TestSlotDecision -Entries $entries -Ticket $mine.ticket
        if ($decision.Grant) {
            $mine.state = "granted"
            $mine.granted_at = ConvertTo-TestSlotTime $now
            Write-TestSlotEntry -Store $Store -Entry $mine
            return [pscustomobject]@{ Decision = "ONAY"; Ticket = $mine.ticket; Position = 0; Line = "ONAY $($mine.ticket)"; Fresh = $true; Entry = $mine }
        }
        Write-TestSlotEntry -Store $Store -Entry $mine
        $holders = @($decision.Holders | ForEach-Object { "{0} [{1}] {2}" -f (Format-TestSlotWho $_), (@($_.kinds) -join ","), (Format-TestSlotSince -Time $(if ($_.started_at) { $_.started_at } else { $_.granted_at }) -NowUtc $now) })
        $ahead = @($decision.Ahead | ForEach-Object { "{0} [{1}]" -f (Format-TestSlotWho $_), (@($_.kinds) -join ",") })
        $holderText = if (@($holders).Count -gt 0) { "tutan: " + ($holders -join "; ") } else { "tutan: yok (boşalan yer sıradakine ayrıldı)" }
        $aheadText = if (@($ahead).Count -gt 0) { "önde: " + ($ahead -join "; ") } else { "önde: yok" }
        return [pscustomobject]@{
            Decision = "BEKLE"; Ticket = $mine.ticket; Position = $decision.Position
            Line = ("BEKLE {0} | {1} | {2}" -f $decision.Position, $holderText, $aheadText)
            Fresh = $fresh; Entry = $mine
        }
    }
    finally { $lock.Dispose() }
}

function Start-TestSlotRun {
    <# A granted ticket becomes a running slot held by a process (pid + start time). Throws
       'TICKET_VOID: ...' for a ticket that is unknown, void or already used. #>
    param(
        [Parameter(Mandatory = $true)][string]$Store,
        [Parameter(Mandatory = $true)][string]$Ticket,
        [int]$HolderPid = $PID,
        [string]$HolderStart = "",
        [Nullable[datetime]]$NowUtc = $null
    )
    $now = if ($null -ne $NowUtc) { ([datetime]$NowUtc).ToUniversalTime() } else { [DateTime]::UtcNow }
    $lock = Enter-TestSlotLock -Store $Store
    try {
        $entries = @(Invoke-TestSlotPurge -Store $Store -NowUtc $now)
        $e = @($entries | Where-Object { $_.ticket -eq $Ticket }) | Select-Object -First 1
        if ($null -eq $e) { throw "TICKET_VOID: ticket $Ticket is unknown or void (unused for $($script:TestSlotTicketMinutes) min, or released) - ask again" }
        if ($e.state -ne "granted") { throw "TICKET_VOID: ticket $Ticket is $($e.state), not granted" }
        if (-not $HolderStart) {
            $HolderStart = Get-TestSlotProcessStamp -ProcessId $HolderPid
            if ($null -eq $HolderStart) { throw "holder process $HolderPid does not run" }
        }
        $e.state = "running"
        $e.started_at = ConvertTo-TestSlotTime $now
        $e.holder_pid = $HolderPid
        $e.holder_start = [string]$HolderStart
        Write-TestSlotEntry -Store $Store -Entry $e
        return $e
    }
    finally { $lock.Dispose() }
}

function Complete-TestSlotRun {
    <# The run ended: the slot is freed and one line is logged. #>
    param(
        [Parameter(Mandatory = $true)][string]$Store,
        [Parameter(Mandatory = $true)][string]$Ticket,
        [string]$ExitCode = "0",
        [Nullable[datetime]]$NowUtc = $null,
        # Returns the freed entry (for the board's note); nothing without it.
        [switch]$PassThru
    )
    $now = if ($null -ne $NowUtc) { ([datetime]$NowUtc).ToUniversalTime() } else { [DateTime]::UtcNow }
    $lock = Enter-TestSlotLock -Store $Store
    try {
        $e = @(Get-TestSlotEntries -Store $Store | Where-Object { $_.ticket -eq $Ticket }) | Select-Object -First 1
        if ($null -eq $e) { return }
        Remove-TestSlotEntryFile -Store $Store -Ticket $Ticket
        if ($e.state -eq "running") { Write-TestSlotLogLine -Store $Store -Entry $e -NowUtc $now -Exit $ExitCode }
        if ($PassThru) { return $e }
    }
    finally { $lock.Dispose() }
}

function Remove-TestSlotTicket {
    <# 'release': a holder gives its ticket (or its place in line) back. $true if it existed. #>
    param([Parameter(Mandatory = $true)][string]$Store, [Parameter(Mandatory = $true)][string]$Ticket)
    $lock = Enter-TestSlotLock -Store $Store
    try {
        $e = @(Get-TestSlotEntries -Store $Store | Where-Object { $_.ticket -eq $Ticket }) | Select-Object -First 1
        if ($null -eq $e) { return $false }
        Remove-TestSlotEntryFile -Store $Store -Ticket $Ticket
        if ($e.state -eq "running") { Write-TestSlotLogLine -Store $Store -Entry $e -NowUtc ([DateTime]::UtcNow) -Exit "released" }
        return $true
    }
    finally { $lock.Dispose() }
}

function Get-TestSlotStatusText {
    <# The table in Turkish: each kind's use, who runs what since when, who waits. #>
    param([Parameter(Mandatory = $true)][string]$Store, [Nullable[datetime]]$NowUtc = $null)
    $now = if ($null -ne $NowUtc) { ([datetime]$NowUtc).ToUniversalTime() } else { [DateTime]::UtcNow }
    $lock = Enter-TestSlotLock -Store $Store
    try { $entries = @(Invoke-TestSlotPurge -Store $Store -NowUtc $now) }
    finally { $lock.Dispose() }
    $lines = New-Object System.Collections.ArrayList
    [void]$lines.Add(("Test sırası - {0}Z - {1}" -f $now.ToString("yyyy-MM-dd HH:mm:ss", [System.Globalization.CultureInfo]::InvariantCulture), $Store))
    $use = @()
    foreach ($k in $script:TestSlotKindTable) {
        $n = @($entries | Where-Object { ($_.state -eq "granted" -or $_.state -eq "running") -and @($_.kinds) -contains $k.Kind }).Count
        $use += ("{0} {1}/{2}" -f $k.Kind, $n, $k.Capacity)
    }
    [void]$lines.Add("Türler (dolu/kapasite): " + ($use -join ", "))
    $held = @($entries | Where-Object { $_.state -eq "granted" -or $_.state -eq "running" } | Sort-Object -Property @{ Expression = { [long]$_.seq } })
    foreach ($e in $held) {
        if ($e.state -eq "running") {
            [void]$lines.Add(("  çalışıyor   {0} [{1}] pid {2} - {3}" -f (Format-TestSlotWho $e), (@($e.kinds) -join ","), $e.holder_pid, (Format-TestSlotSince -Time $e.started_at -NowUtc $now)))
        }
        else {
            [void]$lines.Add(("  onaylandı   {0} [{1}] bilet {2} henüz kullanılmadı - {3}" -f (Format-TestSlotWho $e), (@($e.kinds) -join ","), $e.ticket, (Format-TestSlotSince -Time $e.granted_at -NowUtc $now)))
        }
    }
    $i = 0
    foreach ($w in @(Get-TestSlotQueue -Entries $entries)) {
        $i++
        [void]$lines.Add(("  bekliyor {0}  {1} [{2}] - ilk soru {3}" -f $i, (Format-TestSlotWho $w), (@($w.kinds) -join ","), (Format-TestSlotSince -Time $w.first_asked -NowUtc $now)))
    }
    if (@($held).Count -eq 0 -and $i -eq 0) { [void]$lines.Add("  Kimse çalışmıyor, kimse beklemiyor.") }
    return @($lines)
}

function Wait-TestSlotGrant {
    <# For a caller that has nothing else to do (the gate): asks every $PollSeconds until ONAY,
       prints the BEKLE line once per $NoticeSeconds. Returns Ticket and WaitedSeconds. #>
    param(
        [Parameter(Mandatory = $true)][string]$Store,
        [Parameter(Mandatory = $true)][object[]]$Kind,
        [Parameter(Mandatory = $true)][string]$Task,
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][string]$What,
        [int]$PollSeconds = 20,
        [int]$NoticeSeconds = 60
    )
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $lastNotice = $null
    while ($true) {
        $r = Invoke-TestSlotAsk -Store $Store -Kind $Kind -Task $Task -Role $Role -What $What
        if ($r.Decision -eq "ONAY") {
            return [pscustomobject]@{ Ticket = $r.Ticket; WaitedSeconds = [math]::Round($sw.Elapsed.TotalSeconds, 1) }
        }
        if ($null -eq $lastNotice -or ($sw.Elapsed - $lastNotice).TotalSeconds -ge $NoticeSeconds) {
            Write-Host ("TEST SIRASI: {0}" -f $r.Line) -ForegroundColor Yellow
            $lastNotice = $sw.Elapsed
        }
        Start-Sleep -Seconds ([math]::Max(1, $PollSeconds))
    }
}

# ---------------------------------------------------------------------- the board (the office sees the line)

$script:TestSlotKindTr = @{ database = "veritabanı"; desktop = "masaüstü"; heavy = "ağır" }
$script:TestSlotNoteTextMax = 280
$script:TestSlotNoteWhatMax = 100
$script:TestSlotNamesMax = 20
$script:TestSlotEstimateRuns = 5
# The board's own patterns (services/api/app/team/board.py SEAT_PATTERN, TASK_PATTERN).
$script:TestSlotSeatPattern = '^(?:lead|researcher|integrator|inspector(?:-[1-9])?|worker-[1-9])$'
$script:TestSlotTaskPattern = '^[a-z0-9][a-z0-9-]{2,63}$'

function Get-TestSlotSeatName {
    <# A seat or a role in plain Turkish: worker-2 -> "Çalışan 2", gate -> "Kapı". #>
    param([string]$Name)
    if ($Name -match '^worker-(\d)$') { return "Çalışan " + $Matches[1] }
    if ($Name -match '^inspector-(\d)$') { return "Denetleyici " + $Matches[1] }
    switch ($Name) {
        "inspector" { return "Denetleyici" }
        "lead" { return "Proje Yöneticisi" }
        "gate" { return "Kapı" }
        "researcher" { return "Araştırmacı" }
        "integrator" { return "Entegratör" }
        "worker" { return "Çalışan" }
    }
    return $Name
}

function Get-TestSlotEntryName {
    <# Who holds or waits, as the board names it: the entry's seat, else its role. #>
    param($Entry)
    $seat = if ($Entry.PSObject.Properties["seat"]) { [string]$Entry.seat } else { "" }
    $name = if ($seat) { $seat } else { ([string]$Entry.role).ToLowerInvariant() }
    if ($name -notmatch '^[a-z][a-z0-9-]{0,31}$') { $name = "test" }
    return $name
}

function Get-TestSlotKindText {
    param($Entry)
    return ((@($Entry.kinds) | ForEach-Object { $script:TestSlotKindTr[[string]$_] }) -join ", ")
}

function Get-TestSlotWhatText {
    param($Entry)
    $what = ([string]$Entry.what -replace '\s+', ' ').Trim()
    if ($what.Length -gt $script:TestSlotNoteWhatMax) { $what = $what.Substring(0, $script:TestSlotNoteWhatMax - 1) + "…" }
    return $what
}

function Get-TestSlotEstimateMinutes {
    <# The mean of the last runs of the same command (else of the same kinds) in runs.log, in
       whole minutes; 0 when the log knows none. Read-only, decides nothing. #>
    param([string]$Store, $Entry)
    $path = Get-TestSlotLogPath -Store $Store
    if (-not (Test-Path -LiteralPath $path)) { return 0 }
    $runs = @()
    try { $lines = [System.IO.File]::ReadAllLines($path, [System.Text.Encoding]::UTF8) } catch { return 0 }
    foreach ($line in $lines) {
        $f = @{}
        foreach ($part in ($line -split "`t")) { $kv = $part -split "=", 2; if ($kv.Count -eq 2) { $f[$kv[0]] = $kv[1] } }
        if (-not $f.ContainsKey("ran_s") -or $f["exit"] -eq "holder-gone" -or $f["exit"] -eq "released") { continue }
        $runs += [pscustomobject]@{ What = [string]$f["what"]; Kinds = [string]$f["kinds"]; Ran = [int]$f["ran_s"] }
    }
    $same = @($runs | Where-Object { $_.What -eq [string]$Entry.what })
    if ($same.Count -eq 0) { $same = @($runs | Where-Object { $_.Kinds -eq (@($Entry.kinds) -join ",") }) }
    if ($same.Count -eq 0) { return 0 }
    $last = @($same | Select-Object -Last $script:TestSlotEstimateRuns)
    $mean = ($last | Measure-Object -Property Ran -Average).Average
    return [int][math]::Max(1, [math]::Ceiling($mean / 60))
}

function Get-TestSlotNoteSeat {
    <# The seat a note is written under: the entry's seat; the gate writes as the lead; a role
       that is a seat (inspector, lead, ...) as itself; else none (no note). #>
    param($Entry)
    $seat = if ($Entry.PSObject.Properties["seat"]) { [string]$Entry.seat } else { "" }
    if ($seat -match $script:TestSlotSeatPattern) { return $seat }
    $role = ([string]$Entry.role).ToLowerInvariant()
    if ($role -eq "gate") { return "lead" }
    if ($role -match $script:TestSlotSeatPattern) { return $role }
    return ""
}

function New-TestSlotBoardNote {
    <# One 'bilgi' note for a decision the queue already made: take (ONAY), wait (a first BEKLE)
       or free (a run ended). Pure over the entries it is given; $null when the entry has no
       seat or task the board knows. #>
    param(
        [Parameter(Mandatory = $true)][ValidateSet("take", "wait", "free")][string]$Event,
        [Parameter(Mandatory = $true)]$Entry,
        [AllowEmptyCollection()][object[]]$Entries = @(),
        [string]$Store = "",
        [string]$ExitCode = "0",
        [Nullable[datetime]]$NowUtc = $null
    )
    $seat = Get-TestSlotNoteSeat -Entry $Entry
    if (-not $seat -or [string]$Entry.task -notmatch $script:TestSlotTaskPattern) { return $null }
    $now = if ($null -ne $NowUtc) { ([datetime]$NowUtc).ToUniversalTime() } else { [DateTime]::UtcNow }
    $name = Get-TestSlotSeatName -Name (Get-TestSlotEntryName -Entry $Entry)
    $what = Get-TestSlotWhatText -Entry $Entry
    $kinds = Get-TestSlotKindText -Entry $Entry
    $holding = @(@($Entries) | Where-Object { ($_.state -eq "granted" -or $_.state -eq "running") -and $_.ticket -ne $(if ($Event -eq "free") { $Entry.ticket } else { "" }) } | Sort-Object -Property @{ Expression = { [long]$_.seq } })
    $line = @(Get-TestSlotQueue -Entries @($Entries))
    $slot = [ordered]@{
        state   = $Event
        kinds   = @($Entry.kinds | ForEach-Object { [string]$_ })
        holders = @($holding | ForEach-Object { Get-TestSlotEntryName -Entry $_ } | Select-Object -First $script:TestSlotNamesMax)
        waiting = @($line | ForEach-Object { Get-TestSlotEntryName -Entry $_ } | Select-Object -First $script:TestSlotNamesMax)
    }
    if ($Event -eq "take") {
        $estimate = if ($Store) { Get-TestSlotEstimateMinutes -Store $Store -Entry $Entry } else { 0 }
        $tail = if ($estimate -gt 0) { "tahmini $estimate dk" } else { "süre tahmini yok" }
        $text = "{0}: {1} başlatıyorum ({2}), {3}" -f $name, $what, $kinds, $tail
        if ($estimate -gt 0) { $slot.estimate_min = [int]$estimate }
    }
    elseif ($Event -eq "wait") {
        $mine = @($Entry.kinds)
        $before = @($holding | Where-Object { $hk = @($_.kinds); @($mine | Where-Object { $hk -contains $_ }).Count -gt 0 })
        foreach ($w in $line) {
            if ($w.ticket -eq $Entry.ticket) { break }
            $wk = @($w.kinds)
            if (@($mine | Where-Object { $wk -contains $_ }).Count -gt 0) { $before += $w }
        }
        $names = @($before | ForEach-Object { Get-TestSlotSeatName -Name (Get-TestSlotEntryName -Entry $_) } | Select-Object -Unique)
        $position = 1
        foreach ($w in $line) { if ($w.ticket -eq $Entry.ticket) { break }; $position++ }
        $ahead = if ($names.Count -gt 0) { "önümde " + ($names -join ", ") } else { "önümde kimse yok" }
        $text = "{0}: test sırası bekliyorum ({1}: {2}), sıram {3}, {4}" -f $name, $kinds, $what, $position, $ahead
    }
    else {
        $started = ConvertFrom-TestSlotTime ([string]$Entry.started_at)
        $ran = if ($null -ne $started) { [int][math]::Max(0, [math]::Round(($now - $started).TotalMinutes)) } else { 0 }
        $next = @($line | Select-Object -First 1)
        $after = if ($next.Count -gt 0) { "sıradaki: " + (Get-TestSlotSeatName -Name (Get-TestSlotEntryName -Entry $next[0])) } else { "sırada kimse yok" }
        $text = "{0}: {1} bitti ({2} dk, çıkış {3}); {4}" -f $name, $what, $ran, $ExitCode, $after
    }
    if ($text.Length -gt $script:TestSlotNoteTextMax) { $text = $text.Substring(0, $script:TestSlotNoteTextMax - 1) + "…" }
    return [pscustomobject]@{ seat = $seat; task = [string]$Entry.task; text = $text; slot = $slot }
}

function Publish-TestSlotBoardNote {
    <# Sends one note with -Sender; never throws, never retries: $true when it was sent. The
       board is a report of the queue, never a part of its decision. #>
    param($Note, [scriptblock]$Sender)
    if ($null -eq $Note -or $null -eq $Sender) { return $false }
    try { [void](& $Sender $Note); return $true } catch { return $false }
}

function Get-TestSlotBoardEntries {
    <# The entries as they are now, read without the lock (a report never waits on a decision). #>
    param([string]$Store)
    try { return @(Get-TestSlotEntries -Store $Store) } catch { return @() }
}

function Get-TestSlotWhoText {
    <# `who`: who tests what right now and the waiting line, by seat name, in Turkish. #>
    param([Parameter(Mandatory = $true)][string]$Store, [Nullable[datetime]]$NowUtc = $null)
    $now = if ($null -ne $NowUtc) { ([datetime]$NowUtc).ToUniversalTime() } else { [DateTime]::UtcNow }
    $lock = Enter-TestSlotLock -Store $Store
    try { $entries = @(Invoke-TestSlotPurge -Store $Store -NowUtc $now) }
    finally { $lock.Dispose() }
    $held = @($entries | Where-Object { $_.state -eq "granted" -or $_.state -eq "running" } | Sort-Object -Property @{ Expression = { [long]$_.seq } })
    $line = @(Get-TestSlotQueue -Entries $entries)
    $lines = New-Object System.Collections.ArrayList
    if ($held.Count -eq 0 -and $line.Count -eq 0) { [void]$lines.Add("Şu an test yapan yok, sırada kimse yok."); return @($lines) }
    [void]$lines.Add(("Şu an test yapan: {0}, sırada: {1}" -f $held.Count, $line.Count))
    foreach ($e in $held) {
        $since = if ($e.started_at) { $e.started_at } else { $e.granted_at }
        $at = ConvertFrom-TestSlotTime ([string]$since)
        $min = if ($null -ne $at) { [int][math]::Max(0, [math]::Floor(($now - $at).TotalMinutes)) } else { 0 }
        $state = if ($e.state -eq "running") { "" } else { ", onay aldı, henüz başlamadı" }
        [void]$lines.Add(("  TEST    {0} - {1} ({2}), {3} dk{4}" -f (Get-TestSlotSeatName -Name (Get-TestSlotEntryName -Entry $e)), (Get-TestSlotWhatText -Entry $e), (Get-TestSlotKindText -Entry $e), $min, $state))
    }
    $i = 0
    foreach ($w in $line) {
        $i++
        [void]$lines.Add(("  SIRA {0}  {1} - {2} ({3})" -f $i, (Get-TestSlotSeatName -Name (Get-TestSlotEntryName -Entry $w)), (Get-TestSlotWhatText -Entry $w), (Get-TestSlotKindText -Entry $w)))
    }
    return @($lines)
}
