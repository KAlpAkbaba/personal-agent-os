using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Connection;
using PagentOS.Agent.Core.Enrollment;
using PagentOS.Agent.Core.Identity;
using PagentOS.Agent.Core.Idempotency;
using PagentOS.Agent.Core.Ipc;
using PagentOS.Agent.Core.Logging;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Core.Security;

namespace PagentOS.DeviceService;

public static class Program
{
    public static async Task<int> Main(string[] args)
    {
        var verb = args.Length > 0 ? args[0].ToLowerInvariant() : "run";
        try
        {
            return verb switch
            {
                "enroll" => await EnrollAsync(args.Skip(1).ToArray()).ConfigureAwait(false),
                "run" => await RunAsync(args.Skip(1).ToArray()).ConfigureAwait(false),
                "identity" => Identity(),
                "capabilities" => Capabilities(),
                _ => PrintUsage(),
            };
        }
        catch (Exception ex)
        {
            await Console.Error.WriteLineAsync($"fatal: {ex.Message}").ConfigureAwait(false);
            return 1;
        }
    }

    private static int PrintUsage()
    {
        Console.Error.WriteLine("usage:");
        Console.Error.WriteLine("  PagentOS.DeviceService enroll --broker-url <http-base> --token <one-time-token> --name <device-name>");
        Console.Error.WriteLine("  PagentOS.DeviceService run");
        Console.Error.WriteLine("  PagentOS.DeviceService identity");
        Console.Error.WriteLine("  PagentOS.DeviceService capabilities");
        return 2;
    }

    /// <summary>
    /// Print the capability manifest this install would advertise, as exactly one JSON
    /// document on stdout: <c>{"browser_enabled":bool,"capabilities":[…]}</c>. Reads only
    /// appsettings.json and the environment — no data directory, no key — so the verifier
    /// can run it unelevated and compare what the service says with what the worker says.
    /// </summary>
    private static int Capabilities()
    {
        var options = AgentServiceOptions.FromConfiguration(BuildConfiguration());
        var document = new System.Text.Json.Nodes.JsonObject
        {
            // M18.4 gap 3: the version this binary will announce in its hello, so a staged
            // candidate can be described BEFORE it runs and recognised on Cloud Core after.
            // 2026-09-08: the rest of the identity a candidate must expose, from ONE source
            // (AgentInfo) — which half of the agent this is, the assembly stamp that must
            // agree with the announced version, and a derived fingerprint of the capability
            // vocabulary so two installs that claim the same manifest really speak it.
            ["software_version"] = AgentInfo.SoftwareVersion,
            ["build_id"] = AgentInfo.BuildId,
            ["source_revision"] = AgentInfo.SourceRevision,
            ["component"] = AgentInfo.Component,
            ["assembly_version"] = AgentInfo.AssemblyVersion,
            ["capability_manifest_version"] = AgentInfo.CapabilityManifestVersion,
            ["display_power_enabled"] = options.DisplayPowerEnabled,
            ["browser_enabled"] = options.BrowserEnabled,
            ["operator_enabled"] = options.OperatorEnabled,
            ["capabilities"] = new System.Text.Json.Nodes.JsonArray(
                [.. options.AdvertisedCapabilities.Select(c => (System.Text.Json.Nodes.JsonNode)c)]),
        };
        Console.WriteLine(document.ToJsonString());
        return 0;
    }

    /// <summary>
    /// Print the device's NON-SECRET identity metadata as exactly one JSON document on
    /// stdout: device_id, name, broker URL, enrolled_at, and the PUBLIC (SPKI) half of the
    /// key. Diagnostics go to stderr, per the machine-readable child protocol.
    ///
    /// Load-only, and loudly so: an elevated recovery flow asking "what is this device's
    /// identity" must never cause a key or state file to be minted — a missing file here is
    /// a recovery question, not a first run. Distinct exit codes let the caller tell "not
    /// enrolled" from "enrolled but the key is gone", which need opposite responses.
    ///
    /// Exists because Windows PowerShell 5.1 (.NET Framework) has no ImportFromPem, and the
    /// alternative — reimplementing ECDSA PEM parsing in a script — is exactly the kind of
    /// crypto duplication ADR-0029 forbids. Key handling stays in this process, on the same
    /// implementation the agent itself signs with; the private key never crosses stdout.
    /// </summary>
    private static int Identity()
    {
        var options = AgentServiceOptions.FromConfiguration(BuildConfiguration());
        var state = AgentState.Load(options.StateFilePath);
        if (state is null)
        {
            Console.Error.WriteLine($"not enrolled: no state at {options.StateFilePath}. This verb never enrolls; nothing was created.");
            return 3;
        }

        if (!File.Exists(options.KeyFilePath))
        {
            Console.Error.WriteLine(
                $"enrolled state exists but the device key is missing at {options.KeyFilePath}. " +
                "This is a recovery situation, not a first run; no key was created.");
            return 4;
        }

        using var identity = DeviceIdentity.LoadOrCreate(options.KeyFilePath);
        var document = new System.Text.Json.Nodes.JsonObject
        {
            ["device_id"] = state.DeviceId,
            ["name"] = state.Name,
            ["broker_rest_url"] = state.BrokerRestUrl,
            ["enrolled_at"] = state.EnrolledAt.ToString("O", System.Globalization.CultureInfo.InvariantCulture),
            ["public_key_spki_b64"] = identity.PublicKeySpkiBase64,
        };
        Console.WriteLine(document.ToJsonString());
        return 0;
    }

    private static IConfigurationRoot BuildConfiguration()
        => new ConfigurationBuilder()
            .SetBasePath(AppContext.BaseDirectory)
            .AddJsonFile("appsettings.json", optional: true)
            .AddEnvironmentVariables("PAGENTOS_AGENT_")
            .Build();

    private static async Task<int> EnrollAsync(string[] args)
    {
        string? brokerUrl = null;
        string? token = null;
        string? name = null;
        for (var i = 0; i < args.Length - 1; i++)
        {
            switch (args[i])
            {
                case "--broker-url":
                    brokerUrl = args[++i];
                    break;
                case "--token":
                    token = args[++i];
                    break;
                case "--name":
                    name = args[++i];
                    break;
                default:
                    break;
            }
        }

        var options = AgentServiceOptions.FromConfiguration(BuildConfiguration());
        brokerUrl ??= options.BrokerRestUrl;
        if (string.IsNullOrWhiteSpace(token) || string.IsNullOrWhiteSpace(name))
        {
            return PrintUsage();
        }

        // Enrollment normally runs elevated as the OWNER and creates material the SERVICE
        // must later read. Declaring the posture here is what makes that handover correct.
        MachineMaterial.UseDeveloperPosture(options.DeveloperMaterialPosture);
        var dataDirExisted = Directory.Exists(options.DataDir);
        Directory.CreateDirectory(options.DataDir);
        if (!dataDirExisted)
        {
            MachineMaterial.Protect(options.DataDir, MachineMaterialKind.Directory);
        }

        using var identity = DeviceIdentity.LoadOrCreate(options.KeyFilePath, options.DeveloperMaterialPosture);
        using var httpClient = new HttpClient { Timeout = TimeSpan.FromSeconds(30) };
        var client = new EnrollmentClient(httpClient);
        var deviceId = await client.EnrollAsync(
            new Uri(brokerUrl),
            token,
            name,
            identity.PublicKeySpkiBase64,
            options.AdvertisedCapabilities).ConfigureAwait(false);

        var state = new AgentState
        {
            DeviceId = deviceId,
            Name = name,
            BrokerRestUrl = brokerUrl,
            EnrolledAt = DateTimeOffset.UtcNow,
        };
        state.Save(options.StateFilePath);
        new AuditLog(options.AuditLogPath).Write("enrolled", deviceId: deviceId, detail: $"name={name}");
        Console.WriteLine($"enrolled device_id={deviceId}");
        Console.WriteLine($"state: {options.StateFilePath}");
        return 0;
    }

    /// <summary>
    /// Who is allowed to be the companion. Configured explicitly in a service install, where
    /// the service account and the owner account differ; falls back to this process's own SID
    /// for a developer run, which is the same trust as before and no looser. The fallback is
    /// logged at startup so a service install that forgot to set CompanionSid is visible
    /// rather than silently self-authorizing.
    /// </summary>
    public static CompanionAdmissionPolicy BuildAdmissionPolicy(AgentServiceOptions options)
    {
        var sid = options.CompanionSid;
        if (string.IsNullOrWhiteSpace(sid))
        {
            sid = System.Security.Principal.WindowsIdentity.GetCurrent().User?.Value
                  ?? throw new InvalidOperationException(
                      "CompanionSid is not configured and this process's own SID could not be resolved");
            Console.Error.WriteLine(
                "warning: CompanionSid is not configured; falling back to this process's SID. " +
                "A Windows Service install must set PAGENTOS_AGENT_CompanionSid to the owner's SID.");
        }

        if (string.IsNullOrWhiteSpace(options.CompanionImagePath))
        {
            // The same loud treatment as a missing CompanionSid, for the same reason: this
            // is the check that separates "the companion" from "anything the owner runs".
            // An install that sets the SID but forgets the binary admits any process in the
            // owner's session, and would look correct in the log without this line.
            Console.Error.WriteLine(
                "warning: CompanionImagePath is not configured; ANY process running as the " +
                "authorized SID in an interactive session will be admitted as the companion. " +
                "A Windows Service install must set PAGENTOS_AGENT_CompanionImagePath to the " +
                "installed companion executable.");
        }

        return new CompanionAdmissionPolicy(sid, options.CompanionImagePath, options.CompanionSessionId);
    }

    /// <summary>
    /// Check the machine material this process must read BEFORE anything opens it, and say
    /// precisely what is wrong if it cannot.
    ///
    /// The service previously died in <c>DeviceIdentity.LoadOrCreate</c> with a bare
    /// UnauthorizedAccessException on device.key, which the SCM reported as 1067 â€” a crash
    /// code that says nothing about ACLs. The key had been created by an owner-context
    /// enrollment run and protected to that user alone, so LocalSystem could not read it.
    /// Writing the diagnosis to the agent's own log (which SYSTEM can write, since it lives
    /// in the machine data directory) turns a silent restart loop into one readable line.
    ///
    /// Never logs key material: the report contains principals, rights and paths only.
    /// </summary>
    private static bool VerifyMachineMaterial(AgentServiceOptions options, ILogger logger)
    {
        var checks = new (string Path, MachineMaterialKind Kind)[]
        {
            (options.DataDir, MachineMaterialKind.Directory),
            (options.KeyFilePath, MachineMaterialKind.Secret),
            (options.StateFilePath, MachineMaterialKind.State),
        };

        var usable = true;
        foreach (var (path, kind) in checks)
        {
            var report = MachineMaterial.Inspect(path, kind);
            if (!report.Exists)
            {
                // Absent is not necessarily wrong here: an unenrolled agent has no state yet.
                continue;
            }

            // Two different questions, and conflating them was a mistake worth not repeating:
            // "can this account USE the material" decides whether to start, and "is the
            // descriptor exactly as intended" is a hardening report. A directory that still
            // inherits its ACL is worth saying out loud; it is not a reason to refuse to run.
            var accessError = TryAccess(path, kind);
            if (accessError is not null)
            {
                usable = false;
                logger.LogError(
                    "cannot use machine material at {Path}: {Error}. {Report}. " +
                    "Repair it with scripts\\repair-device-material.ps1 (elevated). " +
                    "Enrollment state and the device key are preserved by that repair.",
                    path, accessError, report.Describe());
                continue;
            }

            if (!report.Correct)
            {
                logger.LogWarning("machine material is usable but not fully hardened: {Report}", report.Describe());
                continue;
            }

            logger.LogDebug("machine material ok: {Path}", path);
        }

        return usable;
    }

    /// <summary>
    /// Can this account actually use the material? Returns null when yes, or a short reason.
    /// Opens nothing it does not need and never reads content — the key is opened for read
    /// and closed immediately, which is precisely what failed under LocalSystem.
    /// </summary>
    private static string? TryAccess(string path, MachineMaterialKind kind)
    {
        try
        {
            switch (kind)
            {
                case MachineMaterialKind.Directory:
                    var probe = Path.Combine(path, $".access-probe-{Guid.NewGuid():N}");
                    File.WriteAllText(probe, string.Empty);
                    File.Delete(probe);
                    return null;

                case MachineMaterialKind.Secret:
                case MachineMaterialKind.State:
                default:
                    using (File.Open(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                    {
                        return null;
                    }
            }
        }
        catch (UnauthorizedAccessException ex)
        {
            return ex.Message;
        }
        catch (IOException ex)
        {
            return ex.Message;
        }
    }

    private static async Task<int> RunAsync(string[] args)
    {
        var settings = new HostApplicationBuilderSettings
        {
            Args = args,
            ContentRootPath = AppContext.BaseDirectory,
        };
        var builder = Host.CreateApplicationBuilder(settings);
        builder.Configuration.AddEnvironmentVariables("PAGENTOS_AGENT_");
        var options = AgentServiceOptions.FromConfiguration(builder.Configuration);

        var state = AgentState.Load(options.StateFilePath);
        if (state is null)
        {
            await Console.Error.WriteLineAsync(
                $"device is not enrolled (no {options.StateFilePath}); run the 'enroll' verb first").ConfigureAwait(false);
            return 2;
        }

        MachineMaterial.UseDeveloperPosture(options.DeveloperMaterialPosture);
        var runDataDirExisted = Directory.Exists(options.DataDir);
        Directory.CreateDirectory(options.DataDir);
        if (!runDataDirExisted)
        {
            MachineMaterial.Protect(options.DataDir, MachineMaterialKind.Directory);
        }

        builder.Logging.AddProvider(new FileLoggerProvider(options.LogFilePath));
        builder.Services.AddWindowsService(windowsOptions => windowsOptions.ServiceName = "PagentOSDeviceAgent");

        builder.Services.AddSingleton(options);
        builder.Services.AddSingleton(_ => DeviceIdentity.LoadOrCreate(options.KeyFilePath, options.DeveloperMaterialPosture));
        builder.Services.AddSingleton(new AuditLog(options.AuditLogPath));
        builder.Services.AddSingleton(new IdempotencyStore(options.IdempotencyStorePath));
        var admission = BuildAdmissionPolicy(options);
        builder.Services.AddSingleton(admission);
        builder.Services.AddSingleton(provider => new CompanionPipeServer(
            options.PipeName,
            admission,
            new WindowsPipePeerInspector(),
            provider.GetRequiredService<ILogger<CompanionPipeServer>>(),
            provider.GetRequiredService<AuditLog>(),
            // M22 (§6k): the origin this device dials, told to the companion in the challenge
            // so file.fetch is pinned to it on both sides of the pipe.
            brokerOrigin: options.BrokerRestUrl));
        builder.Services.AddSingleton<ICapabilityExecutor>(provider =>
            new InteractiveCapabilityExecutor(
                provider.GetRequiredService<CompanionPipeServer>(),
                browserEnabled: options.BrowserEnabled,
                displayPowerEnabled: options.DisplayPowerEnabled,
                // M18.3 (§6h): the only origin desktop.play_audio may fetch from.
                brokerRestUrl: options.BrokerRestUrl,
                // M19: the Digital Operator family, behind OperatorEnabled.
                operatorEnabled: options.OperatorEnabled));
        builder.Services.AddSingleton(provider => new CommandDispatcher(
            provider.GetRequiredService<IdempotencyStore>(),
            provider.GetRequiredService<ICapabilityExecutor>(),
            provider.GetRequiredService<AuditLog>(),
            provider.GetRequiredService<ILogger<CommandDispatcher>>()));
        builder.Services.AddSingleton(provider => new AgentConnection(
            new AgentConnectionOptions
            {
                BrokerWsUrl = new Uri(options.BrokerWsUrl),
                DeviceId = state.DeviceId,
                // A family is advertised only when this service routes it: the browser family
                // behind BrowserEnabled (M13), desktop.display_off behind DisplayPowerEnabled
                // (M18). The alarm pair and the desktop open_* pair are unconditional.
                Capabilities = options.AdvertisedCapabilities,
                BackoffBaseSeconds = options.BackoffBaseSeconds,
                BackoffMaxSeconds = options.BackoffMaxSeconds,
                HeartbeatIntervalOverrideS = options.HeartbeatIntervalOverrideS,
            },
            provider.GetRequiredService<DeviceIdentity>(),
            provider.GetRequiredService<CommandDispatcher>(),
            provider.GetRequiredService<AuditLog>(),
            provider.GetRequiredService<ILogger<AgentConnection>>(),
            // M12 (ADR-0039): voice_sideband frames are forwarded to the companion, opaquely.
            sidebandSink: new PipeSidebandForwarder(provider.GetRequiredService<CompanionPipeServer>()),
            // M18.3 (§6g): each heartbeat carries what the companion currently sees, when it can
            // say so within 1.5 s. It never delays or fails the heartbeat itself.
            statusProvider: new CompanionHeartbeatStatusProvider(provider.GetRequiredService<CompanionPipeServer>()),
            // ADR-0199: pointer_stream batches go to the companion one-way, for the mouse
            // session it opened; the service never touches the pointer.
            pointerSink: new PipePointerStreamForwarder(provider.GetRequiredService<CompanionPipeServer>())));

        builder.Services.AddHostedService(provider => provider.GetRequiredService<CompanionPipeServer>());
        builder.Services.AddHostedService(provider => new AgentWorker(
            provider.GetRequiredService<AgentConnection>(),
            provider.GetRequiredService<ILogger<AgentWorker>>()));

        using var host = builder.Build();
        var logger = host.Services.GetRequiredService<ILogger<AgentWorker>>();

        // Preflight before anything opens the key. A misprotected file is diagnosed here, in
        // the log, instead of surfacing as an access-denied crash and an SCM 1067.
        if (!VerifyMachineMaterial(options, logger))
        {
            logger.LogCritical(
                "refusing to start: the service account cannot use its own machine material. " +
                "Nothing was modified; the device identity and enrollment state are intact.");
            return 3;
        }
        // The identity FIRST, and the whole of it (2026-09-08 incident): a running service's
        // own log must answer "which version is this, and is it the one that was installed"
        // without anyone having to ask another process. `started_at` is here rather than
        // inferred from the log's timestamp because a rotated or re-read log loses that.
        logger.LogInformation(
            "agent identity: component={Component} software_version={SoftwareVersion} build_id={BuildId} assembly_version={AssemblyVersion} capability_manifest={CapabilityManifest} started_at={StartedAt}",
            AgentInfo.Component,
            AgentInfo.SoftwareVersion,
            AgentInfo.BuildId,
            AgentInfo.AssemblyVersion,
            AgentInfo.CapabilityManifestVersion,
            DateTimeOffset.UtcNow.ToString("O", System.Globalization.CultureInfo.InvariantCulture));
        logger.LogInformation(
            "starting device service: device_id={DeviceId} broker={Broker} data_dir={DataDir} pipe={Pipe}",
            state.DeviceId,
            options.BrokerWsUrl,
            options.DataDir,
            options.PipeName);
        logger.LogInformation(
            "companion admission: sid={Sid} session={Session} binary={Binary}",
            admission.AuthorizedSid,
            admission.ExpectedSessionId?.ToString() ?? "any interactive",
            admission.ExpectedImagePath ?? "not pinned");
        logger.LogInformation(
            "capabilities advertised: {Capabilities} (browser family {BrowserState}, operator family {OperatorState})",
            string.Join(",", options.AdvertisedCapabilities),
            options.BrowserEnabled ? "enabled" : "disabled",
            options.OperatorEnabled ? "enabled" : "disabled");
        await host.RunAsync().ConfigureAwait(false);
        return 0;
    }
}
