using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.Agent.Core.Commands;

/// <summary>Executes one command's capability. Throws <see cref="CapabilityException"/> on typed failure.</summary>
public interface ICapabilityExecutor
{
    Task<JsonObject?> ExecuteAsync(CommandEnvelope command, CancellationToken cancellationToken);
}
