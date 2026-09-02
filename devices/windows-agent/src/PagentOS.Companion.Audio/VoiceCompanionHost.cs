using System.Runtime.Versioning;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Companion.Audio.Media;
using PagentOS.Companion.Audio.Media.OpenAi;
using PagentOS.Companion.Audio.Orchestration;
using PagentOS.Companion.Audio.Sideband;
using PagentOS.Companion.Audio.Turn;
using PagentOS.Companion.Audio.Wasapi;

namespace PagentOS.Companion.Audio;

/// <summary>
/// What the Session Companion needs to decide about voice, parsed from its configuration by
/// a pure function so the default — OFF — is asserted by a test rather than assumed. Voice
/// is additive to the qualified companion: with <c>VoiceEnabled</c> unset, nothing in this
/// assembly runs.
/// </summary>
public sealed record VoiceCompanionOptions(
    bool Enabled,
    Uri? CloudCoreUrl,
    string? PreferredCaptureDeviceId,
    string? PreferredRenderDeviceId,
    EndOfTurnMode EndOfTurn,
    string? DeviceId)
{
    public static VoiceCompanionOptions Parse(
        string? enabled,
        string? cloudCoreUrl,
        string? preferredCapture,
        string? preferredRender,
        string? endOfTurn,
        string? deviceId,
        bool commandLineFlag = false)
    {
        var on = commandLineFlag || string.Equals(enabled?.Trim(), "true", StringComparison.OrdinalIgnoreCase) || enabled?.Trim() == "1";
        Uri? url = null;
        if (!string.IsNullOrWhiteSpace(cloudCoreUrl) && Uri.TryCreate(cloudCoreUrl.Trim(), UriKind.Absolute, out var parsed)
            && parsed.Scheme is "http" or "https")
        {
            url = parsed;
        }

        var mode = string.Equals(endOfTurn?.Trim(), "client", StringComparison.OrdinalIgnoreCase)
            ? EndOfTurnMode.Client
            : EndOfTurnMode.Server;
        return new VoiceCompanionOptions(
            on && url is not null,
            url,
            Blank(preferredCapture),
            Blank(preferredRender),
            mode,
            Blank(deviceId));
    }

    private static string? Blank(string? value) => string.IsNullOrWhiteSpace(value) ? null : value.Trim();
}

/// <summary>Production composition: real WASAPI devices, the OpenAI wire codec over WebSocket, HTTP sideband with the DPAPI-stored owner token.</summary>
[SupportedOSPlatform("windows")]
public static class VoiceCompanionHost
{
    public static async Task RunAsync(VoiceCompanionOptions options, ILogger logger, AuditLog? audit, CancellationToken cancellationToken)
    {
        if (!options.Enabled || options.CloudCoreUrl is null)
        {
            logger.LogInformation("voice: disabled (set PAGENTOS_AGENT_VoiceEnabled=true and PAGENTOS_AGENT_CloudCoreUrl to enable)");
            return;
        }

        var tokens = new DpapiOwnerSessionTokenSource();
        if (tokens.GetToken() is null)
        {
            logger.LogWarning("voice: no owner session token at {Path}; the sideband cannot authenticate, voice stays off", tokens.Path);
            return;
        }

        using var http = new HttpClient { BaseAddress = options.CloudCoreUrl, Timeout = TimeSpan.FromSeconds(30) };
        var sideband = new CloudCoreSidebandClient(http, tokens);
        using var catalog = new WasapiDeviceCatalog();
        var devices = new WasapiDeviceFactory(catalog, logger);
        var codecs = new Dictionary<string, IProviderWireCodec>(StringComparer.OrdinalIgnoreCase)
        {
            [OpenAiRealtimeWireCodec.ProviderName] = new OpenAiRealtimeWireCodec(),
            ["openai"] = new OpenAiRealtimeWireCodec(),
        };

        IMediaLeg LegFor(RealtimeSessionGrant grant)
        {
            if (!codecs.TryGetValue(grant.Provider, out var codec))
            {
                throw new NotSupportedException($"no wire codec for provider '{grant.Provider}' in this build");
            }

            return MediaLegFactory.Create(grant.Transport, codec, TimeProvider.System, logger);
        }

        var clientOptions = new VoiceClientOptions
        {
            EndOfTurn = options.EndOfTurn,
            DeviceId = options.DeviceId,
            PreferredCaptureDeviceId = options.PreferredCaptureDeviceId,
            PreferredRenderDeviceId = options.PreferredRenderDeviceId,
        };

        await using var orchestrator = new VoiceSessionOrchestrator(
            clientOptions, catalog, devices, LegFor, sideband, new NullSidebandPushSource(), TimeProvider.System, logger, audit);
        await orchestrator.RunAsync(cancellationToken).ConfigureAwait(false);
    }
}
