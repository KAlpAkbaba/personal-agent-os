using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Scenes;

/// <summary>
/// M25_CREATIVE_3D_SPEC.md §1/§6, the Unity half — the REAL <c>Unity.exe</c> in batch mode on
/// a throwaway project under the fixture root. The owner's own project
/// (<c>E:\hologram\HologramVehicleTest</c>) is never opened: the allowlist refuses an absolute
/// <c>-projectPath</c> before any process exists (SceneAllowlistTests), and this test's
/// project folder is one the lab scaffolded a moment ago.
///
/// The state measured on the owner's machine on 2026-09-08 (09:55Z in
/// <c>docs/evidence/m25-tool-detection-2026-09-08.json</c>, again at 13:24Z while this was
/// written): the editor exits 198 and its log says "No valid Unity Editor license found" —
/// owner item 32 is a Unity Hub sign-in. So the test READS which of the two states it is in
/// and asserts the matching shape:
/// <list type="bullet">
/// <item>no licence — <c>project.run</c> must answer <c>dependency_unavailable</c> (never
/// <c>device_error</c>) carrying the licensing client's own line, so the Cloud Core can say
/// "Unity lisansı yok: yapılamadı";</item>
/// <item>licensed — <c>project.run</c> must answer exit 0 and <c>scene.inspect</c> must return
/// the driver's read-back.</item>
/// </list>
/// Neither branch is vacuous, and the flip needs no code change.
/// </summary>
[Collection(SceneLabCollection.Name)]
public sealed class SceneUnityTests
{
    [SceneLabFact("unity")]
    public void The_real_unity_editor_answers_either_the_licence_refusal_or_the_run_and_its_inspection()
    {
        using var lab = new SceneLab();
        var folder = lab.Scaffold3d("un-1", "arac", SceneLab.UnityCommand(), files: SceneLab.UnityFiles());
        Assert.True(File.Exists(Path.Combine(folder, "Assets", "PagentOS", "Editor", "SceneDriver.cs")));
        Assert.StartsWith(lab.ProjectsRoot3d, folder, StringComparison.OrdinalIgnoreCase);

        JsonObject? run = null;
        CapabilityException? refusal = null;
        try
        {
            run = lab.Exec(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "un-1" }, budgetSeconds: 600);
        }
        catch (CapabilityException ex)
        {
            refusal = ex;
        }

        Assert.Equal(1, lab.Runner.ProcessesStarted);

        if (refusal is not null)
        {
            // The state of this machine on 2026-09-08. The editor IS installed and DID start;
            // what is missing is an entitlement only the owner can obtain.
            Assert.Equal(ErrorClasses.DependencyUnavailable, refusal.ErrorClass);
            Assert.NotEqual(ErrorClasses.InternalBug, refusal.ErrorClass);
            Assert.False(refusal.Retryable);
            Assert.Equal(SceneCapabilityNames.UnityNoLicenceDetail, refusal.Detail["detail"]);
            Assert.Equal(SceneCapabilityNames.UnityNoLicenceExitCode, refusal.Detail["exit_code"]);

            // The licensing client's OWN line, not a paraphrase.
            var line = Assert.IsType<string>(refusal.Detail["detail_line"]);
            Assert.Contains(SceneCapabilityNames.UnityNoLicenceMarker, line, StringComparison.OrdinalIgnoreCase);
            Assert.Contains(line, refusal.Message, StringComparison.Ordinal);

            // The tail came from Unity's -logFile, which is where Unity writes everything.
            var tail = Assert.IsType<string>(refusal.Detail["log_tail"]);
            Assert.Contains("Licensing", tail, StringComparison.OrdinalIgnoreCase);
            Assert.True(File.Exists(Path.Combine(folder, SceneLab.UnityLogFileName)), "the editor's log file is under the project root");

            // And nothing claims a scene was made.
            var inspect = lab.ExpectFailure(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "un-1" });
            Assert.Equal(ErrorClasses.PostconditionFailed, inspect.ErrorClass);
            Assert.Equal("inspection_missing", inspect.Detail["detail"]);
            return;
        }

        // The owner has signed in since: the same lab, the same code, the real thing.
        Assert.NotNull(run);
        Assert.True(run!["batch"]!.GetValue<bool>());
        Assert.Equal("unity", run["runtime"]!.GetValue<string>());
        Assert.Equal(0, run["exit_code"]!.GetValue<int>());

        var inspected = lab.Exec(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "un-1" });
        Assert.Equal("unity", ((JsonObject)inspected["inspection"]!)["tool"]!.GetValue<string>());
    }

    [SceneLabFact("unity")]
    public void The_unity_editor_is_detected_where_the_hub_installs_it_and_never_looked_for_on_path()
    {
        var unity = SceneLab.Unity!;
        Assert.EndsWith(Path.Combine("Editor", "Unity.exe"), unity.Executable, StringComparison.OrdinalIgnoreCase);
        Assert.Contains(Path.Combine("Unity", "Hub", "Editor"), unity.Executable, StringComparison.OrdinalIgnoreCase);
        Assert.True(File.Exists(unity.Executable));
        Assert.Matches(@"^\d+\.\d+", unity.Version);

        // The Hub's directory name IS the version: nothing was executed to learn it.
        Assert.Contains(unity.Version, unity.Executable, StringComparison.OrdinalIgnoreCase);
    }
}
