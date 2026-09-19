using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion.Operator;

public enum PointerButton
{
    Left,
    Right,
}

/// <summary>
/// The input the operator can synthesise (M19_DIGITAL_OPERATOR_SPEC.md §3), behind an
/// interface so a test can prove that a refused payload sent NOTHING — not infer it from a
/// window that happened to stay empty. Every call here is made only after the focus guard
/// verified the target (invariant 2), and the keyboard calls carry the guard along as
/// <c>stillTargeted</c>: it is asked again before EVERY batch of events, and a "no" stops the
/// stream where it is (ADR-0082 addendum 2, finding 2). The interface itself knows nothing
/// about windows.
/// </summary>
public interface IInputSynthesizer
{
    /// <summary>
    /// Unicode text, one <c>KEYEVENTF_UNICODE</c> pair per UTF-16 code unit; newlines and tabs as
    /// their keys. <paramref name="stillTargeted"/> is asked before every batch; a false answer
    /// is <c>focus_mismatch</c> (retryable) whose <c>Detail["typed_chars"]</c> and message say how
    /// many characters were sent before it, and nothing more is sent.
    /// </summary>
    void TypeText(string text, Func<bool> stillTargeted);

    /// <summary>One named key (see <see cref="KeyMap"/>), pressed and released; <paramref name="stillTargeted"/> as for <see cref="TypeText"/>.</summary>
    void PressKey(string key, Func<bool> stillTargeted);

    /// <summary>Modifiers down in order, the key pressed and released, modifiers up in reverse; <paramref name="stillTargeted"/> as for <see cref="TypeText"/>.</summary>
    void Shortcut(IReadOnlyList<string> keys, Func<bool> stillTargeted);

    /// <summary>Move the pointer to a screen position without pressing anything.</summary>
    void MoveTo(int screenX, int screenY);

    /// <summary>Move, then press and release a button <paramref name="clicks"/> times.</summary>
    void Click(int screenX, int screenY, PointerButton button, int clicks);

    /// <summary>Move, then turn the wheel <paramref name="notches"/> notches (positive = away from the user / up).</summary>
    void Scroll(int screenX, int screenY, int notches);
}

/// <summary>
/// The fixed vocabulary of <c>keyboard.key</c> and <c>keyboard.shortcut</c> (§2). Anything
/// outside it is a <c>validation_error</c> before a single event exists; a key name is never
/// interpreted as a character to type.
/// </summary>
public static class KeyMap
{
    private static readonly Dictionary<string, (ushort Vk, bool Extended)> Named = new(StringComparer.OrdinalIgnoreCase)
    {
        ["enter"] = (0x0D, false),
        ["escape"] = (0x1B, false),
        ["tab"] = (0x09, false),
        ["backspace"] = (0x08, false),
        ["delete"] = (0x2E, true),
        ["insert"] = (0x2D, true),
        ["home"] = (0x24, true),
        ["end"] = (0x23, true),
        ["pageup"] = (0x21, true),
        ["pagedown"] = (0x22, true),
        ["up"] = (0x26, true),
        ["down"] = (0x28, true),
        ["left"] = (0x25, true),
        ["right"] = (0x27, true),
        ["space"] = (0x20, false),
        ["f1"] = (0x70, false),
        ["f2"] = (0x71, false),
        ["f3"] = (0x72, false),
        ["f4"] = (0x73, false),
        ["f5"] = (0x74, false),
        ["f6"] = (0x75, false),
        ["f7"] = (0x76, false),
        ["f8"] = (0x77, false),
        ["f9"] = (0x78, false),
        ["f10"] = (0x79, false),
        ["f11"] = (0x7A, false),
        ["f12"] = (0x7B, false),
    };

    private static readonly Dictionary<string, ushort> Modifiers = new(StringComparer.OrdinalIgnoreCase)
    {
        ["ctrl"] = 0x11,
        ["alt"] = 0x12,
        ["shift"] = 0x10,
    };

    /// <summary>The names <c>keyboard.key</c> accepts, for messages and documentation.</summary>
    public static IReadOnlyList<string> KeyNames => [.. Named.Keys.OrderBy(k => k, StringComparer.Ordinal)];

    public static IReadOnlyList<string> ModifierNames => [.. Modifiers.Keys.OrderBy(k => k, StringComparer.Ordinal)];

    public static bool IsModifier(string name) => Modifiers.ContainsKey(name);

    public static bool TryModifier(string name, out ushort vk) => Modifiers.TryGetValue(name, out vk);

    /// <summary>A named key, or — for shortcuts — a single letter or digit.</summary>
    public static bool TryKey(string name, bool allowCharacters, out ushort vk, out bool extended)
    {
        vk = 0;
        extended = false;
        if (Named.TryGetValue(name, out var named))
        {
            vk = named.Vk;
            extended = named.Extended;
            return true;
        }

        if (allowCharacters && name.Length == 1)
        {
            var ch = char.ToUpperInvariant(name[0]);
            if (ch is >= 'A' and <= 'Z' || ch is >= '0' and <= '9')
            {
                vk = ch;
                return true;
            }
        }

        return false;
    }

    /// <summary>Validate a <c>keyboard.shortcut</c> combination: 2..4 names, at least one modifier, exactly one key.</summary>
    public static void ValidateShortcut(IReadOnlyList<string> keys)
    {
        if (keys.Count is < 2 or > 4)
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "payload.keys must name 2 to 4 keys (modifiers plus one key)", retryable: false);
        }

        var modifiers = 0;
        var plain = 0;
        foreach (var key in keys)
        {
            if (IsModifier(key))
            {
                modifiers++;
            }
            else if (TryKey(key, allowCharacters: true, out _, out _))
            {
                plain++;
            }
            else
            {
                throw new CapabilityException(
                    ErrorClasses.ValidationError,
                    $"'{key}' is not a key this device knows (modifiers: {string.Join(",", ModifierNames)}; keys: a-z, 0-9, {string.Join(",", KeyNames)})",
                    retryable: false);
            }
        }

        if (modifiers == 0 || plain != 1)
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "payload.keys must combine at least one modifier with exactly one key", retryable: false);
        }
    }

    /// <summary>
    /// A named key, or ONE ASCII letter or digit. A page's own shortcuts are single characters
    /// (YouTube: k = play/pause, 0 = from the start) and the owner asks for them as keys -
    /// "0 tuşuna bas" (2026-09-19). Sent as a virtual key, so the page sees a real keydown;
    /// <c>keyboard.type</c>'s Unicode events would not trigger a shortcut. Anything longer, or
    /// outside A-Z/0-9, is still text and still refused here.
    /// </summary>
    public static void ValidateKey(string key)
    {
        if (!TryKey(key, allowCharacters: true, out _, out _))
        {
            throw new CapabilityException(
                ErrorClasses.ValidationError,
                $"'{key}' is not a key keyboard.key accepts ({string.Join(",", KeyNames)}, or one letter or digit); use keyboard.type for text",
                retryable: false);
        }
    }
}

/// <summary>
/// The batch loop, separated from <c>SendInput</c> so the rule can be PROVEN without a
/// desktop: events go out in batches of <see cref="BatchSize"/> with a short pause between
/// them (applications drop very large batches, and a dropped batch would be a silent
/// truncation of what the owner asked to type), and <c>stillTargeted</c> is asked before
/// EVERY batch, the first included. A "no" is <c>focus_mismatch</c> (retryable) carrying, in
/// <see cref="CapabilityException.Detail"/> and in the message, <c>typed_chars</c> — the
/// characters handed to the system while the target was verified in front — and
/// <c>uncertain_chars</c>: the LAST batch of them. Windows assigns injected keyboard input to
/// a thread's queue when that thread retrieves it, not when <c>SendInput</c> returns, so a
/// batch handed over within one pause of the change can reach the window now in front (the
/// lab measured exactly one batch doing so); <c>confirmed_chars</c> is everything before it.
/// No further batch is sent. The guard is a check before each hand-over, not a lock on the
/// foreground — nothing in user mode is.
/// </summary>
public static class InputBatcher
{
    public const int BatchSize = 64;
    public const int PauseBetweenBatchesMs = 5;

    /// <param name="eventCount">Events to send.</param>
    /// <param name="send">Hands events <c>[offset, offset + count)</c> to the system; throws when the system took fewer than asked.</param>
    /// <param name="stillTargeted">Asked before every batch.</param>
    /// <param name="charactersCompletedBy">Maps "events sent so far" to "characters of the owner's text completed", for the refusal.</param>
    /// <param name="totalCharacters">The text's length, for the refusal.</param>
    /// <returns>Batches sent.</returns>
    public static int Send(int eventCount, Action<int, int> send, Func<bool> stillTargeted, Func<int, int> charactersCompletedBy, int totalCharacters)
    {
        var batches = 0;
        var lastBatchOffset = 0;
        for (var offset = 0; offset < eventCount; offset += BatchSize)
        {
            if (!stillTargeted())
            {
                var typed = charactersCompletedBy(offset);
                var confirmed = charactersCompletedBy(lastBatchOffset);
                throw new CapabilityException(
                    ErrorClasses.FocusMismatch,
                    $"the window in front changed while input was being sent: typed_chars={typed} of {totalCharacters} characters were handed to the system before the change (confirmed_chars={confirmed}; the last uncertain_chars={typed - confirmed} went out within one batch pause of it and may have reached the window now in front), none after",
                    retryable: true,
                    new Dictionary<string, object?>
                    {
                        ["typed_chars"] = typed,
                        ["confirmed_chars"] = confirmed,
                        ["uncertain_chars"] = typed - confirmed,
                        ["total_chars"] = totalCharacters,
                        ["batches_sent"] = batches,
                    });
            }

            var count = Math.Min(BatchSize, eventCount - offset);
            lastBatchOffset = offset;
            send(offset, count);
            batches++;
            if (offset + count < eventCount)
            {
                Thread.Sleep(PauseBetweenBatchesMs);
            }
        }

        return batches;
    }

    /// <summary>The guard for a stream nothing can re-target (a pointer move the caller guarded once).</summary>
    public static bool AlwaysTargeted() => true;
}

/// <summary>
/// <c>SendInput</c>, in the owner's session. Text goes in as Unicode code units, so Turkish
/// (ğüşöçıİ) and everything else the owner writes arrives as itself, independent of the
/// keyboard layout; keys go in as virtual keys with the extended flag where Windows expects
/// it; the pointer is placed with <c>SetCursorPos</c> and then pressed. Events are sent
/// through <see cref="InputBatcher"/>, which asks the focus guard before every batch.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class Win32InputSynthesizer : IInputSynthesizer
{
    public void TypeText(string text, Func<bool> stillTargeted)
    {
        var events = new List<OperatorNative.Input>(text.Length * 2);
        // The text index each event belongs to, so a stream stopped at a batch boundary can
        // say how many characters were sent: a batch is 64 events and every character is
        // exactly two, so a boundary never splits a character.
        var characterOfEvent = new List<int>(text.Length * 2);
        for (var i = 0; i < text.Length; i++)
        {
            var ch = text[i];
            var before = events.Count;
            switch (ch)
            {
                case '\r':
                    if (i + 1 < text.Length && text[i + 1] == '\n')
                    {
                        continue;
                    }

                    AddKey(events, 0x0D, false);
                    break;
                case '\n':
                    AddKey(events, 0x0D, false);
                    break;
                case '\t':
                    AddKey(events, 0x09, false);
                    break;
                default:
                    events.Add(Unicode(ch, up: false));
                    events.Add(Unicode(ch, up: true));
                    break;
            }

            for (var e = before; e < events.Count; e++)
            {
                characterOfEvent.Add(i);
            }
        }

        SendInBatches(events, stillTargeted, sent => sent == 0 ? 0 : characterOfEvent[sent - 1] + 1, text.Length);
    }

    public void PressKey(string key, Func<bool> stillTargeted)
    {
        if (!KeyMap.TryKey(key, allowCharacters: true, out var vk, out var extended))
        {
            throw new CapabilityException(ErrorClasses.ValidationError, $"'{key}' is not a key this device knows", retryable: false);
        }

        var events = new List<OperatorNative.Input>(2);
        AddKey(events, vk, extended);
        SendInBatches(events, stillTargeted, _ => 0, 0);
    }

    public void Shortcut(IReadOnlyList<string> keys, Func<bool> stillTargeted)
    {
        KeyMap.ValidateShortcut(keys);
        var modifiers = new List<ushort>();
        ushort plain = 0;
        var extended = false;
        foreach (var key in keys)
        {
            if (KeyMap.TryModifier(key, out var modifier))
            {
                modifiers.Add(modifier);
            }
            else
            {
                KeyMap.TryKey(key, allowCharacters: true, out plain, out extended);
            }
        }

        var events = new List<OperatorNative.Input>();
        foreach (var modifier in modifiers)
        {
            events.Add(VirtualKey(modifier, up: false, extended: false));
        }

        events.Add(VirtualKey(plain, up: false, extended));
        events.Add(VirtualKey(plain, up: true, extended));
        for (var i = modifiers.Count - 1; i >= 0; i--)
        {
            events.Add(VirtualKey(modifiers[i], up: true, extended: false));
        }

        SendInBatches(events, stillTargeted, _ => 0, 0);
    }

    public void MoveTo(int screenX, int screenY)
    {
        if (!OperatorNative.SetCursorPos(screenX, screenY))
        {
            throw new CapabilityException(ErrorClasses.UiStateChanged, $"the pointer could not be placed at ({screenX},{screenY})", retryable: true);
        }

        // A zero-delta move after SetCursorPos: the application sees a real pointer event at
        // the new place, which is what hover-sensitive controls need.
        SendInBatches([Mouse(OperatorNative.MouseEventMove, 0)], InputBatcher.AlwaysTargeted, _ => 0, 0);
    }

    public void Click(int screenX, int screenY, PointerButton button, int clicks)
    {
        MoveTo(screenX, screenY);
        var (down, up) = button == PointerButton.Right
            ? (OperatorNative.MouseEventRightDown, OperatorNative.MouseEventRightUp)
            : (OperatorNative.MouseEventLeftDown, OperatorNative.MouseEventLeftUp);
        var events = new List<OperatorNative.Input>(clicks * 2);
        for (var i = 0; i < clicks; i++)
        {
            events.Add(Mouse(down, 0));
            events.Add(Mouse(up, 0));
        }

        SendInBatches(events, InputBatcher.AlwaysTargeted, _ => 0, 0);
    }

    public void Scroll(int screenX, int screenY, int notches)
    {
        MoveTo(screenX, screenY);
        SendInBatches([Mouse(OperatorNative.MouseEventWheel, unchecked((uint)(notches * OperatorNative.WheelDelta)))], InputBatcher.AlwaysTargeted, _ => 0, 0);
    }

    /// <summary>The zero-delta pointer move <see cref="WindowActions.Activate"/> uses to become "the process with the last input event".</summary>
    public static void NudgePointer() => SendInBatches([Mouse(OperatorNative.MouseEventMove, 0)], InputBatcher.AlwaysTargeted, _ => 0, 0);

    private static void AddKey(List<OperatorNative.Input> events, ushort vk, bool extended)
    {
        events.Add(VirtualKey(vk, up: false, extended));
        events.Add(VirtualKey(vk, up: true, extended));
    }

    private static OperatorNative.Input Unicode(char unit, bool up) => new()
    {
        Type = OperatorNative.InputKeyboard,
        Union = new OperatorNative.InputUnion
        {
            Keyboard = new OperatorNative.KeybdInput
            {
                VirtualKey = 0,
                Scan = unit,
                Flags = OperatorNative.KeyEventUnicode | (up ? OperatorNative.KeyEventKeyUp : 0),
            },
        },
    };

    private static OperatorNative.Input VirtualKey(ushort vk, bool up, bool extended) => new()
    {
        Type = OperatorNative.InputKeyboard,
        Union = new OperatorNative.InputUnion
        {
            Keyboard = new OperatorNative.KeybdInput
            {
                VirtualKey = vk,
                Scan = 0,
                Flags = (up ? OperatorNative.KeyEventKeyUp : 0) | (extended ? OperatorNative.KeyEventExtendedKey : 0),
            },
        },
    };

    private static OperatorNative.Input Mouse(uint flags, uint data) => new()
    {
        Type = OperatorNative.InputMouse,
        Union = new OperatorNative.InputUnion
        {
            Mouse = new OperatorNative.MouseInput { Dx = 0, Dy = 0, MouseData = data, Flags = flags },
        },
    };

    private static void SendInBatches(IReadOnlyList<OperatorNative.Input> events, Func<bool> stillTargeted, Func<int, int> charactersCompletedBy, int totalCharacters)
    {
        var size = Marshal.SizeOf<OperatorNative.Input>();
        InputBatcher.Send(
            events.Count,
            (offset, count) =>
            {
                var batch = new OperatorNative.Input[count];
                for (var i = 0; i < count; i++)
                {
                    batch[i] = events[offset + i];
                }

                var sent = OperatorNative.SendInput((uint)count, batch, size);
                if (sent != count)
                {
                    var error = Marshal.GetLastWin32Error();
                    throw new CapabilityException(
                        ErrorClasses.UiStateChanged,
                        $"SendInput delivered {sent} of {count} events (win32={error}); the input is blocked (a UIPI-protected window, a locked session)",
                        retryable: true);
                }
            },
            stillTargeted,
            charactersCompletedBy,
            totalCharacters);
    }
}
