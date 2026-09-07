using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// Invariant 2 (M19_DIGITAL_OPERATOR_SPEC.md §1): two Notepads, a plan that targets A while B
/// is in front. The type is refused with <c>focus_mismatch</c> naming both windows, and B's
/// document — read back over UI Automation — is still empty: nothing was typed anywhere.
/// </summary>
[Collection(OperatorLabCollection.Name)]
public sealed class FocusGuardTests : IDisposable
{
    private readonly OperatorLab _lab = new();

    public void Dispose() => _lab.Dispose();

    [LabFact]
    public void Typing_into_a_window_that_is_not_in_front_is_refused_and_nothing_reaches_the_front_window()
    {
        var (_, a, _) = _lab.LaunchNotepad();
        var (_, b, _) = _lab.LaunchNotepad();
        _lab.Activate(b);

        var ex = _lab.ExpectFailure(OperatorCapabilityNames.KeyboardType, new JsonObject { ["window_id"] = a, ["text"] = OperatorLab.TypedSample });

        Assert.Equal(ErrorClasses.FocusMismatch, ex.ErrorClass);
        Assert.True(ex.Retryable);
        Assert.Contains(a, ex.Message, StringComparison.Ordinal);
        Assert.Contains(b, ex.Message, StringComparison.Ordinal);
        Assert.Contains("nothing was sent", ex.Message, StringComparison.Ordinal);

        // Neither document received a character.
        Thread.Sleep(300);
        Assert.Equal(string.Empty, _lab.ReadDocument(b).Value);
        Assert.Equal(string.Empty, _lab.ReadDocument(a).Value);

        // The pointer family is guarded the same way.
        var click = _lab.ExpectFailure(OperatorCapabilityNames.PointerClick, new JsonObject { ["window_id"] = a, ["x"] = 10, ["y"] = 10 });
        Assert.Equal(ErrorClasses.FocusMismatch, click.ErrorClass);

        // After the planner re-resolves — activates A out loud — the same type succeeds.
        _lab.Activate(a);
        _lab.Exec(OperatorCapabilityNames.KeyboardType, new JsonObject { ["window_id"] = a, ["text"] = "A" });
        Assert.Equal("A", _lab.WaitForDocument(a, "A"));
        Assert.Equal(string.Empty, _lab.ReadDocument(b).Value);
    }

    [Fact]
    public void The_rule_is_handle_pid_image_and_a_24_character_title_prefix()
    {
        var expected = new WindowInfo("w-1-1", new IntPtr(1), 10, "notepad.exe", "Adsız - Not Defteri", "normal", new WindowRect(0, 0, 1, 1), true, "Notepad", false);

        Assert.True(FocusGuard.Matches(expected, expected));
        Assert.True(FocusGuard.Matches(expected, expected with { Title = "Adsız - Not Defteri*" }), "a suffix beyond the prefix is tolerated");
        Assert.False(FocusGuard.Matches(expected, null));
        Assert.False(FocusGuard.Matches(expected, expected with { Handle = new IntPtr(2) }));
        Assert.False(FocusGuard.Matches(expected, expected with { Pid = 11 }));
        Assert.False(FocusGuard.Matches(expected, expected with { Image = "chrome.exe" }));
        Assert.False(FocusGuard.Matches(expected, expected with { Title = "*Adsız - Not Defteri" }), "a changed beginning is a different window as far as the guard knows");

        // The mid-stream leg (ADR-0082 addendum 2, finding 2) is identity only: the same
        // handle stays the target when its title changes because of the typing (Notepad's
        // dirty marker is a PREFIX on Windows 10), and no title makes another handle the target.
        Assert.True(FocusGuard.SameWindow(expected, expected with { Title = "*Adsız - Not Defteri" }));
        Assert.True(FocusGuard.SameWindow(expected, expected with { Title = "something else entirely" }));
        Assert.False(FocusGuard.SameWindow(expected, expected with { Handle = new IntPtr(2) }));
        Assert.False(FocusGuard.SameWindow(expected, expected with { Pid = 11 }));
        Assert.False(FocusGuard.SameWindow(expected, expected with { Image = "chrome.exe" }));
        Assert.False(FocusGuard.SameWindow(expected, null));

        var longTitle = new string('x', 40);
        var expectedLong = expected with { Title = longTitle + "-tail" };
        Assert.True(FocusGuard.Matches(expectedLong, expectedLong with { Title = longTitle[..FocusGuard.TitlePrefixChars] + "something else" }));
        Assert.False(FocusGuard.Matches(expectedLong, expectedLong with { Title = longTitle[..(FocusGuard.TitlePrefixChars - 1)] + "y" }));
    }
}
