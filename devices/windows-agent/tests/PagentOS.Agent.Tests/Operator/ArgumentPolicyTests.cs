using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// ADR-0082 addendum 2, finding 3: each allowlisted application has an argument policy, and
/// an argument outside it is a <c>validation_error</c> naming the argument before a process
/// exists. <c>notepad</c> / <c>explorer</c>: at most one path inside the roots, resolved;
/// <c>calc</c> / <c>powershell</c> / an absolute executable: none; <c>chrome</c> / <c>msedge</c>:
/// http(s) URLs and <c>--new-window</c> only.
/// </summary>
[Collection(OperatorLabCollection.Name)]
public sealed class ArgumentPolicyTests : IDisposable
{
    private readonly JunctionFixture _fx = new();

    public void Dispose() => _fx.Dispose();

    [Fact]
    public void Each_allowlisted_name_has_the_documented_policy_and_anything_else_has_none()
    {
        Assert.Same(ArgumentPolicy.OnePathUnderRoots, ArgumentPolicy.For("notepad"));
        Assert.Same(ArgumentPolicy.OnePathUnderRoots, ArgumentPolicy.For("Explorer"));
        Assert.Same(ArgumentPolicy.None, ArgumentPolicy.For("calc"));
        Assert.Same(ArgumentPolicy.None, ArgumentPolicy.For("powershell"));
        Assert.Same(ArgumentPolicy.BrowserUrls, ArgumentPolicy.For("chrome"));
        Assert.Same(ArgumentPolicy.BrowserUrls, ArgumentPolicy.For("MSEDGE"));
        Assert.Same(ArgumentPolicy.None, ArgumentPolicy.For(@"C:\Program Files\Something\app.exe"));
        Assert.Same(ArgumentPolicy.None, ArgumentPolicy.For("regedit"));
    }

    [Fact]
    public void Notepad_and_explorer_take_one_resolved_path_inside_the_roots_and_nothing_else()
    {
        var roots = new AuthorisedRoots([_fx.Root]);
        var policy = ArgumentPolicy.OnePathUnderRoots;

        Assert.Empty(policy.Apply("notepad", [], roots));
        var accepted = policy.Apply("notepad", [_fx.GenuineFile], roots);
        Assert.Equal([Path.GetFullPath(_fx.GenuineFile)], accepted, StringComparer.OrdinalIgnoreCase);
        Assert.Equal([Path.GetFullPath(_fx.Root)], policy.Apply("explorer", [_fx.Root], roots), StringComparer.OrdinalIgnoreCase);

        Refused(() => policy.Apply("notepad", [_fx.GenuineFile, _fx.GenuineFile], roots), "at most one");
        Refused(() => policy.Apply("notepad", ["genuine.txt"], roots), "genuine.txt");
        Refused(() => policy.Apply("notepad", [_fx.SecretThroughJunction], roots), "secret.txt");
        Refused(() => policy.Apply("notepad", [_fx.SecretFile], roots), "secret.txt");
        Refused(() => policy.Apply("notepad", [Path.Combine(_fx.Root, "missing.txt")], roots), "missing.txt");
        Refused(() => policy.Apply("notepad", ["/A"], roots), "/A");
        Refused(() => policy.Apply("explorer", ["/select," + _fx.GenuineFile], roots), "/select,");
    }

    [Fact]
    public void Calc_powershell_and_an_absolute_executable_take_no_arguments()
    {
        var roots = new AuthorisedRoots([_fx.Root]);
        Assert.Empty(ArgumentPolicy.None.Apply("calc", [], roots));
        Refused(() => ArgumentPolicy.None.Apply("calc", ["1"], roots), "'1'");
        Refused(() => ArgumentPolicy.None.Apply("powershell", ["-Command", "hostname"], roots), "-Command");
        Refused(() => ArgumentPolicy.None.Apply("powershell", ["-EncodedCommand"], roots), "-EncodedCommand");
        Refused(() => ArgumentPolicy.None.Apply(@"C:\Windows\System32\notepad.exe", [_fx.GenuineFile], roots), "genuine.txt");
    }

    [Theory]
    [InlineData("--remote-debugging-port=9222")]
    [InlineData("--load-extension=C:\\evil")]
    [InlineData("--disable-web-security")]
    [InlineData("--user-data-dir=C:\\profile")]
    [InlineData("--proxy-server=127.0.0.1:8080")]
    [InlineData("--headless")]
    [InlineData("--new-window=x")]
    [InlineData("-incognito")]
    [InlineData("file:///C:/Windows/win.ini")]
    [InlineData("javascript:alert(1)")]
    [InlineData("chrome://settings")]
    [InlineData("example.com")]
    [InlineData("http://example.com --remote-debugging-port=9222")]
    [InlineData("http:///no-host")]
    [InlineData("")]
    public void A_browser_refuses_everything_but_web_urls_and_new_window(string argument)
    {
        var roots = new AuthorisedRoots([_fx.Root]);
        Refused(() => ArgumentPolicy.BrowserUrls.Apply("chrome", ["https://example.com", argument], roots), argument.Length == 0 ? "''" : argument[..Math.Min(argument.Length, 40)]);
        Refused(() => ArgumentPolicy.BrowserUrls.Apply("msedge", [argument], roots), argument.Length == 0 ? "''" : argument[..Math.Min(argument.Length, 40)]);
    }

    [Fact]
    public void A_browser_accepts_web_urls_and_new_window_as_given()
    {
        var roots = new AuthorisedRoots([_fx.Root]);
        var accepted = ArgumentPolicy.BrowserUrls.Apply("chrome", ["--new-window", "https://example.com/a?b=c#d", "http://localhost:8080/"], roots);
        Assert.Equal(["--new-window", "https://example.com/a?b=c#d", "http://localhost:8080/"], accepted);
        Assert.Equal(["--new-window"], ArgumentPolicy.BrowserUrls.Apply("msedge", ["--NEW-WINDOW"], roots));
        Assert.Empty(ArgumentPolicy.BrowserUrls.Apply("chrome", [], roots));
    }

    [Fact]
    public void Through_app_launch_a_refused_argument_is_a_validation_error_and_no_process_exists()
    {
        // Every name mapped to notepad.exe so the allowlist resolves on any machine; the
        // policy is keyed by NAME, and nothing below gets far enough to start anything.
        var notepad = OperatorLab.NotepadPath;
        var applications = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["notepad"] = notepad,
            ["calc"] = notepad,
            ["explorer"] = notepad,
            ["powershell"] = notepad,
            ["chrome"] = notepad,
            ["msedge"] = notepad,
        };
        using var lab = new OperatorLab(roots: [_fx.Root], applications: applications);

        Launch(lab, "calc", ["1"], "'1'");
        Launch(lab, "powershell", ["-Command", "hostname"], "-Command");
        Launch(lab, "chrome", ["--remote-debugging-port=9222"], "--remote-debugging-port");
        Launch(lab, "chrome", ["https://example.com", "--load-extension=C:\\x"], "--load-extension");
        Launch(lab, "msedge", ["--disable-web-security"], "--disable-web-security");
        Launch(lab, "msedge", ["file:///C:/Windows/win.ini"], "file:///");
        Launch(lab, "notepad", [_fx.SecretThroughJunction], "secret.txt");
        Launch(lab, "notepad", [_fx.GenuineFile, _fx.GenuineFile], "at most one");
        Launch(lab, "explorer", ["/select," + _fx.GenuineFile], "/select,");
        Launch(lab, notepad, [_fx.GenuineFile], "takes no arguments");

        // file.open applies the same policy to the one argument it gives the application.
        var fileOpen = lab.ExpectFailure(OperatorCapabilityNames.FileOpen, new JsonObject { ["path"] = _fx.GenuineFile, ["application"] = "calc" });
        Assert.Equal(ErrorClasses.ValidationError, fileOpen.ErrorClass);
        var browserOpen = lab.ExpectFailure(OperatorCapabilityNames.FileOpen, new JsonObject { ["path"] = _fx.GenuineFile, ["application"] = "chrome" });
        Assert.Equal(ErrorClasses.ValidationError, browserOpen.ErrorClass);

        Assert.Empty(lab.Operator.StartedPids);
    }

    [LabFact]
    public void App_launch_notepad_with_a_path_inside_the_roots_opens_that_file()
    {
        using var lab = new OperatorLab(roots: [_fx.Root]);
        var launched = lab.Exec(OperatorCapabilityNames.AppLaunch, new JsonObject { ["application"] = "notepad", ["args"] = new JsonArray(_fx.GenuineFile) });
        var pid = launched["pid"]!.GetValue<int>();
        lab.TrackPid(pid);
        Assert.True(launched["observed"]!["window_appeared"]!.GetValue<bool>());
        var windowId = launched["window_id"]!.GetValue<string>();

        var value = lab.WaitForDocument(windowId, "inside the root\r\n", TimeSpan.FromSeconds(5));
        Assert.StartsWith("inside the root", value, StringComparison.Ordinal);

        var closed = lab.Exec(OperatorCapabilityNames.AppClose, new JsonObject { ["pid"] = pid, ["force"] = true });
        Assert.True(closed["closed"]!.GetValue<bool>());
    }

    private static void Launch(OperatorLab lab, string application, string[] args, string expectedInMessage)
    {
        var ex = lab.ExpectFailure(OperatorCapabilityNames.AppLaunch, new JsonObject { ["application"] = application, ["args"] = new JsonArray([.. args.Select(a => (JsonNode)a)]) });
        Assert.Equal(ErrorClasses.ValidationError, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Contains(expectedInMessage, ex.Message, StringComparison.Ordinal);
        Assert.Contains("nothing was started", ex.Message, StringComparison.Ordinal);
    }

    private static void Refused(Func<IReadOnlyList<string>> apply, string expectedInMessage)
    {
        var ex = Assert.Throws<CapabilityException>(() => apply());
        Assert.Equal(ErrorClasses.ValidationError, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Contains(expectedInMessage, ex.Message, StringComparison.Ordinal);
    }
}
