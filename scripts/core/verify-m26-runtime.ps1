<#
.SYNOPSIS
    M26 runtime verification against the DEPLOYED Cloud Core: the release markers, the UI
    contract version, one real executive run, one live weather answer, one briefing, and
    the news surface's honest refusal.

.DESCRIPTION
    Read-only except for the executive run and the weather query, both of which are the
    point: a gate row that says PROVEN_REAL has to name something that actually ran on
    production, not something a unit test proved could.

    Nothing here is destructive and nothing touches the owner's desktop: the executive
    shape chosen is (a) research -> synthesize -> document artifact, which runs entirely on
    the Cloud Core and the owner's already-enrolled browser worker; no mail is sent, no
    calendar is committed, no file of the owner's is written or deleted. The run is left in
    place rather than cancelled, because cancelling would run compensations over verified
    work for no reason.

    The owner session comes from the DPAPI-stored credential (PAGENTOS_OWNER_CREDENTIAL);
    nothing secret is printed.

.EXAMPLE
    .\scripts\core\verify-m26-runtime.ps1
    .\scripts\core\verify-m26-runtime.ps1 -SkipExecutiveRun   # markers + reads only
#>
[CmdletBinding()]
param(
    [string]$BrokerHost = "pagentos-core",
    [int]$ApiPort = 8001,
    [switch]$SkipExecutiveRun,
    [int]$RunPollSeconds = 240,
    [string]$EvidencePath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
. (Join-Path $repoRoot "scripts\lib\OwnerHarness.ps1")
$BaseUrl = "http://${BrokerHost}:$ApiPort"

$evidence = [ordered]@{
    checked_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    base_url   = $BaseUrl
}

function Write-Section { param([string]$Text) Write-Host "" ; Write-Host "== $Text" -ForegroundColor Cyan }

# ---------------------------------------------------------------- health / markers

Write-Section "release markers and contract versions"
$health = Invoke-JsonUtf8 -Uri "$BaseUrl/v1/system/health" -TimeoutSec 30
$evidence.release = [ordered]@{
    version          = [string]$health.release.version
    last_known_good  = [string]$health.release.last_known_good
    app_version      = [string]$health.release.app_version
    ui_state         = [int]$health.release.contracts.ui_state
    action           = [int]$health.release.contracts.action
    status           = [string]$health.status
    uptime_s         = [double]$health.release.uptime_s
}
$evidence.release | Format-List | Out-String | Write-Host

if ($evidence.release.ui_state -lt 11) {
    Write-Host "ui_state contract is $($evidence.release.ui_state); M26 publishes v11 (executive.run)" -ForegroundColor Yellow
}

# ------------------------------------------------------------------- owner session

$credential = $null
try { $credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL" } catch { $credential = $null }
if (-not $credential) { throw "PAGENTOS_OWNER_CREDENTIAL is not stored (DPAPI); every route below is owner-gated" }
$loginBody = @{ owner_credential = $credential; client_kind = "cli"; label = "verify-m26-runtime" } | ConvertTo-Json -Compress
$issued = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body $loginBody
$credential = $null; $loginBody = $null
$headers = @{ Authorization = "Bearer $([string]$issued.token)" }

function Get-Json { param([string]$Path) return Invoke-JsonUtf8 -Uri "$BaseUrl$Path" -Headers $headers -TimeoutSec 60 }
function Post-Json {
    param([string]$Path, $Body)
    $json = if ($null -eq $Body) { "{}" } else { ($Body | ConvertTo-Json -Depth 8 -Compress) }
    return Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl$Path" -Headers $headers -Body $json -TimeoutSec 120
}

# --------------------------------------------- live weather / briefing (NOT via REST)

# ADR-0091 decision 9: this track added NO REST routes on purpose - weather, location and
# the briefing are voice-tool surfaces only, and `apps/web`'s contract file was off-limits
# to the branch that built them. So there is nothing to GET here, and pretending otherwise
# would be a gate row citing a route that does not exist. They are verified instead by
# driving the REAL services inside the production api container (see
# `scripts/core/verify-m26-services-on-host.sh`, invoked separately over Tailscale SSH),
# which is what "live weather from the deployed Cloud Core" actually means.

# ---------------------------------------------------------------------- news sources

Write-Section "news sources (the identity it refuses to guess)"
try {
    $sources = Get-Json -Path "/v1/news/sources"
    $evidence.news_sources = @($sources.sources | ForEach-Object {
        [ordered]@{
            id              = [string]$_.news_source_id
            display_name    = [string]$_.display_name
            identity_status = [string]$_.identity_status
            channel_id      = [string]$_.channel_id
        }
    })
    $evidence.news_sources | ConvertTo-Json -Depth 4 | Write-Host
} catch {
    Write-Host "news routes unavailable: $($_.Exception.Message)" -ForegroundColor Yellow
}

# ------------------------------------------------------------------- executive run

if (-not $SkipExecutiveRun) {
    Write-Section "one real executive run on production (shape a: research -> synthesize -> document)"
    $directive = "Son uc gundeki yapay zeka gelismelerini arastir, bana etkisini cikar ve Word raporu hazirla."
    $started = Post-Json -Path "/v1/executive/runs" -Body @{ directive = $directive }
    $runId = [string]$started.run
    Write-Host "run $runId started; steps $($started.total)"

    $deadline = (Get-Date).AddSeconds($RunPollSeconds)
    $last = $null
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 10
        $last = Get-Json -Path "/v1/executive/runs/$runId"
        Write-Host ("  {0}  {1}/{2}  {3}" -f $last.state, $last.done, $last.total, $last.step)
        if (@("completed", "partial", "failed", "cancelled") -contains [string]$last.state) { break }
    }
    $evidence.executive_run = [ordered]@{
        run    = $runId
        state  = [string]$last.state
        done   = [int]$last.done
        total  = [int]$last.total
        steps  = @($last.steps | ForEach-Object {
            [ordered]@{
                id          = [string]$_.step
                kind        = [string]$_.kind
                state       = [string]$_.state
                error_class = [string]$_.error_class
            }
        })
    }
    $evidence.executive_run | ConvertTo-Json -Depth 5 | Write-Host
}

# ------------------------------------------------------------------------- evidence

if ($EvidencePath) {
    $dir = Split-Path -Parent $EvidencePath
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    ($evidence | ConvertTo-Json -Depth 8) | Out-File -FilePath $EvidencePath -Encoding utf8
    Write-Host ""
    Write-Host "evidence written to $EvidencePath"
}
