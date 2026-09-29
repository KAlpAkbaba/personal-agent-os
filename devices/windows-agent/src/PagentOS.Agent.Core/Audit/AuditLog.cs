using System.Text;
using System.Text.Json.Nodes;

namespace PagentOS.Agent.Core.Audit;

/// <summary>
/// Which process writes this trail, which decides who may write its directory.
///
/// Stated by the caller, never inferred from the account the process happens to run as: an
/// elevated developer console and the installed service look alike from the inside, and so do
/// the companion and any other process of the owner's.
/// </summary>
public enum AuditWriter
{
    /// <summary>
    /// The Device Service, as LocalSystem. The directory is service-owned machine material:
    /// SYSTEM and Administrators, nobody else. This is the zero value AND the constructor's
    /// default, so a caller that forgets the argument gets the strict posture — the service
    /// cannot inherit the weaker one by omission.
    /// </summary>
    Service = 0,

    /// <summary>
    /// The Session Companion, as the owner, not elevated. The directory additionally grants
    /// the account the companion runs as Modify; see <see cref="Security.OwnerSessionMaterial"/>.
    /// </summary>
    OwnerSessionCompanion = 1,
}

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

    /// <param name="path">The JSONL file. Its directory is created if it is missing.</param>
    /// <param name="writer">
    /// Whose trail this is. <see cref="AuditWriter.Service"/> unless the caller says otherwise.
    /// </param>
    /// <param name="developerRun">
    /// For <see cref="AuditWriter.Service"/> only: the machine-material posture, stated at the
    /// call instead of read from the process-wide default (tests assert the production
    /// posture this way). Null means the process-wide default.
    /// </param>
    public AuditLog(string path, AuditWriter writer = AuditWriter.Service, bool? developerRun = null)
    {
        _path = path;
        var directory = Path.GetDirectoryName(Path.GetFullPath(path));
        if (string.IsNullOrEmpty(directory))
        {
            return;
        }

        if (writer == AuditWriter.OwnerSessionCompanion)
        {
            // Security review of 2a2f7f95: the companion's data root is a place other
            // accounts can create folders in, so `audit` - or the root itself - may be a
            // junction somebody planted. Asked before the directory is created, before any
            // DACL is written and (in Write) before every row; a trail refused this way is
            // a failed write like any other, counted and reported once.
            _guardedDirectory = directory;
            var refusal = Security.OwnerSessionMaterial.ReparsePointRefusal(directory);
            if (refusal is not null)
            {
                _directoryProblem = refusal;
                return;
            }
        }

        var created = !Directory.Exists(directory);
        Directory.CreateDirectory(directory);

        if (writer == AuditWriter.OwnerSessionCompanion)
        {
            // 2026-09-29: this branch did not exist. The companion's directory was protected
            // as the service's is - SYSTEM and Administrators only - by the companion itself,
            // which could apply that DACL (it owned the directory it had just created) and
            // could not write a row afterwards. So this runs on EVERY start, not only when
            // the directory is new: the machines that already carry the locked directory are
            // repaired by the process that locked it. A directory it cannot repair (created
            // elevated) is left for the installer; the write failure is reported either way.
            //
            // Whether an EXISTING directory needs repair is decided by writing into it, not
            // by reading its DACL for the owner's SID: access through a group is access.
            _directoryProblem = Security.OwnerSessionMaterial
                .EnsureOwnerWritableDirectory(directory, justCreated: created, existingFile: path)
                .Problem;
            return;
        }

        if (created)
        {
            // The audit directory is service-owned machine material: the service writes
            // it as SYSTEM, and nobody unprivileged should be able to edit the record of
            // what the agent did.
            Security.MachineMaterial.Protect(directory, Security.MachineMaterialKind.Directory, developerRun);
        }
    }

    /// <summary>
    /// Names where the FIRST failed write is reported — the process's own log, where someone
    /// will read it. Once per instance, however many rows are lost after it: the count is in
    /// <see cref="FailedWrites"/>, and a broken path must not turn every audited action into
    /// a log line.
    ///
    /// The audit log is built before the logger in both hosts, so a failure that happened
    /// before this was called is reported at the call rather than forgiven.
    /// </summary>
    public void ReportFirstWriteFailureTo(Action<string> report)
    {
        ArgumentNullException.ThrowIfNull(report);
        string? pending;
        lock (_failureSync)
        {
            _failureReport = report;
            pending = _failureReported ? null : _firstFailure;
            if (pending is not null)
            {
                _failureReported = true;
            }
        }

        if (pending is not null)
        {
            Deliver(report, pending);
        }
    }

    private void NoteFailure(string message)
    {
        Action<string>? report;
        string text;
        lock (_failureSync)
        {
            _firstFailure ??= _directoryProblem is null ? message : $"{message} [{_directoryProblem}]";
            if (_failureReported || _failureReport is null)
            {
                return;
            }

            _failureReported = true;
            report = _failureReport;
            text = _firstFailure;
        }

        Deliver(report, text);
    }

    private static void Deliver(Action<string> report, string message)
    {
        // The same rule as the write itself: reporting a lost row must not lose the command.
        try { report(message); } catch (Exception) { }
    }

    private readonly object _failureSync = new();
    private readonly string? _directoryProblem;

    // Set for the companion's trail only: the directory that is checked for a reparse point
    // before every row. The service's directory admits no unprivileged writer to plant one.
    private readonly string? _guardedDirectory;
    private Action<string>? _failureReport;
    private string? _firstFailure;
    private bool _failureReported;

    /// <summary>The file this trail is written to.</summary>
    public string FilePath => _path;

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
                if (_guardedDirectory is not null
                    && Security.OwnerSessionMaterial.ReparsePointRefusal(_guardedDirectory) is { } refusal)
                {
                    // Not written, and not written THROUGH: the row is lost on purpose.
                    throw new IOException(refusal);
                }

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

            // stderr is captured for the service and read by nobody for the companion, which
            // is how a trail stayed unwritten for a day (2026-09-29). The first failure also
            // goes wherever the host said its log is.
            NoteFailure(message);
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
