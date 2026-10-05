<#
.SYNOPSIS
    İkinci bakış: a red gate's failed tests re-run and classified, with numbers, into one JSON.

.DESCRIPTION
    For every test the gate's log names (scripts/lib/TeamGateSecondLook.ps1):
      5 runs alone, 5 runs of its file in its own order, and - with -MainWorktree - 5 runs alone
      on main's tip; the counts become gercek / kararsiz / siraya_bagli (pytest: the polluting
      earlier file is found by halves) or yarim (no class).

    It never turns the gate green and never changes the gate's record: it only writes -OutFile:
      { durum: tamam|yarim, ozet: "kapı kırmızı: ...", testler: [ {id, paket, adim, sinif,
        sebep, sayilar: {tek, dosya, main}, kirleten, main_de_de} ], ... }

    Limits, all binding:
      - the budget (-BudgetMinutes, default 20): when it runs out the look is "yarım kaldı" and
        the tests left get no class;
      - the test queue: before any run it asks scripts/team/test-slot.ps1 (heavy, and database
        for a database test); BEKLE, or a queue that fails, is "yarim" - it never runs outside it;
      - a database test runs only on the gate's own database (-DatabaseUrl, handed to the child
        as PAGENTOS_DATABASE_URL); without one it is "yarim" and never touches the shared dev one;
      - PAGENTOS_TEST_SHARD is removed from every child (the api conftest splits the suite by it).

    Exit: 0 the JSON was written (whatever it says); 2 a bad request.

    Example:
      powershell -NoProfile -File scripts/team/gate-second-look.ps1 -LogPath team/reports/c/gate-3.log -Worktree <gate tree> -MainWorktree <main tree> -OutFile team/reports/c/gate-3-second-look.json
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$LogPath,
    # The tree the gate ran in (its commit): the re-runs run here.
    [Parameter(Mandatory = $true)][string]$Worktree,
    # main's tip, checked out: the alone runs again, to tell "this branch" from "already on main".
    [string]$MainWorktree = "",
    [int]$BudgetMinutes = 20,
    [Parameter(Mandatory = $true)][string]$OutFile,
    # The gate's OWN database; a database test without it is not run.
    [string]$DatabaseUrl = "",
    # test-slot.ps1's -Store (tests use a temp one).
    [string]$TestSlotStore = "",
    # The runner, for the tests: param($Command) -> {ExitCode, Output, TimedOut}. Default: a real process.
    [scriptblock]$Invoke = $null,
    # The gate script that names each PowerShell step's .tests.ps1 (default: the worktree's).
    [string]$GateScript = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamTestSlots.ps1")
. (Join-Path $repoRoot "scripts\lib\TeamGateSecondLook.ps1")

if (-not (Test-Path -LiteralPath $LogPath)) { [Console]::Error.WriteLine("gate-second-look: no log at $LogPath"); exit 2 }
if (-not (Test-Path -LiteralPath $Worktree)) { [Console]::Error.WriteLine("gate-second-look: no worktree at $Worktree"); exit 2 }
if ($BudgetMinutes -lt 0) { [Console]::Error.WriteLine("gate-second-look: -BudgetMinutes cannot be negative"); exit 2 }
if (-not $GateScript) {
    $GateScript = Join-Path $Worktree "scripts\quality-gate.ps1"
    if (-not (Test-Path -LiteralPath $GateScript)) { $GateScript = Join-Path $repoRoot "scripts\quality-gate.ps1" }
}

$runs = $script:GateSecondLookRuns
$started = [DateTime]::UtcNow
$deadline = $started.AddMinutes($BudgetMinutes)
$halfReason = ""

function Test-OverBudget { return ([DateTime]::UtcNow -ge $deadline) }

function Invoke-GateRerunProcess {
    <# The real runner: the command with its environment changes, stdout+stderr together, killed
       (the whole tree) when the budget is out. #>
    param($Command)
    $left = [int][math]::Max(1, [math]::Floor(($deadline - [DateTime]::UtcNow).TotalSeconds))
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = [string]$Command.exe
    $psi.Arguments = ConvertTo-NativeArgumentLine -Arguments @($Command.args)
    $psi.WorkingDirectory = [string]$Command.cwd
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    foreach ($k in @($Command.env.Keys)) {
        if ($null -eq $Command.env[$k]) { if ($psi.EnvironmentVariables.ContainsKey($k)) { $psi.EnvironmentVariables.Remove($k) } }
        else { $psi.EnvironmentVariables[$k] = [string]$Command.env[$k] }
    }
    $p = [System.Diagnostics.Process]::Start($psi)
    $o = $p.StandardOutput.ReadToEndAsync()
    $e = $p.StandardError.ReadToEndAsync()
    if (-not $p.WaitForExit($left * 1000)) {
        try { & (Get-SystemTool "taskkill.exe") /T /F /PID $p.Id 2>&1 | Out-Null } catch { }
        try { $p.Kill() } catch { }
        return [pscustomobject]@{ ExitCode = -1; Output = ""; TimedOut = $true }
    }
    $p.WaitForExit()
    return [pscustomobject]@{ ExitCode = $p.ExitCode; Output = ([string]$o.Result + "`n" + [string]$e.Result); TimedOut = $false }
}

$runner = if ($null -ne $Invoke) { $Invoke } else { ${function:Invoke-GateRerunProcess} }

function Invoke-OneRun {
    param($Test, $Command)
    if ($Command.db) { $Command.env["PAGENTOS_DATABASE_URL"] = $DatabaseUrl }
    $r = & $runner $Command
    return (Get-GateRunOutcome -Test $Test -Run $r)
}

function Get-RunCounts {
    <# Runs one kind N times: {gecti, dustu, hata, n}, $null when the package has no such run.
       Sets $script:OutOfTime when the budget ran out before the N runs were done. #>
    param($Test, [string]$Kind, [string]$Root)
    $cmd = Get-GateRerunCommand -Test $Test -Kind $Kind -Root $Root
    if ($null -eq $cmd) { return $null }
    $c = [ordered]@{ gecti = 0; dustu = 0; hata = 0; n = 0 }
    for ($i = 0; $i -lt $runs; $i++) {
        if (Test-OverBudget) { $script:OutOfTime = $true; return $c }
        $one = Get-GateRerunCommand -Test $Test -Kind $Kind -Root $Root
        $word = Invoke-OneRun -Test $Test -Command $one
        $c[$word] = [int]$c[$word] + 1
        $c["n"] = [int]$c["n"] + 1
    }
    return $c
}

function Get-Passes {
    <# A count the classifier can use: passes out of N, only when all N runs gave a word. #>
    param($Counts)
    if ($null -eq $Counts) { return $null }
    if ($Counts.n -lt $runs -or $Counts.hata -gt 0) { return $null }
    return [int]$Counts.gecti
}

function Get-CollectedFiles {
    <# The suite's files in its own collection order (pytest --collect-only -q). #>
    param($Test)
    $cmd = Get-GateRerunCommand -Test $Test -Kind sira -Root $Worktree
    if ($null -eq $cmd) { return @() }
    $r = & $runner $cmd
    $files = New-Object System.Collections.ArrayList
    foreach ($line in @(([string]$r.Output) -split "`r?`n")) {
        if ($line -match '^([^\s:]+\.py)::') {
            $f = $Matches[1] -replace '\\', '/'
            if ($files -notcontains $f) { [void]$files.Add($f) }
        }
    }
    return @($files.ToArray())
}

# ---------------------------------------------------------------------- the tests
$found = @(Get-GateFailedTests -LogPath $LogPath -GateScript $GateScript)
$records = New-Object System.Collections.ArrayList
foreach ($t in $found) {
    $rec = [ordered]@{
        id = [string]$t.id; paket = [string]$t.paket; adim = [string]$t.adim; dosya = [string]$t.dosya
        sinif = $null; sebep = ""; sayilar = [ordered]@{ tek = $null; dosya = $null; main = $null }; kirleten = $null; main_de_de = $false
    }
    if (-not $t.id) { $rec.sebep = "tanınmayan kırmızı adım" }
    [void]$records.Add([pscustomobject]@{ Test = $t; Rec = $rec })
}
$runnable = @($records | Where-Object { $_.Test.id })
foreach ($x in $runnable) {
    $probe = Get-GateRerunCommand -Test $x.Test -Kind dosya -Root $Worktree
    if ($null -ne $probe -and $probe.db -and -not $DatabaseUrl) {
        $x.Rec.sinif = "yarim"; $x.Rec.sebep = "kapının kendi veritabanı verilmedi; paylaşılan dev veritabanında koşulmaz"
    }
}
$toRun = @($runnable | Where-Object { $null -eq $_.Rec.sinif })

# ---------------------------------------------------------------------- the queue and the runs
$ticket = $null
$store = if ($TestSlotStore) { $TestSlotStore } else { Get-TestSlotDefaultStore }
try {
    if (@($toRun).Count -gt 0 -and $BudgetMinutes -eq 0) { $halfReason = "bütçe 0 dakika" }
    elseif (@($toRun).Count -gt 0) {
        $kinds = New-Object System.Collections.ArrayList
        foreach ($x in $toRun) {
            $c = Get-GateRerunCommand -Test $x.Test -Kind dosya -Root $Worktree
            if ($null -ne $c) { foreach ($k in @($c.kinds)) { if ($kinds -notcontains $k) { [void]$kinds.Add($k) } } }
        }
        if (@($kinds).Count -eq 0) { [void]$kinds.Add("heavy") }
        $askArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $repoRoot "scripts\team\test-slot.ps1"), "ask",
            "-Kind", (@($kinds.ToArray()) -join ","), "-Task", "gate-second-look", "-Role", "gate", "-What", ("ikinci bakış: " + @($toRun).Count + " test"), "-Store", $store)
        $ask = Invoke-NativeProcess -FilePath (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -Arguments $askArgs -SuccessExitCodes @(0, 3) -TimeoutSeconds 120
        $answer = ([string]$ask.StdOut).Trim()
        if ($ask.ExitCode -eq 0 -and $answer -match 'ONAY\s+(\S+)') {
            $ticket = $Matches[1]
            [void](Start-TestSlotRun -Store $store -Ticket $ticket -HolderPid $PID)
        }
        elseif ($ask.ExitCode -eq 3) { $halfReason = "test sırası BEKLE ($answer)" }
        else { $halfReason = "test sırası çalışmadı (çıkış $($ask.ExitCode)); sırasız koşulmaz" }
    }

    $script:OutOfTime = $false
    foreach ($x in $toRun) {
        $t = $x.Test; $rec = $x.Rec
        if ($halfReason -or $script:OutOfTime -or (Test-OverBudget)) {
            if (-not $halfReason) { $halfReason = "$BudgetMinutes dakikalık bütçe aşıldı" }
            $rec.sinif = "yarim"; $rec.sebep = $halfReason
            continue
        }
        $rec.sayilar.tek = Get-RunCounts -Test $t -Kind tek -Root $Worktree
        if (-not $script:OutOfTime) { $rec.sayilar.dosya = Get-RunCounts -Test $t -Kind dosya -Root $Worktree }
        if (-not $script:OutOfTime -and $MainWorktree) { $rec.sayilar.main = Get-RunCounts -Test $t -Kind tek -Root $MainWorktree }
        $class = Get-GateRedClass -Alone (Get-Passes $rec.sayilar.tek) -InFile (Get-Passes $rec.sayilar.dosya) -OnMain (Get-Passes $rec.sayilar.main) -OverBudget $script:OutOfTime
        $rec.sinif = $class.sinif; $rec.sebep = $class.sebep; $rec.main_de_de = [bool]$class.main_de_de
        if ($class.sinif -eq "yarim" -and $script:OutOfTime) { $halfReason = "$BudgetMinutes dakikalık bütçe aşıldı" }
        if ($class.sinif -eq "siraya_bagli" -and $class.yer -eq "ayni_dosya") { $rec.kirleten = "aynı dosyada" }
        elseif ($class.sinif -eq "siraya_bagli") {
            $probe = Get-GateRerunCommand -Test $t -Kind bolme -Root $Worktree
            if ($null -eq $probe) { $rec.kirleten = "ikiye bölme bu pakette yok" }
            else {
                $all = @(Get-CollectedFiles -Test $t)
                $at = [array]::IndexOf([string[]]$all, [string]$t.dosya)
                $before = if ($at -gt 0) { @($all[0..($at - 1)]) } else { @() }
                $bisect = {
                    param($Files, $Target)
                    $cmd = Get-GateRerunCommand -Test $t -Kind bolme -Root $Worktree -Files @($Files)
                    $word = Invoke-OneRun -Test $t -Command $cmd
                    if ($word -eq "dustu") { return $true }
                    if ($word -eq "gecti") { return $false }
                    return $null
                }
                $p = Find-GatePolluter -Candidates $before -Target ([string]$t.id) -Invoke $bisect -Deadline $deadline
                $rec.kirleten = switch ($p.durum) {
                    "bulundu" { [string]$p.kirleten }
                    "yarim" { "yarım: aday aralığı $($p.aralik) dosya" }
                    default { "bulunamadı" }
                }
            }
        }
    }
}
finally {
    if ($ticket) { try { Complete-TestSlotRun -Store $store -Ticket $ticket -ExitCode "0" } catch { } }
}

# ---------------------------------------------------------------------- the record
function Format-Count {
    param($Counts)
    if ($null -eq $Counts) { return "yok" }
    if ($Counts.hata -gt 0 -or $Counts.n -lt $runs) { return "$($Counts.gecti)/$($Counts.n) ($($Counts.hata) sonuçsuz)" }
    return "$($Counts.gecti)/$runs"
}

$classWords = @{ gercek = "gerçek hata"; kararsiz = "kararsız test"; siraya_bagli = "sıraya bağlı test" }
$parts = New-Object System.Collections.ArrayList
$halfCount = 0
foreach ($x in $records) {
    $rec = $x.Rec
    if (-not $rec.id) { [void]$parts.Add("tanınmayan kırmızı adım: $($rec.adim)"); continue }
    if ($rec.sinif -eq "yarim") { $halfCount++; continue }
    $counts = "tek başına $(Format-Count $rec.sayilar.tek), dosya sırasında $(Format-Count $rec.sayilar.dosya)"
    if ($MainWorktree) { $counts += ", main'de $(Format-Count $rec.sayilar.main)" }
    $text = "$($classWords[$rec.sinif]) $($rec.id) ($counts)"
    if ($rec.kirleten) { $text += "; kirleten: $($rec.kirleten)" }
    if ($rec.main_de_de) { $text += "; main'de de" }
    [void]$parts.Add($text)
}
if ($halfCount -gt 0) {
    $why = if ($halfReason) { $halfReason } else { "kapının kendi veritabanı verilmedi" }
    [void]$parts.Insert(0, "ikinci bakış yarım kaldı ($why): $halfCount test sınıfsız")
}
if (@($records).Count -eq 0) { [void]$parts.Add("log'da kırık adım bulunamadı") }
$ozet = "kapı kırmızı: " + (@($parts.ToArray()) -join "; ")

$record = [ordered]@{
    surum = 1
    log = $LogPath; worktree = $Worktree; main_worktree = $MainWorktree
    baslangic = $started.ToString("yyyy-MM-ddTHH:mm:ssZ"); bitis = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    butce_dk = $BudgetMinutes
    durum = $(if ($halfCount -gt 0) { "yarim" } else { "tamam" })
    ozet = $ozet
    testler = @($records | ForEach-Object { [pscustomobject]$_.Rec })
}
$dir = Split-Path -Parent ([System.IO.Path]::GetFullPath($OutFile))
if (-not (Test-Path -LiteralPath $dir)) { [void](New-Item -ItemType Directory -Path $dir -Force) }
[System.IO.File]::WriteAllText([System.IO.Path]::GetFullPath($OutFile), ($record | ConvertTo-Json -Depth 8), (New-Object System.Text.UTF8Encoding $false))
Write-Output $ozet
exit 0
