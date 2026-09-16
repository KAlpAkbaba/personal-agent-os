using System.Diagnostics;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Documents;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Native;

/// <summary>
/// M28_NATIVE_APP_FACTORY_SPEC.md §5/§9, ADR-0095 decision 4 — the four native argv shapes,
/// and everything that is not one of them. The discipline is M23's, unchanged: the match is
/// TOKEN FOR TOKEN against a fixed list, never a string prefix, and the verdict is reached
/// before any process exists. Every hostile variant below is refused with a counting seam in
/// place of <c>Process.Start</c>, and the counter is 0 at the end: the refusals cost nothing
/// but a parse.
///
/// These tests need no toolchain installed — that is the point. The allowlist is a property of
/// the code, not of what happens to be on the machine.
/// </summary>
[Collection(NativeLabCollection.Name)]
public sealed class NativeAllowlistTests
{
    private const string Dotnet = NativeCapabilityNames.DotnetProgram;
    private const string MakeAppx = NativeCapabilityNames.MakeAppxProgram;
    private const string Release = NativeCapabilityNames.Configuration;
    private const string Rid = NativeCapabilityNames.RuntimeIdentifier;
    private const string Project = NativeLab.ProjectFileName;

    /// <summary>A <c>Process.Start</c> that counts and never starts anything — a refusal that reaches it would show up as a non-zero count.</summary>
    private sealed class CountingStart
    {
        public int Calls { get; private set; }

        public Process? Start(ProcessStartInfo startInfo)
        {
            Calls++;
            return null;
        }
    }

    [Fact]
    public void The_four_shapes_are_admitted_under_the_native_root_and_stored_as_an_argument_list()
    {
        var counter = new CountingStart();
        using var lab = new NativeLab(start: counter.Start);

        var folder = lab.ScaffoldNative(
            "native-notes",
            "notlarim",
            run: new Dictionary<string, string>
            {
                ["build"] = NativeLab.DotnetCommand("build"),
                ["publish"] = NativeLab.PublishCommand(),
                ["package"] = NativeLab.PackCommand(),
            },
            test: new Dictionary<string, string> { ["test"] = NativeLab.DotnetCommand("test", NativeLab.TestProjectFileName) });

        Assert.Equal(lab.FolderNativeOf("notlarim"), folder, StringComparer.OrdinalIgnoreCase);
        Assert.StartsWith(lab.ProjectsRootNative, folder, StringComparison.OrdinalIgnoreCase);
        Assert.True(File.Exists(Path.Combine(folder, "app", "app.csproj")));

        var manifest = ProjectManifest.Parse(ProjectRoots.ReadMarker(folder)!.Manifest, null, ProjectScope.Native);

        // A native manifest may carry no port at all: every one of its commands is a batch run
        // and nothing binds one.
        Assert.Equal(ProjectManifest.NoPort, manifest.Port);

        var build = manifest.Run["build"];
        Assert.Equal(ProjectRuntime.Dotnet, build.Runtime);
        Assert.True(build.IsBatch);
        Assert.Equal(["build", Project, "-c", Release], build.Arguments);

        var publish = manifest.Run["publish"];
        Assert.Equal(ProjectRuntime.Dotnet, publish.Runtime);
        Assert.Equal(
            ["publish", Project, "-c", Release, "-r", Rid, "--self-contained", "true", "-o", NativeLab.PublishDirName],
            publish.Arguments);

        var package = manifest.Run["package"];
        Assert.Equal(ProjectRuntime.MakeAppx, package.Runtime);
        Assert.True(package.IsBatch);
        Assert.Equal(["pack", "/d", NativeLab.PackageSourceDirName, "/p", NativeLab.PackageFileName, "/o", "/nv"], package.Arguments);

        var test = manifest.Test["test"];
        Assert.Equal(ProjectRuntime.Dotnet, test.Runtime);
        Assert.Equal(["test", NativeLab.TestProjectFileName, "-c", Release], test.Arguments);

        // Nothing here carries a placeholder, so materialising against the folder changes
        // nothing: every path is relative and the child's working directory IS the folder.
        Assert.Equal(publish.Arguments, publish.Materialise(folder));

        Assert.Equal(0, counter.Calls);
    }

    [Theory]
    // another program entirely
    [InlineData("msbuild app/app.csproj -c Release")]
    [InlineData("cmd /c dotnet build")]
    [InlineData("powershell -c dotnet build")]
    [InlineData("dotnet.exe build app/app.csproj -c Release")]
    // a verb that is not one of the four
    [InlineData("dotnet run app/app.csproj -c Release")]
    [InlineData("dotnet restore app/app.csproj -c Release")]
    [InlineData("dotnet nuget add source https://example.invalid/x -c Release")]
    [InlineData("dotnet tool install --global something -c Release")]
    [InlineData("dotnet exec app/app.csproj -c Release")]
    [InlineData("makeappx unpack /d staging /p dist/app.msix /o /nv")]
    [InlineData("makeappx bundle /d staging /p dist/app.msix /o /nv")]
    // a configuration that is not Release
    [InlineData("dotnet build app/app.csproj -c Debug")]
    [InlineData("dotnet build app/app.csproj -c")]
    // a runtime identifier that is not win-x64
    [InlineData("dotnet publish app/app.csproj -c Release -r linux-x64 --self-contained true -o out")]
    [InlineData("dotnet publish app/app.csproj -c Release -r win-x64 --self-contained false -o out")]
    // an extra or a missing token
    [InlineData("dotnet build app/app.csproj -c Release --nologo")]
    [InlineData("dotnet build app/app.csproj")]
    [InlineData("dotnet build -c Release")]
    [InlineData("dotnet publish app/app.csproj -c Release -r win-x64 --self-contained true -o out extra")]
    [InlineData("makeappx pack /d staging /p dist/app.msix /o")]
    [InlineData("makeappx pack /d staging /p dist/app.msix /o /nv /l")]
    // an MSBuild property, which is a command line a caller composed by another name
    [InlineData("dotnet build app/app.csproj -c Release -p:PreBuildEvent=calc.exe")]
    // a path that is not inside the project, or not the right kind of thing
    [InlineData(@"dotnet build C:\Users\owner\secret\secret.csproj -c Release")]
    [InlineData("dotnet build ../../owner/owner.csproj -c Release")]
    [InlineData("dotnet build app/app.sln -c Release")]
    [InlineData("dotnet build app -c Release")]
    [InlineData("makeappx pack /d ../.. /p dist/app.msix /o /nv")]
    [InlineData(@"makeappx pack /d staging /p C:\Users\owner\Desktop\app.msix /o /nv")]
    [InlineData("makeappx pack /d staging /p dist/app.zip /o /nv")]
    // the companion's own folder inside the project
    [InlineData("dotnet publish app/app.csproj -c Release -r win-x64 --self-contained true -o .pagentos")]
    [InlineData("makeappx pack /d .pagentos/out /p dist/app.msix /o /nv")]
    // a shell composition
    [InlineData("dotnet build app/app.csproj -c Release && calc")]
    [InlineData("dotnet build app/app.csproj -c Release; calc")]
    [InlineData("dotnet build \"app/app.csproj\" -c Release")]
    // the signing tools, which this device never runs at all
    [InlineData("signtool sign /f cert.pfx dist/app.msix")]
    [InlineData("signtool.exe sign /a dist/app.msix")]
    [InlineData("certutil -importpfx cert.pfx")]
    [InlineData("makecert -r -pe -n CN=Owner cert.cer")]
    public void A_command_that_is_not_one_of_the_four_is_refused_before_any_process_exists(string command)
    {
        var counter = new CountingStart();
        using var lab = new NativeLab(start: counter.Start);

        var failure = lab.ExpectFailure(
            ProjectCapabilityNames.ProjectScaffold,
            NativeLab.ScaffoldNativePayload("native-hostile", "hostile", new Dictionary<string, string> { ["build"] = command }));

        Assert.Equal(ErrorClasses.PermissionDenied, failure.ErrorClass);
        Assert.False(failure.Retryable);
        Assert.Equal("command_not_allowlisted", failure.Detail[DocumentErrors.DetailKey]);
        Assert.False(Directory.Exists(lab.FolderNativeOf("hostile")), "a refused manifest writes nothing");
        Assert.Equal(0, counter.Calls);
    }

    [Fact]
    public void A_signing_tool_is_refused_by_NAME_and_says_so_rather_than_saying_the_shape_was_wrong()
    {
        var counter = new CountingStart();
        using var lab = new NativeLab(start: counter.Start);

        foreach (var program in NativeCapabilityNames.ForbiddenPrograms)
        {
            var failure = lab.ExpectFailure(
                ProjectCapabilityNames.ProjectScaffold,
                NativeLab.ScaffoldNativePayload("native-sign", "sign", new Dictionary<string, string> { ["build"] = $"{program} whatever" }));
            Assert.Equal(ErrorClasses.PermissionDenied, failure.ErrorClass);
            Assert.Contains("runs no signing program", failure.Message, StringComparison.Ordinal);
        }

        Assert.Equal(0, counter.Calls);
    }

    [Fact]
    public void The_native_shapes_are_refused_in_the_Projects_root_and_in_the_3d_root()
    {
        var counter = new CountingStart();
        using var lab = new NativeLab(start: counter.Start);

        foreach (var scope in new[] { ProjectScope.Web, ProjectScope.ThreeD })
        {
            foreach (var command in new[] { NativeLab.DotnetCommand("build"), NativeLab.PublishCommand(), NativeLab.PackCommand() })
            {
                var failure = Assert.Throws<CapabilityException>(() => ProjectManifest.Parse(
                    new JsonObject
                    {
                        ["entry"] = NativeLab.ProjectFileName,
                        ["port"] = 8080,
                        ["run"] = new JsonObject { ["build"] = command },
                    },
                    filePaths: null,
                    scope));
                Assert.Equal(ErrorClasses.PermissionDenied, failure.ErrorClass);
                Assert.Contains("compiler runs only under", failure.Message, StringComparison.Ordinal);
            }
        }

        Assert.Equal(0, counter.Calls);
    }

    [Fact]
    public void The_web_runtimes_are_refused_under_the_native_root()
    {
        var counter = new CountingStart();
        using var lab = new NativeLab(start: counter.Start);

        foreach (var command in new[] { "python -m http.server 8080 --bind 127.0.0.1", "node app.js", $"npm --prefix {ProjectManifest.RootPlaceholder} run start" })
        {
            var failure = lab.ExpectFailure(
                ProjectCapabilityNames.ProjectScaffold,
                NativeLab.ScaffoldNativePayload("native-web", "web", new Dictionary<string, string> { ["run"] = command }));
            Assert.Equal(ErrorClasses.PermissionDenied, failure.ErrorClass);
            Assert.Contains(NativeCapabilityNames.RootNativeFolderName, failure.Message, StringComparison.Ordinal);
        }

        // …and the two 3D editors, which have their own root and are not this one.
        foreach (var command in new[] { $"{SceneCapabilityNames.BlenderProgram} {SceneCapabilityNames.BlenderFactoryStart} -b --python d.py -- plan.json out.json" })
        {
            var failure = lab.ExpectFailure(
                ProjectCapabilityNames.ProjectScaffold,
                NativeLab.ScaffoldNativePayload("native-3d", "threed", new Dictionary<string, string> { ["run"] = command }));
            Assert.Equal(ErrorClasses.PermissionDenied, failure.ErrorClass);
        }

        Assert.Equal(0, counter.Calls);
    }

    [Fact]
    public void Build_is_not_a_test_command_and_test_is_not_a_run_command()
    {
        var counter = new CountingStart();
        using var lab = new NativeLab(start: counter.Start);

        // `dotnet test` in the run section: a run that reported a compile as a test suite would
        // be the difference between "it builds" and "its tests pass".
        var asRun = lab.ExpectFailure(
            ProjectCapabilityNames.ProjectScaffold,
            NativeLab.ScaffoldNativePayload("native-mix-1", "mix1", new Dictionary<string, string> { ["run"] = NativeLab.DotnetCommand("test") }));
        Assert.Equal(ErrorClasses.PermissionDenied, asRun.ErrorClass);

        // `dotnet build` in the test section: the reverse, and the more dangerous direction.
        var asTest = lab.ExpectFailure(
            ProjectCapabilityNames.ProjectScaffold,
            NativeLab.ScaffoldNativePayload(
                "native-mix-2",
                "mix2",
                run: new Dictionary<string, string> { ["build"] = NativeLab.DotnetCommand("build") },
                test: new Dictionary<string, string> { ["test"] = NativeLab.DotnetCommand("build") }));
        Assert.Equal(ErrorClasses.PermissionDenied, asTest.ErrorClass);

        // …and so are publish and pack, which produce artefacts rather than verdicts.
        foreach (var command in new[] { NativeLab.PublishCommand(), NativeLab.PackCommand() })
        {
            var failure = lab.ExpectFailure(
                ProjectCapabilityNames.ProjectScaffold,
                NativeLab.ScaffoldNativePayload(
                    "native-mix-3",
                    "mix3",
                    run: new Dictionary<string, string> { ["build"] = NativeLab.DotnetCommand("build") },
                    test: new Dictionary<string, string> { ["test"] = command }));
            Assert.Equal(ErrorClasses.PermissionDenied, failure.ErrorClass);
        }

        Assert.Equal(0, counter.Calls);
    }

    [Fact]
    public void A_marker_edited_into_a_hostile_command_is_refused_at_RUN_time_too()
    {
        var counter = new CountingStart();
        using var lab = new NativeLab(start: counter.Start);

        var folder = lab.ScaffoldNative(
            "native-tamper",
            "tamper",
            run: new Dictionary<string, string> { ["build"] = NativeLab.DotnetCommand("build") });

        // Whoever edited the marker — the owner, a script, anything — the command is parsed
        // again from it before a process exists.
        NativeLab.TamperRunCommand(folder, "build", "dotnet build app/app.csproj -c Release -p:PreBuildEvent=calc.exe");
        var failure = lab.ExpectFailure(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "native-tamper" });
        Assert.Equal(ErrorClasses.PermissionDenied, failure.ErrorClass);
        Assert.Equal("command_not_allowlisted", failure.Detail[DocumentErrors.DetailKey]);

        NativeLab.TamperRunCommand(folder, "build", "signtool sign /a dist/app.msix");
        var signing = lab.ExpectFailure(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "native-tamper" });
        Assert.Equal(ErrorClasses.PermissionDenied, signing.ErrorClass);

        Assert.Equal(0, counter.Calls);
        Assert.Equal(0, lab.Runner.ProcessesStarted);
    }

    [Fact]
    public void The_runner_refuses_a_signer_again_at_the_last_moment_before_a_process()
    {
        // Defence in depth, and the thing the allowlist test above cannot prove: even if a
        // shape were widened later, or a detection returned the wrong file, this runner does
        // not start a signing tool and does not pass one as an argument.
        foreach (var program in NativeCapabilityNames.ForbiddenPrograms)
        {
            var asExecutable = Assert.Throws<CapabilityException>(
                () => ProjectRunner.RequireNoSigner($@"C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\{program}.exe", []));
            Assert.Equal(ErrorClasses.PermissionDenied, asExecutable.ErrorClass);

            var asArgument = Assert.Throws<CapabilityException>(
                () => ProjectRunner.RequireNoSigner(@"C:\Program Files\dotnet\dotnet.exe", ["build", $@"C:\tools\{program}.exe"]));
            Assert.Equal(ErrorClasses.PermissionDenied, asArgument.ErrorClass);
        }

        // The four real invocations pass it, which is what makes the refusals above mean
        // something rather than being a gate nothing ever crosses.
        ProjectRunner.RequireNoSigner(@"C:\Program Files\dotnet\dotnet.exe", ["build", "app/app.csproj", "-c", "Release"]);
        ProjectRunner.RequireNoSigner(@"C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\makeappx.exe", ["pack", "/d", "staging", "/p", "dist/app.msix", "/o", "/nv"]);
    }
}
