using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion;
using PagentOS.SessionCompanion.Camera;
using Xunit;

namespace PagentOS.Agent.Tests.Camera;

/// <summary>
/// B48 (rows 300, 307, 308, 326, 327, 671): the device-local presence provider. Every frame here
/// is made of numbers; the owner's camera is never opened by a test.
/// </summary>
public sealed class CameraPresenceTests : IDisposable
{
    private static readonly DateTimeOffset Start = new(2026, 9, 16, 23, 0, 0, TimeSpan.Zero);

    private readonly string _dir = Path.Combine(Path.GetTempPath(), "pagentos-camera-" + Guid.NewGuid().ToString("N"));

    public void Dispose()
    {
        try
        {
            Directory.Delete(_dir, recursive: true);
        }
        catch (DirectoryNotFoundException)
        {
        }
    }

    private static CameraOptions Options(bool enabled = true) => new()
    {
        FrameSpacing = TimeSpan.Zero,
        FramesPerSample = 4,
        RestingAfter = TimeSpan.FromMinutes(5),
        EnabledOnDevice = enabled,
    };

    private sealed record Rig(
        CameraPresenceMonitor Monitor,
        FakeCameraSource Source,
        RecordingCameraIndicator Indicator,
        FakeIdle Idle,
        FakeMedia Media,
        FakeConsent Consent,
        SteppedClock Clock);

    private Rig NewRig(CameraOptions? options = null, TimeSpan? idle = null, AuditLog? audit = null)
    {
        var indicator = new RecordingCameraIndicator();
        var source = new FakeCameraSource(indicator);
        var fakeIdle = new FakeIdle(idle ?? TimeSpan.FromHours(1));
        var media = new FakeMedia(false);
        var consent = new FakeConsent(null);
        var clock = new SteppedClock(Start);
        var monitor = new CameraPresenceMonitor(
            source, indicator, NullLogger.Instance, options ?? Options(), consent, fakeIdle, media, clock, audit);
        return new Rig(monitor, source, indicator, fakeIdle, media, consent, clock);
    }

    private static JsonObject Mode(string mode, int? interval = null)
    {
        var payload = new JsonObject { ["mode"] = mode, ["reason"] = "test" };
        if (interval is not null)
        {
            payload["interval_s"] = interval;
        }

        return payload;
    }

    // ================================================================ consent (300, 671)

    [Fact]
    public async Task The_camera_starts_off_and_a_check_in_off_mode_never_opens_it()
    {
        var rig = NewRig();

        Assert.Equal(CameraModes.Off, rig.Monitor.Mode);
        Assert.False(await rig.Monitor.CheckOnceAsync(CancellationToken.None));

        Assert.Equal(0, rig.Source.Opens);
        Assert.Null(rig.Monitor.LatestObservation());
        Assert.Equal(CameraIndicatorState.Hidden, rig.Indicator.State);
        Assert.Equal("off", rig.Monitor.StatusObject()["state"]!.GetValue<string>());
    }

    [Fact]
    public async Task Windows_denying_the_camera_is_reported_by_name_and_nothing_is_opened()
    {
        var audit = new AuditLog(Path.Combine(_dir, "audit.jsonl"));
        var rig = NewRig(audit: audit);
        rig.Consent.Denied = "privacy_desktop_apps_off";
        rig.Monitor.Configure(Mode(CameraModes.Periodic));

        Assert.False(await rig.Monitor.CheckOnceAsync(CancellationToken.None));
        Assert.False(await rig.Monitor.CheckOnceAsync(CancellationToken.None));

        Assert.Equal(0, rig.Source.Opens);
        var status = rig.Monitor.StatusObject();
        Assert.Equal(CameraStates.Blocked, status["state"]!.GetValue<string>());
        Assert.Equal("privacy_desktop_apps_off", status["error"]!.GetValue<string>());
        Assert.Null(rig.Monitor.LatestObservation());

        // Audited once, not once per check.
        var lines = File.ReadAllLines(Path.Combine(_dir, "audit.jsonl"));
        Assert.Single(lines, l => l.Contains("\"camera_blocked\"", StringComparison.Ordinal));
    }

    [Theory]
    [InlineData(CameraFailure.Blocked, CameraStates.Blocked, "access_denied")]
    [InlineData(CameraFailure.Unavailable, CameraStates.Unavailable, "no_camera")]
    [InlineData(CameraFailure.Busy, CameraStates.Busy, "camera_in_use")]
    [InlineData(CameraFailure.Error, CameraStates.Error, "init_failed")]
    public async Task A_camera_that_cannot_be_opened_is_reported_honestly(CameraFailure failure, string state, string token)
    {
        var rig = NewRig();
        rig.Source.FailOpenWith = new CameraAccessException(failure, token, "fake");
        rig.Monitor.Configure(Mode(CameraModes.Continuous));

        Assert.False(await rig.Monitor.CheckOnceAsync(CancellationToken.None));

        var status = rig.Monitor.StatusObject();
        Assert.Equal(state, status["state"]!.GetValue<string>());
        Assert.Equal(token, status["error"]!.GetValue<string>());
        Assert.Null(rig.Monitor.LatestObservation());
        // Not open, so not shown as open.
        Assert.Equal(CameraIndicatorState.Armed, rig.Indicator.State);
    }

    [Fact]
    public async Task The_device_local_switch_keeps_the_camera_closed_whatever_the_cloud_asks()
    {
        var rig = NewRig(Options(enabled: false));
        rig.Monitor.Configure(Mode(CameraModes.Continuous));

        Assert.False(await rig.Monitor.CheckOnceAsync(CancellationToken.None));

        Assert.Equal(0, rig.Source.Opens);
        Assert.Equal(CameraStates.Blocked, rig.Monitor.State);
        Assert.Equal("disabled_on_device", rig.Monitor.StatusObject()["error"]!.GetValue<string>());
    }

    [Fact]
    public async Task The_owner_closing_the_camera_on_the_device_outranks_the_cloud_mode_until_re_allowed()
    {
        var rig = NewRig();
        rig.Monitor.Configure(Mode(CameraModes.Continuous));
        Assert.True(await rig.Monitor.CheckOnceAsync(CancellationToken.None));
        Assert.True(rig.Monitor.IsOpen);

        rig.Indicator.RaiseOwnerVeto(true);
        Assert.Null(rig.Monitor.LatestObservation());
        Assert.False(await rig.Monitor.CheckOnceAsync(CancellationToken.None));

        Assert.False(rig.Monitor.IsOpen);
        Assert.Equal(0, rig.Source.OpenSessions);
        Assert.Equal(CameraStates.Vetoed, rig.Monitor.State);
        // The cloud's mode is kept (so Cloud Core does not keep re-sending it) but not obeyed.
        Assert.Equal(CameraModes.Continuous, rig.Monitor.Mode);
        rig.Monitor.Configure(Mode(CameraModes.Continuous));
        Assert.False(await rig.Monitor.CheckOnceAsync(CancellationToken.None));
        Assert.Equal(1, rig.Source.Opens);

        rig.Indicator.RaiseOwnerVeto(false);
        Assert.True(await rig.Monitor.CheckOnceAsync(CancellationToken.None));
        Assert.Equal(2, rig.Source.Opens);
    }

    // ================================================================ the indicator

    [Fact]
    public async Task The_indicator_shows_open_before_the_camera_is_touched_and_until_it_is_closed()
    {
        var rig = NewRig();
        rig.Monitor.Configure(Mode(CameraModes.Periodic));

        Assert.True(await rig.Monitor.CheckOnceAsync(CancellationToken.None));

        // Open, then four frames: every touch of the device happened under "open".
        Assert.Equal(5, rig.Source.IndicatorWhenTouched.Count);
        Assert.All(rig.Source.IndicatorWhenTouched, state => Assert.Equal(CameraIndicatorState.Open, state));

        // Periodic: closed again, and the tray says "armed" (a mode is on, the camera is not).
        Assert.Equal(0, rig.Source.OpenSessions);
        Assert.Equal(CameraIndicatorState.Armed, rig.Indicator.State);
        Assert.Equal("armed", rig.Monitor.StatusObject()["indicator"]!.GetValue<string>());
        Assert.Equal(CameraStates.Idle, rig.Monitor.State);
    }

    [Fact]
    public async Task Continuous_mode_keeps_the_camera_open_and_off_closes_it_and_forgets_the_reading()
    {
        var rig = NewRig();
        rig.Monitor.Configure(Mode(CameraModes.Continuous));

        Assert.True(await rig.Monitor.CheckOnceAsync(CancellationToken.None));
        Assert.True(await rig.Monitor.CheckOnceAsync(CancellationToken.None));
        Assert.Equal(1, rig.Source.Opens);
        Assert.Equal(1, rig.Source.OpenSessions);
        Assert.Equal(CameraStates.Capturing, rig.Monitor.State);
        Assert.Equal(CameraIndicatorState.Open, rig.Indicator.State);
        Assert.NotNull(rig.Monitor.LatestObservation());

        var answer = rig.Monitor.Configure(Mode(CameraModes.Off));
        Assert.True(answer["changed"]!.GetValue<bool>());
        // The reading is gone at once, before the loop has even closed the device.
        Assert.Null(rig.Monitor.LatestObservation());

        Assert.False(await rig.Monitor.CheckOnceAsync(CancellationToken.None));
        Assert.Equal(0, rig.Source.OpenSessions);
        Assert.Equal(CameraIndicatorState.Hidden, rig.Indicator.State);
        Assert.Equal(CameraStates.Off, rig.Monitor.State);

        // Turned on again: nothing from before the "off" is reported before a new sample exists.
        rig.Monitor.Configure(Mode(CameraModes.Periodic));
        Assert.Null(rig.Monitor.LatestObservation());
    }

    // ================================================================ in memory only (328/329)

    [Fact]
    public async Task Every_frame_of_a_sample_is_zeroed_before_the_observation_is_reported()
    {
        var rig = NewRig();
        rig.Monitor.Configure(Mode(CameraModes.Periodic));

        Assert.True(await rig.Monitor.CheckOnceAsync(CancellationToken.None));

        Assert.Equal(4, rig.Source.Handed.Count);
        Assert.All(rig.Source.Handed, frame =>
        {
            Assert.True(frame.Cleared);
            Assert.True(frame.Luma.ToArray().All(b => b == 0));
        });
    }

    [Fact]
    public async Task The_observation_is_exactly_the_seven_structured_fields_from_the_camera()
    {
        var rig = NewRig();
        rig.Monitor.Configure(Mode(CameraModes.Periodic));
        await rig.Monitor.CheckOnceAsync(CancellationToken.None);

        var observation = rig.Monitor.LatestObservation()!;
        Assert.Equal(
            CameraObservation.Fields.OrderBy(k => k, StringComparer.Ordinal),
            observation.Select(p => p.Key).OrderBy(k => k, StringComparer.Ordinal));
        Assert.Equal(HeartbeatStatus.PresenceFields.OrderBy(k => k, StringComparer.Ordinal), CameraObservation.Fields.OrderBy(k => k, StringComparer.Ordinal));
        Assert.Equal("camera", observation["source"]!.GetValue<string>());
        Assert.True(observation["person_present"]!.GetValue<bool>());
        Assert.Equal("2026-09-16T23:00:00.000Z", observation["observed_at"]!.GetValue<string>());
        Assert.All(observation, pair => Assert.IsAssignableFrom<JsonValue>(pair.Value));
    }

    // ================================================================ 307 / 308 from device signals

    [Fact]
    public async Task A_still_present_owner_with_no_input_and_no_sound_reads_as_resting_after_the_threshold()
    {
        var rig = NewRig();
        rig.Monitor.Configure(Mode(CameraModes.Periodic));

        await rig.Monitor.CheckOnceAsync(CancellationToken.None);
        Assert.Equal("upright", rig.Monitor.LatestObservation()!["posture"]!.GetValue<string>());

        rig.Clock.Advance(TimeSpan.FromMinutes(4));
        await rig.Monitor.CheckOnceAsync(CancellationToken.None);
        Assert.Equal("upright", rig.Monitor.LatestObservation()!["posture"]!.GetValue<string>());

        rig.Clock.Advance(TimeSpan.FromMinutes(1));
        await rig.Monitor.CheckOnceAsync(CancellationToken.None);
        var rest = rig.Monitor.LatestObservation()!;
        Assert.Equal("resting", rest["posture"]!.GetValue<string>());
        Assert.Equal("resting", rest["awake_state"]!.GetValue<string>());
        Assert.Equal("none", rest["activity_level"]!.GetValue<string>());
        Assert.True(rest["person_present"]!.GetValue<bool>());
    }

    [Fact]
    public void A_film_is_not_sleep_sound_playing_keeps_a_still_owner_upright()
    {
        var classifier = new PresenceClassifier(Options());
        var still = new SampleSummary(4, 4, 120, 0.0);
        classifier.Classify(still, Start, TimeSpan.FromHours(1), mediaPlaying: true);

        var later = classifier.Classify(still, Start.AddMinutes(30), TimeSpan.FromHours(1), mediaPlaying: true)!;

        Assert.Equal("upright", later.Posture);
        Assert.NotEqual("resting", later.AwakeState);

        // The film ends: half an hour of watching is not half an hour of rest. Stillness
        // starts counting when the sound stops, so rest needs the full threshold again.
        var credits = classifier.Classify(still, Start.AddMinutes(31), TimeSpan.FromHours(1), mediaPlaying: false)!;
        Assert.Equal("upright", credits.Posture);
        var asleep = classifier.Classify(still, Start.AddMinutes(36), TimeSpan.FromHours(1), mediaPlaying: false)!;
        Assert.Equal("resting", asleep.Posture);
    }

    [Fact]
    public void Input_during_the_still_run_restarts_it_at_that_input()
    {
        var classifier = new PresenceClassifier(Options());
        var still = new SampleSummary(4, 4, 120, 0.0);
        classifier.Classify(still, Start, TimeSpan.FromHours(1), mediaPlaying: false);

        // Ten minutes of camera stillness, but the keyboard was touched three minutes ago.
        var afterInput = classifier.Classify(still, Start.AddMinutes(10), TimeSpan.FromMinutes(3), mediaPlaying: false)!;
        Assert.Equal("upright", afterInput.Posture);
        Assert.Equal(TimeSpan.FromMinutes(3), classifier.StillFor(Start.AddMinutes(10)));

        var rested = classifier.Classify(still, Start.AddMinutes(12), TimeSpan.FromMinutes(5), mediaPlaying: false)!;
        Assert.Equal("resting", rested.Posture);
    }

    [Fact]
    public void An_idle_keyboard_alone_is_never_rest_the_camera_must_see_a_still_owner()
    {
        // ADR-0155 decision 1: nobody in front of the camera, hours of idle input.
        var classifier = new PresenceClassifier(Options());
        var empty = new SampleSummary(4, 0, 120, 0.0);
        classifier.Classify(empty, Start, TimeSpan.FromHours(3), mediaPlaying: false);
        var later = classifier.Classify(empty, Start.AddHours(1), TimeSpan.FromHours(4), mediaPlaying: false)!;

        Assert.False(later.PersonPresent);
        Assert.Equal("unknown", later.Posture);

        // And a moving owner never rests, however idle the keyboard.
        var moving = new PresenceClassifier(Options());
        var busy = new SampleSummary(4, 4, 120, 0.05);
        moving.Classify(busy, Start, TimeSpan.FromHours(3), mediaPlaying: false);
        Assert.Equal("upright", moving.Classify(busy, Start.AddHours(1), TimeSpan.FromHours(4), mediaPlaying: false)!.Posture);
    }

    [Theory]
    [InlineData(0.0, "none")]
    [InlineData(0.008, "low")]
    [InlineData(0.02, "medium")]
    [InlineData(0.08, "high")]
    public void Motion_is_bucketed_into_the_four_activity_levels(double motion, string level)
    {
        var classifier = new PresenceClassifier(Options());
        var observation = classifier.Classify(new SampleSummary(4, 4, 120, motion), Start, null, null)!;
        Assert.Equal(level, observation.ActivityLevel);
    }

    [Fact]
    public void A_dark_room_is_reported_with_a_confidence_fusion_will_not_act_on()
    {
        var classifier = new PresenceClassifier(Options());
        var dark = classifier.Classify(new SampleSummary(4, 0, 4, 0.0), Start, null, null)!;
        var lit = classifier.Classify(new SampleSummary(4, 0, 120, 0.0), Start, null, null)!;

        Assert.False(dark.PersonPresent);
        // Cloud Core's min_confidence is 0.35: a dark room becomes UNKNOWN, never AWAY.
        Assert.True(dark.PresenceConfidence < 0.35);
        Assert.True(lit.PresenceConfidence >= 0.35);
    }

    [Fact]
    public void A_face_in_too_few_frames_is_not_presence_and_an_empty_sample_says_nothing()
    {
        var classifier = new PresenceClassifier(Options());
        Assert.False(classifier.Classify(new SampleSummary(5, 1, 120, 0.0), Start, null, null)!.PersonPresent);
        Assert.True(classifier.Classify(new SampleSummary(5, 2, 120, 0.0), Start, null, null)!.PersonPresent);
        Assert.Null(classifier.Classify(new SampleSummary(0, 0, 0, null), Start, null, null));
    }

    [Fact]
    public void The_sample_summary_measures_motion_between_consecutive_frames()
    {
        var a = new CameraFrame(2, 1, [100, 100], [new FaceBox(0, 0, 1, 1)], Start);
        var b = new CameraFrame(2, 1, [110, 90], [], Start);
        var summary = SampleSummary.Of([a, b]);

        Assert.Equal(2, summary.Frames);
        Assert.Equal(1, summary.FaceFrames);
        Assert.Equal(100.0, summary.MeanLuma, 6);
        Assert.Equal(10.0 / 255.0, summary.Motion!.Value, 6);
        Assert.Null(SampleSummary.Of([a]).Motion);
    }

    // ================================================================ the capability

    [Fact]
    public void Camera_mode_validates_its_payload_and_clamps_the_interval()
    {
        var rig = NewRig();

        var bad = Assert.Throws<CapabilityException>(() => rig.Monitor.Configure(new JsonObject { ["mode"] = "always" }));
        Assert.Equal(ErrorClasses.ValidationError, bad.ErrorClass);
        var badInterval = Assert.Throws<CapabilityException>(() => rig.Monitor.Configure(new JsonObject { ["mode"] = "periodic", ["interval_s"] = "soon" }));
        Assert.Equal(ErrorClasses.ValidationError, badInterval.ErrorClass);

        var read = rig.Monitor.Configure([]);
        Assert.False(read["changed"]!.GetValue<bool>());
        Assert.Equal("off", read["mode"]!.GetValue<string>());

        Assert.Equal(30, rig.Monitor.Configure(Mode(CameraModes.Periodic, 1))["interval_s"]!.GetValue<int>());
        Assert.Equal(900, rig.Monitor.Configure(Mode(CameraModes.Periodic, 100_000))["interval_s"]!.GetValue<int>());
        Assert.Equal(2, rig.Monitor.Configure(Mode(CameraModes.Continuous, 0))["interval_s"]!.GetValue<int>());
        Assert.Equal(5, rig.Monitor.Configure(Mode(CameraModes.Continuous))["interval_s"]!.GetValue<int>());
    }

    [Fact]
    public async Task A_mode_change_wakes_the_loop_rather_than_waiting_out_the_interval()
    {
        var indicator = new RecordingCameraIndicator();
        var source = new FakeCameraSource(indicator);
        using var monitor = new CameraPresenceMonitor(
            source, indicator, NullLogger.Instance, Options() with { PeriodicInterval = TimeSpan.FromMinutes(15) });
        using var cts = new CancellationTokenSource();
        var loop = monitor.RunAsync(cts.Token);
        try
        {
            await Task.Delay(100);
            Assert.Equal(0, source.Opens);

            monitor.Configure(Mode(CameraModes.Periodic));
            await WaitUntil(() => source.Opens == 1 && source.OpenSessions == 0, "the periodic sample after the mode change");

            monitor.Configure(Mode(CameraModes.Continuous));
            await WaitUntil(() => source.OpenSessions == 1, "continuous mode opening the camera");

            monitor.Configure(Mode(CameraModes.Off));
            await WaitUntil(() => source.OpenSessions == 0 && indicator.State == CameraIndicatorState.Hidden, "off closing the camera");
        }
        finally
        {
            await cts.CancelAsync();
            await loop.WaitAsync(TimeSpan.FromSeconds(10));
        }

        Assert.Equal(0, source.OpenSessions);
    }

    private static async Task WaitUntil(Func<bool> condition, string what)
    {
        // A hang guard, not the claim: the claim is the condition.
        var deadline = DateTime.UtcNow.AddSeconds(10);
        while (!condition())
        {
            Assert.True(DateTime.UtcNow < deadline, $"timed out waiting for {what}");
            await Task.Delay(20);
        }
    }
}
