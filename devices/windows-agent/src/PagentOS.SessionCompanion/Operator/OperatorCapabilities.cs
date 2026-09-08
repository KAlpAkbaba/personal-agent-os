using System.Diagnostics;
using System.Globalization;
using System.Runtime.Versioning;
using System.Text.Json.Nodes;
using System.Windows.Automation;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion.Operator;

/// <summary>
/// The Digital Operator's dispatch (M19_DIGITAL_OPERATOR_SPEC.md §3): one method per
/// capability in <see cref="OperatorCapabilityNames"/>, each taking a validated payload and
/// answering with what the companion OBSERVED after acting. Three rules hold for every method:
/// <list type="number">
/// <item>the payload is validated before anything touches a window — required fields, types,
/// bounds, the fixed key vocabulary, and "secrets are never typed";</item>
/// <item>keyboard and pointer input pass the <see cref="FocusGuard"/> immediately before the
/// event is sent, and a mismatch is a refusal, never a retry into whatever is in front;</item>
/// <item>the result's <c>observed</c> block is a fresh read taken after the action — a window's
/// state, the value read back, the process still alive — and a window action whose
/// re-observed state is not the requested one fails with <c>postcondition_failed</c>.</item>
/// </list>
/// Actions run one at a time (one pair of hands on the desktop) under the budget the pipe
/// request carries; cancellation and timeout are typed answers, not hangs. Every result passes
/// the same forbidden-key scan the browser results pass before it leaves the companion.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class OperatorCapabilities
{
    public const string AuditRequestEvent = "operator_request";

    /// <summary>§2: how long <c>app.launch</c> waits for the new process to show a window.</summary>
    public static readonly TimeSpan LaunchWindowWait = TimeSpan.FromSeconds(10);

    /// <summary>§2: how long a window/app is given after WM_CLOSE before "still there" is reported.</summary>
    public static readonly TimeSpan CloseWait = WindowActions.CloseWait;

    /// <summary>How long a state change (maximise, minimise, restore) is given to be observable.</summary>
    public static readonly TimeSpan StateWait = TimeSpan.FromSeconds(2);

    /// <summary>§2: a move/resize is accepted when the re-observed rect is within this many pixels.</summary>
    public const int RectTolerance = 8;

    public const int MaxArgs = 16;
    public const int MaxArgChars = 1024;

    /// <summary>Extensions <c>file.open</c> never opens (they run) — and, since M22, that <c>file.fetch</c> never writes.</summary>
    public static readonly IReadOnlySet<string> ExecutableExtensions = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
    {
        ".exe", ".bat", ".cmd", ".com", ".scr", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".msi", ".msp", ".lnk", ".hta", ".reg", ".cpl", ".inf",
    };

    private readonly OperatorOptions _options;
    private readonly ILogger _logger;
    private readonly AuditLog? _audit;
    private readonly IInputSynthesizer _input;
    private readonly IMonitorInventory _monitors;
    private readonly SemaphoreSlim _gate = new(1, 1);
    private readonly Dictionary<int, Process> _started = new();
    private readonly Dictionary<string, string> _applications;

    public OperatorCapabilities(
        OperatorOptions options,
        ILogger logger,
        AuditLog? audit = null,
        IInputSynthesizer? input = null,
        TerminalRunner? terminal = null,
        IMonitorInventory? monitors = null,
        IReadOnlyDictionary<string, string>? applications = null)
    {
        _options = options;
        _logger = logger;
        _audit = audit;
        _input = input ?? new Win32InputSynthesizer();
        _monitors = monitors ?? new Win32MonitorInventory();
        Registry = new WindowRegistry();
        Guard = new FocusGuard(Registry);
        Inspector = new UiAutomationInspector();
        Terminal = terminal ?? new TerminalRunner(options.TerminalAllowlist, options.AuthorisedRoots, logger);
        _applications = new Dictionary<string, string>(applications ?? DefaultApplications(), StringComparer.OrdinalIgnoreCase);
    }

    public bool Enabled => _options.Enabled;

    public WindowRegistry Registry { get; }

    public FocusGuard Guard { get; }

    public UiAutomationInspector Inspector { get; }

    public TerminalRunner Terminal { get; }

    public IReadOnlyList<string> AuthorisedRoots => _options.AuthorisedRoots;

    /// <summary>Processes this operator started (app.launch, terminal.open, file.open with an application), for terminal.status and for a test's cleanup.</summary>
    public IReadOnlyList<int> StartedPids
    {
        get
        {
            lock (_started)
            {
                return [.. _started.Keys];
            }
        }
    }

    /// <summary>§2: the application allowlist for <c>app.launch</c> — by name, or an absolute path under Program Files / Windows.</summary>
    public static Dictionary<string, string> DefaultApplications()
    {
        var windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        var programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        var programFilesX86 = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);
        var chrome = Path.Combine(programFiles, "Google", "Chrome", "Application", "chrome.exe");
        if (!File.Exists(chrome))
        {
            chrome = Path.Combine(programFilesX86, "Google", "Chrome", "Application", "chrome.exe");
        }

        var edge = Path.Combine(programFilesX86, "Microsoft", "Edge", "Application", "msedge.exe");
        if (!File.Exists(edge))
        {
            edge = Path.Combine(programFiles, "Microsoft", "Edge", "Application", "msedge.exe");
        }

        return new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["notepad"] = Path.Combine(windows, "System32", "notepad.exe"),
            ["calc"] = Path.Combine(windows, "System32", "calc.exe"),
            ["explorer"] = Path.Combine(windows, "explorer.exe"),
            ["powershell"] = TerminalRunner.DefaultPowerShellPath(),
            ["chrome"] = chrome,
            ["msedge"] = edge,
        };
    }

    // ================================================================== entry point

    public async Task<JsonObject> ExecuteAsync(string capability, JsonObject payload, TimeSpan budget, CancellationToken cancellationToken)
    {
        if (!OperatorCapabilityNames.IsMember(capability))
        {
            throw new CapabilityException(ErrorClasses.CapabilityMissing, $"'{capability}' is not a Digital Operator capability", retryable: false);
        }

        var stopwatch = Stopwatch.StartNew();
        await _gate.WaitAsync(cancellationToken).ConfigureAwait(false);
        using var budgetCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        if (budget > TimeSpan.Zero)
        {
            budgetCts.CancelAfter(budget);
        }

        try
        {
            var result = await Task.Run(() => Dispatch(capability, payload, budgetCts.Token), CancellationToken.None).ConfigureAwait(false);
            var forbidden = BrowserWorkerHost.FindForbiddenKey(result, path: "result");
            if (forbidden is not null)
            {
                throw new CapabilityException(
                    ErrorClasses.SecurityScopeError,
                    $"operator result for {capability} carries a forbidden key ({forbidden}); it does not leave the companion",
                    retryable: false);
            }

            Record(capability, "ok", stopwatch.ElapsedMilliseconds, null);
            return result;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            Record(capability, ErrorClasses.Cancelled, stopwatch.ElapsedMilliseconds, true);
            throw new CapabilityException(ErrorClasses.Cancelled, $"{capability} was cancelled after {stopwatch.ElapsedMilliseconds} ms", retryable: true);
        }
        catch (OperationCanceledException)
        {
            Record(capability, ErrorClasses.Timeout, stopwatch.ElapsedMilliseconds, true);
            throw new CapabilityException(ErrorClasses.Timeout, $"{capability} exceeded its {budget.TotalSeconds:F1} s budget", retryable: true);
        }
        catch (CapabilityException ex) when (ex.ErrorClass == ErrorClasses.Cancelled && !cancellationToken.IsCancellationRequested && budgetCts.IsCancellationRequested)
        {
            // A helper (the terminal runner) saw its token cancelled and said so; the token it
            // saw was the BUDGET's, not the caller's, and the truthful answer is timeout.
            Record(capability, ErrorClasses.Timeout, stopwatch.ElapsedMilliseconds, true);
            throw new CapabilityException(ErrorClasses.Timeout, $"{capability} exceeded its {budget.TotalSeconds:F1} s budget ({ex.Message})", retryable: true);
        }
        catch (CapabilityException ex)
        {
            Record(capability, ex.ErrorClass, stopwatch.ElapsedMilliseconds, ex.Retryable);
            throw;
        }
        catch (ElementNotAvailableException)
        {
            Record(capability, ErrorClasses.UiStateChanged, stopwatch.ElapsedMilliseconds, true);
            throw new CapabilityException(ErrorClasses.UiStateChanged, "the UI element or window went away while the action ran", retryable: true);
        }
        finally
        {
            _gate.Release();
        }
    }

    private JsonObject Dispatch(string capability, JsonObject payload, CancellationToken cancellationToken)
        => capability switch
        {
            OperatorCapabilityNames.AppLaunch => AppLaunch(payload, cancellationToken),
            OperatorCapabilityNames.AppList => AppList(),
            OperatorCapabilityNames.AppActivate => AppActivate(payload),
            OperatorCapabilityNames.AppClose => AppClose(payload, cancellationToken),
            OperatorCapabilityNames.WindowList => WindowList(payload),
            OperatorCapabilityNames.WindowCurrent => WindowCurrent(),
            OperatorCapabilityNames.WindowActivate => WindowActivate(payload),
            OperatorCapabilityNames.WindowMinimize => WindowShow(payload, OperatorNative.SwMinimize, WindowInfo.StateMinimized, cancellationToken),
            OperatorCapabilityNames.WindowMaximize => WindowShow(payload, OperatorNative.SwShowMaximized, WindowInfo.StateMaximized, cancellationToken),
            OperatorCapabilityNames.WindowRestore => WindowShow(payload, OperatorNative.SwRestore, WindowInfo.StateNormal, cancellationToken),
            OperatorCapabilityNames.WindowMove => WindowMove(payload, cancellationToken),
            OperatorCapabilityNames.WindowResize => WindowResize(payload, cancellationToken),
            OperatorCapabilityNames.WindowClose => WindowClose(payload, cancellationToken),
            OperatorCapabilityNames.KeyboardType => KeyboardType(payload),
            OperatorCapabilityNames.KeyboardKey => KeyboardKey(payload),
            OperatorCapabilityNames.KeyboardShortcut => KeyboardShortcut(payload),
            OperatorCapabilityNames.PointerMove => Pointer(payload, PointerAction.Move),
            OperatorCapabilityNames.PointerClick => Pointer(payload, PointerAction.Click),
            OperatorCapabilityNames.PointerDoubleClick => Pointer(payload, PointerAction.DoubleClick),
            OperatorCapabilityNames.PointerRightClick => Pointer(payload, PointerAction.RightClick),
            OperatorCapabilityNames.PointerScroll => Pointer(payload, PointerAction.Scroll),
            OperatorCapabilityNames.UiInspect => UiInspect(payload),
            OperatorCapabilityNames.UiInvoke => UiInvoke(payload),
            OperatorCapabilityNames.UiSetValue => UiSetValue(payload),
            OperatorCapabilityNames.UiSelect => UiSelect(payload),
            OperatorCapabilityNames.ScreenCapture => ScreenCaptureAction(payload),
            OperatorCapabilityNames.ScreenInspect => ScreenInspect(),
            OperatorCapabilityNames.FileOpen => FileOpen(payload, cancellationToken),
            OperatorCapabilityNames.FileReveal => FileReveal(payload, cancellationToken),
            OperatorCapabilityNames.TerminalOpen => TerminalOpen(payload, cancellationToken),
            OperatorCapabilityNames.TerminalExecute => TerminalExecute(payload, cancellationToken),
            OperatorCapabilityNames.TerminalStatus => TerminalStatus(payload),
            _ => throw new CapabilityException(ErrorClasses.CapabilityMissing, $"'{capability}' has no dispatch entry", retryable: false),
        };

    // ================================================================== app.*

    private JsonObject AppLaunch(JsonObject payload, CancellationToken cancellationToken)
    {
        var application = RequireString(payload, "application", 260);
        var executable = ResolveApplication(application);
        var args = ArgumentPolicy.For(application).Apply(application, ReadArgs(payload), Terminal.Roots);
        var process = StartTracked(executable, args, visible: true);
        var window = WaitForWindow(process.Id, LaunchWindowWait, cancellationToken);
        _logger.LogInformation("app.launch {Application} pid={Pid} window={Window}", application, process.Id, window?.WindowId ?? "-");
        return new JsonObject
        {
            ["pid"] = process.Id,
            ["window_id"] = window?.WindowId,
            ["title"] = window?.Title,
            ["executable"] = executable,
            ["observed"] = new JsonObject
            {
                ["window_appeared"] = window is not null,
                ["window"] = window?.ToJson(),
                ["process_alive"] = IsAlive(process.Id),
            },
        };
    }

    private JsonObject AppList()
    {
        var windows = Registry.Enumerate();
        var applications = new JsonArray();
        foreach (var group in windows.GroupBy(w => w.Pid).OrderBy(g => g.Key))
        {
            var first = group.First();
            applications.Add(new JsonObject
            {
                ["pid"] = group.Key,
                ["image"] = first.Image,
                ["name"] = Path.GetFileNameWithoutExtension(first.Image),
                ["window_count"] = group.Count(),
                ["windows"] = new JsonArray([.. group.Select(w => (JsonNode)w.WindowId)]),
            });
        }

        return new JsonObject
        {
            ["applications"] = applications,
            ["observed"] = new JsonObject { ["window_count"] = windows.Count, ["foreground"] = Registry.Foreground()?.ToJson() },
        };
    }

    private JsonObject AppActivate(JsonObject payload)
    {
        var target = ResolveTarget(payload);
        var activated = WindowActions.Activate(target.Handle);
        var observed = Registry.Read(target.Handle);
        if (!activated || observed is null || !observed.Foreground)
        {
            throw new CapabilityException(
                ErrorClasses.PostconditionFailed,
                $"{target.WindowId} did not become the foreground window (foreground is {FocusGuard.Describe(Registry.Foreground())})",
                retryable: true);
        }

        return new JsonObject
        {
            ["window_id"] = observed.WindowId,
            ["pid"] = observed.Pid,
            ["foreground"] = true,
            ["observed"] = new JsonObject { ["window"] = observed.ToJson() },
        };
    }

    private JsonObject AppClose(JsonObject payload, CancellationToken cancellationToken)
    {
        var force = OptionalBool(payload, "force") ?? false;
        int pid;
        if (payload["window_id"] is not null)
        {
            pid = Registry.Resolve(payload["window_id"]!.GetValue<string>()).Pid;
        }
        else
        {
            pid = RequireInt(payload, "pid", 1, int.MaxValue);
        }

        var windows = Registry.Enumerate(pid);
        if (windows.Count == 0 && !IsAlive(pid))
        {
            throw new CapabilityException(ErrorClasses.UiTargetNotFound, $"no process {pid} with a window", retryable: false);
        }

        var before = new HashSet<long>(windows.Select(w => w.Handle.ToInt64()));
        foreach (var window in windows.Where(w => !w.Owned))
        {
            WindowActions.RequestClose(window.Handle);
        }

        JsonObject? modal = null;
        var wait = force ? TimeSpan.FromMilliseconds(1500) : CloseWait;
        WindowActions.WaitUntil(
            () =>
            {
                if (!IsAlive(pid) || Registry.Enumerate(pid).Count == 0)
                {
                    return true;
                }

                modal = DetectModal(pid, before);
                return modal is not null;
            },
            wait,
            cancellationToken,
            stepMs: 100);

        var method = "wm_close";
        var remaining = Registry.Enumerate(pid);
        var closed = !IsAlive(pid) || remaining.Count == 0;
        if (!closed && force)
        {
            Terminate(pid);
            WindowActions.WaitUntil(() => !IsAlive(pid), TimeSpan.FromSeconds(3), cancellationToken, stepMs: 100);
            method = "terminated";
            remaining = Registry.Enumerate(pid);
            closed = !IsAlive(pid);
            modal = null;
        }

        var result = new JsonObject
        {
            ["pid"] = pid,
            ["closed"] = closed,
            ["method"] = method,
            ["observed"] = new JsonObject
            {
                ["process_alive"] = IsAlive(pid),
                ["windows"] = new JsonArray([.. remaining.Select(w => (JsonNode)w.ToJson())]),
            },
        };
        if (modal is not null)
        {
            result["modal"] = modal;
        }

        return result;
    }

    // ================================================================== window.*

    private JsonObject WindowList(JsonObject payload)
    {
        var pid = OptionalInt(payload, "pid", 1, int.MaxValue);
        var windows = Registry.Enumerate(pid);
        return new JsonObject
        {
            ["windows"] = new JsonArray([.. windows.Select(w => (JsonNode)w.ToJson())]),
            ["observed"] = new JsonObject { ["count"] = windows.Count, ["foreground"] = Registry.Foreground()?.ToJson() },
        };
    }

    private JsonObject WindowCurrent()
    {
        var window = Registry.Foreground();
        return new JsonObject
        {
            ["window"] = window?.ToJson(),
            ["observed"] = new JsonObject { ["window"] = window?.ToJson() },
        };
    }

    private JsonObject WindowActivate(JsonObject payload)
    {
        var target = Registry.Resolve(payload["window_id"]?.GetValue<string>());
        var activated = WindowActions.Activate(target.Handle);
        var observed = Registry.Read(target.Handle);
        if (!activated || observed is null || !observed.Foreground)
        {
            throw new CapabilityException(
                ErrorClasses.PostconditionFailed,
                $"{target.WindowId} did not become the foreground window (foreground is {FocusGuard.Describe(Registry.Foreground())})",
                retryable: true);
        }

        return WindowResult(observed);
    }

    private JsonObject WindowShow(JsonObject payload, int command, string expectedState, CancellationToken cancellationToken)
    {
        var target = Registry.Resolve(payload["window_id"]?.GetValue<string>());
        WindowActions.Show(target.Handle, command);
        WindowInfo? observed = null;
        WindowActions.WaitUntil(
            () =>
            {
                observed = Registry.Read(target.Handle);
                return observed is not null && observed.State == expectedState;
            },
            StateWait,
            cancellationToken);
        if (observed is null || observed.State != expectedState)
        {
            throw new CapabilityException(
                ErrorClasses.PostconditionFailed,
                $"{target.WindowId} was asked to be {expectedState} and is observed {observed?.State ?? "gone"}",
                retryable: true);
        }

        return WindowResult(observed);
    }

    private JsonObject WindowMove(JsonObject payload, CancellationToken cancellationToken)
    {
        var target = Registry.Resolve(payload["window_id"]?.GetValue<string>());
        var (vx, vy, vw, vh) = VirtualScreen();
        var x = RequireInt(payload, "x", vx - 4096, vx + vw + 4096);
        var y = RequireInt(payload, "y", vy - 4096, vy + vh + 4096);
        if (!WindowActions.Move(target.Handle, x, y))
        {
            throw new CapabilityException(ErrorClasses.UiStateChanged, $"{target.WindowId} refused to move", retryable: true);
        }

        WindowInfo? observed = null;
        WindowActions.WaitUntil(
            () =>
            {
                observed = Registry.Read(target.Handle);
                return observed is not null && Within(observed.Rect.X, x) && Within(observed.Rect.Y, y);
            },
            StateWait,
            cancellationToken);
        if (observed is null || !Within(observed.Rect.X, x) || !Within(observed.Rect.Y, y))
        {
            throw new CapabilityException(
                ErrorClasses.PostconditionFailed,
                $"{target.WindowId} was asked to move to ({x},{y}) and is observed at ({observed?.Rect.X},{observed?.Rect.Y}) state={observed?.State}",
                retryable: true);
        }

        return WindowResult(observed);
    }

    private JsonObject WindowResize(JsonObject payload, CancellationToken cancellationToken)
    {
        var target = Registry.Resolve(payload["window_id"]?.GetValue<string>());
        var (_, _, vw, vh) = VirtualScreen();
        var width = RequireInt(payload, "width", 50, Math.Max(vw, 50));
        var height = RequireInt(payload, "height", 50, Math.Max(vh, 50));
        if (!WindowActions.Resize(target.Handle, width, height))
        {
            throw new CapabilityException(ErrorClasses.UiStateChanged, $"{target.WindowId} refused to resize", retryable: true);
        }

        WindowInfo? observed = null;
        WindowActions.WaitUntil(
            () =>
            {
                observed = Registry.Read(target.Handle);
                return observed is not null && Within(observed.Rect.Width, width) && Within(observed.Rect.Height, height);
            },
            StateWait,
            cancellationToken);
        if (observed is null || !Within(observed.Rect.Width, width) || !Within(observed.Rect.Height, height))
        {
            throw new CapabilityException(
                ErrorClasses.PostconditionFailed,
                $"{target.WindowId} was asked to be {width}x{height} and is observed {observed?.Rect.Width}x{observed?.Rect.Height} state={observed?.State}",
                retryable: true);
        }

        return WindowResult(observed);
    }

    private JsonObject WindowClose(JsonObject payload, CancellationToken cancellationToken)
    {
        var target = Registry.Resolve(payload["window_id"]?.GetValue<string>());
        var before = new HashSet<long>(Registry.Enumerate(target.Pid).Select(w => w.Handle.ToInt64()));
        WindowActions.RequestClose(target.Handle);
        JsonObject? modal = null;
        var gone = WindowActions.WaitForGone(
            target.Handle,
            CloseWait,
            cancellationToken,
            stopEarly: () =>
            {
                modal = DetectModal(target.Pid, before);
                return modal is not null;
            });
        var observed = gone ? null : Registry.Read(target.Handle);
        var result = new JsonObject
        {
            ["window_id"] = target.WindowId,
            ["closed"] = gone,
            ["observed"] = new JsonObject { ["window"] = observed?.ToJson(), ["process_alive"] = IsAlive(target.Pid) },
        };
        if (modal is not null)
        {
            result["modal"] = modal;
        }

        return result;
    }

    // ================================================================== keyboard.*

    private JsonObject KeyboardType(JsonObject payload)
    {
        var text = RequireText(payload, "text");
        RefuseSecret(payload);
        var target = Registry.Resolve(payload["window_id"]?.GetValue<string>());
        GuardedInput(target, stillTargeted => _input.TypeText(text, stillTargeted));
        return new JsonObject
        {
            ["typed_chars"] = text.Length,
            ["window_id"] = target.WindowId,
            ["observed"] = new JsonObject { ["window"] = Registry.Foreground()?.ToJson() },
        };
    }

    private JsonObject KeyboardKey(JsonObject payload)
    {
        var key = RequireString(payload, "key", 16).ToLowerInvariant();
        KeyMap.ValidateKey(key);
        var target = Registry.Resolve(payload["window_id"]?.GetValue<string>());
        GuardedInput(target, stillTargeted => _input.PressKey(key, stillTargeted));
        return new JsonObject
        {
            ["key"] = key,
            ["window_id"] = target.WindowId,
            ["observed"] = new JsonObject { ["window"] = Registry.Foreground()?.ToJson() },
        };
    }

    private JsonObject KeyboardShortcut(JsonObject payload)
    {
        if (payload["keys"] is not JsonArray array)
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "payload.keys must be an array of key names", retryable: false);
        }

        var keys = new List<string>();
        foreach (var node in array)
        {
            if (node is null || node.GetValueKind() != System.Text.Json.JsonValueKind.String)
            {
                throw new CapabilityException(ErrorClasses.ValidationError, "payload.keys must contain strings", retryable: false);
            }

            keys.Add(node.GetValue<string>().ToLowerInvariant());
        }

        KeyMap.ValidateShortcut(keys);
        var target = Registry.Resolve(payload["window_id"]?.GetValue<string>());
        GuardedInput(target, stillTargeted => _input.Shortcut(keys, stillTargeted));
        return new JsonObject
        {
            ["keys"] = new JsonArray([.. keys.Select(k => (JsonNode)k)]),
            ["window_id"] = target.WindowId,
            ["observed"] = new JsonObject { ["window"] = Registry.Foreground()?.ToJson() },
        };
    }

    // ================================================================== pointer.*

    private enum PointerAction
    {
        Move,
        Click,
        DoubleClick,
        RightClick,
        Scroll,
    }

    private JsonObject Pointer(JsonObject payload, PointerAction action)
    {
        var space = (OptionalString(payload, "space", 16) ?? "window").ToLowerInvariant();
        if (space is not ("window" or "screen"))
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "payload.space must be \"window\" or \"screen\"", retryable: false);
        }

        var delta = action == PointerAction.Scroll ? RequireInt(payload, "delta", -50, 50) : 0;
        if (action == PointerAction.Scroll && delta == 0)
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "payload.delta must be a non-zero number of wheel notches", retryable: false);
        }

        var target = Registry.Resolve(payload["window_id"]?.GetValue<string>());
        var (vx, vy, vw, vh) = VirtualScreen();
        int x, y, screenX, screenY;
        if (space == "window")
        {
            x = RequireInt(payload, "x", 0, Math.Max(0, target.Rect.Width - 1));
            y = RequireInt(payload, "y", 0, Math.Max(0, target.Rect.Height - 1));
            screenX = target.Rect.X + x;
            screenY = target.Rect.Y + y;
        }
        else
        {
            x = RequireInt(payload, "x", vx, vx + vw - 1);
            y = RequireInt(payload, "y", vy, vy + vh - 1);
            screenX = x;
            screenY = y;
        }

        Guarded(target);
        switch (action)
        {
            case PointerAction.Move:
                _input.MoveTo(screenX, screenY);
                break;
            case PointerAction.Click:
                _input.Click(screenX, screenY, PointerButton.Left, 1);
                break;
            case PointerAction.DoubleClick:
                _input.Click(screenX, screenY, PointerButton.Left, 2);
                break;
            case PointerAction.RightClick:
                _input.Click(screenX, screenY, PointerButton.Right, 1);
                break;
            case PointerAction.Scroll:
                _input.Scroll(screenX, screenY, delta);
                break;
        }

        OperatorNative.GetCursorPos(out var cursor);
        var result = new JsonObject
        {
            ["x"] = x,
            ["y"] = y,
            ["space"] = space,
            ["screen_x"] = screenX,
            ["screen_y"] = screenY,
            ["window_id"] = target.WindowId,
            ["observed"] = new JsonObject
            {
                ["cursor"] = new JsonObject { ["x"] = cursor.X, ["y"] = cursor.Y },
                ["window"] = Registry.Foreground()?.ToJson(),
            },
        };
        if (action == PointerAction.Scroll)
        {
            result["delta"] = delta;
        }

        return result;
    }

    // ================================================================== ui.*

    private JsonObject UiInspect(JsonObject payload)
    {
        var target = Registry.Resolve(payload["window_id"]?.GetValue<string>());
        var depth = OptionalInt(payload, "depth", 1, UiAutomationInspector.MaxDepth) ?? UiAutomationInspector.DefaultDepth;
        var maxNodes = OptionalInt(payload, "max_nodes", 1, UiAutomationInspector.MaxNodes) ?? UiAutomationInspector.DefaultMaxNodes;
        var query = ReadQuery(payload);
        var root = Inspector.RootForWindow(target.Handle);
        var tree = Inspector.Inspect(root, depth, maxNodes, query);
        tree["window_id"] = target.WindowId;
        tree["observed"] = new JsonObject { ["window"] = Registry.Read(target.Handle)?.ToJson() };
        return tree;
    }

    private JsonObject UiInvoke(JsonObject payload)
    {
        var target = Registry.Resolve(payload["window_id"]?.GetValue<string>());
        var query = ReadQuery(payload);
        if (!query.NamesAnElement)
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "ui.invoke needs automation_id, name or name_prefix", retryable: false);
        }

        var element = Inspector.Find(Inspector.RootForWindow(target.Handle), query);
        var description = Inspector.Describe(element);
        var method = Inspector.Invoke(element);
        Thread.Sleep(100);
        JsonObject? after = null;
        try
        {
            after = Inspector.Describe(element);
        }
        catch (ElementNotAvailableException)
        {
            // The invoke removed the element (a dialog button that closed the dialog).
        }

        return new JsonObject
        {
            ["invoked"] = true,
            ["method"] = method,
            ["element"] = description,
            ["window_id"] = target.WindowId,
            ["observed"] = new JsonObject
            {
                ["element"] = after,
                ["element_present"] = after is not null,
                ["window"] = Registry.Read(target.Handle)?.ToJson(),
            },
        };
    }

    private JsonObject UiSetValue(JsonObject payload)
    {
        var value = RequireText(payload, "value");
        RefuseSecret(payload);
        var target = Registry.Resolve(payload["window_id"]?.GetValue<string>());
        var query = ReadQuery(payload);
        if (!query.NamesAnElement && query.ControlType is null)
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "ui.set_value needs automation_id, name, name_prefix or control_type", retryable: false);
        }

        var element = Inspector.Find(Inspector.RootForWindow(target.Handle), query);
        var observedValue = Inspector.SetValue(element, value);
        return new JsonObject
        {
            ["element"] = Inspector.Describe(element),
            ["observed_value"] = observedValue,
            ["window_id"] = target.WindowId,
            ["observed"] = new JsonObject { ["value"] = observedValue, ["window"] = Registry.Read(target.Handle)?.ToJson() },
        };
    }

    private JsonObject UiSelect(JsonObject payload)
    {
        var item = RequireString(payload, "item", 512);
        var target = Registry.Resolve(payload["window_id"]?.GetValue<string>());
        var query = ReadQuery(payload);
        if (!query.NamesAnElement && query.ControlType is null)
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "ui.select needs the container's automation_id, name, name_prefix or control_type", retryable: false);
        }

        var container = Inspector.Find(Inspector.RootForWindow(target.Handle), query);
        var selected = Inspector.Select(container, item);
        return new JsonObject
        {
            ["element"] = Inspector.Describe(container),
            ["selected"] = item,
            ["window_id"] = target.WindowId,
            ["observed"] = new JsonObject
            {
                ["selected"] = new JsonArray([.. selected.Select(s => (JsonNode)s)]),
                ["window"] = Registry.Read(target.Handle)?.ToJson(),
            },
        };
    }

    // ================================================================== screen.*

    private JsonObject ScreenCaptureAction(JsonObject payload)
    {
        var format = OptionalString(payload, "format", 8) ?? "png";
        if (!string.Equals(format, "png", StringComparison.OrdinalIgnoreCase))
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "payload.format must be \"png\"", retryable: false);
        }

        WindowInfo? target = null;
        RgbImage image;
        if (payload["window_id"] is not null)
        {
            target = Registry.Resolve(payload["window_id"]!.GetValue<string>());
            image = ScreenCapture.CaptureWindow(target.Handle);
        }
        else
        {
            image = ScreenCapture.CapturePrimaryScreen();
        }

        var scale = 1;
        var png = PngEncoder.Encode(image);
        while (png.Length > OperatorCapabilityNames.MaxCaptureBytes && scale < 4)
        {
            image = image.Halve();
            scale *= 2;
            png = PngEncoder.Encode(image);
        }

        if (png.Length > OperatorCapabilityNames.MaxCaptureBytes)
        {
            throw new CapabilityException(
                ErrorClasses.ValidationError,
                $"the capture is {png.Length} bytes as PNG even at 1/{scale} scale, over the {OperatorCapabilityNames.MaxCaptureBytes} byte cap",
                retryable: false);
        }

        return new JsonObject
        {
            ["width"] = image.Width,
            ["height"] = image.Height,
            ["png_base64"] = Convert.ToBase64String(png),
            ["bytes"] = png.Length,
            ["scale"] = scale,
            ["window_id"] = target?.WindowId,
            ["observed"] = new JsonObject { ["window"] = target is null ? null : Registry.Read(target.Handle)?.ToJson() },
        };
    }

    private JsonObject ScreenInspect()
    {
        var monitors = new JsonArray();
        foreach (var monitor in _monitors.List())
        {
            monitors.Add(new JsonObject
            {
                ["index"] = monitor.Index,
                ["primary"] = monitor.Primary,
                ["left"] = monitor.Left,
                ["top"] = monitor.Top,
                ["width"] = monitor.Width,
                ["height"] = monitor.Height,
            });
        }

        OperatorNative.GetCursorPos(out var cursor);
        var foreground = Registry.Foreground();
        var (vx, vy, vw, vh) = VirtualScreen();
        return new JsonObject
        {
            ["monitors"] = monitors,
            ["virtual_screen"] = new JsonObject { ["x"] = vx, ["y"] = vy, ["width"] = vw, ["height"] = vh },
            ["foreground"] = foreground?.ToJson(),
            ["cursor"] = new JsonObject { ["x"] = cursor.X, ["y"] = cursor.Y },
            ["observed"] = new JsonObject { ["foreground"] = foreground?.ToJson() },
        };
    }

    // ================================================================== file.*

    private JsonObject FileOpen(JsonObject payload, CancellationToken cancellationToken)
    {
        var path = RequireAuthorisedPath(payload, mustBeFile: true);
        var application = OptionalString(payload, "application", 260);
        int? pid = null;
        WindowInfo? window = null;
        if (application is not null)
        {
            var executable = ResolveApplication(application);
            // The application's argument policy applies to the one argument file.open gives
            // it: notepad and explorer take a path, calc / powershell / the browsers do not.
            var args = ArgumentPolicy.For(application).Apply(application, [path], Terminal.Roots);
            var process = StartTracked(executable, args, visible: true);
            pid = process.Id;
            window = WaitForWindow(process.Id, LaunchWindowWait, cancellationToken);
        }
        else
        {
            // ShellExecute: the owner's registered handler, which may reuse a running
            // instance and give us no process to wait on.
            try
            {
                using var process = Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
                pid = process?.Id;
            }
            catch (System.ComponentModel.Win32Exception ex)
            {
                throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"no application could open '{Path.GetFileName(path)}': {ex.Message}", retryable: false);
            }

            if (pid is not null)
            {
                window = WaitForWindow(pid.Value, TimeSpan.FromSeconds(3), cancellationToken);
            }
        }

        return new JsonObject
        {
            ["opened"] = true,
            ["path"] = path,
            ["pid"] = pid,
            ["window_id"] = window?.WindowId,
            ["observed"] = new JsonObject { ["window"] = window?.ToJson(), ["window_appeared"] = window is not null },
        };
    }

    private JsonObject FileReveal(JsonObject payload, CancellationToken cancellationToken)
    {
        var path = RequireAuthorisedPath(payload, mustBeFile: false);
        var explorer = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "explorer.exe");
        var before = new HashSet<long>(Registry.Enumerate().Where(w => w.Image == "explorer.exe").Select(w => w.Handle.ToInt64()));
        var startInfo = new ProcessStartInfo(explorer)
        {
            UseShellExecute = false,
            // Explorer's own syntax; the path was validated above and is quoted as a whole.
            Arguments = "/select,\"" + path + "\"",
        };
        using (var launcher = Process.Start(startInfo))
        {
            // The launcher hands the window to the shell's explorer.exe and exits.
        }

        WindowInfo? window = null;
        WindowActions.WaitUntil(
            () =>
            {
                window = Registry.Enumerate()
                    .FirstOrDefault(w => w.Image == "explorer.exe" && !before.Contains(w.Handle.ToInt64()) && w.ClassName == "CabinetWClass");
                return window is not null;
            },
            TimeSpan.FromSeconds(5),
            cancellationToken,
            stepMs: 100);
        if (window is null)
        {
            throw new CapabilityException(ErrorClasses.PostconditionFailed, "no Explorer window appeared for the path within 5 s", retryable: true);
        }

        var fileName = Path.GetFileName(path);
        var selection = WaitForSelection(window, fileName, TimeSpan.FromSeconds(5), cancellationToken);
        var stem = Path.GetFileNameWithoutExtension(fileName);
        var selectionNamesFile = selection.Any(s => s.StartsWith(stem, StringComparison.OrdinalIgnoreCase));
        // Whether the file is SHOWN in the view is observed separately from whether the
        // platform reports it selected: Windows Server's Explorer (the GitHub runner) never
        // exposed the item's selection through UI Automation while the item was plainly
        // listed. A planner reads both; a postcondition that needs the selection asks for it.
        var fileVisible = selectionNamesFile || FileListedInWindow(window, stem);
        return new JsonObject
        {
            ["revealed"] = true,
            ["path"] = path,
            ["window_id"] = window.WindowId,
            ["observed"] = new JsonObject
            {
                ["window"] = Registry.Read(window.Handle)?.ToJson(),
                ["selection"] = new JsonArray([.. selection.Select(s => (JsonNode)s)]),
                ["selection_names_file"] = selectionNamesFile,
                ["file_visible"] = fileVisible,
            },
        };
    }

    private bool FileListedInWindow(WindowInfo window, string stem)
    {
        try
        {
            var root = Inspector.RootForWindow(window.Handle);
            foreach (var controlType in new[] { ControlType.ListItem, ControlType.DataItem, ControlType.TreeItem })
            {
                var items = root.FindAll(TreeScope.Descendants, new PropertyCondition(AutomationElement.ControlTypeProperty, controlType));
                var inspected = 0;
                foreach (AutomationElement item in items)
                {
                    if (++inspected > 400)
                    {
                        break;
                    }

                    string? name;
                    try
                    {
                        name = item.Current.Name;
                    }
                    catch (Exception)
                    {
                        continue;
                    }

                    if (name is not null && name.StartsWith(stem, StringComparison.OrdinalIgnoreCase))
                    {
                        return true;
                    }
                }
            }
        }
        catch (Exception)
        {
            // An inspector failure is "not observed", never "not there".
        }

        return false;
    }

    // ================================================================== terminal.*

    private JsonObject TerminalOpen(JsonObject payload, CancellationToken cancellationToken)
    {
        var shell = (OptionalString(payload, "shell", 32) ?? "powershell").ToLowerInvariant();
        if (shell != "powershell")
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "payload.shell must be \"powershell\"", retryable: false);
        }

        var process = StartTracked(TerminalRunner.DefaultPowerShellPath(), ["-NoLogo"], visible: true);
        var window = WaitForWindow(process.Id, LaunchWindowWait, cancellationToken);
        return new JsonObject
        {
            ["pid"] = process.Id,
            ["window_id"] = window?.WindowId,
            ["shell"] = shell,
            ["observed"] = new JsonObject { ["window"] = window?.ToJson(), ["window_appeared"] = window is not null, ["process_alive"] = IsAlive(process.Id) },
        };
    }

    private JsonObject TerminalExecute(JsonObject payload, CancellationToken cancellationToken)
    {
        var command = RequireString(payload, "command", TerminalRunner.MaxCommandChars);
        var timeoutS = OptionalInt(payload, "timeout_s", 1, TerminalRunner.MaxTimeoutSeconds) ?? TerminalRunner.DefaultTimeoutSeconds;
        var result = Terminal.ExecuteAsync(command, TimeSpan.FromSeconds(timeoutS), cancellationToken).GetAwaiter().GetResult();
        result["observed"] = new JsonObject { ["exit_code"] = result["exit_code"]?.GetValue<int>(), ["stdout_chars"] = result["stdout"]?.GetValue<string>().Length ?? 0 };
        return result;
    }

    private JsonObject TerminalStatus(JsonObject payload)
    {
        var pid = RequireInt(payload, "pid", 1, int.MaxValue);
        Process? tracked;
        lock (_started)
        {
            _started.TryGetValue(pid, out tracked);
        }

        var alive = IsAlive(pid);
        int? exitCode = null;
        if (tracked is not null && tracked.HasExited)
        {
            try
            {
                exitCode = tracked.ExitCode;
            }
            catch (InvalidOperationException)
            {
                // Not ours to read.
            }
        }

        return new JsonObject
        {
            ["pid"] = pid,
            ["alive"] = alive,
            ["exit_code"] = exitCode,
            ["tracked"] = tracked is not null,
            ["observed"] = new JsonObject { ["alive"] = alive, ["windows"] = new JsonArray([.. Registry.Enumerate(pid).Select(w => (JsonNode)w.WindowId)]) },
        };
    }

    // ================================================================== helpers: windows + processes

    private static JsonObject WindowResult(WindowInfo observed) => new()
    {
        ["window"] = observed.ToJson(),
        ["observed"] = new JsonObject { ["window"] = observed.ToJson(), ["state"] = observed.State },
    };

    private WindowInfo ResolveTarget(JsonObject payload)
    {
        if (payload["window_id"] is not null)
        {
            return Registry.Resolve(payload["window_id"]!.GetValue<string>());
        }

        var pid = RequireInt(payload, "pid", 1, int.MaxValue);
        var windows = Registry.Enumerate(pid);
        return windows.FirstOrDefault(w => !w.Owned)
               ?? windows.FirstOrDefault()
               ?? throw new CapabilityException(ErrorClasses.UiTargetNotFound, $"process {pid} has no visible top-level window", retryable: true);
    }

    /// <summary>The focus guard, in the one place it is applied: expect the target the payload named, verify the foreground right before input.</summary>
    private void Guarded(WindowInfo target)
    {
        Guard.Expect(target);
        Guard.Verify();
    }

    /// <summary>
    /// The guard for a keyboard stream (ADR-0082 addendum 2, finding 2): verified once before
    /// anything is sent — a mismatch there is the usual "nothing was sent" — and handed to the
    /// synthesizer as <c>stillTargeted</c>, which asks it again before every batch. A stream
    /// stopped mid-way is re-described here with both windows and the count the synthesizer
    /// reported, so the planner knows exactly how much of the text reached the target.
    /// </summary>
    private void GuardedInput(WindowInfo target, Action<Func<bool>> send)
    {
        Guarded(target);
        try
        {
            send(Guard.StillTargeted);
        }
        catch (CapabilityException ex) when (ex.ErrorClass == ErrorClasses.FocusMismatch && ex.Detail.ContainsKey("typed_chars"))
        {
            throw new CapabilityException(
                ErrorClasses.FocusMismatch,
                $"the window in front changed while input was being sent: expected {FocusGuard.Describe(target)}, actual {FocusGuard.Describe(Registry.Foreground())}; typed_chars={ex.Detail["typed_chars"]} of {ex.Detail["total_chars"]} characters were handed to the system before the change (confirmed_chars={ex.Detail["confirmed_chars"]}; the last uncertain_chars={ex.Detail["uncertain_chars"]} may have reached the window now in front - read the target back before continuing), none after",
                retryable: true,
                ex.Detail);
        }
    }

    private WindowInfo? WaitForWindow(int pid, TimeSpan wait, CancellationToken cancellationToken)
    {
        WindowInfo? window = null;
        WindowActions.WaitUntil(
            () =>
            {
                var windows = Registry.Enumerate(pid);
                window = windows.FirstOrDefault(w => !w.Owned) ?? windows.FirstOrDefault();
                return window is not null;
            },
            wait,
            cancellationToken,
            stepMs: 100);
        return window;
    }

    private JsonObject? DetectModal(int pid, ISet<long> before)
    {
        var candidate = Registry.Enumerate(pid).FirstOrDefault(w => !before.Contains(w.Handle.ToInt64()) && (w.Owned || w.ClassName == "#32770"));
        if (candidate is null)
        {
            return null;
        }

        JsonObject dialog;
        try
        {
            dialog = Inspector.DescribeDialog(candidate.Handle);
        }
        catch (CapabilityException)
        {
            dialog = new JsonObject { ["title"] = candidate.Title, ["buttons"] = new JsonArray(), ["texts"] = new JsonArray() };
        }

        return new JsonObject
        {
            ["window_id"] = candidate.WindowId,
            ["window"] = candidate.ToJson(),
            ["dialog"] = dialog,
        };
    }

    private IReadOnlyList<string> WaitForSelection(WindowInfo window, string fileName, TimeSpan wait, CancellationToken cancellationToken)
    {
        IReadOnlyList<string> selection = [];
        var stem = Path.GetFileNameWithoutExtension(fileName);
        WindowActions.WaitUntil(
            () =>
            {
                try
                {
                    // Explorer holds several List controls (the navigation pane, quick
                    // access, the items view); which one comes first differs between
                    // Windows editions and languages (the GitHub runner's English Server
                    // returned an empty selection from its first list). Read every list,
                    // bounded, and take the one whose selection names the file.
                    var root = Inspector.RootForWindow(window.Handle);
                    // First the whole window's selected items (SelectionItem.IsSelected): on the
                    // GitHub runner's English Server the items view is not a List control at
                    // all, and the lists that do exist select the breadcrumb ("pagentos-operator-
                    // fixture") - the folder, not the file.
                    var selectedAnywhere = Inspector.SelectedNames(root);
                    if (selectedAnywhere.Any(s => s.StartsWith(stem, StringComparison.OrdinalIgnoreCase)))
                    {
                        selection = selectedAnywhere;
                        return true;
                    }

                    var lists = root.FindAll(TreeScope.Descendants, new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.List));
                    var inspected = 0;
                    foreach (AutomationElement list in lists)
                    {
                        if (++inspected > 8)
                        {
                            break;
                        }

                        var names = Inspector.SelectedNames(list);
                        if (names.Any(s => s.StartsWith(stem, StringComparison.OrdinalIgnoreCase)))
                        {
                            selection = names;
                            return true;
                        }

                        if (selection.Count == 0 && names.Count > 0)
                        {
                            selection = names;
                        }
                    }

                    return false;
                }
                catch (CapabilityException)
                {
                    return false;
                }
            },
            wait,
            cancellationToken,
            stepMs: 150);
        return selection;
    }

    private Process StartTracked(string executable, IReadOnlyList<string> args, bool visible)
    {
        var startInfo = new ProcessStartInfo(executable) { UseShellExecute = false, CreateNoWindow = !visible };
        foreach (var arg in args)
        {
            startInfo.ArgumentList.Add(arg);
        }

        var process = Process.Start(startInfo)
                      ?? throw new CapabilityException(ErrorClasses.InternalBug, $"Process.Start returned null for {executable}", retryable: true);
        lock (_started)
        {
            _started[process.Id] = process;
        }

        return process;
    }

    private static bool IsAlive(int pid)
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
        catch (InvalidOperationException)
        {
            return false;
        }
    }

    private static void Terminate(int pid)
    {
        try
        {
            using var process = Process.GetProcessById(pid);
            process.Kill(entireProcessTree: true);
        }
        catch (Exception)
        {
            // Already gone.
        }
    }

    private string ResolveApplication(string application)
    {
        if (_applications.TryGetValue(application, out var known))
        {
            if (!File.Exists(known))
            {
                throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"'{application}' is allowlisted but {known} is not installed", retryable: false);
            }

            return known;
        }

        if (Path.IsPathRooted(application) && application.EndsWith(".exe", StringComparison.OrdinalIgnoreCase))
        {
            var full = Path.GetFullPath(application);
            var roots = new[]
            {
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86),
                Environment.GetFolderPath(Environment.SpecialFolder.Windows),
            };
            if (roots.Any(root => !string.IsNullOrEmpty(root) && full.StartsWith(root.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)))
            {
                if (!File.Exists(full))
                {
                    throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"{full} does not exist", retryable: false);
                }

                return full;
            }
        }

        throw new CapabilityException(
            ErrorClasses.PermissionDenied,
            $"'{application}' is not an allowlisted application ({string.Join(",", _applications.Keys.OrderBy(k => k, StringComparer.Ordinal))}) nor an absolute .exe under Program Files / Windows",
            retryable: false);
    }

    /// <summary>
    /// <c>payload.path</c>, RESOLVED and CONTAINED (ADR-0082 addendum 2, finding 1): the path
    /// is opened, the file system asked what it really opened, and only that final path is
    /// compared with the roots' resolved forms. A path that cannot be resolved — missing,
    /// unreadable — is <c>permission_denied</c> like one outside the roots; there is no lexical
    /// fallback. What is returned, and acted on, is the resolved path.
    /// </summary>
    private string RequireAuthorisedPath(JsonObject payload, bool mustBeFile)
    {
        var raw = RequireString(payload, "path", 1024);
        if (!Path.IsPathRooted(raw))
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "payload.path must be absolute", retryable: false);
        }

        var resolved = Terminal.Roots.Confine(raw)
                       ?? throw new CapabilityException(
                           ErrorClasses.PermissionDenied,
                           $"'{raw}' does not resolve to a path inside the owner's authorised roots ({string.Join(";", AuthorisedRoots)}); every junction and link is followed before the comparison, and a path that cannot be resolved is refused",
                           retryable: false);

        if (mustBeFile)
        {
            if (!File.Exists(resolved))
            {
                throw new CapabilityException(ErrorClasses.UiTargetNotFound, $"'{resolved}' is not a file", retryable: false);
            }

            if (ExecutableExtensions.Contains(Path.GetExtension(resolved)))
            {
                throw new CapabilityException(ErrorClasses.PermissionDenied, $"'{Path.GetFileName(resolved)}' is executable; file.open opens documents, app.launch runs programs", retryable: false);
            }
        }

        return resolved;
    }

    private static (int X, int Y, int Width, int Height) VirtualScreen()
    {
        var x = OperatorNative.GetSystemMetrics(OperatorNative.SmXVirtualScreen);
        var y = OperatorNative.GetSystemMetrics(OperatorNative.SmYVirtualScreen);
        var w = OperatorNative.GetSystemMetrics(OperatorNative.SmCxVirtualScreen);
        var h = OperatorNative.GetSystemMetrics(OperatorNative.SmCyVirtualScreen);
        if (w <= 0 || h <= 0)
        {
            w = Math.Max(1, OperatorNative.GetSystemMetrics(OperatorNative.SmCxScreen));
            h = Math.Max(1, OperatorNative.GetSystemMetrics(OperatorNative.SmCyScreen));
        }

        return (x, y, w, h);
    }

    private static bool Within(int actual, int requested) => Math.Abs(actual - requested) <= RectTolerance;

    // ================================================================== helpers: payload

    private static ElementQuery ReadQuery(JsonObject payload)
        => new(
            OptionalString(payload, "automation_id", 256),
            OptionalString(payload, "name", 512),
            OptionalString(payload, "control_type", 64),
            OptionalString(payload, "name_prefix", 512));

    private static IReadOnlyList<string> ReadArgs(JsonObject payload)
    {
        if (payload["args"] is null)
        {
            return [];
        }

        if (payload["args"] is not JsonArray array)
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "payload.args must be an array of strings", retryable: false);
        }

        if (array.Count > MaxArgs)
        {
            throw new CapabilityException(ErrorClasses.ValidationError, $"payload.args may carry at most {MaxArgs} arguments", retryable: false);
        }

        var args = new List<string>();
        foreach (var node in array)
        {
            if (node is null || node.GetValueKind() != System.Text.Json.JsonValueKind.String)
            {
                throw new CapabilityException(ErrorClasses.ValidationError, "payload.args must contain strings", retryable: false);
            }

            var value = node.GetValue<string>();
            if (value.Length > MaxArgChars || value.Any(char.IsControl))
            {
                throw new CapabilityException(ErrorClasses.ValidationError, $"an argument exceeds {MaxArgChars} characters or carries control characters", retryable: false);
            }

            args.Add(value);
        }

        return args;
    }

    /// <summary>§1: a payload flagged <c>secret</c> is refused before anything else looks at it. Secrets are the password manager's job.</summary>
    private static void RefuseSecret(JsonObject payload)
    {
        if (payload["secret"] is JsonValue flag && flag.TryGetValue<bool>(out var secret) && secret)
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "secrets are never typed", retryable: false);
        }
    }

    private static string RequireText(JsonObject payload, string key)
    {
        var text = RequireString(payload, key, OperatorCapabilityNames.MaxTypedChars, allowEmpty: true);
        foreach (var ch in text)
        {
            if (char.IsControl(ch) && ch is not ('\t' or '\r' or '\n'))
            {
                throw new CapabilityException(ErrorClasses.ValidationError, $"payload.{key} carries a control character (U+{(int)ch:X4}); only tab and newline are typed", retryable: false);
            }
        }

        return text;
    }

    private static string RequireString(JsonObject payload, string key, int maxChars, bool allowEmpty = false)
    {
        var node = payload[key];
        if (node is null || node.GetValueKind() != System.Text.Json.JsonValueKind.String)
        {
            throw new CapabilityException(ErrorClasses.ValidationError, $"payload.{key} is required and must be a string", retryable: false);
        }

        var value = node.GetValue<string>();
        if (!allowEmpty && string.IsNullOrWhiteSpace(value))
        {
            throw new CapabilityException(ErrorClasses.ValidationError, $"payload.{key} must not be empty", retryable: false);
        }

        if (value.Length > maxChars)
        {
            throw new CapabilityException(ErrorClasses.ValidationError, $"payload.{key} exceeds {maxChars} characters ({value.Length})", retryable: false);
        }

        return value;
    }

    private static string? OptionalString(JsonObject payload, string key, int maxChars)
    {
        var node = payload[key];
        if (node is null)
        {
            return null;
        }

        return RequireString(payload, key, maxChars);
    }

    private static int RequireInt(JsonObject payload, string key, int min, int max)
    {
        var value = OptionalInt(payload, key, min, max);
        return value ?? throw new CapabilityException(ErrorClasses.ValidationError, $"payload.{key} is required and must be an integer", retryable: false);
    }

    private static int? OptionalInt(JsonObject payload, string key, int min, int max)
    {
        var node = payload[key];
        if (node is null)
        {
            return null;
        }

        if (node.GetValueKind() != System.Text.Json.JsonValueKind.Number || node is not JsonValue number)
        {
            throw new CapabilityException(ErrorClasses.ValidationError, $"payload.{key} must be an integer", retryable: false);
        }

        // A parsed payload converts to any numeric type; a node built in-process from an
        // int converts only to that type. Try the integer shapes first, the double last.
        double raw;
        if (number.TryGetValue<int>(out var asInt))
        {
            raw = asInt;
        }
        else if (number.TryGetValue<long>(out var asLong))
        {
            raw = asLong;
        }
        else if (!number.TryGetValue<double>(out raw))
        {
            throw new CapabilityException(ErrorClasses.ValidationError, $"payload.{key} must be an integer", retryable: false);
        }

        if (Math.Floor(raw) != raw)
        {
            throw new CapabilityException(ErrorClasses.ValidationError, $"payload.{key} must be an integer", retryable: false);
        }

        if (raw < min || raw > max)
        {
            throw new CapabilityException(
                ErrorClasses.ValidationError,
                $"payload.{key}={raw.ToString(CultureInfo.InvariantCulture)} is outside [{min}, {max}]",
                retryable: false);
        }

        return (int)raw;
    }

    private static bool? OptionalBool(JsonObject payload, string key)
    {
        var node = payload[key];
        if (node is null)
        {
            return null;
        }

        var kind = node.GetValueKind();
        if (kind is not (System.Text.Json.JsonValueKind.True or System.Text.Json.JsonValueKind.False))
        {
            throw new CapabilityException(ErrorClasses.ValidationError, $"payload.{key} must be a boolean", retryable: false);
        }

        return kind == System.Text.Json.JsonValueKind.True;
    }

    private void Record(string capability, string outcome, long durationMs, bool? retryable)
    {
        var detail = $"outcome={outcome} duration_ms={durationMs}" + (retryable is null ? string.Empty : $" retryable={(retryable.Value ? "true" : "false")}");
        _audit?.Write(AuditRequestEvent, capability: capability, status: outcome, detail: detail);
        _logger.LogInformation("operator request {Capability} outcome={Outcome} duration_ms={DurationMs}", capability, outcome, durationMs);
    }
}
