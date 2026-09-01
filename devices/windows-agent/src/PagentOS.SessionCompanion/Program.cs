using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Ipc;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion;

public static class Program
{
    /// <summary>
    /// Which pipes this companion is willing to take orders from.
    ///
    /// The default is the production posture — only SYSTEM or Administrators may own the
    /// pipe — because the alternative is not a smaller mistake: developer mode also trusts
    /// a pipe owned by the current user, and UAC splits integrity level rather than
    /// identity, so every ordinary process in the owner's session shares that SID. A
    /// companion that inherits developer mode by default will accept exec requests from any
    /// process the owner happens to be running, which is the exact attack the pipe-owner
    /// check exists to stop (found by the ADR-0028 security review: the shipped binary was
    /// constructing the runtime without a policy and silently getting developer mode).
    ///
    /// Developer runs opt in out loud, with <c>--dev-trust</c> or
    /// <c>PAGENTOS_AGENT_ServiceTrustMode=developer</c>, and the choice is logged either way.
    /// </summary>
    public static ServiceAdmissionPolicy BuildServicePolicy(string? mode, string? ownerSid)
    {
        var wantsDeveloper = string.Equals(mode?.Trim(), "developer", StringComparison.OrdinalIgnoreCase);
        if (!wantsDeveloper)
        {
            return ServiceAdmissionPolicy.ServiceMode();
        }

        if (string.IsNullOrWhiteSpace(ownerSid))
        {
            // Asked for developer mode but we cannot say whose pipes to trust: stay
            // production rather than widen the trust set to something unnamed.
            return ServiceAdmissionPolicy.ServiceMode();
        }

        return ServiceAdmissionPolicy.DeveloperMode(ownerSid);
    }

    public static async Task<int> Main(string[] args)
    {
        string? pipeArg = null;
        var devTrust = false;
        for (var i = 0; i < args.Length; i++)
        {
            if (args[i] == "--pipe" && i + 1 < args.Length)
            {
                pipeArg = args[i + 1];
            }
            else if (args[i] == "--dev-trust")
            {
                devTrust = true;
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

        var ownerSid = OperatingSystem.IsWindows()
            ? System.Security.Principal.WindowsIdentity.GetCurrent().User?.Value
            : null;
        var trustMode = devTrust ? "developer" : configuration["ServiceTrustMode"];
        var servicePolicy = BuildServicePolicy(trustMode, ownerSid);

        logger.LogInformation(
            "session companion starting; pipe={Pipe} allowlist=[{Allowlist}] artifact_roots=[{Roots}]",
            pipeName,
            string.Join(", ", allowlist.Keys),
            string.Join(", ", artifactOpener.Roots));

        // Always say which posture is in force. A trust decision nobody can see from the
        // running output is a trust decision nobody will notice is wrong.
        if (servicePolicy.RequiresElevatedOwner)
        {
            logger.LogInformation(
                "pipe trust: SERVICE mode - only a pipe owned by SYSTEM or Administrators is accepted");
        }
        else
        {
            logger.LogWarning(
                "pipe trust: DEVELOPER mode - a pipe owned by {Owner} is also accepted. " +
                "Any process running as that account can drive this companion. Never use this on an installed agent.",
                ownerSid);
        }

        var runtime = new CompanionRuntime(
            pipeName,
            new AppLauncher(allowlist),
            artifactOpener,
            logger,
            backoff: null,
            servicePolicy: servicePolicy);
        await runtime.RunAsync(cts.Token).ConfigureAwait(false);
        return 0;
    }
}
