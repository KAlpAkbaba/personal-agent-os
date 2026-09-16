using System.Text.Json.Nodes;
using PagentOS.Companion.Audio.Listening;
using PagentOS.Companion.Audio.Listening.Spotting;
using Xunit;

namespace PagentOS.Companion.Audio.Tests;

/// <summary>
/// B47: <c>packages/protocol/device-voice.json</c>, read by the DEVICE half. The Cloud Core half
/// is <c>services/api/tests/unit/test_device_voice_contract.py</c>; both read the same file, so
/// a number or a name edited on one side alone fails here or there.
/// </summary>
public sealed class DeviceVoiceContractTests
{
    public const string ContractFile = "device-voice.json";

    private static JsonObject Contract()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null
               && !File.Exists(Path.Combine(directory.FullName, "packages", "protocol", ContractFile)))
        {
            directory = directory.Parent;
        }

        Assert.True(directory is not null, $"packages/protocol/{ContractFile} was not found above the test output");
        return (JsonObject)JsonNode.Parse(File.ReadAllText(Path.Combine(directory!.FullName, "packages", "protocol", ContractFile)))!;
    }

    private static string[] Strings(JsonNode? node) => ((JsonArray)node!).Select(n => n!.GetValue<string>()).ToArray();

    [Fact]
    public void The_device_voice_contract_is_the_one_this_device_speaks()
    {
        var contract = Contract();
        Assert.Equal("device.voice", contract["contract"]!.GetValue<string>());
        Assert.Equal(1, contract["version"]!.GetValue<int>());
        Assert.Equal("devices/windows-agent/src/PagentOS.Companion.Audio/Listening/DeviceVoiceContract.cs", contract["read_by"]!["device"]!.GetValue<string>());
    }

    [Fact]
    public void The_privacy_bounds_are_the_contract_s_numbers()
    {
        var privacy = Contract()["privacy"]!;
        Assert.Equal(DeviceVoiceContract.DefaultPreRollMs, privacy["default_preroll_ms"]!.GetValue<int>());
        Assert.Equal(DeviceVoiceContract.MaxPreRollMs, privacy["max_preroll_ms"]!.GetValue<int>());
        Assert.Equal(DeviceVoiceContract.MaxSegmentMs, privacy["max_segment_ms"]!.GetValue<int>());
        Assert.False(privacy["raw_audio_persisted"]!.GetValue<bool>());
        Assert.False(privacy["silence_leaves_device"]!.GetValue<bool>());
        Assert.Equal("session_companion", privacy["capture_process"]!.GetValue<string>());
        Assert.Equal(DeviceVoiceContract.RemoteEnableAllowed, privacy["remote_enable_allowed"]!.GetValue<bool>());
        Assert.True(DeviceVoiceContract.DefaultPreRollMs <= DeviceVoiceContract.MaxPreRollMs);
        Assert.Equal(DeviceVoiceContract.MaxPreRollMs, new PreRollBuffer(int.MaxValue).CapacityMs);
    }

    [Fact]
    public void The_listening_modes_and_defaults_are_the_contract_s()
    {
        var listening = Contract()["listening"]!;
        Assert.Equal(DeviceVoiceContract.Modes, Strings(listening["modes"]));
        Assert.Equal(DeviceVoiceContract.DefaultMode, listening["default_mode"]!.GetValue<string>());
        Assert.Equal(DeviceVoiceContract.DefaultEnabled, listening["default_enabled"]!.GetValue<bool>());
        Assert.Equal(DeviceVoiceContract.WakeWordPhraseId, listening["wake_word_phrase_id"]!.GetValue<string>());
        Assert.Equal(DeviceVoiceContract.FollowUpWindowMs, listening["follow_up_window_ms"]!.GetValue<int>());
        Assert.Equal(DeviceVoiceContract.PushToTalkMinHoldMs, listening["push_to_talk_min_hold_ms"]!.GetValue<int>());
        Assert.All(Enum.GetValues<ListeningMode>(), m => Assert.Contains(m.ToWire(), DeviceVoiceContract.Modes));
        Assert.Equal(ListeningSettings.Default, new ListeningSettings(DeviceVoiceContract.DefaultEnabled, ListeningModes.Parse(DeviceVoiceContract.DefaultMode)!.Value));
    }

    [Fact]
    public void The_states_and_heartbeat_keys_are_the_contract_s()
    {
        var contract = Contract();
        Assert.Equal(DeviceVoiceContract.IndicatorStates, Strings(contract["indicator_states"]));
        Assert.Equal(DeviceVoiceContract.ServiceStates, Strings(contract["service_states"]));
        Assert.Equal(DeviceVoiceContract.HeartbeatField, contract["heartbeat"]!["field"]!.GetValue<string>());
        Assert.Equal(DeviceVoiceContract.HeartbeatKeys, Strings(contract["heartbeat"]!["keys"]));
        Assert.Equal(
            DeviceVoiceContract.HeartbeatKeys.Order(StringComparer.Ordinal),
            new DeviceVoiceHealth().Heartbeat().Select(p => p.Key).Order(StringComparer.Ordinal));
    }

    [Fact]
    public void The_offline_command_table_is_the_contract_s_row_for_row()
    {
        var offline = Contract()["offline_commands"]!;
        Assert.Equal(DeviceVoiceContract.OfflineEngine, offline["engine"]!.GetValue<string>());
        var rows = ((JsonArray)offline["commands"]!).Select(r => new OfflineCommandRule(
            r!["id"]!.GetValue<string>(),
            r["requires"]!.GetValue<string>(),
            r["acts"]!.GetValue<string>())).ToList();
        Assert.Equal(DeviceVoiceContract.OfflineCommands, rows);

        // Only turning listening OFF may act while the Cloud Core is reachable.
        Assert.Equal(
            [DeviceVoiceContract.CommandListeningOff],
            rows.Where(r => r.Acts == DeviceVoiceContract.ActsAlways).Select(r => r.Id));
        Assert.All(rows, r => Assert.True(KeywordTemplateSet.IsValidPhraseId(r.Id)));
    }

    [Fact]
    public void The_local_snooze_shape_is_the_contract_s()
    {
        var snooze = Contract()["local_snooze"]!;
        Assert.Equal(DeviceVoiceContract.LocalSnoozeField, snooze["heartbeat_field"]!.GetValue<string>());
        Assert.Equal(DeviceVoiceContract.LocalSnoozeEntryKeys, Strings(snooze["entry_keys"]));
        Assert.Equal(DeviceVoiceContract.LocalSnoozeMaxEntries, snooze["max_entries"]!.GetValue<int>());
        Assert.Equal(
            [DeviceVoiceContract.PayloadSnoozeMinutes, DeviceVoiceContract.PayloadSnoozesLeft],
            Strings(snooze["payload_keys"]));
    }
}
