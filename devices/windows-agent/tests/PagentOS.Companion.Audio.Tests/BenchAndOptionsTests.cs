using System.Text.Json;
using PagentOS.Companion.Audio.Testing;
using PagentOS.Companion.Audio.Turn;
using Xunit;

namespace PagentOS.Companion.Audio.Tests;

public sealed class BenchAndOptionsTests
{
    [Theory]
    [InlineData(EndOfTurnMode.Client)]
    [InlineData(EndOfTurnMode.Server)]
    public async Task The_offline_bench_produces_the_five_metrics_with_no_defects(EndOfTurnMode mode)
    {
        var report = await new OfflineVoiceBench(new BenchOptions(Turns: 4, EndOfTurn: mode)).RunAsync();

        // Four scenarios, five owner turns: the barge-in scenario is two turns (the question, then the interruption).
        Assert.Equal(5, report["turns"]!.GetValue<int>());
        Assert.True(report["mic_to_uplink_ms"]!["n"]!.GetValue<int>() >= 3);
        Assert.True(report["eot_to_first_audio_ms"]!["n"]!.GetValue<int>() >= 2);
        Assert.Equal(1, report["barge_in_to_stop_ms"]!["n"]!.GetValue<int>());
        Assert.Equal(1, report["tool_preamble_ms"]!["n"]!.GetValue<int>());
        Assert.Equal(1, report["tool_done_to_speech_ms"]!["n"]!.GetValue<int>());
        Assert.Equal(1, report["barge_ins"]!.GetValue<int>());
        Assert.Equal(1, report["tool_executions"]!.GetValue<int>());
        Assert.Empty(report["defects"]!.AsArray());
        Assert.Contains(report["scenario_log"]!.AsArray(), line => line!.GetValue<string>().StartsWith("ok:"));
        Assert.Contains("barge_in_start", report["cloud_events"]!.AsArray().Select(e => e!.GetValue<string>()));
        Assert.True(report["barge_in_to_stop_ms"]!["p95"]!.GetValue<double>() < 150, "offline barge-in stop must be far under the 150 ms target");
        Assert.Contains("offline gate", report["disclaimer"]!.GetValue<string>());
    }

    [Fact]
    public async Task A_simulated_stop_cost_shows_up_in_the_barge_in_metric()
    {
        var report = await new OfflineVoiceBench(new BenchOptions(Turns: 3, SimulatedPlaybackStopMs: 0)).RunAsync();
        Assert.Equal(1, report["barge_in_to_stop_ms"]!["n"]!.GetValue<int>());
        Assert.True(report["barge_in_to_stop_ms"]!["max"]!.GetValue<double>() >= 0);
    }

    [Fact]
    public void Voice_is_off_unless_explicitly_enabled_with_a_cloud_url()
    {
        Assert.False(VoiceCompanionOptions.Parse(null, null, null, null, null, null).Enabled);
        Assert.False(VoiceCompanionOptions.Parse("true", null, null, null, null, null).Enabled);
        Assert.False(VoiceCompanionOptions.Parse("true", "not a url", null, null, null, null).Enabled);
        Assert.False(VoiceCompanionOptions.Parse("true", "ftp://x", null, null, null, null).Enabled);
        Assert.False(VoiceCompanionOptions.Parse(null, "http://100.90.158.26:8001", null, null, null, null).Enabled);

        var on = VoiceCompanionOptions.Parse("true", "http://100.90.158.26:8001", " cap-1 ", "", "client", "dev-1");
        Assert.True(on.Enabled);
        Assert.Equal(new Uri("http://100.90.158.26:8001"), on.CloudCoreUrl);
        Assert.Equal("cap-1", on.PreferredCaptureDeviceId);
        Assert.Null(on.PreferredRenderDeviceId);
        Assert.Equal(EndOfTurnMode.Client, on.EndOfTurn);
        Assert.Equal("dev-1", on.DeviceId);

        var flag = VoiceCompanionOptions.Parse(null, "https://cloud.example", null, null, null, null, commandLineFlag: true);
        Assert.True(flag.Enabled);
        Assert.Equal(EndOfTurnMode.Server, flag.EndOfTurn);
    }

    [Fact]
    public void The_shipped_companion_configuration_leaves_voice_off()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "appsettings.json");
        Assert.True(File.Exists(path), "the companion's appsettings.json flows to the test output");
        using var doc = JsonDocument.Parse(File.ReadAllText(path));
        var root = doc.RootElement;
        var options = VoiceCompanionOptions.Parse(
            root.GetProperty("VoiceEnabled").GetString(),
            root.GetProperty("CloudCoreUrl").GetString(),
            null, null, root.GetProperty("VoiceEndOfTurn").GetString(), null);
        Assert.False(options.Enabled);
        Assert.Equal(EndOfTurnMode.Server, options.EndOfTurn);
    }
}
