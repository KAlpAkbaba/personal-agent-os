using System.Collections.Concurrent;
using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Connection;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion;

/// <summary>
/// Hosts the Browser Worker child process for the Session Companion (BROWSER_CAPABILITIES.md
/// §7). One worker at a time; started lazily on the first <c>browser.*</c> request (or
/// eagerly when configured); restarted with exponential backoff after an exit; told to shut
/// down and then killed when the companion stops.
///
/// The host is a transport, not a judge: it never looks at a payload or a result beyond the
/// checks the contract puts on this side — the operation allowlist (only §1 names are ever
/// written to the worker), the result size cap, the forbidden-key scan (session material
/// never crosses the pipe, under the same normalised rule the worker applies), that an
/// error class is one the device taxonomy knows, and a ceiling on one stdout line so the
/// worker cannot exhaust the companion. Everything else the worker says is passed through
/// typed, and the worker's stderr goes to the companion log — sanitised, never into a
/// result.
///
/// Concurrency: requests run concurrently (the worker serialises per session itself);
/// <c>request_id</c> correlates a result with its request, and a single writer lock keeps
/// stdin lines whole. In-flight requests fail with <c>dependency_unavailable</c>
/// (retryable) the moment the worker exits, so Cloud Core retries after the restart rather
/// than waiting out its own timeout.
/// </summary>
public sealed class BrowserWorkerHost : IAsyncDisposable
{
    private const string AuditRequestEvent = "browser_request";
    private const string AuditWorkerStarted = "browser_worker_started";
    private const string AuditWorkerExited = "browser_worker_exited";
    private const int LivenessMissedPongs = 3;

    /// <summary>
    /// After this many consecutive failed starts / unexpected exits an eager host stops
    /// restarting the worker in the background (it still restarts on the next explicit
    /// request). Without a ceiling a worker that dies on every start would be respawned
    /// forever, once a minute, for as long as the companion lives.
    /// </summary>
    public const int DefaultEagerRestartCeiling = 10;

    /// <summary>
    /// The longest stdout line the host will assemble: a protocol message is one result
    /// (≤ <see cref="BrowserCapabilities.MaxResultBytes"/>) plus its envelope, so four
    /// times the cap is generous for anything legitimate and small enough that one bad
    /// line cannot take the companion — and with it desktop and voice — down.
    /// </summary>
    public const int MaxStdoutLineBytes = 4 * BrowserCapabilities.MaxResultBytes;

    private readonly BrowserWorkerOptions _options;
    private readonly ILogger _logger;
    private readonly AuditLog? _audit;
    private readonly BackoffPolicy _restartBackoff;
    private readonly TimeSpan _helloTimeout;
    private readonly TimeSpan _pingInterval;
    private readonly TimeSpan _shutdownGrace;
    private readonly int _eagerRestartCeiling;
    private readonly SemaphoreSlim _startLock = new(1, 1);
    private readonly CancellationTokenSource _lifetime = new();

    private WorkerProcess? _worker;
    private int _consecutiveFailures;
    private int _eagerRestartSuspended;
    private DateTimeOffset? _lastExitAt;
    private volatile bool _stopping;
    private int _starts;
    private int _cancelsSent;
    private int _pingsSent;
    private int _pongsReceived;
    private int _livenessKills;
    private int _oversizeLineKills;

    public BrowserWorkerHost(
        BrowserWorkerOptions options,
        ILogger logger,
        AuditLog? audit = null,
        BackoffPolicy? restartBackoff = null,
        TimeSpan? helloTimeout = null,
        TimeSpan? pingInterval = null,
        TimeSpan? shutdownGrace = null,
        int? eagerRestartCeiling = null)
    {
        _options = options;
        _logger = logger;
        _audit = audit;
        _restartBackoff = restartBackoff ?? new BackoffPolicy(baseSeconds: 1.0, maxSeconds: 60.0);
        _helloTimeout = helloTimeout ?? TimeSpan.FromSeconds(20);
        _pingInterval = pingInterval ?? TimeSpan.FromSeconds(15);
        _shutdownGrace = shutdownGrace ?? TimeSpan.FromSeconds(5);
        _eagerRestartCeiling = Math.Max(1, eagerRestartCeiling ?? DefaultEagerRestartCeiling);
    }

    public BrowserWorkerOptions Options => _options;

    public bool IsConfigured => _options.IsConfigured;

    /// <summary>The most recent worker hello (null until a worker has started).</summary>
    public BrowserWorkerHello? Hello { get; private set; }

    /// <summary>What the worker said it can execute, once it has said so.</summary>
    public IReadOnlyList<string>? WorkerCapabilities => Hello?.Capabilities;

    public bool WorkerRunning => _worker is { Alive: true };

    /// <summary>Process id of the running worker, for diagnostics.</summary>
    public int? WorkerPid => _worker is { Alive: true } worker ? worker.Pid : null;

    // Telemetry — surfaced in logs, asserted by tests.
    public int Starts => Volatile.Read(ref _starts);

    public int Restarts => Math.Max(0, Starts - 1);

    public int CancelsSent => Volatile.Read(ref _cancelsSent);

    public int PingsSent => Volatile.Read(ref _pingsSent);

    public int PongsReceived => Volatile.Read(ref _pongsReceived);

    public int LivenessKills => Volatile.Read(ref _livenessKills);

    /// <summary>Workers killed for writing a stdout line longer than <see cref="MaxStdoutLineBytes"/>.</summary>
    public int OversizeLineKills => Volatile.Read(ref _oversizeLineKills);

    /// <summary>Consecutive failed starts / unexpected exits since the last successful request.</summary>
    public int ConsecutiveFailures => Volatile.Read(ref _consecutiveFailures);

    public int EagerRestartCeiling => _eagerRestartCeiling;

    /// <summary>True while eager background restarts are suspended (ceiling reached); an explicit request lifts it by succeeding.</summary>
    public bool EagerRestartSuspended => Volatile.Read(ref _eagerRestartSuspended) == 1;

    /// <summary>Eager start: spawn the worker now and wait for its hello. Failures are logged, never thrown — the next request retries.</summary>
    public async Task StartAsync(CancellationToken cancellationToken)
    {
        if (!IsConfigured)
        {
            return;
        }

        try
        {
            await EnsureWorkerAsync(cancellationToken).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        catch (CapabilityException ex)
        {
            _logger.LogWarning("browser worker eager start failed ({Class}): {Reason}; it will be retried on the first request", ex.ErrorClass, ex.Message);
        }
    }

    /// <summary>
    /// Execute one <c>browser.*</c> capability. Throws <see cref="CapabilityException"/> with
    /// a device-taxonomy class on every failure; never returns a result that failed a check.
    /// </summary>
    public async Task<JsonObject> ExecuteAsync(string capability, JsonObject payload, TimeSpan timeout, CancellationToken cancellationToken)
    {
        if (!IsConfigured)
        {
            throw new CapabilityException(
                ErrorClasses.CapabilityMissing,
                "no browser worker is configured on this companion (BrowserWorkerCommand is empty)",
                retryable: false);
        }

        if (!BrowserCapabilities.IsOperation(capability))
        {
            // Allowlist before anything is written to the worker's stdin — and before a
            // lazy worker is even started for it. The worker refuses unknown names too, but
            // the companion is the higher-assurance side of that pipe and must not depend
            // on the child to do its filtering.
            var reason = string.Equals(capability, BrowserCapabilities.Family, StringComparison.Ordinal)
                ? $"'{BrowserCapabilities.Family}' is the family marker, not an operation"
                : $"'{capability}' is not a browser operation this companion knows (BROWSER_CAPABILITIES.md §1); nothing was sent to the worker";
            throw new CapabilityException(ErrorClasses.CapabilityMissing, reason, retryable: false);
        }

        if (_stopping)
        {
            throw new CapabilityException(ErrorClasses.DependencyUnavailable, "the session companion is stopping", retryable: true);
        }

        var requestId = Guid.NewGuid().ToString("N");
        var stopwatch = Stopwatch.StartNew();
        WorkerProcess? worker = null;
        try
        {
            worker = await EnsureWorkerAsync(cancellationToken).ConfigureAwait(false);
            var result = await ExecuteOnWorkerAsync(worker, requestId, capability, payload, timeout, cancellationToken).ConfigureAwait(false);
            worker.SawSuccess = true;
            Interlocked.Exchange(ref _consecutiveFailures, 0);
            Interlocked.Exchange(ref _eagerRestartSuspended, 0);
            AuditRequest(capability, requestId, "ok", stopwatch.ElapsedMilliseconds, retryable: null);
            return result;
        }
        catch (CapabilityException ex)
        {
            AuditRequest(capability, requestId, ex.ErrorClass, stopwatch.ElapsedMilliseconds, ex.Retryable);
            throw;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            AuditRequest(capability, requestId, ErrorClasses.Cancelled, stopwatch.ElapsedMilliseconds, retryable: true);
            throw new CapabilityException(ErrorClasses.Cancelled, "browser request cancelled by the companion", retryable: true);
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "browser worker host crashed handling {Capability}", capability);
            AuditRequest(capability, requestId, ErrorClasses.InternalBug, stopwatch.ElapsedMilliseconds, retryable: false);
            throw new CapabilityException(ErrorClasses.InternalBug, $"browser worker host failure: {ex.Message}", retryable: false);
        }
        finally
        {
            worker?.Pending.TryRemove(requestId, out _);
        }
    }

    private async Task<JsonObject> ExecuteOnWorkerAsync(
        WorkerProcess worker,
        string requestId,
        string capability,
        JsonObject payload,
        TimeSpan timeout,
        CancellationToken cancellationToken)
    {
        var tcs = new TaskCompletionSource<JsonObject>(TaskCreationOptions.RunContinuationsAsynchronously);
        worker.Pending[requestId] = tcs;

        var timeoutMs = (int)Math.Clamp(timeout.TotalMilliseconds, 1, int.MaxValue);
        var wrote = await worker.TryWriteAsync(
            BrowserWorkerMessageTypes.NewExec(requestId, capability, payload, timeoutMs),
            cancellationToken).ConfigureAwait(false);
        if (!wrote)
        {
            throw new CapabilityException(ErrorClasses.DependencyUnavailable, "the browser worker's stdin is closed; it is being restarted", retryable: true);
        }

        using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeoutCts.CancelAfter(timeout);
        await using var registration = timeoutCts.Token.Register(() => tcs.TrySetCanceled(timeoutCts.Token)).ConfigureAwait(false);

        JsonObject message;
        try
        {
            message = await tcs.Task.ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            // The request's budget elapsed: tell the worker to stop working on it, then
            // answer timeout. A late result for this id is dropped by the reader.
            await SendCancelAsync(worker, requestId).ConfigureAwait(false);
            throw new CapabilityException(
                ErrorClasses.Timeout,
                $"browser worker did not answer {capability} within {timeout.TotalSeconds:F1} s",
                retryable: true);
        }
        catch (OperationCanceledException)
        {
            await SendCancelAsync(worker, requestId).ConfigureAwait(false);
            throw;
        }

        return InterpretResult(message, capability);
    }

    /// <summary>The three companion-side checks, then pass-through.</summary>
    private static JsonObject InterpretResult(JsonObject message, string capability)
    {
        var ok = message["ok"] is JsonValue okValue && okValue.TryGetValue<bool>(out var parsedOk) && parsedOk;
        if (!ok)
        {
            var error = message["error"] as JsonObject;
            var errorClass = error?["class"]?.GetValue<string>();
            var errorMessage = error?["message"]?.GetValue<string>() ?? "browser worker reported failure without a message";
            var retryable = error?["retryable"] is JsonValue rv && rv.TryGetValue<bool>(out var parsedRetryable) && parsedRetryable;
            if (errorClass is null || !ErrorClasses.All.Contains(errorClass))
            {
                throw new CapabilityException(
                    ErrorClasses.InternalBug,
                    $"browser worker reported an error class outside the device taxonomy ('{errorClass ?? "<none>"}') for {capability}: {Truncate(errorMessage)}",
                    retryable: false);
            }

            throw new CapabilityException(errorClass, Truncate(errorMessage), retryable);
        }

        if (message["result"] is not JsonObject result)
        {
            throw new CapabilityException(
                ErrorClasses.InternalBug,
                $"browser worker answered {capability} ok without a result object",
                retryable: false);
        }

        var bytes = Encoding.UTF8.GetByteCount(result.ToJsonString());
        if (bytes > BrowserCapabilities.MaxResultBytes)
        {
            // The worker owns truncation (§3). A result this size means it did not do its
            // job, and passing it on would let one page flood the command path.
            throw new CapabilityException(
                ErrorClasses.InternalBug,
                $"browser worker result for {capability} is {bytes} bytes; the contract caps results at {BrowserCapabilities.MaxResultBytes} and the worker must truncate",
                retryable: false);
        }

        var forbidden = FindForbiddenKey(result, path: "result");
        if (forbidden is not null)
        {
            throw new CapabilityException(
                ErrorClasses.SecurityScopeError,
                $"browser worker result for {capability} carries a forbidden key ({forbidden}); session material never leaves the companion",
                retryable: false);
        }

        return result;
    }

    /// <summary>
    /// §6(b): any key that <see cref="BrowserCapabilities.IsForbiddenKey"/> refuses — the
    /// normalised-substring rule the worker applies — at any depth, arrays included.
    /// </summary>
    public static string? FindForbiddenKey(JsonNode? node, string path = "$")
    {
        switch (node)
        {
            case JsonObject obj:
                foreach (var pair in obj)
                {
                    if (BrowserCapabilities.IsForbiddenKey(pair.Key))
                    {
                        return $"{path}.{pair.Key}";
                    }

                    var nested = FindForbiddenKey(pair.Value, $"{path}.{pair.Key}");
                    if (nested is not null)
                    {
                        return nested;
                    }
                }

                return null;

            case JsonArray array:
                for (var i = 0; i < array.Count; i++)
                {
                    var nested = FindForbiddenKey(array[i], $"{path}[{i}]");
                    if (nested is not null)
                    {
                        return nested;
                    }
                }

                return null;

            default:
                return null;
        }
    }

    private async Task SendCancelAsync(WorkerProcess worker, string requestId)
    {
        try
        {
            if (await worker.TryWriteAsync(BrowserWorkerMessageTypes.NewCancel(requestId), CancellationToken.None).ConfigureAwait(false))
            {
                Interlocked.Increment(ref _cancelsSent);
            }
        }
        catch (Exception ex)
        {
            _logger.LogDebug("could not forward cancel for {RequestId}: {Reason}", requestId, ex.Message);
        }
    }

    // ------------------------------------------------------------------ lifecycle

    private async Task<WorkerProcess> EnsureWorkerAsync(CancellationToken cancellationToken)
    {
        await _startLock.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            if (_worker is { } current)
            {
                if (current.Alive)
                {
                    return current;
                }

                // Exited without the event having been processed yet: settle it now.
                HandleExit(current);
            }

            if (_stopping)
            {
                throw new CapabilityException(ErrorClasses.DependencyUnavailable, "the session companion is stopping", retryable: true);
            }

            await WaitForBackoffAsync(cancellationToken).ConfigureAwait(false);

            var worker = Spawn();
            _worker = worker;
            Interlocked.Increment(ref _starts);
            _logger.LogInformation(
                "browser worker started (pid={Pid}, start #{Start}): {Command} {Args}",
                worker.Pid,
                Starts,
                _options.WorkerCommand,
                string.Join(' ', _options.BuildArgumentList()));

            BrowserWorkerHello hello;
            try
            {
                hello = await worker.HelloTask.WaitAsync(_helloTimeout, cancellationToken).ConfigureAwait(false);
            }
            catch (TimeoutException)
            {
                _logger.LogError("browser worker (pid={Pid}) sent no hello within {Seconds:F0} s; killing it", worker.Pid, _helloTimeout.TotalSeconds);
                worker.Kill();
                HandleExit(worker);
                throw new CapabilityException(
                    ErrorClasses.DependencyUnavailable,
                    $"browser worker did not announce itself within {_helloTimeout.TotalSeconds:F0} s",
                    retryable: true);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                throw;
            }
            catch (Exception ex)
            {
                // Exited before hello, or the hello was malformed.
                worker.Kill();
                HandleExit(worker);
                throw new CapabilityException(
                    ErrorClasses.DependencyUnavailable,
                    $"browser worker failed to start: {ex.Message}",
                    retryable: true);
            }

            Hello = hello;
            _audit?.Write(
                AuditWorkerStarted,
                status: "ok",
                detail: $"pid={worker.Pid}; worker_version={hello.WorkerVersion}; browser={hello.BrowserChannel ?? "-"}/{hello.BrowserVersion ?? "-"}; available={hello.BrowserAvailable}; capabilities={hello.Capabilities.Count}; start={Starts}");
            _logger.LogInformation(
                "browser worker hello: version={Version} browser={Channel}/{BrowserVersion} available={Available} capabilities={Count}",
                hello.WorkerVersion,
                hello.BrowserChannel,
                hello.BrowserVersion,
                hello.BrowserAvailable,
                hello.Capabilities.Count);

            worker.LivenessTask = Task.Run(() => LivenessLoopAsync(worker), CancellationToken.None);
            return worker;
        }
        finally
        {
            _startLock.Release();
        }
    }

    private async Task WaitForBackoffAsync(CancellationToken cancellationToken)
    {
        var failures = Volatile.Read(ref _consecutiveFailures);
        if (failures <= 0 || _lastExitAt is null)
        {
            return;
        }

        var delay = _restartBackoff.NextDelay(Math.Min(failures - 1, 20));
        var elapsed = DateTimeOffset.UtcNow - _lastExitAt.Value;
        var remaining = delay - elapsed;
        if (remaining > TimeSpan.Zero)
        {
            _logger.LogInformation("browser worker restart #{Failures} in {Delay:F1} s (exponential backoff)", failures, remaining.TotalSeconds);
            await Task.Delay(remaining, cancellationToken).ConfigureAwait(false);
        }
    }

    private WorkerProcess Spawn()
    {
        Directory.CreateDirectory(_options.DataDir);
        Directory.CreateDirectory(_options.ProfileDir);

        var startInfo = new ProcessStartInfo
        {
            FileName = _options.WorkerCommand!,
            UseShellExecute = false,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
            WorkingDirectory = _options.DataDir,
            StandardInputEncoding = new UTF8Encoding(false),
            StandardOutputEncoding = new UTF8Encoding(false),
            StandardErrorEncoding = new UTF8Encoding(false),
        };
        foreach (var argument in _options.BuildArgumentList())
        {
            startInfo.ArgumentList.Add(argument);
        }

        // Harmless for a non-Python worker; decisive for the real one, whose stdout would
        // otherwise be block-buffered and cp1254-encoded under a redirected console.
        startInfo.Environment["PYTHONIOENCODING"] = "utf-8";
        startInfo.Environment["PYTHONUTF8"] = "1";
        startInfo.Environment["PYTHONUNBUFFERED"] = "1";

        Process process;
        try
        {
            process = Process.Start(startInfo)
                      ?? throw new InvalidOperationException("Process.Start returned null");
        }
        catch (Exception ex)
        {
            _lastExitAt = DateTimeOffset.UtcNow;
            Interlocked.Increment(ref _consecutiveFailures);
            throw new CapabilityException(
                ErrorClasses.DependencyUnavailable,
                $"cannot start the browser worker '{_options.WorkerCommand}': {ex.Message}",
                retryable: true);
        }

        var worker = new WorkerProcess(process);
        worker.ReaderTask = Task.Run(() => ReadStdoutAsync(worker), CancellationToken.None);
        worker.StderrTask = Task.Run(() => ReadStderrAsync(worker), CancellationToken.None);
        worker.ExitTask = Task.Run(async () =>
        {
            try
            {
                await process.WaitForExitAsync(CancellationToken.None).ConfigureAwait(false);
            }
            catch (Exception)
            {
                // The process handle is gone; treat as exited.
            }

            HandleExit(worker);
        });
        return worker;
    }

    /// <summary>Idempotent per worker: fail its in-flight requests, retire it, and account for the exit.</summary>
    private void HandleExit(WorkerProcess worker)
    {
        if (!worker.MarkExitHandled())
        {
            return;
        }

        var exitCode = worker.ExitCodeOrNull();
        var unexpected = !worker.ShutdownRequested && !_stopping;
        if (unexpected)
        {
            Interlocked.Increment(ref _consecutiveFailures);
        }

        _lastExitAt = DateTimeOffset.UtcNow;
        Interlocked.CompareExchange(ref _worker, null, worker);

        var pending = worker.Pending.Count;
        var exitText = worker.KillReason is { } killReason
            ? $"the browser worker was killed by the companion ({killReason})"
            : $"the browser worker exited (code {exitCode?.ToString(CultureInfo.InvariantCulture) ?? "?"})";
        foreach (var pair in worker.Pending)
        {
            pair.Value.TrySetException(new CapabilityException(
                ErrorClasses.DependencyUnavailable,
                $"{exitText} while this request was in flight; it is being restarted",
                retryable: true));
        }

        worker.Pending.Clear();
        worker.HelloFailed(new InvalidOperationException($"browser worker exited with code {exitCode?.ToString(CultureInfo.InvariantCulture) ?? "?"} before hello"));

        if (unexpected)
        {
            _logger.LogWarning(
                "browser worker (pid={Pid}) exited with code {Code}; {Pending} in-flight request(s) failed dependency_unavailable; consecutive failures={Failures}",
                worker.Pid,
                exitCode,
                pending,
                Volatile.Read(ref _consecutiveFailures));
        }
        else
        {
            _logger.LogInformation("browser worker (pid={Pid}) exited with code {Code} after shutdown", worker.Pid, exitCode);
        }

        _audit?.Write(
            AuditWorkerExited,
            status: unexpected ? "unexpected" : "shutdown",
            detail: $"pid={worker.Pid}; exit_code={exitCode?.ToString(CultureInfo.InvariantCulture) ?? "?"}; in_flight_failed={pending}; consecutive_failures={Volatile.Read(ref _consecutiveFailures)}");

        worker.DisposeQuietly();

        if (unexpected && _options.Eager && !_lifetime.IsCancellationRequested)
        {
            var failures = Volatile.Read(ref _consecutiveFailures);
            if (failures >= _eagerRestartCeiling)
            {
                // Ceiling: stop spending process starts on a worker that cannot stay up.
                // Logged once per episode; the next explicit request still tries (through
                // the backoff), and a success re-arms eager restarts.
                if (Interlocked.Exchange(ref _eagerRestartSuspended, 1) == 0)
                {
                    _logger.LogWarning(
                        "browser worker failed {Failures} times in a row; eager background restarts are suspended until a browser request succeeds (it is still started on the next request)",
                        failures);
                }

                return;
            }

            // An eager worker is kept warm: restart in the background, through the same
            // backoff, so the next request finds it up instead of paying the start cost.
            _ = Task.Run(async () =>
            {
                try
                {
                    await EnsureWorkerAsync(_lifetime.Token).ConfigureAwait(false);
                }
                catch (Exception ex)
                {
                    _logger.LogDebug("eager restart deferred to the next request: {Reason}", ex.Message);
                }
            });
        }
    }

    private async Task ReadStdoutAsync(WorkerProcess worker)
    {
        var reader = new BoundedLineReader(worker.StdoutStream, MaxStdoutLineBytes);
        try
        {
            while (true)
            {
                string? line;
                try
                {
                    line = await reader.ReadLineAsync(CancellationToken.None).ConfigureAwait(false);
                }
                catch (LineTooLongException ex)
                {
                    // The protocol channel is being flooded. Whatever the worker meant, the
                    // companion will not buffer it: kill the whole tree, let the exit path
                    // fail every in-flight request dependency_unavailable (retryable) and
                    // count it as a failure for the restart backoff.
                    _logger.LogError(
                        "browser worker (pid={Pid}) wrote a stdout line over {Max} bytes ({Seen}+ seen); killing it — in-flight requests fail dependency_unavailable and it is restarted",
                        worker.Pid,
                        ex.MaxLineBytes,
                        ex.SeenBytes);
                    Interlocked.Increment(ref _oversizeLineKills);
                    worker.KillReason = $"stdout line over {ex.MaxLineBytes} bytes";
                    worker.Kill();
                    return;
                }

                if (line is null)
                {
                    return;
                }

                if (string.IsNullOrWhiteSpace(line))
                {
                    continue;
                }

                JsonObject message;
                try
                {
                    message = JsonNode.Parse(line) as JsonObject
                              ?? throw new JsonException("not a JSON object");
                }
                catch (Exception ex)
                {
                    // Content deliberately not logged: stdout is the protocol channel and
                    // anything else on it is a worker bug, not something to echo.
                    _logger.LogWarning("browser worker wrote a non-protocol line to stdout ({Length} chars): {Reason}", line.Length, ex.Message);
                    continue;
                }

                Dispatch(worker, message);
            }
        }
        catch (Exception ex)
        {
            _logger.LogDebug("browser worker stdout reader ended: {Reason}", ex.Message);
        }
    }

    private void Dispatch(WorkerProcess worker, JsonObject message)
    {
        var type = message["type"]?.GetValue<string>();
        switch (type)
        {
            case BrowserWorkerMessageTypes.Hello:
                try
                {
                    worker.HelloReceived(BrowserWorkerHello.Parse(message));
                }
                catch (Exception ex)
                {
                    worker.HelloFailed(ex);
                }

                break;

            case BrowserWorkerMessageTypes.Result:
                var requestId = message["request_id"]?.GetValue<string>();
                if (requestId is not null && worker.Pending.TryRemove(requestId, out var tcs))
                {
                    tcs.TrySetResult(message);
                }
                else
                {
                    // Late (after timeout/cancel) or unknown: dropped, counted in the log only.
                    _logger.LogDebug("browser worker result for unknown/late request {RequestId} dropped", requestId ?? "<none>");
                }

                break;

            case BrowserWorkerMessageTypes.Pong:
                Interlocked.Increment(ref _pongsReceived);
                worker.PongReceived();
                break;

            case BrowserWorkerMessageTypes.Log:
                _logger.LogInformation(
                    "browser worker log [{Level}]: {Event}",
                    WorkerLogSanitizer.Sanitize(message["level"]?.GetValue<string>() ?? "info"),
                    WorkerLogSanitizer.Sanitize(message["event"]?.GetValue<string>() ?? "-"));
                break;

            default:
                _logger.LogDebug("browser worker sent an unknown message type '{Type}'; ignored", type ?? "<none>");
                break;
        }
    }

    private async Task ReadStderrAsync(WorkerProcess worker)
    {
        try
        {
            while (true)
            {
                var line = await worker.Stderr.ReadLineAsync(CancellationToken.None).ConfigureAwait(false);
                if (line is null)
                {
                    return;
                }

                if (line.Length > 0)
                {
                    // The worker's log is the companion's log — after the sanitiser: no
                    // query strings, no "key: value" credential lines, no unbounded lines.
                    // Never a result.
                    _logger.LogInformation("browser worker stderr: {Line}", WorkerLogSanitizer.Sanitize(line));
                }
            }
        }
        catch (Exception ex)
        {
            _logger.LogDebug("browser worker stderr reader ended: {Reason}", ex.Message);
        }
    }

    private async Task LivenessLoopAsync(WorkerProcess worker)
    {
        try
        {
            while (worker.Alive && !_lifetime.IsCancellationRequested)
            {
                await Task.Delay(_pingInterval, _lifetime.Token).ConfigureAwait(false);
                if (!worker.Alive)
                {
                    return;
                }

                if (worker.OutstandingPings >= LivenessMissedPongs)
                {
                    _logger.LogError(
                        "browser worker (pid={Pid}) missed {Count} consecutive pings; killing it so the next request gets a fresh one",
                        worker.Pid,
                        worker.OutstandingPings);
                    Interlocked.Increment(ref _livenessKills);
                    worker.KillReason = $"missed {worker.OutstandingPings} consecutive pings";
                    worker.Kill();
                    return;
                }

                if (await worker.TryWriteAsync(BrowserWorkerMessageTypes.NewPing(), _lifetime.Token).ConfigureAwait(false))
                {
                    worker.PingSent();
                    Interlocked.Increment(ref _pingsSent);
                }
            }
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception ex)
        {
            _logger.LogDebug("browser worker liveness loop ended: {Reason}", ex.Message);
        }
    }

    /// <summary>"shutdown" on stdin, a bounded wait, then Kill (with Chrome, the process tree).</summary>
    public async Task StopAsync()
    {
        _stopping = true;
        _lifetime.Cancel();

        var worker = _worker;
        if (worker is null)
        {
            return;
        }

        worker.ShutdownRequested = true;
        if (worker.Alive)
        {
            await worker.TryWriteAsync(BrowserWorkerMessageTypes.NewShutdown(), CancellationToken.None).ConfigureAwait(false);
            try
            {
                await worker.ExitTask!.WaitAsync(_shutdownGrace).ConfigureAwait(false);
            }
            catch (TimeoutException)
            {
                _logger.LogWarning("browser worker (pid={Pid}) ignored shutdown for {Seconds:F0} s; killing it", worker.Pid, _shutdownGrace.TotalSeconds);
                worker.Kill();
            }
        }

        HandleExit(worker);
    }

    public async ValueTask DisposeAsync()
    {
        await StopAsync().ConfigureAwait(false);
        _startLock.Dispose();
        _lifetime.Dispose();
    }

    private void AuditRequest(string capability, string requestId, string status, long durationMs, bool? retryable)
    {
        // The contract's audit row: capability, request id, outcome class, duration. Never
        // the payload, never the result, never a URL — those are Cloud Core's to keep.
        var detail = retryable is null
            ? $"request_id={requestId}; duration_ms={durationMs}"
            : $"request_id={requestId}; duration_ms={durationMs}; retryable={(retryable.Value ? "true" : "false")}";
        _audit?.Write(AuditRequestEvent, capability: capability, status: status, detail: detail);
    }

    private static string Truncate(string message)
        => message.Length <= ProtocolConstants.MaxErrorMessageLength ? message : message[..ProtocolConstants.MaxErrorMessageLength];

    /// <summary>One spawned worker: its streams, its pending table, its hello, its liveness counters.</summary>
    private sealed class WorkerProcess
    {
        private readonly Process _process;
        private readonly StreamWriter _stdin;
        private readonly SemaphoreSlim _writeLock = new(1, 1);
        private readonly TaskCompletionSource<BrowserWorkerHello> _hello = new(TaskCreationOptions.RunContinuationsAsynchronously);
        private int _exitHandled;
        private int _outstandingPings;

        public WorkerProcess(Process process)
        {
            _process = process;
            Pid = process.Id;
            _stdin = process.StandardInput;
            _stdin.AutoFlush = true;
            // The raw byte stream, not the StreamReader: the host reads it through a
            // BoundedLineReader so one line can never grow without limit.
            StdoutStream = process.StandardOutput.BaseStream;
            Stderr = process.StandardError;
        }

        public int Pid { get; }

        public Stream StdoutStream { get; }

        public StreamReader Stderr { get; }

        /// <summary>Why the companion killed this worker, when it did; null for a worker that exited on its own.</summary>
        public volatile string? KillReason;

        public ConcurrentDictionary<string, TaskCompletionSource<JsonObject>> Pending { get; } = new(StringComparer.Ordinal);

        public Task<BrowserWorkerHello> HelloTask => _hello.Task;

        public Task? ReaderTask { get; set; }

        public Task? StderrTask { get; set; }

        public Task? ExitTask { get; set; }

        public Task? LivenessTask { get; set; }

        public bool ShutdownRequested { get; set; }

        public bool SawSuccess { get; set; }

        public int OutstandingPings => Volatile.Read(ref _outstandingPings);

        public bool Alive
        {
            get
            {
                try
                {
                    return !_process.HasExited;
                }
                catch (Exception)
                {
                    return false;
                }
            }
        }

        public void HelloReceived(BrowserWorkerHello hello) => _hello.TrySetResult(hello);

        public void HelloFailed(Exception ex) => _hello.TrySetException(ex);

        public void PingSent() => Interlocked.Increment(ref _outstandingPings);

        public void PongReceived() => Interlocked.Exchange(ref _outstandingPings, 0);

        public bool MarkExitHandled() => Interlocked.Exchange(ref _exitHandled, 1) == 0;

        public int? ExitCodeOrNull()
        {
            try
            {
                return _process.HasExited ? _process.ExitCode : null;
            }
            catch (Exception)
            {
                return null;
            }
        }

        /// <summary>One line on stdin, whole. False (never throws) when the worker is gone.</summary>
        public async Task<bool> TryWriteAsync(JsonObject message, CancellationToken cancellationToken)
        {
            if (!Alive)
            {
                return false;
            }

            var line = message.ToJsonString();
            try
            {
                await _writeLock.WaitAsync(cancellationToken).ConfigureAwait(false);
            }
            catch (ObjectDisposedException)
            {
                return false;
            }

            try
            {
                await _stdin.WriteLineAsync(line.AsMemory(), cancellationToken).ConfigureAwait(false);
                return true;
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                throw;
            }
            catch (Exception)
            {
                return false;
            }
            finally
            {
                try
                {
                    _writeLock.Release();
                }
                catch (ObjectDisposedException)
                {
                }
            }
        }

        public void Kill()
        {
            try
            {
                if (!_process.HasExited)
                {
                    _process.Kill(entireProcessTree: true);
                }
            }
            catch (Exception)
            {
                // Already gone.
            }
        }

        public void DisposeQuietly()
        {
            try
            {
                _stdin.Dispose();
            }
            catch (Exception)
            {
            }

            try
            {
                _writeLock.Dispose();
            }
            catch (Exception)
            {
            }

            try
            {
                _process.Dispose();
            }
            catch (Exception)
            {
            }
        }
    }
}
