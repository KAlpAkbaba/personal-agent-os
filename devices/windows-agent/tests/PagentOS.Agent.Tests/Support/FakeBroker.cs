using System.Net;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using System.Threading.Channels;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.Agent.Tests.Support;

/// <summary>
/// Minimal in-test Device Broker: Kestrel WebSocket endpoint implementing the protocol v1
/// handshake plus the enrollment REST endpoint. Tests script command delivery through
/// <see cref="BrokerSession"/>.
/// </summary>
public sealed class FakeBroker : IAsyncDisposable
{
    private readonly WebApplication _app;
    private readonly Channel<BrokerSession> _sessions = Channel.CreateUnbounded<BrokerSession>();

    public int Port { get; }

    public double HeartbeatIntervalS { get; }

    /// <summary>When set, the auth signature is verified against this SPKI public key.</summary>
    public string? ExpectedPublicKeySpkiB64 { get; set; }

    public JsonObject? LastEnrollRequest { get; private set; }

    private FakeBroker(int port, double heartbeatIntervalS)
    {
        Port = port;
        HeartbeatIntervalS = heartbeatIntervalS;

        var builder = WebApplication.CreateBuilder();
        builder.Logging.ClearProviders();
        builder.WebHost.UseUrls($"http://127.0.0.1:{port}");
        _app = builder.Build();
        _app.UseWebSockets();
        _app.MapPost("/v1/devices/enroll", async (HttpContext context) =>
        {
            var body = await JsonNode.ParseAsync(context.Request.Body);
            LastEnrollRequest = body?.AsObject();
            await context.Response.WriteAsJsonAsync(new { device_id = Guid.NewGuid().ToString() });
        });
        _app.Map("/v1/devices/connect", HandleWebSocketAsync);
    }

    public Uri WsUri => new($"ws://127.0.0.1:{Port}/v1/devices/connect");

    public Uri RestBase => new($"http://127.0.0.1:{Port}");

    public static async Task<FakeBroker> StartAsync(int port = 0, double heartbeatIntervalS = 60)
    {
        if (port == 0)
        {
            port = GetFreePort();
        }

        var broker = new FakeBroker(port, heartbeatIntervalS);
        await broker._app.StartAsync();
        return broker;
    }

    public static int GetFreePort()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        listener.Stop();
        return port;
    }

    /// <summary>
    /// How long a wait helper gives up after when the caller names no bound.
    ///
    /// This is a HANG GUARD, not a latency budget. Every caller's assertion is about the
    /// CONTENT of the frame that arrives, and the tests that genuinely measure cadence pass
    /// their own bound (LivingCoreCapabilityTests measures the interval between two
    /// heartbeats, with 20 s and 10 s written out). At 15 s it was neither: a loaded machine
    /// turned "the agent connected and sent its first heartbeat" into a failure - the sibling
    /// test in that file already records a 8.65 s start-up on a GitHub runner, and
    /// A_device_with_no_companion_sends_the_heartbeat_it_always_sent lost a full-suite run to
    /// it here on 2026-09-09. Nothing is weakened by the larger number: no test asserts that
    /// a wait TIMES OUT, so only a genuine hang can reach it, and the happy path is unchanged.
    /// </summary>
    internal static readonly TimeSpan DefaultWait = TimeSpan.FromSeconds(60);

    public async Task<BrokerSession> WaitForSessionAsync(TimeSpan? timeout = null)
    {
        using var cts = new CancellationTokenSource(timeout ?? DefaultWait);
        try
        {
            return await _sessions.Reader.ReadAsync(cts.Token);
        }
        catch (OperationCanceledException)
        {
            throw new TimeoutException("no agent session was established in time");
        }
    }

    public async ValueTask DisposeAsync()
    {
        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        try
        {
            await _app.StopAsync(cts.Token);
        }
        catch (Exception)
        {
            // Force shutdown below.
        }

        await _app.DisposeAsync();
    }

    private async Task HandleWebSocketAsync(HttpContext context)
    {
        if (!context.WebSockets.IsWebSocketRequest)
        {
            context.Response.StatusCode = 400;
            return;
        }

        using var ws = await context.WebSockets.AcceptWebSocketAsync();
        var aborted = context.RequestAborted;
        try
        {
            var hello = (HelloMessage)ProtocolJson.Deserialize(await WsText.ReceiveAsync(ws, aborted));
            var nonce = RandomNumberGenerator.GetBytes(32);
            await WsText.SendAsync(ws, ProtocolJson.Serialize(new ChallengeMessage { Nonce = Convert.ToBase64String(nonce) }), aborted);
            var auth = (AuthMessage)ProtocolJson.Deserialize(await WsText.ReceiveAsync(ws, aborted));
            if (ExpectedPublicKeySpkiB64 is not null && !VerifySignature(hello.DeviceId, nonce, auth.Signature))
            {
                await WsText.SendAsync(
                    ws,
                    ProtocolJson.Serialize(new ErrorMessage
                    {
                        Error = ErrorObjects.Create(ErrorClasses.AuthError, "authentication failed", retryable: false),
                    }),
                    aborted);
                await ws.CloseAsync(WebSocketCloseStatus.PolicyViolation, "auth", aborted);
                return;
            }

            await WsText.SendAsync(
                ws,
                ProtocolJson.Serialize(new WelcomeMessage
                {
                    SessionId = Guid.NewGuid().ToString(),
                    HeartbeatIntervalS = HeartbeatIntervalS,
                }),
                aborted);

            var session = new BrokerSession(ws, hello);
            _sessions.Writer.TryWrite(session);
            await session.PumpAsync(aborted);
        }
        catch (Exception)
        {
            // Session teardown; the test observes it through the session channel.
        }
    }

    private bool VerifySignature(string deviceId, byte[] nonce, string signatureB64)
    {
        using var publicKey = ECDsa.Create();
        publicKey.ImportSubjectPublicKeyInfo(Convert.FromBase64String(ExpectedPublicKeySpkiB64!), out _);
        var deviceIdBytes = Encoding.UTF8.GetBytes(deviceId);
        var data = new byte[nonce.Length + deviceIdBytes.Length];
        nonce.CopyTo(data, 0);
        deviceIdBytes.CopyTo(data, nonce.Length);
        return publicKey.VerifyData(
            data,
            Convert.FromBase64String(signatureB64),
            HashAlgorithmName.SHA256,
            DSASignatureFormat.Rfc3279DerSequence);
    }
}

public static class WsText
{
    public static async Task<string> ReceiveAsync(WebSocket ws, CancellationToken cancellationToken)
    {
        var buffer = new byte[16 * 1024];
        using var stream = new MemoryStream();
        while (true)
        {
            var result = await ws.ReceiveAsync(buffer, cancellationToken);
            if (result.MessageType == WebSocketMessageType.Close)
            {
                throw new WebSocketException(WebSocketError.ConnectionClosedPrematurely, "peer closed");
            }

            stream.Write(buffer, 0, result.Count);
            if (result.EndOfMessage)
            {
                break;
            }
        }

        return Encoding.UTF8.GetString(stream.ToArray());
    }

    public static Task SendAsync(WebSocket ws, string text, CancellationToken cancellationToken)
        => ws.SendAsync(Encoding.UTF8.GetBytes(text), WebSocketMessageType.Text, endOfMessage: true, cancellationToken);
}

/// <summary>One authenticated agent session as seen by the fake broker.</summary>
public sealed class BrokerSession(WebSocket ws, HelloMessage hello)
{
    private readonly SemaphoreSlim _sendLock = new(1, 1);
    private readonly Channel<ProtocolMessage> _received = Channel.CreateUnbounded<ProtocolMessage>();

    public HelloMessage Hello { get; } = hello;

    public bool AutoHeartbeatAck { get; set; } = true;

    internal async Task PumpAsync(CancellationToken cancellationToken)
    {
        try
        {
            while (true)
            {
                var raw = await WsText.ReceiveAsync(ws, cancellationToken);
                var message = ProtocolJson.Deserialize(raw);
                if (message is HeartbeatMessage heartbeat && AutoHeartbeatAck)
                {
                    await SendAsync(new HeartbeatAckMessage { Seq = heartbeat.Seq }, cancellationToken);
                }

                _received.Writer.TryWrite(message);
            }
        }
        finally
        {
            _received.Writer.TryComplete();
        }
    }

    public async Task SendAsync(ProtocolMessage message, CancellationToken cancellationToken = default)
    {
        await SendRawAsync(ProtocolJson.Serialize(message), cancellationToken);
    }

    public async Task SendRawAsync(string raw, CancellationToken cancellationToken = default)
    {
        await _sendLock.WaitAsync(cancellationToken);
        try
        {
            await WsText.SendAsync(ws, raw, cancellationToken);
        }
        finally
        {
            _sendLock.Release();
        }
    }

    public Task SendCommandAsync(CommandEnvelope command, CancellationToken cancellationToken = default)
        => SendAsync(new CommandMessage { Command = command }, cancellationToken);

    public async Task<CommandAckMessage> WaitForAckAsync(string commandId, string status, TimeSpan? timeout = null)
    {
        return await WaitForAsync<CommandAckMessage>(
            ack => ack.CommandId == commandId && ack.Status == status,
            $"ack {commandId}/{status}",
            timeout);
    }

    public async Task<ErrorMessage> WaitForErrorAsync(TimeSpan? timeout = null)
        => await WaitForAsync<ErrorMessage>(_ => true, "error frame", timeout);

    public async Task<HeartbeatMessage> WaitForHeartbeatAsync(TimeSpan? timeout = null)
        => await WaitForAsync<HeartbeatMessage>(_ => true, "heartbeat", timeout);

    private async Task<T> WaitForAsync<T>(Func<T, bool> predicate, string description, TimeSpan? timeout)
        where T : ProtocolMessage
    {
        using var cts = new CancellationTokenSource(timeout ?? FakeBroker.DefaultWait);
        try
        {
            while (true)
            {
                var message = await _received.Reader.ReadAsync(cts.Token);
                if (message is T typed && predicate(typed))
                {
                    return typed;
                }
            }
        }
        catch (OperationCanceledException)
        {
            throw new TimeoutException($"did not receive {description} in time");
        }
        catch (ChannelClosedException)
        {
            throw new TimeoutException($"session closed before {description} arrived");
        }
    }
}
