using System.Runtime.Versioning;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion.Notify;

/// <summary>
/// The production <see cref="IToastSink"/> since B11-toast (rows 369, 370): a real Windows
/// toast with the Cloud Core's buttons, and the tray balloon only when the toast platform
/// cannot be used.
/// </summary>
/// <remarks>
/// <para><strong>What "shown" means here.</strong> Windows accepted <c>Show</c> without an
/// error - nothing more. It is never "the owner saw it": a toast raised while nobody is at
/// the desk goes to the Action Center unseen, and Focus Assist (which Windows does not
/// report through <c>ToastNotifier.Setting</c>) sends it there without a popup. The answer
/// carries the shell's own user-notification state (<c>user_state</c>) so the Cloud Core can
/// see a presentation or full-screen state, and says nothing it does not know.</para>
/// <para><strong>When the owner has turned notifications off, nothing is shown.</strong>
/// <c>disabled_for_user</c>, <c>disabled_for_application</c> and
/// <c>disabled_by_group_policy</c> answer <c>shown: false</c> with
/// <c>notifications_disabled</c>: a balloon from the same process would route around a choice
/// the owner made in Windows' settings. The balloon is the fallback for a toast platform that
/// cannot be used at all - no AppUserModelID shortcut, <c>ToastNotificationManager</c>
/// throwing, <c>disabled_by_manifest</c>, or <c>Show</c> refusing - and the answer says
/// <c>surface: "balloon"</c> with zero buttons whenever it was used.</para>
/// <para><strong>Buttons are data.</strong> A press reaches this process as the arguments
/// string <see cref="ToastXml"/> wrote; it is accepted only for a toast this sink showed and an
/// action id that toast offered, and it is then only QUEUED for the Cloud Core
/// (<see cref="NotifyActionQueue"/>). Nothing is run, opened or launched here.</para>
/// </remarks>
[SupportedOSPlatform("windows")]
public sealed class WindowsToastSink : IToastSink, IToastEvents, IDisposable
{
    /// <summary>How many shown toasts are remembered for attributing a press.</summary>
    public const int MaxTracked = 32;

    private readonly IToastPlatform _platform;
    private readonly Func<string?> _ensureIdentity;
    private readonly IToastSink? _fallback;
    private readonly NotifyActionQueue _actions;
    private readonly ILogger _logger;
    private readonly AuditLog? _audit;
    private readonly Func<bool> _interactive;
    private readonly object _gate = new();
    private readonly Dictionary<string, Tracked> _tracked = new(StringComparer.Ordinal);
    private readonly Queue<string> _order = new();
    private volatile bool _identityReady;
    private bool _disposed;
    private long _shown;
    private long _fellBack;
    private long _activations;
    private long _refusedActivations;
    private long _dismissals;
    private long _failedAfterShow;

    /// <param name="platform">The toast platform.</param>
    /// <param name="ensureIdentity">Makes the AppUserModelID shortcut exist; null on success, otherwise why it could not.</param>
    /// <param name="fallback">The balloon, or null when there is none.</param>
    /// <param name="actions">Where accepted presses wait for the heartbeat.</param>
    /// <param name="logger">The companion's logger.</param>
    /// <param name="appId">The AppUserModelID the shortcut carries.</param>
    /// <param name="audit">The companion's audit log.</param>
    /// <param name="interactive">Whether this process runs in an interactive session (a test seam).</param>
    public WindowsToastSink(
        IToastPlatform platform,
        Func<string?> ensureIdentity,
        IToastSink? fallback,
        NotifyActionQueue actions,
        ILogger logger,
        string appId = AppIdentityShortcut.AppUserModelId,
        AuditLog? audit = null,
        Func<bool>? interactive = null)
    {
        _platform = platform;
        _ensureIdentity = ensureIdentity;
        _fallback = fallback;
        _actions = actions;
        _logger = logger;
        AppId = appId;
        _audit = audit;
        _interactive = interactive ?? (() => Environment.UserInteractive);
    }

    public string AppId { get; }

    public long ShownCount => Interlocked.Read(ref _shown);

    public long FallbackCount => Interlocked.Read(ref _fellBack);

    public long ActivationCount => Interlocked.Read(ref _activations);

    public long RefusedActivationCount => Interlocked.Read(ref _refusedActivations);

    public long DismissalCount => Interlocked.Read(ref _dismissals);

    /// <summary>Toasts Windows reported as failed AFTER <c>Show</c> returned (the answer had already said shown).</summary>
    public long FailedAfterShowCount => Interlocked.Read(ref _failedAfterShow);

    public ToastOutcome Show(ToastRequest request)
    {
        ArgumentNullException.ThrowIfNull(request);
        if (!_interactive())
        {
            return ToastOutcome.NotShown(NotifyCapabilities.ReasonNoInteractiveSession);
        }

        var identityProblem = EnsureIdentity();
        if (identityProblem is not null)
        {
            return FallBack(request, ToastNotifierSettings.Unknown, "app_identity_unavailable:" + identityProblem);
        }

        string setting;
        try
        {
            setting = _platform.ReadSetting(AppId);
        }
        catch (Exception ex) when (ex.HResult == ToastPlatformErrors.NotFound)
        {
            // Windows keeps no settings for an id before its first toast, so on a fresh
            // install this is the normal answer, not a fault: the shortcut exists (checked
            // above), the toast is tried, and the history read-back below decides.
            setting = ToastNotifierSettings.Unknown;
        }
        catch (Exception ex)
        {
            _logger.LogWarning("toast platform cannot be asked ({Error}); using the balloon", Describe(ex));
            return FallBack(request, ToastNotifierSettings.Unknown, "toast_platform_unavailable:" + Describe(ex));
        }

        var userState = SafeUserState();
        switch (setting)
        {
            case ToastNotifierSettings.Enabled:
            case ToastNotifierSettings.Unknown:
                break;
            case ToastNotifierSettings.DisabledForUser:
            case ToastNotifierSettings.DisabledForApplication:
            case ToastNotifierSettings.DisabledByGroupPolicy:
                // The owner's (or the machine's) choice. Not routed around.
                return ToastOutcome.NotShown(NotifyCapabilities.ReasonNotificationsDisabled, setting) with
                {
                    NotifierSetting = setting,
                    ActionsRendered = 0,
                    UserState = userState,
                };
            default:
                return FallBack(request, setting, "toast_platform_unavailable:" + setting);
        }

        var tag = ToastXml.Tag(request);
        string xml;
        try
        {
            xml = ToastXml.Build(request);
        }
        catch (ArgumentException ex)
        {
            return ToastOutcome.NotShown(NotifyCapabilities.ReasonInvalidPayload, ex.Message) with
            {
                NotifierSetting = setting,
            };
        }

        // Tracked BEFORE Show: Windows may deliver an activation as soon as the toast exists.
        Track(tag, request);
        try
        {
            _platform.Show(AppId, new ToastSpec(xml, tag, ToastXml.Group, ToastXml.IsHighPriority(request)), this);
        }
        catch (Exception ex)
        {
            Untrack(tag, request.NotificationId);
            _logger.LogWarning("Windows refused the toast for {NotificationId} ({Error}); using the balloon", request.NotificationId, Describe(ex));
            return FallBack(request, setting, "toast_show_failed:" + Describe(ex));
        }

        // "A queue is not a delivery": Show returning is not yet Windows holding the toast.
        bool held;
        try
        {
            held = _platform.InHistory(AppId, tag, ToastXml.Group);
        }
        catch (Exception ex)
        {
            _logger.LogWarning("toast history could not be read for {NotificationId} ({Error})", request.NotificationId, Describe(ex));
            held = false;
        }

        if (!held)
        {
            Untrack(tag, request.NotificationId);
            try
            {
                _platform.Remove(AppId, tag, ToastXml.Group);
            }
            catch (Exception)
            {
                // Nothing to remove is the likely case.
            }

            _logger.LogWarning("Windows did not keep the toast for {NotificationId}; using the balloon", request.NotificationId);
            return FallBack(request, setting, "toast_not_in_history");
        }

        if (setting == ToastNotifierSettings.Unknown)
        {
            // The first toast creates the settings; say what they are now.
            try
            {
                setting = _platform.ReadSetting(AppId);
            }
            catch (Exception)
            {
                // Stays unknown.
            }
        }

        Interlocked.Increment(ref _shown);
        _logger.LogInformation(
            "desktop.notify toast accepted by Windows for {NotificationId} (tag {Tag}, {Count} button(s), priority {Priority})",
            request.NotificationId,
            tag,
            request.Actions.Count,
            request.Priority);
        return new ToastOutcome(true)
        {
            Surface = ToastSurfaces.Toast,
            NotifierSetting = setting,
            ActionsRendered = request.Actions.Count,
            UserState = userState,
        };
    }

    public void OnActivated(string tag, string arguments)
    {
        var activation = ToastXml.ParseArguments(arguments);
        Tracked? tracked;
        lock (_gate)
        {
            _tracked.TryGetValue(tag, out tracked);
        }

        if (activation is null || tracked is null || !string.Equals(tracked.NotificationId, activation.NotificationId, StringComparison.Ordinal))
        {
            Interlocked.Increment(ref _refusedActivations);
            _logger.LogWarning("toast activation ignored: not a toast or an argument form this companion wrote (tag {Tag})", tag);
            return;
        }

        if (activation.ActionId is null)
        {
            // The body, not a button. The contract reports buttons only; opening is the inbox's.
            _logger.LogInformation("toast {NotificationId} opened by the owner (no button)", activation.NotificationId);
            return;
        }

        if (!tracked.ActionIds.Contains(activation.ActionId))
        {
            Interlocked.Increment(ref _refusedActivations);
            _logger.LogWarning("toast activation ignored: {NotificationId} offered no action {ActionId}", activation.NotificationId, activation.ActionId);
            return;
        }

        _actions.Record(activation.NotificationId, activation.ActionId);
        Interlocked.Increment(ref _activations);
        _audit?.Write(
            "notify_action",
            capability: AgentCapabilities.DesktopNotify,
            status: "recorded",
            detail: $"notification={activation.NotificationId}; action={activation.ActionId}");
        _logger.LogInformation("toast button {ActionId} pressed on {NotificationId}; queued for the Cloud Core", activation.ActionId, activation.NotificationId);
    }

    public void OnDismissed(string tag, string reason)
    {
        Interlocked.Increment(ref _dismissals);
        _logger.LogDebug("toast {Tag} dismissed ({Reason})", tag, reason);
    }

    public void OnFailed(string tag, string error)
    {
        Interlocked.Increment(ref _failedAfterShow);
        _logger.LogWarning("Windows reported toast {Tag} failed after it was accepted ({Error})", tag, error);
    }

    /// <summary>
    /// Takes this process's button-bearing toasts off the screen: once the companion is gone,
    /// nothing receives their presses, and a button that does nothing is worse than none.
    /// Plain toasts stay in the Action Center.
    /// </summary>
    public void Dispose()
    {
        List<string> withButtons;
        lock (_gate)
        {
            if (_disposed)
            {
                return;
            }

            _disposed = true;
            withButtons = _tracked.Where(pair => pair.Value.ActionIds.Count > 0).Select(pair => pair.Key).ToList();
            _tracked.Clear();
            _order.Clear();
        }

        foreach (var tag in withButtons)
        {
            try
            {
                _platform.Remove(AppId, tag, ToastXml.Group);
            }
            catch (Exception ex)
            {
                _logger.LogDebug("toast {Tag} could not be removed at exit ({Error})", tag, Describe(ex));
            }
        }
    }

    /// <summary>
    /// Writes the AppUserModelID shortcut now rather than at the first toast. Null when it is
    /// ready; otherwise why not (the first toast will try again).
    /// </summary>
    public string? PrepareIdentity() => EnsureIdentity();

    private string? EnsureIdentity()
    {
        if (_identityReady)
        {
            return null;
        }

        string? problem;
        try
        {
            problem = _ensureIdentity();
        }
        catch (Exception ex)
        {
            problem = Describe(ex);
        }

        if (problem is null)
        {
            _identityReady = true;
        }

        return problem;
    }

    private ToastOutcome FallBack(ToastRequest request, string setting, string why)
    {
        Interlocked.Increment(ref _fellBack);
        if (_fallback is null)
        {
            return ToastOutcome.NotShown(NotifyCapabilities.ReasonShellUnavailable, why) with { NotifierSetting = setting };
        }

        ToastOutcome outcome;
        try
        {
            outcome = _fallback.Show(request);
        }
        catch (Exception ex)
        {
            outcome = ToastOutcome.NotShown(NotifyCapabilities.ReasonShellUnavailable, Describe(ex));
        }

        var detail = string.IsNullOrWhiteSpace(outcome.Detail) ? why : why + "; " + outcome.Detail;
        return outcome with
        {
            Detail = detail,
            NotifierSetting = setting,
            Surface = outcome.Shown ? (outcome.Surface ?? ToastSurfaces.Balloon) : null,
            ActionsRendered = outcome.Shown ? 0 : outcome.ActionsRendered,
        };
    }

    private string? SafeUserState()
    {
        try
        {
            return _platform.UserState();
        }
        catch (Exception)
        {
            return null;
        }
    }

    private void Track(string tag, ToastRequest request)
    {
        lock (_gate)
        {
            if (!_tracked.ContainsKey(tag))
            {
                _order.Enqueue(tag);
            }

            _tracked[tag] = new Tracked(request.NotificationId, request.Actions.Select(a => a.Id).ToHashSet(StringComparer.Ordinal));
            // A re-shown tag keeps its place and does not grow the table, so the oldest
            // entry evicted here is never the one just tracked.
            while (_tracked.Count > MaxTracked && _order.Count > 0)
            {
                _tracked.Remove(_order.Dequeue());
            }
        }
    }

    private void Untrack(string tag, string notificationId)
    {
        lock (_gate)
        {
            if (_tracked.TryGetValue(tag, out var tracked) && tracked.NotificationId == notificationId)
            {
                _tracked.Remove(tag);
            }
        }
    }

    private static string Describe(Exception ex)
        => ex.HResult != 0 ? $"{ex.GetType().Name} 0x{ex.HResult:X8}" : ex.GetType().Name;

    private sealed record Tracked(string NotificationId, HashSet<string> ActionIds);
}
