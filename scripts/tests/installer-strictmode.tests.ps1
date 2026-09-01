<#
.SYNOPSIS
    Windows PowerShell 5.1 cardinality regression tests: 0, 1 and many, under StrictMode.

.DESCRIPTION
    A real elevated install failed with:

        The property 'Count' cannot be found on this object. Verify that the property exists.
        FullyQualifiedErrorId : PropertyNotFoundStrict

    PowerShell unrolls a returned collection. A function returning an empty array yields
    $null; one returning a single item yields that item. Under `Set-StrictMode -Version
    Latest` — which the installer's libraries set, and which dot-sourcing propagates into the
    installer's own scope — reading `.Count` on either throws. Reproduced here before fixing:

        0 restored -> $null           -> .Count throws
        1 restored -> System.String   -> .Count throws
        2 restored -> Object[]        -> works

    So the rerun with nothing to restore failed, and a rerun with exactly one thing to restore
    would have failed too. The previous tests passed because they happened to exercise neither
    through a `.Count`.

    This file therefore does three things:

      1. asserts the engine really is Windows PowerShell 5.1 with StrictMode Latest, so the
         suite cannot silently pass on a more forgiving host;
      2. drives every collection-returning function at 0, 1 and many, and reads `.Count` on
         each result the way the installer does;
      3. lints the installer scripts mechanically for the pattern itself, so a future edit
         that reintroduces a bare `$x.Count` fails here rather than on the owner's machine.

    Run: powershell -NoProfile -File scripts\tests\installer-strictmode.tests.ps1
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\InstallAcl.ps1")
. (Join-Path $repoRoot "scripts\lib\ServiceInstall.ps1")

$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-strict-tests-$([guid]::NewGuid().ToString('N'))"

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

function Assert-True {
    param([bool]$Condition, [string]$Because)
    if (-not $Condition) { throw $Because }
}

function Assert-Equal {
    param($Expected, $Actual, [string]$Because)
    if ($Expected -ne $Actual) { throw "$Because`n          expected: <$Expected>`n          actual  : <$Actual>" }
}

function New-Tree {
    param([string]$Name, [string[]]$Components = @("service", "companion"))
    $root = Join-Path $script:Sandbox $Name
    foreach ($component in $Components) {
        New-Item -ItemType Directory -Force -Path (Join-Path $root $component) | Out-Null
        Set-Content -LiteralPath (Join-Path $root "$component\appsettings.json") -Value '{}' -Encoding ASCII
    }
    return $root
}

function Move-ToPrevious {
    <#  Stage the "interrupted mid-swap" state for N components.  #>
    param([string]$Root, [string[]]$Components)
    $previous = Join-Path $Root ".previous"
    New-Item -ItemType Directory -Force -Path $previous | Out-Null
    foreach ($component in $Components) {
        Move-Item -LiteralPath (Join-Path $Root $component) -Destination (Join-Path $previous $component)
    }
}

New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null

try {
    Write-Host ""
    Write-Host "the engine under test"

    Test-Case "this really is Windows PowerShell 5.1" {
        Assert-Equal -Expected 5 -Actual $PSVersionTable.PSVersion.Major `
            -Because "these are 5.1 compatibility tests; passing them on PowerShell 7 would prove nothing"
        Assert-Equal -Expected "Desktop" -Actual $PSVersionTable.PSEdition -Because "the installer runs on Windows PowerShell"
    }

    Test-Case "StrictMode Latest is actually in force" {
        # If it were not, every assertion below would pass vacuously.
        $threw = $false
        try {
            $nothing = $null
            $null = $nothing.Count
        }
        catch {
            $threw = ($_.FullyQualifiedErrorId -match "PropertyNotFoundStrict")
        }
        Assert-True -Condition $threw -Because "StrictMode Latest must be on, or these tests cannot detect the defect"
    }

    Write-Host ""
    Write-Host "Resume-InterruptedDeployment: 0, 1 and many"

    Test-Case "nothing to restore: @(Call) yields an empty array, and .Count works" {
        $root = New-Tree -Name "resume-zero"
        $restored = @(Resume-InterruptedDeployment -Root $root -Components @("service", "companion"))

        Assert-True -Condition ($null -ne $restored) -Because "the wrapped call must never be `$null"
        Assert-True -Condition ($restored -is [array]) -Because "the caller indexes and counts this"
        Assert-Equal -Expected 0 -Actual $restored.Count -Because "reading .Count is what the installer does"
        # Exactly one level of array: the earlier fix combined ,@() with @(Call) and nested
        # them, so every count silently read 1.
        Assert-True -Condition (@($restored | Where-Object { $_ -is [array] }).Count -eq 0) `
            -Because "the result must not contain a nested array"
    }

    Test-Case "exactly one restored: a one-element array, not a bare string" {
        $root = New-Tree -Name "resume-one"
        Move-ToPrevious -Root $root -Components @("service")

        $restored = @(Resume-InterruptedDeployment -Root $root -Components @("service", "companion"))

        Assert-True -Condition ($restored -is [array]) -Because "a single item must not arrive as System.String"
        Assert-Equal -Expected 1 -Actual $restored.Count -Because "one component was mid-swap"
        Assert-Equal -Expected "service" -Actual $restored[0] -Because "and it should be named"
    }

    Test-Case "two restored returns both" {
        $root = New-Tree -Name "resume-many"
        Move-ToPrevious -Root $root -Components @("service", "companion")

        $restored = @(Resume-InterruptedDeployment -Root $root -Components @("service", "companion"))

        Assert-Equal -Expected 2 -Actual $restored.Count -Because "both components were mid-swap"
        Assert-True -Condition (Test-Path (Join-Path $root "service")) -Because "service must be back"
        Assert-True -Condition (Test-Path (Join-Path $root "companion")) -Because "companion must be back"
    }

    Write-Host ""
    Write-Host "Invoke-InstallRecovery: the installer's own pre-staging sequence"

    Test-Case "recovery on a clean tree reports arrays and no restores" {
        $root = New-Tree -Name "recovery-clean"
        $recovery = Invoke-InstallRecovery -Root $root -Components @("service", "companion") -Quiet

        Assert-Equal -Expected 0 -Actual $recovery.Restored.Count -Because "nothing was mid-swap"
        Assert-True -Condition ($recovery.Messages -is [array]) -Because "the installer iterates Messages"
        Assert-True -Condition ($recovery.Actions -is [array]) -Because "the installer joins Actions"
        Assert-Equal -Expected 0 -Actual $recovery.Messages.Count -Because "a clean tree needs no announcement"
    }

    Test-Case "recovery on a tree with one interrupted component reports it" {
        $root = New-Tree -Name "recovery-one"
        Move-ToPrevious -Root $root -Components @("service")

        $recovery = Invoke-InstallRecovery -Root $root -Components @("service", "companion") -Quiet

        Assert-Equal -Expected 1 -Actual $recovery.Restored.Count -Because "one component was mid-swap"
        Assert-True -Condition (@($recovery.Messages | Where-Object { $_ -match "restored service" }).Count -eq 1) `
            -Because "the owner should be told: $($recovery.Messages -join ' | ')"
    }

    Test-Case "recovery creates the root when there is no install at all" {
        $root = Join-Path $script:Sandbox "recovery-absent"
        Assert-True -Condition (-not (Test-Path $root)) -Because "the fixture must start empty"

        $recovery = Invoke-InstallRecovery -Root $root -Components @("service", "companion") -Quiet

        Assert-True -Condition (Test-Path $root) -Because "a first install must not need the directory to exist"
        Assert-Equal -Expected 0 -Actual $recovery.Restored.Count -Because "nothing to restore"
    }

    Test-Case "recovery of a hardened tree with empty DACLs reports what it did" {
        $root = New-Tree -Name "recovery-hardened"
        Set-HardenedAcl -Root $root
        $config = Join-Path $root "service\appsettings.json"
        $acl = Get-SecurityDescriptor -Path $config
        $acl.SetAccessRuleProtection($true, $false)
        foreach ($ace in @($acl.GetAccessRules($true, $false, [System.Security.Principal.SecurityIdentifier]))) {
            [void]$acl.RemoveAccessRuleSpecific($ace)
        }
        Set-SecurityDescriptor -Path $config -Security $acl

        $recovery = Invoke-InstallRecovery -Root $root -Components @("service", "companion") -Quiet

        Assert-True -Condition $recovery.Repaired -Because "the empty DACL had to be repaired"
        Assert-True -Condition ($recovery.Actions.Count -ge 1) -Because "the repair should say what it did"
        Assert-True -Condition ((Get-AclReport -Path $config).AceCount -gt 0) -Because "and the file must be usable again"
    }

    Write-Host ""
    Write-Host "other collection-returning functions at every cardinality"

    Test-Case "Test-InstallAclPosture violations: none, and several" {
        $clean = New-Tree -Name "posture-zero"
        Set-HardenedAcl -Root $clean
        $ok = Test-InstallAclPosture -Root $clean
        Assert-Equal -Expected 0 -Actual $ok.Violations.Count -Because "a clean tree has no violations, and .Count must still work"
        Assert-True -Condition ($ok.Violations -is [array]) -Because "callers count and slice this"

        $broken = New-Tree -Name "posture-many"
        $bad = Test-InstallAclPosture -Root $broken
        Assert-True -Condition ($bad.Violations.Count -gt 0) -Because "an unhardened tree grants write to others"
        Assert-True -Condition ($bad.Violations -is [array]) -Because "and it must still be an array"
    }

    Test-Case "Repair-InstallTreeAcl actions: nothing to do, and something to do" {
        $clean = New-Tree -Name "repair-none"
        $nothing = Repair-InstallTreeAcl -Root $clean -Quiet
        Assert-True -Condition ($nothing.Actions -is [array]) -Because "Actions is joined by the caller"
        Assert-Equal -Expected 0 -Actual $nothing.Actions.Count `
            -Because "a healthy tree must report NO repair, or 'Repaired' means nothing"
        Assert-Equal -Expected $false -Actual $nothing.Repaired -Because "and Repaired must be false"

        $dirty = New-Tree -Name "repair-some"
        $file = Get-Item -LiteralPath (Join-Path $dirty "service\appsettings.json")
        $file.Attributes = $file.Attributes -bor [System.IO.FileAttributes]::ReadOnly
        $some = Repair-InstallTreeAcl -Root $dirty -Quiet
        Assert-True -Condition ($some.Actions.Count -ge 1) -Because "clearing read-only is an action worth reporting"
    }

    Test-Case "Get-AclReport aces: a path that does not exist, and one that does" {
        $missing = Get-AclReport -Path (Join-Path $script:Sandbox "no-such-path")
        Assert-Equal -Expected $false -Actual $missing.Exists -Because "a missing path must report cleanly"
        Assert-Equal -Expected 0 -Actual $missing.AceCount -Because "and .Count-style reads must not throw"
        Assert-True -Condition ($missing.Aces -is [array]) -Because "Aces is enumerated by callers"

        $root = New-Tree -Name "acl-report"
        $present = Get-AclReport -Path $root
        Assert-True -Condition ($present.AceCount -gt 0) -Because "a real directory has ACEs"
    }

    Test-Case "sc.exe argument builders return arrays at every shape" {
        $withArgs = New-ScCreateArgumentList -ServiceName "S" -BinaryPathValue '"C:\a b\x.exe" run'
        Assert-True -Condition ($withArgs -is [array]) -Because "the argument list is indexed and counted"
        Assert-Equal -Expected 8 -Actual $withArgs.Count -Because "create + name + 3 key/value pairs"

        $failure = New-ScFailureArgumentList -ServiceName "S"
        Assert-True -Condition ($failure -is [array]) -Because "same for the failure-action list"

        $noArgs = New-ScBinaryPathValue -ExecutablePath "C:\a b\x.exe"
        Assert-Equal -Expected "C:\a b\x.exe" -Actual $noArgs -Because "no service arguments means a bare path"
        $empty = New-ScBinaryPathValue -ExecutablePath "C:\a b\x.exe" -ServiceArguments @()
        Assert-Equal -Expected "C:\a b\x.exe" -Actual $empty -Because "an empty argument array must behave like none"
    }

    Test-Case "Get-ServiceInstallPlan differences: none and several" {
        $same = Get-ServiceInstallPlan -Current ([pscustomobject]@{
            Name = "S"; PathName = '"C:\x.exe" run'; StartName = "LocalSystem"; StartMode = "Auto"; State = "Running"; ProcessId = 1
        }) -ExpectedPathName '"C:\x.exe" run'
        Assert-Equal -Expected "AlreadyCorrect" -Actual $same.Action -Because "no differences must not throw on an empty list"

        $different = Get-ServiceInstallPlan -Current ([pscustomobject]@{
            Name = "S"; PathName = '"C:\old.exe" run'; StartName = "NT AUTHORITY\NetworkService"; StartMode = "Manual"; State = "Stopped"; ProcessId = 0
        }) -ExpectedPathName '"C:\x.exe" run'
        Assert-Equal -Expected "Reconfigure" -Actual $different.Action -Because "three differences at once"
    }

    Write-Host ""
    Write-Host "static lint: the pattern itself, across the installer scripts"

    Test-Case "no bare `$x.Count anywhere in the installer scripts" {
        # Mechanical, so a future edit cannot reintroduce the class. Every .Count must be
        # written @(...).Count, which is correct for `$null, a scalar and an array alike.
        $scripts = @(
            "scripts\install-device-service.ps1",
            "scripts\uninstall-device-service.ps1",
            "scripts\verify-device-service.ps1",
            "scripts\lib\InstallAcl.ps1",
            "scripts\lib\NativeProcess.ps1",
            "scripts\lib\ServiceInstall.ps1"
        )

        # Comments are excluded through the PowerShell parser rather than by guessing at '#'
        # — the documentation in these files quotes the bug itself inside <# #> blocks, and a
        # line-prefix heuristic flagged that prose as code.
        $offenders = New-Object System.Collections.ArrayList
        foreach ($relative in $scripts) {
            $path = Join-Path $repoRoot $relative
            $tokens = $null
            $errors = $null
            [void][System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
            Assert-True -Condition (@($errors).Count -eq 0) -Because "$relative does not parse"

            foreach ($token in @($tokens)) {
                if ($token.Kind -eq [System.Management.Automation.Language.TokenKind]::Comment) { continue }
                if ($token.Kind -ne [System.Management.Automation.Language.TokenKind]::Identifier) { continue }
                if ($token.Text -ne "Count") { continue }

                # The token before an identifier member access is the '.' operator; the one
                # before that tells us whether the expression was parenthesised, i.e. @(...).
                $index = [array]::IndexOf($tokens, $token)
                $before = if ($index -ge 2) { $tokens[$index - 2] } else { $null }
                if ($null -eq $before -or $before.Kind -ne [System.Management.Automation.Language.TokenKind]::RParen) {
                    $line = $token.Extent.StartLineNumber
                    [void]$offenders.Add("$relative`:$line`: $($token.Extent.StartScriptPosition.Line.Trim())")
                }
            }
        }

        Assert-Equal -Expected 0 -Actual $offenders.Count `
            -Because "these read .Count without @( ) and will throw on `$null or a scalar:`n          $($offenders -join "`n          ")"
    }

    Test-Case "every collection-returning library function is unrolling-safe" {
        # Belt and braces for the lint above: call each one in its empty case and read .Count
        # exactly as the installer does. If any regains the unrolling behaviour, this fails.
        $root = New-Tree -Name "unroll-safety"

        $checks = @(
            @{ Name = "Resume-InterruptedDeployment"; Value = @(Resume-InterruptedDeployment -Root $root -Components @("service", "companion")) },
            @{ Name = "Invoke-InstallRecovery.Restored"; Value = (Invoke-InstallRecovery -Root $root -Components @("service") -Quiet).Restored },
            @{ Name = "Invoke-InstallRecovery.Messages"; Value = (Invoke-InstallRecovery -Root $root -Components @("service") -Quiet).Messages },
            @{ Name = "Repair-InstallTreeAcl.Actions"; Value = (Repair-InstallTreeAcl -Root $root -Quiet).Actions },
            @{ Name = "Test-InstallAclPosture.Violations"; Value = (Test-InstallAclPosture -Root $root).Violations }
        )

        foreach ($check in $checks) {
            Assert-True -Condition ($null -ne $check.Value) -Because "$($check.Name) returned `$null instead of an empty array"
            Assert-True -Condition ($check.Value -is [array]) -Because "$($check.Name) did not return an array"
            $null = $check.Value.Count   # the read that failed on the owner's machine
            Assert-True -Condition (@($check.Value | Where-Object { $_ -is [array] }).Count -eq 0) `
                -Because "$($check.Name) contains a nested array, so its count is wrong"
        }
    }
}
finally {
    if (Test-Path -LiteralPath $script:Sandbox) {
        Get-ChildItem -LiteralPath $script:Sandbox -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            try { [void](Repair-InstallTreeAcl -Root $_.FullName -Quiet) } catch { }
        }
        Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "$($script:Passes) passed, $($script:Failures) failed"
exit $script:Failures
