using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.SessionCompanion.Operator;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Native;

/// <summary>
/// M28_NATIVE_APP_FACTORY_SPEC.md §5, ADR-0095 decision 4 — the native root, and the two
/// halves that have to agree about it.
///
/// The cross-half tests here read <c>services/api/app/nativefactory/roots.py</c> and
/// <c>service.py</c> rather than restating their values. This repository has a hard-won rule
/// about that: both suites can be green while the two sides drift, and the only test that
/// catches a rename is one that reads the OTHER side's source.
/// </summary>
[Collection(NativeLabCollection.Name)]
public sealed class NativeRootTests
{
    private static string RepoRoot()
        => new DirectoryInfo(CompanionSources.Directory()).Parent!.Parent!.Parent!.Parent!.FullName;

    private static string CloudCoreSource(string fileName)
        => File.ReadAllText(Path.Combine(RepoRoot(), "services", "api", "app", "nativefactory", fileName));

    [Fact]
    public void The_root_name_is_the_one_the_Cloud_Core_declares_read_from_its_own_source()
    {
        // NATIVE_SUBDIR: Final = "native"
        var roots = CloudCoreSource("roots.py");
        var subdir = Regex.Match(roots, @"^NATIVE_SUBDIR\s*:\s*Final\s*=\s*""([^""]+)""", RegexOptions.Multiline);
        Assert.True(subdir.Success, "app/nativefactory/roots.py no longer declares NATIVE_SUBDIR in the shape this test reads; the two halves must be re-reconciled by hand");
        // The Cloud Core's value is the EXPECTED one here on purpose: this test exists to catch
        // a rename on that side, so its source is what the device's constant is judged against.
        Assert.True(
            string.Equals(subdir.Groups[1].Value, NativeCapabilityNames.RootNativeFolderName, StringComparison.Ordinal),
            $"the Cloud Core builds under '{subdir.Groups[1].Value}' and this device admits '{NativeCapabilityNames.RootNativeFolderName}'");

        // PROJECTS_FOLDER: Final = "PagentOS Projects" — the folder the native root hangs under.
        var projects = Regex.Match(roots, @"^PROJECTS_FOLDER\s*:\s*Final\s*=\s*""([^""]+)""", RegexOptions.Multiline);
        Assert.True(projects.Success, "app/nativefactory/roots.py no longer declares PROJECTS_FOLDER in the shape this test reads");
        Assert.True(
            string.Equals(projects.Groups[1].Value, OperatorOptions.ProjectsFolderName, StringComparison.Ordinal),
            $"the Cloud Core's Projects folder is '{projects.Groups[1].Value}' and this device's is '{OperatorOptions.ProjectsFolderName}'");

        // And therefore the whole path agrees, which is the thing that actually matters: the
        // Cloud Core will not ask the device to build anywhere the device would refuse.
        Assert.Equal(
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), projects.Groups[1].Value, subdir.Groups[1].Value),
            OperatorOptions.DefaultNativeRoot(OperatorOptions.DefaultProjectsRoot()));
    }

    [Fact]
    public void The_build_bound_is_the_one_the_Cloud_Core_gives_its_runner_read_from_its_own_source()
    {
        // BUILD_TIMEOUT_S: Final = 20 * 60. A device bound SHORTER than the Cloud Core's would
        // have the device kill a build the Cloud Core is still waiting on and report a timeout
        // the Cloud Core cannot explain; a device bound LONGER would have the Cloud Core give
        // up on a build still running under a job. They have to be the same number.
        var service = CloudCoreSource("service.py");
        var timeout = Regex.Match(service, @"^BUILD_TIMEOUT_S\s*:\s*Final\s*=\s*(\d+)\s*\*\s*(\d+)", RegexOptions.Multiline);
        Assert.True(timeout.Success, "app/nativefactory/service.py no longer declares BUILD_TIMEOUT_S in the shape this test reads");
        var seconds = int.Parse(timeout.Groups[1].Value) * int.Parse(timeout.Groups[2].Value);
        Assert.Equal(seconds, (int)NativeCapabilityNames.RunLimit.TotalSeconds);
    }

    [Fact]
    public void The_native_root_is_an_authorised_root_and_lives_under_the_Projects_root()
    {
        using var lab = new NativeLab();
        Assert.Equal(Path.Combine(lab.ProjectsRoot, NativeCapabilityNames.RootNativeFolderName), lab.ProjectsRootNative);
        Assert.Contains(lab.ProjectsRootNative, lab.Options.NativeOptions().AuthorisedRoots);

        // It is a sibling of the 3D root and never the same folder: a compiler and an editor
        // are different trust decisions and they do not share a directory.
        Assert.NotEqual(lab.Projects.ProjectsRoot3d, lab.ProjectsRootNative);
    }

    [Fact]
    public void A_web_project_may_not_be_called_native_because_that_folder_IS_the_root()
    {
        using var lab = new NativeLab();
        var failure = lab.ExpectFailure(
            ProjectCapabilityNames.ProjectScaffold,
            new JsonObject
            {
                ["project_id"] = "web-native",
                ["slug"] = NativeCapabilityNames.RootNativeFolderName,
                ["files"] = new JsonArray(new JsonObject { ["path"] = "index.js", ["text"] = "console.log(1);\n" }),
                ["manifest"] = new JsonObject
                {
                    ["entry"] = "index.js",
                    ["port"] = 8123,
                    ["run"] = new JsonObject { ["start"] = "node index.js" },
                },
            });
        Assert.Equal(ErrorClasses.ValidationError, failure.ErrorClass);
        Assert.Contains("native root's own folder", failure.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void The_root_vocabulary_is_three_closed_words_and_never_a_path()
    {
        using var lab = new NativeLab();
        foreach (var word in new[] { @"C:\Users\owner", "../native", "NATIVE", "nativ", string.Empty, "3D" })
        {
            var failure = lab.ExpectFailure(
                ProjectCapabilityNames.ProjectScaffold,
                NativeLab.ScaffoldNativePayload(
                    "native-word",
                    "word",
                    new Dictionary<string, string> { ["build"] = NativeLab.DotnetCommand("build") },
                    root: word));
            Assert.Equal(ErrorClasses.ValidationError, failure.ErrorClass);
        }

        // Read the other way, so the word the result echoes cannot drift from the word the
        // payload takes.
        Assert.Equal(NativeCapabilityNames.RootNativeFolderName, ProjectScaffold.ScopeWord(ProjectScope.Native));
        Assert.Equal(SceneCapabilityNames.Root3dFolderName, ProjectScaffold.ScopeWord(ProjectScope.ThreeD));
        Assert.Equal(ProjectScaffold.WebScopeWord, ProjectScaffold.ScopeWord(ProjectScope.Web));
        Assert.Equal(ProjectScope.Native, ProjectScaffold.ReadScope(new JsonObject { ["root"] = NativeCapabilityNames.RootNativeFolderName }));
    }

    [Fact]
    public void Containment_is_resolve_then_contain_so_a_junction_out_of_the_root_is_not_a_project()
    {
        using var lab = new NativeLab();
        var folder = lab.ScaffoldNative(
            "native-contained",
            "contained",
            run: new Dictionary<string, string> { ["build"] = NativeLab.DotnetCommand("build") });

        // The folder the scaffold reports resolves DIRECTLY under the resolved native root.
        var resolved = ProjectRoots.ConfineProjectFolder(folder, AuthorisedRoots.ResolveFinal(lab.ProjectsRootNative)!);
        Assert.NotNull(resolved);

        // A directory beside the root — a plausible mistake, and the one containment exists for
        // — is not a project of it however its name is spelled.
        var outside = Path.Combine(lab.Root, "elsewhere");
        Directory.CreateDirectory(outside);
        Assert.Null(ProjectRoots.ConfineProjectFolder(outside, AuthorisedRoots.ResolveFinal(lab.ProjectsRootNative)!));

        // And a path composed to climb out is resolved BEFORE it is compared, which is why the
        // string comparison it would have beaten never happens.
        var climbed = Path.Combine(lab.ProjectsRootNative, "contained", "..", "..", "elsewhere");
        Assert.Null(ProjectRoots.ConfineProjectFolder(climbed, AuthorisedRoots.ResolveFinal(lab.ProjectsRootNative)!));
    }

    [Fact]
    public void A_project_id_is_found_in_the_native_root_and_a_native_project_has_no_scene_to_inspect()
    {
        using var lab = new NativeLab();
        lab.ScaffoldNative(
            "native-found",
            "found",
            run: new Dictionary<string, string> { ["build"] = NativeLab.DotnetCommand("build") });

        var status = lab.Exec(ProjectCapabilityNames.ProjectStatus, new JsonObject { ["project_id"] = "native-found" });
        Assert.Equal("found", status["slug"]!.GetValue<string>());
        Assert.Equal("scaffolded", status["state"]!.GetValue<string>());
        Assert.Equal(lab.FolderNativeOf("found"), status["root_path"]!.GetValue<string>(), StringComparer.OrdinalIgnoreCase);

        // M25's read-back is for 3D projects. A native project is not one, and the answer says
        // so rather than half-answering.
        var inspect = lab.ExpectFailure(SceneCapabilityNames.Inspect, new JsonObject { ["project_id"] = "native-found" });
        Assert.Equal(ErrorClasses.NotFound, inspect.ErrorClass);

        var unknown = lab.ExpectFailure(ProjectCapabilityNames.ProjectStatus, new JsonObject { ["project_id"] = "native-nobody" });
        Assert.Equal(ErrorClasses.NotFound, unknown.ErrorClass);
    }
}
