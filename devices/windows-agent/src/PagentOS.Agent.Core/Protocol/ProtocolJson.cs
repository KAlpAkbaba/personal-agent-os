using System.Text.Json;
using System.Text.Json.Serialization;

namespace PagentOS.Agent.Core.Protocol;

/// <summary>Thrown when a frame parses as JSON but violates the protocol schema.</summary>
public sealed class ProtocolValidationException(string message) : Exception(message);

public static class ProtocolJson
{
    public static readonly JsonSerializerOptions Options = new()
    {
        AllowOutOfOrderMetadataProperties = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.Never,
        ReadCommentHandling = JsonCommentHandling.Disallow,
    };

    /// <summary>
    /// Parses one protocol frame. Throws <see cref="JsonException"/> (bad JSON / missing required
    /// fields), <see cref="NotSupportedException"/> (unknown "type" discriminator) or
    /// <see cref="ProtocolValidationException"/> (null frame).
    /// </summary>
    public static ProtocolMessage Deserialize(string json)
    {
        return JsonSerializer.Deserialize<ProtocolMessage>(json, Options)
               ?? throw new ProtocolValidationException("frame deserialized to null");
    }

    public static string Serialize(ProtocolMessage message)
    {
        return JsonSerializer.Serialize(message, Options);
    }
}
