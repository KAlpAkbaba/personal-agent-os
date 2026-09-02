using System.Text.Json.Nodes;

namespace PagentOS.Companion.Audio.Sideband;

/// <summary>
/// What <c>POST /v1/voice/realtime/sessions</c> returns (M12 spec §4.1). The credential is
/// kept as the JSON object Cloud Core sent: its exact shape is the provider adapter's
/// business (track B) and the client only needs the secret value and, optionally, a URL.
/// Never logged, never audited — see <see cref="ToAuditJson"/>.
/// </summary>
public sealed record RealtimeSessionGrant(
    string SessionId,
    string Provider,
    string Transport,
    JsonObject Credential,
    JsonArray? Tools,
    string? Instructions,
    JsonObject? ProviderSessionConfig,
    DateTimeOffset? ExpiresAt)
{
    private static readonly string[] CredentialKeys = { "value", "client_secret", "token", "key", "secret", "ephemeral_key" };

    public static RealtimeSessionGrant Parse(JsonObject body)
    {
        var sessionId = Require(body, "session_id");
        var provider = Require(body, "provider");
        var transport = Require(body, "transport");

        JsonObject credential;
        switch (body["credential"])
        {
            case JsonObject obj:
                credential = (JsonObject)JsonNode.Parse(obj.ToJsonString())!;
                break;
            case JsonValue value when value.TryGetValue<string>(out var secret):
                credential = new JsonObject { ["value"] = secret };
                break;
            default:
                throw new FormatException("session grant has no credential");
        }

        var tools = body["tools"] is JsonArray toolsNode ? (JsonArray)JsonNode.Parse(toolsNode.ToJsonString())! : null;
        var instructions = body["instructions"]?.GetValue<string>();
        var config = body["provider_session_config"] is JsonObject configNode
            ? (JsonObject)JsonNode.Parse(configNode.ToJsonString())!
            : null;
        DateTimeOffset? expires = null;
        if (body["expires_at"]?.GetValue<string>() is { } expiresText && DateTimeOffset.TryParse(expiresText, out var parsed))
        {
            expires = parsed;
        }

        return new RealtimeSessionGrant(sessionId, provider, transport, credential, tools, instructions, config, expires);
    }

    /// <summary>The bearer secret for the provider, whichever key the adapter used.</summary>
    public string? CredentialValue()
    {
        foreach (var key in CredentialKeys)
        {
            switch (Credential[key])
            {
                case JsonValue value when value.TryGetValue<string>(out var text) && !string.IsNullOrWhiteSpace(text):
                    return text;
                case JsonObject nested when nested["value"]?.GetValue<string>() is { Length: > 0 } nestedValue:
                    return nestedValue;
            }
        }

        return null;
    }

    /// <summary>Ids and timings only: the credential is deliberately absent.</summary>
    public JsonObject ToAuditJson() => new()
    {
        ["session_id"] = SessionId,
        ["provider"] = Provider,
        ["transport"] = Transport,
        ["tools"] = Tools?.Count ?? 0,
        ["expires_at"] = ExpiresAt?.ToString("O"),
    };

    private static string Require(JsonObject body, string name)
        => body[name]?.GetValue<string>() is { Length: > 0 } value
            ? value
            : throw new FormatException($"session grant is missing '{name}'");
}

public sealed record CreateSessionRequest(
    string ClientKind,
    string? DeviceId,
    IReadOnlyList<string> TransportPreference,
    string Language = "tr-TR",
    JsonObject? ClientCapabilities = null)
{
    public JsonObject ToJson()
    {
        var json = new JsonObject
        {
            ["client_kind"] = ClientKind,
            ["language"] = Language,
            ["transport_preference"] = new JsonArray(TransportPreference.Select(t => (JsonNode)t).ToArray()),
        };
        if (DeviceId is not null)
        {
            json["device_id"] = DeviceId;
        }

        if (ClientCapabilities is not null)
        {
            json["client_capabilities"] = JsonNode.Parse(ClientCapabilities.ToJsonString());
        }

        return json;
    }
}

/// <summary>What <c>POST .../tool-calls</c> returned for one call (spec §4.3).</summary>
public sealed record ToolCallRelayResult(string CallId, JsonNode? Result, JsonObject? Error, string? Status, string? Preamble)
{
    public bool IsRunning => string.Equals(Status, "running", StringComparison.Ordinal);

    public static ToolCallRelayResult Parse(JsonObject body, string fallbackCallId)
    {
        var callId = body["call_id"]?.GetValue<string>() ?? fallbackCallId;
        var result = body["result"] is { } r ? JsonNode.Parse(r.ToJsonString()) : null;
        var error = body["error"] is JsonObject e ? (JsonObject)JsonNode.Parse(e.ToJsonString())! : null;
        return new ToolCallRelayResult(callId, result, error, body["status"]?.GetValue<string>(), body["preamble"]?.GetValue<string>());
    }

    /// <summary>Turkish stays readable in what the model is handed: "Bakıyorum" rather than the default "Bakıyorum" escape.</summary>
    private static readonly System.Text.Json.JsonSerializerOptions Readable = new()
    {
        Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    /// <summary>The JSON the provider is handed as the function output.</summary>
    public string OutputJson()
    {
        if (IsRunning)
        {
            return new JsonObject { ["status"] = "running", ["preamble"] = Preamble }.ToJsonString(Readable);
        }

        if (Error is not null)
        {
            return new JsonObject { ["error"] = JsonNode.Parse(Error.ToJsonString()) }.ToJsonString(Readable);
        }

        return Result is null ? new JsonObject { ["result"] = null }.ToJsonString(Readable) : Result.ToJsonString(Readable);
    }
}

/// <summary>One client-reported timing/state event (spec §4.5). <c>client_seq</c> makes re-delivery idempotent.</summary>
public sealed record VoiceClientEventRecord(long ClientSeq, string Event, double ClientTsMs, JsonObject Data)
{
    public JsonObject ToJson() => new()
    {
        ["event"] = Event,
        ["client_seq"] = ClientSeq,
        ["client_ts_ms"] = Math.Round(ClientTsMs, 3),
        ["data"] = JsonNode.Parse(Data.ToJsonString()),
    };
}

/// <summary>Kinds Cloud Core may push over the sideband (spec §4.4).</summary>
public static class SidebandPushKinds
{
    public const string PlanChanged = "plan_changed";
    public const string ToolProgress = "tool_progress";
    public const string ToolCompleted = "tool_completed";
    public const string NarrationCursor = "narration_cursor";
    public const string Say = "say";
}

public sealed record SidebandPush(string Kind, JsonObject Payload);

/// <summary>The event names the client reports; the fixed vocabulary from the M12/C brief.</summary>
public static class VoiceClientEvents
{
    public const string MicUplink = "mic_uplink";
    public const string SpeechStarted = "speech_started";
    public const string SpeechEnded = "speech_ended";
    public const string FirstAudio = "first_audio";
    public const string BargeIn = "barge_in";
    public const string PlaybackStopped = "playback_stopped";
    public const string ToolCallRelayed = "tool_call_relayed";
    public const string ToolResultSubmitted = "tool_result_submitted";
    public const string NetworkLost = "network_lost";
    public const string NetworkRestored = "network_restored";

    public static readonly IReadOnlySet<string> All = new HashSet<string>(StringComparer.Ordinal)
    {
        MicUplink, SpeechStarted, SpeechEnded, FirstAudio, BargeIn, PlaybackStopped,
        ToolCallRelayed, ToolResultSubmitted, NetworkLost, NetworkRestored,
    };
}
