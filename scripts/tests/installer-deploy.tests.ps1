<#
.SYNOPSIS
    The transactional deployment engine, under Windows PowerShell 5.1, against real
    directory trees and a real running process holding the live tree.

.DESCRIPTION
    The incident these tests encode: a deployment renamed the live service directory while
    the service was RUNNING from it. NTFS refused (mapped images), the first move died, the
    companion had already been killed with its restart after the failing call, and the
    machine was left with an empty .previous, a full .staging, a running service and no
    companion — with nothing recording which state it was in.

    The "runtime" here is a real powershell.exe child launched FROM the live tree, so the
    tree really is locked by a mapped image, exactly like the service. The engine's handlers
    are injected, so every failure leg is drivable deterministically.

    Run: powershell -NoProfile -File scripts\tests\installer-deploy.tests.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\Deployment.ps1")

$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-deploy-tests-$([guid]::NewGuid().ToString('N'))"
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

function New-DeployRoot {
    <#  A root with live trees and a staged candidate, shaped like the real install.  #>
    param([string]$Name, [switch]$NoStaging)
    $root = Join-Path $script:Sandbox $Name
    foreach ($component in @("service", "companion")) {
        New-Item -ItemType Directory -Force -Path (Join-Path $root $component) | Out-Null
        Set-Content -LiteralPath (Join-Path $root "$component\PagentOS.Fake.exe") -Value "v1" -Encoding ASCII
        Set-Content -LiteralPath (Join-Path $root "$component\marker.txt") -Value "old" -Encoding ASCII
        if (-not $NoStaging) {
            New-Item -ItemType Directory -Force -Path (Join-Path $root ".staging\$component") | Out-Null
            Set-Content -LiteralPath (Join-Path $root ".staging\$component\PagentOS.Fake.exe") -Value "v2" -Encoding ASCII
            Set-Content -LiteralPath (Join-Path $root ".staging\$component\marker.txt") -Value "new" -Encoding ASCII
        }
    }
    return $root
}

function Start-TreeLocker {
    <#
    .SYNOPSIS
        A REAL process executing from the live tree, so the tree is locked the way the real
        service locks it: by a mapped image, which makes the directory unrenamable.
    #>
    param([string]$LiveDir)
    $exe = Join-Path $LiveDir "locker.exe"
    Copy-Item -LiteralPath (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe") -Destination $exe
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe
    $psi.Arguments = "-NoProfile -Command Start-Sleep -Seconds 300"
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    return [System.Diagnostics.Process]::Start($psi)
}

$noop = { }
$healthy = { $true }

try {
    Write-Host ""
    Write-Host "the incident itself"

    Test-Case "a tree with a running process is refused BEFORE anything moves" {
        $root = New-DeployRoot -Name "locked"
        $locker = Start-TreeLocker -LiveDir (Join-Path $root "service")
        try {
            # StopRuntime deliberately does NOT stop the locker: the engine must detect the
            # survivor and refuse, instead of dying mid-swap like the real deployment did.
            try {
                Invoke-AgentDeployment -Root $root -Components @("service", "companion") `
                    -StopRuntime $noop -StartRuntime $noop -TestHealth $healthy | Out-Null
                throw "the deployment should have refused"
            }
            catch {
                Assert-True -Condition ($_.Exception.Message -match "processes execute from them|locker") `
                    -Because "the refusal must name the holder: $($_.Exception.Message)"
            }

            # And nothing moved: live intact, staging intact.
            Assert-Equal -Expected "old" -Actual (Get-Content (Join-Path $root "service\marker.txt")) -Because "live must be untouched"
            Assert-True -Condition (Test-Path (Join-Path $root ".staging\service\marker.txt")) -Because "the candidate must survive for a rerun"
            $journal = Read-DeployJournal -Root $root
            Assert-Equal -Expected "rolled_back" -Actual $journal.phase -Because "the journal must record the outcome"
        }
        finally {
            try { $locker.Kill(); [void]$locker.WaitForExit(5000) } catch { }
        }
    }

    Test-Case "with the runtime properly stopped, the same deployment succeeds" {
        $root = New-DeployRoot -Name "clean-swap"
        $locker = Start-TreeLocker -LiveDir (Join-Path $root "service")
        $stop = { if (-not $locker.HasExited) { $locker.Kill(); [void]$locker.WaitForExit(5000) } }.GetNewClosure()

        $result = Invoke-AgentDeployment -Root $root -Components @("service", "companion") `
            -StopRuntime $stop -StartRuntime $noop -TestHealth $healthy

        Assert-True -Condition $result -Because "the deployment should commit"
        Assert-Equal -Expected "new" -Actual (Get-Content (Join-Path $root "service\marker.txt")) -Because "live is the candidate"
        Assert-Equal -Expected "new" -Actual (Get-Content (Join-Path $root "companion\marker.txt")) -Because "both components"
        Assert-Equal -Expected "committed" -Actual (Read-DeployJournal -Root $root).phase -Because "journal committed"
        Assert-True -Condition (-not (Test-Path (Join-Path $root ".previous"))) -Because "old version deleted only after success - and it was successful"
    }

    Write-Host ""
    Write-Host "failure legs, each restoring BOTH halves"

    Test-Case "candidate fails health -> rollback to previous, runtime restarted" {
        $root = New-DeployRoot -Name "unhealthy"
        $script:starts = 0
        $start = { $script:starts++ }
        try {
            Invoke-AgentDeployment -Root $root -Components @("service", "companion") `
                -StopRuntime $noop -StartRuntime $start -TestHealth { $false } | Out-Null
            throw "should have thrown"
        }
        catch {
            Assert-True -Condition ($_.Exception.Message -match "did not become healthy") -Because "got: $($_.Exception.Message)"
        }

        Assert-Equal -Expected "old" -Actual (Get-Content (Join-Path $root "service\marker.txt")) -Because "previous restored"
        Assert-Equal -Expected "old" -Actual (Get-Content (Join-Path $root "companion\marker.txt")) -Because "BOTH restored, not only the service"
        Assert-True -Condition (Test-Path (Join-Path $root ".staging\service\marker.txt")) -Because "candidate back in staging for a retry"
        Assert-True -Condition ($script:starts -ge 2) -Because "StartRuntime ran for the candidate AND again for the restored previous"
        Assert-Equal -Expected "rolled_back" -Actual (Read-DeployJournal -Root $root).phase -Because "journal records the rollback"
    }

    Test-Case "the restored PREVIOUS release is judged by the baseline predicate, not the candidate's contract" {
        # 2026-09-08 incident, second failure. TestHealth asserts the CANDIDATE's contract -
        # its capability manifest, its version on Cloud Core. The engine then ran that same
        # predicate against the RESTORED PREVIOUS release, which predates all of it by
        # definition, and journalled "restored but NOT healthy - investigate" about a correct
        # rollback. The owner's log showed "the installed service ... lacks: browser.media_play,
        # browser.media_volume, browser.media_status, browser.media_stop" - a capability
        # regression that did not exist.
        $root = New-DeployRoot -Name "rollback-baseline"
        $script:candidateChecks = 0
        $script:baselineChecks = 0
        # Fails for the candidate AND would fail for the previous release - the candidate's
        # contract is not satisfiable by the old binaries, which is the whole point.
        $candidateHealth = { $script:candidateChecks++; return $false }
        $baselineHealth = { $script:baselineChecks++; return $true }
        try {
            Invoke-AgentDeployment -Root $root -Components @("service", "companion") `
                -StopRuntime $noop -StartRuntime $noop -TestHealth $candidateHealth `
                -TestRollbackHealth $baselineHealth | Out-Null
            throw "should have thrown"
        }
        catch {
            Assert-True -Condition ($_.Exception.Message -match "did not become healthy") -Because "got: $($_.Exception.Message)"
        }

        Assert-Equal -Expected "old" -Actual (Get-Content (Join-Path $root "service\marker.txt")) -Because "the previous release is live again"
        Assert-Equal -Expected 1 -Actual $script:candidateChecks -Because "the candidate's contract is asked EXACTLY once, about the candidate"
        Assert-Equal -Expected 1 -Actual $script:baselineChecks -Because "the restored release is judged by the baseline predicate instead"
        $journal = Read-DeployJournal -Root $root
        Assert-Equal -Expected "rolled_back" -Actual $journal.phase -Because "still a rollback"
        $rolled = @($journal.history | Where-Object { $_.phase -eq "rolled_back" })[-1]
        Assert-True -Condition ($rolled.detail -match "restored and healthy") `
            -Because "a correct rollback must journal 'restored and healthy', not 'NOT healthy - investigate'; got: $($rolled.detail)"
    }

    Test-Case "a rollback whose restored release really is broken still says so" {
        # The fix must not turn the rollback health check into a rubber stamp.
        $root = New-DeployRoot -Name "rollback-broken"
        try {
            Invoke-AgentDeployment -Root $root -Components @("service", "companion") `
                -StopRuntime $noop -StartRuntime $noop -TestHealth { $false } `
                -TestRollbackHealth { $false } | Out-Null
            throw "should have thrown"
        }
        catch { }
        $journal = Read-DeployJournal -Root $root
        $rolled = @($journal.history | Where-Object { $_.phase -eq "rolled_back" })[-1]
        Assert-True -Condition ($rolled.detail -match "NOT healthy - investigate") `
            -Because "a genuinely unhealthy restored release must still be reported; got: $($rolled.detail)"
    }

    Test-Case "without a baseline predicate the engine behaves exactly as before" {
        $root = New-DeployRoot -Name "rollback-default"
        $script:checks = 0
        try {
            Invoke-AgentDeployment -Root $root -Components @("service", "companion") `
                -StopRuntime $noop -StartRuntime $noop -TestHealth { $script:checks++; return $false } | Out-Null
            throw "should have thrown"
        }
        catch { }
        Assert-Equal -Expected 2 -Actual $script:checks -Because "TestHealth is still the fallback for callers that pass no baseline"
    }

    Test-Case "failure right after the first directory swap -> previous restored" {
        $root = New-DeployRoot -Name "midswap"
        # StartRuntime throws: the failure lands after candidate_promoted + acl phase.
        try {
            Invoke-AgentDeployment -Root $root -Components @("service", "companion") `
                -StopRuntime $noop -StartRuntime { throw "start exploded" } -TestHealth $healthy | Out-Null
            throw "should have thrown"
        }
        catch {
            Assert-True -Condition ($_.Exception.Message -match "start exploded") -Because "got: $($_.Exception.Message)"
        }
        Assert-Equal -Expected "old" -Actual (Get-Content (Join-Path $root "service\marker.txt")) -Because "live is the previous version again"
        $journal = Read-DeployJournal -Root $root
        Assert-Equal -Expected "rolled_back" -Actual $journal.phase -Because "journalled"
        Assert-True -Condition (@($journal.history | Where-Object { $_.phase -eq "candidate_promoted" }).Count -eq 1) `
            -Because "the journal shows how far it got before the failure"
    }

    Test-Case "ACL application failure -> rollback" {
        $root = New-DeployRoot -Name "acl-fail"
        try {
            Invoke-AgentDeployment -Root $root -Components @("service", "companion") `
                -StopRuntime $noop -StartRuntime $noop -TestHealth $healthy -ApplyAcl { throw "acl exploded" } | Out-Null
            throw "should have thrown"
        }
        catch {
            Assert-True -Condition ($_.Exception.Message -match "acl exploded") -Because "got: $($_.Exception.Message)"
        }
        Assert-Equal -Expected "old" -Actual (Get-Content (Join-Path $root "service\marker.txt")) -Because "rolled back"
    }

    Write-Host ""
    Write-Host "candidate validation and partial-state recovery"

    Test-Case "an incomplete candidate is refused before the runtime is even stopped" {
        $root = New-DeployRoot -Name "no-exe"
        Remove-Item (Join-Path $root ".staging\service\PagentOS.Fake.exe")
        $script:stopped = $false
        try {
            Invoke-AgentDeployment -Root $root -Components @("service", "companion") `
                -StopRuntime { $script:stopped = $true } -StartRuntime $noop -TestHealth $healthy | Out-Null
            throw "should have refused"
        }
        catch {
            Assert-True -Condition ($_.Exception.Message -match "no PagentOS executable") -Because "got: $($_.Exception.Message)"
        }
        Assert-True -Condition (-not $script:stopped) -Because "a broken candidate must not cost the runtime an outage"
    }

    Test-Case "the exact real-machine state resolves to RetryFromStaging" {
        # Live trees intact, complete .staging, empty .previous, NO journal - byte for byte
        # what the pre-journal failure left on the owner's machine.
        $root = New-DeployRoot -Name "real-state"
        New-Item -ItemType Directory -Force -Path (Join-Path $root ".previous") | Out-Null

        $resolution = Resolve-InterruptedDeployment -Root $root -Components @("service", "companion")
        Assert-Equal -Expected "RetryFromStaging" -Actual $resolution.Action `
            -Because "live intact + full candidate staged = the candidate is retryable: $($resolution.Reason)"
    }

    Test-Case "successful rerun from that partial staging state" {
        $root = New-DeployRoot -Name "rerun"
        New-Item -ItemType Directory -Force -Path (Join-Path $root ".previous") | Out-Null

        $resolution = Resolve-InterruptedDeployment -Root $root -Components @("service", "companion")
        Assert-Equal -Expected "RetryFromStaging" -Actual $resolution.Action -Because "precondition"

        $result = Invoke-AgentDeployment -Root $root -Components @("service", "companion") `
            -StopRuntime $noop -StartRuntime $noop -TestHealth $healthy
        Assert-True -Condition $result -Because "the retry should commit"
        Assert-Equal -Expected "new" -Actual (Get-Content (Join-Path $root "service\marker.txt")) -Because "candidate promoted on the rerun"
    }

    Test-Case "an interrupted journal (promoted, never verified) resolves to rollback" {
        $root = New-DeployRoot -Name "journal-promoted"
        Write-DeployPhase -Root $root -Version "test" -Phase "staged"
        Write-DeployPhase -Root $root -Version "test" -Phase "runtime_stopped"
        Write-DeployPhase -Root $root -Version "test" -Phase "candidate_promoted"

        $resolution = Resolve-InterruptedDeployment -Root $root -Components @("service", "companion")
        Assert-Equal -Expected "RollbackToPrevious" -Actual $resolution.Action `
            -Because "promoted-but-unverified must go back to the last version that worked: $($resolution.Reason)"
    }

    Test-Case "a committed journal resolves to None; a missing live tree with no journal is Blocked" {
        $rootCommitted = New-DeployRoot -Name "journal-committed" -NoStaging
        Write-DeployPhase -Root $rootCommitted -Version "test" -Phase "committed"
        Assert-Equal -Expected "None" -Actual (Resolve-InterruptedDeployment -Root $rootCommitted -Components @("service", "companion")).Action `
            -Because "committed means done"

        $rootBroken = New-DeployRoot -Name "journal-broken" -NoStaging
        Remove-Item -LiteralPath (Join-Path $rootBroken "service") -Recurse -Force
        Assert-Equal -Expected "Blocked" -Actual (Resolve-InterruptedDeployment -Root $rootBroken -Components @("service", "companion")).Action `
            -Because "no journal + incomplete live trees must refuse to guess"
    }

    Write-Host ""
    Write-Host "hardened destination"

    Test-Case "a hardened (admin-writable) destination deploys under an elevated caller, or refuses cleanly otherwise" {
        # The live trees are hardened the way Program Files is. Non-elevated, this account
        # can still rename its OWN sandbox trees (owner keeps WRITE_DAC/DELETE via the
        # parent), so what is asserted is the invariant that matters everywhere: either the
        # deployment commits, or nothing changed.
        $root = New-DeployRoot -Name "hardened"
        Set-HardenedAcl -Root (Join-Path $root "service")
        $before = Get-Content (Join-Path $root "service\marker.txt") -ErrorAction SilentlyContinue

        $committed = $false
        try {
            $committed = Invoke-AgentDeployment -Root $root -Components @("service", "companion") `
                -StopRuntime $noop -StartRuntime $noop -TestHealth $healthy
        }
        catch { }

        if ($committed) {
            Assert-Equal -Expected "new" -Actual (Get-Content (Join-Path $root "service\marker.txt")) -Because "committed means promoted"
        }
        else {
            Assert-Equal -Expected $before -Actual (Get-Content (Join-Path $root "service\marker.txt") -ErrorAction SilentlyContinue) `
                -Because "not committed means untouched"
        }
    }
}
finally {
    Get-ChildItem -LiteralPath $script:Sandbox -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        try { [void](Repair-InstallTreeAcl -Root $_.FullName -Quiet) } catch { }
    }
    Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "$($script:Passes) passed, $($script:Failures) failed"
exit $script:Failures
