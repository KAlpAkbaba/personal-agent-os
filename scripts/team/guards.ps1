<#
.SYNOPSIS
    Run the guard list on one tree: the tests that read the whole application, in seconds,
    before the 80-minute gate finds the same thing.

.DESCRIPTION
    Runs every guard of `team/guards.json` (the WORKTREE's own list unless -List names
    another) on the worktree's own copy of each file, one after another, and prints
    `koruyucular: yeşil` or one Turkish line per row that is not green. It starts no agent,
    calls no network, installs nothing and leaves the worktree as it found it.

    Exit code: 0 = every guard green; 1 = at least one row is red, hung or missing;
    2 = it could not run at all (no such directory, not a git work tree, no list, a refused
    list, no interpreter). -OutFile receives the RESULT (scripts/lib/TeamGuards.ps1) as
    UTF-8 without a byte-order mark; nothing is written when the exit code is 2.

    A red guard on a TASK branch can be red by nature - a new suite is wired into the gate
    only at integration - so this script stops nothing by itself: it reports.

    -AfterMerge (card guards-after-every-merge, 2026-10-06): the run right after a merge into
    an integration branch. The list plus `ruff check .` in services/api (when the tree has it);
    green prints "birleştirmeden sonra koruyucular yeşil: N koruyucu, S sn (son birleşen: B)",
    red prints one line per row with the guard's id and the task branch merged last. With
    -OutFile, exit 2 writes { status: 'cannot_run', reason } there as well.

.EXAMPLE
    .\scripts\team\guards.ps1 -Worktree .claude\worktrees\team\gate\d20261002-4
.EXAMPLE
    .\scripts\team\guards.ps1 -Worktree .claude\worktrees\gate\integrate\d20261006 -AfterMerge
#>
[CmdletBinding()]
param(
    [string]$Worktree = "",
    [string]$List = "",
    [string]$OutFile = "",
    [int]$HangSeconds = 0,
    [string]$Python = "",
    # Right after a merge into an integration branch (integrate.ps1; the cycle's merge): ruff is
    # run too, and every line names the task branch merged last. The RESULT gets `last_merge`.
    [switch]$AfterMerge
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\TeamGuards.ps1")

function Invoke-GuardsScript {
    <# The whole script as one answer: the lines to print and the exit code. #>
    param([string]$Worktree, [string]$List, [string]$OutFile, [int]$HangSeconds, [string]$Python, [bool]$AfterMerge)
    $cannot = "koruyucular koşamadı: "
    if (-not $Worktree) { return [pscustomobject]@{ Code = 2; Lines = @($cannot + "-Worktree verilmedi") } }
    if (-not (Test-Path -LiteralPath $Worktree -PathType Container)) {
        return [pscustomobject]@{ Code = 2; Lines = @($cannot + "dizin yok: $Worktree") }
    }
    $root = (Resolve-Path -LiteralPath $Worktree).ProviderPath
    if (-not (Test-TeamGuardWorktree -Worktree $root) -or -not (Get-TeamGuardHead -Worktree $root)) {
        return [pscustomobject]@{ Code = 2; Lines = @($cannot + "git çalışma ağacı değil: $root") }
    }
    if (-not $List) { $List = Join-Path $root "team\guards.json" }
    $read = Read-TeamGuardList -Path $List
    if ($read.refused) { return [pscustomobject]@{ Code = 2; Lines = @($cannot + [string]$read.reason) } }
    $guards = @($read.guards)
    $lastMerge = $null
    if ($AfterMerge) {
        # Right after a merge into an integration branch: ruff too, where the tree has the API.
        $ruff = New-TeamGuardRuffRow
        if (Test-Path -LiteralPath (Join-Path $root (([string]$ruff.path) -replace "/", "\")) -PathType Container) { $guards = @($guards) + @($ruff) }
        $lastMerge = Get-TeamGuardLastMerge -Worktree $root
    }
    $named = [bool]$Python
    if (-not $named) { $Python = Get-TeamGuardDefaultPython -Worktree $root }
    $needed = $named -or (@($guards | Where-Object { $_.kind -ne "powershell" }).Count -gt 0)
    if ($needed -and -not ($Python -and (Test-Path -LiteralPath $Python -PathType Leaf))) {
        return [pscustomobject]@{ Code = 2; Lines = @($cannot + "Python yorumlayıcısı yok: $Python") }
    }

    $result = Invoke-TeamGuards -Worktree $root -List $guards -Python $Python -HangSeconds $HangSeconds
    if ($AfterMerge) { $result | Add-Member -NotePropertyName last_merge -NotePropertyValue $lastMerge }
    if ($OutFile) { Write-TeamJson -Path $OutFile -Document $result }
    if ($AfterMerge) {
        $code = if ($result.status -eq "green") { 0 } else { 1 }
        return [pscustomobject]@{ Code = $code; Lines = @(Get-TeamGuardAfterMergeLines -Result $result -LastMerge $lastMerge) }
    }
    if ($result.status -eq "green") { return [pscustomobject]@{ Code = 0; Lines = @("koruyucular: yeşil") } }
    $lines = @($result.rows | Where-Object { $_.outcome -ne "green" } | ForEach-Object { Get-TeamGuardLine -Row $_ })
    return [pscustomobject]@{ Code = 1; Lines = @($lines) }
}

# Turkish letters reach a pipe as UTF-8 whatever the console's code page is; the session
# that typed the script's name gets its own encoding back.
$consoleEncoding = $null
try {
    $consoleEncoding = [Console]::OutputEncoding
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
}
catch { $consoleEncoding = $null }

$answer = $null
try {
    $answer = Invoke-GuardsScript -Worktree $Worktree -List $List -OutFile $OutFile -HangSeconds $HangSeconds -Python $Python -AfterMerge ([bool]$AfterMerge)
}
catch {
    $answer = [pscustomobject]@{ Code = 2; Lines = @("koruyucular koşamadı: $($_.Exception.Message)") }
}
# After a merge the caller reads the file, never the console (its code page is not UTF-8): a run
# that could not run at all leaves its reason there too.
if ($AfterMerge -and $OutFile -and [int]$answer.Code -eq 2) {
    try { Write-TeamJson -Path $OutFile -Document ([pscustomobject]@{ status = "cannot_run"; reason = (@($answer.Lines) -join "; ") }) } catch { }
}
foreach ($line in @($answer.Lines)) { Write-Host $line }
if ($null -ne $consoleEncoding) {
    try { [Console]::OutputEncoding = $consoleEncoding } catch { }
}
exit ([int]$answer.Code)
