using System.Diagnostics;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// A long operator action answers <c>cancelled</c> when its token is cancelled and
/// <c>timeout</c> when its budget or its own timeout elapses — typed, prompt, and with the
/// process it started ended (M19_DIGITAL_OPERATOR_SPEC.md §5). <c>Start-Sleep</c> is in the
/// TEST allowlist only. No desktop needed.
/// </summary>
public sealed class CancellationAndTimeoutTests : IDisposable
{
    private readonly OperatorLab _lab = new();

    public void Dispose() => _lab.Dispose();

    [Fact]
    public async Task A_running_terminal_command_cancelled_through_the_token_answers_cancelled_promptly()
    {
        using var cts = new CancellationTokenSource();
        var stopwatch = Stopwatch.StartNew();
        var task = _lab.ExecAsync(OperatorCapabilityNames.TerminalExecute, new JsonObject { ["command"] = "Start-Sleep 20" }, TimeSpan.FromSeconds(30), cts.Token);
        await Task.Delay(700);
        Assert.False(task.IsCompleted, "the sleep should still be running");
        cts.Cancel();

        var ex = await Assert.ThrowsAsync<CapabilityException>(() => task);
        stopwatch.Stop();
        Assert.Equal(ErrorClasses.Cancelled, ex.ErrorClass);
        Assert.True(ex.Retryable);
        // The bound includes PowerShell's own start (7-8 s cold on the GitHub runner, where
        // an 8 s bound once failed at 8.53 s); the property is "well under the 20 s sleep
        // and the 30 s cap", so the bound is 15 s.
        Assert.True(stopwatch.Elapsed < TimeSpan.FromSeconds(15), $"cancel took {stopwatch.Elapsed}");
    }

    [Fact]
    public async Task A_one_second_terminal_timeout_answers_timeout()
    {
        var stopwatch = Stopwatch.StartNew();
        var ex = await Assert.ThrowsAsync<CapabilityException>(() => _lab.ExecAsync(
            OperatorCapabilityNames.TerminalExecute,
            new JsonObject { ["command"] = "Start-Sleep 20", ["timeout_s"] = 1 },
            TimeSpan.FromSeconds(30),
            CancellationToken.None));
        stopwatch.Stop();

        Assert.Equal(ErrorClasses.Timeout, ex.ErrorClass);
        Assert.True(ex.Retryable);
        Assert.True(stopwatch.Elapsed < TimeSpan.FromSeconds(8), $"timeout took {stopwatch.Elapsed}");
    }

    [Fact]
    public async Task The_pipe_budget_bounds_an_action_that_would_outlive_it()
    {
        // The service's cap reaches the companion as a budget; an action that ignores its own
        // timeout still ends with the budget, and the answer is the typed timeout.
        var stopwatch = Stopwatch.StartNew();
        var ex = await Assert.ThrowsAsync<CapabilityException>(() => _lab.ExecAsync(
            OperatorCapabilityNames.TerminalExecute,
            new JsonObject { ["command"] = "Start-Sleep 20", ["timeout_s"] = 30 },
            TimeSpan.FromSeconds(1),
            CancellationToken.None));
        stopwatch.Stop();

        Assert.Equal(ErrorClasses.Timeout, ex.ErrorClass);
        Assert.True(stopwatch.Elapsed < TimeSpan.FromSeconds(8), $"budget took {stopwatch.Elapsed}");
    }

    [Fact]
    public async Task A_timeout_above_the_family_cap_is_a_validation_error()
    {
        var ex = await Assert.ThrowsAsync<CapabilityException>(() => _lab.ExecAsync(
            OperatorCapabilityNames.TerminalExecute,
            new JsonObject { ["command"] = "hostname", ["timeout_s"] = 31 },
            TimeSpan.FromSeconds(30),
            CancellationToken.None));
        Assert.Equal(ErrorClasses.ValidationError, ex.ErrorClass);
    }
}
