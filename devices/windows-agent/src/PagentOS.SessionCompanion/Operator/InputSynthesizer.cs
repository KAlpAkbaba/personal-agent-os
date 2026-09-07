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
/// verified the target (invariant 2); the interface itself knows nothing about windows.
/// </summary>
public interface IInputSynthesizer
{
    /// <summary>Unicode text, one <c>KEYEVENTF_UNICODE</c> pair per UTF-16 code unit; newlines and tabs as their keys.</summary>
    void TypeText(string text);

    /// <summary>One named key (see <see cref="KeyMap"/>), pressed and released.</summary>
    void PressKey(string key);

    /// <summary>Modifiers down in order, the key pressed and released, modifiers up in reverse.</summary>
    void Shortcut(IReadOnlyList<string> keys);

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

    public static void ValidateKey(string key)
    {
        if (!TryKey(key, allowCharacters: false, out _, out _))
        {
            throw new CapabilityException(
                ErrorClasses.ValidationError,
                $"'{key}' is not a key keyboard.key accepts ({string.Join(",", KeyNames)}); use keyboard.type for text",
                retryable: false);
        }
    }
}

/// <summary>
/// <c>SendInput</c>, in the owner's session. Text goes in as Unicode code units, so Turkish
/// (ğüşöçıİ) and everything else the owner writes arrives as itself, independent of the
/// keyboard layout; keys go in as virtual keys with the extended flag where Windows expects
/// it; the pointer is placed with <c>SetCursorPos</c> and then pressed. Events are sent in
/// small batches — applications drop very large batches, and a dropped batch would be a
/// silent truncation of what the owner asked to type.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class Win32InputSynthesizer : IInputSynthesizer
{
    private const int BatchSize = 64;

    public void TypeText(string text)
    {
        var events = new List<OperatorNative.Input>(text.Length * 2);
        for (var i = 0; i < text.Length; i++)
        {
            var ch = text[i];
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
        }

        SendInBatches(events);
    }

    public void PressKey(string key)
    {
        if (!KeyMap.TryKey(key, allowCharacters: false, out var vk, out var extended))
        {
            throw new CapabilityException(ErrorClasses.ValidationError, $"'{key}' is not a key this device knows", retryable: false);
        }

        var events = new List<OperatorNative.Input>(2);
        AddKey(events, vk, extended);
        SendInBatches(events);
    }

    public void Shortcut(IReadOnlyList<string> keys)
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

        SendInBatches(events);
    }

    public void MoveTo(int screenX, int screenY)
    {
        if (!OperatorNative.SetCursorPos(screenX, screenY))
        {
            throw new CapabilityException(ErrorClasses.UiStateChanged, $"the pointer could not be placed at ({screenX},{screenY})", retryable: true);
        }

        // A zero-delta move after SetCursorPos: the application sees a real pointer event at
        // the new place, which is what hover-sensitive controls need.
        SendInBatches([Mouse(OperatorNative.MouseEventMove, 0)]);
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

        SendInBatches(events);
    }

    public void Scroll(int screenX, int screenY, int notches)
    {
        MoveTo(screenX, screenY);
        SendInBatches([Mouse(OperatorNative.MouseEventWheel, unchecked((uint)(notches * OperatorNative.WheelDelta)))]);
    }

    /// <summary>The zero-delta pointer move <see cref="WindowActions.Activate"/> uses to become "the process with the last input event".</summary>
    public static void NudgePointer() => SendInBatches([Mouse(OperatorNative.MouseEventMove, 0)]);

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

    private static void SendInBatches(IReadOnlyList<OperatorNative.Input> events)
    {
        var size = Marshal.SizeOf<OperatorNative.Input>();
        for (var offset = 0; offset < events.Count; offset += BatchSize)
        {
            var count = Math.Min(BatchSize, events.Count - offset);
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

            if (offset + count < events.Count)
            {
                Thread.Sleep(5);
            }
        }
    }
}
