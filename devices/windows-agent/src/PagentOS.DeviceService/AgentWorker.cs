using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Connection;

namespace PagentOS.DeviceService;

/// <summary>Hosts the outbound broker connection loop for the lifetime of the service.</summary>
public sealed class AgentWorker(AgentConnection connection, ILogger<AgentWorker> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        logger.LogInformation("device agent worker starting");
        await connection.RunAsync(stoppingToken).ConfigureAwait(false);
        logger.LogInformation("device agent worker stopped");
    }
}
