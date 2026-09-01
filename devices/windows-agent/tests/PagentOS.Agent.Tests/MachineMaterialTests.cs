using System.Security.AccessControl;
using System.Security.Principal;
using PagentOS.Agent.Core.Identity;
using PagentOS.Agent.Core.Security;
using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>
/// Tests for the service/owner security boundary on persisted machine material.
///
/// The failure these exist for was proven on the owner's machine: enrollment ran elevated as
/// the owner, `DeviceIdentity` protected the new device key with a DACL containing exactly one
/// ACE — the creating user — and the service then died as LocalSystem with
/// `UnauthorizedAccessException` on `device.key`, reported by the SCM as 1067. The key was
/// service-owned material protected as if it were owner material.
///
/// These tests run as an ordinary, non-elevated user, which makes several of them real rather
/// than simulated: this process genuinely is "an ordinary owner process", so when it cannot
/// write protected material afterwards, that is measured, not asserted by proxy. What cannot
/// be measured here is SYSTEM actually opening the file — no test process is LocalSystem — so
/// that is checked as "SYSTEM holds exactly these rights in the DACL", and the qualification
/// matrix records the difference.
/// </summary>
public class MachineMaterialTests : IDisposable
{
    private readonly string _root = Path.Combine(
        Path.GetTempPath(), "pagentos-machine-material", Guid.NewGuid().ToString("N"));

    private static readonly SecurityIdentifier SystemSid = new(WellKnownSidType.LocalSystemSid, null);
    private static readonly SecurityIdentifier AdministratorsSid = new(WellKnownSidType.BuiltinAdministratorsSid, null);
    private static readonly SecurityIdentifier UsersSid = new(WellKnownSidType.BuiltinUsersSid, null);
    private static readonly SecurityIdentifier EveryoneSid = new(WellKnownSidType.WorldSid, null);

    public MachineMaterialTests()
    {
        Directory.CreateDirectory(_root);
        // Deliberately does NOT touch the process-wide posture: xUnit runs classes in
        // parallel, and flipping a global here raced other classes into the production
        // posture mid-test. Every assertion below states the posture at the call instead.
    }

    public void Dispose()
    {
        // Protected directories deny this account even enumeration, so a plain recursive walk
        // throws before it reaches the files. Each directory's DACL is restored on the way
        // down — possible because this account owns what it created, and an owner always
        // keeps WRITE_DAC. That is the same property the production repair path relies on.
        RestoreAccess(_root);
        try { Directory.Delete(_root, recursive: true); } catch (Exception) { }
    }

    private static void RestoreAccess(string directory)
    {
        try
        {
            var security = new DirectorySecurity();
            security.SetAccessRuleProtection(false, false);
            new DirectoryInfo(directory).SetAccessControl(security);
        }
        catch (Exception)
        {
            return;
        }

        foreach (var child in Directory.EnumerateDirectories(directory))
        {
            RestoreAccess(child);
        }

        foreach (var file in Directory.EnumerateFiles(directory))
        {
            try
            {
                var security = new FileSecurity();
                security.SetAccessRuleProtection(false, false);
                new FileInfo(file).SetAccessControl(security);
            }
            catch (Exception)
            {
                // Best-effort cleanup.
            }
        }
    }

    private static IReadOnlyList<FileSystemAccessRule> RulesOf(string path)
    {
        var security = new FileInfo(path).GetAccessControl(AccessControlSections.Access);
        return security.GetAccessRules(true, true, typeof(SecurityIdentifier)).Cast<FileSystemAccessRule>().ToList();
    }

    private static bool CanWrite(string path)
    {
        try
        {
            using var stream = File.Open(path, FileMode.Open, FileAccess.Write, FileShare.None);
            return true;
        }
        catch (UnauthorizedAccessException)
        {
            return false;
        }
    }

    private static bool CanRead(string path)
    {
        try
        {
            using var stream = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            return true;
        }
        catch (UnauthorizedAccessException)
        {
            return false;
        }
    }

    // ------------------------------------------- owner-context enrollment -> service startup

    [Fact]
    public void A_key_created_in_owner_context_is_readable_by_SYSTEM()
    {
        // The regression. Creating the key here is exactly what elevated enrollment does:
        // an owner-context process writes it. SYSTEM must be able to read it afterwards, or
        // the service cannot start.
        var keyPath = Path.Combine(_root, "device.key");
        using (var identity = DeviceIdentity.LoadOrCreate(keyPath, developerRun: false))
        {
            Assert.NotEmpty(identity.PublicKeySpkiBase64);
        }

        var systemRules = RulesOf(keyPath)
            .Where(rule => ((SecurityIdentifier)rule.IdentityReference).Equals(SystemSid))
            .Where(rule => rule.AccessControlType == AccessControlType.Allow)
            .ToList();

        Assert.NotEmpty(systemRules);
        Assert.Contains(systemRules, rule => rule.FileSystemRights.HasFlag(FileSystemRights.Read));
    }

    [Fact]
    public void The_key_grants_SYSTEM_read_but_not_write()
    {
        // Minimum required permission: the running service reads its identity and never
        // rewrites it, so it does not get the ability to.
        var keyPath = Path.Combine(_root, "device.key");
        DeviceIdentity.LoadOrCreate(keyPath, developerRun: false).Dispose();

        var systemRights = RulesOf(keyPath)
            .Where(rule => ((SecurityIdentifier)rule.IdentityReference).Equals(SystemSid) && rule.AccessControlType == AccessControlType.Allow)
            .Aggregate((FileSystemRights)0, (accumulated, rule) => accumulated | rule.FileSystemRights);

        Assert.True(systemRights.HasFlag(FileSystemRights.Read), "SYSTEM must be able to read the key");
        Assert.False(systemRights.HasFlag(FileSystemRights.WriteData), "SYSTEM does not need to rewrite the device identity");
        Assert.False(systemRights.HasFlag(FileSystemRights.FullControl), "full control is more than the service needs");
    }

    [Fact]
    public void Administrators_keep_full_control_for_recovery()
    {
        var keyPath = Path.Combine(_root, "device.key");
        DeviceIdentity.LoadOrCreate(keyPath, developerRun: false).Dispose();

        Assert.Contains(RulesOf(keyPath), rule =>
            ((SecurityIdentifier)rule.IdentityReference).Equals(AdministratorsSid)
            && rule.AccessControlType == AccessControlType.Allow
            && rule.FileSystemRights.HasFlag(FileSystemRights.FullControl));
    }

    // --------------------------------------------------- unprivileged principals get nothing

    [Fact]
    public void No_ordinary_user_group_appears_in_the_key_acl()
    {
        var keyPath = Path.Combine(_root, "device.key");
        DeviceIdentity.LoadOrCreate(keyPath, developerRun: false).Dispose();

        var principals = RulesOf(keyPath).Select(rule => (SecurityIdentifier)rule.IdentityReference).ToList();

        Assert.DoesNotContain(UsersSid, principals);
        Assert.DoesNotContain(EveryoneSid, principals);
        // Only the two intended principals, and nothing else at all — this file is a private key.
        Assert.All(principals, sid => Assert.True(
            sid.Equals(SystemSid) || sid.Equals(AdministratorsSid),
            $"unexpected principal in the device key ACL: {sid.Value}"));
    }

    [Fact]
    public void An_ordinary_user_process_cannot_modify_service_identity_material()
    {
        // Measured, not asserted by proxy: this test process is an ordinary user process, and
        // it is not an administrator. After protection it must lose write access to the key
        // it just created.
        var keyPath = Path.Combine(_root, "device.key");
        DeviceIdentity.LoadOrCreate(keyPath, developerRun: false).Dispose();

        var isElevated = new WindowsPrincipal(WindowsIdentity.GetCurrent())
            .IsInRole(WindowsBuiltInRole.Administrator);

        if (isElevated)
        {
            // Running elevated, this process IS Administrators, which legitimately has full
            // control. Assert the ACL instead of the outcome, and say so rather than passing
            // a test that measured nothing.
            Assert.DoesNotContain(RulesOf(keyPath), rule =>
                ((SecurityIdentifier)rule.IdentityReference).Equals(UsersSid));
            return;
        }

        Assert.False(CanWrite(keyPath), "an ordinary owner process must not be able to rewrite the device key");
        Assert.False(CanRead(keyPath), "nor read it: the private key is service material, not owner material");
    }

    // ------------------------------------------------------------ detection and safe repair

    [Fact]
    public void A_key_protected_the_old_way_is_detected_as_wrong()
    {
        // Reproduce the exact production state: a protected DACL granting only the creating
        // user, which is what DeviceIdentity used to write.
        var keyPath = Path.Combine(_root, "device.key");
        DeviceIdentity.LoadOrCreate(keyPath, developerRun: false).Dispose();
        ApplyOwnerOnlyAcl(keyPath);

        var report = DeviceIdentity.InspectKey(keyPath, developerRun: false);

        Assert.False(report.Correct);
        Assert.Contains(report.Problems, problem => problem.Contains("SYSTEM lacks", StringComparison.Ordinal));
    }

    [Fact]
    public void Repair_fixes_the_acl_and_preserves_the_device_identity()
    {
        // The key must survive: a wrong ACL is never a reason to issue a new device identity.
        //
        // Reading the bytes back needs a moment of access this account does not have under the
        // production ACL — which is the protection working. It regains it the way an
        // administrator recovering a key would, through the WRITE_DAC every owner keeps, and
        // the test re-asserts the production ACL afterwards so nothing is left loosened.
        var keyPath = Path.Combine(_root, "device.key");
        string publicKeyBefore;
        byte[] bytesBefore;

        // The explicit developerRun argument is enough; this must NOT touch the process-wide
        // posture. It did, and the `finally` set it to production while other test classes
        // were running in parallel — their audit directories were then protected against the
        // very account writing them, and an unrelated test failed with "no audit row was
        // ever written". A global that one test flips is a global every test depends on.
        using (var identity = DeviceIdentity.LoadOrCreate(keyPath, developerRun: true))
        {
            publicKeyBefore = identity.PublicKeySpkiBase64;
            bytesBefore = File.ReadAllBytes(keyPath);
        }

        // Now put it in the exact state the real machine was in.
        ApplyOwnerOnlyAcl(keyPath);
        Assert.False(DeviceIdentity.InspectKey(keyPath, developerRun: false).Correct);

        Assert.True(DeviceIdentity.RepairKeyProtection(keyPath, developerRun: false), "the repair should report that it changed something");
        Assert.True(DeviceIdentity.InspectKey(keyPath, developerRun: false).Correct, "and the result must be the production ACL");

        WithTemporaryOwnerAccess(keyPath, () =>
        {
            Assert.Equal(bytesBefore, File.ReadAllBytes(keyPath));
            using var reloaded = DeviceIdentity.LoadOrCreate(keyPath);
            Assert.Equal(publicKeyBefore, reloaded.PublicKeySpkiBase64);
        });

        Assert.True(DeviceIdentity.InspectKey(keyPath, developerRun: false).Correct, "the temporary access must be given back");
    }

    [Fact]
    public void Repair_is_idempotent_and_a_healthy_key_is_left_alone()
    {
        var keyPath = Path.Combine(_root, "device.key");
        DeviceIdentity.LoadOrCreate(keyPath, developerRun: false).Dispose();

        Assert.False(DeviceIdentity.RepairKeyProtection(keyPath, developerRun: false), "a correct key needs no repair");
        Assert.True(DeviceIdentity.InspectKey(keyPath, developerRun: false).Correct);

        ApplyOwnerOnlyAcl(keyPath);
        Assert.True(DeviceIdentity.RepairKeyProtection(keyPath, developerRun: false));
        Assert.False(DeviceIdentity.RepairKeyProtection(keyPath, developerRun: false), "a second repair must be a no-op");
    }

    [Fact]
    public void Reading_an_unreadable_key_produces_a_diagnosis_not_a_bare_access_denied()
    {
        // The service's startup path: whatever else happens, the operator must be told which
        // file, which principals, and what to run — without a byte of the key.
        var keyPath = Path.Combine(_root, "device.key");
        DeviceIdentity.LoadOrCreate(keyPath, developerRun: false).Dispose();
        DenyEveryone(keyPath);

        var report = DeviceIdentity.InspectKey(keyPath, developerRun: false);
        Assert.False(report.Correct);

        var description = report.Describe();
        Assert.Contains("device.key", description, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("PRIVATE KEY", description, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("BEGIN", description, StringComparison.OrdinalIgnoreCase);
    }

    // ------------------------------------------------------------------ state and directories

    [Fact]
    public void Enrollment_state_is_machine_material_writable_by_SYSTEM()
    {
        var statePath = Path.Combine(_root, "state.json");
        new AgentState
        {
            DeviceId = Guid.NewGuid().ToString(),
            Name = "test",
            BrokerRestUrl = "http://127.0.0.1:8001",
            EnrolledAt = DateTimeOffset.UtcNow,
        }.Save(statePath);

        var systemRights = RulesOf(statePath)
            .Where(rule => ((SecurityIdentifier)rule.IdentityReference).Equals(SystemSid) && rule.AccessControlType == AccessControlType.Allow)
            .Aggregate((FileSystemRights)0, (accumulated, rule) => accumulated | rule.FileSystemRights);

        // The service rewrites state, unlike the key.
        Assert.True(systemRights.HasFlag(FileSystemRights.FullControl), "the service must be able to update its own enrollment state");
        Assert.DoesNotContain(RulesOf(statePath), rule => ((SecurityIdentifier)rule.IdentityReference).Equals(UsersSid));
    }

    [Fact]
    public void A_machine_directory_grants_only_SYSTEM_and_administrators_and_inherits_down()
    {
        var directory = Path.Combine(_root, "machine-dir");
        Directory.CreateDirectory(directory);
        MachineMaterial.Protect(directory, MachineMaterialKind.Directory, includeCurrentUser: false);

        var security = new DirectoryInfo(directory).GetAccessControl(AccessControlSections.Access);
        var rules = security.GetAccessRules(true, true, typeof(SecurityIdentifier)).Cast<FileSystemAccessRule>().ToList();

        Assert.True(security.AreAccessRulesProtected, "the directory must not inherit from ProgramData");
        Assert.All(rules, rule => Assert.True(
            ((SecurityIdentifier)rule.IdentityReference).Equals(SystemSid) || ((SecurityIdentifier)rule.IdentityReference).Equals(AdministratorsSid),
            $"unexpected principal on the machine directory: {rule.IdentityReference}"));
        Assert.All(rules, rule => Assert.True(
            rule.InheritanceFlags.HasFlag(InheritanceFlags.ObjectInherit),
            "files created later must inherit this protection"));
    }

    [Fact]
    public void Inspect_reports_a_missing_path_without_throwing()
    {
        var report = MachineMaterial.Inspect(Path.Combine(_root, "not-there.json"), MachineMaterialKind.State);
        Assert.False(report.Exists);
        Assert.False(report.Correct);
    }

    // ------------------------------------------------------------------------------ helpers

    /// <summary>The pre-fix behaviour: a protected DACL naming only the creating user.</summary>
    private static void ApplyOwnerOnlyAcl(string path)
    {
        var user = WindowsIdentity.GetCurrent().User!;
        var security = new FileSecurity();
        security.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);
        security.AddAccessRule(new FileSystemAccessRule(user, FileSystemRights.FullControl, AccessControlType.Allow));
        new FileInfo(path).SetAccessControl(security);
    }

    /// <summary>
    /// Temporarily grant this account access through the WRITE_DAC an owner always keeps,
    /// run the action, then restore the production protection.
    /// </summary>
    private static void WithTemporaryOwnerAccess(string path, Action action)
    {
        var user = WindowsIdentity.GetCurrent().User!;
        var security = new FileInfo(path).GetAccessControl(AccessControlSections.Access);
        security.AddAccessRule(new FileSystemAccessRule(user, FileSystemRights.FullControl, AccessControlType.Allow));
        new FileInfo(path).SetAccessControl(security);
        try
        {
            action();
        }
        finally
        {
            MachineMaterial.Protect(path, MachineMaterialKind.Secret, includeCurrentUser: false);
        }
    }

    /// <summary>An empty protected DACL: nobody at all, the harshest readable-by-none state.</summary>
    private static void DenyEveryone(string path)
    {
        var security = new FileSecurity();
        security.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);
        new FileInfo(path).SetAccessControl(security);
    }
}
