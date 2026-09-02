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
    ISidebandForwardSink? sidebandSink = null)
{
    private const int ConnectTimeoutMs = 2000;

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
            Capabilities = AgentCapabilities.All,
            ConnectionId = challenge.ConnectionId,
            Nonce = challenge.Nonce,
            Seq = guard.NextOutboundSeq(),
        };
        await writer.WriteLineAsync(PipeJson.Serialize(hello).AsMemory(), cancellationToken).ConfigureAwait(false);

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

            var response = Execute(request) with
            {
                ConnectionId = guard.ConnectionId,
                Seq = guard.NextOutboundSeq(),
            };
            await writer.WriteLineAsync(PipeJson.Serialize(response).AsMemory(), cancellationToken).ConfigureAwait(false);
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
