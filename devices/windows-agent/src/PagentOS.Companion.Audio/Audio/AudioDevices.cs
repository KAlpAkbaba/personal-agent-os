namespace PagentOS.Companion.Audio.Audio;

public enum AudioDirection
{
    Capture,
    Render,
}

/// <summary>
/// What kind of thing a device is, as far as the client can tell. It matters for one
/// decision: whether the microphone can hear the speakers. A headset's microphone does
/// not, so barge-in can trust the VAD outright; a laptop microphone next to laptop
/// speakers does, so the VAD must demand a margin over the echo while playback runs.
/// </summary>
public enum AudioFormFactor
{
    Unknown,
    Headset,
    Headphones,
    Handset,
    Bluetooth,
    Usb,
    BuiltIn,
    Speakers,
    Microphone,
}

public sealed record AudioDeviceInfo(
    string Id,
    string Name,
    AudioDirection Direction,
    AudioFormFactor FormFactor,
    bool IsDefault,
    bool IsDefaultCommunications)
{
    /// <summary>True when playback through this device is near the ear rather than in the room.</summary>
    public bool IsHeadsetLike => FormFactor is AudioFormFactor.Headset
        or AudioFormFactor.Headphones
        or AudioFormFactor.Handset
        or AudioFormFactor.Bluetooth;
}

public interface IAudioDeviceCatalog
{
    IReadOnlyList<AudioDeviceInfo> List(AudioDirection direction);

    /// <summary>Raised when an endpoint arrives, leaves, or a default changes. Thread-agnostic.</summary>
    event Action? DevicesChanged;
}

public interface IAudioCapture : IDisposable
{
    string DeviceId { get; }

    AudioFormat Format { get; }

    /// <summary>Raised on the capture thread with a frame whose <see cref="AudioFrame.CapturedAt"/> is already stamped.</summary>
    event Action<AudioFrame>? FrameCaptured;

    void Start();

    void Stop();
}

/// <summary>What <see cref="IAudioPlayback.StopImmediately"/> did: how much queued audio it threw away and how much may still be audible from the device buffer.</summary>
public readonly record struct PlaybackStopReport(int DiscardedMs, int ResidualLatencyMs);

public interface IAudioPlayback : IDisposable
{
    string DeviceId { get; }

    AudioFormat Format { get; }

    bool IsPlaying { get; }

    /// <summary>Audio accepted but not yet handed to the device.</summary>
    int QueuedMs { get; }

    /// <summary>Raised (on any thread) when the queue runs dry after having played something.</summary>
    event Action? Drained;

    void Start();

    void Enqueue(ReadOnlySpan<byte> pcm16);

    /// <summary>
    /// The latency-critical barge-in action: drop everything queued and silence the device as
    /// fast as the backend allows. Must be safe to call when nothing is playing.
    /// </summary>
    PlaybackStopReport StopImmediately();
}

public interface IAudioDeviceFactory
{
    IAudioCapture OpenCapture(string deviceId, AudioFormat format);

    IAudioPlayback OpenPlayback(string deviceId, AudioFormat format);
}
