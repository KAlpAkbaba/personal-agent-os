using System.Diagnostics;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Scenes;

/// <summary>
/// M25_CREATIVE_3D_SPEC.md §3, ADR-0088 decision 3 — the bounds a 3D run lives inside. The
/// job's flags are M23's, unchanged (kill-on-close, no breakaway, every UI restriction, the
/// scrubbed environment); what M25 adds is a per-runtime memory bound and a WALL-CLOCK bound,
/// because a batch run has no port to fail to answer on. The lab shortens the Blender bound
/// to two seconds and drives a driver that would sit for two minutes: the job ends it, the
/// answer is <c>timeout</c>, and the process is gone.
/// </summary>
[Collection(SceneLabCollection.Name)]
public sealed class SceneBoundsTests
{
    [SceneLabFact("blender")]
    public void A_blender_run_past_its_bound_is_ended_by_its_job_and_answered_as_a_timeout()
    {
        using var lab = new SceneLab(blenderLimit: TimeSpan.FromSeconds(2));
        var folder = lab.Scaffold3d(
            "slow-1",
            "yavas",
            SceneLab.BlenderCommand(driver: SceneLab.SlowDriverFileName),
            files: [(SceneLab.SlowDriverFileName, SceneLab.SlowDriverScript()), (SceneLab.PlanFileName, SceneLab.PlanJson())]);
        File.Copy(lab.MakeBaseBlend(), Path.Combine(folder, SceneLab.SceneFileName));

        var stopwatch = Stopwatch.StartNew();
        var failure = lab.ExpectFailure(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "slow-1" }, budgetSeconds: 120);
        stopwatch.Stop();

        Assert.Equal(ErrorClasses.Timeout, failure.ErrorClass);
        Assert.True(failure.Retryable);
        Assert.Contains("the job was ended", failure.Message, StringComparison.Ordinal);
        Assert.True(stopwatch.Elapsed < TimeSpan.FromSeconds(45), $"the bound did not end the run promptly ({stopwatch.Elapsed.TotalSeconds:F1} s)");

        var pid = Assert.IsType<int>(failure.Detail["pid"]);
        Assert.True(WaitGone(pid, TimeSpan.FromSeconds(10)), "the blender child outlived its job");
        Assert.Equal(ProjectRun.StateStopped, lab.Runner.RunOf("slow-1")!.State);
    }

    [SceneLabFact("blender")]
    public void The_job_a_3d_run_lives_in_carries_the_runtime_bounds_the_kernel_reads_back()
    {
        using var lab = new SceneLab(blenderLimit: TimeSpan.FromSeconds(2));
        var folder = lab.Scaffold3d(
            "slow-2",
            "sinirli",
            SceneLab.BlenderCommand(driver: SceneLab.SlowDriverFileName),
            files: [(SceneLab.SlowDriverFileName, SceneLab.SlowDriverScript()), (SceneLab.PlanFileName, SceneLab.PlanJson())]);
        File.Copy(lab.MakeBaseBlend(), Path.Combine(folder, SceneLab.SceneFileName));

        var runTask = Task.Run(() => lab.Exec(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "slow-2" }, budgetSeconds: 120));

        // While it is alive, ask the KERNEL what the job holds — not the constructor.
        var deadline = DateTime.UtcNow.AddSeconds(30);
        JobLimits? limits = null;
        while (DateTime.UtcNow < deadline)
        {
            limits = lab.Runner.RunOf("slow-2")?.Limits;
            if (limits is not null)
            {
                break;
            }

            Thread.Sleep(50);
        }

        Assert.NotNull(limits);
        Assert.True(limits!.KillOnJobClose, "a companion that dies must take the editor with it");
        Assert.False(limits.BreakawayAllowed, "a child may not leave the job");
        Assert.True(limits.MemoryBounded);
        Assert.Equal(SceneCapabilityNames.BlenderMemoryLimitBytes, limits.JobMemoryLimitBytes);
        Assert.Equal(SceneCapabilityNames.BlenderRunLimit, limits.PerJobUserTimeLimit);
        Assert.Equal(JobObject.UiLimitAll, limits.UiRestrictions);
        Assert.Equal((uint)JobObject.Max3dProcessesPerJob, limits.ActiveProcessLimit);

        // And the run itself still ends the way the bound says it must.
        var failure = Assert.Throws<PagentOS.Agent.Core.Commands.CapabilityException>(runTask.GetAwaiter().GetResult);
        Assert.Equal(ErrorClasses.Timeout, failure.ErrorClass);
    }

    [Fact]
    public void The_runtime_bounds_are_the_spec_s_numbers()
    {
        Assert.Equal(TimeSpan.FromMinutes(5), SceneCapabilityNames.BlenderRunLimit);
        Assert.Equal(2L * 1024 * 1024 * 1024, SceneCapabilityNames.BlenderMemoryLimitBytes);
        Assert.Equal(TimeSpan.FromMinutes(10), SceneCapabilityNames.UnityRunLimit);
        Assert.Equal(4L * 1024 * 1024 * 1024, SceneCapabilityNames.UnityMemoryLimitBytes);
        Assert.Equal(256L * 1024, SceneCapabilityNames.MaxInspectionBytes);
        Assert.Equal(512L * 1024, SceneCapabilityNames.MaxRenderBytes);

        using var runner = new ProjectRunner(new Support.ListLogger());
        Assert.Equal(SceneCapabilityNames.BlenderRunLimit, runner.LimitFor(ProjectRuntime.Blender));
        Assert.Equal(SceneCapabilityNames.UnityRunLimit, runner.LimitFor(ProjectRuntime.Unity));

        // The web runtimes keep M23's bounds: nothing about them changed.
        using var web = JobObject.CreateBounded(ProjectRuntime.Python);
        Assert.Equal(ProjectCapabilityNames.MemoryLimitBytes, web.ReadLimits().JobMemoryLimitBytes);
        using var unity = JobObject.CreateBounded(ProjectRuntime.Unity);
        Assert.Equal(SceneCapabilityNames.UnityMemoryLimitBytes, unity.ReadLimits().JobMemoryLimitBytes);
    }

    private static bool WaitGone(int pid, TimeSpan wait)
    {
        var deadline = DateTime.UtcNow + wait;
        while (DateTime.UtcNow < deadline)
        {
            if (!SceneLabProcesses.IsAlive(pid))
            {
                return true;
            }

            Thread.Sleep(50);
        }

        return !SceneLabProcesses.IsAlive(pid);
    }
}
