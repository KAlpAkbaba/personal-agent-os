using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Media;
using PagentOS.Companion.Audio.Sideband;
using PagentOS.Companion.Audio.Timing;

namespace PagentOS.Companion.Audio.Session;

public sealed record BargeInOutcome(
    bool StopWord,
    double PlaybackStoppedMs,
    PlaybackStopReport Stop,
    double CancelMs,
    double TotalMs);

/// <summary>
/// The barge-in policy from spec §5, in code and in this order, always:
/// 1. stop local playback (the only step the owner can hear; measured as <c>playback_stopped_ms</c>),
/// 2. cancel the provider's in-flight response,
/// 3. report <c>barge_in</c> with the measured number, then <c>playback_stopped</c>.
/// The state machine enforces 1-before-3 by refusing to latch without a recorded cut; the
/// tests enforce 1-before-2-before-3 by tracing all three fakes into one list.
/// </summary>
public sealed class BargeInController(
    Func<IAudioPlayback> playback,
    Func<IMediaLeg> leg,
    IVoiceEventReporter reporter,
    VoiceClientStateMachine fsm,
    TimeProvider time,
    ILogger? logger = null)
{
    public async Task<BargeInOutcome> ExecuteAsync(bool stopWord, string detail, CancellationToken cancellationToken)
    {
        var t0 = time.GetTimestamp();

        // 1. Silence first. Nothing may be awaited before this line.
        var stop = playback().StopImmediately();
        var playbackStoppedMs = time.ElapsedMs(t0);
        fsm.AssistantSpeechCut(stopWord ? "stop_word" : "overlap");

        // 2. Then tell the provider to stop generating.
        var t1 = time.GetTimestamp();
        try
        {
            await leg().CancelResponseAsync(cancellationToken).ConfigureAwait(false);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            // The owner has already been heard; a failed cancel is a provider problem to log,
            // not a reason to un-stop playback or skip the report.
            logger?.LogWarning("provider cancel failed after barge-in: {Reason}", ex.Message);
        }

        var cancelMs = time.ElapsedMs(t1);
        fsm.BargeInLatched(stopWord, detail);

        // 3. Report, with the number the owner actually experienced.
        var totalMs = time.ElapsedMs(t0);
        await reporter.ReportAsync(VoiceClientEvents.BargeIn, new JsonObject
        {
            ["playback_stopped_ms"] = Math.Round(playbackStoppedMs, 3),
            ["discarded_ms"] = stop.DiscardedMs,
            ["residual_latency_ms"] = stop.ResidualLatencyMs,
            ["cancel_ms"] = Math.Round(cancelMs, 3),
            ["stop_word"] = stopWord,
        }, cancellationToken).ConfigureAwait(false);
        await reporter.ReportAsync(VoiceClientEvents.PlaybackStopped, new JsonObject
        {
            ["reason"] = stopWord ? "stop_word" : "barge_in",
            ["residual_latency_ms"] = stop.ResidualLatencyMs,
        }, cancellationToken).ConfigureAwait(false);

        return new BargeInOutcome(stopWord, playbackStoppedMs, stop, cancelMs, totalMs);
    }
}
