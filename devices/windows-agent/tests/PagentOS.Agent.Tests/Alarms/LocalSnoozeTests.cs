using System.Globalization;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.Companion.Audio.Fakes;
using PagentOS.Companion.Audio.Listening;
using PagentOS.Companion.Audio.Timing;
using PagentOS.SessionCompanion;
using PagentOS.SessionCompanion.Notify;
using PagentOS.SessionCompanion.Voice;
using Xunit;

namespace PagentOS.Agent.Tests.Alarms;

/// <summary>
/// B47 (B13 requirement 259's local trigger) and row 253: a ringing alarm snoozed on the device
/// without the Cloud Core, on the cloud's own terms, reported once with the instant it will ring
/// again; and the other offline commands' executor.
/// </summary>
public sealed class LocalSnoozeTests : IDisposable
{
    private readonly ManualTimeProvider _clock = new();
    private readonly AlarmController _alarm;
    private readonly string _storePath;
    private readonly AlarmArmController _arms;
    private readonly RecordingToasts _toasts = new();

    public LocalSnoozeTests()
    {
        var devices = new FakeDeviceFactory(_clock);
        _alarm = new AlarmController(devices, () => "ren-laptop", NullLogger.Instance, _clock, autoPump: false);
        _storePath = Path.Combine(TestPaths.NewTempDir(), "armed-alarms.json");
        _arms = new AlarmArmController(new ArmedAlarmStore(_storePath, NullLogger.Instance), _alarm, NullLogger.Instance, _clock, autoTick: false);
    }

    public void Dispose()
    {
        _arms.Dispose();
        _alarm.Dispose();
    }

    private static JsonObject Ring(string id, int? minutes = 9, int? left = 5)
    {
        var payload = new JsonObject { ["alarm_id"] = id, ["label"] = "Kalk", ["max_duration_s"] = 120 };
        if (minutes is not null)
        {
            payload["snooze_minutes"] = minutes;
        }

        if (left is not null)
        {
            payload["snoozes_left"] = left;
        }

        return payload;
    }

    [Fact]
    public void A_snooze_stops_the_ring_re_arms_the_same_alarm_on_the_clouds_terms_and_is_reported_once()
    {
        _alarm.Start(Ring("wake-1"));

        var outcome = _arms.SnoozeRinging("offline_voice");

        Assert.True(outcome.Snoozed);
        Assert.False(_alarm.IsRinging);
        Assert.Equal(_clock.GetUtcNow().AddMinutes(9), outcome.Until);
        Assert.Equal(1, _arms.ArmedCount);
        Assert.Equal(_clock.GetUtcNow().AddMinutes(9).AddSeconds(AlarmArmController.DefaultGraceSeconds), _arms.NextFireLocalAt);

        var report = _arms.DrainLocallySnoozed();
        var entry = Assert.Single(report)!.AsObject();
        Assert.Equal(DeviceVoiceContract.LocalSnoozeEntryKeys, entry.Select(p => p.Key));
        Assert.Equal("wake-1", entry["alarm_id"]!.GetValue<string>());
        Assert.Equal(outcome.Until, DateTimeOffset.Parse(entry["until"]!.GetValue<string>(), CultureInfo.InvariantCulture));
        Assert.Empty(_arms.DrainLocallySnoozed());
    }

    [Fact]
    public void The_re_armed_alarm_rings_again_with_one_snooze_fewer_and_survives_a_restart()
    {
        _alarm.Start(Ring("wake-2", minutes: 5, left: 1));
        Assert.True(_arms.SnoozeRinging("offline_voice").Snoozed);

        // A companion restart between the snooze and the ring keeps the terms.
        var reloaded = new ArmedAlarmStore(_storePath, NullLogger.Instance).All.Single();
        Assert.Equal(5, reloaded.SnoozeMinutes);
        Assert.Equal(0, reloaded.SnoozesLeft);

        _clock.Advance(TimeSpan.FromMinutes(5) + TimeSpan.FromSeconds(AlarmArmController.DefaultGraceSeconds));
        Assert.Equal(1, _arms.Tick());
        var ringing = _alarm.RingingPayload!;
        Assert.Equal("wake-2", ringing["alarm_id"]!.GetValue<string>());
        Assert.Equal(0, ringing["snoozes_left"]!.GetValue<int>());

        // No snoozes left: the cloud's own limit, applied here without the cloud.
        var refused = _arms.SnoozeRinging("offline_voice");
        Assert.False(refused.Snoozed);
        Assert.Equal(AlarmArmController.SnoozeLimitReached, refused.Detail);
        Assert.True(_alarm.IsRinging);
    }

    /// <summary>
    /// Regression (found by B47): when the cloud gives up on an unreachable device it queues a
    /// disarm with a short expiry. A network that comes back inside that window used to deliver
    /// it after the owner had snoozed offline - and the disarm deleted the snoozed wake-up.
    /// </summary>
    [Fact]
    public void A_disarm_issued_before_the_cloud_knew_of_a_local_snooze_does_not_delete_it()
    {
        _alarm.Start(Ring("wake-6"));
        Assert.True(_arms.SnoozeRinging("offline_voice").Snoozed);

        var stale = _arms.Disarm(new JsonObject { ["alarm_id"] = "wake-6" });
        var staleAll = _arms.Disarm([]);

        Assert.Equal(1, _arms.ArmedCount);
        Assert.Equal("wake-6", Assert.Single(stale["kept_local_snooze"]!.AsArray())!.GetValue<string>());
        Assert.Single(staleAll["kept_local_snooze"]!.AsArray());

        // Once the snooze has been reported, the cloud's decisions apply again.
        Assert.Single(_arms.DrainLocallySnoozed());
        var informed = _arms.Disarm(new JsonObject { ["alarm_id"] = "wake-6" });
        Assert.True(informed["was_armed"]!.GetValue<bool>());
        Assert.Empty(informed["kept_local_snooze"]!.AsArray());
        Assert.Equal(0, _arms.ArmedCount);
    }

    [Fact]
    public void Without_the_clouds_terms_the_device_does_not_invent_its_own_and_the_alarm_keeps_ringing()
    {
        _alarm.Start(Ring("wake-3", minutes: null, left: null));

        var outcome = _arms.SnoozeRinging("offline_voice");

        Assert.False(outcome.Snoozed);
        Assert.Equal(AlarmArmController.SnoozeTermsUnknown, outcome.Detail);
        Assert.True(_alarm.IsRinging);
        Assert.Equal(0, _arms.ArmedCount);
        Assert.Empty(_arms.DrainLocallySnoozed());
    }

    [Fact]
    public void Nothing_ringing_is_nothing_to_snooze()
    {
        var outcome = _arms.SnoozeRinging("tray");
        Assert.False(outcome.Snoozed);
        Assert.Equal(AlarmArmController.SnoozeNotRinging, outcome.Detail);
    }

    [Fact]
    public void An_arm_carries_the_snooze_terms_into_its_local_ring_and_refuses_malformed_ones()
    {
        _arms.Arm(new JsonObject
        {
            ["alarm_id"] = "wake-4",
            ["fire_at"] = _clock.GetUtcNow().ToString("O", CultureInfo.InvariantCulture),
            ["grace_s"] = 0,
            ["snooze_minutes"] = 7,
            ["snoozes_left"] = 3,
            ["is_test"] = true,
        });
        Assert.Equal(1, _arms.Tick());
        var ringing = _alarm.RingingPayload!;
        Assert.Equal(7, ringing["snooze_minutes"]!.GetValue<int>());
        Assert.Equal(3, ringing["snoozes_left"]!.GetValue<int>());
        Assert.True(ringing["is_test"]!.GetValue<bool>());

        foreach (var (key, value) in new (string, JsonNode)[] { ("snooze_minutes", 0), ("snooze_minutes", "9"), ("snoozes_left", -1), ("snoozes_left", 1.5) })
        {
            var ex = Assert.Throws<CapabilityException>(() => _arms.Arm(new JsonObject
            {
                ["alarm_id"] = "bad",
                ["fire_at"] = _clock.GetUtcNow().AddHours(1).ToString("O", CultureInfo.InvariantCulture),
                [key] = value,
            }));
            Assert.Equal(ErrorClasses.ValidationError, ex.ErrorClass);
        }
    }

    [Fact]
    public void The_offline_executor_stops_snoozes_and_tells_the_time_and_says_so_truthfully()
    {
        var commands = new OfflineVoiceCommands(_alarm, _arms, _toasts, _clock, NullLogger.Instance);
        Assert.False(commands.AlarmRinging);
        Assert.Equal("not_ringing", commands.Execute(DeviceVoiceContract.CommandAlarmStop).Detail);

        _alarm.Start(Ring("wake-5"));
        Assert.True(commands.AlarmRinging);
        var snooze = commands.Execute(DeviceVoiceContract.CommandAlarmSnooze);
        Assert.True(snooze.Executed);
        Assert.False(_alarm.IsRinging);

        _alarm.Start(Ring("wake-5"));
        var stop = commands.Execute(DeviceVoiceContract.CommandAlarmStop);
        Assert.True(stop.Executed);
        Assert.False(_alarm.IsRinging);
        // Stopped means stopped: the snooze's local arm is consumed too, so it does not ring later.
        Assert.Equal(0, _arms.ArmedCount);

        var time = commands.Execute(DeviceVoiceContract.CommandTimeTell);
        Assert.True(time.Executed);
        var local = TimeZoneInfo.ConvertTime(_clock.GetUtcNow(), _clock.LocalTimeZone);
        Assert.Equal("Saat " + local.ToString("HH:mm", CultureInfo.InvariantCulture), Assert.Single(_toasts.Shown).Title);

        Assert.False(commands.Execute("files.delete").Executed);
        Assert.False(new OfflineVoiceCommands(null, null, null, _clock, NullLogger.Instance).Execute(DeviceVoiceContract.CommandTimeTell).Executed);
    }

    private sealed class RecordingToasts : IToastSink
    {
        public List<ToastRequest> Shown { get; } = [];

        public ToastOutcome Show(ToastRequest request)
        {
            Shown.Add(request);
            return ToastOutcome.Ok();
        }
    }
}
