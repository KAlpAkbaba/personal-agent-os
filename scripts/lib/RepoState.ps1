<#
.SYNOPSIS
    Local blockers that would stop a Cloud Core release, checked BEFORE any expensive work.

.DESCRIPTION
    A release ships HEAD only (scripts\cloud\release-cloud-core.ps1 refuses a dirty working
    tree, and that guard is correct: it is what keeps the deployed tree equal to a commit).
    The owner hit it late, on 2026-09-04, after the research command had already checked the
    installed worker release and asked for the Owner Credential - the impossible release was
    only discovered at the moment it was attempted.

    Get-ReleaseBlockers answers the same question the release script asks, cheaply and with
    no side effect, so a caller can say so at startup and refuse the moment a release turns
    out to be required. `git status --porcelain --untracked-files=no` is the exact command the
    release guard uses: untracked files (a run's evidence JSON, a scratch file) never block a
    release and must never be reported as blockers.

    The git invocation is injectable (-GitRunner) so the logic is testable without a repo.
#>

Set-StrictMode -Version Latest

function Get-GitPath {
    <#  Absolute git, resolved the way the other scripts resolve tools (PATH is unreliable).  #>
    [CmdletBinding()]
    param()
    $command = Get-Command git -ErrorAction SilentlyContinue
    if ($command -and $command.Source -and (Test-Path -LiteralPath $command.Source)) { return $command.Source }
    foreach ($candidate in @("$env:ProgramFiles\Git\cmd\git.exe", "$env:ProgramFiles\Git\bin\git.exe", "${env:ProgramFiles(x86)}\Git\cmd\git.exe")) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return $null
}

function Get-ReleaseBlockers {
    <#
    .SYNOPSIS
        The tracked, uncommitted changes that would make a Cloud Core release impossible.
    .OUTPUTS
        Blocked   - $true when a release would be refused
        Changes   - the porcelain lines (e.g. " M docs/X.md"), empty when clean
        Checked   - $false when git could not be run at all (then Blocked is $false: never
                    invent a blocker from a missing tool; the release script re-checks anyway)
    #>
    [CmdletBinding()]
    param(
        [string]$RepoRoot = ".",
        [scriptblock]$GitRunner = $null
    )
    if ($null -eq $GitRunner) {
        $git = Get-GitPath
        if (-not $git) {
            return [pscustomobject]@{ Blocked = $false; Changes = @(); Checked = $false }
        }
        $GitRunner = { param($Root) & $git -C $Root status --porcelain --untracked-files=no 2>$null }
    }
    $lines = @()
    try { $lines = @(& $GitRunner $RepoRoot | Where-Object { $_ -and $_.Trim() }) }
    catch { return [pscustomobject]@{ Blocked = $false; Changes = @(); Checked = $false } }
    return [pscustomobject]@{ Blocked = (@($lines).Count -gt 0); Changes = @($lines); Checked = $true }
}

function Write-ReleaseBlockers {
    <#  Print the blockers the way the owner can act on them (path per line).  #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Blockers, [string]$Prefix = "      ")
    foreach ($change in @($Blockers.Changes)) { Write-Host "$Prefix  $change" -ForegroundColor Yellow }
}
