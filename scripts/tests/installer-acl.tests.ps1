<#
.SYNOPSIS
    Tests for the installer's ACL posture, staging/deploy model and partial-install recovery.

.DESCRIPTION
    A real install left C:\Program Files\PagentOS\agent in a state the installer could not
    recover from: hardening with `/T` and (OI)(CI) grants stripped every existing FILE to a
    protected, empty DACL — 219 of them — which denies everyone including SYSTEM and
    Administrators. Rewriting appsettings.json then failed with access denied even elevated,
    and the service would not have started either.

    These tests reproduce that faithfully in a temporary tree. That works because the failure
    is about ACLs, not about elevation: the test process OWNS the directories it creates, and
    an owner always keeps READ_CONTROL and WRITE_DAC — exactly the lever the repair uses on
    the real machine, where the owner is BUILTIN\Administrators. After hardening a temp tree,
    the non-elevated test account really does lose write access to it, so "can the installer
    recover from its own hardened state" is a genuine question here and not a simulated one.

    Nothing here touches C:\Program Files, requires elevation, or weakens a posture to pass.

    Run: powershell -NoProfile -File scripts\tests\installer-acl.tests.ps1
    Exit code is the number of failed assertions.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\NativeProcess.ps1")
. (Join-Path $repoRoot "scripts\lib\InstallAcl.ps1")

$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-acl-tests-$([guid]::NewGuid().ToString('N'))"

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

function New-TestRoot {
    <#  A fresh install root with the shape the installer produces.  #>
    param([string]$Name)
    $root = Join-Path $script:Sandbox $Name
    New-Item -ItemType Directory -Force -Path (Join-Path $root "service") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $root "companion") | Out-Null
    Set-Content -LiteralPath (Join-Path $root "service\PagentOS.DeviceService.exe") -Value "binary" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $root "service\appsettings.json") -Value '{"DataDir":"old"}' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $root "companion\PagentOS.SessionCompanion.exe") -Value "binary" -Encoding ASCII
    return $root
}

function Set-EmptyProtectedDacl {
    <#
    .SYNOPSIS
        Reproduce the exact damage: a protected DACL with no ACEs, denying everyone.
    #>
    param([string]$Path)
    # Through the same owner-and-DACL-only helpers the module uses: asking for the SACL
    # needs SeSecurityPrivilege, which no part of this installer should want.
    $acl = Get-SecurityDescriptor -Path $Path
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($ace in @($acl.GetAccessRules($true, $false, [System.Security.Principal.SecurityIdentifier]))) {
        [void]$acl.RemoveAccessRuleSpecific($ace)
    }
    Set-SecurityDescriptor -Path $Path -Security $acl
}

function Add-AceForTest {
    <#  Grant a principal rights on a path, for building "wrong ACL" fixtures.  #>
    param([string]$Path, [string]$Sid, [System.Security.AccessControl.FileSystemRights]$Rights)
    $acl = Get-SecurityDescriptor -Path $Path
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        (New-Object System.Security.Principal.SecurityIdentifier($Sid)),
        $Rights,
        ([System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit),
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow)))
    Set-SecurityDescriptor -Path $Path -Security $acl
}

function Test-CanWrite {
    <#  Can this process actually replace the file? The only question that matters.  #>
    param([string]$Path)
    try {
        $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        $stream.Dispose()
        return $true
    }
    catch {
        return $false
    }
}

New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null

try {
    Write-Host ""
    Write-Host "clean first install"

    Test-Case "hardening a fresh tree gives files INHERITED aces, never an empty DACL" {
        $root = New-TestRoot -Name "clean"
        Set-HardenedAcl -Root $root

        $rootReport = Get-AclReport -Path $root
        Assert-True -Condition $rootReport.IsProtected -Because "the root DACL must be protected"
        Assert-Equal -Expected 3 -Actual $rootReport.AceCount -Because "SYSTEM, Administrators, Users - and nothing else"

        $file = Get-AclReport -Path (Join-Path $root "service\appsettings.json")
        Assert-True -Condition $file.Readable -Because "the file's DACL must be readable"
        Assert-True -Condition ($file.AceCount -gt 0) -Because "THE original bug: the file was left with no ACEs at all"
        Assert-True -Condition (-not $file.IsProtected) -Because "children must inherit, not carry protected explicit ACEs"
        Assert-True -Condition (@($file.Aces | Where-Object { $_.IsInherited }).Count -gt 0) -Because "the ACEs must come from the root by inheritance"
    }

    Test-Case "the posture check passes on a correctly hardened tree" {
        $root = New-TestRoot -Name "posture-ok"
        Set-HardenedAcl -Root $root
        $posture = Test-InstallAclPosture -Root $root
        Assert-True -Condition $posture.Ok -Because "violations: $($posture.Violations -join '; ')"
        Assert-True -Condition ($posture.Checked -ge 5) -Because "the check must actually visit the tree"
    }

    Test-Case "no unprivileged principal keeps write authority" {
        $root = New-TestRoot -Name "no-user-write"
        Set-HardenedAcl -Root $root
        foreach ($path in @($root, (Join-Path $root "service"), (Join-Path $root "service\appsettings.json"))) {
            foreach ($ace in (Get-AclReport -Path $path).Aces) {
                $sid = Resolve-SidValue -Identity $ace.IdentityReference
                if ($sid -in @("S-1-5-32-545", "S-1-5-11", "S-1-1-0", "S-1-5-4")) {
                    $writes = ([int]$ace.FileSystemRights -band [int]([System.Security.AccessControl.FileSystemRights]::WriteData -bor
                        [System.Security.AccessControl.FileSystemRights]::AppendData -bor
                        [System.Security.AccessControl.FileSystemRights]::Delete -bor
                        [System.Security.AccessControl.FileSystemRights]::ChangePermissions -bor
                        [System.Security.AccessControl.FileSystemRights]::TakeOwnership))
                    Assert-Equal -Expected 0 -Actual $writes -Because "$($ace.IdentityReference) must not hold write authority on $path"
                }
            }
        }
    }

    Write-Host ""
    Write-Host "rerun after a successful install"

    Test-Case "hardening twice is idempotent and leaves the same posture" {
        $root = New-TestRoot -Name "rerun-ok"
        Set-HardenedAcl -Root $root
        $first = (Get-AclReport -Path $root).Sddl
        Set-HardenedAcl -Root $root
        $second = (Get-AclReport -Path $root).Sddl
        Assert-Equal -Expected $first -Actual $second -Because "a rerun must not drift the ACL"
        Assert-True -Condition (Test-InstallAclPosture -Root $root).Ok -Because "posture must still hold"
    }

    Write-Host ""
    Write-Host "rerun after failure immediately after ACL hardening (the real-machine defect)"

    Test-Case "an existing file with an empty protected DACL cannot be written - reproduced" {
        $root = New-TestRoot -Name "repro"
        $config = Join-Path $root "service\appsettings.json"
        Set-EmptyProtectedDacl -Path $config
        Assert-True -Condition (-not (Test-CanWrite -Path $config)) `
            -Because "this is the exact failure the owner hit: access denied replacing appsettings.json"
    }

    Test-Case "the repair makes an empty-DACL file writable again, with no manual ACL reset" {
        # Unhardened root, so the inherited ACEs are the temp directory's own — which grant
        # this account write. That makes "is it writable again" a real question here, and it
        # is the same mechanism as on the real machine, where the inherited ACEs grant
        # Administrators and the installer runs elevated.
        $root = New-TestRoot -Name "repair-writable"
        foreach ($name in @("service\appsettings.json", "service\PagentOS.DeviceService.exe")) {
            Set-EmptyProtectedDacl -Path (Join-Path $root $name)
        }
        Assert-True -Condition (-not (Test-CanWrite -Path (Join-Path $root "service\appsettings.json"))) `
            -Because "the tree must really be broken first"

        $repair = Repair-InstallTreeAcl -Root $root -Quiet
        Assert-True -Condition $repair.Repaired -Because "the repair should report that it did something"

        foreach ($name in @("service\appsettings.json", "service\PagentOS.DeviceService.exe")) {
            $report = Get-AclReport -Path (Join-Path $root $name)
            Assert-True -Condition ($report.AceCount -gt 0) -Because "$name still has an empty DACL after repair"
            Assert-True -Condition (Test-CanWrite -Path (Join-Path $root $name)) -Because "$name is still not writable after repair"
        }
    }

    Test-Case "the repair restores administrability of a HARDENED tree with empty DACLs" {
        # The real-machine shape: hardened root, files stripped to empty DACLs. This test
        # account is not an administrator, so it will still not be able to write afterwards —
        # correctly. What must be true is that SYSTEM and Administrators can again, which is
        # what the elevated installer needs and what the empty DACL had denied them.
        $root = New-TestRoot -Name "repair-hardened"
        Set-HardenedAcl -Root $root
        foreach ($name in @("service\appsettings.json", "service\PagentOS.DeviceService.exe", "companion\PagentOS.SessionCompanion.exe")) {
            Set-EmptyProtectedDacl -Path (Join-Path $root $name)
        }
        Assert-True -Condition (-not (Test-InstallAclPosture -Root $root).Ok) -Because "the broken tree must fail the posture check first"

        [void](Repair-InstallTreeAcl -Root $root -Quiet)

        foreach ($name in @("service\appsettings.json", "service\PagentOS.DeviceService.exe", "companion\PagentOS.SessionCompanion.exe")) {
            $report = Get-AclReport -Path (Join-Path $root $name)
            Assert-True -Condition ($report.AceCount -gt 0) -Because "$name still has an empty DACL after repair"
            $sids = @($report.Aces | ForEach-Object { Resolve-SidValue -Identity $_.IdentityReference })
            Assert-True -Condition ($sids -contains "S-1-5-18") -Because "SYSTEM must be able to read $name or the service cannot start"
            Assert-True -Condition ($sids -contains "S-1-5-32-544") -Because "Administrators must be able to update $name"
        }
        Assert-True -Condition (Test-InstallAclPosture -Root $root).Ok -Because "and the tree must be back to the intended posture"
    }

    Test-Case "repair does not widen rights: the posture still holds afterwards" {
        $root = New-TestRoot -Name "repair-posture"
        Set-HardenedAcl -Root $root
        Set-EmptyProtectedDacl -Path (Join-Path $root "service\appsettings.json")
        [void](Repair-InstallTreeAcl -Root $root -Quiet)
        $posture = Test-InstallAclPosture -Root $root
        Assert-True -Condition $posture.Ok -Because "repair must restore access without granting anyone new rights: $($posture.Violations -join '; ')"
    }

    Write-Host ""
    Write-Host "existing configuration files"

    Test-Case "an existing appsettings.json is replaced, not appended to" {
        $root = New-TestRoot -Name "existing-config"
        $config = Join-Path $root "service\appsettings.json"
        Write-JsonFile -Path $config -Content '{"DataDir":"new"}'
        Assert-Equal -Expected '{"DataDir":"new"}' -Actual ([System.IO.File]::ReadAllText($config)) -Because "the file must be replaced wholesale"
    }

    Test-Case "a read-only config file is replaced rather than failing the install" {
        $root = New-TestRoot -Name "readonly-config"
        $config = Join-Path $root "service\appsettings.json"
        $item = Get-Item -LiteralPath $config
        $item.Attributes = $item.Attributes -bor [System.IO.FileAttributes]::ReadOnly
        Assert-True -Condition ((Get-AclReport -Path $config).IsReadOnly) -Because "the fixture must really be read-only"

        Write-JsonFile -Path $config -Content '{"DataDir":"replaced"}'
        Assert-Equal -Expected '{"DataDir":"replaced"}' -Actual ([System.IO.File]::ReadAllText($config)) -Because "a read-only attribute is not a permission decision"
    }

    Test-Case "the repair clears read-only attributes left by an interrupted run" {
        $root = New-TestRoot -Name "readonly-repair"
        $config = Join-Path $root "service\appsettings.json"
        $item = Get-Item -LiteralPath $config
        $item.Attributes = $item.Attributes -bor [System.IO.FileAttributes]::ReadOnly

        $repair = Repair-InstallTreeAcl -Root $root -Quiet
        Assert-True -Condition (-not (Get-AclReport -Path $config).IsReadOnly) -Because "read-only should be cleared: $($repair.Actions -join '; ')"
    }

    Write-Host ""
    Write-Host "wrong ACL"

    Test-Case "a tree granting Users write is reported as a violation" {
        $root = New-TestRoot -Name "wrong-acl"
        Set-HardenedAcl -Root $root
        Add-AceForTest -Path $root -Sid "S-1-5-32-545" -Rights ([System.Security.AccessControl.FileSystemRights]::Modify)

        $posture = Test-InstallAclPosture -Root $root
        Assert-True -Condition (-not $posture.Ok) -Because "Users with Modify must be caught"
        Assert-True -Condition (@($posture.Violations | Where-Object { $_ -match "write authority" }).Count -gt 0) `
            -Because "the violation should name write authority: $($posture.Violations -join '; ')"
    }

    Test-Case "re-hardening removes an unintended grant" {
        $root = New-TestRoot -Name "wrong-acl-fix"
        Set-HardenedAcl -Root $root
        Add-AceForTest -Path $root -Sid "S-1-1-0" -Rights ([System.Security.AccessControl.FileSystemRights]::FullControl)
        Assert-True -Condition (-not (Test-InstallAclPosture -Root $root).Ok) -Because "Everyone:F must be a violation"

        Set-HardenedAcl -Root $root
        Assert-True -Condition (Test-InstallAclPosture -Root $root).Ok -Because "hardening must replace the DACL, not merge into it"
    }

    Test-Case "an empty DACL anywhere in the tree fails the posture check" {
        $root = New-TestRoot -Name "empty-dacl-posture"
        Set-HardenedAcl -Root $root
        Set-EmptyProtectedDacl -Path (Join-Path $root "service\appsettings.json")
        $posture = Test-InstallAclPosture -Root $root
        Assert-True -Condition (-not $posture.Ok) -Because "an empty DACL denies SYSTEM and must never pass as installed"
    }

    Write-Host ""
    Write-Host "staging and interrupted installs"

    Test-Case "a staged directory is swapped in atomically" {
        $root = New-TestRoot -Name "deploy"
        $staged = New-StagingDirectory -Root $root -Name "service"
        Set-Content -LiteralPath (Join-Path $staged "PagentOS.DeviceService.exe") -Value "new binary" -Encoding ASCII
        Set-Content -LiteralPath (Join-Path $staged "appsettings.json") -Value '{"DataDir":"staged"}' -Encoding ASCII

        Publish-StagedDirectory -Root $root -Component "service" -StagedPath $staged | Out-Null

        Assert-Equal -Expected "new binary" -Actual ([System.IO.File]::ReadAllText((Join-Path $root "service\PagentOS.DeviceService.exe")).Trim()) `
            -Because "the live tree must now be the staged content"
        Assert-True -Condition (-not (Test-Path (Join-Path $root ".previous\service"))) -Because "the backup is removed once the swap succeeds"
    }

    Test-Case "deploying over a hardened live tree replaces config, and hardening drops the installer's own grant" {
        # The scenario that failed on the real machine, end to end. The installer runs as an
        # administrator there; this test account is not one, so it stands in for that
        # identity with an explicit FullControl ACE. That makes the test stricter, not
        # weaker: the final assertion is that hardening REMOVES that stand-in grant, so the
        # installed tree does not keep write authority for whoever installed it.
        $root = New-TestRoot -Name "deploy-hardened"
        Set-HardenedAcl -Root $root

        $installer = ([System.Security.Principal.WindowsIdentity]::GetCurrent()).User
        Add-AceForTest -Path $root -Sid $installer.Value -Rights ([System.Security.AccessControl.FileSystemRights]::FullControl)
        [void](Repair-InstallTreeAcl -Root $root -Quiet)

        $staged = New-StagingDirectory -Root $root -Name "service"
        Set-Content -LiteralPath (Join-Path $staged "PagentOS.DeviceService.exe") -Value "v2" -Encoding ASCII
        Write-JsonFile -Path (Join-Path $staged "appsettings.json") -Content '{"DataDir":"v2"}'

        Publish-StagedDirectory -Root $root -Component "service" -StagedPath $staged | Out-Null
        Set-HardenedAcl -Root $root

        Assert-Equal -Expected '{"DataDir":"v2"}' -Actual ([System.IO.File]::ReadAllText((Join-Path $root "service\appsettings.json"))) `
            -Because "a rerun must be able to replace configuration in a hardened install"

        $posture = Test-InstallAclPosture -Root $root
        Assert-True -Condition $posture.Ok -Because "the install must end hardened: $($posture.Violations -join '; ')"
        $rootSids = @((Get-AclReport -Path $root).Aces | ForEach-Object { Resolve-SidValue -Identity $_.IdentityReference })
        Assert-True -Condition ($rootSids -notcontains $installer.Value) `
            -Because "hardening must drop the installing identity's own grant, not preserve it"
    }

    Test-Case "an install interrupted mid-swap is restored to the last working state" {
        $root = New-TestRoot -Name "interrupted"
        # Simulate the interruption: live moved aside, staging not yet moved in.
        $previous = Join-Path $root ".previous"
        New-Item -ItemType Directory -Force -Path $previous | Out-Null
        Move-Item -LiteralPath (Join-Path $root "service") -Destination (Join-Path $previous "service")
        New-Item -ItemType Directory -Force -Path (Join-Path $root ".staging\service") | Out-Null
        Assert-True -Condition (-not (Test-Path (Join-Path $root "service"))) -Because "the fixture must really be mid-swap"

        $restored = Resume-InterruptedDeployment -Root $root -Components @("service", "companion")

        Assert-True -Condition ($restored -contains "service") -Because "the interrupted component should be reported"
        Assert-True -Condition (Test-Path (Join-Path $root "service\appsettings.json")) -Because "the previous install must be back in place"
        Assert-True -Condition (-not (Test-Path (Join-Path $root ".staging"))) -Because "stale staging must be cleared"
    }

    Test-Case "a completed swap with interrupted cleanup just tidies up" {
        $root = New-TestRoot -Name "interrupted-cleanup"
        $previous = Join-Path $root ".previous"
        New-Item -ItemType Directory -Force -Path (Join-Path $previous "service") | Out-Null
        Set-Content -LiteralPath (Join-Path $previous "service\stale.txt") -Value "old" -Encoding ASCII

        [void](Resume-InterruptedDeployment -Root $root -Components @("service", "companion"))

        Assert-True -Condition (Test-Path (Join-Path $root "service\appsettings.json")) -Because "the live tree must be untouched"
        Assert-True -Condition (-not (Test-Path (Join-Path $previous "service"))) -Because "the leftover backup must be removed"
    }

    Test-Case "a failed swap leaves the previous install in place" {
        $root = New-TestRoot -Name "rollback"
        $marker = Join-Path $root "service\appsettings.json"
        Write-JsonFile -Path $marker -Content '{"DataDir":"original"}'

        # Staging that does not exist: the deploy must refuse before it moves anything aside.
        try {
            Publish-StagedDirectory -Root $root -Component "service" -StagedPath (Join-Path $root ".staging\nonexistent") | Out-Null
            throw "the deploy should have refused"
        }
        catch {
            Assert-True -Condition ($_.Exception.Message -match "nothing staged") -Because "the refusal should say what was missing"
        }

        Assert-Equal -Expected '{"DataDir":"original"}' -Actual ([System.IO.File]::ReadAllText($marker)) `
            -Because "the live install must be exactly as it was"
    }
}
finally {
    # Test trees are hardened against the test account, so ownership is what makes cleanup
    # possible - the same property the repair relies on.
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
