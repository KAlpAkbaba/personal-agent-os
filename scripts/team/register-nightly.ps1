<#
.SYNOPSIS
    The nightly cycle as a Windows scheduled task: 02:00 Europe/Istanbul, home PC only.

.DESCRIPTION
    Without -Register this script changes NOTHING: it prints the task it would register
    and how to register it. The protocol opens the nightly cycle only after the pilot has
    been measured and the owner has said so (TEAM_PROTOCOL.md section 9).

    With -Register it registers the task for the current user, to run only while that user
    is logged on (no stored password, not elevated). It refuses on any machine but the home
    PC: the office PC never runs a scheduled cycle.

.EXAMPLE
    .\scripts\team\register-nightly.ps1
    .\scripts\team\register-nightly.ps1 -Register
    .\scripts\team\register-nightly.ps1 -Unregister
#>
[CmdletBinding()]
param(
    # 0 = no money cap and no time cap (owner decision 2026-09-30, ADR-0214 addendum 3):
    # the subscription has none; the cycle's one stop is the usage limit, which it waits out.
    [double]$MaxUsd = 0,
    [int]$MaxParallel = 2,
    [int]$CycleMinutes = 0,
    # ADR-0222: the queue, the lock and the live status on the Cloud Core. -QueueToken is the
    # PATH of the file scripts/team/write-queue-token.ps1 wrote (never the token itself).
    [string]$QueueUrl = "",
    [string]$QueueToken = "",
    # The continuous cycle (owner, 2026-10-01: "sürekli, kontrollü"): with -EveryMinutes N the task
    # starts a cycle every N minutes all day instead of once at 02:00. The lock keeps two from
    # running at once, a cycle with nothing to do ends in seconds, the day's cycles share one
    # integration branch (-DailyId) and the researcher runs at most every -ResearchEveryHours.
    [int]$EveryMinutes = 0,
    # The branch the workers' branches are opened from (cycle.ps1 -Base). The lead's branch
    # while it carries machinery main does not have yet; empty = the cycle's own default (main).
    [string]$Base = "",
    [double]$ResearchEveryHours = 6,
    [string]$TaskName = "PagentOS Team Nightly Cycle",
    [string]$HomeMachine = "MAIL",
    [string]$Machine = $env:COMPUTERNAME,
    [switch]$Register,
    [switch]$Unregister,
    # The Danisman's night watch (danisman-watch-in-repo): scripts/team/watch.ps1 every
    # -WatchEveryMinutes as its own task, "PagentOS Danisman Watch" unless -TaskName names one.
    [switch]$Watch,
    [int]$WatchEveryMinutes = 15,
    # The tests read the plan and register nothing.
    [switch]$PlanOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Get-NightlyPlan {
    <# What would be registered, as data: the tests read this, and so does the owner. #>
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][double]$MaxUsd,
        [Parameter(Mandatory = $true)][int]$MaxParallel,
        [Parameter(Mandatory = $true)][int]$CycleMinutes,
        [Parameter(Mandatory = $true)][System.TimeZoneInfo]$LocalZone,
        [string]$QueueUrl = "",
        [string]$QueueToken = ""
    )
    $istanbul = [System.TimeZoneInfo]::FindSystemTimeZoneById("Turkey Standard Time")
    $two = [datetime]::SpecifyKind((Get-Date).Date.AddHours(2), [System.DateTimeKind]::Unspecified)
    $utc = [System.TimeZoneInfo]::ConvertTimeToUtc($two, $istanbul)
    $local = [System.TimeZoneInfo]::ConvertTimeFromUtc($utc, $LocalZone)
    $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    # tick.ps1: the roadmap feeder first, then the cycle (ADR-0214 addendum 8).
    $script = Join-Path $RepoRoot "scripts\team\tick.ps1"
    $usd = $MaxUsd.ToString("0.##", [System.Globalization.CultureInfo]::InvariantCulture)
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -MaxUsd $usd -MaxParallel $MaxParallel -CycleMinutes $CycleMinutes"
    # No -Research: the researcher runs in EVERY cycle by default, whether the queue is full or
    # not (owner, 2026-10-01; ADR-0214 addendum 5); its proposals wait in the Onay Merkezi as
    # ideas. A task registered earlier with -Research keeps working: the cycle accepts it.
    if ($QueueUrl) {
        if (-not $QueueToken) { throw "-QueueUrl needs -QueueToken (the path of the token file)" }
        $arguments += " -QueueUrl $QueueUrl -QueueToken `"$QueueToken`""
    }
    return [pscustomobject]@{
        Execute          = $powershell
        Arguments        = $arguments
        WorkingDirectory = $RepoRoot
        LocalTime        = $local.ToString("HH:mm", [System.Globalization.CultureInfo]::InvariantCulture)
        IstanbulTime     = "02:00"
    }
}

if ($Machine.ToUpperInvariant() -ne $HomeMachine.ToUpperInvariant()) {
    Write-Host "this is $Machine; the nightly cycle runs on $HomeMachine only. Nothing was changed."
    exit 6
}

if ($Watch -and -not $PSBoundParameters.ContainsKey("TaskName")) { $TaskName = "PagentOS Danisman Watch" }

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "the task '$TaskName' is not registered."
    exit 0
}

if ($Watch) {
    # The watch looks, starts what is missing and drafts cards; it never runs the cycle itself.
    # Its output stays in %USERPROFILE%\.pagentos-team, where the chat session reads it.
    if ($WatchEveryMinutes -lt 5 -or $WatchEveryMinutes -gt 120) { throw "-WatchEveryMinutes is 5..120" }
    $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $repoRoot 'scripts\team\watch.ps1')`""
    if ($QueueUrl) {
        if (-not $QueueToken) { throw "-QueueUrl needs -QueueToken (the path of the token file)" }
        $arguments += " -QueueUrl $QueueUrl -QueueToken `"$QueueToken`""
    }
    Write-Host "task      : $TaskName"
    Write-Host "when      : every $WatchEveryMinutes minutes, all day (a look that still runs keeps the next from starting)"
    Write-Host "runs      : $powershell $arguments"
    Write-Host "in        : $repoRoot"
    Write-Host "as        : $env:USERNAME, only while logged on; no stored password, not elevated"
    if ($PlanOnly -or -not $Register) {
        Write-Host ""
        Write-Host "NOT registered. To register it, run this script again with -Register."
        exit 0
    }
    $action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $repoRoot
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes $WatchEveryMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
    # The Danisman's run has 25 minutes; the look around it a few more.
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 40) -MultipleInstances IgnoreNew -StartWhenAvailable
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    [void](Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force)
    Write-Host "registered."
    exit 0
}

$plan = Get-NightlyPlan -RepoRoot $repoRoot -MaxUsd $MaxUsd -MaxParallel $MaxParallel `
    -CycleMinutes $CycleMinutes -LocalZone ([System.TimeZoneInfo]::Local) -QueueUrl $QueueUrl -QueueToken $QueueToken
if ($EveryMinutes -gt 0) {
    $hours = $ResearchEveryHours.ToString("0.##", [System.Globalization.CultureInfo]::InvariantCulture)
    $plan.Arguments += " -DailyId -ResearchEveryHours $hours"
}
if ($Base) {
    if ($Base -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._/-]{0,80}$') { throw "-Base is a branch name" }
    $plan.Arguments += " -Base $Base"
}
Write-Host "task      : $TaskName"
Write-Host $(if ($EveryMinutes -gt 0) { "when      : every $EveryMinutes minutes, all day (one cycle at a time: the lock)" } else { "when      : every day at $($plan.LocalTime) local time ($($plan.IstanbulTime) Europe/Istanbul)" })
Write-Host "runs      : $($plan.Execute) $($plan.Arguments)"
Write-Host "in        : $($plan.WorkingDirectory)"
Write-Host "as        : $env:USERNAME, only while logged on; no stored password, not elevated"

if ($PlanOnly -or -not $Register) {
    Write-Host ""
    Write-Host "NOT registered. To register it, run this script again with -Register."
    exit 0
}

$action = New-ScheduledTaskAction -Execute $plan.Execute -Argument $plan.Arguments -WorkingDirectory $plan.WorkingDirectory
if ($EveryMinutes -gt 0) {
    # From today's midnight, every N minutes, for ten years (Windows 10 wants a duration).
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
}
else { $trigger = New-ScheduledTaskTrigger -Daily -At $plan.LocalTime }
# A zero limit is Task Scheduler's "no execution time limit".
$limit = if ($CycleMinutes -gt 0) { New-TimeSpan -Minutes ($CycleMinutes + 30) } else { New-TimeSpan -Seconds 0 }
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit $limit -MultipleInstances IgnoreNew -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
[void](Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force)
Write-Host "registered."
exit 0
