using System.Text.Json.Nodes;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests.Alarms;

/// <summary>
/// B13 requirement 284: <c>packages/protocol/alarm-timing.json</c>, read by the DEVICE half.
/// </summary>
/// <remarks>
/// <para>An alarm has two independent ways to ring — the cloud's command path and this
/// device's own armed copy — and both answer the same question: is it now too late for this
/// to be a wake-up? Until this contract they answered it with different numbers. The cloud
/// said two hours. This device said five minutes, cut down from two hours after 2026-09-10,
/// when a 07:30 alarm armed the night before rang at 08:10 because the machine had slept
/// through the alarm time: 39 minutes and 39 seconds late, into a room where the owner was
/// already awake.</para>
/// <para>This device learned from that. The cloud did not, because nothing made the two
/// halves read the same file. These tests are that "nothing" being fixed: they read the
/// shared contract rather than restating what this device believes it says.</para>
/// </remarks>
public sealed class AlarmTimingContractTests
{
    private static JsonObject Contract()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null
               && !File.Exists(Path.Combine(directory.FullName, "packages", "protocol", "alarm-timing.json")))
        {
            directory = directory.Parent;
        }

        Assert.NotNull(directory);
        var path = Path.Combine(directory!.FullName, "packages", "protocol", "alarm-timing.json");
        return (JsonObject)JsonNode.Parse(File.ReadAllText(path))!;
    }

    [Fact]
    public void The_stale_horizon_is_the_contract_s_number()
    {
        var contractSeconds = Contract()["late_fire"]!["max_late_seconds"]!.GetValue<int>();

        Assert.Equal(contractSeconds, (int)AlarmArmController.StaleAfter.TotalSeconds);
    }

    [Fact]
    public void The_default_grace_is_the_contract_s_number()
    {
        var contractSeconds = Contract()["grace"]!["device_default_seconds"]!.GetValue<int>();

        // The constant is the `expected` argument because xUnit's analyzer says so; the
        // claim is unchanged - these two numbers are one number.
        Assert.Equal(AlarmArmController.DefaultGraceSeconds, contractSeconds);
    }

    [Fact]
    public void The_grace_bounds_are_the_contract_s_numbers()
    {
        var grace = Contract()["grace"]!;

        Assert.Equal(AlarmArmController.MinGraceSeconds, grace["min_seconds"]!.GetValue<int>());
        Assert.Equal(AlarmArmController.MaxGraceSeconds, grace["max_seconds"]!.GetValue<int>());
    }

    [Fact]
    public void Grace_stays_well_under_the_stale_horizon()
    {
        // Two different questions, and the ordering between them is load-bearing: a grace as
        // long as the horizon would leave this device politely waiting for a cloud that is
        // never coming, right up to the moment ringing stopped being worth doing.
        var contract = Contract();
        var late = contract["late_fire"]!["max_late_seconds"]!.GetValue<int>();

        Assert.True(contract["grace"]!["device_default_seconds"]!.GetValue<int>() < late);
        Assert.True(contract["grace"]!["cloud_sends_seconds"]!.GetValue<int>() < late);
    }

    [Fact]
    public void The_contract_names_both_halves_that_read_it()
    {
        // A contract with one reader is a file. This assertion is what stops the next
        // capability from being written the way the four before it were - both suites green,
        // neither half able to talk to the other.
        var readBy = Contract()["read_by"]!.AsObject();

        Assert.Contains("cloud", readBy);
        Assert.Contains("device", readBy);
        Assert.Contains("AlarmArmController", readBy["device"]!.GetValue<string>());
    }
}
