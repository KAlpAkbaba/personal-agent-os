using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Native;
using PagentOS.SessionCompanion.Projects;
using Xunit;
using Xunit.Abstractions;

namespace PagentOS.Agent.Tests.Native;

/// <summary>
/// B33 requirements 468/473: installing a SIGNED MSIX for the current user. The lab never makes
/// its certificate trusted on this machine (that is the owner's elevated step, and tests never
/// write a LocalMachine store), so the two halves are proved separately: the device's own trust
/// gate with a simulated trust answer and a recording deployer, and Windows' real package
/// manager — driven through <see cref="WindowsPackageDeployer"/> — refusing the same untrusted
/// package by itself, which is the backstop and the proof that the interop reaches Windows.
/// </summary>
[Collection(NativeLabCollection.Name)]
public sealed class NativeMsixInstallTests(ITestOutputHelper output)
{
    private const string ProjectId = "native-signing";

    private static JsonObject Install() => new() { ["project_id"] = ProjectId, ["kind"] = NativeLifecycle.InstallKindMsix, ["name"] = "Notlarım" };

    private static (NativeLab Lab, SigningLab Signing, string Folder, RecordingDeployer Deployer) Packed(bool sign = true, IMsixDeployer? real = null)
    {
        var signing = new SigningLab();
        var deployer = new RecordingDeployer();
        var lab = new NativeLab(signing: signing.Identity, deployer: real ?? deployer);
        var folder = lab.ScaffoldNative(ProjectId, "notlarim", run: new Dictionary<string, string> { ["build"] = NativeLab.DotnetCommand("build") });
        var exe = Path.Combine(folder, NativeLifecycle.PublishDirName, "notlarim.exe");
        Directory.CreateDirectory(Path.GetDirectoryName(exe)!);
        File.Copy(Environment.ProcessPath!, exe, overwrite: true);
        var staging = Path.Combine(folder, NativeLifecycle.StagingDirName);
        Directory.CreateDirectory(staging);
        File.WriteAllText(Path.Combine(staging, "AppxManifest.xml"), NativePackageSigningTests.Manifest(NativeCapabilityNames.TestSigningSubject));
        var payload = new JsonObject { ["project_id"] = ProjectId, ["kind"] = "msix" };
        if (sign)
        {
            payload["signing_mode"] = NativeCapabilityNames.SigningModeTestCertificate;
        }

        lab.Exec(ProjectCapabilityNames.ProjectPackage, payload, budgetSeconds: 300);
        return (lab, signing, folder, deployer);
    }

    [NativeLabFact(NativeCapabilityNames.MakeAppxProgram)]
    public void An_untrusted_signer_is_refused_by_name_with_the_owner_s_trust_step_and_windows_is_never_asked()
    {
        var (lab, signing, _, deployer) = Packed();
        using (signing)
        using (lab)
        {
            var failure = lab.ExpectFailure(ProjectCapabilityNames.ProjectInstall, Install());

            Assert.Equal(ErrorClasses.PermissionDenied, failure.ErrorClass);
            Assert.StartsWith(NativeCapabilityNames.UntrustedSignerMarker + ":", failure.Message, StringComparison.Ordinal);
            Assert.Contains(NativeCapabilityNames.TrustScript, failure.Message);
            Assert.Contains(signing.Identity.Describe()!.Thumbprint, failure.Message);
            Assert.False(failure.Retryable);
            Assert.Empty(deployer.Calls);
            Assert.False(File.Exists(Path.Combine(lab.ProjectsRootNative, NativeLifecycle.InstalledRecordName)));
        }
    }

    [NativeLabFact(NativeCapabilityNames.MakeAppxProgram)]
    public void An_unsigned_package_is_refused_even_when_everything_is_trusted()
    {
        var (lab, signing, _, deployer) = Packed(sign: false);
        using (signing)
        using (lab)
        {
            var failure = lab.ExpectFailure(ProjectCapabilityNames.ProjectInstall, Install());

            Assert.Equal(ErrorClasses.PermissionDenied, failure.ErrorClass);
            Assert.StartsWith(NativeCapabilityNames.UnsignedPackageMarker + ":", failure.Message, StringComparison.Ordinal);
            Assert.Empty(deployer.Calls);
        }
    }

    [Fact]
    public void An_msix_install_without_a_package_is_not_found_and_an_unknown_kind_is_invalid()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        using var signing = new SigningLab();
        using var lab = new NativeLab(signing: signing.Identity, deployer: new RecordingDeployer());
        lab.ScaffoldNative(ProjectId, "notlarim", run: new Dictionary<string, string> { ["build"] = NativeLab.DotnetCommand("build") });

        var missing = lab.ExpectFailure(ProjectCapabilityNames.ProjectInstall, Install());
        Assert.Equal(ErrorClasses.NotFound, missing.ErrorClass);
        Assert.Contains("package the MSIX first", missing.Message);

        var unknown = lab.ExpectFailure(ProjectCapabilityNames.ProjectInstall, new JsonObject { ["project_id"] = ProjectId, ["kind"] = "appinstaller" });
        Assert.Equal(ErrorClasses.ValidationError, unknown.ErrorClass);
    }

    [NativeLabFact(NativeCapabilityNames.MakeAppxProgram)]
    public void A_trusted_signer_installs_through_the_deployer_is_read_back_registered_and_uninstalls_the_same_package()
    {
        var (lab, signing, folder, deployer) = Packed();
        using (signing)
        using (lab)
        {
            var thumbprint = signing.Identity.Describe()!.Thumbprint;
            signing.TrustOnThisMachine(thumbprint);

            var installed = lab.Exec(ProjectCapabilityNames.ProjectInstall, Install());

            var package = Path.Combine(folder, NativeLifecycle.DistDirName, "notlarim.msix");
            Assert.Equal([$"add {package}"], deployer.Calls);
            Assert.True(installed["installed"]!.GetValue<bool>());
            Assert.Equal(NativeLifecycle.InstallKindMsix, installed["method"]!.GetValue<string>());
            Assert.True(installed["signed"]!.GetValue<bool>());
            Assert.True(installed["trusted"]!.GetValue<bool>());
            Assert.Equal(thumbprint, installed["signer_thumbprint"]!.GetValue<string>());
            Assert.True(installed["observed"]!["package_registered"]!.GetValue<bool>());

            // The names are Windows' own, computed from the identity INSIDE the package.
            var fullName = installed["package_full_name"]!.GetValue<string>();
            var familyName = installed["package_family_name"]!.GetValue<string>();
            Assert.StartsWith("PagentOS.notlarim.lab_0.1.0.0_x64__", fullName, StringComparison.Ordinal);
            Assert.StartsWith("PagentOS.notlarim.lab_", familyName, StringComparison.Ordinal);
            Assert.EndsWith(familyName["PagentOS.notlarim.lab_".Length..], fullName, StringComparison.Ordinal);

            var record = JsonNode.Parse(File.ReadAllText(Path.Combine(lab.ProjectsRootNative, NativeLifecycle.InstalledRecordName)))!.AsObject();
            Assert.Equal(NativeLifecycle.InstallKindMsix, record[ProjectId]!["method"]!.GetValue<string>());
            Assert.Equal(fullName, record[ProjectId]!["package_full_name"]!.GetValue<string>());

            var removed = lab.Exec(ProjectCapabilityNames.ProjectUninstall, new JsonObject { ["project_id"] = ProjectId });
            Assert.Equal($"remove {fullName}", deployer.Calls[^1]);
            Assert.True(removed["uninstalled"]!.GetValue<bool>());
            Assert.True(removed["package_removed"]!.GetValue<bool>());
            Assert.False(removed["observed"]!["package_registered"]!.GetValue<bool>());
            Assert.False(removed["observed"]!["shortcut_exists"]!.GetValue<bool>());
            Assert.True(removed["build_kept"]!.GetValue<bool>());
            Assert.True(File.Exists(package));

            var again = lab.ExpectFailure(ProjectCapabilityNames.ProjectUninstall, new JsonObject { ["project_id"] = ProjectId });
            Assert.Equal(ErrorClasses.NotFound, again.ErrorClass);
        }
    }

    [NativeLabFact(NativeCapabilityNames.MakeAppxProgram)]
    public void Windows_reporting_success_without_registering_the_package_is_a_postcondition_failure_not_an_install()
    {
        var signing = new SigningLab();
        var deployer = new RecordingDeployer { RegisterOnAdd = false };
        using (signing)
        {
            var (lab, _, _, _) = PackedWith(signing, deployer);
            using (lab)
            {
                signing.TrustOnThisMachine(signing.Identity.Describe()!.Thumbprint);
                var failure = lab.ExpectFailure(ProjectCapabilityNames.ProjectInstall, Install());
                Assert.Equal(ErrorClasses.PostconditionFailed, failure.ErrorClass);
                Assert.False(File.Exists(Path.Combine(lab.ProjectsRootNative, NativeLifecycle.InstalledRecordName)));
            }
        }
    }

    [NativeLabFact(NativeCapabilityNames.MakeAppxProgram)]
    public void Windows_own_package_manager_refuses_the_untrusted_package_by_itself_and_registers_nothing()
    {
        // The device gate is bypassed on purpose (simulated trust) so the REAL deployer is asked:
        // the operating system must refuse on its own. This is the backstop, and it is also the
        // proof that AddPackageAsync, IAsyncInfo and the deployment result are bound correctly.
        var real = new WindowsPackageDeployer();
        var (lab, signing, _, _) = Packed(real: real);
        using (signing)
        using (lab)
        {
            signing.TrustOnThisMachine(signing.Identity.Describe()!.Thumbprint);

            var failure = lab.ExpectFailure(ProjectCapabilityNames.ProjectInstall, Install(), budgetSeconds: 300);

            Assert.Equal(ErrorClasses.DependencyUnavailable, failure.ErrorClass);
            output.WriteLine(failure.Message);
            // Measured 2026-09-16: Windows' own words, carried through the deployment result.
            Assert.StartsWith("msix_install_failed (0x800B0109)", failure.Message, StringComparison.Ordinal);
            Assert.Contains("root certificate", failure.Message, StringComparison.OrdinalIgnoreCase);
            Assert.False(File.Exists(Path.Combine(lab.ProjectsRootNative, NativeLifecycle.InstalledRecordName)));

            var package = Directory.GetFiles(lab.ProjectsRootNative, "notlarim.msix", SearchOption.AllDirectories).Single();
            var (fullName, familyName) = MsixPackageNames.For(MsixIdentity.Read(package));
            Assert.False(real.IsRegistered(familyName, fullName));
            Assert.Empty(WindowsPackageDeployer.RegisteredFullNames(familyName));
        }
    }

    [Fact]
    public void Windows_own_package_manager_answers_a_removal_of_a_package_that_does_not_exist_with_an_error()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        var outcome = new WindowsPackageDeployer().Remove("PagentOS.never.installed_0.0.0.1_x64__0000000000000");
        Assert.False(outcome.Ok);
        output.WriteLine($"{outcome.HResultHex} {outcome.ErrorText}");
        Assert.Equal("0x80073CF1", outcome.HResultHex); // ERROR_INSTALL_PACKAGE_NOT_FOUND
        Assert.Contains("PagentOS.never.installed", outcome.ErrorText);
    }

    [Fact]
    public void Package_names_are_windows_own_computation_checked_against_a_known_family()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        var calculator = new MsixIdentity(
            "Microsoft.WindowsCalculator",
            "CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US",
            "10.2103.8.0",
            "x64");
        var (fullName, familyName) = MsixPackageNames.For(calculator);
        Assert.Equal("Microsoft.WindowsCalculator_8wekyb3d8bbwe", familyName);
        Assert.Equal("Microsoft.WindowsCalculator_10.2103.8.0_x64__8wekyb3d8bbwe", fullName);

        Assert.Equal(0x000A_0837_0008_0000UL, MsixPackageNames.Version("10.2103.8.0"));
        Assert.Equal(9u, MsixPackageNames.Architecture("x64"));
        Assert.Throws<InvalidDataException>(() => MsixPackageNames.Version("1.2.3"));

        // Where this machine has the calculator, Windows lists it under exactly that family.
        foreach (var name in WindowsPackageDeployer.RegisteredFullNames(familyName))
        {
            Assert.StartsWith("Microsoft.WindowsCalculator_", name, StringComparison.Ordinal);
            Assert.EndsWith("__8wekyb3d8bbwe", name, StringComparison.Ordinal);
        }

        Assert.Empty(WindowsPackageDeployer.RegisteredFullNames("PagentOS.never.installed_0000000000000"));
    }

    private static (NativeLab Lab, SigningLab Signing, string Folder, RecordingDeployer Deployer) PackedWith(SigningLab signing, RecordingDeployer deployer)
    {
        var lab = new NativeLab(signing: signing.Identity, deployer: deployer);
        var folder = lab.ScaffoldNative(ProjectId, "notlarim", run: new Dictionary<string, string> { ["build"] = NativeLab.DotnetCommand("build") });
        var exe = Path.Combine(folder, NativeLifecycle.PublishDirName, "notlarim.exe");
        Directory.CreateDirectory(Path.GetDirectoryName(exe)!);
        File.Copy(Environment.ProcessPath!, exe, overwrite: true);
        var staging = Path.Combine(folder, NativeLifecycle.StagingDirName);
        Directory.CreateDirectory(staging);
        File.WriteAllText(Path.Combine(staging, "AppxManifest.xml"), NativePackageSigningTests.Manifest(NativeCapabilityNames.TestSigningSubject));
        lab.Exec(ProjectCapabilityNames.ProjectPackage, new JsonObject { ["project_id"] = ProjectId, ["kind"] = "msix", ["signing_mode"] = NativeCapabilityNames.SigningModeTestCertificate }, budgetSeconds: 300);
        return (lab, signing, folder, deployer);
    }

    /// <summary>A deployer that records what it was asked and registers what it "installed" — used only where trust is simulated.</summary>
    private sealed class RecordingDeployer : IMsixDeployer
    {
        private readonly HashSet<string> _registered = new(StringComparer.OrdinalIgnoreCase);

        public List<string> Calls { get; } = [];

        public bool RegisterOnAdd { get; init; } = true;

        public DeploymentOutcome Add(string packagePath)
        {
            Calls.Add($"add {packagePath}");
            if (RegisterOnAdd)
            {
                _registered.Add(MsixPackageNames.For(MsixIdentity.Read(packagePath)).FullName);
            }

            return new DeploymentOutcome(0, 0, null);
        }

        public DeploymentOutcome Remove(string packageFullName)
        {
            Calls.Add($"remove {packageFullName}");
            _registered.Remove(packageFullName);
            return new DeploymentOutcome(0, 0, null);
        }

        public bool IsRegistered(string packageFamilyName, string packageFullName) => _registered.Contains(packageFullName);
    }
}
