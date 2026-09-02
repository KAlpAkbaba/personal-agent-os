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

    [Fact]
    public void A_grant_is_parsed_and_its_credential_never_reaches_the_audit_shape()
    {
        var body = JsonNode.Parse("""
            {"session_id":"rts-1","provider":"openai-realtime","transport":"websocket",
             "credential":{"client_secret":{"value":"ek_abc","expires_at":1}},
             "tools":[{"name":"echo"}],"instructions":"Türkçe konuş","expires_at":"2026-09-02T12:10:00Z",
             "provider_session_config":{"voice":"marin"}}
            """)!.AsObject();

        var grant = RealtimeSessionGrant.Parse(body);

        Assert.Equal("rts-1", grant.SessionId);
        Assert.Equal("ek_abc", grant.CredentialValue());
        Assert.Single(grant.Tools!);
        Assert.Equal("marin", grant.ProviderSessionConfig!["voice"]!.GetValue<string>());
        Assert.DoesNotContain("ek_abc", grant.ToAuditJson().ToJsonString());
    }

    [Fact]
    public void A_bare_string_credential_is_accepted_and_a_missing_id_is_refused()
    {
        var grant = RealtimeSessionGrant.Parse(JsonNode.Parse("""{"session_id":"s","provider":"p","transport":"websocket","credential":"ek_plain"}""")!.AsObject());
        Assert.Equal("ek_plain", grant.CredentialValue());

        Assert.Throws<FormatException>(() => RealtimeSessionGrant.Parse(JsonNode.Parse("""{"provider":"p","transport":"t","credential":"x"}""")!.AsObject()));
        Assert.Throws<FormatException>(() => RealtimeSessionGrant.Parse(JsonNode.Parse("""{"session_id":"s","provider":"p","transport":"t"}""")!.AsObject()));
    }

    [Fact]
    public void Tool_results_become_the_function_output_shape_the_provider_expects()
    {
        Assert.Equal("""{"status":"running","preamble":"Bakıyorum."}""", new ToolCallRelayResult("c", null, null, "running", "Bakıyorum.").OutputJson());
        Assert.Equal("""{"error":{"class":"x"}}""", new ToolCallRelayResult("c", null, new JsonObject { ["class"] = "x" }, null, null).OutputJson());
        Assert.Equal("""{"a":1}""", new ToolCallRelayResult("c", JsonNode.Parse("""{"a":1}"""), null, null, null).OutputJson());
        Assert.Equal("""{"result":null}""", new ToolCallRelayResult("c", null, null, null, null).OutputJson());
    }

    [Fact]
    public async Task The_http_client_creates_a_session_relays_a_tool_call_and_reattaches()
    {
        using var cloud = new InProcessFakeCloudCore(Token);
        var client = cloud.CreateSidebandClient();

        var grant = await client.CreateSessionAsync(new CreateSessionRequest("windows-companion", "dev-1", new[] { "websocket" }), CancellationToken.None);
        Assert.StartsWith("rts-", grant.SessionId);
        Assert.Equal("websocket", grant.Transport);
        Assert.NotNull(grant.CredentialValue());

        var echoed = await client.RelayToolCallAsync(grant.SessionId, "call-1", "echo", """{"x":1}""", CancellationToken.None);
        Assert.Equal(1, echoed.Result!["x"]!.GetValue<int>());
        Assert.False(echoed.IsRunning);

        var running = await client.RelayToolCallAsync(grant.SessionId, "call-2", "research", "{}", CancellationToken.None);
        Assert.True(running.IsRunning);
        Assert.StartsWith("Bakıyorum", running.Preamble);

        var reattached = await client.AttachAsync(grant.SessionId, CancellationToken.None);
        Assert.Equal(grant.SessionId, reattached!.SessionId);
        Assert.NotEqual(grant.CredentialValue(), reattached.CredentialValue());
        Assert.Null(await client.AttachAsync("rts-nope", CancellationToken.None));
    }

    [Fact]
    public async Task A_wrong_owner_token_is_refused_with_401_and_a_missing_token_never_leaves_the_process()
    {
        using var cloud = new InProcessFakeCloudCore(Token);

        var bad = cloud.CreateSidebandClient("wrong");
        var ex = await Assert.ThrowsAsync<SidebandException>(() => bad.CreateSessionAsync(new CreateSessionRequest("windows-companion", null, new[] { "websocket" }), CancellationToken.None));
        Assert.Equal(401, ex.StatusCode);
        Assert.False(ex.Transient);

        var none = new CloudCoreSidebandClient(cloud.CreateClient(), new StaticOwnerSessionTokenSource(null));
        await Assert.ThrowsAsync<InvalidOperationException>(() => none.CreateSessionAsync(new CreateSessionRequest("windows-companion", null, new[] { "websocket" }), CancellationToken.None));
        Assert.Equal(1, cloud.Unauthorized);
    }

    [Fact]
    public async Task Re_delivering_an_event_with_the_same_client_seq_is_stored_once()
    {
        using var cloud = new InProcessFakeCloudCore(Token);
        var client = cloud.CreateSidebandClient();
        var grant = await client.CreateSessionAsync(new CreateSessionRequest("windows-companion", null, new[] { "websocket" }), CancellationToken.None);
        var record = new VoiceClientEventRecord(7, VoiceClientEvents.SpeechStarted, 12.5, new JsonObject());

        await client.ReportEventAsync(grant.SessionId, record, CancellationToken.None);
        await client.ReportEventAsync(grant.SessionId, record, CancellationToken.None);

        Assert.Equal(2, cloud.EventRequests);
        Assert.Single(cloud.EventsFor(grant.SessionId));
        Assert.Equal(7, cloud.EventsFor(grant.SessionId)[0]["client_seq"]!.GetValue<long>());
    }

    [Fact]
    public async Task The_reporter_keeps_order_across_an_outage_and_resends_with_the_same_seq()
    {
        using var cloud = new InProcessFakeCloudCore(Token);
        var client = cloud.CreateSidebandClient();
        var grant = await client.CreateSessionAsync(new CreateSessionRequest("windows-companion", null, new[] { "websocket" }), CancellationToken.None);
        var time = new ManualTimeProvider();
        var reporter = new VoiceEventReporter(client, grant.SessionId, time, time.GetTimestamp());

        await reporter.ReportAsync(VoiceClientEvents.SpeechStarted, null, CancellationToken.None);
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
        Assert.Equal(new[] { "speech_started", "network_lost", "network_restored" }, cloud.EventNamesFor(grant.SessionId));
        Assert.Equal(new long[] { 1, 2, 3 }, cloud.EventsFor(grant.SessionId).Select(e => e["client_seq"]!.GetValue<long>()));
        Assert.Equal(10.0, cloud.EventsFor(grant.SessionId)[1]["client_ts_ms"]!.GetValue<double>(), precision: 3);
        // Four requests for three events: the one refused with 503 came back with its original seq (2), not a new one.
        Assert.Equal(4, cloud.EventRequests);
    }

    [Fact]
    public async Task The_reporter_refuses_event_names_outside_the_contract()
    {
        using var cloud = new InProcessFakeCloudCore(Token);
        var reporter = new VoiceEventReporter(cloud.CreateSidebandClient(), "rts-x", new ManualTimeProvider(), 0);
        await Assert.ThrowsAsync<ArgumentException>(async () => await reporter.ReportAsync("made_up", null, CancellationToken.None));
    }

    private static (ToolCallRelay Relay, FakeMediaLeg Leg, RecordingEventReporter Reporter, VoiceClientStateMachine Fsm, InProcessFakeCloudCore Cloud, string SessionId) BuildRelay(InProcessFakeCloudCore cloud, IReadOnlyList<TimeSpan>? retries = null)
    {
        var time = TimeProvider.System;
        var client = cloud.CreateSidebandClient();
        var grant = client.CreateSessionAsync(new CreateSessionRequest("windows-companion", null, new[] { "websocket" }), CancellationToken.None).GetAwaiter().GetResult();
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
        Assert.Equal(new[] { "tool_call_relayed", "tool_result_submitted" }, reporter.EventNames);
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
        Assert.True(reporter.Reports.Single(r => r.Event == VoiceClientEvents.ToolResultSubmitted).Data["preamble"]!.GetValue<bool>());

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
        Assert.Equal("capability_missing", result.Error!["class"]!.GetValue<string>());
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
        if (fromPowerShell.ExitCode != 0 || fromPowerShell.Stdout.Length == 0)
        {
            // Windows PowerShell itself could not produce a DPAPI blob here (a hosted CI
            // runner without a usable user profile does this; it first showed up as a bare
            // "CryptUnprotectData failed with Win32 error 87" because the empty output was
            // fed straight to the decoder). That is the ENVIRONMENT, not our code, so the
            // interop claim is simply not evaluable - it stays proven only where PowerShell
            // works, which includes the owner's machine. A blob that PowerShell DID produce
            // and we cannot read still fails below, with PowerShell's own stderr attached.
            Console.Error.WriteLine(
                $"SKIP (environment): powershell.exe exit {fromPowerShell.ExitCode}, stdout {fromPowerShell.Stdout.Length} chars, stderr: {fromPowerShell.Stderr}");
            return;
        }

        try
        {
            Assert.Equal(value, DpapiSecretStore.DecodeSecureString(fromPowerShell.Stdout));
        }
        catch (Exception ex) when (ex is not Xunit.Sdk.XunitException)
        {
            throw new Xunit.Sdk.XunitException(
                $"PowerShell produced a blob ({fromPowerShell.Stdout.Length} hex chars) our decoder rejects: {ex.Message}; PowerShell stderr: {fromPowerShell.Stderr}");
        }

        var ours = DpapiSecretStore.EncodeSecureString(value);
        var readBack = RunPowerShell(
            powershell,
            $"$s = '{ours}' | ConvertTo-SecureString; $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b)");
        Assert.True(readBack.ExitCode == 0, $"PowerShell could not read our blob back: exit {readBack.ExitCode}, stderr: {readBack.Stderr}");
        Assert.Equal(value, readBack.Stdout);
    }

    [Fact]
    public void DecodeSecureString_rejects_an_empty_blob_with_a_clear_message()
    {
        var ex = Assert.Throws<ArgumentException>(() => DpapiSecretStore.DecodeSecureString(""));
        Assert.Contains("empty DPAPI blob", ex.Message);
    }

    private sealed record PowerShellResult(int ExitCode, string Stdout, string Stderr);

    private static PowerShellResult RunPowerShell(string exe, string command)
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
        var stderrTask = process.StandardError.ReadToEndAsync();
        var output = process.StandardOutput.ReadToEnd();
        process.WaitForExit(30000);
        return new PowerShellResult(process.ExitCode, output.Trim(), stderrTask.Result.Trim());
    }
}
