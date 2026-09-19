using System.Text.Json.Nodes;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Connection;
using PagentOS.Agent.Core.Ipc;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.DeviceService;
using PagentOS.SessionCompanion;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Projects;

/// <summary>
/// The family's place in the manifest and the routing (M23_APP_FACTORY_SPEC.md §3, ADR-0086):
/// five names after the documents family, advertised only behind <c>OperatorEnabled</c>,
/// interactive on every member, capped at 30 s (5 min 30 s for <c>project.test</c>), refused
/// by the service before the pipe when off, answered by the companion over the real pipe when
/// on — a scaffold and a typed refusal crossing the pipe as themselves.
/// </summary>
[Collection(ProjectLabCollection.Name)]
public sealed class ProjectAdvertisementTests
{
    [Fact]
    public void Compose_lists_the_five_names_last_only_when_operator_is_enabled()
    {
        Assert.Equal(["project.scaffold", "project.run", "project.status", "project.stop", "project.test", "project.package", "project.install", "project.uninstall", "project.artifact"], AgentCapabilities.Projects);
        Assert.Equal(9, AgentCapabilities.Projects.Count);

        var without = AgentCapabilities.Compose(browserEnabled: true, displayPowerEnabled: true, operatorEnabled: false);
        Assert.DoesNotContain(without, AgentCapabilities.IsProjects);

        var with = AgentCapabilities.Compose(browserEnabled: true, displayPowerEnabled: true, operatorEnabled: true);
        // M25 appended the scenes family after the projects family; the projects names keep
        // their order and their place, one step in from the end.
        Assert.Equal(AgentCapabilities.Scenes, with.TakeLast(1));
        Assert.Equal(AgentCapabilities.Projects, with.SkipLast(1).TakeLast(AgentCapabilities.Projects.Count));
        Assert.Equal(AgentCapabilities.Documents, with.SkipLast(AgentCapabilities.Projects.Count + AgentCapabilities.Scenes.Count).TakeLast(AgentCapabilities.Documents.Count));
        Assert.Equal(without.Count + 37 + 14 + 9 + 1, with.Count); // 37: ADR-0176 appended screen.ocr to the operator family
        Assert.Equal(with.Count, with.Distinct(StringComparer.Ordinal).Count());
        Assert.Equal("0.6.0", AgentInfo.SoftwareVersion);
        Assert.Equal(AgentCapabilities.Compose(browserEnabled: false), AgentCapabilities.Compose(browserEnabled: false, displayPowerEnabled: false, operatorEnabled: false));
    }

    [Fact]
    public void Every_member_is_interactive_a_projects_name_and_never_an_operator_documents_browser_or_desktop_name()
    {
        foreach (var name in AgentCapabilities.Projects)
        {
            Assert.True(AgentCapabilities.IsInteractive(name), $"{name} must route to the companion");
            Assert.True(AgentCapabilities.IsProjects(name));
            Assert.True(ProjectCapabilityNames.IsMember(name));
            Assert.False(AgentCapabilities.IsOperator(name));
            Assert.False(AgentCapabilities.IsDocuments(name));
            Assert.False(AgentCapabilities.IsBrowser(name));
            Assert.False(AgentCapabilities.IsDesktop(name));
            Assert.Matches("^[a-z][a-z0-9_.]{1,63}$", name);
        }

        // ADR-0086 decision 5: there is no delete.
        Assert.False(AgentCapabilities.IsProjects("project.delete"));
        Assert.False(AgentCapabilities.IsInteractive("project.delete"));
        Assert.False(AgentCapabilities.IsProjects("project.exec"));
    }

    [Fact]
    public async Task The_service_caps_the_family_and_refuses_it_before_the_pipe_when_operator_is_off()
    {
        Assert.Equal(TimeSpan.FromSeconds(30), InteractiveCapabilityExecutor.TimeoutCapFor(ProjectCapabilityNames.ProjectScaffold));
        // M25 raised project.run's ceiling to the longest batch bound plus headroom (a web run
        // still answers within its 20 s port wait; the cap is a ceiling, not a wait). M28's
        // 20 min build bound is now the longest, and project.test's ceiling is the same one
        // because a `dotnet test` restores and compiles before it runs anything.
        Assert.Equal(TimeSpan.FromMinutes(20) + TimeSpan.FromSeconds(30), InteractiveCapabilityExecutor.TimeoutCapFor(ProjectCapabilityNames.ProjectRun));
        Assert.Equal(TimeSpan.FromSeconds(30), InteractiveCapabilityExecutor.TimeoutCapFor(ProjectCapabilityNames.ProjectStatus));
        Assert.Equal(TimeSpan.FromSeconds(30), InteractiveCapabilityExecutor.TimeoutCapFor(ProjectCapabilityNames.ProjectStop));
        Assert.Equal(TimeSpan.FromMinutes(20) + TimeSpan.FromSeconds(30), InteractiveCapabilityExecutor.TimeoutCapFor(ProjectCapabilityNames.ProjectTest));

        var transport = new CountingTransport();
        var executor = new InteractiveCapabilityExecutor(transport, operatorEnabled: false);
        foreach (var name in AgentCapabilities.Projects)
        {
            var ex = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(TestCommands.New(name, new JsonObject()), CancellationToken.None));
            Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);
            Assert.False(ex.Retryable);
            Assert.Contains("OperatorEnabled=false", ex.Message, StringComparison.Ordinal);
        }

        Assert.Equal(0, transport.Calls);

        var enabled = new InteractiveCapabilityExecutor(transport, operatorEnabled: true);
        await enabled.ExecuteAsync(TestCommands.New(ProjectCapabilityNames.ProjectStatus, new JsonObject(), expiresIn: TimeSpan.FromMinutes(10)), CancellationToken.None);
        Assert.Equal(1, transport.Calls);
        Assert.Equal(TimeSpan.FromSeconds(30), transport.LastTimeout);
        await enabled.ExecuteAsync(TestCommands.New(ProjectCapabilityNames.ProjectTest, new JsonObject(), expiresIn: TimeSpan.FromMinutes(30)), CancellationToken.None);
        Assert.Equal(TimeSpan.FromMinutes(20) + TimeSpan.FromSeconds(30), transport.LastTimeout);

        var configuration = new ConfigurationBuilder().AddInMemoryCollection([new KeyValuePair<string, string?>("OperatorEnabled", "true")]).Build();
        var options = AgentServiceOptions.FromConfiguration(configuration);
        Assert.All(AgentCapabilities.Projects, name => Assert.Contains(name, options.AdvertisedCapabilities));
        var off = AgentServiceOptions.FromConfiguration(new ConfigurationBuilder().Build());
        Assert.DoesNotContain(off.AdvertisedCapabilities, AgentCapabilities.IsProjects);
    }

    // ------------------------------------------------------------- companion over the real pipe

    private static CompanionRuntime NewCompanion(string pipeName, ProjectCapabilities? projects)
        => new(
            pipeName,
            new AppLauncher(AppLauncher.DefaultAllowlist()),
            new ArtifactOpener(new[] { Path.GetTempPath() }, new NoOpFileOpener()),
            NullLogger.Instance,
            new BackoffPolicy(baseSeconds: 0.05, maxSeconds: 0.2),
            ServiceAdmissionPolicy.DeveloperMode(IpcTestSupport.CurrentSid()),
            projectCapabilities: projects);

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
    public async Task A_companion_built_without_the_projects_object_does_not_advertise_the_family_and_answers_capability_missing()
    {
        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        using var companionCts = new CancellationTokenSource();
        var companion = NewCompanion(pipeName, projects: null);
        var companionTask = Task.Run(() => companion.RunAsync(companionCts.Token));
        try
        {
            await WaitForCompanionAsync(server);
            Assert.DoesNotContain(server.CompanionCapabilities!, AgentCapabilities.IsProjects);
            var ex = await Assert.ThrowsAsync<CapabilityException>(() => server.ExecuteCapabilityAsync(
                ProjectCapabilityNames.ProjectStatus, new JsonObject { ["project_id"] = "x" }, TimeSpan.FromSeconds(10), CancellationToken.None));
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
    public async Task The_whole_chain_service_pipe_companion_projects_scaffolds_and_answers_typed_refusals()
    {
        using var lab = new ProjectLab();
        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        using var companionCts = new CancellationTokenSource();
        var companion = NewCompanion(pipeName, lab.Projects);
        var companionTask = Task.Run(() => companion.RunAsync(companionCts.Token));
        try
        {
            await WaitForCompanionAsync(server);
            Assert.Equal(AgentCapabilities.Compose(browserEnabled: false, displayPowerEnabled: false, operatorEnabled: true), server.CompanionCapabilities);

            var executor = new InteractiveCapabilityExecutor(server, operatorEnabled: true);
            var payload = ProjectLab.ScaffoldPayload("chain-1", "over-the-pipe");
            var scaffolded = await executor.ExecuteAsync(TestCommands.New(ProjectCapabilityNames.ProjectScaffold, payload, expiresIn: TimeSpan.FromMinutes(5)), CancellationToken.None);
            Assert.Equal(lab.FolderOf("over-the-pipe"), scaffolded!["root_path"]!.GetValue<string>(), StringComparer.OrdinalIgnoreCase);
            Assert.True(File.Exists(Path.Combine(lab.FolderOf("over-the-pipe"), "index.html")));
            Assert.NotNull(ProjectRoots.ReadMarker(lab.FolderOf("over-the-pipe")));

            var status = await executor.ExecuteAsync(TestCommands.New(ProjectCapabilityNames.ProjectStatus, new JsonObject { ["project_id"] = "chain-1" }, expiresIn: TimeSpan.FromMinutes(5)), CancellationToken.None);
            Assert.Equal("scaffolded", status!["state"]!.GetValue<string>());

            // Typed refusals cross the pipe as themselves.
            var notFound = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
                TestCommands.New(ProjectCapabilityNames.ProjectStatus, new JsonObject { ["project_id"] = "nobody" }), CancellationToken.None));
            Assert.Equal(ErrorClasses.NotFound, notFound.ErrorClass);
            var badManifest = ProjectLab.ScaffoldPayload("chain-2", "bad-manifest");
            badManifest["manifest"]!["run"] = new JsonObject { ["serve"] = "powershell -Command x" };
            var denied = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
                TestCommands.New(ProjectCapabilityNames.ProjectScaffold, badManifest), CancellationToken.None));
            Assert.Equal(ErrorClasses.PermissionDenied, denied.ErrorClass);
            Assert.False(Directory.Exists(lab.FolderOf("bad-manifest")));
            var outside = ProjectLab.ScaffoldPayload("chain-3", "outside");
            ((JsonArray)outside["files"]!).Add(new JsonObject { ["path"] = @"..\x.txt", ["text"] = "x" });
            var invalid = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
                TestCommands.New(ProjectCapabilityNames.ProjectScaffold, outside), CancellationToken.None));
            Assert.Equal(ErrorClasses.ValidationError, invalid.ErrorClass);
            Assert.Empty(server.Refusals);
            Assert.Empty(companion.Refusals);
        }
        finally
        {
            companionCts.Cancel();
            try { await companionTask.WaitAsync(TimeSpan.FromSeconds(5)); } catch (Exception) { }
            await server.StopAsync(CancellationToken.None);
        }
    }

    private sealed class CountingTransport : ICompanionCapabilityTransport
    {
        public int Calls { get; private set; }

        public TimeSpan LastTimeout { get; private set; }

        public Task<JsonObject?> ExecuteCapabilityAsync(string capability, JsonObject payload, TimeSpan timeout, CancellationToken cancellationToken)
        {
            Calls++;
            LastTimeout = timeout;
            return Task.FromResult<JsonObject?>(new JsonObject());
        }
    }

    private sealed class NoOpFileOpener : IFileOpener
    {
        public void Open(string fullPath)
        {
        }
    }
}
