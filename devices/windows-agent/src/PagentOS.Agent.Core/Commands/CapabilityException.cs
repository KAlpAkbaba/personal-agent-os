namespace PagentOS.Agent.Core.Commands;

/// <summary>
/// Typed capability failure carrying the protocol error taxonomy class
/// (docs/API_AND_PROTOCOLS.md §8) and retryability.
/// </summary>
public sealed class CapabilityException(string errorClass, string message, bool retryable) : Exception(message)
{
    public string ErrorClass { get; } = errorClass;

    public bool Retryable { get; } = retryable;
}
