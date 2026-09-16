using System.Globalization;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Configuration;

namespace PagentOS.SessionCompanion.Camera;

/// <summary>Every number the camera path depends on. Data, not branches - a test builds its own.</summary>
public sealed record CameraOptions
{
    /// <summary>Periodic mode: one sample this often. 60 s keeps the newest camera reading inside
    /// Cloud Core's 120 s <c>camera_unknown_grace_s</c>, which is what an automatic off requires.</summary>
    public TimeSpan PeriodicInterval { get; init; } = TimeSpan.FromSeconds(60);

    public TimeSpan ContinuousInterval { get; init; } = TimeSpan.FromSeconds(5);

    public static readonly TimeSpan MinPeriodicInterval = TimeSpan.FromSeconds(30);
    public static readonly TimeSpan MaxPeriodicInterval = TimeSpan.FromMinutes(15);
    public static readonly TimeSpan MinContinuousInterval = TimeSpan.FromSeconds(2);
    public static readonly TimeSpan MaxContinuousInterval = TimeSpan.FromSeconds(60);

    /// <summary>Frames analysed per sample, and the gap between them (the motion estimate needs two or more).</summary>
    public int FramesPerSample { get; init; } = 5;

    public TimeSpan FrameSpacing { get; init; } = TimeSpan.FromMilliseconds(300);

    public TimeSpan FrameTimeout { get; init; } = TimeSpan.FromSeconds(3);

    /// <summary>A present, still owner with no input and no sound for this long reads as resting.
    /// Cloud Core then needs that rest to HOLD for its own threshold (20 min inside the quiet hours,
    /// longer outside them) before it says LIKELY_ASLEEP.</summary>
    public TimeSpan RestingAfter { get; init; } = TimeSpan.FromMinutes(5);

    /// <summary>Input this recent means the owner is awake whatever the camera sees.</summary>
    public TimeSpan AwakeInputWithin { get; init; } = TimeSpan.FromSeconds(60);

    /// <summary>Mean absolute luma change between consecutive frames (0..1), bucketed.</summary>
    public double MotionNoneBelow { get; init; } = 0.004;

    public double MotionLowBelow { get; init; } = 0.012;

    public double MotionMediumBelow { get; init; } = 0.03;

    /// <summary>A scene darker than this (0..255 mean) is one the camera cannot see into.</summary>
    public double DarkBelowLuma { get; init; } = 16;

    /// <summary>The fraction of a sample's frames that must hold a face for "present".</summary>
    public double PresentFaceFraction { get; init; } = 0.4;

    /// <summary>The render peak above which sound counts as playing.</summary>
    public double MediaPeakThreshold { get; init; } = 0.02;

    /// <summary>The device-local kill switch (the rollback path): false and the camera never opens.</summary>
    public bool EnabledOnDevice { get; init; } = true;

    public TimeSpan IntervalFor(string mode)
        => mode == CameraModes.Continuous ? ContinuousInterval : PeriodicInterval;

    public static TimeSpan Clamp(string mode, TimeSpan requested)
    {
        var (min, max) = mode == CameraModes.Continuous
            ? (MinContinuousInterval, MaxContinuousInterval)
            : (MinPeriodicInterval, MaxPeriodicInterval);
        return requested < min ? min : requested > max ? max : requested;
    }

    /// <summary><c>CameraEnabled</c> (default true), <c>CameraPeriodicIntervalSeconds</c>, <c>CameraRestingAfterSeconds</c>.</summary>
    public static CameraOptions FromConfiguration(IConfiguration configuration)
    {
        var options = new CameraOptions();
        var enabled = configuration["CameraEnabled"];
        if (!string.IsNullOrWhiteSpace(enabled))
        {
            options = options with { EnabledOnDevice = Program.ParseFlag(enabled) };
        }

        if (int.TryParse(configuration["CameraPeriodicIntervalSeconds"], NumberStyles.Integer, CultureInfo.InvariantCulture, out var periodic))
        {
            options = options with { PeriodicInterval = Clamp(CameraModes.Periodic, TimeSpan.FromSeconds(periodic)) };
        }

        if (int.TryParse(configuration["CameraRestingAfterSeconds"], NumberStyles.Integer, CultureInfo.InvariantCulture, out var resting) && resting >= 60)
        {
            options = options with { RestingAfter = TimeSpan.FromSeconds(resting) };
        }

        return options;
    }
}

/// <summary>What one sample of frames says, before anything is concluded from it.</summary>
public sealed record SampleSummary(int Frames, int FaceFrames, double MeanLuma, double? Motion)
{
    public double FaceFraction => Frames == 0 ? 0 : (double)FaceFrames / Frames;

    /// <summary>Summarises a sample. Reads the frames; keeps nothing of them.</summary>
    public static SampleSummary Of(IReadOnlyList<CameraFrame> frames)
    {
        ArgumentNullException.ThrowIfNull(frames);
        if (frames.Count == 0)
        {
            return new SampleSummary(0, 0, 0, null);
        }

        var faces = 0;
        double lumaTotal = 0;
        foreach (var frame in frames)
        {
            if (frame.Faces.Count > 0)
            {
                faces++;
            }

            lumaTotal += Mean(frame.Luma);
        }

        double motionTotal = 0;
        var pairs = 0;
        for (var i = 1; i < frames.Count; i++)
        {
            var a = frames[i - 1];
            var b = frames[i];
            if (a.Width != b.Width || a.Height != b.Height)
            {
                continue;
            }

            motionTotal += MeanAbsoluteDifference(a.Luma, b.Luma);
            pairs++;
        }

        return new SampleSummary(
            frames.Count,
            faces,
            lumaTotal / frames.Count,
            pairs == 0 ? null : motionTotal / pairs / 255.0);
    }

    private static double Mean(ReadOnlySpan<byte> plane)
    {
        if (plane.IsEmpty)
        {
            return 0;
        }

        long sum = 0;
        foreach (var value in plane)
        {
            sum += value;
        }

        return (double)sum / plane.Length;
    }

    private static double MeanAbsoluteDifference(ReadOnlySpan<byte> a, ReadOnlySpan<byte> b)
    {
        long sum = 0;
        for (var i = 0; i < a.Length; i++)
        {
            sum += Math.Abs(a[i] - b[i]);
        }

        return a.IsEmpty ? 0 : (double)sum / a.Length;
    }
}

/// <summary>
/// The seven-field structured observation (M18_HOLOGRAPHIC_CORE_SPEC.md §2) this device sends
/// for its camera. Exactly those fields: Cloud Core refuses any other key at its boundary, and
/// the heartbeat schema closes the object as well.
/// </summary>
public sealed record CameraObservation(
    bool PersonPresent,
    double PresenceConfidence,
    string ActivityLevel,
    string Posture,
    string AwakeState,
    DateTimeOffset ObservedAt)
{
    public const string Source = "camera";

    public static readonly IReadOnlyList<string> Fields =
    [
        "person_present", "presence_confidence", "activity_level", "posture", "awake_state", "observed_at", "source",
    ];

    public JsonObject ToJson() => new()
    {
        ["person_present"] = PersonPresent,
        ["presence_confidence"] = Math.Round(PresenceConfidence, 3),
        ["activity_level"] = ActivityLevel,
        ["posture"] = Posture,
        ["awake_state"] = AwakeState,
        ["observed_at"] = ObservedAt.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'", CultureInfo.InvariantCulture),
        ["source"] = Source,
    };
}

/// <summary>
/// Turns a sample into an observation. Pure apart from one remembered instant: since when the
/// owner has been present and still. ADR-0155 decision 1 holds - an idle keyboard alone is never
/// rest - and it is the CAMERA that makes rest possible: a face that is there, a scene that does
/// not move, no input for a while and no sound playing. Any one of those missing and the posture
/// is not "resting".
/// </summary>
public sealed class PresenceClassifier(CameraOptions options)
{
    private DateTimeOffset? _stillSince;

    public TimeSpan? StillFor(DateTimeOffset now) => _stillSince is null ? null : now - _stillSince.Value;

    public void Reset() => _stillSince = null;

    /// <summary>Null when the sample held no frame at all (there is nothing honest to say).</summary>
    public CameraObservation? Classify(SampleSummary summary, DateTimeOffset now, TimeSpan? inputIdle, bool? mediaPlaying)
    {
        ArgumentNullException.ThrowIfNull(summary);
        if (summary.Frames == 0)
        {
            return null;
        }

        var dark = summary.MeanLuma < options.DarkBelowLuma;
        var present = summary.FaceFraction >= options.PresentFaceFraction;
        var activity = ActivityFor(summary.Motion, present);

        if (!present)
        {
            _stillSince = null;

            // A dark room is one the camera cannot see into: the answer is "absent" with a
            // confidence below Cloud Core's min_confidence, so fusion says UNKNOWN - and
            // uncertain means the screens stay on.
            var confidence = dark ? 0.2 : summary.Frames >= 3 ? 0.7 : 0.5;
            return new CameraObservation(false, confidence, "none", "unknown", "uncertain", now);
        }

        var still = activity is "none" or "low";
        if (!still || mediaPlaying == true)
        {
            _stillSince = null;
        }
        else
        {
            _stillSince ??= now;
            if (inputIdle is { } idle && now - idle > _stillSince.Value)
            {
                // The owner touched the machine during the "still" run: stillness restarts at
                // that input, never before it.
                _stillSince = now - idle;
            }
        }

        var inputRecent = inputIdle is { } recent && recent < options.AwakeInputWithin;
        // Sound playing already cleared the still run above, so a film can never reach this.
        var resting = _stillSince is { } since
                      && now - since >= options.RestingAfter
                      && !inputRecent;

        var presentConfidence = Math.Clamp(0.55 + (0.4 * summary.FaceFraction), 0, 0.95);
        if (resting)
        {
            return new CameraObservation(true, presentConfidence, activity, "resting", "resting", now);
        }

        var awake = inputRecent || activity is "medium" or "high" ? "awake" : "uncertain";
        return new CameraObservation(true, presentConfidence, activity, "upright", awake, now);
    }

    private string ActivityFor(double? motion, bool present)
    {
        if (motion is null)
        {
            return present ? "low" : "none";
        }

        if (motion < options.MotionNoneBelow)
        {
            return "none";
        }

        if (motion < options.MotionLowBelow)
        {
            return "low";
        }

        return motion < options.MotionMediumBelow ? "medium" : "high";
    }
}
