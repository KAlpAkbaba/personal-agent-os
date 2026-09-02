using System.Text;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace PagentOS.Companion.Audio.Sideband;

/// <summary>
/// The realtime-session contract as Cloud Core defines it (ADR-0039): request/response
/// shapes, event kinds, limits and the forbidden-key rule are copied from
/// <c>services/api/app/voice/realtime_sessions/routes.py</c> and <c>service.py</c>, the single
/// source of truth. Nothing here is a guess; a mismatch with the server is a bug in this
/// file and the in-process fake (<c>InProcessFakeCloudCore</c>) enforces the same rules so
/// the mismatch is caught offline.
/// </summary>
public static partial class RealtimeContract
{
    public const string SessionsPath = "/v1/voice/realtime/sessions";

    /// <summary><c>EventsRequest.events</c>: 1..200 per request.</summary>
    public const int MaxEventsPerRequest = 200;

    /// <summary><c>MAX_EVENT_PAYLOAD_BYTES</c>: an event payload, JSON-encoded.</summary>
    public const int MaxEventPayloadBytes = 4 * 1024;

    /// <summary><c>MAX_ARGUMENTS_BYTES</c>: tool-call arguments / results, JSON-encoded.</summary>
    public const int MaxArgumentsBytes = 16 * 1024;

    /// <summary><c>ClientEvent.text</c> max length.</summary>
    public const int MaxEventTextChars = 4000;

    public const long MaxTMs = 1_000_000_000_000;

    public const int MaxTurn = 1_000_000;

    /// <summary><c>TRANSPORTS</c> in providers.py.</summary>
    public static readonly IReadOnlyList<string> Transports = new[] { "webrtc", "websocket", "simulated" };

    /// <summary>
    /// <c>FORBIDDEN_KEY_PARTS</c>: a key containing any of these under ANY spelling (case and
    /// separators dropped before matching) is refused by the server with 422 — <c>apiKey</c>,
    /// <c>api-key</c> and <c>API_KEY</c> are all <c>apikey</c>. Note that <c>text</c> catches
    /// <c>context</c> and <c>next_item</c> too; that is the server's rule, mirrored, not softened.
    /// </summary>
    public static readonly IReadOnlyList<string> ForbiddenKeyParts = new[]
    {
        "audio", "pcm", "wave", "secret", "credential", "token", "apikey", "password", "text", "transcript",
    };

    [GeneratedRegex("^[a-z][a-z0-9_]{0,15}$")]
    public static partial Regex ClientKindRegex();

    [GeneratedRegex("^[A-Za-z0-9_.:-]+$")]
    public static partial Regex CallIdRegex();

    [GeneratedRegex("^[a-z][a-z0-9_.]*$")]
    public static partial Regex ToolNameRegex();

    [GeneratedRegex("[^a-z0-9]+")]
    private static partial Regex KeyNormalizer();

    /// <summary>Mirrors <c>service.is_forbidden_key</c> exactly.</summary>
    public static bool IsForbiddenKey(string key)
    {
        var normalized = KeyNormalizer().Replace(key.ToLowerInvariant(), string.Empty);
        foreach (var part in ForbiddenKeyParts)
        {
            if (normalized.Contains(part, StringComparison.Ordinal))
            {
                return true;
            }
        }

        return false;
    }

    /// <summary>The first forbidden key anywhere in the value (objects and arrays, recursively), or null.</summary>
    public static string? FindForbiddenKey(JsonNode? value)
    {
        switch (value)
        {
            case JsonObject obj:
                foreach (var pair in obj)
                {
                    if (IsForbiddenKey(pair.Key))
                    {
                        return pair.Key;
                    }

                    var nested = FindForbiddenKey(pair.Value);
                    if (nested is not null)
                    {
                        return nested;
                    }
                }

                return null;
            case JsonArray array:
                foreach (var item in array)
                {
                    var nested = FindForbiddenKey(item);
                    if (nested is not null)
                    {
                        return nested;
                    }
                }

                return null;
            default:
                return null;
        }
    }

    public static int EncodedBytes(JsonNode value) => Encoding.UTF8.GetByteCount(value.ToJsonString());
}

/// <summary>
/// What <c>POST /v1/voice/realtime/sessions</c> and <c>.../attach</c> return (spec §4.1,
/// <c>service._leg_payload</c>). The credential is exactly <c>EphemeralCredential.to_client_dict()</c>:
/// <c>{provider, secret, expires_at, transport, session_ref[, transport_descriptor]}</c>.
/// Never logged, never audited — see <see cref="ToAuditJson"/>.
/// </summary>
public sealed record RealtimeSessionGrant(
    string SessionId,
    string Provider,
    string Transport,
    JsonObject Credential,
    JsonArray? Tools,
    string? Instructions,
    string Language,
    DateTimeOffset? ExpiresAt,
    string? State)
{
    public static RealtimeSessionGrant Parse(JsonObject body)
    {
        var sessionId = Require(body, "session_id");
        var provider = Require(body, "provider");
        var transport = Require(body, "transport");

        if (body["credential"] is not JsonObject credentialNode)
        {
            throw new FormatException("session grant has no credential object");
        }

        var credential = (JsonObject)JsonNode.Parse(credentialNode.ToJsonString())!;
        if (credential["secret"]?.GetValue<string>() is not { Length: > 0 })
        {
            throw new FormatException("session grant credential has no 'secret'");
        }

        var tools = body["tools"] is JsonArray toolsNode ? (JsonArray)JsonNode.Parse(toolsNode.ToJsonString())! : null;
        var instructions = body["instructions"]?.GetValue<string>();
        var language = body["language"]?.GetValue<string>() ?? "tr-TR";
        DateTimeOffset? expires = null;
        if (body["expires_at"]?.GetValue<string>() is { } expiresText && DateTimeOffset.TryParse(expiresText, out var parsed))
        {
            expires = parsed;
        }

        // create: "state" is the row state string; attach: "state" is the continuity object (which carries its own "state").
        var state = body["state"] switch
        {
            JsonValue value when value.TryGetValue<string>(out var text) => text,
            JsonObject continuity => continuity["state"]?.GetValue<string>(),
            _ => null,
        };
        return new RealtimeSessionGrant(sessionId, provider, transport, credential, tools, instructions, language, expires, state);
    }

    /// <summary>The per-session provider secret: <c>credential.secret</c>, the one documented key.</summary>
    public string CredentialSecret() => Credential["secret"]!.GetValue<string>();

    /// <summary>The adapter's NON-secret media-leg contract (ADR-0038); absent for the simulator.</summary>
    public JsonObject? TransportDescriptor => Credential["transport_descriptor"] as JsonObject;

    public DateTimeOffset? CredentialExpiresAt
        => Credential["expires_at"]?.GetValue<string>() is { } text && DateTimeOffset.TryParse(text, out var parsed) ? parsed : null;

    /// <summary>Ids and timings only: the credential is deliberately absent.</summary>
    public JsonObject ToAuditJson() => new()
    {
        ["session_id"] = SessionId,
        ["provider"] = Provider,
        ["transport"] = Transport,
        ["tools"] = Tools?.Count ?? 0,
        ["expires_at"] = ExpiresAt?.ToString("O"),
        ["credential_expires_at"] = CredentialExpiresAt?.ToString("O"),
    };

    private static string Require(JsonObject body, string name)
        => body[name]?.GetValue<string>() is { Length: > 0 } value
            ? value
            : throw new FormatException($"session grant is missing '{name}'");
}

/// <summary><c>CreateSessionRequest</c> in routes.py (extra fields are refused with 422).</summary>
public sealed record CreateSessionRequest(string ClientKind, string? Transport, string Language = "tr-TR")
{
    public JsonObject ToJson()
    {
        if (!RealtimeContract.ClientKindRegex().IsMatch(ClientKind))
        {
            throw new ArgumentException($"client_kind '{ClientKind}' does not match the server pattern ^[a-z][a-z0-9_]{{0,15}}$", nameof(ClientKind));
        }

        if (Transport is not null && !RealtimeContract.Transports.Contains(Transport))
        {
            throw new ArgumentException($"transport '{Transport}' is not one of {string.Join("/", RealtimeContract.Transports)}", nameof(Transport));
        }

        var json = new JsonObject
        {
            ["client_kind"] = ClientKind,
            ["language"] = Language,
        };
        if (Transport is not null)
        {
            json["transport"] = Transport;
        }

        return json;
    }
}

/// <summary>What <c>POST .../attach</c> returned (spec §7): the new leg's grant, the continuity state and the sideband backlog.</summary>
public sealed record AttachResult(RealtimeSessionGrant Grant, JsonObject? State, IReadOnlyList<SidebandPush> PendingSideband, JsonObject? PreviousLeg)
{
    public static AttachResult Parse(JsonObject body)
    {
        var grant = RealtimeSessionGrant.Parse(body);
        var state = body["state"] is JsonObject s ? (JsonObject)JsonNode.Parse(s.ToJsonString())! : null;
        var previous = body["previous_leg"] is JsonObject p ? (JsonObject)JsonNode.Parse(p.ToJsonString())! : null;
        return new AttachResult(grant, state, SidebandPush.ParseFrames(body["pending_sideband"] as JsonArray), previous);
    }
}

/// <summary>What <c>POST .../tool-calls</c> returned for one call (<c>service._tool_row_payload</c>).</summary>
public sealed record ToolCallRelayResult(
    string CallId,
    string? Name,
    string Status,
    bool LongRunning,
    bool Replayed,
    JsonNode? Result,
    JsonObject? Error,
    string? Preamble)
{
    public const string StatusRunning = "running";
    public const string StatusSucceeded = "succeeded";
    public const string StatusFailed = "failed";

    public bool IsRunning => string.Equals(Status, StatusRunning, StringComparison.Ordinal);

    public static ToolCallRelayResult Parse(JsonObject body, string fallbackCallId)
    {
        var callId = body["call_id"]?.GetValue<string>() ?? fallbackCallId;
        var result = body["result"] is { } r ? JsonNode.Parse(r.ToJsonString()) : null;
        var error = body["error"] is JsonObject e ? (JsonObject)JsonNode.Parse(e.ToJsonString())! : null;
        var status = body["status"]?.GetValue<string>() ?? (error is not null ? StatusFailed : StatusSucceeded);
        return new ToolCallRelayResult(
            callId,
            body["name"]?.GetValue<string>(),
            status,
            body["long_running"]?.GetValue<bool>() ?? false,
            body["replayed"]?.GetValue<bool>() ?? false,
            result,
            error,
            body["preamble"]?.GetValue<string>());
    }

    /// <summary>A failure produced on the CLIENT (relay refused, retries exhausted): the provider is told, the conversation goes on.</summary>
    public static ToolCallRelayResult Failed(string callId, string name, string errorClass, string message) => new(
        callId, name, StatusFailed, LongRunning: false, Replayed: false, Result: null,
        Error: new JsonObject { ["error_class"] = errorClass, ["message"] = message }, Preamble: null);

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

/// <summary>
/// One <c>ClientEvent</c> (routes.py): <c>{kind, t_ms, turn, payload[, text]}</c>. <c>ClientSeq</c>
/// is client-side bookkeeping for ordering and re-delivery and is NOT serialized — the server
/// model forbids extra fields.
/// </summary>
public sealed record VoiceClientEventRecord(long ClientSeq, string Kind, long TMs, int Turn, JsonObject Payload, string? Text = null)
{
    public JsonObject ToJson()
    {
        var json = new JsonObject
        {
            ["kind"] = Kind,
            ["t_ms"] = TMs,
            ["turn"] = Turn,
            ["payload"] = JsonNode.Parse(Payload.ToJsonString()),
        };
        if (Text is not null)
        {
            json["text"] = Text;
        }

        return json;
    }

    /// <summary><c>EventsRequest</c>: <c>{"events":[...]}</c>, the only body shape the server accepts.</summary>
    public static JsonObject BatchJson(IEnumerable<VoiceClientEventRecord> records)
        => new() { ["events"] = new JsonArray(records.Select(r => (JsonNode)r.ToJson()).ToArray()) };
}

/// <summary>What <c>POST .../events</c> returned (<c>service.record_client_events</c>).</summary>
public sealed record EventsAck(int Accepted, JsonArray ResolvedIntents, IReadOnlyList<SidebandPush> PendingSideband, JsonObject? State)
{
    public static EventsAck Parse(JsonObject body) => new(
        body["accepted"]?.GetValue<int>() ?? 0,
        body["resolved_intents"] is JsonArray intents ? (JsonArray)JsonNode.Parse(intents.ToJsonString())! : new JsonArray(),
        SidebandPush.ParseFrames(body["pending_sideband"] as JsonArray),
        body["state"] is JsonObject state ? (JsonObject)JsonNode.Parse(state.ToJsonString())! : null);
}

/// <summary>Events Cloud Core may push over the sideband (<c>sideband.SIDEBAND_EVENTS</c>; the device-protocol <c>voice_sideband</c> enum).</summary>
public static class SidebandPushKinds
{
    public const string PlanChanged = "plan_changed";
    public const string ToolProgress = "tool_progress";
    public const string ToolCompleted = "tool_completed";
    public const string NarrationCursor = "narration_cursor";
    public const string Say = "say";
    public const string LegClosed = "leg_closed";

    public static readonly IReadOnlySet<string> All = new HashSet<string>(StringComparer.Ordinal)
    {
        PlanChanged, ToolProgress, ToolCompleted, NarrationCursor, Say, LegClosed,
    };
}

/// <summary>
/// One sideband push, whichever way it arrived: the device-protocol <c>voice_sideband</c>
/// frame over the pipe, or the <c>pending_sideband</c> backlog in an <c>/events</c> or
/// <c>attach</c> response. Both are the same frame shape:
/// <c>{type:"voice_sideband", session_id, event, payload, at}</c>.
/// </summary>
public sealed record SidebandPush(string Kind, JsonObject Payload, string? SessionId = null)
{
    public const string FrameType = "voice_sideband";

    /// <summary>Null when the object is not a well-formed voice_sideband frame.</summary>
    public static SidebandPush? TryParseFrame(JsonObject frame)
    {
        if (frame["type"]?.GetValue<string>() != FrameType)
        {
            return null;
        }

        var kind = frame["event"]?.GetValue<string>();
        if (string.IsNullOrEmpty(kind) || frame["payload"] is not JsonObject payload)
        {
            return null;
        }

        return new SidebandPush(kind, (JsonObject)JsonNode.Parse(payload.ToJsonString())!, frame["session_id"]?.GetValue<string>());
    }

    public static IReadOnlyList<SidebandPush> ParseFrames(JsonArray? frames)
    {
        if (frames is null)
        {
            return Array.Empty<SidebandPush>();
        }

        var pushes = new List<SidebandPush>();
        foreach (var node in frames)
        {
            if (node is JsonObject frame && TryParseFrame(frame) is { } push)
            {
                pushes.Add(push);
            }
        }

        return pushes;
    }
}

/// <summary>
/// The event kinds Cloud Core accepts at <c>POST .../events</c>: <c>CLIENT_EVENT_KINDS =
/// TIMING_EVENT_KINDS + STATE_EVENT_KINDS</c> (realtime_bench.py / service.py). Anything
/// else is a 422.
/// </summary>
public static class VoiceClientEvents
{
    // TIMING_EVENT_KINDS — the benchmark pairs (spec §8)
    public const string MicSpeechStart = "mic_speech_start";
    public const string UplinkFirstPacket = "uplink_first_packet";
    public const string EndOfTurn = "end_of_turn";
    public const string FirstAudio = "first_audio";
    public const string BargeInStart = "barge_in_start";
    public const string PlaybackStopped = "playback_stopped";
    public const string ToolCall = "tool_call";
    public const string PreambleAudioStart = "preamble_audio_start";
    public const string ToolDone = "tool_done";
    public const string SpeechResumed = "speech_resumed";
    public const string AudioFrame = "audio_frame";
    public const string ResponseDone = "response_done";
    public const string NetworkLost = "network_lost";
    public const string NetworkRestored = "network_restored";

    // STATE_EVENT_KINDS — session record / audit / intents
    public const string Utterance = "utterance";
    public const string Summary = "summary";
    public const string Intent = "intent";
    public const string State = "state";
    public const string Error = "error";

    public static readonly IReadOnlySet<string> Timing = new HashSet<string>(StringComparer.Ordinal)
    {
        MicSpeechStart, UplinkFirstPacket, EndOfTurn, FirstAudio, BargeInStart, PlaybackStopped,
        ToolCall, PreambleAudioStart, ToolDone, SpeechResumed, AudioFrame, ResponseDone,
        NetworkLost, NetworkRestored,
    };

    public static readonly IReadOnlySet<string> StateKinds = new HashSet<string>(StringComparer.Ordinal)
    {
        Utterance, Summary, Intent, State, Error,
    };

    public static readonly IReadOnlySet<string> All = new HashSet<string>(Timing.Concat(StateKinds), StringComparer.Ordinal);
}
