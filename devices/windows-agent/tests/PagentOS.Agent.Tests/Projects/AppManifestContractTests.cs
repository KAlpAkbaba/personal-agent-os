using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Projects;

/// <summary>
/// B03 req 4/417-421: the manifest the App Factory sends, parsed by THIS device's real parser.
/// </summary>
/// <remarks>
/// Every app the factory ever produced was refused here at the first parse, and both halves'
/// suites were green while it happened:
/// <list type="bullet">
/// <item>two templates wrote <c>{port}</c>; this device's placeholder is <c>&lt;port&gt;</c>
/// and it refuses <c>{</c> and <c>}</c> anywhere in a command;</item>
/// <item>the cli-tool template carried no <c>run</c> section, which this device requires;</item>
/// <item>and no <c>port</c> either - so a CLI tool, which binds nothing, had no truthful
/// manifest it could send at all. That last one was this device's gap as much as the Cloud
/// Core's: <c>port: 0</c> was already how a 3D batch run said "binds nothing" and a web
/// project could not say it.</item>
/// </list>
/// So the Cloud Core writes what it sends into
/// <c>packages/protocol/app-manifest.example.json</c> (its own test keeps that file equal to
/// what the templates produce) and these tests scaffold THOSE examples, unedited, through the
/// real <see cref="ProjectScaffold"/>. A shape only one half admits fails here.
/// </remarks>
[Collection(ProjectLabCollection.Name)]
public sealed class AppManifestContractTests
{
    private static JsonObject Examples()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null
               && !File.Exists(Path.Combine(directory.FullName, "packages", "protocol", "app-manifest.example.json")))
        {
            directory = directory.Parent;
        }

        Assert.NotNull(directory);
        var path = Path.Combine(directory!.FullName, "packages", "protocol", "app-manifest.example.json");
        return (JsonObject)((JsonObject)JsonNode.Parse(File.ReadAllText(path))!)["examples"]!;
    }

    private static JsonObject Payload(ProjectLab lab, string slug, JsonObject manifest, params string[] files)
    {
        var list = new JsonArray();
        foreach (var file in files)
        {
            list.Add(new JsonObject { ["path"] = file, ["text"] = "// fixture\n" });
        }

        return new JsonObject
        {
            ["project_id"] = $"proj-{slug}",
            ["slug"] = slug,
            ["files"] = list,
            ["manifest"] = manifest,
        };
    }

    [Fact]
    public void The_server_manifest_the_cloud_core_sends_is_one_this_device_admits()
    {
        var manifest = (JsonObject)Examples()["server"]!.DeepClone();
        using var lab = new ProjectLab();

        var result = lab.Exec(
            ProjectCapabilityNames.ProjectScaffold,
            Payload(lab, "tanitim", manifest, "index.html", "tests/run.js"));

        var accepted = (JsonObject)result["manifest"]!;
        Assert.Equal("index.html", accepted["entry"]!.GetValue<string>());
        Assert.Equal(8766, accepted["port"]!.GetValue<int>());
        // The command survives the parse naming its port one of the two ways this device
        // admits - the placeholder, or the number it stands for. What matters for the defect
        // is that neither spelling is a brace.
        var serve = ((JsonObject)accepted["run"]!)["serve"]!.GetValue<string>();
        Assert.Contains("http.server", serve, StringComparison.Ordinal);
        Assert.True(
            serve.Contains(ProjectManifest.PortPlaceholder, StringComparison.Ordinal)
            || serve.Contains("8766", StringComparison.Ordinal),
            serve);
        Assert.DoesNotContain("{", serve, StringComparison.Ordinal);
    }

    [Fact]
    public void The_manifest_of_a_project_that_binds_nothing_is_admitted_too()
    {
        var manifest = (JsonObject)Examples()["binds_nothing"]!.DeepClone();
        using var lab = new ProjectLab();

        var result = lab.Exec(
            ProjectCapabilityNames.ProjectScaffold,
            Payload(lab, "selamla", manifest, "cli.js", "tests/run.js"));

        var accepted = (JsonObject)result["manifest"]!;
        Assert.Equal("cli.js", accepted["entry"]!.GetValue<string>());
        Assert.Equal(ProjectManifest.NoPort, accepted["port"]!.GetValue<int>());
        Assert.Equal("node cli.js", ((JsonObject)accepted["run"]!)["start"]!.GetValue<string>());
    }

    [Fact]
    public void The_brace_placeholder_production_actually_sent_is_refused()
    {
        // The exact bytes that were on the wire until 2026-09-12.
        var manifest = (JsonObject)Examples()["server"]!.DeepClone();
        ((JsonObject)manifest["run"]!)["serve"] = "python -m http.server {port} --bind 127.0.0.1";
        using var lab = new ProjectLab();

        var failure = lab.ExpectFailure(
            ProjectCapabilityNames.ProjectScaffold,
            Payload(lab, "tanitim-bad", manifest, "index.html", "tests/run.js"));

        Assert.Contains("run", failure.Message, StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void A_manifest_with_no_run_section_is_refused_the_way_production_saw_it()
    {
        var manifest = (JsonObject)Examples()["binds_nothing"]!.DeepClone();
        manifest.Remove("run");
        using var lab = new ProjectLab();

        var failure = lab.ExpectFailure(
            ProjectCapabilityNames.ProjectScaffold,
            Payload(lab, "selamla-norun", manifest, "cli.js", "tests/run.js"));

        Assert.Contains("manifest.run is required", failure.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void A_web_manifest_with_no_port_at_all_is_still_refused()
    {
        // `port: 0` says "binds nothing" on purpose. Saying NOTHING is still an error: a
        // server that forgot its port would otherwise be run as a batch and answer with the
        // exit code of a process that was supposed to keep listening.
        var manifest = (JsonObject)Examples()["server"]!.DeepClone();
        manifest.Remove("port");
        using var lab = new ProjectLab();

        var failure = lab.ExpectFailure(
            ProjectCapabilityNames.ProjectScaffold,
            Payload(lab, "tanitim-noport", manifest, "index.html", "tests/run.js"));

        Assert.Contains("manifest.port is required", failure.Message, StringComparison.Ordinal);
    }
}
