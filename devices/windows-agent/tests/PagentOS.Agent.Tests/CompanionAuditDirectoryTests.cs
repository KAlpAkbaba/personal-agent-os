using System.Security.AccessControl;
using System.Security.Principal;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Logging;
using PagentOS.Agent.Core.Security;
using PagentOS.Agent.Tests.Support;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>
/// Whose audit trail is it? The 2026-09-29 incident, as tests.
///
/// <c>AuditLog</c> protected every audit directory it created as service-owned machine
/// material: SYSTEM and Administrators, nobody else. That is right for the Device Service and
/// it locked the Session Companion out of its own trail, because the companion is the owner's
/// non-elevated process. It could still apply that DACL — it had just created the directory,
/// and a creator owns what it creates — so it wrote itself out of
/// <c>C:\ProgramData\PagentOS\companion\audit</c> on its first start and never wrote a row
/// again. The failure went to a stderr nobody reads; the installer waited ninety seconds for
/// a <c>browser_worker_started</c> row that could not be written and rolled back a healthy
/// browser worker 0.5.0, twice.
///
/// These run as an ordinary, non-elevated account, like <see cref="MachineMaterialTests"/>,
/// which makes the central ones measured rather than simulated: this process IS the owner's
/// non-elevated process, and whether it can write the row afterwards is the question.
/// Every posture is stated at the call, never through the process-wide default.
/// </summary>
public sealed class CompanionAuditDirectoryTests : IDisposable
{
    private static readonly SecurityIdentifier SystemSid = new(WellKnownSidType.LocalSystemSid, null);
    private static readonly SecurityIdentifier AdministratorsSid = new(WellKnownSidType.BuiltinAdministratorsSid, null);

    private readonly string _root = Path.Combine(
        Path.GetTempPath(), "pagentos-companion-audit", Guid.NewGuid().ToString("N"));

    public CompanionAuditDirectoryTests() => Directory.CreateDirectory(_root);

    public void Dispose()
    {
        // A directory protected against this account cannot be walked by it; the DACL is
        // handed back first, through the WRITE_DAC an owner always keeps.
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
    }

    private static SecurityIdentifier CurrentUser => WindowsIdentity.GetCurrent().User!;

    private static bool IsElevated => new WindowsPrincipal(WindowsIdentity.GetCurrent())
        .IsInRole(WindowsBuiltInRole.Administrator);

    private static (DirectorySecurity Security, IReadOnlyList<FileSystemAccessRule> Rules) DaclOf(string directory)
    {
        var security = new DirectoryInfo(directory).GetAccessControl(AccessControlSections.Access);
        var rules = security.GetAccessRules(true, true, typeof(SecurityIdentifier)).Cast<FileSystemAccessRule>().ToList();
        return (security, rules);
    }

    private static FileSystemRights AllowedTo(IEnumerable<FileSystemAccessRule> rules, SecurityIdentifier sid)
        => rules
            .Where(rule => rule.AccessControlType == AccessControlType.Allow && ((SecurityIdentifier)rule.IdentityReference).Equals(sid))
            .Aggregate((FileSystemRights)0, (accumulated, rule) => accumulated | rule.FileSystemRights);

    // ------------------------------------------------------------------ the incident itself

    [Fact]
    public void The_companion_can_write_the_audit_trail_in_the_directory_it_created()
    {
        // Production posture, stated: nothing here is a developer run. The companion creates
        // its audit directory on first start, exactly as it did on the owner's machine.
        var path = Path.Combine(_root, "companion", "audit", "companion-audit.jsonl");

        var audit = new AuditLog(path, AuditWriter.OwnerSessionCompanion, developerRun: false);
        audit.Write("browser_worker_started", status: "ok", detail: "pid=32524; worker_version=0.5.0");

        Assert.Equal(0, audit.FailedWrites);
        var line = Assert.Single(File.ReadAllLines(path));
        Assert.Contains("\"event\":\"browser_worker_started\"", line, StringComparison.Ordinal);
    }

    [Fact]
    public void The_companions_audit_directory_names_the_owner_with_modify_and_nothing_more()
    {
        var directory = Path.Combine(_root, "companion", "audit");
        _ = new AuditLog(Path.Combine(directory, "companion-audit.jsonl"), AuditWriter.OwnerSessionCompanion, developerRun: false);

        var (security, rules) = DaclOf(directory);

        Assert.True(security.AreAccessRulesProtected, "the posture is stated on the directory, not borrowed from ProgramData");
        Assert.True(AllowedTo(rules, SystemSid).HasFlag(FileSystemRights.FullControl));
        Assert.True(AllowedTo(rules, AdministratorsSid).HasFlag(FileSystemRights.FullControl));

        var owner = AllowedTo(rules, CurrentUser);
        Assert.True(owner.HasFlag(FileSystemRights.Modify), $"the owner needs Modify to append a row; has {owner}");
        // Modify, not FullControl: the owner's process writes the trail, it does not get to
        // re-permission it or take it over.
        Assert.False(owner.HasFlag(FileSystemRights.ChangePermissions), "Modify does not include WRITE_DAC");
        Assert.False(owner.HasFlag(FileSystemRights.TakeOwnership), "nor WRITE_OWNER");

        Assert.All(rules, rule =>
        {
            var sid = (SecurityIdentifier)rule.IdentityReference;
            Assert.True(
                sid.Equals(SystemSid) || sid.Equals(AdministratorsSid) || sid.Equals(CurrentUser),
                $"unexpected principal on the companion's audit directory: {sid.Value}");
            Assert.True(rule.InheritanceFlags.HasFlag(InheritanceFlags.ObjectInherit), "the audit FILE created later must inherit this");
        });
    }

    [Fact]
    public void A_directory_the_old_companion_locked_itself_out_of_is_repaired_at_the_next_start()
    {
        // The state the owner's machine was in: the directory exists, this account created it
        // (and so owns it), and its protected DACL names SYSTEM and Administrators only.
        var directory = Path.Combine(_root, "companion", "audit");
        Directory.CreateDirectory(directory);
        MachineMaterial.Protect(directory, MachineMaterialKind.Directory, includeCurrentUser: false);
        Assert.False(AllowedTo(DaclOf(directory).Rules, CurrentUser).HasFlag(FileSystemRights.Modify), "the fixture must really be locked first");

        var audit = new AuditLog(Path.Combine(directory, "companion-audit.jsonl"), AuditWriter.OwnerSessionCompanion, developerRun: false);
        audit.Write("browser_worker_started", status: "ok");

        Assert.Equal(0, audit.FailedWrites);
        Assert.True(AllowedTo(DaclOf(directory).Rules, CurrentUser).HasFlag(FileSystemRights.Modify));
    }

    [Fact]
    public void A_directory_the_owner_can_already_write_is_left_exactly_as_it_is()
    {
        // The installer's arrangement (or a developer's) is not the companion's to rewrite:
        // what it needs is to be able to write, and here it can.
        var directory = Path.Combine(_root, "companion", "audit");
        Directory.CreateDirectory(directory);
        var before = new DirectoryInfo(directory).GetAccessControl(AccessControlSections.Access)
            .GetSecurityDescriptorSddlForm(AccessControlSections.Access);

        var audit = new AuditLog(Path.Combine(directory, "companion-audit.jsonl"), AuditWriter.OwnerSessionCompanion, developerRun: false);
        audit.Write("artifact_opened", status: "ok");

        var after = new DirectoryInfo(directory).GetAccessControl(AccessControlSections.Access)
            .GetSecurityDescriptorSddlForm(AccessControlSections.Access);
        Assert.Equal(before, after);
        Assert.Equal(0, audit.FailedWrites);
    }

    // ------------------------------------------------- the service does not get the weaker one

    [Fact]
    public void The_writer_is_the_service_unless_something_says_otherwise()
    {
        // The default of the enum AND the default of the parameter: a caller that forgets the
        // argument gets the strict posture, never the owner-writable one.
        Assert.Equal(AuditWriter.Service, default(AuditWriter));
        var parameter = typeof(AuditLog).GetConstructors().Single().GetParameters().Single(p => p.Name == "writer");
        Assert.True(parameter.HasDefaultValue);
        Assert.Equal(AuditWriter.Service, (AuditWriter)parameter.DefaultValue!);
    }

    [Fact]
    public void The_services_audit_directory_stays_SYSTEM_and_administrators_only()
    {
        var directory = Path.Combine(_root, "agent", "audit");
        var audit = new AuditLog(Path.Combine(directory, "agent-audit.jsonl"), developerRun: false);

        var (security, rules) = DaclOf(directory);
        Assert.True(security.AreAccessRulesProtected);
        Assert.All(rules, rule => Assert.True(
            ((SecurityIdentifier)rule.IdentityReference).Equals(SystemSid) || ((SecurityIdentifier)rule.IdentityReference).Equals(AdministratorsSid),
            $"unexpected principal on the service's audit directory: {rule.IdentityReference}"));

        if (IsElevated)
        {
            // Elevated, this process IS Administrators and may write; the DACL above is the
            // assertion. Said, rather than passing a test that measured nothing.
            return;
        }

        // Measured: the owner's ordinary process cannot add a row to the SERVICE's trail.
        audit.Write("command_ack", commandId: "cmd-1", traceId: "t-1", status: "accepted");
        Assert.Equal(1, audit.FailedWrites);
    }

    [Fact]
    public void The_shipped_hosts_each_name_their_own_posture()
    {
        // Composition, read from the two places it is made. Everything above proves what the
        // postures DO; this is what proves the shipped companion asks for the right one and
        // wires its failure to its log - Main is not something a test can call.
        var companion = CompanionSources.Read("Program.cs");
        Assert.Contains(
            "new AuditLog(Path.Combine(dataDir, \"audit\", \"companion-audit.jsonl\"), AuditWriter.OwnerSessionCompanion)",
            companion,
            StringComparison.Ordinal);
        Assert.Contains("ReportAuditFailures(audit, logger);", companion, StringComparison.Ordinal);

        var service = File.ReadAllText(Path.Combine(CompanionSources.Directory(), "..", "PagentOS.DeviceService", "Program.cs"));
        Assert.DoesNotContain("AuditWriter.OwnerSessionCompanion", service, StringComparison.Ordinal);
        Assert.Contains("new AuditLog(options.AuditLogPath)", service, StringComparison.Ordinal);
    }

    // ------------------------------------------------------- the failure is said, and said once

    [Fact]
    public void A_trail_that_cannot_be_written_is_one_warning_in_the_companion_log_not_one_per_row()
    {
        var logPath = Program.CompanionLogPath(Path.Combine(_root, "companion"));
        var auditPath = Path.Combine(_root, "companion", "audit", "companion-audit.jsonl");
        var audit = new AuditLog(auditPath, AuditWriter.OwnerSessionCompanion, developerRun: false);
        Directory.CreateDirectory(auditPath);   // a directory where the file should be: every append fails

        using (var provider = new FileLoggerProvider(logPath, maxBytes: Program.CompanionLogMaxBytes, keepRotated: Program.CompanionLogKeepRotated))
        {
            var logger = provider.CreateLogger("SessionCompanion");
            Program.ReportAuditFailures(audit, logger);
            for (var i = 0; i < 25; i++)
            {
                audit.Write("browser_request", capability: "browser.inspect", status: "ok", detail: $"row {i}");
            }
        }

        Assert.Equal(25, audit.FailedWrites);
        var warnings = File.ReadAllLines(logPath)
            .Where(line => line.Contains("audit", StringComparison.OrdinalIgnoreCase))
            .ToList();
        var warning = Assert.Single(warnings);
        Assert.Contains("Warning", warning, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("companion-audit.jsonl", warning, StringComparison.Ordinal);
    }

    [Fact]
    public void A_failure_from_before_the_logger_existed_is_still_reported_when_it_is_attached()
    {
        // The companion builds its audit log before its logger (the artifact opener needs the
        // first, the log file path needs nothing). A row lost in between is not forgiven.
        var auditPath = Path.Combine(_root, "companion", "audit", "companion-audit.jsonl");
        var audit = new AuditLog(auditPath, AuditWriter.OwnerSessionCompanion, developerRun: false);
        Directory.CreateDirectory(auditPath);
        audit.Write("artifact_opened", status: "ok");
        audit.Write("artifact_opened", status: "ok");

        var log = new ListLogger();
        Program.ReportAuditFailures(audit, log);
        audit.Write("artifact_opened", status: "ok");

        var warning = Assert.Single(log.Lines);
        Assert.StartsWith("[Warning]", warning, StringComparison.Ordinal);
        Assert.Contains(auditPath, warning, StringComparison.Ordinal);
    }

    [Fact]
    public void A_trail_that_is_being_written_says_nothing()
    {
        var audit = new AuditLog(Path.Combine(_root, "companion", "audit", "companion-audit.jsonl"), AuditWriter.OwnerSessionCompanion, developerRun: false);
        var log = new ListLogger();
        Program.ReportAuditFailures(audit, log);

        audit.Write("artifact_opened", status: "ok");

        Assert.Equal(0, audit.FailedWrites);
        Assert.Empty(log.Lines);
    }
}
