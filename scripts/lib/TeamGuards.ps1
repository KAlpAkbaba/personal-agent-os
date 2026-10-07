<#
.SYNOPSIS
    The guard list (`team/guards.json`) and its runner: the tests that read the WHOLE
    application, run on one tree, with no agent and no network.

.DESCRIPTION
    Three of the seven integration gates of 1-2 October 2026 were red on their first run,
    each on a test of this family that neither the worker nor the inspector had run (the
    full unit suite is too long for a task; both wrote NOT_RUN). The family is short, so it
    can run where the work is:

      * Read-TeamGuardList   the list, or a refusal with the reason in Turkish;
      * Get-TeamGuardCommand what is started for one guard - the command the quality gate
                             uses for the same file, on the WORKTREE's copy of it;
      * Invoke-TeamGuards    the run: one guard after another, one row per entry;
      * Get-TeamGuardLastMerge, New-TeamGuardRuffRow, Get-TeamGuardAfterMergeLines
                             the run right after a merge into an integration branch: the list
                             plus ruff, a red one naming the card merged last.

    The list:   { version: 1, guards: [ { id, kind: 'pytest' | 'powershell', path, label } ] }
    The result: { at, sha, seconds, status: 'green' | 'red',
                  rows: [ { id, path, outcome: 'green' | 'red' | 'hung' | 'missing' | 'no-database',
                            seconds, label, detail } ] }

    Nothing here installs anything: no `uv sync`, no virtualenv in the worktree. The
    interpreter is the main checkout's; pytest is started in the worktree's `services/api`,
    so the code it imports is the worktree's. The run leaves the worktree as it found it.

    Windows PowerShell 5.1, StrictMode.
#>

Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "NativeProcess.ps1")
. (Join-Path $PSScriptRoot "TeamQueue.ps1")

$script:TeamGuardKinds = @("pytest", "powershell")
# A hang guard, never a measure of success: the slowest guard measured takes well under a
# minute on a busy machine. A guard that is still going after this is stopped with its
# whole process tree and its row says 'hung'.
$script:TeamGuardHangSeconds = 600
$script:TeamGuardDetailMax = 400
# After a tree was stopped (or a guard ended and left a child holding its output), how long
# the runner waits for what was printed before it goes on without it.
$script:TeamGuardDrainMilliseconds = 15000

function New-TeamGuardList {
    param([bool]$Refused, [string]$Reason, [object[]]$Guards = @())
    return [pscustomobject]@{ refused = $Refused; reason = $Reason; guards = @($Guards) }
}

function Read-TeamGuardList {
    <#
    .SYNOPSIS
        The entries of a guard list, or a refusal: `refused`, `reason` (Turkish), `guards`.
    #>
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return (New-TeamGuardList -Refused $true -Reason "liste yok: $Path")
    }
    $document = $null
    try { $document = Read-TeamJson -Path $Path }
    catch { return (New-TeamGuardList -Refused $true -Reason "liste reddedildi: geçerli JSON değil ($Path)") }
    if ([string](Get-TeamProperty -InputObject $document -Name "version" -Default "") -ne "1") {
        return (New-TeamGuardList -Refused $true -Reason "liste reddedildi: sürüm 1 olmalı ($Path)")
    }
    $entries = @(Get-TeamProperty -InputObject $document -Name "guards" -Default @())
    if (@($entries).Count -eq 0) {
        return (New-TeamGuardList -Refused $true -Reason "liste reddedildi: listede koruyucu yok ($Path)")
    }
    $guards = New-Object System.Collections.ArrayList
    $seen = @{}
    foreach ($entry in $entries) {
        $id = [string](Get-TeamProperty -InputObject $entry -Name "id" -Default "")
        $kind = [string](Get-TeamProperty -InputObject $entry -Name "kind" -Default "")
        $relative = [string](Get-TeamProperty -InputObject $entry -Name "path" -Default "")
        $label = [string](Get-TeamProperty -InputObject $entry -Name "label" -Default "")
        $problem = ""
        if ($id -cnotmatch '^[a-z0-9-]+$') { $problem = "kimlik yalnız a-z, 0-9 ve '-' içerebilir: '$id'" }
        elseif ($seen.ContainsKey($id)) { $problem = "yinelenen kimlik: '$id'" }
        elseif ($script:TeamGuardKinds -cnotcontains $kind) { $problem = "bilinmeyen tür '$kind' ($id): pytest ya da powershell olmalı" }
        elseif (-not $relative) { $problem = "yol boş ($id)" }
        elseif ($relative.Contains("\")) { $problem = "yol ters eğik çizgi içeriyor ($id): düz eğik çizgi kullanılır" }
        elseif ($relative -match '^[A-Za-z]:') { $problem = "yol sürücü harfi içeriyor ($id): depoya göreli olmalı" }
        elseif ($relative.StartsWith("/")) { $problem = "yol mutlak ($id): depoya göreli olmalı" }
        elseif (@($relative.Split("/") | Where-Object { $_ -eq ".." }).Count -gt 0) { $problem = "yol '..' içeriyor ($id): ağacın dışına çıkamaz" }
        elseif ([string]::IsNullOrWhiteSpace($label)) { $problem = "etiket boş ($id)" }
        if ($problem) { return (New-TeamGuardList -Refused $true -Reason "liste reddedildi: $problem") }
        $seen[$id] = $true
        [void]$guards.Add([pscustomobject]@{ id = $id; kind = $kind; path = $relative; label = $label })
    }
    return (New-TeamGuardList -Refused $false -Reason "" -Guards @($guards.ToArray()))
}

function Get-TeamGuardGit {
    $command = Get-Command "git.exe" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return [string]$command.Source }
    # PATH is not trustworthy in a spawned shell on the owner's machine.
    $fallback = Join-Path $env:ProgramFiles "Git\cmd\git.exe"
    if (Test-Path -LiteralPath $fallback -PathType Leaf) { return $fallback }
    throw "git bulunamadı"
}

function Invoke-TeamGuardGit {
    <# One git answer about a tree, trimmed; $null when git says no. #>
    param([Parameter(Mandatory = $true)][string]$Worktree, [Parameter(Mandatory = $true)][string[]]$Arguments)
    $ran = Invoke-NativeProcess -FilePath (Get-TeamGuardGit) -Arguments (@("-C", $Worktree) + $Arguments) -TimeoutSeconds 60
    if (-not $ran.Success) { return $null }
    return ([string]$ran.StdOut).Trim()
}

function Test-TeamGuardWorktree {
    param([Parameter(Mandatory = $true)][string]$Worktree)
    return ((Invoke-TeamGuardGit -Worktree $Worktree -Arguments @("rev-parse", "--is-inside-work-tree")) -eq "true")
}

function Get-TeamGuardHead {
    <# The 40-hex HEAD of the tree, or $null (a repository with no commit has none). #>
    param([Parameter(Mandatory = $true)][string]$Worktree)
    $sha = Invoke-TeamGuardGit -Worktree $Worktree -Arguments @("rev-parse", "--verify", "HEAD")
    if ($sha -and $sha -cmatch '^[0-9a-f]{40}$') { return $sha }
    return $null
}

function Get-TeamGuardDefaultPython {
    <#
    .SYNOPSIS
        The interpreter of the MAIN checkout's API environment - what `uv run` resolves to
        there. A worktree has no environment of its own and this runner never makes one.
    #>
    param([Parameter(Mandatory = $true)][string]$Worktree)
    $common = Invoke-TeamGuardGit -Worktree $Worktree -Arguments @("rev-parse", "--path-format=absolute", "--git-common-dir")
    if (-not $common) { return "" }
    $main = Split-Path -Parent ($common -replace "/", "\")
    return (Join-Path $main "services\api\.venv\Scripts\python.exe")
}

function Get-TeamGuardCommand {
    <#
    .SYNOPSIS
        What is started for one guard: FilePath, Arguments, WorkingDirectory, Environment (set
        for the child only) - and File, the guard itself, which is always the WORKTREE's copy -
        and Database, the throwaway database the run makes and drops ("" for none).

    .DESCRIPTION
        The same commands `scripts/quality-gate.ps1` uses for these files ('API unit tests',
        'Script syntax'), with what a second tree needs: an explicit --rootdir (a pytest
        child whose working directory and file sat on different drives spent 300 s
        collecting on this machine) and no cache provider (it writes into the tree).

        A pytest guard under services/api/tests/integration/ gets a database of its own: a new
        pagentos_g_<id> name on the server of -DatabaseUrl, and PAGENTOS_DATABASE_URL naming it
        (Test-TeamGuardNeedsDatabase). The URL holds a password: it is never printed.
    #>
    param(
        [Parameter(Mandatory = $true)]$Guard,
        [Parameter(Mandatory = $true)][string]$Worktree,
        [string]$Python = "",
        [string]$DatabaseUrl = ""
    )
    $file = Join-Path $Worktree (([string]$Guard.path) -replace "/", "\")
    if ([string]$Guard.kind -eq "ruff") {
        # `uv run ruff check .` of the gate's 'API lint (ruff)' step, on the interpreter that holds
        # ruff; no cache: the run leaves the tree as it found it.
        return [pscustomobject]@{
            FilePath         = $Python
            Arguments        = @("-m", "ruff", "check", ".", "--no-cache")
            WorkingDirectory = $file
            File             = $file
            Environment      = @{}
            Database         = ""
        }
    }
    if ([string]$Guard.kind -eq "pytest") {
        $apiRoot = Join-Path $Worktree "services\api"
        $environment = @{}
        $database = ""
        if (Test-TeamGuardNeedsDatabase -Guard $Guard) {
            $database = New-TeamGuardDatabaseName
            if ($DatabaseUrl) { $environment["PAGENTOS_DATABASE_URL"] = Get-TeamGuardDatabaseUrl -BaseUrl $DatabaseUrl -Name $database }
        }
        return [pscustomobject]@{
            FilePath         = $Python
            Arguments        = @("-m", "pytest", $file, "-q", "--rootdir", $apiRoot, "-p", "no:cacheprovider")
            WorkingDirectory = $apiRoot
            File             = $file
            Environment      = $environment
            Database         = $database
        }
    }
    return [pscustomobject]@{
        FilePath         = (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe")
        Arguments        = @("-NoProfile", "-File", $file)
        WorkingDirectory = $Worktree
        File             = $file
        Environment      = @{}
        Database         = ""
    }
}

# ------------------------------------------------------------------ a database of its own (card guards-integration-tests-own-db)
# 2026-10-07: a task's own Postgres test, run as a guard after its merge, ran on the dev server's
# SHARED database `pagentos` (tests/integration/conftest.py migrates Settings().database_url). That
# database was at a revision no released tree knows, every test errored at the fixture and the guard
# said the task was red; on a healthy shared database it would have migrated it to the integration
# branch's head. Such a guard now makes pagentos_g_<id> on the same server, points only its child at
# it, and drops it whatever the run did. The shared database is never named.

$script:TeamGuardDatabasePrefix = "pagentos_g_"
$script:TeamGuardDatabaseSeconds = 120
# Create / drop on the server of the application's URL, through the interpreter that already holds
# psycopg. The URL comes in an environment variable (never on a command line); the name is checked
# again here. An error says psycopg's own words, which carry host and port, never the password.
$script:TeamGuardDatabaseScript = @'
import os, re, sys
import psycopg
action, name = sys.argv[1], sys.argv[2]
if not re.fullmatch(r"pagentos_g_[a-z0-9]{1,40}", name):
    sys.exit("refused name: " + name)
url = re.sub(r"^postgresql\+\w+://", "postgresql://", os.environ["PAGENTOS_TEAM_GUARD_SERVER_URL"])
try:
    with psycopg.connect(url, dbname="postgres", autocommit=True, connect_timeout=10) as admin:
        if action == "drop":
            admin.execute("DROP DATABASE IF EXISTS " + name + " WITH (FORCE)")
            sys.exit(0)
        admin.execute("CREATE DATABASE " + name)
    try:
        with psycopg.connect(url, dbname=name, autocommit=True, connect_timeout=10) as own:
            own.execute("CREATE EXTENSION IF NOT EXISTS vector")
    except Exception:
        with psycopg.connect(url, dbname="postgres", autocommit=True, connect_timeout=10) as admin:
            admin.execute("DROP DATABASE IF EXISTS " + name + " WITH (FORCE)")
        raise
except Exception as error:
    sys.exit(type(error).__name__ + ": " + " ".join(str(error).split()))
'@

function Test-TeamGuardNeedsDatabase {
    <# A pytest guard whose file is under services/api/tests/integration/: it migrates a database. #>
    param([Parameter(Mandatory = $true)]$Guard)
    return ([string]$Guard.kind -eq "pytest" -and ([string]$Guard.path).StartsWith("services/api/tests/integration/", [System.StringComparison]::Ordinal))
}

function New-TeamGuardDatabaseName {
    return ($script:TeamGuardDatabasePrefix + [guid]::NewGuid().ToString("N").Substring(0, 12))
}

function Get-TeamGuardDatabaseUrl {
    <# The base URL with its database replaced by a pagentos_g_ name. Holds a password: never print it. #>
    param([Parameter(Mandatory = $true)][string]$BaseUrl, [Parameter(Mandatory = $true)][string]$Name)
    if ($Name -cnotmatch '^pagentos_g_[a-z0-9]{1,40}$') { throw "koruyucu veritabanı adı reddedildi: '$Name'" }
    if ($BaseUrl -notmatch '^(?<head>[a-z0-9+]+://[^/?#]+/)(?<db>[^/?#]*)(?<tail>[?#].*)?$') {
        throw "veritabanı ayarı veritabanı adı taşıyan bir adres değil (gösterilmez: parola taşır)"
    }
    $tail = if ($Matches.ContainsKey("tail")) { $Matches["tail"] } else { "" }
    return $Matches["head"] + $Name + $tail
}

function Get-TeamGuardSettingsDatabaseUrl {
    <# The database URL the application's Settings resolve in ApiRoot, asked of the application itself. Never printed. #>
    param([Parameter(Mandatory = $true)][string]$Python, [Parameter(Mandatory = $true)][string]$ApiRoot)
    $ran = Invoke-NativeProcess -FilePath $Python -Arguments @("-c", "from app.config import Settings; print(Settings().database_url)") `
        -WorkingDirectory $ApiRoot -TimeoutSeconds $script:TeamGuardDatabaseSeconds -SuccessExitCodes @(0, 1, 2)
    $line = @(([string]$ran.StdOut -split "`r?`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^postgres' }) | Select-Object -Last 1
    if ($ran.ExitCode -ne 0 -or -not $line) { throw "uygulamanın veritabanı ayarı okunamadı (çıkış kodu $($ran.ExitCode))" }
    return [string]$line
}

function Invoke-TeamGuardDatabase {
    <# create | drop the guard's database on the server of BaseUrl. Throws with the server's words. #>
    param(
        [Parameter(Mandatory = $true)][ValidateSet("create", "drop")][string]$Action,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$BaseUrl
    )
    if ($Name -cnotmatch '^pagentos_g_[a-z0-9]{1,40}$') { throw "koruyucu veritabanı adı reddedildi: '$Name'" }
    $before = [Environment]::GetEnvironmentVariable("PAGENTOS_TEAM_GUARD_SERVER_URL", "Process")
    [Environment]::SetEnvironmentVariable("PAGENTOS_TEAM_GUARD_SERVER_URL", $BaseUrl, "Process")
    try {
        $ran = Invoke-NativeProcess -FilePath $Python -Arguments @("-c", $script:TeamGuardDatabaseScript, $Action, $Name) `
            -TimeoutSeconds $script:TeamGuardDatabaseSeconds -SuccessExitCodes @(0, 1)
    }
    finally { [Environment]::SetEnvironmentVariable("PAGENTOS_TEAM_GUARD_SERVER_URL", $before, "Process") }
    if ($ran.ExitCode -ne 0) {
        $said = (([string]$ran.StdErr + " " + [string]$ran.StdOut) -replace '\s+', ' ').Trim()
        if ($said.Length -gt 300) { $said = $said.Substring(0, 300) }
        throw "$Action $Name (çıkış kodu $($ran.ExitCode)): $said"
    }
}

function Get-TeamGuardConsoleEncoding {
    <# What a console child writes in when it has a console of its own: the OEM code page. #>
    try {
        $page = [int](Get-ItemProperty -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Control\Nls\CodePage" -Name "OEMCP").OEMCP
        return [System.Text.Encoding]::GetEncoding($page)
    }
    catch { return [System.Text.Encoding]::UTF8 }
}

function Stop-TeamGuardProcessTree {
    <# The guard and everything it started: a guard's child holds its files and its ports. #>
    param([Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process)
    $taskkill = Join-Path (Join-Path $env:SystemRoot "System32") "taskkill.exe"
    try {
        [void](Invoke-NativeProcess -FilePath $taskkill -Arguments @("/PID", "$($Process.Id)", "/T", "/F") -TimeoutSeconds 30)
    }
    catch { }
    if (-not $Process.WaitForExit($script:TeamGuardDrainMilliseconds)) {
        try { $Process.Kill() } catch { }
        [void]$Process.WaitForExit($script:TeamGuardDrainMilliseconds)
    }
}

function Get-TeamGuardDetail {
    <#
    .SYNOPSIS
        The run's own failing lines (the failing test names), at most 400 characters. A run
        that failed without naming anything gives its last lines instead.
    #>
    param([Parameter(Mandatory = $true)][string]$Kind, [string]$Text = "")
    $lines = @($Text -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $pattern = switch ($Kind) {
        "pytest" { '^(FAILED|ERROR) ' }
        "ruff" { ':\d+:\d+: [A-Z]+\d+' }
        default { '^FAIL(ED)?\b' }
    }
    $failing = @($lines | Where-Object { $_ -cmatch $pattern })
    if (@($failing).Count -eq 0) { $failing = @($lines | Select-Object -Last 3) }
    $detail = (@($failing) -join "`n")
    if ($detail.Length -gt $script:TeamGuardDetailMax) { $detail = $detail.Substring(0, $script:TeamGuardDetailMax) }
    return $detail
}

function Invoke-TeamGuardProcess {
    <# One guard, to its end or to the hang guard: ExitCode, Hung, Text. #>
    param([Parameter(Mandatory = $true)]$Command, [Parameter(Mandatory = $true)][string]$Kind, [Parameter(Mandatory = $true)][int]$HangSeconds)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = [string]$Command.FilePath
    $psi.Arguments = (ConvertTo-NativeArgumentLine -Arguments @($Command.Arguments))
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.WorkingDirectory = [string]$Command.WorkingDirectory
    $encoding = if ($Kind -eq "powershell") { Get-TeamGuardConsoleEncoding } else { [System.Text.Encoding]::UTF8 }
    $psi.StandardOutputEncoding = $encoding
    $psi.StandardErrorEncoding = $encoding
    # The run leaves the tree as it found it: no __pycache__ beside the worktree's sources.
    $psi.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1"
    $psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8"
    $environment = Get-TeamProperty -InputObject $Command -Name "Environment" -Default @{}
    if ($environment -is [hashtable]) { foreach ($name in @($environment.Keys)) { $psi.EnvironmentVariables[[string]$name] = [string]$environment[$name] } }
    $process = [System.Diagnostics.Process]::Start($psi)
    $process.StandardInput.Close()
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $milliseconds = [int][Math]::Min([double][int]::MaxValue, [double]$HangSeconds * 1000)
    $hung = -not $process.WaitForExit($milliseconds)
    if ($hung) { Stop-TeamGuardProcessTree -Process $process }
    $text = ""
    if ($stdout.Wait($script:TeamGuardDrainMilliseconds)) { $text = [string]$stdout.Result }
    if ($stderr.Wait($script:TeamGuardDrainMilliseconds)) { $text = $text + "`n" + [string]$stderr.Result }
    $exitCode = if ($hung) { -1 } else { $process.ExitCode }
    $process.Dispose()
    return [pscustomobject]@{ ExitCode = $exitCode; Hung = $hung; Text = $text }
}

function Invoke-TeamGuards {
    <#
    .SYNOPSIS
        Run the guards of a list on one tree, one after another, and return the RESULT.

    .DESCRIPTION
        -List is the entries Read-TeamGuardList returned. One row per entry, in the list's
        order; a guard the tree does not have is 'missing', one stopped by the hang guard is
        'hung', and in both cases the guards after it still run. `status` is 'green' only
        when every row is.

        A guard under services/api/tests/integration/ runs on a pagentos_g_ database of its own
        on the server of -DatabaseUrl (default: what the worktree's Settings resolve), dropped
        after it whatever it did. One that cannot get its database is 'no-database'.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Worktree,
        [Parameter(Mandatory = $true)][object[]]$List,
        [string]$Python = "",
        [int]$HangSeconds = 0,
        [string]$DatabaseUrl = ""
    )
    if ($HangSeconds -le 0) { $HangSeconds = $script:TeamGuardHangSeconds }
    $root = (Resolve-Path -LiteralPath $Worktree).ProviderPath
    $sha = Get-TeamGuardHead -Worktree $root
    if (-not $sha) { throw "git çalışma ağacı değil ya da hiç commit yok: $root" }
    if (-not $Python) { $Python = Get-TeamGuardDefaultPython -Worktree $root }
    $at = Get-TeamTimestamp
    $total = [System.Diagnostics.Stopwatch]::StartNew()
    $rows = New-Object System.Collections.ArrayList
    $databaseProblem = ""
    foreach ($guard in @($List)) {
        if ((Test-TeamGuardNeedsDatabase -Guard $guard) -and -not $DatabaseUrl -and -not $databaseProblem) {
            try { $DatabaseUrl = Get-TeamGuardSettingsDatabaseUrl -Python $Python -ApiRoot (Join-Path $root "services\api") }
            catch { $databaseProblem = [string]$_.Exception.Message }
        }
        $command = Get-TeamGuardCommand -Guard $guard -Worktree $root -Python $Python -DatabaseUrl $DatabaseUrl
        $watch = [System.Diagnostics.Stopwatch]::StartNew()
        $outcome = "missing"
        $detail = ""
        $pathType = if ([string]$guard.kind -eq "ruff") { "Container" } else { "Leaf" }
        if (Test-Path -LiteralPath $command.File -PathType $pathType) {
            $database = [string]$command.Database
            $ready = $true
            if ($database) {
                try {
                    if ($databaseProblem) { throw $databaseProblem }
                    Invoke-TeamGuardDatabase -Action "create" -Name $database -Python $Python -BaseUrl $DatabaseUrl
                }
                catch { $ready = $false; $outcome = "no-database"; $detail = [string]$_.Exception.Message }
            }
            if ($ready) {
                try {
                    $ran = Invoke-TeamGuardProcess -Command $command -Kind ([string]$guard.kind) -HangSeconds $HangSeconds
                    if ($ran.Hung) { $outcome = "hung" }
                    elseif ($ran.ExitCode -eq 0) { $outcome = "green" }
                    else { $outcome = "red" }
                    if ($outcome -ne "green") { $detail = Get-TeamGuardDetail -Kind ([string]$guard.kind) -Text $ran.Text }
                }
                finally {
                    if ($database) {
                        try { Invoke-TeamGuardDatabase -Action "drop" -Name $database -Python $Python -BaseUrl $DatabaseUrl }
                        catch {
                            # A database left behind on the dev server is said, never hidden: not green.
                            $outcome = "red"
                            $detail = (("koruyucu veritabanı kaldırılamadı: " + $_.Exception.Message + " " + $detail).Trim())
                        }
                    }
                }
            }
            if ($detail.Length -gt $script:TeamGuardDetailMax) { $detail = $detail.Substring(0, $script:TeamGuardDetailMax) }
        }
        $watch.Stop()
        [void]$rows.Add([pscustomobject]@{
                id      = [string]$guard.id
                path    = [string]$guard.path
                outcome = $outcome
                seconds = [Math]::Round($watch.Elapsed.TotalSeconds, 1)
                label   = [string]$guard.label
                detail  = [string]$detail
            })
    }
    $total.Stop()
    $notGreen = @($rows.ToArray() | Where-Object { $_.outcome -ne "green" })
    return [pscustomobject]@{
        at      = $at
        sha     = $sha
        seconds = [Math]::Round($total.Elapsed.TotalSeconds, 1)
        status  = $(if (@($notGreen).Count -eq 0) { "green" } else { "red" })
        rows    = @($rows.ToArray())
    }
}

function Get-TeamGuardLine {
    <# The one Turkish line the owner sees for a row that is not green. #>
    param([Parameter(Mandatory = $true)]$Row)
    switch ([string]$Row.outcome) {
        "red" { return "koruyucu kırmızı: $($Row.label)" }
        "hung" { return "koruyucu asılı kaldı, durduruldu: $($Row.label)" }
        "missing" { return "koruyucu dosyası bu ağaçta yok ($($Row.path)): $($Row.label)" }
        # Not the label: the task's test never ran, so it is not said red.
        "no-database" { return "koruyucunun veritabanı açılamadı, test koşmadı ($($Row.path))" }
    }
    return ""
}

# ------------------------------------------------------------------ after a merge (card guards-after-every-merge)
# 2026-10-06: most reds that restarted the 95-minute gate were guards of this list (and ruff),
# each red only once two cards met on the integration branch, found 40-60 minutes in. They run
# right after a merge instead, and a red one names the card merged last.

$script:TeamGuardRuffPath = "services/api"

function New-TeamGuardRuffRow {
    <#
    .SYNOPSIS
        The ruff row a run after a merge adds to the list: the gate's 'API lint (ruff)' step. Not
        a kind the LIST may name (Read-TeamGuardList keeps its two): the step adds it itself.
    #>
    return [pscustomobject]@{ id = "ruff"; kind = "ruff"; path = $script:TeamGuardRuffPath; label = "ruff (uv run ruff check .): satır uzunluğu ya da kural ihlali" }
}

function Get-TeamGuardLastMerge {
    <#
    .SYNOPSIS
        The task branch merged last into the tree's branch: { branch, merge, tip }, or $null.

    .DESCRIPTION
        The newest first-parent merge whose subject is the cycle's own "merge: team/<...> into
        <...>" (Merge-TeamBranch). main's "before the gate" merge and the lead's wiring commit
        after it are not a card's, and are passed over.
    #>
    param([Parameter(Mandatory = $true)][string]$Worktree)
    $log = Invoke-TeamGuardGit -Worktree $Worktree -Arguments @("log", "--first-parent", "--merges", "-n", "200", "--format=%H %P%x09%s", "HEAD")
    if (-not $log) { return $null }
    foreach ($line in @($log -split "`r?`n")) {
        $parts = $line.Split("`t", 2)
        if (@($parts).Count -lt 2) { continue }
        if ($parts[1] -cnotmatch '^merge: (team/\S+) into \S+') { continue }
        $branch = $Matches[1]
        $shas = @($parts[0].Trim().Split(" ") | Where-Object { $_ })
        if (@($shas).Count -lt 3) { continue }
        return [pscustomobject]@{ branch = $branch; merge = $shas[0]; tip = $shas[2] }
    }
    return $null
}

function Get-TeamGuardAfterMergeLines {
    <# What a run after a merge says: one green line, or one line per row that is not green - each with the card merged last. #>
    param([Parameter(Mandatory = $true)]$Result, $LastMerge = $null)
    $who = if ($null -ne $LastMerge) { [string]$LastMerge.branch } else { "bilinmiyor (dalda iş birleştirmesi yok)" }
    $rows = @($Result.rows)
    if ([string]$Result.status -eq "green") {
        return @("birleştirmeden sonra koruyucular yeşil: $(@($rows).Count) koruyucu, $($Result.seconds) sn (son birleşen: $who)")
    }
    return @($rows | Where-Object { $_.outcome -ne "green" } | ForEach-Object { "$(Get-TeamGuardLine -Row $_) [$($_.id)] - son birleşen: $who" })
}
