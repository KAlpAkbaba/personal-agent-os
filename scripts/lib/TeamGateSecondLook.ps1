<#
.SYNOPSIS
    The second look at a red gate: which tests the gate's log names, how each is re-run, what
    the counts make of it, and which earlier file polluted it (team/plans/gate-second-look-adr.md).

.DESCRIPTION
    A red gate says "red"; this says WHAT KIND of red, with numbers:

      gercek        fails every time it is run
      kararsiz      passes sometimes (main_de_de: it fails on main's tip too - not this branch's fault)
      siraya_bagli  passes alone every time, failed in the suite (a test before it leaves a trace)
      yarim         the budget ran out, or the counts are not there: NO class

    None of the four turns the gate green; there is no green value here on purpose. The gate's
    red verdict is the gate's (scripts/team/integrate.ps1); this only explains it.

    Get-GateRedClass is pure (numbers in, a class out). Find-GatePolluter takes its runner as a
    scriptblock, so its tests use fakes and the script (scripts/team/gate-second-look.ps1) the
    real processes.
#>

Set-StrictMode -Version Latest

# The runs per kind (alone, in the file's order, on main's tip).
$script:GateSecondLookRuns = 5
# A test that passed at least this many of the runs (and failed at least once) is flaky.
$script:FlakyMinPasses = 1

# The gate's steps that are not one .tests.ps1 file: the step's name -> the package.
$script:GateStepPackages = @{
    "API unit tests"                                              = "api-unit"
    "API integration tests"                                       = "api-integration"
    "Recovery supervisor tests"                                   = "supervisor"
    "Browser agent lint + tests"                                  = "browser"
    "Web shell lint, unit tests and types (oxlint, vitest, tsc)" = "web"
}

# THE re-run table: every package's commands in one place. {id} {dosya} {ad} {dosyalar} are
# filled in by Get-GateRerunCommand; 'tek' = the test alone, 'dosya' = its file in its own
# order, 'sira' = the suite's collection order (pytest only), 'bolme' = earlier files + the test.
$script:GateRerunTable = @{
    "api-unit"        = @{ exe = "uv"; cwd = "services\api"; db = $false; kinds = @("heavy"); pytest = $true
        tek = @("run", "pytest", "-q", "-p", "no:cacheprovider", "-rfEp", "{id}")
        dosya = @("run", "pytest", "-q", "-p", "no:cacheprovider", "-rfEp", "{dosya}")
        sira = @("run", "pytest", "--collect-only", "-q", "tests/unit")
        bolme = @("run", "pytest", "-q", "-p", "no:cacheprovider", "-rfEp", "{dosyalar}", "{id}") }
    "api-integration" = @{ exe = "uv"; cwd = "services\api"; db = $true; kinds = @("database", "heavy"); pytest = $true
        tek = @("run", "pytest", "-q", "-p", "no:cacheprovider", "-rfEp", "-m", "integration", "{id}")
        dosya = @("run", "pytest", "-q", "-p", "no:cacheprovider", "-rfEp", "-m", "integration", "{dosya}")
        sira = @("run", "pytest", "--collect-only", "-q", "-m", "integration", "tests/integration")
        bolme = @("run", "pytest", "-q", "-p", "no:cacheprovider", "-rfEp", "-m", "integration", "{dosyalar}", "{id}") }
    "supervisor"      = @{ exe = "uv"; cwd = "services\recovery-supervisor"; db = $false; kinds = @("heavy"); pytest = $true
        tek = @("run", "pytest", "-q", "-p", "no:cacheprovider", "-rfEp", "{id}")
        dosya = @("run", "pytest", "-q", "-p", "no:cacheprovider", "-rfEp", "{dosya}")
        sira = @("run", "pytest", "--collect-only", "-q")
        bolme = @("run", "pytest", "-q", "-p", "no:cacheprovider", "-rfEp", "{dosyalar}", "{id}") }
    "browser"         = @{ exe = "uv"; cwd = "services\browser"; db = $false; kinds = @("heavy"); pytest = $true
        tek = @("run", "pytest", "-q", "-p", "no:cacheprovider", "-rfEp", "{id}")
        dosya = @("run", "pytest", "-q", "-p", "no:cacheprovider", "-rfEp", "{dosya}")
        sira = @("run", "pytest", "--collect-only", "-q")
        bolme = @("run", "pytest", "-q", "-p", "no:cacheprovider", "-rfEp", "{dosyalar}", "{id}") }
    "web"             = @{ exe = "pnpm"; cwd = "apps\web"; db = $false; kinds = @("heavy"); pytest = $false
        tek = @("exec", "vitest", "run", "{dosya}", "-t", "{ad}", "--reporter=verbose")
        dosya = @("exec", "vitest", "run", "{dosya}", "--reporter=verbose")
        sira = $null; bolme = $null }
    "ps-suite"        = @{ exe = "powershell"; cwd = ""; db = $false; kinds = @("heavy"); pytest = $false
        tek = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "{dosya}", "-Filter", "{ad}")
        dosya = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "{dosya}")
        sira = $null; bolme = $null }
}

$script:GateUnrecognised = "tan" + [char]0x0131 + "nmad" + [char]0x0131
$script:GateNotFound = "bulunamad" + [char]0x0131

function Get-GateStepSuiteMap {
    <# The gate script's PowerShell steps: step name -> "scripts/tests/<x>.tests.ps1", read from
       the script itself (a hand-typed copy would go stale the day a step is added). A step that
       runs several files through a variable has no single file and is left out. #>
    param([Parameter(Mandatory = $true)][string]$GateScript)
    $map = @{}
    $current = ""
    foreach ($line in @(Get-Content -LiteralPath $GateScript -Encoding UTF8)) {
        if ($line -match '^\s*Invoke-Step\s+"([^"]+)"') { $current = $Matches[1]; continue }
        if ($current -and -not $map.ContainsKey($current) -and $line -match 'scripts\\tests\\([\w.-]+\.tests\.ps1)') {
            $map[$current] = "scripts/tests/" + $Matches[1]
        }
    }
    return $map
}

function Get-GateLogSections {
    <# The log's steps in order: name -> lines, and which steps went red. The same section rule
       as Read-TeamGateLog (scripts/lib/TeamIntegrate.ps1): '^=== (.+) ===\s*$', the BOM trimmed,
       a step is red when its section holds the gate's "FAILED: " line or the summary says FAIL. #>
    param([string]$Text)
    $lines = @(([string]$Text).TrimStart([char]0xFEFF) -split "`r?`n")
    $order = New-Object System.Collections.ArrayList
    $sections = @{}
    $red = New-Object System.Collections.ArrayList
    $current = ""
    $inSummary = $false
    foreach ($line in $lines) {
        if ($line -match '^=== (.+) ===\s*$') {
            $current = $Matches[1].Trim()
            $inSummary = ($current -eq "Quality gate summary")
            if (-not $inSummary -and -not $sections.ContainsKey($current)) {
                $sections[$current] = New-Object System.Collections.ArrayList
                [void]$order.Add($current)
            }
            continue
        }
        if ($inSummary) {
            if ($line -match '^(.+?)\s+FAIL\s+[\d.,]+') {
                $name = $Matches[1].Trim()
                if ($red -notcontains $name) { [void]$red.Add($name) }
            }
            continue
        }
        if ($current) {
            [void]$sections[$current].Add($line)
            if ($line -match '^FAILED: ' -and $red -notcontains $current) { [void]$red.Add($current) }
        }
    }
    $steps = @($order | Where-Object { $red -contains $_ })
    foreach ($name in $red) { if ($steps -notcontains $name) { $steps += $name } }
    return [pscustomobject]@{ Sections = $sections; RedSteps = @($steps) }
}

function Get-GateTestIdsFromOutput {
    <# The failed tests a package's output names: {id, dosya, ad}. pytest: "FAILED|ERROR <file>::<test>"
       ([param] cut: one test, all its parameters); vitest: "FAIL  <file> > ... > <name>";
       the PowerShell suites: "  FAIL  <case>" (this repository's runner) and "[-] <case>" (Pester). #>
    param([string[]]$Lines, [string]$Paket)
    $found = New-Object System.Collections.ArrayList
    $seen = @{}
    foreach ($raw in @($Lines)) {
        $line = ([string]$raw).TrimEnd()
        $rec = $null
        if ($Paket -in @("api-unit", "api-integration", "supervisor", "browser")) {
            if ($line -match '^\s*(?:FAILED|ERROR)\s+([^\s:]+)::([^\s\[]+)') {
                $file = $Matches[1] -replace '\\', '/'
                $rec = @{ id = "$file::$($Matches[2])"; dosya = $file; ad = $Matches[2] }
            }
        }
        elseif ($Paket -eq "web") {
            if ($line -match '^\s*FAIL\s+(\S+?\.(?:test|spec)\.[cm]?[jt]sx?)(?:\s+>\s+(.+))?$') {
                $file = $Matches[1]
                $rest = if ($Matches[2]) { $Matches[2].Trim() } else { "" }
                $name = if ($rest) { @($rest -split '\s+>\s+')[-1] } else { "" }
                $id = if ($rest) { "$file > $rest" } else { $file }
                $rec = @{ id = $id; dosya = $file; ad = $name }
            }
        }
        elseif ($Paket -eq "ps-suite") {
            if ($line -match '^\s*FAIL\s{2,}(\S.*)$') { $rec = @{ id = $Matches[1].Trim(); dosya = ""; ad = $Matches[1].Trim() } }
            elseif ($line -match '^\s*\[-\]\s+(.+?)(?:\s+\d+(?:\.\d+)?m?s)?$') { $rec = @{ id = $Matches[1].Trim(); dosya = ""; ad = $Matches[1].Trim() } }
        }
        if ($null -ne $rec -and -not $seen.ContainsKey($rec.id)) {
            $seen[$rec.id] = $true
            [void]$found.Add([pscustomobject]$rec)
        }
    }
    return @($found.ToArray())
}

function Get-GateFailedTests {
    <# The failed tests of a red gate log, in the gate's order: {id, paket, adim, dosya, ad}.
       A red step it cannot read (no known package, or no test named in it) is ONE record with
       id '' and paket 'tanınmadı' - and no class is ever given to it. #>
    param(
        [Parameter(Mandatory = $true)][string]$LogPath,
        # The gate script whose steps name the .tests.ps1 files (default: this repository's).
        [string]$GateScript = ""
    )
    if (-not $GateScript) { $GateScript = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "scripts\quality-gate.ps1" }
    $suites = if (Test-Path -LiteralPath $GateScript) { Get-GateStepSuiteMap -GateScript $GateScript } else { @{} }
    $log = Get-GateLogSections -Text ([System.IO.File]::ReadAllText($LogPath))
    $out = New-Object System.Collections.ArrayList
    foreach ($step in @($log.RedSteps)) {
        $paket = ""
        $file = ""
        if ($script:GateStepPackages.ContainsKey($step)) { $paket = $script:GateStepPackages[$step] }
        elseif ($suites.ContainsKey($step)) { $paket = "ps-suite"; $file = $suites[$step] }
        $lines = if ($log.Sections.ContainsKey($step)) { @($log.Sections[$step].ToArray()) } else { @() }
        $tests = if ($paket) { @(Get-GateTestIdsFromOutput -Lines $lines -Paket $paket) } else { @() }
        if (@($tests).Count -eq 0) {
            [void]$out.Add([pscustomobject]@{ id = ""; paket = $script:GateUnrecognised; adim = $step; dosya = ""; ad = "" })
            continue
        }
        foreach ($t in $tests) {
            $f = if ($file) { $file } else { [string]$t.dosya }
            [void]$out.Add([pscustomobject]@{ id = [string]$t.id; paket = $paket; adim = $step; dosya = $f; ad = [string]$t.ad })
        }
    }
    return @($out.ToArray())
}

function Test-GateSuiteHasFilter {
    <# A PowerShell suite can run one case only when it takes -Filter. #>
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    return ([System.IO.File]::ReadAllText($Path) -match '\[string\]\s*\$Filter\b')
}

function Resolve-GateTool {
    param([string]$Name)
    switch ($Name) {
        "powershell" { return (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") }
        "pnpm" {
            foreach ($p in @((Join-Path ([string]$env:APPDATA) "npm\pnpm.cmd"), (Join-Path ([string]$env:LOCALAPPDATA) "pnpm\pnpm.exe"))) {
                if (Test-Path -LiteralPath $p) { return $p }
            }
        }
    }
    $c = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $c) { return [string]$c.Source }
    return $Name
}

function Get-GateRerunCommand {
    <# One command from the re-run table: {exe, args, cwd, db, kinds, env}. env holds what the
       child's environment must differ by: PAGENTOS_TEST_SHARD is ALWAYS removed ($null) -
       services/api/tests/conftest.py splits the suite by it, and a test that is not selected
       looks green. $null when the package has no such run (a suite without -Filter alone). #>
    param(
        [Parameter(Mandatory = $true)]$Test,
        [Parameter(Mandatory = $true)][ValidateSet("tek", "dosya", "sira", "bolme")][string]$Kind,
        [Parameter(Mandatory = $true)][string]$Root,
        [string[]]$Files = @()
    )
    $paket = [string]$Test.paket
    if (-not $script:GateRerunTable.ContainsKey($paket)) { return $null }
    $row = $script:GateRerunTable[$paket]
    $template = $row[$Kind]
    if ($null -eq $template) { return $null }
    $dosya = [string]$Test.dosya
    $ad = [string]$Test.ad
    if ($paket -eq "ps-suite") {
        $dosya = Join-Path $Root ($dosya -replace '/', '\')
        if ($Kind -eq "tek") {
            if (-not (Test-GateSuiteHasFilter -Path $dosya)) { return $null }
            $ad = "^" + [regex]::Escape([string]$Test.id) + "$"
        }
    }
    elseif ($paket -eq "web" -and $Kind -eq "tek") {
        if (-not $ad) { return $null }
        $ad = "^" + [regex]::Escape($ad) + "$"
    }
    $argv = New-Object System.Collections.ArrayList
    foreach ($a in @($template)) {
        switch ($a) {
            "{id}" { [void]$argv.Add([string]$Test.id) }
            "{dosya}" { [void]$argv.Add($dosya) }
            "{ad}" { [void]$argv.Add($ad) }
            "{dosyalar}" { foreach ($f in @($Files)) { [void]$argv.Add([string]$f) } }
            default { [void]$argv.Add([string]$a) }
        }
    }
    $cwd = if ($row.cwd) { Join-Path $Root $row.cwd } else { $Root }
    return [pscustomobject]@{
        exe = (Resolve-GateTool -Name $row.exe); args = @($argv.ToArray()); cwd = $cwd; db = [bool]$row.db
        kinds = @($row.kinds); pytest = [bool]$row.pytest; env = @{ PAGENTOS_TEST_SHARD = $null }
    }
}

function Get-GateRunOutcome {
    <# One run's word on one test: 'gecti', 'dustu' or 'hata' (no word: did not run, timed out,
       could not be read). Passing needs positive evidence - a test that was never selected is
       not a pass, and neither is "4 passed, 1 skipped" with the target the skipped one: the
       target's OWN pass line is required (pytest -rfEp "PASSED <id>[param]", vitest's verbose
       tick line, the suites' PASS / [+] line). #>
    param([Parameter(Mandatory = $true)]$Test, [Parameter(Mandatory = $true)]$Run)
    if ([bool]$Run.TimedOut) { return "hata" }
    $lines = @(([string]$Run.Output -replace ([string][char]0x1b + '\[[0-9;]*m'), '') -split "`r?`n")
    $failed = @(Get-GateTestIdsFromOutput -Lines $lines -Paket ([string]$Test.paket) | ForEach-Object { $_.id })
    $id = [string]$Test.id
    if ($failed -contains $id) { return "dustu" }
    $code = [int]$Run.ExitCode
    if ($code -notin @(0, 1)) { return "hata" }
    $pat = switch ([string]$Test.paket) {
        "ps-suite" { '^\s*(?:PASS\s{2,}|\[\+\]\s+)' + [regex]::Escape($id) + '(?:\s+\d+(?:\.\d+)?m?s)?\s*$' }
        "web" { '^\s*[' + [char]0x2713 + [char]0x221A + ']\s+(?:\|[^|]+\|\s+)?' + [regex]::Escape($id) + '(?:\s+\d+(?:\.\d+)?m?s)?\s*$' }
        default { '^\s*PASSED\s+' + [regex]::Escape($id) + '(?:\[.*\])?(?:\s|$)' }
    }
    if (@($lines | Where-Object { $_ -match $pat }).Count -gt 0) { return "gecti" }
    return "hata"
}

function Get-GateRedClass {
    <#
    .SYNOPSIS
        PURE: the counts of a red test's re-runs -> its class. Starts nothing.
    .DESCRIPTION
        -Alone / -InFile / -OnMain: passes out of -Runs ($null = not known: not run, or not
        every run gave a word). Returns {sinif, yer, main_de_de, sebep}; sinif is one of
        gercek | kararsiz | siraya_bagli | yarim, and nothing else - there is no green.
    #>
    param(
        [Nullable[int]]$Alone = $null,
        [Nullable[int]]$InFile = $null,
        [Nullable[int]]$OnMain = $null,
        [bool]$FailedInSuite = $true,
        [bool]$OverBudget = $false,
        [int]$Runs = $script:GateSecondLookRuns
    )
    $mainToo = ($null -ne $OnMain -and [int]$OnMain -lt $Runs)
    function New-Class { param([string]$Sinif, [string]$Yer, [string]$Sebep) [pscustomobject]@{ sinif = $Sinif; yer = $Yer; main_de_de = $mainToo; sebep = $Sebep } }
    if ($OverBudget) { return (New-Class "yarim" "" "bütçe aşıldı") }
    if (-not $FailedInSuite) { return (New-Class "yarim" "" "takımda düştüğü bilinmiyor") }
    $known = @(@($Alone, $InFile) | Where-Object { $null -ne $_ })
    if (@($known).Count -eq 0) { return (New-Class "yarim" "" "sayı yok") }
    foreach ($n in $known) {
        if ([int]$n -ge $script:FlakyMinPasses -and [int]$n -lt $Runs) { return (New-Class "kararsiz" "" "bazen geçer") }
    }
    if ($null -ne $Alone -and [int]$Alone -eq $Runs) {
        if ($null -ne $InFile -and [int]$InFile -eq 0) { return (New-Class "siraya_bagli" "ayni_dosya" "tek başına geçer, kendi dosyasında düşer") }
        return (New-Class "siraya_bagli" "onceki_dosyalar" "tek başına ve dosyasında geçer, takımda düşer")
    }
    if (@($known | Where-Object { [int]$_ -ne 0 }).Count -eq 0) { return (New-Class "gercek" "" "her seferinde düşer") }
    # Some runs all passed, others all failed (0/5 alone but 5/5 in its file; or no alone run and
    # 5/5 in its file while the gate saw it fail): it does pass sometimes.
    return (New-Class "kararsiz" "" "bazen geçer (koşu türüne göre)")
}

function Find-GatePolluter {
    <#
    .SYNOPSIS
        The earlier file that makes -Target fail, by halves.
    .DESCRIPTION
        -Invoke { param($Files, $Target) } runs the files then the target and returns $true when
        the target FAILED, $false when it passed, $null when the run said nothing. The first run
        is every candidate (no failure: 'bulunamadı'); then the half that still fails is kept -
        16 files: 1 + 4 runs. Returns {durum (bulundu|bulunamadı|yarim), kirleten, adimlar,
        aralik (candidates still open), adaylar}.
    #>
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$Candidates,
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)][scriptblock]$Invoke,
        [Nullable[datetime]]$Deadline = $null
    )
    $range = @($Candidates)
    $steps = 0
    function New-Result { param([string]$Durum, [string]$Name) [pscustomobject]@{ durum = $Durum; kirleten = $Name; adimlar = $steps; aralik = @($range).Count; adaylar = @($range) } }
    function Test-Late { return ($null -ne $Deadline -and [DateTime]::UtcNow -ge ([datetime]$Deadline).ToUniversalTime()) }
    if (@($range).Count -eq 0) { return (New-Result $script:GateNotFound "") }
    if (Test-Late) { return (New-Result "yarim" "") }
    $steps++
    $all = & $Invoke $range $Target
    if ($null -eq $all) { return (New-Result "yarim" "") }
    if (-not [bool]$all) { return (New-Result $script:GateNotFound "") }
    while (@($range).Count -gt 1) {
        if (Test-Late) { return (New-Result "yarim" "") }
        $half = [int][math]::Floor(@($range).Count / 2)
        $first = @($range[0..($half - 1)])
        $second = @($range[$half..(@($range).Count - 1)])
        $steps++
        $failed = & $Invoke $first $Target
        if ($null -eq $failed) { return (New-Result "yarim" "") }
        if ([bool]$failed) { $range = $first } else { $range = $second }
    }
    return (New-Result "bulundu" ([string]$range[0]))
}
