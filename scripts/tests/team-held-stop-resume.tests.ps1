<#
.SYNOPSIS
    A stopped card held for another card's files comes back by itself when the files are free
    (held-stop-resumes-when-area-frees).

.DESCRIPTION
    Measured 2026-10-07 11:40: three stopped cards whose reason ends
    "(alan çakışması: X; o iş bitince)" sat after X had left the work - migration-rechain-on-merge,
    alarm-song-by-voice, test-round-io-report. The cycle resumed only the holds it wrote itself
    (the Proje Yöneticisi's return prefix, Import-DutyWaits); a hold the Danışman or another
    cycle's decision wrote into the reason was never looked at again.

    scripts/team/cycle.ps1 is run for real, in a git repository made for the test, with
    scripts/tests/lib/fake-claude.ps1 in place of the model and the duty on. What is asserted is
    what is on disk afterwards: the queue, the report, and the fake's log of the runs.

    Run: powershell -NoProfile -File scripts\tests\team-held-stop-resume.tests.ps1
#>

[CmdletBinding()]
param(
    # Runs only the cases whose name matches this pattern (the mutation proofs re-run a slice).
    [string]$Filter = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")

$script:Failures = 0
$script:Passes = 0
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$sandboxes = New-Object System.Collections.ArrayList

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

function New-Task {
    param([string]$Id, [string]$State = "approved", [string[]]$Area = @("src/area"), [string]$Branch = "")
    return [pscustomobject]@{
        id = $Id; title = "the task $Id"; roadmap_row = "row"; state = $State; area = @($Area)
        branch = $Branch; worktree = ""; assignee = ""; reports = @(); budget = [pscustomobject]@{ max_usd = 5 }
        created_at = "2026-09-30T00:00:00Z"; updated_at = "2026-09-30T00:00:00Z"
    }
}

function New-Stopped {
    param([string]$Id = "held-one", [string]$Reason, [string[]]$Area = @("src/area"))
    $task = New-Task -Id $Id -State "stopped" -Area $Area
    $task.updated_at = "2026-10-06T17:35:00Z"
    $task | Add-Member -NotePropertyName reason -NotePropertyValue $Reason
    $task | Add-Member -NotePropertyName returns -NotePropertyValue 1
    return $task
}

function New-HeldInWork {
    <# A task that holds src/area and cannot run in the cycle: it waits for an idea on the owner's gate. #>
    param([string]$Id)
    $task = New-Task -Id $Id -State "returned" -Area @("src/area")
    $task | Add-Member -NotePropertyName depends_on -NotePropertyValue @("idea-gate")
    return $task
}

function Invoke-SandboxGit {
    param([string]$Root, [string[]]$Arguments)
    $result = Invoke-TeamGit -WorkingDirectory $Root -Arguments $Arguments
    if (-not $result.Success) { throw "git $($Arguments -join ' '): $($result.StdErr)" }
    return $result.StdOut.Trim()
}

function New-Sandbox {
    param([object[]]$Tasks)
    $root = Join-Path $env:TEMP ("pagentos-held-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    [void]$sandboxes.Add($root)
    foreach ($folder in @("scripts\lib", "scripts\team", "scripts\tests\lib", ".claude\agents", "team", "src\area")) {
        [void](New-Item -ItemType Directory -Force -Path (Join-Path $root $folder))
    }
    foreach ($name in @("NativeProcess.ps1", "TeamQueue.ps1", "TeamRun.ps1", "HttpJson.ps1", "TeamArea.ps1", "TeamAreaProtected.ps1")) {
        Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\lib\$name") -Destination (Join-Path $root "scripts\lib\$name")
    }
    Copy-Item -Path (Join-Path $repoRoot "scripts\team\*.ps1") -Destination (Join-Path $root "scripts\team")
    Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\tests\lib\fake-claude.ps1") -Destination (Join-Path $root "scripts\tests\lib\fake-claude.ps1")
    foreach ($role in @("lead", "researcher", "integrator", "worker", "inspector")) {
        Copy-Item -LiteralPath (Join-Path $repoRoot ".claude\agents\$role.md") -Destination (Join-Path $root ".claude\agents\$role.md")
    }
    Set-Content -LiteralPath (Join-Path $root ".gitignore") -Value ".claude/worktrees/" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $root "src\area\README.txt") -Value "the area" -Encoding ASCII
    Write-TeamJson -Path (Join-Path $root "team\queue.json") -Document ([pscustomobject]@{ version = 1; tasks = @($Tasks) })
    Write-TeamJson -Path (Join-Path $root "team\lock.json") -Document (New-TeamLockReleased)
    [void](Invoke-SandboxGit -Root $root -Arguments @("init", "-q", "-b", "main"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.name", "team test"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "user.email", "team@example.invalid"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("config", "core.autocrlf", "false"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("add", "-A"))
    [void](Invoke-SandboxGit -Root $root -Arguments @("commit", "-q", "-m", "the sandbox"))
    return $root
}

function Invoke-DutyCycle {
    <# One cycle with the duty on; a duty run writes no decision (the fake writes none unset). #>
    param([string]$Root)
    $log = Join-Path $Root "fake.log"
    $env:PAGENTOS_FAKE_CLAUDE_SCENARIO = "approve"
    $env:PAGENTOS_FAKE_CLAUDE_LOG = $log
    $env:PAGENTOS_FAKE_CLAUDE_DUTY_CARD = (Join-Path $Root "duty-cards.txt")
    try {
        $arguments = @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
            ("& '" + (Join-Path $Root "scripts\team\cycle.ps1") + "' -CycleId 'c1' -MaxParallel 2 -Machine 'MAIL' -NoResearch" +
            " -ClaudePath '$powershell' -ClaudePrefixArguments '-NoProfile','-ExecutionPolicy','Bypass','-File','" +
            (Join-Path $Root "scripts\tests\lib\fake-claude.ps1") + "'; exit `$LASTEXITCODE")
        )
        $result = Invoke-NativeProcess -FilePath $powershell -Arguments $arguments -WorkingDirectory $Root -TimeoutSeconds 300 -SuccessExitCodes @(0, 2, 3)
    }
    finally {
        foreach ($name in @("PAGENTOS_FAKE_CLAUDE_SCENARIO", "PAGENTOS_FAKE_CLAUDE_LOG", "PAGENTOS_FAKE_CLAUDE_DUTY_CARD")) { Remove-Item -Path "Env:\$name" -ErrorAction SilentlyContinue }
    }
    $calls = @()
    if (Test-Path -LiteralPath $log) {
        $calls = @(Get-Content -LiteralPath $log -Encoding UTF8 | Where-Object { $_.Trim() } | ForEach-Object { ConvertFrom-Json -InputObject $_ })
    }
    $reportPath = Join-Path $Root "team\reports\c1.md"
    return [pscustomobject]@{
        ExitCode = $result.ExitCode; StdOut = $result.StdOut; StdErr = $result.StdErr
        Order    = (@($calls | ForEach-Object { "$($_.role):$($_.task)" }) -join ",")
        Queue    = (Read-TeamJson -Path (Join-Path $Root "team\queue.json"))
        Report   = $(if (Test-Path -LiteralPath $reportPath) { [System.IO.File]::ReadAllText($reportPath, [System.Text.Encoding]::UTF8) } else { "" })
    }
}

function Get-TaskById {
    param($Queue, [string]$Id)
    return @(Get-TeamTasks -Queue $Queue | Where-Object { $_.id -eq $Id })[0]
}

# The words of the real holds (2026-10-06/07): no return prefix, written by a decision of another
# cycle or by the Danışman, the holder named at the end.
$base = "al - 0067 numarali goc artik 0071_wake_alarm_song olmali; testlerde sabit goc numarasi yazma."
$gate = New-Task -Id "idea-gate" -State "awaiting_owner" -Area @("src/other")

Write-Host ""
Write-Host "a stopped card held for another card's files comes back when they are free"

try {
    Test-Case "a held card whose named holder is merged returns at the next refill (no return prefix), with the hold taken off its reason and one line in the report" {
        $held = New-Stopped -Reason "$base (alan çakışması: verify-mode; o iş bitince)"
        $root = New-Sandbox -Tasks @($held, (New-Task -Id "verify-mode" -State "merged" -Area @("src/area")))
        $run = Invoke-DutyCycle -Root $root
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "worker:held-one,inspector:held-one" -Actual $run.Order -Because "returned to its worker, never handed to the duty: $($run.Report)"
        $task = Get-TaskById -Queue $run.Queue -Id "held-one"
        Assert-Equal -Expected "merged|$base" -Actual ("{0}|{1}" -f $task.state, $task.reason) -Because "worked with the reason before the hold"
        Assert-True -Condition ($run.Report -match "alan boşaldı: held-one geri döndü \(verify-mode bitti\)") -Because "one line in the report: $($run.Report)"
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $run.Queue).Count -Because "the queue keeps the protocol"
    }

    Test-Case "a held card whose named holder left, but whose files another card now holds, stays stopped and its reason names that card" {
        $held = New-Stopped -Reason "$base (alan çakışması: gate-unit-parallel, duty-waits-survive-restart; o iş bitince)"
        $root = New-Sandbox -Tasks @($held, (New-Task -Id "gate-unit-parallel" -State "merged" -Area @("src/area")),
            (New-Task -Id "duty-waits-survive-restart" -State "awaiting_release" -Area @("src/area")), (New-HeldInWork -Id "new-holder"), $gate)
        $run = Invoke-DutyCycle -Root $root
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "" -Actual $run.Order -Because "no worker beside the new holder, no duty run for a hold: $($run.Report)"
        $task = Get-TaskById -Queue $run.Queue -Id "held-one"
        Assert-Equal -Expected "stopped|$base (alan çakışması: new-holder; o iş bitince)" -Actual ("{0}|{1}" -f $task.state, $task.reason) -Because "the hold names who holds the files now"
        Assert-True -Condition ([string]$task.updated_at -cne "2026-10-06T17:35:00Z") -Because "the reason changed, so did the time"
        Assert-Equal -Expected 0 -Actual @(Test-TeamQueue -Queue $run.Queue).Count -Because "the queue keeps the protocol"
    }

    Test-Case "a held card whose named holder still holds the files is left exactly as it is" {
        $reason = "$base (alan çakışması: new-holder; o iş bitince)"
        $root = New-Sandbox -Tasks @((New-Stopped -Reason $reason), (New-HeldInWork -Id "new-holder"), $gate)
        $run = Invoke-DutyCycle -Root $root
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "" -Actual $run.Order -Because "no run: $($run.Report)"
        $task = Get-TaskById -Queue $run.Queue -Id "held-one"
        Assert-Equal -Expected "stopped|$reason|2026-10-06T17:35:00Z" -Actual ("{0}|{1}|{2}" -f $task.state, $task.reason, $task.updated_at) -Because "untouched"
    }

    Test-Case "a stopped card without the suffix is untouched (the duty gets it as any stop), one naming a card the queue does not know is not guessed at, and the Danışman's own stop is his" {
        $plain = New-Stopped -Reason "inceleme durdu: test eksik"
        $unknown = New-Stopped -Id "held-two" -Area @("src/b") -Reason "$base (alan çakışması: nowhere-card; o iş bitince)"
        $danisman = New-Stopped -Id "held-three" -Area @("src/c") -Reason "Danışman'a iletildi: $base (alan çakışması: verify-mode; o iş bitince)"
        $root = New-Sandbox -Tasks @($plain, $unknown, $danisman, (New-Task -Id "verify-mode" -State "merged" -Area @("src/c")))
        $run = Invoke-DutyCycle -Root $root
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-Equal -Expected "lead:" -Actual $run.Order -Because "the first two go to the duty (it wrote no decision), the Danışman's to nobody: $($run.Report)"
        foreach ($expected in @(@("held-one", "inceleme durdu: test eksik"), @("held-two", "$base (alan çakışması: nowhere-card; o iş bitince)"),
                @("held-three", "Danışman'a iletildi: $base (alan çakışması: verify-mode; o iş bitince)"))) {
            $task = Get-TaskById -Queue $run.Queue -Id $expected[0]
            Assert-Equal -Expected "stopped|$($expected[1])|2026-10-06T17:35:00Z" -Actual ("{0}|{1}|{2}" -f $task.state, $task.reason, $task.updated_at) -Because "untouched"
        }
    }

    Test-Case "the owner-postponed stops ('bekletiliyor - sahibin sırası') are untouched, with or without the Danışman's prefix, though their holder is merged" {
        $postponed = "bekletiliyor - sahibin sırası (2026-10-05): önce JARVIS'in kodlaması, sonra test ekibi, sonra ofis. (alan çakışması: verify-mode; o iş bitince)"
        $escalated = New-Stopped -Reason ("Danışman'a iletildi: " + $postponed)
        $bare = New-Stopped -Id "held-two" -Area @("src/b") -Reason $postponed
        $root = New-Sandbox -Tasks @($escalated, $bare, (New-Task -Id "verify-mode" -State "merged" -Area @("src/area", "src/b")))
        $run = Invoke-DutyCycle -Root $root
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because ($run.StdOut + $run.StdErr)
        Assert-True -Condition ($run.Order -notmatch "worker:") -Because "no worker for an owner-postponed card: $($run.Order) $($run.Report)"
        foreach ($expected in @(@("held-one", "Danışman'a iletildi: $postponed"), @("held-two", $postponed))) {
            $task = Get-TaskById -Queue $run.Queue -Id $expected[0]
            Assert-Equal -Expected "stopped|$($expected[1])|2026-10-06T17:35:00Z" -Actual ("{0}|{1}|{2}" -f $task.state, $task.reason, $task.updated_at) -Because "untouched"
        }
    }
}
finally {
    foreach ($root in $sandboxes) { Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Host ""
Write-Host "passed: $script:Passes  failed: $script:Failures"
if ($script:Failures -gt 0) { exit 1 }
exit 0
