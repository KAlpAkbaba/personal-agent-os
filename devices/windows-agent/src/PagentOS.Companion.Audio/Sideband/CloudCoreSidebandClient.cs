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
/// </summary>
public sealed class CloudCoreSidebandClient(HttpClient http, IOwnerSessionTokenSource tokens) : ISidebandClient
{
    private const string SessionsPath = "/v1/voice/realtime/sessions";

    public async Task<RealtimeSessionGrant> CreateSessionAsync(CreateSessionRequest request, CancellationToken cancellationToken)
    {
        var body = await PostAsync(SessionsPath, request.ToJson(), cancellationToken).ConfigureAwait(false);
        return RealtimeSessionGrant.Parse(body);
    }

    public async Task<ToolCallRelayResult> RelayToolCallAsync(string sessionId, string callId, string name, string argumentsJson, CancellationToken cancellationToken)
    {
        JsonNode? arguments;
        try
        {
            arguments = JsonNode.Parse(string.IsNullOrWhiteSpace(argumentsJson) ? "{}" : argumentsJson);
        }
        catch (System.Text.Json.JsonException)
        {
            arguments = argumentsJson;
        }

        var payload = new JsonObject
        {
            ["call_id"] = callId,
            ["name"] = name,
            ["arguments"] = arguments,
        };
        var body = await PostAsync($"{SessionsPath}/{Uri.EscapeDataString(sessionId)}/tool-calls", payload, cancellationToken).ConfigureAwait(false);
        return ToolCallRelayResult.Parse(body, callId);
    }

    public async Task ReportEventAsync(string sessionId, VoiceClientEventRecord record, CancellationToken cancellationToken)
    {
        await PostAsync($"{SessionsPath}/{Uri.EscapeDataString(sessionId)}/events", record.ToJson(), cancellationToken).ConfigureAwait(false);
    }

    public async Task<RealtimeSessionGrant?> AttachAsync(string sessionId, CancellationToken cancellationToken)
    {
        try
        {
            var body = await PostAsync(
                $"{SessionsPath}/{Uri.EscapeDataString(sessionId)}/attach",
                new JsonObject { ["client_kind"] = "windows-companion" },
                cancellationToken).ConfigureAwait(false);
            return RealtimeSessionGrant.Parse(body);
        }
        catch (SidebandException ex) when (ex.StatusCode is (int)HttpStatusCode.NotFound or (int)HttpStatusCode.Gone)
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

        return JsonNode.Parse(text) as JsonObject ?? throw new SidebandException((int)response.StatusCode, path, "response was not a JSON object");
    }
}
