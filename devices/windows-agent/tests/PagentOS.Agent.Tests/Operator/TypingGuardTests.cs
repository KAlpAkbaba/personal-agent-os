using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// ADR-0082 addendum 2, finding 2: the focus guard is asked before EVERY batch of input, not
/// once per call. Proven twice — on the batch loop with a fake sender and a check that flips
/// after N batches (the exact count, no further batch), and in the lab with two real Notepads
/// where B is brought to the front while a 3000-character text is streaming into A.
/// </summary>
[Collection(OperatorLabCollection.Name)]
public sealed class TypingGuardTests : IDisposable
{
    private readonly OperatorLab _lab = new();

    public void Dispose() => _lab.Dispose();

    [Fact]
    public void The_batcher_asks_before_every_batch_and_stops_at_the_first_no_with_the_exact_count()
    {
        const int Chars = 3000;
        var events = Chars * 2;
        var sent = new List<(int Offset, int Count)>();
        var asks = 0;
        var ex = Assert.Throws<CapabilityException>(() => InputBatcher.Send(
            events,
            (offset, count) => sent.Add((offset, count)),
            () => ++asks <= 17,
            eventsSent => eventsSent / 2,
            Chars));

        Assert.Equal(ErrorClasses.FocusMismatch, ex.ErrorClass);
        Assert.True(ex.Retryable);
        Assert.Equal(17, sent.Count);
        Assert.Equal(18, asks);
        Assert.Equal(17 * InputBatcher.BatchSize / 2, ex.Detail["typed_chars"]);
        Assert.Equal(16 * InputBatcher.BatchSize / 2, ex.Detail["confirmed_chars"]);
        Assert.Equal(InputBatcher.BatchSize / 2, ex.Detail["uncertain_chars"]);
        Assert.Equal(Chars, ex.Detail["total_chars"]);
        Assert.Equal(17, ex.Detail["batches_sent"]);
        Assert.Contains($"typed_chars={17 * InputBatcher.BatchSize / 2}", ex.Message, StringComparison.Ordinal);
        Assert.Contains($"confirmed_chars={16 * InputBatcher.BatchSize / 2}", ex.Message, StringComparison.Ordinal);
        Assert.Equal(16 * InputBatcher.BatchSize, sent[^1].Offset);

        // A "no" before the first batch: nothing sent, typed_chars=0, nothing uncertain.
        sent.Clear();
        var first = Assert.Throws<CapabilityException>(() => InputBatcher.Send(events, (o, c) => sent.Add((o, c)), () => false, e => e / 2, Chars));
        Assert.Empty(sent);
        Assert.Equal(0, first.Detail["typed_chars"]);
        Assert.Equal(0, first.Detail["uncertain_chars"]);

        // A "yes" every time: every event goes, one ask per batch.
        sent.Clear();
        asks = 0;
        var batches = InputBatcher.Send(events, (o, c) => sent.Add((o, c)), () => { asks++; return true; }, e => e / 2, Chars);
        Assert.Equal((events + InputBatcher.BatchSize - 1) / InputBatcher.BatchSize, batches);
        Assert.Equal(batches, asks);
        Assert.Equal(events, sent.Sum(s => s.Count));
    }

    [LabFact]
    public void A_stream_stopped_by_the_synthesizer_is_re_described_with_both_windows_and_the_count()
    {
        var input = new StoppingInput();
        using var lab = new OperatorLab(input);
        var (_, a, _) = lab.LaunchNotepad();
        lab.Activate(a);

        var ex = lab.ExpectFailure(OperatorCapabilityNames.KeyboardType, new JsonObject { ["window_id"] = a, ["text"] = new string('x', 300) });

        Assert.Equal(ErrorClasses.FocusMismatch, ex.ErrorClass);
        Assert.True(ex.Retryable);
        Assert.Equal(96, ex.Detail["typed_chars"]);
        Assert.Contains(a, ex.Message, StringComparison.Ordinal);
        Assert.Contains("expected", ex.Message, StringComparison.Ordinal);
        Assert.Contains("typed_chars=96 of 300", ex.Message, StringComparison.Ordinal);
        Assert.True(input.GuardAnswered, "the synthesizer was handed a live guard that answers");
    }

    [LabFact]
    public void A_focus_change_mid_stream_stops_the_typing_and_the_front_window_receives_nothing()
    {
        // The steal races the stream: at most MaxTypedChars (4096, the protocol cap) go out in
        // 64-event batches, a few hundred milliseconds on this machine. On a loaded runner
        // SetForegroundWindow once took longer than the whole stream - an attempt that lands
        // AFTER the last batch proves nothing either way (the guard had nothing left to stop),
        // so it is repeated with fresh windows, at most three times. Every assertion on the
        // attempt that landed is exact; nothing is relaxed for the slow case.
        string detail = "";
        for (var attempt = 1; attempt <= 3; attempt++)
        {
            if (AttemptFocusChangeMidStream(out detail))
            {
                return;
            }
        }

        Assert.Fail($"the focus steal never landed mid-stream in 3 attempts; last: {detail}");
    }

    /// <summary>
    /// One attempt. <c>true</c> = the steal landed while the stream was running and every
    /// assertion held; <c>false</c> = inconclusive (the stream ended before the steal landed:
    /// A holds the whole text and nothing was refused). A landed steal that the guard did NOT
    /// stop, or any misplaced character, fails the test outright.
    /// </summary>
    private bool AttemptFocusChangeMidStream(out string detail)
    {
        var (_, a, _) = _lab.LaunchNotepad();
        var (_, b, _) = _lab.LaunchNotepad();
        _lab.Activate(a);
        var bHandle = _lab.Operator.Registry.Resolve(b).Handle;

        // MaxTypedChars distinct-ish characters, no newlines: what A holds afterwards must be an
        // exact prefix of this, and B must hold none of it.
        var text = string.Concat(Enumerable.Range(0, OperatorCapabilityNames.MaxTypedChars).Select(i => (char)('a' + (i % 26))));

        CapabilityException? refused = null;
        var typing = Task.Run(() =>
        {
            try
            {
                _lab.Exec(OperatorCapabilityNames.KeyboardType, new JsonObject { ["window_id"] = a, ["text"] = text });
            }
            catch (CapabilityException ex)
            {
                refused = ex;
            }
        });
        Thread.Sleep(100);
        // From another thread, the way another application would: a plain SetForegroundWindow
        // (this process is entitled - it sent the last input event), NOT through the operator
        // (whose single-flight gate would wait for the typing to finish) and NOT through
        // WindowActions.Activate, whose AttachThreadInput dance merges and splits the input
        // queues and, measured here, drops input already queued for A - an artefact no real
        // focus steal has. Bounded at 2 s, and abandoned as soon as the stream has ended.
        var landed = BringToFront(bHandle, () => typing.IsCompleted);
        var streamStillRunning = landed && !typing.IsCompleted;
        typing.GetAwaiter().GetResult();

        if (refused is null)
        {
            // Nothing was refused. Legitimate only when the whole text went to A before the
            // steal took effect - then the attempt is inconclusive, never a pass.
            var aAll = _lab.ReadDocument(a).Value;
            var bAll = _lab.ReadDocument(b).Value;
            Assert.True(aAll == text && bAll.Length == 0, $"the stream was not refused, yet A holds {aAll.Length} of {text.Length} chars and B holds {bAll.Length} [{bAll}] (steal landed: {landed}, stream running at the steal: {streamStillRunning}, foreground 0x{GetForegroundWindow():X})");
            detail = $"inconclusive: the stream ended before the steal landed (landed={landed}, foreground 0x{GetForegroundWindow():X})";
            return false;
        }

        var ex = refused;
        Assert.True(landed, $"the guard refused although the steal never landed: {ex.Message}");
        Assert.Equal(ErrorClasses.FocusMismatch, ex.ErrorClass);
        Assert.True(ex.Retryable);
        var typed = Assert.IsType<int>(ex.Detail["typed_chars"]);
        var confirmed = Assert.IsType<int>(ex.Detail["confirmed_chars"]);
        var uncertain = Assert.IsType<int>(ex.Detail["uncertain_chars"]);
        Assert.InRange(typed, 1, text.Length - 1);
        Assert.Equal(0, typed % (InputBatcher.BatchSize / 2));
        Assert.Equal(InputBatcher.BatchSize / 2, uncertain);
        Assert.Equal(typed - uncertain, confirmed);
        Assert.Contains($"typed_chars={typed} of {text.Length}", ex.Message, StringComparison.Ordinal);
        Assert.True(ex.Message.Contains(a, StringComparison.Ordinal), $"A ({a}) not named: {ex.Message}");
        Assert.True(ex.Message.Contains(b, StringComparison.Ordinal), $"B ({b}) not named: {ex.Message}");

        // Read both back. Everything handed over before the change is in A, except possibly
        // the last batch, which Windows assigned to whichever thread retrieved it - the
        // measured bound the refusal names as uncertain_chars. Nothing sent after the change
        // exists anywhere: A + B is EXACTLY the typed prefix, and B is at most one batch.
        var deadline = DateTime.UtcNow.AddSeconds(4);
        string aValue, bValue;
        do
        {
            Thread.Sleep(200);
            aValue = _lab.ReadDocument(a).Value;
            bValue = _lab.ReadDocument(b).Value;
        }
        while (aValue.Length + bValue.Length < typed && DateTime.UtcNow < deadline);

        Assert.True(bValue.Length <= uncertain, $"B received {bValue.Length} chars [{bValue}], more than uncertain_chars={uncertain}; A holds {aValue.Length} of typed_chars={typed}");
        Assert.True(aValue.Length >= confirmed, $"A holds {aValue.Length} chars, fewer than confirmed_chars={confirmed}; B holds {bValue.Length}");
        Assert.True(aValue + bValue == text[..typed], $"A ({aValue.Length}) + B ({bValue.Length}) is not the typed prefix ({typed}); A=[{aValue[^Math.Min(40, aValue.Length)..]}] B=[{bValue}]");
        detail = "landed";
        return true;
    }

    private static bool BringToFront(IntPtr hwnd, Func<bool> abandon)
    {
        var deadline = DateTime.UtcNow.AddMilliseconds(2000);
        while (DateTime.UtcNow < deadline && !abandon())
        {
            SetForegroundWindow(hwnd);
            if (GetForegroundWindow() == hwnd)
            {
                return true;
            }

            Thread.Sleep(10);
        }

        return GetForegroundWindow() == hwnd;
    }

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr hwnd);

    [System.Runtime.InteropServices.DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();

    /// <summary>A synthesizer that sends 96 characters' worth of batches, then reports the guard said no — the batcher's contract, without input.</summary>
    private sealed class StoppingInput : IInputSynthesizer
    {
        public bool GuardAnswered { get; private set; }

        public void TypeText(string text, Func<bool> stillTargeted)
        {
            GuardAnswered = stillTargeted();
            var asks = 0;
            InputBatcher.Send(text.Length * 2, (_, _) => { }, () => ++asks <= 3, eventsSent => eventsSent / 2, text.Length);
        }

        public void PressKey(string key, Func<bool> stillTargeted)
        {
        }

        public void Shortcut(IReadOnlyList<string> keys, Func<bool> stillTargeted)
        {
        }

        public void MoveTo(int screenX, int screenY)
        {
        }

        public void Click(int screenX, int screenY, PointerButton button, int clicks)
        {
        }

        public void Scroll(int screenX, int screenY, int notches)
        {
        }
    }
}
