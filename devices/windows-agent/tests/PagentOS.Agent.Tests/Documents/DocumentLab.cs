using System.Text.Json;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Tests.Support;
using PagentOS.SessionCompanion.Documents;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Documents;

/// <summary>
/// The documents lab classes share one collection so their fixture copies never race the
/// operator lab's Explorer windows for the same folder; the collection itself may run beside
/// anything else, since every lab instance works in its own run directory.
/// </summary>
[CollectionDefinition(Name)]
public sealed class DocumentLabCollection
{
    public const string Name = "documents-lab";
}

/// <summary>
/// The M20 device lab (M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §4, ADR-0083 decision 6): the
/// committed fixtures under <c>services/api/tests/fixtures/documents/</c> are COPIED into
/// <c>%TEMP%\pagentos-operator-fixture\documents\&lt;run-id&gt;\</c> — inside the default
/// authorised roots — and the real <see cref="DocumentCapabilities"/> is driven through its
/// real dispatcher against them. The oracle (<c>truth.json</c>, <c>expected/*.extract.json</c>)
/// is read from the repository, never copied: a search over the run directory must find the
/// fixtures and nothing else. The lab reads only what it copied; the owner's own files are
/// never touched. The run directory is removed on dispose.
/// </summary>
public sealed class DocumentLab : IDisposable
{
    public DocumentLab(bool enabled = true, IReadOnlyList<string>? roots = null, IReadOnlyList<IDocumentExtractor>? extractors = null)
    {
        RunId = Guid.NewGuid().ToString("N")[..12];
        Root = Path.Combine(OperatorOptions.FixtureRoot, "documents", RunId);
        CopyFixtures(FixtureSource, Root);
        Log = new ListLogger();
        Options = new OperatorOptions(enabled, TerminalRunner.DefaultAllowlist, roots ?? [Root]);
        Documents = new DocumentCapabilities(Options, Log, extractors: extractors);
    }

    public string RunId { get; }

    /// <summary>The run directory: the fixtures' copy, and the lab's only authorised root unless a test says otherwise.</summary>
    public string Root { get; }

    public DocumentCapabilities Documents { get; }

    public OperatorOptions Options { get; }

    public ListLogger Log { get; }

    /// <summary><c>services/api/tests/fixtures/documents</c> in the repository, found by walking up from the test output.</summary>
    public static string FixtureSource
    {
        get
        {
            var dir = new DirectoryInfo(AppContext.BaseDirectory);
            while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "services", "api", "tests", "fixtures", "documents", "truth.json")))
            {
                dir = dir.Parent;
            }

            return dir is null
                ? throw new DirectoryNotFoundException("could not locate services/api/tests/fixtures/documents/truth.json from the test output")
                : Path.Combine(dir.FullName, "services", "api", "tests", "fixtures", "documents");
        }
    }

    public static JsonObject Truth() => (JsonObject)JsonNode.Parse(File.ReadAllText(Path.Combine(FixtureSource, "truth.json")))!;

    /// <summary>Every fixture's relative path (forward slashes, as <c>truth.json</c> spells them).</summary>
    public static IReadOnlyList<string> FixturePaths()
        => [.. Truth()["files"]!.AsArray().Select(f => f!["path"]!.GetValue<string>())];

    /// <summary>The <c>truth.json</c> entry for a fixture.</summary>
    public static JsonObject TruthFor(string relative)
        => (JsonObject)Truth()["files"]!.AsArray().First(f => f!["path"]!.GetValue<string>() == relative)!;

    /// <summary><c>expected/&lt;path with / as __&gt;.extract.json</c>.</summary>
    public static JsonObject Expected(string relative)
        => (JsonObject)JsonNode.Parse(File.ReadAllText(Path.Combine(FixtureSource, "expected", relative.Replace("/", "__") + ".extract.json")))!;

    /// <summary>The copied fixture's absolute path.</summary>
    public string PathOf(string relative) => Path.Combine(Root, relative.Replace('/', Path.DirectorySeparatorChar));

    public JsonObject Exec(string capability, JsonObject payload, double budgetSeconds = 30)
        => Documents.ExecuteAsync(capability, payload, TimeSpan.FromSeconds(budgetSeconds), CancellationToken.None).GetAwaiter().GetResult();

    public CapabilityException ExpectFailure(string capability, JsonObject payload, double budgetSeconds = 30)
        => Assert.Throws<CapabilityException>(() => Exec(capability, payload, budgetSeconds));

    /// <summary>The spec's normalisation for <c>equals_normalised</c>: whitespace runs to one space, trimmed.</summary>
    public static string Normalise(string text) => TextFileReader.Normalise(text);

    private static void CopyFixtures(string source, string destination)
    {
        Directory.CreateDirectory(destination);
        foreach (var file in Directory.EnumerateFiles(source, "*", SearchOption.AllDirectories))
        {
            var relative = Path.GetRelativePath(source, file);
            // The oracle stays in the repository; the folder's own .gitattributes (which pins
            // the text fixtures to LF) is not a fixture either.
            if (relative.StartsWith("expected" + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
                || string.Equals(relative, "truth.json", StringComparison.OrdinalIgnoreCase)
                || string.Equals(relative, ".gitattributes", StringComparison.OrdinalIgnoreCase))
            {
                continue;
            }

            var target = Path.Combine(destination, relative);
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            File.Copy(file, target, overwrite: true);
        }
    }

    public void Dispose()
    {
        try
        {
            if (Directory.Exists(Root))
            {
                // A recursive delete removes a junction as an entry; it never descends into its target.
                Directory.Delete(Root, recursive: true);
            }
        }
        catch (Exception)
        {
            // Best-effort teardown.
        }
    }
}
