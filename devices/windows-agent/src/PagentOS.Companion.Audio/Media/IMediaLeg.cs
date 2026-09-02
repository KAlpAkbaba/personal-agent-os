using System.Threading.Channels;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Sideband;
using PagentOS.Companion.Audio.Turn;

namespace PagentOS.Companion.Audio.Media;

public sealed record MediaLegOptions(AudioFormat Format, EndOfTurnMode EndOfTurn);

/// <summary>
/// The realtime media transport to the provider: audio up, audio and events down, a few
/// control intents. Implementations: <see cref="WebSocketMediaLeg"/> (real, PCM16 over the
/// provider's WebSocket), <see cref="WebRtcMediaLeg"/> (adapter reserved; not implemented
/// yet, see ADR), <see cref="Fakes.FakeMediaLeg"/> (deterministic, for tests and the bench).
/// The orchestrator never knows which one it holds.
/// </summary>
public interface IMediaLeg : IAsyncDisposable
{
    string Transport { get; }

    bool IsOpen { get; }

    ChannelReader<ProviderEvent> Events { get; }

    Task OpenAsync(RealtimeSessionGrant grant, MediaLegOptions options, CancellationToken cancellationToken);

    /// <summary>Uplink one frame. Returns when the bytes have been handed to the transport.</summary>
    ValueTask SendAudioAsync(AudioFrame frame, CancellationToken cancellationToken);

    Task CommitTurnAsync(CancellationToken cancellationToken);

    Task CancelResponseAsync(CancellationToken cancellationToken);

    Task SubmitToolResultAsync(string callId, string outputJson, bool final, bool followUp, CancellationToken cancellationToken);

    Task SayAsync(string text, CancellationToken cancellationToken);

    Task CloseAsync(CancellationToken cancellationToken);
}

public interface IProviderWireCodec
{
    string Provider { get; }

    AudioFormat AudioFormat { get; }

    Uri ConnectUri(RealtimeSessionGrant grant);

    IReadOnlyDictionary<string, string> ConnectHeaders(RealtimeSessionGrant grant);

    /// <summary>One command may become several wire messages (commit + response.create).</summary>
    IReadOnlyList<string> Encode(ProviderCommand command);

    /// <summary>Null for messages the client does not act on.</summary>
    ProviderEvent? Decode(string json, long receivedAt);
}
