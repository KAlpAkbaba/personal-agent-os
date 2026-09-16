using System.Runtime.InteropServices;
using System.Runtime.InteropServices.WindowsRuntime;
using System.Runtime.Versioning;
using Windows.Graphics.Imaging;
using Windows.Media.Capture;
using Windows.Media.Capture.Frames;
using Windows.Media.FaceAnalysis;
using Windows.Media.MediaProperties;

namespace PagentOS.SessionCompanion.Camera;

/// <summary>
/// The on-device face pass: <c>Windows.Media.FaceAnalysis.FaceDetector</c>, which ships with
/// Windows, needs no account and sends nothing anywhere. It turns one bitmap into face BOXES
/// and a small down-sampled luma plane, both in memory; every intermediate buffer is zeroed
/// before this method returns. Separate from the capture so it can be exercised on a
/// synthetic bitmap without opening any camera.
/// </summary>
[SupportedOSPlatform("windows10.0.19041.0")]
public sealed class WindowsFaceAnalyzer
{
    /// <summary>The luma plane kept for the motion estimate: small on purpose.</summary>
    public const int LumaWidth = 80;
    public const int LumaHeight = 60;

    private readonly FaceDetector _detector;

    private WindowsFaceAnalyzer(FaceDetector detector)
    {
        _detector = detector;
    }

    public static bool IsSupported => FaceDetector.IsSupported;

    public static async Task<WindowsFaceAnalyzer> CreateAsync()
    {
        if (!FaceDetector.IsSupported)
        {
            throw new CameraAccessException(CameraFailure.Unavailable, "face_detector_unsupported", "Windows.Media.FaceAnalysis is not available on this machine");
        }

        return new WindowsFaceAnalyzer(await FaceDetector.CreateAsync());
    }

    public async Task<CameraFrame> AnalyseAsync(SoftwareBitmap bitmap, DateTimeOffset capturedAt)
    {
        ArgumentNullException.ThrowIfNull(bitmap);
        using var gray = bitmap.BitmapPixelFormat == BitmapPixelFormat.Gray8
            ? SoftwareBitmap.Copy(bitmap)
            : SoftwareBitmap.Convert(bitmap, BitmapPixelFormat.Gray8);
        var width = gray.PixelWidth;
        var height = gray.PixelHeight;

        var detected = await _detector.DetectFacesAsync(gray);
        var faces = new List<FaceBox>(detected.Count);
        foreach (var face in detected)
        {
            faces.Add(new FaceBox(
                (double)face.FaceBox.X / width,
                (double)face.FaceBox.Y / height,
                (double)face.FaceBox.Width / width,
                (double)face.FaceBox.Height / height));
        }

        int stride;
        using (var locked = gray.LockBuffer(BitmapBufferAccessMode.Read))
        {
            stride = locked.GetPlaneDescription(0).Stride;
        }

        var buffer = new Windows.Storage.Streams.Buffer((uint)(stride * height));
        gray.CopyToBuffer(buffer);
        var full = buffer.ToArray();
        try
        {
            return new CameraFrame(LumaWidth, LumaHeight, Downsample(full, width, height, stride), faces, capturedAt);
        }
        finally
        {
            Array.Clear(full);
            new byte[buffer.Length].CopyTo(buffer);
        }
    }

    /// <summary>Box-average a Gray8 plane to <see cref="LumaWidth"/> x <see cref="LumaHeight"/>.</summary>
    public static byte[] Downsample(byte[] plane, int width, int height, int stride)
    {
        var result = new byte[LumaWidth * LumaHeight];
        for (var y = 0; y < LumaHeight; y++)
        {
            var y0 = y * height / LumaHeight;
            var y1 = Math.Max(y0 + 1, (y + 1) * height / LumaHeight);
            for (var x = 0; x < LumaWidth; x++)
            {
                var x0 = x * width / LumaWidth;
                var x1 = Math.Max(x0 + 1, (x + 1) * width / LumaWidth);
                long sum = 0;
                var count = 0;
                for (var yy = y0; yy < y1 && yy < height; yy++)
                {
                    var row = yy * stride;
                    for (var xx = x0; xx < x1 && xx < width; xx++)
                    {
                        sum += plane[row + xx];
                        count++;
                    }
                }

                result[(y * LumaWidth) + x] = count == 0 ? (byte)0 : (byte)(sum / count);
            }
        }

        return result;
    }
}

/// <summary>
/// The real camera: <c>Windows.Media.Capture.MediaCapture</c> with a <c>MediaFrameReader</c>,
/// opened in SHARED read-only mode first so an owner's video call keeps its camera. Nothing is
/// recorded: there is no sink, no encoder and no file anywhere in this path - frames are pulled
/// from the reader, analysed by <see cref="WindowsFaceAnalyzer"/> and released.
/// </summary>
[SupportedOSPlatform("windows10.0.19041.0")]
public sealed class WindowsCameraFrameSource : ICameraFrameSource
{
    // HRESULTs the capture stack answers with, mapped onto what the owner is told.
    private const int AccessDenied = unchecked((int)0x80070005);
    private const int HardwareBusy = unchecked((int)0xC00D3704);
    private const int NoCaptureDevices = unchecked((int)0xC00D3E86);
    private const int ElementNotFound = unchecked((int)0x80070490);
    private const int DeviceRemoved = unchecked((int)0xC00DABE0);

    private readonly TimeProvider _time;

    public WindowsCameraFrameSource(TimeProvider? time = null)
    {
        _time = time ?? TimeProvider.System;
    }

    public async Task<ICameraSession> OpenAsync(CancellationToken cancellationToken)
    {
        var groups = await MediaFrameSourceGroup.FindAllAsync();
        MediaFrameSourceGroup? group = null;
        MediaFrameSourceInfo? info = null;
        foreach (var candidate in groups)
        {
            info = candidate.SourceInfos.FirstOrDefault(s => s.SourceKind == MediaFrameSourceKind.Color && s.MediaStreamType == Windows.Media.Capture.MediaStreamType.VideoPreview)
                   ?? candidate.SourceInfos.FirstOrDefault(s => s.SourceKind == MediaFrameSourceKind.Color);
            if (info is not null)
            {
                group = candidate;
                break;
            }
        }

        if (group is null || info is null)
        {
            throw new CameraAccessException(CameraFailure.Unavailable, "no_camera", "no color camera is attached");
        }

        cancellationToken.ThrowIfCancellationRequested();
        var analyzer = await WindowsFaceAnalyzer.CreateAsync().ConfigureAwait(false);
        var capture = await InitialiseAsync(group).ConfigureAwait(false);
        try
        {
            var source = capture.FrameSources[info.Id];
            var reader = await capture.CreateFrameReaderAsync(source, MediaEncodingSubtypes.Bgra8);
            reader.AcquisitionMode = MediaFrameReaderAcquisitionMode.Realtime;
            var started = await reader.StartAsync();
            if (started != MediaFrameReaderStartStatus.Success)
            {
                reader.Dispose();
                throw started switch
                {
                    MediaFrameReaderStartStatus.ExclusiveControlNotAvailable => new CameraAccessException(CameraFailure.Busy, "camera_in_use", "another application holds the camera"),
                    MediaFrameReaderStartStatus.DeviceNotAvailable => new CameraAccessException(CameraFailure.Unavailable, "device_not_available", "the camera is not available"),
                    _ => new CameraAccessException(CameraFailure.Error, "reader_start_failed", $"the frame reader did not start: {started}"),
                };
            }

            return new Session(capture, reader, analyzer, _time);
        }
        catch
        {
            capture.Dispose();
            throw;
        }
    }

    private static async Task<MediaCapture> InitialiseAsync(MediaFrameSourceGroup group)
    {
        foreach (var sharing in new[] { MediaCaptureSharingMode.SharedReadOnly, MediaCaptureSharingMode.ExclusiveControl })
        {
            var capture = new MediaCapture();
            try
            {
                await capture.InitializeAsync(new MediaCaptureInitializationSettings
                {
                    SourceGroup = group,
                    SharingMode = sharing,
                    MemoryPreference = MediaCaptureMemoryPreference.Cpu,
                    StreamingCaptureMode = StreamingCaptureMode.Video,
                });
                return capture;
            }
            catch (UnauthorizedAccessException ex)
            {
                capture.Dispose();
                throw new CameraAccessException(CameraFailure.Blocked, "access_denied", "Windows denied camera access", ex);
            }
            catch (Exception ex) when (ex.HResult == AccessDenied)
            {
                capture.Dispose();
                throw new CameraAccessException(CameraFailure.Blocked, "access_denied", "Windows denied camera access", ex);
            }
            catch (Exception ex) when (ex.HResult is NoCaptureDevices or ElementNotFound or DeviceRemoved)
            {
                capture.Dispose();
                throw new CameraAccessException(CameraFailure.Unavailable, "no_camera", "the camera could not be found", ex);
            }
            catch (Exception ex) when (ex is COMException or InvalidOperationException or ArgumentException)
            {
                capture.Dispose();
                if (sharing == MediaCaptureSharingMode.ExclusiveControl)
                {
                    throw ex.HResult == HardwareBusy
                        ? new CameraAccessException(CameraFailure.Busy, "camera_in_use", "the camera is in use", ex)
                        : new CameraAccessException(CameraFailure.Error, "init_failed", "the camera did not initialise", ex);
                }
            }
        }

        throw new CameraAccessException(CameraFailure.Error, "init_failed", "the camera did not initialise");
    }

    private sealed class Session(MediaCapture capture, MediaFrameReader reader, WindowsFaceAnalyzer analyzer, TimeProvider time) : ICameraSession
    {
        private static readonly TimeSpan Poll = TimeSpan.FromMilliseconds(50);

        public async Task<CameraFrame?> NextFrameAsync(TimeSpan timeout, CancellationToken cancellationToken)
        {
            var deadline = time.GetUtcNow() + timeout;
            while (time.GetUtcNow() < deadline)
            {
                cancellationToken.ThrowIfCancellationRequested();
                using (var reference = reader.TryAcquireLatestFrame())
                {
                    var bitmap = reference?.VideoMediaFrame?.SoftwareBitmap;
                    if (bitmap is not null)
                    {
                        using (bitmap)
                        {
                            return await analyzer.AnalyseAsync(bitmap, time.GetUtcNow()).ConfigureAwait(false);
                        }
                    }
                }

                await Task.Delay(Poll, time, cancellationToken).ConfigureAwait(false);
            }

            return null;
        }

        public async ValueTask DisposeAsync()
        {
            try
            {
                await reader.StopAsync();
            }
            finally
            {
                reader.Dispose();
                capture.Dispose();
            }
        }
    }
}
