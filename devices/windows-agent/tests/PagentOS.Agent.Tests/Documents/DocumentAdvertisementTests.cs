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
using PagentOS.SessionCompanion.Documents;
using Xunit;

namespace PagentOS.Agent.Tests.Documents;

/// <summary>
/// The family's place in the manifest and the routing (M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md
/// §2, ADR-0083 decision 1): six names after the operator family — seven since M22 appended
/// <c>file.fetch</c> (ADR-0085) — advertised only behind <c>OperatorEnabled</c>
/// (<c>Compose(operatorEnabled: true)</c> lists them, <c>false</c> lists none), interactive on
/// every member, capped at 30 s, refused by the service before the pipe when off, answered by
/// the companion over the real pipe when on; the two new error classes in the taxonomy, the
/// schema and (by the broker test) <c>frames.py</c>; the software version.
/// </summary>
public sealed class DocumentAdvertisementTests
{
    [Fact]
    public void Compose_lists_the_seven_names_after_the_operator_family_only_when_operator_is_enabled()
    {
        Assert.Equal(["file.search", "file.locate", "file.inspect", "file.read", "file.compare", "document.extract", "file.fetch"], AgentCapabilities.Documents);
        Assert.Equal(7, AgentCapabilities.Documents.Count);
        Assert.Equal(32, AgentCapabilities.Operator.Count);

        var without = AgentCapabilities.Compose(browserEnabled: true, displayPowerEnabled: true, operatorEnabled: false);
        Assert.DoesNotContain(without, AgentCapabilities.IsDocuments);
        Assert.DoesNotContain(without, AgentCapabilities.IsOperator);

        // M23 appends the projects family (5) after the documents family under the same flag;
        // the documents family itself is exactly where it was, right after the operator's 32.
        var with = AgentCapabilities.Compose(browserEnabled: true, displayPowerEnabled: true, operatorEnabled: true);
        var projects = AgentCapabilities.Projects.Count;
        Assert.Equal(AgentCapabilities.Documents, with.SkipLast(projects).TakeLast(AgentCapabilities.Documents.Count));
        Assert.Equal(AgentCapabilities.Operator, with.SkipLast(AgentCapabilities.Documents.Count + projects).TakeLast(AgentCapabilities.Operator.Count));
        Assert.Equal(with.Count, with.Distinct(StringComparer.Ordinal).Count());
        Assert.Equal(without.Count + 32 + 7 + 5, with.Count);

        // The deployed 0.1.0 / 0.2.0 baseline — no operator — is untouched by M20, M22 and M23.
        Assert.Equal(AgentCapabilities.Compose(browserEnabled: false), AgentCapabilities.Compose(browserEnabled: false, displayPowerEnabled: false, operatorEnabled: false));
        Assert.Equal(AgentCapabilities.Desktop, AgentCapabilities.All);
        Assert.Equal("0.5.0", AgentInfo.SoftwareVersion);
    }

    [Fact]
    public void Every_member_is_interactive_a_documents_name_and_never_an_operator_browser_or_desktop_name()
    {
        foreach (var name in AgentCapabilities.Documents)
        {
            Assert.True(AgentCapabilities.IsInteractive(name), $"{name} must route to the companion");
            Assert.True(AgentCapabilities.IsDocuments(name));
            Assert.True(DocumentCapabilityNames.IsMember(name));
            Assert.False(AgentCapabilities.IsOperator(name));
            Assert.False(AgentCapabilities.IsBrowser(name));
            Assert.False(AgentCapabilities.IsDesktop(name));
            Assert.Matches("^[a-z][a-z0-9_.]{1,63}$", name);
        }

        Assert.False(AgentCapabilities.IsDocuments("file.open"));
        Assert.False(AgentCapabilities.IsDocuments("file.delete"));
        Assert.False(AgentCapabilities.IsDocuments("document.write"));
        Assert.False(AgentCapabilities.IsInteractive("file.delete"));
        Assert.True(AgentCapabilities.IsOperator("file.open"));
    }

    [Fact]
    public async Task The_service_caps_the_family_at_30s_and_refuses_it_before_the_pipe_when_operator_is_off()
    {
        foreach (var name in AgentCapabilities.Documents)
        {
            Assert.Equal(TimeSpan.FromSeconds(30), InteractiveCapabilityExecutor.TimeoutCapFor(name));
        }

        var transport = new CountingTransport();
        var executor = new InteractiveCapabilityExecutor(transport, operatorEnabled: false);
        foreach (var name in AgentCapabilities.Documents)
        {
            var ex = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(TestCommands.New(name, new JsonObject()), CancellationToken.None));
            Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);
            Assert.False(ex.Retryable);
            Assert.Contains("OperatorEnabled=false", ex.Message, StringComparison.Ordinal);
        }

        Assert.Equal(0, transport.Calls);

        var enabled = new InteractiveCapabilityExecutor(transport, operatorEnabled: true);
        await enabled.ExecuteAsync(TestCommands.New(DocumentCapabilityNames.FileLocate, new JsonObject(), expiresIn: TimeSpan.FromMinutes(10)), CancellationToken.None);
        Assert.Equal(1, transport.Calls);
        Assert.Equal(DocumentCapabilityNames.FileLocate, transport.LastCapability);
        Assert.Equal(TimeSpan.FromSeconds(30), transport.LastTimeout);

        var configuration = new ConfigurationBuilder().AddInMemoryCollection([new KeyValuePair<string, string?>("OperatorEnabled", "true")]).Build();
        var options = AgentServiceOptions.FromConfiguration(configuration);
        Assert.All(AgentCapabilities.Documents, name => Assert.Contains(name, options.AdvertisedCapabilities));
        var off = AgentServiceOptions.FromConfiguration(new ConfigurationBuilder().Build());
        Assert.DoesNotContain(off.AdvertisedCapabilities, AgentCapabilities.IsDocuments);
    }

    [Fact]
    public void The_two_error_classes_are_in_the_device_taxonomy_and_the_shared_schema_agrees()
    {
        Assert.Equal("unsupported_format", ErrorClasses.UnsupportedFormat);
        Assert.Equal("not_found", ErrorClasses.NotFound);
        Assert.Contains(ErrorClasses.UnsupportedFormat, ErrorClasses.All);
        Assert.Contains(ErrorClasses.NotFound, ErrorClasses.All);

        var repoRoot = new DirectoryInfo(CompanionSources.Directory()).Parent!.Parent!.Parent!.Parent!.FullName;
        var schemaPath = Path.Combine(repoRoot, "packages", "schemas", "device-protocol.schema.json");
        using var schema = JsonDocument.Parse(File.ReadAllText(schemaPath));
        var enumerated = schema.RootElement.GetProperty("$defs").GetProperty("errorObject").GetProperty("properties").GetProperty("class").GetProperty("enum")
            .EnumerateArray().Select(e => e.GetString()!).ToHashSet(StringComparer.Ordinal);
        Assert.Contains("unsupported_format", enumerated);
        Assert.Contains("not_found", enumerated);
        Assert.Equal(enumerated, ErrorClasses.All.ToHashSet(StringComparer.Ordinal));

        // The broker's mirror carries both too (its own test validates the tuple; this reads the source).
        var frames = File.ReadAllText(Path.Combine(repoRoot, "services", "api", "app", "broker", "frames.py"));
        Assert.Contains("\"unsupported_format\",", frames, StringComparison.Ordinal);
        Assert.Contains("\"not_found\",", frames, StringComparison.Ordinal);

        MessageValidator.ValidateErrorObject(ErrorObjects.Create(ErrorClasses.UnsupportedFormat, "x", retryable: false));
        MessageValidator.ValidateErrorObject(ErrorObjects.Create(ErrorClasses.NotFound, "x", retryable: false));
    }

    [Fact]
    public void The_documents_dispatcher_refuses_a_name_outside_its_family_and_answers_nothing_when_disabled()
    {
        using var lab = new DocumentLab(enabled: false);
        Assert.False(lab.Documents.Enabled);
        var operatorName = lab.ExpectFailure(OperatorCapabilityNames.WindowCurrent, new JsonObject());
        Assert.Equal(ErrorClasses.CapabilityMissing, operatorName.ErrorClass);
        var made = lab.ExpectFailure("file.delete", new JsonObject { ["path"] = lab.PathOf("notlar.md") });
        Assert.Equal(ErrorClasses.CapabilityMissing, made.ErrorClass);
        Assert.True(File.Exists(lab.PathOf("notlar.md")));
    }

    // ------------------------------------------------------------- companion over the real pipe

    private static CompanionRuntime NewCompanion(string pipeName, DocumentCapabilities? documents)
        => new(
            pipeName,
            new AppLauncher(AppLauncher.DefaultAllowlist()),
            new ArtifactOpener(new[] { Path.GetTempPath() }, new NoOpFileOpener()),
            NullLogger.Instance,
            new BackoffPolicy(baseSeconds: 0.05, maxSeconds: 0.2),
            ServiceAdmissionPolicy.DeveloperMode(IpcTestSupport.CurrentSid()),
            documentCapabilities: documents);

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
    public async Task A_companion_built_without_the_documents_object_does_not_advertise_the_family_and_answers_capability_missing()
    {
        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        using var companionCts = new CancellationTokenSource();
        var companion = NewCompanion(pipeName, documents: null);
        var companionTask = Task.Run(() => companion.RunAsync(companionCts.Token));
        try
        {
            await WaitForCompanionAsync(server);
            Assert.DoesNotContain(server.CompanionCapabilities!, AgentCapabilities.IsDocuments);
            var ex = await Assert.ThrowsAsync<CapabilityException>(() => server.ExecuteCapabilityAsync(
                DocumentCapabilityNames.FileLocate, new JsonObject { ["path"] = @"C:\x.txt" }, TimeSpan.FromSeconds(10), CancellationToken.None));
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
    public async Task The_whole_chain_service_pipe_companion_documents_answers_locate_extract_and_a_typed_refusal()
    {
        using var lab = new DocumentLab();
        var pipeName = IpcTestSupport.NewPipeName();
        var server = IpcTestSupport.NewServer(pipeName);
        await server.StartAsync(CancellationToken.None);
        using var companionCts = new CancellationTokenSource();
        var companion = NewCompanion(pipeName, lab.Documents);
        var companionTask = Task.Run(() => companion.RunAsync(companionCts.Token));
        try
        {
            await WaitForCompanionAsync(server);
            Assert.Equal(AgentCapabilities.Compose(browserEnabled: false, displayPowerEnabled: false, operatorEnabled: true), server.CompanionCapabilities);

            var executor = new InteractiveCapabilityExecutor(server, operatorEnabled: true);
            var located = await executor.ExecuteAsync(TestCommands.New(DocumentCapabilityNames.FileLocate, new JsonObject { ["path"] = lab.PathOf("veri.csv") }, expiresIn: TimeSpan.FromMinutes(5)), CancellationToken.None);
            Assert.Equal("veri.csv", located!["file"]!["name"]!.GetValue<string>());
            Assert.Equal(DocumentLab.TruthFor("veri.csv")["sha256"]!.GetValue<string>(), located["file"]!["sha256"]!.GetValue<string>());

            var extracted = await executor.ExecuteAsync(TestCommands.New(DocumentCapabilityNames.DocumentExtract, new JsonObject { ["file_id"] = located["file"]!["file_id"]!.GetValue<string>() }, expiresIn: TimeSpan.FromMinutes(5)), CancellationToken.None);
            Assert.Equal("Kerem,İzmir,45,1320,aktif", extracted!["blocks"]!.AsArray().First(b => b!["ref"]!.GetValue<string>() == "r7")!["text"]!.GetValue<string>());

            // A typed refusal crosses the pipe as itself.
            var refused = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
                TestCommands.New(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = @"C:\Windows\System32\drivers\etc\hosts" }), CancellationToken.None));
            Assert.Equal(ErrorClasses.PermissionDenied, refused.ErrorClass);
            var unsupported = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
                TestCommands.New(DocumentCapabilityNames.FileRead, new JsonObject { ["path"] = lab.PathOf("rapor.pdf") }), CancellationToken.None));
            Assert.Equal(ErrorClasses.UnsupportedFormat, unsupported.ErrorClass);
            var notFound = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
                TestCommands.New(DocumentCapabilityNames.FileLocate, new JsonObject { ["file_id"] = "file:ffffffffffffffffffffffffffffffff" }), CancellationToken.None));
            Assert.Equal(ErrorClasses.NotFound, notFound.ErrorClass);
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
