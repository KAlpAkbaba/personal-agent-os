<#
.SYNOPSIS
    One look at the team cycle: a live cycle of this machine whose status has not been
    written for 15 minutes is stopped (with its runs and its tick, never Docker/WSL), its lock
    released and the scheduled task started again (card cycle-watchdog).

.DESCRIPTION
    Meant for the 30-minute "PagentOS Team Feeder" task (the Danisman wires it), before
    feed.ps1. A fresh status is never touched. A second restart inside two hours is not made:
    the Danisman is asked on the board instead - a loop is not a fix. The rules and the reasons
    are in scripts/lib/TeamWatchdog.ps1.

    Exit 0 whatever it decided (the feeder after it must run); exit 1 only when it could not
    look at all (the lock could not be read), and the log says why.

.EXAMPLE
    .\scripts\team\watchdog.ps1 -QueueUrl http://100.90.158.26:8001 -QueueToken "$env:LOCALAPPDATA\PagentOS\team-queue.token"
#>
[CmdletBinding()]
param(
    # The Cloud Core's queue (as cycle.ps1): the lock and the status are read there, and the
    # board is the same server's. Without it, the files under -TeamRoot.
    [string]$QueueUrl = "",
    [string]$QueueToken = "",
    [string]$TeamRoot = "",
    [string]$Machine = $env:COMPUTERNAME,
    [string]$TaskName = "PagentOS Team Nightly Cycle",
    [double]$StaleMinutes = 15,
    [double]$LoopHours = 2,
    # Where the restarts are remembered and the log is written (default team/logs/, git-ignored).
    [string]$HistoryPath = "",
    [string]$LogPath = "",
    # A test hook: a script run with -TaskName in place of `schtasks /Run`.
    [string]$RunTaskPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamTickKeep.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamBoard.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamWatchdog.ps1")

$teamFolder = if ($TeamRoot) { $TeamRoot } else { Join-Path $repoRoot "team" }
$historyFile = if ($HistoryPath) { $HistoryPath } else { Join-Path $teamFolder "logs\watchdog-restarts.json" }
$logFile = if ($LogPath) { $LogPath } else { Join-Path $teamFolder "logs\watchdog.log" }
$reportsRoot = Join-Path $teamFolder "reports"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

function Write-WatchdogLog {
    param([string]$Text)
    Write-Host "watchdog: $Text"
    try {
        $folder = Split-Path -Parent $logFile
        if ($folder -and -not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
        $line = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss", [System.Globalization.CultureInfo]::InvariantCulture) + " " + $Text
        [System.IO.File]::AppendAllText($logFile, $line + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
    }
    catch { Write-Host "watchdog: the log could not be written: $($_.Exception.Message)" }
}

$store = $null
$board = $null
if ($QueueUrl) {
    if (-not $QueueToken) { throw "-QueueUrl needs -QueueToken (the path of the token file)" }
    $store = New-TeamApiStore -Url $QueueUrl -TokenFile $QueueToken
    try { $board = New-TeamBoardClient -Url $QueueUrl -TokenFile $QueueToken } catch { Write-WatchdogLog "no board: $($_.Exception.Message)" }
}
$lockFile = Join-Path $teamFolder "lock.json"
$statusFile = Join-Path $teamFolder "status.json"

$io = @{
    HistoryPath = $historyFile
    ReadLock    = {
        if ($null -ne $store) { return (Get-TeamLockApi -Store $store) }
        if (Test-Path -LiteralPath $lockFile) { return (Read-TeamJson -Path $lockFile) }
        return $null
    }
    ReadStatus  = {
        if ($null -ne $store) { return (Invoke-TeamApi -Store $store -Method "GET" -Path "/v1/team/queue/status") }
        if (Test-Path -LiteralPath $statusFile) { return (Read-TeamJson -Path $statusFile) }
        return $null
    }
    Snapshot    = { Get-TeamWatchdogSnapshot }
    IsAlive     = { param($Id) $null -ne (Get-Process -Id $Id -ErrorAction SilentlyContinue) }
    StopProcess = { param($Id) Stop-Process -Id $Id -Force -ErrorAction Stop }
    ReleaseLock = {
        param($Lock)
        $cycleId = [string](Get-TeamProperty -InputObject $Lock -Name "cycle_id" -Default "")
        if ($null -ne $store) { Clear-TeamLockApi -Store $store -Machine $Machine -CycleId $cycleId; return }
        # Only the lock we judged: a cycle that took it meanwhile keeps it.
        $now = Read-TeamJson -Path $lockFile
        if ([int](Get-TeamProperty -InputObject $now -Name "pid" -Default 0) -eq [int](Get-TeamProperty -InputObject $Lock -Name "pid" -Default 0)) {
            Write-TeamJson -Path $lockFile -Document (New-TeamLockReleased)
        }
    }
    StartTask   = {
        if ($RunTaskPath) { & $powershell -NoProfile -ExecutionPolicy Bypass -File $RunTaskPath -TaskName $TaskName | Out-Null }
        else {
            $ran = Invoke-NativeProcess -FilePath (Join-Path $env:SystemRoot "System32\schtasks.exe") -Arguments @("/Run", "/TN", $TaskName) -TimeoutSeconds 60
            if (-not $ran.Success) { Write-WatchdogLog "schtasks /Run '$TaskName' failed: exit $($ran.ExitCode) $($ran.StdErr.Trim())" }
        }
        Write-WatchdogLog "the scheduled task '$TaskName' was started again"
    }
    Post        = {
        param($Kind, $To, $Text)
        if ($null -eq $board) { Write-WatchdogLog "board not reachable here; note: $Text"; return }
        try {
            $short = if ($Text.Length -gt 280) { $Text.Substring(0, 280) } else { $Text }
            [void](Send-TeamBoardNote -Client $board -Body (New-TeamBoardNoteBody -Seat "lead" -Task "cycle-watchdog" -Kind $Kind -Text $short -To $To))
        }
        catch { Write-WatchdogLog "UYARI: the board refused the note: $($_.Exception.Message)" }
    }
    Report      = {
        param($CycleId, $Text)
        try { Add-TeamWatchdogReportLine -ReportsRoot $reportsRoot -CycleId $CycleId -Text $Text }
        catch { Write-WatchdogLog "the report could not be written: $($_.Exception.Message)" }
    }
    Log         = { param($Text) Write-WatchdogLog $Text }
}

try {
    $result = Invoke-TeamWatchdog -Io $io -Machine $Machine -StaleMinutes $StaleMinutes -LoopHours $LoopHours
}
catch {
    Write-WatchdogLog "could not look: $($_.Exception.Message -replace '\s+', ' ')"
    exit 1
}
Write-Host "watchdog: $($result.Kind)"
exit 0
