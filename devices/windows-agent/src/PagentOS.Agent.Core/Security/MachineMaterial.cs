using System.Runtime.Versioning;
using System.Security.AccessControl;
using System.Security.Principal;

namespace PagentOS.Agent.Core.Security;

/// <summary>
/// What the material is for, which decides who may touch it.
/// </summary>
public enum MachineMaterialKind
{
    /// <summary>
    /// Service-owned secret the service only ever READS after creation — the device private
    /// key. SYSTEM gets read, and nothing more: the running service has no reason to be able
    /// to rewrite its own identity, and a service that cannot overwrite its key cannot be
    /// tricked into replacing it either.
    /// </summary>
    Secret,

    /// <summary>
    /// Service-owned state the service actively writes: enrollment state, the idempotency
    /// store, audit and logs. SYSTEM needs full control here.
    /// </summary>
    State,

    /// <summary>
    /// A directory holding the above. SYSTEM full control, inheritable, so anything the
    /// service creates later lands correct without a second thought.
    /// </summary>
    Directory,
}

/// <summary>
/// NTFS protection for **service-owned machine material** — the files a Session-0 LocalSystem
/// service must read, that an owner-context installer or enrollment run creates.
///
/// This exists because of a proven production failure. `DeviceIdentity` used to protect the
/// device private key with a DACL containing exactly one ACE: the *creating* user. That is
/// right for material owned by the interactive owner and wrong for material owned by the
/// service. Enrollment runs elevated as the owner, so the key was created owned by the owner
/// account and unreadable by anyone else; the service then started as LocalSystem and died in
/// <c>DeviceIdentity.LoadOrCreate</c> with
/// <c>UnauthorizedAccessException: Access to the path '...\device.key' is denied</c>, before
/// it ever reached the broker or the IPC layer. SCM reported 1067; the ACL was the cause.
///
/// The two domains are kept apart deliberately and must not be merged:
///
///   - <b>service-owned machine material</b> (this class): SYSTEM plus Administrators, no
///     inheritance, no other principal. Ordinary users get nothing — not even read, because
///     this includes the device private key;
///   - <b>owner-session material</b> (the owner credential, session tokens, the DPAPI secret
///     store): owner-scoped, and it stays that way. Nothing here converts owner secrets to
///     machine scope to make a service work.
///
/// Administrators keep full control, because the existing security model already relies on
/// administrative recovery — the installer repairs its own damaged trees that way, and a key
/// nobody can back up or replace is not more secure, only more fragile.
/// </summary>
public static class MachineMaterial
{
    // Built on demand inside Windows-only code paths rather than in a static initializer:
    // SecurityIdentifier is a Windows type, and a field initializer would run on any platform.
    [SupportedOSPlatform("windows")]
    private static SecurityIdentifier SystemSid => new(WellKnownSidType.LocalSystemSid, null);

    [SupportedOSPlatform("windows")]
    private static SecurityIdentifier AdministratorsSid => new(WellKnownSidType.BuiltinAdministratorsSid, null);

    /// <summary>Rights SYSTEM needs for each kind — the minimum, stated per kind.</summary>
    [SupportedOSPlatform("windows")]
    private static FileSystemRights SystemRightsFor(MachineMaterialKind kind) => kind switch
    {
        MachineMaterialKind.Secret => FileSystemRights.Read,
        _ => FileSystemRights.FullControl,
    };

    /// <summary>
    /// Apply the intended protection. No-op off Windows; throws if the descriptor cannot be
    /// written, because silently leaving a private key world-readable is not a "best effort".
    /// </summary>
    public static void Protect(string path, MachineMaterialKind kind, bool? includeCurrentUser = null)
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        ProtectWindows(path, kind, includeCurrentUser ?? DeveloperPosture);
    }

    /// <summary>
    /// Whether this process protects material for a developer run rather than for an
    /// installed service. False unless something says otherwise, so production cannot inherit
    /// the weaker posture by omission.
    /// </summary>
    public static bool DeveloperPosture { get; private set; }

    /// <summary>
    /// Declare the posture once, at startup.
    ///
    /// In a developer run the "service" is a console process the owner started, so the owner
    /// IS the service account, and protecting the key against them would make the agent
    /// unusable outside an installed service. Process-wide because it describes the process,
    /// not any one file — and named explicitly, the same way the companion's
    /// <c>--dev-trust</c> is, so a developer posture is always something someone asked for.
    /// </summary>
    public static void UseDeveloperPosture(bool enabled) => DeveloperPosture = enabled;

    [SupportedOSPlatform("windows")]
    private static void ProtectWindows(string path, MachineMaterialKind kind, bool includeCurrentUser)
    {
        var systemRights = SystemRightsFor(kind);
        var currentUser = includeCurrentUser ? WindowsIdentity.GetCurrent().User : null;

        if (kind == MachineMaterialKind.Directory)
        {
            var directoryInfo = new DirectoryInfo(path);
            var directorySecurity = new DirectorySecurity();
            directorySecurity.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);

            const InheritanceFlags inherit = InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit;
            directorySecurity.AddAccessRule(new FileSystemAccessRule(
                SystemSid, FileSystemRights.FullControl, inherit, PropagationFlags.None, AccessControlType.Allow));
            directorySecurity.AddAccessRule(new FileSystemAccessRule(
                AdministratorsSid, FileSystemRights.FullControl, inherit, PropagationFlags.None, AccessControlType.Allow));
            if (currentUser is not null)
            {
                directorySecurity.AddAccessRule(new FileSystemAccessRule(
                    currentUser, FileSystemRights.FullControl, inherit, PropagationFlags.None, AccessControlType.Allow));
            }

            directoryInfo.SetAccessControl(directorySecurity);
            return;
        }

        var fileInfo = new FileInfo(path);
        var fileSecurity = new FileSecurity();
        fileSecurity.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);
        fileSecurity.AddAccessRule(new FileSystemAccessRule(SystemSid, systemRights, AccessControlType.Allow));
        fileSecurity.AddAccessRule(new FileSystemAccessRule(AdministratorsSid, FileSystemRights.FullControl, AccessControlType.Allow));
        if (currentUser is not null)
        {
            fileSecurity.AddAccessRule(new FileSystemAccessRule(currentUser, FileSystemRights.FullControl, AccessControlType.Allow));
        }

        fileInfo.SetAccessControl(fileSecurity);
    }

    /// <summary>
    /// Is this path protected exactly as intended? Used to decide whether a repair is needed
    /// and, at service startup, to produce a diagnosis instead of an access-denied stack
    /// trace.
    /// </summary>
    public static MachineMaterialReport Inspect(string path, MachineMaterialKind kind, bool? includeCurrentUser = null)
    {
        if (!OperatingSystem.IsWindows())
        {
            return new MachineMaterialReport(path, Exists: File.Exists(path) || Directory.Exists(path), Readable: true, Correct: true, Problems: Array.Empty<string>());
        }

        return InspectWindows(path, kind, includeCurrentUser ?? DeveloperPosture);
    }

    [SupportedOSPlatform("windows")]
    private static MachineMaterialReport InspectWindows(string path, MachineMaterialKind kind, bool allowCurrentUser)
    {
        var isDirectory = Directory.Exists(path);
        if (!isDirectory && !File.Exists(path))
        {
            return new MachineMaterialReport(path, Exists: false, Readable: false, Correct: false, Problems: new[] { "does not exist" });
        }

        FileSystemSecurity security;
        try
        {
            security = isDirectory
                ? new DirectoryInfo(path).GetAccessControl(AccessControlSections.Access | AccessControlSections.Owner)
                : new FileInfo(path).GetAccessControl(AccessControlSections.Access | AccessControlSections.Owner);
        }
        catch (Exception ex)
        {
            return new MachineMaterialReport(path, Exists: true, Readable: false, Correct: false,
                Problems: new[] { $"security descriptor unreadable from this account: {ex.GetType().Name}" });
        }

        var problems = new List<string>();
        var rules = security.GetAccessRules(includeExplicit: true, includeInherited: true, typeof(SecurityIdentifier));

        var systemRights = SystemRightsFor(kind);
        var systemHasRights = false;
        var adminHasFull = false;

        foreach (FileSystemAccessRule rule in rules)
        {
            var sid = (SecurityIdentifier)rule.IdentityReference;
            var allow = rule.AccessControlType == AccessControlType.Allow;

            if (!allow)
            {
                continue;
            }

            if (sid.Equals(SystemSid))
            {
                if ((rule.FileSystemRights & systemRights) == systemRights)
                {
                    systemHasRights = true;
                }
            }
            else if (sid.Equals(AdministratorsSid))
            {
                if (rule.FileSystemRights.HasFlag(FileSystemRights.FullControl))
                {
                    adminHasFull = true;
                }
            }
            else if (allowCurrentUser && WindowsIdentity.GetCurrent().User is { } currentUser && sid.Equals(currentUser))
            {
                // Developer posture: the account running the agent is the service account.
            }
            else
            {
                // Anything else at all is a problem: this material includes a private key.
                problems.Add($"unexpected principal {sid.Value} granted {rule.FileSystemRights}");
            }
        }

        if (!systemHasRights)
        {
            problems.Add($"SYSTEM lacks {systemRights} — the service cannot start without it");
        }

        if (!adminHasFull)
        {
            problems.Add("Administrators lack FullControl — the material could not be repaired or backed up");
        }

        if (!security.AreAccessRulesProtected)
        {
            problems.Add("inheritance is enabled; the protection would change if the parent's ACL changed");
        }

        return new MachineMaterialReport(path, Exists: true, Readable: true, Correct: problems.Count == 0, Problems: problems.ToArray());
    }

    /// <summary>
    /// Repair in place: fix the descriptor without touching the bytes. Returns true when a
    /// change was made. Never creates, never deletes, never rewrites content — a device
    /// identity is not something to regenerate because its ACL was wrong.
    /// </summary>
    public static bool Repair(string path, MachineMaterialKind kind, bool? includeCurrentUser = null)
    {
        var before = Inspect(path, kind, includeCurrentUser);
        if (!before.Exists || before.Correct)
        {
            return false;
        }

        Protect(path, kind, includeCurrentUser);
        return true;
    }
}

/// <summary>What <see cref="MachineMaterial.Inspect"/> found. Never contains file contents.</summary>
public sealed record MachineMaterialReport(
    string Path,
    bool Exists,
    bool Readable,
    bool Correct,
    IReadOnlyList<string> Problems)
{
    public string Describe()
        => Correct
            ? $"{Path}: correctly protected"
            : $"{Path}: {string.Join("; ", Problems)}";
}
