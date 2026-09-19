using System.Runtime.InteropServices.WindowsRuntime;
using System.Text;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Operator;
using Windows.Graphics.Imaging;
using Windows.Storage.Streams;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// 2026-09-19 17:14:36Z, production, the day ADR-0175 shipped: the owner's maximized Chrome
/// (2576x1416, the YouTube home page - photographs) came back as PNG at scale 4, 644x354,
/// its titles unreadable, and the vision provider answered "not found" for a video plainly
/// on the screen. PNG cannot carry a photographic 3.6 MP window in one frame; JPEG can
/// (ADR-0176). Pictures only - no screen, no window, no desktop.
/// </summary>
public sealed class CaptureJpegTests
{
    private const int OwnerChromeWidth = 2576;
    private const int OwnerChromeHeight = 1416;

    /// <summary>
    /// A photograph-like surface: smooth colour that changes everywhere, plus sensor-like
    /// noise of amplitude <paramref name="noise"/>. PNG (no filter, fastest deflate) finds
    /// nothing to compress in it; JPEG was made for it.
    /// </summary>
    private static RgbImage Photograph(int width, int height, int noise)
    {
        var sx = new double[width];
        var sy = new double[height];
        for (var x = 0; x < width; x++)
        {
            sx[x] = Math.Sin(x / 97.0) + (0.5 * Math.Sin(x / 23.0));
        }

        for (var y = 0; y < height; y++)
        {
            sy[y] = Math.Cos(y / 71.0) + (0.5 * Math.Sin(y / 19.0));
        }

        var grain = new byte[width * height * 3];
        new Random(20260919).NextBytes(grain);
        var rgb = new byte[grain.Length];
        var i = 0;
        for (var y = 0; y < height; y++)
        {
            for (var x = 0; x < width; x++)
            {
                for (var c = 0; c < 3; c++, i++)
                {
                    var smooth = 128 + (40 * sx[x] * (c + 1) / 2) + (30 * sy[y]) + (10 * sx[x] * sy[y] * (3 - c));
                    var value = smooth + ((grain[i] - 128) * noise / 128.0);
                    rgb[i] = (byte)Math.Clamp(value, 0, 255);
                }
            }
        }

        return new RgbImage(width, height, rgb);
    }

    private static RgbImage Noise(int width, int height)
    {
        var rgb = new byte[width * height * 3];
        new Random(7).NextBytes(rgb);
        return new RgbImage(width, height, rgb);
    }

    private static CommandAckMessage AckCarrying(JsonObject result) => new()
    {
        CommandId = "11111111-2222-3333-4444-555555555555",
        Status = AckStatus.Succeeded,
        Result = result,
    };

    private static int FrameBytes(ProtocolMessage message) => Encoding.UTF8.GetByteCount(ProtocolJson.Serialize(message));

    /// <summary>A real decode, by the platform's decoder: the dimensions and every pixel.</summary>
    private static (int Width, int Height, int PixelBytes) Decode(byte[] jpeg)
    {
        return Task.Run(async () =>
        {
            using var stream = new InMemoryRandomAccessStream();
            await stream.WriteAsync(jpeg.AsBuffer());
            stream.Seek(0);
            var decoder = await BitmapDecoder.CreateAsync(BitmapDecoder.JpegDecoderId, stream);
            var pixels = await decoder.GetPixelDataAsync();
            return ((int)decoder.PixelWidth, (int)decoder.PixelHeight, pixels.DetachPixelData().Length);
        }).GetAwaiter().GetResult();
    }

    [Fact]
    public void The_owners_photographic_window_goes_out_whole_as_jpeg_in_one_frame()
    {
        var image = Photograph(OwnerChromeWidth, OwnerChromeHeight, noise: 8);
        var window = new JsonObject { ["window_id"] = "w-0007", ["title"] = "YouTube - Google Chrome", ["state"] = "maximized" };

        // Why this format exists: the same picture as PNG has to be shrunk.
        var png = CaptureFit.BuildResult(image, "w-0007", window);
        Assert.True(png["scale"]!.GetValue<int>() >= 2, $"PNG carried it at scale {png["scale"]}");

        var result = CaptureFit.BuildResult(image, "w-0007", window, format: CaptureFormat.Jpeg);

        Assert.Equal(1, result["scale"]!.GetValue<int>());
        Assert.Equal(OwnerChromeWidth, result["width"]!.GetValue<int>());
        Assert.Equal(OwnerChromeHeight, result["height"]!.GetValue<int>());
        Assert.Equal("image/jpeg", result["mime"]!.GetValue<string>());
        Assert.Contains(result["quality"]!.GetValue<int>(), CaptureFit.JpegQualities);
        Assert.Equal(
            ["width", "height", "image_base64", "mime", "bytes", "scale", "quality", "window_id", "observed"],
            result.Select(p => p.Key).ToArray());
        Assert.Equal("maximized", result["observed"]!["window"]!["state"]!.GetValue<string>());

        var ack = AckCarrying(result);
        var bytes = FrameBytes(ack);
        Assert.True(bytes <= ProtocolConstants.MaxFrameBytes, $"the ack is {bytes} bytes");
        Assert.Same(ack, ProtocolConstants.FitToFrame(ack, bytes));

        var jpeg = Convert.FromBase64String(result["image_base64"]!.GetValue<string>());
        Assert.Equal(jpeg.Length, result["bytes"]!.GetValue<int>());
        Assert.Equal(0xFF, jpeg[0]);
        Assert.Equal(0xD8, jpeg[1]);
        Assert.Equal((OwnerChromeWidth, OwnerChromeHeight), JpegEncoder.ReadHeader(jpeg));
        Assert.Equal((OwnerChromeWidth, OwnerChromeHeight, OwnerChromeWidth * OwnerChromeHeight * 4), Decode(jpeg));
    }

    [Fact]
    public void Quality_is_given_up_before_resolution()
    {
        // The least noisy photograph whose quality-80 JPEG does NOT fit the frame at full
        // size: found, not written down - the encoder's output size is the platform's business.
        RgbImage? image = null;
        for (var noise = 8; noise <= 96 && image is null; noise += 4)
        {
            var candidate = Photograph(OwnerChromeWidth, OwnerChromeHeight, noise);
            var atEighty = new JsonObject { ["image_base64"] = Convert.ToBase64String(JpegEncoder.Encode(candidate, 80)) };
            if (CaptureFit.SerializedBytes(atEighty) > CaptureFit.MaxResultBytes)
            {
                image = candidate;
            }
        }

        Assert.NotNull(image);

        var result = CaptureFit.BuildResult(image!, null, null, format: CaptureFormat.Jpeg);

        Assert.Equal(1, result["scale"]!.GetValue<int>());
        Assert.Equal(OwnerChromeWidth, result["width"]!.GetValue<int>());
        Assert.True(result["quality"]!.GetValue<int>() < 80, $"quality is {result["quality"]}");
        Assert.Contains(result["quality"]!.GetValue<int>(), CaptureFit.JpegQualities);
        Assert.True(FrameBytes(AckCarrying(result)) <= ProtocolConstants.MaxFrameBytes);
    }

    [Fact]
    public void The_qualities_are_tried_from_eighty_down_to_fifty()
    {
        Assert.Equal([80, 70, 60, 50], CaptureFit.JpegQualities);
    }

    [Fact]
    public void A_lower_quality_is_a_smaller_picture()
    {
        var image = Photograph(640, 480, noise: 24);
        var sizes = CaptureFit.JpegQualities.Select(q => JpegEncoder.Encode(image, q).Length).ToArray();
        Assert.Equal(sizes.OrderByDescending(s => s).ToArray(), sizes);
        Assert.True(sizes[^1] < sizes[0], $"quality 50 is {sizes[^1]} bytes, quality 80 is {sizes[0]}");
    }

    [Fact]
    public void A_picture_no_quality_can_fit_is_halved_and_says_so_truthfully()
    {
        var result = CaptureFit.BuildResult(Noise(OwnerChromeWidth, OwnerChromeHeight), null, null, format: CaptureFormat.Jpeg);

        var scale = result["scale"]!.GetValue<int>();
        Assert.True(scale > 1, $"scale is {scale}");
        var jpeg = Convert.FromBase64String(result["image_base64"]!.GetValue<string>());
        Assert.Equal((OwnerChromeWidth / scale, OwnerChromeHeight / scale), JpegEncoder.ReadHeader(jpeg));
        Assert.Equal(OwnerChromeWidth / scale, result["width"]!.GetValue<int>());
        Assert.Contains(result["quality"]!.GetValue<int>(), CaptureFit.JpegQualities);
        Assert.True(FrameBytes(AckCarrying(result)) <= ProtocolConstants.MaxFrameBytes);
    }

    [Fact]
    public void A_jpeg_that_cannot_fit_at_the_smallest_scale_is_a_typed_validation_error()
    {
        var error = Assert.Throws<CapabilityException>(
            () => CaptureFit.BuildResult(Noise(OwnerChromeWidth, OwnerChromeHeight), null, null, maxScale: 1, format: CaptureFormat.Jpeg));

        Assert.Equal(ErrorClasses.ValidationError, error.ErrorClass);
        Assert.False(error.Retryable);
        Assert.Contains("quality 50", error.Message, StringComparison.Ordinal);
        Assert.Contains("1/1 scale", error.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void Png_stays_the_default_and_keeps_its_shape()
    {
        var result = CaptureFit.BuildResult(Photograph(320, 200, noise: 4), null, null);

        Assert.Equal(
            ["width", "height", "png_base64", "bytes", "scale", "window_id", "observed"],
            result.Select(p => p.Key).ToArray());
        Assert.Equal((320, 200), PngEncoder.ReadHeader(Convert.FromBase64String(result["png_base64"]!.GetValue<string>())));
    }

    [Theory]
    [InlineData(null, CaptureFormat.Png)]
    [InlineData("png", CaptureFormat.Png)]
    [InlineData("PNG", CaptureFormat.Png)]
    [InlineData("jpeg", CaptureFormat.Jpeg)]
    [InlineData("JPEG", CaptureFormat.Jpeg)]
    public void The_two_formats_are_named_png_and_jpeg(string? format, CaptureFormat expected)
    {
        Assert.Equal(expected, CaptureFit.ParseFormat(format));
    }

    [Theory]
    [InlineData("")]
    [InlineData("jpg")]
    [InlineData("webp")]
    [InlineData("bmp")]
    [InlineData("jpeg ")]
    public void Any_other_format_is_refused(string format)
    {
        var error = Assert.Throws<CapabilityException>(() => CaptureFit.ParseFormat(format));
        Assert.Equal(ErrorClasses.ValidationError, error.ErrorClass);
        Assert.False(error.Retryable);
    }

    [Fact]
    public void A_jpeg_header_is_read_from_the_frame_segment_and_nothing_else_is_a_jpeg()
    {
        Assert.Equal((37, 21), JpegEncoder.ReadHeader(JpegEncoder.Encode(Photograph(37, 21, noise: 0), 80)));
        Assert.Throws<InvalidDataException>(() => JpegEncoder.ReadHeader(PngEncoder.Encode(Photograph(8, 8, noise: 0))));
    }
}
