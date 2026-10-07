<#
.SYNOPSIS
    The test team's dry round (team/plans/testteam-dry-round-schema-adr.md): the result and plan
    files have ONE contract (scripts/testteam/schema/result.json, plan.json), one reader that
    reads by it and never throws (scripts/testteam/TestTeamSchema.ps1), and every half-written
    file the schema allows goes through the reader and the test team's unchanged report
    functions without killing them.

.DESCRIPTION
    2026-10-06: three patches (3d430def, 75ceba26, fc62979c) for one class of defect - a field the
    role file let the model leave out killed the script that read the file; two real rounds died.
    This suite closes the class, not the three instances:
      - the half documents are GENERATED from the schema (a field added to the schema later gets
        its half documents with no new test line);
      - the three shapes of 6 October are FIXED fixtures, word for word, run beside the generated
        family (the two sides must not come from one source);
      - a small 'old reader' (plain ConvertFrom-Json + direct property access under StrictMode,
        the shape of the readers before 6 October) must throw on each fixture: the proof that the
        fixtures are of the killing kind;
      - the contract halves read each other: the field list of run-scenario.ps1's help block and
        the role files that must name the schema are read here, not copied.

    No model, no network, no staging; a temporary folder; seconds.

    Run: powershell -NoProfile -File scripts\tests\testteam-dry-round.tests.ps1
#>
[CmdletBinding()]
param([string]$Filter = "")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\TeamQueue.ps1")
. (Join-Path $repoRoot "scripts\testteam\TestTeam.ps1")
$schemaLibrary = Join-Path $repoRoot "scripts\testteam\TestTeamSchema.ps1"
$script:LibraryError = ""
try { . $schemaLibrary } catch { $script:LibraryError = "TestTeamSchema.ps1 yüklenemedi: $($_.Exception.Message)" }

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

function Assert-SameSet {
    param([AllowEmptyCollection()][object[]]$Expected = @(), [AllowEmptyCollection()][object[]]$Actual = @(), [string]$Because)
    $e = (@($Expected) | ForEach-Object { [string]$_ } | Sort-Object) -join ", "
    $a = (@($Actual) | ForEach-Object { [string]$_ } | Sort-Object) -join ", "
    if ($e -ne $a) { throw "$Because`n          expected: [$e]`n          actual  : [$a]" }
}

function Write-Utf8 {
    param([string]$Path, [string]$Text)
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-PathValue {
    <# A value at a dotted path of the reader's (steps[0].notes); throws when a step is not there. #>
    param($Object, [string]$Path)
    $current = $Object
    foreach ($part in $Path.Split('.')) {
        $name = $part; $index = -1
        if ($part -match '^(.+)\[(\d+)\]$') { $name = $Matches[1]; $index = [int]$Matches[2] }
        $property = $current.PSObject.Properties[$name]
        if ($null -eq $property) { throw "'$Path': '$name' yok" }
        $current = $property.Value
        if ($index -ge 0) { $current = @($current)[$index] }
    }
    return $current
}

function Assert-Library {
    if ($script:LibraryError) { throw $script:LibraryError }
    foreach ($name in @("Read-TestTeamSchema", "ConvertTo-TestTeamShape", "Read-TestTeamResult", "Read-TestTeamPlan", "New-TestTeamHalfDocuments", "Test-TestTeamRoleNamesSchema")) {
        if (-not (Get-Command -Name $name -CommandType Function -ErrorAction SilentlyContinue)) { throw "işlev yok: $name (scripts/testteam/TestTeamSchema.ps1)" }
    }
}

$work = Join-Path $env:TEMP ("pagentos-testteam-dry-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
[void](New-Item -ItemType Directory -Force -Path $work)

$schemaPaths = @{
    result = Join-Path $repoRoot "scripts\testteam\schema\result.json"
    plan   = Join-Path $repoRoot "scripts\testteam\schema\plan.json"
}

# ============================================================== the fixtures of 6 October
# Word for word, NOT generated from the schema. Each one killed a real round (or its report).

# 3d430def: a family with no scenario file yet - 'improvise' and no 'scenario'. Each job carries
# the JARVIS row it tests since test-plan-names-roadmap-rows (2026-10-07: the plan is refused
# without it); the killing shape - no 'scenario' - is unchanged.
$Fixture3d430def = @'
{
  "jobs": [
    { "family": "dil-dayanikliligi", "improvise": true, "roadmap_row": "Always-listening natural conversation, interruptible, in the owner's language", "why": "her turda bir dil dayanıklılığı işi (sahip, 2026-10-06)" },
    { "family": "nobet", "scenario": "scripts/testteam/scenarios/watches.json", "improvise": true, "roadmap_row": "Proactive: warns, briefs, watches over him", "why": "Stage 54 nöbet motorunu çıkardı" }
  ]
}
'@

# 75ceba26: a result a tester wrote by hand - state, steps, breaking; no tester, no card.
$Fixture75ceba26 = @'
{
  "family": "ev-stoku",
  "scenario": "team/testteam/r1/ev-stoku-dogaclama.json",
  "state": "broke",
  "staging_sha": "b1f8ef94c028b2476ba368b462ffa5a91c4277fc",
  "steps": [
    { "name": "stok ekle", "method": "POST", "path": "/v1/household/items", "expected": "201", "actual": "201", "ok": true, "ms": 84 }
  ],
  "breaking": {
    "what": "eşzamanlı stok ekleme",
    "tried": [ { "load": 1, "ok": 1, "errors": 0, "p95_ms": 90 }, { "load": 8, "ok": 5, "errors": 3, "p95_ms": 2100 } ],
    "first_failure": { "load": 8, "ok": 5, "errors": 3, "p95_ms": 2100 }
  },
  "notes": "elle yazıldı: senaryo dosyası bu turda yazıldı"
}
'@

# fc62979c: a ladder rung with no p95_ms.
$Fixturefc62979c = @'
{
  "card": "tj-r2-3",
  "tester": "tester-3",
  "family": "alarm",
  "scenario": "team/testteam/r2/alarm-dogaclama.json",
  "state": "broke",
  "steps": [],
  "breaking": {
    "what": "aynı dakikaya eşzamanlı alarm kurma",
    "tried": [ { "load": 4, "ok": 4, "errors": 0 }, { "load": 16, "ok": 11, "errors": 5 } ],
    "first_failure": { "load": 16, "ok": 11, "errors": 5 }
  }
}
'@

# Not of 6 October, but the inspector's finding of 2026-10-07: a hand-written result measures in
# decimals ("p95_ms": 1234.7, "ms": "812.5"); an int field that refused them turned a real
# measurement into '?' / 0 and named it "missing".
$FixtureDecimal = @'
{
  "card": "tj-r2-5",
  "tester": "tester-1",
  "family": "alisveris-listesi",
  "state": "broke",
  "steps": [ { "name": "liste ekle", "ok": true, "ms": 812.5 }, { "name": "liste oku", "ok": true, "ms": "40.2" } ],
  "breaking": {
    "what": "eşzamanlı öğe ekleme",
    "tried": [ { "load": 2, "ok": 2, "errors": 0, "p95_ms": 310.4 }, { "load": 32, "ok": 20, "errors": 12, "p95_ms": 1234.7 } ],
    "first_failure": { "load": 32, "ok": 20, "errors": 12, "p95_ms": "1234.7" }
  }
}
'@

# Not of 6 October, but as fixed: a hand-written result with no 'state' - says nothing.
$FixtureNoState = @'
{
  "card": "tj-r2-4",
  "tester": "tester-4",
  "family": "saglik",
  "steps": [ { "name": "sağlık", "method": "GET", "path": "/v1/system/health", "expected": "200", "actual": "500", "ok": false, "ms": 12 } ]
}
'@

# The readers before 6 October: plain ConvertFrom-Json, then direct property access under StrictMode.
$OldResultReader = {
    param([string]$Text)
    Set-StrictMode -Version Latest
    $r = ConvertFrom-Json -InputObject $Text
    $who = "{0} ({1}, {2})" -f $r.family, $r.tester, $r.card
    $state = [string]$r.state
    if ($null -ne $r.breaking) {
        foreach ($rung in @($r.breaking.tried)) { $null = "{0}:{1}/{2} p95 {3}" -f $rung.load, $rung.ok, $rung.errors, $rung.p95_ms }
    }
    return "$who $state"
}
$OldPlanReader = {
    param([string]$Text)
    Set-StrictMode -Version Latest
    $p = ConvertFrom-Json -InputObject $Text
    foreach ($job in @($p.jobs)) { $null = "{0} {1}" -f $job.family, $job.scenario }
    return "ok"
}

# ======================================================================= negative control

Write-Host ""
Write-Host "negatif kontrol: 6 Ekim fikstürleri eski okuyucuyu öldürür"

$negative = @(
    @{ Name = "3d430def"; Text = $Fixture3d430def; Reader = $OldPlanReader; Property = "scenario" }
    @{ Name = "75ceba26"; Text = $Fixture75ceba26; Reader = $OldResultReader; Property = "tester" }
    @{ Name = "fc62979c"; Text = $Fixturefc62979c; Reader = $OldResultReader; Property = "p95_ms" }
)
foreach ($row in $negative) {
    Test-Case "eski okuyucu $($row.Name) fikstüründe '$($row.Property)' yüzünden düşer" {
        $died = ""
        try { [void](& $row.Reader $row.Text) } catch { $died = $_.Exception.Message }
        Assert-True -Condition ([bool]$died) -Because "fikstür $($row.Name) eski okuyucuyu artık öldürmüyor: zararsızlaştı, öldüren biçimi taşımıyor"
        Write-Host "        eski okuyucu: $died"
        Assert-True -Condition ($died -match ("'" + [regex]::Escape($row.Property) + "'")) -Because "fikstür $($row.Name) '$($row.Property)' yüzünden değil başka nedenle düştü: $died"
    }
}

# ======================================================================= schema files

Write-Host ""
Write-Host "şema dosyaları"

function Get-SchemaSpecs {
    <# Every field spec of a schema with its dotted path (arrays as [0]). #>
    param($Fields, [string]$Prefix = "")
    foreach ($property in $Fields.PSObject.Properties) {
        $path = if ($Prefix) { "$Prefix.$($property.Name)" } else { $property.Name }
        [pscustomobject]@{ Path = $path; Spec = $property.Value }
        $spec = $property.Value
        $type = [string](Get-TeamProperty -InputObject $spec -Name "type" -Default "")
        if ($type -eq "object") { Get-SchemaSpecs -Fields $spec.fields -Prefix $path }
        if ($type -eq "array") { Get-SchemaSpecs -Fields $spec.items.fields -Prefix "$path[0]" }
    }
}

foreach ($kind in @("result", "plan")) {
    Test-Case "şema $kind.json yüklenir; her alanın required/type/default'u, her enum'un values'u var" {
        Assert-Library
        $schema = Read-TestTeamSchema -Path $schemaPaths[$kind]
        Assert-Equal -Expected 1 -Actual ([int]$schema.version) -Because "şema sürümü"
        $specs = @(Get-SchemaSpecs -Fields $schema.fields)
        Assert-True -Condition ($specs.Count -gt 0) -Because "şemada alan yok"
        foreach ($s in $specs) {
            foreach ($key in @("required", "type", "default")) {
                Assert-True -Condition ($null -ne $s.Spec.PSObject.Properties[$key]) -Because "$kind.json '$($s.Path)': '$key' yok"
            }
            Assert-True -Condition ($s.Spec.required -is [bool]) -Because "$kind.json '$($s.Path)': required bool değil"
            Assert-True -Condition (@("string", "int", "bool", "enum", "array", "object") -contains [string]$s.Spec.type) -Because "$kind.json '$($s.Path)': tip '$($s.Spec.type)'"
            if ($s.Spec.type -eq "enum") { Assert-True -Condition (@($s.Spec.values).Count -gt 0) -Because "$kind.json '$($s.Path)': enum values boş" }
        }
    }
}

Test-Case "zorunlu alanlar: result yalnız 'state'; plan 'jobs', her işin 'family'si ve 'roadmap_row'u" {
    Assert-Library
    $result = Read-TestTeamSchema -Path $schemaPaths.result
    $plan = Read-TestTeamSchema -Path $schemaPaths.plan
    Assert-SameSet -Expected @("state") -Actual @(Get-SchemaSpecs -Fields $result.fields | Where-Object { $_.Spec.required } | ForEach-Object { $_.Path }) -Because "result.json zorunluları"
    Assert-SameSet -Expected @("jobs", "jobs[0].family", "jobs[0].roadmap_row") -Actual @(Get-SchemaSpecs -Fields $plan.fields | Where-Object { $_.Spec.required } | ForEach-Object { $_.Path }) -Because "plan.json zorunluları"
}

Test-Case "bozuk şema dosyası okunamazsa throw: o bizim dosyamız" {
    Assert-Library
    $bad = Join-Path $work "bozuk-sema.json"
    Write-Utf8 -Path $bad -Text '{ "version": 1, "fields": { "state": { "type": "enum", "required": true, "default": null } } }'
    $threw = $false
    try { [void](Read-TestTeamSchema -Path $bad) } catch { $threw = $true }
    Assert-True -Condition $threw -Because "values'u olmayan enum kabul edildi"
}

# ===================================================== contract: run-scenario.ps1's help

function Get-HelpResultFields {
    <# The fields run-scenario.ps1's help names for its result file, as dotted paths, and the
       value lists written as a|b|c. Reads the other half of the contract; edits nothing. #>
    param([string]$Text)
    $at = $Text.IndexOf("The result file is ")
    if ($at -lt 0) { throw "run-scenario.ps1 yardımında 'The result file is' yok" }
    $rest = $Text.Substring($at)
    $end = [regex]::Match($rest, "\r?\n\s*\r?\n")
    if ($end.Success) { $rest = $rest.Substring(0, $end.Index) }
    $rest = $rest.Substring($rest.IndexOf("{"))
    $tokens = @([regex]::Matches($rest, '[A-Za-z_][A-Za-z0-9_]*(\|[A-Za-z_][A-Za-z0-9_]*)*|[{}\[\]:,]') | ForEach-Object { $_.Value })
    $script:helpAt = 0
    $out = New-Object System.Collections.ArrayList
    function Read-HelpObject([string]$Prefix) {
        if ($tokens[$script:helpAt] -ne "{") { throw "yardım ayrıştırılamadı: '{' beklendi, '$($tokens[$script:helpAt])'" }
        $script:helpAt++
        while ($tokens[$script:helpAt] -ne "}") {
            if ($tokens[$script:helpAt] -eq ",") { $script:helpAt++; continue }
            $name = $tokens[$script:helpAt]; $script:helpAt++
            $path = if ($Prefix) { "$Prefix.$name" } else { $name }
            $values = @()
            if ($tokens[$script:helpAt] -eq ":") {
                $script:helpAt++
                $next = $tokens[$script:helpAt]
                if ($next -eq "{") { [void]$out.Add([pscustomobject]@{ Path = $path; Values = @() }); Read-HelpObject -Prefix $path; continue }
                if ($next -eq "[") {
                    $script:helpAt++
                    [void]$out.Add([pscustomobject]@{ Path = $path; Values = @() })
                    Read-HelpObject -Prefix "$path[0]"
                    if ($tokens[$script:helpAt] -ne "]") { throw "yardım ayrıştırılamadı: ']' beklendi" }
                    $script:helpAt++
                    continue
                }
                $values = @($next.Split('|')); $script:helpAt++
            }
            [void]$out.Add([pscustomobject]@{ Path = $path; Values = $values })
        }
        $script:helpAt++
    }
    Read-HelpObject -Prefix ""
    return @($out.ToArray())
}

Test-Case "sözleşme yarıları birbirini okur: run-scenario.ps1 yardımının adlandırdığı her sonuç alanı result.json şemasında" {
    Assert-Library
    $help = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\testteam\run-scenario.ps1"), [System.Text.Encoding]::UTF8)
    $named = @(Get-HelpResultFields -Text $help)
    Assert-True -Condition ($named.Count -ge 20) -Because "yardımdan yalnız $($named.Count) alan okundu: ayrıştırıcı yarım"
    $schema = Read-TestTeamSchema -Path $schemaPaths.result
    $specs = @{}
    foreach ($s in @(Get-SchemaSpecs -Fields $schema.fields)) { $specs[$s.Path] = $s.Spec }
    $absent = @($named | Where-Object { -not $specs.ContainsKey($_.Path) } | ForEach-Object { $_.Path })
    Assert-True -Condition ($absent.Count -eq 0) -Because ("run-scenario.ps1 yardımının adlandırdığı alan(lar) result.json şemasında yok: " + ($absent -join ", "))
    foreach ($n in @($named | Where-Object { @($_.Values).Count -gt 1 })) {
        Assert-Equal -Expected "enum" -Actual ([string]$specs[$n.Path].type) -Because "'$($n.Path)' yardımda bir değer listesi ($(@($n.Values) -join '|'))"
        Assert-SameSet -Expected @($n.Values) -Actual @($specs[$n.Path].values) -Because "'$($n.Path)' değerleri yardımla şemada aynı değil"
    }
}

# ============================================================================ role files

Write-Host ""
Write-Host "rol dosyaları"

# role file -> the schema it must name. 2/2 adds the tester.md -> result.json row.
$roleRows = @(
    @{ Role = ".claude/agents/test-lead.md"; Schema = "scripts/testteam/schema/plan.json" }
    @{ Role = "scripts/testteam/roles/test-lead.md"; Schema = "scripts/testteam/schema/plan.json" }
)
foreach ($row in $roleRows) {
    Test-Case "rol dosyası $($row.Role) şemayı adlandırır: $($row.Schema)" {
        Assert-Library
        $file = Join-Path $repoRoot ($row.Role.Replace('/', '\'))
        Assert-True -Condition (Test-TestTeamRoleNamesSchema -RoleFile $file -SchemaPath $row.Schema) -Because "$($row.Role) '$($row.Schema)' adını taşımıyor: model planın biçimini şemadan değil örnekten öğrenir"
    }
}

Test-Case "iki test-lead.md kopyası bayt bayt aynı (kurulu kopya = kaynağı)" {
    $a = [System.IO.File]::ReadAllBytes((Join-Path $repoRoot ".claude\agents\test-lead.md"))
    $b = [System.IO.File]::ReadAllBytes((Join-Path $repoRoot "scripts\testteam\roles\test-lead.md"))
    Assert-Equal -Expected $a.Length -Actual $b.Length -Because ".claude/agents/test-lead.md ile scripts/testteam/roles/test-lead.md boyu ayrı"
    for ($i = 0; $i -lt $a.Length; $i++) { if ($a[$i] -ne $b[$i]) { throw "iki test-lead.md kopyası $i. baytta ayrışıyor" } }
}

Test-Case "senaryosuz iş kuralı: rol dosyasının cümlesi ile New-TestTeamCards aynı şeyi söyler" {
    Assert-Library
    $role = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\testteam\roles\test-lead.md"), [System.Text.Encoding]::UTF8)
    Assert-True -Condition ($role.Contains('("improvise": true)')) -Because "test-lead.md dosyası olmayan aileye '(""improvise"": true)' demiyor"
    $file = Join-Path $work "senaryosuz.plan.json"
    Write-Utf8 -Path $file -Text '{ "jobs": [ { "family": "yeni-aile", "roadmap_row": "Repairs and improves itself" } ] }'
    $plan = Read-TestTeamPlan -Path $file
    Assert-True -Condition $plan.Readable -Because "aile adı ve satırı olan bir plan okunur ($($plan.Why))"
    $threw = ""
    try { [void](New-TestTeamCards -Round "kuru" -Jobs @($plan.Value.jobs)) } catch { $threw = $_.Exception.Message }
    Assert-True -Condition ($threw -match 'improvise: true ister') -Because "senaryosuz, improvise'sız iş kart oldu: '$threw'"
}

# ================================================================= the fixed fixtures

Write-Host ""
Write-Host "6 Ekim'in üç biçimi yeni okuyucudan geçer"

$jobCards = @(New-TestTeamCards -Round "kuru" -Jobs @(
        [pscustomobject]@{ family = "ev-stoku"; scenario = ""; improvise = $true }
        [pscustomobject]@{ family = "alarm"; scenario = ""; improvise = $true }
        [pscustomobject]@{ family = "saglik"; scenario = "scripts/testteam/scenarios/health.json" }
    ))

Test-Case "3d430def: senaryosuz doğaçlama iş okunur ve improvise true, scenario '' ile kart olur" {
    Assert-Library
    $file = Join-Path $work "3d430def.plan.json"; Write-Utf8 -Path $file -Text $Fixture3d430def
    $plan = Read-TestTeamPlan -Path $file
    Assert-True -Condition $plan.Readable -Because "plan okunamadı: $($plan.Why)"
    Assert-True -Condition (@($plan.Defaulted) -contains "jobs[0].scenario") -Because "Defaulted 'jobs[0].scenario' adlandırmıyor: $(@($plan.Defaulted) -join ', ')"
    $cards = @(New-TestTeamCards -Round "kuru" -Jobs @($plan.Value.jobs))
    Assert-Equal -Expected 2 -Actual $cards.Count -Because "iki iş iki kart"
    Assert-Equal -Expected $true -Actual $cards[0].improvise -Because "ilk kart doğaçlama"
    Assert-Equal -Expected "" -Actual $cards[0].scenario -Because "ilk kartın senaryosu boş"
    Assert-Equal -Expected "dil-dayanikliligi" -Actual $cards[0].family -Because "ilk kartın ailesi"
}

Test-Case "75ceba26: tester/card'sız el yazısı sonuç -Card ile okunur, kırılma raporuna girer" {
    Assert-Library
    $file = Join-Path $work "75ceba26.result.json"; Write-Utf8 -Path $file -Text $Fixture75ceba26
    $read = Read-TestTeamResult -Path $file -Card $jobCards[0]
    Assert-True -Condition $read.Readable -Because "sonuç okunamadı: $($read.Why)"
    foreach ($name in @("tester", "card")) { Assert-True -Condition (@($read.Defaulted) -contains $name) -Because "Defaulted '$name' adlandırmıyor: $(@($read.Defaulted) -join ', ')" }
    Assert-Equal -Expected $jobCards[0].tester -Actual $read.Value.tester -Because "tester iş kartından"
    Assert-Equal -Expected $jobCards[0].id -Actual $read.Value.card -Because "card iş kartının id'si"
    Assert-Equal -Expected "elle yazıldı: senaryo dosyası bu turda yazıldı" -Actual $read.Value.notes -Because "şemada olmayan 'notes' korunur"
    $report = Format-TestTeamBreakingReport -Round "kuru" -Results @($read.Value)
    Assert-True -Condition ($report.Markdown -match ("KIRILDI ev-stoku \({0}, {1}\)" -f [regex]::Escape($jobCards[0].tester), [regex]::Escape($jobCards[0].id))) -Because "kırılma raporu tester ve kartı adlandırmıyor:`n$($report.Markdown)"
}

Test-Case "fc62979c: p95_ms'siz basamak okunur, p95 '?' ile doldurulur ve Defaulted'da adlanır" {
    Assert-Library
    $file = Join-Path $work "fc62979c.result.json"; Write-Utf8 -Path $file -Text $Fixturefc62979c
    $read = Read-TestTeamResult -Path $file
    Assert-True -Condition $read.Readable -Because "sonuç okunamadı: $($read.Why)"
    Assert-Equal -Expected 0 -Actual @($read.Missing).Count -Because "eksik zorunlu alan yok"
    foreach ($path in @("breaking.tried[0].p95_ms", "breaking.tried[1].p95_ms", "breaking.first_failure.p95_ms")) {
        Assert-True -Condition (@($read.Defaulted) -contains $path) -Because "Defaulted '$path' adlandırmıyor: $(@($read.Defaulted) -join ', ')"
        Assert-Equal -Expected "?" -Actual (Get-PathValue -Object $read.Value -Path $path) -Because "$path varsayılanı"
    }
    Assert-Equal -Expected 16 -Actual (Get-PathValue -Object $read.Value -Path "breaking.first_failure.load") -Because "yük korunur"
    $report = Format-TestTeamBreakingReport -Round "kuru" -Results @($read.Value)
    Assert-True -Condition ($report.Markdown -match "ilk kırılan yük 16, 5 hata / 16 istek, istek başına p95 \? ms") -Because "kırılma satırı:`n$($report.Markdown)"
}

Test-Case "ondalık ölçüm: p95_ms/ms ondalık sayı ya da metin yuvarlanarak okunur, varsayılana düşmez" {
    Assert-Library
    $file = Join-Path $work "ondalik.result.json"; Write-Utf8 -Path $file -Text $FixtureDecimal
    $read = Read-TestTeamResult -Path $file
    Assert-True -Condition $read.Readable -Because "sonuç okunamadı: $($read.Why)"
    $expected = [ordered]@{ "steps[0].ms" = 813; "steps[1].ms" = 40; "breaking.tried[0].p95_ms" = 310; "breaking.tried[1].p95_ms" = 1235; "breaking.first_failure.p95_ms" = 1235 }
    foreach ($path in @($expected.Keys)) {
        Assert-True -Condition (@($read.Defaulted) -notcontains $path) -Because "ölçülmüş '$path' varsayılanla dolan sayıldı: $(@($read.Defaulted) -join ', ')"
        Assert-Equal -Expected ([string]$expected[$path]) -Actual ([string](Get-PathValue -Object $read.Value -Path $path)) -Because "$path yuvarlanır"
    }
    $report = Format-TestTeamBreakingReport -Round "kuru" -Results @($read.Value)
    Assert-True -Condition ($report.Markdown -match "ilk kırılan yük 32, 12 hata / 32 istek, istek başına p95 1235 ms") -Because "kırılma satırı ölçümü taşımıyor:`n$($report.Markdown)"
}

Test-Case "state'siz sabit fikstür okunamadı kaydı döner: Missing = state, Why adını söyler, throw yok" {
    Assert-Library
    $file = Join-Path $work "durumsuz.result.json"; Write-Utf8 -Path $file -Text $FixtureNoState
    $read = Read-TestTeamResult -Path $file -Card $jobCards[2]
    Assert-Equal -Expected $false -Actual $read.Readable -Because "state'siz sonuç okunabilir sayıldı (fazla hoşgörülü okuyucu): $($read.Why)"
    Assert-SameSet -Expected @("state") -Actual @($read.Missing) -Because "Missing"
    Assert-True -Condition ($read.Why -match "state") -Because "Why eksik alanı adlandırmıyor: $($read.Why)"
    Assert-Equal -Expected $file -Actual $read.Path -Because "kayıt dosyanın yolunu taşır"
}

# ===================================================================== the dry round

Write-Host ""
Write-Host "kuru tur: şemanın izin verdiği en yarım belgeler"

$script:DryResults = New-Object System.Collections.ArrayList
$script:Generated = @{}
foreach ($kind in @("result", "plan")) {
    $docs = @()
    try {
        Assert-Library
        $docs = @(New-TestTeamHalfDocuments -Schema (Read-TestTeamSchema -Path $schemaPaths[$kind]) -Kind $kind)
    }
    catch {
        $why = "$($script:LibraryError) $($_.Exception.Message)"
        Test-Case "kuru tur $kind`: yarım belgeler üretilir" { throw $why }
        continue
    }
    $script:Generated[$kind] = $docs.Count
    Write-Host "  ($kind`: $($docs.Count) yarım belge)"
    Test-Case "kuru tur $kind`: aile her sınıftan belge taşır" {
        $names = @($docs | ForEach-Object { $_.Name })
        $classes = @("zorunlu-yalniz", "iskelet", "zorunlu-eksik:", "metin:", "json-degil", "bos", "fazladan", "tam")
        if (@(Get-SchemaSpecs -Fields (Read-TestTeamSchema -Path $schemaPaths[$kind]).fields | Where-Object { $_.Spec.type -eq "enum" }).Count -gt 0) { $classes += "enum-disi:" }
        foreach ($prefix in $classes) {
            Assert-True -Condition (@($names | Where-Object { $_.StartsWith($prefix) }).Count -gt 0) -Because "'$prefix' sınıfından belge yok"
        }
    }
    $values = @{}
    foreach ($doc in $docs) {
        Test-Case "kuru tur $kind`: $($doc.Name)" {
            $file = Join-Path $work ("{0}-{1}.json" -f $kind, ([guid]::NewGuid().ToString("N").Substring(0, 8)))
            $text = if ($null -ne $doc.Text) { [string]$doc.Text } else { ConvertTo-Json -InputObject $doc.Document -Depth 30 }
            Write-Utf8 -Path $file -Text $text
            $read = if ($kind -eq "result") { Read-TestTeamResult -Path $file } else { Read-TestTeamPlan -Path $file }
            Assert-Equal -Expected $doc.Expect.Readable -Actual $read.Readable -Because "Readable ($($read.Why))"
            Assert-SameSet -Expected @($doc.Expect.Missing) -Actual @($read.Missing) -Because "Missing"
            Assert-SameSet -Expected @($doc.Expect.Defaulted) -Actual @($read.Defaulted) -Because "Defaulted"
            Assert-Equal -Expected ($read.Readable) -Actual (@($read.Missing).Count -eq 0) -Because "Readable = Missing boş"
            Assert-True -Condition ([bool]$read.Why) -Because "Why boş"
            if (-not $read.Readable) {
                foreach ($m in @($read.Missing)) { Assert-True -Condition ($read.Why.Contains($m)) -Because "okunamadı kaydının Why'ı '$m' adlandırmıyor: $($read.Why)" }
            }
            foreach ($kept in @($doc.Expect.Kept)) {
                Assert-True -Condition ($null -ne (Get-PathValue -Object $read.Value -Path $kept)) -Because "şemada olmayan '$kept' korunmadı"
            }
            $values[$doc.Name] = $read
            if ($doc.Expect.SameValueAs) {
                $same = $values[$doc.Expect.SameValueAs]
                Assert-True -Condition ($null -ne $same) -Because "'$($doc.Expect.SameValueAs)' önce okunur"
                Assert-Equal -Expected (ConvertTo-Json -InputObject $same.Value -Depth 30 -Compress) -Actual (ConvertTo-Json -InputObject $read.Value -Depth 30 -Compress) -Because "metin olarak yazılan sayı/bool tipine çevrilmedi"
            }
            if ($kind -eq "result" -and $read.Readable) {
                $withCard = Read-TestTeamResult -Path $file -Card $jobCards[0]
                Assert-SameSet -Expected @($read.Defaulted) -Actual @($withCard.Defaulted) -Because "-Card Defaulted'ı değiştirmez (yalnız değerleri doldurur)"
                [void]$script:DryResults.Add([pscustomobject]@{ Name = $doc.Name; Value = $withCard.Value })
            }
            if ($kind -eq "plan" -and $read.Readable) {
                $jobs = @($read.Value.jobs)
                $needsImprovise = @($jobs | Where-Object { -not [string]$_.scenario -and -not [bool]$_.improvise }).Count -gt 0
                $threw = ""
                $cards = @()
                try { $cards = @(New-TestTeamCards -Round "kuru" -Jobs $jobs) } catch { $threw = $_.Exception.Message }
                if ($needsImprovise) { Assert-True -Condition ($threw -match 'improvise: true ister') -Because "senaryosuz improvise'sız iş: kural yerine '$threw'" }
                else {
                    Assert-Equal -Expected "" -Actual $threw -Because "okunabilen plan kart üretmeli"
                    Assert-Equal -Expected $jobs.Count -Actual $cards.Count -Because "iş başına bir kart"
                }
            }
        }
    }
}

Test-Case "kuru tur: okunabilen her sonuç TestTeam.ps1'in DEĞİŞMEYEN rapor işlevlerinden geçer" {
    Assert-True -Condition ($script:DryResults.Count -gt 0) -Because "okunabilen sonuç yok"
    $all = @($script:DryResults | ForEach-Object { $_.Value })
    foreach ($fixture in @($Fixture75ceba26, $Fixturefc62979c)) {
        $file = Join-Path $work ("fikstur-" + [guid]::NewGuid().ToString("N").Substring(0, 8) + ".json"); Write-Utf8 -Path $file -Text $fixture
        $all += (Read-TestTeamResult -Path $file -Card $jobCards[1]).Value
    }
    foreach ($entry in $script:DryResults) {
        try {
            [void](Get-TestTeamFailures -Result $entry.Value)
            $card = [pscustomobject]@{ id = "tj-kuru-1"; family = [string]$entry.Value.family; state = [string]$entry.Value.state }
            [void](Format-TestTeamSeatNote -Card $card -Result $entry.Value)
        }
        catch { throw "'$($entry.Name)' rapor işlevini öldürdü: $($_.Exception.Message)" }
    }
    $report = Format-TestTeamBreakingReport -Round "kuru" -Results $all
    foreach ($value in $all) {
        if ($null -eq $value.breaking) { continue }
        Assert-True -Condition ($report.Markdown.Contains([string]$value.family + " (")) -Because "kırılma raporu '$($value.family)' ailesini adlandırmıyor"
    }
}

Test-Case "okuyucu belge ne olursa olsun throw etmez" {
    Assert-Library
    $schema = Read-TestTeamSchema -Path $schemaPaths.result
    $odd = @($null, 42, "metin", @(), @(1, 2), @{ state = "passed" }, [pscustomobject]@{ state = [pscustomobject]@{ a = 1 } },
        [pscustomobject]@{ state = "failed"; steps = "adım değil"; breaking = @(1, 2) },
        [pscustomobject]@{ state = "broke"; breaking = [pscustomobject]@{ tried = @($null, "x", 3, [pscustomobject]@{ load = @(1) }) } })
    foreach ($doc in $odd) {
        try { $shape = ConvertTo-TestTeamShape -Document $doc -Schema $schema }
        catch { throw "ConvertTo-TestTeamShape throw etti ($(ConvertTo-Json -InputObject $doc -Compress -Depth 5)): $($_.Exception.Message)" }
        Assert-Equal -Expected ($shape.Readable) -Actual (@($shape.Missing).Count -eq 0) -Because "Readable = Missing boş"
    }
    $absent = Read-TestTeamResult -Path (Join-Path $work "hic-yok.result.json")
    Assert-Equal -Expected $false -Actual $absent.Readable -Because "olmayan dosya"
    Assert-True -Condition ($absent.Why.StartsWith("dosya yok")) -Because "Why: $($absent.Why)"
}

Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
$sizes = ($script:Generated.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($_.Name) $($_.Value)" }) -join ", "
Write-Host "yarım belge ailesi: $sizes"
Write-Host "testteam-dry-round: $script:Passes passed, $script:Failures failed"
if ($script:Failures -gt 0) { exit 1 }
exit 0
