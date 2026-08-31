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

        var pipeName = configuration["PipeName"];
        if (string.IsNullOrWhiteSpace(pipeName))
        {
            pipeName = PipeNaming.DefaultPipeName();
        }

        double? heartbeatOverride = null;
        var heartbeatRaw = configuration["HeartbeatIntervalOverrideS"];
        if (!string.IsNullOrWhiteSpace(heartbeatRaw)
            && double.TryParse(heartbeatRaw, System.Globalization.CultureInfo.InvariantCulture, out var parsed))
        {
            heartbeatOverride = parsed;
        }

        return new AgentServiceOptions
        {
            BrokerRestUrl = configuration["BrokerRestUrl"] ?? "http://127.0.0.1:8001",
            BrokerWsUrl = configuration["BrokerWsUrl"] ?? "ws://127.0.0.1:8001/v1/devices/connect",
            DataDir = dataDir,
            PipeName = pipeName,
            HeartbeatIntervalOverrideS = heartbeatOverride,
            BackoffBaseSeconds = configuration.GetValue("BackoffBaseSeconds", 1.0),
            BackoffMaxSeconds = configuration.GetValue("BackoffMaxSeconds", 60.0),
        };
    }
}
