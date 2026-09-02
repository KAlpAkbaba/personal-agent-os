using System.Threading.Channels;

namespace PagentOS.Companion.Audio.Sideband;

/// <summary>The client's view of Cloud Core's realtime-session API (M12 spec §4).</summary>
public interface ISidebandClient
{
    Task<RealtimeSessionGrant> CreateSessionAsync(CreateSessionRequest request, CancellationToken cancellationToken);

    Task<ToolCallRelayResult> RelayToolCallAsync(string sessionId, string callId, string name, string argumentsJson, CancellationToken cancellationToken);

    Task ReportEventAsync(string sessionId, VoiceClientEventRecord record, CancellationToken cancellationToken);

    /// <summary>Re-attach after a network loss (spec §7). Null when Cloud Core no longer knows the session.</summary>
    Task<RealtimeSessionGrant?> AttachAsync(string sessionId, CancellationToken cancellationToken);
}

public sealed class SidebandException(int statusCode, string endpoint, string body)
    : Exception($"{endpoint} returned {statusCode}: {Truncate(body)}")
{
    public int StatusCode { get; } = statusCode;

    public string Endpoint { get; } = endpoint;

    /// <summary>5xx and 429 are worth retrying with the same idempotency key; 4xx otherwise are not.</summary>
    public bool Transient => StatusCode >= 500 || StatusCode == 429 || StatusCode == 408;

    private static string Truncate(string body) => body.Length <= 200 ? body : body[..200];
}

/// <summary>Where Cloud Core's pushes (say, tool_completed, plan_changed…) arrive from.</summary>
public interface ISidebandPushSource
{
    ChannelReader<SidebandPush> Pushes { get; }
}

/// <summary>No push channel wired (the authenticated WS surface is not part of track C); never yields.</summary>
public sealed class NullSidebandPushSource : ISidebandPushSource
{
    public ChannelReader<SidebandPush> Pushes { get; } = Channel.CreateUnbounded<SidebandPush>().Reader;
}

public sealed class FakeSidebandPushSource : ISidebandPushSource
{
    private readonly Channel<SidebandPush> _channel = Channel.CreateUnbounded<SidebandPush>();

    public ChannelReader<SidebandPush> Pushes => _channel.Reader;

    public void Push(string kind, System.Text.Json.Nodes.JsonObject payload) => _channel.Writer.TryWrite(new SidebandPush(kind, payload));
}
