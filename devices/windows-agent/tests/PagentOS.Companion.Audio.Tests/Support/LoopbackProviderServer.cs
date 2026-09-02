using System.Net;
using System.Net.Sockets;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json.Nodes;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging;

namespace PagentOS.Companion.Audio.Tests.Support;

/// <summary>
/// A loopback stand-in for a realtime provider's WebSocket endpoint, speaking just enough of
/// the OpenAI event vocabulary to exercise the real <c>WebSocketMediaLeg</c>: it answers
/// session.update with session.created, counts appended audio, answers a commit with a
/// response (created, one audio delta, done), and answers response.cancel with a cancelled
/// response.done. It records the Authorization header it was handed.
/// </summary>
public sealed class LoopbackProviderServer : IAsyncDisposable
{
    private readonly WebApplication _app;
    private WebSocket? _socket;

    public int Port { get; }

    public string? AuthorizationHeader { get; private set; }

    public int AudioAppends { get; private set; }

    public int Commits { get; private set; }

    public int Cancels { get; private set; }

    public JsonObject? SessionUpdate { get; private set; }

    private LoopbackProviderServer(int port)
    {
        Port = port;
        var builder = WebApplication.CreateBuilder();
        builder.Logging.ClearProviders();
        builder.WebHost.UseUrls($"http://127.0.0.1:{port}");
        _app = builder.Build();
        _app.UseWebSockets();
        _app.Map("/realtime", HandleAsync);
    }

    public Uri Uri => new($"ws://127.0.0.1:{Port}/realtime");

    public static async Task<LoopbackProviderServer> StartAsync()
    {
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        var port = ((IPEndPoint)listener.LocalEndpoint).Port;
        listener.Stop();
        var server = new LoopbackProviderServer(port);
        await server._app.StartAsync();
        return server;
    }

    /// <summary>Closes the provider side, as an outage would.</summary>
    public async Task DropClientAsync()
    {
        var socket = _socket;
        if (socket is { State: WebSocketState.Open })
        {
            await socket.CloseOutputAsync(WebSocketCloseStatus.EndpointUnavailable, "provider going away", CancellationToken.None);
        }
    }

    private async Task HandleAsync(HttpContext context)
    {
        if (!context.WebSockets.IsWebSocketRequest)
        {
            context.Response.StatusCode = 400;
            return;
        }

        AuthorizationHeader = context.Request.Headers.Authorization.ToString();
        using var socket = await context.WebSockets.AcceptWebSocketAsync();
        _socket = socket;
        var buffer = new byte[256 * 1024];
        try
        {
            while (socket.State == WebSocketState.Open)
            {
                var message = new MemoryStream();
                WebSocketReceiveResult result;
                do
                {
                    result = await socket.ReceiveAsync(buffer, context.RequestAborted);
                    if (result.MessageType == WebSocketMessageType.Close)
                    {
                        await socket.CloseAsync(WebSocketCloseStatus.NormalClosure, "bye", CancellationToken.None);
                        return;
                    }

                    message.Write(buffer, 0, result.Count);
                }
                while (!result.EndOfMessage);

                var node = JsonNode.Parse(Encoding.UTF8.GetString(message.ToArray()))!.AsObject();
                switch (node["type"]!.GetValue<string>())
                {
                    case "session.update":
                        SessionUpdate = node;
                        await SendAsync(socket, new JsonObject { ["type"] = "session.created", ["session"] = new JsonObject { ["id"] = "sess_loopback" } });
                        break;
                    case "input_audio_buffer.append":
                        AudioAppends++;
                        break;
                    case "input_audio_buffer.commit":
                        Commits++;
                        break;
                    case "response.create":
                        await SendAsync(socket, new JsonObject { ["type"] = "response.created", ["response"] = new JsonObject { ["id"] = "resp_" + Commits } });
                        await SendAsync(socket, new JsonObject
                        {
                            ["type"] = "response.audio.delta",
                            ["response_id"] = "resp_" + Commits,
                            ["delta"] = Convert.ToBase64String(new byte[960]),
                        });
                        await SendAsync(socket, new JsonObject { ["type"] = "response.done", ["response"] = new JsonObject { ["id"] = "resp_" + Commits, ["status"] = "completed" } });
                        break;
                    case "response.cancel":
                        Cancels++;
                        await SendAsync(socket, new JsonObject { ["type"] = "response.done", ["response"] = new JsonObject { ["id"] = "resp_" + Commits, ["status"] = "cancelled" } });
                        break;
                }
            }
        }
        catch (Exception) when (context.RequestAborted.IsCancellationRequested || socket.State != WebSocketState.Open)
        {
        }
    }

    private static Task SendAsync(WebSocket socket, JsonObject node)
        => socket.SendAsync(Encoding.UTF8.GetBytes(node.ToJsonString()), WebSocketMessageType.Text, true, CancellationToken.None);

    public async ValueTask DisposeAsync()
    {
        await _app.StopAsync();
        await _app.DisposeAsync();
    }
}
