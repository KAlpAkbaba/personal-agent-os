<#
.SYNOPSIS
    Authorize the agent to attach to the OWNER's own Chrome (contract v1.4, ADR-0113).

.DESCRIPTION
    Two things happen here, and the second only because the first is impossible any other
    way.

    Chrome accepts --remote-debugging-port ONLY at launch. A Chrome that is already
    running cannot be given the port afterwards: a second chrome.exe with the flag simply
    hands the command line to the running instance and exits, and the flag is ignored.
    So this script closes Chrome (asking first) and relaunches it with the port bound to
    127.0.0.1 on a FIXED loopback port, using the owner's own profile - their tabs, their
    logins, their extensions - and writes a shortcut that launches it the same way, so a
    restart does not silently end the authorization.

    Then it writes a BrowserEnrollment record: the authorization the browser worker reads
    before it will attach to anything at all. No record, no attach; the worker has no
    default endpoint and does not go looking for one.

    WHAT THIS GRANTS, in plain words: the agent can act as you on every site you are
    signed into - read your mail, click, fill forms, submit them, buy things. That is what
    was asked for, twice, in full knowledge. Anything else on this machine that can reach
    127.0.0.1 on the chosen port can drive the browser the same way; loopback is a bound,
    not a lock.

    To undo it: close Chrome, start it normally from the Start menu, and run this script
    with -Revoke (which deletes the record; the worker then refuses the 'owner' profile).

.EXAMPLE
    .\scripts\browser\enroll-owner-chrome.ps1

.EXAMPLE
    .\scripts\browser\enroll-owner-chrome.ps1 -Revoke
#>
[CmdletBinding()]
param(
    # Where the authorization is recorded. The default is the installed browser worker's
    # own state directory, which is where the worker is told to look.
    [string]$EnrollmentFile = "$env:ProgramData\PagentOS\browser\owner-enrollment.json",
    # The owner's REAL Chrome profile, named EXPLICITLY on the command line -- and that is
    # not a detail. Since Chrome 136 the debugging port is silently ignored when the data
    # directory is left implicit, and Chrome 152 on this machine did exactly that: it
    # accepted the flag, started nineteen processes, and opened no port. Naming the very
    # same directory makes it work. Measured both ways on 2026-09-10 before this line
    # existed, and the script's own probe below is what caught it.
    [string]$UserDataDir = "$env:LOCALAPPDATA\Google\Chrome\User Data",
    # FIXED, not random. The first version picked a random high port so the endpoint
    # would not be a well-known address -- which was security theatre: the port is
    # loopback-only either way, and anything already running on this machine can
    # enumerate ports in milliseconds. What the randomness DID buy was a broken feature
    # every time Chrome restarted, because the recorded endpoint pointed at a port
    # nothing was listening on any more. That happened within an hour, on 2026-09-10,
    # and the owner's answer to "play a song" became "tarayıcıyı açamadı". A fixed port
    # plus the shortcut written below means a restart through that shortcut keeps the
    # enrollment valid.
    [int]$Port = 19222,
    [switch]$Revoke,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step { param([string]$Text) Write-Host "  $Text" }

# ---------------------------------------------------------------- revoke

if ($Revoke) {
    if (Test-Path -LiteralPath $EnrollmentFile) {
        Remove-Item -LiteralPath $EnrollmentFile -Force
        Write-Host "Enrollment revoked: $EnrollmentFile removed." -ForegroundColor Green
        Write-Host "The worker will now refuse profile 'owner'. Close Chrome and reopen it normally."
    }
    else {
        Write-Host "Nothing to revoke; $EnrollmentFile does not exist." -ForegroundColor Yellow
    }
    exit 0
}

# ---------------------------------------------------------------- chrome

$chromeCandidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chrome = $chromeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $chrome) {
    throw "Chrome was not found in any of: $($chromeCandidates -join ', ')"
}
Write-Step "chrome  : $chrome"

$endpoint = "http://127.0.0.1:$Port"
Write-Step "endpoint: $endpoint  (loopback only)"

$running = @(Get-Process -Name chrome -ErrorAction SilentlyContinue)
if ($running.Count -gt 0) {
    Write-Host ""
    Write-Host "Chrome is running ($($running.Count) processes)." -ForegroundColor Yellow
    Write-Host "It has to be CLOSED and reopened, because Chrome only accepts the debugging"
    Write-Host "port at launch. Your tabs come back if 'Continue where you left off' is on."
    if (-not $Force) {
        $answer = Read-Host "Close Chrome now? (yes/no)"
        if ($answer -ne "yes") {
            Write-Host "Stopped. Nothing was changed." -ForegroundColor Yellow
            exit 1
        }
    }
    foreach ($proc in $running) {
        try { $null = $proc.CloseMainWindow() } catch { }
    }
    Start-Sleep -Seconds 3
    # Renderers and utility processes have no main window, so CloseMainWindow never
    # reaches them -- and ONE survivor is enough to make the relaunch hand its command
    # line to the old instance and drop the flag on the floor. Keep going until the
    # count is zero, and say so plainly if it will not get there.
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        $still = @(Get-Process -Name chrome -ErrorAction SilentlyContinue)
        if ($still.Count -eq 0) { break }
        Write-Step "closing $($still.Count) remaining chrome process(es)"
        foreach ($proc in $still) {
            try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch { }
        }
        Start-Sleep -Seconds 2
    }
    $left = @(Get-Process -Name chrome -ErrorAction SilentlyContinue)
    if ($left.Count -gt 0) {
        throw "$($left.Count) chrome.exe still running after five attempts; close them by hand and run this again."
    }
}

if (-not (Test-Path -LiteralPath $UserDataDir)) {
    throw "Chrome profile directory not found: $UserDataDir"
}
Write-Step "profile : $UserDataDir"
Write-Step "starting Chrome with the debugging port"
$null = Start-Process -FilePath $chrome -ArgumentList @(
    "--remote-debugging-port=$Port",
    "--remote-debugging-address=127.0.0.1",
    "--user-data-dir=$UserDataDir"
) -PassThru

# ------------------------------------------------------- prove it answers

$deadline = (Get-Date).AddSeconds(20)
$version = $null
while ((Get-Date) -lt $deadline) {
    try {
        $version = Invoke-RestMethod -Uri "$endpoint/json/version" -TimeoutSec 3
        break
    }
    catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $version) {
    throw "Chrome did not answer on $endpoint within 20s. Either an instance was still running and took the command line (close every chrome.exe and try again), or this Chrome refused to open a debugging port on this profile -- which is what happens when --user-data-dir is left implicit."
}
Write-Step "answered: $($version.Browser)"

# ------------------------------------------------------------- the record

$record = [ordered]@{
    enrollments = @(
        [ordered]@{
            id                             = [guid]::NewGuid().ToString()
            name                           = "owner-chrome"
            transport                      = "cdp_loopback"
            endpoint                       = $endpoint
            capability_overrides           = @{}
            created_at                     = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.ffffffZ")
            owner_authorized_for_research  = $false
        }
    )
}
$directory = Split-Path -Parent $EnrollmentFile
if (-not (Test-Path -LiteralPath $directory)) {
    $null = New-Item -ItemType Directory -Path $directory -Force
}
# WITHOUT a byte-order mark. PowerShell 5.1's `Out-File -Encoding utf8` always writes
# one, and the Python side that reads this file rejected it outright
# ("Unexpected UTF-8 BOM") the first time this ran for real. The reader tolerates a BOM
# now as well, because a human editing this in Notepad would reintroduce one - but the
# writer should not be the thing that needs forgiving.
$json = $record | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText($EnrollmentFile, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Step "recorded: $EnrollmentFile"

# Owner-only. The record is not a secret in the password sense, but it names the exact
# loopback endpoint that drives a signed-in browser, so it is not world-readable either.
$acl = "$env:SystemRoot\System32\icacls.exe"
& $acl $EnrollmentFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" /grant:r "SYSTEM:(R,W)" | Out-Null

# A shortcut that launches Chrome the SAME way this script just did.
#
# Without it the enrollment lasts exactly as long as this Chrome process: the owner
# closes the browser, Windows or an update reopens it the ordinary way, the port is gone,
# and the next "play me a song" answers "I could not reach your browser". With a fixed
# port and this shortcut, restarting through it puts the port back where the enrollment
# already says it is. Desktop and Start Menu, so it is where a browser is looked for.
$shortcutTargets = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Chrome (PagentOS).lnk'),
    (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\Chrome (PagentOS).lnk')
)
$shell = New-Object -ComObject WScript.Shell
foreach ($target in $shortcutTargets) {
    $parent = Split-Path -Parent $target
    if (-not (Test-Path -LiteralPath $parent)) { continue }
    try {
        $link = $shell.CreateShortcut($target)
        $link.TargetPath = $chrome
        $link.Arguments = "--remote-debugging-port=$Port --remote-debugging-address=127.0.0.1 --user-data-dir=`"$UserDataDir`""
        $link.WorkingDirectory = Split-Path -Parent $chrome
        $link.IconLocation = "$chrome,0"
        $link.Description = "Chrome the agent can attach to (PagentOS, loopback port $Port)"
        $link.Save()
        Write-Step "shortcut: $target"
    }
    catch {
        Write-Step "could not write $target ($($_.Exception.Message))"
    }
}

Write-Host ""
Write-Host "The agent may now attach to your Chrome." -ForegroundColor Green
Write-Host "From now on open Chrome with the 'Chrome (PagentOS)' shortcut on your desktop"
Write-Host "or in the Start menu. Opened any other way, the debugging port is not there and"
Write-Host "the agent will say so instead of quietly using a blank browser."
Write-Host ""
Write-Host "Undo at any time:  .\scripts\browser\enroll-owner-chrome.ps1 -Revoke"
