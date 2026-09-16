namespace PagentOS.Companion.Audio.Listening.Spotting;

/// <summary>
/// Streaming mel-frequency cepstral coefficients: 25 ms frames every 10 ms, pre-emphasis,
/// Hamming window, a 26-band mel filterbank over 60..7600 Hz, log, DCT-II, coefficients 1..12
/// (c0, the loudness term, is dropped so a quiet and a loud "ertele" look alike).
///
/// The features are what the keyword spotter compares; they are LOSSY by design and are the
/// only thing a template ever keeps of an enrolled recording. Everything is plain managed
/// arithmetic - no model, no native library, no network - which is the whole reason this
/// engine exists (no offline Turkish recogniser is installed on the owner's machine).
/// </summary>
public sealed class MfccExtractor
{
    public const int Coefficients = 12;
    public const int FrameMs = 25;
    public const int HopMs = 10;
    private const int Bands = 26;
    private const double LowHz = 60;
    private const double HighHz = 7600;
    private const double PreEmphasis = 0.97;

    private readonly int _frameLength;
    private readonly int _hop;
    private readonly int _fftSize;
    private readonly double[] _window;
    private readonly double[][] _filters;
    private readonly double[,] _dct;
    private readonly List<double> _pending = new();
    private double _previousSample;

    public MfccExtractor(int sampleRate)
    {
        if (sampleRate < 8000)
        {
            throw new ArgumentOutOfRangeException(nameof(sampleRate), "the spotter needs at least 8 kHz audio");
        }

        SampleRate = sampleRate;
        _frameLength = sampleRate * FrameMs / 1000;
        _hop = sampleRate * HopMs / 1000;
        _fftSize = 1;
        while (_fftSize < _frameLength)
        {
            _fftSize <<= 1;
        }

        _window = new double[_frameLength];
        for (var i = 0; i < _frameLength; i++)
        {
            _window[i] = 0.54 - 0.46 * Math.Cos(2 * Math.PI * i / (_frameLength - 1));
        }

        _filters = BuildFilterbank(sampleRate, _fftSize);
        _dct = new double[Coefficients, Bands];
        for (var k = 0; k < Coefficients; k++)
        {
            for (var n = 0; n < Bands; n++)
            {
                _dct[k, n] = Math.Cos(Math.PI * (k + 1) * (n + 0.5) / Bands);
            }
        }
    }

    public int SampleRate { get; }

    /// <summary>Appends PCM16 samples and returns every feature frame that became complete.</summary>
    public IReadOnlyList<float[]> Append(ReadOnlySpan<short> samples)
    {
        foreach (var sample in samples)
        {
            double x = sample / 32768.0;
            _pending.Add(x - PreEmphasis * _previousSample);
            _previousSample = x;
        }

        var result = new List<float[]>();
        while (_pending.Count >= _frameLength)
        {
            result.Add(Compute(_pending, 0));
            _pending.RemoveRange(0, _hop);
        }

        return result;
    }

    public void Reset()
    {
        _pending.Clear();
        _previousSample = 0;
    }

    /// <summary>Features for a whole recording (enrollment, tests).</summary>
    public static float[][] Extract(ReadOnlySpan<short> samples, int sampleRate)
    {
        var extractor = new MfccExtractor(sampleRate);
        return [.. extractor.Append(samples)];
    }

    /// <summary>Cepstral mean normalisation: removes the microphone's and the room's constant colouring.</summary>
    public static float[][] Normalise(IReadOnlyList<float[]> frames)
    {
        if (frames.Count == 0)
        {
            return [];
        }

        var dims = frames[0].Length;
        var mean = new double[dims];
        foreach (var frame in frames)
        {
            for (var d = 0; d < dims; d++)
            {
                mean[d] += frame[d];
            }
        }

        for (var d = 0; d < dims; d++)
        {
            mean[d] /= frames.Count;
        }

        var result = new float[frames.Count][];
        for (var i = 0; i < frames.Count; i++)
        {
            var normalised = new float[dims];
            for (var d = 0; d < dims; d++)
            {
                normalised[d] = (float)(frames[i][d] - mean[d]);
            }

            result[i] = normalised;
        }

        return result;
    }

    private float[] Compute(List<double> source, int offset)
    {
        var real = new double[_fftSize];
        var imaginary = new double[_fftSize];
        for (var i = 0; i < _frameLength; i++)
        {
            real[i] = source[offset + i] * _window[i];
        }

        Fft(real, imaginary);
        var bins = _fftSize / 2 + 1;
        var power = new double[bins];
        for (var i = 0; i < bins; i++)
        {
            power[i] = (real[i] * real[i] + imaginary[i] * imaginary[i]) / _fftSize;
        }

        var logEnergies = new double[Bands];
        for (var b = 0; b < Bands; b++)
        {
            double sum = 0;
            var filter = _filters[b];
            for (var i = 0; i < bins; i++)
            {
                sum += filter[i] * power[i];
            }

            logEnergies[b] = Math.Log(Math.Max(sum, 1e-10));
        }

        var coefficients = new float[Coefficients];
        for (var k = 0; k < Coefficients; k++)
        {
            double sum = 0;
            for (var n = 0; n < Bands; n++)
            {
                sum += logEnergies[n] * _dct[k, n];
            }

            coefficients[k] = (float)sum;
        }

        return coefficients;
    }

    private static double[][] BuildFilterbank(int sampleRate, int fftSize)
    {
        static double ToMel(double hz) => 2595 * Math.Log10(1 + hz / 700);
        static double ToHz(double mel) => 700 * (Math.Pow(10, mel / 2595) - 1);

        var high = Math.Min(HighHz, sampleRate / 2.0);
        var lowMel = ToMel(LowHz);
        var highMel = ToMel(high);
        var bins = fftSize / 2 + 1;
        var points = new double[Bands + 2];
        for (var i = 0; i < points.Length; i++)
        {
            var hz = ToHz(lowMel + (highMel - lowMel) * i / (Bands + 1));
            points[i] = hz * fftSize / sampleRate;
        }

        var filters = new double[Bands][];
        for (var b = 0; b < Bands; b++)
        {
            var filter = new double[bins];
            double left = points[b], centre = points[b + 1], right = points[b + 2];
            for (var i = 0; i < bins; i++)
            {
                if (i > left && i <= centre)
                {
                    filter[i] = (i - left) / (centre - left);
                }
                else if (i > centre && i < right)
                {
                    filter[i] = (right - i) / (right - centre);
                }
            }

            filters[b] = filter;
        }

        return filters;
    }

    /// <summary>In-place iterative radix-2 FFT.</summary>
    private static void Fft(double[] real, double[] imaginary)
    {
        var n = real.Length;
        for (int i = 1, j = 0; i < n; i++)
        {
            var bit = n >> 1;
            for (; (j & bit) != 0; bit >>= 1)
            {
                j ^= bit;
            }

            j ^= bit;
            if (i < j)
            {
                (real[i], real[j]) = (real[j], real[i]);
                (imaginary[i], imaginary[j]) = (imaginary[j], imaginary[i]);
            }
        }

        for (var length = 2; length <= n; length <<= 1)
        {
            var angle = -2 * Math.PI / length;
            double wReal = Math.Cos(angle), wImaginary = Math.Sin(angle);
            for (var start = 0; start < n; start += length)
            {
                double curReal = 1, curImaginary = 0;
                for (var k = 0; k < length / 2; k++)
                {
                    var evenIndex = start + k;
                    var oddIndex = evenIndex + length / 2;
                    var oddReal = real[oddIndex] * curReal - imaginary[oddIndex] * curImaginary;
                    var oddImaginary = real[oddIndex] * curImaginary + imaginary[oddIndex] * curReal;
                    real[oddIndex] = real[evenIndex] - oddReal;
                    imaginary[oddIndex] = imaginary[evenIndex] - oddImaginary;
                    real[evenIndex] += oddReal;
                    imaginary[evenIndex] += oddImaginary;
                    var nextReal = curReal * wReal - curImaginary * wImaginary;
                    curImaginary = curReal * wImaginary + curImaginary * wReal;
                    curReal = nextReal;
                }
            }
        }
    }
}
