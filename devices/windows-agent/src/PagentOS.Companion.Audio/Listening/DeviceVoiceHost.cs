using Microsoft.Extensions.Logging;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Audio.Processing;
using PagentOS.Companion.Audio.Orchestration;
using PagentOS.Companion.Audio.Sideband;

namespace PagentOS.Companion.Audio.Listening;

public sealed record DeviceVoiceHostOptions
{
    public TimeSpan MinRestartDelay { get; init; } = TimeSpan.FromSeconds(2);

    public TimeSpan MaxRestartDelay { get; init; } = TimeSpan.FromSeconds(30);

    /// <summary>How often a missing owner token is looked for again (row 251: it may be written later).</summary>
    public TimeSpan TokenRetry { get; init; } = TimeSpan.FromSeconds(30);

    /// <summary>How often the session's connectivity is copied into the listener and the health.</summary>
    public TimeSpan SupervisePeriod { get; init; } = TimeSpan.FromMilliseconds(500);
}

/// <summary>
/// Rows 240 and 250-252: the device voice service. The listener runs for the whole life of the
/// companion - browser or no browser, Cloud Core or no Cloud Core - and a realtime session is
/// laid over it whenever one can be had. A session that ends (the Cloud Core closed it, the
/// network went, the credential expired) is replaced after a backoff; a missing owner token is
/// looked for again; every failure is counted and its CLASS reported, so "the device stopped
/// listening" is a state the Cloud Core can see rather than a silence it has to notice.
/// </summary>
public sealed class DeviceVoiceHost(
    DeviceListeningService listening,
    DeviceVoiceHealth health,
    Func<IAudioDeviceFactory, VoiceSessionOrchestrator> sessionFactory,
    IOwnerSessionTokenSource tokens,
    IAudioDeviceFactory playbackDevices,
    TimeProvider time,
    ILogger logger,
    DeviceVoiceHostOptions? options = null)
{
    private readonly DeviceVoiceHostOptions _options = options ?? new DeviceVoiceHostOptions();

    /// <summary>Sessions opened since start (observable by tests).</summary>
    public int SessionsOpened { get; private set; }

    public async Task RunAsync(CancellationToken cancellationToken)
    {
        health.SetState(DeviceVoiceContract.StateStarting);
        await listening.StartAsync(cancellationToken).ConfigureAwait(false);
        var gated = new GatedCaptureFactory(listening, playbackDevices);
        var failures = 0;
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                if (tokens.GetToken() is null)
                {
                    health.SetState(DeviceVoiceContract.StateOffline, "no_owner_token");
                    logger.LogWarning("voice: no owner session token; listening continues on the device only, and the token is looked for again in {Seconds:F0} s", _options.TokenRetry.TotalSeconds);
                    await Task.Delay(_options.TokenRetry, time, cancellationToken).ConfigureAwait(false);
                    continue;
                }

                string reason;
                var failed = false;
                VoiceSessionOrchestrator? session = null;
                try
                {
                    session = sessionFactory(gated);
                    await session.StartAsync(cancellationToken).ConfigureAwait(false);
                    SessionsOpened++;
                    failures = 0;
                    health.SetState(DeviceVoiceContract.StateRunning);
                    var active = session;
                    listening.PlaybackContext = () => new AudioProcessingContext(
                        active.Playback?.IsPlaying ?? false,
                        active.ProcessingReport?.EchoCancellation != AudioProcessingReport.HeadsetNoEchoPath);
                    await SuperviseAsync(session, cancellationToken).ConfigureAwait(false);
                    reason = session.StopReason ?? "loop_ended";
                }
                catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
                {
                    return;
                }
                catch (Exception ex)
                {
                    failed = true;
                    failures++;
                    reason = ex.GetType().Name;
                    health.CountRestart("session:" + reason);
                    logger.LogError(ex, "voice: session failed: {Reason}", ex.Message);
                }
                finally
                {
                    listening.SetCloudConnected(false);
                    health.SetCloudConnected(false);
                    listening.PlaybackContext = null!;
                    if (session is not null)
                    {
                        await session.DisposeAsync().ConfigureAwait(false);
                    }
                }

                if (cancellationToken.IsCancellationRequested)
                {
                    return;
                }

                health.SetState(failed ? DeviceVoiceContract.StateBackoff : DeviceVoiceContract.StateOffline);
                var seconds = Math.Min(
                    _options.MaxRestartDelay.TotalSeconds,
                    _options.MinRestartDelay.TotalSeconds * Math.Pow(2, Math.Min(failures, 4)));
                logger.LogInformation("voice: session ended ({Reason}); listening continues on the device, a new session in {Delay:F0} s", reason, seconds);
                await Task.Delay(TimeSpan.FromSeconds(seconds), time, cancellationToken).ConfigureAwait(false);
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        finally
        {
            await listening.DisposeAsync().ConfigureAwait(false);
            health.SetCloudConnected(false);
            health.SetState(DeviceVoiceContract.StateStopped);
        }
    }

    private async Task SuperviseAsync(VoiceSessionOrchestrator session, CancellationToken cancellationToken)
    {
        var completion = session.Completion ?? Task.CompletedTask;
        bool? last = null;
        while (!completion.IsCompleted)
        {
            var connected = !session.Reconnecting;
            if (connected != last)
            {
                last = connected;
                listening.SetCloudConnected(connected);
                health.SetCloudConnected(connected);
                health.SetState(connected ? DeviceVoiceContract.StateRunning : DeviceVoiceContract.StateOffline);
            }

            await Task.WhenAny(completion, Task.Delay(_options.SupervisePeriod, time, cancellationToken)).ConfigureAwait(false);
            cancellationToken.ThrowIfCancellationRequested();
        }

        try
        {
            await completion.ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            // The session's own stop.
        }
    }
}
