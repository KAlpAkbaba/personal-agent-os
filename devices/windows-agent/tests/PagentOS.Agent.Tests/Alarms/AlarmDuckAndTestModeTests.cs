using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Tests.Support;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Timing;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests.Alarms;

/// <summary>
/// B13 requirements 269 and 286: the tone steps back while the greeting speaks, and a test
/// alarm is unmistakable as one all the way down to this device's audit row.
/// </summary>
public sealed class AlarmDuckAndTestModeTests
{
    /// <summary>A playback that KEEPS the samples, so the duck can be measured rather than
    /// asserted about. `FakePlayback` only counts bytes, and a test that read a byte count
    /// would pass with the level ignored entirely.</summary>
    private sealed class RecordingPlayback(string deviceId, AudioFormat format) : IAudioPlayback
    {
        public List<byte> Recorded { get; } = [];

        public string DeviceId { get; } = deviceId;

        public AudioFormat Format { get; } = format;

        public bool Started { get; private set; }

        public bool Disposed { get; private set; }

        public bool IsPlaying => Started;

        public int QueuedMs => 0;

        public event Action? Drained;

        public void Start() => Started = true;

        public void Enqueue(ReadOnlySpan<byte> pcm16) => Recorded.AddRange(pcm16.ToArray());

        public PlaybackStopReport StopImmediately()
        {
            Drained?.Invoke();
            return new PlaybackStopReport(0, 0);
        }

        public void Dispose() => Disposed = true;
    }

    private sealed class RecordingDeviceFactory : IAudioDeviceFactory
    {
        public List<RecordingPlayback> Playbacks { get; } = [];

        public IAudioCapture OpenCapture(string deviceId, AudioFormat format)
            => throw new NotSupportedException("the alarm tone never captures");

        public IAudioPlayback OpenPlayback(string deviceId, AudioFormat format)
        {
            var playback = new RecordingPlayback(deviceId, format);
            Playbacks.Add(playback);
            return playback;
        }
    }

    private sealed class Scope : IDisposable
    {
        public Scope()
        {
            Clock = new ManualTimeProvider();
            Devices = new RecordingDeviceFactory();
            Alarm = new AlarmController(Devices, () => "ren-laptop", NullLogger.Instance, Clock, autoPump: false);
            Store = new ArmedAlarmStore(
                Path.Combine(TestPaths.NewTempDir(), "armed-alarms.json"), NullLogger.Instance);
            Arms = new AlarmArmController(Store, Alarm, NullLogger.Instance, Clock, autoTick: false);
        }

        public ManualTimeProvider Clock { get; }

        public RecordingDeviceFactory Devices { get; }

        public AlarmController Alarm { get; }

        public ArmedAlarmStore Store { get; }

        public AlarmArmController Arms { get; }

        public void Dispose()
        {
            Arms.Dispose();
            Alarm.Dispose();
        }
    }

    /// <summary>
    /// Pumps a few chunks and returns the loudest LEVEL that reached the endpoint, as a
    /// fraction of full scale — <see cref="WakeTone.PeakLevel"/>, the generator's own
    /// test-facing reading of its own output. A raw sample count would have to be compared
    /// against whatever the ramp happened to be at, which is a moving number and not the
    /// claim: the duck is a level, not a fraction of wherever the ramp had reached.
    /// </summary>
    private static double PumpToPeakLevel(Scope scope, int chunks = 40)
    {
        for (var i = 0; i < chunks; i++)
        {
            scope.Clock.Advance(TimeSpan.FromSeconds(1));
            scope.Alarm.Pump();
        }

        var playback = scope.Devices.Playbacks[^1];
        var level = WakeTone.PeakLevel([.. playback.Recorded]);
        playback.Recorded.Clear();
        return level;
    }

    // -------------------------------------------------------------------- 269: ducking

    [Fact]
    public void The_tone_drops_while_something_else_is_speaking()
    {
        // The defect: the cloud has ducked the owner's MUSIC since M18.3 and could never duck
        // this, because the tone is generated inside the companion and has no per-stream
        // volume the cloud can address. On the tone-fallback path the greeting played over a
        // ringing alarm at full level - the one sentence the owner was meant to hear was the
        // one thing in the room they could not.
        using var scope = new Scope();
        scope.Alarm.Start(new JsonObject { ["alarm_id"] = "wake-269" });
        var loud = PumpToPeakLevel(scope);
        Assert.True(loud > AlarmController.DuckLevel, "the ramp must reach a level worth ducking");

        using (scope.Alarm.Duck("greeting"))
        {
            var ducked = PumpToPeakLevel(scope, chunks: 4);

            Assert.True(scope.Alarm.Ducked);
            // Not merely "lower": AT the declared level. A duck that shaved five per cent
            // would satisfy "quieter" and still leave the greeting inaudible under the tone.
            Assert.InRange(ducked, 0.0, AlarmController.DuckLevel + 0.01);
        }
    }

    [Fact]
    public void The_tone_comes_back_when_the_speaking_stops()
    {
        using var scope = new Scope();
        scope.Alarm.Start(new JsonObject { ["alarm_id"] = "wake-269" });
        PumpToPeakLevel(scope);

        using (scope.Alarm.Duck("greeting"))
        {
            PumpToPeakLevel(scope, chunks: 4);
        }

        Assert.False(scope.Alarm.Ducked);
        Assert.True(PumpToPeakLevel(scope, chunks: 4) > AlarmController.DuckLevel);
    }

    [Fact]
    public void Two_overlapping_greetings_do_not_let_the_first_one_restore_full_volume()
    {
        // Reference counted on purpose: a snooze confirmation arriving while the greeting is
        // still speaking would otherwise end the duck under it.
        using var scope = new Scope();
        scope.Alarm.Start(new JsonObject { ["alarm_id"] = "wake-269" });

        var first = scope.Alarm.Duck("greeting");
        var second = scope.Alarm.Duck("confirmation");
        first.Dispose();

        Assert.True(scope.Alarm.Ducked);
        second.Dispose();
        Assert.False(scope.Alarm.Ducked);
    }

    [Fact]
    public void Disposing_a_duck_twice_does_not_unduck_somebody_else_s()
    {
        using var scope = new Scope();
        var first = scope.Alarm.Duck("greeting");
        using var second = scope.Alarm.Duck("confirmation");

        first.Dispose();
        first.Dispose();

        Assert.True(scope.Alarm.Ducked);
    }

    // ------------------------------------------------------------------ 286: test mode

    [Fact]
    public void An_armed_test_alarm_says_so_in_its_own_record()
    {
        using var scope = new Scope();

        scope.Arms.Arm(new JsonObject
        {
            ["alarm_id"] = "wake-286",
            ["fire_at"] = (scope.Clock.GetUtcNow() + TimeSpan.FromMinutes(5)).ToString("O"),
            ["grace_s"] = 60,
            ["is_test"] = true,
        });

        Assert.True(scope.Store.All.Single().IsTest);
    }

    [Fact]
    public void An_ordinary_alarm_is_not_marked_a_test()
    {
        using var scope = new Scope();

        scope.Arms.Arm(new JsonObject
        {
            ["alarm_id"] = "wake-286",
            ["fire_at"] = (scope.Clock.GetUtcNow() + TimeSpan.FromMinutes(5)).ToString("O"),
            ["grace_s"] = 60,
        });

        Assert.False(scope.Store.All.Single().IsTest);
    }

    [Fact]
    public void The_mark_survives_the_restart_the_local_fallback_exists_for()
    {
        // The ring that matters most for this is the LOCAL one: it happens when the cloud is
        // not there to label it afterwards, and it may happen after a companion restart —
        // which is the whole reason the arm is on disk.
        var path = Path.Combine(TestPaths.NewTempDir(), "armed-alarms.json");
        var clock = new ManualTimeProvider();
        using (var alarm = new AlarmController(
                   new RecordingDeviceFactory(), () => "ren-laptop", NullLogger.Instance, clock, autoPump: false))
        {
            var store = new ArmedAlarmStore(path, NullLogger.Instance);
            using var arms = new AlarmArmController(store, alarm, NullLogger.Instance, clock, autoTick: false);
            arms.Arm(new JsonObject
            {
                ["alarm_id"] = "wake-286",
                ["fire_at"] = (clock.GetUtcNow() + TimeSpan.FromMinutes(5)).ToString("O"),
                ["grace_s"] = 60,
                ["is_test"] = true,
            });
        }

        var reloaded = new ArmedAlarmStore(path, NullLogger.Instance);

        Assert.True(reloaded.All.Single().IsTest);
    }
}
