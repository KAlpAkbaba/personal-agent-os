using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace PagentOS.Agent.Core.Protocol;

/// <summary>
/// Device protocol v1 message set. Field names and the "type" discriminator follow
/// packages/schemas/device-protocol.schema.json exactly (snake_case).
/// </summary>
[JsonPolymorphic(TypeDiscriminatorPropertyName = "type", UnknownDerivedTypeHandling = JsonUnknownDerivedTypeHandling.FailSerialization)]
[JsonDerivedType(typeof(HelloMessage), "hello")]
[JsonDerivedType(typeof(ChallengeMessage), "challenge")]
[JsonDerivedType(typeof(AuthMessage), "auth")]
[JsonDerivedType(typeof(WelcomeMessage), "welcome")]
[JsonDerivedType(typeof(HeartbeatMessage), "heartbeat")]
[JsonDerivedType(typeof(HeartbeatAckMessage), "heartbeat_ack")]
[JsonDerivedType(typeof(CommandMessage), "command")]
[JsonDerivedType(typeof(CommandAckMessage), "command_ack")]
[JsonDerivedType(typeof(CancelMessage), "cancel")]
[JsonDerivedType(typeof(ErrorMessage), "error")]
[JsonDerivedType(typeof(VoiceSidebandMessage), VoiceSideband.FrameType)]
public abstract record ProtocolMessage;

public sealed record HelloMessage : ProtocolMessage
{
    [JsonPropertyName("protocol_version")]
    public required int ProtocolVersion { get; init; }

    [JsonPropertyName("device_id")]
    public required string DeviceId { get; init; }

    [JsonPropertyName("software_version")]
    public required string SoftwareVersion { get; init; }

    [JsonPropertyName("capabilities")]
    public required IReadOnlyList<string> Capabilities { get; init; }
}

public sealed record ChallengeMessage : ProtocolMessage
{
    /// <summary>Base64-encoded random nonce bytes.</summary>
    [JsonPropertyName("nonce")]
    public required string Nonce { get; init; }
}

public sealed record AuthMessage : ProtocolMessage
{
    /// <summary>Base64 ECDSA-SHA256 signature over nonce_bytes || device_id_utf8.</summary>
    [JsonPropertyName("signature")]
    public required string Signature { get; init; }
}

public sealed record WelcomeMessage : ProtocolMessage
{
    [JsonPropertyName("session_id")]
    public required string SessionId { get; init; }

    [JsonPropertyName("heartbeat_interval_s")]
    public required double HeartbeatIntervalS { get; init; }
}

public sealed record HeartbeatMessage : ProtocolMessage
{
    [JsonPropertyName("seq")]
    public required long Seq { get; init; }

    /// <summary>
    /// M18.3, additive and optional (DEVICE_PROTOCOL.md §6g): what the owner-session companion
    /// currently sees — idle time, display state, alarm state. Absent when there is no companion,
    /// when it did not answer inside <see cref="HeartbeatStatus.MaxWait"/>, or when this device
    /// is older than the field. Its keys are exactly <see cref="HeartbeatStatus.Fields"/>; the
    /// service projects onto that list before sending, so a newer companion cannot put a key
    /// here that the broker's schema would refuse.
    /// </summary>
    [JsonPropertyName("status")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public JsonObject? Status { get; init; }
}

public sealed record HeartbeatAckMessage : ProtocolMessage
{
    [JsonPropertyName("seq")]
    public required long Seq { get; init; }
}

public sealed record CommandEnvelope
{
    [JsonPropertyName("command_id")]
    public required string CommandId { get; init; }

    [JsonPropertyName("idempotency_key")]
    public required string IdempotencyKey { get; init; }

    [JsonPropertyName("capability")]
    public required string Capability { get; init; }

    [JsonPropertyName("payload")]
    public required JsonObject Payload { get; init; }

    [JsonPropertyName("expires_at")]
    public required DateTimeOffset ExpiresAt { get; init; }

    [JsonPropertyName("trace_id")]
    public required string TraceId { get; init; }
}

public sealed record CommandMessage : ProtocolMessage
{
    [JsonPropertyName("command")]
    public required CommandEnvelope Command { get; init; }
}

public sealed record CommandAckMessage : ProtocolMessage
{
    [JsonPropertyName("command_id")]
    public required string CommandId { get; init; }

    /// <summary>One of <see cref="AckStatus"/>: accepted | running | succeeded | failed.</summary>
    [JsonPropertyName("status")]
    public required string Status { get; init; }

    [JsonPropertyName("result")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public JsonObject? Result { get; init; }

    [JsonPropertyName("error")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public ErrorObject? Error { get; init; }
}

public sealed record CancelMessage : ProtocolMessage
{
    [JsonPropertyName("command_id")]
    public required string CommandId { get; init; }
}

public sealed record ErrorMessage : ProtocolMessage
{
    [JsonPropertyName("command_id")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? CommandId { get; init; }

    [JsonPropertyName("error")]
    public required ErrorObject Error { get; init; }
}

public sealed record ErrorObject
{
    [JsonPropertyName("class")]
    public required string Class { get; init; }

    [JsonPropertyName("message")]
    public required string Message { get; init; }

    [JsonPropertyName("retryable")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public bool? Retryable { get; init; }
}
