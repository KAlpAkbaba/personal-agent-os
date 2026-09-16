using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests.Camera;

/// <summary>
/// B48 (row 320): what is measurable about multiple-monitor power without a person looking -
/// each monitor's own DDC/CI power reading, read only when asked for by name. Whether a panel
/// is VISIBLY dark stays the owner's judgement (scripts/core/qualify-device-camera.ps1).
/// </summary>
public sealed class MonitorPowerTests
{
    private sealed class PoweredMonitors(params MonitorGeometry[] monitors) : IMonitorInventory
    {
        public int PowerReads { get; private set; }

        public IReadOnlyList<MonitorGeometry> List() => [.. monitors.Select(m => m with { Power = null })];

        public IReadOnlyList<MonitorGeometry> ListWithPower()
        {
            PowerReads++;
            return monitors;
        }
    }

    private static DisplayPowerController Controller(IMonitorInventory monitors)
        => new(new NeverDisplayPower(), NullLogger.Instance, enabled: false, monitors: monitors);

    [Fact]
    public void Status_reports_each_monitors_own_power_and_the_count_that_is_still_on_when_asked()
    {
        var monitors = new PoweredMonitors(
            new MonitorGeometry(0, true, 0, 0, 2560, 1440, "off"),
            new MonitorGeometry(1, false, 2560, 0, 1920, 1080, "on"),
            new MonitorGeometry(2, false, -1920, 0, 1920, 1080, "unsupported"));

        var result = Controller(monitors).Status(new JsonObject { ["probe_power"] = true });

        var list = result["monitors"]!.AsArray();
        Assert.Equal(["off", "on", "unsupported"], list.Select(m => m!["power"]!.GetValue<string>()));
        Assert.Equal(1, result["monitors_powered_on"]!.GetValue<int>());
        Assert.Equal(1, result["monitors_power_unknown"]!.GetValue<int>());
        Assert.Equal(1, monitors.PowerReads);
    }

    [Fact]
    public void A_plain_status_never_touches_the_monitor_bus()
    {
        // Some monitors wake on DDC traffic: the status a routine polls must not be the thing
        // that lights a screen the owner's policy just darkened.
        var monitors = new PoweredMonitors(new MonitorGeometry(0, true, 0, 0, 1920, 1080, "off"));

        var result = Controller(monitors).Status([]);

        Assert.Equal(0, monitors.PowerReads);
        Assert.False(result["monitors"]!.AsArray()[0]!.AsObject().ContainsKey("power"));
        Assert.False(result.ContainsKey("monitors_powered_on"));
    }

    [Theory]
    [InlineData(1u, "on")]
    [InlineData(2u, "standby")]
    [InlineData(3u, "suspend")]
    [InlineData(4u, "off")]
    [InlineData(5u, "off")]
    [InlineData(0u, "unsupported")]
    [InlineData(9u, "unsupported")]
    public void The_mccs_power_mode_values_map_to_words(uint value, string word)
        => Assert.Equal(word, Win32MonitorInventory.PowerWord(value));

    [Fact]
    public void Owner_lab_the_real_monitors_answer_a_power_read()
    {
        // Opt-in: a DDC/CI READ of the attached monitors (never a write). Run by the owner's
        // qualification or with PAGENTOS_TEST_DDC=1; otherwise it proves nothing and returns.
        if (Environment.GetEnvironmentVariable("PAGENTOS_TEST_DDC") != "1" || !OperatingSystem.IsWindows())
        {
            return;
        }

        var monitors = new Win32MonitorInventory().ListWithPower();

        Assert.NotEmpty(monitors);
        Assert.All(monitors, m => Assert.Contains(m.Power, new[] { "on", "standby", "suspend", "off", "unsupported" }));
        Console.WriteLine("DDC: " + string.Join(", ", monitors.Select(m => $"#{m.Index} {m.Width}x{m.Height} primary={m.Primary} power={m.Power}")));
    }

    private sealed class NeverDisplayPower : IDisplayPower
    {
        public void TurnOff() => throw new InvalidOperationException("no test turns a display off");
    }
}
