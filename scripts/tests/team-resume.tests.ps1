<#
.SYNOPSIS
    A run cut by the usage limit goes on where it stopped: scripts/lib/TeamResume.ps1.

.DESCRIPTION
    The groups, by the prefix of a case's name:

      plan        Select-TeamResumePlan: 'resume' only for a usage limit, a recorded session,
                  a run of at least ten minutes and the same role; the other account's
                  session by the full path of its .jsonl; everything else 'fresh';
      args        Get-TeamRunSessionArgs (--session-id in place of --no-session-persistence),
                  Get-TeamResumeArgs (--resume, --model, the short go-on prompt) and
                  New-TeamRunSessionId;
      path        Get-TeamProjectDirName and Get-TeamSessionFilePath;
      remove      Remove-TeamRunSessionFiles in a TEMP sandbox: only a .jsonl under a given
                  account directory is deleted; the result follows the disk (Failed for what
                  stayed), never throws, refuses a junction and a drive root.

    The library starts no process; the wiring into TeamRun.ps1 / cycle.ps1 is another card
    (team/plans/limit-resume-session-adr.md names its lines).

    Run: powershell -NoProfile -File scripts\tests\team-resume.tests.ps1
#>

[CmdletBinding()]
param(
    # Runs only the cases whose name matches this pattern (the mutation proofs re-run a slice).
    [string]$Filter = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\TeamResume.ps1")

$script:Failures = 0
$script:Passes = 0

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

$session = "0aed973e-f135-467d-b435-42864d0319e7"
$sandbox = Join-Path ([System.IO.Path]::GetTempPath()) ("team-resume-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
[void](New-Item -ItemType Directory -Path $sandbox)

try {
    # ------------------------------------------------------------------ plan

    Test-Case "plan (a): a limit, a recorded session and 700 s go on in the same session, on the same model, with the short prompt" {
        $plan = Select-TeamResumePlan -RunSession $session -ElapsedSeconds 700 -StopReason "usage_limit" -SameAccount $true -SessionFile "" -Role "worker" -SessionRole "worker"
        Assert-Equal -Expected "resume" -Actual $plan.Mode -Because "every condition holds"
        Assert-Equal -Expected $session -Actual $plan.ResumeTarget -Because "the same account resumes by the id"
        Assert-True -Condition ($plan.Reason -match "devam etti \(oturum $session") -Because "the report line names the session: $($plan.Reason)"
        $resume = Get-TeamResumeArgs -Plan $plan -Model "claude-sonnet-5-5"
        $line = @($resume.Arguments) -join " "
        Assert-True -Condition ($line -match "--resume $session( |$)") -Because "--resume and the id: $line"
        Assert-True -Condition ($line -match "--model claude-sonnet-5-5( |$)") -Because "the model goes with the resume: $line"
        Assert-True -Condition ($resume.Prompt -match "^Limit kalktı; kaldığın yerden devam et") -Because "the go-on prompt: $($resume.Prompt)"
        Assert-True -Condition ($resume.Prompt -notmatch "Task card|# Run") -Because "never the first run's long prompt"
        Assert-True -Condition ($resume.Prompt.Length -lt 200) -Because "short: $($resume.Prompt.Length)"
    }

    Test-Case "plan (b): a limit after 300 s starts afresh and says the ten-minute threshold" {
        $plan = Select-TeamResumePlan -RunSession $session -ElapsedSeconds 300 -StopReason "usage_limit" -SameAccount $true -SessionFile "" -Role "worker" -SessionRole "worker"
        Assert-Equal -Expected "fresh" -Actual $plan.Mode -Because "reloading the context costs more than redoing five minutes"
        Assert-Equal -Expected "" -Actual $plan.ResumeTarget -Because "nothing to resume"
        Assert-True -Condition ($plan.Reason -match "10 dakikadan kısa") -Because "the reason names the threshold: $($plan.Reason)"
    }

    Test-Case "plan (b2): exactly 600 s is ten minutes: it goes on (>= MinSeconds), 599 s does not" {
        $plan = Select-TeamResumePlan -RunSession $session -ElapsedSeconds 600 -StopReason "usage_limit" -SameAccount $true -SessionFile "" -Role "worker" -SessionRole "worker"
        Assert-Equal -Expected "resume" -Actual $plan.Mode -Because "the threshold itself resumes: $($plan.Reason)"
        $short = Select-TeamResumePlan -RunSession $session -ElapsedSeconds 599 -StopReason "usage_limit" -SameAccount $true -SessionFile "" -Role "worker" -SessionRole "worker"
        Assert-Equal -Expected "fresh" -Actual $short.Mode -Because "one second short"
    }

    Test-Case "plan (c): a limit with no recorded session starts afresh" {
        $plan = Select-TeamResumePlan -RunSession "" -ElapsedSeconds 2000 -StopReason "usage_limit" -SameAccount $true -SessionFile "" -Role "worker" -SessionRole "worker"
        Assert-Equal -Expected "fresh" -Actual $plan.Mode -Because "no run_session"
        Assert-True -Condition ($plan.Reason -match "oturum") -Because "the reason says why: $($plan.Reason)"
    }

    Test-Case "plan (d): an error or a timeout is never resumed, however long the run was" {
        foreach ($reason in @("error", "timeout", "")) {
            $plan = Select-TeamResumePlan -RunSession $session -ElapsedSeconds 2000 -StopReason $reason -SameAccount $true -SessionFile "" -Role "worker" -SessionRole "worker"
            Assert-Equal -Expected "fresh" -Actual $plan.Mode -Because "stop reason '$reason' is not the limit"
        }
    }

    Test-Case "plan (e): on another account the session is resumed by the full path of its .jsonl; no file, afresh" {
        $file = Join-Path $sandbox "$session.jsonl"
        [System.IO.File]::WriteAllText($file, "{}`n", $utf8)
        $plan = Select-TeamResumePlan -RunSession $session -ElapsedSeconds 700 -StopReason "usage_limit" -SameAccount $false -SessionFile $file -Role "worker" -SessionRole "worker"
        Assert-Equal -Expected "resume" -Actual $plan.Mode -Because "the file is there"
        Assert-Equal -Expected $file -Actual $plan.ResumeTarget -Because "the full path, not the id"
        $line = @((Get-TeamResumeArgs -Plan $plan -Model "claude-opus-5-5").Arguments) -join " "
        Assert-True -Condition ($line.Contains("--resume $file")) -Because "the path is what --resume gets: $line"
        $missing = Select-TeamResumePlan -RunSession $session -ElapsedSeconds 700 -StopReason "usage_limit" -SameAccount $false -SessionFile (Join-Path $sandbox "yok.jsonl") -Role "worker" -SessionRole "worker"
        Assert-Equal -Expected "fresh" -Actual $missing.Mode -Because "no file"
        $empty = Select-TeamResumePlan -RunSession $session -ElapsedSeconds 700 -StopReason "usage_limit" -SameAccount $false -SessionFile "" -Role "worker" -SessionRole "worker"
        Assert-Equal -Expected "fresh" -Actual $empty.Mode -Because "no path"
    }

    Test-Case "plan (f): the inspector never resumes the worker's session; its own, yes" {
        $plan = Select-TeamResumePlan -RunSession $session -ElapsedSeconds 2000 -StopReason "usage_limit" -SameAccount $true -SessionFile "" -Role "inspector" -SessionRole "worker"
        Assert-Equal -Expected "fresh" -Actual $plan.Mode -Because "the inspector stays independent"
        Assert-True -Condition ($plan.Reason -match "rol") -Because "the reason names the role: $($plan.Reason)"
        $own = Select-TeamResumePlan -RunSession $session -ElapsedSeconds 2000 -StopReason "usage_limit" -SameAccount $true -SessionFile "" -Role "inspector" -SessionRole "inspector"
        Assert-Equal -Expected "resume" -Actual $own.Mode -Because "its own earlier session"
        $noRole = Select-TeamResumePlan -RunSession $session -ElapsedSeconds 2000 -StopReason "usage_limit" -SameAccount $true -SessionFile "" -Role "inspector" -SessionRole ""
        Assert-Equal -Expected "fresh" -Actual $noRole.Mode -Because "a session of no known role is nobody's"
        # Both empty are equal, and still nobody's: a run that names no role resumes nothing.
        $neither = Select-TeamResumePlan -RunSession $session -ElapsedSeconds 2000 -StopReason "usage_limit" -SameAccount $true -SessionFile "" -Role "" -SessionRole ""
        Assert-Equal -Expected "fresh" -Actual $neither.Mode -Because "no role on either side: $($neither.Reason)"
    }

    Test-Case "plan: the part number counts on, and a 'fresh' plan gives no resume arguments" {
        $plan = Select-TeamResumePlan -RunSession $session -ElapsedSeconds 700 -StopReason "usage_limit" -SameAccount $true -SessionFile "" -Role "worker" -SessionRole "worker" -Part 3
        Assert-True -Condition ($plan.Reason -match "4\. parça") -Because "the third part's limit starts the fourth: $($plan.Reason)"
        $fresh = Select-TeamResumePlan -RunSession "" -ElapsedSeconds 700 -StopReason "usage_limit" -SameAccount $true -SessionFile "" -Role "worker" -SessionRole "worker"
        $threw = $false
        try { [void](Get-TeamResumeArgs -Plan $fresh -Model "claude-opus-5-5") } catch { $threw = $true }
        Assert-True -Condition $threw -Because "a fresh plan has nothing to resume"
    }

    # ------------------------------------------------------------------ args

    Test-Case "args (g): --session-id <uuid> in place of --no-session-persistence; a new id each call" {
        $one = New-TeamRunSessionId
        $two = New-TeamRunSessionId
        $guid = '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        Assert-True -Condition ($one -cmatch $guid) -Because "lower-case GUID: $one"
        Assert-True -Condition ($two -cmatch $guid) -Because "lower-case GUID: $two"
        Assert-True -Condition ($one -ne $two) -Because "two calls, two ids"
        $arguments = @(Get-TeamRunSessionArgs -SessionId $one)
        Assert-Equal -Expected "--session-id $one" -Actual ($arguments -join " ") -Because "the two arguments"
        Assert-True -Condition (($arguments -join " ") -notmatch "no-session-persistence") -Because "the session is kept"
        $threw = $false
        try { [void](Get-TeamRunSessionArgs -SessionId "not-a-uuid") } catch { $threw = $true }
        Assert-True -Condition $threw -Because "the tool takes only a valid UUID"
    }

    # ------------------------------------------------------------------ path

    Test-Case "path (h): <account>/projects/<project>/<uuid>.jsonl, the project folder named as Claude Code names it" {
        Assert-Equal -Expected "E--AI-PersonalAgentOS-Claude-Autonomous-Build-Package-v1--claude-worktrees-team-d20261004-worker-limit-resume-session" `
            -Actual (Get-TeamProjectDirName -Path "E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1\.claude\worktrees\team\d20261004\worker-limit-resume-session") `
            -Because "every character but a letter or a digit is '-'"
        Assert-Equal -Expected "C--Users-alpak" -Actual (Get-TeamProjectDirName -Path "C:\Users\alpak") -Because "the home folder"
        $path = Get-TeamSessionFilePath -AccountDir "C:\Users\alpak\.claude-hesap2" -ProjectDir "C--Users-alpak" -SessionId $session
        Assert-Equal -Expected "C:\Users\alpak\.claude-hesap2\projects\C--Users-alpak\$session.jsonl" -Actual $path -Because "the session file"
    }

    # ------------------------------------------------------------------ remove

    Test-Case "remove (i): a .jsonl under an account directory is deleted; one outside it and a non-.jsonl are not" {
        $account = Join-Path $sandbox ".claude-hesap1"
        $project = Join-Path $account "projects\E--repo"
        [void](New-Item -ItemType Directory -Path $project -Force)
        $inside = Join-Path $project "$session.jsonl"
        $notJsonl = Join-Path $project "notes.txt"
        $outsideDir = Join-Path $sandbox "elsewhere"
        [void](New-Item -ItemType Directory -Path $outsideDir -Force)
        $outside = Join-Path $outsideDir "$session.jsonl"
        # A sibling whose name only starts like the account directory is outside it too.
        $siblingDir = Join-Path $sandbox ".claude-hesap1-copy"
        [void](New-Item -ItemType Directory -Path $siblingDir -Force)
        $sibling = Join-Path $siblingDir "$session.jsonl"
        $escape = Join-Path $project "..\..\..\elsewhere\$session.jsonl"
        foreach ($file in @($inside, $notJsonl, $outside, $sibling)) { [System.IO.File]::WriteAllText($file, "{}`n", $utf8) }
        $result = Remove-TeamRunSessionFiles -Paths @($inside, $notJsonl, $outside, $sibling, $escape) -AccountDirs @($account) -WarningAction SilentlyContinue
        Assert-True -Condition (-not (Test-Path -LiteralPath $inside)) -Because "the run's session file is gone"
        Assert-True -Condition (Test-Path -LiteralPath $notJsonl) -Because "not a .jsonl"
        Assert-True -Condition (Test-Path -LiteralPath $outside) -Because "outside every account directory"
        Assert-True -Condition (Test-Path -LiteralPath $sibling) -Because "a look-alike sibling is outside"
        Assert-Equal -Expected 1 -Actual @($result.Removed).Count -Because "one file deleted"
        Assert-Equal -Expected 4 -Actual @($result.Refused).Count -Because "four refused (the '..' path among them)"
    }

    Test-Case "remove (j): the session's <uuid>\tool-results folder goes with its .jsonl, even with no .jsonl left; no other folder" {
        $account = Join-Path $sandbox ".claude-hesap3"
        $project = Join-Path $account "projects\E--repo"
        $other = "7f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f"
        # With its .jsonl, and without one (--no-session-persistence still writes tool-results).
        foreach ($id in @($session, $other)) {
            $results = Join-Path $project "$id\tool-results"
            [void](New-Item -ItemType Directory -Path $results -Force)
            [System.IO.File]::WriteAllText((Join-Path $results "out.txt"), "repo content`n", $utf8)
        }
        [System.IO.File]::WriteAllText((Join-Path $project "$session.jsonl"), "{}`n", $utf8)
        # A .jsonl that is not named by a uuid takes no folder with it.
        $plain = Join-Path $project "notes"
        [void](New-Item -ItemType Directory -Path $plain -Force)
        [System.IO.File]::WriteAllText((Join-Path $project "notes.jsonl"), "{}`n", $utf8)
        # A uuid folder outside every account directory stays.
        $outsideDir = Join-Path $sandbox "elsewhere3\$session\tool-results"
        [void](New-Item -ItemType Directory -Path $outsideDir -Force)
        $paths = @((Join-Path $project "$session.jsonl"), (Join-Path $project "$other.jsonl"), (Join-Path $project "notes.jsonl"), (Join-Path $sandbox "elsewhere3\$session.jsonl"))
        $result = Remove-TeamRunSessionFiles -Paths $paths -AccountDirs @($account) -WarningAction SilentlyContinue
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $project $session))) -Because "the session's folder is gone with its .jsonl"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $project $other))) -Because "the folder goes even when the .jsonl was never written"
        Assert-True -Condition (Test-Path -LiteralPath $plain) -Because "a folder not named by a uuid stays"
        Assert-True -Condition (Test-Path -LiteralPath $outsideDir) -Because "outside every account directory"
        Assert-Equal -Expected 2 -Actual @($result.RemovedDirs).Count -Because "two session folders deleted"
    }

    # A session folder holding a read-only file: Directory.Delete throws. The result follows
    # the disk, not the call: the folder is Failed (with the error), never RemovedDirs, and the
    # next path of the same call is still deleted - under the default EAP and under 'Stop'
    # (cycle.ps1:155 runs with 'Stop').
    foreach ($eap in @("Continue", "Stop")) {
        Test-Case "remove (k): a folder that cannot be deleted (read-only file) is Failed, not RemovedDirs; the next path still goes (EAP=$eap)" {
            $account = Join-Path $sandbox ".claude-hesap-ro-$eap"
            $project = Join-Path $account "projects\E--repo"
            $stuck = Join-Path $project $session
            $results = Join-Path $stuck "tool-results"
            [void](New-Item -ItemType Directory -Path $results -Force)
            $locked = Join-Path $results "out.txt"
            [System.IO.File]::WriteAllText($locked, "repo content`n", $utf8)
            [System.IO.File]::SetAttributes($locked, [System.IO.FileAttributes]::ReadOnly)
            $next = "7f1c2d3e-4a5b-4c6d-8e9f-0a1b2c3d4e5f"
            $nextFile = Join-Path $project "$next.jsonl"
            [System.IO.File]::WriteAllText($nextFile, "{}`n", $utf8)
            $paths = @((Join-Path $project "$session.jsonl"), $nextFile)
            $result = $null
            $threw = ""
            $ErrorActionPreference = $eap
            try { $result = Remove-TeamRunSessionFiles -Paths $paths -AccountDirs @($account) -WarningAction SilentlyContinue }
            catch { $threw = $_.Exception.Message }
            finally { $ErrorActionPreference = "Stop" }
            if (Test-Path -LiteralPath $locked) { [System.IO.File]::SetAttributes($locked, [System.IO.FileAttributes]::Normal) }
            Assert-True -Condition (Test-Path -LiteralPath $stuck) -Because "the folder is still on disk (the sandbox's premise)"
            Assert-True -Condition (Test-Path -LiteralPath $locked) -Because "the read-only file was not silently unlocked and deleted"
            if ($result) { Assert-True -Condition (@($result.RemovedDirs) -notcontains $stuck) -Because "a folder on disk is never reported removed: $(@($result.RemovedDirs) -join ', ')" }
            Assert-Equal -Expected "" -Actual $threw -Because "the function never throws nor writes an error"
            $failed = @($result.Failed | Where-Object { $_.Path -eq $stuck })
            Assert-Equal -Expected 1 -Actual $failed.Count -Because "the folder is in Failed"
            Assert-True -Condition ([bool][string]$failed[0].Error) -Because "Failed carries the error"
            Assert-True -Condition (-not (Test-Path -LiteralPath $nextFile)) -Because "the next path of the same call is still deleted"
            Assert-True -Condition (@($result.Removed) -contains $nextFile) -Because "and reported removed"
        }
    }

    Test-Case "remove (l): a session folder that is itself a junction is refused; its target and the target's content stay" {
        $account = Join-Path $sandbox ".claude-hesap-jn"
        $project = Join-Path $account "projects\E--repo"
        [void](New-Item -ItemType Directory -Path $project -Force)
        $target = Join-Path $sandbox "junction-target"
        [void](New-Item -ItemType Directory -Path $target -Force)
        $keep = Join-Path $target "keep.txt"
        [System.IO.File]::WriteAllText($keep, "not the session's`n", $utf8)
        $link = Join-Path $project $session
        [void](New-Item -ItemType Junction -Path $link -Value $target)
        try {
            $result = Remove-TeamRunSessionFiles -Paths @((Join-Path $project "$session.jsonl")) -AccountDirs @($account) -WarningAction SilentlyContinue
            Assert-True -Condition (@($result.Refused) -contains $link) -Because "the junction is refused: $(@($result.Refused) -join ', ')"
            Assert-True -Condition (@($result.RemovedDirs) -notcontains $link) -Because "never reported removed"
            Assert-True -Condition (Test-Path -LiteralPath $link) -Because "the junction itself is left alone"
            Assert-True -Condition (Test-Path -LiteralPath $keep) -Because "the target's content stays"
        }
        finally {
            # Drop the link only (rmdir on a junction never touches its target).
            if (Test-Path -LiteralPath $link) { [System.IO.Directory]::Delete($link, $false) }
        }
    }

    Test-Case "remove (m): a drive root is no account directory: a .jsonl under it is refused" {
        $file = Join-Path $sandbox "$session.jsonl"
        [System.IO.File]::WriteAllText($file, "{}`n", $utf8)
        $root = [System.IO.Path]::GetPathRoot($sandbox)
        $result = Remove-TeamRunSessionFiles -Paths @($file) -AccountDirs @($root) -WarningAction SilentlyContinue
        Assert-True -Condition (Test-Path -LiteralPath $file) -Because "the drive root covers nothing"
        Assert-Equal -Expected 1 -Actual @($result.Refused).Count -Because "refused"
        Assert-Equal -Expected 0 -Actual @($result.Removed).Count -Because "nothing deleted"
    }
}
finally {
    Remove-Item -LiteralPath $sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host ("team-resume: {0} PASS, {1} FAIL" -f $script:Passes, $script:Failures)
if ($script:Failures -gt 0) { exit 1 }
exit 0
