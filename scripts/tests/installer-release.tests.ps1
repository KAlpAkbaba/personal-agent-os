<#
.SYNOPSIS
    Release-identity checks behind INSTALL VERIFIED (scripts\lib\BrowserRelease.ps1), under
    Windows PowerShell 5.1, without elevation, uv, Python or Chrome.

.DESCRIPTION
    The 2026-09-04 deployment truthfulness defect, encoded as tests:

      - the package digest must be the SAME algorithm as browser_agent/release.py (a shared
        fixture pins both to one constant);
      - WORKER_VERSION / CONTRACTS are parsed from the source, never trusted from a hello;
      - a hello whose module lives outside the venv (a source directory shadowing the
        installed package), or whose version / hash / package digest disagree with the
        staged source, or which lacks the module block at all (an old worker), is refused;
      - source vs site-packages comparison finds stale, missing and extra files;
      - the uv argv always rebuilds the project (--reinstall-package);
      - the live-worker check refuses a pid that is not running, runs another executable,
        names another data dir, predates the deployment, or leaves a pre-swap pid alive;
      - Select-BrowserWorkerProcess finds the trampoline AND the base-interpreter child by
        command line, never by executable path;
      - the audit parser picks the newest browser_worker_started row after a moment.

    Run: powershell -NoProfile -File scripts\tests\installer-release.tests.ps1
    Exit code is the number of failed assertions.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\BrowserRelease.ps1")

$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-release-tests-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    try {
        & $Body
        $script:Passes++
        Write-Host "  PASS  $Name"
    }
    catch {
        $script:Failures++
        Write-Host "  FAIL  $Name" -ForegroundColor Red
        Write-Host "        $($_.Exception.Message)" -ForegroundColor Red
    }
}

function Assert-True { param([bool]$Condition, [string]$Message) if (-not $Condition) { throw $Message } }
function Assert-Equal { param($Expected, $Actual, [string]$Message) if ($Expected -ne $Actual) { throw "$Message (expected '$Expected', got '$Actual')" } }
function Assert-Throws {
    param([scriptblock]$Body, [string]$Pattern)
    $threw = $false
    try { & $Body } catch { $threw = $true; if ($Pattern -and $_.Exception.Message -notmatch $Pattern) { throw "threw, but not matching '$Pattern': $($_.Exception.Message)" } }
    if (-not $threw) { throw "expected an exception matching '$Pattern'" }
}

function New-SourceTree {
    param([string]$Name, [string]$Version = "0.3.0", [string]$Extra = "")
    $root = Join-Path $script:Sandbox $Name
    $pkg = Join-Path $root "browser_agent"
    New-Item -ItemType Directory -Force -Path $pkg | Out-Null
    [IO.File]::WriteAllText((Join-Path $pkg "__init__.py"), "", [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $pkg "worker.py"), "import sys`nWORKER_VERSION = `"$Version`"`nCONTRACTS: dict[str, int] = {`"browser.search`": 2}`n$Extra", [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $pkg "lifecycle.py"), "X = 1`n", [Text.UTF8Encoding]::new($false))
    return $root
}

function Copy-ToSitePackages {
    param([string]$Root)
    $site = Get-SitePackagesBrowserAgentDir -BrowserRoot $Root
    New-Item -ItemType Directory -Force -Path $site | Out-Null
    Copy-Item (Join-Path $Root "browser_agent\*.py") -Destination $site -Force
    return $site
}

function New-Hello {
    param([string]$Root, [string]$Version = "0.3.0", [string]$ModuleFile = $null, [string]$WorkerSha = $null, [string]$PackageSha = $null, [switch]$NoModule)
    $expected = Get-ExpectedWorkerRelease -BrowserSource $Root
    if (-not $ModuleFile) { $ModuleFile = Join-Path (Get-SitePackagesBrowserAgentDir -BrowserRoot $Root) "worker.py" }
    if (-not $WorkerSha) { $WorkerSha = $expected.WorkerSha256 }
    if (-not $PackageSha) { $PackageSha = $expected.PackageSha256 }
    $hello = [ordered]@{ type = "hello"; worker_version = $Version; contracts = @{ "browser.search" = 2 } }
    if (-not $NoModule) { $hello.module = @{ file = $ModuleFile; sha256 = $WorkerSha; package_sha256 = $PackageSha } }
    return ($hello | ConvertTo-Json -Depth 5 | ConvertFrom-Json)
}

Write-Host "installer-release tests"

Test-Case "package digest matches the Python algorithm on the shared fixture" {
    $dir = Join-Path $script:Sandbox "digest-fixture"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    [IO.File]::WriteAllText((Join-Path $dir "b.py"), "b = 2`n", [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $dir "a.py"), "a = 1`n", [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $dir "notes.txt"), "x", [Text.UTF8Encoding]::new($false))
    # services/browser/tests/unit/test_release.py pins the same constant for release.package_digest
    Assert-Equal "396f5e079e36439516843dfc55df4cbbe45c58a602c6aacd99e6962716f2feb9" (Get-BrowserPackageDigest -PackageDir $dir) "digest"
}

Test-Case "expected release is parsed from the source, with contracts and hashes" {
    $root = New-SourceTree "src-parse" -Version "1.2.3"
    $r = Get-ExpectedWorkerRelease -BrowserSource $root
    Assert-Equal "1.2.3" $r.Version "version"
    Assert-Equal 2 $r.Contracts["browser.search"] "contract"
    Assert-Equal 64 $r.WorkerSha256.Length "worker sha"
    Assert-Equal 64 $r.PackageSha256.Length "package sha"
    Assert-Equal (Get-FileSha256Hex -Path (Join-Path $root "browser_agent\worker.py")) $r.WorkerSha256 "worker hash equals the file"
}

Test-Case "a hello from the venv copy with matching version/hashes passes" {
    $root = New-SourceTree "ok"
    Copy-ToSitePackages $root | Out-Null
    $proof = Assert-WorkerHelloMatchesRelease -Hello (New-Hello $root) -Expected (Get-ExpectedWorkerRelease -BrowserSource $root) -BrowserRoot $root
    Assert-Equal "0.3.0" $proof.Version "version"
}

Test-Case "a hello whose module is the SOURCE tree (cwd shadowing) is refused" {
    $root = New-SourceTree "shadow"
    Copy-ToSitePackages $root | Out-Null
    $hello = New-Hello $root -ModuleFile (Join-Path $root "browser_agent\worker.py")
    Assert-Throws { Assert-WorkerHelloMatchesRelease -Hello $hello -Expected (Get-ExpectedWorkerRelease -BrowserSource $root) -BrowserRoot $root } "not inside the venv"
}

Test-Case "a hello with an old version, no module block, is refused as an old worker" {
    $root = New-SourceTree "old"
    $hello = New-Hello $root -Version "0.1.0" -NoModule
    Assert-Throws { Assert-WorkerHelloMatchesRelease -Hello $hello -Expected (Get-ExpectedWorkerRelease -BrowserSource $root) -BrowserRoot $root } "worker_version is '0.1.0'.*no 'module' block"
}

Test-Case "a hello whose package digest differs from the staged source is refused" {
    $root = New-SourceTree "digest"
    Copy-ToSitePackages $root | Out-Null
    $hello = New-Hello $root -PackageSha ("f" * 64)
    Assert-Throws { Assert-WorkerHelloMatchesRelease -Hello $hello -Expected (Get-ExpectedWorkerRelease -BrowserSource $root) -BrowserRoot $root } "package digest"
}

Test-Case "a hello whose contract is behind the staged source is refused" {
    $root = New-SourceTree "contract"
    Copy-ToSitePackages $root | Out-Null
    $hello = New-Hello $root
    $hello.contracts = ([ordered]@{ "browser.search" = 1 } | ConvertTo-Json | ConvertFrom-Json)
    Assert-Throws { Assert-WorkerHelloMatchesRelease -Hello $hello -Expected (Get-ExpectedWorkerRelease -BrowserSource $root) -BrowserRoot $root } "contract browser.search is 1"
}

Test-Case "source vs site-packages comparison reports stale, missing and extra files" {
    $root = New-SourceTree "copies"
    $site = Copy-ToSitePackages $root
    [IO.File]::WriteAllText((Join-Path $site "worker.py"), "WORKER_VERSION = `"0.1.0`"`n", [Text.UTF8Encoding]::new($false))
    Remove-Item (Join-Path $site "lifecycle.py")
    [IO.File]::WriteAllText((Join-Path $site "obsolete.py"), "", [Text.UTF8Encoding]::new($false))
    $diff = @(Compare-BrowserPackageCopies -Source (Join-Path $root "browser_agent") -Installed $site)
    Assert-Equal 3 $diff.Count "three differences: $($diff -join '; ')"
    Assert-True (($diff -join ';') -match "worker.py differs") "stale worker"
    Assert-True (($diff -join ';') -match "lifecycle.py missing") "missing"
    Assert-True (($diff -join ';') -match "obsolete.py present only") "extra"
    Remove-Item (Join-Path $site "obsolete.py")
    Assert-Equal 0 @(Compare-BrowserPackageCopies -Source (Join-Path $root "browser_agent") -Installed (Copy-ToSitePackages $root)).Count "identical after re-copy"
}

Test-Case "uv sync argv rebuilds the project and stays non-editable" {
    $args = Get-BrowserWorkerSyncArguments
    Assert-True ($args -contains "--no-editable") "no-editable"
    Assert-True ($args -contains "--frozen") "frozen"
    $i = [array]::IndexOf($args, "--reinstall-package")
    Assert-True ($i -ge 0 -and $args[$i + 1] -eq "pagentos-browser") "reinstall-package pagentos-browser"
}

Test-Case "Select-BrowserWorkerProcess finds trampoline and base-interpreter child by command line" {
    $procs = @(
        [pscustomobject]@{ ProcessId = 10; Name = "python.exe"; ExecutablePath = "C:\Program Files\PagentOS\agent\browser\.venv\Scripts\python.exe"; CommandLine = '"C:\Program Files\PagentOS\agent\browser\.venv\Scripts\python.exe" -m browser_agent.worker --data-dir C:\ProgramData\PagentOS\companion\browser --channel chrome' },
        [pscustomobject]@{ ProcessId = 11; Name = "python.exe"; ExecutablePath = "C:\Users\o\Python312\python.exe"; CommandLine = '"C:\Users\o\Python312\python.exe" -m browser_agent.worker --data-dir C:\ProgramData\PagentOS\companion\browser --channel chrome' },
        [pscustomobject]@{ ProcessId = 12; Name = "python.exe"; ExecutablePath = "C:\Users\o\Python312\python.exe"; CommandLine = '"C:\Users\o\Python312\python.exe" -m browser_agent.worker --data-dir C:\Temp\other' },
        [pscustomobject]@{ ProcessId = 13; Name = "python.exe"; ExecutablePath = "C:\Users\o\Python312\python.exe"; CommandLine = '"C:\Users\o\Python312\python.exe" -m pytest' },
        # a shell whose command line merely mentions the module (grep, an editor, this very test runner) is never a worker
        [pscustomobject]@{ ProcessId = 14; Name = "powershell.exe"; ExecutablePath = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"; CommandLine = 'powershell.exe -Command "Get-Content log | Select-String -m browser_agent.worker"' },
        [pscustomobject]@{ ProcessId = 15; Name = "python.exe"; ExecutablePath = "C:\Users\o\Python312\python.exe"; CommandLine = '"C:\Users\o\Python312\python.exe" tools\inspect.py --about browser_agent.worker' }
    )
    $all = @(Select-BrowserWorkerProcess -Processes $procs)
    Assert-Equal "10,11,12" (($all | ForEach-Object { $_.ProcessId }) -join ",") "all workers"
    $scoped = @(Select-BrowserWorkerProcess -Processes $procs -DataDir "C:\ProgramData\PagentOS\companion\browser\")
    Assert-Equal "10,11" (($scoped | ForEach-Object { $_.ProcessId }) -join ",") "scoped to the data dir"
}

Test-Case "audit parser returns the newest worker start after -Since with pid, version and module" {
    $audit = Join-Path $script:Sandbox "audit.jsonl"
    $lines = @(
        '{"ts":"2026-09-04T06:00:00.0000000+00:00","event":"browser_worker_started","status":"ok","detail":"pid=1; worker_version=0.1.0; browser=chrome/1; available=True; capabilities=24; start=1"}',
        '{"ts":"2026-09-04T07:00:00.0000000+00:00","event":"browser_worker_exited","status":"ok","detail":"pid=1"}',
        '{"ts":"2026-09-04T07:00:05.0000000+00:00","event":"browser_worker_started","status":"ok","detail":"pid=22; worker_version=0.3.0; browser=chrome/1; available=True; capabilities=24; start=2; lifecycle_fault=none; module=C:\\Program Files\\PagentOS\\agent\\browser\\.venv\\Lib\\site-packages\\browser_agent\\worker.py; package_sha256=abc"}'
    )
    [IO.File]::WriteAllLines($audit, $lines)
    $row = Get-LatestWorkerStartAudit -AuditPath $audit -Since ([datetime]::Parse("2026-09-04T06:30:00Z").ToUniversalTime())
    Assert-Equal 22 $row.Pid "pid"
    Assert-Equal "0.3.0" $row.WorkerVersion "version"
    Assert-Equal "C:\Program Files\PagentOS\agent\browser\.venv\Lib\site-packages\browser_agent\worker.py" $row.Module "module"
    $none = Get-LatestWorkerStartAudit -AuditPath $audit -Since ([datetime]::Parse("2026-09-04T08:00:00Z").ToUniversalTime())
    Assert-True ($null -eq $none) "nothing after 08:00"
}

Test-Case "live worker check: the good case passes and each disagreement is named" {
    $root = New-SourceTree "live"
    $expected = Get-ExpectedWorkerRelease -BrowserSource $root
    $exe = Join-Path $root ".venv\Scripts\python.exe"
    $module = Join-Path (Get-SitePackagesBrowserAgentDir -BrowserRoot $root) "worker.py"
    $deployAt = [datetime]::Parse("2026-09-04T09:41:00")
    $audit = [pscustomobject]@{ Ts = $deployAt.AddSeconds(30).ToUniversalTime(); Pid = 500; WorkerVersion = "0.3.0"; Module = $module; Detail = "" }
    $good = [pscustomobject]@{ ProcessId = 500; Name = "python.exe"; ExecutablePath = $exe; CommandLine = "`"$exe`" -m browser_agent.worker --data-dir C:\ProgramData\PagentOS\companion\browser --channel chrome"; CreationDate = $deployAt.AddSeconds(20) }
    $problems = @(Test-LiveBrowserWorker -Audit $audit -Expected $expected -BrowserRoot $root -BrowserDataDir "C:\ProgramData\PagentOS\companion\browser" -DeployStartedAt $deployAt -PreviousPids @(400) -Processes @($good))
    Assert-Equal 0 $problems.Count "good case: $($problems -join '; ')"

    $oldAlive = [pscustomobject]@{ ProcessId = 400; Name = "python.exe"; ExecutablePath = "C:\Users\o\Python312\python.exe"; CommandLine = "python -m browser_agent.worker --data-dir C:\ProgramData\PagentOS\companion\browser"; CreationDate = $deployAt.AddHours(-1) }
    $problems = @(Test-LiveBrowserWorker -Audit $audit -Expected $expected -BrowserRoot $root -BrowserDataDir "C:\ProgramData\PagentOS\companion\browser" -DeployStartedAt $deployAt -PreviousPids @(400) -Processes @($good, $oldAlive))
    Assert-True (($problems -join ';') -match "pre-swap worker pid 400 is still alive") "old pid alive: $($problems -join '; ')"

    $stale = [pscustomobject]@{ Ts = $audit.Ts; Pid = 500; WorkerVersion = "0.1.0"; Module = "-"; Detail = "" }
    $problems = @(Test-LiveBrowserWorker -Audit $stale -Expected $expected -BrowserRoot $root -BrowserDataDir "C:\ProgramData\PagentOS\companion\browser" -DeployStartedAt $deployAt -Processes @($good))
    Assert-True (($problems -join ';') -match "started worker '0.1.0'") "stale version"
    Assert-True (($problems -join ';') -match "no module origin") "no module"

    $elsewhere = [pscustomobject]@{ ProcessId = 500; Name = "python.exe"; ExecutablePath = "C:\Users\o\Python312\python.exe"; CommandLine = "python -m browser_agent.worker --data-dir C:\Temp\x"; CreationDate = $deployAt.AddHours(-2) }
    $problems = @(Test-LiveBrowserWorker -Audit $audit -Expected $expected -BrowserRoot $root -BrowserDataDir "C:\ProgramData\PagentOS\companion\browser" -DeployStartedAt $deployAt -Processes @($elsewhere))
    Assert-True (($problems -join ';') -match "runs 'C:\\Users") "other executable"
    Assert-True (($problems -join ';') -match "does not name --data-dir") "other data dir"
    Assert-True (($problems -join ';') -match "before this deployment") "older than the deployment"

    $problems = @(Test-LiveBrowserWorker -Audit $audit -Expected $expected -BrowserRoot $root -BrowserDataDir "C:\ProgramData\PagentOS\companion\browser" -DeployStartedAt $deployAt -Processes @())
    Assert-True (($problems -join ';') -match "is not running") "pid gone"
}

Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
Write-Host ""
Write-Host "$($script:Passes) passed, $($script:Failures) failed"
exit $script:Failures
