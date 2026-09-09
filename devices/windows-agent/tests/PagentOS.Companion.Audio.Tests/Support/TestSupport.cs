using PagentOS.Companion.Audio.Audio;

namespace PagentOS.Companion.Audio.Tests.Support;

public static class TestSupport
{
    public static readonly AudioFormat Format = AudioFormat.Pcm16Mono24k;

    /// <summary>Polls until the condition holds or the timeout passes; returns whether it held.</summary>
    /// <summary>
    /// Polls until <paramref name="condition"/> holds, or gives up.
    /// </summary>
    /// <remarks>
    /// The bound is a HANG GUARD, not a latency budget. Every caller writes
    /// <c>Assert.True(await WaitForAsync(...))</c> - nothing in this suite asserts that a wait
    /// TIMES OUT - so the number only decides how long a genuinely stuck loop takes to be
    /// reported. At three seconds it was also deciding whether a busy CI runner passed: on run
    /// 34379161742 the orchestrator's return to Idle did not arrive inside it and the suite
    /// failed with "condition never held" about a loop that was merely slow. The poll's own
    /// `Task.Delay(3)` continuation is queued on the same starved pool, so the effective poll
    /// rate collapses exactly when the machine is busiest.
    /// </remarks>
    public static async Task<bool> WaitForAsync(Func<bool> condition, int timeoutMs = 30_000)
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
