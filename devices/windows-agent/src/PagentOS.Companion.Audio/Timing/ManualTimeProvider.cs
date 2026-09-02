namespace PagentOS.Companion.Audio.Timing;

/// <summary>
/// A clock the test moves by hand. Latency arithmetic in this library goes through
/// <see cref="TimeProvider.GetTimestamp"/> / <see cref="TimeProvider.GetElapsedTime(long)"/>
/// only, so a test that advances this clock inside a fake (e.g. "stopping playback took
/// 3 ms") gets an exact, asserted number rather than a flaky wall-clock one.
/// </summary>
public sealed class ManualTimeProvider : TimeProvider
{
    private long _ticks;
    private DateTimeOffset _utcNow = new(2026, 9, 2, 12, 0, 0, TimeSpan.Zero);

    public override long TimestampFrequency => TimeSpan.TicksPerSecond;

    public override long GetTimestamp() => Interlocked.Read(ref _ticks);

    public override DateTimeOffset GetUtcNow() => _utcNow;

    public void Advance(TimeSpan by)
    {
        Interlocked.Add(ref _ticks, by.Ticks);
        _utcNow += by;
    }

    public void AdvanceMs(double milliseconds) => Advance(TimeSpan.FromMilliseconds(milliseconds));
}

public static class TimeProviderExtensions
{
    public static double ElapsedMs(this TimeProvider time, long fromTimestamp)
        => time.GetElapsedTime(fromTimestamp).TotalMilliseconds;

    public static double ElapsedMs(this TimeProvider time, long fromTimestamp, long toTimestamp)
        => time.GetElapsedTime(fromTimestamp, toTimestamp).TotalMilliseconds;
}
