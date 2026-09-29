using System.Runtime.Versioning;
using System.Security.AccessControl;
using System.Security.Principal;

namespace PagentOS.Agent.Core.Security;

/// <summary>
/// NTFS protection for a directory the <b>owner-session companion</b> writes — today, its
/// audit directory.
///
/// This is deliberately not a mode of <see cref="MachineMaterial"/>. That class protects what
/// the Session-0 service owns, and its rule is "SYSTEM plus Administrators, no other
/// principal"; the companion is the owner's non-elevated process, so under that rule it cannot
/// write its own files. Exactly that happened (2026-09-29): the audit log protected the
/// companion's audit directory as machine material, the companion — which had just created
/// the directory and therefore owned it — applied the DACL, and from then on every audit row
/// was refused. The installer waited ninety seconds for a <c>browser_worker_started</c> row
/// and rolled a healthy release back.
///
/// The posture here: a protected DACL naming SYSTEM and Administrators with full control and
/// the owner with <b>Modify</b>, all inheritable. Modify rather than full control, because the
/// process that writes the trail has no business re-permissioning it or taking it over; and
/// nobody else at all, because ProgramData's inherited ACL would otherwise let every local
/// user create files beside the trail.
/// </summary>
public static class OwnerSessionMaterial
{
    /// <summary>
    /// Make <paramref name="directory"/> writable by the account this process runs as.
    ///
    /// A directory this process has <paramref name="justCreated"/> gets the posture above,
    /// stated on the directory. One that already existed is changed only if the owner cannot
    /// write it — the installer's arrangement, or a developer's, is not this process's to
    /// rewrite. Returns null when the owner can write afterwards, otherwise why the directory
    /// could not be put right. Never throws: the caller is an audit log, and a trail that
    /// cannot be written must degrade, not stop the companion from starting.
    /// </summary>
    public static string? EnsureOwnerWritableDirectory(string directory, bool justCreated)
    {
        if (!OperatingSystem.IsWindows())
        {
            return null;
        }

        return EnsureWindows(directory, justCreated);
    }

    /// <summary>
    /// Does the directory's DACL allow <paramref name="owner"/> Modify, inherited by the files
    /// in it, with no deny entry against them? Read from the DACL, not tried: the answer has
    /// to be the same for an installer asking about another account.
    /// </summary>
    [SupportedOSPlatform("windows")]
    public static bool OwnerCanWrite(string directory, SecurityIdentifier owner)
    {
        var security = new DirectoryInfo(directory).GetAccessControl(AccessControlSections.Access);
        var allowed = (FileSystemRights)0;
        foreach (FileSystemAccessRule rule in security.GetAccessRules(includeExplicit: true, includeInherited: true, typeof(SecurityIdentifier)))
        {
            if (!((SecurityIdentifier)rule.IdentityReference).Equals(owner))
            {
                continue;
            }

            if (rule.AccessControlType == AccessControlType.Deny)
            {
                if ((rule.FileSystemRights & FileSystemRights.Modify) != 0)
                {
                    return false;
                }

                continue;
            }

            // An ACE that applies to this directory only does not reach the audit FILE.
            if (rule.InheritanceFlags.HasFlag(InheritanceFlags.ObjectInherit))
            {
                allowed |= rule.FileSystemRights;
            }
        }

        return (allowed & FileSystemRights.Modify) == FileSystemRights.Modify;
    }

    [SupportedOSPlatform("windows")]
    private static string? EnsureWindows(string directory, bool justCreated)
    {
        try
        {
            var owner = WindowsIdentity.GetCurrent().User;
            if (owner is null)
            {
                return "this process has no user SID";
            }

            if (!justCreated && OwnerCanWrite(directory, owner))
            {
                // Already writable - by the installer's grant, by inheritance, by a developer's
                // own arrangement. Not this process's to rewrite.
                return null;
            }

            const InheritanceFlags Inherit = InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit;
            var security = new DirectorySecurity();
            security.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);
            security.AddAccessRule(new FileSystemAccessRule(
                new SecurityIdentifier(WellKnownSidType.LocalSystemSid, null),
                FileSystemRights.FullControl, Inherit, PropagationFlags.None, AccessControlType.Allow));
            security.AddAccessRule(new FileSystemAccessRule(
                new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null),
                FileSystemRights.FullControl, Inherit, PropagationFlags.None, AccessControlType.Allow));
            security.AddAccessRule(new FileSystemAccessRule(
                owner, FileSystemRights.Modify, Inherit, PropagationFlags.None, AccessControlType.Allow));

            // Possible only for the directory's owner (WRITE_DAC comes with ownership) - which
            // this process is for a directory it created, including the one the old code
            // locked it out of. A directory an elevated process created is the installer's to
            // repair, and the refusal below says so.
            new DirectoryInfo(directory).SetAccessControl(security);
            return null;
        }
        catch (Exception ex)
        {
            return $"{directory} is not writable by this account and its ACL could not be changed from here ({ex.GetType().Name}: {ex.Message}); rerun the installer, which repairs it";
        }
    }
}
