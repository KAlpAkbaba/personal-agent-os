using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Connection;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.DeviceService;

/// <summary>
/// Asks the Session Companion for <c>desktop.activity_status</c> just before each heartbeat and
/// hands the answer to <see cref="AgentConnection"/> (DEVICE_PROTOCOL.md §6g, M18.3).
///
/// <para><b>Nothing here can break a heartbeat.</b> No companion connected, a companion that
/// takes too long, a companion that answers with an error or with a shape this service does not
/// recognise — every one of those returns null, and the heartbeat goes out on time without a
/// status. That is not defensive coding for its own sake: presence is computed from heartbeats,
/// so a status path that could stall one would let a busy companion make the whole device look
/// offline, which is a strictly worse failure than a heartbeat with no status on it.</para>
///
/// <para>The wait here is <see cref="HeartbeatStatus.MaxWait"/> minus a little, so the pipe's own
/// typed answer (or its typed timeout) arrives before the connection's outer bound fires — the
/// same headroom rule the browser path learned to apply on the companion side.</para>
/// </summary>
public sealed class CompanionHeartbeatStatusProvider(ICompanionCapabilityTransport pipeServer)
    : IHeartbeatStatusProvider
{
    /// <summary>Headroom under <see cref="HeartbeatStatus.MaxWait"/> for the pipe round trip.</summary>
    public static readonly TimeSpan PipeTimeout = HeartbeatStatus.MaxWait - TimeSpan.FromMilliseconds(250);

    public async Task<JsonObject?> GetStatusAsync(CancellationToken cancellationToken)
    {
        try
        {
            var result = await pipeServer
                .ExecuteCapabilityAsync(
                    AgentCapabilities.DesktopActivityStatus,
                    [],
                    PipeTimeout,
                    cancellationToken)
                .ConfigureAwait(false);
            return HeartbeatStatus.Project(result);
        }
        catch (Exception)
        {
            // Includes "no session companion is connected" (the ordinary case on a device the
            // owner has not logged into yet) and every companion-side error class. A heartbeat
            // never carries a fault; it carries what is known, or nothing.
            return null;
        }
    }
}
