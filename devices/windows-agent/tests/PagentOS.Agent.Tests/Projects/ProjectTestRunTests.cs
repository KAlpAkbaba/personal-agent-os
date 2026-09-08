using System.Diagnostics;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Projects;

/// <summary>
/// <c>project.test</c> (M23_APP_FACTORY_SPEC.md §3): the template's own Node runner in the same
/// bounded job — the counts parsed from its <c>passed: N failed: M</c> line, a failing runner's
/// exit code and counts, the 5 min bound (shortened here) that ends the job, the memory bound
/// that ends a child allocating past 512 MiB, and the 1 MiB log bound.
/// </summary>
[Collection(ProjectLabCollection.Name)]
public sealed class ProjectTestRunTests
{
    private static List<(string Path, string Text)> WithRunner(string runner)
    {
        var files = ProjectLab.TemplateFiles();
        files.RemoveAll(f => f.Path.Equals("tests/run.js", StringComparison.OrdinalIgnoreCase));
        files.Add(("tests/run.js", runner));
        return files;
    }

    [ProjectLabFact("node")]
    public void Test_runs_the_templates_runner_in_a_job_and_parses_the_counts()
    {
        using var lab = new ProjectLab();
        lab.Scaffold("test-1", "gorev-takip");
        var result = lab.Exec(ProjectCapabilityNames.ProjectTest, new JsonObject { ["project_id"] = "test-1", ["command_key"] = "unit" });

        Assert.Equal(0, result["exit_code"]!.GetValue<int>());
        Assert.True(result["counts_parsed"]!.GetValue<bool>());
        Assert.True(result["passed"]!.GetValue<int>() >= 1, result["report_tail"]!.GetValue<string>());
        Assert.Equal(0, result["failed"]!.GetValue<int>());
        Assert.Contains("passed: ", result["report_tail"]!.GetValue<string>(), StringComparison.Ordinal);
        Assert.False(result["truncated"]!.GetValue<bool>());
        Assert.True(result["duration_ms"]!.GetValue<int>() >= 0);
        var logPath = Path.Combine(lab.FolderOf("gorev-takip"), ProjectRoots.StateFolderName, ProjectRunner.TestLogName);
        Assert.Equal(logPath, result["log_path"]!.GetValue<string>(), StringComparer.OrdinalIgnoreCase);
        Assert.Contains("passed: ", File.ReadAllText(logPath), StringComparison.Ordinal);
        Assert.Equal(1, lab.Runner.ProcessesStarted);
        Assert.Equal(0, lab.Runner.ActiveRuns);

        // The default key is the manifest's first test command.
        var defaulted = lab.Exec(ProjectCapabilityNames.ProjectTest, new JsonObject { ["project_id"] = "test-1" });
        Assert.Equal("unit", defaulted["command_key"]!.GetValue<string>());
    }

    [ProjectLabFact("node")]
    public void A_failing_runner_reports_its_exit_code_and_counts()
    {
        using var lab = new ProjectLab();
        lab.Scaffold("test-2", "failing", files: WithRunner(ProjectLab.FailingRunner()));
        var result = lab.Exec(ProjectCapabilityNames.ProjectTest, new JsonObject { ["project_id"] = "test-2" });

        Assert.Equal(1, result["exit_code"]!.GetValue<int>());
        Assert.Equal(1, result["passed"]!.GetValue<int>());
        Assert.Equal(1, result["failed"]!.GetValue<int>());
        Assert.Contains("beklenen hata", result["report_tail"]!.GetValue<string>(), StringComparison.Ordinal);
    }

    [ProjectLabFact("node")]
    public void A_test_past_its_bound_is_timeout_and_the_job_is_ended()
    {
        using var lab = new ProjectLab(testTimeout: TimeSpan.FromSeconds(2));
        lab.Scaffold("test-3", "sleepy", files: WithRunner("console.log('sleeping');\nsetTimeout(() => { console.log('passed: 1 failed: 0'); }, 120000);\n"));

        var started = Stopwatch.StartNew();
        var ex = lab.ExpectFailure(ProjectCapabilityNames.ProjectTest, new JsonObject { ["project_id"] = "test-3" });
        Assert.Equal(ErrorClasses.Timeout, ex.ErrorClass);
        Assert.True(ex.Retryable);
        Assert.True(started.Elapsed < TimeSpan.FromSeconds(15), $"took {started.Elapsed}");
        Assert.Contains("sleeping", ex.Message, StringComparison.Ordinal);

        // The child was in the job that was ended: it is gone.
        var pid = (int)ex.Detail["pid"]!;
        Assert.True(ProjectLab.WaitForExit(pid, TimeSpan.FromSeconds(5)), "the sleeping runner outlived its bound");
    }

    [ProjectLabFact("node")]
    public async Task A_test_cancelled_by_the_caller_is_cancelled_and_the_job_is_ended()
    {
        using var lab = new ProjectLab();
        lab.Scaffold("test-4", "cancel-me", files: WithRunner("setTimeout(() => {}, 120000);\n"));
        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(1));
        var ex = await Assert.ThrowsAsync<PagentOS.Agent.Core.Commands.CapabilityException>(() => lab.ExecAsync(ProjectCapabilityNames.ProjectTest, new JsonObject { ["project_id"] = "test-4" }, TimeSpan.FromSeconds(30), cts.Token));
        Assert.Equal(ErrorClasses.Cancelled, ex.ErrorClass);
        Assert.True(ex.Retryable);
    }

    [ProjectLabFact("node")]
    public void The_memory_bound_ends_a_child_that_allocates_past_512_MiB()
    {
        using var lab = new ProjectLab();
        var hog = "const chunks = [];\nfor (let i = 0; i < 12; i++) { const b = Buffer.alloc(64 * 1024 * 1024, 1); chunks.push(b); console.log('allocated ' + ((i + 1) * 64) + ' MiB'); }\nconsole.log('allocated all 768 MiB');\nconsole.log('passed: 1 failed: 0');\n";
        lab.Scaffold("test-5", "hog", files: WithRunner(hog));

        var result = lab.Exec(ProjectCapabilityNames.ProjectTest, new JsonObject { ["project_id"] = "test-5" }, budgetSeconds: 60);
        var tail = result["report_tail"]!.GetValue<string>();
        Assert.NotEqual(0, result["exit_code"]!.GetValue<int>());
        Assert.DoesNotContain("allocated all 768 MiB", tail, StringComparison.Ordinal);
        Assert.Contains("allocated 64 MiB", tail, StringComparison.Ordinal);
    }

    [ProjectLabFact("node")]
    public void The_log_is_bounded_at_1_MiB_and_the_result_says_so()
    {
        using var lab = new ProjectLab();
        var spam = "const line = 'x'.repeat(1023) + '\\n';\nfor (let i = 0; i < 3072; i++) { process.stdout.write(line); }\nconsole.log('passed: 2 failed: 0');\n";
        lab.Scaffold("test-6", "spam", files: WithRunner(spam));

        var result = lab.Exec(ProjectCapabilityNames.ProjectTest, new JsonObject { ["project_id"] = "test-6" }, budgetSeconds: 60);
        Assert.True(result["truncated"]!.GetValue<bool>());
        var logPath = result["log_path"]!.GetValue<string>();
        var length = new FileInfo(logPath).Length;
        Assert.True(length <= ProjectCapabilityNames.MaxLogBytes + 128, $"log is {length} bytes");
        Assert.Contains("log bound reached", File.ReadAllText(logPath), StringComparison.Ordinal);
        // The tail is kept in memory past the file bound, so the summary line is still parsed.
        Assert.Equal(2, result["passed"]!.GetValue<int>());
    }

    [Theory]
    [InlineData("ok - a\nok - b\npassed: 2 failed: 0\n", 2, 0)]
    [InlineData("passed: 1 failed: 0\npassed: 3 failed: 1\n", 3, 1)]
    [InlineData("  3 passing (12ms)\n  1 failing\n", 3, 1)]
    [InlineData("Tests: 4 passed, 2 failed, 6 total", 4, 2)]
    [InlineData("nothing here", null, null)]
    [InlineData("", null, null)]
    public void ParseCounts_reads_the_runners_summary_and_says_null_when_there_is_none(string text, int? passed, int? failed)
    {
        var (p, f) = ProjectRunner.ParseCounts(text);
        Assert.Equal(passed, p);
        Assert.Equal(failed, f);
    }

    [Fact]
    public void A_project_without_a_test_command_is_validation_error_and_no_process_starts()
    {
        var started = 0;
        using var lab = new ProjectLab(start: info =>
        {
            Interlocked.Increment(ref started);
            return Process.Start(info);
        });
        var manifest = ProjectLab.TemplateManifest(ProjectLab.FreePort());
        manifest.Remove("test");
        lab.Exec(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("test-7", "no-tests", manifest: manifest));
        var ex = lab.ExpectFailure(ProjectCapabilityNames.ProjectTest, new JsonObject { ["project_id"] = "test-7" });
        Assert.Equal(ErrorClasses.ValidationError, ex.ErrorClass);
        Assert.Equal(0, started);
    }
}
