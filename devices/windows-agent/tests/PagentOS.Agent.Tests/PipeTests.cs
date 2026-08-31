using System.Diagnostics;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Connection;
using PagentOS.Agent.Core.Protocol;
using PagentOS.DeviceService;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests;

public class PipeTests
{
    private static readonly string CmdPath =
        Environment.ExpandEnvironmentVariables(@"%WINDIR%\System32\cmd.exe");

    private static string NewPipeName() => $"pagentos-test-{Guid.NewGuid():N}";

    private static CompanionRuntime NewCompanion(string pipeName)
        => new(
            pipeName,
            new AppLauncher(new Dictionary<string, string> { ["cmdtest"] = CmdPath }),
            NullLogger.Instance,
            new BackoffPolicy(baseSeconds: 0.05, maxSeconds: 0.2));

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
    public async Task Interactive_command_without_companion_fails_fast_with_dependency_unavailable()
    {
        var server = new CompanionPipeServer(NewPipeName(), NullLogger<CompanionPipeServer>.Instance);
        await server.StartAsync(CancellationToken.None);
        try
        {
            var stopwatch = Stopwatch.StartNew();
            var ex = await Assert.ThrowsAsync<CapabilityException>(() => server.ExecuteCapabilityAsync(
                AgentCapabilities.DesktopOpenApplication,
                new JsonObject { ["application"] = "cmdtest" },
                TimeSpan.FromSeconds(10),
                CancellationToken.None));
            stopwatch.Stop();

            Assert.Equal(ErrorClasses.DependencyUnavailable, ex.ErrorClass);
            Assert.True(ex.Retryable);
            Assert.True(stopwatch.Elapsed < TimeSpan.FromSeconds(2), "should fail fast, not wait for a timeout");
        }
        finally
        {
            await server.StopAsync(CancellationToken.None);
        }
    }

    [Fact]
    public async Task Service_and_companion_round_trip_over_real_named_pipe()
    {
        var pipeName = NewPipeName();
        var server = new CompanionPipeServer(pipeName, NullLogger<CompanionPipeServer>.Instance);
        await server.StartAsync(CancellationToken.None);
        using var companionCts = new CancellationTokenSource();
        var companionTask = Task.Run(() => NewCompanion(pipeName).RunAsync(companionCts.Token));
        try
        {
            await WaitForCompanionAsync(server);

            var result = await server.ExecuteCapabilityAsync(
                AgentCapabilities.DesktopOpenApplication,
                new JsonObject { ["application"] = "cmdtest", ["args"] = new JsonArray("/c", "exit") },
                TimeSpan.FromSeconds(15),
                CancellationToken.None);

            Assert.NotNull(result);
            Assert.True(result!["pid"]!.GetValue<int>() > 0);
            Assert.Equal(CmdPath, result["executable"]!.GetValue<string>());

            // Unknown application: the companion's allowlist rejection travels back typed.
            var ex = await Assert.ThrowsAsync<CapabilityException>(() => server.ExecuteCapabilityAsync(
                AgentCapabilities.DesktopOpenApplication,
                new JsonObject { ["application"] = "unknown-app" },
                TimeSpan.FromSeconds(15),
                CancellationToken.None));
            Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);
        }
        finally
        {
            companionCts.Cancel();
            try
            {
                await companionTask.WaitAsync(TimeSpan.FromSeconds(5));
            }
            catch (Exception)
            {
                // Teardown only.
            }

            await server.StopAsync(CancellationToken.None);
        }
    }

    [Fact]
    public async Task Companion_reconnects_with_backoff_after_service_restart()
    {
        var pipeName = NewPipeName();
        var firstServer = new CompanionPipeServer(pipeName, NullLogger<CompanionPipeServer>.Instance);
        await firstServer.StartAsync(CancellationToken.None);
        using var companionCts = new CancellationTokenSource();
        var companionTask = Task.Run(() => NewCompanion(pipeName).RunAsync(companionCts.Token));
        try
        {
            await WaitForCompanionAsync(firstServer);
            await firstServer.StopAsync(CancellationToken.None);

            // Simulated service restart with the same pipe name.
            var secondServer = new CompanionPipeServer(pipeName, NullLogger<CompanionPipeServer>.Instance);
            await secondServer.StartAsync(CancellationToken.None);
            try
            {
                await WaitForCompanionAsync(secondServer);

                var result = await secondServer.ExecuteCapabilityAsync(
                    AgentCapabilities.DesktopOpenApplication,
                    new JsonObject { ["application"] = "cmdtest", ["args"] = new JsonArray("/c", "exit") },
                    TimeSpan.FromSeconds(15),
                    CancellationToken.None);
                Assert.True(result!["pid"]!.GetValue<int>() > 0);
            }
            finally
            {
                await secondServer.StopAsync(CancellationToken.None);
            }
        }
        finally
        {
            companionCts.Cancel();
            try
            {
                await companionTask.WaitAsync(TimeSpan.FromSeconds(5));
            }
            catch (Exception)
            {
                // Teardown only.
            }
        }
    }
}
