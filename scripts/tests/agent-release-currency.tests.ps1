<#
.SYNOPSIS
    Deployment lifecycle separated from execution (owner decision, 2026-09-04):
    Test-AgentReleaseCurrent decides, from files alone, whether a deployment is needed.

.DESCRIPTION
    The owner's complaint: qualification commands ran the transactional installer every time,
    even when the installed worker was already the right release. Normal execution must use
    the installed worker with no installer, no staging/swap and no service restart; a
    deployment happens only when the release actually changed, the contract is incompatible,
    the owner asks, or a new release is being qualified.

    Encoded here (no elevation, no processes, no network — pure file comparison):
      * identical checkout and installed tree (source AND the venv copy that really runs)
        -> Current, no deployment;
      * a changed source file, a stale venv copy, a different version, a missing install or a
        missing venv package -> not Current, with the reason named;
      * an installed worker whose CONTRACT is behind the checkout is additionally
        ContractCompatible = false (that is the case execution must refuse, not just report);
      * a newer installed patch release with the same contracts stays contract-compatible;
      * the smoke and the research script must not hardcode an unconditional install.

    Run: powershell -NoProfile -File scripts\tests\agent-release-currency.tests.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\BrowserRelease.ps1")

$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-release-currency-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Passes++; Write-Host "  PASS  $Name" }
    catch { $script:Failures++; Write-Host "  FAIL  $Name" -ForegroundColor Red; Write-Host "        $($_.Exception.Message)" -ForegroundColor Red }
}
function Assert-True { param([bool]$Condition, [string]$Message) if (-not $Condition) { throw $Message } }
function Assert-False { param([bool]$Condition, [string]$Message) if ($Condition) { throw $Message } }
function Assert-Match { param([string[]]$Reasons, [string]$Pattern) if (-not (($Reasons -join '; ') -match $Pattern)) { throw "no reason matching '$Pattern' in: $($Reasons -join '; ')" } }

function New-Tree {
    <#  A browser source tree plus, optionally, its installed venv copy.  #>
    param([string]$Name, [string]$Version = "0.4.0", [int]$SearchContract = 3, [string]$Extra = "", [switch]$NoVenv)
    $root = Join-Path $script:Sandbox $Name
    $pkg = Join-Path $root "browser_agent"
    New-Item -ItemType Directory -Force -Path $pkg | Out-Null
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText((Join-Path $pkg "__init__.py"), "", $utf8)
    [IO.File]::WriteAllText((Join-Path $pkg "worker.py"), "WORKER_VERSION = `"$Version`"`nCONTRACTS: dict[str, int] = {`"browser.search`": $SearchContract}`n$Extra", $utf8)
    [IO.File]::WriteAllText((Join-Path $pkg "lifecycle.py"), "X = 1`n", $utf8)
    if (-not $NoVenv) {
        $site = Get-SitePackagesBrowserAgentDir -BrowserRoot $root
        New-Item -ItemType Directory -Force -Path $site | Out-Null
        Copy-Item (Join-Path $pkg "*.py") -Destination $site -Force
    }
    return $root
}

Write-Host "agent-release-currency tests"

Test-Case "identical checkout and install: current, contract compatible, no reasons" {
    $checkout = New-Tree "same-checkout"
    $installed = New-Tree "same-installed"
    $status = Test-AgentReleaseCurrent -CheckoutBrowserSource $checkout -InstalledBrowserRoot $installed
    Assert-True $status.Current "should be current: $($status.Reasons -join '; ')"
    Assert-True $status.ContractCompatible "contract compatible"
    Assert-True (@($status.Reasons).Count -eq 0) "no reasons"
}

Test-Case "a changed source file makes it not current" {
    $checkout = New-Tree "changed-checkout" -Extra "NEW = 1`n"
    $installed = New-Tree "changed-installed"
    $status = Test-AgentReleaseCurrent -CheckoutBrowserSource $checkout -InstalledBrowserRoot $installed
    Assert-False $status.Current "must not be current"
    Assert-Match $status.Reasons "source package digest"
}

Test-Case "a stale venv copy (the copy that actually runs) makes it not current" {
    $checkout = New-Tree "stale-checkout"
    $installed = New-Tree "stale-installed"
    $site = Get-SitePackagesBrowserAgentDir -BrowserRoot $installed
    [IO.File]::WriteAllText((Join-Path $site "worker.py"), "WORKER_VERSION = `"0.1.0`"`n", (New-Object System.Text.UTF8Encoding($false)))
    $status = Test-AgentReleaseCurrent -CheckoutBrowserSource $checkout -InstalledBrowserRoot $installed
    Assert-False $status.Current "must not be current"
    Assert-Match $status.Reasons "actually runs"
}

Test-Case "a different installed version is named, and an older contract is incompatible" {
    $checkout = New-Tree "ver-checkout" -Version "0.4.0" -SearchContract 3
    $installed = New-Tree "ver-installed" -Version "0.3.0" -SearchContract 2
    $status = Test-AgentReleaseCurrent -CheckoutBrowserSource $checkout -InstalledBrowserRoot $installed
    Assert-False $status.Current "not current"
    Assert-False $status.ContractCompatible "an older contract cannot serve this checkout"
    Assert-Match $status.Reasons "installed worker release 0.3.0"
    Assert-Match $status.Reasons "installed contract browser.search is 2"
}

Test-Case "a newer installed patch release with the same contracts stays contract compatible" {
    $checkout = New-Tree "newer-checkout" -Version "0.4.0" -SearchContract 3
    $installed = New-Tree "newer-installed" -Version "0.4.1" -SearchContract 3
    $status = Test-AgentReleaseCurrent -CheckoutBrowserSource $checkout -InstalledBrowserRoot $installed
    Assert-False $status.Current "versions differ"
    Assert-True $status.ContractCompatible "same contracts: execution may proceed"
}

Test-Case "nothing installed, or an install without its venv package, is reported not compatible" {
    $checkout = New-Tree "missing-checkout"
    $status = Test-AgentReleaseCurrent -CheckoutBrowserSource $checkout -InstalledBrowserRoot (Join-Path $script:Sandbox "does-not-exist")
    Assert-False $status.Current "nothing installed"
    Assert-False $status.ContractCompatible "nothing installed"
    Assert-Match $status.Reasons "no browser worker is installed"

    $installed = New-Tree "novenv-installed" -NoVenv
    $status2 = Test-AgentReleaseCurrent -CheckoutBrowserSource $checkout -InstalledBrowserRoot $installed
    Assert-False $status2.Current "no venv package"
    Assert-Match $status2.Reasons "no browser_agent package"
}

Test-Case "the smoke asks before installing and the research script never installs" {
    $smoke = Get-Content -LiteralPath (Join-Path $repoRoot "scripts\browser\real-browser-smoke.ps1") -Raw
    Assert-True ($smoke -match 'Test-AgentReleaseCurrent') "the smoke checks the installed release"
    Assert-True ($smoke -match '\$AgentUpdate') "the smoke has an -AgentUpdate policy"
    Assert-True ($smoke -match 'no deployment: the installed worker is this checkout') "the smoke says when it skips"

    $research = Get-Content -LiteralPath (Join-Path $repoRoot "scripts\research\owner-research.ps1") -Raw
    Assert-True ($research -match 'Test-AgentReleaseCurrent') "the research script checks the installed release"
    Assert-True ($research -notmatch 'install-device-service\.ps1"?\s*\)?\s*\r?\n?\s*(&|Start-Process)') "the research script never runs the installer"
    Assert-True ($research -match 'search_provider') "the research request carries the provider policy"
}

Test-Case "release blockers: tracked changes block, untracked evidence files never do" {
    . (Join-Path $repoRoot "scripts\lib\RepoState.ps1")
    $clean = Get-ReleaseBlockers -RepoRoot "." -GitRunner { param($Root) @() }
    Assert-False $clean.Blocked "a clean tree does not block"
    Assert-True $clean.Checked "checked"

    $dirty = Get-ReleaseBlockers -RepoRoot "." -GitRunner { param($Root) @(" M docs/X.md", "A  scripts/Y.ps1", "") }
    Assert-True $dirty.Blocked "tracked changes block a release"
    Assert-True (@($dirty.Changes).Count -eq 2) "blank porcelain lines are dropped"

    # --untracked-files=no is what the release guard uses: a run evidence JSON is untracked and
    # therefore never a blocker (2026-09-04: research-1.json must not block the next release)
    $runner = { param($Root) @() }
    Assert-False (Get-ReleaseBlockers -RepoRoot "." -GitRunner $runner).Blocked "untracked files are not blockers"

    $broken = Get-ReleaseBlockers -RepoRoot "." -GitRunner { param($Root) throw "git missing" }
    Assert-False $broken.Blocked "a git failure never invents a blocker"
    Assert-False $broken.Checked "and says it could not check"
}

Test-Case "the research script checks release blockers before the credential prompt and before releasing" {
    $research = Get-Content -LiteralPath (Join-Path $repoRoot (Join-Path "scripts" (Join-Path "research" "owner-research.ps1"))) -Raw
    Assert-True ($research -match 'Get-ReleaseBlockers') "the research script checks blockers"
    $blockerAt = $research.IndexOf('$releaseBlockers = Get-ReleaseBlockers')
    $promptAt = $research.IndexOf('Read-Host -Prompt "Cloud Owner Credential')
    # the CALL, not the function definition that precedes it
    $releaseAt = $research.LastIndexOf('Invoke-CloudCoreRelease')
    Assert-True ($blockerAt -gt 0 -and $promptAt -gt 0 -and $releaseAt -gt 0) "all three points exist"
    Assert-True ($blockerAt -lt $promptAt) "blockers are checked before the credential prompt"
    Assert-True ($research -match 'releaseCloud -and \$releaseBlockers\.Blocked') "a needed release with a dirty tree is refused"
    $refusalAt = $research.IndexOf('$releaseCloud -and $releaseBlockers.Blocked')
    Assert-True ($refusalAt -lt $releaseAt) "the refusal comes before the release call"
}

Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "$($script:Passes) passed, $($script:Failures) failed"
exit $script:Failures
