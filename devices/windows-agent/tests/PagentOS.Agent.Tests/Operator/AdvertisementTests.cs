using System.Text.Json;
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
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// The family's place in the manifest and the routing (M19_DIGITAL_OPERATOR_SPEC.md §2/§3,
/// ADR-0082 decision 1): advertised only behind <c>OperatorEnabled</c>, interactive on every
/// member, capped at 30 s (15 s for <c>app.launch</c>), refused by the service before the pipe
/// when off, and answered by the companion — over the real pipe — only when it was built with
/// the operator.
/// </summary>
public sealed class AdvertisementTests
{
    [Fact]
    public void Compose_lists_the_family_only_when_operator_is_enabled_and_after_everything_else()
    {
        var without = AgentCapabilities.Compose(browserEnabled: false, displayPowerEnabled: false, operatorEnabled: false);
        Assert.DoesNotContain(without, AgentCapabilities.IsOperator);
        Assert.Equal(AgentCapabilities.Compose(browserEnabled: false), without);

        // M20 appends the documents family (6; 7 since M22's file.fetch) after the operator
        // family (32) under the same flag, M23 the projects family (5) after that, and M25 the
        // scenes family (1) after that; the operator family itself is exactly where it was.
        var with = AgentCapabilities.Compose(browserEnabled: true, displayPowerEnabled: true, operatorEnabled: true);
        var appended = AgentCapabilities.Operator.Count + AgentCapabilities.Documents.Count + AgentCapabilities.Projects.Count + AgentCapabilities.Scenes.Count;
        Assert.Equal(AgentCapabilities.Operator, with.TakeLast(appended).Take(AgentCapabilities.Operator.Count));
        Assert.Equal(AgentCapabilities.Documents, with.TakeLast(appended).Skip(AgentCapabilities.Operator.Count).Take(AgentCapabilities.Documents.Count));
        Assert.Equal(AgentCapabilities.Projects, with.TakeLast(AgentCapabilities.Projects.Count + AgentCapabilities.Scenes.Count).Take(AgentCapabilities.Projects.Count));
        Assert.Equal(AgentCapabilities.Scenes, with.TakeLast(AgentCapabilities.Scenes.Count));
        Assert.Equal(with.Count, with.Distinct(StringComparer.Ordinal).Count());
        Assert.Equal(32, AgentCapabilities.Operator.Count);
        Assert.Equal(7, AgentCapabilities.Documents.Count);
        Assert.Equal(5, AgentCapabilities.Projects.Count);
        Assert.Single(AgentCapabilities.Scenes);
        Assert.Equal(AgentCapabilities.Compose(browserEnabled: true, displayPowerEnabled: true), with.Take(with.Count - appended));
    }

    [Fact]
    public void Every_member_is_interactive_an_operator_name_and_never_a_browser_or_desktop_name()
    {
        foreach (var name in AgentCapabilities.Operator)
        {
            Assert.True(AgentCapabilities.IsInteractive(name), $"{name} must route to the companion");
            Assert.True(AgentCapabilities.IsOperator(name));
            Assert.False(AgentCapabilities.IsBrowser(name));
            Assert.False(AgentCapabilities.IsDesktop(name));
            Assert.Matches("^[a-z][a-z0-9_.]{1,63}$", name);
        }

        Assert.False(AgentCapabilities.IsOperator("browser.click"));
        Assert.False(AgentCapabilities.IsOperator("desktop.open_application"));
        Assert.False(AgentCapabilities.IsOperator("app.anything"));
        Assert.Equal(AgentCapabilities.Desktop, AgentCapabilities.All);
        Assert.All(OperatorCapabilityNames.Guarded, g => Assert.True(OperatorCapabilityNames.IsGuarded(g)));
        Assert.False(OperatorCapabilityNames.IsGuarded(OperatorCapabilityNames.UiInvoke));
    }

    [Fact]
    public void The_service_caps_the_family_at_30s_and_app_launch_at_15s()
    {
        Assert.Equal(TimeSpan.FromSeconds(30), InteractiveCapabilityExecutor.TimeoutCapFor(OperatorCapabilityNames.KeyboardType));
        Assert.Equal(TimeSpan.FromSeconds(30), InteractiveCapabilityExecutor.TimeoutCapFor(OperatorCapabilityNames.TerminalExecute));
        Assert.Equal(TimeSpan.FromSeconds(15), InteractiveCapabilityExecutor.TimeoutCapFor(OperatorCapabilityNames.AppLaunch));
        Assert.Equal(TimeSpan.FromSeconds(60), InteractiveCapabilityExecutor.TimeoutCapFor(AgentCapabilities.DesktopOpenApplication));
        Assert.Equal(TimeSpan.FromSeconds(120), InteractiveCapabilityExecutor.TimeoutCapFor(BrowserCapabilities.Navigate));

        var executor = new InteractiveCapabilityExecutor(new CountingTransport(), operatorEnabled: true);
        Assert.Equal(TimeSpan.FromSeconds(30), executor.ResolveTimeout(TestCommands.New(OperatorCapabilityNames.WindowList, expiresIn: TimeSpan.FromMinutes(10))));
        Assert.Equal(TimeSpan.FromSeconds(15), executor.ResolveTimeout(TestCommands.New(OperatorCapabilityNames.AppLaunch, expiresIn: TimeSpan.FromMinutes(10))));
    }

    [Theory]
    [InlineData(null, false)]
    [InlineData("", false)]
    [InlineData("false", false)]
    [InlineData("true", true)]
    [InlineData("1", true)]
    public void The_service_option_OperatorEnabled_decides_advertisement(string? raw, bool expected)
    {
        var configuration = new ConfigurationBuilder()
            .AddInMemoryCollection([new KeyValuePair<string, string?>("OperatorEnabled", raw)])
            .Build();
        var options = AgentServiceOptions.FromConfiguration(configuration);

        Assert.Equal(expected, options.OperatorEnabled);
        Assert.Equal(expected, options.AdvertisedCapabilities.Contains(OperatorCapabilityNames.AppLaunch));
        Assert.Equal(expected, options.AdvertisedCapabilities.Contains(OperatorCapabilityNames.TerminalStatus));

        var companion = OperatorOptions.FromConfiguration(configuration);
        Assert.Equal(expected, companion.Enabled);
        Assert.Equal(TerminalRunner.DefaultAllowlist, companion.TerminalAllowlist);
        Assert.Equal(OperatorOptions.DefaultRoots(), companion.AuthorisedRoots);
    }

    [Fact]
    public void Companion_configuration_can_extend_the_allowlist_and_the_roots()
    {
        var configuration = new ConfigurationBuilder()
            .AddInMemoryCollection(
            [
                new KeyValuePair<string, string?>("OperatorEnabled", "true"),
                new KeyValuePair<string, string?>("TerminalAllowlist", "hostname; Get-Date ;ipconfig /all"),
                new KeyValuePair<string, string?>("OperatorRoots", @"D:\Owner;E:\Projects"),
            ])
            .Build();
        var options = OperatorOptions.FromConfiguration(configuration);

        Assert.True(options.Enabled);
        Assert.Equal(["hostname", "Get-Date", "ipconfig /all"], options.TerminalAllowlist);
        // M23: the Projects root is an authorised root whatever the owner listed — appended,
        // never replacing an entry — so the family has a place to write and the operator can
        // open what it wrote. M25 appends the 3D root beneath it for the same reason, and M28
        // the native root beside that one.
        Assert.Equal(
            [
                @"D:\Owner",
                @"E:\Projects",
                OperatorOptions.DefaultProjectsRoot()!,
                OperatorOptions.Default3dRoot(OperatorOptions.DefaultProjectsRoot())!,
                OperatorOptions.DefaultNativeRoot(OperatorOptions.DefaultProjectsRoot())!,
            ],
            options.AuthorisedRoots);
        Assert.Equal(OperatorOptions.DefaultProjectsRoot(), options.EffectiveProjectsRoot);
        Assert.EndsWith(@"\Documents\PagentOS Projects", options.EffectiveProjectsRoot, StringComparison.OrdinalIgnoreCase);
        Assert.EndsWith(@"\Documents\PagentOS Projects\native", options.EffectiveProjectsRootNative, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public async Task With_operator_disabled_the_service_refuses_before_the_pipe()
    {
        var transport = new CountingTransport();
        var executor = new InteractiveCapabilityExecutor(transport, operatorEnabled: false);

        foreach (var name in AgentCapabilities.Operator)
        {
            var ex = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(TestCommands.New(name, new JsonObject()), CancellationToken.None));
            Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);
            Assert.False(ex.Retryable);
            Assert.Contains("OperatorEnabled=false", ex.Message, StringComparison.Ordinal);
        }

        Assert.Equal(0, transport.Calls);

        var enabled = new InteractiveCapabilityExecutor(transport, operatorEnabled: true);
        await enabled.ExecuteAsync(TestCommands.New(OperatorCapabilityNames.WindowCurrent, new JsonObject()), CancellationToken.None);
        Assert.Equal(1, transport.Calls);
        Assert.Equal(OperatorCapabilityNames.WindowCurrent, transport.LastCapability);
        Assert.True(transport.LastTimeout <= TimeSpan.FromSeconds(30));
    }

    [Fact]
    public void The_three_operator_error_classes_are_in_the_device_taxonomy_and_the_shared_schema_agrees()
    {
        Assert.Equal("focus_mismatch", ErrorClasses.FocusMismatch);
        Assert.Equal("permission_denied", ErrorClasses.PermissionDenied);
        Assert.Equal("postcondition_failed", ErrorClasses.PostconditionFailed);
        Assert.Contains(ErrorClasses.FocusMismatch, ErrorClasses.All);
        Assert.Contains(ErrorClasses.PermissionDenied, ErrorClasses.All);
        Assert.Contains(ErrorClasses.PostconditionFailed, ErrorClasses.All);

        // MessageValidator refuses an error object whose class is not in All; the broker
        // refuses one that is not in the schema. The two lists must be the same list.
        var repoRoot = new DirectoryInfo(CompanionSources.Directory()).Parent!.Parent!.Parent!.Parent!.FullName;
        var schemaPath = Path.Combine(repoRoot, "packages", "schemas", "device-protocol.schema.json");
        using var schema = JsonDocument.Parse(File.ReadAllText(schemaPath));
        var enumerated = schema.RootElement.GetProperty("$defs").GetProperty("errorObject").GetProperty("properties").GetProperty("class").GetProperty("enum")
            .EnumerateArray().Select(e => e.GetString()!).ToHashSet(StringComparer.Ordinal);
        Assert.Equal(enumerated, ErrorClasses.All.ToHashSet(StringComparer.Ordinal));

        MessageValidator.ValidateErrorObject(ErrorObjects.Create(ErrorClasses.FocusMismatch, "x", retryable: true));
    }

    // ------------------------------------------------------------- companion over the real pipe

    private static CompanionRuntime NewCompanion(string pipeName, OperatorCapabilities? operatorCapabilities)
        => new(
            pipeName,
            new AppLauncher(AppLauncher.DefaultAllowlist()),
            new ArtifactOpener(new[] { Path.GetTempPath() }, new NoOpFileOpener()),
            NullLogger.Instance,
            new BackoffPolicy(baseSeconds: 0.05, maxSeconds: 0.2),
            ServiceAdmissionPolicy.DeveloperMode(IpcTestSupport.CurrentSid()),
            operatorCapabilities: operatorCapabilities);

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
    public async Task A_companion_built_without_the_operator_does_not_advertise_it_and_answers_capability_missing()
    {
        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        using var companionCts = new CancellationTokenSource();
        var companion = NewCompanion(pipeName, operatorCapabilities: null);
        var companionTask = Task.Run(() => companion.RunAsync(companionCts.Token));
        try
        {
            await WaitForCompanionAsync(server);
            Assert.DoesNotContain(server.CompanionCapabilities!, AgentCapabilities.IsOperator);

            var ex = await Assert.ThrowsAsync<CapabilityException>(() => server.ExecuteCapabilityAsync(
                OperatorCapabilityNames.WindowCurrent, new JsonObject(), TimeSpan.FromSeconds(10), CancellationToken.None));
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
    public async Task A_companion_built_with_the_operator_switched_off_still_answers_capability_missing()
    {
        // OperatorEnabled=false on the companion with an object present: the flag, not the
        // object, is the gate.
        using var lab = new OperatorLab(enabled: false);
        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        using var companionCts = new CancellationTokenSource();
        var companion = NewCompanion(pipeName, lab.Operator);
        var companionTask = Task.Run(() => companion.RunAsync(companionCts.Token));
        try
        {
            await WaitForCompanionAsync(server);
            Assert.DoesNotContain(server.CompanionCapabilities!, AgentCapabilities.IsOperator);
            var ex = await Assert.ThrowsAsync<CapabilityException>(() => server.ExecuteCapabilityAsync(
                OperatorCapabilityNames.AppList, new JsonObject(), TimeSpan.FromSeconds(10), CancellationToken.None));
            Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);
        }
        finally
        {
            companionCts.Cancel();
            try { await companionTask.WaitAsync(TimeSpan.FromSeconds(5)); } catch (Exception) { }
            await server.StopAsync(CancellationToken.None);
        }
    }

    [LabFact]
    public async Task The_whole_chain_service_pipe_companion_operator_answers_window_current_and_a_typed_refusal()
    {
        using var lab = new OperatorLab();
        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        using var companionCts = new CancellationTokenSource();
        var companion = NewCompanion(pipeName, lab.Operator);
        var companionTask = Task.Run(() => companion.RunAsync(companionCts.Token));
        try
        {
            await WaitForCompanionAsync(server);
            Assert.Equal(AgentCapabilities.Compose(browserEnabled: false, displayPowerEnabled: false, operatorEnabled: true), server.CompanionCapabilities);

            var executor = new InteractiveCapabilityExecutor(server, operatorEnabled: true);
            var current = await executor.ExecuteAsync(TestCommands.New(OperatorCapabilityNames.WindowCurrent, new JsonObject(), expiresIn: TimeSpan.FromMinutes(5)), CancellationToken.None);
            Assert.NotNull(current);
            Assert.True(current!.ContainsKey("observed"));

            // A typed refusal crosses the pipe as itself: an id the companion never issued.
            var refused = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
                TestCommands.New(OperatorCapabilityNames.KeyboardType, new JsonObject { ["window_id"] = "w-1-1", ["text"] = "x" }), CancellationToken.None));
            Assert.Equal(ErrorClasses.UiTargetNotFound, refused.ErrorClass);

            var secret = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
                TestCommands.New(OperatorCapabilityNames.KeyboardType, new JsonObject { ["window_id"] = "w-1-1", ["text"] = "x", ["secret"] = true }), CancellationToken.None));
            Assert.Equal(ErrorClasses.ValidationError, secret.ErrorClass);
            Assert.Equal("secrets are never typed", secret.Message);
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

        public string? LastCapability { get; private set; }

        public TimeSpan LastTimeout { get; private set; }

        public Task<JsonObject?> ExecuteCapabilityAsync(string capability, JsonObject payload, TimeSpan timeout, CancellationToken cancellationToken)
        {
            Calls++;
            LastCapability = capability;
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
