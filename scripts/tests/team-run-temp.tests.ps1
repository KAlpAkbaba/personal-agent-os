<#
.SYNOPSIS
    A role run's own temp folder (scripts/lib/TeamRun.ps1 Remove-TeamRunTemp and
    Invoke-TeamRunTempSweep): emptied when the run ends, the folder itself kept; empty old
    folders swept, never the one Git Bash has mounted as /tmp.

.DESCRIPTION
    Measured 2026-10-06 01:50: Git for Windows mounts /tmp as 'usertemp', resolved once from
    the TEMP of the first msys process of the logon session and shared by every msys process
    after it. A worker run's bash was that first process, the run's folder was deleted when the
    run ended, and every bash on the machine lost /tmp (mktemp failed in six gate tests).

    The cases, by number (the card's acceptance list):
      1  after Remove-TeamRunTemp the folder exists and is empty;
      2  a locked child file is named (a warning), not thrown;
      3  the sweep removes an empty folder older than 24 h;
      4  the sweep keeps the folder `mount` reports as /tmp (fake mount output);
      5  the sweep keeps a non-empty folder;
      and beside them: a young empty folder is kept, a mount that cannot be read keeps
      everything, and the real mount text is parsed.

    Every case has its own folder under %TEMP%; the mount is a fake unless the case says so.

    Run: powershell -NoProfile -File scripts\tests\team-run-temp.tests.ps1 [-Filter <regex>]
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
$script:TempRoot = Join-Path $env:TEMP ("pagentos-run-temp-tests-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
[void](New-Item -ItemType Directory -Path $script:TempRoot -Force)

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    if ($Filter -and $Name -notmatch $Filter) { return }
    try {
        & $Body
        $script:Passes++; Write-Host "  PASS  $Name"
    }
    catch {
        $script:Failures++
        Write-Host "  FAIL  $Name" -ForegroundColor Red
        Write-Host "        $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Assert-True {
    param([bool]$Condition, [string]$Because)
    if (-not $Condition) { throw $Because }
}

function New-CaseRoot {
    <# A run_temp_root of the case's own. #>
    $d = Join-Path $script:TempRoot ("r-" + [guid]::NewGuid().ToString("N").Substring(0, 10))
    [void](New-Item -ItemType Directory -Path $d -Force)
    return $d
}

function New-RunFolder {
    param([string]$Root, [string]$Name, [double]$AgeHours = 0, [switch]$WithFile)
    $d = Join-Path $Root $Name
    [void](New-Item -ItemType Directory -Path $d -Force)
    if ($WithFile) { [System.IO.File]::WriteAllText((Join-Path $d "left.txt"), "x") }
    if ($AgeHours -gt 0) { (Get-Item -LiteralPath $d).LastWriteTimeUtc = [datetime]::UtcNow.AddHours(-$AgeHours) }
    return $d
}

function Get-FakeMount {
    <# What Git's `mount` prints, with /tmp on the given folder. #>
    param([string]$TmpFolder)
    $posix = $TmpFolder -replace '\\', '/'
    return @(
        "C:/Program Files/Git on / type ntfs (binary,noacl,auto)",
        "C:/Program Files/Git/usr/bin on /bin type ntfs (binary,noacl,auto)",
        "$posix on /tmp type ntfs (binary,noacl,posix=0,usertemp)",
        "C: on /c type ntfs (binary,noacl,posix=0,user,noumount,auto)"
    ) -join "`n"
}

Test-Case "1 after Remove-TeamRunTemp the run's folder exists and is empty" {
    $root = New-CaseRoot
    $run = New-RunFolder -Root $root -Name "task-worker-11111111"
    [System.IO.File]::WriteAllText((Join-Path $run "a.txt"), "a")
    $sub = Join-Path $run "tmp.XXXX\deep"
    [void](New-Item -ItemType Directory -Path $sub -Force)
    [System.IO.File]::WriteAllText((Join-Path $sub "b.txt"), "b")
    Remove-TeamRunTemp -Path $run -ReadMount { Get-FakeMount -TmpFolder $run }
    Assert-True (Test-Path -LiteralPath $run -PathType Container) "the folder itself is kept (Git Bash may have it as /tmp)"
    $left = @(Get-ChildItem -LiteralPath $run -Force)
    Assert-True (@($left).Count -eq 0) "the folder is empty; left: $(@($left | ForEach-Object { $_.Name }) -join ', ')"
}

Test-Case "2 a locked child file is named, not thrown" {
    $root = New-CaseRoot
    $run = New-RunFolder -Root $root -Name "task-worker-22222222"
    $free = Join-Path $run "free.txt"
    $locked = Join-Path $run "held-open.log"
    [System.IO.File]::WriteAllText($free, "f")
    $stream = [System.IO.File]::Open($locked, [System.IO.FileMode]::Create, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    try {
        $warnings = $null
        Remove-TeamRunTemp -Path $run -ReadMount { $null } -WarningVariable warnings -WarningAction SilentlyContinue
        Assert-True (Test-Path -LiteralPath $run -PathType Container) "the folder is kept"
        Assert-True (-not (Test-Path -LiteralPath $free)) "the free file was removed beside the locked one"
        Assert-True (Test-Path -LiteralPath $locked) "the locked file is still there (it is held open)"
        $text = (@($warnings) | ForEach-Object { [string]$_ }) -join "`n"
        Assert-True ($text -match [regex]::Escape("held-open.log")) "a warning names the file left; warnings: <$text>"
    }
    finally { $stream.Dispose() }
}

Test-Case "3 the sweep removes an empty folder older than 24 h" {
    $root = New-CaseRoot
    $old = New-RunFolder -Root $root -Name "old-worker-33333333" -AgeHours 25
    $mounted = New-RunFolder -Root $root -Name "mounted-worker-33333334" -AgeHours 25
    Invoke-TeamRunTempSweep -Root $root -ReadMount { Get-FakeMount -TmpFolder $mounted }
    Assert-True (-not (Test-Path -LiteralPath $old)) "the empty 25-hour-old folder is gone"
}

Test-Case "4 the sweep keeps the folder mount reports as /tmp" {
    $root = New-CaseRoot
    $mounted = New-RunFolder -Root $root -Name "jarvis-calls-owner-worker-882d335e" -AgeHours 30
    $other = New-RunFolder -Root $root -Name "other-worker-44444444" -AgeHours 30
    Invoke-TeamRunTempSweep -Root $root -ReadMount { Get-FakeMount -TmpFolder $mounted }
    Assert-True (Test-Path -LiteralPath $mounted -PathType Container) "the /tmp folder is kept, old and empty as it is"
    Assert-True (-not (Test-Path -LiteralPath $other)) "the sweep did run: the other old empty folder is gone"
}

Test-Case "5 the sweep keeps a non-empty folder" {
    $root = New-CaseRoot
    $full = New-RunFolder -Root $root -Name "full-worker-55555555" -WithFile
    (Get-Item -LiteralPath $full).LastWriteTimeUtc = [datetime]::UtcNow.AddHours(-48)
    $empty = New-RunFolder -Root $root -Name "empty-worker-55555556" -AgeHours 48
    Invoke-TeamRunTempSweep -Root $root -ReadMount { Get-FakeMount -TmpFolder (Join-Path $root "elsewhere") }
    Assert-True (Test-Path -LiteralPath (Join-Path $full "left.txt")) "a folder with something in it is kept, with its file"
    Assert-True (-not (Test-Path -LiteralPath $empty)) "the sweep did run: the empty one beside it is gone"
}

Test-Case "6 the sweep keeps an empty folder younger than 24 h" {
    $root = New-CaseRoot
    $young = New-RunFolder -Root $root -Name "young-worker-66666666" -AgeHours 23
    Invoke-TeamRunTempSweep -Root $root -ReadMount { Get-FakeMount -TmpFolder (Join-Path $root "elsewhere") }
    Assert-True (Test-Path -LiteralPath $young -PathType Container) "a folder emptied within the day is kept"
}

Test-Case "7 a mount that cannot be read keeps every folder" {
    $root = New-CaseRoot
    $old = New-RunFolder -Root $root -Name "old-worker-77777777" -AgeHours 72
    Invoke-TeamRunTempSweep -Root $root -ReadMount { $null }
    Assert-True (Test-Path -LiteralPath $old -PathType Container) "bash could not be asked: nothing is removed"
    Invoke-TeamRunTempSweep -Root $root -ReadMount { "C:/Program Files/Git on / type ntfs (binary,noacl,auto)" }
    Assert-True (Test-Path -LiteralPath $old -PathType Container) "no /tmp line in the mount text: nothing is removed"
    Invoke-TeamRunTempSweep -Root $root -ReadMount { throw "bash.exe was not found" }
    Assert-True (Test-Path -LiteralPath $old -PathType Container) "a reader that throws: nothing is removed, nothing thrown"
}

Test-Case "8 the /tmp folder is read from mount text, forward slashes and case aside" {
    $text = Get-FakeMount -TmpFolder "E:\AI\tmp-team\Jarvis-Calls-Owner-worker-882d335e"
    $path = ConvertFrom-TeamGitBashMount -Text $text
    Assert-True ($path -eq "E:\AI\tmp-team\Jarvis-Calls-Owner-worker-882d335e") "the /tmp line's source as a Windows path; got <$path>"
    Assert-True ($null -eq (ConvertFrom-TeamGitBashMount -Text "")) "no text, no path"
    Assert-True ($null -eq (ConvertFrom-TeamGitBashMount -Text "C:/x on /tmpx type ntfs (binary)")) "/tmpx is not /tmp"
}

Test-Case "9 the real Git Bash mount text is read (this machine)" {
    $text = Read-TeamGitBashMount
    if ($null -eq $text) { throw "Git's bash.exe could not be asked for mount" }
    $path = ConvertFrom-TeamGitBashMount -Text $text
    Assert-True ($null -ne $path -and $path -match '^[A-Za-z]:\\') "the real mount names /tmp as a Windows folder; got <$path>"
}

Test-Case "a native tool that exits while a process it started holds its output: the call throws within the grace, never waits for ever (the cycle froze on it three times, 2026-10-06)" {
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $threw = $null
    # cmd exits at once; the ping it started in the background keeps cmd's stdout open for ~25 s.
    try { [void](Invoke-NativeProcess -FilePath (Join-Path $env:SystemRoot "System32\cmd.exe") -Arguments @("/c", "start", "/b", "ping", "-n", "25", "127.0.0.1") -TimeoutSeconds 20 -OutputGraceSeconds 3) }
    catch { $threw = [string]$_.Exception.Message }
    $seconds = $watch.Elapsed.TotalSeconds
    Get-CimInstance Win32_Process -Filter "Name='PING.EXE'" | Where-Object { ([string]$_.CommandLine) -match '-n 25 127\.0\.0\.1' } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch { } }
    if ($null -eq $threw) { throw "the call returned instead of saying its output stayed open" }
    if ($threw -notmatch "output stayed open") { throw "a different error: $threw" }
    # A hang guard, not the claim: the claim is the throw above.
    if ($seconds -gt 20) { throw "it waited $([Math]::Round($seconds)) s" }
}

# ---------------------------------------------------------------------------------------------
# A half-made worktree heals itself (card worktree-half-made-heals, 2026-10-06): on 6 October
# twelve cards stopped because 'git worktree add' was killed at 300 s and left either a folder
# whose admin entry has 'locked' + an empty 'index.lock' and no 'index', or a branch with no
# folder and a stale admin entry. Every case runs in a sandbox repository under %TEMP%; the real
# repository is never touched.
# ---------------------------------------------------------------------------------------------

function New-SandboxRepo {
    <# A throwaway repository with one commit on main; .claude/ ignored as in the real one. #>
    $repo = Join-Path $script:TempRoot ("g-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    [void](New-Item -ItemType Directory -Path $repo -Force)
    foreach ($a in @(@("init", "-q", "-b", "main"), @("config", "user.email", "sandbox@example.invalid"), @("config", "user.name", "sandbox"), @("config", "core.autocrlf", "false"))) {
        $r = Invoke-TeamGit -WorkingDirectory $repo -Arguments $a
        if (-not $r.Success) { throw "sandbox git $($a -join ' '): $($r.StdErr)" }
    }
    [System.IO.File]::WriteAllText((Join-Path $repo ".gitignore"), ".claude/`n")
    [System.IO.File]::WriteAllText((Join-Path $repo "a.txt"), "a`n")
    [void](New-Item -ItemType Directory -Path (Join-Path $repo "d") -Force)
    [System.IO.File]::WriteAllText((Join-Path $repo "d\b.txt"), "b`n")
    foreach ($a in @(@("add", "-A"), @("commit", "-q", "-m", "init"))) {
        $r = Invoke-TeamGit -WorkingDirectory $repo -Arguments $a
        if (-not $r.Success) { throw "sandbox git $($a -join ' '): $($r.StdErr)" }
    }
    return $repo
}

function Get-SandboxSha {
    param([string]$Repo, [string]$Ref)
    $r = Invoke-TeamGit -WorkingDirectory $Repo -Arguments @("rev-parse", "--verify", "--quiet", $Ref)
    if (-not $r.Success) { return $null }
    return $r.StdOut.Trim()
}

function Get-SandboxAdminDir {
    <# The worktree's admin folder under the common git dir (.git/worktrees/<name>). #>
    param([string]$Tree)
    $r = Invoke-TeamGit -WorkingDirectory $Tree -Arguments @("rev-parse", "--git-dir")
    if (-not $r.Success) { throw "rev-parse --git-dir failed in ${Tree}: $($r.StdErr)" }
    return ($r.StdOut.Trim() -replace '/', '\')
}

function Set-HalfMade {
    <# What a 'git worktree add' killed at 300 s left on 6 October: locked, an empty index.lock, no index. #>
    param([string]$AdminDir)
    $index = Join-Path $AdminDir "index"
    if (Test-Path -LiteralPath $index) { Remove-Item -LiteralPath $index -Force }
    [System.IO.File]::WriteAllText((Join-Path $AdminDir "locked"), "initializing")
    [System.IO.File]::WriteAllBytes((Join-Path $AdminDir "index.lock"), [byte[]]@())
}

Test-Case "wt-a a half-made worktree (locked, empty index.lock, no index, 0 commits, clean) is repaired and healthy" {
    $repo = New-SandboxRepo
    $branch = "team/sandbox/worker-a"
    $first = New-TeamWorktree -RepoRoot $repo -Branch $branch
    Set-HalfMade -AdminDir (Get-SandboxAdminDir -Tree $first.Path)
    Assert-True (-not (Test-TeamWorktreeHealthy -RepoRoot $repo -Path $first.Path -Branch $branch).Healthy) "the half-made tree reads as unhealthy"
    $second = New-TeamWorktree -RepoRoot $repo -Branch $branch
    Assert-True ($second.Created -eq $true) "the tree was made again (Created); note: <$($second.Note)>"
    Assert-True ($second.Note -match "onar") "the note says it was repaired; note: <$($second.Note)>"
    $health = Test-TeamWorktreeHealthy -RepoRoot $repo -Path $second.Path -Branch $branch
    Assert-True $health.Healthy "the repaired tree is healthy; reason: <$($health.Reason)>"
    Assert-True (Test-Path -LiteralPath (Join-Path $second.Path "d\b.txt")) "the files are checked out"
    # Each condition alone makes the tree unhealthy (a check that only sees the three together is not a check).
    $admin = Get-SandboxAdminDir -Tree $second.Path
    foreach ($one in @(@("locked", "locked"), @("index.lock", "index.lock"), @("no-index", "index"))) {
        $index = Join-Path $admin "index"
        $saved = [System.IO.File]::ReadAllBytes($index)
        if ($one[0] -eq "no-index") { Remove-Item -LiteralPath $index -Force }
        else { [System.IO.File]::WriteAllBytes((Join-Path $admin $one[0]), [byte[]]@()) }
        $alone = Test-TeamWorktreeHealthy -RepoRoot $repo -Path $second.Path -Branch $branch
        if ($one[0] -eq "no-index") { [System.IO.File]::WriteAllBytes($index, $saved) } else { Remove-Item -LiteralPath (Join-Path $admin $one[0]) -Force }
        Assert-True (-not $alone.Healthy -and $alone.Reason -match [regex]::Escape("'$($one[1])'")) "'$($one[0])' alone reads as unhealthy and is named; got Healthy=$($alone.Healthy) <$($alone.Reason)>"
    }
    Assert-True (Test-TeamWorktreeHealthy -RepoRoot $repo -Path $second.Path -Branch $branch).Healthy "healthy again once each condition is put back"
}

Test-Case "wt-b a half-made worktree whose branch has a commit is NOT touched" {
    $repo = New-SandboxRepo
    $branch = "team/sandbox/worker-b"
    $tree = (New-TeamWorktree -RepoRoot $repo -Branch $branch).Path
    [System.IO.File]::WriteAllText((Join-Path $tree "work.txt"), "the worker's work`n")
    foreach ($a in @(@("add", "work.txt"), @("commit", "-q", "-m", "work"))) { [void](Invoke-TeamGit -WorkingDirectory $tree -Arguments $a) }
    $sha = Get-SandboxSha -Repo $repo -Ref "refs/heads/$branch"
    Set-HalfMade -AdminDir (Get-SandboxAdminDir -Tree $tree)
    $threw = $null
    try { [void](New-TeamWorktree -RepoRoot $repo -Branch $branch) } catch { $threw = [string]$_.Exception.Message }
    Assert-True ($null -ne $threw -and $threw -match "yarım ağaç onarılmadı" -and $threw -match "1 commit") "it throws 'yarım ağaç onarılmadı ... 1 commit'; got <$threw>"
    Assert-True ((Get-SandboxSha -Repo $repo -Ref "refs/heads/$branch") -eq $sha) "the branch still has its commit"
    Assert-True (Test-Path -LiteralPath (Join-Path $tree "work.txt")) "the folder and its file are where they were"
}

Test-Case "wt-c a half-made worktree with an untracked file (dirty) is NOT touched" {
    $repo = New-SandboxRepo
    $branch = "team/sandbox/worker-c"
    $tree = (New-TeamWorktree -RepoRoot $repo -Branch $branch).Path
    $file = Join-Path $tree "not-yet-committed.txt"
    [System.IO.File]::WriteAllText($file, "half a day of work`n")
    Set-HalfMade -AdminDir (Get-SandboxAdminDir -Tree $tree)
    $threw = $null
    try { [void](New-TeamWorktree -RepoRoot $repo -Branch $branch) } catch { $threw = [string]$_.Exception.Message }
    Assert-True ($null -ne $threw -and $threw -match "yarım ağaç onarılmadı" -and $threw -match "kirli") "it throws 'yarım ağaç onarılmadı ... kirli'; got <$threw>"
    Assert-True (Test-Path -LiteralPath $file) "the untracked file is still there"
}

Test-Case "wt-d a branch with no folder and a stale locked admin entry is attached again without -b, its sha unchanged" {
    $repo = New-SandboxRepo
    $branch = "team/sandbox/worker-d"
    $tree = (New-TeamWorktree -RepoRoot $repo -Branch $branch).Path
    $admin = Get-SandboxAdminDir -Tree $tree
    $sha = Get-SandboxSha -Repo $repo -Ref "refs/heads/$branch"
    Remove-Item -LiteralPath $tree -Recurse -Force
    [System.IO.File]::WriteAllText((Join-Path $admin "locked"), "initializing")
    Assert-True (Test-Path -LiteralPath $admin) "the stale admin entry is there before the call"
    $result = New-TeamWorktree -RepoRoot $repo -Branch $branch
    Assert-True ($result.Created -eq $true) "the worktree was attached"
    Assert-True (Test-TeamWorktreeHealthy -RepoRoot $repo -Path $result.Path -Branch $branch).Healthy "the attached tree is healthy"
    Assert-True ((Get-SandboxSha -Repo $repo -Ref "refs/heads/$branch") -eq $sha) "the branch's sha did not change"
    $head = (Invoke-TeamGit -WorkingDirectory $result.Path -Arguments @("rev-parse", "--abbrev-ref", "HEAD")).StdOut.Trim()
    Assert-True ($head -eq $branch) "the tree is on the branch; HEAD is <$head>"
}

Test-Case "wt-e a healthy worktree is 'already there' and no git write command runs" {
    $repo = New-SandboxRepo
    $branch = "team/sandbox/worker-e"
    [void](New-TeamWorktree -RepoRoot $repo -Branch $branch)
    $caseRealGit = ${function:Invoke-TeamGit}
    $caseCalls = New-Object System.Collections.ArrayList
    function Invoke-TeamGit {
        param([string]$WorkingDirectory, [string[]]$Arguments, [int]$TimeoutSeconds = 300)
        [void]$caseCalls.Add(($Arguments -join " "))
        return (& $caseRealGit -WorkingDirectory $WorkingDirectory -Arguments $Arguments -TimeoutSeconds $TimeoutSeconds)
    }
    $result = New-TeamWorktree -RepoRoot $repo -Branch $branch
    Assert-True ($result.Created -eq $false -and $result.Note -eq "the worktree is already there") "already there; got Created=$($result.Created) <$($result.Note)>"
    $writes = @($caseCalls | Where-Object { $_ -match '^worktree (add|remove|prune|unlock)|^branch |^read-tree' })
    Assert-True ($writes.Count -eq 0) "no git write command; ran: $($writes -join ' | ')"
    Assert-True ($caseCalls.Count -gt 0) "the shadow saw the calls (the case can fail)"
}

Test-Case "wt-f two processes adding worktrees at once take turns (one machine-wide 'worktree add' lock)" {
    $repo = New-SandboxRepo
    $go = Join-Path $repo "go.flag"
    $child = Join-Path $script:TempRoot ("wt-f-child-" + [guid]::NewGuid().ToString("N").Substring(0, 6) + ".ps1")
    $childText = @'
param([string]$Lib, [string]$Repo, [string]$Branch, [string]$Log, [string]$Go)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $Lib "NativeProcess.ps1")
. (Join-Path $Lib "TeamQueue.ps1")
. (Join-Path $Lib "TeamRun.ps1")
$script:RealGit = ${function:Invoke-TeamGit}
function Invoke-TeamGit {
    param([string]$WorkingDirectory, [string[]]$Arguments, [int]$TimeoutSeconds = 300)
    $isAdd = ($Arguments.Count -ge 2 -and $Arguments[0] -eq "worktree" -and $Arguments[1] -eq "add")
    if ($isAdd) { Add-Content -LiteralPath $Log -Value ("start " + [datetime]::UtcNow.Ticks) }
    try {
        if ($isAdd) { Start-Sleep -Milliseconds 2500 }
        return (& $script:RealGit -WorkingDirectory $WorkingDirectory -Arguments $Arguments -TimeoutSeconds $TimeoutSeconds)
    }
    finally { if ($isAdd) { Add-Content -LiteralPath $Log -Value ("end " + [datetime]::UtcNow.Ticks) } }
}
$deadline = [datetime]::UtcNow.AddSeconds(60)
while (-not (Test-Path -LiteralPath $Go)) { if ([datetime]::UtcNow -gt $deadline) { exit 3 }; Start-Sleep -Milliseconds 20 }
$r = New-TeamWorktree -RepoRoot $Repo -Branch $Branch
if (-not $r.Created) { exit 4 }
exit 0
'@
    [System.IO.File]::WriteAllText($child, $childText)
    $lib = Join-Path $repoRoot "scripts\lib"
    $procs = @()
    $logs = @()
    foreach ($n in 1, 2) {
        $log = Join-Path $script:TempRoot ("wt-f-$n-" + [guid]::NewGuid().ToString("N").Substring(0, 6) + ".log")
        $logs += $log
        $procs += Start-Process -FilePath "powershell.exe" -PassThru -WindowStyle Hidden -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$child`"", "-Lib", "`"$lib`"", "-Repo", "`"$repo`"",
            "-Branch", "team/sandbox/worker-f$n", "-Log", "`"$log`"", "-Go", "`"$go`"")
    }
    Start-Sleep -Seconds 3
    [System.IO.File]::WriteAllText($go, "go")
    foreach ($p in $procs) {
        if (-not $p.WaitForExit(120000)) { try { $p.Kill() } catch { }; throw "a child did not finish in 120 s" }
    }
    foreach ($p in $procs) { Assert-True ($p.ExitCode -eq 0) "each child made its worktree; exit codes: $(@($procs | ForEach-Object { $_.ExitCode }) -join ',')" }
    $spans = @()
    foreach ($log in $logs) {
        $lines = @(Get-Content -LiteralPath $log)
        $start = [long](($lines | Where-Object { $_ -like "start *" } | Select-Object -First 1) -replace '^start ', '')
        $end = [long](($lines | Where-Object { $_ -like "end *" } | Select-Object -Last 1) -replace '^end ', '')
        $spans += , @($start, $end)
    }
    $overlap = ($spans[0][0] -lt $spans[1][1]) -and ($spans[1][0] -lt $spans[0][1])
    $ms = [Math]::Round(([Math]::Min($spans[0][1], $spans[1][1]) - [Math]::Max($spans[0][0], $spans[1][0])) / 10000)
    Assert-True (-not $overlap) "the two adds overlapped by $ms ms"
}

Test-Case "wt-g an add that fails twice throws 'host yavaş: git worktree add' with Data[PagentosReason]=host-slow, after exactly two tries" {
    $repo = New-SandboxRepo
    $caseRealGit = ${function:Invoke-TeamGit}
    $caseAdds = New-Object System.Collections.ArrayList
    function Invoke-TeamGit {
        param([string]$WorkingDirectory, [string[]]$Arguments, [int]$TimeoutSeconds = 300)
        if ($Arguments.Count -ge 2 -and $Arguments[0] -eq "worktree" -and $Arguments[1] -eq "add") {
            [void]$caseAdds.Add(($Arguments -join " "))
            return [pscustomobject]@{ FilePath = "git"; CommandLine = ""; ExitCode = 128; StdOut = ""; StdErr = "fatal: simulated slow host"; Success = $false }
        }
        return (& $caseRealGit -WorkingDirectory $WorkingDirectory -Arguments $Arguments -TimeoutSeconds $TimeoutSeconds)
    }
    $caught = $null
    try { [void](New-TeamWorktree -RepoRoot $repo -Branch "team/sandbox/worker-g") } catch { $caught = $_.Exception }
    Assert-True ($null -ne $caught) "it threw"
    Assert-True ($caught.Message.StartsWith("host yavaş: git worktree add")) "the message starts with the fixed prefix; got <$($caught.Message)>"
    Assert-True ([string]$caught.Data["PagentosReason"] -eq "host-slow") "Data[PagentosReason] is host-slow; got <$($caught.Data["PagentosReason"])>"
    Assert-True ($caseAdds.Count -eq 2) "exactly two adds were tried; tried $($caseAdds.Count): $($caseAdds -join ' | ')"
}

try { Remove-Item -LiteralPath $script:TempRoot -Recurse -Force -ErrorAction Stop } catch { Write-Host "  (temp folder left: $script:TempRoot)" }

Write-Host ""
Write-Host "team-run-temp tests:$script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
