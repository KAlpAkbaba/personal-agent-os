using System.Collections.Concurrent;
using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json.Nodes;
using PagentOS.Companion.Audio.Sideband;

namespace PagentOS.Companion.Audio.Testing;

public sealed record FakeToolBehaviour(bool LongRunning, string? Preamble, Func<JsonNode?, JsonNode?> Result);

/// <summary>
/// The Cloud Core side of the M12 §4 contract as an <see cref="HttpMessageHandler"/>: no
/// sockets, no ports, so the whole sideband path runs in-process. It enforces the bearer
/// token, mints a fake grant, dispatches tool calls with idempotency on <c>call_id</c>,
/// stores events with idempotency on <c>client_seq</c>, and can be told to fail the next
/// N requests to prove that retries re-use the same keys.
/// </summary>
public sealed class InProcessFakeCloudCore(string expectedToken, string provider = "fake-realtime", string transport = "websocket") : HttpMessageHandler
{
    private readonly ConcurrentDictionary<string, JsonObject> _toolResults = new(StringComparer.Ordinal);
    private readonly ConcurrentDictionary<string, ConcurrentDictionary<long, JsonObject>> _events = new(StringComparer.Ordinal);
    private int _failNext;
    private int _sessionCounter;

    public ConcurrentDictionary<string, FakeToolBehaviour> Tools { get; } = new(StringComparer.Ordinal)
    {
        ["echo"] = new(LongRunning: false, Preamble: null, args => args),
        ["research"] = new(
            LongRunning: true,
            Preamble: "Bakıyorum. OpenAI, Anthropic, Google ve önemli açık kaynak gelişmelerini karşılaştıracağım.",
            args => new JsonObject { ["summary"] = "araştırma tamam" }),
    };

    public ConcurrentDictionary<string, JsonObject> Sessions { get; } = new(StringComparer.Ordinal);

    public int ToolCallRequests => _toolCallRequests;

    public int ToolExecutions => _toolExecutions;

    public int EventRequests => _eventRequests;

    public int AttachRequests => _attachRequests;

    public int Unauthorized => _unauthorized;

    private int _toolCallRequests;
    private int _toolExecutions;
    private int _eventRequests;
    private int _attachRequests;
    private int _unauthorized;

    public IReadOnlyList<JsonObject> EventsFor(string sessionId)
        => _events.TryGetValue(sessionId, out var events)
            ? events.OrderBy(pair => pair.Key).Select(pair => pair.Value).ToList()
            : Array.Empty<JsonObject>();

    public IReadOnlyList<string> EventNamesFor(string sessionId)
        => EventsFor(sessionId).Select(e => e["event"]!.GetValue<string>()).ToList();

    /// <summary>The next <paramref name="count"/> requests answer 503 before any handling.</summary>
    public void FailNextRequests(int count) => Interlocked.Exchange(ref _failNext, count);

    public HttpClient CreateClient() => new(this, disposeHandler: false) { BaseAddress = new Uri("http://cloud-core.fake") };

    public CloudCoreSidebandClient CreateSidebandClient(string? token = null)
        => new(CreateClient(), new StaticOwnerSessionTokenSource(token ?? expectedToken));

    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        // Requests are counted before any outage or auth refusal: a retry after a 503 must be
        // visible as a second request carrying the same idempotency key.
        var requestPath = request.RequestUri!.AbsolutePath;
        if (requestPath.EndsWith("/tool-calls", StringComparison.Ordinal))
        {
            Interlocked.Increment(ref _toolCallRequests);
        }
        else if (requestPath.EndsWith("/events", StringComparison.Ordinal))
        {
            Interlocked.Increment(ref _eventRequests);
        }

        if (Interlocked.Decrement(ref _failNext) >= 0)
        {
            return Json(HttpStatusCode.ServiceUnavailable, new JsonObject { ["detail"] = "simulated outage" });
        }

        Interlocked.Exchange(ref _failNext, 0);

        if (request.Headers.Authorization is not { Scheme: "Bearer" } auth || auth.Parameter != expectedToken)
        {
            Interlocked.Increment(ref _unauthorized);
            var challenge = Json(HttpStatusCode.Unauthorized, new JsonObject { ["detail"] = "unauthorized" });
            challenge.Headers.WwwAuthenticate.Add(new AuthenticationHeaderValue("Bearer"));
            return challenge;
        }

        var path = request.RequestUri!.AbsolutePath;
        var body = request.Content is null
            ? new JsonObject()
            : JsonNode.Parse(await request.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false)) as JsonObject ?? new JsonObject();

        if (request.Method != HttpMethod.Post)
        {
            return Json(HttpStatusCode.MethodNotAllowed, new JsonObject { ["detail"] = "method not allowed" });
        }

        if (path == "/v1/voice/realtime/sessions")
        {
            return CreateSession(body);
        }

        var segments = path.Split('/', StringSplitOptions.RemoveEmptyEntries);
        if (segments.Length == 5 && segments[0] == "v1" && segments[1] == "voice" && segments[2] == "realtime" && segments[3] == "sessions")
        {
            return Json(HttpStatusCode.NotFound, new JsonObject { ["detail"] = "unknown route" });
        }

        if (segments.Length == 6 && segments[3] == "sessions")
        {
            var sessionId = Uri.UnescapeDataString(segments[4]);
            if (!Sessions.ContainsKey(sessionId))
            {
                return Json(HttpStatusCode.NotFound, new JsonObject { ["detail"] = "unknown session" });
            }

            return segments[5] switch
            {
                "tool-calls" => ToolCall(sessionId, body),
                "events" => Event(sessionId, body),
                "attach" => Attach(sessionId),
                _ => Json(HttpStatusCode.NotFound, new JsonObject { ["detail"] = "unknown route" }),
            };
        }

        return Json(HttpStatusCode.NotFound, new JsonObject { ["detail"] = "unknown route" });
    }

    private HttpResponseMessage CreateSession(JsonObject body)
    {
        var id = "rts-" + Interlocked.Increment(ref _sessionCounter).ToString("D4");
        var preference = body["transport_preference"] as JsonArray;
        var chosenTransport = preference?.Select(p => p?.GetValue<string>()).FirstOrDefault(p => p == transport) ?? transport;
        var grant = new JsonObject
        {
            ["session_id"] = id,
            ["provider"] = provider,
            ["transport"] = chosenTransport,
            ["credential"] = new JsonObject
            {
                ["value"] = "ek_fake_" + Guid.NewGuid().ToString("N"),
                ["url"] = "ws://provider.fake/realtime",
            },
            ["tools"] = new JsonArray(Tools.Select(t => (JsonNode)new JsonObject
            {
                ["type"] = "function",
                ["name"] = t.Key,
                ["long_running"] = t.Value.LongRunning,
                ["parameters"] = new JsonObject { ["type"] = "object" },
            }).ToArray()),
            ["instructions"] = "Sen sahibinin Türkçe konuşan yönetici asistanısın.",
            ["expires_at"] = DateTimeOffset.UtcNow.AddMinutes(10).ToString("O"),
        };
        Sessions[id] = (JsonObject)JsonNode.Parse(grant.ToJsonString())!;
        _events[id] = new ConcurrentDictionary<long, JsonObject>();
        return Json(HttpStatusCode.OK, grant);
    }

    private HttpResponseMessage ToolCall(string sessionId, JsonObject body)
    {
        var callId = body["call_id"]?.GetValue<string>();
        var name = body["name"]?.GetValue<string>();
        if (string.IsNullOrEmpty(callId) || string.IsNullOrEmpty(name))
        {
            return Json(HttpStatusCode.UnprocessableEntity, new JsonObject { ["detail"] = "call_id and name are required" });
        }

        var key = sessionId + "/" + callId;
        var response = _toolResults.GetOrAdd(key, _ =>
        {
            Interlocked.Increment(ref _toolExecutions);
            if (!Tools.TryGetValue(name, out var tool))
            {
                return new JsonObject
                {
                    ["call_id"] = callId,
                    ["error"] = new JsonObject { ["class"] = "capability_missing", ["message"] = $"unknown tool '{name}'" },
                };
            }

            if (tool.LongRunning)
            {
                return new JsonObject { ["call_id"] = callId, ["status"] = "running", ["preamble"] = tool.Preamble };
            }

            var arguments = body["arguments"] is { } node ? JsonNode.Parse(node.ToJsonString()) : null;
            var produced = tool.Result(arguments);
            return new JsonObject
            {
                ["call_id"] = callId,
                ["result"] = produced is null ? null : JsonNode.Parse(produced.ToJsonString()),
            };
        });

        return Json(HttpStatusCode.OK, (JsonObject)JsonNode.Parse(response.ToJsonString())!);
    }

    private HttpResponseMessage Event(string sessionId, JsonObject body)
    {
        var name = body["event"]?.GetValue<string>();
        var seq = body["client_seq"]?.GetValue<long>();
        if (name is null || seq is null || !VoiceClientEvents.All.Contains(name))
        {
            return Json(HttpStatusCode.UnprocessableEntity, new JsonObject { ["detail"] = "event and client_seq are required and event must be known" });
        }

        var stored = _events[sessionId].TryAdd(seq.Value, (JsonObject)JsonNode.Parse(body.ToJsonString())!);
        return Json(HttpStatusCode.OK, new JsonObject { ["accepted"] = true, ["duplicate"] = !stored });
    }

    private HttpResponseMessage Attach(string sessionId)
    {
        Interlocked.Increment(ref _attachRequests);
        var grant = (JsonObject)JsonNode.Parse(Sessions[sessionId].ToJsonString())!;
        grant["credential"] = new JsonObject
        {
            ["value"] = "ek_fake_reattach_" + Guid.NewGuid().ToString("N"),
            ["url"] = "ws://provider.fake/realtime",
        };
        return Json(HttpStatusCode.OK, grant);
    }

    private static HttpResponseMessage Json(HttpStatusCode status, JsonObject body) => new(status)
    {
        Content = new StringContent(body.ToJsonString(), Encoding.UTF8, "application/json"),
    };
}
