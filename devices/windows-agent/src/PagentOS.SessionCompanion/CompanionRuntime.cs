using System.IO.Pipes;
using System.Text;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Connection;
using PagentOS.Agent.Core.Ipc;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion;

/// <summary>
/// Session Companion loop: connects to the Device Service named pipe from the interactive
/// session, announces its capabilities and serves exec requests. Reconnects with exponential
/// backoff (full jitter) whenever the service restarts or the pipe breaks.
/// </summary>
public sealed class CompanionRuntime(
    string pipeName,
    AppLauncher launcher,
    ArtifactOpener artifactOpener,
    ILogger logger,
    BackoffPolicy? backoff = null)
{
    private const int ConnectTimeoutMs = 2000;

    private readonly BackoffPolicy _backoff = backoff ?? new BackoffPolicy(baseSeconds: 1.0, maxSeconds: 30.0);

    public async Task RunAsync(CancellationToken cancellationToken)
    {
        var attempt = 0;
        while (!cancellationToken.IsCancellationRequested)
        {
            try
            {
                await ServeOneConnectionAsync(() => attempt = 0, cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                break;
            }
            catch (TimeoutException)
            {
                // Service not up yet; retry quietly.
            }
            catch (Exception ex)
            {
                logger.LogWarning("companion pipe connection ended: {Reason}", ex.Message);
            }

            if (cancellationToken.IsCancellationRequested)
            {
                break;
            }

            var delay = _backoff.NextDelay(attempt);
            attempt = Math.Min(attempt + 1, 20);
            try
            {
                await Task.Delay(delay, cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                break;
            }
        }
    }

    private async Task ServeOneConnectionAsync(Action onConnected, CancellationToken cancellationToken)
    {
        await using var client = new NamedPipeClientStream(".", pipeName, PipeDirection.InOut, PipeOptions.Asynchronous);
        await client.ConnectAsync(ConnectTimeoutMs, cancellationToken).ConfigureAwait(false);
        onConnected();
        logger.LogInformation("connected to device service pipe \\\\.\\pipe\\{PipeName}", pipeName);

        using var reader = new StreamReader(client, new UTF8Encoding(false), detectEncodingFromByteOrderMarks: false, leaveOpen: true);
        await using var writer = new StreamWriter(client, new UTF8Encoding(false), leaveOpen: true) { AutoFlush = true };

        var hello = new CompanionHello { Capabilities = AgentCapabilities.All };
        await writer.WriteLineAsync(PipeJson.Serialize(hello).AsMemory(), cancellationToken).ConfigureAwait(false);

        while (true)
        {
            var line = await reader.ReadLineAsync(cancellationToken).ConfigureAwait(false);
            if (line is null)
            {
                logger.LogInformation("device service closed the pipe");
                return;
            }

            PipeMessage message;
            try
            {
                message = PipeJson.Deserialize(line);
            }
            catch (Exception ex)
            {
                logger.LogWarning("malformed pipe frame from service: {Reason}", ex.Message);
                continue;
            }

            if (message is not ExecRequest request)
            {
                continue;
            }

            var response = Execute(request);
            await writer.WriteLineAsync(PipeJson.Serialize(response).AsMemory(), cancellationToken).ConfigureAwait(false);
        }
    }

    private ExecResponse Execute(ExecRequest request)
    {
        try
        {
            JsonObject result;
            switch (request.Capability)
            {
                case AgentCapabilities.DesktopOpenApplication:
                    result = launcher.Launch(request.Payload);
                    logger.LogInformation(
                        "executed {Capability}: pid={Pid}",
                        request.Capability,
                        result["pid"]?.GetValue<int>());
                    break;

                case AgentCapabilities.DesktopOpenArtifact:
                    result = artifactOpener.Open(request.Payload);
                    logger.LogInformation(
                        "executed {Capability}: opened={Opened} path={Path}",
                        request.Capability,
                        result["opened"]?.GetValue<bool>(),
                        result["path"]?.GetValue<string>());
                    break;

                default:
                    throw new CapabilityException(
                        ErrorClasses.CapabilityMissing,
                        $"capability '{request.Capability}' is not supported by the session companion",
                        retryable: false);
            }

            return new ExecResponse { RequestId = request.RequestId, Ok = true, Result = result };
        }
        catch (CapabilityException ex)
        {
            logger.LogWarning("capability {Capability} failed: {Class}: {Reason}", request.Capability, ex.ErrorClass, ex.Message);
            return new ExecResponse
            {
                RequestId = request.RequestId,
                Ok = false,
                Error = ErrorObjects.Create(ex.ErrorClass, ex.Message, ex.Retryable),
            };
        }
        catch (Exception ex)
        {
            logger.LogError(ex, "capability {Capability} crashed", request.Capability);
            return new ExecResponse
            {
                RequestId = request.RequestId,
                Ok = false,
                Error = ErrorObjects.Create(ErrorClasses.InternalBug, ex.Message, retryable: false),
            };
        }
    }
}
