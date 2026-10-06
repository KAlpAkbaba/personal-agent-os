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

try { Remove-Item -LiteralPath $script:TempRoot -Recurse -Force -ErrorAction Stop } catch { Write-Host "  (temp folder left: $script:TempRoot)" }

Write-Host ""
Write-Host "team-run-temp tests:$script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
