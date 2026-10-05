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

function Test-TestTeamStagingUrl {
    <#
    .SYNOPSIS
        Whether a url is the staging stack: http, host 127.0.0.1 or localhost, port 28000 (web)
        or 28001 (api), no user part. Everything else - the dev stack, production, the tailnet
        name, another loopback address, a url with credentials in it - is not.
    .PARAMETER AllowTestPort
        For the tests only: one more port on 127.0.0.1 (a local stand-in for staging). Never a
        host other than 127.0.0.1.
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
    if ($AllowTestPort -gt 0 -and $hostName -eq "127.0.0.1" -and $uri.Port -eq $AllowTestPort) { return $true }
    return (($script:TestTeamStagingHosts -contains $hostName) -and ($script:TestTeamStagingPorts -contains $uri.Port))
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
        if (-not $family -or -not $scenario) { throw "bir iş bir aile ve bir senaryo dosyası adlandırır: $($job | ConvertTo-Json -Compress)" }
        if ($seen.ContainsKey($family)) { throw "'$family' ailesi planda iki kez" }
        $seen[$family] = $true
        $n++
        [void]$cards.Add([pscustomobject]@{
                id             = "tj-$Round-$n"
                tester         = "tester-" + ((($n - 1) % $script:TestTeamTesters) + 1)
                family         = $family
                scenario       = $scenario
                improvise      = [bool](Get-TeamProperty -InputObject $job -Name "improvise" -Default $false)
                state          = "planned"
                forwarded_task = ""
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
                alike      = New-Object System.Collections.ArrayList
            }
        $byActual[$actualKey] = $failure
        [void]$out.Add($failure)
    }
    return @($out.ToArray())
}

function Get-TestTeamFailureKey {
    param([Parameter(Mandatory = $true)]$Failure)
    return ("{0}|{1}|{2}" -f ([string]$Failure.scenario).ToLowerInvariant(), ([string]$Failure.step).ToLowerInvariant(), ([string]$Failure.actual).ToLowerInvariant())
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
    $sha = if ($StagingSha) { $StagingSha } else { "(staging sürümü okunamadı)" }
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
        $tried = @(Get-TeamProperty -InputObject $breaking -Name "tried" -Default @())
        $ladder = (@($tried) | ForEach-Object { "{0}:{1}/{2}" -f $_.load, $_.ok, ([int]$_.ok + [int]$_.errors) }) -join ", "
        $first = Get-TeamProperty -InputObject $breaking -Name "first_failure"
        $who = "{0} ({1}, {2})" -f $result.family, $result.tester, $result.card
        # A scenario whose steps already failed: its ladder measures that failure, not a load.
        if ([string](Get-TeamProperty -InputObject $result -Name "state" -Default "") -eq "failed") {
            [void]$lines.Add(("- ölçüm geçersiz {0}: {1} - senaryonun adımları kaldı, merdiven o hatayı ölçtü (merdiven: {2})" -f $who, $what, $ladder))
            continue
        }
        if ($null -ne $first) {
            [void]$broke.Add([pscustomobject]@{ Who = $who; What = $what; First = $first })
            [void]$lines.Add(("- KIRILDI {0}: {1} - ilk kırılan yük {2}, {3} hata / {4} istek, p95 {5} ms (merdiven: {6})" -f $who, $what, $first.load, $first.errors, ([int]$first.ok + [int]$first.errors), $first.p95_ms, $ladder))
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
    return [pscustomobject]@{ Markdown = (($lines.ToArray()) -join "`n") + "`n"; Note = $note }
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
        $first = if ($null -ne $breaking) { Get-TeamProperty -InputObject $breaking -Name "first_failure" } else { $null }
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
