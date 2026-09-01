namespace PagentOS.Agent.Core.Ipc;

/// <summary>
/// The Device Service's answer to "is the process on my pipe the owner's companion?".
///
/// Once the service runs as LocalSystem in Session 0, an ACL alone is not an identity
/// model: it says who may open the pipe, not who did. This policy is the second half —
/// it judges the kernel-supplied facts about the connected process. It is deliberately
/// pure (no Windows API calls, no I/O) so every refusal path can be tested exactly,
/// including the ones that would otherwise need a second user account or a second logon
/// session to reproduce.
///
/// Three independent things must hold, and each has its own refusal so the audit log says
/// which one failed:
///
/// 1. <b>Account</b> — the peer runs as the authorized owner SID. Keeps every other user
///    on the machine out, including other logged-on accounts.
/// 2. <b>Session</b> — the peer is in an interactive session, and in the pinned one when
///    the service knows which that is. Session 0 is refused outright: a companion there
///    would defeat the reason the service/companion split exists.
/// 3. <b>Binary</b> — the peer's image is the installed companion executable. The owner's
///    own account can start any program, so without this any owner-session process could
///    present itself as the companion; with it, an attacker must first be able to write
///    into an administrator-protected directory.
/// </summary>
public sealed class CompanionAdmissionPolicy
{
    private readonly string _authorizedSid;
    private readonly string? _expectedImagePath;
    private readonly int? _expectedSessionId;

    public CompanionAdmissionPolicy(
        string authorizedSid,
        string? expectedImagePath = null,
        int? expectedSessionId = null)
    {
        if (string.IsNullOrWhiteSpace(authorizedSid))
        {
            throw new ArgumentException("an authorized owner SID is required", nameof(authorizedSid));
        }

        _authorizedSid = authorizedSid.Trim();
        _expectedImagePath = string.IsNullOrWhiteSpace(expectedImagePath)
            ? null
            : Path.GetFullPath(expectedImagePath.Trim());
        _expectedSessionId = expectedSessionId;
    }

    public string AuthorizedSid => _authorizedSid;

    public string? ExpectedImagePath => _expectedImagePath;

    public int? ExpectedSessionId => _expectedSessionId;

    /// <summary>True when the policy pins a specific binary, i.e. is at full strength.</summary>
    public bool PinsImagePath => _expectedImagePath is not null;

    public IpcRefusal Evaluate(PipePeer? peer)
    {
        if (peer is null || string.IsNullOrWhiteSpace(peer.Sid))
        {
            return IpcRefusal.UnidentifiablePeer;
        }

        if (!string.Equals(peer.Sid, _authorizedSid, StringComparison.OrdinalIgnoreCase))
        {
            return IpcRefusal.SidMismatch;
        }

        // Checked before the pinned-session comparison so that "a service is impersonating
        // the companion" is never reported as a mere session mismatch.
        if (peer.SessionId == 0)
        {
            return IpcRefusal.ServiceSessionPeer;
        }

        if (_expectedSessionId is int expected && peer.SessionId != expected)
        {
            return IpcRefusal.SessionMismatch;
        }

        if (_expectedImagePath is not null)
        {
            if (string.IsNullOrWhiteSpace(peer.ImagePath))
            {
                // The binary is pinned and we cannot see which binary it is: refuse rather
                // than fall back to "the SID was right, close enough".
                return IpcRefusal.ImagePathMismatch;
            }

            string actual;
            try
            {
                actual = Path.GetFullPath(peer.ImagePath.Trim());
            }
            catch (Exception)
            {
                return IpcRefusal.ImagePathMismatch;
            }

            if (!string.Equals(actual, _expectedImagePath, StringComparison.OrdinalIgnoreCase))
            {
                return IpcRefusal.ImagePathMismatch;
            }
        }

        return IpcRefusal.None;
    }
}
