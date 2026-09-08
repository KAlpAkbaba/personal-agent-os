using System.Security.Cryptography;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Scenes;

/// <summary>
/// M25_CREATIVE_3D_SPEC.md §6, the Blender half — the REAL <c>blender.exe</c> on this
/// machine, headless, inside the job, driven through the real dispatcher. Nothing here is
/// mocked: the plan is data on disk, the driver is a Python file the scaffold wrote, the
/// scene is a <c>.blend</c>, the render is a PNG Blender made, and every fact asserted comes
/// from the tool's own <c>out.json</c> — never from what the plan asked for.
///
/// On a host without Blender (the CI runner) each test SKIPS with the reason named.
/// </summary>
[Collection(SceneLabCollection.Name)]
public sealed class SceneBlenderTests
{
    [SceneLabFact("blender")]
    public void The_real_blender_runs_in_the_job_and_scene_inspect_returns_the_read_back_and_the_render()
    {
        using var lab = new SceneLab();
        var folder = lab.Scaffold3d("bl-1", "kure", SceneLab.BlenderCommand());

        // The base scene is the lab's own fixture (the scaffold writes text; a .blend is not).
        File.Copy(lab.MakeBaseBlend(), Path.Combine(folder, SceneLab.SceneFileName));

        var run = lab.Exec(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "bl-1" }, budgetSeconds: 120);
        Assert.True(run["batch"]!.GetValue<bool>());
        Assert.Equal("blender", run["runtime"]!.GetValue<string>());
        Assert.Equal(0, run["exit_code"]!.GetValue<int>());
        Assert.True(run["seconds"]!.GetValue<double>() > 0);
        Assert.Contains("Blender", run["log_tail"]!.GetValue<string>(), StringComparison.OrdinalIgnoreCase);
        // A batch run answers with the exit, never with a port or a URL.
        Assert.Null(run["url"]);
        Assert.Null(run["port"]);

        // The tool wrote its own read-back; the device parsed it to prove it is JSON.
        var inspected = lab.Exec(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "bl-1" });
        var inspection = (JsonObject)inspected["inspection"]!;
        Assert.Equal("blender", inspection["tool"]!.GetValue<string>());
        Assert.Equal("BLENDER_WORKBENCH", inspection["engine"]!.GetValue<string>());
        Assert.Equal("Kamera", inspection["camera"]!.GetValue<string>());

        var names = ((JsonArray)inspection["objects"]!).Select(o => o!["name"]!.GetValue<string>()).ToList();
        Assert.Equal(["Gunes", "Kamera", "Kure"], names);
        var sphere = ((JsonArray)inspection["objects"]!).Single(o => o!["name"]!.GetValue<string>() == "Kure")!;
        Assert.Equal("MESH", sphere["type"]!.GetValue<string>());
        Assert.Equal(0.0, ((JsonArray)sphere["location"]!)[1]!.GetValue<double>(), 6);

        // The render came back as bytes, and its hash is the one the driver recorded.
        var render = (JsonObject)inspected["render"]!;
        Assert.Equal("render.png", render["path"]!.GetValue<string>());
        Assert.True(render["verified"]!.GetValue<bool>());
        var bytes = Convert.FromBase64String(render["png_base64"]!.GetValue<string>());
        Assert.Equal(render["bytes"]!.GetValue<long>(), bytes.LongLength);
        Assert.Equal(render["sha256"]!.GetValue<string>(), Convert.ToHexStringLower(SHA256.HashData(bytes)));
        Assert.Equal(File.ReadAllBytes(Path.Combine(folder, "render.png")), bytes);
        // A PNG, read by something that is not the thing that wrote it.
        Assert.Equal([0x89, (byte)'P', (byte)'N', (byte)'G'], bytes.Take(4).ToArray());

        // The run really was a child in this runner's job, and it is gone.
        Assert.Equal(1, lab.Runner.ProcessesStarted);
        var record = lab.Runner.RunOf("bl-1")!;
        Assert.Equal(ProjectRun.StateExited, record.State);
        Assert.False(SceneLabProcesses.IsAlive(run["pid"]!.GetValue<int>()));
    }

    [SceneLabFact("blender")]
    public void A_render_whose_bytes_no_longer_match_the_declared_hash_is_postcondition_failed()
    {
        using var lab = new SceneLab();
        var folder = lab.Scaffold3d("bl-2", "bozuk", SceneLab.BlenderCommand());
        File.Copy(lab.MakeBaseBlend(), Path.Combine(folder, SceneLab.SceneFileName));
        lab.Exec(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "bl-2" }, budgetSeconds: 120);

        // Whatever swapped the picture, the device will not pass it on as the tool's work.
        var png = Path.Combine(folder, "render.png");
        var bytes = File.ReadAllBytes(png);
        bytes[^1] ^= 0xFF;
        File.WriteAllBytes(png, bytes);

        var failure = lab.ExpectFailure(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "bl-2" });
        Assert.Equal(ErrorClasses.PostconditionFailed, failure.ErrorClass);
        Assert.False(failure.Retryable);
        Assert.Equal("render_sha256_mismatch", failure.Detail["detail"]);

        // And a render the inspection points OUTSIDE the project is refused before it is read.
        var outJson = (JsonObject)JsonNode.Parse(File.ReadAllText(Path.Combine(folder, SceneCapabilityNames.InspectionFileName)))!;
        ((JsonObject)outJson["render"]!)["path"] = "../../escape.png";
        File.WriteAllText(Path.Combine(folder, SceneCapabilityNames.InspectionFileName), outJson.ToJsonString());
        Assert.Equal(ErrorClasses.PermissionDenied, lab.ExpectFailure(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "bl-2" }).ErrorClass);
    }

    [Fact]
    public void Scene_inspect_bounds_and_refuses_an_inspection_that_is_not_json()
    {
        using var lab = new SceneLab();
        var folder = lab.Scaffold3d("bl-3", "sinir", SceneLab.BlenderCommand());
        var outPath = Path.Combine(folder, SceneCapabilityNames.InspectionFileName);

        // Nothing has run: there is no read-back to give.
        var missing = lab.ExpectFailure(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "bl-3" });
        Assert.Equal(ErrorClasses.PostconditionFailed, missing.ErrorClass);
        Assert.Equal("inspection_missing", missing.Detail["detail"]);

        // A file that is not JSON is not an answer.
        File.WriteAllText(outPath, "{ this is not json");
        Assert.Equal("inspection_not_json", lab.ExpectFailure(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "bl-3" }).Detail["detail"]);

        // Past the bound it is not even read.
        File.WriteAllText(outPath, "{\"pad\":\"" + new string('x', (int)SceneCapabilityNames.MaxInspectionBytes + 16) + "\"}");
        var bound = lab.ExpectFailure(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "bl-3" });
        Assert.Equal("inspection_bound", bound.Detail["detail"]);
        Assert.Contains(SceneCapabilityNames.MaxInspectionBytes.ToString(System.Globalization.CultureInfo.InvariantCulture), bound.Message, StringComparison.Ordinal);

        // A render past its own bound is refused too, with the file on disk untouched.
        var big = new byte[SceneCapabilityNames.MaxRenderBytes + 1];
        Random.Shared.NextBytes(big);
        File.WriteAllBytes(Path.Combine(folder, "big.png"), big);
        File.WriteAllText(outPath, new JsonObject
        {
            ["tool"] = "blender",
            ["render"] = new JsonObject
            {
                ["path"] = "big.png",
                ["bytes"] = big.LongLength,
                ["sha256"] = Convert.ToHexStringLower(SHA256.HashData(big)),
            },
        }.ToJsonString());
        Assert.Equal("render_bound", lab.ExpectFailure(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "bl-3" }).Detail["detail"]);
        Assert.Equal(big.Length, new FileInfo(Path.Combine(folder, "big.png")).Length);

        // An inspection with no render at all is a valid answer: the plan only inspected.
        File.WriteAllText(outPath, new JsonObject { ["tool"] = "blender", ["objects"] = new JsonArray() }.ToJsonString());
        var quiet = lab.Exec(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "bl-3" });
        Assert.Null(quiet["render"]);
        Assert.Equal("blender", ((JsonObject)quiet["inspection"]!)["tool"]!.GetValue<string>());
    }
}

/// <summary>Whether a pid is still a living process — the scenes lab's own, so it does not lean on the projects lab.</summary>
internal static class SceneLabProcesses
{
    public static bool IsAlive(int pid)
    {
        try
        {
            using var process = System.Diagnostics.Process.GetProcessById(pid);
            return !process.HasExited;
        }
        catch (ArgumentException)
        {
            return false;
        }
    }
}
