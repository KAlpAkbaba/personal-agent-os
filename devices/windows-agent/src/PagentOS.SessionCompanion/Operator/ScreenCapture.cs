using System.IO.Compression;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
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
