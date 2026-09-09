using System.IO.Pipes;
using System.Security.AccessControl;
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

    /// <summary>
    /// A real pipe whose OWNER is explicitly this account's user SID — never a service
    /// account — so "an untrusted process is squatting the pipe name" is staged the same
    /// way on every host.
    ///
    /// Letting Windows pick the owner does not do that. The owner of a new object comes
    /// from the creating token's default owner, and for a member of Administrators running
    /// ELEVATED that default is BUILTIN\Administrators (S-1-5-32-544) — an account
    /// <see cref="ServiceAdmissionPolicy.ServiceMode"/> deliberately trusts. So on an
    /// elevated host (every GitHub Actions runner, and the owner's own elevated console)
    /// the "standard user" pipe these tests build was silently owned by Administrators,
    /// the policy correctly accepted it, and the refusal under test never happened. The
    /// product was right and the test was asserting the environment. Stating the owner
    /// removes the assumption instead of skipping the test.
    /// </summary>
    public static NamedPipeServerStream NewPipeOwnedByCurrentUser(string pipeName)
    {
        var user = new SecurityIdentifier(CurrentSid());
        var security = new PipeSecurity();
        security.SetOwner(user);
        // An empty DACL would deny everyone, including the client half of the test.
        security.AddAccessRule(new PipeAccessRule(user, PipeAccessRights.FullControl, AccessControlType.Allow));

        return NamedPipeServerStreamAcl.Create(
            pipeName,
            PipeDirection.InOut,
            maxNumberOfServerInstances: 1,
            PipeTransmissionMode.Byte,
            PipeOptions.Asynchronous,
            inBufferSize: 0,
            outBufferSize: 0,
            security);
    }

    /// <summary>Admission policy that accepts this test process — the honest path.</summary>
    public static CompanionAdmissionPolicy SelfPolicy(string? imagePath = null, int? sessionId = null)
        => new(CurrentSid(), imagePath, sessionId);

    /// <summary>
    /// Start the server AND wait until the pipe is really listening.
    ///
    /// "StartAsync returned" is not "the pipe exists". CompanionPipeServer.ExecuteAsync
    /// creates the first instance before its first await, so normally it is - but if
    /// CreateServerStream throws, the loop logs, waits five seconds and only THEN awaits,
    /// so StartAsync returns with nothing listening and every client burns its full
    /// connect timeout before failing with a bare TimeoutException that names no cause.
    /// That is what happened in CI on 2026-09-05.
    ///
    /// This is the same assumption that cost the owner a qualification run (ADR-0051
    /// addendum 2: the harness assumed a started shell was a ready shell). Wait for the
    /// observable fact instead, and if it never arrives, say WHY - ListenFailures
    /// distinguishes "the pipe could not be created" from "the test was too quick".
    /// </summary>
    public static async Task<CompanionPipeServer> NewListeningServerAsync(
        string pipeName,
        CompanionAdmissionPolicy? policy = null,
        IPipePeerInspector? inspector = null,
        int readyTimeoutMs = 10000)
    {
        var server = NewServer(pipeName, policy, inspector);
        await server.StartAsync(CancellationToken.None);
        await WaitUntilListeningAsync(server, pipeName, readyTimeoutMs);
        return server;
    }

    /// <summary>Block until the named pipe exists, or throw saying why it does not.</summary>
    public static async Task WaitUntilListeningAsync(
        CompanionPipeServer server, string pipeName, int timeoutMs = 10000)
    {
        var path = $@"\\.\pipe\{pipeName}";
        var deadline = DateTime.UtcNow.AddMilliseconds(timeoutMs);
        while (DateTime.UtcNow < deadline)
        {
            // Directory enumeration, not File.Exists: opening the path would CONSUME the
            // listening instance and disrupt admission (see CompanionPipeServer.LastPipeSddl).
            if (Directory.GetFiles(@"\\.\pipe\").Any(
                    p => p.EndsWith(pipeName, StringComparison.OrdinalIgnoreCase)))
            {
                return;
            }

            await Task.Delay(25);
        }

        throw new TimeoutException(
            $"pipe {path} never started listening within {timeoutMs} ms " +
            $"(ListenFailures={server.ListenFailures}). A non-zero ListenFailures means the " +
            "pipe could not be created at all, not that the test raced ahead of it.");
    }

    /// <param name="brokerOrigin">M22: the Cloud Core origin the service tells the companion in its challenge; null stages an older service that says none.</param>
    public static CompanionPipeServer NewServer(
        string pipeName,
        CompanionAdmissionPolicy? policy = null,
        IPipePeerInspector? inspector = null,
        string? brokerOrigin = null)
        => new(
            pipeName,
            policy ?? SelfPolicy(),
            inspector ?? new WindowsPipePeerInspector(),
            NullLogger<CompanionPipeServer>.Instance,
            brokerOrigin: brokerOrigin);

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


/// <summary>Reading a log file that something is still writing to.</summary>
public static class LiveLog
{
    /// <summary>
    /// The contents of <paramref name="path"/>, opened with the share flags its writer
    /// grants.
    /// </summary>
    /// <remarks>
    /// <c>File.ReadAllText</c> opens with <c>FileShare.Read</c>, which denies the writer -
    /// and <c>AuditLog</c>'s own comment names that call as the hazard, measured on the
    /// runner on 2026-09-08 (CI run 34204979854). The reverse also happens, and is what
    /// failed CI run 34339398396: the writer held the file and the READ was refused.
    /// Opening with <c>FileShare.ReadWrite | FileShare.Delete</c> - what
    /// <c>AuditLog</c> itself uses - removes both directions rather than narrowing one.
    /// </remarks>
    public static string Read(string path)
    {
        if (!File.Exists(path))
        {
            return string.Empty;
        }

        using var stream = new FileStream(
            path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
        using var reader = new StreamReader(stream);
        return reader.ReadToEnd();
    }

    /// <summary>
    /// The contents of <paramref name="path"/> once they contain <paramref name="needle"/>,
    /// or whatever they hold when the deadline passes — so the CALLER's assertion is what
    /// fails, with the real text, rather than this method throwing.
    /// </summary>
    /// <remarks>
    /// Two clocks for one decision, which is this repository's most recurrent bug shape. A
    /// test that has waited for an IN-MEMORY signal (a server's <c>LastPipeSddl</c>, a
    /// worker's pid) has learned nothing about whether the audit ROW has reached the disk:
    /// the row is appended by a different path, and CI run 34363259200 read an empty file
    /// microseconds after the pipe reported its SDDL. Waiting for the file to say what the
    /// test is about to assert removes the second clock; a row that never arrives still
    /// fails, just with the assertion the test actually wrote.
    /// </remarks>
    public static async Task<string> WaitForAsync(string path, string needle, TimeSpan? timeout = null)
    {
        var deadline = DateTime.UtcNow + (timeout ?? TimeSpan.FromSeconds(15));
        var content = Read(path);
        while (!content.Contains(needle, StringComparison.Ordinal) && DateTime.UtcNow < deadline)
        {
            await Task.Delay(20);
            content = Read(path);
        }

        return content;
    }
}
