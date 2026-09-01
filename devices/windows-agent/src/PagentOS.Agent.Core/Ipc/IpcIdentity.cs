namespace PagentOS.Agent.Core.Ipc;

/// <summary>
/// What the operating system says about the process on the other end of a named pipe.
///
/// Every field here is obtained from the kernel (client impersonation token, pipe client
/// process id, session id, image path), never from anything the peer sends us. That
/// distinction is the whole point: a peer can claim anything in a JSON frame, so the
/// admission decision is made on facts it cannot author.
/// </summary>
public sealed record PipePeer
{
    /// <summary>SID of the account the peer process runs as.</summary>
    public required string Sid { get; init; }

    /// <summary>Windows terminal-services session. 0 is the service session, never a desktop.</summary>
    public required int SessionId { get; init; }

    public required int ProcessId { get; init; }

    /// <summary>Full image path of the peer process, or null when it could not be resolved.</summary>
    public string? ImagePath { get; init; }
}

/// <summary>
/// Why an IPC peer or frame was refused. The refusing side logs the precise value; the
/// peer is told nothing at all (the connection simply closes), because a local attacker
/// probing which check it failed learns how to pass the next one.
/// </summary>
public enum IpcRefusal
{
    None = 0,

    /// <summary>The kernel would not tell us who the peer is.</summary>
    UnidentifiablePeer,

    /// <summary>The peer runs as some account other than the authorized owner.</summary>
    SidMismatch,

    /// <summary>The peer lives in Session 0 — a service pretending to be the desktop half.</summary>
    ServiceSessionPeer,

    /// <summary>The peer is in an interactive session other than the authorized one.</summary>
    SessionMismatch,

    /// <summary>The peer is the right user in the right session, but is not the companion binary.</summary>
    ImagePathMismatch,

    /// <summary>The pipe we connected to is not owned by an account allowed to run the service.</summary>
    UntrustedPipeOwner,

    /// <summary>Handshake absent, malformed, or answering a challenge we never issued.</summary>
    HandshakeFailed,

    /// <summary>A frame carrying a connection id that is not the live one (replay across connections).</summary>
    StaleConnection,

    /// <summary>A frame whose sequence number has already been seen (replay within a connection).</summary>
    ReplayedFrame,
}

public static class IpcRefusalText
{
    /// <summary>Operator-facing explanation. Logged locally; never sent to the peer.</summary>
    public static string Describe(IpcRefusal refusal) => refusal switch
    {
        IpcRefusal.None => "allowed",
        IpcRefusal.UnidentifiablePeer => "the peer's identity could not be obtained from the OS",
        IpcRefusal.SidMismatch => "peer runs as a different account than the authorized owner",
        IpcRefusal.ServiceSessionPeer => "peer is in Session 0, which is never the interactive desktop",
        IpcRefusal.SessionMismatch => "peer is in a different Windows session than the authorized one",
        IpcRefusal.ImagePathMismatch => "peer process is not the installed companion executable",
        IpcRefusal.UntrustedPipeOwner => "the named pipe is not owned by an account trusted to run the service",
        IpcRefusal.HandshakeFailed => "handshake missing, malformed, or answering an unissued challenge",
        IpcRefusal.StaleConnection => "frame carries a connection id from an earlier connection",
        IpcRefusal.ReplayedFrame => "frame sequence number has already been seen",
        _ => "refused",
    };
}
