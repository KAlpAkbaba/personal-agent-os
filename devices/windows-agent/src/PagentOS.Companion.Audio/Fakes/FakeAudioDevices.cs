using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Timing;

namespace PagentOS.Companion.Audio.Fakes;

public sealed class FakeDeviceCatalog : IAudioDeviceCatalog
{
    private readonly object _sync = new();
    private List<AudioDeviceInfo> _devices = new();

    public event Action? DevicesChanged;

    public IReadOnlyList<AudioDeviceInfo> List(AudioDirection direction)
    {
        lock (_sync)
        {
            return _devices.Where(d => d.Direction == direction).ToList();
        }
    }

    /// <summary>Replace the device set (a plug/unplug) and notify, as WASAPI would.</summary>
    public void Set(params AudioDeviceInfo[] devices)
    {
        lock (_sync)
        {
            _devices = devices.ToList();
        }

        DevicesChanged?.Invoke();
    }

    public static AudioDeviceInfo LaptopMic(bool isDefault = true, bool isCommunications = true)
        => new("cap-laptop", "Microphone Array (Realtek)", AudioDirection.Capture, AudioFormFactor.BuiltIn, isDefault, isCommunications);

    public static AudioDeviceInfo HeadsetMic(bool isDefault = false, bool isCommunications = false)
        => new("cap-headset", "Headset Microphone (USB)", AudioDirection.Capture, AudioFormFactor.Headset, isDefault, isCommunications);

    public static AudioDeviceInfo LaptopSpeakers(bool isDefault = true, bool isCommunications = true)
        => new("ren-laptop", "Speakers (Realtek)", AudioDirection.Render, AudioFormFactor.Speakers, isDefault, isCommunications);

    public static AudioDeviceInfo HeadsetEarpiece(bool isDefault = false, bool isCommunications = false)
        => new("ren-headset", "Headset Earphone (USB)", AudioDirection.Render, AudioFormFactor.Headset, isDefault, isCommunications);
}

public sealed class FakeCapture(string deviceId, AudioFormat format) : IAudioCapture
{
    public string DeviceId { get; } = deviceId;

    public AudioFormat Format { get; } = format;

    public bool Started { get; private set; }

    public int StartCount { get; private set; }

    public bool Disposed { get; private set; }

    public event Action<AudioFrame>? FrameCaptured;

    public void Start()
    {
        Started = true;
        StartCount++;
    }

    public void Stop() => Started = false;

    /// <summary>Delivers a frame as the capture thread would; ignored while stopped.</summary>
    public void Feed(AudioFrame frame)
    {
        if (Started)
        {
            FrameCaptured?.Invoke(frame);
        }
    }

    public void Dispose()
    {
        Started = false;
        Disposed = true;
    }
}

/// <summary>
/// Playback that keeps a queue and, optionally, advances a <see cref="ManualTimeProvider"/>
/// by a fixed amount inside <see cref="StopImmediately"/> so the measured stop latency is an
/// exact, asserted number.
/// </summary>
public sealed class FakePlayback(string deviceId, AudioFormat format, TimeProvider time, List<string>? trace = null) : IAudioPlayback
{
    private readonly object _sync = new();
    private int _queuedBytes;
    private bool _playedSomething;

    public string DeviceId { get; } = deviceId;

    public AudioFormat Format { get; } = format;

    public bool Started { get; private set; }

    public bool Disposed { get; private set; }

    public int EnqueuedBytes { get; private set; }

    public List<long> StopCalls { get; } = new();

    /// <summary>Simulated cost of the stop; applied to a manual clock when one is in use.</summary>
    public double SimulatedStopMs { get; set; }

    public int SimulatedResidualLatencyMs { get; set; } = 20;

    public event Action? Drained;

    public bool IsPlaying
    {
        get
        {
            lock (_sync)
            {
                return Started && _queuedBytes > 0;
            }
        }
    }

    public int QueuedMs
    {
        get
        {
            lock (_sync)
            {
                return (int)Format.MsForBytes(_queuedBytes);
            }
        }
    }

    public void Start() => Started = true;

    public void Enqueue(ReadOnlySpan<byte> pcm16)
    {
        lock (_sync)
        {
            _queuedBytes += pcm16.Length;
            EnqueuedBytes += pcm16.Length;
            _playedSomething = true;
        }
    }

    public PlaybackStopReport StopImmediately()
    {
        trace?.Add("playback:stop");
        StopCalls.Add(time.GetTimestamp());
        if (time is ManualTimeProvider manual && SimulatedStopMs > 0)
        {
            manual.AdvanceMs(SimulatedStopMs);
        }

        int discarded;
        lock (_sync)
        {
            discarded = (int)Format.MsForBytes(_queuedBytes);
            _queuedBytes = 0;
        }

        return new PlaybackStopReport(discarded, SimulatedResidualLatencyMs);
    }

    /// <summary>The device "consumed" everything queued.</summary>
    public void Drain()
    {
        bool raise;
        lock (_sync)
        {
            raise = _playedSomething && _queuedBytes > 0;
            _queuedBytes = 0;
        }

        if (raise)
        {
            Drained?.Invoke();
        }
    }

    public void Dispose()
    {
        Started = false;
        Disposed = true;
    }
}

public sealed class FakeDeviceFactory(TimeProvider time, List<string>? trace = null) : IAudioDeviceFactory
{
    public List<FakeCapture> Captures { get; } = new();

    public List<FakePlayback> Playbacks { get; } = new();

    public double SimulatedStopMs { get; set; }

    public FakeCapture? CurrentCapture => Captures.LastOrDefault(c => !c.Disposed);

    public FakePlayback? CurrentPlayback => Playbacks.LastOrDefault(p => !p.Disposed);

    public IAudioCapture OpenCapture(string deviceId, AudioFormat format)
    {
        var capture = new FakeCapture(deviceId, format);
        Captures.Add(capture);
        return capture;
    }

    public IAudioPlayback OpenPlayback(string deviceId, AudioFormat format)
    {
        var playback = new FakePlayback(deviceId, format, time, trace) { SimulatedStopMs = SimulatedStopMs };
        Playbacks.Add(playback);
        return playback;
    }
}
