<#
.SYNOPSIS
    The owner's command line over an EvolutionOpportunity's lifecycle on the real Cloud
    Core: list, show, advance one transition with its evidence refs, approve.

.DESCRIPTION
    M18.4 gap 5 (owner directive 2026-09-07): the self-evolution lifecycle proven on ONE real,
    production-originated opportunity - opportunity -> candidate -> patch -> regression ->
    gates -> shadow/canary -> promotion -> runtime verification - with every step a REST
    transition carrying what happened (commit, CI run, evidence file) as its refs, so the
    Cockpit, the ledger and `/v1/evolution/opportunities/{id}` tell the same story.

    The owner session comes from the DPAPI-stored credential (PAGENTOS_OWNER_CREDENTIAL);
    nothing secret is printed. Production-side targets (qualifying, deploying, verifying,
    live, ...) mint production authority from that session on the server; approval is its
    own endpoint (POST /approve) and the only way into owner_approved (ADR-0053/0055).

.EXAMPLE
    .\scripts\core\evolution-advance.ps1 -List -Match "device-presence"
    .\scripts\core\evolution-advance.ps1 -Show 57674b6a-4a8d-4e8a-8ac7-5d0187aecc1e
    .\scripts\core\evolution-advance.ps1 -Advance <id> -Target researching -Actor owner -Reason "..." -WorkspaceRef "git:main@552a318"
    .\scripts\core\evolution-advance.ps1 -Approve <id> -Note "..."
#>
[CmdletBinding(DefaultParameterSetName = "List")]
param(
    [string]$BrokerHost = "100.90.158.26",
    [int]$ApiPort = 8001,
    [Parameter(ParameterSetName = "List")][switch]$List,
    [Parameter(ParameterSetName = "List")][string]$Match = "",
    [Parameter(ParameterSetName = "List")][string]$Status = "",
    [Parameter(ParameterSetName = "Show", Mandatory = $true)][string]$Show,
    [Parameter(ParameterSetName = "Advance", Mandatory = $true)][string]$Advance,
    [Parameter(ParameterSetName = "Advance", Mandatory = $true)][string]$Target,
    [Parameter(ParameterSetName = "Advance")][ValidateSet("owner", "system", "lab")][string]$Actor = "owner",
    [Parameter(ParameterSetName = "Advance")][string]$Reason = "",
    [Parameter(ParameterSetName = "Advance")][string]$WorkspaceRef = "",
    [Parameter(ParameterSetName = "Advance")][string]$CandidateRef = "",
    [Parameter(ParameterSetName = "Approve", Mandatory = $true)][string]$Approve,
    [Parameter(ParameterSetName = "Approve")][string]$Note = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\SecretStore.ps1")
. (Join-Path $repoRoot "scripts\lib\OwnerHarness.ps1")
$BaseUrl = "http://${BrokerHost}:$ApiPort"

$credential = $null
try { $credential = Get-StoredSecretValue -Name "PAGENTOS_OWNER_CREDENTIAL" } catch { $credential = $null }
if (-not $credential) { throw "PAGENTOS_OWNER_CREDENTIAL is not stored (DPAPI); the lifecycle routes are owner-gated" }
$loginBody = @{ owner_credential = $credential; client_kind = "cli"; label = "evolution-advance" } | ConvertTo-Json -Compress
$issued = Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl/v1/identity/sessions" -Body $loginBody
$credential = $null; $loginBody = $null
$token = [string]$issued.token
$headers = @{ Authorization = "Bearer $token" }

function Get-Json { param([string]$Path) return Invoke-JsonUtf8 -Uri "$BaseUrl$Path" -Headers $headers -TimeoutSec 30 }
function Post-Json { param([string]$Path, $Body) $json = if ($null -eq $Body) { "{}" } else { ($Body | ConvertTo-Json -Depth 8 -Compress) }; return Invoke-JsonUtf8 -Method POST -Uri "$BaseUrl$Path" -Headers $headers -Body $json -TimeoutSec 60 }

function Show-Opportunity {
    param($Doc)
    Write-Host (($Doc | ConvertTo-Json -Depth 10))
}

try {
    switch ($PSCmdlet.ParameterSetName) {
        "List" {
            $query = "/v1/evolution/opportunities?limit=200"
            if ($Status) { $query += "&status=$Status" }
            $doc = Get-Json $query
            $rows = Get-ArrayProperty -InputObject $doc -Name "opportunities"
            foreach ($row in $rows) {
                if ($null -eq $row) { continue }
                $title = [string](Get-OptionalProperty -InputObject $row -Name "title")
                $key = [string](Get-OptionalProperty -InputObject $row -Name "source_ref")
                $detail = Get-OptionalProperty -InputObject $row -Name "detail"
                $priority = if ($null -ne $detail) { [string](Get-OptionalProperty -InputObject $detail -Name "priority") } else { "" }
                $class = if ($null -ne $detail) { [string](Get-OptionalProperty -InputObject $detail -Name "promotion_class") } else { "" }
                $line = "$(Get-OptionalProperty -InputObject $row -Name 'opportunity_id')  $(Get-OptionalProperty -InputObject $row -Name 'status')  $priority  $class  $key  $title"
                if (-not $Match -or $line -match [regex]::Escape($Match)) { Write-Host $line }
            }
        }
        "Show" {
            Show-Opportunity (Get-Json "/v1/evolution/opportunities/$Show")
        }
        "Advance" {
            $body = @{ target = $Target; actor = $Actor }
            if ($Reason) { $body.reason = $Reason }
            if ($WorkspaceRef) { $body.workspace_ref = $WorkspaceRef }
            if ($CandidateRef) { $body.candidate_ref = $CandidateRef }
            $result = Post-Json "/v1/evolution/opportunities/$Advance/advance" $body
            Write-Host "advanced -> $(Get-OptionalProperty -InputObject $result -Name 'status') (updated $(Get-OptionalProperty -InputObject $result -Name 'updated_at'))"
        }
        "Approve" {
            $body = @{}
            if ($Note) { $body.note = $Note }
            $result = Post-Json "/v1/evolution/opportunities/$Approve/approve" $body
            Write-Host "approved -> $(Get-OptionalProperty -InputObject $result -Name 'status')"
        }
    }
}
finally {
    $token = $null; $headers = $null
}
