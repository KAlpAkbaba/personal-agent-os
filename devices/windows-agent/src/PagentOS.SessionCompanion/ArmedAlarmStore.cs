using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;

namespace PagentOS.SessionCompanion;

/// <summary>
/// One alarm the cloud has told this device to be ready for (DEVICE_PROTOCOL.md §6f, M18.3).
/// It is a FALLBACK, not the alarm: the cloud is still expected to ring it on time with
/// <c>desktop.alarm_start</c>, and this arm is what happens if it cannot.
/// </summary>
public sealed record ArmedAlarm
{
    public required string AlarmId { get; init; }

    /// <summary>When the owner asked to be woken.</summary>
    public required DateTimeOffset FireAt { get; init; }

    /// <summary>
    /// How long after <see cref="FireAt"/> the device waits for the cloud before ringing on its
    /// own. The whole point of the grace: a device that fired at exactly <c>fire_at</c> would
    /// race the cloud's own command over a link that has any latency at all, and the owner
    /// would sometimes hear two alarms.
    /// </summary>
    public int GraceSeconds { get; init; }

    public string? Label { get; init; }

    /// <summary>The ramp to use if this arm ever fires, in <c>desktop.alarm_start</c>'s shape.</summary>
    public JsonObject? WakeVolume { get; init; }

    public int? MaxDurationSeconds { get; init; }

    public DateTimeOffset ArmedAt { get; init; }

    /// <summary>
    /// B13 requirement 286: whether anybody meant this ring.
    /// </summary>
    /// <remarks>
    /// The cloud already shortened the play ceiling, renamed the routine and changed the
    /// spoken sentence for a test alarm — but the device was never told, so the audit row it
    /// wrote for a test ring was byte-identical to one for a real 07:30. Kept on the arm and
    /// persisted with it, because the ring that matters most for this is the LOCAL fallback:
    /// it happens when the cloud is not there to label it afterwards.
    /// </remarks>
    public bool IsTest { get; init; }

    /// <summary>
    /// B47 (req 259's local trigger): how long a local "ertele" defers this alarm, as the cloud's
    /// alarm row says. Null when the cloud did not send it - and then the device does not
    /// snooze on its own, because a default of its own would be a second clock.
    /// </summary>
    public int? SnoozeMinutes { get; init; }

    /// <summary>How many more snoozes the alarm row allows (its limit minus its count).</summary>
    public int? SnoozesLeft { get; init; }

    /// <summary>The moment this device rings, if nothing has consumed the arm by then.</summary>
    public DateTimeOffset FireLocalAt => FireAt + TimeSpan.FromSeconds(GraceSeconds);

    public JsonObject ToJson()
    {
        var node = new JsonObject
        {
            ["alarm_id"] = AlarmId,
            ["fire_at"] = FireAt.ToString("O", CultureInfo.InvariantCulture),
            ["grace_s"] = GraceSeconds,
            ["armed_at"] = ArmedAt.ToString("O", CultureInfo.InvariantCulture),
        };
        if (Label is not null)
        {
            node["label"] = Label;
        }

        if (WakeVolume is not null)
        {
            node["wake_volume"] = WakeVolume.DeepClone();
        }

        if (MaxDurationSeconds is not null)
        {
            node["max_duration_s"] = MaxDurationSeconds.Value;
        }

        if (SnoozeMinutes is not null)
        {
            node["snooze_minutes"] = SnoozeMinutes.Value;
        }

        if (SnoozesLeft is not null)
        {
            node["snoozes_left"] = SnoozesLeft.Value;
        }

        if (IsTest)
        {
            // Written only when true: a store file full of `"is_test": false` says nothing,
            // and the absence of the key already means "a real alarm" for every arm ever
            // persisted before this field existed.
            node["is_test"] = true;
        }

        return node;
    }

    public static ArmedAlarm? FromJson(JsonNode? node)
    {
        if (node is not JsonObject row)
        {
            return null;
        }

        var alarmId = row["alarm_id"]?.GetValue<string>();
        var fireAtRaw = row["fire_at"]?.GetValue<string>();
        if (string.IsNullOrWhiteSpace(alarmId)
            || !DateTimeOffset.TryParse(
                fireAtRaw, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var fireAt))
        {
            return null;
        }

        DateTimeOffset armedAt = fireAt;
        var armedRaw = row["armed_at"]?.GetValue<string>();
        if (DateTimeOffset.TryParse(armedRaw, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var parsedArmed))
        {
            armedAt = parsedArmed;
        }

        return new ArmedAlarm
        {
            AlarmId = alarmId,
            FireAt = fireAt,
            GraceSeconds = row["grace_s"]?.GetValue<int>() ?? 0,
            Label = row["label"]?.GetValue<string>(),
            // Detached from the document being read: a node that still has a parent cannot be
            // added to another object later, and the failure would surface at ring time.
            WakeVolume = row["wake_volume"] is JsonObject wake ? (JsonObject)wake.DeepClone() : null,
            MaxDurationSeconds = row["max_duration_s"]?.GetValue<int>(),
            ArmedAt = armedAt,
            IsTest = row["is_test"]?.GetValue<bool>() ?? false,
            SnoozeMinutes = row["snooze_minutes"]?.GetValue<int>(),
            SnoozesLeft = row["snoozes_left"]?.GetValue<int>(),
        };
    }
}

/// <summary>
/// The armed alarms, on disk, so a companion restart between "arm" and "ring" does not lose the
/// owner's wake-up (DEVICE_PROTOCOL.md §6f).
///
/// <para><b>Why on disk at all.</b> The failure this exists for is the one where the network is
/// down at 06:30 — the cloud cannot reach the device to ring the alarm, and an in-memory arm
/// would also be gone if the companion had restarted overnight. A file the companion writes
/// when the arm is created is the only version of this that survives both.</para>
///
/// <para><b>Atomic by write-then-move</b>, the same shape the idempotency store and the
/// enrollment state use: a half-written alarm file after a power cut would be worse than none,
/// because it would parse as "nothing is armed" while the owner believed otherwise. A file that
/// cannot be parsed at all is moved aside as <c>.corrupt</c> and reported, never silently
/// deleted.</para>
///
/// <para>It lives under <c>%LOCALAPPDATA%\PagentOS\companion</c> — the OWNER's profile, not the
/// service's machine data directory. This is owner-session state written by a non-elevated
/// process, and putting it in the service's tree would either fail on the ACL or force the
/// companion to be trusted with machine material it has no other reason to touch.</para>
/// </summary>
public sealed class ArmedAlarmStore
{
    private readonly string _path;
    private readonly ILogger _logger;
    private readonly Dictionary<string, ArmedAlarm> _alarms = new(StringComparer.Ordinal);

    public ArmedAlarmStore(string path, ILogger logger)
    {
        _path = path;
        _logger = logger;
        Load();
    }

    /// <summary><c>%LOCALAPPDATA%\PagentOS\companion\armed-alarms.json</c>.</summary>
    public static string DefaultPath()
        => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "PagentOS",
            "companion",
            "armed-alarms.json");

    public string FilePath => _path;

    public int Count => _alarms.Count;

    /// <summary>Every arm, earliest local fire time first.</summary>
    public IReadOnlyList<ArmedAlarm> All => [.. _alarms.Values.OrderBy(a => a.FireLocalAt)];

    /// <summary>The next moment this device would ring on its own, or null when nothing is armed.</summary>
    public DateTimeOffset? NextFireLocalAt
        => _alarms.Count == 0 ? null : _alarms.Values.Min(a => a.FireLocalAt);

    public bool Contains(string alarmId) => _alarms.ContainsKey(alarmId);

    /// <summary>Adds or replaces an arm by id. Returns true when it replaced an existing one.</summary>
    public bool Upsert(ArmedAlarm alarm)
    {
        var replaced = _alarms.ContainsKey(alarm.AlarmId);
        _alarms[alarm.AlarmId] = alarm;
        Save();
        return replaced;
    }

    /// <summary>Removes one arm. Returns true when there was one to remove.</summary>
    public bool Remove(string alarmId)
    {
        if (!_alarms.Remove(alarmId))
        {
            return false;
        }

        Save();
        return true;
    }

    /// <summary>Removes every arm. Returns how many there were.</summary>
    public int RemoveAll()
    {
        var count = _alarms.Count;
        if (count == 0)
        {
            return 0;
        }

        _alarms.Clear();
        Save();
        return count;
    }

    private void Load()
    {
        if (!File.Exists(_path))
        {
            return;
        }

        try
        {
            var root = JsonNode.Parse(File.ReadAllText(_path)) as JsonObject;
            if (root?["alarms"] is JsonArray rows)
            {
                foreach (var row in rows)
                {
                    var alarm = ArmedAlarm.FromJson(row);
                    if (alarm is not null)
                    {
                        _alarms[alarm.AlarmId] = alarm;
                    }
                }
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning(
                "armed alarm store at {Path} could not be read ({Reason}); it is kept as .corrupt and this companion starts with nothing armed",
                _path,
                ex.Message);
            try
            {
                File.Move(_path, _path + ".corrupt", overwrite: true);
            }
            catch (Exception moveError)
            {
                _logger.LogWarning("could not set the corrupt armed alarm store aside: {Reason}", moveError.Message);
            }

            _alarms.Clear();
        }
    }

    private void Save()
    {
        var rows = new JsonArray();
        foreach (var alarm in All)
        {
            rows.Add(alarm.ToJson());
        }

        var root = new JsonObject { ["version"] = 1, ["alarms"] = rows };
        try
        {
            var directory = Path.GetDirectoryName(Path.GetFullPath(_path));
            if (!string.IsNullOrEmpty(directory))
            {
                Directory.CreateDirectory(directory);
            }

            var temporary = _path + ".tmp";
            File.WriteAllText(temporary, root.ToJsonString(new JsonSerializerOptions { WriteIndented = false }));
            File.Move(temporary, _path, overwrite: true);
        }
        catch (Exception ex)
        {
            // Same rule as the audit log: a failed write degrades the record, it does not kill
            // the path being recorded. The arm stays live in memory for this run; what is lost
            // is only its survival across a restart, and that is said out loud rather than
            // discovered at 06:30.
            _logger.LogError(
                "could not persist the armed alarm store at {Path}: {Reason}. Arms are live for this session only.",
                _path,
                ex.Message);
        }
    }
}
