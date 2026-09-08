using System.Globalization;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Documents;
using PagentOS.SessionCompanion.Operator;

namespace PagentOS.SessionCompanion.Projects;

/// <summary>The runtime a manifest command runs under — the only five there are (M25 added the two 3D editors).</summary>
public enum ProjectRuntime
{
    Python,
    Node,
    Npm,

    /// <summary>M25: <c>blender.exe -b</c>, headless, running the shipped Python driver on a plan. A BATCH runtime: it ends by itself and the run waits for it.</summary>
    Blender,

    /// <summary>M25: <c>Unity.exe -batchmode -nographics -quit</c>, running the shipped editor driver. A BATCH runtime.</summary>
    Unity,
}

/// <summary>
/// Which root a project lives under, and therefore which half of the allowlist its manifest
/// may draw on (M25_CREATIVE_3D_SPEC.md §7): a <see cref="Web"/> project under the Projects
/// root may run python / node / npm, a <see cref="ThreeD"/> project under the 3D root may run
/// the two editors. The scope is decided by WHERE the project's folder was found, never by
/// anything in the payload or the manifest — so <c>blender</c> in a web project's manifest is
/// refused at parse time, before any process exists.
/// </summary>
public enum ProjectScope
{
    Web,
    ThreeD,
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
    /// <summary>
    /// M25: whether this command is a BATCH run — a process that does its work and ends,
    /// rather than a server the run waits for on a port. The two 3D editors are batch runs:
    /// <c>project.run</c> waits for the exit and answers with it. Nothing else is.
    /// </summary>
    public bool IsBatch => Runtime is ProjectRuntime.Blender or ProjectRuntime.Unity;

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
/// Since M25 (M25_CREATIVE_3D_SPEC.md §7, ADR-0088 decision 3) a project under the 3D root —
/// and ONLY there (<see cref="ProjectScope.ThreeD"/>) — may draw on two more shapes, matched
/// the same way, token for token, never by a string prefix:
/// <list type="bullet">
/// <item><c>blender -b &lt;scene.blend&gt; --python &lt;driver.py&gt; -- &lt;plan.json&gt;
/// &lt;out.json&gt;</c> — every path relative and inside the project;</item>
/// <item><c>unity -batchmode -nographics -quit -projectPath &lt;root&gt; -executeMethod
/// PagentOS.SceneDriver.Run -planPath &lt;plan.json&gt; -outPath &lt;out.json&gt; -logFile
/// &lt;log&gt;</c> — the method is the one shipped driver's entry point and nothing else.</item>
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
    // No shell ever sees a manifest command (the arguments go to CreateProcess as a list), so
    // this is belt and braces over the token-exact match below; `<` and `>` are left out
    // because the placeholders carry them and the exact match refuses them anywhere else.
    private static readonly char[] CompositionCharacters = [';', '|', '&', '$', '(', ')', '{', '}', '`', '\r', '\n', '\0', '"', '\''];

    /// <summary>M25: the port a 3D manifest carries when it names none — a batch run binds nothing, so nothing is checked and nothing is waited for.</summary>
    public const int NoPort = 0;

    private ProjectManifest(string entry, int port, IReadOnlyDictionary<string, ProjectCommand> run, IReadOnlyDictionary<string, ProjectCommand> test, bool hasLockfile, ProjectScope scope, JsonObject json)
    {
        Entry = entry;
        Port = port;
        Run = run;
        Test = test;
        HasLockfile = hasLockfile;
        Scope = scope;
        Json = json;
    }

    /// <summary>The entry file, relative to the project root with <c>/</c> separators.</summary>
    public string Entry { get; }

    /// <summary>The loopback port the app binds; checked free before a run.</summary>
    public int Port { get; }

    public IReadOnlyDictionary<string, ProjectCommand> Run { get; }

    public IReadOnlyDictionary<string, ProjectCommand> Test { get; }

    public bool HasLockfile { get; }

    /// <summary>M25: which root the project lives under, and therefore which half of the allowlist it was parsed against.</summary>
    public ProjectScope Scope { get; }

    /// <summary>The manifest as validated — what the marker records.</summary>
    public JsonObject Json { get; }

    /// <param name="manifest">The payload's <c>manifest</c> object.</param>
    /// <param name="filePaths">At scaffold time, the normalised relative paths of the file list — the entry and every test file must be among them and the npm forms need the lockfile there; null when re-read from a marker (the files are on disk and are checked there).</param>
    /// <param name="scope">M25: which root the project lives under. It comes from where the folder was FOUND, never from the payload.</param>
    public static ProjectManifest Parse(JsonObject manifest, IReadOnlySet<string>? filePaths, ProjectScope scope = ProjectScope.Web)
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

        var port = ReadPort(manifest, scope);
        var hasLockfile = filePaths?.Contains(LockfileName) ?? ManifestSaysLockfile(manifest);

        var run = ParseCommands(manifest, "run", required: true, entry, port, hasLockfile, filePaths, isTest: false, scope);
        var test = ParseCommands(manifest, "test", required: false, entry, port, hasLockfile, filePaths, isTest: true, scope);

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

        return new ProjectManifest(entry, port, run, test, hasLockfile, scope, json);
    }

    /// <summary>Whether a raw command TEXT is one the allowlist admits — for a test that wants the verdict without a manifest around it.</summary>
    public static bool IsAllowlisted(string command, string entry, int port, bool hasLockfile, bool isTest, ProjectScope scope = ProjectScope.Web)
    {
        try
        {
            Authorise("probe", command, entry, port, hasLockfile, null, isTest, scope);
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }

    private static int ReadPort(JsonObject manifest, ProjectScope scope)
    {
        var node = manifest["port"];
        if (scope == ProjectScope.ThreeD && (node is null || IsNoPort(node)))
        {
            // A batch run binds nothing. Requiring a port of a 3D manifest would be requiring
            // a number nothing ever checks, and every 3D project would have to invent one.
            // The marker records what THIS parse produced, so the re-parse at run time must
            // accept the absence back in the shape it wrote it (0 or the key gone).
            return NoPort;
        }

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

    /// <summary>M25: whether a <c>port</c> node is the "no port" a 3D manifest records (the number 0).</summary>
    private static bool IsNoPort(JsonNode node)
        => node is JsonValue value
            && ((value.TryGetValue<int>(out var asInt) && asInt == NoPort)
                || (value.TryGetValue<long>(out var asLong) && asLong == NoPort)
                || (value.TryGetValue<double>(out var asDouble) && asDouble == NoPort));

    private static bool ManifestSaysLockfile(JsonObject manifest)
        => manifest["lockfile"]?.GetValueKind() == JsonValueKind.True;

    private static IReadOnlyDictionary<string, ProjectCommand> ParseCommands(JsonObject manifest, string section, bool required, string entry, int port, bool hasLockfile, IReadOnlySet<string>? filePaths, bool isTest, ProjectScope scope)
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
            commands[key] = Authorise(key, text, entry, port, hasLockfile, filePaths, isTest, scope);
        }

        return commands;
    }

    /// <summary>The allowlist, token for token. <c>permission_denied</c> before anything else happens.</summary>
    private static ProjectCommand Authorise(string key, string text, string entry, int port, bool hasLockfile, IReadOnlySet<string>? filePaths, bool isTest, ProjectScope scope)
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
                    string file;
                    try
                    {
                        file = ProjectScaffold.NormalisePath(tokens[1], $"manifest command '{key}'");
                    }
                    catch (CapabilityException)
                    {
                        throw Refuse(key, text, "a node command runs one relative file inside the project (no '..', no drive, no separator first)");
                    }

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

            // ---------------------------------------------------------- M25: the 3D runtimes

            case SceneCapabilityNames.BlenderProgram when scope != ProjectScope.ThreeD:
                throw Refuse(key, text, $"the 3D runtimes run only under the 3D root ('{SceneCapabilityNames.Root3dFolderName}'), never in a project of the Projects root");

            case SceneCapabilityNames.UnityProgram when scope != ProjectScope.ThreeD:
                throw Refuse(key, text, $"the 3D runtimes run only under the 3D root ('{SceneCapabilityNames.Root3dFolderName}'), never in a project of the Projects root");

            // blender -b <scene.blend> --python <driver.py> -- <plan.json> <out.json>
            //    0     1       2           3         4      5      6           7
            case SceneCapabilityNames.BlenderProgram when tokens.Length == 8
                && tokens[1] == "-b"
                && tokens[3] == "--python"
                && tokens[5] == "--":
                {
                    var scene = InsideProject(key, text, tokens[2], SceneCapabilityNames.BlendExtension, "the scene file");
                    var driver = InsideProject(key, text, tokens[4], SceneCapabilityNames.DriverExtension, "the driver");
                    var plan = InsideProject(key, text, tokens[6], SceneCapabilityNames.JsonExtension, "the plan");
                    var inspection = InsideProject(key, text, tokens[7], SceneCapabilityNames.JsonExtension, "the inspection");
                    return new ProjectCommand(
                        key,
                        ProjectRuntime.Blender,
                        ["-b", scene, "--python", driver, "--", plan, inspection],
                        $"{SceneCapabilityNames.BlenderProgram} -b {scene} --python {driver} -- {plan} {inspection}");
                }

            case SceneCapabilityNames.BlenderProgram:
                throw Refuse(key, text, $"the only blender form is '{SceneCapabilityNames.BlenderProgram} -b <scene{SceneCapabilityNames.BlendExtension}> --python <driver{SceneCapabilityNames.DriverExtension}> -- <plan{SceneCapabilityNames.JsonExtension}> <out{SceneCapabilityNames.JsonExtension}>', every path relative and inside the project");

            case SceneCapabilityNames.UnityProgram when tokens.Length == 14
                && tokens[1] == "-batchmode"
                && tokens[2] == "-nographics"
                && tokens[3] == "-quit"
                && tokens[4] == "-projectPath"
                && tokens[5] == RootPlaceholder
                && tokens[6] == "-executeMethod"
                && tokens[7] == SceneCapabilityNames.UnityDriverMethod
                && tokens[8] == "-planPath"
                && tokens[10] == "-outPath"
                && tokens[12] == "-logFile":
                {
                    var plan = InsideProject(key, text, tokens[9], SceneCapabilityNames.JsonExtension, "the plan");
                    var inspection = InsideProject(key, text, tokens[11], SceneCapabilityNames.JsonExtension, "the inspection");
                    var log = InsideProject(key, text, tokens[13], SceneCapabilityNames.LogExtension, "the editor log");
                    return new ProjectCommand(
                        key,
                        ProjectRuntime.Unity,
                        ["-batchmode", "-nographics", "-quit", "-projectPath", RootPlaceholder, "-executeMethod", SceneCapabilityNames.UnityDriverMethod, "-planPath", plan, "-outPath", inspection, "-logFile", log],
                        $"{SceneCapabilityNames.UnityProgram} -batchmode -nographics -quit -projectPath {RootPlaceholder} -executeMethod {SceneCapabilityNames.UnityDriverMethod} -planPath {plan} -outPath {inspection} -logFile {log}");
                }

            case SceneCapabilityNames.UnityProgram:
                throw Refuse(key, text, $"the only unity form is '{SceneCapabilityNames.UnityProgram} -batchmode -nographics -quit -projectPath {RootPlaceholder} -executeMethod {SceneCapabilityNames.UnityDriverMethod} -planPath <plan{SceneCapabilityNames.JsonExtension}> -outPath <out{SceneCapabilityNames.JsonExtension}> -logFile <log{SceneCapabilityNames.LogExtension}>'");

            default:
                throw Refuse(key, text, "only python, node, npm and — under the 3D root — blender and unity run here");
        }
    }

    /// <summary>
    /// M25: one path token of a 3D command — RELATIVE, inside the project (the M23
    /// normalisation refuses <c>..</c>, a drive, a leading separator and a UNC form), and
    /// carrying the extension its position requires. The working directory of the child IS
    /// the project folder, so nothing has to be made absolute for the editor to find it, and
    /// nothing absolute is ever accepted — which is what keeps a <c>.blend</c> of the owner's
    /// own out of reach before any process exists.
    /// </summary>
    private static string InsideProject(string key, string text, string token, string extension, string what)
    {
        string path;
        try
        {
            path = ProjectScaffold.NormalisePath(token, $"manifest command '{key}'");
        }
        catch (CapabilityException)
        {
            throw Refuse(key, text, $"{what} must be a relative path inside the project (no '..', no drive, no separator first)");
        }

        if (!path.EndsWith(extension, StringComparison.OrdinalIgnoreCase))
        {
            throw Refuse(key, text, $"{what} must be a {extension} file");
        }

        return path;
    }

    private static Exception Refuse(string key, string text, string reason)
        => DocumentErrors.Denied($"manifest command '{key}' is not in the runtime allowlist: {reason} (python -m http.server {PortPlaceholder} --bind {Loopback} | node <entry> | npm --prefix {RootPlaceholder} run start|test with a lockfile | under the 3D root {SceneCapabilityNames.BlenderProgram} -b … --python … | {SceneCapabilityNames.UnityProgram} -batchmode …); nothing was run", "command_not_allowlisted");
}
