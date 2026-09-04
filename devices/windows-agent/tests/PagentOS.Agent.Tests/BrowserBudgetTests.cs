using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Ipc;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>
/// The companion budgets a browser request from what is LEFT of the service's wait (CI race,
/// 2026-09-04): the service's deadline travels with the request, so pipe latency and the
/// companion's own processing no longer eat the headroom that keeps the typed timeout ahead.
/// </summary>
public sealed class BrowserBudgetTests
{
    private static ExecRequest Request(int timeoutMs, long deadlineUtcMs) => new()
    {
        RequestId = "r",
        Capability = "browser.wait",
        Payload = new JsonObject(),
        TimeoutMs = timeoutMs,
        DeadlineUtcMs = deadlineUtcMs,
    };

    [Fact]
    public void Without_a_deadline_the_budget_is_the_requested_duration_minus_the_headroom()
    {
        var budget = CompanionRuntime.BrowserBudget(Request(2_000, 0), nowUnixMs: 1_000_000);
        Assert.Equal(TimeSpan.FromMilliseconds(1_500), budget);
    }

    [Fact]
    public void With_a_deadline_the_budget_is_what_is_left_minus_the_headroom()
    {
        // the request was written 700 ms ago against a 5 s wait: 4 300 ms remain
        var budget = CompanionRuntime.BrowserBudget(Request(5_000, 1_000_000 + 4_300), nowUnixMs: 1_000_000);
        Assert.Equal(TimeSpan.FromMilliseconds(3_800), budget);
        // 1 300 ms left of a 2 s wait: below the minimum after the headroom, so the 1 s floor
        var floor = CompanionRuntime.BrowserBudget(Request(2_000, 1_000_000 + 1_300), nowUnixMs: 1_000_000);
        Assert.Equal(TimeSpan.FromSeconds(1), floor);
    }

    [Fact]
    public void A_deadline_never_extends_the_requested_duration()
    {
        var budget = CompanionRuntime.BrowserBudget(Request(2_000, 1_000_000 + 10_000), nowUnixMs: 1_000_000);
        Assert.Equal(TimeSpan.FromMilliseconds(1_500), budget);
    }

    [Fact]
    public void Almost_out_of_time_still_gets_one_short_attempt_never_zero()
    {
        var budget = CompanionRuntime.BrowserBudget(Request(2_000, 1_000_000 + 300), nowUnixMs: 1_000_000);
        Assert.Equal(TimeSpan.FromMilliseconds(300), budget);
        var past = CompanionRuntime.BrowserBudget(Request(2_000, 1_000_000 - 50), nowUnixMs: 1_000_000);
        Assert.Equal(TimeSpan.FromMilliseconds(1), past);
    }
}
