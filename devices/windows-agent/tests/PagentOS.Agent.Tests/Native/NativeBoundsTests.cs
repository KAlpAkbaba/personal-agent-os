using System.Diagnostics;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.DeviceService;
using PagentOS.SessionCompanion.Native;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Native;

/// <summary>
/// M28_NATIVE_APP_FACTORY_SPEC.md §5 — the bounds a build runs under, read back from the
/// KERNEL rather than from the constructor that set them, and the caps the service applies.
///
/// M28 adds no capability name to the wire (M25's shape: a build is a batch `project.run`
/// under a third root), so the advertised manifest is unchanged and this asserts that too —
/// the whole point of reusing the family is that the installed runtime's capability
/// arithmetic does not move.
/// </summary>
[Collection(NativeLabCollection.Name)]
public sealed class NativeBoundsTests
{
    [Fact]
    public void A_build_job_carries_4_GiB_64_processes_kill_on_close_no_breakaway_and_every_UI_restriction()
    {
        using var job = JobObject.CreateBounded(ProjectRuntime.Dotnet);
        var limits = job.ReadLimits();

        Assert.Equal(NativeCapabilityNames.MemoryLimitBytes, limits.JobMemoryLimitBytes);
        Assert.Equal((uint)NativeCapabilityNames.MaxProcessesPerJob, limits.ActiveProcessLimit);
        Assert.True(limits.KillOnJobClose, "a companion that dies must take its compiler with it");
        Assert.False(limits.BreakawayAllowed, "a build step must not be able to leave the job");
        Assert.True(limits.MemoryBounded);
        Assert.True(limits.CpuTimeBounded);
        Assert.Equal(JobObject.UiLimitAll, limits.UiRestrictions);

        // makeappx gets the same job as the compiler: same flags, same bounds, same reasons.
        using var packJob = JobObject.CreateBounded(ProjectRuntime.MakeAppx);
        Assert.Equal(NativeCapabilityNames.MemoryLimitBytes, packJob.ReadLimits().JobMemoryLimitBytes);
    }

    [Fact]
    public void The_CPU_bound_is_the_wall_bound_times_the_cores_this_machine_has_not_the_wall_bound()
    {
        // This is the one bound in this agent that is NOT the wall clock, and the reason is
        // worth a test rather than a comment: JOB_OBJECT_LIMIT_JOB_TIME ends the job when the
        // SUM of its processes' user time passes the limit, and MSBuild compiles in parallel.
        // A CPU bound set to the wall bound would end an honest build on any multi-core machine
        // and report it as a limit, which is exactly the kind of "true of every run" assertion
        // this repository has been burned by.
        using var job = JobObject.CreateBounded(ProjectRuntime.Dotnet);
        var cpu = job.ReadLimits().PerJobUserTimeLimit;

        Assert.Equal(NativeCapabilityNames.CpuTimeLimitFor(Environment.ProcessorCount), cpu);
        if (Environment.ProcessorCount > 1)
        {
            Assert.True(cpu > NativeCapabilityNames.RunLimit, $"on {Environment.ProcessorCount} cores the CPU bound must exceed the {NativeCapabilityNames.RunLimit.TotalMinutes:F0} min wall bound, or a parallel compile is killed for being parallel");
        }

        // Clamped at both ends so a strange ProcessorCount cannot produce an unbounded job or a
        // zero one.
        Assert.Equal(NativeCapabilityNames.RunLimit, NativeCapabilityNames.CpuTimeLimitFor(0));
        Assert.Equal(NativeCapabilityNames.RunLimit, NativeCapabilityNames.CpuTimeLimitFor(-3));
        Assert.Equal(NativeCapabilityNames.RunLimit * 64, NativeCapabilityNames.CpuTimeLimitFor(4096));
    }

    [Fact]
    public void The_runner_bounds_a_build_at_twenty_minutes_and_leaves_every_other_runtime_alone()
    {
        var runner = new ProjectRunner(new Support.ListLogger());
        Assert.Equal(TimeSpan.FromMinutes(20), NativeCapabilityNames.RunLimit);
        Assert.Equal(NativeCapabilityNames.RunLimit, runner.LimitFor(ProjectRuntime.Dotnet));
        Assert.Equal(NativeCapabilityNames.RunLimit, runner.LimitFor(ProjectRuntime.MakeAppx));
        Assert.Equal(SceneCapabilityNames.BlenderRunLimit, runner.LimitFor(ProjectRuntime.Blender));
        Assert.Equal(SceneCapabilityNames.UnityRunLimit, runner.LimitFor(ProjectRuntime.Unity));
        Assert.Equal(ProjectCapabilityNames.TestTimeout, runner.LimitFor(ProjectRuntime.Node));
        Assert.Equal(ProjectCapabilityNames.TestTimeout, runner.LimitFor(ProjectRuntime.Python));
    }

    [Fact]
    public void M28_adds_no_capability_name_so_the_advertised_manifest_does_not_move()
    {
        // The families are M19's operator, M20's documents, M23's projects and M25's scenes,
        // and M28 adds none: a build is a batch `project.run` of an allowlisted command under a
        // third root. 40 capabilities with -DisplayPower and the browser worker, as
        // scripts/qualify-staged-update.ps1 asserts, and 49 more with -Operator (45 before B30 added
        // the process/service four to the operator family).
        var without = AgentCapabilities.Compose(browserEnabled: true, displayPowerEnabled: true, operatorEnabled: false);
        var with = AgentCapabilities.Compose(browserEnabled: true, displayPowerEnabled: true, operatorEnabled: true);
        Assert.Equal(without.Count + 37 + 14 + 9 + 1, with.Count); // 37: ADR-0176 appended screen.ocr to the operator family
        Assert.Equal(with.Count, with.Distinct(StringComparer.Ordinal).Count());
        Assert.DoesNotContain("native.build", with, StringComparer.Ordinal);
        Assert.DoesNotContain("native.publish", with, StringComparer.Ordinal);
        Assert.False(AgentCapabilities.IsInteractive("native.build"));
    }

    [Fact]
    public void The_service_ceiling_covers_the_longest_bound_there_is()
    {
        // A ceiling shorter than the companion's own bound would have the service synthesise a
        // timeout while the compiler was still working — the M25 lesson, one milestone on.
        Assert.Equal(NativeCapabilityNames.CommandTimeoutCap, InteractiveCapabilityExecutor.TimeoutCapFor(ProjectCapabilityNames.ProjectRun));
        Assert.Equal(NativeCapabilityNames.CommandTimeoutCap, InteractiveCapabilityExecutor.TimeoutCapFor(ProjectCapabilityNames.ProjectTest));
        Assert.True(NativeCapabilityNames.CommandTimeoutCap > NativeCapabilityNames.RunLimit, "the ceiling must leave room for the typed answer");
        Assert.True(NativeCapabilityNames.CommandTimeoutCap > SceneCapabilityNames.UnityRunLimit);
        Assert.True(NativeCapabilityNames.CommandTimeoutCap > ProjectCapabilityNames.TestTimeout);

        // The rest of the family is untouched: scaffold, status and stop are still 30 s.
        Assert.Equal(TimeSpan.FromSeconds(30), InteractiveCapabilityExecutor.TimeoutCapFor(ProjectCapabilityNames.ProjectScaffold));
        Assert.Equal(TimeSpan.FromSeconds(30), InteractiveCapabilityExecutor.TimeoutCapFor(ProjectCapabilityNames.ProjectStatus));
        Assert.Equal(TimeSpan.FromSeconds(30), InteractiveCapabilityExecutor.TimeoutCapFor(ProjectCapabilityNames.ProjectStop));
        Assert.Equal(TimeSpan.FromSeconds(30), InteractiveCapabilityExecutor.TimeoutCapFor(ProjectCapabilityNames.ProjectArtifact));
    }

    [Fact]
    public void The_packaging_and_install_ceiling_covers_the_companion_s_own_five_minute_bounds_and_the_cloud_core_s_wait()
    {
        // B33 regression (found 2026-09-16): project.package rode the 30 s family cap while the
        // companion allows makeappx 5 min and the Cloud Core waits 330 s for the answer — a
        // cold pack of a 60 MB publish folder would have been answered `timeout` by the service
        // while it was still being written. Signing and an MSIX install only lengthen the step.
        var cap = ProjectCapabilityNames.LifecycleCommandTimeoutCap;
        foreach (var name in new[] { ProjectCapabilityNames.ProjectPackage, ProjectCapabilityNames.ProjectInstall, ProjectCapabilityNames.ProjectUninstall })
        {
            Assert.Equal(cap, InteractiveCapabilityExecutor.TimeoutCapFor(name));
        }

        Assert.True(cap > NativeLifecycle.MakeAppxTimeout, "the ceiling must leave room for the typed answer after makeappx");
        Assert.True(cap > WindowsPackageDeployer.OperationLimit, "the ceiling must leave room for the typed answer after a deployment");

        // The Cloud Core's own waits, read from its source rather than restated: they must not
        // give up before the device can answer, and must not wait much longer than it can take.
        var lifecycle = File.ReadAllText(Path.Combine(RepoRoot(), "services", "api", "app", "nativefactory", "device_lifecycle.py"));
        foreach (var constant in new[] { "PACKAGE_TIMEOUT_S", "MSIX_INSTALL_TIMEOUT_S" })
        {
            var match = System.Text.RegularExpressions.Regex.Match(lifecycle, $@"^{constant}: Final = (\d+(?:\.\d+)?)", System.Text.RegularExpressions.RegexOptions.Multiline);
            Assert.True(match.Success, $"{constant} is not declared in device_lifecycle.py");
            Assert.Equal(cap.TotalSeconds, double.Parse(match.Groups[1].Value, System.Globalization.CultureInfo.InvariantCulture));
        }
    }

    private static string RepoRoot()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "services", "api", "app")))
        {
            dir = dir.Parent;
        }

        return dir?.FullName ?? throw new DirectoryNotFoundException("the repository root was not found above the test output");
    }

    [Fact]
    public async Task A_build_that_outstays_its_bound_has_its_job_ended_and_answers_timeout()
    {
        // The bound proved with a two-second lab seam rather than by waiting twenty minutes.
        // `cmd` is not on the allowlist and never could be, so the run is driven through the
        // runner directly with a command the manifest parser produced — the containment and
        // the wait are what is under test here, not the parse (NativeAllowlistTests has that).
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        using var lab = new NativeLab(nativeLimit: TimeSpan.FromSeconds(2));
        var folder = lab.ScaffoldNative(
            "native-slow",
            "slow",
            run: new Dictionary<string, string> { ["build"] = NativeLab.DotnetCommand("build") });

        var manifest = ProjectManifest.Parse(ProjectRoots.ReadMarker(folder)!.Manifest, null, ProjectScope.Native);
        var command = manifest.Run["build"];
        var project = new ProjectContext("native-slow", "slow", folder, manifest, ProjectScope.Native);

        // A stand-in for a compiler that will not finish: the runner's own containment, wait
        // and job-termination path, with `ping -n 60 127.0.0.1` in the child's place. It is
        // NOT a manifest command and could never be one; the runner is being driven directly.
        var slow = new ProjectCommand(
            "build",
            command.Runtime,
            ["-n", "60", ProjectManifest.Loopback],
            "ping");
        var runner = new ProjectRunner(
            lab.Log,
            start: info =>
            {
                var replacement = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory, "ping.exe"))
                {
                    WorkingDirectory = info.WorkingDirectory,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardInput = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                };
                foreach (var argument in info.ArgumentList)
                {
                    replacement.ArgumentList.Add(argument);
                }

                return Process.Start(replacement);
            },
            nativeLimit: TimeSpan.FromSeconds(2));
        using (runner)
        {
            var stopwatch = Stopwatch.StartNew();
            var failure = await Assert.ThrowsAsync<CapabilityException>(
                () => runner.RunBatchAsync(project, slow, TimeSpan.FromMinutes(1), CancellationToken.None));
            stopwatch.Stop();

            Assert.Equal(ErrorClasses.Timeout, failure.ErrorClass);
            Assert.True(failure.Retryable);
            Assert.True(stopwatch.Elapsed < TimeSpan.FromSeconds(45), $"the bound did not end the run promptly ({stopwatch.Elapsed.TotalSeconds:F1} s)");
            Assert.Equal(1, runner.ProcessesStarted);
            Assert.All(runner.Runs, run => Assert.False(run.IsActive));
        }
    }
}
