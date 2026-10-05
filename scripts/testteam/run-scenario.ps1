<#
.SYNOPSIS
    Runs one test-team scenario against STAGING and writes its result file. It refuses every
    other host (exit 2) before it sends anything.

.DESCRIPTION
    A scenario is a JSON file (scripts/testteam/scenarios/):

      { "id": "...", "family": "...",
        "steps": [ { "name": "...", "method": "GET|POST|DELETE", "path": "/v1/..." | "url": "http://...",
                     "body": { ... }, "expect_status": 200, "expect_contains": "text",
                     "web": "apps/web/tests/e2e/owner-scenarios/<script>.mjs" } ],
        "breaking": { "method": "GET", "path": "/v1/...", "body": { ... }, "start": 1, "factor": 2,
                      "max": 64, "max_p95_ms": 5000, "what": "..." },
        "cleanup": [ { "name": "...", "method": "DELETE", "path": "/v1/..." } ] }

    Each step is one request (with the staging owner session from scripts/staging/seed.ps1),
    or - with "web" - one Playwright script run through node that opens the staging web shell
    and writes a screenshot (the voice steps feed a Turkish wav as the fake microphone). The
    'breaking' ladder sends `load` requests AT ONCE, then load*factor, ... up to max, and
    stops at the first load with an error, or a p95 over max_p95_ms: that is the breaking
    point, with its numbers. Never anything irreversible: a scenario only does what the owner
    does on staging, and staging is a copy.

    The result file is <OutDir>/<card>.result.json:
      { card, family, scenario, state: passed|failed|broke, staging_sha, steps: [ { name,
        method, path, expected, actual, ok, ms } ], breaking: { what, tried: [ { load, ok,
        errors, p95_ms } ], first_failure }, screenshot }

    Exit codes: 0 passed, 1 a step failed, 2 refused (not staging, or no scenario), 3 broke
    (every step passed, the ladder found the breaking point).

.PARAMETER AllowTestPort
    For scripts/tests/testteam.tests.ps1 only: one more port on 127.0.0.1 (a stand-in for
    staging, so the test never touches the real staging stack). No other host, ever.
.PARAMETER NoAuth
    Send no session (the stand-in needs none).
.PARAMETER DryRun
    Judge the scenario's targets and stop.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Scenario,
    [string]$BaseUrl = "http://127.0.0.1:28001",
    [int]$AllowTestPort = 0,
    [string]$OutDir = "",
    [string]$Card = "",
    [string]$SessionFile = "",
    [switch]$NoAuth,
    [switch]$DryRun,
    [int]$TimeoutSec = 30
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $PSScriptRoot "TestTeam.ps1")

function Stop-Refused {
    param([string]$Why)
    Write-Host "SENARYO REDDEDİLDİ: $Why"
    exit 2
}

if (-not (Test-Path -LiteralPath $Scenario)) { Stop-Refused "senaryo dosyası yok: $Scenario" }
$document = Get-Content -Raw -Encoding UTF8 -LiteralPath $Scenario | ConvertFrom-Json
$BaseUrl = $BaseUrl.TrimEnd("/")
if (-not (Test-TestTeamStagingUrl -Url $BaseUrl -AllowTestPort $AllowTestPort)) {
    Stop-Refused "STAGING DEĞİL: $BaseUrl (test ekibi yalnız staging'e gider: http://127.0.0.1:28001 ve :28000)"
}

function Resolve-StepUrl {
    param($Step)
    $url = [string](Get-TeamProperty -InputObject $Step -Name "url" -Default "")
    if ($url) { return $url }
    return $BaseUrl + [string](Get-TeamProperty -InputObject $Step -Name "path" -Default "/")
}

$steps = @(Get-TeamProperty -InputObject $document -Name "steps" -Default @())
$breaking = Get-TeamProperty -InputObject $document -Name "breaking"
$cleanup = @(Get-TeamProperty -InputObject $document -Name "cleanup" -Default @())
# Every target is judged BEFORE the first request: a scenario is refused whole.
foreach ($step in @($steps + $cleanup)) {
    $target = Resolve-StepUrl -Step $step
    if (-not (Test-TestTeamStagingUrl -Url $target -AllowTestPort $AllowTestPort)) {
        Stop-Refused "STAGING DEĞİL: '$($step.name)' adımı $target adresine gidiyor"
    }
}
if ($null -ne $breaking -and -not (Test-TestTeamStagingUrl -Url (Resolve-StepUrl -Step $breaking) -AllowTestPort $AllowTestPort)) {
    Stop-Refused "STAGING DEĞİL: kopma merdiveni $(Resolve-StepUrl -Step $breaking) adresine gidiyor"
}
if ($DryRun) { Write-Host "senaryo hedefleri staging: $Scenario"; exit 0 }

$family = [string](Get-TeamProperty -InputObject $document -Name "family" -Default "")
if (-not $Card) { $Card = "tek-" + [string](Get-TeamProperty -InputObject $document -Name "id" -Default "senaryo") }
if (-not $OutDir) { $OutDir = Join-Path $repoRoot "team\testteam\manual" }
[void](New-Item -ItemType Directory -Force -Path $OutDir)

$token = ""
if (-not $NoAuth) {
    if (-not $SessionFile) { $SessionFile = Join-Path $env:LOCALAPPDATA "PagentOS\staging\owner.json" }
    if (-not (Test-Path -LiteralPath $SessionFile)) { Stop-Refused "staging oturumu yok ($SessionFile): önce scripts\staging\seed.ps1" }
    $session = Get-Content -Raw -Encoding UTF8 -LiteralPath $SessionFile | ConvertFrom-Json
    # The seed's own record names the api it opens; a record of another api is not used.
    $recorded = [string](Get-TeamProperty -InputObject $session -Name "api" -Default "")
    if ($recorded -and -not (Test-TestTeamStagingUrl -Url $recorded)) { Stop-Refused "oturum dosyası staging'in değil: $recorded" }
    $token = [string](Get-TeamProperty -InputObject $session -Name "session_token" -Default "")
}

Add-Type -AssemblyName System.Net.Http
$client = New-Object System.Net.Http.HttpClient
$client.Timeout = [TimeSpan]::FromSeconds($TimeoutSec)

function New-Request {
    param($Step, [int]$Load = 0)
    $method = ([string](Get-TeamProperty -InputObject $Step -Name "method" -Default "GET")).ToUpperInvariant()
    $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::new($method), (Resolve-StepUrl -Step $Step))
    if ($token) { [void]$request.Headers.TryAddWithoutValidation("Authorization", "Bearer $token") }
    if ($Load -gt 0) { [void]$request.Headers.TryAddWithoutValidation("X-Load", [string]$Load) }
    $body = Get-TeamProperty -InputObject $Step -Name "body"
    if ($null -ne $body) {
        $json = ConvertTo-Json -InputObject $body -Depth 8 -Compress
        $request.Content = [System.Net.Http.StringContent]::new($json, [System.Text.Encoding]::UTF8, "application/json")
    }
    return $request
}

function Invoke-Step {
    param($Step)
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    $status = "hata"; $text = ""
    try {
        $answer = $client.SendAsync((New-Request -Step $Step)).GetAwaiter().GetResult()
        $status = [string][int]$answer.StatusCode
        $text = $answer.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    }
    catch { $status = "hata: " + ($_.Exception.GetBaseException().Message -replace '\s+', ' ') }
    return [pscustomobject]@{ Status = $status; Text = $text; Ms = [int]$clock.ElapsedMilliseconds }
}

function Get-BrowserPython {
    # Playwright lives in services/browser's environment: this checkout's, else the main checkout's.
    $candidates = @((Join-Path $repoRoot "services\browser\.venv\Scripts\python.exe"))
    $common = (& git -C $repoRoot rev-parse --path-format=absolute --git-common-dir 2>$null)
    if ($LASTEXITCODE -eq 0 -and $common) { $candidates += (Join-Path (Split-Path -Parent ([string]$common).Trim()) "services\browser\.venv\Scripts\python.exe") }
    foreach ($candidate in $candidates) { if (Test-Path -LiteralPath $candidate) { return $candidate } }
    return ""
}

function Invoke-WebStep {
    # A Playwright script (apps/web/tests/e2e/owner-scenarios/): a .py on services/browser's
    # Python, a .mjs on node. It prints one JSON line { ok, actual, screenshot }. The web
    # shell's url is the api's sibling port; "wav" is the fake microphone of a voice step.
    param($Step, [string]$Shot)
    $script = Join-Path $repoRoot ([string]$Step.web)
    $web = (Get-TeamProperty -InputObject $Step -Name "url" -Default "")
    if (-not $web) { $web = ($BaseUrl -replace ':28001$', ':28000') + "/" }
    $extra = @("--url", $web, "--shot", $Shot, "--session", $SessionFile)
    $wav = [string](Get-TeamProperty -InputObject $Step -Name "wav" -Default "")
    if ($wav) { $extra += @("--wav", (Join-Path $repoRoot $wav)) }
    $expect = [string](Get-TeamProperty -InputObject $Step -Name "expect_contains" -Default "")
    if ($expect) { $extra += @("--expect", $expect) }
    if ($AllowTestPort -gt 0) { $extra += @("--allow-test-port", [string]$AllowTestPort) }
    # A native program's standard error is its log, not a terminating error of this script.
    $ErrorActionPreference = "Continue"
    if ($script -like "*.py") {
        $python = Get-BrowserPython
        if (-not $python) { return [pscustomobject]@{ ok = $false; actual = "services/browser ortamı (Playwright) yok"; screenshot = "" } }
        $out = @(& $python $script @extra 2>&1 | ForEach-Object { [string]$_ })
    }
    else {
        $node = (Get-Command node -ErrorAction SilentlyContinue)
        if ($null -eq $node) { return [pscustomobject]@{ ok = $false; actual = "node yok"; screenshot = "" } }
        $out = @(& $node.Source $script @extra 2>&1 | ForEach-Object { [string]$_ })
    }
    $line = @($out | Where-Object { [string]$_ -match '^\{' }) | Select-Object -Last 1
    if (-not $line) { return [pscustomobject]@{ ok = $false; actual = ("çıktı yok: " + (($out | Select-Object -Last 3) -join " ")); screenshot = "" } }
    return ([string]$line | ConvertFrom-Json)
}

$stagingSha = ""
try {
    $health = $client.GetStringAsync("$BaseUrl/v1/system/health").GetAwaiter().GetResult() | ConvertFrom-Json
    $release = Get-TeamProperty -InputObject $health -Name "release"
    if ($null -ne $release) { $stagingSha = [string](Get-TeamProperty -InputObject $release -Name "version" -Default "") }
}
catch { }

$records = New-Object System.Collections.ArrayList
$screenshot = ""
$anyFailed = $false
foreach ($step in $steps) {
    $name = [string](Get-TeamProperty -InputObject $step -Name "name" -Default "")
    if ([string](Get-TeamProperty -InputObject $step -Name "web" -Default "")) {
        $shot = Join-Path $OutDir ("{0}-{1}.png" -f $Card, ($records.Count + 1))
        $web = Invoke-WebStep -Step $step -Shot $shot
        if ([string]$web.screenshot) { $screenshot = [string]$web.screenshot }
        $ok = [bool]$web.ok
        [void]$records.Add([pscustomobject]@{ name = $name; method = "WEB"; path = [string]$step.web; expected = [string](Get-TeamProperty -InputObject $step -Name "expect" -Default "geçer"); actual = [string]$web.actual; ok = $ok; ms = 0 })
    }
    else {
        $answer = Invoke-Step -Step $step
        $expected = [string](Get-TeamProperty -InputObject $step -Name "expect_status" -Default 200)
        $contains = [string](Get-TeamProperty -InputObject $step -Name "expect_contains" -Default "")
        $ok = ($answer.Status -eq $expected) -and (-not $contains -or $answer.Text.Contains($contains))
        $actual = $answer.Status
        if ($answer.Status -eq $expected -and -not $ok) { $actual = "$($answer.Status), yanıtta '$contains' yok" }
        if ($contains) { $expected = "$expected, yanıtta '$contains'" }
        [void]$records.Add([pscustomobject]@{
                name = $name; method = [string](Get-TeamProperty -InputObject $step -Name "method" -Default "GET")
                path = [string](Get-TeamProperty -InputObject $step -Name "path" -Default (Get-TeamProperty -InputObject $step -Name "url" -Default ""))
                expected = $expected; actual = $actual; ok = $ok; ms = $answer.Ms
            })
    }
    if (-not $ok) { $anyFailed = $true }
    Write-Host ("  {0} {1} ({2})" -f $(if ($ok) { "GEÇTİ" } else { "KALDI" }), $name, $records[$records.Count - 1].actual)
}

$ladder = $null
if ($null -ne $breaking) {
    $load = [Math]::Max(1, [int](Get-TeamProperty -InputObject $breaking -Name "start" -Default 1))
    $factor = [Math]::Max(2, [int](Get-TeamProperty -InputObject $breaking -Name "factor" -Default 2))
    $max = [Math]::Min(512, [Math]::Max($load, [int](Get-TeamProperty -InputObject $breaking -Name "max" -Default 64)))
    $limitMs = [int](Get-TeamProperty -InputObject $breaking -Name "max_p95_ms" -Default 0)
    $tried = New-Object System.Collections.ArrayList
    $first = $null
    while ($load -le $max) {
        $tasks = New-Object System.Collections.ArrayList
        $clock = [System.Diagnostics.Stopwatch]::StartNew()
        $timings = New-Object System.Collections.ArrayList
        for ($i = 0; $i -lt $load; $i++) { [void]$tasks.Add($client.SendAsync((New-Request -Step $breaking -Load $load))) }
        $okCount = 0; $errors = 0
        foreach ($t in $tasks) {
            try {
                $answer = $t.GetAwaiter().GetResult()
                if ([int]$answer.StatusCode -ge 200 -and [int]$answer.StatusCode -lt 300) { $okCount++ } else { $errors++ }
            }
            catch { $errors++ }
            [void]$timings.Add([int]$clock.ElapsedMilliseconds)
        }
        $sorted = @($timings | Sort-Object)
        $p95 = $sorted[[Math]::Min($sorted.Count - 1, [int][Math]::Ceiling(0.95 * $sorted.Count) - 1)]
        $step = [pscustomobject]@{ load = $load; ok = $okCount; errors = $errors; p95_ms = $p95 }
        [void]$tried.Add($step)
        Write-Host ("  yük {0}: {1} başarılı, {2} hata, p95 {3} ms" -f $load, $okCount, $errors, $p95)
        if ($errors -gt 0 -or ($limitMs -gt 0 -and $p95 -gt $limitMs)) { $first = $step; break }
        $load = $load * $factor
    }
    $ladder = [pscustomobject]@{
        what = [string](Get-TeamProperty -InputObject $breaking -Name "what" -Default "")
        tried = @($tried.ToArray()); first_failure = $first
    }
}
# What the scenario made on staging it removes again, whatever happened above.
foreach ($step in $cleanup) {
    $answer = Invoke-Step -Step $step
    Write-Host ("  temizlik {0}: {1}" -f [string](Get-TeamProperty -InputObject $step -Name "name" -Default ""), $answer.Status)
}
$client.Dispose()

$state = "passed"
if ($anyFailed) { $state = "failed" }
elseif ($null -ne $ladder -and $null -ne $ladder.first_failure) { $state = "broke" }
$result = [pscustomobject]@{
    card = $Card; family = $family; scenario = $Scenario; state = $state; staging_sha = $stagingSha
    steps = @($records.ToArray()); breaking = $ladder; screenshot = $screenshot
    at = (Get-TeamTimestamp)
}
$file = Join-Path $OutDir "$Card.result.json"
[System.IO.File]::WriteAllText($file, (ConvertTo-Json -InputObject $result -Depth 8), (New-Object System.Text.UTF8Encoding($false)))
Write-Host "sonuç: $state -> $file"
if ($state -eq "failed") { exit 1 }
if ($state -eq "broke") { exit 3 }
exit 0
