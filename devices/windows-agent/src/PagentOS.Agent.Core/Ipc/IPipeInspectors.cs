using System.IO.Pipes;

namespace PagentOS.Agent.Core.Ipc;

/// <summary>
/// Reads the connected client's OS identity off an accepted pipe. Behind an interface for
/// one reason: the adversarial cases worth testing — a peer running as another account, a
/// peer in another Windows session, a peer that is not the companion binary — cannot be
/// staged on a developer machine without a second user account and a second logon. The
/// real implementation is exercised by the honest-path tests; the hostile identities are
/// injected. Which of the two a given test used is recorded in the qualification matrix
/// rather than glossed over.
/// </summary>
public interface IPipePeerInspector
{
    /// <summary>Identity of the process on the other end, or null if the OS would not say.</summary>
    PipePeer? Inspect(NamedPipeServerStream server);
}

/// <summary>
/// Reads the owner SID of the pipe object the companion has connected to. The owner of a
/// kernel object is its creator's account, which is why this — and not the pipe's name — is
/// what tells the companion whether it is talking to a service or to an impostor.
/// </summary>
public interface IPipeOwnerInspector
{
    string? OwnerSid(NamedPipeClientStream client);
}
