using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace PagentOS.SessionCompanion.Operator;

/// <summary>
/// The two facts a caller (the test lab, a diagnostic) may ask about the session this process
/// runs in, without reaching the P/Invoke layer: is there a visible desktop at all, and is
/// there a foreground window on it. Both are false in Session 0 and on a headless runner.
/// </summary>
[SupportedOSPlatform("windows")]
public static class OperatorEnvironment
{
    public static bool HasVisibleDesktop() => OperatorNative.HasVisibleDesktop();

    public static bool HasForegroundWindow() => OperatorNative.GetForegroundWindow() != IntPtr.Zero;
}

/// <summary>
/// The Win32 surface the Digital Operator uses (M19_DIGITAL_OPERATOR_SPEC.md §3), in one
/// place so a reader can see everything this module can do to the owner's session: read
/// window facts, change a window's state or place, post a close request, synthesise input,
/// and copy pixels. Nothing here ends a session, changes power state or installs a hook.
/// </summary>
[SupportedOSPlatform("windows")]
internal static class OperatorNative
{
    public const int SwShowNormal = 1;
    public const int SwShowMinimized = 2;
    public const int SwShowMaximized = 3;
    public const int SwMinimize = 6;
    public const int SwRestore = 9;

    public const uint WmClose = 0x0010;
    public const uint GwOwner = 4;
    public const int GwlExStyle = -20;
    public const long WsExToolWindow = 0x00000080;
    public const uint DwmwaCloaked = 14;
    public const uint AsfwAny = unchecked((uint)-1);

    public const uint ProcessQueryLimitedInformation = 0x1000;

    public const int SmCxScreen = 0;
    public const int SmCyScreen = 1;
    public const int SmXVirtualScreen = 76;
    public const int SmYVirtualScreen = 77;
    public const int SmCxVirtualScreen = 78;
    public const int SmCyVirtualScreen = 79;

    public const uint InputMouse = 0;
    public const uint InputKeyboard = 1;

    public const uint MouseEventMove = 0x0001;
    public const uint MouseEventLeftDown = 0x0002;
    public const uint MouseEventLeftUp = 0x0004;
    public const uint MouseEventRightDown = 0x0008;
    public const uint MouseEventRightUp = 0x0010;
    public const uint MouseEventWheel = 0x0800;
    public const int WheelDelta = 120;

    public const uint KeyEventExtendedKey = 0x0001;
    public const uint KeyEventKeyUp = 0x0002;
    public const uint KeyEventUnicode = 0x0004;

    public const uint SrcCopy = 0x00CC0020;
    public const uint CaptureBlt = 0x40000000;
    public const uint PwRenderFullContent = 0x00000002;
    public const uint DibRgbColors = 0;
    public const uint BiRgb = 0;

    public const int UoiFlags = 1;
    public const uint WsfVisible = 0x0001;

    public delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lparam);

    [StructLayout(LayoutKind.Sequential)]
    public struct Rect
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct Point
    {
        public int X;
        public int Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct WindowPlacement
    {
        public uint Length;
        public uint Flags;
        public uint ShowCmd;
        public Point MinPosition;
        public Point MaxPosition;
        public Rect NormalPosition;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct MouseInput
    {
        public int Dx;
        public int Dy;
        public uint MouseData;
        public uint Flags;
        public uint Time;
        public IntPtr ExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct KeybdInput
    {
        public ushort VirtualKey;
        public ushort Scan;
        public uint Flags;
        public uint Time;
        public IntPtr ExtraInfo;
    }

    [StructLayout(LayoutKind.Explicit)]
    public struct InputUnion
    {
        [FieldOffset(0)]
        public MouseInput Mouse;

        [FieldOffset(0)]
        public KeybdInput Keyboard;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct Input
    {
        public uint Type;
        public InputUnion Union;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct BitmapInfoHeader
    {
        public uint Size;
        public int Width;
        public int Height;
        public ushort Planes;
        public ushort BitCount;
        public uint Compression;
        public uint SizeImage;
        public int XPelsPerMeter;
        public int YPelsPerMeter;
        public uint ClrUsed;
        public uint ClrImportant;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct BitmapInfo
    {
        public BitmapInfoHeader Header;
        public uint FirstColor;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct UserObjectFlags
    {
        public int Inherit;
        public int Reserved;
        public uint Flags;
    }

    // ------------------------------------------------------------------ windows: read

    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lparam);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern int GetWindowTextW(IntPtr hwnd, StringBuilder text, int maxCount);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowTextLengthW(IntPtr hwnd);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetClassNameW(IntPtr hwnd, StringBuilder text, int maxCount);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hwnd);

    [DllImport("user32.dll")]
    public static extern bool IsWindow(IntPtr hwnd);

    [DllImport("user32.dll")]
    public static extern bool IsWindowEnabled(IntPtr hwnd);

    [DllImport("user32.dll")]
    public static extern bool IsIconic(IntPtr hwnd);

    [DllImport("user32.dll")]
    public static extern bool IsZoomed(IntPtr hwnd);

    [DllImport("user32.dll")]
    public static extern bool GetWindowPlacement(IntPtr hwnd, ref WindowPlacement placement);

    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hwnd, out Rect rect);

    [DllImport("user32.dll")]
    public static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    public static extern IntPtr GetWindow(IntPtr hwnd, uint command);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")]
    public static extern IntPtr GetWindowLongPtr(IntPtr hwnd, int index);

    [DllImport("dwmapi.dll")]
    public static extern int DwmGetWindowAttribute(IntPtr hwnd, uint attribute, out int value, int size);

    // ------------------------------------------------------------------ windows: act

    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hwnd, int command);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hwnd);

    [DllImport("user32.dll")]
    public static extern bool BringWindowToTop(IntPtr hwnd);

    [DllImport("user32.dll")]
    public static extern bool AllowSetForegroundWindow(uint pid);

    [DllImport("user32.dll")]
    public static extern bool AttachThreadInput(uint attach, uint attachTo, bool attachFlag);

    [DllImport("kernel32.dll")]
    public static extern uint GetCurrentThreadId();

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool MoveWindow(IntPtr hwnd, int x, int y, int width, int height, bool repaint);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool PostMessageW(IntPtr hwnd, uint message, IntPtr wparam, IntPtr lparam);

    public const uint WmSetText = 0x000C;
    public const uint SmtoAbortIfHung = 0x0002;

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr SendMessageTimeoutW(IntPtr hwnd, uint message, IntPtr wparam, string lparam, uint flags, uint timeoutMs, out IntPtr result);

    /// <summary><c>WM_SETTEXT</c> to a text window, bounded so a hung application cannot hold the companion.</summary>
    public static bool SetWindowTextMessage(IntPtr hwnd, string text)
        => SendMessageTimeoutW(hwnd, WmSetText, IntPtr.Zero, text, SmtoAbortIfHung, 2000, out var result) != IntPtr.Zero && result != IntPtr.Zero;

    // ------------------------------------------------------------------ process image

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool QueryFullProcessImageNameW(IntPtr process, uint flags, StringBuilder name, ref int size);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr handle);

    [DllImport("kernel32.dll")]
    public static extern ulong GetTickCount64();

    // ------------------------------------------------------------------ paths: resolve, then contain

    public const uint FileReadAttributes = 0x0080;
    public const uint FileShareReadWriteDelete = 0x0001 | 0x0002 | 0x0004;
    public const uint OpenExisting = 3;
    public const uint FileFlagBackupSemantics = 0x02000000;
    public const uint FileNameNormalizedVolumeNameDos = 0;
    public const int MaxFinalPathChars = 32768;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern SafeFileHandle CreateFileW(string fileName, uint desiredAccess, uint shareMode, IntPtr securityAttributes, uint creationDisposition, uint flagsAndAttributes, IntPtr templateFile);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern uint GetFinalPathNameByHandleW(SafeFileHandle file, StringBuilder path, uint size, uint flags);

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    public static extern int SHGetKnownFolderPath(ref Guid folderId, uint flags, IntPtr token, out IntPtr path);

    [DllImport("ole32.dll")]
    public static extern void CoTaskMemFree(IntPtr memory);

    /// <summary>
    /// The path the file system ITSELF reports for what <paramref name="path"/> opens — every
    /// junction, mount point and symbolic link in every component followed — or null when it
    /// cannot be opened (missing, unreadable, on a volume without a drive letter). The open asks
    /// for attributes only (no data access) and <c>FILE_FLAG_BACKUP_SEMANTICS</c> so a directory
    /// can be opened at all; <c>FILE_FLAG_OPEN_REPARSE_POINT</c> is deliberately NOT set, so the
    /// handle is the target's, never the link's. Prefixes (<c>\\?\</c>) are left for the caller.
    /// </summary>
    public static string? FinalPath(string path)
    {
        try
        {
            using var handle = CreateFileW(path, FileReadAttributes, FileShareReadWriteDelete, IntPtr.Zero, OpenExisting, FileFlagBackupSemantics, IntPtr.Zero);
            if (handle.IsInvalid)
            {
                return null;
            }

            var builder = new StringBuilder(1024);
            var length = GetFinalPathNameByHandleW(handle, builder, (uint)builder.Capacity, FileNameNormalizedVolumeNameDos);
            if (length >= builder.Capacity && length < MaxFinalPathChars)
            {
                builder.Capacity = (int)length + 1;
                length = GetFinalPathNameByHandleW(handle, builder, (uint)builder.Capacity, FileNameNormalizedVolumeNameDos);
            }

            if (length == 0 || length >= builder.Capacity)
            {
                return null;
            }

            return builder.ToString(0, (int)length);
        }
        catch (Exception)
        {
            return null;
        }
    }

    /// <summary>A known folder's current path (the owner may have moved Downloads), or null.</summary>
    public static string? KnownFolderPath(Guid folderId)
    {
        var memory = IntPtr.Zero;
        try
        {
            if (SHGetKnownFolderPath(ref folderId, 0, IntPtr.Zero, out memory) != 0 || memory == IntPtr.Zero)
            {
                return null;
            }

            return Marshal.PtrToStringUni(memory);
        }
        catch (Exception)
        {
            return null;
        }
        finally
        {
            if (memory != IntPtr.Zero)
            {
                CoTaskMemFree(memory);
            }
        }
    }

    // ------------------------------------------------------------------ input + pointer

    [DllImport("user32.dll", SetLastError = true)]
    public static extern uint SendInput(uint count, Input[] inputs, int structureSize);

    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    public static extern bool GetCursorPos(out Point point);

    [DllImport("user32.dll")]
    public static extern int GetSystemMetrics(int index);

    // ------------------------------------------------------------------ pixels

    [DllImport("user32.dll")]
    public static extern IntPtr GetDC(IntPtr hwnd);

    [DllImport("user32.dll")]
    public static extern int ReleaseDC(IntPtr hwnd, IntPtr hdc);

    [DllImport("gdi32.dll")]
    public static extern IntPtr CreateCompatibleDC(IntPtr hdc);

    [DllImport("gdi32.dll")]
    public static extern IntPtr CreateDIBSection(IntPtr hdc, ref BitmapInfo info, uint usage, out IntPtr bits, IntPtr section, uint offset);

    [DllImport("gdi32.dll")]
    public static extern IntPtr SelectObject(IntPtr hdc, IntPtr obj);

    [DllImport("gdi32.dll")]
    public static extern bool DeleteObject(IntPtr obj);

    [DllImport("gdi32.dll")]
    public static extern bool DeleteDC(IntPtr hdc);

    [DllImport("gdi32.dll")]
    public static extern bool BitBlt(IntPtr dest, int x, int y, int width, int height, IntPtr source, int sx, int sy, uint rop);

    [DllImport("user32.dll")]
    public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);

    // ------------------------------------------------------------------ desktop presence

    [DllImport("user32.dll")]
    public static extern IntPtr GetProcessWindowStation();

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool GetUserObjectInformationW(IntPtr obj, int index, ref UserObjectFlags info, int length, out int needed);

    /// <summary>
    /// Whether this process sits on a VISIBLE window station — the one condition under which
    /// anything in this module can observe or drive a desktop. Session 0 and a headless CI
    /// runner say false here; the owner's session says true.
    /// </summary>
    public static bool HasVisibleDesktop()
    {
        try
        {
            var station = GetProcessWindowStation();
            if (station == IntPtr.Zero)
            {
                return false;
            }

            var flags = new UserObjectFlags();
            if (!GetUserObjectInformationW(station, UoiFlags, ref flags, Marshal.SizeOf<UserObjectFlags>(), out _))
            {
                return false;
            }

            return (flags.Flags & WsfVisible) != 0;
        }
        catch (Exception)
        {
            return false;
        }
    }

    // ------------------------------------------------------------------ session lock (ADR-0199)

    public const uint DesktopReadObjects = 0x0001;

    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint desiredAccess);

    [DllImport("user32.dll")]
    public static extern bool CloseDesktop(IntPtr desktop);

    /// <summary>
    /// Whether the session is locked (or otherwise on a desktop this process may not act on):
    /// the input desktop is the Winlogon secure desktop then, and an ordinary owner-session
    /// process cannot open it. Measured by opening it for read and closing it again — the one
    /// probe that answers "locked" without a hook or a session notification window. A false
    /// here says only that the default desktop is the input desktop; the foreground check
    /// still decides what is in front on it.
    /// </summary>
    public static bool IsSessionLocked()
    {
        try
        {
            var desktop = OpenInputDesktop(0, false, DesktopReadObjects);
            if (desktop == IntPtr.Zero)
            {
                return true;
            }

            CloseDesktop(desktop);
            return false;
        }
        catch (Exception)
        {
            return true;
        }
    }

    public static string WindowText(IntPtr hwnd)
    {
        var length = GetWindowTextLengthW(hwnd);
        if (length <= 0)
        {
            return string.Empty;
        }

        var builder = new StringBuilder(length + 1);
        GetWindowTextW(hwnd, builder, builder.Capacity);
        return builder.ToString();
    }

    public static string ClassName(IntPtr hwnd)
    {
        var builder = new StringBuilder(256);
        return GetClassNameW(hwnd, builder, builder.Capacity) > 0 ? builder.ToString() : string.Empty;
    }

    /// <summary>The process image FILE NAME (never the path) for a pid, lower-case; "?" when it cannot be read.</summary>
    public static string ImageName(uint pid)
    {
        var handle = OpenProcess(ProcessQueryLimitedInformation, false, pid);
        if (handle != IntPtr.Zero)
        {
            try
            {
                var builder = new StringBuilder(1024);
                var size = builder.Capacity;
                if (QueryFullProcessImageNameW(handle, 0, builder, ref size))
                {
                    return Path.GetFileName(builder.ToString()).ToLowerInvariant();
                }
            }
            finally
            {
                CloseHandle(handle);
            }
        }

        try
        {
            return (System.Diagnostics.Process.GetProcessById((int)pid).ProcessName + ".exe").ToLowerInvariant();
        }
        catch (Exception)
        {
            return "?";
        }
    }

    public static bool IsCloaked(IntPtr hwnd)
    {
        try
        {
            return DwmGetWindowAttribute(hwnd, DwmwaCloaked, out var cloaked, sizeof(int)) == 0 && cloaked != 0;
        }
        catch (Exception)
        {
            return false;
        }
    }

    public static bool IsToolWindow(IntPtr hwnd)
        => (GetWindowLongPtr(hwnd, GwlExStyle).ToInt64() & WsExToolWindow) != 0;
}
