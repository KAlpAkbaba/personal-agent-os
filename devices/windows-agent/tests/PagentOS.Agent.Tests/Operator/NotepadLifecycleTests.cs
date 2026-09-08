using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// The spec's first lab scenario (M19_DIGITAL_OPERATOR_SPEC.md §5): Notepad launched, observed,
/// activated, typed into with Turkish text, read back through UI Automation and compared
/// exactly, then maximised / restored / moved / resized with every effect re-observed, then
/// closed and verified gone. Every assertion is on something the companion READ after acting.
/// </summary>
[Collection(OperatorLabCollection.Name)]
public sealed class NotepadLifecycleTests : IDisposable
{
    private readonly OperatorLab _lab = new();

    public void Dispose() => _lab.Dispose();

    [LabFact]
    public void Launch_observe_activate_type_read_back_resize_move_close_verified_gone()
    {
        var (pid, windowId, window) = _lab.LaunchNotepad();
        Assert.Equal(pid, window["pid"]!.GetValue<int>());
        Assert.Equal("normal", window["state"]!.GetValue<string>());

        // The window is in window.list and app.list, by the same id.
        var list = _lab.Exec(OperatorCapabilityNames.WindowList, new JsonObject { ["pid"] = pid });
        Assert.Contains(list["windows"]!.AsArray(), w => w!["window_id"]!.GetValue<string>() == windowId);
        var apps = _lab.Exec(OperatorCapabilityNames.AppList, new JsonObject());
        var app = apps["applications"]!.AsArray().Single(a => a!["pid"]!.GetValue<int>() == pid);
        Assert.Equal("notepad.exe", app!["image"]!.GetValue<string>());
        Assert.Contains(app["windows"]!.AsArray(), w => w!.GetValue<string>() == windowId);

        // Activate, and see it in front through window.current.
        _lab.Activate(windowId);
        var current = _lab.Exec(OperatorCapabilityNames.WindowCurrent, new JsonObject());
        Assert.Equal(windowId, current["window"]!["window_id"]!.GetValue<string>());

        // Type Turkish, read it back through UI Automation, compare exactly.
        var typed = _lab.Exec(OperatorCapabilityNames.KeyboardType, new JsonObject { ["window_id"] = windowId, ["text"] = OperatorLab.TypedSample });
        Assert.Equal(OperatorLab.TypedSample.Length, typed["typed_chars"]!.GetValue<int>());
        var value = _lab.WaitForDocument(windowId, OperatorLab.TypedSample);
        Assert.Equal(OperatorLab.TypedSample, value);

        // Maximise, restore: each re-observed state equals the requested one.
        var maximized = _lab.Exec(OperatorCapabilityNames.WindowMaximize, new JsonObject { ["window_id"] = windowId });
        Assert.Equal("maximized", maximized["observed"]!["state"]!.GetValue<string>());
        var restored = _lab.Exec(OperatorCapabilityNames.WindowRestore, new JsonObject { ["window_id"] = windowId });
        Assert.Equal("normal", restored["observed"]!["state"]!.GetValue<string>());

        // Move to (100,100), resize to 800x600: re-observed within 8 px.
        var moved = _lab.Exec(OperatorCapabilityNames.WindowMove, new JsonObject { ["window_id"] = windowId, ["x"] = 100, ["y"] = 100 });
        var rect = moved["window"]!["rect"]!;
        Assert.InRange(rect["x"]!.GetValue<int>(), 100 - OperatorCapabilities.RectTolerance, 100 + OperatorCapabilities.RectTolerance);
        Assert.InRange(rect["y"]!.GetValue<int>(), 100 - OperatorCapabilities.RectTolerance, 100 + OperatorCapabilities.RectTolerance);
        var resized = _lab.Exec(OperatorCapabilityNames.WindowResize, new JsonObject { ["window_id"] = windowId, ["width"] = 800, ["height"] = 600 });
        rect = resized["window"]!["rect"]!;
        Assert.InRange(rect["width"]!.GetValue<int>(), 800 - OperatorCapabilities.RectTolerance, 800 + OperatorCapabilities.RectTolerance);
        Assert.InRange(rect["height"]!.GetValue<int>(), 600 - OperatorCapabilities.RectTolerance, 600 + OperatorCapabilities.RectTolerance);

        // Minimise and restore once more, so the minimised state is proven as well.
        var minimized = _lab.Exec(OperatorCapabilityNames.WindowMinimize, new JsonObject { ["window_id"] = windowId });
        Assert.Equal("minimized", minimized["observed"]!["state"]!.GetValue<string>());
        _lab.Exec(OperatorCapabilityNames.WindowRestore, new JsonObject { ["window_id"] = windowId });

        // A capture of the window: a real PNG of the window's size, under the cap.
        var capture = _lab.Exec(OperatorCapabilityNames.ScreenCapture, new JsonObject { ["window_id"] = windowId });
        var png = Convert.FromBase64String(capture["png_base64"]!.GetValue<string>());
        Assert.True(png.Length <= OperatorCapabilityNames.MaxCaptureBytes);
        var (pngWidth, pngHeight) = PngEncoder.ReadHeader(png);
        Assert.Equal(capture["width"]!.GetValue<int>(), pngWidth);
        Assert.Equal(capture["height"]!.GetValue<int>(), pngHeight);
        Assert.Equal(1, capture["scale"]!.GetValue<int>());
        Assert.InRange(pngWidth, 800 - OperatorCapabilities.RectTolerance, 800 + OperatorCapabilities.RectTolerance);

        // Close with unsaved text: force, so this test proves "terminated" while
        // ModalDetectionTests proves the polite path. Then verify: gone from the registry,
        // gone as a process.
        var closed = _lab.Exec(OperatorCapabilityNames.AppClose, new JsonObject { ["pid"] = pid, ["force"] = true });
        Assert.True(closed["closed"]!.GetValue<bool>());
        Assert.Equal("terminated", closed["method"]!.GetValue<string>());
        Assert.True(OperatorLab.WaitForExit(pid, TimeSpan.FromSeconds(3)));
        var gone = _lab.ExpectFailure(OperatorCapabilityNames.WindowActivate, new JsonObject { ["window_id"] = windowId });
        Assert.Equal(ErrorClasses.UiTargetNotFound, gone.ErrorClass);
    }

    [LabFact]
    public void A_window_whose_process_died_is_not_a_target_even_while_its_handle_lingers()
    {
        // The runner showed a forced close leaving the window handle alive for a moment after
        // the process had exited: window.activate then failed its postcondition instead of
        // saying the target is gone. The registry now asks the process, not only the handle.
        var (pid, windowId, _) = _lab.LaunchNotepad();
        _lab.Activate(windowId);
        using (var process = System.Diagnostics.Process.GetProcessById(pid))
        {
            process.Kill();
            process.WaitForExit(3000);
        }

        Assert.False(WindowRegistry.ProcessAlive(pid));
        var gone = _lab.ExpectFailure(OperatorCapabilityNames.WindowActivate, new JsonObject { ["window_id"] = windowId });
        Assert.Equal(ErrorClasses.UiTargetNotFound, gone.ErrorClass);
        var listed = _lab.Exec(OperatorCapabilityNames.WindowList, new JsonObject());
        Assert.DoesNotContain(listed["windows"]!.AsArray(), w => w!["window_id"]!.GetValue<string>() == windowId);
    }

    [LabFact]
    public void Setting_the_documents_value_through_UI_Automation_reads_back_exactly()
    {
        var (pid, windowId, _) = _lab.LaunchNotepad();
        _lab.Activate(windowId);
        var (automationId, controlType, className, initial) = _lab.ReadDocument(windowId);
        Assert.Equal(string.Empty, initial);
        Assert.True(controlType is "Document" or "Edit", $"unexpected control type {controlType}");
        Assert.True(className is "Edit" or "RichEditD2DPT", $"unexpected document class {className}");

        var query = string.IsNullOrEmpty(automationId)
            ? new JsonObject { ["window_id"] = windowId, ["control_type"] = controlType, ["value"] = OperatorLab.TypedSample }
            : new JsonObject { ["window_id"] = windowId, ["automation_id"] = automationId, ["value"] = OperatorLab.TypedSample };
        var set = _lab.Exec(OperatorCapabilityNames.UiSetValue, query);
        Assert.Equal(OperatorLab.TypedSample, set["observed_value"]!.GetValue<string>());
        Assert.Equal(OperatorLab.TypedSample, _lab.WaitForDocument(windowId, OperatorLab.TypedSample));

        // A key on the guarded path: End then typing appends, proving keyboard.key reached
        // the same control the value was set on.
        _lab.Exec(OperatorCapabilityNames.KeyboardKey, new JsonObject { ["window_id"] = windowId, ["key"] = "end" });
        _lab.Exec(OperatorCapabilityNames.KeyboardType, new JsonObject { ["window_id"] = windowId, ["text"] = "!" });
        Assert.Equal(OperatorLab.TypedSample + "!", _lab.WaitForDocument(windowId, OperatorLab.TypedSample + "!"));

        // Ctrl+A then Delete through keyboard.shortcut / keyboard.key empties it again.
        _lab.Exec(OperatorCapabilityNames.KeyboardShortcut, new JsonObject { ["window_id"] = windowId, ["keys"] = new JsonArray("ctrl", "a") });
        _lab.Exec(OperatorCapabilityNames.KeyboardKey, new JsonObject { ["window_id"] = windowId, ["key"] = "delete" });
        Assert.Equal(string.Empty, _lab.WaitForDocument(windowId, string.Empty));

        // Empty again, so a plain close needs no dialog: closed through WM_CLOSE, not a kill.
        var closed = _lab.Exec(OperatorCapabilityNames.WindowClose, new JsonObject { ["window_id"] = windowId });
        Assert.True(closed["closed"]!.GetValue<bool>(), "an unmodified Notepad should close on WM_CLOSE");
        Assert.Null(closed["modal"]);
        Assert.True(OperatorLab.WaitForExit(pid, TimeSpan.FromSeconds(5)));
    }

    [LabFact]
    public void A_pointer_click_inside_the_window_lands_and_the_cursor_is_re_observed_there()
    {
        var (_, windowId, window) = _lab.LaunchNotepad();
        _lab.Activate(windowId);
        var rect = window["rect"]!;
        var x = Math.Min(200, rect["width"]!.GetValue<int>() - 10);
        var y = Math.Min(200, rect["height"]!.GetValue<int>() - 10);
        var click = _lab.Exec(OperatorCapabilityNames.PointerClick, new JsonObject { ["window_id"] = windowId, ["x"] = x, ["y"] = y });
        Assert.Equal("window", click["space"]!.GetValue<string>());
        var cursor = click["observed"]!["cursor"]!;
        Assert.InRange(cursor["x"]!.GetValue<int>(), click["screen_x"]!.GetValue<int>() - 2, click["screen_x"]!.GetValue<int>() + 2);
        Assert.InRange(cursor["y"]!.GetValue<int>(), click["screen_y"]!.GetValue<int>() - 2, click["screen_y"]!.GetValue<int>() + 2);
        Assert.Equal(windowId, click["observed"]!["window"]!["window_id"]!.GetValue<string>());

        var screen = _lab.Exec(OperatorCapabilityNames.ScreenInspect, new JsonObject());
        Assert.NotEmpty(screen["monitors"]!.AsArray());
        Assert.Equal(windowId, screen["foreground"]!["window_id"]!.GetValue<string>());
    }
}
