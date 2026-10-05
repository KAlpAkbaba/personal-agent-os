<#
.SYNOPSIS
    Test sırası: ask before a heavy test run, and run it holding the slot.

.DESCRIPTION
    The owner's two words, ONAY and BEKLE (scripts/lib/TeamTestSlots.ps1 has the rules):

      ask     -Kind <database|desktop|heavy>[,...] -Task <id> -Role <role> -What "<one line>"
              Answers at once, never blocks:
                ONAY <ticket>                                         exit 0
                BEKLE <position> | tutan: <who holds it> | önde: <who is ahead>   exit 3
              On BEKLE do other work, or ask again (the place is kept while you ask at least
              every 10 minutes). A ticket not used within 5 minutes is void.
      run     -Ticket <ticket> -- <command> [arguments...]
              Runs the command holding the slot; its stdout and stderr pass straight through
              and its exit code is this script's exit code. The slot is released when the
              command ends - and also when this wrapper is killed (the slot belongs to this
              process: pid and start time). Nothing is killed, and no run is refused for being long.
              A run holding 'database' leaves the dev database at the schema it found
              (scripts/lib/TestSlots.ps1): it reads the alembic revision before the command and,
              when the command moved it, downgrades back from the run's own tree - success or
              failure. A downgrade that fails holds the database: every later 'database' ask
              or run answers DURDU (exit 6) until `unblock`. The promise is also written to
              <store>\database-guard.json before the command (a killed wrapper runs no
              `finally`): the next 'database' run finds a dead owner's record and restores
              from that tree first (or holds); while the dead run's command still runs, a
              'database' ask warns and a 'database' run answers DURDU naming its pid (exit 6).
      status  The queue in Turkish: who runs what since when, who waits; a hold's DURDU line.
      who     Short, by seat name: who is testing what right now and the waiting line -
              ask it before you plan a heavy run.
      release -Ticket <ticket>   gives a ticket (or a place in line) back.
      unblock The Danışman lifts the database hold after repairing the database.

    Common: -Store <folder> (default %LOCALAPPDATA%\PagentOS\test-slots), -DryRun (validates
    the request and prints DRYRUN, touches nothing), -NowUtc <ISO time> (tests only), -Seat
    <your seat on the team's board> (default $env:PAGENTOS_TEAM_SEAT).

    The team's board (scripts/team/board.ps1) hears the line: a take, a first BEKLE and a freed
    slot are one 'bilgi' note each ("Çalışan 2: birim testleri başlatıyorum (ağır), tahmini 6
    dk"), sent AFTER the queue decided, to $env:PAGENTOS_TEAM_URL with the token file of
    $env:PAGENTOS_TEAM_TOKEN_FILE. No address, no token, a board that is down: no note, and the
    answer and the exit code are the same.

    Exit codes: 0 ONAY / done; 3 BEKLE; 2 a bad request; 4 the ticket is unknown or void
    (ask again); 5 the queue itself failed; 6 DURDU, the database is held. `run` returns the
    COMMAND's exit code once the command started (127 when it could not be started), and 8
    when the command passed but the database could not be restored.

    The arguments are parsed by hand on purpose: under `powershell -File` the binder reads
    `--` as a parameter name and refuses it, so a param() block cannot take "run -- cmd".

    Examples:
      powershell -NoProfile -File scripts/team/test-slot.ps1 ask -Kind database,heavy -Task my-task -Role worker -What "api integration suite"
      powershell -NoProfile -File scripts/team/test-slot.ps1 run -Ticket ts-0123456789ab -- uv run pytest tests/integration -q -m integration
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamTestSlots.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamBoard.ps1")
. (Join-Path $repoRoot "scripts\lib\TestSlots.ps1")

function Write-Problem {
    param([string]$Text)
    [Console]::Error.WriteLine("test-slot: $Text")
}

function Exit-BadRequest {
    param([string]$Text)
    Write-Problem $Text
    Write-Problem 'kullanım: ask -Kind <database|desktop|heavy>[,...] -Task <id> -Role <role> -What "<bir satır>" | run -Ticket <bilet> -- <komut...> | status | release -Ticket <bilet> | unblock'
    exit 2
}

# ---------------------------------------------------------------------- arguments
$valueOptions = @("kind", "task", "role", "what", "ticket", "store", "nowutc", "seat")
$opts = @{}
$verb = $null
$dryRun = $false
$command = New-Object System.Collections.ArrayList
$inCommand = $false
$all = @($args)
for ($i = 0; $i -lt @($all).Count; $i++) {
    $a = $all[$i]
    if ($inCommand) { foreach ($x in @($a)) { [void]$command.Add([string]$x) }; continue }
    $text = if ($a -is [string]) { $a } else { $null }
    if ($text -eq "--") { $inCommand = $true; continue }
    if ($null -ne $text -and $text -match '^-([A-Za-z]+)$') {
        $name = $Matches[1].ToLowerInvariant()
        if ($name -eq "dryrun") { $dryRun = $true; continue }
        if ($valueOptions -contains $name) {
            if ($i + 1 -ge @($all).Count) { Exit-BadRequest "-$name needs a value" }
            $i++
            $opts[$name] = $all[$i]
            continue
        }
        if ($null -ne $verb) { Exit-BadRequest "unknown option '$text'" }
    }
    if ($null -eq $verb) { $verb = [string]$a; continue }
    # PowerShell's own `&` invocation swallows `--`: the first bare word after the options is the command.
    $inCommand = $true
    foreach ($x in @($a)) { [void]$command.Add([string]$x) }
}

if (-not $verb) { Exit-BadRequest "no verb (ask, run, status, who, release)" }
$verb = $verb.ToLowerInvariant()
$store = if ($opts.ContainsKey("store") -and $opts["store"]) { [string]$opts["store"] } else { Get-TestSlotDefaultStore }
$now = $null
if ($opts.ContainsKey("nowutc")) {
    try {
        $styles = [System.Globalization.DateTimeStyles]::AdjustToUniversal -bor [System.Globalization.DateTimeStyles]::AssumeUniversal
        $now = [datetime]::Parse([string]$opts["nowutc"], [System.Globalization.CultureInfo]::InvariantCulture, $styles)
    }
    catch { Exit-BadRequest "-NowUtc is not a time: $($opts['nowutc'])" }
}

function Get-Opt {
    param([string]$Name)
    if ($opts.ContainsKey($Name)) { return $opts[$Name] }
    return $null
}

function Get-BoardSender {
    <# The board's sender, or $null (no address or no token file: no notes). Short timeout: a
       slow board costs a run seconds, never its slot. #>
    if (-not $env:PAGENTOS_TEAM_URL -or -not $env:PAGENTOS_TEAM_TOKEN_FILE) { return $null }
    try { $script:BoardClient = New-TeamBoardClient -Url $env:PAGENTOS_TEAM_URL -TokenFile $env:PAGENTOS_TEAM_TOKEN_FILE -TimeoutSec 5 }
    catch { return $null }
    return {
        param($Note)
        $body = New-TeamBoardNoteBody -Seat $Note.seat -Task $Note.task -Kind "bilgi" -Text $Note.text -Slot $Note.slot
        [void](Send-TeamBoardNote -Client $script:BoardClient -Body $body)
    }
}

function Send-BoardEvent {
    <# After the decision: one note for it, built from the entries as they are now. Never throws. #>
    param([string]$Event, $Entry, [string]$ExitCode = "0")
    try {
        $sender = Get-BoardSender
        if ($null -eq $sender) { return }
        $note = New-TestSlotBoardNote -Event $Event -Entry $Entry -Entries @(Get-TestSlotBoardEntries -Store $store) -Store $store -ExitCode $ExitCode
        [void](Publish-TestSlotBoardNote -Note $note -Sender $sender)
    }
    catch { }
}

$seat = [string](Get-Opt "seat")
if (-not $seat -and $env:PAGENTOS_TEAM_SEAT) { $seat = [string]$env:PAGENTOS_TEAM_SEAT }

function Restore-SlotSchema {
    <# After a 'database' run (or for a dead run's guard record): put the database back at the
       recorded revision from that run's own tree, verify it, and on any failure hold the
       database. $true when it is where the run found it. -Label prefixes the restore line. #>
    param(
        [Parameter(Mandatory = $true)]$Guard,
        [string]$Label = ""
    )
    $expected = [string]$Guard.expected
    $dir = [string]$Guard.tree
    $found = "?"
    $problem = ""
    if (-not $expected -or -not $dir) { $problem = "the guard record has no revision or tree to restore" }
    else {
        $after = Get-TestSlotSchemaRevision -AlembicDir $dir
        $problem = $after.Error
        if ($after.Ok -and $after.Database -ne [string]$Guard.database) {
            # The tree's settings point elsewhere now: restoring there would touch the wrong database.
            $problem = "the tree's settings now point at '$($after.Database)', not '$($Guard.database)'"
        }
        elseif ($after.Ok -and $after.Revision -eq $expected) { Write-Problem "SEMA_KORUMA ${Label}ayni $expected"; return $true }
        elseif ($after.Ok) {
            $found = $after.Revision
            $restore = Invoke-TestSlotSchemaRestore -AlembicDir $dir -Revision $expected
            $check = Get-TestSlotSchemaRevision -AlembicDir $dir
            if ($restore.Ok -and $check.Ok -and $check.Revision -eq $expected) {
                Write-Problem "SEMA_KORUMA ${Label}geri_alindi $found -> $expected ('$($Guard.database)')"
                return $true
            }
            $problem = if (-not $restore.Ok) { $restore.Error } elseif (-not $check.Ok) { $check.Error } else { "after the downgrade the database is at '$($check.Revision)'" }
        }
    }
    $hold = [pscustomobject][ordered]@{
        at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"); task = [string]$Guard.task; role = [string]$Guard.role
        ticket = [string]$Guard.ticket; database = [string]$Guard.database; tree = $dir; found = $found; expected = $expected; error = $problem
    }
    Set-TestSlotDatabaseHold -Store $store -Hold $hold
    Write-Problem "SEMA_KORUMA BASARISIZ: $found -> $expected geri alınamadı ($problem)"
    Write-Problem (Format-TestSlotHoldLine -Hold $hold)
    return $false
}

try {
    switch ($verb) {
        "ask" {
            foreach ($need in @("kind", "task", "role", "what")) {
                $v = Get-Opt $need
                if ($null -eq $v -or -not ([string](@($v) -join ",")).Trim()) { Exit-BadRequest "ask needs -$need" }
            }
            if (@($command).Count -gt 0) { Exit-BadRequest "ask takes no command (did you mean run?)" }
            try { $kinds = @(Resolve-TestSlotKinds -Kind @(Get-Opt "kind")) } catch { Exit-BadRequest $_.Exception.Message }
            $task = [string](Get-Opt "task"); $role = [string](Get-Opt "role"); $what = [string](Get-Opt "what")
            if ($dryRun) {
                Write-Output ("DRYRUN ask kinds={0} task={1} role={2} what={3}" -f ($kinds -join ","), $task, $role, $what)
                exit 0
            }
            if ($kinds -contains "database") {
                $hold = Get-TestSlotDatabaseHold -Store $store
                if ($null -ne $hold) { Write-Output (Format-TestSlotHoldLine -Hold $hold); exit 6 }
                $left = Get-TestSlotDatabaseGuard -Store $store
                if ($null -ne $left) {
                    $state = Get-TestSlotGuardState -Guard $left
                    # The slot belongs to the wrapper (a killed one frees it): the ask is answered as
                    # usual and `run` refuses while the dead run's command still has the database.
                    if ($state -eq "orphan") { Write-Problem ("SEMA_KORUMA uyarı - run bunu söyleyecek: " + (Format-TestSlotOrphanLine -Guard $left -ProcessId ([int]$left.child_pid))) }
                    if ($state -eq "dead") { Write-Problem "SEMA_KORUMA ölen bir koşunun kaydı var ($($left.task), beklenen $($left.expected)): run komuttan önce geri alır" }
                }
            }
            $r = Invoke-TestSlotAsk -Store $store -Kind $kinds -Task $task -Role $role -What $what -NowUtc $now -Seat $seat
            Write-Output $r.Line
            if ($r.Fresh) { Send-BoardEvent -Event $(if ($r.Decision -eq "ONAY") { "take" } else { "wait" }) -Entry $r.Entry }
            if ($r.Decision -eq "ONAY") { exit 0 }
            exit 3
        }
        "run" {
            $ticket = [string](Get-Opt "ticket")
            if (-not $ticket) { Exit-BadRequest "run needs -Ticket" }
            if (@($command).Count -eq 0) { Exit-BadRequest "run needs a command after --" }
            if ($dryRun) {
                Write-Output ("DRYRUN run ticket={0} command={1}" -f $ticket, (ConvertTo-NativeArgumentLine -Arguments @($command)))
                exit 0
            }
            $entry = $null
            try { $entry = @(Start-TestSlotRun -Store $store -Ticket $ticket -HolderPid $PID -NowUtc $now) | Select-Object -Last 1 }
            catch {
                if ($_.Exception.Message -like "TICKET_VOID:*") { Write-Problem $_.Exception.Message; exit 4 }
                throw
            }
            $guard = $null
            if (@($entry.kinds) -contains "database") {
                $hold = Get-TestSlotDatabaseHold -Store $store
                if ($null -ne $hold) {
                    [void](Remove-TestSlotTicket -Store $store -Ticket $ticket)
                    Write-Problem (Format-TestSlotHoldLine -Hold $hold)
                    exit 6
                }
                # A guard record left behind: its wrapper was killed before its `finally`.
                $left = Get-TestSlotDatabaseGuard -Store $store
                if ($null -ne $left) {
                    $state = Get-TestSlotGuardState -Guard $left
                    if ($state -ne "dead") {
                        $holderPid = if ($state -eq "orphan") { [int]$left.child_pid } else { [int]$left.holder_pid }
                        [void](Remove-TestSlotTicket -Store $store -Ticket $ticket)
                        Write-Problem (Format-TestSlotOrphanLine -Guard $left -ProcessId $holderPid)
                        exit 6
                    }
                    $restoredLeft = Restore-SlotSchema -Guard $left -Label "olu_kosu "
                    Clear-TestSlotDatabaseGuard -Store $store
                    if (-not $restoredLeft) {
                        [void](Remove-TestSlotTicket -Store $store -Ticket $ticket)
                        exit 6
                    }
                }
                $cwd = (Get-Location).ProviderPath
                $alembicDir = Find-TestSlotAlembicDir -StartDirectory $cwd
                if (-not $alembicDir) { Write-Problem "SEMA_KORUMA ağaç yok: $cwd üstünde services\api\alembic.ini yok - şema kaydı ve geri alma yok" }
                else {
                    $before = Get-TestSlotSchemaRevision -AlembicDir $alembicDir
                    if (-not $before.Ok) { Write-Problem "SEMA_KORUMA kaydedilemedi ($($before.Error)) - geri alma YOK, bu koşu korumasız" }
                    elseif (-not $before.HasTable -or -not $before.Revision) { Write-Problem "SEMA_KORUMA kayıt yok: '$($before.Database)' içinde alembic_version yok - geri alma yok" }
                    elseif ($before.Revision -like "*,*") { Write-Problem "SEMA_KORUMA birden çok baş ($($before.Revision)) - geri alma yok" }
                    else {
                        Write-Problem "SEMA_KORUMA kayit $($before.Revision) ('$($before.Database)', ağaç $alembicDir)"
                        $guard = [pscustomobject][ordered]@{
                            at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"); task = [string]$entry.task; role = [string]$entry.role
                            ticket = $ticket; database = $before.Database; tree = $alembicDir; expected = $before.Revision
                            holder_pid = $PID; holder_start = [string](Get-TestSlotProcessStamp -ProcessId $PID); child_pid = 0; child_start = ""
                        }
                        # Written down before the command: a killed wrapper's promise outlives it.
                        Set-TestSlotDatabaseGuard -Store $store -Guard $guard
                    }
                }
            }
            $code = 127
            $restoreFailed = $false
            try {
                $exe = $command[0]
                if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
                    $found = @(Get-Command -Name $exe -CommandType Application -ErrorAction SilentlyContinue) | Select-Object -First 1
                    if ($null -ne $found) { $exe = $found.Source }
                }
                $rest = @()
                if (@($command).Count -gt 1) { $rest = @($command[1..(@($command).Count - 1)]) }
                # No redirection: the child inherits this process's stdout and stderr, so both
                # streams pass through untouched, separately, and nothing here can fill a pipe.
                $psi = New-Object System.Diagnostics.ProcessStartInfo $exe
                $psi.Arguments = if (@($rest).Count -gt 0) { ConvertTo-NativeArgumentLine -Arguments $rest } else { "" }
                $psi.UseShellExecute = $false
                $psi.WorkingDirectory = (Get-Location).ProviderPath
                $child = $null
                try { $child = [System.Diagnostics.Process]::Start($psi) }
                catch { Write-Problem "the command could not be started: $exe ($($_.Exception.Message))" }
                if ($null -ne $child) {
                    if ($null -ne $guard) {
                        # The command may outlive a killed wrapper: the next run must not restore under it.
                        $guard.child_pid = $child.Id
                        try { $guard.child_start = [string]$child.StartTime.ToUniversalTime().Ticks } catch { $guard.child_start = "?" }
                        try { Set-TestSlotDatabaseGuard -Store $store -Guard $guard } catch { Write-Problem "SEMA_KORUMA kaydına komutun pid'i yazılamadı: $($_.Exception.Message)" }
                    }
                    $child.WaitForExit()
                    $code = $child.ExitCode
                }
            }
            finally {
                # Still holding the slot: nobody else starts on the database while it is put back.
                if ($null -ne $guard) {
                    try {
                        $restoreFailed = -not (Restore-SlotSchema -Guard $guard)
                        # Restored, or held: either way the promise is settled (a hold speaks for itself).
                        Clear-TestSlotDatabaseGuard -Store $store
                    }
                    catch { $restoreFailed = $true; Write-Problem "SEMA_KORUMA BASARISIZ: $($_.Exception.Message) - kayıt duruyor, sonraki database koşusu yeniden dener" }
                }
                $freed = Complete-TestSlotRun -Store $store -Ticket $ticket -ExitCode ([string]$code) -PassThru
                if ($null -ne $freed) { Send-BoardEvent -Event "free" -Entry $freed -ExitCode ([string]$code) }
            }
            if ($restoreFailed -and $code -eq 0) { exit 8 }
            exit $code
        }
        "status" {
            foreach ($line in @(Get-TestSlotStatusText -Store $store -NowUtc $now)) { Write-Output $line }
            $hold = Get-TestSlotDatabaseHold -Store $store
            if ($null -ne $hold) { Write-Output (Format-TestSlotHoldLine -Hold $hold) }
            $left = Get-TestSlotDatabaseGuard -Store $store
            if ($null -ne $left) { Write-Output ("şema kaydı: {0} ({1}) '{2}' {3}'e dönecek, ağaç {4} - sahibi: {5}" -f $left.task, $left.role, $left.database, $left.expected, $left.tree, (Get-TestSlotGuardState -Guard $left)) }
            exit 0
        }
        "unblock" {
            if ($dryRun) { Write-Output "DRYRUN unblock"; exit 0 }
            $hold = Get-TestSlotDatabaseHold -Store $store
            if ($null -eq $hold) { Write-Output "kilit yok"; exit 0 }
            [void](Clear-TestSlotDatabaseHold -Store $store)
            Write-Output ("KİLİT KALDIRILDI: " + (Format-TestSlotHoldLine -Hold $hold))
            exit 0
        }
        "who" {
            # Stdout as UTF-8: an agent's Bash reads the Turkish names whole (board.ps1 does the same).
            try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
            foreach ($line in @(Get-TestSlotWhoText -Store $store -NowUtc $now)) { Write-Output $line }
            exit 0
        }
        "release" {
            $ticket = [string](Get-Opt "ticket")
            if (-not $ticket) { Exit-BadRequest "release needs -Ticket" }
            if ($dryRun) { Write-Output "DRYRUN release ticket=$ticket"; exit 0 }
            if (Remove-TestSlotTicket -Store $store -Ticket $ticket) { Write-Output "BIRAKILDI $ticket"; exit 0 }
            Write-Problem "ticket $ticket is unknown or already void"
            exit 4
        }
        default { Exit-BadRequest "unknown verb '$verb'" }
    }
}
catch {
    Write-Problem "the queue failed: $($_.Exception.Message)"
    exit 5
}
