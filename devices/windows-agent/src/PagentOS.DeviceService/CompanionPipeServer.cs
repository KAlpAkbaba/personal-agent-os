using System.Collections.Concurrent;
using System.IO.Pipes;
using System.Runtime.Versioning;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Text;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Ipc;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.DeviceService;

/// <summary>
/// Named-pipe server owned by the Device Service, which in production runs as LocalSystem in
/// Session 0. The Session Companion connects from the owner's interactive session and
/// executes desktop capabilities there.
///
/// The identity model has four layers, and the M1 review's finding was that only the first
/// existed:
///
/// 1. <b>DACL</b> — who may open the pipe: SYSTEM and the authorized owner SID, nobody else.
///    Under a Session-0 service the owner is a *different* account than the creator, which is
///    exactly why "ACL it to the current user" stopped being right.
/// 2. <b>First instance</b> — the pipe is created with FILE_FLAG_FIRST_PIPE_INSTANCE, so if
///    anything already holds that name, creation fails loudly instead of the service quietly
///    becoming the second instance behind a squatter.
/// 3. <b>Peer admission</b> — the connected process's account, Windows session and image path
///    are read from the kernel and judged by <see cref="CompanionAdmissionPolicy"/>. An ACL
///    says who may knock; this says who did.
/// 4. <b>Channel freshness</b> — a per-connection id and a strictly increasing sequence
///    (<see cref="IpcChannelGuard"/>), so a frame from a retired connection or a repeated
///    frame is refused rather than acted on.
///
/// A refused peer is told nothing: the connection closes. The reason goes to the log and the
/// audit trail, where the owner can see it and an attacker cannot.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class CompanionPipeServer : BackgroundService
{
    private static readonly TimeSpan HandshakeTimeout = TimeSpan.FromSeconds(10);

    private readonly string _pipeName;
    private readonly CompanionAdmissionPolicy _policy;
    private readonly IPipePeerInspector _inspector;
    private readonly ILogger<CompanionPipeServer> _logger;
    private readonly AuditLog? _audit;
    private readonly ConcurrentDictionary<string, TaskCompletionSource<ExecResponse>> _pending = new(StringComparer.Ordinal);
    private volatile CompanionConnection? _connection;

    public CompanionPipeServer(
        string pipeName,
        CompanionAdmissionPolicy policy,
        IPipePeerInspector inspector,
        ILogger<CompanionPipeServer> logger,
        AuditLog? audit = null)
    {
        _pipeName = pipeName;
        _policy = policy;
        _inspector = inspector;
        _logger = logger;
        _audit = audit;
    }

    public bool CompanionConnected => _connection is not null;

    /// <summary>Refusals since start, by reason. Surfaced in telemetry; also what the tests assert.</summary>
    public ConcurrentDictionary<IpcRefusal, int> Refusals { get; } = new();

    /// <summary>Identity of the currently admitted companion, or null when none is connected.</summary>
    public PipePeer? ConnectedPeer => _connection?.Peer;

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _logger.LogInformation(
            "companion pipe server listening on \\\\.\\pipe\\{PipeName} (authorized sid={Sid}, pinned binary={Pinned})",
            _pipeName,
            _policy.AuthorizedSid,
            _policy.PinsImagePath);

        while (!stoppingToken.IsCancellationRequested)
        {
            NamedPipeServerStream server;
            try
            {
                server = CreateServerStream();
            }
            catch (Exception ex)
            {
                // Includes "the name is already taken" — which, with FirstPipeInstance, is
                // how a squatted pipe name shows up. Never fall back to a shared instance.
                _logger.LogError(ex, "failed to create companion pipe; retrying in 5 s");
                try
                {
                    await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    return;
                }

                continue;
            }

            try
            {
                await server.WaitForConnectionAsync(stoppingToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                await server.DisposeAsync().ConfigureAwait(false);
                return;
            }
            catch (Exception ex)
            {
                _logger.LogWarning("pipe wait failed: {Reason}", ex.Message);
                await server.DisposeAsync().ConfigureAwait(false);
                continue;
            }

            var peer = SafeInspect(server);
            var verdict = _policy.Evaluate(peer);
            if (verdict != IpcRefusal.None)
            {
                RecordRefusal(verdict, peer);
                await DisconnectQuietlyAsync(server).ConfigureAwait(false);
                continue;
            }

            var guard = IpcChannelGuard.Create();
            var connection = new CompanionConnection(server, guard, peer!);
            try
            {
                if (!await HandshakeAsync(connection, stoppingToken).ConfigureAwait(false))
                {
                    RecordRefusal(IpcRefusal.HandshakeFailed, peer);
                    await connection.DisposeAsync().ConfigureAwait(false);
                    continue;
                }

                _connection = connection;
                _audit?.Write(
                    "ipc_companion_admitted",
                    status: "ok",
                    detail: $"sid={peer!.Sid} session={peer.SessionId} pid={peer.ProcessId} conn={guard.ConnectionId[..16]}");
                await ReadLoopAsync(connection, stoppingToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                // Shutdown.
            }
            catch (Exception ex)
            {
                _logger.LogWarning("companion connection ended: {Reason}", ex.Message);
            }
            finally
            {
                _connection = null;
                FailAllPending("session companion disconnected");
                await connection.DisposeAsync().ConfigureAwait(false);
                _logger.LogInformation("session companion disconnected");
            }
        }
    }

    /// <summary>Forwards an interactive capability to the connected companion.</summary>
    public async Task<JsonObject?> ExecuteCapabilityAsync(
        string capability,
        JsonObject payload,
        TimeSpan timeout,
        CancellationToken cancellationToken)
    {
        var connection = _connection;
        if (connection is null)
        {
            throw new CapabilityException(
                ErrorClasses.DependencyUnavailable,
                "no session companion is connected; interactive capabilities are unavailable",
                retryable: true);
        }

        var requestId = Guid.NewGuid().ToString();
        var tcs = new TaskCompletionSource<ExecResponse>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[requestId] = tcs;
        try
        {
            var request = new ExecRequest
            {
                RequestId = requestId,
                Capability = capability,
                Payload = (JsonObject)payload.DeepClone(),
                TimeoutMs = (int)timeout.TotalMilliseconds,
                ConnectionId = connection.Guard.ConnectionId,
                Seq = connection.Guard.NextOutboundSeq(),
            };
            await connection.WriteLineAsync(PipeJson.Serialize(request), cancellationToken).ConfigureAwait(false);

            using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeoutCts.CancelAfter(timeout);
            await using var registration = timeoutCts.Token.Register(() => tcs.TrySetCanceled(timeoutCts.Token)).ConfigureAwait(false);

            ExecResponse response;
            try
            {
                response = await tcs.Task.ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
            {
                throw new CapabilityException(
                    ErrorClasses.Timeout,
                    $"session companion did not answer within {timeout.TotalSeconds:F0} s",
                    retryable: true);
            }

            if (!response.Ok)
            {
                var error = response.Error ?? ErrorObjects.Create(ErrorClasses.InternalBug, "companion reported failure without error detail", retryable: false);
                throw new CapabilityException(error.Class, error.Message, error.Retryable ?? false);
            }

            return response.Result;
        }
        finally
        {
            _pending.TryRemove(requestId, out _);
        }
    }

    /// <summary>
    /// Challenge, then hello. The challenge is not a secret and proves nothing about who the
    /// companion is — the kernel already answered that. It establishes the connection id and
    /// nonce that every later frame is bound to, and it makes a hello recorded from an
    /// earlier connection useless here.
    /// </summary>
    private async Task<bool> HandshakeAsync(CompanionConnection connection, CancellationToken cancellationToken)
    {
        var nonce = IpcChannelGuard.NewToken();
        var challenge = new ServiceChallenge
        {
            ConnectionId = connection.Guard.ConnectionId,
            Nonce = nonce,
        };
        await connection.WriteLineAsync(PipeJson.Serialize(challenge), cancellationToken).ConfigureAwait(false);

        using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeoutCts.CancelAfter(HandshakeTimeout);

        string? line;
        try
        {
            line = await connection.Reader.ReadLineAsync(timeoutCts.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            _logger.LogWarning("companion did not complete the handshake within {Seconds:F0} s", HandshakeTimeout.TotalSeconds);
            return false;
        }

        if (line is null)
        {
            return false;
        }

        CompanionHello hello;
        try
        {
            if (PipeJson.Deserialize(line) is not CompanionHello parsed)
            {
                _logger.LogWarning("companion connection did not start with companion_hello; dropping");
                return false;
            }

            hello = parsed;
        }
        catch (Exception ex)
        {
            _logger.LogWarning("malformed companion hello: {Reason}", ex.Message);
            return false;
        }

        if (hello.Nonce is null || !IpcChannelGuard.FixedTimeEquals(hello.Nonce, nonce))
        {
            _logger.LogWarning("companion hello did not echo this connection's challenge nonce");
            return false;
        }

        var verdict = connection.Guard.Accept(hello.ConnectionId, hello.Seq);
        if (verdict != IpcRefusal.None)
        {
            _logger.LogWarning("companion hello refused: {Reason}", IpcRefusalText.Describe(verdict));
            return false;
        }

        _logger.LogInformation(
            "session companion admitted (sid={Sid}, session={Session}, pid={Pid}, capabilities: {Capabilities})",
            connection.Peer.Sid,
            connection.Peer.SessionId,
            connection.Peer.ProcessId,
            string.Join(",", hello.Capabilities));
        return true;
    }

    private async Task ReadLoopAsync(CompanionConnection connection, CancellationToken cancellationToken)
    {
        while (true)
        {
            var line = await connection.Reader.ReadLineAsync(cancellationToken).ConfigureAwait(false);
            if (line is null)
            {
                return;
            }

            PipeMessage message;
            try
            {
                message = PipeJson.Deserialize(line);
            }
            catch (Exception ex)
            {
                _logger.LogWarning("malformed pipe frame from companion: {Reason}", ex.Message);
                continue;
            }

            if (message is not ExecResponse response)
            {
                continue;
            }

            var verdict = connection.Guard.Accept(response.ConnectionId, response.Seq);
            if (verdict != IpcRefusal.None)
            {
                // A replayed or stale response must not satisfy a pending request: that is
                // how one honest answer gets reused for a different question.
                RecordRefusal(verdict, connection.Peer);
                continue;
            }

            if (_pending.TryGetValue(response.RequestId, out var tcs))
            {
                tcs.TrySetResult(response);
            }
        }
    }

    private PipePeer? SafeInspect(NamedPipeServerStream server)
    {
        try
        {
            return _inspector.Inspect(server);
        }
        catch (Exception ex)
        {
            _logger.LogWarning("could not inspect pipe peer: {Reason}", ex.Message);
            return null;
        }
    }

    private void RecordRefusal(IpcRefusal refusal, PipePeer? peer)
    {
        Refusals.AddOrUpdate(refusal, 1, (_, count) => count + 1);
        var detail = peer is null
            ? "peer=unidentified"
            : $"sid={peer.Sid} session={peer.SessionId} pid={peer.ProcessId} image={peer.ImagePath ?? "unknown"}";
        _logger.LogWarning("ipc peer refused ({Refusal}): {Reason}; {Detail}", refusal, IpcRefusalText.Describe(refusal), detail);
        _audit?.Write("ipc_peer_refused", status: refusal.ToString(), detail: detail);
    }

    private static async Task DisconnectQuietlyAsync(NamedPipeServerStream server)
    {
        try
        {
            if (server.IsConnected)
            {
                server.Disconnect();
            }
        }
        catch (Exception)
        {
            // The peer may already be gone.
        }

        await server.DisposeAsync().ConfigureAwait(false);
    }

    private void FailAllPending(string reason)
    {
        foreach (var pair in _pending)
        {
            pair.Value.TrySetException(new CapabilityException(ErrorClasses.DependencyUnavailable, reason, retryable: true));
        }
    }

    private NamedPipeServerStream CreateServerStream()
    {
        var security = new PipeSecurity();
        var self = WindowsIdentity.GetCurrent().User
                   ?? throw new InvalidOperationException("cannot resolve current user SID for pipe ACL");
        var authorized = new SecurityIdentifier(_policy.AuthorizedSid);

        // The service account keeps full control; the owner gets exactly what a client needs
        // to talk on the pipe. Note these are usually two different accounts in production —
        // ACLing to "whoever created me" is what broke under Session 0.
        security.AddAccessRule(new PipeAccessRule(self, PipeAccessRights.FullControl, AccessControlType.Allow));
        if (!authorized.Equals(self))
        {
            security.AddAccessRule(new PipeAccessRule(
                authorized,
                PipeAccessRights.ReadWrite | PipeAccessRights.Synchronize,
                AccessControlType.Allow));
        }

        return NamedPipeServerStreamAcl.Create(
            _pipeName,
            PipeDirection.InOut,
            maxNumberOfServerInstances: 1,
            PipeTransmissionMode.Byte,
            // FirstPipeInstance: if this name is already taken, fail — never share the name
            // with whatever got there first.
            PipeOptions.Asynchronous | PipeOptions.FirstPipeInstance,
            inBufferSize: 0,
            outBufferSize: 0,
            security);
    }

    private sealed class CompanionConnection : IAsyncDisposable
    {
        private readonly NamedPipeServerStream _stream;
        private readonly StreamWriter _writer;
        private readonly SemaphoreSlim _writeLock = new(1, 1);

        public CompanionConnection(NamedPipeServerStream stream, IpcChannelGuard guard, PipePeer peer)
        {
            _stream = stream;
            Guard = guard;
            Peer = peer;
            Reader = new StreamReader(stream, new UTF8Encoding(false), detectEncodingFromByteOrderMarks: false, leaveOpen: true);
            _writer = new StreamWriter(stream, new UTF8Encoding(false), leaveOpen: true) { AutoFlush = true };
        }

        public StreamReader Reader { get; }

        public IpcChannelGuard Guard { get; }

        public PipePeer Peer { get; }

        public async Task WriteLineAsync(string line, CancellationToken cancellationToken)
        {
            await _writeLock.WaitAsync(cancellationToken).ConfigureAwait(false);
            try
            {
                await _writer.WriteLineAsync(line.AsMemory(), cancellationToken).ConfigureAwait(false);
            }
            finally
            {
                _writeLock.Release();
            }
        }

        public async ValueTask DisposeAsync()
        {
            _writeLock.Dispose();
            Reader.Dispose();
            await _writer.DisposeAsync().ConfigureAwait(false);
            await _stream.DisposeAsync().ConfigureAwait(false);
        }
    }
}
