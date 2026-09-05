using System.IO.Pipes;
using System.Security.Principal;
using System.Text;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Connection;
using PagentOS.Agent.Core.Ipc;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.DeviceService;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>
/// Adversarial tests for the service ↔ companion channel (M1 security finding #1).
///
/// Each test names the attack it stages and, where the attack cannot be staged for real on a
/// single-account developer machine, says so in its own words rather than implying more than
/// it proves. `docs/QUALIFICATION.md` carries the same distinction per criterion.
/// </summary>
public class IpcSecurityTests
{
    private static readonly string CmdPath =
        Environment.ExpandEnvironmentVariables(@"%WINDIR%\System32\cmd.exe");

    // ------------------------------------------------------------------ wrong SID

    [Fact]
    public void A_peer_running_as_another_account_is_refused()
    {
        // Injected identity: staging a real second account is an owner action, not a unit
        // test. What is proven here is that the decision itself refuses, and with which
        // reason — the kernel lookup that feeds it is proven separately by the honest-path
        // tests that use the real inspector.
        var policy = IpcTestSupport.SelfPolicy();
        var stranger = IpcTestSupport.SelfPeer() with { Sid = "S-1-5-21-99-99-99-1001" };

        Assert.Equal(IpcRefusal.SidMismatch, policy.Evaluate(stranger));
        Assert.Equal(IpcRefusal.None, policy.Evaluate(IpcTestSupport.SelfPeer()));
    }

    [Fact]
    public async Task A_connection_from_an_unauthorized_sid_never_becomes_the_companion()
    {
        // End to end over a real pipe: a real client connects, but the service is told (via
        // the inspector seam) that the peer is a different account. The connection must not
        // become the live companion, and interactive commands must keep failing fast.
        var pipeName = IpcTestSupport.NewPipeName();
        var hostile = IpcTestSupport.SelfPeer() with { Sid = "S-1-5-21-99-99-99-1001" };
        var server = IpcTestSupport.NewServer(pipeName, IpcTestSupport.SelfPolicy(), new FixedPeerInspector(hostile));
        await server.StartAsync(CancellationToken.None);
        await IpcTestSupport.WaitUntilListeningAsync(server, pipeName);
        try
        {
            await using var client = new NamedPipeClientStream(
                ".", pipeName, PipeDirection.InOut, PipeOptions.Asynchronous, TokenImpersonationLevel.Identification);
            await client.ConnectAsync(5000);

            await WaitForRefusalAsync(server, IpcRefusal.SidMismatch);
            Assert.False(server.CompanionConnected);
            Assert.Null(server.ConnectedPeer);

            var ex = await Assert.ThrowsAsync<CapabilityException>(() => server.ExecuteCapabilityAsync(
                AgentCapabilities.DesktopOpenApplication,
                new JsonObject { ["application"] = "cmdtest" },
                TimeSpan.FromSeconds(5),
                CancellationToken.None));
            Assert.Equal(ErrorClasses.DependencyUnavailable, ex.ErrorClass);
        }
        finally
        {
            await server.StopAsync(CancellationToken.None);
        }
    }

    // -------------------------------------------------------------- wrong session

    [Fact]
    public void A_peer_in_session_zero_is_refused_even_with_the_right_sid()
    {
        // Session 0 is where services live. A "companion" there is either a service
        // impersonating the desktop half or a misconfiguration; either way it is not the
        // owner's interactive session, which is the only thing this channel exists to reach.
        var policy = IpcTestSupport.SelfPolicy();
        var inSessionZero = IpcTestSupport.SelfPeer() with { SessionId = 0 };

        Assert.Equal(IpcRefusal.ServiceSessionPeer, policy.Evaluate(inSessionZero));
    }

    [Fact]
    public void A_peer_in_a_different_interactive_session_is_refused_when_the_session_is_pinned()
    {
        var self = IpcTestSupport.SelfPeer();
        var policy = IpcTestSupport.SelfPolicy(sessionId: self.SessionId);
        var otherSession = self with { SessionId = self.SessionId + 7 };

        Assert.Equal(IpcRefusal.SessionMismatch, policy.Evaluate(otherSession));
        Assert.Equal(IpcRefusal.None, policy.Evaluate(self));
    }

    // ----------------------------------------------------- forged companion identity

    [Fact]
    public void The_right_user_running_the_wrong_binary_is_refused()
    {
        // The owner's account can start any program, so "runs as the owner" is not the same
        // as "is the companion". With the binary pinned, an attacker must first be able to
        // write into an administrator-protected directory.
        var self = IpcTestSupport.SelfPeer();
        var policy = IpcTestSupport.SelfPolicy(imagePath: CmdPath);
        var impostor = self with { ImagePath = Path.Combine(Path.GetTempPath(), "not-the-companion.exe") };

        Assert.Equal(IpcRefusal.ImagePathMismatch, policy.Evaluate(impostor));
        Assert.Equal(IpcRefusal.None, policy.Evaluate(self with { ImagePath = CmdPath }));
    }

    [Fact]
    public void A_pinned_binary_refuses_a_peer_whose_image_cannot_be_read()
    {
        var policy = IpcTestSupport.SelfPolicy(imagePath: CmdPath);
        var opaque = IpcTestSupport.SelfPeer() with { ImagePath = null };

        // "I could not check" must never resolve to "close enough".
        Assert.Equal(IpcRefusal.ImagePathMismatch, policy.Evaluate(opaque));
    }

    [Fact]
    public void An_unidentifiable_peer_is_refused()
    {
        Assert.Equal(IpcRefusal.UnidentifiablePeer, IpcTestSupport.SelfPolicy().Evaluate(null));
        Assert.Equal(
            IpcRefusal.UnidentifiablePeer,
            IpcTestSupport.SelfPolicy().Evaluate(IpcTestSupport.SelfPeer() with { Sid = "" }));
    }

    // ------------------------------------------------------------------- fake pipe

    [Fact]
    public async Task The_service_refuses_to_share_a_pipe_name_someone_else_already_holds()
    {
        // Real squatting: a process creates the service's pipe name first, and it does so
        // with room for MORE instances — which is the point. An earlier version of this test
        // used maxNumberOfServerInstances: 1, and Windows blocks a second instance in that
        // case regardless of FILE_FLAG_FIRST_PIPE_INSTANCE, so the test passed even with the
        // flag deleted from production code. It proved a true thing while being cited as
        // evidence for a defense it never exercised (caught by the independent verification
        // of ADR-0028). With a multi-instance squatter, only the flag stops the service from
        // quietly becoming instance #2 behind it.
        var pipeName = IpcTestSupport.NewPipeName();
        using var squatter = new NamedPipeServerStream(
            pipeName, PipeDirection.InOut, 4, PipeTransmissionMode.Byte, PipeOptions.Asynchronous);

        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        await IpcTestSupport.WaitUntilListeningAsync(server, pipeName);
        try
        {
            // The service must have FAILED to listen. Asserting the failure directly, rather
            // than inferring it from "nobody connected", is what makes the flag load-bearing
            // for this test.
            var deadline = DateTime.UtcNow.AddSeconds(15);
            while (server.ListenFailures == 0)
            {
                Assert.True(
                    DateTime.UtcNow < deadline,
                    "the service created a pipe on a name another process already held");
                await Task.Delay(20);
            }

            // And the squatter still owns the name: a client reaches it, not the service.
            await using var client = new NamedPipeClientStream(
                ".", pipeName, PipeDirection.InOut, PipeOptions.Asynchronous, TokenImpersonationLevel.Identification);
            await client.ConnectAsync(5000);
            await squatter.WaitForConnectionAsync().WaitAsync(TimeSpan.FromSeconds(5));

            Assert.True(squatter.IsConnected, "the squatter should still own the pipe name");
            Assert.False(server.CompanionConnected);
        }
        finally
        {
            await server.StopAsync(CancellationToken.None);
        }
    }

    [Fact]
    public async Task A_second_connection_cannot_displace_the_connected_companion()
    {
        // The pipe is single-instance, so a second process cannot get its own channel while
        // the companion holds one. Worth asserting rather than assuming: "the attacker just
        // connects too" is the obvious next move once every other door is shut, and the
        // consequence would be a hijacked exec channel rather than a merely failed connect.
        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        await IpcTestSupport.WaitUntilListeningAsync(server, pipeName);
        using var companionCts = new CancellationTokenSource();
        var companionTask = Task.Run(() => NewCompanion(
            pipeName,
            ServiceAdmissionPolicy.DeveloperMode(IpcTestSupport.CurrentSid()),
            new WindowsPipeOwnerInspector()).RunAsync(companionCts.Token));
        try
        {
            var deadline = DateTime.UtcNow.AddSeconds(15);
            while (!server.CompanionConnected)
            {
                Assert.True(DateTime.UtcNow < deadline, "the companion never connected");
                await Task.Delay(20);
            }

            var intruder = new NamedPipeClientStream(
                ".", pipeName, PipeDirection.InOut, PipeOptions.Asynchronous, TokenImpersonationLevel.Identification);
            await using (intruder)
            {
                await Assert.ThrowsAnyAsync<Exception>(() => intruder.ConnectAsync(1500));
            }

            // The legitimate companion is untouched and still serving.
            Assert.True(server.CompanionConnected);
            var result = await server.ExecuteCapabilityAsync(
                AgentCapabilities.DesktopOpenApplication,
                new JsonObject { ["application"] = "cmdtest", ["args"] = new JsonArray("/c", "exit") },
                TimeSpan.FromSeconds(15),
                CancellationToken.None);
            Assert.True(result!["pid"]!.GetValue<int>() > 0);
        }
        finally
        {
            companionCts.Cancel();
            await SwallowAsync(companionTask);
            await server.StopAsync(CancellationToken.None);
        }
    }

    [Fact]
    public async Task The_companion_refuses_a_pipe_that_is_not_owned_by_a_service_account()
    {
        // The other half of the same attack: the squatter is already there and the companion
        // connects to it. This one IS proven for real — the fake pipe below is a real pipe
        // owned by a real non-service account, and the production policy (SYSTEM and
        // Administrators only) really does reject it via the real WindowsPipeOwnerInspector
        // reading the kernel's security descriptor.
        //
        // It used to say "created by this test process, so its owner really is a standard
        // user". That was the assumption, not the fact: on an elevated host the creating
        // token hands ownership to BUILTIN\Administrators, which the policy trusts, so the
        // impostor was never an impostor and the refusal never fired. Stated explicitly now.
        var pipeName = IpcTestSupport.NewPipeName();
        // The owner is STATED as this account's user SID rather than left to the token's
        // default: elevated hosts hand new objects to BUILTIN\Administrators, which the
        // production policy trusts on purpose, so the impostor would not have been one.
        using var fakeService = IpcTestSupport.NewPipeOwnedByCurrentUser(pipeName);
        var accepted = fakeService.WaitForConnectionAsync();

        var companion = NewCompanion(pipeName, ServiceAdmissionPolicy.ServiceMode(), new WindowsPipeOwnerInspector());
        using var cts = new CancellationTokenSource();
        var run = Task.Run(() => companion.RunAsync(cts.Token));
        try
        {
            await WaitForRefusalAsync(companion, IpcRefusal.UntrustedPipeOwner);

            // And it never spoke: the impostor got a connection and not one frame of intent.
            if (accepted.IsCompletedSuccessfully)
            {
                using var reader = new StreamReader(fakeService, new UTF8Encoding(false), false, leaveOpen: true);
                var read = reader.ReadLineAsync(CancellationToken.None).AsTask();
                var finished = await Task.WhenAny(read, Task.Delay(500, CancellationToken.None));
                if (finished == read)
                {
                    Assert.Null(await read);
                }
            }
        }
        finally
        {
            cts.Cancel();
            await SwallowAsync(run);
        }
    }

    // ------------------------------------ the posture the SHIPPED binary actually runs at

    [Fact]
    public void The_shipped_companion_defaults_to_service_trust_not_developer_trust()
    {
        // Regression for the ADR-0028 review's Critical: the real entry point constructed
        // CompanionRuntime without a policy, and the constructor default was developer mode.
        // Since UAC splits integrity level rather than identity, that meant any process
        // running as the owner could drive the companion on an installed agent. The default
        // must be the production posture, and asking for developer mode must be explicit.
        var owner = IpcTestSupport.CurrentSid();

        var byDefault = SessionCompanion.Program.BuildServicePolicy(null, owner);
        Assert.True(byDefault.RequiresElevatedOwner);
        Assert.Equal(IpcRefusal.UntrustedPipeOwner, byDefault.Evaluate(owner));

        var unknownMode = SessionCompanion.Program.BuildServicePolicy("something-else", owner);
        Assert.True(unknownMode.RequiresElevatedOwner);

        // Asking for developer mode without saying whose pipes to trust stays production
        // rather than widening the trust set to something unnamed.
        var unnamed = SessionCompanion.Program.BuildServicePolicy("developer", null);
        Assert.True(unnamed.RequiresElevatedOwner);

        var explicitlyDeveloper = SessionCompanion.Program.BuildServicePolicy("developer", owner);
        Assert.False(explicitlyDeveloper.RequiresElevatedOwner);
        Assert.Equal(IpcRefusal.None, explicitlyDeveloper.Evaluate(owner));
    }

    [Fact]
    public void A_runtime_built_without_a_policy_refuses_an_owner_owned_pipe()
    {
        // The same defect one layer down: whatever the caller forgets, the runtime's own
        // default must be the safe one.
        var companion = new CompanionRuntime(
            IpcTestSupport.NewPipeName(),
            new AppLauncher(new Dictionary<string, string> { ["cmdtest"] = CmdPath }),
            new ArtifactOpener(new[] { Path.GetTempPath() }, new RecordingFileOpener()),
            NullLogger.Instance);

        Assert.True(companion.ServicePolicy.RequiresElevatedOwner);
        Assert.Equal(IpcRefusal.UntrustedPipeOwner, companion.ServicePolicy.Evaluate(IpcTestSupport.CurrentSid()));
    }

    [Fact]
    public void The_service_and_the_companion_derive_the_same_pipe_name_from_the_owner()
    {
        // Under a Session-0 service the two halves are different accounts. A pipe name
        // derived from "the current user" would put them on different names — two healthy
        // processes that never meet, with nothing in either log saying why.
        var owner = IpcTestSupport.CurrentSid();
        Assert.Equal(PipeNaming.ForOwnerSid(owner), PipeNaming.DefaultPipeName());
        Assert.NotEqual(PipeNaming.ForOwnerSid(ServiceAdmissionPolicy.LocalSystemSid), PipeNaming.ForOwnerSid(owner));
    }

    [Fact]
    public void A_developer_run_trusts_the_owner_but_says_so()
    {
        var owner = IpcTestSupport.CurrentSid();
        var dev = ServiceAdmissionPolicy.DeveloperMode(owner);
        var prod = ServiceAdmissionPolicy.ServiceMode();

        Assert.Equal(IpcRefusal.None, dev.Evaluate(owner));
        Assert.Equal(IpcRefusal.UntrustedPipeOwner, prod.Evaluate(owner));
        Assert.Equal(IpcRefusal.None, prod.Evaluate(ServiceAdmissionPolicy.LocalSystemSid));

        // The flag exists so a report can state which posture produced a PASS instead of
        // letting a developer-mode pass read as a production one.
        Assert.False(dev.RequiresElevatedOwner);
        Assert.True(prod.RequiresElevatedOwner);
    }

    // --------------------------------------------------------------- replayed IPC

    [Fact]
    public void A_frame_replayed_on_the_same_connection_is_refused()
    {
        var guard = IpcChannelGuard.Create();

        Assert.Equal(IpcRefusal.None, guard.Accept(guard.ConnectionId, 1));
        Assert.Equal(IpcRefusal.None, guard.Accept(guard.ConnectionId, 2));

        // The exact frame again, and an older one: both are refused, and neither moves the
        // window, so replaying cannot be used to advance past a pending sequence.
        Assert.Equal(IpcRefusal.ReplayedFrame, guard.Accept(guard.ConnectionId, 2));
        Assert.Equal(IpcRefusal.ReplayedFrame, guard.Accept(guard.ConnectionId, 1));
        Assert.Equal(2, guard.LastInboundSeq);
        Assert.Equal(IpcRefusal.None, guard.Accept(guard.ConnectionId, 3));
    }

    [Fact]
    public void A_frame_with_no_sequence_at_all_is_refused()
    {
        // A v1 frame — no conn_id, no seq — must not be accepted by a v2 service just
        // because its JSON still parses.
        var guard = IpcChannelGuard.Create();
        Assert.Equal(IpcRefusal.ReplayedFrame, guard.Accept(guard.ConnectionId, 0));
        Assert.Equal(IpcRefusal.StaleConnection, guard.Accept(null, 1));
    }

    // ------------------------------------------------------ stale session credentials

    [Fact]
    public void A_frame_from_an_earlier_connection_is_refused_after_reconnect()
    {
        var first = IpcChannelGuard.Create();
        Assert.Equal(IpcRefusal.None, first.Accept(first.ConnectionId, 1));

        // The companion reconnects (logoff/logon, service restart): a new connection id.
        var second = IpcChannelGuard.Create();
        Assert.NotEqual(first.ConnectionId, second.ConnectionId);

        // Credentials from the retired connection are worthless on the new one, at any
        // sequence number — including one that would be perfectly valid there.
        Assert.Equal(IpcRefusal.StaleConnection, second.Accept(first.ConnectionId, 1));
        Assert.Equal(IpcRefusal.StaleConnection, second.Accept(first.ConnectionId, 99));
        Assert.Equal(IpcRefusal.None, second.Accept(second.ConnectionId, 1));
    }

    [Fact]
    public void Connection_ids_are_unpredictable_and_not_derived_from_anything_guessable()
    {
        var ids = Enumerable.Range(0, 64).Select(_ => IpcChannelGuard.NewToken()).ToList();

        Assert.Equal(ids.Count, ids.Distinct(StringComparer.Ordinal).Count());
        Assert.All(ids, id => Assert.Equal(64, id.Length)); // 32 bytes, hex
    }

    [Fact]
    public async Task A_companion_hello_that_does_not_answer_this_challenge_is_refused()
    {
        // Staged for real over a live pipe: connect, read the challenge, then reply with a
        // hello carrying a *different* nonce and connection id — exactly what replaying a
        // captured hello from an earlier connection would look like.
        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        await IpcTestSupport.WaitUntilListeningAsync(server, pipeName);
        try
        {
            await using var client = new NamedPipeClientStream(
                ".", pipeName, PipeDirection.InOut, PipeOptions.Asynchronous, TokenImpersonationLevel.Identification);
            await client.ConnectAsync(5000);

            using var reader = new StreamReader(client, new UTF8Encoding(false), false, leaveOpen: true);
            await using var writer = new StreamWriter(client, new UTF8Encoding(false), leaveOpen: true) { AutoFlush = true };

            var challengeLine = await reader.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(5));
            Assert.NotNull(challengeLine);
            var challenge = Assert.IsType<ServiceChallenge>(PipeJson.Deserialize(challengeLine!));

            var forged = new CompanionHello
            {
                Capabilities = AgentCapabilities.All,
                ConnectionId = IpcChannelGuard.NewToken(),  // some other connection's id
                Nonce = IpcChannelGuard.NewToken(),         // and not this challenge's nonce
                Seq = 1,
            };
            Assert.NotEqual(challenge.Nonce, forged.Nonce);
            await writer.WriteLineAsync(PipeJson.Serialize(forged));

            await WaitForRefusalAsync(server, IpcRefusal.HandshakeFailed);
            Assert.False(server.CompanionConnected);
        }
        finally
        {
            await server.StopAsync(CancellationToken.None);
        }
    }

    [Fact]
    public async Task A_replayed_exec_response_does_not_satisfy_a_later_request()
    {
        // The full replay attack, staged over a real pipe: complete a handshake, answer one
        // request honestly, then re-send that same response frame verbatim. The service must
        // refuse the duplicate — otherwise one recorded "success" could answer any later
        // question the owner asks.
        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        await IpcTestSupport.WaitUntilListeningAsync(server, pipeName);
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
                Capabilities = AgentCapabilities.All,
                ConnectionId = challenge.ConnectionId,
                Nonce = challenge.Nonce,
                Seq = 1,
            }));

            // The hello has been written, but the service admits the connection on its own
            // loop: wait for that rather than racing it.
            await WaitForCompanionAsync(server);

            var first = server.ExecuteCapabilityAsync(
                AgentCapabilities.DesktopOpenApplication,
                new JsonObject { ["application"] = "cmdtest" },
                TimeSpan.FromSeconds(10),
                CancellationToken.None);

            var requestLine = await reader.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(5));
            var request = Assert.IsType<ExecRequest>(PipeJson.Deserialize(requestLine!));
            var responseJson = PipeJson.Serialize(new ExecResponse
            {
                RequestId = request.RequestId,
                Ok = true,
                Result = new JsonObject { ["pid"] = 4242 },
                ConnectionId = challenge.ConnectionId,
                Seq = 2,
            });
            await writer.WriteLineAsync(responseJson);
            Assert.Equal(4242, (await first)!["pid"]!.GetValue<int>());

            // Now the replay: the identical frame, and a second request in flight that it
            // must not be allowed to answer.
            var second = server.ExecuteCapabilityAsync(
                AgentCapabilities.DesktopOpenApplication,
                new JsonObject { ["application"] = "cmdtest" },
                TimeSpan.FromSeconds(10),
                CancellationToken.None);
            await reader.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(5)); // drain request #2
            await writer.WriteLineAsync(responseJson);

            // Wait for the refusal to be recorded before judging the pending request, so the
            // assertion is about the guard's decision and not about which of two clocks won.
            await WaitForRefusalAsync(server, IpcRefusal.ReplayedFrame);
            Assert.False(second.IsCompleted, "the replayed response must not answer a later request");

            var ex = await Assert.ThrowsAsync<CapabilityException>(() => second);
            Assert.Equal(ErrorClasses.Timeout, ex.ErrorClass);
        }
        finally
        {
            await server.StopAsync(CancellationToken.None);
        }
    }

    // ---------------------------------------------------------------------- helpers

    private static CompanionRuntime NewCompanion(
        string pipeName,
        ServiceAdmissionPolicy policy,
        IPipeOwnerInspector inspector)
        => new(
            pipeName,
            new AppLauncher(new Dictionary<string, string> { ["cmdtest"] = CmdPath }),
            new ArtifactOpener(new[] { Path.GetTempPath() }, new RecordingFileOpener()),
            NullLogger.Instance,
            new BackoffPolicy(baseSeconds: 0.05, maxSeconds: 0.2),
            policy,
            inspector);

    private static async Task WaitForCompanionAsync(CompanionPipeServer server)
    {
        var deadline = DateTime.UtcNow.AddSeconds(15);
        while (!server.CompanionConnected)
        {
            Assert.True(DateTime.UtcNow < deadline, "the service never admitted the companion");
            await Task.Delay(20);
        }
    }

    private static async Task WaitForRefusalAsync(CompanionPipeServer server, IpcRefusal refusal)
    {
        var deadline = DateTime.UtcNow.AddSeconds(15);
        while (!server.Refusals.TryGetValue(refusal, out var count) || count == 0)
        {
            Assert.True(DateTime.UtcNow < deadline, $"server never recorded {refusal}");
            await Task.Delay(20);
        }
    }

    private static async Task WaitForRefusalAsync(CompanionRuntime companion, IpcRefusal refusal)
    {
        var deadline = DateTime.UtcNow.AddSeconds(15);
        while (!companion.Refusals.TryGetValue(refusal, out var count) || count == 0)
        {
            Assert.True(DateTime.UtcNow < deadline, $"companion never recorded {refusal}");
            await Task.Delay(20);
        }
    }

    private static async Task SwallowAsync(Task task)
    {
        try
        {
            await task.WaitAsync(TimeSpan.FromSeconds(5));
        }
        catch (Exception)
        {
            // Teardown only.
        }
    }
}
