using System.Runtime.Versioning;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Listening;
using PagentOS.Companion.Audio.Listening.Spotting;
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

/// <summary>
/// What the Session Companion hands the voice host for B47: the pieces that live in the
/// companion (the tray indicator, the alarm-backed command sink) and where the owner's
/// listening choice and keyword templates are kept.
/// </summary>
public sealed record DeviceVoiceComposition(
    DeviceVoiceHealth Health,
    IPrivacyIndicator Indicator,
    IOfflineCommandSink? Commands,
    string DataDir,
    int PushToTalkVirtualKey = Win32PushToTalkKey.RightControl,
    Action<DeviceListeningService>? OnListening = null);

/// <summary>
/// Production composition: real WASAPI devices, the OpenAI wire codec over WebSocket, HTTP
/// sideband with the DPAPI-stored owner token, and the REAL push source — the
/// <c>voice_sideband</c> frames the Device Service forwards over the named pipe
/// (ADR-0039).
///
/// B47 (owner decision 2026-09-16): the microphone belongs to the
/// <see cref="DeviceListeningService"/>, which runs for the life of the companion and admits
/// only what its local detector, the wake word or the push-to-talk key let through. The
/// realtime session sees the gate's tap, never the raw stream, and is replaced after a backoff
/// whenever it ends (<see cref="DeviceVoiceHost"/>). Before B47 this host gave the orchestrator
/// the microphone directly with <c>StreamWhileIdle</c> on, so every captured frame - silence
/// included - went to the provider while voice was enabled.
/// </summary>
[SupportedOSPlatform("windows")]
public static class VoiceCompanionHost
{
    public static async Task RunAsync(
        VoiceCompanionOptions options,
        ILogger logger,
        AuditLog? audit,
        ISidebandPushSource pushes,
        DeviceVoiceComposition device,
        CancellationToken cancellationToken)
    {
        if (!options.Enabled || options.CloudCoreUrl is null)
        {
            device.Health.SetState(DeviceVoiceContract.StateDisabled);
            logger.LogInformation("voice: disabled (set PAGENTOS_AGENT_VoiceEnabled=true and PAGENTOS_AGENT_CloudCoreUrl to enable)");
            return;
        }

        var tokens = new DpapiOwnerSessionTokenSource();
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

        if (options.EndOfTurn != EndOfTurnMode.Client)
        {
            // The gate stops sending when the utterance ends, so a provider-side detector would
            // wait for silence that never arrives; the client commits the turn instead.
            logger.LogInformation("voice: device listening commits turns on the client (VoiceEndOfTurn={Configured} is overridden)", options.EndOfTurn);
        }

        var clientOptions = ClientOptionsFor(options);

        var format = clientOptions.Format;
        var templates = new FileKeywordTemplateStore(
            FileKeywordTemplateStore.DefaultPath(device.DataDir),
            DpapiSecretStore.ProtectBytes,
            DpapiSecretStore.UnprotectBytes,
            logger);
        var listening = new DeviceListeningService(
            new DeviceListeningOptions { Format = format, PreferredCaptureDeviceId = options.PreferredCaptureDeviceId },
            catalog,
            devices,
            new FileListeningSettingsStore(FileListeningSettingsStore.DefaultPath(device.DataDir), logger),
            new WasapiMuteMonitor(catalog),
            new Win32PushToTalkKey(device.PushToTalkVirtualKey),
            BuildSpotter(templates.Load(), format),
            device.Indicator,
            device.Commands,
            device.Health,
            TimeProvider.System,
            logger,
            audit);
        device.OnListening?.Invoke(listening);

        // An enrollment (--voice-enroll, run by the owner) writes a new template file; the
        // running listener picks it up without a restart.
        using var reloadCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        var reload = WatchTemplatesAsync(templates, format, listening, logger, reloadCts.Token);

        var host = new DeviceVoiceHost(
            listening,
            device.Health,
            gated => new VoiceSessionOrchestrator(clientOptions, catalog, gated, LegFor, sideband, pushes, TimeProvider.System, logger, audit),
            tokens,
            devices,
            TimeProvider.System,
            logger);
        try
        {
            await host.RunAsync(cancellationToken).ConfigureAwait(false);
        }
        finally
        {
            reloadCts.Cancel();
            await reload.ConfigureAwait(false);
        }
    }

    private static async Task WatchTemplatesAsync(FileKeywordTemplateStore templates, AudioFormat format, DeviceListeningService listening, ILogger logger, CancellationToken ct)
    {
        DateTime Stamp() => File.Exists(templates.Path) ? File.GetLastWriteTimeUtc(templates.Path) : DateTime.MinValue;
        var last = Stamp();
        try
        {
            while (!ct.IsCancellationRequested)
            {
                await Task.Delay(TimeSpan.FromSeconds(5), ct).ConfigureAwait(false);
                var now = Stamp();
                if (now == last)
                {
                    continue;
                }

                last = now;
                var spotter = BuildSpotter(templates.Load(), format);
                listening.ReplaceSpotter(spotter);
                logger.LogInformation("voice: keyword templates reloaded; available [{Phrases}]", string.Join(",", spotter.AvailablePhrases.Order(StringComparer.Ordinal)));
            }
        }
        catch (OperationCanceledException)
        {
        }
    }

    /// <summary>
    /// The realtime client's options on a listening device. Two are not negotiable: the client
    /// never streams between turns (the gate has already withheld that audio, and this keeps it
    /// withheld even if a tap ever delivered some), and the client commits each turn itself,
    /// because the gate stops sending at the end of an utterance and a provider-side detector
    /// would wait for silence that never arrives.
    /// </summary>
    public static VoiceClientOptions ClientOptionsFor(VoiceCompanionOptions options) => new()
    {
        EndOfTurn = EndOfTurnMode.Client,
        StreamWhileIdle = false,
        DeviceId = options.DeviceId,
        PreferredCaptureDeviceId = options.PreferredCaptureDeviceId,
        PreferredRenderDeviceId = options.PreferredRenderDeviceId,
    };

    /// <summary>The enrolled templates as an engine, or an honest "nothing enrolled".</summary>
    public static IKeywordSpotter BuildSpotter(KeywordTemplateSet? set, AudioFormat format)
    {
        if (set is null)
        {
            return new NoKeywordSpotter("nothing is enrolled (PagentOS.SessionCompanion.exe --voice-enroll)", format.SampleRate);
        }

        if (set.SampleRate != format.SampleRate)
        {
            return new NoKeywordSpotter($"templates were enrolled at {set.SampleRate} Hz; the microphone runs at {format.SampleRate} Hz", format.SampleRate);
        }

        return new TemplateKeywordSpotter(set);
    }
}
