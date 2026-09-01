using Microsoft.Extensions.Configuration;
using PagentOS.Agent.Core.Ipc;

namespace PagentOS.DeviceService;

/// <summary>
/// Service configuration: appsettings.json plus environment overrides with the
/// PAGENTOS_AGENT_ prefix (e.g. PAGENTOS_AGENT_BrokerWsUrl, PAGENTOS_AGENT_DataDir).
/// </summary>
public sealed record AgentServiceOptions
{
    public required string BrokerRestUrl { get; init; }

    public required string BrokerWsUrl { get; init; }

    public required string DataDir { get; init; }

    public required string PipeName { get; init; }

    /// <summary>
    /// SID of the account whose Session Companion may connect. Under a Session-0 service
    /// this is NOT the service's own account, which is precisely why it must be configured
    /// rather than inferred from the running process (M1 security finding #1). When absent,
    /// the service falls back to its own SID — correct only for a developer run where both
    /// halves are the same user, and reported as such at startup.
    /// </summary>
    /// <summary>
    /// "developer" keeps the running account's access to persisted machine material (device
    /// key, enrollment state), because in a developer run the owner IS the service account.
    /// Anything else, including absent, means the installed-service posture: SYSTEM and
    /// Administrators only. Set PAGENTOS_AGENT_MachineMaterialMode=developer for a console
    /// run; an installed service must never set it.
    /// </summary>
    public string? MachineMaterialMode { get; init; }

    public bool DeveloperMaterialPosture
        => string.Equals(MachineMaterialMode?.Trim(), "developer", StringComparison.OrdinalIgnoreCase);

    public string? CompanionSid { get; init; }

    /// <summary>
    /// Full path of the installed companion executable. When set, a peer that is the right
    /// user in the right session but is not this binary is refused. Left unset in developer
    /// runs, where the companion is launched from a build output that moves around.
    /// </summary>
    public string? CompanionImagePath { get; init; }

    /// <summary>
    /// Windows session the companion must be in. Normally left unset (any interactive
    /// session, never Session 0); pinned when the owner wants one specific console session.
    /// </summary>
    public int? CompanionSessionId { get; init; }

    public double? HeartbeatIntervalOverrideS { get; init; }

    public double BackoffBaseSeconds { get; init; } = 1.0;

    public double BackoffMaxSeconds { get; init; } = 60.0;

    public string KeyFilePath => Path.Combine(DataDir, "device.key");

    public string StateFilePath => Path.Combine(DataDir, "state.json");

    public string IdempotencyStorePath => Path.Combine(DataDir, "idempotency.json");

    public string AuditLogPath => Path.Combine(DataDir, "audit", "agent-audit.jsonl");

    public string LogFilePath => Path.Combine(DataDir, "logs", "device-service.log");

    public static string DefaultDataDir()
        => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PagentOS", "agent");

    public static AgentServiceOptions FromConfiguration(IConfiguration configuration)
    {
        var dataDir = configuration["DataDir"];
        if (string.IsNullOrWhiteSpace(dataDir))
        {
            dataDir = DefaultDataDir();
        }

        var companionSid = configuration["CompanionSid"];
        var pipeName = configuration["PipeName"];
        if (string.IsNullOrWhiteSpace(pipeName))
        {
            // Named after the OWNER, not after this process. Under a Session-0 service those
            // are different accounts, and defaulting to "the current user" would put the
            // service on pagentos-companion-S-1-5-18 while the companion waited on
            // pagentos-companion-{ownerSid} — two healthy-looking halves that never meet.
            pipeName = string.IsNullOrWhiteSpace(companionSid)
                ? PipeNaming.DefaultPipeName()
                : PipeNaming.ForOwnerSid(companionSid);
        }

        double? heartbeatOverride = null;
        var heartbeatRaw = configuration["HeartbeatIntervalOverrideS"];
        if (!string.IsNullOrWhiteSpace(heartbeatRaw)
            && double.TryParse(heartbeatRaw, System.Globalization.CultureInfo.InvariantCulture, out var parsed))
        {
            heartbeatOverride = parsed;
        }

        int? companionSessionId = null;
        var sessionRaw = configuration["CompanionSessionId"];
        if (!string.IsNullOrWhiteSpace(sessionRaw) && int.TryParse(sessionRaw, out var parsedSession))
        {
            companionSessionId = parsedSession;
        }

        return new AgentServiceOptions
        {
            BrokerRestUrl = configuration["BrokerRestUrl"] ?? "http://127.0.0.1:8001",
            BrokerWsUrl = configuration["BrokerWsUrl"] ?? "ws://127.0.0.1:8001/v1/devices/connect",
            DataDir = dataDir,
            PipeName = pipeName,
            MachineMaterialMode = configuration["MachineMaterialMode"],
            CompanionSid = companionSid,
            CompanionImagePath = configuration["CompanionImagePath"],
            CompanionSessionId = companionSessionId,
            HeartbeatIntervalOverrideS = heartbeatOverride,
            BackoffBaseSeconds = configuration.GetValue("BackoffBaseSeconds", 1.0),
            BackoffMaxSeconds = configuration.GetValue("BackoffMaxSeconds", 60.0),
        };
    }
}
