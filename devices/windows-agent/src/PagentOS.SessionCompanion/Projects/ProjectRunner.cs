using System.Diagnostics;
using System.Globalization;
using System.Net;
using System.Net.Sockets;
using System.Runtime.Versioning;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Documents;
using PagentOS.SessionCompanion.Operator;

namespace PagentOS.SessionCompanion.Projects;

/// <summary>The project a run or a test belongs to: its id, slug, resolved folder, validated manifest and (M25) which root it was found under.</summary>
public sealed record ProjectContext(string ProjectId, string Slug, string Folder, ProjectManifest Manifest, ProjectScope Scope = ProjectScope.Web);

/// <summary>What <c>project.test</c> observed: the exit code, the counts parsed from the runner's output (null when it printed none), the log's tail.</summary>
public sealed record TestOutcome(int ExitCode, int? Passed, int? Failed, string ReportTail, bool Truncated, long DurationMs, string LogPath);

/// <summary>What <c>project.stop</c> observed.</summary>
public sealed record StopOutcome(bool WasRunning, int? Pid, bool Exited);

/// <summary>
/// M25: what a BATCH <c>project.run</c> observed — Blender headless or Unity in batch mode.
/// These runtimes do their work and end, so the answer is the exit itself: the code, how long
/// it took, and the tail of what the tool said (for Unity, its <c>-logFile</c> too).
/// </summary>
public sealed record BatchOutcome(ProjectRuntime Runtime, int ExitCode, double Seconds, string LogTail, bool Truncated, string LogPath, int Pid);

/// <summary>
/// A run the companion owns: the process, the job it lives in, its bounded log. States:
/// <c>starting</c> (the slot is held, the port not yet answering), <c>running</c>,
/// <c>exited</c> (ended by itself; <see cref="ExitCode"/> says how), <c>stopped</c>
/// (<see cref="StopReason"/>: <c>stop</c>, <c>lifetime</c>, <c>port_never_answered</c>,
/// <c>cancelled</c>, <c>shutdown</c>).
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class ProjectRun
{
    public const string StateStarting = "starting";
    public const string StateRunning = "running";
    public const string StateExited = "exited";
    public const string StateStopped = "stopped";

    private readonly object _lock = new();
    private string _state = StateStarting;

    internal ProjectRun(ProjectContext project, string commandKey, int port, string logPath, BoundedLog log)
    {
        Project = project;
        CommandKey = commandKey;
        Port = port;
        LogPath = logPath;
        Log = log;
        StartedAt = DateTimeOffset.UtcNow;
    }

    public ProjectContext Project { get; }

    public string CommandKey { get; }

    public int Port { get; }

    public DateTimeOffset StartedAt { get; }

    public DateTimeOffset? EndedAt { get; private set; }

    public int Pid { get; internal set; }

    public int? ExitCode { get; private set; }

    public string? StopReason { get; private set; }

    public string LogPath { get; }

    internal BoundedLog Log { get; }

    internal Process? Process { get; set; }

    internal JobObject? Job { get; set; }

    internal CancellationTokenSource Lifetime { get; } = new();

    public string State
    {
        get
        {
            lock (_lock)
            {
                return _state;
            }
        }
    }

    public bool IsActive => State is StateStarting or StateRunning;

    public string LogTail => Log.Tail;

    public bool LogTruncated => Log.Truncated;

    public TimeSpan Uptime => (EndedAt ?? DateTimeOffset.UtcNow) - StartedAt;

    /// <summary>The job's bounds as the kernel holds them (null once the job is gone).</summary>
    public JobLimits? Limits
    {
        get
        {
            try
            {
                return Job?.ReadLimits();
            }
            catch (Exception)
            {
                return null;
            }
        }
    }

    /// <summary>Whether the process is a member of the run's own job — asked right after assignment and by the lab.</summary>
    public bool InJob
    {
        get
        {
            try
            {
                return Process is not null && Job is not null && Job.Contains(Process);
            }
            catch (Exception)
            {
                return false;
            }
        }
    }

    internal bool TryMarkRunning()
    {
        lock (_lock)
        {
            if (_state != StateStarting)
            {
                return false;
            }

            _state = StateRunning;
            return true;
        }
    }

    internal bool TryMarkExited(int exitCode)
    {
        lock (_lock)
        {
            if (_state is not (StateStarting or StateRunning))
            {
                return false;
            }

            _state = StateExited;
            ExitCode = exitCode;
            EndedAt = DateTimeOffset.UtcNow;
            return true;
        }
    }

    internal bool TryMarkStopped(string reason)
    {
        lock (_lock)
        {
            if (_state is not (StateStarting or StateRunning))
            {
                return false;
            }

            _state = StateStopped;
            StopReason = reason;
            EndedAt = DateTimeOffset.UtcNow;
            return true;
        }
    }
}

/// <summary>
/// The process side of the projects family (ADR-0086 decision 3). Every process here is the
/// companion's OWN child, created by this class, placed in a <see cref="JobObject"/> with the
/// family's bounds the instant it exists and checked to be there; its environment is a
/// scrubbed copy of the companion's (<see cref="IsSecretVariable"/>: every <c>PAGENTOS_*</c>
/// and every credential-shaped variable removed; the system directories first on PATH); its
/// working directory is the project folder; its stdout and stderr go to a bounded log under
/// <c>&lt;root&gt;\.pagentos\</c>. A run holds one of <see cref="MaxRunning"/> slots, must
/// bind the manifest's port (checked free first) within <see cref="PortWait"/>, and ends by
/// itself after <see cref="Lifetime"/>. Stopping a run terminates ITS job — never a process
/// by pid, so nothing of the owner's (Chrome, PowerShell, Node, Python) is ever touched.
/// Disposing the runner ends every job it holds.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class ProjectRunner : IDisposable
{
    public const string RunLogName = "run.log";
    public const string TestLogName = "test.log";
    public const int TailChars = 8 * 1024;

    private static readonly string[] SecretSuffixes = ["_TOKEN", "_SECRET", "_PASSWORD", "_PASSWD", "_KEY", "_CREDENTIAL", "_CREDENTIALS", "_AUTH"];
    private static readonly string[] SecretNames = ["TOKEN", "SECRET", "PASSWORD", "PASSWD", "APIKEY", "API_KEY", "AUTHORIZATION"];
    private static readonly TimeSpan ProbeInterval = TimeSpan.FromMilliseconds(100);
    private static readonly TimeSpan ProbeTimeout = TimeSpan.FromMilliseconds(500);

    private readonly ILogger _logger;
    private readonly Func<ProcessStartInfo, Process?> _start;
    private readonly Dictionary<string, ProjectRun> _runs = new(StringComparer.Ordinal);
    private readonly object _lock = new();
    private int _processesStarted;
    private int _testsActive;
    private bool _disposed;

    /// <param name="start">The seam a lab counts through; default <see cref="Process.Start(ProcessStartInfo)"/>.</param>
    /// <param name="lifetime">A run's lifetime; default <see cref="ProjectCapabilityNames.RunLifetime"/> (a lab shortens it).</param>
    /// <param name="testTimeout">The most a test may take; default <see cref="ProjectCapabilityNames.TestTimeout"/>.</param>
    /// <param name="portWait">How long a run may take to answer on its port; default <see cref="ProjectCapabilityNames.PortWait"/>.</param>
    /// <param name="maxRunning">Concurrent runs (and, separately, concurrent tests); default <see cref="ProjectCapabilityNames.MaxRunningProjects"/>.</param>
    /// <param name="blenderLimit">M25: the wall-clock bound on a Blender batch run; default <see cref="SceneCapabilityNames.BlenderRunLimit"/> (a lab shortens it to prove the job ends the child).</param>
    /// <param name="unityLimit">M25: the wall-clock bound on a Unity batch run; default <see cref="SceneCapabilityNames.UnityRunLimit"/>.</param>
    /// <param name="nativeLimit">M28: the wall-clock bound on a <c>dotnet</c> / <c>makeappx</c> run; default <see cref="NativeCapabilityNames.RunLimit"/> (a lab shortens it to prove the job ends the child).</param>
    public ProjectRunner(ILogger logger, Func<ProcessStartInfo, Process?>? start = null, TimeSpan? lifetime = null, TimeSpan? testTimeout = null, TimeSpan? portWait = null, int? maxRunning = null, TimeSpan? blenderLimit = null, TimeSpan? unityLimit = null, TimeSpan? nativeLimit = null)
    {
        _logger = logger;
        _start = start ?? Process.Start;
        Lifetime = lifetime ?? ProjectCapabilityNames.RunLifetime;
        TestTimeout = testTimeout ?? ProjectCapabilityNames.TestTimeout;
        PortWait = portWait ?? ProjectCapabilityNames.PortWait;
        MaxRunning = maxRunning ?? ProjectCapabilityNames.MaxRunningProjects;
        BlenderLimit = blenderLimit ?? SceneCapabilityNames.BlenderRunLimit;
        UnityLimit = unityLimit ?? SceneCapabilityNames.UnityRunLimit;
        NativeLimit = nativeLimit ?? NativeCapabilityNames.RunLimit;
    }

    /// <summary>M25: the most a Blender batch run may take before its job is ended.</summary>
    public TimeSpan BlenderLimit { get; }

    /// <summary>M25: the most a Unity batch run may take before its job is ended.</summary>
    public TimeSpan UnityLimit { get; }

    /// <summary>M28: the most a native build run — a compile, a test, a publish, a pack — may take before its job is ended.</summary>
    public TimeSpan NativeLimit { get; }

    /// <summary>
    /// The wall-clock bound for a runtime. M28 makes this the bound <c>project.test</c> uses
    /// too, rather than a flat <see cref="TestTimeout"/>: a <c>dotnet test</c> restores, compiles
    /// and then runs, which is not something five minutes reliably holds, while a node runner
    /// that has not finished in five minutes has not failed in a way twenty more would fix.
    /// The bound follows the RUNTIME the manifest named, which is the only thing that knows
    /// which of those two a command is.
    /// </summary>
    public TimeSpan LimitFor(ProjectRuntime runtime)
        => runtime switch
        {
            ProjectRuntime.Blender => BlenderLimit,
            ProjectRuntime.Unity => UnityLimit,
            ProjectRuntime.Dotnet or ProjectRuntime.MakeAppx => NativeLimit,
            _ => TestTimeout,
        };

    public TimeSpan Lifetime { get; }

    public TimeSpan TestTimeout { get; }

    public TimeSpan PortWait { get; }

    public int MaxRunning { get; }

    /// <summary>Processes this runner has started, ever — a refusal leaves this where it was.</summary>
    public int ProcessesStarted => Volatile.Read(ref _processesStarted);

    /// <summary>Runs still holding a slot.</summary>
    public int ActiveRuns
    {
        get
        {
            lock (_lock)
            {
                return _runs.Values.Count(r => r.IsActive);
            }
        }
    }

    public IReadOnlyList<ProjectRun> Runs
    {
        get
        {
            lock (_lock)
            {
                return [.. _runs.Values];
            }
        }
    }

    /// <summary>The last run known for a project (active or ended), or null when it never ran in this companion's life.</summary>
    public ProjectRun? RunOf(string projectId)
    {
        lock (_lock)
        {
            return _runs.GetValueOrDefault(projectId);
        }
    }

    // ================================================================== environment

    /// <summary>§3: the variables a child never sees — every <c>PAGENTOS_*</c>, and every name shaped like a credential.</summary>
    public static bool IsSecretVariable(string name)
    {
        var upper = name.ToUpperInvariant();
        if (upper.StartsWith("PAGENTOS_", StringComparison.Ordinal))
        {
            return true;
        }

        if (SecretNames.Contains(upper, StringComparer.Ordinal))
        {
            return true;
        }

        foreach (var suffix in SecretSuffixes)
        {
            if (upper.EndsWith(suffix, StringComparison.Ordinal))
            {
                return true;
            }
        }

        return false;
    }

    /// <summary>Removes every secret-shaped variable from <paramref name="environment"/> (a start info's copy) and returns their names, for the log.</summary>
    public static IReadOnlyList<string> Scrub(IDictionary<string, string?> environment)
    {
        var removed = environment.Keys.Where(IsSecretVariable).ToList();
        foreach (var key in removed)
        {
            environment.Remove(key);
        }

        return removed;
    }

    /// <summary>The first <paramref name="executable"/> on the companion's PATH that is a real file — the Store's app-execution aliases under <c>WindowsApps</c> are skipped.</summary>
    public static string? FindOnPath(string executable)
    {
        var path = Environment.GetEnvironmentVariable("PATH") ?? string.Empty;
        foreach (var raw in path.Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            var directory = raw.Trim('"');
            if (directory.Contains(@"\WindowsApps\", StringComparison.OrdinalIgnoreCase) || directory.EndsWith(@"\WindowsApps", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            try
            {
                var candidate = Path.Combine(directory, executable);
                if (File.Exists(candidate) && !ProjectRoots.IsReparsePoint(candidate))
                {
                    return Path.GetFullPath(candidate);
                }
            }
            catch (Exception)
            {
                // A malformed PATH entry is nobody's runtime.
            }
        }

        return null;
    }

    /// <summary>The executable and leading arguments a runtime resolves to, or <c>dependency_unavailable</c> when the machine lacks it.</summary>
    public static (string Executable, IReadOnlyList<string> Prefix) ResolveRuntime(ProjectRuntime runtime)
    {
        switch (runtime)
        {
            case ProjectRuntime.Python:
                return (FindOnPath("python.exe") ?? throw Missing("python.exe"), []);
            case ProjectRuntime.Node:
                return (FindOnPath("node.exe") ?? throw Missing("node.exe"), []);
            case ProjectRuntime.Npm:
                {
                    // npm through node.exe and npm's own CLI script beside it — never through
                    // npm.cmd, whose cmd.exe quoting has no safe form for a path with spaces.
                    var node = FindOnPath("node.exe") ?? throw Missing("node.exe");
                    var npmCli = Path.Combine(Path.GetDirectoryName(node)!, "node_modules", "npm", "bin", "npm-cli.js");
                    if (!File.Exists(npmCli))
                    {
                        throw Missing("npm-cli.js beside node.exe");
                    }

                    return (node, [npmCli]);
                }

            case ProjectRuntime.Blender:
            case ProjectRuntime.Unity:
                // M25: DETECTED, never searched for on PATH (SceneTools). "blender" means the
                // installed Blender, not whatever is called blender.exe earliest on PATH.
                return (Scenes.SceneTools.Require(runtime).Executable, []);

            case ProjectRuntime.Dotnet:
            case ProjectRuntime.MakeAppx:
                // M28: detected the way the Cloud Core detects them (NativeTools) — the .NET
                // installer's own directory before PATH, and the Windows Kits' layout for
                // makeappx, which is never on PATH.
                return (Native.NativeTools.Require(runtime).Executable, []);

            default:
                throw new CapabilityException(ErrorClasses.InternalBug, $"unknown runtime {runtime}", retryable: false);
        }
    }

    private static CapabilityException Missing(string what)
        => new(ErrorClasses.DependencyUnavailable, $"{what} is not on this machine's PATH (the Store alias does not count); nothing was run", retryable: false, new Dictionary<string, object?> { [DocumentErrors.DetailKey] = "runtime_missing" });

    // ================================================================== project.run

    public async Task<ProjectRun> StartAsync(ProjectContext project, ProjectCommand command, TimeSpan budget, CancellationToken cancellationToken)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        var port = project.Manifest.Port;
        var (executable, prefix) = ResolveRuntime(command.Runtime);
        var logPath = Path.Combine(project.Folder, ProjectRoots.StateFolderName, RunLogName);
        var run = new ProjectRun(project, command.Key, port, logPath, new BoundedLog(logPath, ProjectCapabilityNames.MaxLogBytes, TailChars));

        lock (_lock)
        {
            if (_runs.TryGetValue(project.ProjectId, out var existing) && existing.IsActive)
            {
                throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"'{project.Slug}' is already {existing.State} (pid {existing.Pid}, port {existing.Port}); stop it first", retryable: true, new Dictionary<string, object?> { [DocumentErrors.DetailKey] = "already_running" });
            }

            var active = _runs.Values.Count(r => r.IsActive);
            if (active >= MaxRunning)
            {
                throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"{active} projects are running, the most this companion runs at once ({MaxRunning}); stop one first", retryable: true, new Dictionary<string, object?> { [DocumentErrors.DetailKey] = "projects_busy" });
            }

            // The slot is held from here; a refusal below releases it.
            _runs[project.ProjectId] = run;
        }

        try
        {
            RequirePortFree(port);
            var startInfo = BuildStartInfo(executable, [.. prefix, .. command.Materialise(project.Folder)], project.Folder);
            run.Log.Open();
            StartContained(run, startInfo, command.Runtime);
            _logger.LogInformation("project.run {Slug} pid={Pid} port={Port} command={Key} log={Log}", project.Slug, run.Pid, port, command.Key, logPath);

            _ = WatchExitAsync(run);
            _ = ExpireAsync(run);
            await WaitForPortAsync(run, budget, cancellationToken).ConfigureAwait(false);
            return run;
        }
        catch (Exception)
        {
            Release(run);
            throw;
        }
    }

    private void StartContained(ProjectRun run, ProcessStartInfo startInfo, ProjectRuntime runtime)
    {
        var job = JobObject.CreateBounded(runtime);
        Process? process = null;
        try
        {
            process = _start(startInfo) ?? throw new CapabilityException(ErrorClasses.DependencyUnavailable, "the runtime could not be started", retryable: true);
            Interlocked.Increment(ref _processesStarted);
            job.Assign(process);
            if (!job.Contains(process))
            {
                throw new CapabilityException(ErrorClasses.DependencyUnavailable, "the process could not be contained in its job", retryable: false);
            }
        }
        catch (Exception ex)
        {
            // Our own child of a moment ago, not yet contained: ended here so it never runs
            // outside the bounds. Never a process this class did not just create.
            if (process is not null)
            {
                try
                {
                    process.Kill(entireProcessTree: true);
                }
                catch (Exception)
                {
                    // Already gone.
                }

                process.Dispose();
            }

            job.Dispose();
            _logger.LogWarning("project process could not be started under its job: {Reason}", ex.Message);
            throw ex is CapabilityException ? ex : new CapabilityException(ErrorClasses.DependencyUnavailable, $"the runtime could not be started under a job: {ex.Message}", retryable: true);
        }

        run.Process = process;
        run.Job = job;
        run.Pid = process.Id;
        process.StandardInput.Close();
        run.Log.Pump(process.StandardOutput);
        run.Log.Pump(process.StandardError);
    }

    /// <summary>
    /// M28 §9, the last gate before a process exists: this runner never starts a signing or
    /// certificate tool, and never passes one as an argument to something else. The manifest
    /// allowlist already refuses those programs by name at parse time; this repeats the refusal
    /// against the RESOLVED executable and the materialised argument list, so a detection that
    /// went wrong, or a shape widened later, still cannot end in a signer. The device produces
    /// UNSIGNED packages on purpose (the Cloud Core's <c>packaging.py</c> says why): signing
    /// needs a certificate, and the owner's signing identity is theirs.
    /// </summary>
    public static void RequireNoSigner(string executable, IReadOnlyList<string> arguments)
    {
        if (NativeCapabilityNames.IsForbiddenProgram(Path.GetFileName(executable)))
        {
            throw new CapabilityException(
                ErrorClasses.PermissionDenied,
                $"'{Path.GetFileName(executable)}' is a signing or certificate tool; this device signs nothing and never starts one",
                retryable: false,
                new Dictionary<string, object?> { [DocumentErrors.DetailKey] = "command_not_allowlisted" });
        }

        foreach (var argument in arguments)
        {
            if (NativeCapabilityNames.IsForbiddenProgram(Path.GetFileName(argument.TrimEnd('"', '\''))))
            {
                throw new CapabilityException(
                    ErrorClasses.PermissionDenied,
                    $"argument '{argument}' names a signing or certificate tool; this device signs nothing",
                    retryable: false,
                    new Dictionary<string, object?> { [DocumentErrors.DetailKey] = "command_not_allowlisted" });
            }
        }
    }

    private ProcessStartInfo BuildStartInfo(string executable, IReadOnlyList<string> arguments, string workingDirectory)
    {
        RequireNoSigner(executable, arguments);
        var startInfo = new ProcessStartInfo(executable)
        {
            WorkingDirectory = workingDirectory,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        foreach (var argument in arguments)
        {
            startInfo.ArgumentList.Add(argument);
        }

        var removed = Scrub(startInfo.Environment);
        startInfo.Environment["PATH"] = TerminalRunner.SystemPathPrefix() + (Environment.GetEnvironmentVariable("PATH") ?? string.Empty);
        startInfo.Environment["PYTHONIOENCODING"] = "utf-8";
        startInfo.Environment["PYTHONUNBUFFERED"] = "1";
        startInfo.Environment["PYTHONDONTWRITEBYTECODE"] = "1";
        startInfo.Environment["NO_COLOR"] = "1";
        _logger.LogInformation("project child environment: {Removed} secret-shaped variable(s) removed", removed.Count);
        return startInfo;
    }

    private static readonly TimeSpan PortReleaseWait = TimeSpan.FromSeconds(2);

    /// <summary>The manifest's port must be bindable on loopback. A run stopped a moment ago still holds its listener for a few milliseconds after its process is gone, so a bind that fails is retried briefly before it is a refusal.</summary>
    private static void RequirePortFree(int port)
    {
        var deadline = DateTime.UtcNow + PortReleaseWait;
        while (true)
        {
            try
            {
                var probe = new TcpListener(IPAddress.Loopback, port);
                probe.Start();
                probe.Stop();
                return;
            }
            catch (SocketException) when (DateTime.UtcNow < deadline)
            {
                Thread.Sleep(100);
            }
            catch (SocketException)
            {
                throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"port {port} on {ProjectManifest.Loopback} is not free; nothing was run", retryable: true, new Dictionary<string, object?> { [DocumentErrors.DetailKey] = "port_busy" });
            }
        }
    }

    private async Task WaitForPortAsync(ProjectRun run, TimeSpan budget, CancellationToken cancellationToken)
    {
        var wait = PortWait;
        if (budget > TimeSpan.Zero && budget - TimeSpan.FromSeconds(1) < wait)
        {
            wait = budget - TimeSpan.FromSeconds(1);
            if (wait < TimeSpan.FromSeconds(1))
            {
                wait = TimeSpan.FromSeconds(1);
            }
        }

        var deadline = DateTime.UtcNow + wait;
        while (true)
        {
            if (cancellationToken.IsCancellationRequested)
            {
                Stop(run, "cancelled");
                cancellationToken.ThrowIfCancellationRequested();
            }

            if (!run.IsActive || run.Process is null || run.Process.HasExited)
            {
                // Ended before the port answered: let the pumps drain its last words, mark it
                // exited HERE (the watcher may not have run yet, and the start-failure cleanup
                // must not turn an exit into a "stopped"), then say so with the tail.
                await run.Log.CompleteAsync().ConfigureAwait(false);
                var code = run.ExitCode ?? TryExitCode(run.Process);
                run.TryMarkExited(code ?? -1);
                throw new CapabilityException(
                    ErrorClasses.PostconditionFailed,
                    $"'{run.Project.Slug}' exited before port {run.Port} answered (exit code {code?.ToString(CultureInfo.InvariantCulture) ?? "?"}); log tail: {Trim(run.LogTail, 1200)}",
                    retryable: false,
                    new Dictionary<string, object?> { [DocumentErrors.DetailKey] = "exited_early", ["exit_code"] = code, ["log_tail"] = run.LogTail });
            }

            if (await ProbeAsync(run.Port, cancellationToken).ConfigureAwait(false))
            {
                if (run.TryMarkRunning())
                {
                    return;
                }

                // Marked exited or stopped meanwhile: report that on the next turn.
                continue;
            }

            if (DateTime.UtcNow >= deadline)
            {
                Stop(run, "port_never_answered");
                throw new CapabilityException(
                    ErrorClasses.PostconditionFailed,
                    $"'{run.Project.Slug}' did not answer on port {run.Port} within {wait.TotalSeconds:F0} s; the job was ended; log tail: {Trim(run.LogTail, 1200)}",
                    retryable: true,
                    new Dictionary<string, object?> { [DocumentErrors.DetailKey] = "port_never_answered", ["log_tail"] = run.LogTail });
            }

            await Task.Delay(ProbeInterval, CancellationToken.None).ConfigureAwait(false);
        }
    }

    private static async Task<bool> ProbeAsync(int port, CancellationToken cancellationToken)
    {
        try
        {
            using var client = new TcpClient();
            using var probeCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            probeCts.CancelAfter(ProbeTimeout);
            await client.ConnectAsync(IPAddress.Loopback, port, probeCts.Token).ConfigureAwait(false);
            return client.Connected;
        }
        catch (Exception)
        {
            return false;
        }
    }

    private async Task WatchExitAsync(ProjectRun run)
    {
        var process = run.Process!;
        try
        {
            await process.WaitForExitAsync(CancellationToken.None).ConfigureAwait(false);
        }
        catch (Exception)
        {
            // The handle is ours; a failure here means the process is gone.
        }

        var code = TryExitCode(process) ?? -1;
        if (run.TryMarkExited(code))
        {
            _logger.LogInformation("project '{Slug}' pid={Pid} exited by itself with code {Code}", run.Project.Slug, run.Pid, code);
        }

        // The entry process is gone: whatever it left in the job goes with it.
        run.Job?.Terminate();
        await run.Log.CompleteAsync().ConfigureAwait(false);
        run.Lifetime.Cancel();
    }

    private async Task ExpireAsync(ProjectRun run)
    {
        try
        {
            await Task.Delay(Lifetime, run.Lifetime.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            return;
        }

        if (run.IsActive)
        {
            _logger.LogInformation("project '{Slug}' pid={Pid} reached its {Minutes:F0} min lifetime; the job is ended", run.Project.Slug, run.Pid, Lifetime.TotalMinutes);
            Stop(run, "lifetime");
        }
    }

    // ============================================== project.run, the 3D runtimes (M25)

    /// <summary>
    /// M25 (M25_CREATIVE_3D_SPEC.md §3, ADR-0088 decision 3): a BATCH run — Blender headless
    /// or Unity in batch mode. Everything about the containment is M23's (the same job flags,
    /// the same scrubbed environment, the same bounded log, the same slot); what differs is
    /// the shape of the wait: these processes do their work and END, so there is no port to
    /// bind, nothing to probe, and <c>project.run</c> WAITS for the exit and answers with it.
    /// A run that outstays its runtime's bound has its job ended and answers <c>timeout</c>.
    ///
    /// The log tail is the child's stdout and stderr, plus — for Unity, which writes
    /// everything to its <c>-logFile</c> and almost nothing to stdout — the tail of that file,
    /// so the licensing client's own words reach the caller.
    /// </summary>
    public async Task<BatchOutcome> RunBatchAsync(ProjectContext project, ProjectCommand command, TimeSpan budget, CancellationToken cancellationToken)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        var (executable, prefix) = ResolveRuntime(command.Runtime);
        var arguments = (IReadOnlyList<string>)[.. prefix, .. command.Materialise(project.Folder)];
        var logPath = Path.Combine(project.Folder, ProjectRoots.StateFolderName, RunLogName);
        var run = new ProjectRun(project, command.Key, ProjectManifest.NoPort, logPath, new BoundedLog(logPath, ProjectCapabilityNames.MaxLogBytes, TailChars));

        lock (_lock)
        {
            if (_runs.TryGetValue(project.ProjectId, out var existing) && existing.IsActive)
            {
                throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"'{project.Slug}' is already {existing.State} (pid {existing.Pid}); stop it first", retryable: true, new Dictionary<string, object?> { [DocumentErrors.DetailKey] = "already_running" });
            }

            var active = _runs.Values.Count(r => r.IsActive);
            if (active >= MaxRunning)
            {
                throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"{active} projects are running, the most this companion runs at once ({MaxRunning}); stop one first", retryable: true, new Dictionary<string, object?> { [DocumentErrors.DetailKey] = "projects_busy" });
            }

            _runs[project.ProjectId] = run;
        }

        var stopwatch = Stopwatch.StartNew();
        try
        {
            var startInfo = BuildStartInfo(executable, arguments, project.Folder);
            run.Log.Open();
            StartContained(run, startInfo, command.Runtime);
            _logger.LogInformation("project.run (batch {Runtime}) {Slug} pid={Pid} command={Key} log={Log}", command.Runtime, project.Slug, run.Pid, command.Key, logPath);

            var wait = LimitFor(command.Runtime);
            if (budget > TimeSpan.Zero && budget < wait)
            {
                wait = budget;
            }

            var process = run.Process!;
            try
            {
                using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
                timeoutCts.CancelAfter(wait);
                await process.WaitForExitAsync(timeoutCts.Token).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                Stop(run, cancellationToken.IsCancellationRequested ? "cancelled" : "lifetime");
                await WaitForExitAsync(process, ProjectCapabilityNames.StopWait).ConfigureAwait(false);
                await run.Log.CompleteAsync().ConfigureAwait(false);
                if (cancellationToken.IsCancellationRequested)
                {
                    throw;
                }

                var tail = ComposeTail(run.LogTail, command, arguments, project.Folder);
                throw new CapabilityException(
                    ErrorClasses.Timeout,
                    $"'{project.Slug}' ran past {wait.TotalSeconds:F0} s under {command.Runtime}; the job was ended; log tail: {Trim(tail, 1200)}",
                    retryable: true,
                    new Dictionary<string, object?> { ["log_tail"] = tail, ["pid"] = run.Pid });
            }

            stopwatch.Stop();
            var exitCode = TryExitCode(process) ?? -1;
            run.TryMarkExited(exitCode);
            run.Lifetime.Cancel();

            // The entry process is gone; whatever it left in the job (Unity starts a licensing
            // client and a package manager) goes with it BEFORE the pumps are drained — a
            // grandchild that inherited the pipe would otherwise keep the tail open.
            run.Job?.Terminate();
            await run.Log.CompleteAsync().ConfigureAwait(false);

            var logTail = ComposeTail(run.LogTail, command, arguments, project.Folder);
            _logger.LogInformation("project.run (batch {Runtime}) {Slug} exit={Exit} seconds={Seconds}", command.Runtime, project.Slug, exitCode, stopwatch.Elapsed.TotalSeconds);
            RequireLicence(command.Runtime, project.Slug, exitCode, logTail);
            return new BatchOutcome(command.Runtime, exitCode, stopwatch.Elapsed.TotalSeconds, logTail, run.LogTruncated, logPath, run.Pid);
        }
        catch (Exception)
        {
            Release(run);
            throw;
        }
    }

    /// <summary>
    /// M25: a Unity that exited 198, or whose log carries the licensing client's own refusal,
    /// is <c>dependency_unavailable</c> — never <c>device_error</c> and never a plain
    /// <c>exit_code</c>. The editor is installed and it started; what is missing is an
    /// entitlement only the owner can obtain (a Unity Hub sign-in), and the Cloud Core must be
    /// able to say exactly that. The refusal carries the licensing client's line as
    /// <c>detail_line</c> so the receipt quotes the tool rather than paraphrasing it.
    /// </summary>
    private static void RequireLicence(ProjectRuntime runtime, string slug, int exitCode, string logTail)
    {
        if (runtime != ProjectRuntime.Unity)
        {
            return;
        }

        var marked = FindLine(logTail, SceneCapabilityNames.UnityNoLicenceMarker);
        if (exitCode != SceneCapabilityNames.UnityNoLicenceExitCode && marked is null)
        {
            return;
        }

        var line = marked ?? $"the Unity editor exited {SceneCapabilityNames.UnityNoLicenceExitCode}";
        throw new CapabilityException(
            ErrorClasses.DependencyUnavailable,
            $"the Unity editor is installed but has no valid licence for batch mode, so '{slug}' could not be built: {line}",
            retryable: false,
            new Dictionary<string, object?>
            {
                [DocumentErrors.DetailKey] = SceneCapabilityNames.UnityNoLicenceDetail,
                ["detail_line"] = line,
                ["exit_code"] = exitCode,
                ["log_tail"] = logTail,
            });
    }

    /// <summary>The last line of <paramref name="text"/> that contains <paramref name="marker"/> (case-insensitively), trimmed; null when there is none.</summary>
    public static string? FindLine(string text, string marker)
    {
        string? found = null;
        foreach (var line in text.Split('\n'))
        {
            if (line.Contains(marker, StringComparison.OrdinalIgnoreCase))
            {
                found = line.Trim('\r', ' ', '\t');
            }
        }

        return found;
    }

    /// <summary>
    /// The tail a batch run reports: what the child wrote to stdout/stderr, and — when the
    /// command named a <c>-logFile</c> — the tail of that file too. Unity writes everything
    /// there and next to nothing to stdout, so without this the licence refusal would be
    /// invisible to the caller.
    /// </summary>
    private static string ComposeTail(string streams, ProjectCommand command, IReadOnlyList<string> arguments, string workingDirectory)
    {
        if (command.Runtime != ProjectRuntime.Unity)
        {
            return streams;
        }

        var index = arguments.ToList().IndexOf("-logFile");
        if (index < 0 || index + 1 >= arguments.Count)
        {
            return streams;
        }

        // The -logFile argument is RELATIVE (the allowlist admits nothing else) and the
        // child's working directory is the project folder, so that is where it landed.
        var fileTail = ReadTail(Path.Combine(workingDirectory, arguments[index + 1]), TailChars);
        if (string.IsNullOrEmpty(fileTail))
        {
            return streams;
        }

        return string.IsNullOrWhiteSpace(streams) ? fileTail : streams + "\n" + fileTail;
    }

    /// <summary>The last <paramref name="chars"/> characters of a file, read through a share-everything handle (the editor may still hold it); empty when it cannot be read.</summary>
    private static string ReadTail(string path, int chars)
    {
        try
        {
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            var bytes = Math.Min(stream.Length, chars * 2L);
            stream.Seek(-bytes, SeekOrigin.End);
            using var reader = new StreamReader(stream, Encoding.UTF8);
            var text = reader.ReadToEnd();
            return text.Length <= chars ? text : text[^chars..];
        }
        catch (Exception)
        {
            // No log file, or one nobody may read: the streams are the tail.
            return string.Empty;
        }
    }

    // ================================================================== project.stop

    public async Task<StopOutcome> StopAsync(string projectId, CancellationToken cancellationToken)
    {
        var run = RunOf(projectId);
        if (run is null || !run.IsActive)
        {
            return new StopOutcome(WasRunning: false, run?.Pid, Exited: true);
        }

        Stop(run, "stop");
        var exited = await WaitForExitAsync(run, ProjectCapabilityNames.StopWait, cancellationToken).ConfigureAwait(false);
        _logger.LogInformation("project.stop '{Slug}' pid={Pid} exited={Exited}", run.Project.Slug, run.Pid, exited);
        return new StopOutcome(WasRunning: true, run.Pid, exited);
    }

    private void Stop(ProjectRun run, string reason)
    {
        if (!run.TryMarkStopped(reason))
        {
            return;
        }

        run.Lifetime.Cancel();
        try
        {
            // The job's members and nothing else. TerminateJobObject is immediate; the
            // kill-on-close at dispose is the backstop.
            run.Job?.Terminate();
        }
        catch (Exception ex)
        {
            _logger.LogWarning("project '{Slug}' job could not be terminated: {Reason}", run.Project.Slug, ex.Message);
        }
    }

    private static async Task<bool> WaitForExitAsync(ProjectRun run, TimeSpan wait, CancellationToken cancellationToken)
    {
        var process = run.Process;
        if (process is null)
        {
            return true;
        }

        try
        {
            using var waitCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            waitCts.CancelAfter(wait);
            await process.WaitForExitAsync(waitCts.Token).ConfigureAwait(false);
            return true;
        }
        catch (OperationCanceledException)
        {
            return process.HasExited;
        }
        catch (Exception)
        {
            return true;
        }
    }

    /// <summary>A start that did not become a run: the job (if any) ended, the log closed, and — when no process ever existed — the slot's entry forgotten so the project reads as scaffolded again.</summary>
    private void Release(ProjectRun run)
    {
        Stop(run, "start_failed");
        run.Log.Dispose();
        if (run.Process is null)
        {
            lock (_lock)
            {
                if (_runs.TryGetValue(run.Project.ProjectId, out var current) && ReferenceEquals(current, run))
                {
                    _runs.Remove(run.Project.ProjectId);
                }
            }
        }
    }

    // ================================================================== project.test

    public async Task<TestOutcome> TestAsync(ProjectContext project, ProjectCommand command, TimeSpan budget, CancellationToken cancellationToken)
    {
        ObjectDisposedException.ThrowIf(_disposed, this);
        var (executable, prefix) = ResolveRuntime(command.Runtime);
        if (Interlocked.Increment(ref _testsActive) > MaxRunning)
        {
            Interlocked.Decrement(ref _testsActive);
            throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"{MaxRunning} test runs are already in progress; try again when one has finished", retryable: true, new Dictionary<string, object?> { [DocumentErrors.DetailKey] = "tests_busy" });
        }

        var stopwatch = Stopwatch.StartNew();
        var logPath = Path.Combine(project.Folder, ProjectRoots.StateFolderName, TestLogName);
        using var log = new BoundedLog(logPath, ProjectCapabilityNames.MaxLogBytes, TailChars);
        using var job = JobObject.CreateBounded(command.Runtime);
        Process? process = null;
        try
        {
            var startInfo = BuildStartInfo(executable, [.. prefix, .. command.Materialise(project.Folder)], project.Folder);
            process = _start(startInfo) ?? throw new CapabilityException(ErrorClasses.DependencyUnavailable, "the runtime could not be started", retryable: true);
            Interlocked.Increment(ref _processesStarted);
            try
            {
                job.Assign(process);
                if (!job.Contains(process))
                {
                    throw new CapabilityException(ErrorClasses.DependencyUnavailable, "the test process could not be contained in its job", retryable: false);
                }
            }
            catch (Exception)
            {
                try
                {
                    process.Kill(entireProcessTree: true);
                }
                catch (Exception)
                {
                    // Already gone.
                }

                throw;
            }

            _logger.LogInformation("project.test {Slug} pid={Pid} command={Key} log={Log}", project.Slug, process.Id, command.Key, logPath);
            process.StandardInput.Close();
            log.Open();
            log.Pump(process.StandardOutput);
            log.Pump(process.StandardError);

            // M28: the runtime's bound, not a flat five minutes — a `dotnet test` restores and
            // compiles before it runs anything, and the bound that fits a node runner does not
            // fit that. Every other runtime still gets TestTimeout, which is what LimitFor
            // answers for them.
            var wait = LimitFor(command.Runtime);
            if (budget > TimeSpan.Zero && budget < wait)
            {
                wait = budget;
            }

            using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeoutCts.CancelAfter(wait);
            try
            {
                await process.WaitForExitAsync(timeoutCts.Token).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                job.Terminate();
                await WaitForExitAsync(process, ProjectCapabilityNames.StopWait).ConfigureAwait(false);
                if (cancellationToken.IsCancellationRequested)
                {
                    throw;
                }

                throw new CapabilityException(ErrorClasses.Timeout, $"'{project.Slug}' tests ran past {wait.TotalSeconds:F0} s; the job was ended; log tail: {Trim(log.Tail, 1200)}", retryable: true, new Dictionary<string, object?> { ["log_tail"] = log.Tail, ["pid"] = process.Id });
            }

            await log.CompleteAsync().ConfigureAwait(false);
            var exitCode = TryExitCode(process) ?? -1;
            var (passed, failed) = ParseCounts(log.Tail);
            stopwatch.Stop();
            _logger.LogInformation("project.test {Slug} exit={Exit} passed={Passed} failed={Failed} duration_ms={Ms}", project.Slug, exitCode, passed, failed, stopwatch.ElapsedMilliseconds);
            return new TestOutcome(exitCode, passed, failed, log.Tail, log.Truncated, stopwatch.ElapsedMilliseconds, logPath);
        }
        finally
        {
            Interlocked.Decrement(ref _testsActive);
            job.Terminate();
            process?.Dispose();
        }
    }

    private static async Task WaitForExitAsync(Process process, TimeSpan wait)
    {
        try
        {
            using var cts = new CancellationTokenSource(wait);
            await process.WaitForExitAsync(cts.Token).ConfigureAwait(false);
        }
        catch (Exception)
        {
            // Terminated by the job; the wait is only for the exit code to settle.
        }
    }

    /// <summary>The template runner's summary: <c>passed: N</c> / <c>failed: M</c> lines (the last of each), else <c>N passed</c> / <c>M failed|failing|failures</c>; null when nothing was printed.</summary>
    public static (int? Passed, int? Failed) ParseCounts(string text)
    {
        var passed = LastInt(text, @"passed:\s*(\d+)") ?? LastInt(text, @"(\d+)\s+pass(?:ed|ing)\b");
        var failed = LastInt(text, @"failed:\s*(\d+)") ?? LastInt(text, @"(\d+)\s+fail(?:ed|ing|ures?)\b");
        return (passed, failed);
    }

    private static int? LastInt(string text, string pattern)
    {
        var matches = Regex.Matches(text, pattern, RegexOptions.IgnoreCase | RegexOptions.CultureInvariant, TimeSpan.FromSeconds(1));
        if (matches.Count == 0)
        {
            return null;
        }

        return int.TryParse(matches[^1].Groups[1].Value, NumberStyles.None, CultureInfo.InvariantCulture, out var value) ? value : null;
    }

    private static int? TryExitCode(Process? process)
    {
        try
        {
            return process is not null && process.HasExited ? process.ExitCode : null;
        }
        catch (Exception)
        {
            return null;
        }
    }

    private static string Trim(string text, int max) => text.Length <= max ? text : text[^max..];

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        List<ProjectRun> runs;
        lock (_lock)
        {
            runs = [.. _runs.Values];
        }

        foreach (var run in runs)
        {
            Stop(run, "shutdown");
            try
            {
                run.Process?.WaitForExit(3000);
            }
            catch (Exception)
            {
                // Gone.
            }

            run.Job?.Dispose();
            run.Log.Dispose();
            run.Process?.Dispose();
        }
    }
}

/// <summary>
/// A run's log: stdout and stderr appended to one file up to a byte bound (then a single
/// marker line and nothing more), with the last <see cref="Tail"/> characters kept in memory
/// for <c>log_tail</c> / <c>report_tail</c>. The file is shared for reading while the run is
/// alive, so an owner (or a lab) can look.
/// </summary>
public sealed class BoundedLog : IDisposable
{
    private const string TruncationLine = "\n[pagentos: log bound reached; the rest is not kept]\n";

    private readonly object _lock = new();
    private readonly List<Task> _pumps = new();
    private readonly long _maxBytes;
    private readonly int _tailChars;
    private readonly StringBuilder _tail = new();
    private FileStream? _file;
    private long _written;
    private bool _truncated;

    private static readonly TimeSpan OpenRetry = TimeSpan.FromSeconds(3);

    public BoundedLog(string path, long maxBytes, int tailChars)
    {
        Path = path;
        _maxBytes = maxBytes;
        _tailChars = tailChars;
    }

    public string Path { get; }

    /// <summary>
    /// Creates (truncates) the file. The previous run's handle on the same path may still be
    /// closing — its exit watcher closes it a moment after the process ends — so a sharing
    /// violation is retried for a few seconds before it is an error.
    /// </summary>
    public void Open()
    {
        System.IO.Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path)!);
        var deadline = DateTime.UtcNow + OpenRetry;
        while (true)
        {
            try
            {
                var file = new FileStream(Path, FileMode.Create, FileAccess.Write, FileShare.Read);
                lock (_lock)
                {
                    _file = file;
                    _written = 0;
                    _truncated = false;
                }

                return;
            }
            catch (IOException) when (DateTime.UtcNow < deadline)
            {
                Thread.Sleep(50);
            }
        }
    }

    public bool Truncated
    {
        get
        {
            lock (_lock)
            {
                return _truncated;
            }
        }
    }

    public long BytesWritten
    {
        get
        {
            lock (_lock)
            {
                return _written;
            }
        }
    }

    public string Tail
    {
        get
        {
            lock (_lock)
            {
                return _tail.ToString();
            }
        }
    }

    internal void Pump(StreamReader reader)
    {
        lock (_lock)
        {
            _pumps.Add(Task.Run(async () =>
            {
                var buffer = new char[4096];
                while (true)
                {
                    int read;
                    try
                    {
                        read = await reader.ReadAsync(buffer, CancellationToken.None).ConfigureAwait(false);
                    }
                    catch (Exception)
                    {
                        break;
                    }

                    if (read <= 0)
                    {
                        break;
                    }

                    Append(buffer, read);
                }
            }));
        }
    }

    private void Append(char[] buffer, int count)
    {
        lock (_lock)
        {
            _tail.Append(buffer, 0, count);
            if (_tail.Length > _tailChars)
            {
                _tail.Remove(0, _tail.Length - _tailChars);
            }

            if (_file is null || _truncated)
            {
                return;
            }

            var bytes = Encoding.UTF8.GetBytes(buffer, 0, count);
            if (_written + bytes.Length > _maxBytes)
            {
                var room = (int)Math.Max(0, _maxBytes - _written);
                _file.Write(bytes, 0, room);
                var marker = Encoding.UTF8.GetBytes(TruncationLine);
                _file.Write(marker, 0, marker.Length);
                _written += room + marker.Length;
                _truncated = true;
            }
            else
            {
                _file.Write(bytes, 0, bytes.Length);
                _written += bytes.Length;
            }

            _file.Flush();
        }
    }

    /// <summary>Waits for the pumps to drain what the process wrote before it ended, then closes the file; the tail stays readable.</summary>
    internal async Task CompleteAsync()
    {
        Task[] pumps;
        lock (_lock)
        {
            pumps = [.. _pumps];
        }

        try
        {
            await Task.WhenAll(pumps).WaitAsync(TimeSpan.FromSeconds(3)).ConfigureAwait(false);
        }
        catch (Exception)
        {
            // A pump that is stuck on a handle a grandchild inherited: the tail is what it is.
        }

        Dispose();
    }

    /// <summary>Closes the file (the in-memory tail is kept). Idempotent.</summary>
    public void Dispose()
    {
        lock (_lock)
        {
            _file?.Dispose();
            _file = null;
        }
    }
}
