<#
.SYNOPSIS
    The Danisman's night watch, its decisions (danisman-watch-in-repo; scripts/team/watch.ps1).

.DESCRIPTION
    The owner, 2026-10-06 23:40: "15 dakikada bir kontrol et, takilma var mi, senden beklenen
    bir sey var mi; bulursan bir daha yasanmamasi adina duzelecek sekilde yazdir." Every 15
    minutes the watch looks at the cycle's live status, the queue and the machine's process
    list, and this file says what it sees:

      * the cycle: no cycle.ps1 process -> start the nightly task; a status older than 15
        minutes -> a finding;
      * the seats: a free worker seat beside a runnable card on two looks in a row (30
        minutes) -> a finding; a run idle 30+ minutes, or with stuck children -> a finding;
      * the cards: a NEW card in awaiting_owner / returned / stopped -> a finding; a stop the
        Proje Yoneticisi handed to the Danisman (reason "Danisman'a iletildi: ", TeamQueue's
        own prefix) that has waited more than an hour -> a finding of its own (2026-10-06: six
        cards stopped at 10:30 waited thirteen hours);
      * the test team: no round running and none ended or started for two hours -> start one,
        once (the start is remembered, so the next look does not start a second).

    Only a finding not seen in the last three hours starts the Danisman's run
    (Select-TeamWatchFresh), and of what that run drafts only cards the queue's own rules
    accept are queued, as approved, at most three (ConvertTo-TeamWatchCards).

    Nothing here touches the network, a process or a file except the memory's own
    (Read-/Write-TeamWatchState). The clock is a parameter. Needs TeamQueue.ps1.
    Windows PowerShell 5.1, StrictMode.
#>

Set-StrictMode -Version Latest

$script:TeamWatchStatusStaleMinutes = 15
$script:TeamWatchIdleLooks = 2
$script:TeamWatchRunIdleMinutes = 30
$script:TeamWatchEscalatedHours = 1
$script:TeamWatchRoundIdleHours = 2
$script:TeamWatchMemoryHours = 3
$script:TeamWatchMaxCards = 3
$script:TeamWatchWaitingStates = @("awaiting_owner", "returned", "stopped")

function Get-TeamDutyEscalatedPrefix {
    <# The reason a stop handed to the Danisman starts with: TeamQueue's, never a copy. #>
    return $script:TeamDutyEscalated
}

function New-TeamWatchState {
    return [pscustomobject]@{ seen = @{}; idle_streak = 0; last_round_start = ""; last_cards = @() }
}

function Read-TeamWatchState {
    <# The memory; a missing or broken file is a fresh memory, never a stopped watch. #>
    param([Parameter(Mandatory = $true)][string]$Path)
    $state = New-TeamWatchState
    if (-not (Test-Path -LiteralPath $Path)) { return $state }
    try { $raw = ConvertFrom-Json -InputObject ([System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)) } catch { return $state }
    if ($null -eq $raw) { return $state }
    $seen = Get-TeamProperty -InputObject $raw -Name "seen"
    if ($null -ne $seen) { foreach ($p in $seen.PSObject.Properties) { $state.seen[$p.Name] = [string]$p.Value } }
    $streak = Get-TeamProperty -InputObject $raw -Name "idle_streak" -Default 0
    if ($streak -is [ValueType]) { $state.idle_streak = [int]$streak }
    $state.last_round_start = [string](Get-TeamProperty -InputObject $raw -Name "last_round_start" -Default "")
    $state.last_cards = @(Get-TeamProperty -InputObject $raw -Name "last_cards" -Default @() | ForEach-Object { [string]$_ })
    return $state
}

function Write-TeamWatchState {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)]$State)
    $seen = [ordered]@{}
    foreach ($k in @($State.seen.Keys | Sort-Object)) { $seen[$k] = [string]$State.seen[$k] }
    $document = [ordered]@{ seen = $seen; idle_streak = [int]$State.idle_streak; last_round_start = [string]$State.last_round_start; last_cards = @($State.last_cards) }
    [System.IO.File]::WriteAllText($Path, (ConvertTo-Json -InputObject $document -Depth 4), (New-Object System.Text.UTF8Encoding($false)))
}

function Resolve-TeamWatchRoleFile {
    <# The Danisman run's role: .claude\agents\danisman-watch.md when the lead placed it, else the
       repository's own scripts\team\danisman-watch-role.md. Never a file outside the repository
       (an untested file there is not a mechanism); neither -> throws, the watch reports it. #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot)
    foreach ($relative in @(".claude\agents\danisman-watch.md", "scripts\team\danisman-watch-role.md")) {
        $path = Join-Path $RepoRoot $relative
        if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
    }
    throw "rol dosyasi yok: .claude\agents\danisman-watch.md ve scripts\team\danisman-watch-role.md ($RepoRoot)"
}

function Test-TeamWatchCommandLine {
    param($Processes, [Parameter(Mandatory = $true)][string]$Pattern)
    return @(@($Processes) | Where-Object { $null -ne $_ -and ([string](Get-TeamProperty -InputObject $_ -Name "CommandLine" -Default "")) -match $Pattern })
}

function Invoke-TeamWatchCheck {
    <#
    .SYNOPSIS
        One look. Returns { Findings = @({key,text}), Actions = @("start-cycle"|"start-round"),
        Lines = @(text), State = the memory after this look }. -LastRoundEnd: when the newest
        test round folder was last written, $null when there is none.
    #>
    param(
        $Status,
        $Queue,
        [object[]]$Processes = @(),
        [Parameter(Mandatory = $true)]$State,
        [int]$MaxParallel = 4,
        [Parameter(Mandatory = $true)][datetime]$Now,
        $LastRoundEnd = $null
    )
    $findings = New-Object System.Collections.ArrayList
    $actions = New-Object System.Collections.ArrayList
    $lines = New-Object System.Collections.ArrayList
    $find = { param($key, $text) [void]$findings.Add([pscustomobject]@{ key = $key; text = $text }); [void]$lines.Add("SORUN: " + $text) }
    $next = [pscustomobject]@{
        seen = $State.seen; idle_streak = [int]$State.idle_streak
        last_round_start = [string]$State.last_round_start; last_cards = @($State.last_cards)
    }
    $tasks = @(Get-TeamTasks -Queue $Queue)

    # 1. The cycle: alive, and writing its status.
    $cycle = @(Test-TeamWatchCommandLine -Processes $Processes -Pattern 'scripts\\team\\cycle\.ps1')
    $updated = ConvertFrom-TeamTimestamp -Text ([string](Get-TeamProperty -InputObject $Status -Name "updated_at" -Default ""))
    $age = if ($null -ne $updated) { [int][Math]::Round(($Now - $updated).TotalMinutes) } else { -1 }
    if (@($cycle).Count -eq 0) {
        & $find "cycle-not-running" "dongu sureci calismiyor (son durum $age dk once)"
        [void]$actions.Add("start-cycle")
    }
    elseif ($age -lt 0 -or $age -gt $script:TeamWatchStatusStaleMinutes) {
        & $find "cycle-stale" "dongu canli ama durumu $age dk once yazilmis"
    }
    else { [void]$lines.Add("dongu calisiyor (pid $(Get-TeamProperty -InputObject $Status -Name 'pid' -Default '?'), durum $age dk once)") }

    # 2. The seats.
    $runs = @(Get-TeamProperty -InputObject $Status -Name "runs" -Default @() | Where-Object { $null -ne $_ })
    $workers = @($runs | Where-Object { [string](Get-TeamProperty -InputObject $_ -Name "role" -Default "") -eq "worker" })
    $runnable = @($tasks | Where-Object { [string]$_.state -eq "approved" -and @(Get-TeamUnmetDependencies -Task $_ -Queue $Queue).Count -eq 0 })
    [void]$lines.Add(("kosular: {0} (calisan {1}/{2}), baslayabilir kart {3}" -f @($runs).Count, @($workers).Count, $MaxParallel, @($runnable).Count))
    if (@($cycle).Count -gt 0 -and @($workers).Count -lt $MaxParallel -and @($runnable).Count -gt 0) { $next.idle_streak++ } else { $next.idle_streak = 0 }
    if ($next.idle_streak -ge $script:TeamWatchIdleLooks) {
        & $find "idle-seats" ("bos calisan koltugu var ({0}/{1}) ama baslayabilir {2} kart bekliyor (30+ dk): {3}" -f @($workers).Count, $MaxParallel, @($runnable).Count, ((@($runnable | Select-Object -First 4 | ForEach-Object { $_.id })) -join ", "))
    }
    foreach ($r in $runs) {
        $task = [string](Get-TeamProperty -InputObject $r -Name "task" -Default "")
        $idle = Get-TeamProperty -InputObject $r -Name "idle_minutes" -Default 0
        if ($idle -is [ValueType] -and [int]$idle -ge $script:TeamWatchRunIdleMinutes) {
            & $find ("run-idle-" + $task) ("{0} koltugu {1} ({2}) {3} dk hareketsiz" -f $r.role, (Get-TeamProperty -InputObject $r -Name "seat" -Default "?"), $task, $idle)
        }
        $stuck = @(Get-TeamProperty -InputObject $r -Name "stuck_children" -Default @() | Where-Object { $null -ne $_ })
        if (@($stuck).Count -gt 0) {
            & $find ("run-stuck-" + $task) ("{0} ({1}) takili alt surec: {2}" -f $r.role, $task, (ConvertTo-Json -InputObject $stuck -Compress -Depth 3))
        }
    }

    # 3. The cards that wait.
    $current = New-Object System.Collections.ArrayList
    foreach ($s in $script:TeamWatchWaitingStates) {
        $ids = @($tasks | Where-Object { [string]$_.state -eq $s } | ForEach-Object { [string]$_.id })
        foreach ($id in $ids) { [void]$current.Add("$s/$id") }
        $new = @($ids | Where-Object { @($State.last_cards) -notcontains "$s/$_" })
        if (@($new).Count -gt 0) { & $find ("cards-$s-" + ($new -join ",")) ("yeni '$s' kart: " + ($new -join ", ")) }
    }
    $next.last_cards = @($current.ToArray())
    $prefix = Get-TeamDutyEscalatedPrefix
    foreach ($t in @($tasks | Where-Object { [string]$_.state -eq "stopped" })) {
        $reason = [string](Get-TeamProperty -InputObject $t -Name "reason" -Default "")
        if (-not $reason.StartsWith($prefix, [System.StringComparison]::Ordinal)) { continue }
        $since = ConvertFrom-TeamTimestamp -Text ([string](Get-TeamProperty -InputObject $t -Name "updated_at" -Default ""))
        if ($null -eq $since) { continue }
        $hours = ($Now - $since).TotalHours
        if ($hours -gt $script:TeamWatchEscalatedHours) {
            & $find ("escalated-" + [string]$t.id) ("'{0}' {1:0.0} saattir Danismanin masasinda bekliyor: {2}" -f $t.id, $hours, $reason)
        }
    }
    foreach ($s in @("merged", "awaiting_release")) {
        $count = @($tasks | Where-Object { [string]$_.state -eq $s }).Count
        if ($count -gt 0) { [void]$lines.Add("yayin bekleyen ($s): $count kart - Danismanin yayini") }
    }

    # 4. The test team.
    $round = @(Test-TeamWatchCommandLine -Processes $Processes -Pattern 'scripts\\testteam\\test-round\.ps1')
    if (@($round).Count -gt 0) { [void]$lines.Add("test turu calisiyor (pid $($round[0].ProcessId))") }
    else {
        $lastEnd = if ($null -ne $LastRoundEnd) { ([datetime]$LastRoundEnd).ToUniversalTime() } else { [datetime]::MinValue }
        $lastStart = ConvertFrom-TeamTimestamp -Text $State.last_round_start
        if ($null -eq $lastStart) { $lastStart = [datetime]::MinValue }
        $idleHours = ($Now - $lastEnd).TotalHours
        if ($idleHours -ge $script:TeamWatchRoundIdleHours -and ($Now - $lastStart).TotalHours -ge $script:TeamWatchRoundIdleHours) {
            [void]$actions.Add("start-round")
            $next.last_round_start = Get-TeamTimestamp -Now $Now
            & $find "test-team-idle" ("test ekibi {0:0.0} saattir is yapmiyordu" -f [Math]::Min($idleHours, 999))
        }
        else { [void]$lines.Add(("test turu yok; son tur {0:0.0} saat once bitti" -f [Math]::Min($idleHours, 999))) }
    }

    return [pscustomobject]@{ Findings = @($findings.ToArray()); Actions = @($actions.ToArray()); Lines = @($lines.ToArray()); State = $next }
}

function Select-TeamWatchFresh {
    <# The findings not seen in the last three hours; each one returned is noted as seen now. #>
    param([object[]]$Findings = @(), [Parameter(Mandatory = $true)]$State, [Parameter(Mandatory = $true)][datetime]$Now)
    $fresh = New-Object System.Collections.ArrayList
    foreach ($f in @($Findings | Where-Object { $null -ne $_ })) {
        $key = [string]$f.key
        $last = if ($State.seen.ContainsKey($key)) { ConvertFrom-TeamTimestamp -Text ([string]$State.seen[$key]) } else { $null }
        if ($null -ne $last -and ($Now - $last).TotalHours -lt $script:TeamWatchMemoryHours) { continue }
        [void]$fresh.Add($f)
        $State.seen[$key] = Get-TeamTimestamp -Now $Now
    }
    return @($fresh.ToArray())
}

function ConvertTo-TeamWatchCards {
    <#
    .SYNOPSIS
        The Danisman's drafts as queue tasks: at most three, approved, each one only if the
        queue with it added still passes Test-TeamQueue. Returns { Cards, Skipped (sentences) }.
    #>
    param([object[]]$Drafts = @(), [Parameter(Mandatory = $true)]$Queue, [Parameter(Mandatory = $true)][datetime]$Now)
    $cards = New-Object System.Collections.ArrayList
    $skipped = New-Object System.Collections.ArrayList
    $tasks = New-Object System.Collections.ArrayList
    foreach ($t in @(Get-TeamTasks -Queue $Queue)) { [void]$tasks.Add($t) }
    $at = $Now
    foreach ($d in @($Drafts | Where-Object { $null -ne $_ } | Select-Object -First $script:TeamWatchMaxCards)) {
        $id = ([string](Get-TeamProperty -InputObject $d -Name "id" -Default "")).ToLowerInvariant() -replace '[^a-z0-9-]', '-'
        $ids = @($tasks | ForEach-Object { [string]$_.id })
        if (-not $id) { [void]$skipped.Add("adsiz taslak atlandi"); continue }
        if ($ids -contains $id) { [void]$skipped.Add("$id zaten kuyrukta"); continue }
        $at = $at.AddSeconds(1)
        $ts = Get-TeamTimestamp -Now $at
        $card = [pscustomobject]@{
            id = $id; title = [string](Get-TeamProperty -InputObject $d -Name "title" -Default ""); roadmap_row = [string](Get-TeamProperty -InputObject $d -Name "roadmap_row" -Default "")
            state = "approved"; area = @(Get-TeamProperty -InputObject $d -Name "area" -Default @() | ForEach-Object { [string]$_ })
            depends_on = @(Get-TeamProperty -InputObject $d -Name "depends_on" -Default @() | Where-Object { $ids -contains [string]$_ } | ForEach-Object { [string]$_ })
            branch = ""; worktree = ""; assignee = ""; reports = @(); budget = [pscustomobject]@{ max_usd = 0 }
            created_at = $ts; updated_at = $ts
            goal = [string](Get-TeamProperty -InputObject $d -Name "goal" -Default ""); acceptance = [string](Get-TeamProperty -InputObject $d -Name "acceptance" -Default "")
            evidence_expected = [string](Get-TeamProperty -InputObject $d -Name "evidence_expected" -Default ""); needs_integration = $false; reason = ""
        }
        $trial = [pscustomobject]@{ tasks = @(@($tasks.ToArray()) + $card) }
        foreach ($p in $Queue.PSObject.Properties) { if ($p.Name -ne "tasks") { Set-TeamProperty -InputObject $trial -Name $p.Name -Value $p.Value } }
        $problems = @(Test-TeamQueue -Queue $trial)
        if (@($problems).Count -gt 0) { [void]$skipped.Add("$id kuyruga uymadi: " + ($problems -join "; ")); continue }
        [void]$tasks.Add($card)
        [void]$cards.Add($card)
    }
    return [pscustomobject]@{ Cards = @($cards.ToArray()); Skipped = @($skipped.ToArray()) }
}
