<#
.SYNOPSIS
    One cycle of the agent team (docs/TEAM_PROTOCOL.md section 3).

.DESCRIPTION
    Reads `team/queue.json`, takes the lock, and for every task that is not at one of the
    owner's three gates starts the run its state calls for: a FRESH `claude -p` process with
    the role file as its system prompt and the task card as its prompt. The run's raw output
    goes to `team/reports/<cycle-id>/`; at most forty lines of its report go into the queue.

    After the researcher step, a proposal that serves a roadmap row and has no area yet is
    split into tasks: one fresh `lead` run (Read and Write only) writes
    `team/plans/<cycle>-split-<id>.json`, and THIS script validates it (Test-TeamSplit) and
    queues the tasks as `approved`, or refuses the whole split and says why in the report.

    A task the cycle STOPPED (two returns, 'alan dışı dosya', a conflict on the integration
    branch, two failed runs) is the Proje Yöneticisi's, not the owner's (pm-duty-stopped, the
    owner of 2026-10-03): when the lead seat is free, one duty run of the same safe shape as a
    split (role file lead.md, Read/Grep/Glob/Write) is handed the stopped tasks and writes
    `team/plans/<cycle>-duty-<n>.json`; THIS script validates it (Test-TeamDuty) and applies it
    whole - return, grant_and_return (the area widened by the granted paths), escalate (the task
    stays stopped, "Danışman'a iletildi: ") - or refuses it whole and says why in the report. A
    task is handed once per stop; a return beside a task that holds the same files is not
    forced. A split and a duty run never run at once: there is one lead seat.

    It never waits for a human. It ends when nothing in the queue can run - every task is at
    a gate, done or stopped - or when a cap is reached, and it writes
    `team/reports/<cycle-id>.md` in Turkish either way.

    The runs are a POOL, not batches (owner, 2026-10-01: no agent idles while there is work;
    ADR-0214 addendum 8). The runs in flight are polled; one that ends is completed at once -
    report, queue state, the merge of an approved inspection - and the seats that are free are
    filled from the queue, in its order. Seats are per role: -MaxParallel workers, beside them
    -MaxInspectors inspections and -MaxIntegrators integrators, and the researcher and one
    lead split beside those; a role never takes another role's seat. At every refill the
    store is read again (API mode) and `team/cycle-settings.json`, when it is there, names
    the seats ({ "max_parallel", "max_inspectors", "max_integrators" }) - so a card stored, or
    a setting changed, while runs are in flight takes effect when a seat is free. A task
    whose area overlaps the area of a task in flight waits until that run ends.

    What it does NOT do, on purpose:
      * it does not run the quality gate and does not merge to main. Approved branches are
        merged into `integrate/<cycle-id>`; the gate on that branch and the merge to main
        are the lead's;
      * it does not release, register a scheduled task, push, or touch main's checkout
        beyond the files under `team/`;
      * it does not approve anything. A task at a gate moves when the OWNER changes its
        state.

    Idempotent: a branch, a worktree or a merge that exists is used as it is, and a task
    a killed run left `in_progress` is taken up again.

    The model policy (ADR-0214 addendum 7): each role runs on the model the team store's
    setting names for it (`GET /v1/team/queue/models`, else `team/models.json`, else the
    defaults). A run that comes back with the usage limit is started again at once on the
    next model down the chain (`"fallback": true`), and the report says 'model düşürüldü';
    a limited model starts no run until its reset (`team/limits.json` keeps that across
    cycles). The inspector is never started on a model weaker than the STRONGEST one the
    task's worker runs really used (not the last run's: ADR-0214 addendum 10): when every
    model at least that strong is limited, the inspection waits.

.PARAMETER Model
    The model of a role the setting does not name (one of the three ids). The setting wins.

.PARAMETER MaxUsd
    The cycle's money cap; 0 (the default since the owner's decision of 2026-09-30, ADR-0214
    addendum 3) is NO cap: the subscription has none, and the USD the tool reports is kept
    in the report as an estimate only.

.PARAMETER RunMaxUsd
    One run's money cap; 0 is no cap (then a task's own budget.max_usd is an estimate too).

.PARAMETER RunMinutes
    One run's time cap in minutes; 0 is no cap. A killed run is harmless: the cycle is
    idempotent and the next one takes the task up again.

.PARAMETER CycleMinutes
    The cycle's time cap in minutes; 0 is no cap.

.PARAMETER MaxParallel
    The WORKER seats. Inspections, integrators, the researcher and a lead split have seats of
    their own (-MaxInspectors, -MaxIntegrators, one, one).

.PARAMETER MaxHours
    After this many hours the process starts nothing new and ends when its runs do. 0 (the
    default since the owner's rule of 2026-10-03, "Döngüyü kaldırabiliriz") is never: the
    cycle boundary is gone. New code is taken over by a HANDOVER instead (team/handover.flag,
    or - with -Continuous - the scripts' hash changing): the loop stops dispatching, writes its
    live runs to team/logs/loop-handover.json, exits WITHOUT killing them and starts its
    successor, which adopts them. The parameter stays for tests.

.PARAMETER Continuous
    The loop (continuous-team-loop): it does not end when nothing can run - it waits for the
    next refill. It ends on team/stop.flag (when its runs have ended) or hands over. The
    watchdog (tick.ps1 -Watchdog) starts it with this switch.

.PARAMETER RefillSeconds
    The seats are filled whenever a run ends, and at least this often while nothing ends - a
    card that reaches the store beside one long run is started while it runs.

.PARAMETER WaitForUsageLimit
    The one stop the team has is the subscription's usage limit (Max). When a run hits it
    the task goes back to where it was, and with this switch (the default) the cycle WAITS
    until the limit lifts and carries on from there; without it, or when the tool did not
    say when, the cycle stops and says so in the report - the next cycle with the same
    -CycleId continues where it left off.

.PARAMETER NoDuty
    No duty run of the Proje Yöneticisi for stopped tasks in this cycle (it is on by default);
    a stopped task then waits for a person, as it did before pm-duty-stopped.

.PARAMETER ClaudePrefixArguments
    Arguments placed before the ones this script builds. The tests use it to put a fake
    in place of the model: -ClaudePath powershell.exe -ClaudePrefixArguments -File,fake.ps1

.EXAMPLE
    .\scripts\team\cycle.ps1 -CycleId pilot-01
    (the researcher runs beside the tasks; -NoResearch for a cycle without it)
#>
[CmdletBinding()]
param(
    [string]$CycleId = "",
    [string]$TeamRoot = "",
    [int]$MaxParallel = 2,
    # Seats are per role (the owner at the Ofis page, 2026-10-01 22:15: three inspections held
    # the cycle's three slots and every worker seat was empty, with eight tasks assigned).
    [int]$MaxInspectors = 2,
    [int]$MaxIntegrators = 1,
    [double]$MaxUsd = 0,
    [double]$RunMaxUsd = 0,
    [double]$RunMinutes = 0,
    [int]$CycleMinutes = 0,
    [double]$MaxHours = 0,
    [switch]$Continuous,
    [int]$RefillSeconds = 120,
    # How often the runs in flight are looked at. Nothing blocks on one run.
    [int]$PollMilliseconds = 250,
    # Eight, not four: a task with an integrator and ONE honest return is integrator + worker +
    # inspector + worker = four runs, and was stopped before its second inspection
    # (model-policy-cycle, 2026-10-01). Two RETURNs still stop a task; this only bounds a loop.
    [int]$MaxRunsPerTask = 8,
    [bool]$WaitForUsageLimit = $true,
    [string]$ClaudePath = (Join-Path $env:USERPROFILE ".local\bin\claude.exe"),
    [string[]]$ClaudePrefixArguments = @(),
    [string]$Model = "",
    [string]$Machine = $env:COMPUTERNAME,
    [string]$Base = "main",
    # The researcher runs in EVERY cycle, queue full or not (owner, 2026-10-01; ADR-0214
    # addendum 5), in its own seat beside the tasks' runs. -NoResearch turns it off; -Research
    # is accepted and changes nothing, so a task registered with it keeps working.
    [switch]$Research,
    [switch]$NoResearch,
    # The continuous cycle (owner, 2026-10-01: "sürekli, kontrollü"): the scheduled task starts a
    # cycle every half hour, so what broke or finished at noon reaches the others at noon. With
    # -DailyId every one of a day's cycles shares ONE id ("dYYYYMMDD") and so ONE integration
    # branch; with -ResearchEveryHours N the researcher runs only when its last run ended more
    # than N hours ago (0 = in every cycle) - a web scan forty-eight times a day is not control.
    [switch]$DailyId,
    [double]$ResearchEveryHours = 0,
    # What the lead asks the researcher to study, one line per subject. It is placed in the
    # researcher's prompt under a heading of its own; the role file stays the role.
    [string[]]$ResearchBrief = @(),
    # The first step of a cycle by itself: the researcher writes its proposals, they are
    # queued for the owner, and NO task is run or moved - not even an approved one.
    [switch]$ResearchOnly,
    # The Proje Yöneticisi's duty for stopped tasks (pm-duty-stopped) is on in every cycle;
    # -NoDuty turns it off.
    [switch]$NoDuty,
    [switch]$DryRun,
    # The Cloud Core's queue (pilot-02): with -QueueUrl the queue, the lock and the report go
    # through /v1/team/queue there, so an approval can be given with this PC off and the other
    # PC sees the same queue. Without it, the files under -TeamRoot, as before.
    [string]$QueueUrl = "",
    # A PATH to a file the owner wrote holding the owner-session token. A token is never a
    # parameter on the command line: it would sit in the process list.
    [string]$QueueToken = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
# The lead-protected list a duty decision's grant is judged against (pm-duty-stopped). A copy of
# the scripts without it (a test's sandbox of other steps) runs as before: Test-TeamDuty then
# refuses every decision file - it fails closed, it does not guess.
$teamAreaLibrary = Join-Path $repoRoot "scripts\lib\TeamArea.ps1"
if (Test-Path -LiteralPath $teamAreaLibrary) { . $teamAreaLibrary }
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")

if (-not $TeamRoot) { $TeamRoot = Join-Path $repoRoot "team" }
# The clock of this process. Two test hooks, both inert unless set: PAGENTOS_CYCLE_CLOCK_OFFSET_HOURS
# makes the loop believe it started that many hours ago (a loop past four hours, in seconds), and
# PAGENTOS_TEAM_LOCAL_CLOCK_START ("yyyy-MM-ddTHH:mm:ss", local) is the local time at this moment,
# running on from there (local midnight, in seconds).
$processStart = [datetime]::UtcNow
$localClockStart = $null
if ([string]$env:PAGENTOS_TEAM_LOCAL_CLOCK_START -match '^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$') {
    $localClockStart = [datetime]::ParseExact([string]$env:PAGENTOS_TEAM_LOCAL_CLOCK_START, "yyyy-MM-ddTHH:mm:ss", [System.Globalization.CultureInfo]::InvariantCulture)
}
function Get-LoopLocalNow {
    if ($null -ne $script:localClockStart) { return $script:localClockStart.Add([datetime]::UtcNow - $script:processStart) }
    return (Get-Date)
}
# The day's id (continuous-team-loop): with -DailyId and no -CycleId the loop follows the local
# day - at midnight the report folder, the report and the integration branch of the new day
# begin, with no restart. A -CycleId given is kept for the whole run.
$followDay = ([bool]$DailyId -and -not $CycleId)
$dayId = if ($CycleId) { $CycleId } elseif ($DailyId) { "d" + (Get-LoopLocalNow).ToString("yyyyMMdd", [System.Globalization.CultureInfo]::InvariantCulture) } else { "c" + (Get-Date).ToString("yyyyMMdd-HHmm") }
if ($dayId -cnotmatch '^[a-z0-9][a-z0-9.-]{0,40}$') { throw "a cycle id is lower-case letters, digits, '.' and '-': '$dayId'" }
$lockCycleId = $dayId
if ($MaxParallel -lt 1) { throw "-MaxParallel is at least 1" }
if ($MaxInspectors -lt 1 -or $MaxIntegrators -lt 1) { throw "-MaxInspectors and -MaxIntegrators are at least 1" }
if ($MaxHours -lt 0 -or $RefillSeconds -lt 1 -or $PollMilliseconds -lt 10) { throw "-MaxHours is 0 or more, -RefillSeconds at least 1, -PollMilliseconds at least 10" }
# A parameter is never assigned over (provision.tests.ps1 holds every script to it).
$runResearch = (-not $NoResearch) -or [bool]$ResearchOnly
# The researcher's last finished run, on this machine: the throttle of -ResearchEveryHours.
$researchMarker = Join-Path $TeamRoot "research-last.txt"
if ($runResearch -and -not $ResearchOnly -and $ResearchEveryHours -gt 0 -and (Test-Path -LiteralPath $researchMarker)) {
    $lastResearch = ConvertFrom-TeamTimestamp -Text ([System.IO.File]::ReadAllText($researchMarker).Trim())
    if ($null -ne $lastResearch -and ([datetime]::UtcNow - $lastResearch).TotalHours -lt $ResearchEveryHours) { $runResearch = $false }
}

$modelsPath = Join-Path $TeamRoot "models.json"
$limitsPath = Join-Path $TeamRoot "limits.json"
$queuePath = Join-Path $TeamRoot "queue.json"
$lockPath = Join-Path $TeamRoot "lock.json"
$reportsRoot = Join-Path $TeamRoot "reports"
$cycleDir = Join-Path $reportsRoot $dayId
$agentsRoot = Join-Path $repoRoot ".claude\agents"

$useApi = [bool]$QueueUrl
if ($useApi -and -not $QueueToken) { throw "-QueueUrl needs -QueueToken: the path of a file holding the token" }
$apiStore = $null
if ($useApi) { $apiStore = New-TeamApiStore -Url $QueueUrl -TokenFile $QueueToken }

function Save-Queue {
    param($Document)
    if (-not $useApi) { Write-TeamJson -Path $queuePath -Document $Document; return }
    # A task somebody else wrote since the cycle read it (the owner in the Onay Merkezi, the
    # lead, the feeder) is theirs: the cycle's write of THAT task is dropped, the others are
    # written, and the next pass reads the store's version. It used to end the whole cycle.
    try { [void]@(Save-TeamQueueApi -Store $apiStore -Queue $Document -SkipStale) }
    finally {
        # In 'finally': a save that was refused one task and then FAILED on another threw before
        # it could say which it was refused - and the refused task's merge stayed on the
        # integration branch, neither taken back nor named. The store object keeps the refusals
        # as they happen.
        foreach ($id in @($apiStore.Refused.ToArray())) {
            # Theirs until the store is read again: nothing is started for it on the copy we have.
            $script:staleIds[[string]$id] = $true
            $note = "${id}: depoda başkası değiştirdi; döngünün yazdığı bırakıldı, depodaki hali geçerli"
            if (@($script:cycle.risks) -notcontains $note) { Add-CycleNote -List "risks" -Text $note }
        }
        $apiStore.Refused.Clear()
    }
}

function Restore-TaskCopy {
    <# The store refused what a run's result made of the task: the cycle's own copy goes back to
       what it was BEFORE that result, so the report (and anything else that reads the copy until
       the store can be read again) does not say "merged" of a task whose merge was taken back or
       is somebody else's now. The put-back copy is noted as written: it is not sent. #>
    param($Task, [string]$Before)
    if (-not $Before) { return }
    $was = ConvertFrom-Json -InputObject $Before
    foreach ($name in @($Task.PSObject.Properties | ForEach-Object { $_.Name })) {
        if ($null -eq $was.PSObject.Properties[$name]) { $Task.PSObject.Properties.Remove($name) }
    }
    foreach ($property in $was.PSObject.Properties) { Set-TeamProperty -InputObject $Task -Name $property.Name -Value $property.Value }
    if ($useApi) { Set-TeamTaskWritten -Store $apiStore -Task $Task }
}

function Test-TaskMovedInStore {
    <# Did somebody else write this task since the cycle read it? Asked when a run of the task
       ends, BEFORE its result is applied: a merge into the integration branch cannot be taken
       back by a write the store then refuses (a task the lead stopped while its inspector ran
       was merged, 2026-10-02). The whole queue is read and NOT judged: a card that breaks the
       protocol somewhere else must not hide this task's stop. A store that does not answer
       says nothing: the result is applied and its write fails or is refused as before. #>
    param($Task)
    if (-not $useApi) { return $false }
    $id = [string]$Task.id
    if ($script:staleIds.ContainsKey($id)) { return $true }
    $known = $apiStore.Baseline[$id]
    if ($null -eq $known) { return $false }
    try { $now = Get-TeamQueueApi -Store $apiStore } catch { return $false }
    $theirs = @(Get-TeamTasks -Queue $now | Where-Object { [string]$_.id -eq $id })
    if (@($theirs).Count -eq 0) { return $true }
    return ([string]$theirs[0].updated_at -ne [string]$known.Updated)
}

function Save-QueueNow {
    <# A write made while runs are in flight (after the starts; after each applied result). A
       store that is away at that moment must not end the cycle with runs still working - the
       tasks would stay in_progress, the lock would be released, and the next tick would start
       workers in the same worktrees. It is a line, and the next write made when nothing is
       in flight - the strict one - writes what is pending. $false = not written now. #>
    param([string]$What)
    try { Save-Queue -Document $script:queue; return $true }
    catch {
        $note = "${What}: sonuç depoya şimdi yazılamadı (" + ((([string]$_.Exception.Message) -replace '\s+', ' ').Trim()) + "); uçuşta koşu kalmayınca yeniden denenecek"
        if ($note.Length -gt 300) { $note = $note.Substring(0, 300) }
        if (@($script:cycle.risks) -notcontains $note) { Add-CycleNote -List "risks" -Text $note }
        return $false
    }
}

function Add-RefusedMergeNote {
    param($Task, [bool]$Undone)
    $where = [string](Get-TeamProperty -InputObject $Task -Name "integration_branch" -Default "")
    $end = if ($Undone) { "birleştirme GERİ ALINDI (dal önceki commit'inde)" } else { "o birleştirme dalda duruyor - lead geri alır ya da işi yeniden açar" }
    $note = "$($Task.id): entegrasyon dalına ($where) BİRLEŞTİRİLDİ, sonra depo yazmayı reddetti (başkası değiştirdi); $end"
    if (@($script:cycle.risks) -notcontains $note) { Add-CycleNote -List "risks" -Text $note }
}

function Sync-Queue {
    <# The store is the truth and a cycle lives for hours: the owner decides in the Onay Merkezi,
       the lead adds a card, the feeder cuts the roadmap - while this process runs. Read once at
       the start, none of it was seen until the NEXT cycle, and a cycle that has work does not
       end (2026-10-02: the owner's fourth-seat card, stored nine minutes after the cycle began,
       waited four hours beside idle seats). At every refill of the pool the cycle's own
       changes are written and the queue is read again. A store that does not answer, or a
       queue that breaks the protocol now, changes nothing: the refill runs on the copy the
       cycle has, and the report says so once. With files there is one writer, the lock's holder.

       With nothing in flight the write is the strict one (a store that does not take the
       cycle's writes ends the cycle); beside runs in flight it is a line in the report, and
       the queue is NOT read again over what could not be written. #>
    if (-not $useApi) { return }
    if (@($script:pool).Count -eq 0) { Save-Queue -Document $script:queue }
    elseif (-not (Save-QueueNow -What "bekleyen yazmalar")) { return }
    Resolve-UnwrittenMerges
    try {
        $fresh = Get-TeamQueueApi -Store $apiStore
        $why = @(Test-TeamQueue -Queue $fresh)
        if (@($why).Count -gt 0) { throw ("kuyruk protokolü bozuyor: " + ($why -join "; ")) }
    }
    catch {
        if (-not $script:syncNoted) {
            $script:syncNoted = $true
            Add-CycleNote -List "risks" -Text ("kuyruk yeniden okunamadı, döngü elindeki kopyayla sürdü: " + (([string]$_.Exception.Message) -replace '\s+', ' '))
        }
        return
    }
    # A run in flight is the cycle's: its task stays the copy the run was started from, with
    # the version that copy was read at. Its result is written when it ends; if the store's
    # copy changed meanwhile, that write is the stale one and is dropped (Test-TaskMovedInStore
    # asks first). A task the store no longer has stays in the cycle's copy until its run ends.
    $mine = [ordered]@{}
    $versions = @{}
    foreach ($run in $script:pool) {
        if ($null -eq $run.Task) { continue }
        $mine[[string]$run.Task.id] = $run.Task
        $versions[[string]$run.Task.id] = $apiStore.Baseline[[string]$run.Task.id]
    }
    Set-TeamQueueBaseline -Store $apiStore -Queue $fresh
    if (@($mine.Keys).Count -gt 0) {
        $tasks = New-Object System.Collections.ArrayList
        $seen = @{}
        foreach ($task in (Get-TeamTasks -Queue $fresh)) {
            $id = [string]$task.id
            if ($mine.Contains($id)) { [void]$tasks.Add($mine[$id]); $seen[$id] = $true } else { [void]$tasks.Add($task) }
        }
        foreach ($id in @($mine.Keys)) {
            if (-not $seen.ContainsKey($id)) { [void]$tasks.Add($mine[$id]) }
            if ($null -ne $versions[$id]) { $apiStore.Baseline[$id] = $versions[$id] } else { $apiStore.Baseline.Remove($id) }
        }
        Set-TeamProperty -InputObject $fresh -Name "tasks" -Value @($tasks.ToArray())
    }
    $script:queue = $fresh
    # Every other task is the store's version again: nothing but a run's task is "theirs" any more.
    foreach ($id in @($script:staleIds.Keys)) { if (-not $mine.Contains($id)) { $script:staleIds.Remove($id) } }
}

function Resolve-UnwrittenMerges {
    <# A merge whose write had to wait (the store was away when the inspection ended) and was
       refused when it could at last be tried: other merges may stand on it by now, so nothing
       is reset - it is named, for the lead, and the cycle's copy says again what it said
       before. One that was written since is no longer waited for. #>
    foreach ($late in @($script:unwrittenMerges.ToArray())) {
        $id = [string]$late.Task.id
        if ($script:staleIds.ContainsKey($id)) {
            if ([string]$late.Task.state -eq "merged") {
                Add-RefusedMergeNote -Task $late.Task -Undone $false
                Restore-TaskCopy -Task $late.Task -Before $late.Before
            }
            $script:unwrittenMerges.Remove($late)
        }
        elseif (-not $useApi -or ($null -ne $apiStore.Baseline[$id] -and $apiStore.Baseline[$id].Json -ceq (ConvertTo-Json -InputObject $late.Task -Depth 12 -Compress))) {
            $script:unwrittenMerges.Remove($late)
        }
    }
}

$queue = if ($useApi) { Read-TeamQueueApi -Store $apiStore } else { Read-TeamJson -Path $queuePath }
$problems = @(Test-TeamQueue -Queue $queue)
if (@($problems).Count -gt 0) {
    Write-Host "the queue breaks the protocol; nothing was run:"
    foreach ($problem in $problems) { Write-Host "  - $problem" }
    exit 2
}

$started = [datetime]::UtcNow
if ([string]$env:PAGENTOS_CYCLE_CLOCK_OFFSET_HOURS -match '^\d{1,3}$') { $started = $started.AddHours(-[int]$env:PAGENTOS_CYCLE_CLOCK_OFFSET_HOURS) }
$cycle = [pscustomobject]@{
    cycle_id   = $dayId
    machine    = $Machine
    started_at = (Get-TeamTimestamp -Now $started)
    ended_at   = ""
    max_usd    = $MaxUsd
    spent_usd  = 0.0
    conflicts  = 0
    returned   = 0
    runs       = @()
    stops      = @()
    risks      = @()
    gaps       = @()
}

# ------------------------------------------------------------------ the live status
# What the owner's 'Ofis' page reads (office-cycle-status): which runs are in flight NOW, the
# estimate so far and the usage limit. File mode: team/status.json; API mode: PUT
# /v1/team/queue/status. A status nobody refreshed for ten minutes reads as "no cycle", so the
# pool refreshes it every $statusTickSeconds seconds while it has runs in flight or waits for the
# limit. A write that fails is a line under the risks, once, and never a reason to stop the cycle.
$statusPath = Join-Path $TeamRoot "status.json"
$stopFlagPath = Join-Path $TeamRoot "stop.flag"
$statusTickSeconds = 120
# A test hook (the heartbeat is otherwise only visible after two minutes): a whole number of seconds.
if ([string]$env:PAGENTOS_CYCLE_STATUS_TICK_SECONDS -match '^[1-9]\d{0,3}$') { $statusTickSeconds = [int]$env:PAGENTOS_CYCLE_STATUS_TICK_SECONDS }
$liveRuns = New-Object System.Collections.ArrayList
$statusWrittenAt = [datetime]::MinValue
# The pool: the runs in flight (what Start-RoleRun returned). $unwrittenMerges are the merges
# whose write the store could not take when they were made (Resolve-UnwrittenMerges).
$pool = New-Object System.Collections.ArrayList
$unwrittenMerges = New-Object System.Collections.ArrayList
$usageLimit = [pscustomobject]@{ state = "ok"; resets_at = $null }
$statusFailed = $false
$stopNoted = $false
# The store could not be read again before a pass (Sync-Queue): said once in the report.
$syncNoted = $false
# The tasks whose write the store refused (somebody else wrote them) since the queue was last
# read: nothing is started for them, and a run's result is not applied, until it is read again.
$staleIds = @{}
# Runs whose result was not applied, per task, in this cycle; and the tasks left alone after
# three of them (the try of a dropped run is handed back, so nothing else would end it).
$droppedRuns = @{}
$abandoned = @{}
# team-engine. The worker seats' memory (team/logs/seats.json: which seat built a task, the
# session of that run); the loop's own file (team/logs/loop.json: its id, pid and heartbeat - what
# the watchdog reads); the handover file a loop that hands over leaves for its successor.
$logsDir = Join-Path $TeamRoot "logs"
$seatsPath = Join-Path $logsDir "seats.json"
$loopPath = Join-Path $logsDir "loop.json"
$handoverPath = Join-Path $logsDir "loop-handover.json"
$handoverFlagPath = Join-Path $TeamRoot "handover.flag"
$seatRecords = Read-TeamSeatRecords -Path $seatsPath
$loopId = "loop-" + ($Machine -replace '[^A-Za-z0-9-]', '').ToLowerInvariant() + "-" + $processStart.ToString("yyyyMMddHHmmss") + "-$PID"
$scriptsHash = Get-TeamScriptsHash -RepoRoot $repoRoot
# Set when this loop hands over: its runs are left running, its successor adopts them.
$handingOver = $false
$loopEnded = $false
$lockRefreshedAt = [datetime]::UtcNow

function Save-SeatRecords {
    try { Save-TeamSeatRecords -Path $script:seatsPath -Records $script:seatRecords }
    catch {
        $note = "çalışan koltuklarının kaydı yazılamadı (team/logs/seats.json): $($_.Exception.Message)"
        if (@($script:cycle.risks) -notcontains $note) { Add-CycleNote -List "risks" -Text $note }
    }
}

function Set-SeatRecord {
    <# What the loop remembers of a task's worker seat; a field not named stays. #>
    param([string]$Id, [string]$Seat = $null, [string]$Session = $null, [string]$GoneAt = $null)
    $record = $script:seatRecords[$Id]
    if ($null -eq $record) { $record = [pscustomobject]@{ owner_seat = ""; session_id = ""; seat_gone_at = "" }; $script:seatRecords[$Id] = $record }
    if ($PSBoundParameters.ContainsKey("Seat")) { $record.owner_seat = $Seat }
    if ($PSBoundParameters.ContainsKey("Session")) { $record.session_id = $Session }
    if ($PSBoundParameters.ContainsKey("GoneAt")) { $record.seat_gone_at = $GoneAt }
    Save-SeatRecords
}

function Write-LoopFile {
    <# team/logs/loop.json: this loop, alive (its heartbeat) or ended. Never a reason to stop. #>
    param([bool]$Running = $true)
    try {
        $document = [ordered]@{
            loop_id = $script:loopId; machine = $Machine; pid = $PID; running = $Running; continuous = [bool]$Continuous
            started_at = (Get-TeamTimestamp -Now $script:processStart); heartbeat_at = (Get-TeamTimestamp); day_id = $script:dayId
            runs = @($script:liveRuns | ForEach-Object { [ordered]@{ task = $_.task; role = $_.role; seat = [string](Get-TeamProperty -InputObject $_ -Name "seat" -Default ""); started_at = $_.started_at } })
        }
        if (-not (Test-Path -LiteralPath $script:logsDir)) { [void](New-Item -ItemType Directory -Force -Path $script:logsDir) }
        Write-TeamJson -Path $script:loopPath -Document $document
    }
    catch { }
}
# The model policy (ADR-0214 addendum 7). $modelSetting is read below, once the report can be
# written. $limitedModels is what the runs said is limited: model id -> { until, type, seen_at };
# a model in it starts no run until its reset. It lives across cycles in team/limits.json (an
# entry nobody dated lives for this cycle only - on disk it would bar the model for ever).
# $limitWindows is the tool's own usage numbers as last seen: fable / all / session ->
# { used_pct, resets_at, observed_at }. $loweredRuns is this cycle's downgrades, newest last.
$modelSetting = $null
$limitedModels = @{}
# "<task>/<role>|<model>" -> how often that run came back limited with a reset already past.
$staleLimits = @{}
$limitWindows = @{}
$loweredRuns = New-Object System.Collections.ArrayList
# A Cloud Core that does not know the status' model fields yet answers 422: it then gets the
# status in the form it knows, for the rest of the cycle (New-CycleStatus -Level: 0 with the team
# engine's fields, 1 without them, 2 legacy).
$statusLevel = 0
# The usage limit ended the cycle: nothing further starts.
$limitStop = $false
# The usage limit is being waited out until then (UTC): the pool starts nothing, the runs in
# flight go on. $null = no wait.
$limitWaitUntil = $null

function ConvertTo-LoopLiteral {
    <# A value as PowerShell source: what a handover writes on its successor's command line. #>
    param($Value)
    if ($Value -is [System.Management.Automation.SwitchParameter]) { return $(if ($Value.IsPresent) { '$true' } else { '$false' }) }
    if ($Value -is [bool]) { return $(if ($Value) { '$true' } else { '$false' }) }
    if ($Value -is [array]) { return "@(" + ((@($Value) | ForEach-Object { ConvertTo-LoopLiteral -Value $_ }) -join ",") + ")" }
    if ($Value -is [int] -or $Value -is [double] -or $Value -is [long]) { return ([System.Convert]::ToString($Value, [System.Globalization.CultureInfo]::InvariantCulture)) }
    return "'" + ([string]$Value -replace "'", "''") + "'"
}

function Add-CycleNote {
    param([string]$List, [string]$Text)
    $script:cycle.$List = @(@($script:cycle.$List) + $Text)
}

function Get-LimitsDocument {
    <# `limits` of the live status, as the contract of the three model-policy cards states it.
       state: what the runs returned. used_pct: the tool's own number, or null - nobody
       computes one - and null again once the window it describes has reset. #>
    $now = [datetime]::UtcNow
    $chain = @(Get-TeamModelChain)
    $closed = @($chain | Where-Object { Test-TeamModelLimited -Limited $script:limitedModels -Model $_ -Now $now })
    $entries = @{}
    foreach ($name in @("fable", "all")) {
        $percent = $null
        if ($script:limitWindows.ContainsKey($name)) {
            $window = $script:limitWindows[$name]
            $ends = ConvertFrom-TeamTimestamp -Text ([string](Get-TeamProperty -InputObject $window -Name "resets_at" -Default ""))
            if ($null -eq $ends -or $now -lt $ends) { $percent = Get-TeamProperty -InputObject $window -Name "used_pct" }
        }
        $entries[$name] = [ordered]@{ state = "ok"; resets_at = $null; used_pct = $percent }
    }
    if ($closed -contains $chain[0]) {
        $entries["fable"].state = "limited"
        $until = [string](Get-TeamProperty -InputObject $script:limitedModels[$chain[0]] -Name "until" -Default "")
        if ($until) { $entries["fable"].resets_at = $until }
    }
    if (@($closed).Count -eq @($chain).Count) {
        # Every model is closed: the limit of all. It lifts when the first of them does.
        $entries["all"].state = "limited"
        $first = @($closed | ForEach-Object { [string](Get-TeamProperty -InputObject $script:limitedModels[$_] -Name "until" -Default "") } | Where-Object { $_ } | Sort-Object)
        if (@($first).Count -gt 0) { $entries["all"].resets_at = $first[0] }
    }
    return [ordered]@{
        fable    = $entries["fable"]
        all      = $entries["all"]
        fallback = [bool](Get-TeamProperty -InputObject $script:modelSetting -Name "fallback" -Default $true)
        lowered  = @($script:loweredRuns.ToArray())
    }
}

function Get-StatusReturns {
    <# team-engine: every returned task whose worker seat still owns it, with that seat - the Ofis
       draws the '!' on the owner seat only, and an owner busy with another task shows the return
       as "next". #>
    $returns = New-Object System.Collections.ArrayList
    if ($null -eq $script:queue) { return @() }
    # The seats are read with the settings at the first refill; before that, -MaxParallel.
    $seatTable = Get-Variable -Name seats -Scope Script -ValueOnly -ErrorAction SilentlyContinue
    $workerSeats = if ($seatTable -is [hashtable] -and $seatTable.ContainsKey("worker")) { [int]$seatTable["worker"] } else { $MaxParallel }
    foreach ($task in @(Get-TeamTasks -Queue $script:queue)) {
        if ([string]$task.state -ne "returned") { continue }
        $known = Get-TeamTaskSeat -Task $task -Records $script:seatRecords
        if ((Get-TeamWorkerSeatIndex -Seat $known.Seat) -le 0) { continue }
        $claim = Get-TeamSeatClaim -OwnerSeat $known.Seat -WorkerSeats $workerSeats -GoneSince (ConvertFrom-TeamTimestamp -Text $known.GoneAt)
        if ($claim.State -eq "released") { continue }
        [void]$returns.Add([ordered]@{ task = [string]$task.id; owner_seat = $known.Seat })
    }
    return @($returns.ToArray())
}

function New-CycleStatus {
    <# -Level: 0 the team engine's fields too (run seats, the loop, the returns' owner seats);
       1 without them (a Cloud Core that does not know them yet answers 422); 2 legacy (no model,
       no limits). #>
    param([int]$Level = 0)
    $Legacy = ($Level -ge 2)
    $document = [ordered]@{
        cycle_id      = $dayId
        machine       = $Machine
        pid           = $PID
        started_at    = [string]$script:cycle.started_at
        runs          = @($script:liveRuns | ForEach-Object {
                $entry = [ordered]@{ task = $_.task; role = $_.role; started_at = $_.started_at }
                if (-not $Legacy) { $entry["model"] = $_.model }
                if ($Level -eq 0) { $entry["seat"] = [string](Get-TeamProperty -InputObject $_ -Name "seat" -Default $_.role) }
                $entry
            })
        estimated_usd = [Math]::Round([double]$script:cycle.spent_usd, 4)
        usage_limit   = [ordered]@{ state = $script:usageLimit.state; resets_at = $script:usageLimit.resets_at }
    }
    if (-not $Legacy) {
        $document["limits"] = (Get-LimitsDocument)
        # Which Claude account the team runs under (the owner switches them, 2026-10-04, and wants
        # to see it on the Ofis): the folder CLAUDE_CONFIG_DIR names, set by the team wrapper.
        $document["account"] = if ($env:CLAUDE_CONFIG_DIR) { Split-Path -Leaf $env:CLAUDE_CONFIG_DIR } else { "varsayilan" }
    }
    if ($Level -eq 0) {
        $document["loop_id"] = $script:loopId
        $document["loop_started_at"] = (Get-TeamTimestamp -Now $script:processStart)
        $document["returns"] = @(Get-StatusReturns)
    }
    $document["updated_at"] = (Get-TeamTimestamp)
    return $document
}

function Write-CycleStatus {
    $script:statusWrittenAt = [datetime]::UtcNow
    # The loop's heartbeat goes with the status: the watchdog starts a loop only when it is old.
    if (-not $script:loopEnded) { Write-LoopFile }
    try {
        if ($useApi) {
            $saved = $false
            if ($script:statusLevel -eq 0) {
                # The engine's fields first; a store that does not know them yet (422) gets the
                # status without them for the rest of this process - silently: nothing is lost
                # that the Ofis could show today.
                try { Save-TeamStatusApi -Store $apiStore -Status (New-CycleStatus -Level 0); $saved = $true }
                catch {
                    if ($_.Exception.Message -notmatch '^HTTP 422 ') { throw }
                    $script:statusLevel = 1
                }
            }
            if (-not $saved) {
                try { Save-TeamStatusApi -Store $apiStore -Status (New-CycleStatus -Level $script:statusLevel) }
                catch {
                    if ($script:statusLevel -ge 2 -or $_.Exception.Message -notmatch '^HTTP 422 ') { throw }
                    $script:statusLevel = 2
                    Add-CycleNote -List "risks" -Text "Cloud Core canlı durumun model ve limit alanlarını henüz tanımıyor (422); eski biçimde yazıldı - Ofis sayfasında model ve limit görünmez (model-policy-api yayınlanınca düzelir)"
                    Save-TeamStatusApi -Store $apiStore -Status (New-CycleStatus -Level 2)
                }
            }
        }
        else { Write-TeamJson -Path $statusPath -Document (New-CycleStatus) }
    }
    catch {
        if (-not $script:statusFailed) {
            $script:statusFailed = $true
            Add-CycleNote -List "risks" -Text "canlı durum yazılamadı (döngü sürdü): $($_.Exception.Message)"
        }
    }
}

function Test-StopRequested {
    <# The owner or the lead asked for a safe stop (team/stop.flag): no NEW run starts; the ones in
       flight finish and are recorded. #>
    if (-not $script:stopNoted -and (Test-Path -LiteralPath $stopFlagPath)) {
        $script:stopNoted = $true
        Add-CycleNote -List "stops" -Text "sahip/lead durdurdu (team/stop.flag); kaldığı yerden devam eder"
    }
    return $script:stopNoted
}

function Save-Report {
    $script:cycle.ended_at = (Get-TeamTimestamp)
    if (-not (Test-Path -LiteralPath $reportsRoot)) { [void](New-Item -ItemType Directory -Force -Path $reportsRoot) }
    $text = New-TeamCycleReport -CycleId $dayId -Queue $script:queue -Cycle $script:cycle
    $path = Join-Path $reportsRoot "$dayId.md"
    [System.IO.File]::WriteAllText($path, $text + "`n", (New-Object System.Text.UTF8Encoding($false)))
    if ($useApi) {
        # The file above is the report; the store keeps its text so the Onay Merkezi on the Cloud
        # Core can show it. A post that fails does not lose the report.
        try { Send-TeamReportApi -Store $apiStore -Name "$dayId.md" -Text $text }
        catch { Write-Host "the report was not posted to the queue store: $($_.Exception.Message)" }
    }
    return $path
}

# ------------------------------------------------------------------ the model policy
# The model each role runs on (owner, 2026-10-01, ADR-0214 addendum 7) is ONE setting in the
# team store: API mode GET /v1/team/queue/models, file mode team/models.json. A Cloud Core
# that does not have the route yet (404) - or cannot be read - never stops the cycle: the
# local file is used, and without one the defaults (lead and inspector on the strongest
# model, the rest one below, fallback on). A role the setting does not name runs on -Model
# when given. What does stop the cycle, before the lock and before any run, is a value that
# is not one of the three model ids: it would go onto a command line.
if ($Model -and -not (Test-TeamModelId -Model $Model)) {
    Write-Host "-Model '$Model' is not a model; nothing was run. One of: $((Get-TeamModelChain) -join ', ')"
    exit 2
}
$modelDocument = $null
$modelSource = "the defaults"
if ($useApi) {
    try {
        $modelDocument = Get-TeamModelsApi -Store $apiStore
        if ($null -ne $modelDocument) { $modelSource = "GET /v1/team/queue/models" }
    }
    catch { Add-CycleNote -List "risks" -Text "model ayarı takım deposundan okunamadı; yerel dosya ya da varsayılanlar kullanıldı: $($_.Exception.Message)" }
}
if ($null -eq $modelDocument -and (Test-Path -LiteralPath $modelsPath)) {
    $modelDocument = Read-TeamJson -Path $modelsPath
    $modelSource = "team/models.json"
}
$modelRead = Read-TeamModelSetting -Document $modelDocument -DefaultModel $Model
if (-not $modelRead.Ok) {
    Write-Host "the model setting ($modelSource) breaks the contract; nothing was run:"
    foreach ($problem in @($modelRead.Problems)) { Write-Host "  - $problem" }
    exit 2
}
$modelSetting = $modelRead.Setting

# What earlier cycles on this machine learnt about the limits. A file that cannot be read is
# no knowledge: the runs will say it again.
if (Test-Path -LiteralPath $limitsPath) {
    try {
        $limitsDocument = Read-TeamJson -Path $limitsPath
        $known = Get-TeamProperty -InputObject $limitsDocument -Name "models"
        if ($known -is [System.Management.Automation.PSCustomObject]) {
            foreach ($property in $known.PSObject.Properties) {
                $until = [string](Get-TeamProperty -InputObject $property.Value -Name "until" -Default "")
                if (-not (Test-TeamModelId -Model ([string]$property.Name)) -or -not $until) { continue }
                $limitedModels[[string]$property.Name] = [pscustomobject]@{
                    until   = $until
                    type    = [string](Get-TeamProperty -InputObject $property.Value -Name "type" -Default "")
                    seen_at = [string](Get-TeamProperty -InputObject $property.Value -Name "seen_at" -Default "")
                }
            }
        }
        $seen = Get-TeamProperty -InputObject $limitsDocument -Name "windows"
        if ($seen -is [System.Management.Automation.PSCustomObject]) {
            foreach ($property in $seen.PSObject.Properties) {
                $percent = Get-TeamProperty -InputObject $property.Value -Name "used_pct"
                if (@("fable", "all", "session") -cnotcontains [string]$property.Name -or $percent -isnot [ValueType] -or $percent -is [bool]) { continue }
                $limitWindows[[string]$property.Name] = $property.Value
            }
        }
    }
    catch { Add-CycleNote -List "risks" -Text "team/limits.json okunamadı (yok sayıldı): $($_.Exception.Message)" }
}

function Save-Limits {
    <# team/limits.json, on this machine: what the next cycle must not find out again. #>
    $now = [datetime]::UtcNow
    $models = [ordered]@{}
    foreach ($id in @(Get-TeamModelChain)) {
        if (-not $script:limitedModels.ContainsKey($id)) { continue }
        $entry = $script:limitedModels[$id]
        if (-not [string]$entry.until -or -not (Test-TeamModelLimited -Limited $script:limitedModels -Model $id -Now $now)) { continue }
        $models[$id] = $entry
    }
    $windows = [ordered]@{}
    foreach ($name in @("fable", "all", "session")) {
        if ($script:limitWindows.ContainsKey($name)) { $windows[$name] = $script:limitWindows[$name] }
    }
    try { Write-TeamJson -Path $limitsPath -Document ([ordered]@{ models = $models; windows = $windows }) }
    catch { }
}

function Register-Limit {
    <# A run came back with the usage limit: remember WHAT is limited, and until when. A
       session or weekly limit closes every model; a model's own limit closes that model; a
       limit that does not say closes the model the run was on - which is marked in every
       case, so the same task is never started twice on a model that just refused it. #>
    param(
        $Done,
        # Whose run it was ("<task>/<role>"): the bound below counts per task, so three parallel
        # runs that each meet a limit which has just lifted are each retried once.
        [string]$Key = ""
    )
    $ids = New-Object System.Collections.ArrayList
    if ([string]$Done.LimitScope -eq "all") { foreach ($id in @(Get-TeamModelChain)) { [void]$ids.Add($id) } }
    elseif (Test-TeamModelId -Model ([string]$Done.LimitedModel)) { [void]$ids.Add([string]$Done.LimitedModel) }
    if ((Test-TeamModelId -Model ([string]$Done.Model)) -and $ids -notcontains [string]$Done.Model) { [void]$ids.Add([string]$Done.Model) }
    foreach ($id in $ids) {
        $script:limitedModels[$id] = [pscustomobject]@{
            until   = $(if ($Done.ResetsAt) { [string]$Done.ResetsAt } else { $null })
            type    = [string]$Done.LimitType
            seen_at = (Get-TeamTimestamp)
        }
    }
    # A reset that is already past marks nothing: the model is picked again, the wait is 0 s and
    # the try is handed back - without a bound the cycle starts runs for ever (a PC clock ahead
    # of the tool's, a stale resetsAt). Such a limit is believed ONCE per task: the run after it
    # is today's "waited out, run again". The second time, the hour it names is not the truth:
    # what it closed is closed for the rest of this cycle, undated - the chain goes down, or
    # the stop line of a limit nobody dated follows. Undated is not written to team/limits.json.
    $own = [string]$Done.Model
    if ((Test-TeamModelId -Model $own) -and -not (Test-TeamModelLimited -Limited $script:limitedModels -Model $own)) {
        $staleKey = "$Key|$own"
        $script:staleLimits[$staleKey] = 1 + [int]$script:staleLimits[$staleKey]
        if ($script:staleLimits[$staleKey] -ge 2) {
            foreach ($id in $ids) { $script:limitedModels[$id].until = $null }
            Add-CycleNote -List "risks" -Text "limit: $own aynı koşuya ($Key) ikinci kez limitli döndü ve bildirdiği sıfırlanma saati geçmişte ($($Done.ResetsAt)); saate güvenilmedi, model bu döngüde kapalı sayıldı"
        }
    }
    Save-Limits
}

function Select-RunModel {
    <# The model a run of this role starts on now (Get-TeamRunModel); Model is $null when it
       must wait. The inspector's floor is the strongest model the task's worker runs really
       used, an entry that does not say counting as the configured worker model
       (Get-TeamInspectionFloor). #>
    param([string]$Role, $Task = $null)
    $configured = [string](Get-TeamProperty -InputObject $script:modelSetting.roles -Name $Role -Default "")
    if (-not $configured) { $configured = [string]$script:modelSetting.roles.worker }
    $floor = ""
    if ($Role -eq "inspector" -and $null -ne $Task) { $floor = [string](Get-TeamInspectionFloor -Task $Task -Setting $script:modelSetting).Model }
    return (Get-TeamRunModel -Configured $configured -Limited $script:limitedModels -Fallback ([bool]$script:modelSetting.fallback) -Floor $floor)
}

# ------------------------------------------------------------------ the lock

# A loop that handed over to this one is still releasing its lock: it is waited for (a minute at
# most) - its runs are not, they are adopted below.
$handover = $null
if (Test-Path -LiteralPath $handoverPath) {
    try { $handover = Read-TeamJson -Path $handoverPath } catch { $handover = $null }
    $fromPid = [int](Get-TeamProperty -InputObject $handover -Name "from_pid" -Default 0)
    if ($fromPid -gt 0 -and $fromPid -ne $PID) {
        $until = [datetime]::UtcNow.AddSeconds(60)
        while ([datetime]::UtcNow -lt $until -and $null -ne (Get-Process -Id $fromPid -ErrorAction SilentlyContinue)) { Start-Sleep -Milliseconds 200 }
    }
}

$lock = $null
if ($useApi) { $lock = Get-TeamLockApi -Store $apiStore }
elseif (Test-Path -LiteralPath $lockPath) { $lock = Read-TeamJson -Path $lockPath }
$decision = Get-TeamLockDecision -Lock $lock -Machine $Machine -Now $started
if ($decision.Kind -eq "ours") {
    # Ours, and fresh. If the process that took it is gone, the run died and the lock with it.
    $holderPid = [int](Get-TeamProperty -InputObject $lock -Name "pid" -Default 0)
    $alive = $false
    if ($holderPid -gt 0) { $alive = ($null -ne (Get-Process -Id $holderPid -ErrorAction SilentlyContinue)) }
    if (-not $alive) {
        $decision = [pscustomobject]@{ MayRun = $true; Kind = "dead"; Holder = $decision.Holder; Since = $decision.Since }
    }
}
if (-not $decision.MayRun) {
    Add-CycleNote -List "stops" -Text "kilit $($decision.Holder) makinesinde ($($decision.Since)); bu döngü hiçbir şey çalıştırmadı"
    $path = Save-Report
    Write-Host "the lock is held by $($decision.Holder) since $($decision.Since); report: $path"
    exit 3
}
if ($decision.Kind -eq "stale") {
    Add-CycleNote -List "risks" -Text "bayat kilit devralındı: $($decision.Holder), $($decision.Since)"
}
if ($decision.Kind -eq "dead") {
    Add-CycleNote -List "risks" -Text "bu makinenin ölmüş bir koşusunun kilidi devralındı ($($decision.Since))"
}

if ($DryRun) {
    foreach ($task in (Get-TeamTasks -Queue $queue)) {
        $next = Get-TeamNextRole -Task $task
        Write-Host ("{0,-40} {1,-22} -> {2} {3}{4}" -f $task.id, $task.state, $next.Kind, $next.Role, $next.Gate)
    }
    exit 0
}

if ($useApi) {
    $taken = Set-TeamLockApi -Store $apiStore -Machine $Machine -CycleId $script:lockCycleId -TakeoverDead ($decision.Kind -eq "dead")
    if (-not [bool]$taken.acquired) {
        # The other machine took it between our read and our write.
        Add-CycleNote -List "stops" -Text "kilit $($taken.holder) makinesinde ($($taken.since)); bu döngü hiçbir şey çalıştırmadı"
        $path = Save-Report
        Write-Host "the lock was taken by $($taken.holder); report: $path"
        exit 3
    }
}
else { Write-TeamJson -Path $lockPath -Document (New-TeamLock -Machine $Machine -CycleId $script:lockCycleId -Now $started) }

try {
    Write-CycleStatus
    if (-not (Test-Path -LiteralPath $cycleDir)) { [void](New-Item -ItemType Directory -Force -Path $cycleDir) }
    $runCount = @{}

    function Test-CapReached {
        if (Test-StopRequested) { return $true }
        # The usage limit already ended this cycle (its line is in the report).
        if ($script:limitStop) { return $true }
        # A cap of 0 is no cap (owner decision 2026-09-30).
        if ($MaxUsd -gt 0 -and $script:cycle.spent_usd -ge $MaxUsd) {
            Add-CycleNote -List "stops" -Text ("bütçe tavanı: {0:0.00} / {1:0.00} USD" -f $script:cycle.spent_usd, $MaxUsd)
            return $true
        }
        if ($CycleMinutes -gt 0 -and ([datetime]::UtcNow - $started).TotalMinutes -ge $CycleMinutes) {
            Add-CycleNote -List "stops" -Text "süre tavanı: $CycleMinutes dakika"
            return $true
        }
        # Not a cap on the work: this PROCESS is bound to the script and the arguments it
        # started with (2026-10-01: a setting changed at 16:45 took effect at 19:35).
        if ($MaxHours -gt 0 -and ([datetime]::UtcNow - $started).TotalHours -ge $MaxHours) {
            Add-CycleNote -List "stops" -Text "çalışma süresi doldu (-MaxHours $MaxHours saat); yeni koşu başlatılmadı - zamanlayıcının bir sonraki döngüsü güncel betik ve ayarla sürdürür"
            return $true
        }
        return $false
    }

    function Start-RoleRun {
        param(
            $Task, [string]$Role, [string]$WorkingDirectory, [string]$Prompt = "", [string[]]$ExcludeTools = @(),
            # What Select-RunModel answered for this run: the model to start on, and the one it
            # should have been (a run started below it is a lowering, and is written down).
            [Parameter(Mandatory = $true)]$Pick,
            # team-engine: the seat the run sits on (worker-N, or its role) - Select-TeamSeatFill
            # gives it, and it is the ONE source of the seat (the status, the board, the return's
            # owner); the session a return resumes, and the line a fresh run is told when that
            # session could not be resumed.
            [string]$Seat = "", [string]$ResumeSession = "", [string]$FallbackNote = "",
            # A run of no single task (the Proje Yöneticisi's duty): its name in the status, on
            # the board and in its report files ("cycle" when not given), and what it was handed.
            [string]$Label = "",
            $Duty = $null
        )
        if (-not $Seat) { $Seat = $Role }
        $roleFile = Join-Path $agentsRoot "$Role.md"
        if (-not (Test-Path -LiteralPath $roleFile)) { throw "there is no role file for '$Role': $roleFile" }
        # A run cap of 0 is no cap at all: then the task's budget.max_usd is an estimate for
        # the report, and the tool gets no --max-budget-usd (owner decision 2026-09-30).
        $cap = $RunMaxUsd
        if ($cap -gt 0) {
            if ($null -ne $Task) {
                $own = [double](Get-TeamProperty -InputObject (Get-TeamProperty -InputObject $Task -Name "budget") -Name "max_usd" -Default 0)
                if ($own -gt 0 -and $own -lt $cap) { $cap = $own }
            }
            if ($MaxUsd -gt 0) {
                $left = $MaxUsd - $script:cycle.spent_usd
                if ($left -lt $cap) { $cap = [Math]::Max(0.01, $left) }
            }
        }
        $runModel = [string]$Pick.Model
        if (-not (Test-TeamModelId -Model $runModel)) { throw "'$runModel' is not a model: no run is started on it" }
        # A worker's session is kept (a return resumes it); every other run stays fresh.
        $arguments = Get-TeamRunArguments -RoleFile $roleFile -MaxUsd $cap -Model $runModel -PrefixArguments $ClaudePrefixArguments -ExcludeTools $ExcludeTools `
            -KeepSession:($Role -eq "worker") -ResumeSession $ResumeSession
        if ($Prompt) { $prompt = $Prompt }
        elseif ($null -ne $Task) { $prompt = New-TeamTaskCard -Task $Task -Role $Role -CycleId $dayId }
        else {
            $prompt = "# Run ($Role, cycle $dayId)`n`nWork as your role file says. Write your proposals under team/proposals/. " +
            "Return your report as your final message, at most 40 lines, naming each file you wrote."
            $subjects = @($ResearchBrief | Where-Object { ([string]$_).Trim() })
            if (@($subjects).Count -gt 0) {
                $prompt += "`n`n## The subjects the lead asks for (one proposal each, at most three)`n"
                foreach ($subject in $subjects) { $prompt += "`n- " + ([string]$subject).Trim() }
            }
        }
        $taskLabel = $(if ($null -ne $Task) { [string]$Task.id } elseif ($Label) { $Label } else { "cycle" })
        $runTemp = ""
        $tempRoot = Read-TeamRunTempRoot -Path $settingsPath
        if ($tempRoot) {
            $runTemp = Join-Path $tempRoot ("{0}-{1}-{2}" -f $taskLabel, $Role, [guid]::NewGuid().ToString("N").Substring(0, 8))
        }
        if ($FallbackNote) { $prompt += "`n`n## " + $FallbackNote }
        # Its output goes to files under the day's folder, so a handover never cuts it off.
        $stem = Join-Path (Join-Path $cycleDir "running") ("{0}-{1}-{2}" -f $taskLabel, $Role, [guid]::NewGuid().ToString("N").Substring(0, 8))
        # The team's board (the run's seat, its task, the address and the token file's path): the
        # seat is $Seat, the same name the status and the return's owner carry.
        # Without the API no address is handed down - not even one this loop itself inherited.
        $boardEnvironment = @{ PAGENTOS_TEAM_SEAT = $Seat; PAGENTOS_TEAM_TASK = $taskLabel; PAGENTOS_TEAM_URL = ""; PAGENTOS_TEAM_TOKEN_FILE = "" }
        if ($useApi) {
            $boardEnvironment["PAGENTOS_TEAM_URL"] = $QueueUrl
            $boardEnvironment["PAGENTOS_TEAM_TOKEN_FILE"] = $QueueToken
        }
        $run = Start-TeamDetachedRun -FilePath $ClaudePath -Arguments $arguments -Prompt $prompt -WorkingDirectory $WorkingDirectory -TempDirectory $runTemp -Stem $stem -Environment $boardEnvironment
        $live = [pscustomobject]@{ task = $taskLabel; role = $Role; started_at = (Get-TeamTimestamp); model = $runModel; seat = $Seat }
        [void]$script:liveRuns.Add($live)
        $loweredFrom = ""
        if ([bool]$Pick.Lowered) {
            # 'model düşürüldü': on this run's line in the report, and in the live status
            # (this cycle's downgrades, newest last, at most twenty).
            $loweredFrom = [string]$Pick.Intended
            [void]$script:loweredRuns.Add([ordered]@{ task = $live.task; role = $Role; from = $loweredFrom; to = $runModel; at = $live.started_at })
            while (@($script:loweredRuns).Count -gt 20) { $script:loweredRuns.RemoveAt(0) }
        }
        Write-CycleStatus
        $deadline = if ($RunMinutes -gt 0) { [datetime]::UtcNow.AddMinutes($RunMinutes) } else { [datetime]::MaxValue }
        return [pscustomobject]@{
            Task = $Task; Role = $Role; Run = $run; Deadline = $deadline; Live = $live; Model = $runModel
            LoweredFrom = $loweredFrom; Where = $WorkingDirectory; Prompt = $Prompt; ExcludeTools = @($ExcludeTools)
            RunTemp = $runTemp; Seat = $Seat; ResumeSession = $ResumeSession; FallbackNote = $FallbackNote
            Label = $Label; Duty = $Duty
        }
    }

    function Add-AdoptedRuns {
        <# continuous-team-loop: the runs a loop that handed over left running, taken as this loop's
           own - waited on by pid, their files read when they end, their seats busy meanwhile. #>
        if ($null -eq $script:handover) { return }
        $adopted = 0
        foreach ($entry in @(Get-TeamProperty -InputObject $script:handover -Name "runs" -Default @())) {
            $taskId = [string](Get-TeamProperty -InputObject $entry -Name "task" -Default "")
            $role = [string](Get-TeamProperty -InputObject $entry -Name "role" -Default "")
            $stem = [string](Get-TeamProperty -InputObject $entry -Name "stem" -Default "")
            if (-not $role -or -not $stem) { continue }
            $task = $null
            if ($taskId -and $taskId -ne "cycle") {
                $task = @(Get-TeamTasks -Queue $script:queue | Where-Object { [string]$_.id -eq $taskId })[0]
                if ($null -eq $task) {
                    Add-CycleNote -List "risks" -Text "devralınamadı: $taskId/$role - iş kuyrukta yok; koşusu (pid $([int]$entry.pid)) kendi başına biter"
                    continue
                }
            }
            $begun = ConvertFrom-TeamTimestamp -Text ([string](Get-TeamProperty -InputObject $entry -Name "started_at" -Default ""))
            if ($null -eq $begun) { $begun = [datetime]::UtcNow }
            $run = New-TeamAdoptedRun -ProcessId ([int](Get-TeamProperty -InputObject $entry -Name "pid" -Default 0)) -Stem $stem -Started $begun
            $model = [string](Get-TeamProperty -InputObject $entry -Name "model" -Default "")
            $seat = [string](Get-TeamProperty -InputObject $entry -Name "seat" -Default $role)
            $adoptedLabel = [string](Get-TeamProperty -InputObject $entry -Name "label" -Default "")
            $live = [pscustomobject]@{ task = $(if ($null -ne $task) { $taskId } elseif ($adoptedLabel) { $adoptedLabel } else { "cycle" }); role = $role; started_at = (Get-TeamTimestamp -Now $begun); model = $model; seat = $seat }
            [void]$script:liveRuns.Add($live)
            $deadline = if ($RunMinutes -gt 0) { $begun.AddMinutes($RunMinutes) } else { [datetime]::MaxValue }
            [void]$script:pool.Add([pscustomobject]@{
                    Task = $task; Role = $role; Run = $run; Deadline = $deadline; Live = $live; Model = $model
                    LoweredFrom = [string](Get-TeamProperty -InputObject $entry -Name "lowered_from" -Default ""); Where = [string](Get-TeamProperty -InputObject $entry -Name "where" -Default $repoRoot)
                    Prompt = [string](Get-TeamProperty -InputObject $entry -Name "prompt" -Default ""); ExcludeTools = @(Get-TeamProperty -InputObject $entry -Name "exclude_tools" -Default @())
                    RunTemp = [string](Get-TeamProperty -InputObject $entry -Name "run_temp" -Default ""); Seat = $seat
                    ResumeSession = [string](Get-TeamProperty -InputObject $entry -Name "resume_session" -Default "")
                    FallbackNote = [string](Get-TeamProperty -InputObject $entry -Name "fallback_note" -Default "")
                    # A duty run of the Proje Yöneticisi: its label and what it was handed come along,
                    # so its decision file is judged when it ends as if this loop had started it.
                    Label = [string](Get-TeamProperty -InputObject $entry -Name "label" -Default ""); Duty = (Get-TeamProperty -InputObject $entry -Name "duty" -Default $null)
                })
            if ($null -ne $task -and @("worker", "inspector", "integrator") -contains $role) {
                if (-not $script:runCount.ContainsKey($taskId)) { $script:runCount[$taskId] = 0 }
                $script:runCount[$taskId] = $script:runCount[$taskId] + 1
            }
            if ($role -eq "researcher") { $script:researchPending = $false }
            if ($role -eq "lead" -and $null -ne $task) { $script:splitTried[$taskId] = $true }
            $adopted++
        }
        Add-CycleNote -List "risks" -Text ("devralındı: $adopted koşu (önceki döngü pid " + [int](Get-TeamProperty -InputObject $script:handover -Name "from_pid" -Default 0) + ", " + [string](Get-TeamProperty -InputObject $script:handover -Name "loop_id" -Default "?") + ")")
        Remove-Item -LiteralPath $script:handoverPath -Force -ErrorAction SilentlyContinue
        Write-CycleStatus
    }

    function Save-Handover {
        <# continuous-team-loop: this loop hands over to new code. What is in flight is written for the
           successor (pid, seat, task, start, files) - it is never killed. #>
        $entries = @($script:pool | ForEach-Object {
                [ordered]@{
                    task = $(if ($null -ne $_.Task) { [string]$_.Task.id } else { "cycle" }); role = [string]$_.Role; seat = [string]$_.Seat
                    pid = [int]$_.Run.Pid; stem = [string]$_.Run.Stem; started_at = (Get-TeamTimestamp -Now $_.Run.Started)
                    model = [string]$_.Model; lowered_from = [string]$_.LoweredFrom; where = [string]$_.Where; prompt = [string]$_.Prompt
                    exclude_tools = @($_.ExcludeTools); run_temp = [string]$_.RunTemp; resume_session = [string]$_.ResumeSession
                    fallback_note = [string]$_.FallbackNote; label = [string]$_.Label; duty = $_.Duty
                    report = ("team/reports/$($script:dayId)/" + [string]$_.Live.task + "-" + [string]$_.Role)
                }
            })
        if (-not (Test-Path -LiteralPath $script:logsDir)) { [void](New-Item -ItemType Directory -Force -Path $script:logsDir) }
        Write-TeamJson -Path $script:handoverPath -Document ([ordered]@{ from_pid = $PID; loop_id = $script:loopId; written_at = (Get-TeamTimestamp); runs = @($entries) })
        Add-CycleNote -List "risks" -Text ("devredildi: $(@($entries).Count) koşu uçuşta bırakıldı; yeni döngü onları devralır")
    }

    function Complete-RoleRun {
        <# What a run that is over left behind: its files, its cost, its line in the report.
           The pool calls it for a run that has ended or is past its time (Test-TeamRunOver),
           so nothing waits here; a run past its time is killed. #>
        param($Started)
        $finished = Wait-TeamRun -Run $Started.Run -Deadline $Started.Deadline
        Remove-TeamRunTemp -Path ([string]$Started.RunTemp)
        if ($null -ne $Started.Run.PSObject.Properties["Stem"]) { Remove-TeamRunFiles -Stem ([string]$Started.Run.Stem) }
        $result = Read-TeamRunResult -StdOut $finished.StdOut -ExitCode $finished.ExitCode -StdErr $finished.StdErr -Model ([string]$Started.Model)
        # The run's own name: its task, a duty run's label, or "cycle" (the researcher).
        $taskId = [string]$Started.Live.task
        $number = 1
        while (Test-Path -LiteralPath (Join-Path $cycleDir "$taskId-$($Started.Role)-$number.json")) { $number++ }
        $stem = Join-Path $cycleDir "$taskId-$($Started.Role)-$number"
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        # The stream of a worker's run is megabytes of tool output. What is kept is what was
        # always kept - the result document - and the limit events beside it; the raw output
        # (its end) only when there was no result to keep.
        $kept = [string]$result.ResultLine
        if (-not $kept) {
            $kept = [string]$finished.StdOut
            if ($kept.Length -gt 200000) { $kept = $kept.Substring($kept.Length - 200000) }
        }
        [System.IO.File]::WriteAllText("$stem.json", $kept, $utf8)
        if (@($result.EventLines).Count -gt 0) { [System.IO.File]::WriteAllText("$stem.limits.jsonl", ((@($result.EventLines) -join "`n") + "`n"), $utf8) }
        if ($finished.StdErr.Trim()) { [System.IO.File]::WriteAllText("$stem.stderr.txt", [string]$finished.StdErr, $utf8) }
        if ($result.Text) { [System.IO.File]::WriteAllText("$stem.md", $result.Text.TrimEnd() + "`n", $utf8) }

        # The two percentages, when this run's events carried them: the tool's own numbers,
        # kept with their reset and when they were seen. A window no event named stays as it was.
        $sawWindow = $false
        foreach ($name in @("fable", "all", "session")) {
            $window = Get-TeamProperty -InputObject $result.Windows -Name $name
            if ($null -eq $window) { continue }
            $script:limitWindows[$name] = [pscustomobject]@{ used_pct = $window.used_pct; resets_at = $window.resets_at; observed_at = (Get-TeamTimestamp) }
            $sawWindow = $true
        }
        if ($sawWindow) { Save-Limits }

        $outcome = if ($finished.TimedOut) { "süre doldu ($RunMinutes dk)" } elseif ($result.Ok) { "tamam" } else { "başarısız: $($result.Why)" }
        $script:cycle.spent_usd = [double]$script:cycle.spent_usd + [double]$result.CostUsd
        $script:liveRuns.Remove($Started.Live)
        Write-CycleStatus
        $ranModel = if (Test-TeamModelId -Model ([string]$result.RanModel)) { [string]$result.RanModel } else { [string]$Started.Model }
        $script:cycle.runs = @(@($script:cycle.runs) + [pscustomobject]@{
                task = $taskId; role = $Started.Role; cost_usd = $result.CostUsd; seconds = $finished.Seconds; outcome = $outcome
                model = [string]$Started.Model; ran_model = $ranModel; lowered_from = [string]$Started.LoweredFrom
                substituted = [bool]$result.Substituted
            })
        $relative = "team/reports/$dayId/$taskId-$($Started.Role)-$number"
        return [pscustomobject]@{
            Ok = ($result.Ok -and -not $finished.TimedOut); Text = $result.Text; Outcome = $outcome
            File = $(if ($result.Text) { "$relative.md" } else { "$relative.json" }); CostUsd = $result.CostUsd
            UsageLimited = [bool]$result.UsageLimited; ResetsAt = [string]$result.ResetsAt
            LimitScope = [string]$result.LimitScope; LimitedModel = [string]$result.LimitedModel; LimitType = [string]$result.LimitType
            Model = [string]$Started.Model; RanModel = $ranModel; Substituted = [bool]$result.Substituted
            SessionId = [string]$result.SessionId; Why = [string]$result.Why
            ResumeLost = ([bool]$Started.ResumeSession -and -not $result.Ok -and
                (Test-TeamResumeLost -Why ([string]$result.Why) -StdErr ([string]$finished.StdErr) -TimedOut ([bool]$finished.TimedOut) -UsageLimited ([bool]$result.UsageLimited)))
        }
    }

    function Set-LimitWait {
        <# The subscription's limit was hit and no model is left to go down to. True when the
           cycle waits it out: the pool starts nothing until the limit lifts, the runs in
           flight go on and are completed as they end, and nothing blocks here. False when it
           must stop - nothing new starts, and the next cycle continues. #>
        param([string]$ResetsAt)
        # Its line is in the report already: two runs that meet the same limit stop the cycle once.
        if ($script:limitStop) { return $false }
        if (-not $WaitForUsageLimit -or -not $ResetsAt) {
            $script:limitStop = $true
            $script:usageLimit = [pscustomobject]@{ state = "stopped"; resets_at = $(if ($ResetsAt) { $ResetsAt } else { $null }) }
            Write-CycleStatus
            Add-CycleNote -List "stops" -Text ("Max kullanım limiti; " + $(if ($ResetsAt) { "sıfırlanma $ResetsAt; " } else { "ne zaman açılacağı söylenmedi; " }) +
                "limit açılınca aynı -CycleId ile yeniden başlat: kaldığı yerden devam eder")
            return $false
        }
        $until = ([datetime]::Parse($ResetsAt, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AdjustToUniversal)).AddSeconds(90)
        $wait = $until - [datetime]::UtcNow
        $note = "Max kullanım limiti: {0} sıfırlanmasına kadar beklendi ({1:0} dk)" -f $ResetsAt, [Math]::Max(0, $wait.TotalMinutes)
        if (@($script:cycle.risks) -notcontains $note) { Add-CycleNote -List "risks" -Text $note }
        if ($null -eq $script:limitWaitUntil -or $until -gt $script:limitWaitUntil) { $script:limitWaitUntil = $until }
        $script:usageLimit = [pscustomobject]@{ state = "waiting"; resets_at = $ResetsAt }
        Write-CycleStatus
        return $true
    }

    function Add-TaskReport {
        param($Task, [string]$Role, $Done)
        $entry = [pscustomobject]@{
            cycle    = $dayId
            role     = $Role
            at       = (Get-TeamTimestamp)
            file     = $Done.File
            cost_usd = $Done.CostUsd
            # A finished run's entry names the model it really ran on (Get-TeamOkOutcome): the
            # inspector of this task - in this cycle or a later one - is never started below it.
            outcome  = $(if ($Done.Ok) { Get-TeamOkOutcome -Model ([string]$Done.RanModel) } else { $Done.Outcome })
            summary  = @(Get-TeamSummary -Text $Done.Text)
        }
        Set-TeamProperty -InputObject $Task -Name "reports" -Value @(@(Get-TeamProperty -InputObject $Task -Name "reports" -Default @()) + $entry)
        Set-TeamProperty -InputObject $Task -Name "updated_at" -Value (Get-TeamTimestamp)
    }

    function Add-InspectionWaitNote {
        <# Why an inspection was not started: said once per task, under the risks. The
           inspection is not lowered below the worker's model and not skipped - it waits. #>
        param($Task, $Pick)
        $closed = @($Pick.Candidates) -join ", "
        $note = if ([string]$Pick.Floor -and (Get-TeamInspectionFloor -Task $Task -Setting $script:modelSetting).Recorded) {
            "denetim bekliyor: $($Task.id) - işçi $($Pick.Floor) ile koştu; en az o kadar güçlü modeller limitte ($closed); daha zayıf modelde denetlenmez"
        }
        elseif ([string]$Pick.Floor) {
            "denetim bekliyor: $($Task.id) - işçinin modeli kayıtlı değil, ayarlı işçi modeli $($Pick.Floor) taban alındı; en az o kadar güçlü modeller limitte ($closed); daha zayıf modelde denetlenmez"
        }
        else { "denetim bekliyor: $($Task.id) - denetleyicinin kullanabileceği modeller limitte ($closed)" }
        if (@($script:cycle.risks) -notcontains $note) { Add-CycleNote -List "risks" -Text $note }
    }

    function Stop-Task {
        param($Task, [string]$Reason)
        Set-TeamProperty -InputObject $Task -Name "state" -Value "stopped"
        Set-TeamProperty -InputObject $Task -Name "reason" -Value $Reason
        Set-TeamProperty -InputObject $Task -Name "updated_at" -Value (Get-TeamTimestamp)
    }

    # ---------------------------------------------------------------- the pool
    # Owner rule 2026-10-01 (ADR-0214 addendum 8): no agent idles while there is work. The
    # cycle used to run in BATCHES - start up to -MaxParallel runs, wait for ALL of them, look
    # at the queue again - so a seat whose run ended after five minutes stayed empty until the
    # slowest run of its batch ended (cycle adr0224-02: three workers of 3310 / 3295 / 3275
    # seconds, the two short ones waiting for the long one), a finished worker's inspection
    # waited for the whole batch, and the researcher ran alone before any task. Now:
    #   * the runs in flight are POLLED, never waited for one by one. One that ends is
    #     completed at once (Complete-PoolRun: report, queue state, the merge of an approved
    #     inspection, the write) - one after the other, so two merges never run together;
    #   * then the seats are filled (Start-PoolRuns): the settings file and the store are
    #     read again, what can run is computed from the queue as it is NOW, and each role's
    #     free seats are given out in queue order (Select-TeamSeatFill). The researcher and
    #     one lead split run beside the tasks' runs;
    #   * the usage limit, when no model is left to go down to, is waited out without
    #     blocking: nothing starts until it lifts, the runs in flight go on;
    #   * the cycle ends when nothing is in flight and nothing can be started.
    $settingsPath = Join-Path $TeamRoot "cycle-settings.json"
    $settingsNoted = @{}
    $seats = @{}
    # The researcher still to be run in this cycle; the proposals whose split was asked for
    # (one try a cycle); and how often a run the cycle makes for itself - the researcher, a
    # split - was started (three models and a retry after a wait: a tool that says "limited"
    # for ever cannot keep the cycle at it).
    $researchPending = $runResearch
    $splitTried = @{}
    $ownTries = @{}
    $postedIdeas = @{}
    # The approved tasks of this refill whose merge waits (Test-TeamAwaitingMerge).
    $mergeWaiting = New-Object System.Collections.ArrayList
    # The Proje Yöneticisi's duty (pm-duty-stopped). $dutyHanded: a stopped task's id -> the
    # updated_at it had when a duty run was handed it (handed once per stop; an entry goes when
    # the task is no longer stopped, so its next stop is a new one). $dutyTimes: how often a task
    # was handed in this cycle (three at most: a hang guard on paid runs). $dutyWaits: a return
    # the protocol refused beside a task holding the same files, applied by THIS script when
    # that task leaves the work. $dutyCapNoted: the third hand-over is said once.
    $dutyHanded = @{}
    $dutyTimes = @{}
    $dutyWaits = @{}
    $dutyCapNoted = @{}
    $dutyMaxTimes = 3
    # The duty ledger (review 2026-10-04): $dutyHanded and a task's hand-over count outlive the
    # cycle in team/duty-ledger.json, so the next tick does not hand the same stop again, and a
    # task handed $script:TeamDutyMaxHandovers times in all goes to the Danışman. Unreadable =
    # empty, and said: a broken ledger costs at most one more hand-over, never a stopped team.
    $dutyLedgerPath = Join-Path $TeamRoot "duty-ledger.json"
    $dutyTotal = @{}
    $dutyLedgerProblem = ""
    if (Test-Path -LiteralPath $dutyLedgerPath) {
        try {
            $ledger = Read-TeamJson -Path $dutyLedgerPath
            foreach ($entry in @($ledger.PSObject.Properties)) {
                $dutyHanded[[string]$entry.Name] = [string](Get-TeamProperty -InputObject $entry.Value -Name "stamp" -Default "")
                $dutyTotal[[string]$entry.Name] = [int](Get-TeamProperty -InputObject $entry.Value -Name "times" -Default 0)
            }
        }
        catch { $dutyLedgerProblem = "nöbet defteri okunamadı (team/duty-ledger.json): " + (([string]$_.Exception.Message) -replace '\s+', ' ') }
    }
    # A cap, the stop flag or the usage limit ended the cycle: nothing new starts.
    $capped = $false
    # Refills in a row that moved a state and started nothing, with nothing in flight. One or
    # two is ordinary (a proposal moved to the owner's gate); more means the store refuses the
    # same move after every read.
    $idlePasses = 0

    function Read-SeatSettings {
        <# The seats of this refill: the parameters, or team/cycle-settings.json when it is
           there. A file that is no setting changes nothing and is said once. #>
        $read = Read-TeamCycleSettings -Path $settingsPath -Workers $MaxParallel -Inspectors $MaxInspectors -Integrators $MaxIntegrators
        foreach ($problem in @($read.Problems)) {
            if ($script:settingsNoted.ContainsKey([string]$problem)) { continue }
            $script:settingsNoted[[string]$problem] = $true
            Add-CycleNote -List "risks" -Text "$problem; döngü kendi parametreleriyle sürdü"
        }
        $script:seats = @{ worker = $read.Workers; inspector = $read.Inspectors; integrator = $read.Integrators; lead = 1; researcher = 1 }
    }

    function Save-PoolQueue {
        <# The cycle's own changes, written. With nothing in flight it is strict, as it always
           was (a store that does not take the cycle's writes ends the cycle); beside runs in
           flight a store that is away is a line in the report (Save-QueueNow). #>
        param([string]$What)
        if (@($script:pool).Count -eq 0) { Save-Queue -Document $script:queue } else { [void](Save-QueueNow -What $What) }
        Resolve-UnwrittenMerges
    }

    function Send-IdeaTexts {
        <# The text of every idea that waits for the owner goes where the queue is kept
           (ADR-0236): the Onay Merkezi's "Detay" shows it. EVERY cycle, for every waiting idea
           whose file is here, whether a researcher ran or not: the route keeps or replaces, so
           a text the store did not take once (two ideas waited with none on 2026-10-02) is
           there after the next cycle. Each file once a cycle: at its start, and what the
           researcher wrote when its run ends. #>
        if (-not $useApi) { return }
        foreach ($idea in @(Get-TeamTasks -Queue $script:queue | Where-Object { @("awaiting_owner", "proposed") -contains [string]$_.state })) {
            $relative = [string](Get-TeamProperty -InputObject $idea -Name "proposal" -Default "")
            $ideaName = [System.IO.Path]::GetFileName($relative)
            # Only a file OF team/proposals (the folder of -TeamRoot), named as the store names one:
            # an idea's card must not make the cycle post a plan, a source file or a path that climbs.
            if ($relative -cne "team/proposals/$ideaName" -or $ideaName -cnotmatch '^[A-Za-z0-9._-]+\.md$') { continue }
            $ideaFile = Join-Path (Join-Path $TeamRoot "proposals") $ideaName
            if ($script:postedIdeas.ContainsKey($ideaName) -or -not (Test-Path -LiteralPath $ideaFile)) { continue }
            $script:postedIdeas[$ideaName] = $true
            try { Send-TeamProposalApi -Store $apiStore -Name ([System.IO.Path]::GetFileName($ideaFile)) -Text ([System.IO.File]::ReadAllText($ideaFile, [System.Text.Encoding]::UTF8)) }
            catch { Add-CycleNote -List "risks" -Text ("fikrin metni depoya yazılamadı ($([System.IO.Path]::GetFileName($ideaFile))): " + (([string]$_.Exception.Message) -replace '\s+', ' ')) }
        }
    }

    function Add-ResearchProposals {
        <# What is under team/proposals/ and not in the queue yet waits for the owner. #>
        $proposals = Join-Path $TeamRoot "proposals"
        if (Test-Path -LiteralPath $proposals) {
            $known = @(Get-TeamTasks -Queue $script:queue | ForEach-Object { [string](Get-TeamProperty -InputObject $_ -Name "proposal" -Default "") })
            foreach ($file in @(Get-ChildItem -LiteralPath $proposals -Filter *.md -File | Sort-Object -Property Name)) {
                $relative = "team/proposals/$($file.Name)"
                if ($known -contains $relative) { continue }
                $first = @(Get-Content -LiteralPath $file.FullName -Encoding UTF8 -TotalCount 5 | Where-Object { $_ -match '\S' })
                $title = if (@($first).Count -gt 0) { ([string]$first[0]).TrimStart("#", " ").Trim() } else { $file.BaseName }
                $id = ("idea-" + ($file.BaseName.ToLowerInvariant() -replace '[^a-z0-9-]', '-')).Trim("-")
                if ($id.Length -gt 64) { $id = $id.Substring(0, 64).Trim("-") }
                $now = Get-TeamTimestamp
                $task = [pscustomobject]@{
                    id = $id; title = $title; roadmap_row = ""; state = "awaiting_owner"; area = @(); branch = ""
                    worktree = ""; assignee = "researcher"; reports = @(); budget = [pscustomobject]@{ max_usd = $RunMaxUsd }
                    created_at = $now; updated_at = $now; proposal = $relative
                }
                Set-TeamProperty -InputObject $script:queue -Name "tasks" -Value @(@(Get-TeamTasks -Queue $script:queue) + $task)
            }
        }
        Save-PoolQueue -What "araştırmacının önerileri"
        Send-IdeaTexts
    }

    function Get-RunnableTasks {
        <# One look at the queue as it is now: the moves a state calls for are made, and what
           could be run is returned in queue order (Task, Role). A task with a run in flight
           is that run's until it ends. #>
        $runnable = New-Object System.Collections.ArrayList
        $moved = $false
        $flying = @{}
        foreach ($run in $script:pool) { if ($null -ne $run.Task) { $flying[[string]$run.Task.id] = $true } }
        foreach ($task in (Get-TeamTasks -Queue $script:queue)) {
            $id = [string]$task.id
            if ($flying.ContainsKey($id)) { continue }
            # Its write was refused and the store could not be read again yet: it is not ours.
            # Or three of its runs were dropped: this cycle starts nothing more for it.
            if ($script:staleIds.ContainsKey($id) -or $script:abandoned.ContainsKey($id)) { continue }
            # Approved, waiting for its merge (the integration branch could not take its base):
            # merged by the refill, never inspected a second time.
            if (Test-TeamAwaitingMerge -Task $task) { [void]$script:mergeWaiting.Add($task); continue }
            $next = Get-TeamNextRole -Task $task
            if ($next.Kind -ne "rest" -and $next.Kind -ne "gate") {
                # A task whose dependencies are not on main yet waits, and says so once.
                $unmet = @(Get-TeamUnmetDependencies -Task $task -Queue $script:queue)
                if (@($unmet).Count -gt 0) {
                    $waitNote = "bekliyor: $($task.id) -> $($unmet -join ', ') main'e girince"
                    if (@($script:cycle.gaps) -notcontains $waitNote) { Add-CycleNote -List "gaps" -Text $waitNote }
                    continue
                }
            }
            if ($next.Kind -eq "move" -and [string]$next.NextState -eq "assigned") {
                # Section 4: never into work beside a task that holds the same files. Two such
                # tasks make a queue Test-TeamQueue refuses - and every later cycle with it.
                $holders = @(Get-TeamAreaHolders -Task $task -Queue $script:queue)
                if (@($holders).Count -gt 0) {
                    $waitNote = "bekliyor: $($task.id) -> $($holders -join ', ') aynı dosyaları bırakınca"
                    if (@($script:cycle.gaps) -notcontains $waitNote) { Add-CycleNote -List "gaps" -Text $waitNote }
                    continue
                }
            }
            if ($next.Kind -eq "move") {
                Set-TeamProperty -InputObject $task -Name "state" -Value $next.NextState
                Set-TeamProperty -InputObject $task -Name "updated_at" -Value (Get-TeamTimestamp)
                $moved = $true
                # Looked at again in THIS refill: an 'approved' task that just became 'assigned' is
                # runnable now. It used to wait a whole round - behind that round's integrators -
                # with its worker seat empty (the owner saw it on the Ofis page, 2026-10-01).
                $next = Get-TeamNextRole -Task $task
                if ($next.Kind -eq "move") { continue }
            }
            if ($next.Kind -ne "run") { continue }
            if (-not $script:runCount.ContainsKey($id)) { $script:runCount[$id] = 0 }
            if ($script:runCount[$id] -ge $MaxRunsPerTask) {
                Stop-Task -Task $task -Reason "bu döngüde $MaxRunsPerTask koşu yapıldı ve iş bitmedi"
                $moved = $true
                continue
            }
            [void]$runnable.Add([pscustomobject]@{ Task = $task; Role = [string]$next.Role; Pick = $null })
        }
        return [pscustomobject]@{ Runnable = @($runnable.ToArray()); Moved = $moved }
    }

    function Get-ReturnClaims {
        <# team-engine: task id -> the worker seat that owns it, for each returned task of this
           refill whose owner's claim lives (Get-TeamSeatClaim). A seat that went away keeps its
           claim for sixty minutes - when it went is remembered in team/logs/seats.json, so a
           handover does not restart the count - and after that the task is anybody's. #>
        param([object[]]$Candidates)
        $claims = @{}
        $now = [datetime]::UtcNow
        foreach ($item in @($Candidates)) {
            if ([string]$item.Role -ne "worker" -or $null -eq $item.Task -or [string]$item.Task.state -ne "returned") { continue }
            $id = [string]$item.Task.id
            $known = Get-TeamTaskSeat -Task $item.Task -Records $script:seatRecords
            $claim = Get-TeamSeatClaim -OwnerSeat $known.Seat -WorkerSeats ([int]$script:seats.worker) -GoneSince (ConvertFrom-TeamTimestamp -Text $known.GoneAt) -Now $now
            switch ($claim.State) {
                "present" {
                    if ($known.GoneAt) { Set-SeatRecord -Id $id -GoneAt "" }
                    $claims[$id] = $claim.Seat
                }
                "waiting" {
                    if (-not $known.GoneAt) { Set-SeatRecord -Id $id -GoneAt (Get-TeamTimestamp -Now $claim.GoneSince) }
                    $claims[$id] = $claim.Seat
                    $waitNote = "bekliyor: $id -> $($claim.Seat) (koltuk şu an yok; en çok $([int]$script:TeamSeatGraceMinutes) dakika onu bekler)"
                    if (@($script:cycle.gaps) -notcontains $waitNote) { Add-CycleNote -List "gaps" -Text $waitNote }
                }
                "released" {
                    $note = "${id}: $($claim.Seat) $($claim.Minutes) dakikadır yok; geri dönen iş herhangi bir çalışana verildi"
                    if (@($script:cycle.risks) -notcontains $note) { Add-CycleNote -List "risks" -Text $note }
                }
            }
        }
        return $claims
    }

    function Invoke-TaskMerge {
        <# An approved task into the integration branch (integrate-follows-release): the base's
           new commits first, then the task. $true when the task's state moved. A base the
           integration branch cannot take is NOT the task's fault: it stays approved and waiting
           (`inspecting` with the inspector's APPROVE as its last report), the lead is told in one
           line, and the next refill tries again. A conflict of the task itself, with the base
           in, sends it back as it always did. #>
        param($Task)
        $merge = Merge-TeamBranch -RepoRoot $repoRoot -CycleId $dayId -Branch ([string]$Task.branch) -Base $Base -Follow
        if ([bool]$merge.BaseConflict) {
            $files = (@($merge.BaseFiles) | Select-Object -First 5) -join ", "
            $note = "entegrasyon dalı $Base dalını alamadı: $files ($($Task.id) onaylı, birleştirilmeyi bekliyor; lead entegrasyon dalını başka bir çalışma kopyasında günceller)"
            if (@($script:cycle.risks) -notcontains $note) { Add-CycleNote -List "risks" -Text $note }
            return $false
        }
        if ($merge.Merged) {
            Set-TeamProperty -InputObject $Task -Name "state" -Value "merged"
            Set-TeamProperty -InputObject $Task -Name "integration_branch" -Value $merge.Integration
            Set-TeamProperty -InputObject $Task -Name "updated_at" -Value (Get-TeamTimestamp)
            return [pscustomobject]@{ Moved = $true; Fresh = (-not [bool]$merge.Already) }
        }
        $script:cycle.conflicts = [int]$script:cycle.conflicts + 1
        $script:cycle.returned = [int]$script:cycle.returned + 1
        $back = Get-TeamStateAfterInspection -Task $Task -Verdict "RETURN"
        Set-TeamProperty -InputObject $Task -Name "returns" -Value $back.Returns
        Set-TeamProperty -InputObject $Task -Name "state" -Value $back.State
        Set-TeamProperty -InputObject $Task -Name "reason" -Value "entegrasyon dalında çakışma"
        Set-TeamProperty -InputObject $Task -Name "updated_at" -Value (Get-TeamTimestamp)
        return [pscustomobject]@{ Moved = $true; Fresh = $false }
    }

    # ---------------------------------------------------------------- the Proje Yöneticisi's duty
    # pm-duty-stopped (the owner, 2026-10-03: "proje yöneticisi koltuğu var zaten, sadece rolü ve
    # şemayı üzerine alması gerekmez mi?"). A stopped task used to wait until a person noticed it on
    # the Ofis page. Now the lead seat, when it is free, takes a duty run of the split's safe shape.

    function Save-DutyLedger {
        <# team/duty-ledger.json: every task ever handed - the stamp of its current stop (empty when
           it is no longer stopped) and its hand-over count. A write that fails is said; the
           in-memory ledger still holds this cycle. #>
        $document = [ordered]@{}
        foreach ($id in @($script:dutyTotal.Keys | Sort-Object)) {
            $document[[string]$id] = [ordered]@{ stamp = [string]$script:dutyHanded[$id]; times = [int]$script:dutyTotal[$id] }
        }
        try { Write-TeamJson -Path $script:dutyLedgerPath -Document $document }
        catch { Add-CycleNote -List "risks" -Text ("nöbet defteri yazılamadı: " + (([string]$_.Exception.Message) -replace '\s+', ' ')) }
    }

    function Get-DutyCandidates {
        <# The stopped tasks to hand a duty run now. Set aside: a task whose write the store refused
           (until it is read again), one the cycle gave up on, one whose return waits for another
           task's files, one handed $dutyMaxTimes times in this cycle (said once), and one that has
           had -MaxRunsPerTask runs - its worker could not be started again in this cycle. #>
        if ($script:dutyLedgerProblem) {
            Add-CycleNote -List "risks" -Text $script:dutyLedgerProblem
            $script:dutyLedgerProblem = ""
        }
        foreach ($id in @($script:dutyHanded.Keys)) {
            $now = @(Get-TeamTasks -Queue $script:queue | Where-Object { [string]$_.id -eq $id })
            if (@($now).Count -eq 0 -or [string]$now[0].state -ne "stopped") { $script:dutyHanded.Remove($id) }
        }
        $skip = @{}
        foreach ($id in @($script:dutyTotal.Keys)) {
            if ([int]$script:dutyTotal[$id] -lt $script:TeamDutyMaxHandovers) { continue }
            $skip[[string]$id] = $true
            $task = @(Get-TeamTasks -Queue $script:queue | Where-Object { [string]$_.id -eq $id -and [string]$_.state -eq "stopped" })
            if (@($task).Count -gt 0 -and -not $script:dutyCapNoted.ContainsKey("total/$id")) {
                $script:dutyCapNoted["total/$id"] = $true
                Add-CycleNote -List "risks" -Text "nöbet: ${id}: toplam $($script:TeamDutyMaxHandovers) kez Proje Yöneticisi'ne verildi ve yine durdu; artık Danışman'ın"
            }
        }
        foreach ($id in @($script:staleIds.Keys) + @($script:abandoned.Keys) + @($script:dutyWaits.Keys)) { $skip[[string]$id] = $true }
        foreach ($id in @($script:runCount.Keys)) { if ([int]$script:runCount[$id] -ge $MaxRunsPerTask) { $skip[[string]$id] = $true } }
        foreach ($id in @($script:dutyTimes.Keys)) {
            if ([int]$script:dutyTimes[$id] -lt $script:dutyMaxTimes) { continue }
            $skip[[string]$id] = $true
            $task = @(Get-TeamTasks -Queue $script:queue | Where-Object { [string]$_.id -eq $id -and [string]$_.state -eq "stopped" })
            if (@($task).Count -gt 0 -and -not $script:dutyCapNoted.ContainsKey($id) -and [string]$script:dutyHanded[$id] -cne [string]$task[0].updated_at) {
                $script:dutyCapNoted[$id] = $true
                Add-CycleNote -List "risks" -Text "nöbet: ${id}: bu döngüde $($script:dutyMaxTimes) kez Proje Yöneticisi'ne verildi ve yine durdu; bu döngüde bir daha verilmiyor - Danışman'ın"
            }
        }
        return @(Get-TeamDutyCandidates -Queue $script:queue -Handed $script:dutyHanded -Skip $skip)
    }

    function Start-DutyRun {
        <# ONE lead run for the stopped tasks of $Item.Duty. They are handed at once, whether the
           run could be started or not: a stop is handed once. $false when it could not start. #>
        param($Item)
        $tasks = @($Item.Duty)
        $number = 1
        while (Test-Path -LiteralPath (Join-Path $repoRoot ("team\plans\$CycleId-duty-$number.json"))) { $number++ }
        $label = "duty-$number"
        $relative = "team/plans/$CycleId-duty-$number.json"
        $listed = New-Object System.Collections.ArrayList
        foreach ($task in $tasks) {
            $id = [string]$task.id
            $script:dutyHanded[$id] = [string](Get-TeamProperty -InputObject $task -Name "updated_at" -Default "")
            $script:dutyTimes[$id] = 1 + [int]$script:dutyTimes[$id]
            $script:dutyTotal[$id] = 1 + [int]$script:dutyTotal[$id]
            [void]$listed.Add([pscustomobject]@{ Id = $id; Updated = $script:dutyHanded[$id] })
        }
        Save-DutyLedger
        $script:ownTries["$label/lead"] = 1 + [int]$script:ownTries["$label/lead"]
        try {
            $folder = Join-Path $repoRoot "team\plans"
            if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
            $card = New-TeamDutyCard -Tasks $tasks -CycleId $CycleId -DutyFile $relative
            $duty = [pscustomobject]@{ File = $relative; Label = $label; Tasks = @($listed.ToArray()) }
            [void]$script:pool.Add((Start-RoleRun -Task $null -Role "lead" -WorkingDirectory $repoRoot -Prompt $card -ExcludeTools @("Bash", "Edit") -Pick $Item.Pick -Label $label -Duty $duty))
            return $true
        }
        catch {
            Add-CycleNote -List "risks" -Text "nöbet koşusu ($label): koşu başlatılamadı: $($_.Exception.Message)"
            return $false
        }
    }

    function Get-DutyReturnBlock {
        <# Why a stopped task cannot go back to its worker with this area now, or $null. A task in
           work that holds the same files (Get-TeamAreaHolders: the one rule), or any rule of the
           queue the move would break (Test-TeamQueue) - never forced, never a broken queue. #>
        param($Task, [string[]]$Area)
        $copy = ConvertFrom-Json -InputObject (ConvertTo-Json -InputObject $Task -Depth 12 -Compress)
        Set-TeamProperty -InputObject $copy -Name "area" -Value ([string[]]@($Area))
        Set-TeamProperty -InputObject $copy -Name "state" -Value "returned"
        $tasks = @(Get-TeamTasks -Queue $script:queue | ForEach-Object { if ([string]$_.id -eq [string]$Task.id) { $copy } else { $_ } })
        $trial = [pscustomobject]@{ version = (Get-TeamProperty -InputObject $script:queue -Name "version" -Default 1); tasks = $tasks }
        $holders = @(Get-TeamAreaHolders -Task $copy -Queue $trial)
        if (@($holders).Count -gt 0) {
            return [pscustomobject]@{ Holders = @($holders); Text = "alan çakışması: $(@($holders) -join ', '); o iş bitince" }
        }
        $broken = @(Test-TeamQueue -Queue $trial)
        if (@($broken).Count -gt 0) {
            return [pscustomobject]@{ Holders = @(); Text = "kuyruk kuralı: " + (([string]$broken[0]) -replace '\s+', ' ') }
        }
        return $null
    }

    function Set-DutyReturned {
        <# The move itself: the area, the state, the reason, the time. The task is no longer
           stopped, so its next stop is a new one. #>
        param($Task, [string[]]$Area, [string]$Reason)
        Set-TeamProperty -InputObject $Task -Name "area" -Value ([string[]]@($Area))
        Set-TeamProperty -InputObject $Task -Name "state" -Value "returned"
        Set-TeamProperty -InputObject $Task -Name "reason" -Value $Reason
        Set-TeamProperty -InputObject $Task -Name "updated_at" -Value (Get-TeamTimestamp)
        $script:dutyHanded.Remove([string]$Task.id)
    }

    function Invoke-DutyDecision {
        <# One decision of a file Test-TeamDuty passed, on the cycle's copy of the queue. A task
           that changed since it was handed (somebody else decided, or the store has another
           version) is left alone and said. #>
        param($Decision, [object[]]$Listed)
        $id = [string]$Decision.task
        $action = [string]$Decision.action
        $reason = ([string]$Decision.reason).Trim()
        $was = @(@($Listed) | Where-Object { [string]$_.Id -ceq $id })
        $found = @(Get-TeamTasks -Queue $script:queue | Where-Object { [string]$_.id -ceq $id })
        if (@($found).Count -eq 0 -or @($was).Count -eq 0 -or [string]$found[0].state -ne "stopped" -or
            [string]$found[0].updated_at -cne [string]$was[0].Updated -or $script:staleIds.ContainsKey($id) -or (Test-TaskMovedInStore -Task $found[0])) {
            Add-CycleNote -List "risks" -Text "nöbet: ${id}: Proje Yöneticisi karar verirken iş değişti; karar ($action) uygulanmadı"
            return
        }
        $task = $found[0]
        if ($action -eq "escalate") {
            Set-TeamProperty -InputObject $task -Name "reason" -Value ((Get-TeamDutyPrefix -Kind "escalated") + $reason)
            Set-TeamProperty -InputObject $task -Name "updated_at" -Value (Get-TeamTimestamp)
            $script:dutyHanded[$id] = [string]$task.updated_at
            $short = ($reason -replace '\s+', ' ')
            if ($short.Length -gt 300) { $short = $short.Substring(0, 300) + " [...]" }
            Add-CycleNote -List "risks" -Text "Danışman'a iletildi: ${id}: $short"
            return
        }
        $area = New-Object System.Collections.ArrayList
        foreach ($entry in @(Get-TeamProperty -InputObject $task -Name "area" -Default @())) { [void]$area.Add([string]$entry) }
        if ($action -eq "grant_and_return") {
            foreach ($grant in @($Decision.PSObject.Properties["grant"].Value)) {
                $path = (ConvertTo-TeamAreaPath -Text ([string]$grant)).Path
                if (-not (Test-TeamPathInsideArea -Path $path -Area ([string[]]$area.ToArray()))) { [void]$area.Add($path) }
            }
        }
        $newArea = [string[]]$area.ToArray()
        $returned = (Get-TeamDutyPrefix -Kind "returned") + $reason
        $blocked = Get-DutyReturnBlock -Task $task -Area $newArea
        if ($null -ne $blocked) {
            # Not forced: the task stays stopped, and says why. Held by a task in work, the
            # return is THIS script's to make when that task leaves the work (Resolve-DutyWaits).
            Set-TeamProperty -InputObject $task -Name "reason" -Value "$returned ($($blocked.Text))"
            Set-TeamProperty -InputObject $task -Name "updated_at" -Value (Get-TeamTimestamp)
            $script:dutyHanded[$id] = [string]$task.updated_at
            if (@($blocked.Holders).Count -gt 0) {
                $script:dutyWaits[$id] = [pscustomobject]@{ Updated = [string]$task.updated_at; Area = $newArea; Reason = $returned }
                $waitNote = "bekliyor: $id -> $(@($blocked.Holders) -join ', ') aynı dosyaları bırakınca (Proje Yöneticisi geri verdi)"
                if (@($script:cycle.gaps) -notcontains $waitNote) { Add-CycleNote -List "gaps" -Text $waitNote }
            }
            return
        }
        Set-DutyReturned -Task $task -Area $newArea -Reason $returned
    }

    function Resolve-DutyWaits {
        <# The returns the protocol refused beside a task holding the same files: made now when
           nobody holds them any more. One that somebody else moved meanwhile is theirs. $true
           when a task moved. #>
        $moved = $false
        foreach ($id in @($script:dutyWaits.Keys)) {
            $wait = $script:dutyWaits[$id]
            $found = @(Get-TeamTasks -Queue $script:queue | Where-Object { [string]$_.id -ceq $id })
            if (@($found).Count -eq 0 -or [string]$found[0].state -ne "stopped" -or [string]$found[0].updated_at -cne [string]$wait.Updated) {
                $script:dutyWaits.Remove($id)
                continue
            }
            if ($script:staleIds.ContainsKey($id)) { continue }
            if ($null -ne (Get-DutyReturnBlock -Task $found[0] -Area ([string[]]$wait.Area))) { continue }
            Set-DutyReturned -Task $found[0] -Area ([string[]]$wait.Area) -Reason ([string]$wait.Reason)
            $script:dutyWaits.Remove($id)
            $moved = $true
        }
        return $moved
    }

    function Complete-Duty {
        <# The duty run ended: THIS script judges the file it wrote (Test-TeamDuty) and applies it
           whole, or refuses it whole and says why. #>
        param($Started, $Done)
        $duty = $Started.Duty
        $label = [string]$duty.Label
        $next = Resume-OwnRun -Started $Started -Done $Done -Key "$label/lead"
        if ($next -eq "restarted") { return }
        if ($next -eq "waiting") {
            # Handed back: the limit is waited out and the tasks are handed again when it lifts.
            foreach ($listed in @($duty.Tasks)) {
                $script:dutyHanded.Remove([string]$listed.Id)
                $script:dutyTimes[[string]$listed.Id] = [Math]::Max(0, [int]$script:dutyTimes[[string]$listed.Id] - 1)
                $script:dutyTotal[[string]$listed.Id] = [Math]::Max(0, [int]$script:dutyTotal[[string]$listed.Id] - 1)
            }
            Save-DutyLedger
            return
        }
        if (-not $Done.Ok) {
            Add-CycleNote -List "risks" -Text "nöbet koşusu (${label}): $($Done.Outcome)"
            return
        }
        $read = Read-TeamDutyFile -Path (Join-Path $repoRoot ([string]$duty.File -replace '/', '\'))
        $why = @()
        if (-not $read.Ok) { $why = @($read.Why) }
        else { $why = @(Test-TeamDuty -Decisions @($read.Decisions) -Listed @(@($duty.Tasks) | ForEach-Object { [string]$_.Id }) -Queue $script:queue) }
        if (@($why).Count -gt 0) {
            Add-CycleNote -List "risks" -Text ("nöbet kararı reddedildi (${label}): " + ((@($why) | ForEach-Object { ([string]$_) -replace '\s+', ' ' }) -join "; "))
            return
        }
        foreach ($decision in @($read.Decisions)) { Invoke-DutyDecision -Decision $decision -Listed @($duty.Tasks) }
        Save-PoolQueue -What "nöbet: $label"
    }

    function Start-PoolRun {
        <# One candidate of a refill, started in the seat it was given. $false when it could
           not be started: a task is stopped with the reason, a run the cycle makes for itself
           (the researcher, a lead's split) is over for this cycle and a line in the report.
           Never an error out of the refill - that would end the cycle, and its 'finally' kills
           every run in flight. #>
        param($Item)
        $task = $Item.Task
        $role = [string]$Item.Role
        if ($role -eq "researcher") {
            $proposals = Join-Path $TeamRoot "proposals"
            if (-not (Test-Path -LiteralPath $proposals)) { [void](New-Item -ItemType Directory -Force -Path $proposals) }
            $script:ownTries["cycle/researcher"] = 1 + [int]$script:ownTries["cycle/researcher"]
            try {
                [void]$script:pool.Add((Start-RoleRun -Task $null -Role "researcher" -WorkingDirectory $repoRoot -Pick $Item.Pick))
                return $true
            }
            catch {
                # As a researcher's run that failed (Complete-Research), without the marker of a
                # finished run: the next cycle tries again.
                $script:researchPending = $false
                Add-CycleNote -List "stops" -Text "araştırmacı: koşu başlatılamadı: $($_.Exception.Message)"
                Add-ResearchProposals
                return $false
            }
        }
        if ($role -eq "lead" -and $null -ne $Item.PSObject.Properties["Duty"]) {
            # The Proje Yöneticisi's duty for stopped tasks (pm-duty-stopped).
            return (Start-DutyRun -Item $Item)
        }
        if ($role -eq "lead") {
            # The lead's split. A proposal that serves a roadmap row is approved in advance
            # (TEAM_PROTOCOL 3a) but has no area yet. ONE fresh lead run per proposal writes the
            # split; THIS script judges it when the run ends (Complete-Split).
            $proposalId = [string]$task.id
            # One try a cycle, whether the run could be started or not.
            $script:splitTried[$proposalId] = $true
            $script:ownTries["$proposalId/lead"] = 1 + [int]$script:ownTries["$proposalId/lead"]
            try {
                $splitRelative = "team/plans/$dayId-split-$proposalId.json"
                $splitPath = Join-Path $repoRoot ($splitRelative -replace '/', '\')
                # A file left by an earlier run is not this run's answer.
                if (Test-Path -LiteralPath $splitPath) { Remove-Item -LiteralPath $splitPath -Force }
                $splitFolder = Split-Path -Parent $splitPath
                if (-not (Test-Path -LiteralPath $splitFolder)) { [void](New-Item -ItemType Directory -Force -Path $splitFolder) }
                $card = New-TeamSplitCard -Task $task -Queue $script:queue -CycleId $dayId -SplitFile $splitRelative
                # The lead's run follows the setting and the chain like any role's.
                [void]$script:pool.Add((Start-RoleRun -Task $task -Role "lead" -WorkingDirectory $repoRoot -Prompt $card -ExcludeTools @("Bash", "Edit") -Pick $Item.Pick))
                return $true
            }
            catch {
                # The proposal stays where it was; the next cycle asks again.
                Add-CycleNote -List "risks" -Text "bölme koşusu: ${proposalId}: koşu başlatılamadı: $($_.Exception.Message)"
                return $false
            }
        }
        $script:runCount[[string]$task.id] = $script:runCount[[string]$task.id] + 1
        $where = $repoRoot
        $seat = [string](Get-TeamProperty -InputObject $Item -Name "Seat" -Default $role)
        $resume = ""
        if ($role -eq "worker") {
            $known = Get-TeamTaskSeat -Task $task -Records $script:seatRecords
            if ([string]$task.state -eq "returned" -and $known.Seat -eq $seat -and $known.Session) {
                # The seat that built it fixes it, in its own session (the owner's rule of 2026-10-03).
                $resume = $known.Session
            }
            elseif ($known.Seat -ne $seat) {
                # Built here (or its owner's claim ran out): this seat owns it from now on.
                Set-SeatRecord -Id ([string]$task.id) -Seat $seat -Session "" -GoneAt ""
            }
        }
        try {
            if ($role -eq "worker" -or $role -eq "inspector") {
                $branch = [string](Get-TeamProperty -InputObject $task -Name "branch" -Default "")
                if (-not $branch) {
                    $slug = ([string]$task.id)
                    $branch = Get-TeamBranchName -CycleId $dayId -Role "worker" -Slug $slug
                    Set-TeamProperty -InputObject $task -Name "branch" -Value $branch
                }
                $tree = New-TeamWorktree -RepoRoot $repoRoot -Branch $branch -Base $Base
                Set-TeamProperty -InputObject $task -Name "worktree" -Value (".claude/worktrees/" + $branch)
                $where = $tree.Path
            }
            if ($role -eq "worker") { Set-TeamProperty -InputObject $task -Name "state" -Value "in_progress" }
            Set-TeamProperty -InputObject $task -Name "assignee" -Value $role
            [void]$script:pool.Add((Start-RoleRun -Task $task -Role $role -WorkingDirectory $where -Pick $Item.Pick -Seat $seat -ResumeSession $resume))
            return $true
        }
        catch {
            Stop-Task -Task $task -Reason "koşu başlatılamadı: $($_.Exception.Message)"
            return $false
        }
    }

    function Start-PoolRuns {
        <# A refill: the seats that are free are given to what can run now. Returns what it did:
           Started (runs), Failed (starts that failed: each is over - a stopped task, a line in
           the report - and its seat is still free), Moved (a state was moved), Blocked
           (candidates no model is open for) and the earliest reset those wait for ("" = nobody
           said). #>
        $pass = [pscustomobject]@{ Started = 0; Failed = 0; Moved = $false; Blocked = 0; BlockedReset = "" }
        if ($script:capped) { return $pass }
        Read-SeatSettings
        Sync-Queue
        $candidates = New-Object System.Collections.ArrayList
        if ($script:researchPending -and [int]$script:ownTries["cycle/researcher"] -lt 5 -and @($script:pool | Where-Object { $_.Role -eq "researcher" }).Count -eq 0) {
            [void]$candidates.Add([pscustomobject]@{ Task = $null; Role = "researcher"; Pick = $null })
        }
        # -ResearchOnly: the researcher writes its proposals, they are queued for the owner, and
        # NO task is run or moved - not even an approved one.
        if (-not $ResearchOnly) {
            # A return the Proje Yöneticisi made that waited for another task's files: made now
            # when they are free, before the queue is looked at - its worker may start in this refill.
            $dutyMoved = $false
            if (-not $NoDuty) { $dutyMoved = [bool](Resolve-DutyWaits) }
            if (@($script:pool | Where-Object { $_.Role -eq "lead" }).Count -eq 0) {
                # One lead seat: the duty for stopped tasks first (work that waits), then a split.
                if (-not $NoDuty) {
                    $stuck = @(Get-DutyCandidates)
                    if (@($stuck).Count -gt 0) { [void]$candidates.Add([pscustomobject]@{ Task = $null; Role = "lead"; Pick = $null; Duty = @($stuck) }) }
                }
                foreach ($proposal in @(Get-TeamSplitCandidates -Queue $script:queue)) {
                    # A refused split leaves the proposal where it was; the next cycle asks again.
                    if ($script:splitTried.ContainsKey([string]$proposal.id)) { continue }
                    [void]$candidates.Add([pscustomobject]@{ Task = $proposal; Role = "lead"; Pick = $null })
                }
            }
            $script:mergeWaiting.Clear()
            $look = Get-RunnableTasks
            $pass.Moved = ([bool]$look.Moved -or $dutyMoved)
            if ($pass.Moved) { Save-PoolQueue -What "durum taşımaları" }
            # integrate-follows-release: an approved task whose base could not be taken in is merged
            # now that the base may have moved (the lead took it in, or the base merges cleanly now).
            $mergedNow = $false
            foreach ($waiting in @($script:mergeWaiting.ToArray())) {
                if (Invoke-TaskMerge -Task $waiting) { $pass.Moved = $true; $mergedNow = $true }
            }
            if ($mergedNow) { Save-PoolQueue -What "bekleyen birleştirmeler" }
            foreach ($item in @($look.Runnable)) {
                # A move the store refused: the task was written by somebody else between our read
                # and this write (the lead stopped it, the owner decided). It is not run on our copy.
                if (-not $script:staleIds.ContainsKey([string]$item.Task.id)) { [void]$candidates.Add($item) }
            }
        }
        if (@($candidates).Count -eq 0) { return $pass }
        if (Test-CapReached) { $script:capped = $true; return $pass }
        # The limit is being waited out: nothing starts until it lifts.
        if ($null -ne $script:limitWaitUntil) { return $pass }

        # The model first: a run whose every allowed model is limited is given no seat - no
        # worktree, no try spent - and the others of the queue go on.
        $open = New-Object System.Collections.ArrayList
        foreach ($item in $candidates) {
            $pick = Select-RunModel -Role $item.Role -Task $item.Task
            if ($null -eq $pick.Model) {
                $pass.Blocked = $pass.Blocked + 1
                if ($pick.ResetsAt -and (-not $pass.BlockedReset -or [string]::CompareOrdinal([string]$pick.ResetsAt, [string]$pass.BlockedReset) -lt 0)) { $pass.BlockedReset = [string]$pick.ResetsAt }
                if ($item.Role -eq "inspector") { Add-InspectionWaitNote -Task $item.Task -Pick $pick }
                continue
            }
            $item.Pick = $pick
            [void]$open.Add($item)
        }
        $fill = @(Select-TeamSeatFill -Candidates @($open.ToArray()) -InFlight @($script:pool.ToArray()) -Seats $script:seats -Claims (Get-ReturnClaims -Candidates @($open.ToArray())))
        foreach ($item in $fill) {
            if (Start-PoolRun -Item $item) { $pass.Started = $pass.Started + 1 } else { $pass.Failed = $pass.Failed + 1 }
        }
        if (@($fill).Count -gt 0) { [void](Save-QueueNow -What "koşuların başlangıcı") }
        return $pass
    }

    function Resume-LimitedRun {
        <# A run came back with the usage limit. The fallback chain (ADR-0214 addendum 7): the
           SAME run, at once, on the next model down - when the setting allows it, a model is
           open, and nobody asked the cycle to stop. $true when it is in flight again. #>
        param($Started, $Again)
        if ($null -eq $Again.Model -or [string]$Again.Model -eq [string]$Started.Model -or (Test-StopRequested)) { return $false }
        try {
            [void]$script:pool.Add((Start-RoleRun -Task $Started.Task -Role $Started.Role -WorkingDirectory $Started.Where -Prompt $Started.Prompt -ExcludeTools $Started.ExcludeTools -Pick $Again -Label $Started.Label -Duty $Started.Duty `
                -Seat ([string]$Started.Seat) -ResumeSession ([string]$Started.ResumeSession) -FallbackNote ([string]$Started.FallbackNote)))
            return $true
        }
        catch {
            Add-CycleNote -List "risks" -Text "$($Started.Live.task): bir alt modelde yeniden başlatılamadı: $($_.Exception.Message)"
            return $false
        }
    }

    function Resume-OwnRun {
        <# The end of a run the cycle makes for itself (the researcher, a lead's split), with the
           model policy. "over": the run's result stands. "restarted": it is in flight again, one
           model down. "waiting": no model is left and the limit is waited out - the run is
           started again when it lifts. At most five starts, whatever the tool says. #>
        param($Started, $Done, [string]$Key)
        if (-not $Done.UsageLimited) { return "over" }
        Register-Limit -Done $Done -Key $Key
        $again = Select-RunModel -Role $Started.Role -Task $Started.Task
        $left = ([int]$script:ownTries[$Key] -lt 5)
        if ($left -and (Resume-LimitedRun -Started $Started -Again $again)) {
            $script:ownTries[$Key] = 1 + [int]$script:ownTries[$Key]
            return "restarted"
        }
        $reset = if ($null -eq $again.Model) { [string]$again.ResetsAt } else { [string]$Done.ResetsAt }
        if ((Set-LimitWait -ResetsAt $reset) -and $left) { return "waiting" }
        return "over"
    }

    function Complete-Research {
        param($Started, $Done)
        if ((Resume-OwnRun -Started $Started -Done $Done -Key "cycle/researcher") -ne "over") { return }
        $script:researchPending = $false
        # A limited run is nobody's failure (the rule of every run): the limit has its own line,
        # and no finished-run marker is written, so the next cycle runs the researcher again.
        if ($Done.UsageLimited) { }
        elseif (-not $Done.Ok) { Add-CycleNote -List "stops" -Text "araştırmacı: $($Done.Outcome)" }
        else { [System.IO.File]::WriteAllText($researchMarker, (Get-TeamTimestamp), (New-Object System.Text.UTF8Encoding($false))) }
        Add-ResearchProposals
    }

    function Complete-Split {
        <# The lead's split run ended: THIS script judges the file it wrote (Test-TeamSplit) and
           takes it whole or refuses it whole. #>
        param($Started, $Done)
        $proposal = $Started.Task
        $proposalId = [string]$proposal.id
        $next = Resume-OwnRun -Started $Started -Done $Done -Key "$proposalId/lead"
        if ($next -eq "restarted") { return }
        if ($next -eq "waiting") { $script:splitTried.Remove($proposalId); return }
        if (-not $Done.Ok) {
            Add-CycleNote -List "risks" -Text "bölme koşusu: ${proposalId}: $($Done.Outcome)"
            return
        }
        $splitPath = Join-Path $repoRoot ("team\plans\$dayId-split-$proposalId.json")
        $read = Read-TeamSplitFile -Path $splitPath
        $why = @()
        if (-not $read.Ok) { $why = @($read.Why) }
        else { $why = @(Test-TeamSplit -Split $read.Split -Queue $script:queue) }
        if (@($why).Count -eq 0) {
            $made = @(ConvertTo-TeamSplitTasks -Split $read.Split -Proposal $proposal -MaxUsd $RunMaxUsd)
            $trial = [pscustomobject]@{ version = 1; tasks = @(@(Get-TeamTasks -Queue $script:queue) + $made) }
            $why = @(Test-TeamQueue -Queue $trial)
            if (@($why).Count -eq 0) {
                Set-TeamProperty -InputObject $script:queue -Name "tasks" -Value @(@(Get-TeamTasks -Queue $script:queue) + $made)
                Set-TeamProperty -InputObject $proposal -Name "state" -Value "done"
                Set-TeamProperty -InputObject $proposal -Name "reason" -Value ("bölündü: " + ((@($made) | ForEach-Object { [string]$_.id }) -join ", "))
                Set-TeamProperty -InputObject $proposal -Name "updated_at" -Value (Get-TeamTimestamp)
                Save-PoolQueue -What "bölme: $proposalId"
                return
            }
        }
        Add-CycleNote -List "risks" -Text ("bölme reddedildi: ${proposalId}: " + ((@($why) | ForEach-Object { ([string]$_) -replace '\s+', ' ' }) -join "; "))
    }

    function Complete-TaskRun {
        <# The end of a task's run (integrator, worker, inspector): its result moves the task. #>
        param($Started, $Done)
        $task = $Started.Task
        $role = [string]$Started.Role
        $id = [string]$task.id
        # Did THIS run make a new merge commit (the only one that may be taken back)?
        $freshMerge = $false
        # The task as it is before this run's result touches it (see Restore-TaskCopy).
        $beforeResult = ConvertTo-Json -InputObject $task -Depth 12 -Compress
        if (Test-TaskMovedInStore -Task $task) {
            # Their word stands for the RUN too, not only for the row: no merge, no state, no
            # report entry (the report is in its file). What the run said about a MODEL's
            # limit is still true. The next refill reads the store's version of the task.
            if ($done.UsageLimited) { Register-Limit -Done $done -Key "$id/$role" }
            # Not the task's try either: -MaxRunsPerTask counts the runs whose result counted.
            $script:runCount[$id] = [Math]::Max(0, $script:runCount[$id] - 1)
            $script:staleIds[$id] = $true
            Add-CycleNote -List "risks" -Text "${id}: koşu ($role) sürerken depoda başkası değiştirdi; koşunun sonucu uygulanmadı (raporu: $($done.File))"
            # The hand-back must not become a way to run a task for ever (a store that takes
            # the move and refuses every later write: 133 paid runs in a minute on the fake).
            $script:droppedRuns[$id] = [int]$script:droppedRuns[$id] + 1
            if ([int]$script:droppedRuns[$id] -ge 3 -and -not $script:abandoned.ContainsKey($id)) {
                $script:abandoned[$id] = $true
                Add-CycleNote -List "risks" -Text "${id}: üç koşusunun sonucu uygulanamadı (depoda her seferinde başkası değiştirmiş); bu döngü bu işe bir daha koşu başlatmıyor"
            }
            return
        }
        if ($role -eq "inspector" -and $done.Ok) {
            # The inspector's rule holds against the tool too: an inspection the tool itself
            # ran on a weaker model than the worker's gives no verdict. The model that was
            # asked for is treated as limited (that is why the tool left it), and the
            # inspection is started again on a model at least as strong, or waits.
            $floor = Get-TeamInspectionFloor -Task $task -Setting $script:modelSetting
            $workerRank = Get-TeamModelRank -Model ([string]$floor.Model)
            if ($workerRank -ge 0 -and (Get-TeamModelRank -Model ([string]$done.RanModel)) -gt $workerRank) {
                $workerSaid = if ($floor.Recorded) { "işçi $($floor.Model) ile koşmuştu" } else { "işçinin modeli kayıtlı değil, ayarlı işçi modeli $($floor.Model)" }
                $done.Ok = $false
                $done.UsageLimited = $true
                $done.LimitScope = "model"
                $done.LimitedModel = [string]$done.Model
                $done.ResetsAt = ""
                $done.Outcome = "hüküm alınmadı: araç denetimi $($done.RanModel) ile koşturdu, $workerSaid"
                Add-CycleNote -List "risks" -Text "${id}: hüküm alınmadı - araç denetimi $($done.Model) yerine $($done.RanModel) ile koşturdu; bu, işçinin modelinden ($($floor.Model)) zayıf"
            }
        }
        if ($role -eq "worker" -and $done.SessionId -and [string](Get-TeamTaskSeat -Task $task -Records $script:seatRecords).Seat -eq [string]$Started.Seat) {
            # The session of the run that built (or fixed) it: what its next return resumes.
            Set-SeatRecord -Id $id -Session ([string]$done.SessionId)
        }
        if ($role -eq "worker" -and [bool]$done.ResumeLost) {
            # The session could not be resumed (expired, missing, refused): the same seat starts a
            # fresh run with the card - the reason and the inspector's report - and is told so. Not a
            # failure of the task and not a try spent; once only (the fresh run resumes nothing).
            # A resumed run that timed out, hit its budget or crashed is an ordinary failure
            # (Test-TeamResumeLost): its report counts below and no new run takes its place.
            $why = ([string]$done.Why -replace '\s+', ' ').Trim()
            Add-CycleNote -List "risks" -Text "${id}: $($Started.Seat) önceki oturumunu sürdüremedi ($why); taze koşu raporla başlatıldı"
            try {
                $note = "Önceki oturumu sürdürülemedi ($why): bu taze bir koşu. Dal, neden ve denetleyicinin raporu elinde; raporunda bunu söyle."
                [void]$script:pool.Add((Start-RoleRun -Task $task -Role $role -WorkingDirectory $Started.Where -Pick ([pscustomobject]@{ Model = $Started.Model; Lowered = $false; Intended = "" }) -Seat ([string]$Started.Seat) -FallbackNote $note))
                Set-TeamProperty -InputObject $task -Name "updated_at" -Value (Get-TeamTimestamp)
                return
            }
            catch { Add-CycleNote -List "risks" -Text "${id}: taze koşu başlatılamadı: $($_.Exception.Message)" }
        }
        Add-TaskReport -Task $task -Role $role -Done $done

        if ($done.UsageLimited) {
            # Not the task's failure and not a try spent (owner decision 2026-09-30).
            Register-Limit -Done $done -Key "$id/$role"
            $script:runCount[$id] = [Math]::Max(0, $script:runCount[$id] - 1)
            # The inspector is never lowered below the worker's model.
            $again = Select-RunModel -Role $role -Task $task
            if (Resume-LimitedRun -Started $Started -Again $again) {
                $script:runCount[$id] = $script:runCount[$id] + 1
                Set-TeamProperty -InputObject $task -Name "updated_at" -Value (Get-TeamTimestamp)
                return
            }
            # No model to go down to (or fallback is off): the task goes back to where it
            # was and is taken up again when the limit lifts - or the cycle stops for it.
            if ($role -eq "inspector" -and $null -eq $again.Model) { Add-InspectionWaitNote -Task $task -Pick $again }
            $waitFor = if ($null -eq $again.Model) { [string]$again.ResetsAt } else { [string]$done.ResetsAt }
            [void](Set-LimitWait -ResetsAt $waitFor)
            if ($role -eq "worker") { Set-TeamProperty -InputObject $task -Name "state" -Value "assigned" }
            Set-TeamProperty -InputObject $task -Name "updated_at" -Value (Get-TeamTimestamp)
            return
        }
        if (-not $done.Ok) {
            $failures = [int](Get-TeamProperty -InputObject $task -Name "failed_runs" -Default 0) + 1
            Set-TeamProperty -InputObject $task -Name "failed_runs" -Value $failures
            if ($failures -ge 2) { Stop-Task -Task $task -Reason "iki koşu sonuç vermedi ($($done.Outcome))" }
            elseif ($role -eq "worker") { Set-TeamProperty -InputObject $task -Name "state" -Value "assigned" }
            return
        }
        switch ($role) {
            "integrator" {
                $plan = "team/plans/$id-integration.md"
                if (Test-Path -LiteralPath (Join-Path $repoRoot ($plan -replace '/', '\'))) {
                    Set-TeamProperty -InputObject $task -Name "plan" -Value $plan
                    # With its plan an approved task is the refill's to move into work - beside
                    # nobody that holds its files (the same rule as every other approved task).
                    if (@(Get-TeamAreaHolders -Task $task -Queue $script:queue).Count -eq 0) { Set-TeamProperty -InputObject $task -Name "state" -Value "assigned" }
                }
                else { Stop-Task -Task $task -Reason "entegratör plan dosyasını yazmadı ($plan)" }
            }
            "worker" {
                $branch = [string]$task.branch
                $outside = @(Get-TeamChangedFiles -RepoRoot $repoRoot -Branch $branch -Base $Base |
                    Where-Object { -not (Test-TeamPathInsideArea -Path $_ -Area @($task.area)) })
                if (@($outside).Count -gt 0) {
                    # Section 4: a worker never leaves its area. Not the inspector's to find.
                    $script:cycle.returned = [int]$script:cycle.returned + 1
                    $after = Get-TeamStateAfterInspection -Task $task -Verdict "RETURN"
                    Set-TeamProperty -InputObject $task -Name "returns" -Value $after.Returns
                    Set-TeamProperty -InputObject $task -Name "state" -Value $after.State
                    Set-TeamProperty -InputObject $task -Name "reason" -Value ("alan dışı dosya: " + (($outside | Select-Object -First 5) -join ", "))
                }
                else {
                    $sha = Invoke-TeamGit -WorkingDirectory $repoRoot -Arguments @("rev-parse", "refs/heads/$branch")
                    if ($sha.Success) { Set-TeamProperty -InputObject $task -Name "sha" -Value $sha.StdOut.Trim() }
                    Set-TeamProperty -InputObject $task -Name "state" -Value "inspecting"
                }
            }
            "inspector" {
                $verdict = Get-TeamVerdict -Report $done.Text
                $after = Get-TeamStateAfterInspection -Task $task -Verdict $verdict.Verdict
                Set-TeamProperty -InputObject $task -Name "returns" -Value $after.Returns
                $reason = if ($verdict.Detail) { $verdict.Detail } else { $after.Reason }
                if ($after.State -eq "merged") {
                    # The base first, then the task (Invoke-TaskMerge). A base the integration branch
                    # cannot take leaves the task approved and waiting: never stopped, never returned.
                    $merging = Invoke-TaskMerge -Task $task
                    if ($merging -isnot [bool]) { $freshMerge = [bool]$merging.Fresh }
                }
                else {
                    if ($after.State -eq "returned") { $script:cycle.returned = [int]$script:cycle.returned + 1 }
                    Set-TeamProperty -InputObject $task -Name "state" -Value $after.State
                    Set-TeamProperty -InputObject $task -Name "reason" -Value $reason
                }
            }
        }
        Set-TeamProperty -InputObject $task -Name "updated_at" -Value (Get-TeamTimestamp)
        # Written NOW, not when the other runs end: between this task's look at the store and
        # this write there is only what was just done (a merge: seconds), not the rest of what
        # is in flight (six seats: an hour). If the store refuses it all the same, the row is
        # theirs - and a merge that was made is said by name, for the lead to take back. Never
        # the strict write, even when this was the last run in flight: what follows a refusal
        # (the merge taken back, the copy put back) must not be skipped by a store that is away.
        $writtenNow = Save-QueueNow -What $id
        if ($script:staleIds.ContainsKey($id) -and [string]$task.state -eq "merged") {
            # Refused right after the merge: it is still the branch's last commit, and it is
            # taken back. (Not when the merge was "already there": that commit is not ours.)
            $undone = $false
            if ($freshMerge) { $undone = Undo-TeamMerge -RepoRoot $repoRoot -CycleId $dayId -Branch ([string]$task.branch) }
            Add-RefusedMergeNote -Task $task -Undone $undone
        }
        elseif (-not $writtenNow -and [string]$task.state -eq "merged") { [void]$script:unwrittenMerges.Add([pscustomobject]@{ Task = $task; Before = $beforeResult }) }
        # Refused: the copy says again what it said before this result.
        if ($script:staleIds.ContainsKey($id)) { Restore-TaskCopy -Task $task -Before $beforeResult }
        # A merge of an EARLIER run whose write had to wait and was refused in this save.
        Resolve-UnwrittenMerges
    }

    function Complete-PoolRun {
        <# A run of the pool is over: what it left is kept, and its result is applied. #>
        param($Started)
        $done = Complete-RoleRun -Started $Started
        switch ([string]$Started.Role) {
            "researcher" { Complete-Research -Started $Started -Done $done }
            "lead" {
                if ($null -ne $Started.Duty) { Complete-Duty -Started $Started -Done $done }
                else { Complete-Split -Started $Started -Done $done }
            }
            default { Complete-TaskRun -Started $Started -Done $done }
        }
    }

    # ------------------------------------------------------------ the loop (continuous-team-loop)
    # The owner, 2026-10-03: "Döngüyü kaldırabiliriz. Direkt bir sirkülasyon şeklinde getirebiliriz."
    # The four things the cycle boundary did are kept without it: new code is taken over by a
    # HANDOVER (the runs are adopted, not drained), the day's report folder switches at local
    # midnight, the researcher's hours are a timer inside the loop, and a dead loop is restarted
    # by the watchdog (tick.ps1 -Watchdog) - which reads the heartbeat in team/logs/loop.json.
    $researchEnabled = (-not $NoResearch) -or [bool]$ResearchOnly
    $hashCheckedAt = [datetime]::UtcNow

    function Test-LoopStays {
        <# -Continuous: nothing to do is no reason to end; a stop, a cap or the limit's stop is. #>
        return ([bool]$Continuous -and -not $script:capped -and -not $script:limitStop -and -not (Test-StopRequested))
    }

    function Test-HandoverRequested {
        <# team/handover.flag (the lead, or a release), or - in a continuous loop - the scripts this
           process runs changed on disk (looked at every 30 s at most). #>
        if (Test-Path -LiteralPath $script:handoverFlagPath) { return $true }
        if (-not $Continuous -or ([datetime]::UtcNow - $script:hashCheckedAt).TotalSeconds -lt 30) { return $false }
        $script:hashCheckedAt = [datetime]::UtcNow
        try { return ((Get-TeamScriptsHash -RepoRoot $repoRoot) -cne $script:scriptsHash) } catch { return $false }
    }

    function Switch-LoopDay {
        <# Local midnight with -DailyId: the old day's report is written, and the day's id, its
           report folder and its integration branch become the new day's. Nothing restarts; the
           runs in flight go on and are recorded in the new day's report. #>
        if (-not $followDay) { return }
        $today = "d" + (Get-LoopLocalNow).ToString("yyyyMMdd", [System.Globalization.CultureInfo]::InvariantCulture)
        if ($today -ceq $script:dayId) { return }
        $old = $script:dayId
        Add-CycleNote -List "gaps" -Text "gün değişti: $old -> $today; uçuştaki koşular sürdü, kayıtları yeni günün raporunda"
        [void](Save-Report)
        $script:dayId = $today
        $script:cycleDir = Join-Path $reportsRoot $today
        if (-not (Test-Path -LiteralPath $script:cycleDir)) { [void](New-Item -ItemType Directory -Force -Path $script:cycleDir) }
        foreach ($list in @("runs", "stops", "risks", "gaps")) { $script:cycle.$list = @() }
        $script:cycle.cycle_id = $today
        $script:cycle.started_at = (Get-TeamTimestamp)
        $script:cycle.spent_usd = 0.0
        $script:cycle.conflicts = 0
        $script:cycle.returned = 0
        Add-CycleNote -List "gaps" -Text "gün başladı: $old gününden süren döngü ($($script:loopId))"
        Write-CycleStatus
    }

    function Update-LoopTimers {
        <# The researcher every -ResearchEveryHours, inside one loop (0: once, at its start). #>
        if (-not $Continuous -or -not $researchEnabled -or $ResearchEveryHours -le 0 -or $script:researchPending) { return }
        if (@($script:pool | Where-Object { $_.Role -eq "researcher" }).Count -gt 0) { return }
        $last = $null
        if (Test-Path -LiteralPath $researchMarker) { $last = ConvertFrom-TeamTimestamp -Text ([System.IO.File]::ReadAllText($researchMarker).Trim()) }
        if ($null -ne $last -and ([datetime]::UtcNow - $last).TotalHours -lt $ResearchEveryHours) { return }
        $script:researchPending = $true
        $script:ownTries["cycle/researcher"] = 0
    }

    function Update-LockLease {
        <# A lock older than six hours is anybody's: a loop that lives for days renews its own every
           thirty minutes (file mode: rewritten; the store: taken again by its own machine). #>
        if (([datetime]::UtcNow - $script:lockRefreshedAt).TotalMinutes -lt 30) { return }
        $script:lockRefreshedAt = [datetime]::UtcNow
        try {
            if ($useApi) {
                $again = Set-TeamLockApi -Store $apiStore -Machine $Machine -CycleId $script:dayId -TakeoverDead $true
                if ([bool]$again.acquired) { $script:lockCycleId = $script:dayId }
                else { Add-CycleNote -List "risks" -Text "kilit yenilenemedi: $($again.holder) ($($again.since))" }
            }
            else {
                Write-TeamJson -Path $lockPath -Document (New-TeamLock -Machine $Machine -CycleId $script:dayId)
                $script:lockCycleId = $script:dayId
            }
        }
        catch {
            $note = "kilit yenilenemedi: $($_.Exception.Message)"
            if (@($script:cycle.risks) -notcontains $note) { Add-CycleNote -List "risks" -Text $note }
        }
    }

    Send-IdeaTexts
    Add-AdoptedRuns
    $refillDue = $true
    $nextRefill = [datetime]::UtcNow
    while ($true) {
        # The runs that are over, in the order they were started. Each is completed before the
        # next is looked at: two approved inspections that end in the same poll are merged one
        # after the other.
        foreach ($over in @($pool.ToArray() | Where-Object { Test-TeamRunOver -Run $_.Run -Deadline $_.Deadline })) {
            $pool.Remove($over)
            Complete-PoolRun -Started $over
            $refillDue = $true
        }
        # The limit was waited out - or somebody asked for a stop, which ends the wait.
        if ($null -ne $limitWaitUntil -and ([datetime]::UtcNow -ge $limitWaitUntil -or (Test-Path -LiteralPath $stopFlagPath))) {
            $limitWaitUntil = $null
            $usageLimit = [pscustomobject]@{ state = "ok"; resets_at = $null }
            Write-CycleStatus
            $refillDue = $true
        }
        Switch-LoopDay
        Update-LoopTimers
        Update-LockLease
        # New code: this loop stops dispatching and hands its live runs to its successor.
        if (Test-HandoverRequested) {
            Save-Handover
            $handingOver = $true
            break
        }
        # Nothing ended: the store and the settings are still looked at every -RefillSeconds.
        if ([datetime]::UtcNow -ge $nextRefill) { $refillDue = $true }
        if ($refillDue) {
            $refillDue = $false
            $nextRefill = [datetime]::UtcNow.AddSeconds($RefillSeconds)
            $pass = Start-PoolRuns
            # A start that failed is a change, not "nothing can be started": the seat it was given
            # is still free and the next of the queue takes it in the next refill, at once - with
            # nothing else in flight too (the first pool ended there, two assigned tasks never
            # run; the batch loop went on). It ends: every failed start stops its task, or is the
            # one try of the researcher or of a split.
            if ($pass.Failed -gt 0) { $refillDue = $true }
            if ($pass.Started -gt 0 -or $pass.Failed -gt 0) { $idlePasses = 0 }
            elseif (@($pool).Count -eq 0 -and $null -eq $limitWaitUntil) {
                # Nothing is in flight and this refill started nothing.
                if ($pass.Moved -and -not $capped) {
                    # It used to be a bare 'continue': a store that refused the same move after every
                    # read made thousands of requests a minute, past the stop flag and the caps, the
                    # lock held.
                    $idlePasses++
                    if ($idlePasses -ge 3) {
                        # Nothing is runnable in such a refill, so ending here loses no work - and it is said.
                        $why = if (@($staleIds.Keys).Count -gt 0) { "depo aynı yazmayı üst üste reddetti (" + ((@($staleIds.Keys) | Sort-Object) -join ", ") + ")" } else { "üst üste üç turda yalnız durum taşındı, hiçbir koşu başlamadı" }
                        $idleNote = $why + $(if ($Continuous) { "; akış bir sonraki tura kadar bekledi" } else { "; döngünün iş turu burada bitti" })
                        if (@($cycle.risks) -notcontains $idleNote) { Add-CycleNote -List "risks" -Text $idleNote }
                        # A continuous loop waits for the next refill instead (the store may move).
                        if (-not (Test-LoopStays)) { break }
                        $idlePasses = 0
                    }
                    else {
                        if (Test-CapReached) { break }
                        # What moved may have made something runnable: looked at again at once.
                        $refillDue = $true
                        continue
                    }
                }
                else {
                    # Every run that could start waits for a model: the limit is waited out (a known
                    # reset), or the cycle stops with the line (nobody said when).
                    $waits = (-not $capped -and $pass.Blocked -gt 0 -and (Set-LimitWait -ResetsAt ([string]$pass.BlockedReset)))
                    # The end: nothing is in flight and nothing can be started - unless this is the
                    # loop, which waits for the next refill.
                    if (-not $waits -and -not (Test-LoopStays)) { break }
                }
            }
        }
        # A continuous loop asked to stop ends once its runs have ended.
        if ($Continuous -and @($pool).Count -eq 0 -and -not (Test-LoopStays)) { break }
        # Only the limit is waited for, and this process has had its hours: the next start waits.
        if (@($pool).Count -eq 0 -and $null -ne $limitWaitUntil -and $MaxHours -gt 0 -and ([datetime]::UtcNow - $started).TotalHours -ge $MaxHours) {
            [void](Test-CapReached)
            break
        }
        # The heartbeat: a status nobody refreshed for ten minutes reads as "no cycle".
        if (([datetime]::UtcNow - $statusWrittenAt).TotalSeconds -ge $statusTickSeconds) { Write-CycleStatus }
        Start-Sleep -Milliseconds $(if (@($pool).Count -eq 0) { [Math]::Max($PollMilliseconds, 1000) } else { $PollMilliseconds })
    }
    # What a store that was away could not take while runs were in flight: nothing is in flight
    # now, and this write is the strict one.
    Save-Queue -Document $queue
    Resolve-UnwrittenMerges

    # A flag that came up when nothing was left to stop is still the owner's word: say it, remove it.
    [void](Test-StopRequested)
    if (Test-Path -LiteralPath $stopFlagPath) { Remove-Item -LiteralPath $stopFlagPath -Force -ErrorAction SilentlyContinue }
    $merged = @(Get-TeamTasks -Queue $queue | Where-Object { $_.state -eq "merged" })
    if (@($merged).Count -gt 0) {
        Add-CycleNote -List "gaps" -Text "integrate/$dayId üzerinde tam kapı ve main'e birleştirme bu betikte yok; lead yapar, sonra işler 'awaiting_release' olur"
    }
    $path = Save-Report
    Write-Host "cycle $dayId ended; report: $path"
}
finally {
    # Nothing is in flight any more, whatever ended the cycle: a run still working when the
    # cycle dies (an error in the middle of the pool) is killed - its worktree must not be
    # written to after the lock is released. Its task is taken up again by the next cycle.
    # On a handover they are NOT killed: they are the successor's (continuous-team-loop).
    if (-not $handingOver) {
        foreach ($orphan in @($pool.ToArray())) {
            try { if ($null -ne $orphan.Run.Process -and -not $orphan.Run.Process.HasExited) { Stop-TeamProcessTree -ProcessId $orphan.Run.Process.Id } } catch { }
        }
    }
    $pool.Clear()
    $liveRuns.Clear()
    Write-CycleStatus
    if ($useApi) {
        try { Clear-TeamLockApi -Store $apiStore -Machine $Machine -CycleId $script:lockCycleId }
        catch { Write-Host "the lock was not released in the queue store: $($_.Exception.Message)" }
    }
    else { Write-TeamJson -Path $lockPath -Document (New-TeamLockReleased) }
    $loopEnded = $true
    Write-LoopFile -Running $false
}
if ($handingOver) {
    # The successor: this very script, as it is on disk NOW (the new code), with the arguments
    # this process was given. It waits for this process to end, takes the lock and adopts the runs.
    Remove-Item -LiteralPath $handoverFlagPath -Force -ErrorAction SilentlyContinue
    $words = New-Object System.Collections.ArrayList
    foreach ($name in @($PSBoundParameters.Keys)) { [void]$words.Add("-" + $name + ":" + (ConvertTo-LoopLiteral -Value $PSBoundParameters[$name])) }
    $command = "& " + (ConvertTo-LoopLiteral -Value $PSCommandPath) + " " + ($words.ToArray() -join " ") + "; exit `$LASTEXITCODE"
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($command))
    $shell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    try {
        # Hidden and through the shell: the successor inherits no handle of this process.
        $successor = Start-Process -FilePath $shell -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", $encoded) -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
        [System.IO.File]::WriteAllText((Join-Path $logsDir "loop-successor.txt"), [string]$successor.Id)
        Write-Host "handed over to pid $($successor.Id): its runs were left running"
    }
    catch { Write-Host "the successor could not be started ($($_.Exception.Message)): the watchdog starts one; the handover file waits for it" }
}
exit 0
