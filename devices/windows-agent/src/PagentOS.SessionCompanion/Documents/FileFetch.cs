using System.Globalization;
using System.Net;
using System.Runtime.Versioning;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Operator;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>What a validated <c>file.fetch</c> payload asks for (DEVICE_PROTOCOL.md §6k).</summary>
/// <param name="Url">Absolute http(s), no user info; the origin and the path prefix are policy checks done separately.</param>
/// <param name="Name">A plain file name, already validated by <see cref="FileFetch.ValidateName"/>.</param>
/// <param name="Sha256">64 lower-case hex characters.</param>
/// <param name="Size">1 … <see cref="DocumentCapabilityNames.MaxFetchBytes"/>: the exact byte count expected, and the bound enforced while streaming.</param>
/// <param name="Open">Run <c>file.open</c> on the kept file.</param>
/// <param name="Application">The allowlisted application name to hand <c>file.open</c>, when <paramref name="Open"/>.</param>
public sealed record FetchRequest(Uri Url, string Name, string Sha256, long Size, bool Open, string? Application);

/// <summary>What <see cref="FileFetch.RunAsync"/> kept: the RESOLVED final path, how many bytes arrived, and whether the Mark-of-the-Web stream was written.</summary>
public sealed record FetchOutcome(string Path, long Bytes, bool MarkOfTheWeb);

/// <summary>
/// The download half of <c>file.fetch</c> (M22_ARTIFACT_FACTORY_SPEC.md §4, ADR-0085
/// decision 4 and addendum 3, DEVICE_PROTOCOL.md §6k): one Cloud Core render, from the origin
/// the device dialled and nowhere else, into the Downloads root, verified before it is kept
/// and verified again once it has its name.
/// <list type="number">
/// <item><b>Where it comes from.</b> The URL's scheme, host and port must equal the origin the
/// Device Service handed this companion in the pipe challenge, its path must start with
/// <see cref="DocumentCapabilityNames.FetchPathPrefix"/>, and a redirect is never followed
/// (<see cref="HttpClientHandler.AllowAutoRedirect"/> is off; a 3xx is
/// <c>dependency_unavailable</c>). The request carries exactly what the URL carries — the
/// Cloud Core signs the URL; this side adds no owner token, no cookie, no header of its own —
/// and goes straight to the origin: no system or environment proxy is consulted
/// (<see cref="HttpClientHandler.UseProxy"/> is off).</item>
/// <item><b>How big it is.</b> <c>payload.size</c> (≤ 50 MiB) is the bound: a
/// <c>Content-Length</c> that disagrees is refused before a byte is read, and the body is
/// counted while it streams — one byte past the bound aborts the connection and removes the
/// partial file (<c>validation_error</c>). A body that ends short is <c>postcondition_failed</c>.</item>
/// <item><b>How long it may take.</b> Every read of the body is asynchronous and carries the
/// cap token (<see cref="Cap"/>, the family's 30 s by default, linked to the caller's budget),
/// so a peer that sends its headers and then goes quiet cannot hold a thread or the partial
/// file past the cap: the read is abandoned, the connection dropped, the temp file deleted and
/// the answer is <c>timeout</c> (retryable). At most <see cref="MaxConcurrent"/> downloads are
/// in flight per companion; one more is refused before any request
/// (<c>dependency_unavailable</c>, retryable, detail <see cref="BusyDetail"/>).</item>
/// <item><b>That it is the render Cloud Core described.</b> The SHA-256 is computed while
/// streaming and compared to <c>payload.sha256</c> BEFORE the temp file is renamed to its
/// final name; a mismatch deletes it and answers <c>postcondition_failed</c>. The temp file's
/// handle is exclusive for reading and writing (only deletion is shared, which is what the
/// rename needs) and is held ACROSS the rename, so no other process can write the bytes
/// between the hash and the name. Then the final path is re-opened exclusively and hashed
/// again: a file that no longer has the requested hash — swapped under the name by whatever
/// runs as the owner — is deleted and the answer is <c>postcondition_failed</c> with detail
/// <see cref="Sha256MismatchAfterMove"/>. Nothing with the requested name is ever reported
/// unless it has the requested hash at the moment it is reported.</item>
/// <item><b>Where it lands.</b> Only inside the Downloads root, which is itself resolved and
/// contained by <see cref="AuthorisedRoots"/> (resolve-then-contain, ADR-0082 addendum 2):
/// <c>payload.name</c> must be a plain file name (no separators, no <c>..</c>, no drive, no
/// reserved device name, no control or Unicode format characters, ≤ 120 characters, never a
/// secret-bearing or executable name — the macro-enabled Office family included); the bytes
/// go to a temp name beside it (<c>CreateNew</c>, so a planted reparse point is never opened)
/// and are moved to the final name with <c>overwrite: false</c> — an existing file is never
/// replaced, the new one gets <c> (2)</c>, <c> (3)</c>… Every kept file carries the NTFS
/// Mark-of-the-Web (<c>Zone.Identifier</c>, <c>ZoneId=3</c>), so Office opens it in Protected
/// View and the shell asks before running anything from it, as it would for a browser
/// download. What is reported is the final path the file system resolved, checked to be
/// inside the roots again.</item>
/// </list>
/// Every refusal names the payload field it is about and never repeats the URL's host — a
/// foreign origin is "beklenen köken değil" and nothing more.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class FileFetch : IDisposable
{
    public const string TempPrefix = ".pagentos-fetch-";
    public const string TempSuffix = ".part";
    public const int MaxUrlChars = 2048;
    public const int ReadBufferBytes = 64 * 1024;
    public const int MaxCollisionSuffix = 1000;

    /// <summary>ADR-0085 addendum 3: how many downloads one companion has in flight at most; the next is <see cref="BusyDetail"/>.</summary>
    public const int MaxConcurrent = 2;

    /// <summary>The detail a hash mismatch carries for an in-process caller.</summary>
    public const string Sha256Mismatch = "sha256_mismatch";

    /// <summary>The detail when the file under its final name no longer has the hash the temp file had (it was swapped between the rename and the re-check); the file was deleted.</summary>
    public const string Sha256MismatchAfterMove = "sha256_mismatch_after_move";

    /// <summary>The detail when the kept file could not be re-opened exclusively for the re-check within a second (something else holds it); the file was deleted.</summary>
    public const string UnverifiableAfterMove = "unverifiable_after_move";

    /// <summary>The detail when <see cref="MaxConcurrent"/> downloads are already in flight.</summary>
    public const string BusyDetail = "fetch_busy";

    /// <summary>The Mark-of-the-Web stream name and content: Internet zone (3), nothing else — no referrer, no host URL (the signed URL stays out of the file system).</summary>
    public const string ZoneIdentifierStream = "Zone.Identifier";
    public const string ZoneIdentifierContent = "[ZoneTransfer]\r\nZoneId=3\r\n";

    private static readonly HashSet<string> ReservedDeviceNames = new(StringComparer.OrdinalIgnoreCase)
    {
        "CON", "PRN", "AUX", "NUL", "CLOCK$",
        "COM0", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT0", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    };

    private static readonly char[] InvalidNameChars = Path.GetInvalidFileNameChars();

    private readonly HttpClient _http;
    private readonly bool _ownsHttp;
    private readonly SemaphoreSlim _slots = new(MaxConcurrent, MaxConcurrent);

    /// <summary>
    /// With no client given, a pinned one: no redirects, no cookies, no proxy, no automatic
    /// decompression (the bytes counted and hashed are the bytes on the wire), no client-side
    /// timeout of its own — <paramref name="cap"/> (the family's 30 s by default) linked to the
    /// documents budget is the clock.
    /// </summary>
    public FileFetch(HttpClient? http = null, TimeSpan? cap = null)
    {
        _ownsHttp = http is null;
        _http = http ?? NewPinnedClient();
        Cap = cap ?? DocumentCapabilityNames.CommandTimeoutCap;
    }

    /// <summary>The longest one download may take, headers to last byte; the family's cap unless a lab shortens it.</summary>
    public TimeSpan Cap { get; }

    /// <summary>How many downloads are in flight right now (a lab reads it; the gate is <see cref="MaxConcurrent"/>).</summary>
    public int InFlight => MaxConcurrent - _slots.CurrentCount;

    /// <summary>
    /// Test seam (ADR-0085 addendum 3): runs with the FINAL path after the rename and before the
    /// re-verification, when the streaming handle has been released — the one moment a
    /// same-user process could swap the bytes under the name. A lab alters the file here and
    /// proves the re-check refuses and removes it. Null in production.
    /// </summary>
    public Action<string>? BeforeFinalVerify { get; set; }

    public static HttpClientHandler NewPinnedHandler()
        => new()
        {
            AllowAutoRedirect = false,
            UseCookies = false,
            UseProxy = false,
            AutomaticDecompression = DecompressionMethods.None,
        };

    public static HttpClient NewPinnedClient()
        => new(NewPinnedHandler(), disposeHandler: true) { Timeout = Timeout.InfiniteTimeSpan };

    // ================================================================== payload → request

    /// <summary>
    /// Shape validation only — every field's presence, type and bound — as <c>validation_error</c>
    /// naming the field. Runs before any policy check and long before any request, so a bad
    /// name or hash costs no network round trip. The origin and the path prefix are
    /// <see cref="RequireOrigin"/>'s.
    /// </summary>
    public static FetchRequest Parse(JsonObject payload)
    {
        var url = ParseUrl(payload["url"]);
        var name = ValidateName(RequireString(payload, "name", DocumentCapabilityNames.MaxFetchNameChars));
        var sha256 = ParseSha256(payload["sha256"]);
        var size = ParseSize(payload["size"]);
        var open = ParseOpen(payload["open"]);
        var application = payload["application"] is null ? null : RequireString(payload, "application", 260);
        if (application is not null && !open)
        {
            throw DocumentErrors.Invalid("payload.application is only meaningful with open: true");
        }

        return new FetchRequest(url, name, sha256, size, open, application);
    }

    /// <summary>
    /// The policy check: the URL must sit on <paramref name="expectedOrigin"/> — the origin the
    /// Device Service dialled — and under <see cref="DocumentCapabilityNames.FetchPathPrefix"/>.
    /// Anything else, and a companion that was never told an origin, is <c>permission_denied</c>;
    /// the message never repeats the URL's host.
    /// </summary>
    public static void RequireOrigin(Uri url, string? expectedOrigin)
    {
        if (string.IsNullOrWhiteSpace(expectedOrigin))
        {
            throw DocumentErrors.Denied("payload.url cannot be admitted: this companion has not been told the origin the device dialled, so no render URL can be recognised as the owner's own (beklenen köken değil); nothing was fetched");
        }

        if (!HttpOrigin.Same(HttpOrigin.Of(url), expectedOrigin))
        {
            throw DocumentErrors.Denied("payload.url is not on the origin this device dialled for its Cloud Core connection (beklenen köken değil); nothing was fetched");
        }

        if (!url.AbsolutePath.StartsWith(DocumentCapabilityNames.FetchPathPrefix, StringComparison.Ordinal))
        {
            throw DocumentErrors.Denied($"payload.url is on the expected origin but not under {DocumentCapabilityNames.FetchPathPrefix}, the only path a render is served from; nothing was fetched");
        }
    }

    /// <summary>
    /// A plain file name or a <c>validation_error</c> that names <c>payload.name</c>: trimmed;
    /// 1 … 120 characters; none of the file system's invalid characters (which include both
    /// separators, the drive colon, wildcards and the C0 control characters); no C1 control
    /// character and no Unicode format character (category Cf — the bidirectional overrides
    /// and isolates, the zero-width marks, the tags — which are invisible or reorder what
    /// Explorer shows, so <c>fdp.‮txt.docx</c> would display as a PDF); well-formed
    /// UTF-16; no <c>..</c> anywhere; not <c>.</c>; no trailing dot or space (Windows would
    /// strip it and the file would not be the name that was asked for); not a reserved device
    /// name with or without an extension; not a secret-bearing name (<see cref="SecretNames"/>);
    /// not an executable extension (<see cref="OperatorCapabilities.ExecutableExtensions"/>,
    /// the macro-enabled Office family included); not this class's own temp-name prefix.
    /// </summary>
    public static string ValidateName(string raw)
    {
        var name = raw.Trim();
        if (name.Length == 0)
        {
            throw DocumentErrors.Invalid("payload.name must not be empty");
        }

        if (name.Length > DocumentCapabilityNames.MaxFetchNameChars)
        {
            throw DocumentErrors.Invalid($"payload.name exceeds {DocumentCapabilityNames.MaxFetchNameChars} characters ({name.Length})");
        }

        if (name.IndexOfAny(InvalidNameChars) >= 0 || name.Contains('/') || name.Contains('\\') || name.Contains(':'))
        {
            throw DocumentErrors.Invalid("payload.name must be a plain file name: no separators, no drive, no wildcards, no control characters");
        }

        if (HasInvisibleOrDirectionalCharacter(name))
        {
            throw DocumentErrors.Invalid("payload.name must be a plain file name: no control characters and no Unicode format characters (bidirectional overrides, zero-width marks and the like, which change what the name looks like)");
        }

        if (name.Contains("..", StringComparison.Ordinal) || name == ".")
        {
            throw DocumentErrors.Invalid("payload.name must be a plain file name: no \"..\" and no \".\"");
        }

        if (name.EndsWith('.') || name.EndsWith(' '))
        {
            throw DocumentErrors.Invalid("payload.name must not end with a dot or a space");
        }

        var stem = name.Split('.', 2)[0];
        if (ReservedDeviceNames.Contains(stem.TrimEnd()))
        {
            throw DocumentErrors.Invalid("payload.name is a reserved device name");
        }

        if (name.StartsWith(TempPrefix, StringComparison.OrdinalIgnoreCase))
        {
            throw DocumentErrors.Invalid("payload.name must not use the companion's own temporary-file prefix");
        }

        if (SecretNames.IsSecretBearing(name))
        {
            throw DocumentErrors.Invalid($"payload.name is a secret-bearing name; the artifact factory never writes one [{SecretNames.Detail}]");
        }

        if (OperatorCapabilities.ExecutableExtensions.Contains(Path.GetExtension(name)))
        {
            throw DocumentErrors.Invalid("payload.name has an executable extension (a program, a script, a launcher or a macro-enabled Office file); file.fetch writes documents, never programs");
        }

        return name;
    }

    /// <summary>Any C0/C1 control (Cc), any format character (Cf), or a lone surrogate.</summary>
    public static bool HasInvisibleOrDirectionalCharacter(string name)
    {
        var index = 0;
        while (index < name.Length)
        {
            if (!Rune.TryGetRuneAt(name, index, out var rune))
            {
                return true;
            }

            var category = Rune.GetUnicodeCategory(rune);
            if (category is UnicodeCategory.Control or UnicodeCategory.Format)
            {
                return true;
            }

            index += rune.Utf16SequenceLength;
        }

        return false;
    }

    // ================================================================== the download

    /// <summary>
    /// Download into <paramref name="downloadsDirectory"/> (already the RESOLVED, contained
    /// Downloads root) and keep the file only once it has the requested size and hash — and
    /// still has it under its final name. Asynchronous end to end: every network read and
    /// every file write carries the token, which is the documents budget linked with
    /// <see cref="Cap"/>; a cancellation from either deletes the partial file (the cap's is
    /// answered as <c>timeout</c>, the caller's is rethrown for the dispatcher's <c>cancelled</c>).
    /// </summary>
    public async Task<FetchOutcome> RunAsync(FetchRequest request, string downloadsDirectory, AuthorisedRoots roots, CancellationToken cancellationToken)
    {
        if (!_slots.Wait(0, CancellationToken.None))
        {
            throw new CapabilityException(
                ErrorClasses.DependencyUnavailable,
                $"file.fetch already has {MaxConcurrent} downloads in flight on this companion; nothing was requested [{BusyDetail}]",
                retryable: true,
                new Dictionary<string, object?> { [DocumentErrors.DetailKey] = BusyDetail });
        }

        try
        {
            return await RunSlotAsync(request, downloadsDirectory, roots, cancellationToken).ConfigureAwait(false);
        }
        finally
        {
            _slots.Release();
        }
    }

    private async Task<FetchOutcome> RunSlotAsync(FetchRequest request, string downloadsDirectory, AuthorisedRoots roots, CancellationToken cancellationToken)
    {
        using var capCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        capCts.CancelAfter(Cap);
        var token = capCts.Token;

        var tempPath = Path.Combine(downloadsDirectory, TempPrefix + Guid.NewGuid().ToString("N") + TempSuffix);
        string? finalPath = null;
        try
        {
            using var message = new HttpRequestMessage(HttpMethod.Get, request.Url);
            // Nothing is added: no Authorization, no cookie, no custom header. The URL is the
            // whole credential and the Cloud Core signed it.
            HttpResponseMessage response;
            try
            {
                response = await _http.SendAsync(message, HttpCompletionOption.ResponseHeadersRead, token).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (HttpRequestException ex)
            {
                throw Unavailable($"payload.url could not be fetched ({ex.HttpRequestError}); nothing was kept", retryable: true);
            }
            catch (Exception ex) when (ex is IOException or InvalidOperationException or System.Net.Sockets.SocketException)
            {
                throw Unavailable("payload.url could not be fetched; nothing was kept", retryable: true);
            }

            // The temp file's handle: exclusive for reading and writing, shared only for
            // deletion (a rename needs that), held from the first byte across the rename.
            using (response)
            using (var file = new FileStream(tempPath, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.Delete, ReadBufferBytes, FileOptions.Asynchronous | FileOptions.SequentialScan))
            {
                var status = (int)response.StatusCode;
                if (status is >= 300 and < 400)
                {
                    throw Unavailable($"payload.url answered a redirect (HTTP {status}); redirects are never followed — the render must be served by the dialled origin itself; nothing was fetched", retryable: false);
                }

                if (!response.IsSuccessStatusCode)
                {
                    throw Unavailable($"payload.url answered HTTP {status}; nothing was fetched", retryable: status >= 500);
                }

                if (response.Content.Headers.ContentLength is long declared && declared != request.Size)
                {
                    throw DocumentErrors.Invalid($"payload.url declares Content-Length {declared} bytes but payload.size says {request.Size}; nothing was read");
                }

                var (bytes, actual) = await StreamToTempAsync(response, file, request.Size, token).ConfigureAwait(false);
                if (bytes != request.Size)
                {
                    throw Postcondition($"the download ended after {bytes} bytes but payload.size says {request.Size}; nothing was kept", detail: null);
                }

                if (!string.Equals(actual, request.Sha256, StringComparison.Ordinal))
                {
                    throw Postcondition("the bytes that arrived do not have payload.sha256; the file was removed and nothing was kept", Sha256Mismatch);
                }

                // The rename happens while the handle is still open: nothing else can have
                // written a byte between the hash above and the name below.
                finalPath = MoveToFinalName(tempPath, downloadsDirectory, request.Name);
            }

            BeforeFinalVerify?.Invoke(finalPath);
            VerifyFinal(finalPath, request.Sha256, request.Size);
            var marked = TryMarkOfTheWeb(finalPath);

            var resolved = roots.Confine(finalPath);
            if (resolved is null
                || !string.Equals(Path.GetDirectoryName(resolved), downloadsDirectory, StringComparison.OrdinalIgnoreCase))
            {
                TryDelete(finalPath);
                throw DocumentErrors.Denied("payload.name did not resolve to a file inside the Downloads root after the write; it was removed");
            }

            return new FetchOutcome(resolved, request.Size, marked);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            TryDelete(tempPath);
            TryDelete(finalPath);
            throw new CapabilityException(ErrorClasses.Timeout, $"payload.url did not finish downloading within the {Cap.TotalSeconds:F0} s cap; the connection was dropped and nothing was kept", retryable: true);
        }
        catch (Exception)
        {
            TryDelete(tempPath);
            throw;
        }
    }

    /// <summary>
    /// The body into <paramref name="file"/>, counted and hashed as it streams. Every read
    /// awaits with the token, so a peer that stops sending is abandoned at the cap rather
    /// than waited for. The bound is enforced on every read: the chunk that crosses
    /// <paramref name="bound"/> is not written, the response is disposed (the connection
    /// closes under the server, mid-body) and the partial file is the caller's to delete.
    /// </summary>
    private static async Task<(long Bytes, string Sha256)> StreamToTempAsync(HttpResponseMessage response, FileStream file, long bound, CancellationToken cancellationToken)
    {
        long count = 0;
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        using var body = await response.Content.ReadAsStreamAsync(cancellationToken).ConfigureAwait(false);
        var buffer = new byte[ReadBufferBytes];
        while (true)
        {
            cancellationToken.ThrowIfCancellationRequested();
            int read;
            try
            {
                read = await body.ReadAsync(buffer.AsMemory(), cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch (Exception ex) when (cancellationToken.IsCancellationRequested && (ex is IOException or HttpRequestException or ObjectDisposedException))
            {
                // The cap fired while the transport was mid-read; some transports report that
                // as an aborted connection rather than a cancellation.
                throw new OperationCanceledException(cancellationToken);
            }
            catch (Exception ex) when (ex is IOException or HttpRequestException or System.Net.Sockets.SocketException or ObjectDisposedException)
            {
                throw Unavailable($"the download was interrupted after {count} bytes; nothing was kept", retryable: true);
            }

            if (read == 0)
            {
                break;
            }

            count += read;
            if (count > bound)
            {
                throw DocumentErrors.Invalid($"payload.url kept sending past payload.size ({bound} bytes); the download was aborted at {count} bytes and nothing was kept");
            }

            await file.WriteAsync(buffer.AsMemory(0, read), cancellationToken).ConfigureAwait(false);
            hash.AppendData(buffer, 0, read);
        }

        await file.FlushAsync(cancellationToken).ConfigureAwait(false);
        file.Flush(flushToDisk: true);
        return (count, Convert.ToHexStringLower(hash.GetHashAndReset()));
    }

    /// <summary>
    /// <c>name</c>, else <c>stem (2).ext</c>, <c>stem (3).ext</c>… — never over an existing
    /// entry of any kind (<c>overwrite: false</c>; a reparse point planted under the name is an
    /// existing entry too and is simply skipped, never opened). Called while the temp file's
    /// handle is still open (shared for deletion, which is what the rename uses).
    /// </summary>
    private static string MoveToFinalName(string tempPath, string directory, string name)
    {
        var stem = Path.GetFileNameWithoutExtension(name);
        var extension = Path.GetExtension(name);
        for (var attempt = 1; attempt <= MaxCollisionSuffix; attempt++)
        {
            var candidate = attempt == 1 ? name : $"{stem} ({attempt.ToString(CultureInfo.InvariantCulture)}){extension}";
            var target = Path.Combine(directory, candidate);
            if (File.Exists(target) || Directory.Exists(target))
            {
                continue;
            }

            try
            {
                File.Move(tempPath, target, overwrite: false);
                return target;
            }
            catch (IOException) when (File.Exists(target) || Directory.Exists(target))
            {
                // Lost the race to another writer: the next suffix.
            }
        }

        throw Unavailable($"payload.name and its first {MaxCollisionSuffix} suffixed variants all exist in the Downloads root; nothing was kept", retryable: false);
    }

    /// <summary>
    /// The re-check under the final name: opened exclusively (no sharing at all — if
    /// something else holds it, a few short retries, then the file is deleted and the answer
    /// is <see cref="UnverifiableAfterMove"/>), its length and SHA-256 compared with what was
    /// asked for; a mismatch deletes it and answers <see cref="Sha256MismatchAfterMove"/>.
    /// </summary>
    private static void VerifyFinal(string finalPath, string expectedSha256, long expectedSize)
    {
        FileStream? file = null;
        for (var attempt = 0; file is null; attempt++)
        {
            try
            {
                file = new FileStream(finalPath, FileMode.Open, FileAccess.Read, FileShare.None, ReadBufferBytes, FileOptions.SequentialScan);
            }
            catch (IOException) when (attempt < 10 && File.Exists(finalPath))
            {
                Thread.Sleep(100);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                TryDelete(finalPath);
                throw Postcondition("the kept file could not be re-opened exclusively for its final check; it was removed and nothing was kept", UnverifiableAfterMove);
            }
        }

        string actual;
        long length;
        using (file)
        {
            length = file.Length;
            actual = Convert.ToHexStringLower(SHA256.HashData(file));
        }

        if (length != expectedSize || !string.Equals(actual, expectedSha256, StringComparison.Ordinal))
        {
            TryDelete(finalPath);
            throw Postcondition("the file under its final name no longer has payload.sha256 (it was altered between the rename and the final check); it was removed and nothing was kept", Sha256MismatchAfterMove);
        }
    }

    /// <summary>
    /// The Mark-of-the-Web on the kept file: the <c>Zone.Identifier</c> alternate data stream
    /// with <c>ZoneId=3</c> (Internet). Best effort — a volume without alternate streams
    /// (FAT, exFAT) cannot carry one, and the outcome says so — but on NTFS, where the owner's
    /// Downloads folder lives, it is what makes Office open the file in Protected View.
    /// </summary>
    private static bool TryMarkOfTheWeb(string finalPath)
    {
        try
        {
            using var stream = new FileStream(finalPath + ":" + ZoneIdentifierStream, FileMode.Create, FileAccess.Write, FileShare.None);
            var bytes = Encoding.ASCII.GetBytes(ZoneIdentifierContent);
            stream.Write(bytes, 0, bytes.Length);
            return true;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or NotSupportedException)
        {
            return false;
        }
    }

    private static void TryDelete(string? path)
    {
        try
        {
            if (path is not null && File.Exists(path))
            {
                File.Delete(path);
            }
        }
        catch (Exception)
        {
            // Best effort; the caller's refusal already says nothing was kept and the temp
            // name is unique to this attempt.
        }
    }

    // ================================================================== helpers: payload

    private static Uri ParseUrl(JsonNode? node)
    {
        var raw = node?.GetValueKind() == JsonValueKind.String ? node.GetValue<string>() : null;
        if (string.IsNullOrWhiteSpace(raw))
        {
            throw DocumentErrors.Invalid("payload.url is required and must be a string");
        }

        if (raw.Length > MaxUrlChars)
        {
            throw DocumentErrors.Invalid($"payload.url exceeds {MaxUrlChars} characters");
        }

        if (!Uri.TryCreate(raw, UriKind.Absolute, out var url) || HttpOrigin.Of(url) is null)
        {
            throw DocumentErrors.Invalid("payload.url must be an absolute http(s) URL");
        }

        if (url.UserInfo.Length > 0)
        {
            throw DocumentErrors.Invalid("payload.url must not carry credentials");
        }

        return url;
    }

    private static string ParseSha256(JsonNode? node)
    {
        var raw = node?.GetValueKind() == JsonValueKind.String ? node.GetValue<string>() : null;
        if (raw is null || raw.Length != 64 || !raw.All(Uri.IsHexDigit))
        {
            throw DocumentErrors.Invalid("payload.sha256 must be 64 hexadecimal characters");
        }

        return raw.ToLowerInvariant();
    }

    private static long ParseSize(JsonNode? node)
    {
        if (node?.GetValueKind() != JsonValueKind.Number
            || !long.TryParse(node.ToJsonString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var value))
        {
            throw DocumentErrors.Invalid("payload.size must be a whole number of bytes");
        }

        if (value < 1 || value > DocumentCapabilityNames.MaxFetchBytes)
        {
            throw DocumentErrors.Invalid($"payload.size must be between 1 and {DocumentCapabilityNames.MaxFetchBytes} bytes ({DocumentBounds.Mebibytes(DocumentCapabilityNames.MaxFetchBytes)})");
        }

        return value;
    }

    private static bool ParseOpen(JsonNode? node)
    {
        if (node is null)
        {
            return false;
        }

        var kind = node.GetValueKind();
        if (kind is not (JsonValueKind.True or JsonValueKind.False))
        {
            throw DocumentErrors.Invalid("payload.open must be a boolean");
        }

        return kind == JsonValueKind.True;
    }

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

    private static CapabilityException Unavailable(string message, bool retryable)
        => new(ErrorClasses.DependencyUnavailable, message, retryable);

    private static CapabilityException Postcondition(string message, string? detail)
        => detail is null
            ? new CapabilityException(ErrorClasses.PostconditionFailed, message, retryable: false)
            : new CapabilityException(ErrorClasses.PostconditionFailed, message + $" [{detail}]", retryable: false, new Dictionary<string, object?> { [DocumentErrors.DetailKey] = detail });

    public void Dispose()
    {
        if (_ownsHttp)
        {
            _http.Dispose();
        }

        _slots.Dispose();
    }
}
