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
/// <see cref="DocumentCapabilityNames.MaxFileBytes"/> is <c>unsupported_format</c> / <c>too_large</c> — unopened;</item>
/// <item>what the file would make the companion DO is bounded before the parser is entered
/// (<see cref="DocumentBounds"/>, ADR-0083 addendum 3): an OOXML package's central directory
/// is read by <see cref="ContainerGuard"/> and a decompression bomb is
/// <c>unsupported_format</c> / <c>decompression_bound</c> with the SDK never opened; a PDF's
/// streams inflate only through <see cref="BoundedFilterProvider"/>; a text-like file is read
/// as a bounded prefix through one handle whose length is the size that counts.</item>
/// </list>
/// The secret-name rule is applied to the NAME THE CALLER SPELLED before anything is looked
/// up, so a secret-bearing name answers the same whether or not the file exists.
/// Every bound answers <c>truncated: true</c>, never a silent cut; a format that cannot be
/// parsed answers <c>unsupported_format</c> with the reason, never an empty success; every
/// result passes the browser family's forbidden-key scan before it leaves the companion.
/// Read-only by construction: nothing here writes, moves or deletes — with one creator since
/// M22, <c>file.fetch</c> (<see cref="FileFetch"/>, DEVICE_PROTOCOL.md §6k), which brings a
/// Cloud Core render into the Downloads root as a NEW file from the origin the device dialled
/// (<see cref="FetchOrigin"/>, told by the Device Service in the pipe challenge), verified
/// before it is kept and again under its final name, marked as from the web, never over an
/// existing file, at most two at a time.
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
    private readonly OperatorCapabilities? _operator;
    private readonly FileFetch _fetch;
    private readonly ConcurrentDictionary<string, string> _known = new(StringComparer.Ordinal);
    private volatile string? _fetchOrigin;

    public DocumentCapabilities(
        OperatorOptions options,
        ILogger logger,
        AuditLog? audit = null,
        IReadOnlyList<IDocumentExtractor>? extractors = null,
        OperatorCapabilities? fileOpener = null,
        FileFetch? fetch = null)
    {
        _options = options;
        _logger = logger;
        _audit = audit;
        _extractors = extractors ?? DefaultExtractors();
        _operator = fileOpener;
        _fetch = fetch ?? new FileFetch();
        Roots = new AuthorisedRoots(options.AuthorisedRoots);
    }

    public static IReadOnlyList<IDocumentExtractor> DefaultExtractors() => [new OpenXmlExtractor(), new PdfPigExtractor(), new TextLikeExtractor(), new ImageExtractor(), new OpenPackageExtractor(), new RtfExtractor(), new LegacyOfficeExtractor()];

    /// <summary>The same gate as the operator (<c>PAGENTOS_AGENT_OperatorEnabled</c>): the companion may touch the owner's files.</summary>
    public bool Enabled => _options.Enabled;

    public AuthorisedRoots Roots { get; }

    public IReadOnlyList<string> AuthorisedRootsConfigured => _options.AuthorisedRoots;

    /// <summary>
    /// M22 (§6k): the ONE origin <c>file.fetch</c> may download from — scheme, host and port of
    /// the Cloud Core the Device Service dialled, as it says in every pipe challenge
    /// (<see cref="CompanionRuntime"/> sets it on connect; a lab sets it to its local origin).
    /// Null — never told — means every fetch is <c>permission_denied</c>: this companion does
    /// not guess an origin from its own configuration.
    /// </summary>
    public string? FetchOrigin
    {
        get => _fetchOrigin;
        set => _fetchOrigin = HttpOrigin.Of(value);
    }

    /// <summary>The directory <c>file.fetch</c> writes into (<c>DownloadsRoot</c>, else the owner's Downloads folder); it must resolve inside the roots at fetch time.</summary>
    public string? DownloadsRoot => _options.EffectiveDownloadsRoot;

    /// <summary>Whether <c>open: true</c> can be honoured — this companion was built with the operator's <c>file.open</c>.</summary>
    public bool CanOpen => _operator is not null;

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
            // file.fetch is asynchronous end to end (its network reads carry the token, ADR-0085
            // addendum 3) and is awaited directly; the read-only names run on a pool thread.
            var result = string.Equals(capability, DocumentCapabilityNames.FileFetch, StringComparison.Ordinal)
                ? await FetchAsync(payload, budget, budgetCts.Token).ConfigureAwait(false)
                : await Task.Run(() => Dispatch(capability, payload, budget, budgetCts.Token), CancellationToken.None).ConfigureAwait(false);
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
            DocumentCapabilityNames.FileTrash => Trash(payload),
            // B34 (153-165): the managed mutations. Every target is resolved through the
            // same confinement the reads use; the acts themselves live in FileMutations.
            DocumentCapabilityNames.FileWrite => WriteFile(payload),
            DocumentCapabilityNames.FileAppend => AppendFile(payload),
            DocumentCapabilityNames.FileRename => RenameFile(payload),
            DocumentCapabilityNames.FileMove => MoveFile(payload),
            DocumentCapabilityNames.FileCopy => CopyFile(payload),
            DocumentCapabilityNames.FileRestore => RestoreFile(payload),
            _ => throw new CapabilityException(ErrorClasses.CapabilityMissing, $"'{capability}' has no dispatch entry", retryable: false),
        };

    // ================================================================== B34: mutations

    private JsonObject WriteFile(JsonObject payload)
    {
        var text = RequireText(payload);
        var (resolved, before) = ResolveWritable(payload);
        var result = FileMutations.Write(Roots, resolved, before, text, OptionalString(payload, "expected_sha256", 64));
        Remember(result);
        _audit?.Write(AuditRequestEvent, capability: DocumentCapabilityNames.FileWrite, status: before is null ? "created" : "replaced", detail: result["after"]!["file_id"]!.GetValue<string>());
        return result;
    }

    private JsonObject AppendFile(JsonObject payload)
    {
        var text = RequireText(payload);
        var target = ResolveTarget(payload, "payload");
        var result = FileMutations.Append(Roots, target.Path, target.Record, text, OptionalString(payload, "expected_sha256", 64));
        Remember(result);
        _audit?.Write(AuditRequestEvent, capability: DocumentCapabilityNames.FileAppend, status: "appended", detail: target.Record.FileId);
        return result;
    }

    private JsonObject RenameFile(JsonObject payload)
    {
        var target = ResolveTarget(payload, "payload");
        var result = FileMutations.Rename(target.Path, target.Record, RequireString(payload, "new_name", DocumentCapabilityNames.MaxFetchNameChars));
        Forget(target);
        Remember(result);
        _audit?.Write(AuditRequestEvent, capability: DocumentCapabilityNames.FileRename, status: "renamed", detail: target.Record.FileId);
        return result;
    }

    private JsonObject MoveFile(JsonObject payload)
    {
        var target = ResolveTarget(payload, "payload");
        var destinationEntry = OptionalString(payload, "destination_dir", MaxPathChars)
            ?? OptionalString(payload, "destination_folder", MaxPathChars)
            ?? throw DocumentErrors.Invalid("payload.destination_dir or payload.destination_folder is required");
        var destination = ConfineFolderEntry(destinationEntry, "payload.destination_dir");
        var result = FileMutations.Move(target.Path, target.Record, destination);
        Forget(target);
        Remember(result);
        _audit?.Write(AuditRequestEvent, capability: DocumentCapabilityNames.FileMove, status: "moved", detail: target.Record.FileId);
        return result;
    }

    private JsonObject CopyFile(JsonObject payload)
    {
        var target = ResolveTarget(payload, "payload");
        var destinationPath = OptionalString(payload, "destination_path", MaxPathChars);
        var destinationDir = OptionalString(payload, "destination_dir", MaxPathChars)
            ?? OptionalString(payload, "destination_folder", MaxPathChars);
        var newName = OptionalString(payload, "new_name", DocumentCapabilityNames.MaxFetchNameChars);
        string resolvedTarget;
        if (destinationPath is not null)
        {
            resolvedTarget = ConfineNewFile(destinationPath, "payload.destination_path");
        }
        else
        {
            var dir = destinationDir is null ? System.IO.Path.GetDirectoryName(target.Path)! : ConfineFolderEntry(destinationDir, "payload.destination_dir");
            var name = newName is null
                ? (destinationDir is null ? throw DocumentErrors.Invalid("payload.destination_path, payload.destination_dir or payload.new_name is required") : target.Record.Name)
                : FileMutations.RequireFileName(newName, "payload.new_name");
            resolvedTarget = System.IO.Path.Combine(dir, name);
        }

        var result = FileMutations.Copy(target.Path, target.Record, resolvedTarget);
        Remember(result);
        _audit?.Write(AuditRequestEvent, capability: DocumentCapabilityNames.FileCopy, status: "copied", detail: target.Record.FileId);
        return result;
    }

    private JsonObject RestoreFile(JsonObject payload)
    {
        var backupId = RequireString(payload, "backup_id", 64);
        var backup = FileMutations.FindBackup(Roots, backupId)
            ?? throw DocumentErrors.NotFound($"{backupId} is not a backup in any authorised root's undo store");
        var targetPath = OptionalString(payload, "target_path", MaxPathChars);
        var resolvedTarget = targetPath is null ? null : ConfineNewFile(targetPath, "payload.target_path");
        var result = FileMutations.Restore(Roots, backup, resolvedTarget);
        Remember(result);
        _audit?.Write(AuditRequestEvent, capability: DocumentCapabilityNames.FileRestore, status: "restored", detail: backup.BackupId);
        return result;
    }

    private static string RequireText(JsonObject payload)
    {
        var node = payload["text"];
        if (node is null || node.GetValueKind() != JsonValueKind.String)
        {
            throw DocumentErrors.Invalid("payload.text is required and must be a string");
        }

        var text = node.GetValue<string>();
        if (text.Length > FileMutations.MaxTextChars)
        {
            throw DocumentErrors.Unsupported($"payload.text is {text.Length} characters, over the {FileMutations.MaxTextChars} bound", DocumentErrors.TooLarge);
        }

        return text;
    }

    /// <summary>
    /// A write target: an existing file (through <see cref="ResolveTarget"/>, id or path) or a
    /// path that does not exist yet, whose PARENT resolves inside the roots and whose name is
    /// not secret-bearing. Returns the resolved path and the record before, null when new.
    /// </summary>
    private (string Path, FileRecord? Before) ResolveWritable(JsonObject payload)
    {
        var path = OptionalString(payload, "path", MaxPathChars);
        var folder = OptionalString(payload, "folder", MaxPathChars);
        if (path is null && folder is not null)
        {
            // The Cloud Core cannot name the owner's Documents folder - only this machine can
            // - so a NEW file arrives as a bucket (or bucket/relative) plus a name, the same
            // shape file.search's roots take; confined exactly as an absolute path would be.
            var name = FileMutations.RequireFileName(OptionalString(payload, "name", DocumentCapabilityNames.MaxFetchNameChars), "payload.name");
            path = System.IO.Path.Combine(ConfineFolderEntry(folder, "payload.folder"), name);
        }

        if (path is null || payload["file_id"] is not null)
        {
            var target = ResolveTarget(payload, "payload");
            return (target.Path, target.Record);
        }

        var candidate = ConfineNewFile(path, "payload.path");
        if (File.Exists(candidate))
        {
            var target = ResolveTarget(payload, "payload");
            return (target.Path, target.Record);
        }

        return (candidate, null);
    }

    /// <summary>A file path that may not exist yet: its parent must resolve inside the roots, its name must not be secret-bearing or a directory.</summary>
    private string ConfineNewFile(string raw, string where)
    {
        if (!System.IO.Path.IsPathRooted(raw))
        {
            throw DocumentErrors.Invalid($"{where} must be absolute");
        }

        string full;
        try
        {
            full = System.IO.Path.GetFullPath(raw);
        }
        catch (Exception)
        {
            throw DocumentErrors.Denied(RootsRefusal(where));
        }

        var name = FileMutations.RequireFileName(System.IO.Path.GetFileName(full), where);
        if (Roots.Confine(full) is { } existing)
        {
            if (Directory.Exists(existing))
            {
                throw DocumentErrors.Invalid($"{where} names a directory, not a file");
            }

            return existing;
        }

        var parent = System.IO.Path.GetDirectoryName(full);
        var parentResolved = parent is null ? null : Roots.Confine(parent);
        if (parentResolved is null)
        {
            throw DocumentErrors.Denied(RootsRefusal(where));
        }

        return System.IO.Path.Combine(parentResolved, name);
    }

    /// <summary>A bucket name (or bucket/relative segments) as a real, authorised directory - the search-roots rule.</summary>
    private string ConfineFolderEntry(string entry, string where)
    {
        var candidate = System.IO.Path.IsPathRooted(entry)
            ? entry
            : WellKnownFolders.ResolveEntry(entry)
              ?? throw DocumentErrors.Invalid($"{where} takes an absolute path or one of: {WellKnownFolders.NamesForMessage} (optionally followed by relative segments)");
        return ConfineDirectory(candidate, where);
    }

    private string ConfineDirectory(string raw, string where)
    {
        if (!System.IO.Path.IsPathRooted(raw))
        {
            throw DocumentErrors.Invalid($"{where} must be absolute");
        }

        var resolved = Roots.Confine(raw) ?? throw DocumentErrors.Denied(RootsRefusal(where));
        if (!Directory.Exists(resolved))
        {
            throw DocumentErrors.Invalid($"{where} does not name a folder");
        }

        return resolved;
    }

    /// <summary>A mutation's <c>after</c> record is a file this companion can now be asked about by id.</summary>
    private void Remember(JsonObject result)
    {
        if (result["after"] is JsonObject after && after["file_id"]?.GetValue<string>() is { } id && after["path"]?.GetValue<string>() is { } path)
        {
            _known[id] = path;
        }
    }

    private void Forget(Target target) => _known.TryRemove(target.Record.FileId, out _);

    // ================================================================== file.fetch (M22)

    /// <summary>
    /// §6k, in this order: the payload's shape (<c>validation_error</c>, before any request);
    /// <c>open</c> needs the operator (<c>capability_missing</c>); the URL's origin and path
    /// (<c>permission_denied</c>); the Downloads root RESOLVED and CONTAINED by the roots
    /// (<c>permission_denied</c>); then <see cref="FileFetch.RunAsync"/> — at most two in
    /// flight, size bound while streaming, every read under the cap, hash before the rename
    /// and again under the final name, Mark-of-the-Web on the kept file, an existing file never
    /// replaced. The kept file gets a record (and a <c>file_id</c> this companion will resolve
    /// again); <c>open: true</c> then runs the operator's real <c>file.open</c> on the resolved
    /// path and the result carries its observation as <c>opened</c> — a failure to open is
    /// reported inside <c>opened</c> (<c>{opened: false, error: {…}}</c>), because the fetch
    /// itself did succeed.
    /// </summary>
    private async Task<JsonObject> FetchAsync(JsonObject payload, TimeSpan budget, CancellationToken cancellationToken)
    {
        var started = Stopwatch.StartNew();
        var request = FileFetch.Parse(payload);
        if (request.Open && _operator is null)
        {
            throw new CapabilityException(ErrorClasses.CapabilityMissing, "payload.open needs the Digital Operator's file.open, which this companion was built without; nothing was fetched", retryable: false);
        }

        FileFetch.RequireOrigin(request.Url, FetchOrigin);

        var downloads = DownloadsRoot;
        var directory = string.IsNullOrWhiteSpace(downloads) ? null : Roots.Confine(downloads);
        if (directory is null || !Directory.Exists(directory))
        {
            throw DocumentErrors.Denied($"the Downloads root does not resolve to a directory inside the owner's authorised roots ({string.Join(";", AuthorisedRootsConfigured)}); nothing was fetched");
        }

        var outcome = await _fetch.RunAsync(request, directory, Roots, cancellationToken).ConfigureAwait(false);
        var record = FileRecord.From(outcome.Path, hashContent: true);
        _known[record.FileId] = outcome.Path;
        _logger.LogInformation("file.fetch kept {Bytes} bytes from {Origin} (sha256 verified twice, mark-of-the-web {Marked}) in {Ms} ms", outcome.Bytes, FetchOrigin, outcome.MarkOfTheWeb, started.ElapsedMilliseconds);

        var result = new JsonObject
        {
            ["file"] = record.ToJson(),
            ["path"] = outcome.Path,
            ["verified"] = true,
            ["sha256"] = request.Sha256,
            ["bytes"] = outcome.Bytes,
            ["mark_of_the_web"] = outcome.MarkOfTheWeb,
        };

        if (request.Open)
        {
            result["opened"] = await OpenFetchedAsync(outcome.Path, request.Application, budget, started.Elapsed, cancellationToken).ConfigureAwait(false);
        }

        return result;
    }

    /// <summary>The operator's own <c>file.open</c> (its confinement, its argument policy, its window wait), on the rest of this request's budget.</summary>
    private async Task<JsonObject> OpenFetchedAsync(string path, string? application, TimeSpan budget, TimeSpan elapsed, CancellationToken cancellationToken)
    {
        var payload = new JsonObject { ["path"] = path };
        if (application is not null)
        {
            payload["application"] = application;
        }

        var remaining = budget > TimeSpan.Zero ? budget - elapsed : TimeSpan.Zero;
        if (budget > TimeSpan.Zero && remaining < TimeSpan.FromSeconds(1))
        {
            remaining = TimeSpan.FromSeconds(1);
        }

        try
        {
            return await _operator!.ExecuteAsync(OperatorCapabilityNames.FileOpen, payload, remaining, cancellationToken).ConfigureAwait(false);
        }
        catch (CapabilityException ex) when (!cancellationToken.IsCancellationRequested)
        {
            _logger.LogWarning("file.fetch kept the file but file.open failed: {Class}: {Reason}", ex.ErrorClass, ex.Message);
            return new JsonObject
            {
                ["opened"] = false,
                ["error"] = new JsonObject { ["class"] = ex.ErrorClass, ["message"] = ex.Message, ["retryable"] = ex.Retryable },
            };
        }
    }

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
                // An entry is an absolute path, or a bucket name the DEVICE resolves
                // (packages/protocol/file-search-roots.json). The Cloud Core cannot name the
                // owner's Documents folder - only this machine can - so it sends the bucket
                // and this resolves it. Either way the result is confined below: a bucket is
                // never more authority than a path, and cannot reach anywhere the owner's
                // well-known folders do not.
                var candidate = raw;
                if (!Path.IsPathRooted(candidate))
                {
                    candidate = WellKnownFolders.ResolveEntry(raw)
                                ?? throw DocumentErrors.Invalid(
                                    $"payload.roots takes an absolute path or one of: {WellKnownFolders.NamesForMessage}"
                                    + " (optionally followed by relative segments)");
                }

                var resolved = Roots.Confine(candidate)
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

        // M28 row 26.16. A PE says what build it is; nothing else in this result does, and a
        // native build read back without it could only ever be `unverified` on Cloud Core.
        // Additive: no new capability name and no new file kind - an executable extension is
        // `unknown` to FileKinds and stays so. The block appears only when the bytes really
        // are a PE, so a file that merely ends in .exe gets nothing rather than a guess.
        if (OperatorCapabilities.ExecutableExtensions.Contains(target.Record.Extension))
        {
            var pe = PeImageReader.TryRead(target.Path);
            if (pe is not null)
            {
                result["pe"] = pe;
            }
        }

        // B49 (ADR-0161): the Android half of the same idea - an APK or a bundle says which build
        // it is in its own manifest, and the block appears only when that manifest really reads.
        var android = AndroidPackageReader.TryRead(target.Path);
        if (android is not null)
        {
            result["android"] = android;
        }

        var extractor = ExtractorFor(target.Kind);
        if (target.Kind == FileKinds.Archive)
        {
            // B32 req 142: the central directory, never an entry inflated.
            result["archive"] = ArchiveInspector.Inspect(target.Path);
        }
        else if (extractor is not null)
        {
            RequireBoundedContainer(target);
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
                if (decoded.Truncated)
                {
                    result["truncated"] = true;
                }
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

        // total_chars counts what was decoded: the whole file up to the prefix bound; when the
        // file went on, truncated says so even for a window that reached the prefix's end.
        var total = decoded.Text.Length;
        var start = Math.Min(offset, total);
        var count = Math.Min(length, total - start);
        return new JsonObject
        {
            ["file"] = target.Record.ToJson(),
            ["text"] = decoded.Text.Substring(start, count),
            ["encoding"] = decoded.Encoding,
            ["truncated"] = decoded.Truncated || start + count < total,
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
        RequireBoundedContainer(target);
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
            OptionalInt(payload, "max_chars", 1, DocumentCapabilityNames.MaxExtractChars) ?? DocumentCapabilityNames.MaxExtractChars,
            OptionalString(payload, "language", 16));
    }

    // ================================================================== file.trash (B32)

    /// <summary>
    /// B32 requirement 150: send ONE file to the Recycle Bin — never a permanent delete, so
    /// the owner's own undo is a right-click away. The target goes through the same
    /// resolution every other capability uses (roots, secret-bearing names, existence), the
    /// result re-reads the path afterwards and says what it observed. A directory is refused.
    /// </summary>
    private JsonObject Trash(JsonObject payload)
    {
        var target = ResolveTarget(payload, "payload");
        if (Directory.Exists(target.Path))
        {
            throw DocumentErrors.Invalid("payload names a directory; file.trash moves one file");
        }

        var before = target.Record.ToJson();
        // B34 req 159/161: a delete the Cloud Core wants to be able to undo takes a copy
        // into the undo store first; the Recycle Bin stays the owner's own way back.
        FileMutations.BackupRecord? backup = null;
        if (payload["backup"]?.GetValue<bool>() == true)
        {
            backup = FileMutations.Backup(Roots, target.Path, "trash");
        }

        try
        {
            Microsoft.VisualBasic.FileIO.FileSystem.DeleteFile(
                target.Path,
                Microsoft.VisualBasic.FileIO.UIOption.OnlyErrorDialogs,
                Microsoft.VisualBasic.FileIO.RecycleOption.SendToRecycleBin,
                Microsoft.VisualBasic.FileIO.UICancelOption.ThrowException);
        }
        catch (UnauthorizedAccessException ex)
        {
            throw DocumentErrors.Denied($"'{target.Record.Name}' could not be moved to the Recycle Bin: {ex.Message}");
        }
        catch (IOException ex)
        {
            throw DocumentErrors.Denied($"'{target.Record.Name}' could not be moved to the Recycle Bin: {ex.Message}");
        }

        var stillThere = File.Exists(target.Path);
        _audit?.Write(AuditRequestEvent, capability: DocumentCapabilityNames.FileTrash, status: stillThere ? "still_present" : "trashed", detail: target.Record.FileId);
        return new JsonObject
        {
            ["trashed"] = !stillThere,
            ["method"] = "recycle_bin",
            ["file"] = before,
            ["backup"] = backup?.ToJson(),
            ["observed"] = new JsonObject { ["exists"] = stillThere },
        };
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
            result["truncated"] = aText.Truncated || bText.Truncated;
            return result;
        }

        if (!FileKinds.IsExtractable(a.Kind))
        {
            new RefDiff([], [], [], []).WriteTo(result);
            result["summary"] = "unknown kind: content identity only";
            return result;
        }

        // Both containers are bounded before either is extracted: a bomb on side B is refused
        // before side A's work is done, and neither SDK is entered for a refused pair.
        RequireBoundedContainer(a);
        RequireBoundedContainer(b);
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
    /// <c>permission_denied</c>, so an answer never says whether an outside path exists. A
    /// secret-bearing NAME is refused first, lexically, before anything is looked up, so
    /// <c>.env</c> answers <c>secret_bearing_name</c> whether or not it exists — the
    /// <c>not_found</c> branch below never sees one (the M20 review's existence oracle).
    /// </summary>
    private string ConfineFile(string raw, string where)
    {
        if (!System.IO.Path.IsPathRooted(raw))
        {
            throw DocumentErrors.Invalid($"{where}.path must be absolute");
        }

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

        if (!string.IsNullOrEmpty(name) && SecretNames.IsSecretBearing(name))
        {
            throw DocumentErrors.Denied($"'{name}' is a secret-bearing name and is never read", SecretNames.Detail);
        }

        var resolved = Roots.Confine(raw);
        if (resolved is null)
        {
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

    /// <summary>The decompression bound for a package kind (<see cref="ContainerGuard"/>): read from the zip's central directory, before the extractor — and its SDK — is entered.</summary>
    private static void RequireBoundedContainer(Target target)
    {
        if (FileKinds.IsPackage(target.Kind))
        {
            ContainerGuard.RequireBoundedPackage(target.Path, target.Kind);
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
