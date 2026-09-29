<#
.SYNOPSIS
    ACL posture, repair, staging and atomic deployment for the Device Service install.

.DESCRIPTION
    A real install left this tree in a state the installer could not recover from, and the
    cause was the hardening command itself:

        icacls <root> /inheritance:r /grant:r "*S-1-5-32-544:(OI)(CI)F" ... /T

    With /T, icacls visits every child. `/inheritance:r` strips each child's inherited ACEs,
    but the grants carry (OI)(CI) — inheritance flags that mean nothing on a leaf file — so
    icacls applies no ACE to files. Every file that existed at hardening time was left with a
    protected, EMPTY DACL:

        directory : D:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)   correct
        file      : D:PAI                                                          no ACEs

    An empty DACL denies everyone, including Administrators and SYSTEM. Measured on the
    owner's machine: 219 files under service\ in that state. Two consequences followed, one
    visible and one not yet:

    - rewriting appsettings.json in place failed with access denied, even elevated, because
      opening an existing file for write needs a granting ACE and there were none. Only the
      OWNER can act, through the implicit READ_CONTROL/WRITE_DAC that ownership always
      carries — which is the door this module uses to repair;
    - the service would not have started either: SYSTEM cannot read its own executable
      through an empty DACL. The 1639 registration failure hid a second, later failure.

    Files created *after* hardening inherit correctly (the directory's ACEs are OI/CI), which
    is why `dotnet publish` succeeded on the rerun while the config write failed — publish
    deletes and recreates, and deleting a child needs permission on the parent, not the file.

    The model here is therefore: explicit ACEs on the root only, children inherit; stage
    everything outside the live tree; swap it in atomically; harden last; then verify the
    posture rather than assume it.
#>

Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "NativeProcess.ps1")

<#
    A note on cardinality, because it cost a real install.

    Windows PowerShell unrolls a returned collection: a function returning an empty array
    yields $null, and one returning a single item yields that item. Under
    `Set-StrictMode -Version Latest` — which these libraries set, and which dot-sourcing
    propagates into the installer's scope — reading `.Count` on either throws
    PropertyNotFoundStrict. Measured on the owner's machine and reproduced here:

        0 restored -> $null           -> @($restored).Count  throws
        1 restored -> System.String   -> @($restored).Count  throws
        2 restored -> Object[]        -> works

    So a rerun with nothing to restore failed, and a rerun with exactly one thing to restore
    would have failed too. Only the case my tests happened to exercise worked.

    Three rules follow, applied throughout these scripts:

      1. every `.Count` is written as `@(...).Count`, which is correct for $null, for a
         scalar and for an array alike;
      2. a caller collecting a function's collection output writes `@(Call)`;
      3. a function returning a collection returns plain `@(...)` — NOT `,@(...)`.

    Rules 2 and 3 go together, and the pairing is deliberate. `,@(...)` also survives a bare
    assignment, but combined with `@(Call)` at the call site it wraps the array in another
    array: counts silently read 1, and a message interpolates as "System.Object[]". That
    happened here while fixing this very bug. Plain `@(...)` plus `@(Call)` is correct; and
    when a caller forgets the wrapper, the result is $null and StrictMode throws loudly at
    the next `.Count` — a failure that announces itself rather than quietly miscounting.

    Where a function has more than one thing to say, it returns an object with named array
    properties instead (see `Invoke-InstallRecovery`). Property access never unrolls, so that
    shape is safe under either calling style and needs no convention at all.

    `scripts/tests/installer-strictmode.tests.ps1` enforces rule 1 mechanically with the
    PowerShell parser and covers the 0/1/many cases for every function here.
#>

# Well-known SIDs. Written as SIDs, not names, because names are localized — this machine
# reports "BUILTIN\Users" in English but its errors in Turkish, and a name comparison would
# quietly stop matching on a differently-localized Windows.
$script:SidSystem = "S-1-5-18"
$script:SidAdministrators = "S-1-5-32-544"
$script:SidUsers = "S-1-5-32-545"
$script:SidAuthenticatedUsers = "S-1-5-11"
$script:SidEveryone = "S-1-1-0"
$script:SidInteractive = "S-1-5-4"

#: The ONLY principals allowed to hold write authority over the installed tree. An allowlist
#: rather than a denylist of Users/Everyone/Authenticated Users: the question the owner asked
#: is "does only the intended principal retain write authority", and a denylist answers a
#: weaker question — it would pass a tree that granted some individual account full control.
$script:WriteAuthorizedSids = @(
    $script:SidSystem,
    $script:SidAdministrators
)

#: Named for clearer violation messages when one of these well-known groups is the culprit.
$script:UnprivilegedSids = @(
    $script:SidUsers,
    $script:SidAuthenticatedUsers,
    $script:SidEveryone,
    $script:SidInteractive
)

#: Rights that constitute write authority for the purposes of the posture check. Includes
#: ChangePermissions and TakeOwnership: either one is write authority one step removed.
$script:WriteRights = [System.Security.AccessControl.FileSystemRights]::WriteData -bor
                      [System.Security.AccessControl.FileSystemRights]::AppendData -bor
                      [System.Security.AccessControl.FileSystemRights]::WriteAttributes -bor
                      [System.Security.AccessControl.FileSystemRights]::WriteExtendedAttributes -bor
                      [System.Security.AccessControl.FileSystemRights]::Delete -bor
                      [System.Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
                      [System.Security.AccessControl.FileSystemRights]::ChangePermissions -bor
                      [System.Security.AccessControl.FileSystemRights]::TakeOwnership

function Get-SecurityDescriptor {
    <#
    .SYNOPSIS
        Read a file or directory's security descriptor, requesting only the sections needed.

    .DESCRIPTION
        `Get-Acl` requests every section, including the system ACL, and writing such an
        object back needs SeSecurityPrivilege. That made the second call to
        `Set-HardenedAcl` fail on an already-hardened tree — an idempotency bug in the fix
        for an idempotency bug. Owner and DACL are all this installer manipulates, so they
        are all it asks for.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [System.Security.AccessControl.AccessControlSections]$Sections =
            ([System.Security.AccessControl.AccessControlSections]::Access -bor [System.Security.AccessControl.AccessControlSections]::Owner)
    )

    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) {
        return ([System.IO.DirectoryInfo]$item.FullName).GetAccessControl($Sections)
    }
    return ([System.IO.FileInfo]$item.FullName).GetAccessControl($Sections)
}

function Set-SecurityDescriptor {
    <#
    .SYNOPSIS
        Write back a descriptor whose SACL was never requested, so no privilege is needed.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Security
    )

    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer) {
        ([System.IO.DirectoryInfo]$item.FullName).SetAccessControl($Security)
    }
    else {
        ([System.IO.FileInfo]$item.FullName).SetAccessControl($Security)
    }
}

function Get-AclReport {
    <#
    .SYNOPSIS
        Everything the installer needs to know about one path's security, or why it cannot
        be read.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{
            Path = $Path; Exists = $false; Readable = $false; Owner = $null; Sddl = $null
            IsProtected = $false; AceCount = 0; Aces = @(); IsReadOnly = $false; Error = $null
        }
    }

    $item = Get-Item -LiteralPath $Path -Force
    $isReadOnly = -not $item.PSIsContainer -and $item.Attributes.HasFlag([System.IO.FileAttributes]::ReadOnly)

    try {
        # Owner and DACL only. `Get-Acl` asks for every section including the SACL, which
        # requires SeSecurityPrivilege — a privilege this installer has no reason to want and
        # a non-elevated caller does not hold. Asking for what is needed keeps the check
        # usable from an ordinary session and keeps the installer's privilege appetite small.
        $acl = Get-SecurityDescriptor -Path $Path -Sections ([System.Security.AccessControl.AccessControlSections]::Access -bor [System.Security.AccessControl.AccessControlSections]::Owner)
    }
    catch {
        # An unreadable DACL is itself a finding: it means this account has neither an ACE
        # nor ownership, which for our own install directory should never be true.
        return [pscustomobject]@{
            Path = $Path; Exists = $true; Readable = $false; Owner = $null; Sddl = $null
            IsProtected = $false; AceCount = 0; Aces = @(); IsReadOnly = $isReadOnly
            Error = $_.Exception.Message
        }
    }

    $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    return [pscustomobject]@{
        Path        = $Path
        Exists      = $true
        Readable    = $true
        Owner       = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
        Sddl        = $acl.GetSecurityDescriptorSddlForm(
                          [System.Security.AccessControl.AccessControlSections]::Access -bor
                          [System.Security.AccessControl.AccessControlSections]::Owner)
        IsProtected = $acl.AreAccessRulesProtected
        AceCount    = @($rules).Count
        Aces        = $rules
        IsReadOnly  = $isReadOnly
        Error       = $null
    }
}

function Resolve-SidValue {
    <#
    .SYNOPSIS
        SID string for an ACE's identity, whatever form it arrived in.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Identity)

    try {
        if ($Identity -is [System.Security.Principal.SecurityIdentifier]) {
            return $Identity.Value
        }
        return $Identity.Translate([System.Security.Principal.SecurityIdentifier]).Value
    }
    catch {
        return $null
    }
}

function Test-InstallAclPosture {
    <#
    .SYNOPSIS
        Independent check that only the intended principals hold write authority.

    .DESCRIPTION
        Deliberately re-derived from the ACLs on disk rather than from what the installer
        believes it applied. Returns a report with a Violations list; empty means the posture
        is as intended.

        Three things are checked on the root and on a sample of the tree: no unprivileged
        principal holds any write right; SYSTEM and Administrators hold FullControl; and no
        object has an empty or unreadable DACL, which is the specific breakage this module
        exists to repair.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [int]$SampleSize = 40
    )

    $violations = New-Object System.Collections.ArrayList
    $checked = 0

    $targets = New-Object System.Collections.ArrayList
    [void]$targets.Add($Root)
    if (Test-Path -LiteralPath $Root) {
        Get-ChildItem -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue |
            Select-Object -First $SampleSize |
            ForEach-Object { [void]$targets.Add($_.FullName) }
    }

    foreach ($path in $targets) {
        $report = Get-AclReport -Path $path
        if (-not $report.Exists) {
            [void]$violations.Add("missing: $path")
            continue
        }
        $checked++

        if (-not $report.Readable) {
            [void]$violations.Add("unreadable DACL (no ACE and not owner): $path")
            continue
        }

        if ($report.AceCount -eq 0) {
            [void]$violations.Add("empty DACL - denies everyone including SYSTEM: $path")
            continue
        }

        $hasSystemFull = $false
        $hasAdminFull = $false

        foreach ($ace in $report.Aces) {
            $sid = Resolve-SidValue -Identity $ace.IdentityReference
            if (-not $sid) { continue }

            $isAllow = ($ace.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow)
            $grantsWrite = (([int]$ace.FileSystemRights -band [int]$script:WriteRights) -ne 0)

            if ($isAllow -and $grantsWrite -and ($script:WriteAuthorizedSids -notcontains $sid)) {
                $who = if ($script:UnprivilegedSids -contains $sid) { "unprivileged principal" } else { "principal" }
                [void]$violations.Add("write authority granted to $who $sid on $path : $($ace.FileSystemRights)")
            }

            if ($isAllow -and $ace.FileSystemRights.HasFlag([System.Security.AccessControl.FileSystemRights]::FullControl)) {
                if ($sid -eq $script:SidSystem) { $hasSystemFull = $true }
                if ($sid -eq $script:SidAdministrators) { $hasAdminFull = $true }
            }
        }

        if (-not $hasSystemFull) {
            [void]$violations.Add("SYSTEM lacks FullControl (the service runs as SYSTEM and could not read this): $path")
        }
        if (-not $hasAdminFull) {
            [void]$violations.Add("Administrators lack FullControl (the installer could not update this): $path")
        }
    }

    return [pscustomobject]@{
        Root       = $Root
        Checked    = $checked
        Violations = @($violations)
        Ok         = (@($violations).Count -eq 0)
    }
}

function Repair-InstallTreeAcl {
    <#
    .SYNOPSIS
        Bring a tree left in a broken or unknown ACL state back under administrative control,
        without granting anything to anyone else.

    .DESCRIPTION
        The repair is deliberately narrow. It never adds a principal and never widens rights:
        it restores inheritance on children so they pick up the root's intended ACEs, clears
        read-only attributes that would block replacement, and — only if the root itself is
        not administrable — takes ownership to the Administrators group so the DACL can be
        rewritten at all.

        Ownership is the lever that makes recovery possible without a manual reset: an owner
        always retains READ_CONTROL and WRITE_DAC, so a tree whose DACLs deny everyone can
        still be repaired by its owner. Taking ownership as Administrators is the minimum
        privileged transition that restores control, and it is applied only when needed.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [switch]$Quiet
    )

    if (-not (Test-Path -LiteralPath $Root)) {
        return [pscustomobject]@{ Repaired = $false; Actions = @(); Reason = "nothing to repair: $Root does not exist" }
    }

    $actions = New-Object System.Collections.ArrayList
    $icacls = Get-SystemTool -Name "icacls.exe"

    # 1. Is the root itself administrable? If its DACL cannot even be read, ownership is the
    #    only way back.
    $rootReport = Get-AclReport -Path $Root
    if (-not $rootReport.Readable -or $rootReport.AceCount -eq 0) {
        $takeown = Get-SystemTool -Name "takeown.exe"
        $result = Invoke-NativeProcess -FilePath $takeown -Arguments @("/F", $Root, "/A", "/R", "/D", "Y") -TimeoutSeconds 600
        Assert-NativeSuccess -Result $result -Activity "takeown $Root (recovering an unreadable install root)"
        [void]$actions.Add("took ownership of the tree to BUILTIN\Administrators")
    }

    # 2. Clear read-only attributes. A read-only file cannot be replaced even with a
    #    permissive DACL, and a previous interrupted run can leave them behind.
    $readOnly = @(Get-ChildItem -LiteralPath $Root -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Attributes.HasFlag([System.IO.FileAttributes]::ReadOnly) })
    foreach ($file in $readOnly) {
        $file.Attributes = $file.Attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)
    }
    if (@($readOnly).Count -gt 0) {
        [void]$actions.Add("cleared the read-only attribute on $(@($readOnly).Count) file(s)")
    }

    # 3. Restore inheritance, but only where something is actually wrong. Resetting
    #    unconditionally would work, and would also make `Repaired` meaningless — every run
    #    would claim to have repaired something. A child needs the reset when its DACL is
    #    empty or unreadable: those are exactly the states the old hardening produced.
    $damaged = @(Get-ChildItem -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object {
            $report = Get-AclReport -Path $_.FullName
            (-not $report.Readable) -or ($report.AceCount -eq 0)
        } | Select-Object -First 1)

    if (@($damaged).Count -gt 0) {
        # One tree-wide reset rather than per-file: cheaper, and the damage is never isolated
        # in practice — the old hardening hit every file that existed at the time.
        # /C continues past individual failures so one stubborn file cannot abort the repair;
        # the posture check afterwards is what decides whether the repair actually worked.
        $reset = Invoke-NativeProcess -FilePath $icacls `
            -Arguments @((Join-Path $Root "*"), "/reset", "/T", "/C", "/Q") -TimeoutSeconds 600
        if (-not $Quiet) {
            Write-Host "restored inheritance on the existing install tree (icacls exit $($reset.ExitCode))"
        }
        [void]$actions.Add("reset child ACLs to inherit from the install root")
    }

    return [pscustomobject]@{
        Repaired = (@($actions).Count -gt 0)
        Actions  = @($actions)
        Reason   = if (@($actions).Count -gt 0) { "recovered a previously hardened or partial install" } else { "tree was already administrable" }
    }
}

function Set-HardenedAcl {
    <#
    .SYNOPSIS
        Apply the intended posture: explicit ACEs on the root, inheritance everywhere below.

    .DESCRIPTION
        The root gets a protected DACL with exactly three ACEs — SYSTEM and Administrators
        full control, Users read and execute — each marked to inherit to files and
        directories. Children then get NO explicit ACEs at all; they inherit.

        This is the fix for the original defect. The previous version applied the same
        (OI)(CI) grants to every child with /T, and inheritance flags are meaningless on a
        leaf, so files ended up with inheritance stripped and nothing granted.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Root)

    if (-not (Test-Path -LiteralPath $Root)) {
        throw "cannot harden a path that does not exist: $Root"
    }

    $acl = Get-SecurityDescriptor -Path $Root

    # Protect the DACL and drop inherited entries: this tree's rights are stated here, not
    # borrowed from whatever Program Files happens to say.
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($existing in @($acl.GetAccessRules($true, $false, [System.Security.Principal.SecurityIdentifier]))) {
        [void]$acl.RemoveAccessRuleSpecific($existing)
    }

    $inherit = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
               [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    $none = [System.Security.AccessControl.PropagationFlags]::None
    $allow = [System.Security.AccessControl.AccessControlType]::Allow

    foreach ($grant in @(
        @{ Sid = $script:SidSystem;         Rights = [System.Security.AccessControl.FileSystemRights]::FullControl },
        @{ Sid = $script:SidAdministrators; Rights = [System.Security.AccessControl.FileSystemRights]::FullControl },
        @{ Sid = $script:SidUsers;          Rights = [System.Security.AccessControl.FileSystemRights]::ReadAndExecute }
    )) {
        $identity = New-Object System.Security.Principal.SecurityIdentifier($grant.Sid)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($identity, $grant.Rights, $inherit, $none, $allow)
        $acl.AddAccessRule($rule)
    }

    Set-SecurityDescriptor -Path $Root -Security $acl

    # Children hold no explicit ACEs; /reset makes each one inherit what the root now says.
    $icacls = Get-SystemTool -Name "icacls.exe"
    if (@(Get-ChildItem -LiteralPath $Root -Force -ErrorAction SilentlyContinue).Count -gt 0) {
        $reset = Invoke-NativeProcess -FilePath $icacls `
            -Arguments @((Join-Path $Root "*"), "/reset", "/T", "/C", "/Q") -TimeoutSeconds 600
        if ($reset.ExitCode -ne 0) {
            Write-Warning "icacls reported exit $($reset.ExitCode) while resetting child ACLs; the posture check below is authoritative"
        }
    }
}

# ------------------------------------------------------------------- staging and deployment

function Set-MachineDataAcl {
    <#
    .SYNOPSIS
        Protect the agent's DATA directory: SYSTEM and Administrators, nobody else — not
        even read, because this tree holds the device private key.

    .DESCRIPTION
        Same shape as Set-HardenedAcl (explicit ACEs on the root only, children inherit),
        different principal set: no Users read here.

        This function replaced a raw `icacls /inheritance:r /grant:r "*SID:(OI)(CI)F" /T`
        call — the exact empty-DACL bug already fixed once for Program Files, reintroduced
        for ProgramData, and found the hard way a second time: the /T pass stripped the
        service's own LOG FILE to an empty DACL, SYSTEM's next log write threw, and that one
        exception killed the pipe server while the service stayed "Running". Files that only
        inherited (logs, audit) broke; files with explicit ACEs survived — which is exactly
        the signature of the bug class.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Root)

    if (-not (Test-Path -LiteralPath $Root)) {
        throw "cannot protect a path that does not exist: $Root"
    }

    $acl = Get-SecurityDescriptor -Path $Root
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($existing in @($acl.GetAccessRules($true, $false, [System.Security.Principal.SecurityIdentifier]))) {
        [void]$acl.RemoveAccessRuleSpecific($existing)
    }

    $inherit = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
               [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    foreach ($sid in @($script:SidSystem, $script:SidAdministrators)) {
        $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            (New-Object System.Security.Principal.SecurityIdentifier($sid)),
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            $inherit,
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow)))
    }
    Set-SecurityDescriptor -Path $Root -Security $acl

    # Children inherit; /reset clears any explicit or stripped state left by earlier bugs.
    # Explicit ACEs a repair applied on purpose (device.key SYSTEM-read) are re-applied by
    # the repair path, which runs after this in every flow that uses both.
    $icacls = Get-SystemTool -Name "icacls.exe"
    if (@(Get-ChildItem -LiteralPath $Root -Force -ErrorAction SilentlyContinue).Count -gt 0) {
        [void](Invoke-NativeProcess -FilePath $icacls `
            -Arguments @((Join-Path $Root "*"), "/reset", "/T", "/C", "/Q") -TimeoutSeconds 600)
    }
}

# ------------------------------------------------------- the companion's audit directory
#
# 2026-09-29, on the owner's machine. The Session Companion is the OWNER's non-elevated
# process, and its audit directory was protected as if it were the service's: a protected
# DACL naming SYSTEM and Administrators only, applied by the companion itself on its first
# start (it had just created the directory, and a creator owns what it creates). From then
# on it could not write a row. The installer granted the owner Modify on the companion's
# data ROOT, inheritable - and a protected DACL inherits nothing - so the health check
# waited 90 s for a browser_worker_started row that could not be written and rolled back a
# browser worker 0.5.0 that had passed its self-check. Twice, until the owner ran icacls by
# hand.
#
# Protection is therefore by identity. Set-MachineDataAcl above stays what it is, for what
# the service owns. What the companion writes names the owner SID the companion runs as -
# with Modify, not full control, and nobody else.

# Security review of 2a2f7f95 (2026-09-29). Everything below runs ELEVATED on a path an
# unprivileged account can shape - the owner holds Modify on companion\, and ProgramData lets
# every local user create folders there - so four things are checked before anything is
# written, each because a proof of concept showed what happens without it:
#
#   - no reparse point. A junction planted at companion\audit pointed the installer's
#     `icacls <audit>\* /reset /T` at another directory, and a SYSTEM-only file there came
#     back with inherited Administrators and user entries;
#   - no walking through one either: the child reset is an enumeration that looks at every
#     entry's attributes, and steps around what it will not enter;
#   - the owner SID is a USER. -OwnerSid S-1-5-32-545 would have granted every local user
#     Modify on the trail;
#   - the directory's OWNER is someone who may hold WRITE_DAC over it. A directory
#     pre-created by another account stays that account's to re-permission, whatever DACL is
#     written onto it.
#
# What this does NOT close: the check and the write are two system calls on a path, not one
# operation on a handle. An account that can replace companion\audit between them still
# wins the race; the window is milliseconds, and closing it takes handle-based Win32 calls
# this installer does not have.

#: Domain RIDs that name a GROUP (or a machine) under an S-1-5-21 domain. Refused without a
#: lookup, because the lookup needs a domain controller and the answer is already known.
$script:GroupRids = @(498, 512, 513, 514, 515, 516, 517, 518, 519, 520, 521, 522, 525, 526, 527, 553, 571, 572)

#: Who may own the companion's audit directory besides the owner account itself.
$script:TrustedOwnerSids = @($script:SidSystem, $script:SidAdministrators)

function Get-SidNameUse {
    <#
    .SYNOPSIS
        What kind of principal a SID names, from the system (LookupAccountSid): 1 is a user.
        0 when the SID resolves to nobody.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][System.Security.Principal.SecurityIdentifier]$Sid)

    if (-not ("PagentOS.Install.SidLookup" -as [type])) {
        Add-Type -Namespace PagentOS.Install -Name SidLookup -MemberDefinition @"
[DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool LookupAccountSid(string systemName, byte[] sid, System.Text.StringBuilder name, ref uint nameLength, System.Text.StringBuilder domain, ref uint domainLength, out int use);
"@
    }
    $bytes = New-Object byte[] $Sid.BinaryLength
    $Sid.GetBinaryForm($bytes, 0)
    $name = New-Object System.Text.StringBuilder 512
    $domain = New-Object System.Text.StringBuilder 512
    [uint32]$nameLength = 512
    [uint32]$domainLength = 512
    $use = 0
    if ([PagentOS.Install.SidLookup]::LookupAccountSid($null, $bytes, $name, [ref]$nameLength, $domain, [ref]$domainLength, [ref]$use)) {
        return $use
    }
    return 0
}

function Assert-OwnerAccountSid {
    <#
    .SYNOPSIS
        Throws unless the SID names a USER ACCOUNT. Called before anything is changed.

    .DESCRIPTION
        The owner SID is written into DACLs with Modify. A group there - BUILTIN\Users,
        Everyone, Authenticated Users, Domain Users - turns "the owner may write the trail"
        into "everybody may", and nothing downstream would notice: the grant would be exactly
        what was asked for.

        -AllowUnresolved forgives a SID the system cannot resolve (a test's made-up account,
        a domain account while the domain is unreachable). It never forgives one the system
        DOES resolve to something other than a user, and the installer does not pass it.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Sid,
        [switch]$AllowUnresolved
    )

    $refuse = { param($why) throw "the owner SID '$Sid' is refused: $why. It must be the SID of the owner's own user account (whoami /user). Nothing was changed." }

    $parsed = $null
    try { $parsed = New-Object System.Security.Principal.SecurityIdentifier($Sid) } catch { $parsed = $null }
    if ($null -eq $parsed) { & $refuse "it is not a SID" }

    $value = $parsed.Value
    if (($script:UnprivilegedSids -contains $value) -or ($script:WriteAuthorizedSids -contains $value)) {
        & $refuse "it is a well-known group or service principal, not a person"
    }
    # A user account is S-1-5-21-<domain>-<rid> (local or domain) or S-1-12-1-... (Entra ID).
    # Everything else - BUILTIN aliases, CREATOR OWNER, logon and service SIDs - is not.
    if ($value -notmatch '^S-1-5-21-\d+-\d+-\d+-(\d+)$' -and $value -notmatch '^S-1-12-1-\d+-\d+-\d+-\d+$') {
        & $refuse "it is not an account SID"
    }
    if ($value -match '^S-1-5-21-\d+-\d+-\d+-(\d+)$' -and ($script:GroupRids -contains [int64]$Matches[1])) {
        & $refuse "RID $($Matches[1]) is a domain group or machine account"
    }

    $use = Get-SidNameUse -Sid $parsed
    if ($use -eq 1) { return }
    if ($use -eq 0) {
        if ($AllowUnresolved) { return }
        & $refuse "this machine cannot resolve it to any account, so nothing says it is a user"
    }
    & $refuse "it resolves to a principal of kind $use (2 group, 4 alias, 5 well-known group, 9 computer), not a user"
}

function Test-IsReparsePoint {
    <#  Is this path itself a junction, symbolic link or mount point? False when it does not exist.  #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)
    try {
        # The attributes of the LINK, never of what it points at - and they are there for a
        # dangling link too, which Test-Path reports as absent.
        $attributes = [System.IO.File]::GetAttributes($Path)
        return [bool]($attributes -band [System.IO.FileAttributes]::ReparsePoint)
    }
    catch [System.IO.FileNotFoundException] { return $false }
    catch [System.IO.DirectoryNotFoundException] { return $false }
}

function Assert-NoReparsePointInPath {
    <#
    .SYNOPSIS
        Throws when the path, or any directory between it and -TrustedRoot (inclusive), is
        a reparse point. Nothing is created or changed by asking.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$TrustedRoot,
        [Parameter(Mandatory = $true)][string]$Purpose
    )

    $trim = [char[]]@('\', '/')
    $stop = [System.IO.Path]::GetFullPath($TrustedRoot).TrimEnd($trim)
    $current = [System.IO.Path]::GetFullPath($Path).TrimEnd($trim)
    if (-not ($current.Equals($stop, [System.StringComparison]::OrdinalIgnoreCase) -or
              $current.StartsWith($stop + '\', [System.StringComparison]::OrdinalIgnoreCase))) {
        throw "cannot check $Path for reparse points: it is not under $TrustedRoot"
    }

    while ($true) {
        if (Test-IsReparsePoint -Path $current) {
            throw "refusing to touch $Path ($Purpose): $current is a reparse point (a junction or a symbolic link), and whatever is written there would land where it points. " +
                  "Nothing was changed. Look at where it leads (dir /AL `"$(Split-Path -Parent $current)`"), remove it, and run this again."
        }
        if ($current.Equals($stop, [System.StringComparison]::OrdinalIgnoreCase)) { break }
        $current = Split-Path -Parent $current
        if (-not $current) { break }
    }
}

function Reset-ChildAclToInherit {
    <#
    .SYNOPSIS
        Make everything under a directory inherit from it again - by enumeration, looking at
        every entry, and never into or through a reparse point.

    .DESCRIPTION
        Replaces `icacls <dir>\* /reset /T`, which follows junctions: planted inside (or as)
        the directory, one pointed an elevated installer at another tree.

        Returns Reset (count), SkippedReparsePoints and Failures (paths with the reason).
        One entry that cannot be reset does not stop the rest, as /C did not.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [string]$NewOwnerSid
    )

    $skipped = New-Object System.Collections.ArrayList
    $failures = New-Object System.Collections.ArrayList
    $reset = 0
    # A list walked by index, growing as directories are found: every real directory is
    # visited once, in the order it was met. (@( ).Count, per the cardinality note above.)
    $pending = New-Object System.Collections.ArrayList
    [void]$pending.Add($Directory)

    for ($index = 0; $index -lt @($pending).Count; $index++) {
        $directory = $pending[$index]
        $entries = @()
        try { $entries = @([System.IO.Directory]::GetFileSystemEntries($directory)) }
        catch { [void]$failures.Add("$directory (listing): $($_.Exception.Message)"); continue }

        foreach ($entry in $entries) {
            try {
                $attributes = [System.IO.File]::GetAttributes($entry)
                if ($attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                    # Neither reset nor entered: its descriptor is not ours to decide from
                    # here, and what is behind it is not under this directory at all.
                    [void]$skipped.Add($entry)
                    continue
                }
                $isDirectory = [bool]($attributes -band [System.IO.FileAttributes]::Directory)
                if ($isDirectory) { $security = New-Object System.Security.AccessControl.DirectorySecurity }
                else { $security = New-Object System.Security.AccessControl.FileSecurity }
                $security.SetAccessRuleProtection($false, $false)
                if ($NewOwnerSid) { $security.SetOwner((New-Object System.Security.Principal.SecurityIdentifier($NewOwnerSid))) }
                if ($isDirectory) { ([System.IO.DirectoryInfo]$entry).SetAccessControl($security); [void]$pending.Add($entry) }
                else { ([System.IO.FileInfo]$entry).SetAccessControl($security) }
                $reset++
            }
            catch {
                $why = $_.Exception.Message
                if ($_.Exception.InnerException) { $why = $_.Exception.InnerException.Message }
                [void]$failures.Add("$entry : $why")
            }
        }
    }

    return [pscustomobject]@{
        Reset                = $reset
        SkippedReparsePoints = @($skipped)
        Failures             = @($failures)
    }
}

function Get-CompanionAuditDirectoryReport {
    <#
    .SYNOPSIS
        Can this SID write the companion's audit trail? Read from the DACL, so the answer
        is the same whoever asks - the elevated installer asks about another account.

    .DESCRIPTION
        OwnerCanWrite is true when allow entries for the SID (explicit or inherited) that
        reach the FILES in the directory add up to Modify, and no deny entry against the
        SID takes any of it away. OtherWriters lists every other principal holding write
        authority beyond SYSTEM and Administrators; it is evidence, not the verdict.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$OwnerSid,
        # What Get-AclReport said about the path. Injectable because a non-elevated test
        # cannot make another account the owner of a real directory; the verifier and the
        # installer never pass it.
        $AclReport = $null
    )

    $acl = if ($null -ne $AclReport) { $AclReport } else { Get-AclReport -Path $Path }
    $problems = New-Object System.Collections.ArrayList
    $otherWriters = New-Object System.Collections.ArrayList
    $ownerCanWrite = $false
    $ownerTrusted = $false
    $isReparsePoint = Test-IsReparsePoint -Path $Path

    if ($isReparsePoint) {
        [void]$problems.Add("$Path is a reparse point (a junction or a symbolic link): the trail would be written wherever it points")
    }
    elseif (-not $acl.Exists) {
        [void]$problems.Add("$Path does not exist")
    }
    elseif (-not $acl.Readable) {
        [void]$problems.Add("the security descriptor of $Path cannot be read from this account: $($acl.Error)")
    }
    else {
        $modify = [int][System.Security.AccessControl.FileSystemRights]::Modify
        $allowed = 0
        $denied = 0
        foreach ($ace in $acl.Aces) {
            $sid = Resolve-SidValue -Identity $ace.IdentityReference
            if (-not $sid) { continue }
            $isAllow = ($ace.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow)
            if ($sid -eq $OwnerSid) {
                if (-not $isAllow) { $denied = $denied -bor ([int]$ace.FileSystemRights -band $modify) }
                elseif ($ace.InheritanceFlags -band [System.Security.AccessControl.InheritanceFlags]::ObjectInherit) {
                    # An entry for "this folder only" never reaches the audit FILE.
                    $allowed = $allowed -bor [int]$ace.FileSystemRights
                }
                continue
            }
            if ($isAllow -and (([int]$ace.FileSystemRights -band [int]$script:WriteRights) -ne 0) -and
                ($script:WriteAuthorizedSids -notcontains $sid)) {
                [void]$otherWriters.Add("$sid ($($ace.FileSystemRights))")
            }
        }

        if ($denied -ne 0) {
            [void]$problems.Add("a deny entry against $OwnerSid takes away $([System.Security.AccessControl.FileSystemRights]$denied)")
        }
        if (($allowed -band $modify) -ne $modify) {
            $has = if ($allowed -eq 0) { "nothing" } else { [string][System.Security.AccessControl.FileSystemRights]$allowed }
            [void]$problems.Add("$OwnerSid is allowed $has on files in $Path; the companion needs Modify to append a row")
        }
        $ownerCanWrite = (@($problems).Count -eq 0)

        # The directory's OWNER, separately from its DACL: an owner holds WRITE_DAC whatever
        # the DACL says, so a directory some other account pre-created is that account's to
        # re-permission - and then to rewrite the trail in - at any time.
        $ownerTrusted = ($acl.Owner -eq $OwnerSid) -or ($script:TrustedOwnerSids -contains $acl.Owner)
        if (-not $ownerTrusted) {
            [void]$problems.Add("$Path is owned by $($acl.Owner), which is neither $OwnerSid nor Administrators nor SYSTEM; an owner can re-permission what it owns")
        }
    }

    return [pscustomobject]@{
        Path           = $Path
        OwnerSid       = $OwnerSid
        Exists         = [bool]$acl.Exists
        Readable       = [bool]$acl.Readable
        IsProtected    = [bool]$acl.IsProtected
        IsReparsePoint = $isReparsePoint
        Owner          = $acl.Owner
        OwnerTrusted   = $ownerTrusted
        OwnerCanWrite  = $ownerCanWrite
        OtherWriters   = @($otherWriters)
        Problems       = @($problems)
        Sddl           = $acl.Sddl
    }
}

function Set-CompanionAuditDirectoryAcl {
    <#
    .SYNOPSIS
        Create or REPAIR the companion's audit directory: a protected DACL with SYSTEM and
        Administrators full control and the owner SID Modify, inherited by the trail.

    .DESCRIPTION
        Idempotent: a directory that already carries exactly this DACL is left alone and
        reported as unchanged. Anything else - the locked state the old companion produced,
        a deny entry, a stray grant, plain ProgramData inheritance (which lets every local
        user create files beside the trail) - is replaced, not merged into.

        Only the descriptor is written. The rows already in the trail are never touched.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$OwnerSid,
        # How far up reparse points are looked for: the path and every directory between it
        # and this one, inclusive. The companion data root, when the installer calls.
        [string]$TrustedRoot = (Split-Path -Parent $Path),
        # Whether this process holds the Administrators token. Stated by the tests, which
        # cannot be elevated; computed everywhere else.
        [bool]$Elevated = ([System.Security.Principal.WindowsPrincipal]::new(
            [System.Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole(
            [System.Security.Principal.WindowsBuiltInRole]::Administrator),
        [switch]$AllowUnresolvedOwnerSid
    )

    # Both refusals come before the first thing that is created or written.
    Assert-OwnerAccountSid -Sid $OwnerSid -AllowUnresolved:$AllowUnresolvedOwnerSid
    Assert-NoReparsePointInPath -Path $Path -TrustedRoot $TrustedRoot -Purpose "the companion's audit directory"

    $created = -not (Test-Path -LiteralPath $Path)
    if ($created) {
        New-Item -ItemType Directory -Force -Path $Path | Out-Null
        # Created a moment ago by this process, and still checked: between the check above
        # and the creation, the name was free for anyone to take.
        Assert-NoReparsePointInPath -Path $Path -TrustedRoot $TrustedRoot -Purpose "the companion's audit directory"
    }

    $inherit = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
               [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    $none = [System.Security.AccessControl.PropagationFlags]::None
    $allow = [System.Security.AccessControl.AccessControlType]::Allow

    # Built from nothing rather than edited: what the directory ends up with is what is
    # written here, whatever it carried before.
    $wanted = New-Object System.Security.AccessControl.DirectorySecurity
    $wanted.SetAccessRuleProtection($true, $false)
    foreach ($grant in @(
        @{ Sid = $script:SidSystem;         Rights = [System.Security.AccessControl.FileSystemRights]::FullControl },
        @{ Sid = $script:SidAdministrators; Rights = [System.Security.AccessControl.FileSystemRights]::FullControl },
        @{ Sid = $OwnerSid;                 Rights = [System.Security.AccessControl.FileSystemRights]::Modify }
    )) {
        $identity = New-Object System.Security.Principal.SecurityIdentifier($grant.Sid)
        $wanted.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($identity, $grant.Rights, $inherit, $none, $allow)))
    }
    # Compared entry by entry, not as SDDL text: the descriptor read back from disk carries
    # the auto-inherited flag (D:PAI) that one built in memory does not (D:P), and a string
    # comparison called every correct directory changed.
    $describe = {
        param($security)
        $entries = @($security.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) | ForEach-Object {
            "$($_.IdentityReference.Value)|$([int]$_.FileSystemRights)|$([int]$_.InheritanceFlags)|$([int]$_.PropagationFlags)|$($_.AccessControlType)|$($_.IsInherited)"
        } | Sort-Object)
        "protected=$($security.AreAccessRulesProtected);" + ($entries -join ";")
    }
    $access = [System.Security.AccessControl.AccessControlSections]::Access
    $before = $null
    $current = $null
    try {
        $onDisk = Get-SecurityDescriptor -Path $Path -Sections $access
        $before = $onDisk.GetSecurityDescriptorSddlForm($access)
        $current = & $describe $onDisk
    }
    catch { $before = "unreadable: $($_.Exception.Message)" }

    $changed = ($current -ne (& $describe $wanted))
    if ($changed) {
        ([System.IO.DirectoryInfo]$Path).SetAccessControl($wanted)
    }

    # The owner, AFTER the DACL. An elevated run hands the directory to Administrators: the
    # account that created it - the companion's, or anybody's who got there first - keeps
    # WRITE_DAC over it for as long as it owns it, whatever the DACL above says.
    $ownerChanged = $false
    $newOwner = ""
    if ($Elevated) {
        $owner = (Get-AclReport -Path $Path).Owner
        if ($script:TrustedOwnerSids -notcontains $owner) {
            try {
                $ownership = New-Object System.Security.AccessControl.DirectorySecurity
                $ownership.SetOwner((New-Object System.Security.Principal.SecurityIdentifier($script:SidAdministrators)))
                ([System.IO.DirectoryInfo]$Path).SetAccessControl($ownership)
            }
            catch {
                $why = $_.Exception.Message
                if ($_.Exception.InnerException) { $why = $_.Exception.InnerException.Message }
                throw "the owner of $Path is $owner and could not be changed to Administrators ($why). Its permissions were written; its owner can still change them."
            }
            $ownerChanged = $true
            $newOwner = $script:SidAdministrators
        }
    }

    # A trail written while the directory was in some other state may carry explicit entries
    # (or an owner) of its own. Walked entry by entry - never `icacls /T`, which follows
    # junctions, and never into a reparse point.
    $skipped = @()
    if ($changed -or $ownerChanged) {
        $walk = Reset-ChildAclToInherit -Directory $Path -NewOwnerSid $newOwner
        $skipped = @($walk.SkippedReparsePoints)
        foreach ($failure in @($walk.Failures)) {
            Write-Warning "could not reset the ACL of $failure; the report below is authoritative"
        }
        foreach ($link in $skipped) {
            Write-Warning "$link is a reparse point inside the companion's audit directory; it was neither entered nor changed. Nothing the agent writes belongs behind one - look at where it leads and remove it."
        }
    }

    $after = Get-CompanionAuditDirectoryReport -Path $Path -OwnerSid $OwnerSid
    if (-not $after.OwnerCanWrite -or -not $after.OwnerTrusted) {
        $advice = if ($Elevated) { "" } else { " Run the installer elevated: only an elevated run can take a directory away from the account that owns it." }
        throw "the companion's audit directory is not as it must be after the repair: $($after.Problems -join '; ').$advice"
    }

    return [pscustomobject]@{
        Path                 = $Path
        Created              = $created
        Changed              = $changed
        OwnerChanged         = $ownerChanged
        SkippedReparsePoints = @($skipped)
        Before               = $before
        After                = $after
    }
}

function Get-CompanionAuditDirectoryVerdict {
    <#
    .SYNOPSIS
        The verifier's row "audit dir writable by owner", as Criterion / Status / Evidence
        in the PROVEN_REAL / NOT_YET_PROVEN vocabulary. Read-only.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$OwnerSid,
        $AclReport = $null
    )

    $criterion = "audit dir writable by owner"
    $report = Get-CompanionAuditDirectoryReport -Path $Path -OwnerSid $OwnerSid -AclReport $AclReport
    # Writable by the owner AND not re-permissionable by anyone else: a directory owned by
    # another account, or one that is a junction, passes the first and means nothing.
    if ($report.OwnerCanWrite -and $report.OwnerTrusted -and -not $report.IsReparsePoint) {
        $evidence = "$OwnerSid holds Modify on $Path, inherited by the trail; the directory is owned by $($report.Owner) ($($report.Sddl))"
        if (@($report.OtherWriters).Count -gt 0) {
            $evidence += "; NOTE other principals can write there too: $($report.OtherWriters -join ', ') - rerun scripts\install-device-service.ps1 to state the DACL"
        }
        return [pscustomobject]@{ Criterion = $criterion; Status = "PROVEN_REAL"; Evidence = $evidence }
    }

    $remedy = "rerun scripts\install-device-service.ps1 elevated: it creates or repairs this directory before the runtime starts"
    if (-not $report.Exists) {
        $remedy = "start the companion once, or $remedy"
    }
    return [pscustomobject]@{
        Criterion = $criterion
        Status    = "NOT_YET_PROVEN"
        Evidence  = "$($report.Problems -join '; ') - the companion (running as $OwnerSid) cannot write an audit trail that can be relied on, so no browser_worker_started row counts; $remedy"
    }
}

function Restore-MachineStateAcl {
    <#
    .SYNOPSIS
        Repair exactly the named machine-state files whose DACLs are empty or unreadable —
        no recursion, no takeown, no principal beyond SYSTEM and Administrators.

    .DESCRIPTION
        Built for a precisely diagnosed state: `state.json` was rewritten by the old agent
        binary (inherited ACEs only) and the pre-fix `/T` icacls then stripped inherited
        ACEs tree-wide, leaving the file with a protected EMPTY DACL that denied even an
        elevated administrator. `device.key` survived because its ACEs were explicit.

        The elevated caller can rewrite these descriptors because Administrators own the
        files (WRITE_DAC through ownership) — the same recovery lever the whole model
        already depends on. Files that are readable and non-empty are left alone, so the
        function is idempotent and safe to run unconditionally as a pre-read guard.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$DataDir)

    $repaired = @()
    foreach ($entry in @(
        @{ Name = "state.json";        SystemRights = [System.Security.AccessControl.FileSystemRights]::FullControl },
        @{ Name = "idempotency.json";  SystemRights = [System.Security.AccessControl.FileSystemRights]::FullControl },
        @{ Name = "device.key";        SystemRights = [System.Security.AccessControl.FileSystemRights]::Read }
    )) {
        $path = Join-Path $DataDir $entry.Name
        if (-not (Test-Path -LiteralPath $path)) { continue }

        $damaged = $false
        try {
            $acl = Get-SecurityDescriptor -Path $path
            if (@($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])).Count -eq 0) {
                $damaged = $true
            }
        }
        catch {
            $damaged = $true
        }
        if (-not $damaged) { continue }

        $security = New-Object System.Security.AccessControl.FileSecurity
        $security.SetAccessRuleProtection($true, $false)
        $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            (New-Object System.Security.Principal.SecurityIdentifier($script:SidSystem)),
            $entry.SystemRights, [System.Security.AccessControl.AccessControlType]::Allow)))
        $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            (New-Object System.Security.Principal.SecurityIdentifier($script:SidAdministrators)),
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            [System.Security.AccessControl.AccessControlType]::Allow)))
        Set-SecurityDescriptor -Path $path -Security $security
        $repaired += $entry.Name
    }

    return @($repaired)
}

function Get-MachineStateDocument {
    <#
    .SYNOPSIS
        Read a non-secret machine-state file (default state.json) the way an elevated
        updater is allowed to: existence decided by directory listing, DACL guarded,
        denial loud. Returns the raw text, or $null when the file genuinely does not exist.

    .DESCRIPTION
        Two real hazards this closes, both from the state.json empty-DACL incident:

        - `Test-Path`/`File.Exists` return FALSE for an access-DENIED file. A script asking
          "is the device enrolled?" through Test-Path would read damage as absence and
          proceed to re-enroll — the one outcome the recovery rules forbid. Existence is
          therefore decided by listing the parent directory, which an elevated
          administrator can always do on the machine-data tree.
        - a damaged DACL made the read fail cryptically deep in a flow. The targeted
          repair guard runs first, and a read that still fails throws a diagnosis instead
          of an access-denied one-liner.

        Only for the non-secret state documents (ADR-0029). Never used for device.key.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$DataDir,
        [string]$Name = "state.json"
    )

    if ($Name -eq "device.key") {
        throw "Get-MachineStateDocument reads non-secret state documents only, never key material"
    }

    if (-not (Test-Path -LiteralPath $DataDir)) { return $null }
    if (@([System.IO.Directory]::GetFiles($DataDir, $Name)).Count -eq 0) {
        return $null   # genuinely absent — not denied, because the listing itself succeeded
    }

    [void](Restore-MachineStateAcl -DataDir $DataDir)

    $path = Join-Path $DataDir $Name
    try {
        return [System.IO.File]::ReadAllText($path)
    }
    catch [System.UnauthorizedAccessException] {
        $why = "$Name exists but this process cannot read it even after the targeted ACL guard. " +
               "It is service-owned machine material: run from an ELEVATED console so the Administrators " +
               "ACE is effective. Do not delete or re-create the file - it may hold the device enrollment."
        throw $why
    }
}

function Protect-DeviceKeyAcl {
    <#
    .SYNOPSIS
        The device key's explicit protection: SYSTEM read-only, Administrators full, nothing
        else. Mirrors MachineMaterial.Protect(Secret) in the agent, for the scripts' side.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$KeyPath)

    $security = New-Object System.Security.AccessControl.FileSecurity
    $security.SetAccessRuleProtection($true, $false)
    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        (New-Object System.Security.Principal.SecurityIdentifier($script:SidSystem)),
        [System.Security.AccessControl.FileSystemRights]::Read,
        [System.Security.AccessControl.AccessControlType]::Allow)))
    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        (New-Object System.Security.Principal.SecurityIdentifier($script:SidAdministrators)),
        [System.Security.AccessControl.FileSystemRights]::FullControl,
        [System.Security.AccessControl.AccessControlType]::Allow)))
    Set-SecurityDescriptor -Path $KeyPath -Security $security
}

function New-StagingDirectory {
    <#
    .SYNOPSIS
        A clean, administrator-only staging directory beside the live install.

    .DESCRIPTION
        Beside it rather than in %TEMP% so the deploy is a rename on the same volume — an
        atomic operation — rather than a copy that can be interrupted halfway.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Root, [Parameter(Mandatory = $true)][string]$Name)

    $staging = Join-Path (Join-Path $Root ".staging") $Name
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    return $staging
}

function Resume-InterruptedDeployment {
    <#
    .SYNOPSIS
        Put back anything a previous interrupted run left mid-swap.

    .DESCRIPTION
        The swap is: live -> .previous, staging -> live, delete .previous. An interruption
        between the first two steps leaves the component missing and its content sitting in
        .previous. Restoring it means a killed installer costs nothing: the machine goes back
        to the last state that worked.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Root, [Parameter(Mandatory = $true)][string[]]$Components)

    $restored = New-Object System.Collections.ArrayList
    foreach ($component in $Components) {
        $live = Join-Path $Root $component
        $previous = Join-Path (Join-Path $Root ".previous") $component

        if ((Test-Path -LiteralPath $previous) -and -not (Test-Path -LiteralPath $live)) {
            Move-Item -LiteralPath $previous -Destination $live -Force
            [void]$restored.Add($component)
        }
        elseif (Test-Path -LiteralPath $previous) {
            # Both exist: the swap completed and only the cleanup was interrupted.
            Remove-Item -LiteralPath $previous -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    $staging = Join-Path $Root ".staging"
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }

    # Plain @( ), paired with @(Call) at every call site. See the cardinality note at the top
    # of this file: this return unrolls, so a caller that forgets the wrapper gets $null and
    # StrictMode throws at its next `.Count` — which is how the owner's rerun failed, and is
    # the failure mode worth having, because it is loud.
    return @($restored)
}

function Invoke-InstallRecovery {
    <#
    .SYNOPSIS
        Everything the installer does before it stages anything: ensure the root exists, undo
        an interrupted swap, and make a previously hardened tree administrable again.

    .DESCRIPTION
        This is a function rather than a block inside the installer script for one reason:
        the installer's top-level flow had no tests, and that is where the real machine kept
        failing. The whole sequence is now callable, and `installer-strictmode.tests.ps1`
        drives it with nothing to restore, with one component to restore and with two —
        the cardinalities that broke it.

        Returns an object whose Messages, Restored and Actions are always arrays, so a caller
        can count, join or iterate them without knowing how many there were.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string[]]$Components,
        [switch]$Quiet
    )

    $messages = New-Object System.Collections.ArrayList

    New-Item -ItemType Directory -Force -Path $Root | Out-Null

    $restored = @(Resume-InterruptedDeployment -Root $Root -Components $Components)
    if (@($restored).Count -gt 0) {
        [void]$messages.Add("restored $($restored -join ', ') from an interrupted previous run")
    }

    $repair = Repair-InstallTreeAcl -Root $Root -Quiet:$Quiet
    $actions = @($repair.Actions)
    if ($repair.Repaired) {
        [void]$messages.Add("recovered the existing install: $($actions -join '; ')")
    }

    # Plain @( ) here, not ,@( ): a property assignment does not unroll, so the unary comma
    # would wrap the array in another array and every count would read 1. The comma belongs
    # on `return` statements, which do unroll — and nowhere else.
    return [pscustomobject]@{
        Root     = $Root
        Restored = @($restored)
        Actions  = @($actions)
        Repaired = [bool]$repair.Repaired
        Messages = @($messages)
    }
}

function Publish-StagedDirectory {
    <#
    .SYNOPSIS
        Swap a staged directory into place, atomically, with rollback on failure.

    .DESCRIPTION
        Either the component ends up as the staged content, or it ends up as whatever it was
        before. There is no state in which it is half of each — that is the difference
        between an installer that can be interrupted and one that can leave a machine broken.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Component,
        [Parameter(Mandatory = $true)][string]$StagedPath
    )

    $live = Join-Path $Root $Component
    $previousRoot = Join-Path $Root ".previous"
    $previous = Join-Path $previousRoot $Component

    if (-not (Test-Path -LiteralPath $StagedPath)) {
        throw "nothing staged for '$Component' at $StagedPath"
    }

    New-Item -ItemType Directory -Force -Path $previousRoot | Out-Null
    if (Test-Path -LiteralPath $previous) {
        Remove-Item -LiteralPath $previous -Recurse -Force
    }

    $movedAside = $false
    if (Test-Path -LiteralPath $live) {
        Move-Item -LiteralPath $live -Destination $previous -Force
        $movedAside = $true
    }

    try {
        Move-Item -LiteralPath $StagedPath -Destination $live -Force
    }
    catch {
        if ($movedAside -and -not (Test-Path -LiteralPath $live)) {
            Move-Item -LiteralPath $previous -Destination $live -Force
        }
        throw "deploying '$Component' failed and the previous install was restored: $($_.Exception.Message)"
    }

    if ($movedAside) {
        Remove-Item -LiteralPath $previous -Recurse -Force -ErrorAction SilentlyContinue
    }

    return $live
}

function Write-JsonFile {
    <#
    .SYNOPSIS
        Write UTF-8 without a BOM, replacing whatever is there.

    .DESCRIPTION
        Through .NET rather than Set-Content, which in Windows PowerShell 5.1 adds a BOM and
        re-encodes non-ASCII — that round-trip has already corrupted files in this repository.
        The read-only attribute is cleared first: a config file left read-only by a previous
        run would otherwise fail the write for a reason unrelated to permissions.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Content)

    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -LiteralPath $Path -Force
        if ($item.Attributes.HasFlag([System.IO.FileAttributes]::ReadOnly)) {
            $item.Attributes = $item.Attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)
        }
    }

    [System.IO.File]::WriteAllText($Path, $Content, (New-Object System.Text.UTF8Encoding($false)))
}
