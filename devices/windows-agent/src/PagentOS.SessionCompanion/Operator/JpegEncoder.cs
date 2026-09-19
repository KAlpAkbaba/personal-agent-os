using Windows.Graphics.Imaging;
using Windows.Storage.Streams;

namespace PagentOS.SessionCompanion.Operator;

/// <summary>
/// JPEG for <c>screen.capture</c> (ADR-0176) through the WinRT imaging stack
/// (<see cref="BitmapEncoder"/>, which is WIC underneath): shipped with Windows, in memory
/// only (<see cref="InMemoryRandomAccessStream"/> - nothing touches the disk, the same rule
/// <see cref="ScreenCapture"/> keeps), and still no <c>System.Drawing</c>/GDI+.
/// <para>
/// Threading: the WinRT calls are asynchronous and need no UI thread or dispatcher. The
/// capability dispatch is synchronous, so the encode runs on the thread pool and the caller
/// blocks on THAT task - never on a continuation that could want the caller's own context
/// back, which is the shape that deadlocks.
/// </para>
/// </summary>
public static class JpegEncoder
{
    /// <param name="image">8-bit RGB pixels.</param>
    /// <param name="quality">1…100; the encoder's <c>ImageQuality</c> is this over 100.</param>
    public static byte[] Encode(RgbImage image, int quality)
    {
        ArgumentOutOfRangeException.ThrowIfLessThan(quality, 1);
        ArgumentOutOfRangeException.ThrowIfGreaterThan(quality, 100);

        // The encoder takes BGRA; alpha is declared ignored, and 255 keeps it honest anyway.
        var bgra = new byte[image.Width * image.Height * 4];
        var rgb = image.Rgb;
        for (int src = 0, dst = 0; dst < bgra.Length; src += 3, dst += 4)
        {
            bgra[dst] = rgb[src + 2];
            bgra[dst + 1] = rgb[src + 1];
            bgra[dst + 2] = rgb[src];
            bgra[dst + 3] = 255;
        }

        return Task.Run(() => EncodeAsync(bgra, image.Width, image.Height, quality)).GetAwaiter().GetResult();
    }

    /// <summary>
    /// Width and height from a JPEG's frame header (the first SOFn segment) - what a caller
    /// checks a capture with when it has no decoder.
    /// </summary>
    public static (int Width, int Height) ReadHeader(byte[] jpeg)
    {
        if (jpeg.Length < 4 || jpeg[0] != 0xFF || jpeg[1] != 0xD8)
        {
            throw new InvalidDataException("not a JPEG");
        }

        var offset = 2;
        while (offset + 9 < jpeg.Length)
        {
            if (jpeg[offset] != 0xFF)
            {
                throw new InvalidDataException("not a JPEG: a segment does not start with a marker");
            }

            var marker = jpeg[offset + 1];
            if (marker == 0xFF)
            {
                offset++; // fill byte
                continue;
            }

            var length = (jpeg[offset + 2] << 8) | jpeg[offset + 3];

            // SOF0…SOF15, less DHT (C4), JPG (C8) and DAC (CC), which share the range.
            if (marker is >= 0xC0 and <= 0xCF and not 0xC4 and not 0xC8 and not 0xCC)
            {
                var height = (jpeg[offset + 5] << 8) | jpeg[offset + 6];
                var width = (jpeg[offset + 7] << 8) | jpeg[offset + 8];
                return (width, height);
            }

            offset += 2 + length;
        }

        throw new InvalidDataException("not a JPEG: no frame header");
    }

    private static async Task<byte[]> EncodeAsync(byte[] bgra, int width, int height, int quality)
    {
        using var stream = new InMemoryRandomAccessStream();
        var options = new BitmapPropertySet
        {
            ["ImageQuality"] = new BitmapTypedValue(quality / 100f, Windows.Foundation.PropertyType.Single),
        };
        var encoder = await BitmapEncoder.CreateAsync(BitmapEncoder.JpegEncoderId, stream, options).AsTask().ConfigureAwait(false);
        encoder.SetPixelData(BitmapPixelFormat.Bgra8, BitmapAlphaMode.Ignore, (uint)width, (uint)height, 96, 96, bgra);
        await encoder.FlushAsync().AsTask().ConfigureAwait(false);

        var bytes = new byte[stream.Size];
        using var reader = new DataReader(stream.GetInputStreamAt(0));
        await reader.LoadAsync((uint)bytes.Length).AsTask().ConfigureAwait(false);
        reader.ReadBytes(bytes);
        return bytes;
    }
}
