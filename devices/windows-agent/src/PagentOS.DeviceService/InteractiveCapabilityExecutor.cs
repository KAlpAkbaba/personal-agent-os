using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.DeviceService;

/// <summary>
/// Routes interactive capabilities to the Session Companion via the named pipe. The Device
/// Service never touches the interactive desktop itself (CLAUDE.md Windows Agent rule).
/// </summary>
public sealed class InteractiveCapabilityExecutor(CompanionPipeServer pipeServer, TimeProvider? timeProvider = null) : ICapabilityExecutor
{
    private static readonly TimeSpan MinTimeout = TimeSpan.FromSeconds(1);
    private static readonly TimeSpan MaxTimeout = TimeSpan.FromSeconds(60);

    private static readonly HashSet<string> InteractiveCapabilities = new(StringComparer.Ordinal)
    {
        AgentCapabilities.DesktopOpenApplication,
        AgentCapabilities.DesktopOpenArtifact,
    };

    private readonly TimeProvider _time = timeProvider ?? TimeProvider.System;

    public async Task<JsonObject?> ExecuteAsync(CommandEnvelope command, CancellationToken cancellationToken)
    {
        if (!InteractiveCapabilities.Contains(command.Capability))
        {
            throw new CapabilityException(
                ErrorClasses.CapabilityMissing,
                $"capability '{command.Capability}' is not supported by this device",
                retryable: false);
        }

        var remaining = command.ExpiresAt - _time.GetUtcNow();
        var timeout = remaining < MinTimeout ? MinTimeout : remaining > MaxTimeout ? MaxTimeout : remaining;
        return await pipeServer.ExecuteCapabilityAsync(command.Capability, command.Payload, timeout, cancellationToken).ConfigureAwait(false);
    }
}
