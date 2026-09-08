using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Operator;
using PagentOS.SessionCompanion.Documents;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Projects;

/// <summary>
/// <c>project.scaffold</c> (M23_APP_FACTORY_SPEC.md §3, ADR-0086 decision 2): the files, the
/// marker and a hash per path land under <c>Projects\&lt;slug&gt;</c> and nowhere else; an
/// outside path, an owner's folder, an oversize list and a manifest outside the runtime
/// allowlist are refused with nothing written and no process started. No runtime needed.
/// </summary>
[Collection(ProjectLabCollection.Name)]
public sealed class ProjectScaffoldTests
{
    [Fact]
    public void Scaffold_writes_the_files_the_marker_and_a_sha256_per_path_under_the_projects_root()
    {
        using var lab = new ProjectLab();
        var (result, port) = lab.Scaffold("proj-1", "gorev-takip");

        var folder = lab.FolderOf("gorev-takip");
        Assert.Equal(folder, result["root_path"]!.GetValue<string>(), StringComparer.OrdinalIgnoreCase);
        Assert.Equal("proj-1", result["project_id"]!.GetValue<string>());
        var files = ProjectLab.TemplateFiles();
        Assert.Equal(files.Count, result["files_written"]!.GetValue<int>());

        var hashes = (JsonObject)result["sha256_by_path"]!;
        Assert.Equal(files.Count, hashes.Count);
        foreach (var (path, text) in files)
        {
            var onDisk = Path.Combine(folder, path.Replace('/', Path.DirectorySeparatorChar));
            Assert.True(File.Exists(onDisk), $"{path} was not written");
            var bytes = File.ReadAllBytes(onDisk);
            Assert.Equal(text, Encoding.UTF8.GetString(bytes));
            Assert.Equal(Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant(), hashes[path]!.GetValue<string>());
        }

        // The marker names the project and carries the validated manifest; the state folder exists.
        var marker = ProjectRoots.ReadMarker(folder);
        Assert.NotNull(marker);
        Assert.Equal("proj-1", marker!.ProjectId);
        Assert.Equal("gorev-takip", marker.Slug);
        Assert.Equal(port, marker.Manifest["port"]!.GetValue<int>());
        Assert.Equal("index.html", marker.Manifest["entry"]!.GetValue<string>());
        Assert.True(Directory.Exists(Path.Combine(folder, ProjectRoots.StateFolderName)));
        Assert.Equal(port, result["manifest"]!["port"]!.GetValue<int>());

        // Only the project folder appeared under the root; nothing beside the root.
        Assert.Equal(new[] { folder }, Directory.GetDirectories(lab.ProjectsRoot), StringComparer.OrdinalIgnoreCase);
        Assert.Equal(new[] { "Projects" }, Directory.GetDirectories(lab.Root).Select(d => Path.GetFileName(d)!).ToArray());

        // The same project scaffolds again over its own files (an updated generation), never over another's.
        var again = lab.Exec(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-1", "gorev-takip", port));
        Assert.Equal(files.Count, again["files_written"]!.GetValue<int>());

        // status: scaffolded, never run.
        var status = lab.Exec(ProjectCapabilityNames.ProjectStatus, new JsonObject { ["project_id"] = "proj-1" });
        Assert.Equal("scaffolded", status["state"]!.GetValue<string>());
        Assert.Equal(port, status["port"]!.GetValue<int>());
        Assert.True(status["pid"] is null);
        Assert.True(lab.Log.Any("project.scaffold 'gorev-takip'"));
    }

    [Theory]
    [InlineData(@"..\evil.txt")]
    [InlineData("../evil.txt")]
    [InlineData(@"C:\evil.txt")]
    [InlineData(@"\\srv\share\evil.txt")]
    [InlineData("/evil.txt")]
    [InlineData("a/../evil.txt")]
    [InlineData("a//b.txt")]
    [InlineData("nul.txt")]
    [InlineData("con")]
    [InlineData("src/COM1.js")]
    [InlineData("trailing.")]
    [InlineData("trailing ")]
    [InlineData("bad.bat")]
    [InlineData("bad.cmd")]
    [InlineData("bad.ps1")]
    [InlineData("bad.lnk")]
    [InlineData("macro.docm")]
    [InlineData(".pagentos-project.json")]
    [InlineData(".pagentos/run.log")]
    [InlineData("a:b.txt")]
    [InlineData("tab\tname.txt")]
    [InlineData("a/b/c/d/e/f/g/h/i.txt")]
    [InlineData("src/token.js")]
    public void An_outside_reserved_or_launcher_path_in_the_list_is_refused_before_anything_is_written(string path)
    {
        using var lab = new ProjectLab();
        var files = ProjectLab.TemplateFiles();
        files.Add((path, "x"));
        var ex = lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-2", "refused", files: files));

        Assert.Equal(ErrorClasses.ValidationError, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Contains("payload.files[", ex.Message, StringComparison.Ordinal);
        Assert.False(Directory.Exists(lab.FolderOf("refused")), "nothing may be written when one path is refused");
        Assert.False(Directory.Exists(lab.ProjectsRoot), "the Projects root itself is not created for a refused list");
        Assert.False(File.Exists(Path.Combine(lab.Root, "evil.txt")));
    }

    [Fact]
    public void A_folder_without_the_marker_or_with_another_projects_marker_is_never_written_into()
    {
        using var lab = new ProjectLab();
        // The owner's own folder, by the slug the assistant would use.
        var owner = lab.FolderOf("owner-app");
        Directory.CreateDirectory(owner);
        File.WriteAllText(Path.Combine(owner, "notes.txt"), "the owner's notes\n");

        var ex = lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-3", "owner-app"));
        Assert.Equal(ErrorClasses.PermissionDenied, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Equal("owner_folder", ex.Detail[DocumentErrors.DetailKey]);
        Assert.Equal(new[] { "notes.txt" }, Directory.GetFileSystemEntries(owner).Select(e => Path.GetFileName(e)!).ToArray());
        Assert.Equal("the owner's notes\n", File.ReadAllText(Path.Combine(owner, "notes.txt")));

        // Another project's folder.
        lab.Scaffold("proj-a", "shared-slug");
        var other = lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-b", "shared-slug"));
        Assert.Equal(ErrorClasses.PermissionDenied, other.ErrorClass);
        Assert.Equal("other_project", other.Detail[DocumentErrors.DetailKey]);
        Assert.Equal("proj-a", ProjectRoots.ReadMarker(lab.FolderOf("shared-slug"))!.ProjectId);

        // An EMPTY folder without a marker has nothing of the owner's to lose: it is used.
        Directory.CreateDirectory(lab.FolderOf("empty-slug"));
        var used = lab.Exec(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-c", "empty-slug"));
        Assert.Equal(lab.FolderOf("empty-slug"), used["root_path"]!.GetValue<string>(), StringComparer.OrdinalIgnoreCase);

        // A marker that is not JSON is no marker.
        var broken = lab.FolderOf("broken-marker");
        Directory.CreateDirectory(broken);
        File.WriteAllText(Path.Combine(broken, ProjectRoots.MarkerFileName), "not json");
        var brokenRefusal = lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-d", "broken-marker"));
        Assert.Equal(ErrorClasses.PermissionDenied, brokenRefusal.ErrorClass);
        Assert.Equal("not json", File.ReadAllText(Path.Combine(broken, ProjectRoots.MarkerFileName)));
    }

    [Fact]
    public void An_oversize_list_is_refused_whole()
    {
        using var lab = new ProjectLab();
        var many = Enumerable.Range(0, ProjectCapabilityNames.MaxFiles + 1).Select(i => ($"f{i}.txt", "x")).ToList();
        var tooMany = lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-4", "too-many", files: many));
        Assert.Equal(ErrorClasses.ValidationError, tooMany.ErrorClass);
        Assert.Contains("201 files", tooMany.Message, StringComparison.Ordinal);

        var big = ProjectLab.TemplateFiles();
        big.Add(("big.txt", new string('a', (int)ProjectCapabilityNames.MaxTotalBytes)));
        var tooBig = lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-5", "too-big", files: big));
        Assert.Equal(ErrorClasses.ValidationError, tooBig.ErrorClass);
        Assert.Contains("2 MiB", tooBig.Message, StringComparison.Ordinal);

        // Not text: a NUL, a lone surrogate.
        var binary = ProjectLab.TemplateFiles();
        binary.Add(("blob.bin", "abc\0def"));
        Assert.Equal(ErrorClasses.ValidationError, lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-6", "binary", files: binary)).ErrorClass);
        var surrogate = ProjectLab.TemplateFiles();
        surrogate.Add(("bad.txt", "abc\uD800def"));
        Assert.Equal(ErrorClasses.ValidationError, lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-7", "surrogate", files: surrogate)).ErrorClass);

        // A duplicate path (differing in case only).
        var duplicate = ProjectLab.TemplateFiles();
        duplicate.Add(("INDEX.html", "x"));
        Assert.Equal(ErrorClasses.ValidationError, lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-8", "duplicate", files: duplicate)).ErrorClass);

        Assert.False(Directory.Exists(lab.ProjectsRoot));
    }

    [Theory]
    [InlineData("../x")]
    [InlineData(@"..\x")]
    [InlineData("My App")]
    [InlineData("UPPER")]
    [InlineData("-leading")]
    [InlineData("con")]
    [InlineData("a.b")]
    [InlineData("")]
    public void A_slug_that_is_not_a_plain_name_is_refused(string slug)
    {
        using var lab = new ProjectLab();
        var ex = lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-9", slug));
        Assert.Equal(ErrorClasses.ValidationError, ex.ErrorClass);
        Assert.Contains("payload.slug", ex.Message, StringComparison.Ordinal);
        Assert.False(Directory.Exists(lab.ProjectsRoot));

        var tooLong = lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-9", new string('a', 65)));
        Assert.Equal(ErrorClasses.ValidationError, tooLong.ErrorClass);
        Assert.Equal(ErrorClasses.ValidationError, lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("bad id/", "ok-slug")).ErrorClass);
    }

    [Fact]
    public void A_junction_inside_a_project_cannot_redirect_a_write_outside_it()
    {
        using var lab = new ProjectLab();
        lab.Scaffold("proj-10", "linked");
        var outside = Path.Combine(lab.Root, "outside");
        Directory.CreateDirectory(outside);
        var link = Path.Combine(lab.FolderOf("linked"), "out");
        JunctionFixture.CreateJunction(link, outside);
        try
        {
            var files = ProjectLab.TemplateFiles();
            files.Add(("out/planted.txt", "through the link"));
            var ex = lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-10", "linked", files: files));

            Assert.Equal(ErrorClasses.PermissionDenied, ex.ErrorClass);
            Assert.Contains("resolves outside the project folder", ex.Message, StringComparison.Ordinal);
            Assert.False(File.Exists(Path.Combine(outside, "planted.txt")), "the write went through the junction");

            // And a project folder that is itself a junction to somewhere else is refused whole.
            var elsewhere = Path.Combine(lab.Root, "elsewhere");
            Directory.CreateDirectory(elsewhere);
            var folderLink = lab.FolderOf("jump");
            JunctionFixture.CreateJunction(folderLink, elsewhere);
            try
            {
                var jump = lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-11", "jump"));
                Assert.Equal(ErrorClasses.PermissionDenied, jump.ErrorClass);
                Assert.Empty(Directory.GetFileSystemEntries(elsewhere));
            }
            finally
            {
                Directory.Delete(folderLink);
            }
        }
        finally
        {
            Directory.Delete(link);
        }
    }

    [Theory]
    [InlineData("powershell -Command Remove-Item x")]
    [InlineData("cmd /c dir")]
    [InlineData("python evil.py")]
    [InlineData("python -m http.server 80 --bind 0.0.0.0")]
    [InlineData("python -m http.server <port> --bind 0.0.0.0")]
    [InlineData("python -m http.server <port>")]
    [InlineData("node ../evil.js")]
    [InlineData("node C:/evil.js")]
    [InlineData("node app.js")]
    [InlineData("node index.html; whoami")]
    [InlineData("node index.html | whoami")]
    [InlineData("node index.html --inspect")]
    [InlineData("npm --prefix <root> run start")]
    [InlineData("npm --prefix C:/x run start")]
    [InlineData("npx serve")]
    [InlineData("")]
    public void A_manifest_with_a_non_allowlisted_run_command_is_refused_before_any_process(string command)
    {
        var started = 0;
        using var lab = new ProjectLab(start: info =>
        {
            Interlocked.Increment(ref started);
            return Process.Start(info);
        });
        var manifest = ProjectLab.TemplateManifest(ProjectLab.FreePort());
        manifest["run"] = new JsonObject { ["serve"] = command };
        var ex = lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-12", "bad-run", manifest: manifest));

        Assert.Equal(ErrorClasses.PermissionDenied, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Equal("command_not_allowlisted", ex.Detail[DocumentErrors.DetailKey]);
        Assert.Contains("nothing was run", ex.Message, StringComparison.Ordinal);
        Assert.Equal(0, started);
        Assert.Equal(0, lab.Runner.ProcessesStarted);
        Assert.False(Directory.Exists(lab.ProjectsRoot), "a refused manifest writes nothing");
    }

    [Fact]
    public void The_allowlist_admits_exactly_the_three_runtime_forms_and_npm_only_with_a_lockfile()
    {
        Assert.True(ProjectManifest.IsAllowlisted("python -m http.server <port> --bind 127.0.0.1", "index.html", 8123, hasLockfile: false, isTest: false));
        Assert.True(ProjectManifest.IsAllowlisted("python -m http.server 8123 --bind 127.0.0.1", "index.html", 8123, hasLockfile: false, isTest: false));
        Assert.False(ProjectManifest.IsAllowlisted("python -m http.server 8124 --bind 127.0.0.1", "index.html", 8123, hasLockfile: false, isTest: false));
        Assert.False(ProjectManifest.IsAllowlisted("python -m http.server <port> --bind 127.0.0.1", "index.html", 8123, hasLockfile: false, isTest: true));
        Assert.True(ProjectManifest.IsAllowlisted("node server.js", "server.js", 8123, hasLockfile: false, isTest: false));
        Assert.False(ProjectManifest.IsAllowlisted("node other.js", "server.js", 8123, hasLockfile: false, isTest: false));
        Assert.True(ProjectManifest.IsAllowlisted("node tests/run.js", "index.html", 8123, hasLockfile: false, isTest: true));
        Assert.False(ProjectManifest.IsAllowlisted("npm --prefix <root> run start", "index.html", 8123, hasLockfile: false, isTest: false));
        Assert.True(ProjectManifest.IsAllowlisted("npm --prefix <root> run start", "index.html", 8123, hasLockfile: true, isTest: false));
        Assert.True(ProjectManifest.IsAllowlisted("npm --prefix <root> run test", "index.html", 8123, hasLockfile: true, isTest: true));
        Assert.False(ProjectManifest.IsAllowlisted("npm --prefix <root> run build", "index.html", 8123, hasLockfile: true, isTest: false));
        Assert.False(ProjectManifest.IsAllowlisted("npm --prefix <root> run start", "index.html", 8123, hasLockfile: true, isTest: true));
        Assert.False(ProjectManifest.IsAllowlisted("Node server.js && whoami", "server.js", 8123, hasLockfile: false, isTest: false));

        // Through the scaffold: npm admitted only when the file list ships the lockfile.
        using var lab = new ProjectLab();
        var manifest = ProjectLab.TemplateManifest(ProjectLab.FreePort());
        manifest["run"] = new JsonObject { ["start"] = "npm --prefix <root> run start" };
        var files = ProjectLab.TemplateFiles();
        files.Add((ProjectManifest.LockfileName, "{\"name\":\"x\",\"lockfileVersion\":3}"));
        files.Add(("package.json", "{\"name\":\"x\",\"scripts\":{\"start\":\"node index.html\"}}"));
        var result = lab.Exec(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-13", "npm-app", files: files, manifest: manifest));
        Assert.True(result["manifest"]!["lockfile"]!.GetValue<bool>());
        Assert.Equal("npm --prefix <root> run start", result["manifest"]!["run"]!["start"]!.GetValue<string>());

        // A manifest whose entry, port or keys are malformed is validation_error.
        var noEntry = ProjectLab.TemplateManifest(ProjectLab.FreePort());
        noEntry.Remove("entry");
        Assert.Equal(ErrorClasses.ValidationError, lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-14", "no-entry", manifest: noEntry)).ErrorClass);
        var lowPort = ProjectLab.TemplateManifest(80);
        Assert.Equal(ErrorClasses.ValidationError, lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-15", "low-port", manifest: lowPort)).ErrorClass);
        var missingEntry = ProjectLab.TemplateManifest(ProjectLab.FreePort());
        missingEntry["entry"] = "nowhere.html";
        Assert.Equal(ErrorClasses.ValidationError, lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-16", "no-such-entry", manifest: missingEntry)).ErrorClass);
        var badKey = ProjectLab.TemplateManifest(ProjectLab.FreePort());
        badKey["run"] = new JsonObject { ["Serve Now"] = "python -m http.server <port> --bind 127.0.0.1" };
        Assert.Equal(ErrorClasses.ValidationError, lab.ExpectFailure(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("proj-17", "bad-key", manifest: badKey)).ErrorClass);
    }

    [Fact]
    public void A_marker_tampered_into_a_non_allowlisted_command_is_refused_at_run_before_any_process()
    {
        var started = 0;
        using var lab = new ProjectLab(start: info =>
        {
            Interlocked.Increment(ref started);
            return Process.Start(info);
        });
        lab.Scaffold("proj-18", "tampered");
        var folder = lab.FolderOf("tampered");
        var marker = ProjectRoots.ReadMarker(folder)!;
        marker.Manifest["run"] = new JsonObject { ["serve"] = "powershell -Command Remove-Item x" };
        ProjectRoots.WriteMarker(folder, marker);

        var run = lab.ExpectFailure(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "proj-18" });
        Assert.Equal(ErrorClasses.PermissionDenied, run.ErrorClass);
        Assert.Equal("command_not_allowlisted", run.Detail[DocumentErrors.DetailKey]);
        var test = lab.ExpectFailure(ProjectCapabilityNames.ProjectTest, new JsonObject { ["project_id"] = "proj-18" });
        Assert.Equal(ErrorClasses.PermissionDenied, test.ErrorClass);
        Assert.Equal(0, started);
        Assert.Equal(0, lab.Runner.ProcessesStarted);

        // An id nobody scaffolded is not_found on every name; a wrong key is validation_error.
        foreach (var name in new[] { ProjectCapabilityNames.ProjectRun, ProjectCapabilityNames.ProjectStatus, ProjectCapabilityNames.ProjectStop, ProjectCapabilityNames.ProjectTest })
        {
            var unknown = lab.ExpectFailure(name, new JsonObject { ["project_id"] = "nobody" });
            Assert.Equal(ErrorClasses.NotFound, unknown.ErrorClass);
            Assert.False(unknown.Retryable);
        }

        lab.Scaffold("proj-19", "keys");
        var wrongKey = lab.ExpectFailure(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "proj-19", ["command_key"] = "nope" });
        Assert.Equal(ErrorClasses.ValidationError, wrongKey.ErrorClass);
        Assert.Contains("command_key", wrongKey.Message, StringComparison.Ordinal);
        Assert.Equal(0, started);

        // A companion with the family disabled answers capability_missing and writes nothing.
        using var off = new ProjectLab(enabled: false);
        Assert.False(off.Projects.Enabled);
        Assert.Equal(ErrorClasses.CapabilityMissing, off.ExpectFailure("project.delete", new JsonObject { ["project_id"] = "x" }).ErrorClass);
    }
}
