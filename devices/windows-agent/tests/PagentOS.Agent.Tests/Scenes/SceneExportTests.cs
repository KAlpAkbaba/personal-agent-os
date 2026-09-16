using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.SessionCompanion.Projects;
using PagentOS.SessionCompanion.Scenes;
using Xunit;

namespace PagentOS.Agent.Tests.Scenes;

/// <summary>
/// B44 (req 527) — exported scene files verified IN PLACE by <c>scene.inspect</c>, and the
/// Cloud Core's own shipped Blender driver run through the real job and the real checks.
///
/// The second half exists because of what B44 measured: the device lab drove its OWN lab
/// driver (<see cref="SceneLab.DriverScript"/>), which declares a relative render path, while
/// the driver the Cloud Core actually ships declared an absolute one — which this device
/// refuses — and nothing ran the two together. The test below reads the shipped file from the
/// repository, so a drift on either side is a red test here, not a production mystery.
/// </summary>
[Collection(SceneLabCollection.Name)]
public sealed class SceneExportTests
{
    private static string RepoRoot()
        => new DirectoryInfo(CompanionSources.Directory()).Parent!.Parent!.Parent!.Parent!.FullName;

    private static byte[] Glb(uint version = 2, int lengthDelta = 0)
    {
        var body = Encoding.UTF8.GetBytes("{\"asset\":{\"version\":\"2.0\"}}");
        var padded = new List<byte>(body);
        while (padded.Count % 4 != 0)
        {
            padded.Add((byte)' ');
        }

        var total = 12 + 8 + padded.Count;
        var data = new List<byte>();
        data.AddRange("glTF"u8.ToArray());
        data.AddRange(BitConverter.GetBytes(version));
        data.AddRange(BitConverter.GetBytes((uint)(total + lengthDelta)));
        data.AddRange(BitConverter.GetBytes((uint)padded.Count));
        data.AddRange("JSON"u8.ToArray());
        data.AddRange(padded);
        return [.. data];
    }

    private static JsonObject Declare(string format, string path, byte[] content) => new()
    {
        ["format"] = format,
        ["path"] = path,
        ["bytes"] = content.LongLength,
        ["sha256"] = Convert.ToHexStringLower(SHA256.HashData(content)),
    };

    private static void WriteInspection(string folder, params JsonObject[] exports)
    {
        var list = new JsonArray();
        foreach (var export in exports)
        {
            list.Add(export);
        }

        File.WriteAllText(
            Path.Combine(folder, SceneCapabilityNames.InspectionFileName),
            new JsonObject { ["tool"] = "blender", ["objects"] = new JsonArray(), ["exports"] = list }.ToJsonString());
    }

    [Fact]
    public void A_declared_export_is_verified_in_place_and_its_proof_travels_without_its_bytes()
    {
        using var lab = new SceneLab();
        var folder = lab.Scaffold3d("ex-1", "disa", SceneLab.BlenderCommand());
        var glb = Glb();
        File.WriteAllBytes(Path.Combine(folder, "scene.glb"), glb);
        WriteInspection(folder, Declare("glb", "scene.glb", glb));

        var inspected = lab.Exec(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "ex-1" });
        var exports = (JsonArray)inspected["exports"]!;
        var export = (JsonObject)Assert.Single(exports)!;
        Assert.Equal("glb", export["format"]!.GetValue<string>());
        Assert.Equal("scene.glb", export["path"]!.GetValue<string>());
        Assert.Equal(glb.LongLength, export["bytes"]!.GetValue<long>());
        Assert.Equal(Convert.ToHexStringLower(SHA256.HashData(glb)), export["sha256"]!.GetValue<string>());
        Assert.True(export["verified"]!.GetValue<bool>());
        // The proof, never the file: no key of the export carries its bytes.
        Assert.DoesNotContain(export.Select(pair => pair.Key), key => key.Contains("base64", StringComparison.Ordinal));
        Assert.Null(inspected["render"]);

        // Nothing declared is an empty list, not a failure.
        WriteInspection(folder);
        Assert.Empty((JsonArray)lab.Exec(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "ex-1" })["exports"]!);
    }

    [Fact]
    public void An_export_that_is_not_what_the_driver_declared_is_refused_by_name()
    {
        using var lab = new SceneLab();
        var folder = lab.Scaffold3d("ex-2", "bozuk", SceneLab.BlenderCommand());
        var inspect = new JsonObject { ["project_id"] = "ex-2" };
        string DetailOf(CapabilityException failure) => (string)failure.Detail["detail"]!;

        // Declared but not there.
        WriteInspection(folder, Declare("glb", "scene.glb", Glb()));
        Assert.Equal("export_missing", DetailOf(lab.ExpectFailure(SceneCapabilityNames.Inspect, inspect)));

        // Bytes that no longer hash to the driver's sha256.
        var glb = Glb();
        File.WriteAllBytes(Path.Combine(folder, "scene.glb"), glb);
        var flipped = (byte[])glb.Clone();
        flipped[^1] ^= 0xFF;
        WriteInspection(folder, Declare("glb", "scene.glb", flipped));
        var mismatch = lab.ExpectFailure(SceneCapabilityNames.Inspect, inspect);
        Assert.Equal(ErrorClasses.PostconditionFailed, mismatch.ErrorClass);
        Assert.Equal("export_sha256_mismatch", DetailOf(mismatch));

        // A file that hashes correctly but is not a GLB: version 1, and a header lying about its length.
        foreach (var liar in new[] { Glb(version: 1), Glb(lengthDelta: 4) })
        {
            File.WriteAllBytes(Path.Combine(folder, "scene.glb"), liar);
            WriteInspection(folder, Declare("glb", "scene.glb", liar));
            Assert.Equal("export_signature_mismatch", DetailOf(lab.ExpectFailure(SceneCapabilityNames.Inspect, inspect)));
        }

        // An FBX declaration over GLB bytes.
        File.WriteAllBytes(Path.Combine(folder, "scene.fbx"), glb);
        WriteInspection(folder, Declare("fbx", "scene.fbx", glb));
        Assert.Equal("export_signature_mismatch", DetailOf(lab.ExpectFailure(SceneCapabilityNames.Inspect, inspect)));

        // An unknown format, and more exports than the bound.
        WriteInspection(folder, Declare("obj", "scene.glb", glb));
        Assert.Equal("export_undeclared", DetailOf(lab.ExpectFailure(SceneCapabilityNames.Inspect, inspect)));
        WriteInspection(folder, Declare("glb", "scene.glb", glb), Declare("glb", "scene.glb", glb), Declare("glb", "scene.glb", glb));
        Assert.Equal("exports_bound", DetailOf(lab.ExpectFailure(SceneCapabilityNames.Inspect, inspect)));

        // A path that points outside the project is refused before anything is opened.
        WriteInspection(folder, Declare("glb", "../../escape.glb", glb));
        Assert.Equal(ErrorClasses.PermissionDenied, lab.ExpectFailure(SceneCapabilityNames.Inspect, inspect).ErrorClass);

        // And an ABSOLUTE render path — what the shipped driver used to write — is refused too.
        var png = Path.Combine(folder, "render.png");
        File.WriteAllBytes(png, [0x89, (byte)'P', (byte)'N', (byte)'G', 1, 2, 3, 4]);
        File.WriteAllText(
            Path.Combine(folder, SceneCapabilityNames.InspectionFileName),
            new JsonObject
            {
                ["tool"] = "blender",
                ["render"] = new JsonObject
                {
                    ["path"] = png,
                    ["bytes"] = 8,
                    ["sha256"] = Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(png))),
                },
            }.ToJsonString());
        Assert.Equal(ErrorClasses.PermissionDenied, lab.ExpectFailure(SceneCapabilityNames.Inspect, inspect).ErrorClass);
    }

    [Fact]
    public void The_signature_check_reads_the_formats_own_first_bytes()
    {
        var glb = Glb();
        Assert.True(SceneInspection.SignatureOk("glb", glb, glb.LongLength));
        Assert.False(SceneInspection.SignatureOk("glb", glb, glb.LongLength + 1));
        Assert.False(SceneInspection.SignatureOk("glb", Glb(version: 1), glb.LongLength));
        var fbx = "Kaydara FBX Binary  \0"u8.ToArray().Concat(new byte[64]).ToArray();
        Assert.True(SceneInspection.SignatureOk("fbx", fbx, fbx.LongLength));
        Assert.False(SceneInspection.SignatureOk("fbx", glb, glb.LongLength));
        Assert.False(SceneInspection.SignatureOk("obj", glb, glb.LongLength));
    }

    [SceneLabFact("blender")]
    public void The_shipped_cloud_driver_renders_animates_and_exports_through_the_job_and_every_path_passes_the_device()
    {
        var driverPath = Path.Combine(RepoRoot(), "services", "api", "app", "creative3d", "drivers", "blender_driver.py");
        Assert.True(File.Exists(driverPath), $"the shipped driver is not at {driverPath}; the two halves must be re-reconciled by hand");
        const string DriverName = "blender_driver.py";

        var plan = new JsonObject
        {
            ["tool"] = "blender",
            ["project"] = "lab",
            ["scene"] = "bulut",
            ["operations"] = new JsonArray
            {
                new JsonObject { ["op"] = "create_scene" },
                new JsonObject { ["op"] = "add_primitive", ["kind"] = "sphere", ["name"] = "Kure", ["location"] = new JsonArray(0.0, 0.0, 0.0) },
                new JsonObject { ["op"] = "add_primitive", ["kind"] = "camera", ["name"] = "Kamera", ["location"] = new JsonArray(0.0, -6.0, 3.0) },
                new JsonObject { ["op"] = "add_primitive", ["kind"] = "light_sun", ["name"] = "Gunes", ["location"] = new JsonArray(2.0, -2.0, 5.0) },
                new JsonObject { ["op"] = "set_camera", ["name"] = "Kamera", ["look_at"] = "Kure", ["lens"] = 35.0 },
                new JsonObject { ["op"] = "set_light", ["name"] = "Gunes", ["energy"] = 3.0, ["color"] = new JsonArray(1.0, 0.5, 0.25) },
                new JsonObject { ["op"] = "set_frames", ["start"] = 1, ["end"] = 24, ["fps"] = 24 },
                new JsonObject
                {
                    ["op"] = "animate",
                    ["name"] = "Kure",
                    ["channel"] = "location",
                    ["keyframes"] = new JsonArray
                    {
                        new JsonObject { ["frame"] = 1, ["value"] = new JsonArray(0.0, 0.0, 0.0) },
                        new JsonObject { ["frame"] = 24, ["value"] = new JsonArray(0.0, 0.0, 2.0) },
                    },
                },
                new JsonObject { ["op"] = "render", ["width"] = 160, ["height"] = 120, ["engine"] = "workbench" },
                new JsonObject { ["op"] = "export", ["format"] = "glb" },
                new JsonObject { ["op"] = "inspect" },
            },
        };

        using var lab = new SceneLab();
        // The Cloud Core's FIRST-run form: no scene file to open yet, the driver saves scene.blend.
        var folder = lab.Scaffold3d(
            "ex-bl",
            "bulut",
            SceneLab.BlenderCommand(driver: DriverName, scene: null),
            files: [(DriverName, File.ReadAllText(driverPath)), (SceneLab.PlanFileName, plan.ToJsonString())]);

        var run = lab.Exec(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "ex-bl" }, budgetSeconds: 180);
        Assert.Equal(0, run["exit_code"]!.GetValue<int>());
        Assert.True(File.Exists(Path.Combine(folder, SceneLab.SceneFileName)), "the shipped driver did not save scene.blend");

        var inspected = lab.Exec(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "ex-bl" });
        var inspection = (JsonObject)inspected["inspection"]!;
        Assert.Empty((JsonArray)inspection["errors"]!);

        // The render passed the device's checks — which the old absolute path never could.
        var render = (JsonObject)inspected["render"]!;
        Assert.Equal("render.png", render["path"]!.GetValue<string>());
        Assert.True(render["verified"]!.GetValue<bool>());
        var png = Convert.FromBase64String(render["png_base64"]!.GetValue<string>());
        Assert.Equal(render["sha256"]!.GetValue<string>(), Convert.ToHexStringLower(SHA256.HashData(png)));

        // The GLB Blender's own exporter wrote, verified in place.
        var export = (JsonObject)Assert.Single((JsonArray)inspected["exports"]!)!;
        Assert.Equal("glb", export["format"]!.GetValue<string>());
        Assert.Equal("scene.glb", export["path"]!.GetValue<string>());
        Assert.True(export["verified"]!.GetValue<bool>());
        var glbBytes = File.ReadAllBytes(Path.Combine(folder, "scene.glb"));
        Assert.True(SceneInspection.SignatureOk("glb", glbBytes, glbBytes.LongLength));

        // The keyframes Blender holds, the frame range, the lens and the light colour — read back.
        var track = ((JsonArray)inspection["animation"]!).Single(t => t!["object"]!.GetValue<string>() == "Kure" && t["channel"]!.GetValue<string>() == "location")!;
        Assert.Equal([1, 24], ((JsonArray)track["frames"]!).Select(f => f!.GetValue<int>()).ToArray());
        Assert.Equal(2.0, ((JsonArray)((JsonArray)track["values"]!)[1]!)[2]!.GetValue<double>(), 3);
        var frames = (JsonObject)inspection["frames"]!;
        Assert.Equal(24, frames["end"]!.GetValue<int>());
        var camera = ((JsonArray)inspection["objects"]!).Single(o => o!["name"]!.GetValue<string>() == "Kamera")!;
        Assert.Equal(35.0, camera["lens"]!.GetValue<double>(), 3);
        var sun = ((JsonArray)inspection["lights"]!).Single(l => l!["name"]!.GetValue<string>() == "Gunes")!;
        Assert.Equal(0.25, ((JsonArray)sun["color"]!)[2]!.GetValue<double>(), 3);
    }
}
