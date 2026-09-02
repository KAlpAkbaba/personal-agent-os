using System.Net;
using System.Threading.Channels;

namespace PagentOS.Companion.Audio.Sideband;

/// <summary>The client's view of Cloud Core's realtime-session API (M12 spec §4, routes.py).</summary>
public interface ISidebandClient
{
    /// <summary><c>POST /v1/voice/realtime/sessions</c> → 201 with the leg payload.</summary>
    Task<RealtimeSessionGrant> CreateSessionAsync(CreateSessionRequest request, CancellationToken cancellationToken);

    /// <summary><c>POST .../{id}/tool-calls</c>; idempotent on <c>call_id</c> server-side.</summary>
    Task<ToolCallRelayResult> RelayToolCallAsync(string sessionId, string callId, string name, string argumentsJson, CancellationToken cancellationToken);

    /// <summary><c>POST .../{id}/events</c> with <c>{"events":[...]}</c> (1..200); the ack carries the sideband backlog.</summary>
    Task<EventsAck> ReportEventsAsync(string sessionId, IReadOnlyList<VoiceClientEventRecord> events, CancellationToken cancellationToken);

    /// <summary><c>POST .../{id}/attach</c> (spec §7). Null when Cloud Core no longer has a live session (404/410).</summary>
    Task<AttachResult?> AttachAsync(string sessionId, string clientKind, string? transport, CancellationToken cancellationToken);
}

public sealed class SidebandException(int statusCode, string endpoint, string body)
    : Exception($"{endpoint} returned {statusCode}: {Truncate(body, 200)}")
{
    public int StatusCode { get; } = statusCode;

    public string Endpoint { get; } = endpoint;

    /// <summary>The response body (bounded), so a 422 can be logged with the server's exact complaint.</summary>
    public string Body { get; } = Truncate(body, 2000);

    /// <summary>5xx and 429/408 are worth retrying with the same idempotency key; 4xx otherwise are not.</summary>
    public bool Transient => StatusCode >= 500 || StatusCode == 429 || StatusCode == 408;

    /// <summary>409: this owner session no longer holds the media leg (another client attached). Stop; re-attach before anything else.</summary>
    public bool IsStaleLeg => StatusCode == (int)HttpStatusCode.Conflict;

    /// <summary>410 (closed/expired) or 404 (unknown): the session is gone; nothing more can be posted to it.</summary>
    public bool IsSessionGone => StatusCode is (int)HttpStatusCode.Gone or (int)HttpStatusCode.NotFound;

    /// <summary>422: Cloud Core refused the payload. That is a BUG in this client (shape, kind, forbidden key, size) — never retried, always logged loudly.</summary>
    public bool IsPayloadRefused => StatusCode == (int)HttpStatusCode.UnprocessableEntity;

    private static string Truncate(string body, int max) => body.Length <= max ? body : body[..max];
}

/// <summary>Where Cloud Core's pushes (say, tool_completed, plan_changed, leg_closed…) arrive from.</summary>
public interface ISidebandPushSource
{
    ChannelReader<SidebandPush> Pushes { get; }
}

/// <summary>No push channel wired; never yields. Used where the pipe is absent (bench, tests that do not need pushes).</summary>
public sealed class NullSidebandPushSource : ISidebandPushSource
{
    public ChannelReader<SidebandPush> Pushes { get; } = Channel.CreateUnbounded<SidebandPush>().Reader;
}

public sealed class FakeSidebandPushSource : ISidebandPushSource
{
    private readonly Channel<SidebandPush> _channel = Channel.CreateUnbounded<SidebandPush>();

    public ChannelReader<SidebandPush> Pushes => _channel.Reader;

    public void Push(string kind, System.Text.Json.Nodes.JsonObject payload, string? sessionId = null)
        => _channel.Writer.TryWrite(new SidebandPush(kind, payload, sessionId));

    public void Push(SidebandPush push) => _channel.Writer.TryWrite(push);
}
