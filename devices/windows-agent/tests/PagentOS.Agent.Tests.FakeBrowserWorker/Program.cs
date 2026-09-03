using System.Collections.Concurrent;
using System.Text;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.Agent.Tests.FakeBrowserWorker;

/// <summary>
/// A stand-in for <c>python -m browser_agent.worker</c> that speaks the exact stdio protocol
/// of BROWSER_CAPABILITIES.md §7 and nothing else. It needs no Python, no Playwright and
/// no browser, so the companion's <c>BrowserWorkerHost</c> can be driven over REAL stdin/
/// stdout in the unit suite: hello, exec/result correlation, typed errors, timeouts,
/// cancel forwarding, crashes mid-request, oversize and forbidden results, pings, shutdown.
///
/// Behaviour is chosen by the request payload's <c>mode</c> field (default: echo) and by a
/// few process flags, so one binary covers every scenario:
/// <list type="bullet">
/// <item><c>--self-check</c>: print the hello and exit 0 (the real worker's install probe).</item>
/// <item><c>--no-hello</c>: never announce (hello-timeout test).</item>
/// <item><c>--no-pong</c>: ignore pings (liveness test).</item>
/// <item><c>--hello-delay-ms N</c>: announce late.</item>
/// </list>
/// The contract's own CLI arguments (<c>--data-dir</c>, <c>--profile-dir</c>, <c>--channel</c>,
/// <c>--visible|--headless</c>, <c>--idle-timeout-s</c>) are accepted and echoed in the hello
/// as <c>argv</c>, so a test can prove the host passed them.
/// </summary>
public static class Program
{
    private static readonly object StdoutLock = new();
    private static readonly ConcurrentDictionary<string, CancellationTokenSource> InFlight = new(StringComparer.Ordinal);

    public static async Task<int> Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        Console.InputEncoding = new UTF8Encoding(false);
        var stdout = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true };
        var stdin = new StreamReader(Console.OpenStandardInput(), new UTF8Encoding(false));

        var selfCheck = args.Contains("--self-check");
        var noHello = args.Contains("--no-hello");
        var noPong = args.Contains("--no-pong");
        var helloDelay = 0;
        for (var i = 0; i < args.Length - 1; i++)
        {
            if (args[i] == "--hello-delay-ms" && int.TryParse(args[i + 1], out var parsed))
            {
                helloDelay = parsed;
            }
        }

        await Console.Error.WriteLineAsync($"fake-worker: started pid={Environment.ProcessId} argv={string.Join(' ', args)}");

        var hello = new JsonObject
        {
            ["type"] = "hello",
            ["worker_version"] = "fake-1.0",
            ["protocol_version"] = 1,
            ["capabilities"] = new JsonArray([.. BrowserCapabilities.Operations.Select(c => (JsonNode)c)]),
            ["browser"] = new JsonObject
            {
                ["channel"] = "chrome",
                ["available"] = true,
                ["version"] = "fake-chrome-1.0",
            },
            ["argv"] = new JsonArray([.. args.Select(a => (JsonNode)a)]),
        };

        if (selfCheck)
        {
            WriteLine(stdout, hello);
            return 0;
        }

        if (!noHello)
        {
            if (helloDelay > 0)
            {
                await Task.Delay(helloDelay);
            }

            WriteLine(stdout, hello);
        }

        while (true)
        {
            var line = await stdin.ReadLineAsync();
            if (line is null)
            {
                await Console.Error.WriteLineAsync("fake-worker: stdin closed; exiting");
                return 0;
            }

            if (string.IsNullOrWhiteSpace(line))
            {
                continue;
            }

            JsonObject message;
            try
            {
                message = JsonNode.Parse(line) as JsonObject ?? throw new FormatException("not an object");
            }
            catch (Exception ex)
            {
                await Console.Error.WriteLineAsync($"fake-worker: bad line: {ex.Message}");
                continue;
            }

            switch (message["type"]?.GetValue<string>())
            {
                case "exec":
                    _ = Task.Run(() => HandleExecAsync(stdout, message));
                    break;

                case "cancel":
                    var cancelId = message["request_id"]?.GetValue<string>() ?? "";
                    await Console.Error.WriteLineAsync($"fake-worker: cancel request_id={cancelId}");
                    if (InFlight.TryGetValue(cancelId, out var cts))
                    {
                        cts.Cancel();
                    }

                    break;

                case "ping":
                    if (!noPong)
                    {
                        WriteLine(stdout, new JsonObject { ["type"] = "pong", ["sessions"] = InFlight.Count });
                    }

                    break;

                case "shutdown":
                    await Console.Error.WriteLineAsync("fake-worker: shutdown");
                    return 0;

                default:
                    await Console.Error.WriteLineAsync($"fake-worker: unknown message type");
                    break;
            }
        }
    }

    private static async Task HandleExecAsync(StreamWriter stdout, JsonObject message)
    {
        var requestId = message["request_id"]?.GetValue<string>() ?? Guid.NewGuid().ToString("N");
        var capability = message["capability"]?.GetValue<string>() ?? "";
        var payload = message["payload"] as JsonObject ?? new JsonObject();
        var timeoutMs = message["timeout_ms"]?.GetValue<int>() ?? 0;
        var mode = payload["mode"]?.GetValue<string>() ?? "echo";

        using var cts = new CancellationTokenSource();
        InFlight[requestId] = cts;
        try
        {
            if (!BrowserCapabilities.IsOperation(capability))
            {
                WriteLine(stdout, Failure(requestId, ErrorClasses.CapabilityMissing, $"unknown browser operation '{capability}'", retryable: false));
                return;
            }

            switch (mode)
            {
                case "error":
                    WriteLine(stdout, Failure(
                        requestId,
                        payload["error_class"]?.GetValue<string>() ?? ErrorClasses.UiTargetNotFound,
                        payload["message"]?.GetValue<string>() ?? "fake error",
                        payload["retryable"]?.GetValue<bool>() ?? false));
                    return;

                case "sleep":
                    var sleepMs = payload["sleep_ms"]?.GetValue<int>() ?? 1000;
                    try
                    {
                        await Task.Delay(sleepMs, cts.Token);
                    }
                    catch (OperationCanceledException)
                    {
                        WriteLine(stdout, Failure(requestId, ErrorClasses.Cancelled, "cancelled by companion", retryable: false));
                        return;
                    }

                    break;

                case "crash":
                    await Console.Error.WriteLineAsync("fake-worker: crashing on request");
                    await Console.Error.FlushAsync();
                    Environment.Exit(3);
                    return;

                case "oversize":
                    var bytes = payload["bytes"]?.GetValue<int>() ?? (BrowserCapabilities.MaxResultBytes + 4096);
                    WriteLine(stdout, Success(requestId, new JsonObject { ["blob"] = new string('x', bytes) }));
                    return;

                case "forbidden":
                    var key = payload["key"]?.GetValue<string>() ?? "Set-Cookie";
                    WriteLine(stdout, Success(requestId, new JsonObject
                    {
                        ["page"] = new JsonObject
                        {
                            ["title"] = "ok",
                            ["headers"] = new JsonArray(new JsonObject { [key] = "session=abc" }),
                        },
                    }));
                    return;

                case "unknown_class":
                    WriteLine(stdout, Failure(requestId, "made_up_class", "not in the taxonomy", retryable: false));
                    return;

                default:
                    break;
            }

            WriteLine(stdout, Success(requestId, new JsonObject
            {
                ["capability"] = capability,
                ["echo"] = payload.DeepClone(),
                ["timeout_ms_seen"] = timeoutMs,
            }));
        }
        finally
        {
            InFlight.TryRemove(requestId, out _);
        }
    }

    private static JsonObject Success(string requestId, JsonObject result) => new()
    {
        ["type"] = "result",
        ["request_id"] = requestId,
        ["ok"] = true,
        ["result"] = result,
    };

    private static JsonObject Failure(string requestId, string errorClass, string message, bool retryable) => new()
    {
        ["type"] = "result",
        ["request_id"] = requestId,
        ["ok"] = false,
        ["error"] = new JsonObject
        {
            ["class"] = errorClass,
            ["message"] = message,
            ["retryable"] = retryable,
        },
    };

    private static void WriteLine(StreamWriter stdout, JsonObject message)
    {
        var line = message.ToJsonString();
        lock (StdoutLock)
        {
            stdout.WriteLine(line);
            stdout.Flush();
        }
    }
}
