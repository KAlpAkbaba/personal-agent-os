<#
.SYNOPSIS
    The test team (team/plans/test-team-adr.md): its seats, its cap, its cards, its staging-only
    rule, the round that hands jobs to four testers and forwards their failures.

.DESCRIPTION
    The decisions (scripts/testteam/TestTeam.ps1 and the test-team part of
    scripts/lib/TeamQueue.ps1) are driven as functions. The scenario runner is run for real
    against a local HTTP listener that stands in for staging (only for the refusals it is
    pointed at other hosts, and it must refuse them BEFORE it sends anything). The round
    (scripts/testteam/test-round.ps1) is run for real with a fake tester in place of the
    model; what is asserted is what is on disk afterwards: the cards, the queue, the report.

    No model is started; the real staging stack, the board and this repository's team/ files
    are not touched.

    Run: powershell -NoProfile -File scripts\tests\testteam.tests.ps1
#>
[CmdletBinding()]
param([string]$Filter = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\testteam\TestTeam.ps1")

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
    $path = Join-Path $env:TEMP ("pagentos-testteam-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    [void](New-Item -ItemType Directory -Force -Path $path)
    return $path
}

function Write-Utf8 {
    param([string]$Path, [string]$Text)
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

$powershell = Join-Path $PSHOME "powershell.exe"
$runScenario = Join-Path $repoRoot "scripts\testteam\run-scenario.ps1"
$testRound = Join-Path $repoRoot "scripts\testteam\test-round.ps1"

# ============================================================================ staging only

Write-Host ""
Write-Host "staging only"

Test-Case "only the staging api and web on this machine are staging; every near miss is refused" {
    foreach ($good in @("http://127.0.0.1:28001", "http://127.0.0.1:28001/v1/watches", "http://localhost:28000/", "HTTP://127.0.0.1:28000/x?y=1")) {
        Assert-True -Condition (Test-TestTeamStagingUrl -Url $good) -Because "'$good' is staging"
    }
    foreach ($bad in @(
            "http://127.0.0.1:8000", "http://127.0.0.1:3000/", "https://127.0.0.1:28001", "http://127.0.0.2:28001",
            "http://pagentos.tailnet.ts.net:28001", "http://127.0.0.1:28001@evil.example/", "http://user:pw@127.0.0.1:28001/",
            "http://0.0.0.0:28001", "http://[::1]:28001", "ftp://127.0.0.1:28001", "127.0.0.1:28001", "", "http://127.0.0.1"
        )) {
        Assert-True -Condition (-not (Test-TestTeamStagingUrl -Url $bad)) -Because "'$bad' is not staging"
    }
}

Test-Case "the tests' extra port is a port of their own range only: the dev api, the web shell and other services are never staging" {
    # The inspector, 2026-10-05: '-BaseUrl http://127.0.0.1:8000 -AllowTestPort 8000 -DryRun' said
    # "hedefler staging" - the dev api opened by a flag anybody can pass.
    foreach ($port in @(8000, 3000, 7233, 5432, 8001, 28002, 40999, 50000)) {
        Assert-True -Condition (-not (Test-TestTeamStagingUrl -Url "http://127.0.0.1:$port/v1/x" -AllowTestPort $port)) -Because ":$port is not a test port"
    }
    Assert-True -Condition (Test-TestTeamStagingUrl -Url "http://127.0.0.1:41000/" -AllowTestPort 41000) -Because "the range's low end"
    Assert-True -Condition (Test-TestTeamStagingUrl -Url "http://127.0.0.1:49999/" -AllowTestPort 49999) -Because "the range's high end"
    Assert-True -Condition (-not (Test-TestTeamStagingUrl -Url "http://localhost:41000/" -AllowTestPort 41000)) -Because "127.0.0.1 only"
    $work = New-Work
    try {
        $scenario = Join-Path $work "s.json"
        Write-Utf8 $scenario ('{"id":"h","family":"h","steps":[{"name":"saglik","method":"GET","path":"/v1/system/health","expect_status":200}]}')
        $out = & $powershell -NoProfile -File $runScenario -Scenario $scenario -BaseUrl "http://127.0.0.1:8000" -AllowTestPort 8000 -OutDir $work -NoAuth -DryRun 2>&1
        Assert-Equal -Expected 2 -Actual $LASTEXITCODE -Because "the dev api through -AllowTestPort 8000 is refused: $out"
    }
    finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "the breaking ladder's own target is judged too: a ladder aimed off staging refuses the whole scenario" {
    $work = New-Work
    try {
        $scenario = Join-Path $work "l.json"
        Write-Utf8 $scenario ('{"id":"l","family":"l","steps":[{"name":"saglik","method":"GET","path":"/v1/system/health","expect_status":200}],"breaking":{"method":"GET","url":"http://127.0.0.1:8000/v1/system/health","start":2,"max":4}}')
        $out = & $powershell -NoProfile -File $runScenario -Scenario $scenario -OutDir $work -NoAuth -DryRun 2>&1
        Assert-Equal -Expected 2 -Actual $LASTEXITCODE -Because "the ladder goes to :8000: $out"
        Assert-True -Condition (($out -join " ") -match "merdiven") -Because "it names the ladder: $out"
        Write-Utf8 $scenario ('{"id":"l","family":"l","steps":[{"name":"saglik","method":"GET","path":"/v1/system/health","expect_status":200}],"breaking":{"method":"GET","path":"/v1/system/health","start":2,"max":4}}')
        $out = & $powershell -NoProfile -File $runScenario -Scenario $scenario -OutDir $work -NoAuth -DryRun 2>&1
        Assert-Equal -Expected 0 -Actual $LASTEXITCODE -Because "the same ladder on staging's own path is fine: $out"
    }
    finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "results and screenshots are run data: an output folder inside the checkout is refused" {
    $inside = Join-Path $repoRoot "team\testteam\manual-test-$([guid]::NewGuid().ToString('N').Substring(0, 6))"
    $work = New-Work
    try {
        Assert-True -Condition (-not (Test-TestTeamPathOutside -Path $inside -Root $repoRoot)) -Because "inside"
        Assert-True -Condition (Test-TestTeamPathOutside -Path $work -Root $repoRoot) -Because "TEMP is outside"
        Assert-True -Condition (Test-TestTeamPathOutside -Path ($repoRoot + "-sibling\x") -Root $repoRoot) -Because "a sibling folder whose name starts alike is outside"
        $scenario = Join-Path $work "s.json"
        Write-Utf8 $scenario ('{"id":"h","family":"h","steps":[]}')
        $out = & $powershell -NoProfile -File $runScenario -Scenario $scenario -OutDir $inside -NoAuth 2>&1
        Assert-Equal -Expected 2 -Actual $LASTEXITCODE -Because "refused: $out"
        Assert-True -Condition (-not (Test-Path -LiteralPath $inside)) -Because "nothing was written into the tree"
    }
    finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $inside -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "run-scenario refuses a scenario against a non-staging host, exit 2, before it sends anything" {
    $work = New-Work
    try {
        $listener = New-Object System.Net.HttpListener
        $port = Get-Random -Minimum 41000 -Maximum 49000
        $listener.Prefixes.Add("http://127.0.0.1:$port/")
        $listener.Start()
        try {
            $scenario = Join-Path $work "s.json"
            Write-Utf8 $scenario ('{"id":"health","family":"saglik","steps":[{"name":"saglik","method":"GET","path":"/v1/system/health","expect_status":200}]}')
            $out = & $powershell -NoProfile -File $runScenario -Scenario $scenario -BaseUrl "http://127.0.0.1:$port" -OutDir $work -NoAuth 2>&1
            Assert-Equal -Expected 2 -Actual $LASTEXITCODE -Because "a host that is not staging: $out"
            Assert-True -Condition (($out -join " ") -match "STAGING DE") -Because "it says why (the console folds the Turkish letters): $out"
            $pending = $listener.GetContextAsync()
            Assert-True -Condition (-not $pending.Wait(300)) -Because "nothing reached the other host"
        }
        finally { $listener.Stop(); $listener.Close() }
        # A scenario step that names a full url of another host is refused too, whatever -BaseUrl says.
        Write-Utf8 (Join-Path $work "t.json") ('{"id":"jump","family":"x","steps":[{"name":"dis","method":"GET","url":"http://127.0.0.1:8000/v1/system/health","expect_status":200}]}')
        $out = & $powershell -NoProfile -File $runScenario -Scenario (Join-Path $work "t.json") -OutDir $work -NoAuth -DryRun 2>&1
        Assert-Equal -Expected 2 -Actual $LASTEXITCODE -Because "a step's own url is judged as well: $out"
    }
    finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}

# ============================================================================ the cap

Write-Host ""
Write-Host "the cap"

Test-Case "cycle-settings.json names test_parallel (default 4) and the memory floor; the worker seats do not move" {
    $work = New-Work
    try {
        $file = Join-Path $work "cycle-settings.json"
        $read = { param($text)
            if ($null -ne $text) { Write-Utf8 $file $text }
            $s = Read-TeamCycleSettings -Path $file -Workers 4 -Inspectors 3 -Integrators 1
            "$($s.Workers)/$($s.Testers)/$($s.TestFloorGb)/$(@($s.Problems).Count)"
        }
        Assert-Equal -Expected "4/4/8/0" -Actual (& $read $null) -Because "no file: four testers, an 8 GB floor"
        Assert-Equal -Expected "4/2/8/0" -Actual (& $read '{"max_parallel":4,"test_parallel":2}') -Because "the test cap is its own"
        Assert-Equal -Expected "4/0/8/0" -Actual (& $read '{"test_parallel":0}') -Because "0 switches the test team off"
        Assert-Equal -Expected "4/4/12/0" -Actual (& $read '{"test_memory_floor_gb":12}') -Because "the floor"
        foreach ($bad in @('{"test_parallel":-1}', '{"test_parallel":"4"}', '{"test_parallel":17}', '{"test_memory_floor_gb":0}')) {
            Assert-Equal -Expected "4/4/8/1" -Actual (& $read $bad) -Because "'$bad' changes nothing and is said"
        }
    }
    finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "the test cap drops under the memory floor and while the gate holds a heavy slot, and says why" {
    $gb = [int64]1GB
    $free = Get-TeamTestCap -Configured 4 -FreeBytes (20 * $gb) -FloorGb 8 -GateRunning $false
    Assert-Equal -Expected 4 -Actual $free.Cap -Because "enough memory, no gate"
    Assert-Equal -Expected 0 -Actual @($free.Reasons).Count -Because "nothing to say"
    $low = Get-TeamTestCap -Configured 4 -FreeBytes (7 * $gb) -FloorGb 8 -GateRunning $false
    Assert-Equal -Expected 0 -Actual $low.Cap -Because "under the floor nothing new starts"
    Assert-True -Condition ((@($low.Reasons) -join " ") -match "bellek") -Because "the reason names memory: $($low.Reasons)"
    $gate = Get-TeamTestCap -Configured 4 -FreeBytes (20 * $gb) -FloorGb 8 -GateRunning $true
    Assert-Equal -Expected 1 -Actual $gate.Cap -Because "a gate runs: one tester"
    Assert-True -Condition ((@($gate.Reasons) -join " ") -match "kap") -Because "the reason names the gate: $($gate.Reasons)"
    Assert-Equal -Expected 0 -Actual (Get-TeamTestCap -Configured 4 -FreeBytes (2 * $gb) -FloorGb 8 -GateRunning $true).Cap -Because "both: none"
    Assert-Equal -Expected 0 -Actual (Get-TeamTestCap -Configured 0 -FreeBytes (40 * $gb) -FloorGb 8 -GateRunning $false).Cap -Because "switched off stays off"
    Assert-Equal -Expected 4 -Actual (Get-TeamTestCap -Configured 4 -FreeBytes (8 * $gb) -FloorGb 8 -GateRunning $false).Cap -Because "the floor itself is enough"
}

Test-Case "the gate holds a heavy slot only when an entry of the role gate is granted or running with heavy" {
    $e = { param($role, $state, $kinds) [pscustomobject]@{ role = $role; state = $state; kinds = @($kinds) } }
    Assert-True -Condition (Test-TeamGateHoldsHeavy -Entries @((& $e "gate" "running" @("heavy")))) -Because "running"
    Assert-True -Condition (Test-TeamGateHoldsHeavy -Entries @((& $e "gate" "granted" @("database", "heavy")))) -Because "granted"
    Assert-True -Condition (-not (Test-TeamGateHoldsHeavy -Entries @((& $e "gate" "waiting" @("heavy"))))) -Because "a waiting gate holds nothing"
    Assert-True -Condition (-not (Test-TeamGateHoldsHeavy -Entries @((& $e "worker" "running" @("heavy"))))) -Because "a worker's corpus is not the gate"
    Assert-True -Condition (-not (Test-TeamGateHoldsHeavy -Entries @((& $e "gate" "running" @("database"))))) -Because "the gate's database step is not heavy"
    Assert-True -Condition (-not (Test-TeamGateHoldsHeavy -Entries @())) -Because "an empty queue"
}

# ============================================================================ cards and seats

Write-Host ""
Write-Host "cards and seats"

Test-Case "the test team has its own five seats, none of them a software seat" {
    Assert-Equal -Expected "test-lead,tester-1,tester-2,tester-3,tester-4" -Actual ((Get-TestTeamSeats) -join ",") -Because "one test lead, four testers"
    foreach ($seat in @(Get-TestTeamSeats)) { Assert-True -Condition ($seat -notmatch '^(lead|worker-\d|inspector|integrator|researcher)$') -Because "$seat" }
}

Test-Case "a test card moves planned -> running -> passed/failed/broke, and a failed one only back to running" {
    Assert-Equal -Expected "planned,running,passed,failed,broke" -Actual ((Get-TeamTestCardStates) -join ",") -Because "the five states"
    foreach ($ok in @("planned>running", "running>passed", "running>failed", "running>broke", "failed>running", "broke>running", "passed>running")) {
        $p = $ok -split ">"
        Assert-True -Condition (Test-TeamTestCardMove -From $p[0] -To $p[1]) -Because $ok
    }
    foreach ($no in @("planned>passed", "planned>failed", "failed>passed", "broke>passed", "passed>failed", "running>planned", "x>running")) {
        $p = $no -split ">"
        Assert-True -Condition (-not (Test-TeamTestCardMove -From $p[0] -To $p[1])) -Because $no
    }
}

Test-Case "jobs are dealt to the four testers in turn, one scenario family a job" {
    $jobs = @(1..6 | ForEach-Object { [pscustomobject]@{ family = "f$_"; scenario = "s$_.json"; improvise = ($_ -eq 1) } })
    $cards = @(New-TestTeamCards -Round "r1" -Jobs $jobs)
    Assert-Equal -Expected 6 -Actual @($cards).Count -Because "a card per job"
    Assert-Equal -Expected "tester-1,tester-2,tester-3,tester-4,tester-1,tester-2" -Actual ((@($cards) | ForEach-Object { $_.tester }) -join ",") -Because "in turn"
    Assert-Equal -Expected "planned" -Actual (@($cards)[0].state) -Because "a new card is planned"
    Assert-Equal -Expected "tj-r1-1" -Actual (@($cards)[0].id) -Because "ids"
    $twice = @([pscustomobject]@{ family = "a"; scenario = "x" }, [pscustomobject]@{ family = "a"; scenario = "y" })
    $threw = $false
    try { [void](New-TestTeamCards -Round "r1" -Jobs $twice) } catch { $threw = $true }
    Assert-True -Condition $threw -Because "one family is one job"
    # test-lead.md: a family with no scenario file is an improvise job; its tester writes one.
    $new = @(New-TestTeamCards -Round "r1" -Jobs @([pscustomobject]@{ family = "ev-stoku"; scenario = $null; improvise = $true; why = "yeni" }))
    Assert-Equal -Expected "" -Actual ([string]@($new)[0].scenario) -Because "no file yet: the tester writes it"
    Assert-True -Condition (@($new)[0].improvise) -Because "the job is to improvise"
    $threw = $false
    try { [void](New-TestTeamCards -Round "r1" -Jobs @([pscustomobject]@{ family = "x"; scenario = $null; improvise = $false })) } catch { $threw = $true }
    Assert-True -Condition $threw -Because "a scripted job with no file names nothing to run"
}

Test-Case "a failure becomes a software card with steps, expected, actual, the scenario, the screenshot and the staging sha; two alike are one" {
    $failure = [pscustomobject]@{
        card = "tj-r1-2"; tester = "tester-2"; family = "nobet"; scenario = "scripts/testteam/scenarios/watches.json"
        step = "nöbet listesi"; steps = @("POST /v1/watches", "GET /v1/watches"); expected = "200"; actual = "500"
        screenshot = "team/testteam/r1/shots/tj-r1-2.png"
    }
    $same = $failure.PSObject.Copy(); $same.card = "tj-r1-5"
    $merged = @(Merge-TestTeamFailures -Failures @($failure, $same))
    Assert-Equal -Expected 1 -Actual @($merged).Count -Because "the same scenario, step and actual are one failure"
    $task = ConvertTo-TestTeamFailureTask -Failure $failure -StagingSha ("a" * 40) -Round "r1" -Now "2026-10-05T12:00:00Z"
    Assert-Equal -Expected "proposed" -Actual $task.state -Because "the software Proje Yöneticisi decides"
    foreach ($word in @("Adımlar", "Beklenen: 200", "Gerçekleşen: 500", "watches.json", "tj-r1-2.png", ("a" * 40))) {
        Assert-True -Condition ($task.goal -match [regex]::Escape($word)) -Because "the goal carries '$word': $($task.goal)"
    }
    $schemaProblems = @(Test-TeamQueue -Queue ([pscustomobject]@{ version = 1; tasks = @($task) }))
    Assert-Equal -Expected 0 -Actual @($schemaProblems).Count -Because "a normal card of the queue: $($schemaProblems -join '; ')"
}

Test-Case "a forwarded card carries a first area from its family's known code paths; an unknown family carries none" {
    # 2026-10-06 21:02: the first automatic round forwarded 19 cards with NO area; seven moved to
    # 'assigned' as they were and the whole team stopped. The Proje Yöneticisi widens a first
    # area; it does not invent one.
    $expected = [ordered]@{
        "nobet" = "services/api/app/watch/"; "ev-stoku" = "services/api/app/household/"
        "alarm" = "services/api/app/alarms/"; "dil-dayanikliligi" = "services/api/app/voice/understanding/"
    }
    foreach ($family in @($expected.Keys)) {
        $failure = [pscustomobject]@{
            card = "tj-r1-1"; tester = "tester-1"; family = $family; scenario = "scripts/testteam/scenarios/$family.json"
            step = "adım"; steps = @("GET /v1/x"); expected = "200"; actual = "500"; screenshot = ""
        }
        $task = ConvertTo-TestTeamFailureTask -Failure $failure -Round "r1" -Now "2026-10-06T21:02:00Z"
        Assert-True -Condition (@($task.area) -contains $expected[$family]) -Because "$family -> $($expected[$family]); got: $(@($task.area) -join ', ')"
        Assert-Equal -Expected "proposed" -Actual $task.state -Because "the area is a first one; the Proje Yöneticisi still decides"
        $problems = @(Test-TeamQueue -Queue ([pscustomobject]@{ version = 1; tasks = @($task) }))
        Assert-Equal -Expected 0 -Actual @($problems).Count -Because "a normal card of the queue: $($problems -join '; ')"
    }
    $unknown = [pscustomobject]@{ card = "tj-r1-9"; tester = "tester-1"; family = "bilinmeyen"; scenario = "x.json"; step = "a"; steps = @(); expected = "1"; actual = "2"; screenshot = "" }
    Assert-Equal -Expected 0 -Actual @((ConvertTo-TestTeamFailureTask -Failure $unknown -Round "r1").area).Count -Because "no known path: no invented area"
}

Test-Case "a failure's id does not hang on how the scenario path was spelled" {
    # The inspector, 2026-10-05: an absolute and a relative path of one scenario were two cards.
    $base = [pscustomobject]@{ family = "nobet"; step = "nöbet listesi"; actual = "500"; scenario = "scripts/testteam/scenarios/watches.json" }
    $ids = foreach ($spelling in @(
            "scripts/testteam/scenarios/watches.json", "scripts\testteam\scenarios\watches.json", ".\scripts\testteam\scenarios\watches.json",
            "E:\AI\PersonalAgentOS\scripts\testteam\scenarios\watches.json",
            "E:\AI\PersonalAgentOS\.claude\worktrees\team\d1\worker-x\scripts\testteam\scenarios\Watches.json")) {
        $f = $base.PSObject.Copy(); $f.scenario = $spelling
        Get-TestTeamFailureTaskId -Failure $f
    }
    Assert-Equal -Expected 1 -Actual @($ids | Select-Object -Unique).Count -Because "one scenario, one id: $($ids -join ', ')"
    $other = $base.PSObject.Copy(); $other.scenario = "scripts/testteam/scenarios/health.json"
    Assert-True -Condition ((Get-TestTeamFailureTaskId -Failure $other) -ne @($ids)[0]) -Because "another scenario is another failure"
    $spelled = @(foreach ($s in @("scripts/testteam/scenarios/watches.json", "E:\x\scripts\testteam\scenarios\watches.json")) { $f = $base.PSObject.Copy(); $f.scenario = $s; $f })
    Assert-Equal -Expected 1 -Actual @(Merge-TestTeamFailures -Failures $spelled).Count -Because "two testers, two spellings, one card"
}

Test-Case "a fix is re-tested only once it left for main AND staging no longer serves the sha the failure was found on" {
    # The inspector, 2026-10-05: -Retest took 'merged' and never looked at staging's sha - staging
    # was still 6a21294c, so the retest judged the old staging and reopened a fixed card.
    $found = "6a21294c4aa6664e843938fca903a4002041508d"; $new = "b30df6c547ebc8afb826441ac56091688aba2c3f"
    foreach ($state in @("released", "done", "awaiting_real_evidence")) {
        Assert-True -Condition (Get-TestTeamRetestDecision -TaskState $state -FoundSha $found -StagingSha $new).Due -Because "$state, staging redeployed"
    }
    foreach ($state in @("merged", "proposed", "in_progress", "awaiting_release", "returned")) {
        Assert-True -Condition (-not (Get-TestTeamRetestDecision -TaskState $state -FoundSha $found -StagingSha $new).Due) -Because "$state is not on staging"
    }
    $same = Get-TestTeamRetestDecision -TaskState "released" -FoundSha $found -StagingSha $found.ToUpperInvariant()
    Assert-True -Condition (-not $same.Due) -Because "staging still serves the failing sha"
    Assert-True -Condition ($same.Why -match "hâlâ") -Because "and says so: $($same.Why)"
    Assert-True -Condition (-not (Get-TestTeamRetestDecision -TaskState "released" -FoundSha $found -StagingSha "").Due) -Because "an unreadable staging is not judged"
}

Test-Case "one result whose steps fail alike is one failure, the others named in it; a different actual is a failure of its own" {
    # Found by the first real round (2026-10-05): staging was a release behind, every watch step
    # answered 404, and five cards were opened for one cause.
    $step = { param($name, $actual, $ok) [pscustomobject]@{ name = $name; method = "GET"; path = "/v1/$name"; expected = "200"; actual = $actual; ok = $ok } }
    $result = [pscustomobject]@{ card = "tj-1"; tester = "tester-2"; family = "nobet"; scenario = "w.json"; state = "failed"; screenshot = ""
        steps = @((& $step "a" "404" $false), (& $step "b" "404" $false), (& $step "c" "200" $true), (& $step "d" "404" $false), (& $step "e" "500" $false)) }
    $failures = @(Get-TestTeamFailures -Result $result)
    Assert-Equal -Expected "a,e" -Actual ((@($failures) | ForEach-Object { $_.step }) -join ",") -Because "404 once, 500 once"
    Assert-Equal -Expected "b,d" -Actual ((@(@($failures)[0].alike)) -join ",") -Because "the steps that failed alike are named"
    $task = ConvertTo-TestTeamFailureTask -Failure @($failures)[0] -Round "r1" -Now "2026-10-05T12:00:00Z"
    Assert-True -Condition ($task.goal -match "Aynı sonuçla kalan adımlar: b, d") -Because "the card says so: $($task.goal)"
}

Test-Case "the breaking-point report is short, names the first failing load with its numbers, and is addressed to the Danışman" {
    $results = @(
        [pscustomobject]@{ card = "tj-r1-1"; tester = "tester-1"; family = "yuk"; breaking = [pscustomobject]@{
                tried = @([pscustomobject]@{ load = 1; ok = 1; errors = 0; p95_ms = 40 }, [pscustomobject]@{ load = 8; ok = 8; errors = 0; p95_ms = 90 }, [pscustomobject]@{ load = 32; ok = 20; errors = 12; p95_ms = 4100 })
                first_failure = [pscustomobject]@{ load = 32; ok = 20; errors = 12; p95_ms = 4100 }; what = "GET /v1/watches eşzamanlı" } },
        [pscustomobject]@{ card = "tj-r1-2"; tester = "tester-2"; family = "iptal"; breaking = [pscustomobject]@{ tried = @([pscustomobject]@{ load = 4; ok = 4; errors = 0; p95_ms = 50 }); first_failure = $null; what = "aynı istek iki kez" } }
    )
    $report = Format-TestTeamBreakingReport -Round "r1" -Results $results -StagingSha ("b" * 40)
    Assert-True -Condition ($report.Note.Length -le 280) -Because "a board note: $($report.Note.Length)"
    Assert-True -Condition ($report.Note -match "^Danışman'a") -Because "addressed: $($report.Note)"
    Assert-Equal -Expected "danisman" -Actual $report.To -Because "the note goes to the Danışman's seat on the board, not to everyone"
    Assert-True -Condition ($report.Note -match "32" -and $report.Note -match "12") -Because "the numbers: $($report.Note)"
    Assert-True -Condition ($report.Markdown -match "kırılmadı" -and $report.Markdown -match "aynı istek iki kez") -Because "what was tried and did not break is said"
    Assert-True -Condition ($report.Markdown -notmatch "sahib") -Because "the report goes to the Danışman, not to the owner"
    # A ladder of a scenario whose steps already failed measures that failure, not a load
    # (the first real round: every watch request 404, "broke at load 2").
    $failedToo = @([pscustomobject]@{ card = "tj-r1-3"; tester = "tester-3"; family = "nobet"; state = "failed"; breaking = [pscustomobject]@{
                tried = @([pscustomobject]@{ load = 2; ok = 0; errors = 2; p95_ms = 30 }); first_failure = [pscustomobject]@{ load = 2; ok = 0; errors = 2; p95_ms = 30 }; what = "POST /v1/watches" } }) + $results
    $report = Format-TestTeamBreakingReport -Round "r1" -Results $failedToo
    Assert-True -Condition ($report.Note -match "yük 32" -and $report.Note -notmatch "yük 2 ") -Because "the headline is the real breaking point: $($report.Note)"
    Assert-True -Condition ($report.Markdown -match "ölçüm geçersiz") -Because "the failed scenario's ladder is said, and why it does not count: $($report.Markdown)"
    # 2026-10-06: an improvised result a tester wrote by hand has no tester or card; the round
    # died here after every tester had finished, and no report was written.
    $handWritten = @([pscustomobject]@{ family = "nobet"; state = "broke"; breaking = [pscustomobject]@{
                tried = @([pscustomobject]@{ load = 8; ok = 6; errors = 2; p95_ms = 40 }); first_failure = [pscustomobject]@{ load = 8; ok = 6; errors = 2; p95_ms = 40 }; what = "POST /v1/watches" } }) + $results
    $report = Format-TestTeamBreakingReport -Round "r1" -Results $handWritten
    Assert-True -Condition ($report.Markdown -match "nobet \(\?, \?\)") -Because "a result without tester and card is reported, not thrown: $($report.Markdown)"
}

Test-Case "a tester's board note is what the Ofis' Test odası reads: 'iş: <job>', then 'sonuç: <state> - <job> - kopma: ...'" {
    $card = [pscustomobject]@{ id = "tj-r1-2"; family = "saglik"; state = "running" }
    Assert-Equal -Expected "iş: saglik (tj-r1-2)" -Actual (Format-TestTeamSeatNote -Card $card) -Because "a running card is the seat's job"
    $card.state = "broke"
    $result = [pscustomobject]@{ breaking = [pscustomobject]@{ first_failure = [pscustomobject]@{ load = 256; ok = 250; errors = 6; p95_ms = 7982 } } }
    Assert-Equal -Expected "sonuç: broke - saglik (tj-r1-2) - kopma: yük 256, 6 hata / 256, p95 7982 ms" -Actual (Format-TestTeamSeatNote -Card $card -Result $result) -Because "a broken card carries its numbers"
    # 2026-10-06: a hand-written result's rung without p95_ms stopped a round mid-way.
    $handWritten = [pscustomobject]@{ breaking = [pscustomobject]@{ first_failure = [pscustomobject]@{ load = 8; ok = 4; errors = 4 } } }
    Assert-Equal -Expected "sonuç: broke - saglik (tj-r1-2) - kopma: yük 8, 4 hata / 8, p95 ? ms" -Actual (Format-TestTeamSeatNote -Card $card -Result $handWritten) -Because "a missing field is '?', not a thrown round"
    $report = Format-TestTeamBreakingReport -Round "r1" -Results @([pscustomobject]@{ family = "x"; state = "broke"; breaking = [pscustomobject]@{ what = "w"; tried = @([pscustomobject]@{ load = 8 }); first_failure = [pscustomobject]@{ load = 8 } } })
    Assert-True -Condition ($report.Markdown -match "ilk kırılan yük 8") -Because "a rung with only its load is still reported: $($report.Markdown)"
    $card.state = "passed"
    Assert-Equal -Expected "sonuç: passed - saglik (tj-r1-2)" -Actual (Format-TestTeamSeatNote -Card $card -Result $result) -Because "a passed card is just its end"
}

# ============================================================================ a real scenario run

Write-Host ""
Write-Host "a scenario against a stand-in staging"

function Start-FakeStaging {
    # A listener on the staging api's port would collide with the real staging stack; the
    # runner's -AllowTestPort is the one way to point it elsewhere, and only at 127.0.0.1 on a
    # port of the tests' range. It answers each request on its own thread (a serial listener
    # would queue the ladder's requests and every timing would be the queue's):
    #   /broken 500; /load 503 above X-Load $FailAbove; /slowone holds the FIRST request 1.5 s;
    #   /flaky 503 to the first request of load 16 only; everything else 200 with the sha.
    # The session: 'Bearer dead' (a staging credential rotated under it) is 401 everywhere but
    # /broken, /v1/identity/sessions/current is 200 only for 'Bearer good', /unauth is 401 to all. With -Log every request's
    # method and path is one line of that file (what reached staging).
    param([int]$Port, [int]$FailAbove = 0, [string]$Sha = ("c" * 40), [string]$Log = "")
    $script = @"
Add-Type -TypeDefinition @'
using System;
using System.Net;
using System.Text;
using System.Threading;
public static class FakeStaging {
    static int slowSeen = 0;
    static int flakySeen = 0;
    static string log = "";
    static readonly object logLock = new object();
    public static void Run(int port, int failAbove, string sha, string logFile) {
        log = logFile;
        var listener = new HttpListener();
        listener.Prefixes.Add("http://127.0.0.1:" + port + "/");
        listener.Start();
        while (true) {
            var c = listener.GetContext();
            if (c.Request.Url.AbsolutePath == "/stop") { c.Response.Close(); break; }
            ThreadPool.QueueUserWorkItem(delegate { Serve(c, failAbove, sha); });
        }
        listener.Stop();
    }
    static void Serve(HttpListenerContext c, int failAbove, string sha) {
        try {
            string p = c.Request.Url.AbsolutePath;
            int code = 200;
            string body = "{\"status\":\"ok\",\"release\":{\"version\":\"" + sha + "\"},\"items\":[]}";
            int load = 0;
            Int32.TryParse(c.Request.Headers["X-Load"] ?? "0", out load);
            if (log.Length > 0) { lock (logLock) { System.IO.File.AppendAllText(log, c.Request.HttpMethod + " " + p + "\n"); } }
            string auth = c.Request.Headers["Authorization"] ?? "";
            if (p == "/v1/identity/sessions/current" && auth != "Bearer good") { code = 401; body = "{\"detail\":\"unauthorized\"}"; }
            if (auth == "Bearer dead") { code = 401; body = "{\"detail\":\"unauthorized\"}"; }
            if (p == "/broken") { code = 500; body = "{\"detail\":\"boom\"}"; }
            if (p == "/unauth") { code = 401; body = "{\"detail\":\"unauthorized\"}"; }
            if (p == "/load" && failAbove > 0 && load > failAbove) { code = 503; body = "{}"; }
            if (p == "/slowone" && Interlocked.Increment(ref slowSeen) == 1) { Thread.Sleep(1500); }
            if (p == "/flaky" && load == 16 && Interlocked.Increment(ref flakySeen) == 1) { code = 503; body = "{}"; }
            if (p == "/big") { body = "{\"items\":\"" + new String('x', 6000) + "\"}"; }
            if (p == "/secret") { body = "{\"access_token\":\"tok-SIRRI-1234\",\"title\":\"ok\"}"; }
            byte[] bytes = Encoding.UTF8.GetBytes(body);
            c.Response.StatusCode = code;
            c.Response.OutputStream.Write(bytes, 0, bytes.Length);
            c.Response.Close();
        }
        catch (Exception) { }
    }
}
'@
[System.Threading.ThreadPool]::SetMinThreads(64, 64) | Out-Null
[FakeStaging]::Run($Port, $FailAbove, '$Sha', '$Log')
"@
    $file = Join-Path $env:TEMP ("pagentos-fake-staging-$Port.ps1")
    Write-Utf8 $file $script
    $p = Start-Process -FilePath $powershell -ArgumentList @("-NoProfile", "-File", $file) -PassThru -WindowStyle Hidden
    for ($i = 0; $i -lt 50; $i++) {
        try { [void](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/v1/system/health" -TimeoutSec 2); break } catch { Start-Sleep -Milliseconds 200 }
    }
    return $p
}

Test-Case "a scenario run writes passed/failed with expected and actual per step, the staging sha, and a breaking-point ladder" {
    $work = New-Work
    $port = Get-Random -Minimum 41000 -Maximum 49000
    $fake = Start-FakeStaging -Port $port -FailAbove 8
    try {
        $scenario = Join-Path $work "s.json"
        Write-Utf8 $scenario ('{"id":"mix","family":"karma","steps":[{"name":"saglik","method":"GET","path":"/v1/system/health","expect_status":200,"expect_contains":"ok"},{"name":"kirik","method":"GET","path":"/broken","expect_status":200}],"breaking":{"method":"GET","path":"/load","start":2,"factor":2,"max":32,"what":"yuk merdiveni"}}')
        $out = & $powershell -NoProfile -File $runScenario -Scenario $scenario -BaseUrl "http://127.0.0.1:$port" -AllowTestPort $port -OutDir $work -NoAuth -Card "tj-t-1" 2>&1
        Assert-Equal -Expected 1 -Actual $LASTEXITCODE -Because "a failing step is exit 1: $out"
        $result = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $work "tj-t-1.result.json") | ConvertFrom-Json
        Assert-Equal -Expected "failed" -Actual $result.state -Because "one step failed"
        Assert-Equal -Expected ("c" * 40) -Actual $result.staging_sha -Because "the sha staging said"
        Assert-Equal -Expected "500" -Actual ([string]@($result.steps)[1].actual) -Because "the actual status"
        Assert-Equal -Expected "200" -Actual ([string]@($result.steps)[1].expected) -Because "the expected status"
        Assert-Equal -Expected 16 -Actual ([int]$result.breaking.first_failure.load) -Because "2,4,8 pass; 16 is the first over 8: $(@($result.breaking.tried | ForEach-Object { $_.load }) -join ',')"
        Assert-True -Condition ([int]$result.breaking.first_failure.errors -gt 0) -Because "with its errors counted"
        Assert-True -Condition ([int]$result.breaking.first_failure.repeat.errors -gt 0) -Because "the breaking load was measured twice and broke twice: $($result.breaking.first_failure | ConvertTo-Json -Compress)"
    }
    finally {
        try { [void](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/stop" -TimeoutSec 2) } catch { }
        if (-not $fake.HasExited) { $fake.Kill() }
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "the ladder's p95 is each request's own time: one slow request among 32 is not 32 slow ones" {
    # The inspector, 2026-10-05: the p95 was read from one clock while the requests were awaited
    # in order - the first slow answer made every later one 'slow', and the breaking load of one
    # staging sha moved between 128 and 256.
    $work = New-Work
    $port = Get-Random -Minimum 41000 -Maximum 49000
    $fake = Start-FakeStaging -Port $port
    try {
        $scenario = Join-Path $work "p.json"
        Write-Utf8 $scenario ('{"id":"p","family":"p95","steps":[],"breaking":{"method":"GET","path":"/slowone","start":32,"factor":2,"max":32,"max_p95_ms":1000,"what":"bir yavas istek"}}')
        $out = & $powershell -NoProfile -File $runScenario -Scenario $scenario -BaseUrl "http://127.0.0.1:$port" -AllowTestPort $port -OutDir $work -NoAuth -Card "tj-p" 2>&1
        $result = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $work "tj-p.result.json") | ConvertFrom-Json
        Assert-Equal -Expected 0 -Actual $LASTEXITCODE -Because "31 fast answers and one slow one do not break a 1000 ms p95: $out"
        Assert-True -Condition ($null -eq $result.breaking.first_failure) -Because "no breaking point: $($result.breaking | ConvertTo-Json -Compress -Depth 5)"
        Assert-True -Condition ([int]@($result.breaking.tried)[0].p95_ms -lt 1000) -Because "the p95 is not the slow one's 1500 ms: $(@($result.breaking.tried)[0].p95_ms)"
        Assert-True -Condition ([int]@($result.breaking.tried)[0].max_ms -ge 1400) -Because "the slow one was there and is kept as the slowest: $(@($result.breaking.tried)[0].max_ms)"
    }
    finally {
        try { [void](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/stop" -TimeoutSec 2) } catch { }
        if (-not $fake.HasExited) { $fake.Kill() }
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "a load that breaks once and holds when measured again is 'flaky', not the breaking point; the ladder goes on" {
    $work = New-Work
    $port = Get-Random -Minimum 41000 -Maximum 49000
    $fake = Start-FakeStaging -Port $port
    try {
        $scenario = Join-Path $work "f.json"
        Write-Utf8 $scenario ('{"id":"f","family":"oynak","steps":[],"breaking":{"method":"GET","path":"/flaky","start":2,"factor":2,"max":32,"what":"oynak"}}')
        $out = & $powershell -NoProfile -File $runScenario -Scenario $scenario -BaseUrl "http://127.0.0.1:$port" -AllowTestPort $port -OutDir $work -NoAuth -Card "tj-f" 2>&1
        $result = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $work "tj-f.result.json") | ConvertFrom-Json
        Assert-Equal -Expected 0 -Actual $LASTEXITCODE -Because "nothing broke twice: $out"
        Assert-Equal -Expected "2,4,8,16,32" -Actual ((@($result.breaking.tried) | ForEach-Object { $_.load }) -join ",") -Because "the ladder went past 16"
        $sixteen = @($result.breaking.tried)[3]
        Assert-True -Condition ([bool](Get-TeamProperty -InputObject $sixteen -Name "flaky" -Default $false)) -Because "16 is marked flaky: $($sixteen | ConvertTo-Json -Compress)"
        Assert-Equal -Expected 0 -Actual ([int]$sixteen.repeat.errors) -Because "its second measurement held"
    }
    finally {
        try { [void](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/stop" -TimeoutSec 2) } catch { }
        if (-not $fake.HasExited) { $fake.Kill() }
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "a scenario run records each step's input and output: the body as sent, the answer cut at 4 KB, the session and secret fields masked" {
    # The owner, 2026-10-06: "test ekibinin yaptığı işlemleri ve aldığı sonuçların girdi çıktı
    # olarak raporlarını istiyorum". The result kept a status; not what was sent nor what came back.
    $work = New-Work
    $port = Get-Random -Minimum 41000 -Maximum 49000
    $fake = Start-FakeStaging -Port $port
    $sessionToken = "pagentos" + "_st_" + "OturumAnahtariTestIcin0123456789abcdefghij"
    try {
        $session = Join-Path $work "owner.json"
        Write-Utf8 $session ('{"session_token":"' + $sessionToken + '"}')
        $scenario = Join-Path $work "io.json"
        Write-Utf8 $scenario ('{"id":"io","family":"girdi-cikti","steps":[{"name":"büyük yanıt","method":"GET","path":"/big","expect_status":200},{"name":"giriş","method":"POST","path":"/secret","body":{"title":"Not al","password":"hunter2-gizli"},"expect_status":200,"expect_contains":"ok"}]}')
        $out = & $powershell -NoProfile -File $runScenario -Scenario $scenario -BaseUrl "http://127.0.0.1:$port" -AllowTestPort $port -OutDir $work -SessionFile $session -Card "tj-io" 2>&1
        Assert-Equal -Expected 0 -Actual $LASTEXITCODE -Because "both steps pass: $out"
        $raw = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $work "tj-io.result.json")
        $result = $raw | ConvertFrom-Json
        $big = @($result.steps)[0]
        Assert-Equal -Expected "GET" -Actual ([string]$big.input.method) -Because "the input names the method"
        Assert-Equal -Expected "/big" -Actual ([string]$big.input.path) -Because "and the path"
        Assert-Equal -Expected "Bearer ***" -Actual ([string]$big.input.headers.Authorization) -Because "the session is masked"
        Assert-Equal -Expected "200" -Actual ([string]$big.output.status) -Because "the output's status"
        $body = [string]$big.output.body
        Assert-True -Condition ($body.StartsWith('{"items":"xxxx')) -Because "the answer as it came: $($body.Substring(0, 30))"
        Assert-True -Condition ($body.Length -lt 4200 -and $body.Length -gt 4096) -Because "cut at 4096 with a marker: $($body.Length)"
        Assert-True -Condition ($body -match 'kesildi') -Because "the marker says it was cut"
        $login = @($result.steps)[1]
        Assert-True -Condition ([string]$login.input.body -match '"title":"Not al"') -Because "the body as sent: $($login.input.body)"
        Assert-True -Condition ([string]$login.input.body -match '"password":"\*\*\*"') -Because "a password field masked: $($login.input.body)"
        Assert-True -Condition ([string]$login.output.body -match '"access_token":"\*\*\*"') -Because "a token field in the answer masked: $($login.output.body)"
        foreach ($secret in @($sessionToken, "hunter2-gizli", "tok-SIRRI-1234")) {
            Assert-True -Condition (-not $raw.Contains($secret)) -Because "'$secret' is nowhere in the result file"
        }
        # Inspector, 2026-10-07: the report cut the cut body again and its marker said "4134
        # karakterin" - the answer was 6012 ({"items":"<6000 x>"}).
        $md = (Format-TestTeamRoundReport -Round "r-io" -Cards @([pscustomobject]@{ id = "tj-io"; tester = "tester-1"; family = "girdi-cikti"; state = "passed" }) -Results @($result)).Markdown
        Assert-Equal -Expected 1 -Actual ([regex]::Matches($md, 'kesildi').Count) -Because "one marker in the report: $([regex]::Matches($md, '…\[kesildi[^\]]*\]') | ForEach-Object { $_.Value })"
        Assert-True -Condition ($md.Contains("…[kesildi: 6012 karakterin ilk 4096'i]")) -Because "the marker gives the answer's own length"
    }
    finally {
        try { [void](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/stop" -TimeoutSec 2) } catch { }
        if (-not $fake.HasExited) { $fake.Kill() }
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "masking and cutting: token, secret and password fields, Bearer values and the session itself; masked before cut" {
    $masked = Protect-TestTeamText -Text '{"refresh_token":"r-1","clientSecret":"s-2","Password":12345,"name":"ali","nested":{"api_token":"x"}}'
    Assert-Equal -Expected '{"refresh_token":"***","clientSecret":"***","Password":"***","name":"ali","nested":{"api_token":"***"}}' -Actual $masked -Because "every such field"
    Assert-Equal -Expected "Authorization: ***" -Actual (Protect-TestTeamText -Text "Authorization: Bearer abc.def") -Because "a header line"
    Assert-Equal -Expected "oturum *** bitti" -Actual (Protect-TestTeamText -Text "oturum ABCDEFGH123 bitti" -Secrets @("ABCDEFGH123")) -Because "the session token wherever it stands"
    Assert-Equal -Expected "kısa" -Actual (Limit-TestTeamText -Text "kısa") -Because "a short body is whole"
    $cut = ConvertTo-TestTeamShownText -Text (("a" * 4080) + '"token":"GIZLIGIZLIGIZLI"')
    Assert-True -Condition (-not $cut.Contains("GIZLI")) -Because "masked first, so the cut never shows half a secret: $($cut.Substring(4080))"
    # The session itself across the cut: cut first, its first half would no longer match it.
    $session = "QWERTYUIOPASDFGHJKLZ"
    $cut = ConvertTo-TestTeamShownText -Text (("a" * 4090) + $session) -Secrets @($session)
    Assert-True -Condition (-not $cut.Contains("QWERTY")) -Because "no half of the session shows: $($cut.Substring(4085))"
}

Test-Case "a cut body cut again keeps the first length; a cut never splits a surrogate pair; NUL and lone surrogates are cleaned" {
    $once = Limit-TestTeamText -Text ("y" * 6012)
    Assert-Equal -Expected $once -Actual (Limit-TestTeamText -Text $once) -Because "a body already cut is left as it is"
    Assert-Equal -Expected $once -Actual (ConvertTo-TestTeamShownText -Text $once) -Because "also through mask-then-cut"
    $twice = Limit-TestTeamText -Text $once -Max 100
    Assert-Equal -Expected (("y" * 100) + "…[kesildi: 6012 karakterin ilk 100'i]") -Actual $twice -Because "cut shorter, the marker still gives 6012"
    $emoji = [char]::ConvertFromUtf32(0x1F600)
    $split = Limit-TestTeamText -Text (("a" * 4095) + $emoji + "b")
    Assert-True -Condition (-not [char]::IsHighSurrogate($split[4094])) -Because "the pair is not cut in half: $([int]$split[4094])"
    Assert-True -Condition ($split.StartsWith(("a" * 4095) + "…[kesildi: 4098 karakterin ilk 4095'i]")) -Because "cut one earlier: $($split.Substring(4090))"
    $dirty = "a" + [char]0 + "b" + [char]0xD83D + "c" + [char]0xDE00 + "d" + $emoji
    Assert-Equal -Expected ("a␀b" + [char]0xFFFD + "c" + [char]0xFFFD + "d" + $emoji) -Actual (ConvertTo-TestTeamCleanText -Text $dirty) -Because "NUL shown, a lone half replaced, a whole pair kept"
    $cards = @([pscustomobject]@{ id = "tj-n-1"; tester = "tester-1"; family = "ikili"; state = "failed" })
    $results = @([pscustomobject]@{ card = "tj-n-1"; steps = @([pscustomobject]@{ name = "ikili"; ok = $false; input = [pscustomobject]@{ method = "GET"; path = "/x"; body = "" }; output = [pscustomobject]@{ status = "200"; body = $dirty } }) })
    $md = (Format-TestTeamRoundReport -Round "r-n" -Cards $cards -Results $results -Unfinished ("kopma" + [char]0)).Markdown
    Assert-True -Condition ($md.Contains($emoji) -and $md.Replace($emoji, "") -notmatch '[\x00\uD800-\uDFFF]') -Because "the report holds no NUL and no lone surrogate (the Cloud Core refuses them)"
    Assert-True -Condition (-not $md.Contains([string][char]0)) -Because "no NUL in the report"
    Assert-True -Condition ($md.Contains("yarım kaldı: kopma␀")) -Because "nor in its why"
}

Test-Case "the POST body is cleaned after the last cut: an emoji-heavy report over 256 KB never sends a lone surrogate; the why is at most 500 characters, cleaned" {
    # Inspector, 2026-10-07: the 256 KB cut backs off on a character index and landed between the
    # halves of a pair in 43 of 123 reports; the Cloud Core then refused the round's report (422).
    $emoji = [char]::ConvertFromUtf32(0x1F600)
    $strict = New-Object System.Text.UTF8Encoding($false, $true)
    $counts = [ordered]@{ passed = 0; failed = 1; broke = 0 }
    $lone = @()
    # 16 leading letters move the cut across the pairs; before the fix lead 11 sent a lone half.
    for ($lead = 0; $lead -lt 16; $lead++) {
        $md = ("a" * $lead) + ($emoji * 70000)
        $body = New-TestTeamReportBody -Round "r-e" -StagingSha "" -Counts $counts -Unfinished "" -Markdown $md
        try { $bytes = $strict.GetByteCount([string]$body.text) } catch { $lone += $lead; continue }
        Assert-True -Condition ($bytes -le $script:TestTeamReportMaxBytes) -Because "at most 256 KB: $bytes"
        Assert-True -Condition ([string]$body.text).EndsWith("tamamı tur klasöründe]`n") -Because "cut with the marker"
    }
    Assert-Equal -Expected "" -Actual ($lone -join ",") -Because "no body with a lone surrogate (leads listed)"
    $why = [string][char]0 + [char]0xD83D + "x" + ("ö" * 700)
    $body = New-TestTeamReportBody -Round "r-u" -StagingSha ("d" * 40) -Counts $counts -Unfinished $why -Markdown "# Test turu r-u"
    Assert-Equal -Expected 500 -Actual ([string]$body.unfinished).Length -Because "the route takes at most 500 characters"
    Assert-True -Condition ([string]$body.unfinished).StartsWith("␀" + [char]0xFFFD + "xö") -Because "NUL shown, the lone half replaced: $(([string]$body.unfinished).Substring(0, 4))"
    Assert-Equal -Expected "r-u" -Actual ([string]$body.round) -Because "the round"
    Assert-Equal -Expected ("d" * 40) -Actual ([string]$body.staging_sha) -Because "the staging sha"
    Assert-Equal -Expected 1 -Actual ([int]$body.counts.failed) -Because "the counts"
    Assert-Equal -Expected "# Test turu r-u" -Actual ([string]$body.text) -Because "a short report is sent whole"
}

Test-Case "the round report: Girdi / Beklenen / Çıktı / Sonuç for every step, the plan's why, the ladder table, the forwarded cards; a dead round says 'yarım kaldı'" {
    $cards = @(
        [pscustomobject]@{ id = "tj-r-1"; tester = "tester-1"; family = "saglik"; state = "passed" },
        [pscustomobject]@{ id = "tj-r-2"; tester = "tester-2"; family = "yuk"; state = "broke" },
        [pscustomobject]@{ id = "tj-r-3"; tester = "tester-3"; family = "kirik"; state = "running" }
    )
    $jobs = @(
        [pscustomobject]@{ family = "saglik"; why = "Stage 58 yayında" },
        [pscustomobject]@{ family = "yuk" },
        [pscustomobject]@{ family = "kirik"; why = "dikili hata" }
    )
    $results = @(
        [pscustomobject]@{ card = "tj-r-1"; scenario = "s.json"; steps = @(
                [pscustomobject]@{ name = "sağlık"; method = "GET"; path = "/v1/system/health"; expected = "200"; actual = "200"; ok = $true; ms = 12
                    input = [pscustomobject]@{ method = "GET"; path = "/v1/system/health"; headers = [pscustomobject]@{ Authorization = "Bearer ***" }; body = "" }
                    output = [pscustomobject]@{ status = "200"; body = '{"status":"ok"}' } },
                [pscustomobject]@{ name = "not"; method = "POST"; path = "/v1/notes"; expected = "201"; actual = "500"; ok = $false; ms = 40
                    input = [pscustomobject]@{ method = "POST"; path = "/v1/notes"; body = '{"text":"süt al","password":"p-sizdi"}' }
                    output = [pscustomobject]@{ status = "500"; body = '{"detail":"boom"}' } },
                [pscustomobject]@{ name = "elle"; method = "GET"; path = "/v1/x"; expected = "200"; actual = "404"; ok = $false }
            ) },
        [pscustomobject]@{ card = "tj-r-2"; scenario = "y.json"; steps = @(); breaking = [pscustomobject]@{ what = "GET /v1/watches"; tried = @([pscustomobject]@{ load = 8; ok = 8; errors = 0; p95_ms = 80 }, [pscustomobject]@{ load = 16; ok = 10; errors = 6; p95_ms = 3000 }); first_failure = [pscustomobject]@{ load = 16; ok = 10; errors = 6; p95_ms = 3000 } } }
    )
    $forwarded = @([pscustomobject]@{ id = "test-fail-saglik-abc"; title = "Test ekibi: saglik - not" })
    $report = Format-TestTeamRoundReport -Round "r7" -StagingSha ("e" * 40) -Jobs $jobs -Cards $cards -Results $results -Forwarded $forwarded -At "2026-10-07T00:00:00Z"
    $md = $report.Markdown
    Assert-Equal -Expected 3 -Actual ([regex]::Matches($md, '(?m)^Girdi:$').Count) -Because "one Girdi per step: $md"
    Assert-Equal -Expected 3 -Actual ([regex]::Matches($md, '(?m)^Beklenen: ').Count) -Because "one Beklenen per step"
    Assert-Equal -Expected 3 -Actual ([regex]::Matches($md, '(?m)^Çıktı:$').Count) -Because "one Çıktı per step"
    Assert-Equal -Expected 3 -Actual ([regex]::Matches($md, '(?m)^Sonuç: (geçti|kaldı)$').Count) -Because "one Sonuç per step"
    Assert-True -Condition ($md -match '### 1\. sağlık \(12 ms\)') -Because "the step and its ms"
    Assert-True -Condition ($md -match 'POST /v1/notes') -Because "the input's request line"
    Assert-True -Condition ($md -match '"password":"\*\*\*"' -and -not $md.Contains("p-sizdi")) -Because "a secret a hand-written result left in is masked here too"
    Assert-True -Condition ($md -match '\{"detail":"boom"\}') -Because "the output's body"
    Assert-True -Condition ($md -match 'GET /v1/x \(girdi kaydı yok\)') -Because "a step without a record says so"
    Assert-True -Condition ($md -match '- saglik \(tj-r-1, tester-1\): Stage 58 yayında') -Because "the plan's why per job"
    Assert-True -Condition ($md -match 'yuk \(tj-r-2, tester-2\): \(gerekçe yazılmamış\)') -Because "a job with no why says so"
    Assert-True -Condition ($md -match 'tester-2 - yuk \(tj-r-2\) - sonuç: koptu') -Because "the tester's result: koptu"
    Assert-True -Condition ($md -match 'tester-3 - kirik \(tj-r-3\) - sonuç: sonuç yazılmadı') -Because "a running card wrote nothing"
    Assert-True -Condition ($md -match '\| 16 \| 6 \| 3000 \|') -Because "the ladder table: yük, hata, p95"
    Assert-True -Condition ($md -match '- test-fail-saglik-abc: Test ekibi: saglik - not') -Because "the forwarded card"
    Assert-True -Condition ($md -match '1 geçti, 0 kaldı, 1 koptu') -Because "the counts"
    Assert-True -Condition ($md -notmatch 'yarım kaldı') -Because "a whole round is not 'yarım'"
    Assert-Equal -Expected 1 -Actual ([int]$report.Counts.broke) -Because "the counts the Cloud Core keeps"
    $dead = Format-TestTeamRoundReport -Round "r7" -Cards $cards -Results $results -Unfinished "kuyruk okunamadı"
    Assert-True -Condition ($dead.Markdown -match '\*\*yarım kaldı: kuyruk okunamadı\*\*') -Because "the dead round says why"
    Assert-Equal -Expected 3 -Actual ([regex]::Matches($dead.Markdown, '(?m)^Girdi:$').Count) -Because "and keeps what it had"
    $fenced = Format-TestTeamRoundReport -Round "r7" -Cards @($cards[0]) -Results @([pscustomobject]@{ card = "tj-r-1"; steps = @([pscustomobject]@{ name = "x"; ok = $true; output = [pscustomobject]@{ status = "200"; body = "a````b" } }) })
    Assert-Equal -Expected 4 -Actual ([regex]::Matches($fenced.Markdown, '(?m)^````$').Count) -Because "a body cannot close the block it stands in"
    $huge = Limit-TestTeamReportText -Text ("ğ" * 200000)
    Assert-True -Condition ((New-Object System.Text.UTF8Encoding($false)).GetByteCount($huge) -le 262144) -Because "the Cloud Core's copy is at most 256 KB"
    Assert-True -Condition ($huge -match "256 KB") -Because "and says it was cut"
}

# ============================================================================ a round

Write-Host ""
Write-Host "a round"

function New-FakeTester {
    # A stand-in for `claude -p`: reads its card from standard input, writes the result file the
    # card names, prints the result document. The family 'kirik' fails, 'yuk' breaks at 16;
    # 'sikis' raises the memory floor of the settings file named by PAGENTOS_FAKE_SQUEEZE to
    # 64 GB (the machine's memory running out mid-round).
    # Each run writes its OWN file into the folder PAGENTOS_FAKE_TESTER_LOG names: four testers
    # appending to one shared file lost lines (the inspector, 2026-10-05: 2 of 4 runs red).
    param([string]$Dir)
    $file = Join-Path $Dir "fake-tester.ps1"
    Write-Utf8 $file @'
$card = [Console]::In.ReadToEnd()
$log = $env:PAGENTOS_FAKE_TESTER_LOG
$role = ""
for ($i = 0; $i -lt $args.Count; $i++) { if ($args[$i] -eq "--append-system-prompt-file") { $role = [IO.Path]::GetFileNameWithoutExtension($args[$i + 1]) } }
$path = [regex]::Match($card, '(?m)^- result_file: (.+)$').Groups[1].Value.Trim()
$id = [regex]::Match($card, '(?m)^- id: (.+)$').Groups[1].Value.Trim()
$family = [regex]::Match($card, '(?m)^- family: (.+)$').Groups[1].Value.Trim()
$seat = [regex]::Match($card, '(?m)^- tester: (.+)$').Groups[1].Value.Trim()
[void](New-Item -ItemType Directory -Force -Path $log)
[IO.File]::WriteAllText((Join-Path $log "$id.call"), "$role $seat $id $family $([datetime]::UtcNow.ToString('o'))")
if ($family -eq "sikis") { [IO.File]::WriteAllText($env:PAGENTOS_FAKE_SQUEEZE, '{"test_parallel":4,"test_memory_floor_gb":64}') }
Start-Sleep -Milliseconds 600
$state = "passed"; $steps = @(@{ name = "adim"; method = "GET"; path = "/v1/system/health"; expected = "200"; actual = "200"; ok = $true; ms = 7; input = @{ method = "GET"; path = "/v1/system/health"; body = "" }; output = @{ status = "200"; body = '{"status":"ok"}' } })
$breaking = @{ what = "ayni istek iki kez"; tried = @(@{ load = 2; ok = 2; errors = 0; p95_ms = 30 }); first_failure = $null }
if ($family -eq "kirik") { $state = "failed"; $steps = @(@{ name = "nobet listesi"; method = "GET"; path = "/v1/watches"; expected = "200"; actual = "500"; ok = $false }) }
# 'kopuk': the staging credential is rotated under the round (the session file now holds a dead
# token) and every step the tester wrote by hand answered 401 (t-d20261006, 2026-10-06).
if ($family -eq "kopuk") {
    [IO.File]::WriteAllText($env:PAGENTOS_FAKE_SESSION_FILE, '{"api":"http://127.0.0.1:28001","session_token":"dead"}')
    $state = "failed"; $steps = @(@{ name = "nobet kur"; method = "POST"; path = "/v1/watches"; expected = "201"; actual = "401"; ok = $false }, @{ name = "nobet listesi"; method = "GET"; path = "/v1/watches"; expected = "200"; actual = "401"; ok = $false })
}
# 'karma': the session dies too, but a step answered 500 before it; 'karma-ortam' says
# 'environment' itself for such a mixed failure (a runner that trusts any 401 too much).
if ($family -like "karma*") {
    [IO.File]::WriteAllText($env:PAGENTOS_FAKE_SESSION_FILE, '{"api":"http://127.0.0.1:28001","session_token":"dead"}')
    $state = "failed"; $steps = @(@{ name = "nobet kur"; method = "POST"; path = "/v1/watches"; expected = "201"; actual = "500"; ok = $false }, @{ name = "nobet listesi"; method = "GET"; path = "/v1/watches"; expected = "200"; actual = "401"; ok = $false })
    if ($family -eq "karma-ortam") { $state = "environment" }
}
if ($family -eq "yuk") { $state = "broke"; $breaking = @{ what = "GET /v1/watches esz."; tried = @(@{ load = 8; ok = 8; errors = 0; p95_ms = 80 }, @{ load = 16; ok = 10; errors = 6; p95_ms = 3000 }); first_failure = @{ load = 16; ok = 10; errors = 6; p95_ms = 3000 } } }
$doc = @{ card = $id; tester = $seat; family = $family; state = $state; scenario = "scripts/testteam/scenarios/$family.json"; staging_sha = ("d" * 40); steps = $steps; breaking = $breaking; screenshot = "" }
[IO.File]::WriteAllText($path, ($doc | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))
Write-Output '{"type":"result","subtype":"success","is_error":false,"result":"rapor yazildi","total_cost_usd":0}'
'@
    return $file
}

function Get-FakeCalls {
    # The fake testers' calls, one file each, in the order they started.
    param([string]$Dir)
    if (-not (Test-Path -LiteralPath $Dir)) { return @() }
    $calls = @(Get-ChildItem -LiteralPath $Dir -Filter "*.call" -File | ForEach-Object { [System.IO.File]::ReadAllText($_.FullName) })
    return @($calls | Sort-Object { [datetime]::Parse(($_ -split " ")[4]) })
}

Test-Case "a round deals four jobs to four testers at once, gets four results back, forwards the failure as one card and writes the breaking report" {
    $work = New-Work
    try {
        $team = Join-Path $work "team"
        [void](New-Item -ItemType Directory -Force -Path $team)
        Write-Utf8 (Join-Path $team "queue.json") '{"version":1,"tasks":[]}'
        Write-Utf8 (Join-Path $team "cycle-settings.json") '{"max_parallel":4,"test_parallel":4}'
        $plan = Join-Path $work "plan.json"
        Write-Utf8 $plan '{"jobs":[{"family":"saglik","scenario":"scripts/testteam/scenarios/health.json"},{"family":"kirik","scenario":"scripts/testteam/scenarios/kirik.json"},{"family":"yuk","scenario":"scripts/testteam/scenarios/yuk.json","improvise":true},{"family":"iptal","scenario":"scripts/testteam/scenarios/iptal.json","improvise":true}]}'
        $fake = New-FakeTester -Dir $work
        $env:PAGENTOS_FAKE_TESTER_LOG = Join-Path $work "calls"
        $outRoot = Join-Path $work "out"
        $out = & $powershell -NoProfile -File $testRound -NoAuth -Round "r9" -TeamRoot $team -OutRoot $outRoot -PlanPath $plan -ClaudePath $powershell -ClaudePrefixArguments "-NoProfile,-File,$fake" -AssumeFreeGb 30 -AssumeGateRunning 0 -NoBoard 2>&1
        Assert-Equal -Expected 0 -Actual $LASTEXITCODE -Because "the round ends: $out"
        $calls = @(Get-FakeCalls -Dir $env:PAGENTOS_FAKE_TESTER_LOG)
        Assert-Equal -Expected 4 -Actual @($calls).Count -Because "four tester runs"
        Assert-Equal -Expected "tester-1,tester-2,tester-3,tester-4" -Actual ((@($calls) | ForEach-Object { ($_ -split " ")[1] } | Sort-Object) -join ",") -Because "one each"
        Assert-True -Condition (@($calls | Where-Object { ($_ -split " ")[0] -ne "tester" }).Count -eq 0) -Because "every run is on the tester role file"
        $cards = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $outRoot "r9\cards.json") | ConvertFrom-Json
        Assert-Equal -Expected "passed,failed,broke,passed" -Actual ((@($cards.cards) | ForEach-Object { $_.state }) -join ",") -Because "four results came back"
        $queue = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $team "queue.json") | ConvertFrom-Json
        Assert-Equal -Expected 1 -Actual @($queue.tasks).Count -Because "the one failure is one card in the software queue"
        Assert-Equal -Expected "proposed" -Actual (@($queue.tasks)[0].state) -Because "for the Proje Yöneticisi"
        Assert-Equal -Expected (@($queue.tasks)[0].id) -Actual ([string](@($cards.cards)[1].forwarded_task)) -Because "the card remembers the forwarded task"
        Assert-Equal -Expected ("d" * 40) -Actual ([string](@($cards.cards)[1].found_sha)) -Because "and the staging sha the failure was found on (the retest's yardstick)"
        $breaking = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $outRoot "r9\kopma-noktasi.md")
        Assert-True -Condition ($breaking -match "16") -Because "the first failing load"
        # The owner's input/output report (test-round-io-report), left in the round folder.
        $io = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $outRoot "r9\test-raporu.md")
        Assert-Equal -Expected 4 -Actual ([regex]::Matches($io, '(?m)^Girdi:$').Count) -Because "one Girdi for each of the four testers' one step: $io"
        Assert-Equal -Expected 4 -Actual ([regex]::Matches($io, '(?m)^Çıktı:$').Count) -Because "one Çıktı each"
        Assert-True -Condition ($io -match 'tester-2 - kirik \(tj-r9-2\) - sonuç: kaldı' -and $io -match 'tester-3 - yuk \(tj-r9-3\) - sonuç: koptu') -Because "each tester's result"
        Assert-True -Condition ($io -match '- test-fail-kirik-[0-9a-f]{10}: ') -Because "the forwarded card is named"
        Assert-True -Condition ($io -match '2 geçti, 1 kaldı, 1 koptu') -Because "the counts"
        Assert-True -Condition (($out -join " ") -match "Cloud Core adresi yok") -Because "without a Cloud Core it says the report stayed in the folder: $out"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $team "testteam"))) -Because "no run data beside the team's files"
        # A second round over the same queue forwards nothing twice.
        $out = & $powershell -NoProfile -File $testRound -NoAuth -Round "r10" -TeamRoot $team -OutRoot $outRoot -PlanPath $plan -ClaudePath $powershell -ClaudePrefixArguments "-NoProfile,-File,$fake" -AssumeFreeGb 30 -AssumeGateRunning 0 -NoBoard 2>&1
        $queue = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $team "queue.json") | ConvertFrom-Json
        Assert-Equal -Expected 1 -Actual @($queue.tasks).Count -Because "an open card for the same failure is not opened again: $out"
    }
    finally {
        Remove-Item Env:\PAGENTOS_FAKE_TESTER_LOG -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "a round that dies after its testers still writes its input/output report, marked 'yarım kaldı: <why>'" {
    $work = New-Work
    try {
        $team = Join-Path $work "team"
        [void](New-Item -ItemType Directory -Force -Path $team)
        # A queue that cannot be read: the round dies at the forward, after both testers finished.
        Write-Utf8 (Join-Path $team "queue.json") '{"version":1,"tasks":[ bozuk'
        $plan = Join-Path $work "plan.json"
        Write-Utf8 $plan '{"jobs":[{"family":"saglik","scenario":"a.json","why":"her tur"},{"family":"kirik","scenario":"b.json"}]}'
        $fake = New-FakeTester -Dir $work
        $env:PAGENTOS_FAKE_TESTER_LOG = Join-Path $work "calls"
        $outRoot = Join-Path $work "out"
        # The dying child writes its error to stderr: a line of output here, not this test's error.
        $ErrorActionPreference = "Continue"
        $out = & $powershell -NoProfile -File $testRound -Round "olu"-TeamRoot $team -OutRoot $outRoot -PlanPath $plan -ClaudePath $powershell -ClaudePrefixArguments "-NoProfile,-File,$fake" -AssumeFreeGb 30 -AssumeGateRunning 0 -NoBoard 2>&1
        Assert-True -Condition ($LASTEXITCODE -ne 0) -Because "the round died: $out"
        $file = Join-Path $outRoot "olu\test-raporu.md"
        Assert-True -Condition (Test-Path -LiteralPath $file) -Because "the report is written anyway: $out"
        $io = Get-Content -Raw -Encoding UTF8 -LiteralPath $file
        Assert-True -Condition ($io -match '\*\*yarım kaldı: .+\*\*') -Because "marked unfinished, with why: $io"
        Assert-Equal -Expected 2 -Actual ([regex]::Matches($io, '(?m)^Girdi:$').Count) -Because "with what the two testers did"
        Assert-True -Condition ($io -match '- saglik \(tj-olu-1, tester-1\): her tur') -Because "and the plan"
    }
    finally {
        Remove-Item Env:\PAGENTOS_FAKE_TESTER_LOG -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "with the Cloud Core's queue the failure goes through the feed's create-only write, and a known id is not written again" {
    $work = New-Work
    $port = Get-Random -Minimum 41000 -Maximum 49000
    $puts = Join-Path $work "puts.log"
    # A stand-in Cloud Core: GET /v1/team/queue answers one task (the id the failure would get
    # a second time), PUT /v1/team/queue/tasks/<id> is recorded and answered 200.
    $core = @"
`$l = New-Object System.Net.HttpListener
`$l.Prefixes.Add('http://127.0.0.1:$port/')
`$l.Start()
while (`$true) {
    `$c = `$l.GetContext()
    `$p = `$c.Request.Url.AbsolutePath
    `$body = '{}'
    if (`$p -eq '/stop') { `$c.Response.Close(); break }
    if (`$c.Request.HttpMethod -eq 'GET' -and `$p -eq '/v1/team/queue') { `$body = [IO.File]::ReadAllText('$work\queue-answer.json') }
    if (`$c.Request.HttpMethod -eq 'PUT') {
        `$in = (New-Object IO.StreamReader(`$c.Request.InputStream, [Text.Encoding]::UTF8)).ReadToEnd()
        [IO.File]::AppendAllText('$puts', `$p + ' ' + `$in + "``n")
        `$body = '{"ok":true}'
    }
    if (`$c.Request.HttpMethod -eq 'POST') {
        `$in = (New-Object IO.StreamReader(`$c.Request.InputStream, [Text.Encoding]::UTF8)).ReadToEnd()
        [IO.File]::WriteAllText('$work\post-' + [guid]::NewGuid().ToString('N') + '.json', `$p + ' ' + `$c.Request.Headers['Authorization'] + ' ' + `$in, [Text.Encoding]::UTF8)
        `$body = '{"report":{"round":"x"}}'
    }
    `$b = [Text.Encoding]::UTF8.GetBytes(`$body)
    `$c.Response.ContentType = 'application/json'
    `$c.Response.OutputStream.Write(`$b, 0, `$b.Length)
    `$c.Response.Close()
}
"@
    Write-Utf8 (Join-Path $work "queue-answer.json") '{"version":1,"tasks":[]}'
    Write-Utf8 (Join-Path $work "core.ps1") $core
    $listener = Start-Process -FilePath $powershell -ArgumentList @("-NoProfile", "-File", (Join-Path $work "core.ps1")) -PassThru -WindowStyle Hidden
    try {
        for ($i = 0; $i -lt 50; $i++) { try { [void](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/v1/team/queue" -TimeoutSec 2); break } catch { Start-Sleep -Milliseconds 200 } }
        $team = Join-Path $work "team"
        [void](New-Item -ItemType Directory -Force -Path $team)
        $token = Join-Path $work "token.txt"; Write-Utf8 $token "test-token"
        $plan = Join-Path $work "plan.json"
        Write-Utf8 $plan '{"jobs":[{"family":"kirik","scenario":"scripts/testteam/scenarios/kirik.json"},{"family":"saglik","scenario":"s.json"}]}'
        $fake = New-FakeTester -Dir $work
        $env:PAGENTOS_FAKE_TESTER_LOG = Join-Path $work "calls"
        $outRoot = Join-Path $work "out"
        $out = & $powershell -NoProfile -File $testRound -NoAuth -Round "api1" -TeamRoot $team -OutRoot $outRoot -PlanPath $plan -ClaudePath $powershell -ClaudePrefixArguments "-NoProfile,-File,$fake" -AssumeFreeGb 30 -AssumeGateRunning 0 -NoBoard -QueueUrl "http://127.0.0.1:$port" -QueueToken $token 2>&1
        Assert-Equal -Expected 0 -Actual $LASTEXITCODE -Because "the round ends: $out"
        $written = @(Get-Content -LiteralPath $puts -Encoding UTF8 | Where-Object { $_ })
        Assert-Equal -Expected 1 -Actual @($written).Count -Because "one failure, one create: $out"
        Assert-True -Condition ($written[0] -match '^/v1/team/queue/tasks/test-fail-kirik-[0-9a-f]{10} ') -Because "the task's own path: $($written[0])"
        Assert-True -Condition ($written[0] -match '"expected_updated_at":null') -Because "create-only: $($written[0])"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $team "queue.json"))) -Because "no local queue file is written in API mode"
        # The round's input/output report goes to the Cloud Core's owner-only route.
        $posts = @(Get-ChildItem -LiteralPath $work -Filter "post-*.json" | ForEach-Object { [System.IO.File]::ReadAllText($_.FullName, [System.Text.Encoding]::UTF8) })
        Assert-Equal -Expected 1 -Actual @($posts).Count -Because "one report a round: $out"
        $post = $posts[0]
        Assert-True -Condition ($post.StartsWith("/v1/team/test-reports Bearer test-token ")) -Because "the route, with the team token: $($post.Substring(0, 60))"
        $sent = $post.Substring("/v1/team/test-reports Bearer test-token ".Length) | ConvertFrom-Json
        Assert-Equal -Expected "api1" -Actual ([string]$sent.round) -Because "the round"
        Assert-Equal -Expected 1 -Actual ([int]$sent.counts.failed) -Because "the counts: $($sent.counts | ConvertTo-Json -Compress)"
        Assert-Equal -Expected ("d" * 40) -Actual ([string]$sent.staging_sha) -Because "the staging sha"
        Assert-True -Condition ([string]$sent.text -match '(?m)^Girdi:$') -Because "the report's text"
        Assert-Equal -Expected "" -Actual ([string]$sent.unfinished) -Because "a whole round"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $outRoot "api1\test-raporu.md")) -Because "and the same report in the round folder"
        # The same failure next round: the store has the id, nothing is written.
        $id = [regex]::Match($written[0], 'tasks/(\S+) ').Groups[1].Value
        Write-Utf8 (Join-Path $work "queue-answer.json") ('{"version":1,"tasks":[{"id":"' + $id + '","state":"proposed"}]}')
        $out = & $powershell -NoProfile -File $testRound -NoAuth -Round "api2" -TeamRoot $team -OutRoot $outRoot -PlanPath $plan -ClaudePath $powershell -ClaudePrefixArguments "-NoProfile,-File,$fake" -AssumeFreeGb 30 -AssumeGateRunning 0 -NoBoard -QueueUrl "http://127.0.0.1:$port" -QueueToken $token 2>&1
        Assert-Equal -Expected 1 -Actual @(Get-Content -LiteralPath $puts -Encoding UTF8 | Where-Object { $_ }).Count -Because "a known id is not written again: $out"
    }
    finally {
        try { [void](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/stop" -TimeoutSec 2) } catch { }
        if (-not $listener.HasExited) { $listener.Kill() }
        Remove-Item Env:\PAGENTOS_FAKE_TESTER_LOG -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "under the memory floor a round starts no tester and says so; while a gate runs one tester at a time" {
    $work = New-Work
    try {
        $team = Join-Path $work "team"
        [void](New-Item -ItemType Directory -Force -Path $team)
        Write-Utf8 (Join-Path $team "queue.json") '{"version":1,"tasks":[]}'
        $plan = Join-Path $work "plan.json"
        Write-Utf8 $plan '{"jobs":[{"family":"a","scenario":"a.json"},{"family":"b","scenario":"b.json"}]}'
        $fake = New-FakeTester -Dir $work
        $env:PAGENTOS_FAKE_TESTER_LOG = Join-Path $work "calls"
        $outRoot = Join-Path $work "out"
        $out = & $powershell -NoProfile -File $testRound -NoAuth -Round "low" -TeamRoot $team -OutRoot $outRoot -PlanPath $plan -ClaudePath $powershell -ClaudePrefixArguments "-NoProfile,-File,$fake" -AssumeFreeGb 3 -AssumeGateRunning 0 -NoBoard 2>&1
        Assert-True -Condition (-not (Test-Path -LiteralPath $env:PAGENTOS_FAKE_TESTER_LOG)) -Because "no tester started: $out"
        Assert-True -Condition (($out -join " ") -match "bellek") -Because "it says why: $out"
        $out = & $powershell -NoProfile -File $testRound -NoAuth -Round "gate" -TeamRoot $team -OutRoot $outRoot -PlanPath $plan -ClaudePath $powershell -ClaudePrefixArguments "-NoProfile,-File,$fake" -AssumeFreeGb 30 -AssumeGateRunning 1 -NoBoard 2>&1
        $calls = @(Get-FakeCalls -Dir $env:PAGENTOS_FAKE_TESTER_LOG)
        Assert-Equal -Expected 2 -Actual @($calls).Count -Because "both jobs ran: $out"
        $t1 = [datetime]::Parse((@($calls)[0] -split " ")[4]); $t2 = [datetime]::Parse((@($calls)[1] -split " ")[4])
        Assert-True -Condition ([math]::Abs(($t2 - $t1).TotalMilliseconds) -ge 500) -Because "one after the other, not together ($t1 / $t2)"
    }
    finally {
        Remove-Item Env:\PAGENTOS_FAKE_TESTER_LOG -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "measured, not assumed: the machine's own free memory and the test-slot store's gate entry set the cap" {
    # The inspector, 2026-10-05: every round of the tests passed -Assume*, the measuring path had no test.
    $work = New-Work
    try {
        $team = Join-Path $work "team"
        [void](New-Item -ItemType Directory -Force -Path $team)
        Write-Utf8 (Join-Path $team "queue.json") '{"version":1,"tasks":[]}'
        # A 1 GB floor: the real free memory of this machine is above it, the gate is what lowers the cap.
        Write-Utf8 (Join-Path $team "cycle-settings.json") '{"test_parallel":4,"test_memory_floor_gb":1}'
        $slots = Join-Path $work "slots"
        [void](New-Item -ItemType Directory -Force -Path (Join-Path $slots "entries"))
        $now = [datetime]::UtcNow.ToString("o")
        $gateEntry = [ordered]@{ ticket = "t-gate"; kinds = @("database", "heavy"); state = "running"; role = "gate"; task = "gate"; what = "quality gate"; seq = 1
            first_asked = $now; last_asked = $now; granted_at = $now; started_at = $now; holder_pid = $PID; holder_start = "" }
        Write-Utf8 (Join-Path $slots "entries\t-gate.json") ($gateEntry | ConvertTo-Json -Compress)
        $plan = Join-Path $work "plan.json"
        Write-Utf8 $plan '{"jobs":[{"family":"a","scenario":"a.json"},{"family":"b","scenario":"b.json"}]}'
        $fake = New-FakeTester -Dir $work
        $env:PAGENTOS_FAKE_TESTER_LOG = Join-Path $work "calls"
        $out = & $powershell -NoProfile -File $testRound -NoAuth -Round "measured" -TeamRoot $team -OutRoot (Join-Path $work "out") -PlanPath $plan -ClaudePath $powershell -ClaudePrefixArguments "-NoProfile,-File,$fake" -SlotStore $slots -NoBoard 2>&1
        Assert-Equal -Expected 0 -Actual $LASTEXITCODE -Because "the round ends: $out"
        Assert-True -Condition (($out -join " ") -match "kap") -Because "the measured gate is said: $out"
        $calls = @(Get-FakeCalls -Dir $env:PAGENTOS_FAKE_TESTER_LOG)
        Assert-Equal -Expected 2 -Actual @($calls).Count -Because "both jobs ran: $out"
        $t1 = [datetime]::Parse((@($calls)[0] -split " ")[4]); $t2 = [datetime]::Parse((@($calls)[1] -split " ")[4])
        Assert-True -Condition ([math]::Abs(($t2 - $t1).TotalMilliseconds) -ge 500) -Because "the measured gate made it one at a time ($t1 / $t2)"
    }
    finally {
        Remove-Item Env:\PAGENTOS_FAKE_TESTER_LOG -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "the cap is measured again before every tester start: memory that runs out mid-round holds the next job, which stays planned" {
    # The inspector, 2026-10-05: the cap was measured once, at the start; four testers went on
    # whatever happened to the machine during the round.
    $work = New-Work
    try {
        $team = Join-Path $work "team"
        [void](New-Item -ItemType Directory -Force -Path $team)
        Write-Utf8 (Join-Path $team "queue.json") '{"version":1,"tasks":[]}'
        $settings = Join-Path $team "cycle-settings.json"
        Write-Utf8 $settings '{"test_parallel":4,"test_memory_floor_gb":8}'
        $plan = Join-Path $work "plan.json"
        # Five jobs: tester-1 has the first and the fifth; its first job squeezes the memory.
        Write-Utf8 $plan '{"jobs":[{"family":"sikis","scenario":"a.json"},{"family":"b","scenario":"b.json"},{"family":"c","scenario":"c.json"},{"family":"d","scenario":"d.json"},{"family":"e","scenario":"e.json"}]}'
        $fake = New-FakeTester -Dir $work
        $env:PAGENTOS_FAKE_TESTER_LOG = Join-Path $work "calls"
        $env:PAGENTOS_FAKE_SQUEEZE = $settings
        $outRoot = Join-Path $work "out"
        $out = & $powershell -NoProfile -File $testRound -NoAuth -Round "squeeze" -TeamRoot $team -OutRoot $outRoot -PlanPath $plan -ClaudePath $powershell -ClaudePrefixArguments "-NoProfile,-File,$fake" -AssumeFreeGb 30 -AssumeGateRunning 0 -NoBoard 2>&1
        Assert-Equal -Expected 0 -Actual $LASTEXITCODE -Because "the round ends: $out"
        $calls = @(Get-FakeCalls -Dir $env:PAGENTOS_FAKE_TESTER_LOG)
        Assert-Equal -Expected 4 -Actual @($calls).Count -Because "the fifth job did not start: $out"
        $cards = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $outRoot "squeeze\cards.json") | ConvertFrom-Json
        Assert-Equal -Expected "passed,passed,passed,passed,planned" -Actual ((@($cards.cards) | ForEach-Object { $_.state }) -join ",") -Because "it stays planned"
        Assert-True -Condition (($out -join " ") -match "bellek") -Because "and the round says why: $out"
    }
    finally {
        Remove-Item Env:\PAGENTOS_FAKE_TESTER_LOG -ErrorAction SilentlyContinue
        Remove-Item Env:\PAGENTOS_FAKE_SQUEEZE -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "a round's run data goes under run_temp_root, never into the checkout" {
    # The inspector, 2026-10-05: results, logs and staging screenshots were written under
    # team/testteam/<round>/, a folder git does not ignore.
    $work = New-Work
    $inside = ""
    try {
        $team = Join-Path $work "team"
        [void](New-Item -ItemType Directory -Force -Path $team)
        Write-Utf8 (Join-Path $team "queue.json") '{"version":1,"tasks":[]}'
        $runTemp = Join-Path $work "run-temp"
        Write-Utf8 (Join-Path $team "cycle-settings.json") ('{"test_parallel":4,"run_temp_root":' + (ConvertTo-Json -InputObject $runTemp) + '}')
        $plan = Join-Path $work "plan.json"
        Write-Utf8 $plan '{"jobs":[{"family":"a","scenario":"a.json"}]}'
        $out = & $powershell -NoProfile -File $testRound -NoAuth -Round "where" -TeamRoot $team -PlanPath $plan -AssumeFreeGb 0 -AssumeGateRunning 0 -NoBoard 2>&1
        Assert-Equal -Expected 0 -Actual $LASTEXITCODE -Because "the round ends (no memory, no tester): $out"
        Assert-True -Condition (Test-Path -LiteralPath (Join-Path $runTemp "testteam\where\cards.json")) -Because "the cards are under run_temp_root: $out"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $team "testteam"))) -Because "nothing beside the team's files"
        $inside = Join-Path $repoRoot "team\testteam-refused-$([guid]::NewGuid().ToString('N').Substring(0, 6))"
        $out = & $powershell -NoProfile -File $testRound -NoAuth -Round "where" -TeamRoot $team -OutRoot $inside -PlanPath $plan -AssumeFreeGb 0 -AssumeGateRunning 0 -NoBoard 2>&1
        Assert-Equal -Expected 2 -Actual $LASTEXITCODE -Because "an out root in the checkout is refused: $out"
        Assert-True -Condition (-not (Test-Path -LiteralPath $inside)) -Because "and nothing was created there"
    }
    finally {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
        # A red run (the guard broken) must not leave its folder in the checkout.
        if ($inside) { Remove-Item -LiteralPath $inside -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

Test-Case "a retest after the fix is released AND staging was redeployed closes the card when the scenario passes and reopens it when it fails" {
    $work = New-Work
    $port = Get-Random -Minimum 41000 -Maximum 49000
    # Staging now serves 'c...'; the failures were found on 'e...' unless a card says otherwise.
    $fake = Start-FakeStaging -Port $port -Sha ("c" * 40)
    try {
        $team = Join-Path $work "team"
        $outRoot = Join-Path $work "out"
        $roundDir = Join-Path $outRoot "r1"
        [void](New-Item -ItemType Directory -Force -Path $roundDir)
        $good = Join-Path $work "good.json"; Write-Utf8 $good '{"id":"g","family":"g","steps":[{"name":"saglik","method":"GET","path":"/v1/system/health","expect_status":200}]}'
        $bad = Join-Path $work "bad.json"; Write-Utf8 $bad '{"id":"b","family":"b","steps":[{"name":"kirik","method":"GET","path":"/broken","expect_status":200}]}'
        $task = { param($id, $state) [pscustomobject]@{ id = $id; title = "t"; roadmap_row = "r"; state = $state; area = @(); branch = ""; worktree = ""; assignee = ""; reports = @(); budget = [pscustomobject]@{ max_usd = 0 }; created_at = "2026-10-05T00:00:00Z"; updated_at = "2026-10-05T00:00:00Z" } }
        $queue = [pscustomobject]@{ version = 1; tasks = @((& $task "test-fail-g" "released"), (& $task "test-fail-b" "released"), (& $task "test-fail-w" "in_progress"), (& $task "test-fail-s" "released"), (& $task "test-fail-m" "merged")) }
        [void](New-Item -ItemType Directory -Force -Path $team)
        Write-Utf8 (Join-Path $team "queue.json") ($queue | ConvertTo-Json -Depth 6)
        $card = { param($n, $scenario, $task, $sha) [pscustomobject]@{ id = "tj-r1-$n"; tester = "tester-1"; family = "f$n"; scenario = $scenario; improvise = $false; state = "failed"; forwarded_task = $task; found_sha = $sha; reopened = 0 } }
        $cards = [pscustomobject]@{ round = "r1"; cards = @(
                (& $card 1 $good "test-fail-g" ("e" * 40)),
                (& $card 2 $bad "test-fail-b" ("e" * 40)),
                (& $card 3 $bad "test-fail-w" ("e" * 40)),
                # Released, but staging still serves the sha of the failure: the fix is not on it.
                (& $card 4 $good "test-fail-s" ("c" * 40)),
                # Merged into an integration branch only: not on staging.
                (& $card 5 $good "test-fail-m" ("e" * 40))) }
        Write-Utf8 (Join-Path $roundDir "cards.json") ($cards | ConvertTo-Json -Depth 6)
        $out = & $powershell -NoProfile -File $testRound -Retest -Round "r1" -TeamRoot $team -OutRoot $outRoot -BaseUrl "http://127.0.0.1:$port" -AllowTestPort $port -NoAuth -NoBoard 2>&1
        $after = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $roundDir "cards.json") | ConvertFrom-Json
        Assert-Equal -Expected "passed,failed,failed,failed,failed" -Actual ((@($after.cards) | ForEach-Object { $_.state }) -join ",") -Because "fixed closes, still broken reopens, the rest wait: $out"
        Assert-Equal -Expected "0,1,0,0,0" -Actual ((@($after.cards) | ForEach-Object { [int]$_.reopened }) -join ",") -Because "only the re-run that failed is a reopen"
        Assert-Equal -Expected ("c" * 40) -Actual ([string](Get-TeamProperty -InputObject @($after.cards)[0] -Name "retested_sha" -Default "")) -Because "the card says which staging closed it"
        Assert-True -Condition (($out -join " ") -match "hâlâ|hala|h.l.") -Because "the unredeployed staging is said: $out"
    }
    finally {
        try { [void](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/stop" -TimeoutSec 2) } catch { }
        if (-not $fake.HasExited) { $fake.Kill() }
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ============================================================================ the staging session

Write-Host ""
Write-Host "the staging session"

function New-FakeSeed {
    # A stand-in for scripts\staging\seed.ps1: writes the session file PAGENTOS_FAKE_SESSION_FILE
    # names with the token PAGENTOS_FAKE_SEED_TOKEN names, and one line per run (with the time)
    # into PAGENTOS_FAKE_SEED_LOG.
    param([string]$Dir)
    $file = Join-Path $Dir "fake-seed.ps1"
    Write-Utf8 $file @'
param([string]$ApiBase = "")
[IO.File]::WriteAllText($env:PAGENTOS_FAKE_SESSION_FILE, ('{"api":"http://127.0.0.1:28001","session_token":"' + $env:PAGENTOS_FAKE_SEED_TOKEN + '"}'))
[IO.File]::AppendAllText($env:PAGENTOS_FAKE_SEED_LOG, "seed $ApiBase $([datetime]::UtcNow.ToString('o'))`n")
Start-Sleep -Milliseconds 300
exit 0
'@
    return $file
}

Test-Case "a scenario step that enrols, rotates or revokes an identity is refused and never sent; the rest of the scenario runs" {
    # t-d20261006 (2026-10-06): one tester's improvisation that rotates the staging owner
    # credential turns every other tester's steps into 401 for the rest of the round.
    $work = New-Work
    $port = Get-Random -Minimum 41000 -Maximum 49000
    $log = Join-Path $work "requests.log"
    $fake = Start-FakeStaging -Port $port -Log $log
    try {
        $scenario = Join-Path $work "i.json"
        Write-Utf8 $scenario ('{"id":"i","family":"kimlik","steps":[' +
            '{"name":"liste","method":"GET","path":"/v1/watches","expect_status":200},' +
            '{"name":"oturumu iptal et","method":"POST","path":"/v1/identity/sessions/abc/revoke","expect_status":200},' +
            '{"name":"kimlik kur","method":"POST","path":"/v1/identity/bootstrap","expect_status":201},' +
            '{"name":"yeni oturum","method":"POST","path":"/v1/identity/sessions","expect_status":201},' +
            '{"name":"bu oturumu kapat","method":"DELETE","path":"/v1/identity/sessions/current","expect_status":200},' +
            '{"name":"panik","method":"POST","url":"http://127.0.0.1:' + $port + '/V1/Identity/panic","expect_status":200},' +
            '{"name":"cihaz kaydet","method":"POST","path":"/v1/devices/enroll","expect_status":201},' +
            '{"name":"cihaz iptal","method":"POST","path":"/v1/devices/d1/revoke","expect_status":200},' +
            '{"name":"kimlik dondur","method":"POST","path":"/v1/owner/credential/rotate","expect_status":200}],' +
            '"cleanup":[{"name":"temizlikte iptal","method":"POST","path":"/v1/identity/sessions/xyz/revoke"},{"name":"saglik","method":"GET","path":"/v1/system/health"}]}')
        $out = & $powershell -NoProfile -File $runScenario -Scenario $scenario -BaseUrl "http://127.0.0.1:$port" -AllowTestPort $port -OutDir $work -NoAuth -Card "tj-i" 2>&1
        Assert-Equal -Expected 0 -Actual $LASTEXITCODE -Because "the one sent step passed; a refused step is not a failure of staging: $out"
        $sent = @(Get-Content -LiteralPath $log -Encoding UTF8 | Where-Object { $_ })
        Assert-Equal -Expected 0 -Actual @($sent | Where-Object { $_ -match '(?i)/v1/identity|/v1/devices|/rotate' }).Count -Because "no identity request reached staging: $($sent -join '; ')"
        Assert-True -Condition (@($sent) -contains "GET /v1/watches") -Because "the harmless step was sent: $($sent -join '; ')"
        Assert-True -Condition (@($sent) -contains "GET /v1/system/health") -Because "the harmless cleanup step was sent: $($sent -join '; ')"
        $result = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $work "tj-i.result.json") | ConvertFrom-Json
        Assert-Equal -Expected "passed" -Actual $result.state -Because "nothing sent failed"
        Assert-Equal -Expected 9 -Actual @($result.refused).Count -Because "eight steps and one cleanup step refused: $($result.refused | ConvertTo-Json -Compress)"
        Assert-Equal -Expected "refused" -Actual ([string]@($result.refused)[0].state) -Because "each is marked 'refused'"
        Assert-Equal -Expected 1 -Actual @($result.steps).Count -Because "steps are what was sent"
        Assert-True -Condition (($out -join " ") -match "REDDED") -Because "a clear refusal is printed: $out"
        # A ladder of revocations is refused whole, before anything is sent.
        Write-Utf8 $scenario ('{"id":"i2","family":"kimlik","steps":[],"breaking":{"method":"POST","path":"/v1/identity/sessions/abc/revoke","start":2,"max":4}}')
        $out = & $powershell -NoProfile -File $runScenario -Scenario $scenario -BaseUrl "http://127.0.0.1:$port" -AllowTestPort $port -OutDir $work -NoAuth -Card "tj-i2" 2>&1
        Assert-Equal -Expected 2 -Actual $LASTEXITCODE -Because "a ladder aimed at revocation: $out"
        Assert-Equal -Expected 0 -Actual @(Get-Content -LiteralPath $log -Encoding UTF8 | Where-Object { $_ -match '(?i)/v1/identity' }).Count -Because "nothing reached staging"
        # Reading the session is not changing it.
        Write-Utf8 $scenario ('{"id":"i3","family":"kimlik","steps":[{"name":"oturum oku","method":"GET","path":"/v1/identity/sessions/current","expect_status":401}]}')
        $out = & $powershell -NoProfile -File $runScenario -Scenario $scenario -BaseUrl "http://127.0.0.1:$port" -AllowTestPort $port -OutDir $work -NoAuth -Card "tj-i3" 2>&1
        Assert-Equal -Expected 0 -Actual $LASTEXITCODE -Because "a GET of the session is allowed: $out"
    }
    finally {
        try { [void](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/stop" -TimeoutSec 2) } catch { }
        if (-not $fake.HasExited) { $fake.Kill() }
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "a scenario whose session staging no longer accepts is 'environment' (exit 4), not 'failed'; a live session's 401-free failure stays 'failed'" {
    $work = New-Work
    $port = Get-Random -Minimum 41000 -Maximum 49000
    $fake = Start-FakeStaging -Port $port
    try {
        $session = Join-Path $work "owner.json"
        Write-Utf8 $session '{"api":"http://127.0.0.1:28001","session_token":"dead"}'
        $scenario = Join-Path $work "w.json"
        Write-Utf8 $scenario '{"id":"w","family":"nobet","steps":[{"name":"nobet kur","method":"POST","path":"/v1/watches","body":{"url":"https://example.com/"},"expect_status":200},{"name":"nobet listesi","method":"GET","path":"/v1/watches","expect_status":200}]}'
        $out = & $powershell -NoProfile -File $runScenario -Scenario $scenario -BaseUrl "http://127.0.0.1:$port" -AllowTestPort $port -OutDir $work -SessionFile $session -Card "tj-w" 2>&1
        Assert-Equal -Expected 4 -Actual $LASTEXITCODE -Because "every step 401 on a dead session is the environment: $out"
        $result = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $work "tj-w.result.json") | ConvertFrom-Json
        Assert-Equal -Expected "environment" -Actual $result.state -Because "not 'failed'"
        Assert-True -Condition ([string]$result.environment -match "oturum") -Because "it says why: $($result.environment)"
        Assert-Equal -Expected 0 -Actual @(Get-TestTeamFailures -Result $result).Count -Because "an environment result forwards nothing"
        Write-Utf8 $session '{"api":"http://127.0.0.1:28001","session_token":"good"}'
        Write-Utf8 $scenario '{"id":"b","family":"kirik","steps":[{"name":"kirik","method":"GET","path":"/broken","expect_status":200}]}'
        $out = & $powershell -NoProfile -File $runScenario -Scenario $scenario -BaseUrl "http://127.0.0.1:$port" -AllowTestPort $port -OutDir $work -SessionFile $session -Card "tj-b" 2>&1
        Assert-Equal -Expected 1 -Actual $LASTEXITCODE -Because "a live session and a 500 is a failure of staging: $out"
        # A 401 while the session itself is still accepted is staging's bug (an auth regression).
        Write-Utf8 $scenario '{"id":"u","family":"yetki","steps":[{"name":"yetkisiz","method":"GET","path":"/unauth","expect_status":200}]}'
        $out = & $powershell -NoProfile -File $runScenario -Scenario $scenario -BaseUrl "http://127.0.0.1:$port" -AllowTestPort $port -OutDir $work -SessionFile $session -Card "tj-u" 2>&1
        Assert-Equal -Expected 1 -Actual $LASTEXITCODE -Because "a 401 on a live session stays 'failed': $out"
        $result = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $work "tj-u.result.json") | ConvertFrom-Json
        Assert-Equal -Expected "failed" -Actual $result.state -Because "the session probe answered 200"
        Assert-Equal -Expected 1 -Actual @(Get-TestTeamFailures -Result $result).Count -Because "it is forwarded"
        # A dead session, but one step failed with a 500 before it: that 500 is staging's bug.
        Write-Utf8 $session '{"api":"http://127.0.0.1:28001","session_token":"dead"}'
        Write-Utf8 $scenario '{"id":"m","family":"karma","steps":[{"name":"kirik","method":"GET","path":"/broken","expect_status":200},{"name":"nobet listesi","method":"GET","path":"/v1/watches","expect_status":200}]}'
        $out = & $powershell -NoProfile -File $runScenario -Scenario $scenario -BaseUrl "http://127.0.0.1:$port" -AllowTestPort $port -OutDir $work -SessionFile $session -Card "tj-m" 2>&1
        Assert-Equal -Expected 1 -Actual $LASTEXITCODE -Because "a 500 among the 401s is not the environment: $out"
        $result = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $work "tj-m.result.json") | ConvertFrom-Json
        Assert-Equal -Expected "failed" -Actual $result.state -Because "the 500 is forwarded"
        Assert-True -Condition (@(Get-TestTeamFailures -Result $result).Count -ge 1) -Because "the 500 step is a failure to forward"
    }
    finally {
        try { [void](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/stop" -TimeoutSec 2) } catch { }
        if (-not $fake.HasExited) { $fake.Kill() }
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "a round seeds the staging session once before the first tester; a seeded session staging answers 401 starts no tester and opens no card" {
    $work = New-Work
    $port = Get-Random -Minimum 41000 -Maximum 49000
    $fakeStaging = Start-FakeStaging -Port $port
    try {
        $team = Join-Path $work "team"
        [void](New-Item -ItemType Directory -Force -Path $team)
        Write-Utf8 (Join-Path $team "queue.json") '{"version":1,"tasks":[]}'
        Write-Utf8 (Join-Path $team "cycle-settings.json") '{"max_parallel":4,"test_parallel":4}'
        $plan = Join-Path $work "plan.json"
        Write-Utf8 $plan '{"jobs":[{"family":"kirik","scenario":"scripts/testteam/scenarios/kirik.json"},{"family":"saglik","scenario":"s.json"}]}'
        $fake = New-FakeTester -Dir $work
        $seed = New-FakeSeed -Dir $work
        $env:PAGENTOS_FAKE_TESTER_LOG = Join-Path $work "calls"
        $env:PAGENTOS_FAKE_SESSION_FILE = Join-Path $work "owner.json"
        $env:PAGENTOS_FAKE_SEED_LOG = Join-Path $work "seed.log"
        $env:PAGENTOS_FAKE_SEED_TOKEN = "dead"
        $posts = Join-Path $work "posts.log"
        $fakeBoard = Join-Path $work "board.ps1"
        Write-Utf8 $fakeBoard ("[IO.File]::AppendAllText('$posts', ((@(`$args) -join '|') + [Environment]::NewLine), (New-Object Text.UTF8Encoding(`$false)))")
        $common = @("-TeamRoot", $team, "-PlanPath", $plan, "-ClaudePath", $powershell, "-ClaudePrefixArguments", "-NoProfile,-File,$fake", "-AssumeFreeGb", "30", "-AssumeGateRunning", "0",
            "-BoardScript", $fakeBoard, "-BaseUrl", "http://127.0.0.1:$port", "-AllowTestPort", "$port", "-SeedScript", $seed, "-SessionFile", $env:PAGENTOS_FAKE_SESSION_FILE)
        $out = & $powershell -NoProfile -File $testRound -Round "dead" -OutRoot (Join-Path $work "out") @common 2>&1
        Assert-Equal -Expected 1 -Actual $LASTEXITCODE -Because "the round refuses to start: $out"
        Assert-Equal -Expected 1 -Actual @(Get-Content -LiteralPath $env:PAGENTOS_FAKE_SEED_LOG | Where-Object { $_ }).Count -Because "it seeded once"
        Assert-Equal -Expected 0 -Actual @(Get-FakeCalls -Dir $env:PAGENTOS_FAKE_TESTER_LOG).Count -Because "no tester started: $out"
        $queue = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $team "queue.json") | ConvertFrom-Json
        Assert-Equal -Expected 0 -Actual @($queue.tasks).Count -Because "no card was opened"
        $notes = [System.IO.File]::ReadAllText($posts, [System.Text.Encoding]::UTF8)
        Assert-True -Condition ($notes -match "oturum") -Because "the board is told why: $notes"
        Assert-True -Condition (($out -join " ") -match "401") -Because "the console says what staging answered: $out"
        # The same round with a session staging accepts: seeded once, before the first tester.
        Remove-Item -LiteralPath $env:PAGENTOS_FAKE_SEED_LOG -Force
        $env:PAGENTOS_FAKE_SEED_TOKEN = "good"
        $out = & $powershell -NoProfile -File $testRound -Round "live" -OutRoot (Join-Path $work "out") @common 2>&1
        Assert-Equal -Expected 0 -Actual $LASTEXITCODE -Because "the round runs: $out"
        $seeds = @(Get-Content -LiteralPath $env:PAGENTOS_FAKE_SEED_LOG | Where-Object { $_ })
        Assert-Equal -Expected 1 -Actual @($seeds).Count -Because "one seed for the round, not one per tester"
        Assert-True -Condition ($seeds[0] -match [regex]::Escape("http://127.0.0.1:$port")) -Because "seeded against the round's staging: $($seeds[0])"
        $calls = @(Get-FakeCalls -Dir $env:PAGENTOS_FAKE_TESTER_LOG)
        Assert-Equal -Expected 2 -Actual @($calls).Count -Because "both testers ran: $out"
        Assert-True -Condition ([datetime]::Parse(($seeds[0] -split " ")[2]) -lt [datetime]::Parse((@($calls)[0] -split " ")[4])) -Because "the seed ran before the first tester"
    }
    finally {
        try { [void](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/stop" -TimeoutSec 2) } catch { }
        if (-not $fakeStaging.HasExited) { $fakeStaging.Kill() }
        foreach ($name in @("PAGENTOS_FAKE_TESTER_LOG", "PAGENTOS_FAKE_SESSION_FILE", "PAGENTOS_FAKE_SEED_LOG", "PAGENTOS_FAKE_SEED_TOKEN")) { Remove-Item "Env:\$name" -ErrorAction SilentlyContinue }
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "a job whose steps all answer 401 while the round's session is dead is 'environment' and forwards no card" {
    # t-d20261006: the nöbet job failed every step with 401 (the session revoked by a rotation)
    # and the round would have forwarded it as a software card.
    $work = New-Work
    $port = Get-Random -Minimum 41000 -Maximum 49000
    $fakeStaging = Start-FakeStaging -Port $port
    try {
        $team = Join-Path $work "team"
        [void](New-Item -ItemType Directory -Force -Path $team)
        Write-Utf8 (Join-Path $team "queue.json") '{"version":1,"tasks":[]}'
        Write-Utf8 (Join-Path $team "cycle-settings.json") '{"max_parallel":4,"test_parallel":4}'
        $plan = Join-Path $work "plan.json"
        Write-Utf8 $plan '{"jobs":[{"family":"kopuk","scenario":"scripts/testteam/scenarios/watches.json"}]}'
        $fake = New-FakeTester -Dir $work
        $seed = New-FakeSeed -Dir $work
        $env:PAGENTOS_FAKE_TESTER_LOG = Join-Path $work "calls"
        $env:PAGENTOS_FAKE_SESSION_FILE = Join-Path $work "owner.json"
        $env:PAGENTOS_FAKE_SEED_LOG = Join-Path $work "seed.log"
        $env:PAGENTOS_FAKE_SEED_TOKEN = "good"
        $outRoot = Join-Path $work "out"
        $out = & $powershell -NoProfile -File $testRound -Round "env" -TeamRoot $team -OutRoot $outRoot -PlanPath $plan -ClaudePath $powershell -ClaudePrefixArguments "-NoProfile,-File,$fake" -AssumeFreeGb 30 -AssumeGateRunning 0 -NoBoard -BaseUrl "http://127.0.0.1:$port" -AllowTestPort $port -SeedScript $seed -SessionFile $env:PAGENTOS_FAKE_SESSION_FILE 2>&1
        Assert-Equal -Expected 0 -Actual $LASTEXITCODE -Because "the round ends: $out"
        Assert-Equal -Expected 1 -Actual @(Get-FakeCalls -Dir $env:PAGENTOS_FAKE_TESTER_LOG).Count -Because "the tester ran: $out"
        $cards = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $outRoot "env\cards.json") | ConvertFrom-Json
        Assert-Equal -Expected "environment" -Actual ([string]@($cards.cards)[0].state) -Because "not failed: $out"
        Assert-Equal -Expected "" -Actual ([string]@($cards.cards)[0].forwarded_task) -Because "nothing forwarded"
        $queue = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $team "queue.json") | ConvertFrom-Json
        Assert-Equal -Expected 0 -Actual @($queue.tasks).Count -Because "no software card for a dead session: $out"
        Assert-True -Condition (($out -join " ") -match "ortam") -Because "the round says it was the environment: $out"
    }
    finally {
        try { [void](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/stop" -TimeoutSec 2) } catch { }
        if (-not $fakeStaging.HasExited) { $fakeStaging.Kill() }
        foreach ($name in @("PAGENTOS_FAKE_TESTER_LOG", "PAGENTOS_FAKE_SESSION_FILE", "PAGENTOS_FAKE_SEED_LOG", "PAGENTOS_FAKE_SEED_TOKEN")) { Remove-Item "Env:\$name" -ErrorAction SilentlyContinue }
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "a job with a 500 among its 401s on a dead session is forwarded, even when its result says 'environment'" {
    $work = New-Work
    $port = Get-Random -Minimum 41000 -Maximum 49000
    $fakeStaging = Start-FakeStaging -Port $port
    try {
        $team = Join-Path $work "team"
        [void](New-Item -ItemType Directory -Force -Path $team)
        Write-Utf8 (Join-Path $team "queue.json") '{"version":1,"tasks":[]}'
        Write-Utf8 (Join-Path $team "cycle-settings.json") '{"max_parallel":4,"test_parallel":4}'
        $plan = Join-Path $work "plan.json"
        Write-Utf8 $plan '{"jobs":[{"family":"karma","scenario":"scripts/testteam/scenarios/watches.json"},{"family":"karma-ortam","scenario":"scripts/testteam/scenarios/watches.json"}]}'
        $fake = New-FakeTester -Dir $work
        $seed = New-FakeSeed -Dir $work
        $env:PAGENTOS_FAKE_TESTER_LOG = Join-Path $work "calls"
        $env:PAGENTOS_FAKE_SESSION_FILE = Join-Path $work "owner.json"
        $env:PAGENTOS_FAKE_SEED_LOG = Join-Path $work "seed.log"
        $env:PAGENTOS_FAKE_SEED_TOKEN = "good"
        $outRoot = Join-Path $work "out"
        $out = & $powershell -NoProfile -File $testRound -Round "mix" -TeamRoot $team -OutRoot $outRoot -PlanPath $plan -ClaudePath $powershell -ClaudePrefixArguments "-NoProfile,-File,$fake" -AssumeFreeGb 30 -AssumeGateRunning 0 -NoBoard -BaseUrl "http://127.0.0.1:$port" -AllowTestPort $port -SeedScript $seed -SessionFile $env:PAGENTOS_FAKE_SESSION_FILE 2>&1
        Assert-Equal -Expected 0 -Actual $LASTEXITCODE -Because "the round ends: $out"
        Assert-Equal -Expected 2 -Actual @(Get-FakeCalls -Dir $env:PAGENTOS_FAKE_TESTER_LOG).Count -Because "both testers ran: $out"
        $cards = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $outRoot "mix\cards.json") | ConvertFrom-Json
        foreach ($card in @($cards.cards)) {
            Assert-Equal -Expected "failed" -Actual ([string]$card.state) -Because "$($card.family): the 500 is staging's bug, not the environment: $out"
            Assert-True -Condition ([bool][string]$card.forwarded_task) -Because "$($card.family) is forwarded: $out"
        }
        $queue = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $team "queue.json") | ConvertFrom-Json
        Assert-True -Condition (@($queue.tasks).Count -ge 1) -Because "a software card for the 500: $out"
    }
    finally {
        try { [void](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/stop" -TimeoutSec 2) } catch { }
        if (-not $fakeStaging.HasExited) { $fakeStaging.Kill() }
        foreach ($name in @("PAGENTOS_FAKE_TESTER_LOG", "PAGENTOS_FAKE_SESSION_FILE", "PAGENTOS_FAKE_SEED_LOG", "PAGENTOS_FAKE_SEED_TOKEN")) { Remove-Item "Env:\$name" -ErrorAction SilentlyContinue }
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "the tester's role file forbids re-seeding and identity changes in its 'Never anything irreversible' paragraph" {
    $tester = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\testteam\roles\tester.md"), [System.Text.Encoding]::UTF8)
    $paragraph = [regex]::Match($tester, '(?s)Never anything irreversible.*?(\r?\n\r?\n|$)').Value
    Assert-True -Condition ($paragraph -match "seed\.ps1") -Because "it names seed.ps1: $paragraph"
    Assert-True -Condition ($paragraph -match "(?i)rotat" -and $paragraph -match "(?i)revok" -and $paragraph -match "(?i)enrol") -Because "it names enrol, rotate and revoke: $paragraph"
    Assert-True -Condition ($paragraph -match "environment") -Because "it says a dead session is the environment: $paragraph"
}

# ============================================================================ the board

Write-Host ""
Write-Host "the board (services/api/app/team/board.py - outside this card's area)"

Test-Case "the board takes the test team's seats and the Danışman's, so the round's notes and the breaking report arrive" {
    # The inspector, 2026-10-05: 'board.ps1 post -Seat test-lead' answered HTTP 422 - the board's
    # SEAT_PATTERN knows no test seat and no Danışman, so no note of a round ever arrives and the
    # Ofis' Test odası shows five waiting seats. RED until board.py is widened (ALAN_ISTEGI).
    $board = [System.IO.File]::ReadAllText((Join-Path $repoRoot "services\api\app\team\board.py"), [System.Text.Encoding]::UTF8)
    $match = [regex]::Match($board, '(?m)^SEAT_PATTERN\s*=\s*r"([^"]+)"')
    Assert-True -Condition $match.Success -Because "board.py names SEAT_PATTERN"
    $pattern = $match.Groups[1].Value
    foreach ($seat in @(@(Get-TestTeamSeats) + "danisman")) {
        Assert-True -Condition ($seat -match $pattern) -Because "the board accepts the seat '$seat' (SEAT_PATTERN $pattern)"
    }
}

Test-Case "a round sends its breaking report to the board as test-lead, addressed to the Danışman's seat" {
    # The inspector, 2026-10-05: a round that posted the report with no -To survived every test.
    $work = New-Work
    try {
        $team = Join-Path $work "team"
        [void](New-Item -ItemType Directory -Force -Path $team)
        Write-Utf8 (Join-Path $team "queue.json") '{"version":1,"tasks":[]}'
        Write-Utf8 (Join-Path $team "cycle-settings.json") '{"max_parallel":4,"test_parallel":4}'
        $plan = Join-Path $work "plan.json"
        Write-Utf8 $plan '{"jobs":[{"family":"yuk","scenario":"scripts/testteam/scenarios/yuk.json","improvise":true}]}'
        $fake = New-FakeTester -Dir $work
        $env:PAGENTOS_FAKE_TESTER_LOG = Join-Path $work "calls"
        # A stand-in board.ps1: one line per post, its arguments joined by '|'.
        $posts = Join-Path $work "posts.log"
        $fakeBoard = Join-Path $work "board.ps1"
        Write-Utf8 $fakeBoard ("[IO.File]::AppendAllText('$posts', ((@(`$args) -join '|') + [Environment]::NewLine), (New-Object Text.UTF8Encoding(`$false)))")
        $out = & $powershell -NoProfile -File $testRound -NoAuth -Round "rb" -TeamRoot $team -OutRoot (Join-Path $work "out") -PlanPath $plan -ClaudePath $powershell -ClaudePrefixArguments "-NoProfile,-File,$fake" -AssumeFreeGb 30 -AssumeGateRunning 0 -BoardScript $fakeBoard 2>&1
        Assert-Equal -Expected 0 -Actual $LASTEXITCODE -Because "the round ends: $out"
        $lines = @([System.IO.File]::ReadAllLines($posts, [System.Text.Encoding]::UTF8) | Where-Object { $_ -match "kopma noktası" })
        Assert-Equal -Expected 1 -Actual @($lines).Count -Because "one breaking report note: $out"
        Assert-True -Condition ($lines[0] -match '(^|\|)-Seat\|test-lead(\||$)') -Because "posted as test-lead: $($lines[0])"
        Assert-True -Condition ($lines[0] -match '(^|\|)-To\|danisman(\||$)') -Because "addressed to the Danışman: $($lines[0])"
    }
    finally {
        Remove-Item Env:\PAGENTOS_FAKE_TESTER_LOG -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ============================================================================ staging must be main's tip

Write-Host ""
Write-Host "a round on a stale staging (staging-follows-release, the Danışman 2026-10-06)"

function Invoke-StagingRound {
    # A round against a fake staging serving -Sha, main's tip -MainSha, the round's seed writing
    # -Token ('good' is the session staging accepts, 'dead' one it does not); what is returned is
    # the exit code, the output, the fake testers' calls, whether a cards.json was written, the
    # software queue, the board posts and how often the seed ran.
    param([string]$Sha, [string]$MainSha, [string]$Token = "good", [string]$Extra = "")
    $work = New-Work
    $port = Get-Random -Minimum 41000 -Maximum 49000
    $staging = Start-FakeStaging -Port $port -Sha $Sha
    try {
        $team = Join-Path $work "team"
        [void](New-Item -ItemType Directory -Force -Path $team)
        Write-Utf8 (Join-Path $team "queue.json") '{"version":1,"tasks":[]}'
        Write-Utf8 (Join-Path $team "cycle-settings.json") '{"max_parallel":4,"test_parallel":4}'
        $plan = Join-Path $work "plan.json"
        Write-Utf8 $plan '{"jobs":[{"family":"kirik","scenario":"scripts/testteam/scenarios/kirik.json"},{"family":"saglik","scenario":"s.json"}]}'
        $fake = New-FakeTester -Dir $work
        $seed = New-FakeSeed -Dir $work
        $env:PAGENTOS_FAKE_TESTER_LOG = Join-Path $work "calls"
        $env:PAGENTOS_FAKE_SESSION_FILE = Join-Path $work "owner.json"
        $env:PAGENTOS_FAKE_SEED_LOG = Join-Path $work "seed.log"
        $env:PAGENTOS_FAKE_SEED_TOKEN = $Token
        $posts = Join-Path $work "posts.log"
        $fakeBoard = Join-Path $work "board.ps1"
        Write-Utf8 $fakeBoard ("[IO.File]::AppendAllText('$posts', ((@(`$args) -join '|') + [Environment]::NewLine), (New-Object Text.UTF8Encoding(`$false)))")
        $outRoot = Join-Path $work "out"
        $arguments = @("-NoProfile", "-File", $testRound, "-Round", "st", "-TeamRoot", $team, "-OutRoot", $outRoot, "-PlanPath", $plan,
            "-ClaudePath", $powershell, "-ClaudePrefixArguments", "-NoProfile,-File,$fake", "-AssumeFreeGb", "30", "-AssumeGateRunning", "0",
            "-BoardScript", $fakeBoard, "-BaseUrl", "http://127.0.0.1:$port", "-AllowTestPort", "$port",
            "-SeedScript", $seed, "-SessionFile", $env:PAGENTOS_FAKE_SESSION_FILE, "-MainSha", $MainSha)
        if ($Extra) { $arguments += $Extra }
        $out = & $powershell @arguments 2>&1
        $code = $LASTEXITCODE
        return [pscustomobject]@{
            ExitCode = $code; Output = ($out -join " ")
            Calls    = @(Get-FakeCalls -Dir $env:PAGENTOS_FAKE_TESTER_LOG)
            Cards    = (Test-Path -LiteralPath (Join-Path $outRoot "st\cards.json"))
            Queue    = @((Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $team "queue.json") | ConvertFrom-Json).tasks)
            Posts    = $(if (Test-Path -LiteralPath $posts) { [System.IO.File]::ReadAllText($posts, [System.Text.Encoding]::UTF8) } else { "" })
            Seeds    = $(if (Test-Path -LiteralPath $env:PAGENTOS_FAKE_SEED_LOG) { @(Get-Content -LiteralPath $env:PAGENTOS_FAKE_SEED_LOG | Where-Object { $_ }).Count } else { 0 })
        }
    }
    finally {
        try { [void](Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/stop" -TimeoutSec 2) } catch { }
        if (-not $staging.HasExited) { $staging.Kill() }
        foreach ($name in @("PAGENTOS_FAKE_TESTER_LOG", "PAGENTOS_FAKE_SESSION_FILE", "PAGENTOS_FAKE_SEED_LOG", "PAGENTOS_FAKE_SEED_TOKEN")) { Remove-Item "Env:\$name" -ErrorAction SilentlyContinue }
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Test-Case "a round whose staging serves another sha than main's tip starts no tester, opens no card, seeds nothing and says both shas" {
    # 2026-10-06 13:05: staging 6a21294c, production 72884b71 - every watch step 404, false failures.
    $old = "a" * 40; $main = "b" * 40
    $run = Invoke-StagingRound -Sha $old -MainSha $main
    Assert-Equal -Expected 4 -Actual $run.ExitCode -Because "refused, an exit of its own (not a failure, 1): $($run.Output)"
    Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "no tester started: $($run.Output)"
    Assert-Equal -Expected $false -Actual $run.Cards -Because "no card of the round is written"
    Assert-Equal -Expected 0 -Actual @($run.Queue).Count -Because "nothing reaches the software queue"
    Assert-Equal -Expected 0 -Actual $run.Seeds -Because "a refused round does not rotate staging's credential"
    Assert-True -Condition ($run.Output.Contains($old) -and $run.Output.Contains($main)) -Because "it says both shas: $($run.Output)"
    Assert-True -Condition ($run.Posts.Contains($old) -and $run.Posts.Contains($main)) -Because "and posts them on the board: $($run.Posts)"
}

Test-Case "-AllowStaleStaging starts a deliberate round on an old build" {
    $run = Invoke-StagingRound -Sha ("a" * 40) -MainSha ("b" * 40) -Extra "-AllowStaleStaging"
    Assert-Equal -Expected 0 -Actual $run.ExitCode -Because "the round ends: $($run.Output)"
    Assert-Equal -Expected 2 -Actual @($run.Calls).Count -Because "both jobs ran: $($run.Output)"
}

Test-Case "staging at main's tip with a session staging accepts starts the round" {
    $sha = "c" * 40
    $run = Invoke-StagingRound -Sha $sha -MainSha $sha
    Assert-Equal -Expected 0 -Actual $run.ExitCode -Because "the round ends: $($run.Output)"
    Assert-Equal -Expected 2 -Actual @($run.Calls).Count -Because "both jobs ran: $($run.Output)"
}

Test-Case "staging at main's tip whose seeded session answers 401 (redeployed, the seed did not take) starts no tester" {
    # 2026-10-06 13:20: right after a staging redeploy every authenticated step answered 401.
    $sha = "c" * 40
    $run = Invoke-StagingRound -Sha $sha -MainSha $sha -Token "dead"
    Assert-True -Condition ($run.ExitCode -ne 0) -Because "refused: $($run.Output)"
    Assert-Equal -Expected 0 -Actual @($run.Calls).Count -Because "no tester started: $($run.Output)"
    Assert-Equal -Expected 0 -Actual @($run.Queue).Count -Because "no card was opened"
    Assert-True -Condition ($run.Output -match "oturum" -and $run.Output -match "401") -Because "it says why: $($run.Output)"
    Assert-True -Condition ($run.Posts -match "oturum") -Because "on the board too: $($run.Posts)"
}

# ============================================================================ the role files

Write-Host ""
Write-Host "the role files"

Test-Case "test-lead and tester have role files with tools; the tester's names staging as its only target" {
    foreach ($role in @("test-lead", "tester")) {
        $file = Join-Path $repoRoot "scripts\testteam\roles\$role.md"
        Assert-True -Condition (Test-Path -LiteralPath $file) -Because "$role.md"
        Assert-True -Condition (@(Get-TeamRoleTools -RoleFile $file).Count -gt 0) -Because "$role grants tools"
        Assert-True -Condition (@(Get-TeamRoleTools -RoleFile $file) -notcontains "Agent") -Because "$role starts no agents"
        Assert-True -Condition (@(Get-TeamRoleTools -RoleFile $file) -notcontains "Edit") -Because "$role edits no code"
        # The installed copy (.claude/agents/) is the source byte for byte when it is there.
        $installed = Join-Path $repoRoot ".claude\agents\$role.md"
        if (Test-Path -LiteralPath $installed) {
            Assert-Equal -Expected (Get-FileHash -LiteralPath $file).Hash -Actual (Get-FileHash -LiteralPath $installed).Hash -Because ".claude/agents/$role.md is a copy of scripts/testteam/roles/$role.md"
        }
    }
    $tester = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\testteam\roles\tester.md"), [System.Text.Encoding]::UTF8)
    Assert-True -Condition ($tester -match "127\.0\.0\.1:28001" -and $tester -match "run-scenario\.ps1") -Because "the tester's target and tool"
}

Write-Host ""
Write-Host "testteam tests: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
