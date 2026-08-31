using System.Net.Http.Json;
using System.Text.Json.Nodes;

namespace PagentOS.Agent.Core.Enrollment;

public sealed class EnrollmentException(string message) : Exception(message);

/// <summary>One-time enrollment REST client (DEVICE_PROTOCOL.md §2).</summary>
public sealed class EnrollmentClient(HttpClient httpClient)
{
    /// <summary>Calls POST {restBase}/v1/devices/enroll and returns the assigned device_id.</summary>
    public async Task<string> EnrollAsync(
        Uri restBase,
        string token,
        string name,
        string publicKeySpkiBase64,
        IReadOnlyList<string> capabilities,
        CancellationToken cancellationToken = default)
    {
        var endpoint = new Uri(restBase, "/v1/devices/enroll");
        var body = new JsonObject
        {
            ["token"] = token,
            ["name"] = name,
            ["platform"] = Protocol.AgentInfo.Platform,
            ["public_key_spki_b64"] = publicKeySpkiBase64,
            ["capabilities"] = new JsonArray([.. capabilities.Select(c => (JsonNode)c)]),
        };

        using var response = await httpClient.PostAsJsonAsync(endpoint, body, cancellationToken).ConfigureAwait(false);
        var responseText = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            throw new EnrollmentException($"enrollment failed with HTTP {(int)response.StatusCode}: {Truncate(responseText)}");
        }

        JsonNode? node;
        try
        {
            node = JsonNode.Parse(responseText);
        }
        catch (Exception ex)
        {
            throw new EnrollmentException($"enrollment response is not valid JSON: {ex.Message}");
        }

        var deviceId = node?["device_id"]?.GetValue<string>();
        if (string.IsNullOrEmpty(deviceId))
        {
            throw new EnrollmentException("enrollment response is missing device_id");
        }

        return deviceId;
    }

    private static string Truncate(string text) => text.Length <= 500 ? text : text[..500];
}
