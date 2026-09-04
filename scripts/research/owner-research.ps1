<#
.SYNOPSIS
    Run one real research job on the Cloud Core, on the owner's own machine, and print the
    report: executive summary first, then the findings with why each matters, then the sources.

.DESCRIPTION
    EXECUTION ONLY (owner decision, 2026-09-04). This script never installs, stages, swaps or
    restarts anything. It first compares this checkout's browser-worker release with the one
    already installed:

      * identical            -> start immediately, no deployment;
      * different but able to serve this checkout's contracts -> start, and say so;
      * incompatible contract -> stop and print the ONE update command, without doing it.

    Discovery uses DuckDuckGo, the production default for Research: every search records
    requested_provider=duckduckgo, provider=duckduckgo, fallback=false. Google stays fully
    implemented behind -SearchProvider google (its owner-verification handoff is unchanged);
    it is simply not attempted first any more.

    The run itself is the Cloud Core's durable workflow on the device this machine registered:
    one persistent owner-session Chrome (one session per job, opened once, closed at the end),
    DuckDuckGo discovery, then real page opens and extraction for each candidate - never
    search snippets alone - with dedup, publication-date awareness and per-source provenance
    (title, URL, publisher, retrieved/published time, the device command that fetched it).

    The owner credential is typed into a masked prompt, exchanged for one session that is
    revoked at the end, and never printed. Nothing but ids, timings and the report text is
    written to -OutFile.

.EXAMPLE
    .\scripts\research\owner-research.ps1 -OutFile research-1.json
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    # Full base URL; overrides BrokerHost/ApiPort (the dev harness passes http://127.0.0.1:<port>).
    [string]$BaseUrl = "",
    # Default: the first real Research target (built from char codes below; this file stays ASCII).
    [string]$Topic = "",
    # Pick a device explicitly (id, name or a Turkish alias such as "ev"); default: automatic.
    [string]$Device = "",
    [int]$RecencyDays = 3,
    [int]$MaxSources = 12,
    # auto = the best synthesis provider configured on the Cloud Core (an OpenAI key makes it
    # real prose); deterministic = offline provenance summaries, still a real browser run.
    [ValidateSet("auto", "deterministic")][string]$Synthesis = "auto",
    # duckduckgo is the production default; google runs the optional Google path (with the
    # owner-verification handoff when -Interactive is given); auto is the ordered chain.
    [ValidateSet("duckduckgo", "google", "auto")][string]$SearchProvider = "duckduckgo",
    # Only meaningful for the Google path: pause on a verification page and wait for you.
    [switch]$Interactive,
    [ValidateRange(30, 1800)][int]$InteractiveWaitSec = 45,
    [int]$TimeoutSec = 900,
    [string]$OutFile = "",
    # Dev-chain harness only: take the owner session token from PAGENTOS_RESEARCH_TOKEN and
    # skip local device evidence (there is no installed agent in that run).
    [switch]$SessionTokenFromEnv,
    [switch]$SkipLocalEvidence,
    # The Cloud Core carries the research policy (which provider a default run uses).
    # auto (default): release it ONLY when the deployed one does not know the policy
    # this checkout expects; never: refuse instead of releasing; force: always release.
    [ValidateSet("auto", "never", "force")][string]$CloudCoreUpdate = "auto"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\BrowserRelease.ps1")
. (Join-Path $repoRoot "scripts\lib\RepoState.ps1")

if (-not $BaseUrl) { $BaseUrl = "http://${BrokerHost}:$ApiPort" }
# The first real Research target. Built from character codes so this script file stays
# pure ASCII: Windows PowerShell 5.1 reads a BOM-less file as ANSI and would mangle
# Turkish letters (the same convention as scripts\e2e-m13-research.ps1).
if (-not $Topic) {
    $Topic = "Son " + [char]0x00FC + [char]0x00E7 + " g" + [char]0x00FC + "ndeki yapay zek" + [char]0x00E2 + " ajanlar" + [char]0x0131 + "yla ilgili en " + [char]0x00F6 + "nemli geli" + [char]0x015F + "meleri ara" + [char]0x015F + "t" + [char]0x0131 + "r. En " + [char]0x00F6 + "nemli 5 geli" + [char]0x015F + "meyi se" + [char]0x00E7 + ", neden " + [char]0x00F6 + "nemli olduklar" + [char]0x0131 + "n" + [char]0x0131 + " a" + [char]0x00E7 + [char]0x0131 + "kla ve kaynaklar" + [char]0x0131 + "n" + [char]0x0131 + " ver."
}
$BaseUrl = $BaseUrl.TrimEnd('/')
$runId = "owner-research-" + (Get-Date -Format "yyyyMMdd-HHmmss")
$startedAt = (Get-Date).ToUniversalTime()

function Get-OptionalProperty {
    param($InputObject, [string]$Name)
    if ($null -eq $InputObject) { return $null }
    $prop = $InputObject.PSObject.Properties[$Name]
    if ($null -ne $prop) { return $prop.Value }
    return $null
}

function Get-ProfileChromeCount {
    if ($SkipLocalEvidence) { return -1 }
    $marker = "PagentOS\companion\browser\profile"
    return @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$marker*" -and $_.CommandLine -notlike "*--type=*" }).Count
}

$evidence = [ordered]@{
    run_id     = $runId
    started_at = $startedAt.ToString("o")
    cloud      = $BaseUrl
    topic      = $Topic
    policy     = [ordered]@{ search_provider = $SearchProvider; recency_days = $RecencyDays; max_sources = $MaxSources; synthesis = $Synthesis; interactive = [bool]$Interactive }
    deployment = $null
    task       = $null
    stages     = @()
    report     = $null
    sources    = @()
    local      = $null
    verdict    = "FAIL"
}

# ------------------------------------------------------------------ deployment decision

Write-Host "PagentOS owner research ($runId)"

# Local release blockers first: a Cloud Core release ships HEAD only, so a dirty working
# tree makes one impossible. Checked here, before the credential prompt and any other
# work, so an impossible release is visible immediately rather than at the release call
# (2026-09-04: the owner met that guard only after the whole preflight had run).
$releaseBlockers = Get-ReleaseBlockers -RepoRoot $repoRoot
if ($releaseBlockers.Blocked) {
    Write-Host "      working tree: $(@($releaseBlockers.Changes).Count) uncommitted change(s) - a Cloud Core release would be refused:" -ForegroundColor Yellow
    Write-ReleaseBlockers -Blockers $releaseBlockers
    if ($CloudCoreUpdate -ne "never") {
        Write-Host "      (this only matters if the deployed research policy turns out to be behind this checkout)" -ForegroundColor Yellow
    }
}
elseif ($releaseBlockers.Checked) {
    Write-Host "      working tree: clean (a Cloud Core release is possible if one is needed)"
}
if ($SkipLocalEvidence) {
    Write-Host "      release check skipped (-SkipLocalEvidence: dev chain, no installed agent)"
    $evidence.deployment = [ordered]@{ checked = $false; deployed = $false }
}
else {
    $releaseStatus = Test-AgentReleaseCurrent -CheckoutBrowserSource (Join-Path $repoRoot "services\browser")
    Write-AgentReleaseStatus -Status $releaseStatus
    $evidence.deployment = [ordered]@{
        checked                   = $true
        deployed                  = $false
        installed_release_current = $releaseStatus.Current
        contract_compatible       = $releaseStatus.ContractCompatible
        checkout_release          = $releaseStatus.Expected.Version
        installed_release         = $(if ($releaseStatus.Installed) { $releaseStatus.Installed.Version } else { $null })
        reasons                   = @($releaseStatus.Reasons)
    }
    if (-not $releaseStatus.ContractCompatible) {
        Write-Host ""
        Write-Host "the installed browser worker cannot serve this checkout:" -ForegroundColor Red
        foreach ($reason in $releaseStatus.Reasons) { Write-Host "  - $reason" -ForegroundColor Red }
        Write-Host "deploy it once (one UAC prompt), then run this command again:" -ForegroundColor Yellow
        Write-Host "  .\scripts\browser\real-browser-smoke.ps1 -AgentUpdate force -Mode lifecycle" -ForegroundColor Yellow
        throw "incompatible installed release; nothing was deployed and no research was started"
    }
}

# ------------------------------------------------------------------ Cloud Core policy

function Get-ExpectedPolicyVersion {
    <#  What this checkout's Cloud Core answers on /v1/research/policy.  #>
    $routes = Join-Path $repoRoot "services\api\app\research\routes.py"
    $match = [regex]::Match([System.IO.File]::ReadAllText($routes), '(?m)^RESEARCH_POLICY_VERSION\s*=\s*(\d+)')
    if (-not $match.Success) { throw "RESEARCH_POLICY_VERSION not found in $routes" }
    return [int]$match.Groups[1].Value
}

function Invoke-CloudCoreRelease {
    $release = Join-Path $repoRoot "scripts\cloud\release-cloud-core.ps1"
    Write-Host "      releasing the Cloud Core (transactional: build, migrate, recreate api only, health, rollback on failure)..." -ForegroundColor Yellow
    & $release
    if ($LASTEXITCODE -ne 0) { throw "the Cloud Core release exited $LASTEXITCODE; nothing was researched" }
}

# ------------------------------------------------------------------ owner session

$token = $null
$mintedId = $null
if ($SessionTokenFromEnv) {
    $token = [Environment]::GetEnvironmentVariable("PAGENTOS_RESEARCH_TOKEN")
    if (-not $token) { throw "-SessionTokenFromEnv given but PAGENTOS_RESEARCH_TOKEN is empty" }
}
else {
    $secure = Read-Host -Prompt "Cloud Owner Credential (input is hidden; exchanged for one session, then dropped)" -AsSecureString
    if ($secure.Length -eq 0) { throw "empty credential; nothing done" }
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $credential = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    try {
        $body = @{ owner_credential = $credential; client_kind = "cli"; label = $runId } | ConvertTo-Json -Compress
        $issued = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body $body
    }
    finally { $credential = $null; $body = $null }
    $token = [string]$issued.token
    $mintedId = [string](Get-OptionalProperty -InputObject $issued -Name "session_id")
}
$headers = @{ Authorization = "Bearer $token" }

function Get-Json { param([string]$Path) return Invoke-JsonUtf8 -Uri "$BaseUrl$Path" -Headers $headers -TimeoutSec 30 }

try {
    # ------------------------------------------------------------------ Cloud Core policy

    $expectedPolicy = Get-ExpectedPolicyVersion
    $policy = $null
    try { $policy = Get-Json "/v1/research/policy" } catch { $policy = $null }
    $policyVersion = if ($null -ne $policy) { [int](Get-OptionalProperty -InputObject $policy -Name "policy_version") } else { 0 }
    $deployedProvider = if ($null -ne $policy) { [string](Get-OptionalProperty -InputObject $policy -Name "search_provider") } else { "(unknown)" }
    Write-Host "      cloud policy: deployed version $policyVersion (provider $deployedProvider), this checkout expects $expectedPolicy"
    $cloudStale = ($policyVersion -lt $expectedPolicy)
    $releaseCloud = switch ($CloudCoreUpdate) {
        "force" { $true }
        "never" { $false }
        default { $cloudStale }
    }
    if ($cloudStale -and $CloudCoreUpdate -eq "never") {
        throw "the deployed Cloud Core does not know this checkout's research policy (version $policyVersion < $expectedPolicy), so a default run would not use $SearchProvider. Release it with .\scripts\cloud\release-cloud-core.ps1 or rerun without -CloudCoreUpdate never."
    }
    if ($releaseCloud -and $releaseBlockers.Blocked) {
        throw ("a Cloud Core release is required (deployed policy $policyVersion < $expectedPolicy) but the working tree has " +
               "$(@($releaseBlockers.Changes).Count) uncommitted change(s), and a release ships HEAD only: " +
               ($releaseBlockers.Changes -join "; ") +
               ". Commit or revert them, then rerun this command. Nothing was released and no research was started.")
    }
    if ($releaseCloud) {
        Write-Host "      the deployed Cloud Core predates this checkout's research policy: releasing it once" -ForegroundColor Yellow
        Invoke-CloudCoreRelease
        $policy = Get-Json "/v1/research/policy"
        $policyVersion = [int](Get-OptionalProperty -InputObject $policy -Name "policy_version")
        $deployedProvider = [string](Get-OptionalProperty -InputObject $policy -Name "search_provider")
        Write-Host "      cloud policy after the release: version $policyVersion, provider $deployedProvider"
        if ($policyVersion -lt $expectedPolicy) { throw "the Cloud Core still answers policy version $policyVersion after a release" }
    }
    else {
        Write-Host "      no Cloud Core release: the deployed policy is current"
    }
    $evidence.cloud_policy = [ordered]@{
        working_tree_blockers = @($releaseBlockers.Changes)
        expected_version = $expectedPolicy
        deployed_version = $policyVersion
        deployed_provider = $deployedProvider
        released = [bool]$releaseCloud
    }

    # ------------------------------------------------------------------ start the research

    $chromeBefore = Get-ProfileChromeCount
    $requestBody = [ordered]@{
        input           = $Topic
        recency_days    = $RecencyDays
        max_sources     = $MaxSources
        synthesis       = $Synthesis
        search_provider = $SearchProvider
        mode            = $(if ($Interactive) { "interactive" } else { "unattended" })
    }
    if ($Device) { $requestBody["target_device"] = $Device }
    if ($Interactive) { $requestBody["interactive_wait_s"] = $InteractiveWaitSec }

    Write-Host ""
    Write-Host "topic: $Topic"
    Write-Host "policy: provider=$SearchProvider recency=${RecencyDays}d max_sources=$MaxSources synthesis=$Synthesis mode=$($requestBody['mode'])"
    $created = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/research" -Headers $headers -Body ($requestBody | ConvertTo-Json -Depth 6 -Compress) -TimeoutSec 60
    $taskId = [string]$created.task_id
    $deviceSummary = Get-OptionalProperty -InputObject $created -Name "device"
    $evidence.task = [ordered]@{
        task_id     = $taskId
        workflow_id = [string](Get-OptionalProperty -InputObject $created -Name "workflow_id")
        device      = $deviceSummary
    }
    Write-Host "task $taskId on device $(if ($deviceSummary) { "$($deviceSummary.name) ($($deviceSummary.device_id))" } else { 'auto' })"
    Write-Host "a Chrome window will open on this PC and work through searches and pages; leave it alone."
    Write-Host ""

    # ------------------------------------------------------------------ follow it

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $lastStage = ""
    $detail = $null
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        $detail = Get-Json "/v1/research/$taskId"
        $stage = [string](Get-OptionalProperty -InputObject $detail -Name "stage")
        if ($stage -ne $lastStage) {
            $progress = Get-OptionalProperty -InputObject $detail -Name "progress"
            $counters = ""
            if ($null -ne $progress) {
                $counters = (($progress.PSObject.Properties | Where-Object { $_.Value -is [int] } | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join " ")
            }
            Write-Host ("  {0,-32} {1}" -f $stage, $counters)
            $evidence.stages += [ordered]@{ stage = $stage; at = (Get-Date).ToUniversalTime().ToString("o"); counters = $counters }
            $lastStage = $stage
        }
        if ($stage -eq "waiting_for_owner_verification") {
            Write-Host "      Google manuel dogrulama istiyor: acik Chrome penceresindeki sayfayi kendiniz tamamlayin." -ForegroundColor Yellow
        }
        if ($stage -in @("ready", "failed", "cancelled")) { break }
    }
    if ($null -eq $detail) { throw "no status was returned for task $taskId" }
    $finalStage = [string](Get-OptionalProperty -InputObject $detail -Name "stage")
    if ($finalStage -ne "ready") {
        $err = Get-OptionalProperty -InputObject $detail -Name "error"
        $evidence.error = $err
        throw "the research ended '$finalStage': $($err | ConvertTo-Json -Compress -Depth 4)"
    }

    # ------------------------------------------------------------------ the report

    $report = Get-OptionalProperty -InputObject $detail -Name "report"
    if ($null -eq $report) { $report = Get-Json "/v1/research/$taskId/report" }
    $findings = @(Get-OptionalProperty -InputObject $report -Name "findings")
    $sources = @(Get-OptionalProperty -InputObject $report -Name "sources")
    $whyList = @(Get-OptionalProperty -InputObject $report -Name "why_it_matters")

    Write-Host ""
    Write-Host "EXECUTIVE SUMMARY / YONETICI OZETI" -ForegroundColor Cyan
    Write-Host ("  " + [string](Get-OptionalProperty -InputObject $report -Name "executive_summary"))
    Write-Host ""
    Write-Host "FINDINGS / GELISMELER ($($findings.Count))" -ForegroundColor Cyan
    $n = 0
    foreach ($finding in $findings) {
        $n++
        Write-Host ("  {0}. {1}" -f $n, [string]$finding.title)
        Write-Host ("     summary: {0}" -f [string]$finding.summary)
        Write-Host ("     why    : {0}" -f [string]$finding.why_it_matters)
        $ids = @(Get-OptionalProperty -InputObject $finding -Name "evidence_ids")
        foreach ($id in $ids) {
            $source = $sources | Where-Object { [string]$_.id -eq [string]$id } | Select-Object -First 1
            if ($source) { Write-Host ("     source : {0} | {1} | {2}" -f [string]$source.title, [string]$source.url, [string]$source.publisher) }
        }
    }
    if (@($whyList).Count -gt 0) {
        Write-Host ""
        Write-Host "WHY IT MATTERS / NEDEN ONEMLI" -ForegroundColor Cyan
        foreach ($statement in $whyList) { Write-Host ("  - {0}" -f [string]$statement.text) }
    }
    # What the quality gate refused, so a short report can be read correctly: a thin answer
    # over a thin web is not the same failure as a gate that is too strict.
    $stats = Get-OptionalProperty -InputObject $report -Name "stats"
    $rejectedByReason = if ($null -ne $stats) { Get-OptionalProperty -InputObject $stats -Name "rejected_by_reason" } else { $null }
    $rejectedTotal = if ($null -ne $stats) { [int](Get-OptionalProperty -InputObject $stats -Name "rejected") } else { 0 }
    if ($rejectedTotal -gt 0) {
        Write-Host ""
        Write-Host "REFUSED PAGES / ELENEN SAYFALAR ($rejectedTotal)" -ForegroundColor Cyan
        if ($null -ne $rejectedByReason) {
            foreach ($property in $rejectedByReason.PSObject.Properties) {
                Write-Host ("  {0}: {1}" -f $property.Name, $property.Value)
            }
        }
    }

    Write-Host ""
    Write-Host "SOURCES / KAYNAKLAR ($($sources.Count))" -ForegroundColor Cyan
    foreach ($source in $sources) {
        Write-Host ("  [{0}] {1}" -f [string]$source.id, [string]$source.title)
        Write-Host ("       {0}" -f [string]$source.url)
        Write-Host ("       publisher={0} published={1} retrieved={2} class={3} command={4}" -f
            [string]$source.publisher, [string]$source.published_at, [string]$source.retrieved_at,
            [string]$source.source_class, [string]$source.command_id)
    }

    $evidence.report = [ordered]@{
        executive_summary = [string](Get-OptionalProperty -InputObject $report -Name "executive_summary")
        findings          = @($findings | ForEach-Object { [ordered]@{ id = [string]$_.id; title = [string]$_.title; summary = [string]$_.summary; why_it_matters = [string]$_.why_it_matters; importance = $_.importance; label = [string]$_.label; evidence_ids = @($_.evidence_ids) } })
        why_it_matters    = @($whyList | ForEach-Object { [string]$_.text })
        stats             = $stats
        rejected          = $rejectedTotal
        rejected_by_reason = $rejectedByReason
        artifact_id       = [string](Get-OptionalProperty -InputObject $detail -Name "artifact_id")
        memory_id         = [string](Get-OptionalProperty -InputObject $detail -Name "memory_id")
    }
    $evidence.sources = @($sources | ForEach-Object {
        [ordered]@{
            id = [string]$_.id; title = [string]$_.title; url = [string]$_.url; final_url = [string]$_.final_url
            publisher = [string]$_.publisher; published_at = [string]$_.published_at; retrieved_at = [string]$_.retrieved_at
            source_class = [string]$_.source_class; command_id = [string]$_.command_id
            device_id = [string]$_.device_id; syndicated_of = [string]$_.syndicated_of
        }
    })
    $evidence.events = @(Get-OptionalProperty -InputObject $detail -Name "events")

    # ------------------------------------------------------------------ closure evidence

    Start-Sleep -Seconds 3
    $chromeAfter = Get-ProfileChromeCount
    $evidence.local = [ordered]@{ pagentos_chrome_before = $chromeBefore; pagentos_chrome_after = $chromeAfter }
    if (-not $SkipLocalEvidence) {
        Write-Host ""
        Write-Host "session closure: PagentOS-profile Chrome processes before=$chromeBefore after=$chromeAfter"
        if ($chromeAfter -ne 0) { throw "the research left $chromeAfter PagentOS-profile Chrome process(es) running" }
    }

    # ------------------------------------------------------------------ policy assertions

    if ($deployedProvider -ne $SearchProvider -and $SearchProvider -eq "duckduckgo") {
        throw "the Cloud Core's default provider is '$deployedProvider', not '$SearchProvider'"
    }
    $distinctPublishers = @($sources | ForEach-Object { [string]$_.publisher } | Where-Object { $_ } | Select-Object -Unique).Count
    if (@($findings).Count -lt 3) { throw "only $(@($findings).Count) findings; the report needs at least 3" }
    if (@($sources).Count -lt 3) { throw "only $(@($sources).Count) sources were opened and extracted" }
    foreach ($source in $sources) {
        if (-not $source.title -or -not $source.url) { throw "a source is missing its title or URL" }
        if (-not ([string]$source.command_id)) { throw "source $($source.id) carries no device command id (no durable evidence)" }
    }
    $sourceIds = @($sources | ForEach-Object { [string]$_.id })
    foreach ($finding in $findings) {
        if (-not [string]$finding.why_it_matters) { throw "finding $($finding.id) does not say why it matters" }
        if (@($finding.evidence_ids).Count -lt 1) { throw "finding $($finding.id) cites no source" }
        foreach ($citedId in @($finding.evidence_ids)) {
            # A citation that points at nothing is worse than no citation: it reads as
            # attributed while nobody can check it.
            if ($sourceIds -notcontains [string]$citedId) {
                throw "finding $($finding.id) cites '$citedId', which is not one of the report's sources"
            }
        }
    }
    Write-Host ""
    Write-Host "checks: findings=$(@($findings).Count) sources=$(@($sources).Count) distinct publishers=$distinctPublishers refused=$rejectedTotal deployment=$(if ($evidence.deployment.deployed) { 'ran' } else { 'skipped' })"
    $evidence.verdict = "PASS"
}
finally {
    if ($OutFile) {
        $evidence.finished_at = (Get-Date).ToUniversalTime().ToString("o")
        $evidence | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $OutFile -Encoding utf8
        Write-Host "evidence written to $OutFile"
    }
    if ($mintedId -and $token) {
        try { [void](Invoke-JsonUtf8 -Method DELETE -Uri "$BaseUrl/v1/identity/sessions/$mintedId" -Headers $headers -TimeoutSec 15) } catch { }
    }
    $token = $null; $headers = $null
}

Write-Host ""
if ($evidence.verdict -eq "PASS") { Write-Host "OWNER RESEARCH: PASS" -ForegroundColor Green }
else { Write-Host "OWNER RESEARCH: FAIL" -ForegroundColor Red }
