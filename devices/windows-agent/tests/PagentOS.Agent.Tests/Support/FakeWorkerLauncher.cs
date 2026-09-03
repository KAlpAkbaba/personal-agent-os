using System.Collections.Concurrent;
using System.Runtime.InteropServices;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Connection;
using PagentOS.SessionCompanion;

namespace PagentOS.Agent.Tests.Support;

/// <summary>
/// Locates the fake Browser Worker executable (built beside the tests from
/// <c>tests/PagentOS.Agent.Tests.FakeBrowserWorker</c>) and builds hosts around it. The
/// host is the REAL <see cref="BrowserWorkerHost"/>; only the process on the other end of
/// stdin/stdout is a stand-in, and it needs neither Python nor a browser.
/// </summary>
public static class FakeWorkerLauncher
{
    private const string AssemblyName = "PagentOS.Agent.Tests.FakeBrowserWorker";

    /// <summary>The command + leading arguments that start the fake worker, as the companion would be configured.</summary>
    public static (string Command, string Args) Launcher()
    {
        var exe = Path.Combine(AppContext.BaseDirectory, AssemblyName + ".exe");
        if (File.Exists(exe))
        {
            return (exe, string.Empty);
        }

        // No apphost beside the tests: run the assembly on the same runtime the tests use.
        var dll = Path.Combine(AppContext.BaseDirectory, AssemblyName + ".dll");
        if (!File.Exists(dll))
        {
            throw new FileNotFoundException($"the fake browser worker was not built beside the tests: {dll}");
        }

        var runtimeDir = RuntimeEnvironment.GetRuntimeDirectory().TrimEnd(Path.DirectorySeparatorChar);
        var dotnetRoot = Path.GetDirectoryName(Path.GetDirectoryName(Path.GetDirectoryName(runtimeDir)))
                         ?? throw new InvalidOperationException("cannot locate the dotnet root from the runtime directory");
        var dotnet = Path.Combine(dotnetRoot, "dotnet.exe");
        if (!File.Exists(dotnet))
        {
            throw new FileNotFoundException($"dotnet host not found at {dotnet}");
        }

        return (dotnet, $"\"{dll}\"");
    }

    public static BrowserWorkerOptions Options(string dataDir, string extraArgs = "", bool eager = false, bool visible = false)
    {
        var (command, args) = Launcher();
        var joined = string.IsNullOrEmpty(extraArgs) ? args : $"{args} {extraArgs}".Trim();
        return new BrowserWorkerOptions
        {
            WorkerCommand = command,
            WorkerArgs = joined,
            DataDir = dataDir,
            ProfileDir = Path.Combine(dataDir, "profile"),
            Channel = "chrome",
            Visible = visible,
            IdleTimeoutS = 600,
            Eager = eager,
        };
    }

    /// <summary>A host with test-speed backoff, a short hello wait and (optionally) a fast ping.</summary>
    public static BrowserWorkerHost NewHost(
        string dataDir,
        ILogger? logger = null,
        AuditLog? audit = null,
        string extraArgs = "",
        bool eager = false,
        TimeSpan? helloTimeout = null,
        TimeSpan? pingInterval = null,
        int? eagerRestartCeiling = null)
        => new(
            Options(dataDir, extraArgs, eager),
            logger ?? new ListLogger(),
            audit,
            restartBackoff: new BackoffPolicy(baseSeconds: 0.02, maxSeconds: 0.1),
            helloTimeout: helloTimeout ?? TimeSpan.FromSeconds(20),
            pingInterval: pingInterval,
            shutdownGrace: TimeSpan.FromSeconds(5),
            eagerRestartCeiling: eagerRestartCeiling);
}

/// <summary>
/// A companion transport that must never be reached: any call is a test failure. Stands
/// in for the pipe server when the point under test is that the executor refused a command
/// BEFORE forwarding it — proven by a count of zero, not inferred from an error class.
/// </summary>
public sealed class UnreachableCompanionTransport : PagentOS.DeviceService.ICompanionCapabilityTransport
{
    private int _calls;

    public int Calls => Volatile.Read(ref _calls);

    public Task<System.Text.Json.Nodes.JsonObject?> ExecuteCapabilityAsync(string capability, System.Text.Json.Nodes.JsonObject payload, TimeSpan timeout, CancellationToken cancellationToken)
    {
        Interlocked.Increment(ref _calls);
        throw new InvalidOperationException($"the executor forwarded '{capability}' to the companion; it should have been refused before the pipe");
    }
}

/// <summary>Captures every log line so a test can assert what the companion would have written — and what it would not.</summary>
public sealed class ListLogger : ILogger
{
    private readonly ConcurrentQueue<string> _lines = new();

    public IReadOnlyCollection<string> Lines => _lines;

    public IDisposable BeginScope<TState>(TState state) where TState : notnull => NullScope.Instance;

    public bool IsEnabled(LogLevel logLevel) => true;

    public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception? exception, Func<TState, Exception?, string> formatter)
        => _lines.Enqueue($"[{logLevel}] {formatter(state, exception)}");

    public bool Any(string fragment) => _lines.Any(line => line.Contains(fragment, StringComparison.Ordinal));

    private sealed class NullScope : IDisposable
    {
        public static readonly NullScope Instance = new();

        public void Dispose()
        {
        }
    }
}
