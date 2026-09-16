using System.Globalization;
using Microsoft.Extensions.Logging;
using PagentOS.Companion.Audio.Listening;
using PagentOS.SessionCompanion.Notify;

namespace PagentOS.SessionCompanion.Voice;

/// <summary>
/// Row 253 and B13 requirement 259's local trigger: the device's own answers to the offline
/// command set (<c>packages/protocol/device-voice.json</c> <c>offline_commands</c>), on the same
/// objects the Cloud Core's commands drive - the alarm that is ringing here, its local arm, and
/// the shell toast. The listening service decides WHETHER a command may act (the alarm must be
/// ringing; offline-only commands wait while the Cloud Core is reachable); this class only does
/// what was decided, and says truthfully what happened.
/// </summary>
/// <remarks>
/// "listening.off" is not here: the listening service owns that switch and flips it itself.
/// </remarks>
public sealed class OfflineVoiceCommands(
    AlarmController? alarm,
    AlarmArmController? arms,
    IToastSink? toasts,
    TimeProvider time,
    ILogger logger) : IOfflineCommandSink
{
    public const string Source = "offline_voice";

    public bool AlarmRinging => alarm?.IsRinging == true;

    public OfflineCommandOutcome Execute(string commandId)
    {
        switch (commandId)
        {
            case DeviceVoiceContract.CommandAlarmStop:
                return StopAlarm();
            case DeviceVoiceContract.CommandAlarmSnooze:
                if (arms is null)
                {
                    return new OfflineCommandOutcome(false, AlarmArmController.SnoozeNoOutput);
                }

                var snooze = arms.SnoozeRinging(Source);
                return new OfflineCommandOutcome(snooze.Snoozed, snooze.Detail);
            case DeviceVoiceContract.CommandTimeTell:
                return TellTime();
            default:
                return new OfflineCommandOutcome(false, "unknown_command");
        }
    }

    private OfflineCommandOutcome StopAlarm()
    {
        var ringing = alarm?.RingingAlarmId;
        if (alarm is null || ringing is null)
        {
            return new OfflineCommandOutcome(false, AlarmArmController.SnoozeNotRinging);
        }

        alarm.Stop(new System.Text.Json.Nodes.JsonObject { ["alarm_id"] = ringing });
        arms?.Consume(ringing, Source + "_stop");
        logger.LogInformation("alarm {AlarmId} stopped by an offline voice command", ringing);
        return new OfflineCommandOutcome(true, "stopped");
    }

    private OfflineCommandOutcome TellTime()
    {
        if (toasts is null)
        {
            return new OfflineCommandOutcome(false, "no_toast_sink");
        }

        var local = TimeZoneInfo.ConvertTime(time.GetUtcNow(), time.LocalTimeZone);
        var text = "Saat " + local.ToString("HH:mm", CultureInfo.GetCultureInfo("tr-TR"));
        var shown = toasts.Show(new ToastRequest(
            "voice-time-" + local.ToUnixTimeSeconds().ToString(CultureInfo.InvariantCulture),
            text,
            "Cloud Core'a ulaşılamıyor; saat cihazdan söylendi.",
            "normal",
            "voice-offline",
            []));
        return new OfflineCommandOutcome(shown.Shown, shown.Shown ? "shown" : "not_shown:" + shown.Reason);
    }
}
