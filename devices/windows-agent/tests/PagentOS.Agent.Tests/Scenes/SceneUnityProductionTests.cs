using System.Security.Cryptography;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Scenes;
using Xunit;

namespace PagentOS.Agent.Tests.Scenes;

/// <summary>
/// B50 (ADR-0164): the Unity path as the Cloud Core ships it, in the real licensed editor. The
/// lab's own Unity driver (<see cref="SceneLab.UnityDriverScript"/>) proves the job and the
/// allowlist; this proves the PRODUCTION bytes - <c>services/api/app/creative3d/drivers/SceneDriver.cs</c>,
/// the pinned <c>unity_support/</c> package manifest and catalogue scripts, and the plan fixture
/// the Cloud Core's own test keeps equal to <c>ScenePlan.plan_json()</c> - read from this
/// repository, scaffolded exactly as the Cloud Core scaffolds them, run inside the job, and read
/// back through <c>scene.inspect</c>. Skipped by name on a machine without Unity (CI).
/// </summary>
[Collection(SceneLabCollection.Name)]
public sealed class SceneUnityProductionTests
{
    private static string RepoRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "services", "api", "app", "creative3d", "drivers")))
        {
            dir = dir.Parent;
        }

        Assert.NotNull(dir);
        return dir!.FullName;
    }

    private static string EditorLogErrors(string folder)
    {
        var editorLog = Path.Combine(folder, SceneLab.UnityLogFileName);
        if (!File.Exists(editorLog))
        {
            return "(no editor log)";
        }

        // A build failure's cause is the tool output Unity prints BEFORE its LogError stack, so
        // each such stack brings the lines above it; other lines only when they say "error".
        var lines = File.ReadAllLines(editorLog);
        var picked = new SortedSet<int>();
        for (var i = 0; i < lines.Length; i++)
        {
            if (lines[i].StartsWith("UnityEngine.Debug:LogError", StringComparison.Ordinal))
            {
                for (var j = Math.Max(0, i - 12); j < i; j++)
                {
                    picked.Add(j);
                }
            }
            else if (lines[i].Contains("error", StringComparison.OrdinalIgnoreCase)
                && !lines[i].StartsWith("Importing ", StringComparison.Ordinal))
            {
                picked.Add(i);
            }
        }

        return string.Join('\n', picked.Take(120).Select(i => lines[i]));
    }

    [SceneLabFact("unity")]
    public void The_shipped_cloud_driver_builds_a_real_scene_in_the_licensed_editor_and_the_device_reads_it_back()
    {
        var root = RepoRoot();
        var drivers = Path.Combine(root, "services", "api", "app", "creative3d", "drivers");
        var support = Path.Combine(drivers, "unity_support");
        var files = new List<(string Path, string Text)>
        {
            (SceneLab.PlanFileName, File.ReadAllText(Path.Combine(root, "services", "api", "tests", "fixtures", "creative3d", "unity-plan.json"))),
            ("Assets/PagentOS/Editor/SceneDriver.cs", File.ReadAllText(Path.Combine(drivers, "SceneDriver.cs"))),
        };
        foreach (var file in Directory.EnumerateFiles(support, "*", SearchOption.AllDirectories))
        {
            files.Add((Path.GetRelativePath(support, file).Replace('\\', '/'), File.ReadAllText(file)));
        }

        Assert.Contains(files, f => f.Path == "Packages/manifest.json");
        Assert.Contains(files, f => f.Path == "Assets/PagentOS/Scripts/Spinner.cs");

        using var lab = new SceneLab();
        var folder = lab.Scaffold3d("un-prod", "unity-qualification", SceneLab.UnityCommand(), files: files);

        var run = lab.Exec(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "un-prod" }, budgetSeconds: 900);
        if (run["exit_code"]!.GetValue<int>() != 0)
        {
            Assert.Fail($"unity exited {run["exit_code"]}; editor log errors:\n{EditorLogErrors(folder)}");
        }

        var inspected = lab.Exec(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "un-prod" });
        var inspection = (JsonObject)inspected["inspection"]!;
        Assert.Equal("unity", inspection["tool"]?.GetValue<string>() ?? "unity");
        var errorsReported = ((JsonArray)inspection["errors"]!).Select(e => e!.GetValue<string>()).ToArray();
        Assert.True(
            errorsReported.Length == 0,
            "the driver reported: " + string.Join(" | ", errorsReported) + "\neditor log errors:\n" + EditorLogErrors(folder));

        // The objects the plan asked for, where it asked for them, read back from the editor.
        var objects = ((JsonArray)inspection["objects"]!).Select(o => (JsonObject)o!).ToList();
        var names = objects.Select(o => o["name"]!.GetValue<string>()).ToHashSet();
        Assert.Superset(new HashSet<string> { "Kutu", "Top", "Zemin", "Gunes", "Kamera" }, names);
        var box = objects.Single(o => o["name"]!.GetValue<string>() == "Kutu");
        Assert.Equal(45.0, ((JsonArray)box["rotation"]!)[1]!.GetValue<double>(), 1);
        Assert.Equal(0.9, ((JsonArray)box["material_color"]!)[0]!.GetValue<double>(), 2);
        Assert.Equal("Kamera", inspection["camera"]!.GetValue<string>());
        var sun = ((JsonArray)inspection["lights"]!).Single(l => l!["name"]!.GetValue<string>() == "Gunes")!;
        Assert.Equal(2.0, sun["energy"]!.GetValue<double>(), 3);

        // The render passed the device's checks: relative, inside the project, hash-checked.
        var render = (JsonObject)inspected["render"]!;
        Assert.Equal("render.png", render["path"]!.GetValue<string>());
        Assert.True(render["verified"]!.GetValue<bool>());
        var png = Convert.FromBase64String(render["png_base64"]!.GetValue<string>());
        Assert.Equal(render["sha256"]!.GetValue<string>(), Convert.ToHexStringLower(SHA256.HashData(png)));
        Assert.Equal((byte)0x89, png[0]);

        // And the scene the driver saved is on disk, where the next run re-opens it.
        Assert.NotEmpty(Directory.EnumerateFiles(folder, "*.unity", SearchOption.AllDirectories));

        // req 533: every catalogue script the plan attached was stepped and acted.
        var tests = ((JsonArray)inspection["tests"]!).Select(t => (JsonObject)t!).ToList();
        Assert.Equal(
            new[] { "Bouncer", "ColorCycler", "Spinner" },
            tests.Select(t => t["script"]!.GetValue<string>()).Order().ToArray());
        Assert.All(tests, t => Assert.True(t["passed"]!.GetValue<bool>(), t.ToJsonString()));
        // The editor runs in the owner's tr-TR culture; the details are machine-read, so "0.5",
        // never "0,5" (the first measured run wrote the comma).
        Assert.All(tests, t => Assert.DoesNotContain(',', t["detail"]!.GetValue<string>()));
        // ...and restored: the saved scene is the plan's, not the stepped one.
        Assert.Equal(45.0, ((JsonArray)box["rotation"]!)[1]!.GetValue<double>(), 1);
        Assert.Equal(0.9, ((JsonArray)box["material_color"]!)[0]!.GetValue<double>(), 2);

        // req 532: a Windows player, built by the editor, that this device's own PE reader
        // accepts, with the hash the editor declared - what the Cloud Core's file.inspect reads.
        var build = (JsonObject)inspection["build"]!;
        Assert.Equal("Succeeded", build["result"]!.GetValue<string>());
        Assert.Equal("Build/unity-qualification.exe", build["path"]!.GetValue<string>());
        var exe = Path.Combine(folder, "Build", "unity-qualification.exe");
        Assert.True(File.Exists(exe));
        Assert.Equal(build["sha256"]!.GetValue<string>(), Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(exe))));
        var pe = PagentOS.SessionCompanion.Documents.PeImageReader.TryRead(exe);
        Assert.NotNull(pe);

        // Opt-in: the measured run as a docs/evidence record (never on CI, never by default).
        var evidenceOut = Environment.GetEnvironmentVariable("PAGENTOS_B50_EVIDENCE_OUT");
        if (!string.IsNullOrEmpty(evidenceOut))
        {
            var supportPins = new JsonObject();
            foreach (var file in files.Where(f => f.Path != SceneLab.PlanFileName).OrderBy(f => f.Path, StringComparer.Ordinal))
            {
                supportPins[file.Path] = Convert.ToHexStringLower(SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(file.Text)));
            }

            var renderMeta = (JsonObject)render.DeepClone();

            var evidence = new JsonObject
            {
                ["kind"] = "b50_unity_production",
                ["measured_at"] = DateTimeOffset.UtcNow.ToString("o"),
                ["unity"] = SceneLab.Unity is { } unity ? new JsonObject { ["executable"] = unity.Executable, ["version"] = unity.Version } : null,
                ["processor_count"] = Environment.ProcessorCount,
                ["job_process_cap"] = SceneCapabilityNames.UnityProcessesPerJob(Environment.ProcessorCount),
                ["project_run_exit_code"] = run["exit_code"]!.GetValue<int>(),
                ["scaffolded_file_sha256"] = supportPins,
                ["inspection"] = inspection.DeepClone(),
                ["device_render_check"] = renderMeta,
                ["device_build_check"] = new JsonObject
                {
                    ["path"] = build["path"]!.GetValue<string>(),
                    ["bytes"] = new FileInfo(exe).Length,
                    ["sha256"] = Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(exe))),
                    ["verified"] = pe is not null,
                    ["matches_editor_sha256"] = true,
                    ["pe"] = pe!.DeepClone(),
                },
            };
            File.WriteAllText(evidenceOut, evidence.ToJsonString(new System.Text.Json.JsonSerializerOptions { WriteIndented = true }) + "\n");
        }
    }
}
