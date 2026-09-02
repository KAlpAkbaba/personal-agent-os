using System.Diagnostics;
using System.Text.Json.Nodes;
using PagentOS.Companion.Audio.Fakes;
using PagentOS.Companion.Audio.Media;
using PagentOS.Companion.Audio.Session;
using PagentOS.Companion.Audio.Sideband;
using PagentOS.Companion.Audio.Testing;
using PagentOS.Companion.Audio.Timing;
using Xunit;

namespace PagentOS.Companion.Audio.Tests;

public sealed class SidebandTests
{
    private const string Token = "pagentos_sess_test_token";

    private static CreateSessionRequest Create() => new("windows_desktop", "websocket");

    [Fact]
    public void A_grant_is_parsed_from_the_servers_shape_and_its_credential_never_reaches_the_audit_shape()
    {
        // The exact body of POST /v1/voice/realtime/sessions (service._leg_payload + EphemeralCredential.to_client_dict).
        var body = JsonNode.Parse("""
            {"session_id":"7f4c2a1e-0000-4000-8000-000000000001","provider":"openai-realtime","transport":"websocket",
             "credential":{"provider":"openai-realtime","secret":"ek_abc","expires_at":"2026-09-02T12:10:00Z",
                           "transport":"websocket","session_ref":"sess_x",
                           "transport_descriptor":{"transport":"websocket","websocket_url":"wss://api.openai.com/v1/realtime","query":{"model":"gpt-realtime"}}},
             "tools":[{"name":"clock.now","description":"","parameters":{"type":"object"},"long_running":false}],
             "instructions":"Türkçe konuş","language":"tr-TR","expires_at":"2026-09-02T13:00:00Z","state":"created"}
            """)!.AsObject();

        var grant = RealtimeSessionGrant.Parse(body);

        Assert.Equal("7f4c2a1e-0000-4000-8000-000000000001", grant.SessionId);
        Assert.Equal("ek_abc", grant.CredentialSecret());
        Assert.Equal("wss://api.openai.com/v1/realtime", grant.TransportDescriptor!["websocket_url"]!.GetValue<string>());
        Assert.Single(grant.Tools!);
        Assert.Equal("tr-TR", grant.Language);
        Assert.Equal("created", grant.State);
        Assert.Equal(new DateTimeOffset(2026, 9, 2, 12, 10, 0, TimeSpan.Zero), grant.CredentialExpiresAt);
        Assert.DoesNotContain("ek_abc", grant.ToAuditJson().ToJsonString());
    }

    [Fact]
    public void A_credential_that_is_not_the_documented_object_is_refused_not_guessed()
    {
        // Before ADR-0039 the client accepted value/client_secret/token/key/ephemeral_key/a bare string.
        // The contract has exactly one key, `secret`; anything else is a server the client does not know.
        Assert.Throws<FormatException>(() => RealtimeSessionGrant.Parse(JsonNode.Parse("""{"session_id":"s","provider":"p","transport":"websocket","credential":"ek_plain"}""")!.AsObject()));
        Assert.Throws<FormatException>(() => RealtimeSessionGrant.Parse(JsonNode.Parse("""{"session_id":"s","provider":"p","transport":"websocket","credential":{"value":"ek"}}""")!.AsObject()));
        Assert.Throws<FormatException>(() => RealtimeSessionGrant.Parse(JsonNode.Parse("""{"session_id":"s","provider":"p","transport":"websocket","credential":{"client_secret":{"value":"ek"}}}""")!.AsObject()));
        Assert.Throws<FormatException>(() => RealtimeSessionGrant.Parse(JsonNode.Parse("""{"provider":"p","transport":"t","credential":{"secret":"x"}}""")!.AsObject()));
        Assert.Throws<FormatException>(() => RealtimeSessionGrant.Parse(JsonNode.Parse("""{"session_id":"s","provider":"p","transport":"t"}""")!.AsObject()));
        // the simulator omits transport_descriptor entirely; that is a valid grant
        var simulator = RealtimeSessionGrant.Parse(JsonNode.Parse("""{"session_id":"s","provider":"simulator","transport":"simulated","credential":{"provider":"simulator","secret":"sim","expires_at":"2026-09-02T12:10:00Z","transport":"simulated","session_ref":"r"}}""")!.AsObject());
        Assert.Null(simulator.TransportDescriptor);
    }

    [Fact]
    public void Tool_results_become_the_function_output_shape_the_provider_expects()
    {
        Assert.Equal("""{"status":"running","preamble":"Bakıyorum."}""", new ToolCallRelayResult("c", "research.start", "running", true, false, null, null, "Bakıyorum.").OutputJson());
        Assert.Equal("""{"error":{"error_class":"x"}}""", new ToolCallRelayResult("c", "t", "failed", false, false, null, new JsonObject { ["error_class"] = "x" }, null).OutputJson());
        Assert.Equal("""{"a":1}""", new ToolCallRelayResult("c", "t", "succeeded", false, false, JsonNode.Parse("""{"a":1}"""), null, null).OutputJson());
        Assert.Equal("""{"result":null}""", new ToolCallRelayResult("c", "t", "succeeded", false, false, null, null, null).OutputJson());
        // the server's failed-tool shape parses into Error with error_class, as _tool_row_payload writes it
        var failed = ToolCallRelayResult.Parse(JsonNode.Parse("""{"call_id":"c","name":"nope","status":"failed","long_running":false,"replayed":false,"error":{"error_class":"capability_missing","message":"unknown tool 'nope'","available":["clock.now"]}}""")!.AsObject(), "c");
        Assert.False(failed.IsRunning);
        Assert.Equal("capability_missing", failed.Error!["error_class"]!.GetValue<string>());
    }

    [Fact]
    public async Task The_http_client_creates_a_session_relays_a_tool_call_and_reattaches()
    {
        using var cloud = new InProcessFakeCloudCore(Token);
        var client = cloud.CreateSidebandClient();

        var grant = await client.CreateSessionAsync(Create(), CancellationToken.None);
        Assert.True(Guid.TryParse(grant.SessionId, out _));
        Assert.Equal("websocket", grant.Transport);
        Assert.NotEmpty(grant.CredentialSecret());
        Assert.Equal("created", grant.State);

        var echoed = await client.RelayToolCallAsync(grant.SessionId, "call-1", "echo", """{"x":1}""", CancellationToken.None);
        Assert.Equal(1, echoed.Result!["x"]!.GetValue<int>());
        Assert.False(echoed.IsRunning);
        Assert.Equal("succeeded", echoed.Status);
        Assert.False(echoed.Replayed);

        var running = await client.RelayToolCallAsync(grant.SessionId, "call-2", "research", "{}", CancellationToken.None);
        Assert.True(running.IsRunning);
        Assert.True(running.LongRunning);
        Assert.StartsWith("Bakıyorum", running.Preamble);

        var reattached = await client.AttachAsync(grant.SessionId, "windows_desktop", null, CancellationToken.None);
        Assert.Equal(grant.SessionId, reattached!.Grant.SessionId);
        Assert.NotEqual(grant.CredentialSecret(), reattached.Grant.CredentialSecret());
        Assert.Null(reattached.PreviousLeg); // same owner session: a reconnect, not a takeover
        Assert.Equal("active", reattached.State!["state"]!.GetValue<string>());
        Assert.Null(await client.AttachAsync(Guid.NewGuid().ToString(), "windows_desktop", null, CancellationToken.None));
        Assert.Equal(0, cloud.Refusals);
    }

    [Fact]
    public async Task A_wrong_owner_token_is_refused_with_401_and_a_missing_token_never_leaves_the_process()
    {
        using var cloud = new InProcessFakeCloudCore(Token);

        var bad = cloud.CreateSidebandClient("wrong");
        var ex = await Assert.ThrowsAsync<SidebandException>(() => bad.CreateSessionAsync(Create(), CancellationToken.None));
        Assert.Equal(401, ex.StatusCode);
        Assert.False(ex.Transient);

        var none = new CloudCoreSidebandClient(cloud.CreateClient(), new StaticOwnerSessionTokenSource(null));
        await Assert.ThrowsAsync<InvalidOperationException>(() => none.CreateSessionAsync(Create(), CancellationToken.None));
        Assert.Equal(1, cloud.Unauthorized);
    }

    [Fact]
    public async Task Events_are_posted_in_the_servers_batch_shape_and_nothing_else()
    {
        using var cloud = new InProcessFakeCloudCore(Token);
        var client = cloud.CreateSidebandClient();
        var grant = await client.CreateSessionAsync(Create(), CancellationToken.None);
        var record = new VoiceClientEventRecord(7, VoiceClientEvents.MicSpeechStart, 12, 1, new JsonObject { ["reason"] = "vad_onset" });

        var ack = await client.ReportEventsAsync(grant.SessionId, new[] { record }, CancellationToken.None);

        Assert.Equal(1, ack.Accepted);
        Assert.Equal("active", ack.State!["state"]!.GetValue<string>());
        var stored = Assert.Single(cloud.EventsFor(grant.SessionId));
        Assert.Equal(new[] { "kind", "t_ms", "turn", "payload" }, stored.Select(p => p.Key).ToArray());
        Assert.Equal("mic_speech_start", stored["kind"]!.GetValue<string>());
        Assert.Equal(12, stored["t_ms"]!.GetValue<long>());
        Assert.Equal(1, stored["turn"]!.GetValue<int>());
        Assert.Equal("vad_onset", stored["payload"]!["reason"]!.GetValue<string>());
        // client_seq is bookkeeping, never a wire field (the server model forbids extras)
        Assert.DoesNotContain("client_seq", record.ToJson().ToJsonString());
        Assert.Equal(0, cloud.Refusals);
    }

    [Fact]
    public async Task The_reporter_keeps_order_across_an_outage_and_resends_the_backlog_as_one_batch()
    {
        using var cloud = new InProcessFakeCloudCore(Token);
        var client = cloud.CreateSidebandClient();
        var grant = await client.CreateSessionAsync(Create(), CancellationToken.None);
        var time = new ManualTimeProvider();
        var reporter = new VoiceEventReporter(client, grant.SessionId, time, time.GetTimestamp());

        await reporter.ReportAsync(VoiceClientEvents.MicSpeechStart, null, CancellationToken.None);
        cloud.FailNextRequests(1);
        time.AdvanceMs(10);
        await reporter.ReportAsync(VoiceClientEvents.NetworkLost, new JsonObject { ["reason"] = "ws" }, CancellationToken.None);
        Assert.False(reporter.Online);
        Assert.Equal(1, reporter.PendingCount);
        Assert.Equal(1, reporter.DeliveryFailures);

        await reporter.ReportAsync(VoiceClientEvents.NetworkRestored, null, CancellationToken.None);
        Assert.Equal(2, reporter.PendingCount);

        reporter.SetOnline(true);
        await reporter.FlushAsync(CancellationToken.None);

        Assert.Equal(0, reporter.PendingCount);
        Assert.Equal(new[] { "mic_speech_start", "network_lost", "network_restored" }, cloud.EventKindsFor(grant.SessionId));
        Assert.Equal(new long[] { 1, 2, 3 }, reporter.Delivered.Select(e => e.ClientSeq));
        Assert.Equal(10, cloud.EventsFor(grant.SessionId)[1]["t_ms"]!.GetValue<long>());
        // Three requests for three events: one delivered, one refused with 503, then ONE batch carrying the two held back.
        Assert.Equal(3, cloud.EventRequests);
        Assert.Equal(3, reporter.BatchesSent);
        Assert.Equal(0, cloud.Refusals);
    }

    [Fact]
    public async Task The_reporter_refuses_kinds_and_payload_keys_the_server_would_refuse_before_posting_them()
    {
        using var cloud = new InProcessFakeCloudCore(Token);
        var reporter = new VoiceEventReporter(cloud.CreateSidebandClient(), Guid.NewGuid().ToString(), new ManualTimeProvider(), 0);
        // the pre-ADR-0039 vocabulary is gone: these are not kinds Cloud Core knows
        foreach (var stale in new[] { "made_up", "speech_started", "speech_ended", "mic_uplink", "barge_in", "tool_call_relayed", "tool_result_submitted" })
        {
            await Assert.ThrowsAsync<ArgumentException>(async () => await reporter.ReportAsync(stale, null, CancellationToken.None));
        }

        foreach (var spelling in new[] { "apiKey", "api-key", "API_KEY", "accessToken", "audioPcm", "context", "waveform" })
        {
            await Assert.ThrowsAsync<ArgumentException>(async () => await reporter.ReportAsync(VoiceClientEvents.Error, new JsonObject { [spelling] = "v" }, CancellationToken.None));
            await Assert.ThrowsAsync<ArgumentException>(async () => await reporter.ReportAsync(VoiceClientEvents.Error, new JsonObject { ["nested"] = new JsonObject { [spelling] = "v" } }, CancellationToken.None));
        }

        await Assert.ThrowsAsync<ArgumentException>(async () => await reporter.ReportAsync(VoiceClientEvents.Error, new JsonObject { ["blob"] = new string('x', 5000) }, CancellationToken.None));
        Assert.Equal(0, cloud.EventRequests);
    }

    private static (ToolCallRelay Relay, FakeMediaLeg Leg, RecordingEventReporter Reporter, VoiceClientStateMachine Fsm, InProcessFakeCloudCore Cloud, string SessionId) BuildRelay(InProcessFakeCloudCore cloud, IReadOnlyList<TimeSpan>? retries = null)
    {
        var time = TimeProvider.System;
        var client = cloud.CreateSidebandClient();
        var grant = client.CreateSessionAsync(Create(), CancellationToken.None).GetAwaiter().GetResult();
        var leg = new FakeMediaLeg(time);
        leg.OpenAsync(grant, new MediaLegOptions(Support.TestSupport.Format, Turn.EndOfTurnMode.Server), CancellationToken.None).GetAwaiter().GetResult();
        var reporter = new RecordingEventReporter(time);
        var fsm = new VoiceClientStateMachine(time);
        var relay = new ToolCallRelay(client, () => leg, reporter, fsm, time, grant.SessionId, retryDelays: retries ?? new[] { TimeSpan.Zero, TimeSpan.Zero });
        return (relay, leg, reporter, fsm, cloud, grant.SessionId);
    }

    [Fact]
    public async Task A_duplicate_provider_tool_call_event_is_relayed_once()
    {
        using var cloud = new InProcessFakeCloudCore(Token);
        var (relay, leg, reporter, fsm, _, _) = BuildRelay(cloud);
        var call = new ToolCallEvent(0, "call-1", "echo", """{"q":"merhaba"}""");

        var first = relay.HandleAsync(call, CancellationToken.None);
        var second = relay.HandleAsync(call, CancellationToken.None);
        await Task.WhenAll(first, second);

        Assert.Same(first, second);
        Assert.Equal(1, cloud.ToolCallRequests);
        Assert.Equal(1, cloud.ToolExecutions);
        var submitted = Assert.Single(leg.Commands.OfType<ToolResultCommand>());
        Assert.True(submitted.Final);
        Assert.False(submitted.FollowUp);
        Assert.Contains("merhaba", submitted.OutputJson);
        Assert.Equal(new[] { "tool_call" }, reporter.EventNames);
        Assert.Equal("succeeded", reporter.Reports.Single().Data["status"]!.GetValue<string>());
        Assert.Equal(new[] { "tool_call_started", "tool_call_finished" }, fsm.EventKinds());
    }

    [Fact]
    public async Task A_transient_failure_is_retried_with_the_same_call_id_and_executed_once()
    {
        using var cloud = new InProcessFakeCloudCore(Token);
        var (relay, leg, _, _, _, _) = BuildRelay(cloud);
        cloud.FailNextRequests(1);

        var result = await relay.HandleAsync(new ToolCallEvent(0, "call-9", "echo", "{}"), CancellationToken.None);

        Assert.Null(result.Error);
        Assert.Equal(2, cloud.ToolCallRequests);
        Assert.Equal(1, cloud.ToolExecutions);
        Assert.Single(leg.Commands.OfType<ToolResultCommand>());
    }

    [Fact]
    public async Task A_long_running_tool_gets_its_preamble_submitted_now_and_its_result_as_a_follow_up()
    {
        using var cloud = new InProcessFakeCloudCore(Token);
        var (relay, leg, reporter, fsm, _, _) = BuildRelay(cloud);

        var provisional = await relay.HandleAsync(new ToolCallEvent(0, "call-r", "research", "{}"), CancellationToken.None);

        Assert.True(provisional.IsRunning);
        Assert.Contains("call-r", relay.RunningCalls);
        var first = Assert.Single(leg.Commands.OfType<ToolResultCommand>());
        Assert.False(first.Final);
        Assert.Contains("Bakıyorum", first.OutputJson);
        Assert.Equal(VoiceClientState.ToolRunning, fsm.State);
        Assert.Equal("running", reporter.Reports.Single(r => r.Event == VoiceClientEvents.ToolCall).Data["status"]!.GetValue<string>());

        var completed = await relay.CompleteAsync("call-r", JsonNode.Parse("""{"summary":"tamam"}"""), null, CancellationToken.None);

        Assert.True(completed);
        Assert.Empty(relay.RunningCalls);
        var final = leg.Commands.OfType<ToolResultCommand>().Last();
        Assert.True(final.Final);
        Assert.True(final.FollowUp);
        Assert.Contains("tamam", final.OutputJson);
        Assert.Equal(VoiceClientState.Idle, fsm.State);
        Assert.False(await relay.CompleteAsync("call-r", null, null, CancellationToken.None));
        Assert.False(await relay.CompleteAsync("never", null, null, CancellationToken.None));
    }

    [Fact]
    public async Task An_unknown_tool_is_an_error_result_not_an_exception()
    {
        using var cloud = new InProcessFakeCloudCore(Token);
        var (relay, leg, _, fsm, _, _) = BuildRelay(cloud);

        var result = await relay.HandleAsync(new ToolCallEvent(0, "call-u", "no_such_tool", "{}"), CancellationToken.None);

        Assert.NotNull(result.Error);
        Assert.Equal("failed", result.Status);
        Assert.Equal("capability_missing", result.Error!["error_class"]!.GetValue<string>());
        Assert.Contains("\"error\"", leg.Commands.OfType<ToolResultCommand>().Single().OutputJson);
        Assert.Equal(VoiceClientState.Idle, fsm.State);
    }

    [Fact]
    public async Task A_tool_call_cloud_core_refuses_as_invalid_is_a_loud_client_bug_and_the_provider_still_gets_an_answer()
    {
        using var cloud = new InProcessFakeCloudCore(Token);
        var (relay, leg, _, fsm, _, _) = BuildRelay(cloud);

        // a forbidden key inside the model's arguments: the server says 422, the relay must not leave the provider hanging
        var result = await relay.HandleAsync(new ToolCallEvent(0, "call-bad", "echo", """{"apiKey":"leak"}"""), CancellationToken.None);

        Assert.Equal("failed", result.Status);
        Assert.Equal("validation_error", result.Error!["error_class"]!.GetValue<string>());
        Assert.Equal(1, relay.RefusedRelays);
        Assert.Equal(1, cloud.Refusals);
        Assert.Equal(1, cloud.ToolCallRequests); // never retried
        Assert.Contains("\"error\"", leg.Commands.OfType<ToolResultCommand>().Single().OutputJson);
        Assert.Equal(VoiceClientState.Idle, fsm.State);
    }

    // ---- the owner-session token: the existing DPAPI store, read from the owner's session ----

    [Fact]
    public void The_dpapi_secure_string_format_round_trips_including_Turkish_characters()
    {
        const string secret = "pagentos_ok_test_şğıİ_value";
        var hex = DpapiSecretStore.EncodeSecureString(secret);
        Assert.Matches("^[0-9a-f]+$", hex);
        Assert.Equal(secret, DpapiSecretStore.DecodeSecureString(hex));
        Assert.Equal(secret, DpapiSecretStore.DecodeSecureString(hex.ToUpperInvariant() + "\r\n"));
    }

    [Fact]
    public void The_token_source_reads_the_stored_file_and_returns_null_when_there_is_none()
    {
        var path = Path.Combine(Path.GetTempPath(), "pagentos-audio-tests", Guid.NewGuid().ToString("N"), "PAGENTOS_OWNER_SESSION_TOKEN.dpapi");
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        try
        {
            Assert.Null(new DpapiOwnerSessionTokenSource(path).GetToken());
            File.WriteAllText(path, DpapiSecretStore.EncodeSecureString("pagentos_sess_dummy") + Environment.NewLine);
            Assert.Equal("pagentos_sess_dummy", new DpapiOwnerSessionTokenSource(path).GetToken());
            File.WriteAllText(path, "not-hex");
            Assert.Null(new DpapiOwnerSessionTokenSource(path).GetToken());
        }
        finally
        {
            Directory.Delete(Path.GetDirectoryName(path)!, recursive: true);
        }
    }

    [Fact]
    public void The_default_path_is_the_secret_store_the_scripts_write()
    {
        var expected = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PagentOS", "secrets", "PAGENTOS_OWNER_SESSION_TOKEN.dpapi");
        Assert.Equal(expected, new DpapiOwnerSessionTokenSource().Path);
    }

    /// <summary>
    /// The real proof: what Windows PowerShell 5.1's ConvertFrom-SecureString writes, this
    /// code reads, and vice versa. Runs the actual powershell.exe on a throwaway value.
    /// </summary>
    [Fact]
    public void PowerShell_ConvertFrom_SecureString_output_is_readable_and_our_encoding_is_readable_by_PowerShell()
    {
        var powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
        if (!File.Exists(powershell))
        {
            return;
        }

        const string value = "dummy-not-a-secret-123";
        var fromPowerShell = RunPowerShell(powershell, $"ConvertTo-SecureString -String '{value}' -AsPlainText -Force | ConvertFrom-SecureString");
        Assert.Equal(value, DpapiSecretStore.DecodeSecureString(fromPowerShell));

        var ours = DpapiSecretStore.EncodeSecureString(value);
        var readBack = RunPowerShell(
            powershell,
            $"$s = '{ours}' | ConvertTo-SecureString; $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b)");
        Assert.Equal(value, readBack);
    }

    private static string RunPowerShell(string exe, string command)
    {
        var psi = new ProcessStartInfo(exe)
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        psi.ArgumentList.Add("-NoProfile");
        psi.ArgumentList.Add("-NonInteractive");
        psi.ArgumentList.Add("-Command");
        psi.ArgumentList.Add(command);
        using var process = Process.Start(psi)!;
        var output = process.StandardOutput.ReadToEnd();
        process.WaitForExit(30000);
        return output.Trim();
    }
}
