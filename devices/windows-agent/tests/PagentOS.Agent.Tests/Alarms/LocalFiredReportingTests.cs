using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.Companion.Audio.Fakes;
using PagentOS.Companion.Audio.Timing;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests.Alarms;

/// <summary>
/// B13 requirements 282 and 283: this device tells Cloud Core WHICH alarm it rang on its own,
/// and tells it in time to matter.
/// </summary>
/// <remarks>
/// <para>Before this the device kept a counter. A counter cannot answer the question the cloud
/// actually has, which is not "how many rang" but "which one" — so the cloud could not tell
/// that the alarm it was about to fire had already woken the owner, and it rang a second time.
/// Requirement 283 records that as "the cloud can fire a second time half an hour later".</para>
/// <para>Two channels, on purpose, because they answer at two speeds. The heartbeat's
/// <c>local_alarm_fired</c> is eventual — it leaves on the next report. <see
/// cref="AlarmArmController.Disarm"/>'s <c>already_fired</c> is synchronous, and disarm is the
/// FIRST thing the cloud does when it fires: it is the one moment the cloud can learn this
/// before deciding whether to ring.</para>
/// </remarks>
public sealed class LocalFiredReportingTests
{
    private sealed class Scope : IDisposable
    {
        public Scope()
        {
            Clock = new ManualTimeProvider();
            Devices = new FakeDeviceFactory(Clock);
            Alarm = new AlarmController(Devices, () => "ren-laptop", NullLogger.Instance, Clock, autoPump: false);
            Store = new ArmedAlarmStore(
                Path.Combine(TestPaths.NewTempDir(), "armed-alarms.json"), NullLogger.Instance);
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

    private static void ArmFor(Scope scope, string id, TimeSpan ahead, int graceSeconds = 60)
    {
        scope.Arms.Arm(new JsonObject
        {
            ["alarm_id"] = id,
            ["fire_at"] = (scope.Clock.GetUtcNow() + ahead).ToString("O"),
            ["grace_s"] = graceSeconds,
            ["label"] = "Sabah alarmı",
        });
    }

    /// <summary>Advances past fire_at + grace and ticks, so the local fallback rings.</summary>
    private static void RingLocally(Scope scope, string id)
    {
        ArmFor(scope, id, TimeSpan.FromMinutes(1));
        scope.Clock.Advance(TimeSpan.FromMinutes(2));
        Assert.Equal(1, scope.Arms.Tick());
    }

    // ------------------------------------------------------- 282: which one, not how many

    [Fact]
    public void A_local_ring_records_the_alarm_s_own_id()
    {
        using var scope = new Scope();

        RingLocally(scope, "wake-282");

        Assert.Equal(["wake-282"], scope.Arms.LocallyFiredIds);
    }

    [Fact]
    public void Reporting_drains_the_list_so_one_ring_is_reported_once()
    {
        // A field that only grew would make one local ring look like a ring on every
        // heartbeat until the process restarted, and the cloud would keep re-reconciling an
        // alarm it had already closed.
        using var scope = new Scope();
        RingLocally(scope, "wake-282");

        Assert.Equal(["wake-282"], scope.Arms.DrainLocallyFiredIds());
        Assert.Empty(scope.Arms.DrainLocallyFiredIds());
        Assert.Empty(scope.Arms.LocallyFiredIds);
    }

    [Fact]
    public void The_status_report_carries_the_ids_and_carries_them_away()
    {
        using var scope = new Scope();
        RingLocally(scope, "wake-282");
        var reporter = new ActivityStatusReporter(
            new FixedIdle(TimeSpan.FromSeconds(3)), new NoDisplay(), () => null, scope.Arms);

        var first = reporter.Compose();
        var second = reporter.Compose();

        Assert.Equal(
            ["wake-282"],
            first[HeartbeatStatus.LocalAlarmFired]!.AsArray().Select(n => n!.GetValue<string>()));
        Assert.Empty(second[HeartbeatStatus.LocalAlarmFired]!.AsArray());
    }

    [Fact]
    public void A_device_that_rang_nothing_reports_an_empty_list_not_a_missing_key()
    {
        using var scope = new Scope();
        var reporter = new ActivityStatusReporter(
            new FixedIdle(TimeSpan.FromSeconds(3)), new NoDisplay(), () => null, scope.Arms);

        var status = reporter.Compose();

        Assert.True(status.ContainsKey(HeartbeatStatus.LocalAlarmFired));
        Assert.Empty(status[HeartbeatStatus.LocalAlarmFired]!.AsArray());
    }

    [Fact]
    public void The_field_survives_the_service_s_projection()
    {
        // `Project` drops anything not in `Fields`, so a key the schema declares and the list
        // forgets would vanish silently between the companion and the wire.
        var projected = HeartbeatStatus.Project(new JsonObject
        {
            [HeartbeatStatus.LocalAlarmFired] = new JsonArray("wake-282"),
        });

        Assert.NotNull(projected);
        Assert.Equal(
            ["wake-282"],
            projected![HeartbeatStatus.LocalAlarmFired]!.AsArray().Select(n => n!.GetValue<string>()));
    }

    // ------------------------------------------------- 283: told in time to matter

    [Fact]
    public void Disarm_tells_the_cloud_this_alarm_already_rang()
    {
        using var scope = new Scope();
        RingLocally(scope, "wake-283");

        var disarmed = scope.Arms.Disarm(new JsonObject { ["alarm_id"] = "wake-283" });

        Assert.True(disarmed["already_fired"]!.GetValue<bool>());
    }

    [Fact]
    public void Disarm_of_an_alarm_that_never_rang_says_so()
    {
        // The half that keeps the fix from becoming the bug: if this were ever true by
        // default the cloud would stand down for every alarm and none would ever ring.
        using var scope = new Scope();
        ArmFor(scope, "wake-283", TimeSpan.FromMinutes(10));

        var disarmed = scope.Arms.Disarm(new JsonObject { ["alarm_id"] = "wake-283" });

        Assert.True(disarmed["was_armed"]!.GetValue<bool>());
        Assert.False(disarmed["already_fired"]!.GetValue<bool>());
    }

    [Fact]
    public void Was_armed_false_alone_could_not_have_answered_this()
    {
        // Why `already_fired` had to be its own field: after a local ring the arm is gone, so
        // `was_armed: false` means "never armed" AND "already rang" at the same time, and the
        // cloud cannot act on an ambiguity.
        using var scope = new Scope();
        RingLocally(scope, "wake-283");

        var rang = scope.Arms.Disarm(new JsonObject { ["alarm_id"] = "wake-283" });
        var never = scope.Arms.Disarm(new JsonObject { ["alarm_id"] = "wake-never-existed" });

        Assert.Equal(rang["was_armed"]!.GetValue<bool>(), never["was_armed"]!.GetValue<bool>());
        Assert.NotEqual(rang["already_fired"]!.GetValue<bool>(), never["already_fired"]!.GetValue<bool>());
    }

    [Fact]
    public void The_fact_is_taken_by_the_disarm_not_left_behind()
    {
        // Telling the cloud twice would make a genuinely new occurrence stand down for a ring
        // that belonged to yesterday.
        using var scope = new Scope();
        RingLocally(scope, "wake-283");

        scope.Arms.Disarm(new JsonObject { ["alarm_id"] = "wake-283" });
        var again = scope.Arms.Disarm(new JsonObject { ["alarm_id"] = "wake-283" });

        Assert.False(again["already_fired"]!.GetValue<bool>());
    }

    [Fact]
    public void Arming_the_same_id_again_is_a_new_occurrence()
    {
        // A recurring alarm keeps its id. Yesterday's local ring must not make the cloud
        // stand down for tomorrow's — which would be one silent morning per late night.
        using var scope = new Scope();
        RingLocally(scope, "wake-daily");

        ArmFor(scope, "wake-daily", TimeSpan.FromHours(24));
        var disarmed = scope.Arms.Disarm(new JsonObject { ["alarm_id"] = "wake-daily" });

        Assert.False(disarmed["already_fired"]!.GetValue<bool>());
    }

    private sealed class FixedIdle(TimeSpan idle) : IInputActivitySource
    {
        public TimeSpan? IdleTime => idle;
    }

    private sealed class NoDisplay : IDisplayStateObserver
    {
        public DisplayObservation Current => new("unknown", null);
    }
}
