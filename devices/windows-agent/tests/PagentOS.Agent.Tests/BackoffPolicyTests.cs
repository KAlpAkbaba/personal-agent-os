using PagentOS.Agent.Core.Connection;
using Xunit;

namespace PagentOS.Agent.Tests;

public class BackoffPolicyTests
{
    [Fact]
    public void Delays_stay_within_full_jitter_bounds()
    {
        var policy = new BackoffPolicy(baseSeconds: 1.0, maxSeconds: 60.0, random: new Random(1234));
        for (var attempt = 0; attempt <= 12; attempt++)
        {
            var cap = Math.Min(60.0, Math.Pow(2, attempt));
            for (var sample = 0; sample < 200; sample++)
            {
                var delay = policy.NextDelay(attempt).TotalSeconds;
                Assert.InRange(delay, 0.0, cap);
            }
        }
    }

    [Fact]
    public void Cap_reaches_sixty_seconds_and_never_exceeds_it()
    {
        var policy = new BackoffPolicy(random: new Random(42));
        for (var sample = 0; sample < 500; sample++)
        {
            Assert.InRange(policy.NextDelay(30).TotalSeconds, 0.0, 60.0);
        }
    }

    [Fact]
    public void Jitter_produces_varied_delays()
    {
        var policy = new BackoffPolicy(random: new Random(7));
        var delays = Enumerable.Range(0, 50).Select(_ => policy.NextDelay(6).TotalSeconds).ToHashSet();
        Assert.True(delays.Count > 10, "full jitter should produce varied delays");
    }

    [Fact]
    public void Custom_base_and_max_are_respected()
    {
        var policy = new BackoffPolicy(baseSeconds: 0.05, maxSeconds: 0.2, random: new Random(9));
        for (var attempt = 0; attempt <= 10; attempt++)
        {
            var cap = Math.Min(0.2, 0.05 * Math.Pow(2, attempt));
            for (var sample = 0; sample < 50; sample++)
            {
                Assert.InRange(policy.NextDelay(attempt).TotalSeconds, 0.0, cap);
            }
        }
    }

    [Fact]
    public void Negative_attempt_is_rejected()
    {
        var policy = new BackoffPolicy();
        Assert.Throws<ArgumentOutOfRangeException>(() => policy.NextDelay(-1));
    }
}
