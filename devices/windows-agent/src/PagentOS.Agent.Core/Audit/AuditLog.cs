using System.Text;
using System.Text.Json.Nodes;

namespace PagentOS.Agent.Core.Audit;

/// <summary>
/// Local append-only JSONL audit log (DEVICE_PROTOCOL.md §7). One JSON object per line.
/// Never records secrets; detail payloads are truncated at 4 KB.
/// </summary>
public sealed class AuditLog
{
    private const int MaxDetailLength = 4096;

    // A reader that opened the file with FileShare.Read (File.ReadAllText, an editor, a log
    // shipper) makes the writer's open fail with a sharing violation for as long as it holds
    // the handle. Measured on the runner 2026-09-08 (CI run 34204979854): the IPC test's
    // 50 ms poll collided with the single "ipc_companion_admitted" write and the row was lost
    // for good - counted, but never retried. A row is retried across a short window before it
    // is counted as failed; the total budget stays small so a broken path still degrades fast.
    private const int SharingRetries = 20;
    private const int SharingRetryDelayMs = 25;
    private const int ErrorSharingViolation = 32;
    private const int ErrorLockViolation = 33;
    private static readonly UTF8Encoding Utf8NoBom = new(false);

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
                AppendWithRetry(line + Environment.NewLine);
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

    /// <summary>
    /// Appends one row, sharing the file with readers and writers (a tailer that opens it with
    /// ReadWrite never blocks the trail) and retrying a sharing/lock violation raised by a
    /// reader that opened it more restrictively. Any other failure surfaces at once.
    /// </summary>
    private void AppendWithRetry(string text)
    {
        for (var attempt = 0; ; attempt++)
        {
            try
            {
                using var stream = new FileStream(
                    _path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete);
                using var writer = new StreamWriter(stream, Utf8NoBom);
                writer.Write(text);
                return;
            }
            catch (IOException ex) when (attempt < SharingRetries && IsSharingViolation(ex))
            {
                Thread.Sleep(SharingRetryDelayMs);
            }
        }
    }

    private static bool IsSharingViolation(IOException ex)
    {
        var code = ex.HResult & 0xFFFF;
        return code is ErrorSharingViolation or ErrorLockViolation;
    }

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
