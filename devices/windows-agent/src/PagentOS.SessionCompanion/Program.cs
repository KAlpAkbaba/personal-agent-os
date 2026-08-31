using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Ipc;

namespace PagentOS.SessionCompanion;

public static class Program
{
    public static async Task<int> Main(string[] args)
    {
        string? pipeArg = null;
        for (var i = 0; i < args.Length - 1; i++)
        {
            if (args[i] == "--pipe")
            {
                pipeArg = args[i + 1];
            }
        }

        var configuration = new ConfigurationBuilder()
            .SetBasePath(AppContext.BaseDirectory)
            .AddJsonFile("appsettings.json", optional: true)
            .AddEnvironmentVariables("PAGENTOS_AGENT_")
            .Build();

        var pipeName = pipeArg;
        if (string.IsNullOrWhiteSpace(pipeName))
        {
            pipeName = configuration["PipeName"];
        }

        if (string.IsNullOrWhiteSpace(pipeName))
        {
            pipeName = PipeNaming.DefaultPipeName();
        }

        var allowlist = configuration.GetSection("AllowedApplications").Get<Dictionary<string, string>>();
        if (allowlist is null || allowlist.Count == 0)
        {
            allowlist = AppLauncher.DefaultAllowlist();
        }

        // Artifact roots: <data-dir>\artifacts plus any semicolon-separated PAGENTOS_AGENT_ArtifactRoots.
        var dataDir = configuration["DataDir"];
        if (string.IsNullOrWhiteSpace(dataDir))
        {
            dataDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PagentOS", "agent");
        }

        var artifactRoots = new List<string> { Path.Combine(dataDir, "artifacts") };
        var extraRoots = configuration["ArtifactRoots"];
        if (!string.IsNullOrWhiteSpace(extraRoots))
        {
            artifactRoots.AddRange(extraRoots.Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries));
        }

        IReadOnlySet<string>? allowedExtensions = null;
        var extensionOverride = configuration["ArtifactExtensions"];
        if (!string.IsNullOrWhiteSpace(extensionOverride))
        {
            allowedExtensions = extensionOverride
                .Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
                .Select(e => e.StartsWith('.') ? e : "." + e)
                .ToHashSet(StringComparer.OrdinalIgnoreCase);
        }

        Directory.CreateDirectory(artifactRoots[0]);
        var audit = new AuditLog(Path.Combine(dataDir, "audit", "companion-audit.jsonl"));
        var artifactOpener = new ArtifactOpener(artifactRoots, new ShellFileOpener(), allowedExtensions, audit);

        using var loggerFactory = LoggerFactory.Create(logging => logging.AddSimpleConsole(console =>
        {
            console.SingleLine = true;
            console.TimestampFormat = "HH:mm:ss ";
        }));
        var logger = loggerFactory.CreateLogger("SessionCompanion");

        using var cts = new CancellationTokenSource();
        Console.CancelKeyPress += (_, eventArgs) =>
        {
            eventArgs.Cancel = true;
            cts.Cancel();
        };

        logger.LogInformation(
            "session companion starting; pipe={Pipe} allowlist=[{Allowlist}] artifact_roots=[{Roots}]",
            pipeName,
            string.Join(", ", allowlist.Keys),
            string.Join(", ", artifactOpener.Roots));

        var runtime = new CompanionRuntime(pipeName, new AppLauncher(allowlist), artifactOpener, logger);
        await runtime.RunAsync(cts.Token).ConfigureAwait(false);
        return 0;
    }
}
