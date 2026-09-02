namespace PagentOS.Companion.Audio.Audio;

/// <summary>
/// Device-native samples → the client's PCM16 mono shape. WASAPI shared mode hands out the
/// engine's mix format (typically float32, 2 channels, 48 kHz); the provider wants 16-bit mono
/// at 24 kHz. Down-mix by averaging channels and resample by linear interpolation — coarse
/// against a proper polyphase filter, but deterministic, dependency-free and entirely
/// adequate for speech at these ratios. Kept pure so the resampling arithmetic is tested.
/// </summary>
public sealed class PcmConverter
{
    private readonly int _inputRate;
    private readonly int _inputChannels;
    private readonly bool _inputIsFloat32;
    private readonly AudioFormat _output;
    private double _phase;
    private float _lastSample;
    private bool _hasLast;

    public PcmConverter(int inputSampleRate, int inputChannels, bool inputIsFloat32, AudioFormat output)
    {
        ArgumentOutOfRangeException.ThrowIfLessThanOrEqual(inputSampleRate, 0);
        ArgumentOutOfRangeException.ThrowIfLessThanOrEqual(inputChannels, 0);
        if (output.Channels != 1)
        {
            throw new NotSupportedException("the client pipeline is mono; multi-channel output is not needed");
        }

        _inputRate = inputSampleRate;
        _inputChannels = inputChannels;
        _inputIsFloat32 = inputIsFloat32;
        _output = output;
    }

    public AudioFormat Output => _output;

    /// <summary>Converts a device buffer; returns PCM16 mono bytes at the output rate (possibly empty).</summary>
    public byte[] Convert(ReadOnlySpan<byte> input)
    {
        var mono = ToMonoFloat(input);
        if (mono.Length == 0)
        {
            return Array.Empty<byte>();
        }

        if (_inputRate == _output.SampleRate)
        {
            return ToPcm16(mono);
        }

        return Resample(mono);
    }

    private float[] ToMonoFloat(ReadOnlySpan<byte> input)
    {
        var bytesPerSample = _inputIsFloat32 ? 4 : 2;
        var frames = input.Length / (bytesPerSample * _inputChannels);
        var mono = new float[frames];
        var offset = 0;
        for (var i = 0; i < frames; i++)
        {
            float sum = 0;
            for (var c = 0; c < _inputChannels; c++)
            {
                float sample;
                if (_inputIsFloat32)
                {
                    sample = BitConverter.ToSingle(input.Slice(offset, 4));
                    offset += 4;
                }
                else
                {
                    sample = BitConverter.ToInt16(input.Slice(offset, 2)) / 32768f;
                    offset += 2;
                }

                sum += sample;
            }

            mono[i] = sum / _inputChannels;
        }

        return mono;
    }

    private byte[] Resample(float[] mono)
    {
        var step = (double)_inputRate / _output.SampleRate;
        var outputs = new List<float>((int)(mono.Length / step) + 2);

        // _phase is the fractional read position, relative to the sample before this buffer
        // (_lastSample) when we have one. Carrying it across calls keeps the resampler
        // continuous at buffer boundaries instead of clicking every 10 ms.
        var position = _phase;
        var span = _hasLast ? mono.Length : mono.Length - 1;
        while (position < span)
        {
            var index = (int)Math.Floor(position);
            var fraction = (float)(position - index);
            float a;
            float b;
            if (_hasLast)
            {
                a = index == 0 ? _lastSample : mono[index - 1];
                b = mono[index];
            }
            else
            {
                a = mono[index];
                b = mono[index + 1];
            }

            outputs.Add(a + (b - a) * fraction);
            position += step;
        }

        _phase = position - span;
        _lastSample = mono[^1];
        _hasLast = true;
        return ToPcm16(outputs);
    }

    private static byte[] ToPcm16(IReadOnlyList<float> samples)
    {
        var bytes = new byte[samples.Count * 2];
        for (var i = 0; i < samples.Count; i++)
        {
            var clamped = Math.Clamp(samples[i], -1f, 1f);
            var value = (short)Math.Round(clamped * 32767f);
            bytes[2 * i] = (byte)(value & 0xFF);
            bytes[2 * i + 1] = (byte)((value >> 8) & 0xFF);
        }

        return bytes;
    }
}
