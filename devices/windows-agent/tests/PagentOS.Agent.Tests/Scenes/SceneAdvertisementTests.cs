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
using PagentOS.SessionCompanion.Operator;
using PagentOS.SessionCompanion.Projects;
using PagentOS.SessionCompanion.Scenes;
using Xunit;

namespace PagentOS.Agent.Tests.Scenes;

/// <summary>
/// The scenes family's place in the manifest and the routing (M25_CREATIVE_3D_SPEC.md §3,
/// ADR-0088, DEVICE_PROTOCOL.md §6m): ONE name after the projects family, advertised only
/// behind <c>OperatorEnabled</c>, interactive, capped at 30 s, refused by the service before
/// the pipe when off, and answered by the companion over the real pipe when on.
/// </summary>
[Collection(SceneLabCollection.Name)]
public sealed class SceneAdvertisementTests
{
    [Fact]
    public void Compose_lists_scene_inspect_last_only_when_operator_is_enabled()
    {
        Assert.Equal(["scene.inspect"], AgentCapabilities.Scenes);
        Assert.Single(AgentCapabilities.Scenes);

        var without = AgentCapabilities.Compose(browserEnabled: true, displayPowerEnabled: true, operatorEnabled: false);
        Assert.DoesNotContain(without, AgentCapabilities.IsScenes);

        var with = AgentCapabilities.Compose(browserEnabled: true, displayPowerEnabled: true, operatorEnabled: true);
        Assert.Equal(AgentCapabilities.Scenes, with.TakeLast(1));
        Assert.Equal(AgentCapabilities.Projects, with.SkipLast(1).TakeLast(AgentCapabilities.Projects.Count));
        Assert.Equal(without.Count + 37 + 14 + 9 + 1, with.Count); // 37: ADR-0176 appended screen.ocr to the operator family
        Assert.Equal(with.Count, with.Distinct(StringComparer.Ordinal).Count());

        // The first agent that advertises the family.
        Assert.Equal("0.6.0", AgentInfo.SoftwareVersion);
    }

    [Fact]
    public void Scene_inspect_is_interactive_a_scenes_name_and_never_a_member_of_another_family()
    {
        foreach (var name in AgentCapabilities.Scenes)
        {
            Assert.True(AgentCapabilities.IsInteractive(name), $"{name} must route to the companion");
            Assert.True(AgentCapabilities.IsScenes(name));
            Assert.True(SceneCapabilityNames.IsMember(name));
            Assert.False(AgentCapabilities.IsProjects(name));
            Assert.False(AgentCapabilities.IsOperator(name));
            Assert.False(AgentCapabilities.IsDocuments(name));
            Assert.False(AgentCapabilities.IsBrowser(name));
            Assert.False(AgentCapabilities.IsDesktop(name));
            Assert.Matches("^[a-z][a-z0-9_.]{1,63}$", name);
        }

        // There is no creator, no delete and no free-form runner on the device.
        Assert.False(AgentCapabilities.IsScenes("scene.create"));
        Assert.False(AgentCapabilities.IsScenes("scene.render"));
        Assert.False(AgentCapabilities.IsScenes("scene.delete"));
        Assert.False(AgentCapabilities.IsInteractive("scene.run_python"));
    }

    [Fact]
    public async Task The_service_caps_the_family_and_refuses_it_before_the_pipe_when_operator_is_off()
    {
        Assert.Equal(TimeSpan.FromSeconds(30), InteractiveCapabilityExecutor.TimeoutCapFor(SceneCapabilityNames.Inspect));

        // M25 raised project.run's ceiling to the longest batch bound plus headroom; M28's
        // 20 min build bound took that place. The rest of the projects family is untouched.
        Assert.Equal(NativeCapabilityNames.RunLimit + TimeSpan.FromSeconds(30), InteractiveCapabilityExecutor.TimeoutCapFor(ProjectCapabilityNames.ProjectRun));
        Assert.True(NativeCapabilityNames.RunLimit > SceneCapabilityNames.UnityRunLimit, "a ceiling that no longer covers Unity's own bound would time a licensed Unity run out mid-import");
        Assert.Equal(TimeSpan.FromSeconds(30), InteractiveCapabilityExecutor.TimeoutCapFor(ProjectCapabilityNames.ProjectStatus));

        var transport = new CountingTransport();
        var executor = new InteractiveCapabilityExecutor(transport, operatorEnabled: false);
        foreach (var name in AgentCapabilities.Scenes)
        {
            var ex = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(TestCommands.New(name, new JsonObject()), CancellationToken.None));
            Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);
            Assert.False(ex.Retryable);
            Assert.Contains("OperatorEnabled=false", ex.Message, StringComparison.Ordinal);
        }

        Assert.Equal(0, transport.Calls);

        var enabled = new InteractiveCapabilityExecutor(transport, operatorEnabled: true);
        await enabled.ExecuteAsync(TestCommands.New(SceneCapabilityNames.Inspect, new JsonObject(), expiresIn: TimeSpan.FromMinutes(10)), CancellationToken.None);
        Assert.Equal(1, transport.Calls);
        Assert.Equal(TimeSpan.FromSeconds(30), transport.LastTimeout);

        var configuration = new ConfigurationBuilder().AddInMemoryCollection([new KeyValuePair<string, string?>("OperatorEnabled", "true")]).Build();
        var options = AgentServiceOptions.FromConfiguration(configuration);
        Assert.All(AgentCapabilities.Scenes, name => Assert.Contains(name, options.AdvertisedCapabilities));
        var off = AgentServiceOptions.FromConfiguration(new ConfigurationBuilder().Build());
        Assert.DoesNotContain(off.AdvertisedCapabilities, AgentCapabilities.IsScenes);
    }

    [Fact]
    public void The_3d_root_is_always_authorised_however_the_owner_configured_the_roots()
    {
        var configured = OperatorOptions.FromConfiguration(new ConfigurationBuilder().AddInMemoryCollection(
        [
            new KeyValuePair<string, string?>("OperatorEnabled", "true"),
            new KeyValuePair<string, string?>("OperatorRoots", Path.Combine(Path.GetTempPath(), "only-here")),
            new KeyValuePair<string, string?>("ProjectsRoot", Path.Combine(Path.GetTempPath(), "elsewhere", "Projects")),
        ]).Build());

        Assert.Equal(Path.Combine(Path.GetTempPath(), "elsewhere", "Projects", SceneCapabilityNames.Root3dFolderName), configured.EffectiveProjectsRoot3d);
        Assert.Contains(configured.EffectiveProjectsRoot3d!, configured.AuthorisedRoots, StringComparer.OrdinalIgnoreCase);

        // Configured on its own, it is still authorised and is no longer derived.
        var separate = OperatorOptions.FromConfiguration(new ConfigurationBuilder().AddInMemoryCollection(
        [
            new KeyValuePair<string, string?>("ProjectsRoot3d", Path.Combine(Path.GetTempPath(), "scenes-elsewhere")),
        ]).Build());
        Assert.Equal(Path.Combine(Path.GetTempPath(), "scenes-elsewhere"), separate.EffectiveProjectsRoot3d);
        Assert.Contains(separate.EffectiveProjectsRoot3d!, separate.AuthorisedRoots, StringComparer.OrdinalIgnoreCase);

        // The default is Documents\PagentOS Projects\3d, and it is in the default roots.
        Assert.Equal(Path.Combine(OperatorOptions.DefaultProjectsRoot()!, SceneCapabilityNames.Root3dFolderName), OperatorOptions.Default3dRoot(OperatorOptions.DefaultProjectsRoot()));
        Assert.Contains(OperatorOptions.Default3dRoot(OperatorOptions.DefaultProjectsRoot())!, OperatorOptions.DefaultRoots(), StringComparer.OrdinalIgnoreCase);
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
    public async Task The_whole_chain_service_pipe_companion_answers_scene_inspect_and_its_typed_refusals()
    {
        using var lab = new SceneLab();
        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        using var companionCts = new CancellationTokenSource();
        var companion = NewCompanion(pipeName, lab.Projects);
        var companionTask = Task.Run(() => companion.RunAsync(companionCts.Token));
        try
        {
            await WaitForCompanionAsync(server);
            Assert.Contains(server.CompanionCapabilities!, AgentCapabilities.IsScenes);
            Assert.Equal(AgentCapabilities.Compose(browserEnabled: false, displayPowerEnabled: false, operatorEnabled: true), server.CompanionCapabilities);

            var executor = new InteractiveCapabilityExecutor(server, operatorEnabled: true);
            var payload = SceneLab.Scaffold3dPayload("chain-3d", "over-the-pipe", SceneLab.BlenderCommand());
            var scaffolded = await executor.ExecuteAsync(TestCommands.New(ProjectCapabilityNames.ProjectScaffold, payload, expiresIn: TimeSpan.FromMinutes(5)), CancellationToken.None);
            Assert.Equal(lab.Folder3dOf("over-the-pipe"), scaffolded!["root_path"]!.GetValue<string>(), StringComparer.OrdinalIgnoreCase);
            Assert.Equal(SceneCapabilityNames.Root3dFolderName, scaffolded["root"]!.GetValue<string>());

            // Nothing has run, so the read-back says so — as itself, across the pipe.
            var notYet = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
                TestCommands.New(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "chain-3d" }, expiresIn: TimeSpan.FromMinutes(5)), CancellationToken.None));
            Assert.Equal(ErrorClasses.PostconditionFailed, notYet.ErrorClass);

            // The driver's own out.json, written here in its place, comes back whole.
            File.WriteAllText(
                Path.Combine(lab.Folder3dOf("over-the-pipe"), SceneCapabilityNames.InspectionFileName),
                new JsonObject { ["tool"] = "blender", ["engine"] = "BLENDER_WORKBENCH", ["objects"] = new JsonArray() }.ToJsonString());
            var inspected = await executor.ExecuteAsync(
                TestCommands.New(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "chain-3d" }, expiresIn: TimeSpan.FromMinutes(5)), CancellationToken.None);
            Assert.Equal("BLENDER_WORKBENCH", ((JsonObject)inspected!["inspection"]!)["engine"]!.GetValue<string>());

            var unknown = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
                TestCommands.New(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "nobody" }), CancellationToken.None));
            Assert.Equal(ErrorClasses.NotFound, unknown.ErrorClass);

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

    [Fact]
    public async Task A_companion_built_without_the_projects_object_does_not_advertise_scene_inspect()
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
            Assert.DoesNotContain(server.CompanionCapabilities!, AgentCapabilities.IsScenes);
            var ex = await Assert.ThrowsAsync<CapabilityException>(() => server.ExecuteCapabilityAsync(
                SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "x" }, TimeSpan.FromSeconds(10), CancellationToken.None));
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
    public void The_editors_are_detected_where_they_are_installed_and_never_on_path()
    {
        // Whatever this machine has, the description is a fact, not a claim of control.
        var described = SceneTools.Describe();
        Assert.Contains("blender=", described, StringComparison.Ordinal);
        Assert.Contains("unity=", described, StringComparison.Ordinal);

        var blender = SceneTools.FindBlender();
        if (blender is null)
        {
            Assert.Contains("blender=(not installed)", described, StringComparison.Ordinal);
        }
        else
        {
            Assert.True(File.Exists(blender.Executable));
            Assert.EndsWith("blender.exe", blender.Executable, StringComparison.OrdinalIgnoreCase);
            Assert.False(blender.Executable.Contains("WindowsApps", StringComparison.OrdinalIgnoreCase));
        }

        // A runtime the machine lacks is dependency_unavailable, never a claim of control.
        if (SceneTools.FindUnity() is null)
        {
            var ex = Assert.Throws<CapabilityException>(() => SceneTools.Require(ProjectRuntime.Unity));
            Assert.Equal(ErrorClasses.DependencyUnavailable, ex.ErrorClass);
            Assert.Equal("runtime_missing", ex.Detail["detail"]);
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
