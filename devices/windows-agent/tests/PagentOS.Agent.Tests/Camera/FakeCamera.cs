using PagentOS.SessionCompanion;
using PagentOS.SessionCompanion.Camera;

namespace PagentOS.Agent.Tests.Camera;

/// <summary>
/// A camera that is not a camera: frames are made from numbers. No automated test opens the
/// owner's real camera (B48 rule); the physical evaluation is scripts/core/qualify-device-camera.ps1.
/// </summary>
public sealed class FakeCameraSource(ICameraIndicator indicator) : ICameraFrameSource
{
    private readonly object _sync = new();
    private int _tick;

    /// <summary>Whether the next frames hold a face.</summary>
    public bool FacePresent { get; set; } = true;

    /// <summary>The base brightness of a frame (0..255).</summary>
    public byte Brightness { get; set; } = 120;

    /// <summary>How much consecutive frames differ (0 = a perfectly still scene).</summary>
    public byte MotionStep { get; set; }

    public CameraAccessException? FailOpenWith { get; set; }

    public int Opens { get; private set; }

    public int Closes { get; private set; }

    public int OpenSessions => Opens - Closes;

    /// <summary>The indicator's state at every moment the device was touched (open and each frame).</summary>
    public List<CameraIndicatorState> IndicatorWhenTouched { get; } = [];

    public List<CameraFrame> Handed { get; } = [];

    public Task<ICameraSession> OpenAsync(CancellationToken cancellationToken)
    {
        lock (_sync)
        {
            IndicatorWhenTouched.Add(indicator.State);
            if (FailOpenWith is not null)
            {
                throw FailOpenWith;
            }

            Opens++;
        }

        return Task.FromResult<ICameraSession>(new Session(this));
    }

    private CameraFrame NextFrame()
    {
        lock (_sync)
        {
            IndicatorWhenTouched.Add(indicator.State);
            var luma = new byte[8 * 6];
            var value = (byte)Math.Clamp(Brightness + ((_tick++ % 2) * MotionStep), 0, 255);
            Array.Fill(luma, value);
            IReadOnlyList<FaceBox> faces = FacePresent ? [new FaceBox(0.4, 0.3, 0.2, 0.25)] : [];
            var frame = new CameraFrame(8, 6, luma, faces, DateTimeOffset.UnixEpoch);
            Handed.Add(frame);
            return frame;
        }
    }

    private sealed class Session(FakeCameraSource owner) : ICameraSession
    {
        private bool _closed;

        public Task<CameraFrame?> NextFrameAsync(TimeSpan timeout, CancellationToken cancellationToken)
        {
            if (_closed)
            {
                throw new ObjectDisposedException("camera session");
            }

            return Task.FromResult<CameraFrame?>(owner.NextFrame());
        }

        public ValueTask DisposeAsync()
        {
            if (!_closed)
            {
                _closed = true;
                lock (owner._sync)
                {
                    owner.Closes++;
                }
            }

            return ValueTask.CompletedTask;
        }
    }
}

public sealed class FakeIdle(TimeSpan? idle) : IInputActivitySource
{
    public TimeSpan? Value { get; set; } = idle;

    public TimeSpan? IdleTime => Value;
}

public sealed class FakeMedia(bool? playing) : IMediaActivityProbe
{
    public bool? Playing { get; set; } = playing;

    public bool? IsPlaying() => Playing;
}

public sealed class FakeConsent(string? deniedBy) : ICameraConsent
{
    public string? Denied { get; set; } = deniedBy;

    public string? DeniedBy() => Denied;
}

/// <summary>A clock that only moves when told to. Task.Delay(0) completes at once on it.</summary>
public sealed class SteppedClock(DateTimeOffset start) : TimeProvider
{
    public DateTimeOffset Now { get; set; } = start;

    public override DateTimeOffset GetUtcNow() => Now;

    public void Advance(TimeSpan by) => Now += by;
}
