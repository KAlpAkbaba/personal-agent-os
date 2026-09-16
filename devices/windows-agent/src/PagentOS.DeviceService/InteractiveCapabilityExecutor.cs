using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.DeviceService;

/// <summary>
/// Routes interactive capabilities to the Session Companion via the named pipe. The Device
/// Service never touches the interactive desktop itself (CLAUDE.md Windows Agent rule).
///
/// Two families ride the same pipe (DEVICE_PROTOCOL.md §6, §6a, §6b):
/// <list type="bullet">
/// <item><c>desktop.*</c> — the M1/M3 open_* pair and the M18 alarm pair are always routed;
/// <c>desktop.display_off</c> is routed only when <c>DisplayPowerEnabled</c> is configured,
/// and is otherwise <c>capability_missing</c> before the companion is consulted, exactly like
/// a browser name on a device with no worker. Per-command cap 60 s (M1 behaviour, unchanged):
/// <c>alarm_start</c> arms a ramp and returns, it does not hold the pipe while the alarm
/// rings.</item>
/// <item><c>browser.*</c> — routed only when <c>BrowserEnabled</c> is configured AND the name
/// is one of the contract's operations (BROWSER_CAPABILITIES.md §1); anything else in the
/// namespace is refused here with <c>capability_missing</c> before the companion is
/// consulted, so an unknown operation never crosses the pipe. Per-command cap 120 s (§3),
/// because a real navigation plus extraction on a slow site is legitimately longer than
/// opening Notepad.</item>
/// <item>the Digital Operator family (M19, M19_DIGITAL_OPERATOR_SPEC.md §2) — routed only
/// when <c>OperatorEnabled</c> is configured, otherwise <c>capability_missing</c> before the
/// companion is consulted. Per-command cap 30 s (<c>app.launch</c> 15 s): every member acts
/// and re-observes within seconds, and a terminal command has its own 30 s ceiling.</item>
/// <item>the documents family (M20, M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §2) — routed under
/// the SAME <c>OperatorEnabled</c> gate (one trust decision: the companion may touch the
/// owner's files), otherwise <c>capability_missing</c> before the companion is consulted.
/// Per-command cap 30 s: a search is bounded at 10 s by contract and an extraction of a
/// 50 MiB document fits well inside the rest.</item>
/// <item>the projects family (M23, M23_APP_FACTORY_SPEC.md §3) — routed under the SAME
/// <c>OperatorEnabled</c> gate (writing source and starting a bounded child on the owner's
/// machine is the same trust decision), otherwise <c>capability_missing</c> before the
/// companion is consulted. Per-command cap 30 s (<c>project.run</c> answers once the port
/// does, within 20 s) and 5 min 30 s for <c>project.test</c>, whose bound is 5 min.</item>
/// </list>
/// The "no companion connected → <c>dependency_unavailable</c>, retryable" rule belongs to
/// the pipe server and applies to every family.
/// </summary>
public sealed class InteractiveCapabilityExecutor(
    ICompanionCapabilityTransport pipeServer,
    TimeProvider? timeProvider = null,
    bool browserEnabled = false,
    bool displayPowerEnabled = false,
    string? brokerRestUrl = null,
    bool operatorEnabled = false) : ICapabilityExecutor
{
    private static readonly TimeSpan MinTimeout = TimeSpan.FromSeconds(1);

    /// <summary>The M1 cap for the desktop family.</summary>
    public static readonly TimeSpan DesktopTimeoutCap = TimeSpan.FromSeconds(60);

    /// <summary>The M19 cap for the operator family (§3).</summary>
    public static readonly TimeSpan OperatorTimeoutCap = OperatorCapabilityNames.CommandTimeoutCap;

    /// <summary>The M19 cap for <c>app.launch</c> alone: a 10 s window wait plus headroom.</summary>
    public static readonly TimeSpan OperatorLaunchTimeoutCap = OperatorCapabilityNames.LaunchTimeoutCap;

    /// <summary>The M20 cap for the documents family (§2 bounds: a 10 s search, a bounded extraction).</summary>
    public static readonly TimeSpan DocumentsTimeoutCap = DocumentCapabilityNames.CommandTimeoutCap;

    /// <summary>The M23 cap for the projects family (scaffold, run, status, stop).</summary>
    public static readonly TimeSpan ProjectsTimeoutCap = ProjectCapabilityNames.CommandTimeoutCap;

    /// <summary>The M23 cap for <c>project.test</c> alone: the 5 min test bound plus headroom.</summary>
    public static readonly TimeSpan ProjectTestTimeoutCap = ProjectCapabilityNames.TestCommandTimeoutCap;

    /// <summary>The M25 cap for <c>project.run</c> alone: the longest 3D batch bound (Unity's 10 min) plus headroom. A web run still answers within its 20 s port wait — the cap is a ceiling.</summary>
    public static readonly TimeSpan ProjectRunTimeoutCap = ProjectCapabilityNames.RunCommandTimeoutCap;

    /// <summary>The B33 cap for <c>project.package</c> / <c>project.install</c> / <c>project.uninstall</c>: the companion's 5 min makeappx and MSIX deployment bounds plus headroom.</summary>
    public static readonly TimeSpan ProjectLifecycleTimeoutCap = ProjectCapabilityNames.LifecycleCommandTimeoutCap;

    /// <summary>The M25 cap for the scenes family: reading a bounded file and a bounded PNG is the operator family's 30 s.</summary>
    public static readonly TimeSpan ScenesTimeoutCap = SceneCapabilityNames.CommandTimeoutCap;

    private readonly TimeProvider _time = timeProvider ?? TimeProvider.System;

    /// <summary>Whether <c>browser.*</c> commands are routed at all (service option <c>BrowserEnabled</c>).</summary>
    public bool BrowserEnabled { get; } = browserEnabled;

    /// <summary>
    /// Whether <c>desktop.display_off</c> is routed at all (service option
    /// <c>DisplayPowerEnabled</c>, default false). Off is the M18 v1 posture: display-off has
    /// its own owner qualification and until it has run, a device answers as one that does not
    /// have the capability.
    /// </summary>
    public bool DisplayPowerEnabled { get; } = displayPowerEnabled;

    /// <summary>
    /// Whether the Digital Operator family is routed at all (service option
    /// <c>OperatorEnabled</c>, default false). Off is the M19 v1 posture until the lab gate is
    /// green on the owner's machine and the installer is run with <c>-Operator</c>.
    /// </summary>
    public bool OperatorEnabled { get; } = operatorEnabled;

    /// <summary>
    /// M18.3 (§6h): the ONE origin <c>desktop.play_audio</c> may fetch from — the broker REST
    /// base this device is enrolled against, reduced to scheme, host and port. Null when the
    /// service has no broker URL configured, in which case every <c>play_audio</c> is refused:
    /// a capability that fetches and plays a URL with nothing to compare it against is a
    /// capability that plays whatever reaches the payload.
    /// </summary>
    public string? AudioOrigin { get; } = OriginOf(brokerRestUrl);

    /// <summary>
    /// M22 (§6k): the same origin, under the name the rest of the agent uses — the ONE origin
    /// <c>file.fetch</c> may download from. The service refuses a foreign origin before the
    /// pipe and tells the companion this value in the challenge so it refuses it again.
    /// </summary>
    public string? BrokerOrigin => AudioOrigin;

    /// <summary>Per-family cap on the time one command may hold the companion.</summary>
    public static TimeSpan TimeoutCapFor(string capability)
    {
        if (AgentCapabilities.IsBrowser(capability))
        {
            return BrowserCapabilities.CommandTimeoutCap;
        }

        if (string.Equals(capability, OperatorCapabilityNames.AppLaunch, StringComparison.Ordinal))
        {
            return OperatorLaunchTimeoutCap;
        }

        if (AgentCapabilities.IsDocuments(capability))
        {
            return DocumentsTimeoutCap;
        }

        if (string.Equals(capability, ProjectCapabilityNames.ProjectTest, StringComparison.Ordinal))
        {
            return ProjectTestTimeoutCap;
        }

        if (string.Equals(capability, ProjectCapabilityNames.ProjectRun, StringComparison.Ordinal))
        {
            return ProjectRunTimeoutCap;
        }

        if (ProjectCapabilityNames.IsLongLifecycle(capability))
        {
            return ProjectLifecycleTimeoutCap;
        }

        if (AgentCapabilities.IsProjects(capability) || AgentCapabilities.IsScenes(capability))
        {
            return ProjectsTimeoutCap;
        }

        return AgentCapabilities.IsOperator(capability) ? OperatorTimeoutCap : DesktopTimeoutCap;
    }

    /// <summary>
    /// The pipe timeout for a command: the time until its expiry, clamped to [1 s, family cap].
    /// Public and pure so the cap is asserted directly rather than inferred from a stopwatch.
    /// </summary>
    public TimeSpan ResolveTimeout(CommandEnvelope command)
    {
        var cap = TimeoutCapFor(command.Capability);
        var remaining = command.ExpiresAt - _time.GetUtcNow();
        return remaining < MinTimeout ? MinTimeout : remaining > cap ? cap : remaining;
    }

    public async Task<JsonObject?> ExecuteAsync(CommandEnvelope command, CancellationToken cancellationToken)
    {
        if (AgentCapabilities.IsBrowser(command.Capability))
        {
            if (!BrowserEnabled)
            {
                // Refused before the companion is consulted: a device that does not
                // advertise the family must answer its commands the way an unknown
                // capability is answered, not with "try again later".
                throw new CapabilityException(
                    ErrorClasses.CapabilityMissing,
                    $"capability '{command.Capability}' is not enabled on this device (BrowserEnabled=false)",
                    retryable: false);
            }

            if (!BrowserCapabilities.IsOperation(command.Capability))
            {
                // The family marker and any made-up name: refused on the service side so the
                // pipe only ever carries names the contract defines. The companion and the
                // worker host repeat this check (defence in depth), but this is the first
                // trust boundary the command meets on the device.
                throw new CapabilityException(
                    ErrorClasses.CapabilityMissing,
                    $"capability '{command.Capability}' is not a browser operation this device knows (BROWSER_CAPABILITIES.md §1)",
                    retryable: false);
            }
        }
        else if (AgentCapabilities.IsDisplayPower(command.Capability))
        {
            if (!DisplayPowerEnabled)
            {
                // The device does not advertise this name either (AgentCapabilities.Compose),
                // so Cloud Core would normally never send it. Refusing it here as well means
                // a command aimed straight at the device — a stale one, a hand-made one —
                // still cannot blank the owner's screen before the qualification has run.
                throw new CapabilityException(
                    ErrorClasses.CapabilityMissing,
                    $"capability '{command.Capability}' is not enabled on this device (DisplayPowerEnabled=false)",
                    retryable: false);
            }
        }
        else if (string.Equals(command.Capability, AgentCapabilities.DesktopPlayAudio, StringComparison.Ordinal))
        {
            // Before the pipe, not after: the companion is the thing that would fetch and play
            // the URL, so the check has to happen on the side that has not been asked to yet.
            ValidateAudioOrigin(command.Payload, AudioOrigin);
        }
        else if (AgentCapabilities.IsOperator(command.Capability))
        {
            if (!OperatorEnabled)
            {
                // Same rule as display-off: not advertised, and refused here as well so a
                // command aimed straight at the device cannot drive the owner's desktop
                // before the family was switched on out loud.
                throw new CapabilityException(
                    ErrorClasses.CapabilityMissing,
                    $"capability '{command.Capability}' is not enabled on this device (OperatorEnabled=false)",
                    retryable: false);
            }
        }
        else if (AgentCapabilities.IsDocuments(command.Capability))
        {
            if (!OperatorEnabled)
            {
                // M20: the documents family is advertised and routed under the operator's
                // flag; a command aimed straight at the device cannot read the owner's files
                // before that flag was switched on out loud.
                throw new CapabilityException(
                    ErrorClasses.CapabilityMissing,
                    $"capability '{command.Capability}' is not enabled on this device (OperatorEnabled=false gates the documents family)",
                    retryable: false);
            }

            if (string.Equals(command.Capability, DocumentCapabilityNames.FileFetch, StringComparison.Ordinal))
            {
                // M22: before the pipe, as for play_audio — the companion is the thing that
                // would download the URL, so the origin is checked on the side that has not
                // been asked to yet, against the origin THIS side dialled.
                ValidateFetchOrigin(command.Payload, BrokerOrigin);
            }
        }
        else if (AgentCapabilities.IsProjects(command.Capability))
        {
            if (!OperatorEnabled)
            {
                // M23: the projects family is advertised and routed under the operator's
                // flag; a command aimed straight at the device cannot write source or start
                // a process before that flag was switched on out loud.
                throw new CapabilityException(
                    ErrorClasses.CapabilityMissing,
                    $"capability '{command.Capability}' is not enabled on this device (OperatorEnabled=false gates the projects family)",
                    retryable: false);
            }
        }
        else if (AgentCapabilities.IsScenes(command.Capability))
        {
            if (!OperatorEnabled)
            {
                // M25: the scenes family reads back a 3D project the projects family made, in
                // the same root, under the same flag — one trust decision, not two.
                throw new CapabilityException(
                    ErrorClasses.CapabilityMissing,
                    $"capability '{command.Capability}' is not enabled on this device (OperatorEnabled=false gates the scenes family)",
                    retryable: false);
            }
        }
        else if (!AgentCapabilities.IsInteractive(command.Capability))
        {
            throw new CapabilityException(
                ErrorClasses.CapabilityMissing,
                $"capability '{command.Capability}' is not supported by this device",
                retryable: false);
        }

        var timeout = ResolveTimeout(command);
        return await pipeServer.ExecuteCapabilityAsync(command.Capability, command.Payload, timeout, cancellationToken).ConfigureAwait(false);
    }

    /// <summary>
    /// Refuses a <c>desktop.play_audio</c> payload whose <c>audio.url</c> is not on
    /// <paramref name="allowedOrigin"/>, with <c>security_scope_error</c> (never retryable —
    /// the same URL will be just as wrong next time). Public and pure so the refusal is asserted
    /// directly rather than inferred from what did not reach the pipe.
    ///
    /// <para>Scheme, host and port must all match. A default-deny when no origin is configured
    /// is deliberate: "we could not tell where audio may come from" and "this audio may be
    /// played" must not be the same answer.</para>
    /// </summary>
    public static void ValidateAudioOrigin(JsonObject payload, string? allowedOrigin)
    {
        ArgumentNullException.ThrowIfNull(payload);

        if (string.IsNullOrWhiteSpace(allowedOrigin))
        {
            throw new CapabilityException(
                ErrorClasses.SecurityScopeError,
                $"'{AgentCapabilities.DesktopPlayAudio}' is refused: this device has no configured broker origin, "
                + "so no audio URL can be recognised as the owner's own",
                retryable: false);
        }

        var raw = payload["audio"] is JsonObject audio ? audio["url"]?.GetValue<string>() : null;
        if (string.IsNullOrWhiteSpace(raw)
            || !Uri.TryCreate(raw, UriKind.Absolute, out var url)
            || (url.Scheme != Uri.UriSchemeHttp && url.Scheme != Uri.UriSchemeHttps))
        {
            throw new CapabilityException(
                ErrorClasses.ValidationError,
                "payload.audio.url must be an absolute http(s) URL",
                retryable: false);
        }

        var origin = url.GetLeftPart(UriPartial.Authority);
        if (!string.Equals(origin, allowedOrigin, StringComparison.OrdinalIgnoreCase))
        {
            throw new CapabilityException(
                ErrorClasses.SecurityScopeError,
                $"audio may only be fetched from {allowedOrigin}; this payload named {origin}",
                retryable: false);
        }
    }

    /// <summary>
    /// M22 (§6k): refuses a <c>file.fetch</c> payload whose <c>url</c> is not on
    /// <paramref name="allowedOrigin"/> with <c>permission_denied</c> (never retryable). The
    /// message names the field and says "beklenen köken değil"; it never repeats the URL's
    /// host. No configured origin is a refusal too, for the reason
    /// <see cref="ValidateAudioOrigin"/> gives. Everything else about the payload — the
    /// path prefix, the name, the hash, the size — is the companion's to validate.
    /// </summary>
    public static void ValidateFetchOrigin(JsonObject payload, string? allowedOrigin)
    {
        ArgumentNullException.ThrowIfNull(payload);

        if (string.IsNullOrWhiteSpace(allowedOrigin))
        {
            throw new CapabilityException(
                ErrorClasses.PermissionDenied,
                $"'{DocumentCapabilityNames.FileFetch}' is refused: this device has no configured broker origin, so no render URL can be recognised as the owner's own (beklenen köken değil)",
                retryable: false);
        }

        var raw = payload["url"]?.GetValueKind() == System.Text.Json.JsonValueKind.String ? payload["url"]!.GetValue<string>() : null;
        if (string.IsNullOrWhiteSpace(raw)
            || !Uri.TryCreate(raw, UriKind.Absolute, out var url)
            || HttpOrigin.Of(url) is null)
        {
            throw new CapabilityException(
                ErrorClasses.ValidationError,
                "payload.url must be an absolute http(s) URL",
                retryable: false);
        }

        if (!HttpOrigin.Same(HttpOrigin.Of(url), allowedOrigin))
        {
            throw new CapabilityException(
                ErrorClasses.PermissionDenied,
                "payload.url is not on the origin this device dialled for its Cloud Core connection (beklenen köken değil); nothing was fetched",
                retryable: false);
        }
    }

    /// <summary>Scheme, host and port of a configured URL, or null when it is unusable (<see cref="HttpOrigin.Of(string?)"/>).</summary>
    public static string? OriginOf(string? url) => HttpOrigin.Of(url);
}
