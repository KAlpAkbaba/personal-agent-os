using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Security.Principal;
using PagentOS.Agent.Core.Ipc;

namespace PagentOS.SessionCompanion;

/// <summary>
/// Reads the owner SID of the pipe the companion connected to.
///
/// The owner of a kernel object is the account of whoever created it. A standard user
/// process cannot create an object owned by LocalSystem, so "this pipe is owned by SYSTEM"
/// is a claim only the real service can make — unlike the pipe's name, which anyone can
/// use, and unlike anything sent over the pipe, which anyone connected can say.
///
/// Read through <c>GetSecurityInfo</c> on the pipe handle rather than a managed wrapper:
/// the owner is the one field needed and this asks the kernel for exactly it.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class WindowsPipeOwnerInspector : IPipeOwnerInspector
{
    private const int SePipeObject = 6;              // SE_OBJECT_TYPE.SE_KERNEL_OBJECT
    private const uint OwnerSecurityInformation = 0x00000001;

    public string? OwnerSid(NamedPipeClientStream client)
    {
        IntPtr descriptor = IntPtr.Zero;
        try
        {
            var handle = client.SafePipeHandle.DangerousGetHandle();
            var status = GetSecurityInfo(
                handle,
                SePipeObject,
                OwnerSecurityInformation,
                out var ownerPtr,
                out _,
                out _,
                out _,
                out descriptor);

            if (status != 0 || ownerPtr == IntPtr.Zero)
            {
                return null;
            }

            return new SecurityIdentifier(ownerPtr).Value;
        }
        catch (Exception)
        {
            // Cannot read the descriptor: treat as unknown, which the policy refuses.
            return null;
        }
        finally
        {
            if (descriptor != IntPtr.Zero)
            {
                LocalFree(descriptor);
            }
        }
    }

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern int GetSecurityInfo(
        IntPtr handle,
        int objectType,
        uint securityInformation,
        out IntPtr owner,
        out IntPtr group,
        out IntPtr dacl,
        out IntPtr sacl,
        out IntPtr securityDescriptor);

    [DllImport("kernel32.dll")]
    private static extern IntPtr LocalFree(IntPtr handle);
}

/// <summary>Test/dev seam: a fixed answer, for cases where no real descriptor exists.</summary>
public sealed class StaticPipeOwnerInspector(string? ownerSid) : IPipeOwnerInspector
{
    public string? OwnerSid(NamedPipeClientStream client) => ownerSid;
}
