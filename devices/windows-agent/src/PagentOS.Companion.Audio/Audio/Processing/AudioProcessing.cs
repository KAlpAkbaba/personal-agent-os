namespace PagentOS.Companion.Audio.Audio.Processing;

/// <summary>What the pipeline knows about the far end while a frame is processed.</summary>
public readonly record struct AudioProcessingContext(bool PlaybackActive, bool RenderIsLoudspeaker);

public interface IAudioProcessor
{
    string Name { get; }

    void Process(Span<short> samples, AudioFormat format, in AudioProcessingContext context);
}

public sealed class ProcessorChain(IReadOnlyList<IAudioProcessor> processors) : IAudioProcessor
{
    public IReadOnlyList<IAudioProcessor> Processors { get; } = processors;

    public string Name => string.Join("+", Processors.Select(p => p.Name));

    public void Process(Span<short> samples, AudioFormat format, in AudioProcessingContext context)
    {
        foreach (var processor in Processors)
        {
            processor.Process(samples, format, in context);
        }
    }
}

/// <summary>
/// First-order high-pass (pole at <c>R</c>): removes DC offset and sub-bass rumble that some
/// laptop microphones deliver and that would otherwise inflate every energy reading the VAD
/// makes. y[n] = x[n] - x[n-1] + R*y[n-1].
/// </summary>
public sealed class DcBlockerProcessor(double r = 0.995) : IAudioProcessor
{
    private double _previousInput;
    private double _previousOutput;

    public string Name => "dc_blocker";

    public void Process(Span<short> samples, AudioFormat format, in AudioProcessingContext context)
    {
        for (var i = 0; i < samples.Length; i++)
        {
            double input = samples[i];
            var output = input - _previousInput + r * _previousOutput;
            _previousInput = input;
            _previousOutput = output;
            samples[i] = (short)Math.Clamp(Math.Round(output), short.MinValue, short.MaxValue);
        }
    }
}

/// <summary>
/// A real, if modest, noise suppressor: frames whose energy sits under the floor are faded
/// towards silence with attack/release smoothing so room tone between words does not reach
/// the provider as "speech", while a genuine onset opens the gate within one frame. This is
/// not spectral suppression and does not pretend to be — see <see cref="AudioProcessingReport"/>.
/// </summary>
public sealed class NoiseGateProcessor(double floorDbfs = -55, double attackMs = 5, double releaseMs = 80) : IAudioProcessor
{
    private double _gain = 1.0;

    public string Name => "noise_gate";

    public double FloorDbfs { get; } = floorDbfs;

    public double CurrentGain => _gain;

    public void Process(Span<short> samples, AudioFormat format, in AudioProcessingContext context)
    {
        if (samples.Length == 0)
        {
            return;
        }

        var energy = AudioEnergy.Dbfs(samples);
        var target = energy >= FloorDbfs ? 1.0 : 0.0;
        var frameMs = format.MsForSamples(samples.Length / format.Channels);
        var tau = target > _gain ? attackMs : releaseMs;
        var alpha = tau <= 0 ? 1.0 : 1.0 - Math.Exp(-frameMs / tau);
        _gain += (target - _gain) * alpha;
        if (_gain > 0.999)
        {
            _gain = 1.0;
            return;
        }

        for (var i = 0; i < samples.Length; i++)
        {
            samples[i] = (short)Math.Round(samples[i] * _gain);
        }
    }
}

public static class AudioEnergy
{
    public const double SilenceFloorDbfs = -100;

    /// <summary>RMS level in dB relative to full scale; <see cref="SilenceFloorDbfs"/> for digital silence.</summary>
    public static double Dbfs(ReadOnlySpan<short> samples)
    {
        if (samples.Length == 0)
        {
            return SilenceFloorDbfs;
        }

        double sum = 0;
        foreach (var sample in samples)
        {
            var normalized = sample / 32768.0;
            sum += normalized * normalized;
        }

        var rms = Math.Sqrt(sum / samples.Length);
        return rms <= 1e-9 ? SilenceFloorDbfs : Math.Max(SilenceFloorDbfs, 20 * Math.Log10(rms));
    }
}

/// <summary>
/// Honest inventory of what the shipped pipeline does to microphone audio, written into the
/// audit trail at session start so nobody reads "AEC/NS" in a spec and assumes it is running.
/// </summary>
public sealed record AudioProcessingReport(
    string EchoCancellation,
    string NoiseSuppression,
    string AutomaticGain,
    IReadOnlyList<string> ClientProcessors,
    IReadOnlyList<string> Deferred)
{
    public const string DriverApoCommunications = "driver_apo:communications_category";
    public const string HeadsetNoEchoPath = "not_needed:headset";
    public const string EchoAwareGateOnly = "echo_aware_vad_gate_only";
    public const string NoiseGate = "client:noise_gate";

    public static readonly IReadOnlyList<string> DeferredItems = new[]
    {
        "webrtc_apm (software AEC3 / RNNoise-class NS): no maintained managed port; native build deferred",
        "windows11_IAcousticEchoCancellationControl: requires build 22621+, owner runs 10.0.19045",
        "provider-side input_audio_noise_reduction: enabled by Cloud Core in the provider session config, not here",
    };

    public static AudioProcessingReport Describe(bool driverApoActive, bool renderIsHeadset, IAudioProcessor chain)
    {
        var echo = driverApoActive
            ? DriverApoCommunications
            : renderIsHeadset ? HeadsetNoEchoPath : EchoAwareGateOnly;
        var noise = driverApoActive ? DriverApoCommunications + "+" + NoiseGate : NoiseGate;
        return new AudioProcessingReport(
            echo,
            noise,
            driverApoActive ? DriverApoCommunications : "none",
            chain is ProcessorChain c ? c.Processors.Select(p => p.Name).ToList() : new List<string> { chain.Name },
            DeferredItems);
    }
}
