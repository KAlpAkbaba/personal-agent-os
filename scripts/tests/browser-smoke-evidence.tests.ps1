<#
.SYNOPSIS
    The owner browser smoke's payload/evidence builders (scripts\lib\BrowserSmokeEvidence.ps1)
    and the smoke's own construction rules, under Windows PowerShell 5.1, no device needed.

.DESCRIPTION
    Real owner incident 2026-09-04: after a 600 s owner-verification timeout the owner chose to
    fall back and the smoke died with "Item has already been added. Key in dictionary:
    'interstitial'" - a hashtable `+` merge of two payloads that both carried `interstitial`.
    Encoded here:
      - payloads are built fresh with one `interstitial` key; handoff and fallback payloads for
        the same query are independent objects;
      - the smoke source never adds hashtables and never re-adds `interstitial`;
      - the evidence projection of every handoff outcome (pending, cleared, timeout-fallback,
        repeat-fallback, unattended fallback) has fixed keys, the interstitial appears exactly
        once (search.verification), and the JSON round-trip has no duplicate key;
      - the duplicate-key checker itself catches a duplicate.

    Run: powershell -NoProfile -File scripts\tests\browser-smoke-evidence.tests.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\BrowserSmokeEvidence.ps1")

$script:Failures = 0
$script:Passes = 0

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Passes++; Write-Host "  PASS  $Name" }
    catch { $script:Failures++; Write-Host "  FAIL  $Name" -ForegroundColor Red; Write-Host "        $($_.Exception.Message)" -ForegroundColor Red }
}
function Assert-True { param([bool]$Condition, [string]$Message) if (-not $Condition) { throw $Message } }
function Assert-Equal { param($Expected, $Actual, [string]$Message) if ($Expected -ne $Actual) { throw "$Message (expected '$Expected', got '$Actual')" } }

function New-WorkerResult {
    param([string]$Path, [string]$State = "ok", [string]$Provider = "google", [bool]$Fallback = $false, [string]$Reason = $null, [hashtable]$Verification = $null, [int]$Handoffs = 0)
    $r = [ordered]@{
        schema_version = 3; engine = $Provider; requested_provider = "google"; provider = $Provider
        fallback = $Fallback; fallback_reason = $Reason; query = "yapay zeka"; result_count = 1
        locale = "tr-TR"; attempts = @(@{ provider = "google"; outcome = "ok"; detail = "1 results" })
        results = @(@{ rank = 1; title = "t"; url = "https://example.com/1"; snippet = "s" })
        page_kind = "ok"; path = $Path; state = $State; mode = "interactive"; verification_handoffs = $Handoffs
        verification = $(if ($Verification) { $Verification } else { @{ handoffs = $Handoffs; outcome = $null; interstitial = $null; verification_url = $null } })
    }
    return ($r | ConvertTo-Json -Depth 6 | ConvertFrom-Json)
}

Write-Host "browser-smoke-evidence tests"

Test-Case "search payloads are built fresh with exactly one interstitial key; handoff and fallback payloads are independent" {
    $a = New-BrowserSearchPayload -SessionId "s" -Query "q" -Mode interactive -Interstitial handoff
    $b = New-BrowserSearchPayload -SessionId "s" -Query "q" -Mode interactive -Interstitial fallback
    Assert-Equal "handoff" $a.interstitial "a"
    Assert-Equal "fallback" $b.interstitial "b"
    Assert-True (-not [object]::ReferenceEquals($a, $b)) "distinct objects"
    Assert-Equal 1 @($a.Keys | Where-Object { $_ -eq "interstitial" }).Count "one interstitial key"
    Assert-Equal "s,q,auto,8,interactive,handoff" (@($a.session_id, $a.query, $a.engine, $a.max_results, $a.mode, $a.interstitial) -join ",") "fixed keys"
    $b.interstitial = "handoff"   # mutating one never touches the other
    Assert-Equal "handoff" $a.interstitial "a unchanged"
}

Test-Case "the smoke never adds hashtables and never re-adds interstitial" {
    $smoke = Get-Content -LiteralPath (Join-Path $repoRoot "scripts\browser\real-browser-smoke.ps1") -Raw
    Assert-True ($smoke -notmatch '\+\s*@\{\s*interstitial') "no `+ @{ interstitial` merge"
    Assert-True ($smoke -notmatch '@\{\}\s*\+\s*\$searchPayload') "no hashtable addition of the payload"
    Assert-True ($smoke -match 'New-BrowserSearchPayload .* -Interstitial "fallback"') "fallback payload comes from the builder"
    Assert-True ($smoke -match 'ConvertTo-SearchEvidence -Result \$sr') "evidence comes from the projection"
    Assert-True ($smoke -match 'Test-EvidenceKeysUnique') "duplicate keys are checked on the JSON"
}

Test-Case "handoff evidence has fixed owner-side keys and no interstitial kind" {
    $h = New-HandoffEvidence -Mode interactive -TimeoutSec 45
    Assert-Equal "mode,occurred,timeout_s,waited_s,cleared,resumed,repeat,timed_out,owner_decision,fallback_issued" (($h.Keys) -join ",") "keys"
    Assert-True (-not $h.Contains("interstitial") -and -not $h.Contains("page_kind")) "interstitial lives only in search.verification"
}

$scenarios = @(
    @{ name = "pending";           result = (New-WorkerResult -Path "handoff_pending" -State "waiting_for_owner_verification" -Provider $null -Handoffs 1 -Verification @{ handoffs = 1; outcome = "pending"; interstitial = "captcha"; verification_url = "https://www.google.com/sorry/index" }) }
    @{ name = "cleared";           result = (New-WorkerResult -Path "handoff_cleared" -Handoffs 1 -Verification @{ handoffs = 1; outcome = "cleared"; interstitial = "captcha"; verification_url = "https://www.google.com/sorry/index" }) }
    @{ name = "timeout-fallback";  result = (New-WorkerResult -Path "handoff_timeout_fallback" -Provider "duckduckgo" -Fallback $true -Reason "google:verification_timeout" -Handoffs 1 -Verification @{ handoffs = 1; outcome = "timeout"; interstitial = "captcha"; verification_url = "https://www.google.com/sorry/index" }) }
    @{ name = "repeat-fallback";   result = (New-WorkerResult -Path "handoff_repeat_fallback" -Provider "duckduckgo" -Fallback $true -Reason "google:interstitial_after_verification" -Handoffs 1 -Verification @{ handoffs = 1; outcome = "repeat"; interstitial = "consent"; verification_url = "https://consent.google.com/m" }) }
    @{ name = "unattended-fallback"; result = (New-WorkerResult -Path "fallback" -Provider "duckduckgo" -Fallback $true -Reason "google:captcha") }
)
foreach ($scenario in $scenarios) {
    Test-Case "evidence projection '$($scenario.name)': fixed keys, interstitial exactly once, JSON has no duplicate key" {
        $ev = ConvertTo-SearchEvidence -Result $scenario.result
        Assert-Equal "schema_version,requested_provider,provider,fallback,fallback_reason,query,result_count,locale,state,path,mode,verification,attempts,results" (($ev.Keys) -join ",") "keys"
        $json = $ev | ConvertTo-Json -Depth 8
        Assert-True (Test-EvidenceKeysUnique -Json $json) "unique keys"
        $occurrences = ([regex]::Matches($json, '"interstitial"\s*:')).Count
        Assert-Equal 1 $occurrences "interstitial key appears exactly once"
        if ($scenario.name -eq "timeout-fallback") {
            Assert-Equal "google:verification_timeout" $ev.fallback_reason "reason"
            Assert-Equal "timeout" $ev.verification.outcome "outcome"
            Assert-Equal "duckduckgo" $ev.provider "provider"
            Assert-Equal "google" $ev.requested_provider "requested"
            Assert-Equal $true $ev.fallback "fallback"
        }
        if ($scenario.name -eq "unattended-fallback") { Assert-True ($null -eq $ev.verification.outcome) "no handoff outcome" }
    }
}

Test-Case "the duplicate-key checker catches a duplicate at one level and accepts nesting" {
    Assert-True (Test-EvidenceKeysUnique -Json '{"a":1,"b":{"a":2,"c":[{"a":3},{"a":4}]}}') "same key at different levels is fine"
    $threw = $false
    try { [void](Test-EvidenceKeysUnique -Json '{"interstitial":"captcha","x":{"y":1},"interstitial":"consent"}') } catch { $threw = ($_.Exception.Message -like "*duplicate evidence key 'interstitial'*") }
    Assert-True $threw "duplicate detected"
}

Write-Host ""
Write-Host "$($script:Passes) passed, $($script:Failures) failed"
exit $script:Failures
