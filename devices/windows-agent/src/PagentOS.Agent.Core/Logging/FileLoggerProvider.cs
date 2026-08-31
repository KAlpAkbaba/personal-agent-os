using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;

namespace PagentOS.Agent.Core.Logging;

/// <summary>
/// Minimal structured JSON-lines file logger. Emits ts/level/category/message and, when a
/// logging scope carries a "trace_id" key, the trace_id — pairing with console logging for the
/// structured console+file requirement.
/// </summary>
public sealed class FileLoggerProvider : ILoggerProvider, ISupportExternalScope
{
    private readonly object _sync = new();
    private readonly string _path;
    private readonly LogLevel _minLevel;
    private IExternalScopeProvider _scopeProvider = new LoggerExternalScopeProvider();

    public FileLoggerProvider(string path, LogLevel minLevel = LogLevel.Debug)
    {
        _path = path;
        _minLevel = minLevel;
        var directory = Path.GetDirectoryName(Path.GetFullPath(path));
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }
    }

    public ILogger CreateLogger(string categoryName) => new FileLogger(this, categoryName);

    public void SetScopeProvider(IExternalScopeProvider scopeProvider) => _scopeProvider = scopeProvider;

    public void Dispose()
    {
    }

    private void WriteLine(string line)
    {
        lock (_sync)
        {
            File.AppendAllText(_path, line + Environment.NewLine);
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
