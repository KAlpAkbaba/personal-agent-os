namespace PagentOS.Agent.Core.Protocol;

public static class ProtocolConstants
{
    public const int Version = 1;

    /// <summary>Maximum length of ErrorObject.message per schema.</summary>
    public const int MaxErrorMessageLength = 2000;
}

public static class AgentInfo
{
    // 0.2.0 (M18.4): the M18.3 alarm/ambient capabilities, the staged worker update and
    // the candidate manifest; a version the installer can tell apart from 0.1.0 on Cloud Core.
    // 0.3.0 (M20, ADR-0083): the documents family (file.search/locate/inspect/read/compare,
    // document.extract) behind the operator gate, and the error classes it needs.
    // 0.4.0 (M22, ADR-0085): file.fetch — a Cloud Core render, from the dialled origin only,
    // into the Downloads root, hash-verified before it is kept — and the broker origin the
    // service now hands the companion in the pipe challenge.
    // 0.5.0 (M23, ADR-0086): the projects family (project.scaffold/run/status/stop/test) —
    // source written into the one Projects root, a run as the companion's own child in a
    // bounded Windows Job Object — behind the same operator gate.
    // 0.6.0 (M25, ADR-0088): the two 3D runtimes on the projects family's manifest allowlist
    // (Blender headless and Unity batch mode, under the 3D root only) and scene.inspect —
    // the tool's own read-back and its render — behind the same operator gate.
    // 0.6.0 stays put through M28 (ADR-0095 addendum 2 §1): the native factory added the
    // `native` root, a project scope and four argv shapes, but NO new capability name, so the
    // advertised manifest is unchanged at 40/85 and the qualification arithmetic holds. What
    // M28 exposed instead is that a product version cannot also serve as a build identity —
    // see BuildId.
    public const string SoftwareVersion = "0.6.0";
    public const string Platform = "windows";

    /// <summary>
    /// Which half of the agent this process is. The staged-update verifier and Cloud Core
    /// both need to say WHICH component announced a version; "the device reports 0.6.0" is
    /// only meaningful once it names what did the reporting.
    /// </summary>
    public const string Component = "device-service";

    /// <summary>
    /// The assembly version this binary was built with, as a three-part string.
    /// <c>Directory.Build.props</c> sets it from the same number as
    /// <see cref="SoftwareVersion"/>, and <c>AgentIdentityTests</c> asserts they are equal:
    /// there is ONE canonical version identity for this agent, and a file-version stamp that
    /// disagreed with the announced version would be a second one.
    /// </summary>
    public static string AssemblyVersion =>
        typeof(AgentInfo).Assembly.GetName().Version?.ToString(3) ?? "0.0.0";

    /// <summary>
    /// A DERIVED fingerprint of the full capability vocabulary this binary can advertise —
    /// every family, every gate on — as the first 12 hex characters of SHA-256 over the
    /// newline-joined superset manifest.
    ///
    /// Derived rather than hand-maintained on purpose: a hand-written manifest version is a
    /// number someone forgets to bump, and the failure mode is a device that announces an
    /// old manifest identity with a new manifest. This one cannot drift — adding, removing
    /// or reordering a capability name changes it by construction, so two installs that
    /// report the same fingerprint really do speak the same capability vocabulary.
    /// </summary>
    public static string CapabilityManifestVersion
    {
        get
        {
            var superset = string.Join(
                "\n",
                AgentCapabilities.Compose(browserEnabled: true, displayPowerEnabled: true, operatorEnabled: true));
            var digest = System.Security.Cryptography.SHA256.HashData(
                System.Text.Encoding.UTF8.GetBytes(superset));
            return Convert.ToHexString(digest)[..12].ToLowerInvariant();
        }
    }

    /// <summary>
    /// The commit this binary was built from, or an empty string when it cannot be known.
    /// </summary>
    /// <remarks>
    /// Read from <c>AssemblyInformationalVersionAttribute</c>, whose <c>+metadata</c> the
    /// .NET SDK stamps from the git checkout automatically — the installed agent carries
    /// <c>0.6.0+72843b21…</c> without anything in this repository configuring it. It is
    /// PROVENANCE, not identity: two builds of the same commit share it, and a build from a
    /// dirty tree names the commit it was not quite built from. <see cref="BuildId"/> is the
    /// identity; this is how a human finds the source.
    /// </remarks>
    public static string SourceRevision
    {
        get
        {
            var informational = typeof(AgentInfo).Assembly
                .GetCustomAttributes(typeof(System.Reflection.AssemblyInformationalVersionAttribute), false)
                .OfType<System.Reflection.AssemblyInformationalVersionAttribute>()
                .FirstOrDefault()?.InformationalVersion ?? string.Empty;
            var plus = informational.IndexOf('+');
            return plus >= 0 ? informational[(plus + 1)..] : string.Empty;
        }
    }

    /// <summary>
    /// What this build IS: 16 hex characters over the content of the agent's own assemblies.
    /// </summary>
    /// <remarks>
    /// <para>
    /// The 2026-09-08 incident rolled a healthy release back because the staged updater
    /// compares what the device ANNOUNCES, and the only identity it announced was
    /// <see cref="SoftwareVersion"/>. That field is a product version and is meant to stay
    /// still across builds: on 2026-09-11 the deployed agent advertised the full 85-capability
    /// M28 manifest while announcing <c>0.6.0</c>, which is the M25 number. Two different
    /// builds were therefore indistinguishable to the one comparison that decides whether a
    /// staged update took — so "Cloud Core sees the candidate" could not be proven, only
    /// assumed.
    /// </para>
    /// <para>
    /// This is the missing half, and it is deliberately NOT a second version number to
    /// maintain. It is derived — every <c>PagentOS.*.dll</c> beside this assembly, sorted by
    /// name, each file's SHA-256 folded into one digest — so it cannot drift, cannot be
    /// forgotten, and changes by construction when any of the agent's own code changes. The
    /// .NET runtime files next to it are excluded: a runtime patch is not a new agent build.
    /// </para>
    /// <para>
    /// Semantic versioning is untouched. <see cref="SoftwareVersion"/> still answers "which
    /// product release is this" and <c>AgentIdentityTests</c> still holds it equal to the
    /// assembly version; <see cref="BuildId"/> answers "which build is this", which is the
    /// question the staged updater was actually asking.
    /// </para>
    /// <para>
    /// Never throws. A host where the directory cannot be read answers <c>"unknown"</c>, and
    /// a comparison against <c>"unknown"</c> is treated by the updater as "no build identity
    /// available", never as a match.
    /// </para>
    /// </remarks>
    public static string BuildId => _buildId.Value;

    /// <summary>What <see cref="BuildId"/> answers when the agent's own files cannot be read.</summary>
    public const string UnknownBuildId = "unknown";

    private static readonly Lazy<string> _buildId = new(ComputeBuildId);

    private static string ComputeBuildId()
    {
        try
        {
            var home = System.IO.Path.GetDirectoryName(typeof(AgentInfo).Assembly.Location);
            if (string.IsNullOrEmpty(home))
            {
                return UnknownBuildId;
            }

            var files = System.IO.Directory
                .GetFiles(home, "PagentOS.*.dll", System.IO.SearchOption.TopDirectoryOnly)
                .OrderBy(path => System.IO.Path.GetFileName(path), StringComparer.Ordinal)
                .ToArray();
            if (files.Length == 0)
            {
                return UnknownBuildId;
            }

            using var fold = System.Security.Cryptography.IncrementalHash.CreateHash(
                System.Security.Cryptography.HashAlgorithmName.SHA256);
            foreach (var file in files)
            {
                // The name is folded in as well as the bytes, so adding or removing an
                // assembly changes the identity even if the remaining bytes are untouched.
                fold.AppendData(System.Text.Encoding.UTF8.GetBytes(System.IO.Path.GetFileName(file)));
                fold.AppendData(System.Security.Cryptography.SHA256.HashData(System.IO.File.ReadAllBytes(file)));
            }

            return Convert.ToHexString(fold.GetHashAndReset())[..16].ToLowerInvariant();
        }
        catch (Exception)
        {
            // An identity that throws would take the handshake down with it. A build that
            // cannot say which build it is says so.
            return UnknownBuildId;
        }
    }
}

public static class AckStatus
{
    public const string Accepted = "accepted";
    public const string Running = "running";
    public const string Succeeded = "succeeded";
    public const string Failed = "failed";

    public static readonly IReadOnlySet<string> All =
        new HashSet<string>(StringComparer.Ordinal) { Accepted, Running, Succeeded, Failed };

    public static bool IsTerminal(string status) => status is Succeeded or Failed;
}

/// <summary>
/// The capability manifest this device advertises (enrollment, WS <c>hello</c>, companion
/// hello). Four groups, each with its own rule about when it appears:
/// <list type="bullet">
/// <item><c>desktop.open_*</c> — always present (M1/M3 behaviour, unchanged).</item>
/// <item><c>desktop.alarm_*</c> — always present (M18). The companion can always answer them:
/// with no render endpoint it says <c>dependency_unavailable</c>, which is a true statement
/// about right now, not a missing capability.</item>
/// <item><c>desktop.display_wake</c>, <c>desktop.display_status</c>,
/// <c>desktop.activity_status</c>, <c>desktop.alarm_arm</c>, <c>desktop.alarm_disarm</c>,
/// <c>desktop.play_audio</c> — always present (M18.3). Every one of them is safe by
/// construction: waking a screen, reading a state, arming a local fallback and playing a
/// bounded sound the owner's own broker served are all reversible and none of them takes
/// anything away from the owner. Only the one irreversible-feeling action — darkening a
/// screen — stays behind a flag.</item>
/// <item><c>desktop.display_off</c> — present only when the companion was started with
/// <c>DisplayPowerEnabled</c>. Display-off has its own owner qualification
/// (M18_HOLOGRAPHIC_CORE_SPEC.md §7), and until it has run this device must look to Cloud
/// Core like a device that cannot blank a screen, because that is what it is.</item>
/// <item><c>browser.*</c> (BROWSER_CAPABILITIES.md §1, M13) — present only when a Browser
/// Worker is configured: service side <c>BrowserEnabled=true</c>, companion side
/// <c>BrowserWorkerCommand</c> set.</item>
/// </list>
/// <see cref="Compose"/> is the one place they are joined, so a manifest can never advertise
/// a name the device has nothing to execute.
/// </summary>
public static class AgentCapabilities
{
    public const string DesktopOpenApplication = "desktop.open_application";
    public const string DesktopOpenArtifact = "desktop.open_artifact";

    /// <summary>M18: start the wake alarm — a volume RAMP, never a level (DEVICE_PROTOCOL.md §6c).</summary>
    public const string DesktopAlarmStart = "desktop.alarm_start";

    /// <summary>M18: stop a ringing alarm. An alarm that cannot be stopped is not an alarm.</summary>
    public const string DesktopAlarmStop = "desktop.alarm_stop";

    /// <summary>M18.3: arm a LOCAL fallback for an alarm the cloud intends to ring (§6f).</summary>
    public const string DesktopAlarmArm = "desktop.alarm_arm";

    /// <summary>M18.3: forget a local fallback arm (§6f). Idempotent by <c>alarm_id</c>.</summary>
    public const string DesktopAlarmDisarm = "desktop.alarm_disarm";

    /// <summary>
    /// B11 requirement 369: a desktop toast. The one channel that reaches the owner with the
    /// browser closed and the screen locked, and only this process can raise it — nothing in
    /// the cloud runs in the owner's interactive session. Always advertised: it only adds,
    /// and it has a truthful answer on every device (<c>shown: false</c> with a reason when
    /// there is no session or the owner has turned notifications off).
    /// </summary>
    public const string DesktopNotify = "desktop.notify";

    /// <summary>M18: turn the display off — the ONLY machine-state action, and only off (§6e).</summary>
    public const string DesktopDisplayOff = "desktop.display_off";

    /// <summary>M18.3: nudge the display back on. Never a key event, never the machine's power state (§6e).</summary>
    public const string DesktopDisplayWake = "desktop.display_wake";

    /// <summary>M18.3: report what the display observer has seen, and the monitor geometry (§6e).</summary>
    public const string DesktopDisplayStatus = "desktop.display_status";

    /// <summary>M18.3: idle time, display state and alarm state — also the heartbeat's <c>status</c> (§6g).</summary>
    public const string DesktopActivityStatus = "desktop.activity_status";

    /// <summary>M18.3: play one short greeting the owner's own broker served (§6h).</summary>
    public const string DesktopPlayAudio = "desktop.play_audio";

    /// <summary>
    /// B47 (rows 239, 250-252, 254, 255): the device microphone provider's state - whether this
    /// device listens, how (continuous, wake word, push-to-talk), what its privacy indicator
    /// shows, whether the endpoint is muted, whether a session is up and how often it has been
    /// restarted (§6i). Read-only. Always advertised: a device whose voice service is off
    /// answers <c>state: "disabled"</c>, which is the truthful answer, and there is deliberately
    /// no name that turns a microphone ON from the cloud.
    /// </summary>
    public const string DesktopVoiceStatus = "desktop.voice_status";

    /// <summary>
    /// B48 (rows 300, 326, 327, 671; DEVICE_PROTOCOL.md §6p): set or read the device camera's
    /// presence mode — <c>off</c> | <c>periodic</c> | <c>continuous</c>. Advertised
    /// unconditionally in the ambient group: the mode is <c>off</c> after every start, only the
    /// owner's choice relayed by Cloud Core turns it on, and what leaves the device is the
    /// seven-field observation, never a frame.
    /// </summary>
    public const string DesktopCameraMode = "desktop.camera_mode";

    /// <summary>The desktop family — what every device advertises (M1/M3 behaviour, unchanged).</summary>
    public static readonly IReadOnlyList<string> Desktop = [DesktopOpenApplication, DesktopOpenArtifact];

    /// <summary>The alarm pair (M18). Always advertised; see the class docstring for why.</summary>
    public static readonly IReadOnlyList<string> Alarm = [DesktopAlarmStart, DesktopAlarmStop];

    /// <summary>
    /// The M18.3 living-core group: everything the companion can do that only ADDS — waking a
    /// screen, reporting a state, arming a local fallback, playing a short greeting. Always
    /// advertised, for the same reason the alarm pair is: each of them has a truthful answer
    /// on every device, and none of them can take anything away from the owner.
    /// </summary>
    public static readonly IReadOnlyList<string> Ambient =
    [
        DesktopDisplayWake, DesktopDisplayStatus, DesktopActivityStatus,
        DesktopAlarmArm, DesktopAlarmDisarm, DesktopPlayAudio, DesktopNotify,
        DesktopVoiceStatus,
        // B48: appended after B47's voice status, so the manifest diff reads as one addition.
        DesktopCameraMode,
    ];

    /// <summary>Display power (M18). Advertised only behind <c>DisplayPowerEnabled</c>.</summary>
    public static readonly IReadOnlyList<string> DisplayPower = [DesktopDisplayOff];

    /// <summary>
    /// The M1/M3 baseline manifest: identical to <see cref="Desktop"/>. Kept under its
    /// historical name so those callers (and their byte-for-byte hello/enrollment
    /// expectations) are untouched; every later family is added through
    /// <see cref="Compose"/> only.
    /// </summary>
    public static readonly IReadOnlyList<string> All = Desktop;

    /// <summary>The browser family (family marker + every per-operation name).</summary>
    public static IReadOnlyList<string> Browser => BrowserCapabilities.All;

    /// <summary>
    /// The Digital Operator family (M19, M19_DIGITAL_OPERATOR_SPEC.md §2): apps, windows,
    /// keyboard, pointer, UI Automation, screen, files and the governed terminal. Advertised
    /// only behind <c>OperatorEnabled</c>; every member is interactive and executed by the
    /// companion under the focus guard.
    /// </summary>
    public static IReadOnlyList<string> Operator => OperatorCapabilityNames.All;

    /// <summary>
    /// The documents family (M20, M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §2, ADR-0083):
    /// search, locate, inspect, read and compare files inside the authorised roots and
    /// extract a document's text and structure with the references an answer cites. It is
    /// advertised behind the SAME gate as the operator family (<c>OperatorEnabled</c> on both
    /// halves) because it is the same trust decision: the companion may touch the owner's
    /// files. Read-only by construction — there is no delete, move or write name — with one
    /// creator since M22: <c>file.fetch</c> brings a Cloud Core render into the Downloads root
    /// as a NEW file (never over an existing one), from the origin the device dialled only.
    /// </summary>
    public static IReadOnlyList<string> Documents => DocumentCapabilityNames.All;

    /// <summary>
    /// The projects family (M23, M23_APP_FACTORY_SPEC.md §3, ADR-0086): scaffold an app the
    /// assistant generated into the one Projects root, run it as the companion's own child in
    /// a bounded Windows Job Object, report it, stop it, run its own tests. Advertised behind
    /// the SAME gate as the operator family — the companion may write source and start a
    /// process on the owner's machine is the same trust decision as touching the owner's
    /// files — and appended after the documents family so the manifest reads as an addition.
    /// There is no delete name (ADR-0086 decision 5).
    /// </summary>
    public static IReadOnlyList<string> Projects => ProjectCapabilityNames.All;

    /// <summary>
    /// The scenes family (M25, M25_CREATIVE_3D_SPEC.md §3/§7, ADR-0088 decision 3): one name,
    /// <c>scene.inspect</c> — the tool's own read-back of a 3D project (the bounded
    /// <c>out.json</c> a driver wrote) and the render it declares, hash-checked. It rides the
    /// projects family's object and its 3D root, is advertised behind the SAME
    /// <c>OperatorEnabled</c> gate, and is appended after the projects family so the manifest
    /// reads as an addition. There is no scene creator on the device: a scene is created by
    /// <c>project.run</c> of an allowlisted 3D runtime, and this name only reads back.
    /// </summary>
    public static IReadOnlyList<string> Scenes => SceneCapabilityNames.All;

    /// <summary>
    /// The manifest this device actually advertises: the desktop names, the alarm pair and
    /// the M18.3 ambient group always, display power, the browser family and the operator
    /// family (with the documents, projects and scenes families that share its gate) only when
    /// each is configured. Order is stable (desktop, alarm, ambient, display, browser,
    /// operator, documents, projects, scenes) so a manifest diff between two versions reads as
    /// an addition rather than a reshuffle.
    /// </summary>
    public static IReadOnlyList<string> Compose(bool browserEnabled, bool displayPowerEnabled = false, bool operatorEnabled = false)
    {
        var names = new List<string>(
            Desktop.Count + Alarm.Count + Ambient.Count + DisplayPower.Count + BrowserCapabilities.All.Count
            + OperatorCapabilityNames.All.Count + DocumentCapabilityNames.All.Count + ProjectCapabilityNames.All.Count
            + SceneCapabilityNames.All.Count);
        names.AddRange(Desktop);
        names.AddRange(Alarm);
        names.AddRange(Ambient);
        if (displayPowerEnabled)
        {
            names.AddRange(DisplayPower);
        }

        if (browserEnabled)
        {
            names.AddRange(BrowserCapabilities.All);
        }

        if (operatorEnabled)
        {
            names.AddRange(OperatorCapabilityNames.All);
            names.AddRange(DocumentCapabilityNames.All);
            names.AddRange(ProjectCapabilityNames.All);
            names.AddRange(SceneCapabilityNames.All);
        }

        return names;
    }

    public static bool IsDesktop(string capability) => Desktop.Contains(capability, StringComparer.Ordinal);

    public static bool IsAlarm(string capability) => Alarm.Contains(capability, StringComparer.Ordinal);

    public static bool IsAmbient(string capability) => Ambient.Contains(capability, StringComparer.Ordinal);

    public static bool IsDisplayPower(string capability) => DisplayPower.Contains(capability, StringComparer.Ordinal);

    /// <summary>M19: a member of the Digital Operator family (never the browser family, which has its own worker).</summary>
    public static bool IsOperator(string capability) => OperatorCapabilityNames.IsMember(capability);

    /// <summary>M20: a member of the documents family — routed like the operator family, gated by the same flag, never a member of it.</summary>
    public static bool IsDocuments(string capability) => DocumentCapabilityNames.IsMember(capability);

    /// <summary>M23: a member of the projects family — routed like the operator family, gated by the same flag, never a member of it.</summary>
    public static bool IsProjects(string capability) => ProjectCapabilityNames.IsMember(capability);

    /// <summary>M25: a member of the scenes family — routed like the projects family, gated by the same flag, executed by the same companion object, never a member of the projects family.</summary>
    public static bool IsScenes(string capability) => SceneCapabilityNames.IsMember(capability);

    /// <summary>
    /// Every name the Session Companion executes in the owner's interactive session. The
    /// Device Service routes exactly this set over the pipe and refuses everything else
    /// outside the browser family, so a new interactive name is reachable only by being
    /// added here — never by being spelled <c>desktop.</c>-something.
    /// </summary>
    public static bool IsInteractive(string capability)
        => IsDesktop(capability) || IsAlarm(capability) || IsAmbient(capability) || IsDisplayPower(capability) || IsOperator(capability) || IsDocuments(capability) || IsProjects(capability) || IsScenes(capability);

    public static bool IsBrowser(string capability) => BrowserCapabilities.IsFamilyMember(capability);
}

/// <summary>
/// M19_DIGITAL_OPERATOR_SPEC.md §2 — the Digital Operator names. The wire contract with Cloud
/// Core's <c>OperatorTask</c>; change the document first. Unlike the browser family there is no
/// family marker: every name here is an operation the companion executes itself, in the
/// owner's session, and re-observes afterwards.
/// </summary>
public static class OperatorCapabilityNames
{
    public const string AppLaunch = "app.launch";
    public const string AppList = "app.list";
    public const string AppActivate = "app.activate";
    public const string AppClose = "app.close";

    public const string WindowList = "window.list";
    public const string WindowCurrent = "window.current";
    public const string WindowActivate = "window.activate";
    public const string WindowMinimize = "window.minimize";
    public const string WindowMaximize = "window.maximize";
    public const string WindowRestore = "window.restore";
    public const string WindowMove = "window.move";
    public const string WindowResize = "window.resize";
    public const string WindowClose = "window.close";

    public const string KeyboardType = "keyboard.type";
    public const string KeyboardKey = "keyboard.key";
    public const string KeyboardShortcut = "keyboard.shortcut";

    public const string PointerMove = "pointer.move";
    public const string PointerClick = "pointer.click";
    public const string PointerDoubleClick = "pointer.double_click";
    public const string PointerRightClick = "pointer.right_click";
    public const string PointerScroll = "pointer.scroll";

    public const string UiInspect = "ui.inspect";
    public const string UiInvoke = "ui.invoke";
    public const string UiSetValue = "ui.set_value";
    public const string UiSelect = "ui.select";

    public const string ScreenCapture = "screen.capture";
    public const string ScreenInspect = "screen.inspect";

    public const string FileOpen = "file.open";
    public const string FileReveal = "file.reveal";

    public const string TerminalOpen = "terminal.open";
    public const string TerminalExecute = "terminal.execute";
    public const string TerminalStatus = "terminal.status";

    /// <summary>
    /// B30 (requirements 119-122): the processes and the Windows services of this machine.
    /// <c>process.list</c> and <c>service.status</c> are reads; <c>process.stop</c> is allowed
    /// only for an image <c>packages/protocol/operator-allowlists.json</c> names as stoppable
    /// and <c>service.restart</c> only for a service it names as restartable, both refused with
    /// <c>permission_denied</c> before anything is touched — and a restart also needs an
    /// elevated companion, which the owner grants through UAC and this device never bypasses.
    /// </summary>
    public const string ProcessList = "process.list";
    public const string ProcessStop = "process.stop";
    public const string ServiceStatus = "service.status";
    public const string ServiceRestart = "service.restart";

    /// <summary>Every operator name, in the order of the specification's table.</summary>
    public static readonly IReadOnlyList<string> All =
    [
        AppLaunch, AppList, AppActivate, AppClose,
        WindowList, WindowCurrent, WindowActivate, WindowMinimize, WindowMaximize, WindowRestore,
        WindowMove, WindowResize, WindowClose,
        KeyboardType, KeyboardKey, KeyboardShortcut,
        PointerMove, PointerClick, PointerDoubleClick, PointerRightClick, PointerScroll,
        UiInspect, UiInvoke, UiSetValue, UiSelect,
        ScreenCapture, ScreenInspect,
        FileOpen, FileReveal,
        TerminalOpen, TerminalExecute, TerminalStatus,
        ProcessList, ProcessStop, ServiceStatus, ServiceRestart,
    ];

    /// <summary>The names that synthesise input and therefore run under the focus guard (§1, invariant 2).</summary>
    public static readonly IReadOnlyList<string> Guarded =
    [
        KeyboardType, KeyboardKey, KeyboardShortcut,
        PointerMove, PointerClick, PointerDoubleClick, PointerRightClick, PointerScroll,
    ];

    /// <summary>§3: the service's per-command cap for the family.</summary>
    public static readonly TimeSpan CommandTimeoutCap = TimeSpan.FromSeconds(30);

    /// <summary>§3: <c>app.launch</c> waits at most 10 s for a window, so its cap is shorter than the family's.</summary>
    public static readonly TimeSpan LaunchTimeoutCap = TimeSpan.FromSeconds(15);

    /// <summary>§2: the longest text one <c>keyboard.type</c> may carry.</summary>
    public const int MaxTypedChars = 4096;

    /// <summary>§2: the largest <c>screen.capture</c> result, PNG bytes before base64.</summary>
    public const int MaxCaptureBytes = 2 * 1024 * 1024;

    public static bool IsMember(string capability) => All.Contains(capability, StringComparer.Ordinal);

    public static bool IsGuarded(string capability) => Guarded.Contains(capability, StringComparer.Ordinal);
}

/// <summary>
/// M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §2 — the documents family. The wire contract with
/// Cloud Core's document intelligence (<c>app/documents/</c>) and the fixtures' oracle
/// (<c>services/api/tests/fixtures/documents/</c>); change the document first. Every name is
/// an operation the companion executes itself inside the owner's authorised roots; the
/// bounds below are the spec's, and a result that hits one says <c>truncated: true</c> rather
/// than cutting silently.
/// </summary>
public static class DocumentCapabilityNames
{
    public const string FileSearch = "file.search";
    public const string FileLocate = "file.locate";
    public const string FileInspect = "file.inspect";
    public const string FileRead = "file.read";
    public const string FileCompare = "file.compare";
    public const string DocumentExtract = "document.extract";

    /// <summary>
    /// M22 (M22_ARTIFACT_FACTORY_SPEC.md §4, ADR-0085 decision 4, DEVICE_PROTOCOL.md §6k):
    /// download one Cloud Core render — from the origin the device dialled, nowhere else —
    /// into the Downloads root, verify its SHA-256 before it is kept, optionally open it
    /// through <c>file.open</c>. Appended to the family so the manifest reads as an addition.
    /// </summary>
    public const string FileFetch = "file.fetch";

    /// <summary>
    /// B32 requirement 150 (DEVICE_PROTOCOL.md §6j): move ONE file inside the authorised
    /// roots to the Recycle Bin — never a permanent delete — after the Cloud Core's
    /// duplicate proposal and the owner's explicit word. Appended after <c>file.fetch</c>.
    /// </summary>
    public const string FileTrash = "file.trash";

    /// <summary>
    /// B34 requirements 153–165 (DEVICE_PROTOCOL.md §6m, appended): the managed mutations.
    /// Each acts on ONE file inside the authorised roots, backs the file up to the root's
    /// undo store before changing it (write / append / trash), writes atomically, and answers
    /// the record BEFORE and AFTER with sha256. Only text-like kinds are written or appended
    /// to. <c>file.restore</c> puts a backup back. Nothing here deletes permanently.
    /// </summary>
    public const string FileWrite = "file.write";
    public const string FileAppend = "file.append";
    public const string FileRename = "file.rename";
    public const string FileMove = "file.move";
    public const string FileCopy = "file.copy";
    public const string FileRestore = "file.restore";

    /// <summary>Every documents name, in the order of the specification's table (<c>file.fetch</c> M22, <c>file.trash</c> B32, the six mutations B34, appended).</summary>
    public static readonly IReadOnlyList<string> All =
    [
        FileSearch, FileLocate, FileInspect, FileRead, FileCompare, DocumentExtract, FileFetch, FileTrash,
        FileWrite, FileAppend, FileRename, FileMove, FileCopy, FileRestore,
    ];

    /// <summary>§6k: the most <c>file.fetch</c> will download — the same 50 MiB the family reads.</summary>
    public const long MaxFetchBytes = 50L * 1024 * 1024;

    /// <summary>§6k: the longest <c>payload.name</c> <c>file.fetch</c> accepts (a plain file name; the unique suffix on collision is the companion's own).</summary>
    public const int MaxFetchNameChars = 120;

    /// <summary>§6k: the only path prefix a render URL may have on the dialled origin.</summary>
    public const string FetchPathPrefix = "/v1/artifacts/";

    /// <summary>§2: <c>file.search</c> answers at most this many records.</summary>
    public const int MaxSearchResults = 200;

    /// <summary>§2: <c>file.search</c> looks at no more than this many directory entries.</summary>
    public const int MaxSearchEntries = 20_000;

    /// <summary>§2: <c>file.search</c> stops after this long, whatever it has found.</summary>
    public static readonly TimeSpan SearchTimeout = TimeSpan.FromSeconds(10);

    /// <summary>§2: <c>file.read</c> returns at most this many characters per call.</summary>
    public const int MaxReadChars = 65_536;

    /// <summary>§2: <c>document.extract</c> carries at most this many characters of block text.</summary>
    public const int MaxExtractChars = 65_536;

    /// <summary>§2: a file larger than this is not opened for reading or extraction.</summary>
    public const long MaxFileBytes = 50L * 1024 * 1024;

    /// <summary>§2: a record carries <c>sha256</c> only when the file is at most this large.</summary>
    public const long Sha256SizeLimit = 8L * 1024 * 1024;

    /// <summary>§2: a PDF is extracted up to this many pages.</summary>
    public const int MaxPdfPages = 200;

    /// <summary>§2: a worksheet is extracted up to this many rows.</summary>
    public const int MaxSheetRows = 2_000;

    /// <summary>The service's per-command cap for the family: the search bound plus extraction headroom, the same 30 s as the operator family.</summary>
    public static readonly TimeSpan CommandTimeoutCap = TimeSpan.FromSeconds(30);

    public static bool IsMember(string capability) => All.Contains(capability, StringComparer.Ordinal);
}

/// <summary>
/// M23_APP_FACTORY_SPEC.md §3 — the projects family (DEVICE_PROTOCOL.md §6l). The wire
/// contract with Cloud Core's App Factory (<c>app/appfactory/</c>); change the document first.
/// Every name is an operation the companion executes itself: source is written ONLY under the
/// Projects root, a run is the companion's own child in a Windows Job Object with the bounds
/// below, and the payload of <c>project.run</c> / <c>project.test</c> names a command KEY from
/// the manifest's allowlist — never a command line. There is no delete name.
/// </summary>
public static class ProjectCapabilityNames
{
    public const string ProjectScaffold = "project.scaffold";
    public const string ProjectRun = "project.run";
    public const string ProjectStatus = "project.status";
    public const string ProjectStop = "project.stop";
    public const string ProjectTest = "project.test";

    /// <summary>
    /// B33 requirements 456/457/468/469/471 (DEVICE_PROTOCOL.md §6l, appended): a built native
    /// application packaged (portable zip / MSIX through makeappx, unsigned), installed as a
    /// Start Menu shortcut to the built executable, uninstalled (shortcut and record removed,
    /// the build kept), and its artefact read back in bounded base64 chunks for a Cloud Core
    /// that cannot see this disk. Appended after <c>project.test</c> so the manifest reads as
    /// an addition.
    /// </summary>
    public const string ProjectPackage = "project.package";
    public const string ProjectInstall = "project.install";
    public const string ProjectUninstall = "project.uninstall";
    public const string ProjectArtifact = "project.artifact";

    /// <summary>Every projects name, in the order of the specification's table (the B33 four appended).</summary>
    public static readonly IReadOnlyList<string> All =
    [
        ProjectScaffold, ProjectRun, ProjectStatus, ProjectStop, ProjectTest,
        ProjectPackage, ProjectInstall, ProjectUninstall, ProjectArtifact,
    ];

    /// <summary>§2: a generated file set carries at most this many files.</summary>
    public const int MaxFiles = 200;

    /// <summary>§2: a generated file set's texts sum to at most this many UTF-8 bytes.</summary>
    public const long MaxTotalBytes = 2L * 1024 * 1024;

    /// <summary>§1: a slug is a plain name of at most this many characters (<c>[a-z0-9-]</c>).</summary>
    public const int MaxSlugChars = 64;

    /// <summary>§3: the job's committed-memory bound.</summary>
    public const long MemoryLimitBytes = 512L * 1024 * 1024;

    /// <summary>§3: the job's user-mode CPU-time bound — a static server or a test runner that burns this much is not the app that was asked for.</summary>
    public static readonly TimeSpan CpuTimeLimit = TimeSpan.FromMinutes(10);

    /// <summary>§3: the most processes one job may hold at once (a runtime plus what it forks; never a fork bomb).</summary>
    public const int MaxProcessesPerJob = 8;

    /// <summary>§3: a run's stdout/stderr log under the project root is cut at this size; the result says <c>truncated: true</c>.</summary>
    public const long MaxLogBytes = 1L * 1024 * 1024;

    /// <summary>§3: a run ends by itself after this long.</summary>
    public static readonly TimeSpan RunLifetime = TimeSpan.FromMinutes(30);

    /// <summary>§3: at most this many projects run at once.</summary>
    public const int MaxRunningProjects = 2;

    /// <summary>§3: <c>project.test</c> waits at most this long for the manifest's test command.</summary>
    public static readonly TimeSpan TestTimeout = TimeSpan.FromMinutes(5);

    /// <summary>§3: <c>project.run</c> waits at most this long for the manifest's port to answer before it calls the run a failure.</summary>
    public static readonly TimeSpan PortWait = TimeSpan.FromSeconds(20);

    /// <summary>§3: <c>project.stop</c> waits at most this long for the job's processes to be gone.</summary>
    public static readonly TimeSpan StopWait = TimeSpan.FromSeconds(5);

    /// <summary>The service's per-command cap for scaffold, status and stop — the operator family's 30 s.</summary>
    public static readonly TimeSpan CommandTimeoutCap = TimeSpan.FromSeconds(30);

    /// <summary>
    /// The service's cap for <c>project.test</c> alone. It was <see cref="TestTimeout"/> plus
    /// headroom; M28 raises it to the longest test bound there is, because a <c>dotnet test</c>
    /// under the native root is bounded by <see cref="NativeCapabilityNames.RunLimit"/> and not
    /// by the 5 minutes a node runner gets. A web project's test still answers within its own
    /// 5 min bound — the cap is a ceiling, not a wait — and the companion is what decides which
    /// bound applies, from the runtime the manifest named.
    /// </summary>
    public static readonly TimeSpan TestCommandTimeoutCap = NativeCapabilityNames.CommandTimeoutCap;

    /// <summary>
    /// M25: the service's cap for <c>project.run</c> alone. A web run still answers within
    /// <see cref="PortWait"/> — the cap is a ceiling, not a wait — but a 3D run is a BATCH run
    /// the companion waits out, so the ceiling has to be the longest runtime's bound plus
    /// headroom for the typed answer, or the service would synthesise a timeout while the tool
    /// was still working. M28's builds are batch runs too, and their 20 min bound is now the
    /// longest, so the ceiling is theirs.
    /// </summary>
    public static readonly TimeSpan RunCommandTimeoutCap = NativeCapabilityNames.CommandTimeoutCap;

    public static bool IsMember(string capability) => All.Contains(capability, StringComparer.Ordinal);
}

/// <summary>
/// M25_CREATIVE_3D_SPEC.md §3/§6/§7 and ADR-0088 decision 3 — the scenes family and the two
/// 3D runtimes the projects family's manifest allowlist gains. The wire contract with Cloud
/// Core's <c>app/creative3d</c>; change <c>packages/protocol/DEVICE_PROTOCOL.md</c> §6m first.
///
/// The owner's rule made structural: the assistant drives Blender and Unity through THEIR OWN
/// scripting interfaces, headless, as bounded children under the 3D root — never a window,
/// never the owner's own projects — and it reports only what the tool wrote back.
/// </summary>
public static class SceneCapabilityNames
{
    /// <summary>Return the driver's inspection (<c>out.json</c>) and the render it declares, for one 3D project.</summary>
    public const string Inspect = "scene.inspect";

    /// <summary>Every scenes name, in the order of the specification's table.</summary>
    public static readonly IReadOnlyList<string> All = [Inspect];

    /// <summary>§1: the folder under the Projects root that holds the 3D projects, and NOTHING the owner made.</summary>
    public const string Root3dFolderName = "3d";

    /// <summary>§3: the file a driver writes its read-back into, at the project root.</summary>
    public const string InspectionFileName = "out.json";

    /// <summary>§7: the inspection is bounded — a file larger than this is <c>postcondition_failed</c>, not parsed.</summary>
    public const long MaxInspectionBytes = 256L * 1024;

    /// <summary>§7: the render <c>scene.inspect</c> carries back as base64 is bounded too.</summary>
    public const long MaxRenderBytes = 512L * 1024;

    /// <summary>B44 (req 527): an exported scene file (GLB/FBX) is verified IN PLACE and never carried back — a scene file can be megabytes and the connection's frame is one — but it is hashed, so its size is bounded too.</summary>
    public const long MaxExportBytes = 64L * 1024 * 1024;

    /// <summary>B44: at most this many exports one inspection may declare (one per format).</summary>
    public const int MaxExports = 2;

    /// <summary>The first token of the Blender shape (resolved to the installed <c>blender.exe</c> — detected, never searched for on PATH).</summary>
    public const string BlenderProgram = "blender";

    /// <summary>The first token of the Unity shape (resolved to the Hub's installed editor).</summary>
    public const string UnityProgram = "unity";

    /// <summary>§3: the ONE method a Unity batch run may execute — the shipped driver's entry point.</summary>
    public const string UnityDriverMethod = "PagentOS.SceneDriver.Run";

    /// <summary>The extension the Blender shape's scene file must carry.</summary>
    /// <summary>M25: what stands in the scene-file position on the FIRST run, when there is
    /// no <c>.blend</c> to open yet. It also keeps the owner's own Blender preferences and
    /// startup file out of the run.</summary>
    public const string BlenderFactoryStart = "--factory-startup";

    public const string BlendExtension = ".blend";

    /// <summary>The extension the Blender shape's driver must carry (a fixed repository file, never model-authored).</summary>
    public const string DriverExtension = ".py";

    /// <summary>The extension the plan and the inspection must carry: the plan is DATA.</summary>
    public const string JsonExtension = ".json";

    /// <summary>The extension Unity's <c>-logFile</c> must carry.</summary>
    public const string LogExtension = ".log";

    /// <summary>§3: a Blender batch run is ended by its job after this long.</summary>
    public static readonly TimeSpan BlenderRunLimit = TimeSpan.FromMinutes(5);

    /// <summary>§3: a Blender job's committed-memory bound.</summary>
    public const long BlenderMemoryLimitBytes = 2L * 1024 * 1024 * 1024;

    /// <summary>§3: a Unity batch run is ended by its job after this long.</summary>
    public static readonly TimeSpan UnityRunLimit = TimeSpan.FromMinutes(10);

    /// <summary>§3: a Unity job's committed-memory bound (the editor is not small).</summary>
    public const long UnityMemoryLimitBytes = 4L * 1024 * 1024 * 1024;

    /// <summary>
    /// B50 (ADR-0164): the most processes one Unity job may hold, derived from this machine's
    /// processor count. Unity 6's build backend (Bee) and its shader compilers start a worker
    /// per core, so the fixed 32 that fits Blender refused the editor's own script compilation
    /// on a 28-thread machine: bee_backend could not start a process (GetLastError 1816,
    /// ERROR_NOT_ENOUGH_QUOTA) and the run ended "Scripts have compiler errors" - the first run
    /// after the owner's licence became valid (2026-09-16). A Windows player build (req 532) failed
    /// the same way at two per core, so four per core. Still a bounded cap, never a fork bomb.
    /// </summary>
    public static int UnityProcessesPerJob(int processorCount) => Math.Clamp((4 * processorCount) + 64, 128, 512);

    /// <summary>
    /// B50 (ADR-0164): the CPU-time bound of a Unity job, which - like a native build's - is NOT
    /// the wall-clock bound: the job's user time is the SUM over its parallel compilers, so a
    /// bound equal to <see cref="UnityRunLimit"/> would end an honest import on a many-core
    /// machine. The wall clock stays the real bound (the runner ends the job at UnityRunLimit).
    /// </summary>
    public static TimeSpan UnityCpuTimeLimitFor(int processorCount) => UnityRunLimit * Math.Clamp(processorCount, 1, 64);

    /// <summary>
    /// The exit code the Unity editor uses for "no licence" — measured on the owner's machine
    /// 2026-09-08 (<c>docs/evidence/m25-tool-detection-2026-09-08.json</c>, and again at
    /// 13:24Z). It is reported as <c>dependency_unavailable</c>, never <c>device_error</c>:
    /// the editor is installed and was started; it is the entitlement that is missing, and the
    /// Cloud Core must be able to say that in the owner's own words.
    /// </summary>
    public const int UnityNoLicenceExitCode = 198;

    /// <summary>The licensing client's own line, matched in Unity's log file (case-insensitively).</summary>
    public const string UnityNoLicenceMarker = "No valid Unity Editor license found";

    /// <summary>The detail a licence refusal carries.</summary>
    public const string UnityNoLicenceDetail = "unity_licence";

    /// <summary>The service's per-command cap for the family: reading a bounded file and a bounded PNG is the operator family's 30 s.</summary>
    public static readonly TimeSpan CommandTimeoutCap = TimeSpan.FromSeconds(30);

    public static bool IsMember(string capability) => All.Contains(capability, StringComparer.Ordinal);
}

/// <summary>
/// M28_NATIVE_APP_FACTORY_SPEC.md §5/§9 and ADR-0095 decision 4 — the native build toolchain.
/// Like M25, this adds NO capability name to the wire: a native build is a BATCH
/// <c>project.run</c> (and <c>project.test</c>) of a command the manifest allowlist admits,
/// under a third root the companion holds. The wire contract is
/// <c>packages/protocol/DEVICE_PROTOCOL.md</c> §6n; change the document first.
///
/// The owner's rule made structural: a compiler runs on this machine only under the
/// <c>native</c> root, only as one of FOUR argv shapes matched token for token, and never as
/// a command line anything composed. A native project cannot name its own compiler
/// invocation — which is the whole reason M23's manifest carries a KEY.
///
/// Nothing here signs anything (<see cref="ForbiddenPrograms"/>). The Cloud Core's
/// <c>app/nativefactory/packaging.py</c> says why in as many words: signing needs a
/// certificate, and the owner's real signing identity is theirs. An unsigned MSIX is the
/// honest state of a package nobody signed.
/// </summary>
public static class NativeCapabilityNames
{
    /// <summary>§5: the folder under the Projects root that holds the native projects — the ONLY place a compiler runs. It matches <c>app/nativefactory/roots.py</c>'s <c>NATIVE_SUBDIR</c>, and a test reads that file rather than restating the word.</summary>
    public const string RootNativeFolderName = "native";

    /// <summary>The first token of the three .NET shapes (resolved to the installed <c>dotnet.exe</c>).</summary>
    public const string DotnetProgram = "dotnet";

    /// <summary>The first token of the packaging shape (resolved to the Windows Kits' <c>makeappx.exe</c> — detected, never searched for on PATH).</summary>
    public const string MakeAppxProgram = "makeappx";

    /// <summary>The ONE configuration a native build may name. A Debug build is not a distributable application.</summary>
    public const string Configuration = "Release";

    /// <summary>The ONE runtime identifier <c>dotnet publish</c> may name.</summary>
    public const string RuntimeIdentifier = "win-x64";

    /// <summary>The extension the <c>&lt;project&gt;</c> token of the three .NET shapes must carry.</summary>
    public const string ProjectExtension = ".csproj";

    /// <summary>The extension the <c>/p</c> token of the packaging shape must carry.</summary>
    public const string PackageExtension = ".msix";

    /// <summary>
    /// §9: programs this device never runs, at any point, under any root. Signing is the
    /// first of them and the reason the list exists: an autonomous build does not reach for
    /// the owner's certificate store, and a "run-local test certificate" is still a
    /// certificate this code is not the thing that should be creating. They are refused by
    /// NAME at manifest-parse time, before any resolution, so widening a shape can never
    /// widen them in by accident.
    /// </summary>
    public static readonly IReadOnlyList<string> ForbiddenPrograms =
    [
        "signtool", "certutil", "certmgr", "makecert", "pvk2pfx", "certreq",
        // B49 (ADR-0161): the Java and Android signers and the key store tool. The Gradle shapes
        // never name them; this keeps a widened shape from ever reaching them.
        "apksigner", "jarsigner", "keytool",
    ];

    /// <summary>
    /// B49 (ADR-0161): the first token of the three Android shapes. It is a WORD, not a program:
    /// the device runs the configured JDK's <c>java.exe</c> on the configured Gradle
    /// distribution's launcher jar (<c>gradle.bat</c> is a batch file, and nothing here runs a
    /// shell), so no <c>gradle</c>, <c>gradlew</c> or <c>gradle.bat</c> is ever resolved.
    /// </summary>
    public const string GradleProgram = "gradle";

    /// <summary>The two flags every Gradle shape carries, in this order: no resident daemon outlives the job, and the log is plain text.</summary>
    public static readonly IReadOnlyList<string> GradleFlags = ["--no-daemon", "--console=plain"];

    /// <summary>The Gradle task a run command may name: the debug APK (installable, signed by the Android plugin with its own throwaway debug key) and the release bundle (unsigned).</summary>
    public static readonly IReadOnlyList<string> GradleRunTasks = ["assembleDebug", "bundleRelease"];

    /// <summary>The one Gradle task a test command may name: the project's JVM unit tests.</summary>
    public const string GradleTestTask = "test";

    /// <summary>§5: a native build is ended by its job after this long. It is the Cloud Core's own <c>BUILD_TIMEOUT_S</c>, and a test reads that file.</summary>
    public static readonly TimeSpan RunLimit = TimeSpan.FromMinutes(20);

    /// <summary>§5: a native build job's committed-memory bound (M25's Unity bound; a compiler with a Roslyn server and NuGet is not small).</summary>
    public const long MemoryLimitBytes = 4L * 1024 * 1024 * 1024;

    /// <summary>
    /// §5: the most processes one build job may hold. Higher than M25's 32 because MSBuild
    /// forks a node per core and adds VBCSCompiler, NuGet and the SDK's own resolvers — but
    /// still a fixed, small cap, never a fork bomb.
    /// </summary>
    public const int MaxProcessesPerJob = 64;

    /// <summary>
    /// The CPU-time bound for a build job, which — unlike every other job in this agent — is
    /// NOT the wall-clock bound. <c>JOB_OBJECT_LIMIT_JOB_TIME</c> terminates the whole job when
    /// the SUM of its processes' user time passes the limit, and MSBuild compiles in parallel:
    /// on an eight-core machine a perfectly legitimate fifteen-minute build burns two hours of
    /// user time. Setting the CPU bound to the wall bound would therefore kill honest builds
    /// and call it a limit. The wall clock is the real bound here (the runner ends the job at
    /// <see cref="RunLimit"/>), and the CPU bound is what that wall clock could legitimately
    /// consume: the limit times the processors this machine actually has.
    /// </summary>
    public static TimeSpan CpuTimeLimitFor(int processorCount) => RunLimit * Math.Clamp(processorCount, 1, 64);

    /// <summary>The service's cap for a command that may carry a build: the bound plus headroom for the typed answer.</summary>
    public static readonly TimeSpan CommandTimeoutCap = RunLimit + TimeSpan.FromSeconds(30);

    /// <summary>Whether <paramref name="program"/> is one this device never runs (case-insensitively, with or without <c>.exe</c>).</summary>
    public static bool IsForbiddenProgram(string program)
    {
        var stem = program.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) ? program[..^4] : program;
        return ForbiddenPrograms.Contains(stem, StringComparer.OrdinalIgnoreCase);
    }
}

/// <summary>
/// BROWSER_CAPABILITIES.md §1 — the names are the wire contract with Cloud Core and with the
/// Browser Worker; change the document first. <see cref="Family"/> is a marker, not an
/// operation: it says "this device has a configured worker", and is never executed.
/// </summary>
public static class BrowserCapabilities
{
    public const string Prefix = "browser.";

    public const string Family = "browser.chrome";

    public const string SessionOpen = "browser.session_open";
    public const string SessionClose = "browser.session_close";
    public const string WorkerStatus = "browser.worker_status";
    public const string Navigate = "browser.navigate";
    public const string Back = "browser.back";
    public const string Forward = "browser.forward";
    public const string TabList = "browser.tab_list";
    public const string TabNew = "browser.tab_new";
    public const string TabClose = "browser.tab_close";
    public const string TabSelect = "browser.tab_select";
    public const string Inspect = "browser.inspect";
    public const string Find = "browser.find";
    public const string Click = "browser.click";
    public const string Fill = "browser.fill";
    public const string SelectOption = "browser.select_option";
    public const string SetChecked = "browser.set_checked";
    public const string Scroll = "browser.scroll";
    public const string Wait = "browser.wait";
    public const string Extract = "browser.extract";
    public const string Snapshot = "browser.snapshot";
    public const string Screenshot = "browser.screenshot";
    public const string Download = "browser.download";
    /// <summary>B31 requirement 181 (contract v1.5): the operation behind the worker's <c>uploads</c> flag.</summary>
    public const string Upload = "browser.upload";
    public const string Search = "browser.search";
    public const string FetchEvidence = "browser.fetch_evidence";

    // BROWSER_CAPABILITIES.md §3b (contract v1.2, M18.3): the alarm media family. The worker
    // plays the owner's named wake music in its own dedicated `alarm` profile; the host's
    // only job here is to let these four names through the allowlist below and to apply the
    // same result cap and forbidden-key scan it applies to every other browser result.
    public const string MediaPlay = "browser.media_play";
    public const string MediaVolume = "browser.media_volume";
    public const string MediaStatus = "browser.media_status";
    public const string MediaStop = "browser.media_stop";

    /// <summary>Every per-operation name, in the order of BROWSER_CAPABILITIES.md §1.</summary>
    public static readonly IReadOnlyList<string> Operations =
    [
        SessionOpen, SessionClose, WorkerStatus,
        Navigate, Back, Forward,
        TabList, TabNew, TabClose, TabSelect,
        Inspect, Find, Click, Fill, SelectOption, SetChecked, Scroll, Wait,
        Extract, Snapshot, Screenshot, Download, Upload, Search, FetchEvidence,
        MediaPlay, MediaVolume, MediaStatus, MediaStop,
    ];

    /// <summary>Family marker first, then the operations — what the manifest carries.</summary>
    public static readonly IReadOnlyList<string> All = [Family, .. Operations];

    /// <summary>§3: every worker result is a JSON object of at most this many UTF-8 bytes; the worker truncates, never the host.</summary>
    public const int MaxResultBytes = 48 * 1024;

    /// <summary>§3: the service raises its per-command cap to this for the browser family.</summary>
    public static readonly TimeSpan CommandTimeoutCap = TimeSpan.FromSeconds(120);

    /// <summary>
    /// §6(b): a result carrying any key whose NORMALISED name (see <see cref="NormalizeKey"/>)
    /// contains one of these, at any depth, is refused with <c>security_scope_error</c>
    /// before it leaves the companion. Session material never crosses the pipe, whatever
    /// the worker did.
    ///
    /// The fragments are stored already normalised (the document's <c>set-cookie</c> is
    /// <c>setcookie</c> here) so the list and the rule agree by construction: a raw
    /// substring match against the document's spelling lets <c>api-key</c>, <c>api_key</c>
    /// and <c>Set_Cookie</c> through while the Browser Worker (<c>_FORBIDDEN_KEY_TOKENS</c>
    /// in <c>browser_agent/worker.py</c>) refuses them — the two sides must agree, and the
    /// companion is the higher-assurance one.
    /// </summary>
    public static readonly IReadOnlyList<string> ForbiddenResultKeyFragments =
    [
        "cookie", "authorization", "setcookie", "localstorage", "sessionstorage",
        "password", "token", "secret", "apikey",
    ];

    /// <summary>
    /// The worker's <c>_normalize_key</c>, verbatim: lower-case, then drop every character
    /// that is not a letter or a digit. <c>x-Api-Key</c>, <c>api_key</c> and <c>APIKEY</c>
    /// all become <c>apikey</c>.
    /// </summary>
    public static string NormalizeKey(string key)
    {
        var lowered = key.ToLowerInvariant();
        var builder = new System.Text.StringBuilder(lowered.Length);
        foreach (var ch in lowered)
        {
            if (char.IsLetterOrDigit(ch))
            {
                builder.Append(ch);
            }
        }

        return builder.ToString();
    }

    /// <summary>
    /// The shared forbidden-key rule: true when the normalised key contains any fragment.
    /// It is a SUBSTRING rule by contract, so <c>tokens_count</c> is forbidden (it contains
    /// <c>token</c>) while <c>text_chars</c> and <c>links_count</c> are not; the worker's
    /// result vocabulary (§3) is chosen to stay clear of the fragments.
    /// </summary>
    public static bool IsForbiddenKey(string key)
    {
        var normalized = NormalizeKey(key);
        foreach (var fragment in ForbiddenResultKeyFragments)
        {
            if (normalized.Contains(fragment, StringComparison.Ordinal))
            {
                return true;
            }
        }

        return false;
    }

    public static bool IsFamilyMember(string capability)
        => capability.StartsWith(Prefix, StringComparison.Ordinal);

    public static bool IsOperation(string capability)
        => Operations.Contains(capability, StringComparer.Ordinal);
}

public static class ErrorClasses
{
    public const string ValidationError = "validation_error";
    public const string AuthError = "auth_error";
    public const string DeviceOffline = "device_offline";
    public const string CapabilityMissing = "capability_missing";
    public const string DependencyUnavailable = "dependency_unavailable";
    public const string ProviderRateLimited = "provider_rate_limited";
    public const string ProviderError = "provider_error";
    public const string UiTargetNotFound = "ui_target_not_found";
    public const string UiStateChanged = "ui_state_changed";
    public const string Timeout = "timeout";
    public const string CommandExpired = "command_expired";
    public const string Cancelled = "cancelled";
    public const string RetryExhausted = "retry_exhausted";
    public const string ArtifactRenderError = "artifact_render_error";
    public const string VoiceProviderError = "voice_provider_error";
    public const string SecurityScopeError = "security_scope_error";
    public const string InternalBug = "internal_bug";

    /// <summary>
    /// M13 (2026-09-03 incident): the Browser Worker found, or left, the PagentOS profile's
    /// Chrome outside its own lifecycle — an orphan holding the profile lock, a launch that
    /// landed in it. Never retryable: retrying a launch on a locked profile is exactly what
    /// cascaded windows across the owner's desktop.
    /// </summary>
    public const string BrowserLifecycleViolation = "browser_lifecycle_violation";

    /// <summary>
    /// M19 (M19_DIGITAL_OPERATOR_SPEC.md §1, invariant 2): the window in front at the moment
    /// of acting is not the window the plan observed. Retryable — the planner re-resolves —
    /// and never a retry into whatever is in front; the message names both windows.
    /// </summary>
    public const string FocusMismatch = "focus_mismatch";

    /// <summary>
    /// M19: the request is outside what the owner authorised on this device — a terminal
    /// command that is not allowlisted, a path outside the authorised roots. Never retryable
    /// and answered before any process exists.
    /// </summary>
    public const string PermissionDenied = "permission_denied";

    /// <summary>
    /// M19: the action was performed but the re-observed world does not show the requested
    /// state (a window asked to maximise that is still normal). The result is a truthful
    /// failure rather than a success inferred from the call having returned.
    /// </summary>
    public const string PostconditionFailed = "postcondition_failed";

    /// <summary>
    /// M20 (M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §2): the device cannot parse the file as
    /// the kind its name says — a corrupt OOXML package, an encrypted PDF, a binary asked
    /// for as text, a file over the size bound. Never retryable, and the message names the
    /// reason; a format the device cannot read is never an empty success.
    /// </summary>
    public const string UnsupportedFormat = "unsupported_format";

    /// <summary>
    /// M20: the file the request names is not there — a <c>file_id</c> this companion never
    /// issued (or issued for a file since deleted), a path inside the roots that does not
    /// exist. Never retryable by itself; the planner searches again. Distinct from
    /// <c>permission_denied</c>, which is what a path OUTSIDE the roots gets whether or not
    /// it exists, so an answer never reveals anything about the outside.
    /// </summary>
    public const string NotFound = "not_found";

    public static readonly IReadOnlySet<string> All = new HashSet<string>(StringComparer.Ordinal)
    {
        ValidationError, AuthError, DeviceOffline, CapabilityMissing, DependencyUnavailable,
        ProviderRateLimited, ProviderError, UiTargetNotFound, UiStateChanged, Timeout,
        CommandExpired, Cancelled, RetryExhausted, ArtifactRenderError, VoiceProviderError,
        SecurityScopeError, InternalBug, BrowserLifecycleViolation,
        FocusMismatch, PermissionDenied, PostconditionFailed,
        UnsupportedFormat, NotFound,
    };
}

public static class ErrorObjects
{
    /// <summary>Builds an ErrorObject, truncating the message to the schema limit.</summary>
    public static ErrorObject Create(string errorClass, string message, bool retryable)
    {
        if (message.Length > ProtocolConstants.MaxErrorMessageLength)
        {
            message = message[..ProtocolConstants.MaxErrorMessageLength];
        }

        return new ErrorObject { Class = errorClass, Message = message, Retryable = retryable };
    }
}
