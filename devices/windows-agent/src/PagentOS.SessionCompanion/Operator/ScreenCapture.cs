using System.IO.Compression;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion.Operator;

/// <summary>Pixels in memory: 8-bit RGB, row-major, no padding. Never written to disk by this module.</summary>
public sealed record RgbImage(int Width, int Height, byte[] Rgb)
{
    /// <summary>A 2x box downscale, used only when a full-size PNG would exceed the result cap.</summary>
    public RgbImage Halve()
    {
        var width = Math.Max(1, Width / 2);
        var height = Math.Max(1, Height / 2);
        var output = new byte[width * height * 3];
        for (var y = 0; y < height; y++)
        {
            var sy = Math.Min(y * 2, Height - 1);
            var sy2 = Math.Min(sy + 1, Height - 1);
            for (var x = 0; x < width; x++)
            {
                var sx = Math.Min(x * 2, Width - 1);
                var sx2 = Math.Min(sx + 1, Width - 1);
                for (var c = 0; c < 3; c++)
                {
                    var sum = Rgb[(sy * Width + sx) * 3 + c] + Rgb[(sy * Width + sx2) * 3 + c]
                              + Rgb[(sy2 * Width + sx) * 3 + c] + Rgb[(sy2 * Width + sx2) * 3 + c];
                    output[(y * width + x) * 3 + c] = (byte)(sum / 4);
                }
            }
        }

        return new RgbImage(width, height, output);
    }
}

/// <summary>
/// <c>screen.capture</c> (M19_DIGITAL_OPERATOR_SPEC.md §2/§3): <c>PrintWindow</c> for one
/// window, <c>BitBlt</c> of the primary screen otherwise, into a DIB this process owns, then
/// PNG through <see cref="PngEncoder"/>. <c>System.Drawing</c> is not used, on purpose: it
/// would pull GDI+ into a process whose dependency set is otherwise exactly what the protocol
/// needs, and a bitmap that lives only in this method cannot be left behind on disk.
/// </summary>
[SupportedOSPlatform("windows")]
public static class ScreenCapture
{
    /// <summary>A capture larger than this on a side is refused rather than silently cropped.</summary>
    public const int MaxDimension = 8192;

    public static RgbImage CaptureWindow(IntPtr hwnd)
    {
        if (!OperatorNative.GetWindowRect(hwnd, out var rect))
        {
            throw new CapabilityException(ErrorClasses.UiStateChanged, "the window's rectangle could not be read", retryable: true);
        }

        var width = rect.Right - rect.Left;
        var height = rect.Bottom - rect.Top;
        if (width <= 0 || height <= 0)
        {
            throw new CapabilityException(ErrorClasses.UiStateChanged, "the window has no visible area to capture (minimised?)", retryable: true);
        }

        return Capture(width, height, (hdc) =>
        {
            if (OperatorNative.PrintWindow(hwnd, hdc, OperatorNative.PwRenderFullContent))
            {
                return;
            }

            // PrintWindow refused (some GPU-composed surfaces): copy the window's area of the
            // screen instead, which is what the owner sees there anyway.
            var screen = OperatorNative.GetDC(IntPtr.Zero);
            try
            {
                OperatorNative.BitBlt(hdc, 0, 0, width, height, screen, rect.Left, rect.Top, OperatorNative.SrcCopy | OperatorNative.CaptureBlt);
            }
            finally
            {
                OperatorNative.ReleaseDC(IntPtr.Zero, screen);
            }
        });
    }

    public static RgbImage CapturePrimaryScreen()
    {
        var width = OperatorNative.GetSystemMetrics(OperatorNative.SmCxScreen);
        var height = OperatorNative.GetSystemMetrics(OperatorNative.SmCyScreen);
        if (width <= 0 || height <= 0)
        {
            throw new CapabilityException(ErrorClasses.DependencyUnavailable, "no primary screen to capture", retryable: true);
        }

        return Capture(width, height, (hdc) =>
        {
            var screen = OperatorNative.GetDC(IntPtr.Zero);
            try
            {
                OperatorNative.BitBlt(hdc, 0, 0, width, height, screen, 0, 0, OperatorNative.SrcCopy | OperatorNative.CaptureBlt);
            }
            finally
            {
                OperatorNative.ReleaseDC(IntPtr.Zero, screen);
            }
        });
    }

    private static RgbImage Capture(int width, int height, Action<IntPtr> paint)
    {
        if (width > MaxDimension || height > MaxDimension)
        {
            throw new CapabilityException(ErrorClasses.ValidationError, $"capture of {width}x{height} exceeds {MaxDimension} px on a side", retryable: false);
        }

        var screenDc = OperatorNative.GetDC(IntPtr.Zero);
        var memoryDc = IntPtr.Zero;
        var bitmap = IntPtr.Zero;
        var previous = IntPtr.Zero;
        try
        {
            memoryDc = OperatorNative.CreateCompatibleDC(screenDc);
            var info = new OperatorNative.BitmapInfo
            {
                Header = new OperatorNative.BitmapInfoHeader
                {
                    Size = (uint)Marshal.SizeOf<OperatorNative.BitmapInfoHeader>(),
                    Width = width,
                    Height = -height, // top-down rows
                    Planes = 1,
                    BitCount = 32,
                    Compression = OperatorNative.BiRgb,
                },
            };
            bitmap = OperatorNative.CreateDIBSection(screenDc, ref info, OperatorNative.DibRgbColors, out var bits, IntPtr.Zero, 0);
            if (bitmap == IntPtr.Zero || bits == IntPtr.Zero)
            {
                throw new CapabilityException(ErrorClasses.DependencyUnavailable, "a capture surface could not be created", retryable: true);
            }

            previous = OperatorNative.SelectObject(memoryDc, bitmap);
            paint(memoryDc);

            var bgra = new byte[width * height * 4];
            Marshal.Copy(bits, bgra, 0, bgra.Length);
            var rgb = new byte[width * height * 3];
            for (int src = 0, dst = 0; src < bgra.Length; src += 4, dst += 3)
            {
                rgb[dst] = bgra[src + 2];
                rgb[dst + 1] = bgra[src + 1];
                rgb[dst + 2] = bgra[src];
            }

            return new RgbImage(width, height, rgb);
        }
        finally
        {
            if (previous != IntPtr.Zero)
            {
                OperatorNative.SelectObject(memoryDc, previous);
            }

            if (bitmap != IntPtr.Zero)
            {
                OperatorNative.DeleteObject(bitmap);
            }

            if (memoryDc != IntPtr.Zero)
            {
                OperatorNative.DeleteDC(memoryDc);
            }

            OperatorNative.ReleaseDC(IntPtr.Zero, screenDc);
        }
    }
}

/// <summary><c>screen.capture</c>'s <c>payload.format</c> (ADR-0176).</summary>
public enum CaptureFormat
{
    Png,
    Jpeg,
}

/// <summary>
/// Makes a capture fit ONE broker frame (ADR-0175). The picture is halved until two things
/// hold: the PNG is within <see cref="OperatorCapabilityNames.MaxCaptureBytes"/> (the budget
/// derived from the frame bound), and the result AS IT WILL BE SERIALIZED - measured, not
/// estimated, through the protocol's own serializer options, whose encoder writes every
/// base64 <c>+</c> as six bytes - leaves <see cref="OperatorCapabilityNames.CaptureEnvelopeBytes"/>
/// of the frame for the ack around it. <c>scale</c> is the true factor between a coordinate
/// in the returned picture and the same point on the surface: Cloud Core multiplies the
/// vision provider's answer by it before it clicks.
/// <para>
/// ADR-0176: a JPEG capture is fitted against the SAME measured bound, and gives up quality
/// (80, 70, 60, 50) at a scale before it gives up the scale - a photographic 3.6 MP window
/// that PNG can only carry at 1/4 goes out whole.
/// </para>
/// </summary>
public static class CaptureFit
{
    /// <summary>The most a serialized <c>screen.capture</c> result may be, in UTF-8 bytes.</summary>
    public const int MaxResultBytes = ProtocolConstants.MaxFrameBytes - OperatorCapabilityNames.CaptureEnvelopeBytes;

    /// <param name="image">The surface as captured, at full size.</param>
    /// <param name="windowId">The captured window's id, or null for the primary screen.</param>
    /// <param name="observedWindow">The window's read-back, or null.</param>
    /// <param name="maxScale">The smallest scale tried; a test lowers it to reach the refusal.</param>
    /// <param name="format">ADR-0176: PNG (the default, lossless) or JPEG (what a photographic surface needs).</param>
    public static JsonObject BuildResult(
        RgbImage image,
        string? windowId,
        JsonNode? observedWindow,
        int maxScale = OperatorCapabilityNames.MaxCaptureScale,
        CaptureFormat format = CaptureFormat.Png)
    {
        var scale = 1;
        while (true)
        {
            var (result, measured) = format == CaptureFormat.Jpeg
                ? TryJpeg(image, scale, windowId, observedWindow)
                : TryPng(image, scale, windowId, observedWindow);
            if (result is not null)
            {
                return result;
            }

            if (scale >= maxScale || (image.Width == 1 && image.Height == 1))
            {
                throw new CapabilityException(
                    ErrorClasses.ValidationError,
                    $"the capture is {measured}, even at 1/{scale} scale",
                    retryable: false);
            }

            image = image.Halve();
            scale *= 2;
        }
    }

    /// <summary>
    /// ADR-0176: the JPEG qualities tried at ONE scale, in order, before the picture is halved.
    /// Resolution is what a vision provider reads titles with; quality is the cheaper thing
    /// to give up, so it goes first.
    /// </summary>
    public static readonly IReadOnlyList<int> JpegQualities = [80, 70, 60, 50];

    public const string JpegMime = "image/jpeg";

    /// <summary><c>payload.format</c>: absent or "png", or "jpeg"; anything else is refused.</summary>
    public static CaptureFormat ParseFormat(string? format)
    {
        if (format is null || string.Equals(format, "png", StringComparison.OrdinalIgnoreCase))
        {
            return CaptureFormat.Png;
        }

        if (string.Equals(format, "jpeg", StringComparison.OrdinalIgnoreCase))
        {
            return CaptureFormat.Jpeg;
        }

        throw new CapabilityException(ErrorClasses.ValidationError, "payload.format must be \"png\" or \"jpeg\"", retryable: false);
    }

    private static (JsonObject? Result, string Measured) TryPng(RgbImage image, int scale, string? windowId, JsonNode? observedWindow)
    {
        var png = PngEncoder.Encode(image);
        if (png.Length > OperatorCapabilityNames.MaxCaptureBytes)
        {
            return (null, $"{png.Length} bytes as PNG, over the {OperatorCapabilityNames.MaxCaptureBytes} byte cap");
        }

        var result = new JsonObject
        {
            ["width"] = image.Width,
            ["height"] = image.Height,
            ["png_base64"] = Convert.ToBase64String(png),
            ["bytes"] = png.Length,
            ["scale"] = scale,
            ["window_id"] = windowId,
            ["observed"] = new JsonObject { ["window"] = observedWindow?.DeepClone() },
        };
        var serializedBytes = SerializedBytes(result);
        return serializedBytes <= MaxResultBytes
            ? (result, string.Empty)
            : (null, $"{serializedBytes} bytes as a result, over the {MaxResultBytes} bytes one broker frame leaves it");
    }

    private static (JsonObject? Result, string Measured) TryJpeg(RgbImage image, int scale, string? windowId, JsonNode? observedWindow)
    {
        var serializedBytes = 0;
        var quality = 0;
        foreach (var candidate in JpegQualities)
        {
            quality = candidate;
            var jpeg = JpegEncoder.Encode(image, quality);
            var result = new JsonObject
            {
                ["width"] = image.Width,
                ["height"] = image.Height,
                ["image_base64"] = Convert.ToBase64String(jpeg),
                ["mime"] = JpegMime,
                ["bytes"] = jpeg.Length,
                ["scale"] = scale,
                ["quality"] = quality,
                ["window_id"] = windowId,
                ["observed"] = new JsonObject { ["window"] = observedWindow?.DeepClone() },
            };
            serializedBytes = SerializedBytes(result);
            if (serializedBytes <= MaxResultBytes)
            {
                return (result, string.Empty);
            }
        }

        return (null, $"{serializedBytes} bytes as a JPEG result at quality {quality}, over the {MaxResultBytes} bytes one broker frame leaves it");
    }

    /// <summary>The result's size exactly as the ack will carry it: same options, same encoder.</summary>
    public static int SerializedBytes(JsonObject result)
        => System.Text.Encoding.UTF8.GetByteCount(result.ToJsonString(ProtocolJson.Options));
}

/// <summary>
/// A minimal PNG writer: signature, IHDR (8-bit RGB), one IDAT (zlib via
/// <see cref="ZLibStream"/>, filter 0 on every row), IEND. Pure managed, a few dozen lines,
/// and readable by anything that reads PNG.
/// </summary>
public static class PngEncoder
{
    private static readonly byte[] Signature = [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A];
    private static readonly uint[] CrcTable = BuildCrcTable();

    public static byte[] Encode(RgbImage image)
    {
        using var output = new MemoryStream();
        output.Write(Signature);

        var header = new byte[13];
        WriteBigEndian(header, 0, (uint)image.Width);
        WriteBigEndian(header, 4, (uint)image.Height);
        header[8] = 8; // bit depth
        header[9] = 2; // colour type: truecolour
        header[10] = 0;
        header[11] = 0;
        header[12] = 0;
        WriteChunk(output, "IHDR", header);

        using (var raw = new MemoryStream())
        {
            using (var zlib = new ZLibStream(raw, CompressionLevel.Fastest, leaveOpen: true))
            {
                var stride = image.Width * 3;
                var filterByte = new byte[] { 0 };
                for (var y = 0; y < image.Height; y++)
                {
                    zlib.Write(filterByte);
                    zlib.Write(image.Rgb, y * stride, stride);
                }
            }

            WriteChunk(output, "IDAT", raw.ToArray());
        }

        WriteChunk(output, "IEND", []);
        return output.ToArray();
    }

    /// <summary>Width and height from a PNG's IHDR — what a test uses to check the capture without a decoder.</summary>
    public static (int Width, int Height) ReadHeader(byte[] png)
    {
        if (png.Length < 24 || !png.AsSpan(0, 8).SequenceEqual(Signature))
        {
            throw new InvalidDataException("not a PNG");
        }

        return ((int)ReadBigEndian(png, 16), (int)ReadBigEndian(png, 20));
    }

    private static void WriteChunk(Stream output, string type, byte[] data)
    {
        var length = new byte[4];
        WriteBigEndian(length, 0, (uint)data.Length);
        output.Write(length);
        var typeBytes = System.Text.Encoding.ASCII.GetBytes(type);
        output.Write(typeBytes);
        output.Write(data);
        var crc = Crc32(typeBytes, data);
        var crcBytes = new byte[4];
        WriteBigEndian(crcBytes, 0, crc);
        output.Write(crcBytes);
    }

    private static uint Crc32(byte[] first, byte[] second)
    {
        var crc = 0xFFFFFFFFu;
        foreach (var b in first)
        {
            crc = CrcTable[(crc ^ b) & 0xFF] ^ (crc >> 8);
        }

        foreach (var b in second)
        {
            crc = CrcTable[(crc ^ b) & 0xFF] ^ (crc >> 8);
        }

        return crc ^ 0xFFFFFFFFu;
    }

    private static uint[] BuildCrcTable()
    {
        var table = new uint[256];
        for (uint n = 0; n < 256; n++)
        {
            var c = n;
            for (var k = 0; k < 8; k++)
            {
                c = (c & 1) != 0 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            }

            table[n] = c;
        }

        return table;
    }

    private static void WriteBigEndian(byte[] buffer, int offset, uint value)
    {
        buffer[offset] = (byte)(value >> 24);
        buffer[offset + 1] = (byte)(value >> 16);
        buffer[offset + 2] = (byte)(value >> 8);
        buffer[offset + 3] = (byte)value;
    }

    private static uint ReadBigEndian(byte[] buffer, int offset)
        => ((uint)buffer[offset] << 24) | ((uint)buffer[offset + 1] << 16) | ((uint)buffer[offset + 2] << 8) | buffer[offset + 3];
}
