using System.Buffers.Binary;
using System.Text;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Companion.Audio.Audio;

namespace PagentOS.SessionCompanion;

/// <summary>
/// A decoded PCM16 WAV: the format it declares and the sample bytes, and nothing else.
///
/// <para><b>Why the parser is written here rather than pulled in.</b> The only audio this
/// capability accepts is a short greeting the owner's own broker rendered, in one format
/// (<c>format: "wav"</c>, PCM16, mono or stereo, any common rate). A parser that accepts one
/// shape and refuses everything else with a validation error is a smaller attack surface than a
/// general decoder, and every refusal here is a refusal to hand unfamiliar bytes to an audio
/// stack running in the owner's session.</para>
///
/// <para>The level is applied to these SAMPLES — see <see cref="Scale"/>. Nothing in this file
/// or in <see cref="GreetingPlayer"/> touches the machine's mixer, and a structural test reads
/// every companion source to prove no endpoint-volume API name appears in any of them.</para>
/// </summary>
public sealed record WavAudio(AudioFormat Format, byte[] Pcm16)
{
    /// <summary>Sample rates a render endpoint can plausibly be asked for. Outside this, refuse.</summary>
    public const int MinSampleRate = 8000;

    public const int MaxSampleRate = 192000;

    public double DurationMs => Format.MsForBytes(Pcm16.Length);

    /// <summary>
    /// Parses a RIFF/WAVE container carrying 16-bit PCM. Anything else — a different bit depth,
    /// a compressed codec, more than two channels, a truncated container — is a
    /// <c>validation_error</c>, never a best-effort guess at what the bytes might have meant.
    /// </summary>
    public static WavAudio Parse(ReadOnlySpan<byte> bytes)
    {
        if (bytes.Length < 12
            || !Ascii(bytes[..4], "RIFF")
            || !Ascii(bytes.Slice(8, 4), "WAVE"))
        {
            throw Invalid("payload.audio is not a RIFF/WAVE container");
        }

        var sampleRate = 0;
        var channels = 0;
        var bitsPerSample = 0;
        var formatTag = 0;
        ReadOnlySpan<byte> data = default;
        var sawFormat = false;
        var sawData = false;

        var offset = 12;
        while (offset + 8 <= bytes.Length)
        {
            var chunkId = bytes.Slice(offset, 4);
            var chunkSize = (int)Math.Min(
                BinaryPrimitives.ReadUInt32LittleEndian(bytes.Slice(offset + 4, 4)),
                int.MaxValue);
            var body = offset + 8;
            if (chunkSize < 0 || body + chunkSize > bytes.Length)
            {
                // A chunk that claims more than the file holds: the last data chunk of a
                // truncated download looks exactly like this, so it is refused rather than
                // played as far as it goes.
                if (Ascii(chunkId, "data"))
                {
                    throw Invalid("payload.audio data chunk is truncated");
                }

                break;
            }

            if (Ascii(chunkId, "fmt ") && chunkSize >= 16)
            {
                formatTag = BinaryPrimitives.ReadUInt16LittleEndian(bytes.Slice(body, 2));
                channels = BinaryPrimitives.ReadUInt16LittleEndian(bytes.Slice(body + 2, 2));
                sampleRate = (int)BinaryPrimitives.ReadUInt32LittleEndian(bytes.Slice(body + 4, 4));
                bitsPerSample = BinaryPrimitives.ReadUInt16LittleEndian(bytes.Slice(body + 14, 2));
                if (formatTag == 0xFFFE && chunkSize >= 40)
                {
                    // WAVE_FORMAT_EXTENSIBLE: the real tag is the first two bytes of the
                    // sub-format GUID, at offset 24 of the chunk body.
                    formatTag = BinaryPrimitives.ReadUInt16LittleEndian(bytes.Slice(body + 24, 2));
                }

                sawFormat = true;
            }
            else if (Ascii(chunkId, "data"))
            {
                data = bytes.Slice(body, chunkSize);
                sawData = true;
            }

            // Chunks are word-aligned; an odd size carries one pad byte.
            offset = body + chunkSize + (chunkSize % 2);
        }

        if (!sawFormat)
        {
            throw Invalid("payload.audio has no fmt chunk");
        }

        if (!sawData)
        {
            throw Invalid("payload.audio has no data chunk");
        }

        if (formatTag != 1)
        {
            throw Invalid($"payload.audio must be uncompressed PCM (format tag {formatTag} is not supported)");
        }

        if (bitsPerSample != AudioFormat.BitsPerSample)
        {
            throw Invalid($"payload.audio must be 16-bit PCM (this file is {bitsPerSample}-bit)");
        }

        if (channels is not (1 or 2))
        {
            throw Invalid($"payload.audio must be mono or stereo (this file has {channels} channels)");
        }

        if (sampleRate is < MinSampleRate or > MaxSampleRate)
        {
            throw Invalid($"payload.audio sample rate {sampleRate} Hz is outside {MinSampleRate}..{MaxSampleRate}");
        }

        var format = new AudioFormat(sampleRate, channels);
        var frameBytes = format.BytesPerSampleFrame;
        var usable = data.Length - (data.Length % frameBytes);
        if (usable <= 0)
        {
            throw Invalid("payload.audio contains no complete sample frames");
        }

        return new WavAudio(format, data[..usable].ToArray());
    }

    /// <summary>
    /// The first <paramref name="maxMilliseconds"/> of the audio, cut on a whole sample frame.
    /// A greeting longer than the cap is trimmed rather than refused: the owner asked to be
    /// greeted, and a slightly short greeting serves that better than silence plus an error.
    /// </summary>
    public WavAudio Truncate(int maxMilliseconds)
    {
        if (maxMilliseconds <= 0)
        {
            return this;
        }

        var limit = Format.BytesForMs(maxMilliseconds);
        return limit >= Pcm16.Length ? this : new WavAudio(Format, Pcm16[..limit]);
    }

    /// <summary>
    /// Scales every sample by <paramref name="level"/> (0..1), with saturation at full scale.
    /// This is the ONLY volume control in this capability: the machine's mixer is exactly where
    /// the owner left it, before, during and after the greeting.
    /// </summary>
    public static byte[] Scale(ReadOnlySpan<byte> pcm16, double level)
    {
        var factor = Math.Clamp(level, 0.0, 1.0);
        var scaled = new byte[pcm16.Length - (pcm16.Length % 2)];
        for (var i = 0; i + 1 < scaled.Length; i += 2)
        {
            var sample = BinaryPrimitives.ReadInt16LittleEndian(pcm16.Slice(i, 2));
            var value = (int)Math.Round(sample * factor);
            var clamped = (short)Math.Clamp(value, short.MinValue, short.MaxValue);
            BinaryPrimitives.WriteInt16LittleEndian(scaled.AsSpan(i, 2), clamped);
        }

        return scaled;
    }

    private static bool Ascii(ReadOnlySpan<byte> bytes, string expected)
        => bytes.Length == expected.Length && Encoding.ASCII.GetString(bytes) == expected;

    private static CapabilityException Invalid(string message)
        => new(ErrorClasses.ValidationError, message, retryable: false);
}
