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

function ConvertTo-TestTeamFailureTask {
    <#
    .SYNOPSIS
        One failure as a normal card of the software queue: state 'proposed' (the software
        Proje Yöneticisi decides and splits it), no area yet, and a goal that reproduces it -
        the steps, the expected, the actual, the scenario file, the screenshot, the staging sha.
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
    ) -join "`n"
    return [pscustomobject]@{
        id                = (Get-TestTeamFailureTaskId -Failure $Failure)
        title             = ("Test ekibi: {0} - {1}" -f $Failure.family, $Failure.step)
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
        goal              = $goal
        acceptance        = "Senaryo $($Failure.scenario) staging'de geçer (test ekibinin yeniden testi kartı kapatır); hata için bir regresyon testi."
        evidence_expected = "regresyon testi, staging'de yeniden test dökümü"
        reason            = "test ekibi: $($Failure.card) ($($Failure.tester)), tur $Round"
    }
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
