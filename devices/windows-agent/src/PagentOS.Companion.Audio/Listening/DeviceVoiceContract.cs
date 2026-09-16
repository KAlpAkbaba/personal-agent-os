namespace PagentOS.Companion.Audio.Listening;

/// <summary>
/// The device half of <c>packages/protocol/device-voice.json</c> (B47). Every value here is
/// one the Cloud Core reads from the same file; <c>DeviceVoiceContractTests</c> fails when this
/// class and the file disagree, so editing one alone goes red.
/// </summary>
/// <remarks>
/// Constants rather than a run-time read: the contract file is not shipped with the companion,
/// and a device that could not find it must not fall back to a guess about how much audio it
/// may hold. The test is the reader.
/// </remarks>
public static class DeviceVoiceContract
{
    /// <summary>Pre-roll the gate keeps so the first syllable is not clipped.</summary>
    public const int DefaultPreRollMs = 300;

    /// <summary>The hard ceiling on un-admitted audio held in memory. Nothing configures past it.</summary>
    public const int MaxPreRollMs = 2000;

    /// <summary>The most audio the local keyword spotter holds for one utterance.</summary>
    public const int MaxSegmentMs = 2000;

    public const string ModeContinuous = "continuous";
    public const string ModeWakeWord = "wake_word";
    public const string ModePushToTalk = "push_to_talk";

    public static readonly IReadOnlyList<string> Modes = [ModeContinuous, ModeWakeWord, ModePushToTalk];

    /// <summary>The owner's choice of 2026-09-16: listening on, continuously, with no wake word.</summary>
    public const string DefaultMode = ModeContinuous;

    public const bool DefaultEnabled = true;

    /// <summary>A remote command may turn listening off; only the owner at the device may turn it on.</summary>
    public const bool RemoteEnableAllowed = false;

    public const string WakeWordPhraseId = "wake";

    /// <summary>After a wake word (or a push-to-talk turn) the owner may keep talking without repeating it.</summary>
    public const int FollowUpWindowMs = 8000;

    /// <summary>A push-to-talk key held for less than this is a keyboard shortcut, not a request to talk.</summary>
    public const int PushToTalkMinHoldMs = 150;

    public const string IndicatorOff = "off";
    public const string IndicatorMuted = "muted";
    public const string IndicatorListening = "listening";
    public const string IndicatorWakeWord = "wake_word";
    public const string IndicatorPushToTalk = "push_to_talk";
    public const string IndicatorSending = "sending";

    public static readonly IReadOnlyList<string> IndicatorStates =
    [
        IndicatorOff, IndicatorMuted, IndicatorListening, IndicatorWakeWord, IndicatorPushToTalk, IndicatorSending,
    ];

    public const string StateDisabled = "disabled";
    public const string StateStarting = "starting";
    public const string StateRunning = "running";
    public const string StateOffline = "offline";
    public const string StateBackoff = "backoff";
    public const string StateStopped = "stopped";

    public static readonly IReadOnlyList<string> ServiceStates =
    [
        StateDisabled, StateStarting, StateRunning, StateOffline, StateBackoff, StateStopped,
    ];

    /// <summary>The heartbeat status key that carries <see cref="HeartbeatKeys"/>.</summary>
    public const string HeartbeatField = "voice";

    public static readonly IReadOnlyList<string> HeartbeatKeys =
    [
        "state", "indicator", "mode", "listening", "mic_muted", "cloud_connected", "restarts", "last_error",
    ];

    public const string OfflineEngine = "template_dtw";

    public const string CommandAlarmStop = "alarm.stop";
    public const string CommandAlarmSnooze = "alarm.snooze";
    public const string CommandListeningOff = "listening.off";
    public const string CommandTimeTell = "time.tell";

    public const string RequiresAlarmRinging = "alarm_ringing";
    public const string RequiresNone = "none";
    public const string ActsOfflineOnly = "offline_only";
    public const string ActsAlways = "always";

    /// <summary>Every offline command, with the condition it needs and when it may act.</summary>
    public static readonly IReadOnlyList<OfflineCommandRule> OfflineCommands =
    [
        new(CommandAlarmStop, RequiresAlarmRinging, ActsOfflineOnly),
        new(CommandAlarmSnooze, RequiresAlarmRinging, ActsOfflineOnly),
        new(CommandListeningOff, RequiresNone, ActsAlways),
        new(CommandTimeTell, RequiresNone, ActsOfflineOnly),
    ];

    public const string LocalSnoozeField = "local_alarm_snoozed";

    public static readonly IReadOnlyList<string> LocalSnoozeEntryKeys = ["alarm_id", "until"];

    public const int LocalSnoozeMaxEntries = 32;

    public const string PayloadSnoozeMinutes = "snooze_minutes";
    public const string PayloadSnoozesLeft = "snoozes_left";

    public static OfflineCommandRule? RuleFor(string commandId)
        => OfflineCommands.FirstOrDefault(r => string.Equals(r.Id, commandId, StringComparison.Ordinal));
}

/// <summary>One row of the contract's offline command table.</summary>
public sealed record OfflineCommandRule(string Id, string Requires, string Acts);
