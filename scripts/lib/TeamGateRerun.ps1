<#
.SYNOPSIS
    The rerun of a red gate's failed steps (card gate-rerun-failed-steps, the owner 2026-10-06:
    "bu kapının daha hızlı kontrolünü sağlamanın bir yolu var mı, çok zaman kaybediyoruz";
    team/plans/gate-rerun-failed-steps-adr.md).

.DESCRIPTION
    After a red gate on sha A, a fix A..B whose files ALL belong to the red steps is judged by
    running only those steps (and the steps they need, and the cheap ones every run gets) with
    scripts/quality-gate.ps1 -OnlyStep - minutes, not the hour and a half of the full gate. The
    green record then says 'gate A + rerun B (steps ...)', and the release step accepts it only
    when B descends from A and both logs say what the record says (Test-TeamGateRerunChain).

    THE FULL GATE, whenever any of these holds:
      - the last attempt on the branch is not a red gate (green, cleared, a conflict, a lead run
        that failed) or it was the guards' red, not the gate's;
      - the last attempt was itself a partial rerun: never two in a row;
      - the red gate did not finish (no "QUALITY GATE: FAIL"), or named no step, or names a step
        the gate at B no longer has;
      - B does not descend from A;
      - a changed file is the gate's own (scripts/quality-gate.ps1, scripts/lib/Gate*.ps1), a
        lock or project file, a migration, or infrastructure ($script:TeamGateRerunFullPaths);
      - a changed file is in no family of the map, or in a family none of whose steps was red
        (code shared with steps that were green);
      - a document a test reads changed and no reader could be found for it, or a library reads it.
    A red with NO code change (B is A, or A..B changes nothing) reruns the same steps only when
    the red was the environment's (too many clients, Docker down, a junction node_modules);
    any other red on the same content is an answer already known: the full gate.

    Dot-sourced after TeamIntegrate.ps1 (Invoke-TeamGit, Test-TeamAncestor, Get-TeamCommittedFiles,
    Read-TeamGateLog, Get-TeamProperty, Read-TeamJson). Nothing here writes.
#>

# ---------------------------------------------------------------------------- the map

# Run in EVERY partial rerun: seconds each, and they read the whole tree.
$script:TeamGateRerunAlways = @("Required files", "Secret hygiene", "API lint (ruff)", "Other services lint (ruff)", "Script syntax (PowerShell 5.1)")

# The steps a step cannot run without, in a slice: the integration tests need the dev stack and
# the gate's own database, and the database is dropped by its own step.
$script:TeamGateRerunNeeds = @{
    "API integration tests" = @("Dev stack up (docker compose)", "Alembic upgrade head", "Gate database dropped")
}

# A change here is judged by the full gate, whatever was red.
$script:TeamGateRerunFullPaths = @(
    "scripts/quality-gate.ps1", "scripts/lib/Gate*.ps1", "scripts/lib/GateSteps.ps1",
    "**/uv.lock", "**/pnpm-lock.yaml", "pnpm-workspace.yaml", "**/package.json", "**/pyproject.toml",
    "**/*.csproj", "**/*.sln", "**/Directory.Build.*", "**/global.json", "**/packages.lock.json",
    "services/api/alembic/**", "services/api/alembic.ini", "**/migrations/**",
    "infra/**", ".env.example", "**/conftest.py",
    # Application code is shared with steps beyond the two that test it (the harnesses, the corpus):
    # the card's rule, "app code shared by other steps means the FULL gate".
    "services/api/app/**"
)

# path globs -> the steps that test them. A family whose steps include a red one is rerun whole.
$script:TeamGateRerunFamilies = @(
    [pscustomobject]@{ Name = "api-integration"; Paths = @("services/api/tests/integration/**"); Steps = @("API integration tests") }
    [pscustomobject]@{ Name = "api-unit"; Paths = @("services/api/tests/unit/**"); Steps = @("API unit tests") }
    [pscustomobject]@{ Name = "api-test-support"; Paths = @("services/api/tests/*.py"); Steps = @("API unit tests", "API integration tests") }
    [pscustomobject]@{ Name = "web"; Paths = @("apps/web/**"); Steps = @("Web shell build", "Web shell lint*") }
)

# The documents tests READ (an ADR number, the handoff block): not code, but not nobody's either.
# A change to one adds the steps whose tests name it (Get-TeamGateReaderSteps) instead of the full gate.
$script:TeamGateRerunDocuments = @("docs/HANDOFF.md", "docs/DECISIONS.md", "state/BUILD_STATE.json", "team/plans/**", "team/reports/**")

# What an environment's red says (the gate's failing lines): nothing in the code would change it.
$script:TeamGateEnvironmentMarks = @(
    'too many clients',
    'Cannot connect to the Docker daemon',
    'error during connect',
    'docker[^\r\n]*(is not running|not running|çalışmıyor|daemon is not)',
    'dockerDesktopLinuxEngine',
    'node_modules[^\r\n]*(junction|EPERM|ELOOP|EISDIR)',
    'could not find /tmp',
    'No space left on device',
    'There is not enough space on the disk',
    'connection to server at [^\r\n]* failed: Connection refused'
)

function Test-TeamGatePathGlob {
    <# Whether a repository-relative path (forward slashes) matches a glob: ** any depth, * one name. #>
    param([string]$Path, [string]$Glob)
    $p = (([string]$Path) -replace '\\', '/').TrimStart('/')
    $pattern = [regex]::Escape((([string]$Glob) -replace '\\', '/'))
    $pattern = $pattern -replace '\\\*\\\*/', '(?:.*/)?' -replace '\\\*\\\*', '.*' -replace '\\\*', '[^/]*' -replace '\\\?', '[^/]'
    return [regex]::IsMatch($p, '^' + $pattern + '$', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
}

function Test-TeamGatePathAny {
    param([string]$Path, [string[]]$Globs = @())
    foreach ($glob in @($Globs)) { if (Test-TeamGatePathGlob -Path $Path -Glob $glob) { return $true } }
    return $false
}

function Get-TeamGateStepNames {
    <# The steps a gate script declares (Invoke-Step "<name>"), in its order. #>
    param([string]$GateText)
    $names = New-Object System.Collections.ArrayList
    foreach ($m in [regex]::Matches([string]$GateText, '(?m)^\s*Invoke-Step\s+"([^"]+)"')) {
        $name = $m.Groups[1].Value
        if ($names -notcontains $name) { [void]$names.Add($name) }
    }
    return @($names.ToArray())
}

function Get-TeamGateSuiteFamilies {
    <#
        From the gate script itself: each step that runs scripts\tests\<x>.tests.ps1 is that
        suite's family - a change to the suite file is judged by its step. (A library the suite
        dot-sources is shared with other suites: scripts/lib is never in a family.)
    #>
    param([string]$GateText)
    $families = New-Object System.Collections.ArrayList
    $current = ""
    foreach ($line in @(([string]$GateText) -split "`r?`n")) {
        if ($line -match '^\s*Invoke-Step\s+"([^"]+)"') { $current = $Matches[1] }
        if (-not $current) { continue }
        foreach ($m in [regex]::Matches($line, 'tests[\\/]([A-Za-z0-9_.-]+\.tests\.ps1)')) {
            [void]$families.Add([pscustomobject]@{ Name = "suite:" + $m.Groups[1].Value; Paths = @("scripts/tests/" + $m.Groups[1].Value); Steps = @($current) })
        }
    }
    return @($families.ToArray())
}

function Resolve-TeamGateStepPattern {
    <# The gate's step names a -like pattern of the map names. #>
    param([string]$Pattern, [string[]]$StepNames = @())
    return @(@($StepNames) | Where-Object { $_ -like $Pattern })
}

function Test-TeamGateEnvironmentRed {
    <# Whether a red gate's failing text is an environment's (nothing in the code would change it). #>
    param([string]$FailureText)
    $text = [string]$FailureText
    if (-not $text.Trim()) { return $false }
    foreach ($mark in $script:TeamGateEnvironmentMarks) {
        if ([regex]::IsMatch($text, $mark, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)) { return $true }
    }
    return $false
}

function ConvertTo-TeamOnlyStepPattern {
    <#
        A step name as one -OnlyStep pattern: the list is split on commas, so a name with one is
        cut before it and ends in '*' ("Agent team automatic release*"); wildcard characters are escaped.
    #>
    param([string]$Name)
    $cut = [string]$Name
    $comma = $cut.IndexOf(',')
    $star = $false
    if ($comma -ge 0) { $cut = $cut.Substring(0, $comma); $star = $true }
    $escaped = [System.Management.Automation.WildcardPattern]::Escape($cut)
    if ($star) { $escaped += "*" }
    return $escaped
}

function Get-TeamGateRerunArguments {
    <# The gate's command-line arguments of a partial rerun: ONE comma-joined -OnlyStep (as -File delivers it). #>
    param([string[]]$Patterns = @())
    if (@($Patterns).Count -eq 0) { return @() }
    return @("-OnlyStep", (@($Patterns) -join ","))
}

# ---------------------------------------------------------------------------- the decision

function Get-TeamGateLastRed {
    <# The last attempt on the branch when it is the GATE's red (not the guards'), else $null. #>
    param([object[]]$Records = @())
    $all = @($Records)
    if (@($all).Count -eq 0) { return $null }
    $last = $all[@($all).Count - 1]
    if ([string](Get-TeamProperty -InputObject $last -Name "result" -Default "") -ne "red") { return $null }
    if ([string](Get-TeamProperty -InputObject $last -Name "by" -Default "") -eq "guards") { return $null }
    return $last
}

function Get-TeamGateRerunDecision {
    <#
    .SYNOPSIS
        Full gate or a partial rerun, from what is known (no git, no files): the records, the sha
        to gate, the files A..B changed, whether B descends from A, the gate script at B, the red
        gate's log, and for each changed document the steps whose tests read it.

    .OUTPUTS
        Mode "full" or "partial"; Steps (the gate's names, in its order) and Patterns (-OnlyStep)
        for a partial one; From / FromNumber / FromLog (the red gate); SameContent; Why (Turkish,
        for the report and the record).
    #>
    param(
        [object[]]$Records = @(),
        [string]$Sha = "",
        [string[]]$Changed = @(),
        [bool]$Descends = $false,
        [string]$GateText = "",
        [string]$LastLogText = "",
        # path -> the steps whose tests read it; a path mapped to $null has a reader no step owns.
        [hashtable]$Readers = @{}
    )
    $full = { param([string]$Why) [pscustomobject]@{ Mode = "full"; Steps = @(); Patterns = @(); From = ""; FromNumber = 0; FromLog = ""; SameContent = $false; Why = $Why } }
    $last = Get-TeamGateLastRed -Records $Records
    if ($null -eq $last) { return (& $full "son deneme kapının kırmızısı değil; tam kapı") }
    if ($null -ne (Get-TeamProperty -InputObject $last -Name "rerun_of" -Default $null)) {
        return (& $full "son deneme zaten kısmi bir yeniden koşuydu ve kırmızıydı; arka arkaya ikinci kısmi koşu yok, tam kapı")
    }
    $from = [string](Get-TeamProperty -InputObject $last -Name "sha" -Default "")
    $red = @(@(Get-TeamProperty -InputObject $last -Name "steps" -Default @()) | ForEach-Object { [string]$_ } | Where-Object { $_ })
    if (-not $from -or @($red).Count -eq 0) { return (& $full "kırmızı kapı hiçbir adım adlandırmadı; tam kapı") }
    if (-not ([string]$LastLogText -match '(?m)^QUALITY GATE: FAIL\s*$')) {
        return (& $full "kırmızı kapı sonuna kadar koşmadı (son sözü yok); tam kapı")
    }
    $names = @(Get-TeamGateStepNames -GateText $GateText)
    $missing = @($red | Where-Object { $names -notcontains $_ })
    if (@($missing).Count -gt 0) { return (& $full ("kırmızı adım kapıda yok: " + ($missing -join ", ") + "; tam kapı")) }

    $files = @(@($Changed) | ForEach-Object { ([string]$_ -replace '\\', '/').Trim() } | Where-Object { $_ })
    $same = ($Sha -eq $from) -or (@($files).Count -eq 0)
    $chosen = New-Object System.Collections.ArrayList
    foreach ($step in $red) { [void]$chosen.Add($step) }
    if ($same) {
        $gate = Read-TeamGateLog -Text $LastLogText -ExitCode 1
        if (-not (Test-TeamGateEnvironmentRed -FailureText ($gate.FailureText + "`n" + $gate.FirstFailure))) {
            return (& $full "kod değişmedi ve kırmızı ortamın değil; aynı içerik için cevap belli, tam kapı")
        }
    }
    else {
        if ($Sha -ne $from -and -not $Descends) { return (& $full "$Sha, kırmızı kapının $from commit'inden inmiyor; tam kapı") }
        $families = @($script:TeamGateRerunFamilies) + @(Get-TeamGateSuiteFamilies -GateText $GateText)
        foreach ($file in $files) {
            if (Test-TeamGatePathAny -Path $file -Globs $script:TeamGateRerunFullPaths) { return (& $full "$file her adımı ilgilendirir (kapı, kilit, göç ya da altyapı); tam kapı") }
            if (Test-TeamGatePathAny -Path $file -Globs $script:TeamGateRerunDocuments) {
                # Not $readers: PowerShell's names are case-blind, it would BE -Readers.
                $readSteps = $null
                if ($Readers.ContainsKey($file)) { $readSteps = $Readers[$file] }
                if ($null -eq $readSteps) { return (& $full "$file okuyan, adımı bilinmeyen bir test var; tam kapı") }
                foreach ($step in @($readSteps)) { if ($chosen -notcontains $step) { [void]$chosen.Add([string]$step) } }
                continue
            }
            $hit = @($families | Where-Object { Test-TeamGatePathAny -Path $file -Globs @($_.Paths) })
            if (@($hit).Count -eq 0) { return (& $full "$file adım haritasında yok; tam kapı") }
            foreach ($family in $hit) {
                $steps = @(@($family.Steps) | ForEach-Object { Resolve-TeamGateStepPattern -Pattern $_ -StepNames $names })
                if (@($steps | Where-Object { $red -contains $_ }).Count -eq 0) {
                    return (& $full ("$file yeşil adımları da ilgilendiriyor (" + ($steps -join ", ") + "); tam kapı"))
                }
                foreach ($step in $steps) { if ($chosen -notcontains $step) { [void]$chosen.Add($step) } }
            }
        }
    }
    # What the chosen steps need, then the cheap ones every run gets.
    $queue = @($chosen.ToArray())
    foreach ($step in $queue) {
        if ($script:TeamGateRerunNeeds.ContainsKey($step)) {
            foreach ($need in @($script:TeamGateRerunNeeds[$step])) { if ($chosen -notcontains $need) { [void]$chosen.Add($need) } }
        }
    }
    foreach ($step in $script:TeamGateRerunAlways) { if ($chosen -notcontains $step) { [void]$chosen.Add($step) } }
    $ordered = @($names | Where-Object { $chosen -contains $_ })
    $logName = Split-Path -Leaf ([string](Get-TeamProperty -InputObject $last -Name "log" -Default ""))
    $why = if ($same) { "ortam kırmızısı, kod değişmedi: aynı adımlar $Sha üzerinde yeniden" } else { "düzeltme yalnız kırmızı adımların alanında: kapı $from + yeniden koşu $Sha" }
    return [pscustomobject]@{
        Mode = "partial"; Steps = @($ordered); Patterns = @($ordered | ForEach-Object { ConvertTo-TeamOnlyStepPattern -Name $_ })
        From = $from; FromNumber = [int](Get-TeamProperty -InputObject $last -Name "n" -Default 0); FromLog = [string](Get-TeamProperty -InputObject $last -Name "log" -Default "")
        FromLogName = $logName; RedSteps = @($red); SameContent = $same; Why = ($why + " (adımlar: " + ($ordered -join "; ") + ")")
    }
}

function Get-TeamGateReaderSteps {
    <#
        The steps whose TESTS read a document: its name in services/api's tests or app code (the
        two API test steps), in a suite the gate runs (that suite's step) or in apps/web (the web
        steps). $null when a suite the gate does not run names it: who reads it is not known, and
        the answer is the full gate. A script library naming it (a list of protected files) is
        not a reader: the suites that run those libraries run them on sandbox copies.
    #>
    param([Parameter(Mandatory = $true)][string]$Tree, [Parameter(Mandatory = $true)][string]$Path, [string]$GateText = "")
    $forward = ([string]$Path -replace '\\', '/')
    $needles = @(Split-Path -Leaf ($forward -replace '/', '\'))
    if ($forward -like "team/*") {
        $folder = $forward -replace '/[^/]+$', ''
        $needles += @($folder, ($folder -replace '/', '\'), ($folder -replace '/', '\\'))
    }
    $names = @(Get-TeamGateStepNames -GateText $GateText)
    $suites = @{}
    foreach ($family in @(Get-TeamGateSuiteFamilies -GateText $GateText)) { $suites[[string]$family.Paths[0]] = [string]$family.Steps[0] }
    $steps = New-Object System.Collections.ArrayList
    $add = { param([string[]]$More) foreach ($s in @($More)) { if ($s -and $steps -notcontains $s) { [void]$steps.Add($s) } } }
    $files = New-Object System.Collections.ArrayList
    foreach ($root in @("services\api\tests", "services\api\app", "apps\web\src", "apps\web\tests")) {
        $folder = Join-Path $Tree $root
        if (Test-Path -LiteralPath $folder) { foreach ($f in @(Get-ChildItem -LiteralPath $folder -Recurse -File -Include *.py, *.ts, *.tsx -ErrorAction SilentlyContinue)) { [void]$files.Add($f) } }
    }
    $suiteFolder = Join-Path $Tree "scripts\tests"
    if (Test-Path -LiteralPath $suiteFolder) { foreach ($f in @(Get-ChildItem -LiteralPath $suiteFolder -File -Filter "*.tests.ps1")) { [void]$files.Add($f) } }
    foreach ($file in @($files.ToArray())) {
        if ($file.FullName -match '[\\/](node_modules|\.venv|__pycache__)[\\/]') { continue }
        $text = ""
        try { $text = [System.IO.File]::ReadAllText($file.FullName) } catch { continue }
        $hit = $false
        foreach ($needle in $needles) { if ($text.IndexOf($needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { $hit = $true; break } }
        if (-not $hit) { continue }
        $relative = $file.FullName.Substring($Tree.TrimEnd('\').Length + 1) -replace '\\', '/'
        if ($relative -like "services/api/*") { & $add @("API unit tests", "API integration tests") }
        elseif ($relative -like "apps/web/*") { & $add @(Resolve-TeamGateStepPattern -Pattern "Web shell*" -StepNames $names) }
        elseif ($suites.ContainsKey($relative)) { & $add @($suites[$relative]) }
        else { return [pscustomobject]@{ Known = $false; Steps = @(); Why = $relative } }
    }
    # An object, not the array: an empty list returned from a function arrives as $null ("unknown").
    return [pscustomobject]@{ Known = $true; Steps = @(@($steps.ToArray()) | Where-Object { $names -contains $_ }); Why = "" }
}

function Get-TeamGateRerunPlan {
    <#
    .SYNOPSIS
        Get-TeamGateRerunDecision with what it needs read: the red gate's log (beside its record
        in -Directory), A..-Sha's files and the ancestry from git, the readers of a changed
        document from -Tree. Any of these that cannot be read is the full gate.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$Directory,
        [object[]]$Records = @(),
        [Parameter(Mandatory = $true)][string]$Sha,
        [string]$GateText = "",
        [string]$Tree = ""
    )
    $last = Get-TeamGateLastRed -Records $Records
    if ($null -eq $last) { return (Get-TeamGateRerunDecision -Records $Records -Sha $Sha) }
    $from = [string](Get-TeamProperty -InputObject $last -Name "sha" -Default "")
    $logName = Split-Path -Leaf ([string](Get-TeamProperty -InputObject $last -Name "log" -Default ""))
    $logPath = if ($logName) { Join-Path $Directory $logName } else { "" }
    $logText = if ($logPath -and (Test-Path -LiteralPath $logPath)) { [System.IO.File]::ReadAllText($logPath, [System.Text.Encoding]::UTF8) } else { "" }
    $changed = @()
    $descends = $false
    $readers = @{}
    if ($from -and $from -ne $Sha) {
        $descends = Test-TeamAncestor -RepoRoot $RepoRoot -Ancestor $from -Of $Sha
        if ($descends) {
            $changed = @(Get-TeamCommittedFiles -Worktree $RepoRoot -From $from -To $Sha)
            $where = if ($Tree) { $Tree } else { $RepoRoot }
            foreach ($file in $changed) {
                $path = ([string]$file -replace '\\', '/')
                if (Test-TeamGatePathAny -Path $path -Globs $script:TeamGateRerunDocuments) {
                    $found = Get-TeamGateReaderSteps -Tree $where -Path $path -GateText $GateText
                    $readers[$path] = $null
                    if ($found.Known) { $readers[$path] = [string[]]@($found.Steps) }
                }
            }
        }
    }
    return (Get-TeamGateRerunDecision -Records $Records -Sha $Sha -Changed $changed -Descends $descends -GateText $GateText -LastLogText $logText -Readers $readers)
}

function Test-TeamGateEnvironmentRerunDue {
    <#
        Whether a branch that waits on "the gate was red on this very commit" is let through once:
        the red was the environment's, on this sha, and it was a full gate (never two reruns).
    #>
    param([object[]]$Records = @(), [string]$Sha = "", [string]$Directory = "")
    $last = Get-TeamGateLastRed -Records $Records
    if ($null -eq $last -or -not $Sha) { return $false }
    if ([string](Get-TeamProperty -InputObject $last -Name "sha" -Default "") -ne $Sha) { return $false }
    if ($null -ne (Get-TeamProperty -InputObject $last -Name "rerun_of" -Default $null)) { return $false }
    $logName = Split-Path -Leaf ([string](Get-TeamProperty -InputObject $last -Name "log" -Default ""))
    if (-not $logName -or -not $Directory) { return $false }
    $logPath = Join-Path $Directory $logName
    if (-not (Test-Path -LiteralPath $logPath)) { return $false }
    $text = [System.IO.File]::ReadAllText($logPath, [System.Text.Encoding]::UTF8)
    if (-not ($text -match '(?m)^QUALITY GATE: FAIL\s*$')) { return $false }
    $gate = Read-TeamGateLog -Text $text -ExitCode 1
    return (Test-TeamGateEnvironmentRed -FailureText ($gate.FailureText + "`n" + $gate.FirstFailure))
}

# ---------------------------------------------------------------------------- the slice's log, and the chain

function Test-TeamGateRerunLog {
    <#
    .SYNOPSIS
        Whether a partial rerun's log is green for EVERY step it was asked for: the gate green
        (Read-TeamGateLog), each step's own section in the log, its summary row PASS, and no
        "OnlyStep: no step matched" (a pattern that ran nothing is not a passed step).
    #>
    param([string]$Text, [int]$ExitCode = 0, [bool]$TimedOut = $false, [string[]]$Steps = @())
    $gate = Read-TeamGateLog -Text $Text -ExitCode $ExitCode -TimedOut $TimedOut
    if (-not $gate.Green) { return [pscustomobject]@{ Ok = $false; Why = [string]$gate.Why } }
    if (@($Steps).Count -eq 0) { return [pscustomobject]@{ Ok = $false; Why = "yeniden koşuda adım yok" } }
    if ([string]$Text -match '(?m)^OnlyStep: no step matched') { return [pscustomobject]@{ Ok = $false; Why = "yeniden koşuda bir adım deseni hiçbir adımı bulmadı" } }
    foreach ($step in @($Steps)) {
        $name = [regex]::Escape([string]$step)
        if (-not ([string]$Text -match ('(?m)^=== ' + $name + ' ===\s*$'))) { return [pscustomobject]@{ Ok = $false; Why = "yeniden koşuda $step koşmadı" } }
        if ([string]$Text -match ('(?m)^' + $name + '\s+(SKIPPED|FAIL)\b')) { return [pscustomobject]@{ Ok = $false; Why = "yeniden koşuda $step geçmedi" } }
    }
    return [pscustomobject]@{ Ok = $true; Why = "" }
}

function Test-TeamGateRerunChain {
    <#
    .SYNOPSIS
        The release step's check of a 'gate A + rerun B' green record (one with `rerun_of`): the
        red record it names is in the same folder, red, on `gate_sha`; every step that was red
        there was rerun; B (`sha`) descends from A; A's log finished and failed only in those
        steps; and B's log (`rerun_log`) is green for every rerun step. Ok, Why.
    #>
    param([Parameter(Mandatory = $true)][string]$RepoRoot, [Parameter(Mandatory = $true)][string]$Directory, [Parameter(Mandatory = $true)]$Record)
    $no = { param([string]$Why) [pscustomobject]@{ Ok = $false; Why = $Why } }
    $of = [int](Get-TeamProperty -InputObject $Record -Name "rerun_of" -Default 0)
    $a = [string](Get-TeamProperty -InputObject $Record -Name "gate_sha" -Default "")
    $b = [string](Get-TeamProperty -InputObject $Record -Name "sha" -Default "")
    $steps = @(@(Get-TeamProperty -InputObject $Record -Name "rerun_steps" -Default @()) | ForEach-Object { [string]$_ })
    if ($of -le 0 -or -not $a -or -not $b) { return (& $no "yeniden koşu kaydı eksik (rerun_of, gate_sha, sha)") }
    $redPath = Join-Path $Directory "gate-$of.json"
    if (-not (Test-Path -LiteralPath $redPath)) { return (& $no "yeniden koşunun dayandığı kapı kaydı yok (gate-$of.json)") }
    $red = Read-TeamJson -Path $redPath
    if ([string](Get-TeamProperty -InputObject $red -Name "result" -Default "") -ne "red" -or [string](Get-TeamProperty -InputObject $red -Name "sha" -Default "") -ne $a) {
        return (& $no "gate-$of.json $a üzerinde kırmızı bir kapı değil")
    }
    $redSteps = @(@(Get-TeamProperty -InputObject $red -Name "steps" -Default @()) | ForEach-Object { [string]$_ })
    $notRerun = @($redSteps | Where-Object { $steps -notcontains $_ })
    if (@($redSteps).Count -eq 0 -or @($notRerun).Count -gt 0) { return (& $no ("kırmızı adımlar yeniden koşulmadı: " + ($notRerun -join ", "))) }
    if ($a -ne $b -and -not (Test-TeamAncestor -RepoRoot $RepoRoot -Ancestor $a -Of $b)) { return (& $no "$b, $a commit'inden inmiyor: kapı ve yeniden koşu zincirlenmiyor") }
    $aLog = Join-Path $Directory (Split-Path -Leaf ([string](Get-TeamProperty -InputObject $red -Name "log" -Default "")))
    if (-not (Test-Path -LiteralPath $aLog -PathType Leaf)) { return (& $no "kapı A'nın günlüğü yok") }
    $aText = [System.IO.File]::ReadAllText($aLog, [System.Text.Encoding]::UTF8)
    if (-not ($aText -match '(?m)^QUALITY GATE: FAIL\s*$')) { return (& $no "kapı A sonuna kadar koşmadı") }
    $aGate = Read-TeamGateLog -Text $aText -ExitCode 1
    $other = @(@($aGate.FailedSteps) | Where-Object { $redSteps -notcontains $_ })
    if (@($other).Count -gt 0) { return (& $no ("kapı A'nın günlüğünde kayıtta olmayan kırmızı adım var: " + ($other -join ", "))) }
    $bLogName = Split-Path -Leaf ([string](Get-TeamProperty -InputObject $Record -Name "rerun_log" -Default ""))
    $bLog = if ($bLogName) { Join-Path $Directory $bLogName } else { "" }
    if (-not $bLog -or -not (Test-Path -LiteralPath $bLog -PathType Leaf)) { return (& $no "yeniden koşunun günlüğü yok") }
    $slice = Test-TeamGateRerunLog -Text ([System.IO.File]::ReadAllText($bLog, [System.Text.Encoding]::UTF8)) -ExitCode 0 -Steps $steps
    if (-not $slice.Ok) { return (& $no ("yeniden koşu günlüğü: " + $slice.Why)) }
    return [pscustomobject]@{ Ok = $true; Why = "kapı $a + yeniden koşu $b (adımlar: " + ($steps -join "; ") + ")" }
}
