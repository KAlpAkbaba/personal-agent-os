using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Native;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Native;

/// <summary>
/// B33 requirement 473: a REAL MSIX, packed by the real <c>makeappx</c>, signed in process by
/// <see cref="PackageSigner"/> with the lab's throwaway self-signed identity, and read back by
/// Windows' own verifier and by the package's signature block — never by the code that signed
/// it. The certificate is never trusted on this machine (tests do not write LocalMachine
/// stores), so every package here is "intact, untrusted", which is exactly the state before
/// the owner's elevated step.
/// </summary>
[Collection(NativeLabCollection.Name)]
public sealed class NativePackageSigningTests
{
    private const string Slug = "notlarim";
    private const string ProjectId = "native-signing";

    internal static (NativeLab Lab, SigningLab Signing, string Folder) Scaffolded(string publisher = NativeCapabilityNames.TestSigningSubject)
    {
        var signing = new SigningLab();
        var lab = new NativeLab(signing: signing.Identity);
        try
        {
            var folder = lab.ScaffoldNative(ProjectId, Slug, run: new Dictionary<string, string> { ["build"] = NativeLab.DotnetCommand("build") });
            var exe = Path.Combine(folder, NativeLifecycle.PublishDirName, Slug + ".exe");
            Directory.CreateDirectory(Path.GetDirectoryName(exe)!);
            File.Copy(Environment.ProcessPath!, exe, overwrite: true);
            var staging = Path.Combine(folder, NativeLifecycle.StagingDirName);
            Directory.CreateDirectory(staging);
            File.WriteAllText(Path.Combine(staging, "AppxManifest.xml"), Manifest(publisher));
            return (lab, signing, folder);
        }
        catch
        {
            lab.Dispose();
            signing.Dispose();
            throw;
        }
    }

    private static JsonObject PackSigned(NativeLab lab)
        => lab.Exec(
            ProjectCapabilityNames.ProjectPackage,
            new JsonObject { ["project_id"] = ProjectId, ["kind"] = "msix", ["signing_mode"] = NativeCapabilityNames.SigningModeTestCertificate },
            budgetSeconds: 300);

    [NativeLabFact(NativeCapabilityNames.MakeAppxProgram)]
    public void A_signed_package_answers_signed_with_the_signer_and_windows_verifier_agrees()
    {
        var (lab, signing, _) = Scaffolded();
        using (signing)
        using (lab)
        {
            var result = PackSigned(lab);

            var path = result["path"]!.GetValue<string>();
            Assert.True(result["signed"]!.GetValue<bool>());
            Assert.Equal(NativeCapabilityNames.SigningModeTestCertificate, result["signing_mode"]!.GetValue<string>());
            var described = signing.Identity.Describe()!;
            Assert.Equal(described.Thumbprint, result["signer_thumbprint"]!.GetValue<string>());
            Assert.Equal(NativeCapabilityNames.TestSigningSubject, result["signer_subject"]!.GetValue<string>());
            Assert.False(result["trusted"]!.GetValue<bool>());
            Assert.Equal(NativeCapabilityNames.TrustScript, result["trust_step"]!.GetValue<string>());
            Assert.True(result["observed"]!["signature_intact"]!.GetValue<bool>());
            Assert.Equal(Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant(), result["sha256"]!.GetValue<string>());

            // Independent readers, not the signer: the zip has a signature block, and Windows'
            // verifier recomputes the digests and only disputes the (untrusted) chain.
            using (var zip = ZipFile.OpenRead(path))
            {
                Assert.Contains(zip.Entries, e => e.FullName == PackageSigner.SignatureEntryName);
            }

            var readBack = PackageSigner.Verify(path);
            Assert.True(readBack.Signed, readBack.Detail);
            Assert.True(readBack.Intact);
            Assert.False(readBack.ChainTrusted);
            Assert.NotEqual(PackageSigner.TrustSuccess, readBack.VerifyStatus);
            Assert.True(PackageSigner.IsIntact(readBack.VerifyStatus), readBack.VerifyStatusHex);
            // Measured on this machine: the verifier disputes only the ROOT (a self-signed certificate nobody trusted yet).
            Assert.Equal(PackageSigner.CertEUntrustedRoot, readBack.VerifyStatus);
            Assert.Equal("0x800B0109", result["observed"]!["verify_status"]!.GetValue<string>());
            Assert.Equal(described.Thumbprint, readBack.SignerThumbprint);
        }
    }

    [NativeLabFact(NativeCapabilityNames.MakeAppxProgram)]
    public void A_package_changed_after_signing_does_not_read_back_as_signed()
    {
        var (lab, signing, _) = Scaffolded();
        using (signing)
        using (lab)
        {
            var path = PackSigned(lab)["path"]!.GetValue<string>();

            // Flip ONE byte in place, in the middle of the file - inside the packaged executable's
            // data, which is most of the package. Same length, same zip structure: only a digest
            // can notice, and nothing the signer wrote can hide it.
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.ReadWrite))
            {
                stream.Position = stream.Length / 2;
                var original = stream.ReadByte();
                stream.Position = stream.Length / 2;
                stream.WriteByte((byte)(original ^ 0xFF));
            }

            var readBack = PackageSigner.Verify(path);
            Assert.False(readBack.Signed, readBack.Detail);
            Assert.False(readBack.Intact);
            Assert.False(PackageSigner.IsIntact(readBack.VerifyStatus), readBack.VerifyStatusHex);
            Assert.Equal(PackageSigner.TrustEBadDigest, readBack.VerifyStatus);
        }
    }

    [NativeLabFact(NativeCapabilityNames.MakeAppxProgram)]
    public void A_signature_that_does_not_read_back_is_never_answered_as_signed_and_the_package_is_removed()
    {
        // The signer "succeeds" and the file is then damaged before the read-back: the answer
        // must be a refusal, and no package may be left at the path claiming a signature.
        var signing = new SigningLab();
        using (signing)
        {
            var lab = new NativeLab(signing: signing.Identity, signMsix: (path, certificate) =>
            {
                PackageSigner.SignMsix(path, certificate);
                using var stream = new FileStream(path, FileMode.Open, FileAccess.ReadWrite);
                stream.Position = stream.Length / 2;
                var original = stream.ReadByte();
                stream.Position = stream.Length / 2;
                stream.WriteByte((byte)(original ^ 0xFF));
            });
            using (lab)
            {
                var folder = Prepare(lab);
                var failure = lab.ExpectFailure(
                    ProjectCapabilityNames.ProjectPackage,
                    new JsonObject { ["project_id"] = ProjectId, ["kind"] = "msix", ["signing_mode"] = NativeCapabilityNames.SigningModeTestCertificate },
                    budgetSeconds: 300);
                Assert.Equal(ErrorClasses.PostconditionFailed, failure.ErrorClass);
                Assert.Contains("did not read back", failure.Message);
                Assert.Contains("0x80096010", failure.Message);
                Assert.False(File.Exists(Path.Combine(folder, NativeLifecycle.DistDirName, Slug + ".msix")));
            }
        }
    }

    [NativeLabFact(NativeCapabilityNames.MakeAppxProgram)]
    public void A_signer_failure_removes_the_unsigned_package_and_says_signing_failed()
    {
        var signing = new SigningLab();
        using (signing)
        {
            var lab = new NativeLab(signing: signing.Identity, signMsix: (_, _) => throw new CryptographicException("the MSIX signer refused the package (0x8007000B)"));
            using (lab)
            {
                var folder = Prepare(lab);
                var failure = lab.ExpectFailure(
                    ProjectCapabilityNames.ProjectPackage,
                    new JsonObject { ["project_id"] = ProjectId, ["kind"] = "msix", ["signing_mode"] = NativeCapabilityNames.SigningModeTestCertificate },
                    budgetSeconds: 300);
                Assert.Equal(ErrorClasses.DependencyUnavailable, failure.ErrorClass);
                Assert.StartsWith("signing_failed:", failure.Message, StringComparison.Ordinal);
                Assert.False(failure.Retryable);
                Assert.False(File.Exists(Path.Combine(folder, NativeLifecycle.DistDirName, Slug + ".msix")));
            }
        }
    }

    private static string Prepare(NativeLab lab)
    {
        var folder = lab.ScaffoldNative(ProjectId, Slug, run: new Dictionary<string, string> { ["build"] = NativeLab.DotnetCommand("build") });
        var exe = Path.Combine(folder, NativeLifecycle.PublishDirName, Slug + ".exe");
        Directory.CreateDirectory(Path.GetDirectoryName(exe)!);
        File.Copy(Environment.ProcessPath!, exe, overwrite: true);
        var staging = Path.Combine(folder, NativeLifecycle.StagingDirName);
        Directory.CreateDirectory(staging);
        File.WriteAllText(Path.Combine(staging, "AppxManifest.xml"), Manifest(NativeCapabilityNames.TestSigningSubject));
        return folder;
    }

    [NativeLabFact(NativeCapabilityNames.MakeAppxProgram)]
    public void An_unsigned_package_reads_back_as_unsigned_and_says_why()
    {
        var (lab, signing, _) = Scaffolded();
        using (signing)
        using (lab)
        {
            var result = lab.Exec(ProjectCapabilityNames.ProjectPackage, new JsonObject { ["project_id"] = ProjectId, ["kind"] = "msix" }, budgetSeconds: 300);
            Assert.False(result["signed"]!.GetValue<bool>());
            Assert.Equal(NativeCapabilityNames.SigningModeUnsigned, result["signing_mode"]!.GetValue<string>());
            Assert.Null(result["signer_thumbprint"]);

            var readBack = PackageSigner.Verify(result["path"]!.GetValue<string>());
            Assert.False(readBack.Signed);
            Assert.Equal(PackageSigner.TrustENoSignature, readBack.VerifyStatus);
            Assert.Contains("no AppxSignature.p7x", readBack.Detail);

            // Nothing was created for a package nobody asked to sign.
            Assert.Null(signing.Identity.Describe());
        }
    }

    [NativeLabFact(NativeCapabilityNames.MakeAppxProgram)]
    public void A_manifest_whose_publisher_is_not_the_signing_subject_is_refused_before_anything_is_packed()
    {
        var (lab, signing, folder) = Scaffolded(publisher: "CN=PagentOS Unsigned Build");
        using (signing)
        using (lab)
        {
            var failure = lab.ExpectFailure(
                ProjectCapabilityNames.ProjectPackage,
                new JsonObject { ["project_id"] = ProjectId, ["kind"] = "msix", ["signing_mode"] = NativeCapabilityNames.SigningModeTestCertificate },
                budgetSeconds: 300);
            Assert.Equal(ErrorClasses.ValidationError, failure.ErrorClass);
            Assert.Contains("Publisher", failure.Message);
            Assert.Contains(NativeCapabilityNames.TestSigningSubject, failure.Message);
            Assert.False(File.Exists(Path.Combine(folder, NativeLifecycle.DistDirName, Slug + ".msix")));
        }
    }

    [Fact]
    public void The_owner_certificate_mode_is_refused_by_name_and_an_unknown_mode_is_invalid()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        var (lab, signing, _) = Scaffolded();
        using (signing)
        using (lab)
        {
            var owner = lab.ExpectFailure(
                ProjectCapabilityNames.ProjectPackage,
                new JsonObject { ["project_id"] = ProjectId, ["kind"] = "msix", ["signing_mode"] = NativeCapabilityNames.SigningModeOwnerCertificate });
            Assert.Equal(ErrorClasses.PermissionDenied, owner.ErrorClass);
            Assert.Contains(NativeCapabilityNames.SigningModeOwnerCertificate, owner.Message);

            var unknown = lab.ExpectFailure(
                ProjectCapabilityNames.ProjectPackage,
                new JsonObject { ["project_id"] = ProjectId, ["kind"] = "msix", ["signing_mode"] = "ev_certificate" });
            Assert.Equal(ErrorClasses.ValidationError, unknown.ErrorClass);
            Assert.Null(signing.Identity.Describe());
        }
    }

    [Fact]
    public void A_portable_zip_is_never_signed_even_when_signing_was_asked_for_and_says_so()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        var (lab, signing, _) = Scaffolded();
        using (signing)
        using (lab)
        {
            var result = lab.Exec(
                ProjectCapabilityNames.ProjectPackage,
                new JsonObject { ["project_id"] = ProjectId, ["kind"] = "portable", ["signing_mode"] = NativeCapabilityNames.SigningModeTestCertificate });
            Assert.False(result["signed"]!.GetValue<bool>());
            Assert.Equal(NativeCapabilityNames.SigningModeUnsigned, result["signing_mode"]!.GetValue<string>());
            Assert.Equal("portable_not_signed", result["signing_note"]!.GetValue<string>());
            Assert.Null(signing.Identity.Describe());
        }
    }

    internal static string Manifest(string publisher) =>
        $"""
        <?xml version="1.0" encoding="utf-8"?>
        <Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10" xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10" xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities">
          <Identity Name="PagentOS.notlarim.lab" Version="0.1.0.0" Publisher="{publisher}" ProcessorArchitecture="x64" />
          <Properties>
            <DisplayName>Notlarım</DisplayName>
            <PublisherDisplayName>PagentOS</PublisherDisplayName>
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
