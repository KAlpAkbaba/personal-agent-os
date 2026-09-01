namespace PagentOS.Agent.Core.Ipc;

/// <summary>
/// The companion's answer to "is the pipe I just opened really the Device Service?".
///
/// This direction matters as much as the other one. A named pipe name is not a secret and
/// the server side of a pipe is created by whoever asks first, so any process able to
/// create <c>\\.\pipe\pagentos-companion-*</c> before the service starts could otherwise
/// sit there and issue exec requests — a local process would be telling the owner's
/// desktop agent what to launch. The service defends against being pre-empted with
/// FILE_FLAG_FIRST_PIPE_INSTANCE; the companion defends against connecting to an impostor
/// by checking who <b>owns</b> the pipe object, which is the creating process's account and
/// is not something a normal user process can forge.
///
/// Trusted owners are the accounts allowed to host the service: LocalSystem and the local
/// Administrators group in production. In a developer run — service and companion both
/// started by the owner, no Windows Service installed — the owner's own SID is added
/// explicitly, and <see cref="RequiresElevatedOwner"/> reports that the check is running at
/// developer strength so the qualification record can say so rather than imply otherwise.
/// </summary>
public sealed class ServiceAdmissionPolicy
{
    /// <summary>NT AUTHORITY\SYSTEM.</summary>
    public const string LocalSystemSid = "S-1-5-18";

    /// <summary>BUILTIN\Administrators.</summary>
    public const string AdministratorsSid = "S-1-5-32-544";

    private readonly HashSet<string> _trusted;

    public ServiceAdmissionPolicy(IEnumerable<string> trustedOwnerSids)
    {
        _trusted = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var sid in trustedOwnerSids)
        {
            if (!string.IsNullOrWhiteSpace(sid))
            {
                _trusted.Add(sid.Trim());
            }
        }

        if (_trusted.Count == 0)
        {
            throw new ArgumentException("at least one trusted pipe-owner SID is required", nameof(trustedOwnerSids));
        }
    }

    /// <summary>Production posture: only accounts that can host a Windows Service.</summary>
    public static ServiceAdmissionPolicy ServiceMode()
        => new(new[] { LocalSystemSid, AdministratorsSid });

    /// <summary>
    /// Developer posture: also accept a pipe the owner created, because in a dev run the
    /// "service" is an ordinary process the owner started.
    /// </summary>
    public static ServiceAdmissionPolicy DeveloperMode(string ownerSid)
        => new(new[] { LocalSystemSid, AdministratorsSid, ownerSid });

    public IReadOnlyCollection<string> TrustedOwnerSids => _trusted;

    /// <summary>True when nothing below administrator level is trusted to host the pipe.</summary>
    public bool RequiresElevatedOwner
        => _trusted.All(sid =>
            string.Equals(sid, LocalSystemSid, StringComparison.OrdinalIgnoreCase)
            || string.Equals(sid, AdministratorsSid, StringComparison.OrdinalIgnoreCase));

    public IpcRefusal Evaluate(string? pipeOwnerSid)
    {
        if (string.IsNullOrWhiteSpace(pipeOwnerSid))
        {
            return IpcRefusal.UnidentifiablePeer;
        }

        return _trusted.Contains(pipeOwnerSid.Trim())
            ? IpcRefusal.None
            : IpcRefusal.UntrustedPipeOwner;
    }
}
