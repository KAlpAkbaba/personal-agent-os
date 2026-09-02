using System.Net.WebSockets;
using System.Text;
using System.Threading.Channels;
using Microsoft.Extensions.Logging;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Sideband;

namespace PagentOS.Companion.Audio.Media;

/// <summary>
/// The WebSocket audio leg: PCM16 frames base64-encoded into the provider's JSON events,
/// provider events decoded on a receive loop into <see cref="Events"/>. Higher latency than
/// WebRTC (no jitter buffer, TCP head-of-line blocking) but available to any .NET process
/// today, so it is the first real leg. The socket factory is injectable so the leg is
/// exercised against a loopback Kestrel endpoint in tests.
/// </summary>
public sealed class WebSocketMediaLeg(
    IProviderWireCodec codec,
    TimeProvider time,
    ILogger? logger = null,
    Func<ClientWebSocket>? socketFactory = null) : IMediaLeg
{
    public const string TransportName = "websocket";

    private readonly Channel<ProviderEvent> _events = Channel.CreateUnbounded<ProviderEvent>(new UnboundedChannelOptions
    {
        SingleReader = true,
    });

    private readonly SemaphoreSlim _sendLock = new(1, 1);
    private ClientWebSocket? _socket;
    private Task? _receiveLoop;
    private CancellationTokenSource? _receiveCts;
    private bool _disconnectReported;

    public string Transport => TransportName;

    public bool IsOpen => _socket?.State == WebSocketState.Open;

    public ChannelReader<ProviderEvent> Events => _events.Reader;

    public IProviderWireCodec Codec => codec;

    public async Task OpenAsync(RealtimeSessionGrant grant, MediaLegOptions options, CancellationToken cancellationToken)
    {
        if (_socket is not null)
        {
            throw new InvalidOperationException("media leg already opened; create a new leg per connection");
        }

        var socket = socketFactory?.Invoke() ?? new ClientWebSocket();
        foreach (var header in codec.ConnectHeaders(grant))
        {
            socket.Options.SetRequestHeader(header.Key, header.Value);
        }

        var uri = codec.ConnectUri(grant);
        await socket.ConnectAsync(uri, cancellationToken).ConfigureAwait(false);
        _socket = socket;
        _disconnectReported = false;
        _receiveCts = CancellationTokenSource.CreateLinkedTokenSource(CancellationToken.None);
        _receiveLoop = Task.Run(() => ReceiveLoopAsync(socket, _receiveCts.Token), CancellationToken.None);
        logger?.LogInformation("media leg open: transport=websocket provider={Provider} host={Host}", codec.Provider, uri.Host);

        await SendCommandAsync(
            new SessionConfigureCommand(grant.Instructions, grant.Tools, options.EndOfTurn, grant.ProviderSessionConfig),
            cancellationToken).ConfigureAwait(false);
    }

    public ValueTask SendAudioAsync(AudioFrame frame, CancellationToken cancellationToken)
        => new(SendCommandAsync(new AppendAudioCommand(frame.Pcm16), cancellationToken));

    public Task CommitTurnAsync(CancellationToken cancellationToken)
        => SendCommandAsync(new CommitTurnCommand(), cancellationToken);

    public Task CancelResponseAsync(CancellationToken cancellationToken)
        => SendCommandAsync(new CancelResponseCommand(), cancellationToken);

    public Task SubmitToolResultAsync(string callId, string outputJson, bool final, bool followUp, CancellationToken cancellationToken)
        => SendCommandAsync(new ToolResultCommand(callId, outputJson, final, followUp), cancellationToken);

    public Task SayAsync(string text, CancellationToken cancellationToken)
        => SendCommandAsync(new SayCommand(text), cancellationToken);

    public async Task CloseAsync(CancellationToken cancellationToken)
    {
        var socket = _socket;
        if (socket is null)
        {
            return;
        }

        try
        {
            if (socket.State is WebSocketState.Open or WebSocketState.CloseReceived)
            {
                await socket.CloseOutputAsync(WebSocketCloseStatus.NormalClosure, "client closing", cancellationToken).ConfigureAwait(false);
            }
        }
        catch (Exception ex) when (ex is WebSocketException or ObjectDisposedException or OperationCanceledException)
        {
            // Already gone; the receive loop reports the disconnect.
        }

        _receiveCts?.Cancel();
        if (_receiveLoop is not null)
        {
            try
            {
                await _receiveLoop.ConfigureAwait(false);
            }
            catch (Exception)
            {
                // Receive loop failures were already turned into DisconnectedEvent.
            }
        }
    }

    public async ValueTask DisposeAsync()
    {
        await CloseAsync(CancellationToken.None).ConfigureAwait(false);
        _socket?.Dispose();
        _receiveCts?.Dispose();
        _sendLock.Dispose();
    }

    private async Task SendCommandAsync(ProviderCommand command, CancellationToken cancellationToken)
    {
        var socket = _socket ?? throw new InvalidOperationException("media leg is not open");
        var messages = codec.Encode(command);
        await _sendLock.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            foreach (var message in messages)
            {
                var bytes = Encoding.UTF8.GetBytes(message);
                await socket.SendAsync(bytes, WebSocketMessageType.Text, endOfMessage: true, cancellationToken).ConfigureAwait(false);
            }
        }
        catch (Exception ex) when (ex is WebSocketException or ObjectDisposedException or InvalidOperationException)
        {
            ReportDisconnect("send_failed: " + ex.Message);
            throw;
        }
        finally
        {
            _sendLock.Release();
        }
    }

    private async Task ReceiveLoopAsync(ClientWebSocket socket, CancellationToken cancellationToken)
    {
        var buffer = new byte[64 * 1024];
        var message = new MemoryStream();
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                var result = await socket.ReceiveAsync(buffer, cancellationToken).ConfigureAwait(false);
                if (result.MessageType == WebSocketMessageType.Close)
                {
                    ReportDisconnect("closed_by_peer: " + (result.CloseStatus?.ToString() ?? "unknown"));
                    return;
                }

                message.Write(buffer, 0, result.Count);
                if (!result.EndOfMessage)
                {
                    continue;
                }

                var text = Encoding.UTF8.GetString(message.GetBuffer(), 0, (int)message.Length);
                message.SetLength(0);
                var decoded = codec.Decode(text, time.GetTimestamp());
                if (decoded is not null)
                {
                    _events.Writer.TryWrite(decoded);
                }
            }
        }
        catch (OperationCanceledException)
        {
            ReportDisconnect("closed_by_client");
        }
        catch (Exception ex)
        {
            ReportDisconnect("receive_failed: " + ex.Message);
        }
    }

    private void ReportDisconnect(string reason)
    {
        if (_disconnectReported)
        {
            return;
        }

        _disconnectReported = true;
        logger?.LogWarning("media leg disconnected: {Reason}", reason);
        _events.Writer.TryWrite(new DisconnectedEvent(time.GetTimestamp(), reason));
    }
}
