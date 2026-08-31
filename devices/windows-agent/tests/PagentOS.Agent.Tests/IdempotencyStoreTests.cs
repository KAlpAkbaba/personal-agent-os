using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Idempotency;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using Xunit;

namespace PagentOS.Agent.Tests;

public class IdempotencyStoreTests
{
    private static CommandAckMessage TerminalAck(string? commandId = null, string status = AckStatus.Succeeded)
        => new()
        {
            CommandId = commandId ?? Guid.NewGuid().ToString(),
            Status = status,
            Result = status == AckStatus.Succeeded ? new JsonObject { ["pid"] = 1234 } : null,
            Error = status == AckStatus.Failed
                ? ErrorObjects.Create(ErrorClasses.Cancelled, "cancelled", retryable: false)
                : null,
        };

    [Fact]
    public void Duplicate_key_returns_cached_terminal_ack()
    {
        var path = Path.Combine(TestPaths.NewTempDir(), "idempotency.json");
        var store = new IdempotencyStore(path);
        var ack = TerminalAck();
        store.PutTerminalAck("key-00000001", ack);

        Assert.True(store.TryGetTerminalAck("key-00000001", out var cached));
        Assert.Equal(ack.CommandId, cached!.CommandId);
        Assert.Equal(AckStatus.Succeeded, cached.Status);
        Assert.Equal(1234, cached.Result!["pid"]!.GetValue<int>());

        Assert.False(store.TryGetTerminalAck("key-unknown1", out _));
    }

    [Fact]
    public void Store_survives_reload_from_disk()
    {
        var path = Path.Combine(TestPaths.NewTempDir(), "idempotency.json");
        var ack = TerminalAck(status: AckStatus.Failed);
        var firstStore = new IdempotencyStore(path);
        firstStore.PutTerminalAck("key-00000001", ack);

        var reloaded = new IdempotencyStore(path);
        Assert.True(reloaded.TryGetTerminalAck("key-00000001", out var cached));
        Assert.Equal(ack.CommandId, cached!.CommandId);
        Assert.Equal(AckStatus.Failed, cached.Status);
        Assert.Equal(ErrorClasses.Cancelled, cached.Error!.Class);
    }

    [Fact]
    public void Lookup_by_command_id_works_after_reload()
    {
        var path = Path.Combine(TestPaths.NewTempDir(), "idempotency.json");
        var ack = TerminalAck();
        new IdempotencyStore(path).PutTerminalAck("key-00000001", ack);

        var reloaded = new IdempotencyStore(path);
        Assert.True(reloaded.TryGetAckByCommandId(ack.CommandId, out var cached));
        Assert.Equal(ack.CommandId, cached!.CommandId);
    }

    [Fact]
    public void Lru_bound_evicts_least_recently_used()
    {
        var path = Path.Combine(TestPaths.NewTempDir(), "idempotency.json");
        var store = new IdempotencyStore(path, capacity: 3);
        store.PutTerminalAck("key-00000001", TerminalAck());
        store.PutTerminalAck("key-00000002", TerminalAck());
        store.PutTerminalAck("key-00000003", TerminalAck());

        // Touch key 1 so key 2 becomes the least recently used.
        Assert.True(store.TryGetTerminalAck("key-00000001", out _));

        store.PutTerminalAck("key-00000004", TerminalAck());

        Assert.Equal(3, store.Count);
        Assert.True(store.TryGetTerminalAck("key-00000001", out _));
        Assert.False(store.TryGetTerminalAck("key-00000002", out _));
        Assert.True(store.TryGetTerminalAck("key-00000003", out _));
        Assert.True(store.TryGetTerminalAck("key-00000004", out _));
    }

    [Fact]
    public void Non_terminal_ack_is_rejected()
    {
        var path = Path.Combine(TestPaths.NewTempDir(), "idempotency.json");
        var store = new IdempotencyStore(path);
        var running = new CommandAckMessage { CommandId = Guid.NewGuid().ToString(), Status = AckStatus.Running };
        Assert.Throws<ArgumentException>(() => store.PutTerminalAck("key-00000001", running));
    }

    [Fact]
    public void Corrupt_store_file_starts_empty_instead_of_crashing()
    {
        var path = Path.Combine(TestPaths.NewTempDir(), "idempotency.json");
        File.WriteAllText(path, "{ this is not json");
        var store = new IdempotencyStore(path);
        Assert.Equal(0, store.Count);
        store.PutTerminalAck("key-00000001", TerminalAck());
        Assert.True(store.TryGetTerminalAck("key-00000001", out _));
    }
}
