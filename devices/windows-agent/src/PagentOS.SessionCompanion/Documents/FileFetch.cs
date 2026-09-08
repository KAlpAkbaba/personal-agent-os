using System.Globalization;
using System.Net;
using System.Runtime.Versioning;
using System.Security.Cryptography;
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

/// <summary>What <see cref="FileFetch.Run"/> kept: the RESOLVED final path and how many bytes arrived.</summary>
public sealed record FetchOutcome(string Path, long Bytes);

/// <summary>
/// The download half of <c>file.fetch</c> (M22_ARTIFACT_FACTORY_SPEC.md §4, ADR-0085
/// decision 4, DEVICE_PROTOCOL.md §6k): one Cloud Core render, from the origin the device
/// dialled and nowhere else, into the Downloads root, verified before it is kept.
/// <list type="number">
/// <item><b>Where it comes from.</b> The URL's scheme, host and port must equal the origin the
/// Device Service handed this companion in the pipe challenge, its path must start with
/// <see cref="DocumentCapabilityNames.FetchPathPrefix"/>, and a redirect is never followed
/// (<see cref="HttpClientHandler.AllowAutoRedirect"/> is off; a 3xx is
/// <c>dependency_unavailable</c>). The request carries exactly what the URL carries — the
/// Cloud Core signs the URL; this side adds no owner token, no cookie, no header of its own.</item>
/// <item><b>How big it is.</b> <c>payload.size</c> (≤ 50 MiB) is the bound: a
/// <c>Content-Length</c> that disagrees is refused before a byte is read, and the body is
/// counted while it streams — one byte past the bound aborts the connection and removes the
/// partial file (<c>validation_error</c>). A body that ends short is <c>postcondition_failed</c>.</item>
/// <item><b>That it is the render Cloud Core described.</b> The SHA-256 is computed while
/// streaming and compared to <c>payload.sha256</c> BEFORE the temp file is renamed to its
/// final name; a mismatch deletes it and answers <c>postcondition_failed</c>. Nothing with the
/// requested name ever exists unless it has the requested hash.</item>
/// <item><b>Where it lands.</b> Only inside the Downloads root, which is itself resolved and
/// contained by <see cref="AuthorisedRoots"/> (resolve-then-contain, ADR-0082 addendum 2):
/// <c>payload.name</c> must be a plain file name (no separators, no <c>..</c>, no drive, no
/// reserved device name, no control characters, ≤ 120 characters, never a secret-bearing or
/// executable name); the bytes go to a temp name beside it (<c>CreateNew</c>, so a planted
/// reparse point is never opened) and are moved to the final name with <c>overwrite: false</c>
/// — an existing file is never replaced, the new one gets <c> (2)</c>, <c> (3)</c>… What is
/// reported is the final path the file system resolved, checked to be inside the roots again.</item>
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

    /// <summary>The detail a hash mismatch carries for an in-process caller.</summary>
    public const string Sha256Mismatch = "sha256_mismatch";

    private static readonly HashSet<string> ReservedDeviceNames = new(StringComparer.OrdinalIgnoreCase)
    {
        "CON", "PRN", "AUX", "NUL", "CLOCK$",
        "COM0", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT0", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    };

    private static readonly char[] InvalidNameChars = Path.GetInvalidFileNameChars();

    private readonly HttpClient _http;
    private readonly bool _ownsHttp;

    /// <summary>
    /// With no client given, a pinned one: no redirects, no cookies, no automatic decompression
    /// (the bytes counted and hashed are the bytes on the wire), no client-side timeout of its
    /// own — the documents budget and the family's 30 s cap are the clock.
    /// </summary>
    public FileFetch(HttpClient? http = null)
    {
        _ownsHttp = http is null;
        _http = http ?? NewPinnedClient();
    }

    public static HttpClient NewPinnedClient()
    {
        var handler = new HttpClientHandler
        {
            AllowAutoRedirect = false,
            UseCookies = false,
            AutomaticDecompression = DecompressionMethods.None,
        };
        return new HttpClient(handler, disposeHandler: true) { Timeout = Timeout.InfiniteTimeSpan };
    }

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
    /// separators, the drive colon, wildcards and every control character); no <c>..</c>
    /// anywhere; not <c>.</c>; no trailing dot or space (Windows would strip it and the file
    /// would not be the name that was asked for); not a reserved device name with or without
    /// an extension; not a secret-bearing name (<see cref="SecretNames"/>); not an executable
    /// extension (<see cref="OperatorCapabilities.ExecutableExtensions"/>); not this class's
    /// own temp-name prefix.
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
            throw DocumentErrors.Invalid("payload.name has an executable extension; file.fetch writes documents, never programs");
        }

        return name;
    }

    // ================================================================== the download

    /// <summary>
    /// Download into <paramref name="downloadsDirectory"/> (already the RESOLVED, contained
    /// Downloads root) and keep the file only once it has the requested size and hash. Runs
    /// synchronously on the caller's thread (the documents dispatcher's pool thread); the
    /// token is the documents budget, and the family's cap applies on top of it.
    /// </summary>
    public FetchOutcome Run(FetchRequest request, string downloadsDirectory, AuthorisedRoots roots, CancellationToken cancellationToken)
    {
        using var capCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        capCts.CancelAfter(DocumentCapabilityNames.CommandTimeoutCap);
        var token = capCts.Token;

        var tempPath = Path.Combine(downloadsDirectory, TempPrefix + Guid.NewGuid().ToString("N") + TempSuffix);
        try
        {
            using var message = new HttpRequestMessage(HttpMethod.Get, request.Url);
            // Nothing is added: no Authorization, no cookie, no custom header. The URL is the
            // whole credential and the Cloud Core signed it.
            HttpResponseMessage response;
            try
            {
                response = _http.Send(message, HttpCompletionOption.ResponseHeadersRead, token);
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

            using (response)
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

                var (bytes, actual) = StreamToTemp(response, tempPath, request.Size, token);
                if (bytes != request.Size)
                {
                    throw Postcondition($"the download ended after {bytes} bytes but payload.size says {request.Size}; nothing was kept", detail: null);
                }

                if (!string.Equals(actual, request.Sha256, StringComparison.Ordinal))
                {
                    throw Postcondition("the bytes that arrived do not have payload.sha256; the file was removed and nothing was kept", Sha256Mismatch);
                }
            }

            var final = MoveToFinalName(tempPath, downloadsDirectory, request.Name);
            var resolved = roots.Confine(final);
            if (resolved is null
                || !string.Equals(Path.GetDirectoryName(resolved), downloadsDirectory, StringComparison.OrdinalIgnoreCase))
            {
                TryDelete(final);
                throw DocumentErrors.Denied("payload.name did not resolve to a file inside the Downloads root after the write; it was removed");
            }

            return new FetchOutcome(resolved, request.Size);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            TryDelete(tempPath);
            throw new CapabilityException(ErrorClasses.Timeout, $"payload.url did not finish downloading within the {DocumentCapabilityNames.CommandTimeoutCap.TotalSeconds:F0} s cap; nothing was kept", retryable: true);
        }
        catch (Exception)
        {
            TryDelete(tempPath);
            throw;
        }
    }

    /// <summary>
    /// The body to the temp file, counted and hashed as it streams. The bound is enforced on
    /// every read: the chunk that crosses <paramref name="bound"/> is not written, the response
    /// is disposed (the connection closes under the server, mid-body) and the partial file is
    /// the caller's to delete.
    /// </summary>
    private static (long Bytes, string Sha256) StreamToTemp(HttpResponseMessage response, string tempPath, long bound, CancellationToken cancellationToken)
    {
        long count = 0;
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        using var file = new FileStream(tempPath, FileMode.CreateNew, FileAccess.Write, FileShare.None, ReadBufferBytes, FileOptions.SequentialScan);
        using var body = response.Content.ReadAsStream(cancellationToken);
        var buffer = new byte[ReadBufferBytes];
        while (true)
        {
            cancellationToken.ThrowIfCancellationRequested();
            int read;
            try
            {
                read = body.Read(buffer, 0, buffer.Length);
            }
            catch (OperationCanceledException)
            {
                throw;
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

            file.Write(buffer, 0, read);
            hash.AppendData(buffer, 0, read);
        }

        file.Flush(flushToDisk: true);
        return (count, Convert.ToHexStringLower(hash.GetHashAndReset()));
    }

    /// <summary>
    /// <c>name</c>, else <c>stem (2).ext</c>, <c>stem (3).ext</c>… — never over an existing
    /// entry of any kind (<c>overwrite: false</c>; a reparse point planted under the name is an
    /// existing entry too and is simply skipped, never opened).
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

    private static void TryDelete(string path)
    {
        try
        {
            if (File.Exists(path))
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
    }
}
