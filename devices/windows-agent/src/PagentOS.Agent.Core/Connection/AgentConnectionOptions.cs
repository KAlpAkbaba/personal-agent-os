using PagentOS.Agent.Core.Protocol;

namespace PagentOS.Agent.Core.Connection;

public sealed record AgentConnectionOptions
{
    public required Uri BrokerWsUrl { get; init; }

    public required string DeviceId { get; init; }

    public string SoftwareVersion { get; init; } = AgentInfo.SoftwareVersion;

    public IReadOnlyList<string> Capabilities { get; init; } = AgentCapabilities.All;

    public double BackoffBaseSeconds { get; init; } = 1.0;

    public double BackoffMaxSeconds { get; init; } = 60.0;

    /// <summary>Overrides the welcome-provided heartbeat interval when set (test hook).</summary>
    public double? HeartbeatIntervalOverrideS { get; init; }

    public TimeSpan HandshakeTimeout { get; init; } = TimeSpan.FromSeconds(15);
}
