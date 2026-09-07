using System.Globalization;
using System.Runtime.Versioning;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion.Operator;

/// <summary>A window's place on the (virtualised) screen, in pixels.</summary>
public sealed record WindowRect(int X, int Y, int Width, int Height)
{
    public JsonObject ToJson() => new() { ["x"] = X, ["y"] = Y, ["width"] = Width, ["height"] = Height };
}

/// <summary>
/// One observed top-level window (M19_DIGITAL_OPERATOR_SPEC.md §2): what the companion SAW at
/// the moment of reading, never what it intended. <see cref="WindowId"/> is stable for the
/// life of the window; <see cref="Handle"/> is the HWND behind it and never crosses the pipe
/// on its own.
/// </summary>
public sealed record WindowInfo(
    string WindowId,
    IntPtr Handle,
    int Pid,
    string Image,
    string Title,
    string State,
    WindowRect Rect,
    bool Foreground,
    string ClassName,
    bool Owned)
{
    public const string StateNormal = "normal";
    public const string StateMinimized = "minimized";
    public const string StateMaximized = "maximized";

    public JsonObject ToJson() => new()
    {
        ["window_id"] = WindowId,
        ["pid"] = Pid,
        ["image"] = Image,
        ["title"] = Title,
        ["state"] = State,
        ["rect"] = Rect.ToJson(),
        ["foreground"] = Foreground,
        ["class_name"] = ClassName,
        ["owned"] = Owned,
    };
}

/// <summary>
/// The companion's view of the owner's top-level windows (§3): <c>EnumWindows</c> and friends,
/// plus the stable ids. A window gets its id — <c>w-&lt;hwnd&gt;-&lt;tick at first sight&gt;</c> — the
/// first time this registry sees it and keeps it until the handle is gone, so a handle Windows
/// later reuses for a different window yields a different id and a stale plan cannot act on
/// the wrong window by accident. An id this registry never issued (a previous companion life,
/// a made-up one) resolves to <c>ui_target_not_found</c>.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class WindowRegistry
{
    private readonly object _lock = new();
    private readonly Dictionary<long, (string Id, int Pid)> _known = new();

    /// <summary>
    /// Every visible, titled top-level window (owned dialogs included and marked), refreshing
    /// the id table on the way. Optionally only one process's windows.
    /// </summary>
    public IReadOnlyList<WindowInfo> Enumerate(int? pid = null)
    {
        var handles = new List<IntPtr>();
        OperatorNative.EnumWindows((hwnd, _) =>
        {
            handles.Add(hwnd);
            return true;
        }, IntPtr.Zero);

        var foreground = OperatorNative.GetForegroundWindow();
        var result = new List<WindowInfo>();
        lock (_lock)
        {
            var seen = new HashSet<long>();
            foreach (var hwnd in handles)
            {
                if (!IsEligible(hwnd))
                {
                    continue;
                }

                var info = ReadLocked(hwnd, foreground);
                if (info is null)
                {
                    continue;
                }

                seen.Add(hwnd.ToInt64());
                if (pid is null || info.Pid == pid.Value)
                {
                    result.Add(info);
                }
            }

            // Forget handles that are gone, so a reused handle is a new window with a new id.
            foreach (var key in _known.Keys.Where(k => !seen.Contains(k) && !OperatorNative.IsWindow(new IntPtr(k))).ToList())
            {
                _known.Remove(key);
            }
        }

        return result;
    }

    /// <summary>The foreground window, or null when there is none (a locked or empty session) or it is not eligible.</summary>
    public WindowInfo? Foreground()
    {
        var hwnd = OperatorNative.GetForegroundWindow();
        if (hwnd == IntPtr.Zero)
        {
            return null;
        }

        return Read(hwnd);
    }

    /// <summary>A fresh read of one window by handle, or null when it no longer exists / is not eligible.</summary>
    public WindowInfo? Read(IntPtr hwnd)
    {
        if (!IsEligible(hwnd))
        {
            return null;
        }

        lock (_lock)
        {
            return ReadLocked(hwnd, OperatorNative.GetForegroundWindow());
        }
    }

    /// <summary>The window behind an id this registry issued, freshly read; <c>ui_target_not_found</c> otherwise.</summary>
    public WindowInfo Resolve(string? windowId)
    {
        if (string.IsNullOrWhiteSpace(windowId))
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "payload.window_id is required", retryable: false);
        }

        if (!TryParseHandle(windowId, out var hwnd))
        {
            throw new CapabilityException(ErrorClasses.ValidationError, $"'{windowId}' is not a window id (expected w-<hwnd>-<tick>)", retryable: false);
        }

        lock (_lock)
        {
            if (!_known.TryGetValue(hwnd.ToInt64(), out var known) || !string.Equals(known.Id, windowId, StringComparison.Ordinal))
            {
                throw new CapabilityException(
                    ErrorClasses.UiTargetNotFound,
                    $"window '{windowId}' is not one this companion has observed; observe again (window.list / window.current) and re-resolve",
                    retryable: true);
            }

            if (!OperatorNative.IsWindow(hwnd))
            {
                _known.Remove(hwnd.ToInt64());
                throw new CapabilityException(ErrorClasses.UiTargetNotFound, $"window '{windowId}' no longer exists", retryable: true);
            }

            var info = ReadLocked(hwnd, OperatorNative.GetForegroundWindow());
            return info ?? throw new CapabilityException(
                ErrorClasses.UiStateChanged,
                $"window '{windowId}' exists but is no longer a visible top-level window",
                retryable: true);
        }
    }

    /// <summary>Whether the handle behind an id is still a window (no eligibility check — a closing window is "gone" only when the handle is).</summary>
    public static bool Exists(string windowId)
        => TryParseHandle(windowId, out var hwnd) && OperatorNative.IsWindow(hwnd);

    public static bool TryParseHandle(string windowId, out IntPtr hwnd)
    {
        hwnd = IntPtr.Zero;
        var parts = windowId.Split('-');
        if (parts.Length != 3 || parts[0] != "w")
        {
            return false;
        }

        if (!long.TryParse(parts[1], NumberStyles.None, CultureInfo.InvariantCulture, out var raw) || raw <= 0)
        {
            return false;
        }

        if (!ulong.TryParse(parts[2], NumberStyles.None, CultureInfo.InvariantCulture, out _))
        {
            return false;
        }

        hwnd = new IntPtr(raw);
        return true;
    }

    /// <summary>Visible, not cloaked, not a tool window, and titled. Owned dialogs qualify (they are what a modal is).</summary>
    public static bool IsEligible(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero || !OperatorNative.IsWindow(hwnd) || !OperatorNative.IsWindowVisible(hwnd))
        {
            return false;
        }

        if (OperatorNative.IsToolWindow(hwnd) || OperatorNative.IsCloaked(hwnd))
        {
            return false;
        }

        return OperatorNative.GetWindowTextLengthW(hwnd) > 0;
    }

    private WindowInfo? ReadLocked(IntPtr hwnd, IntPtr foreground)
    {
        OperatorNative.GetWindowThreadProcessId(hwnd, out var pid);
        if (pid == 0)
        {
            return null;
        }

        var key = hwnd.ToInt64();
        if (!_known.TryGetValue(key, out var known) || known.Pid != (int)pid)
        {
            known = (string.Create(CultureInfo.InvariantCulture, $"w-{key}-{OperatorNative.GetTickCount64()}"), (int)pid);
            _known[key] = known;
        }

        if (!OperatorNative.GetWindowRect(hwnd, out var rect))
        {
            return null;
        }

        var placement = new OperatorNative.WindowPlacement { Length = (uint)System.Runtime.InteropServices.Marshal.SizeOf<OperatorNative.WindowPlacement>() };
        var state = WindowInfo.StateNormal;
        if (OperatorNative.GetWindowPlacement(hwnd, ref placement))
        {
            state = placement.ShowCmd switch
            {
                OperatorNative.SwShowMinimized => WindowInfo.StateMinimized,
                OperatorNative.SwShowMaximized => WindowInfo.StateMaximized,
                _ => WindowInfo.StateNormal,
            };
        }
        else if (OperatorNative.IsIconic(hwnd))
        {
            state = WindowInfo.StateMinimized;
        }
        else if (OperatorNative.IsZoomed(hwnd))
        {
            state = WindowInfo.StateMaximized;
        }

        return new WindowInfo(
            known.Id,
            hwnd,
            (int)pid,
            OperatorNative.ImageName(pid),
            OperatorNative.WindowText(hwnd),
            state,
            new WindowRect(rect.Left, rect.Top, rect.Right - rect.Left, rect.Bottom - rect.Top),
            hwnd == foreground,
            OperatorNative.ClassName(hwnd),
            OperatorNative.GetWindow(hwnd, OperatorNative.GwOwner) != IntPtr.Zero);
    }
}
