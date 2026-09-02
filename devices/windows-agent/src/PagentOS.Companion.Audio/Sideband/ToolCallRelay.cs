using System.Collections.Concurrent;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Companion.Audio.Media;
using PagentOS.Companion.Audio.Session;
using PagentOS.Companion.Audio.Timing;

namespace PagentOS.Companion.Audio.Sideband;

/// <summary>
/// Provider tool call → Cloud Core → provider (spec §4.3, §6). Idempotent on <c>call_id</c>
/// at two levels: a duplicate provider event for a call already in flight joins that call
/// rather than posting again, and a transient failure is retried with the same
/// <c>call_id</c> so Cloud Core's own idempotency absorbs it. A long-running tool's
/// provisional result (status running + Turkish preamble) is submitted at once so the
/// provider can speak it; the final result lands through <see cref="CompleteAsync"/> from
/// the sideband push.
/// </summary>
public sealed class ToolCallRelay(
    ISidebandClient sideband,
    Func<IMediaLeg> leg,
    IVoiceEventReporter reporter,
    VoiceClientStateMachine fsm,
    TimeProvider time,
    string sessionId,
    ILogger? logger = null,
    IReadOnlyList<TimeSpan>? retryDelays = null)
{
    private static readonly TimeSpan[] DefaultRetryDelays =
    {
        TimeSpan.FromMilliseconds(100), TimeSpan.FromMilliseconds(300), TimeSpan.FromMilliseconds(900),
    };

    private readonly ConcurrentDictionary<string, Task<ToolCallRelayResult>> _inFlight = new(StringComparer.Ordinal);
    private readonly ConcurrentDictionary<string, byte> _running = new(StringComparer.Ordinal);
    private readonly IReadOnlyList<TimeSpan> _retryDelays = retryDelays ?? DefaultRetryDelays;

    public IReadOnlyCollection<string> RunningCalls => _running.Keys.ToList();

    public int RelayedCount => _inFlight.Count;

    public Task<ToolCallRelayResult> HandleAsync(ToolCallEvent call, CancellationToken cancellationToken)
        => _inFlight.GetOrAdd(call.CallId, _ => RunAsync(call, cancellationToken));

    /// <summary>Final result of a long-running call arriving from Cloud Core's push. False if the call is unknown or already final.</summary>
    public async Task<bool> CompleteAsync(string callId, JsonNode? result, JsonObject? error, CancellationToken cancellationToken)
    {
        if (!_running.TryRemove(callId, out _))
        {
            logger?.LogWarning("tool_completed for unknown or already-final call {CallId}; ignored", callId);
            return false;
        }

        var final = new ToolCallRelayResult(callId, result, error, Status: "completed", Preamble: null);
        var t0 = time.GetTimestamp();
        await leg().SubmitToolResultAsync(callId, final.OutputJson(), final: true, followUp: true, cancellationToken).ConfigureAwait(false);
        await reporter.ReportAsync(VoiceClientEvents.ToolResultSubmitted, new JsonObject
        {
            ["call_id"] = callId,
            ["final"] = true,
            ["follow_up"] = true,
            ["submit_ms"] = Math.Round(time.ElapsedMs(t0), 2),
        }, cancellationToken).ConfigureAwait(false);
        fsm.FinishToolCall(callId);
        return true;
    }

    private async Task<ToolCallRelayResult> RunAsync(ToolCallEvent call, CancellationToken cancellationToken)
    {
        fsm.StartToolCall(call.Name);
        var t0 = time.GetTimestamp();
        var result = await RelayWithRetryAsync(call, cancellationToken).ConfigureAwait(false);
        var relayMs = time.ElapsedMs(t0);

        await reporter.ReportAsync(VoiceClientEvents.ToolCallRelayed, new JsonObject
        {
            ["call_id"] = call.CallId,
            ["name"] = call.Name,
            ["relay_ms"] = Math.Round(relayMs, 2),
            ["status"] = result.IsRunning ? "running" : result.Error is null ? "ok" : "error",
        }, cancellationToken).ConfigureAwait(false);

        if (result.IsRunning)
        {
            _running[call.CallId] = 1;
        }

        var t1 = time.GetTimestamp();
        await leg().SubmitToolResultAsync(call.CallId, result.OutputJson(), final: !result.IsRunning, followUp: false, cancellationToken).ConfigureAwait(false);
        await reporter.ReportAsync(VoiceClientEvents.ToolResultSubmitted, new JsonObject
        {
            ["call_id"] = call.CallId,
            ["final"] = !result.IsRunning,
            ["follow_up"] = false,
            ["preamble"] = result.IsRunning && !string.IsNullOrEmpty(result.Preamble),
            ["submit_ms"] = Math.Round(time.ElapsedMs(t1), 2),
        }, cancellationToken).ConfigureAwait(false);

        if (!result.IsRunning)
        {
            fsm.FinishToolCall(call.CallId);
        }

        return result;
    }

    private async Task<ToolCallRelayResult> RelayWithRetryAsync(ToolCallEvent call, CancellationToken cancellationToken)
    {
        var attempt = 0;
        while (true)
        {
            try
            {
                return await sideband.RelayToolCallAsync(sessionId, call.CallId, call.Name, call.ArgumentsJson, cancellationToken).ConfigureAwait(false);
            }
            catch (Exception ex) when (IsTransient(ex) && attempt < _retryDelays.Count && !cancellationToken.IsCancellationRequested)
            {
                logger?.LogWarning("tool call {CallId} relay attempt {Attempt} failed ({Reason}); retrying with the same call_id", call.CallId, attempt + 1, ex.Message);
                await Task.Delay(_retryDelays[attempt], time, cancellationToken).ConfigureAwait(false);
                attempt++;
            }
        }
    }

    private static bool IsTransient(Exception ex) => ex switch
    {
        SidebandException sideband => sideband.Transient,
        HttpRequestException => true,
        IOException => true,
        TaskCanceledException => true,
        _ => false,
    };
}
