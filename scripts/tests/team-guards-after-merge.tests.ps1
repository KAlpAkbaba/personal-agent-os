<#
.SYNOPSIS
    The fast guards run right after a merge into an integration branch (card guards-after-every-merge,
    the owner 2026-10-06: "bu kapının daha hızlı kontrolünü sağlamanın bir yolu var mı").

.DESCRIPTION
    Of the reds that restarted the 95-minute gate on 2026-10-06, most were cross-cutting guards
    that run in seconds and only fail once two cards meet on the integration branch. This suite
    holds:

      * Get-TeamGuardLastMerge (scripts/lib/TeamGuards.ps1): the card a red guard names is the
        LAST task branch merged into the branch - not main's "before the gate" merge, not the
        lead's wiring commit;
      * scripts/team/guards.ps1 -AfterMerge: green says so and names the last merge; red names
        the guard and the last merged card; the RESULT file carries `last_merge`;
      * scripts/team/integrate.ps1: after the lead's wiring and BEFORE the gate the guards of
        the gate tree's team/guards.json run. Red: the gate is NOT started, the merge stays on
        the branch, the last merged card goes back ('returned') with the guard's name, and the
        attempt is a red record (the existing rule: the branch waits for that card's fix).
        Green: the guards ran, the report says so, the gate runs as before.

    Everything runs in a git repository made for the test, with fake guards (PowerShell suites
    the test writes) and the fakes of team-integrate.tests.ps1 in place of the gate and the lead.
    The real gate is never run; this repository's branches and team/ files are never written.

    Run: powershell -NoProfile -File scripts\tests\team-guards-after-merge.tests.ps1
#>

[CmdletBinding()]
param([string]$Filter = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamGuards.ps1")

$script:Failures = 0
$script:Passes = 0
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$sandboxes = New-Object System.Collections.ArrayList
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    if ($Filter -and $Name -notmatch $Filter) { return }
    try { & $Body; $script:Passes++; Write-Host "  PASS  $Name" }
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

function Invoke-SandboxGit {
    param([string]$Root, [string[]]$Arguments)
    $result = Invoke-TeamGit -WorkingDirectory $Root -Arguments $Arguments
    if (-not $result.Success) { throw "git $($Arguments -join ' '): $($result.StdErr)" }
    return $result.StdOut.Trim()
}

# A fake guard: a PowerShell suite that is red when any file under src/ holds the word BREAKS-<id>.
# Two cards that each write their own file make it red only when the one that holds the word is merged.
$fakeGuard = @'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$word = "BREAKS-" + [System.IO.Path]::GetFileNameWithoutExtension([System.IO.Path]::GetFileNameWithoutExtension($PSCommandPath))
$hit = @(Get-ChildItem -LiteralPath (Join-Path $root "src") -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { (Get-Content -LiteralPath $_.FullName -Raw) -match [regex]::Escape($word) })
if (@($hit).Count -gt 0) { Write-Host "FAIL $word in $($hit[0].Name)"; exit 1 }
Write-Host "PASS"
exit 0
'@

function New-Sandbox {
    <#
        main with the team scripts, team/guards.json naming two fake guards (guard-alpha,
        guard-beta), and for each -Work item a task branch (one file in its area, holding
        -Content) merged into integrate/c1 by the cycle's own Merge-TeamBranch, in order.
    #>
    param([hashtable[]]$Work = @(), [switch]$NoList)
    $root = Join-Path $env:TEMP ("pagentos-gam-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    $tools = "$root-tools"
    [void]$sandboxes.Add($root); [void]$sandboxes.Add($tools)
    foreach ($folder in @("scripts\lib", "scripts\team", "scripts\tests\lib", ".claude\agents", "team", "src\area", "docs")) {
        [void](New-Item -ItemType Directory -Force -Path (Join-Path $root $folder))
    }
    [void](New-Item -ItemType Directory -Force -Path $tools)
    foreach ($name in @("NativeProcess.ps1", "TeamQueue.ps1", "TeamRun.ps1", "HttpJson.ps1", "TeamIntegrate.ps1", "TeamGuards.ps1")) {
        Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\lib\$name") -Destination (Join-Path $root "scripts\lib\$name")
    }
    Copy-Item -Path (Join-Path $repoRoot "scripts\team\*.ps1") -Destination (Join-Path $root "scripts\team")
    foreach ($name in @("fake-claude.ps1", "fake-gate.ps1")) {
        Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\tests\lib\$name") -Destination (Join-Path $root "scripts\tests\lib\$name")
    }
    foreach ($role in @("lead", "researcher", "integrator", "worker", "inspector")) {
        Copy-Item -LiteralPath (Join-Path $repoRoot ".claude\agents\$role.md") -Destination (Join-Path $root ".claude\agents\$role.md")
    }
    foreach ($guard in @("alpha", "beta")) {
        [System.IO.File]::WriteAllText((Join-Path $root "scripts\tests\guard-$guard.tests.ps1"), $fakeGuard, $utf8)
    }
    if (-not $NoList) {
        Write-TeamJson -Path (Join-Path $root "team\guards.json") -Document ([pscustomobject]@{
                version = 1
                guards  = @(
                    [pscustomobject]@{ id = "guard-alpha"; kind = "powershell"; path = "scripts/tests/guard-alpha.tests.ps1"; label = "alpha koruyucusu" },
                    [pscustomobject]@{ id = "guard-beta"; kind = "powershell"; path = "scripts/tests/guard-beta.tests.ps1"; label = "beta koruyucusu" })
            })
    }
    Set-Content -LiteralPath (Join-Path $root ".gitignore") -Value ".claude/worktrees/`nteam/reports/" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $root "src\area\README.txt") -Value "the area" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $root "docs\DECISIONS.md") -Value "# decisions" -Encoding ASCII
    Write-TeamJson -Path (Join-Path $root "team\lock.json") -Document (New-TeamLockReleased)
    [void](Invoke-SandboxGit -Root $root -Arguments @("init", "-q", "-b", "main"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.name", "team test"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.email", "team@example.invalid"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "core.autocrlf", "false"))
    Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document ([pscustomobject]@{ version = 1; tasks = @() })
    [void](Invoke-SandboxGit -Root $root -Arguments @("add", "-A"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-m", "the sandbox"))

    $tasks = New-Object System.Collections.ArrayList
    foreach ($item in @($Work)) {
        $id = [string]$item.Id
        $area = [string]$item.Area
        $branch = "team/c1/worker-$id"
        [void](Invoke-SandboxGit -Root $root -Arguments @("branch", $branch, "main"))
        $tree = Join-Path $tools "wt-$id"
        [void](Invoke-SandboxGit -Root $root -Arguments @("worktree", "add", "-q", $tree, $branch))
        $target = Join-Path $tree (($area -replace "/", "\") + "\$id.txt")
        [void](New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target))
        $content = if ($item.ContainsKey("Content")) { [string]$item.Content } else { "work on $id" }
        Set-Content -LiteralPath $target -Value $content -Encoding ASCII
        [void](Invoke-SandboxGit -Root $tree -Arguments @("add", "-A"))
        [void](Invoke-SandboxGit -Root $tree -Arguments @("commit", "-q", "-m", "work on $id"))
        [void](Invoke-SandboxGit -Root $root -Arguments @("worktree", "remove", "--force", $tree))
        $merge = Merge-TeamBranch -RepoRoot $root -CycleId "c1" -Branch $branch -Base "main"
        if (-not $merge.Merged) { throw "the sandbox could not merge ${branch}: $($merge.Detail)" }
        $task = [pscustomobject]@{
            id = $id; title = "the task $id"; roadmap_row = "row"; state = "merged"; area = @($area)
            branch = $branch; worktree = ""; assignee = ""; reports = @(); budget = [pscustomobject]@{ max_usd = 0 }
            created_at = "2026-10-06T00:00:00Z"; updated_at = "2026-10-06T00:00:00Z"
            integration_branch = "integrate/c1"; sha = (Invoke-SandboxGit -Root $root -Arguments @("rev-parse", $branch))
        }
        [void]$tasks.Add($task)
    }
    Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document ([pscustomobject]@{ version = 1; tasks = @($tasks.ToArray()) })
    Set-Content -LiteralPath (Join-Path $tools "docker-ok.cmd") -Value "@exit /b 0" -Encoding ASCII
    foreach ($tool in @("uv", "pnpm")) { Set-Content -LiteralPath (Join-Path $tools "$tool.cmd") -Value "@exit /b 0" -Encoding ASCII }
    return $root
}

function Get-IntegrationTree {
    <# The integration branch's own worktree, as the cycle's Merge-TeamBranch left it. #>
    param([string]$Root)
    $list = Invoke-SandboxGit -Root $Root -Arguments @("worktree", "list", "--porcelain")
    $path = ""
    foreach ($line in @($list -split "`r?`n")) {
        if ($line -like "worktree *") { $path = $line.Substring(9) }
        if ($line -eq "branch refs/heads/integrate/c1") { return ($path -replace "/", "\") }
    }
    throw "no worktree holds integrate/c1"
}

function Invoke-Guards {
    <# scripts/team/guards.ps1 -AfterMerge on a tree: ExitCode, Output, Result (the -OutFile document or $null). #>
    param([string]$Root, [string]$Tree)
    $out = Join-Path "$Root-tools" ("guards-" + [guid]::NewGuid().ToString("N").Substring(0, 8) + ".json")
    # Read as UTF-8: the runner writes UTF-8 to a pipe whatever the console's code page is.
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $powershell
    $psi.Arguments = ConvertTo-NativeArgumentLine -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        (Join-Path $Root "scripts\team\guards.ps1"), "-Worktree", $Tree, "-AfterMerge", "-OutFile", $out)
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    $process = [System.Diagnostics.Process]::Start($psi)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit(300000)) { try { $process.Kill() } catch { }; throw "guards.ps1 did not end within 300 s" }
    $process.WaitForExit()
    $result = if (Test-Path -LiteralPath $out) { Read-TeamJson -Path $out } else { $null }
    return [pscustomobject]@{ ExitCode = $process.ExitCode; Output = ($stdout.Result + $stderr.Result); Result = $result }
}

function Invoke-Integrate {
    param([string]$Root)
    $tools = "$Root-tools"
    $set = @{
        PAGENTOS_FAKE_GATE_SCENARIO = "green"; PAGENTOS_FAKE_GATE_LOG = (Join-Path $tools "gate.log")
        PAGENTOS_FAKE_CLAUDE_SCENARIO = "approve"; PAGENTOS_FAKE_CLAUDE_LOG = (Join-Path $tools "lead.log")
    }
    foreach ($name in @($set.Keys)) { Set-Item -Path "Env:\$name" -Value $set[$name] }
    try {
        $command = "& '" + (Join-Path $Root "scripts\team\integrate.ps1") + "' -Machine 'MAIL'" +
        " -GatePath '" + (Join-Path $Root "scripts\tests\lib\fake-gate.ps1") + "' -GateMinutes 3 -LeadMinutes 3" +
        " -DockerPath '" + (Join-Path $tools "docker-ok.cmd") + "' -UvPath '" + (Join-Path $tools "uv.cmd") + "' -PnpmPath '" + (Join-Path $tools "pnpm.cmd") + "'" +
        " -ClaudePath '$powershell' -ClaudePrefixArguments '-NoProfile','-ExecutionPolicy','Bypass','-File','" + (Join-Path $Root "scripts\tests\lib\fake-claude.ps1") + "'" +
        " -StepLockPath '" + (Join-Path $tools "integrate-step.lock") + "'"
        $result = Invoke-NativeProcess -FilePath $powershell -Arguments @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ($command + "; exit `$LASTEXITCODE")) `
            -WorkingDirectory $Root -TimeoutSeconds 600 -SuccessExitCodes @(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13)
    }
    finally { foreach ($name in @($set.Keys)) { Remove-Item -Path "Env:\$name" -ErrorAction SilentlyContinue } }
    $gateLog = Join-Path $tools "gate.log"
    $report = Join-Path $Root "team\reports\c1-integrate.md"
    return [pscustomobject]@{
        ExitCode  = $result.ExitCode; Output = ($result.StdOut + $result.StdErr)
        Queue     = (Read-TeamJson -Path (Join-Path $Root "team\queue.json"))
        GateCalls = @(if (Test-Path -LiteralPath $gateLog) { @(Get-Content -LiteralPath $gateLog | Where-Object { $_.Trim() }) })
        Report    = $(if (Test-Path -LiteralPath $report) { [System.IO.File]::ReadAllText($report, [System.Text.Encoding]::UTF8) } else { "" })
        Records   = @(Get-ChildItem -LiteralPath (Join-Path $Root "team\reports\c1") -Filter "gate-*.json" -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match '^gate-\d+\.json$' } | Sort-Object { [int]($_.BaseName.Substring(5)) } | ForEach-Object { Read-TeamJson -Path $_.FullName })
    }
}

function Get-Task { param($Queue, [string]$Id) return @(@($Queue.tasks) | Where-Object { $_.id -eq $Id })[0] }

function Test-TeamGuardOnMain { param([string]$Root, [string]$File) return [bool](Invoke-TeamGit -WorkingDirectory $Root -Arguments @("cat-file", "-e", "main:$File")).Success }

$twoCards = @(@{ Id = "task-one"; Area = "src/a" }, @{ Id = "task-two"; Area = "src/b"; Content = "BREAKS-guard-beta" })
$twoGreen = @(@{ Id = "task-one"; Area = "src/a" }, @{ Id = "task-two"; Area = "src/b" })

Write-Host ""
Write-Host "the card a red guard names: the last task branch merged"

Test-Case "the last merge is the newest team branch merged, not main's merge nor a wiring commit after it" {
    $root = New-Sandbox -Work $twoGreen
    $tree = Get-IntegrationTree -Root $root
    # What the integration step does after the cycle: main merged in, then the lead's wiring committed.
    Set-Content -LiteralPath (Join-Path $root "docs\DECISIONS.md") -Value "# decisions`nmain moved" -Encoding ASCII
    [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-am", "main moved"))
    [void](Invoke-SandboxGit -Root $tree -Arguments @("merge", "--no-ff", "-q", "-m", "merge: main into integrate/c1 (before the gate)", "main"))
    Set-Content -LiteralPath (Join-Path $tree "docs\wired.md") -Value "wired" -Encoding ASCII
    [void](Invoke-SandboxGit -Root $tree -Arguments @("add", "-A"))
    [void](Invoke-SandboxGit -Root $tree -Arguments @("commit", "-q", "-m", "integrate: the lead's merge wiring for task-one, task-two"))
    $last = Get-TeamGuardLastMerge -Worktree $tree
    Assert-True -Condition ($null -ne $last) -Because "two task merges are on the branch"
    Assert-Equal -Expected "team/c1/worker-task-two" -Actual ([string]$last.branch) -Because "task-two was merged last"
    Assert-Equal -Expected (Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "team/c1/worker-task-two")) -Actual ([string]$last.tip) -Because "the merged tip is the branch's"
    Assert-True -Condition ([string]$last.merge -cmatch '^[0-9a-f]{40}$') -Because "the merge commit is named: $($last.merge)"
    Assert-Equal -Expected $null -Actual (Get-TeamGuardLastMerge -Worktree $root) -Because "main has no task merge"
}

Write-Host ""
Write-Host "scripts/team/guards.ps1 -AfterMerge on the integration branch"

Test-Case "a green merge runs every guard, exits 0 and says so, naming the last merged card" {
    $root = New-Sandbox -Work $twoGreen
    $ran = Invoke-Guards -Root $root -Tree (Get-IntegrationTree -Root $root)
    Assert-Equal -Expected 0 -Actual $ran.ExitCode -Because "all green: $($ran.Output)"
    Assert-True -Condition ($ran.Output -match "koruyucular yeşil" -and $ran.Output -match "2 koruyucu" -and $ran.Output -match "team/c1/worker-task-two") -Because "it says green, how many ran and the last merge: $($ran.Output)"
    Assert-Equal -Expected "green" -Actual ([string]$ran.Result.status) -Because "the result is green"
    Assert-Equal -Expected 2 -Actual @($ran.Result.rows).Count -Because "both guards ran (no API in this tree: no ruff row)"
    Assert-Equal -Expected "team/c1/worker-task-two" -Actual ([string]$ran.Result.last_merge.branch) -Because "the result carries the last merge"
}

Test-Case "a merge that breaks a guard exits 1 and names the guard and the merged card" {
    $root = New-Sandbox -Work $twoCards
    $ran = Invoke-Guards -Root $root -Tree (Get-IntegrationTree -Root $root)
    Assert-Equal -Expected 1 -Actual $ran.ExitCode -Because "a red guard: $($ran.Output)"
    Assert-True -Condition ($ran.Output -match "beta koruyucusu" -and $ran.Output -match "guard-beta") -Because "the guard by label and id: $($ran.Output)"
    Assert-True -Condition ($ran.Output -match "son birleşen: team/c1/worker-task-two") -Because "the last merged card: $($ran.Output)"
    Assert-True -Condition ($ran.Output -notmatch "alpha koruyucusu") -Because "the green guard is not said red: $($ran.Output)"
    $beta = @(@($ran.Result.rows) | Where-Object { $_.id -eq "guard-beta" })[0]
    Assert-Equal -Expected "red" -Actual ([string]$beta.outcome) -Because "the row is red"
}

Test-Case "an API tree gets a ruff row after the list; a tree without one does not" {
    $guard = New-TeamGuardRuffRow
    Assert-Equal -Expected "ruff" -Actual ([string]$guard.kind) -Because "its own kind"
    $command = Get-TeamGuardCommand -Guard $guard -Worktree "E:\tree" -Python "E:\py\python.exe"
    Assert-Equal -Expected "E:\tree\services\api" -Actual ([string]$command.WorkingDirectory) -Because "ruff runs where the gate runs it"
    Assert-Equal -Expected "-m ruff check . --no-cache" -Actual (@($command.Arguments) -join " ") -Because "uv run ruff check ., with no cache written into the tree"
    $read = Read-TeamGuardList -Path (Join-Path $repoRoot "team\guards.json")
    Assert-True -Condition (-not $read.refused) -Because "the real list is read: $($read.reason)"
    Assert-True -Condition (@($read.guards | Where-Object { $_.kind -eq "ruff" }).Count -eq 0) -Because "the list itself keeps its two kinds"
}

Write-Host ""
Write-Host "scripts/team/integrate.ps1: the guards after the wiring, before the gate"

Test-Case "a merge that breaks a guard: no gate, the merge stays, the last merged card goes back with the guard's name" {
    $root = New-Sandbox -Work $twoCards
    $tipBefore = Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "integrate/c1")
    $run = Invoke-Integrate -Root $root
    Assert-Equal -Expected 6 -Actual $run.ExitCode -Because "red, as a red gate: $($run.Output)"
    Assert-Equal -Expected 0 -Actual @($run.GateCalls).Count -Because "the gate is never started on a red guard"
    Assert-True -Condition ($run.Report -match "guard-beta" -and $run.Report -match "beta koruyucusu" -and $run.Report -match "task-two") -Because "the report names the guard and the card: $($run.Report)"
    $two = Get-Task -Queue $run.Queue -Id "task-two"
    $one = Get-Task -Queue $run.Queue -Id "task-one"
    Assert-Equal -Expected "returned" -Actual ([string]$two.state) -Because "the last merged card goes back"
    Assert-True -Condition ([string]$two.reason -match "guard-beta") -Because "its reason names the guard: $($two.reason)"
    Assert-Equal -Expected "merged" -Actual ([string]$one.state) -Because "the other card stays merged"
    Assert-True -Condition ([bool](Invoke-TeamGit -WorkingDirectory $root -Arguments @("merge-base", "--is-ancestor", $tipBefore, "integrate/c1")).Success) -Because "the merge stays on the branch"
    $last = $run.Records[@($run.Records).Count - 1]
    Assert-Equal -Expected "red" -Actual ([string]$last.result) -Because "the attempt is a red record"
    Assert-Equal -Expected "guards" -Actual ([string]$last.by) -Because "and says the guards made it red"
    Assert-Equal -Expected "task-two" -Actual (@($last.blamed) -join ",") -Because "the record keeps whom it named"
    Assert-True -Condition (-not (Test-TeamGuardOnMain -Root $root -File "src/b/task-two.txt")) -Because "nothing reached main"
}

Test-Case "a green merge: the guards run and the report says so, then the gate runs and main gets the work" {
    $root = New-Sandbox -Work $twoGreen
    $run = Invoke-Integrate -Root $root
    Assert-Equal -Expected 0 -Actual $run.ExitCode -Because "green: $($run.Output)"
    Assert-True -Condition ($run.Report -match "koruyucular yeşil" -and $run.Report -match "2 koruyucu") -Because "the report says the guards ran green: $($run.Report)"
    Assert-Equal -Expected 1 -Actual @($run.GateCalls).Count -Because "the gate ran once"
    Assert-True -Condition (Test-TeamGuardOnMain -Root $root -File "src/b/task-two.txt") -Because "main got the work"
}

Test-Case "a tree without a guard list says so and the gate runs as before" {
    $root = New-Sandbox -Work $twoGreen -NoList
    $run = Invoke-Integrate -Root $root
    Assert-Equal -Expected 0 -Actual $run.ExitCode -Because "no list stops nothing: $($run.Output)"
    Assert-True -Condition ($run.Report -match "koruyucu listesi yok") -Because "it is said: $($run.Report)"
    Assert-Equal -Expected 1 -Actual @($run.GateCalls).Count -Because "the gate ran"
}

Test-Case "a list the runner refuses: said in Turkish from the runner's own file, and the gate runs" {
    $root = New-Sandbox -Work $twoGreen
    # The integration branch gets a broken list (as a card could merge one): the runner exits 2.
    $tree = Get-IntegrationTree -Root $root
    [System.IO.File]::WriteAllText((Join-Path $tree "team\guards.json"), "{ not json", $utf8)
    [void](Invoke-SandboxGit -Root $tree -Arguments @("commit", "-q", "-am", "merge: team/c1/worker-broken into integrate/c1"))
    $run = Invoke-Integrate -Root $root
    Assert-Equal -Expected 0 -Actual $run.ExitCode -Because "a runner that cannot run stops nothing: $($run.Output)"
    Assert-True -Condition ($run.Report -match "koruyucular koşamadı \(çıkış kodu 2: koruyucular koşamadı: liste reddedildi: geçerli JSON değil") -Because "the runner's reason, in readable Turkish: $($run.Report)"
    Assert-Equal -Expected 1 -Actual @($run.GateCalls).Count -Because "the gate ran"
}

Write-Host ""
Write-Host "a guard under services/api/tests/integration/ runs on a database of its own (card guards-integration-tests-own-db)"

# 2026-10-07: the task's own Postgres test ran on the SHARED dev database (a revision no released
# tree knows), all three tests errored at the fixture, and the guard said the task was red. A guard
# that ran green would have migrated the shared database to the integration branch's head instead.

$docker = [string](@((Get-Command "docker" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1 | ForEach-Object { $_.Source }),
        "C:\Program Files\Docker\Docker\resources\bin\docker.exe") | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1)
$py = Get-TeamGuardDefaultPython -Worktree $repoRoot
$sharedUrl = "postgresql+psycopg://pagentos:secret@127.0.0.1:15432/pagentos"

function Invoke-DevPsql {
    <# One answer from the dev server's psql (database -Database), through the dev container. #>
    param([string]$Database, [string]$Sql)
    $r = Invoke-NativeProcess -FilePath $docker -Arguments @("exec", "pagentos-postgres", "psql", "-U", "pagentos", "-d", $Database, "-tAc", $Sql) -TimeoutSeconds 60
    if (-not $r.Success) { throw "psql: $($r.StdErr)" }
    return @(([string]$r.StdOut -split "`r?`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Get-GuardDatabases { return @(Invoke-DevPsql -Database "postgres" -Sql "SELECT datname FROM pg_database WHERE datname LIKE 'pagentos\_g\_%' ORDER BY datname") }

function New-DatabaseGuardSandbox {
    <# A git repository holding services/api/tests/integration/test_guard_db_<mode>.py for each mode: pass, fail, hang. #>
    $root = Join-Path $env:TEMP ("pagentos-gdb-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    [void]$sandboxes.Add($root)
    $folder = Join-Path $root "services\api\tests\integration"
    [void](New-Item -ItemType Directory -Force -Path $folder)
    $body = @{ pass = "pass"; fail = "raise AssertionError('red on purpose')"; hang = "time.sleep(300)" }
    foreach ($mode in @("pass", "fail", "hang")) {
        $text = "import os`nimport time`n`nimport psycopg`n`n`ndef test_own_database():`n" +
        "    url = os.environ[`"PAGENTOS_DATABASE_URL`"].replace(`"postgresql+psycopg://`", `"postgresql://`", 1)`n" +
        "    with psycopg.connect(url, connect_timeout=10) as conn:`n" +
        "        name = conn.execute(`"select current_database()`").fetchone()[0]`n" +
        "        assert name.startswith(`"pagentos_g_`"), name`n" +
        "        conn.execute(`"create table guard_probe (id int)`")`n" +
        "        $($body[$mode])`n"
        [System.IO.File]::WriteAllText((Join-Path $folder "test_guard_db_$mode.py"), $text, $utf8)
    }
    [void](Invoke-SandboxGit -Root $root -Arguments @("init", "-q", "-b", "main"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.name", "team test"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.email", "team@example.invalid"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("add", "-A"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-m", "the sandbox"))
    return $root
}

function New-DatabaseGuard { param([string]$Mode) return [pscustomobject]@{ id = "task-db-$Mode"; kind = "pytest"; path = "services/api/tests/integration/test_guard_db_$Mode.py"; label = "işin kendi testi kırmızı (test_guard_db_$Mode.py)" } }

Test-Case "the command of an integration guard names a pagentos_g_ database of its own; a unit guard's names none" {
    $integration = New-DatabaseGuard -Mode "pass"
    $command = Get-TeamGuardCommand -Guard $integration -Worktree "E:\tree" -Python "E:\py\python.exe" -DatabaseUrl $sharedUrl
    $environment = $command.Environment
    Assert-True -Condition ($null -ne $environment -and $environment.ContainsKey("PAGENTOS_DATABASE_URL")) -Because "the child is pointed at a database"
    $url = [string]$environment["PAGENTOS_DATABASE_URL"]
    Assert-True -Condition ($url -cmatch '^postgresql\+psycopg://pagentos:secret@127\.0\.0\.1:15432/pagentos_g_[a-z0-9]+$') -Because "the same server, a pagentos_g_ database: $url"
    Assert-True -Condition ($url -notmatch '/pagentos$') -Because "never the shared database"
    Assert-Equal -Expected ($url.Substring($url.LastIndexOf("/") + 1)) -Actual ([string]$command.Database) -Because "the command says which database it makes and drops"
    $again = Get-TeamGuardCommand -Guard $integration -Worktree "E:\tree" -Python "E:\py\python.exe" -DatabaseUrl $sharedUrl
    Assert-True -Condition ([string]$again.Database -ne [string]$command.Database) -Because "every run its own name"
    $unit = [pscustomobject]@{ id = "owner-error-language"; kind = "pytest"; path = "services/api/tests/unit/test_owner_error_language.py"; label = "unit" }
    $plain = Get-TeamGuardCommand -Guard $unit -Worktree "E:\tree" -Python "E:\py\python.exe" -DatabaseUrl $sharedUrl
    Assert-True -Condition (-not (@($plain.Environment.Keys) -contains "PAGENTOS_DATABASE_URL")) -Because "a unit guard gets no database variable"
    Assert-Equal -Expected "" -Actual ([string]$plain.Database) -Because "and no database"
}

Test-Case "against the dev server: a passing, a failing and a hung guard each leave no pagentos_g_ database and the shared revision as it was" {
    if (-not $docker) { throw "docker was not found: the dev stack's PostgreSQL is this case's evidence" }
    $base = Get-TeamGuardSettingsDatabaseUrl -Python $py -ApiRoot (Join-Path $repoRoot "services\api")
    Assert-True -Condition ($base -cmatch '^postgresql') -Because "the application's own database setting is read"
    $revisionBefore = @(Invoke-DevPsql -Database "pagentos" -Sql "SELECT version_num FROM alembic_version") -join ","
    $databasesBefore = @(Get-GuardDatabases)
    $root = New-DatabaseGuardSandbox
    $run = Invoke-TeamGuards -Worktree $root -List @((New-DatabaseGuard -Mode "pass"), (New-DatabaseGuard -Mode "fail"), (New-DatabaseGuard -Mode "hang")) -Python $py -HangSeconds 20 -DatabaseUrl $base
    $outcomes = @($run.rows | ForEach-Object { "$($_.id)=$($_.outcome)" }) -join " "
    Write-Host "        rows: $outcomes; pagentos alembic_version before: $revisionBefore"
    Assert-Equal -Expected "task-db-pass=green task-db-fail=red task-db-hang=hung" -Actual $outcomes -Because "the passing test ran on its own database, the failing one is red, the hung one stopped: $(@($run.rows | ForEach-Object { $_.detail }) -join ' | ')"
    $databasesAfter = @(Get-GuardDatabases)
    Write-Host "        pagentos_g_* after: $(@($databasesAfter).Count) (before: $(@($databasesBefore).Count))"
    Assert-Equal -Expected (@($databasesBefore) -join ",") -Actual (@($databasesAfter) -join ",") -Because "every guard database is dropped, success, failure or hang"
    $revisionAfter = @(Invoke-DevPsql -Database "pagentos" -Sql "SELECT version_num FROM alembic_version") -join ","
    Write-Host "        pagentos alembic_version after: $revisionAfter"
    Assert-Equal -Expected $revisionBefore -Actual $revisionAfter -Because "the shared database is never named"
    Assert-Equal -Expected 0 -Actual @(Invoke-DevPsql -Database "pagentos" -Sql "SELECT 1 FROM information_schema.tables WHERE table_name = 'guard_probe'").Count -Because "the tests' table never reached the shared database"
}

Test-Case "a guard whose database cannot be made says 'veritabanı açılamadı', not that the task's test is red" {
    $root = New-DatabaseGuardSandbox
    $closed = "postgresql+psycopg://pagentos:secret@127.0.0.1:1/pagentos"
    $run = Invoke-TeamGuards -Worktree $root -List @(New-DatabaseGuard -Mode "pass") -Python $py -DatabaseUrl $closed
    $row = @($run.rows)[0]
    Assert-Equal -Expected "no-database" -Actual ([string]$row.outcome) -Because "its own outcome: $($row.detail)"
    Assert-Equal -Expected "red" -Actual ([string]$run.status) -Because "a guard that could not run is not green"
    $line = Get-TeamGuardLine -Row $row
    Assert-True -Condition ($line -match "veritabanı açılamadı" -and $line -notmatch "işin kendi testi kırmızı") -Because "its own line: $line"
    Assert-True -Condition ($line -notmatch "secret" -and [string]$row.detail -notmatch "secret") -Because "the password is never said: $line / $($row.detail)"
}

foreach ($folder in @($sandboxes)) {
    if (Test-Path -LiteralPath $folder) {
        if (Test-Path -LiteralPath (Join-Path $folder ".git")) { [void](Invoke-TeamGit -WorkingDirectory $folder -Arguments @("worktree", "prune")) }
        Remove-Item -LiteralPath $folder -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "team-guards-after-merge: $($script:Passes) passed, $($script:Failures) failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
