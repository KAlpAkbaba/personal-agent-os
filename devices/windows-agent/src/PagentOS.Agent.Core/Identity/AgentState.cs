using System.Text.Json;
using System.Text.Json.Serialization;
using PagentOS.Agent.Core.Security;

namespace PagentOS.Agent.Core.Identity;

/// <summary>Persistent device state written after successful enrollment (never contains secrets).</summary>
public sealed record AgentState
{
    [JsonPropertyName("device_id")]
    public required string DeviceId { get; init; }

    [JsonPropertyName("name")]
    public required string Name { get; init; }

    [JsonPropertyName("broker_rest_url")]
    public required string BrokerRestUrl { get; init; }

    [JsonPropertyName("enrolled_at")]
    public required DateTimeOffset EnrolledAt { get; init; }

    private static readonly JsonSerializerOptions Options = new() { WriteIndented = true };

    public static AgentState? Load(string path)
    {
        if (!File.Exists(path))
        {
            return null;
        }

        return JsonSerializer.Deserialize<AgentState>(File.ReadAllText(path), Options);
    }

    public void Save(string path)
    {
        var directory = Path.GetDirectoryName(Path.GetFullPath(path));
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }

        var tempPath = path + ".tmp";
        File.WriteAllText(tempPath, JsonSerializer.Serialize(this, Options));
        File.Move(tempPath, path, overwrite: true);
        // Enrollment state is service-owned machine material: written by an owner-context
        // enrollment run, read and rewritten by LocalSystem. Stated on the file rather than
        // inherited, so a later change to the directory's ACL cannot silently widen it — and
        // applied after the move, because the final DACL excludes the writing account and
        // File.Move needs DELETE on the source.
        MachineMaterial.Protect(path, MachineMaterialKind.State);
    }
}
