using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// B30 requirements 117-122: this device's compiled allowlists are held equal to
/// <c>packages/protocol/operator-allowlists.json</c>, the one file the Cloud Core also reads.
/// </summary>
/// <remarks>
/// Until 2026-09-14 the Cloud Core's application allowlist had six names and this device's
/// <see cref="OperatorCapabilities.DefaultApplications"/> had seven; the Cloud Core's shell
/// vocabulary was two commands against this device's eight terminal patterns. Neither half
/// read the other, both suites were green, and a name the owner could say was refused by the
/// device. The <c>file-search-roots.json</c> discipline (B03) applied to the operator: one
/// contract, a Python test on one side, this on the other.
/// </remarks>
public sealed class OperatorAllowlistsContractTests
{
    private static JsonObject Contract()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null
               && !File.Exists(Path.Combine(directory.FullName, "packages", "protocol", "operator-allowlists.json")))
        {
            directory = directory.Parent;
        }

        Assert.NotNull(directory);
        var path = Path.Combine(directory!.FullName, "packages", "protocol", "operator-allowlists.json");
        return (JsonObject)JsonNode.Parse(File.ReadAllText(path))!;
    }

    private static string[] Strings(JsonNode? node) => ((JsonArray)node!).Select(n => n!.GetValue<string>()).ToArray();

    [Fact]
    public void The_applications_this_device_launches_are_exactly_the_ones_the_contract_names_with_their_images()
    {
        var declared = ((JsonArray)Contract()["applications"]!)
            .Select(entry => ((JsonObject)entry!))
            .ToDictionary(e => e["id"]!.GetValue<string>(), e => e["image"]!.GetValue<string>(), StringComparer.OrdinalIgnoreCase);

        var compiled = OperatorCapabilities.DefaultApplications();
        Assert.Equal(declared.Keys.OrderBy(k => k, StringComparer.Ordinal), compiled.Keys.OrderBy(k => k, StringComparer.Ordinal));
        foreach (var (id, path) in compiled)
        {
            Assert.Equal(declared[id], Path.GetFileName(path), ignoreCase: true);
        }
    }

    [Fact]
    public void The_terminal_patterns_are_the_contracts_verbatim_and_every_cloud_command_matches_one()
    {
        var contract = Contract();
        Assert.Equal(Strings(contract["terminal"]!["patterns"]), TerminalRunner.DefaultAllowlist);

        var runner = new TerminalRunner(TerminalRunner.DefaultAllowlist, [Path.GetTempPath()], new ListLogger());
        foreach (var (kind, node) in (JsonObject)contract["terminal"]!["cloud_commands"]!)
        {
            var command = node!.GetValue<string>();
            Assert.False(string.IsNullOrWhiteSpace(runner.Authorise(command)), $"{kind}: '{command}' is not allowlisted on this device");
        }
    }

    [Fact]
    public void The_stop_and_restart_policies_are_the_contracts()
    {
        var contract = Contract();
        Assert.Equal(Strings(contract["processes"]!["stoppable_images"]), OperatorCapabilities.DefaultStoppableImages);
        Assert.Equal(Strings(contract["services"]!["restartable"]), OperatorCapabilities.DefaultRestartableServices);

        // Every stoppable image is an application's image or a known alias of one (the Store
        // calculator runs as CalculatorApp.exe); never a system process.
        var images = ((JsonArray)contract["applications"]!)
            .Select(entry => ((JsonObject)entry!)["image"]!.GetValue<string>())
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        foreach (var stoppable in OperatorCapabilities.DefaultStoppableImages)
        {
            Assert.True(images.Contains(stoppable) || stoppable == "calculatorapp.exe", stoppable);
            Assert.DoesNotContain(stoppable, new[] { "explorer.exe", "powershell.exe", "csrss.exe", "winlogon.exe", "svchost.exe" });
        }
    }

    [Fact]
    public void The_four_process_and_service_names_are_operator_members_and_the_contract_says_who_reads_it()
    {
        foreach (var name in new[] { OperatorCapabilityNames.ProcessList, OperatorCapabilityNames.ProcessStop, OperatorCapabilityNames.ServiceStatus, OperatorCapabilityNames.ServiceRestart })
        {
            Assert.True(OperatorCapabilityNames.IsMember(name), name);
            Assert.False(OperatorCapabilityNames.IsGuarded(name), name);
        }

        var readers = Strings(Contract()["read_by"]);
        Assert.Contains(readers, r => r.Contains("OperatorCapabilities.cs", StringComparison.Ordinal));
        Assert.Contains(readers, r => r.Contains("TerminalRunner.cs", StringComparison.Ordinal));
        Assert.Contains(readers, r => r.Contains("allowlists.py", StringComparison.Ordinal));
    }
}
