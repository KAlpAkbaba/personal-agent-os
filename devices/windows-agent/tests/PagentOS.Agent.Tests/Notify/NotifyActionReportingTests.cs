using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.SessionCompanion.Notify;
using Xunit;

namespace PagentOS.Agent.Tests.Notify;

/// <summary>
/// B11-toast (row 370): a press on its way out of this device - the queue that repeats it, the
/// Device Service's projection that closes it, and the shared numbers both read from
/// <c>packages/protocol/desktop-notify.json</c> and the protocol schema.
/// </summary>
public sealed class NotifyActionReportingTests
{
    private const string Id = "6b1d0b2e-1f4a-4f2e-9d3c-2a5f9c8e7d10";

    private static string RepoFile(params string[] parts)
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null && !File.Exists(Path.Combine([directory.FullName, .. parts])))
        {
            directory = directory.Parent;
        }

        Assert.NotNull(directory);
        return Path.Combine([directory!.FullName, .. parts]);
    }

    private static JsonObject Contract()
        => (JsonObject)JsonNode.Parse(File.ReadAllText(RepoFile("packages", "protocol", "desktop-notify.json")))!;

    private static JsonObject Schema()
        => (JsonObject)JsonNode.Parse(File.ReadAllText(RepoFile("packages", "schemas", "device-protocol.schema.json")))!;

    private sealed class SteppedTime(DateTimeOffset start) : TimeProvider
    {
        public DateTimeOffset Now { get; set; } = start;

        public override DateTimeOffset GetUtcNow() => Now;
    }

    // --------------------------------------------------------------------- the numbers

    [Fact]
    public void The_queue_and_the_projection_use_the_contracts_numbers_and_field()
    {
        var transport = Contract()["action_event"]!["transport"]!;

        Assert.Equal(HeartbeatStatus.NotifyActions, transport["field"]!.GetValue<string>());
        Assert.Equal(NotifyActionQueue.MaxEntries, transport["max_entries"]!.GetValue<int>());
        Assert.Equal(HeartbeatStatus.MaxNotifyActions, transport["max_entries"]!.GetValue<int>());
        Assert.Equal(NotifyActionQueue.Transmissions, transport["transmissions"]!.GetValue<int>());

        var declared = Schema()["$defs"]!["deviceStatus"]!["properties"]![HeartbeatStatus.NotifyActions]!;
        Assert.Equal(HeartbeatStatus.MaxNotifyActions, declared["maxItems"]!.GetValue<int>());
        Assert.Equal(
            HeartbeatStatus.NotifyActionFields.Order(StringComparer.Ordinal),
            declared["items"]!["properties"]!.AsObject().Select(p => p.Key).Order(StringComparer.Ordinal));
        Assert.False(declared["items"]!["additionalProperties"]!.GetValue<bool>());
    }

    [Fact]
    public void The_answer_tokens_are_the_contracts()
    {
        var response = Contract()["response"]!;

        Assert.Equal(
            new[] { ToastSurfaces.Balloon, ToastSurfaces.Toast },
            response["surface"]!["values"]!.AsArray().Select(v => v!.GetValue<string>()).Order(StringComparer.Ordinal));
        Assert.Equal(
            ToastNotifierSettings.All.Order(StringComparer.Ordinal),
            response["notifier_setting"]!["values"]!.AsArray().Select(v => v!.GetValue<string>()).Order(StringComparer.Ordinal));
    }

    [Fact]
    public void The_user_state_tokens_the_platform_can_answer_are_the_contracts()
    {
        var declared = Contract()["response"]!["user_state"]!["values"]!.AsArray().Select(v => v!.GetValue<string>()).ToHashSet();
        var source = CompanionSources.Read(Path.Combine("Notify", "ToastPlatform.cs"));
        var start = source.IndexOf("public string? UserState()", StringComparison.Ordinal);
        var body = source[start..source.IndexOf("[DllImport", start, StringComparison.Ordinal)];
        var written = System.Text.RegularExpressions.Regex.Matches(body, "=> \"([a-z_0-9]+)\"")
            .Select(m => m.Groups[1].Value)
            .ToHashSet();

        Assert.Equal(declared, written);
    }

    // ----------------------------------------------------------------------- the queue

    [Fact]
    public void Each_press_rides_in_exactly_the_contracts_number_of_reports()
    {
        var time = new SteppedTime(new DateTimeOffset(2026, 9, 17, 11, 0, 0, 123, TimeSpan.Zero));
        var queue = new NotifyActionQueue(time);
        queue.Record(Id, "open");
        time.Now = time.Now.AddSeconds(10);

        var first = queue.Report();
        queue.Record(Id, "snooze");
        var counts = Enumerable.Range(0, 4).Select(_ => queue.Report().Count).ToArray();

        var entry = Assert.Single(first)!.AsObject();
        Assert.Equal(new[] { "notification_id", "action_id", "pressed_at" }, entry.Select(p => p.Key));
        Assert.Equal("2026-09-17T11:00:00.123Z", entry["pressed_at"]!.GetValue<string>());
        // open: reports 1, 2, 3; snooze: reports 2, 3, 4 (counts start at report 2).
        Assert.Equal(new[] { 2, 2, 1, 0 }, counts);
        Assert.Equal(0, queue.Pending);
        Assert.Equal(2, queue.Recorded);
    }

    [Fact]
    public void A_full_queue_drops_the_oldest_press_and_counts_it()
    {
        var queue = new NotifyActionQueue();
        for (var i = 0; i < NotifyActionQueue.MaxEntries + 3; i++)
        {
            queue.Record(Id, "a" + i);
        }

        var report = queue.Report();

        Assert.Equal(NotifyActionQueue.MaxEntries, report.Count);
        Assert.Equal("a3", report[0]!["action_id"]!.GetValue<string>());
        Assert.Equal(3, queue.Dropped);
    }

    [Fact]
    public async Task Concurrent_presses_and_reports_never_send_a_press_more_than_the_contract_allows()
    {
        var queue = new NotifyActionQueue();
        var seen = new System.Collections.Concurrent.ConcurrentDictionary<string, int>();
        var writer = Task.Run(() =>
        {
            for (var i = 0; i < 200; i++)
            {
                queue.Record(Id, "p" + i);
                if (i % 8 == 0)
                {
                    Thread.Yield();
                }
            }
        });
        var reader = Task.Run(() =>
        {
            while (!writer.IsCompleted || queue.Pending > 0)
            {
                foreach (var node in queue.Report())
                {
                    seen.AddOrUpdate(node!["action_id"]!.GetValue<string>(), 1, (_, n) => n + 1);
                }
            }
        });

        await Task.WhenAll(writer, reader).WaitAsync(TimeSpan.FromSeconds(30)); // hang guard only
        Assert.All(seen.Values, n => Assert.InRange(n, 1, NotifyActionQueue.Transmissions));
        Assert.Equal(200, queue.Recorded);
        if (queue.Dropped == 0)
        {
            // A reader that kept up saw every press, each exactly as often as the contract says.
            Assert.Equal(200, seen.Count);
            Assert.All(seen.Values, n => Assert.Equal(NotifyActionQueue.Transmissions, n));
        }
    }

    // ------------------------------------------------------------------ the projection

    private static JsonObject Entry(string? id = Id, string? action = "open", string? at = "2026-09-17T11:00:00.000Z")
    {
        var entry = new JsonObject();
        if (id is not null) entry["notification_id"] = id;
        if (action is not null) entry["action_id"] = action;
        if (at is not null) entry["pressed_at"] = at;
        return entry;
    }

    private static JsonObject? ProjectWith(JsonNode? list)
        => HeartbeatStatus.Project(new JsonObject { ["input_idle_s"] = 3.0, [HeartbeatStatus.NotifyActions] = list });

    [Fact]
    public void A_well_formed_list_passes_the_service_as_a_copy()
    {
        var list = new JsonArray(Entry(), Entry(action: "snooze"));

        var projected = ProjectWith(list)!;

        var sent = projected[HeartbeatStatus.NotifyActions]!.AsArray();
        Assert.Equal(2, sent.Count);
        Assert.Equal("snooze", sent[1]!["action_id"]!.GetValue<string>());
        Assert.NotSame(list, sent);
    }

    public static TheoryData<JsonNode?> RefusedLists()
    {
        var withExtra = Entry();
        withExtra["title"] = "Yedek alınamadı";
        var tooMany = new JsonArray();
        for (var i = 0; i <= HeartbeatStatus.MaxNotifyActions; i++)
        {
            tooMany.Add(Entry());
        }

        return new TheoryData<JsonNode?>
        {
            null,
            JsonValue.Create("open"),
            new JsonObject { ["open"] = true },
            tooMany,
            new JsonArray(withExtra),
            new JsonArray(Entry(at: null)),
            new JsonArray(Entry(), JsonValue.Create(3)),
            new JsonArray(new JsonObject { ["notification_id"] = Id, ["action_id"] = 7, ["pressed_at"] = "t" }),
            new JsonArray(new JsonObject { ["notification_id"] = Id, ["action_id"] = new JsonObject(), ["pressed_at"] = "t" }),
            new JsonArray(Entry(action: new string('x', HeartbeatStatus.MaxNestedStringLength + 1))),
            new JsonArray(Entry(id: string.Empty)),
        };
    }

    [Theory]
    [MemberData(nameof(RefusedLists))]
    public void Anything_else_drops_the_whole_list_and_never_sends_null(JsonNode? list)
    {
        var projected = ProjectWith(list)!;

        Assert.False(projected.ContainsKey(HeartbeatStatus.NotifyActions));
        Assert.Equal(3.0, projected["input_idle_s"]!.GetValue<double>());
    }

    [Fact]
    public void An_empty_list_is_sent_as_an_empty_list()
    {
        var projected = ProjectWith(new JsonArray())!;

        Assert.Empty(projected[HeartbeatStatus.NotifyActions]!.AsArray());
    }
}
