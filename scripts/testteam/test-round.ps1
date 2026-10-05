<#
.SYNOPSIS
    One round of the TEST team (team/plans/test-team-adr.md): the Test Proje Yöneticisi's plan
    is dealt to tester-1..4, their results come back, every failure is forwarded to the
    software Proje Yöneticisi's queue, and the breaking-point report goes to the Danışman.

.DESCRIPTION
    The owner's design of 2026-10-03, binding: the test team is SEPARATE from the software
    team - its own seats (test-lead, tester-1..4), its own cap ('test_parallel' in
    team/cycle-settings.json), its own cards (<OutRoot>/<round>/cards.json, states planned /
    running / passed / failed / broke). It never takes a software seat: a round runs beside a
    cycle and the cycle's max_parallel does not move.

      1. The plan: -PlanPath, or ONE fresh `claude -p` run of the test lead
         (.claude/agents/test-lead.md) that writes <OutRoot>/<round>/plan.json - jobs, one
         scenario family each, from the roadmap's HAVE rows, the owner's 'Dene' list and the
         recent releases.
      2. The cap: test_parallel, lowered to none under the memory floor and to one while the
         gate holds a heavy test slot (Get-TeamTestCap); the reasons are printed and posted.
      3. The jobs are dealt to the testers in turn (New-TestTeamCards) and run, at most the cap
         at once: each is ONE fresh `claude -p` run of .claude/agents/tester.md with its job
         card; it writes its result file and reports to the test lead (this script) only.
      4. The failures are deduplicated and each becomes a normal card of the software queue
         (state 'proposed', steps/expected/actual/scenario/screenshot/staging sha); a card whose
         id is already in the queue is not opened again.
      5. The 'kopma noktası' report: <OutRoot>/<round>/kopma-noktasi.md, and one board note
         addressed to the Danışman's seat ('danisman') (never to the owner).

    -Retest: for every failed card of -Round whose forwarded task is released, done or
    awaiting_real_evidence (NOT merged: an integration branch is not staging) AND whose staging
    now answers a sha other than the one the failure was found on (found_sha), the scenario is
    run again on staging (run-scenario.ps1, no model): passed closes the card, failed reopens it
    (reopened +1). A fix not yet released, or a staging not yet redeployed, is left alone.

    The cap is measured again before every tester start (the settings file, the free memory,
    the gate's test slot): a gate that takes the heavy slot mid-round, or memory that falls under
    the floor, holds the next job; the reason is said once. Cards that could not start stay
    planned.

    Run data - cards.json, plan.json, results, logs, staging screenshots, the report - lives
    under -OutRoot (default: <run_temp_root of team/cycle-settings.json, else TEMP>\testteam),
    never in the checkout.

    Staging only: run-scenario.ps1 refuses any other host, and the tester's role file says so.
#>
[CmdletBinding()]
param(
    [string]$Round = "",
    [string]$TeamRoot = "",
    [string]$OutRoot = "",
    [string]$PlanPath = "",
    [string]$ClaudePath = "claude",
    [string[]]$ClaudePrefixArguments = @(),
    # For the tests: the free memory in GB and whether the gate holds a heavy slot (1/0); -1 is "measure".
    [int]$AssumeFreeGb = -1,
    [int]$AssumeGateRunning = -1,
    [string]$SlotStore = "",
    [int]$RunMinutes = 60,
    [switch]$NoBoard,
    # For the tests: a stand-in for scripts\team\board.ps1.
    [string]$BoardScript = "",
    [switch]$Retest,
    [string]$BaseUrl = "http://127.0.0.1:28001",
    [int]$AllowTestPort = 0,
    [switch]$NoAuth,
    # The Cloud Core's queue, as in the cycle: the failures go there as create-only writes (the
    # feeder's Save-TeamFeedCreates). -QueueToken is the PATH of the token file.
    [string]$QueueUrl = "",
    [string]$QueueToken = ""
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamTestSlots.ps1")
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamFeed.ps1")
. (Join-Path $PSScriptRoot "TestTeam.ps1")
$apiStore = $null
if ($QueueUrl) {
    if (-not $QueueToken) { throw "-QueueUrl needs -QueueToken: the path of a file holding the token" }
    $apiStore = New-TeamApiStore -Url $QueueUrl -TokenFile $QueueToken
}

# Through -File a list arrives as one "a,b,c" string.
$ClaudePrefixArguments = @($ClaudePrefixArguments | ForEach-Object { [string]$_ -split ',' } | Where-Object { $_ })
if (-not $TeamRoot) { $TeamRoot = Join-Path $repoRoot "team" }
if (-not $Round) { $Round = "t" + [datetime]::UtcNow.ToString("yyyyMMddHHmm") }
if ($Round -notmatch '^[a-z0-9][a-z0-9-]{0,40}$') { throw "-Round: a-z, 0-9 ve '-' (en çok 41)" }
$settingsPath = Join-Path $TeamRoot "cycle-settings.json"
if (-not $OutRoot) {
    $tempRoot = Read-TeamRunTempRoot -Path $settingsPath
    if (-not $tempRoot) { $tempRoot = $env:TEMP }
    $OutRoot = Join-Path $tempRoot "testteam"
}
if (-not (Test-TestTeamPathOutside -Path $OutRoot -Root $repoRoot)) {
    Write-Host "TUR REDDEDİLDİ: çıktı klasörü depo ağacının içinde ($OutRoot); run_temp_root altına yazılır"
    exit 2
}
$roundDir = Join-Path $OutRoot $Round
[void](New-Item -ItemType Directory -Force -Path $roundDir)
$cardsPath = Join-Path $roundDir "cards.json"
$queuePath = Join-Path $TeamRoot "queue.json"
$runScenario = Join-Path $PSScriptRoot "run-scenario.ps1"
$powershell = Join-Path $PSHOME "powershell.exe"

function Write-Json {
    param([string]$Path, $Document)
    [System.IO.File]::WriteAllText($Path, (ConvertTo-Json -InputObject $Document -Depth 10), (New-Object System.Text.UTF8Encoding($false)))
}

function Send-Note {
    # The board never stops the round (board.ps1 says UYARI and exits 0 when it cannot post).
    param([string]$Seat, [string]$Text, [string]$To = "")
    if ($NoBoard) { return }
    $board = if ($BoardScript) { $BoardScript } else { Join-Path $repoRoot "scripts\team\board.ps1" }
    $arguments = @("-NoProfile", "-File", $board, "post", "-Seat", $Seat, "-Task", "test-team", "-Kind", "bilgi", "-Text", $Text)
    if ($To) { $arguments += @("-To", $To) }
    try { & $powershell @arguments 2>&1 | ForEach-Object { Write-Host "  pano: $_" } } catch { Write-Host "  pano: UYARI: $($_.Exception.Message)" }
}

function Get-RoleModel {
    # The model policy (team/models.json): the test lead runs on the lead's model, a tester on the worker's.
    param([string]$Role)
    $path = Join-Path $TeamRoot "models.json"
    $defaults = Get-TeamModelDefaults
    $key = if ($Role -eq "test-lead") { "lead" } else { "worker" }
    $model = [string]$defaults.roles.$key
    if (Test-Path -LiteralPath $path) {
        try { $doc = Read-TeamJson -Path $path; $named = [string](Get-TeamProperty -InputObject $doc.roles -Name $key -Default ""); if (Test-TeamModelId -Model $named) { $model = $named } } catch { }
    }
    return $model
}

function Start-RoleProcess {
    param([string]$Role, [string]$Prompt, [string]$Seat)
    # .claude/agents/ is the installed copy; scripts/testteam/roles/ is the source it is copied from.
    $roleFile = Join-Path $repoRoot ".claude\agents\$Role.md"
    if (-not (Test-Path -LiteralPath $roleFile)) { $roleFile = Join-Path $PSScriptRoot "roles\$Role.md" }
    $arguments = Get-TeamRunArguments -RoleFile $roleFile -Model (Get-RoleModel -Role $Role) -PrefixArguments $ClaudePrefixArguments
    $environment = @{ PAGENTOS_TEAM_SEAT = $Seat; PAGENTOS_TEAM_TASK = "test-team" }
    return (Start-TeamRun -FilePath $ClaudePath -Arguments $arguments -Prompt $Prompt -WorkingDirectory $repoRoot -Environment $environment)
}

function Read-Queue {
    if ($null -ne $apiStore) { return (Get-TeamQueueApi -Store $apiStore) }
    if (-not (Test-Path -LiteralPath $queuePath)) { return [pscustomobject]@{ version = 1; tasks = @() } }
    return (Read-TeamJson -Path $queuePath)
}

# ------------------------------------------------------------------------------ the re-test

if ($Retest) {
    if (-not (Test-Path -LiteralPath $cardsPath)) { Write-Host "yeniden test: $cardsPath yok"; exit 2 }
    $document = Read-TeamJson -Path $cardsPath
    $queue = Read-Queue
    $byId = @{}
    foreach ($task in (Get-TeamTasks -Queue $queue)) { $byId[[string]$task.id] = $task }
    # The sha staging serves NOW: a fix is judged on the staging that carries it, never on the old one.
    $stagingNow = ""
    if (Test-TestTeamStagingUrl -Url $BaseUrl -AllowTestPort $AllowTestPort) {
        try {
            $health = Invoke-RestMethod -UseBasicParsing -Uri ($BaseUrl.TrimEnd("/") + "/v1/system/health") -TimeoutSec 15
            $release = Get-TeamProperty -InputObject $health -Name "release"
            if ($null -ne $release) { $stagingNow = [string](Get-TeamProperty -InputObject $release -Name "version" -Default "") }
        }
        catch { Write-Host "  staging sürümü okunamadı: $($_.Exception.Message -replace '\s+', ' ')" }
    }
    Write-Host ("staging şimdi: {0}" -f $(if ($stagingNow) { $stagingNow } else { "okunamadı" }))
    foreach ($card in @($document.cards)) {
        if (@("failed", "broke") -notcontains [string]$card.state) { continue }
        $forwarded = [string](Get-TeamProperty -InputObject $card -Name "forwarded_task" -Default "")
        if (-not $forwarded -or -not $byId.ContainsKey($forwarded)) { continue }
        $foundSha = [string](Get-TeamProperty -InputObject $card -Name "found_sha" -Default "")
        $decision = Get-TestTeamRetestDecision -TaskState ([string]$byId[$forwarded].state) -FoundSha $foundSha -StagingSha $stagingNow
        if (-not $decision.Due) {
            Write-Host "  $($card.id): yeniden test yok ($forwarded) - $($decision.Why); beklemede"
            continue
        }
        Set-TeamProperty -InputObject $card -Name "retested_sha" -Value $stagingNow
        Set-TestTeamCardState -Card $card -To "running"
        $arguments = @("-NoProfile", "-File", $runScenario, "-Scenario", [string]$card.scenario, "-Card", "$($card.id)-retest", "-OutDir", $roundDir, "-BaseUrl", $BaseUrl)
        if ($AllowTestPort -gt 0) { $arguments += @("-AllowTestPort", [string]$AllowTestPort) }
        if ($NoAuth) { $arguments += "-NoAuth" }
        & $powershell @arguments 2>&1 | ForEach-Object { Write-Host "    $_" }
        $code = $LASTEXITCODE
        if ($code -eq 0 -or $code -eq 3) {
            Set-TestTeamCardState -Card $card -To "passed"
            Write-Host "  $($card.id): yeniden test GEÇTİ - kapandı ($forwarded)"
        }
        else {
            Set-TestTeamCardState -Card $card -To "failed"
            Set-TeamProperty -InputObject $card -Name "reopened" -Value (1 + [int](Get-TeamProperty -InputObject $card -Name "reopened" -Default 0))
            Write-Host "  $($card.id): yeniden test KALDI (çıkış $code) - yeniden açıldı ($forwarded)"
            Send-Note -Seat "test-lead" -Text "Test PY: $($card.id) yeniden açıldı - $forwarded düzeltmesinden sonra senaryo staging'de hâlâ kalıyor."
        }
    }
    Write-Json -Path $cardsPath -Document $document
    exit 0
}

# ------------------------------------------------------------------------------ the plan

if (-not $PlanPath) {
    $PlanPath = Join-Path $roundDir "plan.json"
    if (-not (Test-Path -LiteralPath $PlanPath)) {
        $prompt = @(
            "# Test round $Round - the test plan"
            ""
            "- plan_file: $PlanPath"
            "- scenarios: scripts/testteam/scenarios/"
            "- staging_api: http://127.0.0.1:28001"
            ""
            "Write the plan of this round as your role file says, and nothing else."
        ) -join "`n"
        $run = Start-RoleProcess -Role "test-lead" -Prompt $prompt -Seat "test-lead"
        $done = Wait-TeamRun -Run $run -Deadline ([datetime]::UtcNow.AddMinutes($RunMinutes))
        [System.IO.File]::WriteAllText((Join-Path $roundDir "test-lead-plan.log"), [string]$done.StdOut, (New-Object System.Text.UTF8Encoding($false)))
        if (-not (Test-Path -LiteralPath $PlanPath)) { Write-Host "Test PY plan yazmadı ($PlanPath); tur başlamadı"; exit 1 }
    }
}
$plan = Read-TeamJson -Path $PlanPath
$cards = @(New-TestTeamCards -Round $Round -Jobs @($plan.jobs))
$document = [pscustomobject]@{ round = $Round; plan = $PlanPath; cards = $cards }
Write-Json -Path $cardsPath -Document $document

# ------------------------------------------------------------------------------ the cap

$script:saidReasons = ""
function Get-RoundCap {
    # Measured NOW: the settings file, the free memory and the gate's test slot. Called before
    # every tester start, so a gate that takes the heavy slot mid-round holds the next job.
    $settings = Read-TeamCycleSettings -Path $settingsPath -Workers 4 -Inspectors 3 -Integrators 1
    $freeBytes = if ($AssumeFreeGb -ge 0) { [int64]$AssumeFreeGb * 1GB } else { [int64]((Get-CimInstance -ClassName Win32_OperatingSystem).FreePhysicalMemory) * 1KB }
    $gate = $false
    if ($AssumeGateRunning -ge 0) { $gate = ($AssumeGateRunning -eq 1) }
    else {
        $store = if ($SlotStore) { $SlotStore } else { Get-TestSlotDefaultStore }
        $gate = Test-TeamGateHoldsHeavy -Entries @(Get-TestSlotEntries -Store $store)
    }
    $cap = Get-TeamTestCap -Configured $settings.Testers -FreeBytes $freeBytes -FloorGb $settings.TestFloorGb -GateRunning $gate
    # A reason is said when it changes, not at every measurement.
    $said = (@($settings.Problems) + @($cap.Reasons)) -join " | "
    if ($said -ne $script:saidReasons) {
        foreach ($problem in @($settings.Problems)) { Write-Host "  ayar: $problem" }
        Write-Host ("test ekibi sınırı: {0} (ayar {1})" -f $cap.Cap, $cap.Configured)
        foreach ($reason in @($cap.Reasons)) {
            Write-Host "  $reason"
            Send-Note -Seat "test-lead" -Text "Test PY: $reason"
        }
        $script:saidReasons = $said
    }
    return $cap
}

$cap = Get-RoundCap
if ($script:saidReasons -eq "") { Write-Host ("test ekibi sınırı: {0} (ayar {1})" -f $cap.Cap, $cap.Configured) }
if ($cap.Cap -eq 0) {
    Write-Host "bu turda test çalışanı başlatılmadı; kartlar planned kaldı: $cardsPath"
    exit 0
}

# ------------------------------------------------------------------------------ the testers

$pending = New-Object System.Collections.Queue
foreach ($card in $cards) { $pending.Enqueue($card) }
$inFlight = New-Object System.Collections.ArrayList
$results = New-Object System.Collections.ArrayList
$measuredAt = [datetime]::UtcNow
$finishedSince = $false
while ($pending.Count -gt 0 -or $inFlight.Count -gt 0) {
    if ($pending.Count -gt 0 -and ($finishedSince -or ([datetime]::UtcNow - $measuredAt).TotalSeconds -ge 5)) {
        $cap = Get-RoundCap
        $measuredAt = [datetime]::UtcNow
        $finishedSince = $false
    }
    if ($pending.Count -gt 0 -and $cap.Cap -eq 0 -and $inFlight.Count -eq 0) {
        Write-Host "  sınır 0: kalan $($pending.Count) kart planned kaldı ($(@($pending.ToArray() | ForEach-Object { $_.id }) -join ', '))"
        $pending.Clear()
        break
    }
    $busy = @($inFlight | ForEach-Object { $_.Card.tester })
    $waiting = $pending.Count
    for ($i = 0; $i -lt $waiting -and $inFlight.Count -lt $cap.Cap; $i++) {
        $card = $pending.Dequeue()
        if ($busy -contains $card.tester) { $pending.Enqueue($card); continue }
        $resultFile = Join-Path $roundDir "$($card.id).result.json"
        Set-TestTeamCardState -Card $card -To "running"
        Write-Json -Path $cardsPath -Document $document
        $run = Start-RoleProcess -Role "tester" -Prompt (New-TestTeamJobCard -Card $card -ResultFile $resultFile -Round $Round) -Seat $card.tester
        [void]$inFlight.Add([pscustomobject]@{ Card = $card; Run = $run; ResultFile = $resultFile; Deadline = [datetime]::UtcNow.AddMinutes($RunMinutes) })
        $busy += $card.tester
        Write-Host "  $($card.tester) <- $($card.id) ($($card.family))"
        # The Ofis' Test odası (officeTestRoom.tsx) reads "iş: <job>" and "sonuç: <state> - <job> - ...".
        Send-Note -Seat $card.tester -Text (Format-TestTeamSeatNote -Card $card)
    }
    $over = @($inFlight | Where-Object { Test-TeamRunOver -Run $_.Run -Deadline $_.Deadline })
    if (@($over).Count -eq 0) { Start-Sleep -Milliseconds 300; continue }
    foreach ($entry in $over) {
        $inFlight.Remove($entry)
        $finishedSince = $true
        $done = Wait-TeamRun -Run $entry.Run -Deadline $entry.Deadline
        [System.IO.File]::WriteAllText((Join-Path $roundDir "$($entry.Card.id).log"), [string]$done.StdOut, (New-Object System.Text.UTF8Encoding($false)))
        $state = "failed"
        $result = $null
        if (Test-Path -LiteralPath $entry.ResultFile) {
            try { $result = Read-TeamJson -Path $entry.ResultFile; $state = [string]$result.state } catch { $result = $null }
        }
        if (@("passed", "failed", "broke") -notcontains $state) { $state = "failed" }
        if ($null -eq $result) {
            # A tester that wrote no result is a failure of the round, not of staging: it is said, not forwarded.
            Write-Host "  $($entry.Card.id): $($entry.Card.tester) sonuç yazmadı (çıkış $($done.ExitCode))"
        }
        Set-TestTeamCardState -Card $entry.Card -To $state
        if ($null -ne $result) { [void]$results.Add($result) }
        Write-Json -Path $cardsPath -Document $document
        Write-Host "  $($entry.Card.tester) -> $($entry.Card.id): $state"
        Send-Note -Seat $entry.Card.tester -Text (Format-TestTeamSeatNote -Card $entry.Card -Result $result)
    }
}

# ------------------------------------------------------------------------------ forward

$failures = @()
foreach ($result in $results) { $failures += @(Get-TestTeamFailures -Result $result) }
$failures = @(Merge-TestTeamFailures -Failures $failures)
$stagingSha = ""
foreach ($result in $results) { $sha = [string](Get-TeamProperty -InputObject $result -Name "staging_sha" -Default ""); if ($sha) { $stagingSha = $sha } }
$queue = Read-Queue
$known = @{}
foreach ($task in (Get-TeamTasks -Queue $queue)) { $known[[string]$task.id] = $true }
$added = New-Object System.Collections.ArrayList
foreach ($failure in $failures) {
    $task = ConvertTo-TestTeamFailureTask -Failure $failure -StagingSha $stagingSha -Round $Round
    foreach ($card in $cards) {
        if ($card.id -ne $failure.card) { continue }
        $card.forwarded_task = $task.id
        # The retest judges the fix only on a staging that no longer serves this sha.
        $card.found_sha = [string](Get-TeamProperty -InputObject $failure -Name "staging_sha" -Default "")
    }
    if ($known.ContainsKey($task.id)) { Write-Host "  $($task.id) kuyrukta zaten var; yeniden açılmadı"; continue }
    $known[$task.id] = $true
    [void]$added.Add($task)
}
if ($added.Count -gt 0) {
    $queue.tasks = @(@(Get-TeamTasks -Queue $queue) + @($added.ToArray()))
    # The store's own tasks are the store's to judge; the new cards are this round's.
    $judged = if ($null -ne $apiStore) { [pscustomobject]@{ version = 1; tasks = @($added.ToArray()) } } else { $queue }
    $problems = @(Test-TeamQueue -Queue $judged)
    if (@($problems).Count -gt 0) { throw "kuyruk protokolü bozulurdu: $($problems -join '; ')" }
    if ($null -ne $apiStore) {
        # Create-only: a task the store already has is never overwritten (somebody opened it first).
        $saved = Save-TeamFeedCreates -Store $apiStore -Tasks @($added.ToArray())
        foreach ($drop in @($saved.Dropped)) { Write-Host "  $($drop.Id) yazılmadı: $($drop.Why)" }
        if ($saved.Failed) { Write-Host "  depo yazmayı kesti: $($saved.Failed)" }
        $added = New-Object System.Collections.ArrayList
        foreach ($task in $queue.tasks) { if (@($saved.Written) -contains [string]$task.id) { [void]$added.Add($task) } }
    }
    else { Write-TeamJson -Path $queuePath -Document $queue }
    foreach ($task in $added) {
        Write-Host "  yazılım Proje Yöneticisi'ne iletildi: $($task.id)"
        Send-Note -Seat "test-lead" -To "lead" -Text ("Test PY: staging'de hata, kart {0} kuyrukta (proposed). {1}" -f $task.id, $task.title)
    }
}
Write-Json -Path $cardsPath -Document $document

# ------------------------------------------------------------------------------ the breaking point

$report = Format-TestTeamBreakingReport -Round $Round -Results @($results.ToArray()) -StagingSha $stagingSha
[System.IO.File]::WriteAllText((Join-Path $roundDir "kopma-noktasi.md"), $report.Markdown, (New-Object System.Text.UTF8Encoding($false)))
Send-Note -Seat "test-lead" -To $report.To -Text $report.Note
$counts = @($cards | Group-Object state | ForEach-Object { "$($_.Name) $($_.Count)" }) -join ", "
Write-Host "tur $Round bitti: $counts; iletilen $($added.Count); kopma raporu $(Join-Path $roundDir 'kopma-noktasi.md')"
exit 0
