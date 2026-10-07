<#
.SYNOPSIS
    The test team's decisions (team/plans/test-team-adr.md): staging only, its seats, its
    cards, the failure it forwards, the breaking-point report it sends to the Danışman.

.DESCRIPTION
    The owner's design of 2026-10-03: a SEPARATE team - one Test Proje Yöneticisi (test-lead)
    and four test çalışanları (tester-1..4) - uses STAGING as the owner would and looks for
    where it breaks. The test lead deals jobs (one scenario family a job) to the testers; the
    results come back to the test lead only; every failure goes to the software Proje
    Yöneticisi as a normal card of the queue (state 'proposed': the software PM decides); the
    breaking-point report goes to the Danışman, never to the owner.

    Functions take their inputs and return; scripts/testteam/test-round.ps1 and
    run-scenario.ps1 are what touch the disk and the network. Needs scripts/lib/TeamQueue.ps1
    (the cap, the card states). Windows PowerShell 5.1, StrictMode.
#>

Set-StrictMode -Version Latest

# Staging on the home PC (scripts/staging/up.ps1): the api and the web shell, nothing else.
$script:TestTeamStagingHosts = @("127.0.0.1", "localhost")
$script:TestTeamStagingPorts = @(28000, 28001)
$script:TestTeamStagingApi = "http://127.0.0.1:28001"
$script:TestTeamTesters = 4
$script:TestTeamNoteMax = 280
# The tests' stand-ins listen on a random port of this range; nothing of the product does (the
# dev api :8000, the web shell :3000, Temporal :7233, Postgres :5432 are all outside it).
$script:TestTeamTestPortLow = 41000
$script:TestTeamTestPortHigh = 49999
# Where the Danışman reads the breaking-point report on the board.
$script:TestTeamAdvisorSeat = "danisman"
# A fix is on staging only once the queue says it left the branch for main.
$script:TestTeamRetestStates = @("released", "done", "awaiting_real_evidence")
# services/api/app/team/board.py TASK_PATTERN (case-sensitive there: compare with -cmatch). The
# board allows RATE_PER_TASK_HOUR notes per task: a job posts under its own id, a round under its id.
$script:TestTeamBoardTaskPattern = '^[a-z0-9][a-z0-9-]{2,63}$'

function Get-TestTeamBoardTask {
    <#
    .SYNOPSIS
        The board task a note of a round (-Id <round>) or of a job (-Id <card id>) is posted
        under: the id in lowercase, 'test-<id>' when that is shorter than the board takes
        ('t1' -> 'test-t1'). An id that still is no task id is refused here, not by a 422.
    #>
    param([string]$Id)
    # An empty id would become 'test-', which the board takes: one shared task again.
    if (-not $Id) { throw "boş kimlik bir pano görev kimliği olamaz" }
    $task = $Id.ToLowerInvariant()
    if ($task -cnotmatch $script:TestTeamBoardTaskPattern) { $task = "test-$task" }
    if ($task -cnotmatch $script:TestTeamBoardTaskPattern) { throw "'$Id' bir pano görev kimliği olamaz ($script:TestTeamBoardTaskPattern)" }
    return $task
}

function Test-TestTeamTestPort {
    <# Whether -AllowTestPort names a port of the tests' range (41000-49999); 0 is "none". #>
    param([int]$Port)
    return ($Port -ge $script:TestTeamTestPortLow -and $Port -le $script:TestTeamTestPortHigh)
}

function Test-TestTeamStagingUrl {
    <#
    .SYNOPSIS
        Whether a url is the staging stack: http, host 127.0.0.1 or localhost, port 28000 (web)
        or 28001 (api), no user part. Everything else - the dev stack, production, the tailnet
        name, another loopback address, a url with credentials in it - is not.
    .PARAMETER AllowTestPort
        For the tests only: one more port on 127.0.0.1 (a local stand-in for staging), and only
        a port of the tests' range 41000-49999 - never the dev api's :8000 or another service's.
    #>
    param([AllowEmptyString()][string]$Url, [int]$AllowTestPort = 0)
    if (-not $Url) { return $false }
    $uri = $null
    if (-not [System.Uri]::TryCreate($Url, [System.UriKind]::Absolute, [ref]$uri)) { return $false }
    if ($uri.Scheme -ne "http") { return $false }
    if ($uri.UserInfo) { return $false }
    if ($Url -notmatch '^(?i)http://[^/@]+(/|$|\?)') { return $false }
    if ($uri.IsDefaultPort) { return $false }
    $hostName = $uri.Host.ToLowerInvariant()
    if ((Test-TestTeamTestPort -Port $AllowTestPort) -and $hostName -eq "127.0.0.1" -and $uri.Port -eq $AllowTestPort) { return $true }
    return (($script:TestTeamStagingHosts -contains $hostName) -and ($script:TestTeamStagingPorts -contains $uri.Port))
}

function Test-TestTeamPathOutside {
    <# Whether a path lies outside a folder (the checkout): a round's results, logs and staging
       screenshots are run data, never files of the tracked tree. #>
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Root)
    $full = [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/') + '\'
    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/') + '\'
    return (-not $full.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase))
}

function Get-TestTeamP95 {
    <# The 95th percentile of request durations (ms), nearest rank; 0 for none. #>
    param([AllowEmptyCollection()][object[]]$Timings = @())
    $sorted = @(@($Timings) | ForEach-Object { [int64]$_ } | Sort-Object)
    if ($sorted.Count -eq 0) { return 0 }
    return [int64]$sorted[[Math]::Min($sorted.Count - 1, [int][Math]::Ceiling(0.95 * $sorted.Count) - 1)]
}

function Test-TestTeamLoadBroken {
    <# Whether one load of the ladder broke: an error, or a p95 over the scenario's limit. #>
    param([Parameter(Mandatory = $true)]$Measured, [int]$LimitMs = 0)
    return ([int]$Measured.errors -gt 0 -or ($LimitMs -gt 0 -and [int64]$Measured.p95_ms -gt $LimitMs))
}

function Get-TestTeamRetestDecision {
    <#
    .SYNOPSIS
        Whether a failed card is re-run now: its forwarded task has left for main (released /
        done / awaiting_real_evidence - 'merged' is an integration branch, not staging), and the
        staging stack answers a sha that is NOT the one the failure was found on (a staging that
        was not redeployed still serves the bug; re-running it would reopen a fixed card).
    #>
    param([AllowEmptyString()][string]$TaskState, [AllowEmptyString()][string]$FoundSha, [AllowEmptyString()][string]$StagingSha)
    if ($script:TestTeamRetestStates -notcontains $TaskState) {
        return [pscustomobject]@{ Due = $false; Why = "düzeltme henüz main'e çıkmadı ($TaskState)" }
    }
    if (-not $StagingSha) { return [pscustomobject]@{ Due = $false; Why = "staging sürümü okunamadı" } }
    if ($FoundSha -and $FoundSha.ToLowerInvariant() -eq $StagingSha.ToLowerInvariant()) {
        return [pscustomobject]@{ Due = $false; Why = "staging hâlâ hatanın bulunduğu sürümde ($StagingSha): düzeltme staging'e kurulmadı" }
    }
    return [pscustomobject]@{ Due = $true; Why = "" }
}

function Get-TestTeamScenarioKey {
    <#
    .SYNOPSIS
        One spelling of a scenario path for the failure's id: slashes forward, lower case, and
        the part from 'scripts/testteam/scenarios/' on when it is there (an absolute path in the
        main checkout, one in a worktree and the relative one are the same scenario).
    #>
    param([AllowEmptyString()][string]$Scenario)
    $key = ([string]$Scenario).Trim().Replace('\', '/').ToLowerInvariant()
    $at = $key.LastIndexOf("scripts/testteam/scenarios/")
    if ($at -ge 0) { return $key.Substring($at) }
    while ($key.StartsWith("./")) { $key = $key.Substring(2) }
    return $key
}

function Get-TestTeamSeats {
    $seats = @("test-lead")
    for ($n = 1; $n -le $script:TestTeamTesters; $n++) { $seats += "tester-$n" }
    return $seats
}

function New-TestTeamCards {
    <#
    .SYNOPSIS
        The test lead's plan as cards: one job is one scenario family, dealt to tester-1..4 in
        turn. A family twice in one plan is refused (two testers on one family is one job done
        twice).
    #>
    param([Parameter(Mandatory = $true)][string]$Round, [Parameter(Mandatory = $true)][object[]]$Jobs)
    $seen = @{}
    $cards = New-Object System.Collections.ArrayList
    $n = 0
    foreach ($job in @($Jobs)) {
        $family = [string](Get-TeamProperty -InputObject $job -Name "family" -Default "")
        $scenario = [string](Get-TeamProperty -InputObject $job -Name "scenario" -Default "")
        $improvise = [bool](Get-TeamProperty -InputObject $job -Name "improvise" -Default $false)
        # test-lead.md: a family with no scenario file yet is an improvise job, and its tester
        # writes the first file in the round folder (2026-10-06: the first real plan named two
        # such families and the round died here, before any tester started).
        if (-not $family -or (-not $scenario -and -not $improvise)) { throw "bir iş bir aile ve bir senaryo dosyası adlandırır (dosyası olmayan aile improvise: true ister): $($job | ConvertTo-Json -Compress)" }
        if ($seen.ContainsKey($family)) { throw "'$family' ailesi planda iki kez" }
        $seen[$family] = $true
        $n++
        [void]$cards.Add([pscustomobject]@{
                id             = "tj-$Round-$n"
                tester         = "tester-" + ((($n - 1) % $script:TestTeamTesters) + 1)
                family         = $family
                scenario       = $scenario
                improvise      = $improvise
                state          = "planned"
                forwarded_task = ""
                found_sha      = ""
                reopened       = 0
            })
    }
    return @($cards.ToArray())
}

function Set-TestTeamCardState {
    <# Move a card; a move the card states do not allow throws. #>
    param([Parameter(Mandatory = $true)]$Card, [Parameter(Mandatory = $true)][string]$To)
    $from = [string]$Card.state
    if (-not (Test-TeamTestCardMove -From $from -To $To)) { throw "test kartı $($Card.id): '$from' -> '$To' geçişi yok" }
    $Card.state = $To
}

function Get-TestTeamFailures {
    <# The failed steps of one result, one failure per distinct actual: the steps after the
       first that failed with the same actual are one cause, named in its 'alike' (the first
       real round, 2026-10-05: a staging a release behind answered 404 to every watch step). #>
    param([Parameter(Mandatory = $true)]$Result)
    $out = New-Object System.Collections.ArrayList
    if ([string](Get-TeamProperty -InputObject $Result -Name "state" -Default "") -ne "failed") { return @() }
    $steps = @(Get-TeamProperty -InputObject $Result -Name "steps" -Default @())
    $trail = New-Object System.Collections.ArrayList
    $byActual = @{}
    foreach ($step in $steps) {
        $label = [string](Get-TeamProperty -InputObject $step -Name "name" -Default "")
        $method = [string](Get-TeamProperty -InputObject $step -Name "method" -Default "")
        $path = [string](Get-TeamProperty -InputObject $step -Name "path" -Default "")
        [void]$trail.Add((("{0} {1} {2}" -f $label, $method, $path) -replace '\s+', ' ').Trim())
        if ([bool](Get-TeamProperty -InputObject $step -Name "ok" -Default $true)) { continue }
        $actualKey = ([string](Get-TeamProperty -InputObject $step -Name "actual" -Default "")).ToLowerInvariant()
        if ($byActual.ContainsKey($actualKey)) { [void]$byActual[$actualKey].alike.Add($label); continue }
        $failure = [pscustomobject]@{
                card       = [string](Get-TeamProperty -InputObject $Result -Name "card" -Default "")
                tester     = [string](Get-TeamProperty -InputObject $Result -Name "tester" -Default "")
                family     = [string](Get-TeamProperty -InputObject $Result -Name "family" -Default "")
                scenario   = [string](Get-TeamProperty -InputObject $Result -Name "scenario" -Default "")
                step       = $label
                steps      = @($trail.ToArray())
                expected   = [string](Get-TeamProperty -InputObject $step -Name "expected" -Default "")
                actual     = [string](Get-TeamProperty -InputObject $step -Name "actual" -Default "")
                screenshot = [string](Get-TeamProperty -InputObject $Result -Name "screenshot" -Default "")
                staging_sha = [string](Get-TeamProperty -InputObject $Result -Name "staging_sha" -Default "")
                alike      = New-Object System.Collections.ArrayList
            }
        $byActual[$actualKey] = $failure
        [void]$out.Add($failure)
    }
    return @($out.ToArray())
}

function Get-TestTeamFailureKey {
    param([Parameter(Mandatory = $true)]$Failure)
    return ("{0}|{1}|{2}" -f (Get-TestTeamScenarioKey -Scenario ([string]$Failure.scenario)),([string]$Failure.step).ToLowerInvariant(), ([string]$Failure.actual).ToLowerInvariant())
}

function Merge-TestTeamFailures {
    <# Two testers who met the same failure (scenario, step, actual) are one card. #>
    param([AllowEmptyCollection()][object[]]$Failures = @())
    $seen = @{}
    $out = New-Object System.Collections.ArrayList
    foreach ($failure in @($Failures)) {
        $key = Get-TestTeamFailureKey -Failure $failure
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        [void]$out.Add($failure)
    }
    return @($out.ToArray())
}

function Get-TestTeamFailureTaskId {
    <# A stable id per failure: the same failure found in the next round is the same task. #>
    param([Parameter(Mandatory = $true)]$Failure)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes((Get-TestTeamFailureKey -Failure $Failure))) }
    finally { $sha.Dispose() }
    $hex = (($bytes | Select-Object -First 5) | ForEach-Object { $_.ToString("x2") }) -join ""
    $family = (([string]$Failure.family).ToLowerInvariant() -replace '[^a-z0-9]+', '-').Trim('-')
    if (-not $family) { $family = "senaryo" }
    if ($family.Length -gt 30) { $family = $family.Substring(0, 30).Trim('-') }
    return "test-fail-$family-$hex"
}

# A scenario family's known code paths: a forwarded card's first area, so the Proje Yöneticisi
# widens rather than invents (2026-10-06: 19 cards forwarded with no area; seven moved into work
# as they were and stopped the whole team). A family not named here carries no area.
$script:TestTeamFamilyAreas = @{
    "nobet"             = @("services/api/app/watch/")
    "ev-stoku"          = @("services/api/app/household/")
    "alarm"             = @("services/api/app/alarms/")
    "dil-dayanikliligi" = @("services/api/app/voice/understanding/")
    "yanlis-duyulan"    = @("services/api/app/voice/misheard/")
    "saglik"            = @("services/api/app/health.py")
}

function Get-TestTeamFamilyArea {
    <# The first area of a failure's card: its family's known code paths, or none. #>
    param([string]$Family)
    $key = (([string]$Family).ToLowerInvariant() -replace '[^a-z0-9]+', '-').Trim('-')
    if ($script:TestTeamFamilyAreas.ContainsKey($key)) { return [string[]]@($script:TestTeamFamilyAreas[$key]) }
    return [string[]]@()
}

# The prefix of every test card's reason and proposal: the Proje Yöneticisi's triage finds them by it.
$script:TestTeamPmPrefix = "Test PY bulgusu:"
# At most this many cards a round; the rest go into one summary card (2026-10-06: 61 in two rounds).
$script:TestTeamMaxForwards = 10

function ConvertTo-TestTeamFoldedText {
    <# Lower case, Turkish letters folded to ASCII, runs of white space one: 'BİRLEŞTİRİLDİ' and
       'birlestirildi', 'Saat  kaçta' and 'saat kacta' are one text. #>
    param([string]$Text)
    $t = ([string]$Text).Replace([string][char]0x0130, "i").Replace("I", "i").ToLowerInvariant()
    $t = $t.Replace([string][char]0x0307, "")
    foreach ($pair in @(@([char]0x0131, "i"), @([char]0x015F, "s"), @([char]0x011F, "g"), @([char]0x00FC, "u"), @([char]0x00F6, "o"), @([char]0x00E7, "c"))) { $t = $t.Replace([string]$pair[0], [string]$pair[1]) }
    return (($t -replace '\s+', ' ').Trim())
}

function Get-TestTeamSignature {
    param([string]$Family, [string]$Step, [string]$Expected)
    $key = (([string]$Family).ToLowerInvariant() -replace '[^a-z0-9]+', '-').Trim('-')
    return ("{0}|{1}|{2}" -f $key, (ConvertTo-TestTeamFoldedText $Step), (ConvertTo-TestTeamFoldedText $Expected))
}

function Get-TestTeamFailureSignature {
    <# What makes a failing step the same finding across rounds: family, step name, expected -
       not the actual, not the scenario's path (2026-10-06: one sentence was three cards). #>
    param([Parameter(Mandatory = $true)]$Failure)
    return (Get-TestTeamSignature -Family ([string]$Failure.family) -Step ([string]$Failure.step) -Expected ([string]$Failure.expected))
}

function Get-TestTeamTaskSignatures {
    <# The signatures a queue card holds: its 'İmza:' lines, or - a card written before them (the
       61 of 2026-10-06, the ones folded by hand) - its title 'Test ekibi: <family> - <step>' and
       its 'Beklenen:' line. A card that is no test card holds none. #>
    param([Parameter(Mandatory = $true)]$Task)
    $goal = [string](Get-TeamProperty -InputObject $Task -Name "goal" -Default "")
    $lines = @([regex]::Matches($goal, '(?m)^İmza: (.+?)\s*$') | ForEach-Object { $_.Groups[1].Value })
    if (@($lines).Count -gt 0) { return [string[]]@($lines) }
    $title = [regex]::Match([string](Get-TeamProperty -InputObject $Task -Name "title" -Default ""), '^Test ekibi: (.+?) - (.+)$')
    if (-not $title.Success) { return [string[]]@() }
    $expected = [regex]::Match($goal, '(?m)^Beklenen: (.*?)\s*$').Groups[1].Value
    return [string[]]@(Get-TestTeamSignature -Family $title.Groups[1].Value -Step $title.Groups[2].Value -Expected $expected)
}

function Test-TestTeamTaskHolds {
    <# Whether a card still holds its signatures: open (any state but done / released), or done
       because it was folded into another card or split into cards. A card done by its fix does
       not: the same step failing again is news. #>
    param([Parameter(Mandatory = $true)]$Task)
    $state = [string](Get-TeamProperty -InputObject $Task -Name "state" -Default "")
    if (@("done", "released") -notcontains $state) { return $true }
    $reason = ConvertTo-TestTeamFoldedText ([string](Get-TeamProperty -InputObject $Task -Name "reason" -Default ""))
    return ($state -eq "done" -and $reason -match 'birlestirildi|bolundu')
}

function Select-TestTeamForwards {
    <#
    .SYNOPSIS
        Which of a round's failures become cards: one per signature not held by a card of the
        store (Test-TestTeamTaskHolds), at most $script:TestTeamMaxForwards; the rest are one
        summary card (Summary, or $null). Held lists the failures a card already holds.
    #>
    param([AllowEmptyCollection()][object[]]$Failures = @(), [AllowEmptyCollection()][object[]]$Tasks = @(), [Parameter(Mandatory = $true)][string]$Round, [string]$StagingSha = "", [string]$Now = "")
    $held = @{}
    foreach ($task in @($Tasks)) {
        if (-not (Test-TestTeamTaskHolds -Task $task)) { continue }
        foreach ($signature in @(Get-TestTeamTaskSignatures -Task $task)) { $held[$signature] = [string]$task.id }
    }
    $forward = New-Object System.Collections.ArrayList
    $rest = New-Object System.Collections.ArrayList
    $skipped = New-Object System.Collections.ArrayList
    foreach ($failure in @($Failures)) {
        $signature = Get-TestTeamFailureSignature -Failure $failure
        if ($held.ContainsKey($signature)) { [void]$skipped.Add([pscustomobject]@{ Failure = $failure; By = $held[$signature] }); continue }
        # Held by the card this round opens for it - its id, so the retest finds that card.
        if ($forward.Count -lt $script:TestTeamMaxForwards) { [void]$forward.Add($failure); $held[$signature] = Get-TestTeamFailureTaskId -Failure $failure }
        else { [void]$rest.Add($failure); $held[$signature] = Get-TestTeamSummaryTaskId -Round $Round }
    }
    $summary = $null
    if ($rest.Count -gt 0) { $summary = ConvertTo-TestTeamSummaryTask -Failures @($rest.ToArray()) -Round $Round -StagingSha $StagingSha -Now $Now }
    return [pscustomobject]@{ Forward = @($forward.ToArray()); Summary = $summary; Summarised = @($rest.ToArray()); Held = @($skipped.ToArray()) }
}

function Get-TestTeamSummaryTaskId {
    param([Parameter(Mandatory = $true)][string]$Round)
    return ("test-fail-ozet-" + $Round)
}

function ConvertTo-TestTeamProposalLine {
    <# A proposal as ONE line free of what a Windows path refuses: cycle.ps1 Send-IdeaTexts reads
       every proposal with Path.GetFileName, which on PowerShell 5.1 throws on a newline, a control
       character or '"<>|' and stops the cycle (inspection of 2026-10-07). Lines are joined ' ; '. #>
    param([string]$Text)
    $t = ([string]$Text).Replace("->", [string][char]0x2192).Replace('"', "'").Replace("<", [string][char]0x2039).Replace(">", [string][char]0x203A).Replace("|", "/")
    $t = $t -replace '\s*[\r\n]+\s*', ' ; '
    $t = $t -replace '[\x00-\x1F]', ' '
    return (($t -replace ' {2,}', ' ').Trim())
}

function New-TestTeamPmTask {
    # A test card's common shape: 'proposed' with a one-line, path-safe proposal and NO area, so the cycle
    # hands it to the Proje Yöneticisi's split (Test-TeamSplitCandidate) - never to the owner's
    # gate, where a plain 'proposed' card goes (Get-TeamNextRole).
    param([string]$Id, [string]$Title, [string]$Goal, [string]$Proposal, [string]$Acceptance, [string]$Reason, [string]$Now)
    return [pscustomobject]@{
        id                = $Id
        title             = $Title
        roadmap_row       = "Repairs and improves itself"
        state             = "proposed"
        area              = @()
        branch            = ""
        worktree          = ""
        assignee          = ""
        reports           = @()
        budget            = [pscustomobject]@{ max_usd = 0 }
        created_at        = $Now
        updated_at        = $Now
        goal              = $Goal
        acceptance        = $Acceptance
        evidence_expected = "regresyon testi, staging'de yeniden test dökümü"
        reason            = "$($script:TestTeamPmPrefix) $Reason"
        proposal          = (ConvertTo-TestTeamProposalLine $Proposal)
    }
}

function Get-TestTeamPmInstruction {
    param([string]$Family)
    $area = @(Get-TestTeamFamilyArea -Family $Family)
    $line = "Proje Yöneticisi: aynı aileden ($Family) açık Test PY bulgusu kartlarını tek kartta birleştir (diğerleri done, 'BİRLEŞTİRİLDİ -> <kart>'), ona bir dosya alanı ver."
    if ($area.Count -gt 0) { $line += " Önerilen ilk alan: " + ($area -join ", ") }
    return $line
}

function ConvertTo-TestTeamSummaryTask {
    <# The failures over a round's limit, as ONE card: each named with its step, expected, actual
       and scenario, and its 'İmza:' line, so the next round opens none of them again. #>
    param([Parameter(Mandatory = $true)][object[]]$Failures, [Parameter(Mandatory = $true)][string]$Round, [string]$StagingSha = "", [string]$Now = "")
    if (-not $Now) { $Now = Get-TeamTimestamp }
    $families = @($Failures | ForEach-Object { [string]$_.family } | Select-Object -Unique)
    $lines = New-Object System.Collections.ArrayList
    [void]$lines.Add("Test ekibi staging'de tur $Round içinde $($script:TestTeamMaxForwards) karttan fazla hata buldu; kalan $(@($Failures).Count) hata bu tek kartta.")
    foreach ($failure in @($Failures)) {
        [void]$lines.Add(("- {0} / {1}: beklenen {2}, gerçekleşen {3} (senaryo {4}, {5})" -f $failure.family, $failure.step, $failure.expected, $failure.actual, $failure.scenario, $failure.card))
    }
    if ($StagingSha) { [void]$lines.Add("Staging sha: $StagingSha") }
    foreach ($failure in @($Failures)) { [void]$lines.Add("İmza: " + (Get-TestTeamFailureSignature -Failure $failure)) }
    $goal = ($lines.ToArray()) -join "`n"
    $instruction = (@($families | ForEach-Object { Get-TestTeamPmInstruction -Family $_ }) -join "`n")
    return (New-TestTeamPmTask -Id (Get-TestTeamSummaryTaskId -Round $Round) -Title ("Test ekibi: tur {0} - {1} hata daha (özet)" -f $Round, @($Failures).Count) `
            -Goal $goal -Proposal ("$($script:TestTeamPmPrefix) tur $Round özeti, $(@($Failures).Count) hata ($($families -join ', ')).`n$goal`n$instruction") `
            -Acceptance "Her hatanın senaryosu staging'de geçer; her hata için bir regresyon testi." -Reason "tur $Round, sınır üstü $(@($Failures).Count) hata" -Now $Now)
}

function ConvertTo-TestTeamFailureTask {
    <#
    .SYNOPSIS
        One failure as a card of the software queue for the Proje Yöneticisi's triage: state
        'proposed', reason and proposal prefixed 'Test PY bulgusu:', no area (the PM's split
        gives it; the family's known code paths, Get-TestTeamFamilyArea, are named in the
        proposal as the first area), and a goal that reproduces it - the steps, the expected,
        the actual, the scenario file, the screenshot, the staging sha - and its 'İmza:' line.
    #>
    param([Parameter(Mandatory = $true)]$Failure, [string]$StagingSha = "", [Parameter(Mandatory = $true)][string]$Round, [string]$Now = "")
    if (-not $Now) { $Now = Get-TeamTimestamp }
    $steps = @($Failure.steps)
    $numbered = for ($i = 0; $i -lt $steps.Count; $i++) { "{0}. {1}" -f ($i + 1), $steps[$i] }
    $shot = if ([string]$Failure.screenshot) { [string]$Failure.screenshot } else { "(ekran görüntüsü yok: api adımı)" }
    $own = [string](Get-TeamProperty -InputObject $Failure -Name "staging_sha" -Default "")
    $sha = if ($own) { $own } elseif ($StagingSha) { $StagingSha } else { "(staging sürümü okunamadı)" }
    $goal = @(
        "Test ekibi staging'de bir hata buldu ($($Failure.tester), $($Failure.card), tur $Round)."
        "Adımlar: " + ($numbered -join " ")
        "Beklenen: $($Failure.expected)"
        "Gerçekleşen: $($Failure.actual)"
        $(if (@(Get-TeamProperty -InputObject $Failure -Name "alike" -Default @()).Count -gt 0) { "Aynı sonuçla kalan adımlar: " + (@($Failure.alike) -join ", ") })
        "Senaryo: $($Failure.scenario)"
        "Ekran görüntüsü: $shot"
        "Staging sha: $sha"
        "İmza: $(Get-TestTeamFailureSignature -Failure $Failure)"
    ) -join "`n"
    $title = "Test ekibi: {0} - {1}" -f $Failure.family, $Failure.step
    return (New-TestTeamPmTask -Id (Get-TestTeamFailureTaskId -Failure $Failure) -Title $title -Goal $goal `
            -Proposal ("$($script:TestTeamPmPrefix) $title`n$goal`n" + (Get-TestTeamPmInstruction -Family ([string]$Failure.family))) `
            -Acceptance "Senaryo $($Failure.scenario) staging'de geçer (test ekibinin yeniden testi kartı kapatır); hata için bir regresyon testi." `
            -Reason "test ekibi: $($Failure.card) ($($Failure.tester)), tur $Round" -Now $Now)
}

function Format-TestTeamBreakingReport {
    <#
    .SYNOPSIS
        The test lead's 'kopma noktası' report of a round: Markdown for the file, and one board
        note (at most 280 characters) addressed to the Danışman. The first failing load or
        combination with its numbers - or what was tried and that nothing broke.
    #>
    param([Parameter(Mandatory = $true)][string]$Round, [AllowEmptyCollection()][object[]]$Results = @(), [string]$StagingSha = "")
    $lines = New-Object System.Collections.ArrayList
    [void]$lines.Add("# Kopma noktası - test turu $Round")
    [void]$lines.Add("")
    [void]$lines.Add("Alıcı: Danışman. Staging sha: " + $(if ($StagingSha) { $StagingSha } else { "okunamadı" }))
    [void]$lines.Add("")
    $broke = New-Object System.Collections.ArrayList
    $held = New-Object System.Collections.ArrayList
    foreach ($result in @($Results)) {
        $breaking = Get-TeamProperty -InputObject $result -Name "breaking"
        if ($null -eq $breaking) { continue }
        $what = [string](Get-TeamProperty -InputObject $breaking -Name "what" -Default "")
        $tried = @(@(Get-TeamProperty -InputObject $breaking -Name "tried" -Default @()) | ForEach-Object { ConvertTo-TestTeamLadderStep -Step $_ })
        $ladder = (@($tried) | ForEach-Object { "{0}:{1}/{2}" -f $_.load, $_.ok, ([int]$_.ok + [int]$_.errors) }) -join ", "
        $first = ConvertTo-TestTeamLadderStep -Step (Get-TeamProperty -InputObject $breaking -Name "first_failure")
        # StrictMode: a result a tester wrote by hand (an improvised run) may carry no tester or card
        # (2026-10-06: the first real round died here, after every tester had finished).
        $who = "{0} ({1}, {2})" -f [string](Get-TeamProperty -InputObject $result -Name "family" -Default "?"), [string](Get-TeamProperty -InputObject $result -Name "tester" -Default "?"), [string](Get-TeamProperty -InputObject $result -Name "card" -Default "?")
        # A scenario whose steps already failed: its ladder measures that failure, not a load.
        if ([string](Get-TeamProperty -InputObject $result -Name "state" -Default "") -eq "failed") {
            [void]$lines.Add(("- ölçüm geçersiz {0}: {1} - senaryonun adımları kaldı, merdiven o hatayı ölçtü (merdiven: {2})" -f $who, $what, $ladder))
            continue
        }
        if ($null -ne $first) {
            [void]$broke.Add([pscustomobject]@{ Who = $who; What = $what; First = $first })
            $again = ConvertTo-TestTeamLadderStep -Step $first.repeat
            $confirmed = if ($null -ne $again) { "; yinelendi: {0} hata, p95 {1} ms" -f $again.errors, $again.p95_ms } else { "" }
            [void]$lines.Add(("- KIRILDI {0}: {1} - ilk kırılan yük {2}, {3} hata / {4} istek, istek başına p95 {5} ms{6} (merdiven: {7})" -f $who, $what, $first.load, $first.errors, ([int]$first.ok + [int]$first.errors), $first.p95_ms, $confirmed, $ladder))
        }
        else {
            [void]$held.Add($what)
            $top = if (@($tried).Count -gt 0) { [string]@($tried)[-1].load } else { "0" }
            [void]$lines.Add(("- kırılmadı {0}: {1} - {2} yüküne kadar denendi (merdiven: {3})" -f $who, $what, $top, $ladder))
        }
    }
    if (@($broke).Count -eq 0 -and @($held).Count -eq 0) { [void]$lines.Add("- bu turda doğaçlama koşusu yok") }
    if (@($broke).Count -gt 0) {
        $f = @($broke)[0]
        $note = "Danışman'a, test turu ${Round}: kopma noktası $($f.What) - yük $($f.First.load) ilk kırılan ($($f.First.errors) hata / $([int]$f.First.ok + [int]$f.First.errors), p95 $($f.First.p95_ms) ms). Kırılan $(@($broke).Count), kırılmayan $(@($held).Count)."
    }
    else {
        $note = "Danışman'a, test turu ${Round}: kırılma bulunmadı; denenen: " + ((@($held) | Where-Object { $_ }) -join "; ")
    }
    if ($note.Length -gt $script:TestTeamNoteMax) { $note = $note.Substring(0, $script:TestTeamNoteMax - 1) + "…" }
    return [pscustomobject]@{ Markdown = (($lines.ToArray()) -join "`n") + "`n"; Note = $note; To = $script:TestTeamAdvisorSeat }
}

function ConvertTo-TestTeamLadderStep {
    <# One rung of a breaking ladder with every field the reports read; a field a hand-written
       result left out is "?" (p95) or 0 (counts). 2026-10-06: two rounds died on a missing
       'tester' and then a missing 'p95_ms' after every tester had finished. #>
    param($Step)
    if ($null -eq $Step) { return $null }
    $load = Get-TeamProperty -InputObject $Step -Name "load" -Default "?"
    $ok = 0; [void][int]::TryParse([string](Get-TeamProperty -InputObject $Step -Name "ok" -Default 0), [ref]$ok)
    $errors = 0; [void][int]::TryParse([string](Get-TeamProperty -InputObject $Step -Name "errors" -Default 0), [ref]$errors)
    return [pscustomobject]@{
        load   = $load
        ok     = $ok
        errors = $errors
        p95_ms = Get-TeamProperty -InputObject $Step -Name "p95_ms" -Default "?"
        repeat = Get-TeamProperty -InputObject $Step -Name "repeat"
    }
}

function Format-TestTeamSeatNote {
    <#
    .SYNOPSIS
        A tester's board note, the Ofis' Test odası reads it (apps/web/app/core/office/
        officeTestRoom.tsx): a card still planned/running is "iş: <family> (<id>)"; a finished one
        "sonuç: <state> - <family> (<id>)", and for broke " - kopma: yük N, E hata / M, p95 X ms".
    #>
    param([Parameter(Mandatory = $true)]$Card, $Result = $null)
    $job = "{0} ({1})" -f $Card.family, $Card.id
    if (@("passed", "failed", "broke") -notcontains [string]$Card.state) { return "iş: $job" }
    $text = "sonuç: $($Card.state) - $job"
    if ([string]$Card.state -eq "broke" -and $null -ne $Result) {
        $breaking = Get-TeamProperty -InputObject $Result -Name "breaking"
        $first = if ($null -ne $breaking) { ConvertTo-TestTeamLadderStep -Step (Get-TeamProperty -InputObject $breaking -Name "first_failure") } else { $null }
        if ($null -ne $first) { $text += (" - kopma: yük {0}, {1} hata / {2}, p95 {3} ms" -f $first.load, $first.errors, ([int]$first.ok + [int]$first.errors), $first.p95_ms) }
    }
    if ($text.Length -gt $script:TestTeamNoteMax) { $text = $text.Substring(0, $script:TestTeamNoteMax - 1) + "…" }
    return $text
}

function New-TestTeamJobCard {
    <# The prompt of one tester run: its card, its result file, the staging api, the rules. #>
    param([Parameter(Mandatory = $true)]$Card, [Parameter(Mandatory = $true)][string]$ResultFile, [Parameter(Mandatory = $true)][string]$Round, [string]$StagingApi = $script:TestTeamStagingApi)
    return @(
        "# Test job ($Round)"
        ""
        "- id: $($Card.id)"
        "- tester: $($Card.tester)"
        "- family: $($Card.family)"
        "- scenario: $($Card.scenario)"
        "- improvise: $(([string][bool]$Card.improvise).ToLowerInvariant())"
        "- staging_api: $StagingApi"
        "- result_file: $ResultFile"
        ""
        "Run the scenario with scripts/testteam/run-scenario.ps1 -Scenario <file> -Card $($Card.id) -OutDir <folder of result_file>,"
        "then improvise inside the frame of your role file, and write ONE result file at result_file."
        "Report to the Test Proje Yöneticisi only (your final message). Staging only; nothing irreversible."
    ) -join "`n"
}
