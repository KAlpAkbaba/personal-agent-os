<#
.SYNOPSIS
    The step after a cycle (docs/TEAM_PROTOCOL.md sections 3, 4, 9a, 10): what the cycle left
    'merged' on an integration branch is wired by a lead run, gated in a worktree of its own,
    and - only when the gate is green - put on main. It never releases.

.DESCRIPTION
    A cycle ends at "merged into integrate/<cycle-id>". Until this step existed the full gate
    and the merge to main were the lead's, by hand, so a task that depends on another (the
    dependency must be ON MAIN) waited for a person. The scheduled task runs this after the
    cycle. For every integration branch that holds merged tasks and is ahead of main:

      1. in API mode the step's OWN lock is taken first - a machine-local file (pid and start
         time; a dead holder's file is taken over and said), so two gates never overlap on one
         machine - and released in `finally` on every path. Then the team lock (the file or the
         Cloud Core's, the cycle's own functions) is taken as the cycle 'integrate-<branch>' and
         released in `finally` - EXCEPT with -BesideCycle (API mode), where the cycle's lock is
         never read, taken or released and the cycle runs while the gate does. A result is then
         written onto a task only if the store, read again, still has it 'merged' on the same
         integration branch with the same sha; otherwise it is dropped and named. The wiring
         may grow scripts/quality-gate.ps1, never take a step, an Assert-ExitCode line or its
         'QUALITY GATE: PASS' line out of it (the run is refused, like a disallowed file);
      2. in .claude/worktrees/gate/<branch> - never the main checkout, the owner works there -
         main is merged into the branch. A conflict stops the tasks ('main ile çakışma') and
         forces nothing;
      3. the worktree gets its own environment (uv sync, pnpm install) - BEFORE the lead's run,
         so a tool that cannot build it costs no model run; what the build scribbles on a tracked
         file is discarded. Docker's dev stack is shared and must be up: if it is not, the step
         stops with 'Docker çalışmıyor' and changes nothing;
      4. a LEAD run (a fresh `claude -p`, .claude/agents/lead.md, its tools, in that worktree)
         gets the "For the lead at merge" sections of the tasks' newest worker and inspector
         reports and wires the shared files. It runs on the LEAD'S MODEL of the team's setting
         (the store's in API mode, else team/models.json, else the defaults - as the cycle
         reads it), or - when that model is limited (team/limits.json, or the run's own
         answer) - one model down at once; the report says 'model düşürüldü'. A usage limit is
         never an attempt and is NEVER waited for: the lock is held. The run's command is
         created suspended, put into a job object and only then let run, so everything it ever
         starts is in the job; a command that cannot be put into one is never let run (no run,
         nothing counted). When the run ends, every process it left going is stopped before
         anything is read. What it changed is then
         COMMITTED, and THIS script checks the committed diff (renames off: a file moved out
         of an area is a deletion there): docs/, .github/, team/, scripts/quality-gate.ps1,
         state/BUILD_STATE.json and the files a section names; one file outside that refuses
         the run and nothing is merged. What passed is the integration branch's new tip, and
         the tree is put back on exactly that commit before the gate. The run shares the repository, so main, the integration branch
         and the remote's main are read before and after it: one that MOVED refuses the run,
         is named with both shas, and stops the branch at once (it is not put back here). When
         the wiring changed a file the environment is built from, it is built again;
      5. the FULL gate runs there, its log kept as team/reports/<cycle>/gate-<n>.log;
      6. GREEN (exit code 0 AND the gate's last word): main gets a --no-ff merge naming the
         gated sha, is pushed, and the tasks become 'awaiting_release' with main's sha.
         RED: nothing reaches main; the failing steps and the first failing test go into the
         report and into each task's reason; the tasks whose files a FAILING line names go back
         to 'returned' (a PASS line, and a gate that died, name nobody), the others stay
         'merged'. The verdict is kept in the attempt's record: when the queue could not be
         written, the next run writes it - without a second gate. Two failed attempts on one
         branch stop it until the lead looks (-ClearGateStop).

    The lead's run and the gate are ALWAYS capped (-LeadMinutes, -GateMinutes; 0 is refused):
    the lock is held while they go, and it is taken over after six hours.

    A branch goes onto main WHOLE or not at all: while a task whose code is on it is not
    'merged' (a red gate returned it; its code is still on the branch) nothing of the branch is
    gated - the cycle merges the fix into the same branch, and one gate judges everything. And
    a commit the gate was red on is not gated a second time: the step waits for a new tip (a
    fix merged in, main moved) or for the lead's -ClearGateStop. Both waits take no lock.

    What it does NOT do, on purpose: it starts no release, makes no tag, writes nothing about
    production or the recovery supervisor, and never resets, forces or checks out a branch in a
    worktree it did not make. main is only ever moved FORWARD (scripts/lib/TeamIntegrate.ps1,
    Move-TeamBranchForward).

    Idempotent: a run that was killed is finished by the next one - a gate that was green on the
    branch's tip is not run again. A gate worktree whose folder was deleted by hand is made again.

    A run that STARTED NOTHING (exit 3, exit 4) writes and posts no report: the branch's report
    keeps what the last run that did something found. It leaves one line in
    team/reports/integrate-skipped.log (this machine only; the newest 200 are kept).

    Exit codes: 0 done or nothing to do; 2 the queue, the model setting or -Model breaks the
    protocol; 3 the lock is held; 4 Docker is down; 5 conflict with main; 6 the gate is red;
    7 the lead's run was refused or gave no result, or no model is open for it (the usage
    limit: not an attempt, never counted towards 8), or its command could not be put into a
    job (never let run, not an attempt); 8 the branch is stopped (two failed attempts); 9 main moved but the push
    failed; 10 the worktree's environment could not be built; 11 a branch could not be moved
    forward; 12 an unexpected error, or the queue could not be written (the next run finishes
    it); 13 a ref moved during the lead's run.

.PARAMETER GatePath
    The gate script to run in the gate worktree. Empty (the default) is the worktree's own
    scripts\quality-gate.ps1. The tests name scripts/tests/lib/fake-gate.ps1 here.

.PARAMETER Model
    The model of a role the setting does not name (one of the three ids). The setting wins.

.PARAMETER ClearGateStop
    The lead looked at a branch that two failed attempts (or a moved ref) stopped: the count
    starts again. It also gates a commit again that waits after ONE red gate. It does not open
    a branch that a returned task holds.

.EXAMPLE
    .\scripts\team\integrate.ps1 -QueueUrl https://core.example/ -QueueToken C:\path\token.txt
#>
[CmdletBinding()]
param(
    [string]$TeamRoot = "",
    # Only this integration branch; empty = every branch that holds merged tasks.
    [string]$Branch = "",
    [string]$Base = "main",
    [string]$Remote = "origin",
    [string]$GatePath = "",
    # Caps, never 0: the lock is held while the lead's run and the gate go, and a gate that hangs
    # would hold it until the six-hour takeover. The real gate takes the better part of an hour.
    [double]$GateMinutes = 150,
    [double]$LeadMinutes = 30,
    [string]$ClaudePath = (Join-Path $env:USERPROFILE ".local\bin\claude.exe"),
    [string[]]$ClaudePrefixArguments = @(),
    [string]$Model = "",
    [string]$Machine = $env:COMPUTERNAME,
    # Never PATH alone: a spawned shell on this machine does not reliably have it.
    [string]$DockerPath = "",
    [string]$UvPath = "",
    [string]$PnpmPath = "",
    [switch]$ClearGateStop,
    [switch]$DryRun,
    [string]$QueueUrl = "",
    # A PATH to the file holding the owner-session token; a token is never a parameter.
    [string]$QueueToken = "",
    # API mode only: the step runs beside a cycle of this machine and never reads, takes or
    # releases the cycle's lock (its own lock still keeps two gates apart). Passed by the
    # scheduled call only once the gate has a database of its own (card gate-own-database).
    [switch]$BesideCycle,
    # The step's own lock (API mode): a machine-local file, outside the repository. "" = under
    # %LOCALAPPDATA%\PagentOS. The tests name one of their own.
    [string]$StepLockPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamIntegrate.ps1")

# A parameter is never assigned over (provision.tests.ps1 holds every script to it).
$teamDir = if ($TeamRoot) { $TeamRoot } else { Join-Path $repoRoot "team" }
$queuePath = Join-Path $teamDir "queue.json"
$lockPath = Join-Path $teamDir "lock.json"
$reportsRoot = Join-Path $teamDir "reports"
$leadRoleFile = Join-Path $repoRoot ".claude\agents\lead.md"
$utf8 = New-Object System.Text.UTF8Encoding($false)
$stopSentence = "aynı entegrasyon dalında iki kez kırmızı (kapı ya da lead koşusu); lead bakana kadar durdu (TEAM_PROTOCOL 10)"
$movedSentence = "lead koşusu sırasında bir dal YER DEĞİŞTİRDİ; lead bakana kadar durdu (TEAM_PROTOCOL 10)"

$capProblem = Test-TeamCapMinutes -GateMinutes $GateMinutes -LeadMinutes $LeadMinutes
if ($capProblem) { throw $capProblem }
# As in the cycle: a value that is not one of the three model ids never reaches a command line.
if ($Model -and -not (Test-TeamModelId -Model $Model)) {
    Write-Host "-Model '$Model' is not a model; nothing was done. One of: $((Get-TeamModelChain) -join ', ')"
    exit 2
}

$useApi = [bool]$QueueUrl
if ($useApi -and -not $QueueToken) { throw "-QueueUrl needs -QueueToken: the path of a file holding the token" }
$apiStore = $null
if ($useApi) { $apiStore = New-TeamApiStore -Url $QueueUrl -TokenFile $QueueToken }
# In file mode the queue has ONE writer, the holder of the cycle's lock: the step takes it, as always.
$besideCycle = $useApi -and $BesideCycle
if ($BesideCycle -and -not $useApi) { Write-Host "-BesideCycle has no effect in file mode: the cycle's lock is taken (the queue file has one writer)" }
$localRoot = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { $env:TEMP }
$stepLockFile = if ($StepLockPath) { $StepLockPath } else { Join-Path $localRoot "PagentOS\integrate-step.lock" }

function Save-Queue {
    <#
        Writes the results of one branch's tasks. In API mode the cycle may have moved a task
        while the gate ran: each task is READ AGAIN from the store, and its result is written only
        when it is still the task the step took (merged, the same integration branch, the same
        sha), on top of what others changed. The tasks whose result was dropped are answered.
    #>
    param([object[]]$Tasks = @())
    if (-not $useApi) { Write-TeamJson -Path $queuePath -Document $script:queue; return @() }
    $fresh = Read-TeamQueueApi -Store $apiStore
    $merged = Merge-TeamStepResults -Ours $script:queue -Fresh $fresh -Taken $script:taken -Ids @(@($Tasks) | ForEach-Object { [string]$_.id })
    Save-TeamQueueApi -Store $apiStore -Queue $merged.Queue
    return @($merged.Dropped)
}

# Resolved before anything is taken or written: an .exe or a .cmd, never a .ps1 (a given path
# that is neither ends the step here, by name). "" = not found; said where the tool is needed.
$dockerTool = Resolve-TeamToolPath -Given $DockerPath -Name "docker" -Fallbacks @("%ProgramFiles%\Docker\Docker\resources\bin\docker.exe")
$uvTool = Resolve-TeamToolPath -Given $UvPath -Name "uv" -Fallbacks @("%USERPROFILE%\.local\bin\uv.exe", "%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe")
$pnpmTool = Resolve-TeamToolPath -Given $PnpmPath -Name "pnpm" -Fallbacks @("%APPDATA%\npm\pnpm.cmd", "%LOCALAPPDATA%\pnpm\pnpm.exe")

$queue = if ($useApi) { Read-TeamQueueApi -Store $apiStore } else { Read-TeamJson -Path $queuePath }
$problems = @(Test-TeamQueue -Queue $queue)
if (@($problems).Count -gt 0) {
    Write-Host "the queue breaks the protocol; nothing was done:"
    foreach ($problem in $problems) { Write-Host "  - $problem" }
    exit 2
}
# Each task as the step took it: a result is written only onto the task it was gated as (Save-Queue).
$taken = @{}
foreach ($task in @(Get-TeamTasks -Queue $queue)) { $taken[[string]$task.id] = (ConvertTo-Json -InputObject $task -Depth 12 -Compress) }

# git never asks a question here: a push that wants a password fails instead of waiting for one.
$env:GIT_TERMINAL_PROMPT = "0"

$started = [datetime]::UtcNow
$startedAt = Get-TeamTimestamp -Now $started
$stops = New-Object System.Collections.ArrayList
$risks = New-Object System.Collections.ArrayList
$outcomes = New-Object System.Collections.ArrayList

# ------------------------------------------------------------------ what waits for the gate

$baseAtStart = Get-TeamRevision -RepoRoot $repoRoot -Revision "refs/heads/$Base"
if (-not $baseAtStart) { throw "the branch '$Base' does not exist in $repoRoot" }

$pending = New-Object System.Collections.ArrayList
foreach ($group in @(Get-TeamMergedGroups -Queue $queue)) {
    $integration = [string]$group.Branch
    if ($Branch -and $integration -ne $Branch) { continue }
    $cycleOf = ""
    try { $cycleOf = Get-TeamIntegrationCycleId -Branch $integration }
    catch { Write-Host "skipped: '$integration' is not an integration branch"; continue }
    $tip = Get-TeamRevision -RepoRoot $repoRoot -Revision "refs/heads/$integration"
    if (-not $tip) { Write-Host "skipped: the branch $integration does not exist here"; continue }
    $directory = Join-Path $reportsRoot $cycleOf
    $records = @(Get-TeamGateRecords -Directory $directory -Branch $integration)
    $green = $null
    if (@($records).Count -gt 0) {
        $last = $records[@($records).Count - 1]
        if ([string](Get-TeamProperty -InputObject $last -Name "result" -Default "") -eq "green" -and
            [string](Get-TeamProperty -InputObject $last -Name "sha" -Default "") -eq $tip) { $green = $last }
    }
    $ahead = -not (Test-TeamAncestor -RepoRoot $repoRoot -Ancestor $tip -Of $baseAtStart)
    # A branch a moved ref stopped says so every run until the lead looked, wherever its tip is now.
    $movedStop = ((Get-TeamGateStopKind -Records $records) -eq "refs_moved")
    if (-not $ahead -and $null -eq $green -and -not $movedStop) {
        Write-Host "nothing to do for ${integration}: it is not ahead of $Base"
        continue
    }
    # A red gate whose verdict never reached the queue (the write failed) is written by this run,
    # before anything else is asked of the branch. The lead's -ClearGateStop asks for a new gate instead.
    $unapplied = $null
    if ($ahead -and -not $movedStop -and -not $ClearGateStop) { $unapplied = Get-TeamGateUnappliedVerdict -Records $records -Sha $tip }
    if ($ahead -and -not $movedStop -and $null -eq $unapplied) {
        # A branch goes onto main whole or not at all. While a task whose code is on it is not
        # 'merged' (a red gate returned it), a green gate would put that code on main with the
        # others - so nothing of the branch is gated, and the lead's -ClearGateStop does not open it.
        $held = @(Get-TeamBranchHeldTasks -Queue $queue -Branch $integration)
        if (@($held).Count -gt 0) {
            $who = (@($held) | ForEach-Object { "$($_.id) ($($_.state))" }) -join ", "
            Write-Host "waits: $integration is held by $who - their code is on the branch and has not passed; the branch goes onto $Base whole, when they are merged again"
            continue
        }
        # The gate was red on this very commit and main has not moved past it: the answer is known.
        if (-not $ClearGateStop -and -not (Test-TeamGateStopped -Records $records) -and
            (Test-TeamGateAlreadyRed -Records $records -Sha $tip) -and (Test-TeamAncestor -RepoRoot $repoRoot -Ancestor $baseAtStart -Of $tip)) {
            Write-Host "waits: $integration - the gate was red on $tip and neither the branch nor $Base has moved; a new commit (or the lead's -ClearGateStop) is gated, not the same one again"
            continue
        }
    }
    [void]$pending.Add([pscustomobject]@{
            Branch = $integration; CycleId = $cycleOf; Tasks = @($group.Tasks); Tip = $tip; Ahead = $ahead
            Directory = $directory; Green = $green; Unapplied = $unapplied
            # What this run did with the branch: the attempt's number, whether a lead run was paid for,
            # and the red record whose verdict is on the tasks (marked applied once the queue is written).
            Attempt = 0; LeadRan = $false; Verdict = 0
        })
}
if (@($pending).Count -eq 0) {
    Write-Host "nothing to integrate: no merged task on an integration branch that is ahead of $Base and may be gated now"
    exit 0
}

# ------------------------------------------------------------------ the model policy (TEAM_PROTOCOL 9a)
# The lead's model is read where the cycle reads it (ADR-0214 addendum 7): the team store's
# setting in API mode, else team/models.json, else the defaults - a store that cannot be read
# never stops the step. What the cycles learnt about the limits (team/limits.json) is read and
# never written here. A lead model that is limited starts no run: the run goes one model down
# when the setting allows it (Get-TeamRunModel), and the report says 'model düşürüldü'. The
# step NEVER waits for a reset - it holds the team lock - and a usage limit is never an attempt.
$modelDocument = $null
$modelSource = "the defaults"
if ($useApi) {
    try {
        $modelDocument = Get-TeamModelsApi -Store $apiStore
        if ($null -ne $modelDocument) { $modelSource = "GET /v1/team/queue/models" }
    }
    catch { [void]$risks.Add("model ayarı takım deposundan okunamadı; yerel dosya ya da varsayılanlar kullanıldı: $($_.Exception.Message)") }
}
$modelsPath = Join-Path $teamDir "models.json"
if ($null -eq $modelDocument -and (Test-Path -LiteralPath $modelsPath)) {
    $modelDocument = Read-TeamJson -Path $modelsPath
    $modelSource = "team/models.json"
}
$modelRead = Read-TeamModelSetting -Document $modelDocument -DefaultModel $Model
if (-not $modelRead.Ok) {
    Write-Host "the model setting ($modelSource) breaks the contract; nothing was done:"
    foreach ($problem in @($modelRead.Problems)) { Write-Host "  - $problem" }
    exit 2
}
$leadConfigured = [string]$modelRead.Setting.roles.lead
$modelFallback = [bool]$modelRead.Setting.fallback
$limitedModels = Read-TeamLimitedModels -Path (Join-Path $teamDir "limits.json")

function Select-LeadModel {
    <# The model the lead's run starts on NOW; Model is $null when none is open (no run is started). #>
    return (Get-TeamRunModel -Configured $leadConfigured -Limited $script:limitedModels -Fallback $modelFallback)
}

function Get-NoModelSentence {
    <# Why no lead run can be started now, in the words the report and every task carry. #>
    param($Pick)
    $who = if ($modelFallback) { "lead'in kullanabileceği modellerin hepsi limitte" } else { "lead'in modeli $leadConfigured limitte ve model düşürme kapalı" }
    $when = if ($Pick.ResetsAt) { " (en erken sıfırlanma $($Pick.ResetsAt))" } else { " (ne zaman açılacağı söylenmedi)" }
    return "Max kullanım limiti: $who$when; sayılmadı, beklenmedi (kilit tutulurken beklenmez), bir sonraki adım dener"
}

function New-Outcome {
    param($Item)
    $outcome = [pscustomobject]@{ Branch = [string]$Item.Branch; CycleId = [string]$Item.CycleId; Result = "başlamadı"; Lines = (New-Object System.Collections.ArrayList) }
    [void]$script:outcomes.Add($outcome)
    return $outcome
}

function Save-Reports {
    <# One report per integration branch, beside the cycle's own: team/reports/<cycle>-integrate.md. #>
    if (-not (Test-Path -LiteralPath $reportsRoot)) { [void](New-Item -ItemType Directory -Force -Path $reportsRoot) }
    foreach ($outcome in @($script:outcomes)) {
        $text = New-TeamIntegrateReport -Machine $Machine -StartedAt $startedAt -Outcomes @($outcome) -Stops @($script:stops.ToArray()) -Risks @($script:risks.ToArray())
        $name = "$($outcome.CycleId)-integrate.md"
        [System.IO.File]::WriteAllText((Join-Path $reportsRoot $name), $text + "`n", $utf8)
        if ($useApi) {
            try { Send-TeamReportApi -Store $apiStore -Name $name -Text $text }
            catch { Write-Host "the report was not posted to the queue store: $($_.Exception.Message)" }
        }
    }
}

function Write-Skipped {
    <#
        A run that STARTED NOTHING (the lock is somebody's, Docker is down) writes no report for
        the branch and posts none: team/reports/<cycle>-integrate.md and the store's copy keep what
        the last run that did something found (a red gate's words are not replaced by "kilit başka
        koşuda" half an hour later). What it says is one line in team/reports/integrate-skipped.log.
    #>
    param([string]$Sentence)
    $branches = (@($pending) | ForEach-Object { [string]$_.Branch }) -join ", "
    try { Add-TeamSkippedLine -Path (Join-Path $reportsRoot "integrate-skipped.log") -Line "$(Get-TeamTimestamp) $Machine ${branches}: $Sentence" }
    catch { Write-Host "the line was not written to integrate-skipped.log: $($_.Exception.Message)" }
}

# ------------------------------------------------------------------ the cycle's lock, as the cycle takes it
# Beside the cycle (API mode, -BesideCycle) it is not even read: the cycle runs while the gate does,
# and only the step's own lock (below) keeps two gates apart.

$lock = $null
if ($besideCycle) { }
elseif ($useApi) { $lock = Get-TeamLockApi -Store $apiStore }
elseif (Test-Path -LiteralPath $lockPath) { $lock = Read-TeamJson -Path $lockPath }
$decision = Get-TeamLockDecision -Lock $lock -Machine $Machine -Now $started
if ($decision.Kind -eq "ours") {
    # Ours, and fresh. If the process that took it is gone, the run died and the lock with it.
    $holderPid = [int](Get-TeamProperty -InputObject $lock -Name "pid" -Default 0)
    $alive = $false
    if ($holderPid -gt 0) { $alive = ($null -ne (Get-Process -Id $holderPid -ErrorAction SilentlyContinue)) }
    if (-not $alive) { $decision = [pscustomobject]@{ MayRun = $true; Kind = "dead"; Holder = $decision.Holder; Since = $decision.Since } }
}

if ($DryRun) {
    Write-Host "DRY RUN: nothing is changed."
    if (-not $decision.MayRun) { Write-Host "  the lock is held by $($decision.Holder) since $($decision.Since): a real run would stop here" }
    foreach ($item in $pending) {
        $ids = (@($item.Tasks) | ForEach-Object { [string]$_.id }) -join ", "
        Write-Host "  $($item.Branch) @ $($item.Tip) - tasks: $ids"
        if (Test-TeamGateStopped -Records @(Get-TeamGateRecords -Directory $item.Directory -Branch $item.Branch)) {
            Write-Host "    would stop: two failed attempts on this branch (TEAM_PROTOCOL 10)$(if ($ClearGateStop) { '; -ClearGateStop would start the count again' })"
        }
        elseif ($null -ne $item.Unapplied) { Write-Host "    would write the red gate's verdict an earlier run could not write to the queue; no second gate on the same commit" }
        elseif ($null -ne $item.Green) { Write-Host "    would finish an earlier run: the gate was green on this commit; $Base would get it without a second gate" }
        else {
            Write-Host "    would merge $Base into it in $(Get-TeamGateWorktreePath -RepoRoot $repoRoot -Branch $item.Branch)"
            $dryPick = Select-LeadModel
            $onModel = if ($null -eq $dryPick.Model) { "NO model is open for the lead (usage limit): no run would be started, nothing counted" }
            elseif ($dryPick.Lowered) { "on $($dryPick.Model) (lowered from $($dryPick.Intended): limited)" }
            else { "on $($dryPick.Model)" }
            Write-Host "    would build the worktree's environment, then run the lead's wiring run there (at most $LeadMinutes min) - $onModel ($modelSource) -, stop what it left going, commit and check the committed diff"
            Write-Host "    would run $(if ($GatePath) { $GatePath } else { 'scripts\quality-gate.ps1' }) (at most $GateMinutes min)"
            Write-Host "    green: $Base gets a --no-ff merge, is pushed to $Remote, the tasks become awaiting_release; red: nothing reaches $Base"
        }
    }
    exit 0
}

# ------------------------------------------------------------------ one branch

function Set-TaskNote {
    param($Task, [string]$State = "", [string]$Reason = "", [string]$Sha = "")
    if ($State) { Set-TeamProperty -InputObject $Task -Name "state" -Value $State }
    if ($Reason) { Set-TeamProperty -InputObject $Task -Name "reason" -Value $Reason }
    if ($Sha) { Set-TeamProperty -InputObject $Task -Name "sha" -Value $Sha }
    Set-TeamProperty -InputObject $Task -Name "updated_at" -Value (Get-TeamTimestamp)
}

function Add-Strike {
    <# A failed attempt is recorded; the answer is whether the branch is now stopped. #>
    param($Item, [int]$Number, [string]$Result, [hashtable]$More = @{})
    $record = [ordered]@{ n = $Number; branch = [string]$Item.Branch; at = (Get-TeamTimestamp); result = $Result }
    foreach ($name in @($More.Keys)) { $record[$name] = $More[$name] }
    Write-TeamGateRecord -Directory $Item.Directory -Number $Number -Record ([pscustomobject]$record)
    return (Test-TeamGateStopped -Records @(Get-TeamGateRecords -Directory $Item.Directory -Branch $Item.Branch))
}

function Set-GateVerdict {
    <# A red gate's verdict on the tasks: its words on every one, 'returned' for those it named. #>
    param([object[]]$Tasks, [string]$Reason, [string]$WaitsFor, [string[]]$Blamed = @(), [bool]$Stopped = $false)
    foreach ($task in @($Tasks)) {
        $own = $Reason + " | " + $WaitsFor
        if ($Stopped) { $own += " | " + $stopSentence }
        if (@($Blamed) -contains [string]$task.id) { Set-TaskNote -Task $task -State "returned" -Reason $own }
        else { Set-TaskNote -Task $task -Reason $own }
    }
}

function Invoke-EnvironmentBuild {
    <# The gate worktree's own environment (uv sync, pnpm install). "" when it is built, else what failed. #>
    param([string]$Tree, $Outcome, [string]$When = "")
    foreach ($step in @(Get-TeamGateEnvironmentPlan -Worktree $Tree -UvPath $uvTool -PnpmPath $pnpmTool)) {
        $watch = [System.Diagnostics.Stopwatch]::StartNew()
        $failure = ""
        if (-not $step.FilePath) { $failure = "araç bulunamadı" }
        else {
            try {
                $ran = Invoke-NativeProcess -FilePath $step.FilePath -Arguments @($step.Arguments) -WorkingDirectory $step.Directory -TimeoutSeconds $script:TeamEnvironmentStepSeconds
                if (-not $ran.Success) { $failure = "çıkış kodu $($ran.ExitCode)" }
            }
            catch { $failure = $_.Exception.Message }
        }
        $watch.Stop()
        if ($failure) { return "$($step.Name) ($failure)" }
        [void]$Outcome.Lines.Add("ortam${When}: $($step.Name): $([int]$watch.Elapsed.TotalSeconds) sn")
    }
    return ""
}

function Invoke-TreeGit {
    <# git in the gate worktree; a failure is an error, with git's own words. #>
    param([string]$Tree, [string[]]$Arguments)
    $result = Invoke-TeamGit -WorkingDirectory $Tree -Arguments $Arguments
    if (-not $result.Success) { throw "git $($Arguments -join ' ') failed in the gate worktree: $((($result.StdOut + ' ' + $result.StdErr) -replace '\s+', ' ').Trim())" }
    return $result.StdOut.Trim()
}

function Invoke-BranchIntegration {
    param($Item, $Outcome)
    $integration = [string]$Item.Branch
    $tasks = @($Item.Tasks)
    $ids = (@($tasks) | ForEach-Object { [string]$_.id }) -join ", "
    $number = Get-TeamGateNextNumber -Directory $Item.Directory
    $relative = "team/reports/$($Item.CycleId)"

    if ($null -ne $Item.Unapplied) {
        # The gate was red on this commit and the queue never got its verdict (the write failed).
        # The record kept it: it is written now. No lead run, no gate - the answer is known.
        $record = $Item.Unapplied
        $stopped = Test-TeamGateStopped -Records @(Get-TeamGateRecords -Directory $Item.Directory -Branch $integration)
        $blamed = @(@(Get-TeamProperty -InputObject $record -Name "blamed" -Default @()) | ForEach-Object { [string]$_ } | Where-Object { $_ })
        $reason = [string](Get-TeamProperty -InputObject $record -Name "reason" -Default "kapı kırmızı")
        Set-GateVerdict -Tasks $tasks -Reason $reason -WaitsFor ([string](Get-TeamProperty -InputObject $record -Name "waits" -Default "")) -Blamed $blamed -Stopped $stopped
        $Item.Verdict = [int](Get-TeamProperty -InputObject $record -Name "n" -Default 0)
        $Outcome.Result = "kapı kırmızı"
        [void]$Outcome.Lines.Add("önceki koşunun kırmızı kapı kararı kuyruğa yazılamamıştı; kapı yeniden KOŞULMADI, karar şimdi yazıldı")
        [void]$Outcome.Lines.Add($reason)
        [void]$Outcome.Lines.Add("geri verilen: " + $(if (@($blamed).Count -gt 0) { $blamed -join ", " } else { "yok" }) + "; $Base değişmedi")
        if ($stopped) { [void]$script:stops.Add("${integration}: $stopSentence. Baktıktan sonra: scripts\team\integrate.ps1 -ClearGateStop"); return 8 }
        return 6
    }

    if ($ClearGateStop -and (Test-TeamGateStopped -Records @(Get-TeamGateRecords -Directory $Item.Directory -Branch $integration))) {
        Write-TeamGateRecord -Directory $Item.Directory -Number $number -Record ([pscustomobject]@{ n = $number; branch = $integration; at = (Get-TeamTimestamp); result = "cleared" })
        [void]$Outcome.Lines.Add("lead baktı (-ClearGateStop): sayım yeniden başladı")
        $number = Get-TeamGateNextNumber -Directory $Item.Directory
    }
    $attempts = @(Get-TeamGateRecords -Directory $Item.Directory -Branch $integration)
    $stopKind = Get-TeamGateStopKind -Records $attempts
    if ($stopKind) {
        $sentence = $stopSentence
        if ($stopKind -eq "refs_moved") {
            # The stop names the ref again, every run, until the lead looked.
            $last = @($attempts | Where-Object { [string](Get-TeamProperty -InputObject $_ -Name "result" -Default "") -eq "refs_moved" } | Select-Object -Last 1)[0]
            $sentence = "$movedSentence (" + ((@(Get-TeamProperty -InputObject $last -Name "refs" -Default @()) | ForEach-Object { [string]$_ }) -join "; ") + ")"
        }
        $Outcome.Result = "durduruldu"
        [void]$script:stops.Add("${integration}: $sentence. Baktıktan sonra: scripts\team\integrate.ps1 -ClearGateStop")
        foreach ($task in $tasks) {
            $reason = [string](Get-TeamProperty -InputObject $task -Name "reason" -Default "")
            if ($reason -notmatch "TEAM_PROTOCOL 10") { Set-TaskNote -Task $task -Reason (($reason + " | " + $sentence).Trim(" ", "|")) }
        }
        return 8
    }

    $tip = [string]$Item.Tip
    $baseSha = Get-TeamRevision -RepoRoot $repoRoot -Revision "refs/heads/$Base"

    if (-not $Item.Ahead -and $null -eq $Item.Green) {
        # Only a branch that a moved ref had stopped comes here (the lead has just cleared it).
        $Outcome.Result = "kapıya girecek bir şey yok"
        [void]$Outcome.Lines.Add("$integration, $Base'in ilerisinde değil ve üzerinde yeşil kapı kaydı yok; işlere dokunulmadı: $ids")
        return 0
    }
    # A run that died after main moved: the gate was green on this very commit and it is on main.
    if (-not $Item.Ahead) {
        $onMain = [string](Get-TeamProperty -InputObject $Item.Green -Name "main" -Default "")
        if (-not $onMain) { $onMain = $baseSha }
        foreach ($task in $tasks) { Set-TaskNote -Task $task -State "awaiting_release" -Sha $onMain -Reason "kapı yeşil: $tip; $Base $onMain (önceki koşu tamamlandı)" }
        $Outcome.Result = "yayın bekliyor"
        [void]$Outcome.Lines.Add("önceki koşu tamamlandı: kapı $tip üzerinde yeşildi ve $Base üzerinde; işler 'yayın bekliyor': $ids")
        [void]$Outcome.Lines.Add("${Base}: $onMain")
        return 0
    }

    $tree = Reset-TeamGateWorktree -RepoRoot $repoRoot -Branch $integration -At $tip
    $candidate = $tip
    $logFile = [string](Get-TeamProperty -InputObject $Item.Green -Name "log" -Default "")
    # A gate that was green on this very commit is not run again (main could not be moved then).
    $gated = ($null -ne $Item.Green) -and (Test-TeamAncestor -RepoRoot $repoRoot -Ancestor $baseSha -Of $tip)

    if (-not $gated) {
        # ---- 2. main into the integration branch, before anything is judged
        $merge = Invoke-TeamGit -WorkingDirectory $tree -Arguments @("merge", "--no-ff", "-m", "merge: $Base into $integration (before the gate)", $baseSha)
        if (-not $merge.Success) {
            $conflicted = @((Invoke-TeamGit -WorkingDirectory $tree -Arguments @("diff", "--name-only", "--diff-filter=U")).StdOut -split "`r?`n" | Where-Object { $_.Trim() })
            [void](Invoke-TeamGit -WorkingDirectory $tree -Arguments @("merge", "--abort"))
            $reason = "main ile çakışma"
            if (@($conflicted).Count -gt 0) { $reason += ": " + ((@($conflicted) | Select-Object -First 8) -join ", ") }
            foreach ($task in $tasks) { Set-TaskNote -Task $task -State "stopped" -Reason $reason }
            Write-TeamGateRecord -Directory $Item.Directory -Number $number -Record ([pscustomobject]@{ n = $number; branch = $integration; at = (Get-TeamTimestamp); result = "conflict"; sha = $tip })
            $Outcome.Result = "main ile çakışma"
            [void]$Outcome.Lines.Add("$reason; hiçbir şey zorlanmadı, işler durduruldu: $ids")
            return 5
        }
        $beforeLead = Invoke-TreeGit -Tree $tree -Arguments @("rev-parse", "HEAD")
        $Item.Attempt = $number

        # ---- the model of the lead's run, before anything is built for it: with no open model
        # there is no run, and so no environment and no gate. Not an attempt: nothing is counted.
        $pick = Select-LeadModel
        if ($null -eq $pick.Model) {
            $reason = "lead koşusu başlatılmadı - " + (Get-NoModelSentence -Pick $pick)
            foreach ($task in $tasks) { Set-TaskNote -Task $task -Reason $reason }
            $Outcome.Result = "lead koşusu yapılamadı"
            [void]$Outcome.Lines.Add($reason)
            return 7
        }

        # ---- 3. the worktree's own environment, BEFORE the lead's run: a uv or a pnpm that cannot
        # build it must not cost a model run every half hour. Nothing is counted: nothing was paid for.
        $failure = Invoke-EnvironmentBuild -Tree $tree -Outcome $Outcome
        if ($failure) {
            $reason = "ortam kurulamadı: $failure; lead koşusu ve kapı koşmadı"
            foreach ($task in $tasks) { Set-TaskNote -Task $task -Reason $reason }
            $Outcome.Result = "ortam kurulamadı"
            [void]$Outcome.Lines.Add($reason)
            return 10
        }
        # What the build scribbled on a tracked file (a lock file rewritten) is not the lead's change
        # and is not what is gated: the tree is put back on the commit. What git ignores stays.
        [void](Reset-TeamGateWorktree -RepoRoot $repoRoot -Branch $integration -At $beforeLead)

        # ---- 4. the lead's wiring run, and the check of what it changed
        $notes = @(Get-TeamLeadMergeNotes -Tasks $tasks -RepoRoot $repoRoot)
        $card = New-TeamLeadMergeCard -CycleId $Item.CycleId -Branch $integration -Notes $notes
        # ONE cap for the whole of it, however many models are tried: the lock is held meanwhile.
        $deadline = [datetime]::UtcNow.AddMinutes($LeadMinutes)
        # The run has Bash and shares the repository: what it must not move is read before and after.
        # The worktree's diff alone does not see a commit made on main from here (main checked out nowhere).
        $watched = @("refs/heads/$Base", "refs/heads/$integration", "refs/remotes/$Remote/$Base")
        if (-not (Test-Path -LiteralPath $Item.Directory)) { [void](New-Item -ItemType Directory -Force -Path $Item.Directory) }
        $leadTry = 0
        $limitSaid = ""
        $finished = $null
        $result = $null
        $movedRefs = @()
        while ($true) {
            $leadTry++
            # The model of THIS try: the lead's own, or - when that one is limited - the next open one down.
            $runModel = [string]$pick.Model
            if ($pick.Lowered) { [void]$Outcome.Lines.Add("model düşürüldü: $($pick.Intended) -> $runModel (limit)") }
            $arguments = Get-TeamRunArguments -RoleFile $leadRoleFile -Model $runModel -PrefixArguments $ClaudePrefixArguments
            $refsBefore = Get-TeamRefValues -RepoRoot $repoRoot -Names $watched
            # The run's whole process tree is held from its first moment - the command is created
            # suspended, put into its job and only then let run: when the run ends, what it left
            # going ("tests are running in the background") is stopped BEFORE anything is read.
            $start = Start-TeamHeldRun -FilePath $ClaudePath -Arguments $arguments -Prompt $card -WorkingDirectory $tree
            if (-not $start.Held) {
                # No job, no run: the command never ran one instruction. Nothing was paid for, nothing is counted.
                [void](Reset-TeamGateWorktree -RepoRoot $repoRoot -Branch $integration -At $tip)
                $reason = "lead koşusu başlatılmadı - süreç ağacı tutulamadı ($($start.Why)); komut hiç çalışmadı, sayılmadı, bir sonraki adım dener"
                foreach ($task in $tasks) { Set-TaskNote -Task $task -Reason $reason }
                $Outcome.Result = "lead koşusu yapılamadı"
                [void]$Outcome.Lines.Add($reason)
                return 7
            }
            $leadRun = $start.Run
            $job = $start.Job
            # From here a lead run is paid for: an attempt that breaks after this is counted (the caller's catch).
            $Item.LeadRan = $true
            $finished = Wait-TeamLeadRun -Run $leadRun -Job $job -Deadline $deadline
            $movedRefs = @(Compare-TeamRefValues -Before $refsBefore -After (Get-TeamRefValues -RepoRoot $repoRoot -Names $watched))
            $result = Read-TeamRunResult -StdOut $finished.StdOut -ExitCode $finished.ExitCode -StdErr $finished.StdErr -Model $runModel
            $stem = Join-Path $Item.Directory $(if ($leadTry -gt 1) { "gate-$number-lead-$leadTry" } else { "gate-$number-lead" })
            [System.IO.File]::WriteAllText("$stem.json", [string]$finished.StdOut, $utf8)
            if ($result.Text) { [System.IO.File]::WriteAllText("$stem.md", $result.Text.TrimEnd() + "`n", $utf8) }
            $limitedNow = ([bool]$result.UsageLimited -and -not $result.Ok -and -not $finished.TimedOut)
            $leadLine = ("lead koşusu: {0} sn, tahmini {1:0.00} USD, model {2}" -f [int]$finished.Seconds, [double]$result.CostUsd, $runModel)
            if ($limitedNow) { $leadLine += " - Max kullanım limiti" + $(if ($result.ResetsAt) { " (sıfırlanma $($result.ResetsAt))" } else { "" }) + "; sayılmadı" }
            [void]$Outcome.Lines.Add($leadLine)
            if ($result.Ok -and [bool]$result.Substituted) { [void]$Outcome.Lines.Add("model düşürüldü (araç): $runModel -> $(if ($result.RanModel) { $result.RanModel } else { '?' })") }
            if (@($finished.Left).Count -gt 0) {
                [void]$Outcome.Lines.Add("lead koşusu bittiğinde arkasında $(@($finished.Left).Count) süreç bıraktı ($(($finished.Left | Sort-Object -Unique) -join ', ')); fark okunmadan önce durduruldu - yazacakları commit edilmedi")
            }
            if (@($movedRefs).Count -gt 0 -or -not $limitedNow) { break }

            # The usage limit: never an attempt, never waited for (the lock is held). What it closes is
            # closed for the rest of this step; the same wiring run is started again AT ONCE one model
            # down - one try per model of the chain, under the one deadline.
            [void](Reset-TeamGateWorktree -RepoRoot $repoRoot -Branch $integration -At $beforeLead)
            [void](Set-TeamModelClosed -Limited $script:limitedModels -Result $result -RunModel $runModel)
            $limitSaid = "Max kullanım limiti" + $(if ($result.ResetsAt) { " (sıfırlanma $($result.ResetsAt))" } else { "" })
            $pick = Select-LeadModel
            if ($null -ne $pick.Model -and $leadTry -lt @(Get-TeamModelChain).Count -and [datetime]::UtcNow -lt $deadline) { continue }
            [void](Reset-TeamGateWorktree -RepoRoot $repoRoot -Branch $integration -At $tip)
            $reason = "lead koşusu: " + $(if ($null -eq $pick.Model) { Get-NoModelSentence -Pick $pick } else { "$limitSaid; sayılmadı, beklenmedi, bir sonraki adım dener" })
            foreach ($task in $tasks) { Set-TaskNote -Task $task -Reason $reason }
            $Outcome.Result = "lead koşusu yapılamadı"
            [void]$Outcome.Lines.Add($reason)
            return 7
        }

        if (@($movedRefs).Count -gt 0) {
            # Before anything else is judged, and whatever the run answered. This step does NOT put the
            # ref back: it cannot tell the run's move from a person's, and a branch is never moved backwards here.
            [void](Reset-TeamGateWorktree -RepoRoot $repoRoot -Branch $integration -At $tip)
            $said = @($movedRefs | ForEach-Object { "$($_.Name): $(if ($_.Before) { $_.Before } else { 'yoktu' }) -> $(if ($_.After) { $_.After } else { 'silindi' })" })
            $reason = "$movedSentence. " + ($said -join "; ") + "; koşu reddedildi, kapı koşmadı. Taşınan dal GERİ ALINMADI"
            Write-TeamGateRecord -Directory $Item.Directory -Number $number -Record ([pscustomobject]@{ n = $number; branch = $integration; at = (Get-TeamTimestamp); result = "refs_moved"; sha = $tip; refs = @($said) })
            foreach ($task in $tasks) { Set-TaskNote -Task $task -Reason $reason }
            $Outcome.Result = "DAL YER DEĞİŞTİRDİ"
            [void]$Outcome.Lines.Add($reason)
            foreach ($ref in $movedRefs) {
                Write-Host "  A REF MOVED DURING THE LEAD'S RUN: $($ref.Name) was '$($ref.Before)' and is '$($ref.After)'. The run is refused and $integration is stopped; the ref was NOT put back."
                if ($ref.Before -and $ref.After) { [void]$Outcome.Lines.Add("lead baktıktan sonra, taşıyan lead koşusuysa geri almak için: git update-ref $($ref.Name) $($ref.Before) $($ref.After)") }
                [void]$script:risks.Add("$($ref.Name) kapıdan geçmeden yer değiştirdi ($($ref.Before) -> $($ref.After)); $Remote'e itilmeden önce bakılmalı")
            }
            [void]$script:stops.Add("${integration}: $movedSentence (" + ($said -join "; ") + "). Baktıktan sonra: scripts\team\integrate.ps1 -ClearGateStop")
            return 13
        }

        if (-not $result.Ok -or $finished.TimedOut) {
            [void](Reset-TeamGateWorktree -RepoRoot $repoRoot -Branch $integration -At $tip)
            $why = if ($finished.TimedOut) { "süre doldu ($LeadMinutes dk)" } else { [string]$result.Why }
            $reason = "lead koşusu sonuç vermedi ($why); kapı koşmadı"
            $stopped = Add-Strike -Item $Item -Number $number -Result "lead_failed" -More @{ sha = $tip; why = $why }
            if ($stopped) { $reason += " | " + $stopSentence; [void]$script:stops.Add("${integration}: $stopSentence") }
            foreach ($task in $tasks) { Set-TaskNote -Task $task -Reason $reason }
            $Outcome.Result = "lead koşusu sonuç vermedi"
            [void]$Outcome.Lines.Add($reason)
            if ($stopped) { return 8 }
            return 7
        }

        # What the run changed is COMMITTED first and judged second: the allow-list is held against
        # the diff of the commit that would be gated, never against the working tree as it looked a
        # moment before staging. The run's process tree is already stopped (Wait-TeamLeadRun); a
        # writer no job could hold, writing between a look at the tree and `git add`, was committed
        # unseen and reached main (cycle-auto-integrate, the fourth inspection: 2 of 10 runs).
        $named = @($notes | ForEach-Object { @($_.Named) } | Where-Object { $_ })
        $changed = @()
        $refused = @()
        $shrunk = @()
        $headAfter = Invoke-TreeGit -Tree $tree -Arguments @("rev-parse", "HEAD")
        if (-not (Test-TeamAncestor -RepoRoot $repoRoot -Ancestor $beforeLead -Of $headAfter)) { $refused = @("(HEAD dalın dışına taşındı)") }
        else {
            if ((Invoke-TreeGit -Tree $tree -Arguments @("status", "--porcelain")).Length -gt 0) {
                [void](Invoke-TreeGit -Tree $tree -Arguments @("add", "-A"))
                [void](Invoke-TreeGit -Tree $tree -Arguments @("commit", "--quiet", "-m", "integrate: the lead's merge wiring for $ids"))
            }
            $candidate = Invoke-TreeGit -Tree $tree -Arguments @("rev-parse", "HEAD")
            $changed = @(Get-TeamCommittedFiles -Worktree $tree -From $beforeLead -To $candidate)
            $refused = @(Get-TeamLeadRefusedFiles -Changed $changed -NamedFiles $named)
            # The gate script is the wiring's to GROW: a step, an exit-code check or the last word taken out of it
            # would make the gate green on less. Judged from the committed texts, never by the model.
            $gateFile = "scripts/quality-gate.ps1"
            if (@($changed | Where-Object { ([string]$_ -replace '\\', '/') -eq $gateFile }).Count -gt 0) {
                $shrunk = @(Get-TeamGateShrink -Before (Get-TeamFileAtCommit -RepoRoot $tree -Commit $beforeLead -Path $gateFile) -After (Get-TeamFileAtCommit -RepoRoot $tree -Commit $candidate -Path $gateFile))
            }
        }
        if (@($refused).Count -gt 0 -or @($shrunk).Count -gt 0) {
            # The gate worktree is this step's own: what the run wrote is discarded, whole.
            [void](Reset-TeamGateWorktree -RepoRoot $repoRoot -Branch $integration -At $tip)
            $said = @()
            if (@($refused).Count -gt 0) { $said += "lead koşusu izinsiz dosya değiştirdi: " + ((@($refused) | Select-Object -First 8) -join ", ") }
            if (@($shrunk).Count -gt 0) { $said += "lead koşusu kapıyı küçülttü ($gateFile): " + ((@($shrunk) | Select-Object -First 8) -join "; ") }
            $reason = ($said -join "; ") + "; koşu reddedildi, hiçbir şey birleştirilmedi"
            $stopped = Add-Strike -Item $Item -Number $number -Result "lead_refused" -More @{ sha = $tip; files = @($refused); gate_lines = @($shrunk) }
            if ($stopped) { $reason += " | " + $stopSentence; [void]$script:stops.Add("${integration}: $stopSentence") }
            foreach ($task in $tasks) { Set-TaskNote -Task $task -Reason $reason }
            $Outcome.Result = "lead koşusu reddedildi"
            [void]$Outcome.Lines.Add($reason)
            if ($stopped) { return 8 }
            return 7
        }
        if (@($changed).Count -gt 0) { [void]$Outcome.Lines.Add("lead'in bağladığı dosyalar (commit edilen fark): " + ($changed -join ", ")) }
        else { [void]$Outcome.Lines.Add("lead hiçbir dosya değiştirmedi") }
        # The tree is put back on the commit that was judged: what was written after it (a writer
        # the step could not stop) is neither committed nor what the gate runs on.
        [void](Reset-TeamGateWorktree -RepoRoot $repoRoot -Branch $integration -At $candidate)

        # The wiring changed a file the environment is built from: it is built again, on the commit
        # that will be gated. A build that fails NOW is the wiring's doing and a lead run was paid
        # for: a failed attempt, counted - and the integration branch is not moved.
        $inputs = @($changed | Where-Object { Test-TeamEnvironmentInput -Path ([string]$_) })
        if (@($inputs).Count -gt 0) {
            $failure = Invoke-EnvironmentBuild -Tree $tree -Outcome $Outcome -When " (lead'in bağlamasından sonra)"
            if ($failure) {
                [void](Reset-TeamGateWorktree -RepoRoot $repoRoot -Branch $integration -At $tip)
                $why = "lead'in değiştirdiği " + ($inputs -join ", ") + " ile ortam kurulamadı: $failure"
                $reason = "$why; kapı koşmadı, hiçbir şey birleştirilmedi"
                $stopped = Add-Strike -Item $Item -Number $number -Result "lead_failed" -More @{ sha = $tip; why = $why }
                if ($stopped) { $reason += " | " + $stopSentence; [void]$script:stops.Add("${integration}: $stopSentence") }
                foreach ($task in $tasks) { Set-TaskNote -Task $task -Reason $reason }
                $Outcome.Result = "ortam kurulamadı (lead'in bağlamasından sonra)"
                [void]$Outcome.Lines.Add($reason)
                if ($stopped) { return 8 }
                return 7
            }
            [void](Reset-TeamGateWorktree -RepoRoot $repoRoot -Branch $integration -At $candidate)
        }

        # What will be gated IS the integration branch: main and the wiring are on it from here.
        if ($candidate -ne $tip) {
            $moved = Move-TeamBranchForward -RepoRoot $repoRoot -Branch $integration -To $candidate -Expected $tip
            if (-not $moved.Moved) {
                $reason = "entegrasyon dalı ilerletilemedi: $($moved.Detail)"
                foreach ($task in $tasks) { Set-TaskNote -Task $task -Reason $reason }
                $Outcome.Result = "entegrasyon dalı ilerletilemedi"
                [void]$Outcome.Lines.Add($reason)
                return 11
            }
        }

        # ---- 5. the full gate, in the environment built above
        $logFile = "$relative/gate-$number.log"
        $logPath = Join-Path $Item.Directory "gate-$number.log"
        $gateScript = if ($GatePath) { $GatePath } else { Join-Path $tree "scripts\quality-gate.ps1" }
        $ran = Invoke-TeamGate -GatePath $gateScript -WorkingDirectory $tree -LogPath $logPath -TimeoutMinutes $GateMinutes
        $logText = if (Test-Path -LiteralPath $logPath) { [System.IO.File]::ReadAllText($logPath, [System.Text.Encoding]::UTF8) } else { "" }
        $gate = Read-TeamGateLog -Text $logText -ExitCode $ran.ExitCode -TimedOut $ran.TimedOut
        [void]$Outcome.Lines.Add("kapı: $candidate üzerinde, $($ran.Seconds) sn, çıkış kodu $($ran.ExitCode); kayıt: $logFile")

        if (-not $gate.Green) {
            # ---- 6 (red). Nothing reaches main.
            $strikes = (Get-TeamGateStrikes -Records @(Get-TeamGateRecords -Directory $Item.Directory -Branch $integration)) + 1
            $reason = Get-TeamGateReason -Gate $gate -LogFile $logFile -Attempt $strikes
            $files = @{}
            foreach ($task in $tasks) {
                $files[[string]$task.id] = @()
                $taskBranch = [string](Get-TeamProperty -InputObject $task -Name "branch" -Default "")
                if ($taskBranch -and (Test-TeamBranch -RepoRoot $repoRoot -Branch $taskBranch)) {
                    # Inside the task's AREA only: a worker's branch is opened from the cycle's -Base (the lead's
                    # branch, which can be ahead of main), so its diff against main also holds files that are not its own.
                    $area = @(Get-TeamProperty -InputObject $task -Name "area" -Default @())
                    try {
                        $files[[string]$task.id] = @(Get-TeamChangedFiles -RepoRoot $repoRoot -Branch $taskBranch -Base $baseSha |
                                Where-Object { Test-TeamPathInsideArea -Path $_ -Area $area })
                    }
                    catch { }
                }
            }
            $blamed = @(Get-TeamGateBlamedTasks -FailureText $gate.FailureText -TaskFiles $files)
            # What the branch waits for now, in the words every task carries.
            $waitsFor = if (@($blamed).Count -gt 0) { "dal $Base'e bütün olarak girer: " + ($blamed -join ", ") + " düzeltilip yeniden birleşene kadar hiçbiri kapıya girmez" }
            else { "aynı commit yeniden kapıya girmez: yeni bir commit ya da lead'in -ClearGateStop'u beklenir" }
            # The record carries the verdict, marked NOT applied: it is written before the queue is, and
            # if the queue's write fails (a task changed in the store during the gate's hour) the next
            # run writes the verdict from here instead of waiting in silence on a commit "already judged".
            $stopped = Add-Strike -Item $Item -Number $number -Result "red" -More @{
                sha = $candidate; steps = @($gate.FailedSteps); first = [string]$gate.FirstFailure; log = $logFile
                applied = $false; blamed = @($blamed); reason = $reason; waits = $waitsFor
            }
            Set-GateVerdict -Tasks $tasks -Reason $reason -WaitsFor $waitsFor -Blamed $blamed -Stopped $stopped
            $Item.Verdict = $number
            $Outcome.Result = "kapı kırmızı"
            [void]$Outcome.Lines.Add($reason)
            if (@($gate.FailedSteps).Count -gt 0) { [void]$Outcome.Lines.Add("kırılan adımlar: " + ($gate.FailedSteps -join "; ")) }
            if ($gate.FirstFailure) { [void]$Outcome.Lines.Add("ilk kırılan test: $($gate.FirstFailure)") }
            [void]$Outcome.Lines.Add("geri verilen: " + $(if (@($blamed).Count -gt 0) { $blamed -join ", " } else { "yok (kapı hiçbir işin dosyasını adlandırmadı)" }) + "; $Base değişmedi")
            [void]$Outcome.Lines.Add($waitsFor)
            if ($stopped) { [void]$script:stops.Add("${integration}: $stopSentence. Baktıktan sonra: scripts\team\integrate.ps1 -ClearGateStop"); return 8 }
            return 6
        }
        # Green is recorded BEFORE main is touched: a run that dies here is finished by the next one.
        Write-TeamGateRecord -Directory $Item.Directory -Number $number -Record ([pscustomobject]@{ n = $number; branch = $integration; at = (Get-TeamTimestamp); result = "green"; sha = $candidate; main = ""; log = $logFile })
    }
    else {
        $number = [int](Get-TeamProperty -InputObject $Item.Green -Name "n" -Default $number)
        [void]$Outcome.Lines.Add("kapı $candidate üzerinde daha önce yeşildi ($logFile); yeniden koşulmadı")
    }

    # ---- 5 (green). main goes forward by a --no-ff merge of exactly what was gated.
    $baseNow = Get-TeamRevision -RepoRoot $repoRoot -Revision "refs/heads/$Base"
    if (-not (Test-TeamAncestor -RepoRoot $repoRoot -Ancestor $baseNow -Of $candidate)) {
        $reason = "kapı koşarken $Base ilerledi; kapıdan geçen $candidate onu içermiyor, bir sonraki adım yeniden dener"
        foreach ($task in $tasks) { Set-TaskNote -Task $task -Reason $reason }
        $Outcome.Result = "$Base ilerledi"
        [void]$Outcome.Lines.Add($reason)
        return 11
    }
    # What the gate left behind in its worktree (a formatted file, a rewritten lock file) is not
    # what was gated: the tree is put back on the gated commit before the merge is made in it.
    [void](Reset-TeamGateWorktree -RepoRoot $repoRoot -Branch $integration -At $candidate)
    [void](Invoke-TreeGit -Tree $tree -Arguments @("checkout", "--detach", "--quiet", $baseNow))
    try {
        [void](Invoke-TreeGit -Tree $tree -Arguments @("merge", "--no-ff", "-m", "merge: $integration (gated $candidate) into $Base", $candidate))
        $merged = Invoke-TreeGit -Tree $tree -Arguments @("rev-parse", "HEAD")
    }
    finally { [void](Invoke-TeamGit -WorkingDirectory $tree -Arguments @("checkout", "--detach", "--quiet", $candidate)) }
    if (-not (Test-TeamSameTree -RepoRoot $repoRoot -A $merged -B $candidate)) {
        throw "the merge for $Base does not hold exactly what was gated ($candidate); nothing was moved"
    }
    $forward = Move-TeamBranchForward -RepoRoot $repoRoot -Branch $Base -To $merged -Expected $baseNow
    if (-not $forward.Moved) {
        $reason = "kapı yeşil ($candidate) ama $Base ilerletilemedi: $($forward.Detail); hiçbir şey zorlanmadı, bir sonraki adım kapıyı yeniden koşmadan dener"
        foreach ($task in $tasks) { Set-TaskNote -Task $task -Reason $reason }
        $Outcome.Result = "$Base ilerletilemedi"
        [void]$Outcome.Lines.Add($reason)
        return 11
    }
    Write-TeamGateRecord -Directory $Item.Directory -Number $number -Record ([pscustomobject]@{ n = $number; branch = $integration; at = (Get-TeamTimestamp); result = "green"; sha = $candidate; main = $merged; log = $logFile })

    $code = 0
    $hasRemote = (Invoke-TeamGit -WorkingDirectory $repoRoot -Arguments @("remote", "get-url", $Remote)).Success
    if (-not $hasRemote) { [void]$script:risks.Add("$Remote yok: $Base yalnız bu makinede ilerledi, itilmedi") ; [void]$Outcome.Lines.Add("$Remote yok; $Base itilmedi") }
    else {
        # A push that fails or hangs must not undo what is true: the work IS on main here.
        $pushed = $false
        $said = ""
        try {
            $push = Invoke-TeamGit -WorkingDirectory $repoRoot -Arguments @("push", $Remote, "refs/heads/${Base}:refs/heads/${Base}") -TimeoutSeconds 600
            $pushed = [bool]$push.Success
            $said = (($push.StdOut + " " + $push.StdErr) -replace '\s+', ' ').Trim()
        }
        catch { $said = $_.Exception.Message }
        if ($pushed) { [void]$Outcome.Lines.Add("$Base $Remote'e itildi") }
        else {
            $code = 9
            [void]$script:risks.Add("$Base bu makinede $merged oldu ama $Remote'e itilemedi: $said")
            [void]$Outcome.Lines.Add("$Remote'e İTİLEMEDİ: $said")
        }
    }
    foreach ($task in $tasks) { Set-TaskNote -Task $task -State "awaiting_release" -Sha $merged -Reason "kapı yeşil: $candidate; $Base $merged; kayıt: $logFile" }
    $Outcome.Result = "yayın bekliyor"
    [void]$Outcome.Lines.Add("kapı yeşil; ${Base}: $merged (kapıdan geçen: $candidate)")
    [void]$Outcome.Lines.Add("işler 'yayın bekliyor' (yayın bu adımda YAPILMAZ): $ids")
    return $code
}

# ------------------------------------------------------------------ the step's own lock (API mode)
# Two gates never run at once on one machine. In file mode the cycle's lock does that (one writer).

$stepLock = $null
if ($useApi) {
    $stepLock = Enter-TeamStepLock -Path $stepLockFile -Machine $Machine
    if (-not $stepLock.Taken) {
        $whose = if ($stepLock.Holder -gt 0) { "pid $($stepLock.Holder), $($stepLock.Since)" } else { "kilit dosyası açılamadı: $stepLockFile" }
        Write-Skipped -Sentence "kilit bu makinenin başka bir entegrasyon adımında ($whose); bu adım hiçbir şey çalıştırmadı"
        Write-Host "the integration step's own lock is held ($whose); nothing was done"
        exit 3
    }
    if ($stepLock.TookOver) { [void]$risks.Add("bu makinenin ölmüş bir entegrasyon adımının kilidi devralındı ($($stepLock.TookOver))") }
}

$exitCode = 0
$cycleLockTaken = $false
$lockCycle = "integrate-" + [string]$pending[0].Branch
try {
    if (-not $decision.MayRun) {
        Write-Skipped -Sentence "kilit $($decision.Holder) makinesinde ($($decision.Since)); bu adım hiçbir şey çalıştırmadı"
        Write-Host "the lock is held by $($decision.Holder) since $($decision.Since); nothing was done"
        exit 3
    }
    if ($decision.Kind -eq "stale") { [void]$risks.Add("bayat kilit devralındı: $($decision.Holder), $($decision.Since)") }
    if ($decision.Kind -eq "dead") { [void]$risks.Add("bu makinenin ölmüş bir koşusunun kilidi devralındı ($($decision.Since))") }

    # ---- Docker (shared, and the gate needs it)
    if (@($pending | Where-Object { $_.Ahead -and $null -eq $_.Unapplied }).Count -gt 0) {
        $dockerUp = $false
        if ($dockerTool) {
            try { $dockerUp = [bool](Invoke-NativeProcess -FilePath $dockerTool -Arguments @("info") -TimeoutSeconds 90).Success } catch { $dockerUp = $false }
        }
        if (-not $dockerUp) {
            Write-Skipped -Sentence "Docker çalışmıyor; kapı koşmadı, hiçbir şey değişmedi (Docker Desktop açılınca bir sonraki adım dener)"
            Write-Host "Docker is not running (Docker calismiyor); nothing was done"
            exit 4
        }
    }

    # ---- the cycle's lock, held for the whole gate - unless the step runs beside the cycle
    if ($besideCycle) { Write-Host "beside the cycle (-BesideCycle): the cycle's lock is not taken; the step's own lock is held" }
    elseif ($useApi) {
        $acquired = Set-TeamLockApi -Store $apiStore -Machine $Machine -CycleId $lockCycle -TakeoverDead ($decision.Kind -eq "dead")
        if (-not [bool]$acquired.acquired) {
            Write-Skipped -Sentence "kilit $($acquired.holder) makinesinde ($($acquired.since)); bu adım hiçbir şey çalıştırmadı"
            Write-Host "the lock was taken by $($acquired.holder); nothing was done"
            exit 3
        }
        $cycleLockTaken = $true
    }
    else {
        Write-TeamJson -Path $lockPath -Document (New-TeamLock -Machine $Machine -CycleId $lockCycle -Now $started)
        $cycleLockTaken = $true
    }

    foreach ($item in $pending) {
        $outcome = New-Outcome -Item $item
        $code = 12
        try { $code = Invoke-BranchIntegration -Item $item -Outcome $outcome }
        catch {
            $said = [string]$_.Exception.Message
            $outcome.Result = "beklenmeyen hata"
            Write-Host "  unexpected error: $said"
            [void]$outcome.Lines.Add("beklenmeyen hata: $said")
            [void]$risks.Add("$($item.Branch): beklenmeyen hata; $Base bu adımda yalnız yeşil kapıdan sonra ilerler: $said")
            # A lead run was paid for and the attempt has no record yet: it is counted, so the same
            # error is not bought again every half hour. Two stop the branch (TEAM_PROTOCOL 10).
            if ($item.LeadRan -and $item.Attempt -gt 0 -and -not (Test-Path -LiteralPath (Join-Path $item.Directory "gate-$($item.Attempt).json"))) {
                try {
                    if (Add-Strike -Item $item -Number $item.Attempt -Result "error" -More @{ sha = [string]$item.Tip; why = $said }) {
                        $code = 8
                        [void]$stops.Add("$($item.Branch): $stopSentence. Baktıktan sonra: scripts\team\integrate.ps1 -ClearGateStop")
                    }
                }
                catch { [void]$risks.Add("$($item.Branch): deneme kaydı yazılamadı: $($_.Exception.Message)") }
            }
        }
        try {
            foreach ($drop in @(Save-Queue -Tasks @($item.Tasks))) {
                # The cycle moved this task while the gate ran: its word stands, this result is not written.
                $line = "kapı koşarken döngü değiştirdi, sonucu YAZILMADI: $($drop.Id) ($($drop.Why))"
                [void]$outcome.Lines.Add($line)
                Write-Host "  $line"
            }
            if ($item.Verdict -gt 0) {
                # The queue has the red gate's verdict: the record says so, and no later run writes it again.
                $record = Read-TeamJson -Path (Join-Path $item.Directory "gate-$($item.Verdict).json")
                Set-TeamProperty -InputObject $record -Name "applied" -Value $true
                Write-TeamGateRecord -Directory $item.Directory -Number $item.Verdict -Record $record
            }
        }
        catch {
            $code = 12
            [void]$risks.Add("$($item.Branch): kuyruk yazılamadı ($($_.Exception.Message)); bir sonraki adım kaldığı yerden tamamlar")
        }
        Write-Host "$($item.Branch): $($outcome.Result) (code $code)"
        if ($exitCode -eq 0) { $exitCode = [int]$code }
    }
    Save-Reports
}
finally {
    if ($cycleLockTaken) {
        if ($useApi) {
            try { Clear-TeamLockApi -Store $apiStore -Machine $Machine -CycleId $lockCycle }
            catch { Write-Host "the lock was not released in the queue store: $($_.Exception.Message)" }
        }
        else { Write-TeamJson -Path $lockPath -Document (New-TeamLockReleased) }
    }
    # On every path - green, red, a refused wiring, a moved ref, a limit, an error, an early stop.
    try { Exit-TeamStepLock -Lock $stepLock }
    catch { Write-Host "the step's own lock was not released ($stepLockFile): $($_.Exception.Message); the next step takes it over once this process is gone" }
}
exit $exitCode
