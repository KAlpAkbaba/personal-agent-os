namespace PagentOS.Agent.Core.Protocol;

public static class ProtocolConstants
{
    public const int Version = 1;

    /// <summary>Maximum length of ErrorObject.message per schema.</summary>
    public const int MaxErrorMessageLength = 2000;
}

public static class AgentInfo
{
    public const string SoftwareVersion = "0.1.0";
    public const string Platform = "windows";
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

    /// <summary>M18: turn the display off — the ONLY machine-state action, and only off (§6d).</summary>
    public const string DesktopDisplayOff = "desktop.display_off";

    /// <summary>The desktop family — what every device advertises (M1/M3 behaviour, unchanged).</summary>
    public static readonly IReadOnlyList<string> Desktop = [DesktopOpenApplication, DesktopOpenArtifact];

    /// <summary>The alarm pair (M18). Always advertised; see the class docstring for why.</summary>
    public static readonly IReadOnlyList<string> Alarm = [DesktopAlarmStart, DesktopAlarmStop];

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
    /// The manifest this device actually advertises: the desktop names and the alarm pair
    /// always, display power and the browser family only when each is configured. Order is
    /// stable (desktop, alarm, display, browser) so a manifest diff between two versions
    /// reads as an addition rather than a reshuffle.
    /// </summary>
    public static IReadOnlyList<string> Compose(bool browserEnabled, bool displayPowerEnabled = false)
    {
        var names = new List<string>(Desktop.Count + Alarm.Count + DisplayPower.Count + BrowserCapabilities.All.Count);
        names.AddRange(Desktop);
        names.AddRange(Alarm);
        if (displayPowerEnabled)
        {
            names.AddRange(DisplayPower);
        }

        if (browserEnabled)
        {
            names.AddRange(BrowserCapabilities.All);
        }

        return names;
    }

    public static bool IsDesktop(string capability) => Desktop.Contains(capability, StringComparer.Ordinal);

    public static bool IsAlarm(string capability) => Alarm.Contains(capability, StringComparer.Ordinal);

    public static bool IsDisplayPower(string capability) => DisplayPower.Contains(capability, StringComparer.Ordinal);

    /// <summary>
    /// Every name the Session Companion executes in the owner's interactive session. The
    /// Device Service routes exactly this set over the pipe and refuses everything else
    /// outside the browser family, so a new interactive name is reachable only by being
    /// added here — never by being spelled <c>desktop.</c>-something.
    /// </summary>
    public static bool IsInteractive(string capability)
        => IsDesktop(capability) || IsAlarm(capability) || IsDisplayPower(capability);

    public static bool IsBrowser(string capability) => BrowserCapabilities.IsFamilyMember(capability);
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
    public const string Search = "browser.search";
    public const string FetchEvidence = "browser.fetch_evidence";

    /// <summary>Every per-operation name, in the order of BROWSER_CAPABILITIES.md §1.</summary>
    public static readonly IReadOnlyList<string> Operations =
    [
        SessionOpen, SessionClose, WorkerStatus,
        Navigate, Back, Forward,
        TabList, TabNew, TabClose, TabSelect,
        Inspect, Find, Click, Fill, SelectOption, SetChecked, Scroll, Wait,
        Extract, Snapshot, Screenshot, Download, Search, FetchEvidence,
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

    public static readonly IReadOnlySet<string> All = new HashSet<string>(StringComparer.Ordinal)
    {
        ValidationError, AuthError, DeviceOffline, CapabilityMissing, DependencyUnavailable,
        ProviderRateLimited, ProviderError, UiTargetNotFound, UiStateChanged, Timeout,
        CommandExpired, Cancelled, RetryExhausted, ArtifactRenderError, VoiceProviderError,
        SecurityScopeError, InternalBug, BrowserLifecycleViolation,
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
