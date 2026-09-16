using System.Threading.Channels;
using PagentOS.Companion.Audio.Audio;

namespace PagentOS.Companion.Audio.Listening.Spotting;

/// <summary>
/// Records one bounded window from a capture device for enrollment - only when the owner has
/// asked to enroll (<c>--voice-enroll</c>), and never longer than
/// <see cref="DeviceVoiceContract.MaxSegmentMs"/> plus the lead the owner needs to start
/// speaking. The frames go straight to <see cref="KeywordEnrollment.FromRecording"/>, which
/// zeroes them.
/// </summary>
public static class EnrollmentRecorder
{
    public static readonly TimeSpan Window = TimeSpan.FromMilliseconds(DeviceVoiceContract.MaxSegmentMs + 1000);

    public static async Task<IReadOnlyList<AudioFrame>> RecordAsync(IAudioCapture capture, TimeSpan window, CancellationToken cancellationToken)
    {
        var frames = Channel.CreateUnbounded<AudioFrame>();
        void OnFrame(AudioFrame frame) => frames.Writer.TryWrite(frame);
        capture.FrameCaptured += OnFrame;
        var collected = new List<AudioFrame>();
        double ms = 0;
        var limit = Math.Min(window.TotalMilliseconds, Window.TotalMilliseconds);
        try
        {
            capture.Start();
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(window + TimeSpan.FromSeconds(5));
            while (ms < limit)
            {
                AudioFrame frame;
                try
                {
                    frame = await frames.Reader.ReadAsync(timeout.Token).ConfigureAwait(false);
                }
                catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
                {
                    break;
                }

                collected.Add(frame);
                ms += frame.DurationMs;
            }
        }
        finally
        {
            capture.FrameCaptured -= OnFrame;
            capture.Stop();
            while (frames.Reader.TryRead(out var late))
            {
                Array.Clear(late.Pcm16);
            }
        }

        return collected;
    }
}
