using System.Text;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// 2026-09-19, production (device MAIL, agent 0.6.0): <c>screen.capture</c> of the owner's
/// maximized Chrome (2576x1416) failed twice with <c>postcondition_failed</c>, "the result is
/// 1805360 bytes; one broker frame carries at most 1048576". The capture cap was a
/// free-standing 2 MiB of PNG; the frame the ack travels in carries 1 MiB of JSON. Two halves
/// of one contract that never read each other (ADR-0175). These tests read both, and drive
/// the fit with pictures - no screen, no window, no desktop.
/// </summary>
public sealed class CaptureFrameFitTests
{
    private const int OwnerChromeWidth = 2576;
    private const int OwnerChromeHeight = 1416;

    private static int Constant(Type type, string name)
        => (int)type.GetField(name)!.GetRawConstantValue()!;

    /// <summary>
    /// A picture whose first <paramref name="noisyRows"/> rows are noise (which PNG cannot
    /// compress) and whose remainder is one flat colour (which costs almost nothing): the
    /// PNG's size is chosen by the row count, the dimensions are the owner's window.
    /// </summary>
    private static RgbImage Picture(int width, int height, int noisyRows)
    {
        var rgb = new byte[width * height * 3];
        Array.Fill(rgb, (byte)0x20);
        new Random(20260919).NextBytes(rgb.AsSpan(0, width * noisyRows * 3));
        return new RgbImage(width, height, rgb);
    }

    private static CommandAckMessage AckCarrying(JsonObject result) => new()
    {
        CommandId = "11111111-2222-3333-4444-555555555555",
        Status = AckStatus.Succeeded,
        Result = result,
    };

    private static int FrameBytes(ProtocolMessage message) => Encoding.UTF8.GetByteCount(ProtocolJson.Serialize(message));

    private static JsonObject ChromeWindow() => new()
    {
        ["window_id"] = "w-0007",
        ["title"] = new string('ş', 1024) + " - YouTube - Google Chrome",
        ["state"] = "maximized",
    };

    [Fact]
    public void The_capture_budget_is_derived_from_the_frame_bound_and_fits_it_after_base64()
    {
        long frame = Constant(typeof(ProtocolConstants), nameof(ProtocolConstants.MaxFrameBytes));
        long capture = Constant(typeof(OperatorCapabilityNames), nameof(OperatorCapabilityNames.MaxCaptureBytes));
        long envelope = Constant(typeof(OperatorCapabilityNames), nameof(OperatorCapabilityNames.CaptureEnvelopeBytes));

        var base64Chars = (capture + 2) / 3 * 4;
        Assert.True(
            base64Chars + envelope <= frame,
            $"a {capture} byte PNG is {base64Chars} base64 characters; with the {envelope} byte envelope that is over the {frame} byte frame");

        // Generous, and not "fixed" by having no budget at all.
        Assert.True(envelope >= 16 * 1024, $"the envelope is {envelope} bytes");
        Assert.True(capture >= 512 * 1024, $"the capture budget is {capture} bytes");
        Assert.True(frame - envelope == Constant(typeof(CaptureFit), nameof(CaptureFit.MaxResultBytes)), "the measured bound is not the frame less the envelope");
    }

    [Fact]
    public void The_owners_maximized_window_comes_back_smaller_in_one_frame_with_its_true_scale()
    {
        // ~1.35 MB of PNG at full size: what production captured, and what the old 2 MiB cap let through.
        var image = Picture(OwnerChromeWidth, OwnerChromeHeight, noisyRows: 175);
        var fullSize = PngEncoder.Encode(image).Length;
        Assert.InRange(fullSize, 1_200_000, 2 * 1024 * 1024);

        var result = CaptureFit.BuildResult(image, "w-0007", ChromeWindow());

        var ack = AckCarrying(result);
        var bytes = FrameBytes(ack);
        Assert.True(bytes <= ProtocolConstants.MaxFrameBytes, $"the ack is {bytes} bytes");
        Assert.Same(ack, ProtocolConstants.FitToFrame(ack, bytes));

        var scale = result["scale"]!.GetValue<int>();
        Assert.True(scale > 1, $"scale is {scale}");
        var png = Convert.FromBase64String(result["png_base64"]!.GetValue<string>());
        var (width, height) = PngEncoder.ReadHeader(png);
        Assert.Equal(result["width"]!.GetValue<int>(), width);
        Assert.Equal(result["height"]!.GetValue<int>(), height);
        Assert.Equal(png.Length, result["bytes"]!.GetValue<int>());

        // The scale is what Cloud Core multiplies a vision coordinate by: it must be the
        // real factor between this picture and the window, computed here from the window.
        Assert.Equal(OwnerChromeWidth / scale, width);
        Assert.Equal(OwnerChromeHeight / scale, height);
        Assert.Equal("w-0007", result["window_id"]!.GetValue<string>());
        Assert.Equal("maximized", result["observed"]!["window"]!["state"]!.GetValue<string>());
    }

    [Fact]
    public void A_picture_within_the_png_budget_whose_json_is_not_is_still_made_to_fit()
    {
        // All noise, sized to pass the PNG budget. Its base64 is within the frame by the 4/3
        // arithmetic, but the protocol's encoder writes each '+' as six bytes, and noise has
        // one in every 64 characters: only measuring the serialized result catches this.
        // The largest square of noise whose PNG passes the budget (the deflate stream's size
        // for noise is the zlib build's business, so the side is found, not written down).
        var side = 500;
        var image = Picture(side, side, noisyRows: side);
        var fullSize = PngEncoder.Encode(image);
        while (fullSize.Length > OperatorCapabilityNames.MaxCaptureBytes)
        {
            side--;
            image = Picture(side, side, noisyRows: side);
            fullSize = PngEncoder.Encode(image);
        }

        Assert.True(side < 500, "a 500 px square of noise already fits; start larger");
        var naive = AckCarrying(new JsonObject { ["png_base64"] = Convert.ToBase64String(fullSize) });
        Assert.True(FrameBytes(naive) > ProtocolConstants.MaxFrameBytes, $"the unfitted ack is only {FrameBytes(naive)} bytes");

        var result = CaptureFit.BuildResult(image, null, null);

        Assert.True(FrameBytes(AckCarrying(result)) <= ProtocolConstants.MaxFrameBytes);
        Assert.True(CaptureFit.SerializedBytes(result) <= CaptureFit.MaxResultBytes);
        Assert.Equal(2, result["scale"]!.GetValue<int>());
        Assert.Equal(side / 2, result["width"]!.GetValue<int>());
    }

    [Fact]
    public void A_small_picture_is_returned_whole_at_scale_one()
    {
        var image = Picture(640, 480, noisyRows: 40);

        var result = CaptureFit.BuildResult(image, null, null);

        Assert.Equal(1, result["scale"]!.GetValue<int>());
        Assert.Equal(640, result["width"]!.GetValue<int>());
        Assert.Equal(480, result["height"]!.GetValue<int>());
        Assert.Equal((640, 480), PngEncoder.ReadHeader(Convert.FromBase64String(result["png_base64"]!.GetValue<string>())));
        Assert.Null(result["window_id"]);
        Assert.True(FrameBytes(AckCarrying(result)) <= ProtocolConstants.MaxFrameBytes);
    }

    [Fact]
    public void More_halvings_than_two_are_taken_when_the_picture_needs_them()
    {
        // All noise on a surface twice the owner's on a side: 1/4 - where the old loop
        // stopped - is still megabytes; the fit goes on rather than refusing.
        var image = Picture(OwnerChromeWidth * 2, OwnerChromeHeight * 2, noisyRows: OwnerChromeHeight * 2);
        Assert.True(PngEncoder.Encode(image.Halve().Halve()).Length > OperatorCapabilityNames.MaxCaptureBytes);

        var result = CaptureFit.BuildResult(image, null, null);

        Assert.True(result["scale"]!.GetValue<int>() > 4, $"scale is {result["scale"]}");
        Assert.True(result["scale"]!.GetValue<int>() <= OperatorCapabilityNames.MaxCaptureScale);
        Assert.True(FrameBytes(AckCarrying(result)) <= ProtocolConstants.MaxFrameBytes);
    }

    [Fact]
    public void A_picture_that_cannot_fit_at_the_smallest_scale_is_a_typed_validation_error()
    {
        var image = Picture(OwnerChromeWidth, OwnerChromeHeight, noisyRows: OwnerChromeHeight);

        var error = Assert.Throws<CapabilityException>(() => CaptureFit.BuildResult(image, null, null, maxScale: 2));

        Assert.Equal(ErrorClasses.ValidationError, error.ErrorClass);
        Assert.False(error.Retryable);
        Assert.Contains("1/2 scale", error.Message, StringComparison.Ordinal);
    }
}
