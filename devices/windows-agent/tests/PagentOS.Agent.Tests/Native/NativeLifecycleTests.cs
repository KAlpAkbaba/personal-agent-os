using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Native;

/// <summary>
/// B33 requirements 456/457/468/469/471: the four lifecycle capabilities appended to the
/// projects family, driven through the REAL <see cref="ProjectCapabilities"/> dispatcher over
/// a scaffolded native project whose "publish output" is a placeholder executable — no
/// compiler is needed for any of these, because none of them compiles. The MSIX pack is the
/// one that needs a tool (<c>makeappx</c>) and is gated on it being present.
/// </summary>
[Collection(NativeLabCollection.Name)]
public sealed class NativeLifecycleTests
{
    private const string Slug = "notlarim";
    private const string ProjectId = "native-lifecycle";

    private static (NativeLab Lab, string Folder, byte[] ExeBytes) Scaffolded()
    {
        var lab = new NativeLab();
        var folder = lab.ScaffoldNative(
            ProjectId,
            Slug,
            run: new Dictionary<string, string> { ["build"] = NativeLab.DotnetCommand("build") });
        var exe = Path.Combine(folder, NativeLifecycle.PublishDirName, Slug + ".exe");
        Directory.CreateDirectory(Path.GetDirectoryName(exe)!);
        // The placeholder is a REAL executable - this test host's own - copied into the
        // publish folder: the shell reads a shortcut's target as a PE when it saves the link,
        // and answered E_FAIL for an "MZ" stub of random bytes. It is never started.
        File.Copy(Environment.ProcessPath!, exe, overwrite: true);
        var bytes = File.ReadAllBytes(exe);
        Assert.True(bytes.Length > 2 * NativeLifecycle.MaxChunkBytes, "the artefact test needs at least three chunks");
        File.WriteAllText(Path.Combine(folder, NativeLifecycle.PublishDirName, "app.dll"), "placeholder");
        return (lab, folder, bytes);
    }

    private static string Sha256Hex(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

    [Fact]
    public void Package_portable_zips_the_publish_output_under_dist_and_reports_the_hash_it_observed()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        var (lab, folder, _) = Scaffolded();
        using (lab)
        {
            var result = lab.Exec(ProjectCapabilityNames.ProjectPackage, new JsonObject { ["project_id"] = ProjectId, ["kind"] = "portable" });

            var path = result["path"]!.GetValue<string>();
            Assert.Equal("portable", result["kind"]!.GetValue<string>());
            Assert.Equal(Path.Combine(folder, NativeLifecycle.DistDirName, Slug + "-portable.zip"), path);
            Assert.True(File.Exists(path));
            Assert.False(result["signed"]!.GetValue<bool>());
            Assert.Equal(new FileInfo(path).Length, result["bytes"]!.GetValue<long>());
            Assert.Equal(Sha256Hex(File.ReadAllBytes(path)), result["sha256"]!.GetValue<string>());
            Assert.True(result["observed"]!["exists"]!.GetValue<bool>());

            using var zip = ZipFile.OpenRead(path);
            Assert.Contains(zip.Entries, e => e.FullName == Slug + ".exe");
            Assert.Contains(zip.Entries, e => e.FullName == "app.dll");
        }
    }

    [Fact]
    public void Package_refuses_an_unknown_kind_and_a_project_that_was_never_published()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        using var lab = new NativeLab();
        lab.ScaffoldNative(ProjectId, Slug, run: new Dictionary<string, string> { ["build"] = NativeLab.DotnetCommand("build") });

        var invalid = lab.ExpectFailure(ProjectCapabilityNames.ProjectPackage, new JsonObject { ["project_id"] = ProjectId, ["kind"] = "deb" });
        Assert.Equal(ErrorClasses.ValidationError, invalid.ErrorClass);

        var unpublished = lab.ExpectFailure(ProjectCapabilityNames.ProjectPackage, new JsonObject { ["project_id"] = ProjectId, ["kind"] = "portable" });
        Assert.Equal(ErrorClasses.NotFound, unpublished.ErrorClass);
        Assert.Contains("publish first", unpublished.Message);
    }

    [Fact]
    public void Package_msix_without_a_scaffolded_manifest_is_refused_by_name_before_makeappx_is_looked_for()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        var (lab, _, _) = Scaffolded();
        using (lab)
        {
            var failure = lab.ExpectFailure(ProjectCapabilityNames.ProjectPackage, new JsonObject { ["project_id"] = ProjectId, ["kind"] = "msix" });
            Assert.Equal(ErrorClasses.NotFound, failure.ErrorClass);
            Assert.Contains("AppxManifest.xml", failure.Message);
        }
    }

    [NativeLabFact(NativeCapabilityNames.MakeAppxProgram)]
    public void Package_msix_packs_the_staged_publish_output_with_makeappx_and_never_signs_it()
    {
        var (lab, folder, _) = Scaffolded();
        using (lab)
        {
            var staging = Path.Combine(folder, NativeLifecycle.StagingDirName);
            Directory.CreateDirectory(staging);
            File.WriteAllText(Path.Combine(staging, "AppxManifest.xml"), Manifest());

            var result = lab.Exec(ProjectCapabilityNames.ProjectPackage, new JsonObject { ["project_id"] = ProjectId, ["kind"] = "msix" }, budgetSeconds: 300);

            var path = result["path"]!.GetValue<string>();
            Assert.Equal("msix", result["kind"]!.GetValue<string>());
            Assert.EndsWith(Slug + ".msix", path);
            Assert.True(File.Exists(path));
            Assert.False(result["signed"]!.GetValue<bool>());
            Assert.Equal(Sha256Hex(File.ReadAllBytes(path)), result["sha256"]!.GetValue<string>());
            // An MSIX is a zip: the executable and the manifest are inside it, no signature block is.
            using var zip = ZipFile.OpenRead(path);
            Assert.Contains(zip.Entries, e => e.FullName == Slug + ".exe");
            Assert.Contains(zip.Entries, e => e.FullName == "AppxManifest.xml");
            Assert.DoesNotContain(zip.Entries, e => e.FullName == "AppxSignature.p7x");
        }
    }

    [Fact]
    public void Install_writes_a_start_menu_shortcut_to_the_built_executable_and_uninstall_removes_only_that()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        var (lab, folder, _) = Scaffolded();
        var programs = Path.Combine(lab.Root, "StartMenu", "PagentOS");
        Environment.SetEnvironmentVariable(NativeLifecycle.ProgramsDirEnvironmentVariable, programs);
        try
        {
            using (lab)
            {
                var installed = lab.Exec(ProjectCapabilityNames.ProjectInstall, new JsonObject { ["project_id"] = ProjectId, ["name"] = "Notlarım" });

                var shortcut = installed["shortcut"]!.GetValue<string>();
                Assert.True(installed["installed"]!.GetValue<bool>());
                Assert.Equal("start_menu_shortcut", installed["method"]!.GetValue<string>());
                Assert.Equal(Path.Combine(programs, "Notlarım.lnk"), shortcut);
                Assert.True(File.Exists(shortcut));
                Assert.Equal(Path.Combine(folder, NativeLifecycle.PublishDirName, Slug + ".exe"), installed["exe"]!.GetValue<string>());
                Assert.True(installed["observed"]!["shortcut_exists"]!.GetValue<bool>());
                Assert.True(installed["observed"]!["exe_exists"]!.GetValue<bool>());

                var record = JsonNode.Parse(File.ReadAllText(Path.Combine(lab.ProjectsRootNative, NativeLifecycle.InstalledRecordName)))!.AsObject();
                Assert.Equal(shortcut, record[ProjectId]!["shortcut"]!.GetValue<string>());

                var removed = lab.Exec(ProjectCapabilityNames.ProjectUninstall, new JsonObject { ["project_id"] = ProjectId });
                Assert.True(removed["uninstalled"]!.GetValue<bool>());
                Assert.True(removed["shortcut_removed"]!.GetValue<bool>());
                Assert.True(removed["build_kept"]!.GetValue<bool>());
                Assert.False(File.Exists(shortcut));
                Assert.False(removed["observed"]!["shortcut_exists"]!.GetValue<bool>());
                // The build folder is untouched: the executable is still where it was built.
                Assert.True(File.Exists(installed["exe"]!.GetValue<string>()));

                var again = lab.ExpectFailure(ProjectCapabilityNames.ProjectUninstall, new JsonObject { ["project_id"] = ProjectId });
                Assert.Equal(ErrorClasses.NotFound, again.ErrorClass);
                Assert.Contains("not installed by this system", again.Message);
            }
        }
        finally
        {
            Environment.SetEnvironmentVariable(NativeLifecycle.ProgramsDirEnvironmentVariable, null);
        }
    }

    [Fact]
    public void Install_refuses_an_executable_outside_the_project_folder()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        var (lab, _, _) = Scaffolded();
        using (lab)
        {
            var failure = lab.ExpectFailure(ProjectCapabilityNames.ProjectInstall, new JsonObject { ["project_id"] = ProjectId, ["exe"] = @"..\..\..\Windows\System32\cmd.exe" });
            Assert.Equal(ErrorClasses.PermissionDenied, failure.ErrorClass);
        }
    }

    [Fact]
    public void Artifact_streams_the_executable_in_bounded_chunks_that_reassemble_to_the_reported_hash()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        var (lab, _, bytes) = Scaffolded();
        using (lab)
        {
            var assembled = new List<byte>();
            long offset = 0;
            string? sha = null;
            var chunks = 0;
            while (true)
            {
                var chunk = lab.Exec(ProjectCapabilityNames.ProjectArtifact, new JsonObject { ["project_id"] = ProjectId, ["offset"] = offset, ["length"] = 1_000_000 });
                chunks++;
                sha ??= chunk["sha256"]!.GetValue<string>();
                var part = Convert.FromBase64String(chunk["base64"]!.GetValue<string>());
                Assert.True(part.Length <= NativeLifecycle.MaxChunkBytes);
                Assert.Equal(part.Length, chunk["length"]!.GetValue<int>());
                Assert.Equal(offset, chunk["offset"]!.GetValue<long>());
                assembled.AddRange(part);
                offset += part.Length;
                if (chunk["eof"]!.GetValue<bool>())
                {
                    Assert.Equal(bytes.Length, chunk["bytes"]!.GetValue<long>());
                    break;
                }
            }

            Assert.Equal((bytes.Length + NativeLifecycle.MaxChunkBytes - 1) / NativeLifecycle.MaxChunkBytes, chunks);
            Assert.Equal(bytes, assembled.ToArray());
            Assert.Equal(Sha256Hex(bytes), sha);

            var outside = lab.ExpectFailure(ProjectCapabilityNames.ProjectArtifact, new JsonObject { ["project_id"] = ProjectId, ["path"] = @"..\other\x.exe" });
            Assert.Equal(ErrorClasses.PermissionDenied, outside.ErrorClass);
        }
    }

    [Fact]
    public void The_four_are_advertised_after_project_test_in_the_order_the_protocol_lists()
    {
        Assert.Equal(
            ["project.package", "project.install", "project.uninstall", "project.artifact"],
            ProjectCapabilityNames.All.Skip(5).ToArray());
    }

    private static string Manifest() =>
        """
        <?xml version="1.0" encoding="utf-8"?>
        <Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10" xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10" xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities">
          <Identity Name="PagentOS.notlarim" Version="0.1.0.0" Publisher="CN=PagentOS Unsigned" ProcessorArchitecture="x64" />
          <Properties>
            <DisplayName>Notlarım</DisplayName>
            <PublisherDisplayName>PagentOS (imzasız)</PublisherDisplayName>
            <Logo>Assets\StoreLogo.png</Logo>
          </Properties>
          <Dependencies>
            <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0" MaxVersionTested="10.0.22621.0" />
          </Dependencies>
          <Resources>
            <Resource Language="tr-TR" />
          </Resources>
          <Applications>
            <Application Id="App" Executable="notlarim.exe" EntryPoint="Windows.FullTrustApplication">
              <uap:VisualElements DisplayName="Notlarım" Description="Notlarım" BackgroundColor="transparent" Square150x150Logo="Assets\Square150x150Logo.png" Square44x44Logo="Assets\Square44x44Logo.png" />
            </Application>
          </Applications>
          <Capabilities>
            <rescap:Capability Name="runFullTrust" />
          </Capabilities>
        </Package>
        """;
}
