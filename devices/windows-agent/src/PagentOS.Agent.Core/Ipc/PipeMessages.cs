using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.Agent.Core.Ipc;

/// <summary>
/// Internal service ↔ companion IPC frames (newline-delimited JSON over the local named pipe).
/// This is not part of the device protocol; it never leaves the machine.
/// </summary>
[JsonPolymorphic(TypeDiscriminatorPropertyName = "type", UnknownDerivedTypeHandling = JsonUnknownDerivedTypeHandling.FailSerialization)]
[JsonDerivedType(typeof(CompanionHello), "companion_hello")]
[JsonDerivedType(typeof(ExecRequest), "exec_request")]
[JsonDerivedType(typeof(ExecResponse), "exec_response")]
public abstract record PipeMessage;

public sealed record CompanionHello : PipeMessage
{
    [JsonPropertyName("capabilities")]
    public required IReadOnlyList<string> Capabilities { get; init; }
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
}

public sealed record ExecResponse : PipeMessage
{
    [JsonPropertyName("request_id")]
    public required string RequestId { get; init; }

    [JsonPropertyName("ok")]
    public required bool Ok { get; init; }

    [JsonPropertyName("result")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public JsonObject? Result { get; init; }

    [JsonPropertyName("error")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public ErrorObject? Error { get; init; }
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
