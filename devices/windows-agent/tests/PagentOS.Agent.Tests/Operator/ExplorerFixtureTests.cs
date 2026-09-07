using System.Text.Json.Nodes;
using System.Threading;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// The file family on a fixture folder under <c>%TEMP%\pagentos-operator-fixture\</c>
/// (M19_DIGITAL_OPERATOR_SPEC.md §5): <c>file.reveal</c> opens Explorer with the file selected
/// — verified through the window title and the Explorer list's selection read back over UI
/// Automation — and <c>file.open</c> opens it in Notepad. Both windows are closed afterwards;
/// Explorer's process is never touched.
/// </summary>
[Collection(OperatorLabCollection.Name)]
public sealed class ExplorerFixtureTests : IDisposable
{
    private readonly OperatorLab _lab = new();

    public void Dispose() => _lab.Dispose();

    [LabFact]
    public void File_reveal_opens_Explorer_on_the_fixture_folder_with_the_file_selected()
    {
        var (dir, file) = OperatorLab.Fixture();

        var revealed = _lab.Exec(OperatorCapabilityNames.FileReveal, new JsonObject { ["path"] = file });
        var windowId = revealed["window_id"]!.GetValue<string>();
        _lab.TrackWindow(windowId);
        Assert.True(revealed["revealed"]!.GetValue<bool>());

        var window = (JsonObject)revealed["observed"]!["window"]!;
        Assert.Equal("explorer.exe", window["image"]!.GetValue<string>());
        // Explorer titles the window with the folder name; an English Windows appends
        // " - File Explorer" (the GitHub runner), a Turkish one " - Dosya Gezgini" or nothing.
        Assert.StartsWith(Path.GetFileName(dir), window["title"]!.GetValue<string>(), StringComparison.Ordinal);

        var selection = revealed["observed"]!["selection"]!.AsArray().Select(s => s!.GetValue<string>()).ToList();
        Assert.Contains(selection, s => s.StartsWith("fixture", StringComparison.OrdinalIgnoreCase));

        // The item is reachable by name through ui.inspect too: the planner's way of checking.
        var inspected = _lab.Exec(OperatorCapabilityNames.UiInspect, new JsonObject
        {
            ["window_id"] = windowId,
            ["control_type"] = "ListItem",
            ["name_prefix"] = "fixture",
            ["depth"] = 1,
        });
        Assert.StartsWith("fixture", inspected["root"]!["name"]!.GetValue<string>(), StringComparison.OrdinalIgnoreCase);
        Assert.True(inspected["root"]!["selected"]!.GetValue<bool>());

        var closed = _lab.Exec(OperatorCapabilityNames.WindowClose, new JsonObject { ["window_id"] = windowId });
        Assert.True(closed["closed"]!.GetValue<bool>());
    }

    [LabFact]
    public void File_open_with_notepad_shows_the_file_and_closes_cleanly()
    {
        var (_, file) = OperatorLab.Fixture();

        var opened = _lab.Exec(OperatorCapabilityNames.FileOpen, new JsonObject { ["path"] = file, ["application"] = "notepad" });
        Assert.True(opened["opened"]!.GetValue<bool>());
        var pid = opened["pid"]!.GetValue<int>();
        _lab.TrackPid(pid);
        Assert.True(opened["observed"]!["window_appeared"]!.GetValue<bool>());
        var windowId = opened["window_id"]!.GetValue<string>();
        var window = (JsonObject)opened["observed"]!["window"]!;
        Assert.Equal("notepad.exe", window["image"]!.GetValue<string>());
        // The window can appear titled "Untitled - Notepad" a few hundred milliseconds
        // before the file is loaded (seen on the GitHub runner): re-observe until the
        // title names the file, bounded - the planner's OBSERVE AGAIN, not a guess.
        var title = window["title"]!.GetValue<string>();
        var deadline = DateTime.UtcNow.AddSeconds(5);
        while (!title.Contains("fixture", StringComparison.OrdinalIgnoreCase) && DateTime.UtcNow < deadline)
        {
            Thread.Sleep(100);
            var listed = _lab.Exec(OperatorCapabilityNames.WindowList, new JsonObject { ["pid"] = pid });
            var match = listed["windows"]!.AsArray()
                .Select(w => (JsonObject)w!)
                .FirstOrDefault(w => w["window_id"]!.GetValue<string>() == windowId);
            if (match is not null)
            {
                title = match["title"]!.GetValue<string>();
            }
        }
        Assert.Contains("fixture", title, StringComparison.OrdinalIgnoreCase);

        // The document shows the fixture's text, read back over UI Automation.
        var (_, _, _, value) = _lab.ReadDocument(windowId);
        Assert.StartsWith("PagentOS operator fixture", value, StringComparison.Ordinal);

        var closed = _lab.Exec(OperatorCapabilityNames.AppClose, new JsonObject { ["pid"] = pid });
        Assert.True(closed["closed"]!.GetValue<bool>());
        Assert.Equal("wm_close", closed["method"]!.GetValue<string>());
        Assert.True(OperatorLab.WaitForExit(pid, TimeSpan.FromSeconds(3)));
    }

    [Fact]
    public void A_path_outside_the_authorised_roots_or_an_executable_is_refused_before_anything_opens()
    {
        var outside = _lab.ExpectFailure(OperatorCapabilityNames.FileOpen, new JsonObject { ["path"] = @"C:\Windows\System32\drivers\etc\hosts" });
        Assert.Equal(ErrorClasses.PermissionDenied, outside.ErrorClass);
        Assert.False(outside.Retryable);

        var (dir, _) = OperatorLab.Fixture();
        var exe = Path.Combine(dir, "harmless.exe");
        File.WriteAllText(exe, "not really");
        var executable = _lab.ExpectFailure(OperatorCapabilityNames.FileOpen, new JsonObject { ["path"] = exe });
        Assert.Equal(ErrorClasses.PermissionDenied, executable.ErrorClass);

        var traversal = _lab.ExpectFailure(OperatorCapabilityNames.FileReveal, new JsonObject { ["path"] = Path.Combine(dir, "..", "..", "..", "..", "Windows", "notepad.exe") });
        Assert.True(traversal.ErrorClass is ErrorClasses.PermissionDenied or ErrorClasses.UiTargetNotFound, traversal.ErrorClass);

        var relative = _lab.ExpectFailure(OperatorCapabilityNames.FileOpen, new JsonObject { ["path"] = "fixture.txt" });
        Assert.Equal(ErrorClasses.ValidationError, relative.ErrorClass);
        Assert.Empty(_lab.Operator.StartedPids);
    }
}
