using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.Agent.Core.Ipc;

/// <summary>
/// Internal service ↔ companion IPC frames (newline-delimited JSON over the local named pipe).
/// This is not part of the device protocol; it never leaves the machine.
///
/// Frames carry <c>conn_id</c> and <c>seq</c> from protocol version 2 onward. They are not
/// authentication — the peer's identity comes from the kernel, never from a frame — they are
/// freshness: they bind a frame to the connection it was written on and to a position in
/// that connection, so a frame from a retired connection or a frame seen twice is refused
/// (<see cref="IpcChannelGuard"/>).
/// </summary>
[JsonPolymorphic(TypeDiscriminatorPropertyName = "type", UnknownDerivedTypeHandling = JsonUnknownDerivedTypeHandling.FailSerialization)]
[JsonDerivedType(typeof(ServiceChallenge), "service_challenge")]
[JsonDerivedType(typeof(CompanionHello), "companion_hello")]
[JsonDerivedType(typeof(ExecRequest), "exec_request")]
[JsonDerivedType(typeof(ExecResponse), "exec_response")]
[JsonDerivedType(typeof(SidebandForward), "voice_sideband")]
public abstract record PipeMessage;

public static class IpcProtocol
{
    /// <summary>v1: unauthenticated hello. v2: challenge + connection binding + sequencing.</summary>
    public const int Version = 2;
}

/// <summary>
/// First frame on every accepted connection, written by the service once the peer has
/// passed the OS-level identity checks. Announces the connection id the companion must
/// echo and use on every later frame.
/// </summary>
public sealed record ServiceChallenge : PipeMessage
{
    [JsonPropertyName("conn_id")]
    public required string ConnectionId { get; init; }

    [JsonPropertyName("nonce")]
    public required string Nonce { get; init; }

    [JsonPropertyName("protocol_version")]
    public int ProtocolVersion { get; init; } = IpcProtocol.Version;
}

public sealed record CompanionHello : PipeMessage
{
    [JsonPropertyName("capabilities")]
    public required IReadOnlyList<string> Capabilities { get; init; }

    /// <summary>Echo of the challenge's connection id.</summary>
    [JsonPropertyName("conn_id")]
    public string? ConnectionId { get; init; }

    /// <summary>Echo of the challenge nonce, proving this hello answers *this* challenge.</summary>
    [JsonPropertyName("nonce")]
    public string? Nonce { get; init; }

    [JsonPropertyName("seq")]
    public long Seq { get; init; }

    [JsonPropertyName("protocol_version")]
    public int ProtocolVersion { get; init; } = IpcProtocol.Version;
}

public sealed record ExecRequest : PipeMessage
{
    [JsonPropertyName("request_id")]
    public required string RequestId { get; init; }

    [JsonPropertyName("capability")]
    public required string Capability { get; init; }

    [JsonPropertyName("payload")]
    public required JsonObject Payload { get; init; }

    [JsonPropertyName("timeout_ms")]
    public required int TimeoutMs { get; init; }

    /// <summary>
    /// The absolute moment (Unix ms, the shared machine clock) at which the service gives up
    /// waiting for this request. Optional (0 = absent, older service): the companion then
    /// budgets from <see cref="TimeoutMs"/> alone. With it, the companion's budget is what
    /// is LEFT, so the pipe-in latency and its own processing are no longer charged against
    /// the headroom that keeps its typed timeout ahead of the service's (CI race, 2026-09-04).
    /// </summary>
    [JsonPropertyName("deadline_utc_ms")]
    public long DeadlineUtcMs { get; init; }

    [JsonPropertyName("conn_id")]
    public string? ConnectionId { get; init; }

    [JsonPropertyName("seq")]
    public long Seq { get; init; }
}

public sealed record ExecResponse : PipeMessage
{
    [JsonPropertyName("request_id")]
    public required string RequestId { get; init; }

    [JsonPropertyName("conn_id")]
    public string? ConnectionId { get; init; }

    [JsonPropertyName("seq")]
    public long Seq { get; init; }

    [JsonPropertyName("ok")]
    public required bool Ok { get; init; }

    [JsonPropertyName("result")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public JsonObject? Result { get; init; }

    [JsonPropertyName("error")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public ErrorObject? Error { get; init; }
}

/// <summary>
/// Service → companion, one way, no response (M12, ADR-0039): a <c>voice_sideband</c>
/// device-protocol frame forwarded OPAQUELY. <c>frame</c> is the broker's frame verbatim;
/// the service has validated its envelope and its ≤ 16 KiB bound and nothing else. The
/// same connection id and sequence every pipe frame carries apply, so a replayed or
/// stale forward is refused by the companion exactly like a replayed exec_request.
/// Grants the companion nothing: it can only receive this; it never answers it.
/// </summary>
public sealed record SidebandForward : PipeMessage
{
    [JsonPropertyName("conn_id")]
    public string? ConnectionId { get; init; }

    [JsonPropertyName("seq")]
    public long Seq { get; init; }

    [JsonPropertyName("frame")]
    public required JsonObject Frame { get; init; }
}

/// <summary>Companion side: where an accepted <see cref="SidebandForward"/> frame is delivered.</summary>
public interface ISidebandForwardSink
{
    /// <summary>Must not throw; a bad frame is the sink's to count and drop.</summary>
    void Accept(JsonObject frame);
}

public static class PipeJson
{
    public static readonly JsonSerializerOptions Options = new()
    {
        AllowOutOfOrderMetadataProperties = true,
    };

    public static PipeMessage Deserialize(string json)
        => JsonSerializer.Deserialize<PipeMessage>(json, Options)
           ?? throw new ProtocolValidationException("pipe frame deserialized to null");

    public static string Serialize(PipeMessage message) => JsonSerializer.Serialize(message, Options);
}
