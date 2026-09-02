using System.Text.Json.Nodes;
using System.Threading.Channels;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Ipc;

namespace PagentOS.Companion.Audio.Sideband;

/// <summary>
/// The REAL push source (ADR-0039): <c>voice_sideband</c> device-protocol frames the Device
/// Service forwarded over the authenticated named pipe. The companion's pipe loop hands
/// each accepted (fresh, this-connection) frame to <see cref="Accept"/>; the voice
/// orchestrator reads <see cref="Pushes"/>. Bounded: with voice off or the loop busy, the
/// oldest frame is dropped rather than memory growing — Cloud Core keeps its own backlog
/// and the client drains it over HTTP on its next <c>/events</c> ack or <c>attach</c>.
/// </summary>
public sealed class PipeSidebandPushSource : ISidebandPushSource, ISidebandForwardSink
{
    private readonly Channel<SidebandPush> _channel;
    private readonly ILogger? _logger;
    private int _accepted;
    private int _malformed;

    public PipeSidebandPushSource(ILogger? logger = null, int capacity = 256)
    {
        _logger = logger;
        _channel = Channel.CreateBounded<SidebandPush>(new BoundedChannelOptions(capacity)
        {
            FullMode = BoundedChannelFullMode.DropOldest,
            SingleReader = true,
        });
    }

    public ChannelReader<SidebandPush> Pushes => _channel.Reader;

    public int Accepted => _accepted;

    public int Malformed => _malformed;

    public void Accept(JsonObject frame)
    {
        SidebandPush? push;
        try
        {
            push = SidebandPush.TryParseFrame(frame);
        }
        catch (Exception ex)
        {
            push = null;
            _logger?.LogWarning("voice_sideband frame could not be parsed: {Reason}", ex.Message);
        }

        if (push is null)
        {
            Interlocked.Increment(ref _malformed);
            _logger?.LogWarning("voice_sideband frame ignored: not a well-formed frame");
            return;
        }

        Interlocked.Increment(ref _accepted);
        _channel.Writer.TryWrite(push);
    }
}
