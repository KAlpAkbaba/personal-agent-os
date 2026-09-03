using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Ipc;
using PagentOS.Agent.Core.Logging;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion;

public static class Program
{
    /// <summary>Rotation bound for <c>companion.log</c>; with <see cref="CompanionLogKeepRotated"/> older files the disk cost is bounded at three times this.</summary>
    public const long CompanionLogMaxBytes = 8L * 1024 * 1024;

    public const int CompanionLogKeepRotated = 2;

    /// <summary><c>&lt;companion DataDir&gt;\logs\companion.log</c> — beside the audit, like the Device Service's <c>logs\device-service.log</c>.</summary>
    public static string CompanionLogPath(string dataDir) => Path.Combine(dataDir, "logs", "companion.log");

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
        var voiceFlag = false;
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
            else if (args[i] == "--voice")
            {
                voiceFlag = true;
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

        // Console AND a size-bounded file (the same lines, the Device Service's JSONL
        // format). The 2026-09-03 incident — dozens of Chrome windows on the owner's
        // desktop — could not be reconstructed because the companion, which owns the
        // Browser Worker and its Chrome, had no log anyone could read after the fact.
        var logFilePath = CompanionLogPath(dataDir);
        using var loggerFactory = LoggerFactory.Create(logging =>
        {
            logging.AddSimpleConsole(console =>
            {
                console.SingleLine = true;
                console.TimestampFormat = "HH:mm:ss ";
            });
            logging.AddProvider(new FileLoggerProvider(logFilePath, maxBytes: CompanionLogMaxBytes, keepRotated: CompanionLogKeepRotated));
        });
        var logger = loggerFactory.CreateLogger("SessionCompanion");
        logger.LogInformation(
            "session companion log file: {Path} (rotated at {MaxMiB} MiB, {Keep} older file(s) kept)",
            logFilePath,
            CompanionLogMaxBytes / (1024 * 1024),
            CompanionLogKeepRotated);

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

        // M12 (ADR-0039): voice_sideband frames the Device Service forwards over the pipe land
        // here and are read by the voice client. With voice off they are counted and dropped;
        // the pipe loop itself neither executes nor answers them.
        var sidebandSource = new PagentOS.Companion.Audio.Sideband.PipeSidebandPushSource(loggerFactory.CreateLogger("Sideband"));

        // M13 (BROWSER_CAPABILITIES.md §7): the Browser Worker is a child of THIS process,
        // in the owner's session, started only when configured. No command configured means
        // no browser family advertised and browser.* answered capability_missing.
        var browserOptions = BrowserWorkerOptions.FromConfiguration(configuration, dataDir);
        BrowserWorkerHost? browserHost = null;
        if (browserOptions.IsConfigured)
        {
            browserHost = new BrowserWorkerHost(browserOptions, loggerFactory.CreateLogger("BrowserWorker"), audit);
            logger.LogInformation(
                "browser worker: configured command={Command} args=[{Args}] channel={Channel} visible={Visible} data_dir={DataDir} eager={Eager}",
                browserOptions.WorkerCommand,
                string.Join(' ', browserOptions.BuildArgumentList()),
                browserOptions.Channel,
                browserOptions.Visible,
                browserOptions.DataDir,
                browserOptions.Eager);
        }
        else
        {
            logger.LogInformation("browser worker: not configured (PAGENTOS_AGENT_BrowserWorkerCommand is empty); browser.* capabilities are not advertised");
        }

        var runtime = new CompanionRuntime(
            pipeName,
            new AppLauncher(allowlist),
            artifactOpener,
            logger,
            backoff: null,
            servicePolicy: servicePolicy,
            sidebandSink: sidebandSource,
            browserWorker: browserHost);
        logger.LogInformation("capabilities advertised to the device service: {Capabilities}", string.Join(",", runtime.AdvertisedCapabilities));

        if (browserHost is not null && browserOptions.Eager)
        {
            await browserHost.StartAsync(cts.Token).ConfigureAwait(false);
        }

        // M12 track C: the realtime voice client is ADDITIVE and OFF by default. It runs beside
        // the qualified pipe loop, never inside it, and a voice failure can only log — the
        // service/companion path the owner qualified does not depend on it in any way.
        var voiceOptions = PagentOS.Companion.Audio.VoiceCompanionOptions.Parse(
            configuration["VoiceEnabled"],
            configuration["CloudCoreUrl"],
            configuration["VoiceCaptureDevice"],
            configuration["VoiceRenderDevice"],
            configuration["VoiceEndOfTurn"],
            configuration["DeviceId"],
            commandLineFlag: voiceFlag);
        var voiceTask = voiceOptions.Enabled
            ? RunVoiceAsync(voiceOptions, loggerFactory.CreateLogger("Voice"), audit, sidebandSource, cts.Token)
            : Task.CompletedTask;
        if (!voiceOptions.Enabled)
        {
            logger.LogInformation("voice: disabled (PAGENTOS_AGENT_VoiceEnabled=true plus PAGENTOS_AGENT_CloudCoreUrl, or --voice, enables it)");
        }

        try
        {
            await runtime.RunAsync(cts.Token).ConfigureAwait(false);
            await voiceTask.ConfigureAwait(false);
        }
        finally
        {
            if (browserHost is not null)
            {
                // "shutdown" on stdin, a bounded wait, then the process tree — Chrome included.
                await browserHost.StopAsync().ConfigureAwait(false);
            }
        }

        return 0;
    }

    private static async Task RunVoiceAsync(
        PagentOS.Companion.Audio.VoiceCompanionOptions options,
        ILogger logger,
        AuditLog audit,
        PagentOS.Companion.Audio.Sideband.ISidebandPushSource pushes,
        CancellationToken cancellationToken)
    {
        try
        {
            if (OperatingSystem.IsWindows())
            {
                await PagentOS.Companion.Audio.VoiceCompanionHost.RunAsync(options, logger, audit, pushes, cancellationToken).ConfigureAwait(false);
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "voice client stopped: {Reason}", ex.Message);
        }
    }
}
