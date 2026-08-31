using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Identity;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.Agent.Core.Connection;

/// <summary>
/// Outbound WebSocket client loop: hello → challenge → auth → welcome handshake, heartbeats at
/// the welcome-provided interval, command/cancel dispatch, malformed-frame error replies, and
/// infinite exponential-backoff reconnect (1 s → 60 s, factor 2, full jitter).
/// </summary>
public sealed class AgentConnection(
    AgentConnectionOptions options,
    DeviceIdentity identity,
    CommandDispatcher dispatcher,
    AuditLog audit,
    ILogger<AgentConnection> logger,
    Random? jitterRandom = null)
{
    private const int MaxFrameBytes = 1024 * 1024;

    private delegate Task SendFunc(ProtocolMessage message, CancellationToken cancellationToken);

    public async Task RunAsync(CancellationToken cancellationToken)
    {
        var backoff = new BackoffPolicy(options.BackoffBaseSeconds, options.BackoffMaxSeconds, jitterRandom);
        var attempt = 0;
        while (!cancellationToken.IsCancellationRequested)
        {
            try
            {
                await ConnectAndServeAsync(() => attempt = 0, cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                logger.LogWarning("connection to {Url} ended: {Reason}", options.BrokerWsUrl, ex.Message);
            }

            if (cancellationToken.IsCancellationRequested)
            {
                break;
            }

            var delay = backoff.NextDelay(attempt);
            attempt = Math.Min(attempt + 1, 30);
            logger.LogInformation("reconnecting in {DelayMs} ms (attempt {Attempt})", (int)delay.TotalMilliseconds, attempt);
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

    private async Task ConnectAndServeAsync(Action onAuthenticated, CancellationToken cancellationToken)
    {
        using var ws = new ClientWebSocket();
        await ws.ConnectAsync(options.BrokerWsUrl, cancellationToken).ConfigureAwait(false);
        using var sendLock = new SemaphoreSlim(1, 1);

        async Task SendAsync(ProtocolMessage message, CancellationToken ct)
        {
            var bytes = Encoding.UTF8.GetBytes(ProtocolJson.Serialize(message));
            await sendLock.WaitAsync(ct).ConfigureAwait(false);
            try
            {
                await ws.SendAsync(bytes, WebSocketMessageType.Text, endOfMessage: true, ct).ConfigureAwait(false);
            }
            finally
            {
                sendLock.Release();
            }
        }

        using var handshakeCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        handshakeCts.CancelAfter(options.HandshakeTimeout);
        var handshakeToken = handshakeCts.Token;

        await SendAsync(
            new HelloMessage
            {
                ProtocolVersion = ProtocolConstants.Version,
                DeviceId = options.DeviceId,
                SoftwareVersion = options.SoftwareVersion,
                Capabilities = options.Capabilities,
            },
            handshakeToken).ConfigureAwait(false);

        var challenge = await ReceiveExpectedAsync<ChallengeMessage>(ws, handshakeToken).ConfigureAwait(false);
        var nonceBytes = Convert.FromBase64String(challenge.Nonce);
        var signature = identity.Sign(nonceBytes, options.DeviceId);
        await SendAsync(new AuthMessage { Signature = Convert.ToBase64String(signature) }, handshakeToken).ConfigureAwait(false);

        var welcome = await ReceiveExpectedAsync<WelcomeMessage>(ws, handshakeToken).ConfigureAwait(false);
        onAuthenticated();
        audit.Write("session_start", deviceId: options.DeviceId, detail: welcome.SessionId);
        logger.LogInformation("session {SessionId} established with {Url}", welcome.SessionId, options.BrokerWsUrl);

        var heartbeatInterval = options.HeartbeatIntervalOverrideS ?? welcome.HeartbeatIntervalS;

        dispatcher.AttachSender((ack, ct) => SendAsync(ack, ct));
        using var loopCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        var heartbeatTask = HeartbeatLoopAsync(SendAsync, ws, heartbeatInterval, loopCts.Token);
        try
        {
            while (true)
            {
                var raw = await ReceiveTextAsync(ws, cancellationToken).ConfigureAwait(false);
                await HandleFrameAsync(raw, SendAsync, cancellationToken).ConfigureAwait(false);
            }
        }
        finally
        {
            dispatcher.DetachSender();
            loopCts.Cancel();
            try
            {
                await heartbeatTask.ConfigureAwait(false);
            }
            catch (Exception)
            {
                // Heartbeat teardown failures are irrelevant; the reconnect loop handles recovery.
            }

            audit.Write("session_end", deviceId: options.DeviceId, detail: welcome.SessionId);
        }
    }

    private async Task HandleFrameAsync(string raw, SendFunc send, CancellationToken cancellationToken)
    {
        ProtocolMessage message;
        try
        {
            message = ProtocolJson.Deserialize(raw);
            MessageValidator.ValidateInbound(message);
        }
        catch (Exception ex) when (ex is JsonException or NotSupportedException or ProtocolValidationException or FormatException)
        {
            logger.LogWarning("malformed frame received: {Reason}", ex.Message);
            await send(
                new ErrorMessage
                {
                    CommandId = TryExtractCommandId(raw),
                    Error = ErrorObjects.Create(ErrorClasses.ValidationError, $"malformed frame: {ex.Message}", retryable: false),
                },
                cancellationToken).ConfigureAwait(false);
            return;
        }

        switch (message)
        {
            case HeartbeatAckMessage:
                break;
            case CommandMessage command:
                RunDispatch(() => dispatcher.HandleCommandAsync(command.Command));
                break;
            case CancelMessage cancel:
                RunDispatch(() => dispatcher.HandleCancelAsync(cancel.CommandId));
                break;
            case ErrorMessage error:
                logger.LogWarning("broker error frame: {Class} {Message}", error.Error.Class, error.Error.Message);
                break;
            default:
                logger.LogWarning("unexpected frame type {Type} ignored", message.GetType().Name);
                break;
        }
    }

    private void RunDispatch(Func<Task> action)
    {
        _ = Task.Run(async () =>
        {
            try
            {
                await action().ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                logger.LogError(ex, "command dispatch failed");
            }
        });
    }

    private async Task HeartbeatLoopAsync(SendFunc send, ClientWebSocket ws, double intervalSeconds, CancellationToken cancellationToken)
    {
        long seq = 0;
        var interval = TimeSpan.FromSeconds(intervalSeconds);
        try
        {
            while (true)
            {
                await Task.Delay(interval, cancellationToken).ConfigureAwait(false);
                await send(new HeartbeatMessage { Seq = seq++ }, cancellationToken).ConfigureAwait(false);
            }
        }
        catch (OperationCanceledException)
        {
            // Normal teardown.
        }
        catch (Exception ex)
        {
            logger.LogWarning("heartbeat send failed ({Reason}); aborting socket", ex.Message);
            ws.Abort();
        }
    }

    private async Task<T> ReceiveExpectedAsync<T>(ClientWebSocket ws, CancellationToken cancellationToken)
        where T : ProtocolMessage
    {
        var raw = await ReceiveTextAsync(ws, cancellationToken).ConfigureAwait(false);
        var message = ProtocolJson.Deserialize(raw);
        if (message is ErrorMessage error)
        {
            throw new InvalidOperationException($"broker rejected handshake: {error.Error.Class}: {error.Error.Message}");
        }

        if (message is not T expected)
        {
            throw new ProtocolValidationException($"expected {typeof(T).Name} during handshake but received {message.GetType().Name}");
        }

        MessageValidator.ValidateInbound(expected);
        return expected;
    }

    private static async Task<string> ReceiveTextAsync(ClientWebSocket ws, CancellationToken cancellationToken)
    {
        var buffer = new byte[16 * 1024];
        using var stream = new MemoryStream();
        while (true)
        {
            var result = await ws.ReceiveAsync(buffer, cancellationToken).ConfigureAwait(false);
            if (result.MessageType == WebSocketMessageType.Close)
            {
                throw new WebSocketException(WebSocketError.ConnectionClosedPrematurely, "broker closed the connection");
            }

            stream.Write(buffer, 0, result.Count);
            if (stream.Length > MaxFrameBytes)
            {
                throw new ProtocolValidationException("frame exceeds 1 MiB limit");
            }

            if (result.EndOfMessage)
            {
                break;
            }
        }

        return Encoding.UTF8.GetString(stream.ToArray());
    }

    private static string? TryExtractCommandId(string raw)
    {
        try
        {
            var node = JsonNode.Parse(raw);
            var value = node?["command_id"]?.GetValue<string>() ?? node?["command"]?["command_id"]?.GetValue<string>();
            return value is not null && Guid.TryParse(value, out _) ? value : null;
        }
        catch (Exception)
        {
            return null;
        }
    }
}
