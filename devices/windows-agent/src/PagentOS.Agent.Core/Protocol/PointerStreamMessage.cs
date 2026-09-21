using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace PagentOS.Agent.Core.Protocol;

/// <summary>
/// Broker → agent <c>pointer_stream</c> frame (ADR-0199, stage 2 of the hand control),
/// additive to protocol v1 beside <c>command</c> / <c>heartbeat</c> / <c>voice_sideband</c>:
/// a batch of the owner's own pointer frames — relative moves and button events the browser
/// derived from the owner's hand — for the mouse session Cloud Core opened on this device
/// with <c>pointer.stream_begin</c>.
///
/// <para>Best effort by contract: never acknowledged, never re-delivered, never a command
/// row, never an audit row per frame. The Device Service validates the envelope and the size
/// bound, checks the session is one the companion opened, and forwards the batch over the
/// authenticated pipe as a fire-and-forget <c>pointer.stream</c> request. It never applies a
/// frame itself — Session 0 has no pointer — and a malformed frame is counted and dropped,
/// never answered with an error and never a reason to disconnect: a hand that shakes must
/// not cost the owner the voice session.</para>
///
/// <para>Field names follow ADR-0199 exactly:
/// <c>{"kind":"pointer_stream","session":"&lt;id&gt;","frames":[...]}</c> on Cloud Core's
/// side is <c>{"type":"pointer_stream","session":"&lt;id&gt;","frames":[...]}</c> on this
/// connection, because every device-protocol frame is discriminated by <c>type</c>
/// (packages/schemas/device-protocol.schema.json); <c>session</c> and <c>frames</c> are the
/// ADR's names verbatim, and each frame inside is
/// <c>{"t":"move","dx":int,"dy":int,"seq":n}</c> |
/// <c>{"t":"button","button":"left"|"right","action":"down"|"up"|"click"}</c> |
/// <c>{"t":"end"}</c>.</para>
/// </summary>
public sealed record PointerStreamMessage : ProtocolMessage
{
    /// <summary>The mouse session this batch belongs to — the id <c>pointer.stream_begin</c> carried.</summary>
    [JsonPropertyName("session")]
    public required string Session { get; init; }

    /// <summary>The frames, in order, opaque here; parsed by <see cref="PointerFrame.TryParse"/> where they are applied.</summary>
    [JsonPropertyName("frames")]
    public required JsonArray Frames { get; init; }

    /// <summary>The batch payload the companion receives: <c>{session, frames}</c>, a fresh object.</summary>
    public JsonObject ToPayload() => new()
    {
        ["session"] = Session,
        ["frames"] = (JsonArray)Frames.DeepClone(),
    };

    /// <summary>Bytes on the wire, UTF-8 JSON — what the bound is measured against.</summary>
    public int SerializedBytes() => Encoding.UTF8.GetByteCount(ProtocolJson.Serialize(this));
}

public static class PointerStream
{
    public const string FrameType = "pointer_stream";

    /// <summary>Serialized bound the agent enforces before the pipe — the sideband's, for the same reason: a tiny frame family.</summary>
    public const int MaxFrameBytes = 16 * 1024;

    /// <summary>
    /// The most frames one batch may carry. The browser sends ≤ 30/s and Cloud Core coalesces
    /// moves it could not forward in time, so a batch is a few frames; 64 leaves room for a
    /// server that fell a second behind and none for a firehose.
    /// </summary>
    public const int MaxFramesPerBatch = 64;

    public const int MaxSessionLength = 128;

    /// <summary>Frame kinds (<c>t</c>).</summary>
    public const string Move = "move";
    public const string Button = "button";
    public const string End = "end";

    /// <summary>Buttons and actions of a <c>button</c> frame.</summary>
    public const string Left = "left";
    public const string Right = "right";
    public const string Down = "down";
    public const string Up = "up";
    public const string Click = "click";
}

/// <summary>
/// One parsed pointer frame. <see cref="TryParse"/> is the ONE place the frame vocabulary is
/// read, shared by the companion (which applies frames) and the tests (which prove the
/// shape), so a frame the browser half emits and this half refuses is a contract test
/// failure, never a silent drop found on the owner's hand.
/// </summary>
public sealed record PointerFrame(string Kind, int Dx, int Dy, long Seq, string? ButtonName, string? Action)
{
    public bool IsMove => Kind == PointerStream.Move;

    public bool IsButton => Kind == PointerStream.Button;

    public bool IsEnd => Kind == PointerStream.End;

    /// <summary>
    /// Parses one frame node, strictly: <c>t</c> from the closed set, integers where integers
    /// are declared, the button and action names from their closed sets. Anything else is
    /// false — the caller counts it as dropped. Never throws.
    /// </summary>
    public static bool TryParse(JsonNode? node, out PointerFrame frame)
    {
        frame = null!;
        if (node is not JsonObject obj)
        {
            return false;
        }

        if (!TryString(obj, "t", out var kind))
        {
            return false;
        }

        switch (kind)
        {
            case PointerStream.Move:
                if (!TryInt(obj, "dx", out var dx) || !TryInt(obj, "dy", out var dy))
                {
                    return false;
                }

                long seq = 0;
                if (obj.ContainsKey("seq") && !TryLong(obj, "seq", out seq))
                {
                    return false;
                }

                frame = new PointerFrame(kind, dx, dy, seq, null, null);
                return true;

            case PointerStream.Button:
                if (!TryString(obj, "button", out var button) || button is not (PointerStream.Left or PointerStream.Right))
                {
                    return false;
                }

                if (!TryString(obj, "action", out var action) || action is not (PointerStream.Down or PointerStream.Up or PointerStream.Click))
                {
                    return false;
                }

                frame = new PointerFrame(kind, 0, 0, 0, button, action);
                return true;

            case PointerStream.End:
                frame = new PointerFrame(kind, 0, 0, 0, null, null);
                return true;

            default:
                return false;
        }
    }

    private static bool TryString(JsonObject obj, string key, out string value)
    {
        value = string.Empty;
        if (obj[key] is JsonValue v && v.GetValueKind() == JsonValueKind.String)
        {
            value = v.GetValue<string>();
            return true;
        }

        return false;
    }

    private static bool TryInt(JsonObject obj, string key, out int value)
    {
        value = 0;
        if (obj[key] is JsonValue v && v.GetValueKind() == JsonValueKind.Number)
        {
            // Parsed JSON and an in-process JsonValue built from an int both answer here;
            // a fractional number (1.5 px) is not an int and is refused.
            if (v.TryGetValue<int>(out value))
            {
                return true;
            }

            if (v.TryGetValue<double>(out var d) && Math.Abs(d - Math.Round(d)) < double.Epsilon && d is >= int.MinValue and <= int.MaxValue)
            {
                value = (int)d;
                return true;
            }
        }

        return false;
    }

    private static bool TryLong(JsonObject obj, string key, out long value)
    {
        value = 0;
        if (obj[key] is JsonValue v && v.GetValueKind() == JsonValueKind.Number)
        {
            if (v.TryGetValue<long>(out value))
            {
                return true;
            }

            if (v.TryGetValue<int>(out var i))
            {
                value = i;
                return true;
            }

            if (v.TryGetValue<double>(out var d) && Math.Abs(d - Math.Round(d)) < double.Epsilon)
            {
                value = (long)d;
                return true;
            }
        }

        return false;
    }
}

/// <summary>
/// Where an accepted <c>pointer_stream</c> frame goes. The Device Service supplies the pipe
/// forwarder; with no sink the connection counts the frame and drops it, which is the exact
/// behaviour of a build that does not know the frame plus one counter.
/// </summary>
public interface IPointerStreamFrameSink
{
    /// <summary>True when the batch was handed on; false when dropped (no companion, unknown session, oversize). Must not throw.</summary>
    ValueTask<bool> ForwardAsync(PointerStreamMessage frame, CancellationToken cancellationToken);
}
