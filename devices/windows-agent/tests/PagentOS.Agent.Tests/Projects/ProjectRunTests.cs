using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Documents;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Projects;

/// <summary>
/// <c>project.run</c> / <c>project.status</c> / <c>project.stop</c> (M23_APP_FACTORY_SPEC.md §3,
/// ADR-0086 decisions 3 and 6) with REAL processes: a <c>python -m http.server</c> child in a
/// Windows Job Object on a free loopback port that answers <c>GET /</c> with the app's HTML,
/// the log captured under the project, two at once and a third refused, the stop that ends
/// the job and nothing else, the bounds read back from the kernel, the early exit, the port
/// that never answers, the lifetime, the busy port, and the environment scrub proven from
/// inside the child. Tests that need a runtime skip with the reason when it is absent.
/// </summary>
[Collection(ProjectLabCollection.Name)]
public sealed class ProjectRunTests
{
    private static readonly string EnvDumpServer =
        "const fs = require('fs');\nfs.writeFileSync('env.json', JSON.stringify(process.env));\n";

    /// <summary>A node entry that binds the manifest's port and serves one line; the port is baked into the file at scaffold time.</summary>
    private static string ListeningEntry(int port, string prefix = "")
        => prefix + $"require('http').createServer((req, res) => {{ res.end('merhaba'); }}).listen({port}, '127.0.0.1');\n";

    private static JsonObject NodeManifest(int port, string entry)
        => new()
        {
            ["entry"] = entry,
            ["port"] = port,
            ["run"] = new JsonObject { ["serve"] = $"node {entry}" },
            ["test"] = new JsonObject { ["unit"] = "node tests/run.js" },
        };

    [ProjectLabFact("python")]
    public async Task Run_starts_a_real_http_server_in_a_bounded_job_and_the_page_answers()
    {
        using var lab = new ProjectLab();
        var (_, port) = lab.Scaffold("run-1", "gorev-takip");

        var result = lab.Exec(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "run-1", ["command_key"] = "serve" });
        var pid = result["pid"]!.GetValue<int>();
        Assert.Equal(port, result["port"]!.GetValue<int>());
        Assert.Equal($"http://127.0.0.1:{port}/", result["url"]!.GetValue<string>());
        Assert.Matches(@"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", result["started_at"]!.GetValue<string>());
        Assert.True(ProjectLab.IsAlive(pid));

        // The child is python — the companion's OWN child, not any python of the owner's.
        using (var child = Process.GetProcessById(pid))
        {
            Assert.Equal("python", child.ProcessName, StringComparer.OrdinalIgnoreCase);
        }

        // GET / answers the app's HTML.
        var html = await ProjectLab.GetAsync(result["url"]!.GetValue<string>());
        Assert.Contains("<title>", html, StringComparison.Ordinal);
        Assert.Contains("</html>", html, StringComparison.Ordinal);

        // In its job, with the bounds the kernel holds — read back, not assumed.
        var run = lab.Runner.RunOf("run-1")!;
        Assert.True(run.InJob, "the child is not in the run's job");
        var limits = run.Limits!;
        Assert.True(limits.KillOnJobClose, "KILL_ON_JOB_CLOSE is not set");
        Assert.False(limits.BreakawayAllowed, "breakaway must be denied");
        Assert.True(limits.MemoryBounded);
        Assert.Equal(512L * 1024 * 1024, limits.JobMemoryLimitBytes);
        Assert.True(limits.CpuTimeBounded);
        Assert.Equal(TimeSpan.FromMinutes(10), limits.PerJobUserTimeLimit);
        Assert.Equal(8u, limits.ActiveProcessLimit);
        Assert.Equal(JobObject.UiLimitAll, limits.UiRestrictions);

        // status: running, the log captured under the project (the request line is in it).
        var status = lab.Exec(ProjectCapabilityNames.ProjectStatus, new JsonObject { ["project_id"] = "run-1" });
        Assert.Equal("running", status["state"]!.GetValue<string>());
        Assert.Equal(pid, status["pid"]!.GetValue<int>());
        Assert.Equal(port, status["port"]!.GetValue<int>());
        Assert.True(status["uptime_s"]!.GetValue<int>() >= 0);
        var logPath = Path.Combine(lab.FolderOf("gorev-takip"), ProjectRoots.StateFolderName, ProjectRunner.RunLogName);
        Assert.Equal(logPath, result["log_path"]!.GetValue<string>(), StringComparer.OrdinalIgnoreCase);
        var deadline = DateTime.UtcNow.AddSeconds(5);
        while (!status["log_tail"]!.GetValue<string>().Contains("GET / ", StringComparison.Ordinal) && DateTime.UtcNow < deadline)
        {
            await Task.Delay(100);
            status = lab.Exec(ProjectCapabilityNames.ProjectStatus, new JsonObject { ["project_id"] = "run-1" });
        }

        Assert.Contains("GET / ", status["log_tail"]!.GetValue<string>(), StringComparison.Ordinal);
        using (var logFile = new FileStream(logPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
        using (var reader = new StreamReader(logFile))
        {
            Assert.Contains("GET / ", await reader.ReadToEndAsync(), StringComparison.Ordinal);
        }

        // A second run of the same project while it runs is refused; the first is untouched.
        var again = lab.ExpectFailure(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "run-1" });
        Assert.Equal(ErrorClasses.DependencyUnavailable, again.ErrorClass);
        Assert.Equal("already_running", again.Detail[DocumentErrors.DetailKey]);
        Assert.True(ProjectLab.IsAlive(pid));

        // stop: the child is gone within 5 s, the status says so, and the slot is free again.
        var stopped = lab.Exec(ProjectCapabilityNames.ProjectStop, new JsonObject { ["project_id"] = "run-1" });
        Assert.True(stopped["stopped"]!.GetValue<bool>());
        Assert.True(stopped["was_running"]!.GetValue<bool>());
        Assert.True(ProjectLab.WaitForExit(pid, TimeSpan.FromSeconds(5)), "the child outlived project.stop");
        var after = lab.Exec(ProjectCapabilityNames.ProjectStatus, new JsonObject { ["project_id"] = "run-1" });
        Assert.Equal("stopped", after["state"]!.GetValue<string>());
        Assert.Equal("stop", after["stop_reason"]!.GetValue<string>());
        var idempotent = lab.Exec(ProjectCapabilityNames.ProjectStop, new JsonObject { ["project_id"] = "run-1" });
        Assert.False(idempotent["was_running"]!.GetValue<bool>());

        var second = lab.Exec(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "run-1" });
        Assert.NotEqual(pid, second["pid"]!.GetValue<int>());
        Assert.Equal(2, lab.Runner.ProcessesStarted);
        Assert.True(lab.Log.Any("project.run gorev-takip"));
    }

    [ProjectLabFact("python")]
    public async Task Two_projects_run_at_once_and_a_third_is_refused_until_one_stops()
    {
        using var lab = new ProjectLab();
        lab.Scaffold("two-a", "app-a");
        lab.Scaffold("two-b", "app-b");
        lab.Scaffold("two-c", "app-c");

        var a = lab.Exec(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "two-a" });
        var b = lab.Exec(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "two-b" });
        Assert.NotEqual(a["port"]!.GetValue<int>(), b["port"]!.GetValue<int>());
        Assert.Contains("<html", await ProjectLab.GetAsync(a["url"]!.GetValue<string>()), StringComparison.Ordinal);
        Assert.Contains("<html", await ProjectLab.GetAsync(b["url"]!.GetValue<string>()), StringComparison.Ordinal);
        Assert.Equal(2, lab.Runner.ActiveRuns);

        var third = lab.ExpectFailure(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "two-c" });
        Assert.Equal(ErrorClasses.DependencyUnavailable, third.ErrorClass);
        Assert.True(third.Retryable);
        Assert.Equal("projects_busy", third.Detail[DocumentErrors.DetailKey]);
        Assert.Equal(2, lab.Runner.ProcessesStarted);
        Assert.Equal("scaffolded", lab.Exec(ProjectCapabilityNames.ProjectStatus, new JsonObject { ["project_id"] = "two-c" })["state"]!.GetValue<string>());

        lab.Exec(ProjectCapabilityNames.ProjectStop, new JsonObject { ["project_id"] = "two-a" });
        var c = lab.Exec(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "two-c" });
        Assert.Contains("<html", await ProjectLab.GetAsync(c["url"]!.GetValue<string>()), StringComparison.Ordinal);
        Assert.Equal(2, lab.Runner.ActiveRuns);
        Assert.True(ProjectLab.IsAlive(b["pid"]!.GetValue<int>()), "stopping A must not touch B");
    }

    [ProjectLabFact("python")]
    public void Stop_ends_only_the_job_and_a_sentinel_started_outside_it_survives()
    {
        using var lab = new ProjectLab();
        lab.Scaffold("stop-1", "app");
        // A python of "the owner's": started by the test, outside any job of the runner's.
        using var sentinel = Process.Start(new ProcessStartInfo(ProjectLab.PythonPath!)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            ArgumentList = { "-c", "import time; time.sleep(120)" },
        })!;
        try
        {
            var run = lab.Exec(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "stop-1" });
            var pid = run["pid"]!.GetValue<int>();
            Assert.NotEqual(sentinel.Id, pid);

            var stopped = lab.Exec(ProjectCapabilityNames.ProjectStop, new JsonObject { ["project_id"] = "stop-1" });
            Assert.True(stopped["was_running"]!.GetValue<bool>());
            Assert.True(ProjectLab.WaitForExit(pid, TimeSpan.FromSeconds(5)));

            sentinel.Refresh();
            Assert.False(sentinel.HasExited, "project.stop killed a process outside its job");

            // Disposing the runner (the companion going away) ends its jobs — and only its jobs.
            var another = lab.Exec(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "stop-1" });
            var anotherPid = another["pid"]!.GetValue<int>();
            lab.Runner.Dispose();
            Assert.True(ProjectLab.WaitForExit(anotherPid, TimeSpan.FromSeconds(5)), "the job did not die with the runner");
            sentinel.Refresh();
            Assert.False(sentinel.HasExited);
        }
        finally
        {
            try
            {
                sentinel.Kill();
            }
            catch (Exception)
            {
                // Gone.
            }
        }
    }

    [ProjectLabFact("node")]
    public void A_run_that_exits_early_is_postcondition_failed_with_its_log_tail()
    {
        using var lab = new ProjectLab();
        var port = ProjectLab.FreePort();
        var files = ProjectLab.TemplateFiles();
        files.Add(("boom.js", "console.error('kaboom: port not bound');\nprocess.exit(3);\n"));
        lab.Exec(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("early-1", "boom", port, files, NodeManifest(port, "boom.js")));

        var ex = lab.ExpectFailure(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "early-1" });
        Assert.Equal(ErrorClasses.PostconditionFailed, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Equal("exited_early", ex.Detail[DocumentErrors.DetailKey]);
        Assert.Contains("kaboom", ex.Message, StringComparison.Ordinal);
        Assert.Contains("exit code 3", ex.Message, StringComparison.Ordinal);

        var status = lab.Exec(ProjectCapabilityNames.ProjectStatus, new JsonObject { ["project_id"] = "early-1" });
        Assert.Equal("exited", status["state"]!.GetValue<string>());
        Assert.Equal(3, status["exit_code"]!.GetValue<int>());
        Assert.Contains("kaboom", status["log_tail"]!.GetValue<string>(), StringComparison.Ordinal);
        Assert.Equal(0, lab.Runner.ActiveRuns);
    }

    [ProjectLabFact("node")]
    public void A_run_that_never_answers_on_its_port_is_ended_and_reported()
    {
        using var lab = new ProjectLab(portWait: TimeSpan.FromSeconds(2));
        var port = ProjectLab.FreePort();
        var files = ProjectLab.TemplateFiles();
        files.Add(("quiet.js", "console.log('listening nowhere');\nsetTimeout(() => {}, 120000);\n"));
        lab.Exec(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("quiet-1", "quiet", port, files, NodeManifest(port, "quiet.js")));

        var started = Stopwatch.StartNew();
        var ex = lab.ExpectFailure(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "quiet-1" });
        Assert.Equal(ErrorClasses.PostconditionFailed, ex.ErrorClass);
        Assert.True(ex.Retryable);
        Assert.Equal("port_never_answered", ex.Detail[DocumentErrors.DetailKey]);
        Assert.True(started.Elapsed < TimeSpan.FromSeconds(15), $"took {started.Elapsed}");

        var run = lab.Runner.RunOf("quiet-1")!;
        Assert.Equal("stopped", run.State);
        Assert.Equal("port_never_answered", run.StopReason);
        Assert.True(ProjectLab.WaitForExit(run.Pid, TimeSpan.FromSeconds(5)), "the quiet child was not ended");
        Assert.Equal(0, lab.Runner.ActiveRuns);
    }

    [ProjectLabFact("python")]
    public void The_lifetime_ends_a_run_by_itself()
    {
        using var lab = new ProjectLab(lifetime: TimeSpan.FromSeconds(2));
        lab.Scaffold("life-1", "short-lived");
        var run = lab.Exec(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "life-1" });
        var pid = run["pid"]!.GetValue<int>();

        Assert.True(ProjectLab.WaitForExit(pid, TimeSpan.FromSeconds(10)), "the lifetime did not end the run");
        var status = lab.Exec(ProjectCapabilityNames.ProjectStatus, new JsonObject { ["project_id"] = "life-1" });
        Assert.Equal("stopped", status["state"]!.GetValue<string>());
        Assert.Equal("lifetime", status["stop_reason"]!.GetValue<string>());
        Assert.Equal(0, lab.Runner.ActiveRuns);
        Assert.True(lab.Log.Any("reached its 0 min lifetime"));
    }

    [Fact]
    public void A_busy_port_is_refused_before_any_process()
    {
        var started = 0;
        using var lab = new ProjectLab(start: info =>
        {
            Interlocked.Increment(ref started);
            return Process.Start(info);
        });
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        try
        {
            lab.Exec(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("busy-1", "busy", port));
            var ex = lab.ExpectFailure(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "busy-1" });
            Assert.Equal(ErrorClasses.DependencyUnavailable, ex.ErrorClass);
            Assert.True(ex.Retryable);
            Assert.Equal("port_busy", ex.Detail[DocumentErrors.DetailKey]);
            Assert.Equal(0, started);
            Assert.Equal(0, lab.Runner.ActiveRuns);
        }
        finally
        {
            listener.Stop();
        }
    }

    [ProjectLabFact("node")]
    public void The_child_environment_carries_no_pagentos_or_credential_variable()
    {
        using var lab = new ProjectLab();
        var port = ProjectLab.FreePort();
        var files = ProjectLab.TemplateFiles();
        files.Add(("server.js", ListeningEntry(port, EnvDumpServer)));
        lab.Exec(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("env-1", "env-dump", port, files, NodeManifest(port, "server.js")));

        var secrets = new Dictionary<string, string>
        {
            ["PAGENTOS_TEST_SECRET"] = "s3cr3t",
            ["PAGENTOS_AGENT_DeviceId"] = "dev-1",
            ["MY_API_KEY"] = "k",
            ["GITHUB_TOKEN"] = "t",
            ["DB_PASSWORD"] = "p",
            ["AZURE_CLIENT_SECRET"] = "c",
        };
        foreach (var (name, value) in secrets)
        {
            Environment.SetEnvironmentVariable(name, value);
        }

        Environment.SetEnvironmentVariable("PAGENTOS_LAB_PLAIN", "visible-to-the-test-only");
        try
        {
            Assert.Equal("s3cr3t", Environment.GetEnvironmentVariable("PAGENTOS_TEST_SECRET"));
            var run = lab.Exec(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "env-1" });
            Assert.True(ProjectLab.IsAlive(run["pid"]!.GetValue<int>()));

            var dump = (JsonObject)JsonNode.Parse(File.ReadAllText(Path.Combine(lab.FolderOf("env-dump"), "env.json")))!;
            var keys = dump.Select(kv => kv.Key).ToHashSet(StringComparer.OrdinalIgnoreCase);
            foreach (var name in secrets.Keys.Append("PAGENTOS_LAB_PLAIN"))
            {
                Assert.False(keys.Contains(name), $"{name} reached the child");
            }

            Assert.DoesNotContain(keys, k => k.StartsWith("PAGENTOS_", StringComparison.OrdinalIgnoreCase));
            Assert.Contains("PATH", keys);
            Assert.StartsWith(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32"), dump["Path"]?.GetValue<string>() ?? dump["PATH"]!.GetValue<string>(), StringComparison.OrdinalIgnoreCase);
            Assert.Equal("1", dump["NO_COLOR"]!.GetValue<string>());
        }
        finally
        {
            foreach (var name in secrets.Keys.Append("PAGENTOS_LAB_PLAIN"))
            {
                Environment.SetEnvironmentVariable(name, null);
            }
        }
    }

    [Theory]
    [InlineData("PAGENTOS_TEST_SECRET", true)]
    [InlineData("pagentos_agent_deviceid", true)]
    [InlineData("OPENAI_API_KEY", true)]
    [InlineData("AWS_SECRET_ACCESS_KEY", true)]
    [InlineData("GITHUB_TOKEN", true)]
    [InlineData("NPM_TOKEN", true)]
    [InlineData("DB_PASSWORD", true)]
    [InlineData("SMTP_PASSWD", true)]
    [InlineData("GOOGLE_APPLICATION_CREDENTIALS", true)]
    [InlineData("TOKEN", true)]
    [InlineData("SECRET", true)]
    [InlineData("PATH", false)]
    [InlineData("TEMP", false)]
    [InlineData("USERPROFILE", false)]
    [InlineData("PROCESSOR_ARCHITECTURE", false)]
    [InlineData("SystemRoot", false)]
    [InlineData("PYTHONIOENCODING", false)]
    public void The_scrub_rule_names_every_credential_shaped_variable_and_nothing_a_runtime_needs(string name, bool secret)
    {
        Assert.Equal(secret, ProjectRunner.IsSecretVariable(name));
        var env = new Dictionary<string, string?> { [name] = "v", ["KEEP"] = "k" };
        var removed = ProjectRunner.Scrub(env);
        Assert.Equal(secret, removed.Contains(name));
        Assert.True(env.ContainsKey("KEEP"));
    }
}
