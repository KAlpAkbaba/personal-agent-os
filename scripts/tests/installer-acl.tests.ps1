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
. (Join-Path $repoRoot "scripts\lib\BrowserProvision.ps1")

$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-acl-tests-$([guid]::NewGuid().ToString('N'))"
$script:LockedForCleanup = New-Object System.Collections.ArrayList

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

    Write-Host ""
    Write-Host "machine-state file recovery (the real state.json empty-DACL incident)"

    function New-MachineDataFixture {
        <#  A data dir shaped like C:\ProgramData\PagentOS\agent after the incident.  #>
        param([string]$Name)
        $dataDir = Join-Path $script:Sandbox $Name
        New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
        Set-Content -LiteralPath (Join-Path $dataDir "state.json") -Value '{"device_id":"real"}' -Encoding ASCII
        Set-Content -LiteralPath (Join-Path $dataDir "device.key") -Value "keymaterial" -Encoding ASCII
        return $dataDir
    }

    Test-Case "a state file with an empty protected DACL is repaired and readable again" {
        # The real failure: state.json rewritten by the service (inherited ACEs only), then
        # the pre-fix /T icacls stripped inheritance tree-wide -> protected EMPTY DACL that
        # denied even the elevated administrator running finalize-qualification.
        $dataDir = New-MachineDataFixture -Name "state-incident"
        $statePath = Join-Path $dataDir "state.json"
        Set-EmptyProtectedDacl -Path $statePath
        try { [void][System.IO.File]::ReadAllText($statePath); throw "damage was not reproduced" }
        catch [System.UnauthorizedAccessException] { }

        $repaired = @(Restore-MachineStateAcl -DataDir $dataDir)
        Assert-True -Condition ($repaired -contains "state.json") -Because "the damaged file must be reported as repaired"

        # Readability is a property of the caller's TOKEN: the finalize updater runs with an
        # elevated Administrators SID, this test may not. Assert the outcome each token earns.
        $tokenIsAdmin = ([System.Security.Principal.WindowsPrincipal]::new(
            [System.Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole(
            [System.Security.Principal.WindowsBuiltInRole]::Administrator)
        if ($tokenIsAdmin) {
            Assert-Equal -Expected '{"device_id":"real"}' -Actual ([System.IO.File]::ReadAllText($statePath).Trim()) `
                -Because "after repair the elevated updater's read (the finalize line that failed) must succeed"
        }
        else {
            $denied = $false
            try { [void][System.IO.File]::ReadAllText($statePath) } catch [System.UnauthorizedAccessException] { $denied = $true }
            Assert-True -Condition $denied -Because "a non-elevated caller must STILL be denied - repair is recovery, not a widening"
        }

        $acl = Get-SecurityDescriptor -Path $statePath
        $sids = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) | ForEach-Object { $_.IdentityReference.Value })
        Assert-True -Condition ($sids -contains "S-1-5-18") -Because "SYSTEM must be able to replace its own state"
        Assert-True -Condition ($sids -contains "S-1-5-32-544") -Because "Administrators carry the recovery read"
        foreach ($sid in $sids) {
            Assert-True -Condition ($sid -in @("S-1-5-18", "S-1-5-32-544")) -Because "repair must not widen access beyond SYSTEM+Administrators (got $sid)"
        }
    }

    Test-Case "device.key repair grants SYSTEM read-only, not full control" {
        $dataDir = New-MachineDataFixture -Name "key-incident"
        $keyPath = Join-Path $dataDir "device.key"
        Set-EmptyProtectedDacl -Path $keyPath
        [void](Restore-MachineStateAcl -DataDir $dataDir)
        $acl = Get-SecurityDescriptor -Path $keyPath
        $systemRules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) |
            Where-Object { $_.IdentityReference.Value -eq "S-1-5-18" })
        Assert-Equal -Expected 1 -Actual @($systemRules).Count -Because "exactly one SYSTEM ACE"
        Assert-True -Condition (-not ($systemRules[0].FileSystemRights.HasFlag([System.Security.AccessControl.FileSystemRights]::WriteData))) `
            -Because "the key stays read-only for SYSTEM - the narrower secret posture must not be flattened by repair"
    }

    Test-Case "healthy machine-state files are left untouched by the repair guard" {
        $dataDir = New-MachineDataFixture -Name "state-healthy"
        $statePath = Join-Path $dataDir "state.json"
        $before = (Get-SecurityDescriptor -Path $statePath).GetSecurityDescriptorSddlForm(
            [System.Security.AccessControl.AccessControlSections]::Access)
        $repaired = @(Restore-MachineStateAcl -DataDir $dataDir)
        Assert-Equal -Expected 0 -Actual @($repaired).Count -Because "nothing was damaged, nothing may be rewritten"
        $after = (Get-SecurityDescriptor -Path $statePath).GetSecurityDescriptorSddlForm(
            [System.Security.AccessControl.AccessControlSections]::Access)
        Assert-Equal -Expected $before -Actual $after -Because "the guard must be a no-op on healthy files"
    }

    Test-Case "the strict state reader returns content for a healthy file and null for a missing one" {
        $dataDir = New-MachineDataFixture -Name "reader-healthy"
        Assert-Equal -Expected '{"device_id":"real"}' -Actual ((Get-MachineStateDocument -DataDir $dataDir).Trim()) `
            -Because "a readable state document is returned as-is"
        Assert-True -Condition ($null -eq (Get-MachineStateDocument -DataDir $dataDir -Name "idempotency.json")) `
            -Because "genuinely absent means null, decided by directory listing, not an exception"
        Assert-True -Condition ($null -eq (Get-MachineStateDocument -DataDir (Join-Path $dataDir "no-such-dir"))) `
            -Because "a missing data dir is also 'not enrolled', not a crash"
    }

    Test-Case "the strict state reader never treats an access-denied file as absent" {
        # The hazard: Test-Path answers FALSE for a denied file, and 'not enrolled' leads to
        # re-enrollment. The reader must either repair-and-read or throw - never return null.
        $dataDir = New-MachineDataFixture -Name "reader-denied"
        Set-EmptyProtectedDacl -Path (Join-Path $dataDir "state.json")

        $tokenIsAdmin = ([System.Security.Principal.WindowsPrincipal]::new(
            [System.Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole(
            [System.Security.Principal.WindowsBuiltInRole]::Administrator)
        if ($tokenIsAdmin) {
            Assert-Equal -Expected '{"device_id":"real"}' -Actual ((Get-MachineStateDocument -DataDir $dataDir).Trim()) `
                -Because "elevated: the guard repairs and the read succeeds"
        }
        else {
            try {
                $result = Get-MachineStateDocument -DataDir $dataDir
                throw "returned <$result> instead of throwing - a denied file must never read as absent"
            }
            catch {
                Assert-True -Condition ($_.Exception.Message -match "ELEVATED") `
                    -Because "the refusal must say what to do, not just 'access denied' (got: $($_.Exception.Message))"
            }
        }
    }

    Test-Case "the strict state reader refuses to read key material" {
        $dataDir = New-MachineDataFixture -Name "reader-key"
        try {
            [void](Get-MachineStateDocument -DataDir $dataDir -Name "device.key")
            throw "device.key must never be readable through the state-document path"
        }
        catch {
            Assert-True -Condition ($_.Exception.Message -match "key material") -Because "the refusal names the boundary"
        }
    }

    Test-Case "repair is idempotent and survives a missing optional file" {
        # idempotency.json does not exist in this fixture; state.json is damaged twice.
        $dataDir = New-MachineDataFixture -Name "state-idempotent"
        Set-EmptyProtectedDacl -Path (Join-Path $dataDir "state.json")
        $first = @(Restore-MachineStateAcl -DataDir $dataDir)
        $second = @(Restore-MachineStateAcl -DataDir $dataDir)
        Assert-Equal -Expected 1 -Actual @($first).Count -Because "first pass repairs the one damaged file"
        Assert-Equal -Expected 0 -Actual @($second).Count -Because "second pass finds nothing to do"
    }

    Write-Host ""
    Write-Host "the companion's audit directory (2026-09-29: a healthy browser worker 0.5.0 rolled back twice)"

    # What happened. The Session Companion - the OWNER's non-elevated process - created
    # C:\ProgramData\PagentOS\companion\audit on its first start and protected it the way the
    # service protects its own: a protected DACL naming SYSTEM and Administrators only. It
    # could apply that (a creator owns what it creates) and could not write a row afterwards.
    # The installer granted the owner Modify on the companion's data ROOT, inheritable - and a
    # protected DACL does not inherit. So the health check waited 90 s for a
    # browser_worker_started row that could not be written, and rolled back.
    #
    # Reproduced for real below: this test account creates the directory, so it owns it
    # exactly as the owner's account does on the real machine, and after the old protection
    # it genuinely cannot write there.

    $script:CurrentSid = ([System.Security.Principal.WindowsIdentity]::GetCurrent()).User.Value
    $script:TokenIsAdmin = ([System.Security.Principal.WindowsPrincipal]::new(
        [System.Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole(
        [System.Security.Principal.WindowsBuiltInRole]::Administrator)

    function New-CompanionDataFixture {
        <#  C:\ProgramData\PagentOS\companion as the incident left it: audit\ locked, a trail inside.  #>
        param([string]$Name, [switch]$WithTrail)
        $companion = Join-Path $script:Sandbox "$Name\companion"
        $audit = Join-Path $companion "audit"
        New-Item -ItemType Directory -Force -Path $audit | Out-Null
        if ($WithTrail) {
            [System.IO.File]::WriteAllText((Join-Path $audit "companion-audit.jsonl"), "{`"event`":`"before`"}`n")
        }
        # The old AuditLog constructor's MachineMaterial.Protect(Directory), byte for byte.
        Set-MachineDataAcl -Root $audit
        [void]$script:LockedForCleanup.Add($audit)
        return $companion
    }

    function Test-CanAppendRow {
        <#  What the companion does for every audited action: open for append, write a line.  #>
        param([string]$AuditDir)
        try {
            $stream = New-Object System.IO.FileStream((Join-Path $AuditDir "companion-audit.jsonl"),
                [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite)
            try { $bytes = [System.Text.Encoding]::UTF8.GetBytes("{`"event`":`"browser_worker_started`"}`n"); $stream.Write($bytes, 0, $bytes.Length) }
            finally { $stream.Dispose() }
            return $true
        }
        catch { return $false }
    }

    function Get-AllowedRights {
        <#  Everything the DACL allows one SID on this path, explicit and inherited.  #>
        param([string]$Path, [string]$Sid)
        $rights = 0
        foreach ($ace in (Get-AclReport -Path $Path).Aces) {
            if ((Resolve-SidValue -Identity $ace.IdentityReference) -ne $Sid) { continue }
            if ($ace.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) { continue }
            $rights = $rights -bor [int]$ace.FileSystemRights
        }
        # .NET adds SYNCHRONIZE to every allow entry it writes; it is not a permission anyone
        # chose, so it is not part of what is compared (FullControl already contains it).
        if ($rights -ne [int][System.Security.AccessControl.FileSystemRights]::FullControl) {
            $rights = $rights -band (-bnot [int][System.Security.AccessControl.FileSystemRights]::Synchronize)
        }
        return $rights
    }

    $script:Modify = [int][System.Security.AccessControl.FileSystemRights]::Modify

    Test-Case "the incident, reproduced: the grant on the companion's data root does not reach a protected audit directory" {
        $companion = New-CompanionDataFixture -Name "audit-repro"
        $audit = Join-Path $companion "audit"
        Set-OwnerWritableDirectory -Path $companion -OwnerSid $script:CurrentSid   # all the installer used to do

        Assert-True -Condition (Test-OwnerWritableDirectory -Path $companion -OwnerSid $script:CurrentSid) -Because "the root really is granted"
        Assert-Equal -Expected 0 -Actual ((Get-AllowedRights -Path $audit -Sid $script:CurrentSid) -band $script:Modify) `
            -Because "and the audit directory, protected, inherits none of it"
        if (-not $script:TokenIsAdmin) {
            Assert-True -Condition (-not (Test-CanAppendRow -AuditDir $audit)) -Because "which is the row the health check waited 90 s for"
        }
    }

    Test-Case "a rerun of the installer's step repairs the locked audit directory, and the row can be written" {
        $companion = New-CompanionDataFixture -Name "audit-rerun" -WithTrail
        $audit = Join-Path $companion "audit"

        Initialize-CompanionDataDirectories -CompanionDataDir $companion -BrowserDataDir (Join-Path $companion "browser") -OwnerSid $script:CurrentSid | Out-Null

        Assert-Equal -Expected $script:Modify -Actual ((Get-AllowedRights -Path $audit -Sid $script:CurrentSid) -band $script:Modify) `
            -Because "the owner SID must hold Modify on the audit directory after a rerun"
        Assert-Equal -Expected $script:Modify -Actual ((Get-AllowedRights -Path (Join-Path $audit "companion-audit.jsonl") -Sid $script:CurrentSid) -band $script:Modify) `
            -Because "and on the trail that was already there"
        Assert-True -Condition (Test-CanAppendRow -AuditDir $audit) -Because "measured, not inferred: the append the companion makes must succeed"
        Assert-True -Condition ([System.IO.File]::ReadAllText((Join-Path $audit "companion-audit.jsonl")).StartsWith("{`"event`":`"before`"}")) `
            -Because "a repair touches the descriptor, never the rows already written"
    }

    Test-Case "the audit directory is repaired when the browser is skipped too - the companion audits without one" {
        $companion = New-CompanionDataFixture -Name "audit-skipbrowser"
        $audit = Join-Path $companion "audit"

        Initialize-CompanionDataDirectories -CompanionDataDir $companion -OwnerSid $script:CurrentSid | Out-Null

        Assert-True -Condition (Test-CanAppendRow -AuditDir $audit) -Because "-SkipBrowser must not leave the trail unwritable"
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $companion "browser"))) -Because "and must not create a browser directory nobody asked for"
    }

    Test-Case "a first install creates the audit directory: protected, SYSTEM and Administrators full, the owner Modify, nobody else" {
        $companion = Join-Path $script:Sandbox "audit-fresh\companion"
        $audit = Join-Path $companion "audit"
        [void]$script:LockedForCleanup.Add($audit)

        Initialize-CompanionDataDirectories -CompanionDataDir $companion -BrowserDataDir (Join-Path $companion "browser") -OwnerSid $script:CurrentSid | Out-Null

        $report = Get-AclReport -Path $audit
        Assert-True -Condition $report.Exists -Because "the installer creates it before the runtime starts"
        Assert-True -Condition $report.IsProtected -Because "the posture is stated on the directory, not borrowed from ProgramData (whose ACL lets every user create files)"
        Assert-Equal -Expected 3 -Actual $report.AceCount -Because "three principals: $($report.Sddl)"
        $full = [int][System.Security.AccessControl.FileSystemRights]::FullControl
        Assert-Equal -Expected $full -Actual (Get-AllowedRights -Path $audit -Sid "S-1-5-18") -Because "SYSTEM full control"
        Assert-Equal -Expected $full -Actual (Get-AllowedRights -Path $audit -Sid "S-1-5-32-544") -Because "Administrators full control"
        Assert-Equal -Expected $script:Modify -Actual (Get-AllowedRights -Path $audit -Sid $script:CurrentSid) `
            -Because "the owner gets Modify exactly: the process that writes the trail does not get to re-permission it"
        foreach ($ace in $report.Aces) {
            Assert-True -Condition ([bool]($ace.InheritanceFlags -band [System.Security.AccessControl.InheritanceFlags]::ObjectInherit)) `
                -Because "the audit FILE is created later and must inherit: $($ace.IdentityReference)"
        }
    }

    Test-Case "protection is by identity: the grant names the owner SID it was given, not whoever runs the installer" {
        # The installer runs elevated as an administrator and is told the owner's SID. A SID
        # that is not this account stands in for that: this account must NOT be granted.
        $someoneElse = "S-1-5-21-511977846-344100023-3014107856-999999"
        $companion = Join-Path $script:Sandbox "audit-identity\companion"
        $audit = Join-Path $companion "audit"
        [void]$script:LockedForCleanup.Add($audit)

        Initialize-CompanionDataDirectories -CompanionDataDir $companion -OwnerSid $someoneElse | Out-Null

        Assert-Equal -Expected $script:Modify -Actual (Get-AllowedRights -Path $audit -Sid $someoneElse) -Because "the named owner holds Modify"
        Assert-Equal -Expected 0 -Actual (Get-AllowedRights -Path $audit -Sid $script:CurrentSid) -Because "the installing account is not written into the audit directory's DACL"
        Assert-True -Condition (Get-CompanionAuditDirectoryReport -Path $audit -OwnerSid $someoneElse).OwnerCanWrite -Because "the report answers for the SID it is asked about"
        Assert-True -Condition (-not (Get-CompanionAuditDirectoryReport -Path $audit -OwnerSid $script:CurrentSid).OwnerCanWrite) -Because "and for no other"
    }

    Test-Case "the repair is idempotent: a second run changes nothing and says so" {
        $companion = New-CompanionDataFixture -Name "audit-idempotent"
        $audit = Join-Path $companion "audit"

        $first = Set-CompanionAuditDirectoryAcl -Path $audit -OwnerSid $script:CurrentSid
        $sddl = (Get-AclReport -Path $audit).Sddl
        $second = Set-CompanionAuditDirectoryAcl -Path $audit -OwnerSid $script:CurrentSid

        Assert-True -Condition $first.Changed -Because "the locked directory must be reported as repaired"
        Assert-True -Condition (-not $first.Created) -Because "it already existed"
        Assert-True -Condition (-not $second.Changed) -Because "a correct directory is left alone"
        Assert-Equal -Expected $sddl -Actual (Get-AclReport -Path $audit).Sddl -Because "a rerun must not drift the ACL"
    }

    Test-Case "a deny entry against the owner is not 'writable', whatever else is allowed" {
        $companion = Join-Path $script:Sandbox "audit-deny\companion"
        $audit = Join-Path $companion "audit"
        [void]$script:LockedForCleanup.Add($audit)
        [void](Set-CompanionAuditDirectoryAcl -Path $audit -OwnerSid $script:CurrentSid)
        $acl = Get-SecurityDescriptor -Path $audit
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            (New-Object System.Security.Principal.SecurityIdentifier($script:CurrentSid)),
            [System.Security.AccessControl.FileSystemRights]::WriteData,
            ([System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit),
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Deny)))
        Set-SecurityDescriptor -Path $audit -Security $acl

        $report = Get-CompanionAuditDirectoryReport -Path $audit -OwnerSid $script:CurrentSid
        Assert-True -Condition (-not $report.OwnerCanWrite) -Because "a deny wins over an allow"
        Assert-True -Condition (@($report.Problems | Where-Object { $_ -match "deny" }).Count -gt 0) -Because "and is named: $($report.Problems -join '; ')"

        [void](Set-CompanionAuditDirectoryAcl -Path $audit -OwnerSid $script:CurrentSid)
        Assert-True -Condition (Get-CompanionAuditDirectoryReport -Path $audit -OwnerSid $script:CurrentSid).OwnerCanWrite -Because "the repair replaces the DACL, deny entry included"
    }

    Test-Case "the verifier's row: PROVEN_REAL when the owner can write, NOT_YET_PROVEN with the remedy when not, or when absent" {
        $companion = New-CompanionDataFixture -Name "audit-verdict"
        $audit = Join-Path $companion "audit"

        $locked = Get-CompanionAuditDirectoryVerdict -Path $audit -OwnerSid $script:CurrentSid
        Assert-Equal -Expected "NOT_YET_PROVEN" -Actual $locked.Status -Because "the incident state must not pass"
        Assert-True -Condition ($locked.Evidence -match [regex]::Escape($script:CurrentSid)) -Because "the evidence names the SID: $($locked.Evidence)"
        Assert-True -Condition ($locked.Evidence -match "install-device-service") -Because "and what to run: $($locked.Evidence)"

        [void](Set-CompanionAuditDirectoryAcl -Path $audit -OwnerSid $script:CurrentSid)
        $repaired = Get-CompanionAuditDirectoryVerdict -Path $audit -OwnerSid $script:CurrentSid
        Assert-Equal -Expected "PROVEN_REAL" -Actual $repaired.Status -Because $repaired.Evidence
        Assert-Equal -Expected "audit dir writable by owner" -Actual $repaired.Criterion -Because "the row is found by this name"

        $absent = Get-CompanionAuditDirectoryVerdict -Path (Join-Path $companion "no-such-audit") -OwnerSid $script:CurrentSid
        Assert-Equal -Expected "NOT_YET_PROVEN" -Actual $absent.Status -Because "nothing to check is not a pass"
        Assert-True -Condition ($absent.Evidence -match "does not exist") -Because $absent.Evidence
    }

    Test-Case "the installer and the verifier are wired to it: before the runtime starts, outside the browser branch" {
        $tokens = $null; $errors = $null
        $installerPath = Join-Path $repoRoot "scripts\install-device-service.ps1"
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($installerPath, [ref]$tokens, [ref]$errors)
        $calls = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true))
        $initialize = @($calls | Where-Object { $_.GetCommandName() -eq "Initialize-CompanionDataDirectories" })
        Assert-Equal -Expected 1 -Actual @($initialize).Count -Because "the installer calls the step exactly once"

        # Not inside `if (-not $SkipBrowser) { ... }`: the companion audits with or without a browser.
        $parent = $initialize[0].Parent
        while ($null -ne $parent) {
            if ($parent -is [System.Management.Automation.Language.IfStatementAst]) {
                throw "the step sits inside an if statement at line $($parent.Extent.StartLineNumber); it must run on every install"
            }
            $parent = $parent.Parent
        }

        $register = @($calls | Where-Object { $_.GetCommandName() -eq "Register-CompanionAutostart" })[0]
        Assert-True -Condition ($initialize[0].Extent.StartLineNumber -lt $register.Extent.StartLineNumber) `
            -Because "the directory is put right before the companion is registered, and long before it is started"
        $raw = @($calls | Where-Object { $_.GetCommandName() -eq "Set-OwnerWritableDirectory" })
        Assert-Equal -Expected 0 -Actual @($raw).Count -Because "the inline grants were the code that missed the audit directory; they live in the step now"

        $verifier = [System.IO.File]::ReadAllText((Join-Path $repoRoot "scripts\verify-device-service.ps1"))
        Assert-True -Condition ($verifier -match "Get-CompanionAuditDirectoryVerdict") -Because "the verifier reports the row"
    }
}
finally {
    # The audit fixtures are protected against this account on purpose. It owns them, and an
    # owner keeps WRITE_DAC, so inheritance is handed back before the sandbox is removed.
    foreach ($locked in @($script:LockedForCleanup)) {
        if (-not (Test-Path -LiteralPath $locked)) { continue }
        try {
            $open = New-Object System.Security.AccessControl.DirectorySecurity
            $open.SetAccessRuleProtection($false, $false)
            ([System.IO.DirectoryInfo]$locked).SetAccessControl($open)
        } catch { }
    }

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
