using System.Runtime.InteropServices;
using System.Runtime.Versioning;

namespace PagentOS.Companion.Audio.Listening;

/// <summary>
/// Row 255: whether the capture endpoint is muted, DETECTED from the endpoint itself (the
/// laptop's mic-mute key, the Sound control panel, a headset's mute switch all land there).
/// Null means "cannot tell" - which is reported as such and never read as "not muted".
/// </summary>
public interface IMicMuteMonitor
{
    bool? IsMuted(string deviceId);
}

/// <summary>A monitor for a device that has no endpoint to ask (tests, or a backend without one).</summary>
public sealed class UnknownMicMuteMonitor : IMicMuteMonitor
{
    public static UnknownMicMuteMonitor Instance { get; } = new();

    public bool? IsMuted(string deviceId) => null;
}

public sealed class FakeMicMuteMonitor : IMicMuteMonitor
{
    public bool? Muted { get; set; } = false;

    public int Queries { get; private set; }

    public bool? IsMuted(string deviceId)
    {
        Queries++;
        return Muted;
    }
}

/// <summary>Row 243: the push-to-talk key, read by polling (no hook, no message loop).</summary>
public interface IPushToTalkKey
{
    string Name { get; }

    bool IsDown { get; }
}

public sealed class FakePushToTalkKey(string name = "fake") : IPushToTalkKey
{
    public string Name { get; } = name;

    public bool IsDown { get; set; }
}

/// <summary>
/// <c>GetAsyncKeyState</c> for one virtual key. It reads the state of the input desktop the
/// companion runs on - the owner's - and never installs a hook: nothing else about the
/// keyboard is observed, and nothing is observed at all unless the owner chose push-to-talk.
/// Default: right Ctrl (<c>VK_RCONTROL</c>), which a shortcut rarely holds alone for
/// <see cref="DeviceVoiceContract.PushToTalkMinHoldMs"/>.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class Win32PushToTalkKey(int virtualKey = Win32PushToTalkKey.RightControl) : IPushToTalkKey
{
    public const int RightControl = 0xA3;

    public string Name { get; } = virtualKey == RightControl ? "RightCtrl" : "VK_0x" + virtualKey.ToString("X2");

    public bool IsDown => (GetAsyncKeyState(virtualKey) & 0x8000) != 0;

    /// <summary>"RightCtrl", a hex virtual key ("0x7B"), or null for anything else.</summary>
    public static int? ParseKey(string? raw)
    {
        var value = raw?.Trim();
        if (string.IsNullOrEmpty(value))
        {
            return null;
        }

        if (string.Equals(value, "RightCtrl", StringComparison.OrdinalIgnoreCase))
        {
            return RightControl;
        }

        if (value.StartsWith("0x", StringComparison.OrdinalIgnoreCase)
            && int.TryParse(value[2..], System.Globalization.NumberStyles.HexNumber, null, out var vk)
            && vk is > 0 and < 0xFF)
        {
            return vk;
        }

        return null;
    }

    [DllImport("user32.dll")]
    private static extern short GetAsyncKeyState(int vKey);
}

/// <summary>Row 254: what the owner sees. Implementations must not block the listening loop.</summary>
public interface IPrivacyIndicator
{
    void Show(string indicatorState, string detail);
}

public sealed class RecordingPrivacyIndicator : IPrivacyIndicator
{
    private readonly object _sync = new();
    private readonly List<string> _states = [];

    public IReadOnlyList<string> States
    {
        get
        {
            lock (_sync)
            {
                return [.. _states];
            }
        }
    }

    public string? Current
    {
        get
        {
            lock (_sync)
            {
                return _states.Count == 0 ? null : _states[^1];
            }
        }
    }

    public void Show(string indicatorState, string detail)
    {
        lock (_sync)
        {
            _states.Add(indicatorState);
        }
    }
}

/// <summary>What an offline command did.</summary>
public sealed record OfflineCommandOutcome(bool Executed, string Detail);

/// <summary>Row 253: the local executor of the contract's offline command set.</summary>
public interface IOfflineCommandSink
{
    /// <summary>True while an alarm is sounding on this device (the <c>alarm_ringing</c> condition).</summary>
    bool AlarmRinging { get; }

    OfflineCommandOutcome Execute(string commandId);
}
