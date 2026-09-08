using System.Diagnostics;
using System.Text;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion.Operator;

/// <summary>
/// <c>terminal.execute</c> (M19_DIGITAL_OPERATOR_SPEC.md §2, ADR-0082 decision 5): the
/// terminal is an ALLOWLIST. A command runs only when it matches an allowlisted pattern token
/// for token; everything else — and anything carrying a composition character (<c>;</c>,
/// <c>|</c>, <c>&amp;</c>, <c>$</c>, brackets, redirection, backticks, newlines) — is
/// <c>permission_denied</c> before a process exists. What runs, runs headless
/// (<c>-NoProfile -NonInteractive -Command</c>) in the owner's session with a hard timeout,
/// never in the visible terminal window.
///
/// <para>Pattern grammar: whitespace-separated tokens; a literal token must match
/// case-insensitively; <c>*</c> matches exactly one token of anything (still free of
/// composition characters); <c>&lt;path&gt;</c> matches one token that is an absolute path
/// which RESOLVES (every junction and link followed) to a place under one of the owner's
/// authorised roots. Token counts must agree, so <c>hostname</c> does not admit
/// <c>hostname anything</c>. <c>&lt;project-entry&gt;</c> (M23, M23_APP_FACTORY_SPEC.md §4)
/// matches one token that is the absolute path of a scaffolded project's own entry file:
/// it must RESOLVE under the Projects root, inside a folder that carries the project marker,
/// and be the file that folder's manifest names as <c>entry</c> — so the ONE project-scoped
/// entry, <c>node &lt;project-entry&gt; --help</c>, runs an app the assistant made and
/// nothing else the owner keeps.</para>
/// </summary>
public sealed class TerminalRunner
{
    public const string PathToken = "<path>";
    public const string AnyToken = "*";
    public const string ProjectEntryToken = "<project-entry>";
    public const int DefaultTimeoutSeconds = 30;
    public const int MaxTimeoutSeconds = 30;
    public const int MaxCommandChars = 512;
    public const int MaxOutputChars = 32 * 1024;

    /// <summary>M23 (§4): the one project-scoped entry — a scaffolded project's own entry file, read-only, its help text.</summary>
    public const string ProjectHelpEntry = "node <project-entry> --help";

    /// <summary>The owner's default allowlist (§2): read-only, and nothing that reaches beyond the authorised roots — plus, since M23, the one project-scoped entry.</summary>
    public static readonly IReadOnlyList<string> DefaultAllowlist =
    [
        "hostname",
        "whoami",
        "ipconfig",
        "Get-Date",
        "Get-ComputerInfo -Property *",
        "Get-Process -Name *",
        "Get-ChildItem <path>",
        ProjectHelpEntry,
    ];

    private static readonly char[] CompositionCharacters = [';', '|', '&', '$', '(', ')', '{', '}', '<', '>', '`', '\r', '\n', '\0'];

    private readonly IReadOnlyList<string[]> _patterns;
    private readonly AuthorisedRoots _roots;
    private readonly ILogger _logger;
    private readonly string _powershell;
    private readonly Func<string, bool>? _isProjectEntry;
    private int _processesStarted;

    /// <param name="isProjectEntry">M23: decides whether a token is a scaffolded project's entry file (<see cref="Projects.ProjectRoots.IsEntry"/>); null means the <c>&lt;project-entry&gt;</c> token admits nothing.</param>
    public TerminalRunner(IReadOnlyList<string> allowlist, IReadOnlyList<string> authorisedRoots, ILogger logger, string? powershellPath = null, Func<string, bool>? isProjectEntry = null)
    {
        _patterns = [.. allowlist.Select(Tokenize).Where(t => t.Length > 0).Select(t => t.ToArray())];
        _roots = new AuthorisedRoots(authorisedRoots);
        _logger = logger;
        _powershell = powershellPath ?? DefaultPowerShellPath();
        _isProjectEntry = isProjectEntry;
    }

    /// <summary>Processes this runner has started, ever — a test asserts a refusal leaves this at zero.</summary>
    public int ProcessesStarted => Volatile.Read(ref _processesStarted);

    public IReadOnlyList<string> Allowlist => [.. _patterns.Select(p => string.Join(' ', p))];

    /// <summary>The roots as configured (normalised), for messages and the log; the check itself goes through <see cref="Roots"/>.</summary>
    public IReadOnlyList<string> AuthorisedRoots => _roots.Configured;

    /// <summary>The resolve-then-contain check every path in this module goes through: the terminal's <c>&lt;path&gt;</c> token, <c>file.*</c>, an application's path argument.</summary>
    public AuthorisedRoots Roots => _roots;

    /// <summary>Windows PowerShell 5.1 by absolute path — the spawned-shell PATH is not to be relied on.</summary>
    public static string DefaultPowerShellPath()
        => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32", "WindowsPowerShell", "v1.0", "powershell.exe");

    /// <summary>The system directories, first on the child's PATH: System32, the Windows directory, and the PowerShell directory.</summary>
    public static string SystemPathPrefix()
    {
        var windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        return string.Join(
            Path.PathSeparator,
            Path.Combine(windows, "System32"),
            windows,
            Path.Combine(windows, "System32", "Wbem"),
            Path.Combine(windows, "System32", "WindowsPowerShell", "v1.0")) + Path.PathSeparator;
    }

    /// <summary>The allowlist entry that admits <paramref name="command"/>, or null. Pure.</summary>
    public string? Authorise(string command)
    {
        if (string.IsNullOrWhiteSpace(command) || command.Length > MaxCommandChars || command.IndexOfAny(CompositionCharacters) >= 0)
        {
            return null;
        }

        var tokens = Tokenize(command);
        if (tokens.Length == 0)
        {
            return null;
        }

        foreach (var pattern in _patterns)
        {
            if (Matches(pattern, tokens))
            {
                return string.Join(' ', pattern);
            }
        }

        return null;
    }

    /// <summary>Refuse with <c>permission_denied</c> unless allowlisted. Called before any process exists.</summary>
    public string Check(string command)
        => Authorise(command) ?? throw new CapabilityException(
            ErrorClasses.PermissionDenied,
            $"terminal command is not in the owner's allowlist ({string.Join(" | ", Allowlist)}); nothing was run",
            retryable: false);

    public async Task<JsonObject> ExecuteAsync(string command, TimeSpan timeout, CancellationToken cancellationToken)
    {
        var matched = Check(command);
        if (timeout <= TimeSpan.Zero || timeout > TimeSpan.FromSeconds(MaxTimeoutSeconds))
        {
            timeout = TimeSpan.FromSeconds(MaxTimeoutSeconds);
        }

        var startInfo = new ProcessStartInfo(_powershell)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            RedirectStandardInput = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        startInfo.ArgumentList.Add("-NoProfile");
        startInfo.ArgumentList.Add("-NonInteractive");
        startInfo.ArgumentList.Add("-Command");
        // The wrapper is fixed text this class owns; the owner's command was authorised above
        // and cannot contain a separator, so it cannot escape the wrapper. The encoding line
        // matters: a headless powershell.exe writes redirected output in the OEM code page
        // (437 or 857 here), which garbles every Turkish character in a file name.
        startInfo.ArgumentList.Add("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + command);

        // The allowlisted names (hostname, whoami, ipconfig) must resolve to the SYSTEM
        // binaries, whatever the owner's PATH says: the system directories go first, so a
        // same-named executable earlier on a broken or tampered PATH is never the one that
        // runs. (On the owner's machine a spawned shell's PATH has been seen without System32
        // at all, which is how this was found.)
        startInfo.Environment["PATH"] = SystemPathPrefix() + (Environment.GetEnvironmentVariable("PATH") ?? string.Empty);

        var stopwatch = Stopwatch.StartNew();
        using var process = new Process { StartInfo = startInfo };
        if (!process.Start())
        {
            throw new CapabilityException(ErrorClasses.DependencyUnavailable, "powershell.exe could not be started", retryable: true);
        }

        Interlocked.Increment(ref _processesStarted);
        process.StandardInput.Close();
        var stdoutTask = ReadBoundedAsync(process.StandardOutput, cancellationToken);
        var stderrTask = ReadBoundedAsync(process.StandardError, cancellationToken);

        using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeoutCts.CancelAfter(timeout);
        try
        {
            await process.WaitForExitAsync(timeoutCts.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            Terminate(process);
            if (cancellationToken.IsCancellationRequested)
            {
                throw new CapabilityException(ErrorClasses.Cancelled, $"terminal command cancelled after {stopwatch.ElapsedMilliseconds} ms; the process was terminated", retryable: true);
            }

            throw new CapabilityException(ErrorClasses.Timeout, $"terminal command exceeded its {timeout.TotalSeconds:F0} s timeout; the process was terminated", retryable: true);
        }

        var (stdout, stdoutTruncated) = await stdoutTask.ConfigureAwait(false);
        var (stderr, stderrTruncated) = await stderrTask.ConfigureAwait(false);
        stopwatch.Stop();
        _logger.LogInformation("terminal.execute matched=\"{Pattern}\" exit={Exit} duration_ms={Duration}", matched, process.ExitCode, stopwatch.ElapsedMilliseconds);
        return new JsonObject
        {
            ["exit_code"] = process.ExitCode,
            ["stdout"] = stdout,
            ["stderr"] = stderr,
            ["duration_ms"] = (int)stopwatch.ElapsedMilliseconds,
            ["truncated"] = stdoutTruncated || stderrTruncated,
            ["matched"] = matched,
        };
    }

    /// <summary>Whitespace split honouring double and single quotes (quotes removed).</summary>
    public static string[] Tokenize(string command)
    {
        var tokens = new List<string>();
        var current = new StringBuilder();
        char? quote = null;
        foreach (var ch in command)
        {
            if (quote is not null)
            {
                if (ch == quote)
                {
                    quote = null;
                }
                else
                {
                    current.Append(ch);
                }
            }
            else if (ch is '"' or '\'')
            {
                quote = ch;
            }
            else if (char.IsWhiteSpace(ch))
            {
                if (current.Length > 0)
                {
                    tokens.Add(current.ToString());
                    current.Clear();
                }
            }
            else
            {
                current.Append(ch);
            }
        }

        if (current.Length > 0)
        {
            tokens.Add(current.ToString());
        }

        return [.. tokens];
    }

    /// <summary>
    /// Whether what <paramref name="path"/> really opens is inside one of the authorised roots
    /// (<see cref="AuthorisedRoots.Confine"/>: resolved through every junction and link first,
    /// then compared with the roots' resolved forms). A path that does not exist or cannot be
    /// resolved is NOT under a root — never a lexical fallback.
    /// </summary>
    public bool IsUnderAuthorisedRoot(string path) => _roots.Contains(path);

    private bool Matches(string[] pattern, string[] tokens)
    {
        if (pattern.Length != tokens.Length)
        {
            return false;
        }

        for (var i = 0; i < pattern.Length; i++)
        {
            var expected = pattern[i];
            var actual = tokens[i];
            if (expected == AnyToken)
            {
                continue;
            }

            if (expected.Equals(PathToken, StringComparison.OrdinalIgnoreCase))
            {
                if (!IsUnderAuthorisedRoot(actual))
                {
                    return false;
                }

                continue;
            }

            if (expected.Equals(ProjectEntryToken, StringComparison.OrdinalIgnoreCase))
            {
                // A project's entry is under the Projects root, which is an authorised root;
                // the project check is the stricter one and is asked on top, never instead.
                if (!IsUnderAuthorisedRoot(actual) || _isProjectEntry?.Invoke(actual) != true)
                {
                    return false;
                }

                continue;
            }

            if (!string.Equals(expected, actual, StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
        }

        return true;
    }

    private static async Task<(string Text, bool Truncated)> ReadBoundedAsync(StreamReader reader, CancellationToken cancellationToken)
    {
        var builder = new StringBuilder();
        var buffer = new char[4096];
        var truncated = false;
        while (true)
        {
            int read;
            try
            {
                read = await reader.ReadAsync(buffer, cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                break;
            }

            if (read <= 0)
            {
                break;
            }

            if (builder.Length < MaxOutputChars)
            {
                var room = Math.Min(read, MaxOutputChars - builder.Length);
                builder.Append(buffer, 0, room);
                if (room < read)
                {
                    truncated = true;
                }
            }
            else
            {
                truncated = true;
            }
        }

        return (builder.ToString(), truncated);
    }

    private static void Terminate(Process process)
    {
        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
            }
        }
        catch (Exception)
        {
            // Already gone.
        }
    }
}
