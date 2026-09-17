using System.Text;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>
/// 2026-09-17, production: a scene.inspect result larger than the broker's frame bound closed
/// the connection, the command was re-delivered on the next one, and the device reconnected
/// about once a second until the command expired. A result that cannot fit one frame is now
/// answered as a failure, so the command ends. (The bound itself is read from both sides by
/// services/api/tests/unit/test_broker_frame_bound.py.)
/// </summary>
public sealed class FrameBoundTests
{
    private static CommandAckMessage Succeeded(int payloadChars) => new()
    {
        CommandId = "11111111-2222-3333-4444-555555555555",
        Status = AckStatus.Succeeded,
        Result = new JsonObject { ["png_base64"] = new string('A', payloadChars) },
    };

    private static int Bytes(ProtocolMessage message) => Encoding.UTF8.GetByteCount(ProtocolJson.Serialize(message));

    [Fact]
    public void A_result_that_fits_is_sent_unchanged()
    {
        var ack = Succeeded(90_000);
        var bytes = Bytes(ack);
        Assert.True(bytes < ProtocolConstants.MaxFrameBytes);
        Assert.Same(ack, ProtocolConstants.FitToFrame(ack, bytes));
    }

    [Fact]
    public void A_result_over_the_frame_bound_ends_the_command_as_a_failure_that_fits()
    {
        var ack = Succeeded(ProtocolConstants.MaxFrameBytes + 10);
        var bytes = Bytes(ack);
        Assert.True(bytes > ProtocolConstants.MaxFrameBytes);

        var fitted = ProtocolConstants.FitToFrame(ack, bytes);

        Assert.Equal(ack.CommandId, fitted.CommandId);
        Assert.Equal(AckStatus.Failed, fitted.Status);
        Assert.Null(fitted.Result);
        Assert.NotNull(fitted.Error);
        Assert.Equal(ErrorClasses.PostconditionFailed, fitted.Error!.Class);
        Assert.False(fitted.Error.Retryable);
        Assert.Contains(bytes.ToString(System.Globalization.CultureInfo.InvariantCulture), fitted.Error.Message, StringComparison.Ordinal);
        Assert.True(Bytes(fitted) < 4096);
        MessageValidator.ValidateErrorObject(fitted.Error);
    }

    [Fact]
    public void The_connection_sends_through_the_fit()
    {
        // Structural: the one send path of the broker connection applies the fit to acks.
        var root = new DirectoryInfo(AppContext.BaseDirectory);
        while (root is not null && !Directory.Exists(Path.Combine(root.FullName, "src", "PagentOS.Agent.Core")))
        {
            root = root.Parent;
        }

        Assert.NotNull(root);
        var source = File.ReadAllText(Path.Combine(root!.FullName, "src", "PagentOS.Agent.Core", "Connection", "AgentConnection.cs"));
        Assert.Contains("ProtocolConstants.FitToFrame(ack, bytes.Length)", source, StringComparison.Ordinal);
        Assert.Contains("private const int MaxFrameBytes = ProtocolConstants.MaxFrameBytes;", source, StringComparison.Ordinal);
    }
}
