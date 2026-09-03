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
/// <item><c>desktop.*</c> — always routed; per-command cap 60 s (M1 behaviour, unchanged).</item>
/// <item><c>browser.*</c> — routed only when <c>BrowserEnabled</c> is configured AND the name
/// is one of the contract's operations (BROWSER_CAPABILITIES.md §1); anything else in the
/// namespace is refused here with <c>capability_missing</c> before the companion is
/// consulted, so an unknown operation never crosses the pipe. Per-command cap 120 s (§3),
/// because a real navigation plus extraction on a slow site is legitimately longer than
/// opening Notepad.</item>
/// </list>
/// The "no companion connected → <c>dependency_unavailable</c>, retryable" rule belongs to
/// the pipe server and applies to both families.
/// </summary>
public sealed class InteractiveCapabilityExecutor(
    ICompanionCapabilityTransport pipeServer,
    TimeProvider? timeProvider = null,
    bool browserEnabled = false) : ICapabilityExecutor
{
    private static readonly TimeSpan MinTimeout = TimeSpan.FromSeconds(1);

    /// <summary>The M1 cap for the desktop family.</summary>
    public static readonly TimeSpan DesktopTimeoutCap = TimeSpan.FromSeconds(60);

    private readonly TimeProvider _time = timeProvider ?? TimeProvider.System;

    /// <summary>Whether <c>browser.*</c> commands are routed at all (service option <c>BrowserEnabled</c>).</summary>
    public bool BrowserEnabled { get; } = browserEnabled;

    /// <summary>Per-family cap on the time one command may hold the companion.</summary>
    public static TimeSpan TimeoutCapFor(string capability)
        => AgentCapabilities.IsBrowser(capability) ? BrowserCapabilities.CommandTimeoutCap : DesktopTimeoutCap;

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
        else if (!AgentCapabilities.IsDesktop(command.Capability))
        {
            throw new CapabilityException(
                ErrorClasses.CapabilityMissing,
                $"capability '{command.Capability}' is not supported by this device",
                retryable: false);
        }

        var timeout = ResolveTimeout(command);
        return await pipeServer.ExecuteCapabilityAsync(command.Capability, command.Payload, timeout, cancellationToken).ConfigureAwait(false);
    }
}
