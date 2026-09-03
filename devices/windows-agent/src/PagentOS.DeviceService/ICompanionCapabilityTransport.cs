using System.Text.Json.Nodes;

namespace PagentOS.DeviceService;

/// <summary>
/// The one thing <see cref="InteractiveCapabilityExecutor"/> needs from the Session
/// Companion's pipe: forward a capability and wait for its answer. Implemented by
/// <see cref="CompanionPipeServer"/> in production; a test double stands in to prove that a
/// refusal made in the executor (<c>BrowserEnabled=false</c>, an operation outside the
/// contract) never reaches the pipe at all, rather than inferring that from the error
/// class the pipe would have produced.
/// </summary>
public interface ICompanionCapabilityTransport
{
    /// <summary>
    /// Forwards an interactive capability to the connected companion. Throws
    /// <c>CapabilityException</c> with <c>dependency_unavailable</c> (retryable) when no
    /// companion is connected.
    /// </summary>
    Task<JsonObject?> ExecuteCapabilityAsync(string capability, JsonObject payload, TimeSpan timeout, CancellationToken cancellationToken);
}
