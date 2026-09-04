using System.Diagnostics;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>
/// The companion's Browser Worker host, driven end to end over REAL stdin/stdout against
/// the fake worker process (BROWSER_CAPABILITIES.md §7). Every scenario here is one the
/// contract names: hello propagation, exec/result correlation, typed error pass-through,
/// timeout with cancel forwarded, crash mid-request with restart, the 48 KiB cap, the
/// forbidden-key refusal, the not-configured answer, and the shape of the audit row.
/// </summary>
public sealed class BrowserWorkerHostTests : IDisposable
{
    private readonly string _dir = TestPaths.NewTempDir();
    private readonly ListLogger _log = new();

    public void Dispose()
    {
        try
        {
            Directory.Delete(_dir, recursive: true);
        }
        catch (Exception)
        {
            // Best-effort cleanup.
        }
    }

    private BrowserWorkerHost NewHost(AuditLog? audit = null, string extraArgs = "", bool eager = false, TimeSpan? helloTimeout = null, TimeSpan? pingInterval = null, int? eagerRestartCeiling = null)
        => FakeWorkerLauncher.NewHost(_dir, _log, audit, extraArgs, eager, helloTimeout, pingInterval, eagerRestartCeiling);

    private static JsonObject Payload(string mode, params (string Key, JsonNode? Value)[] extra)
        => Payload(mode, "t1", extra);

    /// <summary>The fake worker serialises per session (§7), so a test that wants two requests to overlap gives them different sessions.</summary>
    private static JsonObject Payload(string mode, string sessionId, params (string Key, JsonNode? Value)[] extra)
    {
        var payload = new JsonObject { ["mode"] = mode, ["session_id"] = sessionId };
        foreach (var (key, value) in extra)
        {
            payload[key] = value;
        }

        return payload;
    }

    private static Task<JsonObject> Exec(BrowserWorkerHost host, string capability, JsonObject payload, double timeoutS = 10)
        => host.ExecuteAsync(capability, payload, TimeSpan.FromSeconds(timeoutS), CancellationToken.None);

    // ------------------------------------------------------------------- hello

    [Fact]
    public async Task Worker_hello_capabilities_and_browser_availability_are_remembered()
    {
        await using var host = NewHost();
        Assert.Null(host.Hello);
        Assert.False(host.WorkerRunning);

        await host.StartAsync(CancellationToken.None);

        Assert.True(host.WorkerRunning);
        Assert.NotNull(host.Hello);
        Assert.Equal("fake-1.0", host.Hello!.WorkerVersion);
        Assert.Equal(1, host.Hello.ProtocolVersion);
        Assert.True(host.Hello.BrowserAvailable);
        Assert.Equal("chrome", host.Hello.BrowserChannel);
        Assert.Equal(BrowserCapabilities.Operations, host.WorkerCapabilities);
        Assert.Equal(1, host.Starts);
    }

    [Fact]
    public void The_contracts_cli_arguments_are_derived_from_configuration()
    {
        var options = FakeWorkerLauncher.Options(_dir, extraArgs: "--x \"a b\"");
        var args = options.BuildArgumentList();

        // The configured leading arguments first (quotes honoured), then the derived ones.
        Assert.Contains("--x", args);
        Assert.Contains("a b", args);
        var dataIndex = args.ToList().IndexOf("--data-dir");
        Assert.True(dataIndex >= 0);
        Assert.Equal(_dir, args[dataIndex + 1]);
        var profileIndex = args.ToList().IndexOf("--profile-dir");
        Assert.Equal(Path.Combine(_dir, "profile"), args[profileIndex + 1]);
        var channelIndex = args.ToList().IndexOf("--channel");
        Assert.Equal("chrome", args[channelIndex + 1]);
        Assert.Contains("--headless", args);
        Assert.DoesNotContain("--visible", args);
        var idleIndex = args.ToList().IndexOf("--idle-timeout-s");
        Assert.Equal("600", args[idleIndex + 1]);
    }

    [Fact]
    public async Task A_worker_that_never_says_hello_is_killed_and_reported_dependency_unavailable_within_the_bound()
    {
        await using var host = NewHost(extraArgs: "--no-hello", helloTimeout: TimeSpan.FromSeconds(1));
        var stopwatch = Stopwatch.StartNew();

        var ex = await Assert.ThrowsAsync<CapabilityException>(() => Exec(host, BrowserCapabilities.Inspect, Payload("echo")));

        Assert.Equal(ErrorClasses.DependencyUnavailable, ex.ErrorClass);
        Assert.True(ex.Retryable);
        Assert.True(stopwatch.Elapsed < TimeSpan.FromSeconds(10), $"took {stopwatch.Elapsed}");
        Assert.False(host.WorkerRunning);
    }

    // ------------------------------------------------------------------- exec/result

    [Fact]
    public async Task Exec_result_round_trips_with_request_id_correlation_and_timeout_ms()
    {
        await using var host = NewHost();

        var result = await Exec(host, BrowserCapabilities.Navigate, Payload("echo", ("url", "https://example.org/")), timeoutS: 7);

        Assert.Equal(BrowserCapabilities.Navigate, result["capability"]!.GetValue<string>());
        Assert.Equal("https://example.org/", result["echo"]!["url"]!.GetValue<string>());
        // The worker sees what is LEFT of the request's budget: the lazy start comes off the
        // top, so a slow start is answered as a typed timeout instead of leaving the service
        // to give up first (2026-09-05). A little under 7000, never above it.
        var seen = result["timeout_ms_seen"]!.GetValue<int>();
        Assert.InRange(seen, 7000 - 5000, 7000);
        // Lazy start: the first request started the worker.
        Assert.Equal(1, host.Starts);
        Assert.True(host.WorkerRunning);
    }

    [Fact]
    public async Task Concurrent_requests_on_different_sessions_interleave_and_each_gets_its_own_answer()
    {
        await using var host = NewHost();
        await host.StartAsync(CancellationToken.None);

        var slow = Exec(host, BrowserCapabilities.Extract, Payload("sleep", "s1", ("sleep_ms", 700), ("tag", "slow")));
        var fast = Exec(host, BrowserCapabilities.Inspect, Payload("echo", "s2", ("tag", "fast")));

        var fastResult = await fast;
        Assert.False(slow.IsCompleted, "the fast request must not queue behind the slow one");
        var slowResult = await slow;

        Assert.Equal("fast", fastResult["echo"]!["tag"]!.GetValue<string>());
        Assert.Equal("slow", slowResult["echo"]!["tag"]!.GetValue<string>());
        Assert.Equal(1, host.Starts);
    }

    [Fact]
    public async Task Requests_on_the_same_session_complete_in_order_while_another_session_interleaves()
    {
        // §7, last clause: "executed concurrently by the worker per session but serially
        // within a session". The host does not enforce this — the worker does — but the
        // suite must exercise it so the host's concurrency model is proven against a
        // worker that behaves like the real one.
        await using var host = NewHost();
        await host.StartAsync(CancellationToken.None);

        // A long sleep and an elapsed-time check instead of a tight 700 ms window: on a
        // loaded CI runner the interleaving 'other' answer can itself take hundreds of
        // milliseconds, which once made the first request look "already complete".
        const int firstSleepMs = 4000;
        var clock = System.Diagnostics.Stopwatch.StartNew();
        var first = Exec(host, BrowserCapabilities.Navigate, Payload("sleep", "same", ("sleep_ms", firstSleepMs), ("tag", "first")));
        await Task.Delay(50);
        var second = Exec(host, BrowserCapabilities.Extract, Payload("echo", "same", ("tag", "second")));
        var other = Exec(host, BrowserCapabilities.Inspect, Payload("echo", "other", ("tag", "other")));

        var otherResult = await other;
        var otherAnsweredAtMs = clock.ElapsedMilliseconds;
        Assert.Equal("other", otherResult["echo"]!["tag"]!.GetValue<string>());
        Assert.True(otherAnsweredAtMs < firstSleepMs, $"the other session must not wait for 'same' (answered after {otherAnsweredAtMs} ms)");
        Assert.False(first.IsCompleted, "the other session must not wait for 'same'");
        Assert.False(second.IsCompleted, "the second request on 'same' must queue behind the first");

        var secondResult = await second;
        var secondAnsweredAtMs = clock.ElapsedMilliseconds;
        // Ordering is proven by time, not by the outer tasks' completion flags: the worker
        // queues 'second' behind the 4 s sleep, so it cannot be answered earlier, while the
        // host's async continuations may surface the two results in either order.
        Assert.True(secondAnsweredAtMs >= firstSleepMs - 100, $"the second request on a session completes only after the first (answered after {secondAnsweredAtMs} ms)");
        Assert.Equal("second", secondResult["echo"]!["tag"]!.GetValue<string>());
        Assert.Equal("first", (await first.WaitAsync(TimeSpan.FromSeconds(5)))["echo"]!["tag"]!.GetValue<string>());
    }

    [Fact]
    public async Task A_typed_worker_error_passes_through_with_its_class_message_and_retryability()
    {
        await using var host = NewHost();

        var ex = await Assert.ThrowsAsync<CapabilityException>(() => Exec(
            host,
            BrowserCapabilities.Click,
            Payload("error", ("error_class", ErrorClasses.UiTargetNotFound), ("message", "no button named Gönder"), ("retryable", false))));

        Assert.Equal(ErrorClasses.UiTargetNotFound, ex.ErrorClass);
        Assert.Equal("no button named Gönder", ex.Message);
        Assert.False(ex.Retryable);

        var retryable = await Assert.ThrowsAsync<CapabilityException>(() => Exec(
            host,
            BrowserCapabilities.Search,
            Payload("error", ("error_class", ErrorClasses.ProviderRateLimited), ("message", "all engines captcha"), ("retryable", true))));
        Assert.Equal(ErrorClasses.ProviderRateLimited, retryable.ErrorClass);
        Assert.True(retryable.Retryable);
    }

    [Fact]
    public async Task An_error_class_outside_the_taxonomy_is_reported_as_internal_bug()
    {
        await using var host = NewHost();

        var ex = await Assert.ThrowsAsync<CapabilityException>(() => Exec(host, BrowserCapabilities.Find, Payload("unknown_class")));

        Assert.Equal(ErrorClasses.InternalBug, ex.ErrorClass);
        Assert.Contains("made_up_class", ex.Message, StringComparison.Ordinal);
    }

    [Theory]
    [InlineData("browser.teleport")]
    [InlineData("browser.anything")]
    [InlineData("browser.chrome")]
    [InlineData("browser.")]
    public async Task An_unknown_browser_operation_is_refused_by_the_host_before_a_lazy_worker_is_even_started(string capability)
    {
        await using var host = NewHost();

        var ex = await Assert.ThrowsAsync<CapabilityException>(() => Exec(host, capability, Payload("echo")));

        Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Equal(0, host.Starts);
        Assert.False(host.WorkerRunning);
    }

    [Fact]
    public async Task An_unknown_browser_operation_never_reaches_a_running_worker()
    {
        await using var host = NewHost();
        await host.StartAsync(CancellationToken.None);

        var ex = await Assert.ThrowsAsync<CapabilityException>(() => Exec(host, "browser.anything", Payload("echo")));
        Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);

        // The fake acknowledges every exec it receives on stderr, in order. A later, valid
        // request proves the log is caught up — and the refused name is not in it.
        await Exec(host, BrowserCapabilities.Inspect, Payload("echo"));
        await WaitUntilAsync(() => _log.Any("fake-worker: exec capability=browser.inspect"), TimeSpan.FromSeconds(5));
        Assert.False(_log.Any("exec capability=browser.anything"), "the unknown operation was written to the worker's stdin");
        Assert.Equal(1, host.Starts);
    }

    // ------------------------------------------------------------------- timeout / cancel

    [Fact]
    public async Task Timeout_answers_timeout_retryable_and_forwards_a_cancel_to_the_worker()
    {
        await using var host = NewHost();
        await host.StartAsync(CancellationToken.None);
        var stopwatch = Stopwatch.StartNew();

        var ex = await Assert.ThrowsAsync<CapabilityException>(() => Exec(
            host, BrowserCapabilities.Wait, Payload("sleep", ("sleep_ms", 30_000)), timeoutS: 1));

        Assert.Equal(ErrorClasses.Timeout, ex.ErrorClass);
        Assert.True(ex.Retryable);
        Assert.True(stopwatch.Elapsed < TimeSpan.FromSeconds(8), $"took {stopwatch.Elapsed}");
        // The cancel is sent without blocking the timeout answer (so the typed error always
        // beats the caller's own deadline); it lands shortly after, so wait for it.
        var cancelDeadline = DateTime.UtcNow.AddSeconds(5);
        while (host.CancelsSent == 0 && DateTime.UtcNow < cancelDeadline)
        {
            await Task.Delay(25);
        }
        Assert.Equal(1, host.CancelsSent);

        // The worker acknowledged the cancel on its stderr, which the host forwards to the
        // companion log — and the worker is still the same process, ready for more.
        await WaitUntilAsync(() => _log.Any("fake-worker: cancel request_id="), TimeSpan.FromSeconds(5));
        Assert.True(host.WorkerRunning);
        var next = await Exec(host, BrowserCapabilities.Inspect, Payload("echo"));
        Assert.Equal(BrowserCapabilities.Inspect, next["capability"]!.GetValue<string>());
        Assert.Equal(1, host.Starts);
    }

    [Fact]
    public async Task Companion_cancellation_forwards_a_cancel_and_answers_cancelled()
    {
        await using var host = NewHost();
        await host.StartAsync(CancellationToken.None);
        using var cts = new CancellationTokenSource();

        var running = host.ExecuteAsync(BrowserCapabilities.Wait, Payload("sleep", ("sleep_ms", 30_000)), TimeSpan.FromSeconds(30), cts.Token);
        await Task.Delay(200);
        cts.Cancel();

        var ex = await Assert.ThrowsAsync<CapabilityException>(() => running);
        Assert.Equal(ErrorClasses.Cancelled, ex.ErrorClass);
        Assert.Equal(1, host.CancelsSent);
    }

    // ------------------------------------------------------------------- crash / restart

    [Fact]
    public async Task A_crash_mid_request_fails_it_dependency_unavailable_and_the_next_request_gets_a_restarted_worker()
    {
        await using var host = NewHost();
        await host.StartAsync(CancellationToken.None);
        Assert.NotNull(host.WorkerPid);

        var ex = await Assert.ThrowsAsync<CapabilityException>(() => Exec(host, BrowserCapabilities.Navigate, Payload("crash")));

        Assert.Equal(ErrorClasses.DependencyUnavailable, ex.ErrorClass);
        Assert.True(ex.Retryable);

        // Automatic restart, through the backoff, on the next request.
        var result = await Exec(host, BrowserCapabilities.Inspect, Payload("echo"));
        Assert.Equal(BrowserCapabilities.Inspect, result["capability"]!.GetValue<string>());
        Assert.Equal(2, host.Starts);
        Assert.Equal(1, host.Restarts);
        // Windows reuses pids quickly, so a second start — not pid inequality — is the proof of a replacement.
        Assert.Equal(2, host.Starts);
        Assert.True(host.WorkerRunning);
    }

    [Fact]
    public async Task An_in_flight_request_fails_immediately_when_the_worker_dies_rather_than_waiting_out_its_timeout()
    {
        await using var host = NewHost();
        await host.StartAsync(CancellationToken.None);

        var slow = Exec(host, BrowserCapabilities.Extract, Payload("sleep", "s1", ("sleep_ms", 60_000)), timeoutS: 60);
        await Task.Delay(200);
        var stopwatch = Stopwatch.StartNew();
        var crash = Assert.ThrowsAsync<CapabilityException>(() => Exec(host, BrowserCapabilities.Navigate, Payload("crash", "s2")));

        var ex = await Assert.ThrowsAsync<CapabilityException>(() => slow);
        await crash;
        Assert.Equal(ErrorClasses.DependencyUnavailable, ex.ErrorClass);
        Assert.True(ex.Retryable);
        Assert.True(stopwatch.Elapsed < TimeSpan.FromSeconds(10), $"took {stopwatch.Elapsed}");
    }

    [Fact]
    public async Task An_eager_worker_is_restarted_in_the_background_after_a_crash()
    {
        await using var host = NewHost(eager: true);
        await host.StartAsync(CancellationToken.None);

        await Assert.ThrowsAsync<CapabilityException>(() => Exec(host, BrowserCapabilities.Navigate, Payload("crash")));

        await WaitUntilAsync(() => host.Starts >= 2 && host.WorkerRunning, TimeSpan.FromSeconds(10));
        Assert.True(host.WorkerRunning);
    }

    [Fact]
    public async Task A_worker_that_stops_answering_pings_is_killed_and_replaced()
    {
        await using var host = NewHost(extraArgs: "--no-pong", pingInterval: TimeSpan.FromMilliseconds(100));
        await host.StartAsync(CancellationToken.None);
        Assert.NotNull(host.WorkerPid);

        await WaitUntilAsync(() => host.LivenessKills >= 1, TimeSpan.FromSeconds(10));
        Assert.True(host.PingsSent >= 3);
        Assert.Equal(0, host.PongsReceived);

        var result = await Exec(host, BrowserCapabilities.Inspect, Payload("echo"));
        Assert.Equal(BrowserCapabilities.Inspect, result["capability"]!.GetValue<string>());
        // Windows reuses pids quickly, so a second start — not pid inequality — is the proof of a replacement.
        Assert.Equal(2, host.Starts);
        Assert.True(host.WorkerRunning);
    }

    [Fact]
    public async Task A_stdout_line_over_the_ceiling_kills_the_worker_fails_the_request_dependency_unavailable_and_the_next_request_gets_a_fresh_one()
    {
        await using var host = NewHost();
        await host.StartAsync(CancellationToken.None);
        Assert.NotNull(host.WorkerPid);
        var stopwatch = Stopwatch.StartNew();

        var ex = await Assert.ThrowsAsync<CapabilityException>(() => Exec(
            host, BrowserCapabilities.Extract, Payload("longline", ("bytes", 1024 * 1024)), timeoutS: 30));

        Assert.Equal(ErrorClasses.DependencyUnavailable, ex.ErrorClass);
        Assert.True(ex.Retryable);
        Assert.Contains("stdout line over", ex.Message, StringComparison.Ordinal);
        Assert.True(stopwatch.Elapsed < TimeSpan.FromSeconds(20), $"took {stopwatch.Elapsed}");
        Assert.Equal(1, host.OversizeLineKills);
        Assert.Equal(4 * BrowserCapabilities.MaxResultBytes, BrowserWorkerHost.MaxStdoutLineBytes);
        Assert.True(_log.Any("wrote a stdout line over"));

        // Restart through the backoff; the next request succeeds on a new process.
        var result = await Exec(host, BrowserCapabilities.Inspect, Payload("echo"));
        Assert.Equal(BrowserCapabilities.Inspect, result["capability"]!.GetValue<string>());
        Assert.Equal(2, host.Starts);
        // Windows reuses pids quickly, so a second start — not pid inequality — is the proof of a replacement.
        Assert.Equal(2, host.Starts);
        Assert.True(host.WorkerRunning);
    }

    [Fact]
    public async Task An_eager_host_stops_background_restarts_after_the_ceiling_logs_once_and_still_starts_on_the_next_request()
    {
        const int Ceiling = 3;
        await using var host = NewHost(extraArgs: "--no-hello", eager: true, helloTimeout: TimeSpan.FromMilliseconds(150), eagerRestartCeiling: Ceiling);
        Assert.Equal(Ceiling, host.EagerRestartCeiling);
        Assert.Equal(10, BrowserWorkerHost.DefaultEagerRestartCeiling);

        await host.StartAsync(CancellationToken.None);

        await WaitUntilAsync(() => host.EagerRestartSuspended, TimeSpan.FromSeconds(20));
        // Settle: no further background start may follow the suspension.
        await Task.Delay(600);
        Assert.Equal(Ceiling, host.Starts);
        Assert.Equal(Ceiling, host.ConsecutiveFailures);
        Assert.False(host.WorkerRunning);
        Assert.Equal(1, _log.Lines.Count(line => line.Contains("eager background restarts are suspended", StringComparison.Ordinal)));

        // An explicit request still tries (and, with this worker, still fails typed) —
        // and the suspension does not log a second time.
        var ex = await Assert.ThrowsAsync<CapabilityException>(() => Exec(host, BrowserCapabilities.Inspect, Payload("echo")));
        Assert.Equal(ErrorClasses.DependencyUnavailable, ex.ErrorClass);
        Assert.Equal(Ceiling + 1, host.Starts);
        await Task.Delay(400);
        Assert.Equal(Ceiling + 1, host.Starts);
        Assert.Equal(1, _log.Lines.Count(line => line.Contains("eager background restarts are suspended", StringComparison.Ordinal)));
    }

    [Fact]
    public async Task A_healthy_worker_answers_pings()
    {
        await using var host = NewHost(pingInterval: TimeSpan.FromMilliseconds(100));
        await host.StartAsync(CancellationToken.None);

        await WaitUntilAsync(() => host.PongsReceived >= 2, TimeSpan.FromSeconds(10));
        Assert.Equal(0, host.LivenessKills);
        Assert.True(host.WorkerRunning);
    }

    // ------------------------------------------------------------------- result checks

    [Fact]
    public async Task An_oversize_result_is_answered_internal_bug_because_the_worker_must_truncate()
    {
        await using var host = NewHost();

        var ex = await Assert.ThrowsAsync<CapabilityException>(() => Exec(
            host, BrowserCapabilities.Extract, Payload("oversize", ("bytes", BrowserCapabilities.MaxResultBytes + 1024))));

        Assert.Equal(ErrorClasses.InternalBug, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Contains(BrowserCapabilities.MaxResultBytes.ToString(), ex.Message, StringComparison.Ordinal);

        // Under the cap is fine.
        var ok = await Exec(host, BrowserCapabilities.Extract, Payload("oversize", ("bytes", 40_000)));
        Assert.Equal(40_000, ok["blob"]!.GetValue<string>().Length);
    }

    [Theory]
    [InlineData("Set-Cookie")]
    [InlineData("cookie")]
    [InlineData("Authorization")]
    [InlineData("localStorage")]
    [InlineData("SessionStorage")]
    [InlineData("user_password")]
    [InlineData("csrfToken")]
    [InlineData("client_secret")]
    [InlineData("ApiKey")]
    // The separator/case spellings a raw substring match let through (worker parity).
    [InlineData("api-key")]
    [InlineData("api_key")]
    [InlineData("x-api-key")]
    [InlineData("API-KEY")]
    [InlineData("Set_Cookie")]
    [InlineData("SET COOKIE")]
    [InlineData("local-storage")]
    [InlineData("session.storage")]
    [InlineData("tokens_count")]
    public async Task A_result_carrying_a_forbidden_key_at_any_depth_is_refused_with_security_scope_error(string key)
    {
        await using var host = NewHost();

        var ex = await Assert.ThrowsAsync<CapabilityException>(() => Exec(
            host, BrowserCapabilities.Extract, Payload("forbidden", ("key", key))));

        Assert.Equal(ErrorClasses.SecurityScopeError, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Contains(key, ex.Message, StringComparison.Ordinal);
    }

    [Theory]
    [InlineData("api-key")]
    [InlineData("Set_Cookie")]
    public async Task A_forbidden_key_inside_nested_arrays_of_objects_is_still_refused(string key)
    {
        await using var host = NewHost();

        var ex = await Assert.ThrowsAsync<CapabilityException>(() => Exec(
            host, BrowserCapabilities.Extract, Payload("forbidden_nested", ("key", key))));

        Assert.Equal(ErrorClasses.SecurityScopeError, ex.ErrorClass);
        Assert.Contains($"result.links[1].attrs[0][0].{key}", ex.Message, StringComparison.Ordinal);
    }

    [Theory]
    [InlineData("text_chars")]
    [InlineData("links_count")]
    [InlineData("injection_markers")]
    [InlineData("final_url")]
    [InlineData("http_status")]
    [InlineData("session_id")]
    [InlineData("truncated")]
    public async Task The_contracts_own_result_vocabulary_passes_the_forbidden_key_scan(string key)
    {
        await using var host = NewHost();

        var result = await Exec(host, BrowserCapabilities.Extract, Payload("forbidden", ("key", key)));

        Assert.Equal("ok", result["page"]!["title"]!.GetValue<string>());
    }

    [Theory]
    [InlineData("api-key", true)]
    [InlineData("api_key", true)]
    [InlineData("x-api-key", true)]
    [InlineData("API-KEY", true)]
    [InlineData("apiKey", true)]
    [InlineData("Set_Cookie", true)]
    [InlineData("set-cookie", true)]
    [InlineData("Set-Cookie", true)]
    [InlineData("x-AUTHORIZATION-header", true)]
    [InlineData("access_token", true)]
    // "token" is a SUBSTRING rule by contract (§6b): tokens_count is forbidden, and the
    // worker's result vocabulary avoids it (text_chars, links_count). Documented here so a
    // future "fix" does not silently narrow the rule on one side only.
    [InlineData("tokens_count", true)]
    [InlineData("text_chars", false)]
    [InlineData("links_count", false)]
    [InlineData("session_id", false)]
    [InlineData("secretary", true)]
    [InlineData("authorised", false)]
    [InlineData("", false)]
    public void The_forbidden_key_rule_normalises_like_the_worker_before_matching(string key, bool forbidden)
    {
        Assert.Equal(forbidden, BrowserCapabilities.IsForbiddenKey(key));
    }

    [Fact]
    public void The_forbidden_fragments_are_stored_normalised_so_the_list_and_the_rule_agree()
    {
        Assert.All(BrowserCapabilities.ForbiddenResultKeyFragments, fragment => Assert.Equal(fragment, BrowserCapabilities.NormalizeKey(fragment)));
        Assert.Contains("setcookie", BrowserCapabilities.ForbiddenResultKeyFragments);
        Assert.DoesNotContain("set-cookie", BrowserCapabilities.ForbiddenResultKeyFragments);
        Assert.Equal("xapikey", BrowserCapabilities.NormalizeKey("X-Api_Key"));
        Assert.Equal("apikey", BrowserCapabilities.NormalizeKey("Api_Key"));
        Assert.Equal("setcookie", BrowserCapabilities.NormalizeKey("Set-Cookie"));
        Assert.Equal("", BrowserCapabilities.NormalizeKey("-_.:"));
    }

    [Fact]
    public void The_forbidden_key_scan_is_recursive_and_case_insensitive_and_ignores_values()
    {
        var clean = new JsonObject
        {
            ["url"] = "https://example.org/?token=in-value-is-fine",
            ["links"] = new JsonArray(new JsonObject { ["href"] = "x", ["text"] = "cookie policy" }),
        };
        Assert.Null(BrowserWorkerHost.FindForbiddenKey(clean));

        var dirty = new JsonObject
        {
            ["page"] = new JsonObject
            {
                ["items"] = new JsonArray(new JsonObject { ["x-AUTHORIZATION-header"] = "Bearer" }),
            },
        };
        Assert.Equal("$.page.items[0].x-AUTHORIZATION-header", BrowserWorkerHost.FindForbiddenKey(dirty));

        var nestedArrays = new JsonObject
        {
            ["rows"] = new JsonArray(new JsonArray(new JsonObject { ["fine"] = 1 }, new JsonObject { ["api-key"] = "k" })),
        };
        Assert.Equal("$.rows[0][1].api-key", BrowserWorkerHost.FindForbiddenKey(nestedArrays));
    }

    // ------------------------------------------------------------------- not configured

    [Fact]
    public async Task An_unconfigured_host_answers_capability_missing_without_starting_anything()
    {
        var options = new BrowserWorkerOptions { WorkerCommand = null, DataDir = _dir, ProfileDir = Path.Combine(_dir, "profile") };
        await using var host = new BrowserWorkerHost(options, _log);

        Assert.False(host.IsConfigured);
        var ex = await Assert.ThrowsAsync<CapabilityException>(() => Exec(host, BrowserCapabilities.Inspect, Payload("echo")));

        Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Equal(0, host.Starts);
    }

    // ------------------------------------------------------------------- stderr, shutdown, audit

    [Fact]
    public async Task Worker_stderr_goes_to_the_companion_log_and_never_into_a_result()
    {
        await using var host = NewHost();

        var result = await Exec(host, BrowserCapabilities.Inspect, Payload("echo"));

        await WaitUntilAsync(() => _log.Any("browser worker stderr: fake-worker: started"), TimeSpan.FromSeconds(5));
        Assert.DoesNotContain("fake-worker", result.ToJsonString(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task Worker_stderr_is_sanitised_before_it_reaches_the_companion_log()
    {
        await using var host = NewHost();
        var flood = new string('f', 5000);

        await Exec(host, BrowserCapabilities.Inspect, Payload("stderr", ("lines", new JsonArray(
            "plain line ok LINE-A",
            "fetching https://example.org/search?q=QUERY-MARKER&token=TOKEN-MARKER#frag then more",
            "authorization: Bearer AUTH-MARKER",
            "api_key=KEY-MARKER",
            "x-api-key = KEY2-MARKER",
            "Set-Cookie: SESSION-MARKER",
            "request_id=abc session=t1 tokens_used=3 LINE-B",
            flood))));

        // The flood is the last line the fake writes; stderr is read in order, so once it
        // has been logged every earlier line has been through the sanitiser.
        await WaitUntilAsync(() => _log.Lines.Any(line => line.Contains("fffff", StringComparison.Ordinal)), TimeSpan.FromSeconds(5));

        // 1. Plain lines pass; 2. the query string and fragment are gone, scheme://host/path stays.
        Assert.True(_log.Any("browser worker stderr: plain line ok LINE-A"));
        Assert.True(_log.Any("browser worker stderr: fetching https://example.org/search then more"));
        Assert.False(_log.Any("QUERY-MARKER"));
        Assert.False(_log.Any("TOKEN-MARKER"));
        Assert.False(_log.Any("#frag"));

        // 3. "credential-like key" + ':' or '=' → the whole line becomes the marker.
        Assert.False(_log.Any("AUTH-MARKER"));
        Assert.False(_log.Any("KEY-MARKER"));
        Assert.False(_log.Any("KEY2-MARKER"));
        Assert.False(_log.Any("SESSION-MARKER"));
        // Four credential lines plus LINE-B: "tokens_used=" is caught by the same substring
        // rule — documented, not accidental — so that line must not survive verbatim either.
        Assert.Equal(5, _log.Lines.Count(line => line.Contains(WorkerLogSanitizer.RedactedMarker, StringComparison.Ordinal)));
        Assert.False(_log.Any("LINE-B"), "a 'token…=' line was logged verbatim");

        // 4. Capped: no log line carries the whole 5000-char flood.
        var floodLines = _log.Lines.Where(line => line.Contains("fffff", StringComparison.Ordinal)).ToList();
        Assert.Single(floodLines);
        Assert.DoesNotContain(flood, floodLines[0], StringComparison.Ordinal);
        Assert.Contains(new string('f', WorkerLogSanitizer.MaxLineChars), floodLines[0], StringComparison.Ordinal);
        Assert.Contains("[+3000 chars cut]", floodLines[0], StringComparison.Ordinal);
    }

    [Fact]
    public async Task Stop_sends_shutdown_and_the_worker_exits_cleanly()
    {
        var host = NewHost();
        await host.StartAsync(CancellationToken.None);
        var pid = host.WorkerPid!.Value;

        await host.StopAsync();

        Assert.False(host.WorkerRunning);
        await WaitUntilAsync(() => _log.Any("fake-worker: shutdown"), TimeSpan.FromSeconds(5));
        Assert.Throws<ArgumentException>(() => Process.GetProcessById(pid));

        var after = await Assert.ThrowsAsync<CapabilityException>(() => Exec(host, BrowserCapabilities.Inspect, Payload("echo")));
        Assert.Equal(ErrorClasses.DependencyUnavailable, after.ErrorClass);
        await host.DisposeAsync();
    }

    [Fact]
    public async Task Each_request_writes_one_audit_row_with_capability_outcome_and_duration_but_no_payload_or_result_text()
    {
        var auditPath = Path.Combine(_dir, "audit", "companion-audit.jsonl");
        var audit = new AuditLog(auditPath);
        await using var host = NewHost(audit);

        const string PayloadMarker = "PAYLOAD-MARKER-9f3c";
        await Exec(host, BrowserCapabilities.Navigate, Payload("echo", ("url", $"https://example.org/?q={PayloadMarker}")));
        await Assert.ThrowsAsync<CapabilityException>(() => Exec(
            host, BrowserCapabilities.Click, Payload("error", ("error_class", ErrorClasses.UiTargetNotFound), ("message", "RESULT-MARKER-1b2a"))));

        var rows = File.ReadAllLines(auditPath)
            .Select(line => JsonNode.Parse(line)!.AsObject())
            .Where(row => row["event"]!.GetValue<string>() == "browser_request")
            .ToList();

        Assert.Equal(2, rows.Count);

        var ok = rows[0];
        Assert.Equal(BrowserCapabilities.Navigate, ok["capability"]!.GetValue<string>());
        Assert.Equal("ok", ok["status"]!.GetValue<string>());
        Assert.Matches(@"request_id=[0-9a-f]{32}; duration_ms=\d+", ok["detail"]!.GetValue<string>());

        var failed = rows[1];
        Assert.Equal(BrowserCapabilities.Click, failed["capability"]!.GetValue<string>());
        Assert.Equal(ErrorClasses.UiTargetNotFound, failed["status"]!.GetValue<string>());
        Assert.Matches(@"request_id=[0-9a-f]{32}; duration_ms=\d+; retryable=false", failed["detail"]!.GetValue<string>());

        var content = File.ReadAllText(auditPath);
        Assert.DoesNotContain(PayloadMarker, content, StringComparison.Ordinal);
        Assert.DoesNotContain("RESULT-MARKER", content, StringComparison.Ordinal);
        Assert.DoesNotContain("example.org", content, StringComparison.Ordinal);
        Assert.Contains("browser_worker_started", content, StringComparison.Ordinal);
    }

    private static async Task WaitUntilAsync(Func<bool> condition, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (!condition())
        {
            Assert.True(DateTime.UtcNow < deadline, "condition not met in time");
            await Task.Delay(25);
        }
    }
}
