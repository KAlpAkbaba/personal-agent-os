using System.Text.Json.Nodes;

namespace PagentOS.Agent.Core.Connection;

/// <summary>
/// Where <see cref="AgentConnection"/> asks for the optional <c>status</c> object on a heartbeat
/// (DEVICE_PROTOCOL.md §6g, M18.3). Implemented by the Device Service, which asks the
/// owner-session companion; absent in a build with no companion at all.
///
/// <para><b>The contract is that this can fail and nothing happens.</b> The implementation is
/// called with a token that is already cancelled at <see cref="Protocol.HeartbeatStatus.MaxWait"/>,
/// and whatever it returns — null, a partial object — or throws is treated the same way: the
/// heartbeat goes out on time, without a status. A heartbeat is how this device says it is alive;
/// making that depend on a second process answering promptly would turn a slow companion into an
/// offline device.</para>
/// </summary>
public interface IHeartbeatStatusProvider
{
    /// <summary>The status to attach, or null for none. Should not throw, but may.</summary>
    Task<JsonObject?> GetStatusAsync(CancellationToken cancellationToken);
}
