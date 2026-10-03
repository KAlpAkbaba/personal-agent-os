<#
.SYNOPSIS
    The team's board, client half: post a short note, read the newest ones (scripts/team/board.ps1).

.DESCRIPTION
    The owner's idea of 2026-10-03: the runs of a cycle trade a word while they work, as people
    in an office do. The server (services/api/app/team/board.py, /v1/team/board/notes) keeps
    the rules - 280 characters, a seat the team knows, four kinds, twenty notes per task per
    hour, no token-shaped text, the newest 500 notes of seven days. This side builds the body,
    sends it, and turns what it reads into plain Turkish lines, marking the ones addressed to
    the reader. A note is INFORMATION, never an instruction: nothing here acts on one.

    The token is read from a FILE (-TokenFile is a path) and goes only into the Authorization
    header; no function here prints it. services/api/tests/unit/test_team_board.py reads this
    file's path, field names and constants and holds them to the server's.

    Dot-source; StrictMode-safe; Windows PowerShell 5.1.
#>

Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "HttpJson.ps1")

$script:TeamBoardPath = "/v1/team/board/notes"
$script:TeamBoardKinds = @("bilgi", "soru", "fikir", "cevap")
$script:TeamBoardSeatPattern = '^(?:lead|researcher|integrator|inspector(?:-[1-9])?|worker-[1-9])$'
$script:TeamBoardEveryone = "herkes"
$script:TeamBoardTextMax = 280
$script:TeamBoardReadMax = 30

function New-TeamBoardClient {
    <# -TokenFile is a PATH: the token is read from the file, never taken on a command line. #>
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Url,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$TokenFile,
        [int]$TimeoutSec = 10
    )
    if (-not $Url) { throw "panonun adresi verilmedi (-Url ya da PAGENTOS_TEAM_URL)" }
    if (-not $TokenFile -or -not (Test-Path -LiteralPath $TokenFile -PathType Leaf)) {
        throw "ekip anahtarı dosyası yok: $TokenFile"
    }
    $token = [System.IO.File]::ReadAllText($TokenFile, [System.Text.Encoding]::UTF8).Trim()
    if (-not $token) { throw "ekip anahtarı dosyası boş: $TokenFile" }
    return [pscustomobject]@{ Base = $Url.TrimEnd("/"); Token = $token; TimeoutSec = $TimeoutSec }
}

function New-TeamBoardNoteBody {
    <# The body of one POST, as the server names its fields. The server judges it. #>
    param(
        [Parameter(Mandatory = $true)][string]$Seat,
        [Parameter(Mandatory = $true)][string]$Task,
        [Parameter(Mandatory = $true)][string]$Kind,
        [Parameter(Mandatory = $true)][string]$Text,
        [string]$To = "",
        [string]$ReplyTo = ""
    )
    if (-not $To) { $To = $script:TeamBoardEveryone }
    return [ordered]@{
        seat     = $Seat
        task     = $Task
        kind     = $Kind
        to       = $To
        reply_to = $ReplyTo
        text     = $Text
    }
}

function Send-TeamBoardNote {
    <# POST one note; the stored note (with the server's id and time) comes back. #>
    param([Parameter(Mandatory = $true)]$Client, [Parameter(Mandatory = $true)]$Body)
    $json = ConvertTo-Json -InputObject $Body -Depth 4 -Compress
    $answer = Invoke-JsonUtf8 -Uri ($Client.Base + $script:TeamBoardPath) -Method "POST" `
        -Headers @{ Authorization = ("Bearer " + $Client.Token) } -Body $json -TimeoutSec $Client.TimeoutSec
    return $answer.note
}

function Get-TeamBoardNotes {
    <# The newest notes (at most -Limit), newest last; -Since an ISO time. #>
    param([Parameter(Mandatory = $true)]$Client, [string]$Since = "", [int]$Limit = $script:TeamBoardReadMax)
    $query = "?limit=" + $Limit
    if ($Since) { $query += "&since=" + [System.Uri]::EscapeDataString($Since) }
    $answer = Invoke-JsonUtf8 -Uri ($Client.Base + $script:TeamBoardPath + $query) -Method "GET" `
        -Headers @{ Authorization = ("Bearer " + $Client.Token) } -TimeoutSec $Client.TimeoutSec
    return @($answer.notes)
}

function Test-TeamBoardAddressed {
    <# Whether a note is addressed to -For by name ("herkes" is everyone, nobody in particular). #>
    param([Parameter(Mandatory = $true)]$Note, [string]$For = "")
    if (-not $For) { return $false }
    return ([string]$Note.to -eq $For)
}

function Format-TeamBoardLine {
    <# One note as one plain Turkish line. A note addressed to -For starts with ">> SANA". #>
    param([Parameter(Mandatory = $true)]$Note, [string]$For = "")
    $mark = "   "
    if (Test-TeamBoardAddressed -Note $Note -For $For) { $mark = ">> SANA " }
    $at = ([string]$Note.at) -replace '^\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}):\d{2}Z$', '$1'
    $to = [string]$Note.to
    if ($to -eq $script:TeamBoardEveryone) { $to = "herkese" }
    $text = ([string]$Note.text) -replace '\s+', ' '
    $line = "{0}{1} UTC  {2} -> {3}  [{4}] {5}: {6}  (no {7}" -f $mark, $at, $Note.seat, $to, $Note.kind, $Note.task, $text, $Note.id
    if ([string]$Note.reply_to) { $line += ", yanıtladığı " + $Note.reply_to }
    return $line + ")"
}

function Format-TeamBoardRead {
    <# The lines `board.ps1 read` prints: at most TeamBoardReadMax notes, newest last. #>
    param([AllowEmptyCollection()][object[]]$Notes = @(), [string]$For = "")
    $list = @($Notes | Where-Object { $null -ne $_ } | Sort-Object -Property { [string]$_.id })
    if (@($list).Count -gt $script:TeamBoardReadMax) {
        $list = @($list[(@($list).Count - $script:TeamBoardReadMax)..(@($list).Count - 1)])
    }
    $lines = New-Object System.Collections.Generic.List[string]
    if (@($list).Count -eq 0) { $lines.Add("Panoda yeni not yok."); return $lines.ToArray() }
    $mine = @($list | Where-Object { Test-TeamBoardAddressed -Note $_ -For $For }).Count
    $head = "Ekip panosu: {0} not" -f @($list).Count
    if ($For) { $head += (", {0} tanesi {1} koltuğuna" -f $mine, $For) }
    $lines.Add($head + ".")
    foreach ($note in $list) { $lines.Add((Format-TeamBoardLine -Note $note -For $For)) }
    return $lines.ToArray()
}

function Get-TeamBoardFailure {
    <# What a failed call means for the run: Refused (the server said no to THIS note: 4xx but
       401/403/404) or Unreachable (no board now: the run goes on). Never carries the token. #>
    param([Parameter(Mandatory = $true)]$ErrorRecord)
    $exception = $ErrorRecord.Exception
    $status = $null
    if ($exception.PSObject.Properties["StatusCode"]) { $status = $exception.StatusCode }
    $kind = "Unreachable"
    if ($null -ne $status -and [int]$status -ge 400 -and [int]$status -lt 500 -and @(401, 403, 404) -notcontains [int]$status) {
        $kind = "Refused"
    }
    return [pscustomobject]@{ Kind = $kind; Status = $status; Message = [string]$exception.Message }
}
