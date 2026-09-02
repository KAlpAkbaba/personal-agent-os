using System.Threading.Channels;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Sideband;

namespace PagentOS.Companion.Audio.Media;

/// <summary>
/// Reserved adapter for the WebRTC leg. Not implemented in M12/C: the evaluated managed
/// stack (SIPSorcery for ICE/DTLS-SRTP/data channel plus an Opus codec, Concentus or a
/// native encoder) cannot be validated without a live provider, and the WebSocket leg
/// reaches the same session contract today. The seam is <see cref="IMediaLeg"/>; when this
/// class is real, nothing above it changes. <see cref="MediaLegFactory"/> refuses a
/// "webrtc" grant loudly instead of silently downgrading, and the client asks Cloud Core for
/// "websocket" in its transport preference until then.
/// </summary>
public sealed class WebRtcMediaLeg : IMediaLeg
{
    public const string TransportName = "webrtc";

    public static bool IsAvailable => false;

    public string Transport => TransportName;

    public bool IsOpen => false;

    public ChannelReader<ProviderEvent> Events { get; } = Channel.CreateUnbounded<ProviderEvent>().Reader;

    public Task OpenAsync(RealtimeSessionGrant grant, MediaLegOptions options, CancellationToken cancellationToken)
        => throw Deferred();

    public ValueTask SendAudioAsync(AudioFrame frame, CancellationToken cancellationToken) => throw Deferred();

    public Task CommitTurnAsync(CancellationToken cancellationToken) => throw Deferred();

    public Task CancelResponseAsync(CancellationToken cancellationToken) => throw Deferred();

    public Task SubmitToolResultAsync(string callId, string outputJson, bool final, bool followUp, CancellationToken cancellationToken)
        => throw Deferred();

    public Task SayAsync(string text, CancellationToken cancellationToken) => throw Deferred();

    public Task CloseAsync(CancellationToken cancellationToken) => Task.CompletedTask;

    public ValueTask DisposeAsync() => ValueTask.CompletedTask;

    private static NotSupportedException Deferred()
        => new("the WebRTC media leg is deferred (M12 track C, see docs/DECISIONS.md); use the websocket transport");
}

public static class MediaLegFactory
{
    /// <summary>Transports this build can actually open, in preference order, for the session request.</summary>
    public static IReadOnlyList<string> SupportedTransports { get; } = WebRtcMediaLeg.IsAvailable
        ? new[] { WebRtcMediaLeg.TransportName, WebSocketMediaLeg.TransportName }
        : new[] { WebSocketMediaLeg.TransportName };

    public static IMediaLeg Create(string transport, IProviderWireCodec codec, TimeProvider time, Microsoft.Extensions.Logging.ILogger? logger = null)
    {
        return transport switch
        {
            WebSocketMediaLeg.TransportName => new WebSocketMediaLeg(codec, time, logger),
            WebRtcMediaLeg.TransportName when WebRtcMediaLeg.IsAvailable => new WebRtcMediaLeg(),
            WebRtcMediaLeg.TransportName => throw new NotSupportedException(
                "Cloud Core granted a webrtc session but this build cannot open one; request websocket"),
            _ => throw new NotSupportedException($"unknown media transport '{transport}'"),
        };
    }
}
