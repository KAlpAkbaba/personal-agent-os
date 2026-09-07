using System.Diagnostics;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// Every lab class joins this collection, and the collection does not run in parallel with
/// anything: there is one foreground window on the owner's desk and one pair of hands.
/// </summary>
[CollectionDefinition(Name, DisableParallelization = true)]
public sealed class OperatorLabCollection
{
    public const string Name = "operator-lab";
}

/// <summary>
/// A test that needs the owner's interactive desktop (M19_DIGITAL_OPERATOR_SPEC.md §6). On a
/// host without one it is SKIPPED with the runner condition named — never silently passed
/// and never failed for a reason that has nothing to do with the code.
/// </summary>
public sealed class LabFactAttribute : FactAttribute
{
    public LabFactAttribute()
    {
        var reason = OperatorLab.SkipReason();
        if (reason is not null)
        {
            Skip = reason;
        }
    }
}

/// <summary>
/// The Digital Operator test lab (§5): a real <see cref="OperatorCapabilities"/> over real
/// windows, with the discipline the spec demands — every window the lab opens is its own,
/// every process it started is ended on dispose (even when the test failed), and nothing is
/// typed into a window the guard did not just verify.
/// </summary>
public sealed class OperatorLab : IDisposable
{
    public const string TypedSample = "Merhaba Dünya ğüşöçıİ 123";

    /// <summary>The TEST allowlist: the owner's default plus <c>Start-Sleep *</c>, which exists here only so a long command can be cancelled and timed out.</summary>
    public static readonly IReadOnlyList<string> TestAllowlist = [.. TerminalRunner.DefaultAllowlist, "Start-Sleep *"];

    private readonly List<string> _windowsToClose = new();
    private readonly HashSet<int> _ownPids = new();

    public OperatorLab(IInputSynthesizer? input = null, bool enabled = true, IReadOnlyList<string>? roots = null, IReadOnlyDictionary<string, string>? applications = null)
    {
        Log = new ListLogger();
        Options = new OperatorOptions(enabled, TestAllowlist, roots ?? [Path.GetTempPath(), Environment.GetFolderPath(Environment.SpecialFolder.UserProfile)]);
        Operator = new OperatorCapabilities(Options, Log, input: input, applications: applications);
    }

    public OperatorCapabilities Operator { get; }

    public OperatorOptions Options { get; }

    public ListLogger Log { get; }

    /// <summary>Null when the lab can run; otherwise the runner condition that prevents it.</summary>
    public static string? SkipReason()
    {
        if (!OperatingSystem.IsWindows())
        {
            return "the Digital Operator lab needs Windows";
        }

        if (string.Equals(Environment.GetEnvironmentVariable("PAGENTOS_OPERATOR_LAB"), "0", StringComparison.Ordinal))
        {
            return "disabled by PAGENTOS_OPERATOR_LAB=0";
        }

        if (!Environment.UserInteractive)
        {
            return "no interactive desktop: Environment.UserInteractive=false (a service session or a headless CI runner)";
        }

        if (!OperatorEnvironment.HasVisibleDesktop())
        {
            return "no interactive desktop: the process window station is not visible (Session 0 or a headless CI runner)";
        }

        if (!OperatorEnvironment.HasForegroundWindow())
        {
            return "no foreground window: the session is locked or has no desktop (a CI runner without an interactive logon)";
        }

        if (!File.Exists(NotepadPath))
        {
            return $"notepad.exe not found at {NotepadPath}";
        }

        return null;
    }

    public static string NotepadPath => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32", "notepad.exe");

    /// <summary>The fixture folder under %TEMP% with one text file, created fresh.</summary>
    public static (string Directory, string File) Fixture()
    {
        var dir = Path.Combine(Path.GetTempPath(), "pagentos-operator-fixture");
        Directory.CreateDirectory(dir);
        var file = Path.Combine(dir, "fixture.txt");
        File.WriteAllText(file, "PagentOS operator fixture\n");
        return (dir, file);
    }

    public JsonObject Exec(string capability, JsonObject payload, double budgetSeconds = 15)
        => Operator.ExecuteAsync(capability, payload, TimeSpan.FromSeconds(budgetSeconds), CancellationToken.None).GetAwaiter().GetResult();

    public Task<JsonObject> ExecAsync(string capability, JsonObject payload, TimeSpan budget, CancellationToken cancellationToken)
        => Operator.ExecuteAsync(capability, payload, budget, cancellationToken);

    public CapabilityException ExpectFailure(string capability, JsonObject payload, double budgetSeconds = 15)
        => Assert.Throws<CapabilityException>(() => Exec(capability, payload, budgetSeconds));

    /// <summary>Launch Notepad through app.launch and assert a window appeared; the pid is the lab's to end.</summary>
    public (int Pid, string WindowId, JsonObject Window) LaunchNotepad()
    {
        var result = Exec(OperatorCapabilityNames.AppLaunch, new JsonObject { ["application"] = "notepad" });
        var pid = result["pid"]!.GetValue<int>();
        _ownPids.Add(pid);
        Assert.True(result["observed"]!["window_appeared"]!.GetValue<bool>(), "Notepad showed no window within 10 s");
        var windowId = result["window_id"]!.GetValue<string>();
        Assert.StartsWith("w-", windowId, StringComparison.Ordinal);
        var window = (JsonObject)result["observed"]!["window"]!;
        Assert.Equal("notepad.exe", window["image"]!.GetValue<string>());
        return (pid, windowId, window);
    }

    /// <summary>window.activate, then assert the re-observed window is the foreground one.</summary>
    public JsonObject Activate(string windowId)
    {
        var result = Exec(OperatorCapabilityNames.WindowActivate, new JsonObject { ["window_id"] = windowId });
        var window = (JsonObject)result["window"]!;
        Assert.True(window["foreground"]!.GetValue<bool>(), "window.activate returned without the window in front");
        return window;
    }

    /// <summary>Remember a window the lab opened but did not start the process for (an Explorer window), to close on dispose.</summary>
    public void TrackWindow(string windowId) => _windowsToClose.Add(windowId);

    public void TrackPid(int pid) => _ownPids.Add(pid);

    /// <summary>The first node in an inspected tree that satisfies the predicate, depth-first.</summary>
    public static JsonObject? FindNode(JsonObject? node, Func<JsonObject, bool> predicate)
    {
        if (node is null)
        {
            return null;
        }

        if (predicate(node))
        {
            return node;
        }

        if (node["children"] is JsonArray children)
        {
            foreach (var child in children)
            {
                var found = FindNode(child as JsonObject, predicate);
                if (found is not null)
                {
                    return found;
                }
            }
        }

        return null;
    }

    /// <summary>
    /// Notepad's text control, found the way a planner would: inspect the tree and take the
    /// Document/Edit element that carries a value. Classic Notepad exposes an <c>Edit</c>
    /// class control; the Store Notepad a <c>RichEditD2DPT</c> Document deeper in the tree —
    /// both are matched by control type, not by class or position.
    /// </summary>
    public (string AutomationId, string ControlType, string ClassName, string Value) ReadDocument(string windowId)
    {
        var tree = Exec(OperatorCapabilityNames.UiInspect, new JsonObject { ["window_id"] = windowId, ["depth"] = 5, ["max_nodes"] = 200 });
        var document = FindNode(
            (JsonObject)tree["root"]!,
            n => n["value"] is not null && n["control_type"]?.GetValue<string>() is "Document" or "Edit");
        Assert.NotNull(document);
        return (
            document!["automation_id"]!.GetValue<string>(),
            document["control_type"]!.GetValue<string>(),
            document["class_name"]!.GetValue<string>(),
            document["value"]!.GetValue<string>());
    }

    /// <summary>Read the document until it equals <paramref name="expected"/> or the wait elapses; returns the last value read.</summary>
    public string WaitForDocument(string windowId, string expected, TimeSpan? wait = null)
    {
        var deadline = DateTime.UtcNow + (wait ?? TimeSpan.FromSeconds(4));
        string value;
        do
        {
            value = ReadDocument(windowId).Value;
            if (string.Equals(value, expected, StringComparison.Ordinal))
            {
                return value;
            }

            Thread.Sleep(100);
        }
        while (DateTime.UtcNow < deadline);

        return value;
    }

    public static bool IsAlive(int pid)
    {
        try
        {
            using var process = Process.GetProcessById(pid);
            return !process.HasExited;
        }
        catch (ArgumentException)
        {
            return false;
        }
    }

    /// <summary>
    /// Client Windows (10/11) versus Windows Server: Explorer's UI Automation selection
    /// reporting differs (the GitHub runner is Server). Read from the registry's
    /// InstallationType, "Client" on a workstation.
    /// </summary>
    public static bool IsClientWindows
    {
        get
        {
            try
            {
                using var key = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Windows NT\CurrentVersion");
                var type = key?.GetValue("InstallationType") as string;
                return string.Equals(type, "Client", StringComparison.OrdinalIgnoreCase);
            }
            catch (Exception)
            {
                return false;
            }
        }
    }

    public static bool WaitForExit(int pid, TimeSpan wait)
    {
        var deadline = DateTime.UtcNow + wait;
        while (IsAlive(pid))
        {
            if (DateTime.UtcNow >= deadline)
            {
                return false;
            }

            Thread.Sleep(100);
        }

        return true;
    }

    public void Dispose()
    {
        // Windows the lab opened in someone else's process (Explorer): a close request, never a
        // kill — the process is the shell's.
        foreach (var windowId in _windowsToClose)
        {
            try
            {
                if (WindowRegistry.TryParseHandle(windowId, out var hwnd) && WindowRegistry.Exists(windowId))
                {
                    WindowActions.RequestClose(hwnd);
                }
            }
            catch (Exception)
            {
                // Best-effort teardown.
            }
        }

        // Processes the lab started: ours to end, unsaved Notepad text and all. Never a
        // process the lab did not start.
        foreach (var pid in _ownPids.Concat(Operator.StartedPids).Distinct())
        {
            try
            {
                using var process = Process.GetProcessById(pid);
                if (!process.HasExited && IsHarmless(process))
                {
                    process.Kill(entireProcessTree: true);
                    process.WaitForExit(3000);
                }
            }
            catch (Exception)
            {
                // Already gone.
            }
        }
    }

    /// <summary>The lab only ever kills the harmless apps it launched itself — and never a shell, a browser or PagentOS.</summary>
    private static bool IsHarmless(Process process)
    {
        var name = process.ProcessName;
        return name.Equals("notepad", StringComparison.OrdinalIgnoreCase)
               || name.Equals("calc", StringComparison.OrdinalIgnoreCase)
               || name.Equals("Calculator", StringComparison.OrdinalIgnoreCase);
    }
}
