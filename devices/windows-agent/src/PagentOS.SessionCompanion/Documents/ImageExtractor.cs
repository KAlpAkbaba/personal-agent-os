using System.Text.Json.Nodes;
using System.Windows.Media.Imaging;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// B32 requirements 139-141/496: the <c>image</c> kind. <see cref="Inspect"/> reads the
/// picture's own headers — pixel size, format, DPI and the metadata the file carries
/// (date taken, camera, application, title) — through WPF's <see cref="BitmapDecoder"/>,
/// which the companion already references; never the pixels. <see cref="Extract"/> is
/// OCR through <see cref="OcrHost"/>: one block per recognised line, <c>o&lt;n&gt;</c>, in
/// reading order, the whole text under the same character budget every extractor honours.
/// </summary>
public sealed class ImageExtractor : IDocumentExtractor
{
    public const string BlockKind = "ocr_line";

    private readonly OcrHost _ocr;

    public ImageExtractor(OcrHost? ocr = null)
    {
        _ocr = ocr ?? new OcrHost();
    }

    public bool Supports(string kind) => kind == FileKinds.Image;

    public JsonObject Inspect(string path, string kind, CancellationToken cancellationToken)
    {
        var result = new JsonObject();
        var image = ReadHeaders(path);
        result["image"] = image;
        return result;
    }

    public ExtractResult Extract(string path, string kind, ExtractRequest request, CancellationToken cancellationToken)
    {
        var headers = ReadHeaders(path);
        var recognised = _ocr.Recognize(path, request.Language, cancellationToken);
        var collector = new BlockCollector(request.MaxChars);
        var lines = recognised["lines"] as JsonArray ?? [];
        var n = 0;
        foreach (var node in lines)
        {
            if (node is not JsonObject line)
            {
                continue;
            }

            var text = TextFileReader.Normalise(line["text"]?.GetValue<string>() ?? string.Empty);
            if (text.Length == 0)
            {
                continue;
            }

            n++;
            var extra = new JsonObject { ["words"] = (line["words"] as JsonArray)?.Count ?? 0 };
            if (!collector.Add(new DocumentBlock($"o{n}", BlockKind, text, extra)))
            {
                break;
            }
        }

        var structure = new JsonObject
        {
            ["width"] = headers["width"]?.DeepClone(),
            ["height"] = headers["height"]?.DeepClone(),
            ["format"] = headers["format"]?.DeepClone(),
            ["lines"] = n,
            ["language"] = recognised["language"]?.DeepClone(),
            ["engine"] = OcrHost.EngineName,
            ["text_angle"] = recognised["text_angle"]?.DeepClone(),
        };
        return new ExtractResult(null, collector.Blocks, structure, collector.Truncated);
    }

    /// <summary>Pixel size, format, DPI and the file's own metadata (no pixels decoded).</summary>
    public static JsonObject ReadHeaders(string path)
    {
        try
        {
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            var decoder = BitmapDecoder.Create(stream, BitmapCreateOptions.DelayCreation | BitmapCreateOptions.IgnoreColorProfile, BitmapCacheOption.None);
            var frame = decoder.Frames[0];
            var result = new JsonObject
            {
                ["width"] = frame.PixelWidth,
                ["height"] = frame.PixelHeight,
                ["format"] = decoder.CodecInfo?.FriendlyName ?? Path.GetExtension(path).TrimStart('.').ToUpperInvariant(),
                ["dpi_x"] = Math.Round(frame.DpiX, 1),
                ["dpi_y"] = Math.Round(frame.DpiY, 1),
                ["frames"] = decoder.Frames.Count,
            };
            var metadata = new JsonObject();
            if (frame.Metadata is BitmapMetadata bitmapMetadata)
            {
                TryAdd(metadata, "date_taken", () => bitmapMetadata.DateTaken);
                TryAdd(metadata, "camera_manufacturer", () => bitmapMetadata.CameraManufacturer);
                TryAdd(metadata, "camera_model", () => bitmapMetadata.CameraModel);
                TryAdd(metadata, "application", () => bitmapMetadata.ApplicationName);
                TryAdd(metadata, "title", () => bitmapMetadata.Title);
                TryAdd(metadata, "author", () => bitmapMetadata.Author is null ? null : string.Join(", ", bitmapMetadata.Author));
            }

            result["metadata"] = metadata;
            return result;
        }
        catch (Exception ex) when (ex is NotSupportedException or FileFormatException or InvalidOperationException or ArgumentException)
        {
            throw DocumentErrors.Unsupported($"'{Path.GetFileName(path)}' is not an image this device can decode: {ex.GetType().Name}", DocumentErrors.ParseFailed);
        }
    }

    private static void TryAdd(JsonObject target, string key, Func<string?> read)
    {
        try
        {
            var value = read();
            if (!string.IsNullOrWhiteSpace(value))
            {
                target[key] = value;
            }
        }
        catch (Exception)
        {
            // A metadata query the codec does not support is simply absent.
        }
    }
}
