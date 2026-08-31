using System.Collections.Concurrent;
using System.IO.Pipes;
using System.Security.Principal;
using System.Text;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Ipc;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.DeviceService;

/// <summary>
/// Named-pipe server owned by the Device Service (Session 0 capable). The Session Companion
/// connects from the interactive owner session; interactive capabilities are forwarded to it.
/// The pipe ACL is restricted to the current user. When no companion is connected, interactive
/// commands fail fast with dependency_unavailable (retryable) per DEVICE_PROTOCOL.md §9.
/// </summary>
public sealed class CompanionPipeServer : BackgroundService
{
    private readonly string _pipeName;
    private readonly ILogger<CompanionPipeServer> _logger;
    private readonly ConcurrentDictionary<string, TaskCompletionSource<ExecResponse>> _pending = new(StringComparer.Ordinal);
    private volatile CompanionConnection? _connection;

    public CompanionPipeServer(string pipeName, ILogger<CompanionPipeServer> logger)
    {
        _pipeName = pipeName;
        _logger = logger;
    }

    public bool CompanionConnected => _connection is not null;

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        _logger.LogInformation("companion pipe server listening on \\\\.\\pipe\\{PipeName}", _pipeName);
        while (!stoppingToken.IsCancellationRequested)
        {
            NamedPipeServerStream server;
            try
            {
                server = CreateServerStream();
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "failed to create companion pipe; retrying in 5 s");
                try
                {
                    await Task.Delay(TimeSpan.FromSeconds(5), stoppingToken).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    return;
                }

                continue;
            }

            try
            {
                await server.WaitForConnectionAsync(stoppingToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                await server.DisposeAsync().ConfigureAwait(false);
                return;
            }
            catch (Exception ex)
            {
                _logger.LogWarning("pipe wait failed: {Reason}", ex.Message);
                await server.DisposeAsync().ConfigureAwait(false);
                continue;
            }

            var connection = new CompanionConnection(server);
            try
            {
                var helloLine = await connection.Reader.ReadLineAsync(stoppingToken).ConfigureAwait(false);
                if (helloLine is null || PipeJson.Deserialize(helloLine) is not CompanionHello hello)
                {
                    _logger.LogWarning("companion connection did not start with companion_hello; dropping");
                    await connection.DisposeAsync().ConfigureAwait(false);
                    continue;
                }

                _connection = connection;
                _logger.LogInformation("session companion connected (capabilities: {Capabilities})", string.Join(",", hello.Capabilities));
                await ReadLoopAsync(connection, stoppingToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                // Shutdown.
            }
            catch (Exception ex)
            {
                _logger.LogWarning("companion connection ended: {Reason}", ex.Message);
            }
            finally
            {
                _connection = null;
                FailAllPending("session companion disconnected");
                await connection.DisposeAsync().ConfigureAwait(false);
                _logger.LogInformation("session companion disconnected");
            }
        }
    }

    /// <summary>Forwards an interactive capability to the connected companion.</summary>
    public async Task<JsonObject?> ExecuteCapabilityAsync(
        string capability,
        JsonObject payload,
        TimeSpan timeout,
        CancellationToken cancellationToken)
    {
        var connection = _connection;
        if (connection is null)
        {
            throw new CapabilityException(
                ErrorClasses.DependencyUnavailable,
                "no session companion is connected; interactive capabilities are unavailable",
                retryable: true);
        }

        var requestId = Guid.NewGuid().ToString();
        var tcs = new TaskCompletionSource<ExecResponse>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[requestId] = tcs;
        try
        {
            var request = new ExecRequest
            {
                RequestId = requestId,
                Capability = capability,
                Payload = (JsonObject)payload.DeepClone(),
                TimeoutMs = (int)timeout.TotalMilliseconds,
            };
            await connection.WriteLineAsync(PipeJson.Serialize(request), cancellationToken).ConfigureAwait(false);

            using var timeoutCts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeoutCts.CancelAfter(timeout);
            await using var registration = timeoutCts.Token.Register(() => tcs.TrySetCanceled(timeoutCts.Token)).ConfigureAwait(false);

            ExecResponse response;
            try
            {
                response = await tcs.Task.ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
            {
                throw new CapabilityException(
                    ErrorClasses.Timeout,
                    $"session companion did not answer within {timeout.TotalSeconds:F0} s",
                    retryable: true);
            }

            if (!response.Ok)
            {
                var error = response.Error ?? ErrorObjects.Create(ErrorClasses.InternalBug, "companion reported failure without error detail", retryable: false);
                throw new CapabilityException(error.Class, error.Message, error.Retryable ?? false);
            }

            return response.Result;
        }
        finally
        {
            _pending.TryRemove(requestId, out _);
        }
    }

    private async Task ReadLoopAsync(CompanionConnection connection, CancellationToken cancellationToken)
    {
        while (true)
        {
            var line = await connection.Reader.ReadLineAsync(cancellationToken).ConfigureAwait(false);
            if (line is null)
            {
                return;
            }

            PipeMessage message;
            try
            {
                message = PipeJson.Deserialize(line);
            }
            catch (Exception ex)
            {
                _logger.LogWarning("malformed pipe frame from companion: {Reason}", ex.Message);
                continue;
            }

            if (message is ExecResponse response && _pending.TryGetValue(response.RequestId, out var tcs))
            {
                tcs.TrySetResult(response);
            }
        }
    }

    private void FailAllPending(string reason)
    {
        foreach (var pair in _pending)
        {
            pair.Value.TrySetException(new CapabilityException(ErrorClasses.DependencyUnavailable, reason, retryable: true));
        }
    }

    private NamedPipeServerStream CreateServerStream()
    {
        var security = new PipeSecurity();
        var user = WindowsIdentity.GetCurrent().User
                   ?? throw new InvalidOperationException("cannot resolve current user SID for pipe ACL");
        security.AddAccessRule(new PipeAccessRule(user, PipeAccessRights.FullControl, System.Security.AccessControl.AccessControlType.Allow));
        return NamedPipeServerStreamAcl.Create(
            _pipeName,
            PipeDirection.InOut,
            maxNumberOfServerInstances: 1,
            PipeTransmissionMode.Byte,
            PipeOptions.Asynchronous,
            inBufferSize: 0,
            outBufferSize: 0,
            security);
    }

    private sealed class CompanionConnection : IAsyncDisposable
    {
        private readonly NamedPipeServerStream _stream;
        private readonly StreamWriter _writer;
        private readonly SemaphoreSlim _writeLock = new(1, 1);

        public CompanionConnection(NamedPipeServerStream stream)
        {
            _stream = stream;
            Reader = new StreamReader(stream, new UTF8Encoding(false), detectEncodingFromByteOrderMarks: false, leaveOpen: true);
            _writer = new StreamWriter(stream, new UTF8Encoding(false), leaveOpen: true) { AutoFlush = true };
        }

        public StreamReader Reader { get; }

        public async Task WriteLineAsync(string line, CancellationToken cancellationToken)
        {
            await _writeLock.WaitAsync(cancellationToken).ConfigureAwait(false);
            try
            {
                await _writer.WriteLineAsync(line.AsMemory(), cancellationToken).ConfigureAwait(false);
            }
            finally
            {
                _writeLock.Release();
            }
        }

        public async ValueTask DisposeAsync()
        {
            _writeLock.Dispose();
            Reader.Dispose();
            await _writer.DisposeAsync().ConfigureAwait(false);
            await _stream.DisposeAsync().ConfigureAwait(false);
        }
    }
}
