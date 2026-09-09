using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Native;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Native;

/// <summary>
/// M28_NATIVE_APP_FACTORY_SPEC.md §5 — the real toolchain, through the real device path.
///
/// The Cloud Core's lab (<c>scripts/tests/native-windows-lab.py</c>) compiles the same shapes
/// with a direct subprocess, which proves the COMMANDS work. This proves the DEVICE runs them:
/// the same four shapes, scaffolded through <c>project.scaffold</c> into the native root,
/// re-parsed from the marker the companion wrote, and run as the companion's own children
/// inside a Windows Job Object whose limits are read back from the kernel.
///
/// On a machine without the .NET SDK these tests SKIP with the reason named. They are not
/// vacuous passes: the allowlist, root, bounds and signing tests run everywhere and hold the
/// shape.
/// </summary>
[Collection(NativeLabCollection.Name)]
public sealed class NativeBuildTests
{
    [NativeLabFact(NativeCapabilityNames.DotnetProgram)]
    public void The_toolchain_is_detected_where_the_Cloud_Core_looks_for_it_and_nothing_is_run_to_find_it()
    {
        var dotnet = NativeTools.FindDotnet()!;
        Assert.True(File.Exists(dotnet.Executable));
        Assert.Equal(NativeTools.DotnetExecutable, Path.GetFileName(dotnet.Executable), StringComparer.OrdinalIgnoreCase);
        Assert.False(string.IsNullOrWhiteSpace(dotnet.Source));

        // The description is one line for the startup log and never a claim of something absent.
        var described = NativeTools.Describe();
        Assert.Contains("dotnet=", described, StringComparison.Ordinal);
        Assert.Contains("makeappx=", described, StringComparison.Ordinal);
        if (NativeTools.FindMakeAppx() is null)
        {
            Assert.Contains("makeappx=(not installed)", described, StringComparison.Ordinal);
        }
    }

    [Fact]
    public void A_machine_without_the_toolchain_answers_dependency_unavailable_and_names_where_it_looked()
    {
        // Not a skip: this is the honest answer the Cloud Core has to be able to read, and it
        // is asserted through the same Require() the runner calls. On a machine that HAS the
        // tool the assertion is that Require returns it rather than throwing, which is the
        // other half of the same contract.
        foreach (var runtime in new[] { ProjectRuntime.Dotnet, ProjectRuntime.MakeAppx })
        {
            var found = runtime == ProjectRuntime.Dotnet ? NativeTools.FindDotnet() : NativeTools.FindMakeAppx();
            if (found is null)
            {
                var failure = Assert.Throws<CapabilityException>(() => NativeTools.Require(runtime));
                Assert.Equal(ErrorClasses.DependencyUnavailable, failure.ErrorClass);
                Assert.False(failure.Retryable);
                Assert.Equal("runtime_missing", failure.Detail[SessionCompanion.Documents.DocumentErrors.DetailKey]);
                Assert.Contains("nothing was run", failure.Message, StringComparison.Ordinal);
            }
            else
            {
                Assert.Equal(found.Executable, NativeTools.Require(runtime).Executable);
            }
        }
    }

    [NativeLabFact(NativeCapabilityNames.DotnetProgram)]
    public void A_real_build_runs_as_a_contained_child_and_a_real_publish_produces_an_EXE()
    {
        using var lab = new NativeLab();
        var folder = lab.ScaffoldNative(
            "native-real",
            "notlarim",
            run: new Dictionary<string, string>
            {
                ["build"] = NativeLab.DotnetCommand("build"),
                ["publish"] = NativeLab.PublishCommand(),
            },
            test: new Dictionary<string, string> { ["test"] = NativeLab.DotnetCommand("test", NativeLab.TestProjectFileName) });

        // ---- dotnet build, as a batch project.run
        var build = lab.Exec(
            ProjectCapabilityNames.ProjectRun,
            new JsonObject { ["project_id"] = "native-real", ["command_key"] = "build" },
            budgetSeconds: NativeCapabilityNames.RunLimit.TotalSeconds);

        Assert.True(build["batch"]!.GetValue<bool>());
        Assert.Equal("build", build["command_key"]!.GetValue<string>());
        Assert.Equal("dotnet", build["runtime"]!.GetValue<string>());
        Assert.False(build.ContainsKey("port"), "a build binds nothing, so there is no port on the receipt");
        Assert.False(build.ContainsKey("url"));
        Assert.Equal(0, build["exit_code"]!.GetValue<int>());
        Assert.True(build["pid"]!.GetValue<int>() > 0);

        // The compiler really was this companion's own child, in its own job — asked of the
        // kernel, not of the code that created it.
        var run = lab.Runner.RunOf("native-real")!;
        Assert.Equal(NativeCapabilityNames.MemoryLimitBytes, run.Limits?.JobMemoryLimitBytes ?? 0);
        Assert.True(File.Exists(build["log_path"]!.GetValue<string>()), "the run's bounded log is on disk under .pagentos");

        // ---- dotnet test, as project.test
        var test = lab.Exec(
            ProjectCapabilityNames.ProjectTest,
            new JsonObject { ["project_id"] = "native-real" },
            budgetSeconds: NativeCapabilityNames.RunLimit.TotalSeconds);
        Assert.Equal(0, test["exit_code"]!.GetValue<int>());
        Assert.Equal("test", test["command_key"]!.GetValue<string>());

        // ---- dotnet publish -r win-x64 --self-contained, and a REAL exe at the end of it
        var publish = lab.Exec(
            ProjectCapabilityNames.ProjectRun,
            new JsonObject { ["project_id"] = "native-real", ["command_key"] = "publish" },
            budgetSeconds: NativeCapabilityNames.RunLimit.TotalSeconds);
        Assert.Equal(0, publish["exit_code"]!.GetValue<int>());

        var exe = Path.Combine(folder, NativeLab.PublishDirName, "app.exe");
        Assert.True(File.Exists(exe), $"the publish produced no {exe}; log tail: {publish["log_tail"]?.GetValue<string>()}");

        // Read back by something that did not build it: the DOS/NT header, the standard
        // library's own bytes, the way the Cloud Core's independent reader does it. An artefact
        // is not a claim; it is a file something else could read.
        using var stream = File.OpenRead(exe);
        var header = new byte[0x40];
        Assert.Equal(header.Length, stream.Read(header, 0, header.Length));
        Assert.Equal((byte)'M', header[0]);
        Assert.Equal((byte)'Z', header[1]);
        Assert.True(new FileInfo(exe).Length > 32 * 1024, "a self-contained publish produces a real binary, not a stub");

        // The publish landed INSIDE the project folder, which is the containment claim rather
        // than a build-system claim.
        Assert.StartsWith(folder, Path.GetFullPath(exe), StringComparison.OrdinalIgnoreCase);
        Assert.StartsWith(lab.ProjectsRootNative, folder, StringComparison.OrdinalIgnoreCase);
    }

    [NativeLabFact(NativeCapabilityNames.DotnetProgram)]
    public void A_build_that_fails_reports_the_compiler_s_own_words_rather_than_a_verdict()
    {
        using var lab = new NativeLab();
        var files = NativeLab.FixtureFiles(NativeLab.TargetFramework);
        files[1] = ("app/Program.cs", "namespace App;\n\npublic static class Program\n{\n    public static int Main() { return \"this is not an int\"; }\n}\n");

        lab.ScaffoldNative(
            "native-broken",
            "bozuk",
            run: new Dictionary<string, string> { ["build"] = NativeLab.DotnetCommand("build") },
            files: files);

        var build = lab.Exec(
            ProjectCapabilityNames.ProjectRun,
            new JsonObject { ["project_id"] = "native-broken", ["command_key"] = "build" },
            budgetSeconds: NativeCapabilityNames.RunLimit.TotalSeconds);

        // A failed compile is an EXIT CODE and a log tail, never an exception and never a
        // success: the Cloud Core's lifecycle is what decides what a non-zero exit means, and
        // it can only do that if the device hands it the compiler's own output.
        Assert.NotEqual(0, build["exit_code"]!.GetValue<int>());
        Assert.Contains("CS", build["log_tail"]!.GetValue<string>(), StringComparison.Ordinal);
    }

    [NativeLabFact(NativeCapabilityNames.MakeAppxProgram)]
    public void A_real_makeappx_pack_produces_a_zip_the_test_can_open_itself()
    {
        using var lab = new NativeLab();
        var folder = lab.ScaffoldNative(
            "native-pack",
            "paket",
            run: new Dictionary<string, string> { ["package"] = NativeLab.PackCommand() },
            files:
            [
                (NativeLab.ProjectFileName, "<Project Sdk=\"Microsoft.NET.Sdk\"><PropertyGroup><TargetFramework>" + NativeLab.TargetFramework + "</TargetFramework></PropertyGroup></Project>\n"),
                ("staging/AppxManifest.xml", AppxManifest()),
            ]);

        // makeappx needs its staging directory to hold the executable the manifest names; a
        // placeholder is enough for /nv (no semantic validation — see NativeSigningTests).
        File.WriteAllBytes(Path.Combine(folder, NativeLab.PackageSourceDirName, "paket.exe"), [0x4D, 0x5A, 0x90, 0x00]);
        Directory.CreateDirectory(Path.Combine(folder, "dist"));

        var pack = lab.Exec(
            ProjectCapabilityNames.ProjectRun,
            new JsonObject { ["project_id"] = "native-pack", ["command_key"] = "package" },
            budgetSeconds: NativeCapabilityNames.RunLimit.TotalSeconds);

        var package = Path.Combine(folder, "dist", "app.msix");
        Assert.True(
            File.Exists(package),
            $"makeappx exited {pack["exit_code"]} and produced no package; log tail: {pack["log_tail"]?.GetValue<string>()}");
        Assert.Equal(0, pack["exit_code"]!.GetValue<int>());

        // An MSIX is a zip, and the reader here is the standard library rather than the tool
        // that wrote it.
        using var archive = System.IO.Compression.ZipFile.OpenRead(package);
        Assert.Contains(archive.Entries, e => e.FullName.EndsWith("AppxManifest.xml", StringComparison.OrdinalIgnoreCase));

        // And nothing signed it. An unsigned package is the honest state of a package nobody
        // signed (M28 §9): there is no signature block in the archive.
        Assert.DoesNotContain(archive.Entries, e => e.FullName.EndsWith("AppxSignature.p7x", StringComparison.OrdinalIgnoreCase));
    }

    private static string AppxManifest() =>
        """
        <?xml version="1.0" encoding="utf-8"?>
        <Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
                 xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
                 xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
                 IgnorableNamespaces="uap rescap">
          <Identity Name="PagentOS.paket" Version="0.1.0.0" Publisher="CN=PagentOS Unsigned Build" ProcessorArchitecture="x64" />
          <Properties>
            <DisplayName>Paket</DisplayName>
            <PublisherDisplayName>PagentOS (imzasiz)</PublisherDisplayName>
            <Logo>logo.png</Logo>
          </Properties>
          <Dependencies>
            <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0" MaxVersionTested="10.0.22621.0" />
          </Dependencies>
          <Resources>
            <Resource Language="tr-TR" />
          </Resources>
          <Applications>
            <Application Id="App" Executable="paket.exe" EntryPoint="Windows.FullTrustApplication">
              <uap:VisualElements DisplayName="Paket" Description="Paket" BackgroundColor="transparent"
                                  Square150x150Logo="logo.png" Square44x44Logo="logo.png" />
            </Application>
          </Applications>
          <Capabilities>
            <rescap:Capability Name="runFullTrust" />
          </Capabilities>
        </Package>
        """;
}
