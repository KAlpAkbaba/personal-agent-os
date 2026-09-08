using System.Reflection;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Text.Json.Nodes;
using System.Windows.Automation;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion.Operator;

/// <summary>
/// How an element is named in a payload (M19_DIGITAL_OPERATOR_SPEC.md §2): by
/// <c>automation_id</c>, by <c>name</c> (optionally with <c>control_type</c>), or by a
/// <c>name_prefix</c> for controls whose names carry a suffix the planner cannot predict.
/// </summary>
public sealed record ElementQuery(string? AutomationId, string? Name, string? ControlType, string? NamePrefix)
{
    public bool IsEmpty => AutomationId is null && Name is null && NamePrefix is null && ControlType is null;

    public bool NamesAnElement => AutomationId is not null || Name is not null || NamePrefix is not null;

    public override string ToString()
    {
        var parts = new List<string>();
        if (AutomationId is not null)
        {
            parts.Add($"automation_id={AutomationId}");
        }

        if (Name is not null)
        {
            parts.Add($"name=\"{Name}\"");
        }

        if (NamePrefix is not null)
        {
            parts.Add($"name_prefix=\"{NamePrefix}\"");
        }

        if (ControlType is not null)
        {
            parts.Add($"control_type={ControlType}");
        }

        return string.Join(' ', parts);
    }
}

/// <summary>
/// The <c>ui.*</c> family over <c>System.Windows.Automation</c> (the <c>UIAutomationClient</c>
/// assembly of the Windows Desktop runtime, referenced directly — the companion stays a console
/// process). Every walk is bounded (depth ≤ <see cref="MaxDepth"/>, nodes ≤
/// <see cref="MaxNodes"/>) so a busy Explorer window cannot turn one inspect into a minute of
/// tree walking; every write (Invoke, Value, SelectionItem) is followed by a read-back the
/// caller reports. A control that says it is a password field never has its value read.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class UiAutomationInspector
{
    public const int DefaultDepth = 3;
    public const int MaxDepth = 5;
    public const int DefaultMaxNodes = 200;
    public const int MaxNodes = 200;

    private static readonly Dictionary<string, ControlType> ControlTypesByName = BuildControlTypes();

    public AutomationElement RootForWindow(IntPtr hwnd)
    {
        try
        {
            return AutomationElement.FromHandle(hwnd);
        }
        catch (Exception ex) when (IsElementGone(ex))
        {
            throw new CapabilityException(ErrorClasses.UiStateChanged, "the window is no longer available to UI Automation", retryable: true);
        }
    }

    /// <summary>The bounded tree at <paramref name="root"/> (or at the element a query names within it).</summary>
    public JsonObject Inspect(AutomationElement root, int depth, int maxNodes, ElementQuery? query)
    {
        depth = Math.Clamp(depth, 1, MaxDepth);
        maxNodes = Math.Clamp(maxNodes, 1, MaxNodes);
        var start = query is { IsEmpty: false } ? Find(root, query) : root;
        var budget = new WalkBudget(maxNodes);
        var tree = Describe(start, includeChildren: true, depth, budget);
        return new JsonObject
        {
            ["root"] = tree,
            ["node_count"] = budget.Used,
            ["truncated"] = budget.Exhausted,
            ["depth"] = depth,
        };
    }

    /// <summary>The first descendant matching a query, or <c>ui_target_not_found</c>.</summary>
    public AutomationElement Find(AutomationElement root, ElementQuery query)
    {
        if (!query.NamesAnElement && query.ControlType is null)
        {
            throw new CapabilityException(ErrorClasses.ValidationError, "an element must be named by automation_id, name or name_prefix (optionally control_type)", retryable: false);
        }

        try
        {
            AutomationElement? found = null;
            if (query.AutomationId is not null)
            {
                found = root.FindFirst(TreeScope.Element | TreeScope.Descendants, WithControlType(new PropertyCondition(AutomationElement.AutomationIdProperty, query.AutomationId), query.ControlType));
            }
            else if (query.Name is not null)
            {
                found = root.FindFirst(TreeScope.Element | TreeScope.Descendants, WithControlType(new PropertyCondition(AutomationElement.NameProperty, query.Name), query.ControlType));
            }
            else if (query.NamePrefix is not null)
            {
                var candidates = root.FindAll(TreeScope.Element | TreeScope.Descendants, WithControlType(Condition.TrueCondition, query.ControlType));
                var scanned = 0;
                foreach (AutomationElement candidate in candidates)
                {
                    if (++scanned > 2000)
                    {
                        break;
                    }

                    var name = SafeName(candidate);
                    if (name.StartsWith(query.NamePrefix, StringComparison.OrdinalIgnoreCase))
                    {
                        found = candidate;
                        break;
                    }
                }
            }
            else
            {
                found = root.FindFirst(TreeScope.Element | TreeScope.Descendants, WithControlType(Condition.TrueCondition, query.ControlType));
            }

            return found ?? throw new CapabilityException(ErrorClasses.UiTargetNotFound, $"no element matches {query} in this window", retryable: true);
        }
        catch (Exception ex) when (IsElementGone(ex))
        {
            throw new CapabilityException(ErrorClasses.UiStateChanged, "the window changed while it was being searched", retryable: true);
        }
    }

    /// <summary>The element as the protocol describes it; children only when asked, within the budget.</summary>
    public JsonObject Describe(AutomationElement element, bool includeChildren = false, int depth = 1, WalkBudget? budget = null)
    {
        budget ??= new WalkBudget(MaxNodes);
        budget.Take();
        // EVERY getter below reaches UI Automation for itself: `element.Current` is only a
        // struct handed back cheaply, and `info.Name`, `info.ControlType` and the rest each
        // make their own call. Guarding the `.Current` access alone therefore guarded nothing
        // (measured: CI runs 34230759954 and 34234146012 both threw out of a getter, not out
        // of `.Current`), so the whole read is one guarded region. An element that went away
        // is reported in the shape every caller here already handles -
        // `OperatorCapabilities.UiInvoke` catches exactly this after an invoke that closed its
        // own dialog - never as a catastrophic COM failure.
        JsonObject node;
        bool isPassword;
        try
        {
            var info = element.Current;
            node = new JsonObject
            {
                ["automation_id"] = info.AutomationId ?? string.Empty,
                ["name"] = info.Name ?? string.Empty,
                ["control_type"] = ControlTypeName(info.ControlType),
                ["class_name"] = info.ClassName ?? string.Empty,
                ["enabled"] = info.IsEnabled,
                ["bounds"] = BoundsOf(info.BoundingRectangle),
            };
            isPassword = info.IsPassword;
        }
        catch (Exception ex) when (IsElementGone(ex))
        {
            throw new ElementNotAvailableException();
        }

        if (isPassword)
        {
            // Never read: the value of a password field is exactly the thing this module
            // must not carry. The key says "masked", not what was masked.
            node["masked"] = true;
        }
        else
        {
            var value = ReadValue(element);
            if (value is not null)
            {
                node["value"] = value;
            }
        }

        var selected = ReadOrGone<bool?>(
            () => element.TryGetCurrentPattern(SelectionItemPattern.Pattern, out var pattern)
                ? ((SelectionItemPattern)pattern).Current.IsSelected
                : null,
            null);
        if (selected is not null)
        {
            node["selected"] = selected.Value;
        }

        var toggleState = ReadOrGone<string?>(
            () => element.TryGetCurrentPattern(TogglePattern.Pattern, out var pattern)
                ? ((TogglePattern)pattern).Current.ToggleState.ToString().ToLowerInvariant()
                : null,
            null);
        if (toggleState is not null)
        {
            node["toggle_state"] = toggleState;
        }

        if (includeChildren)
        {
            var children = new JsonArray();
            if (depth > 1 && !budget.Exhausted)
            {
                var walker = TreeWalker.ControlViewWalker;
                var child = SafeFirstChild(walker, element);
                while (child is not null && !budget.Exhausted)
                {
                    try
                    {
                        children.Add(Describe(child, includeChildren: true, depth - 1, budget));
                    }
                    catch (Exception ex) when (IsElementGone(ex))
                    {
                        // A child that closed while we walked its siblings is simply not in
                        // the description; the rest of the window is still worth reporting.
                    }

                    child = SafeNextSibling(walker, child);
                }
            }

            node["children"] = children;
        }

        return node;
    }

    /// <summary>Value pattern first, Text pattern second, null when the element carries no readable text.</summary>
    public string? ReadValue(AutomationElement element)
    {
        try
        {
            if (element.Current.IsPassword)
            {
                return null;
            }

            if (element.TryGetCurrentPattern(ValuePattern.Pattern, out var valuePattern))
            {
                return ((ValuePattern)valuePattern).Current.Value;
            }

            if (element.TryGetCurrentPattern(TextPattern.Pattern, out var textPattern))
            {
                return ((TextPattern)textPattern).DocumentRange.GetText(-1);
            }
        }
        catch (Exception ex) when (IsElementGone(ex))
        {
            return null;
        }
        catch (InvalidOperationException)
        {
            return null;
        }

        return null;
    }

    /// <summary>Invoke, else Toggle, else SelectionItem, else ExpandCollapse; <c>ui_state_changed</c> when the element supports none.</summary>
    public string Invoke(AutomationElement element)
    {
        try
        {
            if (element.TryGetCurrentPattern(InvokePattern.Pattern, out var invoke))
            {
                ((InvokePattern)invoke).Invoke();
                return "invoke";
            }

            if (element.TryGetCurrentPattern(TogglePattern.Pattern, out var toggle))
            {
                ((TogglePattern)toggle).Toggle();
                return "toggle";
            }

            if (element.TryGetCurrentPattern(SelectionItemPattern.Pattern, out var selectionItem))
            {
                ((SelectionItemPattern)selectionItem).Select();
                return "select";
            }

            if (element.TryGetCurrentPattern(ExpandCollapsePattern.Pattern, out var expand))
            {
                var pattern = (ExpandCollapsePattern)expand;
                if (pattern.Current.ExpandCollapseState == ExpandCollapseState.Collapsed)
                {
                    pattern.Expand();
                }
                else
                {
                    pattern.Collapse();
                }

                return "expand_collapse";
            }
        }
        catch (Exception ex) when (IsElementGone(ex))
        {
            // The invoke closed the window (a dialog button): that is a success from the
            // invoker's point of view; the caller re-observes and says so.
            return "invoke";
        }

        throw new CapabilityException(ErrorClasses.UiStateChanged, "the element supports none of Invoke, Toggle, SelectionItem or ExpandCollapse; use pointer.click on its bounds", retryable: false);
    }

    /// <summary>
    /// A semantic write, then the read-back. The Value pattern when the element has one; else,
    /// for an element that is a native text window (a classic multi-line Edit is a UIA
    /// Document with a Text pattern and no Value pattern; a RichEdit likewise),
    /// <c>WM_SETTEXT</c> to that window — still a message to the control, never synthesised
    /// input, and needing no focus. Read-only and non-text elements are refused with a reason.
    /// </summary>
    public string? SetValue(AutomationElement element, string value)
    {
        if (element.TryGetCurrentPattern(ValuePattern.Pattern, out var pattern))
        {
            var valuePattern = (ValuePattern)pattern;
            if (valuePattern.Current.IsReadOnly)
            {
                throw new CapabilityException(ErrorClasses.UiStateChanged, "the element's value is read-only", retryable: false);
            }

            try
            {
                valuePattern.SetValue(value);
            }
            catch (InvalidOperationException ex)
            {
                throw new CapabilityException(ErrorClasses.UiStateChanged, $"the element refused the value: {ex.Message}", retryable: true);
            }
        }
        else
        {
            var handle = new IntPtr(element.Current.NativeWindowHandle);
            var hasText = element.TryGetCurrentPattern(TextPattern.Pattern, out _);
            if (handle == IntPtr.Zero || !hasText)
            {
                throw new CapabilityException(ErrorClasses.UiStateChanged, "the element supports neither the Value pattern nor a native text window; use keyboard.type after activating it", retryable: false);
            }

            if (!OperatorNative.SetWindowTextMessage(handle, value))
            {
                throw new CapabilityException(ErrorClasses.UiStateChanged, "the text window did not accept WM_SETTEXT", retryable: true);
            }
        }

        // Some controls apply the value asynchronously; give the read-back a moment to agree.
        var deadline = DateTime.UtcNow.AddMilliseconds(500);
        string? observed;
        do
        {
            observed = ReadValue(element);
            if (string.Equals(observed, value, StringComparison.Ordinal))
            {
                break;
            }

            Thread.Sleep(25);
        }
        while (DateTime.UtcNow < deadline);

        return observed;
    }

    /// <summary>Select the child item named <paramref name="item"/> inside a container, and read the selection back.</summary>
    public IReadOnlyList<string> Select(AutomationElement container, string item)
    {
        var target = container.FindFirst(TreeScope.Descendants, new PropertyCondition(AutomationElement.NameProperty, item))
                     ?? throw new CapabilityException(ErrorClasses.UiTargetNotFound, $"no item named \"{item}\" in the container", retryable: true);
        if (!target.TryGetCurrentPattern(SelectionItemPattern.Pattern, out var pattern))
        {
            throw new CapabilityException(ErrorClasses.UiStateChanged, $"item \"{item}\" is not selectable", retryable: false);
        }

        ((SelectionItemPattern)pattern).Select();
        Thread.Sleep(50);
        return SelectedNames(container);
    }

    /// <summary>The names of the selected items in a container (Selection pattern on it, or SelectionItem on its descendants).</summary>
    public IReadOnlyList<string> SelectedNames(AutomationElement container)
    {
        try
        {
            if (container.TryGetCurrentPattern(SelectionPattern.Pattern, out var selection))
            {
                return [.. ((SelectionPattern)selection).Current.GetSelection().Select(SafeName)];
            }

            var selected = container.FindAll(
                TreeScope.Descendants,
                new PropertyCondition(SelectionItemPattern.IsSelectedProperty, true));
            var names = new List<string>();
            foreach (AutomationElement element in selected)
            {
                names.Add(SafeName(element));
            }

            return names;
        }
        catch (Exception ex) when (IsElementGone(ex))
        {
            return [];
        }
    }

    /// <summary>
    /// A dialog, described for the planner: its title, its buttons and its static texts —
    /// enough to choose "Don't save" or read "Do you want to save changes?" without a
    /// second round trip.
    /// </summary>
    public JsonObject DescribeDialog(IntPtr hwnd)
    {
        var root = RootForWindow(hwnd);
        var buttons = new JsonArray();
        var texts = new JsonArray();
        try
        {
            foreach (AutomationElement button in root.FindAll(TreeScope.Descendants, new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Button)))
            {
                if (buttons.Count >= 20)
                {
                    break;
                }

                buttons.Add(Describe(button));
            }

            foreach (AutomationElement text in root.FindAll(TreeScope.Descendants, new PropertyCondition(AutomationElement.ControlTypeProperty, ControlType.Text)))
            {
                if (texts.Count >= 10)
                {
                    break;
                }

                texts.Add(SafeName(text));
            }
        }
        catch (Exception ex) when (IsElementGone(ex))
        {
            // Dialog closed under us; report what was read.
        }

        return new JsonObject
        {
            ["title"] = SafeName(root),
            ["buttons"] = buttons,
            ["texts"] = texts,
        };
    }

    public static string ControlTypeName(ControlType? type)
    {
        if (type is null)
        {
            return string.Empty;
        }

        var name = type.ProgrammaticName;
        return name.StartsWith("ControlType.", StringComparison.Ordinal) ? name["ControlType.".Length..] : name;
    }

    /// <summary>The <see cref="ControlType"/> behind a payload name such as <c>Button</c> or <c>Document</c>.</summary>
    public static ControlType ParseControlType(string name)
        => ControlTypesByName.TryGetValue(name, out var type)
            ? type
            : throw new CapabilityException(
                ErrorClasses.ValidationError,
                $"'{name}' is not a UI Automation control type ({string.Join(",", ControlTypesByName.Keys.OrderBy(k => k, StringComparer.Ordinal))})",
                retryable: false);

    private static Condition WithControlType(Condition condition, string? controlType)
        => controlType is null
            ? condition
            : new AndCondition(condition, new PropertyCondition(AutomationElement.ControlTypeProperty, ParseControlType(controlType)));

    private static JsonNode? BoundsOf(System.Windows.Rect rect)
        => rect.IsEmpty
            ? null
            : new JsonObject
            {
                ["x"] = (int)rect.X,
                ["y"] = (int)rect.Y,
                ["width"] = (int)rect.Width,
                ["height"] = (int)rect.Height,
            };

    private static string SafeName(AutomationElement element)
    {
        try
        {
            return element.Current.Name ?? string.Empty;
        }
        catch (Exception ex) when (IsElementGone(ex))
        {
            return string.Empty;
        }
    }

    /// <summary>HRESULTs UI Automation raises when the element (or the process behind it)
    /// has gone away between one call and the next. Kept narrow ON PURPOSE: an unrelated COM
    /// failure must still surface, because swallowing it would hide a real defect.</summary>
    private const int UiaElementNotAvailable = unchecked((int)0x80040201);
    private const int Unexpected = unchecked((int)0x8000FFFF);         // E_UNEXPECTED
    private const int RpcDisconnected = unchecked((int)0x80010108);    // RPC_E_DISCONNECTED
    private const int RpcServerUnavailable = unchecked((int)0x800706BA);

    /// <summary>
    /// Did this element just go away? UI Automation says so in two shapes - the typed
    /// <see cref="ElementNotAvailableException"/>, and a raw <see cref="COMException"/>
    /// carrying one of the HRESULTs above. The runner met the second shape on 2026-09-08
    /// while a save dialog was being dismissed, and a capability that already expected the
    /// first failed catastrophically on it. One predicate, both shapes.
    /// </summary>
    public static bool IsElementGone(Exception exception) => exception switch
    {
        ElementNotAvailableException => true,
        COMException com => com.HResult is UiaElementNotAvailable
            or Unexpected
            or RpcDisconnected
            or RpcServerUnavailable,
        _ => false,
    };

    /// <summary>Reads one property of a live element, answering <paramref name="fallback"/>
    /// when the element vanished mid-read rather than failing the whole description.</summary>
    private static T ReadOrGone<T>(Func<T> read, T fallback)
    {
        try
        {
            return read();
        }
        catch (Exception ex) when (IsElementGone(ex))
        {
            return fallback;
        }
    }

    private static AutomationElement? SafeFirstChild(TreeWalker walker, AutomationElement element)
    {
        try
        {
            return walker.GetFirstChild(element);
        }
        catch (Exception ex) when (IsElementGone(ex))
        {
            return null;
        }
    }

    private static AutomationElement? SafeNextSibling(TreeWalker walker, AutomationElement element)
    {
        try
        {
            return walker.GetNextSibling(element);
        }
        catch (Exception ex) when (IsElementGone(ex))
        {
            return null;
        }
    }

    private static Dictionary<string, ControlType> BuildControlTypes()
    {
        var map = new Dictionary<string, ControlType>(StringComparer.OrdinalIgnoreCase);
        foreach (var field in typeof(ControlType).GetFields(BindingFlags.Public | BindingFlags.Static))
        {
            if (field.FieldType == typeof(ControlType) && field.GetValue(null) is ControlType type)
            {
                map[field.Name] = type;
            }
        }

        return map;
    }

    /// <summary>A node budget shared by one walk, so the cap is on the whole tree and not per level.</summary>
    public sealed class WalkBudget(int max)
    {
        public int Used { get; private set; }

        public bool Exhausted => Used >= max;

        public void Take() => Used++;
    }
}
