using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using Microsoft.Extensions.Logging;

namespace PagentOS.SessionCompanion;

/// <summary>
/// What the display was last seen doing, and when. <see cref="ObservedAt"/> is null exactly
/// when <see cref="State"/> is <see cref="Unknown"/>: before the first notification arrives
/// this device has genuinely never been told, and saying so is the whole point of the type.
/// </summary>
public sealed record DisplayObservation(string State, DateTimeOffset? ObservedAt)
{
    /// <summary>Nothing has been observed yet. Never inferred, never guessed at from an idle timer.</summary>
    public const string Unknown = "unknown";

    public const string Off = "off";

    public const string On = "on";

    public const string Dimmed = "dimmed";

    /// <summary>The state every observer starts in.</summary>
    public static DisplayObservation Nothing { get; } = new(Unknown, null);

    public bool IsKnown => !string.Equals(State, Unknown, StringComparison.Ordinal);
}

/// <summary>
/// The display-state seam (DEVICE_PROTOCOL.md §6e, M18.3): a read-only view of what the
/// operating system has told this session about the console display. It has no method that
/// changes anything — an observer that could also act would be able to darken a screen
/// without going through the two gates that guard <c>desktop.display_off</c>, which is
/// precisely the arrangement M18 declined to build.
/// </summary>
public interface IDisplayStateObserver
{
    /// <summary>The last value received, with the moment it arrived. Never throws.</summary>
    DisplayObservation Current { get; }
}

/// <summary>
/// An observer that has never been told anything. Used where no real one is wired; it reports
/// <c>unknown</c> forever, which is true of it.
/// </summary>
public sealed class UnknownDisplayStateObserver : IDisplayStateObserver
{
    public static UnknownDisplayStateObserver Instance { get; } = new();

    public DisplayObservation Current => DisplayObservation.Nothing;
}

/// <summary>
/// The production observer: a message-only window in the owner's interactive session that
/// registers for <c>GUID_CONSOLE_DISPLAY_STATE</c> (0 off, 1 on, 2 dimmed) and
/// <c>GUID_MONITOR_POWER_ON</c> (0 off, 1 on) and records the last value it was handed.
///
/// <para><b>It never asks; it is only told.</b> Windows exposes no reliable synchronous "is the
/// display on?" call, and the ones that look like it are the same APIs that turn a display off.
/// So this class holds no way of driving a display at all — no broadcast target, no execution
/// state, no synthetic input — and a structural test asserts that. Before the first
/// notification the answer is <c>unknown</c>, and it stays <c>unknown</c>: a device that has
/// not been told must not report a state it inferred from an idle timer, because the display
/// idling out and the owner idling out are different events with different consequences.</para>
///
/// <para>A message-only window (<c>HWND_MESSAGE</c> parent) has no presence on the desktop: it
/// is never shown, never focusable, and appears in no taskbar. It exists to receive one
/// message type on one background thread.</para>
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class Win32DisplayStateObserver : IDisplayStateObserver, IDisposable
{
    private const uint WmPowerBroadcast = 0x0218;
    private const uint WmClose = 0x0010;
    private const uint PbtPowerSettingChange = 0x8013;
    private const uint DeviceNotifyWindowHandle = 0x00000000;

    private static readonly Guid ConsoleDisplayStateSetting = new("6fe69556-704a-47a0-8f24-c28d936fda47");
    private static readonly Guid MonitorPowerOnSetting = new("02731015-4510-4526-99e6-e5a17ebd1aea");

    private static readonly IntPtr MessageOnlyParent = new(-3);

    private readonly ILogger _logger;
    private readonly TimeProvider _time;

    /// <summary>Kept in a field so the marshalled thunk is not collected while Windows holds it.</summary>
    private readonly WindowProcedure _procedure;

    private readonly Thread _pump;
    private readonly ManualResetEventSlim _ready = new(false);

    /// <summary>
    /// The whole mutable state, as one immutable value swapped atomically. Written on the
    /// pump thread, read on whichever thread answers a capability; a reference assignment
    /// through <see cref="Volatile"/> needs no mutual exclusion and cannot be observed
    /// half-updated the way two separate fields could.
    /// </summary>
    private DisplayObservation _current = DisplayObservation.Nothing;

    private IntPtr _window;
    private IntPtr _consoleRegistration;
    private IntPtr _monitorRegistration;
    private bool _disposed;

    public Win32DisplayStateObserver(ILogger logger, TimeProvider? time = null)
    {
        _logger = logger;
        _time = time ?? TimeProvider.System;
        _procedure = OnMessage;
        _pump = new Thread(RunPump)
        {
            IsBackground = true,
            Name = "pagentos-display-observer",
        };
    }

    public DisplayObservation Current => Volatile.Read(ref _current);

    /// <summary>True once the window exists and both registrations succeeded.</summary>
    public bool IsListening { get; private set; }

    /// <summary>
    /// Starts the pump and waits briefly for it to settle. A failure here is logged and
    /// swallowed: an agent that could not register for display notifications still answers
    /// every capability, reporting <c>unknown</c>, which is honest and costs nothing.
    /// </summary>
    public void Start(TimeSpan? readyTimeout = null)
    {
        _pump.Start();
        _ready.Wait(readyTimeout ?? TimeSpan.FromSeconds(2));
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        _disposed = true;
        var window = _window;
        if (window != IntPtr.Zero)
        {
            // To its OWN window, on purpose: this class has no broadcast target anywhere, so
            // it cannot reach a window it does not own even by accident.
            _ = PostMessageW(window, WmClose, IntPtr.Zero, IntPtr.Zero);
        }

        _ready.Dispose();
    }

    private void RunPump()
    {
        try
        {
            var className = $"PagentOSDisplayObserver-{Guid.NewGuid():N}";
            var windowClass = new WindowClassEx
            {
                Size = (uint)Marshal.SizeOf<WindowClassEx>(),
                Procedure = Marshal.GetFunctionPointerForDelegate(_procedure),
                Instance = GetModuleHandleW(null),
                ClassName = className,
            };

            if (RegisterClassExW(ref windowClass) == 0)
            {
                _logger.LogWarning(
                    "display observer could not register its window class (win32={Error}); display state stays unknown",
                    Marshal.GetLastWin32Error());
                return;
            }

            _window = CreateWindowExW(
                0, className, className, 0, 0, 0, 0, 0,
                MessageOnlyParent, IntPtr.Zero, windowClass.Instance, IntPtr.Zero);
            if (_window == IntPtr.Zero)
            {
                _logger.LogWarning(
                    "display observer could not create its message-only window (win32={Error}); display state stays unknown",
                    Marshal.GetLastWin32Error());
                return;
            }

            var console = ConsoleDisplayStateSetting;
            var monitor = MonitorPowerOnSetting;
            _consoleRegistration = RegisterPowerSettingNotification(_window, ref console, DeviceNotifyWindowHandle);
            _monitorRegistration = RegisterPowerSettingNotification(_window, ref monitor, DeviceNotifyWindowHandle);
            IsListening = _consoleRegistration != IntPtr.Zero && _monitorRegistration != IntPtr.Zero;
            if (!IsListening)
            {
                _logger.LogWarning("display observer registered no power-setting notification; display state stays unknown");
            }
            else
            {
                _logger.LogInformation("display observer listening (console display state + monitor power)");
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning("display observer could not start: {Reason}; display state stays unknown", ex.Message);
            return;
        }
        finally
        {
            _ready.Set();
        }

        try
        {
            while (GetMessageW(out var message, IntPtr.Zero, 0, 0) > 0)
            {
                _ = TranslateMessage(ref message);
                _ = DispatchMessageW(ref message);
            }
        }
        catch (Exception ex)
        {
            _logger.LogWarning("display observer pump ended: {Reason}", ex.Message);
        }
        finally
        {
            if (_consoleRegistration != IntPtr.Zero)
            {
                _ = UnregisterPowerSettingNotification(_consoleRegistration);
            }

            if (_monitorRegistration != IntPtr.Zero)
            {
                _ = UnregisterPowerSettingNotification(_monitorRegistration);
            }

            IsListening = false;
        }
    }

    private IntPtr OnMessage(IntPtr window, uint message, IntPtr wParam, IntPtr lParam)
    {
        if (message == WmPowerBroadcast && (uint)wParam == PbtPowerSettingChange && lParam != IntPtr.Zero)
        {
            try
            {
                var setting = Marshal.PtrToStructure<PowerSettingChange>(lParam);
                Record(setting.PowerSetting, setting.FirstDataByte);
            }
            catch (Exception ex)
            {
                _logger.LogDebug("display observer ignored a malformed power-setting message: {Reason}", ex.Message);
            }
        }

        if (message == WmClose)
        {
            _ = DestroyWindow(window);
            PostQuitMessage(0);
            return IntPtr.Zero;
        }

        return DefWindowProcW(window, message, wParam, lParam);
    }

    private void Record(Guid setting, byte value)
    {
        string? state = null;
        if (setting == ConsoleDisplayStateSetting)
        {
            state = value switch
            {
                0 => DisplayObservation.Off,
                1 => DisplayObservation.On,
                2 => DisplayObservation.Dimmed,
                _ => null,
            };
        }
        else if (setting == MonitorPowerOnSetting)
        {
            state = value switch
            {
                0 => DisplayObservation.Off,
                1 => DisplayObservation.On,
                _ => null,
            };
        }

        if (state is null)
        {
            return;
        }

        Volatile.Write(ref _current, new DisplayObservation(state, _time.GetUtcNow()));
        _logger.LogDebug("display observed: {State}", state);
    }

    // ------------------------------------------------------------------ interop

    private delegate IntPtr WindowProcedure(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct WindowClassEx
    {
        public uint Size;
        public uint Style;
        public IntPtr Procedure;
        public int ClassExtra;
        public int WindowExtra;
        public IntPtr Instance;
        public IntPtr Icon;
        public IntPtr Cursor;
        public IntPtr Background;
        [MarshalAs(UnmanagedType.LPWStr)]
        public string? MenuName;
        [MarshalAs(UnmanagedType.LPWStr)]
        public string ClassName;
        public IntPtr SmallIcon;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PowerSettingChange
    {
        public Guid PowerSetting;
        public uint DataLength;

        /// <summary>Both settings this class registers for carry a DWORD; only its low byte varies.</summary>
        public byte FirstDataByte;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct WindowMessage
    {
        public IntPtr Window;
        public uint Message;
        public IntPtr WParam;
        public IntPtr LParam;
        public uint Time;
        public int PointX;
        public int PointY;
    }

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern ushort RegisterClassExW(ref WindowClassEx windowClass);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern IntPtr CreateWindowExW(
        uint exStyle, string className, string windowName, uint style,
        int x, int y, int width, int height,
        IntPtr parent, IntPtr menu, IntPtr instance, IntPtr param);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DestroyWindow(IntPtr window);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr DefWindowProcW(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern int GetMessageW(out WindowMessage message, IntPtr window, uint filterMin, uint filterMax);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool TranslateMessage(ref WindowMessage message);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr DispatchMessageW(ref WindowMessage message);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool PostMessageW(IntPtr window, uint message, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern void PostQuitMessage(int exitCode);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern IntPtr RegisterPowerSettingNotification(IntPtr recipient, ref Guid powerSetting, uint flags);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool UnregisterPowerSettingNotification(IntPtr handle);

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern IntPtr GetModuleHandleW(string? moduleName);
}
