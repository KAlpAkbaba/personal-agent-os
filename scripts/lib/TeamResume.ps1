<#
.SYNOPSIS
    A run cut by the usage limit goes on where it stopped: the session id, the go-on decision
    and the resume command line (team/plans/limit-resume-session-adr.md).

.DESCRIPTION
    Today every run starts with --no-session-persistence, and a limited run is started again
    from the top with the same long prompt in a new session (2026-10-03/04: nine cuts, about
    19 000 s). This file decides, from what a run left behind, whether the next part resumes
    that session or starts afresh, and builds the arguments for either:

      New-TeamRunSessionId       a new lower-case GUID for a run;
      Get-TeamRunSessionArgs     '--session-id <uuid>', in place of --no-session-persistence;
      Select-TeamResumePlan      'resume' only for a usage limit, a recorded session of the
                                 same role and a run of at least ten minutes; else 'fresh';
      Get-TeamResumeArgs         '--resume <id or .jsonl path>' '--model <model>' and the
                                 short go-on prompt;
      Get-TeamProjectDirName     the folder Claude Code names after a working directory;
      Get-TeamSessionFilePath    <account>/projects/<project>/<uuid>.jsonl;
      Remove-TeamRunSessionFiles deletes a closed card's session files.

    Pure functions: no process is started here (no claude, no git). The ONE side effect is
    Remove-TeamRunSessionFiles's deletion, and only of a '.jsonl' under a given account
    directory (KVKK: a session file carries the repository's content).

    Dot-source; StrictMode-safe; Windows PowerShell 5.1.
#>

Set-StrictMode -Version Latest

$script:TeamResumeLimitReason = "usage_limit"
$script:TeamResumeMinSeconds = 600
$script:TeamResumeGuidPattern = '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
$script:TeamResumePrompt = "Limit kalktı; kaldığın yerden devam et, bitmiş adımları yeniden yapma; son adımın sonucunu doğrula."

function New-TeamRunSessionId {
    <# A new session id for one run: a GUID, lower case, with hyphens. #>
    return [guid]::NewGuid().ToString("D").ToLowerInvariant()
}

function Test-TeamRunSessionId {
    param([string]$SessionId)
    return ([string]$SessionId -cmatch $script:TeamResumeGuidPattern)
}

function Get-TeamRunSessionArgs {
    <# The arguments that keep a run's session under a known id. They take the place of
       --no-session-persistence in Get-TeamRunArguments. #>
    param([Parameter(Mandatory = $true)][string]$SessionId)
    if (-not (Test-TeamRunSessionId -SessionId $SessionId)) { throw "'$SessionId' is not a lower-case UUID: --session-id takes only a valid UUID" }
    return @("--session-id", $SessionId)
}

function Select-TeamResumePlan {
    <#
    .SYNOPSIS
        Resume the limited run's session, or start the next part afresh.
    .OUTPUTS
        Mode 'resume' | 'fresh'; ResumeTarget (the id on the same account, the .jsonl's full
        path on another, "" when fresh); Reason (one Turkish line for the cycle report).
    #>
    param(
        [string]$RunSession = "",
        [double]$ElapsedSeconds = 0,
        [string]$StopReason = "",
        [bool]$SameAccount = $true,
        [string]$SessionFile = "",
        # The role of the run about to start, and the role that owned the session. The
        # inspector resumes only its own earlier session, never the worker's.
        [string]$Role = "",
        [string]$SessionRole = "",
        [double]$MinSeconds = $script:TeamResumeMinSeconds,
        # The part that just stopped (1 = the first run of the card).
        [int]$Part = 1
    )
    $fresh = {
        param([string]$Why)
        return [pscustomobject]@{ Mode = "fresh"; ResumeTarget = ""; Reason = "baştan: $Why" }
    }
    if ([string]$StopReason -ne $script:TeamResumeLimitReason) {
        $said = if ($StopReason) { $StopReason } else { "bilinmiyor" }
        return (& $fresh "durma nedeni kullanım limiti değil ($said)")
    }
    if (-not $RunSession) { return (& $fresh "koşunun kayıtlı oturumu yok") }
    if (-not (Test-TeamRunSessionId -SessionId $RunSession)) { return (& $fresh "kayıtlı oturum kimliği geçersiz") }
    if (-not $Role -or [string]$Role -ne [string]$SessionRole) {
        $owner = if ($SessionRole) { $SessionRole } else { "bilinmeyen" }
        return (& $fresh "oturum $owner rolünün, $Role yalnız kendi oturumunu sürdürür")
    }
    if ($ElapsedSeconds -lt $MinSeconds) {
        $minutes = [Math]::Round($MinSeconds / 60)
        return (& $fresh ("koşu {0} dakikadan kısa ({1} sn)" -f $minutes, [Math]::Round($ElapsedSeconds)))
    }
    $target = $RunSession
    $where = ""
    if (-not $SameAccount) {
        if (-not $SessionFile -or -not (Test-Path -LiteralPath $SessionFile -PathType Leaf)) {
            return (& $fresh "hesap değişti, oturum dosyası bulunamadı")
        }
        $target = [System.IO.Path]::GetFullPath($SessionFile)
        $where = ", öbür hesabın dosyasından"
    }
    return [pscustomobject]@{
        Mode = "resume"; ResumeTarget = $target
        Reason = ("devam etti (oturum {0}, {1}. parça{2})" -f $RunSession, ($Part + 1), $where)
    }
}

function Get-TeamResumeArgs {
    <# The arguments and the prompt of a resumed part. The model is named again: a resumed
       part may run on the next model down. #>
    param(
        [Parameter(Mandatory = $true)]$Plan,
        [Parameter(Mandatory = $true)][string]$Model
    )
    if ([string]$Plan.Mode -ne "resume" -or -not [string]$Plan.ResumeTarget) { throw "the plan is not a resume: $($Plan.Reason)" }
    return [pscustomobject]@{
        Arguments = @("--resume", [string]$Plan.ResumeTarget, "--model", $Model)
        Prompt = $script:TeamResumePrompt
    }
}

function Get-TeamProjectDirName {
    <# The folder Claude Code keeps a working directory's sessions in: every character that is
       not an ASCII letter or digit becomes '-' (C:\Users\alpak -> C--Users-alpak). -creplace, not
       -replace: under tr-TR the case-insensitive class drops 'I' (E:\AI -> E--A-). #>
    param([Parameter(Mandatory = $true)][string]$Path)
    return ($Path -creplace '[^A-Za-z0-9]', '-')
}

function Get-TeamSessionFilePath {
    <# <account dir>\projects\<project folder>\<uuid>.jsonl. -ProjectDir is the folder's name
       (Get-TeamProjectDirName), not the working directory. #>
    param(
        [Parameter(Mandatory = $true)][string]$AccountDir,
        [Parameter(Mandatory = $true)][string]$ProjectDir,
        [Parameter(Mandatory = $true)][string]$SessionId
    )
    if (-not (Test-TeamRunSessionId -SessionId $SessionId)) { throw "'$SessionId' is not a lower-case UUID" }
    if ($ProjectDir -match '[\\/:]') { throw "'$ProjectDir' is a path, not a project folder name" }
    return (Join-Path (Join-Path (Join-Path $AccountDir "projects") $ProjectDir) "$SessionId.jsonl")
}

function Remove-TeamRunSessionFiles {
    <#
    .SYNOPSIS
        Delete a closed card's session files (KVKK: they carry the repository's content).
    .DESCRIPTION
        THE side effect of this file. A path is deleted only when it ends in '.jsonl' and,
        resolved in full ('..' included), lies under one of -AccountDirs. Any other path is
        left alone with a warning. Returns Removed, Refused and Missing (the full paths).
    #>
    [CmdletBinding()]
    param(
        [string[]]$Paths = @(),
        [Parameter(Mandatory = $true)][string[]]$AccountDirs
    )
    $roots = New-Object System.Collections.ArrayList
    foreach ($dir in @($AccountDirs)) {
        if (-not $dir) { continue }
        $full = [System.IO.Path]::GetFullPath($dir).TrimEnd('\', '/')
        # A drive root is no account directory: everything would lie under it.
        if ($full.Length -le 3) { Write-Warning "hesap dizini sayılmadı (sürücü kökü): $dir"; continue }
        [void]$roots.Add($full + '\')
    }
    $removed = New-Object System.Collections.ArrayList
    $refused = New-Object System.Collections.ArrayList
    $missing = New-Object System.Collections.ArrayList
    foreach ($path in @($Paths)) {
        if (-not $path) { continue }
        $full = [System.IO.Path]::GetFullPath($path)
        $under = $false
        foreach ($root in $roots) {
            if ($full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) { $under = $true; break }
        }
        if (-not $under -or -not [string]::Equals([System.IO.Path]::GetExtension($full), ".jsonl", [System.StringComparison]::OrdinalIgnoreCase)) {
            Write-Warning "oturum dosyası silinmedi (hesap dizini dışında ya da .jsonl değil): $full"
            [void]$refused.Add($full)
            continue
        }
        if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { [void]$missing.Add($full); continue }
        [System.IO.File]::Delete($full)
        [void]$removed.Add($full)
    }
    return [pscustomobject]@{ Removed = @($removed.ToArray()); Refused = @($refused.ToArray()); Missing = @($missing.ToArray()) }
}
