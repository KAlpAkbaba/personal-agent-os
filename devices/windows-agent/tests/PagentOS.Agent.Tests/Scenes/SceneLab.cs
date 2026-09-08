using System.Diagnostics;
using System.Text;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.SessionCompanion.Operator;
using PagentOS.SessionCompanion.Projects;
using PagentOS.SessionCompanion.Scenes;
using Xunit;

namespace PagentOS.Agent.Tests.Scenes;

/// <summary>
/// The scenes lab classes share one collection: they start REAL editors (Blender headless,
/// Unity in batch mode), which are not small, and a class's tests run one after another so
/// two editors are never resident at once. Every lab instance works in its own run directory
/// under <c>%TEMP%\pagentos-operator-fixture\scenes\</c> and removes it on dispose.
/// </summary>
[CollectionDefinition(Name)]
public sealed class SceneLabCollection
{
    public const string Name = "scenes-lab";
}

/// <summary>
/// A test that needs a 3D editor INSTALLED on this machine. On a host without it the test is
/// SKIPPED with the reason named — never passed vacuously, never failed for a reason that has
/// nothing to do with the code. The CI runner has neither, so every real-editor test skips
/// there and the allowlist, bounds and advertisement tests still run.
/// </summary>
public sealed class SceneLabFactAttribute : FactAttribute
{
    public SceneLabFactAttribute(params string[] tools)
    {
        var reason = SceneLab.SkipReason(tools);
        if (reason is not null)
        {
            Skip = reason;
        }
    }
}

/// <summary>
/// The M25 device lab (M25_CREATIVE_3D_SPEC.md §6, ADR-0088 decision 3): a Projects root and
/// its <c>3d</c> root under <c>%TEMP%\pagentos-operator-fixture\scenes\&lt;run-id&gt;\</c> —
/// inside the default authorised roots — and the real <see cref="ProjectCapabilities"/> over
/// the real <see cref="ProjectRunner"/> driven through its real dispatcher.
///
/// The owner's own Unity project (<c>E:\hologram\HologramVehicleTest</c>) is never opened,
/// read or written: every path this lab hands the device is relative and inside its own run
/// directory, and the allowlist refuses anything else before a process exists.
/// </summary>
public sealed class SceneLab : IDisposable
{
    /// <summary>The scene file every Blender fixture project carries (created by the lab, not by the scaffold: it is binary and the scaffold writes text).</summary>
    public const string SceneFileName = "scene.blend";

    public const string DriverFileName = "scene_driver.py";

    public const string SlowDriverFileName = "slow_driver.py";

    public const string PlanFileName = "plan.json";

    public const string UnityLogFileName = "unity.log";

    public SceneLab(
        bool enabled = true,
        TimeSpan? blenderLimit = null,
        TimeSpan? unityLimit = null,
        int? maxRunning = null,
        Func<ProcessStartInfo, Process?>? start = null)
    {
        RunId = Guid.NewGuid().ToString("N")[..12];
        Root = Path.Combine(OperatorOptions.FixtureRoot, "scenes", RunId);
        ProjectsRoot = Path.Combine(Root, "Projects");
        Directory.CreateDirectory(Root);
        Log = new ListLogger();

        // ProjectsRoot3d is left unset on purpose: the lab exercises the DEFAULT derivation
        // (<Projects root>\3d), which is what the owner's machine will use.
        Options = new OperatorOptions(enabled, TerminalRunner.DefaultAllowlist, [Root], Path.Combine(Root, "Downloads"), ProjectsRoot);
        Runner = new ProjectRunner(Log, start, maxRunning: maxRunning, blenderLimit: blenderLimit, unityLimit: unityLimit);
        Projects = new ProjectCapabilities(Options, Log, runner: Runner);
    }

    public string RunId { get; }

    /// <summary>The run directory: the lab's only authorised root.</summary>
    public string Root { get; }

    /// <summary>The Projects root — where a web project would go.</summary>
    public string ProjectsRoot { get; }

    /// <summary>The 3D root: <c>&lt;Projects root&gt;\3d</c>, derived exactly as the companion derives it.</summary>
    public string ProjectsRoot3d => Projects.ProjectsRoot3d!;

    public ProjectCapabilities Projects { get; }

    public ProjectRunner Runner { get; }

    public OperatorOptions Options { get; }

    public ListLogger Log { get; }

    // ------------------------------------------------------------------ the tools

    public static SceneTool? Blender => SceneTools.FindBlender();

    public static SceneTool? Unity => SceneTools.FindUnity();

    /// <summary>Null when every named editor is installed; otherwise the reason to skip, naming where it was looked for.</summary>
    public static string? SkipReason(params string[] tools)
    {
        if (!OperatingSystem.IsWindows())
        {
            return "the scenes lab needs Windows (Job Objects and the editors' Windows builds)";
        }

        foreach (var tool in tools)
        {
            var found = tool switch
            {
                "blender" => Blender,
                "unity" => Unity,
                _ => throw new ArgumentException($"unknown tool '{tool}'", nameof(tools)),
            };
            if (found is null)
            {
                return $"{tool} is not installed on this machine (detected, never searched for on PATH); the test needs the real editor";
            }
        }

        return null;
    }

    // ------------------------------------------------------------------ the fixture scene

    /// <summary>
    /// A minimal <c>.blend</c> for this run, made by Blender itself: <c>blender -b
    /// --factory-startup --python &lt;maker&gt; -- &lt;target&gt;</c>, headless, no window, in
    /// the lab's own directory. It is a FIXTURE — the lab's own hand, not the device path —
    /// because <c>project.scaffold</c> writes text and a <c>.blend</c> is binary. On the
    /// owner's machine the 3D scaffold places the base scene; here the lab does.
    /// </summary>
    public string MakeBaseBlend()
    {
        var blender = Blender ?? throw new InvalidOperationException("MakeBaseBlend needs Blender installed; the caller must be a [SceneLabFact(\"blender\")]");
        var maker = Path.Combine(Root, "make_base.py");
        var target = Path.Combine(Root, "base.blend");
        File.WriteAllText(maker, MakerScript(), new UTF8Encoding(false));

        var startInfo = new ProcessStartInfo(blender.Executable)
        {
            WorkingDirectory = Root,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (var argument in new[] { "-b", "--factory-startup", "--python", maker, "--", target })
        {
            startInfo.ArgumentList.Add(argument);
        }

        using var process = Process.Start(startInfo) ?? throw new InvalidOperationException("blender.exe could not be started for the fixture scene");
        var output = process.StandardOutput.ReadToEnd() + process.StandardError.ReadToEnd();
        process.WaitForExit(120_000);
        Assert.True(File.Exists(target), $"the fixture .blend was not written (exit {process.ExitCode}): {output}");
        return target;
    }

    private static string MakerScript() =>
        "import bpy\nimport sys\n\nargv = sys.argv\ntail = argv[argv.index(\"--\") + 1:] if \"--\" in argv else []\nbpy.ops.wm.read_factory_settings(use_empty=True)\nbpy.ops.wm.save_as_mainfile(filepath=tail[0], compress=True)\n";

    // ------------------------------------------------------------------ the fixture project

    /// <summary>
    /// The lab's Blender driver: a fixed file, stdlib plus <c>bpy</c>, that reads a PLAN (data,
    /// never code), applies its closed vocabulary, renders when asked and writes the
    /// INSPECTION — every object with its transform, the camera, the engine, and the render's
    /// path, size and SHA-256. The track that ships <c>app/creative3d/drivers/blender_driver.py</c>
    /// writes the same shape; this one is what the device lab drives when that file is not in
    /// the checkout.
    /// </summary>
    public static string DriverScript() =>
        """
        import hashlib
        import json
        import os
        import sys

        import bpy


        def tail_args():
            argv = sys.argv
            return argv[argv.index("--") + 1:] if "--" in argv else []


        def add_primitive(op):
            kind = op.get("kind")
            location = tuple(op.get("location", [0.0, 0.0, 0.0]))
            if kind == "sphere":
                bpy.ops.mesh.primitive_uv_sphere_add(location=location)
            elif kind == "cube":
                bpy.ops.mesh.primitive_cube_add(location=location)
            elif kind == "camera":
                bpy.ops.object.camera_add(location=location, rotation=tuple(op.get("rotation", [0.0, 0.0, 0.0])))
                bpy.context.scene.camera = bpy.context.object
            elif kind == "light_sun":
                bpy.ops.object.light_add(type="SUN", location=location)
            else:
                raise ValueError("unknown primitive kind: %r" % (kind,))
            obj = bpy.context.object
            if op.get("name"):
                obj.name = op["name"]
            if op.get("scale"):
                obj.scale = tuple(op["scale"])
            return obj


        def do_render(op, project_root):
            scene = bpy.context.scene
            scene.render.engine = "BLENDER_WORKBENCH"
            scene.render.resolution_x = int(op.get("width", 160))
            scene.render.resolution_y = int(op.get("height", 120))
            scene.render.resolution_percentage = 100
            scene.render.image_settings.file_format = "PNG"
            relative = op.get("path", "render.png")
            target = os.path.join(project_root, relative)
            scene.render.filepath = target
            bpy.ops.render.render(write_still=True)
            return relative, target


        def main():
            args = tail_args()
            plan_path, out_path = args[0], args[1]
            project_root = os.path.dirname(os.path.abspath(out_path))
            with open(plan_path, "r", encoding="utf-8") as handle:
                plan = json.load(handle)

            rendered = None
            for op in plan.get("operations", []):
                kind = op.get("op")
                if kind == "add_primitive":
                    add_primitive(op)
                elif kind == "transform":
                    obj = bpy.data.objects[op["name"]]
                    if "location" in op:
                        obj.location = tuple(op["location"])
                    if "scale" in op:
                        obj.scale = tuple(op["scale"])
                elif kind == "render":
                    rendered = do_render(op, project_root)
                elif kind == "inspect":
                    pass
                else:
                    raise ValueError("unknown operation: %r" % (kind,))

            inspection = {
                "tool": "blender",
                "blender_version": bpy.app.version_string,
                "objects": [
                    {
                        "name": obj.name,
                        "type": obj.type,
                        "location": [round(v, 6) for v in obj.location],
                        "scale": [round(v, 6) for v in obj.scale],
                    }
                    for obj in sorted(bpy.data.objects, key=lambda o: o.name)
                ],
                "camera": bpy.context.scene.camera.name if bpy.context.scene.camera else None,
                "engine": bpy.context.scene.render.engine,
            }

            if rendered is not None:
                relative, target = rendered
                with open(target, "rb") as handle:
                    payload = handle.read()
                inspection["render"] = {
                    "path": relative.replace("\\", "/"),
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }

            if bpy.data.filepath:
                bpy.ops.wm.save_mainfile()

            with open(out_path, "w", encoding="utf-8") as handle:
                json.dump(inspection, handle, ensure_ascii=False, indent=2)


        main()
        """;

    /// <summary>A driver that does nothing but outstay its welcome — for the bound the job enforces.</summary>
    public static string SlowDriverScript() =>
        "import time\nimport sys\n\nprint('slow driver started', flush=True)\ntime.sleep(120)\n";

    /// <summary>The lab's plan: a sphere, a camera, a sun, a 160x120 Workbench still, and an inspect.</summary>
    public static string PlanJson() =>
        """
        {
          "tool": "blender",
          "operations": [
            { "op": "add_primitive", "kind": "sphere", "name": "Kure", "location": [0, 0, 0] },
            { "op": "add_primitive", "kind": "camera", "name": "Kamera", "location": [0, -6, 2], "rotation": [1.2, 0, 0] },
            { "op": "add_primitive", "kind": "light_sun", "name": "Gunes", "location": [3, -3, 6] },
            { "op": "render", "width": 160, "height": 120, "path": "render.png" },
            { "op": "inspect" }
          ]
        }
        """;

    /// <summary>
    /// The Unity fixture project's editor driver — a FIXED lab file, never model-authored C#.
    /// It reads <c>-outPath</c> off the editor's own command line and writes the same
    /// inspection shape the Blender driver writes, so that on a machine whose Unity licence
    /// is valid the very same <c>scene.inspect</c> assertion holds. On the owner's machine
    /// today it is never compiled: the licence check comes first (exit 198).
    /// </summary>
    public static string UnityDriverScript() =>
        """
        using System;
        using System.IO;
        using UnityEditor;

        namespace PagentOS
        {
            public static class SceneDriver
            {
                public static void Run()
                {
                    var args = Environment.GetCommandLineArgs();
                    var outPath = "out.json";
                    for (var i = 0; i < args.Length - 1; i++)
                    {
                        if (args[i] == "-outPath")
                        {
                            outPath = args[i + 1];
                        }
                    }

                    File.WriteAllText(outPath, "{\"tool\":\"unity\",\"objects\":[],\"camera\":null}");
                    EditorApplication.Exit(0);
                }
            }
        }
        """;

    /// <summary>The files a throwaway Unity fixture project carries: the plan and the fixed editor driver under <c>Assets\PagentOS\Editor</c>.</summary>
    public static List<(string Path, string Text)> UnityFiles() =>
    [
        (PlanFileName, PlanJson()),
        ("Assets/PagentOS/Editor/SceneDriver.cs", UnityDriverScript()),
    ];

    /// <summary>The Blender command the allowlist admits, spelled the way a manifest spells it.</summary>
    public static string BlenderCommand(string driver = DriverFileName, string scene = SceneFileName, string plan = PlanFileName, string output = SceneCapabilityNames.InspectionFileName)
        => $"{SceneCapabilityNames.BlenderProgram} -b {scene} --python {driver} -- {plan} {output}";

    /// <summary>The Unity command the allowlist admits.</summary>
    public static string UnityCommand(string plan = PlanFileName, string output = SceneCapabilityNames.InspectionFileName, string log = UnityLogFileName, string method = SceneCapabilityNames.UnityDriverMethod)
        => $"{SceneCapabilityNames.UnityProgram} -batchmode -nographics -quit -projectPath {ProjectManifest.RootPlaceholder} -executeMethod {method} -planPath {plan} -outPath {output} -logFile {log}";

    // ------------------------------------------------------------------ driving

    public JsonObject Exec(string capability, JsonObject payload, double budgetSeconds = 30)
        => Projects.ExecuteAsync(capability, payload, TimeSpan.FromSeconds(budgetSeconds), CancellationToken.None).GetAwaiter().GetResult();

    public CapabilityException ExpectFailure(string capability, JsonObject payload, double budgetSeconds = 30)
        => Assert.Throws<CapabilityException>(() => Exec(capability, payload, budgetSeconds));

    /// <summary>A <c>project.scaffold</c> payload for a 3D project: the driver, the plan, and whatever else the caller adds.</summary>
    public static JsonObject Scaffold3dPayload(string projectId, string slug, string command, IEnumerable<(string Path, string Text)>? files = null, string entry = PlanFileName, IEnumerable<(string Path, string Text)>? extra = null)
    {
        var list = new JsonArray();
        foreach (var (path, text) in files ?? DefaultFiles())
        {
            list.Add(new JsonObject { ["path"] = path, ["text"] = text });
        }

        foreach (var (path, text) in extra ?? [])
        {
            list.Add(new JsonObject { ["path"] = path, ["text"] = text });
        }

        return new JsonObject
        {
            ["project_id"] = projectId,
            ["slug"] = slug,
            ["root"] = SceneCapabilityNames.Root3dFolderName,
            ["files"] = list,
            ["manifest"] = new JsonObject
            {
                ["entry"] = entry,
                ["run"] = new JsonObject { ["scene"] = command },
            },
        };
    }

    public static List<(string Path, string Text)> DefaultFiles() =>
    [
        (DriverFileName, DriverScript()),
        (PlanFileName, PlanJson()),
    ];

    /// <summary>Scaffolds a 3D project and returns its folder.</summary>
    public string Scaffold3d(string projectId, string slug, string command, IEnumerable<(string Path, string Text)>? files = null, string entry = PlanFileName, IEnumerable<(string Path, string Text)>? extra = null)
    {
        var result = Exec(ProjectCapabilityNames.ProjectScaffold, Scaffold3dPayload(projectId, slug, command, files, entry, extra));
        Assert.Equal(SceneCapabilityNames.Root3dFolderName, result["root"]!.GetValue<string>());
        return result["root_path"]!.GetValue<string>();
    }

    public string Folder3dOf(string slug) => Path.Combine(ProjectsRoot3d, slug);

    /// <summary>The marker's manifest, edited in place — the tamper an M23-style test needs to prove the allowlist is re-checked at run time, not only at scaffold time.</summary>
    public static void TamperRunCommand(string folder, string key, string command)
    {
        var marker = ProjectRoots.ReadMarker(folder)!;
        var manifest = (JsonObject)marker.Manifest.DeepClone();
        manifest["run"] = new JsonObject { [key] = command };
        ProjectRoots.WriteMarker(folder, marker with { Manifest = manifest });
    }

    public void Dispose()
    {
        // Every job this lab's runner holds is ended (its own children only), then the folder.
        // An editor ended a moment ago may still be giving its handles back, so the delete is
        // retried for a few seconds.
        Projects.Dispose();
        var deadline = DateTime.UtcNow.AddSeconds(10);
        while (true)
        {
            try
            {
                if (Directory.Exists(Root))
                {
                    Directory.Delete(Root, recursive: true);
                }

                return;
            }
            catch (Exception) when (DateTime.UtcNow < deadline)
            {
                Thread.Sleep(100);
            }
            catch (Exception)
            {
                // Best-effort teardown; the folder is under %TEMP%\pagentos-operator-fixture.
                return;
            }
        }
    }
}
