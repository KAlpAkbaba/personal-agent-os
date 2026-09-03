<#
.SYNOPSIS
    The Browser Worker provisioning step of the installer (M13), under Windows PowerShell
    5.1, without elevation, uv, Python or Chrome.

.DESCRIPTION
    What can go wrong on the owner's machine, encoded as tests:

      - the venv is built in STAGING and then moved. An editable install (.pth naming the
        staging path) or an interpreter inside staging (pyvenv.cfg home) survives the move
        as a dangling reference and the worker dies on its first import — after the swap.
        Test-VenvStagingReferences must catch both before anything moves;
      - the self-check's stdout carries the hello as ONE JSON line among possible noise;
        the parser must find it, and must not be fooled by a non-hello object;
      - uv must never be assumed on PATH (the owner's spawned shells have a broken PATH);
      - the companion settings written must name the LIVE tree, not staging, and the data
        directory under ProgramData that the owner's account can write;
      - a directory created by the elevated installer must carry an explicit inheritable
        grant for the owner's SID, or the owner's companion cannot write the profile.

    Run: powershell -NoProfile -File scripts\tests\installer-browser.tests.ps1
    Exit code is the number of failed assertions.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\BrowserProvision.ps1")

$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-browser-tests-$([guid]::NewGuid().ToString('N'))"
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

function Assert-True { param([bool]$Condition, [string]$Because) if (-not $Condition) { throw $Because } }
function Assert-Equal { param($Expected, $Actual, [string]$Because) if ($Expected -ne $Actual) { throw "$Because`n          expected: <$Expected>`n          actual  : <$Actual>" } }

try {
    Write-Host ""
    Write-Host "package staging"

    Test-Case "the browser package copies with its lock file and without caches" {
        $source = Join-Path $script:Sandbox "src"
        New-Item -ItemType Directory -Force -Path (Join-Path $source "browser_agent\__pycache__") | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $source "browser_agent\sub") | Out-Null
        Set-Content -LiteralPath (Join-Path $source "pyproject.toml") -Value "[project]" -Encoding ASCII
        Set-Content -LiteralPath (Join-Path $source "uv.lock") -Value "version = 1" -Encoding ASCII
        Set-Content -LiteralPath (Join-Path $source "README.md") -Value "# worker" -Encoding ASCII
        Set-Content -LiteralPath (Join-Path $source "browser_agent\__init__.py") -Value "" -Encoding ASCII
        Set-Content -LiteralPath (Join-Path $source "browser_agent\worker.py") -Value "print('hi')" -Encoding ASCII
        Set-Content -LiteralPath (Join-Path $source "browser_agent\sub\x.py") -Value "" -Encoding ASCII
        Set-Content -LiteralPath (Join-Path $source "browser_agent\__pycache__\worker.cpython-312.pyc") -Value "junk" -Encoding ASCII
        Set-Content -LiteralPath (Join-Path $source "browser_agent\stale.pyc") -Value "junk" -Encoding ASCII

        $destination = Join-Path $script:Sandbox "staged"
        $count = Copy-BrowserPackageTree -Source $source -Destination $destination

        Assert-Equal -Expected 6 -Actual $count -Because "pyproject, uv.lock, README and three .py files"
        Assert-True -Condition (Test-Path (Join-Path $destination "uv.lock")) -Because "the lock file pins the environment"
        Assert-True -Condition (Test-Path (Join-Path $destination "browser_agent\sub\x.py")) -Because "nested package files"
        Assert-True -Condition (-not (Test-Path (Join-Path $destination "browser_agent\__pycache__"))) -Because "caches never ship"
        Assert-True -Condition (-not (Test-Path (Join-Path $destination "browser_agent\stale.pyc"))) -Because "compiled files never ship"
    }

    Test-Case "an incomplete package is refused before anything is copied" {
        $source = Join-Path $script:Sandbox "incomplete"
        New-Item -ItemType Directory -Force -Path $source | Out-Null
        Set-Content -LiteralPath (Join-Path $source "pyproject.toml") -Value "[project]" -Encoding ASCII
        try {
            Copy-BrowserPackageTree -Source $source -Destination (Join-Path $script:Sandbox "never") | Out-Null
            throw "should have refused"
        }
        catch {
            Assert-True -Condition ($_.Exception.Message -match "uv.lock") -Because "the error names the missing file: $($_.Exception.Message)"
        }
        Assert-True -Condition (-not (Test-Path (Join-Path $script:Sandbox "never"))) -Because "nothing was created"
    }

    Test-Case "the real services\browser package is complete enough to stage" {
        $real = Join-Path $repoRoot "services\browser"
        $destination = Join-Path $script:Sandbox "real-staged"
        $count = Copy-BrowserPackageTree -Source $real -Destination $destination
        Assert-True -Condition ($count -ge 5) -Because "pyproject, lock and the package modules"
        Assert-True -Condition (Test-Path (Join-Path $destination "browser_agent\__init__.py")) -Because "the package root"
    }

    Write-Host ""
    Write-Host "relocatable environment"

    Test-Case "an editable .pth or an in-staging interpreter is detected before the swap" {
        $staging = Join-Path $script:Sandbox "root\.staging"
        $venv = Join-Path $staging "browser\.venv"
        New-Item -ItemType Directory -Force -Path (Join-Path $venv "Lib\site-packages") | Out-Null
        Set-Content -LiteralPath (Join-Path $venv "pyvenv.cfg") -Value @("home = $staging\python\bin", "version = 3.12.4") -Encoding ASCII
        Set-Content -LiteralPath (Join-Path $venv "Lib\site-packages\_pagentos_browser.pth") -Value "$staging\browser" -Encoding ASCII

        $offenders = @(Test-VenvStagingReferences -VenvDir $venv -StagingRoot $staging)
        Assert-Equal -Expected 2 -Actual $offenders.Count -Because "both the interpreter and the editable install point into staging"
        Assert-True -Condition (($offenders -join "`n") -match "pyvenv.cfg") -Because "names the interpreter reference"
        Assert-True -Condition (($offenders -join "`n") -match "editable") -Because "names the .pth reference"
    }

    Test-Case "a venv whose interpreter lives outside staging and whose package is a real copy is relocatable" {
        $root = Join-Path $script:Sandbox "root2"
        $staging = Join-Path $root ".staging"
        $venv = Join-Path $staging "browser\.venv"
        New-Item -ItemType Directory -Force -Path (Join-Path $venv "Lib\site-packages\browser_agent") | Out-Null
        Set-Content -LiteralPath (Join-Path $venv "pyvenv.cfg") -Value @("home = $root\python\cpython-3.12\bin", "uv = 0.5") -Encoding ASCII
        Set-Content -LiteralPath (Join-Path $venv "Lib\site-packages\distutils-precedence.pth") -Value "import _distutils_hack" -Encoding ASCII

        $offenders = @(Test-VenvStagingReferences -VenvDir $venv -StagingRoot $staging)
        Assert-Equal -Expected 0 -Actual $offenders.Count -Because "nothing names the staging path: $($offenders -join '; ')"
        Assert-Equal -Expected "$root\python\cpython-3.12\bin" -Actual (Get-VenvHome -VenvDir $venv) -Because "home is read verbatim"
    }

    Write-Host ""
    Write-Host "self-check output"

    Test-Case "the hello is found among noise and a non-hello object is ignored" {
        $stdout = @(
            "some banner line",
            '{"type":"log","level":"info","event":"starting"}',
            '{"type":"hello","worker_version":"0.1.0","protocol_version":1,"capabilities":["browser.session_open","browser.navigate"],"browser":{"channel":"chrome","available":true,"version":"128.0"}}',
            ""
        ) -join "`r`n"
        $parsed = ConvertFrom-WorkerHelloOutput -StdOut $stdout
        Assert-True -Condition ($null -ne $parsed.Hello) -Because "the hello line parses"
        Assert-Equal -Expected "0.1.0" -Actual $parsed.Hello.worker_version -Because "fields are read"
        Assert-Equal -Expected 2 -Actual @($parsed.Capabilities).Count -Because "capabilities are an array"
        Assert-Equal -Expected 2 -Actual @($parsed.Noise).Count -Because "the banner and the log line are noise, not errors"
    }

    Test-Case "no hello means no hello, not a crash" {
        $parsed = ConvertFrom-WorkerHelloOutput -StdOut "Traceback (most recent call last):`nModuleNotFoundError: playwright"
        Assert-True -Condition ($null -eq $parsed.Hello) -Because "nothing parsed as a hello"
        Assert-Equal -Expected 0 -Actual @($parsed.Capabilities).Count -Because "no capabilities"
        $empty = ConvertFrom-WorkerHelloOutput -StdOut ""
        Assert-True -Condition ($null -eq $empty.Hello) -Because "empty output is handled"
    }

    Test-Case "the self-check argv is the contract's CLI plus --self-check and a throwaway data dir" {
        $arguments = New-BrowserWorkerSelfCheckArgumentList -Channel "chrome" -DataDir "C:\t\probe"
        Assert-Equal -Expected "-m browser_agent.worker --self-check --channel chrome --headless --data-dir C:\t\probe --profile-dir C:\t\probe\profile" `
            -Actual ($arguments -join " ") -Because "exact argv"
    }

    Test-Case "a missing interpreter is reported, not thrown" {
        $result = Invoke-BrowserWorkerSelfCheck -Python (Join-Path $script:Sandbox "nope\python.exe") -Channel "chrome" -DataDir (Join-Path $script:Sandbox "probe")
        Assert-True -Condition (-not $result.Ok) -Because "cannot be ok without an interpreter"
        Assert-True -Condition ($result.StdErr -match "interpreter not found") -Because "the reason is stated: $($result.StdErr)"
    }

    Test-Case "a real child that prints a hello and exits 0 passes; one that exits 1 fails with its stderr" {
        # powershell.exe stands in for python.exe: it receives the same argv and answers.
        $powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
        $good = Join-Path $script:Sandbox "good-worker.ps1"
        Set-Content -LiteralPath $good -Value 'Write-Output ''{"type":"hello","worker_version":"stub","protocol_version":1,"capabilities":["browser.inspect"],"browser":{"channel":"chrome","available":true,"version":"x"}}''; exit 0' -Encoding ASCII
        $bad = Join-Path $script:Sandbox "bad-worker.ps1"
        Set-Content -LiteralPath $bad -Value '[Console]::Error.WriteLine("chrome not found"); exit 1' -Encoding ASCII

        # Invoke-NativeProcess runs <python> <argv>; here <python> is powershell -File <script> via a wrapper cmd.
        $cmd = Join-Path $env:SystemRoot "System32\cmd.exe"
        $goodWrapper = Join-Path $script:Sandbox "good.cmd"
        Set-Content -LiteralPath $goodWrapper -Value "@`"$powershell`" -NoProfile -File `"$good`" %*" -Encoding ASCII
        $badWrapper = Join-Path $script:Sandbox "bad.cmd"
        Set-Content -LiteralPath $badWrapper -Value "@`"$powershell`" -NoProfile -File `"$bad`" %*" -Encoding ASCII

        $ok = Invoke-BrowserWorkerSelfCheck -Python $goodWrapper -Channel "chrome" -DataDir (Join-Path $script:Sandbox "probe-good")
        Assert-True -Condition $ok.Ok -Because "exit 0 with a hello is ok: exit=$($ok.ExitCode) err=$($ok.StdErr)"
        Assert-Equal -Expected "stub" -Actual $ok.Hello.worker_version -Because "the hello is returned"
        Assert-Equal -Expected 1 -Actual @($ok.Capabilities).Count -Because "capabilities are returned"

        $failed = Invoke-BrowserWorkerSelfCheck -Python $badWrapper -Channel "chrome" -DataDir (Join-Path $script:Sandbox "probe-bad")
        Assert-True -Condition (-not $failed.Ok) -Because "exit 1 is a failure"
        Assert-Equal -Expected 1 -Actual $failed.ExitCode -Because "the exit code is preserved"
        Assert-True -Condition ($failed.StdErr -match "chrome not found") -Because "stderr is captured for the owner"
    }

    Write-Host ""
    Write-Host "tool resolution and settings"

    Test-Case "uv is resolved from an explicit path or known locations, never assumed" {
        $fakeUv = Join-Path $script:Sandbox "tools\uv.exe"
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $fakeUv) | Out-Null
        Set-Content -LiteralPath $fakeUv -Value "stub" -Encoding ASCII
        Assert-Equal -Expected $fakeUv -Actual (Resolve-UvPath -Explicit $fakeUv) -Because "explicit wins"
        try {
            Resolve-UvPath -Explicit (Join-Path $script:Sandbox "missing-uv.exe") | Out-Null
            throw "should have refused a missing explicit path"
        }
        catch {
            Assert-True -Condition ($_.Exception.Message -match "does not exist") -Because "names the problem: $($_.Exception.Message)"
        }
        # The fallback list is the same one preflight.ps1 probes.
        $preflight = Get-Content -LiteralPath (Join-Path $repoRoot "scripts\preflight.ps1") -Raw
        Assert-True -Condition ($preflight -match [regex]::Escape('astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe')) -Because "preflight knows the WinGet location"
        $resolved = $null
        try { $resolved = Resolve-UvPath -Fallbacks @($fakeUv) } catch { }
        Assert-True -Condition ($null -ne $resolved) -Because "a fallback that exists resolves (or PATH had uv)"
    }

    Test-Case "companion settings name the LIVE tree and an owner-writable data directory" {
        $settings = New-CompanionBrowserSettings -BrowserRoot "C:\Program Files\PagentOS\agent\browser" `
            -BrowserDataDir "C:\ProgramData\PagentOS\companion\browser" -Channel "chrome"
        Assert-Equal -Expected "C:\Program Files\PagentOS\agent\browser\.venv\Scripts\python.exe" -Actual $settings.BrowserWorkerCommand -Because "the venv interpreter in the live tree"
        Assert-Equal -Expected "-m browser_agent.worker" -Actual $settings.BrowserWorkerArgs -Because "the contract's module entry point"
        Assert-Equal -Expected "C:\ProgramData\PagentOS\companion\browser" -Actual $settings.BrowserDataDir -Because "under the companion data dir"
        Assert-Equal -Expected "C:\ProgramData\PagentOS\companion\browser\profile" -Actual $settings.BrowserProfileDir -Because "the dedicated profile"
        Assert-Equal -Expected "chrome" -Actual $settings.BrowserChannel -Because "channel"
        Assert-Equal -Expected "true" -Actual $settings.BrowserVisible -Because "headful by default: the owner sees the window"
        Assert-True -Condition (($settings.Keys -join ",") -notmatch "staging") -Because "never a staging path"
        $json = $settings | ConvertTo-Json -Depth 4
        Assert-True -Condition ($json -match '"BrowserWorkerEager":\s*"false"') -Because "lazy start by default"
    }

    Test-Case "the installer declares -SkipBrowser, -BrowserChannel and -UvPath and stages browser as a component" {
        $installer = Get-Content -LiteralPath (Join-Path $repoRoot "scripts\install-device-service.ps1") -Raw
        Assert-True -Condition ($installer -match '\[switch\]\$SkipBrowser') -Because "-SkipBrowser"
        Assert-True -Condition ($installer -match '\$BrowserChannel\s*=\s*"chrome"') -Because "-BrowserChannel defaults to chrome"
        Assert-True -Condition ($installer -match '\[string\]\$UvPath') -Because "-UvPath"
        Assert-True -Condition ($installer -match 'lib\\BrowserProvision\.ps1') -Because "dot-sources the provisioning library"
        Assert-True -Condition ($installer -match '@\("service", "companion", "browser"\)') -Because "browser is a recoverable component"
        Assert-True -Condition ($installer -match '"sync", "--frozen", "--no-dev", "--no-editable"') -Because "frozen, no dev deps, no editable .pth"
        Assert-True -Condition ($installer -match 'Publish-StagedDirectory -Root \$InstallRoot -Component "browser"') -Because "published through the same swap"
        Assert-True -Condition ($installer -match 'BrowserEnabled\s+=\s+\$BrowserEnabled') -Because "the service is told"
        Assert-True -Condition ($installer -match 'C:\\Users' -eq $false) -Because "no user-profile path is hardcoded"
    }

    Test-Case "the verifier reports the worker self-check and the advertised manifest" {
        $verifier = Get-Content -LiteralPath (Join-Path $repoRoot "scripts\verify-device-service.ps1") -Raw
        Assert-True -Condition ($verifier -match '"6b\.1"') -Because "criterion 6b.1"
        Assert-True -Condition ($verifier -match '"6b\.2"') -Because "criterion 6b.2"
        Assert-True -Condition ($verifier -match 'Invoke-BrowserWorkerSelfCheck') -Because "runs the self-check as the current user"
        Assert-True -Condition ($verifier -match '@\("capabilities"\)') -Because "asks the service binary for its manifest"
    }

    Write-Host ""
    Write-Host "owner-writable data directory"

    Test-Case "an explicit inheritable Modify grant for the owner SID is applied and detectable" {
        $sid = ([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value
        $dir = Join-Path $script:Sandbox "data\companion\browser"
        Assert-True -Condition (-not (Test-OwnerWritableDirectory -Path $dir -OwnerSid $sid)) -Because "absent before"
        Set-OwnerWritableDirectory -Path $dir -OwnerSid $sid
        Assert-True -Condition (Test-Path $dir) -Because "created with parents"
        Assert-True -Condition (Test-OwnerWritableDirectory -Path $dir -OwnerSid $sid) -Because "explicit ACE present"
        # And a child created afterwards inherits it.
        $child = Join-Path $dir "profile"
        New-Item -ItemType Directory -Force -Path $child | Out-Null
        $acl = Get-Acl -LiteralPath $child
        $inherited = @($acl.GetAccessRules($false, $true, [System.Security.Principal.SecurityIdentifier]) | Where-Object { $_.IdentityReference.Value -eq $sid })
        Assert-True -Condition ($inherited.Count -ge 1) -Because "the grant inherits to the profile directory"
    }
}
finally {
    Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "$($script:Passes) passed, $($script:Failures) failed"
exit $script:Failures
