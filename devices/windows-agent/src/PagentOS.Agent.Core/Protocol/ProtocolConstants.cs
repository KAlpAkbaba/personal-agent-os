namespace PagentOS.Agent.Core.Protocol;

public static class ProtocolConstants
{
    public const int Version = 1;

    /// <summary>Maximum length of ErrorObject.message per schema.</summary>
    public const int MaxErrorMessageLength = 2000;
}

public static class AgentInfo
{
    public const string SoftwareVersion = "0.1.0";
    public const string Platform = "windows";
}

public static class AckStatus
{
    public const string Accepted = "accepted";
    public const string Running = "running";
    public const string Succeeded = "succeeded";
    public const string Failed = "failed";

    public static readonly IReadOnlySet<string> All =
        new HashSet<string>(StringComparer.Ordinal) { Accepted, Running, Succeeded, Failed };

    public static bool IsTerminal(string status) => status is Succeeded or Failed;
}

public static class AgentCapabilities
{
    public const string DesktopOpenApplication = "desktop.open_application";

    public static readonly IReadOnlyList<string> All = [DesktopOpenApplication];
}

public static class ErrorClasses
{
    public const string ValidationError = "validation_error";
    public const string AuthError = "auth_error";
    public const string DeviceOffline = "device_offline";
    public const string CapabilityMissing = "capability_missing";
    public const string DependencyUnavailable = "dependency_unavailable";
    public const string ProviderRateLimited = "provider_rate_limited";
    public const string ProviderError = "provider_error";
    public const string UiTargetNotFound = "ui_target_not_found";
    public const string UiStateChanged = "ui_state_changed";
    public const string Timeout = "timeout";
    public const string CommandExpired = "command_expired";
    public const string Cancelled = "cancelled";
    public const string RetryExhausted = "retry_exhausted";
    public const string ArtifactRenderError = "artifact_render_error";
    public const string VoiceProviderError = "voice_provider_error";
    public const string SecurityScopeError = "security_scope_error";
    public const string InternalBug = "internal_bug";

    public static readonly IReadOnlySet<string> All = new HashSet<string>(StringComparer.Ordinal)
    {
        ValidationError, AuthError, DeviceOffline, CapabilityMissing, DependencyUnavailable,
        ProviderRateLimited, ProviderError, UiTargetNotFound, UiStateChanged, Timeout,
        CommandExpired, Cancelled, RetryExhausted, ArtifactRenderError, VoiceProviderError,
        SecurityScopeError, InternalBug,
    };
}

public static class ErrorObjects
{
    /// <summary>Builds an ErrorObject, truncating the message to the schema limit.</summary>
    public static ErrorObject Create(string errorClass, string message, bool retryable)
    {
        if (message.Length > ProtocolConstants.MaxErrorMessageLength)
        {
            message = message[..ProtocolConstants.MaxErrorMessageLength];
        }

        return new ErrorObject { Class = errorClass, Message = message, Retryable = retryable };
    }
}
