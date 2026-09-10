using System.Globalization;
using System.Text;
using Microsoft.Extensions.Configuration;

namespace PagentOS.SessionCompanion;

/// <summary>
/// How the companion starts the Browser Worker (BROWSER_CAPABILITIES.md §7). Configuration
/// only — appsettings.json plus <c>PAGENTOS_AGENT_*</c> environment overrides — with nothing
/// hardcoded to any machine: the installer writes the command it provisioned, and a
/// developer run points <c>BrowserWorkerCommand</c> at a venv python. No command means no
/// browser family: the companion then advertises desktop names only and answers
/// <c>browser.*</c> with <c>capability_missing</c>.
/// </summary>
public sealed record BrowserWorkerOptions
{
    /// <summary>Executable path (e.g. the provisioned venv's python.exe). Null/blank = not configured.</summary>
    public string? WorkerCommand { get; init; }

    /// <summary>Leading arguments as one string (e.g. <c>-m browser_agent.worker</c>); split like a command line.</summary>
    public string? WorkerArgs { get; init; }

    /// <summary>Where the worker may write: logs, downloads, the profile. Default <c>&lt;companion DataDir&gt;\browser</c>.</summary>
    public required string DataDir { get; init; }

    /// <summary>The dedicated PagentOS Chrome profile. Default <c>&lt;DataDir&gt;\profile</c>. Never the owner's User Data.</summary>
    public required string ProfileDir { get; init; }

    /// <summary><c>chrome</c> (installed Google Chrome, the qualification target) or <c>chromium</c> (CI).</summary>
    public string Channel { get; init; } = "chrome";

    /// <summary>Headful by default: the owner sees the real Chrome window.</summary>
    public bool Visible { get; init; } = true;

    /// <summary>Seconds a browser session may sit idle before the worker closes it.</summary>
    public int IdleTimeoutS { get; init; } = 600;

    /// <summary>Start the worker when the companion starts instead of on the first <c>browser.*</c> request.</summary>
    public bool Eager { get; init; }

    /// <summary>
    /// Where the owner's attach authorization is recorded (contract v1.4, ADR-0113): the
    /// <c>BrowserEnrollment</c> registry that lets <c>session_open</c> with profile
    /// <c>owner</c> connect to the owner's OWN running Chrome. Default
    /// <c>&lt;ProgramData&gt;/PagentOS/browser/owner-enrollment.json</c>, which is where
    /// <c>scripts/browser/enroll-owner-chrome.ps1</c> writes it.
    ///
    /// The path is always passed; the FILE is what grants anything, and until the owner
    /// creates it the worker refuses that profile outright. Passing a path is not a grant.
    /// </summary>
    public required string OwnerEnrollmentFile { get; init; }

    public bool IsConfigured => !string.IsNullOrWhiteSpace(WorkerCommand);

    public static string DefaultDataDir(string companionDataDir) => Path.Combine(companionDataDir, "browser");

    /// <summary>
    /// Machine-wide, not per-session: the owner enrolls their Chrome once and the
    /// companion finds the record wherever it runs from.
    /// </summary>
    public static string DefaultOwnerEnrollmentDir() => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
        "PagentOS",
        "browser");

    public static BrowserWorkerOptions FromConfiguration(IConfiguration configuration, string companionDataDir)
    {
        var dataDir = configuration["BrowserDataDir"];
        if (string.IsNullOrWhiteSpace(dataDir))
        {
            dataDir = DefaultDataDir(companionDataDir);
        }

        dataDir = Path.GetFullPath(Environment.ExpandEnvironmentVariables(dataDir));

        var profileDir = configuration["BrowserProfileDir"];
        profileDir = string.IsNullOrWhiteSpace(profileDir)
            ? Path.Combine(dataDir, "profile")
            : Path.GetFullPath(Environment.ExpandEnvironmentVariables(profileDir));

        var channel = configuration["BrowserChannel"];
        if (string.IsNullOrWhiteSpace(channel))
        {
            channel = "chrome";
        }

        var idle = 600;
        var idleRaw = configuration["BrowserIdleTimeoutS"];
        if (!string.IsNullOrWhiteSpace(idleRaw)
            && int.TryParse(idleRaw, NumberStyles.Integer, CultureInfo.InvariantCulture, out var parsedIdle)
            && parsedIdle > 0)
        {
            idle = parsedIdle;
        }

        var ownerEnrollment = configuration["BrowserOwnerEnrollmentFile"];
        var ownerEnrollmentFile = string.IsNullOrWhiteSpace(ownerEnrollment)
            ? Path.Combine(DefaultOwnerEnrollmentDir(), "owner-enrollment.json")
            : Path.GetFullPath(Environment.ExpandEnvironmentVariables(ownerEnrollment));

        var command = configuration["BrowserWorkerCommand"];
        return new BrowserWorkerOptions
        {
            WorkerCommand = string.IsNullOrWhiteSpace(command) ? null : Environment.ExpandEnvironmentVariables(command.Trim()),
            WorkerArgs = configuration["BrowserWorkerArgs"],
            DataDir = dataDir,
            ProfileDir = profileDir,
            Channel = channel.Trim(),
            Visible = ParseBool(configuration["BrowserVisible"], defaultValue: true),
            IdleTimeoutS = idle,
            Eager = ParseBool(configuration["BrowserWorkerEager"], defaultValue: false),
            OwnerEnrollmentFile = ownerEnrollmentFile,
        };
    }

    /// <summary>
    /// The worker's full argument list: the configured leading arguments, then the derived
    /// ones the contract's CLI defines (<c>--data-dir</c>, <c>--profile-dir</c>,
    /// <c>--channel</c>, <c>--visible|--headless</c>, <c>--idle-timeout-s</c>).
    /// </summary>
    public IReadOnlyList<string> BuildArgumentList()
    {
        var list = new List<string>(SplitArguments(WorkerArgs))
        {
            "--data-dir", DataDir,
            "--profile-dir", ProfileDir,
            "--owner-enrollment-file", OwnerEnrollmentFile,
            "--channel", Channel,
            Visible ? "--visible" : "--headless",
            "--idle-timeout-s", IdleTimeoutS.ToString(CultureInfo.InvariantCulture),
        };
        return list;
    }

    /// <summary>
    /// Split a configured argument string on whitespace, honouring double quotes so a path
    /// with spaces stays one argument. Quotes are delimiters, never part of the value.
    /// </summary>
    public static IReadOnlyList<string> SplitArguments(string? arguments)
    {
        var result = new List<string>();
        if (string.IsNullOrWhiteSpace(arguments))
        {
            return result;
        }

        var current = new StringBuilder();
        var inQuotes = false;
        var hasToken = false;
        foreach (var ch in arguments)
        {
            if (ch == '"')
            {
                inQuotes = !inQuotes;
                hasToken = true;
                continue;
            }

            if (char.IsWhiteSpace(ch) && !inQuotes)
            {
                if (hasToken)
                {
                    result.Add(current.ToString());
                    current.Clear();
                    hasToken = false;
                }

                continue;
            }

            current.Append(ch);
            hasToken = true;
        }

        if (hasToken)
        {
            result.Add(current.ToString());
        }

        return result;
    }

    public static bool ParseBool(string? raw, bool defaultValue)
    {
        var value = raw?.Trim();
        if (string.IsNullOrEmpty(value))
        {
            return defaultValue;
        }

        if (string.Equals(value, "true", StringComparison.OrdinalIgnoreCase)
            || string.Equals(value, "1", StringComparison.Ordinal)
            || string.Equals(value, "yes", StringComparison.OrdinalIgnoreCase))
        {
            return true;
        }

        if (string.Equals(value, "false", StringComparison.OrdinalIgnoreCase)
            || string.Equals(value, "0", StringComparison.Ordinal)
            || string.Equals(value, "no", StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }

        return defaultValue;
    }
}
