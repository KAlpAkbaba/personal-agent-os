using System.IO.Pipes;
using System.Security.Principal;
using System.Text;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Connection;
using PagentOS.Agent.Core.Ipc;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion;

/// <summary>
/// Session Companion loop: connects to the Device Service named pipe from the interactive
/// session, announces its capabilities and serves exec requests. Reconnects with exponential
/// backoff (full jitter) whenever the service restarts or the pipe breaks.
///
/// Two things happen before this process will execute anything a pipe asks of it:
///
/// 1. <b>Who owns this pipe?</b> The companion refuses a pipe that is not owned by an
///    account trusted to host the service (<see cref="ServiceAdmissionPolicy"/>). A pipe
///    name is not a secret and the server side belongs to whoever creates it first, so
///    without this check a local process could sit on the name and drive the owner's
///    desktop. A standard user cannot create a SYSTEM-owned object, which is what makes the
///    check meaningful rather than decorative.
/// 2. <b>Is this frame from this connection, and new?</b> Every frame carries the connection
///    id issued in the service's challenge plus a strictly increasing sequence
///    (<see cref="IpcChannelGuard"/>), so a captured request cannot be re-sent later or on a
///    later connection.
///
/// The pipe is opened at <see cref="TokenImpersonationLevel.Identification"/>: the service
/// must be able to *identify* the companion (that is how it checks the SID), but must never
/// be able to impersonate it and act with the owner's rights.
/// </summary>
public sealed class CompanionRuntime(
    string pipeName,
    AppLauncher launcher,
    ArtifactOpener artifactOpener,
    ILogger logger,
    BackoffPolicy? backoff = null,
    ServiceAdmissionPolicy? servicePolicy = null,
    IPipeOwnerInspector? ownerInspector = null,
    ISidebandForwardSink? sidebandSink = null,
    BrowserWorkerHost? browserWorker = null)
{
    private const int ConnectTimeoutMs = 2000;

    /// <summary>
    /// Headroom the companion keeps under the service's own wait on a browser request, so
    /// the typed answer (timeout) arrives before the service gives up and synthesises one.
    /// </summary>
    private static readonly TimeSpan BrowserTimeoutMargin = TimeSpan.FromMilliseconds(500);
    private static readonly TimeSpan BrowserMinimumBudget = TimeSpan.FromSeconds(1);
    private static readonly TimeSpan InFlightDrainTimeout = TimeSpan.FromSeconds(3);

    /// <summary>
    /// The worker's budget for a browser request: what is LEFT of the service's wait, minus
    /// the headroom that keeps this side's typed timeout ahead of the service's own.
    /// </summary>
    /// <remarks>
    /// With a deadline on the request (<see cref="ExecRequest.DeadlineUtcMs"/>), the budget
    /// is measured from the service's clock, so pipe latency and this side's processing are
    /// not charged against the headroom - on a loaded CI runner they once ate the whole
    /// 500 ms and the service synthesised an untyped timeout first (2026-09-04). Without a
    /// deadline (an older service) the budget is the requested duration, as before. The
    /// minimum-budget rule is unchanged: a request that is already almost out of time still
    /// gets one short attempt rather than a zero.
    /// </remarks>
    public static TimeSpan BrowserBudget(ExecRequest request, long nowUnixMs)
    {
        var requested = TimeSpan.FromMilliseconds(Math.Max(1, request.TimeoutMs));
        if (request.DeadlineUtcMs > 0)
        {
            var remaining = TimeSpan.FromMilliseconds(Math.Max(1, request.DeadlineUtcMs - nowUnixMs));
            if (remaining < requested)
            {
                requested = remaining;
            }
        }
        var budget = requested - BrowserTimeoutMargin;
        if (budget < BrowserMinimumBudget)
        {
            budget = requested < BrowserMinimumBudget ? requested : BrowserMinimumBudget;
        }
        return budget;
    }

    private readonly BackoffPolicy _backoff = backoff ?? new BackoffPolicy(baseSeconds: 1.0, maxSeconds: 30.0);
    private readonly ServiceAdmissionPolicy _servicePolicy = servicePolicy ?? DefaultServicePolicy();
    private readonly IPipeOwnerInspector _ownerInspector = ownerInspector ?? new WindowsPipeOwnerInspector();

    /// <summary>Refusals since start, by reason — asserted by tests, surfaced in logs.</summary>
    public IReadOnlyDictionary<IpcRefusal, int> Refusals => _refusals;

    /// <summary>The trust posture actually in force. Logged at startup; never inferred by a reader.</summary>
    public ServiceAdmissionPolicy ServicePolicy => _servicePolicy;

    /// <summary>voice_sideband forwards accepted (fresh on this connection) and handed to the sink.</summary>
    public int SidebandAccepted => _sidebandAccepted;

    private int _sidebandAccepted;

    private readonly Dictionary<IpcRefusal, int> _refusals = new();

    /// <summary>
    /// The capabilities this companion announces in its hello: the desktop family always,
    /// the browser family only when a worker is configured (M13). Advertising a name is a
    /// promise to answer it with something other than a hang.
    /// </summary>
    public IReadOnlyList<string> AdvertisedCapabilities
        => AgentCapabilities.Compose(browserWorker?.IsConfigured == true);

    /// <summary>
    /// The default is the PRODUCTION posture: only a pipe owned by an account that can host
    /// a Windows Service is trusted.
    ///
    /// This default used to be developer mode, which also trusts a pipe owned by the current
    /// user — and since UAC splits integrity level rather than identity, any ordinary process
    /// running as the owner shares that SID. A companion defaulting to developer mode would
    /// therefore accept exec requests from any process in the owner's own session, which is
    /// exactly the attack this check exists to stop. Developer mode is now something a
    /// developer run asks for out loud (<c>--dev-trust</c>), never something production
    /// inherits by forgetting to pass an argument.
    /// </summary>
    private static ServiceAdmissionPolicy DefaultServicePolicy() => ServiceAdmissionPolicy.ServiceMode();

    public async Task RunAsync(CancellationToken cancellationToken)
    {
        var attempt = 0;
        while (!cancellationToken.IsCancellationRequested)
        {
            try
            {
                await ServeOneConnectionAsync(() => attempt = 0, cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                break;
            }
            catch (TimeoutException)
            {
                // Service not up yet; retry quietly.
            }
            catch (Exception ex)
            {
                logger.LogWarning("companion pipe connection ended: {Reason}", ex.Message);
            }

            if (cancellationToken.IsCancellationRequested)
            {
                break;
            }

            var delay = _backoff.NextDelay(attempt);
            attempt = Math.Min(attempt + 1, 20);
            try
            {
                await Task.Delay(delay, cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }
    }

    private void RecordRefusal(IpcRefusal refusal, string detail)
    {
        lock (_refusals)
        {
            _refusals[refusal] = _refusals.TryGetValue(refusal, out var count) ? count + 1 : 1;
        }

        logger.LogWarning(
            "refusing this pipe ({Refusal}): {Reason}; {Detail}",
            refusal,
            IpcRefusalText.Describe(refusal),
            detail);
    }

    private async Task ServeOneConnectionAsync(Action onConnected, CancellationToken cancellationToken)
    {
        await using var client = new NamedPipeClientStream(
            ".",
            pipeName,
            PipeDirection.InOut,
            PipeOptions.Asynchronous,
            TokenImpersonationLevel.Identification);
        await client.ConnectAsync(ConnectTimeoutMs, cancellationToken).ConfigureAwait(false);

        // Before a single frame is read: is this the service's pipe, or someone else's?
        var ownerSid = _ownerInspector.OwnerSid(client);
        var ownerVerdict = _servicePolicy.Evaluate(ownerSid);
        if (ownerVerdict != IpcRefusal.None)
        {
            RecordRefusal(ownerVerdict, $"pipe_owner={ownerSid ?? "unknown"} pipe={pipeName}");
            return;
        }

        onConnected();
        logger.LogInformation(
            "connected to device service pipe \\\\.\\pipe\\{PipeName} (owner={Owner})",
            pipeName,
            ownerSid);

        using var reader = new StreamReader(client, new UTF8Encoding(false), detectEncodingFromByteOrderMarks: false, leaveOpen: true);
        await using var writer = new StreamWriter(client, new UTF8Encoding(false), leaveOpen: true) { AutoFlush = true };

        // The service speaks first: its challenge carries the connection id everything else
        // is bound to.
        var challengeLine = await reader.ReadLineAsync(cancellationToken).ConfigureAwait(false);
        if (challengeLine is null)
        {
            return;
        }

        ServiceChallenge challenge;
        try
        {
            if (PipeJson.Deserialize(challengeLine) is not ServiceChallenge parsed)
            {
                RecordRefusal(IpcRefusal.HandshakeFailed, "first frame was not a service_challenge");
                return;
            }

            challenge = parsed;
        }
        catch (Exception ex)
        {
            RecordRefusal(IpcRefusal.HandshakeFailed, $"malformed challenge: {ex.Message}");
            return;
        }

        if (challenge.ProtocolVersion != IpcProtocol.Version)
        {
            RecordRefusal(
                IpcRefusal.HandshakeFailed,
                $"protocol version {challenge.ProtocolVersion} (expected {IpcProtocol.Version})");
            return;
        }

        var guard = new IpcChannelGuard(challenge.ConnectionId);
        var hello = new CompanionHello
        {
            Capabilities = AdvertisedCapabilities,
            ConnectionId = challenge.ConnectionId,
            Nonce = challenge.Nonce,
            Seq = guard.NextOutboundSeq(),
        };
        await writer.WriteLineAsync(PipeJson.Serialize(hello).AsMemory(), cancellationToken).ConfigureAwait(false);

        // One writer, one sequence. Browser responses are produced concurrently, and the
        // service refuses a frame whose sequence is not strictly greater than the last one
        // it saw — so the sequence number is taken under the same lock as the write, never
        // before it. The desktop path goes through the same helper so its framing is what
        // it always was: one ExecResponse per ExecRequest, same conn_id, increasing seq.
        using var connectionCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        using var writeLock = new SemaphoreSlim(1, 1);
        var inFlight = new System.Collections.Concurrent.ConcurrentDictionary<string, Task>(StringComparer.Ordinal);

        async Task SendResponseAsync(ExecResponse response)
        {
            await writeLock.WaitAsync(connectionCts.Token).ConfigureAwait(false);
            try
            {
                var framed = response with
                {
                    ConnectionId = guard.ConnectionId,
                    Seq = guard.NextOutboundSeq(),
                };
                await writer.WriteLineAsync(PipeJson.Serialize(framed).AsMemory(), connectionCts.Token).ConfigureAwait(false);
            }
            finally
            {
                writeLock.Release();
            }
        }

        try
        {
            await ServeRequestsAsync(reader, guard, SendResponseAsync, inFlight, connectionCts.Token).ConfigureAwait(false);
        }
        finally
        {
            // The pipe is gone (or we are stopping): every browser request still running is
            // cancelled — the host forwards the cancel to the worker — and given a moment to
            // settle before the writer they would answer on is disposed.
            connectionCts.Cancel();
            var pending = inFlight.Values.ToArray();
            if (pending.Length > 0)
            {
                try
                {
                    await Task.WhenAll(pending).WaitAsync(InFlightDrainTimeout).ConfigureAwait(false);
                }
                catch (Exception)
                {
                    // Teardown only; each task logs its own outcome.
                }
            }
        }
    }

    private async Task ServeRequestsAsync(
        StreamReader reader,
        IpcChannelGuard guard,
        Func<ExecResponse, Task> sendResponse,
        System.Collections.Concurrent.ConcurrentDictionary<string, Task> inFlight,
        CancellationToken cancellationToken)
    {
        while (true)
        {
            var line = await reader.ReadLineAsync(cancellationToken).ConfigureAwait(false);
            if (line is null)
            {
                logger.LogInformation("device service closed the pipe");
                return;
            }

            PipeMessage message;
            try
            {
                message = PipeJson.Deserialize(line);
            }
            catch (Exception ex)
            {
                logger.LogWarning("malformed pipe frame from service: {Reason}", ex.Message);
                continue;
            }

            if (message is SidebandForward forward)
            {
                // M12 (ADR-0039): one-way, opaque, and bound to this connection like every
                // other frame. Nothing is executed and nothing is answered; a stale or
                // replayed forward is refused exactly as a replayed exec_request would be.
                var forwardVerdict = guard.Accept(forward.ConnectionId, forward.Seq);
                if (forwardVerdict != IpcRefusal.None)
                {
                    RecordRefusal(forwardVerdict, "frame=voice_sideband");
                    continue;
                }

                Interlocked.Increment(ref _sidebandAccepted);
                sidebandSink?.Accept(forward.Frame);
                continue;
            }

            if (message is not ExecRequest request)
            {
                continue;
            }

            var verdict = guard.Accept(request.ConnectionId, request.Seq);
            if (verdict != IpcRefusal.None)
            {
                // A replayed exec_request would run the command a second time; refusing it
                // here is the difference between "at most once per request" and "as often as
                // anyone can echo the bytes back".
                RecordRefusal(verdict, $"request_id={request.RequestId} capability={request.Capability}");
                continue;
            }

            if (AgentCapabilities.IsBrowser(request.Capability))
            {
                // M13: browser requests are long (a navigation, an extraction) and may run
                // concurrently; the read loop must not stall on them. Each one answers on
                // its own task through the shared, sequenced writer; request_id correlates.
                var task = Task.Run(async () =>
                {
                    var browserResponse = await ExecuteBrowserAsync(request, cancellationToken).ConfigureAwait(false);
                    try
                    {
                        await sendResponse(browserResponse).ConfigureAwait(false);
                    }
                    catch (Exception ex)
                    {
                        logger.LogWarning(
                            "browser response for {RequestId} could not be written (pipe gone): {Reason}",
                            request.RequestId,
                            ex.Message);
                    }
                    finally
                    {
                        inFlight.TryRemove(request.RequestId, out _);
                    }
                }, CancellationToken.None);
                inFlight[request.RequestId] = task;
                continue;
            }

            await sendResponse(Execute(request)).ConfigureAwait(false);
        }
    }

    /// <summary>
    /// The browser family: forwarded to the worker host, which owns the worker process and
    /// the contract's companion-side checks. Not configured → <c>capability_missing</c>,
    /// exactly like an unknown desktop capability, because to Cloud Core they are the same
    /// fact: this device cannot do that.
    /// </summary>
    private async Task<ExecResponse> ExecuteBrowserAsync(ExecRequest request, CancellationToken cancellationToken)
    {
        try
        {
            if (browserWorker is null || !browserWorker.IsConfigured)
            {
                throw new CapabilityException(
                    ErrorClasses.CapabilityMissing,
                    $"capability '{request.Capability}' is not available: no browser worker is configured on this companion",
                    retryable: false);
            }

            if (string.Equals(request.Capability, BrowserCapabilities.Family, StringComparison.Ordinal))
            {
                throw new CapabilityException(
                    ErrorClasses.CapabilityMissing,
                    $"'{BrowserCapabilities.Family}' is the family marker, not an operation",
                    retryable: false);
            }

            if (!BrowserCapabilities.IsOperation(request.Capability))
            {
                // Only names the contract defines are ever written to the worker's stdin.
                // The host repeats this check; refusing here keeps the companion's own
                // answer independent of how the host is wired.
                throw new CapabilityException(
                    ErrorClasses.CapabilityMissing,
                    $"capability '{request.Capability}' is not a browser operation this companion knows (BROWSER_CAPABILITIES.md §1)",
                    retryable: false);
            }

            var budget = BrowserBudget(request, DateTimeOffset.UtcNow.ToUnixTimeMilliseconds());

            var result = await browserWorker.ExecuteAsync(request.Capability, request.Payload, budget, cancellationToken).ConfigureAwait(false);
            logger.LogInformation("executed {Capability}: ok", request.Capability);
            return new ExecResponse { RequestId = request.RequestId, Ok = true, Result = result };
        }
        catch (CapabilityException ex)
        {
            logger.LogWarning("capability {Capability} failed: {Class}: {Reason}", request.Capability, ex.ErrorClass, ex.Message);
            return new ExecResponse
            {
                RequestId = request.RequestId,
                Ok = false,
                Error = ErrorObjects.Create(ex.ErrorClass, ex.Message, ex.Retryable),
            };
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            return new ExecResponse
            {
                RequestId = request.RequestId,
                Ok = false,
                Error = ErrorObjects.Create(ErrorClasses.Cancelled, "companion connection closed while the browser request was running", retryable: true),
            };
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "capability {Capability} crashed", request.Capability);
            return new ExecResponse
            {
                RequestId = request.RequestId,
                Ok = false,
                Error = ErrorObjects.Create(ErrorClasses.InternalBug, ex.Message, retryable: false),
            };
        }
    }

    private ExecResponse Execute(ExecRequest request)
    {
        try
        {
            JsonObject result;
            switch (request.Capability)
            {
                case AgentCapabilities.DesktopOpenApplication:
                    result = launcher.Launch(request.Payload);
                    logger.LogInformation(
                        "executed {Capability}: pid={Pid}",
                        request.Capability,
                        result["pid"]?.GetValue<int>());
                    break;

                case AgentCapabilities.DesktopOpenArtifact:
                    result = artifactOpener.Open(request.Payload);
                    logger.LogInformation(
                        "executed {Capability}: opened={Opened} path={Path}",
                        request.Capability,
                        result["opened"]?.GetValue<bool>(),
                        result["path"]?.GetValue<string>());
                    break;

                default:
                    throw new CapabilityException(
                        ErrorClasses.CapabilityMissing,
                        $"capability '{request.Capability}' is not supported by the session companion",
                        retryable: false);
            }

            return new ExecResponse { RequestId = request.RequestId, Ok = true, Result = result };
        }
        catch (CapabilityException ex)
        {
            logger.LogWarning("capability {Capability} failed: {Class}: {Reason}", request.Capability, ex.ErrorClass, ex.Message);
            return new ExecResponse
            {
                RequestId = request.RequestId,
                Ok = false,
                Error = ErrorObjects.Create(ex.ErrorClass, ex.Message, ex.Retryable),
            };
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "capability {Capability} crashed", request.Capability);
            return new ExecResponse
            {
                RequestId = request.RequestId,
                Ok = false,
                Error = ErrorObjects.Create(ErrorClasses.InternalBug, ex.Message, retryable: false),
            };
        }
    }
}
