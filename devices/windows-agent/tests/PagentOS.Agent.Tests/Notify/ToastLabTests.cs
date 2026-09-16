using System.Diagnostics;
using System.Runtime.Versioning;
using System.Text.Json.Nodes;
using System.Windows.Automation;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Operator;
using PagentOS.SessionCompanion;
using PagentOS.SessionCompanion.Notify;
using Windows.UI.Notifications;
using Xunit;
using Xunit.Abstractions;

namespace PagentOS.Agent.Tests.Notify;

/// <summary>
/// The button-press lab needs toast POPUPS, which Windows suppresses without telling any API
/// (Focus Assist, a duplicated display, a full-screen rule). Measured on the owner's machine on
/// 2026-09-17: the shell answered "accepts notifications" and not even Windows PowerShell's own
/// registered toast produced a popup window. So it runs only when asked for.
/// </summary>
public sealed class ToastPressLabFactAttribute : FactAttribute
{
    public ToastPressLabFactAttribute()
    {
        var reason = OperatorLab.SkipReason();
        if (reason is null && !string.Equals(Environment.GetEnvironmentVariable("PAGENTOS_TOAST_PRESS_LAB"), "1", StringComparison.Ordinal))
        {
            reason = "needs visible toast popups (Focus Assist off, display not duplicated) - set PAGENTOS_TOAST_PRESS_LAB=1 on a desktop that shows them";
        }

        if (reason is not null)
        {
            Skip = reason;
        }
    }
}

/// <summary>
/// B11-toast lab (rows 369, 370): the REAL Windows toast platform on the owner's desktop, under
/// lab AppUserModelIDs of its own - never the companion's.
/// </summary>
/// <remarks>
/// <para>
/// What is proved here and nowhere else: that Windows accepts a toast from an unpackaged
/// process under a Start Menu shortcut's AppUserModelID, that the toast is in Windows' own
/// notification history afterwards (read back, not claimed), that an id Windows has never
/// seen answers ERROR_NOT_FOUND until its first toast and the sink still shows that toast, and
/// - when asked for - that pressing a button through UI Automation on the popup arrives as an
/// action id in the heartbeat.
/// </para>
/// <para>
/// It may show real toasts on this desktop. Everything it creates is removed on dispose, even
/// when a test failed: its toasts (<c>History.Clear</c> per lab id) and its shortcut. Windows
/// wrote no per-app settings key for these ids (measured), so no registry is touched. Joins
/// the operator lab's collection: one desktop, one pair of hands.
/// </para>
/// </remarks>
[Collection(OperatorLabCollection.Name)]
[SupportedOSPlatform("windows10.0.19041.0")]
public sealed class ToastLabTests(ITestOutputHelper output) : IDisposable
{
    public const string LabAppId = "PagentOS.Companion.Lab";
    public const string LabShortcut = "PagentOS Companion Lab.lnk";

    private readonly string _programs = AppIdentityShortcut.DefaultProgramsDirectory();
    private readonly WindowsToastPlatform _platform = new();
    private readonly NotifyActionQueue _queue = new();
    private readonly RecordingBalloon _balloon = new();
    private readonly List<string> _appIds = [LabAppId];

    public void Dispose()
    {
        foreach (var appId in _appIds)
        {
            try
            {
                ToastNotificationManager.History.Clear(appId);
            }
            catch (Exception ex)
            {
                output.WriteLine($"history clear for {appId}: {ex.GetType().Name} 0x{ex.HResult:X8}");
            }
        }

        AppIdentityShortcut.Remove(_programs, LabShortcut);
        Assert.Null(AppIdentityShortcut.Read(_programs, LabShortcut));
    }

    private WindowsToastSink Sink(string appId)
        => new(
            _platform,
            () =>
            {
                output.WriteLine($"lab shortcut for {appId}: {AppIdentityShortcut.Ensure(_programs, appId, Environment.ProcessPath!, LabShortcut)}");
                return null;
            },
            _balloon,
            _queue,
            NullLogger.Instance,
            appId: appId);

    private static JsonObject Payload(string id, params (string Id, string Label)[] actions)
    {
        var payload = new JsonObject
        {
            ["notification_id"] = id,
            ["title"] = "PagentOS lab: bildirim denemesi",
            ["body"] = "Bu bir test bildirimidir; ğüşöçıİ <b>&amp;</b> - birazdan kendiliğinden kaldırılacak.",
            ["priority"] = "normal",
        };
        if (actions.Length > 0)
        {
            payload["actions"] = new JsonArray(actions.Select(a => (JsonNode)new JsonObject { ["id"] = a.Id, ["label"] = a.Label }).ToArray());
        }

        return payload;
    }

    [LabFact]
    public void Windows_accepts_a_real_toast_with_buttons_and_keeps_it_in_its_history()
    {
        var id = Guid.NewGuid().ToString();

        var answer = new NotifyCapabilities(Sink(LabAppId), NullLogger.Instance)
            .Notify(Payload(id, ("lab_open", "Aç"), ("lab_snooze", "Ertele")));
        output.WriteLine(answer.ToJsonString());

        Assert.True(answer["shown"]!.GetValue<bool>());
        Assert.Equal(ToastSurfaces.Toast, answer["surface"]!.GetValue<string>());
        Assert.Equal(2, answer["actions_rendered"]!.GetValue<int>());
        Assert.Equal(ToastNotifierSettings.Enabled, answer["notifier_setting"]!.GetValue<string>());
        Assert.Empty(_balloon.Shown);
        Assert.Equal("PagentOS.Companion.Lab", AppIdentityShortcut.Read(_programs, LabShortcut)!.Value.AppUserModelId);

        // Read back from Windows, not claimed: the toast is in this id's notification history
        // under the tag and group the sink chose, with the escaped Turkish text and the data-only
        // button arguments.
        var mine = Assert.Single(ToastNotificationManager.History.GetHistory(LabAppId), toast => toast.Tag == id);
        Assert.Equal(ToastXml.Group, mine.Group);
        var xml = mine.Content.GetXml();
        Assert.Contains("ğüşöçıİ &lt;b&gt;&amp;amp;&lt;/b&gt;", xml, StringComparison.Ordinal);
        Assert.Contains($"action=lab_snooze;notification={id}", xml, StringComparison.Ordinal);

        // Replacing by tag: a second send of the same notification is still ONE toast.
        new NotifyCapabilities(Sink(LabAppId), NullLogger.Instance).Notify(Payload(id, ("lab_open", "Aç")));
        Assert.Single(ToastNotificationManager.History.GetHistory(LabAppId), toast => toast.Tag == id);

        // And removing it takes it out of Windows' history.
        _platform.Remove(LabAppId, id, ToastXml.Group);
        Assert.DoesNotContain(ToastNotificationManager.History.GetHistory(LabAppId), toast => toast.Tag == id);
    }

    [LabFact]
    public void A_fresh_id_has_no_settings_until_its_first_toast_and_that_toast_is_still_shown()
    {
        var fresh = "PagentOS.Lab.Fresh" + Guid.NewGuid().ToString("N")[..10];
        _appIds.Add(fresh);
        var before = Assert.ThrowsAny<Exception>(() => _platform.ReadSetting(fresh));
        output.WriteLine($"before the first toast: {before.GetType().Name} 0x{before.HResult:X8}");
        Assert.Equal(ToastPlatformErrors.NotFound, before.HResult);

        var id = Guid.NewGuid().ToString();
        var answer = new NotifyCapabilities(Sink(fresh), NullLogger.Instance).Notify(Payload(id));
        output.WriteLine(answer.ToJsonString());

        Assert.Equal(ToastSurfaces.Toast, answer["surface"]!.GetValue<string>());
        Assert.Equal(ToastNotifierSettings.Enabled, answer["notifier_setting"]!.GetValue<string>());
        Assert.Equal(ToastNotifierSettings.Enabled, _platform.ReadSetting(fresh));
        Assert.True(_platform.InHistory(fresh, id, ToastXml.Group));
        Assert.Empty(_balloon.Shown);
    }

    [LabFact]
    public void The_history_read_back_does_not_find_a_toast_that_was_never_shown()
    {
        var clock = Stopwatch.StartNew();
        Assert.False(_platform.InHistory(LabAppId, Guid.NewGuid().ToString(), ToastXml.Group));
        output.WriteLine($"a miss took {clock.ElapsedMilliseconds} ms (bound {WindowsToastPlatform.HistoryAttempts} x {WindowsToastPlatform.HistoryPause.TotalMilliseconds} ms)");

        // Hang guard only: a miss must not turn into an unbounded wait on the notify thread.
        Assert.True(clock.Elapsed < TimeSpan.FromSeconds(10), "the history read-back is unbounded");
    }

    [ToastPressLabFact]
    public void A_button_pressed_on_the_real_toast_arrives_in_the_heartbeat_as_its_action_id()
    {
        var id = Guid.NewGuid().ToString();
        var label = "Lab " + id[..8];
        var sink = Sink(LabAppId);
        var answer = new NotifyCapabilities(sink, NullLogger.Instance).Notify(Payload(id, ("lab_ack", label)));
        Assert.Equal(ToastSurfaces.Toast, answer["surface"]!.GetValue<string>());

        var button = FindToastButton(label, TimeSpan.FromSeconds(15));
        Assert.True(button is not null, $"no toast button named '{label}' appeared within 15 s (shell state {_platform.UserState()})");
        ((InvokePattern)button!.GetCurrentPattern(InvokePattern.Pattern)).Invoke();

        var clock = Stopwatch.StartNew();
        while (_queue.Pending == 0 && clock.Elapsed < TimeSpan.FromSeconds(15))
        {
            Thread.Sleep(100);
        }

        output.WriteLine($"activation after {clock.ElapsedMilliseconds} ms; refused={sink.RefusedActivationCount}");
        var reporter = new ActivityStatusReporter(new NoInput(), new NoDisplay(), () => null, notifyActions: _queue);
        var status = HeartbeatStatus.Project(reporter.Compose())!;
        var entry = Assert.Single(status[HeartbeatStatus.NotifyActions]!.AsArray())!;
        Assert.Equal(id, entry["notification_id"]!.GetValue<string>());
        Assert.Equal("lab_ack", entry["action_id"]!.GetValue<string>());
        Assert.Equal(1, sink.ActivationCount);
    }

    /// <summary>The popup's button, found through UI Automation on the shell's own top-level windows.</summary>
    private AutomationElement? FindToastButton(string label, TimeSpan within)
    {
        var condition = new AndCondition(
            new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Button),
            new PropertyCondition(AutomationElement.NameProperty, label));
        var clock = Stopwatch.StartNew();
        while (clock.Elapsed < within)
        {
            var windows = AutomationElement.RootElement.FindAll(
                TreeScope.Children,
                new PropertyCondition(AutomationElement.ClassNameProperty, "Windows.UI.Core.CoreWindow"));
            foreach (AutomationElement window in windows)
            {
                var found = window.FindFirst(TreeScope.Descendants, condition);
                if (found is not null)
                {
                    output.WriteLine($"toast button found in '{window.Current.Name}' after {clock.ElapsedMilliseconds} ms");
                    return found;
                }
            }

            Thread.Sleep(200);
        }

        return null;
    }

    private sealed class NoInput : IInputActivitySource
    {
        public TimeSpan? IdleTime => null;
    }

    private sealed class NoDisplay : IDisplayStateObserver
    {
        public DisplayObservation Current => DisplayObservation.Nothing;
    }
}
