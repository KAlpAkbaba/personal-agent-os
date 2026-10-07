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
      4. The failures are deduplicated and each becomes a card for the software Proje
         Yöneticisi's triage - never the owner's (2026-10-06: 61 test cards sat in
         'awaiting_owner'): state 'proposed', reason and proposal 'Test PY bulgusu: ...', no area,
         so the cycle hands it to the PM's split (steps/expected/actual/scenario/screenshot/
         staging sha in its goal). A failing step (family + step + expected) a card of the store
         already holds - open, merged, folded or split - opens none; at most ten cards a round,
         the rest in one summary card (Select-TestTeamForwards).
      4a. The proof (proof-from-test-rounds-and-trials): per JARVIS roadmap row a plan job names
         ('roadmap_row'), the scenarios that passed and failed on staging, with the staging sha,
         POSTed to the Cloud Core (/v1/team/queue/proof) - the Ofis' 'staging'de kanıtlı'.
         -PostProof posts a finished round's proof again and does nothing else.
      5. The 'kopma noktası' report: <OutRoot>/<round>/kopma-noktasi.md, and one board note
         addressed to the Danışman's seat ('danisman') (never to the owner).

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

    The model of every run is the cycle's choice (Get-TeamRunModel, ADR-0214 addendum 7): the
    role's model, or - when team/limits.json or the cycle's status 'limits' has it limited - the
    next open one down the chain ('model düşürüldü'). A run that returns the usage limit is
    started once more on the next model; with every model limited the round stops in one line
    (team/plans/test-round-model-fallback-adr.md; t-r10070710, 2026-10-07).

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
. (Join-Path $repoRoot "scripts\lib\TeamIntegrate.ps1")
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
    param([string]$Seat, [string]$Text, [string]$To = "")
    if ($NoBoard) { return }
    $board = if ($BoardScript) { $BoardScript } else { Join-Path $repoRoot "scripts\team\board.ps1" }
    $arguments = @("-NoProfile", "-File", $board, "post", "-Seat", $Seat, "-Task", "test-team", "-Kind", "bilgi", "-Text", $Text)
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

function Get-ModelFallback {
    # team/models.json 'fallback' (default on): whether a limited model may be lowered, as in the cycle.
    $path = Join-Path $TeamRoot "models.json"
    if (Test-Path -LiteralPath $path) {
        try { $value = Get-TeamProperty -InputObject (Read-TeamJson -Path $path) -Name "fallback"; if ($value -is [bool]) { return $value } } catch { }
    }
    return $true
}

# The limits the cycle knows (ADR-0214 addendum 7): team/limits.json, and the live status'
# 'limits' (team/status.json, or the Cloud Core's in API mode). t-r10070710 (2026-10-07): the
# plan run was started on Fable while the cycle had it limited until 10-12, and the round died.
$limitedModels = Read-TeamLimitedModels -Path (Join-Path $TeamRoot "limits.json")
$script:saidLowered = @{}

function Add-StatusLimits {
    $status = $null
    try {
        if ($null -ne $apiStore) { $status = Invoke-TeamApi -Store $apiStore -Method "GET" -Path "/v1/team/queue/status" }
        elseif (Test-Path -LiteralPath (Join-Path $TeamRoot "status.json")) { $status = Read-TeamJson -Path (Join-Path $TeamRoot "status.json") }
    }
    catch { Write-Host "  döngü durumu okunamadı, limitler yalnız limits.json'dan: $($_.Exception.Message -replace '\s+', ' ')"; return }
    $limits = Get-TeamProperty -InputObject $status -Name "limits"
    if ($limits -isnot [System.Management.Automation.PSCustomObject]) { return }
    $chain = @(Get-TeamModelChain)
    $closes = [ordered]@{ fable = @($chain[0]); all = $chain }
    foreach ($name in $closes.Keys) {
        $entry = Get-TeamProperty -InputObject $limits -Name $name
        if ([string](Get-TeamProperty -InputObject $entry -Name "state" -Default "") -ne "limited") { continue }
        # Only a dated limit: an undated one in a status the cycle left long ago would bar the
        # model for ever. The run then says the limit itself and is retried once.
        $until = [string](Get-TeamProperty -InputObject $entry -Name "resets_at" -Default "")
        if (-not $until) { continue }
        foreach ($id in $closes[$name]) {
            if (-not (Test-TeamModelLimited -Limited $script:limitedModels -Model $id)) { $script:limitedModels[$id] = [pscustomobject]@{ until = $until } }
        }
    }
}

function Select-RoleModel {
    # The model a run of this role starts on now, as the cycle chooses it (Get-TeamRunModel): the
    # configured one, or the next open one down the chain. Model is $null: every model is limited.
    # A lowering is said once a round, here and from the test lead's seat.
    param([string]$Role)
    $configured = Get-RoleModel -Role $Role
    $pick = Get-TeamRunModel -Configured $configured -Limited $script:limitedModels -Fallback (Get-ModelFallback)
    if ($null -ne $pick.Model -and $pick.Lowered) {
        $line = "model düşürüldü: $configured -> $($pick.Model) (limit, $Role)"
        if (-not $script:saidLowered.ContainsKey($line)) {
            $script:saidLowered[$line] = $true
            Write-Host "  $line"
            Send-Note -Seat "test-lead" -Text "Test PY: $line"
        }
    }
    return $pick
}

function Format-NoModel {
    param($Pick, [string]$Role)
    $text = "modellerin hepsi limitte ($($Role): $(@($Pick.Candidates) -join ', '))"
    if ($Pick.ResetsAt) { $text += "; en erken sıfırlanma $($Pick.ResetsAt)" }
    return $text
}

function Start-RoleProcess {
    param([string]$Role, [string]$Prompt, [string]$Seat, [string]$Model)
    # .claude/agents/ is the installed copy; scripts/testteam/roles/ is the source it is copied from.
    $roleFile = Join-Path $repoRoot ".claude\agents\$Role.md"
    if (-not (Test-Path -LiteralPath $roleFile)) { $roleFile = Join-Path $PSScriptRoot "roles\$Role.md" }
    $arguments = Get-TeamRunArguments -RoleFile $roleFile -Model $Model -PrefixArguments $ClaudePrefixArguments
    $environment = @{ PAGENTOS_TEAM_SEAT = $Seat; PAGENTOS_TEAM_TASK = "test-team" }
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

# ------------------------------------------------------------------------------ the plan

$planRun = $false
if (-not $PlanPath) {
    $PlanPath = Join-Path $roundDir "plan.json"
    $planRun = -not (Test-Path -LiteralPath $PlanPath)
}

# Every model a role of this round may use is limited: nothing is started - no plan run, no
# tester, no seat shown working - and the round says so in one line.
Add-StatusLimits
foreach ($role in @($(if ($planRun) { "test-lead" }), "tester") | Where-Object { $_ }) {
    $pick = Get-TeamRunModel -Configured (Get-RoleModel -Role $role) -Limited $limitedModels -Fallback (Get-ModelFallback)
    if ($null -eq $pick.Model) {
        $why = Format-NoModel -Pick $pick -Role $role
        Write-Host "TUR BAŞLAMADI: $why; Test PY ve test çalışanı başlatılmadı"
        Send-Note -Seat "test-lead" -Text "Test PY: tur $Round başlamadı - $why."
        exit 1
    }
}

if ($planRun) {
    $prompt = @(
        "# Test round $Round - the test plan"
        ""
        "- plan_file: $PlanPath"
        "- scenarios: scripts/testteam/scenarios/"
        "- staging_api: http://127.0.0.1:28001"
        ""
        "Write the plan of this round as your role file says, and nothing else."
    ) -join "`n"
    # A run that comes back with the usage limit (t-r10070710: 'model_requires_usage_credits')
    # closes its model, and the plan is asked ONCE more on the next open model.
    $limitSaid = ""
    $planLog = Join-Path $roundDir "test-lead-plan.log"
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        $pick = Select-RoleModel -Role "test-lead"
        if ($null -eq $pick.Model) { $limitSaid = Format-NoModel -Pick $pick -Role "test-lead"; break }
        $run = Start-RoleProcess -Role "test-lead" -Prompt $prompt -Seat "test-lead" -Model $pick.Model
        $done = Wait-TeamRun -Run $run -Deadline ([datetime]::UtcNow.AddMinutes($RunMinutes))
        [System.IO.File]::AppendAllText($planLog, [string]$done.StdOut, (New-Object System.Text.UTF8Encoding($false)))
        if (Test-Path -LiteralPath $PlanPath) { break }
        $result = Read-TeamRunResult -StdOut ([string]$done.StdOut) -ExitCode ([int]$done.ExitCode) -StdErr ([string]$done.StdErr) -Model $pick.Model
        if (-not $result.UsageLimited) { break }
        $closed = @(Set-TeamModelClosed -Limited $limitedModels -Result $result -RunModel $pick.Model)
        $limitSaid = "kullanım limiti: $($pick.Model) ($($result.Why); kapandı: $($closed -join ', '))"
        Write-Host "  Test PY koşusu $limitSaid"
    }
    if (-not (Test-Path -LiteralPath $PlanPath)) {
        Write-Host ("Test PY plan yazmadı ($PlanPath); tur başlamadı" + $(if ($limitSaid) { " - $limitSaid" } else { "" }))
        if ($limitSaid) { Send-Note -Seat "test-lead" -Text "Test PY: tur $Round başlamadı - $limitSaid." }
        exit 1
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
$results = New-Object System.Collections.ArrayList
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
    # The model the next tester starts on, measured now: a limit a run met a moment ago counts.
    $testerModel = $null
    if ($pending.Count -gt 0) {
        $testerPick = Select-RoleModel -Role "tester"
        $testerModel = $testerPick.Model
        if ($null -eq $testerModel -and $inFlight.Count -eq 0) {
            $why = Format-NoModel -Pick $testerPick -Role "tester"
            Write-Host "TUR DURDU: $why; kalan $($pending.Count) kart planned kaldı ($(@($pending.ToArray() | ForEach-Object { $_.id }) -join ', '))"
            Send-Note -Seat "test-lead" -Text "Test PY: tur $Round durdu - $why. Kalan $($pending.Count) iş başlatılmadı."
            $pending.Clear()
            break
        }
    }
    $busy = @($inFlight | ForEach-Object { $_.Card.tester })
    $waiting = $pending.Count
    for ($i = 0; $i -lt $waiting -and $inFlight.Count -lt $cap.Cap -and $null -ne $testerModel; $i++) {
        $card = $pending.Dequeue()
        if ($busy -contains $card.tester) { $pending.Enqueue($card); continue }
        $resultFile = Join-Path $roundDir "$($card.id).result.json"
        Set-TestTeamCardState -Card $card -To "running"
        Write-Json -Path $cardsPath -Document $document
        $prompt = New-TestTeamJobCard -Card $card -ResultFile $resultFile -Round $Round
        $run = Start-RoleProcess -Role "tester" -Prompt $prompt -Seat $card.tester -Model $testerModel
        [void]$inFlight.Add([pscustomobject]@{ Card = $card; Run = $run; ResultFile = $resultFile; Deadline = [datetime]::UtcNow.AddMinutes($RunMinutes); Model = $testerModel; Attempt = 1; Prompt = $prompt })
        $busy += $card.tester
        Write-Host "  $($card.tester) <- $($card.id) ($($card.family))"
        # The Ofis' Test odası (officeTestRoom.tsx) reads "iş: <job>" and "sonuç: <state> - <job> - ...".
        Send-Note -Seat $card.tester -Text (Format-TestTeamSeatNote -Card $card)
    }
    $over = @($inFlight | Where-Object { Test-TeamRunOver -Run $_.Run -Deadline $_.Deadline })
    if (@($over).Count -eq 0) { Start-Sleep -Milliseconds 300; continue }
    foreach ($entry in $over) {
        $inFlight.Remove($entry)
        $finishedSince = $true
        $done = Wait-TeamRun -Run $entry.Run -Deadline $entry.Deadline
        [System.IO.File]::AppendAllText((Join-Path $roundDir "$($entry.Card.id).log"), [string]$done.StdOut, (New-Object System.Text.UTF8Encoding($false)))
        if (-not (Test-Path -LiteralPath $entry.ResultFile)) {
            # The usage limit, not the job: the model is closed for this round and the same job is
            # started ONCE more on the next open model; with none, the seat is told the job ended.
            $limit = Read-TeamRunResult -StdOut ([string]$done.StdOut) -ExitCode ([int]$done.ExitCode) -StdErr ([string]$done.StdErr) -Model $entry.Model
            if ($limit.UsageLimited) {
                $closed = @(Set-TeamModelClosed -Limited $limitedModels -Result $limit -RunModel $entry.Model)
                Write-Host "  $($entry.Card.tester) -> $($entry.Card.id): kullanım limiti $($entry.Model) ($($limit.Why); kapandı: $($closed -join ', '))"
                $retry = $null
                if ($entry.Attempt -lt 2) { $retry = (Select-RoleModel -Role "tester").Model }
                if ($null -ne $retry) {
                    $run = Start-RoleProcess -Role "tester" -Prompt $entry.Prompt -Seat $entry.Card.tester -Model $retry
                    [void]$inFlight.Add([pscustomobject]@{ Card = $entry.Card; Run = $run; ResultFile = $entry.ResultFile; Deadline = [datetime]::UtcNow.AddMinutes($RunMinutes); Model = $retry; Attempt = $entry.Attempt + 1; Prompt = $entry.Prompt })
                    Write-Host "  $($entry.Card.tester) <- $($entry.Card.id) yeniden, $retry ile"
                    continue
                }
                $entry.Card.state = "environment"
                Set-TeamProperty -InputObject $entry.Card -Name "environment" -Value "model limiti: $($entry.Model), $($entry.Attempt). deneme; iş staging'e ulaşmadı"
                Write-Json -Path $cardsPath -Document $document
                Write-Host "  $($entry.Card.tester) -> $($entry.Card.id): model limiti; iletilmedi"
                # 'error', not 'environment': the Ofis' Test odası ends a seat's job only on passed|failed|broke|error.
                Send-Note -Seat $entry.Card.tester -Text ("sonuç: error - {0} ({1}) - model limiti, iletilmedi" -f $entry.Card.family, $entry.Card.id)
                continue
            }
        }
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
        Send-Note -Seat $entry.Card.tester -Text (Format-TestTeamSeatNote -Card $entry.Card -Result $result)
    }
}

# ------------------------------------------------------------------------------ forward

$failures = @()
foreach ($result in $results) { $failures += @(Get-TestTeamFailures -Result $result) }
$failures = @(Merge-TestTeamFailures -Failures $failures)
$stagingSha = ""
foreach ($result in $results) { $sha = [string](Get-TeamProperty -InputObject $result -Name "staging_sha" -Default ""); if ($sha) { $stagingSha = $sha } }
$queue = Read-Queue
$known = @{}
foreach ($task in (Get-TeamTasks -Queue $queue)) { $known[[string]$task.id] = $true }
$added = New-Object System.Collections.ArrayList
function Set-Forwarded {
    param($Failure, [string]$TaskId)
    foreach ($card in $cards) {
        if ($card.id -ne $Failure.card) { continue }
        $card.forwarded_task = $TaskId
        # The retest judges the fix only on a staging that no longer serves this sha.
        $card.found_sha = [string](Get-TeamProperty -InputObject $Failure -Name "staging_sha" -Default "")
    }
}
# One card per failing step (family, step, expected) that no card of the store holds - open,
# merged, folded or split - at most ten; the rest in one summary card (test-findings-to-pm-not-owner).
$pick = Select-TestTeamForwards -Failures $failures -Tasks @(Get-TeamTasks -Queue $queue) -Round $Round -StagingSha $stagingSha
foreach ($held in @($pick.Held)) {
    Set-Forwarded -Failure $held.Failure -TaskId $held.By
    Write-Host "  $($held.Failure.family) / $($held.Failure.step): $($held.By) kartında zaten var; yeni kart açılmadı"
}
$planned = @(foreach ($failure in @($pick.Forward)) {
        $task = ConvertTo-TestTeamFailureTask -Failure $failure -StagingSha $stagingSha -Round $Round
        Set-Forwarded -Failure $failure -TaskId $task.id
        $task
    })
if ($null -ne $pick.Summary) {
    Write-Host "  sınır: $($script:TestTeamMaxForwards) kart; kalan hatalar tek özet kartta ($($pick.Summary.id))"
    foreach ($failure in @($pick.Summarised)) { Set-Forwarded -Failure $failure -TaskId $pick.Summary.id }
    $planned += $pick.Summary
}
foreach ($task in $planned) {
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
exit 0
