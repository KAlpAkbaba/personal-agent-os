using System.Security.Cryptography;

namespace PagentOS.Agent.Core.Ipc;

/// <summary>
/// Per-connection freshness state: binds every frame to the connection it was written on
/// and to a position in that connection's stream.
///
/// Identity checks answer "who is on the other end". They do not answer "did this frame
/// actually arrive now". Two things must also be true, and neither follows from the ACL:
///
/// - a frame from an earlier connection must not be honoured on a later one (a companion
///   that reconnects after a logoff, or a captured frame fed back in, must not be able to
///   act on credentials the service has already retired);
/// - a frame already seen on this connection must not be honoured twice (a replayed
///   exec_response would otherwise let one answer satisfy a later, different request).
///
/// The connection id is a fresh random value the service mints per accepted connection and
/// the companion echoes; the sequence number is strictly increasing per direction. Both are
/// checked before a frame is allowed to do anything.
/// </summary>
public sealed class IpcChannelGuard
{
    private readonly object _sync = new();
    private long _lastInboundSeq;
    private long _outboundSeq;

    public IpcChannelGuard(string connectionId)
    {
        if (string.IsNullOrWhiteSpace(connectionId))
        {
            throw new ArgumentException("a connection id is required", nameof(connectionId));
        }

        ConnectionId = connectionId;
    }

    public string ConnectionId { get; }

    /// <summary>Highest inbound sequence accepted so far (0 before the first frame).</summary>
    public long LastInboundSeq
    {
        get
        {
            lock (_sync)
            {
                return _lastInboundSeq;
            }
        }
    }

    /// <summary>A 256-bit connection id / nonce. Random, not derived from anything guessable.</summary>
    public static string NewToken() => Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant();

    public static IpcChannelGuard Create() => new(NewToken());

    public long NextOutboundSeq()
    {
        lock (_sync)
        {
            return ++_outboundSeq;
        }
    }

    /// <summary>
    /// Accept an inbound frame, or say why not. A refused frame does not advance any state,
    /// so a flood of replays cannot push the window forward.
    /// </summary>
    public IpcRefusal Accept(string? connectionId, long seq)
    {
        if (string.IsNullOrWhiteSpace(connectionId)
            || !FixedTimeEquals(connectionId, ConnectionId))
        {
            return IpcRefusal.StaleConnection;
        }

        lock (_sync)
        {
            if (seq <= 0 || seq <= _lastInboundSeq)
            {
                return IpcRefusal.ReplayedFrame;
            }

            _lastInboundSeq = seq;
            return IpcRefusal.None;
        }
    }

    /// <summary>
    /// Compare connection ids without leaking a match prefix through timing. These are
    /// local, short-lived values, but a comparison that gives up early is a habit worth not
    /// having in an authentication path.
    /// </summary>
    public static bool FixedTimeEquals(string left, string right)
    {
        var a = System.Text.Encoding.UTF8.GetBytes(left);
        var b = System.Text.Encoding.UTF8.GetBytes(right);
        return CryptographicOperations.FixedTimeEquals(a, b);
    }
}
