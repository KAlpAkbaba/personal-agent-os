using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Companion.Audio.Audio;

namespace PagentOS.SessionCompanion;

/// <summary>
/// Executes <c>desktop.play_audio</c> (DEVICE_PROTOCOL.md §6h, M18.3): plays ONE short piece of
/// audio the owner's own broker rendered — a spoken greeting after a wake alarm — in the owner's
/// interactive session.
///
/// <para><b>Four things bound what this can do, and all four are checked on the device.</b></para>
/// <list type="number">
/// <item><b>Where it came from.</b> The Device Service refuses the command before it reaches the
/// pipe unless the URL's origin is the broker REST origin this device is configured for
/// (<c>security_scope_error</c>). A capability that fetches and plays a URL is a capability that
/// plays whatever anyone can get into a payload, unless someone says where audio may come
/// from.</item>
/// <item><b>How big it is.</b> At most 2 MiB, declared in the payload and enforced while
/// reading, so a server that keeps sending cannot make this process keep buffering.</item>
/// <item><b>That it is the audio that was described.</b> The SHA-256 in the payload must match
/// the bytes that arrived. A mismatch is a <c>security_scope_error</c>, not a retry: the content
/// at that URL is not the content Cloud Core said it was, and playing it anyway would make the
/// hash decoration.</item>
/// <item><b>How long and how loud.</b> At most 20 s, trimmed rather than refused, and the level
/// scales the SAMPLES (<see cref="WavAudio.Scale"/>). The Windows mixer is never touched — a
/// greeting that raised the system volume would leave every other application loud afterwards,
/// which is exactly the complaint the wake alarm was designed to avoid.</item>
/// </list>
///
/// <para>It blocks until the audio has finished, inside the desktop family's 60 s command cap,
/// so a caller that sequences "ring, then greet, then speak" gets an honest completion rather
/// than an immediate success and a sound that starts later.</para>
/// </summary>
public sealed class GreetingPlayer
{
    /// <summary>The payload bound. 2 MiB of PCM16 is about 20 s of 24 kHz mono with room to spare.</summary>
    public const int MaxAudioBytes = 2 * 1024 * 1024;

    public const double DefaultLevel = 0.75;

    /// <summary>Longest greeting this capability will play; anything longer is trimmed to it.</summary>
    public const int MaxSeconds = 20;

    /// <summary>The only container this capability accepts.</summary>
    public const string SupportedFormat = "wav";

    private static readonly TimeSpan DefaultFetchTimeout = TimeSpan.FromSeconds(10);

    /// <summary>Headroom over the audio's own duration before a stuck endpoint is called stuck.</summary>
    private static readonly TimeSpan PlaybackGrace = TimeSpan.FromSeconds(5);

    private readonly IAudioDeviceFactory _devices;
    private readonly Func<string?> _resolveRenderDeviceId;
    private readonly HttpClient _http;
    private readonly ILogger _logger;
    private readonly AuditLog? _audit;
    private readonly TimeSpan _fetchTimeout;

    public GreetingPlayer(
        IAudioDeviceFactory devices,
        Func<string?> resolveRenderDeviceId,
        HttpClient http,
        ILogger logger,
        AuditLog? audit = null,
        TimeSpan? fetchTimeout = null)
    {
        _devices = devices;
        _resolveRenderDeviceId = resolveRenderDeviceId;
        _http = http;
        _logger = logger;
        _audit = audit;
        _fetchTimeout = fetchTimeout ?? DefaultFetchTimeout;
    }

    /// <summary>
    /// Payload: <c>{"audio_id": "&lt;id&gt;", "audio": {"url": "&lt;https://broker/…&gt;",
    /// "sha256": "&lt;64 hex&gt;", "bytes": &lt;int ≤ 2 MiB&gt;, "format": "wav"},
    /// "level": &lt;0..1&gt;?, "max_seconds": &lt;int ≤ 20&gt;?}</c>.
    /// Result: <c>{"played": true, "audio_id": "…", "duration_ms": &lt;int&gt;, "level": &lt;f&gt;}</c>.
    /// </summary>
    public async Task<JsonObject> PlayAsync(JsonObject payload, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(payload);

        var audioId = payload["audio_id"]?.GetValue<string>();
        if (string.IsNullOrWhiteSpace(audioId))
        {
            throw Invalid("payload.audio_id is required");
        }

        if (payload["audio"] is not JsonObject audio)
        {
            throw Invalid("payload.audio must be an object with url, sha256, bytes and format");
        }

        var url = ParseUrl(audio["url"]);
        var sha256 = ParseSha256(audio["sha256"]);
        var declaredBytes = ParseDeclaredBytes(audio["bytes"]);
        var format = audio["format"]?.GetValue<string>();
        if (!string.Equals(format, SupportedFormat, StringComparison.OrdinalIgnoreCase))
        {
            throw Invalid($"payload.audio.format must be \"{SupportedFormat}\"");
        }

        var level = ParseLevel(payload["level"]);
        var maxSeconds = ParseMaxSeconds(payload["max_seconds"]);

        var bytes = await FetchAsync(url, declaredBytes, cancellationToken).ConfigureAwait(false);
        VerifyDigest(audioId, url, bytes, sha256);

        var decoded = WavAudio.Parse(bytes).Truncate(maxSeconds * 1000);
        var durationMs = (int)Math.Round(decoded.DurationMs);
        var samples = WavAudio.Scale(decoded.Pcm16, level);

        var deviceId = _resolveRenderDeviceId();
        if (string.IsNullOrWhiteSpace(deviceId))
        {
            throw new CapabilityException(
                ErrorClasses.DependencyUnavailable,
                "no audio render device is available in the owner's session, so no greeting can play",
                retryable: true);
        }

        await RenderAsync(deviceId, decoded.Format, samples, durationMs, cancellationToken).ConfigureAwait(false);

        _logger.LogInformation(
            "played greeting {AudioId}: {DurationMs} ms at level {Level:0.##} on {Device}",
            audioId,
            durationMs,
            level,
            deviceId);
        _audit?.Write(
            "play_audio",
            capability: AgentCapabilities.DesktopPlayAudio,
            status: AckStatus.Succeeded,
            detail: $"audio_id={audioId}; duration_ms={durationMs}; level={level.ToString("0.###", CultureInfo.InvariantCulture)}; "
                + $"bytes={bytes.Length}; origin={url.GetLeftPart(UriPartial.Authority)}");

        return new JsonObject
        {
            ["played"] = true,
            ["audio_id"] = audioId,
            ["duration_ms"] = durationMs,
            ["level"] = level,
        };
    }

    // ---------------------------------------------------------------- internals

    private async Task<byte[]> FetchAsync(Uri url, int declaredBytes, CancellationToken cancellationToken)
    {
        using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeoutCts.CancelAfter(_fetchTimeout);

        HttpResponseMessage response;
        try
        {
            response = await _http
                .GetAsync(url, HttpCompletionOption.ResponseHeadersRead, timeoutCts.Token)
                .ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            throw new CapabilityException(
                ErrorClasses.Timeout,
                $"the greeting audio did not arrive within {_fetchTimeout.TotalSeconds:F0} s",
                retryable: true);
        }
        catch (Exception ex)
        {
            throw new CapabilityException(
                ErrorClasses.DependencyUnavailable,
                $"the greeting audio could not be fetched: {ex.Message}",
                retryable: true);
        }

        using (response)
        {
            if (!response.IsSuccessStatusCode)
            {
                throw new CapabilityException(
                    ErrorClasses.DependencyUnavailable,
                    $"the greeting audio URL answered HTTP {(int)response.StatusCode}",
                    retryable: true);
            }

            if (response.Content.Headers.ContentLength is long declared && declared > declaredBytes)
            {
                throw Invalid(
                    $"the greeting audio is {declared} bytes but the payload declared {declaredBytes}");
            }

            byte[] body;
            try
            {
                await using var stream = await response.Content.ReadAsStreamAsync(timeoutCts.Token).ConfigureAwait(false);
                body = await ReadBoundedAsync(stream, declaredBytes, timeoutCts.Token).ConfigureAwait(false);
            }
            catch (CapabilityException)
            {
                throw;
            }
            catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
            {
                throw new CapabilityException(
                    ErrorClasses.Timeout,
                    $"the greeting audio did not finish downloading within {_fetchTimeout.TotalSeconds:F0} s",
                    retryable: true);
            }
            catch (Exception ex)
            {
                throw new CapabilityException(
                    ErrorClasses.DependencyUnavailable,
                    $"the greeting audio could not be read: {ex.Message}",
                    retryable: true);
            }

            if (body.Length != declaredBytes)
            {
                throw Invalid(
                    $"the greeting audio is {body.Length} bytes but the payload declared {declaredBytes}");
            }

            return body;
        }
    }

    /// <summary>
    /// Reads at most <paramref name="limit"/> bytes and refuses the moment one more arrives. The
    /// bound is enforced while reading, not after: a Content-Length header is a claim, and this
    /// process must not buffer a gigabyte to find out the claim was false.
    /// </summary>
    private static async Task<byte[]> ReadBoundedAsync(Stream stream, int limit, CancellationToken cancellationToken)
    {
        var buffer = new byte[64 * 1024];
        using var accumulated = new MemoryStream(Math.Min(limit, 256 * 1024));
        while (true)
        {
            var read = await stream.ReadAsync(buffer, cancellationToken).ConfigureAwait(false);
            if (read == 0)
            {
                return accumulated.ToArray();
            }

            if (accumulated.Length + read > limit)
            {
                throw Invalid($"the greeting audio exceeds the declared {limit} bytes");
            }

            accumulated.Write(buffer, 0, read);
        }
    }

    private void VerifyDigest(string audioId, Uri url, byte[] bytes, string expected)
    {
        var actual = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        if (string.Equals(actual, expected, StringComparison.Ordinal))
        {
            return;
        }

        _audit?.Write(
            "play_audio",
            capability: AgentCapabilities.DesktopPlayAudio,
            status: AckStatus.Failed,
            detail: $"audio_id={audioId}; refused=sha256_mismatch; bytes={bytes.Length}; "
                + $"origin={url.GetLeftPart(UriPartial.Authority)}");

        // security_scope_error, not a retry: what is at that URL is not what Cloud Core
        // described, and fetching it again would not change that.
        throw new CapabilityException(
            ErrorClasses.SecurityScopeError,
            "the greeting audio does not match the sha256 in the payload; it was not played",
            retryable: false);
    }

    private async Task RenderAsync(
        string deviceId,
        AudioFormat format,
        byte[] samples,
        int durationMs,
        CancellationToken cancellationToken)
    {
        IAudioPlayback playback;
        try
        {
            // Opened at the WAV's OWN rate and channel count. The shared-mode render path
            // converts to the endpoint's mix format itself, which is a conversion written by
            // people who do that for a living; a resampler in this file would be one more piece
            // of audio code to be wrong about at 06:30.
            playback = _devices.OpenPlayback(deviceId, format);
        }
        catch (Exception ex)
        {
            throw new CapabilityException(
                ErrorClasses.DependencyUnavailable,
                $"could not open the render endpoint for the greeting: {ex.Message}",
                retryable: true);
        }

        try
        {
            var finished = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            void OnDrained() => finished.TrySetResult();

            playback.Drained += OnDrained;
            try
            {
                playback.Start();
                playback.Enqueue(samples);

                if (playback.QueuedMs > 0)
                {
                    var budget = TimeSpan.FromMilliseconds(durationMs) + PlaybackGrace;
                    try
                    {
                        await finished.Task.WaitAsync(budget, cancellationToken).ConfigureAwait(false);
                    }
                    catch (TimeoutException)
                    {
                        _logger.LogWarning(
                            "greeting playback did not drain within {Budget} ms; stopping the endpoint",
                            (int)budget.TotalMilliseconds);
                    }
                }
            }
            finally
            {
                playback.Drained -= OnDrained;
            }
        }
        finally
        {
            try
            {
                playback.StopImmediately();
            }
            catch (Exception ex)
            {
                _logger.LogDebug("greeting endpoint stop: {Reason}", ex.Message);
            }

            playback.Dispose();
        }
    }

    private static Uri ParseUrl(JsonNode? node)
    {
        var raw = node?.GetValueKind() == JsonValueKind.String ? node.GetValue<string>() : null;
        if (string.IsNullOrWhiteSpace(raw)
            || !Uri.TryCreate(raw, UriKind.Absolute, out var url)
            || (url.Scheme != Uri.UriSchemeHttp && url.Scheme != Uri.UriSchemeHttps))
        {
            throw Invalid("payload.audio.url must be an absolute http(s) URL");
        }

        return url;
    }

    private static string ParseSha256(JsonNode? node)
    {
        var raw = node?.GetValueKind() == JsonValueKind.String ? node.GetValue<string>() : null;
        if (raw is null || raw.Length != 64 || !raw.All(Uri.IsHexDigit))
        {
            throw Invalid("payload.audio.sha256 must be 64 hexadecimal characters");
        }

        return raw.ToLowerInvariant();
    }

    private static int ParseDeclaredBytes(JsonNode? node)
    {
        if (node?.GetValueKind() != JsonValueKind.Number
            || !int.TryParse(node.ToJsonString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var value))
        {
            throw Invalid("payload.audio.bytes must be a whole number of bytes");
        }

        if (value is < 1 or > MaxAudioBytes)
        {
            throw Invalid($"payload.audio.bytes must be between 1 and {MaxAudioBytes}");
        }

        return value;
    }

    private static double ParseLevel(JsonNode? node)
    {
        if (node is null)
        {
            return DefaultLevel;
        }

        if (node.GetValueKind() != JsonValueKind.Number
            || !double.TryParse(node.ToJsonString(), NumberStyles.Float, CultureInfo.InvariantCulture, out var value))
        {
            throw Invalid("payload.level must be a number between 0 and 1");
        }

        return Math.Clamp(value, 0.0, 1.0);
    }

    private static int ParseMaxSeconds(JsonNode? node)
    {
        if (node is null)
        {
            return MaxSeconds;
        }

        if (node.GetValueKind() != JsonValueKind.Number
            || !double.TryParse(node.ToJsonString(), NumberStyles.Float, CultureInfo.InvariantCulture, out var value)
            || value != Math.Floor(value))
        {
            throw Invalid("payload.max_seconds must be a whole number of seconds");
        }

        return (int)Math.Clamp(value, 1, MaxSeconds);
    }

    private static CapabilityException Invalid(string message)
        => new(ErrorClasses.ValidationError, message, retryable: false);
}
