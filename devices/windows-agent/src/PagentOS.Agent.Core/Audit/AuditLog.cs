using System.Text.Json.Nodes;

namespace PagentOS.Agent.Core.Audit;

/// <summary>
/// Local append-only JSONL audit log (DEVICE_PROTOCOL.md §7). One JSON object per line.
/// Never records secrets; detail payloads are truncated at 4 KB.
/// </summary>
public sealed class AuditLog
{
    private const int MaxDetailLength = 4096;

    private readonly object _sync = new();
    private readonly string _path;

    public AuditLog(string path)
    {
        _path = path;
        var directory = Path.GetDirectoryName(Path.GetFullPath(path));
        if (!string.IsNullOrEmpty(directory))
        {
            var created = !Directory.Exists(directory);
            Directory.CreateDirectory(directory);
            if (created)
            {
                // The audit directory is service-owned machine material: the service writes
                // it as SYSTEM, and nobody unprivileged should be able to edit the record of
                // what the agent did.
                Security.MachineMaterial.Protect(directory, Security.MachineMaterialKind.Directory);
            }
        }
    }

    public void Write(
        string eventType,
        string? deviceId = null,
        string? commandId = null,
        string? traceId = null,
        string? capability = null,
        string? status = null,
        string? detail = null)
    {
        var record = new JsonObject
        {
            ["ts"] = DateTimeOffset.UtcNow.ToString("O"),
            ["event"] = eventType,
        };
        AddIfPresent(record, "device_id", deviceId);
        AddIfPresent(record, "command_id", commandId);
        AddIfPresent(record, "trace_id", traceId);
        AddIfPresent(record, "capability", capability);
        AddIfPresent(record, "status", status);
        if (detail is not null)
        {
            record["detail"] = detail.Length <= MaxDetailLength ? detail : detail[..MaxDetailLength];
        }

        var line = record.ToJsonString();
        lock (_sync)
        {
            File.AppendAllText(_path, line + Environment.NewLine);
        }
    }

    private static void AddIfPresent(JsonObject record, string name, string? value)
    {
        if (value is not null)
        {
            record[name] = value;
        }
    }
}
