using System.Runtime.Versioning;
using System.Text.Json.Nodes;
using PagentOS.Companion.Audio;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Listening;
using PagentOS.Companion.Audio.Listening.Spotting;
using PagentOS.Companion.Audio.Sideband;
using PagentOS.Companion.Audio.Wasapi;

namespace PagentOS.SessionCompanion.Voice;

/// <summary>
/// <c>PagentOS.SessionCompanion.exe --voice-enroll [phrase,phrase...] [--takes N]</c>: the ONLY
/// place the owner's recordings are taken for the offline engine, and only because the owner
/// ran it. Each take is a bounded window; only its MFCC frames are kept (DPAPI-protected in
/// <c>&lt;DataDir&gt;\voice\keywords.bin</c>); the audio is zeroed as soon as the features exist.
/// The running companion notices the new file and reloads it. The last stdout line is a JSON
/// summary that <c>scripts/core/qualify-device-voice.ps1</c> reads.
/// </summary>
[SupportedOSPlatform("windows")]
public static class VoiceEnrollmentCommand
{
    public static readonly IReadOnlyDictionary<string, string> Prompts = new Dictionary<string, string>(StringComparer.Ordinal)
    {
        [DeviceVoiceContract.WakeWordPhraseId] = "uyandırma sözcüğünüzü (örnek: \"Asistan\")",
        [DeviceVoiceContract.CommandAlarmSnooze] = "\"ertele\"",
        [DeviceVoiceContract.CommandAlarmStop] = "\"alarmı kapat\"",
        [DeviceVoiceContract.CommandListeningOff] = "\"dinlemeyi kapat\"",
        [DeviceVoiceContract.CommandTimeTell] = "\"saat kaç\"",
    };

    public static async Task<int> RunAsync(string dataDir, string? phrases, int takes, string? preferredCapture, CancellationToken cancellationToken)
    {
        Console.OutputEncoding = System.Text.Encoding.UTF8;
        var ids = string.IsNullOrWhiteSpace(phrases)
            ? Prompts.Keys.ToList()
            : phrases.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).ToList();
        var unknown = ids.Where(id => !KeywordTemplateSet.IsValidPhraseId(id)).ToList();
        if (unknown.Count > 0)
        {
            Console.Error.WriteLine($"bilinmeyen ifade: {string.Join(", ", unknown)} (geçerli: {string.Join(", ", Prompts.Keys)})");
            return 2;
        }

        takes = Math.Clamp(takes, KeywordTemplateSet.MinTemplatesPerPhrase, KeywordTemplateSet.MaxTemplatesPerPhrase);
        var format = AudioFormat.Pcm16Mono24k;
        var store = new FileKeywordTemplateStore(
            FileKeywordTemplateStore.DefaultPath(dataDir),
            DpapiSecretStore.ProtectBytes,
            DpapiSecretStore.UnprotectBytes);
        var set = store.Load() is { } existing && existing.SampleRate == format.SampleRate
            ? existing
            : new KeywordTemplateSet(format.SampleRate);

        using var catalog = new WasapiDeviceCatalog();
        var selector = new AudioDeviceSelector(AudioDirection.Capture, preferredCapture);
        var device = selector.Choose(catalog.List(AudioDirection.Capture));
        if (device is null)
        {
            Console.Error.WriteLine("mikrofon bulunamadı");
            return 3;
        }

        var factory = new WasapiDeviceFactory(catalog);
        var summary = new JsonObject();
        foreach (var id in ids)
        {
            set.Remove(id);
            var accepted = 0;
            var attempts = 0;
            while (accepted < takes && attempts < takes * 3)
            {
                attempts++;
                Console.WriteLine();
                Console.WriteLine($"[{id}] {accepted + 1}/{takes}: Enter'a basın, sonra {Prompts[id]} deyin (3 saniye).");
                Console.ReadLine();
                using var capture = factory.OpenCapture(device.DeviceId, format);
                var recording = await EnrollmentRecorder.RecordAsync(capture, EnrollmentRecorder.Window, cancellationToken).ConfigureAwait(false);
                var result = KeywordEnrollment.FromRecording(recording, format);
                if (!result.Accepted)
                {
                    Console.WriteLine($"  kabul edilmedi: {result.Detail} ({result.SpokenMs:F0} ms) - tekrar deneyin");
                    continue;
                }

                set.Add(id, result.Template!);
                accepted++;
                Console.WriteLine($"  alındı ({result.SpokenMs:F0} ms konuşma)");
            }

            summary[id] = new JsonObject { ["takes"] = accepted, ["attempts"] = attempts };
        }

        store.Save(set);
        foreach (var (id, threshold) in KeywordEnrollment.Thresholds(set))
        {
            if (summary[id] is JsonObject row)
            {
                row["threshold"] = threshold is null ? null : Math.Round(threshold.Value, 3);
                row["available"] = threshold is not null;
            }
        }

        var spotter = VoiceCompanionHost.BuildSpotter(store.Load(), format);
        Console.WriteLine();
        Console.WriteLine(new JsonObject
        {
            ["enrolled"] = summary,
            ["available"] = new JsonArray(spotter.AvailablePhrases.Order(StringComparer.Ordinal).Select(p => (JsonNode)JsonValue.Create(p)).ToArray()),
            ["reason"] = spotter.UnavailableReason,
            ["store"] = store.Path,
        }.ToJsonString());
        return 0;
    }
}
