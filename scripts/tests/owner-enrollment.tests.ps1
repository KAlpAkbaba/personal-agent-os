<#
.SYNOPSIS
    Tests for the owner's Chrome enrollment RECORD (scripts\lib\OwnerEnrollment.ps1), under
    Windows PowerShell 5.1, without elevation and without Chrome.

.DESCRIPTION
    2026-09-29, on the owner's machine. scripts\browser\enroll-owner-chrome.ps1 was run
    NON-elevated after an ELEVATED run had created
    C:\ProgramData\PagentOS\browser\owner-enrollment.json, and answered "Access is denied"
    about that file - having already printed "recorded: <path>" and going on to print "The
    agent may now attach to your Chrome".

    The record the elevated run left behind is owned by BUILTIN\Administrators and carries
    a protected DACL of SYSTEM:(R,W) and the owner's account:(R,W). Measured in a sandbox
    (and asserted below), as the owner's non-elevated account:

      - writing the content SUCCEEDS: (R,W) is enough to overwrite in place;
      - changing the DACL is DENIED: that takes WRITE_DAC, which (R,W) does not contain and
        which only the file's owner holds implicitly - icacls exits 5, and its output was
        piped to Out-Null and its exit code never read;
      - deleting the file is DENIED as well: (R,W) has no DELETE and ProgramData gives
        ordinary users no delete-child - so -Revoke could not revoke.

    HOW THE FIXTURE STANDS IN FOR "NOT THE OWNER". A non-elevated account cannot make
    Administrators the owner of a file (that takes SeRestorePrivilege), so the fixture adds
    an OWNER RIGHTS entry (S-1-3-4) granting read-permissions only. That entry REPLACES the
    owner's implicit READ_CONTROL + WRITE_DAC, which leaves this account with exactly the
    access the owner's account has on the real file: read and write, no WRITE_DAC, no
    DELETE. The first test case proves the fixture behaves that way before anything relies
    on it.

    WHAT THAT EMULATION IS, EXACTLY, AND WHAT IT CANNOT SHOW (security review of 2a2f7f95,
    which made the file's owner and the content of OWNER RIGHTS part of "safe"):

      - on disk, the fixture is: owner = THIS account; protected DACL SYSTEM:(R,W), this
        account:(R,W), OWNER RIGHTS:(read permissions). The real record is: owner =
        BUILTIN\Administrators; protected DACL SYSTEM:(R,W), the owner's account:(R,W);
      - the ACCESS this account gets is the same in both (measured: write data granted,
        WRITE_DAC and DELETE denied), so every outcome that depends on access - the write
        succeeding, the permissions call refused, "kept", the revoke emptying instead of
        deleting - is measured for real;
      - the OWNER FIELD is not the same, and nothing on disk here can make it so. The
        fixture passes the owner check because this account IS the owner SID it is asked
        about; the real record passes it because Administrators is a trusted owner. That
        second reason, and the refusal of a record owned by a stranger, are asserted on
        descriptors built in memory (where SetOwner needs no privilege) and, for the write
        path, through an injected descriptor reader;
      - so what NO test here shows is a real file, owned by another account, being refused
        or taken over. The take-over itself (an elevated run setting the owner to
        Administrators) runs only elevated and is not exercised by this suite at all;
      - OWNER RIGHTS with read-permissions only is inside what the review allows that entry
        to carry ("at most Read/ReadControl"), which is why the fixture could keep it.

    THE SCRIPT ITSELF IS NEVER RUN HERE: it closes and relaunches Chrome. Its wiring to the
    library is read with the PowerShell parser instead.

    Run: powershell -NoProfile -File scripts\tests\owner-enrollment.tests.ps1
    Exit code is the number of failed assertions.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\OwnerEnrollment.ps1")

$script:Failures = 0
$script:Passes = 0
$script:Sandbox = Join-Path $env:TEMP "pagentos-enrollment-tests-$([guid]::NewGuid().ToString('N'))"
$script:RestrictedDirectories = New-Object System.Collections.ArrayList
$script:Me = ([System.Security.Principal.WindowsIdentity]::GetCurrent()).User
$script:TokenIsAdmin = ([System.Security.Principal.WindowsPrincipal]::new(
    [System.Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole(
    [System.Security.Principal.WindowsBuiltInRole]::Administrator)

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

function Assert-Throws {
    param([scriptblock]$Body, [string]$Pattern, [string]$Because)
    $message = $null
    try { & $Body | Out-Null } catch { $message = $_.Exception.Message }
    if ($null -eq $message) { throw "$Because`n          expected an error matching <$Pattern>, and nothing was thrown" }
    if ($message -notmatch $Pattern) { throw "$Because`n          expected an error matching <$Pattern>`n          actual  : <$message>" }
}

$script:Allow = [System.Security.AccessControl.AccessControlType]::Allow
$script:ReadWrite = [System.Security.AccessControl.FileSystemRights]::Read -bor
                    [System.Security.AccessControl.FileSystemRights]::Write -bor
                    [System.Security.AccessControl.FileSystemRights]::Synchronize

function New-FileRule {
    param([string]$Sid, [System.Security.AccessControl.FileSystemRights]$Rights)
    return New-Object System.Security.AccessControl.FileSystemAccessRule(
        (New-Object System.Security.Principal.SecurityIdentifier($Sid)), $Rights, $script:Allow)
}

function New-ElevatedCreatedRecord {
    <#
    .SYNOPSIS
        The record as the elevated run left it, in a directory shaped like
        C:\ProgramData\PagentOS\browser is for an ordinary user.
    .PARAMETER MyRights
        What this account holds on the file. (R,W) is the real machine; Read alone is a
        record this account cannot write at all.
    .PARAMETER AlsoGrant
        An extra principal on the file, for "an ACL nobody should accept".
    #>
    param(
        [string]$Name,
        [System.Security.AccessControl.FileSystemRights]$MyRights = $script:ReadWrite,
        [string]$AlsoGrant,
        [string]$Content = '{"enrollments":[{"id":"from-the-elevated-run","transport":"cdp_loopback"}]}'
    )
    $directory = Join-Path $script:Sandbox "$Name\browser"
    $file = Join-Path $directory "owner-enrollment.json"
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    [System.IO.File]::WriteAllText($file, $Content, (New-Object System.Text.UTF8Encoding($false)))

    $security = New-Object System.Security.AccessControl.FileSecurity
    $security.SetAccessRuleProtection($true, $false)
    $security.AddAccessRule((New-FileRule -Sid "S-1-5-18" -Rights $script:ReadWrite))
    $security.AddAccessRule((New-FileRule -Sid $script:Me.Value -Rights $MyRights))
    if ($AlsoGrant) { $security.AddAccessRule((New-FileRule -Sid $AlsoGrant -Rights ([System.Security.AccessControl.FileSystemRights]::Read))) }
    # OWNER RIGHTS: takes away the implicit WRITE_DAC this account has as the file's owner.
    $security.AddAccessRule((New-FileRule -Sid "S-1-3-4" -Rights ([System.Security.AccessControl.FileSystemRights]::ReadPermissions)))
    ([System.IO.FileInfo]$file).SetAccessControl($security)

    # Read, traverse, create - and no delete-child, as BUILTIN\Users has under ProgramData.
    # This account keeps its implicit WRITE_DAC on the DIRECTORY, which is how the sandbox
    # is removed afterwards; nothing under test re-permissions the directory.
    $directorySecurity = New-Object System.Security.AccessControl.DirectorySecurity
    $directorySecurity.SetAccessRuleProtection($true, $false)
    $directorySecurity.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
        $script:Me,
        ([System.Security.AccessControl.FileSystemRights]::ReadAndExecute -bor
         [System.Security.AccessControl.FileSystemRights]::CreateFiles -bor
         [System.Security.AccessControl.FileSystemRights]::CreateDirectories -bor
         [System.Security.AccessControl.FileSystemRights]::Synchronize),
        $script:Allow)))
    ([System.IO.DirectoryInfo]$directory).SetAccessControl($directorySecurity)
    [void]$script:RestrictedDirectories.Add($directory)
    return $file
}

function Get-FileSddl {
    param([string]$Path)
    return ([System.IO.FileInfo]$Path).GetAccessControl([System.Security.AccessControl.AccessControlSections]::Access).GetSecurityDescriptorSddlForm(
        [System.Security.AccessControl.AccessControlSections]::Access)
}

function Get-MyRights {
    param([string]$Path)
    $rights = 0
    $security = ([System.IO.FileInfo]$Path).GetAccessControl([System.Security.AccessControl.AccessControlSections]::Access)
    foreach ($ace in $security.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])) {
        if ($ace.IdentityReference.Value -eq $script:Me.Value -and $ace.AccessControlType -eq $script:Allow) { $rights = $rights -bor [int]$ace.FileSystemRights }
    }
    return ($rights -band (-bnot [int][System.Security.AccessControl.FileSystemRights]::Synchronize))
}

function Get-Sha256 {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

$script:NewJson = '{"enrollments":[{"id":"from-the-owner-run","transport":"cdp_loopback","endpoint":"http://127.0.0.1:19222"}]}'

New-Item -ItemType Directory -Force -Path $script:Sandbox | Out-Null

try {
    Write-Host ""
    Write-Host "the incident, measured"

    Test-Case "the elevated-created record: content can be written, the ACL cannot be changed, the file cannot be deleted" {
        if ($script:TokenIsAdmin) { Write-Host "        (elevated run: this account holds every right through Administrators; nothing to measure)"; return }
        $file = New-ElevatedCreatedRecord -Name "measured"

        [System.IO.File]::WriteAllText($file, "{}", (New-Object System.Text.UTF8Encoding($false)))
        Assert-Equal -Expected "{}" -Actual ([System.IO.File]::ReadAllText($file)) -Because "(R,W) is enough to overwrite in place - the WriteAllText was never the denied call"

        $before = Get-FileSddl -Path $file
        $previous = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $output = & "$env:SystemRoot\System32\icacls.exe" $file /inheritance:r /grant:r "$($env:USERNAME):(R,W)" /grant:r "SYSTEM:(R,W)" 2>&1
        $exit = $LASTEXITCODE
        $ErrorActionPreference = $previous
        Assert-Equal -Expected 5 -Actual $exit -Because "icacls is the call that is denied (ERROR_ACCESS_DENIED): $($output -join ' | ')"
        Assert-Equal -Expected $before -Actual (Get-FileSddl -Path $file) -Because "and it changed nothing"

        $denied = $false
        try { Remove-Item -LiteralPath $file -Force } catch { $denied = $true }
        Assert-True -Condition $denied -Because "deleting needs DELETE on the file or delete-child on the directory; the owner's account has neither"
        Assert-True -Condition (Test-Path -LiteralPath $file) -Because "so -Revoke left the record where it was"
    }

    Write-Host ""
    Write-Host "writing the record as the owner"

    Test-Case "a first record is written, verified, protected - and the owner can delete it later" {
        $file = Join-Path $script:Sandbox "fresh\browser\owner-enrollment.json"

        $result = Write-OwnerEnrollmentRecord -Path $file -Json $script:NewJson -OwnerSid $script:Me.Value

        Assert-Equal -Expected $script:NewJson -Actual ([System.IO.File]::ReadAllText($file)) -Because "the record is what was asked for"
        $bytes = [System.IO.File]::ReadAllBytes($file)
        Assert-True -Condition ($bytes[0] -eq 0x7B) -Because "no byte-order mark: the Python reader once refused the whole registry over one"
        Assert-True -Condition $result.Verified -Because "the write was read back"
        Assert-Equal -Expected (Get-Sha256 -Path $file) -Actual $result.Sha256 -Because "and what is reported is what is on disk"

        $security = ([System.IO.FileInfo]$file).GetAccessControl([System.Security.AccessControl.AccessControlSections]::Access)
        Assert-True -Condition $security.AreAccessRulesProtected -Because "the record names a loopback endpoint that drives a signed-in browser; it does not inherit ProgramData's readers"
        $principals = @($security.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]) | ForEach-Object { $_.IdentityReference.Value } | Sort-Object -Unique)
        Assert-Equal -Expected (@("S-1-5-18", $script:Me.Value) -join ",") -Actual ($principals -join ",") -Because "SYSTEM and the owner, nobody else"
        Assert-Equal -Expected ([int][System.Security.AccessControl.FileSystemRights]::Modify) -Actual (Get-MyRights -Path $file) `
            -Because "Modify, not (R,W): (R,W) has no DELETE, and a record its owner cannot delete is an authorization they cannot revoke"
        Assert-Equal -Expected "set" -Actual $result.Acl -Because "the result says what happened to the ACL"
    }

    Test-Case "the record an elevated run created is rewritten by the owner, and the unchanged ACL is REPORTED, not swallowed" {
        if ($script:TokenIsAdmin) { return }
        $file = New-ElevatedCreatedRecord -Name "rewrite"
        $before = Get-FileSddl -Path $file

        $result = Write-OwnerEnrollmentRecord -Path $file -Json $script:NewJson -OwnerSid $script:Me.Value

        Assert-Equal -Expected $script:NewJson -Actual ([System.IO.File]::ReadAllText($file)) -Because "the owner's run must be able to record the enrollment"
        Assert-True -Condition $result.Verified -Because "and it was read back"
        Assert-Equal -Expected $before -Actual (Get-FileSddl -Path $file) -Because "the ACL could not be changed from here"
        Assert-Equal -Expected "kept" -Actual $result.Acl -Because "which the result says"
        Assert-True -Condition ($result.AclNote -match "elevated") -Because "with what would change it: $($result.AclNote)"
    }

    Test-Case "a record this account cannot write is refused BEFORE anything changes, with elevation asked for by name" {
        if ($script:TokenIsAdmin) { return }
        $file = New-ElevatedCreatedRecord -Name "readonly" -MyRights ([System.Security.AccessControl.FileSystemRights]::Read -bor [System.Security.AccessControl.FileSystemRights]::Synchronize)
        $before = Get-Sha256 -Path $file

        $preflight = Test-OwnerEnrollmentWritable -Path $file
        Assert-True -Condition (-not $preflight.Ok) -Because "the preflight is what stops the script before it closes Chrome"
        Assert-True -Condition ($preflight.Reason -match "(?i)elevated") -Because "and it says what to do: $($preflight.Reason)"
        Assert-True -Condition ($preflight.Reason -match [regex]::Escape($file)) -Because "about which file: $($preflight.Reason)"

        Assert-Throws -Pattern "(?i)elevated" -Because "the write itself refuses the same way" -Body {
            Write-OwnerEnrollmentRecord -Path $file -Json $script:NewJson -OwnerSid $script:Me.Value
        }
        Assert-Equal -Expected $before -Actual (Get-Sha256 -Path $file) -Because "nothing was written"
    }

    Test-Case "an ACL that lets someone else read the record, and cannot be corrected from here, is an error - not a silent success" {
        if ($script:TokenIsAdmin) { return }
        # BUILTIN\Users may read this record, and this account cannot take that away. The old
        # code ran icacls, was denied, threw the output away and printed "recorded".
        $file = New-ElevatedCreatedRecord -Name "leaky" -AlsoGrant "S-1-5-32-545"
        $before = Get-Sha256 -Path $file

        Assert-Throws -Pattern "(?i)elevated" -Because "a failure to protect the record must stop the run" -Body {
            Write-OwnerEnrollmentRecord -Path $file -Json $script:NewJson -OwnerSid $script:Me.Value
        }
        Assert-Equal -Expected $before -Actual (Get-Sha256 -Path $file) -Because "and must stop it before the new endpoint is written into a file others can read"
    }

    Test-Case "a record that does not read back as written is not 'recorded'" {
        $file = Join-Path $script:Sandbox "verify\browser\owner-enrollment.json"
        # The reader is injected: the disk said something else than what was written.
        Assert-Throws -Pattern "(?i)read back|verif" -Because "verification is part of the write" -Body {
            Write-OwnerEnrollmentRecord -Path $file -Json $script:NewJson -OwnerSid $script:Me.Value -ReadBack { param($p) [System.Text.Encoding]::UTF8.GetBytes("{}") }
        }
    }

    Test-Case "writing twice is idempotent: the second run changes content only, and says the ACL was already right" {
        $file = Join-Path $script:Sandbox "twice\browser\owner-enrollment.json"
        [void](Write-OwnerEnrollmentRecord -Path $file -Json '{"enrollments":[]}' -OwnerSid $script:Me.Value)
        $sddl = Get-FileSddl -Path $file

        $second = Write-OwnerEnrollmentRecord -Path $file -Json $script:NewJson -OwnerSid $script:Me.Value

        Assert-Equal -Expected "already" -Actual $second.Acl -Because "nothing to change"
        Assert-Equal -Expected $sddl -Actual (Get-FileSddl -Path $file) -Because "a rerun must not drift the ACL"
        Assert-Equal -Expected $script:NewJson -Actual ([System.IO.File]::ReadAllText($file)) -Because "the newer record wins"
    }

    Write-Host ""
    Write-Host "security review of 2a2f7f95: who OWNS the record, and what OWNER RIGHTS may carry"

    # browser\ lets every local user create files. Another account can therefore get there
    # first, create owner-enrollment.json, give it a DACL that looks exactly right - and keep
    # it: whoever owns a file holds WRITE_DAC over it, whatever the DACL says, and an OWNER
    # RIGHTS entry can say "full control" out loud. Later it re-permissions the record and
    # writes owner_authorized_for_research into it. "Safe" used to look at neither.

    function New-RecordDescriptor {
        <#  A descriptor in memory: the one place a non-elevated test can name any owner it likes.  #>
        param(
            [string]$Owner,
            [System.Security.AccessControl.FileSystemRights]$OwnerAccountRights = $script:ReadWrite,
            $OwnerRightsEntry = $null
        )
        $security = New-Object System.Security.AccessControl.FileSecurity
        $security.SetAccessRuleProtection($true, $false)
        $security.SetOwner((New-Object System.Security.Principal.SecurityIdentifier($Owner)))
        $security.AddAccessRule((New-FileRule -Sid "S-1-5-18" -Rights $script:ReadWrite))
        $security.AddAccessRule((New-FileRule -Sid $script:Me.Value -Rights $OwnerAccountRights))
        if ($null -ne $OwnerRightsEntry) { $security.AddAccessRule((New-FileRule -Sid "S-1-3-4" -Rights $OwnerRightsEntry)) }
        return $security
    }

    $script:Stranger = "S-1-5-21-1111111111-2222222222-3333333333-1005"

    Test-Case "MEDIUM-2: the reviewer's PoC on disk - SYSTEM, the owner with Modify, and OWNER RIGHTS with full control - is NOT safe" {
        $file = Join-Path $script:Sandbox "poc\browser\owner-enrollment.json"
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $file) | Out-Null
        [System.IO.File]::WriteAllText($file, "{}")
        $security = New-Object System.Security.AccessControl.FileSecurity
        $security.SetAccessRuleProtection($true, $false)
        $security.AddAccessRule((New-FileRule -Sid "S-1-5-18" -Rights $script:ReadWrite))
        $security.AddAccessRule((New-FileRule -Sid $script:Me.Value -Rights ([System.Security.AccessControl.FileSystemRights]::Modify)))
        $security.AddAccessRule((New-FileRule -Sid "S-1-3-4" -Rights ([System.Security.AccessControl.FileSystemRights]::FullControl)))
        ([System.IO.FileInfo]$file).SetAccessControl($security)

        $state = Get-OwnerEnrollmentAclState -Path $file -OwnerSid $script:Me.Value

        Assert-True -Condition (-not $state.Safe) -Because "whoever owns this file may do anything to it: $($state.Sddl)"
        Assert-True -Condition (-not $state.Intended) -Because "and it is certainly not the intended DACL"
        Assert-True -Condition (@($state.Problems | Where-Object { $_ -match "OWNER RIGHTS" }).Count -gt 0) -Because "named: $($state.Problems -join '; ')"
    }

    Test-Case "MEDIUM-2: OWNER RIGHTS is accepted only when it carries read and nothing else" {
        foreach ($case in @(
            @{ Rights = [System.Security.AccessControl.FileSystemRights]::ReadPermissions; Safe = $true },
            @{ Rights = [System.Security.AccessControl.FileSystemRights]::Read;            Safe = $true },
            @{ Rights = [System.Security.AccessControl.FileSystemRights]::Write;           Safe = $false },
            @{ Rights = [System.Security.AccessControl.FileSystemRights]::ChangePermissions; Safe = $false },
            @{ Rights = [System.Security.AccessControl.FileSystemRights]::TakeOwnership;   Safe = $false },
            @{ Rights = [System.Security.AccessControl.FileSystemRights]::Delete;          Safe = $false },
            @{ Rights = [System.Security.AccessControl.FileSystemRights]::Modify;          Safe = $false },
            @{ Rights = [System.Security.AccessControl.FileSystemRights]::FullControl;     Safe = $false }
        )) {
            $state = Get-OwnerEnrollmentAclStateFromSecurity -Security (New-RecordDescriptor -Owner "S-1-5-32-544" -OwnerRightsEntry $case.Rights) -OwnerSid $script:Me.Value
            Assert-Equal -Expected $case.Safe -Actual $state.Safe -Because "OWNER RIGHTS:$($case.Rights) - $($state.Problems -join '; ')"
        }
    }

    Test-Case "MEDIUM-2: a record is safe only when the owner's account, Administrators or SYSTEM owns it" {
        foreach ($case in @(
            @{ Owner = "S-1-5-32-544";    Safe = $true;  What = "Administrators - the elevated run of 2026-09-29, with its TRUE owner" },
            @{ Owner = "S-1-5-18";        Safe = $true;  What = "SYSTEM" },
            @{ Owner = $script:Me.Value;  Safe = $true;  What = "the owner's own account" },
            @{ Owner = $script:Stranger;  Safe = $false; What = "another account" },
            @{ Owner = "S-1-5-32-545";    Safe = $false; What = "BUILTIN\Users" }
        )) {
            $state = Get-OwnerEnrollmentAclStateFromSecurity -Security (New-RecordDescriptor -Owner $case.Owner) -OwnerSid $script:Me.Value
            Assert-Equal -Expected $case.Safe -Actual $state.Safe -Because "$($case.What): $($state.Problems -join '; ')"
            Assert-True -Condition (-not $state.Intended) -Because "$($case.What): (R,W) for the owner is never the intended DACL"
            if (-not $case.Safe) {
                Assert-True -Condition (@($state.Problems | Where-Object { $_ -match "owned by $([regex]::Escape($case.Owner))" }).Count -gt 0) `
                    -Because "the owner is named: $($state.Problems -join '; ')"
            }
        }

        # The intended DACL under a stranger's ownership is not "already" right either.
        $perfect = New-RecordDescriptor -Owner $script:Stranger -OwnerAccountRights ([System.Security.AccessControl.FileSystemRights]::Modify)
        $state = Get-OwnerEnrollmentAclStateFromSecurity -Security $perfect -OwnerSid $script:Me.Value
        Assert-True -Condition (-not $state.Intended -and -not $state.Safe) -Because "a perfect DACL on a file somebody else owns is theirs to change"
    }

    Test-Case "MEDIUM-2: a record owned by another account stops the run before a byte is written, and asks for elevation" {
        $file = Join-Path $script:Sandbox "stranger\browser\owner-enrollment.json"
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $file) | Out-Null
        [System.IO.File]::WriteAllText($file, '{"enrollments":[{"id":"planted"}]}')
        $before = Get-Sha256 -Path $file
        $sddl = Get-FileSddl -Path $file

        # This account cannot make a stranger the owner of a real file, so the descriptor the
        # library READS is the stranger's; every write still goes to the real file, which is
        # how "nothing was written" is a measurement.
        $asOwnedByStranger = {
            param($path)
            New-RecordDescriptor -Owner $script:Stranger -OwnerAccountRights ([System.Security.AccessControl.FileSystemRights]::Modify)
        }

        Assert-Throws -Pattern "(?i)owned by .*elevated" -Because "not elevated, there is nothing this run may do about a stranger's file" -Body {
            Write-OwnerEnrollmentRecord -Path $file -Json $script:NewJson -OwnerSid $script:Me.Value -ReadSecurity $asOwnedByStranger -Elevated $false
        }
        Assert-Equal -Expected $before -Actual (Get-Sha256 -Path $file) -Because "the record was not written"
        Assert-Equal -Expected $sddl -Actual (Get-FileSddl -Path $file) -Because "and its permissions were not touched"
    }

    Write-Host ""
    Write-Host "-Revoke"

    Test-Case "revoking a record the owner wrote removes the file" {
        $file = Join-Path $script:Sandbox "revoke-own\browser\owner-enrollment.json"
        [void](Write-OwnerEnrollmentRecord -Path $file -Json $script:NewJson -OwnerSid $script:Me.Value)

        $result = Remove-OwnerEnrollmentRecord -Path $file

        Assert-Equal -Expected "removed" -Actual $result.Outcome -Because $result.Message
        Assert-True -Condition (-not (Test-Path -LiteralPath $file)) -Because "no record, no attach"
    }

    Test-Case "revoking the elevated-created record as the owner still revokes: the record is emptied where it cannot be deleted" {
        if ($script:TokenIsAdmin) { return }
        $file = New-ElevatedCreatedRecord -Name "revoke-elevated"

        $result = Remove-OwnerEnrollmentRecord -Path $file

        Assert-Equal -Expected "emptied" -Actual $result.Outcome -Because $result.Message
        $record = [System.IO.File]::ReadAllText($file) | ConvertFrom-Json
        Assert-Equal -Expected 0 -Actual @($record.enrollments).Count -Because "the worker reads an empty registry and refuses profile 'owner' (browser_agent/worker.py: no cdp_loopback enrollment)"
        Assert-True -Condition ($result.Message -match "(?i)elevated") -Because "and the owner is told the file itself is still there, and how to remove it: $($result.Message)"
    }

    Test-Case "revoking a record this account can neither delete nor write fails loudly and asks for elevation" {
        if ($script:TokenIsAdmin) { return }
        $file = New-ElevatedCreatedRecord -Name "revoke-readonly" -MyRights ([System.Security.AccessControl.FileSystemRights]::Read -bor [System.Security.AccessControl.FileSystemRights]::Synchronize)
        $before = Get-Sha256 -Path $file

        Assert-Throws -Pattern "(?i)elevated" -Because "an authorization that was NOT revoked must never be reported as revoked" -Body {
            Remove-OwnerEnrollmentRecord -Path $file
        }
        Assert-Equal -Expected $before -Actual (Get-Sha256 -Path $file) -Because "the record is untouched"
    }

    Test-Case "revoking when there is no record says so and changes nothing" {
        $result = Remove-OwnerEnrollmentRecord -Path (Join-Path $script:Sandbox "revoke-none\browser\owner-enrollment.json")
        Assert-Equal -Expected "absent" -Actual $result.Outcome -Because $result.Message
        Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $script:Sandbox "revoke-none"))) -Because "and creates nothing on the way"
    }

    Write-Host ""
    Write-Host "the script's wiring (parsed, never run - it closes and relaunches Chrome)"

    $scriptPath = Join-Path $repoRoot "scripts\browser\enroll-owner-chrome.ps1"
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$errors)
    $commands = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.CommandAst] }, $true))
    $lineOf = {
        param([string]$Name)
        $found = @($commands | Where-Object { $_.GetCommandName() -eq $Name })
        if (@($found).Count -eq 0) { return 0 }
        return $found[0].Extent.StartLineNumber
    }

    Test-Case "the script writes and revokes through the library, and no longer touches the file or icacls itself" {
        Assert-Equal -Expected 0 -Actual @($errors).Count -Because "the script parses under PowerShell 5.1"
        Assert-True -Condition ((& $lineOf "Write-OwnerEnrollmentRecord") -gt 0) -Because "the record is written by the tested function"
        Assert-True -Condition ((& $lineOf "Remove-OwnerEnrollmentRecord") -gt 0) -Because "and revoked by the tested function"
        $text = $ast.Extent.Text
        Assert-True -Condition ($text -notmatch "icacls") -Because "the swallowed icacls call is gone from the script"
        Assert-True -Condition ($text -notmatch "WriteAllText\(\s*\`$EnrollmentFile") -Because "and so is the unverified write"
        Assert-True -Condition ($text -notmatch "Remove-Item\s+-LiteralPath\s+\`$EnrollmentFile") -Because "and the delete that could not delete"
    }

    Test-Case "'recorded:' is printed only after the write returned, and nowhere else" {
        $recorded = @($commands | Where-Object { $_.Extent.Text -match "recorded:" })
        Assert-Equal -Expected 1 -Actual @($recorded).Count -Because "one place says 'recorded:'"
        $write = & $lineOf "Write-OwnerEnrollmentRecord"
        Assert-True -Condition ($write -gt 0) -Because "there is a write to come after"
        Assert-True -Condition ($recorded[0].Extent.StartLineNumber -gt $write) `
            -Because "line $($recorded[0].Extent.StartLineNumber) prints it; the write is on line $write and throws on any failure, so the print is unreachable unless it succeeded"
    }

    Test-Case "the script finds out it cannot write BEFORE it closes the owner's Chrome, and exits non-zero" {
        $preflight = & $lineOf "Test-OwnerEnrollmentWritable"
        $chrome = @($commands | Where-Object { $_.GetCommandName() -eq "Get-Process" })[0].Extent.StartLineNumber
        Assert-True -Condition ($preflight -gt 0) -Because "the preflight is called"
        Assert-True -Condition ($preflight -lt $chrome) -Because "on line $preflight, before Chrome is looked at on line $chrome"

        $text = $ast.Extent.Text
        $after = $text.Substring($text.IndexOf("Test-OwnerEnrollmentWritable"))
        $block = $after.Substring(0, $after.IndexOf("Get-Process"))
        Assert-True -Condition ($block -match "exit\s+[1-9]") -Because "a refused preflight ends the script with a non-zero exit code"
    }
}
finally {
    # Hand the restricted directories back their inheritance (this account owns them and
    # keeps WRITE_DAC on them): that carries delete-child, which removes the records
    # whatever their own DACLs say.
    foreach ($directory in @($script:RestrictedDirectories)) {
        try {
            $open = New-Object System.Security.AccessControl.DirectorySecurity
            $open.SetAccessRuleProtection($false, $false)
            ([System.IO.DirectoryInfo]$directory).SetAccessControl($open)
            # A plain delete, not Remove-Item -Force: -Force first clears attributes, which
            # needs a write right the read-only fixtures deliberately do not give.
            foreach ($file in [System.IO.Directory]::GetFiles($directory)) { [System.IO.File]::Delete($file) }
        } catch { }
    }
    Remove-Item -LiteralPath $script:Sandbox -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $script:Sandbox) {
        Write-Host "  NOTE  the sandbox could not be removed: $script:Sandbox" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "$($script:Passes) passed, $($script:Failures) failed"
exit $script:Failures
