using PagentOS.Companion.Audio.Audio;

namespace PagentOS.Companion.Audio.Tests.Support;

public static class TestSupport
{
    public static readonly AudioFormat Format = AudioFormat.Pcm16Mono24k;

    /// <summary>Polls until the condition holds or the timeout passes; returns whether it held.</summary>
    public static async Task<bool> WaitForAsync(Func<bool> condition, int timeoutMs = 3000)
    {
        var deadline = Environment.TickCount64 + timeoutMs;
        while (!condition())
        {
            if (Environment.TickCount64 > deadline)
            {
                return false;
            }

            await Task.Delay(3);
        }

        return true;
    }

    /// <summary>A quiet frame well under the VAD hold threshold (about -54 dBFS of noise).</summary>
    public static AudioFrame Quiet(long capturedAt = 0, int ms = 20) => SyntheticAudio.Noise(Format, ms, capturedAt, amplitude: 0.002);

    /// <summary>Speech stand-in with frame-to-frame level variation, so it does not read as a prolonged vowel.</summary>
    public static IEnumerable<AudioFrame> ModulatedSpeech(SyntheticAudio synth, int totalMs, Func<long> now, int ms = 20)
    {
        var loud = true;
        for (var fed = 0; fed < totalMs; fed += ms)
        {
            yield return synth.Tone(ms, now(), amplitude: loud ? 0.3 : 0.08);
            loud = !loud;
        }
    }
}
