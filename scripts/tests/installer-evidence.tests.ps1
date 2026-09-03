<#
.SYNOPSIS
    Regression tests for the install evidence and the M13 support assertion, and the
    static guarantee that the installer deploys through the journaled engine.
.DESCRIPTION
    The incident: on 2026-09-03 the owner reran the installer after the M13 release; it
    built and staged the new binaries, then its inline swap tried to rename the live
    directories while the service and companion were running from them, NTFS refused,
    `.previous` stayed empty and the old binaries kept running - and the installer had
    printed nothing that said so once the elevated window closed. These tests pin:
      * the installer calls Invoke-AgentDeployment (stop -> swap -> start -> health ->
        commit, rollback otherwise) and never the pre-engine Publish-StagedDirectory swap;
      * the evidence helpers read the commit, hash artifacts and compare them;
      * a manifest without the capabilities verb, or without the browser family when it
        was provisioned, FAILS the install instead of passing.
    Run: powershell -NoProfile -File scripts\tests\installer-evidence.tests.ps1
#>
[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\InstallEvidence.ps1")

$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-evidence-tests-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null

function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body; $script:Passes++; Write-Host "  PASS  $Name" }
    catch { $script:Failures++; Write-Host "  FAIL  $Name" -ForegroundColor Red; Write-Host "        $($_.Exception.Message)" -ForegroundColor Red }
}
function Assert-True { param([bool]$Condition, [string]$Because) if (-not $Condition) { throw $Because } }
function Assert-Equal { param($Expected, $Actual, [string]$Because) if ($Expected -ne $Actual) { throw "$Because`n          expected: <$Expected>`n          actual  : <$Actual>" } }
function Assert-Throws { param([scriptblock]$Body, [string]$Pattern, [string]$Because) $threw = $false; try { & $Body } catch { $threw = $true; if ($_.Exception.Message -notmatch $Pattern) { throw "$Because - wrong message: $($_.Exception.Message)" } }; if (-not $threw) { throw "$Because - did not throw" } }

function New-FakeManifest { param([string[]]$Capabilities, [bool]$Ok = $true, [bool]$BrowserEnabled = $true, [int]$ExitCode = 0)
    return [pscustomobject]@{ Ok = $Ok; Capabilities = @($Capabilities); BrowserEnabled = $BrowserEnabled; ExitCode = $ExitCode; StdErr = "" }
}
$allBrowser = @("browser.chrome") + @(
    "browser.session_open", "browser.session_close", "browser.worker_status",
    "browser.navigate", "browser.back", "browser.forward",
    "browser.tab_list", "browser.tab_new", "browser.tab_close", "browser.tab_select",
    "browser.inspect", "browser.find", "browser.click", "browser.fill", "browser.select_option",
    "browser.set_checked", "browser.scroll", "browser.wait", "browser.extract", "browser.snapshot",
    "browser.screenshot", "browser.download", "browser.search", "browser.fetch_evidence")
$desktop = @("desktop.open_application", "desktop.open_artifact")

try {
    Write-Host ""
    Write-Host "installer deploys through the journaled engine"

    Test-Case "the installer dot-sources Deployment.ps1 and calls Invoke-AgentDeployment, never Publish-StagedDirectory" {
        $installer = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\install-device-service.ps1"))
        Assert-True -Condition ($installer -match 'lib\\Deployment\.ps1') -Because "the engine must be loaded"
        Assert-True -Condition ($installer -match 'Invoke-AgentDeployment') -Because "the engine must be the deploy path"
        Assert-True -Condition ($installer -notmatch 'Publish-StagedDirectory') -Because "the pre-engine swap renames a live tree under a running process"
        Assert-True -Condition ($installer -match 'Assert-InstalledAgentSupportsM13') -Because "an M13-incapable result must fail the install"
        Assert-True -Condition ($installer -match 'Start-Transcript') -Because "a closed elevated window must not lose the failure"
    }

    Test-Case "the runtime handlers stop everything executing from the install root, by PID, before any move" {
        $lib = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\lib\AgentRuntime.ps1"))
        Assert-True -Condition ($lib -match 'Get-ProcessesExecutingUnder') -Because "the Browser Worker's python.exe locks the browser tree too"
        Assert-True -Condition ($lib -match 'WaitForStatus\("Stopped"') -Because "the service stop is awaited"
        Assert-True -Condition ($lib -match 'Wait-ProcessGone') -Because "stopped means the PID is gone, not that the SCM said so"
    }

    Write-Host ""
    Write-Host "evidence: repo HEAD, hashes, comparison"

    Test-Case "Get-RepoHead resolves a symbolic ref, a packed ref and a detached HEAD without git.exe" {
        $repo = Join-Path $script:Sandbox "repo"
        New-Item -ItemType Directory -Force -Path (Join-Path $repo ".git\refs\heads") | Out-Null
        [System.IO.File]::WriteAllText((Join-Path $repo ".git\HEAD"), "ref: refs/heads/main`n")
        [System.IO.File]::WriteAllText((Join-Path $repo ".git\refs\heads\main"), "0123456789abcdef0123456789abcdef01234567`n")
        Assert-Equal -Expected "0123456789abcdef0123456789abcdef01234567 (refs/heads/main)" -Actual (Get-RepoHead -RepoRoot $repo) -Because "loose ref"
        Remove-Item -LiteralPath (Join-Path $repo ".git\refs\heads\main")
        [System.IO.File]::WriteAllText((Join-Path $repo ".git\packed-refs"), "# pack-refs with: peeled`nfedcba9876543210fedcba9876543210fedcba98 refs/heads/main`n")
        Assert-Equal -Expected "fedcba9876543210fedcba9876543210fedcba98 (refs/heads/main, packed)" -Actual (Get-RepoHead -RepoRoot $repo) -Because "packed ref"
        [System.IO.File]::WriteAllText((Join-Path $repo ".git\HEAD"), "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`n")
        Assert-Equal -Expected "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa (detached)" -Actual (Get-RepoHead -RepoRoot $repo) -Because "detached"
        Assert-True -Condition ((Get-RepoHead -RepoRoot (Join-Path $script:Sandbox "nowhere")) -like "unknown*") -Because "no repo is reported, not thrown"
    }

    Test-Case "the real repository's HEAD resolves to a 40-hex commit" {
        $head = Get-RepoHead -RepoRoot $repoRoot
        Assert-True -Condition ($head -match '^[0-9a-f]{40} \(') -Because "got: $head"
    }

    Test-Case "artifact hashes are computed per component file, compared, and a changed byte is a mismatch" {
        $a = Join-Path $script:Sandbox "tree-a"; $b = Join-Path $script:Sandbox "tree-b"
        foreach ($root in @($a, $b)) {
            foreach ($component in @("service", "companion")) {
                New-Item -ItemType Directory -Force -Path (Join-Path $root $component) | Out-Null
                [System.IO.File]::WriteAllText((Join-Path $root "$component\PagentOS.Agent.Core.dll"), "core")
            }
            [System.IO.File]::WriteAllText((Join-Path $root "service\PagentOS.DeviceService.dll"), "service")
            [System.IO.File]::WriteAllText((Join-Path $root "companion\PagentOS.SessionCompanion.dll"), "companion")
        }
        $ha = Get-ArtifactHashes -Root $a; $hb = Get-ArtifactHashes -Root $b
        Assert-Equal -Expected 4 -Actual $ha.Count -Because "four evidence artifacts"
        Assert-Equal -Expected 0 -Actual @(Compare-ArtifactHashes -Expected $ha -Actual $hb).Count -Because "identical trees agree"
        [System.IO.File]::WriteAllText((Join-Path $b "service\PagentOS.DeviceService.dll"), "service-v2")
        $diff = @(Compare-ArtifactHashes -Expected $ha -Actual (Get-ArtifactHashes -Root $b))
        Assert-Equal -Expected 1 -Actual $diff.Count -Because "one artifact changed"
        Assert-True -Condition ($diff[0] -like "service\PagentOS.DeviceService.dll*") -Because "the mismatch names the file"
        Assert-Equal -Expected "MISSING" -Actual (Get-ArtifactHash -Path (Join-Path $b "nope.dll")) -Because "a missing artifact is MISSING, not an exception"
    }

    Write-Host ""
    Write-Host "M13 support assertion"

    Test-Case "a binary that does not answer the capabilities verb fails the install loudly" {
        Assert-Throws -Body { Assert-InstalledAgentSupportsM13 -Manifest (New-FakeManifest -Capabilities @() -Ok $false -ExitCode 2) -ExpectBrowser $true } `
            -Pattern "does not answer the 'capabilities' verb" -Because "pre-M13 binary in the live tree"
    }

    Test-Case "a desktop-only manifest fails when the browser worker was provisioned" {
        Assert-Throws -Body { Assert-InstalledAgentSupportsM13 -Manifest (New-FakeManifest -Capabilities $desktop -BrowserEnabled $false) -ExpectBrowser $true } `
            -Pattern "lacks: browser.chrome" -Because "the family must be advertised"
    }

    Test-Case "a manifest missing one browser operation fails and names it" {
        $partial = $desktop + ($allBrowser | Where-Object { $_ -ne "browser.fetch_evidence" })
        Assert-Throws -Body { Assert-InstalledAgentSupportsM13 -Manifest (New-FakeManifest -Capabilities $partial) -ExpectBrowser $true } `
            -Pattern "browser.fetch_evidence" -Because "every contract operation is required"
    }

    Test-Case "the full manifest passes; desktop-only passes when the browser was skipped; a skipped browser that still advertises the family fails" {
        Assert-InstalledAgentSupportsM13 -Manifest (New-FakeManifest -Capabilities ($desktop + $allBrowser)) -ExpectBrowser $true
        Assert-InstalledAgentSupportsM13 -Manifest (New-FakeManifest -Capabilities $desktop -BrowserEnabled $false) -ExpectBrowser $false
        Assert-Throws -Body { Assert-InstalledAgentSupportsM13 -Manifest (New-FakeManifest -Capabilities ($desktop + $allBrowser)) -ExpectBrowser $false } `
            -Pattern "although the browser worker was not provisioned" -Because "advertising what was not provisioned is a mismatch"
    }

    Test-Case "the manifest reader parses the verb's JSON from a real child and reports a pre-M13 usage answer as not Ok" {
        $fake = Join-Path $script:Sandbox "fake-service.cmd"
        [System.IO.File]::WriteAllText($fake, "@echo off`r`nif ""%1""==""capabilities"" (echo {""browser_enabled"":true,""capabilities"":[""desktop.open_application"",""browser.chrome""]}& exit /b 0)`r`necho usage 1>&2`r`nexit /b 2`r`n")
        $manifest = Get-InstalledAgentManifest -ServiceExe $fake
        Assert-True -Condition $manifest.Ok -Because "the verb answered"
        Assert-True -Condition (@($manifest.Capabilities) -contains "browser.chrome") -Because "parsed capabilities"
        Assert-True -Condition $manifest.BrowserEnabled -Because "parsed flag"
        $old = Join-Path $script:Sandbox "old-service.cmd"
        [System.IO.File]::WriteAllText($old, "@echo off`r`necho usage: 1>&2`r`nexit /b 2`r`n")
        $legacy = Get-InstalledAgentManifest -ServiceExe $old
        Assert-True -Condition (-not $legacy.Ok) -Because "a usage answer is not a manifest"
        Assert-Equal -Expected 2 -Actual $legacy.ExitCode -Because "exit code surfaced"
        Assert-True -Condition (-not (Get-InstalledAgentManifest -ServiceExe (Join-Path $script:Sandbox "missing.exe")).Ok) -Because "missing binary is not Ok"
    }
}
finally {
    Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "$($script:Passes) passed, $($script:Failures) failed"
exit $script:Failures
