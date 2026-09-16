using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using Windows.Data.Xml.Dom;
using Windows.UI.Notifications;

namespace PagentOS.SessionCompanion.Notify;

/// <summary>One toast as the platform is asked to show it.</summary>
public sealed record ToastSpec(string Xml, string Tag, string Group, bool HighPriority);

/// <summary>What the platform tells the sink about a toast after it was shown.</summary>
public interface IToastEvents
{
    /// <summary>The owner pressed the toast or one of its buttons; <paramref name="arguments"/> is what the XML said.</summary>
    void OnActivated(string tag, string arguments);

    /// <summary>The toast left the screen: <c>user_canceled</c>, <c>application_hidden</c> or <c>timed_out</c>.</summary>
    void OnDismissed(string tag, string reason);

    /// <summary>Windows reported, after <c>Show</c> returned, that the toast failed.</summary>
    void OnFailed(string tag, string error);
}

/// <summary>
/// The seam between the sink's decisions and <c>Windows.UI.Notifications</c>. Everything
/// above it is testable with a fake; everything below it needs a logged-in session.
/// </summary>
public interface IToastPlatform
{
    /// <summary>
    /// <c>ToastNotifier.Setting</c> for <paramref name="appId"/> as a
    /// <see cref="ToastNotifierSettings"/> token. Throws when the platform cannot be asked;
    /// <see cref="ToastPlatformErrors.NotFound"/> is what Windows answers for an id that has
    /// never shown a toast (measured on 19045: the answer exists only after the first Show).
    /// </summary>
    string ReadSetting(string appId);

    /// <summary>Shows the toast. Throws when Windows refuses it.</summary>
    void Show(string appId, ToastSpec spec, IToastEvents events);

    /// <summary>
    /// Whether Windows' own notification history holds this app's toast with this tag and
    /// group - the read-back that turns "Show returned" into "Windows took it".
    /// </summary>
    bool InHistory(string appId, string tag, string group);

    /// <summary>Takes one of this app's toasts off the screen and out of the Action Center.</summary>
    void Remove(string appId, string tag, string group);

    /// <summary><c>SHQueryUserNotificationState</c> as a token, or null when it cannot be read.</summary>
    string? UserState();
}

/// <summary>HRESULTs the sink tells apart.</summary>
public static class ToastPlatformErrors
{
    /// <summary>HRESULT_FROM_WIN32(ERROR_NOT_FOUND): Windows holds no notification settings for the id yet.</summary>
    public const int NotFound = unchecked((int)0x80070490);
}

/// <summary>The production platform: WinRT toasts for an unpackaged process.</summary>
/// <remarks>
/// The <see cref="ToastNotification"/> objects are kept referenced until they are replaced,
/// removed or evicted: the <c>Activated</c> event of a toast whose object was collected never
/// reaches this process. For an unpackaged app without a COM activator Windows delivers a
/// button press only to a running process that still holds the toast, which is why the sink
/// removes button-bearing toasts when the companion exits.
/// </remarks>
[SupportedOSPlatform("windows10.0.19041.0")]
public sealed class WindowsToastPlatform : IToastPlatform
{
    /// <summary>How many shown toasts are kept referenced.</summary>
    public const int MaxTracked = 32;

    private readonly object _gate = new();
    private readonly Dictionary<string, Held> _held = new(StringComparer.Ordinal);
    private readonly Queue<string> _order = new();

    public string ReadSetting(string appId)
    {
        var notifier = ToastNotificationManager.CreateToastNotifier(appId);
        return notifier.Setting switch
        {
            NotificationSetting.Enabled => ToastNotifierSettings.Enabled,
            NotificationSetting.DisabledForApplication => ToastNotifierSettings.DisabledForApplication,
            NotificationSetting.DisabledForUser => ToastNotifierSettings.DisabledForUser,
            NotificationSetting.DisabledByGroupPolicy => ToastNotifierSettings.DisabledByGroupPolicy,
            NotificationSetting.DisabledByManifest => ToastNotifierSettings.DisabledByManifest,
            _ => ToastNotifierSettings.Unknown,
        };
    }

    public void Show(string appId, ToastSpec spec, IToastEvents events)
    {
        ArgumentNullException.ThrowIfNull(spec);
        ArgumentNullException.ThrowIfNull(events);
        var document = new XmlDocument();
        document.LoadXml(spec.Xml);
        var toast = new ToastNotification(document)
        {
            Tag = spec.Tag,
            Group = spec.Group,
            Priority = spec.HighPriority ? ToastNotificationPriority.High : ToastNotificationPriority.Default,
        };

        var tag = spec.Tag;
        toast.Activated += (_, args) =>
        {
            var arguments = args is ToastActivatedEventArgs activated ? activated.Arguments : string.Empty;
            events.OnActivated(tag, arguments ?? string.Empty);
        };
        toast.Dismissed += (_, args) => events.OnDismissed(tag, args.Reason switch
        {
            ToastDismissalReason.UserCanceled => "user_canceled",
            ToastDismissalReason.ApplicationHidden => "application_hidden",
            ToastDismissalReason.TimedOut => "timed_out",
            _ => "unknown",
        });
        toast.Failed += (_, args) => events.OnFailed(tag, "0x" + (args.ErrorCode?.HResult ?? 0).ToString("X8", System.Globalization.CultureInfo.InvariantCulture));

        ToastNotificationManager.CreateToastNotifier(appId).Show(toast);

        lock (_gate)
        {
            if (!_held.ContainsKey(tag))
            {
                _order.Enqueue(tag);
            }

            _held[tag] = new Held(toast);
            while (_held.Count > MaxTracked && _order.Count > 0)
            {
                _held.Remove(_order.Dequeue());
            }
        }
    }

    /// <summary>How many times the history is read before a toast counts as not kept.</summary>
    public const int HistoryAttempts = 10;

    /// <summary>The pause between reads: nine pauses, 450 ms of sleeping, for a toast Windows did not keep.</summary>
    public static readonly TimeSpan HistoryPause = TimeSpan.FromMilliseconds(50);

    /// <remarks>
    /// Runs on the <c>desktop.notify</c> handling thread ON PURPOSE: its answer is what the
    /// command reports as <c>shown</c>, so it cannot move after the reply. It is bounded:
    /// a kept toast is normally found on the first read, and a toast Windows did not keep
    /// costs <see cref="HistoryAttempts"/> reads with <see cref="HistoryPause"/> between them -
    /// measured 564 ms on the owner's machine (Windows sleeps overshoot, and each read takes
    /// time) - well inside the desktop family's 60 s cap and the Cloud Core ladder's 15 s
    /// toast timeout. Notify requests are rare (a handful an hour), so this never queues.
    /// </remarks>
    public bool InHistory(string appId, string tag, string group)
    {
        // The history is written as Show is processed; a short bounded wait covers the gap.
        for (var attempt = 0; attempt < HistoryAttempts; attempt++)
        {
            foreach (var toast in ToastNotificationManager.History.GetHistory(appId))
            {
                if (toast.Tag == tag && toast.Group == group)
                {
                    return true;
                }
            }

            if (attempt + 1 < HistoryAttempts)
            {
                Thread.Sleep(HistoryPause);
            }
        }

        return false;
    }

    public void Remove(string appId, string tag, string group)
    {
        lock (_gate)
        {
            _held.Remove(tag);
        }

        ToastNotificationManager.History.Remove(tag, group, appId);
    }

    public string? UserState()
    {
        try
        {
            return SHQueryUserNotificationState(out var state) != 0
                ? null
                : state switch
                {
                    1 => "not_present",
                    2 => "busy",
                    3 => "fullscreen_d3d",
                    4 => "presentation",
                    5 => "accepts_notifications",
                    6 => "quiet_time",
                    7 => "fullscreen_app",
                    _ => null,
                };
        }
        catch (Exception ex) when (ex is DllNotFoundException or EntryPointNotFoundException)
        {
            return null;
        }
    }

    [DllImport("shell32.dll")]
    private static extern int SHQueryUserNotificationState(out int state);

    private sealed record Held(ToastNotification Toast);
}
