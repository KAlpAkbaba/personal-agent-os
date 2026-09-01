using System.IO.Pipes;
using System.Security.Principal;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Core.Ipc;
using PagentOS.DeviceService;

namespace PagentOS.Agent.Tests.Support;

/// <summary>
/// Shared scaffolding for the IPC tests.
///
/// Two kinds of test live on top of this, and the difference is deliberate rather than
/// incidental:
///
/// - <b>real</b> — a genuine named pipe between two processes' worth of code in one test
///   process, with <see cref="WindowsPipePeerInspector"/> asking the kernel who connected.
///   The honest path and the pipe-squatting refusal are proven this way.
/// - <b>injected</b> — a <see cref="FixedPeerInspector"/> that reports an identity the test
///   chooses. Staging a peer that runs as a different account, or lives in another Windows
///   logon session, needs a second user and a second interactive logon; that is an owner
///   action on a real machine, not something a unit test can conjure. So the adversarial
///   identities are injected at the one seam where the kernel's answer enters the system,
///   and the qualification matrix records these as PROVEN_PROXY until the service runs for
///   real under LocalSystem.
/// </summary>
public static class IpcTestSupport
{
    public static string CurrentSid()
        => WindowsIdentity.GetCurrent().User?.Value
           ?? throw new InvalidOperationException("no SID for the current test process");

    public static string NewPipeName() => $"pagentos-test-{Guid.NewGuid():N}";

    /// <summary>Admission policy that accepts this test process — the honest path.</summary>
    public static CompanionAdmissionPolicy SelfPolicy(string? imagePath = null, int? sessionId = null)
        => new(CurrentSid(), imagePath, sessionId);

    public static CompanionPipeServer NewServer(
        string pipeName,
        CompanionAdmissionPolicy? policy = null,
        IPipePeerInspector? inspector = null)
        => new(
            pipeName,
            policy ?? SelfPolicy(),
            inspector ?? new WindowsPipePeerInspector(),
            NullLogger<CompanionPipeServer>.Instance);

    /// <summary>The identity of this test process as the kernel reports it.</summary>
    public static PipePeer SelfPeer()
    {
        using var process = System.Diagnostics.Process.GetCurrentProcess();
        return new PipePeer
        {
            Sid = CurrentSid(),
            SessionId = process.SessionId,
            ProcessId = process.Id,
            ImagePath = Environment.ProcessPath,
        };
    }
}

/// <summary>An inspector that reports whatever identity the test wants the kernel to have said.</summary>
public sealed class FixedPeerInspector(PipePeer? peer) : IPipePeerInspector
{
    public int Calls { get; private set; }

    public PipePeer? Inspect(NamedPipeServerStream server)
    {
        Calls++;
        return peer;
    }
}
