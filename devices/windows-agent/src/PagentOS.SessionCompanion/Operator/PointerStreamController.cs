using System.Globalization;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion.Operator;

/// <summary>
/// The two things the pointer stream does to the owner's desktop (ADR-0199): move the pointer
/// by a delta, and press or release a button. Behind an interface so the controller's rules
/// — the clamp, the rate, the refusals, the held-button release — are proven with a recorder
/// that says exactly what was sent, and so a refused batch is proven to have sent NOTHING.
/// Nothing here types, scrolls or reads.
/// </summary>
public interface IPointerStreamInput
{
    /// <summary>Move the pointer by (<paramref name="dx"/>, <paramref name="dy"/>) screen pixels, pixel-exact.</summary>
    void MoveBy(int dx, int dy);

    /// <summary>Press (<paramref name="down"/> = true) or release a button where the pointer is.</summary>
    void Button(PointerButton button, bool down);
}

/// <summary>
/// The companion-side receiver of a <c>pointer.stream</c> batch: what <see cref="CompanionRuntime"/>
/// hands a one-way request to, and what it tells when its pipe closes.
/// </summary>
public interface IPointerStreamApplier
{
    /// <summary>Applies one batch payload <c>{session, frames}</c>. Never throws; every frame is either applied or counted as dropped.</summary>
    PointerBatchOutcome Apply(JsonObject payload);

    /// <summary>Ends whatever stream is open, releasing any held button, because the thing that fed it is gone.</summary>
    void EndAll(string reason);
}

/// <summary>What one batch did: frames applied, frames dropped, and — when the whole batch was refused — why.</summary>
public sealed record PointerBatchOutcome(int Applied, int Dropped, string? Reason)
{
    public JsonObject ToJson(string? session) => new()
    {
        ["session"] = session,
        ["applied"] = Applied,
        ["dropped"] = Dropped,
        ["reason"] = Reason,
    };
}

/// <summary>
/// The pointer stream's state machine (ADR-0199, "Companion"): at most ONE mouse session open
/// at a time — the owner has one hand on the mouse — opened by <c>pointer.stream_begin</c>,
/// fed by <c>pointer.stream</c> batches, closed by <c>pointer.stream_end</c>, by an
/// <c>{"t":"end"}</c> frame, by <see cref="IdleTimeout"/> without frames, by a replacing
/// begin, by the pipe closing or by the companion shutting down. The rules, each proven on
/// its own:
/// <list type="bullet">
/// <item>a move is clamped to ±<see cref="MaxDeltaPerFrame"/> per frame and applied
/// pixel-exact; more than <see cref="MaxFramesPerSecond"/> frames in a second are dropped, not
/// queued — a hand is live, and a queue would be a pointer that keeps moving after the hand
/// stopped;</item>
/// <item>a batch is refused — every frame dropped and counted, nothing sent — when no stream
/// is open, when it names another session, when the session is locked, or when the window in
/// FRONT is the shell's own (its title carries <c>PersonalAgentOS</c> or <c>PagentOS</c>, the
/// rule Cloud Core's media-window resolution uses): the owner's mouse never lands in the
/// cockpit by a gesture. A refusal also releases a held button, so a drag cannot continue
/// into a place the stream may not act on;</item>
/// <item>a held button NEVER survives the stream: whichever way it ends, a <c>left up</c> (and
/// a <c>right up</c>) is sent for every button still down. This is the one input the
/// controller sends on its own initiative, and it is the opposite of an action;</item>
/// <item>nothing is stored: counts only, and the last few closed streams' summaries so a
/// late <c>pointer.stream_end</c> still gets its numbers.</item>
/// </list>
/// The controller takes the input, the foreground reader, the lock probe and the clock as
/// seams, so every rule above is a deterministic test; the lab proves the real input lands.
/// </summary>
public sealed class PointerStreamController : IPointerStreamApplier, IDisposable
{
    /// <summary>ADR-0199: the largest move one frame may carry, in either axis, after clamping.</summary>
    public const int MaxDeltaPerFrame = 200;

    /// <summary>ADR-0199: the most frames applied in any one second; the rest of that second is dropped.</summary>
    public const int MaxFramesPerSecond = 60;

    /// <summary>ADR-0199: a stream that receives no frame for this long ends itself.</summary>
    public static readonly TimeSpan IdleTimeout = TimeSpan.FromSeconds(60);

    /// <summary>How often the idle rule is checked when no frame arrives to trigger it.</summary>
    public static readonly TimeSpan HousekeepingInterval = TimeSpan.FromSeconds(5);

    /// <summary>How many closed streams keep their summary for a late end.</summary>
    public const int ClosedSummariesKept = 8;

    private static readonly string[] ShellTitleMarkers = ["PersonalAgentOS", "PagentOS"];

    private readonly IPointerStreamInput _input;
    private readonly Func<WindowInfo?> _foreground;
    private readonly Func<bool> _sessionLocked;
    private readonly TimeProvider _time;
    private readonly ILogger? _logger;
    private readonly object _lock = new();
    private readonly List<(string Session, JsonObject Summary)> _closed = new();
    private readonly Timer? _housekeeping;
    private OpenStream? _open;
    private int _batchesReceived;
    private int _refusedNoStream;
    private int _refusedLocked;
    private int _refusedShell;
    private int _disposed;

    public PointerStreamController(
        IPointerStreamInput input,
        Func<WindowInfo?> foreground,
        Func<bool> sessionLocked,
        TimeProvider? time = null,
        ILogger? logger = null,
        bool housekeeping = true)
    {
        _input = input;
        _foreground = foreground;
        _sessionLocked = sessionLocked;
        _time = time ?? TimeProvider.System;
        _logger = logger;
        if (housekeeping)
        {
            _housekeeping = new Timer(_ => SafeExpireIdle(), null, HousekeepingInterval, HousekeepingInterval);
        }
    }

    /// <summary>The open stream's session id, or null.</summary>
    public string? OpenSession
    {
        get
        {
            lock (_lock)
            {
                return _open?.Session;
            }
        }
    }

    /// <summary>Whether the open stream currently holds the left button down (a drag in progress).</summary>
    public bool LeftHeld
    {
        get
        {
            lock (_lock)
            {
                return _open?.LeftDown == true;
            }
        }
    }

    /// <summary>Batches handed to <see cref="Apply"/> since start, accepted or refused (telemetry; asserted by tests).</summary>
    public int BatchesReceived => _batchesReceived;

    /// <summary>Batches refused because no stream was open or the batch named another session.</summary>
    public int RefusedNoStream => _refusedNoStream;

    /// <summary>Batches refused because the session was locked (or had no usable foreground).</summary>
    public int RefusedLocked => _refusedLocked;

    /// <summary>Batches refused because the shell's own window was in front.</summary>
    public int RefusedShell => _refusedShell;

    /// <summary>
    /// The shell rule, pure and public: a foreground title that carries <c>PersonalAgentOS</c>
    /// or <c>PagentOS</c> (any case, anywhere — a browser tab title reads
    /// "PersonalAgentOS - Google Chrome") is the cockpit, and the stream does not act on it.
    /// </summary>
    public static bool IsShellTitle(string? title)
        => title is not null && ShellTitleMarkers.Any(marker => title.Contains(marker, StringComparison.OrdinalIgnoreCase));

    /// <summary>
    /// Opens a mouse session. An already-open stream is ended first ("replaced", its held
    /// button released) and named in the answer, because the thing that opens streams — the
    /// owner holding a pinch — cannot hold two.
    /// </summary>
    public JsonObject Begin(string session, WindowInfo? target)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(session);
        lock (_lock)
        {
            ThrowIfDisposed();
            string? replaced = null;
            if (_open is not null)
            {
                replaced = _open.Session;
                CloseLocked("replaced");
            }

            var now = _time.GetTimestamp();
            _open = new OpenStream(session, target?.WindowId, now, _time.GetUtcNow(), MaxFramesPerSecond);
            _logger?.LogInformation("pointer stream {Session} opened (target {Window}{Replaced})", session, target?.WindowId ?? "-", replaced is null ? string.Empty : $", replaced {replaced}");
            return new JsonObject
            {
                ["session"] = session,
                ["window_id"] = target?.WindowId,
                ["opened_at"] = _open.OpenedAtUtc.ToString("O", CultureInfo.InvariantCulture),
                ["replaced"] = replaced,
            };
        }
    }

    /// <summary>
    /// Closes the stream and answers its counts. A stream that already ended on its own (the
    /// idle rule, an <c>end</c> frame, a replacement) still answers its numbers, marked
    /// <c>already_ended</c>; a session this controller never saw answers zeros with
    /// <c>ended: false</c> — ending what is not there is idempotent, never an error.
    /// </summary>
    public JsonObject End(string session)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(session);
        lock (_lock)
        {
            if (_open is not null && string.Equals(_open.Session, session, StringComparison.Ordinal))
            {
                return CloseLocked("end");
            }

            var closed = _closed.FindLast(c => string.Equals(c.Session, session, StringComparison.Ordinal));
            if (closed.Summary is not null)
            {
                var summary = (JsonObject)closed.Summary.DeepClone();
                summary["already_ended"] = true;
                return summary;
            }

            return new JsonObject
            {
                ["session"] = session,
                ["ended"] = false,
                ["reason"] = "unknown_session",
                ["moves"] = 0,
                ["buttons"] = 0,
                ["dropped"] = 0,
                // A long, like the real summary's: an in-process JsonValue built from an int
                // refuses GetValue<long>() (the M19 JsonValue trap), and a receipt reader
                // must not see two number shapes for one field.
                ["duration_ms"] = 0L,
            };
        }
    }

    /// <inheritdoc />
    public PointerBatchOutcome Apply(JsonObject payload)
    {
        Interlocked.Increment(ref _batchesReceived);
        var session = payload["session"] is JsonValue s && s.GetValueKind() == JsonValueKind.String ? s.GetValue<string>() : null;
        var frames = payload["frames"] as JsonArray;
        var count = frames?.Count ?? 0;

        lock (_lock)
        {
            if (_disposed != 0)
            {
                return new PointerBatchOutcome(0, count, "disposed");
            }

            ExpireIdleLocked();
            var open = _open;
            if (open is null || session is null || frames is null || !string.Equals(open.Session, session, StringComparison.Ordinal))
            {
                Interlocked.Increment(ref _refusedNoStream);
                return new PointerBatchOutcome(0, count, "no_stream");
            }

            if (SafeLocked())
            {
                Interlocked.Increment(ref _refusedLocked);
                open.Dropped += count;
                ReleaseHeldLocked(open, "locked");
                return new PointerBatchOutcome(0, count, "locked");
            }

            var foreground = SafeForeground();
            if (foreground is null)
            {
                Interlocked.Increment(ref _refusedLocked);
                open.Dropped += count;
                ReleaseHeldLocked(open, "no_foreground");
                return new PointerBatchOutcome(0, count, "no_foreground");
            }

            if (IsShellTitle(foreground.Title))
            {
                Interlocked.Increment(ref _refusedShell);
                open.Dropped += count;
                ReleaseHeldLocked(open, "shell_in_front");
                return new PointerBatchOutcome(0, count, "shell_in_front");
            }

            var applied = 0;
            var dropped = 0;
            for (var i = 0; i < frames.Count; i++)
            {
                if (_open != open)
                {
                    // An end frame closed the stream mid-batch: what follows has no stream.
                    dropped++;
                    continue;
                }

                if (!PointerFrame.TryParse(frames[i], out var frame))
                {
                    dropped++;
                    open.Dropped++;
                    continue;
                }

                if (frame.IsEnd)
                {
                    CloseLocked("frame_end");
                    applied++;
                    continue;
                }

                if (!open.TakeToken(_time))
                {
                    dropped++;
                    open.Dropped++;
                    continue;
                }

                if (ApplyFrameLocked(open, frame))
                {
                    applied++;
                    open.LastFrameAt = _time.GetTimestamp();
                }
                else
                {
                    dropped++;
                    open.Dropped++;
                }
            }

            return new PointerBatchOutcome(applied, dropped, null);
        }
    }

    /// <summary>
    /// The idle rule, on demand: ends the open stream when it has had no frame for
    /// <see cref="IdleTimeout"/>, releasing any held button. Called by the housekeeping timer
    /// and before every batch; public so a test can drive it against a manual clock. Returns
    /// true when a stream was ended.
    /// </summary>
    public bool ExpireIdle()
    {
        lock (_lock)
        {
            return ExpireIdleLocked();
        }
    }

    /// <inheritdoc />
    public void EndAll(string reason)
    {
        lock (_lock)
        {
            if (_open is not null)
            {
                CloseLocked(reason);
            }
        }
    }

    /// <summary>Ends the open stream (releasing a held button) and stops housekeeping. Idempotent.</summary>
    public void Dispose()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0)
        {
            return;
        }

        _housekeeping?.Dispose();
        lock (_lock)
        {
            if (_open is not null)
            {
                CloseLocked("shutdown");
            }
        }
    }

    // ------------------------------------------------------------------ internals

    private bool ApplyFrameLocked(OpenStream open, PointerFrame frame)
    {
        try
        {
            if (frame.IsMove)
            {
                var dx = Math.Clamp(frame.Dx, -MaxDeltaPerFrame, MaxDeltaPerFrame);
                var dy = Math.Clamp(frame.Dy, -MaxDeltaPerFrame, MaxDeltaPerFrame);
                _input.MoveBy(dx, dy);
                open.Moves++;
                return true;
            }

            var button = frame.ButtonName == PointerStream.Right ? PointerButton.Right : PointerButton.Left;
            switch (frame.Action)
            {
                case PointerStream.Down:
                    _input.Button(button, down: true);
                    open.SetHeld(button, true);
                    break;
                case PointerStream.Up:
                    _input.Button(button, down: false);
                    open.SetHeld(button, false);
                    break;
                default:
                    _input.Button(button, down: true);
                    open.SetHeld(button, true);
                    _input.Button(button, down: false);
                    open.SetHeld(button, false);
                    break;
            }

            open.Buttons++;
            return true;
        }
        catch (Exception ex)
        {
            // The input was refused by the system (a UIPI-protected window in front, the
            // session locking between the check and the send). Counted, never thrown: the
            // next frame from the hand is the retry.
            _logger?.LogDebug("pointer frame not applied: {Reason}", ex.Message);
            return false;
        }
    }

    private bool ExpireIdleLocked()
    {
        var open = _open;
        if (open is null)
        {
            return false;
        }

        if (_time.GetElapsedTime(open.LastFrameAt) < IdleTimeout)
        {
            return false;
        }

        CloseLocked("timeout");
        return true;
    }

    private JsonObject CloseLocked(string reason)
    {
        var open = _open!;
        var released = ReleaseHeldLocked(open, reason);
        var summary = new JsonObject
        {
            ["session"] = open.Session,
            ["ended"] = true,
            ["ended_by"] = reason,
            ["moves"] = open.Moves,
            ["buttons"] = open.Buttons,
            ["dropped"] = open.Dropped,
            ["duration_ms"] = (long)_time.GetElapsedTime(open.OpenedAt).TotalMilliseconds,
            ["window_id"] = open.TargetWindowId,
            ["released"] = new JsonArray([.. released.Select(r => (JsonNode)r)]),
        };
        _open = null;
        _closed.Add((open.Session, (JsonObject)summary.DeepClone()));
        while (_closed.Count > ClosedSummariesKept)
        {
            _closed.RemoveAt(0);
        }

        _logger?.LogInformation(
            "pointer stream {Session} ended ({Reason}): moves={Moves} buttons={Buttons} dropped={Dropped} duration_ms={Duration}{Released}",
            open.Session,
            reason,
            open.Moves,
            open.Buttons,
            open.Dropped,
            summary["duration_ms"]!.GetValue<long>(),
            released.Count == 0 ? string.Empty : $" released={string.Join(",", released)}");
        return summary;
    }

    /// <summary>
    /// The invariant: a button the stream pressed and did not release is released here,
    /// whatever the reason. The release is attempted even when the send might fail (a locked
    /// session), because the alternative is a left button held until the owner touches the
    /// real mouse.
    /// </summary>
    private List<string> ReleaseHeldLocked(OpenStream open, string reason)
    {
        var released = new List<string>(2);
        if (open.LeftDown)
        {
            released.Add(PointerStream.Left);
            open.LeftDown = false;
            TryRelease(PointerButton.Left, reason);
        }

        if (open.RightDown)
        {
            released.Add(PointerStream.Right);
            open.RightDown = false;
            TryRelease(PointerButton.Right, reason);
        }

        return released;
    }

    private void TryRelease(PointerButton button, string reason)
    {
        try
        {
            _input.Button(button, down: false);
            _logger?.LogInformation("pointer stream released the held {Button} button ({Reason})", button, reason);
        }
        catch (Exception ex)
        {
            _logger?.LogWarning("pointer stream could not release the held {Button} button ({Reason}): {Error}", button, reason, ex.Message);
        }
    }

    private bool SafeLocked()
    {
        try
        {
            return _sessionLocked();
        }
        catch (Exception)
        {
            return true;
        }
    }

    private WindowInfo? SafeForeground()
    {
        try
        {
            return _foreground();
        }
        catch (Exception)
        {
            return null;
        }
    }

    private void SafeExpireIdle()
    {
        try
        {
            ExpireIdle();
        }
        catch (Exception ex)
        {
            _logger?.LogWarning("pointer stream housekeeping failed: {Reason}", ex.Message);
        }
    }

    private void ThrowIfDisposed()
    {
        if (_disposed != 0)
        {
            throw new CapabilityException(ErrorClasses.DependencyUnavailable, "the pointer stream is shutting down", retryable: true);
        }
    }

    /// <summary>One open mouse session: its identity, its counts, its held buttons and its token bucket.</summary>
    private sealed class OpenStream
    {
        private double _tokens;
        private long _tokensAt;

        public OpenStream(string session, string? targetWindowId, long openedAt, DateTimeOffset openedAtUtc, int burst)
        {
            Session = session;
            TargetWindowId = targetWindowId;
            OpenedAt = openedAt;
            OpenedAtUtc = openedAtUtc;
            LastFrameAt = openedAt;
            _tokens = burst;
            _tokensAt = openedAt;
        }

        public string Session { get; }

        public string? TargetWindowId { get; }

        public long OpenedAt { get; }

        public DateTimeOffset OpenedAtUtc { get; }

        public long LastFrameAt { get; set; }

        public int Moves { get; set; }

        public int Buttons { get; set; }

        public int Dropped { get; set; }

        public bool LeftDown { get; set; }

        public bool RightDown { get; set; }

        public void SetHeld(PointerButton button, bool down)
        {
            if (button == PointerButton.Right)
            {
                RightDown = down;
            }
            else
            {
                LeftDown = down;
            }
        }

        /// <summary>
        /// The rate bound as a token bucket: <see cref="MaxFramesPerSecond"/> tokens, refilled
        /// at that rate, one per frame. A batch that arrives after a quiet second may burst a
        /// full second's worth; a firehose gets exactly the rate and the rest is dropped.
        /// </summary>
        public bool TakeToken(TimeProvider time)
        {
            var now = time.GetTimestamp();
            var elapsed = time.GetElapsedTime(_tokensAt, now).TotalSeconds;
            if (elapsed > 0)
            {
                _tokens = Math.Min(MaxFramesPerSecond, _tokens + elapsed * MaxFramesPerSecond);
                _tokensAt = now;
            }

            if (_tokens < 1)
            {
                return false;
            }

            _tokens -= 1;
            return true;
        }
    }
}

/// <summary>
/// <c>SendInput</c> for the stream, in the owner's session. A move is realised as
/// <c>SetCursorPos</c> to the current position plus the delta, followed by a zero-delta
/// <c>MOUSEEVENTF_MOVE</c> so the application under the pointer sees a real pointer event —
/// the same two steps <see cref="Win32InputSynthesizer.MoveTo"/> takes. A raw relative
/// <c>MOUSEEVENTF_MOVE</c> with the delta in <c>dx/dy</c> is deliberately NOT used: relative
/// mouse input is subject to the owner's pointer-speed and acceleration settings ("Enhance
/// pointer precision"), so a 50-pixel frame would land 30 or 110 pixels away depending on
/// the control panel, and the browser's gain — pixels per unit of hand travel — would mean
/// something different on every machine. A button is one <c>SendInput</c> event with the
/// down or up flag, at the pointer's current place.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class Win32PointerStreamInput : IPointerStreamInput
{
    public void MoveBy(int dx, int dy)
    {
        if (!OperatorNative.GetCursorPos(out var at))
        {
            throw new CapabilityException(ErrorClasses.UiStateChanged, "the pointer position could not be read", retryable: true);
        }

        // SetCursorPos clamps to the virtual screen on its own; a delta that would leave it
        // lands on the edge, which is what a mouse does.
        if (!OperatorNative.SetCursorPos(at.X + dx, at.Y + dy))
        {
            throw new CapabilityException(ErrorClasses.UiStateChanged, $"the pointer could not be moved by ({dx},{dy})", retryable: true);
        }

        Send(OperatorNative.MouseEventMove);
    }

    public void Button(PointerButton button, bool down)
    {
        var flags = (button, down) switch
        {
            (PointerButton.Right, true) => OperatorNative.MouseEventRightDown,
            (PointerButton.Right, false) => OperatorNative.MouseEventRightUp,
            (_, true) => OperatorNative.MouseEventLeftDown,
            _ => OperatorNative.MouseEventLeftUp,
        };
        Send(flags);
    }

    private static void Send(uint flags)
    {
        var events = new[]
        {
            new OperatorNative.Input
            {
                Type = OperatorNative.InputMouse,
                Union = new OperatorNative.InputUnion
                {
                    Mouse = new OperatorNative.MouseInput { Dx = 0, Dy = 0, MouseData = 0, Flags = flags },
                },
            },
        };
        var sent = OperatorNative.SendInput(1, events, Marshal.SizeOf<OperatorNative.Input>());
        if (sent != 1)
        {
            var error = Marshal.GetLastWin32Error();
            throw new CapabilityException(
                ErrorClasses.UiStateChanged,
                $"SendInput delivered 0 of 1 pointer events (win32={error}); the input is blocked (a UIPI-protected window, a locked session)",
                retryable: true);
        }
    }
}
