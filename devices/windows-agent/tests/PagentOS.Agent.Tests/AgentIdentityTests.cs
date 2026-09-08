using System.Reflection;
using PagentOS.Agent.Core.Connection;
using PagentOS.Agent.Core.Protocol;
using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>
/// ONE canonical version identity for the Windows agent (2026-09-08 incident).
///
/// The owner staged candidate 0.6.0. It ran, it advertised its 40 capabilities, and the
/// installer's staged-update verifier still reported <c>the device reports software version
/// ""</c> and rolled it back after 92.6 s. The candidate was never wrong; the identity chain
/// had a hole in it, and nothing in any suite walked that chain end to end.
///
/// These tests pin the DEVICE half of the chain: the number the agent announces in its
/// <c>hello</c>, the number its binary is stamped with, and the number its
/// <c>capabilities</c> verb prints are the same number, from one constant. The Cloud Core
/// half is pinned by <c>services/api/tests/unit/test_device_identity_contract.py</c>, which
/// reads this file's PowerShell counterpart; between them the chain has no unowned link.
/// </summary>
public sealed class AgentIdentityTests
{
    [Fact]
    public void SoftwareVersionIsAThreePartVersion()
    {
        Assert.Matches(@"^\d+\.\d+\.\d+$", AgentInfo.SoftwareVersion);
    }

    [Fact]
    public void TheBinaryIsStampedWithTheVersionItAnnounces()
    {
        // Directory.Build.props <Version> and AgentInfo.SoftwareVersion are two files; this
        // is what stops them from becoming two ANSWERS. A candidate whose file version
        // disagrees with the version it announces cannot be reasoned about after the fact:
        // the installer, the SCM and Cloud Core would each be right about a different number.
        Assert.Equal(AgentInfo.SoftwareVersion, AgentInfo.AssemblyVersion);

        var informational = typeof(AgentInfo).Assembly
            .GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion;
        Assert.NotNull(informational);
        Assert.StartsWith(AgentInfo.SoftwareVersion, informational, StringComparison.Ordinal);
    }

    [Fact]
    public void TheHelloAnnouncesExactlyThatVersion()
    {
        var options = new AgentConnectionOptions
        {
            DeviceId = "device-1",
            BrokerWsUrl = new Uri("ws://127.0.0.1:8001/v1/devices/ws"),
        };

        Assert.Equal(AgentInfo.SoftwareVersion, options.SoftwareVersion);
    }

    [Fact]
    public void TheComponentNamesWhichHalfOfTheAgentAnnounced()
    {
        // "the device reports 0.6.0" is only actionable once it says WHAT reported it.
        Assert.Equal("device-service", AgentInfo.Component);
    }

    [Fact]
    public void TheCapabilityManifestFingerprintIsDerivedFromTheManifestItself()
    {
        var fingerprint = AgentInfo.CapabilityManifestVersion;
        Assert.Matches("^[0-9a-f]{12}$", fingerprint);
        Assert.Equal(fingerprint, AgentInfo.CapabilityManifestVersion);

        // Derived, not remembered: it is a function of the superset manifest, so it cannot
        // stay the same across a capability change the way a hand-bumped number can.
        var superset = string.Join(
            "\n",
            AgentCapabilities.Compose(browserEnabled: true, displayPowerEnabled: true, operatorEnabled: true));
        var digest = System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(superset));
        Assert.Equal(Convert.ToHexString(digest)[..12].ToLowerInvariant(), fingerprint);

        var withoutMedia = string.Join(
            "\n",
            AgentCapabilities
                .Compose(browserEnabled: true, displayPowerEnabled: true, operatorEnabled: true)
                .Where(name => name != BrowserCapabilities.MediaPlay));
        var otherDigest = System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(withoutMedia));
        Assert.NotEqual(Convert.ToHexString(otherDigest)[..12].ToLowerInvariant(), fingerprint);
    }

    /// <summary>
    /// The <c>capabilities</c> verb is what describes a STAGED candidate before it has ever
    /// run — the installer builds the candidate manifest from this document. Every field the
    /// installer reads must be in it, spelled exactly as the installer spells it.
    /// </summary>
    [Fact]
    public void TheCapabilitiesVerbPrintsTheWholeIdentity()
    {
        var source = File.ReadAllText(RepoFile("devices", "windows-agent", "src", "PagentOS.DeviceService", "Program.cs"));
        var block = source[source.IndexOf("private static int Capabilities()", StringComparison.Ordinal)..];
        block = block[..block.IndexOf("private static int Identity()", StringComparison.Ordinal)];

        foreach (var key in new[]
                 {
                     "software_version", "component", "assembly_version",
                     "capability_manifest_version", "display_power_enabled",
                     "browser_enabled", "operator_enabled", "capabilities",
                 })
        {
            Assert.Contains($"[\"{key}\"]", block, StringComparison.Ordinal);
        }
    }

    /// <summary>
    /// The installer's reader (<c>Get-InstalledAgentManifest</c>) and the verb that writes the
    /// document are two halves of one contract, in two languages. This test makes the C# half
    /// read the PowerShell half's source: a key renamed on either side fails here.
    /// </summary>
    [Fact]
    public void TheInstallerReadsExactlyTheKeysTheVerbWrites()
    {
        var powershell = File.ReadAllText(RepoFile("scripts", "lib", "InstallEvidence.ps1"));
        var reader = powershell[powershell.IndexOf("function Get-InstalledAgentManifest", StringComparison.Ordinal)..];
        reader = reader[..reader.IndexOf("function Assert-InstalledAgentSupportsM13", StringComparison.Ordinal)];

        foreach (var key in new[]
                 {
                     "software_version", "component", "assembly_version",
                     "capability_manifest_version", "display_power_enabled",
                 })
        {
            Assert.Contains($"$doc.{key}", reader, StringComparison.Ordinal);
        }
    }

    /// <summary>
    /// A running service's own log must answer "which version is this" without anyone having
    /// to ask another process. Before 2026-09-08 the startup log named the device, the broker,
    /// the pipe and the capability list -- and no version at all.
    /// </summary>
    [Fact]
    public void TheStartupLogNamesTheWholeIdentity()
    {
        var source = File.ReadAllText(RepoFile("devices", "windows-agent", "src", "PagentOS.DeviceService", "Program.cs"));
        var index = source.IndexOf("agent identity:", StringComparison.Ordinal);
        Assert.True(index >= 0, "the service must log its identity at startup");
        var line = source[index..source.IndexOf("starting device service:", StringComparison.Ordinal)];

        foreach (var field in new[]
                 {
                     "component=", "software_version=", "assembly_version=",
                     "capability_manifest=", "started_at=",
                 })
        {
            Assert.Contains(field, line, StringComparison.Ordinal);
        }

        Assert.Contains("AgentInfo.SoftwareVersion", line, StringComparison.Ordinal);
        Assert.Contains("AgentInfo.AssemblyVersion", line, StringComparison.Ordinal);
    }

    private static string RepoFile(params string[] parts)
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "PROJECT_CONSTITUTION.md")))
        {
            dir = dir.Parent;
        }

        Assert.NotNull(dir);
        return Path.Combine([dir.FullName, .. parts]);
    }
}
