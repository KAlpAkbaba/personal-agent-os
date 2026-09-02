using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json.Nodes;
using PagentOS.Companion.Audio.Sideband;
using PagentOS.Companion.Audio.Testing;
using Xunit;

namespace PagentOS.Companion.Audio.Tests;

/// <summary>
/// ADR-0039: the in-process fake must refuse exactly what Cloud Core refuses. Every test here
/// posts RAW bodies — bypassing the typed client — so the fake is judged on its own, and the
/// shapes the first client shipped with (one <c>{event, client_seq, client_ts_ms, data}</c>
/// object per call; a guessed credential key; camelCase keys the old fake let through) are
/// pinned as 422s. A fake that is looser than the server is what let that ship.
/// </summary>
public sealed class ContractTests
{
    private const string Token = "pagentos_sess_contract";

    private static async Task<(HttpStatusCode Status, JsonObject Body)> PostRawAsync(InProcessFakeCloudCore cloud, string path, string json, string? token = Token)
    {
        using var http = cloud.CreateClient();
        using var request = new HttpRequestMessage(HttpMethod.Post, path);
        if (token is not null)
        {
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        }

        request.Content = new StringContent(json, Encoding.UTF8, "application/json");
        using var response = await http.SendAsync(request);
        var text = await response.Content.ReadAsStringAsync();
        return (response.StatusCode, JsonNode.Parse(string.IsNullOrWhiteSpace(text) ? "{}" : text)!.AsObject());
    }

    private static async Task<string> CreateAsync(InProcessFakeCloudCore cloud)
    {
        var (status, body) = await PostRawAsync(cloud, RealtimeContract.SessionsPath, """{"client_kind":"windows_desktop","transport":"websocket","language":"tr-TR"}""");
        Assert.Equal(HttpStatusCode.Created, status);
        return body["session_id"]!.GetValue<string>();
    }

    private static string Detail(JsonObject body) => body["detail"]!.ToJsonString();

    // ------------------------------------------------------------- /events shape

    [Fact]
    public async Task A_per_call_single_object_post_is_refused_exactly_as_the_server_refuses_it()
    {
        using var cloud = new InProcessFakeCloudCore(Token);
        var sid = await CreateAsync(cloud);

        // The pre-ADR-0039 client body. The server's EventsRequest has one field, `events`, and forbids extras.
        var (status, body) = await PostRawAsync(cloud, $"{RealtimeContract.SessionsPath}/{sid}/events",
            """{"event":"speech_started","client_seq":1,"client_ts_ms":12.5,"data":{}}""");

        Assert.Equal(HttpStatusCode.UnprocessableEntity, status);
        var detail = body["detail"]!.AsArray();
        Assert.Contains(detail, d => d!["type"]!.GetValue<string>() == "extra_forbidden" && d["loc"]!.ToJsonString().Contains("client_seq"));
        Assert.Empty(cloud.EventsFor(sid));
        Assert.Equal(1, cloud.Refusals);

        // ...and a bare ClientEvent without the `events` wrapper is a missing field, not a lenient accept.
        var (bare, bareBody) = await PostRawAsync(cloud, $"{RealtimeContract.SessionsPath}/{sid}/events",
            """{"kind":"mic_speech_start","t_ms":1}""");
        Assert.Equal(HttpStatusCode.UnprocessableEntity, bare);
        Assert.Contains("extra_forbidden", Detail(bareBody));
    }

    [Fact]
    public async Task The_batch_shape_with_the_servers_kinds_is_accepted_and_returns_the_ack_shape()
    {
        using var cloud = new InProcessFakeCloudCore(Token);
        var sid = await CreateAsync(cloud);
        cloud.QueueSideband(sid, SidebandPushKinds.Say, new JsonObject { ["text"] = "Bir dakika." });

        // the very stream test_events_feed_the_benchmark_and_resolve_intents_server_side posts
        var (status, body) = await PostRawAsync(cloud, $"{RealtimeContract.SessionsPath}/{sid}/events", """
            {"events":[
              {"kind":"mic_speech_start","t_ms":1000,"turn":1},
              {"kind":"uplink_first_packet","t_ms":1040,"turn":1},
              {"kind":"end_of_turn","t_ms":2200,"turn":1},
              {"kind":"first_audio","t_ms":2700,"turn":1},
              {"kind":"barge_in_start","t_ms":3000,"turn":2,"payload":{"playback_stopped_ms":70}},
              {"kind":"playback_stopped","t_ms":3070,"turn":2},
              {"kind":"utterance","t_ms":3100,"turn":2,"text":"dur"},
              {"kind":"state","t_ms":3300,"payload":{"state":"LISTENING"}},
              {"kind":"summary","t_ms":3400,"text":"Sahip durdurdu."},
              {"kind":"network_lost","t_ms":4000},
              {"kind":"network_restored","t_ms":4500}
            ]}
            """);

        Assert.Equal(HttpStatusCode.OK, status);
        Assert.Equal(11, body["accepted"]!.GetValue<int>());
        Assert.Equal("stop", body["resolved_intents"]![0]!["intent"]!.GetValue<string>());
        Assert.Equal("LISTENING", body["state"]!["fsm_state"]!.GetValue<string>());
        Assert.Equal(1, body["state"]!["barge_in_count"]!.GetValue<int>());
        Assert.Equal("restored", body["state"]!["network"]!.GetValue<string>());
        Assert.Equal("Sahip durdurdu.", body["state"]!["transcript_summary"]!.GetValue<string>());
        var pending = body["pending_sideband"]!.AsArray();
        Assert.Single(pending);
        Assert.Equal("voice_sideband", pending[0]!["type"]!.GetValue<string>());
        Assert.Equal("say", pending[0]!["event"]!.GetValue<string>());
        Assert.Equal(0, body["state"]!["pending_sideband_count"]!.GetValue<int>());
        Assert.Equal(0, cloud.Refusals);
    }

    [Theory]
    [InlineData("apiKey")]
    [InlineData("api-key")]
    [InlineData("API_KEY")]
    [InlineData("Api Key")]
    [InlineData("x-api-key")]
    [InlineData("accessToken")]
    [InlineData("audioPcm")]
    public async Task A_forbidden_key_under_any_spelling_in_an_event_payload_is_refused_like_the_server(string spelling)
    {
        // test_forbidden_keys_are_caught_under_any_spelling_at_the_route_and_in_the_scrubber, mirrored.
        using var cloud = new InProcessFakeCloudCore(Token);
        var sid = await CreateAsync(cloud);
        Assert.True(RealtimeContract.IsForbiddenKey(spelling));
        foreach (var benign in new[] { "call_id", "duration_ms", "turn", "t_ms", "error_class" })
        {
            Assert.False(RealtimeContract.IsForbiddenKey(benign));
        }

        var flat = await PostRawAsync(cloud, $"{RealtimeContract.SessionsPath}/{sid}/events",
            "{\"events\":[{\"kind\":\"error\",\"t_ms\":1,\"payload\":{\"" + spelling + "\":\"v\"}}]}");
        Assert.Equal(HttpStatusCode.UnprocessableEntity, flat.Status);
        Assert.Contains("must not carry audio or credentials", Detail(flat.Body));
        Assert.Contains(spelling, Detail(flat.Body));

        var nested = await PostRawAsync(cloud, $"{RealtimeContract.SessionsPath}/{sid}/tool-calls",
            "{\"call_id\":\"c\",\"name\":\"echo\",\"arguments\":{\"nested\":{\"" + spelling + "\":\"v\"}}}");
        Assert.Equal(HttpStatusCode.UnprocessableEntity, nested.Status);
        Assert.Equal(0, cloud.ToolExecutions);
        Assert.Empty(cloud.EventsFor(sid));
    }

    [Fact]
    public async Task Unknown_kinds_oversize_payloads_negative_times_and_empty_batches_are_refused_like_the_server()
    {
        // test_events_reject_audio_unknown_kinds_and_oversize, mirrored.
        using var cloud = new InProcessFakeCloudCore(Token);
        var sid = await CreateAsync(cloud);
        var url = $"{RealtimeContract.SessionsPath}/{sid}/events";

        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await PostRawAsync(cloud, url, """{"events":[]}""")).Status);
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await PostRawAsync(cloud, url, """{"events":[{"kind":"telemetry","t_ms":1}]}""")).Status);
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await PostRawAsync(cloud, url, """{"events":[{"kind":"speech_started","t_ms":1}]}""")).Status);
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await PostRawAsync(cloud, url, """{"events":[{"kind":"audio_frame","t_ms":1,"payload":{"audio":"AAAA"}}]}""")).Status);
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await PostRawAsync(cloud, url, "{\"events\":[{\"kind\":\"audio_frame\",\"t_ms\":1,\"payload\":{\"x\":\"" + new string('y', 5000) + "\"}}]}")).Status);
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await PostRawAsync(cloud, url, """{"events":[{"kind":"audio_frame","t_ms":-1}]}""")).Status);
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await PostRawAsync(cloud, url, """{"events":[{"kind":"audio_frame","t_ms":1,"extra":1}]}""")).Status);
        var tooMany = "{\"events\":[" + string.Join(",", Enumerable.Repeat("""{"kind":"audio_frame","t_ms":1}""", 201)) + "]}";
        Assert.Equal(HttpStatusCode.UnprocessableEntity, (await PostRawAsync(cloud, url, tooMany)).Status);
        var exactly200 = "{\"events\":[" + string.Join(",", Enumerable.Repeat("""{"kind":"audio_frame","t_ms":1}""", 200)) + "]}";
        Assert.Equal(HttpStatusCode.OK, (await PostRawAsync(cloud, url, exactly200)).Status);
        Assert.Equal(8, cloud.Refusals);
    }

    // ---------------------------------------------------------- /sessions shape

    [Fact]
    public async Task Create_refuses_the_old_request_shape_and_returns_the_documented_credential()
    {
        using var cloud = new InProcessFakeCloudCore(Token);

        // The pre-ADR-0039 body: transport_preference/device_id/client_capabilities are not CreateSessionRequest fields.
        var old = await PostRawAsync(cloud, RealtimeContract.SessionsPath,
            """{"client_kind":"windows-companion","language":"tr-TR","transport_preference":["websocket"],"device_id":"dev-1","client_capabilities":{"barge_in":true}}""");
        Assert.Equal(HttpStatusCode.UnprocessableEntity, old.Status);
        Assert.Contains("transport_preference", Detail(old.Body));

        // client_kind has a pattern; a hyphen is outside it
        var hyphen = await PostRawAsync(cloud, RealtimeContract.SessionsPath, """{"client_kind":"windows-companion"}""");
        Assert.Equal(HttpStatusCode.UnprocessableEntity, hyphen.Status);

        // a transport the provider does not offer: 422 naming the offered ones (routes.create_session)
        var webrtc = await PostRawAsync(cloud, RealtimeContract.SessionsPath, """{"client_kind":"windows_desktop","transport":"webrtc"}""");
        Assert.Equal(HttpStatusCode.UnprocessableEntity, webrtc.Status);
        Assert.Equal("websocket", webrtc.Body["detail"]!["transports"]![0]!.GetValue<string>());

        var ok = await PostRawAsync(cloud, RealtimeContract.SessionsPath, """{"client_kind":"windows_desktop","transport":"websocket"}""");
        Assert.Equal(HttpStatusCode.Created, ok.Status);
        Assert.Equal(new[] { "session_id", "provider", "transport", "credential", "tools", "instructions", "language", "expires_at", "state" }, ok.Body.Select(p => p.Key).ToArray());
        Assert.Equal(new[] { "provider", "secret", "expires_at", "transport", "session_ref" }, ok.Body["credential"]!.AsObject().Select(p => p.Key).ToArray());
        Assert.True(Guid.TryParse(ok.Body["session_id"]!.GetValue<string>(), out _));
        Assert.Equal("created", ok.Body["state"]!.GetValue<string>());
        var research = ok.Body["tools"]!.AsArray().Single(t => t!["name"]!.GetValue<string>() == "research");
        Assert.True(research!["long_running"]!.GetValue<bool>());
        Assert.StartsWith("Bakıyorum", research["preamble"]!.GetValue<string>());
    }

    [Fact]
    public async Task The_typed_client_never_produces_a_request_the_fake_refuses()
    {
        using var cloud = new InProcessFakeCloudCore(Token);
        var client = cloud.CreateSidebandClient();
        var grant = await client.CreateSessionAsync(new CreateSessionRequest("windows_desktop", "websocket"), CancellationToken.None);
        await client.RelayToolCallAsync(grant.SessionId, "call_1:abc", "echo", """{"q":1}""", CancellationToken.None);
        await client.RelayToolCallAsync(grant.SessionId, "call_2", "echo", "not json at all", CancellationToken.None);
        await client.ReportEventsAsync(grant.SessionId, new[]
        {
            new VoiceClientEventRecord(1, VoiceClientEvents.MicSpeechStart, 0, 1, new JsonObject { ["reason"] = "vad_onset", ["barge_in"] = false }),
            new VoiceClientEventRecord(2, VoiceClientEvents.Utterance, 5, 1, new JsonObject(), "ikinci maddeyi tekrar oku"),
        }, CancellationToken.None);
        await client.AttachAsync(grant.SessionId, "windows_desktop", "websocket", CancellationToken.None);
        Assert.Equal(0, cloud.Refusals);
        // a client_kind outside the pattern is caught on this side before it is ever sent
        Assert.Throws<ArgumentException>(() => new CreateSessionRequest("windows-companion", null).ToJson());
        Assert.Throws<ArgumentException>(() => new CreateSessionRequest("windows_desktop", "carrier_pigeon").ToJson());
    }

    // ------------------------------------------------------------ 409 / 410 / 404

    [Fact]
    public async Task Requests_after_another_client_took_the_leg_are_409_until_this_client_attaches_again()
    {
        // test_attach_moves_the_leg_replays_sideband_and_closes_the_old_leg, from the desktop's side.
        using var cloud = new InProcessFakeCloudCore(Token);
        var sid = await CreateAsync(cloud);
        var legClosed = cloud.SupersedeLeg(sid, "mobile");
        Assert.Equal("leg_closed", legClosed["event"]!.GetValue<string>());
        Assert.Equal("attached_elsewhere", legClosed["payload"]!["reason"]!.GetValue<string>());

        var stale = await PostRawAsync(cloud, $"{RealtimeContract.SessionsPath}/{sid}/events", """{"events":[{"kind":"end_of_turn","t_ms":5}]}""");
        Assert.Equal(HttpStatusCode.Conflict, stale.Status);
        Assert.Equal("mismatch", stale.Body["detail"]!["details"]!["leg"]!.GetValue<string>());
        var staleTool = await PostRawAsync(cloud, $"{RealtimeContract.SessionsPath}/{sid}/tool-calls", """{"call_id":"z","name":"echo"}""");
        Assert.Equal(HttpStatusCode.Conflict, staleTool.Status);
        Assert.Equal(0, cloud.ToolExecutions);

        cloud.QueueSideband(sid, SidebandPushKinds.ToolCompleted, new JsonObject { ["call_id"] = "r", ["status"] = "succeeded" });
        var attach = await PostRawAsync(cloud, $"{RealtimeContract.SessionsPath}/{sid}/attach", """{"client_kind":"windows_desktop"}""");
        Assert.Equal(HttpStatusCode.OK, attach.Status);
        Assert.Equal("mobile", attach.Body["previous_leg"]!["client_kind"]!.GetValue<string>());
        Assert.Equal(3, attach.Body["state"]!["legs"]!.GetValue<int>());
        Assert.Equal("tool_completed", attach.Body["pending_sideband"]![0]!["event"]!.GetValue<string>());

        var ok = await PostRawAsync(cloud, $"{RealtimeContract.SessionsPath}/{sid}/events", """{"events":[{"kind":"end_of_turn","t_ms":6}]}""");
        Assert.Equal(HttpStatusCode.OK, ok.Status);
        Assert.Equal(0, cloud.Refusals);
    }

    [Fact]
    public async Task A_closed_session_answers_410_and_an_unknown_one_404()
    {
        // test_close_and_expiry_refuse_further_work, mirrored.
        using var cloud = new InProcessFakeCloudCore(Token);
        var sid = await CreateAsync(cloud);
        var close = await PostRawAsync(cloud, $"{RealtimeContract.SessionsPath}/{sid}/close", """{"reason":"client_closed"}""");
        Assert.Equal(HttpStatusCode.OK, close.Status);
        Assert.Equal("closed", close.Body["state"]!.GetValue<string>());

        foreach (var (path, body) in new[]
                 {
                     ("events", """{"events":[{"kind":"end_of_turn","t_ms":5}]}"""),
                     ("tool-calls", """{"call_id":"c","name":"echo"}"""),
                     ("attach", """{"client_kind":"windows_desktop"}"""),
                 })
        {
            var gone = await PostRawAsync(cloud, $"{RealtimeContract.SessionsPath}/{sid}/{path}", body);
            Assert.Equal(HttpStatusCode.Gone, gone.Status);
            Assert.Equal("closed", gone.Body["detail"]!["details"]!["state"]!.GetValue<string>());
        }

        var unknown = await PostRawAsync(cloud, $"{RealtimeContract.SessionsPath}/{Guid.NewGuid()}/events", """{"events":[{"kind":"end_of_turn","t_ms":5}]}""");
        Assert.Equal(HttpStatusCode.NotFound, unknown.Status);
        var badId = await PostRawAsync(cloud, $"{RealtimeContract.SessionsPath}/rts-nope/events", """{"events":[{"kind":"end_of_turn","t_ms":5}]}""");
        Assert.Equal(HttpStatusCode.UnprocessableEntity, badId.Status);
    }

    // ---------------------------------------------------- the reporter's verdict handling

    [Fact]
    public async Task The_reporter_drops_a_refused_batch_loudly_and_keeps_delivering_later_events()
    {
        using var cloud = new InProcessFakeCloudCore(Token);
        var client = cloud.CreateSidebandClient();
        var grant = await client.CreateSessionAsync(new CreateSessionRequest("windows_desktop", "websocket"), CancellationToken.None);
        var time = new Timing.ManualTimeProvider();
        var reporter = new VoiceEventReporter(client, grant.SessionId, time, 0);

        // The typed client surfaces a 422 as exactly that (here: a text the local check truncates but a raw record can overflow).
        var overlong = new VoiceClientEventRecord(1, VoiceClientEvents.Summary, 0, 0, new JsonObject(), new string('x', RealtimeContract.MaxEventTextChars + 1));
        var refused = await Assert.ThrowsAsync<SidebandException>(() => client.ReportEventsAsync(grant.SessionId, new[] { overlong }, CancellationToken.None));
        Assert.True(refused.IsPayloadRefused);
        Assert.False(refused.Transient);
        Assert.Contains("string_too_long", refused.Body);

        // The reporter: a batch the server refuses (a rule this client does not know = contract
        // drift) is dropped and counted, never retried, and the next event still goes out —
        // a refused head must not silently block every later event.
        reporter.Enqueue(VoiceClientEvents.Summary, null, "birinci");
        cloud.RefuseNextEventBatches(1);
        await reporter.FlushAsync(CancellationToken.None);
        Assert.Equal(1, reporter.RefusedBatches);
        Assert.Contains("simulated_contract_drift", reporter.LastRefusal);
        Assert.Equal(0, reporter.PendingCount);
        Assert.Empty(reporter.Delivered);
        Assert.True(reporter.Online);

        await reporter.ReportAsync(VoiceClientEvents.Summary, null, "ikinci", CancellationToken.None);
        Assert.Single(reporter.Delivered);
        Assert.Equal(new[] { "summary" }, cloud.EventKindsFor(grant.SessionId));
        Assert.Equal("ikinci", cloud.EventsFor(grant.SessionId)[0]["text"]!.GetValue<string>());
    }

    [Fact]
    public async Task The_reporter_reports_a_stale_leg_and_a_gone_session_to_its_owner_instead_of_retrying()
    {
        using var cloud = new InProcessFakeCloudCore(Token);
        var client = cloud.CreateSidebandClient();
        var grant = await client.CreateSessionAsync(new CreateSessionRequest("windows_desktop", "websocket"), CancellationToken.None);
        var faults = new List<SidebandFault>();
        var reporter = new VoiceEventReporter(client, grant.SessionId, new Timing.ManualTimeProvider(), 0, onFault: faults.Add);

        cloud.SupersedeLeg(grant.SessionId);
        await reporter.ReportAsync(VoiceClientEvents.EndOfTurn, null, CancellationToken.None);
        Assert.Equal(new[] { SidebandFault.StaleLeg }, faults);
        Assert.False(reporter.Online);
        Assert.Equal(1, reporter.PendingCount); // held, not dropped: it is delivered after re-attach
        Assert.Equal(1, reporter.StaleLegRefusals);

        await client.AttachAsync(grant.SessionId, "windows_desktop", null, CancellationToken.None);
        reporter.SetOnline(true);
        await reporter.FlushAsync(CancellationToken.None);
        Assert.Equal(0, reporter.PendingCount);

        cloud.CloseSession(grant.SessionId);
        await reporter.ReportAsync(VoiceClientEvents.EndOfTurn, null, CancellationToken.None);
        Assert.Equal(new[] { SidebandFault.StaleLeg, SidebandFault.SessionGone }, faults);
        Assert.False(reporter.Online);
    }

    // ------------------------------------------------------------ push frames

    [Fact]
    public void A_voice_sideband_frame_parses_into_a_push_and_anything_else_is_ignored()
    {
        var frame = InProcessFakeCloudCore.Frame("sess-1", SidebandPushKinds.ToolCompleted, new JsonObject { ["call_id"] = "c", ["status"] = "succeeded" });
        var push = SidebandPush.TryParseFrame(frame)!;
        Assert.Equal("tool_completed", push.Kind);
        Assert.Equal("sess-1", push.SessionId);
        Assert.Equal("c", push.Payload["call_id"]!.GetValue<string>());

        Assert.Null(SidebandPush.TryParseFrame(new JsonObject { ["type"] = "command", ["command"] = new JsonObject() }));
        Assert.Null(SidebandPush.TryParseFrame(new JsonObject { ["type"] = "voice_sideband", ["event"] = "say" }));
        Assert.Null(SidebandPush.TryParseFrame(new JsonObject { ["type"] = "voice_sideband", ["payload"] = new JsonObject() }));

        var source = new PipeSidebandPushSource(capacity: 2);
        source.Accept(frame);
        source.Accept(new JsonObject { ["type"] = "heartbeat_ack", ["seq"] = 1 });
        source.Accept(InProcessFakeCloudCore.Frame("sess-1", SidebandPushKinds.Say, new JsonObject { ["text"] = "a" }));
        source.Accept(InProcessFakeCloudCore.Frame("sess-1", SidebandPushKinds.Say, new JsonObject { ["text"] = "b" }));
        Assert.Equal(3, source.Accepted);
        Assert.Equal(1, source.Malformed);
        // bounded: the oldest (tool_completed) was dropped to make room; Cloud Core's backlog covers it
        Assert.True(source.Pushes.TryRead(out var first));
        Assert.Equal("say", first!.Kind);
        Assert.Equal("a", first.Payload["text"]!.GetValue<string>());
        Assert.True(source.Pushes.TryRead(out var second));
        Assert.Equal("b", second!.Payload["text"]!.GetValue<string>());
        Assert.False(source.Pushes.TryRead(out _));
    }
}
