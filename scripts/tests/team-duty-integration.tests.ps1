<#
.SYNOPSIS
    The Proje Yöneticisi resolves an integration-branch conflict itself (pm-resolves-integration-conflicts,
    the owner 2026-10-06: "çalışan 2'nin direk sana değil proje yöneticisine gitmeli").

.DESCRIPTION
    scripts/lib/TeamDuty.ps1 is driven for real, in a git repository made for the test, with
    scripts/team/fake-team-api.ps1 in place of the Cloud Core's queue. Each case builds
    integrate/<cycle> and a task branch that conflicts with it, stops the task with
    "entegrasyon dalında çakışma" in the fake store, runs the resolve duty, and asserts what is
    in the repository and the store afterwards - read by this file's own means (git, its own
    alembic parser), never by the library's.

      1. an additive conflict (two lines appended at one place): merged with both lines, no escalation;
      2. a migration revising the old head: renamed and re-pointed after the new head, one head;
      3. a conflict inside .claude/agents: escalated, reason "korunan dosya";
      4. a guard red after the merge: escalated, reason "koruyucu", integrate/<cycle> unchanged;
      5. a rewrite of the other side's line: escalated, reason "ekleme değil";
      6. two failed resolutions in a row: the first is counted, the second escalated;
      7. both sides adding the same new file (add/add, no base): escalated, reason "ekleme değil";
      8. the task's own test red with the guards green: escalated, the reason names that test;
      9. disjoint line edits in one hunk (each base line changed by one side only, the measured
         routes.py import pair of 2026-10-07): the union takes each line from the side that
         changed it - unit cases on Resolve-TeamDutyHunk and one end-to-end merge.

    Run: powershell -NoProfile -File scripts\tests\team-duty-integration.tests.ps1 [-Filter <regex>]
#>

[CmdletBinding()]
param([string]$Filter = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\HttpJson.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamRun.ps1")
$dutyLib = Join-Path $repoRoot "scripts\lib\TeamDuty.ps1"
if (Test-Path -LiteralPath $dutyLib) { . $dutyLib }

$utf8 = New-Object System.Text.UTF8Encoding($false)
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$script:Failures = 0
$script:Passes = 0
$sandboxes = New-Object System.Collections.ArrayList
$fakeApis = New-Object System.Collections.ArrayList
$cycleId = "c1"

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

function Git {
    param([string]$Root, [string[]]$Arguments)
    $ran = Invoke-TeamGit -WorkingDirectory $Root -Arguments $Arguments
    if (-not $ran.Success) { throw "git $($Arguments -join ' ') failed: $($ran.StdErr)" }
    return ([string]$ran.StdOut).Trim()
}

function Write-File {
    param([string]$Root, [string]$Path, [string]$Text)
    $full = Join-Path $Root ($Path -replace '/', '\')
    $parent = Split-Path -Parent $full
    if (-not (Test-Path -LiteralPath $parent)) { [void](New-Item -ItemType Directory -Force -Path $parent) }
    [System.IO.File]::WriteAllText($full, $Text, $utf8)
}

function Save-All {
    param([string]$Root, [string]$Message)
    [void](Git -Root $Root -Arguments @("add", "-A"))
    [void](Git -Root $Root -Arguments @("commit", "-q", "-m", $Message))
}

function New-Migration {
    param([string]$Revision, [string]$Down)
    $downText = if ($Down) { '"' + $Down + '"' } else { "None" }
    return "`"`"`"A migration.`n`nRevision ID: $Revision`nRevises: $(if ($Down) { $Down } else { '' })`n`"`"`"`n`nrevision: str = `"$Revision`"`ndown_revision: str | None = $downText`n`n`ndef upgrade() -> None:`n    pass`n"
}

function New-Sandbox {
    <# main with: a notes file, a guard that is red when the notes say BOZUK, the guard list, a
       role file, two migrations. integrate/c1 and the task branch both start from main. #>
    $root = Join-Path $env:TEMP ("pagentos-duty-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    [void](New-Item -ItemType Directory -Force -Path $root)
    [void]$sandboxes.Add($root)
    [void](Git -Root $root -Arguments @("init", "-q", "-b", "main"))
    [void](Git -Root $root -Arguments @("config", "user.email", "test@example.invalid"))
    [void](Git -Root $root -Arguments @("config", "user.name", "duty test"))
    [void](Git -Root $root -Arguments @("config", "core.autocrlf", "false"))
    Write-File -Root $root -Path ".gitignore" -Text ".claude/worktrees/`n"
    Write-File -Root $root -Path "src/notes.txt" -Text "bir`niki`n"
    Write-File -Root $root -Path "src/shared.txt" -Text "ortak satır`n"
    Write-File -Root $root -Path ".claude/agents/worker.md" -Text "# worker`nkural bir`n"
    Write-File -Root $root -Path "scripts/tests/notes-guard.tests.ps1" -Text ('$text = [System.IO.File]::ReadAllText((Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "src\notes.txt"))' + "`nif (`$text -match 'BOZUK') { Write-Host 'FAIL notes say BOZUK'; exit 1 }`nWrite-Host 'PASS'`nexit 0`n")
    Write-File -Root $root -Path "team/guards.json" -Text "{`n  `"version`": 1,`n  `"guards`": [`n    {`n      `"id`": `"notes-guard`",`n      `"kind`": `"powershell`",`n      `"path`": `"scripts/tests/notes-guard.tests.ps1`",`n      `"label`": `"notlar bozuk`"`n    }`n  ]`n}`n"
    Write-File -Root $root -Path "services/api/alembic/versions/20261001_0001_base.py" -Text (New-Migration -Revision "0001_base" -Down "")
    Write-File -Root $root -Path "services/api/alembic/versions/20261002_0002_people.py" -Text (New-Migration -Revision "0002_people" -Down "0001_base")
    Save-All -Root $root -Message "base"
    [void](Git -Root $root -Arguments @("branch", "integrate/$cycleId", "main"))
    [void](Git -Root $root -Arguments @("branch", "team/$cycleId/worker-the-task", "main"))
    return $root
}

function Edit-Branch {
    <# One commit on a branch, made in a throwaway worktree. $Files: path -> text. #>
    param([string]$Root, [string]$Branch, [hashtable]$Files, [string]$Message = "change")
    $tree = Join-Path $env:TEMP ("pagentos-duty-wt-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    [void](Git -Root $Root -Arguments @("worktree", "add", "-q", $tree, $Branch))
    foreach ($key in $Files.Keys) { Write-File -Root $tree -Path $key -Text $Files[$key] }
    Save-All -Root $tree -Message $Message
    [void](Git -Root $Root -Arguments @("worktree", "remove", "--force", $tree))
}

function New-StoppedTask {
    param([string]$Reason = "entegrasyon dalında çakışma")
    return [pscustomobject]@{
        id = "the-task"; title = "the task"; roadmap_row = "row"; state = "stopped"; area = @("src")
        branch = "team/$cycleId/worker-the-task"; worktree = ""; assignee = ""; reports = @(); returns = 1
        reason = $Reason; budget = [pscustomobject]@{ max_usd = 0 }
        created_at = "2026-10-06T00:00:00Z"; updated_at = "2026-10-06T00:00:00Z"
    }
}

function Start-FakeApi {
    param([object[]]$Tasks = @())
    $work = Join-Path $env:TEMP ("pagentos-dutyapi-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    [void](New-Item -ItemType Directory -Force -Path $work)
    [void]$sandboxes.Add($work)
    $seed = [pscustomobject]@{ queue = [pscustomobject]@{ version = 1; tasks = @($Tasks) }; lock = (New-TeamLockReleased) }
    [System.IO.File]::WriteAllText((Join-Path $work "seed.json"), (ConvertTo-Json -InputObject $seed -Depth 12), $utf8)
    $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $probe.Start(); $port = $probe.LocalEndpoint.Port; $probe.Stop()
    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + (Join-Path $repoRoot "scripts\team\fake-team-api.ps1") + '"'),
        "-Port", $port, "-Seed", ('"' + (Join-Path $work "seed.json") + '"'), "-Log", ('"' + (Join-Path $work "requests.log") + '"'),
        "-Ready", ('"' + (Join-Path $work "ready") + '"'), "-Token", "test-token")
    $process = Start-Process -FilePath $powershell -ArgumentList $arguments -PassThru -WindowStyle Hidden
    [void]$fakeApis.Add($process)
    $deadline = [datetime]::UtcNow.AddSeconds(40)
    while (-not (Test-Path -LiteralPath (Join-Path $work "ready"))) {
        if ([datetime]::UtcNow -gt $deadline -or $process.HasExited) { throw "the fake team API did not start" }
        Start-Sleep -Milliseconds 200
    }
    $tokenFile = Join-Path $work "token.txt"
    [System.IO.File]::WriteAllText($tokenFile, "test-token`n", $utf8)
    return [pscustomobject]@{ Url = "http://127.0.0.1:$port"; Work = $work; TokenFile = $tokenFile }
}

function Get-StoredTask {
    param($Api)
    $state = Invoke-JsonUtf8 -Uri ($Api.Url + "/__state")
    return @($state.tasks | Where-Object { $_.id -eq "the-task" })[0]
}

function Invoke-Resolve {
    <# The duty, as the cycle would call it: the store, the repository, the cycle. #>
    param([string]$Root, $Api)
    if ($null -eq (Get-Command -Name "Invoke-TeamDutyResolveIntegration" -CommandType Function -ErrorAction SilentlyContinue)) {
        throw "scripts/lib/TeamDuty.ps1 has no Invoke-TeamDutyResolveIntegration"
    }
    $store = New-TeamApiStore -Url $Api.Url -TokenFile $Api.TokenFile
    return @(Invoke-TeamDutyResolveIntegration -RepoRoot $Root -CycleId $cycleId -Store $store -Base "main" -Python "unused")
}

function Get-FileAt {
    <# The file's bytes as UTF-8 (git's console output is decoded with the OEM page elsewhere). #>
    param([string]$Root, [string]$Revision, [string]$Path)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = Get-TeamGit
    $psi.Arguments = "-C `"$Root`" show `"${Revision}:$Path`""
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.StandardOutputEncoding = $utf8
    $process = [System.Diagnostics.Process]::Start($psi)
    $text = $process.StandardOutput.ReadToEnd()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) { throw "git show ${Revision}:$Path failed" }
    return $text
}

function Get-AlembicHeads {
    <# This file's own reading of the chain: every revision no down_revision names. #>
    param([string]$Root, [string]$Revision)
    $names = @((Git -Root $Root -Arguments @("ls-tree", "--name-only", "$Revision", "services/api/alembic/versions/")) -split "`r?`n" | Where-Object { $_ -like "*.py" })
    $revisions = @{}
    $downs = @{}
    foreach ($name in $names) {
        $text = Get-FileAt -Root $Root -Revision $Revision -Path $name
        $rev = [regex]::Match($text, '(?m)^revision\b[^=]*=\s*"([^"]+)"').Groups[1].Value
        $down = [regex]::Match($text, '(?m)^down_revision\b[^=]*=\s*(.+)$').Groups[1].Value
        $revisions[$rev] = $name
        foreach ($m in [regex]::Matches($down, '"([^"]+)"')) { $downs[$m.Groups[1].Value] = $true }
    }
    return @($revisions.Keys | Where-Object { -not $downs.ContainsKey($_) })
}

Write-Host ""
Write-Host "the Proje Yöneticisi resolves an integration-branch conflict (TeamDuty.ps1, a sandbox repository and the fake team API)"

try {
    Test-Case "(1) an additive conflict - two lines appended at one place - is merged with both lines; the task is merged, nothing escalated" {
        $root = New-Sandbox
        Edit-Branch -Root $root -Branch "integrate/$cycleId" -Files @{ "src/notes.txt" = "bir`niki`nöteki işin satırı`n" } -Message "the other task"
        Edit-Branch -Root $root -Branch "team/$cycleId/worker-the-task" -Files @{ "src/notes.txt" = "bir`niki`nbu işin satırı`n" } -Message "the task"
        $taskTip = Git -Root $root -Arguments @("rev-parse", "team/$cycleId/worker-the-task")
        $api = Start-FakeApi -Tasks @((New-StoppedTask))
        $results = Invoke-Resolve -Root $root -Api $api
        $stored = Get-StoredTask -Api $api
        Assert-Equal -Expected "merged" -Actual ([string]$stored.state) -Because "the store holds the task merged ($([string]$stored.reason))"
        Assert-Equal -Expected "integrate/$cycleId" -Actual ([string]$stored.integration_branch) -Because "with its integration branch"
        Assert-True -Condition (-not ([string]$stored.reason).StartsWith("Danışman'a iletildi")) -Because "nothing went to the Danışman"
        $notes = Get-FileAt -Root $root -Revision "integrate/$cycleId" -Path "src/notes.txt"
        Assert-True -Condition ($notes.Contains("öteki işin satırı") -and $notes.Contains("bu işin satırı")) -Because "both lines are kept: $notes"
        Assert-True -Condition ($notes -notmatch '(?m)^(<<<<<<<|=======|>>>>>>>)') -Because "no conflict marker is left: $notes"
        Assert-Equal -Expected $taskTip -Actual (Git -Root $root -Arguments @("rev-parse", "integrate/$cycleId^2")) -Because "the integration branch's tip is a merge of the task's tip (a later merge reads it as merged)"
        Assert-Equal -Expected "merged" -Actual ([string]@($results)[0].Outcome) -Because "the duty says what it did"
    }

    Test-Case "(2) a migration revising the old head is renamed and re-pointed after the new head; one head" {
        $root = New-Sandbox
        Edit-Branch -Root $root -Branch "integrate/$cycleId" -Files @{ "services/api/alembic/versions/20261003_0003_watches.py" = (New-Migration -Revision "0003_watches" -Down "0002_people") } -Message "the other migration"
        Edit-Branch -Root $root -Branch "team/$cycleId/worker-the-task" -Files @{
            "services/api/alembic/versions/20261004_0003_alarm.py" = (New-Migration -Revision "0003_alarm" -Down "0002_people")
            "src/notes.txt" = "bir`niki`nalarm`n"
        } -Message "the task's migration"
        $api = Start-FakeApi -Tasks @((New-StoppedTask))
        [void](Invoke-Resolve -Root $root -Api $api)
        $stored = Get-StoredTask -Api $api
        Assert-Equal -Expected "merged" -Actual ([string]$stored.state) -Because "the store holds the task merged ($([string]$stored.reason))"
        $heads = @(Get-AlembicHeads -Root $root -Revision "integrate/$cycleId")
        Assert-Equal -Expected 1 -Actual @($heads).Count -Because "one head: $($heads -join ', ')"
        Assert-Equal -Expected "0004_alarm" -Actual $heads[0] -Because "the task's migration is renumbered after the new head"
        $files = Git -Root $root -Arguments @("ls-tree", "--name-only", "integrate/$cycleId", "services/api/alembic/versions/")
        Assert-True -Condition ($files.Contains("20261004_0004_alarm.py") -and -not $files.Contains("20261004_0003_alarm.py")) -Because "the file is renamed: $files"
        $text = Get-FileAt -Root $root -Revision "integrate/$cycleId" -Path "services/api/alembic/versions/20261004_0004_alarm.py"
        Assert-True -Condition ($text -match '(?m)^down_revision: str \| None = "0003_watches"$') -Because "down_revision is the new head: $text"
        Assert-True -Condition ($text -match '(?m)^Revision ID: 0004_alarm$' -and $text -match '(?m)^Revises: 0003_watches$') -Because "the docstring says the same: $text"
    }

    Test-Case "(3) a conflict inside .claude/agents is escalated with that reason; the integration branch is unchanged" {
        $root = New-Sandbox
        Edit-Branch -Root $root -Branch "integrate/$cycleId" -Files @{ ".claude/agents/worker.md" = "# worker`nkural bir`nkural öteki`n" } -Message "the other task"
        Edit-Branch -Root $root -Branch "team/$cycleId/worker-the-task" -Files @{ ".claude/agents/worker.md" = "# worker`nkural bir`nkural bu`n" } -Message "the task"
        $before = Git -Root $root -Arguments @("rev-parse", "integrate/$cycleId")
        $api = Start-FakeApi -Tasks @((New-StoppedTask))
        [void](Invoke-Resolve -Root $root -Api $api)
        $stored = Get-StoredTask -Api $api
        Assert-Equal -Expected "stopped" -Actual ([string]$stored.state) -Because "the task stays stopped"
        Assert-True -Condition (([string]$stored.reason).StartsWith("Danışman'a iletildi: ")) -Because "it went to the Danışman: $([string]$stored.reason)"
        Assert-True -Condition (([string]$stored.reason).Contains("korunan dosya") -and ([string]$stored.reason).Contains(".claude/agents/worker.md")) -Because "the reason names the case and the file: $([string]$stored.reason)"
        Assert-Equal -Expected $before -Actual (Git -Root $root -Arguments @("rev-parse", "integrate/$cycleId")) -Because "the integration branch is unchanged"
    }

    Test-Case "(4) a guard red after the merge is escalated and the integration branch is unchanged" {
        $root = New-Sandbox
        Edit-Branch -Root $root -Branch "integrate/$cycleId" -Files @{ "src/notes.txt" = "bir`niki`nöteki işin satırı`n" } -Message "the other task"
        Edit-Branch -Root $root -Branch "team/$cycleId/worker-the-task" -Files @{ "src/notes.txt" = "bir`niki`nBOZUK satır`n" } -Message "the task"
        $before = Git -Root $root -Arguments @("rev-parse", "integrate/$cycleId")
        $api = Start-FakeApi -Tasks @((New-StoppedTask))
        [void](Invoke-Resolve -Root $root -Api $api)
        $stored = Get-StoredTask -Api $api
        Assert-Equal -Expected "stopped" -Actual ([string]$stored.state) -Because "the task stays stopped"
        Assert-True -Condition (([string]$stored.reason).StartsWith("Danışman'a iletildi: ") -and ([string]$stored.reason).Contains("koruyucu")) -Because "escalated for the guard: $([string]$stored.reason)"
        Assert-True -Condition (([string]$stored.reason).Contains("notlar bozuk")) -Because "the guard's label is in the reason: $([string]$stored.reason)"
        Assert-Equal -Expected $before -Actual (Git -Root $root -Arguments @("rev-parse", "integrate/$cycleId")) -Because "the integration branch is unchanged"
        $tree = Get-TeamWorktreePath -RepoRoot $root -Branch "integrate/$cycleId"
        if (Test-Path -LiteralPath $tree) {
            Assert-Equal -Expected "" -Actual (Git -Root $tree -Arguments @("status", "--porcelain")) -Because "its worktree is clean"
        }
    }

    Test-Case "(5) a resolution that would rewrite the other side's line is escalated as not additive" {
        $root = New-Sandbox
        Edit-Branch -Root $root -Branch "integrate/$cycleId" -Files @{ "src/shared.txt" = "ortak satır - öteki böyle dedi`n" } -Message "the other task"
        Edit-Branch -Root $root -Branch "team/$cycleId/worker-the-task" -Files @{ "src/shared.txt" = "ortak satır - bu iş böyle dedi`n" } -Message "the task"
        $before = Git -Root $root -Arguments @("rev-parse", "integrate/$cycleId")
        $api = Start-FakeApi -Tasks @((New-StoppedTask))
        [void](Invoke-Resolve -Root $root -Api $api)
        $stored = Get-StoredTask -Api $api
        Assert-True -Condition (([string]$stored.reason).StartsWith("Danışman'a iletildi: ") -and ([string]$stored.reason).Contains("ekleme değil") -and ([string]$stored.reason).Contains("src/shared.txt")) -Because "escalated as not additive: $([string]$stored.reason)"
        Assert-Equal -Expected $before -Actual (Git -Root $root -Arguments @("rev-parse", "integrate/$cycleId")) -Because "the integration branch is unchanged"
    }

    Test-Case "(6) a failed resolution is counted once, the second in a row goes to the Danışman" {
        $root = New-Sandbox
        Edit-Branch -Root $root -Branch "integrate/$cycleId" -Files @{ "services/api/alembic/versions/20261003_0003_watches.py" = (New-Migration -Revision "0003_watches" -Down "0002_people") } -Message "the other migration"
        # A revision id the duty cannot renumber (no NNNN_ prefix): the resolution fails, not a judgement.
        Edit-Branch -Root $root -Branch "team/$cycleId/worker-the-task" -Files @{ "services/api/alembic/versions/20261004_alarm.py" = (New-Migration -Revision "alarmrev" -Down "0002_people") } -Message "the task's migration"
        $before = Git -Root $root -Arguments @("rev-parse", "integrate/$cycleId")
        $api = Start-FakeApi -Tasks @((New-StoppedTask))
        [void](Invoke-Resolve -Root $root -Api $api)
        $first = Get-StoredTask -Api $api
        Assert-Equal -Expected "stopped" -Actual ([string]$first.state) -Because "still stopped"
        Assert-True -Condition (([string]$first.reason).StartsWith("entegrasyon dalında çakışma") -and ([string]$first.reason).Contains("(1/2)")) -Because "the first failure is counted and the task stays the Proje Yöneticisi's: $([string]$first.reason)"
        [void](Invoke-Resolve -Root $root -Api $api)
        $second = Get-StoredTask -Api $api
        Assert-True -Condition (([string]$second.reason).StartsWith("Danışman'a iletildi: ") -and ([string]$second.reason).Contains("iki çözüm denemesi")) -Because "the second goes to the Danışman: $([string]$second.reason)"
        Assert-Equal -Expected $before -Actual (Git -Root $root -Arguments @("rev-parse", "integrate/$cycleId")) -Because "the integration branch is unchanged"
    }

    Test-Case "(7) both sides adding the same new file with different text (add/add, no base) is escalated as not additive, never glued together" {
        $root = New-Sandbox
        Edit-Branch -Root $root -Branch "integrate/$cycleId" -Files @{ "team/plans/x.md" = "# plan`nöteki işin metni`n" } -Message "the other task"
        Edit-Branch -Root $root -Branch "team/$cycleId/worker-the-task" -Files @{ "team/plans/x.md" = "# plan`nbu işin metni`n" } -Message "the task"
        $before = Git -Root $root -Arguments @("rev-parse", "integrate/$cycleId")
        $api = Start-FakeApi -Tasks @((New-StoppedTask))
        [void](Invoke-Resolve -Root $root -Api $api)
        $stored = Get-StoredTask -Api $api
        Assert-Equal -Expected "stopped" -Actual ([string]$stored.state) -Because "the task stays stopped ($([string]$stored.reason))"
        Assert-True -Condition (([string]$stored.reason).StartsWith("Danışman'a iletildi: ") -and ([string]$stored.reason).Contains("ekleme değil") -and ([string]$stored.reason).Contains("team/plans/x.md")) -Because "escalated as not additive, naming the file: $([string]$stored.reason)"
        Assert-Equal -Expected $before -Actual (Git -Root $root -Arguments @("rev-parse", "integrate/$cycleId")) -Because "the integration branch is unchanged"
    }

    Test-Case "(8) the task's own test red on the merged tree, guards green: escalated naming that test; the integration branch is unchanged" {
        $root = New-Sandbox
        Edit-Branch -Root $root -Branch "integrate/$cycleId" -Files @{ "src/notes.txt" = "bir`niki`nöteki işin satırı`n" } -Message "the other task"
        Edit-Branch -Root $root -Branch "team/$cycleId/worker-the-task" -Files @{
            "src/notes.txt" = "bir`niki`nbu işin satırı`n"
            "scripts/tests/own.tests.ps1" = "Write-Host 'FAIL own test'`nexit 1`n"
        } -Message "the task with its own test"
        $before = Git -Root $root -Arguments @("rev-parse", "integrate/$cycleId")
        $api = Start-FakeApi -Tasks @((New-StoppedTask))
        [void](Invoke-Resolve -Root $root -Api $api)
        $stored = Get-StoredTask -Api $api
        Assert-Equal -Expected "stopped" -Actual ([string]$stored.state) -Because "the task stays stopped ($([string]$stored.reason))"
        Assert-True -Condition (([string]$stored.reason).StartsWith("Danışman'a iletildi: ") -and ([string]$stored.reason).Contains("koruyucu kırmızı")) -Because "escalated as a red guard: $([string]$stored.reason)"
        Assert-True -Condition (([string]$stored.reason).Contains("işin kendi testi kırmızı (scripts/tests/own.tests.ps1)")) -Because "the reason names the task's own test: $([string]$stored.reason)"
        Assert-True -Condition (-not ([string]$stored.reason).Contains("notlar bozuk")) -Because "the guard of guards.json was green: $([string]$stored.reason)"
        Assert-Equal -Expected $before -Actual (Git -Root $root -Arguments @("rev-parse", "integrate/$cycleId")) -Because "the integration branch is unchanged"
    }

    # The measured hunk of 2026-10-07 (services/api/app/voice/realtime_sessions/routes.py,
    # integrate/d20261007 vs 5de7fe4f, merge base 93122258): the integration side changed the
    # first import, the task the second.
    $importBase = @("from fastapi import APIRouter, Depends, HTTPException, Request", "from pydantic import BaseModel, ConfigDict, Field, field_validator")
    $importOurs = @("from fastapi import APIRouter, Depends, HTTPException, Request, Response", "from pydantic import BaseModel, ConfigDict, Field, field_validator")
    $importTheirs = @("from fastapi import APIRouter, Depends, HTTPException, Request", "from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator")

    Test-Case "(9a) disjoint edits in one hunk - ours changes line 1, theirs line 2 (the measured routes.py imports) - are merged: ours' line 1 + theirs' line 2" {
        $hunk = Resolve-TeamDutyHunk -Base $importBase -Ours $importOurs -Theirs $importTheirs
        Assert-True -Condition $hunk.Ok -Because "each base line is changed by one side only"
        Assert-Equal -Expected ($importOurs[0] + "|" + $importTheirs[1]) -Actual (@($hunk.Lines) -join "|") -Because "each line comes from the side that changed it"
        $swapped = Resolve-TeamDutyHunk -Base $importBase -Ours $importTheirs -Theirs $importOurs
        Assert-True -Condition $swapped.Ok -Because "the same with the sides swapped"
        Assert-Equal -Expected ($importOurs[0] + "|" + $importTheirs[1]) -Actual (@($swapped.Lines) -join "|") -Because "the side swap gives the same union"
    }

    Test-Case "(9b) the same line changed by both sides differently is not merged" {
        $hunk = Resolve-TeamDutyHunk -Base $importBase -Ours @($importOurs[0], "from pydantic import BaseModel") -Theirs $importTheirs
        Assert-True -Condition (-not $hunk.Ok) -Because "line 2 is changed by both sides: $(@($hunk.Lines) -join '|')"
        $one = Resolve-TeamDutyHunk -Base @("x = 1") -Ours @("x = 2") -Theirs @("x = 3")
        Assert-True -Condition (-not $one.Ok) -Because "a one-line hunk changed by both sides: $(@($one.Lines) -join '|')"
    }

    Test-Case "(9c) one side deleting a line the other leaves is not merged" {
        $hunk = Resolve-TeamDutyHunk -Base $importBase -Ours $importOurs -Theirs @($importBase[0])
        Assert-True -Condition (-not $hunk.Ok) -Because "theirs deletes line 2: $(@($hunk.Lines) -join '|')"
        $other = Resolve-TeamDutyHunk -Base @("a", "b", "c") -Ours @("A", "b", "c") -Theirs @("a", "c")
        Assert-True -Condition (-not $other.Ok) -Because "theirs deletes b while ours edits a: $(@($other.Lines) -join '|')"
    }

    Test-Case "(9d) end to end: a task editing the line next to an integration-side edit is merged, integrate/<cycle> fast-forwarded, both changes kept" {
        $root = New-Sandbox
        Edit-Branch -Root $root -Branch "integrate/$cycleId" -Files @{ "src/notes.txt" = "bir - öteki iş`niki`n" } -Message "the other task"
        Edit-Branch -Root $root -Branch "team/$cycleId/worker-the-task" -Files @{ "src/notes.txt" = "bir`niki - bu iş`n" } -Message "the task"
        $before = Git -Root $root -Arguments @("rev-parse", "integrate/$cycleId")
        $taskTip = Git -Root $root -Arguments @("rev-parse", "team/$cycleId/worker-the-task")
        $conflict = Invoke-TeamGit -WorkingDirectory $root -Arguments @("merge-tree", "--write-tree", "integrate/$cycleId", "team/$cycleId/worker-the-task")
        Assert-True -Condition (-not $conflict.Success) -Because "git itself conflicts on the adjacent lines (else the case proves nothing)"
        $api = Start-FakeApi -Tasks @((New-StoppedTask))
        $results = Invoke-Resolve -Root $root -Api $api
        $stored = Get-StoredTask -Api $api
        Assert-Equal -Expected "merged" -Actual ([string]$stored.state) -Because "the store holds the task merged ($([string]$stored.reason))"
        Assert-Equal -Expected "merged" -Actual ([string]@($results)[0].Outcome) -Because "the duty says what it did"
        Assert-Equal -Expected $before -Actual (Git -Root $root -Arguments @("rev-parse", "integrate/$cycleId^1")) -Because "integrate/<cycle> is fast-forwarded: its old tip is the merge's first parent"
        Assert-Equal -Expected $taskTip -Actual (Git -Root $root -Arguments @("rev-parse", "integrate/$cycleId^2")) -Because "the merge's second parent is the task's tip"
        $notes = (Get-FileAt -Root $root -Revision "integrate/$cycleId" -Path "src/notes.txt") -replace "`r`n", "`n"
        Assert-Equal -Expected "bir - öteki iş`niki - bu iş`n" -Actual $notes -Because "the file holds both changes"
    }

    Test-Case "a guards.json entry appended by both sides keeps both entries and stays valid JSON (the comma between them is written)" {
        $text = "{`n  `"guards`": [`n    {`n      `"id`": `"a`"`n<<<<<<< HEAD`n    },`n    {`n      `"id`": `"ours`"`n    }`n||||||| base`n    }`n=======`n    },`n    {`n      `"id`": `"theirs`"`n    }`n>>>>>>> task`n  ]`n}`n"
        $resolved = Resolve-TeamDutyConflictText -Text $text -Json $true
        Assert-True -Condition $resolved.Ok -Because "the union is additive"
        $ids = @((ConvertFrom-Json -InputObject $resolved.Text).guards | ForEach-Object { $_.id })
        Assert-Equal -Expected "a,ours,theirs" -Actual ($ids -join ",") -Because "both entries are kept, in order: $($resolved.Text)"
    }

    Test-Case "a task the Danışman already has, or stopped for another reason, is left alone" {
        $root = New-Sandbox
        $api = Start-FakeApi -Tasks @((New-StoppedTask -Reason "Danışman'a iletildi: başka bir şey"))
        $results = Invoke-Resolve -Root $root -Api $api
        Assert-Equal -Expected 0 -Actual @($results).Count -Because "nothing was taken"
        Assert-Equal -Expected "Danışman'a iletildi: başka bir şey" -Actual ([string](Get-StoredTask -Api $api).reason) -Because "the reason is as it was"
    }

    Test-Case "the duty's decision file may name resolve_integration (Test-TeamDuty, scripts/lib/TeamQueue.ps1)" {
        . (Join-Path $repoRoot "scripts\lib\TeamArea.ps1")
        $queue = [pscustomobject]@{ version = 1; tasks = @((New-StoppedTask)) }
        $decision = [pscustomobject]@{ task = "the-task"; action = "resolve_integration"; reason = "eklemeli çakışma: Proje Yöneticisi çözer" }
        $problems = @(Test-TeamDuty -Decisions @($decision) -Listed @("the-task") -Queue $queue)
        Assert-Equal -Expected 0 -Actual @($problems).Count -Because ($problems -join "; ")
    }
}
finally {
    foreach ($process in $fakeApis) { try { if (-not $process.HasExited) { $process.Kill() } } catch { } }
    foreach ($path in $sandboxes) {
        if (Test-Path -LiteralPath $path) {
            [void](Invoke-TeamGit -WorkingDirectory $path -Arguments @("worktree", "prune"))
            Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host ""
Write-Host "passed: $script:Passes, failed: $script:Failures"
if ($script:Failures -gt 0) { exit 1 }
exit 0
