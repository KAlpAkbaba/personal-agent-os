<#
.SYNOPSIS
    The Danisman's night watch (danisman-watch-in-repo): scripts/lib/TeamWatch.ps1 decides,
    scripts/team/watch.ps1 acts.

.DESCRIPTION
    The owner, 2026-10-06 23:40: "15 dakikada bir kontrol et, takilma var mi, senden beklenen
    bir sey var mi; bulursan bir daha yasanmamasi adina duzelecek sekilde yazdir." The watch
    ran outside the repository from 23:45 that night; its first check found six cards stopped
    for a half-made worktree that nobody had reopened for thirteen hours.

    The decision cases feed the library a status, a queue and a process list and an injected
    clock. The end-to-end cases run watch.ps1 the way the scheduled task does, against
    scripts/team/fake-team-api.ps1, with a process list from a file, a launcher that only
    writes down what it was asked to start, and a model stand-in that writes the draft file.

    Run: powershell -NoProfile -File scripts\tests\team-watch.tests.ps1 [-Filter <regex>]
#>

[CmdletBinding()]
param([string]$Filter = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$utf8 = New-Object System.Text.UTF8Encoding($false)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
$libPath = Join-Path $repoRoot "scripts\lib\TeamWatch.ps1"
$watchScript = Join-Path $repoRoot "scripts\team\watch.ps1"
$script:LibLoaded = $false
if (Test-Path -LiteralPath $libPath) { . $libPath; $script:LibLoaded = $true }

$script:Failures = 0
$script:Passes = 0
$script:Sandboxes = New-Object System.Collections.ArrayList
$script:FakeApis = New-Object System.Collections.ArrayList
$script:Now = [datetime]::SpecifyKind([datetime]"2026-10-07T03:00:00", [System.DateTimeKind]::Utc)

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    if ($Filter -and $Name -notmatch $Filter) { return }
    try {
        if (-not $script:LibLoaded) { throw "scripts\lib\TeamWatch.ps1 does not exist" }
        & $Body
        $script:Passes++; Write-Host "  PASS  $Name"
    }
    catch {
        $script:Failures++
        Write-Host "  FAIL  $Name" -ForegroundColor Red
        Write-Host "        $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Assert-Equal {
    param($Expected, $Actual, [string]$Because)
    if ($Expected -ne $Actual) { throw "$Because`n          expected: <$Expected>`n          actual  : <$Actual>" }
}

function Assert-True {
    param([bool]$Condition, [string]$Because)
    if (-not $Condition) { throw $Because }
}

function Stamp { param([datetime]$At) return (Get-TeamTimestamp -Now $At) }

function New-Task {
    param([string]$Id, [string]$State = "approved", [string]$Reason = "", [datetime]$Updated = $script:Now, [string[]]$Area = @("src/x.ps1"))
    return [pscustomobject]@{
        id = $Id; title = "task $Id"; roadmap_row = "row"; state = $State; area = @($Area); depends_on = @()
        branch = ""; worktree = ""; assignee = ""; reports = @(); budget = [pscustomobject]@{ max_usd = 0 }
        created_at = (Stamp $Updated); updated_at = (Stamp $Updated); reason = $Reason
    }
}

function New-Queue { param([object[]]$Tasks = @()) return [pscustomobject]@{ version = 1; tasks = @($Tasks) } }

function New-Status {
    param([datetime]$Updated = $script:Now, [object[]]$Runs = @())
    return [pscustomobject]@{ pid = 4242; updated_at = (Stamp $Updated); runs = @($Runs) }
}

function New-Run { param([string]$Task, [string]$Role = "worker", [int]$Idle = 0) return [pscustomobject]@{ role = $Role; seat = "$Role-1"; task = $Task; idle_minutes = $Idle; stuck_children = @() } }

function CycleProcess { return [pscustomobject]@{ ProcessId = 20832; CommandLine = 'powershell.exe -File "K:\AI\repo\scripts\team\cycle.ps1" -MaxUsd 0' } }
function RoundProcess { return [pscustomobject]@{ ProcessId = 34784; CommandLine = 'powershell.exe -File "K:\AI\repo\scripts\testteam\test-round.ps1" -Round t-1' } }

function Invoke-Check {
    <# One look, as the watch takes it: the decisions only, no network, no process started. #>
    param($Status = (New-Status), $Queue = (New-Queue), [object[]]$Processes = @((CycleProcess)), $State = (New-TeamWatchState),
        [int]$MaxParallel = 4, [datetime]$Now = $script:Now, $LastRoundEnd = $script:Now.AddMinutes(-30))
    return (Invoke-TeamWatchCheck -Status $Status -Queue $Queue -Processes $Processes -State $State -MaxParallel $MaxParallel -Now $Now -LastRoundEnd $LastRoundEnd)
}

function Get-Keys { param($Check) return @($Check.Findings | ForEach-Object { [string]$_.key }) }

# ---- the end-to-end harness ----------------------------------------------------------------

function New-Sandbox {
    $work = Join-Path $env:TEMP ("pagentos-watch-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    [void](New-Item -ItemType Directory -Force -Path $work)
    [void]$script:Sandboxes.Add($work)
    return $work
}

function Start-FakeApi {
    param([object[]]$Tasks = @())
    $work = New-Sandbox
    $seed = [pscustomobject]@{ queue = (New-Queue -Tasks $Tasks); lock = (New-TeamLockReleased) }
    [System.IO.File]::WriteAllText((Join-Path $work "seed.json"), (ConvertTo-Json -InputObject $seed -Depth 12), $utf8)
    $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $probe.Start(); $port = $probe.LocalEndpoint.Port; $probe.Stop()
    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + (Join-Path $repoRoot "scripts\team\fake-team-api.ps1") + '"'),
        "-Port", $port, "-Seed", ('"' + (Join-Path $work "seed.json") + '"'), "-Log", ('"' + (Join-Path $work "requests.log") + '"'),
        "-Ready", ('"' + (Join-Path $work "ready") + '"'), "-Token", "test-token")
    $process = Start-Process -FilePath $powershell -ArgumentList $arguments -PassThru -WindowStyle Hidden
    [void]$script:FakeApis.Add($process)
    $deadline = [datetime]::UtcNow.AddSeconds(40)
    while (-not (Test-Path -LiteralPath (Join-Path $work "ready"))) {
        if ([datetime]::UtcNow -gt $deadline -or $process.HasExited) { throw "the fake team API did not start" }
        Start-Sleep -Milliseconds 200
    }
    $tokenFile = Join-Path $work "token.txt"
    [System.IO.File]::WriteAllText($tokenFile, "test-token`n", $utf8)
    $api = [pscustomobject]@{ Url = "http://127.0.0.1:$port"; Work = $work; TokenFile = $tokenFile }
    # The cycle's live status, as a running cycle writes it.
    [void](Invoke-JsonUtf8 -Uri ($api.Url + "/v1/team/queue/status") -Method PUT -Headers @{ Authorization = "Bearer test-token" } `
            -Body (ConvertTo-Json -InputObject (New-Status -Updated ([datetime]::UtcNow)) -Depth 6 -Compress))
    return $api
}

function Invoke-Watch {
    <# watch.ps1 as the scheduled task runs it; returns what it printed and what it launched. #>
    param($Api, [object[]]$Processes = @(), [string]$ModelScript = "", [switch]$NoModel, [string]$Out = "")
    if (-not $Out) { $Out = New-Sandbox }
    $procFile = Join-Path $Out "processes.json"
    [System.IO.File]::WriteAllText($procFile, (ConvertTo-Json -InputObject @($Processes) -Depth 4), $utf8)
    $launches = Join-Path $Out "launches.txt"
    $launcher = Join-Path $Out "launcher.ps1"
    [System.IO.File]::WriteAllText($launcher, "param([string]`$Kind, [string]`$Name)`r`n[System.IO.File]::AppendAllText('$launches', `"`$Kind `$Name`r`n`")`r`n", $utf8)
    $rounds = Join-Path $Out "rounds"
    if (-not (Test-Path -LiteralPath $rounds)) { [void](New-Item -ItemType Directory -Path $rounds) }
    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $watchScript, "-QueueUrl", $Api.Url, "-QueueToken", $Api.TokenFile,
        "-OutDir", $Out, "-ProcessListFile", $procFile, "-Launcher", $launcher, "-RoundsRoot", $rounds, "-CoreHealthUrl", "none", "-StagingHealthUrl", "none")
    if ($NoModel) { $arguments += "-NoModel" }
    if ($ModelScript) { $arguments += @("-ModelScript", $ModelScript) }
    $printed = & $powershell @arguments 2>&1 | Out-String
    $code = $LASTEXITCODE
    $latest = Join-Path $Out "watch-latest.txt"
    return [pscustomobject]@{
        ExitCode = $code; Printed = $printed; Out = $Out
        Latest   = $(if (Test-Path -LiteralPath $latest) { [System.IO.File]::ReadAllText($latest, $utf8) } else { "" })
        Launches = $(if (Test-Path -LiteralPath $launches) { @([System.IO.File]::ReadAllLines($launches) | Where-Object { $_ }) } else { @() })
    }
}

function Get-StoredIds { param($Api) return @((Invoke-JsonUtf8 -Uri ($Api.Url + "/__state")).tasks | ForEach-Object { [string]$_.id }) }

function New-ModelScript {
    <# Stands in for the Danisman's run: writes $Drafts to the draft file the prompt names. #>
    param([object[]]$Drafts)
    $dir = New-Sandbox
    $json = Join-Path $dir "drafts.json"
    [System.IO.File]::WriteAllText($json, (ConvertTo-Json -InputObject @($Drafts) -Depth 6), $utf8)
    $path = Join-Path $dir "model.ps1"
    $body = @"
param([string]`$PromptFile)
`$prompt = [System.IO.File]::ReadAllText(`$PromptFile, [System.Text.Encoding]::UTF8)
`$draft = [regex]::Match(`$prompt, 'DRAFT_FILE: (.+)').Groups[1].Value.Trim()
Copy-Item -LiteralPath '$json' -Destination `$draft
Write-Output 'iki kart yazdim'
"@
    [System.IO.File]::WriteAllText($path, $body, $utf8)
    return $path
}

function New-Draft {
    param([string]$Id, [string[]]$Area = @("scripts/team/x.ps1"))
    return [pscustomobject]@{ id = $Id; title = "kart $Id"; roadmap_row = "row"; area = @($Area); depends_on = @(); goal = "g"; acceptance = "a"; evidence_expected = "e" }
}

Write-Host ""
Write-Host "the Danisman's night watch (TeamWatch.ps1 decides, watch.ps1 acts)"

try {
    # ---- the cycle -------------------------------------------------------------------------
    Test-Case "no cycle process: the nightly task is to be started, and it is a finding" {
        $check = Invoke-Check -Processes @()
        Assert-True (@($check.Actions) -contains "start-cycle") "no cycle runs: the watch starts the nightly task (actions: $(@($check.Actions) -join ','))"
        Assert-True ((Get-Keys $check) -contains "cycle-not-running") "and says so (findings: $((Get-Keys $check) -join ','))"
    }
    Test-Case "a cycle that runs and wrote its status a minute ago: nothing to do" {
        $check = Invoke-Check -Status (New-Status -Updated $script:Now.AddMinutes(-1))
        Assert-Equal 0 @($check.Findings).Count "a healthy cycle is no finding ($((Get-Keys $check) -join ','))"
        Assert-Equal 0 @($check.Actions).Count "and nothing is started"
    }
    Test-Case "a cycle whose status is 20 minutes old: a stale-status finding, nothing started" {
        $check = Invoke-Check -Status (New-Status -Updated $script:Now.AddMinutes(-20))
        Assert-True ((Get-Keys $check) -contains "cycle-stale") "a status older than 15 minutes is a finding ($((Get-Keys $check) -join ','))"
        Assert-True (@($check.Actions) -notcontains "start-cycle") "a living cycle is never started twice"
    }

    # ---- the seats -------------------------------------------------------------------------
    Test-Case "idle seats beside a runnable card: once is no finding, twice in a row is" {
        $queue = New-Queue -Tasks @((New-Task -Id "ready-card"))
        $status = New-Status -Runs @((New-Run -Task "other"))
        $first = Invoke-Check -Status $status -Queue $queue
        Assert-True ((Get-Keys $first) -notcontains "idle-seats") "one look at an idle seat is not 30 minutes ($((Get-Keys $first) -join ','))"
        $second = Invoke-Check -Status $status -Queue $queue -State $first.State -Now $script:Now.AddMinutes(15)
        Assert-True ((Get-Keys $second) -contains "idle-seats") "the second look in a row is ($((Get-Keys $second) -join ','))"
    }
    Test-Case "full seats, or no runnable card: the idle streak starts again" {
        $queue = New-Queue -Tasks @((New-Task -Id "ready-card"))
        $idle = Invoke-Check -Status (New-Status -Runs @((New-Run -Task "a"))) -Queue $queue
        $full = Invoke-Check -Status (New-Status -Runs @((New-Run -Task "a"), (New-Run -Task "b"))) -Queue $queue -State $idle.State -MaxParallel 2
        $again = Invoke-Check -Status (New-Status -Runs @((New-Run -Task "a"))) -Queue $queue -State $full.State
        Assert-True ((Get-Keys $again) -notcontains "idle-seats") "a full look between resets the streak ($((Get-Keys $again) -join ','))"
    }
    Test-Case "a run idle for 30 minutes is a finding of its own" {
        $check = Invoke-Check -Status (New-Status -Runs @((New-Run -Task "slow" -Idle 31)))
        Assert-True ((Get-Keys $check) -contains "run-idle-slow") "($((Get-Keys $check) -join ','))"
    }

    # ---- the cards -------------------------------------------------------------------------
    Test-Case "a stop handed to the Danisman more than an hour ago is a finding of its own" {
        $old = New-Task -Id "handed-over" -State "stopped" -Reason ((Get-TeamDutyEscalatedPrefix) + "yarim worktree") -Updated $script:Now.AddMinutes(-61)
        $state = New-TeamWatchState
        $state.last_cards = @("stopped/handed-over")
        $check = Invoke-Check -Queue (New-Queue -Tasks @($old)) -State $state
        Assert-True ((Get-Keys $check) -contains "escalated-handed-over") "an hour on the Danisman's desk is a finding ($((Get-Keys $check) -join ','))"
    }
    Test-Case "a stop handed over 30 minutes ago, or stopped for another reason, is not" {
        $young = New-Task -Id "young" -State "stopped" -Reason ((Get-TeamDutyEscalatedPrefix) + "x") -Updated $script:Now.AddMinutes(-30)
        $other = New-Task -Id "plain" -State "stopped" -Reason "worktree yarim" -Updated $script:Now.AddHours(-5)
        $state = New-TeamWatchState
        $state.last_cards = @("stopped/young", "stopped/plain")
        $check = Invoke-Check -Queue (New-Queue -Tasks @($young, $other)) -State $state
        Assert-True (@(Get-Keys $check | Where-Object { $_ -like "escalated-*" }).Count -eq 0) "($((Get-Keys $check) -join ','))"
    }
    Test-Case "a NEW stopped card is a finding; the same card on the next look is not" {
        $queue = New-Queue -Tasks @((New-Task -Id "broke" -State "stopped" -Reason "x"))
        $first = Invoke-Check -Queue $queue
        Assert-True ((Get-Keys $first) -contains "cards-stopped-broke") "($((Get-Keys $first) -join ','))"
        $second = Invoke-Check -Queue $queue -State $first.State
        Assert-True (@(Get-Keys $second | Where-Object { $_ -like "cards-*" }).Count -eq 0) "($((Get-Keys $second) -join ','))"
    }

    # ---- the three-hour memory -------------------------------------------------------------
    Test-Case "a finding seen within three hours is not drafted again; after three hours it is" {
        $finding = [pscustomobject]@{ key = "cycle-stale"; text = "x" }
        $state = New-TeamWatchState
        $first = @(Select-TeamWatchFresh -Findings @($finding) -State $state -Now $script:Now)
        Assert-Equal 1 $first.Count "a finding never seen is fresh"
        $again = @(Select-TeamWatchFresh -Findings @($finding) -State $state -Now $script:Now.AddMinutes(170))
        Assert-Equal 0 $again.Count "2 h 50 min later it is remembered"
        $later = @(Select-TeamWatchFresh -Findings @($finding) -State $state -Now $script:Now.AddMinutes(181))
        Assert-Equal 1 $later.Count "3 h 1 min after it was drafted it is fresh again"
    }
    Test-Case "the memory survives the file it is written to" {
        $state = New-TeamWatchState
        [void](Select-TeamWatchFresh -Findings @([pscustomobject]@{ key = "k1"; text = "x" }) -State $state -Now $script:Now)
        $state.idle_streak = 1
        $path = Join-Path (New-Sandbox) "watch-state.json"
        Write-TeamWatchState -Path $path -State $state
        $read = Read-TeamWatchState -Path $path
        Assert-Equal 1 $read.idle_streak "the idle streak is read back"
        Assert-Equal 0 @(Select-TeamWatchFresh -Findings @([pscustomobject]@{ key = "k1"; text = "x" }) -State $read -Now $script:Now.AddHours(1)).Count "the seen key is read back"
    }

    # ---- the drafts ------------------------------------------------------------------------
    Test-Case "a draft that breaks Test-TeamQueue is not queued; a sound one is, approved" {
        $queue = New-Queue -Tasks @((New-Task -Id "existing"))
        $made = ConvertTo-TeamWatchCards -Drafts @((New-Draft -Id "good-card"), (New-Draft -Id "bad-area" -Area @("C:/outside")), (New-Draft -Id "existing")) -Queue $queue -Now $script:Now
        Assert-Equal "good-card" (@($made.Cards | ForEach-Object { $_.id }) -join ",") "only the sound new card is queued"
        Assert-Equal "approved" ([string]@($made.Cards)[0].state) "the watch's card is approved, for the next release"
        Assert-True ((@($made.Skipped) -join " ") -match "bad-area") "the broken draft is named ($(@($made.Skipped) -join ' | '))"
        Assert-True ((@($made.Skipped) -join " ") -match "existing") "a draft of an existing id is named"
    }
    Test-Case "at most three drafts are taken" {
        $drafts = @(1..5 | ForEach-Object { New-Draft -Id "card-$_" -Area @("scripts/a$_.ps1") })
        $made = ConvertTo-TeamWatchCards -Drafts $drafts -Queue (New-Queue) -Now $script:Now
        Assert-Equal 3 @($made.Cards).Count "three, not five"
    }

    # ---- the test team ---------------------------------------------------------------------
    Test-Case "no test round for two hours: a round is started, once" {
        $first = Invoke-Check -LastRoundEnd $script:Now.AddHours(-2.5)
        Assert-True (@($first.Actions) -contains "start-round") "2.5 h without a round starts one (actions: $(@($first.Actions) -join ','))"
        $second = Invoke-Check -LastRoundEnd $script:Now.AddHours(-2.5) -State $first.State -Now $script:Now.AddMinutes(15)
        Assert-True (@($second.Actions) -notcontains "start-round") "the next look, before the round wrote anything, does not start a second"
    }
    Test-Case "a round that runs, or ended an hour ago: none is started" {
        $running = Invoke-Check -Processes @((CycleProcess), (RoundProcess)) -LastRoundEnd $script:Now.AddHours(-5)
        Assert-True (@($running.Actions) -notcontains "start-round") "a running round is enough"
        $recent = Invoke-Check -LastRoundEnd $script:Now.AddHours(-1)
        Assert-True (@($recent.Actions) -notcontains "start-round") "an hour is not two"
    }

    # ---- the scheduled task ----------------------------------------------------------------
    Test-Case "register-nightly.ps1 -Watch plans 'PagentOS Danisman Watch': watch.ps1 every 15 minutes, with the queue's token path" {
        $printed = & $powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot "scripts\team\register-nightly.ps1") -Watch -PlanOnly -Machine MAIL `
            -QueueUrl "http://127.0.0.1:9" -QueueToken "C:\t\token.txt" 2>&1 | Out-String
        Assert-Equal 0 $LASTEXITCODE "the plan is printed ($printed)"
        Assert-True ($printed -match "task\s+: PagentOS Danisman Watch") "the watch's own task name ($printed)"
        Assert-True ($printed -match [regex]::Escape("scripts\team\watch.ps1")) "it runs watch.ps1"
        Assert-True ($printed -match "every 15 minutes") "every 15 minutes"
        Assert-True ($printed -match [regex]::Escape('-QueueToken "C:\t\token.txt"')) "with the token's PATH"
        Assert-True ($printed -notmatch "tick\.ps1") "not the cycle's tick"
    }

    # ---- watch.ps1, end to end --------------------------------------------------------------
    Test-Case "watch.ps1: no cycle process -> the launcher is asked for the cycle, the output files are written" {
        $api = Start-FakeApi
        $run = Invoke-Watch -Api $api -Processes @((RoundProcess)) -NoModel
        Assert-Equal 0 $run.ExitCode "the watch ends 0 ($($run.Printed))"
        Assert-True (@($run.Launches) -contains "cycle PagentOS Team Nightly Cycle") "the nightly task is started (launches: $(@($run.Launches) -join ' | '); $($run.Printed))"
        Assert-True ($run.Latest -match "dongu sureci calismiyor") "watch-latest.txt says why ($($run.Latest))"
        Assert-True (Test-Path -LiteralPath (Join-Path $run.Out "watch.log")) "watch.log is appended"
        Assert-True (Test-Path -LiteralPath (Join-Path $run.Out "watch-state.json")) "the memory is kept"
    }
    Test-Case "watch.ps1: a sound draft is queued approved, a broken one is not, and the same finding is not drafted twice" {
        $api = Start-FakeApi -Tasks @((New-Task -Id "existing"))
        $model = New-ModelScript -Drafts @((New-Draft -Id "watch-good"), (New-Draft -Id "watch-bad" -Area @("../escape")))
        $out = New-Sandbox
        $run = Invoke-Watch -Api $api -Processes @() -ModelScript $model -Out $out
        Assert-Equal 0 $run.ExitCode "the watch ends 0 ($($run.Printed))"
        $ids = Get-StoredIds -Api $api
        Assert-True ($ids -contains "watch-good") "the sound draft is in the queue (ids: $($ids -join ','); $($run.Latest))"
        Assert-True ($ids -notcontains "watch-bad") "the broken draft is not"
        $stored = @((Invoke-JsonUtf8 -Uri ($api.Url + "/__state")).tasks | Where-Object { $_.id -eq "watch-good" })[0]
        Assert-Equal "approved" ([string]$stored.state) "queued approved"
        $marker = Join-Path $out "model-ran.txt"
        $model2 = New-ModelScript -Drafts @()
        [System.IO.File]::AppendAllText($model2, "`r`n[System.IO.File]::WriteAllText('$marker', 'ran')`r`n", $utf8)
        $again = Invoke-Watch -Api $api -Processes @() -ModelScript $model2 -Out $out
        Assert-True (-not (Test-Path -LiteralPath $marker)) "the same finding 15 minutes later starts no second Danisman run ($($again.Latest))"
    }
}
finally {
    foreach ($p in @($script:FakeApis)) { try { if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force } } catch { } }
    foreach ($d in @($script:Sandboxes)) { Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Host ""
Write-Host ("{0} passed, {1} failed" -f $script:Passes, $script:Failures)
if ($script:Failures -gt 0) { exit 1 }
exit 0
