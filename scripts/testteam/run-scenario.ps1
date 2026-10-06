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
    stops at the first load with an error, or a p95 over max_p95_ms - each request timed from
    its own send, and a breaking load measured TWICE (a load that breaks once and holds the
    second time is 'flaky' and the ladder goes on): that is the breaking point, with its numbers. Never anything irreversible: a scenario only does what the owner
    does on staging, and staging is a copy. A step (or cleanup step) that would enrol, rotate or
    revoke an identity - any write under /v1/identity/, a device enrol or revoke, a credential
    rotation - is NOT sent: it is recorded under 'refused' (state 'refused'); a ladder aimed at
    one refuses the whole scenario. The test team shares one seeded owner session.

    A step that fails with 401 while the session itself is no longer accepted (its own
    /v1/identity/sessions/current answers 401) is the environment, not staging's bug: the
    result is 'environment' and the ladder is not run.

    The result file is <OutDir>/<card>.result.json:
      { card, family, scenario, state: passed|failed|broke|environment, staging_sha, steps: [ {
        name, method, path, expected, actual, ok, ms } ], breaking: { what, tried: [ { load, ok,
        errors, p95_ms } ], first_failure }, screenshot, refused: [ { name, method, path, state,
        why } ], environment }

    Exit codes: 0 passed, 1 a step failed, 2 refused (not staging, or no scenario), 3 broke
    (every step passed, the ladder found the breaking point), 4 environment (the staging
    session is no longer accepted).

.PARAMETER AllowTestPort
    For scripts/tests/testteam.tests.ps1 only: one more port on 127.0.0.1 (a stand-in for
    staging, so the test never touches the real staging stack), and only a port of the tests'
    range 41000-49999 - the dev api's :8000 is refused (exit 2). No other host, ever.
.PARAMETER OutDir
    Default: <run_temp_root of team/cycle-settings.json, else TEMP>\testteam\manual. A folder
    inside the checkout is refused: results and staging screenshots are run data.
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

if ($AllowTestPort -ne 0 -and -not (Test-TestTeamTestPort -Port $AllowTestPort)) {
    Stop-Refused "-AllowTestPort $AllowTestPort testlerin aralığında değil (41000-49999): dev api, web kabuğu ya da başka bir servis staging yerine geçmez"
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

function Get-IdentityChange {
    <# Why a step would change staging's identity - enrol, rotate or revoke a credential, a
       session or a device - or "" when it does not. Every other tester shares the one seeded
       owner session: t-d20261006 (2026-10-06) lost a whole round to 401 after a rotation. #>
    param($Step)
    $method = ([string](Get-TeamProperty -InputObject $Step -Name "method" -Default "GET")).ToUpperInvariant()
    if (@("GET", "HEAD", "OPTIONS") -contains $method) { return "" }
    $path = ([System.Uri](Resolve-StepUrl -Step $Step)).AbsolutePath.ToLowerInvariant()
    if ($path -match '^/v1/identity(/|$)') { return "$method $path kimliği değiştirir (oturum aç/yenile/kapat/iptal, kimlik kurulumu, panik)" }
    if ($path -match '^/v1/devices/(enroll|enrol)/?$' -or $path -match '^/v1/devices/[^/]+/revoke/?$') { return "$method $path bir cihazı kaydeder ya da iptal eder" }
    if ($path -match '(^|/)(rotate|credentials?)(/|$)') { return "$method $path bir kimlik bilgisini döndürür" }
    return ""
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
if ($null -ne $breaking) {
    $change = Get-IdentityChange -Step $breaking
    if ($change) { Stop-Refused "KİMLİK DEĞİŞİKLİĞİ: kopma merdiveni $change - test ekibinin ortak oturumu düşer" }
}
if ($DryRun) { Write-Host "senaryo hedefleri staging: $Scenario"; exit 0 }

$family = [string](Get-TeamProperty -InputObject $document -Name "family" -Default "")
if (-not $Card) { $Card = "tek-" + [string](Get-TeamProperty -InputObject $document -Name "id" -Default "senaryo") }
if (-not $OutDir) {
    # Run data (results, staging screenshots) lives under the cycle's run_temp_root, never in the tree.
    $tempRoot = Read-TeamRunTempRoot -Path (Join-Path $repoRoot "team\cycle-settings.json")
    if (-not $tempRoot) { $tempRoot = $env:TEMP }
    $OutDir = Join-Path $tempRoot "testteam\manual"
}
if (-not (Test-TestTeamPathOutside -Path $OutDir -Root $repoRoot)) { Stop-Refused "sonuç klasörü depo ağacının içinde: $OutDir (run_temp_root altına yazılır)" }
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
# .NET Framework allows two connections to a host by default: a ladder of 256 would queue in
# this process and measure the queue.
[System.Net.ServicePointManager]::DefaultConnectionLimit = 1024
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
$refused = New-Object System.Collections.ArrayList
$screenshot = ""
$anyFailed = $false
$rotatedSaid = $false

function Test-Refused {
    # A step that would change staging's identity is not sent; it is recorded as 'refused'.
    param($Step, [string]$Name)
    $change = Get-IdentityChange -Step $Step
    if (-not $change) { return $false }
    Write-Host "  REDDEDİLDİ $Name - gönderilmedi: $change; test ekibinin ortak staging oturumu düşerdi (t-d20261006)"
    [void]$refused.Add([pscustomobject]@{ name = $Name; method = [string](Get-TeamProperty -InputObject $Step -Name "method" -Default "GET"); path = ([System.Uri](Resolve-StepUrl -Step $Step)).AbsolutePath; state = "refused"; why = $change })
    return $true
}

foreach ($step in $steps) {
    $name = [string](Get-TeamProperty -InputObject $step -Name "name" -Default "")
    if (Test-Refused -Step $step -Name $name) { continue }
    if ([string](Get-TeamProperty -InputObject $step -Name "web" -Default "")) {
        $shot = Join-Path $OutDir ("{0}-{1}.png" -f $Card, ($records.Count + 1))
        $web = Invoke-WebStep -Step $step -Shot $shot
        if ([string]$web.screenshot) { $screenshot = [string]$web.screenshot }
        $ok = [bool]$web.ok
        [void]$records.Add([pscustomobject]@{ name = $name; method = "WEB"; path = [string]$step.web; expected = [string](Get-TeamProperty -InputObject $step -Name "expect" -Default "geçer"); actual = [string]$web.actual; ok = $ok; ms = 0 })
    }
    else {
        $answer = Invoke-Step -Step $step
        if ($answer.Text -match 'owner_credential_rotated') { $rotatedSaid = $true }
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

# A 401 is staging's answer to the SESSION, not to the scenario, when the session itself is no
# longer accepted: its own /v1/identity/sessions/current answers 401 too (the api's 401 body
# says only 'unauthorized'; the reason, e.g. owner_credential_rotated, is in staging's log).
# Every failed step must be a 401: a 500 before the session died is still staging's bug.
$environment = ""
$failedSteps = @($records | Where-Object { -not $_.ok })
$all401 = (@($failedSteps).Count -gt 0) -and (@($failedSteps | Where-Object { [string]$_.actual -notmatch '^401\b' }).Count -eq 0)
if ($anyFailed -and $token -and $all401) {
    $probe = Invoke-Step -Step ([pscustomobject]@{ method = "GET"; path = "/v1/identity/sessions/current" })
    if ($rotatedSaid -or $probe.Status -eq "401") {
        $environment = "staging oturumu geçersiz: /v1/identity/sessions/current $($probe.Status) (kimlik döndürülmüş ya da oturum iptal; seed.ps1 tur başında bir kez)"
        Write-Host "  ORTAM: $environment - bu bir yazılım hatası değil"
    }
}

function Measure-Load {
    # One load of the ladder: `load` requests sent AT ONCE from compiled code, each timed from its
    # own send to its own last byte (a PowerShell loop that builds the requests and awaits them
    # in order times the loop, not the server: the first real rounds' p95 grew with the load).
    param([int]$Load)
    $method = ([string](Get-TeamProperty -InputObject $breaking -Name "method" -Default "GET")).ToUpperInvariant()
    $body = Get-TeamProperty -InputObject $breaking -Name "body"
    $json = if ($null -ne $body) { ConvertTo-Json -InputObject $body -Depth 8 -Compress } else { $null }
    $answers = [TestTeamLadder]::Fire($client, $method, (Resolve-StepUrl -Step $breaking), $token, $json, $Load)
    $okCount = 0; $errors = 0; $firstError = ""
    $timings = New-Object System.Collections.ArrayList
    foreach ($answer in $answers) {
        if ($answer.Status -ge 200 -and $answer.Status -lt 300) { $okCount++ }
        else {
            $errors++
            if (-not $firstError) { $firstError = if ($answer.Status -gt 0) { "HTTP $($answer.Status)" } else { [string]$answer.Error -replace '\s+', ' ' } }
        }
        [void]$timings.Add([int64]$answer.Ms)
    }
    $slowest = if ($timings.Count -gt 0) { [int64](@($timings.ToArray()) | Measure-Object -Maximum).Maximum } else { 0 }
    return [pscustomobject]@{ load = $Load; ok = $okCount; errors = $errors; p95_ms = (Get-TestTeamP95 -Timings @($timings.ToArray())); max_ms = $slowest; first_error = $firstError }
}

$ladder = $null
# A dead session's ladder would measure 401s, not a load.
if ($null -ne $breaking -and -not $environment) {
    if (-not ('TestTeamLadder' -as [type])) {
        Add-Type -ReferencedAssemblies System.Net.Http -TypeDefinition @"
using System;
using System.Diagnostics;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
public sealed class TestTeamAnswer { public int Status; public long Ms; public string Error; }
public static class TestTeamLadder {
    public static TestTeamAnswer[] Fire(HttpClient client, string method, string url, string token, string json, int load) {
        var tasks = new Task<TestTeamAnswer>[load];
        for (int i = 0; i < load; i++) { tasks[i] = One(client, method, url, token, json, load, i); }
        Task.WaitAll(tasks);
        var answers = new TestTeamAnswer[load];
        for (int i = 0; i < load; i++) { answers[i] = tasks[i].Result; }
        return answers;
    }
    static async Task<TestTeamAnswer> One(HttpClient client, string method, string url, string token, string json, int load, int seq) {
        var request = new HttpRequestMessage(new HttpMethod(method), url);
        if (!String.IsNullOrEmpty(token)) { request.Headers.TryAddWithoutValidation("Authorization", "Bearer " + token); }
        request.Headers.TryAddWithoutValidation("X-Load", load.ToString());
        request.Headers.TryAddWithoutValidation("X-Seq", seq.ToString());
        // PowerShell passes a $null string as "": no body is an empty string too.
        if (!String.IsNullOrEmpty(json)) { request.Content = new StringContent(json, Encoding.UTF8, "application/json"); }
        var answer = new TestTeamAnswer();
        var clock = Stopwatch.StartNew();
        try {
            using (var response = await client.SendAsync(request).ConfigureAwait(false)) {
                await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                answer.Status = (int)response.StatusCode;
            }
        }
        catch (Exception e) { answer.Status = 0; answer.Error = e.GetBaseException().Message; }
        answer.Ms = clock.ElapsedMilliseconds;
        return answer;
    }
}
"@
    }
    $load = [Math]::Max(1, [int](Get-TeamProperty -InputObject $breaking -Name "start" -Default 1))
    $factor = [Math]::Max(2, [int](Get-TeamProperty -InputObject $breaking -Name "factor" -Default 2))
    $max = [Math]::Min(512, [Math]::Max($load, [int](Get-TeamProperty -InputObject $breaking -Name "max" -Default 64)))
    $limitMs = [int](Get-TeamProperty -InputObject $breaking -Name "max_p95_ms" -Default 0)
    $tried = New-Object System.Collections.ArrayList
    $first = $null
    while ($load -le $max) {
        $step = Measure-Load -Load $load
        Write-Host ("  yük {0}: {1} başarılı, {2} hata, istek başına p95 {3} ms" -f $load, $step.ok, $step.errors, $step.p95_ms)
        if (Test-TestTeamLoadBroken -Measured $step -LimitMs $limitMs) {
            # A breaking load is measured once more before it is the breaking point: one bad
            # moment of the machine is not where staging breaks.
            $again = Measure-Load -Load $load
            Write-Host ("  yük {0} yinelendi: {1} başarılı, {2} hata, istek başına p95 {3} ms" -f $load, $again.ok, $again.errors, $again.p95_ms)
            Add-Member -InputObject $step -NotePropertyName repeat -NotePropertyValue ([pscustomobject]@{ ok = $again.ok; errors = $again.errors; p95_ms = $again.p95_ms })
            if (Test-TestTeamLoadBroken -Measured $again -LimitMs $limitMs) { [void]$tried.Add($step); $first = $step; break }
            Add-Member -InputObject $step -NotePropertyName flaky -NotePropertyValue $true
        }
        [void]$tried.Add($step)
        $load = $load * $factor
    }
    $ladder = [pscustomobject]@{
        what = [string](Get-TeamProperty -InputObject $breaking -Name "what" -Default "")
        tried = @($tried.ToArray()); first_failure = $first
    }
}
# What the scenario made on staging it removes again, whatever happened above.
foreach ($step in $cleanup) {
    $name = [string](Get-TeamProperty -InputObject $step -Name "name" -Default "")
    if (Test-Refused -Step $step -Name $name) { continue }
    $answer = Invoke-Step -Step $step
    Write-Host ("  temizlik {0}: {1}" -f $name, $answer.Status)
}
$client.Dispose()

$state = "passed"
if ($environment) { $state = "environment" }
elseif ($anyFailed) { $state = "failed" }
elseif ($null -ne $ladder -and $null -ne $ladder.first_failure) { $state = "broke" }
$result = [pscustomobject]@{
    card = $Card; family = $family; scenario = $Scenario; state = $state; staging_sha = $stagingSha
    steps = @($records.ToArray()); breaking = $ladder; screenshot = $screenshot
    refused = @($refused.ToArray()); environment = $environment
    at = (Get-TeamTimestamp)
}
$file = Join-Path $OutDir "$Card.result.json"
[System.IO.File]::WriteAllText($file, (ConvertTo-Json -InputObject $result -Depth 8), (New-Object System.Text.UTF8Encoding($false)))
Write-Host "sonuç: $state -> $file"
if ($state -eq "failed") { exit 1 }
if ($state -eq "broke") { exit 3 }
if ($state -eq "environment") { exit 4 }
exit 0
