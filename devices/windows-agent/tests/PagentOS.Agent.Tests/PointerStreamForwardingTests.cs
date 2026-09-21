using System.IO.Pipes;
using System.Security.Principal;
using System.Text;
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
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>
/// ADR-0199, the device half of the pointer stream between the broker and the companion:
/// the <c>pointer_stream</c> frame is ADDITIVE and BEST EFFORT. Proven here: the frame
/// round-trips with the ADR's names; it reaches the sink and is never a command, an ack, an
/// audit row or an idempotency entry; a malformed one is counted and dropped without an
/// error frame and without a disconnect; the service forwards one batch as ONE one-way
/// <c>pointer.stream</c> request, only for a session the companion opened through it; and
/// the companion applies a one-way request inline and never answers it.
/// </summary>
public class PointerStreamForwardingTests
{
    private static readonly string CmdPath = Environment.ExpandEnvironmentVariables(@"%WINDIR%\System32\cmd.exe");

    private static JsonObject Move(int dx, int dy, int seq) => new() { ["t"] = "move", ["dx"] = dx, ["dy"] = dy, ["seq"] = seq };

    private static JsonObject Click(string button = "left") => new() { ["t"] = "button", ["button"] = button, ["action"] = "click" };

    private static PointerStreamMessage Frame(string session, params JsonNode[] frames) => new()
    {
        Session = session,
        Frames = new JsonArray(frames.Length == 0 ? [Move(1, 2, 1)] : frames),
    };

    private sealed class RecordingSink : IPointerStreamFrameSink
    {
        public List<PointerStreamMessage> Frames { get; } = new();

        public ValueTask<bool> ForwardAsync(PointerStreamMessage frame, CancellationToken cancellationToken)
        {
            lock (Frames)
            {
                Frames.Add(frame);
            }

            return new ValueTask<bool>(true);
        }
    }

    private sealed class RecordingApplier : IPointerStreamApplier
    {
        public List<JsonObject> Batches { get; } = new();

        public List<string> Ended { get; } = new();

        public PointerBatchOutcome Apply(JsonObject payload)
        {
            lock (Batches)
            {
                Batches.Add(payload);
            }

            return new PointerBatchOutcome(payload["frames"]?.AsArray().Count ?? 0, 0, null);
        }

        public void EndAll(string reason)
        {
            lock (Ended)
            {
                Ended.Add(reason);
            }
        }
    }

    // ------------------------------------------------------------ protocol model

    [Fact]
    public void The_frame_round_trips_through_the_protocol_serializer_with_the_adrs_field_names()
    {
        var raw = """{"type":"pointer_stream","session":"rt-1","frames":[{"t":"move","dx":3,"dy":-4,"seq":7},{"t":"button","button":"left","action":"click"},{"t":"end"}]}""";
        var message = Assert.IsType<PointerStreamMessage>(ProtocolJson.Deserialize(raw));
        MessageValidator.ValidateInbound(message);

        Assert.Equal("rt-1", message.Session);
        Assert.Equal(3, message.Frames.Count);
        var payload = message.ToPayload();
        Assert.Equal(new[] { "session", "frames" }, payload.Select(p => p.Key).ToArray());
        Assert.True(PointerFrame.TryParse(payload["frames"]![0], out var move));
        Assert.Equal((3, -4, 7L), (move.Dx, move.Dy, move.Seq));
        Assert.True(PointerFrame.TryParse(payload["frames"]![1], out var click) && click.IsButton && click.Action == "click");
        Assert.True(PointerFrame.TryParse(payload["frames"]![2], out var end) && end.IsEnd);
        // the payload is a fresh copy, never the frame's own array
        payload["frames"]!.AsArray().Clear();
        Assert.Equal(3, message.Frames.Count);

        var serialized = ProtocolJson.Serialize(message);
        Assert.Contains("\"type\":\"pointer_stream\"", serialized, StringComparison.Ordinal);
        Assert.Contains("\"session\":\"rt-1\"", serialized, StringComparison.Ordinal);
        Assert.Contains("\"frames\":[", serialized, StringComparison.Ordinal);
    }

    [Fact]
    public void The_validator_refuses_a_bad_session_too_many_frames_and_an_oversize_frame_but_never_reads_the_frames()
    {
        Assert.Throws<ProtocolValidationException>(() => MessageValidator.ValidateInbound(Frame("bad session!")));
        Assert.Throws<ProtocolValidationException>(() => MessageValidator.ValidateInbound(Frame(string.Empty)));
        Assert.Throws<ProtocolValidationException>(() => MessageValidator.ValidateInbound(Frame(new string('s', PointerStream.MaxSessionLength + 1))));
        Assert.Throws<ProtocolValidationException>(() => MessageValidator.ValidateInbound(
            Frame("ok", Enumerable.Range(0, PointerStream.MaxFramesPerBatch + 1).Select(i => (JsonNode)Move(i, 0, i)).ToArray())));
        Assert.Throws<ProtocolValidationException>(() => MessageValidator.ValidateInbound(
            Frame("ok", new JsonObject { ["t"] = "move", ["blob"] = new string('x', PointerStream.MaxFrameBytes) })));

        // exactly the bound passes; a uuid session and a dotted one pass; garbage INSIDE the
        // frames passes here - it is the companion's to drop and count, one frame at a time
        MessageValidator.ValidateInbound(Frame("ok", Enumerable.Range(0, PointerStream.MaxFramesPerBatch).Select(i => (JsonNode)Move(i, 0, i)).ToArray()));
        MessageValidator.ValidateInbound(Frame(Guid.NewGuid().ToString()));
        MessageValidator.ValidateInbound(Frame("rt.session:1-a"));
        MessageValidator.ValidateInbound(Frame("ok", new JsonObject { ["t"] = "jump" }, JsonValue.Create(42)!));
        Assert.True(Frame("ok").SerializedBytes() < PointerStream.MaxFrameBytes);
    }

    // ---------------------------------------------------- broker -> agent -> sink

    [Fact]
    public async Task A_pointer_stream_frame_from_the_broker_reaches_the_sink_and_is_never_a_command_an_ack_or_a_row()
    {
        await using var broker = await FakeBroker.StartAsync();
        var executor = ScriptedExecutor.Returning(() => new JsonObject { ["pid"] = 1 });
        var sink = new RecordingSink();
        await using var agent = new PointerAgentHarness(broker.WsUri, executor, sink);
        var session = await broker.WaitForSessionAsync();

        var frame = Frame("ps-1", Move(5, -5, 1), Move(6, -6, 2), Click());
        await session.SendAsync(frame);
        var deadline = DateTime.UtcNow.AddSeconds(10);
        while (sink.Frames.Count == 0)
        {
            Assert.True(DateTime.UtcNow < deadline, "the pointer_stream frame never reached the sink");
            await Task.Delay(20);
        }

        var received = sink.Frames.Single();
        Assert.Equal("ps-1", received.Session);
        Assert.Equal(3, received.Frames.Count);

        // Then a command: it acks exactly as before, and NOTHING about the frame preceded it -
        // no ack, no error - proven by order on the broker's own record of what arrived.
        var command = TestCommands.New();
        await session.SendCommandAsync(command);
        await session.WaitForAckAsync(command.CommandId, AckStatus.Succeeded);
        var seen = session.SeenSoFar();
        var firstAck = seen.ToList().FindIndex(m => m is CommandAckMessage);
        Assert.True(firstAck >= 0);
        Assert.All(seen.Take(firstAck), m => Assert.True(m is HeartbeatMessage, $"the frame produced a {m.GetType().Name} before the command's first ack"));
        Assert.DoesNotContain(seen, m => m is ErrorMessage);
        Assert.Equal(1, executor.Invocations);
        Assert.Equal(0, agent.Connection.PointerStreamDropped);
        Assert.Equal(0, agent.Connection.PointerStreamMalformed);

        // and no row anywhere: not in the audit trail, not in the idempotency store
        Assert.DoesNotContain("pointer_stream", LiveLog.Read(agent.AuditPath), StringComparison.Ordinal);
        Assert.DoesNotContain("ps-1", LiveLog.Read(agent.AuditPath), StringComparison.Ordinal);
        Assert.DoesNotContain("ps-1", LiveLog.Read(agent.IdempotencyPath), StringComparison.Ordinal);
    }

    [Fact]
    public async Task A_malformed_pointer_stream_is_counted_and_dropped_without_an_error_frame_and_the_connection_stays_up()
    {
        await using var broker = await FakeBroker.StartAsync();
        var executor = ScriptedExecutor.Returning(() => new JsonObject { ["pid"] = 1 });
        var sink = new RecordingSink();
        await using var agent = new PointerAgentHarness(broker.WsUri, executor, sink);
        var session = await broker.WaitForSessionAsync();

        // three shapes of wrong: frames not an array; a session that fails the pattern; too many frames
        await session.SendRawAsync("""{"type":"pointer_stream","session":"ps-2","frames":"nope"}""");
        await session.SendRawAsync("""{"type":"pointer_stream","session":"bad session!","frames":[]}""");
        await session.SendRawAsync(ProtocolJson.Serialize(Frame("ps-3", Enumerable.Range(0, PointerStream.MaxFramesPerBatch + 1).Select(i => (JsonNode)Move(i, 0, i)).ToArray())));
        // and one good one after them, which must still get through
        await session.SendAsync(Frame("ps-4"));

        var deadline = DateTime.UtcNow.AddSeconds(10);
        while (sink.Frames.Count == 0)
        {
            Assert.True(DateTime.UtcNow < deadline, "the good frame after the malformed ones never reached the sink");
            await Task.Delay(20);
        }

        Assert.Equal("ps-4", sink.Frames.Single().Session);
        Assert.Equal(3, agent.Connection.PointerStreamMalformed);

        var command = TestCommands.New();
        await session.SendCommandAsync(command);
        await session.WaitForAckAsync(command.CommandId, AckStatus.Succeeded);
        Assert.DoesNotContain(session.SeenSoFar(), m => m is ErrorMessage);

        // The error path is not gone for everything else: a malformed voice_sideband is
        // still answered with an error frame, exactly as before ADR-0199.
        await session.SendRawAsync("""{"type":"voice_sideband","session_id":"not-a-uuid","event":"say","payload":{}}""");
        var error = await session.WaitForErrorAsync();
        Assert.Equal(ErrorClasses.ValidationError, error.Error.Class);
        Assert.Contains("voice_sideband", error.Error.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task With_no_sink_the_frame_is_dropped_and_counted_and_the_command_path_is_untouched()
    {
        await using var broker = await FakeBroker.StartAsync();
        var executor = ScriptedExecutor.Returning(() => new JsonObject { ["pid"] = 1 });
        await using var agent = new PointerAgentHarness(broker.WsUri, executor, sink: null);
        var session = await broker.WaitForSessionAsync();

        await session.SendAsync(Frame("ps-5"));
        var command = TestCommands.New();
        await session.SendCommandAsync(command);
        await session.WaitForAckAsync(command.CommandId, AckStatus.Succeeded);

        // ordered behind the command's ack, so the count is settled
        Assert.Equal(1, agent.Connection.PointerStreamDropped);
        Assert.Equal(1, executor.Invocations);
        Assert.DoesNotContain(session.SeenSoFar(), m => m is ErrorMessage);
    }

    // ---------------------------------------------- service -> pipe (raw companion)

    [Fact]
    public async Task The_service_forwards_one_batch_as_one_one_way_request_and_only_for_a_session_the_companion_opened_through_it()
    {
        var pipeName = IpcTestSupport.NewPipeName();
        var server = await IpcTestSupport.NewListeningServerAsync(pipeName);
        try
        {
            await using var companion = await RawCompanion.ConnectAsync(pipeName, OperatorCapabilityNames.PointerStreamFamily);
            var deadline = DateTime.UtcNow.AddSeconds(15);
            while (!server.CompanionConnected)
            {
                Assert.True(DateTime.UtcNow < deadline, "the raw companion was not admitted in time");
                await Task.Delay(20);
            }

            var forwarder = new PipePointerStreamForwarder(server);

            // Before any begin: ignored, counted, and NOTHING crosses the pipe.
            Assert.False(await forwarder.ForwardAsync(Frame("s1"), CancellationToken.None));
            Assert.Equal(1, server.PointerStreamIgnored);
            Assert.Equal(0, server.PointerStreamForwarded);

            // pointer.stream_begin through the ordinary command path, answered like any capability.
            var beginTask = server.ExecuteCapabilityAsync(OperatorCapabilityNames.PointerStreamBegin, new JsonObject { ["session"] = "s1", ["window_id"] = "w-1-1" }, TimeSpan.FromSeconds(10), CancellationToken.None);
            var beginRequest = await companion.ReadRequestAsync();
            Assert.Equal(OperatorCapabilityNames.PointerStreamBegin, beginRequest.Capability);
            Assert.False(beginRequest.OneWay);
            await companion.AnswerAsync(beginRequest, new JsonObject { ["session"] = "s1", ["window_id"] = "w-1-1" });
            var begin = await beginTask;
            Assert.Equal("s1", begin!["session"]!.GetValue<string>());
            Assert.Equal(new[] { "s1" }, server.OpenPointerSessions);

            // The batch: ONE request, one-way, the whole batch as its payload, no answer awaited.
            var batchFrame = Frame("s1", Move(1, 2, 1), Move(3, 4, 2), Click("right"));
            Assert.True(await forwarder.ForwardAsync(batchFrame, CancellationToken.None));
            var batch = await companion.ReadRequestAsync();
            Assert.Equal(OperatorCapabilityNames.PointerStream, batch.Capability);
            Assert.True(batch.OneWay, "the batch must be marked one_way on the pipe");
            Assert.Equal("s1", batch.Payload["session"]!.GetValue<string>());
            var frames = batch.Payload["frames"]!.AsArray();
            Assert.Equal(3, frames.Count);
            Assert.True(PointerFrame.TryParse(frames[0], out var first) && first.Dx == 1 && first.Dy == 2 && first.Seq == 1);
            Assert.True(PointerFrame.TryParse(frames[2], out var third) && third.ButtonName == "right" && third.Action == "click");
            await WaitForCountAsync(() => server.PointerStreamForwarded, 1, "the forwarded count after the companion read the batch");
            // (ReadRequestAsync accepted the batch's conn_id and seq through the companion's own guard: fresh, in order)

            // A companion that is NOT reading must cost batches, never the caller: the pipe's
            // zero-byte buffers make a write complete only when the peer reads (2026-09-21:
            // the first cut awaited the write inline and this test hung here). Four batches
            // accepted at once while nobody reads; the outbox keeps the newest, the oldest
            // are dropped and counted, and the LAST batch the companion then reads is the
            // newest one - a hand is live, and the frame that matters is the latest.
            var droppedBefore = server.PointerStreamDropped;
            for (var i = 1; i <= 4; i++)
            {
                Assert.True(await forwarder.ForwardAsync(Frame("s1", Move(i, 0, i)), CancellationToken.None));
            }

            var read = 0;
            ExecRequest last;
            do
            {
                last = await companion.ReadRequestAsync();
                read++;
                Assert.Equal(OperatorCapabilityNames.PointerStream, last.Capability);
                Assert.True(last.OneWay);
                Assert.True(read <= 4, "more batches arrived than were forwarded");
            }
            while (last.Payload["frames"]![0]!["dx"]!.GetValue<int>() != 4);

            var droppedByOutbox = server.PointerStreamDropped - droppedBefore;
            Assert.Equal(4, read + droppedByOutbox);
            Assert.True(droppedByOutbox >= 1, "an outbox of two behind a stalled reader must have dropped at least one of four");
            await WaitForCountAsync(() => server.PointerStreamForwarded, 1 + read, "the forwarded count after the stall");

            // A batch for a session nobody opened: ignored before the pipe. Proven by ORDER: the
            // next line the companion reads is the end request, not s2's batch.
            Assert.False(await forwarder.ForwardAsync(Frame("s2"), CancellationToken.None));
            Assert.Equal(2, server.PointerStreamIgnored);
            var endTask = server.ExecuteCapabilityAsync(OperatorCapabilityNames.PointerStreamEnd, new JsonObject { ["session"] = "s1" }, TimeSpan.FromSeconds(10), CancellationToken.None);
            var endRequest = await companion.ReadRequestAsync();
            Assert.Equal(OperatorCapabilityNames.PointerStreamEnd, endRequest.Capability);
            Assert.False(endRequest.OneWay);
            await companion.AnswerAsync(endRequest, new JsonObject { ["session"] = "s1", ["ended"] = true, ["moves"] = 2, ["buttons"] = 1, ["dropped"] = 0, ["duration_ms"] = 10 });
            var end = await endTask;
            Assert.Equal(2, end!["moves"]!.GetValue<int>());
            Assert.Empty(server.OpenPointerSessions);

            // After the end: s1 is a session nobody has again.
            Assert.False(await forwarder.ForwardAsync(Frame("s1"), CancellationToken.None));
            Assert.Equal(3, server.PointerStreamIgnored);
            Assert.Equal(1 + read, server.PointerStreamForwarded);
            Assert.Equal(droppedByOutbox, server.PointerStreamDropped);
            Assert.Empty(server.Refusals);
        }
        finally
        {
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
            Assert.False(await new PipePointerStreamForwarder(server).ForwardAsync(Frame("s1"), CancellationToken.None));
            Assert.Equal(1, server.PointerStreamDropped);
            Assert.Equal(0, server.PointerStreamIgnored);
        }
        finally
        {
            await server.StopAsync(CancellationToken.None);
        }
    }

    // ---------------------------------------------- pipe -> companion (raw service)

    [Fact]
    public async Task The_companion_applies_a_one_way_batch_inline_never_answers_it_and_ends_the_stream_when_the_pipe_closes()
    {
        var pipeName = IpcTestSupport.NewPipeName();
        var applier = new RecordingApplier();
        using var companionCts = new CancellationTokenSource();
        var companion = new CompanionRuntime(
            pipeName,
            new AppLauncher(new Dictionary<string, string> { ["cmdtest"] = CmdPath }),
            new ArtifactOpener(new[] { Path.GetTempPath() }, new RecordingFileOpener()),
            NullLogger.Instance,
            new BackoffPolicy(baseSeconds: 0.05, maxSeconds: 0.2),
            ServiceAdmissionPolicy.DeveloperMode(IpcTestSupport.CurrentSid()),
            pointerStream: applier);

        await using var service = IpcTestSupport.NewPipeOwnedByCurrentUser(pipeName);
        var companionTask = Task.Run(() => companion.RunAsync(companionCts.Token));
        try
        {
            await service.WaitForConnectionAsync().WaitAsync(TimeSpan.FromSeconds(15));
            using var reader = new StreamReader(service, new UTF8Encoding(false), detectEncodingFromByteOrderMarks: false, leaveOpen: true);
            // Disposed by hand BEFORE the pipe is disconnected below: a StreamWriter's dispose
            // flushes, and a flush on a disconnected pipe throws (leaveOpen keeps the stream).
            var writer = new StreamWriter(service, new UTF8Encoding(false), leaveOpen: true) { AutoFlush = true };

            var guard = IpcChannelGuard.Create();
            var nonce = IpcChannelGuard.NewToken();
            await writer.WriteLineAsync(PipeJson.Serialize(new ServiceChallenge { ConnectionId = guard.ConnectionId, Nonce = nonce }));
            var hello = Assert.IsType<CompanionHello>(PipeJson.Deserialize((await reader.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(10)))!));
            Assert.Equal(IpcRefusal.None, guard.Accept(hello.ConnectionId, hello.Seq));

            // The one-way batch, then an ordinary request right behind it.
            await writer.WriteLineAsync(PipeJson.Serialize(new ExecRequest
            {
                RequestId = "one-way-1",
                Capability = OperatorCapabilityNames.PointerStream,
                Payload = new JsonObject { ["session"] = "s1", ["frames"] = new JsonArray(Move(1, 1, 1), Move(2, 2, 2), Click()) },
                TimeoutMs = 1000,
                OneWay = true,
                ConnectionId = guard.ConnectionId,
                Seq = guard.NextOutboundSeq(),
            }));
            await writer.WriteLineAsync(PipeJson.Serialize(new ExecRequest
            {
                RequestId = "probe",
                Capability = AgentCapabilities.DesktopOpenApplication,
                Payload = new JsonObject { ["application"] = "cmdtest", ["args"] = new JsonArray("/c", "exit") },
                TimeoutMs = 15000,
                ConnectionId = guard.ConnectionId,
                Seq = guard.NextOutboundSeq(),
            }));

            // The FIRST thing the companion writes back is the probe's answer: had the one-way
            // batch been answered, its response would have been written before it.
            var response = Assert.IsType<ExecResponse>(PipeJson.Deserialize((await reader.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(20)))!));
            Assert.Equal("probe", response.RequestId);
            Assert.True(response.Ok);
            Assert.Equal(IpcRefusal.None, guard.Accept(response.ConnectionId, response.Seq));

            var batch = Assert.Single(applier.Batches);
            Assert.Equal("s1", batch["session"]!.GetValue<string>());
            Assert.Equal(3, batch["frames"]!.AsArray().Count);
            Assert.Equal(1, companion.PointerStreamAccepted);
            Assert.Equal(0, companion.PointerStreamUnapplied);
            Assert.Empty(companion.Refusals);

            // A one-way request that is not pointer.stream does nothing and answers nothing;
            // the next ordinary request is, again, the first answer to arrive.
            await writer.WriteLineAsync(PipeJson.Serialize(new ExecRequest
            {
                RequestId = "one-way-other",
                Capability = OperatorCapabilityNames.KeyboardType,
                Payload = new JsonObject { ["text"] = "never" },
                TimeoutMs = 1000,
                OneWay = true,
                ConnectionId = guard.ConnectionId,
                Seq = guard.NextOutboundSeq(),
            }));
            await writer.WriteLineAsync(PipeJson.Serialize(new ExecRequest
            {
                RequestId = "probe-2",
                Capability = AgentCapabilities.DesktopOpenApplication,
                Payload = new JsonObject { ["application"] = "cmdtest", ["args"] = new JsonArray("/c", "exit") },
                TimeoutMs = 15000,
                ConnectionId = guard.ConnectionId,
                Seq = guard.NextOutboundSeq(),
            }));
            var second = Assert.IsType<ExecResponse>(PipeJson.Deserialize((await reader.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(20)))!));
            Assert.Equal("probe-2", second.RequestId);
            Assert.Equal(1, companion.PointerStreamUnapplied);
            Assert.Single(applier.Batches);

            // A replayed one-way request (an old sequence) is refused by the freshness rule, like any frame.
            await writer.WriteLineAsync(PipeJson.Serialize(new ExecRequest
            {
                RequestId = "one-way-replay",
                Capability = OperatorCapabilityNames.PointerStream,
                Payload = new JsonObject { ["session"] = "s1", ["frames"] = new JsonArray(Move(9, 9, 9)) },
                TimeoutMs = 1000,
                OneWay = true,
                ConnectionId = guard.ConnectionId,
                Seq = 1,
            }));
            await writer.WriteLineAsync(PipeJson.Serialize(new ExecRequest
            {
                RequestId = "probe-3",
                Capability = AgentCapabilities.DesktopOpenApplication,
                Payload = new JsonObject { ["application"] = "cmdtest", ["args"] = new JsonArray("/c", "exit") },
                TimeoutMs = 15000,
                ConnectionId = guard.ConnectionId,
                Seq = guard.NextOutboundSeq(),
            }));
            var third = Assert.IsType<ExecResponse>(PipeJson.Deserialize((await reader.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(20)))!));
            Assert.Equal("probe-3", third.RequestId);
            Assert.Single(applier.Batches);
            Assert.Equal(1, companion.PointerStreamAccepted);
            Assert.True(companion.Refusals.TryGetValue(IpcRefusal.ReplayedFrame, out var stale) && stale == 1, "the replayed one-way request was not refused as a replay");

            // The pipe closes: the stream is ended on the companion's side, right then.
            await writer.DisposeAsync();
            service.Disconnect();
            var deadline = DateTime.UtcNow.AddSeconds(10);
            while (applier.Ended.Count == 0)
            {
                Assert.True(DateTime.UtcNow < deadline, "the companion did not end the stream when the pipe closed");
                await Task.Delay(20);
            }

            Assert.Equal("pipe_closed", applier.Ended[0]);
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
        }
    }

    /// <summary>A counter that is incremented AFTER the observable event (a write the peer has already read): waited for, bounded, so the assertion is the caller's.</summary>
    private static async Task WaitForCountAsync(Func<int> read, int expected, string what)
    {
        var deadline = DateTime.UtcNow.AddSeconds(5);
        while (read() != expected && DateTime.UtcNow < deadline)
        {
            await Task.Delay(10);
        }

        Assert.True(read() == expected, $"{what}: expected {expected}, read {read()}");
    }

    // ------------------------------------------------------------ harnesses

    /// <summary>A full in-process agent with the pointer sink attached and the connection exposed for its counters.</summary>
    private sealed class PointerAgentHarness : IAsyncDisposable
    {
        private readonly CancellationTokenSource _cts = new();
        private readonly Task _runTask;

        public PointerAgentHarness(Uri wsUri, ICapabilityExecutor executor, IPointerStreamFrameSink? sink)
        {
            var dataDir = TestPaths.NewTempDir();
            var identity = DeviceIdentity.LoadOrCreate(Path.Combine(dataDir, "device.key"), developerRun: true);
            AuditPath = Path.Combine(dataDir, "audit.jsonl");
            IdempotencyPath = Path.Combine(dataDir, "idempotency.json");
            var audit = new AuditLog(AuditPath);
            var dispatcher = new CommandDispatcher(new IdempotencyStore(IdempotencyPath), executor, audit, NullLogger<CommandDispatcher>.Instance);
            Connection = new AgentConnection(
                new AgentConnectionOptions
                {
                    BrokerWsUrl = wsUri,
                    DeviceId = Guid.NewGuid().ToString(),
                    BackoffBaseSeconds = 0.05,
                    BackoffMaxSeconds = 0.25,
                },
                identity,
                dispatcher,
                audit,
                NullLogger<AgentConnection>.Instance,
                pointerSink: sink);
            _runTask = Task.Run(() => Connection.RunAsync(_cts.Token));
        }

        public AgentConnection Connection { get; }

        public string AuditPath { get; }

        public string IdempotencyPath { get; }

        public async ValueTask DisposeAsync()
        {
            _cts.Cancel();
            try
            {
                await _runTask.WaitAsync(TimeSpan.FromSeconds(10));
            }
            catch (Exception)
            {
                // Teardown only.
            }
        }
    }

    /// <summary>
    /// A hand-rolled companion on the real pipe: the challenge/hello handshake and nothing
    /// else, so the test reads EXACTLY what the service wrote, line by line, and answers only
    /// what it chooses to.
    /// </summary>
    private sealed class RawCompanion : IAsyncDisposable
    {
        private readonly NamedPipeClientStream _client;
        private readonly StreamReader _reader;
        private readonly StreamWriter _writer;
        private readonly IpcChannelGuard _guard;

        private RawCompanion(NamedPipeClientStream client, StreamReader reader, StreamWriter writer, IpcChannelGuard guard)
        {
            _client = client;
            _reader = reader;
            _writer = writer;
            _guard = guard;
        }

        public static async Task<RawCompanion> ConnectAsync(string pipeName, IReadOnlyList<string> capabilities)
        {
            var client = new NamedPipeClientStream(".", pipeName, PipeDirection.InOut, PipeOptions.Asynchronous, TokenImpersonationLevel.Identification);
            await client.ConnectAsync(10000);
            var reader = new StreamReader(client, new UTF8Encoding(false), detectEncodingFromByteOrderMarks: false, leaveOpen: true);
            var writer = new StreamWriter(client, new UTF8Encoding(false), leaveOpen: true) { AutoFlush = true };
            var challenge = Assert.IsType<ServiceChallenge>(PipeJson.Deserialize((await reader.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(10)))!));
            var guard = new IpcChannelGuard(challenge.ConnectionId);
            await writer.WriteLineAsync(PipeJson.Serialize(new CompanionHello
            {
                Capabilities = capabilities,
                ConnectionId = challenge.ConnectionId,
                Nonce = challenge.Nonce,
                Seq = guard.NextOutboundSeq(),
            }));
            return new RawCompanion(client, reader, writer, guard);
        }

        public async Task<ExecRequest> ReadRequestAsync()
        {
            var line = await _reader.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(10));
            Assert.NotNull(line);
            var request = Assert.IsType<ExecRequest>(PipeJson.Deserialize(line!));
            Assert.Equal(IpcRefusal.None, _guard.Accept(request.ConnectionId, request.Seq));
            return request;
        }

        public Task AnswerAsync(ExecRequest request, JsonObject result)
            => _writer.WriteLineAsync(PipeJson.Serialize(new ExecResponse
            {
                RequestId = request.RequestId,
                ConnectionId = _guard.ConnectionId,
                Seq = _guard.NextOutboundSeq(),
                Ok = true,
                Result = result,
            }));

        public async ValueTask DisposeAsync()
        {
            _reader.Dispose();
            await _writer.DisposeAsync();
            await _client.DisposeAsync();
        }
    }
}
