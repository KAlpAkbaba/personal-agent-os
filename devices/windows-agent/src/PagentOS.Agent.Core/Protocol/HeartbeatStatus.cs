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

    /// <summary>Every key the <c>status</c> object may carry, in the order the schema lists them.</summary>
    public static readonly IReadOnlyList<string> Fields =
    [
        InputIdleSeconds, DisplayState, DisplayObservedAt,
        AlarmRinging, RingingAlarmId, ArmedAlarms, NextAlarmAt, LocalAlarmFired,
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
            if (raw.TryGetPropertyValue(field, out var value))
            {
                projected[field] = value?.DeepClone();
            }
        }

        return projected.Count == 0 ? null : projected;
    }
}
