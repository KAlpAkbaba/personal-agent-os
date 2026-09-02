using System.Collections.Concurrent;
using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json.Nodes;
using PagentOS.Companion.Audio.Session;
using PagentOS.Companion.Audio.Sideband;

namespace PagentOS.Companion.Audio.Testing;

public sealed record FakeToolBehaviour(bool LongRunning, string? Preamble, Func<JsonNode?, JsonNode?> Result, string Description = "fake tool");

/// <summary>
/// The Cloud Core side of the M12 §4 contract as an <see cref="HttpMessageHandler"/>: no
/// sockets, no ports, so the whole sideband path runs in-process.
///
/// It validates requests EXACTLY the way <c>routes.py</c> does — same body shapes with extra
/// fields forbidden, same field names, same kind list (<c>CLIENT_EVENT_KINDS</c>), same
/// normalized forbidden-key rule, same size limits, same patterns — and answers with the
/// server's status codes: 201 on create, 404 unknown session, 409 stale leg, 410 closed,
/// 422 refused payload. A fake that is looser than the server is what let the first
/// client ship with a body the server could never accept; this one is the contract's
/// mirror, and a client that passes here posts to the real service unchanged.
/// </summary>
public sealed class InProcessFakeCloudCore(string expectedToken, string provider = "fake-realtime", string transport = "websocket") : HttpMessageHandler
{
    private static readonly string[] CreateKeys = { "client_kind", "transport", "language", "narration_session_id", "session_ttl_s" };
    private static readonly string[] ToolCallKeys = { "call_id", "name", "arguments" };
    private static readonly string[] EventsKeys = { "events" };
    private static readonly string[] EventKeys = { "kind", "t_ms", "turn", "payload", "text" };
    private static readonly string[] AttachKeys = { "client_kind", "transport" };
    private static readonly string[] CloseKeys = { "reason" };

    private readonly ConcurrentDictionary<string, FakeSession> _sessions = new(StringComparer.Ordinal);
    private int _failNext;
    private int _toolCallRequests;
    private int _toolExecutions;
    private int _eventRequests;
    private int _attachRequests;
    private int _unauthorized;
    private int _refusals;

    public ConcurrentDictionary<string, FakeToolBehaviour> Tools { get; } = new(StringComparer.Ordinal)
    {
        ["echo"] = new(LongRunning: false, Preamble: null, args => args),
        ["research"] = new(
            LongRunning: true,
            Preamble: "Bakıyorum. OpenAI, Anthropic, Google ve önemli açık kaynak gelişmelerini karşılaştıracağım.",
            args => new JsonObject { ["summary"] = "araştırma tamam" }),
    };

    /// <summary>Transports the fake provider offers; a create asking for another one is a 422 with the list, as the route answers.</summary>
    public List<string> OfferedTransports { get; } = new() { transport };

    /// <summary>When set, minted credentials carry it as <c>transport_descriptor</c> (ADR-0038); null mirrors the simulator, which omits the key.</summary>
    public JsonObject? TransportDescriptor { get; set; }

    public IReadOnlyDictionary<string, FakeSession> Sessions => _sessions;

    public int ToolCallRequests => _toolCallRequests;

    public int ToolExecutions => _toolExecutions;

    public int EventRequests => _eventRequests;

    public int AttachRequests => _attachRequests;

    public int Unauthorized => _unauthorized;

    /// <summary>422s answered. Any non-zero value in a test means the client sent something the server would refuse.</summary>
    public int Refusals => _refusals;

    /// <summary>The <c>detail</c> of the most recent 422.</summary>
    public JsonNode? LastRefusal { get; private set; }

    /// <summary>Frames the "broker" would have pushed to the previous leg (leg_closed on supersede); a test routes them into its push source.</summary>
    public List<JsonObject> Pushed { get; } = new();

    public IReadOnlyList<JsonObject> EventsFor(string sessionId)
        => _sessions.TryGetValue(sessionId, out var session) ? session.EventsSnapshot() : Array.Empty<JsonObject>();

    public IReadOnlyList<string> EventKindsFor(string sessionId)
        => EventsFor(sessionId).Select(e => e["kind"]!.GetValue<string>()).ToList();

    /// <summary>The next <paramref name="count"/> requests answer 503 before any handling.</summary>
    public void FailNextRequests(int count) => Interlocked.Exchange(ref _failNext, count);

    /// <summary>
    /// The next <paramref name="count"/> event batches answer 422 whatever they carry — a
    /// server-side rule this client does not know (contract drift), so the reporter's
    /// "drop loudly, never retry, keep going" path is exercised without weakening the checks.
    /// </summary>
    public void RefuseNextEventBatches(int count) => Interlocked.Exchange(ref _refuseNextEvents, count);

    private int _refuseNextEvents;

    /// <summary>Another client attached (spec §7): the leg moves away from the token this fake serves; the previous leg is told with a leg_closed frame.</summary>
    public JsonObject SupersedeLeg(string sessionId, string newClientKind = "mobile")
    {
        var session = _sessions[sessionId];
        session.LegToken = "other-client:" + Guid.NewGuid().ToString("N");
        session.Legs++;
        session.ClientKind = newClientKind;
        var frame = Frame(sessionId, SidebandPushKinds.LegClosed, new JsonObject
        {
            ["reason"] = "attached_elsewhere",
            ["new_client_kind"] = newClientKind,
        });
        Pushed.Add(frame);
        return frame;
    }

    /// <summary>The session was closed/expired server-side: every later request answers 410, as the route does.</summary>
    public void CloseSession(string sessionId)
    {
        var session = _sessions[sessionId];
        session.State = "closed";
        session.ClosedAt = DateTimeOffset.UtcNow;
    }

    /// <summary>A push the broker could not deliver: queued on the session, drained by the next events ack or attach (spec §4 step 4).</summary>
    public void QueueSideband(string sessionId, string kind, JsonObject payload)
    {
        if (!SidebandPushKinds.All.Contains(kind))
        {
            throw new ArgumentException($"'{kind}' is not a sideband event", nameof(kind));
        }

        _sessions[sessionId].Pending.Add(Frame(sessionId, kind, payload));
    }

    public static JsonObject Frame(string sessionId, string kind, JsonObject payload) => new()
    {
        ["type"] = SidebandPush.FrameType,
        ["session_id"] = sessionId,
        ["event"] = kind,
        ["payload"] = JsonNode.Parse(payload.ToJsonString()),
        ["at"] = DateTimeOffset.UtcNow.ToString("O"),
    };

    public HttpClient CreateClient() => new(this, disposeHandler: false) { BaseAddress = new Uri("http://cloud-core.fake") };

    public CloudCoreSidebandClient CreateSidebandClient(string? token = null)
        => new(CreateClient(), new StaticOwnerSessionTokenSource(token ?? expectedToken));

    protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        // Requests are counted before any outage or auth refusal: a retry after a 503 must be
        // visible as a second request carrying the same idempotency key.
        var path = request.RequestUri!.AbsolutePath;
        if (path.EndsWith("/tool-calls", StringComparison.Ordinal))
        {
            Interlocked.Increment(ref _toolCallRequests);
        }
        else if (path.EndsWith("/events", StringComparison.Ordinal))
        {
            Interlocked.Increment(ref _eventRequests);
        }

        if (Interlocked.Decrement(ref _failNext) >= 0)
        {
            return Json(HttpStatusCode.ServiceUnavailable, new JsonObject { ["detail"] = "simulated outage" });
        }

        Interlocked.Exchange(ref _failNext, 0);

        var token = request.Headers.Authorization is { Scheme: "Bearer" } auth ? auth.Parameter : null;
        if (token is null || token != expectedToken)
        {
            Interlocked.Increment(ref _unauthorized);
            var challenge = Json(HttpStatusCode.Unauthorized, new JsonObject { ["detail"] = "unauthorized" });
            challenge.Headers.WwwAuthenticate.Add(new AuthenticationHeaderValue("Bearer"));
            return challenge;
        }

        var segments = path.Split('/', StringSplitOptions.RemoveEmptyEntries);
        if (segments.Length < 4 || segments[0] != "v1" || segments[1] != "voice" || segments[2] != "realtime" || segments[3] != "sessions")
        {
            return Json(HttpStatusCode.NotFound, new JsonObject { ["detail"] = "Not Found" });
        }

        if (request.Method != HttpMethod.Post)
        {
            return Json(HttpStatusCode.MethodNotAllowed, new JsonObject { ["detail"] = "Method Not Allowed" });
        }

        JsonObject? body = null;
        var text = request.Content is null ? string.Empty : await request.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        if (!string.IsNullOrWhiteSpace(text))
        {
            try
            {
                body = JsonNode.Parse(text) as JsonObject;
            }
            catch (System.Text.Json.JsonException)
            {
                body = null;
            }

            if (body is null)
            {
                return Refuse(ValidationError("body", "Input should be a valid dictionary", "model_type"));
            }
        }

        if (segments.Length == 4)
        {
            return CreateSession(body ?? new JsonObject(), token);
        }

        if (segments.Length != 6)
        {
            return Json(HttpStatusCode.NotFound, new JsonObject { ["detail"] = "Not Found" });
        }

        var sessionId = Uri.UnescapeDataString(segments[4]);
        if (!Guid.TryParse(sessionId, out _))
        {
            return Refuse(ValidationError("path.session_id", "Input should be a valid UUID", "uuid_parsing"));
        }

        if (!_sessions.TryGetValue(sessionId, out var session))
        {
            return Json(HttpStatusCode.NotFound, new JsonObject { ["detail"] = "unknown realtime session" });
        }

        return segments[5] switch
        {
            "tool-calls" => ToolCall(session, body ?? new JsonObject(), token),
            "events" => Events(session, body ?? new JsonObject(), token),
            "attach" => Attach(session, body, token),
            "close" => Close(session, body),
            _ => Json(HttpStatusCode.NotFound, new JsonObject { ["detail"] = "Not Found" }),
        };
    }

    // ------------------------------------------------------------------ routes

    private HttpResponseMessage CreateSession(JsonObject body, string token)
    {
        if (ExtraKeys(body, CreateKeys, "body") is { } extra)
        {
            return Refuse(extra);
        }

        var clientKind = body["client_kind"]?.GetValue<string>();
        if (clientKind is not null && !RealtimeContract.ClientKindRegex().IsMatch(clientKind))
        {
            return Refuse(ValidationError("body.client_kind", "String should match pattern '^[a-z][a-z0-9_]{0,15}$'", "string_pattern_mismatch"));
        }

        var language = body["language"]?.GetValue<string>() ?? "tr-TR";
        if (!System.Text.RegularExpressions.Regex.IsMatch(language, "^[a-z]{2}-[A-Z]{2}$"))
        {
            return Refuse(ValidationError("body.language", "String should match pattern '^[a-z]{2}-[A-Z]{2}$'", "string_pattern_mismatch"));
        }

        var requested = body["transport"]?.GetValue<string>();
        if (requested is not null && !RealtimeContract.Transports.Contains(requested))
        {
            return Refuse(ValidationError("body.transport", $"Value error, transport must be one of {string.Join("/", RealtimeContract.Transports)}", "value_error"));
        }

        var chosen = requested ?? OfferedTransports[0];
        if (!OfferedTransports.Contains(chosen))
        {
            // routes.create_session: an explicit 422 naming the transports the provider offers.
            Interlocked.Increment(ref _refusals);
            var detail = new JsonObject
            {
                ["error_class"] = "validation_error",
                ["message"] = $"provider '{provider}' does not offer transport '{chosen}'",
                ["transports"] = new JsonArray(OfferedTransports.Select(t => (JsonNode)t).ToArray()),
            };
            LastRefusal = detail;
            return Json(HttpStatusCode.UnprocessableEntity, new JsonObject { ["detail"] = detail });
        }

        var session = new FakeSession(Guid.NewGuid().ToString(), provider, chosen, clientKind ?? "desktop", language, token);
        _sessions[session.Id] = session;
        return Json(HttpStatusCode.Created, LegPayload(session));
    }

    private HttpResponseMessage ToolCall(FakeSession session, JsonObject body, string token)
    {
        if (ExtraKeys(body, ToolCallKeys, "body") is { } extra)
        {
            return Refuse(extra);
        }

        var callId = body["call_id"]?.GetValue<string>();
        var name = body["name"]?.GetValue<string>();
        if (callId is null)
        {
            return Refuse(ValidationError("body.call_id", "Field required", "missing"));
        }

        if (callId.Length is 0 or > 128 || !RealtimeContract.CallIdRegex().IsMatch(callId))
        {
            return Refuse(ValidationError("body.call_id", "String should match pattern '^[A-Za-z0-9_.:-]+$'", "string_pattern_mismatch"));
        }

        if (name is null)
        {
            return Refuse(ValidationError("body.name", "Field required", "missing"));
        }

        if (name.Length is 0 or > 128 || !RealtimeContract.ToolNameRegex().IsMatch(name))
        {
            return Refuse(ValidationError("body.name", "String should match pattern '^[a-z][a-z0-9_.]*$'", "string_pattern_mismatch"));
        }

        JsonObject arguments;
        switch (body["arguments"])
        {
            case null:
                arguments = new JsonObject();
                break;
            case JsonObject obj:
                arguments = obj;
                break;
            default:
                return Refuse(ValidationError("body.arguments", "Input should be a valid dictionary", "dict_type"));
        }

        if (RealtimeContract.FindForbiddenKey(arguments) is { } forbidden)
        {
            return Refuse(ValidationError("body.arguments", $"Value error, arguments must not carry audio or credentials ('{forbidden}')", "value_error"));
        }

        if (RealtimeContract.EncodedBytes(arguments) > RealtimeContract.MaxArgumentsBytes)
        {
            return Refuse(ValidationError("body.arguments", $"Value error, arguments exceeds {RealtimeContract.MaxArgumentsBytes} bytes", "value_error"));
        }

        if (RequireLive(session) is { } dead)
        {
            return dead;
        }

        if (RequireLeg(session, token) is { } stale)
        {
            return stale;
        }

        session.Touch();
        var payload = session.ToolResults.GetOrAdd(callId, _ =>
        {
            Interlocked.Increment(ref _toolExecutions);
            if (!Tools.TryGetValue(name, out var tool))
            {
                return new JsonObject
                {
                    ["call_id"] = callId,
                    ["name"] = name,
                    ["status"] = ToolCallRelayResult.StatusFailed,
                    ["long_running"] = false,
                    ["error"] = new JsonObject
                    {
                        ["error_class"] = "capability_missing",
                        ["message"] = $"unknown tool '{name}'",
                        ["available"] = new JsonArray(Tools.Keys.OrderBy(k => k, StringComparer.Ordinal).Select(k => (JsonNode)k).ToArray()),
                    },
                };
            }

            if (tool.LongRunning)
            {
                return new JsonObject
                {
                    ["call_id"] = callId,
                    ["name"] = name,
                    ["status"] = ToolCallRelayResult.StatusRunning,
                    ["long_running"] = true,
                    ["result"] = new JsonObject { ["plan_id"] = Guid.NewGuid().ToString(), ["status"] = "running" },
                    ["preamble"] = tool.Preamble,
                };
            }

            var produced = tool.Result(JsonNode.Parse(arguments.ToJsonString()));
            return new JsonObject
            {
                ["call_id"] = callId,
                ["name"] = name,
                ["status"] = ToolCallRelayResult.StatusSucceeded,
                ["long_running"] = false,
                ["result"] = produced is null ? null : JsonNode.Parse(produced.ToJsonString()),
            };
        });

        var replayed = session.SeenCalls.TryAdd(callId, 1) is false;
        var response = (JsonObject)JsonNode.Parse(payload.ToJsonString())!;
        response["replayed"] = replayed;
        return Json(HttpStatusCode.OK, response);
    }

    private HttpResponseMessage Events(FakeSession session, JsonObject body, string token)
    {
        if (Interlocked.Decrement(ref _refuseNextEvents) >= 0)
        {
            return Refuse(ValidationError("body.events", "Value error, simulated_contract_drift", "value_error"));
        }

        Interlocked.Exchange(ref _refuseNextEvents, 0);
        if (ExtraKeys(body, EventsKeys, "body") is { } extra)
        {
            return Refuse(extra);
        }

        if (body["events"] is not JsonArray events)
        {
            return Refuse(ValidationError("body.events", body.ContainsKey("events") ? "Input should be a valid list" : "Field required", body.ContainsKey("events") ? "list_type" : "missing"));
        }

        if (events.Count == 0)
        {
            return Refuse(ValidationError("body.events", "List should have at least 1 item after validation, not 0", "too_short"));
        }

        if (events.Count > RealtimeContract.MaxEventsPerRequest)
        {
            return Refuse(ValidationError("body.events", $"List should have at most {RealtimeContract.MaxEventsPerRequest} items after validation, not {events.Count}", "too_long"));
        }

        var validated = new List<JsonObject>();
        for (var i = 0; i < events.Count; i++)
        {
            if (events[i] is not JsonObject ev)
            {
                return Refuse(ValidationError($"body.events.{i}", "Input should be a valid dictionary", "model_type"));
            }

            if (ExtraKeys(ev, EventKeys, $"body.events.{i}") is { } extraEvent)
            {
                return Refuse(extraEvent);
            }

            var kind = ev["kind"]?.GetValue<string>();
            if (kind is null)
            {
                return Refuse(ValidationError($"body.events.{i}.kind", "Field required", "missing"));
            }

            if (!VoiceClientEvents.All.Contains(kind))
            {
                return Refuse(ValidationError($"body.events.{i}.kind", $"Value error, unknown event kind '{kind}'", "value_error"));
            }

            if (ev["t_ms"] is not JsonValue tValue || !TryInt64(tValue, out var tMs))
            {
                return Refuse(ValidationError($"body.events.{i}.t_ms", ev.ContainsKey("t_ms") ? "Input should be a valid integer" : "Field required", ev.ContainsKey("t_ms") ? "int_type" : "missing"));
            }

            if (tMs < 0 || tMs > RealtimeContract.MaxTMs)
            {
                return Refuse(ValidationError($"body.events.{i}.t_ms", "Input should be greater than or equal to 0", "greater_than_equal"));
            }

            long turn = 0;
            if (ev["turn"] is JsonValue turnValue)
            {
                if (!TryInt64(turnValue, out turn))
                {
                    return Refuse(ValidationError($"body.events.{i}.turn", "Input should be a valid integer", "int_type"));
                }

                if (turn < 0 || turn > RealtimeContract.MaxTurn)
                {
                    return Refuse(ValidationError($"body.events.{i}.turn", "Input should be greater than or equal to 0", "greater_than_equal"));
                }
            }

            JsonObject payload;
            switch (ev["payload"])
            {
                case null:
                    payload = new JsonObject();
                    break;
                case JsonObject obj:
                    payload = obj;
                    break;
                default:
                    return Refuse(ValidationError($"body.events.{i}.payload", "Input should be a valid dictionary", "dict_type"));
            }

            if (RealtimeContract.FindForbiddenKey(payload) is { } forbidden)
            {
                return Refuse(ValidationError($"body.events.{i}.payload", $"Value error, payload must not carry audio or credentials ('{forbidden}')", "value_error"));
            }

            if (RealtimeContract.EncodedBytes(payload) > RealtimeContract.MaxEventPayloadBytes)
            {
                return Refuse(ValidationError($"body.events.{i}.payload", $"Value error, payload exceeds {RealtimeContract.MaxEventPayloadBytes} bytes", "value_error"));
            }

            if (ev["text"] is JsonValue textValue)
            {
                if (!textValue.TryGetValue<string>(out var textString))
                {
                    return Refuse(ValidationError($"body.events.{i}.text", "Input should be a valid string", "string_type"));
                }

                if (textString.Length > RealtimeContract.MaxEventTextChars)
                {
                    return Refuse(ValidationError($"body.events.{i}.text", $"String should have at most {RealtimeContract.MaxEventTextChars} characters", "string_too_long"));
                }
            }

            validated.Add((JsonObject)JsonNode.Parse(ev.ToJsonString())!);
        }

        if (RequireLive(session) is { } dead)
        {
            return dead;
        }

        if (RequireLeg(session, token) is { } stale)
        {
            return stale;
        }

        session.Touch();
        var resolved = new JsonArray();
        foreach (var ev in validated)
        {
            var kind = ev["kind"]!.GetValue<string>();
            session.AddEvent(ev);
            switch (kind)
            {
                case VoiceClientEvents.Utterance:
                    var textValue = ev["text"]?.GetValue<string>() ?? string.Empty;
                    var intent = VoiceClientStateMachine.IsStopWord(textValue) ? "stop" : "none";
                    session.LastIntent = intent;
                    resolved.Add(new JsonObject
                    {
                        ["t_ms"] = ev["t_ms"]!.GetValue<long>(),
                        ["turn"] = ev["turn"]?.GetValue<long>() ?? 0,
                        ["intent"] = intent,
                        ["scope"] = "conversation",
                        ["target_index"] = null,
                        ["normalized_text"] = null,
                        ["fillers_removed"] = 0,
                        ["confidence"] = 1.0,
                        ["matched"] = intent == "stop" ? textValue.Trim().ToLowerInvariant() : string.Empty,
                    });
                    break;
                case VoiceClientEvents.Summary:
                    session.TranscriptSummary = (ev["text"]?.GetValue<string>() ?? string.Empty);
                    break;
                case VoiceClientEvents.State:
                    if (ev["payload"]?["state"]?.GetValue<string>() is { } state)
                    {
                        session.FsmState = state;
                    }

                    break;
                case VoiceClientEvents.BargeInStart:
                    session.BargeInCount++;
                    break;
                case VoiceClientEvents.NetworkLost:
                    session.Network = "lost";
                    break;
                case VoiceClientEvents.NetworkRestored:
                    session.Network = "restored";
                    break;
            }
        }

        var pending = session.DrainPending();
        return Json(HttpStatusCode.OK, new JsonObject
        {
            ["accepted"] = validated.Count,
            ["resolved_intents"] = resolved,
            ["pending_sideband"] = new JsonArray(pending.Select(p => (JsonNode)p).ToArray()),
            ["state"] = StateJson(session),
        });
    }

    private HttpResponseMessage Attach(FakeSession session, JsonObject? body, string token)
    {
        Interlocked.Increment(ref _attachRequests);
        body ??= new JsonObject();
        if (ExtraKeys(body, AttachKeys, "body") is { } extra)
        {
            return Refuse(extra);
        }

        var clientKind = body["client_kind"]?.GetValue<string>();
        if (clientKind is not null && !RealtimeContract.ClientKindRegex().IsMatch(clientKind))
        {
            return Refuse(ValidationError("body.client_kind", "String should match pattern '^[a-z][a-z0-9_]{0,15}$'", "string_pattern_mismatch"));
        }

        var requestedTransport = body["transport"]?.GetValue<string>();
        if (requestedTransport is not null && !RealtimeContract.Transports.Contains(requestedTransport))
        {
            return Refuse(ValidationError("body.transport", $"Value error, transport must be one of {string.Join("/", RealtimeContract.Transports)}", "value_error"));
        }

        if (RequireLive(session) is { } dead)
        {
            return dead;
        }

        var sameLeg = session.LegToken == token;
        JsonObject? previous = null;
        if (!sameLeg)
        {
            previous = new JsonObject
            {
                ["owner_session_id"] = session.LegToken,
                ["device_id"] = null,
                ["client_kind"] = session.ClientKind,
            };
            session.Legs++;
            Pushed.Add(Frame(session.Id, SidebandPushKinds.LegClosed, new JsonObject
            {
                ["reason"] = "attached_elsewhere",
                ["new_client_kind"] = clientKind ?? "desktop",
            }));
        }

        session.LegToken = token;
        session.ClientKind = clientKind ?? session.ClientKind;
        if (requestedTransport is not null)
        {
            session.Transport = requestedTransport;
        }

        if (session.Network == "lost")
        {
            session.Network = "restored";
        }

        session.Touch();
        session.CredentialsMinted++;
        var pending = session.DrainPending();
        var payload = LegPayload(session);
        payload["state"] = StateJson(session);
        payload["pending_sideband"] = new JsonArray(pending.Select(p => (JsonNode)p).ToArray());
        payload["previous_leg"] = previous;
        return Json(HttpStatusCode.OK, payload);
    }

    private HttpResponseMessage Close(FakeSession session, JsonObject? body)
    {
        body ??= new JsonObject();
        if (ExtraKeys(body, CloseKeys, "body") is { } extra)
        {
            return Refuse(extra);
        }

        if (session.State != "closed")
        {
            session.State = "closed";
            session.ClosedAt = DateTimeOffset.UtcNow;
        }

        return Json(HttpStatusCode.OK, new JsonObject
        {
            ["session_id"] = session.Id,
            ["state"] = session.State,
            ["closed_at"] = session.ClosedAt?.ToString("O"),
        });
    }

    // ---------------------------------------------------------------- helpers

    private HttpResponseMessage? RequireLive(FakeSession session)
    {
        if (session.State is "closed" or "expired")
        {
            return Json(HttpStatusCode.Gone, new JsonObject
            {
                ["detail"] = new JsonObject
                {
                    ["error_class"] = "validation_error",
                    ["message"] = $"session is {session.State}",
                    ["provider"] = null,
                    ["retryable"] = false,
                    ["details"] = new JsonObject { ["state"] = session.State },
                },
            });
        }

        return null;
    }

    private HttpResponseMessage? RequireLeg(FakeSession session, string token)
    {
        if (session.LegToken != token)
        {
            return Json(HttpStatusCode.Conflict, new JsonObject
            {
                ["detail"] = new JsonObject
                {
                    ["error_class"] = "validation_error",
                    ["message"] = "this owner session does not hold the session's current media leg; attach first",
                    ["provider"] = null,
                    ["retryable"] = false,
                    ["details"] = new JsonObject { ["leg"] = "mismatch" },
                },
            });
        }

        return null;
    }

    private JsonObject LegPayload(FakeSession session)
    {
        var credential = new JsonObject
        {
            ["provider"] = provider,
            ["secret"] = "ek_fake_" + session.CredentialsMinted + "_" + Guid.NewGuid().ToString("N"),
            ["expires_at"] = DateTimeOffset.UtcNow.AddMinutes(10).ToString("O"),
            ["transport"] = session.Transport,
            ["session_ref"] = "fake-ref-" + session.Id[..8],
        };
        if (TransportDescriptor is not null)
        {
            credential["transport_descriptor"] = JsonNode.Parse(TransportDescriptor.ToJsonString());
        }

        return new JsonObject
        {
            ["session_id"] = session.Id,
            ["provider"] = provider,
            ["transport"] = session.Transport,
            ["credential"] = credential,
            ["tools"] = new JsonArray(Tools.OrderBy(t => t.Key, StringComparer.Ordinal).Select(t =>
            {
                var entry = new JsonObject
                {
                    ["name"] = t.Key,
                    ["description"] = t.Value.Description,
                    ["parameters"] = new JsonObject { ["type"] = "object", ["properties"] = new JsonObject() },
                    ["long_running"] = t.Value.LongRunning,
                };
                if (t.Value.Preamble is not null)
                {
                    entry["preamble"] = t.Value.Preamble;
                }

                return (JsonNode)entry;
            }).ToArray()),
            ["instructions"] = "Sen sahibinin Türkçe konuşan yönetici asistanısın.",
            ["language"] = session.Language,
            ["expires_at"] = session.ExpiresAt.ToString("O"),
            ["state"] = session.State,
        };
    }

    private JsonObject StateJson(FakeSession session) => new()
    {
        ["session_id"] = session.Id,
        ["provider"] = provider,
        ["transport"] = session.Transport,
        ["client_kind"] = session.ClientKind,
        ["device_id"] = null,
        ["state"] = session.State,
        ["language"] = session.Language,
        ["plan"] = null,
        ["plan_id"] = null,
        ["narration"] = null,
        ["presentation"] = null,
        ["last_intent"] = session.LastIntent,
        ["fsm_state"] = session.FsmState,
        ["barge_in_count"] = session.BargeInCount,
        ["network"] = session.Network,
        ["legs"] = session.Legs,
        ["pending_sideband_count"] = session.Pending.Count,
        ["transcript_summary"] = session.TranscriptSummary,
        ["created_at"] = session.CreatedAt.ToString("O"),
        ["expires_at"] = session.ExpiresAt.ToString("O"),
        ["closed_at"] = session.ClosedAt?.ToString("O"),
    };

    private static JsonArray? ExtraKeys(JsonObject body, string[] allowed, string where)
    {
        JsonArray? errors = null;
        foreach (var pair in body)
        {
            if (!allowed.Contains(pair.Key, StringComparer.Ordinal))
            {
                errors ??= new JsonArray();
                errors.Add(new JsonObject
                {
                    ["type"] = "extra_forbidden",
                    ["loc"] = new JsonArray(where.Split('.').Select(s => (JsonNode)s).Append(pair.Key).ToArray()),
                    ["msg"] = "Extra inputs are not permitted",
                });
            }
        }

        return errors;
    }

    private static JsonArray ValidationError(string loc, string message, string type) => new(new JsonObject
    {
        ["type"] = type,
        ["loc"] = new JsonArray(loc.Split('.').Select(s => (JsonNode)s).ToArray()),
        ["msg"] = message,
    });

    private HttpResponseMessage Refuse(JsonArray detail)
    {
        Interlocked.Increment(ref _refusals);
        LastRefusal = detail;
        return Json(HttpStatusCode.UnprocessableEntity, new JsonObject { ["detail"] = JsonNode.Parse(detail.ToJsonString()) });
    }

    private static bool TryInt64(JsonValue value, out long result)
    {
        if (value.TryGetValue<long>(out result))
        {
            return true;
        }

        if (value.TryGetValue<double>(out var d) && Math.Floor(d) == d)
        {
            result = (long)d;
            return true;
        }

        result = 0;
        return false;
    }

    private static HttpResponseMessage Json(HttpStatusCode status, JsonObject body) => new(status)
    {
        Content = new StringContent(body.ToJsonString(), Encoding.UTF8, "application/json"),
    };

    /// <summary>One realtime session row, as the service keeps it (ids, state, legs, backlog; never audio).</summary>
    public sealed class FakeSession(string id, string provider, string transport, string clientKind, string language, string legToken)
    {
        private readonly List<JsonObject> _events = new();

        public string Id { get; } = id;

        public string Provider { get; } = provider;

        public string Transport { get; set; } = transport;

        public string ClientKind { get; set; } = clientKind;

        public string Language { get; } = language;

        /// <summary>The owner bearer that holds the CURRENT media leg (service.require_leg).</summary>
        public string LegToken { get; set; } = legToken;

        public string State { get; set; } = "created";

        public int Legs { get; set; } = 1;

        public int CredentialsMinted { get; set; } = 1;

        public string? LastIntent { get; set; }

        public string FsmState { get; set; } = "IDLE";

        public int BargeInCount { get; set; }

        public string? Network { get; set; }

        public string TranscriptSummary { get; set; } = string.Empty;

        public DateTimeOffset CreatedAt { get; } = DateTimeOffset.UtcNow;

        public DateTimeOffset ExpiresAt { get; } = DateTimeOffset.UtcNow.AddHours(1);

        public DateTimeOffset? ClosedAt { get; set; }

        public List<JsonObject> Pending { get; } = new();

        public ConcurrentDictionary<string, JsonObject> ToolResults { get; } = new(StringComparer.Ordinal);

        public ConcurrentDictionary<string, byte> SeenCalls { get; } = new(StringComparer.Ordinal);

        public void Touch()
        {
            if (State == "created")
            {
                State = "active";
            }
        }

        public void AddEvent(JsonObject ev)
        {
            lock (_events)
            {
                _events.Add(ev);
            }
        }

        public IReadOnlyList<JsonObject> EventsSnapshot()
        {
            lock (_events)
            {
                return _events.ToList();
            }
        }

        public List<JsonObject> DrainPending()
        {
            lock (Pending)
            {
                var drained = Pending.ToList();
                Pending.Clear();
                return drained;
            }
        }
    }
}
