using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion;

/// <summary>
/// The wake-volume RAMP for <c>desktop.alarm_start</c> (DEVICE_PROTOCOL.md §6c, M18).
///
/// <para>The product rule is one sentence: a wake volume ramps from a low level and has a
/// ceiling, and there is no path that produces sudden full volume. This type is where that
/// sentence becomes arithmetic — a pure value with its invariants in its factory, so the
/// property can be asserted directly instead of inferred from what a speaker did.</para>
///
/// <para>Three invariants, and they differ in kind on purpose:</para>
/// <list type="number">
/// <item><b>A start at or above <see cref="MaxStartVolume"/> is REFUSED</b>
/// (<c>validation_error</c>), never quietly lowered. A caller asking to begin at 0.9 is not
/// asking for a ramp; silently turning that into one would hide a malformed request from
/// whoever wrote it. Cloud Core refuses the same descriptor at creation time
/// (<c>app.routines.actions.MAX_WAKE_VOLUME_START</c>); this side refuses it again because a
/// row written before that validator existed must not ring at 0.9 today.</item>
/// <item><b>An end above <see cref="MaxVolume"/> is CLAMPED</b>, and the clamp is reported in
/// the command result. The alarm still has to wake the owner, so refusing the whole thing
/// over a too-loud ceiling would be worse than ringing at the ceiling — but the caller is
/// told the number it will actually reach, so nothing here is silent.</item>
/// <item><b>A ramp shorter than <see cref="MinRampSeconds"/> is CLAMPED UP</b>, for the same
/// reason: a "ramp" of one second is a jolt with extra steps.</item>
/// </list>
///
/// <para>The level is applied to the SAMPLES this companion generates, never to the machine's
/// master volume (<see cref="AlarmController"/>). An alarm that moved the system mixer would
/// leave the owner's whole machine loud after it stopped.</para>
/// </summary>
public sealed record WakeRamp
{
    /// <summary>A start at or above this is a jolt, not a ramp: refused.</summary>
    public const double MaxStartVolume = 0.5;

    /// <summary>The loudest the ramp is ever allowed to reach. Ends above it are clamped here.</summary>
    public const double MaxVolume = 0.85;

    /// <summary>Below this, "ramping" is indistinguishable from switching on. Clamped up.</summary>
    public const int MinRampSeconds = 5;

    /// <summary>An hour of ramp is already absurd; beyond it the arithmetic stops meaning anything.</summary>
    public const int MaxRampSeconds = 3600;

    /// <summary>Defaults, matching <c>app.routines.actions</c>'s so an omitted field means the same on both sides.</summary>
    public const double DefaultStart = 0.05;

    public const double DefaultEnd = 0.8;

    public const int DefaultRampSeconds = 60;

    private WakeRamp(double start, double end, int rampSeconds, bool endClamped, bool rampClamped)
    {
        Start = start;
        End = end;
        RampSeconds = rampSeconds;
        EndClamped = endClamped;
        RampClamped = rampClamped;
    }

    /// <summary>The level the first sample is generated at. Always below <see cref="MaxStartVolume"/>.</summary>
    public double Start { get; }

    /// <summary>The level the ramp reaches and holds. Never above <see cref="MaxVolume"/>.</summary>
    public double End { get; }

    public int RampSeconds { get; }

    /// <summary>True when the requested end exceeded <see cref="MaxVolume"/> and was lowered.</summary>
    public bool EndClamped { get; }

    /// <summary>True when the requested ramp was shorter than <see cref="MinRampSeconds"/> and was lengthened.</summary>
    public bool RampClamped { get; }

    /// <summary>
    /// Builds a ramp from a <c>wake_volume</c> descriptor, applying the three invariants above.
    /// Throws <see cref="CapabilityException"/> (<c>validation_error</c>) for a descriptor that
    /// cannot be made into a ramp at all.
    /// </summary>
    public static WakeRamp Create(double? start, double? end, int? rampSeconds)
    {
        var s = start ?? DefaultStart;
        var e = end ?? DefaultEnd;
        var seconds = rampSeconds ?? DefaultRampSeconds;

        if (double.IsNaN(s) || double.IsNaN(e) || s < 0.0 || s > 1.0 || e < 0.0 || e > 1.0)
        {
            throw new CapabilityException(
                ErrorClasses.ValidationError,
                "wake_volume start/end must each be a number in 0..1",
                retryable: false);
        }

        if (s >= MaxStartVolume)
        {
            throw new CapabilityException(
                ErrorClasses.ValidationError,
                $"wake_volume start {s:0.###} is at or above {MaxStartVolume:0.##}: an alarm must ramp the owner "
                + "awake, never begin at a jolt",
                retryable: false);
        }

        if (s > e)
        {
            throw new CapabilityException(
                ErrorClasses.ValidationError,
                $"wake_volume start {s:0.###} is above end {e:0.###}: this is a RAMP, not a fade",
                retryable: false);
        }

        if (seconds <= 0)
        {
            throw new CapabilityException(
                ErrorClasses.ValidationError,
                "wake_volume ramp_seconds must be a positive whole number of seconds",
                retryable: false);
        }

        var endClamped = e > MaxVolume;
        if (endClamped)
        {
            e = MaxVolume;
        }

        var rampClamped = seconds < MinRampSeconds;
        if (rampClamped)
        {
            seconds = MinRampSeconds;
        }
        else if (seconds > MaxRampSeconds)
        {
            seconds = MaxRampSeconds;
            rampClamped = true;
        }

        return new WakeRamp(s, e, seconds, endClamped, rampClamped);
    }

    /// <summary>
    /// The level at <paramref name="elapsed"/> into the alarm: <see cref="Start"/> at zero,
    /// linear to <see cref="End"/> over <see cref="RampSeconds"/>, then held. Monotonically
    /// non-decreasing and bounded by <see cref="MaxVolume"/> for every input, including
    /// negative and absurdly large ones — the caller's clock is not trusted to be sane.
    /// </summary>
    public double LevelAt(TimeSpan elapsed)
    {
        if (elapsed <= TimeSpan.Zero)
        {
            return Start;
        }

        var seconds = elapsed.TotalSeconds;
        if (seconds >= RampSeconds)
        {
            return End;
        }

        return Start + ((End - Start) * (seconds / RampSeconds));
    }
}
