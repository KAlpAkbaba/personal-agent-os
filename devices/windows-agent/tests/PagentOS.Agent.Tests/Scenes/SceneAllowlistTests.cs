using System.Diagnostics;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Scenes;

/// <summary>
/// M25_CREATIVE_3D_SPEC.md §7, ADR-0088 decision 3 — the two 3D argv shapes, and everything
/// that is not one of them. The discipline is M23's, unchanged: the match is TOKEN FOR TOKEN
/// against a fixed list, never a string prefix, and the verdict is reached before any process
/// exists. Every hostile variant below is refused with a counting seam in place of
/// <c>Process.Start</c>, and the counter is 0 at the end: the refusals cost nothing but a
/// parse.
///
/// These tests need no editor installed — that is the point. The allowlist is a property of
/// the code, not of what happens to be on the machine.
/// </summary>
[Collection(SceneLabCollection.Name)]
public sealed class SceneAllowlistTests
{
    private const string Blender = SceneCapabilityNames.BlenderProgram;
    private const string Unity = SceneCapabilityNames.UnityProgram;
    private const string Root = ProjectManifest.RootPlaceholder;
    private const string Method = SceneCapabilityNames.UnityDriverMethod;

    /// <summary>A <c>Process.Start</c> that counts and never starts anything — a refusal that reaches it would show up as a non-zero count.</summary>
    private sealed class CountingStart
    {
        public int Calls { get; private set; }

        public Process? Start(ProcessStartInfo startInfo)
        {
            Calls++;
            return null;
        }
    }

    [Fact]
    public void The_two_shapes_are_admitted_under_the_3d_root_and_stored_as_an_argument_list()
    {
        var counter = new CountingStart();
        using var lab = new SceneLab(start: counter.Start);

        var blenderFolder = lab.Scaffold3d("scene-blender", "kure", SceneLab.BlenderCommand());
        Assert.Equal(lab.Folder3dOf("kure"), blenderFolder, StringComparer.OrdinalIgnoreCase);
        Assert.StartsWith(lab.ProjectsRoot3d, blenderFolder, StringComparison.OrdinalIgnoreCase);
        Assert.True(File.Exists(Path.Combine(blenderFolder, SceneLab.DriverFileName)));

        var blenderManifest = ProjectManifest.Parse(ProjectRoots.ReadMarker(blenderFolder)!.Manifest, null, ProjectScope.ThreeD);
        var blenderCommand = blenderManifest.Run["scene"];
        Assert.Equal(ProjectRuntime.Blender, blenderCommand.Runtime);
        Assert.True(blenderCommand.IsBatch);
        Assert.Equal(
            ["-b", SceneLab.SceneFileName, "--python", SceneLab.DriverFileName, "--", SceneLab.PlanFileName, SceneCapabilityNames.InspectionFileName],
            blenderCommand.Arguments);
        // A batch manifest may carry no port at all: nothing binds one.
        Assert.Equal(ProjectManifest.NoPort, blenderManifest.Port);

        var unityFolder = lab.Scaffold3d("scene-unity", "arac", SceneLab.UnityCommand());
        var unityManifest = ProjectManifest.Parse(ProjectRoots.ReadMarker(unityFolder)!.Manifest, null, ProjectScope.ThreeD);
        var unityCommand = unityManifest.Run["scene"];
        Assert.Equal(ProjectRuntime.Unity, unityCommand.Runtime);
        Assert.True(unityCommand.IsBatch);
        Assert.Equal(
            ["-batchmode", "-nographics", "-quit", "-projectPath", Root, "-executeMethod", Method, "-planPath", SceneLab.PlanFileName, "-outPath", SceneCapabilityNames.InspectionFileName, "-logFile", SceneLab.UnityLogFileName],
            unityCommand.Arguments);

        // The <root> placeholder becomes the project folder at run time and nothing else does.
        var materialised = unityCommand.Materialise(unityFolder);
        Assert.Equal(unityFolder, materialised[4]);
        Assert.DoesNotContain(materialised, a => a.Contains(Root, StringComparison.Ordinal));

        Assert.Equal(0, counter.Calls);
    }

    public static TheoryData<string, string> HostileCommands() => new()
    {
        // ---- Blender: a path that is not inside the project
        { "an absolute scene file", @"blender -b C:\Users\Public\scene.blend --python scene_driver.py -- plan.json out.json" },
        { "a scene file above the project", "blender -b ../../owner/scene.blend --python scene_driver.py -- plan.json out.json" },
        { "a driver above the project", "blender -b scene.blend --python ../driver.py -- plan.json out.json" },
        { "an absolute out path", @"blender -b scene.blend --python scene_driver.py -- plan.json C:\out.json" },
        { "a plan above the project", "blender -b scene.blend --python scene_driver.py -- ../plan.json out.json" },

        // ---- Blender: the shape itself
        { "no -b (a window, not a headless run)", "blender scene.blend --python scene_driver.py -- plan.json out.json" },
        { "no -- separator", "blender -b scene.blend --python scene_driver.py plan.json out.json" },
        { "one token too many", "blender -b scene.blend --python scene_driver.py -- plan.json out.json extra.json" },
        { "-P instead of --python", "blender -b scene.blend -P scene_driver.py -- plan.json out.json" },
        { "a driver that is not python", "blender -b scene.blend --python driver.exe -- plan.json out.json" },
        { "a scene file that is not a .blend", "blender -b scene.txt --python scene_driver.py -- plan.json out.json" },
        { "a plan that is not json", "blender -b scene.blend --python scene_driver.py -- plan.txt out.json" },
        { "a shell composition", "blender -b scene.blend --python scene_driver.py -- plan.json out.json & whoami" },

        // ---- Unity
        { "another -executeMethod", "unity -batchmode -nographics -quit -projectPath <root> -executeMethod System.Diagnostics.Process.Start -planPath plan.json -outPath out.json -logFile unity.log" },
        { "no -nographics", "unity -batchmode -quit -projectPath <root> -executeMethod PagentOS.SceneDriver.Run -planPath plan.json -outPath out.json -logFile unity.log" },
        { "no -quit (an editor that stays)", "unity -batchmode -nographics -projectPath <root> -executeMethod PagentOS.SceneDriver.Run -planPath plan.json -outPath out.json -logFile unity.log" },
        { "a project path that is the owner's own project", @"unity -batchmode -nographics -quit -projectPath E:\hologram\HologramVehicleTest -executeMethod PagentOS.SceneDriver.Run -planPath plan.json -outPath out.json -logFile unity.log" },
        { "a unity plan above the project", "unity -batchmode -nographics -quit -projectPath <root> -executeMethod PagentOS.SceneDriver.Run -planPath ../plan.json -outPath out.json -logFile unity.log" },
        { "an absolute log file", @"unity -batchmode -nographics -quit -projectPath <root> -executeMethod PagentOS.SceneDriver.Run -planPath plan.json -outPath out.json -logFile C:\unity.log" },
        { "one unity token too many", "unity -batchmode -nographics -quit -projectPath <root> -executeMethod PagentOS.SceneDriver.Run -planPath plan.json -outPath out.json -logFile unity.log -nolog" },

        // ---- neither
        { "another program entirely", "powershell -Command Get-Process" },
    };

    [Theory]
    [MemberData(nameof(HostileCommands))]
    public void A_hostile_3d_command_is_refused_before_any_process_and_nothing_is_written(string what, string command)
    {
        var counter = new CountingStart();
        using var lab = new SceneLab(start: counter.Start);

        var failure = lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, SceneLab.Scaffold3dPayload("hostile", "hostile", command));
        Assert.Equal(ErrorClasses.PermissionDenied, failure.ErrorClass);
        Assert.False(failure.Retryable);
        Assert.Contains("allowlist", failure.Message, StringComparison.OrdinalIgnoreCase);
        Assert.False(Directory.Exists(lab.Folder3dOf("hostile")), $"'{what}' left a folder behind");
        Assert.Equal(0, counter.Calls);
    }

    [Fact]
    public void The_3d_runtimes_are_refused_outside_the_3d_root()
    {
        var counter = new CountingStart();
        using var lab = new SceneLab(start: counter.Start);

        foreach (var command in new[] { SceneLab.BlenderCommand(), SceneLab.UnityCommand() })
        {
            var payload = SceneLab.Scaffold3dPayload("web", "web-project", command);
            // The SAME command, asked for under the Projects root instead of the 3D root (with
            // the port a web manifest must carry, so the refusal is about the runtime and not
            // about the missing number).
            payload["root"] = "projects";
            payload["manifest"]!["port"] = 8123;
            var failure = lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, payload);
            Assert.Equal(ErrorClasses.PermissionDenied, failure.ErrorClass);
            Assert.Contains(SceneCapabilityNames.Root3dFolderName, failure.Message, StringComparison.Ordinal);
            Assert.False(Directory.Exists(Path.Combine(lab.ProjectsRoot, "web-project")));
        }

        // And the web runtimes are unaffected by any of this.
        Assert.True(ProjectManifest.IsAllowlisted("node server.js", "server.js", 8123, hasLockfile: false, isTest: false));
        Assert.False(ProjectManifest.IsAllowlisted(SceneLab.BlenderCommand(), "plan.json", 8123, hasLockfile: false, isTest: false));
        Assert.True(ProjectManifest.IsAllowlisted(SceneLab.BlenderCommand(), "plan.json", 0, hasLockfile: false, isTest: false, ProjectScope.ThreeD));
        Assert.Equal(0, counter.Calls);
    }

    [Fact]
    public void A_marker_edited_into_a_3d_command_outside_the_shape_is_refused_at_run_time_before_any_process()
    {
        var counter = new CountingStart();
        using var lab = new SceneLab(start: counter.Start);
        var folder = lab.Scaffold3d("tamper", "tamper", SceneLab.BlenderCommand());

        // Whoever edits the marker — the owner, a stray script, a compromised generator — the
        // command is re-parsed from it on every run, so the allowlist has the last word.
        SceneLab.TamperRunCommand(folder, "scene", $@"{Blender} -b C:\Users\Public\owner.blend --python scene_driver.py -- plan.json out.json");
        var failure = lab.ExpectFailure(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "tamper" });
        Assert.Equal(ErrorClasses.PermissionDenied, failure.ErrorClass);
        Assert.False(failure.Retryable);
        Assert.Equal(0, counter.Calls);
        Assert.Equal(0, lab.Runner.ProcessesStarted);

        SceneLab.TamperRunCommand(folder, "scene", $"{Unity} -batchmode -nographics -quit -projectPath <root> -executeMethod Evil.Method -planPath plan.json -outPath out.json -logFile unity.log");
        Assert.Equal(ErrorClasses.PermissionDenied, lab.ExpectFailure(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "tamper" }).ErrorClass);
        Assert.Equal(0, counter.Calls);
    }

    [Fact]
    public void The_3d_root_is_the_projects_root_plus_3d_is_always_authorised_and_is_never_a_project_slug()
    {
        using var lab = new SceneLab();
        Assert.Equal(Path.Combine(lab.ProjectsRoot, SceneCapabilityNames.Root3dFolderName), lab.ProjectsRoot3d);
        // The options the scenes half actually builds its roots from list the 3D root, whatever
        // the caller configured; the companion's own configuration path is proved separately
        // (SceneAdvertisementTests).
        Assert.Contains(lab.ProjectsRoot3d, lab.Options.Scene3dOptions().AuthorisedRoots, StringComparer.OrdinalIgnoreCase);
        Assert.Equal(lab.ProjectsRoot3d, lab.Options.Scene3dOptions().EffectiveProjectsRoot);

        // A web project may not be called "3d": that folder IS the 3D root.
        var payload = SceneLab.Scaffold3dPayload("collide", SceneCapabilityNames.Root3dFolderName, "node plan.json");
        payload["root"] = "projects";
        payload["manifest"]!["entry"] = SceneLab.PlanFileName;
        payload["manifest"]!["port"] = 8123;
        payload["manifest"]!["run"] = new JsonObject { ["serve"] = $"node {SceneLab.PlanFileName}" };
        var failure = lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, payload);
        Assert.Equal(ErrorClasses.ValidationError, failure.ErrorClass);

        // And an unknown value for `root` is a validation error, not a path.
        var bogus = SceneLab.Scaffold3dPayload("bogus", "bogus", SceneLab.BlenderCommand());
        bogus["root"] = @"..\..\Desktop";
        Assert.Equal(ErrorClasses.ValidationError, lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, bogus).ErrorClass);
    }

    [Fact]
    public void Scene_inspect_refuses_a_web_project_and_an_unknown_id()
    {
        using var lab = new SceneLab();
        var payload = SceneLab.Scaffold3dPayload("web-only", "web-only", "node plan.json");
        payload["root"] = "projects";
        payload["manifest"]!["port"] = 8123;
        payload["manifest"]!["run"] = new JsonObject { ["serve"] = $"node {SceneLab.PlanFileName}" };
        lab.Exec(ProjectCapabilityNames.ProjectScaffold, payload);

        Assert.Equal(ErrorClasses.NotFound, lab.ExpectFailure(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "web-only" }).ErrorClass);
        Assert.Equal(ErrorClasses.NotFound, lab.ExpectFailure(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "nobody" }).ErrorClass);
        Assert.Equal(ErrorClasses.ValidationError, lab.ExpectFailure(SceneCapabilityNames.Inspect, new JsonObject()).ErrorClass);
    }
}
