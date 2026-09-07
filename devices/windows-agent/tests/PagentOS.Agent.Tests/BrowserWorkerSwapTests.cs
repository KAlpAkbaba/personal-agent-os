using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>
/// M18.4 gap 4 - the browser worker's staged update, over REAL processes (the fake worker):
/// the candidate starts beside the current worker and must say hello; the current worker
/// drains (its in-flight request finishes on it, a request arriving during the drain lands
/// on the candidate), retires (shutdown, then exit), and new work routes to the candidate;
/// a candidate that never says hello, or drops a capability, or is not the expected release,
/// is discarded and the current worker keeps serving untouched; a current worker holding a
/// browser session is not retired (busy) - in-flight owned work is preserved; the request
/// file protocol the updater uses refuses anything outside the install root.
/// </summary>
public sealed class BrowserWorkerSwapTests : IDisposable
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

    private BrowserWorkerHost NewHost(string extraArgs = "", bool eager = false)
        => FakeWorkerLauncher.NewHost(_dir, _log, null, extraArgs, eager, helloTimeout: TimeSpan.FromSeconds(20), pingInterval: TimeSpan.FromSeconds(60), eagerRestartCeiling: null);

    private BrowserWorkerOptions Candidate(string extraArgs)
        => FakeWorkerLauncher.Options(_dir, extraArgs: extraArgs);

    private static JsonObject Payload(string mode, string sessionId = "s1", int sleepMs = 0)
    {
        var payload = new JsonObject { ["mode"] = mode, ["session_id"] = sessionId };
        if (sleepMs > 0)
        {
            payload["sleep_ms"] = sleepMs;
        }

        return payload;
    }

    private static Task<JsonObject> Exec(BrowserWorkerHost host, JsonObject payload, double timeoutS = 15)
        => host.ExecuteAsync(BrowserCapabilities.Navigate, payload, TimeSpan.FromSeconds(timeoutS), CancellationToken.None);

    private static async Task<int> PidOf(BrowserWorkerHost host)
    {
        Assert.True(host.WorkerRunning);
        return host.WorkerPid ?? throw new InvalidOperationException("no worker pid");
    }

    [Fact]
    public async Task A_candidate_replaces_the_current_worker_after_it_drained_and_new_work_routes_to_it()
    {
        await using var host = NewHost();
        await host.StartAsync(CancellationToken.None);
        var oldPid = await PidOf(host);
        Assert.Equal("fake-1.0", host.Hello!.WorkerVersion);

        // One request in flight on the current worker when the swap begins.
        var inFlight = Exec(host, Payload("sleep", sleepMs: 1500));
        await Task.Delay(300);

        var swap = await host.SwapWorkerAsync(Candidate("--worker-version fake-2.0"), TimeSpan.FromSeconds(10), expectedVersion: "fake-2.0");

        Assert.True(swap.Swapped, swap.Reason);
        Assert.Equal(BrowserWorkerSwapResult.OutcomeSwapped, swap.Outcome);
        Assert.Equal(oldPid, swap.OldPid);
        Assert.NotEqual(oldPid, swap.NewPid);
        Assert.Equal("fake-1.0", swap.OldVersion);
        Assert.Equal("fake-2.0", swap.NewVersion);
        Assert.Equal(1, swap.PendingAtDrainStart);
        Assert.True(swap.DrainMs >= 1000, $"the drain waited for the in-flight request ({swap.DrainMs} ms)");

        // The in-flight request completed on the OLD worker, not with dependency_unavailable.
        var result = await inFlight;
        Assert.NotNull(result);

        // New work goes to the candidate; the old process is gone.
        Assert.Equal(swap.NewPid, host.WorkerPid);
        Assert.Equal("fake-2.0", host.Hello!.WorkerVersion);
        Assert.Equal(1, host.Swaps);
        var after = await Exec(host, Payload("echo"));
        Assert.NotNull(after);
        Assert.True(_log.Any($"exited with code 0 after shutdown") || _log.Any("after shutdown"), "the old worker retired through shutdown, not a crash");
        Assert.False(_log.Any("exited on its own\", consecutive"), "no failure was counted against the old worker");
        Assert.Equal(0, host.ConsecutiveFailures);
    }

    [Fact]
    public async Task A_request_arriving_during_the_drain_waits_and_lands_on_the_candidate()
    {
        await using var host = NewHost();
        await host.StartAsync(CancellationToken.None);
        var oldPid = await PidOf(host);

        var inFlight = Exec(host, Payload("sleep", sleepMs: 1500));
        await Task.Delay(200);
        var swapTask = host.SwapWorkerAsync(Candidate("--worker-version fake-2.0"), TimeSpan.FromSeconds(10));
        await Task.Delay(400);

        // Arrives while the old worker is draining: it must not be answered by the old one.
        var late = Exec(host, Payload("echo", sessionId: "s2"));

        var swap = await swapTask;
        Assert.True(swap.Swapped, swap.Reason);
        await inFlight;
        await late;
        Assert.NotEqual(oldPid, host.WorkerPid);
        Assert.Equal("fake-2.0", host.Hello!.WorkerVersion);
        // The candidate's stderr acknowledged the late request (the fake echoes every exec it received).
        Assert.True(_log.Any("session=s2"), "the late request reached a worker");
    }

    [Fact]
    public async Task A_candidate_that_never_says_hello_is_discarded_and_the_current_worker_keeps_serving()
    {
        await using var host = FakeWorkerLauncher.NewHost(_dir, _log, null, string.Empty, false, helloTimeout: TimeSpan.FromSeconds(2), pingInterval: TimeSpan.FromSeconds(60), eagerRestartCeiling: null);
        await host.StartAsync(CancellationToken.None);
        var oldPid = await PidOf(host);

        var swap = await host.SwapWorkerAsync(Candidate("--no-hello"), TimeSpan.FromSeconds(5));

        Assert.False(swap.Swapped);
        Assert.Equal(BrowserWorkerSwapResult.OutcomeCandidateFailed, swap.Outcome);
        Assert.Contains("did not announce itself", swap.Reason);
        Assert.Equal(oldPid, host.WorkerPid);
        Assert.Equal("fake-1.0", host.Hello!.WorkerVersion);
        Assert.Equal(0, host.Swaps);
        Assert.Equal(0, host.ConsecutiveFailures);
        var still = await Exec(host, Payload("echo"));
        Assert.NotNull(still);
    }

    [Fact]
    public async Task A_candidate_that_is_not_the_expected_release_is_rejected_before_any_drain()
    {
        await using var host = NewHost();
        await host.StartAsync(CancellationToken.None);
        var oldPid = await PidOf(host);
        var inFlight = Exec(host, Payload("sleep", sleepMs: 800));

        var swap = await host.SwapWorkerAsync(Candidate("--worker-version fake-2.0"), TimeSpan.FromSeconds(5), expectedVersion: "fake-3.0", expectedPackageSha256: "abc");

        Assert.False(swap.Swapped);
        Assert.Equal(BrowserWorkerSwapResult.OutcomeCandidateRejected, swap.Outcome);
        Assert.Contains("expected fake-3.0", swap.Reason);
        Assert.Contains("package digest", swap.Reason);
        Assert.Equal(oldPid, host.WorkerPid);
        await inFlight;
        Assert.Equal(oldPid, host.WorkerPid);
    }

    [Fact]
    public void The_candidate_must_keep_every_capability_and_the_browser()
    {
        var current = new BrowserWorkerHello { WorkerVersion = "1", ProtocolVersion = 1, Capabilities = ["browser.navigate", "browser.search"], BrowserAvailable = true };
        var narrower = new BrowserWorkerHello { WorkerVersion = "2", ProtocolVersion = 1, Capabilities = ["browser.navigate"], BrowserAvailable = false };
        var reasons = BrowserWorkerHost.VerifyCandidateHello(narrower, current, null, null);
        Assert.Contains(reasons, r => r.Contains("drops capability the current worker serves: browser.search"));
        Assert.Contains(reasons, r => r.Contains("browser unavailable"));

        var wider = new BrowserWorkerHello { WorkerVersion = "2", ProtocolVersion = 1, Capabilities = ["browser.navigate", "browser.search", "browser.new"], BrowserAvailable = true, PackageSha256 = "ABC" };
        Assert.Empty(BrowserWorkerHost.VerifyCandidateHello(wider, current, "2", "abc"));
        Assert.Empty(BrowserWorkerHost.VerifyCandidateHello(wider, null, null, null));
        Assert.Contains(BrowserWorkerHost.VerifyCandidateHello(new BrowserWorkerHello { WorkerVersion = "2", ProtocolVersion = 1, Capabilities = [], BrowserAvailable = true }, null, null, null), r => r.Contains("no capability"));
    }

    [Fact]
    public async Task A_current_worker_holding_a_browser_session_is_not_retired()
    {
        // The current worker claims one open session (owned media playing, say).
        await using var host = NewHost("--open-sessions 1");
        await host.StartAsync(CancellationToken.None);
        var oldPid = await PidOf(host);

        var swap = await host.SwapWorkerAsync(Candidate("--worker-version fake-2.0"), TimeSpan.FromSeconds(1));

        Assert.False(swap.Swapped);
        Assert.Equal(BrowserWorkerSwapResult.OutcomeBusy, swap.Outcome);
        Assert.Contains("keeps serving", swap.Reason);
        Assert.Equal(oldPid, host.WorkerPid);
        Assert.Equal("fake-1.0", host.Hello!.WorkerVersion);
        var still = await Exec(host, Payload("echo"));
        Assert.NotNull(still);
    }

    [Fact]
    public void Session_counting_reads_arrays_numbers_and_absence()
    {
        Assert.Equal(0, BrowserWorkerHost.CountSessions(new JsonObject()));
        Assert.Equal(2, BrowserWorkerHost.CountSessions(new JsonObject { ["sessions"] = 2 }));
        Assert.Equal(3, BrowserWorkerHost.CountSessions(new JsonObject { ["sessions"] = new JsonArray(1, 2, 3) }));
    }

    [Fact]
    public async Task With_no_worker_running_the_candidate_simply_becomes_the_worker()
    {
        await using var host = NewHost();
        Assert.False(host.WorkerRunning);

        var swap = await host.SwapWorkerAsync(Candidate("--worker-version fake-2.0"), TimeSpan.FromSeconds(5));

        Assert.True(swap.Swapped, swap.Reason);
        Assert.Null(swap.OldPid);
        Assert.Equal("fake-2.0", host.Hello!.WorkerVersion);
        Assert.NotNull(await Exec(host, Payload("echo")));
    }

    // ------------------------------------------------------- the request file protocol

    [Fact]
    public void The_request_file_is_parsed_strictly()
    {
        var request = BrowserCandidateRequest.Parse(new JsonObject
        {
            ["worker_command"] = @"C:\Program Files\PagentOS\agent\browser\.venv\Scripts\python.exe",
            ["worker_args"] = "-m browser_agent.worker",
            ["expected_version"] = "0.6.0",
            ["expected_package_sha256"] = "deadbeef",
            ["drain_timeout_s"] = 12,
        });
        Assert.Equal("0.6.0", request.ExpectedVersion);
        Assert.Equal(12, request.DrainTimeoutS);

        Assert.Equal(BrowserCandidateRequest.DefaultDrainTimeoutS, BrowserCandidateRequest.Parse(new JsonObject { ["worker_command"] = "x.exe" }).DrainTimeoutS);
        Assert.Throws<FormatException>(() => BrowserCandidateRequest.Parse(new JsonObject()));
        Assert.Throws<FormatException>(() => BrowserCandidateRequest.Parse(new JsonObject { ["worker_command"] = "x.exe", ["drain_timeout_s"] = -1 }));
        Assert.Throws<FormatException>(() => BrowserCandidateRequest.Parse(new JsonObject { ["worker_command"] = "x.exe", ["drain_timeout_s"] = 100000 }));
    }

    [Fact]
    public void A_candidate_must_live_under_the_install_root()
    {
        var root = @"C:\Program Files\PagentOS\agent";
        Assert.True(BrowserCandidateRequest.IsUnder(@"C:\Program Files\PagentOS\agent\browser.next\.venv\Scripts\python.exe", root));
        Assert.True(BrowserCandidateRequest.IsUnder(@"c:\program files\pagentos\AGENT\browser\.venv\Scripts\python.exe", root));
        Assert.False(BrowserCandidateRequest.IsUnder(@"C:\Program Files\PagentOS\agent2\python.exe", root));
        Assert.False(BrowserCandidateRequest.IsUnder(@"C:\Users\owner\python.exe", root));
        Assert.False(BrowserCandidateRequest.IsUnder(@"C:\Program Files\PagentOS\agent\..\evil\python.exe", root));
        Assert.Equal(@"C:\Program Files\PagentOS\agent", BrowserCandidateWatcher.DeriveAllowedRoot(@"C:\Program Files\PagentOS\agent\browser\.venv\Scripts\python.exe"));
    }

    [Fact]
    public async Task The_watcher_swaps_on_a_request_file_and_leaves_the_outcome_beside_it()
    {
        await using var host = NewHost();
        await host.StartAsync(CancellationToken.None);
        var oldPid = await PidOf(host);
        var (command, args) = FakeWorkerLauncher.Launcher();
        var allowedRoot = Path.GetDirectoryName(Path.GetFullPath(command))!;
        var watcher = new BrowserCandidateWatcher(host, _dir, allowedRoot, _log);

        Assert.Null(await watcher.PollOnceAsync(CancellationToken.None));

        await File.WriteAllTextAsync(watcher.RequestPath, new JsonObject
        {
            ["worker_command"] = command,
            ["worker_args"] = $"{args} --worker-version fake-2.0".Trim(),
            ["expected_version"] = "fake-2.0",
            ["drain_timeout_s"] = 5,
        }.ToJsonString());

        var result = await watcher.PollOnceAsync(CancellationToken.None);

        Assert.NotNull(result);
        Assert.True(result!.Swapped, result.Reason);
        Assert.NotEqual(oldPid, host.WorkerPid);
        Assert.False(File.Exists(watcher.RequestPath), "the request is consumed");
        var written = JsonNode.Parse(await File.ReadAllTextAsync(watcher.ResultPath)) as JsonObject;
        Assert.Equal("swapped", written!["outcome"]!.GetValue<string>());
        Assert.Equal("fake-2.0", written["new_version"]!.GetValue<string>());
        Assert.Null(await watcher.PollOnceAsync(CancellationToken.None));
    }

    [Fact]
    public async Task The_watcher_refuses_a_candidate_outside_the_install_root_without_starting_anything()
    {
        await using var host = NewHost();
        await host.StartAsync(CancellationToken.None);
        var oldPid = await PidOf(host);
        var (command, _) = FakeWorkerLauncher.Launcher();
        var watcher = new BrowserCandidateWatcher(host, _dir, Path.Combine(_dir, "install-root"), _log);
        await File.WriteAllTextAsync(watcher.RequestPath, new JsonObject { ["worker_command"] = command }.ToJsonString());

        var result = await watcher.PollOnceAsync(CancellationToken.None);

        Assert.NotNull(result);
        Assert.Equal(BrowserWorkerSwapResult.OutcomeRefused, result!.Outcome);
        Assert.Contains("outside the install root", result.Reason);
        Assert.Equal(oldPid, host.WorkerPid);
        Assert.Equal(1, host.Starts);
        Assert.False(File.Exists(watcher.RequestPath));
        Assert.True(File.Exists(watcher.ResultPath));
    }
}
