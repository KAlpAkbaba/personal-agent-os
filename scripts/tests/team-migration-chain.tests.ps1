<#
.SYNOPSIS
    The migration rechain on merge (card migration-rechain-on-merge): scripts/lib/TeamMigrationChain.ps1
    and Merge-TeamBranch's use of it.

.DESCRIPTION
    Two halves. The rule is driven as a function with the fixture of 5 October 2026 (two workers
    both called their migration 0067 on 0066_watches) and its near misses. Then Merge-TeamBranch is
    run for real in a git repository made for the test: the rewrite is inside the ONE merge commit,
    the task branch never moves, a chain that cannot be made one-headed takes the merge back.

    The one-head check of the sandboxes is Get-TeamMigrationHeads (the sandboxes have no
    services/api to run uv+pytest in); the patterns it reads with are the pytest test's own.

    Run: powershell -NoProfile -File scripts\tests\team-migration-chain.tests.ps1
#>

[CmdletBinding()]
param([string]$Filter = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamMigrationChain.ps1")

$script:Failures = 0
$script:Passes = 0
$sandboxes = New-Object System.Collections.ArrayList

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
    if ($Expected -cne $Actual) { throw "$Because`n          expected: <$Expected>`n          actual  : <$Actual>" }
}

function Assert-True {
    param([bool]$Condition, [string]$Because)
    if (-not $Condition) { throw $Because }
}

$versions = "services/api/alembic/versions/"

function New-MigrationText {
    param([string]$Revision, [string]$Down, [string[]]$Tables = @(), [string]$DownLine = "")
    $downText = if ($DownLine) { $DownLine } elseif ($Down) { "down_revision: str | None = `"$Down`"" } else { "down_revision: str | None = None" }
    $ops = @($Tables | ForEach-Object { "    op.create_table(`"$_`", sa.Column(`"id`", sa.Integer(), primary_key=True))" })
    if ($ops.Count -eq 0) { $ops = @("    pass") }
    $lines = @(
        '"""A migration made for the test.', '', "Revision ID: $Revision", "Revises: $Down", '"""', '',
        'import sqlalchemy as sa', 'from alembic import op', '',
        "revision: str = `"$Revision`"", $downText, 'branch_labels = None', 'depends_on = None', '', '',
        'def upgrade() -> None:') + $ops + @('', '', 'def downgrade() -> None:', '    pass', '')
    return ($lines -join "`n")
}

function New-File { param([string]$Path, [string]$Text, [bool]$InMain = $false, [bool]$InBase = $false)
    return [pscustomobject]@{ Path = $Path; Text = $Text; InMain = $InMain; InBase = $InBase } }

function Get-MainFiles {
    return @(
        (New-File -Path ($versions + "20261002_0065_misheard_utterances.py") -Text (New-MigrationText -Revision "0065_misheard_utterances" -Down "" -Tables @("misheard_utterances")) -InMain $true),
        (New-File -Path ($versions + "20261003_0066_watches.py") -Text (New-MigrationText -Revision "0066_watches" -Down "0065_misheard_utterances" -Tables @("watches", "people")) -InMain $true)
    )
}

$wake = New-File -Path ($versions + "20261004_0067_wake_alarm_song.py") -Text (New-MigrationText -Revision "0067_wake_alarm_song" -Down "0066_watches" -Tables @("wake_alarm_songs"))
$transcripts = New-File -Path ($versions + "20261005_0067_conversation_transcripts.py") -Text (New-MigrationText -Revision "0067_conversation_transcripts" -Down "0066_watches" -Tables @("conversation_transcripts"))
$testPath = "services/api/tests/unit/test_conversation_transcripts_migration.py"
$testText = "BEFORE = `"0066_watches`"`nAFTER = `"0067_conversation_transcripts`"`nFILE = `"20261005_0067_conversation_transcripts`"`nOTHER = `"0066_watches_extra`"`n"

# ============================================================================ the rule

Write-Host ""
Write-Host "the rule (TeamMigrationChain.ps1)"

Test-Case "5 October: the second 0067 on 0066_watches becomes 0068 on 0067_wake_alarm_song, and its test's BEFORE follows" {
    $plan = Get-TeamMigrationChainPlan -Existing (@(Get-MainFiles) + $wake) -New @($transcripts) -ChangedTests @((New-File -Path $testPath -Text $testText))
    Assert-Equal -Expected "rechain" -Actual $plan.Action -Because $plan.Reason
    Assert-Equal -Expected "0067_wake_alarm_song" -Actual $plan.Tip -Because "the integration tip"
    Assert-Equal -Expected 1 -Actual @($plan.Renames).Count -Because "one migration"
    $rename = $plan.Renames[0]
    Assert-Equal -Expected ($versions + "20261005_0068_conversation_transcripts.py") -Actual $rename.NewPath -Because "only the NNNN prefix changes"
    Assert-Equal -Expected "0068_conversation_transcripts" -Actual $rename.NewRevision -Because "revision"
    Assert-Equal -Expected "0067_wake_alarm_song" -Actual $rename.NewDown -Because "down_revision is the tip"
    Assert-True -Condition ($rename.Text -match '(?m)^revision: str = "0068_conversation_transcripts"$') -Because "the revision line: $($rename.Text)"
    Assert-True -Condition ($rename.Text -match '(?m)^down_revision: str \| None = "0067_wake_alarm_song"$') -Because "the down_revision line"
    Assert-True -Condition ($rename.Text -match '(?m)^Revision ID: 0068_conversation_transcripts$') -Because "the docstring follows"
    Assert-Equal -Expected 1 -Actual @($plan.TestFiles).Count -Because "the branch's test"
    $test = $plan.TestFiles[0].Text
    Assert-True -Condition ($test -match 'BEFORE = "0067_wake_alarm_song"') -Because "BEFORE: $test"
    Assert-True -Condition ($test -match 'AFTER = "0068_conversation_transcripts"') -Because "AFTER: $test"
    Assert-True -Condition ($test -match 'FILE = "20261005_0068_conversation_transcripts"') -Because "the file stem: $test"
    Assert-True -Condition ($test -match 'OTHER = "0066_watches_extra"') -Because "a whole token only: $test"
    Assert-Equal -Expected 3 -Actual @($plan.TestRewrites).Count -Because "three tokens of one file"
    $chain = @(@(Get-MainFiles) + $wake + (New-File -Path $rename.NewPath -Text $rename.Text))
    Assert-Equal -Expected 1 -Actual @(Get-TeamMigrationHeads -Files $chain).Count -Because "one head afterwards"
}

Test-Case "branch B after A: 0068_mail_accounts on 0066_watches becomes 0069 on 0068_conversation_transcripts - one head" {
    $first = (Get-TeamMigrationChainPlan -Existing (@(Get-MainFiles) + $wake) -New @($transcripts)).Renames[0]
    $a = New-File -Path $first.NewPath -Text $first.Text
    $mail = New-File -Path ($versions + "20261005_0068_mail_accounts.py") -Text (New-MigrationText -Revision "0068_mail_accounts" -Down "0066_watches" -Tables @("mail_accounts"))
    $plan = Get-TeamMigrationChainPlan -Existing (@(Get-MainFiles) + $wake + $a) -New @($mail)
    Assert-Equal -Expected "rechain" -Actual $plan.Action -Because $plan.Reason
    Assert-Equal -Expected ($versions + "20261005_0069_mail_accounts.py") -Actual $plan.Renames[0].NewPath -Because "0069"
    Assert-Equal -Expected "0069_mail_accounts" -Actual $plan.Renames[0].NewRevision -Because "revision"
    Assert-Equal -Expected "0068_conversation_transcripts" -Actual $plan.Renames[0].NewDown -Because "down"
    $chain = @(@(Get-MainFiles) + $wake + $a + (New-File -Path $plan.Renames[0].NewPath -Text $plan.Renames[0].Text))
    Assert-Equal -Expected "0069_mail_accounts" -Actual (@(Get-TeamMigrationHeads -Files $chain) -join ",") -Because "one head"
}

Test-Case "a branch of two migrations is renumbered in its own order, each value replaced once" {
    $one = New-File -Path ($versions + "20261005_0067_people_notes.py") -Text (New-MigrationText -Revision "0067_people_notes" -Down "0066_watches" -Tables @("people_notes"))
    $two = New-File -Path ($versions + "20261005_0068_people_tags.py") -Text (New-MigrationText -Revision "0068_people_tags" -Down "0067_people_notes" -Tables @("people_tags"))
    $plan = Get-TeamMigrationChainPlan -Existing (@(Get-MainFiles) + $wake) -New @($two, $one)
    Assert-Equal -Expected "rechain" -Actual $plan.Action -Because $plan.Reason
    Assert-Equal -Expected "0068_people_notes>0067_wake_alarm_song,0069_people_tags>0068_people_notes" -Actual (@($plan.Renames | ForEach-Object { "$($_.NewRevision)>$($_.NewDown)" }) -join ",") -Because "the order of the chain, not of the list"
    Assert-True -Condition ($plan.Renames[1].Text -match 'down_revision: str \| None = "0068_people_notes"') -Because "the second names the first's NEW id: $($plan.Renames[1].Text)"
}

Test-Case "a branch already on the tip with the next number: nothing to do" {
    $next = New-File -Path ($versions + "20261005_0068_conversation_transcripts.py") -Text (New-MigrationText -Revision "0068_conversation_transcripts" -Down "0067_wake_alarm_song" -Tables @("people"))
    $plan = Get-TeamMigrationChainPlan -Existing (@(Get-MainFiles) + $wake) -New @($next)
    Assert-Equal -Expected "none" -Actual $plan.Action -Because $plan.Reason
    Assert-Equal -Expected "none" -Actual (Get-TeamMigrationChainPlan -Existing @(Get-MainFiles) -New @()).Action -Because "no new migration"
    $onMain = New-File -Path ($versions + "20261005_0067_conversation_transcripts.py") -Text $transcripts.Text
    Assert-Equal -Expected "none" -Actual (Get-TeamMigrationChainPlan -Existing @(Get-MainFiles) -New @($onMain)).Action -Because "nothing beside it on the integration branch"
}

Test-Case "two migrations the workers did not see of each other touching one table: stop, named" {
    $first = New-File -Path ($versions + "20261004_0067_people_book.py") -Text (New-MigrationText -Revision "0067_people_book" -Down "0066_watches" -Tables @("persons"))
    $mine = New-File -Path ($versions + "20261005_0067_persons_too.py") -Text (New-MigrationText -Revision "0067_persons_too" -Down "0066_watches" -Tables @("persons"))
    $plan = Get-TeamMigrationChainPlan -Existing (@(Get-MainFiles) + $first) -New @($mine)
    Assert-Equal -Expected "stop" -Actual $plan.Action -Because "same table"
    Assert-Equal -Expected "aynı tablo: persons (20261005_0067_persons_too.py, 20261004_0067_people_book.py)" -Actual $plan.Reason -Because "named"
    # A table main's migration made is touched every day: that is no stop.
    $onWatches = New-File -Path ($versions + "20261005_0067_people_more.py") -Text (New-MigrationText -Revision "0067_people_more" -Down "0066_watches" -Tables @("people"))
    Assert-Equal -Expected "rechain" -Actual (Get-TeamMigrationChainPlan -Existing (@(Get-MainFiles) + $wake) -New @($onWatches)).Action -Because "people is main's (0066_watches)"
}

Test-Case "a branched migration, a branch label and an id over 32 characters: stop" {
    $tuple = New-File -Path ($versions + "20261005_0067_merge_heads.py") -Text (New-MigrationText -Revision "0067_merge_heads" -Down "0066_watches" -DownLine 'down_revision = ("0066_watches", "0067_wake_alarm_song")')
    $plan = Get-TeamMigrationChainPlan -Existing (@(Get-MainFiles) + $wake) -New @($tuple)
    Assert-Equal -Expected "stop" -Actual $plan.Action -Because "tuple"
    Assert-True -Condition ($plan.Reason -like "dallı göç yeniden zincirlenmez*") -Because $plan.Reason
    $labelled = New-File -Path ($versions + "20261005_0067_labelled.py") -Text ((New-MigrationText -Revision "0067_labelled" -Down "0066_watches") -replace 'branch_labels = None', 'branch_labels = ("side",)')
    Assert-True -Condition ((Get-TeamMigrationChainPlan -Existing (@(Get-MainFiles) + $wake) -New @($labelled)).Reason -like "dallı göç*") -Because "branch label"
    $long = "0067_" + ("x" * 28)
    $longFile = New-File -Path ($versions + "20261005_$long.py") -Text (New-MigrationText -Revision $long -Down "0066_watches")
    $plan = Get-TeamMigrationChainPlan -Existing (@(Get-MainFiles) + $wake) -New @($longFile)
    Assert-Equal -Expected "stop" -Actual $plan.Action -Because "33 characters"
    Assert-True -Condition ($plan.Reason -like "revision id 32 karakteri aşıyor*") -Because $plan.Reason
}

Test-Case "main's migration is never renumbered, and an integration branch with two heads is not mended here" {
    $mains = @(Get-MainFiles)
    $base = New-File -Path $mains[1].Path -Text $mains[1].Text -InBase $true
    $plan = Get-TeamMigrationChainPlan -Existing (@($mains[0]) + $wake) -New @($base)
    Assert-Equal -Expected "stop" -Actual $plan.Action -Because "InBase"
    Assert-Equal -Expected ("main'deki göç yeniden numaralanmaz: " + $mains[1].Path) -Actual $plan.Reason -Because "named"
    $twin = New-File -Path ($versions + "20261004_0067_twin.py") -Text (New-MigrationText -Revision "0067_twin" -Down "0066_watches")
    $plan = Get-TeamMigrationChainPlan -Existing (@($mains) + $wake + $twin) -New @($transcripts)
    Assert-Equal -Expected "stop" -Actual $plan.Action -Because "two heads"
    Assert-True -Condition ($plan.Reason -like "entegrasyon dalı zaten iki uçlu*") -Because $plan.Reason
}

Test-Case "a file name without NNNN (money_ledger.py): on the tip already is none; off the tip only the ids move, the name stays" {
    $house = New-File -Path ($versions + "20261004_0067_household_stock.py") -Text (New-MigrationText -Revision "0067_household_stock" -Down "0066_watches" -Tables @("household_items"))
    $onTip = New-File -Path ($versions + "money_ledger.py") -Text (New-MigrationText -Revision "0068_money_ledger" -Down "0067_household_stock" -Tables @("money_entries"))
    $plan = Get-TeamMigrationChainPlan -Existing (@(Get-MainFiles) + $house) -New @($onTip)
    Assert-Equal -Expected "none" -Actual $plan.Action -Because "right tip, right number: $($plan.Reason)"
    $offTip = New-File -Path ($versions + "money_ledger.py") -Text (New-MigrationText -Revision "0067_money_ledger" -Down "0066_watches" -Tables @("money_entries"))
    $plan = Get-TeamMigrationChainPlan -Existing (@(Get-MainFiles) + $house) -New @($offTip)
    Assert-Equal -Expected "rechain" -Actual $plan.Action -Because $plan.Reason
    Assert-Equal -Expected ($versions + "money_ledger.py") -Actual $plan.Renames[0].NewPath -Because "a name without NNNN is kept"
    Assert-Equal -Expected "0068_money_ledger" -Actual $plan.Renames[0].NewRevision -Because "the revision's NNNN moves"
    Assert-True -Condition ($plan.Renames[0].Text -match 'down_revision: str \| None = "0067_household_stock"') -Because "on the tip: $($plan.Renames[0].Text)"
    $plain = New-File -Path ($versions + "ledger_plain.py") -Text (New-MigrationText -Revision "ledger_plain" -Down "0066_watches" -Tables @("ledger_plain"))
    $plan = Get-TeamMigrationChainPlan -Existing (@(Get-MainFiles) + $house) -New @($plain)
    Assert-Equal -Expected "rechain" -Actual $plan.Action -Because $plan.Reason
    Assert-Equal -Expected "ledger_plain>0067_household_stock" -Actual "$($plan.Renames[0].NewRevision)>$($plan.Renames[0].NewDown)" -Because "an id without NNNN keeps its id, only its parent moves"
    $chain = @(@(Get-MainFiles) + $house + (New-File -Path $plan.Renames[0].NewPath -Text $plan.Renames[0].Text))
    Assert-Equal -Expected "ledger_plain" -Actual (@(Get-TeamMigrationHeads -Files $chain) -join ",") -Because "one head"
}

# ============================================================================ Merge-TeamBranch

Write-Host ""
Write-Host "Merge-TeamBranch in a sandbox repository"

function Invoke-SandboxGit {
    param([string]$Root, [string[]]$Arguments)
    $result = Invoke-TeamGit -WorkingDirectory $Root -Arguments $Arguments
    if (-not $result.Success) { throw "git $($Arguments -join ' '): $($result.StdErr)" }
    return $result.StdOut.Trim()
}

function Write-SandboxFile {
    param([string]$Root, [string]$Path, [string]$Text)
    $full = Join-Path $Root ($Path -replace '/', '\')
    $parent = Split-Path -Parent $full
    if (-not (Test-Path -LiteralPath $parent)) { [void](New-Item -ItemType Directory -Force -Path $parent) }
    [System.IO.File]::WriteAllText($full, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

function New-Sandbox {
    $root = Join-Path $env:TEMP ("pagentos-chain-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    [void]$sandboxes.Add($root)
    [void](New-Item -ItemType Directory -Force -Path $root)
    Set-Content -LiteralPath (Join-Path $root ".gitignore") -Value ".claude/worktrees/" -Encoding ASCII
    foreach ($file in @(Get-MainFiles)) { Write-SandboxFile -Root $root -Path $file.Path -Text $file.Text }
    [void](Invoke-SandboxGit -Root $root -Arguments @("init", "-q", "-b", "main"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.name", "team test"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.email", "team@example.invalid"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "core.autocrlf", "false"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("add", "-A"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-m", "main at 0066_watches"))
    return $root
}

function New-SandboxBranch {
    param([string]$Root, [string]$Name, [object[]]$Files)
    [void](Invoke-SandboxGit -Root $Root -Arguments @("checkout", "-q", "-b", $Name, "main"))
    foreach ($file in $Files) { Write-SandboxFile -Root $Root -Path $file.Path -Text $file.Text }
    [void](Invoke-SandboxGit -Root $Root -Arguments @("add", "-A"))
    [void](Invoke-SandboxGit -Root $Root -Arguments @("commit", "-q", "-m", "work on $Name"))
    [void](Invoke-SandboxGit -Root $Root -Arguments @("checkout", "-q", "main"))
    return (Invoke-SandboxGit -Root $Root -Arguments @("rev-parse", "refs/heads/$Name"))
}

# The one-head check: the pytest test's patterns, on the integration worktree's versions folder.
$oneHead = {
    param($tree)
    $folder = Join-Path $tree "services\api\alembic\versions"
    $files = @(Get-ChildItem -LiteralPath $folder -Filter *.py | ForEach-Object { [pscustomobject]@{ Path = $_.Name; Text = [System.IO.File]::ReadAllText($_.FullName) } })
    $heads = @(Get-TeamMigrationHeads -Files $files)
    return [pscustomobject]@{ Success = ($heads.Count -eq 1); Output = "heads: " + ($heads -join ", ") }
}

function Merge-Sandbox { param([string]$Root, [string]$Branch, [scriptblock]$Check = $oneHead)
    return (Merge-TeamBranch -RepoRoot $Root -CycleId "c1" -Branch $Branch -Base "main" -MigrationCheck $Check) }

try {
    Test-Case "end to end: A is rechained INSIDE one merge commit, B after it, Undo-TeamMerge still takes B back" {
        $root = New-Sandbox
        [void](New-SandboxBranch -Root $root -Name "team/c1/worker-wake" -Files @($wake))
        $tipA = New-SandboxBranch -Root $root -Name "team/c1/worker-transcripts" -Files @($transcripts, (New-File -Path $testPath -Text $testText))
        $mail = New-File -Path ($versions + "20261005_0068_mail_accounts.py") -Text (New-MigrationText -Revision "0068_mail_accounts" -Down "0066_watches" -Tables @("mail_accounts"))
        $tipB = New-SandboxBranch -Root $root -Name "team/c1/worker-mail" -Files @($mail)
        $tree = Join-Path $root ".claude\worktrees\integrate\c1"

        $w = Merge-Sandbox -Root $root -Branch "team/c1/worker-wake"
        Assert-True -Condition ($w.Merged -and @($w.Rechained).Count -eq 0) -Because "the first is on main's tip already: $($w.Detail)"
        $beforeA = Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD")

        $a = Merge-Sandbox -Root $root -Branch "team/c1/worker-transcripts"
        Assert-True -Condition ($a.Merged -and -not $a.Conflict) -Because "merged: $($a.Detail)"
        Assert-Equal -Expected "0068_conversation_transcripts" -Actual (@($a.Rechained)[0].NewRevision) -Because "rechained"
        Assert-True -Condition ($a.Detail -like "göç zinciri yeniden kuruldu (uç 0067_wake_alarm_song): 0067_conversation_transcripts -> 0068_conversation_transcripts") -Because $a.Detail
        Assert-Equal -Expected $beforeA -Actual (Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD^1")) -Because "ONE commit on the integration branch: its first parent is where it was"
        Assert-Equal -Expected $tipA -Actual (Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD^2")) -Because "the second parent is the task branch's tip"
        Assert-Equal -Expected $tipA -Actual (Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "refs/heads/team/c1/worker-transcripts")) -Because "the task branch never moved"
        Assert-True -Condition ((Invoke-SandboxGit -Root $tree -Arguments @("log", "-1", "--format=%s")) -like "merge: team/c1/worker-transcripts into integrate/c1 (migration rechained: 0067_conversation_transcripts -> 0068_conversation_transcripts on 0067_wake_alarm_song)") -Because "the message says it"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $tree "services\api\alembic\versions\20261005_0068_conversation_transcripts.py")) -Because "renamed"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $tree "services\api\alembic\versions\20261005_0067_conversation_transcripts.py"))) -Because "the old name is gone"
        Assert-True -Condition ([System.IO.File]::ReadAllText((Join-Path $tree ($testPath -replace '/', '\'))) -match 'BEFORE = "0067_wake_alarm_song"') -Because "the branch's test follows"
        Assert-Equal -Expected "" -Actual (Invoke-SandboxGit -Root $tree -Arguments @("status", "--porcelain")) -Because "clean"
        $afterA = Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD")

        $b = Merge-Sandbox -Root $root -Branch "team/c1/worker-mail"
        Assert-True -Condition $b.Merged -Because $b.Detail
        Assert-Equal -Expected "0069_mail_accounts" -Actual (@($b.Rechained)[0].NewRevision) -Because "after A"
        Assert-Equal -Expected "0068_conversation_transcripts" -Actual (@($b.Rechained)[0].NewDown) -Because "on A"
        Assert-True -Condition (& $oneHead $tree).Success -Because "one head"
        Assert-Equal -Expected $tipB -Actual (Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD^2")) -Because "B's tip"
        $again = Merge-Sandbox -Root $root -Branch "team/c1/worker-mail"
        Assert-True -Condition ($again.Already) -Because "already merged: merge-base --is-ancestor still answers"

        Assert-Equal -Expected $true -Actual (Undo-TeamMerge -RepoRoot $root -CycleId "c1" -Branch "team/c1/worker-mail") -Because "the rechained merge is still the last merge of B"
        Assert-Equal -Expected $afterA -Actual (Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD")) -Because "back on A's merge"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $tree "services\api\alembic\versions\20261005_0069_mail_accounts.py"))) -Because "B is gone"
        Assert-Equal -Expected $tipB -Actual (Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "refs/heads/team/c1/worker-mail")) -Because "B's branch untouched"
    }

    Test-Case "end to end: the same table stops the merge and the integration branch stays where it was" {
        $root = New-Sandbox
        $first = New-File -Path ($versions + "20261004_0067_people_book.py") -Text (New-MigrationText -Revision "0067_people_book" -Down "0066_watches" -Tables @("persons"))
        $mine = New-File -Path ($versions + "20261005_0067_persons_too.py") -Text (New-MigrationText -Revision "0067_persons_too" -Down "0066_watches" -Tables @("persons"))
        [void](New-SandboxBranch -Root $root -Name "team/c1/worker-book" -Files @($first))
        $tip = New-SandboxBranch -Root $root -Name "team/c1/worker-too" -Files @($mine)
        [void](Merge-Sandbox -Root $root -Branch "team/c1/worker-book")
        $tree = Join-Path $root ".claude\worktrees\integrate\c1"
        $before = Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD")
        $merge = Merge-Sandbox -Root $root -Branch "team/c1/worker-too"
        Assert-True -Condition (-not $merge.Merged -and $merge.Conflict) -Because "stopped"
        Assert-Equal -Expected "göç zinciri: aynı tablo: persons (20261005_0067_persons_too.py, 20261004_0067_people_book.py)" -Actual $merge.Detail -Because "the reason is written"
        Assert-Equal -Expected $before -Actual (Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD")) -Because "the integration branch is where it was"
        Assert-Equal -Expected "" -Actual (Invoke-SandboxGit -Root $tree -Arguments @("status", "--porcelain")) -Because "clean"
        Assert-Equal -Expected $tip -Actual (Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "refs/heads/team/c1/worker-too")) -Because "the task branch never moved"
    }

    Test-Case "end to end: a branch that changes main's migration - that file keeps its name and number, the new one moves" {
        $root = New-Sandbox
        [void](New-SandboxBranch -Root $root -Name "team/c1/worker-wake" -Files @($wake))
        $mains = @(Get-MainFiles)
        $edited = New-File -Path $mains[1].Path -Text ($mains[1].Text + "# a comment the branch added`n")
        $extra = New-File -Path ($versions + "20261005_0067_notes.py") -Text (New-MigrationText -Revision "0067_notes" -Down "0066_watches" -Tables @("notes"))
        [void](New-SandboxBranch -Root $root -Name "team/c1/worker-edit" -Files @($edited, $extra))
        [void](Merge-Sandbox -Root $root -Branch "team/c1/worker-wake")
        $merge = Merge-Sandbox -Root $root -Branch "team/c1/worker-edit"
        Assert-True -Condition $merge.Merged -Because $merge.Detail
        Assert-Equal -Expected "0067_notes>0068_notes" -Actual (@($merge.Rechained | ForEach-Object { "$($_.OldRevision)>$($_.NewRevision)" }) -join ",") -Because "only the added file"
        $tree = Join-Path $root ".claude\worktrees\integrate\c1"
        $diff = Invoke-SandboxGit -Root $tree -Arguments @("diff", "--name-status", "HEAD^1", "HEAD")
        Assert-True -Condition ($diff -match "(?m)^M\s+services/api/alembic/versions/20261003_0066_watches\.py$") -Because "main's file: modified in place: $diff"
        Assert-True -Condition ($diff -match "(?m)^A\s+services/api/alembic/versions/20261005_0068_notes\.py$") -Because "the new file: 0068: $diff"
        Assert-True -Condition ([System.IO.File]::ReadAllText((Join-Path $tree "services\api\alembic\versions\20261003_0066_watches.py")) -match 'revision: str = "0066_watches"') -Because "main's revision unchanged"
    }

    Test-Case "end to end: a red one-head check takes the rechained merge back, the branch is untouched" {
        $root = New-Sandbox
        [void](New-SandboxBranch -Root $root -Name "team/c1/worker-wake" -Files @($wake))
        $tip = New-SandboxBranch -Root $root -Name "team/c1/worker-transcripts" -Files @($transcripts)
        [void](Merge-Sandbox -Root $root -Branch "team/c1/worker-wake")
        $tree = Join-Path $root ".claude\worktrees\integrate\c1"
        $before = Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD")
        $red = { param($tree) [pscustomobject]@{ Success = $false; Output = "1 failed`nFAILED test_the_migration_chain_has_exactly_one_head" } }
        $merge = Merge-Sandbox -Root $root -Branch "team/c1/worker-transcripts" -Check $red
        Assert-True -Condition (-not $merge.Merged -and $merge.Conflict) -Because "stopped"
        Assert-True -Condition ($merge.Detail -like "göç zinciri: tek uç testi kırmızı: *test_the_migration_chain_has_exactly_one_head") -Because $merge.Detail
        Assert-Equal -Expected $before -Actual (Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD")) -Because "taken back"
        Assert-Equal -Expected $tip -Actual (Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "refs/heads/team/c1/worker-transcripts")) -Because "the task branch never moved"
    }

    Test-Case "end to end: a check that THROWS (uv timed out, uv missing) takes the rechained merge back too" {
        $root = New-Sandbox
        [void](New-SandboxBranch -Root $root -Name "team/c1/worker-wake" -Files @($wake))
        $tip = New-SandboxBranch -Root $root -Name "team/c1/worker-transcripts" -Files @($transcripts, (New-File -Path $testPath -Text $testText))
        [void](Merge-Sandbox -Root $root -Branch "team/c1/worker-wake")
        $tree = Join-Path $root ".claude\worktrees\integrate\c1"
        $before = Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD")
        $throws = { param($tree) throw "native tool timed out after 900 s" }
        $merge = $null
        try { $merge = Merge-Sandbox -Root $root -Branch "team/c1/worker-transcripts" -Check $throws }
        catch { throw "Merge-TeamBranch let the check's exception out: $($_.Exception.Message)" }
        Assert-True -Condition (-not $merge.Merged -and $merge.Conflict) -Because "stopped"
        Assert-True -Condition ($merge.Detail -like "göç zinciri: tek uç testi koşulamadı: *native tool timed out after 900 s") -Because $merge.Detail
        Assert-Equal -Expected $before -Actual (Invoke-SandboxGit -Root $tree -Arguments @("rev-parse", "HEAD")) -Because "taken back"
        Assert-Equal -Expected "" -Actual (Invoke-SandboxGit -Root $tree -Arguments @("status", "--porcelain")) -Because "clean"
        Assert-Equal -Expected $tip -Actual (Invoke-SandboxGit -Root $root -Arguments @("rev-parse", "refs/heads/team/c1/worker-transcripts")) -Because "the task branch never moved"
    }

    Test-Case "a branch with no migration merges as it always did: no check is run" {
        $root = New-Sandbox
        [void](New-SandboxBranch -Root $root -Name "team/c1/worker-plain" -Files @((New-File -Path "src/plain.txt" -Text "plain")))
        $never = { param($tree) throw "the check must not run for a branch without a migration" }
        $merge = Merge-Sandbox -Root $root -Branch "team/c1/worker-plain" -Check $never
        Assert-True -Condition ($merge.Merged -and -not $merge.Conflict -and $merge.Detail -eq "" -and @($merge.Rechained).Count -eq 0) -Because $merge.Detail
    }
}
finally {
    foreach ($root in $sandboxes) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        try { [void](Invoke-TeamGit -WorkingDirectory $root -Arguments @("worktree", "prune")) } catch { }
        for ($attempt = 0; $attempt -lt 5; $attempt++) {
            try { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction Stop; break }
            catch { Start-Sleep -Milliseconds 400 }
        }
    }
}

Write-Host ""
Write-Host "team-migration-chain tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
