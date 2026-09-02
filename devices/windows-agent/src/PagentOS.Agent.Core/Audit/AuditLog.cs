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
        try
        {
            lock (_sync)
            {
                File.AppendAllText(_path, line + Environment.NewLine);
            }
        }
        catch (Exception ex)
        {
            // Same rule as the file logger, learned from the same incident: a failed audit
            // write must degrade the record, never kill the code path being audited.
            //
            // Non-fatal is not the same as silent. A trail that stops being written while
            // commands keep succeeding is exactly the shape of the first cloud qualification
            // finding, so the failure is counted (observable by callers and tests) and each
            // NEW failure message is written once to stderr, which the service host captures.
            // De-duplicated, because a broken path would otherwise flood the log on every row.
            Interlocked.Increment(ref _failedWrites);
            var message = $"{ex.GetType().Name}: {ex.Message}";
            if (!string.Equals(Interlocked.Exchange(ref _lastFailureMessage, message), message, StringComparison.Ordinal))
            {
                try { Console.Error.WriteLine($"audit write failed ({_path}): {message}"); } catch (Exception) { }
            }
        }
    }

    private long _failedWrites;
    private string? _lastFailureMessage;

    /// <summary>Audit rows that could not be written since this instance was created. Never resets.</summary>
    public long FailedWrites => Interlocked.Read(ref _failedWrites);

    private static void AddIfPresent(JsonObject record, string name, string? value)
    {
        if (value is not null)
        {
            record[name] = value;
        }
    }
}
