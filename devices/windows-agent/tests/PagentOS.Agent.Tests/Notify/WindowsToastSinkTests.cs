using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using System.Xml.Linq;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion;
using PagentOS.SessionCompanion.Notify;
using Xunit;

namespace PagentOS.Agent.Tests.Notify;

/// <summary>
/// B11-toast (rows 369, 370): the production sink's decisions, over a fake platform - which
/// surface, what the answer claims, when the balloon is used and when nothing is, and a
/// button press travelling from Windows' activation to the heartbeat.
/// </summary>
public sealed class WindowsToastSinkTests
{
    private const string Id = "6b1d0b2e-1f4a-4f2e-9d3c-2a5f9c8e7d10";

    private readonly FakeToastPlatform _platform = new();
    private readonly RecordingBalloon _balloon = new();
    private readonly NotifyActionQueue _queue = new();
    private string? _identityProblem;
    private int _identityCalls;

    private WindowsToastSink Sink(bool interactive = true, bool withBalloon = true)
        => new(
            _platform,
            () =>
            {
                _identityCalls++;
                return _identityProblem;
            },
            withBalloon ? _balloon : null,
            _queue,
            NullLogger.Instance,
            appId: "PagentOS.Companion.Test",
            interactive: () => interactive);

    private static JsonObject Payload(string priority = "normal", string? groupKey = null, params (string Id, string Label)[] actions)
    {
        var payload = new JsonObject
        {
            ["notification_id"] = Id,
            ["title"] = "Yedek alınamadı",
            ["body"] = "Gece yedeği 03:00'te başarısız oldu; ayrıntılar gelen kutusunda.",
            ["priority"] = priority,
        };
        if (groupKey is not null)
        {
            payload["group_key"] = groupKey;
        }

        if (actions.Length > 0)
        {
            payload["actions"] = new JsonArray(actions.Select(a => (JsonNode)new JsonObject { ["id"] = a.Id, ["label"] = a.Label }).ToArray());
        }

        return payload;
    }

    private static JsonObject Notify(IToastSink sink, JsonObject payload)
        => new NotifyCapabilities(sink, NullLogger.Instance).Notify(payload);

    // ------------------------------------------------------------------ the toast surface

    [Fact]
    public void An_enabled_notifier_shows_a_real_toast_with_the_buttons_and_says_so()
    {
        var answer = Notify(Sink(), Payload(actions: [("open", "Aç"), ("retry", "Yeniden dene")]));

        Assert.True(answer["shown"]!.GetValue<bool>());
        Assert.Equal("toast", answer["surface"]!.GetValue<string>());
        Assert.Equal(2, answer["actions_rendered"]!.GetValue<int>());
        Assert.Equal("enabled", answer["notifier_setting"]!.GetValue<string>());
        Assert.Equal("accepts_notifications", answer["user_state"]!.GetValue<string>());
        Assert.False(answer.ContainsKey("reason"));
        Assert.Empty(_balloon.Shown);

        var (appId, spec) = Assert.Single(_platform.Shown);
        Assert.Equal("PagentOS.Companion.Test", appId);
        Assert.Equal(Id, spec.Tag);
        Assert.Equal(ToastXml.Group, spec.Group);
        Assert.False(spec.HighPriority);
        var buttons = XElement.Parse(spec.Xml).Element("actions")!.Elements("action").Select(a => a.Attribute("content")!.Value);
        Assert.Equal(new[] { "Aç", "Yeniden dene" }, buttons);
    }

    [Fact]
    public void Urgent_asks_windows_for_high_priority_and_a_group_key_becomes_the_tag()
    {
        Notify(Sink(), Payload(priority: "urgent", groupKey: "backup:nightly"));

        var spec = Assert.Single(_platform.Shown).Spec;
        Assert.True(spec.HighPriority);
        Assert.StartsWith("g-", spec.Tag, StringComparison.Ordinal);
        Assert.Equal("reminder", XElement.Parse(spec.Xml).Attribute("scenario")!.Value);
    }

    [Fact]
    public void The_identity_shortcut_is_written_once_not_per_toast()
    {
        var sink = Sink();
        Assert.Null(sink.PrepareIdentity());
        Notify(sink, Payload());
        Notify(sink, Payload());

        Assert.Equal(1, _identityCalls);
        Assert.Equal(2, sink.ShownCount);
    }

    // ------------------------------------------------------------- the owner said no

    [Theory]
    [InlineData(ToastNotifierSettings.DisabledForUser)]
    [InlineData(ToastNotifierSettings.DisabledForApplication)]
    [InlineData(ToastNotifierSettings.DisabledByGroupPolicy)]
    public void A_disabled_notifier_shows_nothing_and_is_not_routed_around(string setting)
    {
        _platform.Setting = setting;

        var answer = Notify(Sink(), Payload(actions: [("open", "Aç")]));

        Assert.False(answer["shown"]!.GetValue<bool>());
        Assert.Equal(NotifyCapabilities.ReasonNotificationsDisabled, answer["reason"]!.GetValue<string>());
        Assert.Equal(setting, answer["notifier_setting"]!.GetValue<string>());
        Assert.False(answer.ContainsKey("surface"));
        Assert.Empty(_platform.Shown);
        Assert.Empty(_balloon.Shown);
    }

    // ------------------------------------------------------------ the balloon fallback

    [Fact]
    public void A_platform_that_cannot_be_asked_falls_back_to_the_balloon_and_says_it_has_no_buttons()
    {
        _platform.SettingFailure = new COMException("Class not registered.", unchecked((int)0x80040154));

        var answer = Notify(Sink(), Payload(actions: [("open", "Aç")]));

        Assert.True(answer["shown"]!.GetValue<bool>());
        Assert.Equal("balloon", answer["surface"]!.GetValue<string>());
        Assert.Equal(0, answer["actions_rendered"]!.GetValue<int>());
        Assert.Equal("unknown", answer["notifier_setting"]!.GetValue<string>());
        var detail = answer["detail"]!.GetValue<string>();
        Assert.StartsWith("toast_platform_unavailable:COMException 0x80040154", detail, StringComparison.Ordinal);
        Assert.EndsWith("actions_not_rendered", detail, StringComparison.Ordinal);
        Assert.Single(_balloon.Shown);
        Assert.Empty(_platform.Shown);
    }

    [Fact]
    public void The_first_toast_of_a_fresh_install_is_tried_although_windows_has_no_settings_yet()
    {
        // Measured on 19045: ToastNotifier.Setting throws ERROR_NOT_FOUND for an id until its
        // first Show, and answers after it. Treating that as "unavailable" made every fresh
        // install a balloon-only install, for ever (the balloon never creates the settings).
        _platform.SettingFailure = new COMException("Element not found.", ToastPlatformErrors.NotFound);
        _platform.SettingAfterFirstShow = ToastNotifierSettings.Enabled;

        var answer = Notify(Sink(), Payload(actions: [("open", "Aç")]));

        Assert.True(answer["shown"]!.GetValue<bool>());
        Assert.Equal("toast", answer["surface"]!.GetValue<string>());
        Assert.Equal("enabled", answer["notifier_setting"]!.GetValue<string>());
        Assert.Empty(_balloon.Shown);
    }

    [Fact]
    public void A_toast_windows_did_not_keep_is_not_claimed_and_the_balloon_carries_it()
    {
        _platform.Keeps = false;
        var sink = Sink();

        var answer = Notify(sink, Payload(actions: [("open", "Aç")]));

        Assert.Equal("balloon", answer["surface"]!.GetValue<string>());
        Assert.Equal(0, answer["actions_rendered"]!.GetValue<int>());
        Assert.StartsWith("toast_not_in_history", answer["detail"]!.GetValue<string>(), StringComparison.Ordinal);
        Assert.Equal(0, sink.ShownCount);
        Assert.Equal((sink.AppId, Id, ToastXml.Group), Assert.Single(_platform.Removed));
        _platform.Press(Id, ToastXml.ButtonArguments(Id, "open"));
        Assert.Equal(0, _queue.Pending);
    }

    [Fact]
    public void A_show_that_windows_refuses_falls_back_and_forgets_the_toast()
    {
        _platform.ShowFailure = new COMException("refused", unchecked((int)0x80070005));
        var sink = Sink();

        var answer = Notify(sink, Payload(actions: [("open", "Aç")]));

        Assert.Equal("balloon", answer["surface"]!.GetValue<string>());
        Assert.Equal("enabled", answer["notifier_setting"]!.GetValue<string>());
        Assert.StartsWith("toast_show_failed:", answer["detail"]!.GetValue<string>(), StringComparison.Ordinal);
        Assert.Equal(0, sink.ShownCount);
        Assert.Equal(1, sink.FallbackCount);

        // A press on a toast Windows never accepted is not believed.
        sink.OnActivated(Id, ToastXml.ButtonArguments(Id, "open"));
        Assert.Equal(0, _queue.Pending);
    }

    [Fact]
    public void No_identity_shortcut_means_the_balloon_and_the_next_toast_tries_again()
    {
        _identityProblem = "FileNotFoundException";
        var sink = Sink();

        var answer = Notify(sink, Payload());
        Assert.Equal("balloon", answer["surface"]!.GetValue<string>());
        Assert.Equal("app_identity_unavailable:FileNotFoundException", answer["detail"]!.GetValue<string>());

        _identityProblem = null;
        var second = Notify(sink, Payload());
        Assert.Equal("toast", second["surface"]!.GetValue<string>());
        Assert.Equal(2, _identityCalls);
    }

    [Fact]
    public void Disabled_by_manifest_is_a_platform_fault_not_an_owner_choice()
    {
        _platform.Setting = ToastNotifierSettings.DisabledByManifest;

        var answer = Notify(Sink(), Payload());

        Assert.Equal("balloon", answer["surface"]!.GetValue<string>());
        Assert.Equal("disabled_by_manifest", answer["notifier_setting"]!.GetValue<string>());
    }

    [Fact]
    public void Without_a_balloon_an_unusable_platform_is_an_honest_not_shown()
    {
        _platform.SettingFailure = new InvalidOperationException();

        var answer = Notify(Sink(withBalloon: false), Payload());

        Assert.False(answer["shown"]!.GetValue<bool>());
        Assert.Equal(NotifyCapabilities.ReasonShellUnavailable, answer["reason"]!.GetValue<string>());
        Assert.False(answer.ContainsKey("surface"));
    }

    [Fact]
    public void A_balloon_that_throws_is_not_shown_and_not_a_crash()
    {
        _platform.SettingFailure = new InvalidOperationException();
        _balloon.Failure = new ObjectDisposedException("NotifyIcon");

        var answer = Notify(Sink(), Payload());

        Assert.False(answer["shown"]!.GetValue<bool>());
        Assert.False(answer.ContainsKey("surface"));
        Assert.Contains("ObjectDisposedException", answer["detail"]!.GetValue<string>(), StringComparison.Ordinal);
    }

    [Fact]
    public void Session_zero_shows_nothing_and_asks_nothing()
    {
        var answer = Notify(Sink(interactive: false), Payload());

        Assert.False(answer["shown"]!.GetValue<bool>());
        Assert.Equal(NotifyCapabilities.ReasonNoInteractiveSession, answer["reason"]!.GetValue<string>());
        Assert.Equal(0, _identityCalls);
        Assert.Empty(_platform.Shown);
        Assert.Empty(_balloon.Shown);
    }

    // ----------------------------------------------------------------- a press, end to end

    [Fact]
    public void A_pressed_button_reaches_the_heartbeat_three_times_and_then_leaves()
    {
        var sink = Sink();
        Notify(sink, Payload(actions: [("open", "Aç"), ("snooze", "Ertele")]));
        var reporter = new ActivityStatusReporter(new NoInput(), new NoDisplay(), () => null, notifyActions: _queue);

        _platform.Press(Id, ToastXml.ButtonArguments(Id, "snooze"));

        var reports = Enumerable.Range(0, NotifyActionQueue.Transmissions + 1)
            .Select(_ => HeartbeatStatus.Project(reporter.Compose())!)
            .ToArray();
        for (var i = 0; i < NotifyActionQueue.Transmissions; i++)
        {
            var entry = Assert.Single(reports[i][HeartbeatStatus.NotifyActions]!.AsArray())!;
            Assert.Equal(Id, entry["notification_id"]!.GetValue<string>());
            Assert.Equal("snooze", entry["action_id"]!.GetValue<string>());
            Assert.EndsWith("Z", entry["pressed_at"]!.GetValue<string>(), StringComparison.Ordinal);
        }

        Assert.Empty(reports[^1][HeartbeatStatus.NotifyActions]!.AsArray());
        Assert.Equal(1, sink.ActivationCount);
    }

    [Theory]
    [InlineData("action=delete_all;notification=" + Id)]
    [InlineData("action=open;notification=11111111-2222-3333-4444-555555555555")]
    [InlineData("https://example.invalid/open")]
    [InlineData("dismiss")]
    [InlineData("")]
    public void A_press_the_toast_did_not_offer_is_not_queued(string arguments)
    {
        var sink = Sink();
        Notify(sink, Payload(actions: [("open", "Aç")]));

        _platform.Press(Id, arguments);

        Assert.Equal(0, _queue.Pending);
        Assert.Equal(0, sink.ActivationCount);
        Assert.Equal(1, sink.RefusedActivationCount);
    }

    [Fact]
    public void A_press_on_a_tag_this_sink_never_showed_is_not_queued()
    {
        var sink = Sink();

        sink.OnActivated("someone-else", ToastXml.ButtonArguments(Id, "open"));

        Assert.Equal(0, _queue.Pending);
        Assert.Equal(1, sink.RefusedActivationCount);
    }

    [Fact]
    public void Opening_the_toast_body_is_not_a_button_press()
    {
        var sink = Sink();
        Notify(sink, Payload(actions: [("open", "Aç")]));

        _platform.Press(Id, ToastXml.BodyArguments(Id));

        Assert.Equal(0, _queue.Pending);
        Assert.Equal(0, sink.ActivationCount);
        Assert.Equal(0, sink.RefusedActivationCount);
    }

    [Fact]
    public void A_replaced_toast_attributes_presses_to_the_newer_notification_only()
    {
        var sink = Sink();
        var older = Payload(groupKey: "backup:nightly", actions: [("open", "Aç")]);
        Notify(sink, older);
        var newer = Payload(groupKey: "backup:nightly", actions: [("retry", "Yeniden dene")]);
        var newerId = Guid.NewGuid().ToString();
        newer["notification_id"] = newerId;
        Notify(sink, newer);
        var tag = _platform.Shown[^1].Spec.Tag;
        Assert.Equal(_platform.Shown[0].Spec.Tag, tag);

        _platform.Press(tag, ToastXml.ButtonArguments(Id, "open"));
        _platform.Press(tag, ToastXml.ButtonArguments(newerId, "retry"));

        var entry = Assert.Single(_queue.Report())!;
        Assert.Equal(newerId, entry["notification_id"]!.GetValue<string>());
        Assert.Equal("retry", entry["action_id"]!.GetValue<string>());
    }

    [Fact]
    public void Dismissals_and_late_failures_are_counted_not_reported_as_presses()
    {
        var sink = Sink();
        Notify(sink, Payload(actions: [("open", "Aç")]));

        _platform.Dismiss(Id, "timed_out");
        _platform.Fail(Id, "0x80070490");

        Assert.Equal(1, sink.DismissalCount);
        Assert.Equal(1, sink.FailedAfterShowCount);
        Assert.Equal(0, _queue.Pending);
    }

    [Fact]
    public void At_exit_only_toasts_with_buttons_are_taken_down()
    {
        var sink = Sink();
        Notify(sink, Payload(actions: [("open", "Aç")]));
        var plain = Payload();
        plain["notification_id"] = Guid.NewGuid().ToString();
        Notify(sink, plain);

        sink.Dispose();
        sink.Dispose();

        var removed = Assert.Single(_platform.Removed);
        Assert.Equal(("PagentOS.Companion.Test", Id, ToastXml.Group), removed);
    }

    [Fact]
    public void The_tracking_table_is_bounded_and_forgets_the_oldest_first()
    {
        var sink = Sink();
        var first = Payload(actions: [("open", "Aç")]);
        Notify(sink, first);
        for (var i = 0; i < WindowsToastSink.MaxTracked; i++)
        {
            var next = Payload(actions: [("open", "Aç")]);
            next["notification_id"] = Guid.NewGuid().ToString();
            Notify(sink, next);
        }

        _platform.Press(Id, ToastXml.ButtonArguments(Id, "open"));
        var newest = _platform.Shown[^1].Spec.Tag;
        _platform.Press(newest, ToastXml.ButtonArguments(newest, "open"));

        var entry = Assert.Single(_queue.Report())!;
        Assert.Equal(newest, entry["notification_id"]!.GetValue<string>());
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
