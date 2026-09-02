using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Audio.Processing;
using PagentOS.Companion.Audio.Session;
using PagentOS.Companion.Audio.Tests.Support;
using PagentOS.Companion.Audio.Timing;
using Xunit;

namespace PagentOS.Companion.Audio.Tests;

public sealed class AudioPrimitivesTests
{
    [Fact]
    public void Formats_and_frames_do_their_arithmetic()
    {
        var format = AudioFormat.Pcm16Mono24k;
        Assert.Equal(480, format.SamplesForMs(20));
        Assert.Equal(960, format.BytesForMs(20));
        Assert.Equal(20.0, format.MsForBytes(960));
        var frame = AudioFrame.Silence(format, 20, 5);
        Assert.Equal(480, frame.SampleCount);
        Assert.Equal(20.0, frame.DurationMs);
        Assert.Equal(5, frame.CapturedAt);
        Assert.Throws<ArgumentException>(() => new AudioFrame(new AudioFormat(24000, 2), new byte[3], 0));
    }

    [Fact]
    public void Energy_is_dbfs_and_silence_is_the_floor()
    {
        Assert.Equal(AudioEnergy.SilenceFloorDbfs, AudioEnergy.Dbfs(new short[480]));
        var fullScale = Enumerable.Repeat(short.MaxValue, 480).ToArray();
        Assert.Equal(0, AudioEnergy.Dbfs(fullScale), precision: 2);
        var tone = new SyntheticAudio(TestSupport.Format).Tone(20, 0, amplitude: 0.3);
        Assert.InRange(AudioEnergy.Dbfs(tone.Samples), -14.0, -13.0); // 0.3 sine: 20*log10(0.3/sqrt(2)) = -13.5 dBFS
    }

    [Fact]
    public void The_converter_downmixes_float_stereo_48k_to_pcm16_mono_24k()
    {
        var converter = new PcmConverter(48000, 2, inputIsFloat32: true, AudioFormat.Pcm16Mono24k);
        var input = new byte[960 * 2 * 4]; // 20 ms of stereo float at 48 kHz
        for (var i = 0; i < 960; i++)
        {
            BitConverter.TryWriteBytes(input.AsSpan(i * 8, 4), 0.5f);
            BitConverter.TryWriteBytes(input.AsSpan(i * 8 + 4, 4), -0.5f);
        }

        var output = converter.Convert(input);

        Assert.InRange(output.Length, 956, 960);
        Assert.All(Enumerable.Range(0, output.Length / 2), i => Assert.Equal(0, BitConverter.ToInt16(output, i * 2)));
    }

    [Fact]
    public void The_converter_passes_pcm16_mono_through_at_the_same_rate_and_stays_continuous_across_buffers()
    {
        var same = new PcmConverter(24000, 1, inputIsFloat32: false, AudioFormat.Pcm16Mono24k);
        var frame = new SyntheticAudio(TestSupport.Format).Tone(20, 0, amplitude: 0.3);
        Assert.Equal(frame.Pcm16, same.Convert(frame.Pcm16));

        var down = new PcmConverter(48000, 1, inputIsFloat32: false, AudioFormat.Pcm16Mono24k);
        var ramp = new byte[200 * 2];
        for (var i = 0; i < 200; i++)
        {
            BitConverter.TryWriteBytes(ramp.AsSpan(i * 2, 2), (short)(i * 100));
        }

        var first = down.Convert(ramp.AsSpan(0, 200));
        var second = down.Convert(ramp.AsSpan(200, 200));
        var all = first.Concat(second).ToArray();
        var samples = Enumerable.Range(0, all.Length / 2).Select(i => BitConverter.ToInt16(all, i * 2)).ToArray();
        for (var i = 1; i < samples.Length; i++)
        {
            Assert.InRange(samples[i] - samples[i - 1], 150, 250); // a straight line at half rate steps by ~200
        }
    }

    [Fact]
    public void The_noise_gate_closes_on_room_tone_and_opens_at_once_on_speech()
    {
        var gate = new NoiseGateProcessor(floorDbfs: -50);
        var format = TestSupport.Format;
        var context = new AudioProcessingContext(false, true);

        for (var i = 0; i < 10; i++)
        {
            var quiet = TestSupport.Quiet();
            gate.Process(quiet.Samples, format, in context);
        }

        Assert.True(gate.CurrentGain < 0.15, $"gain {gate.CurrentGain}");
        var quietAfter = TestSupport.Quiet();
        gate.Process(quietAfter.Samples, format, in context);
        Assert.True(AudioEnergy.Dbfs(quietAfter.Samples) < -70);

        var loud = new SyntheticAudio(format).Tone(20, 0, amplitude: 0.3);
        gate.Process(loud.Samples, format, in context);
        Assert.True(gate.CurrentGain > 0.95, $"gain {gate.CurrentGain}");
        Assert.True(AudioEnergy.Dbfs(loud.Samples) > -15);
    }

    [Fact]
    public void The_dc_blocker_removes_a_constant_offset()
    {
        var blocker = new DcBlockerProcessor();
        var format = TestSupport.Format;
        var context = new AudioProcessingContext(false, true);
        for (var pass = 0; pass < 20; pass++)
        {
            var offset = Enumerable.Repeat((short)4000, 480).ToArray();
            blocker.Process(offset, format, in context);
            if (pass == 19)
            {
                Assert.True(Math.Abs(offset[^1]) < 200, $"residual {offset[^1]}");
            }
        }
    }

    [Fact]
    public void Nearest_rank_percentiles_and_the_summary_shape()
    {
        var sorted = new List<double> { 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 };
        Assert.Equal(5, LatencyRecorder.Percentile(sorted, 50));
        Assert.Equal(10, LatencyRecorder.Percentile(sorted, 95));
        Assert.Null(LatencyRecorder.Percentile(new List<double>(), 50));

        var time = new ManualTimeProvider();
        var recorder = new LatencyRecorder(time);
        var turn = recorder.BeginTurn();
        time.AdvanceMs(7);
        turn.FirstUplinkAt = time.GetTimestamp();
        time.AdvanceMs(100);
        turn.SpeechEndedAt = time.GetTimestamp();
        time.AdvanceMs(420);
        turn.FirstAudioAt = time.GetTimestamp();

        var summary = recorder.Summary();
        Assert.Equal(7.0, summary["mic_to_uplink_ms"]!["p50"]!.GetValue<double>());
        Assert.Equal(420.0, summary["eot_to_first_audio_ms"]!["max"]!.GetValue<double>());
        Assert.Null(summary["barge_in_to_stop_ms"]);
        Assert.Single(summary["per_turn"]!.AsArray());
    }

    [Fact]
    public void The_processing_report_names_what_is_real_and_what_is_deferred()
    {
        var chain = new ProcessorChain(new IAudioProcessor[] { new DcBlockerProcessor(), new NoiseGateProcessor() });
        var driver = AudioProcessingReport.Describe(driverApoActive: true, renderIsHeadset: false, chain);
        Assert.Equal(AudioProcessingReport.DriverApoCommunications, driver.EchoCancellation);
        Assert.StartsWith(AudioProcessingReport.DriverApoCommunications, driver.NoiseSuppression);

        var plain = AudioProcessingReport.Describe(driverApoActive: false, renderIsHeadset: false, chain);
        Assert.Equal(AudioProcessingReport.EchoAwareGateOnly, plain.EchoCancellation);
        Assert.Contains(plain.Deferred, d => d.Contains("webrtc_apm"));
        Assert.Contains(plain.Deferred, d => d.Contains("19045"));
    }
}
