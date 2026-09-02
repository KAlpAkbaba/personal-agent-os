using System.Text.Json.Nodes;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Media;
using PagentOS.Companion.Audio.Media.OpenAi;
using PagentOS.Companion.Audio.Sideband;
using PagentOS.Companion.Audio.Tests.Support;
using PagentOS.Companion.Audio.Turn;
using Xunit;

namespace PagentOS.Companion.Audio.Tests;

public sealed class WebSocketLegTests
{
    private static RealtimeSessionGrant Grant(Uri uri) => new(
        "rts-ws", OpenAiRealtimeWireCodec.ProviderName, "websocket",
        new JsonObject
        {
            ["provider"] = OpenAiRealtimeWireCodec.ProviderName,
            ["secret"] = "ek_loopback",
            ["expires_at"] = "2026-09-02T12:10:00Z",
            ["transport"] = "websocket",
            ["session_ref"] = "sess_ref",
            ["transport_descriptor"] = new JsonObject { ["transport"] = "websocket", ["websocket_url"] = uri.ToString() },
        },
        null, "Türkçe konuş", "tr-TR", null, "created");

    private static async Task<T> NextAsync<T>(IMediaLeg leg, int timeoutMs = 5000)
        where T : ProviderEvent
    {
        using var cts = new CancellationTokenSource(timeoutMs);
        while (true)
        {
            var ev = await leg.Events.ReadAsync(cts.Token);
            if (ev is T typed)
            {
                return typed;
            }
        }
    }

    [Fact]
    public async Task The_leg_connects_with_the_bearer_configures_the_session_streams_audio_and_receives_a_response()
    {
        await using var server = await LoopbackProviderServer.StartAsync();
        await using var leg = new WebSocketMediaLeg(new OpenAiRealtimeWireCodec(), TimeProvider.System);

        await leg.OpenAsync(Grant(server.Uri), new MediaLegOptions(AudioFormat.Pcm16Mono24k, EndOfTurnMode.Client), CancellationToken.None);

        Assert.True(leg.IsOpen);
        Assert.Equal("Bearer ek_loopback", server.AuthorizationHeader);
        var ready = await NextAsync<SessionReadyEvent>(leg);
        Assert.Equal("sess_loopback", ready.ProviderSessionId);
        Assert.True(await TestSupport.WaitForAsync(() => server.SessionUpdate is not null));
        Assert.Null(server.SessionUpdate!["session"]!["turn_detection"]);
        Assert.Equal("Türkçe konuş", server.SessionUpdate["session"]!["instructions"]!.GetValue<string>());

        for (var i = 0; i < 5; i++)
        {
            await leg.SendAudioAsync(AudioFrame.Silence(AudioFormat.Pcm16Mono24k, 20, 0), CancellationToken.None);
        }

        await leg.CommitTurnAsync(CancellationToken.None);

        var started = await NextAsync<ResponseStartedEvent>(leg);
        Assert.Equal("resp_1", started.ResponseId);
        var delta = await NextAsync<AudioDeltaEvent>(leg);
        Assert.Equal(960, delta.Pcm16.Length);
        var done = await NextAsync<ResponseDoneEvent>(leg);
        Assert.Equal("completed", done.Status);
        Assert.True(await TestSupport.WaitForAsync(() => server.AudioAppends == 5));
        Assert.Equal(1, server.Commits);

        await leg.CancelResponseAsync(CancellationToken.None);
        var cancelled = await NextAsync<ResponseDoneEvent>(leg);
        Assert.Equal("cancelled", cancelled.Status);

        await leg.CloseAsync(CancellationToken.None);
        var disconnected = await NextAsync<DisconnectedEvent>(leg);
        Assert.Contains("closed", disconnected.Reason);
    }

    [Fact]
    public async Task A_provider_that_goes_away_surfaces_as_a_disconnect_event_and_sends_then_fail()
    {
        await using var server = await LoopbackProviderServer.StartAsync();
        await using var leg = new WebSocketMediaLeg(new OpenAiRealtimeWireCodec(), TimeProvider.System);
        await leg.OpenAsync(Grant(server.Uri), new MediaLegOptions(AudioFormat.Pcm16Mono24k, EndOfTurnMode.Server), CancellationToken.None);
        await NextAsync<SessionReadyEvent>(leg);

        await server.DropClientAsync();

        var disconnected = await NextAsync<DisconnectedEvent>(leg);
        Assert.StartsWith("closed_by_peer", disconnected.Reason);
        Assert.True(await TestSupport.WaitForAsync(() => !leg.IsOpen));
    }

    [Fact]
    public async Task A_leg_cannot_be_opened_twice_or_used_before_opening()
    {
        await using var server = await LoopbackProviderServer.StartAsync();
        await using var leg = new WebSocketMediaLeg(new OpenAiRealtimeWireCodec(), TimeProvider.System);
        await Assert.ThrowsAsync<InvalidOperationException>(() => leg.CommitTurnAsync(CancellationToken.None));

        await leg.OpenAsync(Grant(server.Uri), new MediaLegOptions(AudioFormat.Pcm16Mono24k, EndOfTurnMode.Server), CancellationToken.None);
        await Assert.ThrowsAsync<InvalidOperationException>(() => leg.OpenAsync(Grant(server.Uri), new MediaLegOptions(AudioFormat.Pcm16Mono24k, EndOfTurnMode.Server), CancellationToken.None));
    }
}
