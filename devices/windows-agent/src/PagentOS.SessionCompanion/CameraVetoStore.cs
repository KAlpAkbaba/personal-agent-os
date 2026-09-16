using System.Text.Json;
using System.Text.Json.Nodes;

namespace PagentOS.SessionCompanion.Camera;

/// <summary>
/// The owner's device-side camera veto, remembered across companion restarts (B48 security
/// review, HIGH). The owner closed the camera from the tray; a self-update, a crash, a reboot
/// or a logoff must not reopen it because the cloud still holds a non-off mode. Only the
/// owner's own tray action ("Kameraya yeniden izin ver") clears it.
/// </summary>
/// <remarks>
/// Deliberately OUTSIDE the <c>Camera/</c> folder: that folder is structurally forbidden any
/// file API (no frame may ever be written), and this store writes one boolean.
/// </remarks>
public interface ICameraVetoStore
{
    /// <summary>True when the owner's veto is in force. A store that cannot be read answers TRUE.</summary>
    bool Load();

    /// <summary>Remembers the owner's choice. Throws when it could not be written.</summary>
    void Save(bool vetoed);
}

/// <summary>
/// A small JSON file in the OWNER's profile (<c>%LOCALAPPDATA%\PagentOS\companion\camera-veto.json</c>)
/// - not the Session-0 service's machine tree, not the cloud. Written write-then-move.
/// </summary>
/// <remarks>
/// Fail closed: a file that exists but cannot be read or parsed is a veto. The owner may have
/// written it; an unreadable "no" must never become a "yes". A missing file is "no veto" -
/// the state of a device whose owner never used the switch.
/// </remarks>
public sealed class FileCameraVetoStore(string path) : ICameraVetoStore
{
    public string FilePath => path;

    public static string DefaultPath()
        => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "PagentOS",
            "companion",
            "camera-veto.json");

    public bool Load()
    {
        try
        {
            if (!File.Exists(path))
            {
                return false;
            }

            var node = JsonNode.Parse(File.ReadAllText(path)) as JsonObject;
            return node?["vetoed"] is not JsonValue value || !value.TryGetValue<bool>(out var vetoed) || vetoed;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException)
        {
            return true;
        }
    }

    public void Save(bool vetoed)
    {
        var directory = Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }

        var temporary = path + ".tmp";
        File.WriteAllText(
            temporary,
            new JsonObject
            {
                ["vetoed"] = vetoed,
                ["changed_at"] = DateTimeOffset.UtcNow.ToString("O"),
                ["by"] = "owner_tray",
            }.ToJsonString());
        File.Move(temporary, path, overwrite: true);
    }
}

/// <summary>A veto store in memory, for tests and hosts with no profile. It survives a "restart" if shared.</summary>
public sealed class MemoryCameraVetoStore(bool vetoed = false) : ICameraVetoStore
{
    public bool Vetoed { get; private set; } = vetoed;

    public bool FailLoad { get; set; }

    public bool FailSave { get; set; }

    public bool Load() => FailLoad || Vetoed;

    public void Save(bool value)
    {
        if (FailSave)
        {
            throw new IOException("veto store not writable");
        }

        Vetoed = value;
    }
}
