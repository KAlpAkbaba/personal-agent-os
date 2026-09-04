using System.Text.Json.Nodes;

namespace PagentOS.SessionCompanion;

/// <summary>
/// The companion ↔ worker stdio protocol (BROWSER_CAPABILITIES.md §7): newline-delimited
/// UTF-8 JSON objects with a <c>type</c> field, on the worker's stdin/stdout. This never
/// leaves the machine and is not the device protocol; it is deliberately modelled on
/// plain <see cref="JsonObject"/>s so an unknown field from a newer worker is ignored
/// rather than fatal.
/// </summary>
public static class BrowserWorkerMessageTypes
{
    // worker → companion
    public const string Hello = "hello";
    public const string Result = "result";
    public const string Pong = "pong";
    public const string Log = "log";

    // companion → worker
    public const string Exec = "exec";
    public const string Cancel = "cancel";
    public const string Ping = "ping";
    public const string Shutdown = "shutdown";

    public static JsonObject NewExec(string requestId, string capability, JsonObject payload, int timeoutMs) => new()
    {
        ["type"] = Exec,
        ["request_id"] = requestId,
        ["capability"] = capability,
        ["payload"] = (JsonObject)payload.DeepClone(),
        ["timeout_ms"] = timeoutMs,
    };

    public static JsonObject NewCancel(string requestId) => new()
    {
        ["type"] = Cancel,
        ["request_id"] = requestId,
    };

    public static JsonObject NewPing() => new() { ["type"] = Ping };

    public static JsonObject NewShutdown() => new() { ["type"] = Shutdown };
}

/// <summary>The worker's first line: what it is and what it can do.</summary>
public sealed record BrowserWorkerHello
{
    public required string WorkerVersion { get; init; }

    public required int ProtocolVersion { get; init; }

    public required IReadOnlyList<string> Capabilities { get; init; }

    public string? BrowserChannel { get; init; }

    public bool BrowserAvailable { get; init; }

    public string? BrowserVersion { get; init; }

    /// <summary>
    /// The worker's durable lifecycle fault (browser launch-rate circuit breaker tripped),
    /// as compact JSON, or null. Present means the worker will refuse research-profile
    /// launches until the fault ages out; the companion logs it loudly and puts it in the audit.
    /// </summary>
    public string? LifecycleFault { get; init; }

    /// <summary>
    /// Absolute path of the worker.py the worker is executing (hello.module.file), or null for a
    /// worker older than 0.3.0. The installer/verifier compare it with the installed venv: it is
    /// how a stale site-packages copy or a shadowing source directory becomes visible.
    /// </summary>
    public string? ModuleFile { get; init; }

    /// <summary>hello.module.package_sha256 (digest of the executing package), or null.</summary>
    public string? PackageSha256 { get; init; }

    /// <summary>Throws <see cref="FormatException"/> when the object is not a usable hello.</summary>
    public static BrowserWorkerHello Parse(JsonObject message)
    {
        var capabilities = new List<string>();
        if (message["capabilities"] is JsonArray array)
        {
            foreach (var node in array)
            {
                if (node is JsonValue value && value.TryGetValue<string>(out var name) && !string.IsNullOrWhiteSpace(name))
                {
                    capabilities.Add(name);
                }
            }
        }

        var protocolVersion = message["protocol_version"] is JsonValue pv && pv.TryGetValue<int>(out var parsed) ? parsed : 0;
        if (protocolVersion != 1)
        {
            throw new FormatException($"browser worker hello has protocol_version {protocolVersion}; this companion speaks 1");
        }

        var browser = message["browser"] as JsonObject;
        return new BrowserWorkerHello
        {
            WorkerVersion = message["worker_version"]?.GetValue<string>() ?? "unknown",
            ProtocolVersion = protocolVersion,
            Capabilities = capabilities,
            BrowserChannel = browser?["channel"]?.GetValue<string>(),
            BrowserAvailable = browser?["available"] is JsonValue av && av.TryGetValue<bool>(out var available) && available,
            BrowserVersion = browser?["version"]?.GetValue<string>(),
            LifecycleFault = message["lifecycle_fault"] is JsonObject fault ? fault.ToJsonString() : null,
            ModuleFile = message["module"] is JsonObject module ? module["file"]?.GetValue<string>() : null,
            PackageSha256 = message["module"] is JsonObject module2 ? module2["package_sha256"]?.GetValue<string>() : null,
        };
    }
}
