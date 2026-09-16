using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;

namespace PagentOS.Companion.Audio.Listening;

public enum ListeningMode
{
    Continuous,
    WakeWord,
    PushToTalk,
}

public static class ListeningModes
{
    public static string ToWire(this ListeningMode mode) => mode switch
    {
        ListeningMode.WakeWord => DeviceVoiceContract.ModeWakeWord,
        ListeningMode.PushToTalk => DeviceVoiceContract.ModePushToTalk,
        _ => DeviceVoiceContract.ModeContinuous,
    };

    /// <summary>A wire name, or null for anything the contract does not name.</summary>
    public static ListeningMode? Parse(string? raw) => raw?.Trim().ToLowerInvariant() switch
    {
        DeviceVoiceContract.ModeContinuous => ListeningMode.Continuous,
        DeviceVoiceContract.ModeWakeWord => ListeningMode.WakeWord,
        DeviceVoiceContract.ModePushToTalk => ListeningMode.PushToTalk,
        _ => null,
    };
}

/// <summary>
/// The owner's listening choice: on or off, and how. Persisted, because "I turned the
/// microphone off" must survive a companion restart — a privacy switch that resets itself to
/// ON after a crash is not a switch.
/// </summary>
public sealed record ListeningSettings(bool Enabled, ListeningMode Mode)
{
    public static ListeningSettings Default { get; } = new(
        DeviceVoiceContract.DefaultEnabled,
        ListeningModes.Parse(DeviceVoiceContract.DefaultMode) ?? ListeningMode.Continuous);
}

public interface IListeningSettingsStore
{
    ListeningSettings Load();

    void Save(ListeningSettings settings);
}

public sealed class InMemoryListeningSettingsStore(ListeningSettings? initial = null) : IListeningSettingsStore
{
    private ListeningSettings _settings = initial ?? ListeningSettings.Default;

    public int Saves { get; private set; }

    public ListeningSettings Load() => _settings;

    public void Save(ListeningSettings settings)
    {
        _settings = settings;
        Saves++;
    }
}

/// <summary>
/// <c>&lt;DataDir&gt;\voice\listening.json</c>. A file that cannot be read is NOT the default:
/// an unreadable privacy switch loads as OFF, and the log says why. Writes are atomic
/// (temp file + move) so a crash mid-write never leaves a half file that would read as that.
/// </summary>
public sealed class FileListeningSettingsStore(string path, ILogger? logger = null) : IListeningSettingsStore
{
    public string Path { get; } = path;

    public static string DefaultPath(string dataDir) => System.IO.Path.Combine(dataDir, "voice", "listening.json");

    public ListeningSettings Load()
    {
        if (!File.Exists(Path))
        {
            return ListeningSettings.Default;
        }

        try
        {
            var node = JsonNode.Parse(File.ReadAllText(Path)) as JsonObject
                ?? throw new FormatException("not a JSON object");
            var enabled = node["enabled"]?.GetValue<bool>() ?? throw new FormatException("no 'enabled'");
            var mode = ListeningModes.Parse(node["mode"]?.GetValue<string>()) ?? throw new FormatException("unknown 'mode'");
            return new ListeningSettings(enabled, mode);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or FormatException or JsonException or InvalidOperationException)
        {
            logger?.LogWarning("voice: listening settings at {Path} are unreadable ({Reason}); listening stays OFF until the owner turns it on", Path, ex.Message);
            return new ListeningSettings(false, ListeningSettings.Default.Mode);
        }
    }

    public void Save(ListeningSettings settings)
    {
        var directory = System.IO.Path.GetDirectoryName(Path)!;
        Directory.CreateDirectory(directory);
        var body = new JsonObject
        {
            ["enabled"] = settings.Enabled,
            ["mode"] = settings.Mode.ToWire(),
        }.ToJsonString(new JsonSerializerOptions { WriteIndented = true });
        var temp = Path + ".tmp";
        File.WriteAllText(temp, body);
        File.Move(temp, Path, overwrite: true);
    }
}
