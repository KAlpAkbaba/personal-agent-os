using PagentOS.Companion.Audio.Listening;

namespace PagentOS.SessionCompanion;

/// <summary>The voice status of a companion whose device voice service is not running.</summary>
public static class VoiceStatus
{
    /// <summary>A fresh health object: <c>state: "disabled"</c>, indicator off, not listening.</summary>
    public static DeviceVoiceHealth Disabled() => new();
}
