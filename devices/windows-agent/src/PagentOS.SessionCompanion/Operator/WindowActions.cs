using System.Runtime.Versioning;

namespace PagentOS.SessionCompanion.Operator;

/// <summary>
/// The four things the operator does TO a window (M19_DIGITAL_OPERATOR_SPEC.md §3): show it in
/// a state, bring it to the front, move it, ask it to close. Every method returns what it
/// could verify, never what it asked for; the caller re-observes and decides.
/// </summary>
[SupportedOSPlatform("windows")]
public static class WindowActions
{
    /// <summary>How many times activation is attempted before it is reported as failed.</summary>
    public const int ActivateAttempts = 3;

    /// <summary>How long a closing window is given after WM_CLOSE before "still there" is reported.</summary>
    public static readonly TimeSpan CloseWait = TimeSpan.FromSeconds(5);

    public static void Show(IntPtr hwnd, int command) => OperatorNative.ShowWindow(hwnd, command);

    /// <summary>
    /// Bring a window to the foreground and VERIFY it got there, retrying at most
    /// <see cref="ActivateAttempts"/> times. Windows only lets a process take the foreground
    /// when it is entitled to (it owns the current foreground, or received the last input
    /// event), so each attempt does the documented dance — attach to the foreground thread's
    /// input queue, allow the change, ask — and a later attempt first sends a zero-delta
    /// pointer move, the smallest real input event there is, which makes this process "the one
    /// that received the last input". No key is ever synthesised for this.
    /// </summary>
    public static bool Activate(IntPtr hwnd)
    {
        for (var attempt = 1; attempt <= ActivateAttempts; attempt++)
        {
            if (OperatorNative.IsIconic(hwnd))
            {
                OperatorNative.ShowWindow(hwnd, OperatorNative.SwRestore);
            }

            if (OperatorNative.GetForegroundWindow() == hwnd)
            {
                return true;
            }

            if (attempt > 1)
            {
                Win32InputSynthesizer.NudgePointer();
            }

            var current = OperatorNative.GetCurrentThreadId();
            var foreground = OperatorNative.GetForegroundWindow();
            var foregroundThread = foreground == IntPtr.Zero ? 0 : OperatorNative.GetWindowThreadProcessId(foreground, out _);
            var targetThread = OperatorNative.GetWindowThreadProcessId(hwnd, out _);
            var attachedForeground = foregroundThread != 0 && foregroundThread != current && OperatorNative.AttachThreadInput(current, foregroundThread, true);
            var attachedTarget = targetThread != 0 && targetThread != current && targetThread != foregroundThread && OperatorNative.AttachThreadInput(current, targetThread, true);
            try
            {
                OperatorNative.AllowSetForegroundWindow(OperatorNative.AsfwAny);
                OperatorNative.SetForegroundWindow(hwnd);
                OperatorNative.BringWindowToTop(hwnd);
            }
            finally
            {
                if (attachedForeground)
                {
                    OperatorNative.AttachThreadInput(current, foregroundThread, false);
                }

                if (attachedTarget)
                {
                    OperatorNative.AttachThreadInput(current, targetThread, false);
                }
            }

            var deadline = DateTime.UtcNow.AddMilliseconds(250);
            while (DateTime.UtcNow < deadline)
            {
                if (OperatorNative.GetForegroundWindow() == hwnd)
                {
                    return true;
                }

                Thread.Sleep(25);
            }
        }

        return OperatorNative.GetForegroundWindow() == hwnd;
    }

    /// <summary><c>MoveWindow</c> to a new origin, keeping the size; false when Windows refused.</summary>
    public static bool Move(IntPtr hwnd, int x, int y)
    {
        if (!OperatorNative.GetWindowRect(hwnd, out var rect))
        {
            return false;
        }

        return OperatorNative.MoveWindow(hwnd, x, y, rect.Right - rect.Left, rect.Bottom - rect.Top, true);
    }

    /// <summary><c>MoveWindow</c> to a new size, keeping the origin; false when Windows refused.</summary>
    public static bool Resize(IntPtr hwnd, int width, int height)
    {
        if (!OperatorNative.GetWindowRect(hwnd, out var rect))
        {
            return false;
        }

        return OperatorNative.MoveWindow(hwnd, rect.Left, rect.Top, width, height, true);
    }

    /// <summary>Post <c>WM_CLOSE</c> — a REQUEST the application may answer with a dialog. Never a kill.</summary>
    public static bool RequestClose(IntPtr hwnd)
        => OperatorNative.PostMessageW(hwnd, OperatorNative.WmClose, IntPtr.Zero, IntPtr.Zero);

    /// <summary>
    /// Wait for a window handle to be gone, at most <paramref name="wait"/>, or until
    /// <paramref name="stopEarly"/> says the wait is pointless (a modal appeared). True when
    /// the handle is gone.
    /// </summary>
    public static bool WaitForGone(IntPtr hwnd, TimeSpan wait, CancellationToken cancellationToken, Func<bool>? stopEarly = null)
    {
        var deadline = DateTime.UtcNow + wait;
        while (DateTime.UtcNow < deadline)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (!OperatorNative.IsWindow(hwnd))
            {
                return true;
            }

            if (stopEarly?.Invoke() == true)
            {
                return !OperatorNative.IsWindow(hwnd);
            }

            Thread.Sleep(100);
        }

        return !OperatorNative.IsWindow(hwnd);
    }

    /// <summary>Poll until <paramref name="condition"/> holds or <paramref name="wait"/> elapses.</summary>
    public static bool WaitUntil(Func<bool> condition, TimeSpan wait, CancellationToken cancellationToken, int stepMs = 50)
    {
        var deadline = DateTime.UtcNow + wait;
        while (true)
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (condition())
            {
                return true;
            }

            if (DateTime.UtcNow >= deadline)
            {
                return false;
            }

            Thread.Sleep(stepMs);
        }
    }
}
