using System.Text.Json;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Testing;
using PagentOS.Companion.Audio.Turn;
using PagentOS.Companion.Audio.Wasapi;

namespace PagentOS.Companion.Audio.Bench;

/// <summary>
/// Offline latency harness for the desktop audio client (M12 spec §8, "numbers for the
/// gate"). No microphone, no network: fake devices, a scripted provider, an in-process
/// Cloud Core, and the real client code between them.
///
///   PagentOS.Companion.Audio.Bench [--turns N] [--eot client|server] [--stop-ms X] [--json]
///   PagentOS.Companion.Audio.Bench --list-devices     (real WASAPI enumeration only; opens no stream)
/// </summary>
public static class Program
{
    public static async Task<int> Main(string[] args)
    {
        var turns = 4;
        var eot = EndOfTurnMode.Client;
        var stopMs = 0.0;
        var json = false;
        var listDevices = false;
        for (var i = 0; i < args.Length; i++)
        {
            switch (args[i])
            {
                case "--turns" when i + 1 < args.Length:
                    turns = int.Parse(args[++i], System.Globalization.CultureInfo.InvariantCulture);
                    break;
                case "--eot" when i + 1 < args.Length:
                    eot = string.Equals(args[++i], "server", StringComparison.OrdinalIgnoreCase) ? EndOfTurnMode.Server : EndOfTurnMode.Client;
                    break;
                case "--stop-ms" when i + 1 < args.Length:
                    stopMs = double.Parse(args[++i], System.Globalization.CultureInfo.InvariantCulture);
                    break;
                case "--json":
                    json = true;
                    break;
                case "--list-devices":
                    listDevices = true;
                    break;
                case "--help":
                case "-h":
                    Console.WriteLine("usage: PagentOS.Companion.Audio.Bench [--turns N] [--eot client|server] [--stop-ms X] [--json] [--list-devices]");
                    return 0;
            }
        }

        if (listDevices)
        {
            return ListDevices();
        }

        var report = await new OfflineVoiceBench(new BenchOptions(Turns: turns, EndOfTurn: eot, SimulatedPlaybackStopMs: stopMs)).RunAsync().ConfigureAwait(false);
        if (json)
        {
            Console.WriteLine(report.ToJsonString(new JsonSerializerOptions { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping }));
            return report["defects"]!.AsArray().Count == 0 ? 0 : 2;
        }

        Console.WriteLine($"offline voice bench: {report["turns"]} turns, end_of_turn={report["end_of_turn"]}, wall {report["wall_ms"]} ms");
        Console.WriteLine($"  {report["disclaimer"]}");
        foreach (var metric in new[] { "mic_to_uplink_ms", "eot_to_first_audio_ms", "barge_in_to_stop_ms", "tool_preamble_ms", "tool_done_to_speech_ms" })
        {
            var node = report[metric];
            Console.WriteLine(node is null
                ? $"  {metric,-24} (no samples)"
                : $"  {metric,-24} n={node["n"]} p50={node["p50"]} p95={node["p95"]} max={node["max"]}");
        }

        Console.WriteLine($"  barge_ins={report["barge_ins"]} tool_call_requests={report["tool_call_requests"]} tool_executions={report["tool_executions"]}");
        Console.WriteLine($"  processing: aec={report["processing"]!["echo_cancellation"]} ns={report["processing"]!["noise_suppression"]}");
        foreach (var line in report["scenario_log"]!.AsArray())
        {
            Console.WriteLine("  - " + line);
        }

        var defects = report["defects"]!.AsArray();
        Console.WriteLine(defects.Count == 0 ? "  defects: none" : "  DEFECTS: " + string.Join(", ", defects.Select(d => d!.ToString())));
        return defects.Count == 0 ? 0 : 2;
    }

    private static int ListDevices()
    {
        if (!OperatingSystem.IsWindows())
        {
            Console.Error.WriteLine("device listing needs Windows");
            return 1;
        }

        using var catalog = new WasapiDeviceCatalog();
        foreach (var direction in new[] { AudioDirection.Capture, AudioDirection.Render })
        {
            Console.WriteLine(direction + ":");
            foreach (var device in catalog.List(direction))
            {
                var flags = (device.IsDefault ? " default" : string.Empty) + (device.IsDefaultCommunications ? " communications" : string.Empty);
                Console.WriteLine($"  [{device.FormFactor}]{flags} {device.Name}  ({device.Id})");
            }
        }

        return 0;
    }
}
