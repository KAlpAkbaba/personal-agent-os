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

        // M18 (DEVICE_PROTOCOL.md §6c): the wake alarm. It renders its own chime on its own
        // shared-mode stream in THIS session and never touches the machine's master volume;
        // the ramp is enforced by WakeRamp, independently of whatever Cloud Core validated.
        // With no render endpoint present the capability answers dependency_unavailable —
        // true about right now, and retryable — rather than pretending to have rung.
        var audioLogger = loggerFactory.CreateLogger("Alarm");
        (PagentOS.Companion.Audio.Audio.IAudioDeviceFactory Factory, Func<string?> ResolveRenderDevice)? audio =
            OperatingSystem.IsWindows() ? BuildAudioOutput(configuration, audioLogger) : null;
        var alarm = audio is null
            ? null
            : new AlarmController(audio.Value.Factory, audio.Value.ResolveRenderDevice, audioLogger, audit: audit);

        // M18.3 (§6h): the greeting. Same render path as the alarm, same "resolve the endpoint
        // per use" rule, and the same promise about the machine's mixer: it is never touched.
        using var greetingHttp = new HttpClient();
        var greeting = audio is null
            ? null
            : new GreetingPlayer(
                audio.Value.Factory,
                audio.Value.ResolveRenderDevice,
                greetingHttp,
                loggerFactory.CreateLogger("Greeting"),
                audit);

        // M18.3 (§6f): the LOCAL fallback for an alarm the cloud means to ring. Persisted in the
        // owner's profile, so a companion restart between "arm" and 06:30 does not lose it, and
        // consumed by the cloud's own alarm_start so one wake-up never rings twice.
        var armStorePath = configuration["ArmedAlarmStorePath"];
        if (string.IsNullOrWhiteSpace(armStorePath))
        {
            armStorePath = ArmedAlarmStore.DefaultPath();
        }

        var armLogger = loggerFactory.CreateLogger("AlarmArm");
        var alarmArms = new AlarmArmController(
            new ArmedAlarmStore(armStorePath, armLogger),
            alarm,
            armLogger,
            audit: audit);
        var rangOnStart = alarmArms.ReloadOnStart();
        logger.LogInformation(
            "armed alarm store: {Path} ({Count} armed, {Rang} rang on reload)",
            armStorePath,
            alarmArms.ArmedCount,
            rangOnStart);

        // M18 (§6d): display-off. OFF unless asked for out loud, because display-off has its
        // own owner qualification and a wrong sleep inference that blanks the screen
        // interrupts unrelated owner work. Not enabled => not advertised, and refused twice
        // over (here and in the Device Service) if something sends it anyway.
        var displayPowerEnabled = ParseFlag(configuration["DisplayPowerEnabled"]);

        // M18.3 (§6e/§6g): the two things this companion SENSES. Both are read-only, both are
        // unconditional (they add nothing to what anyone can already do to this machine), and
        // both report "unknown" rather than a guess when they have not been told anything.
        var inputActivity = OperatingSystem.IsWindows()
            ? new Win32InputActivitySource()
            : (IInputActivitySource)UnknownInputActivitySource.Instance;
        Win32DisplayStateObserver? displayObserver = null;
        if (OperatingSystem.IsWindows())
        {
            displayObserver = new Win32DisplayStateObserver(loggerFactory.CreateLogger("DisplayObserver"));
            displayObserver.Start();
        }

        var displayPower = OperatingSystem.IsWindows()
            ? new DisplayPowerController(
                new Win32DisplayPower(),
                loggerFactory.CreateLogger("DisplayPower"),
                displayPowerEnabled,
                audit,
                input: inputActivity,
                observer: displayObserver ?? (IDisplayStateObserver)UnknownDisplayStateObserver.Instance,
                monitors: new Win32MonitorInventory(),
                // The holdoff's second half: a screen must not go dark while an alarm is
                // ringing, whatever the idle timer says.
                isAlarmRinging: () => alarm?.IsRinging == true,
                wake: new Win32DisplayWake())
            : null;
        logger.LogInformation(
            displayPowerEnabled
                ? "display power: ENABLED - desktop.display_off is advertised and will turn the display off "
                  + "unless input is recent or an alarm is ringing"
                : "display power: disabled (PAGENTOS_AGENT_DisplayPowerEnabled=true enables it); "
                  + "desktop.display_off is not advertised. Waking and reporting stay available.");

        // B48 (rows 300, 326, 327, 671): the device camera's presence provider. Built always,
        // OPEN never until the owner's mode arrives (it starts "off"); Windows' camera
        // permission is read before every open, the tray shows whenever a mode is on, and only
        // the seven derived observation fields leave this process. CameraEnabled=false is the
        // device-local rollback: the camera is then never opened, whatever the cloud asks.
        var cameraOptions = Camera.CameraOptions.FromConfiguration(configuration);
        var camera = BuildCamera(cameraOptions, inputActivity, loggerFactory, audit);
        logger.LogInformation(
            "camera: {State} - mode starts off; periodic every {Periodic:F0} s, continuous every {Continuous:F0} s; frames are analysed in memory and never stored or sent",
            cameraOptions.EnabledOnDevice ? "available" : "DISABLED on this device (PAGENTOS_AGENT_CameraEnabled=false)",
            cameraOptions.PeriodicInterval.TotalSeconds,
            cameraOptions.ContinuousInterval.TotalSeconds);

        var activityStatus = new ActivityStatusReporter(
            inputActivity,
            displayObserver ?? (IDisplayStateObserver)UnknownDisplayStateObserver.Instance,
            () => alarm?.RingingAlarmId,
            alarmArms,
            camera);

        // B11 requirement 369 (found missing in B48): desktop.notify was advertised on every
        // device while no NotifyCapabilities object was ever handed to the runtime, so every
        // toast answered capability_missing. Built here, beside the other always-on objects.
        using var toastSink = OperatingSystem.IsWindows() ? new Notify.ShellToastSink(loggerFactory.CreateLogger("Notify")) : null;
        var notify = BuildNotify(toastSink, loggerFactory);

        // M19 (M19_DIGITAL_OPERATOR_SPEC.md §2/§3): the Digital Operator. OFF unless asked for
        // out loud on BOTH halves (this key and the service's), and built only then, so a
        // companion that was not told to operate the desktop has no object that could.
        var operatorOptions = Operator.OperatorOptions.FromConfiguration(configuration);

        // B49 (ADR-0161): where the Android toolchain is, as configured - read once, before the
        // native family's startup line says which tools this machine has.
        Native.NativeTools.Android = Native.NativeTools.AndroidToolchainOptions.FromConfiguration(configuration);
        Operator.OperatorCapabilities? operatorCapabilities = null;
        Documents.DocumentCapabilities? documentCapabilities = null;
        Projects.ProjectCapabilities? projectCapabilities = null;
        if (operatorOptions.Enabled && OperatingSystem.IsWindows())
        {
            operatorCapabilities = new Operator.OperatorCapabilities(operatorOptions, loggerFactory.CreateLogger("Operator"), audit);
            logger.LogInformation(
                "digital operator: ENABLED - {Count} capabilities; terminal allowlist=[{Allowlist}] roots=[{Roots}]",
                AgentCapabilities.Operator.Count,
                string.Join(" | ", operatorCapabilities.Terminal.Allowlist),
                string.Join(";", operatorCapabilities.AuthorisedRoots));

            // M20 (M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §2): the documents family rides the
            // same flag and the same roots — one decision, "the companion may touch the
            // owner's files", not two.
            // M22 (§6k): file.fetch opens what it kept through the operator's own file.open;
            // its origin arrives with every pipe challenge (CompanionRuntime), never from here.
            documentCapabilities = new Documents.DocumentCapabilities(operatorOptions, loggerFactory.CreateLogger("Documents"), audit, fileOpener: operatorCapabilities);
            logger.LogInformation(
                "documents: ENABLED - {Count} capabilities inside the operator roots (read-only except file.fetch, which writes new files into {Downloads}; secret-bearing names never read or written)",
                AgentCapabilities.Documents.Count,
                documentCapabilities.DownloadsRoot ?? "(no Downloads folder: every file.fetch is refused)");

            // M23 (M23_APP_FACTORY_SPEC.md §3, ADR-0086): the projects family — the same flag
            // once more. Source goes into the one Projects root only; a run is this process's
            // own child in a bounded job that dies with it (kill-on-close), so nothing the
            // companion started can outlive the companion.
            projectCapabilities = new Projects.ProjectCapabilities(operatorOptions, loggerFactory.CreateLogger("Projects"), audit);
            logger.LogInformation(
                "projects: ENABLED - {Count} capabilities; root={Root}; runs bounded to {Memory} MiB, {Cpu:F0} min CPU, {Lifetime:F0} min, {Max} at once",
                AgentCapabilities.Projects.Count,
                projectCapabilities.ProjectsRoot ?? "(no Documents folder: every project.scaffold is refused)",
                ProjectCapabilityNames.MemoryLimitBytes / (1024 * 1024),
                ProjectCapabilityNames.CpuTimeLimit.TotalMinutes,
                ProjectCapabilityNames.RunLifetime.TotalMinutes,
                ProjectCapabilityNames.MaxRunningProjects);

            // M25 (M25_CREATIVE_3D_SPEC.md §3/§7, ADR-0088): the two 3D runtimes and
            // scene.inspect ride the same object and the same flag. The tools are DETECTED
            // here so the log says, before anything is asked of them, which editors this
            // machine actually has — and a missing one is dependency_unavailable, never a
            // claim of control.
            logger.LogInformation(
                "scenes: ENABLED - {Count} capability; 3d root={Root3d}; blender bounded to {BlenderMemory} MiB / {BlenderMinutes:F0} min, unity to {UnityMemory} MiB / {UnityMinutes:F0} min; tools: {Tools}",
                AgentCapabilities.Scenes.Count,
                projectCapabilities.ProjectsRoot3d ?? "(no Documents folder: every 3D run is refused)",
                SceneCapabilityNames.BlenderMemoryLimitBytes / (1024 * 1024),
                SceneCapabilityNames.BlenderRunLimit.TotalMinutes,
                SceneCapabilityNames.UnityMemoryLimitBytes / (1024 * 1024),
                SceneCapabilityNames.UnityRunLimit.TotalMinutes,
                Scenes.SceneTools.Describe());

            // M28 (M28_NATIVE_APP_FACTORY_SPEC.md §5/§9, ADR-0095): the four build shapes ride
            // the same object, the same flag and the same job containment, one root over. The
            // toolchain is DETECTED here, before anything is asked of it, for the same reason
            // M25 detects the editors — and nothing here signs, so the log says that too.
            logger.LogInformation(
                "native builds: ENABLED - no new capability name; native root={RootNative}; builds bounded to {Memory} MiB / {Minutes:F0} min / {Processes} processes, CPU {Cpu:F0} min on {Cores} cores; tools: {Tools}; SIGNS NOTHING ({Forbidden} refused by name)",
                projectCapabilities.ProjectsRootNative ?? "(no Documents folder: every native build is refused)",
                NativeCapabilityNames.MemoryLimitBytes / (1024 * 1024),
                NativeCapabilityNames.RunLimit.TotalMinutes,
                NativeCapabilityNames.MaxProcessesPerJob,
                NativeCapabilityNames.CpuTimeLimitFor(Environment.ProcessorCount).TotalMinutes,
                Environment.ProcessorCount,
                Native.NativeTools.Describe(),
                string.Join(", ", NativeCapabilityNames.ForbiddenPrograms));
        }
        else
        {
            logger.LogInformation("digital operator: disabled (PAGENTOS_AGENT_OperatorEnabled=true enables it); the operator, documents, projects and scenes families are not advertised");
        }

        var runtime = new CompanionRuntime(
            pipeName,
            new AppLauncher(allowlist),
            artifactOpener,
            logger,
            backoff: null,
            servicePolicy: servicePolicy,
            sidebandSink: sidebandSource,
            browserWorker: browserHost,
            alarm: alarm,
            displayPower: displayPower,
            notify: notify,
            alarmArms: alarmArms,
            activityStatus: activityStatus,
            greeting: greeting,
            operatorCapabilities: operatorCapabilities,
            documentCapabilities: documentCapabilities,
            projectCapabilities: projectCapabilities,
            camera: camera);
        logger.LogInformation("capabilities advertised to the device service: {Capabilities}", string.Join(",", runtime.AdvertisedCapabilities));
        var cameraTask = camera.RunAsync(cts.Token);

        if (browserHost is not null && browserOptions.Eager)
        {
            await browserHost.StartAsync(cts.Token).ConfigureAwait(false);
        }

        // M18.4 gap 4: a staged worker update is a request file in the companion's data
        // directory naming a candidate under the install root; the host swaps the worker
        // after the current one drained (BrowserWorkerHost.SwapWorkerAsync) and the outcome
        // is written beside the request. Polled, never watched: one check every few seconds
        // costs nothing and cannot fire twice for one file.
        Task? candidatePoll = null;
        if (browserHost is not null)
        {
            var watcher = new BrowserCandidateWatcher(
                browserHost,
                dataDir,
                BrowserCandidateWatcher.DeriveAllowedRoot(browserOptions.WorkerCommand!),
                loggerFactory.CreateLogger("BrowserCandidate"),
                audit);
            logger.LogInformation("browser worker candidates: {Request} (candidates must live under {Root})", watcher.RequestPath, watcher.AllowedRoot);
            candidatePoll = Task.Run(async () =>
            {
                while (!cts.IsCancellationRequested)
                {
                    try
                    {
                        await Task.Delay(TimeSpan.FromSeconds(5), cts.Token).ConfigureAwait(false);
                        await watcher.PollOnceAsync(cts.Token).ConfigureAwait(false);
                    }
                    catch (OperationCanceledException) when (cts.IsCancellationRequested)
                    {
                        return;
                    }
                    catch (Exception ex)
                    {
                        logger.LogWarning("browser worker candidate poll failed: {Reason}", ex.Message);
                    }
                }
            });
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
            // The camera closes before anything else: an exiting companion must not leave it open.
            await cts.CancelAsync().ConfigureAwait(false);
            try
            {
                await cameraTask.ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
            }

            camera.Dispose();
            (camera.Indicator as IDisposable)?.Dispose();

            if (browserHost is not null)
            {
                // "shutdown" on stdin, a bounded wait, then the process tree — Chrome included.
                await browserHost.StopAsync().ConfigureAwait(false);
            }

            // M23: every project job this process holds is ended — its own children only.
            projectCapabilities?.Dispose();

            // A ringing alarm must not outlive the process that started it: there would be no
            // way left to stop it except killing the audio session.
            alarmArms.Dispose();
            alarm?.Dispose();
            displayObserver?.Dispose();
        }

        return 0;
    }

    /// <summary>
    /// B48: the production camera provider - the WinRT capture and face pass, the tray
    /// indicator, Windows' permission switches and the render peak meter - or, on a host
    /// without them, a monitor that answers truthfully that there is no camera.
    /// </summary>
    public static Camera.CameraPresenceMonitor BuildCamera(
        Camera.CameraOptions options,
        IInputActivitySource input,
        ILoggerFactory loggerFactory,
        AuditLog? audit)
    {
        var cameraLogger = loggerFactory.CreateLogger("Camera");
        if (OperatingSystem.IsWindowsVersionAtLeast(10, 0, 19041))
        {
            return new Camera.CameraPresenceMonitor(
                new Camera.WindowsCameraFrameSource(),
                new Camera.TrayCameraIndicator(cameraLogger),
                cameraLogger,
                options,
                new Camera.WindowsCameraConsent(),
                input,
                new Camera.RenderPeakMediaProbe(options.MediaPeakThreshold),
                audit: audit);
        }

        return new Camera.CameraPresenceMonitor(
            Camera.NoCameraFrameSource.Instance,
            new Camera.RecordingCameraIndicator(),
            cameraLogger,
            options,
            input: input,
            audit: audit);
    }

    /// <summary>B11 req 369: the toast capability over the given sink, or none when there is no sink.</summary>
    public static Notify.NotifyCapabilities? BuildNotify(Notify.IToastSink? sink, ILoggerFactory loggerFactory)
        => sink is null ? null : new Notify.NotifyCapabilities(sink, loggerFactory.CreateLogger("Notify"));

    /// <summary>"true"/"1"/"yes" (any case) are true; absent, blank and anything else are false.</summary>
    public static bool ParseFlag(string? raw)
    {
        var value = raw?.Trim();
        return string.Equals(value, "true", StringComparison.OrdinalIgnoreCase)
               || string.Equals(value, "1", StringComparison.Ordinal)
               || string.Equals(value, "yes", StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>
    /// The companion's render path, shared by the alarm (§6c) and the greeting (§6h): the
    /// configured <c>AlarmRenderDevice</c>, else <c>VoiceRenderDevice</c> (the owner already
    /// chose a speaker for the assistant's voice), else the session's default render endpoint.
    /// Resolved lazily on every use, not once at startup — a headset plugged in after the
    /// companion started should be usable by the next alarm, and by the greeting after it.
    /// </summary>
    [System.Runtime.Versioning.SupportedOSPlatform("windows")]
    private static (PagentOS.Companion.Audio.Audio.IAudioDeviceFactory Factory, Func<string?> ResolveRenderDevice)
        BuildAudioOutput(IConfiguration configuration, ILogger logger)
    {
        var configured = configuration["AlarmRenderDevice"];
        if (string.IsNullOrWhiteSpace(configured))
        {
            configured = configuration["VoiceRenderDevice"];
        }

        var catalog = new PagentOS.Companion.Audio.Wasapi.WasapiDeviceCatalog();
        var factory = new PagentOS.Companion.Audio.Wasapi.WasapiDeviceFactory(catalog, logger);
        return (factory, () => ResolveRenderDevice(catalog, configured));
    }

    [System.Runtime.Versioning.SupportedOSPlatform("windows")]
    private static string? ResolveRenderDevice(
        PagentOS.Companion.Audio.Audio.IAudioDeviceCatalog catalog,
        string? configured)
    {
        var devices = catalog.List(PagentOS.Companion.Audio.Audio.AudioDirection.Render);
        if (devices.Count == 0)
        {
            return null;
        }

        if (!string.IsNullOrWhiteSpace(configured))
        {
            var match = devices.FirstOrDefault(d =>
                string.Equals(d.Id, configured, StringComparison.OrdinalIgnoreCase)
                || d.Name.Contains(configured, StringComparison.OrdinalIgnoreCase));
            if (match is not null)
            {
                return match.Id;
            }
        }

        return (devices.FirstOrDefault(d => d.IsDefault) ?? devices[0]).Id;
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
