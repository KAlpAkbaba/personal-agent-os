using System.Diagnostics;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.SessionCompanion.Native;
using PagentOS.SessionCompanion.Operator;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Native;

/// <summary>
/// The native lab classes share one collection: a real <c>dotnet build</c> is not small, and a
/// class's tests run one after another so two compilers are never resident at once. Every lab
/// instance works in its own run directory under
/// <c>%TEMP%\pagentos-operator-fixture\native\</c> and removes it on dispose.
/// </summary>
[CollectionDefinition(Name)]
public sealed class NativeLabCollection
{
    public const string Name = "native-lab";
}

/// <summary>
/// A test that needs a native build tool INSTALLED on this machine. On a host without it the
/// test is SKIPPED with the reason named — never passed vacuously, never failed for a reason
/// that has nothing to do with the code. The allowlist, root, bounds, signing and
/// advertisement tests need no toolchain at all and always run.
/// </summary>
public sealed class NativeLabFactAttribute : FactAttribute
{
    public NativeLabFactAttribute(params string[] tools)
    {
        var reason = NativeLab.SkipReason(tools);
        if (reason is not null)
        {
            Skip = reason;
        }
    }
}

/// <summary>
/// The M28 device lab (M28_NATIVE_APP_FACTORY_SPEC.md §5/§9, ADR-0095 decision 4): a Projects
/// root and its <c>native</c> root under
/// <c>%TEMP%\pagentos-operator-fixture\native\&lt;run-id&gt;\</c> — inside the lab's own
/// authorised root — and the real <see cref="ProjectCapabilities"/> over the real
/// <see cref="ProjectRunner"/> driven through its real dispatcher.
///
/// Nothing of the owner's is reachable from here: every path this lab hands the device is
/// relative and inside its own run directory, and the allowlist refuses anything else before a
/// process exists.
/// </summary>
public sealed class NativeLab : IDisposable
{
    private readonly SigningLab? _ownSigning;

    /// <summary>The project file every fixture native project carries.</summary>
    public const string ProjectFileName = "app/app.csproj";

    /// <summary>The tests project of the same fixture.</summary>
    public const string TestProjectFileName = "tests/tests.csproj";

    /// <summary>Where a publish lands, relative to the project folder.</summary>
    public const string PublishDirName = "out";

    /// <summary>Where a package is staged from, relative to the project folder.</summary>
    public const string PackageSourceDirName = "staging";

    /// <summary>The package a pack produces, relative to the project folder.</summary>
    public const string PackageFileName = "dist/app.msix";

    public NativeLab(
        bool enabled = true,
        TimeSpan? nativeLimit = null,
        int? maxRunning = null,
        Func<ProcessStartInfo, Process?>? start = null,
        OwnerSigningIdentity? signing = null,
        IMsixDeployer? deployer = null,
        Action<string, System.Security.Cryptography.X509Certificates.X509Certificate2>? signMsix = null)
    {
        RunId = Guid.NewGuid().ToString("N")[..12];
        Root = Path.Combine(OperatorOptions.FixtureRoot, "native", RunId);
        ProjectsRoot = Path.Combine(Root, "Projects");
        Directory.CreateDirectory(Root);
        Log = new ListLogger();

        // ProjectsRootNative is left unset on purpose: the lab exercises the DEFAULT derivation
        // (<Projects root>\native), which is what the owner's machine will use.
        Options = new OperatorOptions(enabled, TerminalRunner.DefaultAllowlist, [Root], Path.Combine(Root, "Downloads"), ProjectsRoot);
        Runner = new ProjectRunner(Log, start, maxRunning: maxRunning, nativeLimit: nativeLimit);
        // B33: a lab never signs with the owner's identity. Without an injected one it owns a
        // throwaway SigningLab (its own current-user store, removed on dispose).
        if (signing is null)
        {
            _ownSigning = new SigningLab();
            signing = _ownSigning.Identity;
        }

        Projects = new ProjectCapabilities(
            Options,
            Log,
            runner: Runner,
            signing: new NativeSigning(signing, deployer ?? new WindowsPackageDeployer(), signMsix));
    }

    public string RunId { get; }

    /// <summary>The run directory: the lab's only authorised root.</summary>
    public string Root { get; }

    /// <summary>The Projects root — where a web project would go.</summary>
    public string ProjectsRoot { get; }

    /// <summary>The native root: <c>&lt;Projects root&gt;\native</c>, derived exactly as the companion derives it.</summary>
    public string ProjectsRootNative => Projects.ProjectsRootNative!;

    public ProjectCapabilities Projects { get; }


    public ProjectRunner Runner { get; }

    public OperatorOptions Options { get; }

    public ListLogger Log { get; }

    // ------------------------------------------------------------------ the tools

    public static NativeTool? Dotnet => NativeTools.FindDotnet();

    public static NativeTool? MakeAppx => NativeTools.FindMakeAppx();

    /// <summary>Null when every named tool is installed; otherwise the reason to skip, naming where it was looked for.</summary>
    public static string? SkipReason(params string[] tools)
    {
        if (!OperatingSystem.IsWindows())
        {
            return "the native lab needs Windows (Job Objects, the Windows Kits and a win-x64 publish)";
        }

        foreach (var tool in tools)
        {
            var found = tool switch
            {
                NativeCapabilityNames.DotnetProgram => Dotnet,
                NativeCapabilityNames.MakeAppxProgram => MakeAppx,
                _ => throw new ArgumentException($"unknown tool '{tool}'", nameof(tools)),
            };
            if (found is null)
            {
                return $"{tool} is not installed on this machine (detected the way the Cloud Core detects it); the test needs the real toolchain";
            }
        }

        return null;
    }

    // ------------------------------------------------------------------ the commands

    /// <summary>The <c>dotnet build</c> / <c>dotnet test</c> command the allowlist admits, spelled the way a manifest spells it.</summary>
    public static string DotnetCommand(string verb, string project = ProjectFileName)
        => $"{NativeCapabilityNames.DotnetProgram} {verb} {project} -c {NativeCapabilityNames.Configuration}";

    /// <summary>The <c>dotnet publish</c> command the allowlist admits.</summary>
    public static string PublishCommand(string project = ProjectFileName, string output = PublishDirName)
        => $"{NativeCapabilityNames.DotnetProgram} publish {project} -c {NativeCapabilityNames.Configuration} -r {NativeCapabilityNames.RuntimeIdentifier} --self-contained true -o {output}";

    /// <summary>The <c>makeappx pack</c> command the allowlist admits.</summary>
    public static string PackCommand(string source = PackageSourceDirName, string package = PackageFileName)
        => $"{NativeCapabilityNames.MakeAppxProgram} pack /d {source} /p {package} /o /nv";

    // ------------------------------------------------------------------ the fixture project

    /// <summary>
    /// The smallest thing <c>dotnet build</c> and <c>dotnet publish -r win-x64
    /// --self-contained</c> will actually produce an EXE from: a console project targeting the
    /// SDK this machine has, and its own xunit-free test project (a plain console exit code, so
    /// the fixture needs no package restore beyond the SDK's own). Everything is TEXT, because
    /// <c>project.scaffold</c> writes text only.
    /// </summary>
    public static List<(string Path, string Text)> FixtureFiles(string targetFramework) =>
    [
        (ProjectFileName, ConsoleProject(targetFramework, "Exe")),
        ("app/Program.cs", "namespace App;\n\npublic static class Program\n{\n    public static int Main() { System.Console.WriteLine(\"notlarim\"); return 0; }\n}\n"),
        (TestProjectFileName, ConsoleProject(targetFramework, "Exe")),
        ("tests/Program.cs", "namespace Tests;\n\npublic static class Program\n{\n    public static int Main() { System.Console.WriteLine(\"passed: 1\"); return 0; }\n}\n"),
    ];

    private static string ConsoleProject(string targetFramework, string outputType) =>
        $"""
        <Project Sdk="Microsoft.NET.Sdk">
          <PropertyGroup>
            <OutputType>{outputType}</OutputType>
            <TargetFramework>{targetFramework}</TargetFramework>
            <Nullable>enable</Nullable>
            <ImplicitUsings>disable</ImplicitUsings>
            <InvariantGlobalization>true</InvariantGlobalization>
          </PropertyGroup>
        </Project>
        """;

    /// <summary>The framework the running test process targets — the one this machine's SDK certainly builds.</summary>
    public static string TargetFramework =>
        $"net{Environment.Version.Major}.{Environment.Version.Minor}";

    // ------------------------------------------------------------------ driving

    public JsonObject Exec(string capability, JsonObject payload, double budgetSeconds = 30)
        => Projects.ExecuteAsync(capability, payload, TimeSpan.FromSeconds(budgetSeconds), CancellationToken.None).GetAwaiter().GetResult();

    public CapabilityException ExpectFailure(string capability, JsonObject payload, double budgetSeconds = 30)
        => Assert.Throws<CapabilityException>(() => Exec(capability, payload, budgetSeconds));

    /// <summary>A <c>project.scaffold</c> payload for a native project: the run commands, an optional test command, and the files.</summary>
    public static JsonObject ScaffoldNativePayload(
        string projectId,
        string slug,
        IReadOnlyDictionary<string, string> run,
        IReadOnlyDictionary<string, string>? test = null,
        IEnumerable<(string Path, string Text)>? files = null,
        string entry = ProjectFileName,
        string root = NativeCapabilityNames.RootNativeFolderName)
    {
        var list = new JsonArray();
        foreach (var (path, text) in files ?? FixtureFiles(TargetFramework))
        {
            list.Add(new JsonObject { ["path"] = path, ["text"] = text });
        }

        var manifest = new JsonObject
        {
            ["entry"] = entry,
            ["run"] = new JsonObject(run.Select(kv => new KeyValuePair<string, JsonNode?>(kv.Key, kv.Value))),
        };
        if (test is not null)
        {
            manifest["test"] = new JsonObject(test.Select(kv => new KeyValuePair<string, JsonNode?>(kv.Key, kv.Value)));
        }

        return new JsonObject
        {
            ["project_id"] = projectId,
            ["slug"] = slug,
            ["root"] = root,
            ["files"] = list,
            ["manifest"] = manifest,
        };
    }

    /// <summary>Scaffolds a native project and returns its folder.</summary>
    public string ScaffoldNative(
        string projectId,
        string slug,
        IReadOnlyDictionary<string, string> run,
        IReadOnlyDictionary<string, string>? test = null,
        IEnumerable<(string Path, string Text)>? files = null,
        string entry = ProjectFileName)
    {
        var result = Exec(ProjectCapabilityNames.ProjectScaffold, ScaffoldNativePayload(projectId, slug, run, test, files, entry));
        Assert.Equal(NativeCapabilityNames.RootNativeFolderName, result["root"]!.GetValue<string>());
        return result["root_path"]!.GetValue<string>();
    }

    public string FolderNativeOf(string slug) => Path.Combine(ProjectsRootNative, slug);

    /// <summary>The marker's manifest, edited in place — the tamper that proves the allowlist is re-checked at RUN time, not only at scaffold time.</summary>
    public static void TamperRunCommand(string folder, string key, string command)
    {
        var marker = ProjectRoots.ReadMarker(folder)!;
        var manifest = (JsonObject)marker.Manifest.DeepClone();
        manifest["run"] = new JsonObject { [key] = command };
        ProjectRoots.WriteMarker(folder, marker with { Manifest = manifest });
    }

    public void Dispose()
    {
        // Every job this lab's runner holds is ended (its own children only), then the folder.
        // MSBuild's node processes give their handles back a moment after the job ends, so the
        // delete is retried for a few seconds.
        Projects.Dispose();
        _ownSigning?.Dispose();
        var deadline = DateTime.UtcNow.AddSeconds(15);
        while (true)
        {
            try
            {
                if (Directory.Exists(Root))
                {
                    Directory.Delete(Root, recursive: true);
                }

                return;
            }
            catch (Exception) when (DateTime.UtcNow < deadline)
            {
                Thread.Sleep(200);
            }
            catch (Exception)
            {
                // Best-effort teardown; the folder is under %TEMP%\pagentos-operator-fixture.
                return;
            }
        }
    }
}
