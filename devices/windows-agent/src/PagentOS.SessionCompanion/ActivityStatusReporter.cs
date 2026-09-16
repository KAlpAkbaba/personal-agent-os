using System.Globalization;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion;

/// <summary>
/// Executes <c>desktop.activity_status</c> (DEVICE_PROTOCOL.md §6g, M18.3) and produces the same
/// object the Device Service attaches to its heartbeat.
///
/// <para><b>One shape, one producer.</b> If the heartbeat carried a status assembled separately
/// from the capability's answer, the two would drift, and the version Cloud Core reasons about
/// most often would be the one nothing tests. So this class is the only producer, its keys are
/// the ones <see cref="HeartbeatStatus.Fields"/> names, and the service forwards what it is
/// given after projecting it onto that same list.</para>
///
/// <para><b>It reports, it does not decide.</b> There is no "the owner is asleep" field here and
/// there will not be one: this device can say how long since the last input and what the display
/// was last seen doing, and the inference from those to a person's state belongs where it can be
/// explained, argued with and turned off — in Cloud Core, against the presence model
/// (M18_THREAT_MODEL.md §4). A device that shipped its own conclusion would make that argument
/// unreachable.</para>
/// </summary>
public sealed class ActivityStatusReporter(
    IInputActivitySource input,
    IDisplayStateObserver displayObserver,
    Func<string?> ringingAlarmId,
    AlarmArmController? arms = null,
    Func<JsonObject>? voice = null,
    Camera.CameraPresenceMonitor? camera = null)
{
    /// <summary>
    /// Payload: <c>{}</c>. Result: the fields of <see cref="HeartbeatStatus.Fields"/> (the camera pair only when a camera path is wired).
    /// Never throws for a missing subsystem — an unwired one reports null or zero, which is a
    /// true statement about this device.
    /// </summary>
    public JsonObject Report(JsonObject payload)
    {
        ArgumentNullException.ThrowIfNull(payload);
        return Compose();
    }

    /// <summary>The status object itself, for the heartbeat path and for tests.</summary>
    public JsonObject Compose()
    {
        var idle = input.IdleTime;
        var observation = displayObserver.Current;
        var ringing = ringingAlarmId();
        var next = arms?.NextFireLocalAt;
        var fired = new JsonArray();
        foreach (var id in arms?.DrainLocallyFiredIds() ?? [])
        {
            fired.Add(id);
        }

        // B47: drained for the same reason as `fired` - composing a status IS the report.
        var snoozed = arms?.DrainLocallySnoozed() ?? [];

        var status = new JsonObject
        {
            [HeartbeatStatus.InputIdleSeconds] = idle is null
                ? null
                : JsonValue.Create(Math.Round(idle.Value.TotalSeconds, 1)),
            [HeartbeatStatus.DisplayState] = observation.State,
            [HeartbeatStatus.DisplayObservedAt] = observation.ObservedAt is null
                ? null
                : JsonValue.Create(observation.ObservedAt.Value.ToString("O", CultureInfo.InvariantCulture)),
            [HeartbeatStatus.AlarmRinging] = ringing is not null,
            [HeartbeatStatus.RingingAlarmId] = ringing,
            [HeartbeatStatus.ArmedAlarms] = arms?.ArmedCount ?? 0,
            [HeartbeatStatus.NextAlarmAt] = next is null
                ? null
                : JsonValue.Create(next.Value.ToString("O", CultureInfo.InvariantCulture)),
            // B13 req 282. DRAINED, not read: composing a status is the act of reporting, so
            // an id leaves here exactly once. A field that only grew would make one local ring
            // look like a ring on every heartbeat until the process restarted, and the cloud
            // would keep re-reconciling an alarm it had already closed.
            [HeartbeatStatus.LocalAlarmFired] = fired,
            [HeartbeatStatus.LocalAlarmSnoozed] = snoozed,
            // B47 rows 250-252: the voice service's compact health. A companion without voice
            // says "disabled", which is true, rather than leaving the cloud to guess.
            [HeartbeatStatus.Voice] = voice?.Invoke() ?? VoiceStatus.Disabled().Heartbeat(),
        };

        // B48 (rows 326, 327): the camera's own state and its latest DERIVED observation.
        // A companion with no camera path sends neither key - "not known", never "absent".
        if (camera is not null)
        {
            status[HeartbeatStatus.Camera] = camera.StatusObject();
            status[HeartbeatStatus.Presence] = camera.LatestObservation();
        }

        return status;
    }
}
