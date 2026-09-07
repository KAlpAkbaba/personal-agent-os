using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// Modal detection (M19_DIGITAL_OPERATOR_SPEC.md §5): a Notepad with unsaved text answers
/// WM_CLOSE with a Save dialog. <c>app.close</c> reports <c>closed: false</c> and describes
/// the dialog through UI Automation; the planner dismisses it through <c>ui.invoke</c> on the
/// "Don't save" / "Kaydetme" button (or Escape and force when the button cannot be named),
/// and the process is then verified gone.
/// </summary>
[Collection(OperatorLabCollection.Name)]
public sealed class ModalDetectionTests : IDisposable
{
    private readonly OperatorLab _lab = new();

    public void Dispose() => _lab.Dispose();

    private (int Pid, string WindowId) NotepadWithUnsavedText()
    {
        var (pid, windowId, _) = _lab.LaunchNotepad();
        _lab.Activate(windowId);
        _lab.Exec(OperatorCapabilityNames.KeyboardType, new JsonObject { ["window_id"] = windowId, ["text"] = "unsaved" });
        Assert.Equal("unsaved", _lab.WaitForDocument(windowId, "unsaved"));
        return (pid, windowId);
    }

    [LabFact]
    public void A_close_that_raises_a_save_dialog_is_reported_with_the_dialog_and_can_be_dismissed_through_UI_Automation()
    {
        var (pid, windowId) = NotepadWithUnsavedText();

        var attempt = _lab.Exec(OperatorCapabilityNames.AppClose, new JsonObject { ["pid"] = pid });
        Assert.False(attempt["closed"]!.GetValue<bool>());
        Assert.Equal("wm_close", attempt["method"]!.GetValue<string>());
        Assert.True(attempt["observed"]!["process_alive"]!.GetValue<bool>());
        var modal = attempt["modal"] as JsonObject;
        Assert.NotNull(modal);
        var modalWindowId = modal!["window_id"]!.GetValue<string>();
        Assert.NotEqual(windowId, modalWindowId);
        Assert.Equal(pid, modal["window"]!["pid"]!.GetValue<int>());
        var buttons = modal["dialog"]!["buttons"]!.AsArray().Select(b => b!["name"]!.GetValue<string>()).ToList();
        Assert.NotEmpty(buttons);

        var dontSave = buttons.FirstOrDefault(IsDontSave);
        if (dontSave is not null)
        {
            var invoked = _lab.Exec(OperatorCapabilityNames.UiInvoke, new JsonObject { ["window_id"] = modalWindowId, ["name"] = dontSave });
            Assert.True(invoked["invoked"]!.GetValue<bool>());
        }
        else
        {
            // The button could not be named in this locale: Escape dismisses the dialog, and
            // the close is then forced — the planner's documented fallback.
            _lab.Exec(OperatorCapabilityNames.KeyboardKey, new JsonObject { ["window_id"] = modalWindowId, ["key"] = "escape" });
            var forced = _lab.Exec(OperatorCapabilityNames.AppClose, new JsonObject { ["pid"] = pid, ["force"] = true });
            Assert.True(forced["closed"]!.GetValue<bool>());
        }

        Assert.True(OperatorLab.WaitForExit(pid, TimeSpan.FromSeconds(5)), "Notepad should be gone after the dialog was dismissed");
        Assert.DoesNotContain(
            _lab.Exec(OperatorCapabilityNames.WindowList, new JsonObject())["windows"]!.AsArray(),
            w => w!["pid"]!.GetValue<int>() == pid);
    }

    [LabFact]
    public void Window_close_reports_the_same_dialog_and_force_terminates_after_it()
    {
        var (pid, windowId) = NotepadWithUnsavedText();

        var attempt = _lab.Exec(OperatorCapabilityNames.WindowClose, new JsonObject { ["window_id"] = windowId });
        Assert.False(attempt["closed"]!.GetValue<bool>());
        var modal = attempt["modal"] as JsonObject;
        Assert.NotNull(modal);
        Assert.NotEmpty(modal!["dialog"]!["buttons"]!.AsArray());

        var forced = _lab.Exec(OperatorCapabilityNames.AppClose, new JsonObject { ["pid"] = pid, ["force"] = true });
        Assert.True(forced["closed"]!.GetValue<bool>());
        Assert.Equal("terminated", forced["method"]!.GetValue<string>());
        Assert.False(forced["observed"]!["process_alive"]!.GetValue<bool>());
        Assert.True(OperatorLab.WaitForExit(pid, TimeSpan.FromSeconds(3)));
    }

    private static bool IsDontSave(string name)
    {
        var folded = name.Replace("’", "'", StringComparison.Ordinal).Replace("(", string.Empty, StringComparison.Ordinal);
        return folded.Contains("Don't Save", StringComparison.OrdinalIgnoreCase)
               || folded.Contains("Don't save", StringComparison.OrdinalIgnoreCase)
               || folded.Contains("Kaydetme", StringComparison.OrdinalIgnoreCase);
    }
}
