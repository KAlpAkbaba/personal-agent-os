using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;

namespace PagentOS.Companion.Audio.Listening.Spotting;

/// <summary>
/// One enrolled recording, reduced to its normalised MFCC frames. There is no audio in it and
/// no way back to audio worth the name; what is kept is the minimum a matcher needs.
/// </summary>
public sealed record KeywordTemplate(float[][] Frames);

/// <summary>Every phrase the owner enrolled, at the sample rate the features were computed for.</summary>
public sealed class KeywordTemplateSet
{
    public const int MaxTemplatesPerPhrase = 5;

    /// <summary>Fewer than this and a phrase has no measurable threshold, so it is not offered.</summary>
    public const int MinTemplatesPerPhrase = 2;

    private readonly Dictionary<string, List<KeywordTemplate>> _phrases = new(StringComparer.Ordinal);

    public KeywordTemplateSet(int sampleRate)
    {
        SampleRate = sampleRate;
    }

    public int SampleRate { get; }

    public IReadOnlyCollection<string> PhraseIds => _phrases.Keys;

    public IReadOnlyList<KeywordTemplate> For(string phraseId)
        => _phrases.TryGetValue(phraseId, out var list) ? list : [];

    /// <summary>Adds a template; the oldest is dropped past <see cref="MaxTemplatesPerPhrase"/>.</summary>
    public void Add(string phraseId, KeywordTemplate template)
    {
        if (!IsValidPhraseId(phraseId))
        {
            throw new ArgumentException($"'{phraseId}' is not a phrase id the device contract names", nameof(phraseId));
        }

        if (!_phrases.TryGetValue(phraseId, out var list))
        {
            list = [];
            _phrases[phraseId] = list;
        }

        list.Add(template);
        while (list.Count > MaxTemplatesPerPhrase)
        {
            list.RemoveAt(0);
        }
    }

    public bool Remove(string phraseId) => _phrases.Remove(phraseId);

    /// <summary>The wake word and the contract's offline commands; nothing else can be enrolled.</summary>
    public static bool IsValidPhraseId(string phraseId)
        => string.Equals(phraseId, DeviceVoiceContract.WakeWordPhraseId, StringComparison.Ordinal)
           || DeviceVoiceContract.RuleFor(phraseId) is not null;

    public byte[] Serialize()
    {
        var phrases = new JsonObject();
        foreach (var (id, templates) in _phrases)
        {
            var array = new JsonArray();
            foreach (var template in templates)
            {
                var frames = new JsonArray();
                foreach (var frame in template.Frames)
                {
                    frames.Add(new JsonArray(frame.Select(v => (JsonNode)JsonValue.Create(MathF.Round(v, 4))).ToArray()));
                }

                array.Add(new JsonObject { ["frames"] = frames });
            }

            phrases[id] = array;
        }

        var root = new JsonObject
        {
            ["version"] = 1,
            ["engine"] = DeviceVoiceContract.OfflineEngine,
            ["sample_rate"] = SampleRate,
            ["phrases"] = phrases,
        };
        return Encoding.UTF8.GetBytes(root.ToJsonString());
    }

    public static KeywordTemplateSet Deserialize(byte[] utf8)
    {
        var root = JsonNode.Parse(utf8) as JsonObject ?? throw new FormatException("templates are not a JSON object");
        if (root["engine"]?.GetValue<string>() != DeviceVoiceContract.OfflineEngine)
        {
            throw new FormatException("templates were written by a different engine");
        }

        var set = new KeywordTemplateSet(root["sample_rate"]?.GetValue<int>() ?? throw new FormatException("no sample_rate"));
        if (root["phrases"] is JsonObject phrases)
        {
            foreach (var (id, node) in phrases)
            {
                if (node is not JsonArray templates || !IsValidPhraseId(id))
                {
                    continue;
                }

                foreach (var template in templates)
                {
                    var frames = (template?["frames"] as JsonArray)?
                        .Select(f => (f as JsonArray)!.Select(v => v!.GetValue<float>()).ToArray())
                        .ToArray();
                    if (frames is { Length: > 0 } && frames.All(f => f.Length == MfccExtractor.Coefficients))
                    {
                        set.Add(id, new KeywordTemplate(frames));
                    }
                }
            }
        }

        return set;
    }
}

public interface IKeywordTemplateStore
{
    /// <summary>The stored set, or null when nothing was ever enrolled (or it cannot be read).</summary>
    KeywordTemplateSet? Load();

    void Save(KeywordTemplateSet set);
}

public sealed class InMemoryKeywordTemplateStore(KeywordTemplateSet? set = null) : IKeywordTemplateStore
{
    public KeywordTemplateSet? Set { get; private set; } = set;

    public KeywordTemplateSet? Load() => Set;

    public void Save(KeywordTemplateSet set) => Set = set;
}

/// <summary>
/// <c>&lt;DataDir&gt;\voice\keywords.bin</c>, protected with DPAPI for the owner's account
/// (<see cref="Sideband.DpapiSecretStore.ProtectBytes"/>): the templates are derived from the
/// owner's voice, so they are treated like the owner's other secrets even though they are not
/// audio. The transform is injectable so the file format is testable without DPAPI.
/// </summary>
public sealed class FileKeywordTemplateStore(
    string path,
    Func<byte[], byte[]> protect,
    Func<byte[], byte[]> unprotect,
    ILogger? logger = null) : IKeywordTemplateStore
{
    public string Path { get; } = path;

    public static string DefaultPath(string dataDir) => System.IO.Path.Combine(dataDir, "voice", "keywords.bin");

    public KeywordTemplateSet? Load()
    {
        if (!File.Exists(Path))
        {
            return null;
        }

        try
        {
            var plain = unprotect(File.ReadAllBytes(Path));
            try
            {
                return KeywordTemplateSet.Deserialize(plain);
            }
            finally
            {
                Array.Clear(plain);
            }
        }
        catch (Exception ex) when (ex is not OutOfMemoryException)
        {
            logger?.LogWarning("voice: keyword templates at {Path} cannot be read ({Reason}); the offline engine is unavailable until they are enrolled again", Path, ex.GetType().Name);
            return null;
        }
    }

    public void Save(KeywordTemplateSet set)
    {
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path)!);
        var plain = set.Serialize();
        try
        {
            var temp = Path + ".tmp";
            File.WriteAllBytes(temp, protect(plain));
            File.Move(temp, Path, overwrite: true);
        }
        finally
        {
            Array.Clear(plain);
        }
    }
}
