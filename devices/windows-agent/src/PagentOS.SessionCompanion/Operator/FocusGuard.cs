using System.Runtime.Versioning;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion.Operator;

/// <summary>
/// Invariant 2 of M19_DIGITAL_OPERATOR_SPEC.md §1: before any keyboard or pointer action the
/// companion compares the window the plan EXPECTS (handle, process id, process image, title
/// prefix — all from the plan's own OBSERVE) with the window that is ACTUALLY in front at the
/// moment of acting. A mismatch is a refusal that carries both sides, so the planner can
/// re-resolve or give up. It is never a retry into whatever window happens to be in front,
/// and it never activates the expected window on its own: activation is a separate,
/// observable action the planner asks for.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class FocusGuard(WindowRegistry registry)
{
    /// <summary>How much of the expected title must match: enough to tell two documents apart, short enough to survive a dirty-marker suffix.</summary>
    public const int TitlePrefixChars = 24;

    private WindowInfo? _expected;

    public WindowInfo? Expected => _expected;

    public void Expect(WindowInfo window) => _expected = window ?? throw new ArgumentNullException(nameof(window));

    /// <summary>
    /// The check itself, right before input. Returns the verified foreground window; throws
    /// <c>focus_mismatch</c> (retryable) naming expected and actual otherwise.
    /// </summary>
    public WindowInfo Verify()
    {
        var expected = _expected ?? throw new CapabilityException(
            ErrorClasses.InternalBug,
            "focus guard verified with no expected window (the dispatcher must Expect() first)",
            retryable: false);

        var actual = registry.Foreground();
        if (!Matches(expected, actual))
        {
            throw new CapabilityException(
                ErrorClasses.FocusMismatch,
                $"the window in front is not the one this action targets: expected {Describe(expected)}, actual {Describe(actual)}; nothing was sent",
                retryable: true);
        }

        return actual!;
    }

    /// <summary>
    /// The mid-stream check, for the input synthesizer to ask before EVERY batch of events it
    /// sends (ADR-0082 addendum 2, finding 2): is the SAME WINDOW still in front — a fresh read
    /// of the foreground against the expected handle, pid and image. The title is deliberately
    /// not part of it: the title leg of <see cref="Verify"/> catches a plan whose OBSERVE went
    /// stale before ACT, but once input is streaming the title changes BECAUSE of the input
    /// (Notepad prefixes <c>*</c> the moment the first character lands — the lab's first cut
    /// of this check refused every second batch on that), and a same-handle window is the
    /// window the guard verified. False when nothing is expected.
    /// </summary>
    public bool StillTargeted()
    {
        var expected = _expected;
        return expected is not null && SameWindow(expected, registry.Foreground());
    }

    /// <summary>The rule, pure and public: handle, pid and image equal, and the actual title starts with the expected title's first <see cref="TitlePrefixChars"/> characters.</summary>
    public static bool Matches(WindowInfo expected, WindowInfo? actual)
    {
        if (!SameWindow(expected, actual))
        {
            return false;
        }

        var prefix = expected.Title.Length <= TitlePrefixChars ? expected.Title : expected.Title[..TitlePrefixChars];
        return actual!.Title.StartsWith(prefix, StringComparison.Ordinal);
    }

    /// <summary>The identity leg alone: handle, pid and image equal — the window, whatever it is titled right now.</summary>
    public static bool SameWindow(WindowInfo expected, WindowInfo? actual)
        => actual is not null
           && actual.Handle == expected.Handle
           && actual.Pid == expected.Pid
           && string.Equals(actual.Image, expected.Image, StringComparison.OrdinalIgnoreCase);

    public static string Describe(WindowInfo? window)
        => window is null
            ? "(no foreground window)"
            : $"{window.WindowId} pid={window.Pid} image={window.Image} title=\"{Truncate(window.Title, 60)}\"";

    private static string Truncate(string text, int max) => text.Length <= max ? text : text[..max] + "…";
}
