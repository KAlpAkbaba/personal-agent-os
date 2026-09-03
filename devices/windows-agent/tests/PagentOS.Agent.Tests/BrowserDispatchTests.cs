using System.Diagnostics;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Connection;
using PagentOS.Agent.Core.Ipc;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.DeviceService;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>
/// The M13 browser family on the service→companion path: manifest composition, the
/// service-side routing and per-family timeout cap, the companion's dispatch (async,
/// concurrent, same pipe framing), and the whole chain over a real named pipe with the
/// real <see cref="BrowserWorkerHost"/> and the fake worker on the far end.
/// </summary>
public sealed class BrowserDispatchTests : IDisposable
{
    private static readonly Regex CapabilityName = new("^[a-z][a-z0-9_.]{1,63}$", RegexOptions.Compiled);
    private static readonly string CmdPath = Environment.ExpandEnvironmentVariables(@"%WINDIR%\System32\cmd.exe");

    private readonly string _dir = TestPaths.NewTempDir();

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

    // ------------------------------------------------------------- manifest composition

    [Fact]
    public void The_desktop_manifest_is_unchanged_and_All_still_means_exactly_that()
    {
        Assert.Equal(new[] { "desktop.open_application", "desktop.open_artifact" }, AgentCapabilities.Desktop);
        Assert.Equal(AgentCapabilities.Desktop, AgentCapabilities.All);
        Assert.Equal(AgentCapabilities.Desktop, AgentCapabilities.Compose(browserEnabled: false));
    }

    [Fact]
    public void With_browser_enabled_the_manifest_carries_the_family_marker_and_every_operation_once()
    {
        var composed = AgentCapabilities.Compose(browserEnabled: true);

        Assert.Equal(AgentCapabilities.Desktop.Count + 1 + 24, composed.Count);
        Assert.Equal(AgentCapabilities.Desktop, composed.Take(2));
        Assert.Equal(BrowserCapabilities.Family, composed[2]);
        Assert.Equal(24, BrowserCapabilities.Operations.Count);
        Assert.Equal(composed.Count, composed.Distinct(StringComparer.Ordinal).Count());
        Assert.All(composed, name => Assert.Matches(CapabilityName, name));
        Assert.All(BrowserCapabilities.All, name => Assert.StartsWith("browser.", name, StringComparison.Ordinal));

        // The names in BROWSER_CAPABILITIES.md §1, verbatim.
        Assert.Equal(
            new[]
            {
                "browser.session_open", "browser.session_close", "browser.worker_status",
                "browser.navigate", "browser.back", "browser.forward",
                "browser.tab_list", "browser.tab_new", "browser.tab_close", "browser.tab_select",
                "browser.inspect", "browser.find", "browser.click", "browser.fill", "browser.select_option",
                "browser.set_checked", "browser.scroll", "browser.wait", "browser.extract", "browser.snapshot",
                "browser.screenshot", "browser.download", "browser.search", "browser.fetch_evidence",
            },
            BrowserCapabilities.Operations);
    }

    [Fact]
    public void Family_membership_is_by_prefix_and_the_marker_is_not_an_operation()
    {
        Assert.True(AgentCapabilities.IsBrowser("browser.navigate"));
        Assert.True(AgentCapabilities.IsBrowser("browser.not_a_real_op"));
        Assert.False(AgentCapabilities.IsBrowser("desktop.open_application"));
        Assert.False(AgentCapabilities.IsBrowser("browsers.navigate"));
        Assert.True(BrowserCapabilities.IsOperation("browser.fetch_evidence"));
        Assert.False(BrowserCapabilities.IsOperation(BrowserCapabilities.Family));
    }

    [Theory]
    [InlineData(null, false)]
    [InlineData("", false)]
    [InlineData("false", false)]
    [InlineData("nonsense", false)]
    [InlineData("true", true)]
    [InlineData("True", true)]
    [InlineData("1", true)]
    public void The_service_option_BrowserEnabled_decides_the_advertised_manifest(string? raw, bool expected)
    {
        var configuration = new ConfigurationBuilder()
            .AddInMemoryCollection([new KeyValuePair<string, string?>("BrowserEnabled", raw)])
            .Build();
        var options = AgentServiceOptions.FromConfiguration(configuration);

        Assert.Equal(expected, options.BrowserEnabled);
        Assert.Equal(AgentCapabilities.Compose(expected), options.AdvertisedCapabilities);
    }

    [Fact]
    public void Companion_browser_options_default_under_the_companion_data_dir_and_read_every_key()
    {
        var companionDataDir = Path.Combine(_dir, "companion");
        var defaults = BrowserWorkerOptions.FromConfiguration(new ConfigurationBuilder().Build(), companionDataDir);
        Assert.False(defaults.IsConfigured);
        Assert.Equal(Path.Combine(companionDataDir, "browser"), defaults.DataDir);
        Assert.Equal(Path.Combine(companionDataDir, "browser", "profile"), defaults.ProfileDir);
        Assert.Equal("chrome", defaults.Channel);
        Assert.True(defaults.Visible);
        Assert.Equal(600, defaults.IdleTimeoutS);
        Assert.False(defaults.Eager);

        var configured = BrowserWorkerOptions.FromConfiguration(new ConfigurationBuilder().AddInMemoryCollection(
        [
            new("BrowserWorkerCommand", @"C:\Program Files\PagentOS\agent\browser\.venv\Scripts\python.exe"),
            new("BrowserWorkerArgs", "-m browser_agent.worker"),
            new("BrowserDataDir", Path.Combine(_dir, "bdata")),
            new("BrowserChannel", "chromium"),
            new("BrowserVisible", "false"),
            new("BrowserIdleTimeoutS", "90"),
            new("BrowserWorkerEager", "true"),
        ]).Build(), companionDataDir);
        Assert.True(configured.IsConfigured);
        Assert.Equal(Path.Combine(_dir, "bdata"), configured.DataDir);
        Assert.Equal(Path.Combine(_dir, "bdata", "profile"), configured.ProfileDir);
        Assert.Equal("chromium", configured.Channel);
        Assert.False(configured.Visible);
        Assert.Equal(90, configured.IdleTimeoutS);
        Assert.True(configured.Eager);
        Assert.Equal(
            new[] { "-m", "browser_agent.worker", "--data-dir", Path.Combine(_dir, "bdata"), "--profile-dir", Path.Combine(_dir, "bdata", "profile"), "--channel", "chromium", "--headless", "--idle-timeout-s", "90" },
            configured.BuildArgumentList());
    }

    // ------------------------------------------------------------- service routing + cap

    [Fact]
    public void The_timeout_cap_is_60s_for_desktop_and_120s_for_browser()
    {
        Assert.Equal(TimeSpan.FromSeconds(60), InteractiveCapabilityExecutor.TimeoutCapFor(AgentCapabilities.DesktopOpenApplication));
        Assert.Equal(TimeSpan.FromSeconds(60), InteractiveCapabilityExecutor.TimeoutCapFor(AgentCapabilities.DesktopOpenArtifact));
        Assert.Equal(TimeSpan.FromSeconds(120), InteractiveCapabilityExecutor.TimeoutCapFor(BrowserCapabilities.FetchEvidence));
        Assert.Equal(TimeSpan.FromSeconds(120), InteractiveCapabilityExecutor.TimeoutCapFor("browser.anything"));

        var executor = new InteractiveCapabilityExecutor(IpcTestSupport.NewServer(IpcTestSupport.NewPipeName()), browserEnabled: true);
        var farOff = TimeSpan.FromMinutes(30);
        Assert.Equal(TimeSpan.FromSeconds(60), executor.ResolveTimeout(TestCommands.New(AgentCapabilities.DesktopOpenApplication, expiresIn: farOff)));
        Assert.Equal(TimeSpan.FromSeconds(120), executor.ResolveTimeout(TestCommands.New(BrowserCapabilities.Navigate, expiresIn: farOff)));
        // Below the cap the command's own remaining life wins, floored at 1 s.
        var soon = executor.ResolveTimeout(TestCommands.New(BrowserCapabilities.Navigate, expiresIn: TimeSpan.FromSeconds(30)));
        Assert.InRange(soon, TimeSpan.FromSeconds(25), TimeSpan.FromSeconds(30));
        Assert.Equal(TimeSpan.FromSeconds(1), executor.ResolveTimeout(TestCommands.New(BrowserCapabilities.Navigate, expiresIn: TimeSpan.FromMilliseconds(10))));
    }

    [Fact]
    public async Task With_browser_disabled_the_service_answers_capability_missing_before_asking_the_companion()
    {
        var server = IpcTestSupport.NewServer(IpcTestSupport.NewPipeName());
        var executor = new InteractiveCapabilityExecutor(server, browserEnabled: false);
        Assert.False(executor.BrowserEnabled);

        // No companion is connected, so a routed request would say dependency_unavailable;
        // capability_missing proves the refusal happened first.
        var ex = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
            TestCommands.New(BrowserCapabilities.Navigate, new JsonObject { ["session_id"] = "t", ["url"] = "https://example.org/" }),
            CancellationToken.None));

        Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Contains("BrowserEnabled", ex.Message, StringComparison.Ordinal);

        // Desktop is still routed (and reaches the "no companion" answer).
        var desktop = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
            TestCommands.New(AgentCapabilities.DesktopOpenApplication), CancellationToken.None));
        Assert.Equal(ErrorClasses.DependencyUnavailable, desktop.ErrorClass);
    }

    [Fact]
    public async Task With_browser_enabled_and_no_companion_the_family_fails_fast_dependency_unavailable_retryable()
    {
        var server = IpcTestSupport.NewServer(IpcTestSupport.NewPipeName());
        await server.StartAsync(CancellationToken.None);
        try
        {
            var executor = new InteractiveCapabilityExecutor(server, browserEnabled: true);
            var stopwatch = Stopwatch.StartNew();

            var ex = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
                TestCommands.New(BrowserCapabilities.FetchEvidence, new JsonObject { ["session_id"] = "t" }),
                CancellationToken.None));

            Assert.Equal(ErrorClasses.DependencyUnavailable, ex.ErrorClass);
            Assert.True(ex.Retryable);
            Assert.True(stopwatch.Elapsed < TimeSpan.FromSeconds(2));
        }
        finally
        {
            await server.StopAsync(CancellationToken.None);
        }
    }

    [Fact]
    public async Task An_unknown_family_is_still_capability_missing()
    {
        var executor = new InteractiveCapabilityExecutor(IpcTestSupport.NewServer(IpcTestSupport.NewPipeName()), browserEnabled: true);
        var ex = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(TestCommands.New("voice.speak"), CancellationToken.None));
        Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);
    }

    // ------------------------------------------------------------- companion over the real pipe

    private CompanionRuntime NewCompanion(string pipeName, BrowserWorkerHost? browserWorker)
        => new(
            pipeName,
            new AppLauncher(new Dictionary<string, string> { ["cmdtest"] = CmdPath }),
            new ArtifactOpener(new[] { Path.GetTempPath() }, new RecordingFileOpener()),
            NullLogger.Instance,
            new BackoffPolicy(baseSeconds: 0.05, maxSeconds: 0.2),
            ServiceAdmissionPolicy.DeveloperMode(IpcTestSupport.CurrentSid()),
            browserWorker: browserWorker);

    private static async Task WaitForCompanionAsync(CompanionPipeServer server)
    {
        var deadline = DateTime.UtcNow.AddSeconds(15);
        while (!server.CompanionConnected)
        {
            Assert.True(DateTime.UtcNow < deadline, "companion did not connect in time");
            await Task.Delay(20);
        }
    }

    [Fact]
    public async Task A_companion_without_a_worker_advertises_desktop_only_and_answers_browser_with_capability_missing()
    {
        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        using var companionCts = new CancellationTokenSource();
        var companion = NewCompanion(pipeName, browserWorker: null);
        var companionTask = Task.Run(() => companion.RunAsync(companionCts.Token));
        try
        {
            await WaitForCompanionAsync(server);

            Assert.Equal(AgentCapabilities.Desktop, companion.AdvertisedCapabilities);
            Assert.Equal(AgentCapabilities.Desktop, server.CompanionCapabilities);

            var ex = await Assert.ThrowsAsync<CapabilityException>(() => server.ExecuteCapabilityAsync(
                BrowserCapabilities.Navigate, new JsonObject { ["session_id"] = "t" }, TimeSpan.FromSeconds(10), CancellationToken.None));
            Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);
            Assert.False(ex.Retryable);
        }
        finally
        {
            companionCts.Cancel();
            try { await companionTask.WaitAsync(TimeSpan.FromSeconds(5)); } catch (Exception) { }
            await server.StopAsync(CancellationToken.None);
        }
    }

    [Fact]
    public async Task The_whole_chain_service_pipe_companion_worker_round_trips_with_the_120s_cap_and_interleaving()
    {
        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        await using var host = FakeWorkerLauncher.NewHost(_dir);
        using var companionCts = new CancellationTokenSource();
        var companion = NewCompanion(pipeName, host);
        var companionTask = Task.Run(() => companion.RunAsync(companionCts.Token));
        try
        {
            await WaitForCompanionAsync(server);

            // Advertisement: the companion says browser.* because a worker is configured.
            Assert.Equal(AgentCapabilities.Compose(browserEnabled: true), server.CompanionCapabilities);
            Assert.Contains(BrowserCapabilities.Family, server.CompanionCapabilities!);

            var executor = new InteractiveCapabilityExecutor(server, browserEnabled: true);

            // The service's 120 s cap reaches the worker as timeout_ms (minus the companion's
            // 500 ms headroom, which is what keeps the typed timeout ahead of the service's).
            var result = await executor.ExecuteAsync(
                TestCommands.New(BrowserCapabilities.FetchEvidence, new JsonObject { ["mode"] = "echo", ["session_id"] = "t", ["url"] = "https://example.org/" }, expiresIn: TimeSpan.FromMinutes(30)),
                CancellationToken.None);
            Assert.NotNull(result);
            Assert.Equal(BrowserCapabilities.FetchEvidence, result!["capability"]!.GetValue<string>());
            Assert.Equal(120_000 - 500, result["timeout_ms_seen"]!.GetValue<int>());

            // Typed errors cross the pipe intact.
            var typed = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
                TestCommands.New(BrowserCapabilities.Click, new JsonObject { ["mode"] = "error", ["error_class"] = ErrorClasses.SecurityScopeError, ["message"] = "HIGH_IMPACT refused", ["retryable"] = false }),
                CancellationToken.None));
            Assert.Equal(ErrorClasses.SecurityScopeError, typed.ErrorClass);
            Assert.Equal("HIGH_IMPACT refused", typed.Message);

            // Concurrency: a slow browser request must not block a desktop request behind it,
            // and the two browser responses may arrive in either order — request_id
            // correlates, the sequence stays strictly increasing, nothing is refused.
            var slow = executor.ExecuteAsync(
                TestCommands.New(BrowserCapabilities.Extract, new JsonObject { ["mode"] = "sleep", ["sleep_ms"] = 800, ["session_id"] = "t", ["tag"] = "slow" }),
                CancellationToken.None);
            var desktop = await server.ExecuteCapabilityAsync(
                AgentCapabilities.DesktopOpenApplication,
                new JsonObject { ["application"] = "cmdtest", ["args"] = new JsonArray("/c", "exit") },
                TimeSpan.FromSeconds(15),
                CancellationToken.None);
            Assert.True(desktop!["pid"]!.GetValue<int>() > 0);
            Assert.False(slow.IsCompleted, "the desktop request must not wait for the slow browser one");
            var fast = await executor.ExecuteAsync(
                TestCommands.New(BrowserCapabilities.Inspect, new JsonObject { ["mode"] = "echo", ["session_id"] = "t", ["tag"] = "fast" }),
                CancellationToken.None);
            Assert.Equal("fast", fast!["echo"]!["tag"]!.GetValue<string>());
            var slowResult = await slow;
            Assert.Equal("slow", slowResult!["echo"]!["tag"]!.GetValue<string>());
            Assert.Empty(server.Refusals);
            Assert.Empty(companion.Refusals);

            // The family marker is never an operation.
            var marker = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
                TestCommands.New(BrowserCapabilities.Family, new JsonObject()), CancellationToken.None));
            Assert.Equal(ErrorClasses.CapabilityMissing, marker.ErrorClass);
        }
        finally
        {
            companionCts.Cancel();
            try { await companionTask.WaitAsync(TimeSpan.FromSeconds(5)); } catch (Exception) { }
            await server.StopAsync(CancellationToken.None);
        }
    }

    [Fact]
    public async Task A_browser_timeout_over_the_pipe_arrives_typed_from_the_companion_not_synthesised_by_the_service()
    {
        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        await using var host = FakeWorkerLauncher.NewHost(_dir);
        using var companionCts = new CancellationTokenSource();
        var companionTask = Task.Run(() => NewCompanion(pipeName, host).RunAsync(companionCts.Token));
        try
        {
            await WaitForCompanionAsync(server);

            var ex = await Assert.ThrowsAsync<CapabilityException>(() => server.ExecuteCapabilityAsync(
                BrowserCapabilities.Wait,
                new JsonObject { ["mode"] = "sleep", ["sleep_ms"] = 30_000, ["session_id"] = "t" },
                TimeSpan.FromSeconds(2),
                CancellationToken.None));

            Assert.Equal(ErrorClasses.Timeout, ex.ErrorClass);
            Assert.True(ex.Retryable);
            // The companion's message, not the service's "did not answer" one.
            Assert.Contains("browser worker did not answer", ex.Message, StringComparison.Ordinal);
            Assert.Equal(1, host.CancelsSent);
        }
        finally
        {
            companionCts.Cancel();
            try { await companionTask.WaitAsync(TimeSpan.FromSeconds(5)); } catch (Exception) { }
            await server.StopAsync(CancellationToken.None);
        }
    }
}
