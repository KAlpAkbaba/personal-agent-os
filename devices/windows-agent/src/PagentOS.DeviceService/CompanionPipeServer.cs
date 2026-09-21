using System.Collections.Concurrent;
using System.IO.Pipes;
using System.Threading.Channels;
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
/// 2. <b>Sole ownership of the name</b> — if anything already holds the pipe name, creation
///    fails loudly and is recorded, instead of the service quietly becoming a second
///    instance behind a squatter. Three mechanisms overlap here (single instance, a
///    non-default security descriptor, FirstPipeInstance); see `CreateServerStream`.
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
public sealed class CompanionPipeServer : BackgroundService, ICompanionCapabilityTransport
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
        AuditLog? audit = null,
        string? brokerOrigin = null)
    {
        _pipeName = pipeName;
        _policy = policy;
        _inspector = inspector;
        _logger = logger;
        _audit = audit;
        BrokerOrigin = HttpOrigin.Of(brokerOrigin);
    }

    public bool CompanionConnected => _connection is not null;

    /// <summary>
    /// M22: the origin of the Cloud Core this service dials, handed to the companion in every
    /// challenge so it can pin <c>file.fetch</c> to it. Null when the service has no usable
    /// broker URL — then the companion is told nothing and refuses every fetch.
    /// </summary>
    public string? BrokerOrigin { get; }

    /// <summary>Refusals since start, by reason. Surfaced in telemetry; also what the tests assert.</summary>
    public ConcurrentDictionary<IpcRefusal, int> Refusals { get; } = new();

    /// <summary>
    /// How many times creating the pipe failed. Non-zero means something else already holds
    /// the name — the observable signature of squatting. Exposed so a test can assert the
    /// refusal happened, rather than infer it from the absence of a connection.
    /// </summary>
    public int ListenFailures => _listenFailures;

    private int _listenFailures;

    /// <summary>Identity of the currently admitted companion, or null when none is connected.</summary>
    public PipePeer? ConnectedPeer => _connection?.Peer;

    /// <summary>
    /// What the currently admitted companion said it can execute (its hello), or null when
    /// none is connected. Telemetry and a test seam for the M13 manifest composition; the
    /// service's own advertisement to the broker is configuration, not this list.
    /// </summary>
    public IReadOnlyList<string>? CompanionCapabilities => _connection?.Capabilities;

    /// <summary>
    /// The effective security descriptor of the most recently created pipe instance, as
    /// SDDL, read back from the REAL handle — not from the PipeSecurity object we asked for.
    ///
    /// This exists because the pipe's DACL turned out to be unverifiable from outside at
    /// runtime, measured rather than assumed: while the companion is connected (the normal
    /// steady state), every external CreateFile — even a READ_CONTROL-only open, even the
    /// one hiding inside PowerShell's Test-Path — fails with ERROR_PIPE_BUSY on a
    /// single-instance pipe; and against a LISTENING instance the same open *connects*,
    /// consuming the instance and disrupting admission. The only non-invasive place the
    /// effective DACL can be observed is here, on the handle, at creation. It is audited
    /// (`ipc_pipe_created`) so an external verifier can check the runtime descriptor without
    /// touching the pipe. SDDL contains SIDs and rights — no secrets.
    /// </summary>
    public string? LastPipeSddl { get; private set; }

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
                // Includes "the name is already taken", which is how a squatted pipe name
                // shows up. Never fall back to sharing the name with whoever got there first.
                Interlocked.Increment(ref _listenFailures);
                _logger.LogError(ex, "failed to create companion pipe; retrying in 5 s");
                _audit?.Write("ipc_listen_failed", status: "refused", detail: $"pipe={_pipeName}: {ex.Message}");
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
            var connection = new CompanionConnection(server, guard, peer!, onPointerBatchDropped: () => Interlocked.Increment(ref _pointerStreamDropped));
            Task? pointerDrain = null;
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
                pointerDrain = Task.Run(() => DrainPointerOutboxAsync(connection, stoppingToken), CancellationToken.None);
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
                // ADR-0199: whatever mouse session the companion held died with its pipe (the
                // companion releases any held button on its side); a batch that arrives before
                // the next begin is for a session nobody has, and whatever sat in the outbox
                // goes with the connection.
                _openPointerSessions.Clear();
                connection.PointerOutbox.Writer.TryComplete();
                FailAllPending("session companion disconnected");
                await connection.DisposeAsync().ConfigureAwait(false);
                if (pointerDrain is not null)
                {
                    try
                    {
                        await pointerDrain.WaitAsync(TimeSpan.FromSeconds(2)).ConfigureAwait(false);
                    }
                    catch (Exception)
                    {
                        // The drain's write fails with the disposed pipe and the task ends; a
                        // slow exit is not worth holding the accept loop for.
                    }
                }

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
            // The deadline is fixed BEFORE the request is written and the timer below is
            // armed from the same instant, so the companion's remaining budget is measured
            // against exactly the moment this side gives up.
            var deadline = DateTimeOffset.UtcNow.Add(timeout);
            var request = new ExecRequest
            {
                RequestId = requestId,
                Capability = capability,
                Payload = (JsonObject)payload.DeepClone(),
                TimeoutMs = (int)timeout.TotalMilliseconds,
                DeadlineUtcMs = deadline.ToUnixTimeMilliseconds(),
                ConnectionId = connection.Guard.ConnectionId,
                Seq = connection.Guard.NextOutboundSeq(),
            };
            using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeoutCts.CancelAfter(deadline - DateTimeOffset.UtcNow);
            await connection.WriteLineAsync(PipeJson.Serialize(request), cancellationToken).ConfigureAwait(false);

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

            NotePointerSession(capability, payload, response.Result);
            return response.Result;
        }
        finally
        {
            _pending.TryRemove(requestId, out _);
        }
    }

    // ------------------------------------------------------------ pointer stream (ADR-0199)

    private readonly ConcurrentDictionary<string, byte> _openPointerSessions = new(StringComparer.Ordinal);
    private int _pointerStreamForwarded;
    private int _pointerStreamDropped;
    private int _pointerStreamIgnored;

    /// <summary>The mouse sessions the companion has opened (a succeeded <c>pointer.stream_begin</c>) and not yet ended, as this service saw them pass.</summary>
    public IReadOnlyCollection<string> OpenPointerSessions => _openPointerSessions.Keys.ToArray();

    /// <summary>How many <c>pointer_stream</c> batches were written to the companion since start (telemetry; asserted by tests).</summary>
    public int PointerStreamForwarded => _pointerStreamForwarded;

    /// <summary>How many were dropped before the pipe: no companion, oversize, or a write that failed.</summary>
    public int PointerStreamDropped => _pointerStreamDropped;

    /// <summary>How many named a session the companion did not open through this service — ignored, never forwarded.</summary>
    public int PointerStreamIgnored => _pointerStreamIgnored;

    /// <summary>
    /// The service's own record of which mouse sessions exist, kept from the answers that
    /// pass through it: a succeeded <c>pointer.stream_begin</c> opens one, a
    /// <c>pointer.stream_end</c> closes it (whatever its answer said — the companion has
    /// nothing left to apply to), and a disconnect closes all. This is what lets
    /// <see cref="ForwardPointerStreamAsync"/> refuse a batch for a session nobody opened
    /// BEFORE it crosses the pipe, rather than after the companion has already parsed it.
    /// </summary>
    private void NotePointerSession(string capability, JsonObject payload, JsonObject? result)
    {
        if (string.Equals(capability, OperatorCapabilityNames.PointerStreamBegin, StringComparison.Ordinal))
        {
            var session = result?["session"]?.GetValue<string>() ?? payload["session"]?.GetValue<string>();
            if (!string.IsNullOrEmpty(session))
            {
                // One mouse session at a time on the device (the companion replaces an open
                // one on begin), so the set mirrors that: the new session is the only one.
                _openPointerSessions.Clear();
                _openPointerSessions[session] = 1;
            }
        }
        else if (string.Equals(capability, OperatorCapabilityNames.PointerStreamEnd, StringComparison.Ordinal))
        {
            var session = payload["session"]?.GetValue<string>();
            if (!string.IsNullOrEmpty(session))
            {
                _openPointerSessions.TryRemove(session, out _);
            }
        }
    }

    /// <summary>
    /// Forwards a <c>pointer_stream</c> batch to the connected companion as a fire-and-forget
    /// <c>pointer.stream</c> request (ADR-0199): one <see cref="ExecRequest"/> with
    /// <see cref="ExecRequest.OneWay"/> set, the batch as its payload, NO entry in the pending
    /// table and no wait for an answer. The same connection id and outbound sequence as every
    /// other pipe frame, so the companion's freshness rule applies to it; the same peer
    /// admission, because it is written on the one connection that passed it. Returns false —
    /// never throws — when there is no companion, when the session is not one the companion
    /// opened, or when the frame exceeds the bound; a dropped batch is replaced by the next one
    /// from the owner's hand.
    ///
    /// <para>Accepted here means QUEUED, not written. The pipe is created with zero-byte
    /// buffers, so a write completes only when the companion reads it — measured on
    /// 2026-09-21 as a hang of the caller when the peer was not reading — and this method is
    /// called from the device connection's receive loop. A companion that stalls must cost
    /// the owner pointer batches, not the device's WebSocket, so the batch goes into a
    /// per-connection outbox of <see cref="PointerOutboxCapacity"/> that one writer task
    /// drains; when it is full the OLDEST batch is dropped and counted (the hand is live:
    /// the newest frames are the ones that matter), and the sequence number is taken under
    /// the write lock at the moment of writing so the companion's strictly-increasing rule
    /// holds against ordinary requests written meanwhile.</para>
    /// </summary>
    public Task<bool> ForwardPointerStreamAsync(PointerStreamMessage frame, CancellationToken cancellationToken)
    {
        var connection = _connection;
        if (connection is null)
        {
            Interlocked.Increment(ref _pointerStreamDropped);
            return Task.FromResult(false);
        }

        if (!_openPointerSessions.ContainsKey(frame.Session))
        {
            // Ignored, counted, and nothing crosses the pipe: a batch for a session this
            // device never opened is either stale (its stream already ended) or not ours.
            Interlocked.Increment(ref _pointerStreamIgnored);
            return Task.FromResult(false);
        }

        var frameBytes = frame.SerializedBytes();
        if (frameBytes > PointerStream.MaxFrameBytes)
        {
            Interlocked.Increment(ref _pointerStreamDropped);
            _logger.LogWarning("pointer_stream batch refused: {Bytes} bytes exceeds the {Limit}-byte bound", frameBytes, PointerStream.MaxFrameBytes);
            return Task.FromResult(false);
        }

        if (!connection.PointerOutbox.Writer.TryWrite(frame.ToPayload()))
        {
            // The outbox is completed: the connection is going away under us.
            Interlocked.Increment(ref _pointerStreamDropped);
            return Task.FromResult(false);
        }

        return Task.FromResult(true);
    }

    /// <summary>
    /// The one writer of one-way <c>pointer.stream</c> requests for a connection: takes each
    /// queued batch, stamps the request with the NEXT outbound sequence under the write lock
    /// and writes it. A write that fails ends the drain — the connection is gone and its
    /// outbox with it. Never answers, never records, never throws out of the task.
    /// </summary>
    private async Task DrainPointerOutboxAsync(CompanionConnection connection, CancellationToken cancellationToken)
    {
        try
        {
            await foreach (var payload in connection.PointerOutbox.Reader.ReadAllAsync(cancellationToken).ConfigureAwait(false))
            {
                var request = new ExecRequest
                {
                    RequestId = Guid.NewGuid().ToString(),
                    Capability = OperatorCapabilityNames.PointerStream,
                    Payload = payload,
                    TimeoutMs = (int)PointerStreamRequestTimeout.TotalMilliseconds,
                    DeadlineUtcMs = DateTimeOffset.UtcNow.Add(PointerStreamRequestTimeout).ToUnixTimeMilliseconds(),
                    OneWay = true,
                    ConnectionId = connection.Guard.ConnectionId,
                };

                try
                {
                    await connection.WriteSequencedAsync(seq => PipeJson.Serialize(request with { Seq = seq }), cancellationToken).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    return;
                }
                catch (Exception ex)
                {
                    Interlocked.Increment(ref _pointerStreamDropped);
                    _logger.LogWarning("pointer_stream forward failed: {Reason}", ex.Message);
                    return;
                }

                Interlocked.Increment(ref _pointerStreamForwarded);
            }
        }
        catch (OperationCanceledException)
        {
            // Shutdown.
        }
        catch (Exception ex)
        {
            _logger.LogWarning("pointer_stream drain ended: {Reason}", ex.Message);
        }
    }

    /// <summary>The nominal budget stamped on a one-way batch; nothing waits on it, but the request shape carries one.</summary>
    public static readonly TimeSpan PointerStreamRequestTimeout = TimeSpan.FromSeconds(1);

    /// <summary>
    /// Batches waiting for the writer, per connection: one being written plus one behind it.
    /// The browser sends at most 30 batches a second and Cloud Core already coalesces; a
    /// companion that cannot take two in a row is stalled, and the newest batch is the one
    /// the owner's hand is making now.
    /// </summary>
    public const int PointerOutboxCapacity = 2;

    /// <summary>How many voice_sideband frames were written to the companion since start (telemetry; asserted by tests).</summary>
    public int SidebandForwarded => _sidebandForwarded;

    /// <summary>How many voice_sideband frames were refused before the pipe (oversize) or dropped (no companion).</summary>
    public int SidebandDropped => _sidebandDropped;

    private int _sidebandForwarded;
    private int _sidebandDropped;

    /// <summary>
    /// Forwards a <c>voice_sideband</c> device-protocol frame to the connected companion,
    /// opaquely (M12, ADR-0039). One-way: no response is awaited, nothing is recorded in the
    /// pending table, and the frame is never interpreted here. It rides the same connection
    /// id and outbound sequence as exec requests, so the companion applies the same
    /// freshness rule to it. Returns false — never throws — when there is no companion or the
    /// frame exceeds the bound; Cloud Core queues what was not delivered and the client
    /// drains it over HTTP, so a drop here loses nothing.
    /// </summary>
    public async Task<bool> ForwardSidebandAsync(JsonObject frame, CancellationToken cancellationToken)
    {
        var connection = _connection;
        if (connection is null)
        {
            Interlocked.Increment(ref _sidebandDropped);
            return false;
        }

        // The bound is on the broker's frame itself, measured before a sequence number is
        // consumed, so a refused frame leaves the connection's ordering exactly as it was.
        var frameBytes = Encoding.UTF8.GetByteCount(frame.ToJsonString());
        if (frameBytes > VoiceSideband.MaxFrameBytes)
        {
            Interlocked.Increment(ref _sidebandDropped);
            _logger.LogWarning(
                "voice_sideband frame refused: {Bytes} bytes exceeds the {Limit}-byte bound",
                frameBytes,
                VoiceSideband.MaxFrameBytes);
            return false;
        }

        var forward = new SidebandForward
        {
            Frame = (JsonObject)frame.DeepClone(),
            ConnectionId = connection.Guard.ConnectionId,
            Seq = connection.Guard.NextOutboundSeq(),
        };
        var line = PipeJson.Serialize(forward);

        try
        {
            await connection.WriteLineAsync(line, cancellationToken).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception ex)
        {
            Interlocked.Increment(ref _sidebandDropped);
            _logger.LogWarning("voice_sideband forward failed: {Reason}", ex.Message);
            return false;
        }

        Interlocked.Increment(ref _sidebandForwarded);
        return true;
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
            BrokerOrigin = BrokerOrigin,
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

        connection.Capabilities = hello.Capabilities;
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

        var stream = NamedPipeServerStreamAcl.Create(
            _pipeName,
            PipeDirection.InOut,
            // One instance, one owner of the name.
            //
            // Three things independently prevent this service from ending up sharing a name
            // that a squatter already holds: this instance limit, the requirement that a
            // second instance match the first's security descriptor (ours is not the
            // default), and FirstPipeInstance below. Measured, after the independent
            // verification of ADR-0028 pointed out that the original comment credited the
            // flag alone: removing any single one of the three still refuses, so no test can
            // attribute the guarantee to one mechanism and this comment does not pretend
            // otherwise. What is asserted — by
            // `The_service_refuses_to_share_a_pipe_name_someone_else_already_holds` — is the
            // outcome: a listen failure is recorded and the service never serves on that name.
            maxNumberOfServerInstances: 1,
            PipeTransmissionMode.Byte,
            PipeOptions.Asynchronous | PipeOptions.FirstPipeInstance,
            inBufferSize: 0,
            outBufferSize: 0,
            security);

        // Read the EFFECTIVE descriptor back off the real handle and audit it once per
        // change. Asking the object we just configured would prove what we requested; the
        // handle proves what the kernel actually applied — and no external observer can read
        // it non-invasively at any later point in the pipe's life (see LastPipeSddl).
        try
        {
            var effective = stream.GetAccessControl();
            var sddl = effective.GetSecurityDescriptorSddlForm(
                AccessControlSections.Access | AccessControlSections.Owner);
            if (!string.Equals(sddl, LastPipeSddl, StringComparison.Ordinal))
            {
                LastPipeSddl = sddl;
                _logger.LogInformation("companion pipe created; effective sddl={Sddl}", sddl);
                _audit?.Write("ipc_pipe_created", status: "ok", detail: $"pipe={_pipeName} sddl={sddl}");
            }
        }
        catch (Exception ex)
        {
            // The pipe still works without the audit row; the verifier will say NOT proven
            // rather than guessing, which is the correct failure direction.
            _logger.LogWarning("could not read the pipe's effective security descriptor: {Reason}", ex.Message);
        }

        return stream;
    }

    private sealed class CompanionConnection : IAsyncDisposable
    {
        private readonly NamedPipeServerStream _stream;
        private readonly StreamWriter _writer;
        private readonly SemaphoreSlim _writeLock = new(1, 1);

        public CompanionConnection(NamedPipeServerStream stream, IpcChannelGuard guard, PipePeer peer, Action? onPointerBatchDropped = null)
        {
            _stream = stream;
            Guard = guard;
            Peer = peer;
            Reader = new StreamReader(stream, new UTF8Encoding(false), detectEncodingFromByteOrderMarks: false, leaveOpen: true);
            _writer = new StreamWriter(stream, new UTF8Encoding(false), leaveOpen: true) { AutoFlush = true };
            PointerOutbox = Channel.CreateBounded<JsonObject>(
                new BoundedChannelOptions(PointerOutboxCapacity)
                {
                    FullMode = BoundedChannelFullMode.DropOldest,
                    SingleReader = true,
                    SingleWriter = false,
                },
                _ => onPointerBatchDropped?.Invoke());
        }

        public StreamReader Reader { get; }

        public IpcChannelGuard Guard { get; }

        public PipePeer Peer { get; }

        public IReadOnlyList<string>? Capabilities { get; set; }

        /// <summary>ADR-0199: the one-way <c>pointer.stream</c> batches waiting for this connection's writer (see <see cref="ForwardPointerStreamAsync"/>).</summary>
        public Channel<JsonObject> PointerOutbox { get; }

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

        /// <summary>Takes the next outbound sequence UNDER the write lock and writes the line built from it, so no later-numbered frame can be written before an earlier one.</summary>
        public async Task WriteSequencedAsync(Func<long, string> build, CancellationToken cancellationToken)
        {
            await _writeLock.WaitAsync(cancellationToken).ConfigureAwait(false);
            try
            {
                var line = build(Guard.NextOutboundSeq());
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

