using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Connection;
using PagentOS.Agent.Core.Identity;
using PagentOS.Agent.Core.Idempotency;
using PagentOS.Agent.Core.Ipc;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.DeviceService;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>
/// ADR-0039: the <c>voice_sideband</c> frame is ADDITIVE to the qualified Device Service /
/// Session Companion. These tests are the proof of that word: the frame reaches the
/// companion opaquely and bounded; nothing on the command/ack path changes; a build with no
/// sink drops it without answering the broker; the pipe's freshness rule applies to it.
/// </summary>
public class VoiceSidebandForwardingTests
{
    private static readonly string CmdPath = Environment.ExpandEnvironmentVariables(@"%WINDIR%\System32\cmd.exe");

    private static VoiceSidebandMessage Frame(string @event = "say", JsonObject? payload = null) => new()
    {
        SessionId = Guid.NewGuid().ToString(),
        Event = @event,
        Payload = payload ?? new JsonObject { ["text"] = "Bir dakika." },
        At = "2026-09-02T12:00:00Z",
    };

    private sealed class RecordingSink : ISidebandFrameSink
    {
        public List<VoiceSidebandMessage> Frames { get; } = new();

        public ValueTask<bool> ForwardAsync(VoiceSidebandMessage frame, CancellationToken cancellationToken)
        {
            lock (Frames)
            {
                Frames.Add(frame);
            }

            return new ValueTask<bool>(true);
        }
    }

    private sealed class RecordingForwardSink : ISidebandForwardSink
    {
        public List<JsonObject> Frames { get; } = new();

        public void Accept(JsonObject frame)
        {
            lock (Frames)
            {
                Frames.Add(frame);
            }
        }
    }

    // ------------------------------------------------------------ protocol model

    [Fact]
    public void The_frame_round_trips_through_the_protocol_serializer_with_the_schemas_field_names()
    {
        var raw = """{"type":"voice_sideband","session_id":"7f4c2a1e-0000-4000-8000-000000000001","event":"tool_completed","payload":{"call_id":"c1","status":"succeeded","result":{"n":1}},"at":"2026-09-02T12:00:00Z"}""";
        var message = Assert.IsType<VoiceSidebandMessage>(ProtocolJson.Deserialize(raw));
        MessageValidator.ValidateInbound(message);

        Assert.Equal("tool_completed", message.Event);
        Assert.Equal("c1", message.Payload["call_id"]!.GetValue<string>());
        var frame = message.ToFrame();
        Assert.Equal("voice_sideband", frame["type"]!.GetValue<string>());
        Assert.Equal(new[] { "type", "session_id", "event", "payload", "at" }, frame.Select(p => p.Key).ToArray());
        Assert.Equal(1, frame["payload"]!["result"]!["n"]!.GetValue<int>());
    }

    [Fact]
    public void The_validator_refuses_a_bad_session_id_a_bad_event_name_and_an_oversize_frame_but_never_reads_the_payload()
    {
        Assert.Throws<ProtocolValidationException>(() => MessageValidator.ValidateInbound(Frame() with { SessionId = "not-a-uuid" }));
        Assert.Throws<ProtocolValidationException>(() => MessageValidator.ValidateInbound(Frame() with { Event = "Say Something" }));
        Assert.Throws<ProtocolValidationException>(() => MessageValidator.ValidateInbound(Frame(payload: new JsonObject { ["blob"] = new string('x', VoiceSideband.MaxFrameBytes) })));
        // an event the agent has never heard of is still fine by shape: the vocabulary is Cloud Core's and the companion's
        MessageValidator.ValidateInbound(Frame("plan_changed_v2"));
        // and the payload's content is none of the service's business: forbidden-looking keys pass here
        MessageValidator.ValidateInbound(Frame(payload: new JsonObject { ["anything"] = new JsonObject { ["nested"] = true } }));
        Assert.True(Frame().SerializedBytes() < VoiceSideband.MaxFrameBytes);
    }

    // ---------------------------------------------------- broker -> agent -> sink

    [Fact]
    public async Task A_voice_sideband_frame_from_the_broker_reaches_the_sink_and_changes_nothing_on_the_command_path()
    {
        await using var broker = await FakeBroker.StartAsync();
        var executor = ScriptedExecutor.Returning(() => new JsonObject { ["pid"] = 1 });
        var sink = new RecordingSink();
        await using var agent = new SidebandAgentHarness(broker.WsUri, executor, sink);
        var session = await broker.WaitForSessionAsync();

        var frame = Frame("tool_completed", new JsonObject { ["call_id"] = "c1", ["status"] = "succeeded" });
        await session.SendAsync(frame);
        var deadline = DateTime.UtcNow.AddSeconds(10);
        while (sink.Frames.Count == 0)
        {
            Assert.True(DateTime.UtcNow < deadline, "the sideband frame never reached the sink");
            await Task.Delay(20);
        }

        Assert.Equal("tool_completed", sink.Frames.Single().Event);
        Assert.Equal("c1", sink.Frames.Single().Payload["call_id"]!.GetValue<string>());

        // No reply of any kind for the frame, and commands still ack exactly as before.
        var command = TestCommands.New();
        await session.SendCommandAsync(command);
        await session.WaitForAckAsync(command.CommandId, AckStatus.Accepted);
        await session.WaitForAckAsync(command.CommandId, AckStatus.Running);
        var terminal = await session.WaitForAckAsync(command.CommandId, AckStatus.Succeeded);
        Assert.Equal(1, terminal.Result!["pid"]!.GetValue<int>());
        Assert.Equal(1, executor.Invocations);
        // and nothing about the frame is written to the owner's audit trail (no per-frame audit semantics)
        var auditText = File.Exists(agent.AuditPath) ? File.ReadAllText(agent.AuditPath) : string.Empty;
        Assert.DoesNotContain("voice_sideband", auditText, StringComparison.Ordinal);
    }

    [Fact]
    public async Task With_no_sink_the_frame_is_dropped_silently_and_a_malformed_one_is_still_answered_with_an_error_frame()
    {
        await using var broker = await FakeBroker.StartAsync();
        var executor = ScriptedExecutor.Returning(() => new JsonObject { ["pid"] = 1 });
        await using var agent = new AgentHarness(broker.WsUri, executor);
        var session = await broker.WaitForSessionAsync();

        await session.SendAsync(Frame());
        // then a command: if the frame had produced any reply it would arrive before these acks
        var command = TestCommands.New();
        await session.SendCommandAsync(command);
        await session.WaitForAckAsync(command.CommandId, AckStatus.Succeeded);

        // oversize sideband frame: malformed by the protocol's rules -> error frame, connection stays open
        await session.SendRawAsync(ProtocolJson.Serialize(Frame(payload: new JsonObject { ["blob"] = new string('x', VoiceSideband.MaxFrameBytes) })));
        var error = await session.WaitForErrorAsync();
        Assert.Equal(ErrorClasses.ValidationError, error.Error.Class);
        Assert.Contains("voice_sideband", error.Error.Message);
        Assert.Null(error.CommandId);
        var after = TestCommands.New();
        await session.SendCommandAsync(after);
        await session.WaitForAckAsync(after.CommandId, AckStatus.Succeeded);
    }

    // ---------------------------------------------- service -> pipe -> companion

    [Fact]
    public async Task The_service_forwards_the_frame_over_the_real_pipe_and_the_companion_hands_it_to_its_sink()
    {
        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        var companionSink = new RecordingForwardSink();
        using var companionCts = new CancellationTokenSource();
        var companion = new CompanionRuntime(
            pipeName,
            new AppLauncher(new Dictionary<string, string> { ["cmdtest"] = CmdPath }),
            new ArtifactOpener(new[] { Path.GetTempPath() }, new RecordingFileOpener()),
            NullLogger.Instance,
            new BackoffPolicy(baseSeconds: 0.05, maxSeconds: 0.2),
            ServiceAdmissionPolicy.DeveloperMode(IpcTestSupport.CurrentSid()),
            sidebandSink: companionSink);
        var companionTask = Task.Run(() => companion.RunAsync(companionCts.Token));
        try
        {
            var deadline = DateTime.UtcNow.AddSeconds(15);
            while (!server.CompanionConnected)
            {
                Assert.True(DateTime.UtcNow < deadline, "companion did not connect in time");
                await Task.Delay(20);
            }

            // Not connected -> false, never an exception: the frame is Cloud Core's to re-queue.
            var forwarder = new PipeSidebandForwarder(server);
            var frame = Frame("say");
            Assert.True(await forwarder.ForwardAsync(frame, CancellationToken.None));

            deadline = DateTime.UtcNow.AddSeconds(10);
            while (companionSink.Frames.Count == 0)
            {
                Assert.True(DateTime.UtcNow < deadline, "the companion never received the forwarded frame");
                await Task.Delay(20);
            }

            var received = companionSink.Frames.Single();
            Assert.Equal("voice_sideband", received["type"]!.GetValue<string>());
            Assert.Equal(frame.SessionId, received["session_id"]!.GetValue<string>());
            Assert.Equal("Bir dakika.", received["payload"]!["text"]!.GetValue<string>());
            Assert.Equal(1, server.SidebandForwarded);
            Assert.Equal(1, companion.SidebandAccepted);

            // An oversize frame is refused before the pipe.
            Assert.False(await server.ForwardSidebandAsync(new JsonObject
            {
                ["type"] = "voice_sideband",
                ["session_id"] = Guid.NewGuid().ToString(),
                ["event"] = "say",
                ["payload"] = new JsonObject { ["blob"] = new string('x', VoiceSideband.MaxFrameBytes + 1) },
            }, CancellationToken.None));
            Assert.Equal(1, server.SidebandDropped);

            // And the command path over the same pipe is untouched: a forward in between did not
            // disturb the sequence the exec path relies on.
            var result = await server.ExecuteCapabilityAsync(
                AgentCapabilities.DesktopOpenApplication,
                new JsonObject { ["application"] = "cmdtest", ["args"] = new JsonArray("/c", "exit") },
                TimeSpan.FromSeconds(15),
                CancellationToken.None);
            Assert.True(result!["pid"]!.GetValue<int>() > 0);
            Assert.True(await forwarder.ForwardAsync(Frame("plan_changed", new JsonObject { ["revision"] = 2 }), CancellationToken.None));
            deadline = DateTime.UtcNow.AddSeconds(10);
            while (companionSink.Frames.Count < 2)
            {
                Assert.True(DateTime.UtcNow < deadline, "the second frame never arrived");
                await Task.Delay(20);
            }

            Assert.Empty(companion.Refusals);
        }
        finally
        {
            companionCts.Cancel();
            try
            {
                await companionTask.WaitAsync(TimeSpan.FromSeconds(5));
            }
            catch (Exception)
            {
                // Teardown only.
            }

            await server.StopAsync(CancellationToken.None);
        }
    }

    [Fact]
    public async Task Without_a_companion_the_forward_is_a_false_not_a_failure()
    {
        var server = IpcTestSupport.NewServer(IpcTestSupport.NewPipeName());
        await server.StartAsync(CancellationToken.None);
        try
        {
            Assert.False(await new PipeSidebandForwarder(server).ForwardAsync(Frame(), CancellationToken.None));
            Assert.Equal(1, server.SidebandDropped);
        }
        finally
        {
            await server.StopAsync(CancellationToken.None);
        }
    }

    [Fact]
    public void The_pipe_frame_is_opaque_and_carries_the_connection_binding()
    {
        var forward = new SidebandForward
        {
            ConnectionId = "abc",
            Seq = 7,
            Frame = new JsonObject { ["type"] = "voice_sideband", ["session_id"] = "s", ["event"] = "say", ["payload"] = new JsonObject() },
        };
        var line = PipeJson.Serialize(forward);
        Assert.Contains("\"type\":\"voice_sideband\"", line);
        var parsed = Assert.IsType<SidebandForward>(PipeJson.Deserialize(line));
        Assert.Equal("abc", parsed.ConnectionId);
        Assert.Equal(7, parsed.Seq);
        Assert.Equal("say", parsed.Frame["event"]!.GetValue<string>());
        // the companion-side guard treats it like any other frame: a stale connection id is refused
        var guard = new IpcChannelGuard("real-conn");
        Assert.Equal(IpcRefusal.StaleConnection, guard.Accept(parsed.ConnectionId, parsed.Seq));
        Assert.Equal(IpcRefusal.None, guard.Accept("real-conn", 7));
        Assert.Equal(IpcRefusal.ReplayedFrame, guard.Accept("real-conn", 7));
    }

    /// <summary>An in-process agent with a sideband sink attached (the AgentHarness constructor is untouched).</summary>
    private sealed class SidebandAgentHarness : IAsyncDisposable
    {
        private readonly CancellationTokenSource _cts = new();
        private readonly Task _runTask;
        private readonly DeviceIdentity _identity;

        public SidebandAgentHarness(Uri wsUri, ICapabilityExecutor executor, ISidebandFrameSink sink)
        {
            var dataDir = TestPaths.NewTempDir();
            _identity = DeviceIdentity.LoadOrCreate(Path.Combine(dataDir, "device.key"), developerRun: true);
            AuditPath = Path.Combine(dataDir, "audit.jsonl");
            Audit = new AuditLog(AuditPath);
            var dispatcher = new CommandDispatcher(new IdempotencyStore(Path.Combine(dataDir, "idempotency.json")), executor, Audit, NullLogger<CommandDispatcher>.Instance);
            var connection = new AgentConnection(
                new AgentConnectionOptions
                {
                    BrokerWsUrl = wsUri,
                    DeviceId = Guid.NewGuid().ToString(),
                    BackoffBaseSeconds = 0.05,
                    BackoffMaxSeconds = 0.25,
                },
                _identity,
                dispatcher,
                Audit,
                NullLogger<AgentConnection>.Instance,
                sidebandSink: sink);
            _runTask = Task.Run(() => connection.RunAsync(_cts.Token));
        }

        public AuditLog Audit { get; }

        public string AuditPath { get; }

        public async ValueTask DisposeAsync()
        {
            _cts.Cancel();
            try
            {
                await _runTask.WaitAsync(TimeSpan.FromSeconds(10));
            }
            catch (Exception)
            {
            }

            _identity.Dispose();
            _cts.Dispose();
        }
    }
}
