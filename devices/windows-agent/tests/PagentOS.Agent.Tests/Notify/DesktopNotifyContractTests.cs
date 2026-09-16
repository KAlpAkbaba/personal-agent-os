using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Notify;
using Xunit;

namespace PagentOS.Agent.Tests.Notify;

/// <summary>
/// B11 requirement 369/370: <c>packages/protocol/desktop-notify.json</c>, read by THIS
/// device's real parser.
/// </summary>
/// <remarks>
/// The four capabilities that were not written as a contract first — file.search roots, the
/// app manifest, the native manifest, the device protocol schema — each shipped with both
/// halves' suites green and neither half able to talk to the other. This one was written
/// before either half existed, so these tests read the shared file rather than restating
/// what this device believes it says.
/// </remarks>
public sealed class DesktopNotifyContractTests
{
    private static JsonObject Contract()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null
               && !File.Exists(Path.Combine(directory.FullName, "packages", "protocol", "desktop-notify.json")))
        {
            directory = directory.Parent;
        }

        Assert.NotNull(directory);
        var path = Path.Combine(directory!.FullName, "packages", "protocol", "desktop-notify.json");
        return (JsonObject)JsonNode.Parse(File.ReadAllText(path))!;
    }

    private static int Limit(string field)
        => Contract()["request"]![field]!["max_chars"]!.GetValue<int>();

    private static JsonObject Payload(
        string? id = "6b1d0b2e-1f4a-4f2e-9d3c-2a5f9c8e7d10",
        string? title = "Rapor hazır",
        string? body = "Üç klasör karşılaştırması bitti efendim.",
        string? priority = null,
        JsonArray? actions = null,
        string? groupKey = null)
    {
        var payload = new JsonObject();
        if (id is not null) payload["notification_id"] = id;
        if (title is not null) payload["title"] = title;
        if (body is not null) payload["body"] = body;
        if (priority is not null) payload["priority"] = priority;
        if (groupKey is not null) payload["group_key"] = groupKey;
        if (actions is not null) payload["actions"] = actions;
        return payload;
    }

    private sealed class RecordingSink : IToastSink
    {
        public ToastRequest? Last { get; private set; }

        public ToastOutcome Outcome { get; set; } = ToastOutcome.Ok();

        public ToastOutcome Show(ToastRequest request)
        {
            Last = request;
            return Outcome;
        }
    }

    private static NotifyCapabilities Capability(IToastSink sink)
        => new(sink, NullLogger.Instance);

    // ---------------------------------------------------------------- the contract

    [Fact]
    public void TheDeviceLimitsAreTheContractsOwnNumbers()
    {
        // Restating them would be the whole failure mode: the Cloud Core enforces the
        // contract's number and the device enforces its memory of it.
        Assert.Equal(NotifyCapabilities.MaxTitleChars, Limit("title"));
        Assert.Equal(NotifyCapabilities.MaxBodyChars, Limit("body"));
        Assert.Equal(
            NotifyCapabilities.MaxActions,
            Contract()["request"]!["actions"]!["max_items"]!.GetValue<int>());
        Assert.Equal(
            NotifyCapabilities.MaxGroupKeyChars,
            Contract()["request"]!["group_key"]!["max_chars"]!.GetValue<int>());
    }

    [Fact]
    public void TheDeviceAdvertisesTheCapabilityTheContractNames()
    {
        var capability = Contract()["capability"]!.GetValue<string>();

        Assert.Equal(AgentCapabilities.DesktopNotify, capability);
        Assert.Contains(capability, AgentCapabilities.Ambient);
    }

    [Fact]
    public void EveryRefusalReasonTheContractNamesExistsHere()
    {
        var reasons = Contract()["response"]!["reason"]!["values"]!.AsArray()
            .Select(node => node!.GetValue<string>())
            .ToHashSet();

        Assert.Equal(
            reasons,
            new HashSet<string>
            {
                NotifyCapabilities.ReasonNoInteractiveSession,
                NotifyCapabilities.ReasonNotificationsDisabled,
                NotifyCapabilities.ReasonShellUnavailable,
                NotifyCapabilities.ReasonInvalidPayload,
            });
    }

    // ------------------------------------------------------ what the device accepts

    [Fact]
    public void TheShapeTheCloudCoreSendsIsAccepted()
    {
        var sink = new RecordingSink();

        var answer = Capability(sink).Notify(Payload());

        Assert.True(answer["shown"]!.GetValue<bool>());
        Assert.Equal("Rapor hazır", sink.Last!.Title);
    }

    [Fact]
    public void ThreeActionButtonsAreCarriedThrough()
    {
        var sink = new RecordingSink();
        var actions = new JsonArray(
            new JsonObject { ["id"] = "open", ["label"] = "Aç" },
            new JsonObject { ["id"] = "snooze", ["label"] = "Ertele" },
            new JsonObject { ["id"] = "dismiss", ["label"] = "Kapat" });

        Capability(sink).Notify(Payload(actions: actions));

        Assert.Equal(new[] { "open", "snooze", "dismiss" }, sink.Last!.Actions.Select(a => a.Id));
    }

    // ------------------------------------------------------ what the device refuses

    [Fact]
    public void AFourthButtonIsRefusedRatherThanDroppedSilently()
    {
        var sink = new RecordingSink();
        var actions = new JsonArray(
            new JsonObject { ["id"] = "a", ["label"] = "A" },
            new JsonObject { ["id"] = "b", ["label"] = "B" },
            new JsonObject { ["id"] = "c", ["label"] = "C" },
            new JsonObject { ["id"] = "d", ["label"] = "D" });

        var answer = Capability(sink).Notify(Payload(actions: actions));

        Assert.False(answer["shown"]!.GetValue<bool>());
        Assert.Equal(NotifyCapabilities.ReasonInvalidPayload, answer["reason"]!.GetValue<string>());
        Assert.Null(sink.Last);
    }

    [Fact]
    public void ATitleLongerThanTheContractAllowsIsRefusedNotTruncated()
    {
        // What the owner reads is the Cloud Core's decision; a silently shortened sentence
        // is a different sentence.
        var answer = Capability(new RecordingSink()).Notify(Payload(title: new string('x', 200)));

        Assert.False(answer["shown"]!.GetValue<bool>());
    }

    [Fact]
    public void AnActionIdOutsideTheClosedVocabularyIsRefused()
    {
        var actions = new JsonArray(new JsonObject { ["id"] = "Aç Dosyayı", ["label"] = "Aç" });

        var answer = Capability(new RecordingSink()).Notify(Payload(actions: actions));

        Assert.False(answer["shown"]!.GetValue<bool>());
    }

    [Fact]
    public void ANotificationIdThatIsNotAUuidIsRefused()
    {
        var answer = Capability(new RecordingSink()).Notify(Payload(id: "the-latest-one"));

        Assert.False(answer["shown"]!.GetValue<bool>());
    }

    // --------------------------------------------------------- what it answers back

    [Fact]
    public void NotShownCarriesTheReasonAndTheNotificationId()
    {
        // The Cloud Core steps down its ladder on this, and attributes a late answer to the
        // notification it belonged to rather than to whichever one is current.
        var sink = new RecordingSink
        {
            Outcome = ToastOutcome.NotShown(NotifyCapabilities.ReasonNoInteractiveSession),
        };

        var answer = Capability(sink).Notify(Payload());

        Assert.False(answer["shown"]!.GetValue<bool>());
        Assert.Equal(
            NotifyCapabilities.ReasonNoInteractiveSession,
            answer["reason"]!.GetValue<string>());
        Assert.Equal("6b1d0b2e-1f4a-4f2e-9d3c-2a5f9c8e7d10", answer["notification_id"]!.GetValue<string>());
    }

    [Fact]
    public void ASinkThatThrowsIsNotAllowedToKillTheCompanion()
    {
        // A notification is the least important thing this process does; taking the session
        // companion down over one would be the wrong trade every time.
        // Regression (B11-toast): this test's sink used to RETURN not-shown, so it passed while
        // Notify had no try/catch at all and a real throw failed the whole command.
        var answer = Capability(new ThrowingSink()).Notify(Payload());

        Assert.False(answer["shown"]!.GetValue<bool>());
        Assert.Equal(NotifyCapabilities.ReasonShellUnavailable, answer["reason"]!.GetValue<string>());
        Assert.Equal("InvalidOperationException", answer["detail"]!.GetValue<string>());
    }

    private sealed class ThrowingSink : IToastSink
    {
        public ToastOutcome Show(ToastRequest request)
            => throw new InvalidOperationException("the shell went away");
    }

    [Fact]
    public void AShownAnswerStillCarriesItsDetailAndSurface()
    {
        // Regression (B11-toast): detail was written only for shown:false, so the balloon's
        // "actions_not_rendered" - the one thing row 370 needed the Cloud Core to know - never
        // left the device.
        var sink = new RecordingSink
        {
            Outcome = new ToastOutcome(true, null, "actions_not_rendered") { Surface = ToastSurfaces.Balloon, ActionsRendered = 0 },
        };

        var answer = Capability(sink).Notify(Payload(actions: new JsonArray(new JsonObject { ["id"] = "open", ["label"] = "Aç" })));

        Assert.True(answer["shown"]!.GetValue<bool>());
        Assert.Equal("actions_not_rendered", answer["detail"]!.GetValue<string>());
        Assert.Equal("balloon", answer["surface"]!.GetValue<string>());
        Assert.Equal(0, answer["actions_rendered"]!.GetValue<int>());
        Assert.False(answer.ContainsKey("reason"));
    }

    [Fact]
    public void AnActionIdWithATrailingNewlineIsRefused()
    {
        // Regression (B11-toast): `$` in a .NET regex also matches before a final "\n", so
        // "open\n" passed the closed vocabulary and would have reached the toast XML.
        var actions = new JsonArray(new JsonObject { ["id"] = "open\n", ["label"] = "Aç" });

        var answer = Capability(new RecordingSink()).Notify(Payload(actions: actions));

        Assert.False(answer["shown"]!.GetValue<bool>());
        Assert.Equal(NotifyCapabilities.ReasonInvalidPayload, answer["reason"]!.GetValue<string>());
    }

    [Fact]
    public void NothingInTheNotifyFolderCanRunOpenOrLaunchWhatAButtonCarries()
    {
        // Row 370's safety claim, held by reading the code: a press is an action id queued for
        // the Cloud Core, never a command line, a URL or a protocol activation.
        var files = Directory.GetFiles(Path.Combine(Support.CompanionSources.Directory(), "Notify"), "*.cs");
        Assert.NotEmpty(files);
        string[] forbidden =
        [
            "Process.Start", "ProcessStartInfo", "ShellExecute", "Launcher.", "UseShellExecute",
            "\"protocol\"", "activationType=\\\"protocol", "Uri(", "CreateProcess", "cmd.exe", "powershell",
        ];
        foreach (var file in files)
        {
            var text = File.ReadAllText(file);
            foreach (var name in forbidden)
            {
                Assert.False(text.Contains(name, StringComparison.OrdinalIgnoreCase), $"{Path.GetFileName(file)} contains {name}");
            }
        }
    }
}
