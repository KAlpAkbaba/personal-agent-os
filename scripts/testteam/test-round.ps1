<#
.SYNOPSIS
    One round of the TEST team (team/plans/test-team-adr.md): the Test Proje Yöneticisi's plan
    is dealt to tester-1..4, their results come back, every failure is forwarded to the
    software Proje Yöneticisi's queue, and the breaking-point report goes to the Danışman.

.DESCRIPTION
    The owner's design of 2026-10-03, binding: the test team is SEPARATE from the software
    team - its own seats (test-lead, tester-1..4), its own cap ('test_parallel' in
    team/cycle-settings.json), its own cards (<OutRoot>/<round>/cards.json, states planned /
    running / passed / failed / broke). It never takes a software seat: a round runs beside a
    cycle and the cycle's max_parallel does not move.

      1. The plan: -PlanPath, or ONE fresh `claude -p` run of the test lead
         (.claude/agents/test-lead.md) that writes <OutRoot>/<round>/plan.json - jobs, one
         scenario family each, from the roadmap's HAVE rows, the owner's 'Dene' list and the
         recent releases.
      2. The cap: test_parallel, lowered to none under the memory floor and to one while the
         gate holds a heavy test slot (Get-TeamTestCap); the reasons are printed and posted.
      3. The jobs are dealt to the testers in turn (New-TestTeamCards) and run, at most the cap
         at once: each is ONE fresh `claude -p` run of .claude/agents/tester.md with its job
         card; it writes its result file and reports to the test lead (this script) only.
      4. The failures are deduplicated and each becomes a normal card of the software queue
         (state 'proposed', steps/expected/actual/scenario/screenshot/staging sha); a card whose
         id is already in the queue is not opened again.
      4a. The proof (proof-from-test-rounds-and-trials): per JARVIS roadmap row a plan job names
         ('roadmap_row'), the scenarios that passed and failed on staging, with the staging sha,
         POSTed to the Cloud Core (/v1/team/queue/proof) - the Ofis' 'staging'de kanıtlı'.
         -PostProof posts a finished round's proof again and does nothing else.
      5. The 'kopma noktası' report: <OutRoot>/<round>/kopma-noktasi.md, and one board note
         addressed to the Danışman's seat ('danisman') (never to the owner).
      6. The owner's input/output report (test-round-io-report): <OutRoot>/<round>/test-raporu.md
         - per tester and per step Girdi / Beklenen / Çıktı / Sonuç, the plan's why, the ladder,
         the forwarded cards - and, with -QueueUrl, POST /v1/team/test-reports on the Cloud Core
         (the Ofis' 'Test raporları'). A round that dies writes what it had, 'yarım kaldı: <why>'.

    Before the plan (staging-follows-release, the Danışman 2026-10-06): staging must serve
    origin/main's tip (/v1/system/health release.version). Otherwise the round REFUSES - exit 4,
    a board note naming both shas, no plan, no card, no tester: a round on an old build judged
    old code (every step 404). -AllowStaleStaging skips the check for a deliberate test of an old
    build. A test's stand-in staging (-NoAuth or -AllowTestPort) is checked only when -MainSha
    names the sha it must serve. The session is the round's own seed (below).

    -Retest: for every failed card of -Round whose forwarded task is released, done or
    awaiting_real_evidence (NOT merged: an integration branch is not staging) AND whose staging
    now answers a sha other than the one the failure was found on (found_sha), the scenario is
    run again on staging (run-scenario.ps1, no model): passed closes the card, failed reopens it
    (reopened +1). A fix not yet released, or a staging not yet redeployed, is left alone.

    The cap is measured again before every tester start (the settings file, the free memory,
    the gate's test slot): a gate that takes the heavy slot mid-round, or memory that falls under
    the floor, holds the next job; the reason is said once. Cards that could not start stay
    planned.

    Run data - cards.json, plan.json, results, logs, staging screenshots, the report - lives
    under -OutRoot (default: <run_temp_root of team/cycle-settings.json, else TEMP>\testteam),
    never in the checkout.

    Staging only: run-scenario.ps1 refuses any other host, and the tester's role file says so.

    The staging session (t-d20261006, 2026-10-06: the testers read an owner.json two days old -
    the 19:38 seed had written its file into the Claude desktop's redirected LOCALAPPDATA - and
    every step answered 401; two testers then re-seeded and revoked each other's sessions): the
    round runs scripts\staging\seed.ps1 ONCE before its first tester, in its own environment,
    which its testers inherit, and starts no tester (exit 1, a board note, no card) unless
    /v1/identity/sessions/current answers 200 with the seeded token. A job whose result is
    'environment' (run-scenario.ps1 exit 4), or whose failed steps all answered 401 while the
    session is no longer accepted, is 'environment' and forwards nothing.
#>
[CmdletBinding()]
param(
    [string]$Round = "",
    [string]$TeamRoot = "",
    [string]$OutRoot = "",
    [string]$PlanPath = "",
    [string]$ClaudePath = "claude",
    [string[]]$ClaudePrefixArguments = @(),
    # For the tests: the free memory in GB and whether the gate holds a heavy slot (1/0); -1 is "measure".
    [int]$AssumeFreeGb = -1,
    [int]$AssumeGateRunning = -1,
    [string]$SlotStore = "",
    [int]$RunMinutes = 60,
    [switch]$NoBoard,
    # For the tests: a stand-in for scripts\team\board.ps1.
    [string]$BoardScript = "",
    [switch]$Retest,
    # Only post the proof of the finished round -Round (its cards.json, plan and result files).
    [switch]$PostProof,
    # A deliberate round on a staging that does not serve main's tip.
    [switch]$AllowStaleStaging,
    # The sha staging must serve; empty = origin/main's tip, fetched now (the tests name it).
    [string]$MainSha = "",
    [string]$BaseUrl = "http://127.0.0.1:28001",
    [int]$AllowTestPort = 0,
    # The stand-in staging of the tests needs no session: no seed, no session check.
    [switch]$NoAuth,
    # The seed the round runs once before its first tester, and the session file it writes (the
    # one run-scenario.ps1 reads). For the tests: stand-ins.
    [string]$SeedScript = "",
    [string]$SessionFile = "",
    # The Cloud Core's queue, as in the cycle: the failures go there as create-only writes (the
    # feeder's Save-TeamFeedCreates). -QueueToken is the PATH of the token file.
    [string]$QueueUrl = "",
    [string]$QueueToken = ""
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamTestSlots.ps1")
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamFeed.ps1")
. (Join-Path $PSScriptRoot "TestTeam.ps1")
$apiStore = $null
if ($QueueUrl) {
    if (-not $QueueToken) { throw "-QueueUrl needs -QueueToken: the path of a file holding the token" }
    $apiStore = New-TeamApiStore -Url $QueueUrl -TokenFile $QueueToken
}

# Through -File a list arrives as one "a,b,c" string.
$ClaudePrefixArguments = @($ClaudePrefixArguments | ForEach-Object { [string]$_ -split ',' } | Where-Object { $_ })
if (-not $TeamRoot) { $TeamRoot = Join-Path $repoRoot "team" }
if (-not $Round) { $Round = "t" + [datetime]::UtcNow.ToString("yyyyMMddHHmm") }
if ($Round -notmatch '^[a-z0-9][a-z0-9-]{0,40}$') { throw "-Round: a-z, 0-9 ve '-' (en çok 41)" }
# The board allows RATE_PER_TASK_HOUR notes per task: the round posts under its id, a job under its card id.
$roundTask = Get-TestTeamBoardTask -Id $Round
$settingsPath = Join-Path $TeamRoot "cycle-settings.json"
if (-not $OutRoot) {
    $tempRoot = Read-TeamRunTempRoot -Path $settingsPath
    if (-not $tempRoot) { $tempRoot = $env:TEMP }
    $OutRoot = Join-Path $tempRoot "testteam"
}
if (-not (Test-TestTeamPathOutside -Path $OutRoot -Root $repoRoot)) {
    Write-Host "TUR REDDEDİLDİ: çıktı klasörü depo ağacının içinde ($OutRoot); run_temp_root altına yazılır"
    exit 2
}
$roundDir = Join-Path $OutRoot $Round
[void](New-Item -ItemType Directory -Force -Path $roundDir)
$cardsPath = Join-Path $roundDir "cards.json"
$queuePath = Join-Path $TeamRoot "queue.json"
$runScenario = Join-Path $PSScriptRoot "run-scenario.ps1"
$powershell = Join-Path $PSHOME "powershell.exe"

function Write-Json {
    param([string]$Path, $Document)
    [System.IO.File]::WriteAllText($Path, (ConvertTo-Json -InputObject $Document -Depth 10), (New-Object System.Text.UTF8Encoding($false)))
}

function Send-Note {
    # The board never stops the round (board.ps1 says UYARI and exits 0 when it cannot post).
    param([string]$Seat, [string]$Text, [string]$To = "", [string]$Task = $roundTask)
    if ($NoBoard) { return }
    $board = if ($BoardScript) { $BoardScript } else { Join-Path $repoRoot "scripts\team\board.ps1" }
    $arguments = @("-NoProfile", "-File", $board, "post", "-Seat", $Seat, "-Task", $Task, "-Kind", "bilgi", "-Text", $Text)
    if ($To) { $arguments += @("-To", $To) }
    try { & $powershell @arguments 2>&1 | ForEach-Object { Write-Host "  pano: $_" } } catch { Write-Host "  pano: UYARI: $($_.Exception.Message)" }
}

function Get-RoleModel {
    # The model policy (team/models.json): the test lead runs on the lead's model, a tester on the worker's.
    param([string]$Role)
    $path = Join-Path $TeamRoot "models.json"
    $defaults = Get-TeamModelDefaults
    $key = if ($Role -eq "test-lead") { "lead" } else { "worker" }
    $model = [string]$defaults.roles.$key
    if (Test-Path -LiteralPath $path) {
        try { $doc = Read-TeamJson -Path $path; $named = [string](Get-TeamProperty -InputObject $doc.roles -Name $key -Default ""); if (Test-TeamModelId -Model $named) { $model = $named } } catch { }
    }
    return $model
}

function Start-RoleProcess {
    param([string]$Role, [string]$Prompt, [string]$Seat, [string]$Task = $roundTask)
    # .claude/agents/ is the installed copy; scripts/testteam/roles/ is the source it is copied from.
    $roleFile = Join-Path $repoRoot ".claude\agents\$Role.md"
    if (-not (Test-Path -LiteralPath $roleFile)) { $roleFile = Join-Path $PSScriptRoot "roles\$Role.md" }
    $arguments = Get-TeamRunArguments -RoleFile $roleFile -Model (Get-RoleModel -Role $Role) -PrefixArguments $ClaudePrefixArguments
    $environment = @{ PAGENTOS_TEAM_SEAT = $Seat; PAGENTOS_TEAM_TASK = $Task }
    return (Start-TeamRun -FilePath $ClaudePath -Arguments $arguments -Prompt $Prompt -WorkingDirectory $repoRoot -Environment $environment)
}

if (-not $SeedScript) { $SeedScript = Join-Path $repoRoot "scripts\staging\seed.ps1" }
if (-not $SessionFile) { $SessionFile = Join-Path $env:LOCALAPPDATA "PagentOS\staging\owner.json" }

function Get-SessionStatus {
    # What staging answers to the seeded session now: "200", "401", ... or "hata: <why>".
    if (-not (Test-TestTeamStagingUrl -Url $BaseUrl -AllowTestPort $AllowTestPort)) { return "hata: $BaseUrl staging değil" }
    if (-not (Test-Path -LiteralPath $SessionFile)) { return "hata: oturum dosyası yok ($SessionFile)" }
    $token = ""
    try { $token = [string](Get-TeamProperty -InputObject (Read-TeamJson -Path $SessionFile) -Name "session_token" -Default "") } catch { return "hata: oturum dosyası okunamadı" }
    if (-not $token) { return "hata: oturum dosyasında belirteç yok" }
    Add-Type -AssemblyName System.Net.Http
    $client = New-Object System.Net.Http.HttpClient
    $client.Timeout = [TimeSpan]::FromSeconds(15)
    try {
        $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Get, ($BaseUrl.TrimEnd("/") + "/v1/identity/sessions/current"))
        [void]$request.Headers.TryAddWithoutValidation("Authorization", "Bearer $token")
        return [string][int]$client.SendAsync($request).GetAwaiter().GetResult().StatusCode
    }
    catch { return "hata: " + ($_.Exception.GetBaseException().Message -replace '\s+', ' ') }
    finally { $client.Dispose() }
}

function Test-AllFailed401 {
    # A result whose failed steps (at least one) all answered 401.
    param($Result)
    $failed = @(@(Get-TeamProperty -InputObject $Result -Name "steps" -Default @()) | Where-Object { -not [bool](Get-TeamProperty -InputObject $_ -Name "ok" -Default $true) })
    if (@($failed).Count -eq 0) { return $false }
    return (@($failed | Where-Object { [string](Get-TeamProperty -InputObject $_ -Name "actual" -Default "") -notmatch '^401\b' }).Count -eq 0)
}

function Read-Queue {
    if ($null -ne $apiStore) { return (Get-TeamQueueApi -Store $apiStore) }
    if (-not (Test-Path -LiteralPath $queuePath)) { return [pscustomobject]@{ version = 1; tasks = @() } }
    return (Read-TeamJson -Path $queuePath)
}

# ------------------------------------------------------------------------------ the proof

function Get-RoundProof {
    <# The round's proof per JARVIS roadmap row (proof-from-test-rounds-and-trials): a plan job
       names its row ('roadmap_row') when it has one, otherwise its 'why' (or, with neither, its
       family) is sent as written and the Cloud Core resolves the row (app.team.progress
       resolve_row: a wording that names no row is counted 'satır dışı', never a row). A card
       that passed is a passed scenario of that row, one that failed or broke a failed one. A
       card with no result file (the tester wrote none) or one that never ran proves nothing.
       $null, said, when the round's results do not name ONE staging sha. #>
    param($Document, [string]$Plan)
    $rowOf = @{}
    if ($Plan -and (Test-Path -LiteralPath $Plan)) {
        foreach ($job in @((Read-TeamJson -Path $Plan).jobs)) {
            $row = ([string](Get-TeamProperty -InputObject $job -Name "roadmap_row" -Default "")).Trim()
            if (-not $row) { $row = ([string](Get-TeamProperty -InputObject $job -Name "why" -Default "")).Trim() }
            if (-not $row) { $row = ([string]$job.family).Trim() }
            if ($row.Length -gt 300) { $row = $row.Substring(0, 300).Trim() }  # the Core's ROW_NAME_MAX
            if ($row) { $rowOf[[string]$job.family] = $row }
        }
    }
    $rows = [ordered]@{}
    $shas = @{}
    foreach ($card in @($Document.cards)) {
        $state = [string]$card.state
        if (@("passed", "failed", "broke") -notcontains $state) { continue }
        if (-not $rowOf.ContainsKey([string]$card.family)) { continue }
        $resultFile = Join-Path $roundDir "$($card.id).result.json"
        if (-not (Test-Path -LiteralPath $resultFile)) { continue }
        try { $result = Read-TeamJson -Path $resultFile } catch { continue }
        $sha = ([string](Get-TeamProperty -InputObject $result -Name "staging_sha" -Default "")).Trim().ToLowerInvariant()
        if ($sha) { $shas[$sha] = $true }
        $row = $rowOf[[string]$card.family]
        if (-not $rows.Contains($row)) { $rows[$row] = [ordered]@{ row = $row; passed = 0; failed = 0; families = @() } }
        if ($state -eq "passed") { $rows[$row].passed += 1 } else { $rows[$row].failed += 1 }
        if ($rows[$row].families -notcontains [string]$card.family) { $rows[$row].families += [string]$card.family }
    }
    if ($rows.Count -eq 0) { Write-Host "  kanıt: bu turda yol haritası satırına bağlı sonuç yok"; return $null }
    if ($shas.Count -ne 1) { Write-Host "  kanıt: sonuçlar tek bir staging sha'sı adlandırmıyor ($($shas.Count)); gönderilmedi"; return $null }
    return [ordered]@{
        round       = $Round
        staging_sha = @($shas.Keys)[0]
        at          = [datetime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
        rows        = @($rows.Values)
    }
}

function Send-RoundProof {
    <# Kept in the Cloud Core (POST /v1/team/queue/proof), never a document edit; the proof is
       also written beside the cards. A failure is said and never stops the round. #>
    param($Document, [string]$Plan)
    $proof = Get-RoundProof -Document $Document -Plan $Plan
    if ($null -eq $proof) { return }
    Write-Json -Path (Join-Path $roundDir "proof.json") -Document $proof
    if ($null -eq $apiStore) { Write-Host "  kanıt: -QueueUrl yok; yalnız $(Join-Path $roundDir 'proof.json')"; return }
    try {
        [void](Invoke-TeamApi -Store $apiStore -Method "POST" -Path "/v1/team/queue/proof" -Body $proof)
        Write-Host ("  kanıt Cloud Core'a yazıldı: {0} satır, staging {1}" -f @($proof.rows).Count, $proof.staging_sha.Substring(0, [Math]::Min(12, $proof.staging_sha.Length)))
    }
    catch { Write-Host "  kanıt yazılamadı: $($_.Exception.Message -replace '\s+', ' ')" }
}

if ($PostProof) {
    # The proof of a finished round, again (a round that ran before this step, or a store that was down).
    if (-not (Test-Path -LiteralPath $cardsPath)) { Write-Host "kanıt: $cardsPath yok"; exit 2 }
    $document = Read-TeamJson -Path $cardsPath
    $plan = if ($PlanPath) { $PlanPath } else { [string](Get-TeamProperty -InputObject $document -Name "plan" -Default "") }
    Send-RoundProof -Document $document -Plan $plan
    exit 0
}

# ------------------------------------------------------------------------------ the re-test

if ($Retest) {
    if (-not (Test-Path -LiteralPath $cardsPath)) { Write-Host "yeniden test: $cardsPath yok"; exit 2 }
    $document = Read-TeamJson -Path $cardsPath
    $queue = Read-Queue
    $byId = @{}
    foreach ($task in (Get-TeamTasks -Queue $queue)) { $byId[[string]$task.id] = $task }
    # The sha staging serves NOW: a fix is judged on the staging that carries it, never on the old one.
    $stagingNow = ""
    if (Test-TestTeamStagingUrl -Url $BaseUrl -AllowTestPort $AllowTestPort) {
        try {
            $health = Invoke-RestMethod -UseBasicParsing -Uri ($BaseUrl.TrimEnd("/") + "/v1/system/health") -TimeoutSec 15
            $release = Get-TeamProperty -InputObject $health -Name "release"
            if ($null -ne $release) { $stagingNow = [string](Get-TeamProperty -InputObject $release -Name "version" -Default "") }
        }
        catch { Write-Host "  staging sürümü okunamadı: $($_.Exception.Message -replace '\s+', ' ')" }
    }
    Write-Host ("staging şimdi: {0}" -f $(if ($stagingNow) { $stagingNow } else { "okunamadı" }))
    foreach ($card in @($document.cards)) {
        if (@("failed", "broke") -notcontains [string]$card.state) { continue }
        $forwarded = [string](Get-TeamProperty -InputObject $card -Name "forwarded_task" -Default "")
        if (-not $forwarded -or -not $byId.ContainsKey($forwarded)) { continue }
        $foundSha = [string](Get-TeamProperty -InputObject $card -Name "found_sha" -Default "")
        $decision = Get-TestTeamRetestDecision -TaskState ([string]$byId[$forwarded].state) -FoundSha $foundSha -StagingSha $stagingNow
        if (-not $decision.Due) {
            Write-Host "  $($card.id): yeniden test yok ($forwarded) - $($decision.Why); beklemede"
            continue
        }
        Set-TeamProperty -InputObject $card -Name "retested_sha" -Value $stagingNow
        $before = [string]$card.state
        Set-TestTeamCardState -Card $card -To "running"
        $arguments = @("-NoProfile", "-File", $runScenario, "-Scenario", [string]$card.scenario, "-Card", "$($card.id)-retest", "-OutDir", $roundDir, "-BaseUrl", $BaseUrl)
        if ($AllowTestPort -gt 0) { $arguments += @("-AllowTestPort", [string]$AllowTestPort) }
        if ($NoAuth) { $arguments += "-NoAuth" }
        & $powershell @arguments 2>&1 | ForEach-Object { Write-Host "    $_" }
        $code = $LASTEXITCODE
        if ($code -eq 0 -or $code -eq 3) {
            Set-TestTeamCardState -Card $card -To "passed"
            Write-Host "  $($card.id): yeniden test GEÇTİ - kapandı ($forwarded)"
        }
        elseif ($code -eq 4) {
            # A dead staging session judged nothing: the card stays as it was, not reopened.
            Set-TestTeamCardState -Card $card -To $before
            Write-Host "  $($card.id): yeniden test ORTAM - staging oturumu geçersiz; kart değişmedi ($forwarded)"
        }
        else {
            Set-TestTeamCardState -Card $card -To "failed"
            Set-TeamProperty -InputObject $card -Name "reopened" -Value (1 + [int](Get-TeamProperty -InputObject $card -Name "reopened" -Default 0))
            Write-Host "  $($card.id): yeniden test KALDI (çıkış $code) - yeniden açıldı ($forwarded)"
            Send-Note -Seat "test-lead" -Text "Test PY: $($card.id) yeniden açıldı - $forwarded düzeltmesinden sonra senaryo staging'de hâlâ kalıyor."
        }
    }
    Write-Json -Path $cardsPath -Document $document
    exit 0
}

# ------------------------------------------------------------------------------ staging is main's tip

if (-not (Test-TestTeamStagingUrl -Url $BaseUrl -AllowTestPort $AllowTestPort)) {
    Write-Host "TUR REDDEDİLDİ: $BaseUrl staging değil"
    exit 2
}
if ($AllowStaleStaging) { Write-Host "staging sürümü denetlenmedi (-AllowStaleStaging: eski bir sürümün bilerek denenmesi)" }
elseif (($NoAuth -or $AllowTestPort -gt 0) -and -not $MainSha) { Write-Host "staging sürümü denetlenmedi (testin yerine geçen staging'i, -MainSha yok)" }
else {
    $mainTip = $MainSha
    if (-not $mainTip) {
        $fetched = Invoke-TeamGit -WorkingDirectory $repoRoot -Arguments @("fetch", "--quiet", "origin", "refs/heads/main:refs/remotes/origin/main") -TimeoutSeconds 300
        if ($fetched.Success) {
            $parsed = Invoke-TeamGit -WorkingDirectory $repoRoot -Arguments @("rev-parse", "--verify", "--quiet", "refs/remotes/origin/main^{commit}")
            if ($parsed.Success) { $mainTip = ([string]$parsed.StdOut).Trim() }
        }
    }
    $why = ""
    if ($mainTip -notmatch '^[0-9a-f]{40}$') { $why = "origin/main'in ucu okunamadı; staging'in sürümü karşılaştırılamadı" }
    else {
        $servedSha = ""
        try {
            $health = Invoke-RestMethod -UseBasicParsing -Uri ($BaseUrl.TrimEnd("/") + "/v1/system/health") -TimeoutSec 15
            $release = Get-TeamProperty -InputObject $health -Name "release"
            if ($null -ne $release) { $servedSha = [string](Get-TeamProperty -InputObject $release -Name "version" -Default "") }
        }
        catch { Write-Host "  staging sağlığı okunamadı: $($_.Exception.Message -replace '\s+', ' ')" }
        if ($servedSha -ne $mainTip) {
            $shown = if ($servedSha) { $servedSha } else { "okunamadı" }
            $why = "staging eski: staging $shown, main $mainTip (önce scripts\staging\deploy.ps1 $mainTip; bilerek eski sürüm için -AllowStaleStaging)"
        }
    }
    if ($why) {
        # A refusal, not a failure: exit 4, said on the board; no plan, no card, no tester.
        Write-Host "TUR BAŞLAMADI: $why"
        Send-Note -Seat "test-lead" -Text ("Test PY: tur {0} başlamadı - {1}" -f $Round, $why)
        exit 4
    }
    Write-Host "staging main'in ucunda: $mainTip"
}

# ------------------------------------------------------------------------------ the input/output report

# What the round has so far: the report is written from these at its end, or when it dies.
$plan = $null
$cards = @()
$results = New-Object System.Collections.ArrayList
$added = New-Object System.Collections.ArrayList
$stagingSha = ""
$script:ioWritten = $false
$script:unfinished = ""

function Write-RoundIoReport {
    <# The owner's input/output report (test-round-io-report): test-raporu.md in the round folder,
       and POSTed to the Cloud Core's owner-only /v1/team/test-reports when -QueueUrl names it.
       Never stops the round: a report that cannot be written or sent is said. #>
    param([string]$Unfinished = "")
    $script:ioWritten = $true
    try {
        $jobs = if ($null -ne $plan) { @(Get-TeamProperty -InputObject $plan -Name "jobs" -Default @()) } else { @() }
        $sha = $stagingSha
        foreach ($result in $results) { if (-not $sha) { $sha = [string](Get-TeamProperty -InputObject $result -Name "staging_sha" -Default "") } }
        $io = Format-TestTeamRoundReport -Round $Round -StagingSha $sha -Jobs $jobs -Cards @($cards) -Results @($results.ToArray()) -Forwarded @($added) -Unfinished $Unfinished
        $file = Join-Path $roundDir "test-raporu.md"
        [System.IO.File]::WriteAllText($file, $io.Markdown, (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "girdi/çıktı raporu: $file"
        if ($null -eq $apiStore) { Write-Host "  Cloud Core adresi yok; rapor yalnız tur klasöründe"; return }
        $body = [pscustomobject]@{
            round       = $Round
            staging_sha = $sha
            counts      = $io.Counts
            unfinished  = $Unfinished
            text        = (Limit-TestTeamReportText -Text $io.Markdown)
        }
        try { [void](Invoke-TeamApi -Store $apiStore -Method "POST" -Path "/v1/team/test-reports" -Body $body); Write-Host "  rapor Cloud Core'a yazıldı (Ofis: Test raporları)" }
        catch { Write-Host "  rapor Cloud Core'a yazılamadı: $($_.Exception.Message -replace '\s+', ' ')" }
    }
    catch { Write-Host "  girdi/çıktı raporu yazılamadı: $($_.Exception.Message -replace '\s+', ' ')" }
}

# The round from its plan to its end. A round that dies on the way still writes the report of
# what it had, marked 'yarım kaldı: <why>' (finally below); the body is not indented so that the
# history of every line stays readable.
try {

# ------------------------------------------------------------------------------ the plan

if (-not $PlanPath) {
    $PlanPath = Join-Path $roundDir "plan.json"
    if (-not (Test-Path -LiteralPath $PlanPath)) {
        $prompt = @(
            "# Test round $Round - the test plan"
            ""
            "- plan_file: $PlanPath"
            "- scenarios: scripts/testteam/scenarios/"
            "- staging_api: http://127.0.0.1:28001"
            ""
            "Write the plan of this round as your role file says, and nothing else."
        ) -join "`n"
        $run = Start-RoleProcess -Role "test-lead" -Prompt $prompt -Seat "test-lead"
        $done = Wait-TeamRun -Run $run -Deadline ([datetime]::UtcNow.AddMinutes($RunMinutes))
        [System.IO.File]::WriteAllText((Join-Path $roundDir "test-lead-plan.log"), [string]$done.StdOut, (New-Object System.Text.UTF8Encoding($false)))
        if (-not (Test-Path -LiteralPath $PlanPath)) { Write-Host "Test PY plan yazmadı ($PlanPath); tur başlamadı"; $script:unfinished = "Test PY plan yazmadı"; exit 1 }
    }
}
$plan = Read-TeamJson -Path $PlanPath
$cards = @(New-TestTeamCards -Round $Round -Jobs @($plan.jobs))
$document = [pscustomobject]@{ round = $Round; plan = $PlanPath; cards = $cards }
Write-Json -Path $cardsPath -Document $document

# ------------------------------------------------------------------------------ the cap

$script:saidReasons = ""
function Get-RoundCap {
    # Measured NOW: the settings file, the free memory and the gate's test slot. Called before
    # every tester start, so a gate that takes the heavy slot mid-round holds the next job.
    $settings = Read-TeamCycleSettings -Path $settingsPath -Workers 4 -Inspectors 3 -Integrators 1
    $freeBytes = if ($AssumeFreeGb -ge 0) { [int64]$AssumeFreeGb * 1GB } else { [int64]((Get-CimInstance -ClassName Win32_OperatingSystem).FreePhysicalMemory) * 1KB }
    $gate = $false
    if ($AssumeGateRunning -ge 0) { $gate = ($AssumeGateRunning -eq 1) }
    else {
        $store = if ($SlotStore) { $SlotStore } else { Get-TestSlotDefaultStore }
        $gate = Test-TeamGateHoldsHeavy -Entries @(Get-TestSlotEntries -Store $store)
    }
    $cap = Get-TeamTestCap -Configured $settings.Testers -FreeBytes $freeBytes -FloorGb $settings.TestFloorGb -GateRunning $gate
    # A reason is said when it changes, not at every measurement.
    $said = (@($settings.Problems) + @($cap.Reasons)) -join " | "
    if ($said -ne $script:saidReasons) {
        foreach ($problem in @($settings.Problems)) { Write-Host "  ayar: $problem" }
        Write-Host ("test ekibi sınırı: {0} (ayar {1})" -f $cap.Cap, $cap.Configured)
        foreach ($reason in @($cap.Reasons)) {
            Write-Host "  $reason"
            Send-Note -Seat "test-lead" -Text "Test PY: $reason"
        }
        $script:saidReasons = $said
    }
    return $cap
}

$cap = Get-RoundCap
if ($script:saidReasons -eq "") { Write-Host ("test ekibi sınırı: {0} (ayar {1})" -f $cap.Cap, $cap.Configured) }
if ($cap.Cap -eq 0) {
    Write-Host "bu turda test çalışanı başlatılmadı; kartlar planned kaldı: $cardsPath"
    $script:unfinished = "test çalışanı başlatılmadı (sınır 0: $(@($cap.Reasons) -join '; '))"
    exit 0
}

# ------------------------------------------------------------------------------ the staging session

if (-not $NoAuth) {
    # ONE seed for the round, in this process' environment (the testers inherit it, so they read
    # the very file it writes); a tester never seeds (tester.md).
    Write-Host "staging oturumu açılıyor: $SeedScript"
    $ErrorActionPreference = "Continue"
    & $powershell -NoProfile -File $SeedScript -ApiBase $BaseUrl 2>&1 | ForEach-Object { Write-Host "  seed: $_" }
    $seedCode = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    $status = Get-SessionStatus
    if ($status -ne "200") {
        $why = "staging oturumu açılamadı: seed çıkış $seedCode, /v1/identity/sessions/current $status"
        Write-Host "TUR BAŞLAMADI: $why; test çalışanı başlatılmadı, kart açılmadı; kartlar planned: $cardsPath"
        Send-Note -Seat "test-lead" -Text "Test PY: tur $Round başlamadı - $why. Kart açılmadı."
        exit 1
    }
    Write-Host "staging oturumu geçerli (/v1/identity/sessions/current 200)"
}

# ------------------------------------------------------------------------------ the testers

$pending = New-Object System.Collections.Queue
foreach ($card in $cards) { $pending.Enqueue($card) }
$inFlight = New-Object System.Collections.ArrayList
$measuredAt = [datetime]::UtcNow
$finishedSince = $false
while ($pending.Count -gt 0 -or $inFlight.Count -gt 0) {
    if ($pending.Count -gt 0 -and ($finishedSince -or ([datetime]::UtcNow - $measuredAt).TotalSeconds -ge 5)) {
        $cap = Get-RoundCap
        $measuredAt = [datetime]::UtcNow
        $finishedSince = $false
    }
    if ($pending.Count -gt 0 -and $cap.Cap -eq 0 -and $inFlight.Count -eq 0) {
        Write-Host "  sınır 0: kalan $($pending.Count) kart planned kaldı ($(@($pending.ToArray() | ForEach-Object { $_.id }) -join ', '))"
        $pending.Clear()
        break
    }
    $busy = @($inFlight | ForEach-Object { $_.Card.tester })
    $waiting = $pending.Count
    for ($i = 0; $i -lt $waiting -and $inFlight.Count -lt $cap.Cap; $i++) {
        $card = $pending.Dequeue()
        if ($busy -contains $card.tester) { $pending.Enqueue($card); continue }
        $resultFile = Join-Path $roundDir "$($card.id).result.json"
        Set-TestTeamCardState -Card $card -To "running"
        Write-Json -Path $cardsPath -Document $document
        $run = Start-RoleProcess -Role "tester" -Prompt (New-TestTeamJobCard -Card $card -ResultFile $resultFile -Round $Round) -Seat $card.tester -Task (Get-TestTeamBoardTask -Id $card.id)
        [void]$inFlight.Add([pscustomobject]@{ Card = $card; Run = $run; ResultFile = $resultFile; Deadline = [datetime]::UtcNow.AddMinutes($RunMinutes) })
        $busy += $card.tester
        Write-Host "  $($card.tester) <- $($card.id) ($($card.family))"
        # The Ofis' Test odası (officeTestRoom.tsx) reads "iş: <job>" and "sonuç: <state> - <job> - ...".
        Send-Note -Seat $card.tester -Task (Get-TestTeamBoardTask -Id $card.id) -Text (Format-TestTeamSeatNote -Card $card)
    }
    $over = @($inFlight | Where-Object { Test-TeamRunOver -Run $_.Run -Deadline $_.Deadline })
    if (@($over).Count -eq 0) { Start-Sleep -Milliseconds 300; continue }
    foreach ($entry in $over) {
        $inFlight.Remove($entry)
        $finishedSince = $true
        $done = Wait-TeamRun -Run $entry.Run -Deadline $entry.Deadline
        [System.IO.File]::WriteAllText((Join-Path $roundDir "$($entry.Card.id).log"), [string]$done.StdOut, (New-Object System.Text.UTF8Encoding($false)))
        $state = "failed"
        $result = $null
        if (Test-Path -LiteralPath $entry.ResultFile) {
            try { $result = Read-TeamJson -Path $entry.ResultFile; $state = [string]$result.state } catch { $result = $null }
        }
        # A dead staging session is the environment, not staging's bug: run-scenario.ps1 says so
        # (exit 4), or a tester's own result failed only with 401 while the session is dead now.
        # Either way every failed step must be a 401: a 500 among them is forwarded as 'failed'.
        $environment = ""
        if ($null -ne $result -and $state -eq "environment" -and -not (Test-AllFailed401 -Result $result)) {
            $state = "failed"
            Set-TeamProperty -InputObject $result -Name "state" -Value "failed"
        }
        if ($null -ne $result -and $state -eq "environment") { $environment = [string](Get-TeamProperty -InputObject $result -Name "environment" -Default "sonuç 'environment'") }
        elseif ($null -ne $result -and $state -eq "failed" -and -not $NoAuth -and (Test-AllFailed401 -Result $result)) {
            $status = Get-SessionStatus
            if ($status -ne "200") { $environment = "adımlar 401, staging oturumu geçersiz (/v1/identity/sessions/current $status)" }
        }
        if ($environment) {
            # Not a move of the card table (scripts/lib/TeamQueue.ps1): the job never reached the
            # product, nothing is forwarded, and the next round deals new cards.
            $entry.Card.state = "environment"
            Set-TeamProperty -InputObject $entry.Card -Name "environment" -Value $environment
            Write-Json -Path $cardsPath -Document $document
            Write-Host "  $($entry.Card.tester) -> $($entry.Card.id): ortam - $environment; iletilmedi"
            Send-Note -Seat $entry.Card.tester -Text ("sonuç: environment - {0} ({1}) - staging oturumu geçersiz, iletilmedi" -f $entry.Card.family, $entry.Card.id)
            continue
        }
        if (@("passed", "failed", "broke") -notcontains $state) { $state = "failed" }
        if ($null -eq $result) {
            # A tester that wrote no result is a failure of the round, not of staging: it is said, not forwarded.
            Write-Host "  $($entry.Card.id): $($entry.Card.tester) sonuç yazmadı (çıkış $($done.ExitCode))"
        }
        Set-TestTeamCardState -Card $entry.Card -To $state
        if ($null -ne $result) { [void]$results.Add($result) }
        Write-Json -Path $cardsPath -Document $document
        Write-Host "  $($entry.Card.tester) -> $($entry.Card.id): $state"
        Send-Note -Seat $entry.Card.tester -Task (Get-TestTeamBoardTask -Id $entry.Card.id) -Text (Format-TestTeamSeatNote -Card $entry.Card -Result $result)
    }
}

# ------------------------------------------------------------------------------ forward

$failures = @()
foreach ($result in $results) { $failures += @(Get-TestTeamFailures -Result $result) }
$failures = @(Merge-TestTeamFailures -Failures $failures)
foreach ($result in $results) { $sha = [string](Get-TeamProperty -InputObject $result -Name "staging_sha" -Default ""); if ($sha) { $stagingSha = $sha } }
$queue = Read-Queue
$known = @{}
foreach ($task in (Get-TeamTasks -Queue $queue)) { $known[[string]$task.id] = $true }
foreach ($failure in $failures) {
    $task = ConvertTo-TestTeamFailureTask -Failure $failure -StagingSha $stagingSha -Round $Round
    foreach ($card in $cards) {
        if ($card.id -ne $failure.card) { continue }
        $card.forwarded_task = $task.id
        # The retest judges the fix only on a staging that no longer serves this sha.
        $card.found_sha = [string](Get-TeamProperty -InputObject $failure -Name "staging_sha" -Default "")
    }
    if ($known.ContainsKey($task.id)) { Write-Host "  $($task.id) kuyrukta zaten var; yeniden açılmadı"; continue }
    $known[$task.id] = $true
    [void]$added.Add($task)
}
if ($added.Count -gt 0) {
    $queue.tasks = @(@(Get-TeamTasks -Queue $queue) + @($added.ToArray()))
    # The store's own tasks are the store's to judge; the new cards are this round's.
    $judged = if ($null -ne $apiStore) { [pscustomobject]@{ version = 1; tasks = @($added.ToArray()) } } else { $queue }
    $problems = @(Test-TeamQueue -Queue $judged)
    if (@($problems).Count -gt 0) { throw "kuyruk protokolü bozulurdu: $($problems -join '; ')" }
    if ($null -ne $apiStore) {
        # Create-only: a task the store already has is never overwritten (somebody opened it first).
        $saved = Save-TeamFeedCreates -Store $apiStore -Tasks @($added.ToArray())
        foreach ($drop in @($saved.Dropped)) { Write-Host "  $($drop.Id) yazılmadı: $($drop.Why)" }
        if ($saved.Failed) { Write-Host "  depo yazmayı kesti: $($saved.Failed)" }
        $added = New-Object System.Collections.ArrayList
        foreach ($task in $queue.tasks) { if (@($saved.Written) -contains [string]$task.id) { [void]$added.Add($task) } }
    }
    else { Write-TeamJson -Path $queuePath -Document $queue }
    foreach ($task in $added) {
        Write-Host "  yazılım Proje Yöneticisi'ne iletildi: $($task.id)"
        Send-Note -Seat "test-lead" -To "lead" -Text ("Test PY: staging'de hata, kart {0} kuyrukta (proposed). {1}" -f $task.id, $task.title)
    }
}
Write-Json -Path $cardsPath -Document $document
Send-RoundProof -Document $document -Plan $PlanPath

# ------------------------------------------------------------------------------ the breaking point

$report = Format-TestTeamBreakingReport -Round $Round -Results @($results.ToArray()) -StagingSha $stagingSha
[System.IO.File]::WriteAllText((Join-Path $roundDir "kopma-noktasi.md"), $report.Markdown, (New-Object System.Text.UTF8Encoding($false)))
Send-Note -Seat "test-lead" -To $report.To -Text $report.Note
$counts = @($cards | Group-Object state | ForEach-Object { "$($_.Name) $($_.Count)" }) -join ", "
Write-Host "tur $Round bitti: $counts; iletilen $($added.Count); kopma raporu $(Join-Path $roundDir 'kopma-noktasi.md')"
Write-RoundIoReport
exit 0

}
catch {
    $script:unfinished = $_.Exception.Message -replace '\s+', ' '
    throw
}
finally {
    if (-not $script:ioWritten) {
        Write-RoundIoReport -Unfinished $(if ($script:unfinished) { $script:unfinished } else { "tur beklenmedik biçimde bitti" })
    }
}
