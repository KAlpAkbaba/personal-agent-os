using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion;

/// <summary>
/// Executes <c>desktop.display_off</c> (DEVICE_PROTOCOL.md §6d, M18): turns the owner's
/// display off, and nothing else.
///
/// <para><b>The boundary is the whole design.</b> M18 v1 does not shut down, reboot, hibernate
/// or suspend the machine on any inference (M18_HOLOGRAPHIC_CORE_SPEC.md §4, M18_THREAT_MODEL.md
/// §5). Turning a display off is undone by moving the mouse; suspending a machine that is
/// mid-research is not, and the background work would stop with it. So this capability has
/// exactly one operation, it names the one thing it does, and
/// <c>AmbientCapabilityTests.The_display_capability_can_only_turn_a_display_off_never_suspend_the_machine</c>
/// reads this source file to assert that no shutdown, reboot,
/// hibernate, suspend or logoff API name appears in it — the class cannot grow a second
/// meaning without a test going red.</para>
///
/// <para><b>Two gates before it will run at all.</b> Cloud Core refuses to reach it from a
/// routine until display-off has passed its own owner qualification
/// (<c>app.routines.dispatch</c>), and this device refuses it unless the companion was started
/// with <c>PAGENTOS_AGENT_DisplayPowerEnabled=true</c>. Neither gate knows about the other, on
/// purpose: a wrong sleep inference interrupting unrelated owner work is the failure this
/// milestone deliberately declines to risk, and one flag flipped by accident should not be
/// enough to cause it. A companion with the flag off does not advertise the name either, so
/// Cloud Core sees <c>no_capable_device</c> rather than a device that lied.</para>
/// </summary>
public sealed class DisplayPowerController(
    IDisplayPower display,
    ILogger logger,
    bool enabled,
    AuditLog? audit = null)
{
    /// <summary>Whether this companion may turn the display off at all (config <c>DisplayPowerEnabled</c>).</summary>
    public bool Enabled { get; } = enabled;

    /// <summary>
    /// Payload: <c>{"reason": "&lt;short token&gt;"?}</c>. Result:
    /// <c>{"display_off": true, "method": "wm_syscommand_monitorpower"}</c>.
    /// </summary>
    public JsonObject TurnOff(JsonObject payload)
    {
        ArgumentNullException.ThrowIfNull(payload);
        var reason = payload["reason"]?.GetValue<string>();

        if (!Enabled)
        {
            _ = audit?.WriteRefusal(reason);
            throw new CapabilityException(
                ErrorClasses.CapabilityMissing,
                "display power is not enabled on this companion "
                + "(PAGENTOS_AGENT_DisplayPowerEnabled=true, after the owner qualification for display-off)",
                retryable: false);
        }

        try
        {
            display.TurnOff();
        }
        catch (Exception ex)
        {
            audit?.Write(
                "display_off",
                capability: AgentCapabilities.DesktopDisplayOff,
                status: AckStatus.Failed,
                detail: $"reason={reason ?? "-"}; error={ex.GetType().Name}: {ex.Message}");
            throw new CapabilityException(
                ErrorClasses.DependencyUnavailable,
                $"the display did not accept the power-off request: {ex.Message}",
                retryable: true);
        }

        logger.LogInformation("display turned off (reason={Reason})", reason ?? "-");
        audit?.Write(
            "display_off",
            capability: AgentCapabilities.DesktopDisplayOff,
            status: AckStatus.Succeeded,
            detail: $"reason={reason ?? "-"}; method=wm_syscommand_monitorpower");

        return new JsonObject
        {
            ["display_off"] = true,
            ["method"] = "wm_syscommand_monitorpower",
        };
    }
}

/// <summary>
/// The one call this capability makes, behind an interface so tests assert that it happened
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
/// would otherwise block this thread forever, and a stuck alarm/display path is exactly the
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
