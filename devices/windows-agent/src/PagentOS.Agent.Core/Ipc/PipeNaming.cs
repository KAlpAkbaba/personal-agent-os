using System.Security.Principal;

namespace PagentOS.Agent.Core.Ipc;

public static class PipeNaming
{
    /// <summary>
    /// Default companion pipe name: pagentos-companion-{current user SID} on Windows so
    /// concurrent users never collide; a fixed dev name elsewhere. Overridable via config.
    /// </summary>
    public static string DefaultPipeName()
    {
        if (OperatingSystem.IsWindows())
        {
            var sid = WindowsIdentity.GetCurrent().User?.Value;
            if (!string.IsNullOrEmpty(sid))
            {
                return $"pagentos-companion-{sid}";
            }
        }

        return "pagentos-companion-dev";
    }
}
