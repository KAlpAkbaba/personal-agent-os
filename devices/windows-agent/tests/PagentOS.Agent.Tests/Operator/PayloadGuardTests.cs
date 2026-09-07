using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// Payload validation (M19_DIGITAL_OPERATOR_SPEC.md §1/§2): a secret is never typed, an
/// oversize text is refused, coordinates outside the window are refused, an unknown key name
/// is refused — every one BEFORE any event exists, proven by a recording synthesizer that saw
/// nothing rather than by a window that stayed empty.
/// </summary>
[Collection(OperatorLabCollection.Name)]
public sealed class PayloadGuardTests : IDisposable
{
    private readonly RecordingInput _input = new();
    private readonly OperatorLab _lab;

    public PayloadGuardTests()
    {
        _lab = new OperatorLab(_input);
    }

    public void Dispose() => _lab.Dispose();

    [Fact]
    public void A_payload_flagged_secret_is_refused_with_the_documented_sentence_and_nothing_is_sent()
    {
        var ex = _lab.ExpectFailure(OperatorCapabilityNames.KeyboardType, new JsonObject { ["window_id"] = "w-1-1", ["text"] = "hunter2", ["secret"] = true });
        Assert.Equal(ErrorClasses.ValidationError, ex.ErrorClass);
        Assert.Equal("secrets are never typed", ex.Message);
        Assert.False(ex.Retryable);

        var setValue = _lab.ExpectFailure(OperatorCapabilityNames.UiSetValue, new JsonObject { ["window_id"] = "w-1-1", ["automation_id"] = "15", ["value"] = "hunter2", ["secret"] = true });
        Assert.Equal("secrets are never typed", setValue.Message);
        Assert.Empty(_input.Calls);
    }

    [Fact]
    public void Oversize_text_control_characters_and_missing_fields_are_validation_errors()
    {
        var oversize = _lab.ExpectFailure(OperatorCapabilityNames.KeyboardType, new JsonObject { ["window_id"] = "w-1-1", ["text"] = new string('a', OperatorCapabilityNames.MaxTypedChars + 1) });
        Assert.Equal(ErrorClasses.ValidationError, oversize.ErrorClass);
        Assert.Contains("4096", oversize.Message, StringComparison.Ordinal);

        var control = _lab.ExpectFailure(OperatorCapabilityNames.KeyboardType, new JsonObject { ["window_id"] = "w-1-1", ["text"] = "ab" });
        Assert.Equal(ErrorClasses.ValidationError, control.ErrorClass);

        var missing = _lab.ExpectFailure(OperatorCapabilityNames.KeyboardType, new JsonObject { ["window_id"] = "w-1-1" });
        Assert.Equal(ErrorClasses.ValidationError, missing.ErrorClass);

        var badId = _lab.ExpectFailure(OperatorCapabilityNames.WindowActivate, new JsonObject { ["window_id"] = "not-an-id" });
        Assert.Equal(ErrorClasses.ValidationError, badId.ErrorClass);

        var unknownId = _lab.ExpectFailure(OperatorCapabilityNames.WindowActivate, new JsonObject { ["window_id"] = "w-12345-1" });
        Assert.Equal(ErrorClasses.UiTargetNotFound, unknownId.ErrorClass);
        Assert.Empty(_input.Calls);
    }

    [Fact]
    public void Unknown_keys_and_malformed_shortcuts_are_refused_before_the_window_is_resolved()
    {
        var key = _lab.ExpectFailure(OperatorCapabilityNames.KeyboardKey, new JsonObject { ["window_id"] = "w-1-1", ["key"] = "sleep" });
        Assert.Equal(ErrorClasses.ValidationError, key.ErrorClass);
        Assert.Contains("escape", key.Message, StringComparison.Ordinal);

        var noModifier = _lab.ExpectFailure(OperatorCapabilityNames.KeyboardShortcut, new JsonObject { ["window_id"] = "w-1-1", ["keys"] = new JsonArray("a", "b") });
        Assert.Equal(ErrorClasses.ValidationError, noModifier.ErrorClass);

        var win = _lab.ExpectFailure(OperatorCapabilityNames.KeyboardShortcut, new JsonObject { ["window_id"] = "w-1-1", ["keys"] = new JsonArray("win", "r") });
        Assert.Equal(ErrorClasses.ValidationError, win.ErrorClass);

        var tooMany = _lab.ExpectFailure(OperatorCapabilityNames.KeyboardShortcut, new JsonObject { ["window_id"] = "w-1-1", ["keys"] = new JsonArray("ctrl", "alt", "shift", "a", "b") });
        Assert.Equal(ErrorClasses.ValidationError, tooMany.ErrorClass);
        Assert.Empty(_input.Calls);
    }

    [Fact]
    public void Applications_outside_the_allowlist_and_composed_terminal_commands_are_permission_denied()
    {
        var app = _lab.ExpectFailure(OperatorCapabilityNames.AppLaunch, new JsonObject { ["application"] = "regedit" });
        Assert.Equal(ErrorClasses.PermissionDenied, app.ErrorClass);

        var path = _lab.ExpectFailure(OperatorCapabilityNames.AppLaunch, new JsonObject { ["application"] = Path.Combine(Path.GetTempPath(), "x.exe") });
        Assert.Equal(ErrorClasses.PermissionDenied, path.ErrorClass);

        var composed = _lab.ExpectFailure(OperatorCapabilityNames.TerminalExecute, new JsonObject { ["command"] = "hostname; Remove-Item x" });
        Assert.Equal(ErrorClasses.PermissionDenied, composed.ErrorClass);

        var shell = _lab.ExpectFailure(OperatorCapabilityNames.TerminalOpen, new JsonObject { ["shell"] = "cmd" });
        Assert.Equal(ErrorClasses.ValidationError, shell.ErrorClass);

        var unknown = Assert.Throws<CapabilityException>(() => _lab.Exec("desktop.reboot", new JsonObject()));
        Assert.Equal(ErrorClasses.CapabilityMissing, unknown.ErrorClass);
        Assert.Empty(_lab.Operator.StartedPids);
        Assert.Equal(0, _lab.Operator.Terminal.ProcessesStarted);
    }

    [LabFact]
    public void Coordinates_outside_the_window_are_refused_and_no_pointer_event_is_sent()
    {
        var (_, windowId, window) = _lab.LaunchNotepad();
        _lab.Activate(windowId);
        var width = window["rect"]!["width"]!.GetValue<int>();
        var height = window["rect"]!["height"]!.GetValue<int>();

        var outside = _lab.ExpectFailure(OperatorCapabilityNames.PointerClick, new JsonObject { ["window_id"] = windowId, ["x"] = width + 100, ["y"] = 10 });
        Assert.Equal(ErrorClasses.ValidationError, outside.ErrorClass);
        var negative = _lab.ExpectFailure(OperatorCapabilityNames.PointerMove, new JsonObject { ["window_id"] = windowId, ["x"] = -1, ["y"] = 10 });
        Assert.Equal(ErrorClasses.ValidationError, negative.ErrorClass);
        var below = _lab.ExpectFailure(OperatorCapabilityNames.PointerScroll, new JsonObject { ["window_id"] = windowId, ["x"] = 10, ["y"] = height + 5, ["delta"] = 3 });
        Assert.Equal(ErrorClasses.ValidationError, below.ErrorClass);
        var farScreen = _lab.ExpectFailure(OperatorCapabilityNames.PointerClick, new JsonObject { ["window_id"] = windowId, ["x"] = 100_000, ["y"] = 10, ["space"] = "screen" });
        Assert.Equal(ErrorClasses.ValidationError, farScreen.ErrorClass);
        var zeroScroll = _lab.ExpectFailure(OperatorCapabilityNames.PointerScroll, new JsonObject { ["window_id"] = windowId, ["x"] = 10, ["y"] = 10, ["delta"] = 0 });
        Assert.Equal(ErrorClasses.ValidationError, zeroScroll.ErrorClass);
        Assert.Empty(_input.Calls);

        // Inside the window the recorder sees exactly one click at the translated position.
        var inside = _lab.Exec(OperatorCapabilityNames.PointerClick, new JsonObject { ["window_id"] = windowId, ["x"] = 20, ["y"] = 20 });
        Assert.Single(_input.Calls);
        Assert.Equal($"click {inside["screen_x"]},{inside["screen_y"]} Left x1", _input.Calls[0]);
    }

    private sealed class RecordingInput : IInputSynthesizer
    {
        public List<string> Calls { get; } = new();

        public void TypeText(string text) => Calls.Add($"type {text}");

        public void PressKey(string key) => Calls.Add($"key {key}");

        public void Shortcut(IReadOnlyList<string> keys) => Calls.Add($"shortcut {string.Join('+', keys)}");

        public void MoveTo(int screenX, int screenY) => Calls.Add($"move {screenX},{screenY}");

        public void Click(int screenX, int screenY, PointerButton button, int clicks) => Calls.Add($"click {screenX},{screenY} {button} x{clicks}");

        public void Scroll(int screenX, int screenY, int notches) => Calls.Add($"scroll {screenX},{screenY} {notches}");
    }
}
