using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Turn;

namespace PagentOS.Companion.Audio.Listening;

/// <summary>Where an admitted utterance starts or ends, in order with its frames.</summary>
public abstract record UtteranceBoundary(long Timestamp);

public sealed record UtteranceStarted(long Timestamp, string Reason) : UtteranceBoundary(Timestamp);

public sealed record UtteranceEnded(long Timestamp, TurnEvent Turn) : UtteranceBoundary(Timestamp);

/// <summary>
/// A capture device whose frames were already admitted by a device-side gate. A consumer that
/// sees this interface takes turn boundaries from it instead of deciding them itself: the gate
/// has seen audio the consumer never will (the silence it withheld), so only the gate can say
/// where an utterance began and ended.
/// </summary>
public interface IUtteranceBoundarySource
{
    /// <summary>Raised on the gate's thread, ordered with <see cref="IAudioCapture.FrameCaptured"/>.</summary>
    event Action<UtteranceBoundary>? Boundary;

    /// <summary>The provider's transcription of the current turn, so the gate's hesitation guard can use it.</summary>
    void ObserveTranscript(string text);

    /// <summary>The names of the processors the gate already ran, for the consumer's honest processing report.</summary>
    IReadOnlyList<string> UpstreamProcessors { get; }
}

/// <summary>
/// The orchestrator's view of the device microphone: not the microphone, but what the
/// <see cref="DeviceListeningService"/> admitted. Opening it opens nothing; starting it
/// subscribes; stopping or disposing it unsubscribes. Whatever device id the consumer chose is
/// irrelevant - device selection belongs to the listening service, which owns the real stream.
/// </summary>
public sealed class GatedCaptureTap : IAudioCapture, IUtteranceBoundarySource
{
    private readonly DeviceListeningService _source;

    internal GatedCaptureTap(DeviceListeningService source, string deviceId, AudioFormat format)
    {
        _source = source;
        DeviceId = deviceId;
        Format = format;
    }

    public string DeviceId { get; }

    public AudioFormat Format { get; }

    public bool Started { get; private set; }

    public event Action<AudioFrame>? FrameCaptured;

    public event Action<UtteranceBoundary>? Boundary;

    public IReadOnlyList<string> UpstreamProcessors => _source.ProcessorNames;

    public void Start()
    {
        Started = true;
        _source.Attach(this);
    }

    public void Stop()
    {
        Started = false;
        _source.Detach(this);
    }

    public void ObserveTranscript(string text) => _source.ObserveTranscript(text);

    public void Dispose() => Stop();

    internal void Deliver(AudioFrame frame) => FrameCaptured?.Invoke(frame);

    internal void Deliver(UtteranceBoundary boundary) => Boundary?.Invoke(boundary);
}

/// <summary>
/// The factory the orchestrator is given in device-listening mode: capture is the gate's tap,
/// playback is the real device.
/// </summary>
public sealed class GatedCaptureFactory(DeviceListeningService source, IAudioDeviceFactory playback) : IAudioDeviceFactory
{
    public IAudioCapture OpenCapture(string deviceId, AudioFormat format) => source.OpenTap(deviceId, format);

    public IAudioPlayback OpenPlayback(string deviceId, AudioFormat format) => playback.OpenPlayback(deviceId, format);
}
