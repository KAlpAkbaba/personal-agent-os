using System.Runtime.Versioning;
using System.Security.AccessControl;
using System.Security.Principal;

namespace PagentOS.Agent.Core.Security;

/// <summary>
/// What <see cref="OwnerSessionMaterial.EnsureOwnerWritableDirectory"/> found.
/// </summary>
/// <param name="Problem">Null when the owner can write the directory; otherwise why not.</param>
/// <param name="MustNotWrite">
/// True when nothing may be written there at all, whatever the file system would allow — the
/// directory is a reparse point, and a write would land wherever it points.
/// </param>
public readonly record struct OwnerDirectoryVerdict(string? Problem, bool MustNotWrite)
{
    public static OwnerDirectoryVerdict Writable { get; } = new(null, false);
}

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
///
/// Two rules came out of the security review of that fix (commit 2a2f7f95):
///
///   - <b>never through a reparse point.</b> The owner's data root is a place other accounts
///     can create folders in, so <c>audit</c> may be a junction somebody planted; the trail,
///     and any DACL written on "the audit directory", would land wherever it points. A
///     reparse point there is refused, and refused again before every row;
///   - <b>writable is decided by writing.</b> Looking for the owner's SID in the DACL misses
///     access that comes through a group, and the directory of an owner who could write was
///     re-permissioned wholesale. An existing directory is probed, and left exactly as it is
///     when the probe succeeds.
/// </summary>
public static class OwnerSessionMaterial
{
    /// <summary>
    /// Make <paramref name="directory"/> writable by the account this process runs as.
    ///
    /// A directory this process has <paramref name="justCreated"/> gets the posture above,
    /// stated on the directory. One that already existed is changed only if a write into it
    /// fails — the installer's arrangement, or a developer's, is not this process's to
    /// rewrite. Never throws: the caller is an audit log, and a trail that cannot be written
    /// must degrade, not stop the companion from starting.
    /// </summary>
    /// <param name="directory">The directory.</param>
    /// <param name="justCreated">Whether this process created it a moment ago.</param>
    /// <param name="existingFile">
    /// A file in it that will be appended to, if it is already there: a directory that
    /// accepts new files and a trail that refuses this account are different findings.
    /// </param>
    public static OwnerDirectoryVerdict EnsureOwnerWritableDirectory(string directory, bool justCreated, string? existingFile = null)
    {
        if (!OperatingSystem.IsWindows())
        {
            return OwnerDirectoryVerdict.Writable;
        }

        return EnsureWindows(directory, justCreated, existingFile);
    }

    /// <summary>
    /// Why nothing may be written into <paramref name="directory"/>, or null. Looks at the
    /// directory and at its parent — the companion's data root — and at the attributes of the
    /// LINK, never of what it points at. Cheap enough to ask before every row.
    /// </summary>
    public static string? ReparsePointRefusal(string directory)
    {
        var parent = Path.GetDirectoryName(directory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
        foreach (var candidate in new[] { directory, parent })
        {
            if (string.IsNullOrEmpty(candidate))
            {
                continue;
            }

            try
            {
                if ((File.GetAttributes(candidate) & FileAttributes.ReparsePoint) != 0)
                {
                    return $"{candidate} is a reparse point (a junction or a symbolic link); nothing is written through it, "
                           + "because it would land wherever that points. Look at where it leads, remove it, and rerun the installer";
                }
            }
            catch (Exception ex) when (ex is FileNotFoundException or DirectoryNotFoundException)
            {
                // Not there: nothing to refuse. Whatever creates it is checked when it has.
            }
            catch (Exception)
            {
                // Attributes that cannot be read are not evidence of a link; the write itself
                // will say what is wrong with the path.
            }
        }

        return null;
    }

    /// <summary>
    /// Does the directory's DACL allow <paramref name="owner"/> Modify, inherited by the files
    /// in it, with no deny entry against them? Read from the DACL, for a caller asking about
    /// ANOTHER account. It knows nothing of group membership, so it is not how this process
    /// decides about itself — see <see cref="CanWriteInto"/>.
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

    /// <summary>
    /// Can THIS process create a file in the directory, write to it and remove it — and
    /// append to <paramref name="existingFile"/> if that is already there? Decided by doing
    /// it, which is the only answer that accounts for groups, deny entries, integrity levels
    /// and whatever else stands between a token and a file. Leaves nothing behind.
    /// </summary>
    public static bool CanWriteInto(string directory, string? existingFile = null)
    {
        try
        {
            var probe = Path.Combine(directory, $".pagentos-write-probe-{Guid.NewGuid():N}");
            using (var stream = new FileStream(
                       probe, FileMode.CreateNew, FileAccess.Write, FileShare.None, bufferSize: 1, FileOptions.DeleteOnClose))
            {
                stream.WriteByte(0);
            }

            if (!string.IsNullOrEmpty(existingFile) && File.Exists(existingFile))
            {
                using var trail = new FileStream(
                    existingFile, FileMode.Open, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete);
            }

            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }

    [SupportedOSPlatform("windows")]
    private static OwnerDirectoryVerdict EnsureWindows(string directory, bool justCreated, string? existingFile)
    {
        try
        {
            // Before anything else, and before any DACL is written: a DACL written "on the
            // audit directory" through a junction is written on somebody else's directory.
            var refusal = ReparsePointRefusal(directory);
            if (refusal is not null)
            {
                return new OwnerDirectoryVerdict(refusal, MustNotWrite: true);
            }

            var owner = WindowsIdentity.GetCurrent().User;
            if (owner is null)
            {
                return new OwnerDirectoryVerdict("this process has no user SID", MustNotWrite: false);
            }

            if (!justCreated && CanWriteInto(directory, existingFile))
            {
                // Already writable - by the installer's grant, by inheritance, through a
                // group, by a developer's own arrangement. Not this process's to rewrite.
                return OwnerDirectoryVerdict.Writable;
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
            return OwnerDirectoryVerdict.Writable;
        }
        catch (Exception ex)
        {
            return new OwnerDirectoryVerdict(
                $"{directory} is not writable by this account and its ACL could not be changed from here ({ex.GetType().Name}: {ex.Message}); rerun the installer, which repairs it",
                MustNotWrite: false);
        }
    }
}
