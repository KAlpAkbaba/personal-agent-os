using System.Collections.Concurrent;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Idempotency;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using Xunit;

namespace PagentOS.Agent.Tests;

public class DispatcherTests
{
    private static (CommandDispatcher Dispatcher, ConcurrentQueue<CommandAckMessage> Acks, IdempotencyStore Store)
        CreateDispatcher(ICapabilityExecutor executor)
    {
        var dir = TestPaths.NewTempDir();
        var store = new IdempotencyStore(Path.Combine(dir, "idempotency.json"));
        var audit = new AuditLog(Path.Combine(dir, "audit.jsonl"));
        var dispatcher = new CommandDispatcher(store, executor, audit, NullLogger<CommandDispatcher>.Instance);
        var acks = new ConcurrentQueue<CommandAckMessage>();
        dispatcher.AttachSender((ack, _) =>
        {
            acks.Enqueue(ack);
            return Task.CompletedTask;
        });
        return (dispatcher, acks, store);
    }

    [Fact]
    public async Task Expired_command_is_rejected_and_never_executed()
    {
        var executor = ScriptedExecutor.Returning(() => new JsonObject { ["pid"] = 1 });
        var (dispatcher, acks, store) = CreateDispatcher(executor);

        var command = TestCommands.New(expiresIn: TimeSpan.FromMinutes(-1));
        await dispatcher.HandleCommandAsync(command);

        Assert.Equal(0, executor.Invocations);
        var terminal = Assert.Single(acks);
        Assert.Equal(AckStatus.Failed, terminal.Status);
        Assert.Equal(ErrorClasses.CommandExpired, terminal.Error!.Class);
        Assert.False(terminal.Error.Retryable);

        // The expiry rejection is itself terminal and idempotent.
        Assert.True(store.TryGetTerminalAck(command.IdempotencyKey, out var cached));
        Assert.Equal(ErrorClasses.CommandExpired, cached!.Error!.Class);
    }

    [Fact]
    public async Task Successful_command_acks_monotonically_and_is_persisted()
    {
        var executor = ScriptedExecutor.Returning(() => new JsonObject { ["pid"] = 77 });
        var (dispatcher, acks, store) = CreateDispatcher(executor);

        var command = TestCommands.New();
        await dispatcher.HandleCommandAsync(command);

        Assert.Equal(
            new[] { AckStatus.Accepted, AckStatus.Running, AckStatus.Succeeded },
            acks.Select(a => a.Status).ToArray());
        Assert.Equal(1, executor.Invocations);
        Assert.True(store.TryGetTerminalAck(command.IdempotencyKey, out _));
    }

    [Fact]
    public async Task Duplicate_after_terminal_reacks_without_reexecution()
    {
        var executor = ScriptedExecutor.Returning(() => new JsonObject { ["pid"] = 77 });
        var (dispatcher, acks, _) = CreateDispatcher(executor);

        var command = TestCommands.New();
        await dispatcher.HandleCommandAsync(command);
        acks.Clear();

        await dispatcher.HandleCommandAsync(command);
        Assert.Equal(1, executor.Invocations);
        var reack = Assert.Single(acks);
        Assert.Equal(AckStatus.Succeeded, reack.Status);
        Assert.Equal(command.CommandId, reack.CommandId);
    }

    [Fact]
    public async Task Capability_failure_maps_to_typed_failed_ack()
    {
        var executor = new ScriptedExecutor((_, _) =>
            throw new CapabilityException(ErrorClasses.CapabilityMissing, "nope", retryable: false));
        var (dispatcher, acks, _) = CreateDispatcher(executor);

        await dispatcher.HandleCommandAsync(TestCommands.New());
        var terminal = acks.Last();
        Assert.Equal(AckStatus.Failed, terminal.Status);
        Assert.Equal(ErrorClasses.CapabilityMissing, terminal.Error!.Class);
    }

    [Fact]
    public async Task Unexpected_exception_maps_to_internal_bug()
    {
        var executor = new ScriptedExecutor((_, _) => throw new InvalidOperationException("boom"));
        var (dispatcher, acks, _) = CreateDispatcher(executor);

        await dispatcher.HandleCommandAsync(TestCommands.New());
        var terminal = acks.Last();
        Assert.Equal(AckStatus.Failed, terminal.Status);
        Assert.Equal(ErrorClasses.InternalBug, terminal.Error!.Class);
        Assert.False(terminal.Error.Retryable);
    }

    [Fact]
    public async Task Cancel_of_in_flight_command_produces_cancelled_terminal_ack()
    {
        var executor = ScriptedExecutor.BlockingUntilCancelled();
        var (dispatcher, acks, store) = CreateDispatcher(executor);

        var command = TestCommands.New();
        var handling = dispatcher.HandleCommandAsync(command);

        // Wait for the running ack before cancelling.
        var deadline = DateTime.UtcNow.AddSeconds(10);
        while (!acks.Any(a => a.Status == AckStatus.Running) && DateTime.UtcNow < deadline)
        {
            await Task.Delay(10);
        }

        await dispatcher.HandleCancelAsync(command.CommandId);
        await handling.WaitAsync(TimeSpan.FromSeconds(10));

        var terminal = acks.Last();
        Assert.Equal(AckStatus.Failed, terminal.Status);
        Assert.Equal(ErrorClasses.Cancelled, terminal.Error!.Class);
        Assert.True(store.TryGetTerminalAck(command.IdempotencyKey, out _));
    }

    [Fact]
    public async Task Cancel_after_terminal_resends_terminal_ack()
    {
        var executor = ScriptedExecutor.Returning(() => new JsonObject { ["pid"] = 5 });
        var (dispatcher, acks, _) = CreateDispatcher(executor);

        var command = TestCommands.New();
        await dispatcher.HandleCommandAsync(command);
        acks.Clear();

        await dispatcher.HandleCancelAsync(command.CommandId);
        var reack = Assert.Single(acks);
        Assert.Equal(AckStatus.Succeeded, reack.Status);
        Assert.Equal(command.CommandId, reack.CommandId);
    }
}
