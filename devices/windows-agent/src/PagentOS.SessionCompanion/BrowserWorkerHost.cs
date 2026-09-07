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

    private const string AuditOrphanReaped = "browser_chrome_reaped";
    private const string AuditWorkerSwapped = "browser_worker_swapped";
    private const string AuditWorkerSwapFailed = "browser_worker_swap_failed";

    // Replaced as a whole by a staged update (SwapWorkerAsync); read through Volatile.
    private BrowserWorkerOptions _options;
    private readonly ILogger _logger;
    private readonly AuditLog? _audit;
    private ChromeOrphanReaper _reaper;
    private readonly SemaphoreSlim _swapLock = new(1, 1);
    private int _swaps;
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
    private int _orphanChromesReaped;

    public BrowserWorkerHost(
        BrowserWorkerOptions options,
        ILogger logger,
        AuditLog? audit = null,
        BackoffPolicy? restartBackoff = null,
        TimeSpan? helloTimeout = null,
        TimeSpan? pingInterval = null,
        TimeSpan? shutdownGrace = null,
        int? eagerRestartCeiling = null,
        ChromeOrphanReaper? reaper = null)
    {
        _options = options;
        _logger = logger;
        _audit = audit;
        _reaper = reaper ?? new ChromeOrphanReaper(options.ProfileDir, logger);
        _restartBackoff = restartBackoff ?? new BackoffPolicy(baseSeconds: 1.0, maxSeconds: 60.0);
        _helloTimeout = helloTimeout ?? TimeSpan.FromSeconds(20);
        _pingInterval = pingInterval ?? TimeSpan.FromSeconds(15);
        _shutdownGrace = shutdownGrace ?? TimeSpan.FromSeconds(5);
        _eagerRestartCeiling = Math.Max(1, eagerRestartCeiling ?? DefaultEagerRestartCeiling);
    }

    public BrowserWorkerOptions Options => Volatile.Read(ref _options);

    /// <summary>Staged updates that replaced the worker (M18.4 gap 4).</summary>
    public int Swaps => Volatile.Read(ref _swaps);

    public bool IsConfigured => Options.IsConfigured;

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

    /// <summary>
    /// Chrome main processes on the PagentOS profile terminated by the companion because
    /// no worker owned them any more (<see cref="ChromeOrphanReaper"/>): after every kill
    /// or unexpected exit, and before every start. Non-zero means a worker died without
    /// cleaning up — the 2026-09-03 cascade in the making, stopped.
    /// </summary>
    public int OrphanChromesReaped => Volatile.Read(ref _orphanChromesReaped);

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
            // The LAUNCH is inside the budget too. It used to be unbounded: starting the
            // worker (a child process) on a loaded machine could outlast the whole command,
            // and the service then synthesised its own untyped "session companion did not
            // answer" while this side was still waiting for a process to exist (intermittent
            // CI failure, 2026-09-04/05). A start that cannot finish in time is a typed,
            // retryable timeout that names what actually happened.
            worker = await EnsureWorkerWithinBudgetAsync(timeout, cancellationToken).ConfigureAwait(false);
            var remaining = timeout - stopwatch.Elapsed;
            if (remaining < MinimumExecBudget)
            {
                remaining = MinimumExecBudget;
            }
            var result = await ExecuteOnWorkerAsync(worker, requestId, capability, payload, remaining, cancellationToken).ConfigureAwait(false);
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
            // The request's budget elapsed. Tell the worker to stop working on it, but do NOT
            // wait for that write before answering: the cancel is a second pipe round trip, and
            // on a loaded machine it can consume the whole headroom this budget reserves below
            // the caller's own deadline. When that happened the caller timed out first and the
            // owner saw an untyped "did not answer" instead of this typed one (intermittent CI
            // failure, 2026-09-04). A late result for this id is dropped by the reader either way.
            _ = SendCancelAsync(worker, requestId);
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

            if (string.Equals(errorClass, ErrorClasses.BrowserLifecycleViolation, StringComparison.Ordinal))
            {
                // Passes through as itself and is NEVER retryable, whatever the worker
                // said: a retry is a fresh launch on a profile something else holds, and
                // that is the window cascade of 2026-09-03.
                throw new CapabilityException(errorClass, Truncate(errorMessage), retryable: false);
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

    /// <summary>
    /// The smallest exec budget left after a slow start: enough for the worker to answer or
    /// for this side to time out typed, never zero.
    /// </summary>
    private static readonly TimeSpan MinimumExecBudget = TimeSpan.FromMilliseconds(200);

    /// <summary>
    /// Start (or reuse) the worker within the request's own budget, so a slow start is
    /// answered as a typed <see cref="ErrorClasses.Timeout"/> instead of leaving the
    /// service to give up first.
    /// </summary>
    private async Task<WorkerProcess> EnsureWorkerWithinBudgetAsync(
        TimeSpan budget,
        CancellationToken cancellationToken)
    {
        using var startCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        startCts.CancelAfter(budget);
        try
        {
            return await EnsureWorkerAsync(startCts.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            throw new CapabilityException(
                ErrorClasses.Timeout,
                $"the browser worker did not start within {budget.TotalSeconds:F1} s",
                retryable: true);
        }
    }

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

            // A worker that died earlier — or one killed while the companion was not
            // running — cannot have closed its Chrome. Launching a new one onto a
            // profile an orphan still holds opens a window in the orphan instead.
            ReapOrphans("before worker start");

            var worker = Spawn(Options);
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
                worker.KillReason = $"no hello within {_helloTimeout.TotalSeconds:F0} s";
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
                if (worker.Alive)
                {
                    worker.KillReason = $"hello failed: {ex.Message}";
                }

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
                detail: $"pid={worker.Pid}; worker_version={hello.WorkerVersion}; browser={hello.BrowserChannel ?? "-"}/{hello.BrowserVersion ?? "-"}; available={hello.BrowserAvailable}; capabilities={hello.Capabilities.Count}; start={Starts}; lifecycle_fault={hello.LifecycleFault ?? "none"}; module={hello.ModuleFile ?? "-"}; package_sha256={hello.PackageSha256 ?? "-"}");
            if (hello.LifecycleFault is not null)
            {
                _logger.LogError("browser worker (pid={Pid}) reports a durable lifecycle fault; research-profile launches are refused until it ages out: {Fault}", worker.Pid, hello.LifecycleFault);
            }
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

    private WorkerProcess Spawn(BrowserWorkerOptions options, bool candidate = false)
    {
        Directory.CreateDirectory(options.DataDir);
        Directory.CreateDirectory(options.ProfileDir);

        var startInfo = new ProcessStartInfo
        {
            FileName = options.WorkerCommand!,
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
        foreach (var argument in options.BuildArgumentList())
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
                $"cannot start the browser worker '{options.WorkerCommand}': {ex.Message}",
                retryable: true);
        }

        var worker = new WorkerProcess(process) { IsCandidate = candidate };
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
        if (worker.IsCandidate)
        {
            // A staged-update candidate that died before it was promoted: nothing about the
            // CURRENT worker changed. No failure counted, no eager restart, and no reaping
            // by profile - the candidate never had a request, so it opened no browser, while
            // the current worker may well have one on that very profile.
            foreach (var pair in worker.Pending)
            {
                pair.Value.TrySetException(new CapabilityException(ErrorClasses.DependencyUnavailable, "the candidate browser worker exited", retryable: true));
            }

            worker.Pending.Clear();
            worker.HelloFailed(new InvalidOperationException($"candidate browser worker exited with code {exitCode?.ToString(CultureInfo.InvariantCulture) ?? "?"} before hello"));
            _logger.LogInformation("browser worker candidate (pid={Pid}) exited with code {Code} ({Reason}); the current worker is untouched", worker.Pid, exitCode, worker.KillReason ?? "exited on its own");
            worker.DisposeQuietly();
            return;
        }

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
                "browser worker (pid={Pid}) exited with code {Code} ({Reason}); {Pending} in-flight request(s) failed dependency_unavailable; consecutive failures={Failures}",
                worker.Pid,
                exitCode,
                worker.KillReason is { } reason ? $"killed by the companion: {reason}" : "exited on its own",
                pending,
                Volatile.Read(ref _consecutiveFailures));
        }
        else
        {
            _logger.LogInformation(
                "browser worker (pid={Pid}) exited with code {Code} after shutdown ({Reason})",
                worker.Pid,
                exitCode,
                worker.KillReason is { } reason ? $"killed by the companion: {reason}" : "exited on its own");
        }

        _audit?.Write(
            AuditWorkerExited,
            status: unexpected ? "unexpected" : "shutdown",
            detail: $"pid={worker.Pid}; exit_code={exitCode?.ToString(CultureInfo.InvariantCulture) ?? "?"}; kill_reason={worker.KillReason ?? "-"}; in_flight_failed={pending}; consecutive_failures={Volatile.Read(ref _consecutiveFailures)}");

        worker.DisposeQuietly();

        // Whatever took the worker down, its Chrome is nobody's now. The process-tree kill
        // usually gets it, but "usually" is how the 2026-09-03 cascade started: reap by
        // profile, and say which pids.
        ReapOrphans(worker.KillReason is { } killedFor
            ? $"after killing the worker: {killedFor}"
            : unexpected ? "after an unexpected worker exit" : "after worker shutdown");

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

    /// <summary>
    /// M18.4 gap 4 - the staged worker update. A candidate worker is started from
    /// <paramref name="candidate"/> BESIDE the running one and must say hello, keep every
    /// capability the current worker advertised (and browser availability), and match the
    /// expected release when one is named. Only then does the current worker drain: its
    /// in-flight requests finish (new requests wait on the start lock, bounded by their own
    /// budgets), it must hold no open browser session, it is told to shut down (its sessions
    /// close the way a companion stop closes them) and it retires; then new work routes to
    /// the candidate. The candidate is never handed a request while the old worker lives -
    /// one research profile, one Chrome - and the owner's own browser is never touched. A
    /// candidate that fails is killed and the current worker keeps serving, untouched; a
    /// current worker that does not drain within <paramref name="drainTimeout"/> keeps
    /// serving too (outcome <c>busy</c>): in-flight owned work is preserved, the update is
    /// simply retried later.
    /// </summary>
    public async Task<BrowserWorkerSwapResult> SwapWorkerAsync(
        BrowserWorkerOptions candidate,
        TimeSpan drainTimeout,
        string? expectedVersion = null,
        string? expectedPackageSha256 = null,
        CancellationToken cancellationToken = default)
    {
        if (!candidate.IsConfigured)
        {
            return BrowserWorkerSwapResult.Refused("the candidate names no worker command");
        }

        if (_stopping)
        {
            return BrowserWorkerSwapResult.Refused("the session companion is stopping");
        }

        await _swapLock.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            var current = _worker;
            var currentHello = current is { Alive: true } ? Hello : null;
            var oldPid = current is { Alive: true } ? current.Pid : (int?)null;

            // 1. The candidate, beside the current worker. It receives nothing yet.
            WorkerProcess probe;
            try
            {
                probe = Spawn(candidate, candidate: true);
            }
            catch (CapabilityException ex)
            {
                return SwapFailed(BrowserWorkerSwapResult.OutcomeCandidateFailed, oldPid, null, currentHello?.WorkerVersion, null, ex.Message);
            }

            BrowserWorkerHello hello;
            try
            {
                hello = await probe.HelloTask.WaitAsync(_helloTimeout, cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                DiscardCandidate(probe, "swap cancelled");
                throw;
            }
            catch (Exception ex)
            {
                DiscardCandidate(probe, $"no usable hello: {ex.Message}");
                return SwapFailed(BrowserWorkerSwapResult.OutcomeCandidateFailed, oldPid, probe.Pid, currentHello?.WorkerVersion, null, $"candidate worker (pid {probe.Pid}) did not announce itself: {ex.Message}");
            }

            var rejections = VerifyCandidateHello(hello, currentHello, expectedVersion, expectedPackageSha256);
            if (rejections.Count > 0)
            {
                DiscardCandidate(probe, "rejected: " + string.Join("; ", rejections));
                return SwapFailed(BrowserWorkerSwapResult.OutcomeCandidateRejected, oldPid, probe.Pid, currentHello?.WorkerVersion, hello.WorkerVersion, string.Join("; ", rejections));
            }

            // 2. The drain, under the start lock: a new request waits here (bounded by its
            //    own budget) instead of landing on a worker that is about to retire.
            await _startLock.WaitAsync(cancellationToken).ConfigureAwait(false);
            var drain = Stopwatch.StartNew();
            var pendingAtStart = 0;
            try
            {
                current = _worker;
                if (current is { Alive: true })
                {
                    pendingAtStart = current.Pending.Count;
                    var deadline = DateTimeOffset.UtcNow + drainTimeout;
                    var idle = false;
                    while (current.Alive)
                    {
                        if (current.Pending.IsEmpty && await WorkerHoldsNoSessionAsync(current, cancellationToken).ConfigureAwait(false))
                        {
                            idle = true;
                            break;
                        }

                        if (DateTimeOffset.UtcNow >= deadline)
                        {
                            break;
                        }

                        await Task.Delay(50, cancellationToken).ConfigureAwait(false);
                    }

                    if (current.Alive && !idle)
                    {
                        DiscardCandidate(probe, "current worker busy");
                        return SwapFailed(
                            BrowserWorkerSwapResult.OutcomeBusy,
                            current.Pid,
                            probe.Pid,
                            currentHello?.WorkerVersion,
                            hello.WorkerVersion,
                            $"the current worker (pid {current.Pid}) still had {current.Pending.Count} request(s) in flight or a browser session open after {drainTimeout.TotalSeconds:F0} s; it keeps serving",
                            pendingAtStart,
                            drain.ElapsedMilliseconds);
                    }

                    // 3. Retire the old worker: shutdown on stdin, a bounded wait, then kill.
                    if (current.Alive)
                    {
                        current.ShutdownRequested = true;
                        await current.TryWriteAsync(BrowserWorkerMessageTypes.NewShutdown(), CancellationToken.None).ConfigureAwait(false);
                        try
                        {
                            await current.ExitTask!.WaitAsync(_shutdownGrace).ConfigureAwait(false);
                        }
                        catch (TimeoutException)
                        {
                            _logger.LogWarning("browser worker (pid={Pid}) ignored shutdown for {Seconds:F0} s during a staged update; killing it", current.Pid, _shutdownGrace.TotalSeconds);
                            current.KillReason = $"ignored shutdown for {_shutdownGrace.TotalSeconds:F0} s (staged update)";
                            current.Kill();
                        }
                    }
                }

                // 4. The candidate takes over. Options first (Spawn reads them for the next
                //    restart), then the reaper if the profile moved, then the worker slot;
                //    HandleExit on the old worker no longer matches the slot and leaves it alone.
                Volatile.Write(ref _options, candidate);
                if (!string.Equals(_reaper.ProfileDir, ChromeOrphanReaper.NormalizePath(candidate.ProfileDir), StringComparison.OrdinalIgnoreCase))
                {
                    _reaper = new ChromeOrphanReaper(candidate.ProfileDir, _logger);
                }

                probe.IsCandidate = false;
                _worker = probe;
                Hello = hello;
                Interlocked.Increment(ref _starts);
                Interlocked.Increment(ref _swaps);
                Interlocked.Exchange(ref _consecutiveFailures, 0);
                if (current is not null)
                {
                    // The old worker's exit row, and its Chrome (by profile) reaped: the
                    // candidate has not opened a browser - it has not had a request.
                    HandleExit(current);
                }

                probe.LivenessTask = Task.Run(() => LivenessLoopAsync(probe), CancellationToken.None);
                var result = new BrowserWorkerSwapResult(
                    BrowserWorkerSwapResult.OutcomeSwapped,
                    true,
                    oldPid,
                    probe.Pid,
                    currentHello?.WorkerVersion,
                    hello.WorkerVersion,
                    pendingAtStart,
                    drain.ElapsedMilliseconds,
                    null);
                _audit?.Write(
                    AuditWorkerSwapped,
                    status: "ok",
                    detail: $"old_pid={oldPid?.ToString(CultureInfo.InvariantCulture) ?? "-"}; new_pid={probe.Pid}; old_version={currentHello?.WorkerVersion ?? "-"}; new_version={hello.WorkerVersion}; pending_at_drain_start={pendingAtStart}; drain_ms={drain.ElapsedMilliseconds}; capabilities={hello.Capabilities.Count}; module={hello.ModuleFile ?? "-"}; package_sha256={hello.PackageSha256 ?? "-"}");
                _logger.LogInformation(
                    "browser worker swapped: pid {OldPid} -> {NewPid}, version {OldVersion} -> {NewVersion}, drained {Pending} in-flight request(s) in {Drain} ms",
                    oldPid,
                    probe.Pid,
                    currentHello?.WorkerVersion ?? "-",
                    hello.WorkerVersion,
                    pendingAtStart,
                    drain.ElapsedMilliseconds);
                return result;
            }
            finally
            {
                _startLock.Release();
            }
        }
        finally
        {
            _swapLock.Release();
        }
    }

    /// <summary>
    /// What a candidate must satisfy to replace the current worker: the capabilities the
    /// companion has been answering for, browser availability, and the release the updater
    /// expects (when named). Every failure is a sentence; an empty list is a pass.
    /// </summary>
    public static IReadOnlyList<string> VerifyCandidateHello(BrowserWorkerHello candidate, BrowserWorkerHello? current, string? expectedVersion, string? expectedPackageSha256)
    {
        var reasons = new List<string>();
        if (candidate.Capabilities.Count == 0)
        {
            reasons.Add("the candidate advertises no capability");
        }

        if (current is not null)
        {
            var missing = current.Capabilities.Where(c => !candidate.Capabilities.Contains(c, StringComparer.Ordinal)).ToList();
            if (missing.Count > 0)
            {
                reasons.Add($"the candidate drops capabilit{(missing.Count == 1 ? "y" : "ies")} the current worker serves: {string.Join(", ", missing)}");
            }

            if (current.BrowserAvailable && !candidate.BrowserAvailable)
            {
                reasons.Add("the candidate reports the browser unavailable while the current worker has it");
            }
        }

        if (expectedVersion is not null && !string.Equals(candidate.WorkerVersion, expectedVersion, StringComparison.Ordinal))
        {
            reasons.Add($"the candidate is worker {candidate.WorkerVersion}, expected {expectedVersion}");
        }

        if (expectedPackageSha256 is not null && !string.Equals(candidate.PackageSha256, expectedPackageSha256, StringComparison.OrdinalIgnoreCase))
        {
            reasons.Add($"the candidate's package digest is {candidate.PackageSha256 ?? "absent"}, expected {expectedPackageSha256}");
        }

        return reasons;
    }

    /// <summary>
    /// Ask the worker whether it holds an open browser session (<c>browser.worker_status</c>).
    /// A worker that cannot be asked, or answers with a session count above zero, is not idle.
    /// Owned media playing in a session is in-flight owned work: the swap waits for it.
    /// </summary>
    private async Task<bool> WorkerHoldsNoSessionAsync(WorkerProcess worker, CancellationToken cancellationToken)
    {
        var requestId = "swap-" + Guid.NewGuid().ToString("N");
        try
        {
            var status = await ExecuteOnWorkerAsync(worker, requestId, BrowserCapabilities.WorkerStatus, new JsonObject(), TimeSpan.FromSeconds(5), cancellationToken).ConfigureAwait(false);
            return CountSessions(status) == 0;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception ex)
        {
            _logger.LogDebug("staged update: the current worker did not answer worker_status ({Reason}); treating it as not idle", ex.Message);
            return false;
        }
        finally
        {
            worker.Pending.TryRemove(requestId, out _);
        }
    }

    /// <summary>The session count a worker_status result carries: an array's length, a number, or 0 when absent.</summary>
    public static int CountSessions(JsonObject status)
    {
        return status["sessions"] switch
        {
            JsonArray array => array.Count,
            JsonValue value when value.TryGetValue<int>(out var count) => count,
            _ => 0,
        };
    }

    private void DiscardCandidate(WorkerProcess probe, string reason)
    {
        // HandleExit's candidate branch: no failure counted against the CURRENT worker (which
        // keeps serving), no eager restart, no reaping by profile - the candidate never had a
        // request, so it has no browser of its own to reap.
        probe.ShutdownRequested = true;
        probe.KillReason = reason;
        probe.Kill();
        HandleExit(probe);
    }

    private BrowserWorkerSwapResult SwapFailed(string outcome, int? oldPid, int? newPid, string? oldVersion, string? newVersion, string reason, int pendingAtStart = 0, long drainMs = 0)
    {
        _audit?.Write(AuditWorkerSwapFailed, status: outcome, detail: $"old_pid={oldPid?.ToString(CultureInfo.InvariantCulture) ?? "-"}; candidate_pid={newPid?.ToString(CultureInfo.InvariantCulture) ?? "-"}; reason={reason}");
        _logger.LogWarning("browser worker staged update not applied ({Outcome}): {Reason}", outcome, reason);
        return new BrowserWorkerSwapResult(outcome, false, oldPid, newPid, oldVersion, newVersion, pendingAtStart, drainMs, reason);
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
                worker.KillReason = $"ignored shutdown for {_shutdownGrace.TotalSeconds:F0} s";
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
        // The same row in the companion log, so the file alone can reconstruct a sequence.
        _logger.LogInformation("browser request {Capability} request_id={RequestId} outcome={Outcome} duration_ms={DurationMs}{Retryable}", capability, requestId, status, durationMs, retryable is null ? string.Empty : $" retryable={(retryable.Value ? "true" : "false")}");
    }

    private void ReapOrphans(string reason)
    {
        var pids = _reaper.Reap(reason);
        if (pids.Count == 0)
        {
            return;
        }

        Interlocked.Add(ref _orphanChromesReaped, pids.Count);
        var list = string.Join(",", pids.Select(pid => pid.ToString(CultureInfo.InvariantCulture)));
        _logger.LogWarning(
            "{Class}: {Count} orphan {Process} process(es) on the PagentOS profile terminated {Reason}: pids={Pids} (profile={Profile}; total reaped={Total})",
            ErrorClasses.BrowserLifecycleViolation,
            pids.Count,
            _reaper.ProcessName,
            reason,
            list,
            _reaper.ProfileDir,
            OrphanChromesReaped);
        _audit?.Write(AuditOrphanReaped, status: ErrorClasses.BrowserLifecycleViolation, detail: $"pids={list}; reason={reason}; total={OrphanChromesReaped}");
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

        /// <summary>A staged-update candidate not yet promoted (M18.4 gap 4): its exit is nobody's failure.</summary>
        public volatile bool IsCandidate;

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
