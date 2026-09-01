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
