using PagentOS.Agent.Core.Connection;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Audio.Vad;
using PagentOS.Companion.Audio.Media;
using PagentOS.Companion.Audio.Turn;

namespace PagentOS.Companion.Audio.Orchestration;

public sealed record VoiceClientOptions
{
    public AudioFormat Format { get; init; } = AudioFormat.Pcm16Mono24k;

    /// <summary>Server (provider VAD ends turns; default per ADR-0034) or Client (this client commits turns).</summary>
    public EndOfTurnMode EndOfTurn { get; init; } = EndOfTurnMode.Server;

    public string ClientKind { get; init; } = "windows-companion";

    /// <summary>The enrolled device id, when known, so Cloud Core can device-bind the session.</summary>
    public string? DeviceId { get; init; }

    public string? PreferredCaptureDeviceId { get; init; }

    public string? PreferredRenderDeviceId { get; init; }

    public IReadOnlyList<string> TransportPreference { get; init; } = MediaLegFactory.SupportedTransports;

    public EnergyVadOptions Vad { get; init; } = new();

    public HesitationGuardOptions Hesitation { get; init; } = new();

    /// <summary>Keep the uplink flowing between turns so the provider's server VAD hears silence, not gaps.</summary>
    public bool StreamWhileIdle { get; init; } = true;

    public BackoffPolicy ReconnectBackoff { get; init; } = new(baseSeconds: 0.5, maxSeconds: 10);

    public int MaxReconnectAttempts { get; init; } = 20;

    /// <summary>Spec §6: silence during a running tool longer than this is a defect the harness reports.</summary>
    public int ToolSilenceBoundMs { get; init; } = 4000;

    /// <summary>Frame length the capture backends are asked for.</summary>
    public int FrameMs { get; init; } = 20;
}
