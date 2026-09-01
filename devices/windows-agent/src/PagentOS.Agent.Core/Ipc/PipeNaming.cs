using System.Security.Principal;

namespace PagentOS.Agent.Core.Ipc;

public static class PipeNaming
{
    /// <summary>
    /// The pipe both halves must agree on: named after the OWNER, never after whoever is
    /// asking.
    ///
    /// This matters the moment the two halves stop being the same account. A service running
    /// as LocalSystem that named its pipe after "the current user" would open
    /// pagentos-companion-S-1-5-18 while the companion looked for
    /// pagentos-companion-{ownerSid} — two correct-looking processes that never find each
    /// other, with nothing in either log saying why.
    /// </summary>
    public static string ForOwnerSid(string ownerSid)
    {
        if (string.IsNullOrWhiteSpace(ownerSid))
        {
            throw new ArgumentException("an owner SID is required", nameof(ownerSid));
        }

        return $"pagentos-companion-{ownerSid.Trim()}";
    }

    /// <summary>
    /// Default companion pipe name: pagentos-companion-{current user SID} on Windows so
    /// concurrent users never collide; a fixed dev name elsewhere. Correct for the companion,
    /// which runs as the owner. The service must use <see cref="ForOwnerSid"/> with its
    /// configured owner SID instead — see the remark above.
    /// </summary>
    public static string DefaultPipeName()
    {
        if (OperatingSystem.IsWindows())
        {
            var sid = WindowsIdentity.GetCurrent().User?.Value;
            if (!string.IsNullOrEmpty(sid))
            {
                return ForOwnerSid(sid);
            }
        }

        return "pagentos-companion-dev";
    }
}
