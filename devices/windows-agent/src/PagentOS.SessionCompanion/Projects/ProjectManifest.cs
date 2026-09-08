using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using PagentOS.SessionCompanion.Documents;
using PagentOS.SessionCompanion.Operator;

namespace PagentOS.SessionCompanion.Projects;

/// <summary>The runtime a manifest command runs under — the only three there are.</summary>
public enum ProjectRuntime
{
    Python,
    Node,
    Npm,
}

/// <summary>
/// One allowlisted command from a manifest: the runtime and its arguments as a LIST — the
/// companion builds the child's argument list from these and never hands a command line to
/// a shell. <see cref="ProjectManifest.RootPlaceholder"/> in an argument is replaced by the
/// project root at run time (<see cref="Materialise"/>); relative paths are left relative
/// because the child's working directory is the project root.
/// </summary>
public sealed record ProjectCommand(string Key, ProjectRuntime Runtime, IReadOnlyList<string> Arguments, string Text)
{
    public IReadOnlyList<string> Materialise(string projectRoot)
        => [.. Arguments.Select(a => a == ProjectManifest.RootPlaceholder ? projectRoot : a)];
}

/// <summary>
/// The template manifest (M23_APP_FACTORY_SPEC.md §2/§3, ADR-0086 decisions 1 and 3):
/// <c>entry</c>, <c>port</c>, <c>run: {key: command}</c> and <c>test: {key: command}</c>.
/// Every command must match one of a FIXED allowlist of runtime forms, token for token:
/// <list type="bullet">
/// <item><c>python -m http.server &lt;port&gt; --bind 127.0.0.1</c> (run) — the port is the
/// manifest's, spelled as the number or as the placeholder <c>&lt;port&gt;</c>;</item>
/// <item><c>node &lt;entry&gt;</c> (run) — exactly the manifest's entry file;</item>
/// <item><c>node &lt;relative file&gt;</c> (test) — a file of the project, under it;</item>
/// <item><c>npm --prefix &lt;root&gt; run start</c> / <c>run test</c> — only when the template
/// shipped <c>package-lock.json</c>, so what npm runs was pinned by the generator, not by
/// whatever a registry answers.</item>
/// </list>
/// Anything else — another program, a shell, a composition character, an absolute path, a
/// <c>..</c> — is <c>permission_denied</c> at parse time, before any process exists. The
/// payload of <c>project.run</c> / <c>project.test</c> names a KEY into these maps; a
/// command line never crosses the pipe.
/// </summary>
public sealed class ProjectManifest
{
    public const int MaxCommands = 8;
    public const int MaxCommandChars = 256;
    public const int MinPort = 1024;
    public const int MaxPort = 65535;
    public const string PortPlaceholder = "<port>";
    public const string RootPlaceholder = "<root>";
    public const string LockfileName = "package-lock.json";
    public const string Loopback = "127.0.0.1";

    private static readonly Regex KeyPattern = new("^[a-z][a-z0-9_-]{0,31}$", RegexOptions.Compiled | RegexOptions.CultureInvariant);
    private static readonly char[] CompositionCharacters = [';', '|', '&', '$', '(', ')', '{', '}', '<', '>', '`', '\r', '\n', '\0', '"', '\''];

    private ProjectManifest(string entry, int port, IReadOnlyDictionary<string, ProjectCommand> run, IReadOnlyDictionary<string, ProjectCommand> test, bool hasLockfile, JsonObject json)
    {
        Entry = entry;
        Port = port;
        Run = run;
        Test = test;
        HasLockfile = hasLockfile;
        Json = json;
    }

    /// <summary>The entry file, relative to the project root with <c>/</c> separators.</summary>
    public string Entry { get; }

    /// <summary>The loopback port the app binds; checked free before a run.</summary>
    public int Port { get; }

    public IReadOnlyDictionary<string, ProjectCommand> Run { get; }

    public IReadOnlyDictionary<string, ProjectCommand> Test { get; }

    public bool HasLockfile { get; }

    /// <summary>The manifest as validated — what the marker records.</summary>
    public JsonObject Json { get; }

    /// <param name="manifest">The payload's <c>manifest</c> object.</param>
    /// <param name="filePaths">At scaffold time, the normalised relative paths of the file list — the entry and every test file must be among them and the npm forms need the lockfile there; null when re-read from a marker (the files are on disk and are checked there).</param>
    public static ProjectManifest Parse(JsonObject manifest, IReadOnlySet<string>? filePaths)
    {
        var entryRaw = manifest["entry"]?.GetValueKind() == JsonValueKind.String ? manifest["entry"]!.GetValue<string>() : null;
        if (string.IsNullOrWhiteSpace(entryRaw))
        {
            throw DocumentErrors.Invalid("manifest.entry is required and must be a relative file path");
        }

        var entry = ProjectScaffold.NormalisePath(entryRaw, "manifest.entry");
        if (filePaths is not null && !filePaths.Contains(entry))
        {
            throw DocumentErrors.Invalid("manifest.entry must name a file of the file list");
        }

        var port = ReadPort(manifest);
        var hasLockfile = filePaths?.Contains(LockfileName) ?? ManifestSaysLockfile(manifest);

        var run = ParseCommands(manifest, "run", required: true, entry, port, hasLockfile, filePaths, isTest: false);
        var test = ParseCommands(manifest, "test", required: false, entry, port, hasLockfile, filePaths, isTest: true);

        var json = new JsonObject
        {
            ["entry"] = entry,
            ["port"] = port,
            ["run"] = new JsonObject(run.Select(kv => new KeyValuePair<string, JsonNode?>(kv.Key, kv.Value.Text))),
            ["test"] = new JsonObject(test.Select(kv => new KeyValuePair<string, JsonNode?>(kv.Key, kv.Value.Text))),
        };
        if (hasLockfile)
        {
            json["lockfile"] = true;
        }

        return new ProjectManifest(entry, port, run, test, hasLockfile, json);
    }

    /// <summary>Whether a raw command TEXT is one the allowlist admits — for a test that wants the verdict without a manifest around it.</summary>
    public static bool IsAllowlisted(string command, string entry, int port, bool hasLockfile, bool isTest)
    {
        try
        {
            Authorise("probe", command, entry, port, hasLockfile, null, isTest);
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }

    private static int ReadPort(JsonObject manifest)
    {
        var node = manifest["port"];
        if (node is null || node is not JsonValue value)
        {
            throw DocumentErrors.Invalid($"manifest.port is required and must be an integer in [{MinPort}, {MaxPort}]");
        }

        double raw;
        if (value.TryGetValue<int>(out var asInt))
        {
            raw = asInt;
        }
        else if (value.TryGetValue<long>(out var asLong))
        {
            raw = asLong;
        }
        else if (!value.TryGetValue<double>(out raw))
        {
            throw DocumentErrors.Invalid("manifest.port must be an integer");
        }

        if (Math.Floor(raw) != raw || raw < MinPort || raw > MaxPort)
        {
            throw DocumentErrors.Invalid($"manifest.port={raw.ToString(CultureInfo.InvariantCulture)} is outside [{MinPort}, {MaxPort}]");
        }

        return (int)raw;
    }

    private static bool ManifestSaysLockfile(JsonObject manifest)
        => manifest["lockfile"]?.GetValueKind() == JsonValueKind.True;

    private static IReadOnlyDictionary<string, ProjectCommand> ParseCommands(JsonObject manifest, string section, bool required, string entry, int port, bool hasLockfile, IReadOnlySet<string>? filePaths, bool isTest)
    {
        var node = manifest[section];
        if (node is null)
        {
            if (required)
            {
                throw DocumentErrors.Invalid($"manifest.{section} is required and must be an object of {{key: command}}");
            }

            return new Dictionary<string, ProjectCommand>(StringComparer.Ordinal);
        }

        if (node is not JsonObject map || (required && map.Count == 0))
        {
            throw DocumentErrors.Invalid($"manifest.{section} must be a non-empty object of {{key: command}}");
        }

        if (map.Count > MaxCommands)
        {
            throw DocumentErrors.Invalid($"manifest.{section} may carry at most {MaxCommands} commands");
        }

        var commands = new Dictionary<string, ProjectCommand>(StringComparer.Ordinal);
        foreach (var (key, valueNode) in map)
        {
            if (!KeyPattern.IsMatch(key))
            {
                throw DocumentErrors.Invalid($"manifest.{section} key '{key}' must match [a-z][a-z0-9_-]{{0,31}}");
            }

            if (valueNode is null || valueNode.GetValueKind() != JsonValueKind.String)
            {
                throw DocumentErrors.Invalid($"manifest.{section}.{key} must be a command string");
            }

            var text = valueNode.GetValue<string>();
            commands[key] = Authorise(key, text, entry, port, hasLockfile, filePaths, isTest);
        }

        return commands;
    }

    /// <summary>The allowlist, token for token. <c>permission_denied</c> before anything else happens.</summary>
    private static ProjectCommand Authorise(string key, string text, string entry, int port, bool hasLockfile, IReadOnlySet<string>? filePaths, bool isTest)
    {
        if (string.IsNullOrWhiteSpace(text) || text.Length > MaxCommandChars || text.IndexOfAny(CompositionCharacters) >= 0)
        {
            throw Refuse(key, text, "it is empty, too long or carries a composition or quote character");
        }

        var tokens = text.Split(' ', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (tokens.Length == 0)
        {
            throw Refuse(key, text, "it is empty");
        }

        var program = tokens[0].ToLowerInvariant();
        var portText = port.ToString(CultureInfo.InvariantCulture);
        switch (program)
        {
            case "python" when !isTest
                && tokens.Length == 6
                && tokens[1] == "-m"
                && tokens[2] == "http.server"
                && (tokens[3] == PortPlaceholder || tokens[3] == portText)
                && tokens[4] == "--bind"
                && tokens[5] == Loopback:
                return new ProjectCommand(key, ProjectRuntime.Python, ["-m", "http.server", portText, "--bind", Loopback], $"python -m http.server {portText} --bind {Loopback}");

            case "python":
                throw Refuse(key, text, $"the only python form is 'python -m http.server {PortPlaceholder} --bind {Loopback}' with the manifest's port, as a run command");

            case "node" when tokens.Length == 2:
                {
                    var file = ProjectScaffold.NormalisePath(tokens[1], $"manifest command '{key}'");
                    if (!isTest && !string.Equals(file, entry, StringComparison.Ordinal))
                    {
                        throw Refuse(key, text, "a node run command runs exactly the manifest's entry file");
                    }

                    if (isTest && filePaths is not null && !filePaths.Contains(file))
                    {
                        throw Refuse(key, text, "a node test command runs a file of the file list");
                    }

                    return new ProjectCommand(key, ProjectRuntime.Node, [file], $"node {file}");
                }

            case "node":
                throw Refuse(key, text, "the only node form is 'node <one relative file>'");

            case "npm" when tokens.Length == 5
                && tokens[1] == "--prefix"
                && tokens[2] == RootPlaceholder
                && tokens[3] == "run"
                && tokens[4] == (isTest ? "test" : "start"):
                if (!hasLockfile)
                {
                    throw Refuse(key, text, $"an npm command needs the template to ship {LockfileName}");
                }

                return new ProjectCommand(key, ProjectRuntime.Npm, ["--prefix", RootPlaceholder, "run", tokens[4]], $"npm --prefix {RootPlaceholder} run {tokens[4]}");

            case "npm":
                throw Refuse(key, text, $"the only npm form is 'npm --prefix {RootPlaceholder} run {(isTest ? "test" : "start")}'");

            default:
                throw Refuse(key, text, "only python, node and npm run here");
        }
    }

    private static Exception Refuse(string key, string text, string reason)
        => DocumentErrors.Denied($"manifest command '{key}' is not in the runtime allowlist: {reason} (python -m http.server {PortPlaceholder} --bind {Loopback} | node <entry> | npm --prefix {RootPlaceholder} run start|test with a lockfile); nothing was run", "command_not_allowlisted");
}
