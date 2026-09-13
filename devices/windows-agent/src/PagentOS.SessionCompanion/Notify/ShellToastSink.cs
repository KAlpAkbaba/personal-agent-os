using System.Runtime.Versioning;
using System.Windows.Forms;
using Microsoft.Extensions.Logging;

namespace PagentOS.SessionCompanion.Notify;

/// <summary>
/// The production sink: a shell notification the owner sees with the browser closed.
/// </summary>
/// <remarks>
/// <para>
/// Windows 10 and 11 route a tray balloon through the notification system, so it appears as
/// a toast and stays in the Action Center afterwards — which is the property that matters
/// here. The owner does not need to be looking at anything, and the notice is still there
/// when they come back.
/// </para>
/// <para>
/// <strong>What this sink cannot do, stated rather than implied:</strong> action buttons
/// (requirement 370). A balloon has no buttons. Those need the WinRT
/// <c>ToastNotificationManager</c>, which needs a Windows-version-specific target framework
/// this project does not yet set. The request's actions are therefore parsed, validated and
/// carried — the contract half is real — and this sink reports that it showed the toast
/// without them rather than pretending the buttons were there.
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
                return new ToastOutcome(true, null, "actions_not_rendered");
            }

            return ToastOutcome.Ok();
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
