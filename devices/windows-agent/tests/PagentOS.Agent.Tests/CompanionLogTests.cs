using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Logging;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>
/// The companion's file log and the <c>browser_lifecycle_violation</c> class — the two
/// other lessons of the 2026-09-03 incident: nobody could reconstruct the sequence because
/// the companion logged to a console nobody saw, and a worker had no class with which to say
/// "the profile is held by a Chrome I do not own; do NOT retry this".
/// </summary>
public sealed class CompanionLogTests : IDisposable
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

    // ------------------------------------------------------------ the file log

    [Fact]
    public void The_companion_log_lives_under_the_data_dir_beside_the_audit_and_is_bounded()
    {
        Assert.Equal(Path.Combine(@"C:\ProgramData\PagentOS\companion", "logs", "companion.log"), Program.CompanionLogPath(@"C:\ProgramData\PagentOS\companion"));
        Assert.True(Program.CompanionLogMaxBytes > 0);
        Assert.True(Program.CompanionLogKeepRotated >= 1);
    }

    [Fact]
    public void The_file_logger_rotates_at_the_bound_keeps_the_configured_generations_and_never_loses_the_newest_line()
    {
        var path = Path.Combine(_dir, "logs", "companion.log");
        const long MaxBytes = 4096;
        using var provider = new FileLoggerProvider(path, LogLevel.Debug, maxBytes: MaxBytes, keepRotated: 2);
        var logger = provider.CreateLogger("test");
        var filler = new string('x', 120);

        for (var i = 0; i < 300; i++)
        {
            logger.LogInformation("line {Index} {Filler}", i, filler);
        }

        Assert.True(File.Exists(path));
        Assert.True(new FileInfo(path).Length <= MaxBytes, "the live file never exceeds the bound");
        Assert.True(File.Exists(FileLoggerProvider.RotatedPath(path, 1)), "the newest rotated generation exists");
        Assert.True(File.Exists(FileLoggerProvider.RotatedPath(path, 2)), "the second generation exists");
        Assert.False(File.Exists(FileLoggerProvider.RotatedPath(path, 3)), "nothing beyond keepRotated is kept");
        Assert.True(new FileInfo(FileLoggerProvider.RotatedPath(path, 1)).Length <= MaxBytes);

        var live = File.ReadAllLines(path);
        Assert.Contains("line 299 ", live[^1], StringComparison.Ordinal);
        var previous = File.ReadAllLines(FileLoggerProvider.RotatedPath(path, 1));
        Assert.True(previous.Length > 0);
        // Generations are contiguous and ordered: the last line of .1 precedes the first line of the live file.
        var lastRotated = int.Parse(previous[^1].Split("line ")[1].Split(' ')[0], System.Globalization.CultureInfo.InvariantCulture);
        var firstLive = int.Parse(live[0].Split("line ")[1].Split(' ')[0], System.Globalization.CultureInfo.InvariantCulture);
        Assert.Equal(lastRotated + 1, firstLive);
    }

    [Fact]
    public void An_unbounded_file_logger_is_the_default_so_the_device_service_is_unchanged()
    {
        var path = Path.Combine(_dir, "logs", "device-service.log");
        using var provider = new FileLoggerProvider(path);
        Assert.Equal(0, provider.MaxBytes);
        Assert.Equal(path, provider.FilePath);
        provider.CreateLogger("t").LogInformation("one");
        Assert.True(File.Exists(path));
        Assert.False(File.Exists(FileLoggerProvider.RotatedPath(path, 1)));
    }

    [Fact]
    public async Task Every_browser_request_writes_its_capability_request_id_outcome_and_duration_to_the_log()
    {
        // Started first: the ten seconds below is the budget for the CALL, not for launching
        // the worker process it happens to be the first to need.
        await using var host = await FakeWorkerLauncher.NewStartedHostAsync(_dir, _log);

        await host.ExecuteAsync(BrowserCapabilities.Inspect, new JsonObject { ["mode"] = "echo" }, TimeSpan.FromSeconds(10), CancellationToken.None);
        await Assert.ThrowsAsync<CapabilityException>(() => host.ExecuteAsync(
            BrowserCapabilities.Click,
            new JsonObject { ["mode"] = "error", ["error_class"] = ErrorClasses.UiTargetNotFound, ["message"] = "nope", ["retryable"] = false },
            TimeSpan.FromSeconds(10),
            CancellationToken.None));

        var ok = Assert.Single(_log.Lines, line => line.Contains("browser request browser.inspect request_id=", StringComparison.Ordinal));
        Assert.Matches(@"browser request browser\.inspect request_id=[0-9a-f]{32} outcome=ok duration_ms=\d+", ok);
        var failed = Assert.Single(_log.Lines, line => line.Contains("browser request browser.click request_id=", StringComparison.Ordinal));
        Assert.Matches(@"browser request browser\.click request_id=[0-9a-f]{32} outcome=ui_target_not_found duration_ms=\d+ retryable=false", failed);
        Assert.True(_log.Any("browser worker started (pid="));
    }

    // ------------------------------------------------------------ the class

    [Fact]
    public void Browser_lifecycle_violation_is_a_device_taxonomy_class_the_validator_accepts()
    {
        Assert.Equal("browser_lifecycle_violation", ErrorClasses.BrowserLifecycleViolation);
        Assert.Contains(ErrorClasses.BrowserLifecycleViolation, ErrorClasses.All);
        MessageValidator.ValidateErrorObject(ErrorObjects.Create(ErrorClasses.BrowserLifecycleViolation, "orphan chrome holds the profile", retryable: false));
    }

    [Fact]
    public async Task A_worker_reported_browser_lifecycle_violation_passes_through_as_itself_and_is_never_retryable()
    {
        // The one that actually failed, on CI, at eleven seconds: the worker's cold start
        // ate the ten-second budget and the class the owner would have seen was `timeout`
        // rather than the lifecycle violation the worker really reported.
        await using var host = await FakeWorkerLauncher.NewStartedHostAsync(_dir, _log);

        // The worker says retryable=true; the companion knows better — a retry is a fresh
        // launch onto a profile something else still holds.
        var ex = await Assert.ThrowsAsync<CapabilityException>(() => host.ExecuteAsync(
            BrowserCapabilities.SessionOpen,
            new JsonObject
            {
                ["mode"] = "error",
                ["error_class"] = ErrorClasses.BrowserLifecycleViolation,
                ["message"] = "profile already held by chrome.exe pid 4242",
                ["retryable"] = true,
            },
            TimeSpan.FromSeconds(10),
            CancellationToken.None));

        Assert.Equal(ErrorClasses.BrowserLifecycleViolation, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Contains("pid 4242", ex.Message, StringComparison.Ordinal);
        Assert.True(_log.Any("outcome=browser_lifecycle_violation duration_ms="));
        Assert.True(_log.Any("retryable=false"));
    }

    [Fact]
    public async Task A_slow_worker_start_is_not_charged_to_the_call_that_happens_to_be_first()
    {
        // The 2026-09-09 CI failure, made deterministic. There, the lifecycle-violation test
        // above reported `timeout` after eleven seconds: the first ExecuteAsync launched the
        // worker process INSIDE its own ten-second budget, and a busy shared runner spent it
        // on the launch. Here the worker announces itself two seconds late on purpose and the
        // call is given one second - so a start charged to the call cannot possibly fit, and
        // the only way to see the worker's real error class is for the start to be paid
        // separately, under the host's own hello budget.
        await using var host = await FakeWorkerLauncher.NewStartedHostAsync(
            _dir, _log, extraArgs: "--hello-delay-ms 2000");

        var ex = await Assert.ThrowsAsync<CapabilityException>(() => host.ExecuteAsync(
            BrowserCapabilities.SessionOpen,
            new JsonObject
            {
                ["mode"] = "error",
                ["error_class"] = ErrorClasses.BrowserLifecycleViolation,
                ["message"] = "profile already held by chrome.exe pid 4242",
                ["retryable"] = false,
            },
            TimeSpan.FromSeconds(1),
            CancellationToken.None));

        // The owner is told what the worker reported, not what our own bookkeeping cost.
        Assert.Equal(ErrorClasses.BrowserLifecycleViolation, ex.ErrorClass);
        Assert.DoesNotContain(ErrorClasses.Timeout, _log.Lines.Select(l => l), StringComparer.Ordinal);
    }
}
