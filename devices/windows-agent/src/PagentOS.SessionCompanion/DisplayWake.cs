using System.Runtime.InteropServices;
using System.Runtime.Versioning;

namespace PagentOS.SessionCompanion;

/// <summary>
/// The two steps of <c>desktop.display_wake</c> (DEVICE_PROTOCOL.md §6e, M18.3), in the order
/// they must happen, behind an interface so a test asserts the order without a monitor
/// lighting up on the machine running it.
///
/// <para>Why two steps and not one: the execution-state request tells Windows the display is
/// needed and resets the idle timer, which is enough to keep a screen awake but not always
/// enough to bring a dark one back. The zero-delta pointer move is what actually wakes it,
/// because it is a real input event as far as the session is concerned. Together they are
/// what a person moving a mouse does, and nothing more.</para>
/// </summary>
public interface IDisplayWake
{
    /// <summary>
    /// A MOMENTARY display-required request: it resets the display idle timer once and
    /// returns. It deliberately does not make the request continuous — a continuous request
    /// would keep the owner's screen alive until something remembered to clear it, and a
    /// forgotten one is indistinguishable from a broken power plan.
    /// </summary>
    void RequestDisplayNeededOnce();

    /// <summary>
    /// A zero-delta pointer move: the smallest real input event there is. The pointer does not
    /// travel, no button changes, and no key is involved at any point.
    /// </summary>
    void NudgePointer();
}

/// <summary>
/// Production implementation. Runs in the owner's interactive session (the companion), because
/// an execution-state request made in Session 0 speaks for a desktop the owner cannot see.
///
/// <para><b>There is no keyboard here, structurally.</b> The synthetic-input structure this
/// class declares has one member, and it is the pointer one; there is no key-event member to
/// fill in, so this file cannot type into the owner's foreground window even by mistake. A
/// structural test asserts the same thing from the outside.</para>
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class Win32DisplayWake : IDisplayWake
{
    /// <summary>
    /// The display-required flag, sent ALONE. The continuous flag is deliberately absent and a
    /// structural test asserts it never appears: this call is a single reset of the idle
    /// timer, not a standing claim on the owner's screen.
    /// </summary>
    private const uint DisplayNeeded = 0x00000002;

    /// <summary>The synthetic-input kind for a pointer event. There is no other kind in this file.</summary>
    private const uint PointerInput = 0;

    /// <summary>Relative pointer motion. With dx = dy = 0 it moves nothing and wakes the display.</summary>
    private const uint PointerMove = 0x0001;

    public void RequestDisplayNeededOnce()
    {
        if (SetThreadExecutionState(DisplayNeeded) == 0)
        {
            throw new InvalidOperationException("SetThreadExecutionState(display required) was refused");
        }
    }

    public void NudgePointer()
    {
        var events = new SyntheticInput[]
        {
            new()
            {
                Kind = PointerInput,
                Pointer = new PointerEvent { Dx = 0, Dy = 0, Data = 0, Flags = PointerMove, Time = 0, Extra = IntPtr.Zero },
            },
        };

        var sent = SendInput((uint)events.Length, events, Marshal.SizeOf<SyntheticInput>());
        if (sent != events.Length)
        {
            var error = Marshal.GetLastWin32Error();
            throw new InvalidOperationException($"SendInput(pointer move, 0,0) was refused (win32={error})");
        }
    }

    /// <summary>
    /// The pointer half of the Win32 synthetic-input union — and only that half. The native
    /// union is larger than this member, which is harmless: the extra bytes are the padding
    /// <c>Marshal.SizeOf</c> already accounts for on both architectures, and nothing this
    /// process writes ever lands in the members it does not declare.
    /// </summary>
    [StructLayout(LayoutKind.Sequential)]
    private struct PointerEvent
    {
        public int Dx;
        public int Dy;
        public uint Data;
        public uint Flags;
        public uint Time;
        public IntPtr Extra;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SyntheticInput
    {
        public uint Kind;
        public PointerEvent Pointer;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint SetThreadExecutionState(uint flags);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint SendInput(uint count, SyntheticInput[] inputs, int structureSize);
}
