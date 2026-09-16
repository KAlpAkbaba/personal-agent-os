using PagentOS.SessionCompanion.Notify;

namespace PagentOS.Agent.Tests.Notify;

/// <summary>
/// The toast platform without Windows: records what it was asked to show, answers the
/// notifier setting it is told to, and lets a test deliver the events Windows would.
/// </summary>
public sealed class FakeToastPlatform : IToastPlatform
{
    private readonly Dictionary<string, IToastEvents> _events = new(StringComparer.Ordinal);

    public string Setting { get; set; } = ToastNotifierSettings.Enabled;

    public Exception? SettingFailure { get; set; }

    public Exception? ShowFailure { get; set; }

    public string? State { get; set; } = "accepts_notifications";

    public List<(string AppId, ToastSpec Spec)> Shown { get; } = [];

    public List<(string AppId, string Tag, string Group)> Removed { get; } = [];

    public string ReadSetting(string appId)
    {
        if (SettingFailure is not null)
        {
            throw SettingFailure;
        }

        return Setting;
    }

    public void Show(string appId, ToastSpec spec, IToastEvents events)
    {
        if (ShowFailure is not null)
        {
            throw ShowFailure;
        }

        Shown.Add((appId, spec));
        _events[spec.Tag] = events;
    }

    /// <summary>Whether a shown toast is found in the history read-back.</summary>
    public bool Keeps { get; set; } = true;

    /// <summary>Settings the platform answers after the first successful Show (Windows creates them then).</summary>
    public string? SettingAfterFirstShow { get; set; }

    public bool InHistory(string appId, string tag, string group)
    {
        var held = Keeps && Shown.Any(s => s.AppId == appId && s.Spec.Tag == tag && s.Spec.Group == group);
        if (held && SettingAfterFirstShow is not null)
        {
            SettingFailure = null;
            Setting = SettingAfterFirstShow;
        }

        return held;
    }

    public void Remove(string appId, string tag, string group) => Removed.Add((appId, tag, group));

    public string? UserState() => State;

    /// <summary>What Windows does when the owner presses a button: the XML's arguments come back.</summary>
    public void Press(string tag, string arguments) => _events[tag].OnActivated(tag, arguments);

    public void Dismiss(string tag, string reason) => _events[tag].OnDismissed(tag, reason);

    public void Fail(string tag, string error) => _events[tag].OnFailed(tag, error);
}

/// <summary>A balloon that records what it was asked to show.</summary>
public sealed class RecordingBalloon : IToastSink
{
    public List<ToastRequest> Shown { get; } = [];

    public Exception? Failure { get; set; }

    public ToastOutcome Show(ToastRequest request)
    {
        if (Failure is not null)
        {
            throw Failure;
        }

        Shown.Add(request);
        return request.Actions.Count > 0
            ? new ToastOutcome(true, null, "actions_not_rendered") { Surface = ToastSurfaces.Balloon, ActionsRendered = 0 }
            : new ToastOutcome(true) { Surface = ToastSurfaces.Balloon, ActionsRendered = 0 };
    }
}
