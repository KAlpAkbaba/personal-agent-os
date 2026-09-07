using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;

namespace PagentOS.SessionCompanion;

/// <summary>
/// The outcome of one staged worker update (<see cref="BrowserWorkerHost.SwapWorkerAsync"/>,
/// M18.4 gap 4). <c>Outcome</c> is one of <c>swapped</c>, <c>candidate_failed</c> (no usable
/// hello), <c>candidate_rejected</c> (the hello does not satisfy the current contract or the
/// expected release), <c>busy</c> (the current worker did not drain in time and keeps
/// serving), <c>refused</c> (nothing was attempted). Only <c>swapped</c> changed anything.
/// </summary>
public sealed record BrowserWorkerSwapResult(
    string Outcome,
    bool Swapped,
    int? OldPid,
    int? NewPid,
    string? OldVersion,
    string? NewVersion,
    int PendingAtDrainStart,
    long DrainMs,
    string? Reason)
{
    public const string OutcomeSwapped = "swapped";
    public const string OutcomeCandidateFailed = "candidate_failed";
    public const string OutcomeCandidateRejected = "candidate_rejected";
    public const string OutcomeBusy = "busy";
    public const string OutcomeRefused = "refused";

    public static BrowserWorkerSwapResult Refused(string reason)
        => new(OutcomeRefused, false, null, null, null, null, 0, 0, reason);

    public JsonObject ToJson() => new()
    {
        ["outcome"] = Outcome,
        ["swapped"] = Swapped,
        ["old_pid"] = OldPid,
        ["new_pid"] = NewPid,
        ["old_version"] = OldVersion,
        ["new_version"] = NewVersion,
        ["pending_at_drain_start"] = PendingAtDrainStart,
        ["drain_ms"] = DrainMs,
        ["reason"] = Reason,
        ["at"] = DateTimeOffset.UtcNow.ToString("O", CultureInfo.InvariantCulture),
    };
}

/// <summary>
/// One staged-update request for the browser worker, as the updater writes it to
/// <c>&lt;companion DataDir&gt;\browser-candidate.json</c>: the candidate's command (a python.exe
/// under the agent's install root - Program Files, admin-writable only), its leading
/// arguments, the release it is expected to announce, and how long the current worker may
/// take to drain. Anything else is refused before a process is started.
/// </summary>
public sealed record BrowserCandidateRequest(
    string WorkerCommand,
    string? WorkerArgs,
    string? ExpectedVersion,
    string? ExpectedPackageSha256,
    int DrainTimeoutS)
{
    public const int DefaultDrainTimeoutS = 30;
    public const int MaxDrainTimeoutS = 600;

    /// <summary>Throws <see cref="FormatException"/> when the document is not a usable request.</summary>
    public static BrowserCandidateRequest Parse(JsonObject document)
    {
        var command = document["worker_command"]?.GetValue<string>();
        if (string.IsNullOrWhiteSpace(command))
        {
            throw new FormatException("worker_command is required");
        }

        var drain = DefaultDrainTimeoutS;
        if (document["drain_timeout_s"] is JsonValue drainValue)
        {
            if (!drainValue.TryGetValue<int>(out drain) || drain < 0 || drain > MaxDrainTimeoutS)
            {
                throw new FormatException($"drain_timeout_s must be an integer between 0 and {MaxDrainTimeoutS}");
            }
        }

        return new BrowserCandidateRequest(
            command.Trim(),
            document["worker_args"]?.GetValue<string>(),
            Blank(document["expected_version"]?.GetValue<string>()),
            Blank(document["expected_package_sha256"]?.GetValue<string>()),
            drain);
    }

    /// <summary>
    /// True when <paramref name="path"/> resolves inside <paramref name="root"/> (case-insensitive,
    /// full paths, a separator-bounded prefix - "C:\agent2" is not under "C:\agent").
    /// </summary>
    public static bool IsUnder(string path, string root)
    {
        var fullPath = Path.GetFullPath(path);
        var fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
        return fullPath.StartsWith(fullRoot, StringComparison.OrdinalIgnoreCase);
    }

    private static string? Blank(string? value) => string.IsNullOrWhiteSpace(value) ? null : value.Trim();
}

/// <summary>
/// Turns a request file into one <see cref="BrowserWorkerHost.SwapWorkerAsync"/> call and
/// leaves the outcome beside it (<c>browser-candidate.result.json</c>); the request file is
/// removed once handled, so a request is acted on exactly once. The candidate command must
/// exist and live under <see cref="AllowedRoot"/> - the install root, which only an
/// administrator can write - so the owner-writable data directory can never point the
/// companion at an arbitrary executable. Polled by the companion (see Program.cs); the
/// method is public so tests drive it directly, without timers.
/// </summary>
public sealed class BrowserCandidateWatcher
{
    public const string RequestFileName = "browser-candidate.json";
    public const string ResultFileName = "browser-candidate.result.json";
    private const string AuditCandidate = "browser_worker_candidate";

    private readonly BrowserWorkerHost _host;
    private readonly ILogger _logger;
    private readonly AuditLog? _audit;

    public BrowserCandidateWatcher(BrowserWorkerHost host, string dataDir, string allowedRoot, ILogger logger, AuditLog? audit = null)
    {
        _host = host;
        DataDir = dataDir;
        AllowedRoot = allowedRoot;
        _logger = logger;
        _audit = audit;
    }

    public string DataDir { get; }

    public string AllowedRoot { get; }

    public string RequestPath => Path.Combine(DataDir, RequestFileName);

    public string ResultPath => Path.Combine(DataDir, ResultFileName);

    /// <summary>
    /// The install root a configured worker command implies: the venv layout is
    /// <c>&lt;root&gt;\browser\.venv\Scripts\python.exe</c>, so the root is three directories
    /// above the command's directory. A developer command elsewhere yields that tree's own
    /// ancestor, which is still the only place a candidate may come from.
    /// </summary>
    public static string DeriveAllowedRoot(string workerCommand)
    {
        var directory = Path.GetDirectoryName(Path.GetFullPath(workerCommand)) ?? Path.GetPathRoot(workerCommand) ?? workerCommand;
        return Path.GetFullPath(Path.Combine(directory, "..", "..", ".."));
    }

    /// <summary>Handle the request file if one is present. Null when there is nothing to do.</summary>
    public async Task<BrowserWorkerSwapResult?> PollOnceAsync(CancellationToken cancellationToken)
    {
        if (!File.Exists(RequestPath))
        {
            return null;
        }

        BrowserCandidateRequest request;
        try
        {
            var text = await File.ReadAllTextAsync(RequestPath, cancellationToken).ConfigureAwait(false);
            request = BrowserCandidateRequest.Parse(JsonNode.Parse(text) as JsonObject ?? throw new FormatException("not a JSON object"));
        }
        catch (Exception ex) when (ex is FormatException or JsonException or IOException)
        {
            return await Finish(BrowserWorkerSwapResult.Refused($"unreadable candidate request: {ex.Message}"), cancellationToken).ConfigureAwait(false);
        }

        if (!BrowserCandidateRequest.IsUnder(request.WorkerCommand, AllowedRoot))
        {
            return await Finish(BrowserWorkerSwapResult.Refused($"candidate command is outside the install root {AllowedRoot}: {request.WorkerCommand}"), cancellationToken).ConfigureAwait(false);
        }

        if (!File.Exists(request.WorkerCommand))
        {
            return await Finish(BrowserWorkerSwapResult.Refused($"candidate command does not exist: {request.WorkerCommand}"), cancellationToken).ConfigureAwait(false);
        }

        var candidate = _host.Options with { WorkerCommand = request.WorkerCommand, WorkerArgs = request.WorkerArgs };
        _logger.LogInformation(
            "browser worker candidate: {Command} {Args} (expected version {Version}, package {Package}, drain {Drain}s)",
            candidate.WorkerCommand,
            candidate.WorkerArgs ?? string.Empty,
            request.ExpectedVersion ?? "-",
            request.ExpectedPackageSha256 ?? "-",
            request.DrainTimeoutS);
        var result = await _host.SwapWorkerAsync(
            candidate,
            TimeSpan.FromSeconds(request.DrainTimeoutS),
            request.ExpectedVersion,
            request.ExpectedPackageSha256,
            cancellationToken).ConfigureAwait(false);
        return await Finish(result, cancellationToken).ConfigureAwait(false);
    }

    private async Task<BrowserWorkerSwapResult> Finish(BrowserWorkerSwapResult result, CancellationToken cancellationToken)
    {
        try
        {
            Directory.CreateDirectory(DataDir);
            var temporary = ResultPath + ".tmp";
            await File.WriteAllTextAsync(temporary, result.ToJson().ToJsonString(new JsonSerializerOptions { WriteIndented = true }), cancellationToken).ConfigureAwait(false);
            File.Move(temporary, ResultPath, overwrite: true);
        }
        catch (Exception ex)
        {
            _logger.LogWarning("browser worker candidate: could not write {Path}: {Reason}", ResultPath, ex.Message);
        }

        try
        {
            File.Delete(RequestPath);
        }
        catch (Exception ex)
        {
            _logger.LogWarning("browser worker candidate: could not remove {Path}: {Reason}", RequestPath, ex.Message);
        }

        _audit?.Write(AuditCandidate, status: result.Outcome, detail: $"old_pid={result.OldPid?.ToString(CultureInfo.InvariantCulture) ?? "-"}; new_pid={result.NewPid?.ToString(CultureInfo.InvariantCulture) ?? "-"}; old_version={result.OldVersion ?? "-"}; new_version={result.NewVersion ?? "-"}; drain_ms={result.DrainMs}; reason={result.Reason ?? "-"}");
        if (result.Swapped)
        {
            _logger.LogInformation("browser worker candidate swapped in: pid {Old} -> {New}, version {OldVersion} -> {NewVersion}, drained in {Drain} ms", result.OldPid, result.NewPid, result.OldVersion ?? "-", result.NewVersion, result.DrainMs);
        }
        else
        {
            _logger.LogWarning("browser worker candidate not swapped ({Outcome}): {Reason}", result.Outcome, result.Reason);
        }

        return result;
    }
}
