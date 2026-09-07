using System.Diagnostics;
using System.Management;
using Microsoft.Extensions.Logging;

namespace PagentOS.SessionCompanion;

/// <summary>
/// Terminates the Chrome that the Browser Worker launched on the PagentOS profile and then
/// left behind (the 2026-09-03 owner-machine incident): a killed worker takes its Python
/// process away but not the <c>chrome.exe</c> it started, the orphan keeps the profile
/// directory locked, and every later launch on that profile fails by opening yet another
/// window in the orphan — dozens of them, cascading across the owner's desktop.
///
/// Scope is the whole point. A process is reaped only when ALL of these hold:
/// <list type="bullet">
/// <item>its image name is <see cref="ProcessName"/> (<c>chrome.exe</c> in production);</item>
/// <item>its command line carries <c>--user-data-dir=&lt;ProfileDir&gt;</c> — the configured
/// PagentOS profile, compared as a whole path (so <c>…\profile2</c> is not <c>…\profile</c>),
/// case-insensitively, quoted or not;</item>
/// <item>it is a Chrome MAIN process (no <c>--type=</c> switch); the renderers and helpers
/// are its children and go with it.</item>
/// </list>
/// The owner's own Chrome runs on the owner's User Data, never carries the PagentOS
/// profile path, and is never touched. Nothing here throws: a reap that cannot enumerate
/// or cannot kill logs and returns what it did manage.
/// </summary>
public sealed class ChromeOrphanReaper
{
    public const string DefaultProcessName = "chrome.exe";
    public const string UserDataDirSwitch = "--user-data-dir=";
    private const string ProcessTypeSwitch = "--type=";

    private readonly ILogger _logger;
    private readonly TimeSpan _exitWait;

    public ChromeOrphanReaper(string profileDir, ILogger logger, string? processName = null, TimeSpan? exitWait = null)
    {
        ProfileDir = NormalizePath(profileDir);
        ProcessName = string.IsNullOrWhiteSpace(processName) ? DefaultProcessName : processName.Trim();
        _logger = logger;
        _exitWait = exitWait ?? TimeSpan.FromSeconds(5);
    }

    /// <summary>The configured PagentOS profile directory (full path, no trailing separator).</summary>
    public string ProfileDir { get; }

    /// <summary>Image name of the process to look for (<c>chrome.exe</c>; a test substitutes its own child).</summary>
    public string ProcessName { get; }

    /// <summary>
    /// True when <paramref name="commandLine"/> carries <c>--user-data-dir=</c> whose value
    /// is exactly <paramref name="profileDir"/> (case-insensitive, trailing separators and
    /// surrounding quotes ignored). A prefix match is not a match.
    /// </summary>
    public static bool CommandLineTargetsProfile(string? commandLine, string profileDir)
    {
        if (string.IsNullOrWhiteSpace(commandLine) || string.IsNullOrWhiteSpace(profileDir))
        {
            return false;
        }

        var wanted = NormalizePath(profileDir);
        var index = commandLine.IndexOf(UserDataDirSwitch, StringComparison.OrdinalIgnoreCase);
        while (index >= 0)
        {
            var valueStart = index + UserDataDirSwitch.Length;
            string value;
            if (valueStart < commandLine.Length && commandLine[valueStart] == '"')
            {
                // --user-data-dir="C:\path with spaces"
                var close = commandLine.IndexOf('"', valueStart + 1);
                value = close > valueStart
                    ? commandLine[(valueStart + 1)..close]
                    : commandLine[(valueStart + 1)..];
            }
            else if (index > 0 && commandLine[index - 1] == '"')
            {
                // "--user-data-dir=C:\path with spaces" — how .NET's ArgumentList quotes one argument.
                var close = commandLine.IndexOf('"', valueStart);
                value = close >= 0 ? commandLine[valueStart..close] : commandLine[valueStart..];
            }
            else
            {
                var end = commandLine.IndexOfAny([' ', '"'], valueStart);
                value = end >= 0 ? commandLine[valueStart..end] : commandLine[valueStart..];
            }

            if (string.Equals(NormalizePath(value), wanted, StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }

            index = commandLine.IndexOf(UserDataDirSwitch, valueStart, StringComparison.OrdinalIgnoreCase);
        }

        return false;
    }

    /// <summary>A Chrome main (browser) process has no <c>--type=</c>; renderers, GPU and utility children do.</summary>
    public static bool IsMainProcess(string? commandLine)
        => commandLine is null || !commandLine.Contains(ProcessTypeSwitch, StringComparison.OrdinalIgnoreCase);

    /// <summary>The reap rule in one place: right image name, main process, this profile.</summary>
    public bool IsOrphan(string? imageName, string? commandLine)
        => string.Equals(imageName, ProcessName, StringComparison.OrdinalIgnoreCase)
           && IsMainProcess(commandLine)
           && CommandLineTargetsProfile(commandLine, ProfileDir);

    /// <summary>
    /// Enumerate every running <see cref="ProcessName"/> with its command line (WMI
    /// <c>Win32_Process</c>; the same user's processes always answer). Empty, never
    /// throwing, when WMI is unavailable.
    /// </summary>
    public IReadOnlyList<(int Pid, string? CommandLine)> Enumerate()
    {
        var found = new List<(int, string?)>();
        if (!OperatingSystem.IsWindows())
        {
            return found;
        }

        try
        {
            var query = $"SELECT ProcessId, CommandLine FROM Win32_Process WHERE Name = '{ProcessName.Replace("'", "''", StringComparison.Ordinal)}'";
            using var searcher = new ManagementObjectSearcher(query);
            using var results = searcher.Get();
            foreach (var item in results)
            {
                using (item)
                {
                    var pid = Convert.ToInt32(item["ProcessId"], System.Globalization.CultureInfo.InvariantCulture);
                    found.Add((pid, item["CommandLine"] as string));
                }
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning("cannot enumerate {Process} processes for the orphan-chrome reap: {Reason}", ProcessName, ex.Message);
        }

        return found;
    }

    /// <summary>
    /// Kill (process tree) every orphan on the PagentOS profile and wait, bounded, for each
    /// to be gone. Returns the pids it terminated; logs each one with <paramref name="reason"/>.
    /// </summary>
    public IReadOnlyList<int> Reap(string reason)
    {
        var reaped = new List<int>();
        foreach (var (pid, commandLine) in Enumerate())
        {
            if (!IsOrphan(ProcessName, commandLine))
            {
                continue;
            }

            if (pid == Environment.ProcessId)
            {
                continue;
            }

            try
            {
                using var process = Process.GetProcessById(pid);
                if (process.HasExited)
                {
                    continue;
                }

                process.Kill(entireProcessTree: true);
                var gone = process.WaitForExit(_exitWait);
                reaped.Add(pid);
                _logger.LogWarning(
                    "reaped orphan {Process} pid={Pid} holding the PagentOS browser profile ({Reason}); exited={Exited}",
                    ProcessName,
                    pid,
                    reason,
                    gone);
            }
            catch (ArgumentException)
            {
                // Exited between the enumeration and the kill.
            }
            catch (Exception ex)
            {
                _logger.LogWarning("could not terminate orphan {Process} pid={Pid} ({Reason}): {Error}", ProcessName, pid, reason, ex.Message);
            }
        }

        return reaped;
    }

    public static string NormalizePath(string path)
    {
        var trimmed = path.Trim().Trim('"');
        try
        {
            trimmed = Path.GetFullPath(trimmed);
        }
        catch (Exception)
        {
            // Not a path the runtime accepts; compare the raw text.
        }

        return trimmed.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
    }
}
