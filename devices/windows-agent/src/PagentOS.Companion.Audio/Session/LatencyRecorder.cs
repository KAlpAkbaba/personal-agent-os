using System.Text.Json.Nodes;
using PagentOS.Companion.Audio.Timing;

namespace PagentOS.Companion.Audio.Session;

/// <summary>Monotonic timestamps for one owner turn; null until the moment happens.</summary>
public sealed class TurnTimings(int index, long startedAt)
{
    public int Index { get; } = index;

    public long StartedAt { get; } = startedAt;

    public long? FirstUplinkAt { get; set; }

    public long? SpeechEndedAt { get; set; }

    public long? FirstAudioAt { get; set; }

    public long? BargeInAt { get; set; }

    public long? PlaybackStoppedAt { get; set; }

    public long? ToolCallAt { get; set; }

    public long? ToolPreambleAudioAt { get; set; }

    public long? ToolDoneAt { get; set; }

    public long? ResumedSpeechAt { get; set; }

    public int HesitationExtensionMs { get; set; }

    public string HesitationReason { get; set; } = "none";
}

public sealed record TurnMetrics(
    int Turn,
    double? MicToUplinkMs,
    double? EotToFirstAudioMs,
    double? BargeInToStopMs,
    double? ToolPreambleMs,
    double? ToolDoneToSpeechMs,
    int HesitationExtensionMs,
    string HesitationReason)
{
    public JsonObject ToJson()
    {
        var json = new JsonObject { ["turn"] = Turn };
        Add(json, "mic_to_uplink_ms", MicToUplinkMs);
        Add(json, "eot_to_first_audio_ms", EotToFirstAudioMs);
        Add(json, "barge_in_to_stop_ms", BargeInToStopMs);
        Add(json, "tool_preamble_ms", ToolPreambleMs);
        Add(json, "tool_done_to_speech_ms", ToolDoneToSpeechMs);
        json["hesitation_extension_ms"] = HesitationExtensionMs;
        json["hesitation_reason"] = HesitationReason;
        return json;
    }

    private static void Add(JsonObject json, string name, double? value)
    {
        if (value is not null)
        {
            json[name] = Math.Round(value.Value, 2);
        }
    }
}

/// <summary>
/// The five M12 §8 metrics, computed from timestamps the orchestrator stamps as things
/// happen. Percentiles are nearest-rank, which is what the Python bench uses, so the two
/// halves report comparable numbers.
/// </summary>
public sealed class LatencyRecorder(TimeProvider time)
{
    private readonly object _sync = new();
    private readonly List<TurnTimings> _turns = new();

    public TurnTimings? Current
    {
        get
        {
            lock (_sync)
            {
                return _turns.Count == 0 ? null : _turns[^1];
            }
        }
    }

    public TurnTimings BeginTurn() => BeginTurn(time.GetTimestamp());

    /// <summary><paramref name="startedAt"/> is the capture timestamp of the onset frame, so mic→uplink starts at the microphone, not at the loop.</summary>
    public TurnTimings BeginTurn(long startedAt)
    {
        lock (_sync)
        {
            var turn = new TurnTimings(_turns.Count + 1, startedAt);
            _turns.Add(turn);
            return turn;
        }
    }

    public IReadOnlyList<TurnMetrics> Metrics()
    {
        lock (_sync)
        {
            return _turns.Select(Compute).ToList();
        }
    }

    public JsonObject Summary()
    {
        var metrics = Metrics();
        var json = new JsonObject { ["turns"] = metrics.Count };
        json["mic_to_uplink_ms"] = Percentiles(metrics.Select(m => m.MicToUplinkMs));
        json["eot_to_first_audio_ms"] = Percentiles(metrics.Select(m => m.EotToFirstAudioMs));
        json["barge_in_to_stop_ms"] = Percentiles(metrics.Select(m => m.BargeInToStopMs));
        json["tool_preamble_ms"] = Percentiles(metrics.Select(m => m.ToolPreambleMs));
        json["tool_done_to_speech_ms"] = Percentiles(metrics.Select(m => m.ToolDoneToSpeechMs));
        json["per_turn"] = new JsonArray(metrics.Select(m => (JsonNode)m.ToJson()).ToArray());
        return json;
    }

    public static double? Percentile(IReadOnlyList<double> sorted, double p)
    {
        if (sorted.Count == 0)
        {
            return null;
        }

        var rank = (int)Math.Ceiling(p / 100.0 * sorted.Count);
        return sorted[Math.Clamp(rank - 1, 0, sorted.Count - 1)];
    }

    private static JsonNode? Percentiles(IEnumerable<double?> values)
    {
        var sorted = values.Where(v => v is not null).Select(v => v!.Value).OrderBy(v => v).ToList();
        if (sorted.Count == 0)
        {
            return null;
        }

        return new JsonObject
        {
            ["n"] = sorted.Count,
            ["p50"] = Math.Round(Percentile(sorted, 50)!.Value, 2),
            ["p95"] = Math.Round(Percentile(sorted, 95)!.Value, 2),
            ["max"] = Math.Round(sorted[^1], 2),
        };
    }

    private TurnMetrics Compute(TurnTimings t) => new(
        t.Index,
        Delta(t.StartedAt, t.FirstUplinkAt),
        Delta(t.SpeechEndedAt, t.FirstAudioAt),
        Delta(t.BargeInAt, t.PlaybackStoppedAt),
        Delta(t.ToolCallAt, t.ToolPreambleAudioAt),
        Delta(t.ToolDoneAt, t.ResumedSpeechAt),
        t.HesitationExtensionMs,
        t.HesitationReason);

    private double? Delta(long? from, long? to)
        => from is null || to is null ? null : time.ElapsedMs(from.Value, to.Value);
}
