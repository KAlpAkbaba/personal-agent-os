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

    It never waits for a human. It ends when nothing in the queue can run - every task is at
    a gate, done or stopped - or when a cap is reached, and it writes
    `team/reports/<cycle-id>.md` in Turkish either way.

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
    cycles). The inspector is never started on a model weaker than the one the worker's run
    really used: when every model at least that strong is limited, the inspection waits.

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

.PARAMETER WaitForUsageLimit
    The one stop the team has is the subscription's usage limit (Max). When a run hits it
    the task goes back to where it was, and with this switch (the default) the cycle WAITS
    until the limit lifts and carries on from there; without it, or when the tool did not
    say when, the cycle stops and says so in the report - the next cycle with the same
    -CycleId continues where it left off.

.PARAMETER ClaudePrefixArguments
    Arguments placed before the ones this script builds. The tests use it to put a fake
    in place of the model: -ClaudePath powershell.exe -ClaudePrefixArguments -File,fake.ps1

.EXAMPLE
    .\scripts\team\cycle.ps1 -CycleId pilot-01 -Research
#>
[CmdletBinding()]
param(
    [string]$CycleId = "",
    [string]$TeamRoot = "",
    [int]$MaxParallel = 2,
    [double]$MaxUsd = 0,
    [double]$RunMaxUsd = 0,
    [double]$RunMinutes = 0,
    [int]$CycleMinutes = 0,
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
    [switch]$Research,
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
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")

if (-not $TeamRoot) { $TeamRoot = Join-Path $repoRoot "team" }
if (-not $CycleId) { $CycleId = $(if ($DailyId) { "d" + (Get-Date).ToString("yyyyMMdd") } else { "c" + (Get-Date).ToString("yyyyMMdd-HHmm") }) }
if ($CycleId -cnotmatch '^[a-z0-9][a-z0-9.-]{0,40}$') { throw "a cycle id is lower-case letters, digits, '.' and '-': '$CycleId'" }
if ($MaxParallel -lt 1) { throw "-MaxParallel is at least 1" }
# A parameter is never assigned over (provision.tests.ps1 holds every script to it).
$runResearch = [bool]$Research -or [bool]$ResearchOnly
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
$cycleDir = Join-Path $reportsRoot $CycleId
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
    foreach ($id in @(Save-TeamQueueApi -Store $apiStore -Queue $Document -SkipStale)) {
        # Theirs until the store is read again: nothing is started for it on the copy we have.
        $script:staleIds[[string]$id] = $true
        $note = "${id}: depoda başkası değiştirdi; döngünün yazdığı bırakıldı, depodaki hali geçerli"
        if (@($script:cycle.risks) -notcontains $note) { Add-CycleNote -List "risks" -Text $note }
    }
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

function Sync-Queue {
    <# The store is the truth and a cycle lives for hours: the owner decides in the Onay Merkezi,
       the lead adds a card, the feeder cuts the roadmap - while this process runs. Read once at
       the start, none of it was seen until the NEXT cycle, and a cycle that has work does not
       end (2026-10-02: the owner's fourth-seat card, stored nine minutes after the cycle began,
       waited four hours beside idle seats). Before each pass the cycle's own changes are
       written and the queue is read again. A store that does not answer, or a queue that
       breaks the protocol now, changes nothing: the pass runs on the copy the cycle has, and
       the report says so once. With files there is one writer, the lock's holder. #>
    if (-not $useApi) { return }
    Save-Queue -Document $script:queue
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
    Set-TeamQueueBaseline -Store $apiStore -Queue $fresh
    $script:queue = $fresh
    # Every task is the store's version again: nothing is "theirs" any more.
    $script:staleIds.Clear()
}

$queue = if ($useApi) { Read-TeamQueueApi -Store $apiStore } else { Read-TeamJson -Path $queuePath }
$problems = @(Test-TeamQueue -Queue $queue)
if (@($problems).Count -gt 0) {
    Write-Host "the queue breaks the protocol; nothing was run:"
    foreach ($problem in $problems) { Write-Host "  - $problem" }
    exit 2
}

$started = [datetime]::UtcNow
$cycle = [pscustomobject]@{
    cycle_id   = $CycleId
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
# /v1/team/queue/status. A status nobody refreshed for ten minutes reads as "no cycle", so a long
# wait refreshes it every $statusTickSeconds seconds. A write that fails is a line under the risks,
# once, and never a reason to stop the cycle.
$statusPath = Join-Path $TeamRoot "status.json"
$stopFlagPath = Join-Path $TeamRoot "stop.flag"
$statusTickSeconds = 120
# A test hook (the heartbeat is otherwise only visible after two minutes): a whole number of seconds.
if ([string]$env:PAGENTOS_CYCLE_STATUS_TICK_SECONDS -match '^[1-9]\d{0,3}$') { $statusTickSeconds = [int]$env:PAGENTOS_CYCLE_STATUS_TICK_SECONDS }
$liveRuns = New-Object System.Collections.ArrayList
$usageLimit = [pscustomobject]@{ state = "ok"; resets_at = $null }
$statusFailed = $false
$stopNoted = $false
# The store could not be read again before a pass (Sync-Queue): said once in the report.
$syncNoted = $false
# The tasks whose write the store refused (somebody else wrote them) since the queue was last
# read: nothing is started for them, and a run's result is not applied, until it is read again.
$staleIds = @{}
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
# status in the form it knows, for the rest of the cycle.
$statusLegacy = $false
# The usage limit ended the cycle: nothing further starts.
$limitStop = $false

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

function New-CycleStatus {
    param([bool]$Legacy = $false)
    $document = [ordered]@{
        cycle_id      = $CycleId
        machine       = $Machine
        pid           = $PID
        started_at    = [string]$script:cycle.started_at
        runs          = @($script:liveRuns | ForEach-Object {
                $entry = [ordered]@{ task = $_.task; role = $_.role; started_at = $_.started_at }
                if (-not $Legacy) { $entry["model"] = $_.model }
                $entry
            })
        estimated_usd = [Math]::Round([double]$script:cycle.spent_usd, 4)
        usage_limit   = [ordered]@{ state = $script:usageLimit.state; resets_at = $script:usageLimit.resets_at }
    }
    if (-not $Legacy) { $document["limits"] = (Get-LimitsDocument) }
    $document["updated_at"] = (Get-TeamTimestamp)
    return $document
}

function Write-CycleStatus {
    try {
        if ($useApi) {
            try { Save-TeamStatusApi -Store $apiStore -Status (New-CycleStatus -Legacy $script:statusLegacy) }
            catch {
                if ($script:statusLegacy -or $_.Exception.Message -notmatch '^HTTP 422 ') { throw }
                $script:statusLegacy = $true
                Add-CycleNote -List "risks" -Text "Cloud Core canlı durumun model ve limit alanlarını henüz tanımıyor (422); eski biçimde yazıldı - Ofis sayfasında model ve limit görünmez (model-policy-api yayınlanınca düzelir)"
                Save-TeamStatusApi -Store $apiStore -Status (New-CycleStatus -Legacy $true)
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
    $text = New-TeamCycleReport -CycleId $CycleId -Queue $script:queue -Cycle $script:cycle
    $path = Join-Path $reportsRoot "$CycleId.md"
    [System.IO.File]::WriteAllText($path, $text + "`n", (New-Object System.Text.UTF8Encoding($false)))
    if ($useApi) {
        # The file above is the report; the store keeps its text so the Onay Merkezi on the Cloud
        # Core can show it. A post that fails does not lose the report.
        try { Send-TeamReportApi -Store $apiStore -Name "$CycleId.md" -Text $text }
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
       must wait. The inspector's floor is the model the worker's run of that task really used,
       and the configured worker model when its entry does not say (Get-TeamInspectionFloor). #>
    param([string]$Role, $Task = $null)
    $configured = [string](Get-TeamProperty -InputObject $script:modelSetting.roles -Name $Role -Default "")
    if (-not $configured) { $configured = [string]$script:modelSetting.roles.worker }
    $floor = ""
    if ($Role -eq "inspector" -and $null -ne $Task) { $floor = [string](Get-TeamInspectionFloor -Task $Task -Setting $script:modelSetting).Model }
    return (Get-TeamRunModel -Configured $configured -Limited $script:limitedModels -Fallback ([bool]$script:modelSetting.fallback) -Floor $floor)
}

# ------------------------------------------------------------------ the lock

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
    $taken = Set-TeamLockApi -Store $apiStore -Machine $Machine -CycleId $CycleId -TakeoverDead ($decision.Kind -eq "dead")
    if (-not [bool]$taken.acquired) {
        # The other machine took it between our read and our write.
        Add-CycleNote -List "stops" -Text "kilit $($taken.holder) makinesinde ($($taken.since)); bu döngü hiçbir şey çalıştırmadı"
        $path = Save-Report
        Write-Host "the lock was taken by $($taken.holder); report: $path"
        exit 3
    }
}
else { Write-TeamJson -Path $lockPath -Document (New-TeamLock -Machine $Machine -CycleId $CycleId -Now $started) }

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
        return $false
    }

    function Start-RoleRun {
        param(
            $Task, [string]$Role, [string]$WorkingDirectory, [string]$Prompt = "", [string[]]$ExcludeTools = @(),
            # What Select-RunModel answered for this run: the model to start on, and the one it
            # should have been (a run started below it is a lowering, and is written down).
            [Parameter(Mandatory = $true)]$Pick
        )
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
        $arguments = Get-TeamRunArguments -RoleFile $roleFile -MaxUsd $cap -Model $runModel -PrefixArguments $ClaudePrefixArguments -ExcludeTools $ExcludeTools
        if ($Prompt) { $prompt = $Prompt }
        elseif ($null -ne $Task) { $prompt = New-TeamTaskCard -Task $Task -Role $Role -CycleId $CycleId }
        else {
            $prompt = "# Run ($Role, cycle $CycleId)`n`nWork as your role file says. Write your proposals under team/proposals/. " +
            "Return your report as your final message, at most 40 lines, naming each file you wrote."
            $subjects = @($ResearchBrief | Where-Object { ([string]$_).Trim() })
            if (@($subjects).Count -gt 0) {
                $prompt += "`n`n## The subjects the lead asks for (one proposal each, at most three)`n"
                foreach ($subject in $subjects) { $prompt += "`n- " + ([string]$subject).Trim() }
            }
        }
        $run = Start-TeamRun -FilePath $ClaudePath -Arguments $arguments -Prompt $prompt -WorkingDirectory $WorkingDirectory
        $live = [pscustomobject]@{ task = $(if ($null -ne $Task) { [string]$Task.id } else { "cycle" }); role = $Role; started_at = (Get-TeamTimestamp); model = $runModel }
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
        }
    }

    function Complete-RoleRun {
        param($Started)
        $finished = Wait-TeamRun -Run $Started.Run -Deadline $Started.Deadline -OnTick { Write-CycleStatus } -TickSeconds $statusTickSeconds
        $result = Read-TeamRunResult -StdOut $finished.StdOut -ExitCode $finished.ExitCode -StdErr $finished.StdErr -Model ([string]$Started.Model)
        $taskId = if ($null -ne $Started.Task) { [string]$Started.Task.id } else { "cycle" }
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
        $relative = "team/reports/$CycleId/$taskId-$($Started.Role)-$number"
        return [pscustomobject]@{
            Ok = ($result.Ok -and -not $finished.TimedOut); Text = $result.Text; Outcome = $outcome
            File = $(if ($result.Text) { "$relative.md" } else { "$relative.json" }); CostUsd = $result.CostUsd
            UsageLimited = [bool]$result.UsageLimited; ResetsAt = [string]$result.ResetsAt
            LimitScope = [string]$result.LimitScope; LimitedModel = [string]$result.LimitedModel; LimitType = [string]$result.LimitType
            Model = [string]$Started.Model; RanModel = $ranModel; Substituted = [bool]$result.Substituted
        }
    }

    function Invoke-RoleRun {
        <#
        .SYNOPSIS
            One run of a role that the cycle waits for by itself (the researcher, the lead's
            split), with the model policy: started on the model its role is set to or - when
            that one is limited and fallback is on - on the next one down; a run that comes
            back with the usage limit is started again at once one model down, and when no
            model is left the existing wait applies. $null when no run could be started.
        #>
        param($Task, [string]$Role, [string]$WorkingDirectory, [string]$Prompt = "", [string[]]$ExcludeTools = @())
        $done = $null
        # Three models and one retry after a wait: a bound, so a tool that says "limited" for
        # ever cannot keep the cycle here.
        for ($attempt = 0; $attempt -lt 5; $attempt++) {
            $pick = Select-RunModel -Role $Role -Task $Task
            if ($null -eq $pick.Model) {
                if (-not (Wait-UsageLimit -ResetsAt ([string]$pick.ResetsAt))) { return $done }
                continue
            }
            $done = Complete-RoleRun -Started (Start-RoleRun -Task $Task -Role $Role -WorkingDirectory $WorkingDirectory -Prompt $Prompt -ExcludeTools $ExcludeTools -Pick $pick)
            if (-not $done.UsageLimited) { return $done }
            Register-Limit -Done $done -Key ($(if ($null -ne $Task) { [string]$Task.id } else { "cycle" }) + "/$Role")
            $again = Select-RunModel -Role $Role -Task $Task
            if ($null -ne $again.Model -and [string]$again.Model -ne [string]$done.Model) { continue }
            $reset = if ($null -eq $again.Model) { [string]$again.ResetsAt } else { [string]$done.ResetsAt }
            if (-not (Wait-UsageLimit -ResetsAt $reset)) { return $done }
        }
        return $done
    }

    function Wait-UsageLimit {
        <# The subscription's limit was hit. True when the cycle may go on (it waited it
           out); false when it must stop here and the next cycle continues. #>
        param([string]$ResetsAt)
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
        Add-CycleNote -List "risks" -Text ("Max kullanım limiti: {0} sıfırlanmasına kadar beklendi ({1:0} dk)" -f $ResetsAt, [Math]::Max(0, $wait.TotalMinutes))
        $script:usageLimit = [pscustomobject]@{ state = "waiting"; resets_at = $ResetsAt }
        Write-CycleStatus
        # In slices: the status is refreshed, and a stop flag ends the wait.
        while (($until - [datetime]::UtcNow).TotalSeconds -gt 0 -and -not (Test-Path -LiteralPath $stopFlagPath)) {
            Start-Sleep -Seconds ([int][Math]::Max(1, [Math]::Min($statusTickSeconds, ($until - [datetime]::UtcNow).TotalSeconds)))
            Write-CycleStatus
        }
        $script:usageLimit = [pscustomobject]@{ state = "ok"; resets_at = $null }
        Write-CycleStatus
        return $true
    }

    function Add-TaskReport {
        param($Task, [string]$Role, $Done)
        $entry = [pscustomobject]@{
            cycle    = $CycleId
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

    # ---------------------------------------------------------------- the researcher
    if ($runResearch -and -not (Test-CapReached)) {
        $proposals = Join-Path $TeamRoot "proposals"
        if (-not (Test-Path -LiteralPath $proposals)) { [void](New-Item -ItemType Directory -Force -Path $proposals) }
        $done = Invoke-RoleRun -Task $null -Role "researcher" -WorkingDirectory $repoRoot
        # $null: no model was open and the wait was refused - the stop line is already written.
        if ($null -eq $done) { }
        elseif (-not $done.Ok) { Add-CycleNote -List "stops" -Text "araştırmacı: $($done.Outcome)" }
        else { [System.IO.File]::WriteAllText($researchMarker, (Get-TeamTimestamp), (New-Object System.Text.UTF8Encoding($false))) }
        $known =@(Get-TeamTasks -Queue $queue | ForEach-Object { [string](Get-TeamProperty -InputObject $_ -Name "proposal" -Default "") })
        $newIdeas = New-Object System.Collections.ArrayList
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
            Set-TeamProperty -InputObject $queue -Name "tasks" -Value @(@(Get-TeamTasks -Queue $queue) + $task)
            [void]$newIdeas.Add($file)
        }
        Save-Queue -Document $queue
        # The idea's text goes where the queue is kept (ADR-0236): the Onay Merkezi's "Detay"
        # shows it. Two ideas waited for the owner with no text on 2026-10-02 - nothing posted it.
        if ($useApi) {
            foreach ($file in $newIdeas) {
                try { Send-TeamProposalApi -Store $apiStore -Name $file.Name -Text ([System.IO.File]::ReadAllText($file.FullName, [System.Text.Encoding]::UTF8)) }
                catch { Add-CycleNote -List "risks" -Text ("fikrin metni depoya yazılamadı ($($file.Name)): " + (([string]$_.Exception.Message) -replace '\s+', ' ')) }
            }
        }
    }

    # ---------------------------------------------------------------- the lead's split
    # A proposal that serves a roadmap row is approved in advance (TEAM_PROTOCOL 3a) but has no
    # area yet. ONE fresh lead run per proposal writes the split; THIS script judges it
    # (Test-TeamSplit) and takes it whole or refuses it whole. A refused split leaves the
    # proposal where it was; the next cycle asks again.
    if (-not $ResearchOnly) {
        foreach ($proposal in @(Get-TeamSplitCandidates -Queue $queue)) {
            if (Test-CapReached) { break }
            $proposalId = [string]$proposal.id
            $splitRelative = "team/plans/$CycleId-split-$proposalId.json"
            $splitPath = Join-Path $repoRoot ($splitRelative -replace '/', '\')
            # A file left by an earlier run is not this run's answer.
            if (Test-Path -LiteralPath $splitPath) { Remove-Item -LiteralPath $splitPath -Force }
            $splitFolder = Split-Path -Parent $splitPath
            if (-not (Test-Path -LiteralPath $splitFolder)) { [void](New-Item -ItemType Directory -Force -Path $splitFolder) }
            $card = New-TeamSplitCard -Task $proposal -Queue $queue -CycleId $CycleId -SplitFile $splitRelative
            # The lead's run follows the setting and the chain like any role's.
            $done = Invoke-RoleRun -Task $proposal -Role "lead" -WorkingDirectory $repoRoot -Prompt $card -ExcludeTools @("Bash", "Edit")
            if ($null -eq $done) { break }
            if (-not $done.Ok) {
                Add-CycleNote -List "risks" -Text "bölme koşusu: ${proposalId}: $($done.Outcome)"
                continue
            }
            $read = Read-TeamSplitFile -Path $splitPath
            $why = @()
            if (-not $read.Ok) { $why = @($read.Why) }
            else { $why = @(Test-TeamSplit -Split $read.Split -Queue $queue) }
            if (@($why).Count -eq 0) {
                $made = @(ConvertTo-TeamSplitTasks -Split $read.Split -Proposal $proposal -MaxUsd $RunMaxUsd)
                $trial = [pscustomobject]@{ version = 1; tasks = @(@(Get-TeamTasks -Queue $queue) + $made) }
                $why = @(Test-TeamQueue -Queue $trial)
                if (@($why).Count -eq 0) {
                    Set-TeamProperty -InputObject $queue -Name "tasks" -Value @(@(Get-TeamTasks -Queue $queue) + $made)
                    Set-TeamProperty -InputObject $proposal -Name "state" -Value "done"
                    Set-TeamProperty -InputObject $proposal -Name "reason" -Value ("bölündü: " + ((@($made) | ForEach-Object { [string]$_.id }) -join ", "))
                    Set-TeamProperty -InputObject $proposal -Name "updated_at" -Value (Get-TeamTimestamp)
                    Save-Queue -Document $queue
                    continue
                }
            }
            Add-CycleNote -List "risks" -Text ("bölme reddedildi: ${proposalId}: " + ((@($why) | ForEach-Object { ([string]$_) -replace '\s+', ' ' }) -join "; "))
        }
    }

    # ---------------------------------------------------------------- the tasks
    $capped = [bool]$ResearchOnly
    while (-not $capped) {
        Sync-Queue
        $runnable = New-Object System.Collections.ArrayList
        $moved = $false
        foreach ($task in (Get-TeamTasks -Queue $queue)) {
            # Its write was refused and the store could not be read again yet: it is not ours.
            if ($staleIds.ContainsKey([string]$task.id)) { continue }
            $next = Get-TeamNextRole -Task $task
            if ($next.Kind -ne "rest" -and $next.Kind -ne "gate") {
                # A task whose dependencies are not on main yet waits, and says so once.
                $unmet = @(Get-TeamUnmetDependencies -Task $task -Queue $queue)
                if (@($unmet).Count -gt 0) {
                    $waitNote = "bekliyor: $($task.id) -> $($unmet -join ', ') main'e girince"
                    if (@($script:cycle.gaps) -notcontains $waitNote) { Add-CycleNote -List "gaps" -Text $waitNote }
                    continue
                }
            }
            if ($next.Kind -eq "move" -and [string]$next.NextState -eq "assigned") {
                # Section 4: never into work beside a task that holds the same files. Two such
                # tasks make a queue Test-TeamQueue refuses - and every later cycle with it.
                $holders = @(Get-TeamAreaHolders -Task $task -Queue $queue)
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
                # Looked at again in THIS pass: an 'approved' task that just became 'assigned' is
                # runnable now. It used to wait a whole round - behind that round's integrators -
                # with its worker seat empty (the owner saw it on the Ofis page, 2026-10-01).
                $next = Get-TeamNextRole -Task $task
                if ($next.Kind -eq "move") { continue }
            }
            if ($next.Kind -ne "run") { continue }
            $id = [string]$task.id
            if (-not $runCount.ContainsKey($id)) { $runCount[$id] = 0 }
            if ($runCount[$id] -ge $MaxRunsPerTask) {
                Stop-Task -Task $task -Reason "bu döngüde $MaxRunsPerTask koşu yapıldı ve iş bitmedi"
                $moved = $true
                continue
            }
            [void]$runnable.Add([pscustomobject]@{ Task = $task; Next = $next })
        }
        if ($moved) {
            Save-Queue -Document $queue
            # A move the store refused: the task was written by somebody else between our read
            # and this write (the lead stopped it, the owner decided). It is not run on our copy.
            $ours = New-Object System.Collections.ArrayList
            foreach ($item in $runnable) { if (-not $staleIds.ContainsKey([string]$item.Task.id)) { [void]$ours.Add($item) } }
            $runnable = $ours
        }
        if (@($runnable).Count -eq 0) {
            if ($moved) { continue }
            break
        }
        if (Test-CapReached) { $capped = $true; break }

        $startedRuns = New-Object System.Collections.ArrayList
        # The earliest reset a task that could not be started is waiting for ("" = nobody said).
        $blockedReset = ""
        $blocked = 0
        foreach ($item in $runnable) {
            if (@($startedRuns).Count -ge $MaxParallel) { break }
            $task = $item.Task
            $role = [string]$item.Next.Role
            # The model first: a task whose every allowed model is limited starts nothing -
            # no worktree, no try spent - and the others of the queue go on.
            $pick = Select-RunModel -Role $role -Task $task
            if ($null -eq $pick.Model) {
                $blocked++
                if ($pick.ResetsAt -and (-not $blockedReset -or [string]::CompareOrdinal([string]$pick.ResetsAt, $blockedReset) -lt 0)) { $blockedReset = [string]$pick.ResetsAt }
                if ($role -eq "inspector") { Add-InspectionWaitNote -Task $task -Pick $pick }
                continue
            }
            $runCount[[string]$task.id] = $runCount[[string]$task.id] + 1
            $where = $repoRoot
            try {
                if ($role -eq "worker" -or $role -eq "inspector") {
                    $branch = [string](Get-TeamProperty -InputObject $task -Name "branch" -Default "")
                    if (-not $branch) {
                        $slug = ([string]$task.id)
                        $branch = Get-TeamBranchName -CycleId $CycleId -Role "worker" -Slug $slug
                        Set-TeamProperty -InputObject $task -Name "branch" -Value $branch
                    }
                    $tree = New-TeamWorktree -RepoRoot $repoRoot -Branch $branch -Base $Base
                    Set-TeamProperty -InputObject $task -Name "worktree" -Value (".claude/worktrees/" + $branch)
                    $where = $tree.Path
                }
                if ($role -eq "worker") { Set-TeamProperty -InputObject $task -Name "state" -Value "in_progress" }
                Set-TeamProperty -InputObject $task -Name "assignee" -Value $role
                [void]$startedRuns.Add((Start-RoleRun -Task $task -Role $role -WorkingDirectory $where -Pick $pick))
            }
            catch {
                Stop-Task -Task $task -Reason "koşu başlatılamadı: $($_.Exception.Message)"
            }
        }
        Save-Queue -Document $queue
        if (@($startedRuns).Count -eq 0 -and $blocked -gt 0) {
            # Every task that could run waits for a model: the existing wait (known reset:
            # wait it out; unknown: stop with the line), then the queue is looked at again.
            if (-not (Wait-UsageLimit -ResetsAt $blockedReset)) { $capped = $true }
            continue
        }

        $limitHit = ""
        $limitSeen = $false
        # By index, not foreach: a run that comes back with the usage limit is started again
        # at once one model down, and joins the end of this same list.
        for ($runIndex = 0; $runIndex -lt @($startedRuns).Count; $runIndex++) {
            $startedRun = $startedRuns[$runIndex]
            $task = $startedRun.Task
            $role = [string]$startedRun.Role
            $done = Complete-RoleRun -Started $startedRun
            if (Test-TaskMovedInStore -Task $task) {
                # Their word stands for the RUN too, not only for the row: no merge, no state, no
                # report entry (the report is in its file). What the run said about a MODEL's
                # limit is still true. The next pass reads the store's version of the task.
                if ($done.UsageLimited) { Register-Limit -Done $done -Key "$($task.id)/$role" }
                $staleIds[[string]$task.id] = $true
                Add-CycleNote -List "risks" -Text "$($task.id): koşu ($role) sürerken depoda başkası değiştirdi; koşunun sonucu uygulanmadı (raporu: $($done.File))"
                continue
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
                    Add-CycleNote -List "risks" -Text "$($task.id): hüküm alınmadı - araç denetimi $($done.Model) yerine $($done.RanModel) ile koşturdu; bu, işçinin modelinden ($($floor.Model)) zayıf"
                }
            }
            Add-TaskReport -Task $task -Role $role -Done $done

            if ($done.UsageLimited) {
                # Not the task's failure and not a try spent (owner decision 2026-09-30).
                Register-Limit -Done $done -Key "$($task.id)/$role"
                $runCount[[string]$task.id] = [Math]::Max(0, $runCount[[string]$task.id] - 1)
                # The fallback chain (ADR-0214 addendum 7): the SAME task, at once, on the next
                # model down - when the setting allows it, a model is open, and nobody asked the
                # cycle to stop. The inspector is never lowered below the worker's model.
                $again = Select-RunModel -Role $role -Task $task
                if ($null -ne $again.Model -and [string]$again.Model -ne [string]$done.Model -and -not (Test-StopRequested)) {
                    try {
                        [void]$startedRuns.Add((Start-RoleRun -Task $task -Role $role -WorkingDirectory $startedRun.Where -Prompt $startedRun.Prompt -ExcludeTools $startedRun.ExcludeTools -Pick $again))
                        $runCount[[string]$task.id] = $runCount[[string]$task.id] + 1
                        Set-TeamProperty -InputObject $task -Name "updated_at" -Value (Get-TeamTimestamp)
                        continue
                    }
                    catch { Add-CycleNote -List "risks" -Text "$($task.id): bir alt modelde yeniden başlatılamadı: $($_.Exception.Message)" }
                }
                # No model to go down to (or fallback is off): the task goes back to where it
                # was and is taken up again when the limit lifts.
                if ($role -eq "inspector" -and $null -eq $again.Model) { Add-InspectionWaitNote -Task $task -Pick $again }
                $limitSeen = $true
                $waitFor = if ($null -eq $again.Model) { [string]$again.ResetsAt } else { [string]$done.ResetsAt }
                if ($waitFor) { $limitHit = $waitFor }
                if ($role -eq "worker") { Set-TeamProperty -InputObject $task -Name "state" -Value "assigned" }
                Set-TeamProperty -InputObject $task -Name "updated_at" -Value (Get-TeamTimestamp)
                continue
            }
            if (-not $done.Ok) {
                $failures = [int](Get-TeamProperty -InputObject $task -Name "failed_runs" -Default 0) + 1
                Set-TeamProperty -InputObject $task -Name "failed_runs" -Value $failures
                if ($failures -ge 2) { Stop-Task -Task $task -Reason "iki koşu sonuç vermedi ($($done.Outcome))" }
                elseif ($role -eq "worker") { Set-TeamProperty -InputObject $task -Name "state" -Value "assigned" }
                continue
            }
            switch ($role) {
                "integrator" {
                    $plan = "team/plans/$($task.id)-integration.md"
                    if (Test-Path -LiteralPath (Join-Path $repoRoot ($plan -replace '/', '\'))) {
                        Set-TeamProperty -InputObject $task -Name "plan" -Value $plan
                        # With its plan an approved task is the pass's to move into work - beside
                        # nobody that holds its files (the same rule as every other approved task).
                        if (@(Get-TeamAreaHolders -Task $task -Queue $queue).Count -eq 0) { Set-TeamProperty -InputObject $task -Name "state" -Value "assigned" }
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
                        $merge = Merge-TeamBranch -RepoRoot $repoRoot -CycleId $CycleId -Branch ([string]$task.branch) -Base $Base
                        if ($merge.Merged) {
                            Set-TeamProperty -InputObject $task -Name "state" -Value "merged"
                            Set-TeamProperty -InputObject $task -Name "integration_branch" -Value $merge.Integration
                        }
                        else {
                            $script:cycle.conflicts = [int]$script:cycle.conflicts + 1
                            $script:cycle.returned = [int]$script:cycle.returned + 1
                            $back = Get-TeamStateAfterInspection -Task $task -Verdict "RETURN"
                            Set-TeamProperty -InputObject $task -Name "returns" -Value $back.Returns
                            Set-TeamProperty -InputObject $task -Name "state" -Value $back.State
                            Set-TeamProperty -InputObject $task -Name "reason" -Value "entegrasyon dalında çakışma"
                        }
                    }
                    else {
                        if ($after.State -eq "returned") { $script:cycle.returned = [int]$script:cycle.returned + 1 }
                        Set-TeamProperty -InputObject $task -Name "state" -Value $after.State
                        Set-TeamProperty -InputObject $task -Name "reason" -Value $reason
                    }
                }
            }
            Set-TeamProperty -InputObject $task -Name "updated_at" -Value (Get-TeamTimestamp)
        }
        Save-Queue -Document $queue
        if ($limitSeen -and -not (Wait-UsageLimit -ResetsAt $limitHit)) { $capped = $true }
    }

    # A flag that came up when nothing was left to stop is still the owner's word: say it, remove it.
    [void](Test-StopRequested)
    if (Test-Path -LiteralPath $stopFlagPath) { Remove-Item -LiteralPath $stopFlagPath -Force -ErrorAction SilentlyContinue }
    $merged = @(Get-TeamTasks -Queue $queue | Where-Object { $_.state -eq "merged" })
    if (@($merged).Count -gt 0) {
        Add-CycleNote -List "gaps" -Text "integrate/$CycleId üzerinde tam kapı ve main'e birleştirme bu betikte yok; lead yapar, sonra işler 'awaiting_release' olur"
    }
    $path = Save-Report
    Write-Host "cycle $CycleId ended; report: $path"
}
finally {
    # Nothing is in flight any more, whatever ended the cycle.
    $liveRuns.Clear()
    Write-CycleStatus
    if ($useApi) {
        try { Clear-TeamLockApi -Store $apiStore -Machine $Machine -CycleId $CycleId }
        catch { Write-Host "the lock was not released in the queue store: $($_.Exception.Message)" }
    }
    else { Write-TeamJson -Path $lockPath -Document (New-TeamLockReleased) }
}
exit 0
