using System.Text.Json.Nodes;
using PagentOS.Companion.Audio.Media;
using PagentOS.Companion.Audio.Media.OpenAi;
using PagentOS.Companion.Audio.Sideband;
using PagentOS.Companion.Audio.Turn;
using Xunit;

namespace PagentOS.Companion.Audio.Tests;

public sealed class CodecTests
{
    private static readonly OpenAiRealtimeWireCodec Codec = new();

    /// <summary>The credential exactly as Cloud Core's OpenAI adapter mints it (ADR-0038): secret + a non-secret transport descriptor.</summary>
    private static RealtimeSessionGrant Grant(string? url = null, JsonObject? query = null)
    {
        var credential = new JsonObject
        {
            ["provider"] = OpenAiRealtimeWireCodec.ProviderName,
            ["secret"] = "ek_secret",
            ["expires_at"] = "2026-09-02T12:10:00Z",
            ["transport"] = "websocket",
            ["session_ref"] = "sess_ref",
        };
        if (url is not null)
        {
            credential["transport_descriptor"] = new JsonObject
            {
                ["transport"] = "websocket",
                ["websocket_url"] = url,
                ["query"] = query,
                ["auth"] = "bearer_ephemeral_secret",
            };
        }

        return new RealtimeSessionGrant(
            "rts-1", OpenAiRealtimeWireCodec.ProviderName, "websocket", credential,
            new JsonArray(new JsonObject { ["type"] = "function", ["name"] = "echo" }),
            "Türkçe konuş", "tr-TR", null, "created");
    }

    [Fact]
    public void Connection_uses_the_credential_secret_as_a_bearer_and_the_transport_descriptor_for_where_to_connect()
    {
        var headers = Codec.ConnectHeaders(Grant());
        Assert.Equal("Bearer ek_secret", headers["Authorization"]);
        Assert.Equal(new Uri(OpenAiRealtimeWireCodec.DefaultEndpoint), Codec.ConnectUri(Grant()));
        Assert.Equal(new Uri("wss://x.example/rt"), Codec.ConnectUri(Grant("wss://x.example/rt")));
        // ADR-0038: the adapter's descriptor carries the model as a query, so no model name lives in this client.
        Assert.Equal(
            new Uri("wss://api.openai.com/v1/realtime?model=gpt-realtime"),
            Codec.ConnectUri(Grant("wss://api.openai.com/v1/realtime", new JsonObject { ["model"] = "gpt-realtime" })));
        Assert.Throws<InvalidOperationException>(() => Codec.ConnectHeaders(Grant() with { Credential = new JsonObject() }));
    }

    [Fact]
    public void Session_configure_forwards_instructions_tools_and_provider_config_and_turns_server_vad_off_in_client_mode()
    {
        var grant = Grant();
        var server = JsonNode.Parse(Codec.Encode(new SessionConfigureCommand(grant.Instructions, grant.Tools, EndOfTurnMode.Server, new JsonObject { ["voice"] = "marin" })).Single())!;
        Assert.Equal("session.update", server["type"]!.GetValue<string>());
        Assert.Equal("semantic_vad", server["session"]!["turn_detection"]!["type"]!.GetValue<string>());
        Assert.Equal("Türkçe konuş", server["session"]!["instructions"]!.GetValue<string>());
        Assert.Equal("echo", server["session"]!["tools"]![0]!["name"]!.GetValue<string>());
        Assert.Equal("marin", server["session"]!["voice"]!.GetValue<string>());
        Assert.Equal("pcm16", server["session"]!["input_audio_format"]!.GetValue<string>());
        Assert.DoesNotContain("model", server.ToJsonString());

        var client = JsonNode.Parse(Codec.Encode(new SessionConfigureCommand(null, null, EndOfTurnMode.Client, null)).Single())!;
        Assert.True(client["session"]!.AsObject().ContainsKey("turn_detection"));
        Assert.Null(client["session"]!["turn_detection"]);
    }

    [Fact]
    public void Audio_commit_cancel_say_and_tool_results_map_to_the_provider_events()
    {
        var append = JsonNode.Parse(Codec.Encode(new AppendAudioCommand(new byte[] { 1, 2, 3 })).Single())!;
        Assert.Equal("input_audio_buffer.append", append["type"]!.GetValue<string>());
        Assert.Equal(Convert.ToBase64String(new byte[] { 1, 2, 3 }), append["audio"]!.GetValue<string>());

        var commit = Codec.Encode(new CommitTurnCommand()).Select(m => JsonNode.Parse(m)!["type"]!.GetValue<string>()).ToArray();
        Assert.Equal(new[] { "input_audio_buffer.commit", "response.create" }, commit);

        Assert.Equal("response.cancel", JsonNode.Parse(Codec.Encode(new CancelResponseCommand()).Single())!["type"]!.GetValue<string>());

        var say = JsonNode.Parse(Codec.Encode(new SayCommand("Bakıyorum.")).Single())!;
        Assert.Equal("response.create", say["type"]!.GetValue<string>());
        Assert.Contains("Bakıyorum.", say["response"]!["instructions"]!.GetValue<string>());

        var output = Codec.Encode(new ToolResultCommand("call-1", """{"a":1}""", Final: true, FollowUp: false)).Select(m => JsonNode.Parse(m)!).ToArray();
        Assert.Equal("function_call_output", output[0]["item"]!["type"]!.GetValue<string>());
        Assert.Equal("call-1", output[0]["item"]!["call_id"]!.GetValue<string>());
        Assert.Equal("response.create", output[1]["type"]!.GetValue<string>());

        var followUp = Codec.Encode(new ToolResultCommand("call-1", """{"a":1}""", Final: true, FollowUp: true)).Select(m => JsonNode.Parse(m)!).ToArray();
        Assert.Equal("message", followUp[0]["item"]!["type"]!.GetValue<string>());
        Assert.Contains("call-1", followUp[0]["item"]!["content"]![0]!["text"]!.GetValue<string>());
    }

    [Fact]
    public void Provider_events_decode_to_neutral_events_and_unknown_ones_are_ignored()
    {
        var pcm = new byte[] { 9, 8, 7, 6 };
        Assert.IsType<SessionReadyEvent>(Codec.Decode("""{"type":"session.created","session":{"id":"sess_1"}}""", 1));
        Assert.IsType<InputSpeechStartedEvent>(Codec.Decode("""{"type":"input_audio_buffer.speech_started"}""", 1));
        Assert.IsType<InputSpeechStoppedEvent>(Codec.Decode("""{"type":"input_audio_buffer.speech_stopped"}""", 1));
        Assert.Equal("resp_1", ((ResponseStartedEvent)Codec.Decode("""{"type":"response.created","response":{"id":"resp_1"}}""", 1)!).ResponseId);

        var delta = (AudioDeltaEvent)Codec.Decode($$"""{"type":"response.audio.delta","response_id":"resp_1","delta":"{{Convert.ToBase64String(pcm)}}"}""", 42)!;
        Assert.Equal(pcm, delta.Pcm16);
        Assert.Equal(42, delta.ReceivedAt);
        Assert.IsType<AudioDeltaEvent>(Codec.Decode("""{"type":"response.output_audio.delta","response_id":"r","delta":""}""", 1));

        var done = (ResponseDoneEvent)Codec.Decode("""{"type":"response.done","response":{"id":"resp_1","status":"cancelled"}}""", 1)!;
        Assert.Equal("cancelled", done.Status);

        var tool = (ToolCallEvent)Codec.Decode("""{"type":"response.function_call_arguments.done","call_id":"call_1","name":"research","arguments":"{\"topic\":\"ai\"}"}""", 1)!;
        Assert.Equal("research", tool.Name);
        Assert.Equal("""{"topic":"ai"}""", tool.ArgumentsJson);

        var transcript = (TranscriptDeltaEvent)Codec.Decode("""{"type":"conversation.item.input_audio_transcription.completed","transcript":"dur"}""", 1)!;
        Assert.True(transcript.Final);
        Assert.Equal("dur", transcript.Text);
        Assert.False(((TranscriptDeltaEvent)Codec.Decode("""{"type":"conversation.item.input_audio_transcription.delta","delta":"şey"}""", 1)!).Final);

        var error = (ProviderErrorEvent)Codec.Decode("""{"type":"error","error":{"code":"rate_limit","message":"slow down"}}""", 1)!;
        Assert.Equal("rate_limit", error.Code);

        Assert.Null(Codec.Decode("""{"type":"rate_limits.updated"}""", 1));
        Assert.IsType<ProviderErrorEvent>(Codec.Decode("not json", 1));
        Assert.IsType<ProviderErrorEvent>(Codec.Decode("""{"no":"type"}""", 1));
    }

    [Fact]
    public void The_webrtc_leg_is_an_explicit_deferral_not_a_silent_downgrade()
    {
        Assert.False(WebRtcMediaLeg.IsAvailable);
        Assert.Equal(new[] { "websocket" }, MediaLegFactory.SupportedTransports);
        Assert.Throws<NotSupportedException>(() => MediaLegFactory.Create("webrtc", Codec, TimeProvider.System));
        Assert.Throws<NotSupportedException>(() => MediaLegFactory.Create("carrier-pigeon", Codec, TimeProvider.System));
        Assert.IsType<WebSocketMediaLeg>(MediaLegFactory.Create("websocket", Codec, TimeProvider.System));
    }
}
