using System.Globalization;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion;

/// <summary>
/// The display family (DEVICE_PROTOCOL.md §6e): <c>desktop.display_wake</c>,
/// <c>desktop.display_status</c> and <c>desktop.display_off</c>. Waking and reporting are
/// always available; darkening is the one operation that stays behind a flag.
///
/// <para><b>The boundary is the whole design.</b> M18 does not stop, restart or send the
/// machine to sleep on any inference (M18_HOLOGRAPHIC_CORE_SPEC.md §4, M18_THREAT_MODEL.md
/// §5). A dark screen is undone by moving a mouse; a suspended machine that was mid-research
/// is not, and the background work would stop with it. So this family has exactly three
/// operations, each names the one thing it does, and
/// <c>AmbientCapabilityTests.The_display_capability_can_only_turn_a_display_off_never_suspend_the_machine</c>
/// reads every display source file in this project to assert that no power-state, session-end
/// or keyboard API name appears in any of them. The class cannot grow a second meaning without
/// a test going red.</para>
///
/// <para><b>Two gates before a screen goes dark.</b> Cloud Core refuses to reach display-off
/// from a routine until it has passed its own owner qualification
/// (<c>app.routines.dispatch</c>), and this device refuses it unless the companion was started
/// with <c>PAGENTOS_AGENT_DisplayPowerEnabled=true</c>. Neither gate knows about the other, on
/// purpose. A companion with the flag off does not advertise the name either, so Cloud Core
/// sees <c>no_capable_device</c> rather than a device that lied.</para>
///
/// <para><b>And a third gate that is not a flag (M18.3).</b> Even fully enabled, display-off
/// REFUSES — as a successful result, not an error — when the owner has touched the machine
/// inside the holdoff window, or while an alarm is ringing. This is the difference between a
/// routine that decides the owner is asleep and a routine that can prove nobody minds: a
/// screen darkening two seconds after a keystroke is the exact failure this milestone spent
/// its caution budget on. The refusal is a success because nothing went wrong — the device
/// looked, and the answer was no.</para>
/// </summary>
public sealed class DisplayPowerController
{
    /// <summary>How recently the owner may have touched the machine and still have display-off refused.</summary>
    public const int DefaultHoldoffSeconds = 120;

    public const int MinHoldoffSeconds = 0;

    public const int MaxHoldoffSeconds = 3600;

    /// <summary>The owner touched something inside the holdoff window.</summary>
    public const string RefusedRecentInput = "recent_input";

    /// <summary>An alarm is ringing; whatever else is true, this is not the moment to go dark.</summary>
    public const string RefusedAlarmActive = "alarm_active";

    private readonly IDisplayPower _display;
    private readonly ILogger _logger;
    private readonly AuditLog? _audit;
    private readonly IInputActivitySource _input;
    private readonly IDisplayStateObserver _observer;
    private readonly IMonitorInventory _monitors;
    private readonly Func<bool> _isAlarmRinging;
    private readonly IDisplayWake? _wake;

    public DisplayPowerController(
        IDisplayPower display,
        ILogger logger,
        bool enabled,
        AuditLog? audit = null,
        IInputActivitySource? input = null,
        IDisplayStateObserver? observer = null,
        IMonitorInventory? monitors = null,
        Func<bool>? isAlarmRinging = null,
        IDisplayWake? wake = null)
    {
        _display = display;
        _logger = logger;
        _audit = audit;
        _input = input ?? UnknownInputActivitySource.Instance;
        _observer = observer ?? UnknownDisplayStateObserver.Instance;
        _monitors = monitors ?? EmptyMonitorInventory.Instance;
        _isAlarmRinging = isAlarmRinging ?? (static () => false);
        _wake = wake;
        Enabled = enabled;
    }

    /// <summary>Whether this companion may turn the display OFF at all (config <c>DisplayPowerEnabled</c>).</summary>
    public bool Enabled { get; }

    /// <summary>
    /// <c>desktop.display_wake</c>. Payload: <c>{"reason": "&lt;short token&gt;"?}</c>. Result:
    /// <c>{"woken": true, "method": "display_needed+pointer_move_zero", "observed_state": "…",
    /// "observed_at": "…"|null, "input_idle_s": &lt;number&gt;|null}</c>.
    ///
    /// <para>Unconditional: no flag guards it, because waking a screen takes nothing away from
    /// the owner and is the direct inverse of the one operation that does. The two steps run in
    /// a fixed order and neither of them is a key event.</para>
    /// </summary>
    public JsonObject Wake(JsonObject payload)
    {
        ArgumentNullException.ThrowIfNull(payload);
        var reason = payload["reason"]?.GetValue<string>();

        if (_wake is null)
        {
            throw new CapabilityException(
                ErrorClasses.CapabilityMissing,
                "this companion has no way to wake a display in the owner's session",
                retryable: false);
        }

        try
        {
            _wake.RequestDisplayNeededOnce();
            _wake.NudgePointer();
        }
        catch (Exception ex)
        {
            _audit?.Write(
                "display_wake",
                capability: AgentCapabilities.DesktopDisplayWake,
                status: AckStatus.Failed,
                detail: $"reason={reason ?? "-"}; error={ex.GetType().Name}: {ex.Message}");
            throw new CapabilityException(
                ErrorClasses.DependencyUnavailable,
                $"the session did not accept the display-wake request: {ex.Message}",
                retryable: true);
        }

        _logger.LogInformation("display woken (reason={Reason})", reason ?? "-");
        _audit?.Write(
            "display_wake",
            capability: AgentCapabilities.DesktopDisplayWake,
            status: AckStatus.Succeeded,
            detail: $"reason={reason ?? "-"}; method=display_needed+pointer_move_zero");

        var result = new JsonObject
        {
            ["woken"] = true,
            ["method"] = "display_needed+pointer_move_zero",
        };
        AddObservation(result);
        result["input_idle_s"] = IdleSeconds();
        return result;
    }

    /// <summary>
    /// <c>desktop.display_status</c>. Payload: <c>{}</c>. Result:
    /// <c>{"observed_state": "unknown"|"off"|"on"|"dimmed", "observed_at": "…"|null,
    /// "input_idle_s": &lt;number&gt;|null, "monitors": [{…}]?}</c>.
    ///
    /// <para>Reports what it was TOLD, never what it inferred. Before the first notification the
    /// state is <c>unknown</c>, and that is the honest answer — a device that guessed "on"
    /// because someone typed recently would be reporting the input timer twice under two
    /// different names.</para>
    /// </summary>
    public JsonObject Status(JsonObject payload)
    {
        ArgumentNullException.ThrowIfNull(payload);
        var result = new JsonObject();
        AddObservation(result);
        result["input_idle_s"] = IdleSeconds();
        AddMonitors(result);
        return result;
    }

    /// <summary>
    /// <c>desktop.display_off</c>. Payload:
    /// <c>{"reason": "&lt;short token&gt;"?, "holdoff_s": &lt;int&gt;?}</c> (holdoff default 120 s,
    /// clamped to 0…3600).
    ///
    /// <para>Result on a refusal — a SUCCESSFUL result, because nothing failed:
    /// <c>{"display_off": false, "refused": "recent_input"|"alarm_active", "input_idle_s": …,
    /// "holdoff_s": …, "observed_state": …, "observed_at": …}</c>.</para>
    ///
    /// <para>Result on a real off: <c>{"display_off": true,
    /// "method": "wm_syscommand_monitorpower", "input_idle_s": …, "holdoff_s": …,
    /// "observed_state": …, "observed_at": …, "monitors": [{…}]?}</c>, where the observed state
    /// is read back AFTER the broadcast, so the caller sees what the machine reported rather
    /// than what this code intended.</para>
    /// </summary>
    public JsonObject TurnOff(JsonObject payload)
    {
        ArgumentNullException.ThrowIfNull(payload);
        var reason = payload["reason"]?.GetValue<string>();
        var holdoff = ParseHoldoff(payload["holdoff_s"]);

        if (!Enabled)
        {
            _ = _audit?.WriteRefusal(reason);
            throw new CapabilityException(
                ErrorClasses.CapabilityMissing,
                "display power is not enabled on this companion "
                + "(PAGENTOS_AGENT_DisplayPowerEnabled=true, after the owner qualification for display-off)",
                retryable: false);
        }

        // The alarm is checked first: whatever else is true about idle time, a machine that is
        // in the middle of waking somebody up must not darken its own screen.
        var idle = _input.IdleTime;
        if (_isAlarmRinging())
        {
            return Refusal(RefusedAlarmActive, idle, holdoff, reason);
        }

        if (idle is not null && idle.Value.TotalSeconds < holdoff)
        {
            return Refusal(RefusedRecentInput, idle, holdoff, reason);
        }

        try
        {
            _display.TurnOff();
        }
        catch (Exception ex)
        {
            _audit?.Write(
                "display_off",
                capability: AgentCapabilities.DesktopDisplayOff,
                status: AckStatus.Failed,
                detail: $"reason={reason ?? "-"}; error={ex.GetType().Name}: {ex.Message}");
            throw new CapabilityException(
                ErrorClasses.DependencyUnavailable,
                $"the display did not accept the power-off request: {ex.Message}",
                retryable: true);
        }

        _logger.LogInformation("display turned off (reason={Reason}, holdoff={Holdoff}s)", reason ?? "-", holdoff);
        _audit?.Write(
            "display_off",
            capability: AgentCapabilities.DesktopDisplayOff,
            status: AckStatus.Succeeded,
            detail: $"reason={reason ?? "-"}; holdoff_s={holdoff}; method=wm_syscommand_monitorpower");

        var result = new JsonObject
        {
            ["display_off"] = true,
            ["method"] = "wm_syscommand_monitorpower",
            ["input_idle_s"] = ToSeconds(idle),
            ["holdoff_s"] = holdoff,
        };

        // Read back, do not assume: the observer's value after the broadcast is the machine's
        // own report. It may still be "unknown" on a device that was never told, and saying so
        // is better than claiming an "off" nothing confirmed.
        AddObservation(result);
        AddMonitors(result);
        return result;
    }

    // ---------------------------------------------------------------- internals

    private JsonObject Refusal(string refused, TimeSpan? idle, int holdoff, string? reason)
    {
        _logger.LogInformation(
            "display_off refused ({Refused}): idle={Idle} holdoff={Holdoff}s reason={Reason}",
            refused,
            idle?.TotalSeconds.ToString("0.#", CultureInfo.InvariantCulture) ?? "unknown",
            holdoff,
            reason ?? "-");
        _audit?.Write(
            "display_off",
            capability: AgentCapabilities.DesktopDisplayOff,
            status: AckStatus.Succeeded,
            detail: $"reason={reason ?? "-"}; refused={refused}; holdoff_s={holdoff}; "
                + $"input_idle_s={idle?.TotalSeconds.ToString("0.#", CultureInfo.InvariantCulture) ?? "unknown"}");

        var result = new JsonObject
        {
            ["display_off"] = false,
            ["refused"] = refused,
            ["input_idle_s"] = ToSeconds(idle),
            ["holdoff_s"] = holdoff,
        };
        AddObservation(result);
        return result;
    }

    private void AddObservation(JsonObject result)
    {
        var observation = _observer.Current;
        result["observed_state"] = observation.State;
        result["observed_at"] = observation.ObservedAt is null
            ? null
            : JsonValue.Create(observation.ObservedAt.Value.ToString("O", CultureInfo.InvariantCulture));
    }

    private void AddMonitors(JsonObject result)
    {
        var monitors = _monitors.List();
        if (monitors.Count == 0)
        {
            // Absent, not empty: "this session could not enumerate its monitors" is a
            // different claim from "this machine has no monitors".
            return;
        }

        var array = new JsonArray();
        foreach (var monitor in monitors)
        {
            array.Add(new JsonObject
            {
                ["index"] = monitor.Index,
                ["primary"] = monitor.Primary,
                ["left"] = monitor.Left,
                ["top"] = monitor.Top,
                ["width"] = monitor.Width,
                ["height"] = monitor.Height,
            });
        }

        result["monitors"] = array;
    }

    private JsonNode? IdleSeconds() => ToSeconds(_input.IdleTime);

    private static JsonNode? ToSeconds(TimeSpan? idle)
        => idle is null ? null : JsonValue.Create(Math.Round(idle.Value.TotalSeconds, 1));

    private static int ParseHoldoff(JsonNode? node)
    {
        if (node is null)
        {
            return DefaultHoldoffSeconds;
        }

        if (node.GetValueKind() != JsonValueKind.Number
            || !double.TryParse(node.ToJsonString(), NumberStyles.Float, CultureInfo.InvariantCulture, out var value)
            || value != Math.Floor(value))
        {
            throw new CapabilityException(
                ErrorClasses.ValidationError,
                "payload.holdoff_s must be a whole number of seconds",
                retryable: false);
        }

        return (int)Math.Clamp(value, MinHoldoffSeconds, MaxHoldoffSeconds);
    }
}

/// <summary>
/// The one call the OFF operation makes, behind an interface so tests assert that it happened
/// without a monitor going dark on the machine running them.
/// </summary>
public interface IDisplayPower
{
    /// <summary>Puts the display into power-off. Reversible by any input; affects nothing else.</summary>
    void TurnOff();
}

/// <summary>
/// Production implementation: broadcast <c>WM_SYSCOMMAND</c> / <c>SC_MONITORPOWER</c> with
/// the "power off" parameter, which is what the shell itself sends when a power plan blanks
/// the screen. It runs in the owner's interactive session (the companion), because a message
/// broadcast from Session 0 reaches no window the owner can see.
///
/// <para><c>SendMessageTimeout</c>, not <c>SendMessage</c>: a single hung top-level window
/// would otherwise stall this thread forever, and a stuck alarm/display path is exactly the
/// kind of quiet failure the companion must not have. The timeout is short and a timed-out
/// broadcast is reported as a failure, never as success.</para>
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class Win32DisplayPower : IDisplayPower
{
    private const int WmSyscommand = 0x0112;
    private const int ScMonitorPower = 0xF170;

    /// <summary>The <c>SC_MONITORPOWER</c> parameter for "off". 1 is low power, -1 is on; only 2 is ever sent.</summary>
    private const int MonitorOff = 2;

    private const uint SmtoAbortIfHung = 0x0002;
    private const uint BroadcastTimeoutMs = 2000;

    private static readonly IntPtr HwndBroadcast = new(0xFFFF);

    public void TurnOff()
    {
        var result = SendMessageTimeout(
            HwndBroadcast,
            WmSyscommand,
            ScMonitorPower,
            MonitorOff,
            SmtoAbortIfHung,
            BroadcastTimeoutMs,
            out _);

        if (result == IntPtr.Zero)
        {
            var error = Marshal.GetLastWin32Error();
            throw new InvalidOperationException(
                $"SendMessageTimeout(SC_MONITORPOWER, off) failed or timed out (win32={error})");
        }
    }

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern IntPtr SendMessageTimeout(
        IntPtr hWnd,
        int msg,
        int wParam,
        int lParam,
        uint flags,
        uint timeoutMs,
        out IntPtr result);
}

internal static class DisplayAuditExtensions
{
    /// <summary>A refused display-off is audited too: a capability the owner has not qualified
    /// being asked for is worth seeing in the record, not only in a log line.</summary>
    public static bool WriteRefusal(this AuditLog audit, string? reason)
    {
        audit.Write(
            "display_off",
            capability: AgentCapabilities.DesktopDisplayOff,
            status: AckStatus.Failed,
            detail: $"reason={reason ?? "-"}; refused=display_power_not_enabled");
        return true;
    }
}
