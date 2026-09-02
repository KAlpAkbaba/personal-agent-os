namespace PagentOS.Companion.Audio.Audio;

/// <summary>
/// Interleaved 16-bit little-endian PCM. The whole client speaks this one shape internally;
/// device-native formats (float32, stereo, 44.1/48 kHz) are converted at the WASAPI edge by
/// <see cref="PcmConverter"/>, and the provider's wire format is the codec's business.
/// </summary>
public sealed record AudioFormat(int SampleRate, int Channels)
{
    public const int BitsPerSample = 16;

    /// <summary>The realtime providers' common denominator (OpenAI Realtime speaks 24 kHz mono PCM16).</summary>
    public static AudioFormat Pcm16Mono24k { get; } = new(24000, 1);

    public static AudioFormat Pcm16Mono16k { get; } = new(16000, 1);

    public int BytesPerSampleFrame => Channels * (BitsPerSample / 8);

    public int SamplesForMs(int milliseconds) => checked((int)((long)SampleRate * milliseconds / 1000));

    public int BytesForMs(int milliseconds) => SamplesForMs(milliseconds) * BytesPerSampleFrame;

    public double MsForSamples(int samplesPerChannel) => samplesPerChannel * 1000.0 / SampleRate;

    public double MsForBytes(int bytes) => MsForSamples(bytes / BytesPerSampleFrame);
}
