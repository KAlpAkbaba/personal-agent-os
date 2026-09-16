using System.Text.Json.Nodes;

namespace PagentOS.Agent.Core.Protocol;

/// <summary>
/// The optional <c>status</c> object a heartbeat may carry (DEVICE_PROTOCOL.md §6g, M18.3) —
/// the same object <c>desktop.activity_status</c> returns, which is the point: there is one
/// shape, produced in one place, and the heartbeat is simply the cheapest way for Cloud Core to
/// see it without asking.
///
/// <para><b>Additive, and the protocol version does not move.</b> A broker that predates this
/// field ignores it; a device that has no companion sends a heartbeat exactly as before. Nothing
/// about presence, liveness or command handling depends on it in either direction, so a status
/// that is missing is never a fault.</para>
///
/// <para><b>The field list is closed, and the SERVICE closes it.</b> The schema declares
/// <c>additionalProperties: false</c> inside <c>status</c>, so one unknown key from a newer
/// companion would make every heartbeat fail the broker's validation — an unrelated component
/// breaking the connection is exactly the failure mode a bounded contract exists to prevent.
/// <see cref="Project"/> keeps only the keys named here, so the Device Service can never forward
/// a shape the broker will refuse.</para>
///
/// <para><b>What it deliberately does not carry.</b> No key, no character, no pointer position,
/// no window title, no application name. <c>input_idle_s</c> is a duration derived from one tick
/// count (see the companion's input source); the display state is what the operating system
/// broadcast; the alarm fields are this agent's own state. Everything here is a number, a bool,
/// a timestamp or an id this device was handed.</para>
/// </summary>
public static class HeartbeatStatus
{
    /// <summary>Seconds since the last input in the owner's session, or null when unknown.</summary>
    public const string InputIdleSeconds = "input_idle_s";

    /// <summary><c>unknown</c> | <c>off</c> | <c>on</c> | <c>dimmed</c> — observed, never inferred.</summary>
    public const string DisplayState = "display_state";

    /// <summary>When that display state was observed, or null when nothing has been observed.</summary>
    public const string DisplayObservedAt = "display_observed_at";

    /// <summary>Whether an alarm is sounding right now.</summary>
    public const string AlarmRinging = "alarm_ringing";

    /// <summary>The id of the alarm that is sounding, or null.</summary>
    public const string RingingAlarmId = "ringing_alarm_id";

    /// <summary>How many local fallback arms this device is holding.</summary>
    public const string ArmedAlarms = "armed_alarms";

    /// <summary>When this device would next ring on its own, or null.</summary>
    public const string NextAlarmAt = "next_alarm_at";

    /// <summary>
    /// B13 requirement 282: ids this device rang on its own fallback since the last report.
    /// </summary>
    /// <remarks>
    /// The count was already here (<see cref="ArmedAlarms"/> is how many are held, and the
    /// controller counted its rings) and a count cannot answer the question Cloud Core has,
    /// which is not "how many" but "WHICH one". Without the id the cloud knows a fallback rang
    /// and cannot tell that the alarm it is about to fire is the one that already woke the
    /// owner — which is requirement 283, recorded as "the cloud can fire a second time half an
    /// hour later". An id this device was handed, and nothing else: no title, no label, no time.
    /// </remarks>
    public const string LocalAlarmFired = "local_alarm_fired";

    /// <summary>
    /// B47 (B13 requirement 259's local trigger): alarms this device snoozed on its own while
    /// the Cloud Core was unreachable, as <c>{"alarm_id", "until"}</c> objects, drained when
    /// reported. <c>until</c> is when the device will ring again; the cloud adopts that instant
    /// (<c>packages/protocol/device-voice.json</c> <c>local_snooze</c>).
    /// </summary>
    public const string LocalAlarmSnoozed = "local_alarm_snoozed";

    /// <summary>
    /// B47 (rows 250-252): the device voice service's compact health - the closed key set of
    /// <c>device-voice.json</c> <c>heartbeat.keys</c>. States, flags, a counter and an error
    /// class; never a transcript, a phrase or audio.
    /// </summary>
    public const string Voice = "voice";

    /// <summary>
    /// B48 (row 327): the device camera's own state — <c>{mode, state, interval_s, indicator,
    /// last_check_at, error}</c>. Absent on a companion without a camera path.
    /// </summary>
    public const string Camera = "camera";

    /// <summary>
    /// B48 (row 326): the device-local presence provider's latest observation — exactly the
    /// seven fields of M18_HOLOGRAPHIC_CORE_SPEC.md §2 with <c>source: "camera"</c>, or null.
    /// A derived signal: no frame, no image, no byte of one ever rides here.
    /// </summary>
    public const string Presence = "presence";

    /// <summary>Every key the <c>status</c> object may carry, in the order the schema lists them.</summary>
    public static readonly IReadOnlyList<string> Fields =
    [
        InputIdleSeconds, DisplayState, DisplayObservedAt,
        AlarmRinging, RingingAlarmId, ArmedAlarms, NextAlarmAt, LocalAlarmFired,
        LocalAlarmSnoozed, Voice,
        Camera, Presence,
    ];

    /// <summary>
    /// How long the Device Service waits for the companion's status before sending the heartbeat
    /// without one. A heartbeat is a liveness signal first; a slow companion must never be able
    /// to make this device look offline.
    /// </summary>
    public static readonly TimeSpan MaxWait = TimeSpan.FromMilliseconds(1500);

    /// <summary>
    /// Keeps only the known keys, returning null when nothing recognisable is left. Copies the
    /// values so the result shares no node with whatever produced it.
    /// </summary>
    public static JsonObject? Project(JsonObject? raw)
    {
        if (raw is null)
        {
            return null;
        }

        var projected = new JsonObject();
        foreach (var field in Fields)
        {
            if (!raw.TryGetPropertyValue(field, out var value))
            {
                continue;
            }

            projected[field] = field switch
            {
                // B48: the two nested objects are closed as well, and hold scalars only. A
                // companion that put anything else inside them (a frame, a buffer, a nested
                // object) loses that key here - the Session-0 service is the last place on this
                // machine that can make "no image leaves the device" true regardless of what
                // the companion sends.
                Presence => ProjectScalars(value, PresenceFields),
                Camera => ProjectScalars(value, CameraFields),
                _ => value?.DeepClone(),
            };
        }

        return projected.Count == 0 ? null : projected;
    }

    /// <summary>The seven structured-observation keys (M18_HOLOGRAPHIC_CORE_SPEC.md §2).</summary>
    public static readonly IReadOnlyList<string> PresenceFields =
    [
        "person_present", "presence_confidence", "activity_level", "posture", "awake_state", "observed_at", "source",
    ];

    /// <summary>The camera state keys.</summary>
    public static readonly IReadOnlyList<string> CameraFields =
    [
        "mode", "state", "interval_s", "indicator", "last_check_at", "error",
    ];

    /// <summary>The longest string either nested object may carry: every legitimate value is a short token or a timestamp.</summary>
    public const int MaxNestedStringLength = 64;

    private static JsonNode? ProjectScalars(JsonNode? value, IReadOnlyList<string> keys)
    {
        if (value is not JsonObject source)
        {
            return null;
        }

        var result = new JsonObject();
        foreach (var key in keys)
        {
            if (!source.TryGetPropertyValue(key, out var item))
            {
                continue;
            }

            if (item is null)
            {
                result[key] = null;
                continue;
            }

            if (item is not JsonValue scalar)
            {
                return null;
            }

            if (scalar.TryGetValue<string>(out var text) && text.Length > MaxNestedStringLength)
            {
                return null;
            }

            result[key] = scalar.DeepClone();
        }

        return result;
    }
}
