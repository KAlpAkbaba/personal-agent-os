using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json.Nodes;

namespace PagentOS.Companion.Audio.Sideband;

/// <summary>
/// HTTP implementation of the sideband against Cloud Core's owner-session API. The token is
/// read from the source on every request so a rotation lands without a restart, and it is
/// attached as <c>Authorization: Bearer</c> exactly as every other owner client does. The
/// provider credential never travels here; Cloud Core hands it out, the media leg uses it.
///
/// Status handling is the server's (routes.py <c>_raise_http</c>): 409 = this owner session
/// no longer holds the media leg; 410 = closed/expired; 404 = unknown; 422 = the payload was
/// refused (a client bug). The exceptions carry that classification; callers decide.
/// </summary>
public sealed class CloudCoreSidebandClient(HttpClient http, IOwnerSessionTokenSource tokens) : ISidebandClient
{
    public async Task<RealtimeSessionGrant> CreateSessionAsync(CreateSessionRequest request, CancellationToken cancellationToken)
    {
        var body = await PostAsync(RealtimeContract.SessionsPath, request.ToJson(), cancellationToken).ConfigureAwait(false);
        return RealtimeSessionGrant.Parse(body);
    }

    public async Task<ToolCallRelayResult> RelayToolCallAsync(string sessionId, string callId, string name, string argumentsJson, CancellationToken cancellationToken)
    {
        // The server takes `arguments` as an object; a provider that hands us anything else
        // is wrapped rather than refused, so the tool sees what the model actually said.
        JsonNode? parsed = null;
        try
        {
            parsed = JsonNode.Parse(string.IsNullOrWhiteSpace(argumentsJson) ? "{}" : argumentsJson);
        }
        catch (System.Text.Json.JsonException)
        {
            // fall through: wrapped below
        }

        var arguments = parsed as JsonObject ?? new JsonObject { ["raw_arguments"] = argumentsJson };
        var payload = new JsonObject
        {
            ["call_id"] = callId,
            ["name"] = name,
            ["arguments"] = arguments,
        };
        var body = await PostAsync($"{RealtimeContract.SessionsPath}/{Uri.EscapeDataString(sessionId)}/tool-calls", payload, cancellationToken).ConfigureAwait(false);
        return ToolCallRelayResult.Parse(body, callId);
    }

    public async Task<EventsAck> ReportEventsAsync(string sessionId, IReadOnlyList<VoiceClientEventRecord> events, CancellationToken cancellationToken)
    {
        if (events.Count is 0 or > RealtimeContract.MaxEventsPerRequest)
        {
            throw new ArgumentException($"an events batch carries 1..{RealtimeContract.MaxEventsPerRequest} events, not {events.Count}", nameof(events));
        }

        var body = await PostAsync(
            $"{RealtimeContract.SessionsPath}/{Uri.EscapeDataString(sessionId)}/events",
            VoiceClientEventRecord.BatchJson(events),
            cancellationToken).ConfigureAwait(false);
        return EventsAck.Parse(body);
    }

    public async Task<AttachResult?> AttachAsync(string sessionId, string clientKind, string? transport, CancellationToken cancellationToken)
    {
        var request = new JsonObject { ["client_kind"] = clientKind };
        if (transport is not null)
        {
            request["transport"] = transport;
        }

        try
        {
            var body = await PostAsync(
                $"{RealtimeContract.SessionsPath}/{Uri.EscapeDataString(sessionId)}/attach",
                request,
                cancellationToken).ConfigureAwait(false);
            return AttachResult.Parse(body);
        }
        catch (SidebandException ex) when (ex.IsSessionGone)
        {
            return null;
        }
    }

    private async Task<JsonObject> PostAsync(string path, JsonObject payload, CancellationToken cancellationToken)
    {
        var token = tokens.GetToken()
            ?? throw new InvalidOperationException("no owner session token available; the companion cannot open the sideband");

        using var request = new HttpRequestMessage(HttpMethod.Post, path);
        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
        request.Content = new StringContent(payload.ToJsonString(), Encoding.UTF8, "application/json");

        using var response = await http.SendAsync(request, cancellationToken).ConfigureAwait(false);
        var text = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            throw new SidebandException((int)response.StatusCode, path, text);
        }

        if (string.IsNullOrWhiteSpace(text))
        {
            return new JsonObject();
        }

        return JsonNode.Parse(text) as JsonObject ?? throw new SidebandException((int)HttpStatusCode.OK, path, "response was not a JSON object");
    }
}
