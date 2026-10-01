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

      1. the team lock is taken (the file or the Cloud Core's, the cycle's own functions) as the
         cycle 'integrate-<branch>', and released in `finally`;
      2. in .claude/worktrees/gate/<branch> - never the main checkout, the owner works there -
         main is merged into the branch. A conflict stops the tasks ('main ile çakışma') and
         forces nothing;
      3. a LEAD run (a fresh `claude -p`, .claude/agents/lead.md, its tools, in that worktree)
         gets the "For the lead at merge" sections of the tasks' newest worker and inspector
         reports and wires the shared files. THIS script checks its diff: docs/, .github/, team/,
         scripts/quality-gate.ps1, state/BUILD_STATE.json and the files a section names; one
         file outside that refuses the run and nothing is merged. What passed is committed on
         the integration branch. The run shares the repository, so main, the integration branch
         and the remote's main are read before and after it: one that MOVED refuses the run,
         is named with both shas, and stops the branch at once (it is not put back here);
      4. the worktree gets its own environment (uv sync, pnpm install) and the FULL gate runs
         there, its log kept as team/reports/<cycle>/gate-<n>.log. Docker's dev stack is shared
         and must be up: if it is not, the step stops with 'Docker çalışmıyor' and changes nothing;
      5. GREEN (exit code 0 AND the gate's last word): main gets a --no-ff merge naming the
         gated sha, is pushed, and the tasks become 'awaiting_release' with main's sha.
         RED: nothing reaches main; the failing steps and the first failing test go into the
         report and into each task's reason; the tasks whose files the failure names go back to
         'returned', the others stay 'merged'. Two failed attempts on one branch stop it until
         the lead looks (-ClearGateStop).

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
    branch's tip is not run again.

    Exit codes: 0 done or nothing to do; 2 the queue breaks the protocol; 3 the lock is held;
    4 Docker is down; 5 conflict with main; 6 the gate is red; 7 the lead's run was refused or
    gave no result; 8 the branch is stopped (two failed attempts); 9 main moved but the push
    failed; 10 the worktree's environment could not be built; 11 a branch could not be moved
    forward; 12 an unexpected error; 13 a ref moved during the lead's run.

.PARAMETER GatePath
    The gate script to run in the gate worktree. Empty (the default) is the worktree's own
    scripts\quality-gate.ps1. The tests name scripts/tests/lib/fake-gate.ps1 here.

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
    # 0 is no cap: every step is idempotent (TEAM_PROTOCOL section 7).
    [double]$GateMinutes = 0,
    [double]$LeadMinutes = 0,
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
    [string]$QueueToken = ""
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

$useApi = [bool]$QueueUrl
if ($useApi -and -not $QueueToken) { throw "-QueueUrl needs -QueueToken: the path of a file holding the token" }
$apiStore = $null
if ($useApi) { $apiStore = New-TeamApiStore -Url $QueueUrl -TokenFile $QueueToken }

function Save-Queue {
    if ($useApi) { Save-TeamQueueApi -Store $apiStore -Queue $script:queue }
    else { Write-TeamJson -Path $queuePath -Document $script:queue }
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
    if ($ahead -and -not $movedStop) {
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
            Directory = $directory; Green = $green
        })
}
if (@($pending).Count -eq 0) {
    Write-Host "nothing to integrate: no merged task on an integration branch that is ahead of $Base and may be gated now"
    exit 0
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

# ------------------------------------------------------------------ the lock (as the cycle takes it)

$lock = $null
if ($useApi) { $lock = Get-TeamLockApi -Store $apiStore }
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
        elseif ($null -ne $item.Green) { Write-Host "    would finish an earlier run: the gate was green on this commit; $Base would get it without a second gate" }
        else {
            Write-Host "    would merge $Base into it in $(Get-TeamGateWorktreePath -RepoRoot $repoRoot -Branch $item.Branch)"
            Write-Host "    would run the lead's wiring run there, check its diff and commit it"
            Write-Host "    would build the worktree's environment and run $(if ($GatePath) { $GatePath } else { 'scripts\quality-gate.ps1' })"
            Write-Host "    green: $Base gets a --no-ff merge, is pushed to $Remote, the tasks become awaiting_release; red: nothing reaches $Base"
        }
    }
    exit 0
}

if (-not $decision.MayRun) {
    [void]$stops.Add("kilit $($decision.Holder) makinesinde ($($decision.Since)); bu adım hiçbir şey çalıştırmadı")
    foreach ($item in $pending) { (New-Outcome -Item $item).Result = "kilit başka koşuda" }
    Save-Reports
    Write-Host "the lock is held by $($decision.Holder) since $($decision.Since); nothing was done"
    exit 3
}
if ($decision.Kind -eq "stale") { [void]$risks.Add("bayat kilit devralındı: $($decision.Holder), $($decision.Since)") }
if ($decision.Kind -eq "dead") { [void]$risks.Add("bu makinenin ölmüş bir koşusunun kilidi devralındı ($($decision.Since))") }

# ------------------------------------------------------------------ Docker (shared, and the gate needs it)

if (@($pending | Where-Object { $_.Ahead }).Count -gt 0) {
    $dockerUp = $false
    if ($dockerTool) {
        try { $dockerUp = [bool](Invoke-NativeProcess -FilePath $dockerTool -Arguments @("info") -TimeoutSeconds 90).Success } catch { $dockerUp = $false }
    }
    if (-not $dockerUp) {
        [void]$stops.Add("Docker çalışmıyor; kapı koşmadı, hiçbir şey değişmedi (Docker Desktop açılınca bir sonraki adım dener)")
        foreach ($item in $pending) { (New-Outcome -Item $item).Result = "Docker çalışmıyor" }
        Save-Reports
        Write-Host "Docker is not running (Docker calismiyor); nothing was done"
        exit 4
    }
}

$lockCycle = "integrate-" + [string]$pending[0].Branch
if ($useApi) {
    $taken = Set-TeamLockApi -Store $apiStore -Machine $Machine -CycleId $lockCycle -TakeoverDead ($decision.Kind -eq "dead")
    if (-not [bool]$taken.acquired) {
        [void]$stops.Add("kilit $($taken.holder) makinesinde ($($taken.since)); bu adım hiçbir şey çalıştırmadı")
        foreach ($item in $pending) { (New-Outcome -Item $item).Result = "kilit başka koşuda" }
        Save-Reports
        Write-Host "the lock was taken by $($taken.holder); nothing was done"
        exit 3
    }
}
else { Write-TeamJson -Path $lockPath -Document (New-TeamLock -Machine $Machine -CycleId $lockCycle -Now $started) }

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

        # ---- 3. the lead's wiring run, and the check of what it changed
        $notes = @(Get-TeamLeadMergeNotes -Tasks $tasks -RepoRoot $repoRoot)
        $card = New-TeamLeadMergeCard -CycleId $Item.CycleId -Branch $integration -Notes $notes
        $leadModel = Get-TeamRoleModel -TeamRoot $teamDir -Role "lead" -Fallback $Model
        $arguments = Get-TeamRunArguments -RoleFile $leadRoleFile -Model $leadModel -PrefixArguments $ClaudePrefixArguments
        $deadline = if ($LeadMinutes -gt 0) { [datetime]::UtcNow.AddMinutes($LeadMinutes) } else { [datetime]::MaxValue }
        # The run has Bash and shares the repository: what it must not move is read before and after.
        # The worktree's diff alone does not see a commit made on main from here (main checked out nowhere).
        $watched = @("refs/heads/$Base", "refs/heads/$integration", "refs/remotes/$Remote/$Base")
        $refsBefore = Get-TeamRefValues -RepoRoot $repoRoot -Names $watched
        $finished = Wait-TeamRun -Run (Start-TeamRun -FilePath $ClaudePath -Arguments $arguments -Prompt $card -WorkingDirectory $tree) -Deadline $deadline
        $movedRefs = @(Compare-TeamRefValues -Before $refsBefore -After (Get-TeamRefValues -RepoRoot $repoRoot -Names $watched))
        $result = Read-TeamRunResult -StdOut $finished.StdOut -ExitCode $finished.ExitCode -StdErr $finished.StdErr
        if (-not (Test-Path -LiteralPath $Item.Directory)) { [void](New-Item -ItemType Directory -Force -Path $Item.Directory) }
        [System.IO.File]::WriteAllText((Join-Path $Item.Directory "gate-$number-lead.json"), [string]$finished.StdOut, $utf8)
        if ($result.Text) { [System.IO.File]::WriteAllText((Join-Path $Item.Directory "gate-$number-lead.md"), $result.Text.TrimEnd() + "`n", $utf8) }
        $leadLine = ("lead koşusu: {0} sn, tahmini {1:0.00} USD{2}" -f [int]$finished.Seconds, [double]$result.CostUsd, $(if ($leadModel) { ", model $leadModel" } else { "" }))
        [void]$Outcome.Lines.Add($leadLine)

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
            if ($result.UsageLimited) {
                # The one stop the team has (owner, 2026-09-30): not a failed attempt, nothing is counted.
                $reason = "lead koşusu: Max kullanım limiti" + $(if ($result.ResetsAt) { " (sıfırlanma $($result.ResetsAt))" } else { "" }) + "; sayılmadı, bir sonraki adım dener"
                foreach ($task in $tasks) { Set-TaskNote -Task $task -Reason $reason }
                $Outcome.Result = "lead koşusu yapılamadı"
                [void]$Outcome.Lines.Add($reason)
                return 7
            }
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

        $changed = @(Get-TeamWorktreeChanges -Worktree $tree -Since $beforeLead)
        $named = @($notes | ForEach-Object { @($_.Named) } | Where-Object { $_ })
        $refused = @(Get-TeamLeadRefusedFiles -Changed $changed -NamedFiles $named)
        $headAfter = Invoke-TreeGit -Tree $tree -Arguments @("rev-parse", "HEAD")
        if (-not (Test-TeamAncestor -RepoRoot $repoRoot -Ancestor $beforeLead -Of $headAfter)) { $refused = @($refused) + "(HEAD dalın dışına taşındı)" }
        if (@($refused).Count -gt 0) {
            # The gate worktree is this step's own: what the run wrote is discarded, whole.
            [void](Reset-TeamGateWorktree -RepoRoot $repoRoot -Branch $integration -At $tip)
            $reason = "lead koşusu izinsiz dosya değiştirdi: " + ((@($refused) | Select-Object -First 8) -join ", ") + "; koşu reddedildi, hiçbir şey birleştirilmedi"
            $stopped = Add-Strike -Item $Item -Number $number -Result "lead_refused" -More @{ sha = $tip; files = @($refused) }
            if ($stopped) { $reason += " | " + $stopSentence; [void]$script:stops.Add("${integration}: $stopSentence") }
            foreach ($task in $tasks) { Set-TaskNote -Task $task -Reason $reason }
            $Outcome.Result = "lead koşusu reddedildi"
            [void]$Outcome.Lines.Add($reason)
            if ($stopped) { return 8 }
            return 7
        }
        if ((Invoke-TreeGit -Tree $tree -Arguments @("status", "--porcelain")).Length -gt 0) {
            [void](Invoke-TreeGit -Tree $tree -Arguments @("add", "-A"))
            [void](Invoke-TreeGit -Tree $tree -Arguments @("commit", "--quiet", "-m", "integrate: the lead's merge wiring for $ids"))
        }
        if (@($changed).Count -gt 0) { [void]$Outcome.Lines.Add("lead'in bağladığı dosyalar: " + ($changed -join ", ")) }
        else { [void]$Outcome.Lines.Add("lead hiçbir dosya değiştirmedi") }
        $candidate = Invoke-TreeGit -Tree $tree -Arguments @("rev-parse", "HEAD")

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

        # ---- 4. the worktree's own environment, then the full gate
        $plan = @(Get-TeamGateEnvironmentPlan -Worktree $tree -UvPath $uvTool -PnpmPath $pnpmTool)
        foreach ($step in $plan) {
            $watch = [System.Diagnostics.Stopwatch]::StartNew()
            $failure = ""
            if (-not $step.FilePath) { $failure = "araç bulunamadı" }
            else {
                try {
                    $ran = Invoke-NativeProcess -FilePath $step.FilePath -Arguments @($step.Arguments) -WorkingDirectory $step.Directory -TimeoutSeconds 3600
                    if (-not $ran.Success) { $failure = "çıkış kodu $($ran.ExitCode)" }
                }
                catch { $failure = $_.Exception.Message }
            }
            $watch.Stop()
            if ($failure) {
                $reason = "ortam kurulamadı: $($step.Name) ($failure); kapı koşmadı"
                foreach ($task in $tasks) { Set-TaskNote -Task $task -Reason $reason }
                $Outcome.Result = "ortam kurulamadı"
                [void]$Outcome.Lines.Add($reason)
                return 10
            }
            [void]$Outcome.Lines.Add("ortam: $($step.Name): $([int]$watch.Elapsed.TotalSeconds) sn")
        }

        $logFile = "$relative/gate-$number.log"
        $logPath = Join-Path $Item.Directory "gate-$number.log"
        $gateScript = if ($GatePath) { $GatePath } else { Join-Path $tree "scripts\quality-gate.ps1" }
        $ran = Invoke-TeamGate -GatePath $gateScript -WorkingDirectory $tree -LogPath $logPath -TimeoutMinutes $GateMinutes
        $logText = if (Test-Path -LiteralPath $logPath) { [System.IO.File]::ReadAllText($logPath, [System.Text.Encoding]::UTF8) } else { "" }
        $gate = Read-TeamGateLog -Text $logText -ExitCode $ran.ExitCode -TimedOut $ran.TimedOut
        [void]$Outcome.Lines.Add("kapı: $candidate üzerinde, $($ran.Seconds) sn, çıkış kodu $($ran.ExitCode); kayıt: $logFile")

        if (-not $gate.Green) {
            # ---- 5 (red). Nothing reaches main.
            $stopped = Add-Strike -Item $Item -Number $number -Result "red" -More @{ sha = $candidate; steps = @($gate.FailedSteps); first = [string]$gate.FirstFailure; log = $logFile }
            $strikes = Get-TeamGateStrikes -Records @(Get-TeamGateRecords -Directory $Item.Directory -Branch $integration)
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
            foreach ($task in $tasks) {
                $own = $reason + " | " + $waitsFor
                if ($stopped) { $own += " | " + $stopSentence }
                if ($blamed -contains [string]$task.id) { Set-TaskNote -Task $task -State "returned" -Reason $own }
                else { Set-TaskNote -Task $task -Reason $own }
            }
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
    if ((Get-TeamRevision -RepoRoot $repoRoot -Revision "$merged^{tree}") -ne (Get-TeamRevision -RepoRoot $repoRoot -Revision "$candidate^{tree}")) {
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

$exitCode = 0
try {
    foreach ($item in $pending) {
        $outcome = New-Outcome -Item $item
        $code = 12
        try { $code = Invoke-BranchIntegration -Item $item -Outcome $outcome }
        catch {
            $outcome.Result = "beklenmeyen hata"
            Write-Host "  unexpected error: $($_.Exception.Message)"
            [void]$outcome.Lines.Add("beklenmeyen hata: $($_.Exception.Message)")
            [void]$risks.Add("$($item.Branch): beklenmeyen hata; $Base bu adımda yalnız yeşil kapıdan sonra ilerler: $($_.Exception.Message)")
        }
        try { Save-Queue }
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
    if ($useApi) {
        try { Clear-TeamLockApi -Store $apiStore -Machine $Machine -CycleId $lockCycle }
        catch { Write-Host "the lock was not released in the queue store: $($_.Exception.Message)" }
    }
    else { Write-TeamJson -Path $lockPath -Document (New-TeamLockReleased) }
}
exit $exitCode
