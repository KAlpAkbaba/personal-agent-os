<#
.SYNOPSIS
    The JARVIS rows follow the releases (card release-moves-roadmap-rows,
    team/plans/release-moves-roadmap-rows-adr.md; scripts/team/roadmap-rows.ps1).

.DESCRIPTION
    The script runs as the lead runs it (powershell.exe -File) on a fixture ROADMAP, a fake queue
    and a fake strip in TEMP: a released card moves its MISSING row to PARTIAL with the sha; a row
    with open cards stays PARTIAL and lists them; HAVE is only proposed, never written; a second
    run changes nothing; -Commit makes one commit on a lead branch and none on main. One case runs
    on a copy of the real docs/ROADMAP.md (the morning of 2026-10-07: 'Knows his money').

    Run: powershell -NoProfile -File scripts\tests\team-roadmap-rows.tests.ps1 [-Filter <regex>]
#>
[CmdletBinding()]
param([string]$Filter = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")

$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$script = Join-Path $repoRoot "scripts\team\roadmap-rows.ps1"
$utf8 = New-Object System.Text.UTF8Encoding($false)
$script:Failures = 0
$script:Passes = 0
$sandboxes = New-Object System.Collections.ArrayList
foreach ($name in @("PAGENTOS_TEAM_URL", "PAGENTOS_TEAM_TOKEN_FILE", "PAGENTOS_TEAM_SEAT")) { Remove-Item -Path ("Env:" + $name) -ErrorAction SilentlyContinue }

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    if ($Filter -and $Name -notmatch $Filter) { return }
    try { & $Body; $script:Passes++; Write-Host "  PASS  $Name" }
    catch {
        $script:Failures++
        Write-Host "  FAIL  $Name" -ForegroundColor Red
        Write-Host "        $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Assert-Equal {
    param($Expected, $Actual, [string]$Because)
    if ($Expected -ne $Actual) { throw "$Because`n          expected: <$Expected>`n          actual  : <$Actual>" }
}

function Assert-True {
    param([bool]$Condition, [string]$Because)
    if (-not $Condition) { throw $Because }
}

$shaMoney = "da3e26b9" + ("0" * 32)
$shaVerify = "1234abcd" + ("1" * 32)
$shaHouse = "72884b71" + ("2" * 32)
$shaRelease = "d74a8daa" + ("3" * 32)

$fixtureRoadmap = @"
# Roadmap

### What JARVIS does, and where this system stands (2026-09-27)

| JARVIS | PersonalAgentOS today | State |
|---|---|---|
| Researches anything, reads the world's data | Research | **HAVE** |
| **Knows his money: the balance and every spend, without touching the bank** (the owner, 2026-10-05) | Nothing yet | **MISSING** — JARVIS's own ledger |
| **Verifies what he hears and keeps it to argue later** (the owner, 2026-10-05) | Research | **MISSING** — a "doğrula" mode |
| Runs the house: lights, doors, climate | Home Assistant | **MISSING** — adopt |
| **Keeps the house's stock, down to the toilet paper** (the owner, 2026-10-05) | Memory | **PARTIAL** — RELEASED 2026-10-06 (72884b71): the house's stock |
| Personality, dry wit | The persona instructions | **PARTIAL** — tune |

### The limits, stated once

- text after the table
"@ -replace "`r`n", "`n"

function New-Task {
    param([string]$Id, [string]$Row, [string]$State, [string]$Sha = "")
    $task = [ordered]@{ id = $Id; title = $Id; roadmap_row = $Row; state = $State; area = @("x"); branch = ""; worktree = ""; assignee = ""; reports = @(); budget = @{ max_usd = 1 }; created_at = "2026-10-07T00:00:00Z"; updated_at = "2026-10-07T00:00:00Z" }
    if ($Sha) { $task.sha = $Sha }
    return $task
}

function New-Sandbox {
    <# A fixture ROADMAP, a queue and (optionally) a strip, in TEMP. #>
    param([object[]]$Tasks, [object[]]$ProofRows = $null, [string]$Roadmap = $fixtureRoadmap)
    $dir = Join-Path ([System.IO.Path]::GetTempPath()) ("roadmap-rows-" + [guid]::NewGuid().ToString("N").Substring(0, 10))
    New-Item -ItemType Directory -Path (Join-Path $dir "docs") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $dir "team") -Force | Out-Null
    [void]$sandboxes.Add($dir)
    [System.IO.File]::WriteAllText((Join-Path $dir "docs\ROADMAP.md"), $Roadmap, $utf8)
    $queue = [ordered]@{ version = 1; tasks = @($Tasks) }
    [System.IO.File]::WriteAllText((Join-Path $dir "team\queue.json"), (ConvertTo-Json -InputObject $queue -Depth 8), $utf8)
    if ($null -ne $ProofRows) {
        $office = @{ progress = @{ proof = @{ rows = @($ProofRows) } } }
        [System.IO.File]::WriteAllText((Join-Path $dir "office.json"), (ConvertTo-Json -InputObject $office -Depth 8), $utf8)
    }
    return $dir
}

function Invoke-Rows {
    param([string]$Dir, [string[]]$Extra = @())
    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $script, "-Worktree", $Dir)
    if (Test-Path -LiteralPath (Join-Path $Dir "office.json")) { $arguments += @("-StripFile", (Join-Path $Dir "office.json")) }
    # The script writes UTF-8 (its Turkish lines); read it as UTF-8, as board.ps1's readers do.
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $powershell
    $psi.Arguments = ConvertTo-NativeArgumentLine -Arguments ($arguments + $Extra)
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = $utf8
    $psi.StandardErrorEncoding = $utf8
    $process = [System.Diagnostics.Process]::Start($psi)
    $err = $process.StandardError.ReadToEndAsync()
    $out = $process.StandardOutput.ReadToEnd()
    if (-not $process.WaitForExit(120000)) { $process.Kill(); throw "roadmap-rows.ps1 did not end in 120 s" }
    return [pscustomobject]@{ Exit = $process.ExitCode; Out = ($out + $err.Result) }
}

function Read-Roadmap { param([string]$Dir) return [System.IO.File]::ReadAllText((Join-Path $Dir "docs\ROADMAP.md"), $utf8) }
function Get-Row {
    param([string]$Dir, [string]$Start)
    return @((Read-Roadmap $Dir) -split "`n" | Where-Object { $_ -like "| $Start*" -or $_ -like "| **$Start*" })[0]
}
function Get-FileSha { param([string]$Path) return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash }

Write-Host "team-roadmap-rows"

Test-Case "a released card moves its MISSING row to PARTIAL with the card and its sha" {
    $dir = New-Sandbox -Tasks @(
        (New-Task -Id "money-ledger" -Row "Knows his money: the balance and every spend, without touching the bank" -State "released" -Sha $shaMoney)
    )
    $run = Invoke-Rows -Dir $dir
    Assert-Equal 0 $run.Exit "exit 0: $($run.Out)"
    $row = Get-Row -Dir $dir -Start "Knows his money"
    Assert-True ($row.EndsWith("| **PARTIAL** — JARVIS's own ledger [kartlar: yayında money-ledger (da3e26b9); kalan: yok] |")) "the row is PARTIAL with the card and sha: $row"
    Assert-True ($row -notlike "*MISSING*") "MISSING is gone: $row"
    Assert-True ($run.Out -like "*YARIM: Knows his money*money-ledger (da3e26b9)*") "the move is said: $($run.Out)"
    Assert-True ((Get-Row -Dir $dir -Start "Runs the house") -like "*| **MISSING** — adopt |") "a row with no card stays MISSING"
    Assert-True ((Get-Row -Dir $dir -Start "Researches anything") -like "*| **HAVE** |") "a HAVE row is not touched"
    $expected = $fixtureRoadmap.Replace("**MISSING** — JARVIS's own ledger |", "**PARTIAL** — JARVIS's own ledger [kartlar: yayında money-ledger (da3e26b9); kalan: yok] |")
    Assert-Equal $expected (Read-Roadmap $dir) "only that one row changed, every other byte as it was"
}

Test-Case "a row with open cards stays PARTIAL and lists them; a MISSING row with only open cards stays MISSING" {
    $dir = New-Sandbox -Tasks @(
        (New-Task -Id "home-stock-list" -Row "Keeps the house's stock, down to the toilet paper" -State "released" -Sha $shaHouse),
        (New-Task -Id "stock-receipts" -Row "Keeps the house's stock, down to the toilet paper" -State "approved"),
        (New-Task -Id "stock-orders" -Row "Keeps the house's stock (order 5)" -State "in_progress"),
        (New-Task -Id "verify-later" -Row "Verifies what he hears and keeps it to argue later" -State "approved")
    ) -ProofRows @(@{ name = "Keeps the house's stock, down to the toilet paper (the owner, 2026-10-05)"; staging_proven = $true })
    $run = Invoke-Rows -Dir $dir
    Assert-Equal 0 $run.Exit "exit 0: $($run.Out)"
    $row = Get-Row -Dir $dir -Start "Keeps the house"
    Assert-True ($row.EndsWith("| **PARTIAL** — RELEASED 2026-10-06 (72884b71): the house's stock [kartlar: yayında home-stock-list (72884b71); kalan: stock-orders, stock-receipts] |")) "the open cards are listed: $row"
    Assert-True ($run.Out -notlike "*ÖNERİ VAR*") "open cards: no HAVE proposed, even staging-proven: $($run.Out)"
    Assert-True ((Get-Row -Dir $dir -Start "Verifies what he hears") -like "*| **MISSING** — a ""doğrula"" mode |") "only open cards: the row stays MISSING"
}

Test-Case "HAVE is only proposed (all cards released and staging-proven), never written by the script" {
    $proof = @(@{ name = "Keeps the house's stock, down to the toilet paper (the owner, 2026-10-05)"; staging_proven = $true })
    $dir = New-Sandbox -Tasks @(
        (New-Task -Id "home-stock-list" -Row "Keeps the house's stock, down to the toilet paper" -State "released" -Sha $shaHouse),
        (New-Task -Id "stock-receipts" -Row "Keeps the house's stock, down to the toilet paper" -State "done" -Sha $shaMoney),
        (New-Task -Id "stock-merged-away" -Row "Keeps the house's stock, down to the toilet paper" -State "done")
    ) -ProofRows $proof
    $run = Invoke-Rows -Dir $dir
    Assert-Equal 0 $run.Exit "exit 0: $($run.Out)"
    Assert-True ($run.Out -like "*ÖNERİ VAR: Keeps the house's stock*kararı Proje Yöneticisi yazar*") "HAVE is proposed: $($run.Out)"
    $row = Get-Row -Dir $dir -Start "Keeps the house"
    Assert-True ($row.Contains("| **PARTIAL** — ") -and $row.EndsWith("[kartlar: yayında home-stock-list (72884b71), stock-receipts (da3e26b9); kalan: yok] |")) "the row stays PARTIAL: $row"
    Assert-True ((Read-Roadmap $dir) -notmatch '\*\*HAVE\*\*.*house') "the script wrote no HAVE"
    $plain = New-Sandbox -Tasks @(
        (New-Task -Id "home-stock-list" -Row "Keeps the house's stock, down to the toilet paper" -State "released" -Sha $shaHouse)
    ) -ProofRows @(@{ name = "Keeps the house's stock, down to the toilet paper (the owner, 2026-10-05)"; staging_proven = $false })
    $notProven = Invoke-Rows -Dir $plain
    Assert-True ($notProven.Out -notlike "*ÖNERİ VAR*") "not staging-proven: nothing proposed: $($notProven.Out)"
}

Test-Case "a second run on the same queue changes nothing" {
    $dir = New-Sandbox -Tasks @(
        (New-Task -Id "money-ledger" -Row "Knows his money: the balance and every spend, without touching the bank" -State "released" -Sha $shaMoney),
        (New-Task -Id "money-bank-mails" -Row "Knows his money" -State "approved"),
        (New-Task -Id "home-stock-list" -Row "Keeps the house's stock, down to the toilet paper" -State "released" -Sha $shaHouse)
    )
    $first = Invoke-Rows -Dir $dir
    Assert-Equal 0 $first.Exit "first run: $($first.Out)"
    $after = Get-FileSha (Join-Path $dir "docs\ROADMAP.md")
    $second = Invoke-Rows -Dir $dir
    Assert-Equal 0 $second.Exit "second run: $($second.Out)"
    Assert-Equal $after (Get-FileSha (Join-Path $dir "docs\ROADMAP.md")) "the second run left the file byte for byte"
    Assert-True ($second.Out -like "*değişiklik yok*") "the second run says so: $($second.Out)"
    $row = Get-Row -Dir $dir -Start "Knows his money"
    Assert-Equal 1 ([regex]::Matches($row, '\[kartlar:').Count) "one segment, not two: $row"
}

Test-Case "-Commit makes one commit with the release sha on a lead branch, none on a second run, and refuses main" {
    $dir = New-Sandbox -Tasks @(
        (New-Task -Id "money-ledger" -Row "Knows his money: the balance and every spend, without touching the bank" -State "released" -Sha $shaMoney)
    )
    foreach ($a in @(@("init", "--quiet", "-b", "main"), @("config", "user.email", "t@example.invalid"), @("config", "user.name", "t"), @("add", "docs/ROADMAP.md"), @("commit", "--quiet", "-m", "base"))) {
        $g = Invoke-TeamGit -WorkingDirectory $dir -Arguments $a -TimeoutSeconds 60
        Assert-True $g.Success "git $($a -join ' '): $($g.StdErr)"
    }
    $onMain = Invoke-Rows -Dir $dir -Extra @("-Sha", $shaRelease, "-Commit")
    Assert-Equal 12 $onMain.Exit "main is refused: $($onMain.Out)"
    Assert-Equal $fixtureRoadmap (Read-Roadmap $dir) "a refusal on main writes nothing"
    Assert-True ((Invoke-TeamGit -WorkingDirectory $dir -Arguments @("checkout", "--quiet", "-b", "team/nightly/lead")).Success) "lead branch"
    $run = Invoke-Rows -Dir $dir -Extra @("-Sha", $shaRelease, "-Commit")
    Assert-Equal 0 $run.Exit "commit run: $($run.Out)"
    $log = ([string](Invoke-TeamGit -WorkingDirectory $dir -Arguments @("log", "--format=%s")).StdOut).Trim() -split "`n"
    Assert-Equal 2 @($log).Count "one new commit: $($log -join ' / ')"
    Assert-True ($log[0] -like "*$shaRelease*") "the message names the release: $($log[0])"
    Assert-Equal "" ([string](Invoke-TeamGit -WorkingDirectory $dir -Arguments @("status", "--porcelain", "--", "docs/ROADMAP.md")).StdOut).Trim() "the ROADMAP is committed"
    $again = Invoke-Rows -Dir $dir -Extra @("-Sha", $shaRelease, "-Commit")
    Assert-Equal 0 $again.Exit "second commit run: $($again.Out)"
    Assert-Equal 2 @(([string](Invoke-TeamGit -WorkingDirectory $dir -Arguments @("log", "--format=%s")).StdOut).Trim() -split "`n").Count "no commit on the second run"
}

Test-Case "on a copy of the real ROADMAP: money-ledger and verify-mode move their rows; the rest is untouched" {
    $real = [System.IO.File]::ReadAllText((Join-Path $repoRoot "docs\ROADMAP.md"), $utf8)
    $dir = New-Sandbox -Roadmap $real -Tasks @(
        (New-Task -Id "money-ledger" -Row "Knows his money: the balance and every spend, without touching the bank" -State "released" -Sha $shaMoney),
        (New-Task -Id "verify-mode" -Row "Verifies what he hears and keeps it to argue later" -State "released" -Sha $shaVerify),
        (New-Task -Id "office-page" -Row "How it is built from here (TEAM_PROTOCOL 3a)" -State "released" -Sha $shaRelease),
        (New-Task -Id "allowlist-editor" -Row "browser-use, anywhere (order 2b, ADR-0213)" -State "released" -Sha $shaRelease)
    )
    $run = Invoke-Rows -Dir $dir
    Assert-Equal 0 $run.Exit "exit 0: $($run.Out)"
    Assert-True ((Get-Row -Dir $dir -Start "Knows his money") -match '\| \*\*PARTIAL\*\* — .*\[kartlar: yayında money-ledger \(da3e26b9\); kalan: yok\] \|$') "money row PARTIAL"
    Assert-True ((Get-Row -Dir $dir -Start "Verifies what he hears") -match '\| \*\*PARTIAL\*\* — .*\[kartlar: yayında verify-mode \(1234abcd\); kalan: yok\] \|$') "verify row PARTIAL"
    Assert-True ($run.Out -like "*satır dışı*office-page*") "a card of no JARVIS row is named, not placed: $($run.Out)"
    $before = @($real -split "`n"); $after = @((Read-Roadmap $dir) -split "`n")
    $have = @($before | Where-Object { $_ -like "| Researches anything*" })[0]
    Assert-True (($after -ccontains $have) -and $have -like "*| **HAVE** |") "an alias onto a HAVE row (allowlist-editor) writes nothing: $have"
    Assert-Equal $before.Count $after.Count "same number of lines"
    $differ = @(0..($before.Count - 1) | Where-Object { $before[$_] -cne $after[$_] })
    Assert-Equal 2 $differ.Count "exactly two lines changed"
}

Test-Case "a wrong -Sha, -Commit without -Sha and a roadmap without the table write nothing" {
    $dir = New-Sandbox -Tasks @((New-Task -Id "money-ledger" -Row "Knows his money" -State "released" -Sha $shaMoney))
    $before = Get-FileSha (Join-Path $dir "docs\ROADMAP.md")
    Assert-Equal 2 (Invoke-Rows -Dir $dir -Extra @("-Sha", "abc")).Exit "a short sha"
    Assert-Equal 2 (Invoke-Rows -Dir $dir -Extra @("-Commit")).Exit "-Commit without -Sha"
    Assert-Equal $before (Get-FileSha (Join-Path $dir "docs\ROADMAP.md")) "nothing written"
    $none = New-Sandbox -Roadmap "# Roadmap`n`nno table`n" -Tasks @()
    Assert-Equal 2 (Invoke-Rows -Dir $none).Exit "no JARVIS table"
}

foreach ($dir in $sandboxes) { Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue }
Write-Host ""
Write-Host "team-roadmap-rows: $($script:Passes) passed, $($script:Failures) failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
