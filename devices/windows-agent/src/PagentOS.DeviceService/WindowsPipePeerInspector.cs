using System.Diagnostics;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Security.Principal;
using System.Text;
using PagentOS.Agent.Core.Ipc;

namespace PagentOS.DeviceService;

/// <summary>
/// The real thing: asks Windows who is on the other end of an accepted pipe.
///
/// Three separate questions, three separate mechanisms, none of them anything the peer can
/// influence:
///
/// - <b>account</b> — impersonate the client for the length of one call and read the SID off
///   the resulting token (<c>RunAsClient</c>). The client must have connected at
///   Identification level or better for this to work, which the companion does deliberately.
/// - <b>session</b> — <c>GetNamedPipeClientProcessId</c> then <c>ProcessIdToSessionId</c>.
///   The pipe itself tells us the process id, so a peer cannot substitute another one.
/// - <b>binary</b> — <c>QueryFullProcessImageName</c> on a limited-information handle to that
///   same process id.
///
/// Anything that fails returns null (or a null image path) rather than a guess, and the
/// admission policy refuses on null. Failing closed here matters more than diagnosing why.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class WindowsPipePeerInspector : IPipePeerInspector
{
    private const int ProcessQueryLimitedInformation = 0x1000;

    public PipePeer? Inspect(NamedPipeServerStream server)
    {
        var sid = ClientSid(server);
        if (sid is null)
        {
            return null;
        }

        var processId = ClientProcessId(server);
        if (processId is null)
        {
            // We know the account but not the process: that is not enough to admit a peer
            // whose binary and session we are supposed to check.
            return null;
        }

        return new PipePeer
        {
            Sid = sid,
            ProcessId = processId.Value,
            SessionId = SessionOf(processId.Value) ?? 0,
            ImagePath = ImagePathOf(processId.Value),
        };
    }

    private static string? ClientSid(NamedPipeServerStream server)
    {
        string? sid = null;
        try
        {
            server.RunAsClient(() =>
            {
                using var identity = WindowsIdentity.GetCurrent();
                sid = identity.User?.Value;
            });
        }
        catch (Exception)
        {
            return null;
        }

        return sid;
    }

    private static int? ClientProcessId(NamedPipeServerStream server)
    {
        try
        {
            var handle = server.SafePipeHandle.DangerousGetHandle();
            return GetNamedPipeClientProcessId(handle, out var pid) ? (int)pid : null;
        }
        catch (Exception)
        {
            return null;
        }
    }

    private static int? SessionOf(int processId)
    {
        try
        {
            return ProcessIdToSessionId((uint)processId, out var session) ? (int)session : null;
        }
        catch (Exception)
        {
            return null;
        }
    }

    private static string? ImagePathOf(int processId)
    {
        var handle = OpenProcess(ProcessQueryLimitedInformation, false, (uint)processId);
        if (handle == IntPtr.Zero)
        {
            // Fall back to the managed API, which can still answer for same-session peers.
            try
            {
                using var process = Process.GetProcessById(processId);
                return process.MainModule?.FileName;
            }
            catch (Exception)
            {
                return null;
            }
        }

        try
        {
            var buffer = new StringBuilder(1024);
            var size = buffer.Capacity;
            return QueryFullProcessImageName(handle, 0, buffer, ref size) ? buffer.ToString() : null;
        }
        catch (Exception)
        {
            return null;
        }
        finally
        {
            CloseHandle(handle);
        }
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetNamedPipeClientProcessId(IntPtr pipe, out uint clientProcessId);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool ProcessIdToSessionId(uint processId, out uint sessionId);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(int desiredAccess, [MarshalAs(UnmanagedType.Bool)] bool inheritHandle, uint processId);

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool QueryFullProcessImageName(IntPtr process, int flags, StringBuilder exeName, ref int size);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CloseHandle(IntPtr handle);
}
