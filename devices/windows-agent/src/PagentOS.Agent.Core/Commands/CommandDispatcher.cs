using System.Collections.Concurrent;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Audit;
using PagentOS.Agent.Core.Idempotency;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.Agent.Core.Commands;

/// <summary>
/// Executes broker commands with the DEVICE_PROTOCOL.md §5 rules: expiry rejection before
/// execution, effectively-once execution via the persistent idempotency store, monotonic acks
/// (accepted → running → succeeded|failed) and cancellation. The dispatcher outlives individual
/// WebSocket connections; the active connection attaches an ack sender.
/// </summary>
public sealed class CommandDispatcher(
    IdempotencyStore store,
    ICapabilityExecutor executor,
    AuditLog audit,
    ILogger<CommandDispatcher> logger,
    TimeProvider? timeProvider = null)
{
    private static readonly TimeSpan AckSendTimeout = TimeSpan.FromSeconds(10);

    private readonly TimeProvider _time = timeProvider ?? TimeProvider.System;
    private readonly ConcurrentDictionary<string, InFlightCommand> _inFlightByCommandId = new(StringComparer.OrdinalIgnoreCase);
    private readonly ConcurrentDictionary<string, string> _inFlightKeyToCommandId = new(StringComparer.Ordinal);
    private volatile Func<CommandAckMessage, CancellationToken, Task>? _sender;

    private sealed record InFlightCommand(CommandEnvelope Command, CancellationTokenSource Cancellation);

    public void AttachSender(Func<CommandAckMessage, CancellationToken, Task> sender) => _sender = sender;

    public void DetachSender() => _sender = null;

    public async Task HandleCommandAsync(CommandEnvelope command)
    {
        using var scope = logger.BeginScope(new Dictionary<string, object?> { ["trace_id"] = command.TraceId });
        audit.Write("command_received", commandId: command.CommandId, traceId: command.TraceId, capability: command.Capability);

        if (store.TryGetTerminalAck(command.IdempotencyKey, out var cached))
        {
            logger.LogInformation(
                "duplicate delivery of command {CommandId}; re-sending recorded terminal ack ({Status})",
                command.CommandId,
                cached!.Status);
            audit.Write("command_duplicate_reack", commandId: command.CommandId, traceId: command.TraceId, status: cached.Status);
            await SendAckSafeAsync(cached).ConfigureAwait(false);
            return;
        }

        if (!_inFlightKeyToCommandId.TryAdd(command.IdempotencyKey, command.CommandId))
        {
            logger.LogInformation(
                "duplicate delivery of command {CommandId} while still executing; re-acking running",
                command.CommandId);
            await SendAckSafeAsync(Ack(command.CommandId, AckStatus.Running)).ConfigureAwait(false);
            return;
        }

        var inFlight = new InFlightCommand(command, new CancellationTokenSource());
        _inFlightByCommandId[command.CommandId] = inFlight;
        try
        {
            if (_time.GetUtcNow() >= command.ExpiresAt)
            {
                var expired = Ack(
                    command.CommandId,
                    AckStatus.Failed,
                    error: ErrorObjects.Create(ErrorClasses.CommandExpired, $"command expired at {command.ExpiresAt:O}", retryable: false));
                await CompleteAsync(command, expired).ConfigureAwait(false);
                return;
            }

            await SendAckSafeAsync(Ack(command.CommandId, AckStatus.Accepted)).ConfigureAwait(false);
            audit.Write("command_ack", commandId: command.CommandId, traceId: command.TraceId, status: AckStatus.Accepted);
            await SendAckSafeAsync(Ack(command.CommandId, AckStatus.Running)).ConfigureAwait(false);
            audit.Write("command_ack", commandId: command.CommandId, traceId: command.TraceId, status: AckStatus.Running);

            CommandAckMessage terminal;
            try
            {
                var result = await executor.ExecuteAsync(command, inFlight.Cancellation.Token).ConfigureAwait(false);
                terminal = Ack(command.CommandId, AckStatus.Succeeded, result: result);
            }
            catch (OperationCanceledException) when (inFlight.Cancellation.IsCancellationRequested)
            {
                terminal = Ack(
                    command.CommandId,
                    AckStatus.Failed,
                    error: ErrorObjects.Create(ErrorClasses.Cancelled, "command cancelled by broker", retryable: false));
            }
            catch (CapabilityException ex)
            {
                terminal = Ack(
                    command.CommandId,
                    AckStatus.Failed,
                    error: ErrorObjects.Create(ex.ErrorClass, ex.Message, ex.Retryable));
            }
            catch (Exception ex)
            {
                logger.LogError(ex, "capability {Capability} crashed for command {CommandId}", command.Capability, command.CommandId);
                terminal = Ack(
                    command.CommandId,
                    AckStatus.Failed,
                    error: ErrorObjects.Create(ErrorClasses.InternalBug, ex.Message, retryable: false));
            }

            await CompleteAsync(command, terminal).ConfigureAwait(false);
        }
        finally
        {
            _inFlightByCommandId.TryRemove(command.CommandId, out _);
            _inFlightKeyToCommandId.TryRemove(command.IdempotencyKey, out _);
            inFlight.Cancellation.Dispose();
        }
    }

    public async Task HandleCancelAsync(string commandId)
    {
        audit.Write("cancel_received", commandId: commandId);

        if (_inFlightByCommandId.TryGetValue(commandId, out var inFlight))
        {
            logger.LogInformation("cancelling in-flight command {CommandId}", commandId);
            try
            {
                inFlight.Cancellation.Cancel();
            }
            catch (ObjectDisposedException)
            {
                // Execution finished concurrently; the terminal ack path already ran.
            }

            return;
        }

        if (store.TryGetAckByCommandId(commandId, out var terminal))
        {
            logger.LogInformation("cancel for terminal command {CommandId}; re-sending terminal ack", commandId);
            await SendAckSafeAsync(terminal!).ConfigureAwait(false);
            return;
        }

        // Never delivered/executed here: acknowledge the cancellation as a terminal failure so the
        // broker can settle. There is no idempotency key to record it under.
        logger.LogInformation("cancel for unknown command {CommandId}; acking failed/cancelled", commandId);
        await SendAckSafeAsync(Ack(
            commandId,
            AckStatus.Failed,
            error: ErrorObjects.Create(ErrorClasses.Cancelled, "cancelled before delivery", retryable: false))).ConfigureAwait(false);
    }

    private async Task CompleteAsync(CommandEnvelope command, CommandAckMessage terminal)
    {
        // Persist before sending: a lost send is recovered by broker redelivery + re-ack.
        store.PutTerminalAck(command.IdempotencyKey, terminal);
        audit.Write(
            "command_ack",
            commandId: command.CommandId,
            traceId: command.TraceId,
            capability: command.Capability,
            status: terminal.Status,
            detail: terminal.Error is null ? null : $"{terminal.Error.Class}: {terminal.Error.Message}");
        await SendAckSafeAsync(terminal).ConfigureAwait(false);
    }

    private async Task SendAckSafeAsync(CommandAckMessage ack)
    {
        var sender = _sender;
        if (sender is null)
        {
            logger.LogDebug("no active connection; ack {CommandId}/{Status} not sent (redelivery will recover)", ack.CommandId, ack.Status);
            return;
        }

        try
        {
            using var cts = new CancellationTokenSource(AckSendTimeout);
            await sender(ack, cts.Token).ConfigureAwait(false);
        }
        catch (Exception ex)
        {
            logger.LogWarning(
                "failed to send ack {CommandId}/{Status}: {Reason} (redelivery will recover)",
                ack.CommandId,
                ack.Status,
                ex.Message);
        }
    }

    private static CommandAckMessage Ack(string commandId, string status, JsonObject? result = null, ErrorObject? error = null)
        => new() { CommandId = commandId, Status = status, Result = result, Error = error };
}
