using PagentOS.Companion.Audio.Audio;

namespace PagentOS.Companion.Audio.Listening;

/// <summary>
/// The only place un-admitted microphone audio lives: a bounded, in-memory ring of the most
/// recent frames, so an admitted utterance starts with its first syllable instead of 40 ms
/// into it. Row 249 ("raw voice is never archived") is kept here by construction:
/// <list type="bullet">
/// <item>the capacity is clamped to <see cref="DeviceVoiceContract.MaxPreRollMs"/>, whatever a
/// caller asks for;</item>
/// <item>a frame that falls out of the ring, or is dropped by <see cref="Discard"/>, has its
/// samples ZEROED before the reference is released - the bytes do not linger until a garbage
/// collection happens to reuse them;</item>
/// <item>there is no file, stream or logger anywhere in this type.</item>
/// </list>
/// Frames handed out by <see cref="TakeAll"/> are not zeroed: they now belong to the uplink.
/// Not thread-safe; it lives on the listening loop.
/// </summary>
public sealed class PreRollBuffer
{
    private readonly Queue<AudioFrame> _frames = new();
    private double _heldMs;

    public PreRollBuffer(int capacityMs)
    {
        CapacityMs = Math.Clamp(capacityMs, 0, DeviceVoiceContract.MaxPreRollMs);
    }

    public int CapacityMs { get; }

    public double HeldMs => _heldMs;

    public int Count => _frames.Count;

    /// <summary>Frames zeroed so far (evicted or discarded). Asserted by tests.</summary>
    public long ZeroedFrames { get; private set; }

    public void Push(AudioFrame frame)
    {
        if (CapacityMs == 0)
        {
            Zero(frame);
            return;
        }

        _frames.Enqueue(frame);
        _heldMs += frame.DurationMs;
        while (_heldMs > CapacityMs && _frames.Count > 0)
        {
            var old = _frames.Dequeue();
            _heldMs -= old.DurationMs;
            Zero(old);
        }
    }

    /// <summary>Everything held, oldest first; the ring is empty afterwards.</summary>
    public IReadOnlyList<AudioFrame> TakeAll()
    {
        var all = _frames.ToArray();
        _frames.Clear();
        _heldMs = 0;
        return all;
    }

    /// <summary>Drops and zeroes everything held (mute, listening off, device switch).</summary>
    public void Discard()
    {
        while (_frames.Count > 0)
        {
            Zero(_frames.Dequeue());
        }

        _heldMs = 0;
    }

    private void Zero(AudioFrame frame)
    {
        Array.Clear(frame.Pcm16);
        ZeroedFrames++;
    }
}
