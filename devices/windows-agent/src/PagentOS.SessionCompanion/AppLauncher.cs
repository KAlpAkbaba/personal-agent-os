using System.Diagnostics;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion;

/// <summary>
/// Executes desktop.open_application against a local allowlist (DEVICE_PROTOCOL.md §6).
/// Runs inside the interactive owner session, so a plain Process.Start launches the
/// application in that session. Anything not allowlisted fails with capability_missing.
/// </summary>
public sealed class AppLauncher
{
    private readonly Dictionary<string, string> _allowlist;

    public AppLauncher(IReadOnlyDictionary<string, string> allowlist)
    {
        _allowlist = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var pair in allowlist)
        {
            _allowlist[pair.Key] = Environment.ExpandEnvironmentVariables(pair.Value);
        }
    }

    public static Dictionary<string, string> DefaultAllowlist() => new(StringComparer.OrdinalIgnoreCase)
    {
        ["notepad"] = Environment.ExpandEnvironmentVariables(@"%WINDIR%\System32\notepad.exe"),
        ["calc"] = Environment.ExpandEnvironmentVariables(@"%WINDIR%\System32\calc.exe"),
        // M27 (docs/QUALIFICATION.md 25.12): opening an exported image in Paint was the one
        // step of the creative path that no device could take, because mspaint was on neither
        // allowlist. It is a System32 viewer with no arguments on this path - desktop.* passes
        // none - and the governed, argument-bearing route is app.launch's OnePathUnderRoots.
        ["mspaint"] = Environment.ExpandEnvironmentVariables(@"%WINDIR%\System32\mspaint.exe"),
    };

    /// <summary>Payload: {"application": "&lt;name&gt;", "args": [...]?}. Result: {"pid": int, "executable": path}.</summary>
    public JsonObject Launch(JsonObject payload)
    {
        var application = payload["application"]?.GetValue<string>();
        if (string.IsNullOrWhiteSpace(application))
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "payload.application is required", retryable: false);
        }

        if (!_allowlist.TryGetValue(application, out var executable))
        {
            throw new CapabilityException(
                ErrorClasses.CapabilityMissing,
                $"application '{application}' is not in the local allowlist",
                retryable: false);
        }

        if (!File.Exists(executable))
        {
            throw new CapabilityException(
                ErrorClasses.DependencyUnavailable,
                $"allowlisted executable not found: {executable}",
                retryable: false);
        }

        // NOTE (2026-09-09): this legacy route passes `args` through unfiltered, while
        // app.launch - the governed spelling of the same act - confines a path to the
        // authorised roots (ArgumentPolicy, ADR-0082 addendum 2). The asymmetry predates
        // mspaint and is a protocol-level change (DEVICE_PROTOCOL.md §6 declares `args`
        // here), so it is not made silently in passing; every caller in the repo today
        // sends no args, and the voice/operator plan uses app.launch.
        var startInfo = new ProcessStartInfo(executable) { UseShellExecute = false };
        if (payload["args"] is JsonArray args)
        {
            foreach (var arg in args)
            {
                if (arg is not null)
                {
                    startInfo.ArgumentList.Add(arg.GetValue<string>());
                }
            }
        }

        var process = Process.Start(startInfo)
                      ?? throw new CapabilityException(ErrorClasses.InternalBug, $"Process.Start returned null for {executable}", retryable: true);
        return new JsonObject
        {
            ["pid"] = process.Id,
            ["executable"] = executable,
        };
    }
}
