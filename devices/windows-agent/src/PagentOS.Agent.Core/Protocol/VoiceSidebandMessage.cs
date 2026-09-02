using System.Text;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace PagentOS.Agent.Core.Protocol;

/// <summary>
/// Broker → agent <c>voice_sideband</c> frame (M12, ADR-0039), additive to protocol v1:
/// Cloud Core's realtime-voice sideband push for the owner-session companion, carried on
/// the same outbound-only device connection as everything else.
///
/// The Device Service treats it as OPAQUE: it validates the envelope and the size bound,
/// forwards it over the authenticated local IPC, and never reads the payload, never acts
/// on it, never acknowledges it. Nothing from the command path applies — no ack, no
/// idempotency key, no expiry, no audit row per frame — so adding this type changes no
/// existing command/ack semantics. Field names follow
/// packages/schemas/device-protocol.schema.json <c>$defs/voice_sideband</c> exactly.
/// </summary>
public sealed record VoiceSidebandMessage : ProtocolMessage
{
    [JsonPropertyName("session_id")]
    public required string SessionId { get; init; }

    [JsonPropertyName("event")]
    public required string Event { get; init; }

    [JsonPropertyName("payload")]
    public required JsonObject Payload { get; init; }

    [JsonPropertyName("at")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? At { get; init; }

    /// <summary>The frame as the broker sent it, for opaque forwarding (a fresh object, never shared).</summary>
    public JsonObject ToFrame()
        => (JsonObject)JsonNode.Parse(ProtocolJson.Serialize(this))!;

    /// <summary>Bytes on the wire, UTF-8 JSON — what the IPC bound is measured against.</summary>
    public int SerializedBytes() => Encoding.UTF8.GetByteCount(ProtocolJson.Serialize(this));
}

public static class VoiceSideband
{
    public const string FrameType = "voice_sideband";

    /// <summary>Serialized bound the Device Service enforces before forwarding over the pipe (schema: 16 KiB).</summary>
    public const int MaxFrameBytes = 16 * 1024;

    public const int MaxEventLength = 64;
}

/// <summary>
/// Where an accepted <c>voice_sideband</c> frame goes. The Device Service supplies the
/// pipe forwarder; with no sink the connection logs the frame and drops it, which is the
/// exact behaviour of a build that does not know the frame plus one log line.
/// </summary>
public interface ISidebandFrameSink
{
    /// <summary>True when the frame was handed on; false when dropped (no companion, oversize). Must not throw.</summary>
    ValueTask<bool> ForwardAsync(VoiceSidebandMessage frame, CancellationToken cancellationToken);
}
