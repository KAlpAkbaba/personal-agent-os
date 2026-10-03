<#
.SYNOPSIS
    The team's board, client half: scripts/lib/TeamBoard.ps1 and scripts/team/board.ps1.

.DESCRIPTION
    The groups, by the prefix of a case's name:

      body        the POST body: the server's field names, "herkes" when no -To;
      format      a note as a Turkish line, the ones addressed to the reader marked, at most
                  30, newest last;
      failure     which failure is the caller's note (Refused) and which is "no board now";
      command     board.ps1 as a run calls it, against a fake board on 127.0.0.1 (this file
                  in -ServePort mode): one seat posts, another reads; a 422 and a 429 are
                  exit 2; an unreachable board, a 500, a refused token, no address and no
                  token file are a warning and exit 0; the token is never printed.

    The server's rules are not this file's: services/api/tests/unit/test_team_board.py holds
    them, and tests/integration/test_team_board_pg.py runs board.ps1 against the real store.

    Run: powershell -NoProfile -File scripts\tests\team-board.tests.ps1
#>

[CmdletBinding()]
param(
    # Runs only the cases whose name matches this pattern (the mutation proofs re-run a slice).
    [string]$Filter = "",
    # Internal: serve a fake board on this port (the command cases start this file so).
    [int]$ServePort = 0,
    [string]$ServeReady = "",
    [string]$ServeToken = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$utf8 = New-Object System.Text.UTF8Encoding($false)

if ($ServePort -gt 0) {
    # The fake board: notes in memory, a Bearer token, and the status codes a client must
    # handle - text "__500__" is a 500, more than 280 characters a 422, a 21st note of a task
    # a 429. GET /__stop ends it.
    $notes = New-Object System.Collections.ArrayList
    $count = 0
    $listener = New-Object System.Net.HttpListener
    $listener.Prefixes.Add("http://127.0.0.1:$ServePort/")
    $listener.Start()
    [System.IO.File]::WriteAllText($ServeReady, "ready", $utf8)
    $running = $true
    while ($running) {
        $context = $listener.GetContext()
        $request = $context.Request
        $raw = (New-Object System.IO.StreamReader($request.InputStream, $utf8)).ReadToEnd()
        $status = 200
        $answer = @{}
        if ($request.Url.AbsolutePath -eq "/__stop") { $running = $false }
        elseif ([string]$request.Headers["Authorization"] -ne ("Bearer " + $ServeToken)) { $status = 401; $answer = @{ detail = "unauthorized" } }
        elseif ($request.HttpMethod -eq "POST") {
            $body = ConvertFrom-Json -InputObject $raw
            $sameTask = @($notes | Where-Object { $_.task -eq $body.task }).Count
            if ($body.text -eq "__500__") { $status = 500; $answer = @{ detail = "boom" } }
            elseif ($body.text.Length -gt 280) { $status = 422; $answer = @{ detail = @{ code = "invalid"; message = "text is at most 280 characters" } } }
            elseif ($sameTask -ge 20) { $status = 429; $answer = @{ detail = @{ code = "rate_limited"; message = "20 notes in the last hour" } } }
            else {
                $count++
                $note = [ordered]@{
                    id = ("n-20261003T0900{0:00}000000Z-0000abcd" -f $count); at = ("2026-10-03T09:00:{0:00}Z" -f $count)
                    seat = $body.seat; task = $body.task; kind = $body.kind; to = $body.to; reply_to = $body.reply_to; text = $body.text
                }
                [void]$notes.Add([pscustomobject]$note)
                $answer = @{ note = $note }
            }
        }
        else { $answer = @{ notes = @($notes); now = "2026-10-03T09:10:00Z" } }
        $bytes = $utf8.GetBytes((ConvertTo-Json -InputObject $answer -Depth 6 -Compress))
        $context.Response.StatusCode = $status
        $context.Response.ContentType = "application/json; charset=utf-8"
        $context.Response.ContentLength64 = $bytes.Length
        $context.Response.OutputStream.Write($bytes, 0, $bytes.Length)
        $context.Response.Close()
    }
    $listener.Stop()
    exit 0
}

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\TeamBoard.ps1")
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

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

function New-Note {
    param([int]$N, [string]$Seat = "worker-1", [string]$To = "herkes", [string]$Kind = "bilgi", [string]$Text = "", [string]$ReplyTo = "")
    if (-not $Text) { $Text = "not $N" }
    return [pscustomobject]@{
        id = ("n-20261003T09{0:0000}000000Z-0000abcd" -f $N); at = ("2026-10-03T09:{0:00}:00Z" -f ($N % 60))
        seat = $Seat; task = "team-board"; kind = $Kind; to = $To; reply_to = $ReplyTo; text = $Text
    }
}

# ------------------------------------------------------------------ body

Test-Case "body: the server's six fields, and 'herkes' when no -To is given" {
    $body = New-TeamBoardNoteBody -Seat "worker-1" -Task "team-board" -Kind "fikir" -Text "Önbelleği paylaşalım."
    Assert-Equal -Expected "seat,task,kind,to,reply_to,text" -Actual (@($body.Keys) -join ",") -Because "the field names are the server's"
    Assert-Equal -Expected "herkes" -Actual $body.to -Because "a note to nobody in particular is to everyone"
    Assert-Equal -Expected "" -Actual $body.reply_to -Because "no reply"
    $reply = New-TeamBoardNoteBody -Seat "worker-2" -Task "team-board" -Kind "cevap" -Text "Tamam." -To "inspector" -ReplyTo "n-1"
    Assert-Equal -Expected "inspector|n-1" -Actual ($reply.to + "|" + $reply.reply_to) -Because "-To and -ReplyTo pass through"
}

# ------------------------------------------------------------------ format

Test-Case "format: a note addressed to the reader is marked, one to everyone or to another seat is not" {
    $question = New-Note -N 1 -Seat "inspector" -To "worker-2" -Kind "soru" -Text "Dev stack açık mı?"
    $line = Format-TeamBoardLine -Note $question -For "worker-2"
    Assert-True -Condition ($line.StartsWith(">> SANA ")) -Because "addressed to worker-2: $line"
    Assert-True -Condition ($line -match "inspector -> worker-2  \[soru\] team-board: Dev stack açık mı\?") -Because "the line names who, whom, what: $line"
    Assert-True -Condition (-not (Format-TeamBoardLine -Note $question -For "worker-1").StartsWith(">>")) -Because "not worker-1's"
    Assert-True -Condition (-not (Format-TeamBoardLine -Note $question).StartsWith(">>")) -Because "no reader named: nothing marked"
    $all = New-Note -N 2 -To "herkes"
    $allLine = Format-TeamBoardLine -Note $all -For "worker-2"
    Assert-True -Condition (-not $allLine.StartsWith(">>")) -Because "'herkes' is not addressed to one seat: $allLine"
    Assert-True -Condition ($allLine -match "-> herkese") -Because "spelled in Turkish: $allLine"
}

Test-Case "format: a reply names the note it answers, and a text's line breaks become spaces" {
    $line = Format-TeamBoardLine -Note (New-Note -N 3 -Kind "cevap" -Text "Evet,`r`naçık." -ReplyTo "n-x")
    Assert-True -Condition ($line -match "Evet, açık\.") -Because "one line: $line"
    Assert-True -Condition ($line -match "yanıtladığı n-x\)$") -Because "the reply is linked: $line"
}

Test-Case "format: at most 30 notes, newest last, and the header counts the reader's" {
    $many = @(1..40 | ForEach-Object { New-Note -N $_ -To $(if ($_ % 10 -eq 0) { "worker-2" } else { "herkes" }) })
    [array]::Reverse($many)
    $lines = @(Format-TeamBoardRead -Notes $many -For "worker-2")
    Assert-Equal -Expected 31 -Actual $lines.Count -Because "a header and 30 notes"
    Assert-True -Condition ($lines[1] -match "not 11 ") -Because "the oldest shown is the 11th: $($lines[1])"
    Assert-True -Condition ($lines[30] -match "not 40 ") -Because "newest last: $($lines[30])"
    Assert-Equal -Expected "Ekip panosu: 30 not, 3 tanesi worker-2 koltuğuna." -Actual $lines[0] -Because "the header"
    Assert-Equal -Expected 3 -Actual @($lines | Where-Object { $_.StartsWith(">> SANA") }).Count -Because "20, 30 and 40 are worker-2's"
    Assert-Equal -Expected "Panoda yeni not yok." -Actual (@(Format-TeamBoardRead -Notes @())[0]) -Because "an empty board"
}

# ------------------------------------------------------------------ failure

Test-Case "failure: 422 and 429 are the caller's note; 401, 404, 500 and no answer are 'no board now'" {
    foreach ($case in @(@(422, "Refused"), @(429, "Refused"), @(400, "Refused"), @(401, "Unreachable"), @(403, "Unreachable"), @(404, "Unreachable"), @(500, "Unreachable"), @(503, "Unreachable"), @($null, "Unreachable"))) {
        $error1 = New-Object System.Exception("HTTP x")
        $error1 | Add-Member -NotePropertyName StatusCode -NotePropertyValue $case[0] -Force
        $record = New-Object System.Management.Automation.ErrorRecord($error1, "x", "NotSpecified", $null)
        Assert-Equal -Expected $case[1] -Actual (Get-TeamBoardFailure -ErrorRecord $record).Kind -Because "status $($case[0])"
    }
    $plain = New-Object System.Management.Automation.ErrorRecord((New-Object System.Exception("no status")), "x", "NotSpecified", $null)
    Assert-Equal -Expected "Unreachable" -Actual (Get-TeamBoardFailure -ErrorRecord $plain).Kind -Because "an exception without a status"
}

# ------------------------------------------------------------------ command

$work = Join-Path $env:TEMP ("pagentos-board-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
[void](New-Item -ItemType Directory -Force -Path $work)
$token = "board-test-" + [guid]::NewGuid().ToString("N")
$tokenFile = Join-Path $work "token.txt"
[System.IO.File]::WriteAllText($tokenFile, $token + "`n", $utf8)
$server = $null
$url = ""

function Invoke-Board {
    <# board.ps1 in its own 5.1 process, as a run calls it: stdout and the exit code. #>
    param([string[]]$Arguments, [hashtable]$Environment = @{})
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $powershell
    $quoted = @($Arguments | ForEach-Object { if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } })
    $info.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $repoRoot "scripts\team\board.ps1") + '" ' + ($quoted -join " ")
    $info.UseShellExecute = $false
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = $utf8
    $info.CreateNoWindow = $true
    foreach ($name in @("PAGENTOS_TEAM_URL", "PAGENTOS_TEAM_TOKEN_FILE")) { $info.EnvironmentVariables.Remove($name) }
    foreach ($key in $Environment.Keys) { $info.EnvironmentVariables[$key] = [string]$Environment[$key] }
    $process = [System.Diagnostics.Process]::Start($info)
    $stderr = $process.StandardError.ReadToEndAsync()
    $stdout = $process.StandardOutput.ReadToEnd()
    if (-not $process.WaitForExit(60000)) { $process.Kill(); throw "board.ps1 did not end in 60 s" }
    $all = $stdout + $stderr.Result
    if ($all.Contains($token)) { throw "board.ps1 printed the token" }
    return [pscustomobject]@{ ExitCode = $process.ExitCode; Out = $stdout.Trim(); Err = $stderr.Result.Trim() }
}

try {
    $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $probe.Start(); $port = $probe.LocalEndpoint.Port; $probe.Stop()
    $ready = Join-Path $work "ready"
    $serveArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + $PSCommandPath + '"'), "-ServePort", $port, "-ServeReady", ('"' + $ready + '"'), "-ServeToken", $token)
    $server = Start-Process -FilePath $powershell -ArgumentList $serveArgs -PassThru -WindowStyle Hidden
    $deadline = [datetime]::UtcNow.AddSeconds(40)
    while (-not (Test-Path -LiteralPath $ready)) {
        if ([datetime]::UtcNow -gt $deadline -or $server.HasExited) { throw "the fake board did not start" }
        Start-Sleep -Milliseconds 200
    }
    $url = "http://127.0.0.1:$port"
    $common = @("-Url", $url, "-TokenFile", $tokenFile)

    Test-Case "command: one seat posts, another reads it, and a soru to worker-2 is marked for worker-2" {
        $posted = Invoke-Board -Arguments (@("post", "-Seat", "worker-1", "-Task", "team-board", "-Kind", "bilgi", "-Text", "board.py üzerinde çalışıyorum.") + $common)
        Assert-Equal -Expected 0 -Actual $posted.ExitCode -Because "$($posted.Out) $($posted.Err)"
        Assert-True -Condition ($posted.Out -match "^Not panoya yazıldı: no n-") -Because $posted.Out
        $asked = Invoke-Board -Arguments (@("post", "-Seat", "inspector", "-Task", "team-board", "-Kind", "soru", "-To", "worker-2", "-Text", "Dev stack açık mı?") + $common)
        Assert-Equal -Expected 0 -Actual $asked.ExitCode -Because $asked.Out
        $read = Invoke-Board -Arguments (@("read", "-For", "worker-2") + $common)
        Assert-Equal -Expected 0 -Actual $read.ExitCode -Because $read.Out
        $lines = @($read.Out -split "`r?`n")
        Assert-Equal -Expected 3 -Actual $lines.Count -Because $read.Out
        Assert-True -Condition ($lines[1] -match "worker-1 -> herkese  \[bilgi\] team-board: board\.py üzerinde çalışıyorum\.") -Because "Turkish arrives whole: $($lines[1])"
        Assert-True -Condition ($lines[2].StartsWith(">> SANA ") -and $lines[2] -match "Dev stack açık mı\?") -Because $lines[2]
        $other = Invoke-Board -Arguments (@("read", "-For", "worker-1") + $common)
        Assert-Equal -Expected 0 -Actual @(($other.Out -split "`r?`n") | Where-Object { $_.StartsWith(">>") }).Count -Because "nothing is worker-1's"
    }

    Test-Case "command: a note the board refuses (422 length, 429 rate) is one line and exit 2" {
        $long = Invoke-Board -Arguments (@("post", "-Seat", "worker-1", "-Task", "long-task", "-Kind", "fikir", "-Text", ("x" * 281)) + $common)
        Assert-Equal -Expected 2 -Actual $long.ExitCode -Because $long.Out
        Assert-True -Condition ($long.Out -match "^PANO REDDETTI \(HTTP 422\)") -Because $long.Out
        foreach ($n in 1..20) {
            $ok = Invoke-JsonUtf8 -Uri ($url + "/v1/team/board/notes") -Method "POST" -Headers @{ Authorization = "Bearer $token" } -Body (ConvertTo-Json -Compress -InputObject @{ seat = "worker-3"; task = "busy-task"; kind = "bilgi"; to = "herkes"; reply_to = ""; text = "n$n" })
        }
        $rate = Invoke-Board -Arguments (@("post", "-Seat", "worker-3", "-Task", "busy-task", "-Kind", "bilgi", "-Text", "21") + $common)
        Assert-Equal -Expected 2 -Actual $rate.ExitCode -Because $rate.Out
        Assert-True -Condition ($rate.Out -match "HTTP 429") -Because $rate.Out
        $missing = Invoke-Board -Arguments (@("post", "-Seat", "worker-1") + $common)
        Assert-Equal -Expected 2 -Actual $missing.ExitCode -Because $missing.Out
        Assert-True -Condition ($missing.Out -match "eksik: -Task, -Kind, -Text") -Because $missing.Out
    }

    Test-Case "command: the board cannot be reached - a warning and exit 0, never a stopped run" {
        $probe2 = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
        $probe2.Start(); $closed = $probe2.LocalEndpoint.Port; $probe2.Stop()
        $cases = @(
            @{ Name = "a closed port"; Args = @("read", "-Url", "http://127.0.0.1:$closed", "-TokenFile", $tokenFile, "-TimeoutSec", "5") },
            @{ Name = "a 500"; Args = (@("post", "-Seat", "lead", "-Task", "team-board", "-Kind", "bilgi", "-Text", "__500__") + $common) },
            @{ Name = "no address"; Args = @("read", "-TokenFile", $tokenFile) },
            @{ Name = "no token file"; Args = @("read", "-Url", $url, "-TokenFile", (Join-Path $work "absent.token")) }
        )
        foreach ($case in $cases) {
            $run = Invoke-Board -Arguments $case.Args
            Assert-Equal -Expected 0 -Actual $run.ExitCode -Because "$($case.Name): $($run.Out) $($run.Err)"
            Assert-True -Condition ($run.Out -match "^UYARI: ekip panosu") -Because "$($case.Name): $($run.Out)"
        }
        $wrongToken = Join-Path $work "wrong.token"
        [System.IO.File]::WriteAllText($wrongToken, "not-the-token", $utf8)
        $refusedToken = Invoke-Board -Arguments @("read", "-Url", $url, "-TokenFile", $wrongToken)
        Assert-Equal -Expected 0 -Actual $refusedToken.ExitCode -Because "a refused token: $($refusedToken.Out)"
        Assert-True -Condition ($refusedToken.Out -match "^UYARI:.*HTTP 401") -Because $refusedToken.Out
    }

    Test-Case "command: the address and the token file come from the environment when not given" {
        $run = Invoke-Board -Arguments @("read", "-For", "worker-2") -Environment @{ PAGENTOS_TEAM_URL = $url; PAGENTOS_TEAM_TOKEN_FILE = $tokenFile }
        Assert-Equal -Expected 0 -Actual $run.ExitCode -Because $run.Out
        Assert-True -Condition ($run.Out -match "^Ekip panosu: \d+ not, 1 tanesi worker-2 koltuğuna\.") -Because $run.Out
    }
}
finally {
    if ($url) { try { [void](Invoke-JsonUtf8 -Uri ($url + "/__stop") -TimeoutSec 5) } catch { } }
    if ($null -ne $server -and -not $server.HasExited) {
        if (-not $server.WaitForExit(10000)) { try { $server.Kill() } catch { } }
    }
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host ("team-board: {0} passed, {1} failed" -f $script:Passes, $script:Failures)
if ($script:Failures -gt 0) { exit 1 }
exit 0
