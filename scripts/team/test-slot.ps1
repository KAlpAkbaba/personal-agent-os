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
      status  The queue in Turkish: who runs what since when, who waits.
      who     Short, by seat name: who is testing what right now and the waiting line -
              ask it before you plan a heavy run.
      release -Ticket <ticket>   gives a ticket (or a place in line) back.

    Common: -Store <folder> (default %LOCALAPPDATA%\PagentOS\test-slots), -DryRun (validates
    the request and prints DRYRUN, touches nothing), -NowUtc <ISO time> (tests only), -Seat
    <your seat on the team's board> (default $env:PAGENTOS_TEAM_SEAT).

    The team's board (scripts/team/board.ps1) hears the line: a take, a first BEKLE and a freed
    slot are one 'bilgi' note each ("Çalışan 2: birim testleri başlatıyorum (ağır), tahmini 6
    dk"), sent AFTER the queue decided, to $env:PAGENTOS_TEAM_URL with the token file of
    $env:PAGENTOS_TEAM_TOKEN_FILE. No address, no token, a board that is down: no note, and the
    answer and the exit code are the same.

    Exit codes: 0 ONAY / done; 3 BEKLE; 2 a bad request; 4 the ticket is unknown or void
    (ask again); 5 the queue itself failed. `run` returns the COMMAND's exit code once the
    command started (127 when it could not be started).

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

function Write-Problem {
    param([string]$Text)
    [Console]::Error.WriteLine("test-slot: $Text")
}

function Exit-BadRequest {
    param([string]$Text)
    Write-Problem $Text
    Write-Problem 'kullanım: ask -Kind <database|desktop|heavy>[,...] -Task <id> -Role <role> -What "<bir satır>" | run -Ticket <bilet> -- <komut...> | status | release -Ticket <bilet>'
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
            try { [void](Start-TestSlotRun -Store $store -Ticket $ticket -HolderPid $PID -NowUtc $now) }
            catch {
                if ($_.Exception.Message -like "TICKET_VOID:*") { Write-Problem $_.Exception.Message; exit 4 }
                throw
            }
            $code = 127
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
                    $child.WaitForExit()
                    $code = $child.ExitCode
                }
            }
            finally {
                $freed = Complete-TestSlotRun -Store $store -Ticket $ticket -ExitCode ([string]$code) -PassThru
                if ($null -ne $freed) { Send-BoardEvent -Event "free" -Entry $freed -ExitCode ([string]$code) }
            }
            exit $code
        }
        "status" {
            foreach ($line in @(Get-TestSlotStatusText -Store $store -NowUtc $now)) { Write-Output $line }
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
