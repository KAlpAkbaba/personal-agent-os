<#
.SYNOPSIS
    Every job of the test lead's plan names the JARVIS row it tests (test-plan-names-roadmap-rows,
    team/plans/test-plan-names-roadmap-rows-adr.md).

.DESCRIPTION
    2026-10-07 11:45: the round t-w10071102 on staging d74a8daa passed scenarios, but its plan.json
    had no 'roadmap_row' in any job and the round's log no proof line, so the Ofis strip said
    "staging'de kanıtlı %0". The proof (test-round.ps1 Get-RoundProof) counts a passed card only
    under the JARVIS row its job names.

    The contract tested here:
      - scripts/testteam/schema/plan.json requires jobs[].roadmap_row, and its value is one of the
        rows of the table "### What JARVIS does" in docs/ROADMAP.md (the rows the Ofis strip
        counts: app.team.progress parse_jarvis, a NEVER row not counted): the row's whole first
        cell, or its bold title;
      - Read-TestTeamPlan refuses a plan with a job without it, or with a title that is not a
        row, and its Why names the valid titles;
      - test-round.ps1 refuses such a plan before any card or tester, and a plan naming a real
        row ends in a proof whose row is that row;
      - both test-lead.md copies ask for the field and their example plan is readable.

    The valid titles are read HERE from docs/ROADMAP.md with this file's own small parser (the
    shape of progress.py parse_jarvis), not from the library under test.

    No model, no network, no staging; a temporary folder; about a minute.

    Run: powershell -NoProfile -File scripts\tests\testteam-plan-rows.tests.ps1
#>
[CmdletBinding()]
param([string]$Filter = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\testteam\TestTeam.ps1")
. (Join-Path $repoRoot "scripts\testteam\TestTeamSchema.ps1")

$powershell = Join-Path $PSHOME "powershell.exe"
$testRound = Join-Path $repoRoot "scripts\testteam\test-round.ps1"

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

function Write-Utf8 {
    param([string]$Path, [string]$Text)
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

function New-Work {
    $path = Join-Path $env:TEMP ("pagentos-plan-rows-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
    [void](New-Item -ItemType Directory -Force -Path $path)
    return $path
}

function Get-JarvisRowsHere {
    <# The JARVIS table's counted rows, read the way app.team.progress parse_jarvis reads them:
       the table under "### What JARVIS does", header and separator skipped, the first cell with
       '**' dropped and spaces collapsed; a row whose last cell's first bold word starts NEVER is
       not counted. Full = the whole cell; Title = its bold part when the cell starts bold. #>
    $text = [System.IO.File]::ReadAllText((Join-Path $repoRoot "docs\ROADMAP.md"), [System.Text.Encoding]::UTF8)
    $lines = $text -split "`r?`n"
    $at = -1
    for ($i = 0; $i -lt $lines.Count; $i++) { if ($lines[$i].StartsWith("### What JARVIS does")) { $at = $i; break } }
    if ($at -lt 0) { throw "docs/ROADMAP.md: '### What JARVIS does' yok" }
    $table = New-Object System.Collections.ArrayList
    for ($i = $at + 1; $i -lt $lines.Count; $i++) {
        if ($lines[$i].StartsWith("#")) { break }
        if ($lines[$i].TrimStart().StartsWith("|")) { [void]$table.Add($lines[$i]) }
    }
    $rows = New-Object System.Collections.ArrayList
    foreach ($line in @($table.ToArray() | Select-Object -Skip 2)) {
        $cells = @($line.Trim().Trim('|').Split('|') | ForEach-Object { $_.Trim() })
        if ($cells.Count -lt 2) { continue }
        $last = [regex]::Match($cells[$cells.Count - 1], '\*\*(.+?)\*\*')
        if ($last.Success -and $last.Groups[1].Value.Trim().ToUpperInvariant().StartsWith("NEVER")) { continue }
        $full = (($cells[0] -replace '\*\*', '') -split '\s+' | Where-Object { $_ }) -join ' '
        $bold = [regex]::Match($cells[0], '^\*\*(.+?)\*\*')
        $title = if ($bold.Success) { (($bold.Groups[1].Value -split '\s+' | Where-Object { $_ }) -join ' ') } else { $full }
        [void]$rows.Add([pscustomobject]@{ Full = $full; Title = $title })
    }
    return @($rows.ToArray())
}

function Read-PlanText {
    param([string]$Work, [string]$Text)
    $file = Join-Path $Work ("plan-" + [guid]::NewGuid().ToString("N").Substring(0, 8) + ".json")
    Write-Utf8 -Path $file -Text $Text
    return (Read-TestTeamPlan -Path $file)
}

function ConvertTo-PlanJson {
    param([object[]]$Jobs)
    return (ConvertTo-Json -InputObject ([ordered]@{ jobs = @($Jobs) }) -Depth 6)
}

$rows = @(Get-JarvisRowsHere)
$work = New-Work

Write-Host "şema: jobs[].roadmap_row"

Test-Case "ROADMAP.md'den JARVIS satırları okunur (bu dosyanın kendi okuyucusu)" {
    Assert-True -Condition ($rows.Count -ge 10) -Because "yalnız $($rows.Count) satır okundu: ayrıştırıcı yarım"
}

Test-Case "roadmap_row'suz iş reddedilir; Missing jobs[0].roadmap_row, Why geçerli başlıkları sayar" {
    $read = Read-PlanText -Work $work -Text '{ "jobs": [ { "family": "nobet", "scenario": "scripts/testteam/scenarios/watches.json", "improvise": true } ] }'
    Assert-Equal -Expected $false -Actual $read.Readable -Because "satırsız plan okunabilir sayıldı: $($read.Why)"
    Assert-True -Condition (@($read.Missing) -contains "jobs[0].roadmap_row") -Because "Missing: $(@($read.Missing) -join ', ')"
    foreach ($row in $rows) { Assert-True -Condition ($read.Why.Contains($row.Title)) -Because "Why geçerli başlığı adlandırmıyor: '$($row.Title)'`n          Why: $($read.Why)" }
}

Test-Case "ikinci iş satırsızsa yalnız o adlandırılır" {
    $jobs = @([ordered]@{ family = "a"; improvise = $true; roadmap_row = $rows[0].Title }, [ordered]@{ family = "b"; improvise = $true })
    $read = Read-PlanText -Work $work -Text (ConvertTo-PlanJson -Jobs $jobs)
    Assert-Equal -Expected $false -Actual $read.Readable -Because $read.Why
    Assert-Equal -Expected "jobs[1].roadmap_row" -Actual (@($read.Missing) -join ",") -Because "Missing"
}

Test-Case "her JARVIS satırı (kalın başlığı ve tam hücresi) geçer; değer kalın başlıkla okunur" {
    foreach ($row in $rows) {
        foreach ($form in @($row.Title, $row.Full) | Select-Object -Unique) {
            $read = Read-PlanText -Work $work -Text (ConvertTo-PlanJson -Jobs @([ordered]@{ family = "aile"; improvise = $true; roadmap_row = $form }))
            Assert-True -Condition $read.Readable -Because "gerçek satır reddedildi: '$form' ($($read.Why))"
            Assert-Equal -Expected $row.Title -Actual ([string]@($read.Value.jobs)[0].roadmap_row) -Because "okunan değer"
        }
    }
}

Test-Case "yanlış yazılmış satır reddedilir ve geçerli başlıklar sayılır" {
    foreach ($bad in @("Repairs and improve itself", "Repairs and improves itself!", "Proactive", "nobet", "How it is built from here (the team cycle)")) {
        $read = Read-PlanText -Work $work -Text (ConvertTo-PlanJson -Jobs @([ordered]@{ family = "aile"; improvise = $true; roadmap_row = $bad }))
        Assert-Equal -Expected $false -Actual $read.Readable -Because "'$bad' satır sayıldı"
        Assert-True -Condition (@($read.Missing) -contains "jobs[0].roadmap_row") -Because "'$bad': Missing $(@($read.Missing) -join ', ')"
        Assert-True -Condition ($read.Why.Contains($bad)) -Because "Why yanlış değeri adlandırmıyor: $($read.Why)"
        foreach ($row in $rows) { Assert-True -Condition ($read.Why.Contains($row.Title)) -Because "'$bad': Why '$($row.Title)' başlığını saymıyor" }
    }
}

Write-Host ""
Write-Host "rol dosyaları"

foreach ($role in @(".claude/agents/test-lead.md", "scripts/testteam/roles/test-lead.md")) {
    Test-Case "$role roadmap_row'u ister ve örnek planı okunur" {
        $text = [System.IO.File]::ReadAllText((Join-Path $repoRoot $role.Replace('/', '\')), [System.Text.Encoding]::UTF8)
        Assert-True -Condition ($text.Contains("roadmap_row")) -Because "$role 'roadmap_row' demiyor"
        Assert-True -Condition ($text.Contains("What JARVIS does")) -Because "$role satırların tablosunu ('What JARVIS does') adlandırmıyor"
        $example = [regex]::Match($text, '(?s)```json\s*(.+?)```')
        Assert-True -Condition $example.Success -Because "$role örnek plan bloğu yok"
        $read = Read-PlanText -Work $work -Text $example.Groups[1].Value
        Assert-True -Condition $read.Readable -Because "$role örnek planı okunmuyor: $($read.Why)"
    }
}

Write-Host ""
Write-Host "test-round.ps1"

function New-PassingTester {
    # A stand-in for `claude -p`: every card passes on one staging sha.
    param([string]$Dir)
    $file = Join-Path $Dir "fake-tester.ps1"
    Write-Utf8 $file @'
$card = [Console]::In.ReadToEnd()
$path = [regex]::Match($card, '(?m)^- result_file: (.+)$').Groups[1].Value.Trim()
$id = [regex]::Match($card, '(?m)^- id: (.+)$').Groups[1].Value.Trim()
$family = [regex]::Match($card, '(?m)^- family: (.+)$').Groups[1].Value.Trim()
$seat = [regex]::Match($card, '(?m)^- tester: (.+)$').Groups[1].Value.Trim()
[IO.File]::WriteAllText((Join-Path $env:PAGENTOS_FAKE_TESTER_LOG "$id.call"), $family)
$doc = @{ card = $id; tester = $seat; family = $family; state = "passed"; scenario = ""; staging_sha = ("e" * 40); steps = @(@{ name = "adim"; expected = "200"; actual = "200"; ok = $true }); screenshot = "" }
[IO.File]::WriteAllText($path, ($doc | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))
Write-Output '{"type":"result","subtype":"success","is_error":false,"result":"rapor yazildi","total_cost_usd":0}'
'@
    return $file
}

function Invoke-Round {
    param([string]$Round, [string]$PlanText)
    $box = New-Work
    $team = Join-Path $box "team"
    [void](New-Item -ItemType Directory -Force -Path $team)
    Write-Utf8 (Join-Path $team "queue.json") '{"version":1,"tasks":[]}'
    Write-Utf8 (Join-Path $team "cycle-settings.json") '{"max_parallel":4,"test_parallel":4}'
    $plan = Join-Path $box "plan.json"
    Write-Utf8 $plan $PlanText
    $fake = New-PassingTester -Dir $box
    $calls = Join-Path $box "calls"
    [void](New-Item -ItemType Directory -Force -Path $calls)
    $env:PAGENTOS_FAKE_TESTER_LOG = $calls
    $outRoot = Join-Path $box "out"
    try { $out = & $powershell -NoProfile -File $testRound -NoAuth -Round $Round -TeamRoot $team -OutRoot $outRoot -PlanPath $plan -ClaudePath $powershell -ClaudePrefixArguments "-NoProfile,-File,$fake" -AssumeFreeGb 30 -AssumeGateRunning 0 -NoBoard 2>&1 }
    finally { Remove-Item Env:\PAGENTOS_FAKE_TESTER_LOG -ErrorAction SilentlyContinue }
    return [pscustomobject]@{
        Box = $box; Exit = $LASTEXITCODE; Out = (@($out) | ForEach-Object { [string]$_ }) -join "`n"
        Calls = @(Get-ChildItem -LiteralPath $calls -Filter "*.call" -File).Count
        Cards = (Join-Path $outRoot "$Round\cards.json"); Proof = (Join-Path $outRoot "$Round\proof.json")
    }
}

Test-Case "test-round.ps1 roadmap_row'suz planı reddeder: test çalışanı yok, kart yok, geçerli başlıklar çıktıda" {
    $run = Invoke-Round -Round "satirsiz" -PlanText '{"jobs":[{"family":"nobet","scenario":"scripts/testteam/scenarios/watches.json","improvise":true}]}'
    try {
        Assert-True -Condition ($run.Exit -ne 0) -Because "satırsız plan turu çalıştırdı (çıkış $($run.Exit)):`n$($run.Out)"
        Assert-Equal -Expected 0 -Actual $run.Calls -Because "test çalışanı başlatıldı"
        Assert-True -Condition (-not (Test-Path -LiteralPath $run.Cards)) -Because "kart dosyası yazıldı"
        Assert-True -Condition ($run.Out.Contains("roadmap_row")) -Because "çıktı alanı adlandırmıyor:`n$($run.Out)"
        Assert-True -Condition ($run.Out.Contains($rows[0].Title)) -Because "çıktı geçerli başlıkları saymıyor:`n$($run.Out)"
    }
    finally { Remove-Item -LiteralPath $run.Box -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "test-round.ps1 yanlış yazılmış satırı reddeder" {
    $run = Invoke-Round -Round "yanlis" -PlanText '{"jobs":[{"family":"nobet","improvise":true,"roadmap_row":"Repairs and improve itself"}]}'
    try {
        Assert-True -Condition ($run.Exit -ne 0) -Because "yanlış satırlı plan turu çalıştırdı (çıkış $($run.Exit)):`n$($run.Out)"
        Assert-Equal -Expected 0 -Actual $run.Calls -Because "test çalışanı başlatıldı"
        Assert-True -Condition ($run.Out.Contains("Repairs and improve itself")) -Because "çıktı yanlış değeri adlandırmıyor:`n$($run.Out)"
    }
    finally { Remove-Item -LiteralPath $run.Box -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "test-round.ps1 gerçek satırlı planı çalıştırır; kanıtın satırı o satır, geçen senaryo sayılır" {
    $row = $rows[0]
    $jobs = @([ordered]@{ family = "nobet"; improvise = $true; roadmap_row = $row.Full; why = "Stage 60" })
    $run = Invoke-Round -Round "satirli" -PlanText (ConvertTo-PlanJson -Jobs $jobs)
    try {
        Assert-Equal -Expected 0 -Actual $run.Exit -Because "tur bitmeli:`n$($run.Out)"
        Assert-Equal -Expected 1 -Actual $run.Calls -Because "bir iş, bir test çalışanı"
        Assert-True -Condition (Test-Path -LiteralPath $run.Proof) -Because "kanıt yazılmadı:`n$($run.Out)"
        $proof = Get-Content -Raw -Encoding UTF8 -LiteralPath $run.Proof | ConvertFrom-Json
        Assert-Equal -Expected 1 -Actual @($proof.rows).Count -Because "bir satır"
        Assert-Equal -Expected $row.Title -Actual ([string]@($proof.rows)[0].row) -Because "kanıtın satırı planın satırı"
        Assert-Equal -Expected 1 -Actual ([int]@($proof.rows)[0].passed) -Because "geçen senaryo sayılır"
        Assert-Equal -Expected ("e" * 40) -Actual ([string]$proof.staging_sha) -Because "staging sha"
    }
    finally { Remove-Item -LiteralPath $run.Box -Recurse -Force -ErrorAction SilentlyContinue }
}

Test-Case "test-round.ps1 kalın başlıklı satırın tam hücresini alır; kanıtın satırı kalın başlık" {
    $bold = @($rows | Where-Object { $_.Title -ne $_.Full })
    Assert-True -Condition ($bold.Count -gt 0) -Because "ROADMAP.md'de kalın başlıklı JARVIS satırı yok"
    $row = $bold[0]
    $jobs = @([ordered]@{ family = "kalin"; improvise = $true; roadmap_row = $row.Full })
    $run = Invoke-Round -Round "kalin" -PlanText (ConvertTo-PlanJson -Jobs $jobs)
    try {
        Assert-Equal -Expected 0 -Actual $run.Exit -Because "tur bitmeli:`n$($run.Out)"
        Assert-True -Condition (Test-Path -LiteralPath $run.Proof) -Because "kanıt yazılmadı:`n$($run.Out)"
        $proof = Get-Content -Raw -Encoding UTF8 -LiteralPath $run.Proof | ConvertFrom-Json
        Assert-Equal -Expected $row.Title -Actual ([string]@($proof.rows)[0].row) -Because "kanıtın satırı kalın başlık (kanonik biçim), tam hücre değil"
        Assert-Equal -Expected 1 -Actual ([int]@($proof.rows)[0].passed) -Because "geçen senaryo sayılır"
    }
    finally { Remove-Item -LiteralPath $run.Box -Recurse -Force -ErrorAction SilentlyContinue }
}

Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ""
Write-Host ("testteam-plan-rows: {0} geçti, {1} kaldı" -f $script:Passes, $script:Failures)
if ($script:Failures -gt 0) { exit 1 }
exit 0
