using System.Runtime.InteropServices;
using System.Runtime.Versioning;

namespace PagentOS.SessionCompanion;

/// <summary>
/// How long the owner's session has been idle — and nothing else about their input
/// (DEVICE_PROTOCOL.md §6g, M18.3).
///
/// <para><b>The narrowness is the design.</b> Windows will happily tell a program in the
/// owner's session which keys are down, what was typed, and where the pointer is. This agent
/// asks for exactly one number: the tick count of the most recent input event, from which one
/// subtraction gives an idle duration. No key, no character, no coordinate, no window title is
/// ever read, and none is ever stored or sent. A structural test
/// (<c>AmbientCapabilityTests.The_activity_source_reads_a_tick_count_and_never_the_owners_input</c>)
/// reads this source and fails if a hook, a key-state or a pointer-position API name appears
/// in it, so the file cannot quietly become a keylogger.</para>
///
/// <para>Idle time is <b>nullable on purpose</b>. A companion built without a real source (a
/// test, a non-Windows host) reports <c>null</c>, which the protocol carries as
/// <c>input_idle_s: null</c>: "this device does not know". That is a different statement from
/// "the owner has been away for zero seconds", and the display-off holdoff treats it as such —
/// an unknown idle never satisfies the holdoff on its own.</para>
/// </summary>
public interface IInputActivitySource
{
    /// <summary>Time since the last input event in the owner's session, or null when unknown.</summary>
    TimeSpan? IdleTime { get; }
}

/// <summary>
/// <c>GetLastInputInfo</c>, which returns one 32-bit tick count and no content whatsoever.
/// Runs in the owner's interactive session; from Session 0 it would describe nothing the
/// owner did (CLAUDE.md Windows Agent rule).
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class Win32InputActivitySource : IInputActivitySource
{
    public TimeSpan? IdleTime
    {
        get
        {
            var info = new LastInputInfo { StructSize = (uint)Marshal.SizeOf<LastInputInfo>() };
            if (!GetLastInputInfo(ref info))
            {
                return null;
            }

            // Both counters wrap every ~49.7 days; the unchecked subtraction of two uints
            // wraps with them, so the difference stays correct across the rollover.
            var elapsedMs = unchecked((uint)Environment.TickCount - info.LastInputTick);
            return TimeSpan.FromMilliseconds(elapsedMs);
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct LastInputInfo
    {
        public uint StructSize;

        /// <summary>A tick count. Not a key, not a character, not a coordinate.</summary>
        public uint LastInputTick;
    }

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetLastInputInfo(ref LastInputInfo info);
}

/// <summary>A source that knows nothing, used where no real one is wired. Always <c>null</c>.</summary>
public sealed class UnknownInputActivitySource : IInputActivitySource
{
    public static UnknownInputActivitySource Instance { get; } = new();

    public TimeSpan? IdleTime => null;
}
