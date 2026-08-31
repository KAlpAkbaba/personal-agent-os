using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Connection;
using PagentOS.Agent.Core.Identity;
using PagentOS.Agent.Core.Idempotency;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.Agent.Tests.Support;

public static class TestPaths
{
    public static string NewTempDir()
    {
        var dir = Path.Combine(Path.GetTempPath(), "pagentos-agent-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(dir);
        return dir;
    }
}

/// <summary>Counting capability executor with scriptable behavior.</summary>
public sealed class ScriptedExecutor(Func<CommandEnvelope, CancellationToken, Task<JsonObject?>> implementation) : ICapabilityExecutor
{
    private int _invocations;

    public int Invocations => Volatile.Read(ref _invocations);

    public Task<JsonObject?> ExecuteAsync(CommandEnvelope command, CancellationToken cancellationToken)
    {
        Interlocked.Increment(ref _invocations);
        return implementation(command, cancellationToken);
    }

    public static ScriptedExecutor Returning(Func<JsonObject?> resultFactory)
        => new((_, _) => Task.FromResult(resultFactory()));

    public static ScriptedExecutor BlockingUntilCancelled()
        => new(async (_, cancellationToken) =>
        {
            await Task.Delay(Timeout.InfiniteTimeSpan, cancellationToken);
            return null;
        });
}

public static class TestCommands
{
    public static CommandEnvelope New(
        string capability = AgentCapabilities.DesktopOpenApplication,
        JsonObject? payload = null,
        TimeSpan? expiresIn = null,
        string? idempotencyKey = null,
        string? commandId = null)
        => new()
        {
            CommandId = commandId ?? Guid.NewGuid().ToString(),
            IdempotencyKey = idempotencyKey ?? Guid.NewGuid().ToString(),
            Capability = capability,
            Payload = payload ?? new JsonObject { ["application"] = "notepad" },
            ExpiresAt = DateTimeOffset.UtcNow + (expiresIn ?? TimeSpan.FromMinutes(5)),
            TraceId = $"trace-{Guid.NewGuid():N}",
        };
}

/// <summary>A full in-process agent (identity + dispatcher + connection loop) with fast backoff.</summary>
public sealed class AgentHarness : IAsyncDisposable
{
    private readonly CancellationTokenSource _cts = new();
    private readonly Task _runTask;

    public AgentHarness(
        Uri wsUri,
        ICapabilityExecutor executor,
        string? dataDir = null,
        double? heartbeatOverrideS = null)
    {
        DataDir = dataDir ?? TestPaths.NewTempDir();
        Identity = DeviceIdentity.LoadOrCreate(Path.Combine(DataDir, "device.key"));
        DeviceId = Guid.NewGuid().ToString();
        Audit = new AuditLog(Path.Combine(DataDir, "audit.jsonl"));
        Store = new IdempotencyStore(Path.Combine(DataDir, "idempotency.json"));
        Dispatcher = new CommandDispatcher(Store, executor, Audit, NullLogger<CommandDispatcher>.Instance);
        var connection = new AgentConnection(
            new AgentConnectionOptions
            {
                BrokerWsUrl = wsUri,
                DeviceId = DeviceId,
                BackoffBaseSeconds = 0.05,
                BackoffMaxSeconds = 0.25,
                HeartbeatIntervalOverrideS = heartbeatOverrideS,
            },
            Identity,
            Dispatcher,
            Audit,
            NullLogger<AgentConnection>.Instance);
        _runTask = Task.Run(() => connection.RunAsync(_cts.Token));
    }

    public string DataDir { get; }

    public string DeviceId { get; }

    public DeviceIdentity Identity { get; }

    public AuditLog Audit { get; }

    public IdempotencyStore Store { get; }

    public CommandDispatcher Dispatcher { get; }

    public async ValueTask DisposeAsync()
    {
        _cts.Cancel();
        try
        {
            await _runTask.WaitAsync(TimeSpan.FromSeconds(10));
        }
        catch (Exception)
        {
            // Best-effort teardown.
        }

        Identity.Dispose();
        _cts.Dispose();
    }
}
