using System.Runtime.InteropServices;
using System.Runtime.Versioning;

namespace PagentOS.SessionCompanion;

/// <summary>One monitor as the session sees it. Read-only geometry; nothing here is settable.</summary>
public sealed record MonitorGeometry(int Index, bool Primary, int Left, int Top, int Width, int Height);

/// <summary>
/// Reports the monitors attached to the owner's session (DEVICE_PROTOCOL.md §6e, M18.3).
///
/// <para><b>Reporting only.</b> M18.3 changes no topology, no resolution, no orientation and no
/// primary-monitor choice — those are settings the owner arranged, often physically, and an
/// agent that rearranges them has done something the owner cannot undo by moving a mouse. The
/// structural test that guards the display family fails if a display-configuration API name
/// appears in any of these files.</para>
///
/// <para>Enumeration is best-effort: a session with no visible desktop (which is what a
/// Session-0 process would find) returns an empty list, and the capability then omits the
/// <c>monitors</c> field rather than reporting zero monitors as a fact about the hardware.</para>
/// </summary>
public interface IMonitorInventory
{
    /// <summary>The monitors, primary first-indexed as Windows enumerates them. Never throws.</summary>
    IReadOnlyList<MonitorGeometry> List();
}

/// <summary>An inventory that knows nothing. Used where no real one is wired.</summary>
public sealed class EmptyMonitorInventory : IMonitorInventory
{
    public static EmptyMonitorInventory Instance { get; } = new();

    public IReadOnlyList<MonitorGeometry> List() => [];
}

/// <summary><c>EnumDisplayMonitors</c> + <c>GetMonitorInfo</c>, and nothing else.</summary>
[SupportedOSPlatform("windows")]
public sealed class Win32MonitorInventory : IMonitorInventory
{
    private const uint PrimaryMonitorFlag = 0x00000001;

    public IReadOnlyList<MonitorGeometry> List()
    {
        var found = new List<MonitorGeometry>();
        try
        {
            var index = 0;
            _ = EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, Collect, IntPtr.Zero);

            bool Collect(IntPtr monitor, IntPtr deviceContext, ref Rectangle area, IntPtr param)
            {
                var info = new MonitorInfo { Size = (uint)Marshal.SizeOf<MonitorInfo>() };
                if (GetMonitorInfoW(monitor, ref info))
                {
                    found.Add(new MonitorGeometry(
                        index++,
                        (info.Flags & PrimaryMonitorFlag) != 0,
                        info.Monitor.Left,
                        info.Monitor.Top,
                        info.Monitor.Right - info.Monitor.Left,
                        info.Monitor.Bottom - info.Monitor.Top));
                }

                return true;
            }
        }
        catch (Exception)
        {
            // A session that cannot enumerate its monitors reports none; the capability then
            // omits the field instead of asserting a zero-monitor machine.
            return [];
        }

        return found;
    }

    private delegate bool MonitorCallback(IntPtr monitor, IntPtr deviceContext, ref Rectangle area, IntPtr param);

    [StructLayout(LayoutKind.Sequential)]
    private struct Rectangle
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct MonitorInfo
    {
        public uint Size;
        public Rectangle Monitor;
        public Rectangle Work;
        public uint Flags;
    }

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool EnumDisplayMonitors(IntPtr deviceContext, IntPtr clip, MonitorCallback callback, IntPtr param);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetMonitorInfoW(IntPtr monitor, ref MonitorInfo info);
}
