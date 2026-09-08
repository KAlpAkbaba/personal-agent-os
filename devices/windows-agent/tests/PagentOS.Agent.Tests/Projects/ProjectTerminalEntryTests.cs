using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Operator;
using PagentOS.SessionCompanion.Operator;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Projects;

/// <summary>
/// M23_APP_FACTORY_SPEC.md §4: the terminal allowlist's ONE project-scoped entry,
/// <c>node &lt;project-entry&gt; --help</c>. The token admits exactly a scaffolded project's own
/// manifest entry — resolved under the Projects root, in a folder with the marker — and
/// nothing else the owner keeps: another file of the project, a script beside the root, a
/// folder without the marker, a different argument.
/// </summary>
[Collection(ProjectLabCollection.Name)]
public sealed class ProjectTerminalEntryTests
{
    private const string CliSource = "const args = process.argv.slice(2);\nif (args.includes('--help')) { console.log('usage: cli.js [--help]'); process.exit(0); }\nconsole.log('cli ran');\n";

    private static JsonObject CliManifest(int port)
        => new()
        {
            ["entry"] = "cli.js",
            ["port"] = port,
            ["run"] = new JsonObject { ["serve"] = "node cli.js" },
            ["test"] = new JsonObject { ["unit"] = "node tests/run.js" },
        };

    private static ProjectLab ScaffoldCli(out string entryPath)
    {
        var lab = new ProjectLab();
        var port = ProjectLab.FreePort();
        var files = ProjectLab.TemplateFiles();
        files.Add(("cli.js", CliSource));
        lab.Exec(ProjectCapabilityNames.ProjectScaffold, ProjectLab.ScaffoldPayload("cli-1", "cli-tool", port, files, CliManifest(port)));
        entryPath = Path.Combine(lab.FolderOf("cli-tool"), "cli.js");
        return lab;
    }

    [Fact]
    public void The_default_allowlist_carries_the_one_project_entry_and_it_admits_only_a_projects_manifest_entry()
    {
        Assert.Contains(TerminalRunner.ProjectHelpEntry, TerminalRunner.DefaultAllowlist);
        Assert.Equal("node <project-entry> --help", TerminalRunner.ProjectHelpEntry);

        using var lab = ScaffoldCli(out var entry);
        var operatorCapabilities = new OperatorCapabilities(lab.Options, lab.Log);
        var runner = operatorCapabilities.Terminal;

        Assert.Equal(TerminalRunner.ProjectHelpEntry, runner.Authorise($"node \"{entry}\" --help"));
        Assert.Equal(TerminalRunner.ProjectHelpEntry, runner.Authorise($"NODE {entry} --help"));

        // Another file of the same project is not its entry.
        Assert.Null(runner.Authorise($"node \"{Path.Combine(lab.FolderOf("cli-tool"), "app.js")}\" --help"));
        // A different argument, or none.
        Assert.Null(runner.Authorise($"node \"{entry}\" --version"));
        Assert.Null(runner.Authorise($"node \"{entry}\""));
        Assert.Null(runner.Authorise($"node \"{entry}\" --help extra"));
        // A relative spelling, a missing file.
        Assert.Null(runner.Authorise("node cli.js --help"));
        Assert.Null(runner.Authorise($"node \"{Path.Combine(lab.FolderOf("cli-tool"), "missing.js")}\" --help"));

        // A script beside the Projects root, inside the authorised root — still not a project's entry.
        var beside = Path.Combine(lab.Root, "cli.js");
        File.WriteAllText(beside, CliSource);
        Assert.Null(runner.Authorise($"node \"{beside}\" --help"));

        // A folder under Projects WITHOUT the marker (the owner's), with a cli.js of its own.
        var owner = lab.FolderOf("owner-cli");
        Directory.CreateDirectory(owner);
        var ownerEntry = Path.Combine(owner, "cli.js");
        File.WriteAllText(ownerEntry, CliSource);
        Assert.Null(runner.Authorise($"node \"{ownerEntry}\" --help"));

        // The marker gone: the entry is no longer anybody's.
        File.Delete(Path.Combine(lab.FolderOf("cli-tool"), ProjectRoots.MarkerFileName));
        Assert.Null(runner.Authorise($"node \"{entry}\" --help"));
        Assert.Equal(0, runner.ProcessesStarted);
    }

    [Fact]
    public void A_junction_under_the_projects_root_is_never_a_project()
    {
        using var lab = ScaffoldCli(out _);
        var elsewhere = Path.Combine(lab.Root, "elsewhere");
        Directory.CreateDirectory(elsewhere);
        File.WriteAllText(Path.Combine(elsewhere, "cli.js"), CliSource);
        ProjectRoots.WriteMarker(elsewhere, new ProjectMarker("planted", "planted", "2026-09-08T00:00:00Z", CliManifest(8123)));
        var link = lab.FolderOf("planted");
        JunctionFixture.CreateJunction(link, elsewhere);
        try
        {
            var runner = new OperatorCapabilities(lab.Options, lab.Log).Terminal;
            Assert.Null(runner.Authorise($"node \"{Path.Combine(link, "cli.js")}\" --help"));
            Assert.Null(lab.Projects.Roots.Find("planted"));
            Assert.Equal(ErrorClasses.NotFound, lab.ExpectFailure(ProjectCapabilityNames.ProjectStatus, new JsonObject { ["project_id"] = "planted" }).ErrorClass);
        }
        finally
        {
            Directory.Delete(link);
        }
    }

    [ProjectLabFact("node")]
    public async Task The_entry_runs_headless_through_terminal_execute_and_prints_its_help()
    {
        using var lab = ScaffoldCli(out var entry);
        var operatorCapabilities = new OperatorCapabilities(lab.Options, lab.Log);
        var result = await operatorCapabilities.ExecuteAsync(
            OperatorCapabilityNames.TerminalExecute,
            new JsonObject { ["command"] = $"node \"{entry}\" --help", ["timeout_s"] = 30 },
            TimeSpan.FromSeconds(30),
            CancellationToken.None);

        Assert.Equal(0, result["exit_code"]!.GetValue<int>());
        Assert.Contains("usage: cli.js", result["stdout"]!.GetValue<string>(), StringComparison.Ordinal);
        Assert.Equal(TerminalRunner.ProjectHelpEntry, result["matched"]!.GetValue<string>());
    }
}
