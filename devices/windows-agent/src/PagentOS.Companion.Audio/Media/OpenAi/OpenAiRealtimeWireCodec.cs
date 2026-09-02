using System.Text.Json;
using System.Text.Json.Nodes;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Sideband;
using PagentOS.Companion.Audio.Turn;

namespace PagentOS.Companion.Audio.Media.OpenAi;

/// <summary>
/// Pure request/event mapping for the OpenAI Realtime WebSocket protocol — no I/O, in the
/// M4 <c>ProviderRequest</c> tradition, so every mapping is asserted without a network. No
/// model name lives here: the session credential Cloud Core mints is already bound to a
/// model, and where to connect comes from the credential's <c>transport_descriptor</c>
/// (ADR-0038: <c>websocket_url</c> + <c>query</c>), which the adapter fills from data the
/// client never has to know.
/// </summary>
public sealed class OpenAiRealtimeWireCodec : IProviderWireCodec
{
    public const string ProviderName = "openai-realtime";
    public const string DefaultEndpoint = "wss://api.openai.com/v1/realtime";

    public string Provider => ProviderName;

    public AudioFormat AudioFormat => AudioFormat.Pcm16Mono24k;

    public Uri ConnectUri(RealtimeSessionGrant grant)
    {
        var descriptor = grant.TransportDescriptor;
        var url = descriptor?["websocket_url"]?.GetValue<string>();
        var builder = new UriBuilder(string.IsNullOrWhiteSpace(url) ? DefaultEndpoint : url);
        if (descriptor?["query"] is JsonObject query && query.Count > 0)
        {
            var pairs = query
                .Where(p => p.Value is JsonValue)
                .Select(p => Uri.EscapeDataString(p.Key) + "=" + Uri.EscapeDataString(p.Value!.ToString()));
            var extra = string.Join("&", pairs);
            builder.Query = string.IsNullOrEmpty(builder.Query) ? extra : builder.Query.TrimStart('?') + "&" + extra;
        }

        return builder.Uri;
    }

    public IReadOnlyDictionary<string, string> ConnectHeaders(RealtimeSessionGrant grant)
    {
        string secret;
        try
        {
            secret = grant.CredentialSecret();
        }
        catch (Exception ex) when (ex is InvalidOperationException or NullReferenceException or FormatException)
        {
            throw new InvalidOperationException("the session grant carries no provider credential secret", ex);
        }

        return new Dictionary<string, string>
        {
            ["Authorization"] = "Bearer " + secret,
            ["OpenAI-Beta"] = "realtime=v1",
        };
    }

    public IReadOnlyList<string> Encode(ProviderCommand command)
    {
        switch (command)
        {
            case AppendAudioCommand append:
                return One(new JsonObject
                {
                    ["type"] = "input_audio_buffer.append",
                    ["audio"] = Convert.ToBase64String(append.Pcm16),
                });

            case CommitTurnCommand:
                return new[]
                {
                    Serialize(new JsonObject { ["type"] = "input_audio_buffer.commit" }),
                    Serialize(new JsonObject { ["type"] = "response.create" }),
                };

            case CancelResponseCommand:
                return One(new JsonObject { ["type"] = "response.cancel" });

            case ToolResultCommand tool when !tool.FollowUp:
                return new[]
                {
                    Serialize(new JsonObject
                    {
                        ["type"] = "conversation.item.create",
                        ["item"] = new JsonObject
                        {
                            ["type"] = "function_call_output",
                            ["call_id"] = tool.CallId,
                            ["output"] = tool.OutputJson,
                        },
                    }),
                    Serialize(new JsonObject { ["type"] = "response.create" }),
                };

            case ToolResultCommand tool:
                // A function output cannot be amended after it is sent. The completion of a
                // long-running tool therefore lands as a follow-up item that names the call.
                return new[]
                {
                    Serialize(new JsonObject
                    {
                        ["type"] = "conversation.item.create",
                        ["item"] = new JsonObject
                        {
                            ["type"] = "message",
                            ["role"] = "system",
                            ["content"] = new JsonArray(new JsonObject
                            {
                                ["type"] = "input_text",
                                ["text"] = $"Araç tamamlandı (call_id={tool.CallId}). Sonuç: {tool.OutputJson}",
                            }),
                        },
                    }),
                    Serialize(new JsonObject { ["type"] = "response.create" }),
                };

            case SayCommand say:
                return One(new JsonObject
                {
                    ["type"] = "response.create",
                    ["response"] = new JsonObject
                    {
                        ["instructions"] = "Şu cümleyi aynen, başka bir şey eklemeden söyle: " + say.Text,
                    },
                });

            case SessionConfigureCommand configure:
                return One(new JsonObject
                {
                    ["type"] = "session.update",
                    ["session"] = BuildSession(configure),
                });

            default:
                throw new NotSupportedException($"command {command.GetType().Name} has no OpenAI Realtime encoding");
        }
    }

    public ProviderEvent? Decode(string json, long receivedAt)
    {
        JsonObject? node;
        try
        {
            node = JsonNode.Parse(json) as JsonObject;
        }
        catch (JsonException ex)
        {
            return new ProviderErrorEvent(receivedAt, "malformed_event", ex.Message);
        }

        var type = node?["type"]?.GetValue<string>();
        if (node is null || type is null)
        {
            return new ProviderErrorEvent(receivedAt, "malformed_event", "missing type");
        }

        switch (type)
        {
            case "session.created":
            case "session.updated":
                return new SessionReadyEvent(receivedAt, node["session"]?["id"]?.GetValue<string>() ?? string.Empty);

            case "input_audio_buffer.speech_started":
                return new InputSpeechStartedEvent(receivedAt);

            case "input_audio_buffer.speech_stopped":
                return new InputSpeechStoppedEvent(receivedAt);

            case "response.created":
                return new ResponseStartedEvent(receivedAt, node["response"]?["id"]?.GetValue<string>() ?? string.Empty);

            case "response.audio.delta":
            case "response.output_audio.delta":
                var delta = node["delta"]?.GetValue<string>();
                return new AudioDeltaEvent(
                    receivedAt,
                    node["response_id"]?.GetValue<string>() ?? string.Empty,
                    string.IsNullOrEmpty(delta) ? Array.Empty<byte>() : Convert.FromBase64String(delta));

            case "response.done":
                return new ResponseDoneEvent(
                    receivedAt,
                    node["response"]?["id"]?.GetValue<string>() ?? string.Empty,
                    node["response"]?["status"]?.GetValue<string>() ?? "completed");

            case "response.function_call_arguments.done":
                return new ToolCallEvent(
                    receivedAt,
                    node["call_id"]?.GetValue<string>() ?? string.Empty,
                    node["name"]?.GetValue<string>() ?? string.Empty,
                    node["arguments"]?.GetValue<string>() ?? "{}");

            case "conversation.item.input_audio_transcription.delta":
                return new TranscriptDeltaEvent(receivedAt, node["delta"]?.GetValue<string>() ?? string.Empty, Final: false);

            case "conversation.item.input_audio_transcription.completed":
                return new TranscriptDeltaEvent(receivedAt, node["transcript"]?.GetValue<string>() ?? string.Empty, Final: true);

            case "error":
                return new ProviderErrorEvent(
                    receivedAt,
                    node["error"]?["code"]?.GetValue<string>() ?? "error",
                    node["error"]?["message"]?.GetValue<string>() ?? string.Empty);

            default:
                return null;
        }
    }

    private static JsonObject BuildSession(SessionConfigureCommand configure)
    {
        var session = new JsonObject
        {
            ["modalities"] = new JsonArray("audio", "text"),
            ["input_audio_format"] = "pcm16",
            ["output_audio_format"] = "pcm16",
            ["turn_detection"] = configure.EndOfTurn == EndOfTurnMode.Client
                ? null
                : new JsonObject { ["type"] = "semantic_vad" },
        };
        if (configure.Instructions is not null)
        {
            session["instructions"] = configure.Instructions;
        }

        if (configure.Tools is not null)
        {
            session["tools"] = JsonNode.Parse(configure.Tools.ToJsonString());
        }

        if (configure.ProviderSessionConfig is not null)
        {
            foreach (var pair in configure.ProviderSessionConfig)
            {
                session[pair.Key] = pair.Value is null ? null : JsonNode.Parse(pair.Value.ToJsonString());
            }
        }

        return session;
    }

    private static string[] One(JsonObject node) => new[] { Serialize(node) };

    private static string Serialize(JsonObject node) => node.ToJsonString();
}
