namespace PagentOS.Agent.Core.Protocol;

/// <summary>
/// The one notion of "origin" both halves of the agent compare URLs by: scheme, host and
/// port of an absolute http(s) URL, as <see cref="Uri.GetLeftPart(UriPartial)"/> renders the
/// authority (a default port is dropped on both sides, so <c>https://core:443</c> and
/// <c>https://core</c> are one origin; the same host on another port is another server).
/// The Device Service derives it from the broker REST base it enrolled against and dials
/// (<c>desktop.play_audio</c>, M18.3); since M22 it hands the same string to the Session
/// Companion in the pipe challenge so <c>file.fetch</c> can be pinned to it on both sides of
/// the pipe.
/// </summary>
public static class HttpOrigin
{
    /// <summary>Scheme, host and port of an absolute http(s) URL, or null when the URL is not one.</summary>
    public static string? Of(string? url)
        => Uri.TryCreate(url, UriKind.Absolute, out var parsed) ? Of(parsed) : null;

    /// <summary>Scheme, host and port of an absolute http(s) URI, or null for any other scheme.</summary>
    public static string? Of(Uri? url)
        => url is not null && url.IsAbsoluteUri && (url.Scheme == Uri.UriSchemeHttp || url.Scheme == Uri.UriSchemeHttps)
            ? url.GetLeftPart(UriPartial.Authority)
            : null;

    /// <summary>True when both are origins and equal (host case-insensitively).</summary>
    public static bool Same(string? a, string? b)
        => !string.IsNullOrWhiteSpace(a) && !string.IsNullOrWhiteSpace(b) && string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
}
