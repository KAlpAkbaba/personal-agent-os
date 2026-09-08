using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.SessionCompanion.Operator;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Projects;

/// <summary>
/// The projects lab classes share one collection: they start real runtimes on real loopback
/// ports and hold slots in their own runners, and a class's tests run one after another so a
/// port a test bound is free again before the next. The collection itself may run beside
/// anything else — every lab instance works in its own run directory.
/// </summary>
[CollectionDefinition(Name)]
public sealed class ProjectLabCollection
{
    public const string Name = "projects-lab";
}

/// <summary>
/// A test that needs a runtime (<c>python</c>, <c>node</c>) on this machine. On a host without
/// it the test is SKIPPED with the reason named — never passed vacuously, never failed for a
/// reason that has nothing to do with the code.
/// </summary>
public sealed class ProjectLabFactAttribute : FactAttribute
{
    public ProjectLabFactAttribute(params string[] runtimes)
    {
        var reason = ProjectLab.SkipReason(runtimes);
        if (reason is not null)
        {
            Skip = reason;
        }
    }
}

/// <summary>
/// The M23 device lab (M23_APP_FACTORY_SPEC.md §6, ADR-0086 decision 6): a <c>Projects</c>
/// folder under <c>%TEMP%\pagentos-operator-fixture\projects\&lt;run-id&gt;\</c> — inside the
/// default authorised roots — and the real <see cref="ProjectCapabilities"/> over the real
/// <see cref="ProjectRunner"/> driven through its real dispatcher. The template is the
/// committed task-tracker under <c>services/api/tests/fixtures/apps/task-tracker/</c> when the
/// checkout has it (the Cloud Core track commits it), else the built-in minimal one below —
/// the same shape: <c>index.html</c>, an <c>app.js</c> of pure functions, <c>tests/run.js</c>
/// printing <c>passed: N failed: M</c>, and a <c>manifest.json</c>. Every job the lab started
/// is ended on dispose and the run directory removed; the owner's own projects and processes
/// are never touched.
/// </summary>
public sealed class ProjectLab : IDisposable
{
    public const string TitleText = "Görev Takip";

    public ProjectLab(
        bool enabled = true,
        TimeSpan? lifetime = null,
        TimeSpan? testTimeout = null,
        TimeSpan? portWait = null,
        int? maxRunning = null,
        Func<ProcessStartInfo, Process?>? start = null)
    {
        RunId = Guid.NewGuid().ToString("N")[..12];
        Root = Path.Combine(OperatorOptions.FixtureRoot, "projects", RunId);
        ProjectsRoot = Path.Combine(Root, "Projects");
        Directory.CreateDirectory(Root);
        Log = new ListLogger();
        Options = new OperatorOptions(enabled, TerminalRunner.DefaultAllowlist, [Root], Path.Combine(Root, "Downloads"), ProjectsRoot);
        Runner = new ProjectRunner(Log, start, lifetime, testTimeout, portWait, maxRunning);
        Projects = new ProjectCapabilities(Options, Log, runner: Runner);
    }

    public string RunId { get; }

    /// <summary>The run directory: the lab's only authorised root.</summary>
    public string Root { get; }

    /// <summary>The Projects root — the family's only destination — created by the first scaffold.</summary>
    public string ProjectsRoot { get; }

    public ProjectCapabilities Projects { get; }

    public ProjectRunner Runner { get; }

    public OperatorOptions Options { get; }

    public ListLogger Log { get; }

    // ------------------------------------------------------------------ runtimes

    public static string? PythonPath => ProjectRunner.FindOnPath("python.exe");

    public static string? NodePath => ProjectRunner.FindOnPath("node.exe");

    /// <summary>Null when every named runtime is on PATH; otherwise the reason to skip.</summary>
    public static string? SkipReason(params string[] runtimes)
    {
        if (!OperatingSystem.IsWindows())
        {
            return "the projects lab needs Windows (Job Objects)";
        }

        foreach (var runtime in runtimes)
        {
            var found = runtime switch
            {
                "python" => PythonPath,
                "node" => NodePath,
                _ => throw new ArgumentException($"unknown runtime '{runtime}'", nameof(runtimes)),
            };
            if (found is null)
            {
                return $"{runtime}.exe is not on PATH (the Store alias does not count); the test needs the real runtime";
            }
        }

        return null;
    }

    // ------------------------------------------------------------------ template

    /// <summary><c>services/api/tests/fixtures/apps/task-tracker</c> when the checkout has it (with a <c>manifest.json</c>), else null.</summary>
    public static string? FixtureTemplateSource
    {
        get
        {
            var dir = new DirectoryInfo(AppContext.BaseDirectory);
            while (dir is not null)
            {
                var candidate = Path.Combine(dir.FullName, "services", "api", "tests", "fixtures", "apps", "task-tracker");
                if (File.Exists(Path.Combine(candidate, "manifest.json")))
                {
                    return candidate;
                }

                dir = dir.Parent;
            }

            return null;
        }
    }

    /// <summary>The template's files as <c>{path, text}</c> (forward slashes) — the fixture folder's, else the built-in task-tracker's.</summary>
    public static List<(string Path, string Text)> TemplateFiles()
    {
        var source = FixtureTemplateSource;
        if (source is null)
        {
            return BuiltInTemplate();
        }

        var files = new List<(string, string)>();
        foreach (var file in Directory.EnumerateFiles(source, "*", SearchOption.AllDirectories))
        {
            var relative = Path.GetRelativePath(source, file).Replace('\\', '/');
            if (string.Equals(relative, "manifest.json", StringComparison.OrdinalIgnoreCase) || relative.StartsWith("expected/", StringComparison.OrdinalIgnoreCase) || relative.Equals(".gitattributes", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            files.Add((relative, File.ReadAllText(file)));
        }

        return files;
    }

    /// <summary>The template's manifest with <paramref name="port"/> — the fixture's <c>manifest.json</c>, else the built-in one.</summary>
    public static JsonObject TemplateManifest(int port)
    {
        var source = FixtureTemplateSource;
        JsonObject manifest;
        if (source is null)
        {
            manifest = new JsonObject
            {
                ["entry"] = "index.html",
                ["run"] = new JsonObject { ["serve"] = $"python -m http.server {ProjectManifest.PortPlaceholder} --bind {ProjectManifest.Loopback}" },
                ["test"] = new JsonObject { ["unit"] = "node tests/run.js" },
            };
        }
        else
        {
            manifest = (JsonObject)JsonNode.Parse(File.ReadAllText(Path.Combine(source, "manifest.json")))!;
        }

        manifest["port"] = port;
        return manifest;
    }

    /// <summary>The minimal task-tracker: the app's pure functions, a page that uses them, and a Node runner over them.</summary>
    public static List<(string Path, string Text)> BuiltInTemplate() =>
    [
        ("index.html", "<!doctype html>\n<html lang=\"tr\">\n<head>\n<meta charset=\"utf-8\">\n<title>" + TitleText + "</title>\n</head>\n<body>\n<h1 id=\"title\">" + TitleText + "</h1>\n<form id=\"add\"><input id=\"task\" placeholder=\"Yeni görev\"><button type=\"submit\">Ekle</button></form>\n<ul id=\"tasks\"></ul>\n<script src=\"app.js\"></script>\n<script>\nvar tasks = JSON.parse(localStorage.getItem('tasks') || '[]');\nfunction render() { var ul = document.getElementById('tasks'); ul.innerHTML = ''; tasks.forEach(function (t, i) { var li = document.createElement('li'); li.textContent = (t.done ? '[x] ' : '[ ] ') + t.title; li.onclick = function () { tasks = TaskTracker.toggle(tasks, i); save(); }; ul.appendChild(li); }); }\nfunction save() { localStorage.setItem('tasks', JSON.stringify(tasks)); render(); }\ndocument.getElementById('add').onsubmit = function (e) { e.preventDefault(); var input = document.getElementById('task'); tasks = TaskTracker.addTask(tasks, input.value); input.value = ''; save(); };\nrender();\n</script>\n</body>\n</html>\n"),
        ("app.js", "(function (root) {\n  function addTask(tasks, title) {\n    if (!title || !title.trim()) { return tasks; }\n    return tasks.concat([{ title: title.trim(), done: false }]);\n  }\n  function toggle(tasks, index) {\n    return tasks.map(function (t, i) { return i === index ? { title: t.title, done: !t.done } : t; });\n  }\n  var api = { addTask: addTask, toggle: toggle };\n  if (typeof module !== 'undefined' && module.exports) { module.exports = api; } else { root.TaskTracker = api; }\n})(this);\n"),
        ("tests/run.js", PassingRunner()),
        ("README.md", "# " + TitleText + "\n\nTarayıcıda açın: `python -m http.server`.\n"),
    ];

    /// <summary>A runner that passes three tests and prints the summary line the device parses.</summary>
    public static string PassingRunner() =>
        "const assert = require('assert');\nconst app = require('../app.js');\nlet passed = 0, failed = 0;\nfunction test(name, fn) { try { fn(); passed++; console.log('ok - ' + name); } catch (e) { failed++; console.log('not ok - ' + name + ': ' + e.message); } }\ntest('addTask appends', () => { const t = app.addTask([], 'Süt al'); assert.strictEqual(t.length, 1); assert.strictEqual(t[0].done, false); });\ntest('addTask ignores blank', () => { assert.strictEqual(app.addTask([], '   ').length, 0); });\ntest('toggle flips done', () => { const t = app.toggle(app.addTask([], 'x'), 0); assert.strictEqual(t[0].done, true); });\nconsole.log('passed: ' + passed + ' failed: ' + failed);\nprocess.exit(failed === 0 ? 0 : 1);\n";

    /// <summary>A runner with one failing test.</summary>
    public static string FailingRunner() =>
        "let passed = 0, failed = 0;\nfunction test(name, fn) { try { fn(); passed++; console.log('ok - ' + name); } catch (e) { failed++; console.log('not ok - ' + name + ': ' + e.message); } }\ntest('one passes', () => {});\ntest('one fails', () => { throw new Error('beklenen hata'); });\nconsole.log('passed: ' + passed + ' failed: ' + failed);\nprocess.exit(failed === 0 ? 0 : 1);\n";

    // ------------------------------------------------------------------ driving

    public JsonObject Exec(string capability, JsonObject payload, double budgetSeconds = 30)
        => Projects.ExecuteAsync(capability, payload, TimeSpan.FromSeconds(budgetSeconds), CancellationToken.None).GetAwaiter().GetResult();

    public Task<JsonObject> ExecAsync(string capability, JsonObject payload, TimeSpan budget, CancellationToken cancellationToken)
        => Projects.ExecuteAsync(capability, payload, budget, cancellationToken);

    public CapabilityException ExpectFailure(string capability, JsonObject payload, double budgetSeconds = 30)
        => Assert.Throws<CapabilityException>(() => Exec(capability, payload, budgetSeconds));

    /// <summary>A <c>project.scaffold</c> payload for the template, with the manifest's port chosen free unless given.</summary>
    public static JsonObject ScaffoldPayload(string projectId, string slug, int? port = null, IEnumerable<(string Path, string Text)>? files = null, JsonObject? manifest = null)
    {
        var chosen = port ?? FreePort();
        var list = new JsonArray();
        foreach (var (path, text) in files ?? TemplateFiles())
        {
            list.Add(new JsonObject { ["path"] = path, ["text"] = text });
        }

        return new JsonObject
        {
            ["project_id"] = projectId,
            ["slug"] = slug,
            ["files"] = list,
            ["manifest"] = manifest ?? TemplateManifest(chosen),
        };
    }

    /// <summary>Scaffolds the template and returns the result and the port its manifest names.</summary>
    public (JsonObject Result, int Port) Scaffold(string projectId, string slug, IEnumerable<(string Path, string Text)>? files = null, JsonObject? manifest = null)
    {
        var payload = ScaffoldPayload(projectId, slug, files: files, manifest: manifest);
        var result = Exec(ProjectCapabilityNames.ProjectScaffold, payload);
        return (result, payload["manifest"]!["port"]!.GetValue<int>());
    }

    public string FolderOf(string slug) => Path.Combine(ProjectsRoot, slug);

    /// <summary>A loopback port nothing is listening on right now.</summary>
    public static int FreePort()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        listener.Stop();
        return port;
    }

    public static bool IsAlive(int pid)
    {
        try
        {
            using var process = Process.GetProcessById(pid);
            return !process.HasExited;
        }
        catch (ArgumentException)
        {
            return false;
        }
    }

    /// <summary>
    /// True once the process OBJECT is signalled — not merely once <c>GetExitCodeProcess</c>
    /// stops saying STILL_ACTIVE, which happens before the handle table (a node child's
    /// working-directory handle on the project folder among it) is torn down.
    /// </summary>
    public static bool WaitForExit(int pid, TimeSpan wait)
    {
        try
        {
            using var process = Process.GetProcessById(pid);
            return process.WaitForExit((int)wait.TotalMilliseconds);
        }
        catch (ArgumentException)
        {
            return true;
        }
        catch (InvalidOperationException)
        {
            return true;
        }
    }

    public static async Task<string> GetAsync(string url)
    {
        using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(10) };
        return await http.GetStringAsync(url);
    }

    public void Dispose()
    {
        // Every job this lab's runner holds is ended (its own children only), then the folder.
        // A child ended a moment ago may still be giving its handles back (its working
        // directory IS a project folder), so the delete is retried for a few seconds.
        Projects.Dispose();
        var deadline = DateTime.UtcNow.AddSeconds(5);
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
