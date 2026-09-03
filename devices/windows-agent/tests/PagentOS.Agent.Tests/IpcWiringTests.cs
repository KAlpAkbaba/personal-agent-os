using System.IO.Pipes;
using System.Security.Principal;
using System.Text;
using Microsoft.Extensions.Configuration;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Ipc;
using PagentOS.Agent.Tests.Support;
using PagentOS.DeviceService;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>
/// Tests for the composition roots â€” the code that decides what the shipped binaries
/// actually run with.
///
/// This file exists because of a specific, repeated failure: in M8 an authorization provider,
/// in M9 a scope check, and in ADR-0028 the companion's pipe-owner policy were each written
/// correctly, tested thoroughly, and never wired into the program that ships. Every one of
/// those suites was green while the property it described was false in production. So the
/// assertions here are deliberately about wiring rather than about logic: what does
/// `Program` build, what does configuration resolve to, and does the operator get told.
/// </summary>
public class IpcWiringTests
{
    private static AgentServiceOptions Options(params (string Key, string? Value)[] settings)
    {
        var configuration = new ConfigurationBuilder()
            .AddInMemoryCollection(settings.Select(s => new KeyValuePair<string, string?>(s.Key, s.Value)))
            .Build();
        return AgentServiceOptions.FromConfiguration(configuration);
    }

    // --------------------------------------------------- service admission composition

    [Fact]
    public void The_service_pins_the_configured_owner_session_and_binary()
    {
        var options = Options(
            ("CompanionSid", "S-1-5-21-1-2-3-1001"),
            ("CompanionImagePath", @"C:\Program Files\PagentOS\agent\companion\PagentOS.SessionCompanion.exe"),
            ("CompanionSessionId", "3"));

        var policy = DeviceService.Program.BuildAdmissionPolicy(options);

        Assert.Equal("S-1-5-21-1-2-3-1001", policy.AuthorizedSid);
        Assert.True(policy.PinsImagePath);
        Assert.Equal(3, policy.ExpectedSessionId);
    }

    [Fact]
    public void An_install_that_forgets_the_owner_sid_is_told_so_out_loud()
    {
        // The fallback is deliberate â€” a developer run has no configured owner â€” but it must
        // never be silent, because in a service install it means the service just authorized
        // its own account instead of the owner's.
        var original = Console.Error;
        var captured = new StringWriter();
        Console.SetError(captured);
        try
        {
            var policy = DeviceService.Program.BuildAdmissionPolicy(Options(("CompanionSid", null)));
            Assert.Equal(WindowsIdentity.GetCurrent().User!.Value, policy.AuthorizedSid);
        }
        finally
        {
            Console.SetError(original);
        }

        var warning = captured.ToString();
        Assert.Contains("CompanionSid", warning, StringComparison.Ordinal);
        Assert.Contains("warning", warning, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void An_install_that_forgets_the_companion_binary_is_told_so_out_loud()
    {
        // Without the binary pin, every process running as the owner in an interactive
        // session is admitted. That is a much bigger hole than it looks in a config file, so
        // it gets the same treatment as a missing SID.
        var original = Console.Error;
        var captured = new StringWriter();
        Console.SetError(captured);
        try
        {
            var policy = DeviceService.Program.BuildAdmissionPolicy(Options(("CompanionSid", "S-1-5-21-1-2-3-1001")));
            Assert.False(policy.PinsImagePath);
        }
        finally
        {
            Console.SetError(original);
        }

        Assert.Contains("CompanionImagePath", captured.ToString(), StringComparison.Ordinal);
    }

    // ------------------------------------------------------------- pipe-name agreement

    [Fact]
    public void The_service_names_its_pipe_after_the_owner_not_after_itself()
    {
        // Under Session 0 the service runs as LocalSystem. Naming the pipe after the running
        // process would put it on pagentos-companion-S-1-5-18 while the companion waited on
        // the owner's name â€” two healthy halves that never meet.
        var options = Options(("CompanionSid", "S-1-5-21-1-2-3-1001"));

        Assert.Equal("pagentos-companion-S-1-5-21-1-2-3-1001", options.PipeName);
        Assert.NotEqual(PipeNaming.DefaultPipeName(), options.PipeName);
    }

    [Fact]
    public void An_explicit_pipe_name_still_wins()
    {
        var options = Options(("CompanionSid", "S-1-5-21-1-2-3-1001"), ("PipeName", "pagentos-e2e-custom"));
        Assert.Equal("pagentos-e2e-custom", options.PipeName);
    }

    [Fact]
    public void With_no_owner_configured_the_service_falls_back_to_its_own_name()
    {
        Assert.Equal(PipeNaming.DefaultPipeName(), Options().PipeName);
    }

    // ------------------------------------------------------ the audit trail is written

    [Fact]
    public async Task A_refused_peer_is_written_to_the_audit_trail_and_told_nothing()
    {
        // Both halves of qualification criterion 1.10, which previously had confident
        // evidence text and no executing assertion: the refusal reaches the owner's audit
        // log, and the peer learns nothing at all â€” not even that it was refused.
        var dir = Path.Combine(Path.GetTempPath(), "pagentos-ipc-audit", Guid.NewGuid().ToString("N"));
        var auditPath = Path.Combine(dir, "agent-audit.jsonl");
        var audit = new AuditLog(auditPath);

        var pipeName = IpcTestSupport.NewPipeName();
        var hostile = IpcTestSupport.SelfPeer() with { Sid = "S-1-5-21-99-99-99-1001" };
        var server = new CompanionPipeServer(
            pipeName,
            IpcTestSupport.SelfPolicy(),
            new FixedPeerInspector(hostile),
            Microsoft.Extensions.Logging.Abstractions.NullLogger<CompanionPipeServer>.Instance,
            audit);
        await server.StartAsync(CancellationToken.None);
        try
        {
            await using var client = new NamedPipeClientStream(
                ".", pipeName, PipeDirection.InOut, PipeOptions.Asynchronous, TokenImpersonationLevel.Identification);
            await client.ConnectAsync(5000);

            // The peer is told nothing: no challenge, no error frame, just end of stream.
            using var reader = new StreamReader(client, new UTF8Encoding(false), false, leaveOpen: true);
            var read = reader.ReadLineAsync(CancellationToken.None).AsTask();
            var finished = await Task.WhenAny(read, Task.Delay(TimeSpan.FromSeconds(3)));
            if (finished == read)
            {
                Assert.Null(await read);
            }

            var deadline = DateTime.UtcNow.AddSeconds(15);
            string content;
            while (true)
            {
                content = File.Exists(auditPath) ? File.ReadAllText(auditPath) : string.Empty;
                if (content.Contains("ipc_peer_refused", StringComparison.Ordinal))
                {
                    break;
                }

                Assert.True(DateTime.UtcNow < deadline, "no ipc_peer_refused row was ever written");
                await Task.Delay(50);
            }

            Assert.Contains("SidMismatch", content, StringComparison.Ordinal);
            // The audit says who was refused, which is the point of having it.
            Assert.Contains("S-1-5-21-99-99-99-1001", content, StringComparison.Ordinal);
        }
        finally
        {
            await server.StopAsync(CancellationToken.None);
            TryDelete(dir);
        }
    }

    [Fact]
    public async Task An_admitted_companion_is_written_to_the_audit_trail()
    {
        var dir = Path.Combine(Path.GetTempPath(), "pagentos-ipc-audit", Guid.NewGuid().ToString("N"));
        var auditPath = Path.Combine(dir, "agent-audit.jsonl");
        var audit = new AuditLog(auditPath);

        var pipeName = IpcTestSupport.NewPipeName();
        var server = new CompanionPipeServer(
            pipeName,
            IpcTestSupport.SelfPolicy(),
            new WindowsPipePeerInspector(),
            Microsoft.Extensions.Logging.Abstractions.NullLogger<CompanionPipeServer>.Instance,
            audit);
        await server.StartAsync(CancellationToken.None);
        try
        {
            await using var client = new NamedPipeClientStream(
                ".", pipeName, PipeDirection.InOut, PipeOptions.Asynchronous, TokenImpersonationLevel.Identification);
            await client.ConnectAsync(5000);
            using var reader = new StreamReader(client, new UTF8Encoding(false), false, leaveOpen: true);
            await using var writer = new StreamWriter(client, new UTF8Encoding(false), leaveOpen: true) { AutoFlush = true };

            var challenge = Assert.IsType<ServiceChallenge>(
                PipeJson.Deserialize((await reader.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(5)))!));
            await writer.WriteLineAsync(PipeJson.Serialize(new CompanionHello
            {
                Capabilities = Agent.Core.Protocol.AgentCapabilities.All,
                ConnectionId = challenge.ConnectionId,
                Nonce = challenge.Nonce,
                Seq = 1,
            }));

            var deadline = DateTime.UtcNow.AddSeconds(15);
            while (!server.CompanionConnected)
            {
                Assert.True(DateTime.UtcNow < deadline, "the companion was never admitted");
                await Task.Delay(20);
            }

            string content = string.Empty;
            while (DateTime.UtcNow < deadline)
            {
                content = File.Exists(auditPath) ? File.ReadAllText(auditPath) : string.Empty;
                if (content.Contains("ipc_companion_admitted", StringComparison.Ordinal))
                {
                    break;
                }

                await Task.Delay(50);
            }

            Assert.Contains("ipc_companion_admitted", content, StringComparison.Ordinal);
            Assert.Contains(IpcTestSupport.CurrentSid(), content, StringComparison.Ordinal);
            // Never the connection secret in full: the audit is a record, not a key store.
            Assert.DoesNotContain(challenge.Nonce, content, StringComparison.Ordinal);
        }
        finally
        {
            await server.StopAsync(CancellationToken.None);
            TryDelete(dir);
        }
    }

    // ----------------------------------------- the pipe DACL, proven from the REAL handle

    [Fact]
    public async Task The_created_pipes_effective_dacl_names_exactly_the_intended_principals()
    {
        // Runtime proof, not configuration inspection: the SDDL is read back from the real
        // pipe handle after creation and must name the authorized owner SID explicitly, plus
        // the creating (service) account — and nobody else. Measured on this machine: the
        // DACL cannot be read externally at ANY later point without either failing (busy) or
        // consuming the listening instance, so creation time is the only honest observation
        // point and the audit row is how a verifier sees it.
        var ownerSid = IpcTestSupport.CurrentSid();
        var dir = Path.Combine(Path.GetTempPath(), "pagentos-pipe-sddl", Guid.NewGuid().ToString("N"));
        var auditPath = Path.Combine(dir, "agent-audit.jsonl");
        var server = new CompanionPipeServer(
            IpcTestSupport.NewPipeName(),
            IpcTestSupport.SelfPolicy(),
            new WindowsPipePeerInspector(),
            Microsoft.Extensions.Logging.Abstractions.NullLogger<CompanionPipeServer>.Instance,
            new AuditLog(auditPath));
        await server.StartAsync(CancellationToken.None);
        try
        {
            var deadline = DateTime.UtcNow.AddSeconds(15);
            while (server.LastPipeSddl is null)
            {
                Assert.True(DateTime.UtcNow < deadline, "the pipe never reported its effective SDDL");
                await Task.Delay(20);
            }

            var sddl = server.LastPipeSddl!;
            var descriptor = new System.Security.AccessControl.RawSecurityDescriptor(sddl);
            var aces = descriptor.DiscretionaryAcl!.Cast<System.Security.AccessControl.CommonAce>().ToList();

            Assert.NotEmpty(aces);
            // In this test the creating account IS the owner, so exactly one principal is
            // legitimate; anything else at all is a finding.
            Assert.All(aces, ace => Assert.Equal(ownerSid, ace.SecurityIdentifier.Value));
            Assert.DoesNotContain("S-1-5-32-545", sddl); // Users
            Assert.DoesNotContain("S-1-1-0", sddl);      // Everyone
            Assert.DoesNotContain("S-1-5-11", sddl);     // Authenticated Users

            // And the audit row a verifier reads must carry the same SDDL.
            var audit = File.ReadAllText(auditPath);
            Assert.Contains("ipc_pipe_created", audit, StringComparison.Ordinal);
            Assert.Contains(sddl, audit, StringComparison.Ordinal);
        }
        finally
        {
            await server.StopAsync(CancellationToken.None);
            try { Directory.Delete(dir, recursive: true); } catch (Exception) { }
        }
    }

    // ---------------------------------------------- the pipe-owner inspector, positively

    [Fact]
    public async Task The_owner_inspector_reads_a_real_owner_sid_and_the_policy_accepts_it()
    {
        // The refusal path is proven elsewhere against a real standard-user pipe. This is the
        // other half: the real GetSecurityInfo path resolves an owner correctly, and a policy
        // that trusts that owner accepts it. A test process cannot create a SYSTEM-owned
        // pipe, so the SYSTEM case stays PROVEN_PROXY until the service is installed â€” but
        // "the inspector can read an owner at all" is no longer assumed.
        var pipeName = IpcTestSupport.NewPipeName();
        // Owner stated, not inherited from the token: elevated hosts default new objects to
        // BUILTIN\Administrators, which ServiceMode trusts, and the last assertion below
        // would then be asserting the host's elevation rather than the policy.
        using var server = IpcTestSupport.NewPipeOwnedByCurrentUser(pipeName);
        var accepting = server.WaitForConnectionAsync();

        await using var client = new NamedPipeClientStream(
            ".", pipeName, PipeDirection.InOut, PipeOptions.Asynchronous, TokenImpersonationLevel.Identification);
        await client.ConnectAsync(5000);
        await accepting.WaitAsync(TimeSpan.FromSeconds(5));

        var owner = new WindowsPipeOwnerInspector().OwnerSid(client);

        Assert.Equal(IpcTestSupport.CurrentSid(), owner);
        Assert.Equal(IpcRefusal.None, ServiceAdmissionPolicy.DeveloperMode(owner!).Evaluate(owner));
        Assert.Equal(IpcRefusal.UntrustedPipeOwner, ServiceAdmissionPolicy.ServiceMode().Evaluate(owner));
    }

    private static void TryDelete(string directory)
    {
        try
        {
            Directory.Delete(directory, recursive: true);
        }
        catch (Exception)
        {
            // Best-effort cleanup.
        }
    }
}

