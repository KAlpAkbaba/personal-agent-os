using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Native;

/// <summary>
/// M28 row 26.16 — the manifest the Cloud Core sends with <c>project.scaffold</c>, parsed by
/// THIS device's real parser.
///
/// The first real production build died at the first device step: the Cloud Core wrote
/// <c>"test": "dotnet test ..."</c> (a string) and this device requires
/// <c>manifest.test</c> to be an object of {key: command}. Both suites were green — each half
/// had only ever restated the shape to itself. So the Cloud Core now writes one canonical
/// manifest into <c>packages/protocol/native-manifest.example.json</c> (its own test keeps
/// that file equal to what it sends), and this test scaffolds THAT file, unedited, through
/// the real ProjectScaffold. A shape only one half admits fails here.
/// </summary>
[Collection(NativeLabCollection.Name)]
public sealed class NativeManifestContractTests
{
    private static string ExamplePath()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "packages", "protocol", "native-manifest.example.json")))
        {
            dir = dir.Parent;
        }

        Assert.NotNull(dir);
        return Path.Combine(dir!.FullName, "packages", "protocol", "native-manifest.example.json");
    }

    [Fact]
    public void The_manifest_the_cloud_core_sends_for_a_native_build_is_one_this_device_admits()
    {
        var document = (JsonObject)JsonNode.Parse(File.ReadAllText(ExamplePath()))!;
        var csproj = document["csproj"]!.GetValue<string>();
        var manifest = (JsonObject)document["manifest"]!.DeepClone();

        using var lab = new NativeLab();
        var files = new JsonArray
        {
            new JsonObject
            {
                ["path"] = csproj,
                ["text"] = "<Project Sdk=\"Microsoft.NET.Sdk\"><PropertyGroup><OutputType>WinExe</OutputType></PropertyGroup></Project>",
            },
        };
        var payload = new JsonObject
        {
            ["project_id"] = "proj-manifest-contract",
            ["slug"] = "notlarim-contract",
            ["root"] = NativeCapabilityNames.RootNativeFolderName,
            ["files"] = files,
            ["manifest"] = manifest,
        };

        var result = lab.Exec(ProjectCapabilityNames.ProjectScaffold, payload);

        Assert.Equal(NativeCapabilityNames.RootNativeFolderName, result["root"]!.GetValue<string>());
        // The parser kept every command the Cloud Core promised, under the keys it used.
        var accepted = (JsonObject)result["manifest"]!;
        Assert.Equal(
            ((JsonObject)document["manifest"]!["run"]!)["build"]!.GetValue<string>(),
            ((JsonObject)accepted["run"]!)["build"]!.GetValue<string>());
        Assert.Equal(
            ((JsonObject)document["manifest"]!["test"]!)["unit"]!.GetValue<string>(),
            ((JsonObject)accepted["test"]!)["unit"]!.GetValue<string>());
    }

    [Fact]
    public void A_test_section_that_is_a_bare_string_is_refused_the_way_production_saw_it()
    {
        var document = (JsonObject)JsonNode.Parse(File.ReadAllText(ExamplePath()))!;
        var csproj = document["csproj"]!.GetValue<string>();
        var manifest = (JsonObject)document["manifest"]!.DeepClone();
        manifest["test"] = "dotnet test " + csproj + " -c Release";

        using var lab = new NativeLab();
        var files = new JsonArray
        {
            new JsonObject { ["path"] = csproj, ["text"] = "<Project Sdk=\"Microsoft.NET.Sdk\" />" },
        };
        var payload = new JsonObject
        {
            ["project_id"] = "proj-manifest-contract-bad",
            ["slug"] = "notlarim-contract-bad",
            ["root"] = NativeCapabilityNames.RootNativeFolderName,
            ["files"] = files,
            ["manifest"] = manifest,
        };

        var failure = lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, payload);

        Assert.Contains("manifest.test must be a non-empty object", failure.Message);
    }
}
