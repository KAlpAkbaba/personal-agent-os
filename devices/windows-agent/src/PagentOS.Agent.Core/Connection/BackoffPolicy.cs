namespace PagentOS.Agent.Core.Connection;

/// <summary>
/// Exponential backoff with full jitter (DEVICE_PROTOCOL.md §1): the delay for attempt n is a
/// uniform random value in [0, min(max, base * 2^n)]. Defaults: base 1 s, max 60 s, factor 2.
/// </summary>
public sealed class BackoffPolicy(double baseSeconds = 1.0, double maxSeconds = 60.0, Random? random = null)
{
    private readonly Random _random = random ?? Random.Shared;

    public double BaseSeconds { get; } = baseSeconds;

    public double MaxSeconds { get; } = maxSeconds;

    public TimeSpan NextDelay(int attempt)
    {
        ArgumentOutOfRangeException.ThrowIfNegative(attempt);
        var cap = Math.Min(MaxSeconds, BaseSeconds * Math.Pow(2, attempt));
        double sample;
        lock (_random)
        {
            sample = _random.NextDouble();
        }

        return TimeSpan.FromSeconds(sample * cap);
    }
}
