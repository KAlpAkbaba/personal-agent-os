using System.Collections.Concurrent;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.Versioning;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Operator;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// The documents family's dispatch (M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §2, ADR-0083):
/// one method per name in <see cref="DocumentCapabilityNames"/>. Four rules hold for every
/// method, in this order, before any file is opened:
/// <list type="number">
/// <item>the payload is validated — required fields, types, bounds;</item>
/// <item>every path argument (search roots included) is RESOLVED then CONTAINED by
/// <see cref="AuthorisedRoots"/>; outside, or unresolvable, is <c>permission_denied</c> and the
/// path is never echoed; a <c>file_id</c> is looked up in this process's memo of ids it issued
/// (search, locate, inspect, read, extract, compare fill it) and an unknown one is <c>not_found</c>;</item>
/// <item>a secret-bearing name (<see cref="SecretNames"/>) is <c>permission_denied</c> / <c>secret_bearing_name</c>;</item>
/// <item>the size is read from the directory entry and a file over
/// <see cref="DocumentCapabilityNames.MaxFileBytes"/> is <c>unsupported_format</c> / <c>too_large</c> — unopened.</item>
/// </list>
/// Every bound answers <c>truncated: true</c>, never a silent cut; a format that cannot be
/// parsed answers <c>unsupported_format</c> with the reason, never an empty success; every
/// result passes the browser family's forbidden-key scan before it leaves the companion.
/// Read-only by construction: nothing here writes, moves or deletes.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class DocumentCapabilities
{
    public const string AuditRequestEvent = "document_request";

    public const int MaxPathChars = 1024;
    public const int MaxPatternChars = 256;
    public const int MaxSearchRoots = 16;
    public const int MaxExtensions = 32;

    private readonly OperatorOptions _options;
    private readonly ILogger _logger;
    private readonly AuditLog? _audit;
    private readonly IReadOnlyList<IDocumentExtractor> _extractors;
    private readonly ConcurrentDictionary<string, string> _known = new(StringComparer.Ordinal);

    public DocumentCapabilities(OperatorOptions options, ILogger logger, AuditLog? audit = null, IReadOnlyList<IDocumentExtractor>? extractors = null)
    {
        _options = options;
        _logger = logger;
        _audit = audit;
        _extractors = extractors ?? DefaultExtractors();
        Roots = new AuthorisedRoots(options.AuthorisedRoots);
    }

    public static IReadOnlyList<IDocumentExtractor> DefaultExtractors() => [new OpenXmlExtractor(), new PdfPigExtractor(), new TextLikeExtractor()];

    /// <summary>The same gate as the operator (<c>PAGENTOS_AGENT_OperatorEnabled</c>): the companion may touch the owner's files.</summary>
    public bool Enabled => _options.Enabled;

    public AuthorisedRoots Roots { get; }

    public IReadOnlyList<string> AuthorisedRootsConfigured => _options.AuthorisedRoots;

    /// <summary>How many <c>file_id</c>s this process has issued and can resolve again.</summary>
    public int KnownFiles => _known.Count;

    // ================================================================== entry point

    public async Task<JsonObject> ExecuteAsync(string capability, JsonObject payload, TimeSpan budget, CancellationToken cancellationToken)
    {
        if (!DocumentCapabilityNames.IsMember(capability))
        {
            throw new CapabilityException(ErrorClasses.CapabilityMissing, $"'{capability}' is not a documents capability", retryable: false);
        }

        var stopwatch = Stopwatch.StartNew();
        using var budgetCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        if (budget > TimeSpan.Zero)
        {
            budgetCts.CancelAfter(budget);
        }

        try
        {
            var result = await Task.Run(() => Dispatch(capability, payload, budget, budgetCts.Token), CancellationToken.None).ConfigureAwait(false);
            var forbidden = BrowserWorkerHost.FindForbiddenKey(result, path: "result");
            if (forbidden is not null)
            {
                throw new CapabilityException(
                    ErrorClasses.SecurityScopeError,
                    $"documents result for {capability} carries a forbidden key ({forbidden}); it does not leave the companion",
                    retryable: false);
            }

            Record(capability, "ok", stopwatch.ElapsedMilliseconds, null);
            return result;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            Record(capability, ErrorClasses.Cancelled, stopwatch.ElapsedMilliseconds, true);
            throw new CapabilityException(ErrorClasses.Cancelled, $"{capability} was cancelled after {stopwatch.ElapsedMilliseconds} ms", retryable: true);
        }
        catch (OperationCanceledException)
        {
            Record(capability, ErrorClasses.Timeout, stopwatch.ElapsedMilliseconds, true);
            throw new CapabilityException(ErrorClasses.Timeout, $"{capability} exceeded its {budget.TotalSeconds:F1} s budget", retryable: true);
        }
        catch (CapabilityException ex)
        {
            Record(capability, ex.ErrorClass, stopwatch.ElapsedMilliseconds, ex.Retryable);
            throw;
        }
        catch (UnauthorizedAccessException ex)
        {
            Record(capability, ErrorClasses.PermissionDenied, stopwatch.ElapsedMilliseconds, false);
            throw new CapabilityException(ErrorClasses.PermissionDenied, $"the file system refused the read: {ex.Message}", retryable: false);
        }
        catch (IOException ex)
        {
            Record(capability, ErrorClasses.DependencyUnavailable, stopwatch.ElapsedMilliseconds, true);
            throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"the file could not be read: {ex.Message}", retryable: true);
        }
    }

    private JsonObject Dispatch(string capability, JsonObject payload, TimeSpan budget, CancellationToken cancellationToken)
        => capability switch
        {
            DocumentCapabilityNames.FileSearch => Search(payload, budget, cancellationToken),
            DocumentCapabilityNames.FileLocate => Locate(payload),
            DocumentCapabilityNames.FileInspect => Inspect(payload, cancellationToken),
            DocumentCapabilityNames.FileRead => Read(payload),
            DocumentCapabilityNames.FileCompare => Compare(payload, cancellationToken),
            DocumentCapabilityNames.DocumentExtract => Extract(payload, cancellationToken),
            _ => throw new CapabilityException(ErrorClasses.CapabilityMissing, $"'{capability}' has no dispatch entry", retryable: false),
        };

    // ================================================================== file.search

    private JsonObject Search(JsonObject payload, TimeSpan budget, CancellationToken cancellationToken)
    {
        var pattern = RequireString(payload, "pattern", MaxPatternChars);
        var max = OptionalInt(payload, "max", 1, DocumentCapabilityNames.MaxSearchResults) ?? DocumentCapabilityNames.MaxSearchResults;
        var extensions = OptionalStrings(payload, "extensions", MaxExtensions, 32)?.Select(NormaliseExtension).ToList();
        var modifiedAfter = OptionalTimestamp(payload, "modified_after");

        var roots = new List<string>();
        var requested = OptionalStrings(payload, "roots", MaxSearchRoots, MaxPathChars);
        if (requested is null || requested.Count == 0)
        {
            roots.AddRange(Roots.Resolved);
        }
        else
        {
            foreach (var raw in requested)
            {
                if (!Path.IsPathRooted(raw))
                {
                    throw DocumentErrors.Invalid("payload.roots must be absolute paths");
                }

                var resolved = Roots.Confine(raw)
                               ?? throw DocumentErrors.Denied(RootsRefusal("a search root"));
                if (!Directory.Exists(resolved))
                {
                    throw DocumentErrors.Invalid("payload.roots must name directories");
                }

                roots.Add(resolved);
            }
        }

        var timeout = budget > TimeSpan.Zero && budget < DocumentCapabilityNames.SearchTimeout ? budget : DocumentCapabilityNames.SearchTimeout;
        var outcome = FileSearch.Run(new SearchQuery(roots, pattern, extensions, max, modifiedAfter), timeout, cancellationToken);
        var files = new JsonArray();
        foreach (var record in outcome.Files)
        {
            _known[record.FileId] = record.Path;
            files.Add(record.ToJson());
        }

        var result = new JsonObject
        {
            ["files"] = files,
            ["truncated"] = outcome.Truncated,
            ["searched_roots"] = new JsonArray([.. outcome.SearchedRoots.Select(r => (JsonNode?)r)]),
            ["entries_walked"] = outcome.EntriesWalked,
        };
        if (outcome.StopReason is not null)
        {
            result["stop_reason"] = outcome.StopReason;
        }

        return result;
    }

    private static string NormaliseExtension(string raw)
    {
        var trimmed = raw.Trim().ToLowerInvariant();
        if (trimmed.Length == 0 || trimmed.Any(c => c is '\\' or '/' or '*' or '?' || char.IsControl(c)))
        {
            throw DocumentErrors.Invalid("payload.extensions must be plain extensions such as \".pdf\"");
        }

        return trimmed.StartsWith('.') ? trimmed : "." + trimmed;
    }

    // ================================================================== file.locate

    private JsonObject Locate(JsonObject payload)
    {
        var target = ResolveTarget(payload, "payload");
        return new JsonObject { ["file"] = target.Record.ToJson() };
    }

    // ================================================================== file.inspect

    private JsonObject Inspect(JsonObject payload, CancellationToken cancellationToken)
    {
        var target = ResolveTarget(payload, "payload");
        var result = new JsonObject
        {
            ["file"] = target.Record.ToJson(),
            ["kind"] = target.Kind,
            ["is_text"] = FileKinds.IsTextLike(target.Kind),
        };

        if (target.Record.Size > DocumentCapabilityNames.MaxFileBytes)
        {
            // Headers would mean opening it; the record and the kind are what the directory says.
            result["too_large"] = true;
            return result;
        }

        var extractor = ExtractorFor(target.Kind);
        if (extractor is not null)
        {
            foreach (var (key, value) in extractor.Inspect(target.Path, target.Kind, cancellationToken))
            {
                result[key] = value?.DeepClone();
            }
        }
        else
        {
            // Unknown kind: the content itself decides whether file.read may return it.
            var decoded = TextFileReader.Read(target.Path);
            result["is_text"] = decoded.IsText;
            if (decoded.IsText)
            {
                result["encoding"] = decoded.Encoding;
                result["lines"] = TextFileReader.SplitLines(decoded.Text).Count;
            }
        }

        return result;
    }

    // ================================================================== file.read

    private JsonObject Read(JsonObject payload)
    {
        var target = ResolveTarget(payload, "payload");
        var offset = OptionalInt(payload, "offset", 0, int.MaxValue) ?? 0;
        var length = OptionalInt(payload, "length", 1, DocumentCapabilityNames.MaxReadChars) ?? DocumentCapabilityNames.MaxReadChars;

        if (FileKinds.IsBinaryDocument(target.Kind))
        {
            throw DocumentErrors.Unsupported($"'{target.Record.Name}' is a {target.Kind} document, not text; document.extract reads it", DocumentErrors.NotText);
        }

        RequireReadable(target);
        var decoded = TextFileReader.Read(target.Path);
        if (!decoded.IsText)
        {
            throw DocumentErrors.Unsupported($"'{target.Record.Name}' does not decode as text ({decoded.Encoding})", DocumentErrors.NotText);
        }

        var total = decoded.Text.Length;
        var start = Math.Min(offset, total);
        var count = Math.Min(length, total - start);
        return new JsonObject
        {
            ["file"] = target.Record.ToJson(),
            ["text"] = decoded.Text.Substring(start, count),
            ["encoding"] = decoded.Encoding,
            ["truncated"] = start + count < total,
            ["total_chars"] = total,
            ["offset"] = start,
        };
    }

    // ================================================================== document.extract

    private JsonObject Extract(JsonObject payload, CancellationToken cancellationToken)
    {
        var target = ResolveTarget(payload, "payload");
        var request = ReadExtractRequest(payload);
        var extracted = ExtractDocument(target, request, cancellationToken);
        var blocks = new JsonArray();
        foreach (var block in extracted.Blocks)
        {
            var isTable = block.Kind == "table";
            if ((isTable && request.Tables) || (!isTable && request.Text))
            {
                blocks.Add(block.ToJson());
            }
        }

        return new JsonObject
        {
            ["file"] = target.Record.ToJson(),
            ["doc_id"] = target.DocId,
            ["kind"] = target.Kind,
            ["title"] = extracted.Title,
            ["blocks"] = blocks,
            ["structure"] = request.Structure ? extracted.Structure : new JsonObject(),
            ["truncated"] = extracted.Truncated,
        };
    }

    private ExtractResult ExtractDocument(Target target, ExtractRequest request, CancellationToken cancellationToken)
    {
        if (!FileKinds.IsExtractable(target.Kind))
        {
            throw DocumentErrors.Unsupported($"'{target.Record.Name}' has no extractor: extension '{target.Record.Extension}' is not a document kind the device knows", DocumentErrors.UnknownKind);
        }

        RequireReadable(target);
        var extractor = ExtractorFor(target.Kind)
                        ?? throw DocumentErrors.Unsupported($"no extractor is configured for kind '{target.Kind}'", DocumentErrors.UnknownKind);
        target.DocId ??= FileIdentity.DocId(target.Path);
        return extractor.Extract(target.Path, target.Kind, request, cancellationToken);
    }

    private static ExtractRequest ReadExtractRequest(JsonObject payload)
    {
        var text = true;
        var structure = true;
        var tables = true;
        var parts = OptionalStrings(payload, "parts", 3, 16);
        if (parts is not null)
        {
            text = false;
            structure = false;
            tables = false;
            foreach (var part in parts)
            {
                switch (part)
                {
                    case "text":
                        text = true;
                        break;
                    case "structure":
                        structure = true;
                        break;
                    case "tables":
                        tables = true;
                        break;
                    default:
                        throw DocumentErrors.Invalid("payload.parts may contain only \"text\", \"structure\" and \"tables\"");
                }
            }
        }

        return new ExtractRequest(
            text,
            structure,
            tables,
            OptionalRange(payload, "page_range", DocumentCapabilityNames.MaxPdfPages),
            OptionalString(payload, "sheet", 64),
            OptionalRange(payload, "slide_range", 10_000),
            OptionalInt(payload, "max_chars", 1, DocumentCapabilityNames.MaxExtractChars) ?? DocumentCapabilityNames.MaxExtractChars);
    }

    // ================================================================== file.compare

    private JsonObject Compare(JsonObject payload, CancellationToken cancellationToken)
    {
        var a = ResolveTarget(RequireObject(payload, "a"), "payload.a");
        var b = ResolveTarget(RequireObject(payload, "b"), "payload.b");
        RequireReadable(a);
        RequireReadable(b);
        a.DocId = FileIdentity.DocId(a.Path);
        b.DocId = FileIdentity.DocId(b.Path);

        var result = new JsonObject
        {
            ["a"] = a.Record.ToJson(),
            ["b"] = b.Record.ToJson(),
            ["same_content"] = string.Equals(a.DocId, b.DocId, StringComparison.Ordinal),
            ["size_delta"] = b.Record.Size - a.Record.Size,
            ["mtime_delta_s"] = Math.Round((b.Record.MtimeUtc - a.Record.MtimeUtc).TotalSeconds, 3),
            ["truncated"] = false,
        };

        if (!string.Equals(a.Kind, b.Kind, StringComparison.Ordinal))
        {
            result["kind"] = $"{a.Kind}/{b.Kind}";
            new RefDiff([], [], [], []).WriteTo(result);
            result["summary"] = $"different kinds ({a.Kind} and {b.Kind}): content identity only";
            return result;
        }

        result["kind"] = a.Kind;
        if (FileKinds.IsTextLike(a.Kind))
        {
            var aText = TextFileReader.Read(a.Path);
            var bText = TextFileReader.Read(b.Path);
            if (!aText.IsText || !bText.IsText)
            {
                throw DocumentErrors.Unsupported("one of the files does not decode as text", DocumentErrors.NotText);
            }

            DocumentCompare.Lines(aText.Text, bText.Text).WriteTo(result);
            return result;
        }

        if (!FileKinds.IsExtractable(a.Kind))
        {
            new RefDiff([], [], [], []).WriteTo(result);
            result["summary"] = "unknown kind: content identity only";
            return result;
        }

        var request = new ExtractRequest();
        var aBlocks = ExtractDocument(a, request, cancellationToken);
        var bBlocks = ExtractDocument(b, request, cancellationToken);
        DocumentCompare.Blocks(aBlocks.Blocks, bBlocks.Blocks).WriteTo(result);
        result["truncated"] = aBlocks.Truncated || bBlocks.Truncated;
        return result;
    }

    // ================================================================== targets and policy

    private sealed class Target(string path, FileRecord record)
    {
        public string Path { get; } = path;

        public FileRecord Record { get; } = record;

        public string Kind { get; } = record.Kind;

        public string? DocId { get; set; }
    }

    /// <summary>
    /// <c>path</c> or <c>file_id</c> (one of them) → a resolved, contained, non-secret file
    /// with its record. The checks run in the order the class docstring gives; the record
    /// (which hashes the content when it is small) is built last.
    /// </summary>
    private Target ResolveTarget(JsonObject obj, string where)
    {
        var path = OptionalString(obj, "path", MaxPathChars);
        var fileId = OptionalString(obj, "file_id", 64);
        if (path is null && fileId is null)
        {
            throw DocumentErrors.Invalid($"{where}.path or {where}.file_id is required");
        }

        string resolved;
        if (path is not null)
        {
            resolved = ConfineFile(path, where);
        }
        else
        {
            if (!fileId!.StartsWith(FileIdentity.FilePrefix, StringComparison.Ordinal))
            {
                throw DocumentErrors.Invalid($"{where}.file_id must start with \"{FileIdentity.FilePrefix}\"");
            }

            if (!_known.TryGetValue(fileId, out var known))
            {
                throw DocumentErrors.NotFound($"{fileId} is not a file this companion has issued an id for; file.search or file.locate it first");
            }

            if (!File.Exists(known))
            {
                _known.TryRemove(fileId, out _);
                throw DocumentErrors.NotFound($"{fileId} named a file that is no longer there");
            }

            resolved = Roots.Confine(known) ?? throw DocumentErrors.Denied(RootsRefusal($"{where}.file_id"));
        }

        var name = System.IO.Path.GetFileName(resolved);
        if (SecretNames.IsSecretBearing(name))
        {
            throw DocumentErrors.Denied($"'{name}' is a secret-bearing name and is never read", SecretNames.Detail);
        }

        var record = FileRecord.From(resolved, hashContent: true);
        _known[record.FileId] = resolved;
        return new Target(resolved, record);
    }

    /// <summary>
    /// The resolved path when <paramref name="raw"/> really opens inside a root; otherwise a
    /// typed refusal that never echoes the argument. A missing file whose PARENT resolves
    /// inside the roots is <c>not_found</c> (the parent, not the spelling, proves it is inside);
    /// everything else — outside, through a junction that leaves the roots, unresolvable — is
    /// <c>permission_denied</c>, so an answer never says whether an outside path exists.
    /// </summary>
    private string ConfineFile(string raw, string where)
    {
        if (!System.IO.Path.IsPathRooted(raw))
        {
            throw DocumentErrors.Invalid($"{where}.path must be absolute");
        }

        var resolved = Roots.Confine(raw);
        if (resolved is null)
        {
            string? parent = null;
            string? name = null;
            try
            {
                var full = System.IO.Path.GetFullPath(raw);
                parent = System.IO.Path.GetDirectoryName(full);
                name = System.IO.Path.GetFileName(full);
            }
            catch (Exception)
            {
                // Unresolvable spelling: refused below.
            }

            if (parent is not null && !string.IsNullOrEmpty(name) && Roots.Confine(parent) is { } parentResolved)
            {
                var candidate = System.IO.Path.Combine(parentResolved, name);
                if (!File.Exists(candidate) && !Directory.Exists(candidate))
                {
                    throw DocumentErrors.NotFound($"no file named '{name}' exists in that authorised folder");
                }
            }

            throw DocumentErrors.Denied(RootsRefusal($"{where}.path"));
        }

        if (Directory.Exists(resolved))
        {
            throw DocumentErrors.Invalid($"{where}.path names a directory, not a file");
        }

        return resolved;
    }

    private string RootsRefusal(string what)
        => $"{what} does not resolve to a path inside the owner's authorised roots ({string.Join(";", AuthorisedRootsConfigured)}); every junction and link is followed before the comparison, and a path that cannot be resolved is refused";

    /// <summary>The size bound, from the directory entry — before the file is opened.</summary>
    private static void RequireReadable(Target target)
    {
        if (target.Record.Size > DocumentCapabilityNames.MaxFileBytes)
        {
            throw DocumentErrors.Unsupported(
                $"'{target.Record.Name}' is {target.Record.Size} bytes, over the {DocumentCapabilityNames.MaxFileBytes / (1024 * 1024)} MiB bound; it was not opened",
                DocumentErrors.TooLarge);
        }
    }

    private IDocumentExtractor? ExtractorFor(string kind) => _extractors.FirstOrDefault(e => e.Supports(kind));

    // ================================================================== helpers: payload

    private static JsonObject RequireObject(JsonObject payload, string key)
        => payload[key] as JsonObject ?? throw DocumentErrors.Invalid($"payload.{key} is required and must be an object");

    private static string RequireString(JsonObject payload, string key, int maxChars)
    {
        var node = payload[key];
        if (node is null || node.GetValueKind() != JsonValueKind.String)
        {
            throw DocumentErrors.Invalid($"payload.{key} is required and must be a string");
        }

        var value = node.GetValue<string>();
        if (string.IsNullOrWhiteSpace(value))
        {
            throw DocumentErrors.Invalid($"payload.{key} must not be empty");
        }

        if (value.Length > maxChars)
        {
            throw DocumentErrors.Invalid($"payload.{key} exceeds {maxChars} characters ({value.Length})");
        }

        return value;
    }

    private static string? OptionalString(JsonObject payload, string key, int maxChars)
        => payload[key] is null ? null : RequireString(payload, key, maxChars);

    private static List<string>? OptionalStrings(JsonObject payload, string key, int maxItems, int maxChars)
    {
        var node = payload[key];
        if (node is null)
        {
            return null;
        }

        if (node is not JsonArray array)
        {
            throw DocumentErrors.Invalid($"payload.{key} must be an array of strings");
        }

        if (array.Count > maxItems)
        {
            throw DocumentErrors.Invalid($"payload.{key} may carry at most {maxItems} entries");
        }

        var values = new List<string>();
        foreach (var item in array)
        {
            if (item is null || item.GetValueKind() != JsonValueKind.String)
            {
                throw DocumentErrors.Invalid($"payload.{key} must contain strings");
            }

            var value = item.GetValue<string>();
            if (string.IsNullOrWhiteSpace(value) || value.Length > maxChars)
            {
                throw DocumentErrors.Invalid($"payload.{key} entries must be non-empty and at most {maxChars} characters");
            }

            values.Add(value);
        }

        return values;
    }

    private static int? OptionalInt(JsonObject payload, string key, int min, int max)
    {
        var node = payload[key];
        if (node is null)
        {
            return null;
        }

        if (node.GetValueKind() != JsonValueKind.Number || node is not JsonValue number)
        {
            throw DocumentErrors.Invalid($"payload.{key} must be an integer");
        }

        double raw;
        if (number.TryGetValue<int>(out var asInt))
        {
            raw = asInt;
        }
        else if (number.TryGetValue<long>(out var asLong))
        {
            raw = asLong;
        }
        else if (!number.TryGetValue<double>(out raw))
        {
            throw DocumentErrors.Invalid($"payload.{key} must be an integer");
        }

        if (Math.Floor(raw) != raw)
        {
            throw DocumentErrors.Invalid($"payload.{key} must be an integer");
        }

        if (raw < min || raw > max)
        {
            throw DocumentErrors.Invalid($"payload.{key}={raw.ToString(CultureInfo.InvariantCulture)} is outside [{min}, {max}]");
        }

        return (int)raw;
    }

    private static (int From, int To)? OptionalRange(JsonObject payload, string key, int maxSpan)
    {
        var node = payload[key];
        if (node is null)
        {
            return null;
        }

        if (node is not JsonArray array || array.Count != 2)
        {
            throw DocumentErrors.Invalid($"payload.{key} must be [from, to]");
        }

        var probe = new JsonObject { ["from"] = array[0]?.DeepClone(), ["to"] = array[1]?.DeepClone() };
        var from = OptionalInt(probe, "from", 1, int.MaxValue) ?? throw DocumentErrors.Invalid($"payload.{key} must be [from, to]");
        var to = OptionalInt(probe, "to", 1, int.MaxValue) ?? throw DocumentErrors.Invalid($"payload.{key} must be [from, to]");
        if (to < from)
        {
            throw DocumentErrors.Invalid($"payload.{key}: to must not be before from");
        }

        if (to - from + 1 > maxSpan)
        {
            throw DocumentErrors.Invalid($"payload.{key} spans more than {maxSpan}");
        }

        return (from, to);
    }

    private static DateTimeOffset? OptionalTimestamp(JsonObject payload, string key)
    {
        var raw = OptionalString(payload, key, 64);
        if (raw is null)
        {
            return null;
        }

        if (!DateTimeOffset.TryParse(raw, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var parsed))
        {
            throw DocumentErrors.Invalid($"payload.{key} must be an ISO-8601 timestamp");
        }

        return parsed;
    }

    private void Record(string capability, string outcome, long durationMs, bool? retryable)
    {
        var detail = $"outcome={outcome} duration_ms={durationMs}" + (retryable is null ? string.Empty : $" retryable={(retryable.Value ? "true" : "false")}");
        _audit?.Write(AuditRequestEvent, capability: capability, status: outcome, detail: detail);
        _logger.LogInformation("documents request {Capability} outcome={Outcome} duration_ms={DurationMs}", capability, outcome, durationMs);
    }
}
