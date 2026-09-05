using PagentOS.Companion.Audio.Audio;

namespace PagentOS.SessionCompanion;

/// <summary>
/// Generates the alarm's sound: a pulsed two-tone chime as interleaved PCM16, the amplitude
/// scaled by the ramp's current level.
///
/// <para>This is a pure function of (format, offset, duration, level) — no device, no state, no
/// clock. That matters for testing: "the alarm never produces a sudden full-volume sample" is
/// a property of these bytes, and a test asserts it by reading the peak amplitude of the
/// buffer rather than by listening to a speaker.</para>
///
/// <para>The level scales the SAMPLES. Nothing in this file, or anywhere in
/// <see cref="AlarmController"/>, touches the Windows master volume or the endpoint's volume:
/// an alarm that raised the system mixer would leave the owner's machine loud long after it
/// stopped, and would do it to every other application at the same time.</para>
/// </summary>
public static class WakeTone
{
    /// <summary>The chime's two pitches, alternated so it reads as an alarm rather than a drone.</summary>
    public const double LowHz = 880.0;

    public const double HighHz = 1174.7;

    /// <summary>One pulse: tone for <see cref="ToneMs"/>, then silence to <see cref="CycleMs"/>.</summary>
    public const int ToneMs = 450;

    public const int CycleMs = 900;

    /// <summary>The render format. Mono is right for an alarm and halves the bytes pushed per second.</summary>
    public static AudioFormat Format => AudioFormat.Pcm16Mono24k;

    /// <summary>
    /// Renders <paramref name="durationMs"/> of the chime starting <paramref name="offsetMs"/>
    /// into the alarm, at <paramref name="level"/> (0..1). The offset keeps the waveform and
    /// the pulse pattern continuous across successive chunks, so a chunk boundary is not
    /// audible as a click.
    /// </summary>
    public static byte[] Render(AudioFormat format, long offsetMs, int durationMs, double level)
    {
        ArgumentNullException.ThrowIfNull(format);
        if (durationMs <= 0)
        {
            return [];
        }

        // Defence in depth against a caller (or a future edit) that hands in an out-of-range
        // level: the generator itself cannot emit above the ramp ceiling.
        var amplitude = Math.Clamp(level, 0.0, WakeRamp.MaxVolume);

        var samples = format.SamplesForMs(durationMs);
        var buffer = new byte[samples * format.BytesPerSampleFrame];
        var startSample = format.SamplesForMs((int)(offsetMs % int.MaxValue));

        for (var i = 0; i < samples; i++)
        {
            var absolute = startSample + i;
            var tMs = absolute * 1000.0 / format.SampleRate;
            var phaseInCycle = tMs % CycleMs;

            short value = 0;
            if (phaseInCycle < ToneMs)
            {
                // Alternate the pitch every other cycle, and fade each pulse in and out over
                // 40 ms so the pulse itself has no step edge either.
                var cycleIndex = (long)(tMs / CycleMs);
                var hz = cycleIndex % 2 == 0 ? LowHz : HighHz;
                var envelope = PulseEnvelope(phaseInCycle);
                var sample = Math.Sin(2.0 * Math.PI * hz * (absolute / (double)format.SampleRate));
                value = (short)(sample * envelope * amplitude * short.MaxValue);
            }

            for (var channel = 0; channel < format.Channels; channel++)
            {
                var index = ((i * format.Channels) + channel) * 2;
                buffer[index] = (byte)(value & 0xFF);
                buffer[index + 1] = (byte)((value >> 8) & 0xFF);
            }
        }

        return buffer;
    }

    /// <summary>The peak absolute sample of a PCM16 buffer, as a fraction of full scale. Test-facing.</summary>
    public static double PeakLevel(ReadOnlySpan<byte> pcm16)
    {
        var peak = 0;
        for (var i = 0; i + 1 < pcm16.Length; i += 2)
        {
            var sample = (short)(pcm16[i] | (pcm16[i + 1] << 8));
            var magnitude = sample == short.MinValue ? short.MaxValue : Math.Abs((int)sample);
            if (magnitude > peak)
            {
                peak = magnitude;
            }
        }

        return peak / (double)short.MaxValue;
    }

    private const double FadeMs = 40.0;

    private static double PulseEnvelope(double phaseInCycle)
    {
        if (phaseInCycle < FadeMs)
        {
            return phaseInCycle / FadeMs;
        }

        var remaining = ToneMs - phaseInCycle;
        return remaining < FadeMs ? Math.Max(0.0, remaining / FadeMs) : 1.0;
    }
}
