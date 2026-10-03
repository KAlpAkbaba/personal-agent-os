<#
.SYNOPSIS
    Keep the worker seats full (ADR-0214 addendum 8): when the queue has fewer runnable tasks
    than there are seats, ONE lead run cuts the roadmap's next items into cards, and this
    script judges what it wrote.

.DESCRIPTION
    The step the scheduled task runs BEFORE `cycle.ps1`. Owner, 2026-10-01: "hiçbir ajan
    mümkün olduğunca durmasın, mümkün olduğunca roadmap'ten ilerleyelim ve araştırmacının
    yeni fikirleri onaylanırsa bu fikirler roadmap'e eklensin."

      1. It counts the RUNNABLE tasks of the team store (the files under -TeamRoot, or the
         Cloud Core's queue with -QueueUrl - the same functions as the cycle): approved,
         assigned, returned, in_progress or inspecting, with every dependency on main. With
         -MinRunnable of them or more it does nothing, and says so.
      2. Otherwise ONE fresh `claude -p` run of the lead (its role file as the system prompt,
         no Bash, no agents; no Edit either unless there is an idea row to write) is asked for
         at most -MaxNew cards in ONE file, `team/plans/feed-<date>-<n>.json`. The prompt
         carries the roadmap's rows, its limits, its order and the queue as it is.
      3. THE SCRIPT judges the file, whole or nothing (Test-TeamFeed: the judge of the lead's
         split, plus the roadmap row, the count and the title). Accepted cards are queued as
         `approved` with the reason "roadmap: <row>; fed by the lead run <date>"; an item the
         lead marked `needs_owner` is queued as an idea `awaiting_owner` with a proposal
         file, never as a task. A refused file queues nothing; the reason is in the report.
      4. For every idea the owner approved that docs/ROADMAP.md does not name yet (a task
         that is done, has a proposal and a reason starting "sahip onayladı") the same run
         adds ONE row to the "Approved ideas" table. The script checks that the edit is that
         and nothing else, and commits docs/ROADMAP.md alone on the branch the checkout is
         on - never on main, never from a detached HEAD, and it never pushes.
      5. A run that changed any other file is refused whole: nothing is queued, nothing is
         committed, and docs/ROADMAP.md is put back byte for byte. (The other files are left
         as they are found and named in the report: in a checkout a person also works in,
         this script cannot know a stray write from that person's work.)

    It takes and releases the team lock as cycle `feed-<date>`, starts nothing while
    `team/stop.flag` is there (and leaves the flag for the cycle it was written for), runs
    the lead on the model `team/models.json` names for it, and treats the subscription's
    usage limit as the cycle does: wait it out when the tool says when it lifts, else stop.

    BESIDE A RUNNING CYCLE (API mode only, ADR-0214 addendum - feeder-own-lock): when the team
    lock is held by a LIVE cycle of THIS machine, the feeder does not stop and does not touch
    that lock. It takes its own (a machine-local file, -FeederLockPath; a second feeder that
    finds it held exits 3), runs the lead in a throwaway worktree of the checkout's HEAD (what
    the cycle writes into the checkout meanwhile is not the run's; what the run writes where it
    works is judged by rule 5 as ever), writes no idea row and commits nothing, reads the queue
    AGAIN before it writes and judges the feed against that, writes ONLY the new tasks - each as
    a conditional create; a 409 drops that card and the cards that depend on it - and writes its
    report to the file only (the Onay Merkezi keeps showing the cycle's). With the files (no
    -QueueUrl) there is one writer, the lock's holder: a live cycle's lock stops it, as before.

    It writes `team/reports/feed-<date>.md` in Turkish, one section per lead run it started.
    When nothing was started - the seats are full, the stop flag, a lock somebody holds - it
    says so on standard output and writes and posts NO report: the Onay Merkezi shows the
    newest report, and that must stay the cycle's. It does not release, merge, push, or
    write main.

.PARAMETER FeedDate
    The day, YYYY-MM-DD; today when empty. It names the lock's cycle, the feed file and the
    report.

.PARAMETER MinRunnable
    The number of worker seats: with this many runnable tasks or more nothing is cut.

.PARAMETER MaxNew
    The most cards (and the most owner items) one run may add.

.PARAMETER ClaudePrefixArguments
    Arguments placed before the ones this script builds. The tests use it to put a fake in
    place of the model: -ClaudePath powershell.exe -ClaudePrefixArguments -File,fake.ps1

.EXAMPLE
    .\scripts\team\feed.ps1 -DryRun
    .\scripts\team\feed.ps1 -QueueUrl http://100.90.158.26:8001 -QueueToken $env:LOCALAPPDATA\PagentOS\team-queue.token
#>
[CmdletBinding()]
param(
    [string]$FeedDate = "",
    [string]$TeamRoot = "",
    [int]$MinRunnable = 3,
    [int]$MaxNew = 3,
    # 0 is no cap, as in the cycle (owner decision 2026-09-30, ADR-0214 addendum 3).
    [double]$RunMaxUsd = 0,
    [double]$RunMinutes = 0,
    [bool]$WaitForUsageLimit = $true,
    # The longest reset the feeder waits for. It holds the team's lock while it waits, and no
    # cycle can start under that lock: on 2026-10-02 it waited for Fable's week - three days.
    [int]$MaxLimitWaitMinutes = 20,
    [string]$ClaudePath = (Join-Path $env:USERPROFILE ".local\bin\claude.exe"),
    [string[]]$ClaudePrefixArguments = @(),
    # The model when team/models.json does not name one for the lead.
    [string]$Model = "",
    [string]$Machine = $env:COMPUTERNAME,
    # Prints what it would do. Nothing is started, taken or written.
    [switch]$DryRun,
    # The Cloud Core's queue (ADR-0222), as in the cycle. -QueueToken is the PATH of the file
    # that holds the token, never the token.
    [string]$QueueUrl = "",
    [string]$QueueToken = "",
    # The feeder's own lock, taken beside a running cycle: a file outside the repository.
    # Empty is $env:LOCALAPPDATA\PagentOS\team-feeder.lock. The tests name their own.
    [string]$FeederLockPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamFeed.ps1")

# A parameter is never assigned over (provision.tests.ps1 holds every script to it).
$teamDir = if ($TeamRoot) { $TeamRoot } else { Join-Path $repoRoot "team" }
$day = if ($FeedDate) { $FeedDate } else { (Get-Date).ToString("yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture) }
if ($day -cnotmatch '^\d{4}-\d{2}-\d{2}$') { throw "-FeedDate is YYYY-MM-DD: '$day'" }
if ($MinRunnable -lt 1) { throw "-MinRunnable is at least 1" }
if ($MaxNew -lt 1) { throw "-MaxNew is at least 1" }
$feedId = "feed-$day"

$utf8 = New-Object System.Text.UTF8Encoding($false)
$queuePath = Join-Path $teamDir "queue.json"
$lockPath = Join-Path $teamDir "lock.json"
$stopFlagPath = Join-Path $teamDir "stop.flag"
$reportsRoot = Join-Path $teamDir "reports"
$reportPath = Join-Path $reportsRoot "$feedId.md"
$rawDir = Join-Path $reportsRoot $feedId
$roleFile = Join-Path $repoRoot ".claude\agents\lead.md"
$roadmapRelative = "docs/ROADMAP.md"
$roadmapPath = Join-Path $repoRoot "docs\ROADMAP.md"
if (-not (Test-Path -LiteralPath $roleFile)) { throw "there is no role file for the lead: $roleFile" }
if (-not (Test-Path -LiteralPath $roadmapPath)) { throw "there is no roadmap: $roadmapPath" }

# The lead's model (ADR-0214 addendum 7): team/models.json, else -Model, else the tool's own.
# The value goes onto a command line, so a name that is not a model name stops the script.
$leadModel = $Model
$modelsPath = Join-Path $teamDir "models.json"
if (Test-Path -LiteralPath $modelsPath) {
    $named = Get-TeamProperty -InputObject (Get-TeamProperty -InputObject (Read-TeamJson -Path $modelsPath) -Name "roles") -Name "lead"
    if ($null -ne $named -and ([string]$named).Trim()) { $leadModel = ([string]$named).Trim() }
}
if ($leadModel -and $leadModel -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$') { throw "'$leadModel' is not a model name (the lead's)" }

# What the cycles on this machine learnt about the limits (team/limits.json, the cycle's file;
# read only): a model that is limited now starts no feed run. A file that cannot be read is no
# knowledge - the run will say it again.
$limitedModels = @{}
$limitsPath = Join-Path $teamDir "limits.json"
if (Test-Path -LiteralPath $limitsPath) {
    try {
        $known = Get-TeamProperty -InputObject (Read-TeamJson -Path $limitsPath) -Name "models"
        if ($known -is [System.Management.Automation.PSCustomObject]) {
            foreach ($property in $known.PSObject.Properties) {
                $until = [string](Get-TeamProperty -InputObject $property.Value -Name "until" -Default "")
                if ((Test-TeamModelId -Model ([string]$property.Name)) -and $until) { $limitedModels[[string]$property.Name] = [pscustomobject]@{ until = $until } }
            }
        }
    }
    catch { $limitedModels = @{} }
}

$useApi = [bool]$QueueUrl
if ($useApi -and -not $QueueToken) { throw "-QueueUrl needs -QueueToken: the path of a file holding the token" }
$apiStore = $null
if ($useApi) { $apiStore = New-TeamApiStore -Url $QueueUrl -TokenFile $QueueToken }

$queue = if ($useApi) { Read-TeamQueueApi -Store $apiStore } else { Read-TeamJson -Path $queuePath }
$problems = @(Test-TeamQueue -Queue $queue)
if (@($problems).Count -gt 0) {
    Write-Host "the queue breaks the protocol; nothing was started:"
    foreach ($problem in $problems) { Write-Host "  - $problem" }
    exit 2
}

$started = [datetime]::UtcNow
$notes = New-Object System.Collections.ArrayList

function Add-FeedNote {
    <# One line of the report, and of what the script prints. #>
    param([string]$Text)
    [void]$script:notes.Add($Text)
    Write-Host "  $Text"
}

function Save-FeedReport {
    <# The day's report, one section per run that did something; the store keeps its text too. #>
    if (-not (Test-Path -LiteralPath $reportsRoot)) { [void](New-Item -ItemType Directory -Force -Path $reportsRoot) }
    $text = ""
    if (Test-Path -LiteralPath $reportPath) { $text = [System.IO.File]::ReadAllText($reportPath, [System.Text.Encoding]::UTF8) }
    if (-not $text.Trim()) { $text = "# Besleme raporu — $feedId`n" }
    $section = New-Object System.Collections.ArrayList
    [void]$section.Add("")
    [void]$section.Add("## $(Get-TeamTimestamp -Now $started) · $Machine")
    [void]$section.Add("")
    foreach ($note in @($script:notes.ToArray())) { [void]$section.Add("- $note") }
    $text = $text.TrimEnd() + "`n" + ((@($section.ToArray())) -join "`n") + "`n"
    [System.IO.File]::WriteAllText($reportPath, $text, $utf8)
    # Beside a running cycle the report is the file's only: the Onay Merkezi shows the newest
    # report, and that stays the cycle's. The cards themselves are in the queue.
    if ($useApi -and -not $script:lockFree) {
        try { Send-TeamReportApi -Store $apiStore -Name "$feedId.md" -Text $text }
        catch { Write-Host "the report was not posted to the queue store: $($_.Exception.Message)" }
    }
}

function Save-FeedQueue {
    param($Document)
    if ($useApi) { Save-TeamQueueApi -Store $apiStore -Queue $Document }
    else { Write-TeamJson -Path $queuePath -Document $Document }
}

Write-Host "feed ${feedId}:"

# ------------------------------------------------------------------ is there anything to do
if (Test-Path -LiteralPath $stopFlagPath) {
    # The flag is addressed to the cycle, which removes it; a feed run started under it would
    # be a new run after the owner said stop. No report either: a report is of a run, and the
    # Onay Merkezi shows the newest one - this line would stand in the place of the cycle's.
    Write-Host "  the owner/lead stopped the team (team/stop.flag): no lead run was started, the flag is left for the cycle"
    exit 0
}

$runnable = @(Get-TeamRunnableTasks -Queue $queue)
$runnableCount = @($runnable).Count
if ($runnableCount -ge $MinRunnable) {
    Write-Host "  $runnableCount runnable task(s), the seats are $MinRunnable - nothing to cut"
    exit 0
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
# A live cycle of THIS machine, and the store is the Cloud Core's: the feeder works beside it,
# under its own lock (the store makes every task write conditional). With the files there is
# one writer, the lock's holder; another machine's cycle is not serialised by a local lock.
# A live FEEDER of ours (cycle feed-<date>) is not a cycle: it never took the feeder's own lock,
# so a second lead run beside it would cut the same rows again - it stops as before.
$holderCycle = if ($null -ne $lock) { [string](Get-TeamProperty -InputObject $lock -Name "cycle_id" -Default "") } else { "" }
$lockFree = ($useApi -and $decision.Kind -eq "ours" -and $holderCycle -notlike "feed-*")
if (-not $decision.MayRun -and -not $lockFree) {
    # Said, not reported: the lock is a cycle's, every 30 minutes while it runs, and the report
    # the Onay Merkezi shows must stay that cycle's.
    Write-Host "  the lock is held by $($decision.Holder) ($($decision.Since)); the feeder started nothing"
    if ($DryRun) { exit 0 }
    exit 3
}
$ownLockPath = $FeederLockPath
if (-not $ownLockPath) {
    $localRoot = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { $env:TEMP }
    $ownLockPath = Join-Path $localRoot "PagentOS\team-feeder.lock"
}

# ------------------------------------------------------------------ what the run is asked for
$roadmapText = [System.IO.File]::ReadAllText($roadmapPath, [System.Text.Encoding]::UTF8)
$ideas = @(Get-TeamApprovedIdeasMissing -Queue $queue -RoadmapText $roadmapText)
$ideaBlock = ""
$branch = ""
if (@($ideas).Count -gt 0 -and $lockFree) {
    # A commit in the checkout a running cycle works from is not made without its lock: the next
    # feed between cycles asks for the row again.
    $ideaBlock = "döngü çalışıyor"
}
elseif (@($ideas).Count -gt 0) {
    # The row is committed on the branch the checkout is on: the lead's. Never main, never a
    # detached HEAD; and never over somebody's uncommitted edit of the roadmap.
    $head = Invoke-TeamGit -WorkingDirectory $repoRoot -Arguments @("rev-parse", "--abbrev-ref", "HEAD")
    if ($head.Success) { $branch = $head.StdOut.Trim() }
    if (-not $branch -or $branch -eq "HEAD" -or $branch -eq "main" -or $branch -match '(?i)hand-gestures') {
        $ideaBlock = "çalışma kopyası '$branch' üzerinde: main'e ve ayrık HEAD'e yazılmaz"
    }
    else {
        $dirty = Invoke-TeamGit -WorkingDirectory $repoRoot -Arguments @("status", "--porcelain", "--", $roadmapRelative)
        if (-not $dirty.Success) { $ideaBlock = "git status başarısız: $($dirty.StdErr.Trim())" }
        elseif ($dirty.StdOut.Trim()) { $ideaBlock = "$roadmapRelative içinde kaydedilmemiş değişiklik var" }
    }
}
$pendingIds = (@($ideas) | ForEach-Object { [string]$_.id }) -join ", "
$number = 1
while (Test-Path -LiteralPath (Join-Path $repoRoot "team\plans\$feedId-$number.json")) { $number++ }
$feedRelative = "team/plans/$feedId-$number.json"
$feedPath = Join-Path $repoRoot ($feedRelative -replace '/', '\')
$exclude = @("Bash")
if (@($ideas).Count -eq 0 -or $ideaBlock) { $exclude += "Edit" }

if ($DryRun) {
    Write-Host "  $runnableCount runnable task(s), the seats are $MinRunnable"
    if ($lockFree) {
        Write-Host "  the lock is held by a live cycle of this machine ($($decision.Holder), $($decision.Since)): the lock-free path -"
        Write-Host "  the feeder's own lock ($ownLockPath), the lead in a throwaway worktree, the queue read again, new tasks created one by one, the report to the file only; the cycle's lock is not taken"
    }
    Write-Host "  would start ONE lead run (model: $(if ($leadModel) { $leadModel } else { 'the tool default' }); without $($exclude -join ', ')) for at most $MaxNew card(s) in $feedRelative"
    if (@($ideas).Count -gt 0 -and -not $ideaBlock) { Write-Host "  would ask for the 'Approved ideas' row of: $pendingIds (committed on $branch)" }
    elseif (@($ideas).Count -gt 0) { Write-Host "  would NOT ask for the 'Approved ideas' row of: $pendingIds ($ideaBlock)" }
    Write-Host "  dry run: nothing was started, taken or written"
    exit 0
}

$ownLock = $null
if ($lockFree) {
    # The cycle's lock is never taken, released or written on this path.
    $ownLock = Enter-TeamFeederLock -Path $ownLockPath -Machine $Machine -Now $started
    if (-not $ownLock.Acquired) {
        # Said, not reported: another feeder of this machine is cutting cards right now.
        Write-Host "  the feeder's own lock is held ($($ownLock.Why)): another feeder is cutting cards; this one started nothing"
        exit 3
    }
}
elseif ($useApi) {
    $taken = Set-TeamLockApi -Store $apiStore -Machine $Machine -CycleId $feedId -TakeoverDead ($decision.Kind -eq "dead")
    if (-not [bool]$taken.acquired) {
        # The other machine took it between our read and our write. Said, not reported, as above.
        Write-Host "  the lock is held by $($taken.holder) ($($taken.since)); the feeder started nothing"
        exit 3
    }
}
else { Write-TeamJson -Path $lockPath -Document (New-TeamLock -Machine $Machine -CycleId $feedId -Now $started) }

$exitCode = 0
$runRoot = $repoRoot
$worktree = ""
try {
    Add-FeedNote -Text "çalıştırılabilir iş: $runnableCount (koltuk: $MinRunnable) - lead roadmap'ten kart kesecek"
    if ($decision.Kind -eq "stale") { Add-FeedNote -Text "bayat kilit devralındı: $($decision.Holder), $($decision.Since)" }
    if ($decision.Kind -eq "dead") { Add-FeedNote -Text "bu makinenin ölmüş bir koşusunun kilidi devralındı ($($decision.Since))" }
    if ($lockFree) {
        Add-FeedNote -Text "kilitsiz yol: kilit bu makinenin çalışan döngüsünde ($($decision.Holder), $($decision.Since)); besleyici kendi kilidiyle çalışıyor, döngünün kilidine dokunulmadı"
        if ($ownLock.TookOver) { Add-FeedNote -Text "besleyici kilidi devralındı: $($ownLock.TookOver)" }
    }
    if (@($ideas).Count -gt 0 -and $ideaBlock) {
        Add-FeedNote -Text "onaylanan fikir satırı bu koşuda yazılmadı ($pendingIds): $ideaBlock"
        $ideas = @()
    }

    foreach ($folder in @((Split-Path -Parent $feedPath), $rawDir)) {
        if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Force -Path $folder) }
    }
    $card = New-TeamFeedCard -Queue $queue -RoadmapText $roadmapText -FeedFile $feedRelative -MaxNew $MaxNew -Date $day -Ideas $ideas

    # Beside a running cycle the lead works in a throwaway worktree of HEAD: what the cycle
    # writes into the checkout meanwhile (its reports, splits, proposals) is not the run's, and
    # what the run writes where it works is judged exactly as in the checkout.
    if ($lockFree) {
        $worktree = New-TeamFeedWorktree -RepoRoot $repoRoot -Name "$feedId-$number-$PID"
        $runRoot = $worktree
    }
    $runFeedPath = Join-Path $runRoot ($feedRelative -replace '/', '\')
    $runRoadmapPath = Join-Path $runRoot "docs\ROADMAP.md"

    # What the checkout looks like before the run, and the roadmap's own bytes: what the run
    # changed is the difference, and the roadmap can be put back exactly.
    $roadmapBytes = [System.IO.File]::ReadAllBytes($runRoadmapPath)
    $before = Get-TeamFeedSnapshot -RepoRoot $runRoot

    # ---------------------------------------------------------------- the lead run
    $outputs = New-Object System.Collections.ArrayList
    $done = $null
    $attempt = 0
    while ($true) {
        # The flag was looked for before the lock was taken; here it is the flag that came up
        # while the usage limit was waited out - the second try is a new run, and is not started.
        if ($attempt -ge 1 -and (Test-Path -LiteralPath $stopFlagPath)) {
            Add-FeedNote -Text "sahip/lead durdurdu (team/stop.flag): lead koşusu yeniden başlatılmadı"
            break
        }
        $attempt++
        # The model of THIS try (ADR-0214 addendum 7, as in the cycle): the lead's model, or - when
        # that one is limited - the next open model down the chain. No open model: no run.
        $runModel = $leadModel
        if (Test-TeamModelId -Model $leadModel) {
            $pick = Get-TeamRunModel -Configured $leadModel -Limited $limitedModels -Fallback $true
            if ($null -eq $pick.Model) {
                $done = [pscustomobject]@{ Ok = $false; Outcome = "model yok"; UsageLimited = $true; ResetsAt = [string]$pick.ResetsAt }
                Add-FeedNote -Text ("Max kullanım limiti: lead'in kullanabileceği modellerin hepsi limitte" + $(if ($pick.ResetsAt) { " (en erken sıfırlanma $($pick.ResetsAt))" } else { "" }) + "; beklenmedi, bir sonraki besleme yeniden dener")
                break
            }
            $runModel = [string]$pick.Model
            if ($pick.Lowered) { Add-FeedNote -Text "model düşürüldü: $leadModel -> $runModel (limit)" }
        }
        $arguments = Get-TeamRunArguments -RoleFile $roleFile -MaxUsd $RunMaxUsd -Model $runModel -PrefixArguments $ClaudePrefixArguments -ExcludeTools $exclude
        $run = Start-TeamRun -FilePath $ClaudePath -Arguments $arguments -Prompt $card -WorkingDirectory $runRoot
        $deadline = if ($RunMinutes -gt 0) { [datetime]::UtcNow.AddMinutes($RunMinutes) } else { [datetime]::MaxValue }
        $finished = Wait-TeamRun -Run $run -Deadline $deadline
        $result = Read-TeamRunResult -StdOut $finished.StdOut -ExitCode $finished.ExitCode -StdErr $finished.StdErr -Model $runModel
        [void]$outputs.Add([pscustomobject]@{ StdOut = [string]$finished.StdOut; StdErr = [string]$finished.StdErr; Text = [string]$result.Text })
        $outcome = if ($finished.TimedOut) { "süre doldu ($RunMinutes dk)" } elseif ($result.Ok) { "tamam" } else { "başarısız: $($result.Why)" }
        $done = [pscustomobject]@{ Ok = ($result.Ok -and -not $finished.TimedOut); Outcome = $outcome; UsageLimited = [bool]$result.UsageLimited; ResetsAt = [string]$result.ResetsAt }
        Add-FeedNote -Text ("lead koşusu: {0} (tahmini {1:0.00} USD, {2} sn, model {3})" -f $outcome, [double]$result.CostUsd, [int]$finished.Seconds, $(if ($runModel) { $runModel } else { "varsayılan" }))
        if (-not $done.UsageLimited) { break }
        # A limit that closes ONE model (or does not say whose it is: then the model that ran):
        # that model is remembered as limited and the same feed is asked again AT ONCE, one model
        # down - not waited for. One try per model of the chain.
        if ((Test-TeamModelId -Model $runModel) -and [string]$result.LimitScope -ne "all") {
            $closed = if (Test-TeamModelId -Model ([string]$result.LimitedModel)) { [string]$result.LimitedModel } else { $runModel }
            $limitedModels[$closed] = [pscustomobject]@{ until = [string]$done.ResetsAt }
            # A reset that has passed (or was never said) would hand the same model out again.
            if (-not (Test-TeamModelLimited -Limited $limitedModels -Model $closed)) { $limitedModels[$closed] = [pscustomobject]@{ until = "" } }
            if ($attempt -lt (@(Get-TeamModelChain).Count + 1)) { continue }
            break
        }
        if ($attempt -ge 2) { break }
        # The subscription's limit: wait it out when the tool said when it lifts AND that is soon
        # (-MaxLimitWaitMinutes: the lock is held while waiting), then ask once more; else stop
        # here - the next feed run asks again (owner decision 2026-09-30).
        $until = $null
        if ($done.ResetsAt) { $until = ([datetime]::Parse($done.ResetsAt, [System.Globalization.CultureInfo]::InvariantCulture, [System.Globalization.DateTimeStyles]::AdjustToUniversal)).AddSeconds(90) }
        if (-not $WaitForUsageLimit -or $null -eq $until -or ($until - [datetime]::UtcNow).TotalMinutes -gt $MaxLimitWaitMinutes) {
            Add-FeedNote -Text ("Max kullanım limiti; " + $(if ($done.ResetsAt) { "sıfırlanma $($done.ResetsAt); " } else { "ne zaman açılacağı söylenmedi; " }) + "beklenmedi, bir sonraki besleme yeniden dener")
            break
        }
        Add-FeedNote -Text ("Max kullanım limiti: {0} sıfırlanmasına kadar beklendi ({1:0} dk)" -f $done.ResetsAt, [Math]::Max(0, ($until - [datetime]::UtcNow).TotalMinutes))
        while (($until - [datetime]::UtcNow).TotalSeconds -gt 0 -and -not (Test-Path -LiteralPath $stopFlagPath)) {
            Start-Sleep -Seconds ([int][Math]::Max(1, [Math]::Min(120, ($until - [datetime]::UtcNow).TotalSeconds)))
        }
    }

    # ---------------------------------------------------------------- what the run changed
    $after = Get-TeamFeedSnapshot -RepoRoot $runRoot
    $nowBytes = [System.IO.File]::ReadAllBytes($runRoadmapPath)
    $roadmapChanged = ([System.Convert]::ToBase64String($roadmapBytes) -cne [System.Convert]::ToBase64String($nowBytes))
    # The run's raw output goes to files only now: written earlier, they would be files "the
    # run changed".
    $index = 0
    foreach ($output in @($outputs.ToArray())) {
        $index++
        $stem = Join-Path $rawDir "lead-$number-$index"
        [System.IO.File]::WriteAllText("$stem.json", $output.StdOut, $utf8)
        if ($output.StdErr.Trim()) { [System.IO.File]::WriteAllText("$stem.stderr.txt", $output.StdErr, $utf8) }
        if ($output.Text) { [System.IO.File]::WriteAllText("$stem.md", $output.Text.TrimEnd() + "`n", $utf8) }
    }

    $refused = New-Object System.Collections.ArrayList
    $allowed = @($feedRelative)
    if (@($ideas).Count -gt 0) { $allowed += $roadmapRelative }
    $stray = @(Compare-TeamFeedSnapshot -Before $before -After $after -Allowed $allowed)
    if (@($stray).Count -gt 0) {
        [void]$refused.Add("lead koşusu sırasında izin verilmeyen dosya değişti: " + ((@($stray) | Select-Object -First 8) -join ", "))
    }
    $edit = $null
    if ($roadmapChanged -and @($ideas).Count -gt 0) {
        $edit = Test-TeamFeedRoadmapEdit -Before $utf8.GetString($roadmapBytes) -After $utf8.GetString($nowBytes) -Ideas $ideas
        foreach ($problem in @($edit.Problems)) { [void]$refused.Add("${roadmapRelative}: $problem") }
    }
    $trusted = ($null -ne $done -and $done.Ok -and @($refused.ToArray()).Count -eq 0)

    if (-not $trusted) {
        # Nothing of this run is used. The roadmap is the one file this script knows byte for
        # byte, so it is put back; any other file is left as it is found and named above.
        if ($roadmapChanged) {
            [System.IO.File]::WriteAllBytes($runRoadmapPath, $roadmapBytes)
            Add-FeedNote -Text "$roadmapRelative koşudan önceki haline geri konuldu"
        }
        foreach ($reason in @($refused.ToArray())) { Add-FeedNote -Text "reddedildi: $reason" }
        if ($null -ne $done) { Add-FeedNote -Text "kuyruğa hiçbir şey eklenmedi, hiçbir şey commit edilmedi" }
    }
    else {
        # -------------------------------------------------------------- the feed file
        $read = Read-TeamSplitFile -Path $runFeedPath
        if ($lockFree -and (Test-Path -LiteralPath $runFeedPath)) {
            # The file is kept where the between-cycles feed keeps it; the worktree goes.
            [System.IO.File]::Copy($runFeedPath, $feedPath, $true)
        }
        $why = @()
        $made = @()
        $rows = @(Get-TeamRoadmapRows -Text $roadmapText)
        if (-not $read.Ok) { $why = @(([string]$read.Why) -replace 'split file', 'feed file') }
        else {
            $feed = @(@($read.Split) | Where-Object { $null -ne $_ })
            $why = @(Test-TeamFeed -Feed $feed -Queue $queue -RoadmapRows $rows -MaxNew $MaxNew)
            if (@($why).Count -eq 0 -and @($feed).Count -gt 0 -and $lockFree) {
                # The run took minutes, and the cycle, the lead and the owner wrote meanwhile: the
                # feed is judged again against the queue as it is NOW. An id somebody created in
                # the meantime is theirs - that card and its dependants are dropped, the rest is
                # judged whole.
                $fresh = $null
                try { $fresh = Get-TeamQueueApi -Store $apiStore }
                catch {
                    Add-FeedNote -Text ("kuyruk deposu yeniden okunamadı: " + (([string]$_.Exception.Message) -replace '\s+', ' ').Trim())
                    Add-FeedNote -Text "kuyruğa hiçbir şey yazılmadı; besleme dosyası diskte kaldı: $feedRelative"
                    $exitCode = 1
                }
                if ($null -ne $fresh) {
                    $kept = Select-TeamFeedFresh -Feed $feed -Before $queue -Fresh $fresh
                    foreach ($gone in @($kept.Dropped)) { Add-FeedNote -Text "yazılmadı: $($gone.Id) - $($gone.Why)" }
                    $feed = @($kept.Feed)
                    $queue = $fresh
                    $why = @(Test-TeamFeed -Feed $feed -Queue $queue -RoadmapRows $rows -MaxNew $MaxNew)
                }
                else { $feed = @() }
            }
            if (@($why).Count -eq 0 -and @($feed).Count -gt 0) {
                $made = @(ConvertTo-TeamFeedTasks -Feed $feed -RoadmapRows $rows -Date $day -MaxUsd $RunMaxUsd)
                $trial = [pscustomobject]@{ version = 1; tasks = @(@(Get-TeamTasks -Queue $queue) + @($made | ForEach-Object { $_.Task })) }
                $why = @(Test-TeamQueue -Queue $trial)
            }
            elseif (@($why).Count -eq 0 -and $exitCode -eq 0) {
                Add-FeedNote -Text $(if (@(@($read.Split) | Where-Object { $null -ne $_ }).Count -eq 0) { "lead kart kesmedi: $feedRelative boş liste" } else { "kuyruğa yazılacak kart kalmadı: $feedRelative" })
            }
        }
        if (@($why).Count -gt 0) {
            Add-FeedNote -Text ("reddedildi: ${feedRelative}: " + ((@($why) | ForEach-Object { ([string]$_) -replace '\s+', ' ' }) -join "; "))
            Add-FeedNote -Text "kuyruğa hiçbir şey eklenmedi (dosya bütün olarak reddedilir)"
        }
        elseif (@($made).Count -gt 0) {
            foreach ($entry in $made) {
                if ($entry.Kind -ne "owner") { continue }
                $proposalPath = Join-Path $repoRoot (([string]$entry.ProposalPath) -replace '/', '\')
                $proposalFolder = Split-Path -Parent $proposalPath
                if (-not (Test-Path -LiteralPath $proposalFolder)) { [void](New-Item -ItemType Directory -Force -Path $proposalFolder) }
                [System.IO.File]::WriteAllText($proposalPath, [string]$entry.ProposalText, $utf8)
            }
            if ($lockFree) {
                # ONLY the new tasks, each as its own create; a task the store has is never sent.
                $saved = Save-TeamFeedCreates -Store $apiStore -Tasks @($made | ForEach-Object { $_.Task })
                foreach ($gone in @($saved.Dropped)) { Add-FeedNote -Text "yazılmadı: $($gone.Id) - $($gone.Why)" }
                if ($saved.Failed) {
                    Add-FeedNote -Text "kuyruk deposu yazmayı kesti: $($saved.Failed); besleme dosyası diskte kaldı: $feedRelative"
                    $exitCode = 1
                }
                $made = @($made | Where-Object { @($saved.Written) -contains [string]$_.Task.id })
            }
            else {
                Set-TeamProperty -InputObject $queue -Name "tasks" -Value @(@(Get-TeamTasks -Queue $queue) + @($made | ForEach-Object { $_.Task }))
                Save-FeedQueue -Document $queue
            }
            foreach ($entry in $made) {
                $task = $entry.Task
                if ($entry.Kind -eq "owner") { Add-FeedNote -Text "sahibe soruldu (awaiting_owner): $($task.id) — $($task.title): $($task.reason)" }
                else { Add-FeedNote -Text "kuyruğa eklendi (approved): $($task.id) — $($task.title) [$($task.reason)]" }
            }
        }

        # -------------------------------------------------------------- the idea rows
        if (@($ideas).Count -gt 0) {
            $written = @()
            if ($null -ne $edit) { $written = @($edit.Added) }
            if (@($written).Count -gt 0) {
                $names = @($ideas | Where-Object { $written -contains [string]$_.id } | ForEach-Object { "$([string]$_.id) - $([string]$_.title)" })
                $message = "roadmap: approved idea written under 'Approved ideas': " + ($names -join "; ")
                # `commit -- <path>` takes that one path as it is in the working tree and leaves
                # everything else - staged or not - out of the commit.
                $commit = Invoke-TeamGit -WorkingDirectory $repoRoot -Arguments @("commit", "-m", $message, "--", $roadmapRelative)
                if ($commit.Success) {
                    $sha = (Invoke-TeamGit -WorkingDirectory $repoRoot -Arguments @("rev-parse", "HEAD")).StdOut.Trim()
                    Add-FeedNote -Text "roadmap 'Approved ideas' satırı yazıldı: $($written -join ', ') (commit $sha, dal $branch; push yok)"
                }
                else {
                    [System.IO.File]::WriteAllBytes($roadmapPath, $roadmapBytes)
                    Add-FeedNote -Text ("roadmap satırı commit edilemedi, $roadmapRelative geri konuldu: " + (($commit.StdOut + " " + $commit.StdErr) -replace '\s+', ' ').Trim())
                }
            }
            $missing = @($ideas | Where-Object { $written -notcontains [string]$_.id } | ForEach-Object { [string]$_.id })
            if (@($missing).Count -gt 0) { Add-FeedNote -Text "fikir satırı yazılmadı (lead koşusu tabloya eklemedi): $($missing -join ', ') - bir sonraki besleme yeniden ister" }
        }
    }
    Save-FeedReport
    Write-Host "feed $feedId ended; report: $reportPath"
}
finally {
    if ($lockFree) {
        # The worktree first (the run is over and judged), then the feeder's own lock.
        $left = Remove-TeamFeedWorktree -RepoRoot $repoRoot -Path $worktree
        if ($left) { Write-Host "  $left" }
        $left = Exit-TeamFeederLock -Lock $ownLock
        if ($left) { Write-Host "  $left" }
    }
    elseif ($useApi) {
        try { Clear-TeamLockApi -Store $apiStore -Machine $Machine -CycleId $feedId }
        catch { Write-Host "the lock was not released in the queue store: $($_.Exception.Message)" }
    }
    else { Write-TeamJson -Path $lockPath -Document (New-TeamLockReleased) }
}
exit $exitCode
