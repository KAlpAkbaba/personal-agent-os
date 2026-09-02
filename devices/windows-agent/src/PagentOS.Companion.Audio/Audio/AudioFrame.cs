using System.Runtime.InteropServices;

namespace PagentOS.Companion.Audio.Audio;

/// <summary>
/// One block of captured or synthesized PCM16 audio plus the monotonic timestamp
/// (<see cref="TimeProvider.GetTimestamp"/>) at which its last sample was captured. The
/// timestamp is what makes <c>mic_to_uplink_ms</c> measurable: it travels with the frame
/// from the capture callback to the moment the media leg hands it to the socket.
/// </summary>
public sealed class AudioFrame
{
    public AudioFrame(AudioFormat format, byte[] pcm16, long capturedAt)
    {
        ArgumentNullException.ThrowIfNull(format);
        ArgumentNullException.ThrowIfNull(pcm16);
        if (pcm16.Length % format.BytesPerSampleFrame != 0)
        {
            throw new ArgumentException(
                $"pcm16 length {pcm16.Length} is not a whole number of {format.BytesPerSampleFrame}-byte sample frames",
                nameof(pcm16));
        }

        Format = format;
        Pcm16 = pcm16;
        CapturedAt = capturedAt;
    }

    public AudioFormat Format { get; }

    /// <summary>Mutable on purpose: processors (DC blocker, gate) work in place.</summary>
    public byte[] Pcm16 { get; }

    public long CapturedAt { get; }

    public int SampleCount => Pcm16.Length / Format.BytesPerSampleFrame;

    public double DurationMs => Format.MsForSamples(SampleCount);

    public Span<short> Samples => MemoryMarshal.Cast<byte, short>(Pcm16.AsSpan());

    public static AudioFrame Silence(AudioFormat format, int milliseconds, long capturedAt)
        => new(format, new byte[format.BytesForMs(milliseconds)], capturedAt);
}
