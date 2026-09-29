<#
.SYNOPSIS
    The owner's Chrome enrollment RECORD: writing it, protecting it, revoking it - the part
    of scripts\browser\enroll-owner-chrome.ps1 that can be tested, because the rest of that
    script closes and relaunches the owner's browser.

.DESCRIPTION
    2026-09-29, on the owner's machine. The enrollment script was run NON-elevated after an
    ELEVATED run had created C:\ProgramData\PagentOS\browser\owner-enrollment.json. It
    printed "recorded: <path>", then "Access is denied", then "The agent may now attach to
    your Chrome".

    The record an elevated run leaves behind is owned by BUILTIN\Administrators and carries
    a protected DACL of SYSTEM:(R,W) and the owner's account:(R,W). Reproduced in a sandbox
    and pinned by scripts\tests\owner-enrollment.tests.ps1, as the owner's non-elevated
    account:

      writing the content   SUCCEEDS  (R,W) is enough to overwrite in place
      changing the DACL     DENIED    it takes WRITE_DAC, which (R,W) does not contain and
                                      only the file's owner holds implicitly; icacls exits 5
      deleting the file     DENIED    (R,W) has no DELETE, and ProgramData gives ordinary
                                      users no delete-child - so -Revoke could not revoke

    So the denied call was the icacls one, not the write; its output went to Out-Null and
    its exit code was never read; and "recorded:" was printed between the two, before
    anything had been checked.

    What this module does about it:

      - the owner's grant is Modify, not (R,W). Modify contains DELETE, so a record the
        owner wrote is a record the owner can revoke;
      - the DACL is written through .NET, where a refusal is an exception, not an exit code
        somebody has to remember to read. It is written BEFORE the content, so a new
        endpoint never sits, even briefly, in a file that inherited ProgramData's readers;
      - a DACL that cannot be changed from here is accepted only when it is already safe
        (SYSTEM, Administrators and the owner, nobody else, and the owner can write) - and
        then the result SAYS it was kept. Anything else stops the run and asks for
        elevation, before a byte of the record is written;
      - the write is read back and compared byte for byte; the caller prints "recorded:"
        only when this returns;
      - revoking deletes the file, and where it cannot be deleted EMPTIES it: a record with
        no enrollments is what the worker refuses on (browser_agent/worker.py), which is
        what revoking means. Where it can do neither, it fails and says so.

    Nothing here starts, stops or looks at a browser.
#>

Set-StrictMode -Version Latest

$script:EnrollmentSidSystem = "S-1-5-18"
$script:EnrollmentSidAdministrators = "S-1-5-32-544"
#: OWNER RIGHTS. An entry for it grants nobody anything new: it states what the file's
#: owner - an administrator or the owner's own account, here - holds in place of the
#: implicit READ_CONTROL + WRITE_DAC.
$script:EnrollmentSidOwnerRights = "S-1-3-4"
$script:EnrollmentUtf8 = New-Object System.Text.UTF8Encoding($false)

function Get-ElevationAdvice {
    <#  The one sentence every refusal ends with, so the owner is never left with "Access is denied".  #>
    param([Parameter(Mandatory = $true)][string]$Path)
    return "Run this once from an ELEVATED PowerShell (Start menu > Windows PowerShell > Run as administrator): " +
           "the record at $Path was created by an elevated session and is owned by Administrators, so only an elevated run can correct it. " +
           "After that one run the owner's ordinary session can write and revoke it."
}

function Get-OwnerEnrollmentAclState {
    <#
    .SYNOPSIS
        Is the record's DACL the intended one, merely a safe one, or neither?

    .OUTPUTS
        Intended  - protected; SYSTEM (R,W) and the owner Modify; nothing else
        Safe      - protected; only SYSTEM, Administrators, the owner and OWNER RIGHTS appear;
                    the owner can read and write; no deny entry against the owner
        Problems  - why it is not Safe (empty when it is)
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$OwnerSid
    )

    $access = [System.Security.AccessControl.AccessControlSections]::Access
    $security = ([System.IO.FileInfo]$Path).GetAccessControl($access)
    $problems = New-Object System.Collections.ArrayList

    if (-not $security.AreAccessRulesProtected) {
        [void]$problems.Add("it inherits its permissions from the directory, whose readers include every local user")
    }

    $synchronize = [int][System.Security.AccessControl.FileSystemRights]::Synchronize
    $readWrite = [int]([System.Security.AccessControl.FileSystemRights]::Read -bor [System.Security.AccessControl.FileSystemRights]::Write) -band (-bnot $synchronize)
    $modify = [int][System.Security.AccessControl.FileSystemRights]::Modify
    $ownerRights = 0
    $systemRights = 0
    $others = 0
    foreach ($ace in $security.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])) {
        $sid = $ace.IdentityReference.Value
        $rights = [int]$ace.FileSystemRights -band (-bnot $synchronize)
        if ($ace.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow) {
            if ($sid -eq $OwnerSid) { [void]$problems.Add("a deny entry against $OwnerSid takes away $($ace.FileSystemRights)") }
            continue
        }
        if ($sid -eq $OwnerSid) { $ownerRights = $ownerRights -bor $rights }
        elseif ($sid -eq $script:EnrollmentSidSystem) { $systemRights = $systemRights -bor $rights }
        elseif ($sid -eq $script:EnrollmentSidAdministrators -or $sid -eq $script:EnrollmentSidOwnerRights) { $others++ }
        else {
            $others++
            [void]$problems.Add("$sid is allowed $($ace.FileSystemRights) on a record that names the endpoint driving a signed-in browser")
        }
    }
    if (($ownerRights -band $readWrite) -ne $readWrite) {
        [void]$problems.Add("$OwnerSid cannot both read and write it")
    }

    $safe = (@($problems).Count -eq 0)
    return [pscustomobject]@{
        Intended = ($safe -and $others -eq 0 -and $ownerRights -eq $modify -and $systemRights -eq $readWrite)
        Safe     = $safe
        Problems = @($problems)
        Sddl     = $security.GetSecurityDescriptorSddlForm($access)
    }
}

function Set-OwnerEnrollmentAcl {
    <#
    .SYNOPSIS
        Give the record its intended DACL, or say truthfully why it still has another.

    .OUTPUTS
        Acl   - "already" (nothing to do), "set" (written), "kept" (could not be changed
                from this account, and what is there is safe)
        Note  - for "kept": what is there, and what would change it
        Throws when the DACL can neither be changed nor accepted.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$OwnerSid
    )

    $state = Get-OwnerEnrollmentAclState -Path $Path -OwnerSid $OwnerSid
    if ($state.Intended) {
        return [pscustomobject]@{ Acl = "already"; Note = ""; Sddl = $state.Sddl }
    }

    $allow = [System.Security.AccessControl.AccessControlType]::Allow
    $security = New-Object System.Security.AccessControl.FileSecurity
    $security.SetAccessRuleProtection($true, $false)
    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        (New-Object System.Security.Principal.SecurityIdentifier($script:EnrollmentSidSystem)),
        ([System.Security.AccessControl.FileSystemRights]::Read -bor [System.Security.AccessControl.FileSystemRights]::Write), $allow)))
    # Modify, where the script used to grant (R,W): (R,W) has no DELETE, and a record its
    # owner cannot delete is an authorization they cannot revoke.
    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        (New-Object System.Security.Principal.SecurityIdentifier($OwnerSid)),
        [System.Security.AccessControl.FileSystemRights]::Modify, $allow)))

    $refusal = $null
    try {
        ([System.IO.FileInfo]$Path).SetAccessControl($security)
    }
    catch {
        # WRITE_DAC is held by the file's owner and by whoever the DACL gives it to. For a
        # record an elevated run created, the owner's ordinary session is neither.
        $refusal = $_.Exception.Message
        if ($_.Exception.InnerException) { $refusal = $_.Exception.InnerException.Message }
    }

    if ($null -eq $refusal) {
        $after = Get-OwnerEnrollmentAclState -Path $Path -OwnerSid $OwnerSid
        if (-not $after.Intended) {
            throw "the permissions of $Path were written and do not read back as intended ($($after.Sddl)): $($after.Problems -join '; ')"
        }
        return [pscustomobject]@{ Acl = "set"; Note = ""; Sddl = $after.Sddl }
    }

    if ($state.Safe) {
        return [pscustomobject]@{
            Acl  = "kept"
            Note = "the permissions of $Path could not be changed from this session ($refusal) and were kept as they are: $($state.Sddl). " +
                   "They are safe - only SYSTEM, Administrators and the owner appear - but the owner holds less than Modify, so the file can be emptied and not deleted. " +
                   (Get-ElevationAdvice -Path $Path)
            Sddl = $state.Sddl
        }
    }

    throw "the enrollment record cannot be protected: $($state.Problems -join '; '), and its permissions could not be changed from this session ($refusal). Nothing was written. " +
          (Get-ElevationAdvice -Path $Path)
}

function Test-OwnerEnrollmentWritable {
    <#
    .SYNOPSIS
        Can this session write the record? Asked BEFORE the script closes the owner's
        Chrome, so a run that cannot finish changes nothing at all.

    .DESCRIPTION
        An existing record is opened for writing and closed again, without truncating it.
        For a record that does not exist yet, the nearest existing directory is probed with
        a file of its own that is removed again; no directory is created by asking.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    if (Test-Path -LiteralPath $Path -PathType Container) {
        return [pscustomobject]@{ Ok = $false; Reason = "$Path is a directory, not a record." }
    }

    # Decided by listing, not by Test-Path: Test-Path answers FALSE for a file it is denied.
    $directory = Split-Path -Parent $Path
    $exists = $false
    if (Test-Path -LiteralPath $directory -PathType Container) {
        try { $exists = (@([System.IO.Directory]::GetFiles($directory, (Split-Path -Leaf $Path))).Count -gt 0) } catch { $exists = $true }
    }

    if ($exists) {
        try {
            $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite)
            $stream.Dispose()
            return [pscustomobject]@{ Ok = $true; Reason = "" }
        }
        catch {
            $why = $_.Exception.Message
            if ($_.Exception.InnerException) { $why = $_.Exception.InnerException.Message }
            return [pscustomobject]@{ Ok = $false; Reason = "this session cannot write the enrollment record ($why). " + (Get-ElevationAdvice -Path $Path) }
        }
    }

    $probeIn = $directory
    while ($probeIn -and -not (Test-Path -LiteralPath $probeIn -PathType Container)) {
        $probeIn = Split-Path -Parent $probeIn
    }
    if (-not $probeIn) {
        return [pscustomobject]@{ Ok = $false; Reason = "no existing directory above $Path." }
    }
    $probe = Join-Path $probeIn ".pagentos-enrollment-probe-$([guid]::NewGuid().ToString('N'))"
    try {
        [System.IO.File]::WriteAllBytes($probe, (New-Object byte[] 0))
        [System.IO.File]::Delete($probe)
        return [pscustomobject]@{ Ok = $true; Reason = "" }
    }
    catch {
        $why = $_.Exception.Message
        if ($_.Exception.InnerException) { $why = $_.Exception.InnerException.Message }
        return [pscustomobject]@{ Ok = $false; Reason = "this session cannot create a file in $probeIn ($why). " + (Get-ElevationAdvice -Path $Path) }
    }
}

function Write-OwnerEnrollmentRecord {
    <#
    .SYNOPSIS
        Write the record, protect it, read it back. Returns only when all three are true;
        throws - having written nothing it can avoid writing - when any is not.

    .DESCRIPTION
        UTF-8 WITHOUT a byte-order mark: the Python side that reads this file rejected one
        outright the first time this ran for real.

        Order: the file exists (created empty if new), then its DACL, then its content,
        then the read-back. The caller prints "recorded:" after this returns and nowhere
        else.

    .PARAMETER ReadBack
        How the file is read for verification. Injectable so that a disk which answers
        something else than what was written is a case the tests can make.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Json,
        [string]$OwnerSid = ([System.Security.Principal.WindowsIdentity]::GetCurrent()).User.Value,
        [scriptblock]$ReadBack = { param($p) [System.IO.File]::ReadAllBytes($p) }
    )

    $writable = Test-OwnerEnrollmentWritable -Path $Path
    if (-not $writable.Ok) {
        throw "the enrollment was NOT recorded: $($writable.Reason)"
    }

    $directory = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $directory)) {
        $null = New-Item -ItemType Directory -Path $directory -Force
    }

    $createdHere = -not (Test-Path -LiteralPath $Path)
    if ($createdHere) {
        [System.IO.File]::WriteAllBytes($Path, (New-Object byte[] 0))
    }

    try {
        $acl = Set-OwnerEnrollmentAcl -Path $Path -OwnerSid $OwnerSid
    }
    catch {
        # Nothing of the record has been written yet; an empty file made a moment ago is
        # not left behind to be mistaken for one.
        if ($createdHere) { try { [System.IO.File]::Delete($Path) } catch { } }
        throw
    }

    $bytes = $script:EnrollmentUtf8.GetBytes($Json)
    [System.IO.File]::WriteAllBytes($Path, $bytes)

    $onDisk = [byte[]](& $ReadBack $Path)
    $same = ($null -ne $onDisk) -and ($onDisk.Length -eq $bytes.Length)
    if ($same) {
        for ($i = 0; $i -lt $bytes.Length; $i++) {
            if ($onDisk[$i] -ne $bytes[$i]) { $same = $false; break }
        }
    }
    if (-not $same) {
        $length = if ($null -eq $onDisk) { 0 } else { $onDisk.Length }
        throw "the enrollment was NOT recorded: $Path does not read back as written ($($bytes.Length) bytes written, $length read back, contents differ). Verification failed; treat the record as untrustworthy and run this again."
    }

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $digest = -join ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString("x2") }) }
    finally { $sha.Dispose() }

    return [pscustomobject]@{
        Path     = $Path
        Bytes    = $bytes.Length
        Sha256   = $digest
        Verified = $true
        Acl      = $acl.Acl
        AclNote  = $acl.Note
        Sddl     = $acl.Sddl
    }
}

function Remove-OwnerEnrollmentRecord {
    <#
    .SYNOPSIS
        Revoke: delete the record, or - where this session may write it and not delete it -
        empty it. Either way the worker finds no enrollment and refuses profile 'owner'.

    .OUTPUTS
        Outcome - "absent", "removed" or "emptied"; Message - what to tell the owner.
        Throws when the authorization could NOT be revoked, so that nothing prints
        "revoked" about a record that still authorizes.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    $directory = Split-Path -Parent $Path
    $exists = $false
    if (Test-Path -LiteralPath $directory -PathType Container) {
        # By listing: Test-Path would read a denied record as an absent one, and "nothing to
        # revoke" about an authorization that is still in force is the worst answer here.
        $exists = (@([System.IO.Directory]::GetFiles($directory, (Split-Path -Leaf $Path))).Count -gt 0)
    }
    if (-not $exists) {
        return [pscustomobject]@{ Outcome = "absent"; Message = "Nothing to revoke; $Path does not exist." }
    }

    $deleteRefusal = $null
    try {
        [System.IO.File]::Delete($Path)
    }
    catch {
        $deleteRefusal = $_.Exception.Message
        if ($_.Exception.InnerException) { $deleteRefusal = $_.Exception.InnerException.Message }
    }
    if ($null -eq $deleteRefusal) {
        if (@([System.IO.Directory]::GetFiles($directory, (Split-Path -Leaf $Path))).Count -gt 0) {
            throw "the enrollment was NOT revoked: $Path is still there after the delete returned."
        }
        return [pscustomobject]@{ Outcome = "removed"; Message = "Enrollment revoked: $Path removed." }
    }

    $empty = $script:EnrollmentUtf8.GetBytes('{"enrollments": []}')
    try {
        [System.IO.File]::WriteAllBytes($Path, $empty)
        $onDisk = [System.IO.File]::ReadAllBytes($Path)
        if ((-join ($onDisk | ForEach-Object { [char]$_ })) -ne '{"enrollments": []}') {
            throw "it does not read back as the empty record"
        }
    }
    catch {
        $why = $_.Exception.Message
        if ($_.Exception.InnerException) { $why = $_.Exception.InnerException.Message }
        throw "the enrollment was NOT revoked: $Path could be neither deleted ($deleteRefusal) nor emptied ($why); the agent can STILL attach to your Chrome. " +
              (Get-ElevationAdvice -Path $Path)
    }

    return [pscustomobject]@{
        Outcome = "emptied"
        Message = "Enrollment revoked: $Path now holds no enrollment. The file itself could not be deleted from this session ($deleteRefusal); " +
                  "an elevated run of this script with -Revoke removes it."
    }
}
