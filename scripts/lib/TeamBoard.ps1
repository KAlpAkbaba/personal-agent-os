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

    The consultation (team/plans/team-board-talk-adr.md): a 'danisma' carries the asker's
    situation, two or three named options, its own lean and the files; -To auto is routed by
    the server to the running seat that shares files. `read` shows the asker's card under every
    danisma, `context` the card and the asker's branch diff, `wait` polls for the cevap and
    gives up at its bound (at most 15 minutes) - a consultation never blocks a run.

    Dot-source; StrictMode-safe; Windows PowerShell 5.1.
#>

Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "HttpJson.ps1")

$script:TeamBoardPath = "/v1/team/board/notes"
$script:TeamBoardKinds = @("bilgi", "soru", "fikir", "cevap", "danisma")
$script:TeamBoardSeatPattern = '^(?:lead|researcher|integrator|inspector(?:-[1-9])?|worker-[1-9])$'
$script:TeamBoardEveryone = "herkes"
$script:TeamBoardTextMax = 280
$script:TeamBoardReadMax = 30
# The consultation's bounds: the server's (services/api/app/team/board.py; a unit test compares).
$script:TeamBoardAuto = "auto"
$script:TeamBoardSituationMax = 400
$script:TeamBoardOptionMax = 200
$script:TeamBoardLeanMax = 200
$script:TeamBoardFilesMax = 5
$script:TeamBoardWaitMinutesMax = 15
$script:TeamBoardWaitPollSeconds = 30
$script:TeamBoardTopics = @("kod", "test", "kural")

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
        [string]$ReplyTo = "",
        # A cevap to a danisma: the chosen option's letter, or "başka: ...".
        [string]$Choice = "",
        # A test-queue bilgi: the line's snapshot (scripts/lib/TeamTestSlots.ps1).
        $Slot = $null
    )
    if (-not $To) { $To = $script:TeamBoardEveryone }
    $body = [ordered]@{
        seat     = $Seat
        task     = $Task
        kind     = $Kind
        to       = $To
        reply_to = $ReplyTo
        text     = $Text
    }
    if ($Choice) { $body.choice = $Choice }
    if ($null -ne $Slot) { $body.slot = $Slot }
    return $body
}

function New-TeamBoardConsultBody {
    <# A danisma: the situation (it is also the note's text), 2-3 options ("A: ..." or bare: the
       server names them by place), the asker's lean, the files (a comma list is split). #>
    param(
        [Parameter(Mandatory = $true)][string]$Seat,
        [Parameter(Mandatory = $true)][string]$Task,
        [Parameter(Mandatory = $true)][string]$Situation,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Options,
        [Parameter(Mandatory = $true)][string]$Lean,
        [AllowEmptyCollection()][string[]]$Files = @(),
        [string]$Topic = "",
        [string]$To = "",
        [string]$ReplyTo = ""
    )
    if (-not $To) { $To = $script:TeamBoardAuto }
    if (-not $Topic) { $Topic = $script:TeamBoardTopics[0] }
    $paths = New-Object System.Collections.Generic.List[string]
    foreach ($item in @($Files)) {
        foreach ($part in ([string]$item -split ",")) { if ($part.Trim()) { $paths.Add($part.Trim()) } }
    }
    $named = New-Object System.Collections.Generic.List[string]
    foreach ($option in @($Options)) { if ([string]$option) { $named.Add([string]$option) } }
    return [ordered]@{
        seat     = $Seat
        task     = $Task
        kind     = "danisma"
        to       = $To
        reply_to = $ReplyTo
        situation = $Situation
        options  = [string[]]$named.ToArray()
        my_lean  = $Lean
        files    = [string[]]$paths.ToArray()
        topic    = $Topic
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

function Get-TeamBoardPage {
    <# The server's whole answer: notes (at most -Limit, newest last; -Since an ISO time; only
       the answers to -ReplyTo) and the cards of the danismas' askers. #>
    param([Parameter(Mandatory = $true)]$Client, [string]$Since = "", [int]$Limit = $script:TeamBoardReadMax, [string]$ReplyTo = "")
    $query = "?limit=" + $Limit
    if ($Since) { $query += "&since=" + [System.Uri]::EscapeDataString($Since) }
    if ($ReplyTo) { $query += "&reply_to=" + [System.Uri]::EscapeDataString($ReplyTo) }
    return (Invoke-JsonUtf8 -Uri ($Client.Base + $script:TeamBoardPath + $query) -Method "GET" `
            -Headers @{ Authorization = ("Bearer " + $Client.Token) } -TimeoutSec $Client.TimeoutSec)
}

function Get-TeamBoardNotes {
    <# The newest notes (at most -Limit), newest last; -Since an ISO time. #>
    param([Parameter(Mandatory = $true)]$Client, [string]$Since = "", [int]$Limit = $script:TeamBoardReadMax, [string]$ReplyTo = "")
    return @((Get-TeamBoardPage -Client $Client -Since $Since -Limit $Limit -ReplyTo $ReplyTo).notes)
}

function Get-TeamBoardContext {
    <# One note with its asker's card (title, goal and acceptance lines, branch, area) and its answers. #>
    param([Parameter(Mandatory = $true)]$Client, [Parameter(Mandatory = $true)][string]$Note)
    $path = $script:TeamBoardPath + "/" + [System.Uri]::EscapeDataString($Note) + "/context"
    return (Invoke-JsonUtf8 -Uri ($Client.Base + $path) -Method "GET" `
            -Headers @{ Authorization = ("Bearer " + $Client.Token) } -TimeoutSec $Client.TimeoutSec)
}

function Wait-TeamBoardAnswer {
    <# Polls -Fetch (the answers to one note) every -PollSeconds until a cevap comes or -Minutes
       (at most TeamBoardWaitMinutesMax) have passed: the cevap, or $null. A FOREGROUND wait -
       runs have no background commands - and never longer than its bound. -Sleep is the clock
       (tests pass a fake one). #>
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Fetch,
        [double]$Minutes = 5,
        [int]$PollSeconds = $script:TeamBoardWaitPollSeconds,
        [scriptblock]$Sleep = { param($Seconds) Start-Sleep -Seconds $Seconds }
    )
    $teamBoardBound = [int][math]::Round([math]::Min([math]::Max($Minutes, 0), $script:TeamBoardWaitMinutesMax) * 60)
    $teamBoardPoll = [math]::Max(1, $PollSeconds)
    $teamBoardWaited = 0
    while ($true) {
        $teamBoardFound = @(& $Fetch | Where-Object { $null -ne $_ -and [string]$_.kind -eq "cevap" }) | Select-Object -First 1
        if ($null -ne $teamBoardFound) { return $teamBoardFound }
        if ($teamBoardWaited -ge $teamBoardBound) { return $null }
        $teamBoardStep = [math]::Min($teamBoardPoll, $teamBoardBound - $teamBoardWaited)
        & $Sleep $teamBoardStep
        $teamBoardWaited += $teamBoardStep
    }
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
    if ($Note.PSObject.Properties["choice"] -and [string]$Note.choice) { $text = "seçim {0} - {1}" -f $Note.choice, $text }
    $line = "{0}{1} UTC  {2} -> {3}  [{4}] {5}: {6}  (no {7}" -f $mark, $at, $Note.seat, $to, $Note.kind, $Note.task, $text, $Note.id
    if ([string]$Note.reply_to) { $line += ", yanıtladığı " + $Note.reply_to }
    return $line + ")"
}

function Get-TeamBoardField {
    <# A field of a note or a card, or the default when it has none (StrictMode-safe). #>
    param($Object, [string]$Name, $Default = "")
    if ($null -eq $Object) { return $Default }
    if ($Object -is [System.Collections.IDictionary]) { if ($Object.Contains($Name)) { return $Object[$Name] } ; return $Default }
    if ($Object.PSObject.Properties[$Name]) { return $Object.$Name }
    return $Default
}

function Format-TeamBoardConsult {
    <# The lines under a danisma: its options, the asker's lean, the files, the asker's card and
       how to answer it - what the answerer reads before it answers. #>
    param([Parameter(Mandatory = $true)]$Note, $Card = $null)
    $pad = "        "
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add($pad + "seçenekler: " + (@(Get-TeamBoardField $Note "options" @()) -join " | "))
    $lines.Add($pad + "eğilimi: " + (Get-TeamBoardField $Note "my_lean"))
    $files = @(Get-TeamBoardField $Note "files" @())
    if (@($files).Count -gt 0) { $lines.Add($pad + "dosyalar: " + ($files -join ", ")) }
    $route = [string](Get-TeamBoardField $Note "route")
    if ($route) { $lines.Add($pad + "yönlendirme: " + $route) }
    if ($null -ne $Card) {
        $lines.Add($pad + "kart: " + (Get-TeamBoardField $Card "title"))
        $goal = @(Get-TeamBoardField $Card "goal" @())
        if (@($goal).Count -gt 0) { $lines.Add($pad + "hedef: " + ($goal -join " / ")) }
        $acceptance = @(Get-TeamBoardField $Card "acceptance" @())
        if (@($acceptance).Count -gt 0) { $lines.Add($pad + "kabul: " + ($acceptance -join " / ")) }
    }
    $lines.Add($pad + ("cevap: board.ps1 post -Kind cevap -ReplyTo {0} -Choice <A/B/C | 'başka: ...'> -Text '<neden>'; bağlam: board.ps1 context -Note {0}" -f $Note.id))
    return $lines.ToArray()
}

function Format-TeamBoardContext {
    <# `board.ps1 context`: the note, the asker's card, the answers so far and the branch diff. #>
    param([Parameter(Mandatory = $true)]$Context, [AllowEmptyCollection()][string[]]$DiffStat = @(), [string]$DiffHead = "")
    $note = $Context.note
    $card = Get-TeamBoardField $Context "card" $null
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add((Format-TeamBoardLine -Note $note).Trim())
    if ([string]$note.kind -eq "danisma") { foreach ($l in (Format-TeamBoardConsult -Note $note)) { $lines.Add($l) } }
    if ($null -ne $card) {
        $lines.Add("Kart: " + (Get-TeamBoardField $card "title") + "  (" + $note.task + ")")
        foreach ($l in @(Get-TeamBoardField $card "goal" @())) { $lines.Add("  Hedef: " + $l) }
        foreach ($l in @(Get-TeamBoardField $card "acceptance" @())) { $lines.Add("  Kabul: " + $l) }
        $area = @(Get-TeamBoardField $card "area" @())
        if (@($area).Count -gt 0) { $lines.Add("  Alan: " + ($area -join ", ")) }
    }
    else { $lines.Add("Kart: kuyrukta bulunamadı (" + $note.task + ")") }
    $answers = @(Get-TeamBoardField $Context "answers" @())
    if (@($answers).Count -eq 0) { $lines.Add("Cevaplar: henüz yok") }
    foreach ($a in $answers) { $lines.Add("Cevap: " + (Format-TeamBoardLine -Note $a).Trim()) }
    if ($DiffHead) {
        $lines.Add($DiffHead)
        foreach ($l in @($DiffStat)) { $lines.Add("  " + $l) }
    }
    return $lines.ToArray()
}

function Get-TeamBoardBranchDiff {
    <# `git diff --stat main...<branch>` of the asker's branch (local, else origin/): the head
       line and the stat lines; never throws. #>
    param([string]$Branch, [string]$RepoRoot)
    if (-not $Branch) { return [pscustomobject]@{ Head = "Dal farkı: kartta dal yok"; Lines = @() } }
    $head = "Dal farkı (main...$Branch):"
    # git's stderr (an unknown ref) must not become a terminating error under the caller's Stop
    $ErrorActionPreference = "Continue"
    foreach ($ref in @($Branch, "origin/$Branch")) {
        try { $out = @(& git -C $RepoRoot diff --stat "main...$ref" 2>$null) } catch { continue }
        if ($LASTEXITCODE -eq 0) {
            $stat = @($out | Where-Object { [string]$_ } | ForEach-Object { ([string]$_).Trim() })
            if (@($stat).Count -eq 0) { $stat = @("fark yok") }
            if (@($stat).Count -gt 25) { $stat = @($stat[0..19]) + @("...") + @($stat[-1]) }
            return [pscustomobject]@{ Head = $head; Lines = $stat }
        }
    }
    return [pscustomobject]@{ Head = $head; Lines = @("alınamadı (dal bu makinede yok)") }
}

function Format-TeamBoardRead {
    <# The lines `board.ps1 read` prints: at most TeamBoardReadMax notes, newest last; under a
       danisma its options and its asker's card (-Cards, by task id). #>
    param([AllowEmptyCollection()][object[]]$Notes = @(), [string]$For = "", $Cards = $null)
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
    foreach ($note in $list) {
        $lines.Add((Format-TeamBoardLine -Note $note -For $For))
        if ([string]$note.kind -eq "danisma") {
            $card = Get-TeamBoardField $Cards ([string]$note.task) $null
            foreach ($l in (Format-TeamBoardConsult -Note $note -Card $card)) { $lines.Add($l) }
        }
    }
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
