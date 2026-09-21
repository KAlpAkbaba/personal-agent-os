using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// ADR-0199, the pointer stream on the REAL desktop (M19_DIGITAL_OPERATOR_SPEC.md §5
/// discipline): a Notepad this lab launched, in front; the real <see cref="Win32PointerStreamInput"/>
/// moving the real pointer and pressing the real button; every claim read back from the
/// system — the cursor position after a relative move, the button's asynchronous state after
/// down and after up, the cursor NOT moving when the shell is in front. The desk is shared
/// with its owner: a foreground that is not the lab's Notepad is measured and NAMED in the
/// failure, never skipped past and never clicked into.
/// </summary>
[Collection(OperatorLabCollection.Name)]
public sealed class PointerStreamLabTests : IDisposable
{
    private const int VkLButton = 0x01;
    private const string Session = "lab-pointer-stream";

    private readonly OperatorLab _lab = new();

    public void Dispose() => _lab.Dispose();

    [DllImport("user32.dll")]
    private static extern bool GetCursorPos(out Point point);

    [DllImport("user32.dll")]
    private static extern short GetAsyncKeyState(int virtualKey);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool SetWindowTextW(IntPtr hwnd, string text);

    [StructLayout(LayoutKind.Sequential)]
    private struct Point
    {
        public int X;
        public int Y;
    }

    private static (int X, int Y) Cursor()
    {
        Assert.True(GetCursorPos(out var p), "GetCursorPos failed");
        return (p.X, p.Y);
    }

    private static bool LeftButtonDown() => (GetAsyncKeyState(VkLButton) & 0x8000) != 0;

    /// <summary>Poll the button state until it reads <paramref name="down"/> or the wait passes; the last reading is returned so the ASSERTION fails, not this helper.</summary>
    private static bool WaitForLeftButton(bool down)
    {
        var deadline = DateTime.UtcNow.AddMilliseconds(800);
        while (LeftButtonDown() != down && DateTime.UtcNow < deadline)
        {
            Thread.Sleep(20);
        }

        return LeftButtonDown();
    }

    /// <summary>
    /// Bring the lab's Notepad in front and PROVE it is: the stream acts on whatever is in
    /// front, so a move sent while someone else's window holds the desk would be a move on the
    /// owner's app. The intruder is named (see NotepadLifecycleTests for why a number is not
    /// a diagnosis).
    /// </summary>
    private void EnsureFront(string windowId)
    {
        _lab.Activate(windowId);
        var front = _lab.Operator.Registry.Foreground();
        Assert.True(
            front?.WindowId == windowId,
            $"the lab's Notepad is not in front; the foreground is {FocusGuard.Describe(front)} - "
            + "the operator lab needs a desktop nobody else is using (PAGENTOS_OPERATOR_LAB=0 skips it).");
    }

    /// <summary>Put the real pointer at (200,200) inside the window, through the guarded capability, so a delta stays on screen and on the lab's own window.</summary>
    private (int X, int Y) Park(string windowId)
    {
        var moved = _lab.Exec(OperatorCapabilityNames.PointerMove, new JsonObject { ["window_id"] = windowId, ["x"] = 200, ["y"] = 200 });
        return (moved["screen_x"]!.GetValue<int>(), moved["screen_y"]!.GetValue<int>());
    }

    private static JsonObject Batch(params JsonNode[] frames) => new() { ["session"] = Session, ["frames"] = new JsonArray(frames) };

    private static JsonObject Move(int dx, int dy) => new() { ["t"] = "move", ["dx"] = dx, ["dy"] = dy, ["seq"] = 1 };

    private static JsonObject Button(string action) => new() { ["t"] = "button", ["button"] = "left", ["action"] = action };

    [LabFact]
    public void A_relative_move_lands_within_two_pixels_of_the_delta_and_the_stream_end_counts_it()
    {
        var (_, windowId, _) = _lab.LaunchNotepad();
        EnsureFront(windowId);
        var begin = _lab.Exec(OperatorCapabilityNames.PointerStreamBegin, new JsonObject { ["session"] = Session, ["window_id"] = windowId });
        Assert.Equal(windowId, begin["window_id"]!.GetValue<string>());

        // The system cursor is the one thing here the lab does not own (a hand on the mouse,
        // a window raising itself); the claim is "the delta CAN be applied exactly", so it is
        // allowed one disturbance and asked again - never a wider tolerance.
        (int X, int Y) from = default, seen = default;
        var attempt = 0;
        PointerBatchOutcome outcome;
        while (true)
        {
            attempt++;
            EnsureFront(windowId);
            from = Park(windowId);
            outcome = _lab.Operator.PointerStream.Apply(Batch(Move(40, 25)));
            seen = Cursor();
            var offBy = Math.Max(Math.Abs(seen.X - (from.X + 40)), Math.Abs(seen.Y - (from.Y + 25)));
            if ((outcome.Applied == 1 && offBy <= 2) || attempt == 3)
            {
                break;
            }
        }

        Assert.True(
            outcome.Applied == 1,
            $"after {attempt} attempt(s) the move was not applied (reason {outcome.Reason ?? "-"}); the foreground is {FocusGuard.Describe(_lab.Operator.Registry.Foreground())}");
        Assert.InRange(seen.X, from.X + 40 - 2, from.X + 40 + 2);
        Assert.InRange(seen.Y, from.Y + 25 - 2, from.Y + 25 + 2);

        // and back, so the desk is left as found; a clamped delta lands on the bound, not beyond
        var back = _lab.Operator.PointerStream.Apply(Batch(Move(-40, -25)));
        Assert.Equal(1, back.Applied);
        var returned = Cursor();
        Assert.InRange(returned.X, from.X - 2, from.X + 2);
        Assert.InRange(returned.Y, from.Y - 2, from.Y + 2);

        var end = _lab.Exec(OperatorCapabilityNames.PointerStreamEnd, new JsonObject { ["session"] = Session });
        Assert.True(end["ended"]!.GetValue<bool>());
        Assert.Equal(attempt + 1, end["moves"]!.GetValue<int>());
        Assert.Equal(0, end["buttons"]!.GetValue<int>());
        Assert.True(end["duration_ms"]!.GetValue<long>() >= 0);
        Assert.Null(_lab.Operator.PointerStream.OpenSession);
    }

    [LabFact]
    public void A_button_down_up_pair_is_seen_by_the_system_and_a_button_still_held_at_end_is_released()
    {
        var (_, windowId, _) = _lab.LaunchNotepad();
        EnsureFront(windowId);
        Park(windowId);
        Assert.False(LeftButtonDown(), "the left button is already down before the lab pressed anything - a hand on the mouse?");
        _lab.Exec(OperatorCapabilityNames.PointerStreamBegin, new JsonObject { ["session"] = Session, ["window_id"] = windowId });

        try
        {
            EnsureFront(windowId);
            var down = _lab.Operator.PointerStream.Apply(Batch(Button("down")));
            Assert.True(down.Applied == 1, $"the button down was not applied (reason {down.Reason ?? "-"}); the foreground is {FocusGuard.Describe(_lab.Operator.Registry.Foreground())}");
            Assert.True(WaitForLeftButton(down: true), "after 'left down' the system does not report the left button down");
            Assert.True(_lab.Operator.PointerStream.LeftHeld);

            var up = _lab.Operator.PointerStream.Apply(Batch(Button("up")));
            Assert.Equal(1, up.Applied);
            Assert.False(WaitForLeftButton(down: false), "after 'left up' the system still reports the left button down");
            Assert.False(_lab.Operator.PointerStream.LeftHeld);

            // Held at the end of the stream: the end itself releases it - the invariant.
            Assert.Equal(1, _lab.Operator.PointerStream.Apply(Batch(Button("down"))).Applied);
            Assert.True(WaitForLeftButton(down: true));
            var end = _lab.Exec(OperatorCapabilityNames.PointerStreamEnd, new JsonObject { ["session"] = Session });
            Assert.Equal(new[] { "left" }, end["released"]!.AsArray().Select(n => n!.GetValue<string>()));
            Assert.Equal(3, end["buttons"]!.GetValue<int>());
            Assert.False(WaitForLeftButton(down: false), "the stream ended with the left button still down");
        }
        finally
        {
            // Whatever the assertions said: the lab never leaves a button down on the owner's desk.
            // Two layers on purpose: the controller's own release, and then a direct release
            // that does not depend on the controller's bookkeeping - the RED proof of
            // 2026-09-22 (the release on `end` mutated away) closed the stream WITHOUT
            // releasing, EndAll found nothing open, and the owner's real left button stayed
            // down until it was released by hand.
            _lab.Operator.PointerStream.EndAll("lab_teardown");
            ReleaseLeftIfDown();
        }
    }

    [DllImport("user32.dll")]
    private static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);

    private static void ReleaseLeftIfDown()
    {
        if (LeftButtonDown())
        {
            mouse_event(0x0004 /* MOUSEEVENTF_LEFTUP */, 0, 0, 0, UIntPtr.Zero);
        }
    }

    [LabFact]
    public void With_the_shell_in_front_a_batch_is_refused_and_the_real_pointer_does_not_move()
    {
        var (_, windowId, window) = _lab.LaunchNotepad();
        Assert.True(WindowRegistry.TryParseHandle(windowId, out var hwnd));
        var originalTitle = window["title"]!.GetValue<string>();
        EnsureFront(windowId);
        var parked = Park(windowId);
        _lab.Exec(OperatorCapabilityNames.PointerStreamBegin, new JsonObject { ["session"] = Session, ["window_id"] = windowId });

        // The lab's own Notepad wearing the shell's name: the same rule Cloud Core's
        // media-window resolution uses, applied to whatever is in front.
        Assert.True(SetWindowTextW(hwnd, "PagentOS lab shell - Not Defteri"), "SetWindowText on the lab's Notepad failed");
        try
        {
            EnsureFront(windowId);
            var front = _lab.Operator.Registry.Foreground();
            Assert.True(PointerStreamController.IsShellTitle(front?.Title), $"the renamed Notepad does not read as the shell: {FocusGuard.Describe(front)}");

            var refused = _lab.Operator.PointerStream.Apply(Batch(Move(40, 25), Move(40, 25)));

            Assert.Equal("shell_in_front", refused.Reason);
            Assert.Equal((0, 2), (refused.Applied, refused.Dropped));
            Assert.Equal(1, _lab.Operator.PointerStream.RefusedShell);
            Assert.Equal(parked, Cursor());
        }
        finally
        {
            SetWindowTextW(hwnd, originalTitle);
        }

        // and with no stream open, the same batch is refused without a desk being consulted
        var end = _lab.Exec(OperatorCapabilityNames.PointerStreamEnd, new JsonObject { ["session"] = Session });
        Assert.Equal(2, end["dropped"]!.GetValue<int>());
        var noStream = _lab.Operator.PointerStream.Apply(Batch(Move(40, 25)));
        Assert.Equal("no_stream", noStream.Reason);
        Assert.Equal(parked, Cursor());
    }
}
