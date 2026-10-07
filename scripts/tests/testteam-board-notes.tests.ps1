<#
.SYNOPSIS
    The test team's board notes are written per JOB, not under one shared task
    (card test-board-notes-per-job, team/plans/test-board-notes-per-job-adr.md).

.DESCRIPTION
    Measured 2026-10-07 02:39, round t-r10070152: the round's log ended with 'PANO REDDETTI
    (HTTP 429) ... test-team has 20 notes in the last hour; at most 20'. The board
    (services/api/app/team/board.py) allows RATE_PER_TASK_HOUR notes per TASK per hour, and
    every test seat posted under the one task 'test-team'; five seats of one round ran out
    within the hour and the Ofis' Test odası froze (a job never ended, a result never landed).

    The board's limit is NOT raised (it guards the Cloud Core). Instead:
      - a tester's notes - the round's 'iş:' / 'sonuç:' notes for it, and the notes the tester
        run posts itself (PAGENTOS_TEAM_TASK) - go under its job's id (tj-<round>-<n>);
      - the round's own notes (the cap, the forwarded failures, the breaking report) go under
        the round id.

    A stand-in board.ps1 holds the real board's two rules: the task pattern (read from
    board.py's TASK_PATTERN) and RATE_PER_TASK_HOUR per task (read from board.py). It refuses
    exactly as the board would, so a round that shares one task across its jobs is refused
    here as it was refused on the Cloud Core. No model is started.
#>
[CmdletBinding()]
param([string]$Filter = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

$script:Failures = 0
$script:Passes = 0

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

function New-Work {
    $path = Join-Path $env:TEMP ("pagentos-tbn-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    [void](New-Item -ItemType Directory -Force -Path $path)
    return $path
}

function Write-Utf8 {
    param([string]$Path, [string]$Text)
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

$powershell = Join-Path $PSHOME "powershell.exe"
$testRound = Join-Path $repoRoot "scripts\testteam\test-round.ps1"

# The real board's two rules, from its source (both halves of the contract read one file).
$boardSource = [System.IO.File]::ReadAllText((Join-Path $repoRoot "services\api\app\team\board.py"), [System.Text.Encoding]::UTF8)
$taskPattern = [regex]::Match($boardSource, '(?m)^TASK_PATTERN\s*=\s*r"([^"]+)"').Groups[1].Value
$ratePerTask = [int][regex]::Match($boardSource, '(?m)^RATE_PER_TASK_HOUR\s*=\s*(\d+)').Groups[1].Value

function New-FakeBoard {
    # A stand-in board.ps1. Every post is one JSON file in <Dir>\posts (seat, task, to, text,
    # verdict); the verdict is the real board's: 422 for a task off TASK_PATTERN, 429 when the
    # task already holds RATE_PER_TASK_HOUR accepted notes. A named mutex orders the parallel
    # testers' posts, so the count is exact.
    param([string]$Dir)
    $posts = Join-Path $Dir "posts"
    [void](New-Item -ItemType Directory -Force -Path $posts)
    $file = Join-Path $Dir "board.ps1"
    $mutex = "pagentos-fakeboard-" + [guid]::NewGuid().ToString("N")
    $body = @'
$named = @{}
for ($i = 0; $i -lt $args.Count; $i++) { if ([string]$args[$i] -like "-*" -and $i + 1 -lt $args.Count) { $named[([string]$args[$i]).TrimStart("-")] = [string]$args[$i + 1]; $i++ } }
$task = [string]$named["Task"]
$m = New-Object System.Threading.Mutex($false, "__MUTEX__")
[void]$m.WaitOne()
try {
    $accepted = @(Get-ChildItem -LiteralPath "__POSTS__" -Filter "*.json" -File | ForEach-Object { [IO.File]::ReadAllText($_.FullName) | ConvertFrom-Json } | Where-Object { $_.task -eq $task -and $_.verdict -eq "ok" }).Count
    $verdict = "ok"
    if ($task -cnotmatch '__PATTERN__') { $verdict = "422" }
    elseif ($accepted -ge __RATE__) { $verdict = "429" }
    $n = @(Get-ChildItem -LiteralPath "__POSTS__" -Filter "*.json" -File).Count
    $doc = [ordered]@{ n = $n; seat = [string]$named["Seat"]; task = $task; to = [string]$named["To"]; text = [string]$named["Text"]; verdict = $verdict }
    [IO.File]::WriteAllText((Join-Path "__POSTS__" ("{0:D5}.json" -f $n)), ($doc | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding($false)))
}
finally { $m.ReleaseMutex() }
if ($verdict -eq "ok") { Write-Output "Not panoya yazıldı." } else { Write-Output "PANO REDDETTI (HTTP $verdict) $task" }
exit 0
'@
    $body = $body.Replace("__MUTEX__", $mutex).Replace("__POSTS__", $posts).Replace("__PATTERN__", $taskPattern.Replace("'", "''")).Replace("__RATE__", [string]$ratePerTask)
    Write-Utf8 $file $body
    return $file
}

function Get-FakePosts {
    param([string]$Dir)
    $posts = Join-Path $Dir "posts"
    return @(Get-ChildItem -LiteralPath $posts -Filter "*.json" -File | Sort-Object Name | ForEach-Object { [System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8) | ConvertFrom-Json })
}

function New-FakeTester {
    # A stand-in for `claude -p` as a tester: reads its card, posts N notes to the board under
    # the seat and task its run was given (PAGENTOS_TEAM_SEAT / PAGENTOS_TEAM_TASK, as the real
    # tester's role file does), writes a passed result. The family 'cok<N>' posts N notes,
    # every other family 4.
    param([string]$Dir, [string]$Board)
    $file = Join-Path $Dir "fake-tester.ps1"
    $body = @'
$card = [Console]::In.ReadToEnd()
$path = [regex]::Match($card, '(?m)^- result_file: (.+)$').Groups[1].Value.Trim()
$id = [regex]::Match($card, '(?m)^- id: (.+)$').Groups[1].Value.Trim()
$family = [regex]::Match($card, '(?m)^- family: (.+)$').Groups[1].Value.Trim()
$seat = [regex]::Match($card, '(?m)^- tester: (.+)$').Groups[1].Value.Trim()
$count = 4
if ($family -match '^cok(\d+)$') { $count = [int]$Matches[1] }
$ps = Join-Path $PSHOME "powershell.exe"
for ($k = 1; $k -le $count; $k++) {
    & $ps -NoProfile -File "__BOARD__" post -Seat $env:PAGENTOS_TEAM_SEAT -Task $env:PAGENTOS_TEAM_TASK -Kind bilgi -Text "$seat ara not $k ($id)" | Out-Null
}
$doc = @{ card = $id; tester = $seat; family = $family; state = "passed"; scenario = "scripts/testteam/scenarios/$family.json"; staging_sha = ("d" * 40); steps = @(@{ name = "adim"; expected = "200"; actual = "200"; ok = $true }); breaking = @{ what = "x"; tried = @(); first_failure = $null }; screenshot = "" }
[IO.File]::WriteAllText($path, ($doc | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))
Write-Output '{"type":"result","subtype":"success","is_error":false,"result":"rapor yazildi","total_cost_usd":0}'
'@
    Write-Utf8 $file $body.Replace("__BOARD__", $Board)
    return $file
}

function Invoke-FakeRound {
    # One round of test-round.ps1 with the fake testers and the fake board; returns the posts.
    param([string]$Work, [string]$Round, [string[]]$Families)
    $team = Join-Path $Work "team"
    [void](New-Item -ItemType Directory -Force -Path $team)
    Write-Utf8 (Join-Path $team "queue.json") '{"version":1,"tasks":[]}'
    Write-Utf8 (Join-Path $team "cycle-settings.json") '{"max_parallel":4,"test_parallel":4}'
    $jobs = @($Families | ForEach-Object { [ordered]@{ family = $_; scenario = "scripts/testteam/scenarios/$_.json"; improvise = $true } })
    $plan = Join-Path $Work "plan.json"
    Write-Utf8 $plan (ConvertTo-Json -InputObject ([ordered]@{ jobs = $jobs }) -Depth 5)
    $board = New-FakeBoard -Dir $Work
    $fake = New-FakeTester -Dir $Work -Board $board
    $out = & $powershell -NoProfile -File $testRound -Round $Round -TeamRoot $team -OutRoot (Join-Path $Work "out") -PlanPath $plan -ClaudePath $powershell -ClaudePrefixArguments "-NoProfile,-File,$fake" -AssumeFreeGb 30 -AssumeGateRunning 0 -BoardScript $board 2>&1
    $code = $LASTEXITCODE
    return [pscustomobject]@{ Code = $code; Out = ($out -join "`n"); Posts = @(Get-FakePosts -Dir $Work) }
}

Write-Host ""
Write-Host "the fake board holds the real board's rules (board.py TASK_PATTERN '$taskPattern', RATE_PER_TASK_HOUR $ratePerTask)"

Test-Case "the board's rules are read from board.py (a stand-in that drifts from the board proves nothing)" {
    Assert-True -Condition ($taskPattern.Length -gt 0) -Because "board.py names TASK_PATTERN"
    Assert-True -Condition ($ratePerTask -gt 0) -Because "board.py names RATE_PER_TASK_HOUR"
    Assert-True -Condition ('test-team' -match $taskPattern) -Because "the old shared task is a task id"
    Assert-True -Condition (-not ('test-team/tester-1' -match $taskPattern)) -Because "a slash is no task id: 'test-team/<seat>' would be refused (422)"
}

Write-Host ""
Write-Host "the board task of a note (TestTeam.ps1)"

# The helpers alone; Get-TeamProperty is the only library function they could need.
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\testteam\TestTeam.ps1")

Test-Case "TestTeam.ps1 holds board.py's TASK_PATTERN, character for character" {
    Assert-Equal -Expected $taskPattern -Actual $script:TestTeamBoardTaskPattern -Because "the two halves of the contract read one pattern"
}

Test-Case "a round id becomes its board task: lowercase, and 'test-<round>' when shorter than three characters" {
    # -cmatch: the board's re is case-sensitive; PowerShell's -match is not ('R1' -match '^[a-z0-9]...' is True).
    $cases = @(@("rbn5", "rbn5"), @("t202610070152", "t202610070152"), @("t-r10070152", "t-r10070152"), @("t1", "test-t1"), @("a", "test-a"), @("R1", "test-r1"), @("RBN5", "rbn5"), @("T-D20261006-3", "t-d20261006-3"))
    foreach ($case in $cases) {
        $round = $case[0]
        $task = Get-TestTeamBoardTask -Id $round
        Assert-Equal -Expected $case[1] -Actual $task -Because "round '$round'"
        Assert-True -Condition ($task -cmatch $taskPattern) -Because "'$task' is a board task id"
    }
}

Test-Case "a job's board task is its card id (lowercase), one per job, never the shared 'test-team'" {
    $cards = @(New-TestTeamCards -Round "R1" -Jobs @([pscustomobject]@{ family = "aaa"; improvise = $true }, [pscustomobject]@{ family = "bbb"; improvise = $true }))
    $tasks = @($cards | ForEach-Object { Get-TestTeamBoardTask -Id $_.id })
    Assert-Equal -Expected "tj-r1-1,tj-r1-2" -Actual ($tasks -join ",") -Because "each job its own task"
    foreach ($task in $tasks) { Assert-True -Condition ($task -cmatch $taskPattern) -Because "'$task' is a board task id" }
}

Test-Case "an id that cannot be a board task is refused, not posted to be refused (422)" {
    foreach ($bad in @("", "x/y", "a_b", ("a" * 65))) {
        $threw = $false
        try { [void](Get-TestTeamBoardTask -Id $bad) } catch { $threw = ($_.Exception.Message -like "*pano görev kimliği*") }
        Assert-True -Condition $threw -Because "'$bad' is refused by Get-TestTeamBoardTask itself"
    }
}

Write-Host ""
Write-Host "a round's notes, per job"

Test-Case "a round of 5 jobs x 6 notes posts every note, each tester note under its job's task (tj-<round>-<n>)" {
    $work = New-Work
    try {
        $round = Invoke-FakeRound -Work $work -Round "rbn5" -Families @("aaa", "bbb", "ccc", "ddd", "eee")
        Assert-Equal -Expected 0 -Actual $round.Code -Because "the round ends: $($round.Out)"
        $refused = @($round.Posts | Where-Object { $_.verdict -ne "ok" })
        Assert-Equal -Expected 0 -Actual @($refused).Count -Because ("no note is refused; refused: " + (@($refused | ForEach-Object { "$($_.verdict) $($_.task) $($_.seat): $($_.text)" }) -join " | "))
        Assert-True -Condition ($round.Out -notmatch "PANO REDDETTI") -Because "the round's log names no refusal: $($round.Out)"
        for ($n = 1; $n -le 5; $n++) {
            $job = "tj-rbn5-$n"
            $mine = @($round.Posts | Where-Object { [string]$_.text -match [regex]::Escape("($job)") })
            Assert-Equal -Expected 6 -Actual @($mine).Count -Because "job ${job}: 'iş:', 4 own notes, 'sonuç:'"
            foreach ($post in $mine) {
                Assert-Equal -Expected $job -Actual ([string]$post.task) -Because "every note of $job is under its own task ($($post.seat): $($post.text))"
                Assert-True -Condition ([string]$post.seat -match '^tester-[1-4]$') -Because "a tester's note is posted under its seat: $($post.seat)"
            }
        }
    }
    finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "the round's own notes (the breaking report to the Danışman) go under the round id, not under a job" {
    $work = New-Work
    try {
        $round = Invoke-FakeRound -Work $work -Round "rbn5" -Families @("aaa", "bbb")
        Assert-Equal -Expected 0 -Actual $round.Code -Because "the round ends: $($round.Out)"
        $lead = @($round.Posts | Where-Object { $_.seat -eq "test-lead" })
        Assert-True -Condition (@($lead).Count -ge 1) -Because "the test lead posts the breaking report"
        foreach ($post in $lead) { Assert-Equal -Expected "rbn5" -Actual ([string]$post.task) -Because "the test lead's note is under the round id: $($post.text)" }
        # Every job passed: the report says 'kırılma bulunmadı', not 'kopma noktası' (Format-TestTeamBreakingReport).
        $report = @($lead | Where-Object { [string]$_.text -match "^Danışman'a, test turu rbn5:" })
        Assert-Equal -Expected 1 -Actual @($report).Count -Because "one breaking report"
        Assert-Equal -Expected "danisman" -Actual ([string]$report[0].to) -Because "addressed to the Danışman's seat"
        Assert-Equal -Expected "ok" -Actual ([string]$report[0].verdict) -Because "the report is taken by the board"
    }
    finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "the guard holds: a single job with more than $ratePerTask notes in the hour is still refused, all of them under its one task" {
    $work = New-Work
    try {
        # 'cok21': the tester posts 21 notes itself, the round adds 'iş:' and 'sonuç:' - 23 notes for one job.
        $round = Invoke-FakeRound -Work $work -Round "rbn1" -Families @("cok21")
        Assert-Equal -Expected 0 -Actual $round.Code -Because "a refused note never stops the round: $($round.Out)"
        $job = @($round.Posts | Where-Object { $_.seat -like "tester-*" })
        Assert-Equal -Expected 23 -Actual @($job).Count -Because "23 notes were posted for the job"
        $tasks = @($job | ForEach-Object { [string]$_.task } | Sort-Object -Unique)
        Assert-Equal -Expected "tj-rbn1-1" -Actual ($tasks -join ",") -Because "one job, one task: a fresh task per note would walk around the guard"
        $ok = @($job | Where-Object { $_.verdict -eq "ok" })
        $refused = @($job | Where-Object { $_.verdict -eq "429" })
        Assert-Equal -Expected $ratePerTask -Actual @($ok).Count -Because "the board takes $ratePerTask notes of the job in the hour"
        Assert-Equal -Expected (23 - $ratePerTask) -Actual @($refused).Count -Because "and refuses the rest (429): the limit is not raised"
    }
    finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "a hand-given uppercase round 'R1' (test-round.ps1's -notmatch lets it in) still posts every note" {
    $work = New-Work
    try {
        $round = Invoke-FakeRound -Work $work -Round "R1" -Families @("aaa")
        Assert-Equal -Expected 0 -Actual $round.Code -Because "the round ends: $($round.Out)"
        $refused = @($round.Posts | Where-Object { $_.verdict -ne "ok" })
        Assert-Equal -Expected 0 -Actual @($refused).Count -Because ("no note is refused (422 for 'R1' / 'tj-R1-1'); refused: " + (@($refused | ForEach-Object { "$($_.verdict) $($_.task)" }) -join " | "))
        $tasks = @($round.Posts | ForEach-Object { [string]$_.task } | Sort-Object -Unique)
        Assert-Equal -Expected "test-r1,tj-r1-1" -Actual ($tasks -join ",") -Because "the round under test-r1, the job under tj-r1-1"
    }
    finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Host ""
Write-Host "the Ofis' Test odası reads by seat"

Test-Case "officeTestRoom.tsx reads a note by its seat and text, never by its task (a per-job task keeps the room)" {
    $room = [System.IO.File]::ReadAllText((Join-Path $repoRoot "apps\web\app\core\office\officeTestRoom.tsx"), [System.Text.Encoding]::UTF8)
    Assert-True -Condition ($room -match 'note\.seat') -Because "the room places a note by note.seat"
    Assert-True -Condition ($room -notmatch 'note\.task') -Because "the room never filters on note.task (a per-job task would hide every seat)"
    Assert-True -Condition ($room -notmatch '["'']test-team["'']\s*[!=]==|[!=]==\s*["'']test-team["'']') -Because "the room compares nothing with the old task 'test-team'"
}

Write-Host ""
Write-Host "testteam-board-notes tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
