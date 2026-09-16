using System.Runtime.InteropServices;
using System.Runtime.Versioning;

namespace PagentOS.SessionCompanion;

/// <summary>One monitor as the session sees it. Read-only geometry; nothing here is settable.</summary>
/// <remarks>
/// B48 (row 320): <see cref="Power"/> is the monitor's OWN answer to a DDC/CI read of VCP code
/// 0xD6 (power mode) - <c>on</c>, <c>standby</c>, <c>suspend</c>, <c>off</c> - or
/// <c>unsupported</c> when the panel does not answer (most laptop panels, many docks), and
/// null when nobody asked. It is read only on request: some monitors wake on DDC traffic, so
/// the status a heartbeat-like caller polls never touches the bus.
/// </remarks>
public sealed record MonitorGeometry(int Index, bool Primary, int Left, int Top, int Width, int Height, string? Power = null);

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

    /// <summary>B48 (row 320): the same list with each monitor's own DDC/CI power reading. Never throws.</summary>
    IReadOnlyList<MonitorGeometry> ListWithPower() => List();
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

    /// <summary>VCP code 0xD6: the DPM power mode a monitor reports about itself.</summary>
    public const byte PowerModeVcp = 0xD6;

    public IReadOnlyList<MonitorGeometry> List() => Enumerate(probePower: false);

    public IReadOnlyList<MonitorGeometry> ListWithPower() => Enumerate(probePower: true);

    /// <summary>The DDC/CI power mode value mapped to a word (MCCS 2.2: 1 on, 2 standby, 3 suspend, 4/5 off).</summary>
    public static string PowerWord(uint value) => value switch
    {
        1 => "on",
        2 => "standby",
        3 => "suspend",
        4 or 5 => "off",
        _ => "unsupported",
    };

    private IReadOnlyList<MonitorGeometry> Enumerate(bool probePower)
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
                        info.Monitor.Bottom - info.Monitor.Top,
                        probePower ? ReadPower(monitor) : null));
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

    /// <summary>
    /// A READ of the first physical monitor behind a display handle. There is deliberately no
    /// write here - the display family never sets a VCP code (the structural test forbids the
    /// name) - and every handle is released before returning.
    /// </summary>
    private static string ReadPower(IntPtr monitor)
    {
        try
        {
            if (!GetNumberOfPhysicalMonitorsFromHMONITOR(monitor, out var count) || count == 0)
            {
                return "unsupported";
            }

            var physical = new PhysicalMonitor[count];
            if (!GetPhysicalMonitorsFromHMONITOR(monitor, count, physical))
            {
                return "unsupported";
            }

            try
            {
                return GetVCPFeatureAndVCPFeatureReply(physical[0].Handle, PowerModeVcp, IntPtr.Zero, out var current, out _)
                    ? PowerWord(current)
                    : "unsupported";
            }
            finally
            {
                _ = DestroyPhysicalMonitors(count, physical);
            }
        }
        catch (Exception)
        {
            return "unsupported";
        }
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct PhysicalMonitor
    {
        public IntPtr Handle;

        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
        public string Description;
    }

    [DllImport("dxva2.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetNumberOfPhysicalMonitorsFromHMONITOR(IntPtr monitor, out uint count);

    [DllImport("dxva2.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetPhysicalMonitorsFromHMONITOR(IntPtr monitor, uint count, [Out] PhysicalMonitor[] monitors);

    [DllImport("dxva2.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DestroyPhysicalMonitors(uint count, PhysicalMonitor[] monitors);

    [DllImport("dxva2.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetVCPFeatureAndVCPFeatureReply(IntPtr monitor, byte code, IntPtr type, out uint current, out uint maximum);

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
