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
                  account directory is deleted.

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
}
finally {
    Remove-Item -LiteralPath $sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host ("team-resume: {0} PASS, {1} FAIL" -f $script:Passes, $script:Failures)
if ($script:Failures -gt 0) { exit 1 }
exit 0
