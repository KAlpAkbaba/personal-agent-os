using System.Text.Json.Nodes;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Fakes;
using PagentOS.Companion.Audio.Timing;
using PagentOS.DeviceService;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>
/// M18's two new interactive capabilities: <c>desktop.alarm_start</c> / <c>desktop.alarm_stop</c>
/// (DEVICE_PROTOCOL.md §6c) and <c>desktop.display_off</c> (§6d).
///
/// <para>Nothing here plays a sound or turns a monitor off. The audio boundary is
/// <see cref="IAudioDeviceFactory"/>, so the alarm's real ramp runs into a
/// <see cref="FakePlayback"/> whose bytes are then read back; the display boundary is
/// <see cref="IDisplayPower"/>, so the broadcast is counted rather than sent. The properties
/// that matter — "never a sudden full volume", "always stoppable", "never the system volume",
/// "off and nothing else" — are asserted on data, not inferred from a speaker.</para>
/// </summary>
public sealed class AmbientCapabilityTests
{
    // ------------------------------------------------------------------ the ramp itself

    [Fact]
    public void The_ramp_starts_low_climbs_monotonically_and_holds_at_the_end_level()
    {
        var ramp = WakeRamp.Create(0.05, 0.8, 60);

        Assert.Equal(0.05, ramp.LevelAt(TimeSpan.Zero), 6);
        Assert.Equal(0.8, ramp.LevelAt(TimeSpan.FromSeconds(60)), 6);
        Assert.Equal(0.8, ramp.LevelAt(TimeSpan.FromHours(3)), 6);
        Assert.Equal(0.425, ramp.LevelAt(TimeSpan.FromSeconds(30)), 6);

        // A clock that ran backwards must not produce a level below the start, and it must
        // certainly not produce a jump: the alarm's floor is its start, always.
        Assert.Equal(0.05, ramp.LevelAt(TimeSpan.FromSeconds(-10)), 6);

        var previous = -1.0;
        for (var second = 0; second <= 120; second++)
        {
            var level = ramp.LevelAt(TimeSpan.FromSeconds(second));
            Assert.True(level >= previous, $"level fell at t={second}s");
            Assert.True(level <= WakeRamp.MaxVolume, $"level exceeded the ceiling at t={second}s");
            previous = level;
        }
    }

    [Fact]
    public void A_start_volume_at_or_above_the_jolt_threshold_is_refused_not_quietly_lowered()
    {
        // The whole point of the rule: a descriptor asking to begin at 0.9 is malformed, and
        // turning it into a polite ramp would hide that from whoever wrote it.
        var ex = Assert.Throws<CapabilityException>(() => WakeRamp.Create(0.9, 0.95, 60));
        Assert.Equal(ErrorClasses.ValidationError, ex.ErrorClass);
        Assert.False(ex.Retryable);

        Assert.Throws<CapabilityException>(() => WakeRamp.Create(WakeRamp.MaxStartVolume, 0.8, 60));
        Assert.Throws<CapabilityException>(() => WakeRamp.Create(0.6, 0.2, 60));
        Assert.Throws<CapabilityException>(() => WakeRamp.Create(0.05, 0.8, 0));
        Assert.Throws<CapabilityException>(() => WakeRamp.Create(-0.1, 0.8, 60));
        Assert.Throws<CapabilityException>(() => WakeRamp.Create(0.05, 1.4, 60));
    }

    [Fact]
    public void An_end_above_the_ceiling_and_a_ramp_below_the_floor_are_clamped_and_the_clamp_is_reported()
    {
        var ramp = WakeRamp.Create(0.05, 1.0, 1);

        Assert.Equal(WakeRamp.MaxVolume, ramp.End, 6);
        Assert.True(ramp.EndClamped);
        Assert.Equal(WakeRamp.MinRampSeconds, ramp.RampSeconds);
        Assert.True(ramp.RampClamped);

        var untouched = WakeRamp.Create(0.05, 0.8, 60);
        Assert.False(untouched.EndClamped);
        Assert.False(untouched.RampClamped);
    }

    [Fact]
    public void The_generated_samples_never_exceed_the_level_and_a_low_level_is_genuinely_quiet()
    {
        var format = WakeTone.Format;

        // A whole pulse's worth, so the assertion sees the tone and not just a fade edge.
        var loud = WakeTone.Render(format, 100, 400, 0.8);
        var quiet = WakeTone.Render(format, 100, 400, 0.05);

        Assert.True(WakeTone.PeakLevel(loud) <= 0.8 + 1e-3);
        Assert.True(WakeTone.PeakLevel(loud) > 0.5, "the loud end of the ramp should actually be loud");
        Assert.True(WakeTone.PeakLevel(quiet) <= 0.05 + 1e-3);

        // Even asked for full scale, the generator clamps to the ramp ceiling: the two
        // guards would have to fail together for a jolt to reach the endpoint.
        var overdriven = WakeTone.Render(format, 100, 400, 1.0);
        Assert.True(WakeTone.PeakLevel(overdriven) <= WakeRamp.MaxVolume + 1e-3);
    }

    // ------------------------------------------------------------------ the controller

    [Fact]
    public void Starting_an_alarm_opens_a_render_endpoint_and_reports_the_ramp_it_will_follow()
    {
        var (controller, factory, _) = NewAlarm();
        using var _controller = controller;

        var result = controller.Start(new JsonObject
        {
            ["alarm_id"] = "firing-1",
            ["label"] = "Sabah alarmı",
            ["wake_volume"] = new JsonObject { ["start"] = 0.05, ["end"] = 0.8, ["ramp_seconds"] = 60 },
            ["max_duration_s"] = 120,
        });

        Assert.True(result["started"]!.GetValue<bool>());
        Assert.Equal("firing-1", result["alarm_id"]!.GetValue<string>());
        Assert.Equal(0.05, result["start_volume"]!.GetValue<double>(), 6);
        Assert.Equal(0.8, result["end_volume"]!.GetValue<double>(), 6);
        Assert.Equal(60, result["ramp_seconds"]!.GetValue<int>());
        Assert.Equal(120, result["max_duration_s"]!.GetValue<int>());
        Assert.Null(result["replaced_alarm_id"]);

        Assert.Equal("firing-1", controller.RingingAlarmId);
        var playback = Assert.Single(factory.Playbacks);
        Assert.True(playback.Started);
        Assert.True(playback.EnqueuedBytes > 0, "the alarm should be audible immediately, not one pump later");
    }

    [Fact]
    public void The_first_audio_the_owner_hears_is_at_the_ramps_start_level_not_at_its_end()
    {
        var clock = new ManualTimeProvider();
        var factory = new RecordingDeviceFactory(clock);
        using var controller = new AlarmController(
            factory,
            () => "ren-laptop",
            NullLogger.Instance,
            clock,
            autoPump: false,
            chunkMs: 200);

        controller.Start(new JsonObject
        {
            ["alarm_id"] = "a",
            ["wake_volume"] = new JsonObject { ["start"] = 0.05, ["end"] = 0.8, ["ramp_seconds"] = 60 },
        });

        var playback = factory.Recorded.Single();
        var opening = PeakOver(playback, from: 0);
        Assert.True(opening <= 0.05 + 1e-3, "the alarm opened above its own start volume");
        Assert.True(opening > 0.0, "the alarm opened silent");

        // Halfway through the ramp the sound is louder, and at the end it is at the level the
        // caller asked for — never above it. A full pulse cycle is pumped at each point,
        // because the chime is pulsed: a single 200 ms chunk can legitimately land in the
        // quiet half and say nothing about the ramp.
        clock.Advance(TimeSpan.FromSeconds(30));
        var midwayFrom = playback.Chunks.Count;
        PumpOneCycle(controller);
        var midway = PeakOver(playback, midwayFrom);

        clock.Advance(TimeSpan.FromSeconds(30));
        var topFrom = playback.Chunks.Count;
        PumpOneCycle(controller);
        var top = PeakOver(playback, topFrom);

        Assert.True(midway > 0.05, $"the ramp did not climb (midway peak {midway:0.###})");
        Assert.True(top > midway, $"the ramp did not reach its end level (top {top:0.###}, midway {midway:0.###})");
        Assert.True(top <= 0.8 + 1e-3);
    }

    [Fact]
    public void An_alarm_stops_when_told_to_and_stopping_it_twice_is_still_a_success()
    {
        var (controller, factory, _) = NewAlarm();
        using var _controller = controller;

        controller.Start(new JsonObject { ["alarm_id"] = "a" });
        var playback = Assert.Single(factory.Playbacks);

        var stopped = controller.Stop(new JsonObject { ["alarm_id"] = "a" });
        Assert.True(stopped["stopped"]!.GetValue<bool>());
        Assert.True(stopped["was_ringing"]!.GetValue<bool>());
        Assert.Null(controller.RingingAlarmId);
        Assert.Single(playback.StopCalls);
        Assert.True(playback.Disposed);

        // The owner said "stop" and it is stopped: a second stop is not an error to report.
        var again = controller.Stop(new JsonObject { ["alarm_id"] = "a" });
        Assert.True(again["stopped"]!.GetValue<bool>());
        Assert.False(again["was_ringing"]!.GetValue<bool>());

        // ...and a stop with no id at all stops whatever is ringing.
        controller.Start(new JsonObject { ["alarm_id"] = "b" });
        var anonymous = controller.Stop([]);
        Assert.True(anonymous["stopped"]!.GetValue<bool>());
        Assert.Equal("b", anonymous["alarm_id"]!.GetValue<string>());
    }

    [Fact]
    public void A_stop_naming_a_different_alarm_leaves_the_ringing_one_alone()
    {
        var (controller, _, _) = NewAlarm();
        using var _controller = controller;

        controller.Start(new JsonObject { ["alarm_id"] = "current" });
        var result = controller.Stop(new JsonObject { ["alarm_id"] = "yesterdays-retry" });

        Assert.False(result["stopped"]!.GetValue<bool>());
        Assert.False(result["was_ringing"]!.GetValue<bool>());
        Assert.Equal("current", controller.RingingAlarmId);
    }

    [Fact]
    public void A_second_alarm_replaces_the_first_rather_than_layering_two_ramps()
    {
        var (controller, factory, _) = NewAlarm();
        using var _controller = controller;

        controller.Start(new JsonObject { ["alarm_id"] = "first" });
        var second = controller.Start(new JsonObject { ["alarm_id"] = "second" });

        Assert.Equal("first", second["replaced_alarm_id"]!.GetValue<string>());
        Assert.Equal("second", controller.RingingAlarmId);
        Assert.Equal(2, factory.Playbacks.Count);
        Assert.True(factory.Playbacks[0].Disposed);
        Assert.False(factory.Playbacks[1].Disposed);
    }

    [Fact]
    public void An_alarm_nobody_stops_stops_itself_at_its_max_duration()
    {
        var clock = new ManualTimeProvider();
        var factory = new FakeDeviceFactory(clock);
        using var controller = new AlarmController(
            factory, () => "ren-laptop", NullLogger.Instance, clock, autoPump: false);

        controller.Start(new JsonObject { ["alarm_id"] = "a", ["max_duration_s"] = 60 });
        Assert.True(controller.IsRinging);

        clock.Advance(TimeSpan.FromSeconds(59));
        Assert.True(controller.Pump());
        Assert.True(controller.IsRinging);

        clock.Advance(TimeSpan.FromSeconds(2));
        Assert.False(controller.Pump());
        Assert.Null(controller.RingingAlarmId);
        Assert.True(factory.Playbacks.Single().Disposed);
    }

    [Fact]
    public void Max_duration_is_clamped_into_a_sane_window()
    {
        var (controller, _, _) = NewAlarm();
        using var _controller = controller;

        Assert.Equal(
            AlarmController.MinMaxDurationSeconds,
            controller.Start(new JsonObject { ["alarm_id"] = "a", ["max_duration_s"] = 1 })["max_duration_s"]!.GetValue<int>());
        Assert.Equal(
            AlarmController.MaxMaxDurationSeconds,
            controller.Start(new JsonObject { ["alarm_id"] = "b", ["max_duration_s"] = 999999 })["max_duration_s"]!.GetValue<int>());
        Assert.Equal(
            AlarmController.DefaultMaxDurationSeconds,
            controller.Start(new JsonObject { ["alarm_id"] = "c" })["max_duration_s"]!.GetValue<int>());
    }

    [Fact]
    public void With_no_render_endpoint_the_alarm_says_dependency_unavailable_and_retryable()
    {
        var clock = new ManualTimeProvider();
        using var controller = new AlarmController(
            new FakeDeviceFactory(clock), () => null, NullLogger.Instance, clock, autoPump: false);

        var ex = Assert.Throws<CapabilityException>(() => controller.Start([]));

        // Retryable on purpose: "the headset is not plugged in yet" is a fact about now, not
        // a statement that this device cannot ever ring.
        Assert.Equal(ErrorClasses.DependencyUnavailable, ex.ErrorClass);
        Assert.True(ex.Retryable);
    }

    [Fact]
    public void Disposing_the_controller_stops_a_ringing_alarm()
    {
        var (controller, factory, _) = NewAlarm();
        controller.Start(new JsonObject { ["alarm_id"] = "a" });

        controller.Dispose();

        Assert.Null(controller.RingingAlarmId);
        Assert.True(factory.Playbacks.Single().Disposed);
    }

    [Fact]
    public void A_malformed_wake_volume_is_a_validation_error_not_a_default_ramp()
    {
        var (controller, _, _) = NewAlarm();
        using var _controller = controller;

        Assert.Equal(
            ErrorClasses.ValidationError,
            Assert.Throws<CapabilityException>(
                () => controller.Start(new JsonObject { ["wake_volume"] = "loud" })).ErrorClass);
        Assert.Equal(
            ErrorClasses.ValidationError,
            Assert.Throws<CapabilityException>(
                () => controller.Start(new JsonObject
                {
                    ["wake_volume"] = new JsonObject { ["ramp_seconds"] = 12.5 },
                })).ErrorClass);
    }

    // ------------------------------------------------------------------ display power

    [Fact]
    public void Display_off_is_refused_until_the_owner_has_enabled_it_on_this_companion()
    {
        var display = new RecordingDisplayPower();
        var controller = new DisplayPowerController(display, NullLogger.Instance, enabled: false);

        var ex = Assert.Throws<CapabilityException>(() => controller.TurnOff([]));

        Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Equal(0, display.Calls);
    }

    [Fact]
    public void Display_off_when_enabled_turns_the_display_off_exactly_once_and_says_how()
    {
        var display = new RecordingDisplayPower();
        var controller = new DisplayPowerController(display, NullLogger.Instance, enabled: true);

        var result = controller.TurnOff(new JsonObject { ["reason"] = "likely_asleep" });

        Assert.True(result["display_off"]!.GetValue<bool>());
        Assert.Equal("wm_syscommand_monitorpower", result["method"]!.GetValue<string>());
        Assert.Equal(1, display.Calls);
    }

    [Fact]
    public void A_display_that_refuses_the_request_is_a_failure_not_a_reported_success()
    {
        var controller = new DisplayPowerController(
            new ThrowingDisplayPower(), NullLogger.Instance, enabled: true);

        var ex = Assert.Throws<CapabilityException>(() => controller.TurnOff([]));

        Assert.Equal(ErrorClasses.DependencyUnavailable, ex.ErrorClass);
        Assert.True(ex.Retryable);
    }

    [Fact]
    public void The_display_capability_can_only_turn_a_display_off_never_suspend_the_machine()
    {
        // DISPLAY OFF IS NOT SYSTEM SLEEP. That sentence is the whole design of this family
        // (M18_HOLOGRAPHIC_CORE_SPEC.md §4, M18_THREAT_MODEL.md §5): a dark screen is undone by
        // moving a mouse, and a machine that was stopped, restarted or sent to sleep mid-research
        // is not. So this reads the SOURCE of every display-related file — a glob, so a file
        // added tomorrow is guarded tomorrow — and fails if any of them so much as names an API
        // that could end a session, change the machine's power state, or synthesise a key.
        var files = Support.CompanionSources.DisplayFiles();
        Assert.True(files.Count >= 4, $"expected the display family to be several files, found {files.Count}");

        string[] forbidden =
        [
            // Ending a session or the machine.
            "ExitWindowsEx", "InitiateSystemShutdown", "InitiateShutdown", "SetSuspendState",
            "PowrProf", "powrprof", "shutdown.exe", "EWX_", "SHTDN_",
            // Locking the workstation. Capital "Lock" catches LockWorkStation and any Lock*
            // helper; the lower-case `lock` KEYWORD is deliberately not forbidden, because
            // banning it would fail on ordinary mutual exclusion and the fix for that failure
            // would be to weaken this list.
            "LockWorkStation", "LockWorkstation", "Lock",
            // Turning a monitor back on through the power broadcast (unreliable, and not what
            // display_wake does), and reacting to the system's own power transitions.
            "SC_MONITORPOWER_ON", "MonitorOn", "WM_POWERBROADCAST",
            // A CONTINUOUS execution-state claim: display_wake resets the idle timer once and
            // lets go. A standing claim nobody clears is indistinguishable from a broken power
            // plan, from the owner's side.
            "ES_CONTINUOUS", "EsContinuous", "ES_SYSTEM_REQUIRED", "EsSystemRequired",
            "ES_AWAYMODE_REQUIRED", "EsAwaymodeRequired",
            // Keys. display_wake moves a pointer by zero and nothing else; there is no keyboard
            // member in its input structure to fill in, and there must never be one.
            "KEYEVENTF", "INPUT_KEYBOARD", "InputKeyboard", "keybd_event", "VkKeyScan",
            "SendKeys", "KeyboardInput",
            // Rearranging the owner's monitors. M18.3 reports topology; it never sets it.
            "ChangeDisplaySettings", "SetDisplayConfig", "DisplayConfigSetDeviceInfo",
        ];

        string[] forbiddenAnyCase = ["hibernate", "logoff", "log off"];

        foreach (var file in files)
        {
            var source = File.ReadAllText(file);
            var name = Path.GetFileName(file);
            foreach (var token in forbidden)
            {
                Assert.False(
                    source.Contains(token, StringComparison.Ordinal),
                    $"{name} names '{token}'; the display family may only wake, report and turn OFF");
            }

            foreach (var token in forbiddenAnyCase)
            {
                Assert.False(
                    source.Contains(token, StringComparison.OrdinalIgnoreCase),
                    $"{name} names '{token}'; the display family may only wake, report and turn OFF");
            }
        }

        // ...and the one parameter the OFF path sends is the "off" one.
        Assert.Contains("MonitorOff = 2", Support.CompanionSources.Read("DisplayPowerController.cs"), StringComparison.Ordinal);

        // The observer observes. It has no way to reach a window it does not own, no execution
        // state and no synthetic input, so it cannot become a second, ungated way to darken a
        // screen.
        var observer = Support.CompanionSources.Read("DisplayStateObserver.cs");
        foreach (var actor in new[] { "SendInput", "SendMessage", "HwndBroadcast", "HWND_BROADCAST", "SetThreadExecutionState", "ScMonitorPower" })
        {
            Assert.False(
                observer.Contains(actor, StringComparison.Ordinal),
                $"DisplayStateObserver.cs names '{actor}'; an observer that can act is not an observer");
        }
    }

    // ------------------------------------------------------------------ manifest + routing

    [Fact]
    public void The_alarm_pair_is_always_advertised_and_display_off_only_behind_its_flag()
    {
        var withoutDisplay = AgentCapabilities.Compose(browserEnabled: false, displayPowerEnabled: false);
        Assert.Contains(AgentCapabilities.DesktopAlarmStart, withoutDisplay);
        Assert.Contains(AgentCapabilities.DesktopAlarmStop, withoutDisplay);
        Assert.DoesNotContain(AgentCapabilities.DesktopDisplayOff, withoutDisplay);

        var withDisplay = AgentCapabilities.Compose(browserEnabled: false, displayPowerEnabled: true);
        Assert.Contains(AgentCapabilities.DesktopDisplayOff, withDisplay);
        Assert.Equal(withDisplay.Count, withDisplay.Distinct(StringComparer.Ordinal).Count());

        Assert.True(AgentCapabilities.IsInteractive(AgentCapabilities.DesktopAlarmStart));
        Assert.True(AgentCapabilities.IsInteractive(AgentCapabilities.DesktopDisplayOff));
        Assert.True(AgentCapabilities.IsInteractive(AgentCapabilities.DesktopOpenApplication));
        Assert.False(AgentCapabilities.IsInteractive("desktop.reboot"));
        Assert.False(AgentCapabilities.IsInteractive("browser.navigate"));

        // The alarm holds the pipe only long enough to arm a ramp, so it keeps the desktop cap.
        Assert.Equal(
            TimeSpan.FromSeconds(60),
            InteractiveCapabilityExecutor.TimeoutCapFor(AgentCapabilities.DesktopAlarmStart));
    }

    [Theory]
    [InlineData(null, false)]
    [InlineData("false", false)]
    [InlineData("true", true)]
    [InlineData("1", true)]
    public void The_service_option_DisplayPowerEnabled_decides_whether_the_name_is_advertised(string? raw, bool expected)
    {
        var configuration = new ConfigurationBuilder()
            .AddInMemoryCollection([new KeyValuePair<string, string?>("DisplayPowerEnabled", raw)])
            .Build();
        var options = AgentServiceOptions.FromConfiguration(configuration);

        Assert.Equal(expected, options.DisplayPowerEnabled);
        Assert.Equal(expected, options.AdvertisedCapabilities.Contains(AgentCapabilities.DesktopDisplayOff));
    }

    [Fact]
    public async Task The_service_refuses_display_off_before_the_companion_is_ever_consulted()
    {
        var transport = new CountingTransport();
        var executor = new InteractiveCapabilityExecutor(transport, displayPowerEnabled: false);

        var ex = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
            TestCommands.New(AgentCapabilities.DesktopDisplayOff), CancellationToken.None));

        Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Equal(0, transport.Calls);
    }

    [Fact]
    public async Task The_service_routes_the_alarm_pair_to_the_companion()
    {
        var transport = new CountingTransport();
        var executor = new InteractiveCapabilityExecutor(transport);

        await executor.ExecuteAsync(TestCommands.New(AgentCapabilities.DesktopAlarmStart), CancellationToken.None);
        await executor.ExecuteAsync(TestCommands.New(AgentCapabilities.DesktopAlarmStop), CancellationToken.None);

        Assert.Equal(2, transport.Calls);
        Assert.Equal(
            [AgentCapabilities.DesktopAlarmStart, AgentCapabilities.DesktopAlarmStop],
            transport.Capabilities);
    }

    [Fact]
    public void A_companion_built_without_an_alarm_answers_capability_missing_never_a_hang()
    {
        var runtime = new CompanionRuntime(
            IpcTestSupport.NewPipeName(),
            new AppLauncher(new Dictionary<string, string>()),
            new ArtifactOpener([Path.GetTempPath()], new NoopFileOpener()),
            NullLogger.Instance);

        Assert.DoesNotContain(AgentCapabilities.DesktopDisplayOff, runtime.AdvertisedCapabilities);

        // The alarm names are advertised (the companion CAN sound one when an endpoint
        // exists), so an unconfigured build must still answer them rather than stall.
        Assert.Contains(AgentCapabilities.DesktopAlarmStart, runtime.AdvertisedCapabilities);
    }

    // ------------------------------------------------------------------ helpers

    private static (AlarmController Controller, FakeDeviceFactory Factory, ManualTimeProvider Clock) NewAlarm()
    {
        var clock = new ManualTimeProvider();
        var factory = new FakeDeviceFactory(clock);
        var controller = new AlarmController(
            factory, () => "ren-laptop", NullLogger.Instance, clock, autoPump: false);
        return (controller, factory, clock);
    }

    /// <summary>One full pulse cycle of chunks, so the assertion cannot land in the quiet half.</summary>
    private static void PumpOneCycle(AlarmController controller)
    {
        for (var i = 0; i < (WakeTone.CycleMs / 200) + 2; i++)
        {
            Assert.True(controller.Pump());
        }
    }

    private static double PeakOver(RecordingPlayback playback, int from)
    {
        var peak = 0.0;
        for (var i = from; i < playback.Chunks.Count; i++)
        {
            peak = Math.Max(peak, WakeTone.PeakLevel(playback.Chunks[i]));
        }

        return peak;
    }

    private sealed class RecordingDisplayPower : IDisplayPower
    {
        public int Calls { get; private set; }

        public void TurnOff() => Calls++;
    }

    private sealed class ThrowingDisplayPower : IDisplayPower
    {
        public void TurnOff() => throw new InvalidOperationException("no window accepted the broadcast");
    }

    private sealed class NoopFileOpener : IFileOpener
    {
        public void Open(string fullPath)
        {
        }
    }

    private sealed class CountingTransport : ICompanionCapabilityTransport
    {
        public int Calls { get; private set; }

        public List<string> Capabilities { get; } = [];

        public Task<JsonObject?> ExecuteCapabilityAsync(
            string capability, JsonObject payload, TimeSpan timeout, CancellationToken cancellationToken)
        {
            Calls++;
            Capabilities.Add(capability);
            return Task.FromResult<JsonObject?>([]);
        }
    }

    /// <summary>
    /// <see cref="FakeDeviceFactory"/> plus the bytes: the alarm's real generated audio is
    /// kept so a test can read its peak level instead of listening to it.
    /// </summary>
    private sealed class RecordingDeviceFactory(TimeProvider time) : IAudioDeviceFactory
    {
        public List<RecordingPlayback> Recorded { get; } = [];

        public IAudioCapture OpenCapture(string deviceId, AudioFormat format)
            => new FakeCapture(deviceId, format);

        public IAudioPlayback OpenPlayback(string deviceId, AudioFormat format)
        {
            var playback = new RecordingPlayback(deviceId, format, time);
            Recorded.Add(playback);
            return playback;
        }
    }

    private sealed class RecordingPlayback(string deviceId, AudioFormat format, TimeProvider time) : IAudioPlayback
    {
        private readonly FakePlayback _inner = new(deviceId, format, time);

        public List<byte[]> Chunks { get; } = [];

        public string DeviceId => _inner.DeviceId;

        public AudioFormat Format => _inner.Format;

        public bool IsPlaying => _inner.IsPlaying;

        public int QueuedMs => _inner.QueuedMs;

        public event Action? Drained
        {
            add => _inner.Drained += value;
            remove => _inner.Drained -= value;
        }

        public void Start() => _inner.Start();

        public void Enqueue(ReadOnlySpan<byte> pcm16)
        {
            Chunks.Add(pcm16.ToArray());
            _inner.Enqueue(pcm16);
        }

        public PlaybackStopReport StopImmediately() => _inner.StopImmediately();

        public void Dispose() => _inner.Dispose();
    }
}
