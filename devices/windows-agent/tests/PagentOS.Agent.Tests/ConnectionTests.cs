using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Identity;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using Xunit;

namespace PagentOS.Agent.Tests;

public class ConnectionTests
{
    [Fact]
    public async Task Handshake_happy_path_establishes_session_and_heartbeats_flow()
    {
        await using var broker = await FakeBroker.StartAsync(heartbeatIntervalS: 0.2);

        // Pre-create the identity so the broker can enforce signature verification.
        var dataDir = TestPaths.NewTempDir();
        using (var preCreated = DeviceIdentity.LoadOrCreate(Path.Combine(dataDir, "device.key")))
        {
            broker.ExpectedPublicKeySpkiB64 = preCreated.PublicKeySpkiBase64;
        }

        var executor = ScriptedExecutor.Returning(() => new JsonObject { ["pid"] = 1 });
        await using var agent = new AgentHarness(broker.WsUri, executor, dataDir);

        var session = await broker.WaitForSessionAsync();
        Assert.Equal(agent.DeviceId, session.Hello.DeviceId);
        Assert.Equal(1, session.Hello.ProtocolVersion);
        Assert.Contains(AgentCapabilities.DesktopOpenApplication, session.Hello.Capabilities);
        Assert.Equal(AgentInfo.SoftwareVersion, session.Hello.SoftwareVersion);

        // Heartbeats at the welcome-provided interval.
        var first = await session.WaitForHeartbeatAsync();
        var second = await session.WaitForHeartbeatAsync();
        Assert.True(second.Seq > first.Seq);
    }

    [Fact]
    public async Task Command_executes_with_monotonic_acks_and_result()
    {
        await using var broker = await FakeBroker.StartAsync();
        var executor = ScriptedExecutor.Returning(() => new JsonObject { ["pid"] = 4242, ["executable"] = "x" });
        await using var agent = new AgentHarness(broker.WsUri, executor);
        var session = await broker.WaitForSessionAsync();

        var command = TestCommands.New();
        await session.SendCommandAsync(command);

        await session.WaitForAckAsync(command.CommandId, AckStatus.Accepted);
        await session.WaitForAckAsync(command.CommandId, AckStatus.Running);
        var terminal = await session.WaitForAckAsync(command.CommandId, AckStatus.Succeeded);
        Assert.Equal(4242, terminal.Result!["pid"]!.GetValue<int>());
        Assert.Equal(1, executor.Invocations);
    }

    [Fact]
    public async Task Duplicate_delivery_executes_once_and_reacks_terminal()
    {
        await using var broker = await FakeBroker.StartAsync();
        var executor = ScriptedExecutor.Returning(() => new JsonObject { ["pid"] = 7 });
        await using var agent = new AgentHarness(broker.WsUri, executor);
        var session = await broker.WaitForSessionAsync();

        var command = TestCommands.New();
        await session.SendCommandAsync(command);
        await session.WaitForAckAsync(command.CommandId, AckStatus.Succeeded);

        // At-least-once delivery: the same command arrives again.
        await session.SendCommandAsync(command);
        var reack = await session.WaitForAckAsync(command.CommandId, AckStatus.Succeeded);
        Assert.Equal(7, reack.Result!["pid"]!.GetValue<int>());
        Assert.Equal(1, executor.Invocations);
    }

    [Fact]
    public async Task Cancel_before_terminal_aborts_and_acks_cancelled()
    {
        await using var broker = await FakeBroker.StartAsync();
        var executor = ScriptedExecutor.BlockingUntilCancelled();
        await using var agent = new AgentHarness(broker.WsUri, executor);
        var session = await broker.WaitForSessionAsync();

        var command = TestCommands.New();
        await session.SendCommandAsync(command);
        await session.WaitForAckAsync(command.CommandId, AckStatus.Running);

        await session.SendAsync(new CancelMessage { CommandId = command.CommandId });
        var terminal = await session.WaitForAckAsync(command.CommandId, AckStatus.Failed);
        Assert.Equal(ErrorClasses.Cancelled, terminal.Error!.Class);
        Assert.Equal(1, executor.Invocations);
    }

    [Fact]
    public async Task Expired_command_is_rejected_over_the_wire()
    {
        await using var broker = await FakeBroker.StartAsync();
        var executor = ScriptedExecutor.Returning(() => new JsonObject { ["pid"] = 1 });
        await using var agent = new AgentHarness(broker.WsUri, executor);
        var session = await broker.WaitForSessionAsync();

        var command = TestCommands.New(expiresIn: TimeSpan.FromMinutes(-5));
        await session.SendCommandAsync(command);
        var terminal = await session.WaitForAckAsync(command.CommandId, AckStatus.Failed);
        Assert.Equal(ErrorClasses.CommandExpired, terminal.Error!.Class);
        Assert.Equal(0, executor.Invocations);
    }

    [Fact]
    public async Task Malformed_frames_get_validation_error_and_connection_survives()
    {
        await using var broker = await FakeBroker.StartAsync();
        var executor = ScriptedExecutor.Returning(() => new JsonObject { ["pid"] = 9 });
        await using var agent = new AgentHarness(broker.WsUri, executor);
        var session = await broker.WaitForSessionAsync();

        await session.SendRawAsync("this is not json at all");
        var firstError = await session.WaitForErrorAsync();
        Assert.Equal(ErrorClasses.ValidationError, firstError.Error.Class);

        await session.SendRawAsync("""{"type":"bogus_type","x":1}""");
        var secondError = await session.WaitForErrorAsync();
        Assert.Equal(ErrorClasses.ValidationError, secondError.Error.Class);

        // A structurally valid command with an invalid envelope references the command_id.
        var badCommand = """
            {"type":"command","command":{"command_id":"3d2f1a9c-5b6e-47a1-9c3d-2e8f7a6b5c4d",
            "idempotency_key":"short","capability":"desktop.open_application","payload":{},
            "expires_at":"2099-01-01T00:00:00Z","trace_id":"t"}}
            """;
        await session.SendRawAsync(badCommand);
        var thirdError = await session.WaitForErrorAsync();
        Assert.Equal(ErrorClasses.ValidationError, thirdError.Error.Class);
        Assert.Equal("3d2f1a9c-5b6e-47a1-9c3d-2e8f7a6b5c4d", thirdError.CommandId);

        // The connection stayed open: a valid command still executes.
        var command = TestCommands.New();
        await session.SendCommandAsync(command);
        await session.WaitForAckAsync(command.CommandId, AckStatus.Succeeded);
    }

    [Fact]
    public async Task Agent_reconnects_after_broker_restart_and_completes_pending_command()
    {
        var port = FakeBroker.GetFreePort();
        var firstBroker = await FakeBroker.StartAsync(port);

        var gate = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var executor = new ScriptedExecutor(async (_, cancellationToken) =>
        {
            await gate.Task.WaitAsync(cancellationToken);
            return new JsonObject { ["pid"] = 314 };
        });
        await using var agent = new AgentHarness(new Uri($"ws://127.0.0.1:{port}/v1/devices/connect"), executor);

        var firstSession = await firstBroker.WaitForSessionAsync();
        var command = TestCommands.New();
        await firstSession.SendCommandAsync(command);
        await firstSession.WaitForAckAsync(command.CommandId, AckStatus.Running);

        // Broker dies while the command is still executing.
        await firstBroker.DisposeAsync();

        // Broker restarts on the same endpoint; the agent must reconnect on its own.
        await using var secondBroker = await FakeBroker.StartAsync(port);
        var secondSession = await secondBroker.WaitForSessionAsync(TimeSpan.FromSeconds(20));
        Assert.Equal(agent.DeviceId, secondSession.Hello.DeviceId);

        // Redelivery of the still-pending command must not re-execute it.
        await secondSession.SendCommandAsync(command);

        // Now let the pending execution finish; the terminal ack must arrive on the new session.
        gate.SetResult();
        var terminal = await secondSession.WaitForAckAsync(command.CommandId, AckStatus.Succeeded);
        Assert.Equal(314, terminal.Result!["pid"]!.GetValue<int>());
        Assert.Equal(1, executor.Invocations);
    }
}
