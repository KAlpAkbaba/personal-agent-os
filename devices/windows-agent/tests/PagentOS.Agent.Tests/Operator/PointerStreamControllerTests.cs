using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Companion.Audio.Timing;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// ADR-0199, the companion's rules for the pointer stream, each proven on the state machine
/// with a recording input, a foreground the test sets, a lock flag the test flips and a clock
/// the test moves — so the clamp, the rate, the three refusals and the held-button release
/// are exact assertions, not readings of a desktop. What the REAL input does to the REAL
/// pointer is the lab's job (<see cref="PointerStreamLabTests"/>).
/// </summary>
public sealed class PointerStreamControllerTests
{
    private const string Session = "sess-1";

    private sealed class RecordingPointerInput : IPointerStreamInput
    {
        public List<string> Calls { get; } = new();

        public Exception? Fail { get; set; }

        public void MoveBy(int dx, int dy)
        {
            Throw();
            Calls.Add($"move {dx},{dy}");
        }

        public void Button(PointerButton button, bool down)
        {
            Throw();
            Calls.Add($"{button.ToString().ToLowerInvariant()} {(down ? "down" : "up")}");
        }

        private void Throw()
        {
            if (Fail is not null)
            {
                throw Fail;
            }
        }
    }

    /// <summary>One desk: the seams the controller reads, all under the test's hand.</summary>
    private sealed class Desk : IDisposable
    {
        public WindowInfo? Foreground = Window("Adsız - Not Defteri");
        public bool Locked;
        public ManualTimeProvider Time { get; } = new();
        public RecordingPointerInput Input { get; } = new();
        public PointerStreamController Controller { get; }

        public Desk()
        {
            Controller = new PointerStreamController(Input, () => Foreground, () => Locked, Time, logger: null, housekeeping: false);
        }

        public JsonObject Begin(string session = Session, WindowInfo? target = null) => Controller.Begin(session, target ?? Foreground);

        public PointerBatchOutcome Apply(string session, params JsonNode[] frames) => Controller.Apply(Batch(session, frames));

        public PointerBatchOutcome Apply(params JsonNode[] frames) => Apply(Session, frames);

        public void Dispose() => Controller.Dispose();
    }

    private static WindowInfo Window(string title)
        => new("w-4242-1", new IntPtr(4242), 100, "notepad.exe", title, WindowInfo.StateNormal, new WindowRect(0, 0, 800, 600), true, "Notepad", false);

    private static JsonObject Batch(string session, params JsonNode[] frames)
        => new() { ["session"] = session, ["frames"] = new JsonArray(frames) };

    private static JsonObject Move(int dx, int dy, int seq = 0) => new() { ["t"] = "move", ["dx"] = dx, ["dy"] = dy, ["seq"] = seq };

    private static JsonObject Button(string button, string action) => new() { ["t"] = "button", ["button"] = button, ["action"] = action };

    private static JsonObject EndFrame() => new() { ["t"] = "end" };

    // ------------------------------------------------------------------ the frames

    [Fact]
    public void The_frame_parser_reads_the_adrs_shapes_verbatim_and_nothing_else()
    {
        Assert.True(PointerFrame.TryParse(JsonNode.Parse("""{"t":"move","dx":12,"dy":-7,"seq":42}"""), out var move));
        Assert.True(move.IsMove);
        Assert.Equal((12, -7, 42L), (move.Dx, move.Dy, move.Seq));
        // seq is optional on the wire (a coalesced move carries the last one, an early browser none)
        Assert.True(PointerFrame.TryParse(JsonNode.Parse("""{"t":"move","dx":0,"dy":0}"""), out _));
        Assert.True(PointerFrame.TryParse(JsonNode.Parse("""{"t":"button","button":"right","action":"click"}"""), out var click));
        Assert.True(click.IsButton);
        Assert.Equal(("right", "click"), (click.ButtonName, click.Action));
        Assert.True(PointerFrame.TryParse(JsonNode.Parse("""{"t":"end"}"""), out var end));
        Assert.True(end.IsEnd);
        // in-process construction (the lab, a command payload) parses the same way as wire JSON
        Assert.True(PointerFrame.TryParse(Move(3, 4, 5), out _));

        Assert.False(PointerFrame.TryParse(JsonNode.Parse("""{"t":"jump","dx":1,"dy":1}"""), out _));
        Assert.False(PointerFrame.TryParse(JsonNode.Parse("""{"t":"move","dx":"10","dy":0}"""), out _));
        Assert.False(PointerFrame.TryParse(JsonNode.Parse("""{"t":"move","dx":1.5,"dy":0}"""), out _));
        Assert.False(PointerFrame.TryParse(JsonNode.Parse("""{"t":"move","dx":1}"""), out _));
        Assert.False(PointerFrame.TryParse(JsonNode.Parse("""{"t":"button","button":"middle","action":"down"}"""), out _));
        Assert.False(PointerFrame.TryParse(JsonNode.Parse("""{"t":"button","button":"left","action":"tap"}"""), out _));
        Assert.False(PointerFrame.TryParse(JsonNode.Parse("""{"t":"button","button":"left"}"""), out _));
        Assert.False(PointerFrame.TryParse(JsonNode.Parse("\"move\""), out _));
        Assert.False(PointerFrame.TryParse(null, out _));
    }

    // ------------------------------------------------------------------ apply

    [Fact]
    public void A_move_inside_the_bound_is_applied_as_given_and_one_outside_is_clamped_to_200_per_axis()
    {
        using var desk = new Desk();
        desk.Begin();

        var outcome = desk.Apply(Move(50, -30), Move(500, -900), Move(-201, 200), Move(-200, 201));

        Assert.Equal((4, 0), (outcome.Applied, outcome.Dropped));
        Assert.Null(outcome.Reason);
        Assert.Equal(new[] { "move 50,-30", "move 200,-200", "move -200,200", "move -200,200" }, desk.Input.Calls);
        Assert.Equal(200, PointerStreamController.MaxDeltaPerFrame);
    }

    [Fact]
    public void A_button_down_up_pair_and_a_click_are_sent_in_order_and_counted_as_buttons()
    {
        using var desk = new Desk();
        desk.Begin();

        var outcome = desk.Apply(Button("left", "down"), Move(10, 0), Button("left", "up"), Button("right", "click"));

        Assert.Equal((4, 0), (outcome.Applied, outcome.Dropped));
        Assert.Equal(new[] { "left down", "move 10,0", "left up", "right down", "right up" }, desk.Input.Calls);
        Assert.False(desk.Controller.LeftHeld);

        var end = desk.Controller.End(Session);
        Assert.Equal((1, 3, 0), (end["moves"]!.GetValue<int>(), end["buttons"]!.GetValue<int>(), end["dropped"]!.GetValue<int>()));
        Assert.Empty(end["released"]!.AsArray());
    }

    [Fact]
    public void More_than_60_frames_in_a_second_are_dropped_not_queued_and_the_rate_refills_with_the_clock()
    {
        using var desk = new Desk();
        desk.Begin();
        Assert.Equal(60, PointerStreamController.MaxFramesPerSecond);

        // A full second's burst is allowed at once; the 61st is dropped, not delayed.
        var first = desk.Apply(Enumerable.Range(0, 61).Select(_ => (JsonNode)Move(1, 1)).ToArray());
        Assert.Equal((60, 1), (first.Applied, first.Dropped));
        Assert.Equal(60, desk.Input.Calls.Count);

        // Half a second later, half a second's worth has refilled.
        desk.Time.AdvanceMs(500);
        var second = desk.Apply(Enumerable.Range(0, 40).Select(_ => (JsonNode)Move(1, 1)).ToArray());
        Assert.Equal((30, 10), (second.Applied, second.Dropped));

        // A quiet two seconds refill the bucket to the cap and no further.
        desk.Time.Advance(TimeSpan.FromSeconds(2));
        var third = desk.Apply(Enumerable.Range(0, 64).Select(_ => (JsonNode)Move(1, 1)).ToArray());
        Assert.Equal((60, 4), (third.Applied, third.Dropped));

        var end = desk.Controller.End(Session);
        Assert.Equal(150, end["moves"]!.GetValue<int>());
        Assert.Equal(15, end["dropped"]!.GetValue<int>());
    }

    [Fact]
    public void Malformed_frames_are_dropped_and_counted_and_the_good_ones_beside_them_are_applied()
    {
        using var desk = new Desk();
        desk.Begin();

        var outcome = desk.Apply(
            JsonNode.Parse("""{"t":"jump"}""")!,
            JsonNode.Parse("""{"t":"move","dx":"10","dy":0}""")!,
            JsonNode.Parse("""{"t":"button","button":"middle","action":"down"}""")!,
            JsonNode.Parse("""{"t":"button","button":"left","action":"tap"}""")!,
            JsonValue.Create("move")!,
            Move(5, 5),
            JsonNode.Parse("""{"t":"move","dx":1.5,"dy":0}""")!);

        Assert.Equal((1, 6), (outcome.Applied, outcome.Dropped));
        Assert.Equal(new[] { "move 5,5" }, desk.Input.Calls);
        Assert.Equal(6, desk.Controller.End(Session)["dropped"]!.GetValue<int>());
    }

    [Fact]
    public void An_end_frame_closes_the_stream_and_what_follows_it_in_the_batch_has_no_stream()
    {
        using var desk = new Desk();
        desk.Begin();

        var outcome = desk.Apply(Move(1, 1), EndFrame(), Move(2, 2));

        Assert.Equal((2, 1), (outcome.Applied, outcome.Dropped));
        Assert.Equal(new[] { "move 1,1" }, desk.Input.Calls);
        Assert.Null(desk.Controller.OpenSession);

        var end = desk.Controller.End(Session);
        Assert.True(end["already_ended"]!.GetValue<bool>());
        Assert.Equal("frame_end", end["ended_by"]!.GetValue<string>());
        Assert.Equal(1, end["moves"]!.GetValue<int>());
    }

    [Fact]
    public void An_input_the_system_refuses_is_counted_as_dropped_never_thrown()
    {
        using var desk = new Desk();
        desk.Begin();
        desk.Input.Fail = new CapabilityException(ErrorClasses.UiStateChanged, "SendInput delivered 0 of 1", retryable: true);

        var outcome = desk.Apply(Move(1, 1), Button("left", "click"));

        Assert.Equal((0, 2), (outcome.Applied, outcome.Dropped));
        Assert.Empty(desk.Input.Calls);
    }

    // ------------------------------------------------------------------ refusals

    [Fact]
    public void With_no_stream_open_or_for_another_session_the_batch_sends_nothing_and_is_counted()
    {
        using var desk = new Desk();

        var before = desk.Apply(Move(10, 10), Button("left", "click"));
        Assert.Equal((0, 2, "no_stream"), (before.Applied, before.Dropped, before.Reason));
        Assert.Equal(1, desk.Controller.RefusedNoStream);

        desk.Begin("sess-1");
        var other = desk.Apply("sess-2", Move(10, 10));
        Assert.Equal((0, 1, "no_stream"), (other.Applied, other.Dropped, other.Reason));
        Assert.Equal(2, desk.Controller.RefusedNoStream);

        // a batch with no session field, or no frames array, is the same refusal
        Assert.Equal("no_stream", desk.Controller.Apply(new JsonObject { ["frames"] = new JsonArray(Move(1, 1)) }).Reason);
        Assert.Equal("no_stream", desk.Controller.Apply(new JsonObject { ["session"] = "sess-1" }).Reason);

        Assert.Empty(desk.Input.Calls);
        Assert.Equal(4, desk.Controller.BatchesReceived);
        // and the open stream's own count is untouched by batches that were not its
        Assert.Equal(0, desk.Controller.End("sess-1")["dropped"]!.GetValue<int>());
    }

    [Fact]
    public void The_shell_rule_is_the_title_marker_any_case_anywhere()
    {
        Assert.True(PointerStreamController.IsShellTitle("PersonalAgentOS - Google Chrome"));
        Assert.True(PointerStreamController.IsShellTitle("Kokpit | PagentOS"));
        Assert.True(PointerStreamController.IsShellTitle("pagentos"));
        Assert.True(PointerStreamController.IsShellTitle("PERSONALAGENTOS"));
        Assert.False(PointerStreamController.IsShellTitle("Adana Adliyesi 4 - YouTube - Google Chrome"));
        Assert.False(PointerStreamController.IsShellTitle("Adsız - Not Defteri"));
        Assert.False(PointerStreamController.IsShellTitle(string.Empty));
        Assert.False(PointerStreamController.IsShellTitle(null));
    }

    [Fact]
    public void With_the_shell_in_front_the_batch_is_refused_and_a_held_button_is_released()
    {
        using var desk = new Desk();
        desk.Begin();
        Assert.Equal(1, desk.Apply(Button("left", "down")).Applied);
        Assert.True(desk.Controller.LeftHeld);

        desk.Foreground = Window("PersonalAgentOS - Google Chrome");
        var refused = desk.Apply(Move(10, 10), Move(20, 20));

        Assert.Equal((0, 2, "shell_in_front"), (refused.Applied, refused.Dropped, refused.Reason));
        Assert.Equal(1, desk.Controller.RefusedShell);
        // exactly the release went out, and no move
        Assert.Equal(new[] { "left down", "left up" }, desk.Input.Calls);
        Assert.False(desk.Controller.LeftHeld);
        // the stream is still open: the shell going away lets the hand continue
        desk.Foreground = Window("YouTube - Google Chrome");
        Assert.Equal(1, desk.Apply(Move(3, 3)).Applied);

        var end = desk.Controller.End(Session);
        Assert.Equal((1, 1, 2), (end["moves"]!.GetValue<int>(), end["buttons"]!.GetValue<int>(), end["dropped"]!.GetValue<int>()));
    }

    [Fact]
    public void A_locked_session_or_no_foreground_refuses_the_batch_and_releases_a_held_button()
    {
        using var desk = new Desk();
        desk.Begin();
        desk.Apply(Button("right", "down"));

        desk.Locked = true;
        var locked = desk.Apply(Move(1, 1));
        Assert.Equal((0, 1, "locked"), (locked.Applied, locked.Dropped, locked.Reason));
        Assert.Equal(new[] { "right down", "right up" }, desk.Input.Calls);
        Assert.Equal(1, desk.Controller.RefusedLocked);

        desk.Locked = false;
        desk.Foreground = null;
        var none = desk.Apply(Move(1, 1));
        Assert.Equal("no_foreground", none.Reason);
        Assert.Equal(2, desk.Controller.RefusedLocked);
        Assert.Equal(2, desk.Input.Calls.Count);
    }

    // ------------------------------------------------------------------ ending

    [Fact]
    public void End_answers_the_counts_and_the_duration_and_ending_twice_or_an_unknown_session_is_idempotent()
    {
        using var desk = new Desk();
        var begin = desk.Begin(target: Window("YouTube - Google Chrome"));
        Assert.Equal(Session, begin["session"]!.GetValue<string>());
        Assert.Equal("w-4242-1", begin["window_id"]!.GetValue<string>());
        Assert.Null(begin["replaced"]);
        Assert.NotNull(begin["opened_at"]);

        desk.Apply(Move(1, 1), Move(2, 2), Button("left", "click"));
        desk.Time.AdvanceMs(1500);

        var end = desk.Controller.End(Session);
        Assert.True(end["ended"]!.GetValue<bool>());
        Assert.Equal("end", end["ended_by"]!.GetValue<string>());
        Assert.Equal(2, end["moves"]!.GetValue<int>());
        Assert.Equal(1, end["buttons"]!.GetValue<int>());
        Assert.Equal(0, end["dropped"]!.GetValue<int>());
        Assert.Equal(1500L, end["duration_ms"]!.GetValue<long>());
        Assert.Equal("w-4242-1", end["window_id"]!.GetValue<string>());
        Assert.Null(desk.Controller.OpenSession);

        var again = desk.Controller.End(Session);
        Assert.True(again["already_ended"]!.GetValue<bool>());
        Assert.Equal(2, again["moves"]!.GetValue<int>());

        var unknown = desk.Controller.End("never-opened");
        Assert.False(unknown["ended"]!.GetValue<bool>());
        Assert.Equal("unknown_session", unknown["reason"]!.GetValue<string>());
        Assert.Equal((0, 0, 0, 0L), (unknown["moves"]!.GetValue<int>(), unknown["buttons"]!.GetValue<int>(), unknown["dropped"]!.GetValue<int>(), unknown["duration_ms"]!.GetValue<long>()));
    }

    [Fact]
    public void The_stream_ends_itself_after_60_s_without_frames_and_releases_the_held_button()
    {
        using var desk = new Desk();
        desk.Begin();
        desk.Apply(Button("left", "down"));
        Assert.Equal(TimeSpan.FromSeconds(60), PointerStreamController.IdleTimeout);

        desk.Time.Advance(TimeSpan.FromSeconds(59));
        Assert.False(desk.Controller.ExpireIdle());
        Assert.Equal(Session, desk.Controller.OpenSession);
        // a frame inside the window keeps it alive
        desk.Apply(Move(1, 0));
        desk.Time.Advance(TimeSpan.FromSeconds(59));
        Assert.False(desk.Controller.ExpireIdle());

        desk.Time.Advance(TimeSpan.FromSeconds(2));
        Assert.True(desk.Controller.ExpireIdle());
        Assert.Null(desk.Controller.OpenSession);
        Assert.Equal("left up", desk.Input.Calls[^1]);
        Assert.False(desk.Controller.LeftHeld);

        var end = desk.Controller.End(Session);
        Assert.True(end["already_ended"]!.GetValue<bool>());
        Assert.Equal("timeout", end["ended_by"]!.GetValue<string>());
        Assert.Equal(new[] { "left" }, end["released"]!.AsArray().Select(n => n!.GetValue<string>()));

        // and the same rule fires on the next batch when no housekeeping tick came first
        desk.Begin("sess-2");
        desk.Apply("sess-2", Button("left", "down"));
        desk.Time.Advance(TimeSpan.FromSeconds(61));
        var late = desk.Apply("sess-2", Move(1, 1));
        Assert.Equal("no_stream", late.Reason);
        Assert.Equal("left up", desk.Input.Calls[^1]);
    }

    [Fact]
    public void A_second_begin_replaces_the_open_stream_releasing_its_button_and_names_it()
    {
        using var desk = new Desk();
        desk.Begin("sess-1");
        desk.Apply("sess-1", Button("left", "down"));

        var second = desk.Begin("sess-2");

        Assert.Equal("sess-1", second["replaced"]!.GetValue<string>());
        Assert.Equal("sess-2", desk.Controller.OpenSession);
        Assert.Equal("left up", desk.Input.Calls[^1]);
        Assert.Equal("replaced", desk.Controller.End("sess-1")["ended_by"]!.GetValue<string>());
        Assert.Equal(1, desk.Apply("sess-2", Move(1, 1)).Applied);
    }

    [Fact]
    public void EndAll_and_Dispose_release_a_held_button_and_a_disposed_controller_refuses_everything()
    {
        var desk = new Desk();
        desk.Begin("sess-1");
        desk.Apply("sess-1", Button("left", "down"));

        desk.Controller.EndAll("pipe_closed");
        Assert.Equal("left up", desk.Input.Calls[^1]);
        Assert.Null(desk.Controller.OpenSession);
        Assert.Equal("pipe_closed", desk.Controller.End("sess-1")["ended_by"]!.GetValue<string>());

        desk.Begin("sess-2");
        desk.Apply("sess-2", Button("right", "down"));
        desk.Controller.Dispose();
        Assert.Equal("right up", desk.Input.Calls[^1]);
        Assert.Equal("shutdown", desk.Controller.End("sess-2")["ended_by"]!.GetValue<string>());

        Assert.Equal("disposed", desk.Apply("sess-2", Move(1, 1)).Reason);
        var ex = Assert.Throws<CapabilityException>(() => desk.Begin("sess-3"));
        Assert.Equal(ErrorClasses.DependencyUnavailable, ex.ErrorClass);
        desk.Controller.Dispose(); // idempotent
    }

    // ------------------------------------------------------------------ through the operator's dispatch

    [Fact]
    public void Through_the_operator_the_trio_validates_its_payloads_and_a_command_path_batch_answers_its_counts()
    {
        var input = new RecordingPointerInput();
        using var lab = new OperatorLab(pointerStreamInput: input);

        var begin = lab.Exec(OperatorCapabilityNames.PointerStreamBegin, new JsonObject { ["session"] = "sess-op" });
        Assert.Equal("sess-op", begin["session"]!.GetValue<string>());
        Assert.Null(begin["window_id"]);
        Assert.Equal("sess-op", lab.Operator.PointerStream.OpenSession);

        // a window id the registry never issued is refused before anything opens
        var missing = lab.ExpectFailure(OperatorCapabilityNames.PointerStreamBegin, new JsonObject { ["session"] = "sess-x", ["window_id"] = "w-1-1" });
        Assert.Equal(ErrorClasses.UiTargetNotFound, missing.ErrorClass);
        Assert.Equal("sess-op", lab.Operator.PointerStream.OpenSession);

        Assert.Equal(ErrorClasses.ValidationError, lab.ExpectFailure(OperatorCapabilityNames.PointerStreamBegin, new JsonObject()).ErrorClass);
        Assert.Equal(ErrorClasses.ValidationError, lab.ExpectFailure(OperatorCapabilityNames.PointerStream, new JsonObject { ["session"] = "sess-op", ["frames"] = "x" }).ErrorClass);
        Assert.Equal(ErrorClasses.ValidationError, lab.ExpectFailure(OperatorCapabilityNames.PointerStreamEnd, new JsonObject()).ErrorClass);

        // the batch through the command path: same rules, the counts answered (what it did to
        // the pointer depends on the desk in front, so only the shape and the counting are claimed)
        var batch = lab.Exec(OperatorCapabilityNames.PointerStream, Batch("sess-op", Move(1, 1), JsonNode.Parse("""{"t":"jump"}""")!));
        Assert.Equal("sess-op", batch["session"]!.GetValue<string>());
        Assert.Equal(2, batch["applied"]!.GetValue<int>() + batch["dropped"]!.GetValue<int>());
        Assert.Equal(1, lab.Operator.PointerStream.BatchesReceived);

        var end = lab.Exec(OperatorCapabilityNames.PointerStreamEnd, new JsonObject { ["session"] = "sess-op" });
        Assert.True(end["ended"]!.GetValue<bool>());
        Assert.Null(lab.Operator.PointerStream.OpenSession);
        // every result passed the forbidden-key scan and carries the four receipt counts
        Assert.All(new[] { "moves", "buttons", "dropped", "duration_ms" }, key => Assert.NotNull(end[key]));
    }
}
