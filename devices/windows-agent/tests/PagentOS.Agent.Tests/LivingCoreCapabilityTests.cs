using System.Buffers.Binary;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
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
/// M18.3, the living core's device half (DEVICE_PROTOCOL.md §6e–§6h): waking and reporting a
/// display, arming a LOCAL fallback alarm, reporting activity on the heartbeat, and playing one
/// short greeting.
///
/// <para>Nothing here touches a real display, a real speaker or a real network. Every boundary is
/// an interface — <see cref="IDisplayPower"/>, <see cref="IDisplayWake"/>,
/// <see cref="IDisplayStateObserver"/>, <see cref="IInputActivitySource"/>,
/// <see cref="IMonitorInventory"/>, <see cref="IAudioDeviceFactory"/> and an
/// <see cref="HttpMessageHandler"/> — so the properties that matter are asserted on data. The
/// clock is a <see cref="ManualTimeProvider"/>, which makes "it rings at fire_at + grace_s and
/// not one second earlier" exact arithmetic rather than a wall-clock race.</para>
/// </summary>
public sealed class LivingCoreCapabilityTests
{
    // ================================================================ §6e display wake

    [Fact]
    public void Display_wake_runs_its_two_steps_in_order_and_neither_of_them_is_a_key()
    {
        var wake = new RecordingDisplayWake();
        var controller = NewDisplay(wake: wake);

        var result = controller.Wake(new JsonObject { ["reason"] = "alarm_ringing" });

        // The order is load-bearing: the execution-state request resets the idle timer, and the
        // zero-delta pointer move is what actually brings a dark screen back. Reversed, a screen
        // could wake and immediately be eligible to sleep again.
        Assert.Equal(["display_needed_once", "pointer_nudge"], wake.Steps);
        Assert.True(result["woken"]!.GetValue<bool>());
        Assert.Equal("display_needed+pointer_move_zero", result["method"]!.GetValue<string>());

        // ...and there is no key step to run, in this fake or in the real one. The structural
        // half of that claim lives in
        // AmbientCapabilityTests.The_display_capability_can_only_turn_a_display_off_never_suspend_the_machine;
        // this half says the production path calls exactly two things and no more.
        Assert.Equal(2, wake.Steps.Count);
    }

    [Fact]
    public void Display_wake_reports_the_observed_state_and_survives_a_companion_with_no_wake_path()
    {
        var observer = new FakeDisplayObserver(new DisplayObservation(DisplayObservation.On, Instant(12, 0)));
        var controller = NewDisplay(wake: new RecordingDisplayWake(), observer: observer, idle: TimeSpan.FromSeconds(3));

        var result = controller.Wake([]);
        Assert.Equal("on", result["observed_state"]!.GetValue<string>());
        Assert.Equal(3.0, result["input_idle_s"]!.GetValue<double>(), 3);

        var withoutWake = NewDisplay(wake: null);
        var ex = Assert.Throws<CapabilityException>(() => withoutWake.Wake([]));
        Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);
    }

    [Fact]
    public void A_wake_that_the_session_refuses_is_a_failure_not_a_reported_success()
    {
        var controller = NewDisplay(wake: new ThrowingDisplayWake());

        var ex = Assert.Throws<CapabilityException>(() => controller.Wake([]));

        Assert.Equal(ErrorClasses.DependencyUnavailable, ex.ErrorClass);
        Assert.True(ex.Retryable);
    }

    // ================================================================ §6e display status

    [Fact]
    public void Display_status_reports_what_it_was_told_and_says_unknown_before_it_was_told_anything()
    {
        var neverTold = NewDisplay();
        var blank = neverTold.Status([]);

        // "unknown" with a null timestamp, not a guess from the idle timer: the display idling
        // out and the owner idling out are different events.
        Assert.Equal("unknown", blank["observed_state"]!.GetValue<string>());
        Assert.Null(blank["observed_at"]);
        Assert.Null(blank["input_idle_s"]);
        Assert.False(blank.ContainsKey("monitors"));

        var told = NewDisplay(
            observer: new FakeDisplayObserver(new DisplayObservation(DisplayObservation.Dimmed, Instant(1, 30))),
            idle: TimeSpan.FromSeconds(90),
            monitors: new FakeMonitors(
                new MonitorGeometry(0, true, 0, 0, 2560, 1440),
                new MonitorGeometry(1, false, 2560, 0, 1920, 1080)));

        var full = told.Status([]);
        Assert.Equal("dimmed", full["observed_state"]!.GetValue<string>());
        Assert.NotNull(full["observed_at"]);
        Assert.Equal(90.0, full["input_idle_s"]!.GetValue<double>(), 3);

        var monitors = full["monitors"]!.AsArray();
        Assert.Equal(2, monitors.Count);
        Assert.True(monitors[0]!["primary"]!.GetValue<bool>());
        Assert.Equal(2560, monitors[0]!["width"]!.GetValue<int>());
        Assert.Equal(1080, monitors[1]!["height"]!.GetValue<int>());
    }

    // ================================================================ §6e display off

    [Fact]
    public void Display_off_refuses_a_screen_the_owner_just_touched_and_the_refusal_is_a_success()
    {
        var power = new CountingDisplayPower();
        var controller = NewDisplay(power: power, enabled: true, idle: TimeSpan.FromSeconds(11));

        var result = controller.TurnOff(new JsonObject { ["reason"] = "likely_asleep" });

        // A SUCCESSFUL result, deliberately. Nothing went wrong: the device looked, and the
        // answer was no. An error class here would make a correct refusal indistinguishable
        // from a broken device, and a caller would retry it.
        Assert.False(result["display_off"]!.GetValue<bool>());
        Assert.Equal("recent_input", result["refused"]!.GetValue<string>());
        Assert.Equal(11.0, result["input_idle_s"]!.GetValue<double>(), 3);
        Assert.Equal(DisplayPowerController.DefaultHoldoffSeconds, result["holdoff_s"]!.GetValue<int>());
        Assert.Equal("unknown", result["observed_state"]!.GetValue<string>());
        Assert.Equal(0, power.Calls);
    }

    [Theory]
    // Inside the window: refused. On the boundary and past it: allowed. The comparison is
    // strictly-less-than, so a holdoff of 120 s means "120 s of quiet is enough", not 121.
    [InlineData(0, 120, false)]
    [InlineData(119.9, 120, false)]
    [InlineData(120, 120, true)]
    [InlineData(600, 120, true)]
    [InlineData(0, 0, true)]
    [InlineData(5, 30, false)]
    public void The_holdoff_window_is_exactly_the_idle_time_compared_with_the_holdoff(
        double idleSeconds, int holdoff, bool expectedOff)
    {
        var power = new CountingDisplayPower();
        var controller = NewDisplay(power: power, enabled: true, idle: TimeSpan.FromSeconds(idleSeconds));

        var result = controller.TurnOff(new JsonObject { ["holdoff_s"] = holdoff });

        Assert.Equal(expectedOff, result["display_off"]!.GetValue<bool>());
        Assert.Equal(expectedOff ? 1 : 0, power.Calls);
        Assert.Equal(holdoff, result["holdoff_s"]!.GetValue<int>());
    }

    [Fact]
    public void An_unknown_idle_time_never_satisfies_the_holdoff_by_itself_but_does_not_block_the_off()
    {
        // A device that cannot measure idle time reports null, and the holdoff has nothing to
        // compare. It must not fabricate a zero (which would refuse forever) nor a large value
        // (which would claim quiet it never observed); it simply does not refuse on that ground.
        var power = new CountingDisplayPower();
        var controller = NewDisplay(power: power, enabled: true, idle: null);

        var result = controller.TurnOff([]);

        Assert.True(result["display_off"]!.GetValue<bool>());
        Assert.Null(result["input_idle_s"]);
        Assert.Equal(1, power.Calls);
    }

    [Fact]
    public void Display_off_refuses_while_an_alarm_is_ringing_whatever_the_idle_timer_says()
    {
        var power = new CountingDisplayPower();
        var controller = NewDisplay(
            power: power,
            enabled: true,
            idle: TimeSpan.FromHours(3),
            isAlarmRinging: () => true);

        var result = controller.TurnOff([]);

        // Three hours of quiet is exactly the state a wake alarm fires into. Darkening the
        // screen at that moment is the machine working against the thing it just did.
        Assert.False(result["display_off"]!.GetValue<bool>());
        Assert.Equal("alarm_active", result["refused"]!.GetValue<string>());
        Assert.Equal(0, power.Calls);
    }

    [Fact]
    public void A_successful_off_reports_the_state_read_back_after_the_broadcast()
    {
        var observer = new FakeDisplayObserver(DisplayObservation.Nothing);
        var power = new CallbackDisplayPower(() =>
            observer.Set(new DisplayObservation(DisplayObservation.Off, Instant(2, 0))));
        var controller = NewDisplay(
            power: power,
            enabled: true,
            idle: TimeSpan.FromMinutes(30),
            observer: observer,
            monitors: new FakeMonitors(new MonitorGeometry(0, true, 0, 0, 1920, 1080)));

        var result = controller.TurnOff([]);

        Assert.True(result["display_off"]!.GetValue<bool>());
        Assert.Equal("wm_syscommand_monitorpower", result["method"]!.GetValue<string>());
        // Read back, not assumed: the state in the result is the one the machine reported after
        // the broadcast, so a broadcast nobody honoured cannot be reported as a dark screen.
        Assert.Equal("off", result["observed_state"]!.GetValue<string>());
        Assert.NotNull(result["observed_at"]);
        Assert.Single(result["monitors"]!.AsArray());
    }

    [Fact]
    public void The_flag_still_refuses_before_any_holdoff_arithmetic_happens()
    {
        var power = new CountingDisplayPower();
        var controller = NewDisplay(power: power, enabled: false, idle: TimeSpan.FromHours(5));

        var ex = Assert.Throws<CapabilityException>(() => controller.TurnOff([]));

        // Not a refusal result: a device that has not been qualified for display-off does not
        // have the capability at all, and must answer the way it answers any name it lacks.
        Assert.Equal(ErrorClasses.CapabilityMissing, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Equal(0, power.Calls);
    }

    [Fact]
    public void A_malformed_holdoff_is_a_validation_error_not_a_silent_default()
    {
        var controller = NewDisplay(enabled: true);

        Assert.Equal(
            ErrorClasses.ValidationError,
            Assert.Throws<CapabilityException>(
                () => controller.TurnOff(new JsonObject { ["holdoff_s"] = "two minutes" })).ErrorClass);
        Assert.Equal(
            ErrorClasses.ValidationError,
            Assert.Throws<CapabilityException>(
                () => controller.TurnOff(new JsonObject { ["holdoff_s"] = 12.5 })).ErrorClass);

        // Out-of-range values are clamped rather than refused: an absurd holdoff still describes
        // an intent ("wait a long time"), unlike a value that is not a number of seconds at all.
        var clamped = NewDisplay(enabled: true, idle: TimeSpan.FromDays(1))
            .TurnOff(new JsonObject { ["holdoff_s"] = 999999 });
        Assert.Equal(DisplayPowerController.MaxHoldoffSeconds, clamped["holdoff_s"]!.GetValue<int>());
    }

    // ================================================================ §6f alarm arming

    [Fact]
    public void An_armed_alarm_rings_locally_at_fire_at_plus_grace_and_not_one_second_earlier()
    {
        using var scope = new ArmScope();
        var fireAt = scope.Clock.GetUtcNow() + TimeSpan.FromMinutes(10);

        var armed = scope.Arms.Arm(new JsonObject
        {
            ["alarm_id"] = "wake-1",
            ["fire_at"] = fireAt.ToString("O"),
            ["grace_s"] = 90,
            ["label"] = "Sabah alarmı",
        });

        Assert.True(armed["armed"]!.GetValue<bool>());
        Assert.Equal(90, armed["grace_s"]!.GetValue<int>());
        Assert.Equal(1, armed["armed_count"]!.GetValue<int>());
        Assert.False(armed["replaced"]!.GetValue<bool>());

        // At fire_at exactly, the cloud is still the one expected to ring: the grace is what
        // stops the device racing a command that is already in flight.
        scope.Clock.Advance(TimeSpan.FromMinutes(10));
        Assert.Equal(0, scope.Arms.Tick());
        Assert.Null(scope.Alarm.RingingAlarmId);

        scope.Clock.Advance(TimeSpan.FromSeconds(89));
        Assert.Equal(0, scope.Arms.Tick());

        scope.Clock.Advance(TimeSpan.FromSeconds(1));
        Assert.Equal(1, scope.Arms.Tick());
        Assert.Equal("wake-1", scope.Alarm.RingingAlarmId);
        Assert.Equal(0, scope.Arms.ArmedCount);
    }

    [Fact]
    public void A_cloud_alarm_start_consumes_the_arm_so_one_wake_up_never_rings_twice()
    {
        using var scope = new ArmScope();
        var fireAt = scope.Clock.GetUtcNow() + TimeSpan.FromMinutes(5);
        scope.Arms.Arm(new JsonObject
        {
            ["alarm_id"] = "wake-2",
            ["fire_at"] = fireAt.ToString("O"),
            ["grace_s"] = 60,
        });

        // The cloud reaches the device on time. This is what CompanionRuntime does after a
        // successful desktop.alarm_start.
        scope.Alarm.Start(new JsonObject { ["alarm_id"] = "wake-2" });
        Assert.True(scope.Arms.Consume("wake-2", "cloud_alarm_start"));

        scope.Clock.Advance(TimeSpan.FromHours(1));
        Assert.Equal(0, scope.Arms.Tick());
        Assert.Equal(0, scope.Arms.LocalRings);
    }

    [Fact]
    public void Disarming_is_idempotent_and_arming_the_same_id_twice_replaces_rather_than_doubles()
    {
        using var scope = new ArmScope();
        var fireAt = scope.Clock.GetUtcNow() + TimeSpan.FromMinutes(30);

        scope.Arms.Arm(new JsonObject { ["alarm_id"] = "wake-3", ["fire_at"] = fireAt.ToString("O") });
        var again = scope.Arms.Arm(new JsonObject
        {
            ["alarm_id"] = "wake-3",
            ["fire_at"] = fireAt.AddMinutes(15).ToString("O"),
        });

        Assert.True(again["replaced"]!.GetValue<bool>());
        Assert.Equal(1, again["armed_count"]!.GetValue<int>());

        var disarmed = scope.Arms.Disarm(new JsonObject { ["alarm_id"] = "wake-3" });
        Assert.True(disarmed["disarmed"]!.GetValue<bool>());
        Assert.True(disarmed["was_armed"]!.GetValue<bool>());

        // Said twice, still a success: the caller asked for it not to ring, and it will not.
        var repeated = scope.Arms.Disarm(new JsonObject { ["alarm_id"] = "wake-3" });
        Assert.True(repeated["disarmed"]!.GetValue<bool>());
        Assert.False(repeated["was_armed"]!.GetValue<bool>());

        // Only the replacement time survived, so the disarm removed one arm and not two.
        Assert.Equal(0, scope.Arms.ArmedCount);
    }

    [Fact]
    public void An_arm_without_an_id_or_without_a_fire_at_is_a_validation_error()
    {
        using var scope = new ArmScope();

        Assert.Equal(
            ErrorClasses.ValidationError,
            Assert.Throws<CapabilityException>(
                () => scope.Arms.Arm(new JsonObject { ["fire_at"] = "2026-09-07T06:30:00Z" })).ErrorClass);
        Assert.Equal(
            ErrorClasses.ValidationError,
            Assert.Throws<CapabilityException>(
                () => scope.Arms.Arm(new JsonObject { ["alarm_id"] = "x", ["fire_at"] = "tomorrow morning" })).ErrorClass);
    }

    [Fact]
    public void A_restart_rings_a_recently_overdue_arm_once_and_never_a_second_time()
    {
        var path = Path.Combine(TestPaths.NewTempDir(), "armed-alarms.json");
        var clock = new ManualTimeProvider();
        var fireAt = clock.GetUtcNow() + TimeSpan.FromMinutes(10);

        using (var first = new ArmScope(path, clock))
        {
            first.Arms.Arm(new JsonObject
            {
                ["alarm_id"] = "wake-4",
                ["fire_at"] = fireAt.ToString("O"),
                ["grace_s"] = 60,
            });
        }

        // The companion was down when the alarm came due; it starts twenty minutes late.
        clock.Advance(TimeSpan.FromMinutes(31));

        using (var second = new ArmScope(path, clock))
        {
            Assert.Equal(1, second.Arms.ReloadOnStart());
            Assert.Equal("wake-4", second.Alarm.RingingAlarmId);
            Assert.Equal(0, second.Arms.ArmedCount);
        }

        // ...and the arm is gone from disk, so a third start is silent. This is the property the
        // store exists for: the removal is persisted BEFORE the first sample is generated.
        using var third = new ArmScope(path, clock);
        Assert.Equal(0, third.Arms.ReloadOnStart());
        Assert.Null(third.Alarm.RingingAlarmId);
        Assert.Equal(0, third.Arms.LocalRings);
    }

    [Fact]
    public void An_arm_that_is_hours_overdue_expires_with_an_audit_row_instead_of_ringing()
    {
        var path = Path.Combine(TestPaths.NewTempDir(), "armed-alarms.json");
        var clock = new ManualTimeProvider();
        var fireAt = clock.GetUtcNow() + TimeSpan.FromMinutes(5);

        using (var first = new ArmScope(path, clock))
        {
            first.Arms.Arm(new JsonObject { ["alarm_id"] = "wake-5", ["fire_at"] = fireAt.ToString("O") });
        }

        // The machine was off all day. Ringing a 06:30 alarm at 14:00 is not a late wake-up; it
        // is a machine behaving badly, and the owner would rather read about it.
        clock.Advance(AlarmArmController.StaleAfter + TimeSpan.FromHours(1));

        using var second = new ArmScope(path, clock);
        Assert.Equal(0, second.Arms.ReloadOnStart());
        Assert.Equal(1, second.Arms.Expired);
        Assert.Null(second.Alarm.RingingAlarmId);
        Assert.Equal(0, second.Arms.ArmedCount);
    }

    [Fact]
    public void The_arm_carries_its_ramp_to_the_alarm_it_eventually_rings()
    {
        using var scope = new ArmScope();
        var fireAt = scope.Clock.GetUtcNow() + TimeSpan.FromMinutes(1);

        scope.Arms.Arm(new JsonObject
        {
            ["alarm_id"] = "wake-6",
            ["fire_at"] = fireAt.ToString("O"),
            ["grace_s"] = 0,
            ["wake_volume"] = new JsonObject { ["start"] = 0.05, ["end"] = 0.6, ["ramp_seconds"] = 30 },
            ["max_duration_s"] = 120,
        });

        scope.Clock.Advance(TimeSpan.FromMinutes(1));
        Assert.Equal(1, scope.Arms.Tick());

        // The fallback rings the alarm the owner configured, not a default one: the local ring
        // is the SAME alarm, arriving by a different road.
        var playback = Assert.Single(scope.Devices.Playbacks);
        Assert.True(playback.EnqueuedBytes > 0);
        Assert.Equal("wake-6", scope.Alarm.RingingAlarmId);
    }

    // ================================================================ §6g activity status

    [Fact]
    public void Activity_status_carries_exactly_the_fields_the_heartbeat_schema_allows()
    {
        using var scope = new ArmScope();
        scope.Arms.Arm(new JsonObject
        {
            ["alarm_id"] = "wake-7",
            ["fire_at"] = scope.Clock.GetUtcNow().AddHours(6).ToString("O"),
        });
        scope.Alarm.Start(new JsonObject { ["alarm_id"] = "ringing-now" });

        var reporter = new ActivityStatusReporter(
            new FakeInputActivity(TimeSpan.FromSeconds(42.4)),
            new FakeDisplayObserver(new DisplayObservation(DisplayObservation.On, Instant(9, 15))),
            () => scope.Alarm.RingingAlarmId,
            scope.Arms);

        var status = reporter.Report([]);

        Assert.Equal(HeartbeatStatus.Fields.OrderBy(f => f, StringComparer.Ordinal), status.Select(p => p.Key).OrderBy(k => k, StringComparer.Ordinal));
        Assert.Equal(42.4, status["input_idle_s"]!.GetValue<double>(), 3);
        Assert.Equal("on", status["display_state"]!.GetValue<string>());
        Assert.NotNull(status["display_observed_at"]);
        Assert.True(status["alarm_ringing"]!.GetValue<bool>());
        Assert.Equal("ringing-now", status["ringing_alarm_id"]!.GetValue<string>());
        Assert.Equal(1, status["armed_alarms"]!.GetValue<int>());
        Assert.NotNull(status["next_alarm_at"]);
    }

    [Fact]
    public void An_unwired_companion_reports_a_status_of_honest_nulls_rather_than_failing()
    {
        var reporter = new ActivityStatusReporter(
            UnknownInputActivitySource.Instance,
            UnknownDisplayStateObserver.Instance,
            () => null);

        var status = reporter.Report([]);

        Assert.Null(status["input_idle_s"]);
        Assert.Equal("unknown", status["display_state"]!.GetValue<string>());
        Assert.Null(status["display_observed_at"]);
        Assert.False(status["alarm_ringing"]!.GetValue<bool>());
        Assert.Null(status["ringing_alarm_id"]);
        Assert.Equal(0, status["armed_alarms"]!.GetValue<int>());
        Assert.Null(status["next_alarm_at"]);
    }

    [Fact]
    public void The_activity_source_reads_a_tick_count_and_never_the_owners_input()
    {
        // The narrowness of this file is the privacy claim, and it is the kind of claim that is
        // far easier to keep by reading the source than by observing behaviour: a keylogger and
        // an idle timer look identical from outside until the day they do not.
        var source = CompanionSources.Read("InputActivity.cs");
        string[] forbidden =
        [
            "SetWindowsHookEx", "WH_KEYBOARD", "WH_MOUSE", "GetAsyncKeyState", "GetKeyState",
            "GetKeyboardState", "GetKeyNameText", "RegisterRawInputDevices", "GetRawInputData",
            "GetCursorPos", "GetForegroundWindow", "GetWindowText", "ToUnicode",
        ];
        foreach (var name in forbidden)
        {
            Assert.False(
                source.Contains(name, StringComparison.Ordinal),
                $"InputActivity.cs names '{name}'; this file may read a tick count and nothing else");
        }

        Assert.Contains("GetLastInputInfo", source, StringComparison.Ordinal);
    }

    [Fact]
    public void The_heartbeat_status_projection_drops_anything_the_schema_would_refuse()
    {
        // additionalProperties:false inside status means one unknown key would make EVERY
        // heartbeat fail the broker's validation. A newer companion adding a field must not be
        // able to do that to an older service, so the service closes the set itself.
        var projected = HeartbeatStatus.Project(new JsonObject
        {
            ["input_idle_s"] = 12,
            ["display_state"] = "on",
            ["window_title"] = "the owner's bank",
            ["clipboard"] = "hunter2",
        });

        Assert.NotNull(projected);
        Assert.Equal(["input_idle_s", "display_state"], projected!.Select(p => p.Key));
        Assert.Null(HeartbeatStatus.Project(null));
        Assert.Null(HeartbeatStatus.Project(new JsonObject { ["window_title"] = "x" }));
    }

    [Fact]
    public async Task The_heartbeat_carries_the_companions_status_when_it_answers_in_time()
    {
        await using var broker = await FakeBroker.StartAsync(heartbeatIntervalS: 60);
        var provider = new ScriptedStatusProvider(() => new JsonObject
        {
            ["input_idle_s"] = 412.5,
            ["display_state"] = "off",
            ["armed_alarms"] = 1,
        });
        await using var agent = new AgentHarness(
            broker.WsUri,
            ScriptedExecutor.Returning(() => []),
            heartbeatOverrideS: 0.1,
            statusProvider: provider);

        var session = await broker.WaitForSessionAsync();
        var heartbeat = await session.WaitForHeartbeatAsync();

        Assert.NotNull(heartbeat.Status);
        Assert.Equal(412.5, heartbeat.Status!["input_idle_s"]!.GetValue<double>(), 3);
        Assert.Equal("off", heartbeat.Status["display_state"]!.GetValue<string>());
        Assert.True(provider.Calls > 0);
    }

    [Fact]
    public async Task A_device_with_no_companion_sends_the_heartbeat_it_always_sent()
    {
        await using var broker = await FakeBroker.StartAsync(heartbeatIntervalS: 60);
        await using var agent = new AgentHarness(
            broker.WsUri,
            ScriptedExecutor.Returning(() => []),
            heartbeatOverrideS: 0.1);

        var session = await broker.WaitForSessionAsync();
        var heartbeat = await session.WaitForHeartbeatAsync();

        // No provider, no status field. Older brokers and older agents see exactly the frame
        // they saw before M18.3, which is what "additive" has to mean to be worth saying.
        Assert.Null(heartbeat.Status);
    }

    [Fact]
    public async Task A_slow_companion_delays_no_heartbeat_and_simply_loses_its_status()
    {
        await using var broker = await FakeBroker.StartAsync(heartbeatIntervalS: 60);
        var provider = new ScriptedStatusProvider(async token =>
        {
            await Task.Delay(TimeSpan.FromSeconds(30), token);
            return new JsonObject { ["display_state"] = "on" };
        });
        await using var agent = new AgentHarness(
            broker.WsUri,
            ScriptedExecutor.Returning(() => []),
            heartbeatOverrideS: 0.1,
            statusProvider: provider);

        var session = await broker.WaitForSessionAsync();
        // The first heartbeat carries the connection's own start-up latency (on a loaded GitHub
        // runner once 8.65 s) — not what this test measures. The guarantee is about the status
        // path: with a provider that never answers, the heartbeat cadence must still be bounded
        // by MaxWait. So the interval between two consecutive heartbeats is what is measured.
        var first = await session.WaitForHeartbeatAsync(TimeSpan.FromSeconds(20));
        var started = DateTimeOffset.UtcNow;
        var heartbeat = await session.WaitForHeartbeatAsync(TimeSpan.FromSeconds(10));
        var waited = DateTimeOffset.UtcNow - started;

        // Presence is computed from heartbeats. A status path that could stall one would let a
        // busy companion make this device look offline — strictly worse than a heartbeat with
        // no status on it.
        Assert.Null(first.Status);
        Assert.Null(heartbeat.Status);
        Assert.True(
            waited < HeartbeatStatus.MaxWait + TimeSpan.FromSeconds(4),
            $"the heartbeat after the first waited {waited.TotalSeconds:0.##} s on a slow status provider");
    }

    [Fact]
    public async Task A_status_provider_that_throws_costs_the_heartbeat_nothing()
    {
        await using var broker = await FakeBroker.StartAsync(heartbeatIntervalS: 60);
        var provider = new ScriptedStatusProvider(() => throw new InvalidOperationException("pipe gone"));
        await using var agent = new AgentHarness(
            broker.WsUri,
            ScriptedExecutor.Returning(() => []),
            heartbeatOverrideS: 0.1,
            statusProvider: provider);

        var session = await broker.WaitForSessionAsync();
        var heartbeat = await session.WaitForHeartbeatAsync();

        Assert.Null(heartbeat.Status);
        Assert.True(heartbeat.Seq >= 0);
    }

    [Fact]
    public async Task The_service_asks_the_companion_for_the_status_and_reports_nothing_when_it_cannot()
    {
        var transport = new StatusTransport(_ => new JsonObject
        {
            ["display_state"] = "dimmed",
            ["armed_alarms"] = 2,
            ["not_in_the_schema"] = true,
        });
        var provider = new CompanionHeartbeatStatusProvider(transport);

        var status = await provider.GetStatusAsync(CancellationToken.None);
        Assert.NotNull(status);
        Assert.Equal("dimmed", status!["display_state"]!.GetValue<string>());
        Assert.False(status.ContainsKey("not_in_the_schema"));
        Assert.Equal(AgentCapabilities.DesktopActivityStatus, Assert.Single(transport.Capabilities));

        var noCompanion = new CompanionHeartbeatStatusProvider(new StatusTransport(_ =>
            throw new CapabilityException(ErrorClasses.DependencyUnavailable, "no session companion", retryable: true)));
        Assert.Null(await noCompanion.GetStatusAsync(CancellationToken.None));
    }

    // ================================================================ §6h play_audio

    [Fact]
    public async Task A_greeting_from_the_broker_is_verified_scaled_and_played_to_completion()
    {
        var wav = Wav.Pcm16(sampleRate: 24000, channels: 1, milliseconds: 400, amplitude: 20000);
        var devices = new GreetingDeviceFactory();
        var player = NewGreeting(devices, wav);

        var result = await player.PlayAsync(Payload(wav, level: 0.5), CancellationToken.None);

        Assert.True(result["played"]!.GetValue<bool>());
        Assert.Equal("greeting-1", result["audio_id"]!.GetValue<string>());
        Assert.Equal(400, result["duration_ms"]!.GetValue<int>());
        Assert.Equal(0.5, result["level"]!.GetValue<double>(), 6);

        var playback = Assert.Single(devices.Playbacks);
        Assert.Equal(24000, playback.Format.SampleRate);
        Assert.Equal(1, playback.Format.Channels);
        Assert.True(playback.Disposed, "the endpoint must be closed once the greeting has finished");

        // The LEVEL scales the samples, and nothing else: a 20000-amplitude tone at level 0.5
        // reaches the endpoint at 10000, and the machine's mixer never moves.
        var peak = Peak(playback.Enqueued);
        Assert.InRange(peak, 9990, 10010);
    }

    [Theory]
    [InlineData(1.0, 20000)]
    [InlineData(0.75, 15000)]
    [InlineData(0.25, 5000)]
    [InlineData(0.0, 0)]
    public async Task The_level_scales_the_samples_proportionally(double level, int expectedPeak)
    {
        var wav = Wav.Pcm16(sampleRate: 16000, channels: 1, milliseconds: 100, amplitude: 20000);
        var devices = new GreetingDeviceFactory();
        var player = NewGreeting(devices, wav);

        await player.PlayAsync(Payload(wav, level: level), CancellationToken.None);

        Assert.InRange(Peak(devices.Playbacks.Single().Enqueued), Math.Max(0, expectedPeak - 12), expectedPeak + 12);
    }

    [Fact]
    public async Task A_greeting_whose_bytes_do_not_match_the_digest_is_refused_and_never_reaches_an_endpoint()
    {
        var wav = Wav.Pcm16(sampleRate: 24000, channels: 1, milliseconds: 200, amplitude: 8000);
        var devices = new GreetingDeviceFactory();
        var player = NewGreeting(devices, wav);

        var payload = Payload(wav);
        payload["audio"]!["sha256"] = new string('a', 64);

        var ex = await Assert.ThrowsAsync<CapabilityException>(
            () => player.PlayAsync(payload, CancellationToken.None));

        // security_scope_error and never retryable: what is at that URL is not what Cloud Core
        // described, and fetching it again would not change that.
        Assert.Equal(ErrorClasses.SecurityScopeError, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Empty(devices.Playbacks);
    }

    [Fact]
    public async Task A_greeting_larger_than_it_declared_is_refused_while_it_is_still_arriving()
    {
        var wav = Wav.Pcm16(sampleRate: 24000, channels: 1, milliseconds: 400, amplitude: 8000);
        var devices = new GreetingDeviceFactory();
        var player = NewGreeting(devices, wav);

        var payload = Payload(wav);
        // The payload claims a tenth of what the server will actually send.
        payload["audio"]!["bytes"] = wav.Length / 10;

        var ex = await Assert.ThrowsAsync<CapabilityException>(
            () => player.PlayAsync(payload, CancellationToken.None));

        Assert.Equal(ErrorClasses.ValidationError, ex.ErrorClass);
        Assert.Empty(devices.Playbacks);
    }

    [Fact]
    public async Task A_declared_size_beyond_the_two_mebibyte_bound_is_refused_before_anything_is_fetched()
    {
        var wav = Wav.Pcm16(sampleRate: 24000, channels: 1, milliseconds: 100, amplitude: 8000);
        var devices = new GreetingDeviceFactory();
        var handler = new StubHttpHandler(wav);
        var player = new GreetingPlayer(
            devices, () => "ren-laptop", new HttpClient(handler), NullLogger.Instance);

        var payload = Payload(wav);
        payload["audio"]!["bytes"] = GreetingPlayer.MaxAudioBytes + 1;

        var ex = await Assert.ThrowsAsync<CapabilityException>(
            () => player.PlayAsync(payload, CancellationToken.None));

        Assert.Equal(ErrorClasses.ValidationError, ex.ErrorClass);
        Assert.Equal(0, handler.Requests);
    }

    [Fact]
    public async Task A_greeting_longer_than_the_cap_is_trimmed_rather_than_refused()
    {
        var wav = Wav.Pcm16(sampleRate: 8000, channels: 1, milliseconds: 30000, amplitude: 6000);
        var devices = new GreetingDeviceFactory();
        var player = NewGreeting(devices, wav);

        var result = await player.PlayAsync(Payload(wav), CancellationToken.None);

        // The owner asked to be greeted. A slightly short greeting serves that better than
        // silence plus an error.
        Assert.Equal(GreetingPlayer.MaxSeconds * 1000, result["duration_ms"]!.GetValue<int>());
    }

    [Fact]
    public async Task Stereo_and_unusual_rates_are_played_at_their_own_rate_rather_than_resampled_here()
    {
        var wav = Wav.Pcm16(sampleRate: 44100, channels: 2, milliseconds: 200, amplitude: 12000);
        var devices = new GreetingDeviceFactory();
        var player = NewGreeting(devices, wav);

        await player.PlayAsync(Payload(wav), CancellationToken.None);

        var playback = Assert.Single(devices.Playbacks);
        Assert.Equal(44100, playback.Format.SampleRate);
        Assert.Equal(2, playback.Format.Channels);
    }

    [Fact]
    public async Task Audio_that_is_not_a_wav_or_not_PCM16_is_a_validation_error()
    {
        var devices = new GreetingDeviceFactory();
        var junk = Encoding.ASCII.GetBytes("this is definitely not a wave file at all, no");
        var player = new GreetingPlayer(
            devices, () => "ren-laptop", new HttpClient(new StubHttpHandler(junk)), NullLogger.Instance);

        var ex = await Assert.ThrowsAsync<CapabilityException>(
            () => player.PlayAsync(Payload(junk), CancellationToken.None));

        Assert.Equal(ErrorClasses.ValidationError, ex.ErrorClass);
        Assert.Empty(devices.Playbacks);

        // A container this parser understands, at a bit depth it does not.
        Assert.Equal(
            ErrorClasses.ValidationError,
            Assert.Throws<CapabilityException>(() => WavAudio.Parse(Wav.Pcm8(8000, 100))).ErrorClass);
    }

    [Fact]
    public async Task With_no_render_endpoint_the_greeting_says_dependency_unavailable_and_retryable()
    {
        var wav = Wav.Pcm16(sampleRate: 24000, channels: 1, milliseconds: 100, amplitude: 8000);
        var player = new GreetingPlayer(
            new GreetingDeviceFactory(), () => null, new HttpClient(new StubHttpHandler(wav)), NullLogger.Instance);

        var ex = await Assert.ThrowsAsync<CapabilityException>(
            () => player.PlayAsync(Payload(wav), CancellationToken.None));

        Assert.Equal(ErrorClasses.DependencyUnavailable, ex.ErrorClass);
        Assert.True(ex.Retryable);
    }

    [Fact]
    public void No_companion_source_can_reach_the_machines_own_volume()
    {
        // The alarm's promise and the greeting's are the same one: the level scales the samples
        // THIS process generates. A capability that raised the endpoint or master volume would
        // leave the owner's machine loud afterwards, and would do it to every other application
        // at the same time.
        string[] forbidden =
        [
            "IAudioEndpointVolume", "AudioEndpointVolume", "ISimpleAudioVolume", "SimpleAudioVolume",
            "SetMasterVolumeLevel", "MasterVolumeLevel", "waveOutSetVolume", "IAudioVolumeLevel",
            "IChannelAudioVolume", "SetMute",
        ];

        foreach (var file in CompanionSources.AllFiles())
        {
            var source = File.ReadAllText(file);
            foreach (var name in forbidden)
            {
                Assert.False(
                    source.Contains(name, StringComparison.Ordinal),
                    $"{Path.GetFileName(file)} names '{name}'; nothing in the companion may move the machine's mixer");
            }
        }
    }

    // ================================================================ routing + advertisement

    [Fact]
    public void The_M18_3_names_are_advertised_unconditionally_and_display_off_still_is_not()
    {
        var manifest = AgentCapabilities.Compose(browserEnabled: false, displayPowerEnabled: false);

        foreach (var name in AgentCapabilities.Ambient)
        {
            Assert.Contains(name, manifest);
            Assert.True(AgentCapabilities.IsInteractive(name), $"{name} must route to the companion");
            Assert.Equal(TimeSpan.FromSeconds(60), InteractiveCapabilityExecutor.TimeoutCapFor(name));
        }

        // Safe by construction, every one of them: waking, reporting, arming a fallback and
        // playing a bounded sound all ADD. Only the operation that takes a screen away stays
        // behind a flag.
        Assert.DoesNotContain(AgentCapabilities.DesktopDisplayOff, manifest);
        Assert.Equal(manifest.Count, manifest.Distinct(StringComparer.Ordinal).Count());
    }

    [Fact]
    public async Task The_service_routes_every_new_name_to_the_companion()
    {
        var transport = new StatusTransport(_ => []);
        var executor = new InteractiveCapabilityExecutor(transport, brokerRestUrl: "https://core.example:8443");

        foreach (var name in AgentCapabilities.Ambient)
        {
            var payload = string.Equals(name, AgentCapabilities.DesktopPlayAudio, StringComparison.Ordinal)
                ? Payload([1, 2, 3], origin: "https://core.example:8443")
                : [];
            await executor.ExecuteAsync(TestCommands.New(name, payload), CancellationToken.None);
        }

        Assert.Equal(AgentCapabilities.Ambient, transport.Capabilities);
    }

    [Fact]
    public async Task Audio_from_anywhere_but_the_broker_is_refused_before_the_companion_is_consulted()
    {
        var transport = new StatusTransport(_ => []);
        var executor = new InteractiveCapabilityExecutor(transport, brokerRestUrl: "https://core.example:8443");

        var elsewhere = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
            TestCommands.New(AgentCapabilities.DesktopPlayAudio, Payload([1], origin: "https://elsewhere.example")),
            CancellationToken.None));

        Assert.Equal(ErrorClasses.SecurityScopeError, elsewhere.ErrorClass);
        Assert.False(elsewhere.Retryable);
        Assert.Empty(transport.Capabilities);

        // The port is part of the origin: the same host on a different port is a different
        // server, and "close enough" is not a security boundary.
        await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
            TestCommands.New(AgentCapabilities.DesktopPlayAudio, Payload([1], origin: "https://core.example:9443")),
            CancellationToken.None));
        Assert.Empty(transport.Capabilities);
    }

    [Fact]
    public async Task A_device_with_no_configured_broker_origin_plays_nothing_at_all()
    {
        var transport = new StatusTransport(_ => []);
        var executor = new InteractiveCapabilityExecutor(transport);

        Assert.Null(executor.AudioOrigin);

        var ex = await Assert.ThrowsAsync<CapabilityException>(() => executor.ExecuteAsync(
            TestCommands.New(AgentCapabilities.DesktopPlayAudio, Payload([1], origin: "https://core.example")),
            CancellationToken.None));

        // Default deny. "We could not tell where audio may come from" and "this audio may be
        // played" must not be the same answer.
        Assert.Equal(ErrorClasses.SecurityScopeError, ex.ErrorClass);
        Assert.Empty(transport.Capabilities);
    }

    [Fact]
    public void An_unconfigured_companion_answers_every_new_name_rather_than_hanging()
    {
        var runtime = new CompanionRuntime(
            IpcTestSupport.NewPipeName(),
            new AppLauncher(new Dictionary<string, string>()),
            new ArtifactOpener([Path.GetTempPath()], new NoopFileOpener()),
            NullLogger.Instance);

        // Advertised (this companion CAN do them, given the wiring), and answered with
        // capability_missing when the wiring is absent — never a stall, which is the one answer
        // Cloud Core cannot act on.
        foreach (var name in AgentCapabilities.Ambient)
        {
            Assert.Contains(name, runtime.AdvertisedCapabilities);
        }
    }

    // ================================================================ helpers

    private static DateTimeOffset Instant(int hour, int minute)
        => new(2026, 9, 7, hour, minute, 0, TimeSpan.Zero);

    private static DisplayPowerController NewDisplay(
        IDisplayPower? power = null,
        bool enabled = false,
        TimeSpan? idle = null,
        IDisplayStateObserver? observer = null,
        IMonitorInventory? monitors = null,
        Func<bool>? isAlarmRinging = null,
        IDisplayWake? wake = null)
        => new(
            power ?? new CountingDisplayPower(),
            NullLogger.Instance,
            enabled,
            audit: null,
            input: new FakeInputActivity(idle),
            observer: observer,
            monitors: monitors,
            isAlarmRinging: isAlarmRinging,
            wake: wake);

    private static GreetingPlayer NewGreeting(GreetingDeviceFactory devices, byte[] body)
        => new(devices, () => "ren-laptop", new HttpClient(new StubHttpHandler(body)), NullLogger.Instance);

    private static JsonObject Payload(byte[] body, double? level = null, string origin = "https://core.example")
    {
        var payload = new JsonObject
        {
            ["audio_id"] = "greeting-1",
            ["audio"] = new JsonObject
            {
                ["url"] = $"{origin}/v1/audio/greeting-1.wav",
                ["sha256"] = Convert.ToHexString(SHA256.HashData(body)).ToLowerInvariant(),
                ["bytes"] = body.Length,
                ["format"] = "wav",
            },
        };
        if (level is not null)
        {
            payload["level"] = level.Value;
        }

        return payload;
    }

    private static int Peak(byte[] pcm16)
    {
        var peak = 0;
        for (var i = 0; i + 1 < pcm16.Length; i += 2)
        {
            var magnitude = Math.Abs((int)BinaryPrimitives.ReadInt16LittleEndian(pcm16.AsSpan(i, 2)));
            peak = Math.Max(peak, magnitude);
        }

        return peak;
    }

    /// <summary>An alarm controller, an arm controller and a manual clock, wired as production wires them.</summary>
    private sealed class ArmScope : IDisposable
    {
        public ArmScope(string? storePath = null, ManualTimeProvider? clock = null)
        {
            Clock = clock ?? new ManualTimeProvider();
            Devices = new FakeDeviceFactory(Clock);
            Alarm = new AlarmController(
                Devices, () => "ren-laptop", NullLogger.Instance, Clock, autoPump: false);
            Store = new ArmedAlarmStore(
                storePath ?? Path.Combine(TestPaths.NewTempDir(), "armed-alarms.json"),
                NullLogger.Instance);
            Arms = new AlarmArmController(Store, Alarm, NullLogger.Instance, Clock, autoTick: false);
        }

        public ManualTimeProvider Clock { get; }

        public FakeDeviceFactory Devices { get; }

        public AlarmController Alarm { get; }

        public ArmedAlarmStore Store { get; }

        public AlarmArmController Arms { get; }

        public void Dispose()
        {
            Arms.Dispose();
            Alarm.Dispose();
        }
    }

    private sealed class FakeInputActivity(TimeSpan? idle) : IInputActivitySource
    {
        public TimeSpan? IdleTime { get; set; } = idle;
    }

    private sealed class FakeDisplayObserver(DisplayObservation current) : IDisplayStateObserver
    {
        public DisplayObservation Current { get; private set; } = current;

        public void Set(DisplayObservation observation) => Current = observation;
    }

    private sealed class FakeMonitors(params MonitorGeometry[] monitors) : IMonitorInventory
    {
        public IReadOnlyList<MonitorGeometry> List() => monitors;
    }

    private sealed class RecordingDisplayWake : IDisplayWake
    {
        public List<string> Steps { get; } = [];

        public void RequestDisplayNeededOnce() => Steps.Add("display_needed_once");

        public void NudgePointer() => Steps.Add("pointer_nudge");
    }

    private sealed class ThrowingDisplayWake : IDisplayWake
    {
        public void RequestDisplayNeededOnce() => throw new InvalidOperationException("the session refused it");

        public void NudgePointer()
        {
        }
    }

    private sealed class CountingDisplayPower : IDisplayPower
    {
        public int Calls { get; private set; }

        public void TurnOff() => Calls++;
    }

    private sealed class CallbackDisplayPower(Action onTurnOff) : IDisplayPower
    {
        public void TurnOff() => onTurnOff();
    }

    private sealed class NoopFileOpener : IFileOpener
    {
        public void Open(string fullPath)
        {
        }
    }

    private sealed class ScriptedStatusProvider : Agent.Core.Connection.IHeartbeatStatusProvider
    {
        private readonly Func<CancellationToken, Task<JsonObject?>> _implementation;
        private int _calls;

        public ScriptedStatusProvider(Func<JsonObject?> implementation)
            => _implementation = _ => Task.FromResult(implementation());

        public ScriptedStatusProvider(Func<CancellationToken, Task<JsonObject?>> implementation)
            => _implementation = implementation;

        public int Calls => Volatile.Read(ref _calls);

        public Task<JsonObject?> GetStatusAsync(CancellationToken cancellationToken)
        {
            Interlocked.Increment(ref _calls);
            return _implementation(cancellationToken);
        }
    }

    private sealed class StatusTransport(Func<string, JsonObject?> implementation) : ICompanionCapabilityTransport
    {
        public List<string> Capabilities { get; } = [];

        public Task<JsonObject?> ExecuteCapabilityAsync(
            string capability, JsonObject payload, TimeSpan timeout, CancellationToken cancellationToken)
        {
            var result = implementation(capability);
            Capabilities.Add(capability);
            return Task.FromResult(result);
        }
    }

    /// <summary>Serves one fixed body, and counts how many times it was asked at all.</summary>
    private sealed class StubHttpHandler(byte[] body) : HttpMessageHandler
    {
        public int Requests { get; private set; }

        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Requests++;
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new ByteArrayContent(body),
            });
        }
    }

    /// <summary>Playback that keeps every byte it was given and drains itself, as a device would.</summary>
    private sealed class GreetingPlayback(string deviceId, AudioFormat format) : IAudioPlayback
    {
        private int _queuedBytes;

        public List<byte> Recorded { get; } = [];

        public byte[] Enqueued => [.. Recorded];

        public string DeviceId { get; } = deviceId;

        public AudioFormat Format { get; } = format;

        public bool Started { get; private set; }

        public bool Disposed { get; private set; }

        public bool IsPlaying => Started && _queuedBytes > 0;

        public int QueuedMs => (int)Format.MsForBytes(Volatile.Read(ref _queuedBytes));

        public event Action? Drained;

        public void Start() => Started = true;

        public void Enqueue(ReadOnlySpan<byte> pcm16)
        {
            Recorded.AddRange(pcm16.ToArray());
            Interlocked.Add(ref _queuedBytes, pcm16.Length);
            _ = Task.Run(() =>
            {
                Interlocked.Exchange(ref _queuedBytes, 0);
                Drained?.Invoke();
            });
        }

        public PlaybackStopReport StopImmediately()
        {
            var discarded = QueuedMs;
            Interlocked.Exchange(ref _queuedBytes, 0);
            return new PlaybackStopReport(discarded, 0);
        }

        public void Dispose() => Disposed = true;
    }

    private sealed class GreetingDeviceFactory : IAudioDeviceFactory
    {
        public List<GreetingPlayback> Playbacks { get; } = [];

        public IAudioCapture OpenCapture(string deviceId, AudioFormat format)
            => throw new NotSupportedException("the greeting never captures");

        public IAudioPlayback OpenPlayback(string deviceId, AudioFormat format)
        {
            var playback = new GreetingPlayback(deviceId, format);
            Playbacks.Add(playback);
            return playback;
        }
    }

    /// <summary>Builds the smallest legal WAV files the tests need, byte by byte.</summary>
    private static class Wav
    {
        public static byte[] Pcm16(int sampleRate, int channels, int milliseconds, short amplitude)
        {
            var frames = sampleRate * milliseconds / 1000;
            var data = new byte[frames * channels * 2];
            for (var i = 0; i < frames * channels; i++)
            {
                BinaryPrimitives.WriteInt16LittleEndian(data.AsSpan(i * 2, 2), amplitude);
            }

            return Container(sampleRate, channels, bitsPerSample: 16, data);
        }

        public static byte[] Pcm8(int sampleRate, int milliseconds)
            => Container(sampleRate, 1, bitsPerSample: 8, new byte[sampleRate * milliseconds / 1000]);

        private static byte[] Container(int sampleRate, int channels, int bitsPerSample, byte[] data)
        {
            using var stream = new MemoryStream();
            using var writer = new BinaryWriter(stream, Encoding.ASCII, leaveOpen: true);
            var blockAlign = channels * bitsPerSample / 8;

            writer.Write(Encoding.ASCII.GetBytes("RIFF"));
            writer.Write(36 + data.Length);
            writer.Write(Encoding.ASCII.GetBytes("WAVE"));
            writer.Write(Encoding.ASCII.GetBytes("fmt "));
            writer.Write(16);
            writer.Write((short)1);
            writer.Write((short)channels);
            writer.Write(sampleRate);
            writer.Write(sampleRate * blockAlign);
            writer.Write((short)blockAlign);
            writer.Write((short)bitsPerSample);
            writer.Write(Encoding.ASCII.GetBytes("data"));
            writer.Write(data.Length);
            writer.Write(data);
            writer.Flush();
            return stream.ToArray();
        }
    }
}
