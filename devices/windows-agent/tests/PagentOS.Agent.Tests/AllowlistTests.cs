using System.Diagnostics;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests;

public class AllowlistTests
{
    private static readonly string CmdPath =
        Environment.ExpandEnvironmentVariables(@"%WINDIR%\System32\cmd.exe");

    private static AppLauncher Launcher() => new(new Dictionary<string, string>
    {
        ["cmdtest"] = CmdPath,
    });

    [Fact]
    public void Unknown_application_fails_with_capability_missing()
    {
        var ex = Assert.Throws<CapabilityException>(
            () => Launcher().Launch(new JsonObject { ["application"] = "powershell" }));
        Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);
        Assert.False(ex.Retryable);
    }

    [Fact]
    public void Missing_application_field_fails_with_validation_error()
    {
        var ex = Assert.Throws<CapabilityException>(() => Launcher().Launch([]));
        Assert.Equal(ErrorClasses.ValidationError, ex.ErrorClass);
    }

    [Fact]
    public void Allowlisted_application_launches_and_returns_pid_and_executable()
    {
        var payload = new JsonObject
        {
            ["application"] = "CMDTEST", // case-insensitive
            ["args"] = new JsonArray("/c", "exit"),
        };
        var result = Launcher().Launch(payload);

        var pid = result["pid"]!.GetValue<int>();
        Assert.True(pid > 0);
        Assert.Equal(CmdPath, result["executable"]!.GetValue<string>());

        // Let the short-lived process exit so nothing leaks from the test.
        try
        {
            using var process = Process.GetProcessById(pid);
            process.WaitForExit(5000);
        }
        catch (ArgumentException)
        {
            // Already exited.
        }
    }

    [Fact]
    public void Default_allowlist_contains_notepad_calc_and_paint_full_paths()
    {
        var allowlist = AppLauncher.DefaultAllowlist();
        Assert.Equal(3, allowlist.Count);
        Assert.EndsWith(@"System32\notepad.exe", allowlist["notepad"], StringComparison.OrdinalIgnoreCase);
        Assert.EndsWith(@"System32\calc.exe", allowlist["calc"], StringComparison.OrdinalIgnoreCase);
        // M27 (docs/QUALIFICATION.md 25.12): opening an exported image in Paint was the one
        // step of the creative path no device could take - mspaint was on neither allowlist,
        // so desktop.open_application answered capability_missing and the whole M27 device
        // half stayed PROVEN_PROXY behind owner item 28.
        Assert.EndsWith(@"System32\mspaint.exe", allowlist["mspaint"], StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void Missing_executable_fails_with_dependency_unavailable()
    {
        var launcher = new AppLauncher(new Dictionary<string, string>
        {
            ["ghost"] = @"C:\does\not\exist\ghost.exe",
        });
        var ex = Assert.Throws<CapabilityException>(
            () => launcher.Launch(new JsonObject { ["application"] = "ghost" }));
        Assert.Equal(ErrorClasses.DependencyUnavailable, ex.ErrorClass);
    }
}
