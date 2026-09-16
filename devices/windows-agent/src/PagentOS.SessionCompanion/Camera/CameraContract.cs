namespace PagentOS.SessionCompanion.Camera;

/// <summary>
/// B48 (rows 300, 326, 327, 671): the device camera's vocabulary. The camera runs HERE, in
/// the owner's interactive session and never in the Session-0 service, and what leaves this
/// process is the seven-field structured observation of M18_HOLOGRAPHIC_CORE_SPEC.md §2 plus
/// the camera's own state - never a frame, never a picture, never a byte of one.
/// </summary>
public static class CameraModes
{
    /// <summary>The camera is never opened. The default after every start: consent is an act, not a leftover.</summary>
    public const string Off = "off";

    /// <summary>The camera opens for a short sample every <see cref="CameraOptions.PeriodicInterval"/> and closes again.</summary>
    public const string Periodic = "periodic";

    /// <summary>The camera stays open and a sample is analysed every <see cref="CameraOptions.ContinuousInterval"/>.</summary>
    public const string Continuous = "continuous";

    public static readonly IReadOnlyList<string> All = [Off, Periodic, Continuous];

    public static bool IsKnown(string? mode) => mode is not null && All.Contains(mode, StringComparer.Ordinal);
}

/// <summary>What the camera is doing right now, as the heartbeat reports it.</summary>
public static class CameraStates
{
    /// <summary>Mode is off; nothing is open.</summary>
    public const string Off = "off";

    /// <summary>Periodic mode, between two samples: the camera is closed.</summary>
    public const string Idle = "idle";

    /// <summary>The camera is open (a periodic sample, or continuous mode).</summary>
    public const string Capturing = "capturing";

    /// <summary>Windows refused access: the privacy switch, or the device policy. Reported, never worked around.</summary>
    public const string Blocked = "blocked";

    /// <summary>No camera on this machine, or it vanished.</summary>
    public const string Unavailable = "unavailable";

    /// <summary>Another application holds the camera exclusively.</summary>
    public const string Busy = "busy";

    /// <summary>The owner closed the camera from the tray on this device; the cloud's mode is kept but not obeyed.</summary>
    public const string Vetoed = "vetoed";

    /// <summary>Anything else went wrong; <c>error</c> says what, in a short token.</summary>
    public const string Error = "error";

    public static readonly IReadOnlyList<string> All = [Off, Idle, Capturing, Blocked, Unavailable, Busy, Vetoed, Error];
}

/// <summary>The tray indicator's three faces.</summary>
public enum CameraIndicatorState
{
    /// <summary>Mode off: no icon at all.</summary>
    Hidden,

    /// <summary>A mode is on but the camera is closed right now (periodic, between samples).</summary>
    Armed,

    /// <summary>The camera is open. Shown BEFORE the device is opened and hidden only AFTER it is closed.</summary>
    Open,
}

/// <summary>
/// One analysed frame, in memory only. The luma plane is a small down-sampled copy used for a
/// motion estimate; <see cref="Dispose"/> zeroes it, and nothing in this folder can write it
/// anywhere (a structural test reads these sources for file, network and encoder APIs).
/// </summary>
public sealed class CameraFrame : IDisposable
{
    private readonly byte[] _luma;

    public CameraFrame(int width, int height, byte[] luma, IReadOnlyList<FaceBox> faces, DateTimeOffset capturedAt)
    {
        ArgumentNullException.ThrowIfNull(luma);
        ArgumentNullException.ThrowIfNull(faces);
        if (width <= 0 || height <= 0 || luma.Length != width * height)
        {
            throw new ArgumentException("a luma plane must be exactly width x height bytes", nameof(luma));
        }

        Width = width;
        Height = height;
        _luma = luma;
        Faces = faces;
        CapturedAt = capturedAt;
    }

    public int Width { get; }

    public int Height { get; }

    public ReadOnlySpan<byte> Luma => _luma;

    public IReadOnlyList<FaceBox> Faces { get; }

    public DateTimeOffset CapturedAt { get; }

    /// <summary>True once <see cref="Dispose"/> ran - for the tests that prove nothing outlives a sample.</summary>
    public bool Cleared { get; private set; }

    public void Dispose()
    {
        Array.Clear(_luma);
        Cleared = true;
    }
}

/// <summary>A detected face, normalised to the frame (0..1). A box - never a landmark, never an identity.</summary>
public readonly record struct FaceBox(double X, double Y, double Width, double Height)
{
    public double Area => Math.Max(0, Width) * Math.Max(0, Height);
}

/// <summary>Why the camera could not be opened. Mapped onto <see cref="CameraStates"/>, never onto a guess.</summary>
public enum CameraFailure
{
    Blocked,
    Unavailable,
    Busy,
    Error,
}

public sealed class CameraAccessException(CameraFailure failure, string token, string message, Exception? inner = null)
    : Exception(message, inner)
{
    public CameraFailure Failure { get; } = failure;

    /// <summary>A short, stable token for the heartbeat's <c>error</c> field (no free text leaves the device).</summary>
    public string Token { get; } = token;
}

/// <summary>Opens the camera. The only seam between this folder and a real device; tests use a fake.</summary>
public interface ICameraFrameSource
{
    /// <summary>Opens a session or throws <see cref="CameraAccessException"/>.</summary>
    Task<ICameraSession> OpenAsync(CancellationToken cancellationToken);
}

/// <summary>An open camera. Disposing it closes the device.</summary>
public interface ICameraSession : IAsyncDisposable
{
    /// <summary>The next analysed frame, or null when none arrived within <paramref name="timeout"/>.</summary>
    Task<CameraFrame?> NextFrameAsync(TimeSpan timeout, CancellationToken cancellationToken);
}

/// <summary>The owner-visible sign that the camera is armed or open.</summary>
public interface ICameraIndicator
{
    CameraIndicatorState State { get; }

    void Set(CameraIndicatorState state, string mode);

    /// <summary>Raised when the owner closes (true) or re-allows (false) the camera from the indicator itself.</summary>
    event Action<bool>? OwnerVeto;
}

/// <summary>Windows' own camera permission, read before anything is opened (row 671).</summary>
public interface ICameraConsent
{
    /// <summary>Null when Windows allows it (or does not say); otherwise a short token naming the switch that denies it.</summary>
    string? DeniedBy();
}

/// <summary>Whether the machine is playing sound right now - a film is not sleep (ADR-0155 decision 1).</summary>
public interface IMediaActivityProbe
{
    /// <summary>True when sound is playing, false when silent, null when this machine cannot tell.</summary>
    bool? IsPlaying();
}

/// <summary>An indicator for hosts with no tray (tests, a non-Windows host). It still records its state.</summary>
public sealed class RecordingCameraIndicator : ICameraIndicator
{
    private readonly List<CameraIndicatorState> _history = [];

    public CameraIndicatorState State { get; private set; } = CameraIndicatorState.Hidden;

    public IReadOnlyList<CameraIndicatorState> History
    {
        get
        {
            lock (_history)
            {
                return [.. _history];
            }
        }
    }

    public event Action<bool>? OwnerVeto;

    public void Set(CameraIndicatorState state, string mode)
    {
        lock (_history)
        {
            State = state;
            _history.Add(state);
        }
    }

    /// <summary>Simulates the owner using the indicator's own switch.</summary>
    public void RaiseOwnerVeto(bool vetoed) => OwnerVeto?.Invoke(vetoed);
}

/// <summary>A consent source that never denies. For tests and hosts with no registry.</summary>
public sealed class AllowAllCameraConsent : ICameraConsent
{
    public static AllowAllCameraConsent Instance { get; } = new();

    public string? DeniedBy() => null;
}

/// <summary>A media probe that cannot tell.</summary>
public sealed class UnknownMediaActivityProbe : IMediaActivityProbe
{
    public static UnknownMediaActivityProbe Instance { get; } = new();

    public bool? IsPlaying() => null;
}

/// <summary>A frame source for a machine this companion cannot open a camera on at all.</summary>
public sealed class NoCameraFrameSource : ICameraFrameSource
{
    public static NoCameraFrameSource Instance { get; } = new();

    public Task<ICameraSession> OpenAsync(CancellationToken cancellationToken)
        => throw new CameraAccessException(CameraFailure.Unavailable, "no_camera_support", "this companion has no camera path on this host");
}
