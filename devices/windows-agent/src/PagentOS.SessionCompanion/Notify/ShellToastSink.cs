using System.Runtime.Versioning;
using System.Windows.Forms;
using Microsoft.Extensions.Logging;

namespace PagentOS.SessionCompanion.Notify;

/// <summary>
/// The FALLBACK sink since B11-toast: a tray balloon, used by <see cref="WindowsToastSink"/>
/// only when the Windows toast platform cannot be used.
/// </summary>
/// <remarks>
/// <para>
/// Windows 10 and 11 route a tray balloon through the notification system, so it appears as
/// a toast and stays in the Action Center afterwards. The owner does not need to be looking
/// at anything, and the notice is still there when they come back.
/// </para>
/// <para>
/// <strong>What this sink cannot do, stated rather than implied:</strong> action buttons
/// (requirement 370). A balloon has no buttons, so it answers <c>surface: "balloon"</c>,
/// <c>actions_rendered: 0</c> and, when the request had buttons,
/// <c>detail: "actions_not_rendered"</c>. It is also unable to report failure:
/// <c>ShowBalloonTip</c> returns nothing, which is why it is the fallback and not the
/// surface.
/// </para>
/// <para>
/// No interactive session is not a failure to report: it is the honest answer, and it is
/// exactly when the Cloud Core should step down its ladder to push or the inbox.
/// </para>
/// </remarks>
[SupportedOSPlatform("windows")]
public sealed class ShellToastSink : IToastSink, IDisposable
{
    private readonly ILogger _logger;
    private readonly object _gate = new();
    private NotifyIcon? _icon;

    public ShellToastSink(ILogger logger)
    {
        _logger = logger;
    }

    public ToastOutcome Show(ToastRequest request)
    {
        if (!Environment.UserInteractive)
        {
            return ToastOutcome.NotShown(NotifyCapabilities.ReasonNoInteractiveSession);
        }

        try
        {
            lock (_gate)
            {
                _icon ??= new NotifyIcon
                {
                    Icon = System.Drawing.SystemIcons.Information,
                    Visible = true,
                    Text = "PagentOS",
                };

                _icon.BalloonTipTitle = request.Title;
                _icon.BalloonTipText = request.Body;
                _icon.BalloonTipIcon = request.Priority == "urgent"
                    ? ToolTipIcon.Warning
                    : ToolTipIcon.Info;
                // Urgent asks to stay longer. Windows treats this as a hint and may ignore
                // it, which is why the answer below says "shown", never "seen".
                _icon.ShowBalloonTip(request.Priority == "urgent" ? 30_000 : 10_000);
            }

            if (request.Actions.Count > 0)
            {
                _logger.LogInformation(
                    "desktop.notify showed {NotificationId} without its {Count} action button(s): "
                    + "a shell balloon has none (requirement 370 needs the WinRT toast surface)",
                    request.NotificationId,
                    request.Actions.Count);
                return new ToastOutcome(true, null, "actions_not_rendered")
                {
                    Surface = ToastSurfaces.Balloon,
                    ActionsRendered = 0,
                };
            }

            return new ToastOutcome(true) { Surface = ToastSurfaces.Balloon, ActionsRendered = 0 };
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "desktop.notify could not reach the shell");
            return ToastOutcome.NotShown(NotifyCapabilities.ReasonShellUnavailable, ex.GetType().Name);
        }
    }

    public void Dispose()
    {
        lock (_gate)
        {
            if (_icon is not null)
            {
                _icon.Visible = false;
                _icon.Dispose();
                _icon = null;
            }
        }
    }
}
