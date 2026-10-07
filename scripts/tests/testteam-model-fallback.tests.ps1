<#
.SYNOPSIS
    The test round lowers a limited model as the cycle does (team/plans/test-round-model-fallback-adr.md).

.DESCRIPTION
    Measured 2026-10-07 07:10, round t-r10070710: the test lead's plan run ended at once with
    api_error 429 'model_requires_usage_credits' on claude-fable-5-1 - test-round.ps1 started it
    on the lead's configured model while the cycle knew Fable was limited and lowered its own
    runs - "Test PY plan yazmadı ... tur başlamadı", and the test team sat idle.

    scripts/testteam/test-round.ps1 is run for real with a fake `claude` that writes the plan
    (test-lead) or a passed result (tester), and answers the tool's own usage-credits result line
    (the one t-r10070710's log holds) for every model named in PAGENTOS_FAKE_LIMITED. What is
    asserted is which role ran on which model, in which order, and what the round said.

    No model is started; the board, staging and this repository's team/ files are not touched.

    Run: powershell -NoProfile -File scripts\tests\testteam-model-fallback.tests.ps1
#>
[CmdletBinding()]
param([string]$Filter = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$powershell = Join-Path $PSHOME "powershell.exe"
$testRound = Join-Path $repoRoot "scripts\testteam\test-round.ps1"

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

function Write-Utf8 {
    param([string]$Path, [string]$Text)
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

function New-Work {
    # A team folder, a fake claude, a fake board, a call log. Settings: one tester at a time, so
    # the order of the calls is the order of the jobs.
    $path = Join-Path $env:TEMP ("pagentos-tt-model-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    $team = Join-Path $path "team"
    [void](New-Item -ItemType Directory -Force -Path $team)
    Write-Utf8 (Join-Path $team "queue.json") '{"version":1,"tasks":[]}'
    Write-Utf8 (Join-Path $team "cycle-settings.json") '{"max_parallel":4,"test_parallel":1}'
    $fake = Join-Path $path "fake-claude.ps1"
    Write-Utf8 $fake @'
$prompt = [Console]::In.ReadToEnd()
$role = ""; $model = ""
for ($i = 0; $i -lt $args.Count; $i++) {
    if ($args[$i] -eq "--append-system-prompt-file") { $role = [IO.Path]::GetFileNameWithoutExtension($args[$i + 1]) }
    if ($args[$i] -eq "--model") { $model = $args[$i + 1] }
}
[void](New-Item -ItemType Directory -Force -Path $env:PAGENTOS_FAKE_CLAUDE_LOG)
[IO.File]::WriteAllText((Join-Path $env:PAGENTOS_FAKE_CLAUDE_LOG ("{0:D20}.call" -f [datetime]::UtcNow.Ticks)), "$role $model $env:PAGENTOS_TEAM_SEAT")
if (@(([string]$env:PAGENTOS_FAKE_LIMITED) -split ',') -contains $model) {
    # The tool's own answer of t-r10070710 (2026-10-07 07:10), cut to the fields that matter.
    Write-Output '{"type":"system","subtype":"init","model":"x"}'
    Write-Output '{"duration_api_ms":0,"total_cost_usd":0,"modelUsage":{},"terminal_reason":"api_error","is_error":true,"num_turns":1,"subtype":"success","api_error_status":429,"api_error":"model_requires_usage_credits","result":"You''re out of usage credits. Switch to another model, or manage usage credits at claude.ai/settings/usage?from=cc_cli_limit_message, to continue.","type":"result","duration_ms":510}'
    exit 1
}
if ($role -eq "test-lead") {
    $plan = [regex]::Match($prompt, '(?m)^- plan_file: (.+)$').Groups[1].Value.Trim()
    [IO.File]::WriteAllText($plan, '{"jobs":[{"family":"saglik","scenario":"scripts/testteam/scenarios/health.json"}]}')
}
else {
    $path = [regex]::Match($prompt, '(?m)^- result_file: (.+)$').Groups[1].Value.Trim()
    $id = [regex]::Match($prompt, '(?m)^- id: (.+)$').Groups[1].Value.Trim()
    $family = [regex]::Match($prompt, '(?m)^- family: (.+)$').Groups[1].Value.Trim()
    $doc = @{ card = $id; tester = $env:PAGENTOS_TEAM_SEAT; family = $family; state = "passed"; scenario = "s.json"; staging_sha = ("d" * 40); steps = @(@{ name = "adim"; expected = "200"; actual = "200"; ok = $true }); screenshot = "" }
    [IO.File]::WriteAllText($path, ($doc | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))
}
Write-Output '{"type":"result","subtype":"success","is_error":false,"result":"tamam","total_cost_usd":0,"modelUsage":{}}'
'@
    $posts = Join-Path $path "posts.log"
    $board = Join-Path $path "board.ps1"
    Write-Utf8 $board ("[IO.File]::AppendAllText('$posts', ((@(`$args) -join '|') + [Environment]::NewLine), (New-Object Text.UTF8Encoding(`$false)))")
    return [pscustomobject]@{ Dir = $path; Team = $team; Fake = $fake; Board = $board; Posts = $posts; Calls = (Join-Path $path "calls"); Out = (Join-Path $path "out") }
}

function Invoke-Round {
    # The round, as the cycle starts it, with the fake claude and the fake board. -Limited is the
    # fake tool's list of models that answer the usage-credits error.
    param($Work, [string]$Round, [string]$Limited = "", [string]$PlanPath = "", [string[]]$More = @())
    $env:PAGENTOS_FAKE_CLAUDE_LOG = $Work.Calls
    $env:PAGENTOS_FAKE_LIMITED = $Limited
    try {
        $arguments = @("-NoProfile", "-File", $testRound, "-NoAuth", "-Round", $Round, "-TeamRoot", $Work.Team, "-OutRoot", $Work.Out,
            "-ClaudePath", $powershell, "-ClaudePrefixArguments", "-NoProfile,-File,$($Work.Fake)", "-AssumeFreeGb", "30", "-AssumeGateRunning", "0",
            "-BoardScript", $Work.Board)
        if ($PlanPath) { $arguments += @("-PlanPath", $PlanPath) }
        $arguments += $More
        $out = @(& $powershell @arguments 2>&1 | ForEach-Object { [string]$_ })
        return [pscustomobject]@{ Code = $LASTEXITCODE; Lines = $out; Text = ($out -join "`n") }
    }
    finally {
        Remove-Item Env:\PAGENTOS_FAKE_CLAUDE_LOG -ErrorAction SilentlyContinue
        Remove-Item Env:\PAGENTOS_FAKE_LIMITED -ErrorAction SilentlyContinue
    }
}

function Get-Calls {
    # "<role> <model> <seat>", in the order the runs started.
    param($Work)
    if (-not (Test-Path -LiteralPath $Work.Calls)) { return @() }
    return @(Get-ChildItem -LiteralPath $Work.Calls -Filter "*.call" -File | Sort-Object Name | ForEach-Object { [System.IO.File]::ReadAllText($_.FullName) })
}

function Get-Posts {
    param($Work)
    if (-not (Test-Path -LiteralPath $Work.Posts)) { return @() }
    return @(Get-Content -LiteralPath $Work.Posts -Encoding UTF8 | Where-Object { $_ })
}

function Get-CardStates {
    param($Work, [string]$Round)
    $path = Join-Path $Work.Out "$Round\cards.json"
    if (-not (Test-Path -LiteralPath $path)) { return "" }
    $document = Get-Content -Raw -Encoding UTF8 -LiteralPath $path | ConvertFrom-Json
    return ((@($document.cards) | ForEach-Object { $_.state }) -join ",")
}

$future = [datetime]::UtcNow.AddDays(5).ToString("yyyy-MM-ddTHH:mm:ssZ")
$fable = "claude-fable-5-1"; $opus = "claude-opus-5-5"; $sonnet = "claude-sonnet-5-5"

Write-Host ""
Write-Host "the test round's model, as the cycle chooses it"

Test-Case "the lead's model limited in team/limits.json: the plan run starts on the next model, 'model düşürüldü' is said and posted, and the round runs" {
    $work = New-Work
    try {
        Write-Utf8 (Join-Path $work.Team "limits.json") ('{"models":{"' + $fable + '":{"until":"' + $future + '"}},"windows":{}}')
        $run = Invoke-Round -Work $work -Round "lim1"
        Assert-Equal -Expected 0 -Actual $run.Code -Because "the round runs: $($run.Text)"
        $calls = @(Get-Calls -Work $work)
        Assert-Equal -Expected "test-lead $opus test-lead|tester $opus tester-1" -Actual ($calls -join "|") -Because "the plan on Opus, never on the limited Fable; the tester on its own model"
        Assert-True -Condition ($run.Text -match "model d.s.r.ld.: $fable -> $opus") -Because "the round says it lowered: $($run.Text)"
        Assert-True -Condition (@(Get-Posts -Work $work | Where-Object { $_ -match "model düşürüldü" -and $_ -match "-Seat\|test-lead" }).Count -ge 1) -Because "and posts it from the test lead's seat: $(@(Get-Posts -Work $work) -join ' / ')"
        Assert-Equal -Expected "passed" -Actual (Get-CardStates -Work $work -Round "lim1") -Because "the job ran"
    }
    finally { Remove-Item -LiteralPath $work.Dir -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "the cycle's live status (team/status.json 'limits') says Fable is limited: the plan run starts on the next model" {
    $work = New-Work
    try {
        Write-Utf8 (Join-Path $work.Team "status.json") ('{"cycle_id":"c1","runs":[],"limits":{"fable":{"state":"limited","resets_at":"' + $future + '","used_pct":100},"all":{"state":"ok","resets_at":null,"used_pct":6},"fallback":true,"lowered":[]}}')
        $run = Invoke-Round -Work $work -Round "st1"
        Assert-Equal -Expected 0 -Actual $run.Code -Because "the round runs: $($run.Text)"
        Assert-Equal -Expected "test-lead $opus test-lead" -Actual (@(Get-Calls -Work $work)[0]) -Because "the status' limit is the cycle's knowledge too"
    }
    finally { Remove-Item -LiteralPath $work.Dir -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "API mode: the status the Cloud Core keeps (GET /v1/team/queue/status) says Fable is limited: the plan run starts on the next model" {
    $work = New-Work
    $port = Get-Random -Minimum 41000 -Maximum 49000
    $status = Join-Path $work.Dir "status-answer.json"
    Write-Utf8 $status ('{"limits":{"fable":{"state":"limited","resets_at":"' + $future + '"},"all":{"state":"ok"}}}')
    $core = @"
`$l = New-Object System.Net.HttpListener
`$l.Prefixes.Add('http://127.0.0.1:$port/')
`$l.Start()
while (`$true) {
    `$c = `$l.GetContext()
    `$p = `$c.Request.Url.AbsolutePath
    `$body = '{}'
    if (`$p -eq '/stop') { `$c.Response.Close(); break }
    if (`$c.Request.HttpMethod -eq 'GET' -and `$p -eq '/v1/team/queue') { `$body = '{"version":1,"tasks":[]}' }
    if (`$c.Request.HttpMethod -eq 'GET' -and `$p -eq '/v1/team/queue/status') { `$body = [IO.File]::ReadAllText('$status') }
    `$b = [Text.Encoding]::UTF8.GetBytes(`$body)
    `$c.Response.ContentType = 'application/json'
    `$c.Response.OutputStream.Write(`$b, 0, `$b.Length)
    `$c.Response.Close()
}
"@
    Write-Utf8 (Join-Path $work.Dir "core.ps1") $core
    $listener = Start-Process -FilePath $powershell -ArgumentList @("-NoProfile", "-File", (Join-Path $work.Dir "core.ps1")) -PassThru -WindowStyle Hidden
    try {
        for ($i = 0; $i -lt 50; $i++) { try { [void](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/v1/team/queue" -TimeoutSec 2); break } catch { Start-Sleep -Milliseconds 200 } }
        $token = Join-Path $work.Dir "token.txt"; Write-Utf8 $token "test-token"
        $run = Invoke-Round -Work $work -Round "api1" -More @("-QueueUrl", "http://127.0.0.1:$port", "-QueueToken", $token)
        Assert-Equal -Expected 0 -Actual $run.Code -Because "the round runs: $($run.Text)"
        Assert-Equal -Expected "test-lead $opus test-lead" -Actual (@(Get-Calls -Work $work)[0]) -Because "the store's status is read in API mode"
    }
    finally {
        try { [void](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/stop" -TimeoutSec 2) } catch { }
        if (-not $listener.HasExited) { $listener.Kill() }
        Remove-Item -LiteralPath $work.Dir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "the plan run answers 'model_requires_usage_credits' (t-r10070710): it is started once more on the next model and the round runs" {
    $work = New-Work
    try {
        $run = Invoke-Round -Work $work -Round "cr1" -Limited $fable
        Assert-Equal -Expected 0 -Actual $run.Code -Because "the round runs: $($run.Text)"
        Assert-Equal -Expected "test-lead $fable test-lead|test-lead $opus test-lead|tester $opus tester-1" -Actual ((@(Get-Calls -Work $work)) -join "|") -Because "Fable refused, the same plan on Opus"
        Assert-True -Condition ($run.Text -match "model d.s.r.ld.: $fable -> $opus") -Because "said: $($run.Text)"
        Assert-Equal -Expected "passed" -Actual (Get-CardStates -Work $work -Round "cr1") -Because "the job ran"
    }
    finally { Remove-Item -LiteralPath $work.Dir -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "a tester run answers 'model_requires_usage_credits': the job is started once more on the next model, and the next job starts there at once" {
    $work = New-Work
    try {
        $plan = Join-Path $work.Dir "plan.json"
        Write-Utf8 $plan '{"jobs":[{"family":"saglik","scenario":"a.json"},{"family":"iptal","scenario":"b.json"}]}'
        $run = Invoke-Round -Work $work -Round "cr2" -Limited $opus -PlanPath $plan
        Assert-Equal -Expected 0 -Actual $run.Code -Because "the round runs: $($run.Text)"
        Assert-Equal -Expected "tester $opus tester-1|tester $sonnet tester-1|tester $sonnet tester-2" -Actual ((@(Get-Calls -Work $work)) -join "|") -Because "the refused job again on Sonnet; the limit is remembered for the next job"
        Assert-Equal -Expected "passed,passed" -Actual (Get-CardStates -Work $work -Round "cr2") -Because "both jobs ran"
    }
    finally { Remove-Item -LiteralPath $work.Dir -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "retried ONCE: when the next model refuses too, the round gives up with one line and no tester starts" {
    $work = New-Work
    try {
        $run = Invoke-Round -Work $work -Round "cr3" -Limited "$fable,$opus"
        Assert-Equal -Expected 1 -Actual $run.Code -Because "no plan, no round: $($run.Text)"
        Assert-Equal -Expected "test-lead $fable test-lead|test-lead $opus test-lead" -Actual ((@(Get-Calls -Work $work)) -join "|") -Because "one try and one retry, never a third"
        Assert-True -Condition ($run.Text -match "Test PY plan yazmad..*limit") -Because "the line names the limit: $($run.Text)"
    }
    finally { Remove-Item -LiteralPath $work.Dir -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "every model limited: the round stops with one clear line, starts no run and shows no seat working" {
    $work = New-Work
    try {
        Write-Utf8 (Join-Path $work.Team "limits.json") ('{"models":{"' + $fable + '":{"until":"' + $future + '"},"' + $opus + '":{"until":"' + $future + '"},"' + $sonnet + '":{"until":"' + $future + '"}}}')
        $plan = Join-Path $work.Dir "plan.json"
        Write-Utf8 $plan '{"jobs":[{"family":"saglik","scenario":"a.json"},{"family":"iptal","scenario":"b.json"}]}'
        foreach ($case in @(@{ Round = "all1"; Plan = "" }, @{ Round = "all2"; Plan = $plan })) {
            $run = Invoke-Round -Work $work -Round $case.Round -PlanPath $case.Plan
            Assert-Equal -Expected 1 -Actual $run.Code -Because "the round stops ($($case.Round)): $($run.Text)"
            Assert-Equal -Expected 1 -Actual @($run.Lines | Where-Object { $_ -match "modellerin hepsi limitte" }).Count -Because "one clear line ($($case.Round)): $($run.Text)"
            Assert-True -Condition ($run.Text -match [regex]::Escape($future.Substring(0, 10))) -Because "it says when the first limit lifts: $($run.Text)"
        }
        Assert-Equal -Expected 0 -Actual @(Get-Calls -Work $work).Count -Because "no run is started on a limited model"
        Assert-Equal -Expected 0 -Actual @(Get-Posts -Work $work | Where-Object { $_ -match "-Text\|iş: " }).Count -Because "no seat is shown working: $(@(Get-Posts -Work $work) -join ' / ')"
        $states = Get-CardStates -Work $work -Round "all2"
        Assert-True -Condition ($states -eq "" -or $states -eq "planned,planned") -Because "the cards stay planned: $states"
    }
    finally { Remove-Item -LiteralPath $work.Dir -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "every model gives out mid-round: the refused job is not left 'running' on its seat, the rest stay planned, one line" {
    $work = New-Work
    try {
        $plan = Join-Path $work.Dir "plan.json"
        Write-Utf8 $plan '{"jobs":[{"family":"saglik","scenario":"a.json"},{"family":"iptal","scenario":"b.json"}]}'
        $run = Invoke-Round -Work $work -Round "mid1" -Limited "$opus,$sonnet" -PlanPath $plan
        Assert-Equal -Expected "tester $opus tester-1|tester $sonnet tester-1" -Actual ((@(Get-Calls -Work $work)) -join "|") -Because "one try, one retry, nothing more: $($run.Text)"
        Assert-Equal -Expected "environment,planned" -Actual (Get-CardStates -Work $work -Round "mid1") -Because "the refused job never reached staging; the next was not started"
        Assert-Equal -Expected 1 -Actual @($run.Lines | Where-Object { $_ -match "modellerin hepsi limitte" }).Count -Because "one clear line: $($run.Text)"
        $seatNotes = @(Get-Posts -Work $work | Where-Object { $_ -match "-Seat\|tester-1" })
        # The Ofis' Test odası (officeTestRoom.tsx) ends a seat's job only on "sonuç: passed|failed|broke|error".
        Assert-True -Condition (@($seatNotes).Count -ge 1 -and $seatNotes[-1] -match "-Text\|sonuç: error - .*model limiti") -Because "the seat's last note ends its job: $($seatNotes -join ' / ')"
        Assert-Equal -Expected 0 -Actual @(Get-Posts -Work $work | Where-Object { $_ -match "-Seat\|tester-2" }).Count -Because "tester-2 was never shown working"
    }
    finally { Remove-Item -LiteralPath $work.Dir -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Host ""
Write-Host ("testteam-model-fallback: {0} passed, {1} failed" -f $script:Passes, $script:Failures)
if ($script:Failures -gt 0) { exit 1 }
exit 0
