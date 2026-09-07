namespace PagentOS.Agent.Core.Commands;

/// <summary>
/// Typed capability failure carrying the protocol error taxonomy class
/// (docs/API_AND_PROTOCOLS.md §8) and retryability.
/// </summary>
public sealed class CapabilityException : Exception
{
    private static readonly IReadOnlyDictionary<string, object?> NoDetail = new Dictionary<string, object?>();

    public CapabilityException(string errorClass, string message, bool retryable)
        : this(errorClass, message, retryable, null)
    {
    }

    public CapabilityException(string errorClass, string message, bool retryable, IReadOnlyDictionary<string, object?>? detail)
        : base(message)
    {
        ErrorClass = errorClass;
        Retryable = retryable;
        Detail = detail ?? NoDetail;
    }

    public string ErrorClass { get; }

    public bool Retryable { get; }

    /// <summary>
    /// Structured facts about the failure for an IN-PROCESS caller (a test, a dispatcher that
    /// re-describes the failure): for a <c>focus_mismatch</c> raised mid-stream, <c>typed_chars</c>
    /// and <c>total_chars</c>. The wire error object carries class, message and retryable only
    /// (device-protocol.schema.json closes it), so anything a planner needs from here is ALSO
    /// written into the message.
    /// </summary>
    public IReadOnlyDictionary<string, object?> Detail { get; }
}
