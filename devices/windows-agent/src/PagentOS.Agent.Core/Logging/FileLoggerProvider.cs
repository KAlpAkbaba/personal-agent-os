using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;

namespace PagentOS.Agent.Core.Logging;

/// <summary>
/// Minimal structured JSON-lines file logger. Emits ts/level/category/message and, when a
/// logging scope carries a "trace_id" key, the trace_id — pairing with console logging for the
/// structured console+file requirement.
///
/// Optionally size-bounded: with <c>maxBytes &gt; 0</c> the file is rotated to
/// <c>&lt;path&gt;.1</c> (older ones shift to <c>.2</c> … <c>.keepRotated</c>, the oldest is
/// dropped) before a line would take it past the bound, so a long-lived process in the
/// owner's session cannot fill the disk with its own diagnostics.
/// </summary>
public sealed class FileLoggerProvider : ILoggerProvider, ISupportExternalScope
{
    private readonly object _sync = new();
    private readonly string _path;
    private readonly LogLevel _minLevel;
    private readonly long _maxBytes;
    private readonly int _keepRotated;
    private IExternalScopeProvider _scopeProvider = new LoggerExternalScopeProvider();

    public FileLoggerProvider(string path, LogLevel minLevel = LogLevel.Debug, long maxBytes = 0, int keepRotated = 1)
    {
        _path = path;
        _minLevel = minLevel;
        _maxBytes = Math.Max(0, maxBytes);
        _keepRotated = Math.Max(1, keepRotated);
        var directory = Path.GetDirectoryName(Path.GetFullPath(path));
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }
    }

    /// <summary>The file lines are appended to.</summary>
    public string FilePath => _path;

    /// <summary>Rotation bound in bytes; 0 means unbounded.</summary>
    public long MaxBytes => _maxBytes;

    /// <summary>How many rotated files (<c>.1</c> … <c>.N</c>) are kept when a bound is set.</summary>
    public int KeepRotated => _keepRotated;

    public ILogger CreateLogger(string categoryName) => new FileLogger(this, categoryName);

    public void SetScopeProvider(IExternalScopeProvider scopeProvider) => _scopeProvider = scopeProvider;

    public void Dispose()
    {
    }

    /// <summary>The name of the <paramref name="generation"/>-th rotated file (1 = newest).</summary>
    public static string RotatedPath(string path, int generation) => $"{path}.{generation}";

    private void WriteLine(string line)
    {
        // A logger must never take the service down. This is not defensive decoration: a
        // real deployment left the log FILE with an empty DACL, the write threw
        // UnauthorizedAccessException from inside the pipe server's `finally` block, and
        // that single failed log line killed the BackgroundService — the host stayed
        // "Running" while the pipe accept loop was dead and the companion retried forever.
        // Losing a log line is the strictly smaller failure.
        try
        {
            lock (_sync)
            {
                RotateIfNeeded(line.Length + Environment.NewLine.Length);
                File.AppendAllText(_path, line + Environment.NewLine);
            }
        }
        catch (Exception)
        {
            // Swallowed by design; the console logger still carries the line.
        }
    }

    private void RotateIfNeeded(int incomingBytes)
    {
        if (_maxBytes <= 0)
        {
            return;
        }

        var info = new FileInfo(_path);
        if (!info.Exists || info.Length + incomingBytes <= _maxBytes)
        {
            return;
        }

        try
        {
            // Shift .N-1 → .N … .1 → .2, then the live file → .1. A failure anywhere is
            // reported as a lost rotation, never a lost line: the append still happens.
            for (var generation = _keepRotated; generation >= 2; generation--)
            {
                var younger = RotatedPath(_path, generation - 1);
                if (File.Exists(younger))
                {
                    File.Move(younger, RotatedPath(_path, generation), overwrite: true);
                }
            }

            File.Move(_path, RotatedPath(_path, 1), overwrite: true);
        }
        catch (Exception)
        {
            // Rotation is best effort; the next line tries again.
        }
    }

    private sealed class FileLogger(FileLoggerProvider owner, string category) : ILogger
    {
        public IDisposable? BeginScope<TState>(TState state)
            where TState : notnull
            => owner._scopeProvider.Push(state);

        public bool IsEnabled(LogLevel logLevel) => logLevel >= owner._minLevel && logLevel != LogLevel.None;

        public void Log<TState>(
            LogLevel logLevel,
            EventId eventId,
            TState state,
            Exception? exception,
            Func<TState, Exception?, string> formatter)
        {
            if (!IsEnabled(logLevel))
            {
                return;
            }

            string? traceId = null;
            owner._scopeProvider.ForEachScope(
                (scope, _) =>
                {
                    if (scope is IEnumerable<KeyValuePair<string, object?>> pairs)
                    {
                        foreach (var pair in pairs)
                        {
                            if (pair.Key == "trace_id" && pair.Value is not null)
                            {
                                traceId = pair.Value.ToString();
                            }
                        }
                    }
                },
                (object?)null);

            var record = new JsonObject
            {
                ["ts"] = DateTimeOffset.UtcNow.ToString("O"),
                ["level"] = logLevel.ToString(),
                ["category"] = category,
                ["message"] = formatter(state, exception),
            };
            if (traceId is not null)
            {
                record["trace_id"] = traceId;
            }

            if (exception is not null)
            {
                record["exception"] = exception.ToString();
            }

            owner.WriteLine(record.ToJsonString());
        }
    }
}
