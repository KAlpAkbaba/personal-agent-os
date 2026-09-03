using System.Diagnostics;
using System.Text.Json.Nodes;
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

public class PipeTests
{
    private static readonly string CmdPath =
        Environment.ExpandEnvironmentVariables(@"%WINDIR%\System32\cmd.exe");

    private static string NewPipeName() => $"pagentos-test-{Guid.NewGuid():N}";

    /// <summary>
    /// These round-trip tests run both halves inside one test process, so the "service" pipe
    /// is owned by the test user rather than by SYSTEM. The companion refuses that in its
    /// production posture — correctly — so these tests ask for the developer posture out
    /// loud, the same way `scripts/e2e-m1-device.ps1` does. Never a default, always a
    /// request: that is what the ADR-0028 review's Critical was about.
    /// </summary>
    private static CompanionRuntime NewCompanion(string pipeName, ArtifactOpener? artifactOpener = null)
        => new(
            pipeName,
            new AppLauncher(new Dictionary<string, string> { ["cmdtest"] = CmdPath }),
            artifactOpener ?? new ArtifactOpener(new[] { Path.GetTempPath() }, new RecordingFileOpener()),
            NullLogger.Instance,
            new BackoffPolicy(baseSeconds: 0.05, maxSeconds: 0.2),
            ServiceAdmissionPolicy.DeveloperMode(IpcTestSupport.CurrentSid()));

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
        var server = IpcTestSupport.NewServer(NewPipeName());
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
        var server = IpcTestSupport.NewServer(pipeName);
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
    public async Task Open_artifact_round_trips_over_real_named_pipe_using_fake_opener()
    {
        var pipeName = NewPipeName();
        var root = Path.Combine(Path.GetTempPath(), "pagentos-artifact-pipe", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        var artifact = Path.Combine(root, "report.pdf");
        File.WriteAllText(artifact, "%PDF-1.7 fake");
        var opener = new RecordingFileOpener();

        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        using var companionCts = new CancellationTokenSource();
        var companionTask = Task.Run(() =>
            NewCompanion(pipeName, new ArtifactOpener(new[] { root }, opener)).RunAsync(companionCts.Token));
        try
        {
            await WaitForCompanionAsync(server);

            var result = await server.ExecuteCapabilityAsync(
                AgentCapabilities.DesktopOpenArtifact,
                new JsonObject { ["path"] = artifact, ["artifact_id"] = Guid.NewGuid().ToString() },
                TimeSpan.FromSeconds(15),
                CancellationToken.None);

            Assert.NotNull(result);
            Assert.True(result!["opened"]!.GetValue<bool>());
            Assert.Equal("shell-associated", result["handler"]!.GetValue<string>());
            Assert.Equal(Path.GetFullPath(artifact), result["path"]!.GetValue<string>());
            Assert.Equal(new[] { Path.GetFullPath(artifact) }, opener.Opened);

            // An out-of-root path: the companion's security rejection travels back typed.
            var ex = await Assert.ThrowsAsync<CapabilityException>(() => server.ExecuteCapabilityAsync(
                AgentCapabilities.DesktopOpenArtifact,
                new JsonObject { ["path"] = Path.Combine(Path.GetTempPath(), "outside.pdf") },
                TimeSpan.FromSeconds(15),
                CancellationToken.None));
            Assert.Equal(ErrorClasses.SecurityScopeError, ex.ErrorClass);
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
            try
            {
                Directory.Delete(root, recursive: true);
            }
            catch (Exception)
            {
                // Best-effort cleanup.
            }
        }
    }

    [Fact]
    public async Task Companion_reconnects_with_backoff_after_service_restart()
    {
        var pipeName = NewPipeName();
        var firstServer = IpcTestSupport.NewServer(pipeName);
        await firstServer.StartAsync(CancellationToken.None);
        using var companionCts = new CancellationTokenSource();
        var companionTask = Task.Run(() => NewCompanion(pipeName).RunAsync(companionCts.Token));
        try
        {
            await WaitForCompanionAsync(firstServer);
            await firstServer.StopAsync(CancellationToken.None);

            // Simulated service restart with the same pipe name.
            var secondServer = IpcTestSupport.NewServer(pipeName);
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
