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
/// cancel forwarding, crashes mid-request, oversize and forbidden results, pings, shutdown,
/// hostile stderr and a flooded stdout.
///
/// Like the real worker (§7, last clause) it executes requests concurrently across sessions
/// but SERIALLY within one <c>session_id</c>: a request queues behind the previous one on
/// the same session, and a request without a session runs at once.
///
/// Every exec is acknowledged on stderr (<c>fake-worker: exec capability=… request_id=…</c>)
/// before it is handled, so a test can prove from the companion log which requests reached
/// the worker — and which never did.
///
/// Behaviour is chosen by the request payload's <c>mode</c> field (default: echo) and by a
/// few process flags, so one binary covers every scenario:
/// <list type="bullet">
/// <item><c>--self-check</c>: print the hello and exit 0 (the real worker's install probe).</item>
/// <item><c>--no-hello</c>: never announce (hello-timeout test).</item>
/// <item><c>--no-pong</c>: ignore pings (liveness test).</item>
/// <item><c>--no-pong-once</c>: the FIRST worker started in this working directory ignores
/// pings and every later one answers them - "a hung browser is replaced by a healthy one",
/// which is what production looks like and what <c>--no-pong</c> could not express: the
/// replacement inherits the same argv, so it is unresponsive too and a test then has to win
/// a race against the next kill to prove anything about it.</item>
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
    private static readonly ConcurrentDictionary<string, Task> SessionTails = new(StringComparer.Ordinal);
    private static int OpenSessions;

    public static async Task<int> Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        Console.InputEncoding = new UTF8Encoding(false);
        var stdout = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true };
        var stdin = new StreamReader(Console.OpenStandardInput(), new UTF8Encoding(false));

        var selfCheck = args.Contains("--self-check");
        var noHello = args.Contains("--no-hello");
        // M18.4 gap 4: a candidate that dies before it can announce itself (exit 3 at once),
        // so the host's rejection path is exercised without waiting out a hello timeout.
        if (args.Contains("--crash-before-hello"))
        {
            await Console.Error.WriteLineAsync("fake-worker: crashing before hello");
            return 3;
        }
        var noPong = args.Contains("--no-pong");
        var helloDelay = 0;
        var workerVersion = "fake-1.0";
        var openSessions = 0;
        for (var i = 0; i < args.Length - 1; i++)
        {
            if (args[i] == "--hello-delay-ms" && int.TryParse(args[i + 1], out var parsed))
            {
                helloDelay = parsed;
            }

            // M18.4 gap 4 (the staged update): what this worker calls itself, and how many
            // browser sessions it claims to hold when asked for worker_status.
            if (args[i] == "--worker-version")
            {
                workerVersion = args[i + 1];
            }

            if (args[i] == "--open-sessions" && int.TryParse(args[i + 1], out var sessions))
            {
                openSessions = sessions;
            }
        }

        OpenSessions = openSessions;

        await Console.Error.WriteLineAsync($"fake-worker: started pid={Environment.ProcessId} argv={string.Join(' ', args)}");

        var hello = new JsonObject
        {
            ["type"] = "hello",
            ["worker_version"] = workerVersion,
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

        // Decided AFTER the self-check return so an install probe never spends the one
        // unresponsive slot. The host runs us with WorkingDirectory = DataDir, and each test
        // gets its own, so the marker scopes to a single host's worker generations.
        if (args.Contains("--no-pong-once") && ClaimTheUnresponsiveSlot())
        {
            noPong = true;
            await Console.Error.WriteLineAsync("fake-worker: first worker here; ignoring pings");
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
                    await EnqueueExecAsync(stdout, message);
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

    /// <summary>
    /// Registers the request as in flight (so a cancel that arrives while it is still queued
    /// is honoured), then runs it: at once when it carries no session, otherwise after the
    /// previous request on the same session has finished.
    /// </summary>
    private static async Task EnqueueExecAsync(StreamWriter stdout, JsonObject message)
    {
        var requestId = message["request_id"]?.GetValue<string>() ?? Guid.NewGuid().ToString("N");
        var capability = message["capability"]?.GetValue<string>() ?? "";
        var payload = message["payload"] as JsonObject ?? new JsonObject();
        var sessionId = payload["session_id"]?.GetValue<string>();

        await Console.Error.WriteLineAsync($"fake-worker: exec capability={capability} request_id={requestId} session={sessionId ?? "-"}");

        var cts = new CancellationTokenSource();
        InFlight[requestId] = cts;

        if (string.IsNullOrEmpty(sessionId))
        {
            _ = Task.Run(() => HandleExecAsync(stdout, message, requestId, capability, payload, cts));
            return;
        }

        var tail = SessionTails.AddOrUpdate(
            sessionId,
            _ => Task.Run(() => HandleExecAsync(stdout, message, requestId, capability, payload, cts)),
            (_, previous) => previous.ContinueWith(
                _ => HandleExecAsync(stdout, message, requestId, capability, payload, cts),
                TaskContinuationOptions.ExecuteSynchronously).Unwrap());
        _ = tail;
    }

    private static async Task HandleExecAsync(StreamWriter stdout, JsonObject message, string requestId, string capability, JsonObject payload, CancellationTokenSource cts)
    {
        var timeoutMs = message["timeout_ms"]?.GetValue<int>() ?? 0;
        var mode = payload["mode"]?.GetValue<string>() ?? "echo";

        try
        {
            if (!BrowserCapabilities.IsOperation(capability))
            {
                WriteLine(stdout, Failure(requestId, ErrorClasses.CapabilityMissing, $"unknown browser operation '{capability}'", retryable: false));
                return;
            }

            if (capability == BrowserCapabilities.WorkerStatus && mode == "echo")
            {
                WriteLine(stdout, Success(requestId, new JsonObject { ["worker_version"] = "fake", ["sessions"] = OpenSessions }));
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

                case "forbidden_nested":
                    // The key sits three levels down, inside an array of objects inside an
                    // array — where a shallow scan would not look.
                    var nestedKey = payload["key"]?.GetValue<string>() ?? "api-key";
                    WriteLine(stdout, Success(requestId, new JsonObject
                    {
                        ["links"] = new JsonArray(
                            new JsonObject { ["href"] = "https://example.org/", ["text"] = "fine" },
                            new JsonObject
                            {
                                ["href"] = "https://example.org/2",
                                ["attrs"] = new JsonArray(new JsonArray(new JsonObject { [nestedKey] = "leak" })),
                            }),
                    }));
                    return;

                case "unknown_class":
                    WriteLine(stdout, Failure(requestId, "made_up_class", "not in the taxonomy", retryable: false));
                    return;

                case "stderr":
                    // Say whatever the test wants on stderr (the worker's log channel),
                    // then answer normally.
                    if (payload["lines"] is JsonArray lines)
                    {
                        foreach (var node in lines)
                        {
                            await Console.Error.WriteLineAsync(node?.GetValue<string>() ?? string.Empty);
                        }

                        await Console.Error.FlushAsync();
                    }

                    break;

                case "longline":
                    // Flood the protocol channel: one stdout line of N bytes that is not a
                    // message, then never answer. The host must not buffer it.
                    var lineBytes = payload["bytes"]?.GetValue<int>() ?? (1024 * 1024);
                    lock (StdoutLock)
                    {
                        var block = new string('x', 64 * 1024);
                        var written = 0;
                        while (written < lineBytes)
                        {
                            var chunk = Math.Min(block.Length, lineBytes - written);
                            stdout.Write(block.AsSpan(0, chunk));
                            written += chunk;
                        }

                        stdout.WriteLine();
                        stdout.Flush();
                    }

                    await Task.Delay(Timeout.Infinite, cts.Token);
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
        catch (OperationCanceledException)
        {
            WriteLine(stdout, Failure(requestId, ErrorClasses.Cancelled, "cancelled by companion", retryable: false));
        }
        finally
        {
            InFlight.TryRemove(requestId, out _);
            cts.Dispose();
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

    /// <summary>
    /// True for the first <c>--no-pong-once</c> worker in this working directory and false
    /// for every later one. <c>CreateNew</c> rather than an exists-check: two workers can
    /// overlap during a restart, and the file system is the only thing both of them can see.
    /// </summary>
    private static bool ClaimTheUnresponsiveSlot()
    {
        try
        {
            using var _ = new FileStream("no-pong-once.claimed", FileMode.CreateNew, FileAccess.Write);
            return true;
        }
        catch (IOException)
        {
            return false;
        }
    }

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
